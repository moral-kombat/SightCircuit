# Camera HUD stage, client and report

Work-order item L8 of the
[camera display work order](../live-capture.md#camera-display-work-order-adopted-2026-11-16):
the joint between the left-camera frame source
([L5](camera-helper.md#the-pipeline-source-l5)), the mask-free row selector
([L6](hud-runtime.md#row-selection)) and the bridge's observed-frame path
([L7](overlay.md#fixed-hud-mode-work-order-item-l7-2026-11-16)).

**Status: library code, cloud-checked only.** No command constructs the stage
(that is L9), it opens no gate, and real output is refused. Everything below
is checked against the camera stand-in, the validation bridge and, for the
real-output path, a fake OpenVR runtime that logs the bridge's calls. None of
it is a device result, and no time here is a Frame measurement.

## The observed-frame client

`ObservedFrameClient` in `sightcircuit/observed_frames.py` is the only sender
of the observed frame (opcode `6`). It reuses `OverlayClient`'s transport (the
bounded pipe writes, the reader thread, the cooperative stop and its
escalation) and replaces what goes over it and which mode is started. It is a
module of its own so that `sightcircuit/overlay.py` stays as L7 pinned it:
`OverlayClient` starts the validation and replay streams and nothing else,
never names `--fixed-hud` and never sends opcode `6`.

| It sends | When |
| --- | --- |
| Observed frame | `submit_observed(frame_id, observed_ns, expires_ns, width, height, rgba)`: the caller's three numbers, unchanged. The identity must rise; the deadline may not exceed the observation plus one second (`observed_frame_packet` refuses it, as the bridge would). One frame waits at most: a newer one replaces it, and a frame whose deadline has passed when the writer reaches it is not sent |
| Hide request (opcode `5`, visible 0) | `take_down()`: the tile comes down now, and a frame still waiting is dropped |
| Show request (opcode `5`, visible 1) | Right after the first frame that follows a take-down, because the bridge keeps a hidden tile hidden until a show request |
| Keepalive | After `keepalive_seconds` (2 s by default, at most 5 s) without a packet: the last visibility request, repeated |
| Hide, then stop (opcode `3`) | `close()`; the hide is skipped when the tile is already down |

It never sends a settings packet, a frame without a deadline or the
validation layout's expiring frame: `set_settings`, `freeze_settings`,
`submit_frame`, `submit_bgr` and `request_visibility` raise
`observed_frame_client_refuses_<what>`, and it never retransmits a frame.

**The keepalive repeats the request in force.** The
[overlay reference](overlay.md#expiry) describes the keepalive as a show
request, which with nothing presented is answered `no_image` and displays
nothing. That is what this client sends until the first take-down. While the
tile is taken down the keepalive is a hide instead: the bridge keeps a hidden
tile's pixels until its deadline, so a show inside that second would bring
back the tile that was just taken down. Either way the request repeats the
state the bridge is already in.

**It reads** the ready line (which must announce observed frames, and
`fixed_hud` exactly when real output was started), the first-frame line
(`frame_validated` or `frame_submitted`, kept with the time it was read),
every `frame_expired` line with its fields (the first 1,024; later ones are
counted), the visibility lines and the final report. `close()` is safe from
any thread, returns the same report every time, and confirms cleanup only for
a clean exit in the mode it started: a validation bridge that never
initialised a runtime, or a fixed-HUD bridge whose hide, texture clear and
destroy all returned.

**Real output is refused.** With `validation_only=False` the client would
start `--fixed-hud`. `check_start()`, which `start()` calls before anything
is launched, passes the answer of `fixed_hud_output_permitted(client)` to the
`LIVE_HUD_OUTPUT` gate's `require`, and that function returns `False`
unconditionally. It is the one place that decides: work-order item L10 adds
its one-run scope record check there. No argument, environment variable or
bridge line changes the answer, and `sightcircuit/gates.py` is unchanged.

## The stage

`CameraHudStage` in `sightcircuit/hud/camera_stage.py` takes a
`CameraHelperSource`, a detector and an `ObservedFrameClient`, builds a
latest-only `Pipeline` with a one-second input age limit, and is that
pipeline's only sink. For each frame the pipeline processes:

1. the detector runs on the converted frame;
2. `camera_rows` selects the rows (`directions` and `orientation` are the
   stage's parameters; their defaults are not findings);
3. `draw_tile` draws the 320 × 128 tile from the rows alone;
4. one observed frame goes to the client with `observed_ns` the camera
   frame's own (never the send time), the camera frame's sequence as its
   identity, and `expires_ns = observed_ns + MAX_DISPLAY_AGE_NS`, or the run's
   own deadline (`seconds`) when that comes first.

| Rule | How the stage keeps it |
| --- | --- |
| A result whose observation is one maximum display age old is never sent | The pipeline skips it before any sink; `submit` checks again and counts `results_too_old`; the client's writer checks a third time before the write |
| Every newer result with rows is sent | Nothing is held back as unchanged. The bridge renews a deadline only for a newer observation, so the same rows from a newer frame are what keeps a label up. (The work-order row's "submit on change, refresh within 500 ms" predates L7's rule and would let a steady label lapse) |
| A result with no rows takes the tile down within one processing period | `take_down()` when the result arrives; no frame is sent for it. The next result with rows is sent and followed by the show request |
| With nothing to show, the bridge stays connected | The client's keepalive, on its own writer thread, whatever the detector is doing |
| A slow or stopped detector lets the tile expire by itself | Nothing is sent, and the bridge clears the tile at its deadline and writes `frame_expired` |
| Any end of the run takes the tile down and closes the bridge | A watcher thread calls `take_down()` the moment `stop` is set, without waiting for a detector call in flight; the pipeline then clears and closes its sink, and `run` closes the client again in case the sinks were never opened |
| A presence end clears the tile at once | The source sets `stop` when it accepts the `headset_removed` or `standby` end packet, while the helper may still be alive; the watcher's hide follows within this process's scheduling |

`run(stop=None)` must be called on the main thread, because the source's
supervision primitive takes the process's signals. It calls
`client.check_start()` first, so a refused start launches neither the helper
nor the bridge. An error during the run is not raised: the report carries it
as `status` `error` with a `reason` code and the exception's type, the
exception itself stays on `stage.failure`, and the cleanup verdicts are
reported either way. A stage runs once.

| `status` | `reason` | When |
| --- | --- | --- |
| `completed` | `max_frames`, `seconds`, or the helper's end reason | The pipeline's or the helper's own bound |
| `stopped` | `stop_requested` | The caller set `stop` |
| `presence_ended` | `headset_removed` or `standby` | The helper's end packet said so; never retried |
| `cancelled` | `signal` | The supervision primitive took a signal |
| `error` | the source's reason, the exception's text when it is a bare code, else `overlay_bridge_failed` or `camera_hud_run_failed` | A helper error, a detector or sink exception, a bridge that failed |

A caller treats `cleanup.ok` false as a failed run whatever the status, as
the source's report already requires.

**The stage and the gates.** With a validation client the stage displays
nothing; with the real helper that is bounded recognition without display,
the class of use `camera_recognize` already is. Showing camera-derived labels
needs the client's real-output start, which is refused. `run_pipeline` still
refuses the camera source.

## The report

`report()` returns kind `camera_hud_stage`, schema version 1.

| Block | Holds |
| --- | --- |
| top level | `status`, `reason`, `error_type`, the three gate claims (all false), `validation_only`, `bridge_mode`, `images_saved`, `pixels_in_report` and `physical_display_observed` (all false), `fields_withheld` |
| `config` | the HUD configuration id and digest, the tile size, rows, threshold, `directions`, `orientation`, the bounds, the maximum display age and the keepalive interval |
| `identities` | the identity rule (`camera_frame_sequence`), the first and last frame identity written, the last result index and the source id |
| `results` | results seen, with rows, without rows, too old, and after the end |
| `frames` | `submitted` (handed to the client), `sent` (written to the pipe), `not_sent` (replaced, withdrawn by a take-down or past its deadline before the write), `written_past_deadline`, and the bridge's counts: `bridge_received`, `bridge_presented`, `retransmitted`, `refused` (stale or expired on arrival), `expired`, `expired_in_upload` |
| `deadlines` | the cap, the longest deadline after its observation, `within_cap`, and when the run's end was seen |
| `timings` | below |
| `frame_expired` | the count, and every recorded event with the bridge's fields plus `noticed_after_deadline_ms` and, in real output, `hide_returned_after_deadline_ms`, with the run's maxima |
| `labels` | per label: `display_seconds`, `longest_gap_seconds`, `spells`, the first shown and last ended times |
| `pipeline` | frame counts, stale skips, the stage timings and the sink cleanup flags |
| `source` | the source's report: status, counts, the helper's digest, the supervision block, the `ready` and `end` records as the source carries them, and `exposure_minus_acquire_ms` as a timing |
| `bridge` | the client's counts, the line counts, the first-frame line, the bridge's final report and `cleanup_confirmed` |
| `cleanup` | `source_ok`, `bridge_ok`, `sinks_ok`, `ok` |

### Timings and their timebases

Each timing has `count`, `samples`, nearest-rank `p50` and `p95`, `max`, and
declares its `unit` (`ms`), `timebase`, `from` and `to`.

| Timing | From | To | Timebase |
| --- | --- | --- | --- |
| `input_age_ms` | the camera frame's `observed_ns` | processing started | `host_clock_monotonic` |
| `inference_ms` | the detector's own reported duration | | `detector_reported_duration` |
| `processing_ms` | processing started | tile drawn | `host_monotonic_duration` |
| `result_age_ms` | `observed_ns` | the result at the pipeline's output boundary | `host_clock_monotonic` |
| `output_age_ms` | `observed_ns` | the frame's packet fully written to the bridge's pipe | `host_clock_monotonic` |
| `capture_to_display_age_ms` | `observed_ns` | the bridge's first-frame line read | `host_clock_monotonic` |

`host_clock_monotonic` is `time.monotonic_ns()`, the clock of
`Frame.observed_ns` and of every time the bridge reports.

**The bridge acknowledges one frame.** It writes a line for the first frame
it presents and none for the others, so `output_age_ms` cannot end at an
acknowledgement. It ends at the last instant this side knows of a frame, the
completed pipe write, and says so (`bridge_acknowledged` false, `why`
`bridge_reports_no_per_frame_acknowledgement`). The upload and the show come
after that instant, so it is a lower bound on when a tile could be up.
`capture_to_display_age_ms` has at most one sample, the first frame, measured
to the moment its line was read; `frames_without_acknowledgement` counts the
rest, for which `output_age_ms` is the lower bound. Neither is what a headset
displayed. A line per presented frame from the bridge, carrying the frame
identity and the time the present returned, would let both timings cover
every frame; that is a bridge change and is not made here.

**Label display times are this side's estimate.** A tile counts from the
write of its packet (or of the show request that lets a hidden tile up) until
the earliest of its deadline, the next tile's write, a hide request's write
and the end of the run. `longest_gap_seconds` is the longest time between two
such spells of one label.

### What the report cannot hold

`check_report` is the schema, and `report()` runs it on every report. It
accepts null, booleans, integers and finite floats; strings shaped like an
identifier (letters, digits and `_.:/+@-`, at most 128 characters, no white
space); a sanitised row label under the key `label`; objects with identifier
keys; lists of at most 16 scalars or at most 1,024 objects; at most eight
levels. Bytes, buffers, arrays, tuples, long or spaced strings and long
numeric lists are refused wherever they sit, so neither a tile (163,840
bytes) nor a camera frame fits in a field, and no field carries prose.

The embedded source and bridge reports go through `report_value` first: keys
that hold prose, paths or argument vectors (`error`, `detail`, `path`,
`arguments`, `definition`, `scope`) are left out by name, and any other value
the schema would refuse becomes null and is counted in `fields_withheld`. An
exception's message is therefore never in the report; `status`, `reason` and
`error_type` are. The report holds no calibration value: the placement
numbers in the bridge's `fixed_hud` block are the HUD contract's constants.

## Cloud checks

`tests/test_observed_frame_client.py` (standard library, validation bridge):
the packets and their order, the take-down and the show, the keepalive past
the bridge's ten-second timeout with nothing shown, the frame that is
replaced or outlives its deadline before the write, each `frame_expired`
field, the refusals, and the gate.

`tests/test_camera_hud_stage.py` runs the stage on the camera stand-in. On
the validation bridge: every frame's observation and deadline, empty rows, a
slow detector, a presence end (`headset_removed` and `standby`), a stop, the
pipeline's own bounds, a detector error, helper errors, a bridge that dies,
names-only rows and each orientation, the gate refusal with nothing launched,
and the report schema. On `--fixed-hud` against the fake runtime
(`tests/fixtures/fake_vrclient.cpp`), with the permission function patched
for the test: the runtime's hide and show calls at the take-down and the
show, the bridge's own hide at a deadline, the hide at a presence end, the
retirement at every end, the placement applied exactly once, and, from the
fake's call log, that the overlay was never visible past the deadline of the
tile uploaded last (within one bridge poll and this host's scheduling margin
of 150 ms, which is not a claim about the bridge).

The validation ledger of the mirror route (`ValidationLedger`) is not reused
for the deadline check: it requires a mapping candidate and a capture
context, which the camera route does not have.
