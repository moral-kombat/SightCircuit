# Retained device checks for the paused capture track

**Deferred reference.** These build, synthetic-validation, GPU-query, shared-memory,
and advanced-probe procedures belong to the paused dedicated-camera track.
They are not the next onboarding steps. Use the [current plan](../live-capture.md)
for active work and [device access](../on-device.md) for execution context.

The procedures retain their original milestone context and restrictions. A
build or generated-data test does not establish real camera access. GPU queries,
provider reads, and the advanced camera probe have different effects; read the
individual prerequisites. The unchanged public probe interrupted passthrough
and must not be repeated. [Trial history](../history/capture-trials.md) is
separate from these checks.

## Check native capture prerequisites

Use the normal Frame terminal to collect the build metadata needed for a
capture-only provider. Arcturus is connected, but its selection as the current
passthrough source is unknown; this command does not select or identify it.
Update a clean checkout and run:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
python3 scripts/device_check.py --capture-prerequisites
```

This standard-library check reports architecture/OS, the C++ compiler version,
Vulkan `pkg-config` metadata, known header/library paths and existing community
capture configuration paths. It inspects file metadata without reading images,
connecting to a socket, loading Vulkan or initializing OpenVR. It does not
install packages, enable a layer, change settings or restart SteamVR.

Share the JSON report. Missing development files describe build prerequisites,
not a camera failure. A runtime `libvulkan.so.1` alone does not establish that
headers or linker files are installed. Search locations are limited, and
symlink targets are not verified. Do not unlock SteamOS or run the upstream
installer to fill gaps; choose the build approach from the report first.
The [development guide](capture-provider.md#dedicated-capture-build-prerequisites)
explains the distinction. The Frame report now confirms GCC 15.1.1 and Vulkan
1.4.309 development metadata/headers; see [E21](../status.md#e21-capture-prerequisites-on-frame).
Compilation and real capture remain separate checks.


## Build the capture reference without activation

The pinned reference build is an intermediate compiler/linker compatibility
check. It has passed on cloud x86_64 and Frame ARM64, along with all 14 synthetic
reader tests on Frame; see [E23](../status.md#e23-reference-build-and-controlled-reader-on-frame). Its
unmodified upstream code still needs the lifecycle and publication fixes in
the [capture review](../capture-reference-review.md#findings-that-affect-our-implementation)
before deployment. It does not provide a runnable live-capture command.

In the existing Frame checkout, run:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh &&
bash scripts/build_shm_reader.sh &&
.venv-replay/bin/python -m unittest discover -s tests -p test_shm_capture_check.py -v
```

The reference build needs Bash, Git, Python 3, C++17 and a linkable Linux Vulkan
loader. It fetches pinned source from GitHub into ignored `.cache/`, preserving
notices and rejecting changed caches. Existing compiler-search-path Vulkan
headers are used when available; otherwise pinned headers are fetched. No
package installation or upstream script execution occurs. It writes two
non-executable inspection artifacts and `build-report.json` under ignored
`artifacts/capture-reference/`. No manifest or enable marker is generated, and
neither artifact is loaded or run. SteamVR and camera-source settings are untouched.

Share the build report and test summary. The existing shared-memory tests use
synthetic images in temporary ordinary files, not cameras or the compositor.
They check native ARM64 reader behavior without activating a provider. A passed
build/fixture test does not establish live-camera synchronization or compatibility.
Keep the reference artifacts for inspection; do not install them, add execute
permission or run `ptgrab` as the next step.


## Test the transport adaptation without capture

