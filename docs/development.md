# Development setup

This guide owns cloud/desktop setup, dependency environments, build commands
and automated checks. The development smoke check targets Linux x86_64 with
Python 3.12; it does not launch the interactive application or connect to a camera.
Use the [on-device guide](on-device.md) for Frame and [status](status.md) for
validation evidence and compatibility limits.

## Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) using its
documented instructions. Setup was tested with uv 0.12.23. From the repository:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

The setup script resolves its working directory from its own location, creates
`.venv` with Python 3.12, synchronizes the pinned development packages, checks
dependency compatibility, and downloads the three smoke-test fixtures. Running
setup again reuses the environment and verifies cached fixture checksums. The
dedicated `.venv` is managed by this script; dependency synchronization removes
packages absent from `requirements-dev.txt`.

The first run needs PyPI package downloads and
`raw.githubusercontent.com` for the model/image fixtures. If uv needs to obtain
Python, its managed-Python download also needs GitHub release access. No API keys
are required for the current checks.

Setup uses normal TLS verification. The fixture downloader verifies SHA-256
against `scripts/smoke-assets.json` before making a download available. An
existing cached fixture with a different checksum causes an error. Inspect and
remove that specific generated file before downloading it again; do not change
the expected checksum just to make the check pass.

## What validation demonstrates

The smoke check generates an MJPEG video from 12 copies of the upstream sample
image, reads all 12 frames with OpenCV's FFmpeg backend, runs MobileNet-SSD on CPU,
and requires bicycle, car and dog detections on each frame. Qt renders the
annotations and writes a nonblank screenshot. A separate bundled ONNX
multiplication graph executes with checked numerical output.

It invalidates the previous `artifacts/smoke/readiness.json` before importing
runtime dependencies and writes a new report only after success. The detector
fixture is a development check, not a selected production model.

| Path | Contents | Committed? |
| --- | --- | --- |
| `.venv/` | Dedicated Python environment | No |
| `.cache/uv/` | Default package cache | No |
| `.cache/framejacker/assets/` | Verified model, model configuration and sample image | No |
| `artifacts/smoke/readiness.json` | Current passing validation report | No |
| `artifacts/smoke/detection-window.png` | Rendered detection result | No |
| `artifacts/smoke/sample.avi` | Generated repeated-image MJPEG video | No |
| `scripts/smoke-assets.json` | Immutable fixture URLs and SHA-256 checksums | Yes |

Set `UV_CACHE_DIR` or `XDG_CACHE_HOME` before setup to reuse another writable
package/font cache. The fixture directory stays repository-local. The downloader
also accepts `--asset-dir` for isolated download checks; the smoke check uses the
default fixture directory.

## Window rendering and hardware

The default smoke check sets `QT_QPA_PLATFORM=offscreen` when unset. It shows no
visible desktop UI and exits after checking the frames. Visible presentation
needs a working display server and Qt platform libraries. The interactive
prototype is `scripts/replay_window.py`. OpenCV is installed headless; the
replay app uses Qt for presentation.

OpenCV 4.12 is pinned because the Caffe importer needed by this fixture was
removed from OpenCV 5. The wheel includes its own FFmpeg backend; the current
smoke script does not invoke a separately installed `ffmpeg` executable.

Do not copy an x86_64 virtual environment onto the headset. Device dependencies
and camera/runtime capabilities must be installed and verified for the target
architecture. [Status](status.md) distinguishes device confirmations from cloud
tests and lists unverified capture, geometry and performance capabilities.
See the [camera guide](steam-frame-camera.md) and
[implementation milestones](architecture.md) for their technical constraints.

## Recorded MP4 inspection

`python3 scripts/recording_check.py /path/to/recording.mp4` inspects container
headers without opening a camera, decoding frames, or installing packages. With
no path it selects the latest `*_stereo.mp4` in
`~/Videos/ArcturusVisionCamera`. Stop the recording before inspection. Track
sample-entry codes and dimensions guide decoder selection; successful metadata
inspection does not establish decoding or detection support for that recording.

On the headset, `bash scripts/frame_replay_check.sh` combines this inspection
with `scripts/device_check.py`. It is intentionally read-only and does not
initialize OpenVR.

Run the dependency-free parser checks with
`python3 -m unittest discover -s tests -v`. They cover track metadata, malformed
containers, parsing limits, media-payload skipping and nonregular-file rejection.
Decoder integration tests are skipped when PyAV is unavailable.

## File-decoding environment

`bash scripts/setup_replay.sh` creates a separate Python 3.12 `.venv-replay` and
installs the pinned binary wheel in `requirements-replay.txt`. Set
`FRAMEJACKER_PYTHON=/path/to/python3.12` if the default `python3` differs.
This check does not need the full desktop requirements or `uv`.

