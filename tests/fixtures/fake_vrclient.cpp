// A fake vrclient.so for the overlay bridge's tests: just enough of a runtime for the bridge's own calls,
// loaded by the SDK's public loader through VR_PATHREG_OVERRIDE. It displays nothing and talks to no device.
// Every overlay call is appended to $FAKE_VR_LOG as one JSON line with CLOCK_MONOTONIC start and end times
// (the bridge's clock), so a test can order the bridge's calls against its own protocol lines.
//   FAKE_VR_<Call>_DELAY_MS  comma-separated milliseconds, one per call of that name in order (then none)
//   FAKE_VR_<Call>_FAIL      comma-separated 1-based call numbers that return VROverlayError_RequestFailed
//   FAKE_VR_STICKY_VISIBLE   when set, HideOverlay returns success and the overlay stays visible
// fake_vrclient_stubs.hpp is generated from the pinned openvr.h by tests/fake_vrclient.py: every pure virtual
// of IVRSystem and IVROverlay with a default body, so this file names only what the bridge calls.
#include <openvr.h>
#include "ivrclientcore.h"
#include "fake_vrclient_stubs.hpp"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <string>
#include <time.h>

namespace {
std::uint64_t now_ns() {
    timespec value{};
    clock_gettime(CLOCK_MONOTONIC, &value);
    return std::uint64_t(value.tv_sec)*1000000000ULL + std::uint64_t(value.tv_nsec);
}
std::map<std::string, unsigned> calls;
// The nth comma-separated number of an environment variable (1-based), or 0.
long nth(const std::string& name, unsigned index) {
    const char* text = std::getenv(name.c_str());
    for (unsigned at = 1; text && *text; ++at) {
        char* end = nullptr;
        const long value = std::strtol(text, &end, 10);
        if (at == index) return value;
        text = *end == ',' ? end + 1 : end;
        if (end == text && *end != ',') break;
    }
    return 0;
}
bool listed(const std::string& name, unsigned index) {
    const char* text = std::getenv(name.c_str());
    while (text && *text) {
        char* end = nullptr;
        if (std::strtol(text, &end, 10) == long(index)) return true;
        if (end == text) break;
        text = *end == ',' ? end + 1 : end;
    }
    return false;
}
// One logged call: its delay, whether it is told to fail, and the log line when it ends.
struct Call {
    std::string name;
    std::uint64_t start;
    bool fail;
    explicit Call(const char* call) : name(call), start(now_ns()) {
        const unsigned index = ++calls[name];
        if (const long ms = nth("FAKE_VR_" + name + "_DELAY_MS", index); ms > 0) {
            timespec wait{ms / 1000, (ms % 1000) * 1000000L};
            while (nanosleep(&wait, &wait)) {}
        }
        fail = listed("FAKE_VR_" + name + "_FAIL", index);
    }
    vr::EVROverlayError done(bool visible, const std::string& extra = "") {
        if (const char* path = std::getenv("FAKE_VR_LOG")) {
            if (FILE* log = std::fopen(path, "a")) {
                std::fprintf(log, "{\"call\":\"%s\",\"start_ns\":%llu,\"end_ns\":%llu,\"ok\":%s,\"visible\":%s%s}\n",
                             name.c_str(), static_cast<unsigned long long>(start),
                             static_cast<unsigned long long>(now_ns()), fail ? "false" : "true",
                             visible ? "true" : "false", extra.c_str());
                std::fclose(log);
            }
        }
        return fail ? vr::VROverlayError_RequestFailed : vr::VROverlayError_None;
    }
};

struct Overlay final : FakeOverlayStubs {
    bool created = false, visible = false, textured = false;
    std::deque<vr::EVREventType> events;
    void set_visible(bool wanted) {
        if (wanted != visible) events.push_back(wanted ? vr::VREvent_OverlayShown : vr::VREvent_OverlayHidden);
        visible = wanted;
    }
    vr::EVROverlayError CreateOverlay(const char*, const char*, vr::VROverlayHandle_t* handle) override {
        Call call("CreateOverlay");
        if (!call.fail) { created = true; *handle = 7; }
        return call.done(visible);
    }
    vr::EVROverlayError DestroyOverlay(vr::VROverlayHandle_t) override {
        Call call("DestroyOverlay");
        if (!call.fail) { created = false; visible = false; }
        return call.done(visible);
    }
    const char* GetOverlayErrorNameFromEnum(vr::EVROverlayError error) override {
        return error == vr::VROverlayError_None ? "VROverlayError_None" : "VROverlayError_RequestFailed";
    }
    vr::EVROverlayError SetOverlayInputMethod(vr::VROverlayHandle_t, vr::VROverlayInputMethod) override {
        return vr::VROverlayError_None;
    }
    vr::EVROverlayError SetOverlayTextureColorSpace(vr::VROverlayHandle_t, vr::EColorSpace) override {
        return vr::VROverlayError_None;
    }
    vr::EVROverlayError SetOverlayFlag(vr::VROverlayHandle_t, vr::VROverlayFlags, bool) override {
        return vr::VROverlayError_None;
    }
    vr::EVROverlayError GetOverlayFlag(vr::VROverlayHandle_t, vr::VROverlayFlags, bool* value) override {
        *value = false;
        return vr::VROverlayError_None;
    }
    vr::EVROverlayError SetOverlayWidthInMeters(vr::VROverlayHandle_t, float width) override {
        return Call("SetOverlayWidthInMeters").done(visible, ",\"value\":" + std::to_string(width));
    }
    vr::EVROverlayError SetOverlayAlpha(vr::VROverlayHandle_t, float alpha) override {
        return Call("SetOverlayAlpha").done(visible, ",\"value\":" + std::to_string(alpha));
    }
    vr::EVROverlayError SetOverlayTransformTrackedDeviceRelative(vr::VROverlayHandle_t, vr::TrackedDeviceIndex_t,
                                                                const vr::HmdMatrix34_t* m) override {
        return Call("SetOverlayTransformTrackedDeviceRelative").done(
            visible, ",\"x\":" + std::to_string(m->m[0][3]) + ",\"y\":" + std::to_string(m->m[1][3]) +
                     ",\"z\":" + std::to_string(m->m[2][3]));
    }
    vr::EVROverlayError ShowOverlay(vr::VROverlayHandle_t) override {
        Call call("ShowOverlay");
        if (!call.fail) set_visible(true);
        return call.done(visible);
    }
    vr::EVROverlayError HideOverlay(vr::VROverlayHandle_t) override {
        Call call("HideOverlay");
        if (!call.fail && !std::getenv("FAKE_VR_STICKY_VISIBLE")) set_visible(false);
        return call.done(visible);
    }
    bool IsOverlayVisible(vr::VROverlayHandle_t) override { return visible; }
    bool IsDashboardVisible() override { return false; }
    vr::EVROverlayError SetOverlayRaw(vr::VROverlayHandle_t, void* buffer, uint32_t width, uint32_t height,
                                      uint32_t depth) override {
        Call call("SetOverlayRaw");
        if (!call.fail) textured = true;
        // The first pixel's red byte tells one test tile from another in the log.
        return call.done(visible, ",\"width\":" + std::to_string(width) + ",\"height\":" + std::to_string(height) +
                                  ",\"depth\":" + std::to_string(depth) + ",\"first\":" +
                                  std::to_string(static_cast<const unsigned char*>(buffer)[0]));
    }
    vr::EVROverlayError ClearOverlayTexture(vr::VROverlayHandle_t) override {
        Call call("ClearOverlayTexture");
        if (!call.fail) textured = false;
        return call.done(visible);
    }
    bool PollNextOverlayEvent(vr::VROverlayHandle_t, vr::VREvent_t* event, uint32_t size) override {
        if (events.empty()) return false;
        std::memset(event, 0, size);
        event->eventType = events.front();
        events.pop_front();
        return true;
    }
};
struct System final : FakeSystemStubs {
    bool IsTrackedDeviceConnected(vr::TrackedDeviceIndex_t) override { return true; }
    bool PollNextEvent(vr::VREvent_t*, uint32_t) override { return false; }
    void AcknowledgeQuit_Exiting() override {}
};
struct Core final : vr::IVRClientCore {
    Overlay overlay;
    System system;
    vr::EVRInitError Init(vr::EVRApplicationType, const char*) override { return vr::VRInitError_None; }
    void Cleanup() override {}
    vr::EVRInitError IsInterfaceVersionValid(const char*) override { return vr::VRInitError_None; }
    void* GetGenericInterface(const char* name, vr::EVRInitError* error) override {
        if (error) *error = vr::VRInitError_None;
        if (!std::strcmp(name, vr::IVRSystem_Version)) return static_cast<vr::IVRSystem*>(&system);
        if (!std::strcmp(name, vr::IVROverlay_Version)) return static_cast<vr::IVROverlay*>(&overlay);
        if (error) *error = vr::VRInitError_Init_InterfaceNotFound;
        return nullptr;
    }
    bool BIsHmdPresent() override { return true; }
    const char* GetEnglishStringForHmdError(vr::EVRInitError) override { return "fake"; }
    const char* GetIDForVRInitError(vr::EVRInitError error) override {
        return error == vr::VRInitError_None ? "VRInitError_None" : "VRInitError_Fake";
    }
};
} // namespace

extern "C" __attribute__((visibility("default"))) void* VRClientCoreFactory(const char* name, int* code) {
    static Core core;
    const bool known = !std::strcmp(name, vr::IVRClientCore_Version);
    if (code) *code = known ? vr::VRInitError_None : vr::VRInitError_Init_InterfaceNotFound;
    return known ? static_cast<vr::IVRClientCore*>(&core) : nullptr;
}
