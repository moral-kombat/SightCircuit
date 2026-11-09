// The fake camera backend, built only with -DSIGHTCIRCUIT_CAMERA_BACKEND_FAKE=1.
// It drives the helper through every outcome on any architecture, without the
// SDK headers or the Client Library. Every record it produces says backend
// "fake", so fake output can never pass for device output. It records each
// backend call in order; the helper lists them as fake_calls.
// Exposure timestamps are reported on CLOCK_MONOTONIC, so the helper's
// clock-offset schema is exercised against a known clock.
//
// The export_* scenarios serve frame sets whose buffers are memfd descriptors
// holding linear NV12 or P010 planes with synthetic content (the generator
// below, mirrored by tests/test_camera_export.py): gradients, plus a white and
// a black block at exact positions. They let the export mode's pixel paths,
// pacing, bounds and ends be proved without a camera.
#include <cerrno>
#include <cstring>
#include <ctime>
#include <limits>
#include <string>
#include <vector>

#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "camera_backend.hpp"
#include "camera_sha256.hpp"
#include "camera_ycbcr.hpp"

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
    permission_denied_at_connect, out_of_memory_at_connect, invalid_metadata_at_connect, calibration_oversized,
    internal_error_at_controls, server_timeout_at_reader, invalid_argument_at_acquire, bad_timestamps,
    export_nv12, export_nv12_tiny, export_nv12_bt601, export_p010,
    export_removal, export_standby, export_transport_lost, export_ubwc, export_bad_fd,
    export_hang_in_acquire, export_one_camera, export_bad_plane_size, export_never_ready,
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
    {"permission_denied_at_connect", permission_denied_at_connect}, {"out_of_memory_at_connect", out_of_memory_at_connect},
    {"invalid_metadata_at_connect", invalid_metadata_at_connect}, {"calibration_oversized", calibration_oversized},
    {"internal_error_at_controls", internal_error_at_controls}, {"server_timeout_at_reader", server_timeout_at_reader},
    {"invalid_argument_at_acquire", invalid_argument_at_acquire}, {"bad_timestamps", bad_timestamps},
    {"export_nv12", export_nv12}, {"export_nv12_tiny", export_nv12_tiny}, {"export_nv12_bt601", export_nv12_bt601},
    {"export_p010", export_p010}, {"export_removal", export_removal}, {"export_standby", export_standby},
    {"export_transport_lost", export_transport_lost}, {"export_ubwc", export_ubwc}, {"export_bad_fd", export_bad_fd},
    {"export_hang_in_acquire", export_hang_in_acquire}, {"export_one_camera", export_one_camera},
    {"export_bad_plane_size", export_bad_plane_size}, {"export_never_ready", export_never_ready},
};

bool is_export(Scenario scenario) { return scenario >= export_nv12 && scenario <= export_never_ready; }

// The call log keeps its first and last entries and counts the rest, so the
// final record stays within its line bound on a long run at camera rate.
const std::size_t kCallLogHead = 24;
const std::size_t kCallLogTail = 24;

// The export scenarios' frame geometry: one buffer per camera, two planes.
struct ExportShape {
    uint32_t width, height, stride;  // the stride of both planes, in bytes
    bool p010;
    bool ubwc;
    uint32_t cameras;
    const char* encoding;
};
ExportShape export_shape(Scenario scenario) {
    ExportShape shape = {62, 32, 64, false, false, 2, "bt709"};
    switch (scenario) {
        case export_nv12_tiny: shape.width = 8; shape.height = 4; shape.stride = 8; break;
        case export_nv12_bt601: shape.encoding = "bt601"; break;
        case export_p010: shape.p010 = true; shape.stride = 128; shape.encoding = "bt2020"; break;
        case export_ubwc: shape.ubwc = true; break;
        case export_one_camera: shape.cameras = 1; break;
        default: break;
    }
    return shape;
}

