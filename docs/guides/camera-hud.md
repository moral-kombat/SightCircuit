# Show camera-derived names on the fixed HUD (`camera_hud`)

`camera_hud` is the fifteenth command (work-order item L9 of the
[camera display work order](../live-capture.md#camera-display-work-order-adopted-2026-11-16)).
It drives the three displayed device steps: L-D3 (a synthetic picture, no
camera), L-D4 (first displayed camera labels) and L-D5 (the 60-second
acceptance, row G10), each on one of the bridge's two fixed layouts: the
strip of names at the edge of the view, or the
[box panel](#the-same-steps-on-the-box-panel) in the middle of it.

**This guide is a procedure, not an approval.** Nothing in it authorises a
device run. Each step needs the owner's explicit approval, staged by the Frame
manager, which confirms in its own session. A re-run after any failure is a
new approval with a new record.

**Nothing here has run on the Frame.** The command, the stage, the bridge's
`--fixed-hud` mode and the scope record have been checked in the cloud only:
against the camera stand-in, the helper's fake backend, the validation bridge
and a fake OpenVR runtime. No step below has a device result, and no time on
this page is a Frame measurement.

## Gate statement

Every gate stays closed. The command opens none, and every report carries
`gates.claims()` with all four claims `false`. Showing output on the real
runtime and feeding camera frames to a display are gated uses
(`LIVE_HUD_OUTPUT`, `LIVE_CAMERA_INPUT`). A device mode runs only under a
[one-run scope record](../reference/camera-hud.md#the-one-run-scope-record):
one record for one run of one step. The record is a procedural control
against mistakes, not a cryptographic one; the owner's approval in the Frame
manager's session is the authority. No flag, environment variable,
configuration value or typed answer opens a gate or creates a record.

## The modes

| Mode | What it does | Record needed |
| --- | --- | --- |
| `--validate` | The helper's fake backend, a scripted detector and the validation bridge, for the strip and for the box panel through a region, each with a presence watch armed on a stand-in for the watcher; a run that the watch ends; then the synthetic rows and the synthetic boxes on the validation bridge with one stop each. Nothing is displayed and no camera exists. This is what CI runs | none |
| `--synthetic-rows` | Step L-D3: one row, `count NNNN`, five times a second on the bridge's `--fixed-hud` mode, with scheduled stops in which no frame and no hide request is sent (the client's keepalive writes one show request per stop, which the bridge answers `no_image`) so that the bridge's own expiry is observed. The camera is never opened and no detector is loaded | source `synthetic`, step L-D3 |
| `--synthetic-boxes` | Step L-D3 on the box panel: five named boxes (the orientation scene) five times a second, with the same scheduled stops. The camera is never opened, no detector is loaded and no presence watch is held | source `synthetic`, step L-D3, naming the panel |
| `--camera` | Steps L-D4 and L-D5: the left camera through the stage (detector, rows or boxes, one observed frame per result), at most 90 s, with the [presence watch](#the-presence-watch) held for the whole run | source `arcturus_camera_left`, step L-D4 or L-D5, presence watch required |

A device mode has no option to name another bridge, helper, watcher, camera
or source, and none that turns the presence watch off. It launches only the
files the checkpoint manifest hashes:
`artifacts/overlay-bridge/sightcircuit-overlay-bridge`,
`artifacts/camera-helper/sightcircuit-camera-helper` and, for a camera run,
`artifacts/presence-watcher/sightcircuit-presence-watcher`. The left camera
(index 0) is the only camera.

## Validate in the cloud or on the Frame

```bash
bash scripts/build_overlay_bridge.sh && bash scripts/build_camera_helper.sh --fake
.venv-replay/bin/python scripts/camera_hud.py --validate
```

Expect `report_kind: camera_hud_validation`, `status: validated` and exit 0.
The mode refuses any helper whose `--describe` record does not say
`backend: fake` (`validate_requires_fake_helper`) before asking it to export
anything, so it cannot open a camera. `--helper PATH` names another fake
build. Validation touches no display and needs no approval, but on the Frame
it is still run by the operator, not from a cloud session.

## Before a device step

All of this is preparation. None of it opens the camera or shows anything.

### The operator's rebuild

1. Check out the approved revision with a clean tree.
2. Rebuild the helper, the bridge and the presence watcher on the Frame from
   that revision. The helper speaks camera export v2, and the bridge on the
   Frame predates `--fixed-hud`, so both must be rebuilt; the watcher is
   pinned by the manifest of step 4 since its schema version 4, and every
   camera run launches it. **A helper built before 2026-11-25 must be
   rebuilt before its next use**, for any command: one clause of the
   export notice the helper prints changed that day (a display command
   "shows the names of detected objects in the headset, with approximate
   arrows or as boxes drawn around them"), which moved the camera contract
   digest; an older helper prints the earlier notice and reports the
   earlier digest ([the helper's reference](../reference/camera-helper.md#build)).
   The SDK directory `DIR` is the operator's own copy, outside the
   checkout, as in the
   [probe guide's preparation](camera-probe.md#preparation):

   ```bash
   bash scripts/build_camera_helper.sh --sdk DIR
   bash scripts/build_overlay_bridge.sh
   bash scripts/build_presence_watcher.sh
   ```

3. If SteamOS, SteamVR or Mesa changed since the last step, the Frame manager
   first re-runs the helper's `--describe`, the offline tests and
   `--query-gpu`, as it did for E99.
4. Write the checkpoint manifest after the builds. It pins the commit, the
   bridge, the helper, the presence watcher, the models, every module and
   this command's scripts, and records the digest of each fixed layout:

   ```bash
   mkdir -p artifacts/camera-hud
   .venv-replay/bin/python scripts/checkpoint_manifest.py \
     --write artifacts/camera-hud/checkpoint-manifest.json --camera-hud
   ```

   `--camera-hud` makes the manifest carry the camera HUD's group, which a
   scope record requires for all three steps; a missing helper or watcher is
   then reported as a problem. `--write` never overwrites; move an earlier
   manifest aside first. A manifest written before schema version 4 does
   not verify and no record can be staged against it: write a new one.
5. Run `--validate` (above) on the rebuilt binaries.

### The Frame manager's staging and the record

After the owner has approved one step, and only then, the Frame manager
verifies the manifest and writes the record for that step. The record names
the approval by an identifier; it is not the approval.

```bash
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/camera-hud/checkpoint-manifest.json &&
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D3 \
  --layout fixed-edge-hud-v1 \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D3.json
```

`stage_scope_record.py` is not an operator command and `camera_hud` cannot
write a record. The record is bound to the commit, the manifest's digest, a
single-use run id, an absolute UTC deadline (the whole run must end before
it), a duration bound (at most 120 s for L-D3, 30 s for L-D4, 90 s for L-D5),
the source, the step, and since its schema version 2 exactly what the run
shows and through which view: the layout (`--layout`, which has no default),
and for a camera step the region and rotation, the alignment where the
layout takes one, and that the presence watch is required
(`--presence-watch required`, which has no default either;
[the reference](../reference/camera-hud.md#the-one-run-scope-record) lists
them). Any rebuild, edit or new commit after staging makes the record
refuse; so do a second run, a run for another step, a run with another
layout or view than the record names, a clock that reads earlier than the
record's creation, and a start after the deadline. A record written before
schema version 2 is refused by name. `camera_hud --camera` takes the same
four things as options (`--layout`, `--region`, `--rotation`,
`--alignment`; [below](#the-layout-and-the-view)) and runs only under a
record that names exactly what it was asked to show.

A camera step's record needs one option more than L-D3's:
`--presence-watch required`. Every run that shows camera-derived names or
boxes in the headset holds the presence watch (owner decision of
2026-11-25), so the staging tool writes no camera record without it:
`--presence-watch none` is refused, `scope_presence_watch_required`, for
either layout. The option has no default, so whoever stages the step states
the requirement. For L-D4 on the strip (the same for L-D5 with its own step
and file names):

```bash
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/camera-hud/checkpoint-manifest.json &&
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D4 \
  --layout fixed-edge-hud-v1 --presence-watch required \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D4.json
```

Without `--region` and `--rotation` the record states the whole frame,
unturned. A record staged with a region, a rotation or the panel's layout
(which also needs `--alignment`) serves only a run given the same options;
any difference is refused with the record unspent, because the run would
not do what the record says.

The duration is not only a number in the record. The command's client ends
real output by itself when the run's seconds are over, whatever else is
happening, and a camera run's stage and helper are bounded within the same
seconds. For the synthetic rows the bound is `--seconds` plus 10 seconds for
the bridge's start, so `--seconds` may be at most 110 under an L-D3 record.

### Before the wearer puts the headset on

- **The helper's lock directory.** `XDG_RUNTIME_DIR` must be set, absolute,
  without a trailing slash, and a directory of this user with mode 0700, as
  the helper requires. The command checks it before the record is consulted
  (`helper_runtime_dir_missing` or `helper_runtime_dir_invalid`).
- **G2 recovery ready.** The owner-rehearsed
  [recovery procedure](../evidence/e079.md#recovery-procedure-and-rehearsal)
  is at hand. The green-input cause is unknown; any green input aborts the
  run.
- **No screen in the cameras' view.** No screen showing the Frame's desktop,
  the replay window or the SteamVR mirror may be in the cameras' view during
  a run.
- **The LEDs.** The white service LED is expected to be lit while the camera
  reader is active; record whether it was seen. The red LED is a code
  property, not an observation: no software path in this checkout touches it
  (the helper's records say `led_written: false`), and the wearer is not
  asked to confirm it.
- The operator stays at the terminal for the whole run. These are attended,
  bounded runs.

## The informed start

A device mode prints its notices on the terminal and waits for the operator's
exact typed `yes` on an interactive terminal: the helper's export notice and
the display notice for `--camera` (the strip's or the panel's), the
synthetic-rows notice for `--synthetic-rows`, the synthetic-boxes notice
for `--synthetic-boxes`. A pipe, a flag or any other answer starts nothing.

The typed `yes` starts this one run. It is not an approval, it creates no
record and it opens nothing. If the record does not verify the run is
refused after the `yes`, before the helper or the bridge is launched, with
`status: refused`, `reason: fixed_hud_output_not_approved` and the scope's
own reason in `command.refusal` (for example `scope_record_expired`,
`scope_step_mismatch` or `scope_run_id_used`; `site_preconditions_failed`
when a permission site refused before the record was consulted, which leaves
the record unspent). A record that names another layout than the run shows
ends `status: refused`, `reason: fixed_hud_layout_not_approved` and
`command.refusal: scope_layout_mismatch`, also with the record unspent; a
record that names another region, rotation or alignment ends
`reason: fixed_hud_output_not_approved` with `scope_region_mismatch`,
`scope_rotation_mismatch` or `scope_alignment_mismatch`, and a camera record
that does not require the presence watch with
`scope_presence_watch_required`. Read `command.refusal` first.

## The passthrough watch

Owner decision 7 applies to every step here. The wearer watches passthrough
during the run and for 30 seconds after it. Only a change in the passthrough
image itself counts: a freeze, black, grey, green, a colour change, judder,
or the loss of "Colour, stable". The tile appearing, updating or expiring is
not a passthrough change. During a planned headset removal the watch resumes
when the headset is back on, and the 30 seconds count from then.

At any passthrough change, any green input, or when the wearer asks, the
operator presses Ctrl-C. The command takes the tile down, stops the helper
and reports `cancelled`, with reason `signal`, exit 130 and the run's own
report: everything shown up to that point, the scope block and the cleanup
verdicts. Further presses change nothing; from the typed `yes` to the
printed report every signal is recorded and none interrupts the teardown or
the saving of the report. A terminal sends Ctrl-C to the bridge as well, so
the bridge may retire by itself before the command has asked it to; that is
still a cancelled run with a clean bridge (`end.bridge_left_on_signal`), not
a failure. Then follow the recovery procedure if it is needed. A cancelled
run is recorded as it happened; it is not a pass.

## Step L-D3: synthetic rows (row G9)

```bash
.venv-replay/bin/python scripts/camera_hud.py --synthetic-rows --allow-hud-output \
  --scope-record artifacts/camera-hud/scope-L-D3.json \
  --manifest artifacts/camera-hud/checkpoint-manifest.json \
  --seconds 90 --stops 3 --stop-seconds 3 \
  --report artifacts/camera-hud/l-d3.json
```

Ninety seconds with three 3-second stops is 405 updates in four segments;
G9 asks for at least 375 acknowledged updates. The wearer watches the counter
advance, sees the tile go blank in each stop, and sees it return. Expect
`report_kind: camera_hud_synthetic_rows`, `status: completed`,
`complete: true` and exit 0. `--seconds` is at most 110 here: the record
allows 120 s and 10 s of them are the bridge's start.

`stops.events` pairs each stop with the bridge's `frame_expired` line. The
expiry bound is two parameters, with row G9 as their source, both measured
from the last frame's deadline: the runtime's hide is called within one 20 ms
bridge poll (`hide_called_after_deadline_ms`, verdict
`hide_called_within_poll`), and it has returned within an allowance of 250 ms
(`hide_returned_after_deadline_ms`, verdict
`hide_returned_within_allowance`). The owner accepted these two numbers on
2026-11-16 and row G9 states them; G9's first wording, 1,000 ms plus one poll
from the observation, left the runtime's call no time, and each stop still
carries `within_bound_as_written` for it and the times from the observation.
The report holds every measured value. A stop also needs `hide_ok` true and `api_visible_after_hide`
false. Those are the times the runtime's calls returned; whether the tile was
seen to go blank is the wearer's observation.

A stop is not silent on the pipe. No frame and no hide request is sent, but
the client's keepalive writes one show request about two seconds into each
stop. The bridge has nothing presented by then and answers `no_image`; it
brings nothing back.

## Steps L-D4 and L-D5: the left camera

L-D4, the first displayed labels: at most 30 s on the bottle, the wearer
turning away at least three times, the headset removed once at the end.

```bash
.venv-replay/bin/python scripts/camera_hud.py --camera \
  --allow-camera-input --allow-hud-output \
  --scope-record artifacts/camera-hud/scope-L-D4.json \
  --manifest artifacts/camera-hud/checkpoint-manifest.json \
  --step L-D4 --seconds 30 --fps 5 --names-only --orientation identity \
  --target-label bottle \
  --mark-labels out_of_view,in_view,out_of_view,in_view,out_of_view,in_view,remove \
  --report artifacts/camera-hud/l-d4.json
```

L-D5, row G10: the 90 s bound, the bottle within 1 m, head turns, the bottle
out of view for 5 seconds and back. The pause is cued at 70 s, after the
60-second window: G10 needs a contiguous 60 s with no gap over 2 s, so a
5-second pause in the middle of a 90 s run would leave no such window on
either side. The cues count from the first camera frame, which leaves room
for the bridge's start before the window and for the return after the pause.

```bash
.venv-replay/bin/python scripts/camera_hud.py --camera \
  --allow-camera-input --allow-hud-output \
  --scope-record artifacts/camera-hud/scope-L-D5.json \
  --manifest artifacts/camera-hud/checkpoint-manifest.json \
  --step L-D5 --seconds 90 --fps 5 --names-only --orientation identity \
  --target-label bottle --cue 70:out_of_view --cue 75:in_view \
  --report artifacts/camera-hud/l-d5.json
```

What the options mean:

- `--step` names the step this run is, `L-D4` or `L-D5`. The record must be
  for the same step: an L-D5 record does not serve an L-D4 run.
- `--directions` or `--names-only` has no default. Owner decision 3: arrows
  if L-D1's offline check passed, else names only. The arrow tolerance for
  L-D5 is agreed in writing before the run.
- `--orientation` has no default either. It is the camera image's
  orientation relative to the view as L-D1 measured it; `identity` in the
  blocks above is a placeholder for that measured value, not a finding.
- `--target-label` adds the report's `target` block: row G10's rule for that
  class (named for at least 90% of a contiguous 60 s window with no gap over
  2 s), computed from the bridge's own lines. A run shorter than 60 s has no
  such window and reports `found: false`.
- `--cue SECONDS:LABEL` prints a prompt with a bell and records the mark;
  `--key-marks` and `--mark-labels` record a mark for each Enter on the
  terminal. They are
  [`camera_recognize`'s marks](camera-recognize.md#times-marks-and-the-target-summary),
  from the same closed vocabulary. A turn away is marked `out_of_view`, the
  return `in_view`, the removal `remove`.

### The layout and the view

Four more options say what a camera run shows and through which view. Each
is checked by the parser, before the informed start and before any record
is read, and the record must name the same values.

- `--layout` is `fixed-edge-hud-v1` (the strip, the default) or
  `head-fixed-box-panel-v1` (the box panel: a box with a name around each
  object, on a transparent 512 × 512 panel fixed in front of the head). The
  panel takes `--alignment` and refuses `--directions`, `--names-only` and
  `--orientation`, which are the strip's.
- `--region X0,Y0,X1,Y1` gives the detector that rectangle of the frame.
  The fractions are of the camera's frame as it is delivered, unturned. The
  rectangle is cropped from that frame, resized, and only then turned
  clockwise by `--rotation` (0, 90, 180 or 270), so a rotation never changes
  which part of the frame is taken. They are
  [`camera_recognize`'s options](camera-recognize.md#a-region-of-view)
  and mean the same. Both are arguments, not findings: which rotation is
  upright for the left camera is not established, so no value here is a
  default to copy.
- **A rotation and the strip's arrows.** The panel applies the rotation: it
  shows the region as the detector saw it. The strip does not: its arrows
  are worked out in the unturned frame, so with `--rotation` alone they
  point as in the camera's delivered image, a quarter turn off for the
  wearer. With `--directions`, give the orientation that pairs with the
  rotation as well: `--rotation 90` with `--orientation rotate_90_cw`,
  `--rotation 270` with `--orientation rotate_90_ccw` (and 180 with
  `rotate_180`, 0 with `identity`). The command does not pair them for you
  and does not refuse another pairing: both stay the operator's arguments,
  because neither value is a finding yet. With `--names-only` there are no
  arrows and the orientation changes nothing shown.
- `--alignment SX,SY,OX,OY,TURN` is the panel's placement of the boxes: the
  project's own five numbers (scale, offset in panel fractions, quarter
  turn), never the SDK's calibration. It has no default; the neutral value
  is `1,1,0,0,0`.

The staging command and the run command for the panel's steps are in
[the same steps on the box panel](#the-same-steps-on-the-box-panel).

Expect `report_kind: camera_hud_stage`. A run that ends on its bound is
`status: completed` (reason `output_bound` when the client's own bound ended
it); a run ended by the headset's removal is
`status: presence_ended` with reason `headset_removed` or `standby`. Both
exit 0 when `cleanup.ok` is true. `cancelled` exits 130; everything else
exits 1.

### The presence watch

Every `--camera` run holds the presence watch, on either layout; there is
no option for it and none against it. Beside the bridge and the helper the
command runs `sightcircuit-presence-watcher`, the
[watcher of the run without display](camera-recognize.md#the-presence-stop---presence-watcher):
a background SteamVR application that reads SteamVR's events and the
headset's activity level, nothing of the camera, and needs SteamVR already
running. Its standby signal ends the run and takes the tile down at once.

| What happens | The report says | Exit |
| --- | --- | --- |
| The watch is armed and the run ends on its own bound | `status: completed`; `presence_watch.armed: true`, `ended_run: false` | 0 |
| The watcher's standby signal arrives during the run | `status: presence_ended`, `reason: standby`; `presence_watch.ended_run: true`, with `hide_written_after_signal_ms` and `bridge_hide_after_signal_ms`, the hide's times from the signal | 0 |
| The watcher dies or goes silent during the run | `status: presence_watch_lost` and the loss's reason; the tile is hidden and the run ends | 1 |
| The watch cannot be armed: the watcher reports no SteamVR or a headset already in standby, does not say ready in time, cannot be launched, or was changed between the record's check and its launch | `status: error`, `reason: presence_watch_not_armed`; `presence_watch.refusal` has the watcher's own word. Neither the bridge nor the helper was launched and nothing was shown | 1 |
| The headset comes off, the watcher is lost or the run is stopped while the helper is still starting, before the first frame | `cleanup.ok: false`: the helper was ended before it had started and its cleanup is not confirmed. Nothing was shown | 1 |

**The order, as it is accepted for now: the record is verified and used up
first, and only then is the watch armed. A run whose watch cannot be armed
has therefore spent its record**, and it shows nothing. That is so whatever
the cause: SteamVR not running, the headset asleep, the watcher changed
since the record's check. It is a run that ended, and a re-run is a new
approval with a new record.

So start the run only with SteamVR running and **the headset worn and
awake**. A headset nobody wears is the likely standby case: the watcher
then reports `headset_in_standby_at_start`, and that refusal uses up the
one-run record like any other. The wearer puts the headset on first; the
operator types the `yes` afterwards. A watcher file that is missing is
found before the record is consulted (`presence_watcher_not_found`), and
one that is not the file the manifest hashes is refused with the record
unspent; neither costs a record.

A preflight that costs no record: `camera_recognize`, the
[run without display](camera-recognize.md#the-presence-stop---presence-watcher),
arms the same watcher the same way and asks for no scope record. Run just
before the typed `yes` of the displayed run, with the headset already worn,
it shows whether the watch can be armed now:

```bash
.venv-replay/bin/python scripts/camera_recognize.py --allow-camera-input \
  --helper artifacts/camera-helper/sightcircuit-camera-helper \
  --presence-watcher artifacts/presence-watcher/sightcircuit-presence-watcher \
  --seconds 2 --max-frames 5 --fps 5 --cameras 0
```

Its report must say `presence_watch.armed: true`; otherwise
`presence_watch.refusal` has the watcher's word and no record was touched.
**It is a camera run of its own, not a free check**: it asks for its own
typed `yes`, reads the left camera for at most 2 seconds and shows nothing,
so it needs whatever approval a run without display needs, and nothing here
gives it. It proves the watch could be armed a moment earlier, not that it
will be: the headset must stay worn and awake until the displayed run.

### The removal sub-step

At the end of L-D4 the wearer removes the headset while the run is still
going, so the presence stop is seen on the displayed path. The operator
presses Enter for the `remove` mark once the headset is off, not before.
Two things can end the run on removal, and whichever comes first does: the
helper's own end (`reason: headset_removed` or `standby`) or the presence
watch's standby signal (`reason: standby`, `presence_watch.ended_run:
true`). The stage takes the tile down, and the report's `end` block gives
when the hide was written and when the bridge's hide returned; when the
watch ended the run, `presence_watch` also gives both times from the
signal. No mark is recorded after the watch's signal. Which of the two ends
a run on the Frame, and how long after the removal, is not known from the
cloud. The run is never resumed or retried. The wearer puts the
headset back on, confirms that no tile is shown, and the passthrough watch
resumes from then for 30 seconds.

## The same steps on the box panel

The box panel (`head-fixed-box-panel-v1`) is the second fixed layout: a
transparent 512 × 512 panel fixed in front of the head, on which a camera
run draws a box with a name around each detected object. Its steps are the
strip's steps with another layout in the record and on the command line.
Everything above applies unchanged: the preparation, one record per run,
the informed start, the passthrough watch, Ctrl-C, the removal sub-step and
the report. Nothing on the panel has run on the Frame either.

### The synthetic step (L-D3, no camera)

Staged by the Frame manager after the owner's approval, with the panel as
the layout and nothing else: the synthetic step has no view and no watch.

```bash
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/camera-hud/checkpoint-manifest.json &&
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D3 \
  --layout head-fixed-box-panel-v1 \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D3-panel.json
```

```bash
.venv-replay/bin/python scripts/camera_hud.py --synthetic-boxes --allow-hud-output \
  --scope-record artifacts/camera-hud/scope-L-D3-panel.json \
  --manifest artifacts/camera-hud/checkpoint-manifest.json \
  --seconds 90 --stops 3 --stop-seconds 3 \
  --report artifacts/camera-hud/l-d3-panel.json
```

The picture is five named boxes, drawn here and derived from nothing: one
in each corner and one in the centre, each named by where it is in the
picture. The corner boxes differ in size and shape so that a flipped or
turned panel is visible without reading: **the largest box, wider than
tall, is the picture's top left.** Until the first stop the picture does
not change; after it the centre box's name is the update's counter
(`count NNNN`), so a picture that has stopped updating can be told from one
that has not. The wearer says where the largest box is and how it lies,
whether the names read left to right, whether the room is visible between
and inside the boxes, whether the panel goes blank in each stop and returns,
and whether the counter advances.

The schedule, the stops and their bounds are the synthetic rows'
([step L-D3](#step-l-d3-synthetic-rows-row-g9)): 405 updates in four
segments, each stop observed as the bridge's own expiry. The run ends by
itself at its bound and on Ctrl-C exactly as `--synthetic-rows` does, and in
both cases the panel is taken down and the overlay retired. It holds no
presence watch, because nothing it shows came from a camera; the camera is
never opened and no detector is loaded. Expect
`report_kind: camera_hud_synthetic_boxes`, `status: completed`,
`complete: true` and exit 0. A record that names the strip does not run it,
nor does the panel's record run `--synthetic-rows`
(`scope_layout_mismatch`, the record unspent).

### A camera step (L-D4)

The record names the panel, the region of the frame, the rotation, the
alignment and the presence watch, and the run is given the same four
values. The rotation is a parameter in both commands, not a finding: E103
left open which quarter turn is upright for the left camera, so it may be
90 or 270 (or another), and the staged record and the run must agree on
it. The region below is the centre half of the frame; the alignment is the
neutral one.

```bash
ROTATION=90
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/camera-hud/checkpoint-manifest.json &&
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D4 \
  --layout head-fixed-box-panel-v1 --presence-watch required \
  --region 0.25,0.25,0.75,0.75 --rotation "$ROTATION" --alignment 1,1,0,0,0 \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D4-panel.json
```

```bash
ROTATION=90
.venv-replay/bin/python scripts/camera_hud.py --camera \
  --allow-camera-input --allow-hud-output \
  --scope-record artifacts/camera-hud/scope-L-D4-panel.json \
  --manifest artifacts/camera-hud/checkpoint-manifest.json \
  --step L-D4 --seconds 30 --fps 5 \
  --layout head-fixed-box-panel-v1 \
  --region 0.25,0.25,0.75,0.75 --rotation "$ROTATION" --alignment 1,1,0,0,0 \
  --target-label bottle \
  --mark-labels out_of_view,in_view,out_of_view,in_view,out_of_view,in_view,remove \
  --report artifacts/camera-hud/l-d4-panel.json
```

A run with another rotation, region or alignment than its record is refused
before anything is launched, with the record unspent
(`scope_rotation_mismatch`, `scope_region_mismatch`,
`scope_alignment_mismatch`). The panel takes no `--directions`,
`--names-only` or `--orientation`. The [presence watch](#the-presence-watch)
is held as on the strip. The informed start shows the panel's display
notice: a box sits on its object only while the head is still and trails
when the head turns, and it is taken down about one second after the camera
frame it came from unless a newer frame shows it again. L-D5 on the panel is
the same pair of commands with `--step L-D5`, its own file names and
`--seconds 90`; the numbers its acceptance asks for on the panel are not
settled here (work-order item B6).

Expect `report_kind: camera_hud_stage` with `config.hud_config_id:
head-fixed-box-panel-v1`, the `view` block (the region, the rotation and
where the region lay in the frame), the `boxes` block (what was selected,
drawn and sent) and `presence_watch.armed: true`.

## The report

The report goes to stdout. It is written to a file only where the operator
names a new `.json` path with `--report`, which is the operator's action to
save it. The path is checked before the question is asked: an existing file,
a missing directory or another suffix is refused at once, so it can never
cost a run, and nothing is ever overwritten. The rule for what is written:
a run that was declined, or refused before its record was consulted (a
missing helper or bridge, an unusable lock directory, a site's
preconditions), writes no file and leaves the named path free for the real
run; its report is on the terminal. Once the record has been consulted,
whether it was granted or refused by its own content, the report is written
to the named path. It holds counts, labels, timings and format metadata and is checked
against a schema that refuses anything else. It never holds a pixel, a
still, a preview or a calibration value. Labels and their display intervals
are Camera Data under the SDK agreement; the owner's consent of 2026-11-16
(decision 14) allows them to be shown to the wearer and saved as counts,
labels, boxes, timings and format metadata.

| Block | What it holds |
| --- | --- |
| the stage's blocks | Everything in the [stage report](../reference/camera-hud.md#the-report): the helper's ready and end records, every `frame_expired` record with its hide times, per-label intervals, the timings with their timebases, the cleanup verdicts |
| `command` | The mode, the exit code, `confirmation` (`operator_terminal`), the requested bounds, the detector, `refusal`, whether the report was saved |
| `scope_record` | Identifiers and format metadata only: the step, the source, the run id, the revision, the manifest digest, the deadline, the bound, the approval reference, whether it was granted and consumed, and the refusal; and what the record binds since its schema version 2: the layout the run asked for (`requested_layout`), the record's layout id and digest, the region as `{x0, y0, x1, y1}`, the rotation, the alignment's five numbers and whether the presence watch is required |
| `presence_watch` | A camera run's [presence watch](#the-presence-watch): whether it was armed, the watcher's refusal if not, whether its signal or its loss ended the run, the hide's two times from the signal, and whether the watcher was cleaned up. Identifiers, counts and times only |
| `marks`, `timebase` | The cues and key marks with monotonic and UTC times (the first 32; the count covers all) |
| `target` | Row G10's rule for `--target-label` |

The evidence entry uses the
[camera-HUD device report template](../templates/camera-hud-device-report.json).
It follows the stage's report and has no mask regions and no mapping. Its
`wearer_observations` and `operator_observations` are filled in by people:
whether the tile was seen, the counter advanced, the tile went blank at each
stop, the names were right, the arrows were within the agreed tolerance, the
name went within about 2 seconds of each turn away, the tile cleared on
removal, passthrough and tracking stayed normal during and for 30 seconds
after, any green input, and whether the white service LED was seen. Software
counts and human observations stay in separate fields.

## Reading the result

A step is accepted only on a headset report recorded as a numbered evidence
entry, distinct from cloud checks and CI results, with a verdict per
[acceptance row](../live-capture.md#edge-hud-acceptance-gates) that says what
stays unproven. A clean exit is not acceptance. A failed or stopped run stays
recorded and is investigated before any changed run is proposed. No result
here lifts a gate: that is a separate reviewed edit (item L13) after G7 to
G10 are accepted and the owner decides.
