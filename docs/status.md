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
| Live input in the detector/VR application | Planned; unimplemented | No integrated live capture backend; [active work plan](live-capture.md) |
| Existing-mirror diagnostic | Frame open/format checks confirmed; no frames yet | First ten-second trial returned no frames with normal cleanup and working passthrough; reader startup corrected, device retest pending; [E11](#e11-first-mirror-acquisition-trial) |
| Dedicated-camera shared-memory reader | Implemented; cloud-tested | Read-only native consumer, bounded supervisor and synthetic producer; actual provider and ARM64 validation pending; [E9](#e9-shared-memory-reader-cloud-checks) |
| Dedicated Arcturus Vulkan capture | Upstream-reported | Community implementation reviewed; no Framejacker deployment or device capture result |
| Built-in passthrough camera input | Unverified | Upstream reports empty built-in-camera-sized buffers; our project has received no live frames |
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
passes. Device validation of this correction is pending. This first result
therefore does not establish that the mirror has no usable producer.

## Maintaining the evidence

For a new result, record the execution date, platform/runtime versions,
checkout/binary revision, procedure, observation or report, and its limits.
Mark unavailable fields unknown. Keep user reports, local checks, CI results
and upstream claims separate. Update capability summaries only to the extent
the evidence supports them; use [live capture](live-capture.md) for next tasks
and the [documentation plan](documentation-plan.md) for document ownership.
