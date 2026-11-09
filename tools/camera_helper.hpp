// Camera helper: the interface its units share. The helper is the containment
// for the Arcturus Vision Camera Preview SDK (work-order lane G, step 1 of the
// migration plan in docs/history/2026-11-09-arcturus-sdk-input-review.md).
//
//   camera_helper.cpp            main(): argument parsing and the validate, run, report sequence
//   camera_helper_reporting.cpp  every protocol line: the usage, the watchdog message, the reports
//   camera_helper_fake.cpp       the fake backend, scripted from a file; every CI build links this unit
//   camera_helper_sdk.cpp        the SDK backend; the only SightCircuit source that includes the SDK
//                                headers, linked only by an operator who supplies the Client Library
//
// Everything a mode sees comes through Backend below: counts, enumerators and
// numbers. The interface has no accessor for a buffer descriptor, no camera
// control setter and no recording flag, so no unit built on it can map, read,
// copy or save a pixel, change a shared camera control or touch the red LED.
// The lifecycle helpers (stdout isolation, the SIGALRM watchdog) are the house
// pattern of the overlay bridge, included unchanged from overlay_lifecycle.hpp.
// No SDK header is included here; the SDK unit static_asserts the mirrored
// enumerator values below against the vendored protocol header.
#pragma once
#include "overlay_lifecycle.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace sightcircuit_camera {
namespace lifecycle = sightcircuit_lifecycle;

constexpr const char* kReportKind = "sightcircuit_camera_helper";
constexpr int kSchemaVersion = 1;
constexpr std::uint32_t kMaxCameras = 2;
constexpr std::uint32_t kMaxPlanes = 4;

// Mirror of av1_ipc::Result (AV1CameraIPCProtocol.h); the SDK unit pins each value.
enum Result : std::uint32_t {
    kResultSuccess = 0, kResultUnavailable = 1, kResultInvalidArgument = 2, kResultClientProtocolTooNew = 3,
    kResultNotReady = 4, kResultInternalError = 5, kResultTimeout = 6, kResultResourceDenied = 7,
    kResultClientAlreadyConnected = 8, kResultPermissionDenied = 9, kResultClientProtocolTooOld = 10,
};
// Mirror of AV1CameraClient::Status::Error (AV1CameraClient.h), same order.
enum class CallError : std::uint32_t { None = 0, Server, NotConnected, InvalidArgument, Transport, Timeout, Protocol, OutOfMemory };
// Mirror of av1_ipc::PixelFormat.
enum PixelFormat : std::uint32_t { kPixelUnknown = 0, kPixelNv12 = 1, kPixelNv12Ubwc = 2, kPixelP010 = 3, kPixelP010Ubwc = 4 };

struct CallStatus {
    CallError error = CallError::None;
    std::uint32_t result = kResultSuccess;  // a Result when error is Server, else kResultSuccess
    int system_error = 0;                   // errno when error is Transport
    bool ok() const noexcept { return error == CallError::None; }
    bool permission_denied() const noexcept { return error == CallError::Server && result == kResultPermissionDenied; }
    // The SDK's own rule: a transport, protocol, socket-timeout or not-connected failure closes the session.
    bool closes_session() const noexcept {
        return error == CallError::Transport || error == CallError::Protocol || error == CallError::Timeout ||
               error == CallError::NotConnected;
    }
};
const char* call_error_name(CallError error) noexcept;
const char* result_name(std::uint32_t result) noexcept;
const char* pixel_format_name(std::uint32_t format) noexcept;

