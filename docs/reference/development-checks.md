# Development checks and CI

The [development guide](../development.md) gives the recommended local setup
and check sequence. This reference explains what each check proves, its
artifacts, and the distinction between local tests, CI runners, and Frame results.

## What validation demonstrates

The smoke check generates an MJPEG video from 12 copies of the upstream sample
image, reads all 12 frames with OpenCV's FFmpeg backend, runs the default
detector (MobileNet-SSD) on CPU, and requires bicycle, car and dog detections on
each frame. Every registered detector also runs once on the sample image and
must find each of bicycle, dog and car or truck that its vocabulary has. Qt renders the
annotations and writes a nonblank screenshot. A separate bundled ONNX
multiplication graph executes with checked numerical output.

It invalidates the previous `artifacts/smoke/readiness.json` before importing
`sightcircuit` or any runtime dependency and writes a new report only after
success. The detector fixture is a development check, not a selected production
model.

| Path | Contents | Committed? |
| --- | --- | --- |
| `.venv/` | Dedicated Python environment | No |
| `.cache/uv/` | Default package cache | No |
| `.cache/sightcircuit/assets/` | Verified model, model configuration and sample image | No |
| `artifacts/smoke/readiness.json` | Current passing validation report | No |
| `artifacts/smoke/detection-window.png` | Rendered detection result | No |
| `artifacts/smoke/sample.avi` | Generated repeated-image MJPEG video | No |
| `scripts/smoke-assets.json` | Immutable fixture URLs and SHA-256 checksums | Yes |

Set `UV_CACHE_DIR` or `XDG_CACHE_HOME` before setup to reuse another writable
package/font cache. The fixture directory stays repository-local. The downloader
also accepts `--asset-dir` for isolated download checks; the smoke check uses the
default fixture directory.


## Window rendering and hardware

The default smoke check sets `QT_QPA_PLATFORM=offscreen` when unset. It shows no
visible desktop UI and exits after checking the frames. Visible presentation
needs a working display server and Qt platform libraries. The interactive
prototype is `scripts/replay_window.py`. OpenCV is installed headless; the
replay app uses Qt for presentation.

OpenCV 4.12 is pinned because the Caffe importer needed by this fixture was
removed from OpenCV 5. The wheel includes its own FFmpeg backend; the current
smoke script does not invoke a separately installed `ffmpeg` executable.

Do not copy an x86_64 virtual environment onto the headset. Device dependencies
and camera/runtime capabilities must be installed and verified for the target
architecture. [Status](../status.md) distinguishes device confirmations from cloud
tests and lists unverified capture, geometry and performance capabilities.
See the [camera guide](../steam-frame-camera.md) and
[implementation milestones](../architecture.md) for their technical constraints.


## Continuous integration

[Development checks](../../.github/workflows/checks.yml) run on x86_64 for
pull requests and main pushes. [ARM64 checks](../../.github/workflows/arm64.yml)
run on main pushes and on demand, with an optional pacing run. ARM64 is the
headset's architecture, but a runner has no Frame drivers, SteamVR, or cameras.
Local checks and GitHub Actions results remain separate evidence.

The [shared workflow](../../.github/workflows/check-jobs.yml) has three jobs:

| Job | Work and limits |
| --- | --- |
| `native` | Lint on x86_64, check generated HUD headers, build native helpers and all capture-reference fixtures, then run the system-Python suite once with native modules pinned against skips. Probe checks use explicit missing-runtime paths; no headset is involved. |
| `replay` | Test the minimal decoder environment, then install window dependencies, build the bridge, fetch seed images, and run the full suite with the relevant modules pinned against skips. Check generated documentation and links; run the smoke environment on x86_64. |
| `replay-pacing` | On-demand ARM64 replay of the synthetic stand-in; publishes runner-CPU latency reports, never Frame measurements. |

A newer push cancels superseded pull-request runs. Changes limited to the
listed documentation paths run [Documentation checks](../../.github/workflows/docs.yml):
local links, preserved anchors, generated summaries, and the checker/generator
unit tests. Documents used by procedure-contract tests stay on the full run.
`tests/test_workflows.py` keeps path lists aligned and checks that the modules
pinned against skips exist. The documentation checker never executes code blocks.

The native suite validates mirror discovery, Linux interfaces, synthetic
shared-memory publication, and bounded worker failures. The replay suite adds
decoding, inference, offscreen rendering, HUD, agent, and seed-regression tests.
`scripts/run_tests_no_skips.py --all` runs the full suite once and fails if a
module named on the command line skips a test; dependency-specific skips in
other modules remain allowed. Read the results rather than inferring coverage
from a successful process exit alone.

The runner is the suite's entry point and does two more things there:

- `--durations FILE` writes each test module's wall time and test count as
  JSON and prints the modules slowest first. A module's time runs from the end
  of the previous test (or the start of the run) to the end of each of its
  tests, so class and module fixtures count with the tests they serve; what
  follows the last test goes to the last module. These are runner-CPU numbers
  for the planned suite time budget, never Frame measurements.
