# Recognize objects from the SteamVR mirror

Use this guide to discover the existing SteamVR mirror, run bounded recognition,
and collect stills for detector comparison. Commands run on Frame after
[device access and inventory](../on-device.md). Recognition needs the
[replay/window environment](replay.md); discovery needs only Python's standard library.

The mirror includes the VR panel. Close replay panels, result windows, and saved
preview tabs before recognition. These commands show results only after capture
ends. Continuous HUD output has separate [acceptance requirements](../live-capture.md).

[Verified results](../status.md) · [Implementation and reports](../reference/mirror.md) ·
[Earlier detailed observations](../history/mirror-procedures.md)

## Check an existing SteamVR mirror

Keep normal passthrough running. Discover candidates without opening a video device:

```bash
cd "$HOME/sightcircuit"
python3 scripts/mirror_check.py
```

Expect `status: discovery_only`, exit 0. Inspect `discovery.status` and
`discovery.candidates`: exit 0 alone does not mean a source exists. A valid
candidate has the exact sysfs name `SteamVR`, a virtual-video path, and readable
identity metadata. The historical node was `/dev/video99`; verify the current
candidate instead of assuming that number is stable.

If exactly one verified candidate exists, acquire ten seconds of input:

```bash
python3 scripts/mirror_check.py --capture --seconds 10
```

For multiple candidates, add `--device` with the exact verified node. The reader
opens the existing mirror read-only and checks its driver and current format.
It does not change formats, start a producer, or fall back to a physical camera.
Unsupported input is an explicit result.

To retain one grayscale preview after acquisition closes:

```bash
mkdir -p artifacts/mirror-check
python3 scripts/mirror_check.py --capture --seconds 10 \
  --preview artifacts/mirror-check/new-preview.png
xdg-open "file://$HOME/sightcircuit/artifacts/mirror-check/new-preview.png"
```

Choose a new output path on each run; existing files are preserved. Without
`--preview`, no image is saved. The preview fits within 640 × 640 and is
intentionally grayscale. Check that it shows a recognizable physical scene,
its orientation, and whether passthrough/tracking stayed normal during and after.
If opening the image fails, fix the viewer/path before acquiring again.

The report gives format, read counts, timing, and cleanup. Changed samples
alone do not prove a fresh physical-camera image. The reader has no producer
sequence or exposure timestamp; physical camera identity remains unverified.
Reads are capped at 10 per second. The worker budget is the requested duration
plus three seconds, followed by bounded cleanup.

| Exit code | Meaning |
| --- | --- |
| `0` | Discovery completed, or capture delivered frames with normal cleanup; inspect status |
| `1` | Unavailable input, unsupported format, no frames, timeout, or another error |
| `2` | Invalid options; capture did not start |
| `130` / `143` | Cancelled by Ctrl+C / SIGTERM |

Keep failed reports. Forced termination sets `cleanup_ok: false`; process exit
alone does not establish normal headset behavior.

## Run recognition on the saved mirror image

Use an existing preview after capture has stopped. This opens no camera or VR
panel; it writes a screenshot of the annotated application offscreen:

```bash
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
  artifacts/mirror-check/new-preview.png --max-frames 1 \
  --preview artifacts/mirror-check/scene-detections.png \
  --report artifacts/mirror-check/scene-detections.json
xdg-open "file://$HOME/sightcircuit/artifacts/mirror-check/scene-detections.png"
```

Use new output names. Expect `status: finished`, one processed/presented frame,
`overlay_requested: false`, and `images_saved: true`. Empty detections can be a
valid result. The default MobileNet-SSD has 20 classes and no rug class; the
historical rug-as-sofa error is [E15](../evidence/e015.md). Confidence percentages
are model scores, not calibrated probabilities. Replicating grayscale into
three channels does not recover source color.

## Run bounded recognition from the mirror

