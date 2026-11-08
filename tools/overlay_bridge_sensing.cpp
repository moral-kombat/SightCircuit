// Overlay bridge, sensing unit: everything the bridge reads. The monotonic
// clock every reported timestamp comes from, the stdin protocol (bounded
// reads, packet checks, the expiry poll, dispatch to the rendering unit), the
// runtime's events and visibility queries, and the read-only headset view
// record. Only GetHeadsetView* is ever called on IVRHeadsetView; the mirror's
// mode is never changed.
#include "overlay_bridge.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <functional>
#include <poll.h>
#include <stdexcept>
#include <time.h>
#include <unistd.h>

namespace sightcircuit_bridge {
std::uint64_t monotonic_ns() {
    timespec value{};
    if (clock_gettime(CLOCK_MONOTONIC, &value)) throw std::runtime_error("monotonic_clock_failed");
    return std::uint64_t(value.tv_sec)*1000000000ULL + std::uint64_t(value.tv_nsec);
}
// Read-only headset view record (package M): Background only, never an
// Overlay connect and never a Set* call on IVRHeadsetView.
void Overlay::view_record(Json::Value& connect, Json::Value& record) {
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
void Overlay::calibration_visibility(Json::Value& sample) {
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
void Overlay::events() {
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
        if (visibility_pending &&
                e.eventType == (visibility_pending_hide ? vr::VREvent_OverlayHidden : vr::VREvent_OverlayShown)) {
            // The event confirms the pending request; observed_ns is the poll time, on the same clock as
            // request_ns. Without a pending request an event is a flag only (the marker report).
            emit_visibility_event(visibility_pending_hide, visibility_cycle, monotonic_ns(), validation_only);
            visibility_pending = false;
        }
        if (e.eventType == vr::VREvent_Quit || e.eventType == vr::VREvent_OverlayClosed) throw Stopped{};
        if (e.eventType == vr::VREvent_ImageFailed) throw std::runtime_error("overlay_image_failed");
    }
}
void Overlay::await_visibility(std::uint32_t cycle, bool hide, std::uint64_t request_ns) {
    settle_visibility(request_ns, true);  // A still pending hide is reported missing: its event can no longer be told apart.
    visibility_pending = true; visibility_pending_hide = hide;
    visibility_cycle = cycle; visibility_request_ns = request_ns;
}
void Overlay::settle_visibility(std::uint64_t now, bool force) {
    if (!visibility_pending) return;
    if (!force && now < visibility_request_ns + contract::kVisibilityEventBoundNs) return;
    // Fail closed: a hide without its hidden event is reported once and opens no window; a show that
    // was never confirmed is forgotten without a line, as the design specifies the missing line for hides.
    if (visibility_pending_hide) {
        emit_visibility_event_missing(visibility_cycle, now > visibility_request_ns ? now - visibility_request_ns : 0,
                                      validation_only);
        ++visibility_events_missing;
    }
    visibility_pending = false;
}
namespace {
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
} // namespace
void stream(Overlay& overlay, bool validate, Json::Value& report) {
    std::array<std::uint8_t,contract::kPacketHeaderBytes> header{};
    std::uint64_t expiry = 0;
    bool current = false, visible = true;
    overlay.validation_only = validate;
    report["visibility_requests_received"] = 0U; report["visibility_events_missing"] = 0U;
    // Polled on every input wait, so the stdin loop keeps reading while a hidden event is awaited: the
    // expiry of the presented frame, then the bound on a pending visibility request. Neither blocks.
    const auto expire = [&] {
        const auto now = monotonic_ns();
        if (expiry && now >= expiry) {
            current = false;
            overlay.has_image = false;
            if (!validate) overlay.visibility();
            report["expired_frames"] = report["expired_frames"].asUInt()+1;
            emit_frame_expired(expiry, now, validate);
            expiry = 0;
        }
        overlay.settle_visibility(now);
        report["visibility_events_missing"] = overlay.visibility_events_missing;
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
                                   kind != contract::kOpcodeStop && kind != contract::kOpcodeExpiringFrame &&
                                   kind != contract::kOpcodeVisibility))
            throw std::runtime_error("invalid_packet_header");
        if ((kind == contract::kOpcodeSettings && size != contract::kSettingsBytes) ||
                (kind == contract::kOpcodeStop && size) ||
                (kind == contract::kOpcodeVisibility && size != contract::kVisibilityBytes) ||
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
        } else if (kind == contract::kOpcodeVisibility) {
            // One fallback cycle's show or hide: the request time before the call, the return time after
            // it (the error, if any, is reported on the line and then ends the run as every other call
            // error does), then the runtime's event is awaited by polling.
            const auto cycle = read_u32(payload.data()+contract::kVisibilityCycleOffset);
            const auto requested = read_u32(payload.data()+contract::kVisibilityVisibleOffset);
            if (requested > 1) throw std::runtime_error("invalid_visibility");
            const bool hide = requested == 0;
            report["visibility_requests_received"] = report["visibility_requests_received"].asUInt()+1;
            const auto request_ns = monotonic_ns();
            overlay.settle_visibility(request_ns, true);  // A superseded request is settled before the new lines.
            emit_visibility_requested(hide, cycle, request_ns, validate);
            visible = !hide;
            overlay.config.visible = visible;
            if (!validate) {
                try { overlay.visibility(); }
                catch (const std::exception&) {
                    emit_visibility_returned(hide, cycle, monotonic_ns(), false, validate);
                    throw;
                }
            }
            emit_visibility_returned(hide, cycle, monotonic_ns(), true, validate);
            overlay.await_visibility(cycle, hide, request_ns);
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
            if (report["frames_received"].asUInt() == 1) emit_first_frame(validate, w, h);
        }
        expire();
    }
    // Input ended (stop or EOF) with a request still pending: a hide is reported missing, fail closed.
    overlay.settle_visibility(monotonic_ns(), true);
    report["visibility_events_missing"] = overlay.visibility_events_missing;
}
} // namespace sightcircuit_bridge
