# Architecture council, 2026-11-07

> Dated record. It preserves the decision memo put to the owner, the owner's
> choice, and the three independent code reviews that checked the chosen
> direction against revision `45771fe`. File and line references are to that
> revision. The accepted decisions live in
> [scope](../poc-scope.md#architecture-decisions-2026-11-07); the planned
> structure in [architecture](../architecture.md#planned-structure-2026-11-07);
> the work order in [current work](../live-capture.md#architecture-plan-work-order-2026-11-07).
> Nothing here is a device result, opens a gate or authorizes a device step.

## How the planning ran

The coordinator read the handover, the maintained documents and the code, then
put a decision memo with eight decisions to the owner. The owner accepted every
recommendation on 2026-11-07. Three reviewer sessions then each took a lens
(runtime and lifecycle; HUD and qualification; detection, packaging and the
bridge), received the chosen options and the code but not the memo's reasoning,
and returned findings anchored to file and line. The coordinator folded the
findings into the plan, resolved the planner-level questions and listed the
owner-level ones in the work order.

## Decision memo (condensed)

| Decision | Chosen | Alternatives not taken | Reason given |
| --- | --- | --- | --- |
| D1 End state | The fixed edge HUD is the product of this phase; pose and projection stay out; the native tracking reporter is a diagnostic behind its flag | Shape the bridge and pipeline for world-locked labels now | Every unproven link is on the HUD path; a pose path adds another before the first closes |
| D2 Input strategy | Mirror plus full-footprint mask with a bounded budget of G3 attempts and a written exit to a drafted fallback (time-multiplexed exclusion), which is costed now and built only if the budget is exhausted | Wait for a vendor camera interface; revive the provider track | The mask approach is implemented and offline-validated and is one approved run from new evidence; two failures without an exit rule is how the provider track absorbed October |
| D3 Qualification depth | Two tiers, each a reviewed change to the gate table: a one-run diagnostic tier for the 60-second acceptance with a union-of-all-copies mask and a narrow envelope; full G4 for production | Full G4 including eye identity before any real output; drop eye identity for everyone | The acceptance is a supervised 60-second run; the union mask gives exclusion without eye identity; the one-run scope keeps the diagnostic from drifting into production |
| D4 Lifecycle | One supervision primitive in the runtime package, adopted runner first, live pipeline next, capture supervisor last; Python owns escalation and the verdict; split the bridge source before integrating the reporter | Keep three implementations and fix findings where they sit | The cancellation race cannot be root-caused three times |
| D5 Packaging | Hashed lock files from the existing pins; console entry points for active commands with thin script wrappers; paused scripts left in place | Status quo; also move paused scripts | Pins exist, so a lock is cheap and makes cloud and Frame comparable; moving paused scripts churns documentation |
| D6 Evaluation | Define the representative set (at least twenty boxed native chair stills, annotated and scored on the Frame, JSON only leaving it) and the default rule before any model change; WP1 closes by attribution afterwards if no cause is found | Add a third candidate model now | A third model without a representative set is a third unexplained number |
| D7 Agent runner | Stays in the package as operations tooling outside the production gates, with its own evidence path | Separate repository | It shares the lifecycle primitive and the suite; a split doubles bookkeeping for one device |
| D8 Tests and CI | Keep the layout; root-cause the cancellation race inside D4; add a suite time budget | A quick pull-request lane with the full suite on main only | The trim just landed; a quick lane re-creates the vacuous-pass risk |

Baseline measured by the coordinator on 2026-11-07 in the cloud checkout at
`45771fe`, after `scripts/setup_window.sh` and the native builds: 1,145 tests,
58 skips (capture fixtures not built), 303 seconds on the cloud runner. Ten
replay-window tests fail when the Qt system libraries are missing and pass
once they are installed; that is an environment condition, not a defect.

## Council findings

### Review 1: runtime, lifecycle, agent runner, tests and CI (D4, D7, D8)

#### Inventory

Child supervision exists six times:

| Site | Child | Grace and escalation | Verdict |
| --- | --- | --- | --- |
| `sightcircuit/runtime/guard.py:181-355` `_supervise_owned` | owned session, guardian and subreaper (`:112-125`), lifeline pipe | SIGTERM, wait 5 s (`:40`), killpg TERM 1 s, KILL 1 s; reader join 0.2 s (`:322`) | `cleanup_ok` only if the worker claimed it and nothing was forced (`:350-352`) |
| `sightcircuit/runtime/process.py:45-116` `run_bounded` | multiprocessing spawn, parent-death signal (`:23-38`) | event, join 0.5, terminate 1, kill 1 | `cleanup_ok=False` if forced or no result (`:112-113`) |
| `sightcircuit/agent/runner.py:306-323` `kill_group` | `start_new_session` task or git | killpg TERM, `/proc` scan of the group (`:272-291`), KILL after 10 s (git 5 s) | `leftover` processes fail the task (`:363-374`) |
| `scripts/capture_supervisor.py:221-237` | launcher in its own session | killpg TERM, grace 0.3, KILL 1 | `api_cleanup_confirmed and not killed` (`:263`) |
| `sightcircuit/overlay.py:244-309` `OverlayClient.close` | bridge | stop packet or EOF, wait 2, terminate 2, kill 2 | `bridge_closed_cleanly` (`:32-43`) |
| `scripts/shm_capture_check.py:97-116` | reader binary | terminate 1, kill 1 | own copy |

Only the guard reaps by process group (`waitpid(-pgid)`, `:128-136`).

Signal deferral has four semantics: `guard.cancellation()` (`:90-108`) records
the first SIGINT, SIGTERM or SIGHUP, polled, and overrides an inherited ignore;
`runner.Interruption` (`:74-117`) raises on the first signal unless held, and
honours an inherited ignore by design (`:110-112`, tested at
`tests/test_frame_agent.py:339`); `capture_supervisor.recorded_signals`
(`:66-103`) swaps handlers and replays them, with a real pthread mask in
`deferred_signals` (`:46-63`) and ignore-during-cleanup in `cleanup_signals`
(`:32-43`); `guard.sigterm_cancels()` (`:60-67`) and `stop_on_signals()`
(`:82-87`) serve the launcher and worker sides.

Stall watchdogs: the guard deadline (`:261-271`); the bridge's SIGALRM phases
(`tools/overlay_lifecycle.hpp:100-128`, `overlay_bridge.cpp:43-45,631,639,700,717`);
the observer's baseline and marker deadlines (`scripts/hud_marker_observer.py:139-157`);
the runner's per-task timeout and run budget (`runner.py:357,392-399`). The
`Pipeline` has none, only `seconds`. Cleanup reporting uses five vocabularies.

#### Feasibility of D4

The primitive must offer: spawning into an owned session with optional file
descriptors; a hold-and-arm protocol around `Popen`; group-wide termination with
reaping, not leader-only; deadline-based cleanup verdicts instead of fixed
sub-second joins; a declared policy for an inherited ignored signal; one report
block. The best seed is `guard._supervise_owned` with `child_reaper` and
`_reap_members`; its weaknesses are the polled list and the fixed joins at
`:297` and `:322`.

Real requirements versus accidents: the runner needs raise-on-signal (it must
stop a git mid-fetch and release a lock, `runner.py:147-153`) while the guard
needs record-and-poll, so both modes are legitimate. The runner must honour an
inherited ignore (nohup, `runner.py:25-26`) while the guard overrides it; the
note that SIGINT subtests fail when the test process starts with SIGINT ignored
(`coder-handoff-2026-11-07.md:92`) is exactly this divergence
(`test_frame_agent.py:265,639`), so the policy must be a parameter and the suite
entry should normalise the SIGINT disposition. Grace numbers are tunables. The
`/proc` scan versus `killpg` is an accident; keep `killpg` plus reaping.

Hazards the primitive removes: the SIGINT and SIGTERM pending pair
(`capture_supervisor.py:94-96`) through record-first-only (`guard.py:96-98`); the
default-disposition replay that ends the process without unwinding (`:99-100`)
by never re-raising; `frame=None` handlers vanish with the replay. One hard
point: the `Pipeline` has no supervision, so "the live pipeline adopts the
primitive" means the launcher (`scripts/live_pipeline.py:76-78`) and
`sources/mirror.launch` (`mirror.py:212-229`, which uses `process.run_bounded`).
Retiring `process.py` requires the guard to stream progress reports, which it
cannot today (`:205-218` reads one bounded JSON blob).

#### The bridge

`tools/overlay_bridge.cpp` by concern: lifecycle at `:38-45`, `:303-332`,
`:588-598`, `:630-642`, `:699-719`; overlay rendering in `Overlay` `:106-302`,
`stream` `:333-407`, marker drawing `:408-476`; context sensing in `view_record`
`:180-227`; watchdog `:52-65` with `overlay_lifecycle.hpp:37-128`; reporter in
`emit` `:46-51` and the report assembly `:543-629`, `:691-718`.

It has no child, but it does supervise itself: the SIGALRM watchdog exits the
process (`overlay_lifecycle.hpp:63`), it stops on the parent-death signal
(`:594`), and marker mode has its own display deadline (`:640-668`). Python
cannot interrupt a hung `VR_Init`, so D4 is refined: the bridge may end itself;
the Python side owns escalation and the verdict.

Realistic split: `overlay_lifecycle.hpp` takes `read_exact`, `stopping` and
signal setup; a render header takes `Overlay`, `stream` and marker drawing; a
report header takes `emit`, the watchdog message and the report scaffold; a
sensing header takes `view_record`. Two tests pin source text
(`tests/test_overlay_bridge.py:180-181,330-332`) and `runtime/manifest.py:41-42`
pins the native sources; both move with the split.

The reporter branch (tip `4f15c20`) predates the merged lifecycle work and
conflicts in `emit`, the whole `Overlay` class, `events()`, `read_exact`, all of
`main`, `hud/contract.py`, `manifest.py:41`, `overlay.py` and
`sources/session.py:350`. Integrating after the split lands it as one unit
instead of six conflicting blocks.

#### The cancellation race

The chain in `tests/test_bounded_worker.py:376-399` is test, `supervise_synthetic`,
guardian, worker; the test waits for the worker to catch SIGTERM and then signals
the top process. Three paths yield `cancelled` with `cleanup_ok` false: the worker
joins its capture thread for 0.5 s (`live.py:241`, `pipeline.py:509`) and reports
`source_cleanup_ok` false (`live.py:228-229`); the guardian joins its reader for
0.2 s (`guard.py:322`) and reports `worker_report_incomplete` (`:342`); the outer
supervisor's identical 0.2 s join (`:468-469`). Best hypothesis: the first or
second, a scheduling delay turned into a verdict by a fixed join, which is why
only loaded ARM64 runners show it (`e084.md:34-37`). The confirming experiment:
print the report at `:397-398`, loop the test fifty times under CPU stress and
read `reason`, `source_cleanup_ok` and `report_reader_stopped`. The fix inside
D4: join until the report is complete or the remaining cleanup grace elapses,
and let the worker's join share that deadline.

#### D7 and D8

Couplings of `sightcircuit/agent` to production: `runtime/manifest.py:142`
hashes every package module, so agent changes alter the checkpoint software
identity; `status-data.json:136` carries a capability row; `check-jobs.yml:213`
pins `test_frame_agent*`; `tasks.py:61-63` runs the suite as a Tier A task. No
production code imports the agent package. "Outside the gates" therefore means:
no claim in `gates.py`, a separate group in the checkpoint manifest, its own
evidence ids and its own test timing budget (`test_frame_agent.py` has 70 tests
with 60 s waits, `:53-58`).

The suite has 1,149 test functions across 64 modules; 31 modules spawn
subprocesses; CI runs the suite three times per workflow
(`check-jobs.yml:166,191,202`) without recording durations. A budget needs
per-module wall time from `run_tests_no_skips.py`, a committed ceiling and a
failure when exceeded. Fixtures that sleep 30 or 60 seconds on a failed
handshake (`test_frame_agent.py:203`, `test_mirror_check.py:90`,
`test_frame_claude_install.py:63`) can add minutes silently.

#### Open questions from review 1

Whether the bridge's self-exit is permitted (resolved: yes); whether the
primitive streams progress or the mirror launcher keeps a second supervisor;
whether inherited-ignore-stays-ignored is universal or runner-only; whether
`agent/` belongs in the checkpoint manifest; who owns the reporter's
`--connect-as` decision; what counts as "race fixed".

### Review 2: HUD, mirror, qualification and gates (D2, D3)

#### Gate map

`gates.py:36-46` defines three gates; `require()` permits only the literal
`True` (`:26-33`, pinned by `tests/test_gates.py:36-40`). Syntax-tree tests forbid
any refusal outside `require()` and any hand-written claim key
(`test_gates.py:53-77`). Worker-report contracts require every claim to be
false (`hud/observation.py:554`, `hud/marker.py:122-123`,
`scripts/live_pipeline.py:55-67`), so a diagnostic tier must not flip production
claims and needs its own report block.

| Gate | `require()` sites | Refuses today | Diagnostic tier change | Pinned by |
| --- | --- | --- | --- | --- |
| Live image input | `runtime/live.py:73` via `run_pipeline` `:214` and HUD-mode `run_recognition` `:331,347` | any source whose kind is not synthetic; non-HUD mirror recognition never consults the gate | permit a mirror source whose identity carries a valid one-run scope | `test_live_pipeline.py:110-115`, `test_hud_mask.py:395-410`, `test_mirror_session.py:724-727` |
| Hardware mapping | `hud/mask.py:128-131` (with the `validated` check `:126`, from provenance `contract.py:259-261`); `qualification.py:280` | any calibration not a synthetic fixture | accept a new provenance under scope; `Calibration._issue` callers pinned at `test_hud_calibration.py:92-99` | `test_hud_mask.py:130-155`, `test_hud_qualification.py:260-272` |
| Live HUD output | `overlay.py:216` (`freeze_settings`), `:231` (expiring frames) | any non-validation client; `OverlaySink.open` hard-codes validation (`:354`); the bridge rejects expiring frames in real mode (`overlay_bridge.cpp:369-370,645`) | Python sites plus a bridge change and header regeneration (`hud/native.py:131-138`) | `test_display_expiry.py:187-191`, `test_overlay_bridge.py:378-419`, `test_hud_mask.py:501` |

Smallest honest shape: keep `Gate`; add a second frozen table of three
diagnostic gates with their own claim names and acceptance rows (new rows are
required by `test_gates.py:30-34`); a scope record issued only by a factory, on
the `Calibration._issue` pattern (`contract.py:247-256`), whose permission
check returns the literal `True`; each call site becomes "synthetic condition or
scope permits this gate". A tier field on `Gate` would leave the decision at the
call sites.

#### Union-of-copies mask

Eye identity is required at `Footprint.eye` (`contract.py:200-203`), one
footprint per eye (`mask.py:51-65`), a declared eye per copy with every eye
covered (`qualification.py:178-197`), one copy per eye in `calibration()`
(`qualification.py:266-268`), and each observer copy inside exactly one declared
viewport (`candidate.py:80-84`). Regions are already a tuple of rectangles
(`mask.py:61-67`) and `union_area` exists (`geometry.py:61-72`), so the union
is structurally cheap; the blocker is the per-eye cardinality rule. The observer
never assigns eyes (`observation.py:410`) and records per-copy unions
(`:399-402`). Residual eye dependency the union does not remove: arrow direction
uses the eye viewport (`mask.py:265-269`), eye-crossing rejection
(`geometry.py:39-49`) and the fill reads inside the region's eye (`mask.py:70-90`).
The capture layout must still be declared, and dimensions are not evidence.

Two things reject every real mirror frame regardless of eyes. A mirror
`native_format` carries V4L2 metadata (`mirror_device.py:231-234`,
`mirror.py:183`), kept by `CaptureFormat.from_mapping` (`contract.py:170-173`);
equality fails at `mask.py:151` and `context()` raises
`hud_capture_metadata_unrepresentable` (`contract.py:287-289`); the record has no
metadata field (`qualification.py:109-113`). And `--from-observer` rejects any
report whose tracking tokens are undeclared (`candidate.py:217-219`); no
reporter exists, so no real observer report can become a candidate today.

One-run scope: `runtime/manifest.py:138-152` already pins checkout, bridge,
modules, scripts and packages and verifies them read-only; the candidate carries
`mapping_id`, `expires_ns` (at most 300 s, `contract.py:101`) and digests, with
`check_context` (`qualification.py:226-233`); exclusive-create patterns exist
(`manifest.py:215-226`). Missing: a scope record binding manifest, candidate and
model digests to an absolute deadline in a named timebase, a consumed run id and
a reader. The observer's `approved_scope` string (`hud_marker_observer.py:268-269`)
is neither signed nor single-use. The device report template already has
`run_id`, `artifact_manifest`, `mapping_record_sha256` and
`diagnostic_build_sha256` fields that nothing reads.

#### G3 budget

`--analyze-still` runs the same statistics as the observer precondition
(`scene.py:201-242`, equality tested at `test_observer_scene.py:280`). It cannot
predict provenance (`scene.py:458-461`), the marker phase (each copy adds about
six quads and two outlines, `hud-observation.md:191-192`), the baseline (two
distinct clear frames in 2 s, `observation.py:290-332`), matcher success
(`hud-observation.md:264-271`) or Frame timing (248 ms worst on x86 against a
200 ms period, `hud-observation.md:195-199`). E83 left unknown the bright
fraction, speck count, quads, outlines and dimensions. A statistics-led
revision reads the rejected run's schema-2 statistics
(`hud_marker_observer.py:188-193`) and changes one constant in `scene.py:55-65`,
which means a schema-3 bump with its table and boundary tests
(`scene.py:81-88,293`, `test_observer_scene.py:159,367`).

Other stops before a marker: a missing bridge build (`marker.py:45-46`); the 8 s
ready wait (`marker.py:16`) against the bridge's 6 s connect watchdog covering
three probes and three init attempts (`overlay_bridge.cpp:43`,
`overlay_lifecycle.hpp:144-145`), new since E83's bridge; `headset_not_connected`
(`overlay_bridge.cpp:167`); after the marker, only 2 s of tolerance for an
incomplete marker (`hud_marker_observer.py:145,152`). The V4 tag mismatch (E75)
is untouched by O3 and remains the likeliest stop after a marker appears.

#### Fallback: time-multiplexed exclusion

Reused: `mirror.capture` and `MirrorSource` (`mirror.py:46-192`), `LatestSlot`
and `_run_latest` (`pipeline.py:448-513`), the admission hook
(`pipeline.py:313-318`), the capture context without its mask, `draw_tile`, the
overlay settings path (`overlay.py:206-224`), bridge show and hide
(`overlay_bridge.cpp:239-248`) and expiry hide (`:339-342`), the baseline gate's
digest change detection (`observation.py:319-331`). Bypassed: the mask and fill,
the candidate, the observer and scene precondition, prediction, mask-related G5
counters.

New contract: the bridge emits timestamped hide-requested, hide-returned and
overlay-hidden lines (today the hidden flag is reported only at marker exit,
`overlay_bridge.cpp:281-282,694`); a capture window admits a frame only if its
observation time is at least a device-measured bound after the confirmed hide
and its digest differs from the last pre-hide frame (a repeated loopback frame
is the central hazard; `camera_freshness_verified` is false everywhere,
`mirror.py:42,124`); show only after the window closes; expiry stays on the
existing clock, but the maximum display age is shared with the bridge
(`hud_contract.hpp:69`). Absence cannot be proven by the matcher, so the bound
is time-based plus a one-off device measurement. Device evidence needed:
hide-to-absence latency, loopback repeat behaviour, and that a hidden overlay is
absent from the mirror at all. Costs: at five reads per second the tile is off
for the bound plus the read time out of every 200 ms, so flicker is likely; one
or two reads per second pushes input age toward the 1 s cap (`pipeline.py:246`).
Size: large.

#### Dead and load-bearing

Under D3 the per-eye declaration rules become partly vestigial for the
diagnostic record but stay for G4. Under the fallback, `mask.py`, `candidate.py`,
most of `qualification.py`, the matcher, `scene.py`, `marker.py`, prediction and
`mask_fill_evaluation.py` (about 2,800 lines of code and 4,000 of tests) go
dead. Load-bearing and under-tested on either path: bridge real-mode configure
and visibility (validation mode skips it, `overlay_bridge.cpp:378`; no test
drives hide and show timing); `OverlayClient._write` ordering and heartbeat
(`overlay.py:180-204`); `_expire_presented` granularity (`pipeline.py:486-492`);
`MirrorSession` on a real `/proc` (`hud-session.md:44,52`); `runtime/sensors.py`
reads load and thermal only, so nothing monitors head motion or display state
for the "monitored" envelope.

#### Open questions from review 2

Who declares the capture layout for the diagnostic run and on what evidence;
whether the observer run, conversion and 60 s run must fit one 300 s mapping
lifetime under one approval; whether "no recenter" relies on the reporter or on
the wearer's report; the unmeasured Frame CPU cost of the 1080p mask, fill and
detector; whether a run stopped before bridge initialisation counts against the
attempt budget; how hide-to-absence would be measured for the fallback.

### Review 3: detection, evaluation, packaging and bridge scope (D1, D5, D6)

#### Detector and evaluation

`DETECTORS` and `DEFAULT_DETECTOR = "mobilenet-ssd"` (`sightcircuit/detection.py:268-269`);
`create_detector` (`:279`) is the only factory; every command exposes
`--detector` with that default (`scripts/replay_window.py:143`,
`scripts/mirror_recognize.py:103`, `scripts/live_pipeline.py:87`). YOLOX runtime
options (`detection.py:167`) are not reachable from any command. The harness
`evaluation.score_image` (`sightcircuit/evaluation.py:123-181`) scores presence
and, when objects carry `box_xyxy` in source pixels, per-object IoU matches at
0.5, confusions at 0.3 and, with `boxes_complete`, false positives (`:156-179`,
`:202-207`). Box recall is aggregated over all labels (`:200-201`); no per-label
chair box recall exists. The manifest helper refuses boxes
(`scripts/frame_session_manifest.py:4`); the device session script collects
presence labels by prompt (`scripts/frame_session.sh:135-169`). The evaluator
records per-image inference time, mean and maximum (`evaluate_detectors.py:150-169,291`)
with no percentiles; percentiles exist only in `PipelineStats.report()`
(`pipeline.py:196-222`) through `run_recognition` (`runtime/live.py:374-376`).

No named POC threshold or live budget exists in code; 0.5 is repeated as a
literal in seven places. The nearest budget constants are the 1,000 ms maximum
display age (`hud/contract.py:89-90`) and five frames per second (`pipeline.py:236`).
A boxed, file-only, on-device evaluation with JSON-only export mostly runs with
existing code (a manifest with objects, `--require-input-digests`, a report under
`evaluation/local/`; the report embeds only digests, fingerprints and boxes,
`evaluate_detectors.py:105-111`). Gaps: no box-entry helper; no per-label box
recall; the report's `input_path` is an absolute device path (`:156`); the live
budget must come from a `mirror_recognize` run's inference p95. Private stills
are kept out only by `.gitignore:28`; the guard blocks reads
(`agent/guard.py:61,118-121`); no test asserts the ignore rule.

#### WP1

E80 established that the still exists with a verified digest, that the miss
reproduces (YOLOX 0.427, MobileNet-SSD below 0.05) and that all four audit
variants fed byte-identical inputs because the still is already a 640 by 360 grey
preview. That rules out the evaluator's preprocessing, not the capture-time
preview conversion (`evaluate_detectors.py:29-36`), since no colour native still
of that scene exists. Attribution needs a native-resolution colour still of the
same scene evaluated beside the preview; a YOLOX threshold sweep across the D6
set; and a re-run of E61's commit for the bottle change. Without the first,
attribution is by exclusion only.

#### Packaging

`pyproject.toml` declares setuptools, Python 3.12 only, no dependencies, extras
as ranges, no console scripts, and packages limited to `sightcircuit*` (`:11-27`).
`requirements-dev.txt` omits `av` although the dev extra needs it; ruff is
installed through `uvx`. `scripts/setup.sh:25` uses `uv pip sync`; the Frame and
the replay job use `pip --only-binary=:all:`, so a `uv.lock` would be unusable
there; `uv pip compile --generate-hashes` emits requirements-format locks both
consume. Hashes must cover x86_64 and aarch64 wheels; whether one lock serves
both is undetermined. Console entry points cannot target `scripts/`, which is
not in the wheel, so command bodies move into the package with thin wrappers;
that is compatible with `tests/test_scripts_boundary.py:46-65`. Hazards:
`live_pipeline.py:76` and `hud_marker_observer.py:295` re-spawn themselves by
file path; guard validators are imported by dotted name
(`runtime/guard.py:359,385-398`); `runtime/manifest.py:38-42` pins marker scripts
by hash; tests import `scripts.*` modules and call their `main`;
`test_report_characterization.py` must pass unchanged. Classification: 27
active library-backed commands, 9 paused (`FROZEN_CAPTURE_TRACK`,
`test_scripts_boundary.py:12-15`), two provider-adjacent but not quarantined
(`capture_gpu_check.py`, `shm_capture_check.py`), three device-only shell
scripts, seven build and three setup scripts. The D5 list omitted
`mirror_check` and `frame_session_manifest`. Stale script names remain in
`e008.md:7`, `e067.md:18` and `e069.md:18`.

#### D1 and the bridge

The reporter branch adds `tools/tracking_report.hpp`, a `TrackingSystem` and
reporter mode in the bridge, contract constants, `OverlayClient(tracking=)` and
about 1,200 test lines. Everything is reporter-specific; main lacks no lifecycle
fix from it, and the branch lacks main's probe-then-connect, watchdog and
`--connect-as` work (`tools/overlay_bridge.cpp:41-45,126-177,588-592`). The
reporter hashes zero-pose transforms into an identity token; it logs no per-frame
pose. The refinement D1 needs: `tracking_origin` and `recenter_epoch` are session
tokens (`hud/contract.py:209`); a bound mask rejects every frame when they are
undeclared (`hud-session.md:60`); the observer builds a `TrackingObserver`
nobody feeds (`hud_marker_observer.py:273-275`); the candidate rejects unobserved
tokens (`candidate.py:219`). So without the reporter, no candidate with session
tokens converts and no real-mirror G5 run admits a frame: "integrated when
qualification needs it" means before the diagnostic run. Other pose code on main
(`hud/geometry.py:80-333` prediction, the read-only `--headset-view-record`
`overlay_bridge.cpp:178-227`) is fenced with `qualifies_mapping: False` (`:287`)
and stays fenced; `runtime/sensors.py` is load and thermal, not pose.

#### Open questions from review 3

Whether the tracking tokens stay required; whether chair recall is per-still
presence or per-box IoU; whether the live budget binds p95 or maximum, measured
in `mirror_recognize` or `live_pipeline`; whether `mirror_check` and
`frame_session_manifest` are active for entry points; one hashed lock for both
architectures or two; whether attribution by exclusion closes WP1.

## Coordinator's resolutions

Planner-level questions were settled as follows and recorded as coordinator
calls in the [decisions table](../poc-scope.md#architecture-decisions-2026-11-07).
Owner-level questions are listed in the
[work order](../live-capture.md#decisions-still-needed-from-the-owner).

- The bridge may end itself on its watchdog or parent death; the Python side
  owns escalation and the cleanup verdict.
- The primitive offers both signal modes (raise and record-and-poll) and takes
  the inherited-ignore policy as a parameter; the runner keeps honouring an
  inherited ignore; the suite entry normalises SIGINT for its children.
- Progress streaming is part of the primitive, so `process.py` retires only
  after it exists; until then the mirror launcher keeps its supervisor.
- `sightcircuit/agent` becomes its own group in the checkpoint manifest rather
  than leaving the manifest.
- The tracking tokens stay required, as decided on 2026-11-03, so the reporter
  integration precedes the diagnostic run and ranks after the bridge split.
- Chair recall is per-box at IoU 0.5 on the boxed set; the live budget is the
  inference p95 from a `mirror_recognize` run, with the number itself an owner
  decision; one named threshold constant and one named budget constant in code.
- One hashed lock per requirement set, carrying both architectures' hashes,
  verified on the Frame when its setup next runs.
- `mirror_check` and `frame_session_manifest` join the active entry points.
- An attempt counts against the D2 budget when it reaches the observer's
  precondition or later; a stop for a missing bridge build, a missing headset or
  a failed manifest check is a preparation failure and does not count.
- "Race fixed" means fifty consecutive green runs of the cancellation test under
  CPU stress on the cloud runner, then one green ARM64 run on main.
