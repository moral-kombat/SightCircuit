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

The user's Frame inventory reports `aarch64`, SteamOS 0.4.5 build
`20261007.6125817`, an active SteamVR user service, and `arcimx616` sensor-driver
nodes. The user confirms that the [official Arcturus Camera app][arcturus-camera]
shows live passthrough and can record video. This is evidence of a functioning
vendor capture path on that device.

Our public OpenVR probe was run by the user before and after toggling
passthrough. Both reports show successful background initialization,
`IVRTrackedCamera_006` availability, `HasCamera=true`, camera count zero, and
`OperationFailed` (`100`) from all three frame-size queries before and after
acquisition. Acquisition reports success with a nonzero handle, but header-only
retrieval and stream release return `InvalidHandle` (`101`). No valid frame was
copied. The schema-1 `handle_valid` field records only a nonzero value; it does
not demonstrate that the runtime accepts the handle.

The user also reports that **passthrough goes black while the probe runs**.
This associates the disruption with the probe as a whole; it does not isolate
the responsible call or prove a hardware fault. A successful vendor app and a
failing public API are separate results. The public API report cannot identify
the Arcturus RGB source because it received no frames.

Do not repeat the unchanged probe on this tested runtime. The binary now does
no OpenVR work by default and requires `--allow-passthrough-interruption` for a
deliberate future test after a material change. See the
[device guide](on-device.md#then-verify-camera-access) for the guard and advanced
procedure. The user has confirmed the file-replay window and head-relative VR
panel work on Frame; a supported live-capture interface remains unresolved.

The user's separate file-decode check now succeeds on Frame: PyAV 16.1.0 decoded
three 2464 × 2464 HEVC frames from video-stream ordinal 0 and converted
`yuvj420p` to RGB. The recording has two `hvc1` video tracks, `gpmd` metadata,
audio and fragmented MP4 structure. This establishes selected-file decoding,
not stereo camera identity, calibration or live capture. An annotated Qt replay
window is tested offscreen in the cloud and confirmed working on Frame by the
user. The user has also confirmed video playback, controls and the image
following their head in the native VR panel; see the
[window launch guide](on-device.md#open-the-annotated-replay-window) and
[VR panel guide](vr-overlay.md). These display results use recorded input.

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

The [live capture plan](live-capture.md) expands the provider choice, next code
deliverable and first device trial. A capture-only reader is planned; no
experimental layer has been deployed by this project.

1. Retain the confirmed working Qt replay window and VR panel as device baselines.
   Playback and controls work according to the user. Measure object labels,
   orientation, color, readability, performance and clean shutdown systematically.
   Preserve original video tracks and metadata; track ordinal does not establish
   stereo-side identity.
   The generated cloud fixtures cover detector/presentation integration, not
   vendor color, calibration or stereo alignment.
2. Retain the device inventory and failed public-probe reports with their tested
   runtime versions. Do not rerun the unchanged public probe or deploy an
   experimental capture layer as the next diagnostic step. Its current
   [advanced procedure](on-device.md#then-verify-camera-access) requires an
   explicit interruption flag before any OpenVR calls.
3. Seek a supported live capture contract from Arcturus/Valve and prefer that
   interface if available. Evaluate the cited Vulkan path separately through
   the capture-only reader and device trial in the live capture plan. Any
   eventual live backend must verify color conversion,
   stereo identity, orientation, timestamps, synchronization and behavior after
   runtime updates.
4. Pass frames to the detector with explicit pixel format/stride, camera identity,
   timestamp and timebase provenance, optional producer sequence, calibration
   reference and pose validity. Use a bounded latest-frame queue and measure
   dropped frames, latency and contention with XRService/compositor work.
5. Continue the working VR panel with recorded input and check label readability
   and compositor impact. The public implementation uses Valve's
   [overlay sample][openvr-overlay] as a reference. Add world-space labels only
   after calibration and depth checks.

The user has run the native probe on Frame and verified vendor-app capture,
file replay and VR panel playback with working controls and head-relative
placement. Those device results were not reproduced by the cloud agent. No
camera SDK installation or Vulkan capture deployment has been validated by
this project. Keep public SDK documentation, user device results,
community hardware reports and cloud checks distinct in readiness claims.

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
