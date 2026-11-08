// Overlay bridge, reporting unit: every protocol line the bridge writes (the
// ready, frame and watchdog lines and the final report) and the report fields
// each phase records. Field names and values are the bridge's contract with
// sightcircuit/overlay.py, sightcircuit/hud/marker.py and the evidence
// records; a change here is a protocol change. emit() is the only writer of
// JSON lines, through the saved real stdout of overlay_lifecycle.hpp.
#include "overlay_bridge.hpp"
#include <sstream>
#include <string>

namespace sightcircuit_bridge {
void emit(Json::Value value) {
    value["report_kind"] = "sightcircuit_overlay_bridge";
    value["schema_version"] = 1;
    // Only the saved real stdout carries the protocol; fd 1 points at stderr.
    lifecycle::write_protocol(Json::FastWriter().write(value));
}
std::string watchdog_line(const std::string& mode, const char* phase, unsigned seconds) {
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
    return Json::FastWriter().write(value);
}
std::string usage_text() {
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
                 "which keeps the Background client and never connects as Overlay (device check, opt-in).\n"
                 "The marker modes accept only the visibility request (opcode 5) on stdin, beside their display\n"
                 "timer, which end of input never ends; the stream modes take the whole pipe protocol.\n";
    return usage.str();
}
Json::Value closed_report(const std::string& mode) {
    Json::Value report;
    report["event"]="closed"; report["runtime_initialized"]=false;
    report["frames_received"]=0U; report["frames_submitted"]=0U; report["settings_received"]=0U;
    report["expired_frames"]=0U; report["stale_frames_dropped"]=0U;
    report["expiring_frames_received"]=0U;
    report["presentation_state_scope"]="software_state_before_close_not_headset_evidence";
    report["mode"]=mode;
    return report;
}
void describe_marker_run(Json::Value& report, int seconds, std::uint64_t start_ns, std::uint64_t deadline_ns) {
    report["calibration_only"]=true; report["seconds_requested"]=seconds;
    report["native_started_ns"]=Json::UInt64(start_ns);
    report["display_deadline_ns"]=Json::UInt64(deadline_ns);
    report["deadline_scope"]="best_effort_software_checks_around_synchronous_vr_calls";
    report["physical_display_recovery_verified"]=false;
    report["capture_started"]=false; report["inference_started"]=false;
    report["images_saved"]=false; report["input_frames_accepted"]=false;
    // Fallback item F3a: marker mode accepts visibility requests on stdin and counts them as the stream
    // report does; both stay zero in a run that receives nothing.
    report["visibility_requests_received"]=0U; report["visibility_events_missing"]=0U;
    auto& visibility=report["visibility_after_submit"];
    visibility["scope"]="runtime_queries_not_physical_visibility";
    visibility["sampled"]=false;
    visibility["api_visible"]=Json::Value();
    visibility["dashboard_visible"]=Json::Value();
    visibility["visible_in_dashboard"]=Json::Value();
}
void describe_connect(Json::Value& report, bool view_record, bool validate, bool background_only) {
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
}
void emit_ready(const std::string& mode, bool validate, bool marker, std::uint64_t marker_deadline_ns) {
    Json::Value ready;
    ready["event"]="ready"; ready["validation_only"]=validate;
    ready["frame_expiry_supported"]=mode=="--validate-stdin";
    ready["runtime_initialized"]=!validate; ready["overlay_interface"]=vr::IVROverlay_Version;
    if (marker) ready["display_deadline_ns"]=Json::UInt64(marker_deadline_ns);
    emit(ready);
}
void emit_test_panel_submitted(int seconds, bool api_visible) {
    Json::Value shown;
    shown["event"]="test_panel_submitted"; shown["seconds"]=seconds;
    shown["api_visible"]=api_visible;
    emit(shown);
}
void emit_frame_expired(std::uint64_t deadline_ns, std::uint64_t observed_ns, bool validate) {
    Json::Value event;
    event["event"] = "frame_expired";
    event["deadline_ns"] = Json::UInt64(deadline_ns);
    event["observed_ns"] = Json::UInt64(observed_ns);
    event["validation_only"] = validate;
    event["content_current"] = false;
    emit(event);
}
void emit_first_frame(bool validate, std::uint32_t width, std::uint32_t height) {
    Json::Value first;
    first["event"] = validate ? "frame_validated" : "frame_submitted";
    first["width"] = width; first["height"] = height;
    emit(first);
}
namespace {
Json::Value visibility_line(const char* name, std::uint32_t cycle, bool validate) {
    Json::Value line;
    line["event"] = name; line["cycle"] = cycle; line["validation_only"] = validate;
    return line;
}
} // namespace
void emit_visibility_requested(bool hide, std::uint32_t cycle, std::uint64_t request_ns, bool validate) {
    auto line = visibility_line(hide ? "hide_requested" : "show_requested", cycle, validate);
    line["request_ns"] = Json::UInt64(request_ns);
    emit(line);
}
void emit_visibility_returned(bool hide, std::uint32_t cycle, std::uint64_t returned_ns, bool ok, const char* call,
                              const std::string& reason, bool validate) {
    // call names the OpenVR call actually made ("none" in validation); ok is whether that call was the requested
    // transition and returned without error, and reason says why not (no_image, or the call's error name).
    auto line = visibility_line(hide ? "hide_returned" : "show_returned", cycle, validate);
    line["returned_ns"] = Json::UInt64(returned_ns); line["ok"] = ok; line["call"] = call;
    if (!ok) line["reason"] = reason;
    emit(line);
}
void emit_visibility_event(bool hide, std::uint32_t cycle, std::uint64_t observed_ns, bool validate) {
    auto line = visibility_line(hide ? "overlay_hidden" : "overlay_shown", cycle, validate);
    line["observed_ns"] = Json::UInt64(observed_ns);
    emit(line);
}
void emit_visibility_event_missing(std::uint32_t cycle, std::uint64_t waited_ns, bool validate) {
    auto line = visibility_line("visibility_event_missing", cycle, validate);
    line["waited_ns"] = Json::UInt64(waited_ns);
    emit(line);
}
void record_run_end(Json::Value& report, const Overlay& overlay, bool marker, std::uint64_t marker_deadline_ns) {
    report["runtime_initialized"]=overlay.active || overlay.probe_active;
    if (marker) {
        report["overlay_shown_event_seen"]=overlay.overlay_shown_event_seen;
        report["overlay_hidden_event_seen"]=overlay.overlay_hidden_event_seen;
        const auto cutoff=monotonic_ns();
        report["cutoff_requested_ns"]=Json::UInt64(cutoff);
        report["software_deadline_overshoot_ms"]=cutoff>marker_deadline_ns ? double(cutoff-marker_deadline_ns)/1e6 : 0.;
    }
}
int record_cleanup(Json::Value& report, const Overlay& overlay, bool marker, bool watchdog_armed, bool ok,
                   int exit_code) {
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
    return exit_code;
}
} // namespace sightcircuit_bridge
