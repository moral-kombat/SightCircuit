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
| 1b | Bounded recognition from mirror frames | Latest Frame POC execution and inspected preview localization confirmed | 100 reads, 51 processed at 4.993 fps, 40.065 ms mean inference and clean shutdown; user confirms matching labels/boxes and normal passthrough/tracking during/after. Broader accuracy and continuous output remain separate; [E48](status.md#e48-live-mirror-recognition-poc-and-matching-preview) |
| 1c | Check capture of our own display | Confirmed: the VR panel appears in captured output | Current composited mirror does not exclude our output; no continuous live-image panel from this source; [E18](status.md#e18-vr-panel-included-in-mirror-input) |
| 1d | Detector quality and class coverage | Needs evaluation | Include the rug false positive; select supported target classes and evaluate correct labels, boxes and misses before claiming useful accuracy |
| 2 | Isolated shared-memory reader and controlled producer | Controlled x86_64 and Frame ARM64 checks passed; real provider unverified | All 14 native/supervisor tests pass on Frame; upstream writer ordering and original GPU completion remain unresolved; [E23](status.md#e23-reference-build-and-controlled-reader-on-frame) |
| 3 | Dedicated camera provider | Initial tests, GPU query and 28 watchdog/provider/recovery tests pass on Frame; metadata-only adaptation prepared; not deployed | Producer handoff is absent in reviewed source; live reads blocked; controlled metadata-layer load/restoration and synchronized copy remain |
| 3a | Validated image import and ownership | ARM64 build and ten synthetic tests passed; two-plane coverage added in cloud | Actual camera DMA-BUF import remains untested; [E31](status.md#e31-import-build-and-synthetic-tests-on-frame), [E32](status.md#e32-two-plane-import-fixture-cloud-checks) |
| 3b | Device and remaining GPU resource ownership | Inspection build and nine synthetic cloud tests pass | Native ARM64 and actual driver execution pending; no activation; [E33](status.md#e33-gpu-resource-ownership-cloud-checks) |
| 3c | Capture watchdog, provider metadata/lifetime and configuration recovery | Earlier v2 milestone passed all 28 tests on Frame ARM64 | Generated workers/fake dispatch only; no real layer activation or device restoration; [E35](status.md#e35-watchdog-provider-and-recovery-tests-on-frame) |
| 3d | Producer synchronization and metadata-only inspection | Review complete; earlier v3 ARM64 build and all 24 generated-data tests passed on Frame | No image FDs retained/exported by the layer; original inactive bundle/service preflight received; changed tools and actual load remain; [review](#producer-synchronization-review-and-next-checkpoint), [E37](status.md#e37-metadata-only-provider-and-diagnostic-on-frame) |
| 3e | Controlled metadata-layer trial and recovery | Activation paused; runtime baseline passed on Frame | Service/cgroup/compositor observation works with normal passthrough reported; 65 targeted cloud tests pass. Verify independent SSH before planning actual recovery; original failed operation unresolved; [E46](status.md#e46-read-only-runtime-baseline-on-frame) |
| 4 | Capture-only device trial | Planned | Ten seconds of fresh images from one identified source, correct orientation, clean exit and working normal passthrough |
| 5 | Continuous live input in the existing display | Planned; needs input independent of our output | Reuse detector/display after a dedicated provider or supported overlay-excluded mirror mode is validated; current mirror includes the panel |
| 6 | Color, built-in camera support and later geometry | Planned | Validate each source and color conversion separately; require calibration and stronger timing evidence before stereo depth or spatial labels |

The mirror is a time-boxed opportunity for a first 2D demonstration, not a
dependency on mirror availability. If it is absent, unsuitable or includes our
output, record the limit and continue the dedicated-camera work. Existing
replay remains the application baseline. Record any new detection/readability
measurements in status alongside capture results.

Both bounded POC stages now have device and visual results: recorded recognition
in the head-relative panel ([E47](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame))
and mirror recognition with a matching post-capture preview
([E48](status.md#e48-live-mirror-recognition-poc-and-matching-preview)). Continuous
live VR output still requires input independent of our output; these separate
demos do not remove the dedicated-capture activation/synchronization gates.

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
on Frame; see [E25](status.md#e25-descriptor-transport-on-frame). The publication
adaptation and all eight generated-data tests also passed on Frame; see
[E27](status.md#e27-publication-and-loop-controls-on-frame). The bounded GPU query
now passes on Frame, with normal passthrough; see
[E30](status.md#e30-vulkan-capabilities-on-frame). The import adaptation also
builds and passes all ten synthetic tests on ARM64; see
[E31](status.md#e31-import-build-and-synthetic-tests-on-frame). Continue device
and remaining provider work before the next camera trial. GPU resource ownership
is implemented in a separate cloud-tested inspection adaptation. Capture
supervision, provider metadata/lifetime and recovery passed 28 generated-data
tests on Frame. The latest adaptation permits metadata inspection only: the
review found no coordinated producer handoff for live reads. The inspection builds are not
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
| Community Vulkan layer and `ptgrab` | Use metadata-only inspection first; original buffer reads need a coordinated producer handoff or compositor-owned copy |
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
same publisher helper and actual reader together; all eight also passed on
Frame ARM64. Normal cleanup removes its file; abrupt death may leave a stale file
that is refused on restart. This earlier mode does not include the external
watchdog or identity-based recovery added by the newer milestone below.

## Offline provider work while the Frame is unavailable

The GPU capability report and initial synthetic import suite now pass on Frame;
see [E30](status.md#e30-vulkan-capabilities-on-frame) and
[E31](status.md#e31-import-build-and-synthetic-tests-on-frame). The
`--import-fixes` inspection build applies a third versioned
patch and shares its actual import helper with a fake Vulkan dispatch fixture.
The current cloud suite has eleven tests, including an explicit two-plane
modifier case; the user supplied the earlier ten-test ARM64 result. It rejects
unsupported metadata, nonzero binding
offsets, modifier/memory-plane mismatches and incompatible requirements; it
owns duplicate FDs and imported image/memory resources through failure and
normal retirement. Dedicated allocation uses the exact image requirement size,
bounded by the declared allocation. See
[the implementation and tests](development.md#build-and-test-image-import-ownership).

The completion gate tracks only our serial queue: reset, submit and wait results
are checked before allowing retirement. A failed submit or completion wait
leaves destruction deferred to worker/process teardown, rather than freeing
possibly active resources or adding an unbounded device-idle wait. This does
not supply XRService's missing producer fence. The real capture binaries stay
non-executable, and no layer has been installed or activated.

The `--resource-fixes` adaptation now owns the instance/device/pool, host mappings,
private images, command buffers/fences and imported-image device references.
It removes static command resources, validates bootstrap capabilities and bounds
private allocations. Nine synthetic tests pass, with 57 injected failures in
the startup/private-resource path. This is not actual driver or ARM64 execution;
see [E33](status.md#e33-gpu-resource-ownership-cloud-checks).

The earlier `--provider-fixes` v2 milestone added GPU/driver UUID checks,
device-scoped registry entries, generation checks, shared callback lifetime,
an owned private endpoint, worker supervision and conditional configuration
recovery. Its 28 generated-data tests now pass on Frame ARM64; see
[E35](status.md#e35-watchdog-provider-and-recovery-tests-on-frame).
The current v3 adaptation retains only allocation metadata, exports no image
FDs, and blocks the real reader before GPU initialization. Its separate
metadata diagnostic checks profile compatibility and registry stability;
[development](development.md#metadata-only-provider-inspection) documents its tests.
Both changed suites now pass on Frame ARM64: 12 provider/layer tests and 12
metadata-diagnostic tests; see [E37](status.md#e37-metadata-only-provider-and-diagnostic-on-frame).

Remaining work does not need another unchanged GPU query or initial import test:

1. Verify [independent SSH access](on-device.md#verify-independent-ssh-access).
   The [Frame runtime baseline](status.md#e46-read-only-runtime-baseline-on-frame)
   and normal passthrough during/after are confirmed. No repetition of the
   baseline or new restart is needed for this access check.
2. Review the independent recovery procedure before reassessing service control.
   Actual service stop/start and recovery need a separate checkpoint; process
   liveness does not establish rendering readiness. The old command stderr/phase cannot be recovered from static source;
   record the failed operation as unknown unless additional existing evidence
   identifies it. Require independent access before reassessing the gate; keep
   image access disabled.
3. Establish a coordinated producer release/ready/return contract or design a
   copy within the compositor's existing synchronized access window. Validate
   ownership, layout, completion and host visibility before allowing pixel reads.
4. Once that contract is implemented, identify the physical camera in a capture-only trial,
   then verify orientation, scene motion, clean shutdown and normal passthrough.
   Keep inference and live VR image output out of that first trial.

Use the Frame report for device capability policy: graphics family 0, all four
required extensions, YCbCr conversion and dedicated DMA-BUF imports are
advertised. Family 1 is not graphics-capable. The target NV12 modifier advertises
**two memory planes** on this driver; validate real buffer metadata against that
query rather than hardcoding the generic fixture's four-plane case. The reported
memory types are candidates; actual import still needs the FD/image memory-bit
intersection. Exporter/source metadata is still needed to revise conservative
profile restrictions. The helper validates
declared sizes and lets Vulkan validate layouts; it does not prove the actual
DMA-BUF payload length or complete compressed-plane footprint. The new provider
records/restricts exporter flags/sharing and checks GPU/driver UUIDs, but those
checks have only synthetic evidence. Actual image imports remain untested; ARM64
compile/link and synthetic helper execution are confirmed. Keep the source-buffer
synchronization review and capture-only trial
as separate acceptance gates.

## Next engineering step: capture worker supervision

The external supervisor is now implemented and cloud-tested for startup,
import, copy and destruction hangs, signals, parent death, bounded output and
reaping. It distinguishes graceful API cleanup from forced termination and
recovers only recorded publication identities. Provider generation/loss,
metadata validation, layer/server lifetime and exact configuration recovery
also have synthetic tests. The heading is retained for existing links; this
implementation milestone is complete in the cloud.

The original synchronization/FOREIGN ownership review is complete; it found a
missing producer handoff. The earlier no-camera milestone also passed on Frame.
Earlier native v3 validation passed all 24 tests on Frame. The inactive bundle
and service properties were received. A bounded trial is now implemented with
post-exit recovery, plus loader fallback and diagnostic timing fixes. The
changed tools were prepared for a [device trial](on-device.md#run-the-bounded-metadata-layer-trial),
but the user then reported a restart without console recovery. Further
activation is blocked. The saved report now confirms reported configuration
restoration, followed by an unconfirmed service start; SteamVR is later active
and UI/passthrough work. Actual layer loading and successful inspection remain
unverified. The failure-window journal and unit/selector source are now received.
The offline lifecycle work below passes its targeted checks, and its read-only
runtime observation now passes on Frame. Independent access and actual recovery
remain separate checkpoints.
The supervisor's capture flag returns `blocked` before launching a worker.
Use the [updated native procedure](on-device.md#test-metadata-only-inspection-without-capture).

### SteamVR service-control acceptance

The offline lifecycle model and recovery changes are implemented, based on
the [shipped unit and selector](status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix).
All 65 targeted cloud tests pass: 48 trial, nine service-observation and eight
configuration tests. Generated hook/job events distinguish required and ignored
failures, a separate waiter/compositor, delayed transitions and competing startup.
This is a bounded model, not an emulator or proof of actual vendor hook behavior;
see [E45](status.md#e45-offline-platform-lifecycle-and-guarded-recovery).

The quarantined runner now covers these cases using generated events, fake
process metadata and real temporary configuration, without executing vendor hooks:

- Keep the startup request, tracked waiter and compositor/provider readiness
  distinct. A waiter PID or `ActiveState=active` alone cannot complete inspection
  or establish normal passthrough recovery.
- Model required pre-start and post-stop hook failures, ignored-failure Bash
  hooks, a canceled service job and a client error followed by a later state
  transition. Persist the operation, phase and bounded diagnostics before
  recovery decides its next action; avoid competing retries.
- Test delayed shutdown, an absent waiter with a remaining compositor, and
  competing startup. Restore configuration only after a confirmed stop; if
  that cannot be established within the deadline, report it and preserve the
  known recovery plan rather than claim restoration.
- Keep cleanup independent of the console and trial worker. Distinguish file
  restoration, a successful normal-service start, endpoint/compositor readiness
  and the user's passthrough check. Preserve mandatory platform hooks and the
  configured service policy.

Passing these offline cases does not remove the activation block. The
[read-only runtime observations](status.md#e46-read-only-runtime-baseline-on-frame)
now pass on Frame. These establish compositor process liveness, not rendering
readiness or live camera access. Before a later device rehearsal, verify a
working independent terminal/SSH route, define the exact recovery command and
readiness checks, and retain command/job diagnostics. Do not require a working
console app inside SteamVR to recover SteamVR. Never rerun the old trial just
to obtain its missing stderr. Image access remains a separate blocked gate.

## Producer synchronization review and next checkpoint

Review date: 2026-10-17 UTC. At upstream pin `ff6a2b2`, `copyRegions` and the
downscaler acquire from `VK_QUEUE_FAMILY_FOREIGN_EXT` into their own queue with
`GENERAL` layout. The source supplies no producer completion wait or coordinated
hold on the buffer and no matching return to the external owner. Current upstream
HEAD `8852ec9bdd4f81fb5a359e5bf88ed25f0e6fc89e` was also checked: the three capture
source files and `REPORT.md`/`FINDINGS.md` are byte-identical to the pin. The build pin
remains unchanged. These are source findings, not an observed device failure.

The review changes the implementation boundary:

- Duplicating a DMA-BUF FD preserves allocation lifetime. It does not prevent
  XRService or an ISP from rewriting that allocation while we copy it.
- A registry generation tracks binding/destruction, not frame completion or
  reuse. A stable 16-row strip and our own queue fence cannot supply the missing
  producer sequence, release or exclusive access interval.
- Vulkan [external sharing rules][vk-external-sharing] require ordered ownership
  release/acquire; [ownership transfers][vk-ownership] may themselves read and
  write memory. The correct owner and actual image layout must come from the
  producer contract. `PTGRAB_QFOT=0`, a guessed `GENERAL` layout or an unmatched
  return barrier does not establish that contract.
- Linux DMA-BUF [sync-file export][linux-dmabuf] snapshots current fences. Its
  export/submit/import sequence is not atomic; userspace must coordinate against
  other submissions. Waiting on that snapshot alone cannot exclude a later writer.
- A private staging copy also needs a transfer-write to host-read memory
  dependency, completion wait and any noncoherent invalidation before CPU access.
  The reviewed reference lacks the host-read barrier. A fence wait alone does
  not establish [host visibility][vk-host-visibility].

The v3 layer therefore records metadata without retaining imported image FDs.
The server accepts explicit metadata requests and rejects buffer requests; the
worker/supervisor stop before live access. The diagnostic makes no Vulkan calls,
reads no image data and reports `capture_contract_available: false`. A matching
2464-square NV12 profile is only a candidate; it does not identify Arcturus,
prove complete frames or authorize import.

For live input, prefer a supported vendor frame lease that provides ready/return
synchronization, valid layout, ownership and reuse rules. The alternative is to
copy into our own allocation during a demonstrated synchronized compositor
access window, then publish only that owned copy. Do not add independent GPU
reads until one path is justified and tested. Metadata observation is the next
small device checkpoint. The service-aware trial/recovery procedure is now
implemented and cloud-tested; actual Frame activation remains untested.

[vk-external-sharing]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/resources.adoc#resources-external-sharing
[vk-ownership]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/synchronization.adoc#synchronization-queue-transfers
[vk-host-visibility]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/synchronization.adoc#synchronization-fences-waiting
[linux-dmabuf]: https://github.com/torvalds/linux/blob/v6.18/include/uapi/linux/dma-buf.h

## Next provider step: query GPU capabilities

The standalone `capture_gpu_check.py` supervises an explicit Vulkan query with
a ten-second worker deadline. It is implemented and passes controlled tests
with a fake loader. The Frame query completed successfully in 9.38 ms, with
normal passthrough and clean teardown; see [E30](status.md#e30-vulkan-capabilities-on-frame).
Retain the [device procedure](on-device.md#query-vulkan-capture-capabilities)
for a changed tool/runtime. Its default
does not load Vulkan. The opt-in creates an instance and queries physical-device
properties without creating a logical device, opening a camera, connecting to
the provider or importing any FD. Existing loader/driver/layer configuration
is honored; the check changes none of it.

Use the report to choose a physical device and validate the reviewed importer's
advertised API/extensions, sampler conversion, graphics queue, host-cached memory,
NV12 Qualcomm modifier/memory-plane count, external DMA-BUF image support and
R8 downscaling requirements. These are capability queries, not proof that an
actual buffer can be imported or safely read. Missing capabilities should guide
the next implementation change rather than trigger a camera trial.

Image import validation/ownership is implemented in the inspection adaptation
above, and its initial ten-test suite passes on ARM64. Use the actual report for
GPU selection and capability policy. The separate GPU resource adaptation now
supplies ownership for other resources.
The separate capture watchdog, registry invalidation, layer lifetime and exact
file recovery helpers are now implemented and tested with generated data.
The producer review found no coordinated access contract; the latest mode
allows metadata inspection only. Complete native v3 validation and a controlled
metadata-layer load/restoration checkpoint before designing the synchronized
copy path. Original camera-buffer completion and source identity remain
separate acceptance checks even after shared-memory publication is correct.

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
shared MobileNet-SSD detector without a visible result window. Its first bounded Frame
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
