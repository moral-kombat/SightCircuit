# Project status and evidence

Documentation review: 2026-10-16. This is the authoritative record of current
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
| Annotated Qt replay window | Device-confirmed | User confirms visible playback; controlled offscreen tests exercise detection and frame association; [E6](#e6-window-and-vr-panel), [E7](#e7-cloud-validation) |
| Native head-relative VR replay panel | Device-confirmed | Timed test panel, replay, controls and following head movement reported working; detailed performance unmeasured; [E6](#e6-window-and-vr-panel) |
| Public OpenVR tracked-camera probe | Device-confirmed failure | No frames and observed passthrough interruption; do not repeat the unchanged test; [E3](#e3-public-camera-probe) |
| Continuous live input in the Qt/VR application | Planned; unimplemented | No continuous live display backend; standalone bounded recognition is separate; [active work plan](live-capture.md) |
| Recognition on the saved room still | Frame execution confirmed; false positive | One image processed and annotated; user identifies the `sofa`-labeled item as a rug; [E15](#e15-saved-room-recognition-and-rug-false-positive) |
| Bounded mirror recognition | Frame-confirmed execution | 100 captured / 51 processed reads, 4.993 processed fps, 42.044 ms mean inference and clean shutdown; user reports normal passthrough; label accuracy and live display remain separate; [E17](#e17-bounded-mirror-recognition-on-frame) |
| Existing-mirror diagnostic | Frame capture and physical-room preview confirmed | 100 complete RGB3 frames in ten seconds; user recognizes the room in the 640 × 360 grayscale still; physical camera identity, timing and output feedback remain unverified; [E12](#e12-successful-mirror-frame-delivery), [E14](#e14-physical-room-visible-in-the-mirror-preview) |
| Dedicated-camera shared-memory reader | Implemented; cloud-tested | Read-only native consumer, bounded supervisor and synthetic producer; actual provider and ARM64 validation pending; [E9](#e9-shared-memory-reader-cloud-checks) |
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
capture loop and a [shared checksum-verified CPU detector](../scripts/demo_detector.py).
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
Physical camera identity, continuous scene freshness and output-feedback
exclusion remain unverified. Next use the existing fixed timed panel to check
whether a visible VR overlay appears in mirror input before introducing a
continuous result display.

## Maintaining the evidence

For a new result, record the execution date, platform/runtime versions,
checkout/binary revision, procedure, observation or report, and its limits.
Mark unavailable fields unknown. Keep user reports, local checks, CI results
and upstream claims separate. Update capability summaries only to the extent
the evidence supports them; use [live capture](live-capture.md) for next tasks
and the [documentation plan](documentation-plan.md) for document ownership.
