// Public OpenVR overlay bridge. Never requests a tracked-camera interface.
#include <openvr.h>
#include "json/json.h"
#include "overlay_frame.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <limits>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <thread>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::uint32_t kMaxSide = 512, kMaxPayload = 16 + kMaxSide*kMaxSide*4;
constexpr bool kCalibrationVisibleInDashboard = true;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
struct Stopped {};
void emit(Json::Value value) {
    value["report_kind"] = "framejacker_overlay_bridge";
    value["schema_version"] = 1;
    std::cout << Json::FastWriter().write(value) << std::flush;
}
std::uint32_t u32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1])<<8) |
           (std::uint32_t(p[2])<<16) | (std::uint32_t(p[3])<<24);
}
std::uint64_t monotonic_ns() {
    timespec value{};
    if (clock_gettime(CLOCK_MONOTONIC, &value)) throw std::runtime_error("monotonic_clock_failed");
    return std::uint64_t(value.tv_sec)*1000000000ULL + std::uint64_t(value.tv_nsec);
}
float f32(const std::uint8_t* p) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
    const auto bits = u32(p);
    float result;
    std::memcpy(&result, &bits, 4);
    return result;
}
struct Settings {
    float width = .8f, distance = 1.5f, x = 0, y = -.2f, alpha = .95f;
    bool visible = true;
};
void bounded(float value, float low, float high) {
    if (!std::isfinite(value) || value < low || value > high)
        throw std::runtime_error("panel_setting_out_of_range");
}
Settings settings(const std::vector<std::uint8_t>& b) {
    if (b.size() != 24) throw std::runtime_error("invalid_settings_size");
    Settings s{f32(b.data()), f32(b.data()+4), f32(b.data()+8),
               f32(b.data()+12), f32(b.data()+16), u32(b.data()+20) != 0};
    bounded(s.width,.25f,1.5f); bounded(s.distance,.5f,3.f);
    bounded(s.x,-1.f,1.f); bounded(s.y,-1.f,1.f); bounded(s.alpha,.2f,1.f);
    if (u32(b.data()+20) > 1) throw std::runtime_error("invalid_visibility");
    return s;
}
class Overlay {
public:
    vr::IVRSystem* system = nullptr;
    vr::IVROverlay* api = nullptr;
    vr::VROverlayHandle_t handle = vr::k_ulOverlayHandleInvalid;
    bool active = false, has_image = false;
    bool retirement_attempted = false, shutdown_returned = false;
    framejacker_overlay::Retirement retirement;
    bool overlay_shown_event_seen = false, overlay_hidden_event_seen = false;
    std::uint64_t calibration_show_deadline_ns = 0;
    Settings config;
    ~Overlay() { close(); }
    void check(vr::EVROverlayError e, const char* op) {
        if (e != vr::VROverlayError_None)
            throw std::runtime_error(std::string(op)+":"+api->GetOverlayErrorNameFromEnum(e));
    }
    void init(bool calibration = false) {
        vr::EVRInitError error = vr::VRInitError_None;
        system = vr::VR_Init(&error, vr::VRApplication_Overlay);
        active = error == vr::VRInitError_None && system;
        if (!active) throw std::runtime_error(std::string("VR_Init:")+vr::VR_GetVRInitErrorAsSymbol(error));
        api = vr::VROverlay();
        if (!api) throw std::runtime_error("IVROverlay_028_unavailable");
        if (!system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd))
            throw std::runtime_error("headset_not_connected");
        const auto key = std::string(calibration ? "framejacker.calibration." : "framejacker.replay.")+std::to_string(getpid());
        check(api->CreateOverlay(key.c_str(), calibration ? "Framejacker calibration marker" : "Framejacker replay", &handle), "CreateOverlay");
        check(api->SetOverlayInputMethod(handle, vr::VROverlayInputMethod_None), "SetOverlayInputMethod");
        check(api->SetOverlayTextureColorSpace(handle, vr::ColorSpace_Gamma), "SetOverlayTextureColorSpace");
        if (calibration)
            check(api->SetOverlayFlag(handle, vr::VROverlayFlags_VisibleInDashboard, kCalibrationVisibleInDashboard),
                  "SetOverlayFlag:VisibleInDashboard");
        configure(config);
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
            retirement = framejacker_overlay::retire_overlay(
                [&] { return api->HideOverlay(handle) == vr::VROverlayError_None; },
                [&] { return api->ClearOverlayTexture(handle) == vr::VROverlayError_None; },
                [&] { return api->DestroyOverlay(handle) == vr::VROverlayError_None; });
            ok = retirement.ok();
            handle = vr::k_ulOverlayHandleInvalid;
        }
        if (active) { vr::VR_Shutdown(); active = false; shutdown_returned = true; }
        system = nullptr; api = nullptr;
        return ok;
    }
};
bool read_exact(std::uint8_t* data, std::size_t size, bool eof_allowed, Overlay& overlay,
                const std::function<void()>& expire) {
    const auto deadline = Clock::now()+std::chrono::seconds(eof_allowed ? 10 : 3);
    std::size_t received = 0;
    while (received < size) {
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
    std::array<std::uint8_t,12> header{};
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
        if (std::memcmp(header.data(),"FJO1",4)) throw std::runtime_error("invalid_packet_magic");
        const auto kind = u32(header.data()+4), size = u32(header.data()+8);
        if (size > kMaxPayload || kind < 1 || kind > 4) throw std::runtime_error("invalid_packet_header");
        if ((kind == 2 && size != 24) || (kind == 3 && size) || (kind == 1 && size < 8))
            throw std::runtime_error("invalid_packet_size");
        if (kind == 4 && (!validate || size < 16))
            throw std::runtime_error("expiring_frames_validation_only");
        std::vector<std::uint8_t> payload(size);
        if (size) read_exact(payload.data(),size,false,overlay,expire);
        expire();
        if (kind == 3) break;
        if (kind == 2) {
            const auto s = settings(payload);
            visible = s.visible;
            if (!validate) overlay.configure(s);
            report["settings_received"] = report["settings_received"].asUInt()+1;
        } else {
            const auto frame = framejacker_overlay::decode_frame(kind, payload);
            const auto w = frame.width, h = frame.height;
            const auto requested_expiry = frame.expiry;
            if (kind == 4) {
                const auto now = monotonic_ns();
                const bool current_deadline = framejacker_overlay::deadline_current(frame, now);
                report["expiring_frames_received"] = report["expiring_frames_received"].asUInt()+1;
                if (!current_deadline) {
                    report["stale_frames_dropped"] = report["stale_frames_dropped"].asUInt()+1;
                    continue;
                }
            }
            expiry = requested_expiry;
            current = true;
            report["frames_received"] = report["frames_received"].asUInt()+1;
            if (!validate) framejacker_overlay::dispatch_frame(overlay, frame);
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
    constexpr unsigned w=320,h=128;
    std::vector<std::uint8_t> pixels(w*h*4,255);
    const auto paint = [&](unsigned x, unsigned y, std::uint8_t value) {
        const auto p=(y*w+x)*4;
        pixels[p]=pixels[p+1]=pixels[p+2]=value;
    };
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x)
        paint(x,y,(x<4 || y<4 || x>=w-4 || y>=h-4) ? 255 : 32);
    // Four distinct binary corner tags; the outer border marks the full texture.
    // This is a generated marker, not an automatic eye/layout detector.
    for (unsigned tag=0;tag<4;++tag) {
        const unsigned x0=tag%2 ? w-32 : 8, y0=tag/2 ? h-32 : 8;
        for (unsigned y=0;y<24;++y) for (unsigned x=0;x<24;++x)
            paint(x0+x,y0+y,(x<2 || y<2 || x>=22 || y>=22) ? 0 : 255);
        unsigned decoded=0;
        for (unsigned bit=0;bit<4;++bit) {
            const unsigned x=x0+4+(bit%2)*8,y=y0+4+(bit/2)*8;
            const bool white=((tag+1)>>bit)&1U;
            for (unsigned dy=0;dy<6;++dy) for (unsigned dx=0;dx<6;++dx)
                paint(x+dx,y+dy,white ? 255 : 0);
            if (pixels[(y*w+x)*4]==255) decoded|=1U<<bit;
        }
        description["corner_ids"].append(decoded);
    }
    unsigned opaque=0;
    for (std::size_t i=3;i<pixels.size();i+=4) opaque+=pixels[i]==255;
    description["width"]=w; description["height"]=h;
    description["opaque_pixels"]=opaque; description["border_pixels"]=4;
    description["tag_size_pixels"]=24; description["tag_inset_pixels"]=8;
    description["corner_order"]="top_left,top_right,bottom_left,bottom_right";
    description["config_id"]="fixed-edge-hud-v1";
    description["visible_in_dashboard"]=kCalibrationVisibleInDashboard;
    description["panel_width_m"]=applied.width; description["distance_m"]=applied.distance;
    description["x_m"]=applied.x; description["y_m"]=applied.y; description["opacity"]=applied.alpha;
    return pixels;
}
} // namespace

