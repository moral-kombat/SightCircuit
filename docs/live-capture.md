# Live capture plan

This is the authoritative current capture work plan. [Status](status.md) owns
capability and validation evidence; the [dated source review](capture-reference-review.md)
explains the code findings behind this sequence. Every deliverable below is
planned and unimplemented unless a task explicitly says otherwise. No capture
layer has been deployed by Framejacker.

## Current work order

| Order | Deliverable | State | Acceptance |
| --- | --- | --- | --- |
| 1 | Bounded diagnostic for an existing SteamVR mirror | Next; unimplemented | Identify an existing virtual feed, report fresh frames or explicit unavailability, exit within a deadline, and verify whether physical passthrough is present |
| 2 | Isolated shared-memory reader and controlled producer | Planned; unimplemented | Validate the pinned layout, consistent frame copies, stale/malformed/restarted input and shutdown; test synchronization on ARM64 before a Frame claim |
| 3 | Dedicated Arcturus capture provider | Planned; not deployed | Pin only the capture components, address lifecycle findings, build natively and prepare exact configuration restoration for a controlled trial |
| 4 | Capture-only device trial | Planned | Ten seconds of fresh images from one identified source, correct orientation, clean exit and working normal passthrough |
| 5 | Live input in the detector and existing display | Planned | Preserve source-frame association and one latest input; begin at a target of 5 processed fps and measure device/compositor impact |
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

Prepare a standalone diagnostic with a bounded worker lifetime; a blocking
device read must not prevent its launcher from ending the check. There is no
Framejacker command for this diagnostic yet.

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
for execution context. Once the diagnostic exists, add its actual command,
dependencies, output fields, exit behavior and shutdown instructions to the
operating guide in the same implementation change.

## Choose a frame provider

For dedicated input, start with Arcturus: the vendor app provides live images
according to [device evidence](status.md#e2-vendor-app), and the reviewed
community code implements an experimental path for its passthrough pair.
Built-in camera capture requires separate validation.

| Route | Decision |
| --- | --- |
| Supported Arcturus/Valve interface | Prefer it if available; no public live-frame contract was found in the reviewed pages |
| Public OpenVR tracked-camera API | Retain the [failed result](status.md#e3-public-camera-probe); do not repeat the unchanged probe |
| Existing SteamVR composited mirror | Run only the bounded feasibility check above; verify camera content and output feedback |
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
[`ptgrab`][producer]. This tool has not been implemented yet.

The implementation and tests need to cover:

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
