// Camera helper, fake backend: implements Backend without the SDK, scripted
// from a small text file, so every mode runs in CI on both architectures. It
// follows the SDK's session rules as the headers state them: calls before a
// connect fail NotConnected; a transport, protocol or socket-timeout failure
// closes the session and invalidates the held frame set; a server-side Result
// (permission denied, client already connected, not ready, timeout) leaves
// the session as it was; at most one frame set is outstanding.
//
// Script: one step per line, "<call> <outcome> [key=value ...]", '#' comments.
//   call     connect | control_state | reader | acquire | release
//   outcome  ok | server <result> | not_connected | invalid_argument | transport [errno=N]
//            | timeout | protocol | out_of_memory
//   result   success unavailable invalid_argument client_protocol_too_new not_ready internal_error
//            timeout resource_denied client_already_connected permission_denied client_protocol_too_old
// Steps of one call are consumed in order; an exhausted call answers ok, except
// acquire, which answers "server timeout" (no frame) so a run never finds
// frames the script did not give it. A step may carry repeat=N or repeat=forever.
// The metadata keys are listed in docs/reference/camera-helper.md.
#include "camera_helper.hpp"
#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

namespace sightcircuit_camera {
namespace {
struct Step {
    std::string call;
    CallStatus status;
    std::map<std::string, std::string> keys;
    std::uint64_t repeat = 1;  // 0 means forever
};
struct Script {
    std::vector<Step> steps;
    std::map<std::string, std::vector<std::size_t>> by_call;  // indexes into steps, per call, in order
};
constexpr std::uint64_t kDefaultModifier = 0x0500000000000001ULL;  // the UBWC modifier tools/capture_import.hpp pins
constexpr std::uint32_t kDrmFormatNv12 = 0x3231564eu;             // fourcc 'NV12'
constexpr std::uint32_t kDrmFormatP010 = 0x30313050u;             // fourcc 'P010'

bool parse_number(const std::string& text, std::uint64_t& out) {
    if (text.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long value = std::strtoull(text.c_str(), &end, 0);
    if (errno || *end) return false;
    out = value;
    return true;
}
bool parse_double(const std::string& text, double& out) {
    if (text.empty()) return false;
    char* end = nullptr;
    out = std::strtod(text.c_str(), &end);
    return *end == 0;
}
bool parse_result(const std::string& text, std::uint32_t& out) {
    static const std::map<std::string, std::uint32_t> names = {
        {"success", kResultSuccess}, {"unavailable", kResultUnavailable}, {"invalid_argument", kResultInvalidArgument},
        {"client_protocol_too_new", kResultClientProtocolTooNew}, {"not_ready", kResultNotReady},
        {"internal_error", kResultInternalError}, {"timeout", kResultTimeout}, {"resource_denied", kResultResourceDenied},
        {"client_already_connected", kResultClientAlreadyConnected}, {"permission_denied", kResultPermissionDenied},
        {"client_protocol_too_old", kResultClientProtocolTooOld}};
    const auto found = names.find(text);
    if (found == names.end()) return false;
    out = found->second;
    return true;
}
// Named values of the per-frame and control enumerators (mirrors of AV1CameraIPCProtocol.h).
bool parse_named(const std::string& key, const std::string& text, std::uint32_t& out) {
    static const std::map<std::string, std::map<std::string, std::uint32_t>> tables = {
        {"format", {{"unknown", kPixelUnknown}, {"nv12", kPixelNv12}, {"nv12_ubwc", kPixelNv12Ubwc},
                    {"p010", kPixelP010}, {"p010_ubwc", kPixelP010Ubwc}}},
        {"transfer", {{"linear", 0}, {"srgb", 2}, {"hlg", 4}}},            // TransferFunctionValue
        {"primaries", {{"bt709", 1}, {"bt2020", 2}}},                      // ColorPrimariesValue
        {"encoding", {{"bt601", 1}, {"bt709", 2}, {"bt2020", 3}}},         // YCbCrEncodingValue
        {"resolution", {{"auto", 1}, {"full_resolution", 2}, {"downsampled", 4}}},  // ResolutionModeValue
        {"awb", {{"on", 1}, {"off", 2}, {"freeze", 3}}},                   // AutoWhiteBalanceModeValue
        {"model", {{"kb4", 0}, {"kb_spline", 1}, {"kb_polar_spline", 2}, {"pinhole", 3}}},  // CameraFrameModel
        {"offline_cal_source", {{"none", 0}, {"eeprom", 1}, {"file", 2}, {"default", 3}}}};
    const auto table = tables.find(key);
    if (table == tables.end()) return false;
    const auto found = table->second.find(text);
    if (found == table->second.end()) { std::uint64_t number; if (!parse_number(text, number)) return false; out = static_cast<std::uint32_t>(number); return true; }
    out = found->second;
    return true;
}
std::string parse_line(const std::string& line, Step& step) {
    std::istringstream words(line);
    std::string outcome;
    if (!(words >> step.call >> outcome)) return "missing_call_or_outcome";
    if (step.call != "connect" && step.call != "control_state" && step.call != "reader" && step.call != "acquire" &&
        step.call != "release") return "unknown_call:" + step.call;
    if (outcome == "ok") step.status = CallStatus{};
    else if (outcome == "server") {
        std::string result;
        if (!(words >> result) || !parse_result(result, step.status.result)) return "server_outcome_needs_a_result_name";
        step.status.error = CallError::Server;
    } else if (outcome == "not_connected") step.status.error = CallError::NotConnected;
    else if (outcome == "invalid_argument") step.status.error = CallError::InvalidArgument;
    else if (outcome == "transport") step.status.error = CallError::Transport;
    else if (outcome == "timeout") step.status.error = CallError::Timeout;
    else if (outcome == "protocol") step.status.error = CallError::Protocol;
    else if (outcome == "out_of_memory") step.status.error = CallError::OutOfMemory;
    else return "unknown_outcome:" + outcome;
    std::string pair;
    while (words >> pair) {
        const auto equals = pair.find('=');
        if (equals == std::string::npos || equals == 0 || equals + 1 == pair.size()) return "malformed_key_value:" + pair;
        const std::string key = pair.substr(0, equals), value = pair.substr(equals + 1);
        if (step.keys.count(key)) return "repeated_key:" + key;
        if (key == "repeat") {
            if (value == "forever") step.repeat = 0;
            else if (!parse_number(value, step.repeat) || step.repeat == 0) return "repeat_needs_a_count_or_forever";
        } else if (key == "errno") {
            std::uint64_t number;
            if (step.status.error != CallError::Transport || !parse_number(value, number) || number > 0x7fffffff) return "errno_needs_transport_and_a_number";
            step.status.system_error = static_cast<int>(number);
        } else step.keys[key] = value;
    }
    return "";
}
std::string parse_script(const std::string& path, Script& script) {
    std::ifstream file(path);
    if (!file) return "fake_script_unreadable";
    std::string line;
    std::size_t number = 0;
    while (std::getline(file, line)) {
        ++number;
        const auto hash = line.find('#');
        if (hash != std::string::npos) line.erase(hash);
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        Step step;
        const std::string error = parse_line(line, step);
        if (!error.empty()) return "fake_script_line_" + std::to_string(number) + ":" + error;
        script.by_call[step.call].push_back(script.steps.size());
        script.steps.push_back(std::move(step));
    }
    return "";
}
// A key's numeric value, or `fallback`; `error` names the first key that fails to parse.
struct Keys {
    const std::map<std::string, std::string>& keys;
    std::string error;
    std::uint64_t number(const char* key, std::uint64_t fallback) {
        const auto found = keys.find(key);
        if (found == keys.end()) return fallback;
        std::uint64_t value;
        if (!parse_number(found->second, value)) { if (error.empty()) error = std::string("bad_number:") + key; return fallback; }
        return value;
    }
    std::uint32_t named(const char* key, std::uint32_t fallback) {
        const auto found = keys.find(key);
        if (found == keys.end()) return fallback;
        std::uint32_t value;
        if (!parse_named(key, found->second, value)) { if (error.empty()) error = std::string("bad_name:") + key; return fallback; }
        return value;
    }
    double real(const char* key, double fallback) {
        const auto found = keys.find(key);
        if (found == keys.end()) return fallback;
        double value;
        if (!parse_double(found->second, value)) { if (error.empty()) error = std::string("bad_real:") + key; return fallback; }
        return value;
    }
};
void apply_controls(Keys& keys, ControlState& controls) {
    controls.red_recording_led_enabled = static_cast<std::uint32_t>(keys.number("red_led", controls.red_recording_led_enabled));
    controls.sensor_bit_depth = static_cast<std::uint32_t>(keys.number("bit_depth", controls.sensor_bit_depth));
    controls.auto_white_balance_mode = keys.named("awb", controls.auto_white_balance_mode);
    controls.hdr_enabled = static_cast<std::uint32_t>(keys.number("hdr", controls.hdr_enabled));
    controls.tone_mapping_enabled = static_cast<std::uint32_t>(keys.number("tone_mapping", controls.tone_mapping_enabled));
    controls.transfer_function = keys.named("transfer", controls.transfer_function);
    controls.color_primaries = keys.named("primaries", controls.color_primaries);
    controls.ycbcr_encoding = keys.named("encoding", controls.ycbcr_encoding);
    controls.resolution_mode = keys.named("resolution", controls.resolution_mode);
    controls.current_fps = static_cast<float>(keys.real("fps", controls.current_fps));
    controls.brightness_ev = static_cast<float>(keys.real("brightness_ev", controls.brightness_ev));
    controls.auto_exposure_enabled = static_cast<std::uint32_t>(keys.number("auto_exposure", controls.auto_exposure_enabled));
    controls.exposure_ms = static_cast<float>(keys.real("exposure_ms", controls.exposure_ms));
    controls.gain = static_cast<float>(keys.real("gain", controls.gain));
}
ControlState default_controls() {
    ControlState controls;
    controls.supported_controls = 0x47ffu;  // every ControlBits flag the header names
    controls.current_controls = 0;
    controls.supported_sensor_bit_depths = 3; controls.supported_auto_white_balance_modes = 7;
    controls.supported_transfer_functions = 7; controls.supported_color_primaries = 3;
    controls.supported_ycbcr_encodings = 7; controls.supported_resolution_modes = 7;
    controls.sensor_bit_depth = 8; controls.auto_white_balance_mode = 1; controls.transfer_function = 2;
    controls.color_primaries = 1; controls.ycbcr_encoding = 2; controls.resolution_mode = 1;
    controls.current_fps = 60.0f; controls.auto_exposure_enabled = 1;
    controls.exposure_ms = 8.0f; controls.gain = 1.5f;
    controls.min_exposure_ms = 0.1f; controls.max_exposure_ms = 33.0f; controls.min_gain = 1.0f; controls.max_gain = 16.0f;
    return controls;
}

class FakeBackend final : public Backend {
public:
    explicit FakeBackend(Script script) : script_(std::move(script)) {}
    const char* name() const noexcept override { return "fake"; }
    CallStatus connect(std::uint32_t timeout_ms) noexcept override {
        if (timeout_ms == 0) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        if (connected_) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        const Step* step = next("connect");
        const CallStatus status = step ? step->status : CallStatus{};
        if (!status.ok()) return status;
        hello_ = Hello{};
        hello_.protocol_version = 1; hello_.camera_count = kMaxCameras; hello_.offline_cal_source = 1;
        hello_.controls = default_controls();
        if (step) {
            Keys keys{step->keys, ""};
            hello_.protocol_version = static_cast<std::uint32_t>(keys.number("protocol_version", hello_.protocol_version));
            hello_.camera_count = static_cast<std::uint32_t>(keys.number("camera_count", hello_.camera_count));
            hello_.offline_cal_source = keys.named("offline_cal_source", hello_.offline_cal_source);
            hello_.offline_cal_size = static_cast<std::uint32_t>(keys.number("offline_cal_size", hello_.offline_cal_size));
            apply_controls(keys, hello_.controls);
            if (!keys.error.empty()) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        }
        connected_ = true;
        return status;
    }
    void disconnect() noexcept override { connected_ = false; held_ = false; reader_active_ = false; }
    bool connected() const noexcept override { return connected_; }
    const Hello& hello() const noexcept override { return hello_; }
    CallStatus control_state(ControlState& out) noexcept override {
        if (!connected_) return CallStatus{CallError::NotConnected, kResultSuccess, 0};
        const Step* step = next("control_state");
        const CallStatus status = settle(step ? step->status : CallStatus{});
        if (!status.ok()) return status;
        out = hello_.controls;
        if (step) { Keys keys{step->keys, ""}; apply_controls(keys, out); if (!keys.error.empty()) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0}; }
        return status;
    }
    CallStatus set_reader_active(bool active) noexcept override {
        if (!connected_) return CallStatus{CallError::NotConnected, kResultSuccess, 0};
        const Step* step = next("reader");
        const CallStatus status = settle(step ? step->status : CallStatus{});
        if (status.ok()) reader_active_ = active;
        return status;
    }
    CallStatus acquire_frame_set(FrameSetMetadata& out, std::uint32_t frame_timeout_ms) noexcept override {
        (void)frame_timeout_ms;
        if (!connected_) return CallStatus{CallError::NotConnected, kResultSuccess, 0};
        if (held_) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        // Frames reach only an active reader; the script's frames wait until it is enabled.
        if (!reader_active_) return CallStatus{CallError::Server, kResultNotReady, 0};
        const Step* step = next("acquire");
        // Without a scripted frame there is none: the server's frame timeout, session open.
        const CallStatus status = settle(step ? step->status : CallStatus{CallError::Server, kResultTimeout, 0});
        if (!status.ok()) return status;
        Keys keys{step->keys, ""};
        out = FrameSetMetadata{};
        out.sequence = keys.number("sequence", sequence_ + 1);
        out.camera_count = static_cast<std::uint32_t>(keys.number("cameras", kMaxCameras));
        if (out.camera_count > kMaxCameras) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        out.pose_valid = static_cast<std::uint32_t>(keys.number("pose_valid", 1));
        out.velocity_valid = static_cast<std::uint32_t>(keys.number("velocity_valid", 1));
        for (std::uint32_t index = 0; index < out.camera_count; ++index) {
            CameraMetadata& camera = out.cameras[index];
            camera.frame_id = keys.number("frame_id", frame_id_ + 1) + index * 1000000ULL;
            camera.exposure_timestamp_ns = static_cast<std::int64_t>(keys.number("exposure_ns", out.sequence * 16666667ULL));
            camera.timestamp_seconds = keys.real("timestamp_s", static_cast<double>(camera.exposure_timestamp_ns) / 1e9);
            camera.width = static_cast<std::uint32_t>(keys.number("width", 1280));
            camera.height = static_cast<std::uint32_t>(keys.number("height", 1024));
            camera.pixel_format = keys.named("format", kPixelNv12Ubwc);
            const bool ten_bit = camera.pixel_format == kPixelP010 || camera.pixel_format == kPixelP010Ubwc;
            camera.drm_format = static_cast<std::uint32_t>(keys.number("drm_format", ten_bit ? kDrmFormatP010 : kDrmFormatNv12));
            const bool ubwc = camera.pixel_format == kPixelNv12Ubwc || camera.pixel_format == kPixelP010Ubwc;
            camera.drm_modifier = keys.number("modifier", ubwc ? kDefaultModifier : 0);
            camera.plane_count = static_cast<std::uint32_t>(keys.number("planes", 2));
            if (camera.plane_count > kMaxPlanes) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
            const std::uint32_t stride = static_cast<std::uint32_t>(keys.number("stride", camera.width * (ten_bit ? 2 : 1)));
            std::uint32_t offset = 0;
            for (std::uint32_t plane = 0; plane < camera.plane_count; ++plane) {
                camera.planes[plane].offset = offset;
                camera.planes[plane].stride = stride;
                camera.planes[plane].size = plane == 0 ? stride * camera.height : stride * camera.height / 2;
                offset += camera.planes[plane].size;
            }
            camera.transfer_function = keys.named("transfer", 2);
            camera.color_primaries = keys.named("primaries", 1);
            camera.ycbcr_encoding = keys.named("encoding", 2);
            camera.sensor_temperature_celsius = static_cast<float>(keys.real("temperature_c", 40.0));
            camera.exposure_seconds = static_cast<float>(keys.real("exposure_s", 0.008));
            camera.gain = static_cast<float>(keys.real("gain", 1.5));
            camera.intrinsics_valid = static_cast<std::uint32_t>(keys.number("intrinsics_valid", 1));
            camera.intrinsics_model = keys.named("model", 0);
            camera.kb4_intrinsics_valid = static_cast<std::uint32_t>(keys.number("kb4_valid", 1));
            camera.first_camera_from_camera_valid = index == 0 ? 1 : static_cast<std::uint32_t>(keys.number("transform_valid", 1));
            camera.aux_json_size = static_cast<std::uint32_t>(keys.number("aux_json_size", 0));
        }
        if (!keys.error.empty()) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        sequence_ = out.sequence; frame_id_ = out.cameras[0].frame_id;
        held_ = true;
        return status;
    }
    CallStatus release_frame_set() noexcept override {
        if (!connected_) { held_ = false; return CallStatus{CallError::NotConnected, kResultSuccess, 0}; }
        if (!held_) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        const Step* step = next("release");
        const CallStatus status = settle(step ? step->status : CallStatus{});
        if (status.ok()) held_ = false;
        return status;
    }
    bool frame_set_held() const noexcept override { return held_; }

private:
    // The next scripted step of a call, honouring repeat; nullptr when the script has none left.
    const Step* next(const char* call) {
        auto found = script_.by_call.find(call);
        if (found == script_.by_call.end()) return nullptr;
        auto& indexes = found->second;
        auto& used = used_[call];
        while (used < indexes.size()) {
            const Step& step = script_.steps[indexes[used]];
            auto& count = repeats_[indexes[used]];
            if (step.repeat == 0 || count < step.repeat) { ++count; return &step; }
            ++used;
        }
        return nullptr;
    }
    // Apply the SDK's session rule to an outcome: a closing failure ends the session and its frame set.
    CallStatus settle(CallStatus status) noexcept {
        if (status.closes_session()) { connected_ = false; held_ = false; reader_active_ = false; }
        return status;
    }
    Script script_;
    std::map<std::string, std::size_t> used_;
    std::map<std::size_t, std::uint64_t> repeats_;
    Hello hello_;
    bool connected_ = false, held_ = false, reader_active_ = false;
    std::uint64_t sequence_ = 0, frame_id_ = 0;
};
} // namespace

const char* backend_name() noexcept { return "fake"; }
bool validate_backend_options(const BackendOptions& options, Json& report, std::string& error) {
    Script script;
    if (!options.fake_script.empty()) {
        error = parse_script(options.fake_script, script);
        if (!error.empty()) return false;
    }
    report.set("fake_script_steps", static_cast<std::uint64_t>(script.steps.size()));
    Json per_call = Json::object();
    for (const char* call : {"connect", "control_state", "reader", "acquire", "release"}) {
        const auto found = script.by_call.find(call);
        per_call.set(call, static_cast<std::uint64_t>(found == script.by_call.end() ? 0 : found->second.size()));
    }
    report.set("fake_script_steps_by_call", per_call);
    return true;
}
std::unique_ptr<Backend> make_backend(const BackendOptions& options, std::string& error) {
    Script script;
    if (!options.fake_script.empty()) {
        error = parse_script(options.fake_script, script);
        if (!error.empty()) return nullptr;
    }
    return std::unique_ptr<Backend>(new FakeBackend(std::move(script)));
}
} // namespace sightcircuit_camera
