# Steam Frame and Arcturus camera integration

Initial research snapshot: 2026-10-15; the
[source review and stream strategy](capture-reference-review.md) adds
implementation findings checked on 2026-10-16. Sources were inspected on GitHub
and the official Arcturus website. This cloud environment has no headset,
physical camera, display server, or GPU. Project device results below are reports from the user
running commands and apps on their Frame; community results are attributed to
their authors. Cloud validation covers native builds and controlled
missing-runtime behavior, not working camera capture.

## What the available sources establish

Valve publishes a general application camera interface, `IVRTrackedCamera_006`,
in the [OpenVR SDK][openvr-api]. It does not establish that the Steam Frame
runtime exposes the Arcturus RGB camera through that interface. No public,
stable Steam Frame/Arcturus-specific live capture SDK or electrical connector
pinout was found in the sources reviewed. The official [Arcturus apps page][arcturus-apps]
documents Camera recording/viewing, recording transfer/conversion and Gaussian
XR workflows; these capabilities do not establish a public live-frame API.

The most relevant Frame-specific live-capture source found is the community
[steam-frame-hand-tracking project][hand-report]. Its investigation reports that
the public API fails to deliver frames on its tested runtime, while an
experimental Vulkan capture path delivers the Arcturus passthrough pair.
Treat this as evidence for an implementation experiment, not a vendor support
contract or a result for every firmware version.

| Source | Information useful to Framejacker | Evidence and limits |
| --- | --- | --- |
| [Valve OpenVR camera interface][openvr-api] | Discovery, shared streaming, CPU RGBA frames, GL textures, intrinsics and projection | Official SDK contract; Arcturus support must be probed on-device |
| [Valve tracked-camera sample][openvr-sample] | Buffer allocation, sequence checking, frame copy and cleanup | Official general example; not a Steam Frame compatibility test |
| [Valve OpenCV sandbox][openvr-geometry] | Per-camera intrinsics and camera-to-head transforms | Official geometry example; properties may be unavailable on Frame |
| [Community camera investigation][hand-findings] | Failed public API probes, XRService ownership, recording/calibration findings | Author-reported hardware results from October 2026 |
| [Community capture implementation][hand-capture] | Vulkan DMA-BUF import, GPU luma extraction and shared-memory output | Experimental integration inside `vrcompositor`; not a public camera SDK |
| [Frame Passthrough Shortcuts][source-switch] | Arcturus presence detection and RGB/monochrome switching | Uses private interfaces; source selection does not provide image frames |

## Project device results

