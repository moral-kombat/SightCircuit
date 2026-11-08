# Steam Frame and Arcturus camera integration

Reference for developers comparing **composited mirror input** with **dedicated
camera input**. The mirror contains what SteamVR displays, including our panel;
a dedicated camera interface would provide camera frames under a separate
buffer contract. They are different inputs with different limits.

Use [status](status.md) for reviewed project results, [on-device procedures](on-device.md)
for commands, and the [current work plan](live-capture.md#current-work-order)
for priorities. Dedicated capture is deferred. The original research from
October 15–16 and the November 4 additions are preserved in the
[research snapshot](history/2026-11-07-camera-research.md); source dates and pins
below do not imply validation on a newer runtime.

## What the available sources establish

The reviewed sources do not establish a supported public Steam Frame/Arcturus
live-camera API for SightCircuit. Valve's general OpenVR camera interface is
an application contract, not proof of support on this headset. Arcturus's
[official apps][arcturus-apps] document recording, viewing and reconstruction;
those features do not establish an application frame-access contract.

| Source | What it provides | Limit |
| --- | --- | --- |
| [OpenVR header][openvr-api], [sample][openvr-sample] and [geometry example][openvr-geometry] | General camera acquisition, frames and calibration interfaces | SightCircuit's public probe returned no frames and interrupted passthrough |
| [spyjacob investigation][hand-findings] and [capture implementation][hand-capture] | Experimental Arcturus buffer access through a Vulkan layer | Third-party result; missing producer synchronization |
| [Frame Passthrough Shortcuts][source-switch] | RGB/monochrome source switching through private interfaces | Source selection does not supply frames or a stable public ABI |
| [Arcturus apps][arcturus-apps], [Splat Workshop][splat-workshop] and [Spirula][spirula] | Recording, transfer, conversion and reconstruction | File workflows, separate from live detection |
| [Arcturus developer page][arcturus-developers] (read 2026-11-07) | Passthrough *behind* an app through the OpenXR environment blend mode, with Unity and native samples | States that no direct camera access API is available; output-side only, not frame access |

## Project device results

- **Mirror input works for bounded recognition.** [E48](evidence/e048.md)
  records capture, inference and a matching saved preview; [E81](evidence/e081.md)
  adds the later mirror regression and a 400-frame replay-panel run without an
  observed freeze on that build/runtime. The mirror includes
  our VR panel ([E18](evidence/e018.md)), so live output needs qualified masking
  and feedback/expiry acceptance.
- **The public camera probe failed.** It returned no frames and interrupted
  passthrough ([E3](evidence/e003.md)). Do not repeat it unchanged; the
  [device guide](on-device.md#then-verify-camera-access) owns any future procedure.
- **Metadata-only provider activation was tested.** [E65](evidence/e065.md)
  records provider loading and restoration across platform sessions, with an
  unsettled full registry. That result does not establish synchronized image
  access, physical camera identity or a working dedicated capture backend.

These are brief pointers to evidence, not a second compatibility table.

## Valve's documented application interface

The [public header][openvr-api] and [official sample][openvr-sample] define this
capture pattern:

1. Initialize OpenVR and obtain `vr::VRTrackedCamera()`. The official sample
   queries the HMD using `vr::k_unTrackedDeviceIndex_Hmd`.
2. Call `HasCamera`, query `GetCameraFrameSize` for the chosen frame type, and
   check every returned error. A true `HasCamera` result alone is insufficient.
3. Query calibration with `GetCameraIntrinsics` and `GetCameraProjection`.
   Inspect camera count/layout properties rather than assuming a stereo layout.
4. Acquire a shared stream with `AcquireVideoStreamingService`. Allocate the
   buffer size returned by `GetCameraFrameSize`, rather than deriving allocation
   from guessed dimensions.
5. Poll `GetVideoStreamFrameBuffer`, supplying
   `sizeof(vr::CameraVideoStreamFrameHeader_t)` as the header size. Header-first
   polling and `nFrameSequence` comparison can avoid copying the same frame
   repeatedly. The header suggests approximately 16 ms polling intervals.
6. Release the streaming handle with `ReleaseVideoStreamingService`, including
   on error exits. The GL texture path has an additional
   `ReleaseVideoStreamTextureGL` call.

Important details from the header:

- The CPU frame API returns **RGBA, four bytes per pixel**. Convert explicitly
  for a BGR/RGB detector. Driver-side native formats such as NV12 are a different
  interface contract.
- `AcquireVideoStreamingService` takes a tracked-device index, with no documented
  RGB/monochrome source selector. The `nCameraIndex` argument to calibration calls
  is not documented as a camera-source selection mechanism.
- [Layout flags][openvr-layout] describe mono/stereo and horizontal or vertical
  packing. Frame types distinguish distorted, undistorted and maximum-undistorted
  images; zero alpha marks invalid maximum-undistorted pixels.
- The [frame header][openvr-frame] carries dimensions, bytes per pixel, sequence,
  a tracked-device pose and exposure midpoint in **host-system ticks**. Preserve
  its timebase; do not assume milliseconds, Unix time or seconds.
- [`NoFrameAvailable`][openvr-errors] can occur during startup/reactivation.
  Handle it separately from unsupported-device, size, handle and IPC failures.
  The camera is shared and may become inactive while the headset is idle.
- [`IVRCameraComponent_003`][openvr-driver] is the driver-facing interface for
  adding device support. It is not the application capture API.

## Steam Frame observations from community code

The following findings apply to the pinned upstream sources and their reported
runtimes. Project observations are recorded separately above.

### Public API and camera ownership

The [hand-tracking investigation][hand-findings] reports successful camera
presence/handle calls but failed frame-size queries and unavailable calibration.
A handle alone therefore does not prove usable capture. It identifies
`/dev/video99` as a composited mirror, physical camera nodes as owned by
XRService, and XRService's investigated IPC as single-session. Resolve a device
by identity; a node number does not establish a safe camera interface.

The [source-switching implementation][source-switch] uses the `arcimx616 `
sensor-name prefix and `IVRCameraPassthroughInternal_001` private vtable slots.
Those are author-tested source controls, not frame access or USB UVC support.

### Experimental live capture

The [upstream prototype][hand-capture] follows this path:

```text
Arcturus → XRService → vrcompositor → Vulkan layer shares buffer descriptors
                                  → ptgrab imports buffers → shared-memory luma
```

The author [reports][hand-report] 2464 × 2464 NV12 images at 60 fps with Qualcomm
UBWC compression. These are reported values, not defaults to hardcode. The
importer needs the actual format, modifier, plane offsets and row pitches;
compressed buffers cannot be read as ordinary linear NV12.

The prototype publishes downscaled grayscale (luma) frames, commonly 640 × 640
per slot, to `/dev/shm/mrhand-pt`. Its [protocol][hand-protocol] includes sequence
locks, observation times and counters. It does not provide a ready-made RGB
stream. It sorts buffer IDs into camera slots and infers readiness from pixel-strip
changes; neither mechanism establishes camera identity or producer completion.

The reported timestamps are [observation estimates][hand-timebase], not exposure
times. Consumer-side fences do not synchronize reads with XRService writes;
[producer fences and sequences remain missing][hand-timing]. The
[dated source review](capture-reference-review.md) preserves detailed ownership,
lifetime and publication findings. SightCircuit's adaptation remains metadata-only;
its loading result is separate from the upstream image-access claim.

## Third-party camera claims (2026-11-04)

These are claims from other projects, recorded separately from SightCircuit
evidence. None has been checked on our Frame.

From danthedaniel/roommate-radar
[`docs/arcturus-av1.md` L62-L127](https://github.com/danthedaniel/roommate-radar/blob/d6e8a8cbdb6c1197514e382662951231fd641941/docs/arcturus-av1.md#L62-L127) at
`d6e8a8cbdb6c1197514e382662951231fd641941`. That project is AGPL-3.0, so these
are facts restated in our own words; nothing is copied from it.

- When the Arcturus app's camera provider takes the colour cameras, XRService
  logs that external camera ownership was requested and later released (lines
  tagged `[ExternalCameraOwnership]`). In between, XRService closes its own
  passthrough interfaces and its ICP sessions, and runs the tracking cameras
  raw.
- The provider sends frames to the app over the Unix socket
  `/tmp/av1_external_camera_provider.sock`, and a lock file,
  `/tmp/passthrough_camera.lock`, stops a second provider from owning the
  cameras.
- The colour cameras pause and resume with user presence (standby),
  independently of the tracking cameras ([L75-L76](https://github.com/danthedaniel/roommate-radar/blob/d6e8a8cbdb6c1197514e382662951231fd641941/docs/arcturus-av1.md#L75-L76)). The same
  document reports that XRService pauses them as a game starts
  ([L219-L220](https://github.com/danthedaniel/roommate-radar/blob/d6e8a8cbdb6c1197514e382662951231fd641941/docs/arcturus-av1.md#L219-L220)).
- Attaching the colour module moves the video nodes: with it attached,
  `/dev/video0` and `/dev/video3` carry the passthrough cameras, the SLAM
  cameras move to `/dev/video9` and `/dev/video13`, and the upper cameras to
  `/dev/video6` and `/dev/video7` ([L64-L71](https://github.com/danthedaniel/roommate-radar/blob/d6e8a8cbdb6c1197514e382662951231fd641941/docs/arcturus-av1.md#L64-L71)). Device paths must
  therefore be resolved by identity, not hardcoded.

**Unresolved: the Arcturus buffer format.** Three accounts disagree:

| Source | Claim |
| --- | --- |
| This guide, from spyjacob's report ([above](#experimental-live-capture)) | 2464 × 2464 NV12 with Qualcomm UBWC compression, i.e. 8-bit UBWC NV12 |
| Frametop [`hands/README.md` L81](https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/hands/README.md#L81) at `adebd7d` | Reads the colour frame on the CPU as a 10-bit frame and keeps the top 8 bits of luma, i.e. 10-bit packed linear data |
| roommate-radar [L80-L95](https://github.com/danthedaniel/roommate-radar/blob/d6e8a8cbdb6c1197514e382662951231fd641941/docs/arcturus-av1.md#L80-L95) | TP10 UBWC (Qualcomm compressed 10-bit) as XRService's default; 8-bit linear NV12 only for about a second while SteamVR starts |

They may describe different consumers, settings or SteamOS and SteamVR builds;
SightCircuit has no device evidence that chooses between them. Any importer must
read the format, modifier and plane layout from the buffer it is handed.

**No producer fence.** None of these sources, nor the spyjacob capture path
above, describes a producer fence for these buffers. Consumer-side fences do
not order reads against the camera producer.

## Arcturus developer page (2026-11-07)

Read by the coordinator on 2026-11-07 at the owner's request. The page is the
vendor's statement, not a project result, and nothing from it has been run.
The owner sent the vendor an inquiry about camera access for a passthrough
overlay on 2026-11-08 (owner's report); no reply is recorded yet.

**Camera data access.** The page states: "A direct camera access API is not
yet available. We're taking developer inquiries about your use case." That
confirms, from the vendor, what this reference already concludes: there is no
supported public frame-access contract for the Arcturus camera, and the
dedicated-capture track stays paused for that reason. The page invites
inquiries through the vendor's support address and Discord; whether to send one
is the owner's decision ([vendor interface questions](live-capture.md#vendor-interface-questions)
list what a usable answer must cover).

**Passthrough in an application.** What the page documents is the other
direction: the headset's passthrough shown behind an application's rendering.
Three routes use one mechanism, the standard OpenXR environment blend mode:
Unity through the OpenXR plug-in with a small feature script that requests
alpha blending when the session starts; Unreal Engine 5.8 through Valve's
Android route; and native OpenXR, which chooses a swapchain format with an alpha
channel, sets `XR_ENVIRONMENT_BLEND_MODE_ALPHA_BLEND` in `XrFrameEndInfo` before
`xrEndFrame`, checks support with `xrEnumerateEnvironmentBlendModes`, and sets
`XR_COMPOSITION_LAYER_UNPREMULTIPLIED_ALPHA_BIT` when the content is not
premultiplied. Two samples are offered, dated 14 September 2026 and marked as a
development build: a Unity project and a minimal C++ and Vulkan cube, both MIT
for the original sample code, with checksum files. The page states the
constraint "Local VR only": passthrough blending works for apps running on the
Steam Frame, not for PC apps using streamed VR.

**What this means for SightCircuit.**

- It is not an input. No frames reach an application by this route, so it does
  not change the [accepted input strategy](poc-scope.md#architecture-decisions-2026-11-07)
  (decision D2) and it is not a route around the feedback problem: the mirror
  composites what the compositor shows, so a HUD drawn by an alpha-blended
  OpenXR application would appear in the mirror exactly as the overlay does.
- It is a documented, vendor-supported alternative presentation path for later.
  The HUD today is an OpenVR overlay through the native bridge; an alpha-blended
  native OpenXR application on the Frame would be an application session rather
  than an overlay, with its own lifecycle, and would replace the bridge rather
  than fix anything in it. The [planned structure](architecture.md#planned-structure-2026-11-07)
  splits the bridge before any new presentation path; this route is recorded
  here as the alternative to evaluate if the overlay path proves unreliable.
  Adopting either sample would mean pinning its archive and checksums and
  recording its license in [third-party attribution](../THIRD_PARTY.md).
- The "local VR only" constraint matches the project's on-device approach.

## Community references selected for follow-up (2026-10-16)

These are research leads. The [original scan](history/2026-11-07-camera-research.md#community-references-selected-for-follow-up-2026-10-16)
retains its broader links and context. Re-pin any moving source before adopting
code, and record its license in [third-party attribution](../THIRD_PARTY.md).

### 1. spyjacob: Arcturus live capture and hand tracking

The [report][hand-report] and [capture source][hand-capture] are pinned to
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. The relevant findings are summarized
under [experimental capture](#experimental-live-capture). The author also
reports inference contention and passthrough artifacts, which require separate
capture and inference measurements on any future implementation.

### 3. Frametop: built-in camera research and related leads

[Frametop's hands documentation](https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/hands/README.md)
describes a separate `ft-camd` route that borrows XRService buffers and publishes
four IR tracking-camera streams. Its broker requires `CAP_SYS_PTRACE`,
`CAP_PERFMON` and `CAP_DAC_READ_SEARCH`. Reported color, calibration and wake
problems make it a research lead, not a qualified replacement input.

The [historical related leads](history/2026-11-07-camera-research.md#3-frametop-built-in-camera-research-and-related-leads)
include QuestLHSync's bright-spot driver and a community visualization. Neither
establishes a supported RGB frame provider for SightCircuit.

## Calibration, spatial labels and Gaussian splats

These are deferred, separate capabilities. A 2D detection alone does not locate
an object in 3D. Spatial labels would need actual camera intrinsics/distortion,
extrinsics, camera-to-head transform, image timebase, matching headset pose and
measured or estimated depth.

The [community recorder findings][hand-recorder] describe continuous video from
one SLAM camera plus stereo RGB snapshots. This differs from Arcturus app
`*_stereo.mp4` recordings, which [Splat Workshop][splat-workshop] describes as two
video tracks with calibration/pose metadata. Its reconstruction workflow uses
[Spirula][spirula] and [calibrated-camera manifests][spirula-manifest], then a
viewer on Frame. Preserve original recordings and metadata for that work;
generated replay fixtures do not reproduce the recording format or validate
reconstruction and headset rendering.

## Implementation sequence and remaining checks

Follow the [current work plan](live-capture.md#current-work-order) and its
[acceptance gates](live-capture.md#edge-hud-acceptance-gates). This reference
supplies interface facts and research limits; it does not activate a provider,
change the POC scope or establish new headset evidence.

[openvr-api]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr.h#L4919
[openvr-sample]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/samples/tracked_camera_openvr_sample/tracked_camera_openvr_sample.cpp#L349
[openvr-geometry]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/samples/hmd_opencv_sandbox/opencv_process.cpp#L81
[openvr-layout]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr.h#L2073
[openvr-frame]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr.h#L2102
[openvr-errors]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr.h#L2052
[openvr-driver]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr_driver.h#L3168
[openvr-overlay]: https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/samples/helloworldoverlay/openvroverlaycontroller.cpp
[hand-findings]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/FINDINGS.md#L6-L20
[hand-report]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/REPORT.md#L26-L48
[hand-capture]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/ptgrab.cpp
[hand-protocol]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/handcam_proto.h#L30-L44
[hand-timebase]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/REPORT.md#L116-L117
[hand-timing]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/REPORT.md#L174-L180
[hand-recorder]: https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/FINDINGS.md#L23-L34
[source-switch]: https://github.com/KominoVR/frame-passthrough-shortcuts/blob/032841dcde61059b25d33a487bf202a30fbf58f1/src/openvr_camera_source.cpp#L29-L117
[splat-workshop]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md
[spirula]: https://github.com/arcturus-vision/spirula-studio/blob/069fb5e489fb81c4c8d04b515330851ab88c7965/README.md
[spirula-manifest]: https://github.com/arcturus-vision/spirula-studio/blob/069fb5e489fb81c4c8d04b515330851ab88c7965/src/sfm/README.md#L300
[arcturus-apps]: https://arcturus.vision/apps
[arcturus-developers]: https://arcturus.vision/developers
[arcturus-camera]: https://store.steampowered.com/app/4103250
