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
