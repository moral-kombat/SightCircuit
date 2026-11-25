# Show camera-derived names on the fixed HUD (`camera_hud`)

`camera_hud` is the fifteenth command (work-order item L9 of the
[camera display work order](../live-capture.md#camera-display-work-order-adopted-2026-11-16)).
It drives the three displayed device steps: L-D3 (synthetic rows, no camera),
L-D4 (first displayed camera labels) and L-D5 (the 60-second acceptance, row
G10).

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

## The three modes

| Mode | What it does | Record needed |
| --- | --- | --- |
| `--validate` | The helper's fake backend, a scripted detector and the validation bridge, then synthetic rows on the validation bridge with one stop. Nothing is displayed and no camera exists. This is what CI runs | none |
| `--synthetic-rows` | Step L-D3: one row, `count NNNN`, five times a second on the bridge's `--fixed-hud` mode, with scheduled stops in which no frame and no hide request is sent (the client's keepalive writes one show request per stop, which the bridge answers `no_image`) so that the bridge's own expiry is observed. The camera is never opened and no detector is loaded | source `synthetic`, step L-D3 |
| `--camera` | Steps L-D4 and L-D5: the left camera through the stage (detector, rows, tile, one observed frame per result), at most 90 s | source `arcturus_camera_left`, step L-D4 or L-D5 |

A device mode has no option to name another bridge, helper, camera or source.
It launches only the two files the checkpoint manifest hashes:
`artifacts/overlay-bridge/sightcircuit-overlay-bridge` and
`artifacts/camera-helper/sightcircuit-camera-helper`. The left camera
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
   pinned by the manifest of step 4 since its schema version 4, whether or
   not the step's record requires the watch. The SDK directory `DIR` is the
   operator's own copy, outside the checkout, as in the
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
layout takes one, and whether the presence watch is required
(`--presence-watch required` or `none`, which has no default either;
[the reference](../reference/camera-hud.md#the-one-run-scope-record) lists
them). Any rebuild, edit or new commit after staging makes the record
refuse; so do a second run, a run for another step, a run with another
layout or view than the record names, a clock that reads earlier than the
record's creation, and a start after the deadline. A record written before
schema version 2 is refused by name. `camera_hud --camera` takes the same
four things as options (`--layout`, `--region`, `--rotation`,
`--alignment`; [below](#the-layout-and-the-view)) and runs only under a
record that names exactly what it was asked to show. It holds no presence
watch yet, so it runs only under a record staged with `--presence-watch
none`; the watch on the displayed path, and with it the panel's steps, is
the last part of work-order item L17.

A camera step's record needs one option more than L-D3's: whether the
presence watch is required, which has no default. For L-D4 as `camera_hud`
runs it today (the same for L-D5 with its own step and file names):

```bash
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/camera-hud/checkpoint-manifest.json &&
.venv-replay/bin/python scripts/stage_scope_record.py --step L-D4 \
  --layout fixed-edge-hud-v1 --presence-watch none \
  --approval OWNER-APPROVAL-IDENTIFIER --valid-minutes 60 \
  --checkpoint-manifest artifacts/camera-hud/checkpoint-manifest.json \
  --out artifacts/camera-hud/scope-L-D4.json
```

Without `--region` and `--rotation` the record states the whole frame,
unturned. A record staged with a region, a rotation or the panel's layout
(which also needs `--alignment`) serves only a run given the same options;
any difference is refused with the record unspent, because the run would
not do what the record says. A record staged with `--presence-watch
required` is a valid record that today's `camera_hud` refuses, unspent, for
the same reason: it holds no watch.

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
the display notice for `--camera`, the synthetic-rows notice for
`--synthetic-rows`. A pipe, a flag or any other answer starts nothing.

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
record that names another region, rotation or presence-watch requirement
ends `reason: fixed_hud_output_not_approved` with `scope_region_mismatch`,
`scope_rotation_mismatch` or `scope_presence_watch_mismatch`. Read
`command.refusal` first.

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
- `--region X0,Y0,X1,Y1` gives the detector that rectangle of the frame, as
  fractions; `--rotation` (0, 90, 180 or 270) turns it by quarter turns
  first. They are [`camera_recognize`'s options](camera-recognize.md#a-region-of-view)
  and mean the same. Both are arguments, not findings: which rotation is
  upright for the left camera is not established, so no value here is a
  default to copy.
- `--alignment SX,SY,OX,OY,TURN` is the panel's placement of the boxes: the
  project's own five numbers (scale, offset in panel fractions, quarter
  turn), never the SDK's calibration. It has no default; the neutral value
  is `1,1,0,0,0`.

No command block for a camera run on the panel is given here yet. Such a
run is to be staged with the presence watch required, and the watch on the
displayed path is not built; the staging command and the run command for
each of the panel's steps are added, tested against the tools, with it.
`--validate` runs the panel through a region on the fake path in the
meantime (`camera_panel` in its report).

Expect `report_kind: camera_hud_stage`. A run that ends on its bound is
`status: completed` (reason `output_bound` when the client's own bound ended
it); a run ended by the headset's removal is
`status: presence_ended` with reason `headset_removed` or `standby`. Both
exit 0 when `cleanup.ok` is true. `cancelled` exits 130; everything else
exits 1.

### The removal sub-step

At the end of L-D4 the wearer removes the headset while the run is still
going, so the presence stop is seen on the displayed path. The operator
presses Enter for the `remove` mark once the headset is off, not before. The
helper ends the run on removal; the stage takes the tile down, and the
report's `end` block gives when the hide was written and when the bridge's
hide returned. The run is never resumed or retried. The wearer puts the
headset back on, confirms that no tile is shown, and the passthrough watch
resumes from then for 30 seconds.

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
| `scope_record` | Identifiers only: the step, the source, the run id, the revision, the manifest digest, the deadline, the bound, the approval reference, whether it was granted and consumed, and the refusal |
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
