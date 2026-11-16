# Current work and acceptance

The current goal is a fixed edge HUD: show object names and approximate
direction arrows from the Steam Frame mirror, while keeping the HUD out of
its own detector input and preserving normal passthrough.

This page owns **what comes next and what must pass**. [Project status](status.md)
owns verified capabilities and evidence; [POC scope](poc-scope.md) owns scope
and accepted decisions; [architecture](architecture.md) explains the design.

## Current work order

Lifecycle acceptance (G2) is closed on the owner-rehearsed recovery procedure.
The remaining sequence is marker observation → mapping qualification →
feedback/expiry validation → the combined live HUD run.
Image auditing and representative detector evaluation can advance alongside it.
Dedicated camera capture, world-locked labels, depth and splats remain deferred.

| Work | Implemented or recorded | Remaining acceptance |
| --- | --- | --- |
| Preprocessing audit (WP1) | Original E61 still found, digest verified and miss reproduced file-only on Frame; [E80](evidence/e080.md) | Explain the miss or attribute it to the image/model with evidence. The audit variants produce identical model inputs on this still, so the re-run does not distinguish a cause |
| Detector evaluation (WP2) | Five unboxed Frame stills captured and evaluated; both detectors found the chair and missed the bottle; [E81](evidence/e081.md) | Inspected boxes and more representative stills before choosing a detector default. The owner chose chair for the POC; five presence-only stills do not close WP2 |
| Lifecycle acceptance (WP10 / G2) | G2 closed on November 3 after log analysis and an owner-rehearsed recovery procedure; [E75](evidence/e075.md), [E79](evidence/e079.md) | None for G2. The green-input cause remains unknown; any green input still aborts a run |
| HUD mapping (WP9 / G3–G4) | The November 3 observer attempt stopped at the contour budget before any marker appeared, with clean cleanup; [E83](evidence/e083.md). Scene statistics are implemented offline, and the file-only analysis of the chair still on Frame predicts that the O3 precondition passes ([E88](evidence/e088.md)). The November 7 attempt showed the marker tile in the mirror for the first time and matched its whole pattern; the corner tags did not decode at the tile's mirror size, about 0.86 of native, so the frame was rejected and the attempt counts; [E89](evidence/e089.md) | Reviewed scene-precondition change and a new run approval, then accepted observation, eye/composition and all-copy evidence, bounded motion/filter uncertainty, and reviewed mapping |
| Feedback and expiry (WP11 / G5) | Native masking, neutral fill, exclusion counters and report design tested offline; [E74](evidence/e074.md), [E78](evidence/e078.md) | Separately reviewed diagnostic, qualified mapping, physical feedback exclusion and expiry evidence |
| Combined live HUD (WP12 / G6) | Shared bounded synthetic pipeline and latency reporting; production gates closed | G2–G5 and the 60-second headset acceptance run |

Documentation work (WP7) previously delivered per-entry evidence and generated
summaries. Its continuing maintenance is described in the
[documentation plan](documentation-plan.md); it does not produce device evidence.

### Critical path

1. Review the observer scene-precondition change offline, then obtain a new
   approval for a bounded marker observation. The previous attempts are consumed.
2. Qualify the complete mapping after an accepted observation.
3. Validate feedback exclusion and physical expiry with that mapping.
4. Run the combined live HUD acceptance procedure using the chosen chair.

The [HUD checkpoint](guides/hud-checkpoint.md) owns the operator procedure.
The [qualification design](hud-qualification.md) states what an observation
cannot prove and what qualification still requires. Existing commands and
historical one-run approvals are not new device authorization.

### Camera display work order (adopted 2026-11-16)