// The synthetic content, by pixel: frame is the ordinal of the delivered frame
// set (from 1), camera the index. 8-bit values; P010 widens them below.
uint32_t synthetic_y8(uint32_t x, uint32_t y, unsigned frame, uint32_t camera) {
    if (y < 2 && x < 2) return 255;  // the white block
    if (y < 2 && x < 4) return 0;    // the black block
    return (x * 4 + y * 2 + frame * 7 + camera * 64) & 0xffu;
}
uint32_t synthetic_cb8(uint32_t cx, uint32_t cy, unsigned frame, uint32_t camera) {
    (void)camera;
    if (cy == 0 && cx < 2) return 128;
    return (cx * 8 + frame * 3 + 96) & 0xffu;
}
uint32_t synthetic_cr8(uint32_t cx, uint32_t cy, unsigned frame, uint32_t camera) {
    if (cy == 0 && cx < 2) return 128;
    return (cy * 8 + frame * 5 + camera * 32 + 160) & 0xffu;
}
// P010 samples sit in the high ten bits of each little-endian 16-bit word.
void put_p010(uint8_t* at, uint32_t value10) {
    const uint32_t word = value10 << 6;
    at[0] = static_cast<uint8_t>(word & 0xffu);
    at[1] = static_cast<uint8_t>(word >> 8);
}
void write_synthetic_planes(uint8_t* buffer, const ExportShape& shape, const PlaneMeta planes[2], unsigned frame,
                            uint32_t camera) {
    for (uint32_t y = 0; y < shape.height; ++y) {
        uint8_t* row = buffer + planes[0].offset + static_cast<std::size_t>(planes[0].stride) * y;
        for (uint32_t x = 0; x < shape.width; ++x) {
            const uint32_t y8 = synthetic_y8(x, y, frame, camera);
            if (shape.p010) {
                const bool exact = y < 2 && x < 4;
                put_p010(row + x * 2, exact ? (y8 ? 1023u : 0u) : y8 * 4 + ((x + y) & 3u));
            } else {
                row[x] = static_cast<uint8_t>(y8);
            }
        }
    }
    for (uint32_t cy = 0; cy < shape.height / 2; ++cy) {
        uint8_t* row = buffer + planes[1].offset + static_cast<std::size_t>(planes[1].stride) * cy;
        for (uint32_t cx = 0; cx < shape.width / 2; ++cx) {
            const uint32_t cb8 = synthetic_cb8(cx, cy, frame, camera);
            const uint32_t cr8 = synthetic_cr8(cx, cy, frame, camera);
            if (shape.p010) {
                const bool exact = cy == 0 && cx < 2;
                put_p010(row + cx * 4, exact ? 512u : cb8 * 4 + 1);
                put_p010(row + cx * 4 + 2, exact ? 512u : cr8 * 4 + 2);
            } else {
                row[cx * 2] = static_cast<uint8_t>(cb8);
                row[cx * 2 + 1] = static_cast<uint8_t>(cr8);
            }
        }
    }
}

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
    explicit FakeBackend(Scenario scenario)
        : scenario_(scenario), acquires_(0), delivered_(0), control_reads_(0), sequence_(100), lost_(false), held_(false) {
        for (uint32_t camera = 0; camera < kMaxCameras; ++camera) memfd_[camera] = -1;
    }
    ~FakeBackend() {
        for (uint32_t camera = 0; camera < kMaxCameras; ++camera) {
            if (memfd_[camera] >= 0) ::close(memfd_[camera]);
        }
    }

    const char* backend_name() const { return "fake"; }

    Outcome connect(uint32_t timeout_ms) {
        (void)timeout_ms;
        record_call("connect");
        switch (scenario_) {
            case protocol_too_new: return server("Result_ClientProtocolTooNew", Outcome::protocol_mismatch);
            case protocol_too_old: return server("Result_ClientProtocolTooOld", Outcome::protocol_mismatch);
            case already_connected: return server("Result_ClientAlreadyConnected", Outcome::camera_busy);
            case provider_unreachable:
                return client("Transport", ECONNREFUSED, "connection refused", Outcome::provider_unreachable);
            case permission_denied_at_connect: return server("Result_PermissionDenied", Outcome::permission_denied);
            case out_of_memory_at_connect: return client("OutOfMemory", ENOMEM, "out of memory", Outcome::helper_error);
            default: break;
        }
        hello_ = HelloFacts();
        hello_.protocol_version = 1;
        hello_.camera_count = 2;
        hello_.offline_cal_source = "eeprom";
        hello_.offline_cal_size = kFakeOfflineCalSize;
        hello_.offline_cal_size_within_bound = true;
        hello_.offline_cal_sha256 = sha256_hex(kFakeOfflineCal, kFakeOfflineCalSize);
        // The handshake succeeded but its facts fail the bounds check, as the SDK backend reports them:
        // the hello record carries what was read, the outcome is invalid_metadata, the session is closed.
        if (scenario_ == invalid_metadata_at_connect) {
            hello_.camera_count = 3;
            return client("None", 0, "", Outcome::invalid_metadata);
        }
        if (scenario_ == calibration_oversized) {
            hello_.offline_cal_size = kMaxOfflineCalBytes + 1;
            hello_.offline_cal_size_within_bound = false;
            hello_.offline_cal_sha256.clear();
            return client("None", 0, "", Outcome::invalid_metadata);
        }
        return success();
    }

    const HelloFacts& hello() const { return hello_; }

    Outcome control_state(ControlSummary& summary) {
        record_call("control_state");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::transport_lost);
        ++control_reads_;
        if (scenario_ == permission_denied) return server("Result_PermissionDenied", Outcome::permission_denied);
        if (scenario_ == internal_error_at_controls) return server("Result_InternalError", Outcome::helper_error);
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
        record_call(active ? "set_reader:true" : "set_reader:false");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::transport_lost);
        if (active && scenario_ == permission_denied_at_reader) {
            return server("Result_PermissionDenied", Outcome::permission_denied);
        }
        if (active && scenario_ == resource_denied) return server("Result_ResourceDenied", Outcome::camera_busy);
        // A server timeout on a command other than acquire is not "no frame": it is a helper error.
        if (active && scenario_ == server_timeout_at_reader) return server("Result_Timeout", Outcome::helper_error);
        return success();
    }

    Outcome acquire(uint32_t timeout_ms, FrameSetMeta& meta) {
        record_call("acquire");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::transport_lost);
        const unsigned pass = ++acquires_;
        switch (scenario_) {
            case invalid_argument_at_acquire: return client("InvalidArgument", 0, "invalid argument", Outcome::helper_error);
            case hang_in_acquire: hang_forever(); break;
            case hang_in_release: record_call("release"); hang_forever(); break;
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
        if (scenario_ == bad_timestamps) {
            // Timestamps the server could send and the helper must not compute with: a NaN seconds
            // value and an exposure timestamp whose difference from any host clock overflows.
            for (uint32_t camera = 0; camera < 2; ++camera) {
                meta.cameras[camera].timestamp_seconds = std::numeric_limits<double>::quiet_NaN();
                meta.cameras[camera].exposure_timestamp_ns = std::numeric_limits<int64_t>::min();
            }
        }
        return success();
    }

    Outcome acquire_lease(uint32_t timeout_ms, FrameSetLease& lease) {
        if (held_) return client("None", 0, "lease already held", Outcome::helper_error);
        if (!is_export(scenario_)) {
            // The metadata scenarios serve no buffers: the lease carries descriptors of -1.
            lease = FrameSetLease();
            const Outcome outcome = acquire(timeout_ms, lease.meta);
            if (outcome == Outcome::ok) held_ = true;
            return outcome;
        }
        record_call("acquire");
        if (lost_) return client("NotConnected", 0, "not connected", Outcome::helper_error);
        const unsigned pass = ++acquires_;
        switch (scenario_) {
            case export_hang_in_acquire: hang_forever(); break;
            case export_removal:
                if (delivered_ >= 6) {  // frames stop without a reported reason
                    sleep_ms(timeout_ms > 100 ? 100 : timeout_ms);
                    return server("Result_Timeout", Outcome::no_frame);
                }
                break;
            case export_standby:
                if (delivered_ >= 6) {
                    sleep_ms(16);
                    return server("Result_Unavailable", Outcome::unavailable);
                }
                break;
            case export_never_ready:  // the service never serves a frame set
                sleep_ms(16);
                return server("Result_NotReady", Outcome::not_ready);
            case export_transport_lost:
                if (delivered_ >= 4) {
                    lost_ = true;
                    return client("Transport", EPIPE, "broken pipe", Outcome::transport_lost);
                }
                break;
            default: break;
        }
        sleep_ms(16);  // about a 60 Hz camera
        ++delivered_;
        lease = FrameSetLease();
        const ExportShape shape = export_shape(scenario_);
        fill_export_frame_set(pass, shape, lease.meta);
        for (uint32_t camera = 0; camera < shape.cameras; ++camera) {
            if (scenario_ == export_bad_fd) { lease.camera_fd[camera] = -1; continue; }
            if (!ensure_memfd(camera, shape, lease.meta.cameras[camera].planes)) {
                return client("None", errno, "memfd unavailable", Outcome::helper_error);
            }
            lease.camera_fd[camera] = memfd_[camera];
        }
        held_ = true;
        return success();
    }

    Outcome release_lease() {
        if (!held_) return success();
        held_ = false;
        record_call("release");
        return success();
    }

    void disconnect() { record_call("disconnect"); }

    const StatusDiagnostics& last_status() const { return last_; }
    const std::vector<std::string>* call_log() const {
        log_view_ = head_calls_;
        if (omitted_calls_) log_view_.push_back("..." + std::to_string(omitted_calls_) + " calls omitted...");
        log_view_.insert(log_view_.end(), tail_calls_.begin(), tail_calls_.end());
        return &log_view_;
    }

