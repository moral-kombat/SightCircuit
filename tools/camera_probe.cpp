// Public OpenVR capability probe. Runtime calls require explicit opt-in because
// on-device testing has shown that this probe interrupts visible passthrough.
#include <openvr.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iostream>
#include <new>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::uint64_t kMaxBufferBytes = 64ULL * 1024 * 1024;
constexpr auto kCaptureBudget = std::chrono::seconds(3);
constexpr auto kPollInterval = std::chrono::milliseconds(16);
constexpr int kMaxCalibrationCameras = 4;
constexpr const char* kStreamOptIn = "--allow-passthrough-interruption";
constexpr const char* kPassthroughWarning =
    "This probe has interrupted Steam Frame passthrough without receiving frames. "
    "Do not repeat it on the unchanged tested runtime.";

// A small JSON value/writer keeps the probe independent of third-party libraries.
class Json {
public:
    enum class Kind { Null, Boolean, Number, String, Object, Array };
    using Members = std::vector<std::pair<std::string, Json>>;

    Json() = default;
    Json(const char* value) : kind_(Kind::String), text_(value ? value : "unknown") {}
    Json(std::string value) : kind_(Kind::String), text_(std::move(value)) {}
    Json(bool value) : kind_(Kind::Boolean), text_(value ? "true" : "false") {}

    static Json number(std::int64_t value) {
        Json result;
        result.kind_ = Kind::Number;
        result.text_ = std::to_string(value);
        return result;
    }

    static Json object(Members members = {}) {
        Json result;
        result.kind_ = Kind::Object;
        result.members_ = std::move(members);
        return result;
    }

    static Json array() {
        Json result;
        result.kind_ = Kind::Array;
        return result;
    }

    Json& set(const std::string& key, Json value) {
        for (auto& member : members_) {
            if (member.first == key) {
                member.second = std::move(value);
                return *this;
            }
        }
        members_.emplace_back(key, std::move(value));
        return *this;
    }

    void append(Json value) { items_.push_back(std::move(value)); }

    void write(std::ostream& out) const {
        switch (kind_) {
        case Kind::Null: out << "null"; break;
        case Kind::Boolean:
        case Kind::Number: out << text_; break;
        case Kind::String: quote(out, text_); break;
        case Kind::Object: {
            out << '{';
            bool first = true;
            for (const auto& member : members_) {
                if (!first) out << ',';
                first = false;
                quote(out, member.first);
                out << ':';
                member.second.write(out);
            }
            out << '}';
            break;
        }
        case Kind::Array: {
            out << '[';
            bool first = true;
            for (const auto& item : items_) {
                if (!first) out << ',';
                first = false;
                item.write(out);
            }
            out << ']';
            break;
        }
        }
    }

private:
    static void quote(std::ostream& out, const std::string& value) {
        constexpr char hex[] = "0123456789abcdef";
        out << '"';
        for (unsigned char ch : value) {
            if (ch == '"' || ch == '\\') {
                out << '\\' << static_cast<char>(ch);
            } else if (ch < 0x20 || ch >= 0x7f) {
                out << "\\u00" << hex[ch >> 4] << hex[ch & 15];
            } else {
                out << static_cast<char>(ch);
            }
        }
        out << '"';
    }

    Kind kind_ = Kind::Null;
    std::string text_;
    Members members_;
    std::vector<Json> items_;
};

class OpenVRSession {
public:
    explicit OpenVRSession(bool active) : active_(active) {}
    OpenVRSession(const OpenVRSession&) = delete;
    OpenVRSession& operator=(const OpenVRSession&) = delete;
    ~OpenVRSession() { if (active_) vr::VR_Shutdown(); }

private:
    bool active_;
};

class CameraStream {
public:
    explicit CameraStream(vr::IVRTrackedCamera* camera) : camera_(camera) {}
    CameraStream(const CameraStream&) = delete;
    CameraStream& operator=(const CameraStream&) = delete;
    ~CameraStream() {
        if (handle != INVALID_TRACKED_CAMERA_HANDLE) {
            camera_->ReleaseVideoStreamingService(handle);
        }
    }

    vr::EVRTrackedCameraError release() {
        if (handle == INVALID_TRACKED_CAMERA_HANDLE) return vr::VRTrackedCameraError_None;
        const auto held = handle;
        handle = INVALID_TRACKED_CAMERA_HANDLE;
        return camera_->ReleaseVideoStreamingService(held);
    }

    vr::TrackedCameraHandle_t handle = INVALID_TRACKED_CAMERA_HANDLE;

private:
    vr::IVRTrackedCamera* camera_;
};

