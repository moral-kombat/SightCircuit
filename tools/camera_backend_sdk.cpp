// The SDK backend: the only translation unit, with camera_status_map.hpp, that
// includes the Arcturus SDK headers, and the only SightCircuit code that links
// libAV1CameraClient.a. It reports metadata and never touches a pixel: no
// cameraFd, no mapping, no import. It never changes a control and never asks
// for exclusive access (setReaderState's recording flag is always false).
// C++11, as the SDK headers are; built with -I third_party/arcturus-camera-sdk.
#include <cstring>

#include "AV1CameraClient.h"
#include "camera_backend.hpp"
#include "camera_sha256.hpp"
#include "camera_status_map.hpp"

namespace sightcircuit_camera {
namespace {

static_assert(kMaxCameras == av1_ipc::kMaxCameras, "camera bound drifted from the protocol header");
static_assert(kMaxPlanes == av1_ipc::kMaxPlanes, "plane bound drifted from the protocol header");
static_assert(kMaxAuxJsonBytes == av1_ipc::kMaxAuxJsonBytes, "aux JSON bound drifted from the protocol header");
static_assert(kMaxOfflineCalBytes == av1_ipc::kMaxOfflineCalBytes, "calibration bound drifted from the protocol header");
static_assert(kMaxDistortionParams == av1_ipc::kMaxDistortionParams, "distortion bound drifted from the protocol header");

const std::size_t kMaxSdkMessageBytes = 256;

// Enumerator names for every enum-valued field; an unlisted value reads as
// unknown. TransferFunctionValue_Linear is 0, so a zeroed field reads as
// linear: it is reported as given.
const char* pixel_format_name(uint32_t value) {
    switch (static_cast<av1_ipc::PixelFormat>(value)) {
        case av1_ipc::PixelFormat_Unknown: return "unknown";
        case av1_ipc::PixelFormat_NV12: return "nv12";
        case av1_ipc::PixelFormat_NV12_UBWC: return "nv12_ubwc";
        case av1_ipc::PixelFormat_P010: return "p010";
        case av1_ipc::PixelFormat_P010_UBWC: return "p010_ubwc";
    }
    return "unknown";
}
const char* camera_model_name(uint32_t value) {
    switch (static_cast<av1_ipc::CameraFrameModel>(value)) {
        case av1_ipc::CameraFrameModel_KB4: return "kb4";
        case av1_ipc::CameraFrameModel_KBSpline: return "kb_spline";
        case av1_ipc::CameraFrameModel_KBPolarSpline: return "kb_polar_spline";
        case av1_ipc::CameraFrameModel_Pinhole: return "pinhole";
    }
    return "unknown";
}
const char* offline_cal_source_name(uint32_t value) {
    switch (static_cast<av1_ipc::OfflineCalSource>(value)) {
        case av1_ipc::OfflineCalSource_None: return "none";
        case av1_ipc::OfflineCalSource_EEPROM: return "eeprom";
        case av1_ipc::OfflineCalSource_File: return "file";
        case av1_ipc::OfflineCalSource_Default: return "default";
    }
    return "unknown";
}
const char* transfer_function_name(uint32_t value) {
    switch (static_cast<av1_ipc::TransferFunctionValue>(value)) {
        case av1_ipc::TransferFunctionValue_Linear: return "linear";
        case av1_ipc::TransferFunctionValue_Srgb: return "srgb";
        case av1_ipc::TransferFunctionValue_Hlg: return "hlg";
    }
    return "unknown";
}
const char* color_primaries_name(uint32_t value) {
    switch (static_cast<av1_ipc::ColorPrimariesValue>(value)) {
        case av1_ipc::ColorPrimariesValue_BT709: return "bt709";
        case av1_ipc::ColorPrimariesValue_BT2020: return "bt2020";
    }
    return "unknown";
}
const char* ycbcr_encoding_name(uint32_t value) {
    switch (static_cast<av1_ipc::YCbCrEncodingValue>(value)) {
        case av1_ipc::YCbCrEncodingValue_BT601: return "bt601";
        case av1_ipc::YCbCrEncodingValue_BT709: return "bt709";
        case av1_ipc::YCbCrEncodingValue_BT2020: return "bt2020";
    }
    return "unknown";
}
const char* auto_white_balance_mode_name(uint32_t value) {
    switch (static_cast<av1_ipc::AutoWhiteBalanceModeValue>(value)) {
        case av1_ipc::AutoWhiteBalanceModeValue_On: return "on";
        case av1_ipc::AutoWhiteBalanceModeValue_Off: return "off";
        case av1_ipc::AutoWhiteBalanceModeValue_Freeze: return "freeze";
    }
    return "unknown";
}
const char* resolution_mode_name(uint32_t value) {
    switch (static_cast<av1_ipc::ResolutionModeValue>(value)) {
        case av1_ipc::ResolutionModeValue_Auto: return "auto";
        case av1_ipc::ResolutionModeValue_FullResolution: return "full_resolution";
        case av1_ipc::ResolutionModeValue_Downsampled: return "downsampled";
    }
    return "unknown";
}

// Bitmasks are reported raw, as hex; their bit names stay in the protocol header.
ControlSummary summarize(const av1_ipc::ControlState& state) {
    ControlSummary summary;
    summary.supported_controls = hex_u64(state.supportedControls, 16);
    summary.current_controls = hex_u64(state.currentControls, 16);
    summary.supported_sensor_bit_depths = hex_u32(state.supportedSensorBitDepths);
    summary.supported_auto_white_balance_modes = hex_u32(state.supportedAutoWhiteBalanceModes);
    summary.supported_transfer_functions = hex_u32(state.supportedTransferFunctions);
    summary.supported_color_primaries = hex_u32(state.supportedColorPrimaries);
    summary.supported_ycbcr_encodings = hex_u32(state.supportedYCbCrEncodings);
    summary.supported_resolution_modes = hex_u32(state.supportedResolutionModes);
    summary.red_recording_led_enabled = state.redRecordingLedEnabled;
    summary.sensor_bit_depth = state.sensorBitDepth;
    summary.auto_white_balance_mode = auto_white_balance_mode_name(state.autoWhiteBalanceMode);
    summary.hdr_enabled = state.hdrEnabled;
    summary.tone_mapping_enabled = state.toneMappingEnabled;
    summary.transfer_function = transfer_function_name(state.transferFunction);
    summary.color_primaries = color_primaries_name(state.colorPrimaries);
    summary.ycbcr_encoding = ycbcr_encoding_name(state.yCbCrEncoding);
    summary.resolution_mode = resolution_mode_name(state.resolutionMode);
    summary.brightness_ev = state.brightnessEv;
    summary.auto_exposure_enabled = state.autoExposureEnabled;
    summary.min_exposure_ms = state.minExposureMs;
    summary.max_exposure_ms = state.maxExposureMs;
    summary.min_gain = state.minGain;
    summary.max_gain = state.maxGain;
    summary.current_fps = state.currentFPS;
    // exposureMs and gain are latest sensor values: not configuration, not reported.
    return summary;
}

// Copies one camera's format metadata and validity flags; counts were checked by the caller.
void translate_camera(const av1_ipc::CameraFrame& frame, CameraMeta& meta) {
    meta.frame_id = frame.frameId;
    meta.exposure_timestamp_ns = frame.exposureTimestampNs;
    meta.timestamp_seconds = frame.timestampSeconds;
    meta.width = frame.dmabuf.width;
    meta.height = frame.dmabuf.height;
    meta.drm_format = hex_u32(frame.dmabuf.drmFormat);
    meta.pixel_format = pixel_format_name(frame.dmabuf.pixelFormat);
    meta.drm_modifier = hex_u64(frame.dmabuf.drmModifier, 16);
    meta.plane_count = frame.dmabuf.planeCount;
    for (uint32_t plane = 0; plane < frame.dmabuf.planeCount && plane < kMaxPlanes; ++plane) {
        meta.planes[plane].offset = frame.dmabuf.planes[plane].offset;
        meta.planes[plane].stride = frame.dmabuf.planes[plane].stride;
        meta.planes[plane].size = frame.dmabuf.planes[plane].size;
    }
    meta.texture_bounds_valid = frame.textureBounds.valid != 0;
    meta.intrinsics_valid = frame.intrinsics.valid != 0;
    meta.camera_model = camera_model_name(frame.intrinsics.cameraModel);
    meta.kb4_valid = frame.kb4Intrinsics.valid != 0;
    meta.first_camera_from_camera_valid = frame.firstCameraFromCameraValid != 0;
    meta.aux_json_size = frame.auxJsonSize;
    meta.transfer = transfer_function_name(frame.transferFunction);
    meta.primaries = color_primaries_name(frame.colorPrimaries);
    meta.ycbcr_encoding = ycbcr_encoding_name(frame.yCbCrEncoding);
    // Not copied: intrinsics values, kb4 values, firstCameraFromCamera, auxJson,
    // sensorTemperatureCelsius, exposureSeconds, gain (Camera Data).
}

class SdkBackend : public CameraBackend {
public:
    SdkBackend() : connected_(false) {}
    ~SdkBackend() { disconnect(); }

