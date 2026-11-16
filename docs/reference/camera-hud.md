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
`validation_only=False` the client would start `--fixed-hud`. `check_start()`,
which `start()` calls before anything is launched, passes the answer of
`fixed_hud_output_permitted(client)` to the `LIVE_HUD_OUTPUT` gate's
`require`. That function is the one place that decides. It answers `True`
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
| A bridge that ends, or a client that fails, ends the run at once | The same watcher looks at the client every 20 ms and sets `stop` itself, so the camera helper is stopped whatever the detector is doing; `end.bridge_ended_first` records it |
| A frame the client refuses is never silently lost | `submit` counts it as after the end when the run has ended, and raises otherwise (`observed_frame_client_not_running`) |

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
sitting, the wrong source, a longer run, another program.

| Field | Bound to |
| --- | --- |
| `step` | `L-D3`, `L-D4` or `L-D5` |
| `source` | `synthetic` for L-D3 (the camera is never opened), `arcturus_camera_left` for L-D4 and L-D5; the step fixes it |
| `runner` | `camera_hud`, the only runner a record may name. The caller declares its own name, so this field keeps a record written for another runner from being accepted by a caller that names itself truthfully; it cannot identify the calling program |
| `hud_config_id`, `hud_config_sha256` | `fixed-edge-hud-v1` and the digest of the HUD contract in this checkout |
| `revision` | the checkout's full commit |
| `manifest_sha256` | the SHA-256 of the [checkpoint manifest](hud-development.md#checkpoint-manifest-identity-and-verification) file, which must carry the `camera_hud` group and so pins the camera helper, the bridge, the models, every module, the inference runtime's version and the camera HUD's scripts |
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
group. It reads the revision, the manifest digest and the configuration
digest from the checkout, requires the manifest to carry the `camera_hud`
group and to verify at that moment (so never a dirty tree or another build),
takes the source and the default duration from the step, and refuses an
unknown step, a free-text approval, a duration over the step's bound, a
validity outside 1 to 240 minutes, an existing path and a path that would
dirty the checkout.

```bash
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D3 \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D3.json
```

Running it is a device-side staging action that follows an approval; it is
not run from a cloud session, and the block above is its shape, not an
instruction.

**The check.** A runner holds a `RunScope`: the record's path, the manifest's
path, its own name, the step it is running, the source it will read, its
duration and the tile configuration. Naming the paths permits nothing.
`permit(use, holder)` answers the literal `True` or `False`. The first call
verifies everything, in order, and stops at the first refusal, which
`refusal` names:

| Order | Check | Refusal |
| --- | --- | --- |
| 1 | The request: runner `camera_hud`, a known step and source that belong together, a positive finite duration | `scope_runner_not_permitted`, `scope_source_not_permitted`, `scope_step_unknown`, `scope_step_source_mismatch`, `scope_duration_invalid` |
| 2 | The record: present, at most 4,096 bytes, no key written twice, exactly the fields above, a known step with that step's source, the fixed configuration, a duration within the step's bound | `scope_record_missing`, `scope_record_invalid`, `scope_step_unknown`, `scope_source_not_permitted` (the mirror, both cameras, anything else), `scope_step_source_mismatch`, `scope_geometry_mismatch`, `scope_duration_over_bound` |
| 3 | The request against the record: the same step, source and configuration, a duration within the record's | `scope_step_mismatch`, `scope_source_mismatch`, `scope_geometry_mismatch`, `scope_duration_over_record` |
| 4 | The clock: the record was not created more than 60 s in the future, the deadline has not passed, it is not more than 240 minutes ahead, and the run would end before it | `scope_record_from_the_future`, `scope_record_expired`, `scope_deadline_too_far`, `scope_run_would_pass_deadline` |
| 5 | The revision: the checkout's commit is the record's | `scope_revision_mismatch` |
| 6 | The manifest: the file's digest is the record's; it carries the `camera_hud` group with the helper's digest, for all three steps; and `verify_manifest` reports `verified` (clean tree, every pinned file, binary, model and package as recorded) | `scope_manifest_digest_mismatch`, `scope_manifest_lacks_camera_group`, `scope_manifest_not_verified` |
| 7 | The run id: unused, and marked used now by creating `artifacts/scope-ledger/<run_id>.json` exclusively | `scope_run_id_used`, `scope_ledger_unavailable` |

Only then is the scope granted, for its source's uses: real HUD output, and
for the left camera also camera input to the display. The run id is consumed
as the last step of the verification, inside the permission check, so before
the bridge or the helper is launched. An exception anywhere in it is a
refusal (`scope_verification_failed`).

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

`RunScope.report()` gives identifiers only (the requested and the recorded
step and source, the run id, the revision, the manifest digest, the
deadline, the bound, the approval reference, whether it was granted, revoked
and consumed, and the refusal), for a command's report.

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

`report()` returns kind `camera_hud_stage`, schema version 1.

| Block | Holds |
| --- | --- |
| top level | `status`, `reason`, `error_type`, the three gate claims (all false), `validation_only`, `bridge_mode`, `images_saved`, `pixels_in_report` and `physical_display_observed` (all false), `fields_withheld` |
| `config` | the HUD configuration id and digest, the tile size, rows, threshold, `directions`, `orientation`, the bounds, the maximum display age and the keepalive interval |
| `identities` | the identity rule (`camera_frame_sequence`), the first and last frame identity written, the last result index and the source id |
| `results` | results seen, with rows, without rows, too old, and after the end |
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
bytes) or a camera frame: the total is a quarter of a tile.

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
scripted detector and the validation bridge, then the synthetic rows on the
validation bridge with one stop. Before asking the helper to export anything
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
poll, 20 ms) and `hide_returned_allowance_ms` (250 ms by default, a proposed
value under the owner's review). Each stop reports both measured values and
the verdict of each (`hide_called_within_poll`,
`hide_returned_within_allowance`), the same times from the observation, and
`within_bound_as_written` for the row's own wording (1,000 ms plus one poll
from the observation), so the evidence stands whatever bound is accepted. On
the validation bridge there are no hide times; the time the bridge noticed
the expiry stands in for the hide's call and the second verdict is null. The camera is never opened and
`camera_derived` is false by construction: the row's text is built from the
counter alone.

**`--camera`** runs `run_camera`: `CameraHelperSource` on the left camera,
the chosen detector, `CameraHudStage` and an `ObservedFrameClient` in real
output carrying the run's `RunScope`. `--step` (L-D4 or L-D5) is required
and must be the record's step. `--directions` or `--names-only` and
`--orientation` are required and passed to the stage. The client's own bound
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
