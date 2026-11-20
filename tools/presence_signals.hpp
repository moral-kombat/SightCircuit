// Presence signals from SteamVR: the one table of what a runtime event means for "is the headset worn",
// shared by the presence watcher (tools/presence_watcher.cpp) and, when the displayed path adopts it, the
// overlay bridge. Presence only: nothing here names a tracked-camera interface, an overlay or a setting.
//
// Which signals end a run (work order, 2026-11-20, after E100):
//   enter_standby_event   VREvent_EnterStandbyMode. SteamVR's own statement that the system went to standby;
//                         E100's journal shows it reaching XRService 7.0 s after the wearer's click. Ends the
//                         run with reason "standby".
// Reported with their times and never ending a run, because none has been observed on the Frame and each can
// fire while the headset is still worn (a hand at the proximity sensor, a still head):
//   leave_standby_event           VREvent_LeaveStandbyMode
//   user_interaction_ended        VREvent_TrackedDeviceUserInteractionEnded for the HMD
//   user_interaction_started      VREvent_TrackedDeviceUserInteractionStarted for the HMD
//   proximity_off / proximity_on  VREvent_ButtonUnpress / VREvent_ButtonPress of k_EButton_ProximitySensor on the HMD
//   hmd_activity_changed          GetTrackedDeviceActivityLevel(HMD) differs from the last poll
// sightcircuit/camera/presence.py holds the same table and refuses a line that disagrees with it.
#pragma once
#include <openvr.h>

namespace sightcircuit_presence {
constexpr const char* kProtocol = "presence-watch/1";

struct Signal {
    const char* name;
    bool ends_run;
    const char* end_reason;  // The run's end reason when ends_run, else nullptr.
};
constexpr Signal kEnterStandby{"enter_standby_event", true, "standby"};
constexpr Signal kLeaveStandby{"leave_standby_event", false, nullptr};
constexpr Signal kInteractionEnded{"user_interaction_ended", false, nullptr};
constexpr Signal kInteractionStarted{"user_interaction_started", false, nullptr};
constexpr Signal kProximityOff{"proximity_off", false, nullptr};
constexpr Signal kProximityOn{"proximity_on", false, nullptr};
constexpr Signal kActivityChanged{"hmd_activity_changed", false, nullptr};
constexpr const Signal* kSignals[] = {&kEnterStandby, &kLeaveStandby, &kInteractionEnded, &kInteractionStarted,
                                      &kProximityOff, &kProximityOn, &kActivityChanged};

inline const char* activity_name(vr::EDeviceActivityLevel level) {
    switch (level) {
        case vr::k_EDeviceActivityLevel_Idle: return "idle";
        case vr::k_EDeviceActivityLevel_UserInteraction: return "user_interaction";
        case vr::k_EDeviceActivityLevel_UserInteraction_Timeout: return "user_interaction_timeout";
        case vr::k_EDeviceActivityLevel_Standby: return "standby";
        case vr::k_EDeviceActivityLevel_Idle_Timeout: return "idle_timeout";
        default: return "unknown";
    }
}

// The presence signal a runtime event carries, or nullptr. The standby events are system-wide; the others
// count only for the HMD.
inline const Signal* classify(const vr::VREvent_t& event) {
    const bool hmd = event.trackedDeviceIndex == vr::k_unTrackedDeviceIndex_Hmd;
    switch (event.eventType) {
        case vr::VREvent_EnterStandbyMode: return &kEnterStandby;
        case vr::VREvent_LeaveStandbyMode: return &kLeaveStandby;
        case vr::VREvent_TrackedDeviceUserInteractionEnded: return hmd ? &kInteractionEnded : nullptr;
        case vr::VREvent_TrackedDeviceUserInteractionStarted: return hmd ? &kInteractionStarted : nullptr;
        case vr::VREvent_ButtonUnpress:
            return hmd && event.data.controller.button == vr::k_EButton_ProximitySensor ? &kProximityOff : nullptr;
        case vr::VREvent_ButtonPress:
            return hmd && event.data.controller.button == vr::k_EButton_ProximitySensor ? &kProximityOn : nullptr;
        default: return nullptr;
    }
}
} // namespace sightcircuit_presence
