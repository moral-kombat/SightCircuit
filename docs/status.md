# Project status and evidence

Documentation review: 2026-10-24. This is the authoritative record of current
capability and validation evidence. The review date is not a new device-test
date. Detailed test dates, installed code revisions and SteamVR versions are
unknown unless explicitly recorded below.

## Capability matrix

| Capability | State | Evidence and limits |
| --- | --- | --- |
| Linux development setup and smoke check | Cloud-tested | Linux x86_64, Python 3.12; generated repeated-image video, CPU detection and offscreen rendering; [E7](#e7-cloud-validation) |
| Read-only device and recording inspection | Device-confirmed | Inventory and MP4 header reports supplied by the user; device presence does not prove capture; [E1](#e1-frame-inventory), [E4](#e4-recordings-and-container-metadata) |
| Vendor Arcturus app live preview and recording | Device-confirmed | User reports the vendor app works; it does not supply frames to Framejacker; [E2](#e2-vendor-app) |
| Selected-track HEVC decoding and RGB conversion | Device-confirmed | Three frames decoded from video ordinal 0; not full-file or stereo validation; [E5](#e5-selected-track-decode) |
| Annotated Qt replay window | Device-confirmed | Latest run presents/processes 576 frames and reports four labels; label accuracy remains unevaluated; [E47](#e47-recorded-recognition-and-vr-panel-poc-on-frame) |
| Native head-relative VR replay panel | Device-confirmed, including latest POC | User confirms visible panel/labels and normal passthrough; 576 images submitted with clean shutdown and no forced termination; [E47](#e47-recorded-recognition-and-vr-panel-poc-on-frame) |
| Public OpenVR tracked-camera probe | Device-confirmed failure | No frames and observed passthrough interruption; do not repeat the unchanged test; [E3](#e3-public-camera-probe) |
| Continuous live input in the Qt/VR application | Planned; current mirror includes our panel | The timed VR panel appeared in the saved mirror image; use input independent of our output before continuous VR image display; [E18](#e18-vr-panel-included-in-mirror-input) |
| Recognition on the saved room still | Frame execution confirmed; false positive | One image processed and annotated; user identifies the `sofa`-labeled item as a rug; [E15](#e15-saved-room-recognition-and-rug-false-positive) |
| Bounded mirror recognition | Frame POC execution and sampled preview localization confirmed | Latest run: 100 captured / 51 processed reads, 4.993 processed fps, 40.065 ms mean inference, clean shutdown; user confirms matching preview labels/boxes and normal passthrough/tracking during/after; broader accuracy and continuous display remain separate; [E48](#e48-live-mirror-recognition-poc-and-matching-preview) |
| Real-time recorded replay | Cloud-tested on a synthetic stand-in; Frame measurement pending | Keyframe catch-up and newest-frame inference reach a 0.94 real-time ratio where the previous version reproduced Frame's 0.97 processed fps; [E53](#e53-unattended-replay-regression-and-pacing-rerun), [E54](#e54-keyframe-catch-up-realtime-replay-cloud-checks) |
| Candidate detector and evaluation harness | Cloud-tested; Frame evaluation pending | YOLOX-Nano (80 COCO classes, ONNX Runtime) selectable beside MobileNet-SSD; seed comparison on six outdoor images only; no rug class in either model; [E50](#e50-candidate-detector-and-evaluation-harness-cloud-checks) |
| Existing-mirror diagnostic | Frame capture confirmed; VR panel included in input | Physical-room preview and bounded inference work, but this composited view includes our overlay; physical camera identity/timing remain unknown; [E14](#e14-physical-room-visible-in-the-mirror-preview), [E18](#e18-vr-panel-included-in-mirror-input) |
| Dedicated-camera shared-memory reader | Controlled x86_64 and Frame ARM64 tests passed | All 14 reader/supervisor tests pass on Frame; actual provider and upstream writer ordering remain unverified; [E23](#e23-reference-build-and-controlled-reader-on-frame) |
| Native capture prerequisite report | Frame report received | aarch64 SteamOS 0.5.6, GCC 15.1.1 and Vulkan 1.4.309 development metadata/headers; no capture configuration at checked paths; [E21](#e21-capture-prerequisites-on-frame) |
| Pinned capture reference build | Cloud and Frame ARM64 compile/link checks passed | Unmodified upstream inspection artifacts only; no activation, runtime fixes still required; [E23](#e23-reference-build-and-controlled-reader-on-frame) |
| Descriptor transport adaptation | Controlled cloud and Frame ARM64 checks passed | Adapted components compile/link on Frame and fixture reports seven groups passed; live callbacks and remaining provider work unverified; [E25](#e25-descriptor-transport-on-frame) |
| Provider publication and loop controls | Controlled cloud and Frame tests passed | Exclusive private runtime file, ordered writer and bounded loop controls; all eight generated-data process tests passed on Frame; full GPU/provider lifecycle pending; [E27](#e27-publication-and-loop-controls-on-frame) |
| Bounded Vulkan capability query | Frame-confirmed; queried requirements passed | Turnip Adreno 750, target NV12 modifier has two memory planes, dedicated DMA-BUF import supported; clean query and normal passthrough; no actual FD import; [E30](#e30-vulkan-capabilities-on-frame) |
| Image import validation and ownership | ARM64 build and ten synthetic tests passed; cloud suite now eleven | FD/image ownership and failure paths validated with fake dispatch; added two-plane case cloud-only; real GPU import remains untested; [E31](#e31-import-build-and-synthetic-tests-on-frame), [E32](#e32-two-plane-import-fixture-cloud-checks) |
| Device and remaining GPU resource ownership | Inspection adaptation built; nine synthetic cloud tests passed | Startup/child lifetimes, private allocations, imported-image device retention and 57 injected failures; actual driver and ARM64 execution pending; [E33](#e33-gpu-resource-ownership-cloud-checks) |
| Capture watchdog, provider/layer lifecycle and exact recovery | Earlier v2 milestone passed all 28 generated-data tests on Frame ARM64 | Bounded worker, metadata/GPU provenance, registry changes, owned endpoint and conditional restoration; no real driver/layer execution or device restoration; [E35](#e35-watchdog-provider-and-recovery-tests-on-frame) |
| Metadata-only provider and producer synchronization review | v3 ARM64 build and all 24 generated-data tests passed on Frame | Layer retains/exports no image FDs; no real layer activation or live camera access; controlled load/restoration remains next; [E36](#e36-metadata-only-provider-and-synchronization-review), [E37](#e37-metadata-only-provider-and-diagnostic-on-frame) |
| Inactive v3 bundle and SteamVR service preflight | Frame report received | Bundle prepared without activation; active simple/control-group user service, 20-second stop timeout, no drop-ins; [E38](#e38-inactive-bundle-and-steamvr-service-on-frame) |
| Bounded metadata-layer trial and post-exit recovery | Activation paused; runtime observation passed on Frame | Service/cgroup/compositor liveness check succeeds; user confirms working passthrough during/after. Revised controller has 65 targeted cloud tests; independent SSH and actual recovery remain unverified; [E46](#e46-read-only-runtime-baseline-on-frame) |
| Dedicated Arcturus Vulkan capture | Upstream-reported | Community implementation reviewed; no Framejacker deployment or device capture result |
| Built-in passthrough camera input | Unverified | Upstream reports empty built-in-camera-sized buffers; no source-identified built-in-camera frames have been received |
| Stereo depth and world-space labels | Planned | Calibration, depth and pose/timebase alignment remain unresolved |
| Gaussian splat reconstruction and viewing | Optional future work | Recorded-data workflow researched; training and headset splat rendering not validated by this project |

Implemented tooling, cloud tests, user-confirmed device behavior and upstream
reports are different evidence categories. A successful API call, existing
device node or reviewed implementation does not establish live frame access.

## Evidence record

These entries consolidate user reports previously repeated in the device and
camera guides. The original reports were supplied in the development
conversation; raw device logs and recordings are not bundled in the repository.
Cloud source/tests are linked where available. Do not treat a source link as
proof of a fresh execution.

### E1: Frame inventory

Source: user-supplied `device_check.py` report. Exact execution date and checkout
revision were not recorded. The baseline reports `aarch64`, SteamOS 0.4.5 build
`20261007.6125817`, Python 3.12.3, an active `steamvr.service`, native
`g++`/CMake/Ninja tools, a graphics render node and `arcimx616` sensor-driver
nodes. The report also lists `/dev/video99` with name `SteamVR`; its virtual
sysfs path and working mirror frames were not established by that inventory.
No exact SteamVR version was supplied.

This establishes the reported execution context. An active service, render node
or camera node does not prove camera access, GPU capture or access to the
compositor session. Later UI confirmations did not include a fresh inventory;
do not silently attach a new runtime version to them.

### E2: Vendor app

Source: user observation; exact test date, app version and SteamVR version
unknown. The official Arcturus Camera app shows live passthrough and can record
video. This demonstrates working vendor capture on that device. It does not
establish a public live-frame API or Framejacker capture support.

### E3: Public camera probe

Source: user-provided probe reports before and after toggling passthrough, plus
the user's observation of the displayed view. Exact execution date and tested
binary revision unknown. Both reports show:

- Background OpenVR initialization succeeds and `IVRTrackedCamera_006` is available.
- The HMD is connected; `HasCamera=true`, camera count is zero and frame layout
  returns `TrackedProp_UnknownProperty` (`4`).
- All three frame-size queries return `VRTrackedCameraError_OperationFailed`
  (`100`), before and after acquisition. Intrinsics calls also fail.
- Acquisition returns success with a nonzero handle, but header-only retrieval
  and release return `VRTrackedCameraError_InvalidHandle` (`101`). The schema-1
  `handle_valid` field means nonzero only; it does not prove runtime acceptance.
- No valid frame is copied. The supplied report ends with `cleanup_failure`
  and exit code `1`.

The user reports **passthrough goes black while the probe runs**, while the
vendor app works outside the test. This associates disruption with the probe
as a whole; it does not identify the responsible call, prove a hardware fault
or identify an Arcturus/built-in source because no frames were received.

Do not repeat the unchanged headset test. The current binary makes no OpenVR
calls by default and requires `--allow-passthrough-interruption` for a deliberate
future retest after a material change. The guard is implemented in
[camera_probe.cpp](../tools/camera_probe.cpp); the
[advanced device procedure](on-device.md#advanced-probe-only-for-a-deliberate-future-retest)
documents its scope and exit codes.

### E4: Recordings and container metadata

Source: user-supplied file listing and metadata-inspector output. Two completed
recordings were listed in `~/Videos/ArcturusVisionCamera`:

| Recording | Bytes |
| --- | --- |
| `2026-10-15_21-20-34_stereo.mp4` | 15,923,629 |
| `2026-10-15_21-17-18_stereo.mp4` | 149,778,747 |

The filenames identify the recordings, not the dates of later decode/UI tests.
No `ffprobe` executable was reported on PATH. On the smaller recording,
`recording_check.py` reports two `hvc1` HEVC video tracks at 2464 × 2464, one
`gpmd` metadata track, one `mp4a` audio track, fragmented MP4 structure and no
encrypted sample entries.

Its `unsupported: fragmented_mp4` result describes the inspector's limited
sample/timeline handling, not a corrupt-file diagnosis. Video `mdhd` headers
report 0.515174 seconds; the complete fragmented timeline has not been
validated. Header inspection skips media payloads and does not decode images,
validate metadata payloads/calibration or establish left/right track identity.

### E5: Selected-track decode

Source: user-provided `recorded_video_decode` JSON. Exact test date and checkout
revision unknown. PyAV 16.1.0 successfully decoded three frames from the smaller
recording: video-stream ordinal `0`, container stream index `0`, track ID `1`,
codec HEVC.

All three frames were 2464 × 2464, converted from full-range `yuvj420p` to
`rgb24`, with a 7,392-byte RGB stride. Reported media timestamps were `0`,
`0.016787` and `0.033532` seconds. The report states `status: decoded`,
`frames_decoded: 3` and `images_saved: false`.

This proves selected-track decoding and RGB conversion on Frame. It does not
prove the entire file, second-track decoding on Frame, stereo synchronization,
source identity, HDR appearance or real-time performance. The
[decode procedure](on-device.md#decode-one-recorded-video-track) explains how to
repeat this bounded file check without calling OpenVR.

### E6: Window and VR panel

Source: user observations. Exact test dates, installed checkout revisions and
SteamVR versions unknown. The user confirmed the annotated Qt replay window
works, then reported that the ten-second native panel test worked. For the
replay panel the user stated:

> The controls and video playback all worked, as well as the image that followed my head.

This establishes basic visible replay, working controls and head-relative
placement. It does not establish detection accuracy, label readability, color
fidelity, measured latency/rate, every individual control setting or detailed
tracking/compositor impact. No native cleanup JSON for those observations is
included here; a reported timed-panel pass is distinct from a saved cleanup log.

The native replay-panel implementation was introduced in commit `c22b5c8`.
That is an implementation reference, not proof of the exact installed revision
during the user's observations. See the [overlay guide](vr-overlay.md) for
launch, controls, reports and shutdown.

### E7: Cloud validation

Source: local implementation checks reported during development on Linux
x86_64, separate from the user's Frame. Setup and smoke checks exercised
generated repeated-image video, CPU MobileNet-SSD detections, Qt offscreen
rendering and a separate ONNX arithmetic example. Decoder/window/native
transport tests and native builds were also reported passing during their
implementation. They have not been rerun as hardware tests by this
documentation review.

The [development guide](development.md) owns the reproducible commands.
Behavioral checks are in [recording tests](../tests/test_recording_check.py),
[decoder tests](../tests/test_replay_decode_check.py),
[window tests](../tests/test_replay_window.py) and
[overlay tests](../tests/test_overlay_bridge.py). Tests skip when their optional
dependencies or native binary are absent; a passing partial suite is not
evidence that skipped components were exercised.

Native validation-only mode explicitly performs no OpenVR initialization and
submits zero headset frames. Missing-runtime tests check diagnostics, not
compatibility with the user's runtime. The
[CI workflow](../.github/workflows/checks.yml) describes configured checks; its
existence and a Git push do not establish a successful GitHub Actions run.

### E8: Mirror diagnostic cloud checks

Execution date: 2026-10-16, Linux x86_64 cloud environment. The new
[mirror diagnostic](../scripts/mirror_check.py) and
[V4L2 reader](../scripts/mirror_capture.py) were exercised with controlled sources
and [behavioral tests](../tests/test_mirror_check.py). No physical or virtual
camera is available in this cloud environment.

Checks cover sysfs-only discovery, physical/unverified-device rejection,
read-only opens and get-only ioctls, native Linux-header ABI agreement, pixel
layout/stride bounds, static/changing/short/missing input, abnormal worker exit,
deadline escalation, SIGTERM cancellation and process reaping. An explicit PNG
is encoded after source closure and published without overwriting an existing
destination; with PyAV installed, a real decoder also opens that PNG.

The default invocation here reports `discovery_only` with
`video_sysfs_absent`; opt-in capture reports `unavailable` and exits `1` without
opening a device. The complete decoder/window/native-transport suite also passed
locally alongside these checks. These results do not demonstrate actual mirror
capture, camera content, compatibility with the Frame's driver, ARM64 execution
or a GitHub Actions result. Follow the
[device procedure](on-device.md#check-an-existing-steamvr-mirror) for those checks.

### E9: Shared-memory reader cloud checks

Execution date: 2026-10-16, Linux x86_64 cloud environment. The
[native reader](../tools/shm_reader.cpp),
[supervisor](../scripts/shm_capture_check.py) and original
[synthetic producer](../tools/shm_fixture.cpp) exercise the pinned
[MRHAND layout](../third_party/mrhand/README.md). No camera provider, Vulkan
layer or SteamVR service was started.

[Behavioral checks](../tests/test_shm_capture_check.py) cover both slots and
pixel/counter association across real processes, deliberately split writes,
odd/incomplete publications, stale/stalled input, malformed dimensions/lengths,
nonregular files, truncation, replacement and observable producer resets. They
also exercise native crash containment, deadlines, SIGTERM process reaping,
new-file preview publication and preservation of concurrent destinations.
With PyAV installed, the generated PNG is decoded by a real image decoder.
The full local replay/window/native-transport suite passed alongside these
checks. This is local validation, not a GitHub Actions result.

The controlled producer includes a barrier after the odd sequence marker;
the reviewed upstream writer still requires ordering review/correction on
ARM64. Reader-side sequence checks cannot repair that writer or establish
completion of the original GPU image. The format also has no producer-generation
field, so a restart that catches up between polls may escape detection.
`writer_ordering_verified` and `camera_frame_integrity_verified` remain false.

The default command here reports `discovery_only`, with the upstream shared
file absent and no read attempted. Native ARM64 execution, real-provider input,
physical source identity and actual camera freshness remain unverified. The
[operating procedure](on-device.md#check-existing-luma-shared-memory) and
[controlled fixture](development.md#isolated-luma-reader-and-synthetic-producer)
describe the distinct next checks.

### E10: Mirror discovery on Frame

Source: user-supplied `steamvr_mirror_check` schema-1 output received
2026-10-16 following the requested Frame check. Exact execution time, installed
checkout revision and current SteamOS/SteamVR versions were not included.
The metadata-only command reports `discovery_only`, exit `0`, with
`candidates_found` / `metadata_match` and exactly one candidate:

| Field | Reported value |
| --- | --- |
| Device node | `/dev/video99` |
| Sysfs name | `SteamVR` |
| Resolved sysfs path | `/sys/devices/virtual/video4linux/video99` |
| Device identity | Major `81`, minor `58` |
| Classification | `steamvr_virtual_mirror_candidate` |
| Unverified named nodes | None |

The report explicitly has `device_open_attempted: false`,
`acquisition_started: false`, zero frames and no saved images. This establishes
a candidate virtual mirror from current sysfs metadata. Driver/capability and
format checks, actual frame delivery, passthrough content, physical source
identity and output-feedback behavior were untested at this point. The
[subsequent trial](#e11-first-mirror-acquisition-trial) records what happened
after opt-in acquisition.

### E11: First mirror acquisition trial

Source: user-supplied `steamvr_mirror_check` schema-1 report received
2026-10-16, followed by confirmation that live passthrough stayed visible during
and after the test. Exact execution time, checkout revision and SteamVR version
were not included. This used the initial reader with readiness polling before
its first `read()`.

The verified `/dev/video99` opened successfully and passed capability/format
checks:

| Field | Reported value |
| --- | --- |
| Driver / card | `v4l2 loopback` / `SteamVR` |
| Bus | `platform:v4l2loopback-099` |
| Reported driver version integer / capability flags | `397824` / `85983233` (`0x05200001`) |
| Format | 1920 × 1080, `RGB3`, progressive field `1` |
| Row stride / image buffer | 5,760 / 6,220,800 bytes |
| Read-loop elapsed time | 10,000.142 ms |
| Complete / short frames | `0` / `0` |
| Result / diagnostic exit | `no_frames` / `1` |
| Worker exit / forced termination / cleanup | `0` / `false` / `true` |
| Saved images | None |

An open device and queried format do not establish delivered frames. The
worker exited normally; diagnostic exit `1` reflects zero frames. The user
explicitly confirmed passthrough remained working during and after this test.
The installed loopback module version and underlying physical source remain
unknown.

Review of [upstream v4l2loopback v0.14.0 file-I/O startup](https://github.com/v4l2loopback/v4l2loopback/blob/v0.14.0/v4l2loopback.c#L2385-L2439)
and [poll handling](https://github.com/v4l2loopback/v4l2loopback/blob/v0.14.0/v4l2loopback.c#L2263-L2306)
identified a reader bug: waiting for readiness before the first nonblocking
read can prevent file-I/O capture registration. That upstream source comparison
does not identify the installed module revision or prove it caused this result.
The reader now calls nonblocking `read()` directly at the existing bounded
rate and reports read attempts, would-block results and interruptions. A
controlled lazy-start regression check passes; the full local suite also
passes. Device validation was pending at this point; the
[subsequent corrected-reader result](#e12-successful-mirror-frame-delivery)
confirms frame delivery. This first result does not establish that the mirror
has no usable producer.

### E12: Successful mirror frame delivery

Source: user-supplied `steamvr_mirror_check` schema-1 report received
2026-10-16 after the corrected-reader command was provided. The report identifies
`reader_method: nonblocking_read_without_poll_gate`; exact checkout revision,
execution time and SteamVR version were not included. It reports:

| Field | Reported value |
| --- | --- |
| Source | `/dev/video99`, virtual `SteamVR`, major `81`, minor `58` |
| Driver / bus | `v4l2 loopback` / `platform:v4l2loopback-099` |
| Format | 1920 × 1080 `RGB3`, progressive, 5,760-byte stride, 6,220,800-byte buffer |
| Result / diagnostic exit | `captured` / `0` |
| Complete frames / read attempts | `100` / `100` |
| Would-block / interrupted / short reads | `0` / `0` / `0` |
| Changed / unchanged grayscale samples | `99` / `0` |
| Read-loop elapsed / observed rate | 10,000.214 ms / 10.0 reads per second |
| First frame / maximum observed read gap | 2.446 ms / 103.707 ms |
| Worker elapsed / exit | 10,065.699 ms / `0` |
| Cleanup / forced termination / saved images | `true` / `false` / none |

This establishes complete image delivery through the corrected read-only
mirror path on Frame and normal worker cleanup. The 10 Hz rate is capped by
our diagnostic, not a measurement of maximum producer/camera rate. First-frame
delay is measured from the reader loop, not camera exposure or end-to-end
latency. Changing sampled pixels do not identify the camera or establish
exposure freshness.

No image was supplied or saved by this command, so actual passthrough scene
content, physical camera identity, orientation and recursive capture of our
display remain unverified. Passthrough continuity was confirmed for the
[previous trial](#e11-first-mirror-acquisition-trial); it has not yet been
explicitly reported for this successful read trial. Next inspect an explicitly
requested grayscale preview after acquisition closes, with our replay window
and VR panel closed. Keep detector/VR integration separate until input content
and output feedback are understood. The [next preview trial](#e13-mirror-preview-publication-on-frame)
confirms file publication without yet identifying image content.

### E13: Mirror preview publication on Frame

Source: user-supplied terminal output received 2026-10-16. Exact test time,
checkout revision and SteamVR version were not supplied. The user ran the
corrected mirror reader with explicit `--preview`, followed by `xdg-open` only
after the capture command succeeded.

The report again records `captured`, exit `0`, 100 complete RGB3 frames in ten
seconds, 99 changed samples, 100 read attempts and zero would-block, interrupted
or short reads. The source/format matches E12. Read-loop elapsed time was
10,000.163 ms, first frame 2.83 ms and maximum observed read gap 102.242 ms.
Worker elapsed time was 10,123.424 ms, with exit `0`, no forced termination and
`cleanup_ok: true`.

`preview_ready` and `images_saved` are both true. The explicit output is gray8,
640 × 360, at
`/home/steamos/framejacker/artifacts/mirror-check/scene-check.png`.
The pixels have not been supplied to this development environment. File
publication is confirmed by the report; physical scene content remains unknown.

After capture completed, Chrome's launcher printed “Opening in existing browser
session” and a zygote `write: Broken pipe (32)` error. This message belongs to
the separate viewer launch and does not invalidate the successful capture/file
report. The user subsequently confirmed that a Chrome window opened without
the image and interpreted `artifacts` as a possibly mistyped web address.
The next viewer check used an explicit absolute local-file URL; the
[subsequent user confirmation](#e14-physical-room-visible-in-the-mirror-preview)
identifies the room in that image. Passthrough continuity for this capture
trial, source identity, orientation and output feedback remain unverified.

### E14: Physical room visible in the mirror preview

Source: user observation received 2026-10-16 after opening the saved PNG through
an absolute local-file URL. The user states:

> I see a photo of the room in black and white.

This confirms visible presentation and recognizable physical-room content in
the mirror-derived still from E13. The grayscale appearance is expected:
`mirror_check.py --preview` deliberately converts its sampled output to gray8,
although the acquired V4L2 format is RGB3. This does not identify which physical
camera supplied the scene or establish that the source itself is monochrome.
The pixels have not been provided to the cloud environment for inspection.

A single recognized scene does not establish continuous physical-motion
freshness, detailed orientation/cropping, calibration, producer/exposure timing,
or exclusion of our window/panel from the composited input. Capture cleanup is
confirmed by E13; passthrough continuity during/after that particular trial has
not been explicitly reported. Existing replay/VR confirmations remain separate.

A separate cloud check on 2026-10-16 at checkout `74dc1b8` exercised the existing
`replay_window.py` with a grayscale PNG made from the checked development
fixture, `--max-frames 1`, Qt offscreen and an explicit result PNG. It processed
and presented one frame, returned bicycle/car/dog labels, saved a decodable
annotation image and requested no overlay. This confirms the existing tool
can perform a bounded still-image recognition check; it is not a recognition
result on the user's room image or on Frame. The next device step uses the
saved room PNG after capture has stopped. The [subsequent device result](#e15-saved-room-recognition-and-rug-false-positive)
confirms execution and records a false positive.

### E15: Saved-room recognition and rug false positive

Source: user-supplied `annotated_file_replay` JSON and observations received
2026-10-16 after the one-image command. Exact execution time, checkout revision
and SteamVR version were not included. The report has `status: finished`, one
processed/presented frame, `labels_seen: ["sofa"]`, `images_saved: true`,
`overlay_requested: false` and `qt_platform: offscreen`.

The user saw a green box and `sofa` score around an item identified as a **rug**.
This is a false positive, not a validated sofa detection. The demo's 20-class
label set includes sofa and has no rug class. The user described a score around
0.7; if the old annotation read `0.70`, that is a 70% model confidence score,
not 0.7% and not a calibrated probability of correctness. Labels now use explicit
percentage formatting; this presentation change does not improve model accuracy.

Qt printed `This plugin does not support propagateSizeHints()` while rendering
offscreen. The successful report and the visible annotation establish that
this warning did not prevent processing/output. Chrome again printed launcher
and zygote messages; the user nevertheless saw the annotated result. Neither
message supplies evidence of a camera failure. No live capture or overlay was
requested by this command.

This validates the saved-image processing/display path on Frame while exposing
a model-quality limit. It does not validate correct recognition, continuous
live inference, physical camera identity or display-feedback exclusion.

### E16: Bounded mirror recognition cloud checks

Execution date: 2026-10-16, Linux x86_64 cloud environment. The new
[mirror recognizer](../scripts/mirror_recognize.py) uses the existing guarded
capture loop and a [shared checksum-verified CPU detector](../framejacker/detection.py).
The replay application uses that same detector. No actual camera, SteamVR
service, Qt window or VR panel was started by the recognizer checks.

[Behavioral tests](../tests/test_mirror_recognize.py) exercise metadata-only
defaults, argument guards, one pending input, RGB/BGR order and stride padding,
slow inference with replaced inputs, exact source/result association, stale or
empty input, model/read/close failures and cancellation. Spawned-worker checks
run the real model on the checked image fixture, require bicycle/car/dog labels,
encode only after source closure, decode the resulting PNG, and preserve
existing/concurrently created destinations. Existing replay, mirror, shared-memory
and overlay-transport tests also pass locally. This is not a GitHub Actions
result or a Frame inference/performance test.

The command targets at most five processing starts per second, reads at most
ten times per second and retains one pending mirror read. Reports carry local
read sequence and observation time with the final detections; those are not
producer sequence or exposure timestamps. It opens no live display and saves
no images by default. Optional final annotations are published after successful
capture/inference shutdown. The [Frame procedure](on-device.md#run-bounded-recognition-from-the-mirror)
describes the device test. The [subsequent Frame result](#e17-bounded-mirror-recognition-on-frame)
confirms execution; the rug false positive remains unresolved by this
capture/inference implementation.

### E17: Bounded mirror recognition on Frame

Source: user-supplied `steamvr_mirror_recognition` schema-1 report received
2026-10-16, followed by “Pass through, stay normal and accurate.” Exact test
time, checkout revision and current SteamVR/SteamOS versions were not supplied.
The recognizer ran with no live display or overlay and reported:

| Field | Reported value |
| --- | --- |
| Result / exit / cleanup | `recognized` / `0` / `true` |
| Source | Virtual SteamVR `/dev/video99`, major `81`, minor `58` |
| Input | 1920 × 1080 RGB3, 5,760-byte row stride, 6,220,800-byte buffer |
| Captured reads / read-loop duration | `100` / 10,000.239 ms |
| Capture rate / maximum read gap | 10.0 Hz / 101.893 ms |
| Would-block / interrupted / short reads | `0` / `0` / `0` |
| Processed reads / processing duration | `51` / 10,214.019 ms |
| Actual processing rate / configured target | 4.993 Hz / 5 Hz |
| Mean / maximum inference time | 42.044 / 82.028 ms |
| Replaced pending reads / skipped stale selections | `49` / `0` |
| Maximum age at processing start | 101.427 ms since reader observation |
| Final result / age after cleanup | Local read `100` / 301.606 ms since observation |
| Labels seen across the run | `chair`, `tvmonitor` |
| Final result labels / detections | Empty / empty |
| Final annotated input dimensions | 960 × 540 |
| Saved preview | 640 × 360 at `artifacts/mirror-check/live-detections.png` |
| Worker elapsed / exit / forced termination | 10,427.535 ms / `0` / `false` |

The capture, inference and owned-image annotation path completed together at
the intended processing rate. Replacing 49 pending reads is expected when
sampling a 10 Hz input at approximately 5 Hz; the application does not build
an input queue. Processing includes completion after the capture loop, so the
51 processed reads cover roughly 10.214 seconds. Read observations and inference
timing do not establish exposure timing or end-to-end camera latency.

The final result has no boxes above threshold even though earlier processed
images contributed chair/tvmonitor to `labels_seen`. That aggregate is not a
list of objects in the saved final frame. In a follow-up received 2026-10-16,
the user confirmed that both a chair and a TV/monitor were in view during the
run. This supports class presence, but does not identify which image regions
were labeled or validate their box placement. No intermediate annotated frames
were retained by this run. The rug false positive from E15 remains part of the
quality evidence. The saved pixels have not been supplied to the cloud.

The user confirms normal, accurate-looking passthrough during this trial. This
is an observation, not measured tracking, thermal, compositor or long-duration
performance data. Chrome again printed viewer-launch/zygote messages after the
successful report; no camera/inference failure is indicated by those messages.
Physical camera identity and continuous scene freshness remain unverified. The
[subsequent fixed-panel check](#e18-vr-panel-included-in-mirror-input) established
that this mirror includes our VR output.

### E18: VR panel included in mirror input

Source: user-supplied terminal output and visual observation received 2026-10-16.
Exact execution time, checkout revision and current runtime versions were not
supplied. The user ran the existing 15-second native test panel alongside a
five-second mirror capture with explicit preview, then reported:

> The top panel appears in the VR and in the captured output. Output's still
> black and white. I didn't see any boxes or object identification labels.

The native bridge initialized `IVROverlay_028`, reported the panel submitted and
`api_visible: true`, then ended `closed` with exit `0`, `cleanup_ok: true` and
one submitted frame. The fixed pattern was generated by the bridge; it received
zero input frames. The mirror report records:

| Field | Reported value |
| --- | --- |
| Result / exit / cleanup | `captured` / `0` / `true` |
| Source / format | `/dev/video99`, virtual SteamVR, 1920 × 1080 RGB3 |
| Complete frames / read attempts / changed samples | `50` / `50` / `49` |
| Would-block / interrupted / short reads | `0` / `0` / `0` |
| Read-loop duration / rate / maximum gap | 5,000.194 ms / 10.0 Hz / 101.181 ms |
| First frame / worker elapsed | 2.559 ms / 5,132.134 ms |
| Worker exit / forced termination | `0` / `false` |
| Saved preview | gray8, 640 × 360, `artifacts/mirror-check/feedback-check.png` |

The user's observation confirms that this mirror includes the visible VR test
panel. Feeding mirror images continuously back into that panel could therefore
capture our displayed output. This is a confirmed inclusion result, not a test
of an actual recursive live-image loop. The fixed pattern avoided that loop.
It does not establish which physical camera supplied the room image.

Grayscale is intentional in `mirror_check.py --preview`. No detector was run by
this command, so no object boxes or labels are expected. The result does not
invalidate E17's successful bounded recognition. Both tools closed cleanly;
passthrough/tracking continuity was not separately reported for this particular
panel-inclusion trial.

Keep the mirror for bounded/no-display recognition or snapshots displayed after
capture closes. Continuous VR image output needs camera input independent of
our panel, or a supported and validated mirror mode that excludes it. The
current mirror path has no such exclusion mode. Do not treat cropping or
hiding the panel as demonstrated general exclusion. Dedicated-provider work
requires a reviewed build and exact configuration restoration before deployment.
The subsequent connection confirmation below does not resolve source selection.

### E19: Arcturus connection confirmed, source selection unknown

Source: user observation received 2026-10-16. The user confirms the Arcturus
Vision Camera is physically connected but is unsure whether it is selected for
passthrough. No fresh runtime inventory or source-identified capture accompanied
this confirmation. Connection is sufficient to prepare the Arcturus build path;
it does not identify which camera supplied E14–E18's mirror images or establish
that a dedicated provider is running. Verify source selection and identity
during the capture-only trial.

### E20: Capture prerequisite report cloud checks

Source: local cloud execution on 2026-10-16 of the prerequisite-report worktree
based on `770f3bf`. Both default inventory and
`device_check.py --capture-prerequisites` emitted valid JSON with their expected
report kinds. The focused report recorded `runtime_initialized: false`,
`capture_started: false` and `files_changed: false`. It reported x86_64,
Debian g++ 14.2.0, absent Vulkan `pkg-config` metadata/known headers and two
runtime-library symlink entries. These are cloud observations, not Frame results.

Controlled checks covered missing tools, command timeout/nonzero status,
missing/inaccessible paths and regular-file/directory/socket/symlink metadata.
The default inventory retained `capture_validation: not_performed`.
Documentation links, existing anchors, shell-example syntax and documented
Python options were checked. No camera, Vulkan-loader, native provider build
or ARM64 execution was exercised in this check. The subsequent Frame report is
recorded below; use [the read-only procedure](on-device.md#check-native-capture-prerequisites)
for a new inventory.

### E21: Capture prerequisites on Frame

Source: user-supplied `capture_build_prerequisites` JSON received 2026-10-16,
with terminal prompt `steamos@frame`. Exact execution time, checkout revision
and SteamVR version were not supplied. The report records:

| Field | Reported value |
| --- | --- |
| Architecture | `aarch64` |
| OS / build | SteamOS `0.5.6` / `20261015.6141207` |
| Kernel | `6.18.0-g83913f38113c` |
| C++ compiler | `g++ (GCC) 15.1.1 20250729` |
| Vulkan `pkg-config` version | `1.4.309` |
| Vulkan headers | `/usr/include/vulkan/vulkan.h` and `vk_layer.h` present as regular files |
| Vulkan linker/runtime path entries | `libvulkan.so` and `libvulkan.so.1` symlinks under `/usr/lib` and `/lib` |
| Known community capture paths | Manifest, layer library, enable marker, runtime socket and luma shared memory absent |
| Runtime initialization / capture / files changed by diagnostic | `false` / `false` / `false` |

The checked paths were the reviewed community locations under the user's
`.local/share`, `.config`, `/run/user/1000/mrhand/cam.sock` and
`/dev/shm/mrhand-pt`. Absence there does not exclude another installation path.
Symlink targets were not resolved; the four library entries are not evidence
of four distinct libraries. This supports proceeding to a native compile/link
check without a routine development-package installation. It does not prove a
working Vulkan device, camera import or active passthrough source.

This OS/build differs from E1's older inventory. Retain the runtime evidence
attached to each earlier test; this report does not revalidate replay, mirror
capture or inference on the newly reported build. Arcturus connection remains
confirmed and source selection remains unknown, as recorded in E19.

### E22: Capture reference build cloud checks

Source: local cloud execution on 2026-10-16 of the reference-builder worktree
based on `8dab44b`. The pinned upstream layer and `ptgrab` compiled/linked on
x86_64 using GCC 14.2.0, Vulkan-Headers v1.4.309 at
`952f776f6573aafbb62ea717d871cd1d6816c387` and the existing Linux Vulkan loader.
Source remains unmodified at capture revision
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`.

The build report records source/artifact SHA-256 hashes and compiler target.
Inspection verified ELF x86_64 headers, the layer negotiation export,
`ptgrab`'s `libvulkan.so.1` dependency and missing execute permissions on both
artifacts. Argument checks and changed-cache refusal passed; a rejected cache
was retained and the previous build report preserved. Neither artifact was
loaded or run, and no layer manifest or enable marker was created.
Fresh-cache downloads retained the pinned sources and license texts. A repeat
build using compiler-search-path headers also passed. The CI artifact-check
command was executed locally; both dependency caches remained clean.

The documented reader build/test sequence also passed all 14 shared-memory
tests in 6.623 seconds with no skips, using synthetic input on cloud x86_64.
A dedicated CI job was added for the reference build; its hosted result has
not been checked. The subsequent ARM64 build/reader result is recorded in E23.
Provider runtime fixes, configuration restoration and camera capture remain pending. See
[the next device procedure](on-device.md#build-the-capture-reference-without-activation).

### E23: Reference build and controlled reader on Frame

Source: user-supplied build JSON and complete unittest summary received
2026-10-16. Exact execution time and checkout revision were not supplied.
The preceding inventory reported SteamOS 0.5.6; this build report supplies
architecture/compiler target rather than a fresh OS or SteamVR version.

The unmodified capture reference compiled and linked on `aarch64`, target
`aarch64-unknown-linux-gnu`, using `system` Vulkan headers and
`-l:libvulkan.so.1`. Capture revision is
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`; all three source hashes match E22's
upstream sources. The reported inspection artifacts are:

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| `reference-libVkLayer_mrhand.so` | 79,536 | `dbe97cd3338254c8dfdd0520bad1f4d22b8439158a848f6bcc5d23de372bec28` |
| `reference-ptgrab` | 77,760 | `88d80d286b0ce2c50bc1b462856f94b0b340693d032f949c6105599c748bd8fa` |

The report states `runtime_initialized: false`, `capture_started: false`,
`layer_installation_performed: false` and `deployment_ready: false`. The builder
reported non-executable inspection artifacts and no loading or execution.
Successful linkage does not demonstrate GPU buffer import or live capture.

The reader and synthetic producer also built natively. All **14 tests passed
in 7.204 seconds**, with no failures or skips. The supplied output includes
both-slot cross-process copies, stale/busy/stalled publications, malformed
layouts and source types, truncation/replacement/restarts, preview publication,
signal cleanup and a stuck-reader deadline. These use controlled generated
input; they do not validate the original upstream writer's ordering, camera
buffer completion or source identity. No live camera test was performed.

### E24: Descriptor transport adaptation cloud checks

Source: local x86_64 cloud execution on 2026-10-16 of the transport worktree
based on `117df82`. The versioned patch applies to a disposable copy of the
pinned layer/consumer, leaving the upstream cache unchanged. Both adapted
components compile and link; the two inspection binaries remain non-executable.
The original unmodified build remains a separate mode and artifact directory.

The original socket fixture passed seven test groups for FD snapshot lifetime,
fragmented records, ancillary validation/closure, metadata/generation/ID bounds,
receive deadlines/EOF, send backpressure/disconnects and a named same-user
connection. It counts descriptors before/after; none leaked. Extra/truncated
rights were rejected repeatedly. The fixture uses generated three-byte `memfd`
payloads, Unix sockets and a private temporary socket directory, with no Vulkan
or camera calls. The layer snapshot duplicates each FD under its registry lock
and freezes a single generation; live Vulkan callbacks were not exercised.

Address/undefined-behavior sanitizer checks also passed the fixture. CI includes
the adapted build and fixture; hosted results have not been checked. The
subsequent ARM64 result is recorded in E25. The transport-only adaptation leaves GPU
import validation, writer ordering, ongoing registry/provider lifetime, shared
memory ownership and layer socket restoration unresolved; it is not ready for
activation. Use [the synthetic device procedure](on-device.md#test-the-transport-adaptation-without-capture).

### E25: Descriptor transport on Frame

Source: user-supplied build report and fixture output received 2026-10-16.
Exact execution time, checkout revision and fresh runtime versions were not
provided. The final fixture JSON was cut off in its `scope` field; the supplied
fields report `status: passed`, `tests_run: 7`, `descriptor_leak_detected: false`,
`runtime_initialized: false` and `capture_started: false`. No shell exit status
was included.

The transport adaptation compiled/linked on `aarch64`, target
`aarch64-unknown-linux-gnu`, with system Vulkan headers and `-l:libvulkan.so.1`.
Capture revision remains `ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`.
Patch SHA-256 is
`db48ff5ff0899ab8bbd9b4b935b7f14559e8c37b0bbc59e8f91d80b1eef5d72c`;
transport helper SHA-256 is
`8f48c48fba25d48fc53931cc5e6c75fd6a59df45bb9d2c68af28d58399068a90`.
The reported artifacts are:

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| `reference-libVkLayer_mrhand.so` | 81,048 | `06ba2f08240141f20b0e6fff43156ba53ce06ae269eeeb5f9e7a9b5b5d4d22e4` |
| `reference-ptgrab` | 80,528 | `a359eb92dd684da328b3f516eabf327adb8d64247eb1d5686d935f60ed17f247` |

The build report records modified upstream sources, no runtime initialization,
capture or layer installation, and `deployment_ready: false`. The builder
reports non-executable inspection artifacts. This supports controlled ARM64
transport compatibility, not execution of the actual layer callbacks, GPU
imports, shared-memory provider or live camera capture. Arcturus selection and
physical source identity remain unresolved.

### E26: Publication and loop controls cloud checks

Source: local x86_64 cloud execution on 2026-10-16 of the publication worktree
based on `ddadeb6`. The build applies both versioned patches to disposable
source and compiles/links the adapted layer and producer. Capture binaries
remain non-executable inspection artifacts, with no layer activation or capture
execution. The publisher/control helper is shared with the original fixture.

All **eight process tests passed in 1.535 seconds**, with no skips. They exercise
the production option parser through the fixture, validate publisher dimensions,
metadata and private paths, preserve existing files/symlinks/FIFOs, and copy real
generated 1024 × 1024 publications through the existing reader in both slots.
The saved generated PGM payloads match their frame counters and uniform values.
Other tests check normal deadline removal, SIGINT/SIGTERM cleanup, replacement
preservation and parent-death SIGKILL/reaping. The killed writer leaves a stale
file; a retry refuses it without overwriting it. Truncation before a write is
rejected by the native validation check; a concurrent truncate race can still
fault a mapping and needs process containment.

All three build modes passed local compilation and artifact/hash/permission
checks; pinned source caches remained clean. The native publication self-check
also passed address/undefined-behavior sanitizer checks. CI was extended to
build the new mode and run its tests; hosted results have not been checked.

These are controlled CPU/shared-memory observations, not a real GPU stream.
ARM64 publisher/control validation was pending at this point; see the subsequent
Frame result in E27. GPU import/resource cleanup, registry
refresh, the external watchdog, layer lifetime and exact restoration remain
pending. The local loop deadline does not establish that every driver call
returns on time. Camera identity, source-buffer completion, color and
calibration remain unverified. Use [the next device check](on-device.md#test-owned-publication-without-capture).

### E27: Publication and loop controls on Frame

Source: user-supplied output received 2026-10-16 from the Frame checkout under
`/home/steamos/framejacker`. Exact execution time, checkout revision and fresh
runtime versions were not included. The complete build-report JSON was not
supplied, so no new artifact hashes are recorded here.

The publication reference and native reader/fixture builders reported completion.
All **eight publication process tests passed in 1.562 seconds**, with no skips:
native publisher/options validation, two-slot generated copies through the real
reader, existing-file/symlink/FIFO preservation, deadline and signal cleanup,
replacement preservation, and parent-death/stale-file handling. This extends
E26's controlled publication evidence to the Frame environment. The build
completion explicitly reports that nothing was installed or run; the subsequent
tests run the original CPU/shared-memory fixtures, not the capture binaries.

This validates generated-data publication/control behavior on Frame, not GPU
import, original camera completion, physical source identity or live capture.
No layer activation or camera trial is established by this result.

### E28: Vulkan capability query cloud checks

Source: local x86_64 cloud execution on 2026-10-16 of the capability-query
worktree based on `69e7261`. The standalone query compiled with clean pinned
Vulkan-Headers `952f776f6573aafbb62ea717d871cd1d6816c387`. Building the tool
does not load Vulkan, execute it or install a layer.

All **eleven tests passed**, with no skips. A test-only fake loader exercises
the actual native query structures/function pointers and verifies supported
and unsupported capabilities, four modifier memory planes, R8/image limits,
bounded counts, error teardown, startup/destruction hangs, native crashes,
cancellation, parent death/reaping, bounded stdout/stderr and default opt-in
guards. These are controlled tests, not GPU observations. The CI workflow now
includes this build/test job; hosted results have not been checked.

Fresh pinned-header fetching and compiler-provided-header builds both passed.
An altered header cache was refused and preserved with existing outputs intact.
Address/undefined-behavior sanitizer checks passed supported, unsupported,
oversized-modifier and driver-error paths against the fake loader.

An explicit query of the installed cloud Vulkan loader reported API 1.4.309,
then `vkCreateInstance` returned `-9` (`VK_ERROR_INCOMPATIBLE_DRIVER`). It exited
`1` without creating an instance and reported cleanup/reaping success, with no
forced termination. This cloud environment supplies no usable driver for the
query; Frame GPU capabilities were unknown at this point (subsequently reported in E30). No logical device, camera,
external-memory import or capture configuration change was made.

The subsequent Frame query is recorded in E30; retain
[the device command](on-device.md#query-vulkan-capture-capabilities) for a changed tool/runtime.
A positive capability report would still leave real-buffer import and producer
synchronization, resource cleanup and complete provider lifecycle unverified.

### E29: Validated import and ownership cloud checks

Source: local x86_64 cloud execution on 2026-10-16 of the import worktree based
on `b379ac7`. This work proceeded while the user was away from the Frame; E28's
device query was pending at this point (subsequently reported in E30).
The capture source pin is unchanged.

The new `--import-fixes` mode applies patches 0001–0003 to disposable source,
compiles/links the adapted layer/producer, and records patch/helper hashes in
`artifacts/capture-import-reference/build-report.json`. All four build modes
passed local inspection artifact/hash/permission checks. Real capture binaries
stay non-executable and were not loaded or executed. Pinned caches remain clean.

All **ten import tests passed**, with no skips. Nine native groups use the same
import/submission helper as the adaptation, fake Vulkan function pointers and
generated `memfd` payloads. They cover metadata before driver calls, modifier
and memory-plane counts, import/image limits, requirement bounds and memory-type
intersection, CLOEXEC/FD transfer, exact dedicated allocation size, failure
rollback, moves/vector relocation, batch rollback and pending-work deferral.
Arrayed images are rejected because the wire record omits per-plane array pitch;
exporter flags/sharing mode are also absent and cannot be verified by this helper.
Reset, submission and completion-wait failures are injected separately. The
tenth check verifies default/invalid invocation guards. FD counts match before
and after each native group; repeated failures leave no fake resources. An
uncertain submission intentionally leaves fake resources until simulated process
teardown; this is not reported as graceful Vulkan cleanup.

The fixture also passed address/undefined-behavior sanitizer checks. The prior
eight publication tests and seven transport groups passed regression checks.
CI now includes the fourth build and ten import tests; hosted results have not
been checked. Vulkan 1.4.309's dedicated-allocation and FD ownership rules were
reviewed against the versioned Khronos specification.

These results validate helper control flow and ownership under synthetic
dispatch. They do not prove real DMA-BUF size/provenance, a compressed-plane
footprint, correct physical GPU/source selection, producer completion or ARM64
execution. Device/bootstrap, host/downscaler/command resource ownership, a
capture watchdog, registry/layer lifetime and restoration remain pending.
No layer installation, camera access or actual external-memory import occurred.
Continue the [offline work](live-capture.md#offline-provider-work-while-the-frame-is-unavailable).
The device checks planned at this point subsequently completed in E30 and E31.

### E30: Vulkan capabilities on Frame

Source: user-supplied ARM64 build/query reports and passthrough observation
received 2026-10-16 (client date). Exact execution time, whole-checkout revision,
OS and SteamVR versions were not supplied. Do not attach the earlier prerequisite
report's versions as fresh runtime observations.

The query built on `aarch64`, target `aarch64-unknown-linux-gnu`, with system
headers. Source SHA-256 matches the published native query:
`857937d1b98178c3b48c48a9dc327b09bcf4a051a1cd0ceace9f1cb27af63644`.
Binary SHA-256 is
`44298e12e8a464ff588b4e664d2331331352309d3256ff692d737b9f08c6b280`.
The build did not load Vulkan or install a layer.

The supervised query reported `queried`, exit `0`, elapsed **9.38 ms**, worker
exit `0`, reaped, no forced termination, and `cleanup_ok: true`. Its instance was
created and destroyed. The user explicitly confirms normal passthrough throughout
and afterward. No logical device, camera, GPU allocation or external-memory
import was started; `deployment_ready` remains false.

| Observation | Reported value |
| --- | --- |
| Loader API | 1.3.296 |
| Physical device | `Turnip Adreno (TM) 750`, integrated GPU |
| Device API / driver version | 1.4.362 / raw `109060195` |
| Vendor / device IDs | `20803` / `1124406273` |
| Required extensions | All four present; none missing |
| YCbCr conversion / cached host-visible memory | Supported / available |
| Graphics queue | Family 0, one queue, flags `15`; family 1 flags `8` is not graphics-capable |
| R8 linear blit and transfer image | Supported; maximum width/height 16,384 |
| NV12 target modifier | `0x0500000000000001`, **two memory planes**, tiling features `9433089` |
| External-image query | `VK_SUCCESS`; importable, DMA-BUF-compatible and dedicated-only |
| External feature / compatible handle masks | `7` / `513` |
| NV12 size / array-layer limits | Maximum width/height 16,384; maximum array layers 2,048 |
| Aggregate queried requirements | `capability_checks_passed: true` |

The loader API, physical-device API and earlier 1.4.309 development-header
metadata describe different components. Driver stderr contains an environment
option-override notice and Gamescope WSI application-info messages alongside
successful query/cleanup. These are not evidence of a failed query or of a
layer installed by Framejacker.

These are advertised physical-device capabilities. The two-plane count does not
identify the physical camera, prove actual buffer layouts or replace the
per-buffer modifier/plane validation. No actual DMA-BUF import, original producer
synchronization, image freshness, color or calibration was tested.

### E31: Import build and synthetic tests on Frame

Source: user-supplied full `--import-fixes` build report and unittest output
received 2026-10-16 (client date). Exact device execution time, whole-checkout
revision and fresh OS/SteamVR versions were not supplied. The build reports
`aarch64`, target `aarch64-unknown-linux-gnu`, system Vulkan headers and
`-l:libvulkan.so.1`; capture revision remains
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`.

The import helper and patch hashes match the published code at `b896d6e`:

| Source | SHA-256 |
| --- | --- |
| `capture_import.hpp` | `12919f20b83f150c3ade05e07f1b921e70f6a07b9d9ba6eb07975192117507db` |
| `0003-validated-image-import.patch` | `ad24fc2ba7d789760961ea819733df2a90978c090bf1d754f9bf53a7d5d99d74` |
| Adapted `ptgrab.cpp` | `c17cd406220dca53ce1cdb25e439925b0109bfba071f070ab9ea586d5cfa2f00` |

| Inspection artifact | Bytes | SHA-256 |
| --- | --- | --- |
| `reference-libVkLayer_mrhand.so` | 81,048 | `06ba2f08240141f20b0e6fff43156ba53ce06ae269eeeb5f9e7a9b5b5d4d22e4` |
| `reference-ptgrab` | 151,440 | `da8661419c21fbdf26ecde60ecf0e1a0f026ec063f2fe9ee4549a413eba9c6bb` |

The build reports no runtime initialization, capture or layer installation,
non-executable inspection artifacts, and `deployment_ready: false`. It does
not run the produced capture code. All **ten synthetic import tests passed in
0.015 seconds**, with no skips. They exercise the same import/submission helper
through fake function pointers and generated memfds; this confirms controlled
ARM64 execution, not an actual GPU import. The subsequent two-plane fixture
extension in E32 was not included in this user-supplied native run.

### E32: Two-plane import fixture cloud checks

Source: local x86_64 cloud execution on 2026-10-16 (client date), worktree based
on `b896d6e`. After E30 advertised two memory planes for the target modifier,
the fixture gained an explicit two-plane success case and a four-plane metadata
mismatch rejection against that driver capability. The existing generic
four-plane supported case remains. Offsets/pitches are synthetic and are not
claimed to match actual Frame camera buffers.

All **eleven import tests passed**, with no skips. The ten native fixture groups
also passed address/undefined-behavior sanitizer checks. The import inspection
build passed and the production import helper/patch remained unchanged; no
Vulkan loader, GPU or camera was used by the fixture. The added eleventh test
has not yet run on Frame. CI discovers it automatically; hosted results have
not been checked.

The GPU query and initial native suite are complete. Continue device/bootstrap
and remaining resource ownership, the capture watchdog and registry/layer
lifetime/restoration before a live camera trial. There is no need to repeat the
unchanged device checks to continue that engineering work.

### E33: GPU resource ownership cloud checks

Source: local x86_64 cloud execution on 2026-10-16 (client date), resource
worktree based on `1334d21`. The new `--resource-fixes` inspection mode applies
four patches to disposable source at the unchanged capture pin. It records
helper/patch/artifact hashes under `artifacts/capture-resources-reference/`.
All five modes compiled/linked and passed inspection artifact/hash/permission
checks; real capture binaries stayed non-executable and were not loaded/run.

All **nine resource tests passed**, with no skips. Eight native groups use the
actual ownership helper with fake Vulkan dispatch and generated memfd data for
import lifetime handling. They cover normal teardown and child-before-parent
ordering, 57 injected failures (three passes over the 19 fallible operations
in the tested startup/private-resource path), compatible-device/graphics queue
selection, ambiguous devices, API/enumeration/size/budget checks, coherent host
preference/noncoherent fallback, memory bit 31, moves and child-retained devices.
Imported images retain their logical device across parent scope exit and moves
between contexts. Deferred API destruction is tested both while work remains
unconfirmed and after a caller subsequently marks the queue idle; the poisoned
state stays unusable. Fake process reclamation is not reported as graceful
Vulkan cleanup.

The complete native fixture passed address/undefined-behavior sanitizer checks.
The prior eleven import tests, eight publication tests and seven transport
fixture groups passed regression checks. Pinned caches stayed clean. CI now
builds the fifth mode and runs its tests; hosted results have not been checked.

The new helper owns instance/device/pool, mapped host buffers, private scratch
images, memory and command/fence pairs. Imported-image wrappers retain the
same device state. Private allocations have a 256 MiB aggregate budget; source
shared camera allocations remain separately bounded. Static probe resources
are removed in this adaptation. The earlier import-only mode remains available
and does not include this resource ownership work.

These are synthetic control-flow/ownership results, not real logical-device
startup, GPU commands, camera imports or new ARM64 evidence. Bootstrap capability
selection does not establish exporter GPU provenance. The capture watchdog,
provider metadata/registry, layer lifetime/restoration and producer synchronization
review remain required before a live trial. No layer was installed or activated.
Continue with [capture worker supervision](live-capture.md#next-engineering-step-capture-worker-supervision).

### E34: Capture watchdog, provider lifecycle and recovery cloud checks

Source: local Linux x86_64 cloud execution on 2026-10-17 UTC, worktree based on
`3a02fba`. All six reference/adaptation modes compiled and linked. The new
`--provider-fixes` mode applies five patches to disposable source at the
unchanged upstream pin. The real producer/layer remain non-executable inspection
artifacts, were not loaded/run, and have `deployment_ready: false`. Hashes are
recorded in `artifacts/capture-provider-reference/build-report.json`.

The new suites passed **28 tests with no skips**:

- **10 capture supervisor tests:** real native exec boundary plus a generated
  worker, covering startup/import/copy/destruction stand-in hangs, graceful
  deadline retirement versus forced kill, bounded/corrupt output, crashes,
  SIGINT/SIGTERM, parent death before worker setup, reaping, exact publication
  recovery and preservation of unknown/replaced files.
- **11 provider/layer tests:** original registry/socket helpers and actual layer
  hooks with fake downstream driver calls. They cover exporter metadata and
  GPU/driver UUID validation, device-scoped handle collisions, snapshot FD
  ownership, nonreused IDs, binding/image/device generation changes, provider
  loss/restart/invalidation, bounded registry, idle-client shutdown, endpoint
  ownership, failed Vulkan calls, callback lifetime and the opt-in/crash guard.
- **7 configuration/recovery tests:** inactive preparation, exact prior bytes
  and mode restoration, new/partial/repeated restoration, later-edit protection,
  symlink/hardlink/FIFO/parent-symlink refusal, checksums/home identity, and
  recovery of a deliberately killed synthetic provider while preserving living
  or replaced endpoints.

The complete capture-related suite passed **67 tests**, including prior GPU
query/import/resource/publication coverage, with no skips. The seven native
transport fixture groups passed. Provider and actual-hook fixtures also passed
address/undefined-behavior sanitizer checks. Build reports/artifact hashes,
non-executable permissions, pinned-cache cleanliness and documentation links/
examples were checked. CI now builds the new tools and runs these suites;
hosted CI results have not been checked.

The worker watchdog covers driver startup through destruction, with parent-death
containment established before exec. Private per-run leases record process start
times and publication file identities; recovery never guesses ownership from a
filename. The provider has a separate v2 packet ABI and private endpoint, records
exporter creation/allocation metadata and UUIDs, and fails on changed generations
or provider loss. Its server thread is owned/stopped, callback state is shared,
and registry handles are scoped by logical device. Configuration preparation
writes only an inactive local bundle; conditional restoration changes only the
exact prior/staged Framejacker files and cannot unload an already running layer.

These are synthetic control-flow/ownership results, not a real logical-device
or camera import/copy, physical camera identification, native ARM64 execution,
layer installation, headset restoration or a new passthrough observation.
Generation checks do not lock XRService's writes. The reference still supplies
no original producer fence/exposure sequence; stable-strip readiness and our own
queue completion do not prove complete camera frames. ID-sorted slot grouping
does not prove stereo roles. Capture binaries remain disabled while the
producer synchronization/FOREIGN ownership review and controlled device
load/restoration procedure are prepared. The next no-camera native milestone
is [documented here](on-device.md#test-the-watchdog-and-provider-without-capture).

### E35: Watchdog, provider and recovery tests on Frame

Source: user-supplied native build report and complete test summaries, received
2026-10-17 UTC. Exact execution time, checkout HEAD, current SteamVR version and
OS/kernel/compiler versions were not included. The reported provider/layer
helper and patch hashes match published commit `097f9ba`; this identifies the
earlier **v2** adaptation, not the later metadata-only v3 code.

The build reports `aarch64`, compiler target `aarch64-unknown-linux-gnu`, system
Vulkan headers, `-l:libvulkan.so.1` and upstream capture pin
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. It completed without installation,
runtime initialization or capture. Layer/producer remain non-executable
inspection artifacts and `deployment_ready` is false.

All **28 tests passed with no skips**: 10 watchdog tests in 3.479 seconds,
11 provider/layer tests in 0.130 seconds and 7 configuration/recovery tests in
0.069 seconds. They exercise generated workers/files/local peers and fake
downstream Vulkan dispatch. Restoration tests use temporary generated homes;
they do not restore an installed headset layer.

Reported ARM64 artifact hashes:

| Artifact | SHA-256 | Bytes |
| --- | --- | --- |
| `reference-libVkLayer_mrhand.so` | `afc51be3a62eb7e4d23ecbc2274645f16eb5dd3c18438fab5941736146ea20cd` | 157,376 |
| `reference-ptgrab` | `b51620d2541bf646f94fa0df5b47424a9a5ca762bd70b01ef7d8b8e92b6679f2` | 156,872 |

This extends the v2 synthetic milestone to ARM64. It establishes neither
actual GPU import/copy, completed original camera frames, physical source,
layer activation nor on-device restoration. No new passthrough observation was
supplied with this report. The separate GPU-resource and added two-plane suites
were not included in this batch. Continue with the
[changed v3 native tests](on-device.md#test-metadata-only-inspection-without-capture).

### E36: Metadata-only provider and synchronization review

Source: source/specification review and local Linux x86_64 cloud checks on
2026-10-17 UTC, worktree based on `097f9ba`. The
[synchronization review](live-capture.md#producer-synchronization-review-and-next-checkpoint)
records versioned Vulkan/Linux references. Pinned capture code has no
coordinated producer release/ready/return interval, assumes imported image
layout and lacks a matching return transfer. It also lacks the staging
transfer-write to host-read dependency. Upstream HEAD
`8852ec9bdd4f81fb5a359e5bf88ed25f0e6fc89e` was checked: all three capture source
files plus `REPORT.md`, `FINDINGS.md` and `README.md` are byte-identical to the
existing pin. The pin is unchanged; no code from that newer revision is adopted.

The latest `--provider-fixes` mode applies a sixth patch that replaces the
camera reader with a pre-runtime access gate. The production layer records
metadata without retaining image FDs. Its explicit v3 handshake accepts
metadata requests and rejects buffer requests; old protocol peers and
unexpected received descriptors fail closed. `capture_supervisor.py --capture`
returns `blocked`/exit 3 without starting a worker.

The standalone metadata inspector uses shared profile validators and bounded
registry checks without linking/loading Vulkan, reading image data, saving
images or installing anything. It reports `capture_contract_available: false`;
profile compatibility and registry stability do not establish complete frames.
The v3 provider suite has 12 tests and the metadata diagnostic has 12 tests.
The complete capture-related regression passed **80 tests with no skips**.
Provider and actual-hook fixtures passed address/undefined-behavior sanitizer
checks. The provider inspection build, artifact hashes/permissions, no Vulkan
dependency for the inspector, pinned-cache cleanliness and documentation
links/examples were checked. CI includes the new diagnostic suite; hosted
results have not been checked.

These remain generated-data and fake-dispatch checks. At this cloud checkpoint,
v3 had not yet run on Frame; the later ARM64 result is [E37](#e37-metadata-only-provider-and-diagnostic-on-frame).
No real layer was loaded, image imported/copied, service restarted or
device configuration restored. A controlled metadata-layer load is the next
device checkpoint after native v3 validation and service-aware recovery
preparation. Live pixels still require a supported producer handoff or a copy
inside a demonstrated synchronized compositor access window.

### E37: Metadata-only provider and diagnostic on Frame

Source: user-supplied native build report and complete test summaries, received
2026-10-17 (America/New_York). Exact execution time, checkout HEAD, current
SteamVR version and OS/kernel/compiler versions were not included. All 12
reported source hash/size records and all six patch records match the published
v3 build at `2675af3`. This identifies the build inputs; it does not establish
the entire checkout revision or the test files' hashes.

The build reports `aarch64`, compiler target `aarch64-unknown-linux-gnu`, system
Vulkan headers, `-l:libvulkan.so.1`, and unchanged upstream capture pin
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. Protocol version is 3;
`image_fd_export_enabled` and `layer_retains_image_fds` are false. Runtime
initialization, capture, layer installation and deployment readiness are false.
The layer and access-gate artifacts remain non-executable inspection outputs.

All **24 tests passed with no skips**: 12 provider/layer tests in 0.627 seconds
and 12 metadata-diagnostic tests in 6.926 seconds. The former includes metadata
without image FDs and blocked buffer requests. The latter exercises the native
inspector against generated peers: matching/empty/unsupported metadata, registry
change/provider loss, old protocol/unexpected descriptors, output validation,
deadlines, cancellation and parent death. Driver calls in the layer fixture
are fake; no real camera provider is contacted by these tests.

Reported ARM64 artifact hashes:

| Artifact | SHA-256 | Bytes |
| --- | --- | --- |
| `reference-libVkLayer_mrhand.so` | `0252b2e7e974af367a1f586c0a3f3dceca2a9456164b25e1c9a97cf411864210` | 157,200 |
| `reference-ptgrab` | `a9f788769f1731ac5b6ee011314e8663ada9817c9def49297b657d48a2dbf7ce` | 73,256 |
| `framejacker-provider-inspect` | `51f4bcee93cd2150dc8698b36675c209b9a8140b7463fbd14753842fc554996c` | 77,704 |

This completes native v3 generated-data validation. It does not establish a
loaded layer, actual camera metadata, completed camera frames, GPU import/copy,
physical camera identity or device configuration restoration. No new passthrough
observation accompanied the report; prior v2 watchdog/recovery tests were not
rerun in this batch. The next preparation is an inactive v3 bundle and the
actual SteamVR service properties, followed by an independently recoverable
metadata-layer trial. Live image reads remain blocked pending a producer
handoff or synchronized compositor-owned copy.

### E38: Inactive bundle and SteamVR service on Frame

Source: user-supplied `capture_layer_configuration` report and filtered
`systemctl --user show steamvr.service` output, received 2026-10-17
(America/New_York). Exact execution time and checkout/runtime versions were not
included. Bundle `/home/steamos/framejacker/artifacts/capture-layer-plan-v3-new`
was prepared successfully, with activation/restart both false. Its contents
were not uploaded; no layer load or restoration is established by this report.

The service is loaded, active/running, `Type=simple`, `KillMode=control-group`,
`TimeoutStopUSec=20s`, `MainPID=2517`, with fragment
`/usr/lib/systemd/user/steamvr.service` and no drop-ins. The PID is an observation,
not a value to reuse. ExecStart, environment values, current ControlGroup and
systemd version were not supplied. The new runner queries current properties
directly before acting and preserves environment values internally.

The prepared bundle precedes the loader-fallback fix below. Keep it unchanged;
the first trial requires a newly built library and a fresh inactive bundle.

### E39: Controlled metadata trial and loader fallback cloud checks

Source: local Linux x86_64 cloud development, based on `6aa4fa0`. Review of
[Vulkan-Loader v1.3.296](https://github.com/KhronosGroup/Vulkan-Loader/blob/v1.3.296/loader/loader.c)
and [v1.4.309](https://github.com/KhronosGroup/Vulkan-Loader/blob/v1.4.309/loader/loader.c)
found that failed negotiation can fall back to the manifest's legacy entry
points. This could bypass the earlier negotiation-only gate. These are upstream
reference versions, not proof of the Frame vendor loader's exact implementation.
The layer now enforces the cached opt-in/crash-guard decision at device creation
too. A denied process forwards driver calls without resource observation or a
provider endpoint; successful runtime calls retain their downstream result.

Two new actual-hook fixture cases exercise that fallback for a disallowed
process and an exhausted crash guard. Existing enabled hook cases now execute
from a real copied `vrcompositor` basename to exercise the production gate.
The provider suite has 14 tests. Five layer cases pass address/undefined-behavior
sanitizer checks with fake downstream calls. No real layer is loaded.

The broader regression also exposed a fractional-millisecond rounding bug in
the inspector's final wait. An empty synthetic registry produced 291 and 224
checks in separate one-second runs, exceeding the wrapper's 128-check bound.
Sleeping until the actual deadline removes that busy loop; the native tests
now bound one-second observations to at most 12 checks.

The new [trial runner](../scripts/capture_layer_trial.py) has 20 generated-data
tests covering exact service/build preflight, independent service construction,
post-exit recovery rehearsal, partial writes, service/inspection failures,
interrupted execution, peer identity, later edits, repeat recovery, locks and
ambiguous launch acknowledgment. They use real temporary files and a simulated
service manager, not a real compositor. The complete capture regression passed
102 tests without skips. Static systemd unit verification passed; live user-unit
execution is unavailable in the cloud. The runner therefore performs a real
one-second timeout/cleanup rehearsal on Frame before requesting activation.

The trial uses a temporary runtime drop-in scoped to `steamvr.service`, a
60-second worker budget, no automatic restart of the trial unit, and a separate
systemd `ExecStopPost` recovery command with 60-second stop-phase timeouts.
It requests metadata only, verifies the peer is a `vrcompositor` in the service
cgroup, then restores configuration and starts SteamVR without its enable flag.
No actual Frame activation, recovery, image access, source identity or new
passthrough observation is established by these cloud checks. Hosted CI has not
been checked. See the [device procedure](on-device.md#run-the-bounded-metadata-layer-trial).

### E40: Restart and console access loss reported

Source: user observation received 2026-10-17 (America/New_York), after the
published trial command: “The device restarted and did not recover the console.”
No command output, checkout revision, trial/inspection/rollback reports or
current service state were supplied. Whether this was a full headset reboot
or a SteamVR restart, whether passthrough/UI recovered, and whether SSH remains
available are unknown. Do not infer failed or successful configuration
restoration from console loss alone.

The runner restarts SteamVR and restores its files/service; it contains no
console-app relaunch. The prior instruction to read reports when the console
returns did not establish an independent means of access. That operating
assumption was insufficient for this trial. The cloud rehearsal/generated-data
results remain distinct from device recovery or console continuity.

Further `--start-metadata-test` calls are now blocked before any service query,
rehearsal, state creation or activation. Known-run reporting/recovery and
read-only preflight remain available. The updated trial suite passes 21 cloud
tests, including the early activation guard; no device operation was repeated.
The pause is effective on updated checkouts; it does not remotely stop a trial
already scheduled by the earlier revision.

Next obtain the existing reports through an independent terminal/SSH session
and verify normal UI/passthrough. Do not resume activation until the observed
device state and recovery outcome are understood and independent access is
established. No frame access, camera identity or live capture is demonstrated
by the restart observation.

### E41: Frame trial report and service-control failures

Source: user-supplied saved report, filtered service status and bounded journals,
collected **2026-10-17 10:49:52 -04:00** on Frame. Checkout HEAD is
`d5892f4eae7f9e56ec1db3d70709c8279607ca89`; the later pause was not yet pulled.
The user separately confirms normal headset UI and passthrough work, with only
the console missing. Uptime at collection is 1 hour 39 minutes, predating the
10:36 trial: the observation is a SteamVR restart, not an OS reboot during the
trial. SSH access was not demonstrated; the user ran the collector locally.

Trial directory is
`/home/steamos/framejacker/artifacts/metadata-trials/trial-47df919b06aa4b30ae1dfc4f88aa411b`.
The report and journal establish:

- **10:36:36–10:36:37:** dummy recovery unit starts, reaches its one-second
  runtime limit and runs the cleanup command successfully. Its systemd
  `timeout` result is expected for the rehearsal; this does not prove the
  separate SteamVR stop/start/recovery sequence works.
- **10:36:38–10:36:42:** real worker starts, then exits with
  `service_command_failed:systemctl:1`. It reports no image access/saving.
  The aggregate has no `inspection` entry. This establishes no successful
  metadata inspection and does not identify whether the layer loaded briefly.
- **10:36:42–10:36:46:** the separate rollback command runs and reports
  `configuration_restored: true`, `normal_service_started: false`, and one
  `service_command_failed:systemctl:1`. Overall status remains `recovery_failed`.
  Configuration restoration and confirmed normal-service startup are distinct.
- **From 10:36:48:** the supplied SteamVR tail contains startup output. At
  collection `steamvr.service` is active/running, result success, MainPID 19651,
  with no drop-ins. How it became active after the reported error is unresolved.
  The transient trial is collected (`not-found`, inactive, PID 0); the 10:49
  missing-transient-file message follows status collection, not the 10:36 failure.

The old runner suppressed systemctl stderr and omitted the failing operation
and phase. The supplied SteamVR tail starts after the failure window, so it
does not establish permission denial, start limits, job cancellation, an
ExecStart failure or another specific cause. Avoid choosing a service-control
fix without the focused journal. No new layer trial or rollback is needed
while normal UI/passthrough are working.

Activation remains blocked. Updated diagnostics record trial phases, structured
operation/exit code, and at most 4 KiB of stderr for exact service start/stop
or daemon-reload commands. They never retain command stdout or environment-query
stderr. The 23 cloud trial tests pass, including phase attribution, service
errors and environment-output exclusion; no Frame service operation was repeated.
The [focused read-only diagnostic](on-device.md#read-the-service-failure-window)
is the next step. Raw journals and unrelated user identifiers remain outside Git.

### E42: Focused service window and restart policy on Frame

Source: user-supplied focused journal for 2026-10-17 10:36:30–10:37:05 Eastern,
plus current service policy. The paste also repeats E41's collector output; it
is not a second trial. Exact time of the new policy query was not included.
The grep/line-limited journal is displayed in descending time order. Reordered
chronologically, it shows:

| Time, Eastern | Observation |
| --- | --- |
| 10:36:38 | Trial worker service starts |
| 10:36:41 | SteamVR Launcher begins stopping |
| 10:36:42 | Worker reports the generic systemctl error and exits 1 |
| 10:36:44 | Previous XRService manager reports stopping monitoring |
| 10:36:46 | SteamVR Launcher is stopped; rollback reports configuration restored but an unconfirmed service start/error |
| 10:36:47 | SteamVR Launcher begins starting |
| 10:36:48 | SteamVR Launcher is started; later service status is success |

Policy reports `RefuseManualStart=no`, `RefuseManualStop=no`, `Restart=always`,
`RestartUSec=100ms`, `NRestarts=0`, `ExecMainStatus=0`, and active-enter time
10:36:48 EDT. Manual-operation refusal is not the explanation. `Restart=always`
does not establish who initiated the later start: normal systemd semantics
suppress automatic restart after an explicit stop, and the observed counter
is zero. Relationships, other controllers and launcher behavior remain to be
reviewed before changing service control. See the
[systemd v257 reference](https://github.com/systemd/systemd/blob/v257/man/systemd.service.xml)
for semantics; this is not a new Frame systemd-version measurement.

During the 10:36:48 startup, systemd reports leftover cgroup processes, including
vrcompositor, XRService, vrserver and dashboard helpers. The logs alone do not
establish whether these were older survivors or processes created during startup.
That startup also prints Gamescope non-hooked-swapchain errors, CAP_SYS_NICE/
process-capability warnings, and an XRService ONNX discovery warning. They occur
after the 10:36:42 worker failure and 10:36:46 rollback error; their causal role
and baseline frequency are unverified. They do not prove Framejacker image
import, model execution or a camera hardware failure.

The original command stderr is still missing, so the exact failed systemctl
operation remains unresolved. The new `--service-details` mode performs one
read-only query for allowlisted service relationships/status and Exec* executable
paths. It excludes command arguments, environment values and unrequested fields.
All 24 cloud trial tests pass, including output exclusion and no activation for
this mode. No device mutation or new camera trial was performed. Continue with
[the read-only service topology report](on-device.md#inspect-the-service-relationships-without-restarting).

### E43: SteamVR session relationships and platform hooks

Source: user-supplied `--service-details` JSON, received 2026-10-17
(America/New_York). The query succeeds with `activation_requested: false`.
Exact execution time and checkout HEAD were not included. SteamVR remains
active/running, result success, MainPID 19651, with no drop-ins.

The unit is in
`/user.slice/user-1000.slice/user@1000.service/session.slice/steamvr.service`,
requires `gamescope-session.service`, `session.slice` and `basic.target`, and is
part of `graphical-session.target`. It is ordered after the gamescope session
service and before Steam, gamescope-session.target, steamvr-plasma and several
camera/audio helper units. `Wants` includes `arcturus-camera-switch.service`.
That relationship does not establish which camera is selected.

These relationships have specific directions: `Before` is ordering, not stop
propagation, and `PartOf` propagates the parent target's stop/restart to the
service, not automatically the reverse. The report therefore does not prove
that stopping SteamVR directly stops the entire graphical session. `UpheldBy`,
`Upholds`, `PropagatesStopTo` and `StopPropagatedFrom` are empty;
`StopWhenUnneeded=no`. Manual start/stop are permitted. Job timeout properties
are infinity, while the service stop timeout is 20 seconds. These facts do not
explain the original nonzero systemctl result by themselves.

`ExecStartPre`, `ExecStart` and `ExecStop` use
`/usr/share/deckard/select_steamvr.sh`; `ExecStartPost` and `ExecStopPost` use
`/bin/bash`. Arguments and hook bodies were deliberately omitted by the
read-only report. A later source comparison found that the inspector also
unintentionally dropped earlier entries in repeated Exec* properties; this
report is therefore not a complete hook inventory. See the correction in
[E44](#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix).
This is a platform launcher with additional lifecycle hooks;
the prior generated manager tests do not model those hook side effects.
Nonzero hook results, target transitions and competing jobs remain possible
explanations, not established causes. The old stderr cannot be reconstructed
from these executable paths.

Activation remained blocked. The next read-only input was the shipped unit's
Exec* definitions and selector script, now received in E44. The
[source-reading procedure](on-device.md#read-the-platform-launcher-definitions)
prints static vendor code without executing it, and omits unit environment/
credential directives. Runtime environment and credential files are not read.
No restart policy, capability setting, camera source or driver change is
justified by this report alone. No new device mutation or camera access occurred.

### E44: Platform launcher source and repeated-hook diagnostic fix

Source: user-supplied static Exec*/Type lines from
`/usr/lib/systemd/user/steamvr.service` and the full
`/usr/share/deckard/select_steamvr.sh` source, received 2026-10-17
(America/New_York). Exact execution time and checkout HEAD were not included.
The collector read these files without running the hooks or selector. This
input contains no new runtime trial or camera result.

The unit defines the following sequence. These are configured actions, not
proof of their individual exit statuses during the failed trial:

| Stage | Configured actions and implication |
| --- | --- |
| Pre-start | First-initialization script; three Bash helpers for cache/path/overlay preparation; health-check `--track-started`; selector with `vrstartup`. The three Bash helpers ignore failures through the unit's `-` prefix. The other pre-start hooks do not. |
| Main process | Selector with `vrcmd --background --waitforquit`, under `Type=simple`. The service tracks a waiter after the startup request; its MainPID is not a compositor readiness check. |
| Post-start | An ignored-failure Bash hook writes the charger runtime state. |
| Stop | Selector with `vrstartup -shutdown`. The source establishes a shutdown request, not its completion behavior. |
| Post-stop | Health-check `--track-stopped`, then `displays_turn_off_drm_master`, then an ignored-failure charger-state Bash hook. The first two hooks are not prefixed to ignore failure; their internal behavior remains unreviewed. |

The selector uses `set -euo pipefail`, obtains the selected installation with
`steamvr path --ignore-pending`, verifies the startup/tool files, changes to
the tool directory and `exec`s it with the supplied arguments. If the selected
installation is unavailable but `/opt/steamvr` exists, it changes and applies
the selected SteamVR path before executing the fallback. Consequently the
selector must not be run as a read-only probe. Although its final error message
mentions rebooting into a different partition, the supplied script contains
no reboot command; that branch prints errors and exits 1.

The parser omission in E43 is now explained: `systemctl show` emits one line
per Exec* array element, repeating the property name. Converting those lines
directly to a dictionary kept only the last entry. This serialization is also
visible in the [systemd v257 show implementation](https://github.com/systemd/systemd/blob/v257/src/systemctl/systemctl-show.c).
That reference is not a Frame version measurement. The corrected diagnostic
accumulates every executable path in order, including duplicate Bash paths,
and reads only the leading record path, excluding `path=` text within command
arguments. Unrecognized nonempty hook records are refused without echoing
their content. The report shape and read-only query remain unchanged.

All **26 cloud trial tests pass**, including the new repeated-hook/argument
exclusion regression and unsupported-format refusal. The repeated-hook test
failed against the previous parser before the fix. This does not establish
on-device execution of the corrected diagnostic or successful service recovery.

The original nonzero systemctl operation remains unknown because its stderr
and phase were not retained. Static hook definitions alone cannot establish
which hook ran or failed, who requested the later start, or whether the reported
startup cgroup processes survived an earlier instance. No hook is bypassed and
no restart policy, driver, camera selection or capability setting is changed.
Keep activation paused. The next offline deliverable is a
[platform lifecycle and recovery test model](live-capture.md#steamvr-service-control-acceptance),
with independent access and explicit readiness evidence required before any
later device rehearsal.

### E45: Offline platform lifecycle and guarded recovery

Source: cloud implementation and targeted test execution on 2026-10-17.
The Frame unit/selector facts in E44 informed generated events; no vendor hook,
SteamVR user service or headset runtime was executed here. No new device report
is claimed. The original failed operation is still unknown.

All **65 targeted tests pass, with no skips**: 48 trial/controller tests, nine
service-observation/deadline tests and eight configuration/recovery tests.
The generated platform model covers required health-check/display-hook failures,
ignored Bash-hook failures, delayed compositor startup, a surviving compositor
after waiter exit, pending/canceled jobs, a client error followed by a completed
transition, competing startup, and interruption/foreign-edit preservation.
Generated cgroup/proc files test descendant population, executable/owner/state/
start-time membership checks, missing metadata, wrong process groups and deadlines.
The existing configuration fixture also uses an isolated synthetic provider;
it does not start the real capture producer. CI configuration now includes the
new service-observation suite; hosted CI execution was not checked.

The controller observes existing jobs before requesting another stop and submits
at most one command per transition. It preserves bounded command/timeout errors
even if a later observation permits cleanup. Stop confirmation includes the
original cgroup's descendant population, not just MainPID. Staging/restoration
rechecks stop state, and each restored file is checked again after the runtime
guard so an edit made during observation is preserved. Failed restoration is
not followed by an automatic start. An unchanged healthy configuration requires
no restart if the worker never reached staging. A new regression first failed
against an overly broad shortcut: externally restoring files does not unload
a layer in a running compositor. That shortcut is now limited to pre-staging
trials. Another recovery invocation rechecks an already restored active
service without new stop/start commands. A competing outside controller can
still race between observations and writes; no atomic exclusion is claimed.

Configuration restoration, a successful normal-service start command, an already
running unchanged service, and stable compositor process liveness are separate
report fields. A command error keeps recovery failed even when cleanup/runtime
observations succeed. A process observation does not verify VR rendering or
visible passthrough. Aggregate completion requires the new runtime evidence;
older saved labels alone cannot supply it.

The new `--runtime-status` mode reads service/cgroup/proc metadata without
configuration writes, service actions, VR/Vulkan APIs, provider connections or
image access. It is cloud-tested with generated observations. Native Frame
cgroup/proc behavior and actual recovery remain unvalidated; the
[next device check](on-device.md#check-runtime-liveness-without-restarting)
is read-only and needs no rebuild. `--start-metadata-test` remains blocked
before manager/file calls. Independent access, actual rendering/passthrough
readiness and controlled restoration still need review before any later trial.
Pixel access remains separately blocked on producer synchronization.

### E46: Read-only runtime baseline on Frame

Source: user-supplied `--runtime-status` JSON, received 2026-10-17
(America/New_York). Exact execution time and checkout HEAD were not included.
This is the first received Frame execution result for the new service/cgroup/
proc observation path. It reports `runtime_observed`, exit 0, with
`activation_requested: false`.

| Observation | Reported value |
| --- | --- |
| Service state | Active/running, result success |
| MainPID | 40413 |
| Pending job | `0` (idle) |
| Invocation ID | `7de7425ca53a4ffdbb773ba755d37a8e` |
| Service cgroup | `/user.slice/user-1000.slice/user@1000.service/session.slice/steamvr.service` |
| Original cgroup populated | True |
| Live compositor | PID 40293, start ticks 1292599 |
| Runtime liveness observed | True |
| Rendering readiness verified | False |

The successful mode requires two consistent liveness observations. The distinct
waiter and compositor PIDs agree with the launcher architecture reviewed in
E44; they are not evidence that either process is newly started by Framejacker.
The result validates reading the actual Frame service, original unified cgroup
and live compositor metadata. It does not establish the 65 generated tests ran
on Frame, a successful stop/start or restoration, a loaded capture layer,
provider inspection, camera selection, frame access or visible passthrough.

The report explicitly requires a separate passthrough observation. In the
follow-up, the user confirms normal passthrough worked during/after the check
and states that SSH is not verified. This supplies the human observation for
the baseline; the probe's rendering-readiness flag remains false and service
recovery has not been tested. Keep the current service running. The next
prerequisite is [independent inbound SSH access](on-device.md#verify-independent-ssh-access).
Activation remains blocked while access and the recovery procedure are
reviewed. Do not repeat the successful baseline to manufacture additional
evidence. The original failed service operation remains unknown.

### E47: Recorded recognition and VR panel POC on Frame

Source: user-supplied replay/overlay JSON and follow-up observation, received
2026-10-24 (America/New_York). Exact execution time, recording path, checkout
revision and SteamVR version were not included. The report selects video-track
ordinal 0 / container-stream index 0 and exits with replay status `closed`.

| Observation | Reported value |
| --- | --- |
| Frames processed / presented | 576 / 576 |
| Labels seen across the run | `chair`, `person`, `sofa`, `tvmonitor` |
| Qt platform | `xcb` |
| Overlay images sent / received / submitted | 576 / 576 / 576 |
| Overlay settings messages, including heartbeats | 95 |
| Native runtime initialized / validation-only mode | True / false |
| Native exit / cleanup | 0 / `cleanup_ok: true` |
| Forced termination / images saved | False / false |

The user confirms the recording and detection labels were visible in the
head-following VR panel, and normal passthrough worked during/after playback.
This demonstrates the recorded-input recognition/display POC with a real
OpenVR overlay and clean shutdown. API submission counts do not prove optical
presentation of every frame, and no duration, measured frame rate or latency
was supplied. Settings messages include heartbeats, not necessarily user edits.

The labels aggregate the run; their presence does not establish correct object
identity, box placement or per-frame accuracy. The known rug/sofa false positive
remains relevant. This run does not demonstrate continuous live input, an
Arcturus-selected passthrough source, HDR/stereo fidelity, world-space labels,
dedicated camera capture or capture-layer restoration. The report's generic
scope text remains conservative; the follow-up supplies visual confirmation
for this particular replay panel.

Cloud preparation on the same review date passes **17 replay/overlay tests**
and **11 bounded mirror-recognition tests**, with no skips. Those use generated
clips, offscreen rendering, native validation transport and synthetic mirror
sources; they are separate from this Frame result. No physical mirror/camera
is available in the cloud. The next POC stage is
[ten-second mirror recognition](on-device.md#run-bounded-recognition-from-the-mirror)
with replay/result windows closed and the saved preview displayed after source
closure. Dedicated capture activation remains paused; independent SSH and
actual service recovery are still unverified.

### E48: Live mirror recognition POC and matching preview

Source: user-supplied terminal output and observation, received 2026-10-24
(America/New_York). The command runs the ten-second bounded mirror recognizer
against `/dev/video99` with a five-processing-fps cap and a new explicit preview
path. Git reports already up to date; an exact checkout SHA, execution timestamp
and current SteamVR version were not printed.

| Observation | Reported value |
| --- | --- |
| Recognition result / worker exit | `recognized` / 0 |
| Capture reads / processed frames | 100 / 51 |
| Capture / processing rates | 10.0 / 4.993 Hz, both capped |
| Mean / maximum inference | 40.065 / 67.953 ms |
| Labels aggregated across the run | `chair`, `person`, `tvmonitor` |
| Final image detection | `person`, score 0.88155; box `[536, 290, 692, 405]` in 960 × 540 input coordinates |
| Maximum processing-start observation age | 102.107 ms |
| Replaced pending inputs / stale inputs skipped | 49 / 0 |
| Read errors | Zero short, would-block and interrupted reads |
| Source format | 1920 × 1080 RGB3, stride 5760, virtual SteamVR v4l2loopback node |
| Saved preview | 640 × 360 PNG, published after source/worker closure |
| Recognizer / source cleanup | Both true |
| Forced termination / live display / overlay | All false |

The user states: “The labels and boxes did match the objects.” This supplies
visual correspondence for the inspected preview, whose final report contains
one person detection. It does not validate every intermediate detection of
chair/person/tvmonitor or establish systematic accuracy. The score is not a
calibrated probability. The known rug-as-sofa false positive from E15 remains
part of the model-quality record.

The run demonstrates bounded composited-mirror input, recognition and a correctly
annotated post-capture preview. Together with E47, both POC stages now have
device execution and visual observations: recorded recognition in a VR panel,
and bounded live recognition with a saved result. They are separate paths;
this run starts no live result window or overlay. The reported inference time
is model compute time, not end-to-end camera/display latency, and the local
reader ages are not camera exposure timestamps. Physical camera identity,
fresh camera frames, color fidelity and continuous VR output remain unverified.

The source opens read-only through the existing virtual mirror path; this run
does not activate the dedicated layer or change SteamVR/camera selection.
In the follow-up, the user confirms normal passthrough and head tracking during
and after this particular live run. This supplies the operating observation
alongside successful capture/inference and sampled box correspondence.
Browser diagnostics appear around preview opening, separately from
the successful recognizer exit/cleanup report; they do not identify a capture
or model failure. Dedicated capture activation remains paused pending its
separate access, recovery and synchronization work.

### E49: Shared pipeline and package refactor cloud checks

Execution date: 2026-10-25, Linux x86_64 cloud environment, Python 3.12.3,
branch `claude/peaceful-fermi-8m3bu6`. File replay and bounded mirror recognition
now run through the [shared pipeline](../framejacker/pipeline.py) and detector
interface described in [development](development.md#python-package-and-pipeline);
see the [design review](design-review.md) for the reasoning. Command-line
options, report fields and device behavior are intended to be unchanged; the
mirror report adds `mean_processing_ms`, and the VR panel now receives images
from the replay worker rather than the Qt thread.

All 225 tests pass in `.venv-replay` with 55 skips for unbuilt capture
adaptation fixtures, including 13 new pipeline/output tests that need no OpenCV.
The smoke check passes using the shared detector, and ruff reports no findings.
Replay and mirror scripts also ran as files from outside the checkout.

GitHub Actions on commit `56eb472` passed both the x86_64 workflow (including
lint and smoke) and the new ARM64 workflow on `ubuntu-24.04-arm`, which runs the
native, capture-fixture, replay-window and mirror-recognition suites. That
commit also restores CI: runner images had stopped shipping `libEGL.so.1`, so
PySide6 import failed in the smoke and replay-window jobs on `main` as well.
None of this is a Frame result: replay, the VR panel and mirror recognition must
be rerun on the headset before treating the refactor as device-confirmed.

### E50: Candidate detector and evaluation harness cloud checks

Execution date: 2026-10-25, Linux x86_64 cloud CPU, Python 3.12.3, OpenCV
4.12.0, ONNX Runtime 1.31.0, branch `claude/peaceful-fermi-8m3bu6`. `yolox-nano`
(the pinned YOLOX 0.1.1rc0 ONNX export) is selectable with `--detector` in
replay and mirror recognition; the default remains `mobilenet-ssd`. Mirror
recognition can also save an unannotated `--still` for evaluation sets.
[Evaluation](development.md#detector-evaluation) ran both detectors on the
[seed manifest](../evaluation/seed.json) at threshold 0.5:

| Detector | Runtime | Expected labels found | Must-be-absent labels reported | Unsupported labels | Mean inference |
| --- | --- | --- | --- | --- | --- |
| `mobilenet-ssd` | OpenCV DNN | 8/9 | 3 (horse as cow twice, giraffe as horse) | giraffe, zebra, kite | 21.0 ms |
| `yolox-nano` | ONNX Runtime | 12/12 | 0 | none | 11.0 ms |

MobileNet-SSD also missed the people in the beach/kite photo. YOLOX-Nano under
OpenCV DNN produced the same detections but took about 55–90 ms per image here,
so ONNX Runtime is preferred where installed. Tests cover letterbox mapping,
grid decoding/NMS on synthetic output, both runtimes agreeing on the fixture,
scoring/manifest validation and still publication after source closure.

The seed images are outdoor, color and unrelated to Frame's indoor, composited
or grayscale input, and contain no rug. They do not establish accuracy on the
headset or address the [rug false positive](#e15-saved-room-recognition-and-rug-false-positive);
neither model has a rug class, so the useful Frame measure is whether a rug is
confused with `couch`. Cloud CPU timing is not Frame timing. Run the
[Frame comparison](on-device.md#compare-detectors-on-frame-stills) before
choosing a default.

### E51: Media-time replay pacing (unit-tested only)

Date: 2026-10-25, branch `overnight/pacing-and-live-prep`. Replay gained an
opt-in `--realtime` mode (`ReplaySource(realtime=True)`) that follows media time
and drops frames more than 0.1 s late. Only the `PlaybackClock` schedule logic
has a unit test (`tests/test_playback_clock.py`, three tests, run locally on
Windows Python 3.11). The decode path, Qt window and summary fields were not
run: no PyAV, OpenCV or Qt in that environment, and CI had not yet run. Not
measured on Frame; see the [morning checklist](morning-checklist.md).

Partial Frame console output, relayed by the user from a session on the Frame
(2026-10-25, branch revision `507c91b`; the agent's report, not independently
verified): setup and the YOLOX model download succeeded and the overlay bridge
built (not launched). The offscreen replay regression processed 30 frames at
5.055 fps with 0 dropped, mean processing 37.7 ms, no labels detected. The
realtime pacing run had not finished in the pasted output, so there is no
pacing result yet. Mirror, stills, overlay and window-mirror stages were not
run.

Later the same day, over SSH from the PC (observed directly, Frame revision
`5cea01c`, newest recording 3.8 GB, offscreen): an unpaced replay processed 200
frames at 5.09 fps (mean 42.5 ms processing, max input age 0.4 ms). The first
`--realtime` implementation timed out with no report: decoding this file runs
at about 13 fps, slower than the recording, so every frame was late and all
were dropped. Fixed by resyncing when nothing was emitted for one second.
With the fix, 60 frames processed in about 60 s (0.97 fps), 740 dropped, 59
resyncs. So on this recording `--realtime` is decode-bound and cannot keep up
with real time; pacing works but does not yet make playback real-time. Open
question: a smaller or lower-resolution recording, or decoder settings.

GitHub Actions for PR #2 subsequently passed the lint and x86_64 and ARM64
jobs, including the replay-window and overlay-bridge no-skip checks. The ARM64
smoke job was skipped; all other listed checks passed.

### E52: Bounded mirror stage via SSH

Date: 2026-10-25, Frame revision `c57423a`, run remotely over SSH from the PC
with `SKIP_UPDATE=1 COUNTDOWN=15 STEP_TIMEOUT=90`, stage `mirror`. The stage
exited 0 after its bounded 10-second capture and saved
`artifacts/frame-session/20261025-022435/mirror-recognize.json` and
`mirror-detections.png`. The report says 12 frames were processed and the last
result was `tvmonitor` at confidence 0.587; preview generation completed.

This is a software report from `/dev/video99`, identified only as a SteamVR
virtual-mirror candidate. It does not verify that a TV was physically in view,
that the preview matched the scene, that the source was fresh camera imagery,
or that passthrough/tracking was normal. The interactive observation prompt
was unanswered; no user headset observation is recorded. Still capture,
overlay and window-mirror stages remain unverified.

### E53: Unattended replay regression and pacing rerun

Date: 2026-10-25, Frame revision `b8d2283`, run remotely over SSH with
`SKIP_UPDATE=1 YES=1 STEP_TIMEOUT=300`, stages `regression pacing evaluate`.
All commands exited 0. Offscreen regression processed 30 frames at 4.48 fps,
with no labels and no dropped frames. On the same recording, unpaced replay
processed 200 frames at 5.15 fps with no drops; realtime mode processed 200
frames at 0.969 fps, dropping 2,532 frames and resyncing 199 times. This
reconfirms that realtime replay is decode-bound on this recording. Labels
reported by the file-replay detector are not verified scene truth.

Detector evaluation skipped because `evaluation/local/frame.json` is absent;
no private stills were fabricated without an observed scene and labels.
Overlay and window-mirror were not run: they need a person wearing and
observing the headset. These results are remote device-command observations,
not user confirmation of headset display, passthrough, tracking or detector
accuracy.

### E54: Keyframe catch-up realtime replay cloud checks

Date: 2026-10-26, Linux x86_64 cloud (4 cores), Python 3.12, PyAV 16.1.0.
`--realtime` was redesigned after E53: decoding runs on its own thread with
FFmpeg frame and slice threading (`--decode-threads`, default 2), inference
takes only the newest decoded frame, and when decoding is more than 0.5 s behind
schedule, packets are skipped without decoding until the next keyframe at the
current playback position. Pauses rebase the schedule.

Stand-in clip: synthetic 2464 × 2464 HEVC at 60 fps, 10 s, one-second GOP,
about 320 Mbit/s, which decodes at 14.8 fps with the previous decoder settings,
close to Frame's ~13 fps on the real recording. All runs used 40 processed
frames at a 5 fps cap with MobileNet-SSD:

| Mode | Processed fps | Decode fps | Real-time ratio | Wall time |
| --- | --- | --- | --- | --- |
| Previous `--realtime` (main at `35e144b`) | 0.977 (39 resyncs) | n/a | n/a | 41 s |
| New `--realtime`, 2 decoder threads | 4.682 | 12.9 | 0.942 | 9 s |
| New `--realtime`, 4 decoder threads | 4.660 | 24.5 | 0.938 | 9 s |
| Unpaced (every frame) | 4.727 | 4.7 | 0.077 | |

The previous implementation reproduced E53's Frame result on this clip. The new
one is limited by the 5 fps processing cap, keeps schedule lateness under
0.5 s (494 ms maximum) and advances media time at about real-time rate; the
remaining gap includes startup and waits for keyframes after skipping. On a
decoder benchmark of the same clip, frame threading measured 8.5, 16.3 and
31.6 fps with 1, 2 and 4 threads (14.8 fps for the previous 2-thread slice mode).
Tests use a stepping fake clock to check keyframe-only catch-up, ordering and
pause rebasing, plus an end-to-end offscreen `--realtime` run.

This is a synthetic clip on cloud CPUs. The real recording's GOP length, bitrate
and Frame's core contention with SteamVR are unknown, so real-time ratio,
smoothness and the best thread count on Frame remain to be measured with
`scripts/frame_session.sh pacing`, which now also runs `--decode-threads 4`.
Longer GOPs make playback advance in larger keyframe steps.

## Maintaining the evidence

For a new result, record the execution date, platform/runtime versions,
checkout/binary revision, procedure, observation or report, and its limits.
Mark unavailable fields unknown. Keep user reports, local checks, CI results
and upstream claims separate. Update capability summaries only to the extent
the evidence supports them; use [live capture](live-capture.md) for next tasks
and the [documentation plan](documentation-plan.md) for document ownership.
