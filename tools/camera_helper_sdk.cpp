// Camera helper, SDK backend: the one SightCircuit translation unit that
// includes the Arcturus Vision Camera Preview SDK headers. It is linked only
// by scripts/build_camera_helper.sh when the operator names a local copy of
// the Client Library; CI syntax-checks it against the vendored headers and
// never links it. The SDK's headers and agreement are in
// third_party/arcturus-camera-sdk/ (THIRD_PARTY.md).
//
// Containment, enforced by tests/test_camera_helper.py on this source:
//   - metadata only: the frame set's borrowed buffer descriptor is never read
//     (no cameraFd call), nothing is mapped, copied or written;
//   - no camera control is ever set (no setControls, no ControlsRequest);
//   - the reader is enabled with recording false, so exclusive camera control
//     is never requested and the red LED is never touched;
//   - at most one frame set is outstanding, released before the next acquire;
//   - the client never retries or reconnects here: a failure is reported.
#include "camera_helper.hpp"
#include "AV1CameraClient.h"
#include <cstring>

namespace sightcircuit_camera {
// The mirrored vocabulary in camera_helper.hpp must equal the vendored header.
static_assert(kMaxCameras == av1_ipc::kMaxCameras, "camera count mirror");
static_assert(kMaxPlanes == av1_ipc::kMaxPlanes, "plane count mirror");
template <class Mirror, class Sdk> constexpr bool same(Mirror mirror, Sdk sdk) noexcept {
    return static_cast<std::uint32_t>(mirror) == static_cast<std::uint32_t>(sdk);
}
static_assert(same(kResultSuccess, av1_ipc::Result_Success) && same(kResultUnavailable, av1_ipc::Result_Unavailable) &&
              same(kResultInvalidArgument, av1_ipc::Result_InvalidArgument) &&
              same(kResultClientProtocolTooNew, av1_ipc::Result_ClientProtocolTooNew) &&
              same(kResultNotReady, av1_ipc::Result_NotReady) && same(kResultInternalError, av1_ipc::Result_InternalError) &&
              same(kResultTimeout, av1_ipc::Result_Timeout) && same(kResultResourceDenied, av1_ipc::Result_ResourceDenied) &&
              same(kResultClientAlreadyConnected, av1_ipc::Result_ClientAlreadyConnected) &&
              same(kResultPermissionDenied, av1_ipc::Result_PermissionDenied) &&
              same(kResultClientProtocolTooOld, av1_ipc::Result_ClientProtocolTooOld), "Result mirror");
static_assert(same(kPixelUnknown, av1_ipc::PixelFormat_Unknown) && same(kPixelNv12, av1_ipc::PixelFormat_NV12) &&
              same(kPixelNv12Ubwc, av1_ipc::PixelFormat_NV12_UBWC) && same(kPixelP010, av1_ipc::PixelFormat_P010) &&
              same(kPixelP010Ubwc, av1_ipc::PixelFormat_P010_UBWC), "PixelFormat mirror");
using SdkError = AV1CameraClient::Status::Error;
static_assert(same(CallError::None, SdkError::None) && same(CallError::Server, SdkError::Server) &&
              same(CallError::NotConnected, SdkError::NotConnected) &&
              same(CallError::InvalidArgument, SdkError::InvalidArgument) && same(CallError::Transport, SdkError::Transport) &&
              same(CallError::Timeout, SdkError::Timeout) && same(CallError::Protocol, SdkError::Protocol) &&
              same(CallError::OutOfMemory, SdkError::OutOfMemory), "Status::Error mirror");
static_assert(av1_ipc::kProtocolVersion == 1, "the helper was written against protocol version 1");

namespace {
CallStatus translate(const AV1CameraClient::Status& status) noexcept {
    CallStatus out;
    out.error = static_cast<CallError>(static_cast<std::uint32_t>(status.error));
    out.result = static_cast<std::uint32_t>(status.result);
    out.system_error = status.systemError;
    return out;
}
void copy_controls(const av1_ipc::ControlState& in, ControlState& out) noexcept {
    out.supported_controls = in.supportedControls; out.current_controls = in.currentControls;
    out.supported_sensor_bit_depths = in.supportedSensorBitDepths;
    out.supported_auto_white_balance_modes = in.supportedAutoWhiteBalanceModes;
    out.supported_transfer_functions = in.supportedTransferFunctions;
    out.supported_color_primaries = in.supportedColorPrimaries;
    out.supported_ycbcr_encodings = in.supportedYCbCrEncodings;
    out.supported_resolution_modes = in.supportedResolutionModes;
    out.red_recording_led_enabled = in.redRecordingLedEnabled; out.sensor_bit_depth = in.sensorBitDepth;
    out.auto_white_balance_mode = in.autoWhiteBalanceMode; out.hdr_enabled = in.hdrEnabled;
    out.tone_mapping_enabled = in.toneMappingEnabled; out.transfer_function = in.transferFunction;
    out.color_primaries = in.colorPrimaries; out.ycbcr_encoding = in.yCbCrEncoding;
    out.resolution_mode = in.resolutionMode; out.current_fps = in.currentFPS; out.brightness_ev = in.brightnessEv;
    out.auto_exposure_enabled = in.autoExposureEnabled; out.exposure_ms = in.exposureMs; out.gain = in.gain;
    out.min_exposure_ms = in.minExposureMs; out.max_exposure_ms = in.maxExposureMs;
    out.min_gain = in.minGain; out.max_gain = in.maxGain;
}
// Numbers and flags only. The descriptor, intrinsics coefficients, transforms
// and auxiliary JSON stay in the reply, which is released with the frame set.
void copy_camera(const av1_ipc::CameraFrame& in, CameraMetadata& out) noexcept {
    out.frame_id = in.frameId; out.exposure_timestamp_ns = in.exposureTimestampNs;
    out.timestamp_seconds = in.timestampSeconds;
    out.width = in.dmabuf.width; out.height = in.dmabuf.height; out.drm_format = in.dmabuf.drmFormat;
    out.pixel_format = in.dmabuf.pixelFormat; out.drm_modifier = in.dmabuf.drmModifier;
    out.plane_count = in.dmabuf.planeCount < av1_ipc::kMaxPlanes ? in.dmabuf.planeCount : av1_ipc::kMaxPlanes;
    for (std::uint32_t plane = 0; plane < out.plane_count; ++plane) {
        out.planes[plane].offset = in.dmabuf.planes[plane].offset;
        out.planes[plane].stride = in.dmabuf.planes[plane].stride;
        out.planes[plane].size = in.dmabuf.planes[plane].size;
    }
    out.transfer_function = in.transferFunction; out.color_primaries = in.colorPrimaries;
    out.ycbcr_encoding = in.yCbCrEncoding; out.sensor_temperature_celsius = in.sensorTemperatureCelsius;
    out.exposure_seconds = in.exposureSeconds; out.gain = in.gain;
    out.intrinsics_valid = in.intrinsics.valid; out.intrinsics_model = in.intrinsics.cameraModel;
    out.kb4_intrinsics_valid = in.kb4Intrinsics.valid;
    out.first_camera_from_camera_valid = in.firstCameraFromCameraValid;
    out.aux_json_size = in.auxJsonSize;
}

class SdkBackend final : public Backend {
public:
    const char* name() const noexcept override { return "sdk"; }
    CallStatus connect(std::uint32_t timeout_ms) noexcept override {
        if (timeout_ms == 0 || client_.isConnected()) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        const CallStatus status = translate(client_.connect(timeout_ms));
        if (!status.ok()) return status;
        const av1_ipc::HelloReply& reply = client_.hello();
        hello_ = Hello{};
        hello_.protocol_version = reply.protocolVersion; hello_.camera_count = reply.cameraCount;
        hello_.offline_cal_source = reply.offlineCalSource; hello_.offline_cal_size = reply.offlineCalSize;
        copy_controls(reply.controls, hello_.controls);
        return status;
    }
    void disconnect() noexcept override {
        // Outstanding frames keep their session; release first so the API is free for another client at once.
        if (frame_set_) frame_set_.release();
        client_.disconnect();
    }
    bool connected() const noexcept override { return client_.isConnected(); }
    const Hello& hello() const noexcept override { return hello_; }
    CallStatus control_state(ControlState& out) noexcept override {
        av1_ipc::ControlState controls{};
        const CallStatus status = translate(client_.getControlState(controls));
        if (status.ok()) copy_controls(controls, out);
        return status;
    }
    CallStatus set_reader_active(bool active) noexcept override {
        // recording stays false: no exclusive camera control is ever requested.
        return translate(client_.setReaderState(active, false));
    }
    CallStatus acquire_frame_set(FrameSetMetadata& out, std::uint32_t frame_timeout_ms) noexcept override {
        if (frame_set_) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        const CallStatus status = translate(client_.acquireFrameSet(frame_set_, frame_timeout_ms));
        if (!status.ok()) return status;
        const av1_ipc::AcquireFrameSetReply& reply = frame_set_.metadata();
        out = FrameSetMetadata{};
        out.sequence = reply.sequence;
        out.camera_count = reply.cameraCount < av1_ipc::kMaxCameras ? reply.cameraCount : av1_ipc::kMaxCameras;
        out.pose_valid = reply.pose.poseValid; out.velocity_valid = reply.pose.velocityValid;
        for (std::uint32_t camera = 0; camera < out.camera_count; ++camera) copy_camera(reply.cameras[camera], out.cameras[camera]);
        return status;
    }
    CallStatus release_frame_set() noexcept override {
        if (!frame_set_) return CallStatus{CallError::InvalidArgument, kResultSuccess, 0};
        return translate(frame_set_.release());
    }
    bool frame_set_held() const noexcept override { return static_cast<bool>(frame_set_); }

private:
    AV1CameraClient client_;
    AV1CameraClient::FrameSet frame_set_;
    Hello hello_;
};
} // namespace

const char* backend_name() noexcept { return "sdk"; }
bool validate_backend_options(const BackendOptions& options, Json& report, std::string& error) {
    if (!options.fake_script.empty()) { error = "fake_script_requires_the_fake_backend"; return false; }
    report.set("sdk_protocol_version", static_cast<std::uint64_t>(av1_ipc::kProtocolVersion));
    report.set("client_constructed", false);
    return true;
}
std::unique_ptr<Backend> make_backend(const BackendOptions& options, std::string& error) {
    if (!options.fake_script.empty()) { error = "fake_script_requires_the_fake_backend"; return nullptr; }
    return std::unique_ptr<Backend>(new SdkBackend());
}
} // namespace sightcircuit_camera
