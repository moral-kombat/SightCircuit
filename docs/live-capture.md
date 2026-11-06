# Live capture plan

This is the authoritative current capture work plan. [Status](status.md) owns
capability and validation evidence; the [dated source review](capture-reference-review.md)
explains the code findings behind this sequence. Every deliverable below is
planned unless a task explicitly says otherwise. Metadata-only layer trials
have run ([E65](status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed));
dedicated image access remains blocked.

## Current work order

### Scope decision (2026-10-27)

Revised 2026-10-31: the POC goal is object names with approximate direction
arrows in a fixed edge HUD over live mirror input, with the HUD footprint
masked before inference; world-locked labels are deferred. See
[POC scope](poc-scope.md).
The provider track below stays frozen, and the handoff that follows still
governs device authorization.

The [upstream reuse plan](poc-scope.md#upstream-reuse-plan-2026-10-31) maps the
2026-10-31 source review to these work packages and separates optional later
assistant/recovery work from the current POC.

### October 26 evening handoff

The latest source-only check is [E60](status.md#e60-evening-source-only-mirror-capture-and-scene-correspondence):
The owner confirmed the saved TV scene and normal operation during and 30 seconds
afterward without a toggle. The subsequent [one-still comparison](status.md#e61-file-only-detector-comparison-on-the-evening-grayscale-still)
ran successfully but both models missed the expected TV. The
[green-input recurrence and V4 physical cleanup failure](status.md#e59-arcturus-green-input-recurrence-and-operator-recovery)
remain unresolved. The subsequently recovered [V4 evidence](evidence/e075.md)
explains its early software stop and cleanup fields. E58's general wearer
confirmations remain valid within their original scope.

This checkpoint governs the next work; existing commands elsewhere are not
authorization for another device operation. Frame/Arcturus remains the goal.
Native passthrough is a recovery workaround, not acceptance of a different
POC input source.

1. **Obtain approved access to the existing still before diagnosing it.**
   Inspect the full `scene-gray.png` and the MobileNet bottle box
   [121, 62, 630, 351] with the owner. Record whether the TV is physical, shown on a
   display, cropped, obscured or small in the image; adjudicate the bottle
   only after inspection. The current handoff grants no private-image
   transfer or server exposure. Obtain the existing source digest through an
   approved route; do not invent it, recapture the scene or assume a file
   with the same name is identical.
2. **Make the file-only comparison reproducible.** Pin the source SHA-256,
   code revision, model hashes, actual runtime, threshold 0.5 and warm-up
   policy. Compare the saved 640 × 360 grayscale input with the application's
   conversion/preprocessing path: channel replication/order, scaling,
   crop/framing, SSD stretching, YOLOX letterboxing and mapped boxes.
   Measure one controlled change at a time using the same source and both
   detectors; preserve the original results. Add a focused synthetic
   regression test only if a conversion/mapping defect is found. Do not lower
   thresholds or select a model to make this one image pass.
3. **Keep the evaluation limits explicit.** A `tv`-only manifest tests that
   label's presence; no absent-label or localization accuracy is established.
   Add factual absent labels or box annotations only after image inspection.
   Broader accuracy needs a separately approved representative set, including
   the earlier rug/sofa failure; new still capture is not authorized here.
   Per-image timings are not live throughput or latency.
4. **Resolve physical lifecycle acceptance before proposing output.** Shared
   masking, calibration expiry, guardian supervision, observation deadlines and
   truthful reporting are implemented offline ([E72](evidence/e072.md),
   [E74](evidence/e074.md)). Recovered original V4 evidence and hash-matched
   source now explain the rejected marker callback and immediate client-requested
   stop ([E75](evidence/e075.md)). Inspect any already-saved camera/compositor/
   Arcturus logs and exact runtime versions covering October 24 around 22:48 EDT
   to investigate the still-unexplained green passthrough. Record missing logs
   as unavailable. Keep process reaping, API cleanup and wearer recovery separate;
   do not repeat the unchanged marker test or infer physical recovery from exit 0.
5. **Keep live acceptance separate.** A future proposal needs a pinned
   artifact, explicit source and image/display limits, an independent abort
   path, truthful cleanup outcomes and wearer confirmation. Any HUD mapping
   needs all observed copies/corners and conservative footprints; dimensions
   alone cannot identify eyes or prove feedback exclusion. Blocking VR calls,
   SIGKILL or stuck kernel I/O can leave recovery uncertain. Production
   combined live-source/HUD authorization remains closed; no new capture,
   device commands, camera changes, downloads, restarts or headset tests are
   authorized by recording these results.

The coordinator owns integration, evidence IDs and exact-diff review. Current
implementation uses the [execution prompt](execution-prompt.md); the dated
handoff does not supersede the active user request or authorize a new device
operation. Preserve unrelated `observation_page` work and do not replay a
consumed one-run approval.

| Order | Deliverable | State | Acceptance |
| --- | --- | --- | --- |
| 1 | Bounded diagnostic for an existing SteamVR mirror | Complete for the tested view: room capture works; VR panel is included | Keep for bounded/no-display recognition and post-capture snapshots; physical source/timing unresolved; [evidence](status.md#e18-vr-panel-included-in-mirror-input) |
| 1a | Recognition on the saved mirror still | Frame execution confirmed; rug incorrectly labeled sofa | Keep execution success separate from classification quality; [evidence](status.md#e15-saved-room-recognition-and-rug-false-positive) |
| 1b | Bounded recognition from mirror frames | Latest Frame POC execution and inspected preview localization confirmed | 100 reads, 51 processed at 4.993 fps, 40.065 ms mean inference and clean shutdown; user confirms matching labels/boxes and normal passthrough/tracking during/after. Broader accuracy and continuous output remain separate; [E48](status.md#e48-live-mirror-recognition-poc-and-matching-preview) |
| 1c | Check capture of our own display | Confirmed: the VR panel appears in captured output | Current composited mirror does not exclude our output; no continuous live-image panel from this source; [E18](status.md#e18-vr-panel-included-in-mirror-input) |
| 1d | Detector quality and class coverage | One Frame grayscale still compared; both models missed the expected TV | Inspect the saved input and preprocessing before diagnosing; preserve one-image limits and the earlier rug case; [handoff](#october-26-evening-handoff), [E61](status.md#e61-file-only-detector-comparison-on-the-evening-grayscale-still) |
| 2 | Isolated shared-memory reader and controlled producer | Controlled x86_64 and Frame ARM64 checks passed; real provider unverified | All 14 native/supervisor tests pass on Frame; upstream writer ordering and original GPU completion remain unresolved; [E23](status.md#e23-reference-build-and-controlled-reader-on-frame) |
| 3 | Dedicated camera provider | Frozen; metadata-only load/restoration reported, image access blocked | Reconsider only after a vendor frame interface or coordinated producer handoff exists; metadata is not image ownership |
| 3a | Validated image import and ownership | ARM64 build and ten synthetic tests passed; two-plane coverage added in cloud | Actual camera DMA-BUF import remains untested; [E31](status.md#e31-import-build-and-synthetic-tests-on-frame), [E32](status.md#e32-two-plane-import-fixture-cloud-checks) |
| 3b | Device and remaining GPU resource ownership | Inspection build and nine synthetic cloud tests pass | Native ARM64 and actual driver execution pending; no activation; [E33](status.md#e33-gpu-resource-ownership-cloud-checks) |
| 3c | Capture watchdog, provider metadata/lifetime and configuration recovery | Earlier v2 milestone passed all 28 tests on Frame ARM64 | Generated workers/fake dispatch only; no real layer activation or device restoration; [E35](status.md#e35-watchdog-provider-and-recovery-tests-on-frame) |
| 3d | Producer synchronization and metadata-only inspection | Earlier v3 ARM64 build and 24 generated-data tests passed on Frame; later metadata-only trials reported | No image FDs retained/exported; metadata does not establish synchronized ownership; track frozen; [review](#producer-synchronization-review-and-next-checkpoint), [E37](status.md#e37-metadata-only-provider-and-diagnostic-on-frame), [E65](status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed) |
| 3e | Controlled metadata-layer trial and recovery | Later metadata trials loaded/restored the provider; full registry still unstable | Reconstructed device reports preserve unknown dates/revisions; do not repeat v2 unchanged; image access remains blocked; [E65](status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed) |
| 4 | Capture-only device trial | Planned | Ten seconds of fresh images from one identified source, correct orientation, clean exit and working normal passthrough |
| 5 | Fixed edge HUD over live mirror input | Implemented offline; production closed | Qualify the full native footprint and all eye copies, then pass feedback/expiry and live acceptance gates; a captured-image panel still needs a separate exclusion solution |
| 6 | Color, built-in camera support and later geometry | Planned | Validate each source and color conversion separately; require calibration and stronger timing evidence before stereo depth or spatial labels |

The mirror is the selected fixed edge HUD input. Its inclusion of our output
is addressed only by a qualified native-resolution footprint mask and feedback
acceptance. If the mirror is absent or unsuitable, record the limit and stop
live preparation; continue independent offline work and replay. This does not
reopen the frozen provider track. Record new measurements as per-entry evidence.

Both bounded POC stages now have device and visual results: recorded recognition
in the head-relative panel ([E47](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame))
and mirror recognition with a matching post-capture preview
([E48](status.md#e48-live-mirror-recognition-poc-and-matching-preview)). Continuous
live HUD output still requires qualified masking and feedback/expiry acceptance.
The separate demos do not pass those gates or reopen dedicated capture.

Seek a supported Arcturus/Valve frame-sharing API alongside these experiments
and prefer it if available. The [vendor questions](#vendor-interface-questions)
define the useful interface information without making a vendor reply a
prerequisite for offline reader development.

## Minimum LIVE POC and October 26 target

The minimum LIVE POC is the **combined** loop: live Frame mirror input, CPU
detection, and object names shown live in the headset, continuously, without
our own output feeding back into detection and with normal passthrough and
tracking. Replay with the VR panel
([E47](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame)) has no
live input. Bounded mirror recognition
([E48](status.md#e48-live-mirror-recognition-poc-and-matching-preview),
[E54](status.md#e54-bounded-mirror-capture-on-merged-main)–[E58](status.md#e58-user-observations-for-the-2026-10-26-runs))
shows results only after capture closes. Neither is this POC.

**October 26 target: not met.** The target was a working combined loop by
Monday, October 26, 2026. At the
[October 26 evening handoff](#october-26-evening-handoff), live input and live
display were still separate, and production live-source and HUD output
remained closed. The goal was narrowed on October 27 and revised on October 31
to the fixed edge HUD (see the [scope decision](#scope-decision-2026-10-27)).
The [POC scope](poc-scope.md) now owns scope, work packages and acceptance;
this section keeps the critical path, evidence, risks and offline order in one
place for capture planning.

### Critical path

1. **WP10 lifecycle and cleanup safeguards.** V4's software stop and cleanup
   fields are explained ([E75](evidence/e075.md)); its green physical outcome
   and the later recurrence remain unresolved. Investigate existing runtime
   evidence and establish physical recovery before any live output.
2. **WP9 HUD mapping calibration** on Frame, using the
   [calibration checkpoint](on-device.md#bounded-edge-hud-calibration-checkpoint).
3. **WP11 feedback exclusion check** on Frame with the qualified mask.
4. **WP12 live HUD loop**, behind explicit opt-in and an independent abort
   path.

WP1 (preprocessing audit) and WP2 (evaluation set) run alongside and are needed
before any claim about detection quality. The gates each step must pass are in
[Edge HUD acceptance gates](#edge-hud-acceptance-gates).

### Acceptance evidence

The POC passes on the [acceptance run](poc-scope.md#acceptance). Its report
must carry, as one numbered evidence entry separate from cloud results:

- The exact revision, source identity and capture format, and the versioned
  HUD layout and mapping in use.
- The native mask regions, and a count of detections inside them (which must
  be zero).
- Result expiry events, including a name expiring after the object leaves
  view.
- The supervisor, worker and native cleanup fields, reported separately from
  physical recovery.
- The latency statistics the
  [device report template](templates/hud-device-validation-report.json) names
  under `g6_acceptance.latency`: nearest-rank p50, p95 and maximum input age,
  inference, processing and output age in milliseconds, copied from the run
  report's `pipeline` block ([definitions](development.md#latency-statistics)).
- The wearer's answers on HUD visibility, arrow direction against the agreed
  tolerance, and passthrough and tracking during and after the run.

### Risks

- The mirror includes our own output
  ([E18](status.md#e18-vr-panel-included-in-mirror-input)). Only a qualified
  native-resolution mask addresses this; masking is not a compositor
  exclusion mode.
- The mirror is composited. Its physical camera identity, field of view and
  timing are unknown ([E14](status.md#e14-physical-room-visible-in-the-mirror-preview)),
  so arrows stay approximate.
- V4's green physical outcome and the Arcturus green-input recurrence remain
  unresolved; recovered [E75](evidence/e075.md) explains only software termination
  and cleanup reporting.
- Detection quality is unproven on Frame input: both models missed the TV on
  the one compared still
  ([E61](status.md#e61-file-only-detector-comparison-on-the-evening-grayscale-still)),
  and neither has a rug class.
- Blocking VR calls, SIGKILL or stuck kernel I/O can leave recovery uncertain
  (handoff item 5). Software deadlines are not guarantees.
- About 5 processed frames per second with roughly 40 ms inference (E48)
  limits how quickly names update.

### Offline work order

These need no device and no approval:

1. Keep the synthetic-only gates in the
   [contract review](#synthetic-input-and-feedback-contract-review) intact.
2. WP10: preserve the reconciled safeguards and recovered software account;
   inspect existing runtime logs/version evidence for the green physical outcome
   (handoff item 4 and [E75](evidence/e075.md)).
3. WP1 preprocessing audit, once access to the still is approved.
4. WP7 is complete offline; maintain its evidence sources and generated summaries.

## Edge HUD acceptance gates

The [fixed edge HUD](architecture.md#fixed-edge-hud-decision) reaches live use
only through these gates, in order. Each gate's device evidence is its own
numbered entry. A failed gate stops the sequence; do not repeat it unchanged.
Live HUD input and production HUD output stay closed in code until G4 and G5
pass: the pipeline rejects non-synthetic sources
(`live_image_source_not_approved`), the HUD mask rejects hardware mappings
(`hud_hardware_mapping_not_approved`), and the bridge rejects expiring packets
in real output mode. [`framejacker/gates.py`](../framejacker/gates.py) is the
single table of these closed gates (live image input, hardware mapping, live
HUD output) and the rows that lift each one. Every Python `*_not_approved`
refusal goes through a gate's `require`, every report's
`production_*_enabled`/`mapping_qualified` claim comes from `gates.claims()`,
and `tests/test_gates.py` rejects a hand-written `not_approved` refusal or a
gate claim written as a constant (dictionary entry, keyword argument or
subscript assignment) in `framejacker/` and `scripts/`. The HUD sink's own
contract checks (`hud_sink_*`, `hud_requires_fixed_validation_sink`) and the
native bridge's `expiring_frames_validation_only` refusal stay hand-written as
defence in depth; the test does not scan native code. Nothing in the
table can open a gate; lifting one is a reviewed change after its rows pass on
the headset. The separately scoped
[device-validation design](hud-qualification.md#separately-scoped-device-validation)
defines the G4/G5 evidence path without enabling production output. Its design
and offline report checks do not authorize or implement a live device run.

| Gate | Requires | Status |
| --- | --- | --- |
| G1. Synthetic mask, fail-closed mapping and expiry | Offline tests: full-footprint mask before resize, margin and eye union, fail-closed mapping and format changes, stale expiry independent of inference | Passes offline ([E69](status.md#e69-offline-edge-hud-and-native-mask-validation)) |
| G2. Lifecycle and cleanup (WP10) | Recovered software termination account; evidenced physical failure/recovery account; reconciled guardian, calibration expiry and truthful reporting with failure-path tests. If the cause stays unknown, a written recovery procedure that the owner has rehearsed on the headset satisfies the physical part ([owner decision, 2026-11-03](poc-scope.md#owner-decisions-2026-11-03)) | Open: software account established, green physical failure/recovery unresolved ([E75](evidence/e075.md)) |
| G3. Mapping observed on Frame (WP9) | [Calibration checkpoint](on-device.md#bounded-edge-hud-calibration-checkpoint) ends `observed_unqualified` with clean cleanup; normal passthrough confirmed | Historical V4 attempt rejected ([E75](evidence/e075.md)); no accepted observation |
| G4. Mapping qualified | Versioned evidence for eye composition, all HUD copies, conservative margins and bounded operating limits; see the [proposed method](hud-qualification.md#proposed-g4-method). Candidate schema validation is not qualification | Proposed method; device qualification pending |
| G5. Feedback exclusion and physical expiry (WP11) | With the HUD visible, no detections from its footprint during head motion; stale names disappear on the headset within the expiry | Not run |
| G6. Live HUD loop (WP12) | The [POC acceptance run](poc-scope.md#acceptance) with the evidence listed under [Acceptance evidence](#acceptance-evidence) | Not run |

G1 can be rerun at any time without a device:

```bash
cd "$HOME/framejacker"
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest \
  tests.test_hud_mask tests.test_hud_fill tests.test_live_pipeline tests.test_display_expiry
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m scripts.live_pipeline \
  --synthetic-check --synthetic-hud --validate-overlay --seconds 3
```

Since 2026-11-01 a synthetic HUD run is also a validation run: any box touching
the mask, or not inside exactly one eye, fails it. With the zero-filled mask the
default MobileNetSSD reported the black footprint region of the generated
frames itself as `tvmonitor`, so that run ended `validation_failed` and this
recipe used `--detector fixture` instead. Since 2026-11-03 the mask refills the
zeroed region from its surround
([neutral mask fill](hud-qualification.md#neutral-mask-fill-2026-11-03)), and
the recipe is back on the default detector. The second command now runs
MobileNetSSD on the masked frames, checks the mask, the validation ledger and
the native transport, and ends `completed` with exit 0 and
`g5_software.status` `checked_unqualified` (cloud run, runner CPU, generated
frames; not a Frame result; [E78](evidence/e078.md)). `--detector fixture`,
which reports no detections and loads no model, still checks the same plumbing
where the models are absent.

## Synthetic input and feedback contract review

This review records what the offline live pipeline guarantees today and what
still needs a device. Design and supervision details are in
[development](development.md#offline-foundation-for-the-minimum-live-poc).

**Input contract.** `scripts/live_pipeline.py` accepts only a `synthetic`
source; any other kind is rejected with `live_image_source_not_approved`.
Frames are owned contiguous BGR copies, and source identity and sequence
cannot go backwards. One frame at most waits for inference, and inputs or
results older than one second are dropped. Do not label a real source
`synthetic` to get past the gate.

**HUD contract.** `HudMask` in `framejacker/hud/` accepts only synthetic calibration and
rejects hardware mappings. It masks the full HUD footprint at native
resolution before any resize. Detections that overlap the mask or cross an eye
boundary are dropped from presentation and counted, and a synthetic validation
run with any of them fails.

**Output contract.** The overlay sink only requests the bridge's
`--validate-stdin` mode, so it cannot initialize real VR output. Synthetic
display frames carry an absolute monotonic expiry to the separate native
validation process. Stalled inference, settings heartbeats and partial packet
reads cannot renew it. The expiring-packet opcode is rejected in real output
mode.

**Supervision contract.** Workers run under the shared bounded supervisor and
a Linux guardian that owns the worker process group. Forced termination is
reported as such and never as a clean shutdown. An unkillable process is
reported unconfirmed.

What this does **not** establish: any real live source, actual expiry on the
headset, launcher behavior, or API teardown on Frame. Each needs its own
authorized device check, and the [acceptance gates](#edge-hud-acceptance-gates)
say which. To rerun the contract tests without a device:

```bash
cd "$HOME/framejacker"
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest \
  tests.test_live_pipeline tests.test_display_expiry tests.test_bounded_worker \
  tests.test_overlay_cleanup tests.test_hud_mask
```

## Next deliverable: mirror diagnostic

The standalone diagnostic is implemented in `scripts/mirror_check.py` with
read-only V4L2 handling now in `framejacker/sources/mirror_device.py`. It has passed controlled
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
prevents unmasked continuous captured-image panels in the tested mode. The
fixed edge HUD instead needs qualified footprint masking and feedback acceptance.
Keep bounded/no-display recognition and post-capture snapshots as the baseline.
The user confirms Arcturus is physically connected, but does not
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
[E31](status.md#e31-import-build-and-synthetic-tests-on-frame). These historical
provider milestones do not reopen the frozen track. GPU resource ownership
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

This section preserves the dedicated-input research path, which is deferred
and frozen for the current POC. It is not a prerequisite for the qualified-mask
HUD. For a future supported route, the Arcturus vendor app provides live images
according to [device evidence](status.md#e2-vendor-app), and the reviewed
community code implements an experimental path for its passthrough pair.
Built-in camera capture requires separate validation.

| Route | Decision |
| --- | --- |
| Supported Arcturus/Valve interface | Prefer it if available; no public live-frame contract was found in the reviewed pages |
| Public OpenVR tracked-camera API | Retain the [failed result](status.md#e3-public-camera-probe); do not repeat the unchanged probe |
| Existing SteamVR composited mirror | Selected fixed edge HUD input, conditional on qualified native masking and feedback/expiry acceptance; unmasked live captured-image panels remain unsuitable |
| Community Vulkan layer and `ptgrab` | Frozen; original buffer reads need a supported producer handoff or compositor-owned synchronized copy before this track can resume |
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

Historical provider preparation follows for reference. New provider work remains
frozen until a vendor interface or producer-synchronization answer exists; the
active offline WPs are in [POC scope](poc-scope.md#work-packages).

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
On 2026-10-17 all 65 targeted cloud tests passed: 48 trial, nine
service-observation and eight configuration tests (the service-observation
suite now has 10). Generated hook/job events distinguish required and ignored
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
post-capture snapshots. Streaming captured images into a panel still needs an
independent exclusion solution; no alternate mirror exclusion mode is validated.
The adopted fixed edge HUD uses a different, bounded path: qualify its complete
native-resolution footprint mask, then verify feedback exclusion and physical
expiry through the [acceptance gates](#edge-hud-acceptance-gates). Ordinary Qt
windows need their own feedback check. The dedicated provider remains frozen.

The shared `Frame`, `FrameSource`, `Pipeline` and replay adapters already exist.
Extend their safeguards without making a dedicated capture-only trial a
prerequisite for the HUD path. Carry image format,
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
