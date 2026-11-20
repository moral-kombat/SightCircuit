// sightcircuit-presence-watcher: tells its parent when SteamVR says the headset is no longer worn.
//
// A run without display (camera_recognize, camera_guide) has no bridge, so this small client listens for it.
// It joins a running SteamVR as VRApplication_Background, which never starts one, asks for IVRSystem and
// nothing else, and only reads: runtime events and the HMD's activity level. It creates no overlay, writes no
// setting, requests no tracked-camera interface and reads no camera data, image or calibration value.
//
//   --describe             one JSON line: the protocol, the signal table and what the tool does not do; no SDK call
//   --watch --seconds N    watch for at most N seconds (1 to 120), then end by itself
//
// Output (--watch): strict JSON lines on stdout, each with "seq" (0, 1, 2, ...), "monotonic_ns"
// (CLOCK_MONOTONIC) and "realtime_ns" (CLOCK_REALTIME):
//   ready      the connection is up and the first poll is done: proof that the watcher is listening
//   presence   one signal of presence_signals.hpp, with "ends_run" and "end_reason" from its table
//   heartbeat  every 500 ms, so a parent can tell a stuck watcher from a quiet runtime
//   end        the last line: "reason" is stop_signal, max_seconds, runtime_quit or parent_gone
// A failure before ready is one "error" line and exit 1. SDK chatter goes to stderr (the bridge's stdout
// isolation). SIGTERM or SIGINT ends the watch cleanly (exit 0); the parent's death does too. A loop step that
// blocks for 5 s is ended by SIGALRM's default action, which the parent sees as a watcher that died.
#include <openvr.h>
#include "json/json.h"
#include "overlay_lifecycle.hpp"
#include "presence_signals.hpp"
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

namespace lifecycle = sightcircuit_lifecycle;
namespace presence = sightcircuit_presence;

namespace {
constexpr int kMinSeconds = 1, kMaxSeconds = 120;
constexpr long kPollMs = 20;
constexpr std::uint64_t kHeartbeatNs = 500000000ULL;
constexpr unsigned kStallSeconds = 5, kConnectSeconds = 10;
constexpr int kExitUsage = 64;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }

std::uint64_t clock_ns(clockid_t clock) {
    timespec value{};
    clock_gettime(clock, &value);
    return std::uint64_t(value.tv_sec) * 1000000000ULL + std::uint64_t(value.tv_nsec);
}

struct Output {
    std::uint64_t seq = 0;
    bool failed = false;
    // One line; the times are taken here unless the caller sampled them at the event.
    void emit(Json::Value line, std::uint64_t monotonic_ns = 0) {
        line["seq"] = Json::UInt64(seq++);
        line["monotonic_ns"] = Json::UInt64(monotonic_ns ? monotonic_ns : clock_ns(CLOCK_MONOTONIC));
        line["realtime_ns"] = Json::UInt64(clock_ns(CLOCK_REALTIME));
        Json::StreamWriterBuilder builder;
        builder["indentation"] = "";
        if (!lifecycle::write_protocol(Json::writeString(builder, line) + "\n")) failed = true;
    }
};

Json::Value signal_table() {
    Json::Value table(Json::arrayValue);
    for (const auto* signal : presence::kSignals) {
        Json::Value entry;
        entry["signal"] = signal->name;
        entry["ends_run"] = signal->ends_run;
        entry["end_reason"] = signal->end_reason ? Json::Value(signal->end_reason) : Json::Value();
        table.append(entry);
    }
    return table;
}

// What the tool is and is not, repeated on --describe and on ready.
void describe(Json::Value& line) {
    line["protocol"] = presence::kProtocol;
    line["tool"] = "sightcircuit-presence-watcher";
    line["application_type"] = "VRApplication_Background";
    line["starts_steamvr"] = false;
    line["interfaces_requested"] = Json::arrayValue;
    line["interfaces_requested"].append(vr::IVRSystem_Version);
    line["overlay_created"] = false;
    line["settings_written"] = false;
    line["camera_data_read"] = false;
    line["signals"] = signal_table();
    line["poll_ms"] = Json::Int64(kPollMs);
    line["heartbeat_ms"] = Json::UInt64(kHeartbeatNs / 1000000ULL);
    line["seconds_range"] = Json::arrayValue;
    line["seconds_range"].append(kMinSeconds);
    line["seconds_range"].append(kMaxSeconds);
}

void presence_line(Output& output, const presence::Signal& signal, vr::EDeviceActivityLevel activity,
                   std::uint64_t monotonic_ns, const float* event_age_seconds) {
    Json::Value line;
    line["event"] = "presence";
    line["signal"] = signal.name;
    line["ends_run"] = signal.ends_run;
    line["end_reason"] = signal.end_reason ? Json::Value(signal.end_reason) : Json::Value();
    line["hmd_activity"] = presence::activity_name(activity);
    // How long before the poll the runtime says the event happened; null for a polled change.
    if (event_age_seconds && *event_age_seconds >= 0 && *event_age_seconds < 3600)
        line["event_age_ms"] = Json::Int64(static_cast<long long>(*event_age_seconds * 1000.0f));
    else
        line["event_age_ms"] = Json::Value();
    output.emit(line, monotonic_ns);
}

