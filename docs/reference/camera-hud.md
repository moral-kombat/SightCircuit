# Camera HUD stage, client and report

Work-order item L8 of the
[camera display work order](../live-capture.md#camera-display-work-order-adopted-2026-11-16):
the joint between the left-camera frame source
([L5](camera-helper.md#the-pipeline-source-l5)), the mask-free row selector
([L6](hud-runtime.md#row-selection)) and the bridge's observed-frame path
([L7](overlay.md#fixed-hud-mode-work-order-item-l7-2026-11-16)).

**Status: cloud-checked only.** The [`camera_hud` command](#the-command-l9)
(L9) constructs the stage; it opens no gate, and real output is refused unless a
[one-run scope record](#the-one-run-scope-record) verifies (L10). Everything below
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
| Final hide | `end()`: as `take_down()`, and the client is latched: no frame is accepted and no show is written afterwards |
| Hide, then stop (opcode `3`) | `close()`, which calls `end()` first; the hide is skipped when the tile is already down |

It never sends a settings packet, a frame without a deadline or the
validation layout's expiring frame: `set_settings`, `freeze_settings`,
`submit_frame`, `submit_bgr` and `request_visibility` raise
`observed_frame_client_refuses_<what>`, and it never retransmits a frame.

**A named layout (item B3).** `ObservedFrameClient(layout=...)` takes one of
the bridge's [fixed layouts](overlay.md#fixed-layouts-work-order-item-b3-2026-11-24)
(`hud.contract.FIXED_LAYOUTS`, the contract's own objects and nothing else):
the bridge is launched with `--layout`, must repeat the name on its `ready`
line, and only frames of exactly that layout's size are queued. Without the
argument the client is the strip's, as described here, with the command line
it has always had. The stage and the command on this page pass no layout.
Real output with a layout is admitted only under a
[scope record](#the-one-run-scope-record) that names that layout
([who may show it](box-panel.md#who-may-show-it)).

**The keepalive repeats the request in force.** The
[overlay reference](overlay.md#expiry) describes the keepalive as a show
request, which with nothing presented is answered `no_image` and displays
nothing. That is what this client sends until the first take-down. While the
tile is taken down the keepalive is a hide instead: the bridge keeps a hidden
tile's pixels until its deadline, so a show inside that second would bring
back the tile that was just taken down. Either way the request repeats the
state the bridge is already in.

**The end is latched under the queue's lock.** `end()` sets the latch, drops
a waiting frame and asks for the hide in one step under the lock
`submit_observed` takes, so a submit racing the end is either dropped by the
end or refused after it. It cannot reset the take-down, and the writer sends
no show once the latch is set.

**The end-of-run hide is in-band.** It is a packet on the same pipe as the
frames. A frame the writer is already writing when the end comes is finished
first, and the bridge, one thread, uploads and may show that frame before it
reads the hide. The hide therefore trails the end by up to one frame's write
plus the uploads the bridge still has in hand; the bridge's own deadline
remains the bound on the tile. With no upload delay the fake runtime's hide
call started 0.4 ms after the end. With every upload stalled 600 ms it was
written 247 to 584 ms after the end and the bridge's hide returned 847 to
1,184 ms after it (two runs on this host, not a Frame measurement). The
report's `end` block carries both times so a device run measures them.

**It reads** the ready line (which must announce observed frames, and
`fixed_hud` exactly when real output was started), the first-frame line
(`frame_validated` or `frame_submitted`, kept with the time it was read),
every `frame_expired` line with its fields (the first 1,024; later ones are
counted), the visibility lines, the final report, and the bridge's answer to
each frame: `frame_presented` or `frame_refused`
([overlay reference](overlay.md#the-observed-frame-opcode-6)), kept by frame
identity (the first 4,096 of each) and handed to the listener in the
bridge's order. A malformed answer fails the client. Any exception on the
writer thread, a listener's included, fails the client too. `close()` is safe from
any thread, returns the same report every time, and confirms cleanup only for
a clean exit in the mode it started: a validation bridge that never
initialised a runtime, or a fixed-HUD bridge whose hide, texture clear and
destroy all returned.

**Real output is refused** unless a one-run scope record verifies. With
`validation_only=False` the client would start `--fixed-hud`. `check_start()`
and `start()` each call `require_real_output(client, layout)` before anything
is launched (`start()` directly, on the layout value it then launches with,
not through `check_start()`), and that passes the answer of
`fixed_hud_output_permitted(client, layout)` to the `LIVE_HUD_OUTPUT` gate's
one `require`. That function is the one place that decides. It answers `True`
only for a client whose `scope` is a `RunScope` that permits real output
([below](#the-one-run-scope-record)); a client without one, with any other
object in its place, or with a scope whose record does not verify is refused
as before. Nothing keeps the answer in the client: it asks every time, the
scope knows which client it granted, and at `start()` it checks the deadline
and the bridge's digest again. A real-output client starts once, its mode
cannot be changed after construction, and it ends real output itself at its
own bound (`max_output_seconds`). No argument, environment variable or bridge
line changes the answer, and the gate stays closed.

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
| Any end of the run takes the tile down and closes the bridge | A watcher thread calls the client's `end()` the moment `stop` is set, without waiting for a detector call in flight: nothing is accepted afterwards and the hide is written, behind at most the frame already being written (the in-band limit above). The pipeline then clears and closes its sink, and `run` closes the client again in case the sinks were never opened |
| A presence end clears the tile at once | The source sets `stop` when it accepts the `headset_removed` or `standby` end packet, while the helper may still be alive; the watcher's hide follows within this process's scheduling, subject to the same in-band limit |
| A bridge that ends, or a client that fails, ends the run at once | The same watcher looks at the client every 20 ms and sets `stop` itself, so the camera helper is stopped whatever the detector is doing. A result that reaches `submit` inside those 20 ms meets the client's error on the pipeline's thread first and raises it, which ends the run the same way. `end.bridge_ended_first` records either, as long as the run had not already ended |
| A frame the client refuses is never silently lost | `submit` counts it as after the end when the run has ended, and raises otherwise (`observed_frame_client_not_running`) |

### Two layouts and a region of view (items B5 and L17)

The steps above are the strip's. The stage draws for one of the bridge's
[fixed layouts](overlay.md#fixed-layouts-work-order-item-b3-2026-11-24),
named by `config` (one of the contract's own `FIXED_LAYOUTS` objects; the
strip unless the box panel is named), and it must be the layout its client
names: a stage that would draw one layout through a client that sends
another is refused when it is built (`camera_hud_layout_mismatch`).

| | Strip (`fixed-edge-hud-v1`) | Box panel (`head-fixed-box-panel-v1`) |
| --- | --- | --- |
| Step 2 | `camera_rows` | [`camera_boxes`](box-panel.md#camera_boxes) with the run's `alignment` |
| Step 3 | `draw_tile`, 320 × 128 BGR, converted to RGBA when sent | [`draw_boxes`](box-panel.md#the-renderer), 512 × 512 RGBA, sent as drawn, row 0 first |
| One update | 163,840 bytes | 1,048,576 bytes |
| `alignment` | refused: rows take none | required, an `Alignment`; the neutral one is a value like any other. The project's own five numbers, never the SDK's calibration |
| `directions`, `orientation` | the stage's parameters | not used |
| A result with nothing to show | no rows | no boxes: no detection, or every detection dropped by a rule of `camera_boxes` |

Everything in the table of rules above is the same code for both: the
deadline from the camera frame's observation, sending every newer result,
the acknowledgements and the ledger (fed from the rows' labels or from the
selection's), the watcher, the end, the latch and the bridge-death path.
`tests/test_camera_hud_stage_panel.py` runs the strip's stage suite with
the panel: 26 of the 30 tests the strip had are run unedited, three through
the harness's layout parameters (the frame size the client takes and the
payload expected for a result), and one, the strip's names-only rows and
orientations, does not apply to the panel and is left out there by name.

**What the sites compare is read-only.** `config`, `region`, `alignment`
and `presence_watch` are properties without setters, and whether a frame is
prepared (`prepares`) is derived from the layout and the region on every
frame, so the sites cannot approve one view and the run use another.
`presence_watch` is whether the stage holds a watch, not a flag beside it;
the stage holds none.

**A detection is judged in the detector's input, before its box is mapped.**
Taking a box back to the whole frame sorts, clamps and rounds it
(`Mapping.to_frame`), so `valid_detections` first drops every detection
whose box is not a list or tuple of four coordinates the layout's selector
takes with `0 <= x0 < x1 <= width` and `0 <= y0 < y1 <= height` of the image
the detector was given: a box outside the input, inverted, empty, with a
NaN or an infinity, of three numbers, `None` or text. They are counted
(`view.invalid_detections`, and on the panel in `boxes.dropped.invalid`) and
never raise, so a region or a rotation does not change which detections
count as valid, and a malformed box cannot end a run and waste its record.

**A rotation and the strip's arrows.** With a rotation the panel shows the
region as the detector saw it. The strip's arrows do not follow the
rotation: boxes are taken back to the unturned whole frame and the arrow is
worked out there, so it refers to the camera's delivered image unless
`orientation` is given as well. `ROTATION_ORIENTATION` is the pairing that
makes the arrows refer to the image as the detector saw it (90 with
`rotate_90_cw`, 270 with `rotate_90_ccw`, 180 with `rotate_180`); the stage
does not apply it, because both are the operator's arguments.

**The rule for nothing to show is one rule, kept.** A result with no rows, or
with no boxes, takes the tile down at once with a hide request and nothing is
sent for it; the next result with something to show is sent and followed by
the show request. An all-transparent panel is therefore never uploaded: an
empty view costs one small packet, not a megabyte, and the panel is down by
name and not merely see-through.

**The region of view** (`region`, a `camera.region.Region`; item L17). With a
region the detector is given that rectangle of the native frame, resized and
turned by its quarter turn (`region.prepare`, as in
[bounded recognition](../guides/camera-recognize.md#a-region-of-view)), and
its boxes are taken back to the whole frame (`Mapping.result`) before rows or
boxes are selected: `camera_rows` gets the mapped detections and the box
space's shape, so an arrow says where the object is in the whole frame, and
`camera_boxes` gets them with the frame's `Mapping`, so the panel shows the
region as the detector saw it. The region and the rotation are arguments,
not findings: nothing here knows which way up the camera image is (E103
found the left camera's image a quarter turn from upright and left open
which one). The strip with the whole frame, unturned, converts the frame as
it always did; any other region, and every panel run (its boxes need the
`Mapping`), is prepared by the stage itself.

**Per-frame time on the fake path: cloud figures, not Frame figures.** One
x86_64 cloud container (4 virtual cores, Python 3.12), the camera stand-in
producing 2464 × 2464 frames at 5 a second, a scripted detector that returns
five boxes and takes no time, the validation bridge; 50 frames a row,
milliseconds as p50 / p95. `prepare` is the pipeline's conversion step (the
crop, the resize to the processing size and the turn); the next five are the
stage's own steps (`steps_ms` in the report).

| Layout, view | `prepare` | `map` | `select` | `draw` | `pack` | `queue` | all processing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| strip, whole frame | 10.6 / 13.3 | 0 | 0.06 / 0.10 | 0.16 / 0.26 | 0.10 / 0.17 | 0.08 / 0.22 | 10.9 / 13.7 |
| strip, centre half turned 90 | 6.6 / 8.0 | 0.04 / 0.10 | 0.05 / 0.09 | 0.17 / 0.26 | 0.09 / 0.15 | 0.08 / 0.10 | 7.0 / 8.3 |
| panel, whole frame | 10.1 / 11.8 | 0.04 / 0.06 | 0.69 / 1.13 | 0.99 / 1.46 | 0.17 / 0.27 | 0.30 / 0.49 | 12.2 / 14.2 |
| panel, centre half turned 90 | 6.1 / 7.8 | 0.03 / 0.06 | 0.68 / 1.19 | 0.90 / 1.47 | 0.16 / 0.23 | 0.28 / 0.40 | 8.1 / 10.4 |

On this host the panel costs about 2 ms a frame more than the strip (exact
rational mapping of five boxes, the megabyte drawn, one copy to bytes, one
copy into the packet), and a region costs less than the whole frame because
less is resized. The detector, not the stage, is the larger part of a real
frame's time. The stage adds no thread, no timer and no polling for the
panel: the steps run once per result on the pipeline's thread. What a
megabyte costs the Frame's CPU and its compositor at 5 a second is unknown
([the megabyte, measured in the cloud](box-panel.md#the-megabyte-measured-in-the-cloud)).

`run(stop=None)` must be called on the main thread, because the source's
supervision primitive takes the process's signals. It calls
`client.check_start()` first, so a refused start launches neither the helper
nor the bridge. An error during the run is not raised: the report carries it
as `status` `error` with a `reason` code and the exception's type, the
exception itself stays on `stage.failure`, and the cleanup verdicts are
reported either way. A stage runs once. `threshold`, `max_fps`, `max_frames`
and `seconds` are checked and turned into plain `float` and `int` when the
stage is built, so a NumPy scalar cannot fail the report at the end of a run.

| `status` | `reason` | When |
| --- | --- | --- |
| `completed` | `max_frames`, `seconds`, or the helper's end reason | The pipeline's or the helper's own bound |
| `stopped` | `stop_requested` | The caller set `stop` |
| `presence_ended` | `headset_removed` or `standby` | The helper's end packet said so; never retried |
| `cancelled` | `signal` | The supervision primitive took a signal |
| `error` | the client's error when it is a bare code, else `overlay_bridge_failed` (the bridge left without a final report or was forced) or `overlay_client_failed`; otherwise the source's reason, the exception's text when it is a bare code, else `camera_hud_run_failed` | A bridge that failed, a failure on the client's threads, a helper error, a detector or sink exception |

A caller treats `cleanup.ok` false as a failed run whatever the status, as
the source's report already requires.

**The stage and the gates.** With a validation client the stage displays
nothing; with the real helper that is bounded recognition without display,
the class of use `camera_recognize` already is. Showing camera-derived labels
needs the client's real-output start, which is refused. `run_pipeline` still
refuses the camera source.

## The one-run scope record

Work-order item L10: the diagnostic tier of
[decision D3](../poc-scope.md#architecture-decisions-2026-11-07), without the
mask, as the owner extended it on 2026-11-16 (decision 15) to one record per
displayed step. Library: `sightcircuit/runtime/scope.py`. It opens no gate:
`gates.claims()` reports every claim false before, during and after a run
under a record.

**What it is, and is not.** A record is a small JSON file that ties one run
to one approval. It is a procedural control against mistakes, not a
cryptographic one. Nothing is signed. Anyone who can run the writer on the
Frame can write a record, and anyone who can edit files there can forge one
or delete the ledger. The owner's approval, given in the Frame manager's
session, is the authority. The record makes the ordinary mistakes fail
closed: the wrong commit, a rebuilt binary, a second run, a run after the
sitting, the wrong source, a longer run, another program, and since schema
version 2 (work-order item B5, 2026-11-25) another layout, another region or
rotation of the camera image, another alignment, and a run without the
presence watch where the record requires it.

**Schema version 2.** Version 1 (item L10) bound `fixed-edge-hud-v1` and its
digest as constants and compared them with nothing, because there was one
geometry. Version 2 changes two things and nothing else: `hud_config_id` and
`hud_config_sha256` may name either of the bridge's
[fixed layouts](box-panel.md#who-may-show-it) and are compared with what the
run is about to show, and four fields are new: `region`, `rotation`,
`alignment` and `presence_watch`. A record that states any other version is
refused as `scope_record_schema_unsupported`, whatever else it holds; it is
never read with the old meaning. The ledger, the run id, the deadline, the
steps and their bounds, the runner and the writer's place outside the
operator commands are unchanged.

| Field | Bound to |
| --- | --- |
| `step` | `L-D3`, `L-D4` or `L-D5` |
| `source` | `synthetic` for L-D3 (the camera is never opened), `arcturus_camera_left` for L-D4 and L-D5; the step fixes it |
| `runner` | `camera_hud`, the only runner a record may name. The caller declares its own name, so this field keeps a record written for another runner from being accepted by a caller that names itself truthfully; it cannot identify the calling program |
| `hud_config_id`, `hud_config_sha256` | the layout the run may show: the id of one of the contract's fixed layouts (`fixed-edge-hud-v1`, `head-fixed-box-panel-v1`) and the digest of that layout's fields as this checkout computes it. The run's own layout must be the same, by id and by digest |
| `region`, `rotation` | the view the detector is given: four fractions of the native frame `[x0, y0, x1, y1]` and the clockwise quarter turn (0, 90, 180, 270), by the rules of the [region of view](../guides/camera-recognize.md#a-region-of-view). A camera step's record always states both (the whole frame, unturned, is `[0.0, 0.0, 1.0, 1.0]` and `0`); the synthetic step has no frame and states `null` for both |
| `alignment` | the wearer's [alignment](box-panel.md#the-alignment), its five numbers (`scale_x`, `scale_y`, `offset_x`, `offset_y`, `turn`), for a camera step on a layout that maps boxes to a panel; the neutral alignment is stated like any other. `null` for the strip, which takes none, and for the synthetic step. The numbers are the project's own format metadata, never the SDK's calibration |
| `presence_watch` | `true` when the run must hold the [presence watch](../guides/camera-recognize.md#the-presence-stop---presence-watcher), else `false`; always `false` for the synthetic step, which opens no camera |
| `revision` | the checkout's full commit |
| `manifest_sha256` | the SHA-256 of the [checkpoint manifest](hud-development.md#checkpoint-manifest-identity-and-verification) file, which must be of schema version 4 and carry the `camera_hud` group, and so pins the camera helper, the presence watcher, the bridge, the models, every module, the inference runtime's version and the camera HUD's scripts, and records each fixed layout's digest |
| `run_id` | 32 random hexadecimal characters; single-use through the ledger |
| `created_utc`, `deadline_utc` | absolute UTC times; the deadline is at most 240 minutes after creation, and the whole run must fit before it |
| `max_seconds` | the run's duration bound, at most the step's: 120 s for L-D3, 30 s for L-D4, 90 s for L-D5 |
| `approval` | a reference to the owner's approval: an identifier of at most 64 characters, never free text |
| `statement` | the fixed sentence that the record is a procedural control, opens no gate and is not an approval |

**The writer.** `scope.write_record` creates a record, and only
`scripts/stage_scope_record.py` calls it (a test pins that). The tool is for
the Frame manager, when staging a step the owner approved. It lives in
`scripts/` as a standalone file and is deliberately not one of the active
operator commands: it has no body in `sightcircuit/commands/`, no console
entry point, and no command imports it, so nothing an operator starts a run
with can create a record. The checkpoint manifest pins it in the `camera_hud`
group. It reads the revision, the manifest digest and the layout's
digest from the checkout, requires the manifest to carry the `camera_hud`
group and to verify at that moment (so never a dirty tree or another build),
takes the source and the default duration from the step, and refuses an
unknown step, a free-text approval, a duration over the step's bound, a
validity outside 1 to 240 minutes, an existing path and a path that would
dirty the checkout. What the run may show is named, not defaulted:

| Option | For | Without it |
| --- | --- | --- |
| `--layout ID` | every step | refused: no record names a layout nobody chose |
| `--region X0,Y0,X1,Y1`, `--rotation DEGREES` | camera steps | the whole frame, unturned, stated in the record; the synthetic step refuses both |
| `--alignment SX,SY,OX,OY,TURN` | camera steps on a panel layout | refused there (the neutral alignment is `1,1,0,0,0`), and refused everywhere else when given |
| `--presence-watch required` or `none` | camera steps | refused for a camera step; the synthetic step refuses `required` |

```bash
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D3 \
  --layout fixed-edge-hud-v1 \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D3.json
```

Running it is a device-side staging action that follows an approval; it is
not run from a cloud session, and the block above is its shape, not an
instruction.

**The check.** A runner holds a `RunScope`: the record's path, the manifest's
path, its own name, the step it is running, the source it will read, its
duration, the layout it shows (one of the contract's own objects; the strip
unless another is named) and its view: region, rotation, alignment and
whether it holds the presence watch. Naming the paths permits nothing.
`permit(use, holder)` answers the literal `True` or `False`. The first call
verifies everything, in order, and stops at the first refusal, which
`refusal` names:

| Order | Check | Refusal |
| --- | --- | --- |
| 1 | The request: runner `camera_hud`, a known step and source that belong together, a positive finite duration, a layout that is one of the contract's objects (not a copy or a name), and a view that is well formed and belongs to that source and layout | `scope_runner_not_permitted`, `scope_source_not_permitted`, `scope_step_unknown`, `scope_step_source_mismatch`, `scope_duration_invalid`, `scope_geometry_mismatch`, `scope_region_invalid`, `scope_region_not_for_source`, `scope_alignment_invalid`, `scope_alignment_not_for_layout`, `scope_presence_watch_invalid`, `scope_presence_watch_not_for_source` |
| 2 | The record: present, at most 4,096 bytes, no key written twice, of schema version 2, exactly the fields above, a known step with that step's source, a fixed layout with this checkout's digest for it, the view stated as the writer states it for that source and layout, a duration within the step's bound | `scope_record_missing`, `scope_record_schema_unsupported`, `scope_record_invalid`, `scope_step_unknown`, `scope_source_not_permitted` (the mirror, both cameras, anything else), `scope_step_source_mismatch`, `scope_geometry_mismatch`, the view's refusals of row 1, `scope_duration_over_bound` |
| 3 | The request against the record, field by field: the same step and source; the same layout by id and digest; the same region, rotation, alignment and presence-watch requirement, compared exactly, number for number; a duration within the record's | `scope_step_mismatch`, `scope_source_mismatch`, `scope_layout_mismatch`, `scope_region_mismatch`, `scope_rotation_mismatch`, `scope_alignment_mismatch`, `scope_presence_watch_mismatch`, `scope_duration_over_record` |
| 4 | The clock: the record was not created more than 60 s in the future, the deadline has not passed, it is not more than 240 minutes ahead, and the run would end before it | `scope_record_from_the_future`, `scope_record_expired`, `scope_deadline_too_far`, `scope_run_would_pass_deadline` |
| 5 | The revision: the checkout's commit is the record's | `scope_revision_mismatch` |
| 6 | The manifest: the file's digest is the record's; it is of schema version 4; it carries the `camera_hud` group with a digest for the helper and for the presence watcher, for all three steps; the digest it recorded for the record's layout is the record's; and `verify_manifest` reports `verified` (clean tree, every pinned file, binary, model and package as recorded) | `scope_manifest_digest_mismatch`, `scope_manifest_schema_unsupported`, `scope_manifest_lacks_camera_group`, `scope_manifest_layout_mismatch`, `scope_manifest_not_verified` |
| 7 | The run id: unused, and marked used now by creating `artifacts/scope-ledger/<run_id>.json` exclusively | `scope_run_id_used`, `scope_ledger_unavailable` |

Only then is the scope granted, for its source's uses: real HUD output, and
for the left camera also camera input to the display. The run id is consumed
as the last step of the verification, inside the permission check, so before
the bridge or the helper is launched. An exception anywhere in it is a
refusal (`scope_verification_failed`).

**Which refusals spend the record.** Rows 1 to 6 consume nothing. A run that
differs from its record in any bound field (row 3), or whose record, clock,
revision or manifest does not verify, is refused with the run id unused, and
the same record still serves a run that asks for what it binds. The run id
is spent by row 7 alone, when everything else verified; from then on it
stays spent whatever happens: a start refused past the deadline, a bridge
rewritten between the check and the launch, a run that fails before or
after its bridge started. A run that is refused, or that fails before its
bridge started, gives its scope back (`ObservedFrameClient.withdraw` revokes
it), so that scope object starts nothing later; that is item L10's rule,
unchanged ([revoking](#the-one-run-scope-record), below). Once the bridge
has started `withdraw` does nothing: the scope stays `granted` for the rest
of that run, its client can start no second bridge, and no other client can
hold its use. A re-run after a spent record is a new approval and a new
record.

**Two names for one refusal, and which to read first.** A refused run
carries the site's name in `reason` and the scope's own in
`command.refusal`. Read `command.refusal` first: it says what the record
check found (`scope_layout_mismatch`, `scope_step_mismatch`,
`scope_record_expired`, ...), or `site_preconditions_failed` when a site
refused before the record was consulted. `reason` only says which site
refused and whether the layout differed: it is
`fixed_hud_layout_not_approved` whenever the layout about to be launched is
not the one the scope and the record it read bind, and that can be true
while the scope stopped at an earlier check. A record for another step that
also names another layout ends `reason: fixed_hud_layout_not_approved`,
`command.refusal: scope_step_mismatch`: both are true, and the step is what
to fix first.

**The two sites compare what runs with what was asked.** A scope is granted
for a request equal to its record; each permission site then requires the
request to be what actually runs, before the record is consulted. The output
site compares the layout the client is about to launch with the scope's and,
once the record is read, with the record's id and digest
([who may show it](box-panel.md#who-may-show-it)); it already compared the
duration and the bridge. Each site reads the client's scope once and
judges, asks and compares that one object. The camera site compares the
layout and the view with the stage's own (`stage_view_bound`): the scope's
layout must be the contract's object the stage draws for, and its region,
rotation, alignment and presence-watch requirement, stated as a record
states them, must equal the stage's region and turn, its alignment's five
numbers (or none) and whether it holds the watch. Any difference is refused
with the record unspent, so a record is never spent on a run that would not
do what it says. The stage holds no presence watch yet (the watch on the
displayed path is the last part of item L17), so a record that requires the
watch is still refused by every run.

**What verification runs.** It hashes files and runs read-only queries: `git`
for the commit and the tree's state, and, where no package database is
readable (the Frame's case), the manifest's read-only `pacman -Q` for the
SteamVR package. It starts nothing else, and nothing that it starts writes.

**Holders, and asking again.** A use goes to the first holder that asks for
it (the client for real output, the stage for camera input) and to no other
object. Nothing keeps the answer outside the scope. When the holder asks
again, which the client does at `start()` right before it launches the
bridge, the scope answers `True` only if it has not been revoked, the run's
remaining seconds still end before the deadline, and the binary that use
launches still has the digest the manifest recorded. Nothing is consumed
again. So a start after the deadline, or with the bridge rewritten after the
check, is refused. What remains between that answer and the launch is the
launch itself. For the helper, `run_export` hashes the binary once more
right before its launch and ends the run `helper_digest_mismatch`, with
nothing launched, if it is not the manifest's.

**Revoking.** A run that is refused, or that fails before its bridge started,
revokes its scope (`ObservedFrameClient.withdraw`), so a grant can never
start anything later. The run id stays spent.

**The duration is bound to what runs.** The scope's seconds are not only a
declared number:

- A real-output client carries its own bound, `max_output_seconds`, which the
  output site requires to be present and no longer than the scope's seconds.
  The client enforces it: that long after `start()` is called its writer
  drops what waits, writes the hide and the stop packet and accepts nothing
  more, whatever its caller does. A real-output client with no bound is
  refused.
- The camera site requires the stage's own `seconds` (a stage with none is
  refused), the helper's `max_seconds`, and the helper's `max_frames` at its
  frame rate, all to lie within the scope's seconds.

**The ledger.** It lives in the checkout's ignored `artifacts/` directory,
so it does not dirty the tree and no environment variable moves it. This is
where the control is plainly procedural: a fresh clone has an empty ledger,
and removing the ledger directory makes a spent record usable again until
its deadline.

**The two call sites.** `fixed_hud_output_permitted` (above) is the output
site. Its preconditions, checked before anything is consumed
(`output_preconditions`): an `ObservedFrameClient` in real output, whose mode
is fixed at construction; a `RunScope` of that exact type; a bound of its
own within the scope's seconds; and the checkout's own
`artifacts/overlay-bridge/sightcircuit-overlay-bridge`, the file the manifest
hashes (`scope.pinned_binary`; a copy elsewhere is refused, a link to it is
it, and `~` expands as it does for the helper's path). It then asks every
callable in the client's `prerequisites`, and only then the scope.

`camera_display_input_permitted` in `sightcircuit/hud/camera_stage.py` is the
one check for camera input feeding the display path: `CameraHudStage.run`
hands its answer to the `LIVE_CAMERA_INPUT` gate's `require` whenever its
client is in real output, after the output check and before the helper or the
bridge is launched. Its preconditions
(`camera_display_input_preconditions`): a `CameraHelperSource` on the left
camera whose helper is the checkout's own
`artifacts/camera-helper/sightcircuit-camera-helper`, a scope asked for
`arcturus_camera_left`, and the bounds above. The stage registers those
preconditions as the client's prerequisite, so the output site checks them
before it consumes the record: a wrong helper path, an unbounded stage or the
synthetic step's record on a camera stage is refused with the record unspent
(by the output gate, since that is the site that would have consumed it). A
stage on the validation bridge displays nothing and asks neither site; that
run stays the bounded recognition without display. Both sites take only a
`RunScope` of that exact type and only its literal `True`. `run_pipeline`,
the mirror's HUD mode and `require_synthetic` are unchanged and never consult
a record.

`RunScope.report()` gives identifiers and format metadata only, for a
command's report: the requested and the recorded step and source, the run
id, the revision, the manifest digest, the deadline, the bound, the approval
reference, whether it was granted, revoked and consumed, and the refusal;
and, since the record's schema version 2, `requested_layout` (stated from
the start) with what the accepted record binds: `hud_config_id`,
`hud_config_sha256`, the region as `{x0, y0, x1, y1}`, the rotation, the
alignment's five numbers (a copy) and `presence_watch`.

**A stored report from before this change.** Reports that carry every gate
claim (`gates.claims()`) gained `production_camera_input_enabled`. The
mirror route's validators require each claim to be present and `False`, so
an observer or marker report stored before work-order item L10 no longer
validates (`hud/observation.py`, reached from `hud/candidate.py`). That is
left failing closed: an absent claim is not read as false. The mirror route
is parked, and a new observation would be a new report.

`tests/test_scope_record.py` builds a temporary checkout with a manifest per
case and refuses each of: no record, an expired one, a run that would pass
the deadline, a record from the future, a deadline too far ahead, a wrong
revision, a wrong manifest digest, a manifest without the camera group, a
rebuilt helper or bridge, a changed model, a dirty tree, a reused run id
(also in a race), an unwritable ledger, the mirror and every other source,
both cameras, another geometry, another runner, another step, a duration
over the bound, a stage or a helper bounded beyond the scope's seconds, a
client with no bound, a record for another step's source, a malformed record
and one with a repeated key, a start after the deadline or with a rewritten
bridge, a revoked scope, a truthy answer that is not `True` at both sites, a
valid record named only by environment variables, and the synthetic-path
bypass. It also runs a real-output client against the fake OpenVR runtime
with a caller that never stops, and sees the client end output at its bound.
The records that verify there are test fixture records.

## The report

`report()` returns kind `camera_hud_stage`, schema version 1. Three blocks
and three keys of `config` (`max_boxes`, `alignment`, `update_bytes`) were added with the box layout (items B5 and L17);
no key was removed or changed its meaning.

| Block | Holds |
| --- | --- |
| top level | `status`, `reason`, `error_type`, the three gate claims (all false), `validation_only`, `bridge_mode`, `images_saved`, `pixels_in_report` and `physical_display_observed` (all false), `fields_withheld` |
| `config` | the layout's id and digest, the tile size and `update_bytes` (its RGBA bytes: 163,840 or 1,048,576), `rows` (the strip) or `max_boxes` (the panel), the threshold, `directions` and `orientation` (null on the panel), `alignment` (the five numbers; null on the strip), the bounds, the maximum display age and the keepalive interval |
| `view` | the region as `{x0, y0, x1, y1}` (a report holds no list of fractions), the rotation, `whole_frame`, `prepared` (whether frames went through `region.prepare`), the box space's name and size, where the region lay in the first frame (`crop_native_xyxy`, `resized_px`), `geometry_constant`, and `invalid_detections`: detections dropped before mapping because their box was not a valid box in the detector's input |
| `boxes` | the panel only (null on the strip): [`boxes_report`](box-panel.md#the-report-block) of every counted result, the metadata from the first and the counts summed (`detections`, `boxes_selected`, `boxes_dropped`, `dropped` per reason), with `results_counted`. `boxes_selected` is that block's `boxes_shown` under the name of what it counts here: boxes selected and drawn, whether or not the result was then sent (a result too old or after the end is drawn and not sent). `results_sent` and `boxes_sent` count the results the client accepted and the boxes in them. No label, score, box or pixel |
| `steps_ms` | this side's own work per result: `map`, `select`, `draw` for every result drawn, `pack` for every result packed and `queue` for every result the client accepted (a submit it refused is not timed), each with count, p50, p95 and max on this host's `perf_counter` |
| `identities` | the identity rule (`camera_frame_sequence`), the first and last frame identity written, the last result index and the source id |
| `results` | results seen, with rows, without rows, too old, and after the end (on the panel "rows" reads "boxes": the keys are the strip's and are kept) |
| `frames` | `submitted` (handed to the client), `sent` (written to the pipe), `not_sent` (replaced, withdrawn by a take-down or past its deadline before the write), `written_past_deadline`; the bridge's answers per frame: `acknowledged_presented`, `acknowledged_refused`, `refused_reasons`, `unacknowledged`; and the bridge's final counts: `bridge_received`, `bridge_accepted`, `retransmitted`, `refused` (stale or expired on arrival), `expired`, `expired_in_upload` |
| `deadlines` | the cap, the longest deadline after its observation, `within_cap` |
| `end` | `run_start_ns` (the bridge was ready), `ended_ns` (the watcher saw `stop`), `hide_written_ns` and `bridge_hide_returned_ns` for the final hide with each one's milliseconds after the end, `tile_already_down`, `bridge_ended_first` |
| `timings` | below |
| `frame_expired` | the count, the first 32 events with the bridge's fields plus `noticed_after_deadline_ms` and, in real output, `hide_returned_after_deadline_ms`, and the maxima over all events |
| `labels` | the run's start and end, `spells_dropped`, and per label: `display_seconds`, `longest_gap_seconds`, `spells` and `intervals` (`start_ms`, `end_ms` from the run's start) |
| `pipeline` | frame counts, stale skips, the stage timings and the sink cleanup flags |
| `source` | the source's report: status, counts, the helper's digest, the supervision block, the `ready` and `end` records as the source carries them, and `exposure_minus_acquire_ms` as a timing |
| `bridge` | the client's counts, the line counts, the first-frame line, the bridge's final report and `cleanup_confirmed` |
| `cleanup` | `source_ok`, `bridge_ok`, `sinks_ok`, `ok` |

`bridge_accepted` is the bridge's `frames_received`: frames it judged
presentable on arrival, counted before the upload. A frame that then expired
in the upload is in it, so it is not a count of frames presented;
`acknowledged_presented` is.

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
| `capture_to_display_age_ms` | `observed_ns` | the bridge's `frame_presented` line: its upload and show-or-hide calls returned | `host_clock_monotonic` |

`host_clock_monotonic` is `time.monotonic_ns()`, the clock of
`Frame.observed_ns` and of every time the bridge reports.

`capture_to_display_age_ms` covers every frame the bridge acknowledged as
presented and none it refused; `frames_without_acknowledgement` counts
written frames with no `frame_presented` line. It ends at the return of the
runtime's calls, not at the headset's display (`ends_at_headset_display`
false), and a frame presented while the tile was taken down is in it. A pipe
write can block behind a slow upload, so `output_age_ms` is not always the
smaller of the two.

### What was on display

The ledger books only what the bridge said. A spell starts at a
`frame_presented` line with `visible` true, or at the `show_returned` line
that brings a hidden tile up; it ends at the next `frame_presented`, a
`hide_returned` line, or a `frame_expired` line (at the time its hide
returned, in real output, which can be after the deadline). A frame the
bridge refused or never answered for is booked nowhere, however early its
packet was written. A tile still booked when the run closes ends at its
deadline. These are times the bridge's calls returned on the host clock, not
what a headset displayed.

`longest_gap_seconds` counts the time from the run's start to a label's first
spell and from its last to the run's end, as well as the time between spells.
At most 256 intervals are kept over all labels; `spells_dropped` counts the
rest.

`named_in_window(report, label, window_seconds=, fraction=, max_gap_seconds=)`
evaluates "named at least X of a contiguous W-second window with no gap over
G" from a report's intervals: it returns `found`, the window's start, the
named fraction and the longest gap inside it, counting the stretch before the
first spell in the window and after the last. It refuses a report that
dropped intervals. `window_verdict` is the same rule on plain intervals.
Device step L-D5's "at least 90% of a contiguous 60 s window, no gap over
2 s" is `window_seconds=60, fraction=0.9, max_gap_seconds=2`.

On the fake runtime, with a steady label at 5 frames a second for 5 s: with
every upload stalled 1,100 ms the overlay was never visible and the ledger
books nothing (every frame answered `expired_before_show`); with 600 ms
stalls the ledger's total and each interval agree with the fake's visible
spans within 0.1 s and 5 ms.

### What the report cannot hold

`check_report` is the schema, and `report()` runs it on every report. It
enforces:

- the top-level fields are exactly those of `REPORT_BLOCKS`, and each block
  holds only the keys listed for it;
- below that: null, booleans, integers that fit 64 bits, finite floats;
  strings shaped like an identifier (letters, digits and `_.:-`, at most 72
  characters, no white space and no path separator apart from a trailing
  `/<digits>` version), or a sanitised row label under the key `label`;
  objects of at most 48 identifier keys; lists of at most 8 integers,
  booleans, identifiers or nulls (never floats) or of at most 256 objects;
  at most eight levels;
- the whole report, JSON-encoded, is at most 40,960 bytes. A plain run's
  report is 9 to 11 kB; the cap leaves room for 256 intervals (about 45 bytes
  each) and 32 expiry events (about 400 bytes each).

So bytes, buffers, arrays, tuples, prose, paths and wide integers are refused
wherever they sit, and no arrangement of fields can carry a tile (163,840
bytes), a panel update (1,048,576 bytes) or a camera frame: the total is a
quarter of a tile and a twenty-fifth of a panel update.

It does not, and cannot, check that a number or an identifier-shaped string
means what its key says. A few kilobytes of data spelled as numbers inside
the embedded source or bridge report would pass the check. What keeps that
out is that the stage writes a fixed set of fields and that the embedded
reports are filtered, not this function.

The embedded source and bridge reports go through `report_value` first: keys
that hold prose, paths or argument vectors (`error`, `detail`, `path`,
`arguments`, `definition`, `scope`) are left out by name, and any other value
the schema would refuse becomes null and is counted in `fields_withheld`. An
exception's message is therefore never in the report; `status`, `reason` and
`error_type` are. The placement numbers in the bridge's `fixed_hud` block are
the HUD contract's constants.

## The command (L9)

`sightcircuit/commands/camera_hud.py` is the fifteenth command; the
[camera HUD guide](../guides/camera-hud.md) owns the operator procedure. It
is the only code that builds a `RunScope`, and it has three modes.

**`--validate`** runs `run_camera` on the helper's fake backend with a
scripted detector and the validation bridge, once for the strip from the
whole frame and once for the box panel through a region turned a quarter
(`camera_panel` in its report, whose schema version is 2 since that part was
added), then the synthetic rows on the validation bridge with one stop. The
panel's pass is judged on its `boxes` block as well: the block must be
there, with a positive `results_sent` equal to the frames sent, one box sent
for each (`boxes_sent`) and one selected for each counted result; otherwise
the validation fails with `camera_panel_path_failed`. Before asking the helper to export anything
it reads the helper's `--describe` record (no connect, no camera) and refuses
unless it says `backend: fake` with no SDK release. It needs no record and no
terminal.

**`--synthetic-rows`** runs `SyntheticRows`
(`sightcircuit/hud/synthetic_rows.py`), the driver of step L-D3. It sends one
observed frame five times a second, a tile whose single names-only row is
`count NNNN`, with `observed_ns` the time the tile was drawn and a deadline
one second later. `schedule(seconds, stops, stop_seconds)` splits the run
into `stops + 1` segments of updates with a stop between each (2 to 10 s
each, at most 8 stops, at most 120 s in all, at least one second of updates
per segment). In a stop no frame and no hide request is sent, so the bridge
takes the tile down at the last frame's deadline by itself; the client's
keepalive does write one show request per stop, which the bridge, with
nothing presented, answers `no_image`. The report
(`camera_hud_synthetic_rows`) pairs each stop with the bridge's
`frame_expired` line for the frame that was up. The bound is two named
parameters of the driver, with acceptance row G9 as their source, both
measured from the frame's deadline: `hide_called_within_ms` (one bridge
poll, 20 ms) and `hide_returned_allowance_ms` (250 ms, the owner's accepted
value of 2026-11-16, as row G9 states). Each stop reports both measured values and
the verdict of each (`hide_called_within_poll`,
`hide_returned_within_allowance`), the same times from the observation, and
`within_bound_as_written` for the row's first wording (1,000 ms plus one poll
from the observation), kept for comparison. On
the validation bridge there are no hide times; the time the bridge noticed
the expiry stands in for the hide's call and the second verdict is null. The camera is never opened and
`camera_derived` is false by construction: the row's text is built from the
counter alone.

**`--camera`** runs `run_camera`: `CameraHelperSource` on the left camera,
the chosen detector, `CameraHudStage` and an `ObservedFrameClient` in real
output carrying the run's `RunScope`. `--step` (L-D4 or L-D5) is required
and must be the record's step. `--directions` or `--names-only` and
`--orientation` are required and passed to the stage. `--layout` names the
fixed layout (the strip unless given); the box panel requires `--alignment`
(five numbers, no default) and refuses the strip's three options, and the
strip refuses `--alignment`. `--region` and `--rotation` are
`camera_recognize`'s, parsed by the same `parse_region`. All four are
checked by the parser, before the informed start and before any record is
read, and they are what the run's `RunScope` asks the record for (with no
presence watch: the command holds none yet). A panel run's informed start
shows `BOX_DISPLAY_NOTICE` in place of the strip's display notice. The client's own bound
on real output is the scope's seconds: the run's `--seconds`, which also
bound the stage and the helper; for the synthetic rows it is `--seconds`
plus a 10 s allowance for the bridge's start. The run is on the main
thread, the helper under the supervision primitive, and never routed through
`ReplayWorker` or the live runner.

**Signals.** In both device modes, from the typed `yes` until the report is
printed, SIGINT, SIGTERM and SIGHUP are recorded and never raised
(`guard.cancellation`). The run stops at the next 50 ms poll, its teardown
finishes, and the report is the run's own, `cancelled` with reason `signal`,
however many signals arrive and whenever (during the record's verification,
the bridge's start, the run or the teardown). The previous handlers are
restored afterwards and no thread or descriptor is left behind. A terminal's
Ctrl-C also reaches the bridge, which shares the foreground process group:
it retires its overlay and exits 0 by itself, and this side's next write
fails. A run that saw a signal and whose bridge reported a clean retirement
of its own is `cancelled` with `cleanup.bridge_ok` true
(`camera_stage.bridge_retired_itself`; `end.bridge_left_on_signal` or
`end.bridge_left_on_stop` says so). Without a signal the same bridge exit is
still an error. That judgement is the stage's and the driver's; the client's
own `cleanup_confirmed` is unchanged.

**Preconditions before the record.** The `--report` path (before the
question), the pinned helper and bridge, the helper's lock directory
(`XDG_RUNTIME_DIR`, by the helper's own rule), the detector and the target
label. The helper's supervision waits `ATTACH_SECONDS` (the bridge's ready
wait plus 5 s) for the pipeline to attach, because the pipeline starts the
bridge first; a bridge that takes six seconds no longer ends the run.

**The target label's intervals.** With `--target-label` the stage keeps that
label's display intervals in a budget of their own (128) and every other
label shares the rest (128), so other labels flickering cannot drop one of
the target's intervals and void its G10 verdict
(`labels.target_spells_dropped`).

In both device modes the order is: the parser's checks; the informed start on
the terminal (`sightcircuit/camera/confirmation.py`, shared with
`camera_recognize`); then the cheap preconditions (the pinned helper and
bridge exist, the detector loads, the target label is one of its classes),
checked before the record so a typo does not spend it; then the stage or the
driver, whose first act is the permission check. A gate's refusal ends
`refused` with nothing launched. The typed answer never creates a record and
never stands in for one.

The `--camera` report is the stage's report plus `command`, `scope_record`,
`marks`, `timebase` and `target`, checked by `check_report` with the
command's field table and a size limit of 61,440 bytes (the stage's 40,960
plus 20,480 for those blocks; still well under one tile). `target` is
`named_in_window` for `--target-label` with row G10's numbers (60 s, 90%,
2 s). A report that does not fit its schema, the stage's or the driver's own
included, is replaced by a short error report that says so
(`report_replaced: true`) and keeps the command's block, the scope's block
and the cleanup verdicts; it is never printed as it is.

## Cloud checks

`tests/test_camera_hud_command.py` covers the command: validate mode on the
helper's fake build, and its refusal of every helper that does not describe
itself as the fake; the parser's refusals; no terminal, a declined answer and
a typed `yes` without a record, each with nothing launched and no record
written; a record for the wrong source, the mirror, both cameras, another
runner, a longer run and an expired record; the cheap preconditions leaving
the record unspent; the synthetic-rows schedule; and, on `--fixed-hud`
against the fake runtime under a test fixture record (a temporary checkout
whose pinned bridge is a copy of the bridge built here and whose pinned
helper is the camera stand-in), the synthetic rows expiring at each stop and
returning after it, the camera mode end to end, a headset removal, and the
command line from the typed `yes` to the saved report, with the second
launch on the same record refused.

`tests/test_observed_frame_client.py` (standard library, validation bridge):
the packets and their order, the take-down and the show, the end latch and a
submit racing it, the bridge's answer per frame, the keepalive past the
bridge's ten-second timeout with nothing shown, the frame that is replaced or
outlives its deadline before the write, each `frame_expired` field, a failure
on the writer thread, the refusals, and the gate.

`tests/test_camera_hud_stage.py` runs the stage on the camera stand-in. On
the validation bridge: every frame's observation and deadline, the per-frame
acknowledgements, empty rows, a slow detector, a presence end
(`headset_removed` and `standby`), a stop, an end set during a tile's colour
conversion, the pipeline's own bounds, a detector error, helper errors, a
bridge killed with the detector idle and with it busy for three seconds, a
failure on the client's writer thread, NumPy arguments, names-only rows and
each orientation, the gate refusal with nothing launched, the ledger, the
window verdict, and the report schema with its fan-out forms. On
`--fixed-hud` against the fake runtime (`tests/fixtures/fake_vrclient.cpp`),
with the two permission functions patched for the test: the runtime's hide and
show calls at the take-down and the show, the bridge's own hide at a
deadline, the hide at a presence end, the end racing a submit, the
end-of-run hide behind a stalled upload, keepalives on a live tile and after
its expiry, the ledger against the fake's visible spans with stalled
uploads, the retirement at every end, the placement applied exactly once,
and, from the fake's call log, that the overlay was never visible past the
deadline of the tile uploaded last (within one bridge poll and this host's
scheduling margin of 150 ms, which is not a claim about the bridge).

The validation ledger of the mirror route (`ValidationLedger`) is not reused
for the deadline check: it requires a mapping candidate and a capture
context, which the camera route does not have.
