# Documentation

Choose a guide by the task you want to complete. [Project status](status.md)
records what has been verified and its limits; [live capture](live-capture.md)
owns the current capture work plan.

## Start by task

| Task | Start here |
| --- | --- |
| Understand the project | [Project overview](../README.md) |
| Check what works on Frame | [Generated status and evidence](status.md) |
| Record or review detailed validation evidence | [Per-entry evidence and maintenance](evidence/README.md) |
| Review the previous long README | [Preserved README history](project-history.md) |
| Connect to Frame and inspect a recording | [On-device guide](on-device.md) |
| Decode a completed recording | [File decode procedure](on-device.md#decode-one-recorded-video-track) |
| Run the annotated replay window | [Window launch procedure](on-device.md#open-the-annotated-replay-window) |
| Run and position the VR panel | [VR overlay guide](vr-overlay.md) |
| Discover and test an existing SteamVR mirror | [Bounded mirror diagnostic](on-device.md#check-an-existing-steamvr-mirror) |
| Recognize objects in a captured room still | [Saved-image detector check](on-device.md#run-recognition-on-the-saved-mirror-image) |
| Test mirror capture and recognition together | [Bounded recognition without live display](on-device.md#run-bounded-recognition-from-the-mirror) |
| Check whether VR output is included in mirror input | [Fixed-panel comparison](on-device.md#check-whether-the-vr-panel-appears-in-mirror-input) |
| Test capture and recognition together without live display | [Bounded mirror recognition](on-device.md#run-bounded-recognition-from-the-mirror) |
| Inspect native capture build prerequisites without starting capture | [Read-only prerequisite report](on-device.md#check-native-capture-prerequisites) |
| Compile the capture reference without activating it | [Build-only check and synthetic ARM64 tests](on-device.md#build-the-capture-reference-without-activation) |
| Test descriptor transport changes without camera access | [Transport adaptation and synthetic sockets](on-device.md#test-the-transport-adaptation-without-capture) |
| Test the provider's writer and loop controls without camera access | [Owned publication and generated-data tests](on-device.md#test-owned-publication-without-capture) |
| Query GPU import prerequisites without opening the camera | [Bounded Vulkan capability query](on-device.md#query-vulkan-capture-capabilities) |
| Develop and test buffer imports without the headset | [Import validation and synthetic Vulkan ownership tests](development.md#build-and-test-image-import-ownership) |
| Test GPU startup, memory and resource cleanup without a driver | [GPU ownership adaptation and fault injection](development.md#build-and-test-gpu-resource-ownership) |
| Test hung capture workers, provider lifecycle and recovery | [Watchdog/provider development](development.md#capture-watchdog-provider-lifecycle-and-restoration-preparation) and [native preparation](on-device.md#test-the-watchdog-and-provider-without-capture) |
| Test metadata inspection without image access | [Changed native v3 checks](on-device.md#test-metadata-only-inspection-without-capture) and [producer synchronization findings](live-capture.md#producer-synchronization-review-and-next-checkpoint) |
| Prepare the next metadata-layer checkpoint | [Inactive bundle and service preflight](on-device.md#prepare-the-metadata-layer-trial) |
| Run the bounded metadata-layer activation and recovery test | [Opt-in trial and result reports](on-device.md#run-the-bounded-metadata-layer-trial) |
| Diagnose the paused trial without another restart | [Focused service-failure journal](on-device.md#read-the-service-failure-window) |
| Inspect SteamVR dependencies and launcher paths | [Read-only service details](on-device.md#inspect-the-service-relationships-without-restarting) |
| Review the platform start/stop hooks | [Read shipped launcher definitions without executing them](on-device.md#read-the-platform-launcher-definitions) |
| Connect from an independent development PC | [Verified SSH access](on-device.md#verify-independent-ssh-access) |
| Understand the canceled SteamVR stop and recovery | [Service-only rehearsal result and current guard](on-device.md#service-only-recovery-rehearsal-result) |
| Plan automated Frame testing with an on-device agent | [Claude Code on Frame plan (proposal)](agent-on-frame.md) |
| Run guarded automated checks on Frame | [Fixed task runner and agent settings](on-device.md#run-automated-checks-with-an-on-device-agent) |
| Run the prepared whole-session operator test | [One-command session test and result collection](on-device.md#prepared-platform-session-test) |
| Run the next metadata-only activation/restoration test | [Prepared two-session trial](on-device.md#prepared-metadata-session-trial) |
| Understand or replace the recognition model | [Recognition architecture and model candidates](architecture.md#recognition-implementation-and-model-replacement) |
| Compare detectors on labelled stills | [Frame procedure](on-device.md#compare-detectors-on-frame-stills) and [evaluation format](development.md#detector-evaluation) |
| Inspect existing luma input or test the isolated reader | [Shared-memory procedure](on-device.md#check-existing-luma-shared-memory) and [controlled fixture](development.md#isolated-luma-reader-and-synthetic-producer) |
| Set up cloud or desktop development | [Development setup](development.md) |
| Plan the next POC work packages | [POC scope: fixed edge HUD](poc-scope.md) |
| Check active WPs and execute the remaining offline work | [Current checkpoint](poc-scope.md#execution-checkpoint-2026-10-31) and [copy-ready implementation prompt](execution-prompt.md) |
| Select upstream components for the POC and later assistant | [Ranked reuse plan, source pins and limits](poc-scope.md#upstream-reuse-plan-2026-10-31) |
| Continue live capture development | [Active capture plan](live-capture.md#current-work-order) |
| Review the minimum LIVE POC and Monday target | [Critical path, acceptance evidence and risks](live-capture.md#minimum-live-poc-and-october-26-target) |
| Review mapping qualification and validation preparation | [Proposed G4 method and device-validation design](hud-qualification.md) |
| Review the next proposed device checkpoint | [Bounded edge HUD calibration; separate approval required](on-device.md#bounded-edge-hud-calibration-checkpoint) |
| Investigate camera access and upstream projects | [Camera interface reference](steam-frame-camera.md) and [dated code review](capture-reference-review.md) |
| Understand components and frame flow | [Architecture](architecture.md) |
| Review architecture and implementation improvements | [Dated design review](design-review.md) |
| Contribute a change | [Contributing](../CONTRIBUTING.md) |
| Check dependency attribution | [Third-party sources](../THIRD_PARTY.md) |
| Maintain these documents | [Documentation plan and ownership](documentation-plan.md) |

A browser on Frame can display a cloud terminal. Confirm where commands execute
before using device instructions; see [execution context](on-device.md#confirm-where-commands-run).

## Reading order for a new session

Read [status](status.md), then the guide for the task. For capture development,
read the [current work order](live-capture.md#current-work-order) before the
reference research. The [on-device handoff](on-device.md#context-for-a-new-development-session)
provides a short prompt for continuing on the headset.

Procedures describe implemented tools. Capture experiments marked planned have
no runnable SightCircuit command yet. Research reports describe the cited runtime
or source revision, not compatibility with every headset update.
