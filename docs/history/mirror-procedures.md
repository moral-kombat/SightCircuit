# Earlier mirror procedures and observations

**Historical reference.** This preserves the detailed milestone-by-milestone
account previously embedded in the device guide. Its next-step language and
sample paths describe those runs. Use [the maintained mirror guide](../guides/mirror-recognition.md)
for a new task, [status](../status.md) for reviewed outcomes, and the
[current plan](../live-capture.md) for proposed work.

## Check an existing SteamVR mirror

This Python-standard-library diagnostic discovers existing virtual mirror
metadata by default. It does not open video devices or call OpenVR in that
mode. Close the replay window and its VR panel before a capture trial so our
output cannot become part of its own input. Keep normal passthrough working.

On Frame:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  git pull --ff-only
  python3 scripts/mirror_check.py
)
```

The JSON has `status: discovery_only` and exit code `0` even when no candidate
exists. Inspect `discovery.status` and `discovery.candidates`. A candidate needs
the exact sysfs name `SteamVR`, a virtual-video sysfs path and readable device
identity metadata. The earlier inventory listed a device named SteamVR at
`/dev/video99`; the diagnostic discovers by metadata rather than assuming that
number. Named nodes with unverified metadata are reported but never selected.

If exactly one candidate is reported, opt in to acquisition:

```bash
python3 scripts/mirror_check.py --capture --seconds 10
```

With multiple candidates, add `--device /dev/videoN` using the exact candidate
path from discovery. No arbitrary physical device or symlink fallback is
accepted. The worker rechecks sysfs/device identity, opens read-only and
nonblocking, verifies the V4L2 loopback driver/card and current capabilities,
then queries the current format. It does not request format changes, start a
service, install a layer, or use the public tracked-camera API. Only existing
single-plane `read()` capture is supported; unsupported drivers, capabilities
or formats produce an explicit result instead of trying another source.

The first Frame trial passed these open/format checks but returned zero frames,
with normal cleanup and working passthrough. The reader was then corrected to
make nonblocking reads without waiting for an initial readiness event: some
loopback drivers register file-I/O capture only on the first `read()`. The driver
may internally initialize its capture reader as part of that call; the tool
does not issue queue/stream-on ioctls or launch a producer. The
[corrected trial](../status.md#e12-successful-mirror-frame-delivery) then received
100 complete 1920 × 1080 RGB3 frames in ten seconds, with changing samples and
clean shutdown. The explicit preview below checks recognizable scene content;
delivered mirror frames alone do not identify their scene or physical camera.

Reads are capped at 10 per second. The worker budget is the requested duration
plus three seconds for startup/driver calls, followed by up to 2.5 seconds of
bounded stop/terminate/kill cleanup. Ctrl+C requests cancellation. A worker
terminated forcibly has `cleanup_ok: false`; OS descriptor closure is distinct
from confirmation of the normal close path.

To explicitly save one grayscale PNG after acquisition has closed the source:

```bash
mkdir -p artifacts/mirror-check
python3 scripts/mirror_check.py --capture --seconds 10 \
  --preview artifacts/mirror-check/new-preview.png