Json camera_error(vr::IVRTrackedCamera* camera, vr::EVRTrackedCameraError error) {
    return Json::object({
        {"status", error == vr::VRTrackedCameraError_None ? "ok" : "error"},
        {"error_name", camera->GetCameraErrorNameFromEnum(error)},
        {"error_code", Json::number(static_cast<int>(error))},
    });
}

Json property(vr::IVRSystem* system, vr::ETrackedDeviceProperty name, int* value) {
    vr::ETrackedPropertyError error = vr::TrackedProp_Success;
    const auto result = system->GetInt32TrackedDeviceProperty(
        vr::k_unTrackedDeviceIndex_Hmd, name, &error);
    Json report = Json::object({
        {"status", error == vr::TrackedProp_Success ? "ok" : "error"},
        {"error_name", system->GetPropErrorNameFromEnum(error)},
        {"error_code", Json::number(static_cast<int>(error))},
        {"value", error == vr::TrackedProp_Success ? Json::number(result) : Json()},
    });
    if (error == vr::TrackedProp_Success && value) *value = result;
    return report;
}

struct FrameProbe {
    vr::EVRTrackedCameraFrameType type;
    const char* name;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t bytes = 0;
    bool eligible = false;
    vr::EVRTrackedCameraError initial_size_error = vr::VRTrackedCameraError_None;
    Json initial_size_report = Json();
    Json report = Json::object();
};

void inspect_frame(vr::IVRTrackedCamera* camera, FrameProbe& frame, int cameras,
                   bool initial_query = true) {
    frame.report.set("frame_type", frame.name);
    const auto error = camera->GetCameraFrameSize(
        vr::k_unTrackedDeviceIndex_Hmd, frame.type,
        &frame.width, &frame.height, &frame.bytes);
    auto size = camera_error(camera, error);
    if (error == vr::VRTrackedCameraError_None) {
        size.set("width", Json::number(frame.width));
        size.set("height", Json::number(frame.height));
        size.set("buffer_bytes", Json::number(frame.bytes));
    }
    if (initial_query) {
        frame.initial_size_error = error;
        frame.initial_size_report = size;
    }
    frame.report.set("size", std::move(size));

    auto intrinsics = Json::array();
    // Unknown/zero reported counts do not establish that calibration index 0 is absent.
    const int probes = cameras > 0 ? std::min(cameras, kMaxCalibrationCameras) : 1;
    for (int index = 0; index < probes; ++index) {
        vr::HmdVector2_t focal{}, center{};
        auto calibration = camera_error(camera, camera->GetCameraIntrinsics(
            vr::k_unTrackedDeviceIndex_Hmd, static_cast<std::uint32_t>(index),
            frame.type, &focal, &center));
        calibration.set("camera_index", Json::number(index));
        intrinsics.append(std::move(calibration));
    }
    frame.report.set("intrinsics", std::move(intrinsics));

    std::string reason;
    if (error != vr::VRTrackedCameraError_None) {
        reason = "frame_size_unavailable";
    } else if (!frame.width || !frame.height || !frame.bytes) {
        reason = "invalid_frame_size";
    } else if (frame.bytes > kMaxBufferBytes) {
        reason = "buffer_exceeds_64_mib_limit";
    } else {
        const auto pixels = static_cast<std::uint64_t>(frame.width) * frame.height;
        if (pixels > kMaxBufferBytes / 4 || pixels * 4 > frame.bytes) {
            reason = "buffer_too_small_for_rgba_dimensions";
        }
    }
    frame.eligible = reason.empty();
    frame.report.set("retrieval", Json::object({
        {"status", "not_attempted"},
        {"reason", frame.eligible ? "stream_not_acquired" : reason},
    }));
}

Json frame_header(const vr::CameraVideoStreamFrameHeader_t& header,
                  vr::EVRTrackedCameraFrameType requested_type) {
    return Json::object({
        {"frame_type_matches", header.eFrameType == requested_type},
        {"width", Json::number(header.nWidth)},
        {"height", Json::number(header.nHeight)},
        {"bytes_per_pixel", Json::number(header.nBytesPerPixel)},
        {"frame_sequence", Json::number(header.nFrameSequence)},
        {"pose_valid", header.trackedDevicePose.bPoseIsValid},
        {"exposure_timestamp_present", header.ulFrameExposureTime != 0},
    });
}

void inspect_header(vr::IVRTrackedCamera* camera, vr::TrackedCameraHandle_t handle,
                    FrameProbe& frame) {
    vr::CameraVideoStreamFrameHeader_t header{};
    const auto error = camera->GetVideoStreamFrameBuffer(
        handle, frame.type, nullptr, 0, &header, sizeof(header));
    auto result = camera_error(camera, error);
    if (error == vr::VRTrackedCameraError_None) {
        result.set("header", frame_header(header, frame.type));
    } else if (error == vr::VRTrackedCameraError_NoFrameAvailable) {
        result.set("status", "no_frame_available");
    }
    frame.report.set("header_only_after_acquisition", std::move(result));
}