The first provider adaptation repairs descriptor ownership and bounded socket
messages. The adapted build and all seven fixture groups have now passed on
Frame ARM64; see [E25](../status.md#e25-descriptor-transport-on-frame). Keep this
procedure for a changed implementation/runtime; the next check is publication below.

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --transport-fixes &&
artifacts/capture-transport-reference/sightcircuit-transport-fixture --self-test
```

The build applies our versioned patch to a disposable source copy, preserving
the pinned upstream cache and the earlier reference artifacts. It creates
separate non-executable capture inspection binaries plus an original synthetic
test executable. The explicit `--self-test` runs seven test groups using Unix
sockets and small generated `memfd` payloads; no camera or Vulkan calls occur.
The tests include partial records, malformed/extra descriptors, mixed generations,
stalls, disconnects and descriptor closure. A private temporary socket
directory is removed after its test. Share the build report and fixture JSON.

This remains a transport-only adaptation. GPU import, shared-memory publication,
provider lifetime and exact restoration still need work before deployment; no
capture binary is run and no layer configuration is created by this procedure.


## Test owned publication without capture

This adaptation replaces the producer's shared-memory writer and adds bounded
loop controls. All eight tests passed on cloud x86_64 and on Frame in 1.562
seconds; see [E27](../status.md#e27-publication-and-loop-controls-on-frame).
Retain this procedure for changed code/runtime. The GPU query and original
import suite below have also passed; remaining engineering tasks are in the
[active plan](../live-capture.md#offline-provider-work-while-the-frame-is-unavailable).
In the existing Frame checkout, run:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --publication-fixes &&
bash scripts/build_shm_reader.sh &&
.venv-replay/bin/python -m unittest discover -s tests -p test_capture_publication.py -v
```

This build applies both versioned patches to a disposable source copy and keeps
the pinned cache and earlier builds intact. Capture inspection binaries remain
non-executable in `artifacts/capture-publication-reference/`. The tests run an
original fixture using the same publication/control helpers as the adaptation,
plus the existing native reader. They create generated grayscale frames in
private temporary directories, validate both slots, preserve existing/foreign
files, and check deadlines, signals and parent death. No camera, Vulkan or
OpenVR calls occur. Share the build report and final test summary; expect eight
tests with no skips.

The future provider source is a private runtime file at
`$XDG_RUNTIME_DIR/sightcircuit-capture/frames.shm`; the upstream
`/dev/shm/mrhand-pt` file is not used by this publication adaptation. The tests
use temporary paths and remove their generated inputs. Normal publisher cleanup
removes only the identity-checked file it created. Abrupt parent death kills
the writer; a stale file can remain and a new publisher refuses to overwrite it.
Recovery/lease handling remains part of provider preparation.

The stream loop has a 1–10 second deadline and signal checks. GPU driver calls,
GPU resource cleanup, registry refresh, layer lifetime and exact restoration
still need work; this is not a live-capture procedure. No layer manifest or
enable marker is generated, and no capture binary is run by these commands.


## Query Vulkan capture capabilities

This check passed on Frame: Turnip Adreno 750 advertises the required import
capabilities, and the user reports normal passthrough throughout and afterward.
See [E30](../status.md#e30-vulkan-capabilities-on-frame). Keep this procedure for a
changed tool/runtime; repeating it unchanged is not the next task. It queries
the GPU requirements of the reviewed Arcturus import path:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_gpu_check.sh &&
python3 scripts/capture_gpu_check.py --query-device
```

The builder compiles without loading Vulkan. The Python command starts a child
with a ten-second query budget, then terminates/kills and reaps a stuck worker.
The worker disables core dumps and is killed if its parent dies. Output sizes
and driver-provided counts are capped. With no arguments the Python command
returns `not_run`/exit `3` without starting a worker or loading Vulkan. Use the
Python command for the watchdog; do not run the native binary directly.

The opt-in loads the installed Vulkan loader, creates an instance, and queries
physical-device properties, features, memory/queues and image-format support.
It creates no logical device, queue, image or GPU allocation; it opens no camera,
connects to no provider and imports no FD. It installs no layer, changes no
source selection and restarts no service. Existing Vulkan configuration,
including already configured implicit layers, is honored during instance creation.

Share the complete build and query JSON, plus any change to normal passthrough.
`native.devices` lists GPU identity/API, missing required extensions, sampler
conversion, host-visible/cached memory types, graphics queues, R8 blit features
and image limits, and the NV12 `0x0500000000000001` modifier's **memory**-plane
count. Compressed memory planes are not necessarily the two NV12 color planes.
The external-image query uses DMA-BUF, sampled/transfer-source usage and
exclusive sharing; it reports importability and the dedicated-allocation flag.
The reviewed size is 2464 × 2464, with support for up to two array layers.
The query surveys that broader capability; the initial import adaptation below
accepts only a single layer because the wire record lacks array-stride metadata.

Exit `0`/`queried` means the queries completed, even if a device reports missing
capabilities. `capability_checks_passed` describes only the queried requirements,
not a working import or deployment readiness. Exit `1` means a query/driver,
format-report, timeout or cleanup failure; inspect the reason and native error.
`cleanup_ok: false` after forced termination means graceful Vulkan teardown was
not confirmed. Instance creation is `null` when no complete worker report exists.
Ctrl+C/SIGTERM yields `cancelled` and exit `128 + signal`. Invalid Python options
exit `2`. On success, the instance is destroyed before the report is emitted.

The Frame's loader API reports 1.3.296 and its physical-device API 1.4.362;
these differ from the earlier 1.4.309 development-header/pkg-config metadata.
The NV12 modifier reports two memory planes and dedicated-only DMA-BUF import.
Gamescope WSI/application-info and option-override messages appeared on stderr
alongside successful exit/cleanup; no passthrough change was reported.

The cloud has no usable Vulkan driver (`VK_ERROR_INCOMPATIBLE_DRIVER`); its
fake-loader tests validate dispatch and failure containment. The positive Frame
capability result still leaves actual FD import, producer
completion, camera identity, resource lifetime and layer restoration unverified.
Follow the [active provider work order](../live-capture.md#next-provider-step-query-gpu-capabilities)
before any camera trial.


## Test import ownership without capture

The ARM64 build and original ten-test suite passed in 0.015 seconds with no
skips; see [E31](../status.md#e31-import-build-and-synthetic-tests-on-frame). The
current suite adds one synthetic test for the two-memory-plane count advertised
by the real GPU. It passes in the cloud and has not yet been rerun on Frame.
Keep this procedure for changed code/runtime or batch it with a later native
validation milestone; there is no need to repeat the original checks unchanged:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --import-fixes &&
python3 -m unittest discover -s tests -p test_capture_import.py -v
```

Expect eleven tests with no skips for the updated suite. The build applies three
patches to a disposable copy and keeps the real capture binaries non-executable. Its original fixture
uses generated `memfd` data and fake Vulkan function pointers; it neither loads
Vulkan nor connects to a provider. It checks metadata rejection, allocation/FD
ownership, rollback, move operations and completion-gated cleanup. No camera,
layer configuration or service is changed. Share the build report and test
summary when convenient; no immediate Frame access is required for development.

The fixture's supported case is synthetic. It does not prove that actual
Arcturus buffers match the conservative profile, import successfully, have
finished being written or can be displayed without feedback. Complete the
[remaining provider work](../live-capture.md#offline-provider-work-while-the-frame-is-unavailable)
before activation.


## Test GPU resource ownership without capture

The new resource-lifetime adaptation is cloud-tested; ARM64 execution is pending.
This optional check can be batched with a later milestone. It does not create
a real Vulkan device or open a camera:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --resource-fixes &&
python3 -m unittest discover -s tests -p test_capture_gpu_resources.py -v
```

Expect nine tests with no skips. The builder applies four versioned patches
and keeps the capture layer/producer non-executable. Tests run an original fake
Vulkan backend: startup/resource rollback, memory limits and device/child
lifetimes, including imported-image ownership. They call no actual Vulkan
loader/driver and use no camera data. Neither layer installation nor service
restart is performed. Share the build report and final summary if you run it.

Passing these tests would extend synthetic evidence to ARM64. Actual GPU
startup/import/copy/teardown and restoration on the device remain separate
checks. The newer milestone below implements the watchdog/provider/recovery
prerequisites and can be tested in one batch.


## Test the watchdog and provider without capture

The earlier v2 milestone passed all 28 tests (10 watchdog, 11 provider and 7
recovery) on Frame ARM64; see
[E35](../status.md#e35-watchdog-provider-and-recovery-tests-on-frame). The current
full regression includes v3 metadata-only changes. It uses generated files,
private local sockets and fake Vulkan dispatch, without opening a camera or
loading the layer. The actual capture artifacts remain non-executable:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
bash scripts/build_capture_supervisor.sh &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_supervisor.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_config.py -v
```

Expect **35, 14, 12 and 8 tests**, respectively, with no skips. Share the provider
build report and test summaries when convenient. Existing ARM64 v2 success does not
establish ARM64 execution or live capture. This check changes no camera-source
setting, layer installation or compositor service.

To prepare an exact configuration backup and inactive bundle using this Frame's
home paths, choose a new artifact directory:

```bash
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-new
```

Expect `prepared_inactive_bundle`, with activation/restart both false. Existing
bundles are preserved; use another new directory if that name exists. The plan
backs up exact prior bytes/modes or absence for the proposed SightCircuit manifest
and library. It creates no active manifest/library and does not set the required
enable environment variable. No install command is provided yet. Keep the bundle
local; do not commit its backups. Inspection library bytes in the bundle still
do not constitute a tested layer installation.

The recovery commands below are only for known future trials, not routine
setup. A stale publication run has its exact session directory in its supervisor
report; `--recover-session PATH` verifies its recorded process/file identities.
`capture_layer_config.py --recover-provider-runtime` verifies a dead provider's
recorded socket identity in the current `$XDG_RUNTIME_DIR`. Both refuse living
owners or foreign replacements. To restore a staged configuration that still
matches the bundle:

```bash
python3 scripts/capture_layer_config.py --restore artifacts/capture-layer-plan-new
```

Restoration preserves later edits and does not restart SteamVR or unload a
library already loaded by the compositor. A controlled restart is required if
it was loaded. There has been no SightCircuit layer activation to restore yet.

The synchronization review found no coordinated producer handoff. The latest
layer therefore retains/exports no image FDs and the capture CLI is blocked.
Next validate metadata-only changes and prepare the controlled layer-load/
restoration procedure. UUID and
layout checks do not identify Arcturus versus built-in cameras. During a future
capture-only trial, verify the selected physical source, orientation, scene
motion, shutdown and normal passthrough before connecting inference or a VR
image panel. See the [active plan](../live-capture.md#next-engineering-step-capture-worker-supervision).


## Test metadata-only inspection without capture

This check passed on Frame ARM64: all 24 tests, with no skips; see
[E37](../status.md#e37-metadata-only-provider-and-diagnostic-on-frame). No unchanged
rerun is needed. The command is retained for regression testing. It builds
the metadata-only v3 layer and diagnostic, then runs generated peers and fake
driver calls. It does not load the layer, contact a real camera provider or
restart SteamVR:

```bash
cd "$HOME/sightcircuit" &&
git pull --ff-only &&
git rev-parse HEAD &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v
```

Expect **14 provider/layer tests and 12 metadata diagnostic tests** (E37 ran
12 and 12), with no skips. The build report should say `provider_protocol_version: 3`,
`image_fd_export_enabled: false`, `layer_retains_image_fds: false` and
`deployment_ready: false`. Share the revision, build report and test summaries.
These checks do not establish actual driver execution or complete camera frames.


## Check existing luma shared memory

This is a separate diagnostic for an **already running** compatible provider.
SightCircuit has not installed or started that provider. With no flags the
command only checks file metadata; it does not read a header, map pixels or
start capture:

```bash
python3 scripts/shm_capture_check.py
```

`source.present: false` is expected when no provider publishes
`/dev/shm/mrhand-pt`. A present file alone does not prove live camera input.
Do not install a layer merely to make this check report a file.

Build the reader on Frame using its existing Bash and C++17 compiler:

```bash
bash scripts/build_shm_reader.sh
```

The build downloads nothing, installs no packages and starts no producer. It
creates native binaries in ignored `artifacts/shm-reader/`; do not copy the
cloud's x86_64 binaries onto ARM64 Frame. To exercise generated images first,
use the [synthetic procedure](shared-memory.md#isolated-luma-reader-and-synthetic-producer).
That checks the reader on the device without touching a camera or compositor.

Only when a compatible provider is already running, explicitly read one slot:

```bash
python3 scripts/shm_capture_check.py --read --camera 0 --seconds 10
```

The source must be an ordinary file, not a symlink, device or FIFO. It is opened
read-only. The reader validates the pinned native layout, exact file length,
dimensions, slot count and per-slot sequence before and after copying. Copies
are capped at 10 per second and 16 MiB per slot, with at most two slots. It
rejects incomplete/changing copies, old or invalid observation timestamps,
observable counter resets and changes to the file identity, size or header.
Slot numbers do not establish left/right or physical camera identity.

`native` reports accepted frames, observed rate, dimensions, gray8 format,
sequence/frame counters, stale/busy/rejected copies and cleanup. The default
maximum observation age is 1,000 ms; `--max-age-ms` changes this diagnostic
threshold. `--frames N` permits an earlier exit after N accepted publications.
These are provider observation times on `CLOCK_MONOTONIC`, not exposures.
Writer ordering and original camera-frame integrity remain explicitly
unverified even when a copy succeeds. See [status](../status.md#e9-shared-memory-reader-cloud-checks).

No images are saved by default. To request a grayscale still, choose a new PNG
path in an existing directory:

```bash
mkdir -p artifacts/shm-check
python3 scripts/shm_capture_check.py --read --camera 0 --seconds 10 \
  --preview artifacts/shm-check/new-preview.png
```

The input is unmapped and closed before preview output is written. The still
fits within 640 × 640 and is published only after successful reader shutdown;
existing destinations are preserved. Inspect it after the command ends.

The supervisor allows the requested duration plus three seconds, then up to
two seconds of terminate/kill cleanup. Ctrl+C or SIGTERM stops and reaps the
reader. A concurrent truncation can fault a native mapping; the supervisor
reports that failure and never publishes a preview. Forced shutdown does not
claim normal source cleanup. Exit codes are `0` for metadata inspection or
successful capture, `1` for unavailable/stale/malformed input or another error,
`2` for invalid arguments, and `130`/`143` for cancellation. Retain the JSON
instead of restarting services or retrying a disruptive camera path.


## Advanced probe, only for a deliberate future retest

Building the probe does not initialize OpenVR:

```bash
bash scripts/build_camera_probe.sh
artifacts/camera-probe/sightcircuit-camera-probe --help
```

The build uses Git, Bash and a C++17 compiler. It needs no `uv`, Python packages,
OpenGL or Vulkan development packages. It fetches the pinned Valve OpenVR SDK
sources into `.cache/openvr` and compiles the portable loader for the current
machine. The binary goes in `artifacts/camera-probe/`. Both paths are ignored by
Git. A modified or different cached SDK causes an error rather than a reset.

Running the binary without arguments emits a `not_run` JSON report and exits
with code `3`, before any OpenVR calls. A deliberate runtime test requires the
explicit `--allow-passthrough-interruption` flag. This flag acknowledges the
observed disruption; it does not make the capture route work.

When explicitly enabled, the probe uses the public `IVRTrackedCamera_006`
interface in a background OpenVR application. It queries presence, count/layout,
frame dimensions and calibration statuses, then requests a shared camera stream.
It checks header-only retrieval for all three frame types and retries failed
size queries after acquisition.
Where dimensions are valid, it attempts an actual RGBA frame copy. A successful
handle alone cannot pass the check.

The retrieval loop has a shared three-second polling budget and a 64 MiB buffer
limit; synchronous runtime calls can add time. It reports metadata and named
errors as JSON, saves no images, and attempts to release the stream before
shutting down OpenVR. It does not deliberately change SteamVR settings, open physical video
nodes, install compositor layers, or select an RGB/monochrome source, but its
runtime calls have interrupted passthrough on the user's device. A received
frame would not by itself identify the Arcturus RGB source.

| Exit code | Meaning |
| --- | --- |
| `0` | Explicit stream test copied a frame with valid RGBA metadata and stream release succeeded |
| `1` | Runtime initialization, interface access, cleanup or another probe error; inspect the JSON |
| `2` | Runtime/interface access succeeded, but no valid frame was received |
| `3` | Probe was not run because the interruption flag was absent; no OpenVR calls were made |
| `64` | Invalid command-line arguments; no OpenVR calls were made |

`--help` prints usage and exits `0` without accessing OpenVR.

An unavailable runtime registry or `NoServerForBackgroundApp` is a runtime/session
problem, not proof of unsupported camera hardware. Keep the existing home/XDG
and OpenVR runtime configuration; the loader discovers SteamVR through its
registered paths. Background initialization does not launch SteamVR.

For a deliberate future retest after a material change, this command saves a
report and preserves the probe's exit status. It can black out passthrough; do
not use it to repeat the already failed test on the unchanged runtime:

```bash
(
  set -e
  bash scripts/build_camera_probe.sh
  sightcircuit_probe_status=0
  artifacts/camera-probe/sightcircuit-camera-probe --allow-passthrough-interruption \
    > artifacts/camera-probe/report.json || sightcircuit_probe_status=$?
  python3 -m json.tool artifacts/camera-probe/report.json || cat artifacts/camera-probe/report.json
  printf 'Probe exit code: %s\n' "$sightcircuit_probe_status"
  exit "$sightcircuit_probe_status"
)
```

Keep any future report with the tested runtime version. No public live-capture
SDK was found in the sources reviewed. Seek a supported Arcturus or Valve
live-capture contract first. The [capture plan](../live-capture.md) also defines
an isolated evaluation of the experimental community Vulkan path, which has
not been deployed by this project. Neither an active service nor listed sensor
nodes replace actual frame retrieval.

Do not copy the cloud machine's x86_64 `.venv` to Frame. The existing dependency
pins and smoke setup were validated on Linux x86_64, not the headset; verify
native interpreter, wheel and library availability before installing them there.
The device inventory requires no such installation. Leave compositor-layer
deployment and camera-source changes out of this initial check.

Use the [overlay guide](../vr-overlay.md) to resume recorded playback and the
[status record](../status.md) to retain new measurements. For development tasks,
follow the [active capture work order](../live-capture.md#current-work-order).
The [architecture milestones](../architecture.md#milestones-and-acceptance-checks)
describe later acceptance criteria, including spatial labels and offline splats.


[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