    const char* backend_name() const { return "sdk"; }

    Outcome connect(uint32_t timeout_ms) {
        const AV1CameraClient::Status status = client_.connect(timeout_ms);
        record(status);
        const Outcome outcome = map_status(status, SdkCall::connect);
        if (outcome != Outcome::ok) return outcome;
        connected_ = true;
        const av1_ipc::HelloReply& reply = client_.hello();
        hello_ = HelloFacts();
        hello_.protocol_version = reply.protocolVersion;
        hello_.camera_count = reply.cameraCount;
        hello_.offline_cal_source = offline_cal_source_name(reply.offlineCalSource);
        hello_.offline_cal_size = reply.offlineCalSize;
        hello_.offline_cal_size_within_bound = reply.offlineCalSize <= kMaxOfflineCalBytes;
        if (!hello_.offline_cal_size_within_bound || reply.cameraCount > kMaxCameras) return Outcome::invalid_metadata;
        hello_.offline_cal_sha256 = sha256_hex(reply.offlineCal, reply.offlineCalSize);
        return Outcome::ok;
    }

    const HelloFacts& hello() const { return hello_; }

    Outcome control_state(ControlSummary& summary) {
        av1_ipc::ControlState state;
        std::memset(&state, 0, sizeof state);
        const AV1CameraClient::Status status = client_.getControlState(state);
        record(status);
        const Outcome outcome = map_status(status, SdkCall::control_state);
        if (outcome == Outcome::ok) summary = summarize(state);
        else note_loss(outcome);
        return outcome;
    }