bool retrieve_frame(vr::IVRTrackedCamera* camera, vr::TrackedCameraHandle_t handle,
                    FrameProbe& frame, Clock::time_point deadline) {
    if (!frame.eligible) return false;
    std::vector<std::uint8_t> pixels(frame.bytes);
    const auto started = Clock::now();
    auto result = Json::object();
    int attempts = 0;
    int no_frame_count = 0;
    bool valid = false;
    std::string status = "no_frame_before_deadline";
    while (Clock::now() < deadline) {
        vr::CameraVideoStreamFrameHeader_t header{};
        ++attempts;
        const auto error = camera->GetVideoStreamFrameBuffer(
            handle, frame.type, pixels.data(), frame.bytes, &header, sizeof(header));
        result = camera_error(camera, error);
        if (error == vr::VRTrackedCameraError_NoFrameAvailable) {
            ++no_frame_count;
        } else if (error != vr::VRTrackedCameraError_None) {
            status = "capture_error";
            break;
        } else {
            const auto count = static_cast<std::uint64_t>(header.nWidth) * header.nHeight;
            valid = header.eFrameType == frame.type && header.nWidth == frame.width &&
                header.nHeight == frame.height && header.nBytesPerPixel == 4 &&
                count > 0 && count <= kMaxBufferBytes / 4 && count * 4 <= pixels.size();
            status = valid ? "valid_frame" : "invalid_frame_metadata";
            result.set("header", frame_header(header, frame.type));
            break;
        }
        const auto next = std::min(deadline, Clock::now() + kPollInterval);
        std::this_thread::sleep_until(next);
    }
    result.set("status", status);
    result.set("attempts", Json::number(attempts));
    result.set("no_frame_available_count", Json::number(no_frame_count));
    result.set("elapsed_ms", Json::number(std::chrono::duration_cast<
        std::chrono::milliseconds>(Clock::now() - started).count()));
    frame.report.set("retrieval", std::move(result));
    return valid;
}