int watch(int seconds) {
    Output output;
    std::signal(SIGINT, stop_signal);
    std::signal(SIGTERM, stop_signal);
    std::signal(SIGPIPE, SIG_IGN);
    const auto parent = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() != parent || parent == 1) {
        Json::Value line;
        line["event"] = "error"; line["reason"] = "parent_death_signal_unavailable";
        output.emit(line);
        return 1;
    }
    alarm(kConnectSeconds);
    vr::EVRInitError error = vr::VRInitError_None;
    vr::IVRSystem* system = vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None || !system) {
        Json::Value line;
        line["event"] = "error";
        line["reason"] = error != vr::VRInitError_None ? "vrserver_not_running" : "ivrsystem_unavailable";
        line["init_error"] = vr::VR_GetVRInitErrorAsSymbol(error);
        output.emit(line);
        if (error == vr::VRInitError_None) vr::VR_Shutdown();
        alarm(0);
        return 1;
    }
    const char* end_reason = nullptr;
    int exit_code = 0;
    std::uint64_t events_polled = 0, presence_lines = 0;
    if (!system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd)) {
        Json::Value line;
        line["event"] = "error"; line["reason"] = "headset_not_connected";
        output.emit(line);
        end_reason = "headset_not_connected"; exit_code = 1;
    }
    auto activity = vr::k_EDeviceActivityLevel_Unknown;
    if (!end_reason) {
        activity = system->GetTrackedDeviceActivityLevel(vr::k_unTrackedDeviceIndex_Hmd);
        vr::ETrackedPropertyError property_error = vr::TrackedProp_Success;
        const bool proximity = system->GetBoolTrackedDeviceProperty(
            vr::k_unTrackedDeviceIndex_Hmd, vr::Prop_ContainsProximitySensor_Bool, &property_error);
        Json::Value ready;
        ready["event"] = "ready";
        describe(ready);
        ready["pid"] = Json::Int64(getpid());
        ready["max_seconds"] = seconds;
        ready["hmd_connected"] = true;
        ready["hmd_activity"] = presence::activity_name(activity);
        ready["hmd_has_proximity_sensor"] =
            property_error == vr::TrackedProp_Success ? Json::Value(proximity) : Json::Value();
        output.emit(ready);
    }
    const auto deadline = clock_ns(CLOCK_MONOTONIC) + std::uint64_t(seconds) * 1000000000ULL;
    auto next_heartbeat = clock_ns(CLOCK_MONOTONIC) + kHeartbeatNs;
    while (!end_reason) {
        alarm(kStallSeconds);
        if (stopping) { end_reason = getppid() == parent ? "stop_signal" : "parent_gone"; break; }
        if (output.failed || getppid() != parent) { end_reason = "parent_gone"; break; }
        vr::VREvent_t event{};
        while (system->PollNextEvent(&event, sizeof(event))) {
            ++events_polled;
            const auto polled_ns = clock_ns(CLOCK_MONOTONIC);
            if (event.eventType == vr::VREvent_Quit) {
                system->AcknowledgeQuit_Exiting();
                end_reason = "runtime_quit";
                break;
            }
            if (const auto* signal = presence::classify(event)) {
                activity = system->GetTrackedDeviceActivityLevel(vr::k_unTrackedDeviceIndex_Hmd);
                presence_line(output, *signal, activity, polled_ns, &event.eventAgeSeconds);
                ++presence_lines;
            }
        }
        if (end_reason) break;
        const auto level = system->GetTrackedDeviceActivityLevel(vr::k_unTrackedDeviceIndex_Hmd);
        const auto now = clock_ns(CLOCK_MONOTONIC);
        if (level != activity) {
            activity = level;
            presence_line(output, presence::kActivityChanged, activity, now, nullptr);
            ++presence_lines;
        }
        if (now >= deadline) { end_reason = "max_seconds"; break; }
        if (now >= next_heartbeat) {
            Json::Value line;
            line["event"] = "heartbeat";
            output.emit(line, now);
            next_heartbeat = now + kHeartbeatNs;
        }
        const timespec wait{0, kPollMs * 1000000L};
        nanosleep(&wait, nullptr);  // A stop signal interrupts the sleep.
    }
    alarm(kStallSeconds);
    vr::VR_Shutdown();
    Json::Value end;
    end["event"] = "end";
    end["reason"] = end_reason;
    end["events_polled"] = Json::UInt64(events_polled);
    end["presence_lines"] = Json::UInt64(presence_lines);
    end["shutdown_returned"] = true;
    end["exit_code"] = exit_code;
    output.emit(end);
    alarm(0);
    return exit_code;
}
} // namespace

int main(int argc, char* argv[]) {
    // Before anything can load the SDK: keep the real stdout for the protocol.
    if (!lifecycle::isolate_stdout()) return 1;
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "--describe" && argc == 2) {
        Output output;
        Json::Value line;
        line["event"] = "describe";
        describe(line);
        output.emit(line);
        return output.failed ? 1 : 0;
    }
    if (mode == "--watch" && argc == 4 && std::string(argv[2]) == "--seconds") {
        char* end = nullptr;
        const long seconds = std::strtol(argv[3], &end, 10);
        if (end != argv[3] && *end == '\0' && std::strlen(argv[3]) <= 3 && seconds >= kMinSeconds &&
                seconds <= kMaxSeconds)
            return watch(static_cast<int>(seconds));
    }
    Output output;
    Json::Value line;
    line["event"] = "error";
    line["reason"] = "usage";
    line["usage"] = "sightcircuit-presence-watcher --describe | --watch --seconds 1..120";
    output.emit(line);
    return kExitUsage;
}
