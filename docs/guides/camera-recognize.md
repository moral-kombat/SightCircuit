# Recognize objects on frames the camera helper exports

`camera_recognize` is the fourteenth command: bounded CPU recognition, without
display, on frames that `sightcircuit-camera-helper --export-frames` writes on
its stdout. It is the Python half of the proof of concept that lane S builds
toward ([current work](../live-capture.md#current-work-order)); the helper's
export mode is the native half. This guide describes the command as it exists
and is checked in the cloud against a fake producer. It has run on the Frame
against the helper and the camera ([E98](../evidence/e098.md),
[E99](../evidence/e099.md)); those entries are the device results, and no
device run is approved by this page.

[Scope](../poc-scope.md) · [Gates](../../sightcircuit/gates.py) ·
[Input review](../history/2026-11-09-arcturus-sdk-input-review.md) ·
[Mirror recognition, the pattern it follows](mirror-recognition.md)

## Gate statement

Every gate stays closed and none is lifted by this command. The report of
every run carries `gates.claims()`: `production_live_input_enabled`,
`production_camera_input_enabled` (since work-order item L10),
`mapping_qualified` and `production_hud_enabled` are `false`, and a
`gate_statement` field says the following. On 2026-11-09 the owner classified
bounded camera recognition without display (counts, labels, boxes and timings
only, no saved image, at most 30 seconds) as a confirmed use outside the
live-input gate, exactly as the bounded SteamVR mirror recognizer of
[E48](../evidence/e048.md) is; that classification changed no gate, and nothing
here feeds the live `Pipeline` or the HUD path. The command never builds the live
pipeline, opens no window and writes no image. A run against the helper's SDK
build on the Frame is still a device step with its own approval, under the
accepted passthrough rule (any wearer-visible passthrough change during a run
or in the 30 seconds after stops the SDK track); nothing in this guide
approves one, and until one is approved the command runs only against the
fake producer.

## Camera Data

Under the Arcturus camera SDK agreement (sections 1 and 3.2) the detections,
labels, scores and boxes derived from camera frames are Camera Data, and so is
every per-frame record in the report. The command says so in its docstring and
in the report's `camera_data` block. The owner's consent decision of
2026-11-09 allows counts, labels, boxes, inference timings and format metadata
to be saved locally on the Frame and published in evidence; pixels, stills,
previews and calibration data are never saved or published anywhere, and the
camera's red LED is untouched. The command saves nothing on its own: the
report goes to stdout, and to a file only when the operator names a new
`.json` path with `--report`, which is the operator's explicit action to save
Camera Data. The report never contains pixels (`pixels_in_report: false`),
and no preview, still or other file is written.

## What the command does

1. Asks for the informed start on its own terminal (owner decision
   2026-11-09): prints the helper's export notice verbatim
   (`contract.EXPORT_NOTICE`, the same text the generated header gives the
   helper) with the run's bounds, requires an interactive stdin (otherwise
   `confirmation_requires_terminal`, nothing launched) and the operator's
   exact typed `yes` (anything else is `confirmation_declined`); no flag,
   environment variable or piped answer stands in. The report records
   `confirmation: operator_terminal`, and the helper is launched with
   `--parent-confirmed`, which it accepts only with `--export-frames`; its
   `ready` and `end` headers then say `confirmation: parent`.
2. Loads the detector (`--detector`, MobileNet-SSD by default) before anything
   is launched; a missing model ends the run `error`.
3. Launches the helper binary named with `--helper`, in `--export-frames` mode
   with `--fps`, `--max-seconds`, `--max-frames` and `--cameras`, through the
   supervision primitive (`sightcircuit.runtime.supervision.supervise_owned`):
   an owned session, a deadline of the time bound plus ten seconds, SIGTERM
   then group escalation, and a cleanup verdict independent of the helper's
   claims. The path is given explicitly and never searched; a path inside the
   checkout is refused unless it is under the ignored `artifacts/` directory,
   where `scripts/build_camera_helper.sh` writes, so no tracked file can be
   run as the helper.
4. Reads the helper's stdout with the strict export reader
   (`sightcircuit/camera/export_reader.py`), which refuses the stream on the
   first violation of the contract with a named reason and then stops the
   helper. The helper's stderr (JSON diagnostic lines) is collected into the
   report, bounded to 256 lines of at most 8 KiB, and never printed raw.
5. Keeps the latest validated frame in a one-frame slot and processes it on a
   second thread: conversion to at most 960 pixels on the longer side (the
   detectors' processing bound), the one-second input age limit (a frame whose
   acquisition is older is counted `stale_inputs_skipped`), then the detector.
   A frame replaced before it was processed counts as `pending_frames_replaced`.
6. Ends when the helper ends on its bound (`max_frames` or `max_seconds`), and
   reports.

During the export it also keeps time and takes marks
([below](#times-marks-and-the-target-summary)): a cue you scheduled is
printed with a bell when due, and, when key marks were asked for, Enter
records a mark. With none of the new options nothing is printed during the
run, the terminal is not read or flushed after the confirmation (what is
typed stays in its input queue, as before), the helper is launched with the
same arguments, and the status and exit code are what they were; the report
only gains the times.

Ctrl+C or SIGTERM cancels: the primitive sends the helper SIGTERM, which the
helper collects between SDK calls and answers with an `end` packet of reason
`stop_signal`; the command exits 128 + signal.

## Run it

Both bounds are required and both are checked by the helper and the command:
`--seconds` is at most 30, `--max-frames` at most 300, `--fps` at most 10
(default 5). The helper's own bounds are wider (90 seconds, 900 frames,
camera export v2, work-order items S6 and L4); the command never asks for more than its own. `--allow-camera-input` is the opt-in, named like the observer's
flags; without it nothing is launched.

**Rebuild the helper first.** The checkout speaks camera export v2. A helper
built from an earlier checkout writes version 1, and the reader refuses its
first packet (`refused`, `unsupported_version`) before any frame is read, so
the operator rebuilds the helper on the Frame
(`bash scripts/build_camera_helper.sh --sdk DIR`, the build step of the [probe guide](camera-probe.md#preparation))
before any run with this checkout.

```bash
.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper /path/outside/the/checkout/sightcircuit-camera-helper \
  --seconds 10 --max-frames 50 --fps 5 --cameras 0,1 \
  --report artifacts/camera/run-01.json
```

The terminal confirmation is the command line's only path to the helper. Its
code is `sightcircuit/camera/confirmation.py`, shared with
[`camera_hud`](camera-hud.md) since work-order item L9 so that neither
command imports the other; this command's behaviour is unchanged. The
library functions beneath it, `camera_recognize.recognize(confirmed=...)` and
`camera_helper.run_export(confirmed=...)`, trust their caller's word that the
operator confirmed: they exist for the tests (which pass `confirmed=True`
against the stand-in) and for a caller that performs its own informed
start. One command does: [`camera_guide`](camera-guide.md), the guided
window (2026-11-19), shows the same notice above a Start button and calls
`recognize()` after the wearer's click, recorded as `confirmation:
operator_window`. No other shipped command calls them without the typed
answer, and this command's own start is still the typed `yes`.

`--cameras` takes `0`, `1` or `0,1` (camera index 0 is reported as the left
camera and 1 as the right; the assignment is the helper's, made from the
camera transforms as the SDK example does it, and nothing in Python verifies
it). `--helper-arg` passes an extra argument through to the helper after the
contract ones, for a fake build's `--fake-scenario`. The console entry point
of an editable install is `sightcircuit-camera-recognize`.

Expect `report_kind: camera_recognition`, `status: recognized`, exit 0 and
`cleanup_ok: true` when at least one frame was processed and the helper ended
on its bound. Other statuses: `no_frames` (nothing processed; `reason` says
whether every frame was stale), `refused` (the stream violated the contract;
`reason` names the rule), `helper_ended_early` (the helper's own `end` reason:
`headset_removed`, `standby`, `sdk_error`, `pixel_path_error`, `watchdog`,
`consumer_stalled`), `presence_ended` and `presence_watch_lost` (only with
`--presence-watcher`; see [the presence stop](#the-presence-stop---presence-watcher)),
`cancelled`, `timed_out`, `stopped`, `error` (a helper that exited non-zero,
a failing detector, an unusable helper path, a presence watch that could not
be armed) and `cleanup_failed`. Every
status but `recognized` exits 1, a cancellation 128 + signal.

## The presence stop (`--presence-watcher`)

The helper's own stop on headset removal waits for the camera to go quiet: it
ends `headset_removed` or `standby` after a 2.0 s gap in frames. Device step
L-D1's first run ([E100](../evidence/e100.md)) showed that on the Frame no
gap comes: frames kept arriving at five a second after the headset was off,
while SteamVR reported standby 7.0 s after the wearer's click. The owner
decided on 2026-11-20 ("Yes, use SteamVR's signal (Recommended)") that the
run takes "the headset is off" from SteamVR. The helper's frame-gap stop
stays as the second line of defence.

**Nothing here has run on the Frame.** The watcher has been checked in the
cloud against a fake OpenVR runtime and a stand-in; whether SteamVR on the
Frame delivers the event to a background application, and how soon, is for a
device run to show.

```bash
bash scripts/build_presence_watcher.sh
.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper artifacts/camera-helper/sightcircuit-camera-helper \
  --presence-watcher artifacts/presence-watcher/sightcircuit-presence-watcher \
  --seconds 30 --max-frames 150 --fps 5 --cameras 0 --report artifacts/camera/run-01.json
```

`--presence-watcher` names `sightcircuit-presence-watcher`
(`tools/presence_watcher.cpp`), held to the helper's path rules: given
explicitly, never searched, and inside the checkout only under `artifacts/`.
After the typed `yes` and before the helper, the command launches it with a
bound of the run's seconds plus 20 and waits for its `ready` line.

**What the watcher is.** A small OpenVR client that joins a running SteamVR
as a `VRApplication_Background` application, which never starts SteamVR. It
asks for `IVRSystem` and nothing else. It creates no overlay, writes no
setting, requests no tracked-camera interface and reads **no camera data, no
image and no calibration value**: only runtime events and the headset's
activity level. It ends on SIGTERM, with its parent, on its own bound, or when
SteamVR quits, and a loop step that blocks for five seconds ends it. Its
output is strict JSON lines (`presence-watch/1`), each with a sequence number
and the host's monotonic and realtime clocks: `ready`, `presence`, a
`heartbeat` every 500 ms and `end`. `--describe` prints the signal table and
these facts without calling SteamVR.

**Which signal ends a run.** One table, in `tools/presence_signals.hpp` and
`sightcircuit/camera/presence.py`; a line that disagrees with it is refused.

| Signal | From (pinned OpenVR header) | Ends the run |
| --- | --- | --- |
| `enter_standby_event` | `VREvent_EnterStandbyMode` | yes, with reason `standby` |
| `leave_standby_event` | `VREvent_LeaveStandbyMode` | no; recorded, and nothing restarts |
| `proximity_off`, `proximity_on` | `VREvent_ButtonUnpress` and `VREvent_ButtonPress` of `k_EButton_ProximitySensor` on the headset | no; recorded |
| `user_interaction_ended`, `user_interaction_started` | `VREvent_TrackedDeviceUserInteractionEnded` and `...Started` for the headset | no; recorded |
| `hmd_activity_changed` | `GetTrackedDeviceActivityLevel` of the headset, polled every 20 ms | no; recorded |

Only SteamVR's standby event ends a run, because it is SteamVR's own
statement that the headset is idle and the one signal E100's journal shows on
the Frame. The others may well be faster, and each is recorded with its time
so that a device run can show how much. None ends a run yet: none has been
observed on the Frame, and each can fire with the headset still on (a hand
at the proximity sensor, a still head; the header gives the activity level's
standby as "idle for at least 5 seconds"). Ending on one of them is a
one-line change of the table once a device run supports it.

**When the signal comes** the run's stop is set at once: the helper is sent
SIGTERM and ends `stop_signal` between SDK calls, the watcher is ended, and
the report says `status: presence_ended`, `reason: standby`, exit 1. A signal
that arrives before the helper is launched launches nothing
(`helper_launched: false`). A signal after the helper's own end record, or
after a stop someone else asked for, is recorded and ends nothing.

**Fail closed.** A run that names the watcher does not read the camera
without it, because a removal stop that silently is not there is the failure
E100 found:

- If the watch cannot be armed, the run ends `status: error`, `reason:
  presence_watch_not_armed`, no helper is launched and
  `presence_watch.refusal` says why: `watcher_not_found`,
  `watcher_inside_work_tree`, `watcher_not_executable`,
  `watcher_launch_failed`, `watcher_not_ready_in_time` (12 s),
  `watcher_error:vrserver_not_running` (SteamVR is not running),
  `watcher_error:headset_not_connected`, `headset_in_standby_at_start` (the
  ending signal is an edge that would never come), or the rule a `ready` line
  broke (for example `ready_camera_data_read_refused`).
- If the watcher is lost during the run (it exits, writes nothing for 3 s, or
  writes a line that is not accepted), the helper is stopped at once and the
  run ends `status: presence_watch_lost` with the reason (`watcher_exited`,
  `heartbeat_missed`, `watcher_ended:runtime_quit`, `line_not_json`, ...).
- A run without the option is what it was, and its report says so:
  `presence_watch.requested` and `.armed` are `false`, and each removal entry
  has `presence_watch_armed: false`.

`presence_watch` in the report: `requested`, `armed`, `refusal`; the
watcher's path and SHA-256; `launched`, `armed_at` and the watcher's `ready`
(the headset's activity level, whether it reports a proximity sensor, the
interface version); `events`, at most 64 presence events each with `signal`,
`ends_run`, `end_reason`, the headset's activity level, the watcher's
`monotonic_ns` with `utc_ns`, `received_monotonic_ns`, `event_age_ms` and
`after_run_end` (`event_count` covers them all); `ending_signal` and
`ended_run`; `run_end_reason`; `signal_to_helper_end_ms`, from the signal to
the helper's end record; `frames_received_after_signal`; `lost`;
`heartbeats`; the watcher's `end` line, `exit_code`, `forced_kill` and
`cleanup_ok`, which counts towards the report's. The watcher stamps with
`CLOCK_MONOTONIC`, the clock the report's timebase uses. Counts, closed names
and times only.

## Times, marks and the target summary

Work-order item L15 added these so that a device run can be measured (the
removal stop, the coverage and the cued positions of step L-D1 in the
[camera display work order](../live-capture.md#camera-display-work-order-adopted-2026-11-16)).
They are checked in the cloud against the fake producer only. The command's
bounds are unchanged (30 seconds, 300 frames), it still opens no display and
saves no image, and everything added to the report is a count, a label from a
closed list, a box, a time or a declared constant.

**Times.** The host monotonic clock is the measurement: the helper stamps
each frame with it at acquisition (`observed_ns`), and the command reads the
same clock for marks and for the helper's end record. One realtime sample at
the run's start (`timebase.anchor`, UTC nanoseconds since the Unix epoch)
places those times in the day: every `utc_ns` in the report is the anchor
plus a monotonic difference, never a second reading, so two times differ by
the same amount on either clock. `timebase.wall_clock_step_ns` is how far a
second realtime reading at the end differed from the derived one (a clock
step or slew during the run). Each frame record also carries the helper's own
realtime stamp, `helper_realtime_ns`, as the header gave it.

**Marks.** A mark is a label and a time. The labels are a closed list:
`remove`, `reseat`, `near`, `far`, `left`, `centre`, `right`, `up`, `down`,
`out_of_view`, `in_view`. There is no free text: any other label is refused
before the confirmation is asked, and what you type during a run is counted
for line ends and discarded.

- `--cue SECONDS:LABEL` (repeatable, at strictly rising times within
  `--seconds`, at most 32) schedules a cue from the first frame's acquire
  time. When it is due the terminal shows `CUE <label>` with a bell, and the
  mark records the scheduled and the actual time (`late_ms` apart). A cue the
  run ended before is listed in `cues_not_reached`. The bell is the terminal's:
  check before a device run that the wearer can hear or see it.
- With `--key-marks` or `--mark-labels`, and only then, Enter on the terminal
  during the run records a key mark, for the moment only the operator knows.
  `--mark-labels LABEL,LABEL` gives successive presses their labels; further
  presses, or all of them under `--key-marks` alone, record `mark`. The
  terminal shows `MARK <label>`. With either option the terminal's pending
  input is discarded when the run starts and everything typed during the run
  is read and dropped; without them the terminal is left alone. Key marks
  need the terminal the confirmation was typed on. At most 64 marks are
  recorded; every scheduled cue keeps its place, key marks take the rest (at
  least 32) and further presses are counted (`marks_dropped`).

A cue's time is when it was shown, not when the wearer acted on it. Every
`remove` mark, cue or key, gets its own entry in `summary.removal`, so a run
can use a cue as the prompt and an operator key mark (`--mark-labels remove`)
as the moment the headset is actually off. The helper itself has no presence
signal (the [presence stop](#the-presence-stop---presence-watcher) is the
command's): it ends `headset_removed` or `standby` once no frame set has arrived for two
seconds ([helper reference](../reference/camera-helper.md)), found at the end
of an acquire that timed out. The time from a cued `remove` to the helper's
end therefore holds the wearer's reaction, then that two-second gap, then the
acquire timeout. `last_frame_acquire_after_mark_ms` separates them: it is
about when frames stopped after the mark, and the rest of
`helper_end_after_mark_ms` is the helper's.

Neither kind touches the informed start: the typed `yes` is read first, the
helper is still launched with `--parent-confirmed` and reads no terminal, and
Ctrl+C still cancels.

**Target.** `--target-label CLASS` names one class of the chosen detector
(`bottle` for both models; an unknown class is refused before the
confirmation). It needs a single camera, `--cameras 0` or `--cameras 1`
(the default `0,1` is refused with it), so no window, cell or coverage tally
pools the two cameras' frames. Without a target the `frames` counts are over
every selected camera. Each frame record then has `target`: whether the class was
detected at or above the threshold, how many boxes, and for the best one its
confidence, its box and the 3x3 cell of its centre over the full frame. The
cell uses 0.4 and 0.6 on each axis: a centre below 0.4 of the width is
`left`, above 0.6 `right`, otherwise `centre`; `up`, `centre`, `down` on the
height likewise; the nine names are `up_left`, `up`, `up_right`, `left`,
`centre`, `right`, `down_left`, `down`, `down_right`.

The cell is taken on the image as the helper exported it. Nothing in this
command knows the camera's mounting orientation, so `left`, `right`, `up` and
`down` name sides of the exported image, not of the wearer's view. The cued
run is what shows how the two relate; the report repeats this in
`target.orientation_statement` and gives no verdict on whether a cue and a
cell agree.

**Summaries** (`summary`, counts only, derived from the frame records and
the marks; frames are placed by acquire time). An interval or span is bounded
by a cue's scheduled time and a key or window mark's actual time (`boundary`:
`scheduled` or `actual`), so the span between cues at 3 and 20 seconds is 17
windows whatever the scheduler's wake-up jitter; the removal figures use each
mark's actual time:

| Field | Meaning |
| --- | --- |
| `windows` | 1-second windows from the first frame's acquire time to the last processed frame: `frames` processed in each and, with a target, `target_frames` |
| `intervals` | Per mark, from its `boundary` to the next mark's or the run's end (the helper's end record, else the export's return): `frames`, and with a target `target_frames`, the frames per cell (`cells`, all nine names), per `columns` and per `rows`; the whole 1-second `windows` from the mark, those with a frame and those with the target, and the `remainder_ms` left over |
| `coverage` | With a target. A span opens at `in_view`, `near`, `far` or a position mark and closes at the next `out_of_view` or `remove`, or the run's end. Per span and in total: the whole 1-second windows from the span's start, `windows_with_target`, `windows_without_frames` (they count against coverage) and `fraction` |
| `removal` | Per `remove` mark: `frames_processed_after_mark`, `frames_acquired_over_limit` (acquire time more than 2,000 ms after the mark), `last_frame_acquire_after_mark_ms`, `helper_end_after_mark_ms` and `helper_end_reason` from the helper's end record, `helper_end_is_presence_stop` (`headset_removed` or `standby`) and `helper_end_within_limit` (3,000 ms); `helper_end_after_last_frame_ms`, from the run's last processed frame to the end record (added 2026-11-19 for a `remove` mark made before the headset comes off); the two limits are repeated per entry (`late_frame_limit_ms`, `end_limit_ms`) and are this command's 2,000 and 3,000, which only the [guided window](camera-guide.md) can be given otherwise; the end fields are `null` when no end record was read. Since 2026-11-20, for the [presence stop](#the-presence-stop---presence-watcher): `presence_watch_armed`; `presence_signal` (the ending signal, `null` when none came); `presence_signal_after_mark_ms`, from the mark to the signal; `helper_end_after_presence_signal_ms`, from the signal to the helper's end record; `frames_received_after_presence_signal`; `run_ended_by` (`presence_watch` or `helper`) and `run_end_reason` (`standby` from the watch, else the helper's own reason). `helper_end_reason` stays the helper's own end record, which is `stop_signal` when the watch stopped it, and `helper_end_is_presence_stop` is then `true` as well. A run that ends only on its bound has it `false`, armed or not |

A run for the removal and the coverage, then one for the cued positions
(examples of the options, not an approved device step):

```bash
.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper artifacts/camera-helper/sightcircuit-camera-helper \
  --seconds 30 --max-frames 150 --fps 5 --cameras 0 --target-label bottle \
  --cue 3:near --cue 11:far --cue 20:remove --mark-labels remove \
  --report artifacts/camera/run-01.json

.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper artifacts/camera-helper/sightcircuit-camera-helper \
  --seconds 30 --max-frames 150 --fps 5 --cameras 0 --target-label bottle \
  --cue 2:centre --cue 7:left --cue 12:right --cue 17:up --cue 22:down \
  --cue 27:out_of_view --report artifacts/camera/run-02.json
```

### Validate the reader without any helper

```bash
python3 scripts/camera_recognize.py --validate
```

Runs the reader, in process, over the fake producer's valid stream and over
every invalid stream in its catalogue (`sightcircuit/camera/fake_export.py`:
every framing violation, every field violation, out-of-order kinds, oversize
headers and payloads, a truncated stream, a stream with no end), and reports
each expected and observed refusal. No helper, no detector, no camera;
standard library only. Expect `status: validated` and exit 0.

## The report

| Field | Meaning |
| --- | --- |
| `seconds_requested`, `max_frames_requested`, `fps_requested`, `cameras_requested` | The run limits, which the helper echoes in its `ready` packet (a different echo is a refusal) |
| `detector`, `threshold` | The model (`name`, `runtime`, class count) and the score threshold (`POC_THRESHOLD` by default) |
| `frames_received`, `frames_processed`, `stale_inputs_skipped`, `pending_frames_replaced` | Frames the reader delivered; frames the detector saw; frames older than `maximum_input_observation_age_ms` (1000) at processing; frames replaced in the latest slot |
| `labels_seen` | Every label detected in the run |
| `confirmation` | `operator_terminal` once the typed answer was given on this command's terminal; `null` when the run was refused before launch |
| `schema_version` | 3 since the presence stop (2026-11-20: `presence_watch`, the removal entries' presence fields, the statuses `presence_ended` and `presence_watch_lost`); 2 since item L15 (the times, marks, target and summaries). No earlier field changed |
| `presence_watch`, `helper_launched` | [The presence stop](#the-presence-stop---presence-watcher): whether SteamVR's headset-off signal was armed, each presence event with its times, what ended the run; `helper_launched: false` only when the watch kept the helper from being launched |
| `timebase`, `run_started`, `first_frame`, `helper_end`, `export_returned` | The declared clocks with the anchor, and `{monotonic_ns, utc_ns}` of the run's start, the first frame's acquire, the helper's end record as read (with its `reason`; `null` without one) and the export's return; see [times](#times-marks-and-the-target-summary) |
| `marks` | The vocabulary, the cues and key labels requested, `cues_not_reached`, `key_marks_enabled`, `key_presses`, `key_marks_capacity`, `marks_dropped` and `recorded`: per mark `index`, `source` (`cue`, `key`, or `window` for a mark the [guided window](camera-guide.md) records at a click; `window_marks` counts those, and this command makes none), `label`, `monotonic_ns`, `utc_ns`, `offset_ms` from the first frame's acquire (`null` before it), and for a cue the scheduled offset and times and `late_ms` |
| `target_label`, `target`, `summary` | The class named with `--target-label` (`null` without); its threshold, frame count, cell thresholds and the orientation statement; the counts-only [summaries](#times-marks-and-the-target-summary) |
| `detections` | One record per processed frame: `sequence`, `frame_set_sequence` (the acquired set; both cameras of a set share it), `source_id` (`arcturus_camera_left` or `_right`), `camera_index`, `frame_id`, `observed_ns` (the helper's host monotonic clock at acquisition), `observed_utc_ns` (derived), `helper_realtime_ns`, with a target `target` (`present`, `count`, `confidence`, `box_xyxy`, `column`, `row`, `cell`, `centre_fraction`), `input_age_ms`, `processing_ms`, `inference_ms`, `labels`, up to 20 `detections` (`label`, `confidence`, `box_xyxy` in `detector_input_pixels`), `detection_count`, `native_size`, `detector_input_size`; Camera Data |
| `last_result` | The last record of `detections` |
| `inference_ms`, `processing_ms`, `input_age_ms` | Count, sample size, nearest-rank p50 and p95 and the maximum, as the pipeline statistics report them |
| `inference_p95_budget_ms`, `inference_p95_within_budget` | `LIVE_INFERENCE_P95_BUDGET_MS` (200) and whether this run's p95 met it; which machine ran it is the report's business, so a cloud figure is never a Frame figure |
| `capture` | The source report: `helper` (resolved path, SHA-256, arguments), `supervision` (the primitive's block: pid, exit code, forced termination, tree reaped), `stream` (packets and frames read, frames per camera, dropped frames, the `ready` and `end` headers, `end_received_monotonic_ns`, the refusal if any), `diagnostics` (the collected stderr lines and the counts of dropped, non-JSON and oversize lines), `helper_exit_code` |
| `camera_data` | The Camera Data statement, `report_saved_by_operator` and `report_path` |
| `gate_statement`, `production_live_input_enabled`, `production_camera_input_enabled`, `mapping_qualified`, `production_hud_enabled` | The owner's 2026-11-09 classification in one sentence, and `gates.claims()`, all `false` |
| `cleanup_ok`, `images_saved`, `pixels_in_report`, `display_started`, `overlay_requested` | The primitive's cleanup verdict and the processing thread's exit; the last four are always `false` |

## The wire protocol, camera export v2

The helper's stdout is a sequence of packets and nothing else. A packet is a
16-byte little-endian prefix (the magic `SCCF`, the version 2, the header size,
the payload size), then the header (UTF-8 JSON, one object, at most 16384
bytes), then the payload (at most 33554432 bytes). The header's `kind` is
`ready` (first, once; backend, pixel path, build facts, the delivered format
`BGR3`, the camera selection, the limits in force and how the start was
confirmed, `parent` or `terminal`; no payload; a pixel path of `undecided`
means the helper ended the run before choosing one, and no frame may follow),
`frame` (sequence from 1
without gaps, the frame set it belongs to, numbered from 1 without gaps with
each camera at most once per set, frame id, camera index, exposure and host
clocks, width, height, `BGR3`, stride and buffer size equal to the payload,
derivation, the acquisition format with its DRM FourCC, modifier, planes,
pixel format, encoding, transfer and primaries, and the frames dropped since
the last) or `end` (last, once; the reason, counts, elapsed time, digests and
the confirmation; no payload). The helper's stderr carries JSON diagnostic lines only.

`sightcircuit/camera/contract.py` is the protocol as data: the prefix layout,
the bounds, the exact fields of each kind with types and bounds, the end
reasons, the format vocabulary (the helper's spelling of the SDK enumerators)
and the helper's run limits (`fps` 1 to 10, `max_seconds` 1 to 90,
`max_frames` 1 to 900, the bounds of work-order item S6 as raised by L4;
version 1 had 60 and 600, and the two versions refuse each other by the
prefix version, `unsupported_version`). `python3 scripts/camera_contract_header.py --write` generates
`tools/camera_export_protocol.hpp` for the helper and `--check` fails when it
is stale (CI runs it; `tests/test_camera_contract.py` pins it). The reader
enforces every rule and names its refusals (`export_reader.REASONS`).

## Cloud checks

`tests/test_camera_contract.py`, `tests/test_camera_export_reader.py`,
`tests/test_camera_marks.py` (the vocabulary, the cue schedule, key marks
through a pseudo-terminal and the summaries on scripted sequences) and
`tests/test_camera_helper_source.py` run in the dependency-free CI job (the
stand-in helper, `tests/fake_camera_export.py`, is installed outside the
checkout and launched exactly as the real helper would be);
`tests/test_camera_recognize.py` adds the end-to-end runs with a stand-in
detector in the replay job, including cued and key-marked runs on the fake
producer and a check that no field added by item L15 can hold pixels, free
text or calibration values. Nothing in CI builds against or links the SDK, and
nothing here ran on the Frame.

The presence stop: `tests/test_presence_watch.py` holds the line rules, the
watch against a stand-in watcher (`tests/fake_presence_watcher.py`: the
signal, the refusals, a watcher that dies, falls silent or writes a bad
line, a signal after the run's end, leaving standby, cleanup) and
recognition runs it ends, refuses or loses; `tests/test_presence_watcher_native.py`
builds nothing itself and runs the real watcher against the bridge tests'
fake OpenVR runtime, which scripts the events and logs that the watcher made
one Background connect, asked for `IVRSystem` alone and made no overlay call.
A fake runtime shows what the watcher calls, not what SteamVR sends.
