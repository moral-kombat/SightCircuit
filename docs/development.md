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
That procedure now passed on Frame ARM64; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame).

### Build and test descriptor transport changes

```bash
bash scripts/build_capture_reference.sh --transport-fixes
artifacts/capture-transport-reference/framejacker-transport-fixture --self-test
```

The optional adaptation applies the [versioned patch](../patches/mrhand/README.md)
to a disposable copy. `tools/capture_transport.hpp` supplies bounded Unix-stream
record handling, owned close-on-exec FDs, same-user peer checks and registry
limits. It freezes one generation per layer snapshot and duplicates FDs while
the registry lock protects the originals. The receiver validates generation,
IDs, bounds and one FD per nonterminal record; it rejects truncated ancillary
data and closes received descriptors on rejection or a partial-record timeout.
The whole client exchange and each server send batch have a 500 ms deadline.

Separate inspection artifacts and a build report go to
`artifacts/capture-transport-reference/`; the report records the patch/helper
hashes and `upstream_sources_modified: true`. The two capture binaries retain
non-executable permissions. Only the original fixture is runnable, explicitly
with `--self-test`; it uses generated bytes and temporary sockets. CI runs its
seven groups and checks both build modes. Live layer callbacks, GPU imports and
provider publication are not exercised by those tests. The active plan owns
remaining provider work and the [device guide](on-device.md#test-the-transport-adaptation-without-capture)
owns ARM64 execution.
The user reports the adapted build and seven fixture groups passed on Frame;
see [E25](status.md#e25-descriptor-transport-on-frame).

### Build and test owned publication

```bash
bash scripts/build_capture_reference.sh --publication-fixes
bash scripts/build_shm_reader.sh
python3 -m unittest discover -s tests -p test_capture_publication.py -v
```

This mode applies both pinned adaptation patches and adds
`tools/capture_publication.hpp` plus the existing `shm_common.hpp`. Its separate
inspection artifacts are under `artifacts/capture-publication-reference/`.
The report includes both patch hashes and all helper hashes. No produced capture
binary is loaded/executed. Native fixtures are original test programs.

The publisher keeps the pinned 96-byte/two-slot ABI. It requires an owned private
parent directory, creates a new ordinary file exclusively, maps at most two
16 MiB planes, checks identity/size/permissions before publication and rejects
invalid or regressing metadata. Older or more-than-one-second-old observations
are skipped. The writer places a full barrier after the odd sequence marker
and release-publishes even after payload/metadata. This protects publication;
it does not synchronize the original XRService GPU writes.

Normal cleanup unmaps/closes and removes its identity-checked file; existing or
replaced paths are preserved. The future runtime source is
`$XDG_RUNTIME_DIR/framejacker-capture/frames.shm`. The adaptation drops image-dump
mode, parses explicit duration/rate/opt-in before capture work and checks a
1–10 second deadline/SIGINT/SIGTERM in its stream loop. Parent death uses SIGKILL;
its stale file may remain, and overwrite is refused. No automatic stale-file
recovery is implemented.

The eight process tests use the actual publisher helper, generated 1024 × 1024
luma slots and the existing reader. They check byte/metadata association in
both slots, no-overwrite behavior, replaced-file preservation, signals, deadline
and parent death/reaping. The native self-check also covers invalid bounds,
stale/order/counter handling, truncation before a write and private runtime paths.
CI builds the tools before these tests; absent binaries cause explicit skips
in environments that run discovery without building them.

The loop deadline cannot bound every driver call, and a concurrent truncation
can still fault a mapping between checks. Complete the external process watchdog,
GPU import/resource cleanup, registry refresh, layer lifetime and restoration
before activation. All eight publication tests also passed on Frame ARM64;
retain the [on-device procedure](on-device.md#test-owned-publication-without-capture)
for changed code/runtime.

### Build and test the GPU capability query

```bash
bash scripts/build_capture_gpu_check.sh
python3 -m unittest discover -s tests -p test_capture_gpu_check.py -v
```

The builder uses system Vulkan headers or the same pinned clean Vulkan-Headers
cache as the reference builder. It fetches no capture code and runs no output.
The report and binary are under `artifacts/capture-gpu-check/`. The original
native query uses `VK_NO_PROTOTYPES` and links `libdl`, not Vulkan; `dlopen` and
instance creation occur only after explicit opt-in. The default/invalid-option
tests use a fake loader constructor to verify that it is never loaded.

The opt-in queries Vulkan 1.1 physical-device interfaces; the reviewed path
requires a device advertising 1.3. It enumerates all devices without selecting
the first one, caps devices/extensions/modifiers/queues/memory types, and checks
the reviewed NV12 modifier plus external DMA-BUF sampled/transfer-source usage.
It reports memory planes, importability/dedicated-only flags, extent/layer limits,
sampler conversion, host-visible/cached types, graphics queues and R8 linear blit
features/transfer image limits. No logical device or camera access is involved.

`scripts/capture_gpu_check.py` provides a ten-second worker watchdog with bounded
stdout/stderr, process-group termination, SIGKILL fallback and worker reaping.
The native child sets parent-death SIGKILL and disables core dumps before loading
Vulkan. Normal/error paths destroy the owned instance and unload the library;
forced termination reports unconfirmed API cleanup instead of success. Existing
loader/layer configuration is not overridden, installed or removed.

The eleven tests build a test-only dispatch library in a private temporary
directory. They validate query structures and real function-pointer dispatch,
four memory planes for a two-color-plane format, absent features/extensions,
image/extent limits, oversized counts, driver errors, hangs during creation and
destruction, crash containment, cancellation, parent death and bounded output.
They make no GPU or camera calls. Native tests explicitly skip if the query is
unbuilt; the dedicated CI job builds it before running them. Hosted CI results
are separate from local results. For the real Frame query, use
[the supervised device command](on-device.md#query-vulkan-capture-capabilities).
The first Frame query now passes on Turnip Adreno 750 with clean teardown and
normal passthrough; see [E30](status.md#e30-vulkan-capabilities-on-frame). Its
target modifier reports two memory planes; the generic fake-loader test's four
planes are a separate synthetic case.

### Build and test image import ownership

This work can run without the headset, a camera or a working GPU driver:

```bash
bash scripts/build_capture_reference.sh --import-fixes
python3 -m unittest discover -s tests -p test_capture_import.py -v
```

The fourth build mode applies patches 0001–0003 to a disposable copy of the
same pinned capture source, adds `tools/capture_import.hpp`, and writes separate
inspection artifacts under `artifacts/capture-import-reference/`. The report
includes the import patch/helper hashes. The actual layer/producer stay
non-executable and are never loaded/run by this procedure. The executable
fixture is original synthetic code, defines `VK_NO_PROTOTYPES` and links no
Vulkan loader. It uses function pointers and generated `memfd` payloads.

The helper accepts only the reviewed 2464 × 2464 NV12 profile, **one** array
layer, DMA-BUF, DRM modifier `0x0500000000000001`, sampled/transfer-source usage
and **zero** binding offset. It creates non-disjoint exclusive images. Exporter
flags/sharing mode are absent from this wire contract and remain unverified.
A candidate's
metadata is checked before the adapted producer initializes Vulkan. Declared
allocation size is capped at 1 GiB; active plane offsets/pitches must fit at
least one row within it, and unused plane metadata must be zero. Compressed
memory planes are checked against the driver's modifier list, not inferred
from the format's two color planes. This is a conservative initial profile;
other formats, offsets, flags and source types need separate validation.
Arrayed images are rejected because this record has no per-plane `arrayPitch`;
the importer must not invent that layout. For a single layer and depth one,
the explicit modifier layout's array/depth pitches are correctly zero under
[Vulkan 1.4.309's layout rules][modifier-layout].

At import, bounded modifier and external-image queries validate the actual
physical device's format/usage, importability and extent/layer/resource limits.
The allocation type intersects FD and image memory-type bits with the physical
device's valid type range. Zero/oversized requirements and invalid alignment
are rejected. Dedicated allocation uses the exact image requirement size,
which must fit within the declared allocation, as required by
[Vulkan 1.4.309, VUID 02964][dedicated-allocation]. It does not use the upstream
`max(requirement size, declared size)` allocation. The actual external FD length,
full compressed-plane footprint, and exporter GPU/driver provenance are not
established by these metadata checks.

An imported image is move-only. Its duplicated CLOEXEC FD remains application
owned until `vkAllocateMemory` succeeds; failure closes it, and success transfers
it to Vulkan without touching it again. Failed image creation does not acquire
an undefined output handle. Allocation/binding failure unwinds owned resources;
normal retirement destroys the image before freeing its memory. Partial-batch
failure also retires earlier images.

The shared serial-queue submission helper checks fence reset, queue submission
and its two-second completion wait. Command reset/begin/end and mapped-memory
invalidation results are also checked in the adaptation. Only a successful
completion wait permits imported-image destruction. An uncertain submission
leaves the gate non-idle; cleanup marks deferral and abandons API destruction
to process teardown. The borrowed device and completion gate must outlive all
image owners. They are confined to one worker thread. No unbounded idle wait
is added, but a driver call itself can still hang: the capture supervisor
remains to be implemented. This gate does not synchronize XRService's writes.

Eleven Python tests execute ten native fixture groups plus default/argument guards.
They cover malformed metadata before driver calls, unsupported modifier/memory
planes, format/limits, requirement bounds, memory bit 31, FD transfer and cleanup
order, failure injection at FD query/create/allocate/bind, move/vector ownership,
batch rollback, pending-work deferral, and reset/submit/wait failures. An explicit
two-plane modifier case matches the count advertised in the Frame capability
report; its offsets/pitches are generated, not observed camera metadata. The
generic four-plane supported case remains covered, and a four-plane record is
rejected when the driver advertises two. The native
fixture counts FDs before/after each group. Deferred resources in its fake
backend are reclaimed by simulated process teardown, not described as graceful
Vulkan cleanup. CI builds before running these tests; unbuilt native fixtures
produce explicit skips elsewhere.

The original ten-test suite and import build also passed on Frame ARM64;
the eleventh two-plane case has only been run in the cloud. The GPU query reports
two modifier memory planes and dedicated-only imports on Turnip Adreno 750;
see [E30](status.md#e30-vulkan-capabilities-on-frame) and
[E31](status.md#e31-import-build-and-synthetic-tests-on-frame).
Compile/link and synthetic tests do not validate a real import. The earlier
import-only mode still leaves device and other GPU resources unmanaged; the
resource adaptation below addresses those lifetimes. Capture supervision,
provider metadata/registry and layer lifetime/restoration remain pending. See the
[offline work order](live-capture.md#offline-provider-work-while-the-frame-is-unavailable)
and [optional native fixture procedure](on-device.md#test-import-ownership-without-capture).

[dedicated-allocation]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/memory.adoc#VUID-VkMemoryDedicatedAllocateInfo-image-02964
[modifier-layout]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/resources.adoc#VkImageDrmFormatModifierExplicitCreateInfoEXT

### Build and test GPU resource ownership

```bash
bash scripts/build_capture_reference.sh --resource-fixes &&
python3 -m unittest discover -s tests -p test_capture_gpu_resources.py -v
```

This fifth mode applies patches 0001–0004 to disposable pinned source and adds
`tools/capture_gpu_resources.hpp`. Separate artifacts/report live under
`artifacts/capture-resources-reference/`; the report includes the new helper and
patch hashes. The real layer/producer remain non-executable inspection binaries
and are never loaded/run by this procedure. Only the original synthetic fixture
runs. It defines `VK_NO_PROTOTYPES`, links no Vulkan loader and uses injected
function pointers, fake handles and a generated memfd for import lifetime tests.

Bootstrap requires loader/device Vulkan 1.3, the four reviewed import extensions,
YCbCr conversion, graphics queues, host-visible/cached and device-local memory,
and the reviewed R8 downscaling capabilities. Counts are bounded. CPU devices
are excluded; multiple compatible hardware candidates are rejected for explicit
selection instead of choosing the first. This capability policy does not prove
which physical device exported a camera FD. NV12/modifier/FD checks still occur
per import through the existing import helper.

One shared state owns instance, device, command pool and the serial-queue
completion gate. Move-only private resources and imported-image wrappers retain
that state until their own destruction. Host buffers own their mapping and
memory; R8 images own dedicated memory; command bundles own buffer/fence pairs.
Normal teardown unmaps, destroys resources before freeing bound memory, and
releases pool/device/instance last. Explicit device close refuses live children.
Imported images also retain the device across wrapper moves and parent scope exit.

Private allocations use exact dedicated requirement sizes, compatible memory
types and a **256 MiB aggregate budget**. Host buffer requests are capped at
64 MiB; scratch dimensions at 2464 × 2464. Coherent cached host memory is
preferred; cached noncoherent memory has checked invalidation over the whole
mapped allocation. The budget covers new private allocations, not the imported
shared camera allocations bounded separately by the import/transport contract.

If submitted work is unconfirmed, all remaining resource/parent API destruction
is deferred to process teardown. A deferred state stays unusable even if a later
caller marks its queue idle; it cannot recover abandoned handles. No unbounded
device-idle wait is added. Individual create/destroy calls can still hang, so the
capture watchdog remains necessary before actual driver execution. This gate
tracks only our queue and does not synchronize XRService's writes.

Nine tests execute eight native groups plus default/argument guards. They
exercise normal and partial teardown, 57 injected failures (three passes over
19 fallible startup/private-resource calls), graphics-family selection,
ambiguous devices, API/count/size/budget limits, noncoherent fallback, memory
bit 31, moves, child-retained devices, cross-context imported-image moves, and
deferred cleanup. The fake backend checks the resource graph and models process
reclamation separately from graceful Vulkan destruction. Missing fixture builds
produce explicit skips; CI builds this mode before running its tests.

This implementation is cloud-tested with fake dispatch, not actual logical-device
creation, command execution or Frame ARM64 validation. The
[native fixture procedure](on-device.md#test-gpu-resource-ownership-without-capture)
can be batched with a later milestone. Follow the
[next work order](live-capture.md#next-engineering-step-capture-worker-supervision)
before deployment.

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
also decodes the actual PNG. The user reports all 14 tests passed on Frame ARM64
in 7.204 seconds; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame).

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
