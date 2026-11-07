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

- One coordinator allocates evidence IDs (E79 onward; E76–E78 record #17, #16 and #18) and is the only editor of
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
- V4's software stop and cleanup fields are explained by recovered, verified
  evidence ([E75](evidence/e075.md)). Its green Arcturus outcome and the later
  recurrence remain unexplained; physical recovery is still required before
  any live output.

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
`5237c95e58373931eff7c8a4c307fe79bdd8acac`. At the prepared baseline,
**WP1, WP2, WP7, WP9, WP10, WP11 and WP12 remained open**; WP3–WP6 and WP8 were deferred. The implementation rows below include
subsequent offline support; final evidence and acceptance live in status. Open
means the package's acceptance is not yet evidenced, not that supporting code
is absent.

| WP | Current position | Work that can advance offline | Remaining acceptance |
| --- | --- | --- | --- |
| 1 | Audit implemented; two independent preprocessing/scoring defects fixed; the E61 miss reproduced file-only on Frame with its digest verified, variants indistinguishable on that image ([E80](evidence/e080.md)) | Pinned source/model/runtime tensors and controlled variants; [E73](evidence/e073.md) | An explanation of the miss, or attribution to the image/model with evidence; the 2026-11-03 re-run reproduces it without explaining it |
| 2 | Five unboxed Frame stills captured and scored ([E81](evidence/e081.md)); POC object switched to chair | Shared evaluator distinguishes factual presence/absence/boxes; [E73](evidence/e073.md) | Inspected boxes and more representative stills before a detector-default decision; five presence-only stills are not enough |
| 7 | Complete offline | 75 per-entry records, generated matrix, concise README, preserved history and checked links; [E74](evidence/e074.md), [E75](evidence/e075.md) | Maintenance only; no device requirement |
| 9 | Schema/G4 design verified offline; historical V4 device attempt rejected; the 2026-11-03 attempt stopped at the observer's contour budget before any marker was shown ([E83](evidence/e083.md)) | Exact context/lifetime/margin/provenance checks; [G4 method](hud-qualification.md), [V4 evidence](evidence/e075.md); a scene precondition that measures marker-detection risk rather than raw bright blobs, with diagnostics in the rejection report | A new approval after the precondition change, then an accepted bounded observation, then eye/composition/all-copy/motion-limit evidence and reviewed mapping |
| 10 | G2 closed on 2026-11-03: software termination explained ([E75](evidence/e075.md)), saved logs analyzed without finding the cause of the green input, recovery procedure rehearsed by the owner ([E79](evidence/e079.md)) | Verified original report, ten matched source/test files and termination timeline; [E75](evidence/e075.md); shared safeguards [E72](evidence/e072.md) | None for the gate; the cause of the green input stays unknown and any green input aborts a run |
| 11 | Feedback/expiry telemetry and default-off diagnostic design ready offline | Native-mask tests, bounded validation ledger and report template; [E74](evidence/e074.md) | Reviewed diagnostic implementation, qualified mapping, head-motion exclusion and physical expiry evidence |
| 12 | Shared bounded synthetic loop verified; production closed | Native padded masking, identity/context/age checks and native validation; [E72](evidence/e072.md) | G2–G5, representative detector evidence and combined 60-second headset acceptance |

[G1 passes offline; G2 closed on 2026-11-03; G3 has a rejected historical
attempt and one approved run pending; G5/G6 have not run; G4 remains
unqualified](live-capture.md#edge-hud-acceptance-gates). A
[proposed G4 qualification method](hud-qualification.md#proposed-g4-method) now
defines the missing evidence; proposal/schema validation is not qualification.
The critical path is now **WP9 observation → G4 qualification → WP11 → WP12**.
WP1/WP2 and WP7 can advance alongside it.
Offline completion does not close a package whose acceptance requires Frame.

Use the [execution prompt](execution-prompt.md) to complete all unblocked
implementation, validation and documentation work. It identifies source-level
integration gaps without treating them as proven causes of V4, preserves the
production gates, and requires a concrete next checkpoint after the offline
work is finished. This delivery records no new device result or device-dependent
WP closure. WP7 is complete offline.

The recovered [V4 record](evidence/e075.md) closes the original-report and
source-identity gaps. The next WP10 evidence action is inspection of any
already-saved Arcturus/camera/compositor logs and exact runtime versions for
October 24 around 22:48 EDT. Tag/bit mismatch and green passthrough causes
remain unknown. G2 must be resolved before another marker/output proposal;
no new device run is authorized by this plan update.

### Offline handoff delivery (2026-11-02)

Packages A–H of the [2026-11-01 offline handoff](execution-prompt.md#offline-handoff-2026-11-01)
are implemented and verified offline ([E76](evidence/e076.md)). No device result
is implied, and no device-dependent work package closed.

- **WP2:** the `frame_session.sh` stills stage works again in cloud tests
  (package A), and stills carry pinned digests.
- **WP1 and WP2:** the evaluator gained a mirror-preview variant, per-label
  diagnostics and a CI seed pin (package G).
- **WP10 and G2 software:** a rejected marker run now stops without a forced
  termination, and a killed guardian's worker group is contained (package B).
- **G3 prerequisites:** one generated Python/C++ HUD contract (package D) and
  the checkpoint artifact manifest (package C).
- **WP9, WP11 and G4/G5 offline support:** the observer report converts to an
  unqualified candidate, calibrations come only from factories with expiry and
  session tokens, and exclusion counters feed the validation ledger in
  synthetic runs (package E).
- **WP12 measurement:** latency percentiles and a runner-CPU stand-in job
  (package H). Package F consolidated the live runner and the mirror launcher.

Decided on 2026-11-03: on generated frames, the default detector boxed the
zero-filled HUD mask as `tvmonitor`, so a synthetic validation run with the
real detector failed under the strict G5 rule. The owner chose to fill the
mask differently and keep the rule. The mask now refills its zeroed region
from the surround, and that run passes; this is a cloud result on generated
frames, not a Frame result ([E78](evidence/e078.md)). See the
[neutral mask fill](hud-qualification.md#neutral-mask-fill-2026-11-03).
Each device step these packages unlock still needs its own approval. The
handoff lists them.

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

## Owner decisions (2026-11-03)

The owner answered these in a decision interview; the rows after "Frame agent"
were answered later the same day. Each device step still needs
the owner present at the time it runs. None of these decisions is evidence, and
none opens a gate in `sightcircuit/gates.py`.

| Topic | Decision |
| --- | --- |
| Who authorizes device runs | The owner. Each step below is approved once; repeating a step, or changing it, needs a new approval. |
| G2 without a root cause | If the saved logs do not explain the green input, G2 may close on a written recovery procedure. The owner rehearses that procedure on the headset first, and any green input aborts the run. Unexplained physical failures remain recorded as unknown. |
| G2 log analysis | Approved: read-only analysis of the saved green-event logs on Frame, written up as an evidence draft for the coordinator. |
| WP9 / G3 | One bounded marker-observer run with the owner wearing the headset, approved for after G2 closes and the recovery rehearsal. It follows the [device checkpoint](hud-qualification.md#next-device-checkpoint) and a verified checkpoint manifest. |
| Catch-up session | One session through `scripts/frame_session.sh`. It covers the E47 replay and VR-panel regression, the E48 mirror regression, the replay pacing measurement, and WP2 stills with the evaluation. The stills include the bottle in view and out of view, the rug, the sofa and a chair. |
| WP1 | If the original E61 still exists on Frame, its digest is checked locally; nothing is uploaded. If it is gone, WP1 is retired as unreproducible and WP2's new stills take its place. |
| POC object | **Chair** (changed on 2026-11-04). The first choice was a bottle, with chair as the fallback if bottle detection proved weak on Frame. In the 2026-11-03 catch-up stills, neither detector reported the bottle above 0.05 in the one still where the owner confirmed it was clearly in view. Both found the chair (MobileNet-SSD 0.98, YOLOX-Nano 0.78), consistent with earlier Frame runs (E17, E47, E48). That is five unboxed stills, so the switch is an owner decision, not a detector evaluation. The catch-up results are not yet recorded as evidence. |
| Arrow tolerance | The arrow is one of eight directions, or a dot near the centre. It must point toward the side of the view where the object is. No bearing accuracy is claimed. |
| Strip edge | Lower right (the current `HudConfig`). Choosing among several named positions comes after acceptance. Each position needs its own qualified mapping, because a mask is bound to its configuration digest. |
| Mask fill (G5, E76) | The coder tests a neutral fill for the masked area offline. The strict overlap rule stays as it is. |
| Frame agent (PR #16) | Approved: install the guarded setup following `docs/agent-on-frame.md` Phase 0. |
| Tracking-token binding (package I) | Answered later on 2026-11-03, in the coder session that recorded E77 and E78. The tokens stay bound when `MirrorSource` is declared: a first `tracking_state` must reach the `TrackingObserver` before the source is created. A state first seen later leaves `tracking_origin` and `recenter_epoch` undeclared for the run, so a bound mask rejects every frame. Late binding was not adopted. |
| Unobserved session token (handoff item 2) | Answered on 2026-11-03, in the coder session that recorded E77 and E78. A session token that the marker observer did not observe rejects the observer-to-candidate conversion (`hud_validation_report.py --from-observer`). There is no declared fallback, behind a flag or otherwise; `--declared-context` keeps only the non-token context fields, and its `native_format` must equal the observer's capture format. |
| Observer /proc reads (handoff item 2) | Answered on 2026-11-03, in the coder session that recorded E77 and E78. The marker observer reads session tokens from the real /proc only behind an explicit flag, `--record-session-tokens`, which is off by default, so the approved G3 run is unchanged. Using the flag on Frame is a separate approval. |

## Capture supervisor fix (2026-11-04)

On 2026-11-04 the owner approved two changes to the paused capture-provider
track, in one small PR. First, the capture supervisor holds SIGINT and SIGTERM
while it records worker output, so a cancellation can no longer leave an
announced publication identity unrecorded. Second, it records a cancellation
that arrives while the worker starts until it holds the worker, so cleanup can
stop and reap it ([details](development.md#worker-containment-and-publication-recovery)).
The track otherwise stays paused. This approves no device run, provider
activation, layer installation or SteamVR restart. The publication race was
reproduced in local cloud runs and on a CI runner; the start-up race was
reproduced in local cloud runs only. Neither fix has a Frame result.
