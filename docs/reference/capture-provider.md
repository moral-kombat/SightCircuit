# Paused capture-provider engineering reference

**Paused track.** These detailed implementation notes cover the dedicated
capture provider, synthetic fixtures, and recovery mechanisms. They are
retained for maintainers; they are not part of contributor setup or the active
HUD work. See the [current plan](../live-capture.md) for present priorities.

Milestone-specific “next” and “pending” statements below describe the stage
being documented, not the current work order. Historical device outcomes are
recorded in [status](../status.md). No layer activation or SteamVR restart is
authorized by this reference. [Device build checks](capture-device-checks.md)
and [historical trials](../history/capture-trials.md) are separate.

## Paused capture-provider track

The dedicated-capture provider track is paused and frozen (see the
[current work order](../live-capture.md#current-work-order)); its procedures below and in the
[retained device checks](capture-device-checks.md) are kept for when it resumes. On 2026-11-04 the
owner approved one small PR fixing two cancellation races in
`capture_supervisor.py` ([decision](../poc-scope.md#capture-supervisor-fix-2026-11-04),
[details](#worker-containment-and-publication-recovery)); the track otherwise
stays frozen. Do not activate layers or restart SteamVR from a cloud session.
Its Python commands stay in `scripts/` under their documented paths and are
quarantined in place:

| Script | Role |
| --- | --- |
| `capture_layer_config.py` | Prepare an inactive layer bundle and restore exact files; never restart VR |
| `capture_layer_trial.py` | Bounded metadata-only SteamVR trial, with systemd-owned post-exit recovery |
| `capture_provider_check.py` | Supervise metadata inspection of an already running provider |
| `capture_service_lifecycle.py` | Read-only cgroup observations and bounded service transitions; no VR APIs |
| `capture_session.py` | Own a private capture run and recover only its recorded publication inode |
| `capture_supervisor.py` | Bound an explicitly prepared native capture worker |
| `metadata_session_trial.py` | Prepare an opt-in metadata layer trial across two platform session cycles |
| `service_rehearsal.py` | Rehearse SteamVR stop/start over SSH without installing a capture layer |
| `session_rehearsal.py` | Prepare and rehearse a SteamOS-managed graphical-session logout |

Their tests are `tests/test_capture_layer_config.py`, `test_capture_layer_trial.py`,
`test_capture_provider_check.py`, `test_capture_service_lifecycle.py`,
`test_capture_supervisor.py`, `test_metadata_session_trial.py`,
`test_service_rehearsal.py` and `test_session_rehearsal.py`. Native code lives in
`tools/capture_*` and `patches/mrhand/`, built by `scripts/build_capture_*.sh`.
The read-only diagnostics `shm_capture_check.py` and `capture_gpu_check.py` are
not part of this cluster.

`tests/test_scripts_boundary.py` enforces the quarantine: these modules import
only the standard library and each other; nothing else imports them except
their own tests; the rehearsal and trial source snapshots (`SOURCES`) name only
cluster files; and this section lists every cluster module.

A physical move is deferred until the track resumes, because it cannot be done
without touching frozen material. A top-level directory at the same depth keeps
the code byte-identical (`ROOT` is `parents[1]` and snapshots name sibling
files), but `patches/mrhand/0006-block-unleased-camera-reads.patch` prints the
`scripts/capture_provider_check.py` path, the on-device procedures and
`.github/instructions` globs name these paths, and any bundle already prepared
on Frame records them and would need regenerating.


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
build is invoked. See [the operating procedure](capture-device-checks.md#check-native-capture-prerequisites)
and [the capture plan](../live-capture.md#current-work-order) for the next device step.

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

The native CI job builds these inspection artifacts and checks their hashes,
ELF headers and permissions without executing them. For Frame, follow
[the combined build and reader-test procedure](capture-device-checks.md#build-the-capture-reference-without-activation).
That procedure now passed on Frame ARM64; see [E23](../status.md#e23-reference-build-and-controlled-reader-on-frame).

### Build and test descriptor transport changes

```bash
bash scripts/build_capture_reference.sh --transport-fixes
artifacts/capture-transport-reference/sightcircuit-transport-fixture --self-test
```

The optional adaptation applies the [versioned patch](../../patches/mrhand/README.md)
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
remaining provider work and the [device guide](capture-device-checks.md#test-the-transport-adaptation-without-capture)
owns ARM64 execution.
The user reports the adapted build and seven fixture groups passed on Frame;
see [E25](../status.md#e25-descriptor-transport-on-frame).

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
`$XDG_RUNTIME_DIR/sightcircuit-capture/frames.shm`. The adaptation drops image-dump
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
retain the [on-device procedure](capture-device-checks.md#test-owned-publication-without-capture)
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
unbuilt; the native CI job builds it before running them. Hosted CI results
are separate from local results. For the real Frame query, use
[the supervised device command](capture-device-checks.md#query-vulkan-capture-capabilities).
The first Frame query now passes on Turnip Adreno 750 with clean teardown and
normal passthrough; see [E30](../status.md#e30-vulkan-capabilities-on-frame). Its
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
see [E30](../status.md#e30-vulkan-capabilities-on-frame) and
[E31](../status.md#e31-import-build-and-synthetic-tests-on-frame).
Compile/link and synthetic tests do not validate a real import. The earlier
import-only mode still leaves device and other GPU resources unmanaged; the
resource adaptation below addresses those lifetimes. Capture supervision,
provider metadata/registry and layer lifetime/restoration remain pending. See the
[offline work order](../live-capture.md#offline-provider-work-while-the-frame-is-unavailable)
and [optional native fixture procedure](capture-device-checks.md#test-import-ownership-without-capture).

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
[native fixture procedure](capture-device-checks.md#test-gpu-resource-ownership-without-capture)
can be batched with a later milestone. Follow the
[next work order](../live-capture.md#next-engineering-step-capture-worker-supervision)
before deployment.


## Capture watchdog, provider lifecycle and restoration preparation

The sixth inspection mode now applies patches 0001–0006 and builds the v3
metadata-only provider, diagnostic and actual-layer-hook fixtures. The previous
v2 milestone passed 10 watchdog, 11 provider and 7 recovery tests on Frame ARM64;
see [E35](../status.md#e35-watchdog-provider-and-recovery-tests-on-frame). For a full
development regression, build the native exec boundary and run:

```bash
bash scripts/build_capture_supervisor.sh &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_supervisor.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_config.py -v &&
python3 -m unittest discover -s tests -p test_capture_service_lifecycle.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_trial.py -v
```

The current cloud suites contain **35 watchdog, 14 provider/layer, 12 metadata
diagnostic, 8 configuration/recovery, 10 service-observation and 48 trial tests**. These use generated files,
local sockets and fake driver dispatch. Neither the actual layer nor the
camera producer is loaded/run. The real binaries remain mode 0644 in
`artifacts/capture-provider-reference/`; the report records helper, patch and
artifact hashes and `deployment_ready: false`. All earlier build modes remain
available. The earlier v3 ARM64 build and both 12-test suites passed on Frame;
see [E37](../status.md#e37-metadata-only-provider-and-diagnostic-on-frame). The metadata inspector is
a separate executable, linked without Vulkan; it is not the camera producer.
The trial runner was later attempted on Frame and failed during service control.
Its revised lifecycle controller remains cloud-tested; the read-only cgroup/
compositor observation now passes on Frame. Actual service transitions remain
unvalidated; see [E46](../status.md#e46-read-only-runtime-baseline-on-frame).

### Worker containment and publication recovery

[`capture_supervisor.py`](../../scripts/capture_supervisor.py) defaults to `not_run`
(exit 3). Its explicit `--capture` branch now returns `blocked` (exit 3), with
`producer_access_contract_unavailable`, before starting a process. Patch 0006
also replaces the latest producer with an access-gate stub. There is no
installer or executable-enabling procedure. The internal supervision helper
below remains tested against generated workers for a future synchronized copy.

[`capture_worker_exec.cpp`](../../tools/capture_worker_exec.cpp) establishes
`PR_SET_NO_NEW_PRIVS`, SIGKILL parent-death handling, a parent-PID race check and
a zero core-dump limit before loading the worker's libraries. It executes an
already opened, owned, non-writable-by-others ELF descriptor, avoiding a
pathname substitution between the supervisor's check and exec. Only the required
FD is inherited. The stream retains its own signal/deadline checks, but an
external deadline covers startup, imports, copying and destruction too.

The outer budget is the requested 1–10 seconds plus three seconds. On timeout
or cancellation it requests SIGTERM, allows 0.3 seconds, then uses SIGKILL if
needed, with a bounded one-second reap attempt. A kernel task stuck in
uninterruptible I/O may remain unreaped; the report must say so, and its files
are preserved. Output has a 256 KiB total cap, an 8 KiB event-line cap and a
256-event cap. Partial/malformed/inconsistent completion reports are failures.
Clean cancellation is distinguished from forced termination. A killed process
does not establish graceful Vulkan API cleanup.

Each run uses an exclusive mode-0700
`$XDG_RUNTIME_DIR/sightcircuit-capture-*/` directory and a locked, mode-0600 lease.
The worker publishes to its assigned `frames.shm` and announces device/inode/size.
The supervisor records those identities before recovery and removes a remaining
publication only after reaping that worker and matching the private file. It
preserves replaced, unannounced or unknown files. A very short worker may remove
its publication before the event is consumed; an already absent file needs no
unlink. The reader can later use the report's exact session path with `--source`;
the existing reader default remains the upstream compatibility path.

For a known dead session, `--recover-session PATH` checks the locked lease and
recorded `/proc` process start times before removing only its recorded file.
It refuses a living run or unconfirmed identity. No automatic directory sweep
or unconditional stale-file deletion occurs. The tests cover hangs at each
GPU phase, crashes/output limits, signals, parent death, graceful deadline
retirement, exact recovery and preservation of foreign files.

**Cancellation while a publication is recorded (2026-11-04).** A SIGINT or
SIGTERM that arrived after the supervisor had taken the worker's
`publication_ready` line from the pipe, but before `Session.publication` stored
its identity, left the identity unrecorded. Cleanup then refused the owned
`frames.shm` with `publication_recovery_unconfirmed`: fail-closed, but wrong,
because the identity had been announced. The supervisor now holds SIGINT and
SIGTERM (`pthread_sigmask`) while it reads and records each chunk of worker
output. A single held signal, or a repeat of the same one, is delivered as soon
as the chunk is recorded, so the run still ends `cancelled` with exit 128 +
signal; bytes not yet read stay in the pipe for the post-cancellation drain. The
signals are held inside the deferral's `try`, so a cancellation raised by the
holding call itself still restores the caller's mask. The mask covers only the
calling thread, so it holds only while no other thread in the process leaves
SIGINT or SIGTERM unblocked: the kernel would hand a signal to that thread, and
Python would then run the handler on the main thread inside the deferral. The
supervisor starts no thread; a caller process with other threads, such as a
test process with numpy's BLAS threads, must block both signals in them. If a
recording error is being raised when a held cancellation is delivered, the
cancellation replaces it: the run still ends `cancelled`, the report keeps the
error as `superseded_error`, and the rest of the output is not read or
validated. The supervisor names that error where it is raised rather than take
it from the cancellation's `__context__`, which would also pick up an exception
that the caller of `supervise()` is handling. The recovery rules are
unchanged. A SIGINT and a SIGTERM pending together are not covered, before or
after this change: the second handler
raises inside `cleanup_signals()` before the signals are ignored, so
`supervise()` raises `StopRequested` without reaping the worker or removing the
session. Holding the signals can make the two arrive together, since both are
delivered as the mask is lifted, so this change makes that pre-existing gap
somewhat more likely; it is left for a separate follow-up. This evidence is
cloud and CI-runner only, not Frame: the existing
`test_sigint_and_sigterm_reap_the_worker` failed on PR #21's ARM64 runner
(`arm64 / capture-supervisor`, run 41759960012 attempt 1, job 126345052861; the
re-run in attempt 2 passed) and in about 1 of 40 local runs. The new
deterministic test fails on every run of the previous supervisor and passes with
the change. The owner approved this one change to the paused track
([decision](../poc-scope.md#capture-supervisor-fix-2026-11-04)).

**Cancellation while the worker starts (2026-11-04).** A SIGINT or SIGTERM whose
handler raised inside `subprocess.Popen`, after the worker had been created but
before `Popen` returned, left `supervise()` without a handle on it: the report
said `worker_started` and `worker_reaped` false, and cleanup could neither stop
nor reap the worker, which kept running until the supervisor process exited.
This was reproduced on the previous supervisor under CPU load, once in 36 runs.
The supervisor now records SIGINT and SIGTERM with temporary Python handlers
while the worker starts and its pid is saved (`recorded_signals()`). Once the
pid is in the report and lease it delivers each distinct recorded signal once,
in arrival order, to the caller's handler, until a handler raises or `SIG_DFL`
ends the process, so the run ends `cancelled` and cleanup can stop and reap the
worker. A recorded signal is delivered also when the spawn or the pid save
fails; the run then still ends `cancelled` and keeps that error as
`superseded_error`. A signal the caller set to `SIG_IGN` is not swapped, so it
stays ignored and the worker still inherits `SIG_IGN`, as before. The signal
mask is not held while the worker starts, because the worker would inherit it;
it holds both signals only while the two handlers are put back, so, under the
same thread condition as the deferral above, a signal landing between the two
restores cannot leave the other one recording. The deterministic test
`test_cancellation_while_starting_the_worker_still_reaps_it`
holds the spawn window open until the signal is sent; it fails on every run of
the previous supervisor, and it reads the lease as the cancellation is
delivered, so it also checks that the lease names the worker by then: a caller
whose `SIG_DFL` ends the process still leaves the pid for `--recover-session`.
Other tests deliver a cancellation recorded while a failing spawn or pid save
raises (a failed pid save must still leave the worker stopped and reaped), and
send a signal right after the first of the two handler restores, whichever
signal that is. Counterparts without a cancellation check that a spawn or
recording error then fails the run and is named as its `reason`, and the
malformed and flooding workers must fail with their JSON error rather than reach
the deadline. Another cancels a worker that retires gracefully, also while the
caller handles an error, and checks that its final event is still drained and
validated and that no error is reported as superseded. In-process tests send
signals with `signal.raise_signal`, which targets the calling thread, and run
beside a thread that leaves both signals unblocked, as numpy's BLAS threads do
in the full suite. With `os.kill` such a thread could take a signal the main
thread held, so the restore test failed now and then in the full suite, or
aborted the whole unittest run. The harness of the existing
`test_sigint_and_sigterm_reap_the_worker` (its assertions are unchanged, apart
from a new check that no error is reported as superseded) now also holds both
signals once `supervise()` has returned, because its repeated signal could
otherwise kill the harness process during interpreter shutdown
(exit -2 or -15) after the supervisor had finished. Same approval as above. The
start-up race was reproduced in local cloud runs only, never on a CI runner or
Frame; the PR #21 ARM64 failure cited above was the publication race (its report
had `worker_reaped` true). Neither fix has a Frame result.

### Provider protocol and layer ownership

[`capture_provider.hpp`](../../tools/capture_provider.hpp) defines a separate v3,
328-byte native little-endian/64-bit packet ABI, not an extension sent to old
MRHAND clients. The endpoint is
`$XDG_RUNTIME_DIR/sightcircuit-camera-provider/camera.sock`; there is no `/tmp`
fallback. An explicit first request chooses metadata or image buffers; the
server rejects buffer access because no producer handoff is available. Old
protocol clients/providers fail closed. Snapshot records retain
the original 144-byte buffer description inside the new packet and add an
epoch, generation, logical-device token, physical-device/driver UUIDs,
image flags/type/sharing/depth/mips/samples, external handle types, memory flags
and per-plane array/depth pitches. Unknown creation/allocation chains are marked
incomplete and rejected for capture. Current policy accepts only the existing
single-layer profile with exclusive sharing and zero unsupported flags/pitches.

The registry scopes image/memory handles by logical device, bounds counts and
assigns nonreused buffer IDs. The production layer records allocation metadata
without retaining the imported image FD; Vulkan retains its normal ownership
rules. Metadata snapshots duplicate no image descriptors. Descriptor-owning
registry operations remain only for generated ownership fixtures; the server
never grants descriptor access.
Image destruction, binding, memory changes and device teardown advance the
generation. The original layer implementation uses shared callback state and
exact device-handle lookup; two devices may share a dispatch-table pointer.
Device destruction removes only its registry entries. The last device stops
and joins the owned server thread before releasing its callback state. Registry
errors invalidate capture without changing successful downstream Vulkan results.

The metadata consumer holds a bounded socket lease and checks epoch/generation.
Provider loss, timeout, invalidation or changed registry ends the attempt; it
never reconnects to a different provider. Registry stability describes resource
bindings, not XRService frame completion or reuse. UUID metadata identifies a
GPU/driver, not a physical camera or stereo role. Future pixel access must
establish the [producer ownership and synchronization contract](../live-capture.md#producer-synchronization-review-and-next-checkpoint)
before reusing the tested import helpers.

The server uses a private directory, same-user peers, an exclusive file lock,
bounded I/O and identity-checked endpoint removal. A crash leaves a recorded
socket identity and owner PID/start time. Explicit provider-runtime recovery
locks that lease and removes the socket only after the recorded process is gone
and the endpoint still matches. Competing/live providers and foreign replacements
are preserved. Server shutdown interrupts an idle client; there is no detached
thread. The opt-in gate permits only `vrcompositor` with
`SIGHTCIRCUIT_CAPTURE_LAYER=1`, and allows at most two accepted starts in 120
seconds using locked private runtime state. Other processes/default invocation
cannot start a provider; the setting must not be enabled globally. The gate is
enforced at device creation as well as negotiation, because the loader can
fall back to legacy entry points after a negotiation failure. Denied processes
forward calls without observing resources or creating a runtime endpoint.

### Inactive configuration bundle

[`capture_layer_config.py`](../../scripts/capture_layer_config.py) defaults to no
action. `--prepare NEW_DIRECTORY` creates a private local bundle containing a
disabled-by-default manifest, the inspection library and a checksummed plan.
It snapshots exact existing bytes/modes or absence at the two proposed
SightCircuit manifest/library paths. It does not write those active paths,
install anything, set environment variables or restart a runtime. Backups may
contain local configuration and remain in ignored artifacts.

The implemented restoration path preflights both targets and changes only a
file matching either its recorded prior state or staged state. Later edits,
symlinks, hardlinks, FIFOs, parent symlinks, changed hashes and a different home
are refused. Original bytes/modes are restored; originally absent files are
removed. Partial restoration is repeatable. No unrelated MRHAND, Vulkan or
SteamVR configuration is changed. Restoring files cannot unload a library from
a running compositor; a controlled restart remains part of a future approved
device procedure. Tests emulate staging only inside generated temporary homes.

See the [device preparation procedure](capture-device-checks.md#test-the-watchdog-and-provider-without-capture)
and [remaining live gates](../live-capture.md#next-engineering-step-capture-worker-supervision).

### Metadata-only provider inspection

[`capture_provider_inspect.cpp`](../../tools/capture_provider_inspect.cpp) uses the
v3 metadata request and shared profile validators, without linking/loading
Vulkan, requesting image FDs or reading pixels. It reports metadata for at most
64 records: dimensions, format, allocation/binding, modifier/plane layout,
exporter flags, UUIDs, epoch/generation and peer credentials. Profile rejection
is recorded per entry; an empty or unsupported registry can still be inspected
successfully. Compatibility is only a metadata predicate, not successful import,
camera identification or synchronization proof.

The standalone executable defaults to `not_run` (exit 3). Explicit `--inspect`
checks an existing provider for 1–5 seconds (default 1), using 500 ms exchanges.
[`capture_provider_check.py`](../../scripts/capture_provider_check.py) supervises it
with a duration-plus-two-second deadline, bounded output, signal/parent-death
handling and a validated report. It installs/starts no provider and writes no
files. `inspected` exits 0; missing/changed/old providers, malformed reports,
unexpected descriptors and timeouts fail with exit 1. Cancellation exits
`128 + signal`; forced termination does not claim graceful cleanup.

The 12 generated-data diagnostic tests cover matching/empty/unsupported metadata,
generation changes, provider loss, endpoint/protocol/descriptor rejection,
invalid/default arguments, corrupt/oversized output, hangs, signals, parent
death and the blocked capture CLI. Build with `--provider-fixes`, then run
`test_capture_provider.py` and `test_capture_provider_check.py`. The fixtures
generate their own peers in private temporary directories; no real provider
or layer is needed. Native v3 execution passed on Frame; controlled layer
activation and restoration remain separate checkpoints.

### Controlled metadata-layer trial and recovery

**Device activation is paused** following service-control failures on Frame;
see [E41](../status.md#e41-frame-trial-report-and-service-control-failures). The CLI rejects
`--start-metadata-test` with `blocked`/exit 3 before runtime/file calls. It has no
CLI bypass. Reporting, known-run recovery and read-only preflight remain usable.
The following describes the quarantined implementation, not a current launch
recommendation. The runner restores SteamVR but never relaunches a console app.

[`capture_layer_trial.py`](../../scripts/capture_layer_trial.py) defaults to
`not_run`/exit 3. `--preflight BUNDLE` checks a fresh metadata-only build,
matching bundle/checksums/home, absent prior SightCircuit targets, no existing
provider or enable flags, and the exact reported SteamVR service properties.
It also requires an idle service job and a live compositor in the original
unified cgroup, with a consistent invocation ID across baseline observations.
It changes nothing. Existing configurations or differing service properties
are refused rather than overwritten or guessed.

`--start-metadata-test BUNDLE` first runs a one-second dummy user unit, requiring
both its started marker and systemd's timeout/`ExecStopPost` completion marker.
The dummy never touches SteamVR. Only then does it launch an independent
transient trial service. The worker arms a private rollback plan before stopping
SteamVR, stages the metadata-only library/manifest plus a runtime service-scoped
enable drop-in, reloads the manager and starts SteamVR. It waits at most ten
seconds for the endpoint, performs a two-second metadata inspection, and checks
the peer's UID, executable and membership in the SteamVR service's cgroup.

Systemd runs a **separate** `ExecStopPost` command after success, failure or
termination. Recovery observes existing jobs before requesting any stop,
removes only the exact trial drop-in,
conditionally restores only recorded targets, recovers an owned stale provider
endpoint if necessary, reloads the manager and starts the normal service.
It preserves later edits, reports failures and refuses a living trial worker.
An unchanged healthy configuration needs no restart if the worker never reached
staging; restored files alone cannot prove that a loaded layer was unloaded.
Repeated recovery of an
already restored active service rechecks liveness without another stop/start.
The trial service
has `RuntimeMaxSec=60`, `TimeoutStopSec=60`, `Restart=no`; execution and recovery
can take about three minutes on a timeout path. A failed service stop, manager
failure or unkillable kernel task cannot be reported as successful restoration.

State, metadata and final reports live in ignored private
`artifacts/metadata-trials/trial-*/` directories. `--report-latest` reads them
without runtime calls. An ambiguous launch acknowledgment is `launch_unconfirmed`,
with the attempted run path retained; inspect/recover that run before retrying.
No image descriptors, pixels, inference or VR panel output are requested.

The 48 trial tests exercise real temporary configuration and generated platform
hook/job events. A separate nine-test suite checks generated cgroup/proc files
and virtual deadlines; eight configuration tests cover exact restoration and
edits during a runtime guard. All 65 targeted tests pass in the cloud. They do
not establish actual user-manager execution or vendor hook side effects; static
unit syntax was verified in the cloud, and the real recovery rehearsal is
mandatory on Frame before the trial. The [device procedure](../history/capture-trials.md#run-the-bounded-metadata-layer-trial)
contains opt-in, expected reports and manual recovery.

Worker reports now include the failure phase; private worker state checkpoints
the phase before each action. `ServiceCommandError` records operation and exit
code, with at most 4 KiB of stderr for exact service start/stop/daemon-reload
commands, including bounded timeout diagnostics. Rollback records each command's phase separately. Command stdout and
environment-query stderr are never attached; environment-query values are
excluded from diagnostic reports. These diagnostic changes do not remove the
activation pause or establish a fix for the device's service-control failure.

`--service-details` makes a single read-only `systemctl show` query for selected
status, dependency, stop-propagation and policy fields. Exec* properties are
reduced to executable paths. Each repeated property line contributes one
executable in order; duplicate paths are preserved. Only the leading record
path is read, so `path=` text inside argv is excluded. Unrecognized nonempty
hook records return `refused`/exit 1 with the property name, never their raw
content. Command arguments, environment values and unrequested fields are
not returned. Tests cover the shipped hook order, argument exclusion, format
refusal and the single read-only query without activation or file changes.
See [the platform source findings](../status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix)
and [required lifecycle coverage](../live-capture.md#steamvr-service-control-acceptance).

[`capture_service_lifecycle.py`](../../scripts/capture_service_lifecycle.py) queries
no VR API. It observes the original cgroup's `cgroup.events`, including descendant
population, and reads bounded cgroup process lists plus `/proc` executable,
owner, state, start-time and membership metadata. Missing hierarchy/permission
data is unknown, not an empty group. The implementation currently requires
readable unified cgroups. `systemctl show --all` retains an explicitly empty
`Job=` field, which is normalized to idle (`0`); an absent Job remains unknown.

A transition submits at most one service command, then observes its result
within 25 seconds, sharing a 55-second controller deadline. The command timeout
is at most 20 seconds, leaving observation time within the existing 60-second
systemd watchdogs. A successful stop requires no job, an inactive/failed unit,
zero MainPID and an empty original cgroup across two observations. Startup
requires active/running/success, an idle job, an invocation ID and a stable live
compositor. Pending jobs are observed before recovery issues another command.
Intent, phase, command outcome and changed observations are saved before the
next recovery decision.

The worker rechecks stop state before staging files; recovery rechecks it before
each restored file, followed by another file-content check to preserve edits
made during the observation. A competing controller can still act between
observations and writes; these checks do not supply atomic exclusion against
outside service controllers. Failed restoration never triggers an automatic
normal-service start. A failed command followed by an observed transition may
allow cleanup to proceed, but its error remains in the report and recovery
exits 1. Configuration restoration, a confirmed start command and
`normal_runtime_observed` are separate fields. If the unchanged service was
already running, `normal_service_already_running` is true instead of claiming
that recovery started it. Aggregate completion additionally requires runtime
observation evidence; an older `restored` label alone is insufficient.

`--runtime-status` performs only service/cgroup/proc reads and two liveness
observations, with a 12-second controller budget. It needs no build or inactive
bundle. `runtime_observed`/exit 0 establishes process liveness;
`runtime_unconfirmed` or `refused` exits 1. It returns
`rendering_readiness_verified: false` and requires a separate user passthrough
check. It does not restart SteamVR, initialize OpenVR/Vulkan, connect to the
provider or read images. The
[read-only Frame check](../status.md#e46-read-only-runtime-baseline-on-frame)
now passes on-device; passing it does not unblock activation or validate the
new controller's actual service transitions.


## Service-only recovery rehearsal

`scripts/service_rehearsal.py` reuses the service/cgroup observer and private-file
helpers without preparing or installing a capture layer. Its default does
nothing; `--preflight` reads service policy and verifies absent capture targets.
`--report RUN` and `--report-latest` read saved evidence without runtime calls.

The launcher requires independent inbound SSH and an inactive trial unit. It
checks systemd post-exit cleanup with a dummy timeout, snapshots its Python
helpers into a private run, persists recovery intent before stopping SteamVR,
and uses a separate transient unit with a 60-second worker limit and a
60-second stop phase. Recovery observes an already healthy service or starts
it only after confirmed shutdown; pending jobs and surviving compositor
processes prevent competing starts. Service errors remain failures even when
the runtime later recovers. No service configuration or layer files are written.

The first Frame run exposed the shipped Gamescope `PartOf=steamvr.service`
relationship and a canceled stop job. The current preflight refuses that
relationship before scheduling anything. Keep this guard while designing a
session-aware lifecycle; the earlier independent-service model does not fit
this runtime. [Device evidence](../status.md#e63-service-only-rehearsal-and-graphical-session-dependency)
separates the failed rehearsal from observed runtime recovery.

Run the focused Linux tests without contacting systemd or a camera:

```bash
python3 -m unittest tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

The rehearsal suite covers interrupted workers, command failures, pending jobs,
surviving processes, source snapshots, repeated recovery, later configuration
edits, SSH gating, session-policy refusal and persisted phase diagnostics. The
observer also handles `ESRCH` when a process exits while its metadata is read,
without treating that as proof the entire cgroup is empty. The configuration
suite's native provider fixture may skip if it has not been built.


## Platform-managed session rehearsal

[`session_rehearsal.py`](../../scripts/session_rehearsal.py) is a separate test of
SteamOS Manager's `switch-to-game-mode` operation. It preserves the bare-service
guard above and the metadata activation block. The vendor source at
[`v26.4.1`, commit `a0d399d`](https://gitlab.steamos.cloud/holo/steamos-manager/-/blob/a0d399defebd60db30ba38325d9cf47dbda49bee/steamos-manager/src/session.rs)
selects a temporary game-session login and stops `graphical-session.target`
with `JobMode::Fail`. SDDM starts the next session. This does not promise a
stable stopped interval for layer installation.

The new tool defaults to no action. `--preflight` reads only the platform and
runtime. `--prepare NEW_BUNDLE` saves an inactive, exclusive bundle of helper
sources, hashes, platform fingerprints and baseline metadata. `--check BUNDLE`
validates those helpers, the installed command/daemon/session files, package
version, active D-Bus method, default game mode, absent capture configuration
and stable baseline. It refuses changed platform files or foreign drop-ins.

Only `--start BUNDLE --allow-session-logout` may request a logout, and it
requires independent inbound SSH. It first verifies post-exit cleanup using a
dummy systemd timeout, then creates a private run with its own source snapshot.
Its transient unit is in `app.slice`, with no graphical-session dependencies,
a 120-second runtime limit and a 45-second stop phase. The worker persists
intent before issuing exactly one vendor request. Failures/timeouts are never
retried, since they may still have initiated logout. The optional `--wait`
waits up to 180 seconds for saved results; disconnecting does not cancel the
independent service.

Completion requires new invocation IDs for Gamescope, SteamVR and both session
targets; all must be healthy with no pending jobs, a live compositor and an
active SDDM. A stable identity must persist for two seconds. Unchanged or
partially restarted sessions cannot pass. An independent post-exit observer
waits at most 30 seconds for normal runtime. It never issues another logout or
direct start/stop. If the platform does not recover, it saves a failure for
inspection instead of competing with platform startup. Human confirmation of
UI, passthrough and tracking remains separate from process-liveness evidence.

```bash
python3 -m unittest tests.test_session_rehearsal tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

The 21 new generated-data tests cover full/partial/no-op transitions, identity
churn, observation deadlines, late results, request failures with later recovery,
worker interruption, source/platform drift, conflicting units, capture-file
preservation, session-independent launch properties and duplicate-request
prevention. The [device checkpoint](../status.md#e64-platform-session-test-completed-and-headset-confirmed)
now has a successful real session request, healthy independent recovery and
the user's normal-headset confirmation. Metadata activation/restoration
remains separate work.


## Metadata trial across platform sessions

[`metadata_session_trial.py`](../../scripts/metadata_session_trial.py) prepares a
separate metadata-only activation/restoration trial using the validated vendor
session request. The old bare-service runner remains blocked. Preparation
requires a completed session-proof run on the matching platform and a verified
v3 metadata bundle; it freezes Python helpers, the native inspector, library,
manifest template and platform fingerprint in an exclusive private directory.

The ownership strategy does not require holding SteamVR stopped:

1. Each run gets a new library path. Its bytes are never replaced or removed,
   including during failed recovery. The normal fixed library path stays absent.
2. The implicit manifest points to that library and retains the existing opt-in
   environment condition. The [Khronos loader contract](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderLayerInterface.md#layer-manifest-file-format)
   gates implicit loading on the application environment. Current compositor
   capture flags must be absent before preparation/activation.
3. An exact runtime drop-in sets only `SIGHTCIRCUIT_CAPTURE_LAYER=1` for future
   `steamvr.service` starts. A manager reload does not modify the current
   process's environment. The worker records cleanup intent before exposing
   configuration, then requests one platform session cycle.
4. After a stable new session, the inspector waits at most eight seconds for
   provider startup, then inspects metadata for two seconds. Peer PID/start
   identity, service cgroup, library device/inode mappings, socket ownership
   and unchanged session generation must match. Zero candidate records is
   a valid metadata result, not evidence of a camera source.
5. Independent `ExecStopPost` cleanup removes only the exact trial drop-in and
   manifest, reloads the manager and requests the normal session once. Foreign
   edits are preserved. Existing startup jobs must settle before that request;
   ambiguous requests are never retried.
6. Restoration requires healthy new runtime, absent capture flags and library
   mappings in its compositors, retirement of the identified provider, and
   absence of the provider endpoint. A stale endpoint is removed only after
   matching its recorded lease/PID/start/device/inode. Unknown endpoints are
   preserved. Library/source artifacts remain for diagnosis.

The transient service runs in `app.slice`, independently of the graphical
session and initiating SSH terminal. The worker has a 150-second runtime limit;
the stop phase has a 180-second timeout. `--wait` waits at most six minutes for
saved results. Camera bytes, image FDs, detector and VR output are not part of
this trial. The native v3 provider still rejects buffer requests.

The new 35-test suite uses temporary files and generated session/provider
events. It covers two cycles, interrupted/partial setup, delayed or missing
providers, command ambiguity, later edits, source drift, peer ownership,
mapping identity, stalled sessions, cleanup failures and duplicate requests.
After the first real trial returned `provider_registry_changed`, the v2
orchestrator adds bounded settling: at most four clean metadata inspections in
12 seconds, with one-, two- and three-second gaps after invalidations, subject
to the shared deadline. Each native attempt still requests a full two-second
unchanged registry. Transport/protocol failures, unclean exits, session/socket/
peer changes, changed epochs and regressing generations stop immediately.
Every raw attempt and a settling summary are retained; later success does not
hide earlier invalidations. A failed final snapshot is never used as stable.
The original native inspector and provider protocol are unchanged.

```bash
python3 -m unittest tests.test_metadata_session_trial tests.test_session_rehearsal tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

Both operator runs loaded the provider and restored normal startup. V2 still
invalidated every registry inspection, but the eight candidate metadata records
match across all three saved snapshots. Its normal headset recovery is confirmed.
See [E65](../status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed).
Do not repeat v2 unchanged; investigate scoped lifetime/version tracking offline.