// Per-frame metadata copied out of the SDK reply: numbers only. The buffer
// descriptor, intrinsics coefficients, transforms, pose and auxiliary JSON
// are not carried; only their validity flags and sizes are.
struct Plane { std::uint32_t offset = 0, stride = 0, size = 0; };
struct CameraMetadata {
    std::uint64_t frame_id = 0;
    std::int64_t exposure_timestamp_ns = 0;
    double timestamp_seconds = 0;
    std::uint32_t width = 0, height = 0, drm_format = 0, pixel_format = kPixelUnknown;
    std::uint64_t drm_modifier = 0;
    std::uint32_t plane_count = 0;
    Plane planes[kMaxPlanes];
    std::uint32_t transfer_function = 0, color_primaries = 0, ycbcr_encoding = 0;
    float sensor_temperature_celsius = 0, exposure_seconds = 0, gain = 0;
    std::uint32_t intrinsics_valid = 0, intrinsics_model = 0, kb4_intrinsics_valid = 0;
    std::uint32_t first_camera_from_camera_valid = 0, aux_json_size = 0;
};
struct FrameSetMetadata {
    std::uint64_t sequence = 0;
    std::uint32_t camera_count = 0;
    std::uint32_t pose_valid = 0, velocity_valid = 0;
    CameraMetadata cameras[kMaxCameras];
};
// av1_ipc::ControlState, field for field; read only, never written back.
struct ControlState {
    std::uint64_t supported_controls = 0, current_controls = 0;
    std::uint32_t supported_sensor_bit_depths = 0, supported_auto_white_balance_modes = 0;
    std::uint32_t supported_transfer_functions = 0, supported_color_primaries = 0;
    std::uint32_t supported_ycbcr_encodings = 0, supported_resolution_modes = 0;
    std::uint32_t red_recording_led_enabled = 0, sensor_bit_depth = 0, auto_white_balance_mode = 0;
    std::uint32_t hdr_enabled = 0, tone_mapping_enabled = 0, transfer_function = 0, color_primaries = 0;
    std::uint32_t ycbcr_encoding = 0, resolution_mode = 0;
    float current_fps = 0, brightness_ev = 0;
    std::uint32_t auto_exposure_enabled = 0;
    float exposure_ms = 0, gain = 0, min_exposure_ms = 0, max_exposure_ms = 0, min_gain = 0, max_gain = 0;
};
struct Hello {
    std::uint32_t protocol_version = 0, camera_count = 0, offline_cal_source = 0, offline_cal_size = 0;
    ControlState controls;
};

// What a mode may ask of a camera session. Deliberately absent: any buffer
// descriptor, setControls, and the reader's recording flag (always false).
class Backend {
public:
    virtual ~Backend() = default;
    virtual const char* name() const noexcept = 0;
    virtual CallStatus connect(std::uint32_t timeout_ms) noexcept = 0;  // connect and the mandatory handshake
    virtual void disconnect() noexcept = 0;
    virtual bool connected() const noexcept = 0;
    virtual const Hello& hello() const noexcept = 0;                    // valid after a successful connect
    virtual CallStatus control_state(ControlState& out) noexcept = 0;
    virtual CallStatus set_reader_active(bool active) noexcept = 0;     // recording is never requested
    // At most one frame set is held; it must be released before the next acquire.
    virtual CallStatus acquire_frame_set(FrameSetMetadata& out, std::uint32_t frame_timeout_ms) noexcept = 0;
    virtual CallStatus release_frame_set() noexcept = 0;
    virtual bool frame_set_held() const noexcept = 0;
};

struct BackendOptions {
    std::string fake_script;  // --fake-script FILE; only the fake unit accepts it
};

// A compact ordered JSON value for the protocol lines; the helper depends on
// no JSON library. Non-finite numbers are written as null.
class Json {
public:
    enum class Kind { Null, Boolean, Number, String, Object, Array };
    Json() = default;
    Json(bool value) : kind_(Kind::Boolean), text_(value ? "true" : "false") {}
    Json(const char* value) : kind_(Kind::String), text_(value ? value : "") {}
    Json(const std::string& value) : kind_(Kind::String), text_(value) {}
    Json(int value) : Json(static_cast<std::int64_t>(value)) {}
    Json(unsigned value) : Json(static_cast<std::uint64_t>(value)) {}
    Json(std::int64_t value) : kind_(Kind::Number), text_(std::to_string(value)) {}
    Json(std::uint64_t value) : kind_(Kind::Number), text_(std::to_string(value)) {}
    Json(double value);
    static Json object() { Json json; json.kind_ = Kind::Object; return json; }
    static Json array() { Json json; json.kind_ = Kind::Array; return json; }
    Kind kind() const noexcept { return kind_; }
    // Objects: set replaces an existing member in place or appends; get returns null for a missing member.
    Json& set(const std::string& key, Json value);
    const Json& get(const std::string& key) const noexcept;
    Json& push(Json value);
    const std::string& text() const noexcept { return text_; }
    std::string dump() const;  // one line, no trailing newline

private:
    Kind kind_ = Kind::Null;
    std::string text_;
    std::vector<std::pair<std::string, Json>> members_;
    std::vector<Json> items_;
};
std::string json_escape(const std::string& text);

// Backend unit (camera_helper_fake.cpp or camera_helper_sdk.cpp): exactly one is linked.
const char* backend_name() noexcept;
// Checks the options without connecting or constructing a client; records what it checked in `report`.
bool validate_backend_options(const BackendOptions& options, Json& report, std::string& error);
std::unique_ptr<Backend> make_backend(const BackendOptions& options, std::string& error);

// Reporting unit.
void emit(Json value);
std::string watchdog_line(const std::string& mode, const char* phase, unsigned seconds);
std::string usage_text();
Json base_report(const std::string& mode);
void record_claims(Json& report);
} // namespace sightcircuit_camera
