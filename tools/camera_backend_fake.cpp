// The fake camera backend, built only with -DSIGHTCIRCUIT_CAMERA_BACKEND_FAKE=1.
// It drives the helper through every outcome on any architecture, without the
// SDK headers or the Client Library. Every record it produces says backend
// "fake", so fake output can never pass for device output. It records each
// backend call in order; the helper lists them as fake_calls.
// Exposure timestamps are reported on CLOCK_MONOTONIC, so the helper's
// clock-offset schema is exercised against a known clock.
#include <cerrno>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>

#include <time.h>
#include <unistd.h>

#include "camera_backend.hpp"
#include "camera_sha256.hpp"

namespace sightcircuit_camera {
namespace {

const char kFakeOfflineCal[] = "{\"fake\": \"calibration\"}";
const uint32_t kFakeOfflineCalSize = sizeof(kFakeOfflineCal) - 1;

enum Scenario {
    ok_linear_nv12, ok_ubwc_nv12, ok_p010, slow_ok,
    permission_denied, permission_denied_at_reader,
    protocol_too_new, protocol_too_old, already_connected, resource_denied,
    provider_unreachable,
    server_timeout_then_ok, not_ready, unavailable,
    transport_lost_mid_run, release_fails,
    standby_gap,
    format_changes, controls_change_mid_run, sequence_gap,
    bad_camera_count, bad_plane_count,
    hang_in_acquire, hang_in_release,
    unknown_scenario
};

struct ScenarioName { const char* name; Scenario scenario; };
const ScenarioName kScenarios[] = {
    {"ok_linear_nv12", ok_linear_nv12}, {"ok_ubwc_nv12", ok_ubwc_nv12}, {"ok_p010", ok_p010}, {"slow_ok", slow_ok},
    {"permission_denied", permission_denied}, {"permission_denied_at_reader", permission_denied_at_reader},
    {"protocol_too_new", protocol_too_new}, {"protocol_too_old", protocol_too_old},
    {"already_connected", already_connected}, {"resource_denied", resource_denied},
    {"provider_unreachable", provider_unreachable},
    {"server_timeout_then_ok", server_timeout_then_ok}, {"not_ready", not_ready}, {"unavailable", unavailable},
    {"transport_lost_mid_run", transport_lost_mid_run}, {"release_fails", release_fails},
    {"standby_gap", standby_gap},
    {"format_changes", format_changes}, {"controls_change_mid_run", controls_change_mid_run},
    {"sequence_gap", sequence_gap},
    {"bad_camera_count", bad_camera_count}, {"bad_plane_count", bad_plane_count},
    {"hang_in_acquire", hang_in_acquire}, {"hang_in_release", hang_in_release},
};

Scenario parse_scenario(const std::string& name) {
    for (std::size_t i = 0; i < sizeof kScenarios / sizeof kScenarios[0]; ++i) {
        if (name == kScenarios[i].name) return kScenarios[i].scenario;
    }
    return unknown_scenario;
}

int64_t monotonic_ns() {
    timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return static_cast<int64_t>(now.tv_sec) * 1000000000ll + now.tv_nsec;
}

void sleep_ms(unsigned ms) {
    timespec request;
    request.tv_sec = ms / 1000u;
    request.tv_nsec = static_cast<long>(ms % 1000u) * 1000000l;
    while (nanosleep(&request, &request) != 0 && errno == EINTR) {}
}

void hang_forever() {
    for (;;) sleep_ms(1000);  // Only the SIGALRM watchdog ends this.
}

class FakeBackend : public CameraBackend {
public:
    explicit FakeBackend(Scenario scenario) : scenario_(scenario), acquires_(0), control_reads_(0), sequence_(100), lost_(false) {}

    const char* backend_name() const { return "fake"; }

