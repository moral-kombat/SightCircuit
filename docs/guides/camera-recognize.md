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
`consumer_stalled`),
`cancelled`, `timed_out`, `stopped`, `error` (a helper that exited non-zero,
a failing detector, an unusable helper path) and `cleanup_failed`. Every
status but `recognized` exits 1, a cancellation 128 + signal.

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
as the moment the headset is actually off. The helper has no presence signal:
it ends `headset_removed` or `standby` once no frame set has arrived for two
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
| `removal` | Per `remove` mark: `frames_processed_after_mark`, `frames_acquired_over_limit` (acquire time more than 2,000 ms after the mark), `last_frame_acquire_after_mark_ms`, `helper_end_after_mark_ms` and `helper_end_reason` from the helper's end record, `helper_end_is_presence_stop` (`headset_removed` or `standby`) and `helper_end_within_limit` (3,000 ms); `helper_end_after_last_frame_ms`, from the run's last processed frame to the end record (added 2026-11-19 for a `remove` mark made before the headset comes off); the two limits are repeated per entry (`late_frame_limit_ms`, `end_limit_ms`) and are this command's 2,000 and 3,000, which only the [guided window](camera-guide.md) can be given otherwise; the end fields are `null` when no end record was read |

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

### A region of view

Work-order item L14. The Frame's camera frame is 2464 pixels square and very
wide; resized whole to the 960-pixel processing size, a hand-held object can
be too small for the detector.

- `--region X0,Y0,X1,Y1`: the detector looks at this rectangle of the frame
  only. The four numbers are fractions of the frame with the origin at the
  top left, `0 <= x0 < x1 <= 1` and `0 <= y0 < y1 <= 1`, each side at least
  0.05; `0.25,0.25,0.75,0.75` is the centre half. The rectangle is sliced out
  of the native frame before anything is resized or converted, then resized
  to at most 960 pixels (never enlarged).
- `--rotation 0|90|180|270`: what the detector sees is turned by this many
  degrees clockwise.

Boxes are mapped back, so every box in the report is in one space that
covers the whole frame: `whole_frame_processing_pixels`, the frame scaled so
that its longest side is at most 960. That is the same space the boxes have
without a region, where the detector's input is the whole frame at that size
(`detector_input_pixels`); the cell, side and coverage arithmetic above is
therefore unchanged in meaning. A mapped box is clamped to the frame and is
at least one pixel wide and tall. With a region each frame record carries
`box_coordinate_space: whole_frame_processing_pixels`, `box_space_size`,
`detector_input_size` (what the detector really saw) and `view` (the region,
the rotation, `crop_native_xyxy` and `resized_size`), and the report carries
`view` (`mode: region`, the region, the rotation, the detector requested and
the orientation statement).

A malformed region or rotation is refused on the command line, before the
confirmation is asked and so before the camera is touched. Without the two
options, or with the whole frame unturned spelled out, nothing is cropped
and the report has no `view` block: it is what it was.

Neither value is a finding. Nothing here knows where "straight ahead" falls
in the image or which way up it is, and no calibration data is read. The
[guided window's scan](camera-guide.md#the-scan-where-in-the-image-and-which-way-up)
is the one bounded run that tries the candidates. The shared code is
`sightcircuit/camera/region.py`.

[`camera_hud`](camera-hud.md) does not take these options yet.

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
| `schema_version` | 2 since item L15 (the times, marks, target and summaries; no earlier field changed) |
| `timebase`, `run_started`, `first_frame`, `helper_end`, `export_returned` | The declared clocks with the anchor, and `{monotonic_ns, utc_ns}` of the run's start, the first frame's acquire, the helper's end record as read (with its `reason`; `null` without one) and the export's return; see [times](#times-marks-and-the-target-summary) |
| `marks` | The vocabulary, the cues and key labels requested, `cues_not_reached`, `key_marks_enabled`, `key_presses`, `key_marks_capacity`, `marks_dropped` and `recorded`: per mark `index`, `source` (`cue`, `key`, or `window` for a mark the [guided window](camera-guide.md) records at a click; `window_marks` counts those, and this command makes none), `label`, `monotonic_ns`, `utc_ns`, `offset_ms` from the first frame's acquire (`null` before it), and for a cue the scheduled offset and times and `late_ms` |
| `target_label`, `target`, `summary` | The class named with `--target-label` (`null` without); its threshold, frame count, cell thresholds and the orientation statement; the counts-only [summaries](#times-marks-and-the-target-summary) |
| `detections` | One record per processed frame: `sequence`, `frame_set_sequence` (the acquired set; both cameras of a set share it), `source_id` (`arcturus_camera_left` or `_right`), `camera_index`, `frame_id`, `observed_ns` (the helper's host monotonic clock at acquisition), `observed_utc_ns` (derived), `helper_realtime_ns`, with a target `target` (`present`, `count`, `confidence`, `box_xyxy`, `column`, `row`, `cell`, `centre_fraction`), `input_age_ms`, `processing_ms`, `inference_ms`, `labels`, up to 20 `detections` (`label`, `confidence`, `box_xyxy` in `detector_input_pixels`), `detection_count`, `native_size`, `detector_input_size`; Camera Data |
| `view` | Only with `--region` or `--rotation` (or the guided window's scan): `mode` (`region` or `scan`), `box_coordinate_space` (`whole_frame_processing_pixels`), the `region` and `rotation`, the `detector` requested and the orientation statement; each frame record then has `box_space_size` and its own `view`. See [a region of view](#a-region-of-view) |
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
text or calibration values. `tests/test_camera_region.py` covers the region
of view (item L14): the arguments, the inverse mapping for every rotation on
frames the test draws, a region at the frame's edge, the scan schedule and
its tallies; the recognition tests add the runs through a region and a scan,
the refusal of a bad region before the confirmation, and the unchanged
default. Nothing in CI builds against or links the SDK, and
nothing here ran on the Frame.