int probe(Json& report) {
    vr::EVRInitError init_error = vr::VRInitError_None;
    auto* system = vr::VR_Init(&init_error, vr::VRApplication_Background);
    OpenVRSession session(init_error == vr::VRInitError_None && system);
    report.set("initialization", Json::object({
        {"application_type", "VRApplication_Background"},
        {"status", init_error == vr::VRInitError_None && system ? "ok" : "error"},
        {"error_name", vr::VR_GetVRInitErrorAsSymbol(init_error)},
        {"error_code", Json::number(static_cast<int>(init_error))},
    }));
    if (init_error != vr::VRInitError_None || !system) {
        report.set("status", "initialization_failed");
        return 1;
    }
    auto* camera = vr::VRTrackedCamera();
    if (!camera) {
        report.set("status", "camera_interface_unavailable");
        report.set("camera_interface", Json::object({
            {"status", "unavailable"}, {"requested_version", vr::IVRTrackedCamera_Version},
        }));
        return 1;
    }
    report.set("camera_interface", Json::object({
        {"status", "available"}, {"requested_version", vr::IVRTrackedCamera_Version},
    }));

    bool has_camera = false;
    const auto has_error = camera->HasCamera(vr::k_unTrackedDeviceIndex_Hmd, &has_camera);
    auto presence = camera_error(camera, has_error);
    presence.set("value", has_error == vr::VRTrackedCameraError_None ? Json(has_camera) : Json());
    int cameras = -1;
    auto hmd = Json::object({
        {"connected", system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd)},
        {"has_camera", std::move(presence)},
        {"camera_count", property(system, vr::Prop_NumCameras_Int32, &cameras)},
        {"frame_layout", property(system, vr::Prop_CameraFrameLayout_Int32, nullptr)},
    });
    hmd.set("calibration_indices_limited_to", Json::number(kMaxCalibrationCameras));
    report.set("hmd", std::move(hmd));

    std::array<FrameProbe, 3> frames{{
        {vr::VRTrackedCameraFrameType_Distorted, "distorted"},
        {vr::VRTrackedCameraFrameType_Undistorted, "undistorted"},
        {vr::VRTrackedCameraFrameType_MaximumUndistorted, "maximum_undistorted"},
    }};
    for (auto& frame : frames) inspect_frame(camera, frame, cameras);

    CameraStream stream(camera);
    const auto acquire_error = camera->AcquireVideoStreamingService(
        vr::k_unTrackedDeviceIndex_Hmd, &stream.handle);
    auto acquisition = camera_error(camera, acquire_error);
    const bool handle_valid = stream.handle != INVALID_TRACKED_CAMERA_HANDLE;
    acquisition.set("handle_valid", handle_valid);
    if (acquire_error == vr::VRTrackedCameraError_None && !handle_valid) {
        acquisition.set("status", "invalid_handle");
    }
    auto stream_report = Json::object({{"acquisition", std::move(acquisition)}});
    bool valid = false;
    const auto capture_start = Clock::now();
    const auto global_deadline = capture_start + kCaptureBudget;
    if (acquire_error == vr::VRTrackedCameraError_None && handle_valid) {
        for (auto& frame : frames) {
            // The provider may activate only after acquisition. A header probe is
            // useful even when the original size query failed; it never allocates.
            inspect_header(camera, stream.handle, frame);
            if (frame.initial_size_error != vr::VRTrackedCameraError_None) {
                frame.report.set("size_before_acquisition", frame.initial_size_report);
                inspect_frame(camera, frame, cameras, false);
            }
        }
        int remaining = static_cast<int>(std::count_if(frames.begin(), frames.end(),
            [](const FrameProbe& frame) { return frame.eligible; }));
        for (auto& frame : frames) {
            if (!frame.eligible) continue;
            const auto now = Clock::now();
            // Each eligible frame type gets part of one shared three-second budget.
            const auto deadline = now < global_deadline
                ? now + (global_deadline - now) / remaining : global_deadline;
            valid = retrieve_frame(camera, stream.handle, frame, deadline) || valid;
            --remaining;
        }
    }
    const auto release_error = handle_valid ? stream.release() : vr::VRTrackedCameraError_None;
    stream_report.set("release", handle_valid
        ? camera_error(camera, release_error)
        : Json::object({{"status", "not_needed"}}));
    report.set("stream", std::move(stream_report));
    auto frame_reports = Json::array();
    for (auto& frame : frames) frame_reports.append(std::move(frame.report));
    report.set("frames", std::move(frame_reports));
    report.set("capture", Json::object({
        {"any_valid_frame", valid},
        {"elapsed_ms", Json::number(std::chrono::duration_cast<
            std::chrono::milliseconds>(Clock::now() - capture_start).count())},
        {"polling_budget_ms", Json::number(3000)},
        {"poll_interval_ms", Json::number(16)},
        {"maximum_buffer_bytes", Json::number(kMaxBufferBytes)},
        {"images_saved", false},
    }));
    if (release_error != vr::VRTrackedCameraError_None) {
        report.set("status", "cleanup_failure");
        return 1;
    }
    report.set("status", valid ? "frame_received" : "no_valid_frame");
    return valid ? 0 : 2;
}

} // namespace

int main(int argc, char* argv[]) {
    if (argc == 2 && std::string(argv[1]) == "--help") {
        std::cout << "Usage: sightcircuit-camera-probe " << kStreamOptIn << '\n'
                  << kPassthroughWarning << '\n'
                  << "Without the flag, no OpenVR initialization or camera calls occur.\n";
        return 0;
    }
    auto report = Json::object({
        {"probe", "sightcircuit_public_openvr_camera"},
        {"schema_version", Json::number(1)},
        {"scope", "Public HMD tracked-camera API; source identity and Arcturus RGB support unverified"},
        {"status", "not_started"},
        {"passthrough_warning", kPassthroughWarning},
    });
    int exit_code = 1;
    if (argc == 1) {
        report.set("status", "not_run");
        report.set("reason", "explicit_passthrough_interruption_opt_in_required");
        report.set("required_argument", kStreamOptIn);
        report.set("initialization", Json::object({{"status", "not_attempted"}}));
        exit_code = 3;
    } else if (argc != 2 || std::string(argv[1]) != kStreamOptIn) {
        report.set("status", "invalid_arguments");
        report.set("required_argument", kStreamOptIn);
        report.set("initialization", Json::object({{"status", "not_attempted"}}));
        exit_code = 64;
    } else {
        std::cerr << "Warning: " << kPassthroughWarning << '\n';
        try {
            exit_code = probe(report);
        } catch (const std::bad_alloc&) {
            report.set("status", "allocation_failed");
        } catch (const std::exception& error) {
            report.set("status", "probe_error");
            report.set("error", error.what());
        } catch (...) {
            report.set("status", "probe_error");
            report.set("error", "Unknown exception");
        }
    }
    report.set("exit_code", Json::number(exit_code));
    report.write(std::cout);
    std::cout << '\n';
    return exit_code;
}
