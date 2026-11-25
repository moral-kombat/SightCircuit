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

**One run on the Frame so far.** Run 1 of L-D1 went through this window on
2026-11-19 ([E100](../evidence/e100.md)): the wearer found, started and
finished it unaided with the `removal` script, the bottle was detected in
none of 150 frames, and the run ended on its bound, not on a presence stop.
Everything else here, the `positions` and `scan` scripts, the region
options and the start hold included, has been checked in the cloud only: offscreen, against the
camera stand-in and the helper's fake backend. No time or count on this page
is a Frame measurement unless it says so.

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
   commands. When the window was opened with `--hold-start`, the line that
   counts down says "Start is off until the Frame manager says go. Please
   wait" instead, and Start stays off until the Frame manager
   [releases it](#running-a-companion-beside-the-window); then the line
   says "The Frame manager has said go. You can start in 2 s" and, when
   Start is live, "The Frame manager has said go. You can start now". The
   hold adds no line to this screen, so the notice above it is as fully
   visible as in any other window. The release starts nothing: the wearer's
   click does.
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
- **A held Start is never released** (`--hold-start` was given and no
  release file appeared within 10 minutes of the window opening). The window
  ends as a cancelled one does, on whichever screen it was: `status: error`,
  `reason: start_hold_not_released`, `guide.ended_by: start_hold_timeout`,
  nothing launched, and the result says "Not started: the Frame manager did
  not say go in time. Nothing was launched. Tell the Frame manager."

## The three scripts

Each is shown to the wearer before Start, and each takes the command's whole
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

`--script scan` needs no click after Start either, and is one instruction
for the whole run: "Hold the bottle upright, straight ahead of you, about
half a metre away. Keep it still", marked `near` as soon as the reader is
ready, with the bar running for 28 seconds. What it is for is in
[the scan](#the-scan-where-in-the-image-and-which-way-up) below.

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
matching bound on a late frame). On 2026-11-20, with the presence stop below,
the owner replaced the second clause: the helper's end record follows
SteamVR's signal by at most 1.0 s with at most 5 frames received after it,
and the watch was armed and not lost, with the click before the signal (row
G7 of the [acceptance table](../live-capture.md#edge-hud-acceptance-gates)).

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

## A region of view, and the detector

Work-order item L14, after [L-D1's first run](../live-capture.md#camera-display-work-order-adopted-2026-11-16)
found the bottle in none of 150 frames: the camera frame is 2464 pixels
square and very wide, the run resized all of it to 960, and a hand-held
bottle is then too small for the detector. The owner decided on 2026-11-20
that the detector looks at the middle of the wearer's view, enlarged, and
that the second detector is tried.

- `--region X0,Y0,X1,Y1` makes the detector look at that rectangle of the
  frame only, given as fractions with the origin at the top left
  (`0.25,0.25,0.75,0.75` is the centre half). The rectangle is taken out of
  the native frame before the resize, so the object is larger in what the
  detector sees; nothing outside it is copied.
- `--rotation 0|90|180|270` turns what the detector sees by that many degrees
  clockwise.
- `--detector yolox-nano` chooses the second detector (`mobilenet-ssd` is the
  default).

They are [`camera_recognize`'s options](camera-recognize.md#a-region-of-view)
and apply to the `removal` and `positions` scripts. Every box, cell, side and
coverage count stays over the whole frame: boxes are mapped back, and each
frame record names its space (`whole_frame_processing_pixels`). The report's
`view` block records the region, the rotation and the detector used. A
malformed region is refused on the command line, before any window. Without
`--region` and `--rotation` nothing is cropped and the report is what it was.

Neither the region nor the rotation is a finding. Where "straight ahead"
falls in the left camera's image, and which way up that image is, have not
been measured, and no calibration data is read to find out. That is what the
scan is for.

### The scan: where in the image, and which way up

One bounded 30-second run, the wearer holding the bottle still and straight
ahead at about half a metre:

```bash
.venv-replay/bin/python scripts/camera_guide.py --allow-camera-input \
  --helper /path/outside/the/checkout/sightcircuit-camera-helper \
  --script scan --report artifacts/camera/guide-scan-01.json
```

Each processed frame is seen through the next entry of a fixed schedule, and
the schedule starts again when it ends. An entry is one of six windows at one
of four rotations, 24 entries in all:

| Window | Fractions `x0,y0,x1,y1` | Why |
| --- | --- | --- |
| `whole` | `0,0,1,1` | The run as it was |
| `centre_half` | `0.25,0.25,0.75,0.75` | The owner's choice: the middle, enlarged twice |
| `upper_left`, `upper_right`, `lower_left`, `lower_right` | `0,0,0.6,0.6` and so on | 0.6 of the frame each, overlapping by 0.2: an object up to a fifth of the frame lies whole in at least one, wherever it is |

Consecutive frames never share a rotation and every six frames visit every
window, so a hand that drifts during the run does not favour one entry. At 5
frames a second the schedule takes 4.8 seconds; a 150-frame run goes through
it just over six times, which gives each entry 6 or 7 frames, each rotation
37 or 38 and each window 25.

Every detector named with `--scan-detectors` (default both,
`mobilenet-ssd,yolox-nano`) looks at each frame's one prepared image, so the
frame budget is not divided between them. `--detector`, which must be one of
them, fills the report's usual fields (the per-frame `detections`, `target`,
`summary` and the `inference_ms` statistics). `--scan-detectors yolox-nano
--detector yolox-nano` runs one. `--region` and `--rotation` are refused with
this script.

A scan needs bounds it can serve: at least 10 seconds and 48 frames (two
passes of the schedule). Less is refused on the command line, before any
window. The bar on the run screen runs for the run's time bound less two
seconds, so it and the run end together.

The report gains `scan`:

- `schedule`: the 24 entries as run (`index`, `name`, `region`, `rotation`,
  and where that region lay, `crop_native_xyxy` and `resized_size`). A frame
  record carries only `view.index`, the entry it was seen through;
- per detector: `frames`, `target_frames` and their `share`, that detector's
  inference p50, p95 and maximum, and four tallies, `entries` (per schedule
  entry), `by_rotation`, `by_axis` and `by_region`. Each tally row has the
  frames tried, the frames with the target, their share, the best and the
  median confidence, and the median target box and its centre as a fraction
  of the frame, over the whole frame. Groups differ in size (38, 38, 37 and
  37 frames per rotation in a 150-frame run), so rows are compared by share;
- `best_entry`: the entry with the largest share of its frames holding the
  target, among entries tried at least 3 times, or `null`. Three is under
  half of an entry's 6 or 7 frames, and it stops one lucky frame (1 of 1)
  from outranking 5 of 6. It is an aid to reading, not a verdict;
- `failed` per detector and `detectors_failed`: see below.

The run detector's result for a frame is the record's own `target`; the
other detector's is under the record's `scan`. Neither is repeated.

**The scan gives the axis, and may not give which way up.** A detector finds
a bottle at a turn and, somewhat less often, at the opposite turn: a bottle
looks much the same from both ends. In the independent review's cloud check an
image stored a quarter turn off was found at 90 degrees in 6 of 12 frames
and at 270 in 4 of 12. So read `by_axis` first (`0_180` against `90_270`):
that is what one scan can settle. Treat the leading rotation inside the axis
as a lean, not a finding, unless its share is far ahead. Which way up is
settled by a run that marks up and down: the `positions` script with
`--region` and either rotation of the axis found, where the `up` and `down`
intervals' `rows` and `columns` tallies say which side of the image is the
wearer's up. That needs counts only.

After the axis, the median box says where straight ahead falls in the image,
and `by_region` and `entries` say which window a follow-up run should use as
its `--region`. Per-entry counts are small (6 or 7 frames), and a detector
can name a bottle that is not there, so a result carried by one entry is
weak.

**No coverage figure.** A scan's 150 frames are 24 different views, so the
share of 1-second windows that held the bottle would not be row G7's
coverage, and the report does not offer one: `summary.coverage` is `null`
with `summary.coverage_omitted: scan_pools_views`, the intervals and windows
carry no target counts, and the result screen has no "seen in X of Y
seconds" line. The scan's own tallies are the reading.

**If one detector fails.** A detector that raises, or returns something that
is not a result, is not used again in that run. The other carries on to the
bound and keeps its tallies; the failed one's tallies cover the frames before
it stopped. The report says which (`scan.detectors_failed`, and per detector
`failed` with a closed `reason`, `detector_raised` or
`invalid_detection_result`, and the `frame_index`; never the exception's
text), and the result screen says "yolox-nano failed after 7 frames
(detector_raised). Tell the Frame manager." If the run's own detector is the
one that failed, its records are marked `run_detector_failed` and its usual
fields are empty from there on. If every scan detector fails, the run ends
`error`, `processing_failed`, as any run with a failing detector does; outside
a scan a failing detector still ends the run at once.

The window shows what it always shows during the run: the instruction, the
bar and the time left. After the helper has ended, the result screen gives,
per detector, its count ("yolox-nano: bottle in 31 of 150 frames"), the two
leading rotations by share ("by turn of the image: 90 degrees 29 of 38, 270
degrees 22 of 37", with "(a tie)" when the shares are equal) and the two
leading regions, then one line saying that a turn and its opposite look
alike to the detectors.

**Cloud figures, not Frame measurements.** On a 4-core 2.3 GHz Xeon in the
cloud, with synthetic 2464-pixel frames from the camera stand-in at 5 frames
a second: processing p95 was 37 ms for the whole frame and 32 ms for the
centre half with `mobilenet-ssd`, 20 and 18 ms with `yolox-nano`, and 59 to
71 ms for the scan with both detectors (maximum 136 ms). A scan report from
that machine was about 0.23 MB for 127 frames, against 0.12 MB without the
scan. The one Frame figure here is E100's: a processing p95 of 94 ms with
`mobilenet-ssd` on the whole frame. `yolox-nano` has not been timed on the
Frame's camera path, so the scan's time per frame there is not known; if
frames are replaced in the latest slot, the schedule follows the frames that
were processed and the report's `pending_frames_replaced` and timings say
so. Whether either detector finds the bottle through any window on the Frame
is what the run is for.

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

### Running a companion beside the window

Some runs need another process running beside them from before the first
frame: the presence watcher observing only, a temperature sampler. In
the scan run of 2026-11-24 ([E103](../evidence/e103.md)) the plan was to start the watcher once the wearer
reached the Start screen, and the wearer, who sees only the window, clicked
Start before the Frame manager had said go, so the watcher never ran.
`--hold-start PATH` closes that gap. It is off unless given, and without it
the window and its report are what they were.

1. The Frame manager opens the window with the hold, naming a release file
   that does not exist yet, beside the report:

   ```bash
   .venv-replay/bin/python scripts/camera_guide.py --allow-camera-input \
     --helper /path/outside/the/checkout/sightcircuit-camera-helper \
     --script scan --report artifacts/camera/guide-scan-02.json \
     --hold-start artifacts/camera/guide-scan-02.go
   ```

2. The wearer goes through the screens as usual. On the notice screen Start
   is shown but off, with "Start is off until the Frame manager says go.
   Please wait".
3. The Frame manager starts the companion and sees that it is up.
4. The Frame manager releases the start with one command:

   ```bash
   touch artifacts/camera/guide-scan-02.go
   ```

5. Within about 0.3 seconds the screen says "The Frame manager has said go.
   You can start in 2 s"; 1.2 seconds after the release Start can be clicked
   and the line says "You can start now". The wearer clicks Start, and that click starts the run.

What the hold does and does not do:

- **The release never starts the run.** It only lets Start be clicked. The
  notice's own 5-second wait still applies, and Start is dead for the usual
  1.2 seconds after the release, so a click already on its way when the
  button comes alive lands on nothing. A click made while Start is held
  does nothing and is not recorded as a start.
- **The path is checked before the window opens**, after the report's path.
  Nothing may be at it yet, not even a dangling link, so a file left by an
  earlier run cannot release this one. Its directory must exist and be a
  real directory, not a link to one, and must be either the report's own
  directory or one the user owns and can write to with no write permission
  for group or others (so `/tmp` itself is refused, and a private directory
  under it is accepted). It cannot be the report's own file, by whatever
  name: the two are compared by their resolved directories and their names.
  It cannot have a `..` in it, because the window takes the path as written
  and would drop a link before the `..` that the operator's `touch` follows,
  so the two would mean different places. A `~name` the system does not
  know is refused too (and for `--report`, as `report_path_refused`). A
  refused path ends the command with `reason: hold_start_path_refused` and
  nothing shown. Otherwise only the path and its directory are examined; a
  link further up the path is not looked for.
- **The window looks for the file four times a second** from the same timer
  that moves its counts; there is no thread and nothing that outlives the
  window. It asks only whether a regular file is at that path (`lstat`). A
  link or a directory put there is not a release. The file is never opened,
  so what is in it does not matter, and the command neither creates nor
  removes it: the Frame manager removes it afterwards, or names another path
  for the next run.
- **The release is kept once seen.** Removing the file afterwards does not
  hold Start again. To call a run off after releasing it, close the window
  or send it a signal, as before.
- **The release can come early.** A file made while the wearer is still on
  the first two screens counts; Start is then held only by the notice's own
  wait.
- **An unreleased hold ends by itself** 10 minutes after the window opened,
  with the report and the result screen of a run that was not started
  ([above](#when-a-run-does-not-go-as-scripted)). Once released, there is no
  bound: the wearer starts when ready, or cancels.
- **Nothing camera-derived is involved.** The hold is the window's alone
  (the worker process is not told of it), it ends before anything is
  launched, and what it puts in the notice screen's one line about Start
  comes from the release file's presence and the clock.

The report says what happened, under `guide.start_hold`
([below](#the-report)): whether the start was released, when, and how long
after the release Start was clicked. That delay is how a reader checks that
the companion had the time it needed.

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
`schema_version` is 3 since the scan script; the recognition report's own is 4
since the presence stop, 3 since the region of view):

- `script`, `confirmation`, `started_by_start_button`, and `ended_by`
  (`helper`, `stop_button`, `window_closed`, `cancel_button`, `signal`,
  `not_started` or, for a held Start nobody released, `start_hold_timeout`);
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
- `removal_limits_ms`, the two limits the removal entries were compared with;
- `start_hold`, only when the window was opened with `--hold-start` (the
  block's `schema_version` stays 3, and a report without the option has no
  such key): `requested` (always `true`), `released`,
  `release_monotonic_ns` and `release_utc_ns`, `release_to_start_click_ms`,
  `timed_out`, `timeout_seconds` (600), `poll_interval_ms` (250) and
  `release_file_read` (always `false`). The release time is the window's
  tick that first saw the file, so it is later than the `touch` by up to
  about 0.3 seconds (the poll interval and a tick). `release_utc_ns` is that time by the run's clock (the
  report's `timebase`), and is `null` when no run was launched, because
  there is then no run clock. The delay is `null` when Start was never
  clicked. Neither the file's path nor anything from inside it is recorded.

A launch that started no run (Cancel, "Something else", a window closed
before Start, a held Start nobody released) still leaves the report:
`status: error`, reason `confirmation_declined`, `starting_state_not_stable`
or `start_hold_not_released`, `confirmation: null` and no `capture` block,
because nothing was launched.

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
button texts at 4:3, 5:4 and portrait sizes), the start hold (Start dead
until the release and for the guard interval after it, the release starting
nothing, the report's fields, the bound, the refused paths, the whole
notice visible without scrolling at 1920 by 1080, 1600 by 900, 1366 by 768
and 1280 by 800 for every script whether held or released, no open of the
release file over a whole run (an audit hook), and a window without the
option never looking for a file), and `--validate`:

```bash
bash scripts/build_camera_helper.sh --fake
.venv-replay/bin/python scripts/camera_guide.py --validate
```

`--validate` clicks through the whole window offscreen against the helper's
fake backend with a scripted detector: the removal and positions scripts and
the scan, a run that reaches its bound, a presence end, Stop, the window closed mid-run, a launch that fails,
Cancel, a declined start, a run too short for its script, the refused report
paths and the worker refusing a launch by hand. The start hold's flows are in
their own block of the validation report, `start_hold_flows`, and count
towards `flows_failed` like the rest: Start refused while held and the run
started by the click after the release file is made, a hold nobody releases,
and the release paths that are refused before a window exists. Its guard and
dwell times are shortened so that it runs in about three quarters of a
minute. It refuses any helper
that does not describe itself as the fake before a window is made, so it can
never open a camera. These are cloud and CI results; none is a Frame result.
