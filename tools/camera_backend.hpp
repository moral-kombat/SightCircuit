// SightCircuit's own view of the Arcturus camera backend: the vocabulary the
// camera helper reports and the interface both backends implement. It contains
// no SDK types; camera_backend_sdk.cpp translates the SDK's structures into
// these records, and camera_backend_fake.cpp fills them from a scenario table.
// C++11, so the SDK translation unit can be compiled as the SDK's own dialect.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "camera_sha256.hpp"

namespace sightcircuit_camera {

// Bounds every server-supplied count is checked against before it is used as an
// index or a length. camera_backend_sdk.cpp asserts they equal the protocol
// header's constants, so the helper and the fake share one rule.
const uint32_t kMaxCameras = 2;
const uint32_t kMaxPlanes = 4;
const uint32_t kMaxAuxJsonBytes = 8192;
const uint32_t kMaxOfflineCalBytes = 8192;
const uint32_t kMaxDistortionParams = 128;

inline bool counts_within_bounds(uint32_t camera_count, uint32_t plane_count, uint32_t aux_json_size,
                                 uint32_t distortion_param_count) {
    return camera_count <= kMaxCameras && plane_count <= kMaxPlanes && aux_json_size <= kMaxAuxJsonBytes &&
           distortion_param_count <= kMaxDistortionParams;
}

// The closed outcome vocabulary. camera_status_map.hpp is the only place an SDK
// status becomes one of these; cancelled is the helper's own.
enum class Outcome {
    ok,
    no_frame,
    not_ready,
    unavailable,
    permission_denied,
    protocol_mismatch,
    camera_busy,
    transport_lost,
    provider_unreachable,
    invalid_metadata,
    helper_error,
    cancelled
};
const int kOutcomeCount = 12;

inline const char* outcome_name(Outcome outcome) {
    switch (outcome) {
        case Outcome::ok: return "ok";
        case Outcome::no_frame: return "no_frame";
        case Outcome::not_ready: return "not_ready";
        case Outcome::unavailable: return "unavailable";
        case Outcome::permission_denied: return "permission_denied";
        case Outcome::protocol_mismatch: return "protocol_mismatch";
        case Outcome::camera_busy: return "camera_busy";
        case Outcome::transport_lost: return "transport_lost";
        case Outcome::provider_unreachable: return "provider_unreachable";
        case Outcome::invalid_metadata: return "invalid_metadata";
        case Outcome::helper_error: return "helper_error";
        case Outcome::cancelled: return "cancelled";
    }
    return "helper_error";
}

// Retryable outcomes leave the session usable; the probe loop counts them and goes on.
inline bool outcome_retryable(Outcome outcome) {
    return outcome == Outcome::no_frame || outcome == Outcome::not_ready || outcome == Outcome::unavailable;
}

inline std::string hex_u64(uint64_t value, int digits) {
    char text[32];
    std::snprintf(text, sizeof text, "0x%0*llx", digits, static_cast<unsigned long long>(value));
    return text;
}
inline std::string hex_u32(uint32_t value) { return hex_u64(value, 8); }

// A float's bit pattern with every NaN folded to the quiet NaN, for digests.
inline uint32_t float_bits_normalized(float value) {
    if (value != value) return 0x7fc00000u;
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof bits);
    return bits;
}
inline float round_to_tenth(float value) {
    if (value != value) return value;
    return static_cast<float>(std::round(static_cast<double>(value) * 10.0) / 10.0);
}

// The Hello handshake's facts. The offline calibration JSON itself is never
// reported: only its source, its size (checked against the bound) and the
// SHA-256 of exactly offline_cal_size bytes.
struct HelloFacts {
    uint32_t protocol_version = 0;
    uint32_t camera_count = 0;
    std::string offline_cal_source = "unknown";
    uint32_t offline_cal_size = 0;
    bool offline_cal_size_within_bound = false;
    std::string offline_cal_sha256;  // empty when the size exceeds the bound
};

