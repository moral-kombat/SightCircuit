// Public OpenVR overlay bridge. Never requests a tracked-camera interface.
#include <openvr.h>
#include "json/json.h"
#include "hud_contract.hpp"
#include "overlay_frame.hpp"
#include "overlay_lifecycle.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <functional>
#include <iterator>
#include <limits>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <thread>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
namespace contract = sightcircuit_contract;
namespace lifecycle = sightcircuit_lifecycle;
using sightcircuit_overlay::read_u32;
using Clock = std::chrono::steady_clock;
constexpr std::uint32_t kMaxPayload =
    contract::kExpiringFramePrefixBytes + contract::kMaxSide*contract::kMaxSide*contract::kRgbaBytes;
static_assert(sizeof contract::kPacketMagic - 1 ==
              contract::kPacketHeaderOpcodeOffset - contract::kPacketHeaderMagicOffset);
constexpr bool kCalibrationVisibleInDashboard = true;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
struct Stopped {};
// Watchdog budgets beside sightcircuit/runtime/guard.py, which still owns
// escalation. The connect budget comes from the generated contract header, and
// the Python clients derive their ready wait from the same value, so the two
// move together (BRIDGE_READY_WAIT_SECONDS in sightcircuit/hud/contract.py).
constexpr unsigned kWatchdogConnectSeconds = contract::kBridgeConnectBudgetSeconds;
constexpr unsigned kWatchdogStallSeconds = 5;  // any one loop step, re-armed per step
constexpr unsigned kWatchdogCleanupSeconds = 5;
void emit(Json::Value value) {
    value["report_kind"] = "sightcircuit_overlay_bridge";
    value["schema_version"] = 1;
    // Only the saved real stdout carries the protocol; fd 1 points at stderr.
    lifecycle::write_protocol(Json::FastWriter().write(value));
}
void watchdog(const std::string& mode, const char* phase, unsigned seconds) {
    Json::Value value;
    value["event"] = "error"; value["status"] = "error";
    value["error"] = std::string("watchdog_expired:") + phase;
    value["mode"] = mode; value["watchdog_phase"] = phase; value["watchdog_seconds"] = seconds;
    value["exit_code"] = lifecycle::kWatchdogExitCode;
    value["cleanup_ok"] = false; value["cleanup_skipped_by_watchdog"] = true;
    value["runtime_initialized"] = Json::Value();
    value["physical_display_recovery_verified"] = false;
    value["report_kind"] = "sightcircuit_overlay_bridge";
    value["schema_version"] = 1;
    if (!lifecycle::set_watchdog(Json::FastWriter().write(value), seconds))
        throw std::runtime_error("watchdog_arm_failed");
}
std::uint64_t monotonic_ns() {
    timespec value{};
    if (clock_gettime(CLOCK_MONOTONIC, &value)) throw std::runtime_error("monotonic_clock_failed");
    return std::uint64_t(value.tv_sec)*1000000000ULL + std::uint64_t(value.tv_nsec);
}
float f32(const std::uint8_t* p) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
    const auto bits = read_u32(p);
    float result;
    std::memcpy(&result, &bits, 4);
    return result;
}
struct Settings {
    float width = contract::kPanelDefaultWidth, distance = contract::kPanelDefaultDistance,
          x = contract::kPanelDefaultX, y = contract::kPanelDefaultY, alpha = contract::kPanelDefaultOpacity;
    bool visible = contract::kPanelDefaultVisible;
};
void bounded(float value, float low, float high) {
    if (!std::isfinite(value) || value < low || value > high)
        throw std::runtime_error("panel_setting_out_of_range");
}
Settings settings(const std::vector<std::uint8_t>& b) {
    if (b.size() != contract::kSettingsBytes) throw std::runtime_error("invalid_settings_size");
    const auto visible = read_u32(b.data()+contract::kSettingsVisibleOffset);
    Settings s{f32(b.data()+contract::kSettingsWidthOffset), f32(b.data()+contract::kSettingsDistanceOffset),
               f32(b.data()+contract::kSettingsXOffset), f32(b.data()+contract::kSettingsYOffset),
               f32(b.data()+contract::kSettingsOpacityOffset), visible != 0};
    bounded(s.width, contract::kPanelWidthMin, contract::kPanelWidthMax);
    bounded(s.distance, contract::kPanelDistanceMin, contract::kPanelDistanceMax);
    bounded(s.x, contract::kPanelXMin, contract::kPanelXMax);
    bounded(s.y, contract::kPanelYMin, contract::kPanelYMax);
    bounded(s.alpha, contract::kPanelOpacityMin, contract::kPanelOpacityMax);
    if (visible > 1) throw std::runtime_error("invalid_visibility");
    return s;
}
Settings marker_settings() {
    // The fixed HUD placement; the marker never takes settings from stdin.
    return Settings{contract::kHudPanelWidthM, contract::kHudDistanceM, contract::kHudXM, contract::kHudYM,
                    contract::kHudOpacity, true};
}
class Overlay {
public:
    vr::IVRSystem* system = nullptr;
    vr::IVROverlay* api = nullptr;
    vr::VROverlayHandle_t handle = vr::k_ulOverlayHandleInvalid;
    bool active = false, probe_active = false, has_image = false;
    bool retirement_attempted = false, shutdown_returned = false;
    sightcircuit_overlay::Retirement retirement;
    bool overlay_shown_event_seen = false, overlay_hidden_event_seen = false;
    std::uint64_t calibration_show_deadline_ns = 0;
    Settings config;
    ~Overlay() { close(); }
    void check(vr::EVROverlayError e, const char* op) {
        if (e != vr::VROverlayError_None)
            throw std::runtime_error(std::string(op)+":"+api->GetOverlayErrorNameFromEnum(e));
    }
    static std::string init_error(vr::EVRInitError error, const void* interface) {
        if (error != vr::VRInitError_None) return vr::VR_GetVRInitErrorAsSymbol(error);
        return interface ? std::string() : std::string("VRInitError_None_without_IVRSystem");
    }
    void init(bool calibration, bool background_only, Json::Value& report) {
        // Background never starts a vrserver; only a running one is joined as Overlay.
        // With background_only (an opt-in device check) the probe itself owns the overlay.
        vr::IVRSystem* probe_system = nullptr;
        const auto result = lifecycle::connect_overlay(
            [&] {
                vr::EVRInitError error = vr::VRInitError_None;
                probe_system = vr::VR_Init(&error, vr::VRApplication_Background);
                probe_active = error == vr::VRInitError_None && probe_system;
                if (!probe_active) probe_system = nullptr;
                return init_error(error, probe_system);
            },
            [&] { if (probe_active) { vr::VR_Shutdown(); probe_active = false; } },
            [&] {
                vr::EVRInitError error = vr::VRInitError_None;
                system = vr::VR_Init(&error, vr::VRApplication_Overlay);
                active = error == vr::VRInitError_None && system;
                if (!active) system = nullptr;
                return init_error(error, system);
            },
            [&](unsigned ms) {
                const auto until = Clock::now()+std::chrono::milliseconds(ms);
                while (!stopping && Clock::now() < until) std::this_thread::sleep_for(std::chrono::milliseconds(10));
            },
            [&] { return stopping != 0; }, background_only);
        report["sequence"] = background_only ? "background_only_opt_in" : "background_probe_then_overlay";
        report["application_type"] = background_only ? "VRApplication_Background" : "VRApplication_Overlay";
        report["background_probes"] = result.background_probes;
        report["background_probe_result"] = result.background_probe_error;
        report["vrserver_running"] = result.server_running;
        report["overlay_connect_attempted"] = result.overlay_connect_attempted;
        report["overlay_connect_attempts"] = result.overlay_attempts;
        report["overlay_connect_attempt_limit"] = lifecycle::kOverlayConnectAttempts;
        report["overlay_connect_errors"] = Json::arrayValue;
        for (const auto& error : result.overlay_errors) report["overlay_connect_errors"].append(error);
        report["connected"] = result.connected;
        if (result.stopped) throw Stopped{};
        if (!result.connected) throw std::runtime_error(result.error);
        if (background_only) { system = probe_system; active = true; probe_active = false; }
        api = vr::VROverlay();
        if (!api) throw std::runtime_error("IVROverlay_028_unavailable");
        if (!system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd))
            throw std::runtime_error("headset_not_connected");
        const auto key = std::string(calibration ? "sightcircuit.calibration." : "sightcircuit.replay.")+std::to_string(getpid());
        check(api->CreateOverlay(key.c_str(), calibration ? "SightCircuit calibration marker" : "SightCircuit replay", &handle), "CreateOverlay");
        check(api->SetOverlayInputMethod(handle, vr::VROverlayInputMethod_None), "SetOverlayInputMethod");
        check(api->SetOverlayTextureColorSpace(handle, vr::ColorSpace_Gamma), "SetOverlayTextureColorSpace");
        if (calibration)
            check(api->SetOverlayFlag(handle, vr::VROverlayFlags_VisibleInDashboard, kCalibrationVisibleInDashboard),
                  "SetOverlayFlag:VisibleInDashboard");
        configure(config);
    }
    // Read-only headset view record (package M): Background only, never an
    // Overlay connect and never a Set* call on IVRHeadsetView.
    void view_record(Json::Value& connect, Json::Value& record) {
        connect["sequence"] = "background_only";
        connect["overlay_connect_attempted"] = false;
        connect["background_probes"] = 1U;
        vr::EVRInitError error = vr::VRInitError_None;
        system = vr::VR_Init(&error, vr::VRApplication_Background);
        probe_active = error == vr::VRInitError_None && system;
        const auto symbol = init_error(error, system);
        connect["background_probe_result"] = symbol.empty() ? "VRInitError_None" : symbol;
        connect["vrserver_running"] = symbol.empty();
        if (!symbol.empty()) { system = nullptr; throw std::runtime_error("vrserver_not_running:"+symbol); }
        auto* view = vr::VRHeadsetView();
        if (!view) throw std::runtime_error("IVRHeadsetView_001_unavailable");
        const auto number = [](float value) { return std::isfinite(value) ? Json::Value(value) : Json::Value(); };
        record["report_kind"] = "sightcircuit_headset_view_record";
        record["schema_version"] = 1;
        record["scope"] = "read_only_runtime_query_not_mapping_evidence";
        record["interface"] = vr::IVRHeadsetView_Version;
        auto& headset = record["headset_view"];
        std::uint32_t width = 0, height = 0;
        view->GetHeadsetViewSize(&width, &height);
        headset["width"] = width; headset["height"] = height;
        const auto mode = view->GetHeadsetViewMode();
        headset["mode_value"] = static_cast<int>(mode);
        headset["mode"] = mode == vr::HeadsetViewMode_Left ? "left" : mode == vr::HeadsetViewMode_Right ? "right" :
                          mode == vr::HeadsetViewMode_Both ? "both" : "unknown";
        headset["cropped"] = view->GetHeadsetViewCropped();
        headset["aspect_ratio"] = number(view->GetHeadsetViewAspectRatio());
        float blend_start = 0, blend_end = 0;
        view->GetHeadsetViewBlendRange(&blend_start, &blend_end);
        headset["blend_range"].append(number(blend_start));
        headset["blend_range"].append(number(blend_end));
        for (const auto eye : {vr::Eye_Left, vr::Eye_Right}) {
            auto& entry = record["eyes"][eye == vr::Eye_Left ? "left" : "right"];
            const auto transform = system->GetEyeToHeadTransform(eye);
            for (unsigned row = 0; row < 3; ++row) {
                Json::Value values(Json::arrayValue);
                for (unsigned column = 0; column < 4; ++column) values.append(number(transform.m[row][column]));
                entry["eye_to_head"].append(values);
            }
            float left = 0, right = 0, top = 0, bottom = 0;
            system->GetProjectionRaw(eye, &left, &right, &top, &bottom);
            entry["projection_raw"]["left"] = number(left); entry["projection_raw"]["right"] = number(right);
            entry["projection_raw"]["top"] = number(top); entry["projection_raw"]["bottom"] = number(bottom);
        }
        record["set_calls_made"] = false;
        record["unknowns"]["mirror_carries_headset_view"] = "unknown: not established for /dev/video99";
    }
    void configure(const Settings& s) {
        config = s;
        vr::HmdMatrix34_t matrix{};
        matrix.m[0][0] = matrix.m[1][1] = matrix.m[2][2] = 1;
        matrix.m[0][3] = s.x; matrix.m[1][3] = s.y; matrix.m[2][3] = -s.distance;
        check(api->SetOverlayWidthInMeters(handle,s.width), "SetOverlayWidthInMeters");
        check(api->SetOverlayAlpha(handle,s.alpha), "SetOverlayAlpha");
        check(api->SetOverlayTransformTrackedDeviceRelative(handle,vr::k_unTrackedDeviceIndex_Hmd,&matrix),
              "SetOverlayTransformTrackedDeviceRelative");
        visibility();
    }
    void visibility() {
        if (config.visible && has_image) {
            // A late SetOverlayRaw return must not initiate a new ShowOverlay.
            // A call that never returns still requires external containment.
            if (calibration_show_deadline_ns && monotonic_ns()>=calibration_show_deadline_ns)
                throw std::runtime_error("calibration_deadline_before_show");
            check(api->ShowOverlay(handle), "ShowOverlay");
        }
        else check(api->HideOverlay(handle), "HideOverlay");
    }
    void frame(std::uint32_t w, std::uint32_t h, const void* rgba) {
        check(api->SetOverlayRaw(handle,const_cast<void*>(rgba),w,h,4), "SetOverlayRaw");
        has_image = true;
        visibility();
    }
    void calibration_visibility(Json::Value& sample) {
        const auto before_query = [&]() {
            if (stopping) throw Stopped{};
            if (monotonic_ns() >= calibration_show_deadline_ns)
                throw std::runtime_error("calibration_visibility_query_overran_deadline");
        };
        before_query();
        bool dashboard_flag = false;
        check(api->GetOverlayFlag(handle, vr::VROverlayFlags_VisibleInDashboard, &dashboard_flag),
              "GetOverlayFlag:VisibleInDashboard");
        sample["visible_in_dashboard"] = dashboard_flag;
        before_query();
        sample["api_visible"] = api->IsOverlayVisible(handle);
        before_query();
        sample["dashboard_visible"] = api->IsDashboardVisible();
        before_query();
        sample["sampled"] = true;
    }
    void events() {
        if (!system) return;
        vr::VREvent_t e{};
        while (system->PollNextEvent(&e,sizeof(e))) {
            if (e.eventType == vr::VREvent_Quit) {
                system->AcknowledgeQuit_Exiting(); throw Stopped{};
            }
        }
        while (api->PollNextOverlayEvent(handle,&e,sizeof(e))) {
            overlay_shown_event_seen |= e.eventType == vr::VREvent_OverlayShown;
            overlay_hidden_event_seen |= e.eventType == vr::VREvent_OverlayHidden;
            if (e.eventType == vr::VREvent_Quit || e.eventType == vr::VREvent_OverlayClosed) throw Stopped{};
            if (e.eventType == vr::VREvent_ImageFailed) throw std::runtime_error("overlay_image_failed");
        }
    }
    bool close() noexcept {
        bool ok = true;
        if (api && handle != vr::k_ulOverlayHandleInvalid) {
            retirement_attempted = true;
            retirement = sightcircuit_overlay::retire_overlay(
                [&] { return api->HideOverlay(handle) == vr::VROverlayError_None; },
                [&] { return api->ClearOverlayTexture(handle) == vr::VROverlayError_None; },
                [&] { return api->DestroyOverlay(handle) == vr::VROverlayError_None; });
            ok = retirement.ok();
            handle = vr::k_ulOverlayHandleInvalid;
        }
        if (active || probe_active) { vr::VR_Shutdown(); active = probe_active = false; shutdown_returned = true; }
        system = nullptr; api = nullptr;
        return ok;
    }
};
bool read_exact(std::uint8_t* data, std::size_t size, bool eof_allowed, Overlay& overlay,
                const std::function<void()>& expire) {
    const auto deadline = Clock::now()+std::chrono::seconds(eof_allowed ? 10 : 3);
    std::size_t received = 0;
    while (received < size) {
        lifecycle::rearm_watchdog(kWatchdogStallSeconds);
        if (stopping) throw Stopped{};
        expire();
        overlay.events();
        if (Clock::now() >= deadline) throw std::runtime_error("stdin_timeout");
        pollfd input{STDIN_FILENO,POLLIN,0};
        const auto available = poll(&input,1,20);
        if (available < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("stdin_poll_failed");
        }
        if (!available) continue;
        const auto count = read(STDIN_FILENO,data+received,size-received);
        if (count < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("stdin_read_failed");
        }
        if (!count) {
            if (eof_allowed && !received) return false;
            throw std::runtime_error("truncated_packet");
        }
        received += static_cast<std::size_t>(count);
    }
    return true;
}
void stream(Overlay& overlay, bool validate, Json::Value& report) {
    std::array<std::uint8_t,contract::kPacketHeaderBytes> header{};
    std::uint64_t expiry = 0;
    bool current = false, visible = true;
    const auto expire = [&] {
        const auto now = monotonic_ns();
        if (expiry && now >= expiry) {
            current = false;
            overlay.has_image = false;
            if (!validate) overlay.visibility();
            report["expired_frames"] = report["expired_frames"].asUInt()+1;
            Json::Value event;
            event["event"] = "frame_expired";
            event["deadline_ns"] = Json::UInt64(expiry);
            event["observed_ns"] = Json::UInt64(now);
            event["validation_only"] = validate;
            event["content_current"] = false;
            emit(event);
            expiry = 0;
        }
        report["content_current"] = current;
        report["presentation_visible"] = current && visible;
    };
    while (read_exact(header.data(),header.size(),true,overlay,expire)) {
        if (std::memcmp(header.data()+contract::kPacketHeaderMagicOffset, contract::kPacketMagic,
                        sizeof contract::kPacketMagic - 1))
            throw std::runtime_error("invalid_packet_magic");
        const auto kind = read_u32(header.data()+contract::kPacketHeaderOpcodeOffset);
        const auto size = read_u32(header.data()+contract::kPacketHeaderPayloadBytesOffset);
        if (size > kMaxPayload || (kind != contract::kOpcodeFrame && kind != contract::kOpcodeSettings &&
                                   kind != contract::kOpcodeStop && kind != contract::kOpcodeExpiringFrame))
            throw std::runtime_error("invalid_packet_header");
        if ((kind == contract::kOpcodeSettings && size != contract::kSettingsBytes) ||
                (kind == contract::kOpcodeStop && size) ||
                (kind == contract::kOpcodeFrame && size < contract::kFramePrefixBytes))
            throw std::runtime_error("invalid_packet_size");
        if (kind == contract::kOpcodeExpiringFrame && (!validate || size < contract::kExpiringFramePrefixBytes))
            throw std::runtime_error("expiring_frames_validation_only");
        std::vector<std::uint8_t> payload(size);
        if (size) read_exact(payload.data(),size,false,overlay,expire);
        expire();
        if (kind == contract::kOpcodeStop) break;
        if (kind == contract::kOpcodeSettings) {
            const auto s = settings(payload);
            visible = s.visible;
            if (!validate) overlay.configure(s);
            report["settings_received"] = report["settings_received"].asUInt()+1;
        } else {
            const auto frame = sightcircuit_overlay::decode_frame(kind, payload);
            const auto w = frame.width, h = frame.height;
            const auto requested_expiry = frame.expiry;
            if (kind == contract::kOpcodeExpiringFrame) {
                const auto now = monotonic_ns();
                const bool current_deadline = sightcircuit_overlay::deadline_current(frame, now);
                report["expiring_frames_received"] = report["expiring_frames_received"].asUInt()+1;
                if (!current_deadline) {
                    report["stale_frames_dropped"] = report["stale_frames_dropped"].asUInt()+1;
                    continue;
                }
            }
            expiry = requested_expiry;
            current = true;
            report["frames_received"] = report["frames_received"].asUInt()+1;
            if (!validate) sightcircuit_overlay::dispatch_frame(overlay, frame);
            if (!validate) report["frames_submitted"] = report["frames_received"];
            if (report["frames_received"].asUInt() == 1) {
                Json::Value first;
                first["event"] = validate ? "frame_validated" : "frame_submitted";
                first["width"] = w; first["height"] = h;
                emit(first);
            }
        }
        expire();
    }
}
std::vector<std::uint8_t> pattern() {
    constexpr unsigned w=256,h=128;
    std::vector<std::uint8_t> image(w*h*4,255);
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) {
        const auto p=(y*w+x)*4;
        image[p]=x<w/2 ? (y<h/2 ? 220 : 40) : (y<h/2 ? 40 : 220);
        image[p+1]=x<w/2 ? 40 : 220;
        image[p+2]=y<h/2 ? 40 : (x<w/2 ? 220 : 40);
        if (x%32==0 || y%32==0) image[p]=image[p+1]=image[p+2]=100;
    }
    const std::array<std::array<unsigned,7>,3> letters{{
        {{31,4,4,4,4,4,4}},{{14,17,17,17,17,17,14}},{{30,17,17,30,16,16,16}}
    }};
    for (unsigned c=0;c<3;++c) for (unsigned y=0;y<7;++y) for (unsigned x=0;x<5;++x)
        if (letters[c][y] & (1U<<(4-x))) for (unsigned dy=0;dy<3;++dy) for (unsigned dx=0;dx<3;++dx) {
            const auto p=(((y*3+8+dy)*w)+(c*18+x*3+102+dx))*4;
            image[p]=image[p+1]=image[p+2]=255;
        }
    return image;
}

