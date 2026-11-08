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
lane F, the fallback build, opened on 2026-11-08 at the D2 exit.
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
| A6 | Calibration record version 2 carrying the mirror's native format metadata | M | none |
| A7 | Superseded on 2026-11-08 by the fallback (D2 exit, [E90](evidence/e090.md)): diagnostic gate table, scope record and call-site changes for the mask path; not started | M | superseded |
| A8 | Superseded with A7 (mask path) | M | superseded |
| A9 | Tracking reporter integration behind its flag (a D1 diagnostic, not on the fallback's path; deferred, not superseded) | L | E1, a later decision |
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
| B10 | Phase 2 guard-enforcement test scope for the agent runner: the bounded negative test (what the guard must block, how each refusal is observed), the trust-dialog precondition check, the report the device returns, and the operator steps; cloud work, the device run under its own approval | S | B8 |

**Lane C, detector evaluation.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| C1 | One named POC threshold constant and one named live-budget constant; commands import them | S | the owner's budget number |
| C2 | A test that `evaluation/local/` stays ignored | S | none |
| C3 | Box entry in the session manifest helper, with documentation | S | none |
| C4 | Per-label box recall and a basename-only input path in the report | S | C3 |
| C3b | A Qt box-annotation tool that writes the manifest's objects entries, so the owner can draw the boxes on the Frame desktop | S to M | C3 |
| C5 | The representative evaluation session: at least twenty boxed chair stills, scored file-only, plus one `mirror_recognize` run per detector for inference p95 | M, device | C1 to C4, approval |
| C6 | WP1: a native-resolution colour still of the E61 scene evaluated beside the grey preview | S, device | approval; shares C5's session |
| C7 | Stale script names in E8, E67 and E69 | S | none |
| C8 | The E80 re-run manifest records its input by the pre-move absolute path, so a repeat with `--require-input-digests` fails; make the recorded path relative and document the repeat ([E86](evidence/e086.md)) | S | none |

**Lane P, packaging.**

| Item | Work | Size | Depends on |
| --- | --- | --- | --- |
| P0 | Python 3.14 on the Frame after SteamOS 0.5.8: pin review, 3.14 pins behind markers, setup scripts accept 3.12 and 3.14 (added 2026-11-07 when the update removed Python 3.12, [E85](evidence/e085.md)) | M | none |
| P1 | Hashed locks for the three requirement sets; add the missing `av` pin to the dev set; setup scripts use the locks | M | P0 |
| P2 | Active command bodies move into the package with thin script wrappers and console entry points; validator references and manifest pins follow | M | P1 |
| P3 | A bounded retry with backoff in `sightcircuit/assets.fetch` for transient network errors (timeouts, resets) on the pinned fixture downloads, with the digest check unchanged and a test; two CI re-runs on 2026-11-08 were caused by one release download timing out on the runner | S | none |

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
| F2 | `OverlayClient` event queue and a cycle API on the sink | S | F1 |
| F3 | Device measurements: hide-to-absence latency, hidden-event delivery, loopback repeats, show-to-visible; one evidence entry each | S each, device | F1, approvals |
| F4 | Capture window and admission rule in the pipeline, with rejection counters and boundary tests | M | F3 |
| F5 | The fallback stage: hide, window, infer, show on `_run_latest`; tile rendering without a mask; run reports | M | F2, F4 |
| F6 | Acceptance rows and gate references for the fallback, a reviewed edit to scope, this work order and `sightcircuit/gates.py` | S | planner and owner |
| F7 | Command and operator guide; the 60-second run under the diagnostic tier's scope record | M, then device | F5, F6 |
| F8 | Retire or quarantine the dead mask stack and its documentation | M | an accepted fallback run |

Lane A's device steps consume the D2 attempt budget: A4 is the first counted
attempt, A5 the second and last. An attempt counts when the observer judged the
scene (a precondition rejection, or anything after the marker tile appears); a
stop caused by connectivity or preparation never counts, whatever its position
in the run. The [counting rule](poc-scope.md#architecture-decisions-2026-11-07)
and the [operator table](guides/hud-checkpoint.md) carry the full list.

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
E1 (#38), F1 (#41) and B9 (#40) merged on 2026-11-08. Open: F2 (in
progress) then F3 to F8, B10 (in progress), P3, C5 with C6 (approved, staged on
the Frame, waiting for the owner's start), A9 deferred; A7, A8 and A10
superseded. The Frame's built bridge predates E1 and F1 and is rebuilt only
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
- The Phase 2 guard-enforcement test is scheduled (owner, 2026-11-08): B10
  writes its scope; the owner accepts the workspace trust dialog once on the
  Frame before the run ([E86](evidence/e086.md)); the run gets its own approval.
- Decided 2026-11-08: the fallback (D2 as written; [scope](poc-scope.md#architecture-decisions-2026-11-07)),
  C5 with C6 approved (staged by the Frame manager, E91 reserved), the Phase 2
  test scheduled. Next device approvals, each separate: the F3 measurements
  (after F1), the Phase 2 enforcement run (after B10).
- The Frame rebooted into SteamOS 0.5.8 ([E85](evidence/e085.md)); a Frame
  manager session exists and the "Frame update check" scheduled task was
  rebound to it on 2026-11-07, with the old task left disabled as a record.

## Edge HUD acceptance gates

A gate passes only on its required evidence. Offline tests, ARM64 CI and a
successful process exit are not headset acceptance. A failed device run remains
recorded; investigate it before proposing a changed run.

| Gate | Required evidence | Accepted state in this checkout |
| --- | --- | --- |
| G1. synthetic mask and expiry | Native full-footprint masking, margins/eye union, changed-context rejection and independent stale expiry | Passes offline; [E69](evidence/e069.md), [E74](evidence/e074.md), [E78](evidence/e078.md) |
| G2. lifecycle and cleanup | Explained software termination, truthful cleanup reports, and physical recovery evidence; the accepted alternative permits an owner-rehearsed recovery procedure when the cause remains unknown | **Closed November 3, 2026**, on the owner’s confirmation after rehearsal; [E79](evidence/e079.md). The green-input cause remains unknown, and the clean rehearsal does not prove the procedure cures green input |
| G3. mapping observed on Frame | Bounded marker/observer report ending `observed_unqualified`, with clean cleanup and normal passthrough confirmed | Open. V4 was rejected; the November 3 attempt stopped at `observer_contour_budget` before any marker was shown ([E83](evidence/e083.md)); the November 7 attempt passed the precondition, showed the marker and matched its whole pattern, then rejected on the tag decode at the mirror's scale ([E89](evidence/e089.md)); the November 8 attempt, after the reviewed decode change, accepted one complete decoded copy at 0.885 of native and then rejected on marker movement of about 45 px between two reads 0.2 s apart while the wearer turned, against the 8 px sampled-drift limit ([E90](evidence/e090.md)). The D2 budget is exhausted; the exit rule is reached and the owner's decision is pending |
| G4. mapping qualified | Reviewed evidence for eye composition, all HUD copies, conservative margins, operating limits and exact context | Proposed method; device qualification pending |
| G5. feedback exclusion and physical expiry | HUD visible during head motion with no detections from its footprint; independently observed expiry within the declared bound | Not run in committed evidence |
| G6. combined live HUD | The [60-second POC acceptance](poc-scope.md#acceptance), with the report fields below | Not run. The POC object is chair; the 400-frame replay panel continued updating on the tested device/runtime, but that is not combined live acceptance; [E81](evidence/e081.md) |

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