**Status: adopted by the owner on 2026-11-16** (requested 2026-11-15, after the
proof of concept, [E99](evidence/e099.md)). The owner's answers are recorded in
[scope](poc-scope.md#architecture-decisions-2026-11-07) and
[below](#camera-display-decisions-answered-2026-11-16). This section supersedes
lane F's remaining device work and the mirror-input parts of the lanes below,
as lane R records; nothing here authorises a device step, and every gate stays
closed until its row is accepted and a reviewed edit lands.

**Why the plan changes.** E99 proved the input half on the Frame: SDK camera
frames reached the detector through the helper's GPU import, with inference
p95 39.3 ms and processing p95 51.6 ms against the 200 ms budget, and
passthrough unchanged. The output half (tile rendering, `fixed-edge-hud-v1`,
the overlay sink, the bridge's visibility opcode, the one-second display age)
exists but is checked mostly in the cloud. The mask, mapping and marker work
(G3, G4, WP9, lane A) and the time-multiplexed fallback (F3 to F8) exist
because the mirror contains our own panel ([E18](evidence/e018.md)). SDK
frames image the room before compositing, so on the camera route that
machinery has no job. That is the 2026-11-09 review's statement, not device
evidence, so it becomes a measured row (G8) rather than an assumption.

**What is missing.** The joint between the halves (a frame source around the
helper, a mask-free row selector, real-output expiry and a runner); device
proof of five things never run (the helper and the bridge together, expiry
in real output, which the bridge reports only under `--validate-stdin`, an
overlay update soak at HUD size, the stop on headset removal, and the absence
of feedback); and a governance path, since showing camera-derived labels is
gated live input and HUD output. The E48-pattern classification covers only
bounded recognition without display.

**The object is a bottle.** No chair is available at the device, so the owner
chose the bottle (2026-11-16). E99 did not detect it: it was small or far and
the scene was not controlled. Bottle coverage, held close, is first measured
in L-D1; if it misses its threshold, L14's remedies apply before any displayed
step.

#### Lane L, live label display from the SDK camera

Cloud items run in parallel with the two out-of-gate device steps L-D1 and
L-D2. G-numbers G1 to G6 keep their meaning as mirror rows; G7 to G10 are new.

Progress (2026-11-16): L1, L2, L3's rows, L4, L5, L6, L7, L8, L11 and L15 are
merged, with R0 and R1. Open cloud items: L10 (the camera gate and the
one-run scope record), L9 (the command), L3's template and L12. The
presence stop's timing for L-D1: the helper ends a run after a 2.0 s gap in
frames plus the acquire timeout, so the 3.0 s criterion is read from an
operator key mark made once the headset is off, with the cue as the prompt;
the report carries both.

| Item | Work | Kind | Depends on |
| --- | --- | --- | --- |
| L1 | Done (2026-11-16, this change). Record the owner's answers as dated scope rows, quoting the owner: the SDK camera (left, index 0) supersedes the mirror as the display's input; displayed labels are gated use; the diagnostic tier as decided; the passthrough definition; the presence interim; the consent answer for display; the calibration answer; the contract bound; F3's disposition. Superseded sentences are marked, not deleted (including the 2026-11-08 ordering clause that SDK probes follow F3 and C5/C6). No row opens a gate or authorises a device run | S, docs | the owner's answers |
| L2 | Done (2026-11-16). Truth fixes, independent of the decisions: the S2 row's "no probe has run" and the helper reference's "unverified on the device" paragraph now cite E95 to E99 (the foreign-queue acquire and DMA-BUF sync observed working, not verified in general); the recognition guide's "nothing here is a device result"; the lane S heading's "cloud-only"; a `gates.py` docstring naming bounded camera recognition as an out-of-gate use (docstring only) | S, docs | none |
| L3 | Rows done (2026-11-16): G7 to G10 are in the [acceptance table](#edge-hud-acceptance-gates) and the scope's acceptance is restated; the report template and the docs test pinning `gates.py`'s G-numbers follow L8 and L10. Reviewed acceptance-row edit (F6's role): mark G1 to G6 mirror route, G2 carrying over closed; add G7 to G10 with numeric criteria and the evidence type (headset report, distinct from cloud and CI); restate the scope's acceptance for camera input; a camera-HUD device-report template; rewrite the work-order intro, critical path and risks; a docs test pins every G-number `gates.py` names to the table | M, docs and decision | L1 |
| L4 | Done (#80, 2026-11-16): the two versions refuse each other on the first packet with an instruction to rebuild, so the Frame's helper is rebuilt before any camera run. Camera export contract v2: `max_seconds` 1 to 90 and `max_frames` 1 to 900 (fps 1 to 10 unchanged), protocol `camera-export/2` so an old helper is refused, a regenerated header, a revised wearer-facing notice that says labels are shown; `camera_recognize` keeps its 30 s and 300 frames | S to M, cloud | owner decision 4 |
| L5 | Done (#83, landed on `main` by #89, 2026-11-16), independently reviewed: the source acts on the presence end when the end packet is accepted, a stop clears the sinks without waiting for the helper's teardown, `drive` raises the source's failure, and a non-zero helper exit after a presence end reports a failed cleanup. `CameraHelperSource`: a pipeline frame source around the export reader; left camera only; `headset_removed` and `standby` end the run as `presence_ended`, never retried; acquire-time `observed_ns`; the exposure-minus-acquire offset reported as a timing, never as calibration; a test that no pixel array or intrinsics field reaches a report or log; `run_pipeline` still refuses the source | M, cloud | none (L4 beyond 600 frames) |
| L6 | Done (#84, 2026-11-16): `sightcircuit/hud/camera.py`, with the image-to-view orientation an explicit argument that a device step sets. Mask-free `camera_rows`: the sanitising, dedupe and row cap extracted from `HudMask.select` into a shared helper; direction per owner decision 3, a 3x3 cell from the box centre over the full frame (thresholds 0.4 and 0.6) or names only; no calibration; digest-pinned tile output | S, cloud | none (decision 3 picks the default) |
| L7 | Done (#81, 2026-11-16), independently reviewed against a fake OpenVR runtime: frames in this mode are observed frames (a new opcode with a frame identity and the observation time), so only a newer observation extends a tile; both deadlines are re-read after a late upload before any show; each expiry records when the hide was called and returned and whether the runtime still reports the tile visible. Stated limit: one thread, so a tile that is up stays up while a runtime call blocks (bounded by the 5 s step watchdog); the still panel for L-D2 takes a seed image id only. Not run against SteamVR. Bridge `--fixed-hud` real-output mode: geometry pinned to the HUD contract, no settings packets, the expiring-frame opcode accepted only here and under `--validate-stdin`, the deadline cleared within one poll with a `frame_expired` record, a retransmission never renewing a deadline; every other mode keeps refusing the opcode | M, native | none |
| L8 | Done (#88, 2026-11-16), independently reviewed on the validation bridge and a fake runtime: every newer result with rows is sent as an observed frame (the bridge renews nothing older, so the earlier "submit on change, refresh" rule is dropped); the end of a run latches the client so nothing follows the final hide; a dead bridge stops the run; the bridge acknowledges each frame it presents or refuses, the display ledger books only acknowledged frames, and `named_in_window` evaluates G10's rule from the report's intervals. Stated limits, to be measured on the device: the end-of-run hide cannot overtake a frame already in the pipe, and a stalled upload holds a tile past its deadline. `CameraHudStage`: frame, detector, rows, tile, then submit with `expires_ns = observed_ns + 1 s`; clear on end. Report p50, p95 and max of input age, inference, processing, output age and capture-to-display age with declared timebases; per-label display seconds and longest gap; the helper's ready and end records; no pixels or calibration values | M, cloud | L5, L6, L7 |
| L9 | `camera_hud`, the fifteenth command, with `--validate` (fake producer, validation bridge, in CI), `--synthetic-rows` (G9's driver, camera never opened) and `--camera` (at most 90 s); a typed informed start that never creates a scope record; the supervision primitive and G2's green-input abort; never routed through `ReplayWorker`. The terminal confirmation moves from `camera_recognize` into a shared module. Guide `guides/camera-hud.md`; the command count in CLAUDE.md flagged for an owner-reviewed edit | M to L, cloud and docs | L4, L8 (L10 for device use) |
| L10 | Reviewed `gates.py` edit 1, no gate opens: a new `LIVE_CAMERA_INPUT` lifted by G7, G8 and G10; `LIVE_HUD_OUTPUT` lifted by G8, G9 and G10; `LIVE_IMAGE_INPUT` (the mirror) and `HARDWARE_MAPPING` unchanged. A one-run scope record consulted only by `camera_hud`, written by the Frame manager when staging an approved step, bound to the revision, the manifest digests, a run id, an absolute deadline, the source and the geometry. This is a procedural control against mistakes, not a cryptographic one: anyone who can run the writer on the Frame can write a record, so the owner's approval in the Frame manager's session stays the authority. Tests refuse a missing, expired, mismatched or reused record, the mirror, both cameras, other geometry and other runners | M, cloud and decision | L3, L7, L8, decisions 1, 2 and 15 |
| L11 | Done (#85, 2026-11-16). Close the replay window's ungated live-source hook (input review finding 1): `ReplayWorker` accepts recorded sources only | S, cloud | none |
| L12 | Pre-sitting rehearsal before each displayed device step (L-D3 included): the full pre-push list on the pinned commit plus ARM64 on demand, an independent review against this order and the consent rules, the Frame manager's manifest and step cards; afterwards evidence entries and a verdict per row stating what stays unproven | S, cloud and docs | L9, L10, L11 |
| L13 | Reviewed `gates.py` edit 2, the lift, only after G7 to G10 are accepted and the owner decides: the camera input and HUD output gates open for `camera_hud`, the left camera and `fixed-edge-hud-v1` only. The gate model has no notion of a gate open for a named runner today, so this edit specifies that mechanism and its tests. Mirror input and hardware mapping stay closed; bounded attended runs and the typed start stay | M, cloud and decision | L-D1 to L-D5 accepted |
| L14 | Held remedies if a device step shows processing p95 over 200 ms, a stalled consumer or coverage short of its threshold: downscale or crop inside the helper (frames are 18,213,888 bytes), or preprocessing changes | M, held | a failing device result |
| L15 | Done (#82, 2026-11-16), independently reviewed: `--cue`, `--key-marks` and `--mark-labels`, `--target-label` with a single camera, coverage bounded by scheduled cue times, one removal entry per `remove` mark; without the new options the terminal is never read after the confirmation. Make L-D1 measurable: `camera_recognize` records a wall-clock time beside each frame's monotonic time and accepts operator marks (removal, cue positions), counts only; the arrow side checked on both axes, with the camera image's orientation confirmed by a cue | S, cloud | none |

#### Lane L device steps

Each step needs the owner's explicit approval, staged by the Frame manager,
which confirms in its own session; nothing runs from the cloud. The owner may
name adjacent steps for one sitting, each with its stop point. If SteamOS,
SteamVR or Mesa changed since the last step, the Frame manager re-runs
`--describe`, the offline tests and `--query-gpu` before continuing, as it did
for E99. No screen showing the Frame's desktop, the replay window or the
SteamVR mirror may be in the cameras' view during a run.

| Step | What runs | Acceptance (proposed numbers in decision 9) | Depends on |
| --- | --- | --- | --- |
| L-D1 | G7 and coverage, no display, out of gate as in E99: two `camera_recognize` runs of at most 30 s, left camera, 5 fps. Run 1: the bottle held at about 0.5 m and 1 m; the headset removed at about 20 s. Run 2: the bottle at cued positions (left, centre, right, up, down) at about 0.7 m | the run ends `headset_removed` or `standby` at most 3.0 s after the removal mark, no frame processed with an acquire time over 2.0 s after it; the bottle in at least 80% of in-view 1-s windows within 1 m; p95s within 200 ms; the offline side matches at least 2 of 3 cues per position, or decision 3 falls to names only | L1, L15, approval |
| L-D2 | G8, coexistence and the canary: the bridge shows the public seed fixture with horses (a class absent from the room) at the HUD position at HUD size, updated at 5 Hz for at least 120 s, during one `camera_recognize` run; a printed copy held in view for the last 10 s as the positive control. No camera-derived output is shown. The panel driver needed for a still at 5 Hz is part of L7 | no horse while only the panel shows it; the print detected at least once; at least 600 acknowledged updates, no freeze; camera p95 within 200 ms with the bridge running; both cleanups clean. Measured for this scene and panel only | L1, decisions 6 and 7, approval |
| L-D3 | G9, real-output expiry and soak, no camera: `camera_hud --synthetic-rows` on the rebuilt bridge under one scope record; a counter row at 5 Hz for at least 75 s with three stops | at least 375 acknowledged updates at 320x128; each stop gives `frame_expired` within 1,000 ms plus one poll and the wearer sees the tile blank; nothing displayed past its deadline | L7, L9, L10, L12, approval |
| L-D4 | First displayed camera labels: `camera_hud --camera`, at most 30 s, on the bottle; the wearer turns away at least three times; the headset removed once at the end, so the presence stop is seen on the displayed path. A separate entry; it does not count as G10 | the bottle named; arrows within tolerance if decision 3 is arrows; the name gone within about 2 s of each turn-away by the wearer and `frame_expired`; the tile cleared on removal; p95s within 200 ms | L-D1 to L-D3, L4, L9, L10, L12, approval |
| L-D5 | G10, the 60-second camera-HUD acceptance (formerly F7 and WP12): `camera_hud --camera`, 90 s bound, the bottle within 1 m, head turns, the bottle out of view for 5 s and back. The arrow tolerance agreed in writing first | the bottle named for at least 90% of a contiguous 60 s window, no gap over 2 s, and the wearer agrees; expiry on leaving view; capture-to-display p95 within decision 9's bound; input age at most 1,000 ms; passthrough and tracking normal during and for 30 s after; clean shutdown; green input aborts (G2) | L-D4, approval |

The red LED is a code property (no software path touches it), not a wearer
observation; the step cards say so.

#### Lane R, retiring the mirror route

| Item | Work | Depends on |
| --- | --- | --- |
| R0 | Done (2026-11-16): the Frame manager withdrew the staging (files kept, not runnable) and applied the owner-approved revision of the scheduled task's prompt; the lane F rows below carry the dispositions. Cancel F3 without running it and withdraw its staging in the Frame manager's session; update the "Frame update check" scheduled task's prompt if it names F3; freeze F3a, F3b and their guide; hold F4 to F8 as superseded by lane L; record the E100 to E103 disposition | decision 5 |
| R1 | Done (2026-11-16). Mark A6 and A9 retired; a "mirror route, not current" banner on the HUD checkpoint, qualification and fallback-measurement pages; WP9, G3 and G4 have no further device work | L1 |
| R2 | Quarantine the mirror HUD commands in place (`hud_marker_check`, `hud_marker_observer`, `fallback_measure`) and refuse the live runtime's HUD mirror mode | G10 accepted, L13 merged |
| R3 | Delete the dead mask stack, with the list recounted from today's tree (the F-lane instruments included), and `HARDWARE_MAPPING` with its last call sites; mirror recognition stays as decided | R2, the owner's go |
| R4 | Documentation retirement under the [documentation plan](documentation-plan.md): the HUD runtime page, the overlay page's planned HUD, the architecture's camera boundary, the camera page's D2 sentence; the plan's procedure and containment rows name the camera-HUD guide | R3 |

#### Unchanged or held

Kept: G2 (closed, carries over), F1 and F2, lanes B, C1 to C4, C7, C8, P and
E, WP1 with C6 off the critical path, `mirror_recognize` and `mirror_check` as
E48 diagnostics, never a HUD input. Held for decision 12: WP2 and C5's boxed
half; C5's timing half becomes a camera p95. Deferred: calibrated projection
and world-locked labels (WP3 to WP8). After the lift, each its own decision:
N1, presence and resume (a pause state instead of ending, or a proximity
source for the agreement's section 3.2 beyond attended runs); N2, longer runs,
the right camera and a soak past 600 updates. The capture-provider track stays
paused. Also open: the offline helper test that failed on the Frame at E99 is
fixed in the cloud (#76) and unverified on Turnip; offline review follow-up 2
(the bridge's six-second connection budget) needs its device check, now on the
helper-plus-bridge path.

#### Camera display decisions (answered 2026-11-16)

The owner took every recommended answer ("Yes, take all (Recommended)"),
allowed display (decision 14, "Yes, allow display (Recommended)"), accepted one
record per displayed step (decision 15, "Yes, one per step (Recommended)"),
and for decision 9 answered that no chair is available and chose the bottle.
The list keeps each question as it was put.

1. **Input and gates.** The left SDK camera supersedes the mirror as the
   display's input, and displayed labels are gated use: new rows G7 to G10,
   one-run scope records for the device steps, a lift only after acceptance.
   Recommended: yes.
2. **Gate structure.** A new `LIVE_CAMERA_INPUT` gate, so the mirror stays
   closed after the lift. Recommended: yes.
3. **Tile content.** Coarse arrows from the box position, or names only.
   Recommended: arrows if L-D1's offline check passes, else names only.
4. **Contract bound.** Raise the export contract to 90 s and 900 frames, so
   G10's 60 s window has margin; the operator rebuilds the helper.
   Recommended: yes.
5. **Lane F.** Cancel F3 and release E100 to E103; park F4 to F8 and the mask
   stack until G10, then retire them. Recommended: yes.
6. **L-D2's class.** The replay-panel pattern (E47) with a bounded
   recognition run and no camera-derived output shown, a named one-run use
   outside the gate. Recommended: yes.
7. **Passthrough rule.** It applies to every lane L device step, not only SDK
   probes; only a change in the passthrough image itself counts (freeze,
   black, grey, green, colour change, judder, lost "Colour, stable"), not our
   tile appearing, updating or expiring; during a planned headset removal the
   watch resumes when the headset is back on, and the 30 s after count from
   then. Recommended: yes.
8. **Presence.** Operator-attended bounded runs with the helper ending on
   removal, as the agreement's interim; one typed start per launch.
   Recommended: yes.
9. **Object and numbers.** Is a chair available where the Frame is used? No:
   the bottle, held within 1 m. The thresholds, as proposed: removal stop within 3.0 s; coverage at least 80%; G10
   at least 90% of 60 s with no gap over 2 s; capture-to-display p95 at most
   400 ms; expiry within 1,000 ms plus one poll.
10. **Age clock.** Ages and expiry from the host acquire time, at 1 s, with
    the exposure offset (about 241 to 245 ms, E97) reported beside them.
    Recommended: yes.
11. **Calibration.** No calibration used in this phase; the helper keeps
    discarding it. Calibrated projection needs a new consent answer.
    Recommended: yes.
12. **Evaluation.** Recall measured as counts-only coverage with
    wearer-confirmed presence (L-D1); C5's boxed mirror stills continue only
    if mirror stills are not treated as camera data. Recommended: yes.
13. **Mirror's future.** Bounded mirror recognition stays as a diagnostic;
    the mirror is retired only as a HUD input. Recommended: yes.
14. **Consent for display.** The 2026-11-09 consent covers what may be saved
    or published. Showing camera-derived labels and arrows in the headset to
    the wearer, with nothing saved beyond counts, labels, boxes, timings and
    format metadata, needs its own answer. Recommended: yes, for attended
    bounded runs.
15. **Diagnostic tier.** D3 allowed one diagnostic scope for the 60-second
    acceptance run. This order uses one per displayed step (L-D3, L-D4, L-D5)
    and needs a rule for a re-run after a failure: each re-run is a new
    approval with a new record. Recommended: yes.

### Implementation and evidence reconciliation

The checkout already contains packages A–H, the mirror session tokens and
neutral fill, and observer token recording. The November 6 merges also contain
bridge lifecycle/context-sensor/prediction work (#22), supervisor cancellation
fixes (#24), and observer scene statistics/file analysis (#25). Do not redo
those tasks from the archived handoffs. Their code being present does not
supply device acceptance or fill missing evidence records.

[SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1) has
merged the E79–E84 records carried over from the former repository. They establish
G2 closure by recovery rehearsal, the E61 file-only re-run, the catch-up session
and chair decision, the guarded setup installation, the failed G3 precondition,
and the November 6 cloud review. Installation in [E82](evidence/e082.md) is not
a Frame run of the task runner or a live test of the guard's enforcement.
The old “pending PR #23” handoff no longer describes these committed records.

SightCircuit PRs [#2](https://github.com/moral-kombat/SightCircuit/pull/2)
(agent runner, guard and installer follow-ups) and
[#3](https://github.com/moral-kombat/SightCircuit/pull/3) (package O3, the
observer scene precondition) merged on 2026-11-07. Their code is cloud-checked
only; none of it has run on the Frame. The native tracking reporter remains on
`work/native-tracking-reporter-unintegrated`, awaiting integration and review as
detailed in the
[November 7 handoff](history/coder-handoff-2026-11-07.md#item-3-the-native-tracking-reporter-carried-over-unfinished).

The native tracking reporter remains absent from this checkout. Observer
budget changes still require the scene evidence described in the
[observation reference](reference/hud-observation.md#observer-scene-precondition-2026-11-04).
These existing follow-ups do not change the acceptance order.

### Offline review follow-ups

The November 6 review left the following work, in its recorded order. The
[full review notes](history/coder-handoff-2026-11-07.md#ship-pipeline-follow-ups-2026-11-06)
preserve exact findings and limits; [E84](evidence/e084.md) records what merged.

1. Capture supervisor: simultaneous pending SIGINT/SIGTERM cleanup, replay of a
   default signal disposition, and duplicated signal/mask handling remain open
   on the paused provider track.
2. Bridge: the six-second connection budget needs its listed device check;
   geometry clipping and sampler-bound checks have simplification follow-ups.
3. Observer: review scene-budget validation, repeated contour work and reporting
   cleanup with the pending scene-precondition work; a new device attempt needs
   the reviewed change and a new approval.
4. Agent runner: consolidate the signal hold/start/resume protocol through its
   own review. The former #27 is now open SightCircuit PR #2, not merged code.
5. CI: investigate the load-dependent cancellation test failure; do not suppress
   it as a substitute for fixing the race.

These five map onto the work order below: 1 to B6, 2 to A3 and E1, 3 to A3 and
A5, 4 to B4, 5 to B1 and B3.


### Architecture plan work order (2026-11-07)

The [architecture decisions](poc-scope.md#architecture-decisions-2026-11-07)
produce five lanes (A, B, C, P and E; the letter D is kept for the decisions);
lane F, the fallback build, opened on 2026-11-08 at the D2 exit; lane S, the
SDK camera-input lane, opened cloud-only the same night (the letter G is the
gates').
Sizes are S, M or L. "Device" marks a step that runs on the
Frame and needs the owner's approval for that specific run; a historical
approval does not carry over. Items without a dependency can start now, in
parallel coder sessions. The acceptance order of the gates is unchanged.

**Lane A, the HUD path (critical path).**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| A1 | Record the decisions, the planned structure and this work order | S | none (this change) |
| A2 | File-only still analysis of the planned view with `--analyze-still`, recorded as an evidence entry | S, device | approval |
| A3 | Pre-attempt offline review of the stops before a marker: the bridge's connect budget against the marker's ready wait, the incomplete-marker tolerance, the `--record-session-tokens` choice | M | none |
| A4 | G3 attempt on a checkout containing package O3, with a manifest pinning that checkout | S, device | A2, A3, approval |
| A5 | Only if A4 stops at the precondition: schema-3 scene limits from the run's recorded statistics, with the documentation table and boundary tests, then at most one more attempt | S to M, then device | A4 |
| A6 | Calibration record version 2 carrying the mirror's native format metadata. Retired (2026-11-16): it served the mirror mask, which the camera route does not use | M, retired | none |
| A7 | Superseded on 2026-11-08 by the fallback (D2 exit, [E90](evidence/e090.md)): diagnostic gate table, scope record and call-site changes for the mask path; not started | M | superseded |
| A8 | Superseded with A7 (mask path) | M | superseded |
| A9 | Tracking reporter integration behind its flag (a D1 diagnostic, not on the fallback's path; deferred, not superseded). Retired (2026-11-16): it existed for the mapping's tracking tokens, and the camera route has no mapping | L, retired | none |
| A10 | Superseded with A7; the 60-second run is F7 on the fallback path | M | superseded |
| A11 | Fallback design document and bridge event contract (time-multiplexed exclusion); done (#16). The build is lane F below, opened on 2026-11-08 when the D2 budget was exhausted | S, done | none |

**Lane B, lifecycle and tests.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| B1 | Flake diagnostics: print the report on the cancellation assertion and add per-module durations to the no-skip runner | S | none |
| B2 | Normalise the SIGINT disposition for the suite's children; document the runner's inherited-ignore policy | S | none |
| B3 | The supervision primitive in `sightcircuit/runtime`, seeded from the guard, fixing the race | M | B1 |
| B4 | The agent runner adopts it; the agent package becomes its own checkpoint-manifest group | M | B3 |
| B5 | The live pipeline launcher and the mirror launcher adopt it; progress streaming lets `runtime/process.py` retire | M | B3 |
| B6 | The capture supervisor adopts it (paused track, last) | S | B3 |
| B7 | Suite time budget enforced in CI | S | B1 |
| B8 | Agent documentation and installer follow-ups from [E86](evidence/e086.md): the workspace trust dialog that the Phase 2 enforcement test depends on, in `docs/guides/frame-sessions.md`; the installer summary reporting its own channel default instead of the installed setting | S | none |
| B9 | The held-signal flake of `test_supervision` (checks run 90 on #29 and, found by the coder, run 93 on main at `1889e56`): reproduced at 1 in 1600 under CPU stress, root-caused to the worker's stop handler calling `Event.set()` on the main thread, fixed by a stop relay thread woken through the process's wakeup descriptor (#40, 2026-11-08); two deterministic tests fail on main and pass after; `tests/stress_supervision.py` re-runs the loop. The "race fixed" call is met: the ARM64 run on main at `b4cb2d4` passed on 2026-11-08 | S to M, done | B3 |
| B10 | Phase 2 guard-enforcement test scope for the agent runner. Done (#42, 2026-11-08): [the guide](guides/agent-enforcement-test.md) with the trust precondition, the 95-case matrix over every refusal class and every Tier B and C id, refusals observed from the hook, the saved verbose session transcript and the run directory, the counts-only reports, and `sightcircuit-agent-enforcement-check` (the twelfth command) driving the real guard offline. The device run is a separate approval; the guard stays at version 4 | S, done | B8 |

**Lane C, detector evaluation.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| C1 | One named POC threshold constant and one named live-budget constant; commands import them | S | the owner's budget number |
| C2 | A test that `evaluation/local/` stays ignored | S | none |
| C3 | Box entry in the session manifest helper, with documentation | S | none |
| C4 | Per-label box recall and a basename-only input path in the report | S | C3 |
| C3b | A Qt box-annotation tool that writes the manifest's objects entries, so the owner can draw the boxes on the Frame desktop | S to M | C3 |
| C5 | The representative evaluation session: at least twenty boxed chair stills, scored file-only, plus one `mirror_recognize` run per detector for inference p95. Part one on 2026-11-08: six lamps-on stills, presence-only, paused by the owner ([E93](evidence/e093.md)); resumes under the same approval with the lamps-off stills, the boxing and the timing runs | M, device, in progress | C1 to C4, approval |
| C6 | WP1: a native-resolution colour still of the E61 scene evaluated beside the grey preview | S, device | approval; shares C5's session |
| C7 | Stale script names in E8, E67 and E69 | S | none |
| C8 | The E80 re-run manifest records its input by the pre-move absolute path, so a repeat with `--require-input-digests` fails; make the recorded path relative and document the repeat ([E86](evidence/e086.md)) | S | none |

**Lane P, packaging.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| P0 | Python 3.14 on the Frame after SteamOS 0.5.8: pin review, 3.14 pins behind markers, setup scripts accept 3.12 and 3.14 (added 2026-11-07 when the update removed Python 3.12, [E85](evidence/e085.md)) | M | none |
| P1 | Hashed locks for the three requirement sets; add the missing `av` pin to the dev set; setup scripts use the locks | M | P0 |
| P2 | Active command bodies move into the package with thin script wrappers and console entry points; validator references and manifest pins follow | M | P1 |
| P3 | A bounded retry with backoff in `sightcircuit/assets.fetch` for transient network errors on the pinned fixture downloads. Done (#45, 2026-11-08): four attempts with 2, 4 and 8 s between them on timeouts, resets, incomplete reads, HTTP 429 and 5xx; a wrong digest is never retried; no partial file is left; constants and the 254 s per-asset bound pinned by `tests/test_assets.py`. Three CI re-runs that day were caused by one release download timing out on the runner | S, done | none |

**Lane E, native bridge.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| E1 | Split `tools/overlay_bridge.cpp` into lifecycle, rendering, reporting and sensing units; move the source-text tests and manifest pins. Done (#38, 2026-11-08): one shared header and five translation units, compared offline against the single-file build; the Frame rebuilds the bridge before its next bridge step | L, done | none |

**Lane F, fallback input by time-multiplexed exclusion (opened 2026-11-08).**

Built because the D2 budget is exhausted ([E90](evidence/e090.md)) and the
owner chose the fallback; the design and costs are in the
[fallback reference](reference/hud-fallback.md#work-breakdown), which owns the
step definitions. Sizes as there. "Device" steps need the owner's approval for
that run.

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| F1 | Bridge visibility opcode with a cycle number and the timestamped visibility lines (`hide_requested`, `hide_returned`, `overlay_hidden`, the show counterparts, `visibility_event_missing`); header regeneration, rebuild, packet and source-text tests. Done (#41, 2026-11-08): the returned line names the OpenVR call made, every request ends in exactly one terminal line, the 250 ms bound is a cloud default pinned by a test and unmeasured on the Frame; no `overlay_hidden` line has ever been produced (no runtime in the cloud) | M, done | E1 |
| F2 | `OverlayClient` event queue and a cycle API on the sink. Done (#48, 2026-11-08): every visibility line kept by cycle, a pure outcome reading confirmed only by the runtime's event, a derived 320 ms deadline (cloud default, unmeasured on the Frame), `OverlaySink.hide` and `show` failing closed; no runtime event has ever been produced | S, done | F1 |
| F3a | The marker bridge mode accepts the visibility opcode while the marker is displayed and writes the seven lines, so the measurements use the one tile the device is approved to show and the one pattern the mirror can be read for (the coordinator's call, 2026-11-08). Done (#50): a run that receives nothing is the observer's run, measured identical; only the visibility request is accepted on stdin; the bridge must be rebuilt on the Frame before any run that uses it | S, done | F1 |
| F3b | `fallback_measure`, the thirteenth command: the marker bridge in that mode driven for N bounded cycles through the F2 API while the mirror is read at full rate and `locate_copies` tells presence from absence; one report with a section per measurement, counts and nanoseconds only; validate mode, opt-in flags and the checkpoint manifest as the observer has them. Done (#51): margins taken from the bridge's own display deadline, a cycle starts only with room for both requests, a capture failure ends the cycling; [guide](guides/fallback-measurement.md) | M, done | F3a, F2 |
| F3 | The device measurements themselves: hide-to-absence latency, hidden-event delivery, loopback repeats, show-to-visible; one `fallback_measure` run of 20 s and five cycles would have given four evidence entries. **Cancelled without a run** (owner, 2026-11-16, the camera route): the bound it measures matters only when the mirror carries the tile; the Frame manager withdrew its staging the same day and the E100 to E103 reservation is released | S, device, cancelled | none |
| F4 | Capture window and admission rule in the pipeline, with rejection counters and boundary tests | M, superseded by lane L, retained until R2 | none (owner, 2026-11-16) |
| F5 | The fallback stage: hide, window, infer, show on `_run_latest`; tile rendering without a mask; run reports | M, superseded by lane L (L6, L8), retained until R2 | none |
| F6 | Acceptance rows and gate references for the fallback, a reviewed edit to scope, this work order and `sightcircuit/gates.py` | S, superseded by L3 and L10 | none |
| F7 | Command and operator guide; the 60-second run under the diagnostic tier's scope record | M, then device; superseded by L9, L-D4 and L-D5 | none |
| F8 | Retire or quarantine the dead mask stack and its documentation | M, becomes R2 to R4 | G10 accepted and L13 merged |

Lane A's device steps consume the D2 attempt budget: A4 is the first counted
attempt, A5 the second and last. An attempt counts when the observer judged the
scene (a precondition rejection, or anything after the marker tile appears); a
stop caused by connectivity or preparation never counts, whatever its position
in the run. The [counting rule](poc-scope.md#architecture-decisions-2026-11-07)
and the [operator table](guides/hud-checkpoint.md) carry the full list.

**Lane S, SDK camera input (opened cloud-only; device steps S4 to S9 ran 2026-11-11 to 15, E95 to E99).**

Opened on 2026-11-08 at night after the owner obtained the Arcturus Vision
Camera Preview SDK ([scope row](poc-scope.md#architecture-decisions-2026-11-07));
the [dated input review](history/2026-11-09-arcturus-sdk-input-review.md#migration-plan)
owns the step definitions, the device phases and the owner decisions. The
public headers and the agreement are vendored (#54); the client library is
never committed, fetched or linked in CI, and only the fake backend is built
there. The owner's own session wrote the lane's implementation on branch
`sdk-helper/metadata-probe` (107c893, from `de2523b`), adopted by the owner
over the coder's parallel draft; the coder reviews it, re-authors it onto main
as one PR and fixes what the review finds. Its fake build and 38 tests passed
on the Frame on 2026-11-08 (21:26 to 21:28 EST, a throwaway worktree, no SDK
library, no camera action; the Frame manager's report at the SDK session's
request, with the owner's approval in the Frame manager's session). That run
is a note, not a work-order step and not evidence: nothing on the device
changed and nothing was observed. No gate, pinned file or constant changes in
this lane before the owner's gate classification.

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| S1 | Containment and build: `scripts/build_camera_helper.sh` (`--fake`, `--sdk DIR` outside the work tree, release and header verification, a symbol allow-list per object), the SightCircuit-owned backend interface with no SDK type, the fake backend driving every outcome, the source-text and symbol tests in the dependency-free job. Done (#61, 2026-11-09): the owner's 107c893 re-authored onto main, with SIGPIPE ignored, a named fake-build CI step and the [helper reference](reference/camera-helper.md); an independent review found no containment violation and two defects to fix before any SDK device run (the time bound under a stalled consumer; fake scenarios missing for some outcomes), assigned as a follow-up PR | M, done | the review's finding 5 |
| S2 | `sightcircuit-camera-helper`, the one binary that links the client library: `--describe` (no connect), `--hello` (connect, handshake and control state, reader off) and `--probe-frames N --max-seconds S` (format metadata, planes, timestamps against host clocks, sequence gaps, standby and control drift; no pixel read, no control change, no exclusive request); typed confirmation, runtime-directory lock, signals held around SDK calls, a watchdog with a prepared final record. Done (#61, 2026-11-09), cloud-checked only; the fake build and tests passed on the Frame on 2026-11-08 (user report, not evidence); its probes have since run on the Frame ([E95](evidence/e095.md) to [E97](evidence/e097.md)) | M, done | S1 |
| S3 | The operator procedure for the two probe phases in the device guides: preparation, the passthrough observation as an operator field, the headset-removal sub-step, the G2 recovery procedure ready, the report fields. Done (#57, 2026-11-09): [camera probe guide](guides/camera-probe.md), written against the helper as it is on the owner's branch; its flag names, bounds and record fields follow the S1/S2 review | S, done | S2 merged |
| S4 | Phase A on the Frame: connect, handshake and control state with the reader never activated; the wearer watches passthrough during the run and for 30 s after | S, device | S3, approval |
| S5 | Phase B on the Frame: the reader active for at most 10 s, metadata for a few frame sets, the passthrough observation repeated with headset removal; answers linear or UBWC, standby behaviour and the passthrough question that holds F4 to F8 | S, device | S4, approval |

On 2026-11-09 (02:40Z) the owner set the goal of a working proof of concept
using the helper: camera frames from the SDK reaching the detectors in a
bounded run without display, the [E48](evidence/e048.md) pattern on the camera
route. The items below were added for it; S6 and S7 proceed in parallel with
S1 to S3 and depend on no device result (the pixel path's UBWC branch is
unverified until S5 answers linear or UBWC). The later steps of the review's
migration plan (the acquisition module and the replay, identity and frame-set
findings) follow lane F and are not scheduled.

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| S6 | The helper's `--export-frames` mode: BGR3 frames to the parent over stdout under the camera export protocol (version 1: `SCCF` packets, a `ready`, `frame` and `end` header vocabulary, bounded sizes), paced at 1 to 10 frames a second for at most 60 s or 600 frames; two pixel paths behind one interface, `cpu_linear` (mmap of linear DMA-BUF planes and a tested YCbCr conversion) and `gpu_convert` (Vulkan import by DRM modifier and conversion, allowed to adapt the frozen capture track's import code); ends on headset removal or standby; never writes a pixel anywhere; the fake backend serves memfd-backed linear frames for the tests. Done (#63, 2026-11-09): independently reviewed; one protocol writer for records and packets; the UBWC import failed on the device as first written ([E98](evidence/e098.md)) and ran after #75 ([E99](evidence/e099.md)) | L, done | S1, S2 |
| S7 | The Python side: the camera contract as data with a generated C++ header pinned by a test, a strict export-stream reader producing `Frame`s, a `camera_helper` source launching the helper through the runtime primitive, and `camera_recognize`, the fourteenth command: bounded recognition without display, structured as `mirror_recognize`, at most 30 s and 300 frames, a validate mode against a fake producer, labels, boxes and timings in the report and never pixels; a guide. Done (#62, 2026-11-09): independently reviewed; cloud-checked against the fake producer; it has since run against the helper and the camera on the Frame ([E99](evidence/e099.md)) | M, done | none (tests use a fake producer) |
| S8 | Pixel path on the Frame without saving: `--export-frames` into the reader's validate path, counts and timings only, the passthrough observation as in S5. Done (E98, then E99, 2026-11-13 to 15): the first check failed at the GPU import; after #75 the check passed with 10 frames processed | S, device, done | S5, S6, S7, approval |
| S9 | Bounded camera recognition on the Frame: one `camera_recognize` run of at most 30 s on the chair scene, no display, counts, labels, boxes and timings in the report; the proof of concept the owner asked for. Done ([E99](evidence/e099.md), 2026-11-15): 100 frames from the left camera through the GPU YCbCr-sampler import to MobileNet-SSD, labels and boxes reported, inference p95 39.3 ms, passthrough unchanged; the proof of concept | S, device, done | S8, approval |

Evidence identifiers for S4 to S9 were allocated when each ran (the E100 to
E103 reservation for F3 was released on 2026-11-16). Phase A ran on 2026-11-11 ([E95](evidence/e095.md)):
the pinned SDK verified, the linked helper built and passed its tests on the
Frame, and its single connect ended `provider_unreachable` because the camera
service is absent until Camera Switch is reinstalled from Arcturus Vision
Camera's feedback branch; passthrough unchanged. The second Phase A run, after the owner moved
Arcturus Vision Camera to the feedback branch and reinstalled Camera Switch,
connected on 2026-11-13 ([E96](evidence/e096.md)): handshake, two cameras,
the control state read (60 frames a second, 8-bit, sRGB, BT.709, BT.601), the
reader never on, passthrough unchanged. Phase B ran the same day ([E97](evidence/e097.md)): the reader delivered
NV12 UBWC frame metadata (modifier `0x0500000000000001`, 2464 by 2464, 60
sets a second) with passthrough unchanged, so pixel steps take the
`gpu_convert` path; standby on headset removal is still untested. The pixel-path check (S8)
followed ([E98](evidence/e098.md)): the command, the supervised launch, the
export stream and the clean stop all worked against the real service, and the
first GPU import of the UBWC buffer was refused by the Turnip driver
(`VK_ERROR_FORMAT_NOT_SUPPORTED` at the image-format query), so no frame
reached the detector and S9 was not run; the import fix is cloud work and a
new run is a new approval. After the import fix (#75), the proof of concept
ran on 2026-11-15 ([E99](evidence/e099.md)): SDK camera frames reached the
detector in a bounded run without display, with passthrough unchanged. The
merged chain ran end to end in the cloud against the fake backend on
2026-11-09 ([E94](evidence/e094.md)): twenty synthetic frames exported by the
helper, confirmed on a terminal, read, recognised and reported without a
pixel; a fit check, not a device result.

#### Progress (2026-11-07, evening)

Merged on main, cloud-checked only, none run on the Frame: A1 (#5), A3 (#10),
A6 (#11), B1 and B2 (#9), B3 (#12; its ARM64 run on main, run 16 at `e4019a4`, is green, which completes the "race fixed" acceptance), C1 to C4
(#8), P0 (#7). Evidence E85 (the SteamOS 0.5.8 update and the Python 3.12
removal) and E86 (the installer run and the guard version 4 disclosure) are
recorded. The installer step that the plan listed as a separate approval
turned out to be none: guard version 4 became active with the pull to
`45771fe`, and the installer run at `6e648c7` wrote nothing under `.claude/`
([E86](evidence/e086.md)). The replay environment was rebuilt on the Frame
under Python 3.14 at `544cf34` ([E87](evidence/e087.md)) and A2 ran on the
chair still: the O3 precondition is predicted to pass on all five limits, with
about a tenth of the frame near saturation under the lamps
([E88](evidence/e088.md)). A4 ran on `6f44260` with the lamps off: the marker
tile appeared in the mirror for the first time, its whole pattern matched, and
the corner tags did not decode at about 0.86 of native size, so the attempt
was rejected and counts as the first of two ([E89](evidence/e089.md)). The
owner accepted that evening that the marker or decode change is the one
reviewed change the budget allows before the last attempt
([D2 refinement](poc-scope.md#architecture-decisions-2026-11-07)). A5 ran on
`1889e56` late on November 8 with the lamps off: the decode worked, one
complete copy at 0.885 of native passed every check and was accepted as the
first marker sample on the Frame, and the next read 0.2 s later found the copy
about 45 px to the right while the wearer turned, so the run was rejected on
the 8 px sampled-drift limit. The marker appeared, so the attempt counts as the
second and last of the D2 budget; the exit rule is reached
([E90](evidence/e090.md)). The owner
also launched the Arcturus app at 18:36 EST that evening, which the baseline
recorded as the first run since October 26; recorded here for the G2 timeline,
not as evidence. Also merged since: B4 (#19), B7 (#18), B8 (#17), P1 (#15), P2
(#22), A11 (#16). Merged on 2026-11-08 (UTC): A3b (#23, the manifest's
SteamVR package reader), B5 (#27, the launchers on the primitive and
`runtime/process.py` retired), B6 (#28, the capture supervisor's record-and-poll
policy under the unchanged quarantine rule), C7 and C8 (#31), C3b (#30, the
box-annotation window the C5 session needs; exercised offscreen only), the C8
follow-up (#33), and A5 (#29, main `0233b12`: the stage-two decode at the
mirror's scale, the one reviewed change allowed before the last budgeted G3
attempt; cloud-checked only, nothing run on the Frame). Lanes B and P are
complete. The last budgeted G3 attempt ran ([E90](evidence/e090.md)): the
decode passed and the run rejected on marker movement; the D2 exit rule is
reached and the owner chose the fallback ([scope](poc-scope.md#architecture-decisions-2026-11-07)).
E1 (#38), F1 (#41), F2 (#48), F3a (#50), F3b (#51), B9 (#40), B10 (#42) and
P3 (#45) merged on 2026-11-08; lanes B and P are complete and lane F's cloud
work up to the device measurements is done. Open, all waiting on the owner: F3
(device, approval, with the first bridge rebuild since E1), C5 with C6
(begun, six stills, [E93](evidence/e093.md); resumes on the owner's word). The
Phase 2 enforcement run is done ([E92](evidence/e092.md)). After F3: F4 to F8, held until the SDK
lane's Phase B probe (S5) answers the passthrough question (owner decision,
2026-11-08). Lane S opened cloud-only that night; the owner's implementation
(branch `sdk-helper/metadata-probe`, 107c893) was adopted over the coder's
draft and awaits its review and PR. A9 deferred; A7, A8 and A10 superseded. The Frame's built bridge predates E1 and F1 and is rebuilt only
when a device step needs it (F3), under that step's approval. The council's
hypothesis for the cancellation race was wrong; the recorded cause is the
readiness probe firing on the worker before it had exec'd
([checks reference](reference/development-checks.md)). The held-signal flake
seen in #29's first replay run (checks run 90) and on main at `1889e56` (run
93) was reproduced under CPU stress, root-caused and fixed in B9 (#40):
the worker's stop event is now set by a relay thread, never by the signal
handler on the main thread ([checks reference](reference/development-checks.md#held-signal-flake-root-cause-2026-11-08)).

### Decisions still needed from the owner

- Whether the fallback's tile keeps the arrows, which depend on the capture
  layout, or shows names only; decided when F5 is specified (the capture-layout
  and 300-second mapping-lifetime questions of the mask path lapsed with the
  fallback decision).
- The Phase 2 guard-enforcement run happened on 2026-11-08 and passed with
  reduced coverage ([E92](evidence/e092.md)): the guide is revised for what
  the device showed (no Glob tool in print mode; a deny-rule Read refused
  before the hook). A second run with the revised set is a new approval,
  not scheduled.
- Decided 2026-11-08: the fallback (D2 as written; [scope](poc-scope.md#architecture-decisions-2026-11-07)),
  C5 with C6 approved (begun, E93), the Phase 2
  test scheduled and run ([E92](evidence/e092.md)). Next device approvals,
  each separate: the owner's resumption of the C5/C6 sitting (approved and
  confirmed; part one is E93), the F3 measurement run (staged, not approved,
  with the bridge rebuild; E100 to E103). Superseded on 2026-11-16: F3 is
  cancelled and C5/C6 is on hold, as the entry of that date says.
- Decided 2026-11-08, night: the SDK camera-input lane is open, cloud-only
  (the review's decision 1); F4 to F8 are held until S5 answers the
  passthrough question; the owner's helper branch is the lane's code.
- Decided 2026-11-09 (02:45Z), from the [review's list](history/2026-11-09-arcturus-sdk-input-review.md#owner-decisions-in-priority-order)
  and recorded in [scope](poc-scope.md#architecture-decisions-2026-11-07):
  the passthrough rule (each phase approved separately; any wearer-visible
  passthrough change during a probe or in the 30 s after it is a disturbance,
  and one disturbance stops the SDK track until the owner reopens it); the
  gate classification (the no-pixel probe modes and bounded camera recognition
  without display are confirmed uses outside the live-input gate, the E48
  pattern; every gate stays closed); the frozen capture track's import code
  may be adapted into the SDK helper where it helps, the track itself staying
  paused; consent (counts, labels, boxes, timings and format metadata may be
  saved locally and published; no pixels, stills, previews or calibration data
  anywhere; the red LED untouched). Still open, none blocking S1 to S9: the
  presence source for the agreement's section 3.2 (operator-attended bounded
  runs are the interim), whether Arcturus-app rehearsal recordings count as
  Camera Data, the long-term status of the private camera routes and mirror
  input, and the toolchain beyond the host compiler the build script uses.
  Next device approvals on this lane, each separate, after the S1 to S3 PRs
  merge: S4, then S5, then S8, then S9.
- Decided 2026-11-16: the [camera display work order](#camera-display-work-order-adopted-2026-11-16)
  is adopted with all fifteen answers (scope row of that date): the SDK camera
  becomes the display's input, display to the wearer is allowed in attended
  bounded runs, one record per displayed step, F3 cancelled and E100 to E103
  released (lane R's R0 records it in this page), and the bottle replaces the
  chair as the object. Next device approvals on that lane, each separate:
  L-D1, then L-D2, then L-D3 to L-D5 after their cloud items.
- The Frame rebooted into SteamOS 0.5.8 ([E85](evidence/e085.md)); a Frame
  manager session exists and the "Frame update check" scheduled task was
  rebound to it on 2026-11-07, with the old task left disabled as a record.

## Edge HUD acceptance gates

A gate passes only on its required evidence. Offline tests, ARM64 CI and a
successful process exit are not headset acceptance. A failed device run remains
recorded; investigate it before proposing a changed run.

Since 2026-11-16 the live display's input is the left SDK camera
([work order](#camera-display-work-order-adopted-2026-11-16)). G1 to G6 are
the mirror route's rows: they keep their numbers and their recorded state, G2
carries over closed, and G3 to G6 have no further device work. G7 to G10 are
the camera route's rows; each is accepted only on a headset report recorded
as a numbered evidence entry, distinct from cloud checks and CI results.

| Gate | Required evidence | Accepted state in this checkout |
| --- | --- | --- |
| G1. synthetic mask and expiry | Native full-footprint masking, margins/eye union, changed-context rejection and independent stale expiry | Passes offline; [E69](evidence/e069.md), [E74](evidence/e074.md), [E78](evidence/e078.md) |
| G2. lifecycle and cleanup | Explained software termination, truthful cleanup reports, and physical recovery evidence; the accepted alternative permits an owner-rehearsed recovery procedure when the cause remains unknown | **Closed November 3, 2026**, on the owner’s confirmation after rehearsal; [E79](evidence/e079.md). The green-input cause remains unknown, and the clean rehearsal does not prove the procedure cures green input |
| G3. mapping observed on Frame | Bounded marker/observer report ending `observed_unqualified`, with clean cleanup and normal passthrough confirmed | Open. V4 was rejected; the November 3 attempt stopped at `observer_contour_budget` before any marker was shown ([E83](evidence/e083.md)); the November 7 attempt passed the precondition, showed the marker and matched its whole pattern, then rejected on the tag decode at the mirror's scale ([E89](evidence/e089.md)); the November 8 attempt, after the reviewed decode change, accepted one complete decoded copy at 0.885 of native and then rejected on marker movement of about 45 px between two reads 0.2 s apart while the wearer turned, against the 8 px sampled-drift limit ([E90](evidence/e090.md)). The D2 budget is exhausted; the exit rule is reached and the owner's decision is pending |
| G4. mapping qualified | Reviewed evidence for eye composition, all HUD copies, conservative margins, operating limits and exact context | Proposed method; device qualification pending |
| G5. feedback exclusion and physical expiry | HUD visible during head motion with no detections from its footprint; independently observed expiry within the declared bound | Not run in committed evidence |
| G6. combined live HUD | The [60-second POC acceptance](poc-scope.md#acceptance), with the report fields below | Not run. The POC object is chair; the 400-frame replay panel continued updating on the tested device/runtime, but that is not combined live acceptance; [E81](evidence/e081.md). Mirror route: superseded by G10 |
| G7. presence stop and coverage (camera) | Step L-D1, no display: a bounded `camera_recognize` run ends `headset_removed` or `standby` at most 3.0 s after the operator's key mark made once the headset is off, with no frame processed whose acquire time is more than 2.0 s after that mark; the bottle, within 1 m, detected in at least 80% of the in-view 1-second windows; inference and processing p95 at most 200 ms; passthrough unchanged during the run and for 30 s after the headset is back on | Not run. The proof of concept ([E99](evidence/e099.md)) did not exercise removal and did not detect the bottle at distance |
| G8. coexistence and no feedback (camera) | Step L-D2: with the bridge showing a public seed image of horses at the HUD position and size, updated at 5 Hz for at least 120 s (at least 600 acknowledged updates, no freeze), one bounded `camera_recognize` run reports no `horse` while only the panel shows it and at least one `horse` when a printed copy is held in view (the positive control); camera inference p95 at most 200 ms with the bridge running; both cleanups clean; passthrough unchanged; no screen showing the Frame's output in the cameras' view | Not run. That the camera images the room before compositing is the 2026-11-09 review's statement, not device evidence; a pass holds for the scene and panel tested |
| G9. real-output expiry and soak | Step L-D3, no camera: synthetic rows through the `--fixed-hud` bridge at 5 Hz for at least 75 s (at least 375 acknowledged updates at 320x128, the wearer sees the counter advance to the end); at each of three stops a `frame_expired` record whose `hide_returned_ns` is within 1,000 ms plus one poll of the last frame's observation, `hide_ok` true and the runtime reporting the tile not visible, and the wearer sees the tile gone; nothing displayed past its deadline; cleanup clean; passthrough and tracking normal during and for 30 s after | Not run. The mode has met only a fake runtime in tests (#81) |
| G10. combined camera HUD | The [60-second POC acceptance](poc-scope.md#acceptance) on the camera route, step L-D5 after a first-light run (L-D4): the bottle named for at least 90% of a contiguous 60 s window with no gap over 2 s, and the wearer agrees; arrows within the tolerance agreed in writing before the run, or names only; the name gone when the bottle leaves view and the tile cleared on headset removal, by `frame_expired` and by the wearer; nothing displayed past its deadline; input age at most 1,000 ms; inference and processing p95 at most 200 ms and capture-to-display p95 at most 400 ms; passthrough and tracking normal during and for 30 s after; clean shutdown with truthful helper, supervisor, worker and bridge reports; green input aborts the run (G2) | Not run |

[`sightcircuit/gates.py`](../sightcircuit/gates.py) is the code's single gate
table, and since work-order item L10 (its reviewed edit 1) it has four gates.
Hardware mapping requires G4. Live image input from any source other than the
SDK camera, which is the mirror, is lifted by G4, G5 and G6. Camera input, SDK
camera frames feeding the live pipeline or the HUD path, is a gate of its own,
lifted by G7, G8 and G10, so the mirror stays closed whatever happens to the
camera route. Real HUD output is lifted by G8, G9 and G10. All four remain
closed and every claim they report is false; a test pins that each G-number
the table names is a row above. The numbers in G7 to G10 are the owner's
accepted thresholds of 2026-11-16 (decision 9). Lifting a gate requires a
reviewed code change after its evidence exists (item L13 for the camera
route); no configuration flag, environment variable, typed answer or
hand-edited candidate opens one.

The displayed device steps (L-D3, L-D4, L-D5) collect that evidence while the
gates are closed, under the diagnostic tier the owner decided on 2026-11-16
(decision 15): one
[one-run scope record](reference/camera-hud.md#the-one-run-scope-record) per
step, a re-run being a new approval with a new record. The record is checked
at the two permission sites, real HUD output and camera input to the display,
and nowhere else. It is a procedural control against mistakes, not a
cryptographic one: the owner's approval in the Frame manager's session is the
authority. The mirror route's separately scoped diagnostic in the
[qualification design](hud-qualification.md#separately-scoped-device-validation)
was never implemented and has no further device work.

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

For the camera rows the entry carries no mask regions and no mapping. It adds
the helper's ready and end records (format metadata only), the marks and the
counts-only summaries of the recognition report (G7, G8), every
`frame_expired` record with its hide times (G9, G10), the exposure-minus-acquire
offset as a timing, per-label display seconds and the longest gap (G10), and
the wearer's observations. No pixel, still, preview or calibration value is
recorded anywhere. The camera-HUD report template follows the stage's report
(work-order items L8 and L9).

## Risks

The mirror contains our output and has unverified physical-camera identity,
field of view and exposure timing. A mask must cover the complete HUD footprint;
it is not compositor exclusion. Arrows remain approximate.

Detection quality on representative Frame input remains unproven. Both models
missed the expected TV in E61, neither has a rug class, and a successful capture
or preview does not establish accuracy. Physical green-input failures also
remain unexplained in committed evidence.

Third-party reports describe `SetOverlayRaw` freezing after about 200 updates.
[E81](evidence/e081.md) records a 400-frame replay-panel run that kept updating
to the end with clean cleanup. That one device/runtime observation does not
resolve the upstream reports or validate the future live HUD path. The
[dated risk analysis](history/live-capture-2026-11-07.md#risks) preserves source pins;
keep upload changes evidence-led.

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