int main(int argc, char* argv[]) {
    const std::string mode=argc>1 ? argv[1] : "";
    if (mode=="--help" && argc==2) {
        std::cout << "Usage: framejacker-overlay-bridge --test-panel [--seconds 1..30]\n"
                     "       framejacker-overlay-bridge --stdin\n"
                     "       framejacker-overlay-bridge --validate-stdin (no OpenVR calls)\n"
                     "       framejacker-overlay-bridge --validate-calibration-marker --seconds 1..20\n"
                     "       framejacker-overlay-bridge --calibration-marker --seconds 1..20 --allow-calibration-output\n";
        return 0;
    }
    Json::Value report;
    report["event"]="closed"; report["runtime_initialized"]=false;
    report["frames_received"]=0U; report["frames_submitted"]=0U; report["settings_received"]=0U;
    report["expired_frames"]=0U; report["stale_frames_dropped"]=0U;
    report["expiring_frames_received"]=0U;
    report["presentation_state_scope"]="software_state_before_close_not_headset_evidence";
    report["mode"]=mode;
    const bool marker=mode=="--calibration-marker" || mode=="--validate-calibration-marker";
    const bool validate=mode=="--validate-stdin" || mode=="--validate-calibration-marker";
    int seconds=10;
    try {
        if (marker) {
            if (argc!=(validate ? 4 : 5) || std::string(argv[2])!="--seconds" ||
                    (!validate && std::string(argv[4])!="--allow-calibration-output"))
                throw std::runtime_error("calibration_requires_separate_explicit_opt_in");
            std::size_t used=0;
            seconds=std::stoi(argv[3],&used);
            if (used!=std::strlen(argv[3]) || seconds<1 || seconds>20) throw std::runtime_error("seconds_out_of_range");
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
        overlay.config=Settings{.32f,1.25f,.55f,-.35f,1.f,true};
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
    try {
        if (stopping) throw Stopped{};
        if (!validate) overlay.init(marker);
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
            if (!validate) { overlay.frame(320,128,pixels.data()); report["frames_submitted"]=1U; }
            report["marker_submit_returned_ns"]=Json::UInt64(monotonic_ns());
            if (monotonic_ns()>=marker_deadline) throw std::runtime_error("calibration_submit_overran_deadline");
            if (!validate) overlay.calibration_visibility(report["visibility_after_submit"]);
            while (monotonic_ns()<marker_deadline) {
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
                if (stopping) throw Stopped{};
                overlay.events(); std::this_thread::sleep_for(std::chrono::milliseconds(16));
            }
        } else stream(overlay,validate,report);
        report["status"]=marker ? (validate ? "validated_marker" : "marker_complete") : (validate ? "validated" : "closed");
    } catch (const Stopped&) {
        report["status"]="stopped";
    } catch (const std::exception& error) {
        report["event"]="error"; report["status"]="error";
        report["error"]=error.what(); exit_code=1;
    }
    report["runtime_initialized"]=overlay.active;
    if (marker) {
        report["overlay_shown_event_seen"]=overlay.overlay_shown_event_seen;
        report["overlay_hidden_event_seen"]=overlay.overlay_hidden_event_seen;
        const auto cutoff=monotonic_ns();
        report["cutoff_requested_ns"]=Json::UInt64(cutoff);
        report["software_deadline_overshoot_ms"]=cutoff>marker_deadline ? double(cutoff-marker_deadline)/1e6 : 0.;
    }
    const bool ok=overlay.close();
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
    emit(report);
    return exit_code;
}
