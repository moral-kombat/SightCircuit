# POC scope: fixed edge HUD on live mirror input

Decision record for planners. First recorded 2026-10-27 with a world-locked
label as the target; revised 2026-10-31 to the
[fixed edge HUD](architecture.md#fixed-edge-hud-decision). [Status](status.md)
owns evidence; this page owns scope and work packages. It narrows the goal in
[live capture](live-capture.md#current-work-order) and does not authorize any
device operation: every device step below needs the approval the October 26
handoff requires.

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

## Rules kept and changed

Keep: cloud, CI and headset results recorded separately; bounded runs with
wearer confirmation of normal passthrough; no repeat of failed unchanged tests;
no threshold or model changes to make one image pass; skipped tests fail CI.

Change:

- One coordinator allocates evidence IDs (E63 onward) and is the only editor of
  the `status.md` matrix; coders put evidence in their PR description and the
  coordinator records it. Parallel branches reused E54, and later docs link
  E50, E52, E55 and E60–E62 to entries `status.md` does not contain.
- Provider-track changes are rejected unless a vendor interface or a
  synchronization answer exists.
- New evidence goes in its own file under `docs/evidence/` (WP7); do not grow
  `status.md`.

## Constraints that shape the design

- The mirror includes our own VR panel (E18). Mask the **full HUD footprint**
  (background, text, arrows and edge effects) at native mirror resolution
  before any resizing, filtering or inference, with a calibrated conservative
  margin and the union of both eye footprints where stereo composition
  applies. Masking only detected text or a resized image does not establish
  exclusion, and dimensions alone do not identify the footprint (handoff
  item 5).
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
- The V4 cleanup failure and the Arcturus green-input recurrence (E59) are
  unresolved; lifecycle safeguards must be reconciled before any live output.

## Work packages

| WP | Work | Needs device | Done when |
| --- | --- | --- | --- |
| 1 | **Preprocessing audit of the E61 miss.** Reproduce the one-still result file-only, pin hashes, compare grayscale/channel replication, SSD stretch vs YOLOX letterbox and box mapping, one change at a time (live-capture handoff items 1-3). | No (needs approved access to the still) | A defect is found and fixed with a synthetic regression test, or the miss is attributed to the image/model with evidence |
| 2 | **Evaluation set.** Approved representative Frame stills with factual labels, including the rug/sofa case; absent labels and boxes added only after inspection. | Capture needs approval | `evaluate_detectors.py` reports on Frame stills; default detector chosen from it |
| 7 | **Docs weight.** Per-entry evidence files, generated status matrix, one-screen README. | No | README fits one screen; status matrix generated |
| 9 | **HUD mapping calibration.** A bounded run in which the HUD marker draws the fixed opaque footprint and the empirical observer finds its copies at native mirror resolution, per eye, with their motion and margins. Includes the first Frame run of the ARM64 observer build. The operator procedure is not yet in [on-device](on-device.md). | Yes, wearer | A versioned mapping and mask margin derived from observed copies on Frame, with normal passthrough confirmed. A clean observer result on its own stays `observed_unqualified` |
| 10 | **Lifecycle and cleanup safeguards.** Explain the V4 cleanup failure; reconcile HUD masking, calibration expiry, the independent guardian and truthful early-termination reporting with merged `main` (live-capture handoff item 4). | No for analysis; device to confirm | Root cause recorded, tests cover the failure path, and cleanup reports agree with physical recovery |
| 11 | **Feedback exclusion check.** Show the HUD on device with the qualified mask and confirm the detector never reports the HUD's own text or arrows, including during head motion. | Yes, wearer | A bounded run with the HUD visible, no detections from the masked footprint, and normal passthrough |
| 12 | **Live HUD loop.** `MirrorSource` into `live_pipeline.py` with the native mask, detector, HUD tile and stale expiry, behind explicit opt-in and an independent abort path. Live mirror HUD input is rejected before opening today; lift that only for a qualified mapping. | Yes, wearer | See acceptance |

Order: 1, 7 and 10 start now and are independent. 9 needs one approved device
run and gates 11. 12 needs 9, 10 and 11. 2 is needed before any claim about
detection quality.

### Deferred work packages

These served the earlier world-locked target and return only if world-locked
labels are taken up again after the HUD POC is accepted.

| WP | Work |
| --- | --- |
| 3 | Pose and timestamp logging: a bounded, read-only OpenVR pose logger beside the mirror reader |
| 4 | Projection math: pixel to ray in the head frame, then to the tracked frame using a pose |
| 5 | World-locked overlay mode with recentering in the overlay bridge and client |
| 6 | Tracker and smoothing across frames for a stable world-locked direction |
| 8 | World-locked live loop: mirror input, projection, tracker and world-locked output |

## Acceptance

One real object named in the HUD for 60 seconds while the wearer turns their
head, with its arrow pointing within a tolerance the wearer agrees to before
the run; no detections from the HUD's own footprint; the name expiring when
the object leaves view; normal passthrough and tracking during and after; a
clean shutdown with truthful cleanup reporting; and the report recorded as a
numbered evidence entry separate from cloud results. Detection accuracy beyond
that object, and any world position, are not claimed.

## Open questions for planners

- Who authorizes device runs, and is the HUD calibration (WP9) approved first?
- Acceptable arrow tolerance, and which edge of the view holds the strip.
- Which object for the POC (the rug has no class in either model; choose a
  class both models support, or accept a detector change).
- Whether the head-locked replay panel stays as a fallback display.
- Whether world-locked labels return after acceptance.
