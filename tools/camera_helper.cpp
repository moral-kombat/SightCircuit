// sightcircuit-camera-helper: reports what the Arcturus camera SDK provides,
// and, in --export-frames, delivers its frames to the parent process. The only
// SightCircuit binary that links the Client Library (through
// camera_backend_sdk.cpp); the fake build drives the same code from
// camera_backend_fake.cpp on any architecture.
//
// Modes: --describe (no connect), --hello (connect, handshake and control
// state; the reader stays off), --probe-frames N --max-seconds S (metadata
// of a few frame sets with the reader active, paced well below camera rate)
// and --export-frames (BGR3 frames to the parent over stdout under the camera
// export protocol, camera_export_protocol.hpp, paced by dropping frame sets).
// The metadata modes write NDJSON on the real stdout; the export mode writes
// packets there and nothing else, and every diagnostic record becomes a JSON
// line on stderr. The confirmation notice goes to the terminal, never to the
// protocol stream.
//
// It never writes a control, never asks for exclusive access and writes no
// file. The metadata modes never touch a buffer descriptor; the export mode
// reads buffers only through the pixel paths (camera_pixels.hpp) and a pixel
// leaves the process only through the pipe to its parent. C++17: it includes
// overlay_lifecycle.hpp for stdout isolation and the SIGALRM watchdog.
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <sys/file.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "camera_backend.hpp"
#include "camera_export_protocol.hpp"
#include "camera_pixels.hpp"
#include "overlay_lifecycle.hpp"

namespace lifecycle = sightcircuit_lifecycle;
namespace export_protocol = sightcircuit_camera_export;
using namespace sightcircuit_camera;

namespace {

constexpr const char* kReportKind = "sightcircuit_camera_helper";
constexpr int kSchemaVersion = 1;
constexpr const char* kContract = "camera-helper/1";
constexpr std::size_t kMaxLineBytes = 8192;
constexpr uint32_t kFrameTimeoutMs = 50;
constexpr int kExitRefused = 5;
constexpr int kExitInvalidArguments = 64;
constexpr int kExitCancelled = 130;
// The export mode acquires at camera rate with this server-side timeout and
// ends the run when no frame set has arrived for the presence gap.
constexpr uint32_t kExportFrameTimeoutMs = 100;
constexpr int64_t kPresenceGapNs = 2000000000ll;
constexpr unsigned kConvertWatchdogSeconds = 10;
constexpr unsigned kMaxExportFps = 10;
constexpr unsigned kMaxExportSeconds = 60;
constexpr unsigned kMaxExportFrames = 600;
constexpr unsigned kMaxProbeSeconds = 10;

#ifdef SIGHTCIRCUIT_CAMERA_BACKEND_FAKE
constexpr const char* kBackendName = "fake";
constexpr bool kFakeBuild = true;
#else
constexpr const char* kBackendName = "sdk";
constexpr bool kFakeBuild = false;
#endif

#ifdef SIGHTCIRCUIT_BUILD_COMMIT
constexpr const char* kBuildCommit = SIGHTCIRCUIT_BUILD_COMMIT;
#else
constexpr const char* kBuildCommit = nullptr;
#endif
#ifdef SIGHTCIRCUIT_BUILD_DIRTY
constexpr int kBuildDirty = SIGHTCIRCUIT_BUILD_DIRTY;
#else
constexpr int kBuildDirty = -1;
#endif
#ifdef SIGHTCIRCUIT_BUILD_COMMIT_REASON
constexpr const char* kBuildCommitReason = SIGHTCIRCUIT_BUILD_COMMIT_REASON;
#else
constexpr const char* kBuildCommitReason = "not_recorded_at_build";
#endif
#if defined(SIGHTCIRCUIT_SDK_SOURCE_REVISION) && defined(SIGHTCIRCUIT_SDK_AGREEMENT_VERSION)
constexpr const char* kSdkSourceRevision = SIGHTCIRCUIT_SDK_SOURCE_REVISION;
constexpr const char* kSdkAgreementVersion = SIGHTCIRCUIT_SDK_AGREEMENT_VERSION;
#else
constexpr const char* kSdkSourceRevision = nullptr;
constexpr const char* kSdkAgreementVersion = nullptr;
#endif

// ---- a small JSON writer -------------------------------------------------

std::string json_quote(const std::string& text) {
    std::string out = "\"";
    for (unsigned char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20 || c >= 0x7f) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof escaped, "\\u%04x", static_cast<unsigned>(c));
                    out += escaped;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
    return out;
}

class JsonArray {
public:
    JsonArray& raw(const std::string& json) { begin(); text_ += json; return *this; }
    JsonArray& str(const std::string& value) { return raw(json_quote(value)); }
    std::string done() const { return text_ + "]"; }
private:
    void begin() { text_ += first_ ? "" : ","; first_ = false; }
    std::string text_ = "[";
    bool first_ = true;
};

class JsonObject {
public:
    JsonObject& str(const char* key, const std::string& value) { begin(key); text_ += json_quote(value); return *this; }
    JsonObject& boolean(const char* key, bool value) { begin(key); text_ += value ? "true" : "false"; return *this; }
    JsonObject& integer(const char* key, long long value) { begin(key); text_ += std::to_string(value); return *this; }
    JsonObject& unsigned_integer(const char* key, unsigned long long value) {
        begin(key); text_ += std::to_string(value); return *this;
    }
    // Finite numbers only; NaN and infinities become null, as JSON has no spelling for them.
    JsonObject& number(const char* key, double value) {
        begin(key);
        if (std::isfinite(value)) {
            char text[32];
            std::snprintf(text, sizeof text, "%.17g", value);
            text_ += text;
        } else {
            text_ += "null";
        }
        return *this;
    }
    JsonObject& real(const char* key, float value) {
        begin(key);
        if (std::isfinite(value)) {
            char text[32];
            std::snprintf(text, sizeof text, "%.9g", static_cast<double>(value));
            text_ += text;
        } else {
            text_ += "null";
        }
        return *this;
    }
    JsonObject& null(const char* key) { begin(key); text_ += "null"; return *this; }
    JsonObject& raw(const char* key, const std::string& json) { begin(key); text_ += json; return *this; }
    JsonObject& optional_integer(const char* key, bool present, long long value) {
        return present ? integer(key, value) : null(key);
    }
    std::string done() const { return text_ + "}"; }
private:
    void begin(const char* key) { text_ += first_ ? "" : ","; first_ = false; text_ += json_quote(key); text_ += ':'; }
    std::string text_ = "{";
    bool first_ = true;
};

// ---- clocks and signals ----------------------------------------------------

int64_t clock_ns(clockid_t clock) {
    timespec now{};
    if (clock_gettime(clock, &now) != 0) return 0;
    return static_cast<int64_t>(now.tv_sec) * 1000000000ll + now.tv_nsec;
}

struct HostClocks {
    int64_t monotonic_ns = 0, boottime_ns = 0, realtime_ns = 0;
    static HostClocks sample() {
        HostClocks clocks;
        clocks.monotonic_ns = clock_ns(CLOCK_MONOTONIC);
        clocks.boottime_ns = clock_ns(CLOCK_BOOTTIME);
        clocks.realtime_ns = clock_ns(CLOCK_REALTIME);
        return clocks;
    }
    std::string json() const {
        return JsonObject().integer("monotonic_ns", monotonic_ns).integer("boottime_ns", boottime_ns)
            .integer("realtime_ns", realtime_ns).done();
    }
};

