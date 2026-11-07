# Pipeline and runtime reference

Read [architecture](../architecture.md) for the component overview and
[development setup](../development.md) to run checks. This reference explains
frame ownership, scheduling, reports, timing, and process supervision.
Implementation and cloud-test details are distinct from [Frame evidence](../status.md).

## Python package and pipeline

Reusable code lives in the `sightcircuit/` package; `scripts/` keeps the
documented command-line entry points. No active script imports another, and
`sightcircuit/` never imports `scripts/`; `tests/test_scripts_boundary.py`
enforces both (the paused capture-provider scripts are exempt while frozen). Scripts add the checkout root to `sys.path`
when run as files, so headset commands need no installation step.
`pip install -e '.[window]'` (or `uv pip install -e .`) also works for desktop
development; exact tested pins remain in the `requirements-*.txt` files.

| Module | Responsibility |
| --- | --- |
| `sightcircuit/frame.py` | `Frame`, the only frame type (pixels, layout, source, sequence, observation time, media time, declared `native_format`), with `Frame.owned_bgr` (validated private BGR copy) and `owned()` (the engine's copy on publish); `DetectionResult`, which every detector returns; `Processed`, the one record every engine submits to sinks (annotated image or HUD tile with `HudOutput` rows and exclusion checks, expiry, completion time, sizes and coordinate space) |
| `sightcircuit/pipeline.py` | `FrameSource`/`Sink` interfaces and `Pipeline`, the one engine for replay, mirror recognition and the synthetic live HUD: latest-only or every-frame scheduling, rate cap, `CaptureContext` admission, HUD stage or annotation, HUD exclusion counts, result expiry and deadline, optional sink `open`/`clear`/`close` with cleanup results. `PipelineStats.report()` is the one statistics vocabulary, including the [latency statistics](#latency-statistics): the replay, mirror and synthetic live reports embed it as `pipeline` (mirror and synthetic live once the engine is constructed, replay once its engine has started, on error too, with `statistics_final` false if its worker had not stopped; a report that ends earlier has no block and only the fields its command already wrote) and keep their earlier field names only as aliases (`report_fields`) until a schema version bump |
| `sightcircuit/latest.py` | One-pending-item handoff; newer items replace older ones |
| `sightcircuit/gates.py` | The closed approval gates (live image input, hardware mapping, live HUD output): each boundary's `require` and every report's gate claims; nothing here opens a gate |
| `sightcircuit/context.py` | `CaptureContext`: pins declared source identity, frame source/native layout/session metadata and an optional HUD mask binding (calibration source, format, layout, session tokens and lifetime); `admit(frame)` fails closed and latches the first failure |
| `sightcircuit/convert.py` | One native pixel rule (`buffer_bgr`: BGR24 arrays and padded RGB3/BGR3 buffers), `native_bgr` for frames and `to_bgr` for detector input of at most 960 pixels |
| `sightcircuit/detection.py` | `Detector` interface, `MobileNetSSD` baseline (model-free `decode_ssd()`), `YoloxDetector` candidate, the `DETECTORS` registry whose classes own their `model_files()` and `preprocessing()` description, `create_detector()`, separate `annotate()` and `require_result()` (rejects malformed detector output before any output) |
| `sightcircuit/preprocess.py`, `sightcircuit/labels.py` | Letterbox input and inverse box mapping; COCO vocabulary and VOC-to-canonical label names |
| `sightcircuit/evaluation.py`, `sightcircuit/assets.py` | Detector evaluation scoring; checksum-verified model/fixture download and verification |
| `sightcircuit/mask_fill_evaluation.py` | Offline comparison of HUD mask fills (the runtime's `hud.mask.fill_regions` and the candidates it was chosen over) on the gray sweep and the seed stills, used by `evaluate_mask_fill.py`; the runtime never imports it ([HUD mask fill evaluation](hud-development.md#hud-mask-fill-evaluation)) |
| `sightcircuit/sources/` | `ReplaySource` (PyAV); `standin` (the synthetic realtime-replay [stand-in clip](development-checks.md#continuous-integration)); `mirror_device` (read-only V4L2 SteamVR mirror discovery and reads); `mirror` (bounded acquisition loop `capture`, `MirrorSource` (its optional `session` declares the HUD session tokens and stamps each frame), and `launch`, the one launcher of both mirror commands: candidate selection, output reservation, the bounded worker and all-or-nothing publication); `recording` (one MP4 box-header decoder for finished-file inspection and the growing-file scan used by `recording_growth_check`; newest recording); `PolledSource` (adapter for `open`/`read`/`close` sources); `session` (read-only [session-token providers](../hud-qualification.md#session-tokens-for-the-mirror-source-2026-11-03) for a mirror run: `HostClock`, `RuntimeProcess`, `TrackingObserver` and `MirrorSession`). Adapters forward the wrapped source's declared identity |
| `sightcircuit/overlay.py` | Overlay bridge client and `OverlaySink`, the one panel sink: with a caller-owned client (replay's interactive panel) it only submits images; otherwise it owns a validation-only client through `open`/`clear`/`close`, optionally with the frozen HUD placement and payload contract |
| `sightcircuit/hud/` | Fixed edge HUD contract, shared eye/rectangle geometry (and the [geometric prediction cross-check](../hud-qualification.md#geometric-prediction-cross-check-2026-11-04), which never feeds qualification), the one margin rule, synthetic-only native mask (calibrations from two factories only, with expiry, session tokens and view fields; zeroed regions refilled from their surround by `fill_regions`), tile rendering, the `HudStage` engine stage (and `HudPipelineAdapter`, the stage bound to its sink's fixed output contract, used by `live_pipeline.py`), offline mapping qualification, the calibration marker client, `observation` (empirical marker-copy location in native pixels and the observer report contract, used by `hud_marker_observer.py`), `candidate` (an observer report to an unqualified mapping candidate, used by `hud_validation_report.py --from-observer`) and `validation` (the synthetic HUD run's ledger feed and G5 software counts) |
| `sightcircuit/runtime/` | `supervision` (the one [supervision primitive](#the-supervision-primitive): an owned worker session with an arm handshake, deadline-based cleanup, group termination with reaping, both signal policies, progress streaming and the worker side's parent-death guard, in one report block), `guard` (the primitive under the guard's policy, the Linux parent-loss guardian, and the one stop-on-signal convention: `sigterm_cancels` and `cancelled` for launchers, `stop_on_signals` for workers), `output` (reserve, write, publish all or none, withdraw and discard explicit image outputs; `publish_new` for finished bytes), `live` (`run_report`, the one report runner on `Pipeline`, used by `run_pipeline`, the gated synthetic run, whose HUD run also feeds the validation ledger, and by `run_recognition`, the mirror recognizer), `synthetic` (the generated source and its declared `SyntheticSession`, the fixture calibration factory and its candidate record, `FixtureDetector` and `HeadlessSink`) `manifest` (the [checkpoint artifact manifest](hud-development.md#checkpoint-artifact-manifest): software identity written and verified on Frame) and `sensors` (the read-only [run-context sensor sidecar](#run-context-sensor-sidecar)) |
| `sightcircuit/agent/` | On-device agent support: the fixed task table, the `frame_agent_run.py` runner (timeouts, lock, run budget, `result.json`), the PreToolUse guard and the Frame-only Claude Code settings template; standard library only ([plan](../agent-on-frame.md)) |

`scripts/hud_view_prediction.py` turns a saved headset view record into the
[geometric prediction](../hud-qualification.md#geometric-prediction-cross-check-2026-11-04);
it is a cross-check that never qualifies a mapping.

File replay uses every-frame scheduling, so each decoded frame is processed in
order. Mirror recognition uses latest-only scheduling with a one-second input
age limit. The V4L2 mirror device module and acquisition loop live in
`sightcircuit/sources/mirror_device.py` and `sightcircuit/sources/mirror.py`;
`scripts/mirror_check.py` is the diagnostic command. Both mirror commands start
their workers through `sources.mirror.launch`. `tests/test_report_characterization.py`
pins the report keys, value types and status and exit-code mapping of the
synthetic run, the mirror recognizer and both mirror commands. It was written
before these moved into the library and must keep passing unchanged.

Lint with the configuration in `pyproject.toml`:

```bash
uvx ruff@0.14.2 check .
```

### Latency statistics

The engine times every frame that reaches presentation, including a result
later dropped as stale, at the points where it already measured. The
`pipeline` block keeps its earlier mean, total and maximum fields and adds one
distribution per timing (all in milliseconds):

| Block field | Measures |
| --- | --- |
| `input_age_ms` | Observation (`Frame.observed_ns`) to the start of processing |
| `processing_ms` | Start of processing to a presented result: `stage_ms.prepare` + `detect` + `present` |
| `inference_ms` | The detector's own forward time (`DetectionResult.inference_ms`) |
| `output_age_ms` | Observation to the output boundary, before the sinks |
| `stage_ms.prepare` | Conversion to detector input; with a HUD stage, native masking and conversion |
| `stage_ms.detect` | The whole detector call, including its own pre- and post-processing |
| `stage_ms.present` | Result checks plus annotation (and its owned copy) or HUD rendering |
| `stage_ms.submit` | Every sink's admission check and `submit`, for results submitted to all sinks |

Each holds `count` (frames timed), `samples` (values behind the percentiles),
`p50`, `p95` and `max`, rounded to three decimals; the statistics are null
until a frame is timed. `percentile_method` is `nearest_rank`: the
p-percentile of n sorted values is the value at 1-based rank ceil(p × n / 100),
so it is always a measured value (n = 1 gives that value, n = 2 gives the lower
value for p50 and the upper for p95, and ties return the tied value). Memory is
bounded: each timing keeps at most 4,096 values (`TIMING_SAMPLE_CAPACITY`),
which makes percentiles exact up to 4,096 frames; beyond that a seeded
reservoir keeps a uniform sample of the whole run, so `samples` stays 4,096
while `count` and `max` cover every frame. Stage durations are read from
`Pipeline.timer` (`time.monotonic`); `tests/latency_fixture.py` installs a
stepping clock driven by a fake detector with known delays, so tests check exact
percentiles in the replay, mirror and synthetic reports. Ages are measured
against the source's monotonic observation clock.

### Run-context sensor sidecar

`scripts/sample_run_context.py` (library `sightcircuit/runtime/sensors.py`) is an
optional sidecar for latency runs. It writes one CSV row per interval of CPU
load, thermal zones, throttle state, clock caps, GPU frequency, power rails,
battery and available memory, so a run's latency numbers can be read against
load and heat. It reads only `/proc` and `/sys`; it touches no OpenVR, camera,
SteamVR process or service, and it is not a Tier A runner task.

```bash
python3 scripts/sample_run_context.py --interval 5 --samples 720 --output NEW_CSV
```

Each row takes one clock reading, written as `utc` (ISO 8601 with `+00:00`)
and `monotonic_ns`, so rows line up with the `pipeline` block's monotonic
timings. A missing reading is a blank cell, never a sentinel, and a missing
sensor family has no column. `--interval` is 0.5 to 60 s and `--samples` 1 to
7,200; `--output` creates a new file (mode 0600) and never overwrites one.
SIGINT or SIGTERM ends the run after the current row; a JSON summary goes to
stderr so stdout stays pure CSV.

The paths are ported from steam-frame-harness, which took them from
frame-perf-overlay (both MIT; see [THIRD_PARTY](../../THIRD_PARTY.md#run-context-sensor-sampler)).
They are **unconfirmed on SteamOS 0.5.6**: upstream tested SteamOS 0.4.5. A
read-only sysfs listing on Frame is a later device step that needs the
owner's approval. `tests/test_run_context_sensors.py` checks hand-computed rows
from fake trees, including missing and malformed sensors. The rows are run
context, not latency or device evidence.


## Offline foundation for the minimum live POC

[`live_pipeline.py`](../../scripts/live_pipeline.py) exercises generated input,
the shared CPU detector and a result sink. `Frame.owned_bgr` and the engine's
copy on publish own contiguous BGR copies; source identity/sequence cannot regress.
Only one frame waits for inference, and annotations remain associated with
their source. Inputs/results older than one second are dropped. Deadlines,
cancellation and errors close source/sink; a process supervisor terminates and
reaps hung workers without claiming clean shutdown after forced termination.

The only enabled source kind is `synthetic`. The overlay adapter only requests
`--validate-stdin`; it cannot initialize real VR output. Preserve these gates
until a source's ownership/publication/synchronization and feedback prerequisites
are met and the user authorizes the concrete live test. Do not disguise a real
source as synthetic to bypass the gate.

In an existing replay/window development environment (Python 3.12 or 3.14):

```bash
python -m unittest tests.test_recording_growth_check tests.test_live_pipeline tests.test_bounded_worker tests.test_overlay_cleanup tests.test_display_expiry -v
bash scripts/build_overlay_bridge.sh
python scripts/fetch_smoke_assets.py
python -m scripts.live_pipeline --synthetic-check --validate-overlay --seconds 3
```

These commands use generated images and native transport validation only.
The detector/decode dependencies use the repository's existing pins; no new
model or API credentials are required. The replay CI job on x86_64 (every
pull request) and ARM64 (main pushes and on demand) includes these critical
offline suites in its explicit no-skip gate.
Local results and their limits are recorded in [E66](../status.md#e66-offline-live-pipeline-and-headers-only-input-probe).

[`recording_growth_check.py`](../../scripts/recording_growth_check.py) defaults to
no action. Its `--observe MP4` and `--watch-directory DIR` modes are explicit
metadata-only diagnostics; neither imports a decoder or reads payloads. Both
are bounded, identity checked and isolated in a supervised worker. The watcher
ignores existing clips and refuses ambiguous new selections. Positive output
is only evidence of repeated bounded fragment extents while the file grows,
not proof of a completed payload or approved live source.

The [recording operator procedure](../history/device-handoffs.md#proposed-recording-growth-test)
is preserved but paused; no new recorder experiment is planned. The active
work is the [edge HUD acceptance plan](../live-capture.md#edge-hud-acceptance-gates). The vendor recorder's live publication behavior and the source's
exclusion of our panel remain unknown.

The [lifecycle audit](../status.md#e67-offline-cancellation-and-cleanup-audit)
replaces the pipeline's blocking multiprocessing report receive and the probe's
plain `subprocess.run` with [a shared supervisor](../../sightcircuit/runtime/guard.py).
It caps report bytes before parsing, keeps its deadline independent of report
I/O, handles catchable SIGINT/TERM/HUP without interrupting cleanup, and verifies
exit status against the final report. On Linux, a separate guardian now owns
the worker process group and child subreaping, including helpers left behind by
an exited leader. A private anonymous pipe (write end held only by the outer
supervisor, never inherited by workers) detects abrupt supervisor loss before
or during work. The guardian's own deadline and teardown do not depend on the
supervisor or detector staying alive. Signal handlers and subreaper state are
restored afterward. One supervisor must run on the main thread. Windows retains
single-process supervision and explicitly reports parent-death containment
unavailable; native-helper supervision requires Linux. Each command passes its
own module-level `validate_worker_report` (and options such as
`validation_only`); the guardian imports that validator by dotted module name
from this checkout's `scripts/` or `sightcircuit/` only, so it holds no table of
commands.

### The supervision primitive

`sightcircuit/runtime/supervision.py` is the one supervisor of a bounded child
([decision D4](../poc-scope.md#architecture-decisions-2026-11-07)), seeded from
the guard's owned-session supervisor. The guard uses it through
`supervise_owned` for both of its levels (the guardian, and the worker under
the guardian). The agent runner uses its `Signals` in raise mode and its
`OwnedGroup` (group termination with reaping) for tasks and git, which are
commands rather than report workers (B4). The mirror launcher
(`sources.mirror.launch`, for `mirror_check.py` and `mirror_recognize.py`)
runs its worker through `supervise_owned` with progress streaming
([mirror reference](mirror.md#existing-steamvr-mirror-diagnostic)), and the
synthetic run's worker streams its snapshots the same way, so the former
`runtime/process.py` (spawned `multiprocessing` workers) is retired (B5). The
paused capture supervisor adopts it in B6 and keeps its own copy until then.
The primitive offers:

- **Owned-session spawning** with the caller's descriptors, plus three things
  the worker inherits: `SIGHTCIRCUIT_ARMED_FD`, a descriptor it writes once its
  handlers are installed (`stop_on_signals()` does this through
  `report_armed()`); `SIGHTCIRCUIT_COOPERATIVE_GRACE`, the seconds it has
  after SIGTERM, from which `guard.cleanup_budget(default)` derives a worker's
  own cleanup joins (the synthetic run's capture-thread join: the grace less a
  one-second reserve, so four seconds under the guard, half a second alone);
  and `SIGHTCIRCUIT_SUPERVISOR_PID`, which `enter_worker()` checks after asking
  the kernel for SIGTERM on parent death (Linux), so a worker whose supervisor
  is killed during a driver call stops cooperatively instead of being stranded.
- **Hold-and-arm around process start.** A handler starts held and is armed
  by its caller; `hold` and `resume` bracket a region, such as a `Popen`,
  where there is nothing to terminate yet. The primitive keeps its own handler
  held for the whole supervision and polls the first signal, so no window
  around process start, the cleanup or the report block can lose a signal or
  skip the report; in `raise` mode `Cancelled` surfaces once, after the
  cleanup, carrying the finished report.
- **Group-wide termination with reaping** (`OwnedGroup`), `killpg` TERM then
  KILL with every owned member reaped, and containment of a group a killed
  guardian reported.
- **Deadline-based cleanup verdicts.** A cancellation (or parent loss) first
  waits up to the arm grace for a worker that has not armed yet, so a signal
  that arrives while the worker is still importing is answered by its handler
  and not by the default disposition, which would kill it and leave a forced
  termination in the report. SIGTERM then goes to the leader, and its exit and
  the end of its report are awaited together until one cooperative deadline.
  The fixed sub-second joins of the earlier supervisors are gone; a report
  reserve after the tree is gone covers an orphaned pipe end and is not the
  verdict. Every budget is a `Grace` field and a tunable, not a guarantee.
- **Both signal modes, with the inherited-ignore policy as a parameter.**
  `RECORD_AND_POLL` is the guard's: the first signal is recorded and polled,
  and a signal the process was started with ignored is taken over anyway, so a
  launcher can always be cancelled. `RAISE_ON_SIGNAL` is the agent runner's:
  the first signal raises `Cancelled` (which carries the finished report after
  the cleanup), and an inherited ignore stays ignored, as `nohup` intends.
- **Progress streaming** on the report stream: a line
  `{"event": "progress", "report": {...}}` (`emit_event`) reaches the caller's
  `progress` callback on the supervising thread, `{"event": "result",
  "report": {...}}` or a bare JSON report is the final report, and the
  guardian forwards events unchanged. A pending line and the report text are
  each bounded by the report byte limit; events are dispatched, not kept. The
  synthetic run's worker streams `run_pipeline`'s snapshots this way;
  `supervise_synthetic(options, progress=...)` receives them.
- **One report block**: `status`, `exit_code`, `cleanup_ok`, `reason`,
  `worker_pid`, `worker_armed`, `worker_reaped`, `worker_exit_code`,
  `worker_tree_reaped`, `report_reader_stopped`, `worker_forced_termination`,
  `worker_termination_requested` and `supervisor_elapsed_seconds`; the guard
  adds its guardian fields (`guardian_armed` among them) as before. Every
  field that existed before keeps its meaning.

`tests/test_supervision.py` covers the policies, the hold, the stream, the
budgets and the handshake; `tests/test_bounded_worker.py` covers the guard on
it with real subprocess chains, including a cancellation that arrives before
the worker has armed and a worker that never arms.

Signals follow one convention, in `guard.py`. A launcher runs inside
`sigterm_cancels()`, where SIGTERM raises `StopRequested` as Ctrl+C raises
`KeyboardInterrupt`, and `cancelled(report, error)` records either as
`cancelled` with exit 128 + signal. A guarded worker calls `stop_on_signals()`,
so SIGINT and SIGTERM set its stop event and its own cleanup runs. The live
pipeline, HUD marker check and observer and recording growth workers use it.
`shm_capture_check.py` keeps its own copy of the launcher side.

On cancellation or parent loss the guardian first waits up to one second for a
worker that has not armed its handlers yet, then permits five seconds for
cooperative worker cleanup (the leader's exit with its report complete), then
one second for group termination and one for kill/reaping, plus a one-second
reserve for its report reader after the tree is gone; a timeout skips the arm
wait. This is additional to the normal worker deadline (pipeline: 15 seconds
startup + requested duration + five seconds normal teardown; growth: selection
wait + observation + five seconds overhead). The outer supervisor allows a
further eight seconds for guardian startup/teardown. If the guardian itself is
unresponsive, the outer supervisor gives it ten seconds to stop (the longest
cleanup the worker's budgets allow it, nine seconds, plus one), then one second
for termination, one for kill/reaping and one for report-reader shutdown
(`Grace.for_supervisor_of`). Reports distinguish worker and guardian
reaping/forced termination, and overall supervision time from inner worker time.
Forced termination never establishes clean API/resource shutdown. A reaped
worker with a still-running acquisition thread also cannot claim source cleanup.
An unkillable process is reported unconfirmed; these software budgets are not
guarantees about kernel scheduling or blocked kernel I/O.

The new failure fixtures use local subprocesses and handshake files. They
exercise real parent-only/repeated signals, partial and oversized JSON,
contradictory exit status, orphaned helper pipes, a hanging detector/source,
partial sink startup, hide/close errors and late file scans. The orphan test
also checks that an unrelated child is untouched. A mocked KeyboardInterrupt
alone no longer serves as evidence of process reaping.

The [E68 tests](../status.md#e68-independent-expiry-and-supervisor-loss-containment)
add actual SIGKILL of the outer supervisor, a closed lifeline before startup,
and a deliberately stopped/resumed guardian. They verify owned-worker/helper
reaping while preserving the lack of a final supervisor cleanup report. The
guardian also writes its worker's process group to a private pipe. If the
guardian alone is killed, the outer supervisor (its subreaper) sends that group
SIGTERM, then SIGKILL, and reaps it. The report stays `guardian_failed` with
`cleanup_ok: false`. A guardian-SIGKILL test covers this path
([lifecycle review](../lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01)).
If both supervisor and guardian are killed, the guardian cannot run, or a helper
detaches from its group, this scheme cannot guarantee cleanup. No test forces
real uninterruptible kernel I/O.

Synthetic display frames now carry absolute monotonic expiry to the independent
native validation process. Stalled inference, settings heartbeats and partial
packet reads cannot renew it. Tests inspect validation events/software state,
not a headset. Opcode 4 remains rejected for real output; actual Frame expiry,
launcher behavior and API teardown still need separately authorized validation.
See the [source/output contract review](../live-capture.md#synthetic-input-and-feedback-contract-review).
