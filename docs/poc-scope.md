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

## Upstream reuse plan (2026-10-31)

Source review identified the references below for accelerating the existing
work packages. These are **planned reuse/evaluation tasks**, not integrated
dependencies or new Frame compatibility results. The current work order,
WP1/WP2 image audit and evaluation, and existing acceptance criteria still apply.

Scope coordination: [PR #10](https://github.com/moral-kombat/framejacker/pull/10)
separately proposes a fixed edge HUD and defers several world-locked work
packages. The mappings below describe the published plan on this page; update
their active/deferred status if that scope proposal is accepted. This research
addition does not adopt that proposal or authorize a device operation.

### Priorities for the current POC

| Priority | Reference and license | Work package | Planned reuse and completion check | Important limit |
| --- | --- | --- | --- | --- |
| 1 | **Frametop**: [pose history][frametop-pose] and [absolute overlays][frametop-overlay]. MIT. | WP3, WP4, WP5 | Adapt a bounded timestamped pose history and declared tracking origin to the existing pipeline/bridge. Preserve frame identity and pose age; test transforms, invalid tracking and origin changes with synthetic data before device qualification. | Its nearest-pose lookup is a reference pattern. Mirror observation time is not verified exposure time; clock domains, image/pose offset, field of view and crop still need validation. |
| 2 | **One Euro Filter**: [Python implementation][oneeuro] and [license][oneeuro-license]. BSD-3-Clause for this implementation. | WP6 | Evaluate timestamp-aware smoothing after projection into the tracked frame. Compare angular jitter and lag on the same replay/synthetic inputs; reset on source/origin changes. | Smoothing does not supply object identity, depth or temporal alignment. Association, bounded dropout handling and stale-result expiry remain separate. |
| 3 | **frame-perf-overlay**: [Vulkan texture upload][perf-texture], [overlay lifecycle][perf-overlay] and [placement][perf-placement]. MIT. | WP5, WP8 | Review tracking-validity and cleanup handling; plan compositor frame-time, CPU/GPU, thermal and input-age measurements. Consider a texture-upload change only if profiling justifies it. | Upstream panels are head/controller attached, not world-locked object labels. Its upload fence covers its own texture work, not Arcturus producer access. |
| 4 | **Frame Control**: [native screenshot helper][frame-shot] and [runtime observations][frame-runtime]. MIT. | Optional follow-up input adapter | Reuse the small screenshot path for requested vision-lab stills and diagnostics, retaining source/frame ID and crop metadata. Keep it outside the WP3-WP8 critical path. | Screenshots are composited and can contain our output. They do not establish raw-camera access, exposure timing, calibration or feedback exclusion. |
| 5 | **WayVR**: [absolute transforms][wayvr-overlay] and [tracking origin/recenter handling][wayvr-origin]. GPL-3.0. | WP5 design reference | Review origin and recenter cases for the existing overlay bridge. Prefer the MIT Frametop implementation for source reuse; retain WayVR as a reference or optional separate tool. | Steam Frame support is experimental. A world-space overlay alone does not establish calibrated camera-pixel-to-object alignment. |

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
- [ ] Prepare the Frametop-inspired pose-history/projection work and the One Euro
  Filter evaluation offline, following the existing WP dependencies. Explicitly
  record clock domain, observation time, pose sample time, age and unknown
  image-to-pose offset; never relabel observation time as exposure time.
- [ ] Extend the existing overlay bridge with the WP5 origin/recenter behavior.
  Reconcile the lifecycle safeguards in the current handoff before proposing
  output. A future authorized test first checks a fixed world marker, then
  drives it from one detection; assess rotation, translation and recenter
  separately. Estimated distance remains visibly identified as an estimate.
- [ ] Plan WP8 measurement and feedback exclusion together: retain the overlay-masking
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