sigset_t stop_signals() {
    sigset_t set;
    sigemptyset(&set);
    sigaddset(&set, SIGINT);
    sigaddset(&set, SIGTERM);
    sigaddset(&set, SIGHUP);
    return set;
}
const char* signal_name(int signal) {
    switch (signal) {
        case SIGINT: return "SIGINT";
        case SIGTERM: return "SIGTERM";
        case SIGHUP: return "SIGHUP";
        default: return "signal";
    }
}
// A stop signal already pending, consumed, or 0. Never interrupts an SDK call:
// the signals stay blocked and are only ever collected between calls.
int pending_stop() {
    const sigset_t set = stop_signals();
    siginfo_t info{};
    const timespec zero{0, 0};
    const int signal = sigtimedwait(&set, &info, &zero);
    return signal > 0 ? signal : 0;
}
// Waits until the monotonic deadline, returning early with the stop signal if one arrives.
int wait_for_stop_until(int64_t deadline_ns) {
    const sigset_t set = stop_signals();
    for (;;) {
        const int64_t remaining = deadline_ns - clock_ns(CLOCK_MONOTONIC);
        if (remaining <= 0) return 0;
        timespec wait{};
        wait.tv_sec = static_cast<time_t>(remaining / 1000000000ll);
        wait.tv_nsec = static_cast<long>(remaining % 1000000000ll);
        siginfo_t info{};
        const int signal = sigtimedwait(&set, &info, &wait);
        if (signal > 0) return signal;
        if (errno == EAGAIN) return 0;
        if (errno != EINTR) return 0;
    }
}

