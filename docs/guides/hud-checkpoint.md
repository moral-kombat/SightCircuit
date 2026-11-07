# Prepare a bounded HUD calibration check

This is the canonical operator procedure for observing a fixed calibration
marker in the SteamVR mirror. It requires the prerequisites and specific
device-run authorization described in the [current plan](../live-capture.md).
The [qualification design](../hud-qualification.md) explains what the result
can establish; the [development reference](../reference/hud-development.md)
covers offline checks and software identity.

The latest approved attempt and retry stopped at the scene precondition before
showing a marker ([E83](../evidence/e083.md)). A further attempt needs a revised
precondition reviewed offline and new approval; do not repeat it unchanged.
A clean new observation would still be **unqualified** and would not enable
production HUD input or output.

<a id="next-device-checkpoint"></a>

## Bounded edge HUD calibration checkpoint

This is the operator procedure for the marker-observation checkpoint (G3).
[E79](../evidence/e079.md) closes the recovery prerequisite (G2) under the owner
decision: the owner rehearsed switching to grey tracking-camera passthrough
and back, with normal tracking and passthrough afterward. The green-input
cause remains unknown, and that clean rehearsal does not prove a cure.

The later [E83](../evidence/e083.md) runs stopped with `observer_contour_budget`
before bridge initialization or marker output. This is distinct from the
historical V4 tag/bit rejection in [E75](../evidence/e075.md). G3 remains open.
Review the revised scene precondition and bounded checkpoint under the
[current plan](../live-capture.md) before obtaining approval for a new run.
This page is the procedure, not that approval.

What it does: `scripts/hud_marker_observer.py` reads the verified mirror at
native resolution, at most 5 reads per second for up to 20 seconds. It first
waits up to two seconds for two distinct marker-free frames. It then starts
`scripts/hud_marker_check.py`'s marker through the native bridge: a fixed,
opaque, head-relative 320 × 128 tile with four corner tags, matching the HUD
layout. The observer finds every complete tagged copy in the mirror and reports
its native corners, the union of the copies plus the configured margin, and the
largest corner drift while the wearer moves.

What it does not do: no inference, no saved images, no production HUD output
and no live HUD input. A clean run ends with status `observed_unqualified` and
exit code `2`. That is the expected result, not a failure. It does **not**
qualify the mapping. The observer cannot assign eyes, establish how the copies
are composited, rule out unseen or occluded copies, or prove physical expiry,
so live HUD input stays rejected after this check.

### Before the device run

Run the offline checks on the reviewed revision, in the cloud or on Frame:

```bash
cd "$HOME/sightcircuit"
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest \
  tests.test_hud_marker_check tests.test_hud_marker_observer tests.test_hud_mask \
  tests.test_checkpoint_manifest
```

On Frame, confirm all of the following:

- [Independent SSH](../on-device.md#verify-independent-ssh-access) from the PC works, so the
  run can be stopped and inspected without the headset UI.
- The revised scene precondition has passed its offline review and diagnostics;
  the unchanged E83 attempt is not being repeated.
- The checkout is clean at the approved revision, `.venv-replay` is set up,
  and the bridge is built on Frame with `bash scripts/build_overlay_bridge.sh`.
  The observer checks the build first and stops `native_marker_bridge_not_built`
  before it reads /proc or opens the mirror.
- `/dev/video99` is still the verified SteamVR mirror candidate
  ([mirror check](mirror-recognition.md#check-an-existing-steamvr-mirror)).
- The run record states the environment differences against the
  [E83](../evidence/e083.md) attempts: OS build, kernel release, SteamVR
  package version and interpreter ([E85](../evidence/e085.md) lists the
  2026-11-07 changes). The manifest written below records the kernel, the OS
  release and the SteamVR package identity; the interpreter and packages are
  pinned by the same manifest.
- No replay window, VR panel, mirror capture or other overlay is running.
- Someone is wearing the headset, with normal passthrough and tracking.
  Optionally start the [observation page](frame-sessions.md#record-headset-observations-for-a-run)
  for their answers.

Right after the bridge build, pin the checkout, interpreter, packages and
bridge with the [checkpoint artifact manifest](../reference/hud-development.md#checkpoint-manifest-identity-and-verification).
Write it on Frame; built binaries are never transferred:

```bash
cd "$HOME/sightcircuit"
bash scripts/build_overlay_bridge.sh
mkdir -p artifacts/hud-calibration
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --write artifacts/hud-calibration/checkpoint-manifest.json
```

Expect `"status": "written"`, `"dirty": false` and empty `problems`. It refuses
a dirty tree and never overwrites an earlier manifest; move that aside first.

Then verify the manifest (read-only) and run the marker's validation mode on
Frame. The validation uses the generated marker and native timer only, and
never initializes VR. `&&` stops before the bridge runs if anything differs
from the manifest:

```bash
cd "$HOME/sightcircuit"
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/hud-calibration/checkpoint-manifest.json &&
  .venv-replay/bin/python scripts/hud_marker_check.py --validate --seconds 3
```

Expect `"status": "verified"` from the manifest check, then
`"status": "completed"`, `"exit_code": 0` and `"cleanup_ok": true` from the
validation. Stop here if either reports anything else.

### Approved run

Only with approval for this run. The mode flag, both explicit opt-in flags, and `--device` are
required; the tools refuse a partial opt-in. Over SSH, use the same graphical
session environment as the overlay stage in
[E55](../status.md#e55-annotated-replay-overlay-command-on-frame), which ran
attached to the active `:0` display.

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  test -z "$(git status --porcelain)"
  test -x artifacts/overlay-bridge/sightcircuit-overlay-bridge
  mkdir -p artifacts/hud-calibration
  run="$(mktemp -d "$PWD/artifacts/hud-calibration/run-XXXXXX")"
  git rev-parse HEAD > "$run/revision.txt"
  cp artifacts/hud-calibration/checkpoint-manifest.json "$run/"
  .venv-replay/bin/python scripts/checkpoint_manifest.py \
    --verify artifacts/hud-calibration/checkpoint-manifest.json \
    > "$run/manifest-verify.json" || { echo "Manifest differs; see $run"; exit 1; }
  set +e
  .venv-replay/bin/python scripts/hud_marker_observer.py --observe-calibration \
    --allow-mirror-observation --allow-calibration-output \
    --device /dev/video99 --seconds 20 > "$run/observer.json"
  echo "$?" > "$run/exit-code.txt"
  echo "Saved $run"
)
```

What the command above passes, and why
([pre-attempt review, 2026-11-07](../reference/hud-observation.md#pre-attempt-review-of-the-stops-before-a-marker-2026-11-07)):

- `--seconds 20`, the maximum. The bridge's display timer starts when its
  process starts and its connect may use up to 6 s before the marker is
  generated, so a shorter run shortens the marker's display or ends
  `calibration_deadline_before_marker`.
- No `--record-session-tokens` (the default). Its report could not become a
  mapping candidate before the tracking reporter exists, and its /proc scan and
  binary hash add a 4 s budget, unmeasured on Frame, that can stop the run
  before the marker. The report then says `session_tokens_not_requested`.
- No `--bridge`: the default is the bridge the manifest pins.

The read-only `--verify` step stops the run before the mirror opens if
anything differs from the manifest: a changed module or script, a rebuilt
bridge, a dirty tree, or another interpreter, architecture or package. Keep
the directory, with its `manifest-verify.json`, and do not run the observer.

During the run, the wearer keeps passthrough visible and looks at the scene
for the first two seconds. The bridge then connects, which may take up to 6 s,
and the observer waits up to 8 s for its ready line; after that line the
observer allows 2 s (`MARKER_WARMUP_SECONDS`) for the tile to reach the mirror
before a run without any marker candidate is rejected `no_complete_marker`. A completely unchanging view can end the run with
`baseline_distinct_clear_frames_not_established`. Once the marker tile appears,
they turn their head slowly through a small range and keep the tile in view.
Large movements, or a change in how many copies are visible, end the run
rejected instead of widening the footprint. After a rejection the marker tile
stays until its own timer ends, at most `--seconds`, because the marker bridge
does not read a stop request. Ctrl-C (or SIGTERM over SSH) stops both tools at
once; that stop is reported as forced, with `cleanup_ok: false`.

### Reading the result

| Exit code and status | Meaning | Next |
| --- | --- | --- |
| `2`, `observed_unqualified` | Clean run: copies observed, marker closed cleanly | Record `observed_copy_count`, `footprint_union_native`, `margin_px`, `maximum_corner_drift_px`, `sample_count` and the bridge cleanup fields |
| `1`, `rejected` with `native_marker_bridge_not_built`, a failed manifest verification, `headset_not_connected`, `watchdog_expired:connect`, `marker_ready_timeout`, or a vrserver session stop (`vrserver_not_running:*`, `vrserver_lost_before_overlay_connect:*`, `overlay_connect_failed:*`) | A stop caused by connectivity or preparation: the observer never judged the scene and no marker tile appeared. It does not count against the attempt budget, whatever its position in the run ([counting rule](../poc-scope.md#architecture-decisions-2026-11-07)) | Keep the report; fix the preparation and re-verify the manifest. Any further run is its own device step under the owner's approval |
| `1`, `rejected`, `native_failed` or `cleanup_failed`, other reasons | Observation or cleanup failed; `reason` names the cause. The attempt counts against the budget when the observer judged the scene: a rejection at the scene precondition, and anything after the marker tile appears | Keep the report; do not repeat the unchanged run |
| `130`, `cancelled` | Stopped by signal | Keep the report; note why it was stopped |

Native cleanup fields in the report are process and API results. They do not
show that the headset recovered. Ask the wearer, and record their answers as
user-reported:

- Was the marker tile visible in the headset, and where?
- Did passthrough and tracking stay normal during the run and for 30 seconds
  afterward, without switching the camera source?
- Did the Arcturus input turn green at any point
  ([E59](../status.md#e59-arcturus-green-input-recurrence-and-operator-recovery))?

If passthrough or tracking regresses, stop and keep the report. Follow the
reviewed [E79 recovery procedure](../evidence/e079.md#recovery-procedure-and-rehearsal)
within the approved run scope; recovery does not make the failed run a pass. Hand the
saved directory and the answers to the evidence coordinator for a numbered
entry ([evidence maintenance](../evidence/README.md#maintaining-evidence)). A clean result
supports WP11 (feedback exclusion) planning. It does not enable live HUD input
or production HUD output.


## Measure the observer's scene precondition on saved stills

**Cloud-tested only; file only; a person runs it on Frame (or a session the
owner has approved for it), with the owner's approval.** The 2026-11-03 observer
runs stopped at the old `observer_contour_budget` before showing a marker
([why](../reference/hud-observation.md#observer-scene-precondition-2026-11-04)); package
O3 replaced that budget with the
[O3 rule](../reference/hud-observation.md#the-o3-rule-2026-11-06), whose Frame
classifications are bounds from relayed aggregates. To see what real Frame
scenes look like to the precondition, run the statistics on
the private PNG stills already in `evaluation/local/` (written by
`mirror_recognize.py --still`). This reads those files only: it opens no
device, mirror or bridge and makes no VR call.

```bash
(
  set -u
  cd "$HOME/sightcircuit"
  out="artifacts/scene-analysis/$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$out"
  git rev-parse HEAD > "$out/revision.txt"
  .venv-replay/bin/python -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)" > "$out/versions.txt"
  shopt -s nullglob
  stills=(evaluation/local/*.png)
  [ ${#stills[@]} -gt 0 ] || echo "No PNG stills in evaluation/local/: nothing to analyze"
  for still in "${stills[@]}"; do
    .venv-replay/bin/python scripts/hud_marker_observer.py --analyze-still "$still" \
      > "$out/$(basename "$still" .png).json"
    echo "$still: exit $?"
  done
)
```

Each report holds counts, the still's SHA-256 and its size, never pixels or its
path. Exit 0 means analyzed (even when the scene would be rejected), 1 means the
file was refused. A report counts as a native mirror read only when its
`source.sha256`, `source.width` and `source.height` match the still's capture
report (`still_sha256`, `still_width` and `still_height` in the
`still-<id>.json` that `frame_session.sh` keeps) or its `sha256` in
`evaluation/local/frame.json`. The report itself always says
`provenance_verified: false`: a resized or other PNG is not detected. Other
images, such as the grey 640 × 360 scene check, are not native mirror reads.
Send the reports, with `revision.txt` and `versions.txt`, to the evidence
coordinator as a Frame-run file analysis, kept apart from user reports and from
mirror or device checks. They feed the precondition's derivation, not any gate.
