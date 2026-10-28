# POC scope: world-locked label on a detected object

Decision record, 2026-10-27, for planners. [Status](status.md) owns evidence;
this page owns scope and work packages. It narrows the goal in
[live capture](live-capture.md#current-work-order) and does not authorize any
device operation: every device step below needs the approval the October 26
handoff requires.

## Decision

The POC is: **detect an object in live Frame input, convert its position to a
direction in the head-tracked frame, and show a label that stays on that
direction as the wearer moves, without disturbing passthrough.**

Deferred until this works: the dedicated camera provider, stereo depth and
Gaussian splats. The provider track stays frozen (no producer synchronization
exists; live reads are blocked). Depth is replaced by a fixed or size-based
distance estimate, which the label must state is an estimate.

Reasons: the only live source proven on Frame is the SteamVR mirror (E48: 51
reads at 4.993 fps, 40.065 ms mean inference); head-locked panels already work
(E47); the unproven link between them is head pose against frame time, which is
cheap to test and decides whether the POC is feasible.

## Rules kept and changed

Keep: cloud, CI and headset results recorded separately; bounded runs with
wearer confirmation of normal passthrough; no repeat of failed unchanged tests;
no threshold or model changes to make one image pass; skipped tests fail CI.

Change:

- One coordinator allocates evidence IDs (E63 onward) and is the only editor of
  the `status.md` matrix; coders put evidence in their PR description and the
  coordinator records it. Parallel branches reused E54.
- Provider-track changes are rejected unless a vendor interface or a
  synchronization answer exists.
- New evidence goes in its own file under `docs/evidence/` (WP7); do not grow
  `status.md`.

## Constraints that shape the design

- The mirror includes our own VR panel (E18). Labels drawn into the scene would
  be re-detected or feed back; the pipeline must mask or exclude our overlay
  region before inference. Dimensions alone do not identify it (handoff item 5).
- Mirror frames have no pose. Pose and frame time must be logged together.
- 5 fps input needs smoothing so a world-locked label does not jitter.
- The mirror view is composited and its physical camera identity, field of view
  and timing are unknown (E14, E18). The pixel-to-ray mapping is therefore
  approximate until calibrated.

## Work packages

| WP | Work | Needs device | Done when |
| --- | --- | --- | --- |
| 1 | **Preprocessing audit of the E61 miss.** Reproduce the one-still result file-only, pin hashes, compare grayscale/channel replication, SSD stretch vs YOLOX letterbox and box mapping, one change at a time (live-capture handoff items 1-3). | No (needs approved access to the still) | A defect is found and fixed with a synthetic regression test, or the miss is attributed to the image/model with evidence |
| 2 | **Evaluation set.** Approved representative Frame stills with factual labels, including the rug/sofa case; absent labels and boxes added only after inspection. | Capture needs approval | `evaluate_detectors.py` reports on Frame stills; default detector chosen from it |
| 3 | **Pose and timestamp logging.** A bounded, read-only OpenVR pose logger beside the mirror reader, writing pose and a shared monotonic time per frame. No overlay, no layer, no service changes. | Yes, wearer | A report with frame and pose timestamps, measured skew and pose rate, and normal passthrough confirmed |
| 4 | **Projection math (cloud).** Pixel to ray in the head frame using a configurable field of view and mirror crop, then to the tracked frame using a pose. Pure functions in `framejacker/`, synthetic tests including rotation and letterboxing. | No | Unit tests pass; documented assumptions about field of view and distance |
| 5 | **World-locked overlay mode.** Add a world-locked option with recentering to the overlay bridge and client; limits stay in `PANEL_LIMITS`; label layer separate from the image layer. | Test with wearer | Native and Python tests pass; a fixed label stays fixed on device |
| 6 | **Tracker and smoothing.** Associate detections across frames, smooth direction and hold briefly through misses; stale results expire. Replay-driven tests with synthetic pose. | No | Tests cover jitter, dropout and expiry |
| 7 | **Docs weight.** Per-entry evidence files, generated status matrix, one-screen README. | No | README fits one screen; status matrix generated |
| 8 | **Live loop integration.** `MirrorSource` into the pipeline with the overlay-region mask, projection, tracker and world-locked output, behind explicit opt-in and an independent abort path. | Yes, wearer | See acceptance |

Order: 1, 4 and 7 start now and are independent. 3 needs one approved device
run and gates 8. 5 and 6 can run in parallel after 4. 2 is needed before any
claim about detection quality.

## Acceptance

One real object, one stable world-locked label for 60 seconds while the wearer
moves their head, with the label's direction within a tolerance the wearer
agrees to before the run, normal passthrough and tracking during and after, a
clean shutdown with truthful cleanup reporting, and the report recorded as a
numbered evidence entry separate from cloud results. Detection accuracy beyond
that object is not claimed.

## Open questions for planners

- Who authorizes device runs, and which are approved: WP3 first?
- Acceptable direction tolerance for the label (degrees).
- Which object for the POC (the rug has no class in either model; choose a
  class both models support, or accept a detector change).
- Whether the head-locked panel stays as a fallback display.