- `--budget tests/time_budget.json` enforces the suite time budget (work-order
  item B7): every module's wall time, measured as for `--durations`, against
  its ceiling in the file (or the file's `default_seconds` for a module it
  does not list), and the run against `total_seconds`. A violation fails the
  run after the tests have finished, naming each module, its time and its
  ceiling; the tests themselves still report their own result. CI passes the
  option in both no-skip runs. The ceilings come from the cloud runner's
  durations of 2026-11-07 (`test_frame_agent` 54 s, `test_bounded_worker`
  48 s, the next nine between 8 and 18 s, everything else under 7 s, 308 s in
  all) with about three times margin for a loaded runner: 180 s and 150 s for
  the two largest, 30 to 60 s for the listed middle, a 30 s default and a
  900 s total. A ceiling bounds runner time and is never a reason to skip or
  shorten a test: raise it, with the measurement that needs it, when a module
  legitimately grows. `tests/test_run_tests_no_skips.py` checks that every
  listed ceiling names an existing module and that CI enforces the file.
- Before the tests run it normalises SIGINT for the suite's children. A test
  process started with SIGINT ignored (a background job of a shell without job
  control) passes that ignore to every subprocess the tests signal, so the
  subtests that send SIGINT would depend on how the suite was started. An
  ignored SIGINT is replaced by Python's default handler, which a child does
  not inherit, and the runner says so on its error stream. A plain
  `python -m unittest discover` run does not normalise anything; use the
  runner when the suite is started from such a shell. SIGHUP is left as it is:
  the agent runner honours an inherited ignore by design (see its
  `Interruption` class), and a suite started under `nohup` would see its
  SIGHUP subtests fail for that reason.

### Cancellation test root cause (2026-11-07)

