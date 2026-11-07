# Current work and acceptance

The current goal is a fixed edge HUD: show object names and approximate
direction arrows from the Steam Frame mirror, while keeping the HUD out of
its own detector input and preserving normal passthrough.

This page owns **what comes next and what must pass**. [Project status](status.md)
owns verified capabilities and evidence; [POC scope](poc-scope.md) owns scope
and accepted decisions; [architecture](architecture.md) explains the design.

## Current work order

The established sequence is lifecycle acceptance → marker observation → mapping
qualification → feedback/expiry validation → the combined live HUD run.
Image auditing and representative detector evaluation can advance alongside it.
Dedicated camera capture, world-locked labels, depth and splats remain deferred.

| Work | Implemented or recorded | Remaining acceptance |
| --- | --- | --- |
| Preprocessing audit (WP1) | Audit tooling and two preprocessing/scoring fixes; [E73](evidence/e073.md) | Inspect the original E61 still and digest if available. The accepted decision retires that reproduction as unreproducible if the original is gone; representative new stills then serve WP2 |
| Detector evaluation (WP2) | Shared evaluator, factual annotation support, pinned seed comparison and native still digests; [E73](evidence/e073.md), [E76](evidence/e076.md) | Representative Frame stills and inspected labels before choosing a detector default or claiming accuracy |
| Lifecycle acceptance (WP10 / G2) | V4 software termination explained; cooperative marker stop and guardian containment tested offline; [E75](evidence/e075.md), [E76](evidence/e076.md) | Reviewed physical recovery evidence. The accepted decision permits a written, owner-rehearsed recovery procedure if the physical cause remains unknown |
| HUD mapping (WP9 / G3–G4) | Marker/observer, candidate validation, session tokens and offline prediction/scene tools | Accepted observation, eye/composition and all-copy evidence, bounded motion/filter uncertainty, and reviewed mapping |
| Feedback and expiry (WP11 / G5) | Native masking, neutral fill, exclusion counters and report design tested offline; [E74](evidence/e074.md), [E78](evidence/e078.md) | Separately reviewed diagnostic, qualified mapping, physical feedback exclusion and expiry evidence |
| Combined live HUD (WP12 / G6) | Shared bounded synthetic pipeline and latency reporting; production gates closed | G2–G5 and the 60-second headset acceptance run |

Documentation work (WP7) previously delivered per-entry evidence and generated
summaries. Its continuing maintenance is described in the
[documentation plan](documentation-plan.md); it does not produce device evidence.

### Critical path

1. Resolve lifecycle acceptance using the recorded physical evidence or the
   accepted recovery-rehearsal alternative.
2. Complete a bounded marker observation, then qualify the complete mapping.
3. Validate feedback exclusion and physical expiry with that mapping.
4. Run the combined live HUD acceptance procedure.

The [HUD checkpoint](guides/hud-checkpoint.md) owns the operator procedure.
The [qualification design](hud-qualification.md) states what an observation
cannot prove and what qualification still requires. Existing commands and
historical one-run approvals are not new device authorization.

### Implementation and evidence reconciliation

