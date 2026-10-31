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

- One coordinator allocates evidence IDs (E72 onward) and is the only editor of
  the `status.md` matrix; coders put evidence in their PR description and the
  coordinator records it. Parallel branches reused E50, E52 and E54–E62; those
  results are now recorded as E63–E71.
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
| 9 | **HUD mapping calibration.** A bounded run in which the HUD marker draws the fixed opaque footprint and the empirical observer finds its copies at native mirror resolution, per eye, with their motion and margins. Includes the first Frame run of the observer and marker. Procedure: [bounded edge HUD calibration checkpoint](on-device.md#bounded-edge-hud-calibration-checkpoint). | Yes, wearer | A versioned mapping and mask margin derived from observed copies on Frame, with normal passthrough confirmed. A clean observer result on its own stays `observed_unqualified` |
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

## Execution checkpoint (2026-10-31)

[PR #10](https://github.com/moral-kombat/framejacker/pull/10) is merged.
The implementation baseline reviewed for this checkpoint is
`85cf53cd99bb0abfc176b72d279a46dbec52ba4f`. **WP1, WP2, WP7, WP9, WP10,
WP11 and WP12 remain open**; WP3–WP6 and WP8 are deferred. Open means the
package's acceptance is not yet evidenced, not that supporting code is absent.

| WP | Current position | Work that can advance offline | Remaining acceptance |
| --- | --- | --- | --- |
| 1 | E61 miss unresolved | Reproducible preprocessing audit and focused defect checks using approved available inputs | Original still/digest access and an evidenced defect fix or explanation |
| 2 | Representative Frame evaluation outstanding | Existing evaluator, manifests and reporting; approved input evaluation | Representative factual labels and evidence for a detector-default decision |
| 7 | Documentation repaired in #10; package still open | Per-entry evidence files, generated matrix, concise README and consistent scope pointers | Generated matrix and README/evidence-structure criteria met |
| 9 | Marker/observer implemented offline; Frame observation not run | Versioned mapping/evidence schema, rejection tests and a proposed G4 qualification method | Frame observation plus the required mapping evidence; observer success alone stays unqualified |
| 10 | V4 root cause unresolved; reliable cleanup/recovery unqualified | Reconcile runtime/overlay contracts, independent expiry, guardian and truthful reporting; investigate available evidence | Explained failure, regression coverage and reports consistent with physical recovery |
| 11 | Device feedback exclusion and physical expiry not run | Native-mask/expiry integration tests and bounded validation instrumentation | Qualified mapping, no HUD feedback during head motion and physical expiry evidence |
| 12 | Synthetic loop exists; production live HUD remains closed | Shared runtime integration, native-geometry handling, measurements and validation-only end-to-end checks | Combined 60-second headset acceptance run |

[G1 passes offline; G2 is open; G3, G5 and G6 have not run; G4 has not
started](live-capture.md#edge-hud-acceptance-gates). The G4 qualification method
is still undefined. The critical path is **WP10 → WP9 observation → G4
qualification → WP11 → WP12**. WP1/WP2 and WP7 can advance alongside it.
Offline completion does not close a package whose acceptance requires Frame.

Use the [execution prompt](execution-prompt.md) to complete all unblocked
implementation, validation and documentation work. It identifies source-level
integration gaps without treating them as proven causes of V4, preserves the
production gates, and requires a concrete next checkpoint after the offline
work is finished. This status refresh records no new device result or WP
closure.

## Upstream reuse plan (2026-10-31)

Source review identified the references below for accelerating the existing
work packages. These are **planned reuse/evaluation tasks**, not integrated
dependencies or new Frame compatibility results. The current work order,
WP1/WP2 image audit and evaluation, and existing acceptance criteria still apply.

Scope: this page now targets the fixed edge HUD (revised 2026-10-31), and
WP3–WP6 and WP8 are [deferred](#deferred-work-packages). Priorities 1, 2 and 5
below therefore apply only if world-locked labels return. Priority 3 applies to
the HUD now, through WP10 lifecycle review and WP12 measurement. Priority 4
stays an optional follow-up. This research addition does not authorize a
device operation.

### Priorities for the current POC

| Priority | Reference and license | Work package | Planned reuse and completion check | Important limit |
| --- | --- | --- | --- | --- |
| 1 | **Frametop**: [pose history][frametop-pose] and [absolute overlays][frametop-overlay]. MIT. | WP3, WP4, WP5 (deferred) | Adapt a bounded timestamped pose history and declared tracking origin to the existing pipeline/bridge. Preserve frame identity and pose age; test transforms, invalid tracking and origin changes with synthetic data before device qualification. | Its nearest-pose lookup is a reference pattern. Mirror observation time is not verified exposure time; clock domains, image/pose offset, field of view and crop still need validation. |
| 2 | **One Euro Filter**: [Python implementation][oneeuro] and [license][oneeuro-license]. BSD-3-Clause for this implementation. | WP6 (deferred) | Evaluate timestamp-aware smoothing after projection into the tracked frame. Compare angular jitter and lag on the same replay/synthetic inputs; reset on source/origin changes. | Smoothing does not supply object identity, depth or temporal alignment. Association, bounded dropout handling and stale-result expiry remain separate. |
| 3 | **frame-perf-overlay**: [Vulkan texture upload][perf-texture], [overlay lifecycle][perf-overlay] and [placement][perf-placement]. MIT. | WP10, WP12 (previously WP5, WP8) | Review tracking-validity and cleanup handling; plan compositor frame-time, CPU/GPU, thermal and input-age measurements. Consider a texture-upload change only if profiling justifies it. | Upstream panels are head/controller attached, not world-locked object labels. Its upload fence covers its own texture work, not Arcturus producer access. |
| 4 | **Frame Control**: [native screenshot helper][frame-shot] and [runtime observations][frame-runtime]. MIT. | Optional follow-up input adapter | Reuse the small screenshot path for requested vision-lab stills and diagnostics, retaining source/frame ID and crop metadata. Keep it outside the WP9-WP12 critical path. | Screenshots are composited and can contain our output. They do not establish raw-camera access, exposure timing, calibration or feedback exclusion. |
| 5 | **WayVR**: [absolute transforms][wayvr-overlay] and [tracking origin/recenter handling][wayvr-origin]. GPL-3.0. | WP5 design reference (deferred) | Review origin and recenter cases for the existing overlay bridge. Prefer the MIT Frametop implementation for source reuse; retain WayVR as a reference or optional separate tool. | Steam Frame support is experimental. A world-space overlay alone does not establish calibrated camera-pixel-to-object alignment. |

Frametop's [camera broker notes][frametop-hands] are useful diagnostics research,
but its current colour path rejects problematic frames while the headset is
worn and its colour calibration remains unresolved. Do not treat that broker as
a qualified replacement for Arcturus RGB input. Its [gaze subsystem][frametop-gaze]
is a later reference for selecting an object or crop, not a prerequisite for the
first label.

### Adoption order and evidence

- [ ] Keep WP1's reproducible E61 still/preprocessing audit and WP2's representative
  evaluation ahead of any detector-default decision. MobileNet-SSD and
  YOLOX-Nano already exist; another fixed-class detector is not required by this
  reuse plan.
- [ ] Deferred with world-locked labels: prepare the Frametop-inspired pose-history/projection work and the One Euro
  Filter evaluation offline, following the existing WP dependencies. Explicitly
  record clock domain, observation time, pose sample time, age and unknown
  image-to-pose offset; never relabel observation time as exposure time.
- [ ] Deferred with world-locked labels: extend the existing overlay bridge with the WP5 origin/recenter behavior.
  Reconcile the lifecycle safeguards in the current handoff before proposing
  output. A future authorized test first checks a fixed world marker, then
  drives it from one detection; assess rotation, translation and recenter
  separately. Estimated distance remains visibly identified as an estimate.
- [ ] Plan WP12 measurement and WP11 feedback exclusion together: retain the overlay-masking
  requirement and qualify the mapping before live use; preserve matching frame/result
  identity, latest-frame behavior, expiry,
  independent abort and truthful cleanup reporting. Report input age and total
  processing time separately from model inference time.
- [ ] Before importing code or adding a dependency, record the selected immutable
  revision, preserve applicable notices and update [third-party attribution](../THIRD_PARTY.md).
  Pin model weights, preprocessing and runtime separately. Validate the smallest
  reused component rather than installing an entire upstream application.

These checkboxes add no new capture, private-image transfer, server exposure,
source switching, runtime restart or headset-test permission. Device work
continues through the [current handoff](live-capture.md#october-26-evening-handoff);
research, offline checks and wearer-confirmed results remain distinct.

### Later work and frozen-provider references

| Reference | Planned use / entry point | When to reconsider | Limit |
| --- | --- | --- | --- |
| **Moondream 2** ([model and native query/detect/point examples][moondream2], revision `2025-06-21`, Apache-2.0) | Evaluate on-demand questions, text-described objects and pointing on a selected image/crop on a companion computer. Carry source-frame ID and ROI transform through each answer. | After the current POC, or as a separately scoped vision-lab experiment with approved input. | No measured Steam Frame throughput or accuracy. Do not silently substitute another model/version or its licensing terms. |
| **llama.cpp** ([multimodal server][llama-multimodal], MIT runtime; model terms separate) | Optional local HTTP image-question backend for the vision lab. | When selecting the assistant's inference service. | General multimodal support does not establish Moondream's specialized detection/pointing methods. It does not solve capture. |
| **Steam Link Native Passthrough** ([frame-continuity design][native-continuity], MIT) | Study exact buffer identity, generations, revocable leases, GPU retirement and reset/requeue behavior. | Only when a supported vendor interface or a real producer-synchronization answer permits provider work to resume. | Its controlled leases concern decoded streamed images/masks, not exported Arcturus camera pixels. The provider stays frozen. |
| **Frame Passthrough Shortcuts** ([source][passthrough-shortcuts]) and **Frame Autopass** ([source][autopass]), MIT | Reference for a future explicit RGB/native/RGB recovery action, source-state reporting and compatibility checks. | A separately scoped recovery investigation after the current lifecycle failure is understood. | Internal SteamVR interfaces; the observed toggle is a workaround, not a green-frame root-cause fix. Automatic switching is not part of this plan. |

The [existing Spyjacob capture review](capture-reference-review.md) and
[recorded source attribution](../THIRD_PARTY.md#mrhand-protocol-header) remain the
baseline for that already evaluated reference. Its readiness heuristic does not
supply coordinated producer ownership. Arcturus's [developer page][arcturus-dev],
reviewed 2026-10-31, still states that no direct camera-access API is available;
its passthrough-blending examples do not give applications camera pixels.
Dedicated capture, stereo depth and Gaussian splats remain deferred.

### Reviewed source revisions

The source links below pin the repository revisions checked on 2026-10-31.
They identify the reviewed reference, not an adopted dependency version.
The Moondream model revision is listed separately above; vendor documentation
is linked with its review date.

[frametop-pose]: https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/gaze/ft-gaze.cpp
[frametop-overlay]: https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/screens/vr.cpp
[frametop-hands]: https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/hands/README.md
[frametop-gaze]: https://github.com/Frametop/frametop/blob/adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c/gaze/README.md
[oneeuro]: https://github.com/casiez/OneEuroFilter/tree/d78925584245597f2aa9c4c01a802eb0f0b77fb9/python
[oneeuro-license]: https://github.com/casiez/OneEuroFilter/blob/d78925584245597f2aa9c4c01a802eb0f0b77fb9/python/LICENSE
[perf-texture]: https://github.com/sasaken1102r/frame-perf-overlay/blob/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94/headset/vk_texture.cpp
[perf-overlay]: https://github.com/sasaken1102r/frame-perf-overlay/blob/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94/headset/vr_overlay.cpp
[perf-placement]: https://github.com/sasaken1102r/frame-perf-overlay/blob/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94/headset/placement.cpp
[frame-shot]: https://github.com/saphid/frame-control/blob/4e1f664be47b99fcf88c84d1280126c39d6753b4/ui/frame_vrshot.py
[frame-runtime]: https://github.com/saphid/frame-control/blob/4e1f664be47b99fcf88c84d1280126c39d6753b4/docs/how-the-frame-works.md
[wayvr-overlay]: https://github.com/wayvr-org/wayvr/blob/ef7e5f61ebb962b2385b88a85033c48a167a046e/wayvr/src/backend/openvr/overlay.rs
[wayvr-origin]: https://github.com/wayvr-org/wayvr/blob/ef7e5f61ebb962b2385b88a85033c48a167a046e/wayvr/src/backend/openvr/playspace.rs
[moondream2]: https://huggingface.co/vikhyatk/moondream2
[llama-multimodal]: https://github.com/ggml-org/llama.cpp/blob/0ec4027a2a0183de87c007069adddc06e665f7de/docs/multimodal.md
[native-continuity]: https://github.com/McFredward/steam-link-native-passthrough/blob/5a5a90686fbed4f64ddb2e790c51116086cb40c9/docs/development/FRAME-CONTINUITY.md
[passthrough-shortcuts]: https://github.com/KominoVR/frame-passthrough-shortcuts/tree/032841dcde61059b25d33a487bf202a30fbf58f1
[autopass]: https://github.com/bod09/frame-autopass/tree/e580577ab1a95c546e018ccce353a9c0f57b7514
[arcturus-dev]: https://arcturus.vision/developers

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
