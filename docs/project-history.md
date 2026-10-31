# README history before the evidence migration

Archived from `main` `41eadf8` on 2026-10-31. This preserves the previous
README narrative and links; its next-step instructions are historical. Use the
[current work order](live-capture.md#current-work-order) and
[acceptance gates](live-capture.md#edge-hud-acceptance-gates) for current work.

# Framejacker

Framejacker is an early Linux object-recognition app for Steam Frame. It
processes Arcturus recordings and, in a bounded diagnostic, SteamVR mirror
frames. Recorded video appears in a Qt window and an optional head-relative
VR panel. Bounded mirror capture/inference works on Frame; continuous live
display is planned.

Start with the [documentation index](README.md) for task-based navigation.

## Project status

| Area | Current summary |
| --- | --- |
| Recorded-video decoding, replay and VR panel | Latest POC processes/presents 576 frames and submits 576 VR images with clean shutdown; user confirms visible labels and normal passthrough; [result](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame) |
| Live input in the recognition application | Latest bounded POC reads 100 frames, processes 51 at 4.993 fps and saves an annotated preview; user confirms matching labels/boxes; [result](status.md#e48-live-mirror-recognition-poc-and-matching-preview). Fixed edge HUD/masking is implemented and offline-tested with synthetic input; device calibration and real output remain gated |
| Detection quality, color and performance | Latest preview labels/boxes match the viewed objects; an earlier rug was mislabeled sofa, and the 20-class model has no rug class. Broader accuracy, color and latency evaluation pending |
| Stereo depth, world-space labels and Gaussian splats | Future work; splat reconstruction is a separate recorded-data experiment |

See [status and evidence](status.md) for the complete matrix, device
reports, cloud validation and remaining unknowns. The public tracked-camera
probe returned no frames and interrupted passthrough; do not repeat that
unchanged headset test. Its [operating warning](on-device.md#confirmed-public-probe-result)
and [recorded result](status.md#e3-public-camera-probe) explain the limit.

## Quick start

### On Steam Frame: recorded-video replay

Use an existing checkout in the normal graphical session, with Python 3.12 and
a completed Arcturus recording. For initial access or another checkout location,
follow the [on-device guide](on-device.md).

```bash
cd "$HOME/framejacker"
bash scripts/setup_window.sh
.venv-replay/bin/python scripts/replay_window.py
```

With no path argument, the app selects the newest completed `*_stereo.mp4` in
`~/Videos/ArcturusVisionCamera`. It loops the clip and provides pause, replay
and loop controls. Close the window to stop. See the
[window procedure](on-device.md#open-the-annotated-replay-window) for explicit
file selection, dependencies and limits. To add the VR panel, follow the
[overlay guide](vr-overlay.md), including its native bridge build.

### In the cloud or on Linux x86_64: development smoke check

Use Bash and [uv](https://docs.astral.sh/uv/getting-started/installation/). In an
existing checkout:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

This creates a dedicated Python 3.12 `.venv` and checks generated sample-video
decoding, CPU detection, offscreen Qt rendering and a separate ONNX arithmetic
example. It exits after validation; it does not open the interactive replay app
or prove live camera access. Outputs go to ignored `artifacts/smoke/`.

The Frame replay environment is `.venv-replay`; do not copy the cloud's x86_64
environment onto Frame. [Development setup](development.md) owns full
installation, fixture verification, build and test instructions.

## Design and next steps

The intended application flow is:

```text
camera or replay input → preprocessing → detector → annotated window / VR panel
```

The [active live-capture plan](live-capture.md#current-work-order) owns the
next task: prepare and propose separately approved bounded calibration of the
[fixed edge HUD and native-resolution mask](architecture.md#fixed-edge-hud-decision),
now implemented and offline-tested with synthetic input only. Recorder experiments are paused. The [minimum LIVE POC plan](live-capture.md#minimum-live-poc-and-october-26-target)
sets the October 26 target, acceptance evidence, risks and offline work order.
Replay and bounded mirror demos are not the combined live-input/live-display POC. The
[mirror diagnostic](on-device.md#check-an-existing-steamvr-mirror) is
implemented and has delivered 1920 × 1080 RGB3 frames on Frame at the diagnostic's
10 reads/second limit. The user confirms its saved grayscale preview shows the
room. Recognition on that still completed but mislabeled a rug as sofa. The
[bounded mirror recognizer](on-device.md#run-bounded-recognition-from-the-mirror)
initially processed 51 reads at 4.993 fps, with clean shutdown and normal passthrough.
The latest [live POC](status.md#e48-live-mirror-recognition-poc-and-matching-preview)
again processes 51 reads at that cap, with 40.065 ms mean inference; the user
confirms matching preview labels/boxes and normal passthrough/tracking during/after.
The [fixed-panel check](on-device.md#check-whether-the-vr-panel-appears-in-mirror-input)
confirmed that this mirror captures our VR output. Arcturus is connected; its
selection for passthrough is unknown. The Frame reports GCC 15 and Vulkan 1.4.309
development files. The reference build and all 14 synthetic reader tests passed
on Frame ARM64. The transport adaptation passed seven fixture groups and the
publisher/control suite passed all eight tests on Frame. The bounded
[Vulkan capability query](on-device.md#query-vulkan-capture-capabilities)
also passed on Turnip Adreno 750, reporting two memory planes for the target
NV12 modifier, with normal passthrough throughout and afterward. The
[validated import adaptation](development.md#build-and-test-image-import-ownership)
compiled on ARM64 and passed ten synthetic tests on Frame. The cloud suite now
also covers the two-plane case explicitly. A separate GPU resource-ownership
adaptation now passes nine synthetic cloud tests, including startup rollback
and parent/child lifetime handling. The capture watchdog, versioned provider
metadata/registry, owned layer lifetime and exact configuration recovery are now
implemented; its earlier v2 milestone passed all 28 tests on Frame ARM64.
The [producer synchronization review](live-capture.md#producer-synchronization-review-and-next-checkpoint)
found no coordinated handoff for original camera buffers. The latest v3 layer
therefore records metadata without retaining/exporting image FDs, and live reads
remain blocked. The [metadata-only native checks](on-device.md#test-metadata-only-inspection-without-capture)
passed all 24 tests on Frame ARM64, and inactive preparation/service properties
were received. The [controlled metadata-layer trial](on-device.md#run-the-bounded-metadata-layer-trial)
is implemented but **paused after service-control failures on Frame**.
Saved reports show configuration restoration; SteamVR was later running with
normal UI/passthrough. The failed service operation remains unidentified, and
no successful metadata inspection was recorded. The runner does not relaunch
the console application. The [unit/selector review](status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix)
identified startup, waiter and cleanup stages; the hook inspector now preserves
every repeated hook. [Offline lifecycle/recovery checks](status.md#e45-offline-platform-lifecycle-and-guarded-recovery)
now pass 65 targeted tests, covering surviving processes, pending jobs and
guarded restoration. The
[read-only runtime baseline](status.md#e46-read-only-runtime-baseline-on-frame)
now passes on Frame, confirming service/cgroup/compositor liveness with normal
passthrough reported during/after. Independent PC SSH is now verified. The
[service-only rehearsal](status.md#e63-service-only-rehearsal-and-graphical-session-dependency)
recorded a canceled SteamVR stop job: the shipped Gamescope session is
`PartOf=steamvr.service`, so the graphical session also stopped and restarted.
Independent cleanup observed a healthy compositor afterward without another
service command. The bare-service preflight blocks this policy. The SDDM
restart owner and vendor session interface are now reviewed; a full-session
test bundle and inactive metadata bundle are prepared, with 158 native/synthetic
tests passing on Frame. The operator's platform session test has now completed
successfully, with normal headset appearance confirmed. The new metadata-only
activation/restoration runner loaded the provider and restored normal startup
on its first device run. It received 20 metadata records, including eight
candidate-profile matches, but failed because the registry changed. The v2
bounded settling check has now run: its three snapshots contain identical
metadata for the same eight candidate IDs, while other allocations appear and
disappear. Full-registry inspection still failed. Software restoration and
normal UI/passthrough/tracking are confirmed. Do not repeat v2 unchanged.
Image access remains blocked. A local synthetic pipeline now exercises owned
latest frames, CPU recognition and native overlay validation with clean shutdown;
this is offline preparation, not live camera access or headset rendering.
Actual driver imports and camera identity remain unvalidated; metadata-session
restoration is confirmed, not GPU ownership or synchronized capture. Bounded recognition and snapshots shown after
capture remain useful. The model's class coverage and false positives need separate work.
The [isolated shared-memory reader](on-device.md#check-existing-luma-shared-memory)
also passes controlled x86_64 and ARM64 tests; no dedicated capture provider has
been deployed. Built-in camera capture needs separate validation. The
[dated reference review](capture-reference-review.md)
explains the strategy; [architecture](architecture.md) describes components,
frame ownership and longer-term acceptance criteria.
Its [model replacement guide](architecture.md#recognition-implementation-and-model-replacement)
explains the shared detector, candidate models and evaluation procedure.

## License

Original project code and documentation are covered by the [MIT license](../LICENSE).
External packages and downloaded fixtures retain their upstream terms; see
[third-party sources](../THIRD_PARTY.md). Framejacker is an independent project and
is not affiliated with Valve or Arcturus Vision.