`test_bounded_worker.test_pipeline_cli_parent_cancellation` failed on loaded
runners with `cleanup_ok` false after SIGTERM ([E84](../evidence/e084.md)).
Reproduced on the cloud runner (x86_64, 4 cores) with
`stress-ng --cpu 8 --cpu-method matrixprod` running throughout and a loop that
replays the test's exact command and readiness probe: 5 red runs in 200 (3 of
50, 0 of 50, 2 of 100), 0 of 3 unloaded. None of the three paths the
[architecture council](../history/2026-11-07-architecture-council.md#the-cancellation-race)
suspected was involved in any of the 200 runs: `source_cleanup_ok` (the
worker's capture-thread join), `report_reader_stopped` (the guardian's reader
join) and `guardian_report_reader_stopped` (the outer join) were never false.
Every red run had the worker killed by SIGTERM's default disposition
(`worker_exit_code` -15, forced termination, no report) 10 to 24 ms after it
was spawned. The cause was the probe: it read the guardian's kernel children
list and the candidate's caught signals from `/proc`, and a forked child that
has not yet exec'd is already listed there with its parent's caught signals
and command line (the two instrumented batches recorded the guardian's own
`--guard` command line in both red runs). Under CPU oversubscription that
window lasted long enough for the probe to fire, and the SIGTERM then reached
a worker that had exec'd but not yet run `stop_on_signals()`. The guard
reported that truthfully as a forced termination. The fix is work-order item
B3, the supervision primitive (`sightcircuit/runtime/supervision.py`): a
worker arms its handlers through an inherited descriptor and a cancellation
that arrives earlier waits, bounded, for the arm; the test's probe also
requires the exec'd worker. Its acceptance is fifty consecutive green runs of
the test under the same stress command.

### Held-signal flake root cause (2026-11-08)

Two runner failures on commits that touched no runtime code, both in the
replay job, both ending a cooperative worker by force after it had armed:

- Checks run 90, attempt 1 (pull request #29, `1be5eefe`):
  `test_supervision.SupervisedOwnedTests.test_a_signal_during_process_start_is_held_and_raised_once_the_worker_can_be_ended`
  reported `cancelled`, exit 143, `cleanup_ok` false, `worker_exit_code` -9,
  `worker_forced_termination` true, an empty report ("Expecting value: line 1
  column 1") and 6.113 s elapsed: the arm, the whole 5 s cooperative grace, a
  group SIGTERM the worker also survived for the 1 s terminate grace, then
  SIGKILL. The re-run and five local runs passed.
- Checks run 93 (main, `1889e56e`), the decoder-only `unittest discover` step:
  `test_bounded_worker.BoundedWorkerTests.test_a_cancellation_before_the_worker_arms_waits_for_its_handler`
  reported `worker_armed` true, `worker_exit_code` 130 and
  `worker_forced_termination` true after 1.464 s: the fixture's 0.4 s arm, its
  1.0 s cooperative grace with no reaction to SIGTERM, then the group SIGTERM,
  to which the worker *did* react and exited cooperatively.

Reproduced on the cloud container (x86_64, 4 cores, Python 3.12.3) with
`stress-ng --cpu 8 --cpu-method matrixprod` running throughout and the test
alone in a loop (`python3.12 -m unittest <test id>`), with an import hook
that, before the primitive's group termination, read the still-living worker's
`/proc` signal state and made it dump its Python traceback through
`faulthandler` on SIGUSR1: BASELINE_SUPERVISION for the supervision test and
BASELINE_BOUNDED for the bounded-worker test. The one red run matched run 90
exactly (exit 143, `worker_exit_code` -9, empty report, 6.5 s), and its
worker's traceback was the cause:

```text
File "threading.py", line 300 in __enter__     # Condition.__enter__: acquiring the event's lock
File "threading.py", line 623 in set           # Event.set
File "sightcircuit/runtime/guard.py", line 103 in <lambda>   # the stop handler
File "threading.py", line 350 in wait          # Condition.wait, the lock still held
File "threading.py", line 655 in wait          # Event.wait
```

The worker was sleeping in a futex with no signal pending and SIGTERM caught.
Its stop handler, `lambda *_: stop.set()`, ran on the main thread between two
bytecodes of `Event.wait()`, inside the region where `wait` holds the event's
condition lock (from its entry until `Condition.wait` releases the lock
around the waiter acquire). `Event.set()` then blocked on that non-reentrant
lock, held by the very thread running the handler: a deadlock the group
SIGTERM could not break (its handler ran nested, into the same acquire), so
only SIGKILL ended the worker. None of the three candidates in the work order
was involved: the worker had armed before the SIGTERM, the empty report was
the consequence of the kill, not a reader fault, and there was no window
between the arm and the grace.

Run 93 is the second face of the same handler. A Python-level handler runs
only when the main thread next executes Python code; a signal whose C-level
delivery lands after the eval loop's last check and before the main thread
blocks in the waiter's lock acquire is not handled until that acquire returns
(only a signal arriving *during* the acquire interrupts it). The slow-arm
worker therefore sat in `stop.wait(10)` with its SIGTERM already delivered,
and the group SIGTERM, which did arrive during the acquire, ran the handler
and let it exit 130. Both are deterministic in `tests/test_supervision.py`
(`StopRelayTests`): holding the event's lock while the signal arrives
reproduces the deadlock, and delivering the signal to another thread, which
never interrupts the main thread's acquire, reproduces the deferral; on the
old handler the first hangs and the second waits the whole timeout.

The fix is in the primitive, not in a budget: `supervision.stop_on`, which
`guard.stop_on_signals` now uses, installs a handler that takes no lock (one
non-blocking write to a pipe) and makes that pipe the process's wakeup
descriptor (`signal.set_wakeup_fd`), so the C-level delivery of a stop signal
writes its number at once, from whichever thread received it, whatever the
main thread is doing. A daemon relay thread reads the pipe and sets every
registered stop event on its own thread, where a lock the main thread holds
is contention, not a deadlock. `test_mirror_launcher`'s check that a raised
signal sets the event now waits for the relay instead of asserting
synchronously. Acceptance, as for the cancellation race above: the same
stress loop on the fixed checkout, FIXED_SUPERVISION for the supervision
test and FIXED_BOUNDED for the bounded-worker test, fifty consecutive green
runs being the bar. These are runner-CPU observations on x86_64; nothing here
ran on the Frame.

The optional pacing run uses a generated 2464 × 2464 HEVC clip at 60 fps with
seeded noise. It replays 40 processed frames at a 5 fps cap with two and four
decoder threads, and checks the [latency statistics](pipeline.md#latency-statistics).
Its content differs from the original E62 clip, so timings and bitrate are not
reproductions of that device input. To reproduce one runner configuration:

```bash
mkdir -p artifacts/replay-pacing
.venv-replay/bin/python scripts/replay_standin.py artifacts/replay-pacing/standin.mp4 \
  > artifacts/replay-pacing/standin.json
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
  artifacts/replay-pacing/standin.mp4 --realtime --decode-threads 2 --max-frames 40 --max-fps 5 \
  --report artifacts/replay-pacing/realtime-2-threads.json
```

## Generated evidence summaries

Detailed records live under [evidence](../evidence/README.md); the coordinator
allocates IDs and updates `docs/evidence/index.json`. Reviewed capability rows
in `docs/status-data.json` generate the grouped status matrix and the marked architecture
progress summary. The same generator builds the evidence catalog from the index.
Preserve all historical evidence headings and E63–E71's
reconstructed uncertainty. From the checkout:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
python3 scripts/check_docs.py
python3 -m unittest tests.test_generate_status
python3 -m unittest tests.test_check_docs
```

The generator checks identities, provenance, missing history and evidence
references before writing. The root README keeps short entry points; its older
narrative and links remain in [README history](../project-history.md). The
[qualification design](../hud-qualification.md) owns the proposed G4 method and
separately scoped device-validation evidence path. These offline documents and
schemas do not qualify a mapping or open production output.