private:
    void record_call(const std::string& name) {
        if (head_calls_.size() < kCallLogHead) { head_calls_.push_back(name); return; }
        if (tail_calls_.size() == kCallLogTail) {
            tail_calls_.erase(tail_calls_.begin());
            ++omitted_calls_;
        }
        tail_calls_.push_back(name);
    }

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
            frame.drm_format_value = p010 ? kDrmFourccP010 : kDrmFourccNv12;
            frame.pixel_format = p010 ? (ubwc ? "p010_ubwc" : "p010") : (ubwc ? "nv12_ubwc" : "nv12");
            frame.drm_modifier = ubwc ? "0x0500000000000001" : "0x0000000000000000";
            frame.drm_modifier_value = ubwc ? 0x0500000000000001ull : kDrmModifierLinear;
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

    // The export scenarios' frame set: the same validity flags as the probe
    // scenarios, the scenario's geometry, and planes laid out tightly after each
    // other at the scenario's stride.
    void fill_export_frame_set(unsigned pass, const ExportShape& shape, FrameSetMeta& meta) {
        meta = FrameSetMeta();
        meta.sequence = sequence_++;
        meta.camera_count = shape.cameras;
        meta.pose_valid = true;
        meta.front_from_head_valid = true;
        meta.front_from_avcam0_valid = true;
        meta.velocity_valid = pass % 2 == 1;
        const int64_t now = monotonic_ns();
        for (uint32_t camera = 0; camera < shape.cameras; ++camera) {
            CameraMeta& frame = meta.cameras[camera];
            frame.frame_id = static_cast<uint64_t>(pass) * 10u + camera;
            frame.exposure_timestamp_ns = now - 3000000ll;
            frame.timestamp_seconds = static_cast<double>(frame.exposure_timestamp_ns) / 1e9 + 0.001;
            frame.width = shape.width;
            frame.height = shape.height;
            frame.drm_format = shape.p010 ? "0x30313050" : "0x3231564e";
            frame.drm_format_value = shape.p010 ? kDrmFourccP010 : kDrmFourccNv12;
            frame.pixel_format = shape.p010 ? (shape.ubwc ? "p010_ubwc" : "p010") : (shape.ubwc ? "nv12_ubwc" : "nv12");
            frame.drm_modifier = shape.ubwc ? "0x0500000000000001" : "0x0000000000000000";
            frame.drm_modifier_value = shape.ubwc ? 0x0500000000000001ull : kDrmModifierLinear;
            frame.plane_count = 2;
            frame.planes[0].offset = 0;
            frame.planes[0].stride = shape.stride;
            frame.planes[0].size = shape.stride * shape.height;
            frame.planes[1].offset = frame.planes[0].size;
            frame.planes[1].stride = shape.stride;
            frame.planes[1].size = shape.stride * (shape.height / 2);
            if (scenario_ == export_bad_plane_size) frame.planes[1].size = shape.stride * (shape.height / 2) - shape.stride;
            frame.texture_bounds_valid = true;
            frame.intrinsics_valid = true;
            frame.camera_model = "kb4";
            frame.kb4_valid = true;
            frame.first_camera_from_camera_valid = true;
            frame.aux_json_size = 128;
            frame.transfer = shape.p010 ? "hlg" : "srgb";
            frame.primaries = shape.p010 ? "bt2020" : "bt709";
            frame.ycbcr_encoding = shape.encoding;
        }
    }

    // One memfd per camera, sized once and rewritten with the current frame's content.
    bool ensure_memfd(uint32_t camera, const ExportShape& shape, const PlaneMeta planes[kMaxPlanes]) {
        const std::size_t bytes = static_cast<std::size_t>(planes[1].offset) + planes[1].size;
        if (memfd_[camera] < 0) {
            const int fd = ::memfd_create(camera ? "sightcircuit-fake-camera-1" : "sightcircuit-fake-camera-0", MFD_CLOEXEC);
            if (fd < 0 || ::ftruncate(fd, static_cast<off_t>(bytes)) != 0) {
                if (fd >= 0) ::close(fd);
                return false;
            }
            memfd_[camera] = fd;
        }
        void* mapped = ::mmap(0, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, memfd_[camera], 0);
        if (mapped == MAP_FAILED) return false;
        std::memset(mapped, 0, bytes);
        write_synthetic_planes(static_cast<uint8_t*>(mapped), shape, planes, delivered_, camera);
        ::munmap(mapped, bytes);
        return true;
    }

    Scenario scenario_;
    unsigned acquires_;
    unsigned delivered_;
    unsigned control_reads_;
    uint64_t sequence_;
    bool lost_;
    bool held_;
    int memfd_[kMaxCameras];
    HelloFacts hello_;
    StatusDiagnostics last_;
    std::vector<std::string> head_calls_, tail_calls_;
    std::size_t omitted_calls_ = 0;
    mutable std::vector<std::string> log_view_;
};

} // namespace

std::unique_ptr<CameraBackend> make_camera_backend(const std::string& fake_scenario) {
    const Scenario scenario = parse_scenario(fake_scenario);
    if (scenario == unknown_scenario) return std::unique_ptr<CameraBackend>();
    return std::unique_ptr<CameraBackend>(new FakeBackend(scenario));
}

} // namespace sightcircuit_camera