std::vector<std::uint8_t> calibration_marker(Json::Value& description, const Settings& applied) {
    // Every size, position, gray level and corner id comes from the generated
    // contract, which sightcircuit.hud.observation.marker_template also uses.
    constexpr unsigned w=contract::kHudWidthPx, h=contract::kHudHeightPx;
    constexpr unsigned border=contract::kMarkerBorderPx, size=contract::kMarkerTagSizePx;
    constexpr unsigned inset=contract::kMarkerTagInsetPx, outline=contract::kMarkerTagOutlinePx;
    constexpr unsigned bit_size=contract::kMarkerBitSizePx, columns=contract::kMarkerBitColumns;
    static_assert(contract::kMarkerBorderValue<=255 && contract::kMarkerBackgroundValue<=255 &&
                  contract::kMarkerTagOutlineValue<=255 && contract::kMarkerTagFillValue<=255 &&
                  contract::kMarkerBitSetValue<=255 && contract::kMarkerBitClearValue<=255);
    std::vector<std::uint8_t> pixels(w*h*contract::kRgbaBytes,255);
    const auto paint = [&](unsigned x, unsigned y, std::uint8_t value) {
        const auto p=(y*w+x)*contract::kRgbaBytes;
        pixels[p]=pixels[p+1]=pixels[p+2]=value;
    };
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x)
        paint(x,y,(x<border || y<border || x>=w-border || y>=h-border) ?
              contract::kMarkerBorderValue : contract::kMarkerBackgroundValue);
    // Four distinct binary corner tags; the outer border marks the full texture.
    // This is a generated marker, not an automatic eye/layout detector.
    for (unsigned tag=0;tag<std::size(contract::kMarkerCornerIds);++tag) {
        const unsigned x0=tag%2 ? w-inset-size : inset, y0=tag/2 ? h-inset-size : inset;
        for (unsigned y=0;y<size;++y) for (unsigned x=0;x<size;++x)
            paint(x0+x,y0+y,(x<outline || y<outline || x>=size-outline || y>=size-outline) ?
                  contract::kMarkerTagOutlineValue : contract::kMarkerTagFillValue);
        unsigned decoded=0;
        for (unsigned bit=0;bit<contract::kMarkerBits;++bit) {
            const unsigned x=x0+contract::kMarkerBitOffsetPx+(bit%columns)*contract::kMarkerBitPitchPx;
            const unsigned y=y0+contract::kMarkerBitOffsetPx+(bit/columns)*contract::kMarkerBitPitchPx;
            const bool set=(contract::kMarkerCornerIds[tag]>>bit)&1U;
            for (unsigned dy=0;dy<bit_size;++dy) for (unsigned dx=0;dx<bit_size;++dx)
                paint(x+dx,y+dy,set ? contract::kMarkerBitSetValue : contract::kMarkerBitClearValue);
            if (pixels[(y*w+x)*contract::kRgbaBytes]==contract::kMarkerBitSetValue) decoded|=1U<<bit;
        }
        description["corner_ids"].append(decoded);
    }
    unsigned opaque=0;
    for (std::size_t i=3;i<pixels.size();i+=contract::kRgbaBytes) opaque+=pixels[i]==255;
    description["width"]=w; description["height"]=h;
    description["opaque_pixels"]=opaque; description["border_pixels"]=border;
    description["tag_size_pixels"]=size; description["tag_inset_pixels"]=inset;
    description["corner_order"]=contract::kMarkerCornerOrder;
    description["config_id"]=contract::kHudLayoutId;
    description["visible_in_dashboard"]=kCalibrationVisibleInDashboard;
    description["panel_width_m"]=applied.width; description["distance_m"]=applied.distance;
    description["x_m"]=applied.x; description["y_m"]=applied.y; description["opacity"]=applied.alpha;
    return pixels;
}
void write_new_file(const char* path, const std::vector<std::uint8_t>& bytes) {
    // Exclusive creation: never replaces or follows an existing path.
    const int fd=::open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if (fd<0) throw std::runtime_error("render_output_open_failed");
    std::size_t done=0;
    while (done<bytes.size()) {
        const auto count=::write(fd,bytes.data()+done,bytes.size()-done);
        if (count<0 && errno==EINTR) continue;
        if (count<=0) { ::close(fd); ::unlink(path); throw std::runtime_error("render_output_write_failed"); }
        done+=static_cast<std::size_t>(count);
    }
    if (::close(fd)) { ::unlink(path); throw std::runtime_error("render_output_close_failed"); }
}
int render_marker(int argc, char* argv[]) {
    // Validation only: draw the marker into a new RGBA file; no OpenVR call.
    Json::Value report;
    report["event"]="closed"; report["mode"]="--render-calibration-marker";
    report["validation_only"]=true; report["runtime_initialized"]=false;
    report["contract_digest"]=contract::kContractDigest; report["config_digest"]=contract::kHudConfigDigest;
    report["physical_display_recovery_verified"]=false;
    if (argc!=3 || !*argv[2]) {
        report["event"]="error"; report["status"]="error";
        report["error"]="render_requires_one_new_output_path"; report["exit_code"]=64;
        emit(report); return 64;
    }
    int exit_code=0;
    try {
        const auto pixels=calibration_marker(report["marker"],marker_settings());
        write_new_file(argv[2],pixels);
        report["status"]="rendered"; report["rgba_bytes"]=Json::UInt64(pixels.size());
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error"; report["error"]=error.what(); exit_code=1;
    }
    report["exit_code"]=exit_code;
    emit(report);
    return exit_code;
}
} // namespace