    Outcome connect(uint32_t timeout_ms) {
        (void)timeout_ms;
        calls_.push_back("connect");
        switch (scenario_) {
            case protocol_too_new: return server("Result_ClientProtocolTooNew", Outcome::protocol_mismatch);
            case protocol_too_old: return server("Result_ClientProtocolTooOld", Outcome::protocol_mismatch);
            case already_connected: return server("Result_ClientAlreadyConnected", Outcome::camera_busy);
            case provider_unreachable:
                return client("Transport", ECONNREFUSED, "connection refused", Outcome::provider_unreachable);
            default: break;
        }
        hello_ = HelloFacts();
        hello_.protocol_version = 1;
        hello_.camera_count = 2;
        hello_.offline_cal_source = "eeprom";
        hello_.offline_cal_size = kFakeOfflineCalSize;
        hello_.offline_cal_size_within_bound = true;
        hello_.offline_cal_sha256 = sha256_hex(kFakeOfflineCal, kFakeOfflineCalSize);
        return success();
    }

    const HelloFacts& hello() const { return hello_; }

    Outcome control_state(ControlSummary& summary) {
        calls_.push_back("control_state");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::helper_error);
        ++control_reads_;
        if (scenario_ == permission_denied) return server("Result_PermissionDenied", Outcome::permission_denied);
        summary = ControlSummary();
        summary.supported_controls = "0x00000000000047ff";
        summary.current_controls = "0x0000000000000004";
        summary.supported_sensor_bit_depths = "0x00000003";
        summary.supported_auto_white_balance_modes = "0x00000007";
        summary.supported_transfer_functions = "0x00000007";
        summary.supported_color_primaries = "0x00000003";
        summary.supported_ycbcr_encodings = "0x00000007";
        summary.supported_resolution_modes = "0x00000007";
        summary.red_recording_led_enabled = 0;
        summary.sensor_bit_depth = scenario_ == ok_p010 ? 10 : 8;
        summary.auto_white_balance_mode = "on";
        summary.hdr_enabled = 0;
        summary.tone_mapping_enabled = 1;
        summary.transfer_function = "srgb";
        summary.color_primaries = "bt709";
        summary.ycbcr_encoding = "bt709";
        summary.resolution_mode = "auto";
        summary.brightness_ev = 0.0f;
        summary.auto_exposure_enabled = 1;
        summary.min_exposure_ms = 0.1f;
        summary.max_exposure_ms = 33.3f;
        summary.min_gain = 1.0f;
        summary.max_gain = 16.0f;
        summary.current_fps = 60.0f;
        if (scenario_ == controls_change_mid_run && control_reads_ >= 2) {
            // Another party changed the brightness and the LED while the reader was active.
            summary.brightness_ev = 0.5f;
            summary.red_recording_led_enabled = 1;
        }
        return success();
    }

    Outcome set_reader(bool active) {
        calls_.push_back(active ? "set_reader:true" : "set_reader:false");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::helper_error);
        if (active && scenario_ == permission_denied_at_reader) {
            return server("Result_PermissionDenied", Outcome::permission_denied);
        }
        if (active && scenario_ == resource_denied) return server("Result_ResourceDenied", Outcome::camera_busy);
        return success();
    }

    Outcome acquire(uint32_t timeout_ms, FrameSetMeta& meta) {
        calls_.push_back("acquire");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::helper_error);
        const unsigned pass = ++acquires_;
        switch (scenario_) {
            case hang_in_acquire: hang_forever(); break;
            case hang_in_release: calls_.push_back("release"); hang_forever(); break;
            case slow_ok: sleep_ms(timeout_ms > 300 ? 300 : timeout_ms + 250); break;
            case server_timeout_then_ok:
                if (pass <= 2) return server("Result_Timeout", Outcome::no_frame);
                break;
            case not_ready:
                if (pass <= 3) return server("Result_NotReady", Outcome::not_ready);
                break;
            case unavailable:
                if (pass >= 2) return server("Result_Unavailable", Outcome::unavailable);
                break;
            case standby_gap:
                if (pass >= 3 && pass <= 6) return server("Result_Timeout", Outcome::no_frame);
                break;
            case transport_lost_mid_run:
                if (pass >= 3) {
                    lost_ = true;
                    return client("Transport", EPIPE, "broken pipe", Outcome::transport_lost);
                }
                break;
            case release_fails:
                lost_ = true;
                return client("Transport", EPIPE, "release failed", Outcome::transport_lost);
            case bad_camera_count:
                // The backend's bounds check: the count is checked, nothing is indexed, the set is released.
                if (!counts_within_bounds(3, 2, 0, 0)) return client("None", 0, "", Outcome::invalid_metadata);
                break;
            case bad_plane_count:
                if (!counts_within_bounds(2, 5, 0, 0)) return client("None", 0, "", Outcome::invalid_metadata);
                break;
            default: break;
        }
        fill_frame_set(pass, meta);
        return success();
    }

    void disconnect() { calls_.push_back("disconnect"); }

    const StatusDiagnostics& last_status() const { return last_; }
    const std::vector<std::string>* call_log() const { return &calls_; }

