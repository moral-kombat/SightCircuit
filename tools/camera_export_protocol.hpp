// Camera export protocol, version 1 ("camera export v1"): the constants of the
// packet stream that the camera helper's --export-frames mode writes to its
// parent process, shared with the Python reader. Constants only: a header
// generated from the Python camera contract will replace this file, so no
// logic lives here. tests/test_camera_export.py pins every value.
//
// stdout is a sequence of packets and nothing else. A packet is the magic
// "SCCF", then three little-endian u32 fields (protocol version, header_bytes,
// payload_bytes), then header_bytes of UTF-8 JSON (one object) and
// payload_bytes of payload. The header's "kind" is "ready", "frame" or "end";
// only "frame" carries a payload (BGR3 pixels, stride_bytes * height bytes).
// C++11.
#pragma once
#include <cstddef>
#include <cstdint>

namespace sightcircuit_camera_export {

const char kMagic[4] = {'S', 'C', 'C', 'F'};
const uint32_t kProtocolVersion = 1;
// magic + version + header_bytes + payload_bytes
const std::size_t kPacketPrefixBytes = 16;
const std::size_t kMaxHeaderBytes = 16384;
const std::size_t kMaxPayloadBytes = 33554432;

const char* const kKindReady = "ready";
const char* const kKindFrame = "frame";
const char* const kKindEnd = "end";

const char* const kDeliveredFourcc = "BGR3";

const char* const kPixelPathCpuLinear = "cpu_linear";
const char* const kPixelPathGpuConvert = "gpu_convert";
const char* const kDerivationCpu = "cpu_converted";
const char* const kDerivationGpu = "gpu_converted";

const char* const kEndMaxSeconds = "max_seconds";
const char* const kEndMaxFrames = "max_frames";
const char* const kEndStopSignal = "stop_signal";
const char* const kEndHeadsetRemoved = "headset_removed";
const char* const kEndStandby = "standby";
const char* const kEndSdkError = "sdk_error";
const char* const kEndPixelPathError = "pixel_path_error";
const char* const kEndWatchdog = "watchdog";

} // namespace sightcircuit_camera_export