```

Choose a new `.png` path in an existing directory. The tool refuses overwrites,
including a destination created during capture, and publishes the still only
after successful acquisition and shutdown. It fits within 640 × 640 and is not
a color/HDR fidelity check. Without `--preview`, no image is saved. Open the
still after the command ends and confirm that a moving physical object appears
in the selected passthrough view; changing game/UI pixels alone are insufficient.
Keep a recognizable real object in view near the end of capture. Report whether
the saved image shows that physical scene, a rendered VR/app view, UI or black
pixels, and describe orientation/cropping. Confirm passthrough works during and
after this trial. A single still can establish recognizable content but does
not by itself validate continuous scene freshness or camera identity.

The Frame preview trial has already saved a 640 × 360 grayscale image at
`artifacts/mirror-check/scene-check.png`; see
[the preview evidence](../status.md#e13-mirror-preview-publication-on-frame).
Reopen that existing file before running another capture. Use an explicit
absolute local-file URL so a browser does not interpret `artifacts/...` as a
web address:

```bash
xdg-open "file://$HOME/sightcircuit/artifacts/mirror-check/scene-check.png"
```

`xdg-open` uses the system's associated viewer. The reported Chrome launcher
message “Opening in existing browser session” followed by a zygote broken-pipe
error occurs after capture. The user confirmed Chrome opened but treated the
relative path as an address and did not show the PNG. Opening the absolute file
URL then worked: the user recognized a black-and-white photo of the room.
Grayscale is intentional in this preview; the acquired format was RGB3. See
[the visual confirmation](../status.md#e14-physical-room-visible-in-the-mirror-preview).
The launcher warning does not change a successful
`images_saved: true` capture report. If no image opens,
resolve image presentation without reacquiring the camera. A new capture needs
a new output path because existing files are deliberately preserved.

The report includes driver/capabilities, current format, `frames_read`, observed
read rate, maximum read gap, changed/unchanged grayscale samples, short reads
and worker cleanup. The sampled grid is at most 32 × 32. Identical samples can
mean a static scene; changed samples cannot prove a live physical camera.
Neither V4L2 producer sequence nor exposure timestamps are supplied by this
read-based diagnostic. Physical source identity stays explicitly unverified.
`reader_method: nonblocking_read_without_poll_gate` identifies the correction.
`read_attempts`, `would_block_reads` and `interrupted_reads` distinguish actual
reads from successful frame delivery. If the corrected trial still returns no
frames, retain the JSON and continue provider preparation rather than repeating
the unchanged command or modifying the compositor.

| Exit code | Meaning |
| --- | --- |
| `0` | Discovery completed, or capture read images and confirmed normal cleanup; inspect mode/status |
| `1` | Unavailable/unverified input, unsupported format/capabilities, no frames, deadline or another diagnostic error |
| `2` | Invalid command-line arguments; capture did not start |
| `130` / `143` | Cancelled by Ctrl+C / SIGTERM |

Retain the JSON for unavailable/unsupported results. An unsupported format is a
limit of the current reader, not proof that the mirror is unusable or the camera
is faulty. Do not activate a new mirror service or force a physical camera path
to make this check pass. Frame discovery, opening, format queries and complete
frame delivery succeeded, and the user recognized the room in the saved still.
Physical camera identity remains unknown; the later panel comparison established display feedback. See
[the room-image confirmation](../status.md#e14-physical-room-visible-in-the-mirror-preview),
[device capture](../status.md#e12-successful-mirror-frame-delivery),
[cloud evidence](../status.md#e8-mirror-diagnostic-cloud-checks) and the
[active plan](../live-capture.md#current-work-order).


## Run recognition on the saved mirror image

The saved room preview can be processed with the existing file detector after
capture has stopped. This is a bounded still-image test, not a live-source
window. Use the already configured `.venv-replay` environment and completed
model fixtures from `setup_window.sh`. It opens no camera, requests no OpenVR
panel and creates no visible Qt window during processing:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
    artifacts/mirror-check/scene-check.png --max-frames 1 \
    --preview artifacts/mirror-check/scene-detections.png \
    --report artifacts/mirror-check/scene-detections.json
  xdg-open "file://$HOME/sightcircuit/artifacts/mirror-check/scene-detections.png"
)
```

Choose new output names on subsequent runs; the source PNG is preserved. The
command prints and saves an `annotated_file_replay` report. Expected execution
fields are `status: finished`, `frames_processed: 1`, `frames_presented: 1`,
`overlay_requested: false` and `images_saved: true`. Inspect `labels_seen` and
the annotation image for boxes that match objects. Labels come from the demo's
20 classes, including person, chair, bottle, diningtable and tvmonitor, at the
default confidence threshold of 0.5. An empty label list is a recognition result,
not a capture failure. The output is a screenshot of the annotated application
including its controls; it is not a raw camera image.