private:
    Outcome success() {
        last_ = StatusDiagnostics();
        return Outcome::ok;
    }
    Outcome server(const char* result, Outcome outcome) {
        last_.status_error = "Server";
        last_.result = result;
        last_.system_error = 0;
        last_.sdk_message = std::string("fake: ") + result;
        return outcome;
    }
    Outcome client(const char* error, int system_error, const char* message, Outcome outcome) {
        last_.status_error = error;
        last_.result = "Result_Success";
        last_.system_error = system_error;
        last_.sdk_message = message;
        return outcome;
    }

    void fill_frame_set(unsigned pass, FrameSetMeta& meta) {
        meta = FrameSetMeta();
        if (scenario_ == sequence_gap && pass == 3) sequence_ += 2;  // two frame sets missed
        meta.sequence = sequence_++;
        meta.camera_count = 2;
        meta.pose_valid = true;
        meta.front_from_head_valid = true;
        meta.front_from_avcam0_valid = true;
        meta.velocity_valid = pass % 2 == 1;
        const bool ubwc = scenario_ == ok_ubwc_nv12 || (scenario_ == format_changes && pass >= 3);
        const bool p010 = scenario_ == ok_p010;
        const int64_t now = monotonic_ns();
        for (uint32_t camera = 0; camera < 2; ++camera) {
            CameraMeta& frame = meta.cameras[camera];
            frame.frame_id = static_cast<uint64_t>(pass) * 10u + camera;
            frame.exposure_timestamp_ns = now - 3000000ll;
            frame.timestamp_seconds = static_cast<double>(frame.exposure_timestamp_ns) / 1e9 + 0.001;
            frame.width = 2464;
            frame.height = 2464;
            frame.drm_format = p010 ? "0x30313050" : "0x3231564e";  // P010 / NV12 FourCC
            frame.pixel_format = p010 ? (ubwc ? "p010_ubwc" : "p010") : (ubwc ? "nv12_ubwc" : "nv12");
            frame.drm_modifier = ubwc ? "0x0500000000000001" : "0x0000000000000000";
            frame.plane_count = 2;
            const uint32_t bytes_per_sample = p010 ? 2 : 1;
            frame.planes[0].offset = 0;
            frame.planes[0].stride = 2464 * bytes_per_sample;
            frame.planes[0].size = 2464 * 2464 * bytes_per_sample;
            frame.planes[1].offset = frame.planes[0].size;
            frame.planes[1].stride = 2464 * bytes_per_sample;
            frame.planes[1].size = frame.planes[0].size / 2;
            frame.texture_bounds_valid = true;
            frame.intrinsics_valid = true;
            frame.camera_model = "kb4";
            frame.kb4_valid = true;
            frame.first_camera_from_camera_valid = true;
            frame.aux_json_size = 128;
            frame.transfer = "srgb";
            frame.primaries = "bt709";
            frame.ycbcr_encoding = "bt709";
        }
    }

    Scenario scenario_;
    unsigned acquires_;
    unsigned control_reads_;
    uint64_t sequence_;
    bool lost_;
    HelloFacts hello_;
    StatusDiagnostics last_;
    std::vector<std::string> calls_;
};

} // namespace

std::unique_ptr<CameraBackend> make_camera_backend(const std::string& fake_scenario) {
    const Scenario scenario = parse_scenario(fake_scenario);
    if (scenario == unknown_scenario) return std::unique_ptr<CameraBackend>();
    return std::unique_ptr<CameraBackend>(new FakeBackend(scenario));
}

} // namespace sightcircuit_camera