The checkout already contains packages A–H, the mirror session tokens and
neutral fill, and observer token recording. The November 6 merges also contain
bridge lifecycle/context-sensor/prediction work (#22), supervisor cancellation
fixes (#24), and observer scene statistics/file analysis (#25). Do not redo
those tasks from the archived handoffs. Their code being present does not
supply device acceptance or fill missing evidence records.

The historical handoff also describes pending device evidence in
[PR #23](https://github.com/moral-kombat/framejacker/pull/23), including claimed
G2 closure, a changed POC object and later observation attempts. That PR remains
open; its E79–E84 work has also been carried into
[SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1), which
is still open. These are pending records, not committed evidence in this checkout.
Keep the accepted state below unchanged until the coordinator reviews and
integrates those records. Preserve unknown dates, revisions and physical causes.

The native tracking reporter remains absent from this checkout. Observer
budget changes still require the scene evidence described in the
[observation reference](reference/hud-observation.md#observer-scene-precondition-2026-11-04).
These existing follow-ups do not change the acceptance order.

## Edge HUD acceptance gates

A gate passes only on its required evidence. Offline tests, ARM64 CI and a
successful process exit are not headset acceptance. A failed device run remains
recorded; investigate it before proposing a changed run.

| Gate | Required evidence | Accepted state in this checkout |
| --- | --- | --- |
| G1: synthetic mask and expiry | Native full-footprint masking, margins/eye union, changed-context rejection and independent stale expiry | Passes offline; [E69](evidence/e069.md), [E74](evidence/e074.md), [E78](evidence/e078.md) |
| G2: lifecycle and cleanup | Explained software termination, truthful cleanup reports, and physical failure/recovery evidence; a written, owner-rehearsed recovery procedure may satisfy the physical part when the cause remains unknown | Open in committed evidence; V4 software account established, physical outcome unresolved; [E75](evidence/e075.md) |
| G3: mapping observed on Frame | Bounded marker/observer report ending `observed_unqualified`, with clean cleanup and normal passthrough confirmed | Historical V4 attempt rejected; no accepted observation in committed evidence |
| G4: mapping qualified | Reviewed evidence for eye composition, all HUD copies, conservative margins, operating limits and exact context | Proposed method; device qualification pending |
| G5: feedback exclusion and physical expiry | HUD visible during head motion with no detections from its footprint; independently observed expiry within the declared bound | Not run in committed evidence |
| G6: combined live HUD | The [60-second POC acceptance](poc-scope.md#acceptance), with the report fields below | Not run in committed evidence |

[`sightcircuit/gates.py`](../sightcircuit/gates.py) is the code's single gate
table. Hardware mapping requires G4; production live image input requires
G4/G5/G6; real HUD output requires G5/G6. All three remain closed. Lifting a gate
requires a reviewed code change after its evidence exists; no configuration
flag or hand-edited candidate opens one. The separately scoped diagnostic in the
[qualification design](hud-qualification.md#separately-scoped-device-validation)
is needed to collect device validation evidence before production is enabled.

### Acceptance evidence

Record the combined run as a numbered evidence entry separate from cloud checks,
using the [device report template](templates/hud-device-validation-report.json):

- Exact revision, source identity, native format, HUD configuration and mapping.
- Native mask regions and zero detections overlapping the masked footprint.
- Frame/result identities and original-frame expiry events.
- Supervisor, worker and native API cleanup, separate from physical recovery.
- p50, p95 and maximum input age, inference, processing and output age, using
  the report's declared units and timebase.
- Wearer observations of the HUD, arrow direction, object-leaving-view expiry,
  and normal passthrough/tracking during and for 30 seconds afterward.

## Risks

The mirror contains our output and has unverified physical-camera identity,
field of view and exposure timing. A mask must cover the complete HUD footprint;
it is not compositor exclusion. Arrows remain approximate.

Detection quality on representative Frame input remains unproven. Both models
missed the expected TV in E61, neither has a rug class, and a successful capture
or preview does not establish accuracy. Physical green-input failures also
remain unexplained in committed evidence.

Third-party reports describe `SetOverlayRaw` freezing after about 200 updates.
This is an unverified compatibility risk for a 60-second run at roughly five
updates per second, not a SightCircuit failure finding. Retain the planned
400-frame replay observation and evidence-led upload decision; the
[dated risk analysis](history/live-capture-2026-11-07.md#risks) preserves source pins.

## Synthetic input and feedback contract review

The offline HUD loop accepts generated input, applies the native mask before
resize, keeps one pending frame, rejects stale/changed context and uses native
validation output only. It rejects real source/HUD use before opening it.
The [HUD runtime reference](reference/hud-runtime.md) owns these contracts;
[development](development.md#offline-foundation-for-the-minimum-live-poc)
owns the runnable checks.

## Choose a frame provider

The existing SteamVR mirror is the selected input for the fixed HUD, conditional
on qualification. If it is unavailable or unsuitable, record the limit and
continue independent offline work and replay. This does not reopen dedicated
capture.

A supported vendor interface is preferred for future dedicated capture. The
experimental metadata-only provider remains paused because it lacks a
coordinated producer handoff. Keep metadata inspection, GPU capability queries
and image ownership distinct. See the [camera reference](steam-frame-camera.md)
and the preserved [synchronization review](history/live-capture-2026-11-07.md#producer-synchronization-review-and-next-checkpoint).

## Vendor interface questions

The useful interface question is whether a supported ARM64 API exposes camera
frames while XRService preserves tracking and passthrough. A usable answer
must cover source identity, pixel/color layout, lifetime, synchronization,
timestamps, calibration, permissions and supported runtime versions. No support
message is sent by this documentation work.

## Earlier sections

The complete earlier text is preserved in a [dated snapshot](history/live-capture-2026-11-07.md).
These links retain previous section addresses; use the current sections above for guidance.

<details>
<summary>Historical section links</summary>

<a id="live-capture-plan"></a>
- [Live capture plan](history/live-capture-2026-11-07.md#live-capture-plan)

<a id="scope-decision-2026-10-27"></a>
- [Scope decision (2026-10-27)](history/live-capture-2026-11-07.md#scope-decision-2026-10-27)

<a id="october-26-evening-handoff"></a>
- [October 26 evening handoff](history/live-capture-2026-11-07.md#october-26-evening-handoff)

<a id="minimum-live-poc-and-october-26-target"></a>
- [Minimum LIVE POC and October 26 target](history/live-capture-2026-11-07.md#minimum-live-poc-and-october-26-target)

<a id="offline-work-order"></a>
- [Offline work order](history/live-capture-2026-11-07.md#offline-work-order)

<a id="next-deliverable-mirror-diagnostic"></a>
- [Next deliverable: mirror diagnostic](history/live-capture-2026-11-07.md#next-deliverable-mirror-diagnostic)

<a id="next-dedicated-camera-deliverable-capture-only-reader"></a>
- [Next dedicated-camera deliverable: capture-only reader](history/live-capture-2026-11-07.md#next-dedicated-camera-deliverable-capture-only-reader)

<a id="offline-provider-work-while-the-frame-is-unavailable"></a>
- [Offline provider work while the Frame is unavailable](history/live-capture-2026-11-07.md#offline-provider-work-while-the-frame-is-unavailable)

<a id="next-engineering-step-capture-worker-supervision"></a>
- [Next engineering step: capture worker supervision](history/live-capture-2026-11-07.md#next-engineering-step-capture-worker-supervision)

<a id="steamvr-service-control-acceptance"></a>
- [SteamVR service-control acceptance](history/live-capture-2026-11-07.md#steamvr-service-control-acceptance)

<a id="producer-synchronization-review-and-next-checkpoint"></a>
- [Producer synchronization review and next checkpoint](history/live-capture-2026-11-07.md#producer-synchronization-review-and-next-checkpoint)

<a id="next-provider-step-query-gpu-capabilities"></a>
- [Next provider step: query GPU capabilities](history/live-capture-2026-11-07.md#next-provider-step-query-gpu-capabilities)

<a id="first-live-device-trial"></a>
- [First live device trial](history/live-capture-2026-11-07.md#first-live-device-trial)

<a id="connect-the-working-application"></a>
- [Connect the working application](history/live-capture-2026-11-07.md#connect-the-working-application)

</details>