After verifying that `/dev/video99` is still the SteamVR candidate, aim at a
supported object such as a chair or TV and keep it visible through the final
seconds. This uses the existing detector environment and shows no live output:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  mkdir -p artifacts/poc
  sightcircuit_demo_dir="$(mktemp -d "$PWD/artifacts/poc/live-XXXXXX")"
  .venv-replay/bin/python scripts/mirror_recognize.py --capture \
    --device /dev/video99 --seconds 10 --max-fps 5 \
    --preview "$sightcircuit_demo_dir/detections.png"
  xdg-open "file://$sightcircuit_demo_dir/detections.png"
)
```

The recognizer accepts RGB3/BGR3, loads the checked model before opening the
source, and keeps one latest pending input. No flags means discovery only.
Without `--preview`, no image is saved. The explicit PNG is the last processed
image with annotations, fitted within 640 × 640 and published only after normal
capture and worker cleanup.

Expect `report_kind: steamvr_mirror_recognition`, `status: recognized`, exit 0,
`cleanup_ok: true`, positive `frames_processed`, `display_started: false`, and
`overlay_requested: false`. Inspect the final image against the real objects.
`labels_seen` covers the whole run; `last_result` and the PNG describe only its
final processed frame. An empty final image can be valid even when earlier
frames had labels. [E48](../evidence/e048.md) records one confirmed demonstration,
not general class or localization accuracy.

Inputs older than one second are skipped; stale final input produces no preview.
The worker budget adds 15 seconds to capture duration for startup/completion,
then bounded cleanup. Ctrl+C/SIGTERM cancels and reaps the worker. Exit codes
follow the discovery/capture table above. Keep the JSON and separately record
the wearer's during/after passthrough and tracking observations.

## Compare detectors on Frame stills

The default remains `mobilenet-ssd`; `yolox-nano` is an 80-class COCO candidate.
Neither has a rug class. Update the replay environment when model dependencies
change with `bash scripts/setup_window.sh`.

Capture a private unannotated still and pin its identity in an evaluation
manifest. Change `--present` and `--absent` to match the scene:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  mkdir -p evaluation/local
  sightcircuit_still="evaluation/local/room-$(date +%Y%m%d-%H%M%S)"
  .venv-replay/bin/python scripts/mirror_recognize.py --capture --device /dev/video99 \
    --seconds 5 --detector yolox-nano --still "$sightcircuit_still.png" > "$sightcircuit_still.json"
  .venv-replay/bin/python scripts/frame_session_manifest.py --manifest evaluation/local/frame.json \
    --id "${sightcircuit_still##*/}" --image "$sightcircuit_still.png" \
    --still-report "$sightcircuit_still.json" --present chair --absent couch
)
```

`--still` saves the final processed frame at native mirror size, without labels.
The helper requires a successful capture report and verifies its SHA-256. It
records the digest, detector, source, and sequence. Keep these fields when
adding optional object boxes. `evaluation/local/` is Git-ignored; keep room
images on the device.

Compare both detectors on those same inputs, requiring every entry to have a digest:

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --require-input-digests --report "evaluation/local/report-$(date +%Y%m%d-%H%M%S).json"
```

A hand-added image without a digest must be prepared before this strict command
can run. Supplied digests are always verified. The automated Frame session's
`evaluate` stage does not add `--require-input-digests`; use the explicit command
above when every input must be pinned.

Use canonical COCO labels such as `couch`, `tv`, and `dining table`. Unsupported
classes are reported separately from misses. Presence-only labels do not measure
localization accuracy. See the [manifest and report reference](../reference/detector-evaluation.md)
for optional boxes, confusion labels, timing, and preprocessing audits. Choose
models from representative inspected Frame evidence, not the outdoor seed set.

## Check whether the VR panel appears in mirror input

The existing comparison already confirmed inclusion ([E18](../evidence/e018.md)).
Repeating it unchanged is not the next task. Its [original fixed-panel procedure](../history/mirror-procedures.md#check-whether-the-vr-panel-appears-in-mirror-input)
is retained for a reviewed, materially changed source/view/runtime.

That check used a fixed TOP/grid panel, not the calibration marker or live video.
API visibility alone did not establish that the wearer saw it. A negative
comparison would apply only to the tested view and placement; it would not
prove general exclusion or say whether a desktop window is included.

The active fixed HUD requires qualified masking of its full footprint before
inference, plus feedback-exclusion and physical-expiry acceptance. Keep the
dedicated provider paused and follow the [current plan](../live-capture.md).
