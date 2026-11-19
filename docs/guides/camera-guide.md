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
bridge or SteamVR.

Camera Data is as the [recognition guide](camera-recognize.md#camera-data)
states: the report holds counts, closed labels, boxes, timings and format
metadata, never a pixel, and is written only to the path named with
`--report`.

## What the wearer sees

Large type and large buttons, one thing on each screen. Only the pointer
works: no key starts, answers or stops anything.

1. **Before we start.** "What do you see in the headset right now?" with
   "Colour, stable" and "Something else". "Something else" ends there:
   nothing is launched, and the result screen says to tell the Frame manager.
2. **Please read.** The helper's export notice as the contract has it,
   without its closing instruction to type the answer; the run's bounds; the
   target; where the report will be saved; then **Start** and **Cancel**.
   Clicking Start is the informed start of this one run. The report records
   `confirmation: operator_window`. The typed `yes` stays for the terminal
   commands.
3. **The run.** One instruction at a time with a drawn picture, a progress
   bar while a step is recorded, the step number, the time left, and
   **Stop**. Until the camera reader is ready the screen says "Starting.
   Please wait".
4. **After the run.** For the removal script: "Put the headset back on if it
   is off. Then choose one", with "I took the headset off and it is back on"
   and "I did not take it off". Then the passthrough watch: a 30-second
   countdown with "Something changed", which can be clicked at any moment,
   and "Passthrough unchanged", which can be clicked when the count reaches 0.
5. **Result.** The run in plain words (for example "bottle seen in 9 of 11
   seconds while it was held in view"), the report's path and **Close**.

Stop, closing the window or a signal during the run ends the helper cleanly
and still leaves the report. One launch is one run: a second run is a second
launch, a second notice and a second Start.

## The two scripts

`--script removal`, at most 30 seconds:

| Step | The wearer is told | Mark | Recorded |
| --- | --- | --- | --- |
| 1 | Hold the bottle about half a metre in front of you, then click Ready | `near`, at the click | about 5 s, with the bar |
| 2 | Now about a metre away, then click Ready | `far`, at the click | about 5 s, with the bar |
| 3 | Click, then take the headset off. Put it back on when you like | `remove`, at the click | until the run ends |

After the third click the screen says "You can take the headset off now". The
run ends by itself when the helper reports `headset_removed` or `standby`, or
on its bound.

`--script positions`, at most 25 seconds, no click after Start: centre, left,
right, up and down for about 4 seconds each, each with a dot on the screen
showing where to hold the bottle and its mark made when the instruction
appears, then `out_of_view` for about 3 seconds.

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
shows the measured values and judges nothing; the numbers G7 should carry for
a click made before removal are the owner's to decide.

## How the run is launched on the Frame

After the owner has approved the step, and only then, the Frame manager
starts the window for the wearer from the Frame's normal graphical session,
so that it appears on the Frame's desktop:

```bash
.venv-replay/bin/python scripts/camera_guide.py --allow-camera-input \
  --helper /path/outside/the/checkout/sightcircuit-camera-helper \
  --script removal --report artifacts/camera/guide-removal-01.json
```

The second run is a second command, with `--script positions` and another
report path. `--report` is required and is checked before the window opens: a
new `.json` path in an existing directory. An existing file, a missing
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
track, and "Something changed" is how the wearer says so.

## The report

`camera_recognize`'s [report](camera-recognize.md#the-report), with
`confirmation: operator_window` and a `guide` block:

- `script`, `confirmation`, `started_by_start_button`, and `ended_by`
  (`helper`, `stop_button`, `window_closed`, `cancel_button`, `signal` or
  `not_started`);
- `steps`: per step its label, how it begins, whether it was reached, the
  time of its mark and the index of that mark among the recorded marks;
  `steps_not_reached`; `out_of_time`;
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
moment starts the script's clock, so the first step is not spent on a helper
that is still connecting. Any other line from the worker is dropped and
counted. What the run screen shows is `Guide.view`
(`sightcircuit/camera/guide.py`), a function of the script, the clicks, that
ready time and the clock. The report crosses to the window only after
`recognize()` has returned, which is after the helper has ended. If the
window's process dies, the worker sees its input close and stops the helper.

## Cloud checks

`tests/test_camera_guide.py` covers the script logic and the notice derived
from the contract (standard library), the worker against the stand-in helper,
the window offscreen with Qt's test tools, and `--validate`:

```bash
bash scripts/build_camera_helper.sh --fake
.venv-replay/bin/python scripts/camera_guide.py --validate
```

`--validate` clicks through the whole window offscreen against the helper's
fake backend with a scripted detector: both scripts, a run that reaches its
bound, a presence end, Stop, the window closed mid-run, Cancel, a declined
start and a run too short for its script. It refuses any helper that does not
describe itself as the fake before a window is made, so it can never open a
camera. These are cloud and CI results; none is a Frame result.