    Outcome set_reader(bool active) {
        // The recording flag is always false: this helper never requests exclusive control.
        const AV1CameraClient::Status status = client_.setReaderState(active, false);
        record(status);
        const Outcome outcome = map_status(status, SdkCall::set_reader);
        if (outcome != Outcome::ok) note_loss(outcome);
        return outcome;
    }

    Outcome acquire(uint32_t timeout_ms, FrameSetMeta& meta) {
        AV1CameraClient::FrameSet set;
        const AV1CameraClient::Status status = client_.acquireFrameSet(set, timeout_ms);
        record(status);
        const Outcome outcome = map_status(status, SdkCall::acquire);
        if (outcome != Outcome::ok) {
            note_loss(outcome);
            return outcome;
        }
        // Check every count before indexing, then copy, then release. Nothing
        // beyond the checked counts is read; nothing is clamped silently.
        const av1_ipc::AcquireFrameSetReply& reply = set.metadata();
        bool valid = reply.cameraCount <= kMaxCameras;
        for (uint32_t camera = 0; valid && camera < reply.cameraCount; ++camera) {
            const av1_ipc::CameraFrame& frame = reply.cameras[camera];
            valid = counts_within_bounds(reply.cameraCount, frame.dmabuf.planeCount, frame.auxJsonSize,
                                         frame.intrinsics.distortionParamCount) &&
                    frame.kb4Intrinsics.distortionParamCount <= kMaxDistortionParams;
        }
        if (valid) {
            meta = FrameSetMeta();
            meta.sequence = reply.sequence;
            meta.camera_count = reply.cameraCount;
            meta.pose_valid = reply.pose.poseValid != 0;
            meta.front_from_head_valid = reply.pose.frontCam0FromHeadValid != 0;
            meta.front_from_avcam0_valid = reply.pose.frontCam0FromAvCam0Valid != 0;
            meta.velocity_valid = reply.pose.velocityValid != 0;
            for (uint32_t camera = 0; camera < reply.cameraCount; ++camera) {
                translate_camera(reply.cameras[camera], meta.cameras[camera]);
            }
        }
        const AV1CameraClient::Status released = set.release();
        if (!released) {
            record(released);
            connected_ = false;
            return Outcome::transport_lost;
        }
        return valid ? Outcome::ok : Outcome::invalid_metadata;
    }

    void disconnect() {
        if (connected_) client_.disconnect();
        connected_ = false;
    }

    const StatusDiagnostics& last_status() const { return last_; }

private:
    void record(const AV1CameraClient::Status& status) {
        last_.status_error = status_error_name(status.error);
        last_.result = status.error == AV1CameraClient::Status::Error::Server ? result_name(status.result)
                                                                              : "Result_Success";
        last_.system_error = status.systemError;
        const char* message = status.message();
        last_.sdk_message.clear();
        if (message) last_.sdk_message.assign(message, strnlen(message, kMaxSdkMessageBytes));
    }
    void note_loss(Outcome outcome) {
        if (outcome == Outcome::transport_lost) connected_ = false;
    }

    AV1CameraClient client_;
    HelloFacts hello_;
    StatusDiagnostics last_;
    bool connected_;
};

} // namespace

std::unique_ptr<CameraBackend> make_camera_backend(const std::string& fake_scenario) {
    (void)fake_scenario;
    return std::unique_ptr<CameraBackend>(new SdkBackend());
}

} // namespace sightcircuit_camera