// Every configured field of the SDK's ControlState. It excludes exposureMs and
// gain, which are latest sensor values rather than configuration.
struct ControlSummary {
    std::string supported_controls = "0x0000000000000000";
    std::string current_controls = "0x0000000000000000";
    std::string supported_sensor_bit_depths = "0x00000000";
    std::string supported_auto_white_balance_modes = "0x00000000";
    std::string supported_transfer_functions = "0x00000000";
    std::string supported_color_primaries = "0x00000000";
    std::string supported_ycbcr_encodings = "0x00000000";
    std::string supported_resolution_modes = "0x00000000";
    uint32_t red_recording_led_enabled = 0;
    uint32_t sensor_bit_depth = 0;
    std::string auto_white_balance_mode = "unknown";
    uint32_t hdr_enabled = 0;
    uint32_t tone_mapping_enabled = 0;
    std::string transfer_function = "unknown";
    std::string color_primaries = "unknown";
    std::string ycbcr_encoding = "unknown";
    std::string resolution_mode = "unknown";
    float brightness_ev = 0.0f;
    uint32_t auto_exposure_enabled = 0;
    float min_exposure_ms = 0.0f;
    float max_exposure_ms = 0.0f;
    float min_gain = 0.0f;
    float max_gain = 0.0f;
    float current_fps = 0.0f;

    float current_fps_rounded() const { return round_to_tenth(current_fps); }

    // Field names and canonical values in fixed order; floats by bit pattern.
    std::vector<std::pair<std::string, std::string> > entries() const {
        std::vector<std::pair<std::string, std::string> > out;
        out.push_back(std::make_pair("supported_controls", supported_controls));
        out.push_back(std::make_pair("current_controls", current_controls));
        out.push_back(std::make_pair("supported_sensor_bit_depths", supported_sensor_bit_depths));
        out.push_back(std::make_pair("supported_auto_white_balance_modes", supported_auto_white_balance_modes));
        out.push_back(std::make_pair("supported_transfer_functions", supported_transfer_functions));
        out.push_back(std::make_pair("supported_color_primaries", supported_color_primaries));
        out.push_back(std::make_pair("supported_ycbcr_encodings", supported_ycbcr_encodings));
        out.push_back(std::make_pair("supported_resolution_modes", supported_resolution_modes));
        out.push_back(std::make_pair("red_recording_led_enabled", decimal(red_recording_led_enabled)));
        out.push_back(std::make_pair("sensor_bit_depth", decimal(sensor_bit_depth)));
        out.push_back(std::make_pair("auto_white_balance_mode", auto_white_balance_mode));
        out.push_back(std::make_pair("hdr_enabled", decimal(hdr_enabled)));
        out.push_back(std::make_pair("tone_mapping_enabled", decimal(tone_mapping_enabled)));
        out.push_back(std::make_pair("transfer_function", transfer_function));
        out.push_back(std::make_pair("color_primaries", color_primaries));
        out.push_back(std::make_pair("ycbcr_encoding", ycbcr_encoding));
        out.push_back(std::make_pair("resolution_mode", resolution_mode));
        out.push_back(std::make_pair("brightness_ev", bits(brightness_ev)));
        out.push_back(std::make_pair("auto_exposure_enabled", decimal(auto_exposure_enabled)));
        out.push_back(std::make_pair("min_exposure_ms", bits(min_exposure_ms)));
        out.push_back(std::make_pair("max_exposure_ms", bits(max_exposure_ms)));
        out.push_back(std::make_pair("min_gain", bits(min_gain)));
        out.push_back(std::make_pair("max_gain", bits(max_gain)));
        out.push_back(std::make_pair("current_fps", bits(current_fps_rounded())));
        return out;
    }

    // "key=value;" for every entry, in order: the digest's input.
    std::string canonical() const {
        std::string text;
        const std::vector<std::pair<std::string, std::string> > fields = entries();
        for (std::size_t i = 0; i < fields.size(); ++i) {
            text += fields[i].first;
            text += '=';
            text += fields[i].second;
            text += ';';
        }
        return text;
    }

