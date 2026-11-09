// Camera helper, reporting unit: the JSON value, every protocol line the helper
// writes and the names of the mirrored enumerators. Field names are the
// helper's contract with its tests and, later, a Python client; a change here
// is a protocol change. emit() is the only writer of JSON lines, through the
// saved real stdout of overlay_lifecycle.hpp.
#include "camera_helper.hpp"
#include <cmath>
#include <cstdio>

namespace sightcircuit_camera {
Json::Json(double value) : kind_(Kind::Number) {
    if (!std::isfinite(value)) { kind_ = Kind::Null; return; }
    char buffer[64];
    std::snprintf(buffer, sizeof buffer, "%.9g", value);
    text_ = buffer;
}
Json& Json::set(const std::string& key, Json value) {
    for (auto& member : members_) if (member.first == key) { member.second = std::move(value); return *this; }
    members_.emplace_back(key, std::move(value));
    return *this;
}
const Json& Json::get(const std::string& key) const noexcept {
    static const Json null;
    for (const auto& member : members_) if (member.first == key) return member.second;
    return null;
}
Json& Json::push(Json value) { items_.push_back(std::move(value)); return *this; }
std::string json_escape(const std::string& text) {
    std::string out = "\"";
    for (const unsigned char c : text) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) { char buffer[8]; std::snprintf(buffer, sizeof buffer, "\\u%04x", c); out += buffer; }
            else out += static_cast<char>(c);
        }
    }
    return out + "\"";
}
std::string Json::dump() const {
    switch (kind_) {
    case Kind::Null: return "null";
    case Kind::Boolean: case Kind::Number: return text_;
    case Kind::String: return json_escape(text_);
    case Kind::Object: {
        std::string out = "{";
        for (std::size_t i = 0; i < members_.size(); ++i) {
            if (i) out += ",";
            out += json_escape(members_[i].first) + ":" + members_[i].second.dump();
        }
        return out + "}";
    }
    case Kind::Array: {
        std::string out = "[";
        for (std::size_t i = 0; i < items_.size(); ++i) { if (i) out += ","; out += items_[i].dump(); }
        return out + "]";
    }
    }
    return "null";
}

const char* call_error_name(CallError error) noexcept {
    switch (error) {
    case CallError::None: return "None";
    case CallError::Server: return "Server";
    case CallError::NotConnected: return "NotConnected";
    case CallError::InvalidArgument: return "InvalidArgument";
    case CallError::Transport: return "Transport";
    case CallError::Timeout: return "Timeout";
    case CallError::Protocol: return "Protocol";
    case CallError::OutOfMemory: return "OutOfMemory";
    }
    return "unknown";
}
const char* result_name(std::uint32_t result) noexcept {
    switch (result) {
    case kResultSuccess: return "Result_Success";
    case kResultUnavailable: return "Result_Unavailable";
    case kResultInvalidArgument: return "Result_InvalidArgument";
    case kResultClientProtocolTooNew: return "Result_ClientProtocolTooNew";
    case kResultNotReady: return "Result_NotReady";
    case kResultInternalError: return "Result_InternalError";
    case kResultTimeout: return "Result_Timeout";
    case kResultResourceDenied: return "Result_ResourceDenied";
    case kResultClientAlreadyConnected: return "Result_ClientAlreadyConnected";
    case kResultPermissionDenied: return "Result_PermissionDenied";
    case kResultClientProtocolTooOld: return "Result_ClientProtocolTooOld";
    }
    return "unknown";
}
const char* pixel_format_name(std::uint32_t format) noexcept {
    switch (format) {
    case kPixelUnknown: return "PixelFormat_Unknown";
    case kPixelNv12: return "PixelFormat_NV12";
    case kPixelNv12Ubwc: return "PixelFormat_NV12_UBWC";
    case kPixelP010: return "PixelFormat_P010";
    case kPixelP010Ubwc: return "PixelFormat_P010_UBWC";
    }
    return "unknown";
}

void emit(Json value) {
    value.set("report_kind", kReportKind);
    value.set("schema_version", kSchemaVersion);
    value.set("backend", backend_name());
    // Only the saved real stdout carries the protocol; fd 1 points at stderr.
    lifecycle::write_protocol(value.dump() + "\n");
}
// The claims every final report carries. They are constants of this revision,
// not measurements: the interface has no path that could make one true.
void record_claims(Json& report) {
    report.set("camera_connected", false);
    report.set("reader_enabled", false);
    report.set("recording_requested", false);
    report.set("controls_set", false);
    report.set("led_touched", false);
    report.set("frame_sets_acquired", 0U);
    report.set("pixels_accessed", false);
    report.set("frame_data_written", false);
}
std::string watchdog_line(const std::string& mode, const char* phase, unsigned seconds) {
    Json value = Json::object();
    value.set("event", "error"); value.set("status", "error");
    value.set("error", std::string("watchdog_expired:") + phase);
    value.set("mode", mode); value.set("watchdog_phase", phase); value.set("watchdog_seconds", seconds);
    value.set("exit_code", lifecycle::kWatchdogExitCode);
    value.set("cleanup_ok", false); value.set("cleanup_skipped_by_watchdog", true);
    value.set("report_kind", kReportKind); value.set("schema_version", kSchemaVersion);
    value.set("backend", backend_name());
    return value.dump() + "\n";
}
std::string usage_text() {
    return std::string("Usage: sightcircuit-camera-helper-") + backend_name() + " --validate [--fake-script FILE]\n"
           "       sightcircuit-camera-helper-" + backend_name() + " --help\n"
           "--validate initialises nothing: no client is constructed, no socket is opened, no camera\n"
           "is touched. It checks the arguments (and, in the fake build, parses the script file) and\n"
           "writes one JSON report. A SIGALRM watchdog ends a stalled step with one JSON error.\n"
           "The modes that connect (--describe, --hello, --probe-frames) are step 2 of the camera\n"
           "lane and are not in this revision. Output is counts, enumerators and numbers only.\n";
}
Json base_report(const std::string& mode) {
    Json report = Json::object();
    report.set("event", "closed"); report.set("status", "closed"); report.set("mode", mode);
    report.set("validation_only", true);
    record_claims(report);
    report.set("cleanup_ok", true);
    report.set("exit_code", 0);
    return report;
}
} // namespace sightcircuit_camera
