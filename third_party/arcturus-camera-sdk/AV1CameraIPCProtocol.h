// Copyright (c) 2026 Arcturus Industries LLC. All rights reserved.
//
// Licensed under the Arcturus Vision Camera Preview SDK Developer
// License Agreement (AV1CameraSDK-LICENSE.md).
// Use and distribution are subject to that agreement.

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace av1_ipc
{
constexpr uint32_t kMagic = 0x31435641u; // AVC1
// Protocol version sent by this SDK.
constexpr uint32_t kProtocolVersion = 1;
// Public message IDs use only the low half of the message type field.
constexpr uint32_t kMaxPublicMessageType = 0x7fffffffu;
constexpr uint32_t kMaxPayloadBytes = 65536;
constexpr const char* kDefaultSocketPath = "/tmp/av1_external_camera_provider.sock";
constexpr uint32_t kMaxCameras = 2;
constexpr uint32_t kMaxPlanes = 4;
constexpr uint32_t kMaxDistortionParams = 128;
constexpr uint32_t kMaxAuxJsonBytes = 8192;
// Maximum offline calibration payload size in bytes.
constexpr uint32_t kMaxOfflineCalBytes = 8192;

enum MessageType : uint32_t
{
    MessageType_Hello = 1,
    MessageType_AcquireFrameSet = 2,
    MessageType_ReleaseFrameSet = 3,
    MessageType_SetReaderState = 4,
    MessageType_GetControlState = 5,
    MessageType_SetControls = 6,
};

enum Result : uint32_t
{
    Result_Success = 0,
    Result_Unavailable = 1,
    Result_InvalidArgument = 2,
    Result_ClientProtocolTooNew = 3,
    Result_NotReady = 4,
    Result_InternalError = 5,
    Result_Timeout = 6,
    Result_ResourceDenied = 7,
    Result_ClientAlreadyConnected = 8,
    // SDK operations are disabled by the host's developer-access policy.
    Result_PermissionDenied = 9,
    Result_ClientProtocolTooOld = 10,
};

enum PixelFormat : uint32_t
{
    PixelFormat_Unknown = 0,
    PixelFormat_NV12 = 1,
    PixelFormat_NV12_UBWC = 2,
    PixelFormat_P010 = 3,
    PixelFormat_P010_UBWC = 4,
};

enum CameraFrameModel : uint32_t
{
    CameraFrameModel_KB4 = 0,
    CameraFrameModel_KBSpline = 1,
    CameraFrameModel_KBPolarSpline = 2,
    CameraFrameModel_Pinhole = 3,
};

// Source of the offline calibration returned in HelloReply. Calibration files
// take precedence over EEPROM data, followed by built-in defaults.
enum OfflineCalSource : uint32_t
{
    OfflineCalSource_None = 0,
    OfflineCalSource_EEPROM = 1,
    // Calibration loaded from an aicams.json file, which takes
    // precedence over calibration stored in the camera's EEPROM.
    OfflineCalSource_File = 2,
    OfflineCalSource_Default = 3,
};

enum ControlBits : uint64_t
{
    Control_RedRecordingLed = 1ull << 0,
    Control_SensorBitDepth = 1ull << 1,
    Control_AutoWhiteBalance = 1ull << 2,
    Control_Hdr = 1ull << 3,
    Control_ToneMapping = 1ull << 4,
    Control_TransferFunction = 1ull << 5,
    Control_Resolution = 1ull << 6,
    Control_Fps = 1ull << 7,
    Control_Brightness = 1ull << 8,
    Control_ColorPrimaries = 1ull << 9,
    Control_YCbCrEncoding = 1ull << 10,
    Control_ExposureGain = 1ull << 14,
};

enum SensorBitDepthCapability : uint32_t
{
    SensorBitDepthCapability_8 = 1u << 0,
    SensorBitDepthCapability_10 = 1u << 1,
};

enum AutoWhiteBalanceModeCapability : uint32_t
{
    AutoWhiteBalanceModeCapability_On = 1u << 0,
    AutoWhiteBalanceModeCapability_Off = 1u << 1,
    AutoWhiteBalanceModeCapability_Freeze = 1u << 2,
};

enum TransferFunctionCapability : uint32_t
{
    TransferFunctionCapability_Srgb = 1u << 0,
    TransferFunctionCapability_Hlg = 1u << 1,
    TransferFunctionCapability_Linear = 1u << 2,
};

enum ResolutionModeCapability : uint32_t
{
    ResolutionModeCapability_Auto = 1u << 0,
    ResolutionModeCapability_FullResolution = 1u << 1,
    ResolutionModeCapability_Downsampled = 1u << 2,
};

enum TransferFunctionValue : uint32_t
{
    TransferFunctionValue_Linear = 0,
    TransferFunctionValue_Srgb = 2,
    TransferFunctionValue_Hlg = 4,
};

enum AutoWhiteBalanceModeValue : uint32_t
{
    AutoWhiteBalanceModeValue_On = 1,
    AutoWhiteBalanceModeValue_Off = 2,
    AutoWhiteBalanceModeValue_Freeze = 3,
};

enum ResolutionModeValue : uint32_t
{
    ResolutionModeValue_Auto = 1,
    ResolutionModeValue_FullResolution = 2,
    ResolutionModeValue_Downsampled = 4,
};

enum ColorPrimariesCapability : uint32_t
{
    ColorPrimariesCapability_BT709 = 1u << 0,
    ColorPrimariesCapability_BT2020 = 1u << 1,
};

enum ColorPrimariesValue : uint32_t
{
    ColorPrimariesValue_BT709 = 1,
    ColorPrimariesValue_BT2020 = 2,
};

enum YCbCrEncodingCapability : uint32_t
{
    YCbCrEncodingCapability_BT601 = 1u << 0,
    YCbCrEncodingCapability_BT709 = 1u << 1,
    YCbCrEncodingCapability_BT2020 = 1u << 2,
};

enum YCbCrEncodingValue : uint32_t
{
    YCbCrEncodingValue_BT601 = 1,
    YCbCrEncodingValue_BT709 = 2,
    YCbCrEncodingValue_BT2020 = 3,
};

struct MessageHeader
{
    uint32_t magic;
    uint32_t version;
    uint32_t type;
    uint32_t payloadSize;
    uint64_t requestId;
};

struct GenericReply
{
    uint32_t result;
    uint32_t reserved;
};

struct Plane
{
    uint32_t offset;
    uint32_t stride;
    uint32_t size;
};

struct DmabufAttributes
{
    uint32_t width;
    uint32_t height;
    uint32_t drmFormat;
    uint32_t pixelFormat;
    uint64_t drmModifier;
    uint32_t planeCount;
    Plane planes[kMaxPlanes];
};

struct Intrinsics
{
    uint32_t valid;
    uint32_t cameraModel;
    float fx;
    float fy;
    float cx;
    float cy;
    uint32_t distortionParamCount;
    float distortionParams[kMaxDistortionParams];
};

struct TextureBounds
{
    uint32_t valid;
    float uMin;
    float vMin;
    float uMax;
    float vMax;
};

struct CameraFrame
{
    uint64_t frameId;
    int64_t exposureTimestampNs;
    double timestampSeconds;
    DmabufAttributes dmabuf;
    TextureBounds textureBounds;
    Intrinsics intrinsics; // Camera calibration; cameraModel identifies the model.
    // Separate KB4 calibration, with the current frame's thermal focal correction
    // applied. valid is 0 when no KB4 model is available.
    Intrinsics kb4Intrinsics;
    uint32_t firstCameraFromCameraValid;
    float firstCameraFromCamera[16];
    uint32_t auxJsonSize;
    char auxJson[kMaxAuxJsonBytes];
    uint32_t transferFunction;
    uint32_t colorPrimaries;
    // YCbCrEncodingValue selects the conversion matrix. Camera frames use
    // full-range YCbCr for all supported pixel formats and encodings.
    uint32_t yCbCrEncoding;
    // Sensor temperature for this frame in degrees Celsius; NaN when unavailable.
    // The frame's intrinsics already include the thermal focal correction.
    float sensorTemperatureCelsius;
    // Measured values for this captured frame; NaN when unavailable.
    float exposureSeconds;
    float gain;
};

// There is one pose per frame set. When poseValid and frontCam0FromAvCam0Valid are
// nonzero, compute each camera's world pose as
// worldFromAvCam0 * cameras[i].firstCameraFromCamera, checking that camera's
// firstCameraFromCameraValid flag. The camera-0 transform is identity in this case.
//
// "avCam0" and "avCam1" are the module's passthrough cameras; avCam0 is cameras[0].
// "frontCam0" is the headset's front-right tracking camera, a separate camera from
// the passthrough module. Transform names use destinationFromSource notation.
struct FrameSetPose
{
    uint32_t poseValid;
    uint32_t frontCam0FromHeadValid;
    uint32_t frontCam0FromAvCam0Valid;
    // Transform from avCam0 to the raw, uncalibrated tracking world, predicted at
    // avCam0 mid-exposure. Check poseValid before use. If frontCam0FromAvCam0Valid
    // is 0, this instead contains the frontCam0 tracking-camera pose.
    float worldFromAvCam0[16];
    // Runtime transform from the head to frontCam0, sampled when the camera service starts.
    // Check frontCam0FromHeadValid before use.
    float frontCam0FromHeadRuntime[16];
    // Transform from the factory/offline head frame to the runtime head frame.
    // The offline alignment is frontCam0FromHeadRuntime * headLiveFromHeadOffline.
    // Identity means the runtime and offline alignments are equal.
    float headLiveFromHeadOffline[16];
    // Transform from passthrough camera avCam0 to tracking camera frontCam0, used
    // to compute worldFromAvCam0. Check frontCam0FromAvCam0Valid before use. When
    // it is 0, the frame-set pose and per-camera extrinsics remain frontCam0-rooted.
    // With valid transforms, recover the runtime head pose as:
    // worldFromHead = worldFromAvCam0 * inv(frontCam0FromAvCam0) * frontCam0FromHeadRuntime.
    float frontCam0FromAvCam0[16];
    // Headset velocities at this frame set's prediction timestamp, expressed in
    // the same tracking world as the pose. Check velocityValid before use.
    // Linear velocity is measured at the runtime head origin, not at the camera.
    // For another point P, use v_P = v_head + w x (p_P - p_head), with all vectors
    // in world coordinates and x denoting the cross product.
    // Angular velocity is in rad/s; linear velocity is in m/s.
    uint32_t velocityValid;
    float angularVelocityOfHeadLiveInWorld[3];
    float linearVelocityOfHeadLiveInWorld[3];
};

struct AcquireFrameSetRequest
{
    uint32_t timeoutMs;
};

struct AcquireFrameSetReply
{
    uint32_t result;
    uint32_t cameraCount;
    uint64_t sequence;
    FrameSetPose pose;
    CameraFrame cameras[kMaxCameras];
};

struct ReleaseFrameSetRequest
{
    uint64_t sequence;
};

struct ReaderStateRequest
{
    uint32_t active; // Nonzero enables this client's frame access.
    // While active, a zero-to-nonzero transition requests exclusive camera-control
    // access. Returning to zero or deactivating the reader releases that access.
    // Does not record images or enable the red LED; success does not guarantee
    // exclusive control access.
    uint32_t recording;
};

struct ControlState
{
    uint64_t supportedControls;
    uint64_t currentControls;
    uint32_t supportedSensorBitDepths;
    uint32_t supportedAutoWhiteBalanceModes;
    uint32_t supportedTransferFunctions;
    uint32_t supportedColorPrimaries;
    uint32_t supportedYCbCrEncodings;
    uint32_t supportedResolutionModes;
    uint32_t redRecordingLedEnabled;
    uint32_t sensorBitDepth;
    uint32_t autoWhiteBalanceMode;
    uint32_t hdrEnabled;
    uint32_t toneMappingEnabled;
    uint32_t transferFunction;
    uint32_t colorPrimaries;
    uint32_t yCbCrEncoding;
    uint32_t resolutionMode;
    float currentFPS;
    // General brightness as exposure compensation, in EV stops. 0 == neutral.
    float brightnessEv;
    uint32_t autoExposureEnabled; // 1: automatic exposure, 0: manual exposure/gain.
    float exposureMs;             // Latest sensor value, NAN before the first frame.
    float gain;                   // Latest sensor value, NAN before the first frame.
    float minExposureMs;
    float maxExposureMs;
    float minGain;
    float maxGain;
};

// Hello must succeed before accessing frames or sending other commands. If another
// client owns the session, Camera Switch returns Result_ClientAlreadyConnected and
// closes the new connection. The existing client's session remains active.
struct HelloReply
{
    uint32_t result;
    uint32_t protocolVersion;
    uint32_t cameraCount;
    ControlState controls;
    // Offline calibration JSON. Read exactly offlineCalSize bytes;
    // no NUL terminator is guaranteed. This calibration is fixed for the session.
    uint32_t offlineCalSource; // OfflineCalSource
    uint32_t offlineCalSize;
    char offlineCal[kMaxOfflineCalBytes];
};

struct GetControlStateReply
{
    uint32_t result;
    uint32_t reserved;
    ControlState controls;
};

struct ControlsRequest
{
    uint64_t requestedControls;
    uint32_t redRecordingLedEnabled;
    uint32_t sensorBitDepth;
    uint32_t autoWhiteBalanceMode;
    uint32_t hdrEnabled;
    uint32_t toneMappingEnabled;
    uint32_t transferFunction;
    uint32_t colorPrimaries;
    uint32_t yCbCrEncoding;
    uint32_t resolutionMode;
    float fps;
    // General brightness as exposure compensation, in EV stops. 0 == neutral.
    // Applied only when the Control_Brightness bit is set in requestedControls.
    float brightnessEv;
    // Control_ExposureGain applies the mode and both targets atomically.
    // autoExposureEnabled is 0 or 1. Manual targets must be finite and within
    // the advertised sensor ranges; targets are ignored in automatic mode.
    uint32_t autoExposureEnabled;
    float exposureMs;
    float gain;
};

// Public message IDs occupy the low half of the message type field.
static_assert(MessageType_Hello <= kMaxPublicMessageType && MessageType_AcquireFrameSet <= kMaxPublicMessageType &&
                  MessageType_ReleaseFrameSet <= kMaxPublicMessageType &&
                  MessageType_SetReaderState <= kMaxPublicMessageType &&
                  MessageType_GetControlState <= kMaxPublicMessageType &&
                  MessageType_SetControls <= kMaxPublicMessageType,
              "Public message ID exceeds the public range");

// Required camera protocol wire layout.
static_assert(sizeof(MessageHeader) == 24 && offsetof(MessageHeader, requestId) == 16,
              "Unexpected message header layout");
static_assert(sizeof(GenericReply) == 8, "Unexpected generic reply layout");
static_assert(sizeof(AcquireFrameSetReply) <= kMaxPayloadBytes, "Frame reply exceeds maximum payload size");
} // namespace av1_ipc