```bash
.venv-replay/bin/python scripts/replay_decode_check.py /path/to/recording.mp4
.venv-replay/bin/python -m unittest discover -s tests -v
```

PyAV selects the requested video stream and converts a few decoded frames to
RGB without NumPy, OpenCV or Qt. Tests generate real fragmented HEVC with two
different video-track dimensions, exercise both selections, and check failures
for absent streams and invalid/nonregular files. They do not reproduce Arcturus
calibration, metadata payloads, dependent stereo views or HDR color management.

## Annotated replay window

Replay and bounded mirror recognition share `scripts/demo_detector.py`: the
same checked MobileNet-SSD model, CPU execution, 20 labels, preprocessing and
annotation code. Boxes now show explicit percentage confidence scores. These
scores are not calibrated probabilities; the device's rug-as-sofa false positive
is recorded in [status](status.md#e15-saved-room-recognition-and-rug-false-positive).

```bash
bash scripts/setup_window.sh
.venv-replay/bin/python scripts/replay_window.py /path/to/recording.mp4
```

Setup extends `.venv-replay` using `requirements-window.txt` and verifies the
existing model/image fixtures. The window runs PyAV and CPU MobileNet-SSD on a
worker thread, keeps one latest annotated result, and presents it on the Qt
thread. The prototype has pause/resume, replay and loop controls. Processing
replay is capped at 10 fps by default and preserves every processed frame's
association with its detections; this is not a real-time source-timing player.

To run a finite, offscreen check with explicit output files:

```bash
mkdir -p artifacts/replay-window
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
  /path/to/recording.mp4 --max-frames 4 \
  --preview artifacts/replay-window/preview.png \
  --report artifacts/replay-window/report.json
```

Output paths must be new and distinct from the input recording. Without these
flags the app saves no images or reports; it prints a JSON summary on exit.
Use a new output path for subsequent checks. `--video-stream` selects a video
track; no stereo-side identity is inferred. `--threshold` selects detection
confidence. The demo has 20 classes and is not a selected production detector.
Device presentation results and remaining orientation, color/HDR, accuracy and
performance limits are recorded in [status](status.md#e6-window-and-vr-panel).
Linux ARM64 Qt wheels require glibc 2.39+
and graphical-system dependencies; the native X11 path checks `ldd` output for
missing plugin libraries before creating a QApplication.

Run `.venv-replay/bin/python -m unittest discover -s tests -v` after window
setup for the complete parser, decoder and offscreen display checks. The window
tests generate two-track replay video and require bicycle/car/dog detections,
a nonblank preview, correct track selection and input-file preservation.

## Continuous integration

The [GitHub Actions workflow](../.github/workflows/checks.yml) runs the same setup
and smoke check on an Ubuntu runner with Python 3.12. It needs network access to
the fixture sources. A file-decoder job installs PyAV and exercises fragmented
HEVC replay and RGB conversion. A separate job builds the native OpenVR probe, verifies that
default and invalid invocations stop before OpenVR initialization, and checks
missing-runtime diagnostics with explicit opt-in. The probe has interrupted
visible passthrough on the tested headset; do not repeat that unchanged hardware
test. CI does not stand in for a headset test. Local execution
of these commands is separate from a successful GitHub Actions run after push.

The replay-window job installs the window requirements and exercises decoding,
inference, Qt offscreen presentation and file-protection behavior end to end.
The dependency-free mirror job exercises discovery, native Linux UAPI layout,
read-only capture guards, pixel bounds, deadlines, cancellation and optional
preview publication without camera hardware.
The standalone shared-memory job builds both native helpers and checks
cross-process publications, stale/malformed input, source mutations, native
fault containment, deadlines and preview publication with synthetic images.

## Existing SteamVR mirror diagnostic

`python3 scripts/mirror_check.py` performs sysfs-only discovery. Opt-in capture
uses Python's standard library and the current V4L2 format; no PyAV, OpenCV, Qt,
OpenVR or system package installation is needed. It supports Linux x86_64 and
ARM64 single-plane V4L2 loopback mirrors named `SteamVR` that advertise `read()`.
Format support is intentionally explicit: GREY, NV12/NV21, YU12/YV12,
YUYV/UYVY/YVYU/VYUY and RGB3/BGR3. Other formats/capabilities are reported.

The worker opens the verified node with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`,
uses only `VIDIOC_QUERYCAP` and `VIDIOC_G_FMT`, and calls nonblocking `read()`.
The first read is not gated on `select()`/`poll()`: some v4l2loopback releases
register file-I/O capture inside that call, before readability can be signaled.
The driver can initialize capture internally; no explicit queue or stream-on
ioctl is sent by this tool. Read attempts and would-block/interrupted reads are
reported separately from complete images. A lazy-start backend regression check
exercises first-read registration followed by frame delivery without polling.
No `S_FMT`, buffer-queue setup or explicit stream-on operation is requested.
Raw buffers are bounded at 64 MiB and dimensions at 16 Mi pixels; samples skip
padding and chroma. The supervisor enforces the driver deadline and handles
Ctrl+C/SIGTERM; Linux parent-death signaling stops the worker if its launcher dies.

```bash
python3 -m unittest discover -s tests -p test_mirror_check.py -v
```

Tests use controlled sysfs/device backends and real subprocesses. The ABI test
compiles a tiny Linux-header program when `g++` and UAPI headers are installed;
otherwise that check is explicitly skipped. With PyAV available, preview testing
also decodes the generated PNG. No test opens an actual camera. Read the
[device procedure](on-device.md#check-an-existing-steamvr-mirror) before opt-in
acquisition, and [status](status.md#e8-mirror-diagnostic-cloud-checks) for actual
validation evidence.

## Bounded mirror recognition

`scripts/mirror_recognize.py` reuses the guarded capture loop and supervised
process deadline. Default execution performs metadata-only discovery. Opt-in
capture/inference needs the existing OpenCV/NumPy environment and checked model
fixtures, with no Qt, PyAV or OpenVR initialization. The recognizer accepts only
RGB3/BGR3, respects row padding and converts to owned BGR images of at most 960
pixels on the longer side. A capture thread retains one latest pending read;
slow inference replaces old input rather than accumulating a queue.

```bash
.venv-replay/bin/python -m unittest discover -s tests -p test_mirror_recognize.py -v
```

Tests cover guards, channel order/padding, overload and exact source/result
association, old/empty input, model/read/close failures, cancellation and real
spawned-model/preview publication with the checked fixture. Image encoding is
checked to occur after source closure. Destinations created concurrently remain
untouched. Dependency-specific tests skip without OpenCV/NumPy; the replay-window
CI job installs these and exercises the complete suite. Existing supervision
tests cover hard deadlines, abnormal worker exit and process reaping.

The final report carries at most 20 detections from the last processed input,
local observation/processing timing, captured/processed counts and replaced
pending inputs. One pending read, one in-flight inference and bounded result
storage limit memory; the kernel driver's own buffering is separate. The
supervisor budget includes 15 seconds beyond capture duration for model startup
and completion, then bounded cleanup. File output is opt-in and published only
after capture/inference finishes. Read [the device procedure](on-device.md#run-bounded-recognition-from-the-mirror)
before the first Frame trial; [status](status.md#e16-bounded-mirror-recognition-cloud-checks)
records cloud-only validation and the model-quality limitation.

## Dedicated capture build prerequisites

Collect metadata on the target machine before preparing a native capture build:

```bash
python3 scripts/device_check.py --capture-prerequisites
```

The focused report needs only Python's standard library. It runs bounded
`g++ --version` and `pkg-config` queries and inspects known paths with `lstat`.
It does not load Vulkan, initialize OpenVR, connect to a provider or read images.
The default command without this option still produces the original device
inventory. Missing tools/files are reported rather than installed.

The reviewed community layer needs a C++17 compiler and Vulkan development
headers, including `vulkan/vulkan.h` and `vulkan/vk_layer.h`. Its `ptgrab` producer
also links Vulkan. Runtime `libvulkan.so.1`, unversioned linker `libvulkan.so`,
headers and `pkg-config` metadata are separate prerequisites. Known-path presence
does not prove that a symlink resolves, a build links or the target GPU imports
camera buffers; these searches are not an exhaustive library discovery process.
Use missing-file results to prepare a pinned build dependency in an ignored
cache when appropriate, without changing SteamOS protections as a routine step.

The report also lists metadata for the reviewed community layer manifest,
library, enable marker, runtime socket and luma shared-memory path. Presence
does not establish compatibility or a running provider. No installer or capture
build is invoked. See [the operating procedure](on-device.md#check-native-capture-prerequisites)
and [the capture plan](live-capture.md#current-work-order) for the next device step.

### Compile the unmodified capture reference

```bash
bash scripts/build_capture_reference.sh
```

This build-only script compiles `handcam_layer.cpp` and `ptgrab.cpp` from the
reviewed `ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082` capture subtree. The clean,
pinned source cache is `.cache/capture-reference`; altered caches are retained
and refused. No upstream build/install script is executed. When the compiler
cannot find both Vulkan headers, it fetches Vulkan-Headers v1.4.309 at
`952f776f6573aafbb62ea717d871cd1d6816c387` into `.cache/vulkan-headers`, including
license texts. It links the producer directly to Linux `libvulkan.so.1`; this
checks loader linkage without requiring an unversioned development symlink.
The loader itself must already be installed. No package manager is invoked.

Outputs are `reference-libVkLayer_mrhand.so`, `reference-ptgrab` and
`build-report.json` in `artifacts/capture-reference/`. The report records source
and artifact SHA-256 digests, compiler target and header origin. Both ELF files
lack execute permission. No binary is run/loaded and no layer manifest is
created. `deployment_ready: false` is intentional: the upstream FD/socket,
publication-ordering and cleanup issues remain. These artifacts prove only
that the reference compiles and links on the tested machine. The provider
adaptation, configuration restoration and controlled live trial remain pending.

The dedicated CI job builds these inspection artifacts and checks their hashes,
ELF headers and permissions without executing them. For Frame, follow
[the combined build and reader-test procedure](on-device.md#build-the-capture-reference-without-activation).

## Isolated luma reader and synthetic producer

The shared-memory diagnostic is independent of inference, Qt, OpenVR and Vulkan.
Build it with the existing Linux C++17 compiler on x86_64 or ARM64:

```bash
bash scripts/build_shm_reader.sh
python3 -m unittest discover -s tests -p test_shm_capture_check.py -v
```

No SDK download, package installation, capture-layer activation or producer
startup occurs during the build. Native tests skip if the binaries are absent;
the dedicated CI job builds them first. With PyAV installed, the preview test
also decodes the actual PNG. Native ARM64 tests on Frame remain pending.

For a manual generated-image check, run this producer in one terminal:

```bash
mkdir -p artifacts/shm-check
artifacts/shm-reader/framejacker-shm-fixture \
  --create artifacts/shm-check/new-synthetic.shm --seconds 10 --mode split
```

While it is running, use a second terminal in the same checkout:

```bash
python3 scripts/shm_capture_check.py --read \
  --source artifacts/shm-check/new-synthetic.shm --camera 1 --seconds 3
```

The fixture publishes two 64 × 48 grayscale slots with differing uniform
intensities and deliberately split writes. It prints explicit synthetic
ready/closed events, runs for a bounded duration and leaves its ordinary file
for inspection. Use a new filename for each run; existing files and the
upstream `/dev/shm/mrhand-pt` path are refused. It does not acquire camera
images. Other controlled modes exercise unfinished, old, stalled, truncated,
replaced and restarted publications. Both tools require explicit arguments;
running either native binary without arguments returns `not_run`/exit `3`.

The pinned [protocol header and notice](../third_party/mrhand/README.md) define
a 96-byte native header with two 32-byte slots, followed by `cams` luma planes.
Only 1–2 slots and at most 16 MiB per plane are accepted. The consumer requires
lock-free aligned 64-bit atomic sequence access, a stable even sequence around
each bounded copy, valid monotonic observation time and an unchanged source.
It maps read-only and performs at most ten copy attempts per second.

The original fixture adds a full writer barrier after the odd marker and a
release-store of the final even sequence. The reader uses acquire loads and a
barrier before its final sequence check. These controlled x86_64 checks do not
validate the upstream writer on ARM64 or original GPU-image completion. That
writer needs review/correction before a dedicated-provider trial. The layout
also lacks a generation field; observable counter regressions are rejected,
but not every restart can be detected between samples.

The Python supervisor contains native mapping faults (including a truncation
race) and enforces a deadline plus bounded terminate/kill cleanup. Linux
parent-death signaling prevents a stranded native reader; core dumps are
disabled in that reader. Optional PNG output is encoded only after input
closure and published atomically to a new path. No pixels are saved by default.
See [the device procedure](on-device.md#check-existing-luma-shared-memory) for
existing-provider usage and [evidence](status.md#e9-shared-memory-reader-cloud-checks)
for actual validation limits.

## Native VR panel build and validation

`bash scripts/build_overlay_bridge.sh` builds the native tool into ignored
`artifacts/overlay-bridge/`, using the pinned SDK in `.cache/openvr`. The camera
and overlay builders share `scripts/build_openvr_tool.sh`; neither builder runs
a runtime test. The camera binary's explicit opt-in guard remains in place.

The overlay job checks strict C++ syntax, packet bounds, malformed input, idle
payload timeouts, signals and named missing-runtime diagnostics. The replay
window job also builds the bridge so its validation-only mode can check actual
RGBA forwarding and Qt control creation. This mode reports no OpenVR
initialization and zero submitted frames. For the real hardware procedure,
read [VR replay panel](vr-overlay.md).
