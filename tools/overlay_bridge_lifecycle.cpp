// Overlay bridge, lifecycle unit: the stop flag, the watchdog phases, the
// Background-then-Overlay connect and the overlay's retirement. The connect
// order and the retirement sequence themselves are the OpenVR-free helpers in
// overlay_lifecycle.hpp and overlay_frame.hpp; this unit binds them to the SDK.
#include "overlay_bridge.hpp"
#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>

namespace sightcircuit_bridge {
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
void watchdog(const std::string& mode, const char* phase, unsigned seconds) {
    if (!lifecycle::set_watchdog(watchdog_line(mode, phase, seconds), seconds))
        throw std::runtime_error("watchdog_arm_failed");
}
void Overlay::check(vr::EVROverlayError e, const char* op) {
    if (e != vr::VROverlayError_None)
        throw std::runtime_error(std::string(op)+":"+api->GetOverlayErrorNameFromEnum(e));
}
std::string Overlay::init_error(vr::EVRInitError error, const void* interface) {
    if (error != vr::VRInitError_None) return vr::VR_GetVRInitErrorAsSymbol(error);
    return interface ? std::string() : std::string("VRInitError_None_without_IVRSystem");
}
void Overlay::init(bool calibration, bool background_only, Json::Value& report) {
    // Background never starts a vrserver; only a running one is joined as Overlay.
    // With background_only (an opt-in device check) the probe itself owns the overlay.
    vr::IVRSystem* probe_system = nullptr;
    const auto result = lifecycle::connect_overlay(
        [&] {
            vr::EVRInitError error = vr::VRInitError_None;
            probe_system = vr::VR_Init(&error, vr::VRApplication_Background);
            probe_active = error == vr::VRInitError_None && probe_system;
            if (!probe_active) probe_system = nullptr;
            return init_error(error, probe_system);
        },
        [&] { if (probe_active) { vr::VR_Shutdown(); probe_active = false; } },
        [&] {
            vr::EVRInitError error = vr::VRInitError_None;
            system = vr::VR_Init(&error, vr::VRApplication_Overlay);
            active = error == vr::VRInitError_None && system;
            if (!active) system = nullptr;
            return init_error(error, system);
        },
        [&](unsigned ms) {
            const auto until = Clock::now()+std::chrono::milliseconds(ms);
            while (!stopping && Clock::now() < until) std::this_thread::sleep_for(std::chrono::milliseconds(10));
        },
        [&] { return stopping != 0; }, background_only);
    report["sequence"] = background_only ? "background_only_opt_in" : "background_probe_then_overlay";
    report["application_type"] = background_only ? "VRApplication_Background" : "VRApplication_Overlay";
    report["background_probes"] = result.background_probes;
    report["background_probe_result"] = result.background_probe_error;
    report["vrserver_running"] = result.server_running;
    report["overlay_connect_attempted"] = result.overlay_connect_attempted;
    report["overlay_connect_attempts"] = result.overlay_attempts;
    report["overlay_connect_attempt_limit"] = lifecycle::kOverlayConnectAttempts;
    report["overlay_connect_errors"] = Json::arrayValue;
    for (const auto& error : result.overlay_errors) report["overlay_connect_errors"].append(error);
    report["connected"] = result.connected;
    if (result.stopped) throw Stopped{};
    if (!result.connected) throw std::runtime_error(result.error);
    if (background_only) { system = probe_system; active = true; probe_active = false; }
    api = vr::VROverlay();
    if (!api) throw std::runtime_error("IVROverlay_028_unavailable");
    if (!system->IsTrackedDeviceConnected(vr::k_unTrackedDeviceIndex_Hmd))
        throw std::runtime_error("headset_not_connected");
    const auto key = std::string(calibration ? "sightcircuit.calibration." : "sightcircuit.replay.")+std::to_string(getpid());
    check(api->CreateOverlay(key.c_str(), calibration ? "SightCircuit calibration marker" : "SightCircuit replay", &handle), "CreateOverlay");
    check(api->SetOverlayInputMethod(handle, vr::VROverlayInputMethod_None), "SetOverlayInputMethod");
    check(api->SetOverlayTextureColorSpace(handle, vr::ColorSpace_Gamma), "SetOverlayTextureColorSpace");
    if (calibration)
        check(api->SetOverlayFlag(handle, vr::VROverlayFlags_VisibleInDashboard, kCalibrationVisibleInDashboard),
              "SetOverlayFlag:VisibleInDashboard");
    configure(config);
}
bool Overlay::close() noexcept {
    bool ok = true;
    if (api && handle != vr::k_ulOverlayHandleInvalid) {
        retirement_attempted = true;
        retirement = sightcircuit_overlay::retire_overlay(
            [&] { return api->HideOverlay(handle) == vr::VROverlayError_None; },
            [&] { return api->ClearOverlayTexture(handle) == vr::VROverlayError_None; },
            [&] { return api->DestroyOverlay(handle) == vr::VROverlayError_None; });
        ok = retirement.ok();
        handle = vr::k_ulOverlayHandleInvalid;
    }
    if (active || probe_active) { vr::VR_Shutdown(); active = probe_active = false; shutdown_returned = true; }
    system = nullptr; api = nullptr;
    return ok;
}
} // namespace sightcircuit_bridge
