# Live capture plan

The replay window and head-relative VR panel work on the user's Frame, including
playback and controls. Live capture is not implemented. The next engineering
task is a standalone capture-only reader, tested with a controlled producer
before attempting a device capture trial.

## Choose a frame provider

Start with Arcturus: the user's vendor app already provides live images, and
the pinned community implementation describes a working experimental path for
its passthrough pair. Validate built-in passthrough separately; neither the
working overlay nor a camera slot number establishes which sensor supplied an
image.

| Route | Current evidence | Action |
| --- | --- | --- |
| Supported Arcturus/Valve interface | No public live-frame contract found in the reviewed sources | Seek SDK or frame-sharing documentation; prefer it if available |
| Public OpenVR tracked-camera API | Our probe returned no frames and interrupted passthrough | Retain the result; do not repeat the unchanged probe |
| Community Vulkan layer and `ptgrab` | Upstream reports live Arcturus buffers and publishes downscaled luma in shared memory | Audit the pinned producer and implement an isolated consumer first |
| Built-in passthrough | Live access and compatibility with that producer are unverified | Identify and test the source separately after establishing a usable provider |

The [official apps page](https://arcturus.vision/apps), rechecked on 2026-10-16,
documents viewing/recording and transfer/conversion. Those features do not
establish a live-frame API. The community source remains pinned to
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`; this plan does not claim that pin is
the latest upstream revision. See the [camera research](steam-frame-camera.md)
for the public API findings and capture limitations.

## Next code deliverable: capture-only reader

Keep the reader separate from inference, Qt and OpenVR. It should consume an
already running provider; it must not install or activate a compositor layer,
restart SteamVR or open camera devices. Its first target is the pinned
[`MrhandPtHeader` shared-memory contract][protocol], produced by
[`ptgrab`][producer]. This tool has not been implemented yet.

The implementation and tests need to cover:

- Exact native layout, magic, file size, dimensions and camera-slot bounds.
  Keep the consumer read-only and bound all allocations and copies.
- Consistent frame copies using the producer's per-slot sequence lock, with
  appropriate memory ordering on ARM64. Reject incomplete or changing copies;
  a Python read of a sequence number alone is not a synchronization guarantee.
- Missing, stale, malformed or replaced shared memory, including producer
  restarts. Exit on a bounded deadline and handle Ctrl+C cleanly.
- A controlled producer with changing images, counters and timestamps, plus
  malformed, partial and stalled input. Test on ARM64 as well as x86_64 before
  claiming cross-process synchronization is validated on Frame.
- A short JSON report: selected slot, dimensions, pixel format, number of fresh
  frames, observed delivery rate, stale/copy-rejection counts and shutdown
  result. Do not save images or send frames over a network by default.

The upstream output is **grayscale luma**, not a color RGB stream. Its timestamps
estimate when `ptgrab` observed a stable buffer, rather than camera
exposure time. Preserve those limitations; source identity, exact exposure
timing and stereo synchronization remain unverified until measured separately.

## First live device trial

Before a trial, prepare a pinned native build, inspect the actual layer startup
behavior, record existing configuration and prepare a restoration procedure.
The upstream [installer][install] adds a user-level implicit Vulkan layer and
requires a subsequent compositor start to take effect. Its
[uninstaller][uninstall] also requires a restart to unload the running layer.
These are operational changes, not consequences of building the reader. Do
not run those installers as a routine inventory check or rerun the old public
camera probe.

At the next Frame session:

1. Run `python3 scripts/device_check.py`, note the current SteamVR version and
   confirm the vendor app and normal passthrough still work before any change.
2. Once a provider is prepared for that runtime, run only the capture reader
   for ten seconds on one selected slot. Leave detection and the VR panel off.
3. Verify fresh frames using visible scene motion and an explicitly enabled
   local preview. Identify the physical source and orientation rather than
   assigning left/right or Arcturus/built-in from a slot number.
4. Check normal passthrough during and after the trial, then verify normal
   shutdown and the restoration procedure. Stop the trial if passthrough or
   tracking regresses; retain the report before changing the implementation.

The first acceptance target is sustained fresh images from one identified
source, with a bounded exit and working passthrough. An API success, an existing
shared-memory file or repeated delivery of the same stale image does not pass.
Color, stereo depth and world-space labels are later milestones.

## Connect the working application

After the capture-only trial passes, extract a common frame-source boundary
from `ReplayWorker` while preserving the replay tests. Carry image format,
dimensions, stride, source identity, sequence, timestamp and timebase provenance
with each frame. Add the live reader as an optional source feeding the same
detector and display.

Use one latest-frame slot and discard old live input when inference is slow.
Keep each detection attached to its source image. If the first input is luma,
convert it explicitly for the three-channel demo detector and measure detection
quality; copied grayscale channels do not recover color.

Begin at a low inference rate, then measure delivered/processed rates, frame
age from observation, CPU/GPU load and compositor behavior. Reuse the working
head-relative panel before pursuing stereo rendering or spatial labels. Test
built-in passthrough as a separate source with the same acceptance checks.

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