int main(int argc, char* argv[]) {
    // Before anything can load the SDK: keep the real stdout for the protocol.
    if (!lifecycle::isolate_stdout()) {
        Json::Value failure;
        failure["event"]="error"; failure["status"]="error"; failure["error"]="stdout_isolation_failed";
        failure["runtime_initialized"]=false; failure["exit_code"]=1; emit(failure); return 1;
    }
    const std::string mode=argc>1 ? argv[1] : "";
    if (mode=="--help" && argc==2) {
        const auto marker_seconds=std::to_string(contract::kMarkerMinSeconds)+".."+
                                  std::to_string(contract::kMarkerMaxSeconds);
        std::ostringstream usage;
        usage << "Usage: sightcircuit-overlay-bridge --test-panel [--seconds 1..30]\n"
                     "       sightcircuit-overlay-bridge --stdin\n"
                     "       sightcircuit-overlay-bridge --validate-stdin (no OpenVR calls)\n"
                     "       sightcircuit-overlay-bridge --validate-calibration-marker --seconds " << marker_seconds << "\n"
                     "       sightcircuit-overlay-bridge --calibration-marker --seconds " << marker_seconds <<
                     " --allow-calibration-output\n"
                     "       sightcircuit-overlay-bridge --render-calibration-marker NEW_RGBA_FILE (no OpenVR calls)\n"
                     "       sightcircuit-overlay-bridge --headset-view-record --allow-runtime-query (read-only, Background only)\n"
                     "Runtime modes join an already running SteamVR only: a Background probe comes first and\n"
                     "never starts a vrserver. A SIGALRM watchdog ends a stalled step with one JSON error.\n"
                     "A runtime mode may end with --connect-as overlay (default) or --connect-as background,\n"
                     "which keeps the Background client and never connects as Overlay (device check, opt-in).\n";
        return lifecycle::write_protocol(usage.str()) ? 0 : 1;
    }
    if (mode=="--render-calibration-marker") return render_marker(argc,argv);
    Json::Value report;
    report["event"]="closed"; report["runtime_initialized"]=false;
    report["frames_received"]=0U; report["frames_submitted"]=0U; report["settings_received"]=0U;
    report["expired_frames"]=0U; report["stale_frames_dropped"]=0U;
    report["expiring_frames_received"]=0U;
    report["presentation_state_scope"]="software_state_before_close_not_headset_evidence";
    report["mode"]=mode;
    const bool marker=mode=="--calibration-marker" || mode=="--validate-calibration-marker";
    const bool validate=mode=="--validate-stdin" || mode=="--validate-calibration-marker";
    const bool view_record=mode=="--headset-view-record";
    const bool runtime_mode=mode=="--test-panel" || mode=="--stdin" || mode=="--calibration-marker";
    bool background_only=false;
    int seconds=10;
    try {
        // A trailing "--connect-as overlay|background" is read only for the runtime modes.
        if (argc>=4 && std::string(argv[argc-2])=="--connect-as") {
            const std::string how=argv[argc-1];
            if (!runtime_mode || (how!="overlay" && how!="background"))
                throw std::runtime_error("connect_as_requires_runtime_mode_and_overlay_or_background");
            background_only=how=="background";
            argc-=2;
        }
        if (view_record) {
            if (argc!=3 || std::string(argv[2])!="--allow-runtime-query")
                throw std::runtime_error("view_record_requires_explicit_opt_in");
        } else if (marker) {
            if (argc!=(validate ? 4 : 5) || std::string(argv[2])!="--seconds" ||
                    (!validate && std::string(argv[4])!="--allow-calibration-output"))
                throw std::runtime_error("calibration_requires_separate_explicit_opt_in");
            std::size_t used=0;
            seconds=std::stoi(argv[3],&used);
            if (used!=std::strlen(argv[3]) || seconds<contract::kMarkerMinSeconds || seconds>contract::kMarkerMaxSeconds)
                throw std::runtime_error("seconds_out_of_range");
        } else if (mode=="--test-panel" && argc==4 && std::string(argv[2])=="--seconds") {
            std::size_t used=0;
            seconds=std::stoi(argv[3],&used);
            if (used!=std::strlen(argv[3]) || seconds<1 || seconds>30) throw std::runtime_error("seconds_out_of_range");
        } else if (!(argc==2 && (mode=="--test-panel" || mode=="--stdin" || mode=="--validate-stdin"))) {
            throw std::runtime_error("Choose --test-panel or --stdin; --help shows usage");
        }
    } catch (const std::exception& error) {
        report["event"]=argc==1 ? "not_run" : "error";
        report["error"]=error.what(); report["exit_code"]=argc==1 ? 3 : 64;
        emit(report); return argc==1 ? 3 : 64;
    }
    std::signal(SIGINT,stop_signal); std::signal(SIGTERM,stop_signal); std::signal(SIGPIPE,SIG_IGN);
    if (!lifecycle::install_watchdog()) {
        report["event"]="error"; report["error"]="watchdog_unavailable";
        report["exit_code"]=1; emit(report); return 1;
    }
    const auto parent=getppid();
    if (prctl(PR_SET_PDEATHSIG,SIGTERM)!=0) {
        report["event"]="error"; report["error"]="parent_death_signal_unavailable";
        report["exit_code"]=1; emit(report); return 1;
    }
    if (getppid()!=parent || parent==1) stopping=1;
    Overlay overlay;
    int exit_code=0;
    const auto marker_start=marker ? monotonic_ns() : 0;
    const auto marker_deadline=marker_start+std::uint64_t(seconds)*1000000000ULL;
    if (marker) {
        overlay.config=marker_settings();
        overlay.calibration_show_deadline_ns=marker_deadline;
        report["calibration_only"]=true; report["seconds_requested"]=seconds;
        report["native_started_ns"]=Json::UInt64(marker_start);
        report["display_deadline_ns"]=Json::UInt64(marker_deadline);
        report["deadline_scope"]="best_effort_software_checks_around_synchronous_vr_calls";
        report["physical_display_recovery_verified"]=false;
        report["capture_started"]=false; report["inference_started"]=false;
        report["images_saved"]=false; report["input_frames_accepted"]=false;
        auto& visibility=report["visibility_after_submit"];
        visibility["scope"]="runtime_queries_not_physical_visibility";
        visibility["sampled"]=false;
        visibility["api_visible"]=Json::Value();
        visibility["dashboard_visible"]=Json::Value();
        visibility["visible_in_dashboard"]=Json::Value();
    }
    if (view_record) {
        report["view_record"]=Json::Value();
        report["set_calls_made"]=false;
    } else if (!validate) {
        auto& connect=report["connect"];
        connect["sequence"]="background_probe_then_overlay";
        connect["background_probes"]=0U; connect["overlay_connect_attempted"]=false;
        connect["overlay_connect_attempts"]=0U; connect["connected"]=false;
        connect["background_only_requested"]=background_only;
    }
    try {
        watchdog(mode,"connect",kWatchdogConnectSeconds);
        if (stopping) throw Stopped{};
        if (view_record) {
            overlay.view_record(report["connect"],report["view_record"]);
            report["status"]="view_recorded";
            throw Stopped{};  // Nothing to stream: go straight to cleanup.
        }
        if (!validate) overlay.init(marker,background_only,report["connect"]);
        watchdog(mode,"run",kWatchdogStallSeconds);
        if (marker && monotonic_ns()>=marker_deadline)
            throw std::runtime_error("calibration_deadline_before_marker");
        if (stopping) throw Stopped{};
        Json::Value ready;
        ready["event"]="ready"; ready["validation_only"]=validate;
        ready["frame_expiry_supported"]=mode=="--validate-stdin";
        ready["runtime_initialized"]=!validate; ready["overlay_interface"]=vr::IVROverlay_Version;
        if (marker) ready["display_deadline_ns"]=Json::UInt64(marker_deadline);
        emit(ready);
        if (marker) {
            auto pixels=calibration_marker(report["marker"],overlay.config);
            report["generated_marker_frames"]=1U;
            if (stopping) throw Stopped{};
            if (monotonic_ns()>=marker_deadline) throw std::runtime_error("calibration_deadline_before_marker");
            report["marker_request_ns"]=Json::UInt64(monotonic_ns());
            if (!validate) {
                overlay.frame(contract::kHudWidthPx,contract::kHudHeightPx,pixels.data());
                report["frames_submitted"]=1U;
            }
            report["marker_submit_returned_ns"]=Json::UInt64(monotonic_ns());
            if (monotonic_ns()>=marker_deadline) throw std::runtime_error("calibration_submit_overran_deadline");
            if (!validate) overlay.calibration_visibility(report["visibility_after_submit"]);
            while (monotonic_ns()<marker_deadline) {
                lifecycle::rearm_watchdog(kWatchdogStallSeconds);
                if (stopping) throw Stopped{};
                overlay.events();
                if (monotonic_ns()>=marker_deadline) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
        } else if (mode=="--test-panel") {
            auto pixels=pattern();
            overlay.frame(256,128,pixels.data());
            report["frames_submitted"]=1U;
            Json::Value shown;
            shown["event"]="test_panel_submitted"; shown["seconds"]=seconds;
            shown["api_visible"]=overlay.api->IsOverlayVisible(overlay.handle);
            emit(shown);
            const auto deadline=Clock::now()+std::chrono::seconds(seconds);
            while (Clock::now()<deadline) {
                lifecycle::rearm_watchdog(kWatchdogStallSeconds);
                if (stopping) throw Stopped{};
                overlay.events(); std::this_thread::sleep_for(std::chrono::milliseconds(16));
            }
        } else stream(overlay,validate,report);
        report["status"]=marker ? (validate ? "validated_marker" : "marker_complete") : (validate ? "validated" : "closed");
    } catch (const Stopped&) {
        if (!view_record || report["status"]!="view_recorded") report["status"]="stopped";
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error";
        report["error"]=error.what(); exit_code=1;
    }
    report["runtime_initialized"]=overlay.active || overlay.probe_active;
    if (marker) {
        report["overlay_shown_event_seen"]=overlay.overlay_shown_event_seen;
        report["overlay_hidden_event_seen"]=overlay.overlay_hidden_event_seen;
        const auto cutoff=monotonic_ns();
        report["cutoff_requested_ns"]=Json::UInt64(cutoff);
        report["software_deadline_overshoot_ms"]=cutoff>marker_deadline ? double(cutoff-marker_deadline)/1e6 : 0.;
    }
    bool watchdog_armed=true;
    try { watchdog(mode,"cleanup",kWatchdogCleanupSeconds); }
    catch (const std::exception&) { watchdog_armed=false; }
    const bool ok=overlay.close();
    lifecycle::disarm_watchdog();
    report["watchdog_armed_for_cleanup"]=watchdog_armed;
    if (marker) report["cleanup_returned_ns"]=Json::UInt64(monotonic_ns());
    report["overlay_retirement_attempted"]=overlay.retirement_attempted;
    report["hide_returned_ok"]=overlay.retirement_attempted ? Json::Value(overlay.retirement.hidden) : Json::Value();
    report["clear_texture_returned_ok"]=overlay.retirement_attempted ? Json::Value(overlay.retirement.texture_cleared) : Json::Value();
    report["destroy_returned_ok"]=overlay.retirement_attempted ? Json::Value(overlay.retirement.destroyed) : Json::Value();
    report["runtime_shutdown_returned"]=overlay.shutdown_returned;
    report["physical_display_recovery_verified"]=false;
    report["cleanup_ok"]=ok;
    if (!ok) { report["status"]="cleanup_failure"; exit_code=1; }
    report["exit_code"]=exit_code;
    // The final write is bounded too: a parent that stopped draining the pipe
    // would otherwise hold the bridge here after cleanup, with the alarm cleared.
    try { watchdog(mode,"report",kWatchdogCleanupSeconds); } catch (const std::exception&) {}
    emit(report);
    lifecycle::disarm_watchdog();
    return exit_code;
}