void write_fd(int fd, const std::string& text) {
    const char* data = text.data();
    std::size_t size = text.size();
    while (size) {
        const ssize_t count = ::write(fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return;
        data += count;
        size -= static_cast<std::size_t>(count);
    }
}
// Set once --export-frames is parsed: stdout then carries packets only, so
// records go to stderr as JSON lines and plain notes are wrapped as JSON.
bool g_export_mode = false;
JsonObject record(const char* event);
void note(const std::string& text) {
    if (g_export_mode) {
        write_fd(STDERR_FILENO, record("note").str("message", text).done() + "\n");
        return;
    }
    write_fd(STDERR_FILENO, text + "\n");
}

// ---- arguments -------------------------------------------------------------

struct Options {
    enum Mode { none, describe, hello, probe, export_frames };
    Mode mode = none;
    unsigned frame_sets = 0;
    unsigned max_seconds = 0;
    unsigned interval_ms = 200;
    unsigned timeout_ms = 2000;
    unsigned fps = 5;
    unsigned max_frames = 0;
    std::string camera = "left";        // left, right or both
    std::string pixel_path = "auto";    // auto, cpu_linear or gpu_convert
    std::string fake_scenario = "ok_linear_nv12";
    bool help = false;
    std::string error;

    bool exports_camera(uint32_t index) const {
        return camera == "both" || (camera == "left" && index == 0) || (camera == "right" && index == 1);
    }
    uint32_t highest_camera_index() const { return camera == "left" ? 0u : 1u; }
};

bool parse_unsigned(const char* text, unsigned& value) {
    if (!text || !*text || std::strlen(text) > 7) return false;
    unsigned parsed = 0;
    for (const char* p = text; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        parsed = parsed * 10 + static_cast<unsigned>(*p - '0');
    }
    value = parsed;
    return true;
}

Options parse_arguments(int argc, char** argv) {
    Options options;
    bool have_max_seconds = false, have_max_frames = false, have_interval = false;
    bool have_fps = false, have_camera = false, have_pixel_path = false;
    auto set_mode = [&](Options::Mode mode) {
        if (options.mode != Options::none) options.error = "exactly one mode is allowed";
        options.mode = mode;
    };
    auto take_value = [&](int& index, const char* flag, unsigned& value, unsigned low, unsigned high) {
        if (index + 1 >= argc || !parse_unsigned(argv[index + 1], value) || value < low || value > high) {
            options.error = std::string(flag) + " needs an integer between " + std::to_string(low) + " and " +
                            std::to_string(high);
            return false;
        }
        ++index;
        return true;
    };
    for (int index = 1; index < argc && options.error.empty(); ++index) {
        const std::string argument = argv[index];
        if (argument == "--help") {
            options.help = true;
        } else if (argument == "--describe") {
            set_mode(Options::describe);
        } else if (argument == "--hello") {
            set_mode(Options::hello);
        } else if (argument == "--probe-frames") {
            set_mode(Options::probe);
            take_value(index, "--probe-frames", options.frame_sets, 1, 50);
        } else if (argument == "--export-frames") {
            set_mode(Options::export_frames);
            g_export_mode = true;
        } else if (argument == "--max-seconds") {
            // The probe's bound (1-10) is checked once the mode is known, below.
            if (take_value(index, "--max-seconds", options.max_seconds, 1, kMaxExportSeconds)) have_max_seconds = true;
        } else if (argument == "--max-frames") {
            if (take_value(index, "--max-frames", options.max_frames, 1, kMaxExportFrames)) have_max_frames = true;
        } else if (argument == "--fps") {
            if (take_value(index, "--fps", options.fps, 1, kMaxExportFps)) have_fps = true;
        } else if (argument == "--camera") {
            have_camera = true;
            if (index + 1 >= argc) options.error = "--camera needs left, right or both";
            else options.camera = argv[++index];
            if (options.camera != "left" && options.camera != "right" && options.camera != "both") {
                options.error = "--camera needs left, right or both";
            }
        } else if (argument == "--pixel-path") {
            have_pixel_path = true;
            if (index + 1 >= argc) options.error = "--pixel-path needs auto, cpu_linear or gpu_convert";
            else options.pixel_path = argv[++index];
            if (options.pixel_path != "auto" && options.pixel_path != export_protocol::kPixelPathCpuLinear &&
                options.pixel_path != export_protocol::kPixelPathGpuConvert) {
                options.error = "--pixel-path needs auto, cpu_linear or gpu_convert";
            }
        } else if (argument == "--interval-ms") {
            have_interval = true;
            take_value(index, "--interval-ms", options.interval_ms, 100, 1000);
        } else if (argument == "--timeout-ms") {
            take_value(index, "--timeout-ms", options.timeout_ms, 1, 10000);
        } else if (argument == "--fake-scenario" && kFakeBuild) {
            if (index + 1 >= argc) options.error = "--fake-scenario needs a name";
            else options.fake_scenario = argv[++index];
        } else {
            options.error = "unknown argument: " + argument;
        }
    }
    if (options.help || !options.error.empty()) return options;
    const bool export_only = have_max_frames || have_fps || have_camera || have_pixel_path;
    if (options.mode == Options::none) {
        options.error = "one of --describe, --hello, --probe-frames or --export-frames is required";
    } else if (options.mode == Options::export_frames) {
        if (!have_max_seconds || !have_max_frames) options.error = "--export-frames needs --max-seconds and --max-frames";
        else if (have_interval) options.error = "--interval-ms applies to --probe-frames";
    } else if (export_only) {
        options.error = "--fps, --max-frames, --camera and --pixel-path apply to --export-frames";
    } else if (options.mode == Options::probe) {
        if (!have_max_seconds) options.error = "--probe-frames needs --max-seconds";
        else if (options.max_seconds > kMaxProbeSeconds) {
            options.error = "--max-seconds needs an integer between 1 and " + std::to_string(kMaxProbeSeconds);
        }
    } else if (have_max_seconds) {
        options.error = "--max-seconds applies to --probe-frames and --export-frames";
    }
    return options;
}

const char* usage_text() {
    return "sightcircuit-camera-helper: Arcturus camera SDK metadata probe and frame export; writes no file.\n"
           "  --describe                                  build, backend and SDK release facts; no connect\n"
           "  --hello [--timeout-ms N]                    connect, handshake and control state; reader stays off\n"
           "  --probe-frames N --max-seconds S [--interval-ms M] [--timeout-ms T]\n"
           "                                              metadata of up to N frame sets (1-50) within S seconds (1-10),\n"
           "                                              one pass per M ms (100-1000, default 200); no pixels are read\n"
           "  --export-frames --max-seconds S --max-frames N [--fps F] [--camera left|right|both]\n"
           "                 [--pixel-path auto|cpu_linear|gpu_convert] [--timeout-ms T]\n"
           "                                              BGR3 frames to the parent over stdout (camera export protocol 1),\n"
           "                                              at most F per second (1-10, default 5), S seconds (1-60) and\n"
           "                                              N frames (1-600); stdout must be a pipe; stderr carries JSON lines\n"
           "  --timeout-ms N                              connect timeout, 1-10000 (default 2000)\n"
           "--hello, --probe-frames and --export-frames ask for confirmation on the terminal and take a lock in\n"
           "$XDG_RUNTIME_DIR. Output of the metadata modes: NDJSON records on stdout (report_kind\n"
           "sightcircuit_camera_helper, schema 1).\n";
}

// ---- records -----------------------------------------------------------------

bool g_protocol_write_failed = false;

JsonObject record(const char* event) {
    JsonObject object;
    object.str("report_kind", kReportKind).integer("schema_version", kSchemaVersion).str("backend", kBackendName)
        .str("contract", kContract).str("event", event);
    return object;
}

void emit(const JsonObject& object) {
    const std::string line = object.done() + "\n";
    if (line.size() > kMaxLineBytes) {
        // Never emit a torn or oversized line; say what happened instead.
        const std::string replacement = record("outcome").str("call", "emit").str("outcome", "helper_error")
            .boolean("retryable", false).str("message", "record exceeded 8 KiB").done() + "\n";
        if (g_export_mode) write_fd(STDERR_FILENO, replacement);
        else if (!lifecycle::write_protocol(replacement)) g_protocol_write_failed = true;
        return;
    }
    if (g_export_mode) write_fd(STDERR_FILENO, line);
    else if (!lifecycle::write_protocol(line)) g_protocol_write_failed = true;
}

// ---- the export packet stream ------------------------------------------------------

void put_u32(std::string& out, uint32_t value) {
    for (int shift = 0; shift < 32; shift += 8) out += static_cast<char>((value >> shift) & 0xffu);
}

// A whole packet: prefix, header JSON, payload. Empty when the header is too large.
std::string packet_bytes(const std::string& header_json, const uint8_t* payload, std::size_t payload_bytes) {
    if (header_json.size() > export_protocol::kMaxHeaderBytes || payload_bytes > export_protocol::kMaxPayloadBytes) return "";
    std::string bytes;
    bytes.reserve(export_protocol::kPacketPrefixBytes + header_json.size() + payload_bytes);
    bytes.append(export_protocol::kMagic, sizeof export_protocol::kMagic);
    put_u32(bytes, export_protocol::kProtocolVersion);
    put_u32(bytes, static_cast<uint32_t>(header_json.size()));
    put_u32(bytes, static_cast<uint32_t>(payload_bytes));
    bytes += header_json;
    if (payload_bytes) bytes.append(reinterpret_cast<const char*>(payload), payload_bytes);
    return bytes;
}

// Writes to the protocol descriptor without lifecycle::write_protocol, whose
// torn-line newline has no place in a packet stream. SIGPIPE is ignored (main),
// so a vanished parent shows up here as a failed write.
bool write_packet_bytes(const std::string& bytes) {
    const char* data = bytes.data();
    std::size_t size = bytes.size();
    while (size) {
        const ssize_t count = ::write(lifecycle::protocol_fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        data += count;
        size -= static_cast<std::size_t>(count);
    }
    return true;
}

bool write_packet(const std::string& header_json, const uint8_t* payload, std::size_t payload_bytes) {
    const std::string bytes = packet_bytes(header_json, payload, payload_bytes);
    if (bytes.empty()) return false;
    return write_packet_bytes(bytes);
}

int exit_code_for(Outcome outcome) {
    switch (outcome) {
        case Outcome::ok:
        case Outcome::no_frame:
        case Outcome::not_ready:
        case Outcome::unavailable: return 0;
        case Outcome::permission_denied: return 2;
        case Outcome::camera_busy: return 3;
        case Outcome::protocol_mismatch: return 4;
        case Outcome::transport_lost:
        case Outcome::provider_unreachable:
        case Outcome::invalid_metadata:
        case Outcome::helper_error: return 1;
        case Outcome::cancelled: return kExitCancelled;
    }
    return 1;
}

const char* outcome_message(Outcome outcome) {
    switch (outcome) {
        case Outcome::permission_denied:
            return "Enable Developer camera access in Camera Switch, close Arcturus Vision Camera, then rerun.";
        case Outcome::camera_busy:
            return "Another client owns the cameras. Close Arcturus Vision Camera or the other client; "
                   "this helper never takes over.";
        default: return nullptr;
    }
}

// The export mode reads pixels and hands them to its parent; every other claim
// holds for every mode. The persistence value says where the output went.
void add_false_claims(JsonObject& object) {
    object.boolean("pixels_read", g_export_mode).boolean("images_saved", false).boolean("controls_written", false)
        .boolean("exclusive_control_requested", false).boolean("led_written", false)
        .raw("camera_data_fields", g_export_mode ? "[\"frame_pixels_to_parent\"]" : "[]")
        .str("persistence", g_export_mode ? "stdout_pipe_to_parent_only" : "stdout_only");
}

std::string controls_json(const ControlSummary& c) {
    JsonObject object;
    object.str("supported_controls", c.supported_controls).str("current_controls", c.current_controls)
        .str("supported_sensor_bit_depths", c.supported_sensor_bit_depths)
        .str("supported_auto_white_balance_modes", c.supported_auto_white_balance_modes)
        .str("supported_transfer_functions", c.supported_transfer_functions)
        .str("supported_color_primaries", c.supported_color_primaries)
        .str("supported_ycbcr_encodings", c.supported_ycbcr_encodings)
        .str("supported_resolution_modes", c.supported_resolution_modes)
        .unsigned_integer("red_recording_led_enabled", c.red_recording_led_enabled)
        .unsigned_integer("sensor_bit_depth", c.sensor_bit_depth)
        .str("auto_white_balance_mode", c.auto_white_balance_mode)
        .unsigned_integer("hdr_enabled", c.hdr_enabled).unsigned_integer("tone_mapping_enabled", c.tone_mapping_enabled)
        .str("transfer_function", c.transfer_function).str("color_primaries", c.color_primaries)
        .str("ycbcr_encoding", c.ycbcr_encoding).str("resolution_mode", c.resolution_mode)
        .real("brightness_ev", c.brightness_ev).unsigned_integer("auto_exposure_enabled", c.auto_exposure_enabled)
        .real("min_exposure_ms", c.min_exposure_ms).real("max_exposure_ms", c.max_exposure_ms)
        .real("min_gain", c.min_gain).real("max_gain", c.max_gain)
        .real("current_fps", c.current_fps).real("current_fps_rounded", c.current_fps_rounded())
        .str("digest", c.digest()).str("digest_input", c.canonical());
    return object.done();
}

std::string camera_json(const CameraMeta& camera) {
    JsonArray planes;
    for (uint32_t plane = 0; plane < camera.plane_count && plane < kMaxPlanes; ++plane) {
        planes.raw(JsonObject().unsigned_integer("offset", camera.planes[plane].offset)
                       .unsigned_integer("stride", camera.planes[plane].stride)
                       .unsigned_integer("size", camera.planes[plane].size).done());
    }
    JsonObject object;
    object.unsigned_integer("frame_id", camera.frame_id).integer("exposure_timestamp_ns", camera.exposure_timestamp_ns)
        .number("timestamp_seconds", camera.timestamp_seconds)
        .unsigned_integer("width", camera.width).unsigned_integer("height", camera.height)
        .str("drm_format", camera.drm_format).str("pixel_format", camera.pixel_format)
        .str("drm_modifier", camera.drm_modifier).unsigned_integer("plane_count", camera.plane_count)
        .raw("planes", planes.done())
        .boolean("texture_bounds_valid", camera.texture_bounds_valid)
        .boolean("intrinsics_valid", camera.intrinsics_valid).str("camera_model", camera.camera_model)
        .boolean("kb4_valid", camera.kb4_valid)
        .boolean("first_camera_from_camera_valid", camera.first_camera_from_camera_valid)
        .unsigned_integer("aux_json_size", camera.aux_json_size)
        .str("transfer", camera.transfer).str("primaries", camera.primaries).str("ycbcr_encoding", camera.ycbcr_encoding);
    return object.done();
}

// ---- the session -------------------------------------------------------------

struct Range {
    bool any = false;
    int64_t min = 0, max = 0;
    void add(int64_t value) {
        if (!any || value < min) min = value;
        if (!any || value > max) max = value;
        any = true;
    }
    std::string json() const {
        return any ? JsonObject().integer("min", min).integer("max", max).done() : "null";
    }
};

struct FormatKey {
    std::string pixel_format, drm_modifier, drm_format;
    uint32_t width = 0, height = 0, plane_count = 0;
    static FormatKey of(const CameraMeta& camera) {
        FormatKey key;
        key.pixel_format = camera.pixel_format;
        key.drm_modifier = camera.drm_modifier;
        key.drm_format = camera.drm_format;
        key.width = camera.width;
        key.height = camera.height;
        key.plane_count = camera.plane_count;
        return key;
    }
    // The first differing field, or empty.
    std::string differs_from(const FormatKey& other, std::string& from, std::string& to) const {
        if (pixel_format != other.pixel_format) { from = pixel_format; to = other.pixel_format; return "pixel_format"; }
        if (drm_modifier != other.drm_modifier) { from = drm_modifier; to = other.drm_modifier; return "drm_modifier"; }
        if (drm_format != other.drm_format) { from = drm_format; to = other.drm_format; return "drm_format"; }
        if (width != other.width) { from = std::to_string(width); to = std::to_string(other.width); return "width"; }
        if (height != other.height) { from = std::to_string(height); to = std::to_string(other.height); return "height"; }
        if (plane_count != other.plane_count) {
            from = std::to_string(plane_count); to = std::to_string(other.plane_count); return "plane_count";
        }
        return "";
    }
};

class Session {
public:
    Session(const Options& options, CameraBackend& backend)
        : options_(options), backend_(backend),
          watchdog_seconds_(static_cast<unsigned>(std::ceil((options.timeout_ms + kFrameTimeoutMs) / 1000.0)) + 1) {
        for (int i = 0; i < kOutcomeCount; ++i) counts_[i] = 0;
    }

    const char* mode_name() const {
        switch (options_.mode) {
            case Options::probe: return "probe_frames";
            case Options::export_frames: return "export_frames";
            default: return "hello";
        }
    }

    // The watchdog's pre-built message: a final record, or in the export mode an
    // "end" packet with reason "watchdog" and the counts at the time it was built.
    std::string watchdog_line() const {
        if (options_.mode == Options::export_frames) {
            return packet_bytes(end_header(export_protocol::kEndWatchdog, lifecycle::kWatchdogExitCode, false), nullptr, 0);
        }
        JsonObject object = record("final");
        object.str("mode", mode_name()).null("outcome").str("reason", "watchdog_expired")
            .integer("exit_code", lifecycle::kWatchdogExitCode).boolean("cleanup_ok", false)
            .str("reader_deactivated", "unknown");
        add_false_claims(object);
        return object.done() + "\n";
    }

    // Refused before any SDK call: a final record with the reason and the exit code (5 unless given).
    int refuse(const std::string& reason, int exit_code = kExitRefused, const std::string& detail = "") {
        reason_ = reason;
        detail_ = detail;
        emit_final(exit_code);
        return exit_code;
    }

    // The export mode's pixel path, opened before any SDK call when it was
    // named; "auto" opens the CPU path now and decides on the first frame set.
    bool open_pixel_path(std::string& error) {
        if (options_.pixel_path == export_protocol::kPixelPathGpuConvert) pixel_path_ = make_gpu_convert_path();
        else pixel_path_ = make_cpu_linear_path();
        return pixel_path_->open(error, pixel_path_facts_);
    }

    int run() {
        if (!step_connect()) return finish();
        if (options_.mode == Options::hello) {
            ControlSummary controls;
            if (step_controls("hello", controls)) {
                start_controls_ = controls;
                have_start_ = true;
            }
            return finish();
        }
        if (!step_controls("start", start_controls_)) return finish();
        have_start_ = true;
        if (check_stop()) return finish();
        if (!call_set_reader(true)) return finish();
        reader_active_ = reader_activated_ = true;
        if (options_.mode == Options::export_frames) export_loop();
        else loop();
        return finish();
    }

private:
    bool check_stop() {
        if (!stop_signal_.empty()) return true;
        const int signal = pending_stop();
        if (!signal) return false;
        stop_signal_ = signal_name(signal);
        outcome_ = Outcome::cancelled;
        return true;
    }

    template <class Call>
    Outcome sdk(Call call) {
        lifecycle::rearm_watchdog(watchdog_seconds_);
        const Outcome outcome = call();
        lifecycle::disarm_watchdog();
        ++counts_[static_cast<int>(outcome)];
        if (outcome == Outcome::transport_lost) session_lost_ = true;
        return outcome;
    }

    void emit_outcome(const char* call, Outcome outcome, bool have_pass) {
        const StatusDiagnostics& status = backend_.last_status();
        JsonObject object = record("outcome");
        object.str("call", call).str("outcome", outcome_name(outcome)).boolean("retryable", outcome_retryable(outcome))
            .optional_integer("pass", have_pass, passes_)
            .str("status_error", status.status_error).str("result", status.result)
            .integer("system_error", status.system_error).str("sdk_message", status.sdk_message)
            .integer("monotonic_ns", clock_ns(CLOCK_MONOTONIC));
        if (const char* message = outcome_message(outcome)) object.str("message", message);
        emit(object);
    }

    bool step_connect() {
        if (check_stop()) return false;
        const Outcome outcome = sdk([&] { return backend_.connect(options_.timeout_ms); });
        connect_attempted_ = true;
        if (outcome != Outcome::ok && outcome != Outcome::invalid_metadata) {
            emit_outcome("connect", outcome, false);
            outcome_ = outcome;
            return false;
        }
        connected_ = true;
        const HelloFacts& hello = backend_.hello();
        JsonObject object = record("hello");
        object.unsigned_integer("protocol_version", hello.protocol_version)
            .unsigned_integer("camera_count", hello.camera_count).str("offline_cal_source", hello.offline_cal_source)
            .unsigned_integer("offline_cal_size", hello.offline_cal_size)
            .boolean("offline_cal_size_within_bound", hello.offline_cal_size_within_bound);
        if (hello.offline_cal_sha256.empty()) object.null("offline_cal_sha256");
        else object.str("offline_cal_sha256", hello.offline_cal_sha256);
        emit(object);
        if (outcome == Outcome::invalid_metadata) {
            emit_outcome("connect", outcome, false);
            outcome_ = outcome;
            return false;
        }
        return true;
    }

    // The end read is part of cleanup, so a pending stop does not skip it.
    bool step_controls(const char* phase, ControlSummary& controls, bool stop_check = true) {
        if (stop_check && check_stop()) return false;
        const Outcome outcome = sdk([&] { return backend_.control_state(controls); });
        if (outcome != Outcome::ok) {
            emit_outcome("control_state", outcome, false);
            if (outcome_ == Outcome::ok) outcome_ = outcome;
            return false;
        }
        JsonObject object = record("controls");
        object.str("phase", phase).raw("controls", controls_json(controls));
        emit(object);
        return true;
    }

    bool call_set_reader(bool active) {
        const Outcome outcome = sdk([&] { return backend_.set_reader(active); });
        if (outcome != Outcome::ok) {
            emit_outcome(active ? "set_reader:true" : "set_reader:false", outcome, false);
            if (outcome_ == Outcome::ok) outcome_ = outcome;
            return false;
        }
        return true;
    }

    void loop() {
        const int64_t started = clock_ns(CLOCK_MONOTONIC);
        const int64_t deadline = started + static_cast<int64_t>(options_.max_seconds) * 1000000000ll;
        for (;;) {
            if (check_stop()) return;
            if (frame_sets_ >= options_.frame_sets) return;
            const int64_t pass_started = clock_ns(CLOCK_MONOTONIC);
            if (pass_started >= deadline) return;
            ++passes_;
            FrameSetMeta meta;
            const HostClocks before = HostClocks::sample();
            const Outcome outcome = sdk([&] { return backend_.acquire(kFrameTimeoutMs, meta); });
            const HostClocks after = HostClocks::sample();
            if (outcome == Outcome::ok) {
                ++frame_sets_;
                last_frame_set_ns_ = after.monotonic_ns;
                have_last_frame_set_ = true;
                have_first_retryable_after_ = false;
                account(meta, after);
                JsonObject object = record("frame_set");
                object.unsigned_integer("pass", passes_).unsigned_integer("sequence", meta.sequence)
                    .unsigned_integer("camera_count", meta.camera_count).boolean("pose_valid", meta.pose_valid)
                    .boolean("front_from_head_valid", meta.front_from_head_valid)
                    .boolean("front_from_avcam0_valid", meta.front_from_avcam0_valid)
                    .boolean("velocity_valid", meta.velocity_valid);
                JsonArray cameras;
                for (uint32_t camera = 0; camera < meta.camera_count && camera < kMaxCameras; ++camera) {
                    cameras.raw(camera_json(meta.cameras[camera]));
                }
                object.raw("cameras", cameras.done()).raw("host_clocks_before", before.json())
                    .raw("host_clocks_after", after.json());
                emit(object);
            } else if (outcome_retryable(outcome)) {
                emit_outcome("acquire", outcome, true);
                if (have_last_frame_set_ && !have_first_retryable_after_) {
                    first_retryable_after_ns_ = after.monotonic_ns;
                    have_first_retryable_after_ = true;
                }
            } else {
                emit_outcome("acquire", outcome, true);
                outcome_ = outcome;
                return;
            }
            if (frame_sets_ >= options_.frame_sets) return;
            int64_t next = pass_started + static_cast<int64_t>(options_.interval_ms) * 1000000ll;
            if (next > deadline) next = deadline;
            const int signal = wait_for_stop_until(next);
            if (signal) {
                stop_signal_ = signal_name(signal);
                outcome_ = Outcome::cancelled;
                return;
            }
        }
    }

    // ---- the export mode --------------------------------------------------------------

    // Releases the held frame set; a failure is transport_lost and ends the run.
    bool release_lease() {
        const Outcome outcome = sdk([&] { return backend_.release_lease(); });
        if (outcome != Outcome::ok) {
            ++release_failures_;
            emit_outcome("release", outcome, true);
            if (outcome_ == Outcome::ok) outcome_ = outcome;
            end_reason_ = export_protocol::kEndSdkError;
            return false;
        }
        return true;
    }

    void end_export(const char* reason, const std::string& detail = "") {
        if (end_reason_.empty()) end_reason_ = reason;
        if (!detail.empty() && detail_.empty()) detail_ = detail;
    }

    // "auto" chooses by the first frame set's modifier: linear stays on the CPU
    // path, anything else opens the GPU path. The chosen path is fixed for the run.
    bool settle_pixel_path(const FrameSetMeta& meta) {
        if (pixel_path_settled_) return true;
        pixel_path_settled_ = true;
        if (options_.pixel_path != "auto") return true;
        // The first selected camera's modifier; camera 0's when the selection is absent from the set.
        uint64_t modifier = meta.camera_count ? meta.cameras[0].drm_modifier_value : 0;
        for (uint32_t camera = 0; camera < meta.camera_count && camera < kMaxCameras; ++camera) {
            if (options_.exports_camera(camera)) { modifier = meta.cameras[camera].drm_modifier_value; break; }
        }
        if (modifier_is_linear(modifier)) return true;
        std::string error;
        pixel_path_ = make_gpu_convert_path();
        lifecycle::rearm_watchdog(kConvertWatchdogSeconds);
        const bool opened = pixel_path_->open(error, pixel_path_facts_);
        lifecycle::disarm_watchdog();
        if (!opened) {
            end_export(export_protocol::kEndPixelPathError, "gpu_convert_open_failed:" + error);
            return false;
        }
        return true;
    }

    void export_loop() {
        export_started_ns_ = clock_ns(CLOCK_MONOTONIC);
        // A named pixel path is settled already, so the ready packet goes out
        // before the first acquire; "auto" writes it once the first frame set
        // has shown its modifier.
        if (options_.pixel_path != "auto") {
            pixel_path_settled_ = true;
            if (!write_ready()) return;
        }
        const int64_t deadline = export_started_ns_ + static_cast<int64_t>(options_.max_seconds) * 1000000000ll;
        const int64_t interval_ns = 1000000000ll / static_cast<int64_t>(options_.fps);
        int64_t next_due_ns = export_started_ns_;
        int64_t last_frame_set_ns = export_started_ns_;
        Outcome last_retryable = Outcome::ok;
        bool any_frame_set = false;
        for (;;) {
            if (check_stop()) { end_export(export_protocol::kEndStopSignal); return; }
            if (clock_ns(CLOCK_MONOTONIC) >= deadline) { end_export(export_protocol::kEndMaxSeconds); return; }
            if (exported_ >= options_.max_frames) { end_export(export_protocol::kEndMaxFrames); return; }
            ++passes_;
            FrameSetLease lease;
            arm_export_watchdog();
            const Outcome outcome = sdk([&] { return backend_.acquire_lease(kExportFrameTimeoutMs, lease); });
            const HostClocks clocks = HostClocks::sample();
            if (outcome_retryable(outcome)) {
                ++acquire_failures_;
                last_retryable = outcome;
                emit_outcome("acquire", outcome, true);
                if (clocks.monotonic_ns - last_frame_set_ns > kPresenceGapNs) {
                    // Frames stopped: without a reported reason that is the headset
                    // coming off (the only observable the SDK offers); a reported
                    // not-ready or unavailable is the service's standby. Before the
                    // first frame set the service never served, which is standby too.
                    const bool removed = any_frame_set && last_retryable == Outcome::no_frame;
                    end_export(removed ? export_protocol::kEndHeadsetRemoved : export_protocol::kEndStandby,
                               std::string("no_frame_set_for_ns:") + std::to_string(clocks.monotonic_ns - last_frame_set_ns));
                    return;
                }
                continue;
            }
            if (outcome == Outcome::invalid_metadata) {
                ++acquire_failures_;
                emit_outcome("acquire", outcome, true);
                release_lease();
                outcome_ = outcome;
                end_export(export_protocol::kEndSdkError, "invalid_metadata");
                return;
            }
            if (outcome != Outcome::ok) {
                ++acquire_failures_;
                emit_outcome("acquire", outcome, true);
                outcome_ = outcome;
                end_export(export_protocol::kEndSdkError, outcome_name(outcome));
                return;
            }
            // A frame set is held from here until release_lease().
            any_frame_set = true;
            last_frame_set_ns = clocks.monotonic_ns;
            ++frame_sets_;
            account(lease.meta, clocks);
            if (!settle_pixel_path(lease.meta)) { release_lease(); return; }
            if (!ready_written_) {
                if (!write_ready()) { release_lease(); return; }
            }
            if (options_.highest_camera_index() >= lease.meta.camera_count) {
                release_lease();
                end_export(export_protocol::kEndSdkError, "camera_missing:" + options_.camera + " needs " +
                                                               std::to_string(options_.highest_camera_index() + 1) +
                                                               " cameras, the frame set has " +
                                                               std::to_string(lease.meta.camera_count));
                return;
            }
            if (clocks.monotonic_ns < next_due_ns) {
                ++dropped_;
                ++dropped_since_last_;
                if (!release_lease()) return;
                continue;
            }
            next_due_ns = std::max(next_due_ns + interval_ns, clocks.monotonic_ns);
            // Convert every selected camera while the set is held, release, then write.
            pending_in_set_ = 0;
            std::vector<std::string> headers;
            std::vector<BgrImage> images;
            for (uint32_t camera = 0; camera < lease.meta.camera_count && camera < kMaxCameras; ++camera) {
                if (!options_.exports_camera(camera)) continue;
                if (exported_ + headers.size() >= options_.max_frames) break;  // the bound counts frames, not sets
                SourceFrame source;
                std::string error;
                if (!source_frame_from_meta(lease.meta.cameras[camera], lease.camera_fd[camera], source, error)) {
                    release_lease();
                    end_export(export_protocol::kEndPixelPathError, "camera_" + std::to_string(camera) + ":" + error);
                    return;
                }
                BgrImage image;
                lifecycle::rearm_watchdog(kConvertWatchdogSeconds);
                const bool converted = pixel_path_->convert(source, image, error);
                lifecycle::disarm_watchdog();
                if (!converted) {
                    release_lease();
                    end_export(export_protocol::kEndPixelPathError, "camera_" + std::to_string(camera) + ":" + error);
                    return;
                }
                headers.push_back(frame_header(lease.meta, camera, clocks, image));
                images.push_back(std::move(image));
            }
            if (!release_lease()) return;
            for (std::size_t i = 0; i < headers.size(); ++i) {
                if (!write_packet(headers[i], images[i].pixels.data(), images[i].pixels.size())) {
                    protocol_lost_ = true;
                    note("sightcircuit-camera-helper: the protocol stream failed; the parent is gone");
                    end_export(export_protocol::kEndSdkError, "protocol_write_failed");
                    return;
                }
                ++exported_;
            }
            dropped_since_last_ = 0;
        }
    }

    void arm_export_watchdog() {
        // The message carries the counts so far; if a call hangs, the end packet says where it stopped.
        const std::string message = watchdog_line();
        if (!message.empty()) lifecycle::set_watchdog(message, watchdog_seconds_);
        lifecycle::disarm_watchdog();
    }

    std::string build_json() const {
        JsonObject object;
        if (kBuildCommit) object.str("build_commit", kBuildCommit);
        else object.null("build_commit").str("build_commit_reason", kBuildCommitReason);
        if (kBuildDirty < 0) object.null("build_dirty");
        else object.boolean("build_dirty", kBuildDirty != 0);
        if (kSdkSourceRevision && kSdkAgreementVersion) {
            object.raw("sdk_release", JsonObject().str("source_revision", kSdkSourceRevision)
                                          .str("agreement_version", kSdkAgreementVersion).done());
        } else {
            object.null("sdk_release");
        }
        object.str("compiler", __VERSION__);
        return object.done();
    }

    std::string camera_selection_json() const {
        JsonArray indices;
        for (uint32_t camera = 0; camera < kMaxCameras; ++camera) {
            if (options_.exports_camera(camera)) indices.raw(std::to_string(camera));
        }
        // avCam0 is taken as the left camera until the device's transforms say otherwise.
        return JsonObject().str("requested", options_.camera).raw("camera_indices", indices.done())
            .str("index_mapping", "assumed_avcam0_left").done();
    }

    bool write_ready() {
        const HelloFacts& hello = backend_.hello();
        JsonObject header;
        header.str("kind", export_protocol::kKindReady).integer("protocol", export_protocol::kProtocolVersion)
            .str("backend", kBackendName).str("pixel_path", pixel_path_->name()).raw("build", build_json())
            .raw("delivered_format", JsonObject().str("fourcc", export_protocol::kDeliveredFourcc).done())
            .raw("camera", camera_selection_json())
            .raw("limits", JsonObject().unsigned_integer("fps", options_.fps).unsigned_integer("max_seconds", options_.max_seconds)
                               .unsigned_integer("max_frames", options_.max_frames).done())
            .unsigned_integer("frame_timeout_ms", kExportFrameTimeoutMs).integer("presence_gap_ns", kPresenceGapNs)
            .raw("pixel_path_facts", pixel_path_facts_.empty() ? "{}" : pixel_path_facts_)
            .unsigned_integer("hello_camera_count", hello.camera_count)
            .str("controls_digest_start", have_start_ ? start_controls_.digest() : "")
            .integer("monotonic_ns", clock_ns(CLOCK_MONOTONIC)).integer("realtime_ns", clock_ns(CLOCK_REALTIME));
        if (!write_packet(header.done(), nullptr, 0)) {
            protocol_lost_ = true;
            end_export(export_protocol::kEndSdkError, "protocol_write_failed");
            return false;
        }
        ready_written_ = true;
        return true;
    }

    std::string frame_header(const FrameSetMeta& meta, uint32_t camera, const HostClocks& clocks, const BgrImage& image) const {
        const CameraMeta& source = meta.cameras[camera];
        JsonArray planes;
        for (uint32_t plane = 0; plane < source.plane_count && plane < kMaxPlanes; ++plane) {
            planes.raw(JsonObject().unsigned_integer("offset", source.planes[plane].offset)
                           .unsigned_integer("stride", source.planes[plane].stride)
                           .unsigned_integer("size", source.planes[plane].size).done());
        }
        JsonObject acquisition;
        acquisition.str("drm_fourcc", source.drm_format).str("modifier", source.drm_modifier)
            .unsigned_integer("plane_count", source.plane_count).raw("planes", planes.done())
            .str("pixel_format", source.pixel_format).str("encoding", source.ycbcr_encoding)
            .str("transfer", source.transfer).str("primaries", source.primaries);
        JsonObject header;
        header.str("kind", export_protocol::kKindFrame).unsigned_integer("sequence", exported_ + 1 + pending_in_set_)
            .unsigned_integer("frame_id", source.frame_id).unsigned_integer("frame_set_sequence", meta.sequence)
            .unsigned_integer("camera_index", camera).unsigned_integer("camera_count", meta.camera_count)
            .integer("exposure_ns", source.exposure_timestamp_ns).integer("monotonic_ns", clocks.monotonic_ns)
            .integer("realtime_ns", clocks.realtime_ns).unsigned_integer("width", image.width)
            .unsigned_integer("height", image.height).str("fourcc", export_protocol::kDeliveredFourcc)
            .unsigned_integer("stride_bytes", image.stride_bytes).unsigned_integer("buffer_bytes", image.pixels.size())
            .str("derivation", pixel_path_->derivation()).raw("acquisition_format", acquisition.done())
            .unsigned_integer("dropped_since_last", dropped_since_last_);
        ++pending_in_set_;
        return header.done();
    }

    // The end header; the watchdog's copy is built before each call with the counts so far.
    std::string end_header(const std::string& reason, int exit_code, bool cleanup_ok) const {
        JsonObject counts;
        counts.unsigned_integer("exported", exported_).unsigned_integer("dropped", dropped_)
            .unsigned_integer("acquire_failures", acquire_failures_).unsigned_integer("release_failures", release_failures_)
            .unsigned_integer("frame_sets", frame_sets_).unsigned_integer("passes", passes_);
        JsonObject header;
        header.str("kind", export_protocol::kKindEnd).str("reason", reason).raw("counts", counts.done())
            .integer("elapsed_ns", export_started_ns_ ? clock_ns(CLOCK_MONOTONIC) - export_started_ns_ : 0)
            .integer("exit_code", exit_code).boolean("cleanup_ok", cleanup_ok)
            .str("outcome", outcome_name(outcome_));
        if (detail_.empty()) header.null("detail");
        else header.str("detail", detail_);
        if (have_start_) header.str("controls_digest_start", start_controls_.digest());
        else header.null("controls_digest_start");
        if (have_end_) header.str("controls_digest_end", end_controls_.digest());
        else header.null("controls_digest_end");
        if (have_start_ && have_end_) header.boolean("controls_changed_during_run", !start_controls_.changed_fields(end_controls_).empty());
        else header.null("controls_changed_during_run");
        header.raw("pixel_path_notes", pixel_path_ ? pixel_path_->notes_json() : "{}");
        return header.done();
    }

    void account(const FrameSetMeta& meta, const HostClocks& after) {
        if (have_last_sequence_ && meta.sequence != last_sequence_ + 1) {
            ++sequence_gaps_;
            if (sequence_gaps_ == 1) {
                first_gap_ = JsonObject().unsigned_integer("pass", passes_).unsigned_integer("from", last_sequence_)
                                 .unsigned_integer("to", meta.sequence).done();
            }
        }
        last_sequence_ = meta.sequence;
        have_last_sequence_ = true;
        for (uint32_t camera = 0; camera < meta.camera_count && camera < kMaxCameras; ++camera) {
            const CameraMeta& frame = meta.cameras[camera];
            const int64_t timestamp_ns = static_cast<int64_t>(frame.timestamp_seconds * 1e9);
            const int64_t host[3] = {after.monotonic_ns, after.boottime_ns, after.realtime_ns};
            for (int clock = 0; clock < 3; ++clock) {
                exposure_offsets_[clock].add(host[clock] - frame.exposure_timestamp_ns);
                timestamp_offsets_[clock].add(host[clock] - timestamp_ns);
            }
            const FormatKey key = FormatKey::of(frame);
            if (!have_format_[camera]) {
                first_format_[camera] = key;
                have_format_[camera] = true;
            } else if (!format_changed_) {
                std::string from, to;
                const std::string field = first_format_[camera].differs_from(key, from, to);
                if (!field.empty()) {
                    format_changed_ = true;
                    first_format_change_ = JsonObject().unsigned_integer("pass", passes_)
                                               .unsigned_integer("camera", camera).str("field", field)
                                               .str("from", from).str("to", to).done();
                }
            }
        }
    }

    // Deactivate the reader, read the end control state, disconnect, report.
    int finish() {
        if (session_lost_) {
            reader_deactivated_ = reader_active_ ? "session_closed" : "true";
            if (reader_active_) cleanup_ok_ = false;
        } else {
            if (reader_active_) {
                if (call_set_reader(false)) {
                    reader_deactivated_ = "true";
                } else {
                    reader_deactivated_ = session_lost_ ? "session_closed" : "deactivation_failed";
                    cleanup_ok_ = false;
                }
                reader_active_ = false;
            }
            if ((options_.mode == Options::probe || options_.mode == Options::export_frames) && have_start_ &&
                !session_lost_ && passes_ > 0) {
                have_end_ = step_controls("end", end_controls_, false);
            }
        }
        if (connect_attempted_) {
            lifecycle::rearm_watchdog(watchdog_seconds_);
            backend_.disconnect();
            lifecycle::disarm_watchdog();
        }
        connected_ = false;
        int exit_code = exit_code_for(outcome_);
        if (options_.mode == Options::export_frames) {
            if (end_reason_.empty()) end_reason_ = export_protocol::kEndSdkError;
            if (end_reason_ == export_protocol::kEndStopSignal) exit_code = kExitCancelled;
            else if (end_reason_ == export_protocol::kEndPixelPathError) exit_code = 1;
            else if (end_reason_ == export_protocol::kEndSdkError && exit_code == 0) exit_code = 1;
            else if (end_reason_ != export_protocol::kEndSdkError) exit_code = 0;
            if (protocol_lost_) exit_code = 1;
            if (!cleanup_ok_ && exit_code == 0) exit_code = 1;
            reason_ = end_reason_;
            if (ready_written_ && !protocol_lost_) {
                if (!write_packet(end_header(end_reason_, exit_code, cleanup_ok_), nullptr, 0)) exit_code = 1;
            }
        } else {
            if (reason_.empty()) reason_ = outcome_name(outcome_);
            if (exit_code == 0 && g_protocol_write_failed) exit_code = 1;
        }
        emit_final(exit_code);
        return exit_code;
    }

    void emit_final(int exit_code) {
        JsonObject object = record("final");
        object.str("mode", options_.mode == Options::none ? "none" : mode_name());
        if (connect_attempted_) object.str("outcome", outcome_name(outcome_));
        else object.null("outcome");
        object.str("reason", reason_).integer("exit_code", exit_code).boolean("cleanup_ok", cleanup_ok_);
        if (detail_.empty()) object.null("detail");
        else object.str("detail", detail_);
        if (stop_signal_.empty()) object.null("stop_signal");
        else object.str("stop_signal", stop_signal_);
        if (options_.mode == Options::export_frames) {
            object.unsigned_integer("exported", exported_).unsigned_integer("dropped", dropped_)
                .unsigned_integer("acquire_failures", acquire_failures_).unsigned_integer("release_failures", release_failures_)
                .boolean("ready_written", ready_written_).str("pixel_path", pixel_path_ ? pixel_path_->name() : "");
        }
        JsonObject counts;
        for (int i = 0; i < kOutcomeCount; ++i) counts.unsigned_integer(outcome_name(static_cast<Outcome>(i)), counts_[i]);
        object.raw("outcome_counts", counts.done()).unsigned_integer("passes", passes_)
            .unsigned_integer("frame_sets", frame_sets_);
        object.raw("bounds", JsonObject().unsigned_integer("frame_sets", options_.frame_sets)
                                 .unsigned_integer("max_seconds", options_.max_seconds)
                                 .unsigned_integer("interval_ms", options_.interval_ms)
                                 .unsigned_integer("connect_timeout_ms", options_.timeout_ms)
                                 .unsigned_integer("frame_timeout_ms", kFrameTimeoutMs).done());
        object.raw("sequence_gaps", JsonObject().unsigned_integer("count", sequence_gaps_)
                                        .raw("first", first_gap_.empty() ? "null" : first_gap_).done());
        object.boolean("format_changed", format_changed_)
            .raw("first_format_change", first_format_change_.empty() ? "null" : first_format_change_)
            .optional_integer("last_frame_set_monotonic_ns", have_last_frame_set_, last_frame_set_ns_)
            .optional_integer("first_retryable_after_last_frame_set_monotonic_ns", have_first_retryable_after_,
                              first_retryable_after_ns_);
        static const char* const clock_names[3] = {"monotonic", "boottime", "realtime"};
        JsonObject offsets;
        for (int clock = 0; clock < 3; ++clock) {
            offsets.raw(clock_names[clock], JsonObject().raw("after_minus_exposure_timestamp_ns", exposure_offsets_[clock].json())
                                                .raw("after_minus_timestamp_seconds_ns", timestamp_offsets_[clock].json())
                                                .done());
        }
        object.raw("clock_offsets", offsets.done());
        if (have_start_ && have_end_) {
            const std::vector<std::string> changed = start_controls_.changed_fields(end_controls_);
            JsonArray fields;
            for (const std::string& name : changed) fields.str(name);
            object.boolean("controls_changed_during_run", !changed.empty()).raw("controls_changed_fields", fields.done());
        } else {
            object.null("controls_changed_during_run").raw("controls_changed_fields", "[]");
        }
        if (have_start_) object.str("controls_digest_start", start_controls_.digest());
        else object.null("controls_digest_start");
        if (have_end_) object.str("controls_digest_end", end_controls_.digest());
        else object.null("controls_digest_end");
        add_false_claims(object);
        object.boolean("reader_activated", reader_activated_);
        if (reader_deactivated_ == "true") object.boolean("reader_deactivated", true);
        else object.str("reader_deactivated", reader_deactivated_);
        if (const std::vector<std::string>* calls = backend_.call_log()) {
            JsonArray array;
            for (const std::string& call : *calls) array.str(call);
            object.raw("fake_calls", array.done());
        }
        emit(object);
    }

    const Options& options_;
    CameraBackend& backend_;
    const unsigned watchdog_seconds_;
    unsigned counts_[kOutcomeCount];
    Outcome outcome_ = Outcome::ok;
    std::string reason_;
    std::string stop_signal_;
    bool connect_attempted_ = false, connected_ = false, reader_active_ = false, reader_activated_ = false;
    bool session_lost_ = false;
    bool cleanup_ok_ = true;
    std::string reader_deactivated_ = "true";
    unsigned passes_ = 0, frame_sets_ = 0;
    ControlSummary start_controls_, end_controls_;
    bool have_start_ = false, have_end_ = false;
    bool have_last_sequence_ = false;
    uint64_t last_sequence_ = 0;
    unsigned sequence_gaps_ = 0;
    std::string first_gap_;
    bool format_changed_ = false;
    std::string first_format_change_;
    bool have_format_[kMaxCameras] = {false, false};
    FormatKey first_format_[kMaxCameras];
    bool have_last_frame_set_ = false, have_first_retryable_after_ = false;
    int64_t last_frame_set_ns_ = 0, first_retryable_after_ns_ = 0;
    Range exposure_offsets_[3], timestamp_offsets_[3];
    // The export mode.
    std::unique_ptr<PixelPath> pixel_path_;
    std::string pixel_path_facts_;
    bool pixel_path_settled_ = false;
    std::string end_reason_, detail_;
    bool ready_written_ = false, protocol_lost_ = false;
    int64_t export_started_ns_ = 0;
    unsigned exported_ = 0, dropped_ = 0, dropped_since_last_ = 0, acquire_failures_ = 0, release_failures_ = 0;
    mutable unsigned pending_in_set_ = 0;
};

// ---- process setup, refusals and modes ----------------------------------------

int emit_invalid_arguments(const std::string& error) {
    JsonObject object = record("final");
    object.str("mode", "none").null("outcome").str("reason", "invalid_arguments").str("detail", error)
        .integer("exit_code", kExitInvalidArguments).boolean("cleanup_ok", true);
    add_false_claims(object);
    emit(object);
    note("sightcircuit-camera-helper: " + error);
    if (g_export_mode) note(usage_text());
    else write_fd(STDERR_FILENO, usage_text());
    return kExitInvalidArguments;
}

int run_describe() {
    JsonObject object = record("describe");
    if (kBuildCommit) object.str("build_commit", kBuildCommit);
    else object.null("build_commit").str("build_commit_reason", kBuildCommitReason);
    if (kBuildDirty < 0) object.null("build_dirty");
    else object.boolean("build_dirty", kBuildDirty != 0);
    if (kSdkSourceRevision && kSdkAgreementVersion) {
        object.raw("sdk_release", JsonObject().str("source_revision", kSdkSourceRevision)
                                      .str("agreement_version", kSdkAgreementVersion).done());
    } else {
        object.null("sdk_release");
    }
    object.str("compiler", __VERSION__);
    add_false_claims(object);
    emit(object);
    return g_protocol_write_failed ? 1 : 0;
}

std::string notice_for(const Options& options) {
    if (options.mode == Options::export_frames) {
        const std::string cameras = options.camera == "both" ? "both passthrough cameras" :
                                    options.camera == "left" ? "the left passthrough camera (avCam0)" :
                                                               "the right passthrough camera (avCam1)";
        return "sightcircuit-camera-helper --export-frames: activates this client's frame reader for both passthrough "
               "cameras and delivers converted BGR images of " + cameras + " to the parent process over stdout, at most " +
               std::to_string(options.fps) + " per second, for at most " + std::to_string(options.max_seconds) +
               " seconds or " + std::to_string(options.max_frames) + " frames. This helper saves nothing and writes no "
               "file; what the parent does with the images, and anything you redirect, falls under the owner's consent "
               "decision (licence section 3.2). Export stops when frames stop (the headset removed) or the camera "
               "service reports standby. The red LED is left untouched and no exclusive control is requested. Watch "
               "passthrough during the run and for 30 seconds afterwards.";
    }
    if (options.mode == Options::hello) {
        return "sightcircuit-camera-helper --hello: connects to the camera service; the reader stays off; no frames "
               "are read; watch passthrough during the run and for 30 seconds afterwards; this helper writes no file.";
    }
    return "sightcircuit-camera-helper --probe-frames: activates this client's frame reader for both passthrough "
           "cameras (avCam0 and avCam1) and reads the metadata of up to " + std::to_string(options.frame_sets) +
           " frame sets within " + std::to_string(options.max_seconds) + " seconds, one pass every " +
           std::to_string(options.interval_ms) + " ms. Metadata only; no images are read; this helper writes no "
           "file; anything you redirect or publish falls under the owner's consent decision (licence section 3.2); "
           "if you remove the headset during the run, metadata reading continues until the time bound. Watch "
           "passthrough during the run and for 30 seconds afterwards.";
}

// The lock keeps one helper per runtime directory; never /tmp.
int take_lock(std::string& reason) {
    const char* directory = std::getenv("XDG_RUNTIME_DIR");
    if (!directory || !*directory) {
        reason = "runtime_dir_missing";
        return -1;
    }
    const std::string path = std::string(directory) + "/sightcircuit-camera-helper.lock";
    const int fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) {
        reason = "lock_open_failed";
        return -1;
    }
    if (::flock(fd, LOCK_EX | LOCK_NB) != 0) {
        reason = errno == EWOULDBLOCK ? "helper_already_running" : "lock_failed";
        ::close(fd);
        return -1;
    }
    return fd;
}

// The answer is read from stdin only when it is a terminal; the notice goes to
// the terminal (or stderr), never to the protocol stream. Nothing skips this.
bool confirm(const std::string& notice, std::string& reason) {
    if (!::isatty(STDIN_FILENO)) {
        reason = "confirmation_required";
        note(notice);
        note("sightcircuit-camera-helper: stdin is not a terminal; confirmation is required and cannot be given.");
        return false;
    }
    const int tty = ::open("/dev/tty", O_RDWR | O_NOCTTY | O_CLOEXEC);
    // Without a controlling terminal the notice goes to stderr: as a JSON line in
    // the export mode, where stderr carries nothing else.
    if (tty < 0 && g_export_mode) note(notice + " Type yes to continue.");
    else write_fd(tty >= 0 ? tty : STDERR_FILENO, notice + "\nType yes to continue: ");
    std::string answer;
    for (;;) {
        char c = 0;
        const ssize_t count = ::read(STDIN_FILENO, &c, 1);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0 || c == '\n') break;
        if (answer.size() < 64) answer += c;
    }
    if (tty >= 0) ::close(tty);
    while (!answer.empty() && (answer.back() == '\r' || answer.back() == ' ' || answer.back() == '\t')) answer.pop_back();
    if (answer != "yes") {
        reason = "confirmation_declined";
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (!lifecycle::isolate_stdout()) {
        note("sightcircuit-camera-helper: cannot isolate stdout for the protocol stream");
        return 1;
    }
    ::prctl(PR_SET_PDEATHSIG, SIGTERM);
    // A parent that closed the protocol pipe must not end the helper by SIGPIPE before its
    // final record: the write then fails with EPIPE, which emit() records and the exit code shows.
    std::signal(SIGPIPE, SIG_IGN);
    rlimit no_core{};
    no_core.rlim_cur = 0;
    no_core.rlim_max = 0;
    ::setrlimit(RLIMIT_CORE, &no_core);

    const Options options = parse_arguments(argc, argv);
    if (options.help) {
        write_fd(STDERR_FILENO, usage_text());
        return 0;
    }
    if (!options.error.empty()) return emit_invalid_arguments(options.error);
    if (options.mode == Options::describe) return run_describe();

    std::unique_ptr<CameraBackend> backend = make_camera_backend(options.fake_scenario);
    if (!backend) return emit_invalid_arguments("unknown fake scenario: " + options.fake_scenario);
    Session session(options, *backend);

    std::string reason;
    if (options.mode == Options::export_frames) {
        // Pixels leave only through a pipe or socket to the parent: never a file, never a terminal.
        struct stat protocol_stat{};
        if (::fstat(lifecycle::protocol_fd, &protocol_stat) != 0 ||
            !(S_ISFIFO(protocol_stat.st_mode) || S_ISSOCK(protocol_stat.st_mode))) {
            note("sightcircuit-camera-helper: refused: stdout_not_a_pipe (--export-frames writes packets to its parent only)");
            return session.refuse("stdout_not_a_pipe");
        }
    }
    const int lock_fd = take_lock(reason);
    if (lock_fd < 0) {
        note("sightcircuit-camera-helper: refused: " + reason);
        return session.refuse(reason);
    }
    if (!confirm(notice_for(options), reason)) return session.refuse(reason);
    if (options.mode == Options::export_frames) {
        std::string error;
        if (!session.open_pixel_path(error)) {
            note("sightcircuit-camera-helper: refused: pixel_path_unavailable: " + error);
            return session.refuse("pixel_path_unavailable", 1, error);
        }
    }

    // From here on a stop signal is collected between SDK calls, never delivered
    // during one: the mask is set before the first SDK call so library threads
    // inherit it, and no handler is installed for these signals.
    const sigset_t stops = stop_signals();
    pthread_sigmask(SIG_BLOCK, &stops, nullptr);
    // SIGALRM stays deliverable: the watchdog ends a hung call with a pre-built final record.
    const std::string watchdog_line = session.watchdog_line();
    if (watchdog_line.empty() || !lifecycle::install_watchdog() || !lifecycle::set_watchdog(watchdog_line, 1)) {
        note("sightcircuit-camera-helper: cannot install the watchdog");
        return session.refuse("watchdog_unavailable");
    }
    lifecycle::disarm_watchdog();  // set_watchdog armed it; each SDK call re-arms its own.
    const int exit_code = session.run();
    ::close(lock_fd);
    return exit_code;
}