The grayscale input is converted to the detector's three-channel input; this
does not recover source color. The user completed this test on Frame and saw a
rug mislabeled `sofa`; see [the result](../status.md#e15-saved-room-recognition-and-rug-false-positive).
The demo has no rug class. An annotation score such as the earlier `0.70` means
70% model confidence, not 0.7% or a calibrated probability of correctness.
The shared detector now prints explicit percentages in both replay and mirror
annotations. This formatting change does not improve classification. Keep live input and
VR output separate until source freshness, camera identity and capture of our
own output are understood; follow the [active plan](../live-capture.md#current-work-order).


## Run bounded recognition from the mirror

The [recorded-input POC](../status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame)
has now completed with visible VR labels and clean shutdown. The
[bounded live POC](../status.md#e48-live-mirror-recognition-poc-and-matching-preview)
also completed: 100 reads, 51 processed frames, clean shutdown and a preview
whose labels/boxes the user confirms match objects, with normal passthrough and
tracking during/after. The command below remains
available for another demonstration. Aim at a supported object such as a chair or TV,
keeping it visible through the final seconds: the saved preview is the last
processed image, while `labels_seen` aggregates the entire run.

This Frame trial passed its first execution/cleanup check at 4.993 processed
fps, and the user reports normal passthrough; see
[the device result](../status.md#e17-bounded-mirror-recognition-on-frame). It combines
the existing guarded mirror reader and the same CPU demo detector, with no Qt
window, OpenVR calls or live result display. Use the already configured
`.venv-replay`; no new dependencies or native build are needed. Close the saved
preview tab and any replay/result windows or VR panel, then keep normal
passthrough working before starting:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  git pull --ff-only
  mkdir -p artifacts/poc
  sightcircuit_demo_dir="$(mktemp -d "$PWD/artifacts/poc/live-XXXXXX")"
  .venv-replay/bin/python scripts/mirror_recognize.py --capture \
    --device /dev/video99 --seconds 10 --max-fps 5 \
    --preview "$sightcircuit_demo_dir/detections.png"
  xdg-open "file://$sightcircuit_demo_dir/detections.png"
)
```

The device must still match current virtual SteamVR metadata and read-only
driver/capability checks. The recognizer accepts RGB3/BGR3 only and never
requests another format or falls back to a physical camera. It verifies/loads
the model before opening the source. A capture thread reads at most ten times
per second; a worker targets at most five processing starts per second. There
is one pending input, replaced when processing falls behind. Each annotated
result keeps its own input image, local read number and observation time.

No flags means sysfs-only discovery, without loading the detector or opening a
device. Without `--preview`, no image is saved. The explicit final PNG above
is a copy of the last processed image with annotations, fitted within 640 × 640,
encoded after source closure and published after normal worker shutdown. It is
not a screenshot of a live window. Each run above creates a private output
directory, preserving earlier images. It retains the source's RGB/BGR channels;
actual camera color/HDR fidelity remains unverified.

The JSON has `report_kind: steamvr_mirror_recognition`; successful processing
reports `status: recognized`, exit `0`, `cleanup_ok: true`, positive
`frames_processed`, `display_started: false` and `overlay_requested: false`.
`capture` contains read counters, source format and source cleanup. Other fields
include replaced pending inputs, stale selections, actual processing rate,
mean/maximum inference time, labels seen and the last result's input identity,
score/boxes and age. At most 20 final boxes are reported, in the resized input's
pixel coordinates (at most 960 pixels on its longer side), before optional PNG
downscaling. `read_sequence` is our count, not a producer sequence. Times describe
reader observation and processing, not exposure or full camera latency.

Selections older than 1,000 ms are skipped; an old final observation produces
`stale_input` and no preview. Empty labels alone are not a processing failure.
The worker deadline is the requested duration plus 15 seconds for model startup
and completion, then up to 2.5 seconds of bounded cleanup. Ctrl+C/SIGTERM cancels
and reaps the worker. Forced termination does not claim normal source cleanup.
Exit codes are `0` for discovery/recognized, `1` for unavailable/stale/error
results, `2` for invalid options, and `130`/`143` for cancellation.

Paste the JSON and report whether normal passthrough/tracking remained working
during and after capture. Inspect the PNG after the process ends and compare
labels/boxes with objects; the known rug-as-sofa false positive is unresolved.
The first trial saw chair/tvmonitor labels at some earlier points, but the last
processed frame had no detections. `labels_seen` aggregates the entire run;
the final PNG corresponds only to `last_result`, so it can correctly have no
boxes even when the aggregate is nonempty.
The user confirmed that both a chair and a TV/monitor were in view during this
run; their intermediate boxes were not retained, so localization remains unverified.
The later [E48 result](../status.md#e48-live-mirror-recognition-poc-and-matching-preview)
contains a final person detection and user-confirmed matching preview boxes.
That sampled observation does not validate all intermediate boxes or class accuracy.
This initial check measures capture/inference interaction without introducing
our result into its own input. A future live window/VR panel still requires a
display-feedback check. [Cloud evidence](../status.md#e16-bounded-mirror-recognition-cloud-checks)
is distinct from the subsequent device execution result.


## Compare detectors on Frame stills

`yolox-nano`, an 80-class COCO candidate, can be selected with `--detector` in
`replay_window.py` and `mirror_recognize.py`; the default remains the validated
`mobilenet-ssd`. Neither model has a rug class. Run setup once after updating,
because it adds the pinned ONNX Runtime wheel and the checksum-verified model:

```bash
cd "$HOME/sightcircuit" && git pull --ff-only && bash scripts/setup_window.sh
```

Then capture unannotated stills for a private evaluation set. `--still` saves
the frame of the last processed result without annotation, at its native mirror
size rather than the 960-pixel detector input. Like `--preview`, it needs a new
path and is published only after capture/inference stops and cleanup is
confirmed. `evaluate_detectors.py` applies the application's own conversion to
at most 960 pixels, so its input equals what the live detector saw. The JSON
report records `still_sha256`, `still_detector`, `still_source` and
`still_sequence` (the same frame as `last_result`); keep it, because the
manifest helper pins the still's digest from it. Aim at the scene to label,
including the rug from [E15](../status.md#e15-saved-room-recognition-and-rug-false-positive),
and keep it in view through the final seconds. Change `--present`/`--absent` to
the scene:

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

The `stills` stage of `scripts/frame_session.sh` runs the same two commands for
each scene. The manifest helper refuses a report that is not a successful
`--still` run for that image, or whose SHA-256 the image no longer matches. The
entry then carries `sha256` and `capture` (detector, source and sequence); keep
both when adding boxes by hand. The earlier grayscale
`artifacts/mirror-check/scene-check.png` is also usable, but it has no capture
report, so its entry has no pinned digest.
`evaluation/local/` is ignored by Git, so private room images stay on the
device. Each entry in `evaluation/local/frame.json` describes one still; boxes
are optional source-pixel coordinates (an image viewer's cursor position is enough):

```json
{
  "schema_version": 1,
  "images": [{
    "id": "room-rug",
    "path": "room-20261025-120000.png",
    "present": ["chair"],
    "absent": ["couch"],
    "objects": [{"label": "rug", "box_xyxy": [120, 300, 520, 470], "confusable": ["couch", "bed"]}]
  }]
}
```

Compare both detectors on identical inputs and paste the summary lines and
JSON totals:

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --report "evaluation/local/report-$(date +%Y%m%d-%H%M%S).json"
```

Pinned digests are verified before inference. Add `--require-input-digests` to
refuse any entry without one, such as the hand-added grayscale still.
`present`/`absent` list canonical COCO names (`couch`, `tv`, `dining table`);
VOC output such as `sofa` is mapped automatically. A rug detected as `couch`
inside the rug's box is counted under `confusions`. Labels a model cannot
produce are listed as unsupported rather than missed. Timing is per still on
Frame's CPU, without capture or display. See
[development](../reference/detector-evaluation.md#detector-evaluation) for the full format.


## Check whether the VR panel appears in mirror input

This check confirmed that the panel appears both in the headset and in mirror
input; see [the result](../status.md#e18-vr-panel-included-in-mirror-input). It was
a capture-only comparison, with intentional grayscale output and no inference,
so no object boxes or labels were expected. Keep the procedure for a materially
changed source/view/runtime; repeating it unchanged is not the next task.
Use the existing native
bridge from the confirmed replay-panel setup. If its binary is absent, build
it first with `bash scripts/build_overlay_bridge.sh`; a build does not run the
camera probe. This test uses the bridge's **fixed** TOP/grid pattern, not a
live captured image, and performs no inference.

Close saved-preview tabs and replay/result windows. Keep passthrough visible.
The command below starts a 15-second head-relative test panel, gives it two
seconds to start, captures five seconds from the verified mirror, waits for the
panel to finish, then opens the saved grayscale still:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  test -x artifacts/overlay-bridge/sightcircuit-overlay-bridge
  mkdir -p artifacts/mirror-check
  artifacts/overlay-bridge/sightcircuit-overlay-bridge --test-panel --seconds 15 &
  sightcircuit_panel_pid=$!
  sleep 2
  python3 scripts/mirror_check.py --capture --device /dev/video99 --seconds 5 \
    --preview artifacts/mirror-check/feedback-check.png
  wait "$sightcircuit_panel_pid"
  xdg-open "file://$HOME/sightcircuit/artifacts/mirror-check/feedback-check.png"
)
```

During capture, confirm you actually see the small head-relative grid with
white **TOP** text and four colored quadrants. Keep it in view. Its appearance
is described in the [panel guide](../vr-overlay.md#first-device-test-a-timed-panel).
The native log's `test_panel_submitted` and `api_visible` are API observations;
they do not replace visible confirmation. Retain the mirror JSON and final
native cleanup report. Use a new output path for another test.

After the panel closes, inspect `feedback-check.png` and report:

- Whether the panel was visible in the headset during the mirror capture.
- Whether the TOP/grid panel also appears in the saved room image.
- Whether normal passthrough and tracking stayed working during and after.

If the panel appears in input, this view includes our VR output and is unsuitable
for streaming captured images continuously back into themselves. Continue
bounded/no-display recognition and post-capture snapshots. The adopted fixed
edge HUD instead requires its complete native footprint mask to be qualified,
then feedback exclusion and physical expiry to pass the
[acceptance gates](../live-capture.md#edge-hud-acceptance-gates). The dedicated
provider stays frozen. Do not assume a crop or a hidden panel proves exclusion.

If it does not appear, the result supports exclusion only for that tested view,
placement and runtime. Repeat deliberately at other poses/positions before a
general claim; ordinary Qt windows need their own check. If no panel appeared in
the headset or it was not visible during capture, this comparison is inconclusive.
The user observed the included panel with both tools reporting clean shutdown.
Arcturus is now confirmed connected; the selected passthrough source remains
unknown. The next proposed device checkpoint is
[bounded HUD calibration](../guides/hud-checkpoint.md#bounded-edge-hud-calibration-checkpoint), after its
lifecycle prerequisites and scoped authorization; the
[proposed qualification method](../hud-qualification.md#proposed-g4-method) defines
the additional evidence. The native provider prerequisites below are deferred
reference material. The mirror can still provide bounded recognition and
snapshots displayed after capture closes. This procedure does not install
layers, switch camera sources or repeat the public tracked-camera probe.


[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
