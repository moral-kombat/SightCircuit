# Guide the wearer through a bounded camera recognition run (`camera_guide`)

`camera_guide` is the sixteenth command: a window that walks the wearer
through one [`camera_recognize`](camera-recognize.md) run at a time. The
wearer needs no keyboard, hears no terminal bell and counts no seconds. The
owner asked for it on 2026-11-19, after device step L-D1 stopped because the
wearer, who is also the operator, could not press Enter or hear a bell once
the headset was off ("Can you make a program interface I can work with
instead of relying on the bells keyboard and time"). The owner chose "A window
inside the headset" and accepted "Yes, tap to start".

**This guide is a procedure, not an approval.** The window approves nothing
and neither does this page. A run on the Frame is a device step that needs
the owner's explicit approval first, as every
[lane L device step](../live-capture.md#camera-display-work-order-adopted-2026-11-16)
does.

**Nothing here has run on the Frame.** The command has been checked in the
cloud only: offscreen, against the camera stand-in and the helper's fake
backend. No time or count on this page is a Frame measurement.

[Camera recognition](camera-recognize.md) · [Scope](../poc-scope.md) ·
[Gates](../../sightcircuit/gates.py) · [Current work](../live-capture.md#current-work-order)

## What it is and is not

The window runs what `camera_recognize` runs: the same `recognize()`, the
same helper launch (the named binary, `--parent-confirmed` after the start,
the same owned session and deadline), the same bounds (at most 30 seconds and
300 frames, one camera, 5 frames a second by default) and the same report. It
raises no bound and adds no capability.

Every gate stays closed and none is lifted; the report carries
`gates.claims()`. The run stays in the class the owner classified on
2026-11-09 as outside the live-input gate, bounded recognition without
display, because **while the camera reader is active the window shows nothing
derived from a camera frame**: no detection, label, count or thumbnail, only
the scripted instruction, the progress bar and the time left. Results appear
only after the helper has ended. No image is ever shown, saved or written;
the pictures are shapes the command draws. The window never opens the overlay
bridge and never starts SteamVR. With `--presence-watcher`, and only then, the
run's process launches the [presence watcher](camera-recognize.md#the-presence-stop---presence-watcher),
which joins a running SteamVR to listen for "the headset is off" and reads
presence signals only: no camera data, no overlay, no setting.

Camera Data is as the [recognition guide](camera-recognize.md#camera-data)
states: the report holds counts, closed labels, boxes, timings and format
metadata, never a pixel, and is written only to the path named with
`--report`.

## What the wearer sees

Large type and large buttons, one thing on each screen. The type is sized
from the window's own size.

1. **Before we start.** "What do you see in the headset right now?" with
   "Colour, stable" and "Something else". "Something else" ends there:
   nothing is launched, and the result screen says to tell the Frame manager.
2. **What will happen.** The script's steps in a few lines, and **Next**.
3. **Please read.** The helper's export notice as the contract has it,
   without its closing instruction to type the answer; the run's bounds; the
   target; where the report will be saved; what to be holding; then **Start**
   and **Cancel**. Start can be clicked only after the notice has been up for
   5 seconds, counted down on the screen ("You can start in 3 s"). Clicking
   Start is the informed start of this one run. The report records
   `confirmation: operator_window`. The typed `yes` stays for the terminal
   commands.
4. **The run.** One instruction at a time with a drawn picture, a progress
   bar while a step is recorded, the step number, the time left, and
   **Stop**. Until the camera reader is ready the screen says "Starting the
   camera reader. Please wait", counts the seconds and moves, and after 10
   seconds says to click Stop and tell the Frame manager if nothing changes.
5. **After the run.** If the wearer clicked that the headset was coming off:
   "Did you take the headset off? If it is off, put it back on first", with
   "Yes, off and back on" and "No, I kept it on". Then the passthrough
   watch: a 30-second countdown with "Something changed", which can be
   clicked during the watch, and "Passthrough unchanged", which can be
   clicked when the count reaches 0. "Something changed" is asked once more
   ("You said the passthrough picture changed", **Confirm** or **Go back**),
   because under the passthrough rule that answer stops the camera track.
6. **Result.** The run in plain words (for example "bottle seen in 9 of 11
   seconds while it was held in view"), the report's path and **Close**,
   which can be clicked after the result has been up for 3 seconds.

Stop, closing the window or a signal during the run (SIGINT, SIGTERM, or the
SIGHUP of a dropped SSH session or a closed terminal) ends the helper cleanly
and still leaves the report. One launch is one run: a second run is a second
launch, a second notice and a second Start.

### A pointer interface that cannot be worked by accident

- No key starts, answers or stops anything.
- Every button is dead for 1.2 seconds after it appears, on every screen and
  for the one button that appears during the run. A double click, or a
  second click already on its way, lands on nothing.
- Consecutive screens put their buttons in different places: each screen has
  two bands for buttons and uses the one the screen before it did not. So no
  button sits where the last screen's button was.
- The two waits that matter (before Start, before Close) are shown as counts.

### When a run does not go as scripted

- **The launch fails.** If the run read no camera frame and did not end on a
  presence stop, there is no headset question and no watch: the result says
  at once what happened, "No camera frame was read" and "Tell the Frame
  manager", and the report is saved (`guide.watch: no_frame_read`). The
  helper's `ready` is not used for this, because it can arrive before the
  helper has connected to the camera service.
- **The reader ends on a presence stop before the remove click.** The run is
  watched, and the result says "The camera reader ended early
  (headset_removed), before the click to take the headset off"; it does not
  say the headset came off.
- **The report does not confirm a clean shutdown, or the worker dies.** The
  helper is not proven ended. The window says the run was interrupted, waits
  until the helper's own bound has certainly passed (the run's seconds plus
  ten, from the Start click), then holds the watch. The result says only
  "The run ended without a clean shutdown" or "The run was interrupted" and
  "Tell the Frame manager": nothing derived from a frame is put into words.
- **The presence watch cannot be armed** (`--presence-watcher` was given and
  SteamVR is not running, the watcher did not answer, or the headset already
  reports standby). The camera reader is not started: `status: error`,
  `reason: presence_watch_not_armed`, no watch, and the result says "Not
  started: the headset-off watcher could not be armed, so the camera was not
  read. Tell the Frame manager."
- **The presence watcher is lost during the run.** The reader is stopped at
  once (`presence_watch_lost`), the run is watched, and the result says the
  run was ended because the headset-off watcher stopped answering.
- **Stop while the presence watch is arming.** The stop is honoured at once:
  the wait for the watcher ends, nothing is launched and the run is
  `stopped` (`stopped_before_launch`). After a worker that died, the window
  waits out the helper's bound plus what arming may have taken before the
  helper was launched.
- **SteamVR reports standby before the remove click.** The run ends
  (`presence_ended`) and the result says so; it is not called a removal.
- **Stop while the helper is starting.** A stop that arrives before the
  helper is launched launches nothing. One that arrives during the launch is
  passed on once the helper is ready (or after 3 seconds), so the helper
  stops cleanly.

## The two scripts

Both are shown to the wearer before Start, and both take the command's whole
30 seconds. The time left on the screen is counted from the Start click,
which is before the helper begins its own bound, so the window never shows
more time than the helper has. The script's steps are counted from the
helper's `ready`.

`--script removal` needs **one click** during the run:

| Step | The wearer is told | Mark | Recorded |
| --- | --- | --- | --- |
| before Start | Hold the bottle about half a metre in front of you, and keep holding it when you click Start | | |
| 1 | Keep holding the bottle about half a metre in front of you | `near`, as soon as the reader is ready | about 4 s, with the bar |
| 2 | Now move it to about a metre away, and hold it there | `far`, when the instruction appears | about 2 s to move and 4 s held, with the bar |
| 3 | Click, then take the headset off. Put it back on when you like | `remove`, at the click | until the run ends |

The one button appears 10 seconds after the reader is ready, which leaves 20
seconds of the run less what the launch took. After the click the screen says
"You can take the headset off now". The run ends by itself when SteamVR
reports standby (with `--presence-watcher`: `presence_ended`), when the helper
reports `headset_removed` or `standby`, or on its bound. If fewer than 5
seconds are left, the button is taken down at that moment and the screen says
there is not enough time: a click whose mark could not be recorded is never
accepted. The `far` interval includes the two seconds in which the bottle is
being moved.

`--script positions` needs no click after Start: centre, left, right, up and
down for about 4 seconds each, each with a dot on the screen showing where to
hold the bottle and its mark made when the instruction appears, then
`out_of_view` for about 3 seconds.

Marks are from the [closed vocabulary](camera-recognize.md#times-marks-and-the-target-summary)
and are recorded with source `window`; the summaries are the recognition
command's, unchanged in meaning, and a window mark's own time bounds its
interval. A step that cannot fit in the time left does not begin: the screen
says so and the report lists the steps not reached.

**The removal reading.** The `remove` click is made just before the headset
comes off, not after as the key mark of row
[G7](../live-capture.md#edge-hud-acceptance-gates) was written. Each removal
entry therefore reports what it did before (the helper's end reason, the time
from the mark to the end record, the frames after the mark and the latest of
them) and adds `helper_end_after_last_frame_ms`, the time from the run's last
processed frame to the helper's end record. The two limits an entry is
compared with are parameters, `--late-frame-limit-ms` and
`--removal-end-limit-ms`, with today's 2000 and 3000 as defaults. The window
shows the measured values and judges nothing. For a click made before removal
the owner accepted G7's numbers on 2026-11-19: the run ends by itself at most
10 s after the click and at most 3.0 s after the last frame the helper
received (`--removal-end-limit-ms 10000`; `--late-frame-limit-ms 7000` is the
matching bound on a late frame).

**The presence stop (2026-11-20).** Run 1 of L-D1 ([E100](../evidence/e100.md))
ended on its frame bound 13.3 s after the click: the camera kept delivering
frames with the headset off, so the helper's frame-gap stop never fired,
while SteamVR reported standby 7.0 s after the click. With
`--presence-watcher` the run ends on SteamVR's signal
([the presence stop](camera-recognize.md#the-presence-stop---presence-watcher)).
Each removal entry then also says whether the watch was armed
(`presence_watch_armed`), which signal came (`presence_signal`), the time
from the click to it (`presence_signal_after_mark_ms`), from it to the
helper's end (`helper_end_after_presence_signal_ms`), the frames received
after it, and what ended the run (`run_ended_by`, `run_end_reason`).
`helper_end_is_presence_stop` is true when the watch's signal ended the run
or the helper ended `headset_removed` or `standby`; a run that ends only on
its bound has it false and, with G7's limits, `helper_end_within_limit`
false, armed or not. The result screen adds one line: the signal with its
two times, "SteamVR sent no headset-off signal before the run ended", or
"SteamVR's headset-off signal was not armed for this run". Once the signal
has ended the run the window's marks are refused, so a click that lands
after it records nothing; were one recorded all the same, its entry is
marked `mark_after_presence_signal`, counts as neither a presence stop nor
within the limit, and the result says "SteamVR had already reported the
headset off ... before your click to take it off". Nothing about the
watch is shown during the run. No time here is a Frame measurement.

## How the run is launched on the Frame

After the owner has approved the step, and only then, the Frame manager
starts the window for the wearer from the Frame's normal graphical session,
so that it appears on the Frame's desktop:

```bash
.venv-replay/bin/python scripts/camera_guide.py --allow-camera-input \
  --helper /path/outside/the/checkout/sightcircuit-camera-helper \
  --presence-watcher artifacts/presence-watcher/sightcircuit-presence-watcher \
  --script removal --report artifacts/camera/guide-removal-01.json
```

`--presence-watcher` is optional and names the binary
`bash scripts/build_presence_watcher.sh` builds on the Frame. It is checked
before the window opens by the helper's path rules (refused as
`presence_watcher_not_found`, `presence_watcher_inside_work_tree` or
`presence_watcher_not_executable` with nothing shown). Without it the run is
what it was, and its report says the signal was not armed.

The second run is a second command, with `--script positions` and another
report path. `--report` is required and is checked before the window opens: a
new `.json` path in an existing directory the command can write to (it makes
and removes a probe file there). An existing file, a missing or read-only
directory or another suffix is refused with nothing shown, and nothing is
ever overwritten. The helper path is checked the same way and by the same
rules as the recognition command's. `--target-label` (default `bottle`),
`--seconds`, `--max-frames`, `--fps`, `--camera` (default 0, the left),
`--detector` and `--threshold` are the recognition command's, inside its
bounds. `--windowed` gives a 1280 by 800 window in place of a maximized one.
The console entry point of an editable install is `sightcircuit-camera-guide`.

The command prints its report on stdout when the window closes and exits with
the recognition report's code: 0 for `recognized`, 1 otherwise, which
includes a run that ended `helper_ended_early` with `headset_removed`, the
removal script's expected end.

The wearer puts the headset on, finds the window on the desktop and works it
with the controller's pointer. The Frame manager does not click for the
wearer. The accepted passthrough rule applies as in every device step: any
change in the passthrough image during the run or the watch stops the SDK
track, and "Something changed", confirmed, is how the wearer says so.

## The report

`camera_recognize`'s [report](camera-recognize.md#the-report), with
`confirmation: operator_window` and a `guide` block (its own
`schema_version` is 2):

- `script`, `confirmation`, `started_by_start_button`, and `ended_by`
  (`helper`, `stop_button`, `window_closed`, `cancel_button`, `signal` or
  `not_started`);
- `start_click_monotonic_ns` and `reader_ready_monotonic_ns`;
- `steps`: per step its label, how it begins, whether it was reached, the
  time of its mark and the index of that mark among the recorded marks;
  `steps_not_reached`; `out_of_time`;
- `watch`: `held`, `no_frame_read` (a launch that failed) or
  `run_not_started`;
- `observations`, closed choices only: `starting_state` (`colour_stable` or
  `something_else`), `headset` (`removed_and_back`, `not_removed` or
  `not_asked`), `passthrough_after` (`unchanged` or `changed`), the seconds
  the watch had run when it was answered and whether it was complete. An
  answer that was never given is `null`. There is no free text anywhere;
- `removal_limits_ms`, the two limits the removal entries were compared with.

A launch that started no run (Cancel, "Something else", a window closed
before Start) still leaves the report: `status: error`, reason
`confirmation_declined` or `starting_state_not_stable`, `confirmation: null`
and no `capture` block, because nothing was launched.

## How nothing camera-derived reaches the run screen

The run is in a second process of the command, which calls `recognize()` on
its main thread as the supervision primitive requires. The window's process
loads no detector and reads no frame. During the run the worker tells it one
thing: that the helper's `ready` was accepted, with no value attached. That
moment starts the script's steps. Any other line from the worker, and any
line without the worker's token, is dropped and counted. What the run screen
shows is `Guide.view` (`sightcircuit/camera/guide.py`), a function of the
script, the Start click's time, that ready time, the one click and the clock.
The report crosses to the window only after `recognize()` has returned, which
is after the helper has ended. If the window's process dies, the worker sees
its input close and stops the helper.

`ready` is not the first frame. The helper's fake build, run with a forced
pixel path, says `ready` before it connects to its service; when `ready`
comes before frames flow, the first step's seconds begin before there is
anything to detect on. Whether that happens on the Frame, and by how much, is
for the device run to show: the report has the Start click, the ready time
and every frame's time.

**The worker runs only for its window.** `camera_guide --worker` is the
second process and is never given by hand. Before it reads anything else it
must find, on a pipe it inherited, a one-shot token written by its own parent
process; without that it reads nothing, launches nothing and reports
`guide_worker_not_launched_by_window`. So a shell cannot reach a confirmed
export through it without the window, the notice and the click. This is a
procedural control against mistakes, as the one-run scope record is, not a
cryptographic one: a program written to imitate the window could satisfy it.

## Cloud checks

`tests/test_camera_guide_presence.py` covers the presence stop in the window:
the removal entry and the result's words for a run SteamVR's signal ended,
for E100's own numbers (a run that ends only on its bound still fails), the
worker and the window with a stand-in watcher, and the path check.
`tests/test_camera_guide.py` covers the script logic and the notice derived
from the contract (standard library), the worker's handshake and its
refusals, the worker against the stand-in helper, the window offscreen with
Qt's test tools (clicks sent to the top-level window at a point, including
double clicks and second clicks 100 to 900 ms later on every transition, and
button texts at 4:3, 5:4 and portrait sizes), and `--validate`:

```bash
bash scripts/build_camera_helper.sh --fake
.venv-replay/bin/python scripts/camera_guide.py --validate
```

`--validate` clicks through the whole window offscreen against the helper's
fake backend with a scripted detector: both scripts, a run that reaches its
bound, a presence end, Stop, the window closed mid-run, a launch that fails,
Cancel, a declined start, a run too short for its script, the refused report
paths and the worker refusing a launch by hand. Its guard and dwell times
are shortened so that it runs in about half a minute. It refuses any helper
that does not describe itself as the fake before a window is made, so it can
never open a camera. These are cloud and CI results; none is a Frame result.
