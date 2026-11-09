# Proof-of-concept scope and decisions

This page owns the product goal, work-package definitions and accepted
choices. [Current work](live-capture.md#current-work-order) owns progress,
blockers and ordering. [Status](status.md) owns verified results.

## Decision

The POC is: **detect objects in live Frame mirror input and show their names,
each with an approximate view-relative direction arrow, in a fixed
head-relative strip at the edge of the view, without the HUD feeding back into
detection and without disturbing passthrough.**

The HUD is a fixed edge tile, not a world-locked or scene-anchored marker, and
it makes no claim of spatial accuracy. No label, arrow or leader line crosses
the scene. The scene pixels under the strip and its mask margin are an explicit
blind region.

Deferred until this works: world-locked labels (pose logging, projection,
tracking and a world-locked overlay mode; see
[deferred work packages](#deferred-work-packages)), the dedicated camera
provider, stereo depth and Gaussian splats. The provider track stays frozen (no
producer synchronization exists; live reads are blocked).

Reasons: the only live source proven on Frame is the SteamVR mirror (E48, E60),
and head-relative panels already work on merged `main` (E55, E58). The mirror
includes our own output (E18). A fixed strip whose full footprint is masked at
native mirror resolution is the smallest output that can keep itself out of
detection without a compositor exclusion mode. A world-locked label adds pose
and frame-time alignment and a projection model, each a separate unproven link.

## Constraints that shape the design

- The mirror includes our own VR panel (E18). Mask the **full HUD footprint**
  (background, text, arrows and edge effects) at native mirror resolution
  before any resizing, filtering or inference, with a calibrated conservative
  margin and the union of both eye footprints where stereo composition
  applies. Masking only detected text or a resized image does not establish
  exclusion, and dimensions alone do not identify the footprint. The
  [qualification method](hud-qualification.md#proposed-g4-method) defines the
  required mapping evidence.
- The HUD layout and capture mapping are fixed and versioned together.
  Unexpected capture dimensions, crop, eye layout, orientation, source or
  session, or HUD configuration changes fail closed: stop presentation and
  inference and expire prior content. Never reuse an old mask, silently
  recalibrate or enlarge the visible HUD beyond the qualified mask.
- Results expire on their own clock, independently of inference and
  heartbeats, so a stale name disappears.
- Detections overlapping the masked region or crossing an eye boundary are
  rejected.
- The mirror view is composited and its physical camera identity, field of view
  and timing are unknown (E14, E18). Arrows are approximate view-relative
  directions, not calibrated bearings.
- V4's software stop and cleanup fields are explained by recovered, verified
  evidence ([E75](evidence/e075.md)). Its green Arcturus outcome and the later
  recurrence remain unexplained. G2 closed under the accepted recovery-rehearsal
  alternative ([E79](evidence/e079.md)); this does not identify the cause or
  prove the procedure cures green input. Any green input aborts a run.

## Work packages

Work-package IDs are stable references, not priority numbers. Their definitions
remain unchanged; current implementation and acceptance belong in the
[current work table](live-capture.md#current-work-order).

| ID | Purpose | Completion criterion |
| --- | --- | --- |
| WP1 | Audit the E61 preprocessing/model miss | Explain the reproduced miss or attribute it to the image/model with evidence; the original still and verified digest are available on Frame ([E80](evidence/e080.md)) |
| WP2 | Evaluate detectors on representative Frame stills | Factual annotations and comparable evaluation before a detector-default choice |
| WP7 | Keep documentation navigable and evidence traceable | Concise entry points, maintained ownership and reproducible generated summaries |
| WP9 | Observe and qualify the HUD mapping | Accepted observation plus reviewed composition, all-copy, margin and operating-limit evidence |
| WP10 | Establish lifecycle and cleanup acceptance | Truthful software cleanup and evidenced physical recovery |
| WP11 | Validate HUD feedback exclusion and expiry | Qualified mapping, no HUD-footprint detections during motion, and physical expiry evidence |
| WP12 | Demonstrate the combined live HUD | The acceptance run below, after its gates pass |

### Deferred work packages

WP3 (pose/time logging), WP4 (projection), WP5 (world-locked overlay), WP6
(tracking/smoothing) and WP8 (world-locked live loop) remain deferred with
world-locked labels. Dedicated camera capture, stereo depth and splats also
remain outside the current proof of concept.

## Acceptance

One real object named in the HUD for 60 seconds while the wearer turns their
head, with its arrow pointing within a tolerance the wearer agrees to before
the run; no detections from the HUD's own footprint; the name expiring when
the object leaves view; normal passthrough and tracking during and after; a
clean shutdown with truthful cleanup reporting; and the report recorded as a
numbered evidence entry separate from cloud results. Detection accuracy beyond
that object, and any world position, are not claimed.

## Owner decisions (2026-11-03)

These are the accepted decisions recorded in this checkout. Device approvals
in the table are historical, scoped records; they are not reusable permission
for a new run. Decisions do not supply evidence or open production gates.
The results of the recorded runs now live in E79–E83, integrated by
[SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1).
The chair decision below was added on November 4.

| Topic | Decision |
| --- | --- |
| Who authorizes device runs | The owner. Each step below is approved once; repeating a step, or changing it, needs a new approval. |
| G2 without a root cause | If the saved logs do not explain the green input, G2 may close on a written recovery procedure. The owner rehearses that procedure on the headset first, and any green input aborts the run. Unexplained physical failures remain recorded as unknown. |
| G2 log analysis | The approved read-only saved-log analysis is recorded in [E79](evidence/e079.md). It did not identify the green-input cause; the owner-rehearsed recovery procedure closed G2 |
| WP9 / G3 | The approved November 3 attempt and owner-approved retry are recorded in [E83](evidence/e083.md). Both stopped before a marker appeared. A further attempt needs a reviewed precondition change and new approval, following the [device checkpoint](hud-qualification.md#next-device-checkpoint) |
| Catch-up session | The completed regression, pacing, 400-frame panel, mirror and five-still evaluation session is recorded in [E81](evidence/e081.md). The invalid initial mirror/stills attempt is retained and excluded from scoring |
| WP1 | Check the original still and digest locally without uploading. [E80](evidence/e080.md) found and verified it, so the missing-input retirement alternative does not apply. The reproduced miss remains unexplained |
| POC object | **Chair**, changed by the owner on November 4 after the catch-up stills. Both detectors found the chair (0.98 and 0.78); neither found the clearly visible bottle above 0.05. This is an owner choice on five unboxed stills, not a detector-default decision; [E81](evidence/e081.md) |
| Arrow tolerance | The arrow is one of eight directions, or a dot near the centre. It must point toward the side of the view where the object is. No bearing accuracy is claimed. |
| Strip edge | Lower right (the current `HudConfig`). Choosing among several named positions comes after acceptance. Each position needs its own qualified mapping, because a mask is bound to its configuration digest. |
| Mask fill (G5, E76) | The coder tests a neutral fill for the masked area offline. The strict overlap rule stays as it is. |
| Frame agent (PR #16) | Guarded Phase 0 setup and guard settings were installed on Frame; [E82](evidence/e082.md). Live guard enforcement and the Tier A task runner still have no recorded Frame test |
| Tracking-token binding (package I) | Answered later on 2026-11-03, in the coder session that recorded E77 and E78. The tokens stay bound when `MirrorSource` is declared: a first `tracking_state` must reach the `TrackingObserver` before the source is created. A state first seen later leaves `tracking_origin` and `recenter_epoch` undeclared for the run, so a bound mask rejects every frame. Late binding was not adopted. |
| Unobserved session token (handoff item 2) | Answered on 2026-11-03, in the coder session that recorded E77 and E78. A session token that the marker observer did not observe rejects the observer-to-candidate conversion (`hud_validation_report.py --from-observer`). There is no declared fallback, behind a flag or otherwise; `--declared-context` keeps only the non-token context fields, and its `native_format` must equal the observer's capture format. |
| Observer /proc reads (handoff item 2) | Answered on 2026-11-03, in the coder session that recorded E77 and E78. The marker observer reads session tokens from the real /proc only behind an explicit flag, `--record-session-tokens`, which is off by default, so the approved G3 run is unchanged. Using the flag on Frame is a separate approval. |

## Architecture decisions (2026-11-07)

The owner accepted these eight decisions on 2026-11-07 from a decision memo,
and three independent code reviews then checked them against revision
`45771fe`. The [dated council record](history/2026-11-07-architecture-council.md)
preserves the memo, the reviews and the reasoning. The decisions change no
gate, add no evidence and grant no device permission. The
[planned structure](architecture.md#planned-structure-2026-11-07) describes the
resulting design; the
[work order](live-capture.md#architecture-plan-work-order-2026-11-07) owns the
sequence and its state.

| Topic | Decision |
| --- | --- |
| D1. End state of this phase | The fixed edge HUD is the product of this phase. Pose logging and projection stay out of the plan. The native tracking reporter is a diagnostic behind its explicit flag, integrated only because mapping qualification needs its tracking tokens; it ranks below the HUD path. |
| D2. Input strategy and exit rule | The SteamVR mirror with a full-footprint mask stays the input. Further marker observation (G3) attempts have a budget: a file-only still analysis of the planned view first; one attempt on a checkout containing package O3; if that stops at the scene precondition, one statistics-led limit revision and at most one more attempt. If G3 is still open after the budget, the project switches to the fallback design (time-multiplexed exclusion: the HUD is hidden during each capture window) instead of tuning further. The fallback is drafted and costed now and built only then. |
| D3. Qualification depth | Two tiers, each a reviewed change to `sightcircuit/gates.py` and its call sites, never a flag or configuration value. A **diagnostic tier** governs the 60-second acceptance run: one pinned runtime and session, fixed HUD geometry, no recenter, monitored motion and display states, a mask that is the union of every observed marker copy plus the error-budget margin (so eye identity is not required), a one-run scope bound to digests and an absolute deadline, and a bounded duration. Full G4, as written in the [qualification method](hud-qualification.md#proposed-g4-method), remains the requirement for any production use beyond that run. |
| D4. Lifecycle and process topology | One supervision primitive in `sightcircuit/runtime`, adopted in order: the agent runner (its own review), the live pipeline and mirror launcher, then the paused capture supervisor. The Python side owns escalation and the cleanup verdict; the bridge may end itself on its watchdog or parent death but supervises nothing else. The bridge source is split into lifecycle, rendering, reporting and sensing units before the tracking reporter is integrated. |
| D5. Packaging and entry points (item 5) | Hashed lock files generated from the existing pins, one per requirement set; console entry points for the active commands, with the script files kept as thin wrappers so documented commands keep working; the paused capture-provider scripts stay quarantined in place. |
| D6. Detector default and evaluation set | No model change before the representative set exists: at least twenty boxed native stills on the Frame covering the chair at several distances and lighting, annotated and scored file-only on the device, with only JSON results and digests leaving it. The default-detector rule is chair recall at the POC threshold with Frame inference time inside the live budget. WP1 closes by attribution after this evaluation if no cause is found, and the record says so. |
| D7. The agent runner's place | `sightcircuit/agent` stays in the package as operations tooling outside the production gates, documented as such, with its own evidence path and its own group in the checkpoint manifest. |
| D2 refinement, A5 scope (2026-11-07, evening) | The owner accepted that the one reviewed change the D2 budget allows before the last G3 attempt is a change to the marker or its tag decode for the scale the mirror gives, since the first counted attempt failed at the tag decode and not at the scene precondition ([E89](evidence/e089.md)). The scene-limit revision D2 named is not needed; the budget stays at one remaining attempt. |
| D2 exit reached, fallback chosen (2026-11-08, night) | The last budgeted G3 attempt ran: the reviewed decode change worked and the run was rejected on marker movement under head motion ([E90](evidence/e090.md)). The owner chose to follow D2 as written ("Yes, fallback"): the project switches to the time-multiplexed exclusion design ([fallback reference](reference/hud-fallback.md), work F1 to F8) and makes no further marker-observation attempt. The mask, mapping and qualification path (A7 to A10, gates G3 and G4 as the production route) is superseded for this phase; its code stays until F8 retires or quarantines it after an accepted fallback run. The same evening the owner approved the evaluation session C5 with C6 and asked for the Phase 2 guard-enforcement test to be scheduled (scope written in the cloud first, the run under its own approval). |
| SDK camera-input lane opened, cloud-only (2026-11-08, night) | The owner accepted the Arcturus Vision Camera Preview SDK agreement (version 2026-11-08) and obtained the SDK, confirmed 2026-11-09; its two public headers and the agreement are vendored with pinned checksums (#54), the client library is never committed, fetched or linked in CI, and the [dated input review](history/2026-11-09-arcturus-sdk-input-review.md) records what the SDK provides and the owner decisions it raises. The owner opened the SDK camera-input lane cloud-only ("Yes, open it cloud-only"): containment and a fake backend, metadata-only probe modes and an operator procedure, with the owner's own session's implementation adopted as the lane's code ("Yes, adopt my branch"). D2 is unchanged: the mirror stays the input until the lane's Phase B probe answers the passthrough question, and F4 to F8 are held until then ("Yes, hold F4 to F8"). Device probes (Phase A, Phase B) each need a separate approval after the staged F3 and C5/C6 sittings and never in the same session as them; any passthrough disturbance stops the SDK track. |
| D8. Tests and CI | The current CI layout stays. The load-dependent cancellation test is root-caused inside the D4 consolidation and never skipped, suppressed or quarantined. The suite gets a documented time budget. |

Coordinator's calls made under these decisions, after the council:

| Topic | Call |
| --- | --- |
| Signal policy | The primitive offers both signal modes (raise on signal, and record-and-poll) and takes the inherited-ignore policy as a parameter. The runner keeps honouring an inherited ignore; the suite entry normalises SIGINT for its children. |
| Progress streaming | Part of the primitive. `runtime/process.py` retired with B5 (#27) once the primitive streamed progress; the mirror launcher and the synthetic run supervise through the primitive. |
| Tracking tokens | They stay required, as decided on 2026-11-03, so the reporter integration precedes the diagnostic run and follows the bridge split. |
| Recall and budget | Chair recall is per-box at IoU 0.5 on the boxed set. The live budget is the inference p95 from a `mirror_recognize` run on the Frame. Both become one named constant each in code; the budget's number is the owner's: at most 200 ms, accepted on 2026-11-07 (`POC_THRESHOLD` and `LIVE_INFERENCE_P95_BUDGET_MS` in `sightcircuit/detection.py`; [reference](reference/detection.md#poc-threshold-and-live-budget)). |
| Locks | One hashed lock per requirement set, carrying both architectures' hashes, verified on the Frame when its setup next runs. |
| Active entry points | The eight named commands plus `mirror_check` and `frame_session_manifest` (ten at P2); `annotate_boxes` joined with C3b (#30, 2026-11-08), so eleven; `agent_enforcement_check` with B10 (2026-11-08), so twelve; `fallback_measure` with F3b (2026-11-08), so thirteen. |
| Attempt budget counting | An attempt counts against the D2 budget when the observer judged the scene: a rejection at the scene precondition counts, and anything after the marker tile appears counts. A stop caused by connectivity or preparation does not count whatever its position in the run: a missing bridge build, a failed manifest verification, headset not connected, the connect watchdog, the marker ready timeout, or a vrserver session stop. Refined on 2026-11-07 after the A3 review (finding F1). |
| Race fixed | Fifty consecutive green runs of the cancellation test under CPU stress on the cloud runner, then one green ARM64 run on main. |
| Fallback measurements (F3) | Made with the calibration marker in the bridge's marker mode, extended to accept the visibility opcode (F3a), and read from the mirror with the marker decoder; no stream-mode HUD output and no gate change. The design's "high-contrast test tile" is the marker, the one tile the device is approved to show (2026-11-08). |

## Upstream reuse plan (2026-10-31)

The [dated upstream review](history/poc-scope-2026-11-07.md#upstream-reuse-plan-2026-10-31)
preserves ranked sources, licenses, pins and adoption criteria. It adds no
integrated dependency or device compatibility claim. Pose history, projection
and smoothing remain deferred with world-locked labels; lifecycle and
measurement references remain relevant to WP10/WP12. Optional assistant,
screenshot and recovery experiments remain separate from acceptance.

## Open questions for planners

Authority, arrow tolerance, initial object and strip position are answered in
the table above. Whether the replay panel remains a fallback display and
whether world-locked labels return after acceptance remain future product
questions. They do not block this documentation work or change current scope.

## Capture supervisor fix (2026-11-04)

The approved two-race supervisor fix is merged as PR #24. It preserves
publication identity during cancellation and contains a worker started during
cancellation. This is an offline fix in the paused provider track, not a provider
restart or device result. See the [implementation reference](development.md#worker-containment-and-publication-recovery).

## Earlier sections

The complete earlier text is preserved in a [dated snapshot](history/poc-scope-2026-11-07.md).
These links retain previous section addresses; use the current sections above for guidance.

<details>
<summary>Historical section links</summary>

<a id="poc-scope-fixed-edge-hud-on-live-mirror-input"></a>
- [POC scope: fixed edge HUD on live mirror input](history/poc-scope-2026-11-07.md#poc-scope-fixed-edge-hud-on-live-mirror-input)

<a id="rules-kept-and-changed"></a>
- [Rules kept and changed](history/poc-scope-2026-11-07.md#rules-kept-and-changed)

<a id="execution-checkpoint-2026-10-31"></a>
- [Execution checkpoint (2026-10-31)](history/poc-scope-2026-11-07.md#execution-checkpoint-2026-10-31)

<a id="offline-handoff-delivery-2026-11-02"></a>
- [Offline handoff delivery (2026-11-02)](history/poc-scope-2026-11-07.md#offline-handoff-delivery-2026-11-02)

<a id="priorities-for-the-current-poc"></a>
- [Priorities for the current POC](history/poc-scope-2026-11-07.md#priorities-for-the-current-poc)

<a id="adoption-order-and-evidence"></a>
- [Adoption order and evidence](history/poc-scope-2026-11-07.md#adoption-order-and-evidence)

<a id="later-work-and-frozen-provider-references"></a>
- [Later work and frozen-provider references](history/poc-scope-2026-11-07.md#later-work-and-frozen-provider-references)

<a id="reviewed-source-revisions"></a>
- [Reviewed source revisions](history/poc-scope-2026-11-07.md#reviewed-source-revisions)

</details>
