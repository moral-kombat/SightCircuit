# Measure the fallback's device terms with the calibration marker

This is the operator procedure for work-order step F3, the device measurements
of the [fallback input design](../reference/hud-fallback.md#device-evidence-needed):
the hide-to-absence latency B, the hidden-event delivery, the loopback's repeat
behaviour and the show-to-visible latency. The tool is
`scripts/fallback_measure.py` (work item F3b, 2026-11-08), built and
cloud-checked only; **no device run has happened**. The device run needs its
own approval; the bridge must be rebuilt on the Frame first (the marker mode
accepts the visibility request only since F3a, and the Frame's built bridge
predates E1). This page is the procedure, not that approval.

What it does: the command verifies the checkpoint manifest, then starts the
native bridge in its **marker mode**, the mode the device has been approved to
show (the fixed, opaque, head-relative 320 × 128 calibration tile of the
[HUD checkpoint](hud-checkpoint.md)), and reads the verified mirror at the
capture thread's ten reads per second. Once the tile is seen in the mirror it
runs N hide and show cycles (default 5, at most 20), each a hide request and a
show request through F1's visibility opcode, no closer than 500 ms apart. For
every read it records the time the read returned, a full-frame digest and
whether `locate_copies` found a complete marker copy; for every request it
records the bridge's `request_ns`, `returned_ns` and the confirming event's
`observed_ns`, or the missing line. The report is counts, nanoseconds and
digests, with one section per measurement; no image is saved and nothing is
inferred.

What it does not do: no HUD output through the stream mode, no live input, no
mapping, no gate change. The three software gates stay closed and the report
says so (`production_hud_enabled`, `production_live_input_enabled` and
`mapping_qualified` are `false`). It does not decide the bound B: it produces
the samples the [fallback reference](../reference/hud-fallback.md#device-evidence-needed)
asks for, and the bound is set in a reviewed step from them.

## One clock, one host

Every timestamp in the report is `CLOCK_MONOTONIC` nanoseconds: the bridge
reads it with `monotonic_ns()` and the mirror source stamps each read's return
with `time.monotonic_ns()`. The comparison between them is valid only while
both processes run in the same time namespace with the same offset, the
[`timebase_id`](../reference/hud-session.md#timebase_id) rule of the session
tokens. The command cannot see a namespace difference; the run record must
state that the bridge and the command ran in one session on the Frame, as the
checkpoint runs do.

## Before the device run

Run the offline checks on the reviewed revision, in the cloud or on Frame:

```bash
cd "$HOME/sightcircuit"
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest \
  tests.test_fallback_measure tests.test_overlay_bridge tests.test_hud_marker_check \
  tests.test_visibility_cycle tests.test_checkpoint_manifest
```

On Frame, confirm all of the following, as for the
[checkpoint](hud-checkpoint.md#before-the-device-run):

- [Independent SSH](../on-device.md#verify-independent-ssh-access) from the PC works.
- The checkout is clean at the approved revision, `.venv-replay` is set up,
  and the bridge is **rebuilt on Frame** from the F3a sources with
  `bash scripts/build_overlay_bridge.sh`: an older bridge ends every request
  silently ignored (its marker mode read no stdin), so the run would record
  nothing but missing lines.
- The checkpoint manifest is written **after** that rebuild and verifies
  (`"status": "verified"`); the command verifies it again itself and refuses a
  dirty tree, a changed module or script, a rebuilt bridge or another
  interpreter before the bridge or the mirror opens.
- `/dev/video99` is still the verified SteamVR mirror candidate
  ([mirror check](mirror-recognition.md#check-an-existing-steamvr-mirror)).
- No replay window, VR panel, mirror capture, observer or other overlay is
  running.
- Someone is wearing the headset, with normal passthrough and tracking, ready
  to hold still: the tile must be the only change in the mirror while the
  cycles run.

Then run the validation mode, which initialises no VR and reads no device: the
same cycles against the bridge's validation marker, where every hide ends
`visibility_event_missing` and every show returns `no_image` because nothing is
ever presented:

```bash
cd "$HOME/sightcircuit"
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/hud-calibration/checkpoint-manifest.json &&
  .venv-replay/bin/python scripts/fallback_measure.py --validate --seconds 6 --cycles 2
```

Expect `"status": "measured"`, `"exit_code": 0`, `"cleanup_ok": true`,
`hidden_event_delivery.events_missing` equal to the cycles, the three mirror
sections `"measured": false`, and `visibility_requests_received` twice the
cycles in the bridge's report. Stop here if anything else is reported.

## Approved run

Only with approval for this run; it is a separate device step and **needs its
own approval**. The mode flag, both explicit opt-in flags, `--device` and
`--manifest` are all required; the tool refuses a partial opt-in. Use the same
graphical session environment as the checkpoint runs.

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  test -z "$(git status --porcelain)"
  test -x artifacts/overlay-bridge/sightcircuit-overlay-bridge
  mkdir -p artifacts/fallback-measurement
  run="$(mktemp -d "$PWD/artifacts/fallback-measurement/run-XXXXXX")"
  git rev-parse HEAD > "$run/revision.txt"
  cp artifacts/hud-calibration/checkpoint-manifest.json "$run/"
  set +e
  .venv-replay/bin/python scripts/fallback_measure.py --measure-fallback \
    --allow-mirror-observation --allow-calibration-output \
    --device /dev/video99 --manifest artifacts/hud-calibration/checkpoint-manifest.json \
    --seconds 20 --cycles 5 > "$run/measurement.json"
  echo "$?" > "$run/exit-code.txt"
  echo "Saved $run"
)
```

What the command passes, and why:

- `--seconds 20`, the maximum. The bridge's display timer starts when its
  process starts and its connect may use up to 6 s; the marker then has up to
  2 s to appear in the mirror (`marker_not_visible_in_mirror` otherwise, the
  observer's warm-up). Five cycles at the pacing floor need about 5 s; a slow
  transition stretches a phase to at most 2 s (`phase_bound_ns`), after which
  it is recorded as not observed within the bound.
- The deadline margins, derived and pinned by `tests/test_fallback_measure.py`.
  A show request that the bridge processes inside its final 10 ms poll window
  at the display deadline ends the run `calibration_deadline_before_show`
  (exit 1) instead of a missing line, so no request is sent later than
  `last_request_margin_ns` = 430 ms before the deadline: the client's 320 ms
  terminal deadline, one 10 ms marker poll and a 100 ms margin for the pipe
  write, the call and scheduling. A cycle starts only with
  `cycle_reserve_ns` = 2.43 s of display left (the hide phase's 2 s bound plus
  that margin), so its show is always sent in time; a request that would still
  enter the margin is refused and the run rejected
  `request_inside_display_deadline_margin`, never sent. The marker bridge's
  10 ms stdin poll also means a missing line arrives within 10 ms of the 250 ms
  bound, inside the 20 ms stream poll the F2 320 ms deadline already allows
  for, so that deadline holds for the marker client as for the sink.
- `--cycles 5`, the default: enough samples for a first distribution, few
  enough to complete inside the timer. `cycles_not_started` in the report
  counts the cycles the timer left no room for; a run with any is
  `measurement_incomplete`, exit `1`, and its samples are still kept.
- `--manifest`: the read-only verification runs first. On any difference the
  report is `manifest_not_verified` and nothing starts; keep the directory.

During the run the wearer keeps passthrough visible and holds still from the
moment the tile appears until the command returns: the hide-to-absence bound
is read from frame changes, and a moving scene changes every frame whether or
not the tile is there (the report still records the digests, and
`loopback_repeats.identical_consecutive_digests` says how often two reads were
the same bytes). Ctrl-C (or SIGTERM over SSH) stops the command; the stop is
reported as `cancelled` with the bridge closed.

## Reading the result

| Exit code and status | Meaning | Next |
| --- | --- | --- |
| `0`, `measured` | Every requested cycle ran; the four sections hold the samples | Record one evidence entry per section (below) |
| `1`, `measurement_incomplete` | The display timer left no room for every cycle; the completed cycles are kept | Record what was measured; a longer run is its own step |
| `1`, `manifest_not_verified` | The checkout, bridge, interpreter or packages differ from the manifest | Fix the preparation; nothing ran |
| `1`, `rejected`, `marker_not_visible_in_mirror` | The tile never appeared in the mirror within the warm-up | The fallback's first question is answered in the negative for this run; record it |
| `1`, `rejected`, other reasons; `cleanup_failed` | A preparation or capture stop (`native_marker_bridge_not_built`, `vrserver_not_running:*`, a format change), or cleanup failed | Keep the report; `reason` names the cause |
| `130`, `cancelled` | Stopped by signal | Keep the report; note why |

The four sections and the evidence entry each supports:

| Section | Holds | Evidence row |
| --- | --- | --- |
| `hide_to_absence` | Per cycle: the hide's `request_ns`, `returned_ns` and `event_observed_ns`; the last read with a complete copy and the first without one; the bounds relative to the request (`lower_bound_after_request_ns`, `upper_bound_after_request_ns`) and to the hidden event (`upper_bound_after_event_ns`); `maximum_upper_bound_after_request_ns` over the run | Hide-to-absence latency (B): the largest upper bound plus a margin, after review |
| `hidden_event_delivery` | Per cycle: the terminal line (`overlay_hidden` or `visibility_event_missing`), `event_after_returned_ns`, `waited_ns`; `events_delivered`, `events_missing`, the maximum delivery | Hidden-event delivery: whether the confirmed hide can rest on the event, and its bound |
| `loopback_repeats` | Every read's `observed_ns`, digest and presence (`reads_detail`), `identical_consecutive_digests`, `longest_repeat_run`, `distinct_digests`, `maximum_read_gap_ns`, `observed_read_rate_hz`; the capture report's 32 px sample counts beside it | Loopback repeat behaviour: whether windows can starve; the writer's effective rate |
| `show_to_visible` | The symmetric bounds of each show (last read without a copy, first with one) and the shown event's delivery (`shown_event_delivery`) | Show-to-visible latency, for the duty-cycle table |

Native cleanup fields are process and API results, not headset recovery. Ask
the wearer and record the answers apart from the measurements, as every device
record does: was the tile visible, and did it blink with the cycles; did
passthrough and tracking stay normal during the run and for 30 seconds after;
did the Arcturus input turn green
([E59](../status.md#e59-arcturus-green-input-recurrence-and-operator-recovery)).
If passthrough or tracking regresses, stop and keep the report; follow the
reviewed [E79 recovery procedure](../evidence/e079.md#recovery-procedure-and-rehearsal).
Hand the saved directory and the answers to the evidence coordinator for the
numbered entries ([evidence maintenance](../evidence/README.md#maintaining-evidence)).
A clean result sets the bound B for step F4; it does not enable the fallback
run (F7), live HUD input or production HUD output.
