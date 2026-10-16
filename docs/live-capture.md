# Live capture plan

This is the authoritative current capture work plan. [Status](status.md) owns
capability and validation evidence; the [dated source review](capture-reference-review.md)
explains the code findings behind this sequence. Every deliverable below is
planned unless a task explicitly says otherwise. No capture
layer has been deployed by Framejacker.

## Current work order

| Order | Deliverable | State | Acceptance |
| --- | --- | --- | --- |
| 1 | Bounded diagnostic for an existing SteamVR mirror | Complete for the tested view: room capture works; VR panel is included | Keep for bounded/no-display recognition and post-capture snapshots; physical source/timing unresolved; [evidence](status.md#e18-vr-panel-included-in-mirror-input) |
| 1a | Recognition on the saved mirror still | Frame execution confirmed; rug incorrectly labeled sofa | Keep execution success separate from classification quality; [evidence](status.md#e15-saved-room-recognition-and-rug-false-positive) |
| 1b | Bounded recognition from mirror frames | Frame execution confirmed: 4.993 processed fps and normal passthrough | 100 reads, 51 processed, clean shutdown; user confirms chair/TV present, localization and broader accuracy unverified; [evidence](status.md#e17-bounded-mirror-recognition-on-frame) |
| 1c | Check capture of our own display | Confirmed: the VR panel appears in captured output | Current composited mirror does not exclude our output; no continuous live-image panel from this source; [E18](status.md#e18-vr-panel-included-in-mirror-input) |
| 1d | Detector quality and class coverage | Needs evaluation | Include the rug false positive; select supported target classes and evaluate correct labels, boxes and misses before claiming useful accuracy |
| 2 | Isolated shared-memory reader and controlled producer | Controlled x86_64 and Frame ARM64 checks passed; real provider unverified | All 14 native/supervisor tests pass on Frame; upstream writer ordering and original GPU completion remain unresolved; [E23](status.md#e23-reference-build-and-controlled-reader-on-frame) |
| 3 | Dedicated camera provider | Transport build/tests pass on Frame; publication adaptation cloud-tested; not deployed | Verify publisher/control tests on ARM64, then finish GPU import/resource cleanup/watchdog, registry refresh, layer lifetime and restoration; physical source and built-in support unresolved |
| 4 | Capture-only device trial | Planned | Ten seconds of fresh images from one identified source, correct orientation, clean exit and working normal passthrough |
| 5 | Continuous live input in the existing display | Planned; needs input independent of our output | Reuse detector/display after a dedicated provider or supported overlay-excluded mirror mode is validated; current mirror includes the panel |
| 6 | Color, built-in camera support and later geometry | Planned | Validate each source and color conversion separately; require calibration and stronger timing evidence before stereo depth or spatial labels |

The mirror is a time-boxed opportunity for a first 2D demonstration, not a
dependency on mirror availability. If it is absent, unsuitable or includes our
output, record the limit and continue the dedicated-camera work. Existing
replay remains the application baseline. Record any new detection/readability
measurements in status alongside capture results.

Seek a supported Arcturus/Valve frame-sharing API alongside these experiments
and prefer it if available. The [vendor questions](#vendor-interface-questions)
define the useful interface information without making a vendor reply a
prerequisite for offline reader development.

## Next deliverable: mirror diagnostic

The standalone diagnostic is implemented in `scripts/mirror_check.py` with
read-only V4L2 handling in `scripts/mirror_capture.py`. It has passed controlled
cloud checks. After correcting its initial polling gate, the Frame trial
received 100 complete 1920 × 1080 RGB3 frames from `/dev/video99` in ten seconds,
with 99 changed grayscale samples, zero would-block/short reads and clean
shutdown. See [the device evidence](status.md#e12-successful-mirror-frame-delivery).
This confirms a working composited input path at the diagnostic's capped rate;
the user has now recognized the room in the saved 640 × 360 grayscale preview.
The absolute local-file URL resolved the relative-path browser opening issue;
see [the observation](status.md#e14-physical-room-visible-in-the-mirror-preview).
The saved-image detector ran on Frame but mislabeled a rug as sofa; the demo
has no rug class. Record this false positive separately from successful capture
and annotation. The bounded `mirror_recognize.py` trial then processed 51 reads
at 4.993 fps, with normal passthrough and clean shutdown; see
[the Frame result](status.md#e17-bounded-mirror-recognition-on-frame).
It retained one pending input and replaced older reads. The final frame had no
detections despite chair/tvmonitor labels earlier in the run; model quality is
not established by execution success. The fixed timed panel appeared both in
the headset and in the saved mirror still; see
[the inclusion result](status.md#e18-vr-panel-included-in-mirror-input). This
closes the mirror feasibility branch for continuous VR image output in the
tested mode. Keep bounded/no-display recognition and post-capture snapshots as
the baseline. The user confirms Arcturus is physically connected, but does not
know which passthrough source is selected. The native prerequisite report now
shows GCC 15.1.1 and Vulkan 1.4.309 development files on SteamOS 0.5.6; see
[E21](status.md#e21-capture-prerequisites-on-frame). The reference build and all
14 controlled reader tests now pass on Frame; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame).
The descriptor transport adaptation now builds and passes seven fixture groups
on Frame; see [E25](status.md#e25-descriptor-transport-on-frame). The next
adaptation replaces publication and adds loop controls; run its
[generated-data checks on ARM64](on-device.md#test-owned-publication-without-capture)
while the remaining provider work continues. The inspection builds are not
deployment-ready. Identify the selected source
during capture validation; connection alone does not identify past mirror frames.
Prefer a supported interface; the reviewed
community Arcturus provider remains an experimental alternative, with no
established built-in-camera support. Verify source identity, orientation and
freshness before connecting it to the existing display.
This does not activate a new mirror service or change capture source.
Use the [device procedure](on-device.md#check-an-existing-steamvr-mirror) for
metadata-only discovery before opt-in capture. The requirements below define
the implemented scope and remaining device acceptance checks.

- Identify the existing SteamVR virtual mirror using inventory and device
  metadata before selecting an input. Upstream mentions `/dev/video99`; do not
  hardcode that number, scan physical camera nodes, install a loopback driver
  or start a new service as part of this check.
- Report input identity and available format/dimensions. Reject unsupported or
  unverified device selection explicitly, rather than trying another camera.
- Read for ten seconds with our VR panel hidden. Report frame count, observed
  delivery rate, format, stale input and bounded shutdown. No file or network
  image outputs by default.
- Verify that physical scene motion is visible, not merely changing game/UI
  pixels. If normal system controls permit source selection, test Arcturus
  and built-in passthrough separately. Do not install source-switching tools
  or use private camera calls in this diagnostic.
- For visual inspection, make a still an explicit output choice and inspect
  it after acquisition stops. A composited feed may include UI, reprojection
  and our own result panel; live VR output needs demonstrated exclusion from
  its own input. Otherwise keep this route a capture/recognition proof only.
- Treat missing input or lack of passthrough as a conclusive limitation of this
  route, not proof that the physical camera is broken. A successful mirror test
  does not establish dedicated camera calibration, exposure time or stereo.

Use the [on-device inventory procedure](on-device.md#run-the-device-inventory-first)
for execution context. The operating guide now records commands, dependencies,
output fields, exit behavior and shutdown. The read rate and sampled image
changes are diagnostic observations, not producer frame/exposure timestamps.

## Choose a frame provider

For dedicated input, start with Arcturus: the vendor app provides live images
according to [device evidence](status.md#e2-vendor-app), and the reviewed
community code implements an experimental path for its passthrough pair.
Built-in camera capture requires separate validation.

| Route | Decision |
| --- | --- |
| Supported Arcturus/Valve interface | Prefer it if available; no public live-frame contract was found in the reviewed pages |
| Public OpenVR tracked-camera API | Retain the [failed result](status.md#e3-public-camera-probe); do not repeat the unchanged probe |
| Existing SteamVR composited mirror | Confirmed useful for bounded recognition; includes our VR panel, so not selected for continuous live-image output |
| Community Vulkan layer and `ptgrab` | Prepare an isolated consumer, then adapt only capture components for a controlled Arcturus trial |
| Built-in passthrough | Verify live buffers and source identity separately; the reviewed `ptgrab` size filter and empty-buffer reports do not establish support |

The [camera reference](steam-frame-camera.md) owns interface research. The
[dated source review](capture-reference-review.md#findings-that-affect-our-implementation)
records exact upstream pins, FD/lifecycle issues, inferred camera grouping,
pixel-strip readiness and limitations of the existing importer.

## Next dedicated-camera deliverable: capture-only reader

Keep the reader separate from inference, Qt and OpenVR. It should consume an
already running provider; it must not install or activate a compositor layer,
restart SteamVR or open camera devices. Its first target is the pinned
[`MrhandPtHeader` shared-memory contract][protocol], produced by
[`ptgrab`][producer]. The standalone reader, supervisor and synthetic producer
are now implemented; [development checks](development.md#isolated-luma-reader-and-synthetic-producer)
cover the controlled x86_64 publication path. Follow the
[operating procedure](on-device.md#check-existing-luma-shared-memory) for existing
input. Controlled reader tests passed on Frame ARM64; upstream writer ordering
and original camera-buffer completion remain unverified.

The implementation and controlled tests cover these requirements on x86_64 and
Frame ARM64; real-provider acceptance remains pending:

- Exact native layout, magic, file size, dimensions and camera-slot bounds.
  Keep the consumer read-only and bound all allocations and copies.
- Consistent frame copies using the producer's per-slot sequence lock, with
  appropriate memory ordering on ARM64. Review both writer and reader; reject
  incomplete or changing copies. A Python sequence read alone is not a
  synchronization guarantee, and a stable published copy cannot prove the
  original camera buffer had finished being written.
- Missing, stale, malformed or replaced shared memory, including producer
  restarts and truncation. Exit on a bounded deadline and handle Ctrl+C cleanly.
- A controlled producer with changing images, counters and timestamps, plus
  malformed, partial and stalled input. Test on ARM64 as well as x86_64 before
  claiming cross-process synchronization is validated on Frame.
- A short report: selected slot, dimensions, pixel format, number of fresh
  frames, observed delivery rate, stale/copy-rejection counts and shutdown
  result. Do not save images or send frames over a network by default.

The upstream output is grayscale luma. Its timestamps estimate when `ptgrab`
observed a buffer change; they are not exposure timestamps. Preserve source
identity and timing validity explicitly. Apply the source review's lifecycle,
descriptor and import validation findings to the provider before a trial;
building a read-only consumer does not deploy or repair the provider itself.

The controlled producer includes a full barrier after marking a slot odd. The
reviewed upstream writer's odd release-store alone does not order later payload
stores on ARM64; provider preparation must address that contract. The reader
rejects observable counter regressions and file changes, but this layout has no
producer-generation field: a restart that catches up between polls may be
undetectable. Successful synthetic copies do not resolve GPU completion or
physical source identity.

Dedicated-provider preparation remains an independent offline task: pin only its
capture components, repair descriptor/publication lifecycle issues from the
source review, and prepare a native ARM64 build and exact layer-configuration
restoration. Deployment and the first camera trial remain separate steps.
The prerequisite report distinguishes runtime-library metadata from compiler,
Vulkan headers and linker files; the Frame result reports all expected types.
The new pinned reference builder checks compilation/linkage only, keeps outputs
non-executable and creates no layer manifest. The native Frame build passed;
successful compilation does not resolve the reviewed runtime defects.

The optional transport adaptation duplicates snapshot FDs under the registry
lock and sends a single registry generation. Its shared sender/receiver handles
partial messages, validates ancillary FD counts and metadata bounds, closes
rejected descriptors and applies one 500 ms socket deadline per exchange.
Controlled cloud tests cover disconnects, stalls, malformed responses and FD
lifetime. The same seven fixture groups now pass on Frame ARM64. These tests do
not exercise live layer callbacks.

The optional publication adaptation adds a private runtime file, exclusive
creation, identity/size/permission checks, the ARM64 odd-marker barrier and
monotonic publication. Its stream has explicit duration/rate/opt-in parsing,
signal checks and parent-death killing. Eight controlled cloud tests cover the
same publisher helper and actual reader together; ARM64 publication validation
is next. Normal cleanup removes its file; abrupt death may leave a stale file
that is refused on restart. The implementation does not yet supply automatic
lease recovery or an external watchdog for blocked GPU calls.

Before deployment, finish GPU import validation/resource cleanup, the process
watchdog, registry refresh, layer lifetime/socket ownership and exact restoration.
Original camera-buffer completion and source identity remain separate hardware
acceptance checks even after shared-memory publication is correct.

## First live device trial

Before the dedicated trial, prepare a pinned native build, inspect layer startup,
record existing configuration and prepare exact restoration. The upstream
[installer][install] adds a user-level implicit Vulkan layer and requires a
subsequent compositor start to take effect. Its [uninstaller][uninstall] also
requires a restart to unload the running layer. Do not run these installers as
a routine inventory check or rerun the old public camera probe.

The mirror check has a separate scope above. For the dedicated camera trial:

1. Run `python3 scripts/device_check.py`, note the current SteamVR version and
   confirm vendor preview and normal passthrough work before any change.
2. Once a provider is prepared for that runtime, run only the capture reader
   for ten seconds on one selected slot. Leave detection and the VR panel off.
3. Verify scene motion with an explicitly enabled preview. Identify the physical
   source and orientation instead of inferring left/right or camera type from
   a slot number.
4. Check normal passthrough during and after the trial, then verify shutdown and
   restoration. If passthrough or tracking regresses, stop and retain the report
   before changing the implementation.
5. Add the result to [status](status.md), including date, revision, runtime,
   source identity and remaining unknowns. Advance the task state only when
   its acceptance evidence exists.

The acceptance target is sustained fresh images from one identified source,
a bounded exit and working passthrough. A successful API call, existing
shared-memory file or stale repeated image does not pass. Color and geometry
remain separate milestones.

## Connect the working application

The standalone `mirror_recognize.py` now exercises guarded mirror input and the
shared `DemoDetector` without a visible result window. Its first bounded Frame
trial passed execution/cleanup and the user reports normal passthrough.
Continuous Qt/VR integration remains pending; the bounded tool is not a live
display backend. The fixed-panel test confirmed that our panel reappears in
this input. Keep the current mirror for bounded/no-display recognition and
post-capture snapshots. Prepare a dedicated provider or a supported mirror
mode that excludes our output before streaming captured images into VR. No
such alternate mirror mode has been validated. Ordinary Qt windows also need
their own feedback check.

After the capture-only trial passes, extract a common frame-source boundary
from `ReplayWorker` while preserving the replay tests. Carry image format,
dimensions, stride, source identity, sequence, timestamp and timebase provenance
with each frame. Add live input as an optional source feeding the same detector
and display; mirror input additionally requires the feedback check above.

Use one latest-frame slot and discard old live input when inference is slow.
Keep each detection attached to its source image. For the luma prototype,
convert explicitly to the detector's three-channel input and measure recognition
quality; repeated grayscale channels do not recover color.

Begin at a target of 5 processed fps, then measure delivered/processed rates,
frame age from observation, CPU/GPU load and compositor behavior before increasing
load. This target does not change the recorded replay's implemented 10 fps
default. Actual color requires UV extraction or verified YCbCr sampling and
color-range/matrix handling. Built-in camera support requires buffers that
actually update. Reuse the head-relative panel before stereo rendering or
world-space labels; those need stronger timing and calibration evidence.

## Vendor interface questions

The useful request to Arcturus/Valve is: does a supported native ARM64 app API
expose Arcturus and/or built-in camera frames while XRService continues normal
tracking and passthrough? Ask for a minimal sample, source selection, pixel
layout/color range, buffer lifetime and synchronization, timestamps/timebase,
calibration availability, permission requirements and supported runtime builds.
Include the failed public API result and the fact that the vendor app works.
No support message has been sent as part of this plan.

[protocol]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/handcam_proto.h
[producer]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/ptgrab.cpp
[install]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/install.sh
[uninstall]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/uninstall.sh