    std::string digest() const { return sha256_hex(canonical()); }

    std::vector<std::string> changed_fields(const ControlSummary& other) const {
        std::vector<std::string> names;
        const std::vector<std::pair<std::string, std::string> > mine = entries();
        const std::vector<std::pair<std::string, std::string> > theirs = other.entries();
        for (std::size_t i = 0; i < mine.size() && i < theirs.size(); ++i) {
            if (mine[i].second != theirs[i].second) names.push_back(mine[i].first);
        }
        return names;
    }

private:
    static std::string decimal(uint32_t value) {
        char text[16];
        std::snprintf(text, sizeof text, "%u", static_cast<unsigned>(value));
        return text;
    }
    static std::string bits(float value) { return "bits:" + hex_u32(float_bits_normalized(value)); }
};

struct PlaneMeta {
    uint32_t offset = 0;
    uint32_t stride = 0;
    uint32_t size = 0;
};

// Format metadata and validity flags for one camera. It holds no exposure
// seconds, gain, sensor temperature, auxiliary JSON (or its digest), intrinsics
// values, transforms or pose matrices: those are Camera Data this phase does
// not report.
struct CameraMeta {
    uint64_t frame_id = 0;
    int64_t exposure_timestamp_ns = 0;
    double timestamp_seconds = 0.0;
    uint32_t width = 0;
    uint32_t height = 0;
    std::string drm_format = "0x00000000";  // raw FourCC
    std::string pixel_format = "unknown";    // nv12, nv12_ubwc, p010, p010_ubwc or unknown
    std::string drm_modifier = "0x0000000000000000";
    uint32_t plane_count = 0;
    PlaneMeta planes[kMaxPlanes];
    bool texture_bounds_valid = false;
    bool intrinsics_valid = false;
    std::string camera_model = "unknown";
    bool kb4_valid = false;
    bool first_camera_from_camera_valid = false;
    uint32_t aux_json_size = 0;
    std::string transfer = "unknown";
    std::string primaries = "unknown";
    std::string ycbcr_encoding = "unknown";
};

struct FrameSetMeta {
    uint64_t sequence = 0;
    uint32_t camera_count = 0;
    bool pose_valid = false;
    bool front_from_head_valid = false;
    bool front_from_avcam0_valid = false;
    bool velocity_valid = false;
    CameraMeta cameras[kMaxCameras];
};

// The last SDK status, as names: no SDK types cross this interface.
struct StatusDiagnostics {
    std::string status_error = "None";
    std::string result = "Result_Success";
    int system_error = 0;
    std::string sdk_message;  // null-safe, at most 256 bytes
};

class CameraBackend {
public:
    virtual ~CameraBackend() {}
    virtual const char* backend_name() const = 0;
    virtual Outcome connect(uint32_t timeout_ms) = 0;
    virtual const HelloFacts& hello() const = 0;
    virtual Outcome control_state(ControlSummary& summary) = 0;
    // Always sends the recording flag as false: exclusive control is never requested.
    virtual Outcome set_reader(bool active) = 0;
    // Acquires into a frame set local to the call, validates the counts, copies
    // the metadata and releases before returning. A failed release is
    // transport_lost. The backend never holds a lease after returning.
    virtual Outcome acquire(uint32_t timeout_ms, FrameSetMeta& meta) = 0;
    virtual void disconnect() = 0;
    virtual const StatusDiagnostics& last_status() const = 0;
    // The fake's ordered record of calls; null for the SDK backend.
    virtual const std::vector<std::string>* call_log() const { return 0; }
};

// Defined by exactly one of camera_backend_sdk.cpp and camera_backend_fake.cpp.
// The SDK backend ignores the scenario.
std::unique_ptr<CameraBackend> make_camera_backend(const std::string& fake_scenario);

} // namespace sightcircuit_camera