Detailed project results live in [status and evidence](status.md). The vendor
app works according to the user, while our public probe returned no frames and
interrupted passthrough. [E3](status.md#e3-public-camera-probe) records the API
errors and their limits; [E4–E6](status.md#e4-recordings-and-container-metadata)
record the file-decoding and presentation results.

Do not repeat the unchanged public probe. Its explicit interruption guard and
future-retest procedure remain in the
[on-device guide](on-device.md#then-verify-camera-access). This reference records
public contracts and upstream research; those sources do not establish a
working Framejacker live backend.

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

These findings apply to the cited projects and tested runtimes. Runtime updates
may change them; any future probe must account for the passthrough interruption
observed on our user's device.

### Public API and camera ownership

The [hand-tracking investigation][hand-findings] reports:

- `HasCamera=1` and successful streaming-handle acquisition, but
  `GetCameraFrameSize` fails for all frame types and calibration properties are
  unavailable. Acquiring a handle therefore does not demonstrate usable capture.
- `/dev/video99` is a composited headset mirror, not raw stereo input.
  It may still be useful for a first 2D recognition demo if it includes live
  passthrough on our runtime. That content and output-panel feedback must be
  checked; the mirror has not been captured by this project.
- Physical camera nodes are owned by XRService. Their existence does not make
  `cv2.VideoCapture(0)` a valid Arcturus capture method.
- The investigated XRService IPC path is single-session and already used by
  the tracking driver. It is not established as a supported second-client API.

The [source-switching implementation][source-switch] discovers sensor nodes by
the `arcimx616 ` name prefix and uses a private connection query. It switches
passthrough sources with `IVRCameraPassthroughInternal_001`, using private vtable
slots verified by that author on one firmware build. This is useful evidence
about source control, but supplies no capture buffer and no stable public ABI.
Do not confuse these sensor nodes with proof of USB UVC compatibility.

### Experimental live capture

The [community report][hand-report] describes this path:

```text
Arcturus passthrough pair → XRService → vrcompositor
                                         ↓ passive Vulkan layer shares DMA-BUFs
                               separate Vulkan process (ptgrab)
                                         ↓ GPU extraction/downscale
                               shared-memory luma frames → inference
```

The author reports 2464 × 2464 NV12 camera images at 60 fps, with Qualcomm UBWC
compression. These are observed values, not dimensions or performance to hardcode.
The Vulkan importer needs image format, plane offsets/row pitches and DRM
modifier metadata; the compressed buffers cannot be treated as ordinary linear
NV12 byte arrays.

The [actual capture implementation][hand-capture] publishes downscaled **luma**
frames, commonly 640 × 640 per camera, to `/dev/shm/mrhand-pt`. Its
[shared-memory contract][hand-protocol] includes dimensions, camera slots,
seqlocks, observation times and local frame counters. It does not provide a
ready-made color RGB stream for our detector.

Code review confirms that `ptgrab` filters for 2464 × 2464 images, assigns two
camera slots by sorted buffer IDs and uses pixel-strip changes to infer frame
readiness. The author's report says built-in-camera-sized buffers stayed empty.
The current implementation therefore establishes an Arcturus-specific lead,
not a general built-in-camera backend. The [source review](capture-reference-review.md)
details lifecycle, descriptor and synchronization work needed for adaptation.

The author's [timestamp description][hand-timebase] and
[capture caveats][hand-timing] are material: producer frame sequence and producer
synchronization fences are unavailable through this path. Capture timestamps are
observation-time estimates, not exposure timestamps, and some frames are missed.
Consumer-side Vulkan fences do not establish synchronization with the camera
producer. Avoid presenting these estimates as exact exposure times or guaranteed
stereo synchronization.

This layer changes compositor startup. Keep it as a separately evaluated,
optional backend, with the upstream rollback process available. It has not been
installed in the cloud environment or on a headset as part of this project.

## Third-party camera claims (2026-11-04)

These are claims from other projects, recorded separately from Framejacker
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
Framejacker has no device evidence that chooses between them. Any importer must
read the format, modifier and plane layout from the buffer it is handed.

**No producer fence.** None of these sources, nor the spyjacob capture path
above, describes a producer fence for these buffers. Consumer-side fences do
not order reads against the camera producer.

## Community references selected for follow-up (2026-10-16)

These are references 1 and 3 from the community scan, rechecked against upstream
documentation/source. They are research leads, not Framejacker hardware
validation, a supported SDK, or authorization to install anything. The existing
capture-only reader plan and pinned producer remain unchanged.

### 1. spyjacob: Arcturus live capture and hand tracking

- [Project](https://github.com/spyjacob/steam-frame-hand-tracking),
  [measurements and limitations](https://github.com/spyjacob/steam-frame-hand-tracking/blob/main/REPORT.md),
  [Vulkan layer](https://github.com/spyjacob/steam-frame-hand-tracking/blob/main/capture/handcam_layer.cpp),
  and [ptgrab consumer](https://github.com/spyjacob/steam-frame-hand-tracking/blob/main/capture/ptgrab.cpp).
- Already cited above at a pinned revision. The layer shares imported camera
  DMA-BUFs; a separate Vulkan process extracts downscaled luma for shared-memory
  readers. The author reports live Arcturus capture, not built-in tracking-camera
  capture through this route.
- Useful next review: buffer layout, consumer synchronization and shutdown.
  Preserve the report's estimated observation timestamps, missed-frame and
  producer-fence limitations. Luma is not a color stream.
- The report describes CPU contention and passthrough artifacts during inference.
  Evaluate capture separately from inference, then measure compositor impact.

### 3. Frametop: built-in camera research and related leads

- [Frametop hands documentation](https://github.com/Frametop/frametop/blob/main/hands/README.md)
  describes `ft-camd` borrowing XRService DMA-BUFs and publishing four IR
  tracking-camera streams in a shared-memory ring, using dequeue tracepoints
  for timing. This is a different route from spyjacob's compositor layer.
- Upstream labels hand tracking experimental/deferred because of CPU cost.
  The broker setup requires elevated capabilities
  (`CAP_SYS_PTRACE`, `CAP_PERFMON`, `CAP_DAC_READ_SEARCH`); do not treat it as
  an unprivileged drop-in reader.
- Its known issues include unusable color frames while the headset is worn,
  unsettled color calibration mapping, and wake-related camera failures.
  Those are author-reported constraints, not results from our Frame.
- Next question: could a separately audited provider supply complete, correctly
  identified mono frames without disrupting XRService? Keep source identity,
  timestamps, privilege requirements and restoration checks explicit.

Related leads from reference 3:

- [“That's how the Frame sees the world” community post](https://www.reddit.com/r/SteamFrame/comments/1x58r7b/thats_how_the_frame_sees_the_world/)
  is visual/community context, not a documented capture API. No direct
  implementation relationship to Frametop was established by this review.
- [NotZoeyDev/QuestLHSync, steam-frame branch](https://github.com/NotZoeyDev/QuestLHSync/tree/steam-frame/frame)
  contains a Frame-specific camera/driver path for Lighthouse alignment.
  Its [README](https://github.com/NotZoeyDev/QuestLHSync/blob/steam-frame/README.md)
  describes transmitting bright-spot positions rather than images.
  Investigate it as a source-access/calibration lead, not an RGB frame API
  already suitable for Framejacker. Installation changes SteamVR services/drivers
  and is outside this documentation update.

The links in this section follow moving upstream branches; re-pin an audited
revision before implementation. Nothing here changes the project's current
status: recorded replay works; live camera capture is still unimplemented.

## Calibration, spatial labels and Gaussian splats

For 3D labels, retain camera intrinsics/distortion, per-camera extrinsics, the
camera-to-head transform, frame timebase and the matching headset pose. Detecting
an object in 2D does not determine its 3D location; add measured/estimated depth
and transform it into the chosen tracking space. Use calibration from the actual
camera/headset, rather than copying another author's numerical parameters.

The community [dataset recorder findings][hand-recorder] describe factory rig
calibration and beginning/end stereo RGB snapshots. Its continuous video was
**one SLAM camera**, not continuous Arcturus stereo color video. This diagnostic
recording is distinct from the Arcturus camera app's stereo recordings.

[Splat Workshop][splat-workshop] documents the latter as `*_stereo.mp4` with two
video tracks plus calibration/pose metadata. Its workflow is recorded capture →
vendor AV1-to-3DGS scripts → Arcturus's [Spirula Studio fork][spirula] on a GPU
computer → scene conversion/viewing in Gaussian XR on Frame. Spirula supports a
Vulkan backend; this path does not inherently require an NVIDIA/CUDA-only setup.
The vendor fork also documents [calibrated-camera manifests][spirula-manifest].

Keep reconstruction separate from live detection. Start splat experiments with
mostly static scenes; the community recording guide reports smearing from moving
people and pets. Preserve original camera recordings and metadata for
reconstruction; our generated MJPEG environment fixture does not reproduce that
recording format. The CPU-only cloud environment has not validated splat training
or headset splat rendering.

## Implementation sequence and remaining checks

The [active live-capture plan](live-capture.md#current-work-order) owns the
current work order, deliverables and device acceptance checks. The
[dated source review](capture-reference-review.md) records the implementation
evidence and reasoning behind that plan.

Use [status](status.md) for project compatibility and observed results, and the
[on-device guide](on-device.md) for runnable procedures. Keep official SDK
contracts, user device observations, upstream reports and cloud tests distinct.
No live capture SDK or experimental Vulkan layer has been installed by this
project.

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
[arcturus-camera]: https://store.steampowered.com/app/4103250
