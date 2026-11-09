# Recognize objects on frames the camera helper exports

`camera_recognize` is the fourteenth command: bounded CPU recognition, without
display, on frames that `sightcircuit-camera-helper --export-frames` writes on
its stdout. It is the Python half of the proof of concept that lane S builds
toward ([current work](../live-capture.md#current-work-order)); the helper's
export mode is the native half. This guide describes the command as it exists
and is checked in the cloud: against a fake producer, never a camera. Nothing
here is a device result, and no device run is approved by this page.

[Scope](../poc-scope.md) · [Gates](../../sightcircuit/gates.py) ·
[Input review](../history/2026-11-09-arcturus-sdk-input-review.md) ·
[Mirror recognition, the pattern it follows](mirror-recognition.md)

## Gate statement

Every gate stays closed and none is lifted by this command. The report of
every run carries `gates.claims()`: `production_live_input_enabled`,
`mapping_qualified` and `production_hud_enabled` are `false`, and a
`gate_statement` field says the following. On 2026-11-09 the owner classified
bounded camera recognition without display (counts, labels, boxes and timings
only, no saved image, at most 30 seconds) as a confirmed use outside the
live-input gate, exactly as the bounded SteamVR mirror recognizer of
[E48](../evidence/e048.md) is; `sightcircuit/gates.py` is unchanged and nothing
feeds the live `Pipeline` or the HUD path. The command never builds the live
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

Ctrl+C or SIGTERM cancels: the primitive sends the helper SIGTERM, which the
helper collects between SDK calls and answers with an `end` packet of reason
`stop_signal`; the command exits 128 + signal.

## Run it

Both bounds are required and both are checked by the helper and the command:
`--seconds` is at most 30, `--max-frames` at most 300, `--fps` at most 10
(default 5). The helper's own bounds are wider (60 seconds, 600 frames,
work-order item S6); the command never asks for more than its own. `--allow-camera-input` is the opt-in, named like the observer's
flags; without it nothing is launched.

```bash
.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper /path/outside/the/checkout/sightcircuit-camera-helper \
  --seconds 10 --max-frames 50 --fps 5 --cameras 0,1 \
  --report artifacts/camera/run-01.json
```

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
| `detections` | One record per processed frame: `sequence`, `frame_set_sequence` (the acquired set; both cameras of a set share it), `source_id` (`arcturus_camera_left` or `_right`), `camera_index`, `frame_id`, `observed_ns` (the helper's host monotonic clock at acquisition), `input_age_ms`, `processing_ms`, `inference_ms`, `labels`, up to 20 `detections` (`label`, `confidence`, `box_xyxy` in `detector_input_pixels`), `detection_count`, `native_size`, `detector_input_size`; Camera Data |
| `last_result` | The last record of `detections` |
| `inference_ms`, `processing_ms`, `input_age_ms` | Count, sample size, nearest-rank p50 and p95 and the maximum, as the pipeline statistics report them |
| `inference_p95_budget_ms`, `inference_p95_within_budget` | `LIVE_INFERENCE_P95_BUDGET_MS` (200) and whether this run's p95 met it; which machine ran it is the report's business, so a cloud figure is never a Frame figure |
| `capture` | The source report: `helper` (resolved path, SHA-256, arguments), `supervision` (the primitive's block: pid, exit code, forced termination, tree reaped), `stream` (packets and frames read, frames per camera, dropped frames, the `ready` and `end` headers, the refusal if any), `diagnostics` (the collected stderr lines and the counts of dropped, non-JSON and oversize lines), `helper_exit_code` |
| `camera_data` | The Camera Data statement, `report_saved_by_operator` and `report_path` |
| `gate_statement`, `production_live_input_enabled`, `mapping_qualified`, `production_hud_enabled` | The owner's 2026-11-09 classification in one sentence, and `gates.claims()`, all `false` |
| `cleanup_ok`, `images_saved`, `pixels_in_report`, `display_started`, `overlay_requested` | The primitive's cleanup verdict and the processing thread's exit; the last four are always `false` |

## The wire protocol, camera export v1

The helper's stdout is a sequence of packets and nothing else. A packet is a
16-byte little-endian prefix (the magic `SCCF`, the version 1, the header size,
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
and the helper's run limits (`fps` 1 to 10, `max_seconds` 1 to 60,
`max_frames` 1 to 600, the bounds of work-order item S6). `python3 scripts/camera_contract_header.py --write` generates
`tools/camera_export_protocol.hpp` for the helper and `--check` fails when it
is stale (CI runs it; `tests/test_camera_contract.py` pins it). The reader
enforces every rule and names its refusals (`export_reader.REASONS`).

## Cloud checks

`tests/test_camera_contract.py`, `tests/test_camera_export_reader.py` and
`tests/test_camera_helper_source.py` run in the dependency-free CI job (the
stand-in helper, `tests/fake_camera_export.py`, is installed outside the
checkout and launched exactly as the real helper would be);
`tests/test_camera_recognize.py` adds the end-to-end runs with a stand-in
detector in the replay job. Nothing in CI builds against or links the SDK, and
nothing here ran on the Frame.
