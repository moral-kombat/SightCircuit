# Prepare a bounded HUD calibration check

This is the canonical operator procedure for observing a fixed calibration
marker in the SteamVR mirror. It requires the prerequisites and specific
device-run authorization described in the [current plan](../live-capture.md).
The [qualification design](../hud-qualification.md) explains what the result
can establish; the [development reference](../reference/hud-development.md)
covers offline checks and software identity.

A historical attempt failed ([E75](../evidence/e075.md)). Do not repeat it
unchanged. A clean new observation would still be **unqualified** and would
not enable production HUD input or output.

<a id="next-device-checkpoint"></a>

## Bounded edge HUD calibration checkpoint

This is the proposed device step for WP9 in the
[POC scope](../poc-scope.md#work-packages). Recovered [E75](../evidence/e075.md)
establishes that a historical five-second V4 marker/observer attempt ran on
Frame and was rejected; G3 acceptance is still pending. Do not repeat that
unchanged attempt. Resolve G2's physical failure/recovery prerequisites and
review a revised bounded checkpoint before seeking its own approval under the
[current plan](../live-capture.md). This page is
the procedure, not that approval.

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
- The checkout is clean at the approved revision, `.venv-replay` is set up,
  and the bridge is built on Frame with `bash scripts/build_overlay_bridge.sh`.
- `/dev/video99` is still the verified SteamVR mirror candidate
  ([mirror check](mirror-recognition.md#check-an-existing-steamvr-mirror)).
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

The read-only `--verify` step stops the run before the mirror opens if
anything differs from the manifest: a changed module or script, a rebuilt
bridge, a dirty tree, or another interpreter, architecture or package. Keep
the directory, with its `manifest-verify.json`, and do not run the observer.

During the run, the wearer keeps passthrough visible and looks at the scene
for the first two seconds. A completely unchanging view can end the run with
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
| `1`, `rejected`, `native_failed` or `cleanup_failed` | Observation or cleanup failed; `reason` names the cause | Keep the report; do not repeat the unchanged run |
| `130`, `cancelled` | Stopped by signal | Keep the report; note why it was stopped |

Native cleanup fields in the report are process and API results. They do not
show that the headset recovered. Ask the wearer, and record their answers as
user-reported:

- Was the marker tile visible in the headset, and where?
- Did passthrough and tracking stay normal during the run and for 30 seconds
  afterward, without switching the camera source?
- Did the Arcturus input turn green at any point
  ([E59](../status.md#e59-arcturus-green-input-recurrence-and-operator-recovery))?

If passthrough or tracking regresses, stop and keep the report. Switching to
native passthrough and back is a recovery workaround, not a pass. Hand the
saved directory and the answers to the evidence coordinator for a numbered
entry ([evidence maintenance](../evidence/README.md#maintaining-evidence)). A clean result
supports WP11 (feedback exclusion) planning. It does not enable live HUD input
or production HUD output.


## Measure the observer's scene precondition on saved stills

**File only; needs the owner's approval before it runs on Frame.** Pending E83
in [Framejacker PR #23](https://github.com/moral-kombat/framejacker/pull/23)
relays that the November 3 observer runs stopped at `observer_contour_budget`
before showing a marker. This report is not merged evidence in this checkout;
see the [scene-precondition reference](../reference/hud-observation.md#observer-scene-precondition-2026-11-04).
To see what real Frame scenes look like to that precondition, run the
statistics on the private PNG stills already in `evaluation/local/` (written by
`mirror_recognize.py --still`). This reads those files only: it opens no
device, mirror or bridge and makes no VR call.

```bash
cd "$HOME/sightcircuit"
mkdir -p artifacts/scene-analysis
shopt -s nullglob
stills=(evaluation/local/*.png)
[ ${#stills[@]} -gt 0 ] || echo "No PNG stills in evaluation/local/: nothing to analyze"
for still in "${stills[@]}"; do
  .venv-replay/bin/python scripts/hud_marker_observer.py --analyze-still "$still" \
    > "artifacts/scene-analysis/$(basename "$still" .png).json"
  echo "$still: exit $?"
done
```

Each report holds counts, the still's SHA-256 and its size, never pixels or its
path. Exit 0 means analyzed (even when the scene would be rejected), 1 means the
file was refused. Keep the reports with the run notes; they feed the
replacement precondition (package O3), not any gate.
