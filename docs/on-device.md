# Continue development on Steam Frame

Use this guide for device access, inventory and recorded-video replay. Start
with [status and evidence](status.md) for compatibility and verified results,
then the [overlay guide](vr-overlay.md) to run the VR panel. The
[active capture plan](live-capture.md#current-work-order) owns upcoming live-input
work. The mirror diagnostic and isolated shared-memory reader below are
implemented and cloud-tested. Dedicated camera-provider deployment remains
planned.

## Confirm where commands run

A web interface is useful if its terminal or development agent executes on the
headset. A browser on Frame may instead be displaying a cloud session, a PC
terminal or an isolated container. Opening this chat on Frame does not move its
attached cloud execution environment onto the headset.

Use an existing terminal/editor on the headset, or a development session already
connected to it. A browser-only page cannot directly provide the SteamVR/Linux
camera interfaces needed by this project. Existing terminal access does not
require changing operating-system protections or restarting SteamVR.

## Starting with only a browser

The simplest development connection is a terminal on another computer on the
same network, connected to Frame over SSH. The [Valve setup reference][valve-setup]
and [community Frame instructions][frame-ssh] are the relevant references. The
community instructions describe these menus:

1. On Frame, open **Steam Settings → System → Enable Developer Mode**.
2. Open the **Developer** settings and choose **Set User Password**. Enter the
   password on the device, not in a chat or committed configuration file.
3. Find the headset's IP address under **Steam Settings → Internet**.
4. From a terminal on another computer on that network, connect using the normal
   user account documented by the guide:

   ```bash
   ssh steamos@<FRAME_IP>
   ```

Replace `<FRAME_IP>` with the address shown on the headset. Once connected,
commands execute on Frame. Menu labels and access can vary with runtime builds;
follow the current Valve reference if they differ. Enabling developer access is
a manual device step; no script in this repository performs it.

If Frame is the only available computer, a local terminal/development app is
needed before these commands can run. A browser-only chat does not supply that
execution connection, and this cloud session cannot automatically reach a
headset on a private home network. Establish the local terminal or a supported
remote development connection before proceeding.

After shell access works, an authenticated browser editor/terminal can be
considered for convenience. A browser-based camera dashboard is a separate app
feature: it would communicate with a backend running on Frame, which owns the
capture adapter and detector. A web page alone does not establish Arcturus
capture capability. Start with terminal access rather than deploying a new
web service during the initial camera investigation.

## Obtain the repository

In a writable development directory belonging to your normal user, clone the
repository if it is not already present:

```bash
git clone https://github.com/moral-kombat/framejacker.git
cd framejacker
```

If a checkout already exists, preserve its local changes and use it. Update a
clean checkout of `main` with `git pull --ff-only`; do not reset local work to
match these instructions. Codex tasks are already isolated when applicable;
do not create a worktree unless explicitly requested.

## Run the device inventory first

Use the headset's available Python 3 interpreter. This script needs only the
standard library and does not require the project's virtual environment:

```bash
python3 scripts/device_check.py
```

The report includes architecture, selected OS/build identifiers, tools on PATH,
the result of a user-level SteamVR service query, video-device names and render
node presence. It does not open cameras, acquire a stream, restart services,
change settings, install packages or print credential/environment values.

Share the generated JSON in the development session. Architecture and OS help
distinguish the headset from the cloud machine, but neither establishes access
to the headset's compositor session. Missing services or commands may reflect
terminal/container isolation rather than absent software. Listed device nodes
do not prove working capture or GPU access.

## Then verify camera access

Establish the baseline using the [official Arcturus Camera app][arcturus-camera]
and [vendor setup instructions][arcturus-howto]. Confirm that its live view and
recording work before investigating a new capture route. Preserve completed
recordings and their metadata for the replay checks below.

See [inventory evidence](status.md#e1-frame-inventory) and
[vendor-app evidence](status.md#e2-vendor-app) for the reported baseline.
Device-node presence does not demonstrate frame access. Keep Framejacker's
live-input work separate from the vendor app and use the active plan for a
new capture trial.

### Confirmed public-probe result

**The public camera probe returned no frames and blacked out passthrough while
running on the tested runtime. Do not repeat that unchanged headset test.**
A future retest needs a material change, such as a runtime update or revised
capture implementation. The detailed API failures and their limits are in
[the evidence record](status.md#e3-public-camera-probe).

The binary makes no OpenVR calls by default. Its explicit interruption flag
and advanced procedure appear below; a nonzero streaming handle alone does
not establish usable capture.

### Prepare a recording for replay

Record and stop a short clip in the Arcturus app. Community
[transfer code][recording-folder] uses `~/Videos/ArcturusVisionCamera` and
`*_stereo.mp4` filenames for camera recordings. Verify that against the installed
app version. A metadata inspector using only Python's standard library is
available even when `ffprobe` is absent. Run it on a completed recording:

```bash
python3 scripts/recording_check.py "$HOME/Videos/ArcturusVisionCamera/REPLACE_stereo.mp4"
```

Replace the filename with the selected recording. With no path argument, the
script selects the most recently modified `*_stereo.mp4` in that folder. Stop
recording in the vendor app first so the container metadata is complete.

The JSON report identifies track/sample-entry types, video dimensions and track
durations when present. The script reads container headers and skips media
payloads; it never changes the recording or accesses the camera. It does not
decode images, establish left/right camera identity, validate calibration, or
prove that the headset has a compatible decoder. Keep that metadata report as
a description of the replay input. A recording is replay input, not a live
capture backend.

Known recording sizes, track metadata and decode results are recorded in
[status](status.md#e4-recordings-and-container-metadata). The fragmented-MP4
inspector result describes its limited timeline handling; it is not by itself
a corrupt-file diagnosis.

To inspect a new recording, one command runs the read-only inventory and selects the
newest completed stereo recording automatically:

```bash
cd "$HOME/framejacker"
bash scripts/frame_replay_check.sh
```

This does not initialize OpenVR, start or stop SteamVR, open camera device
nodes, decode video, or modify the recording. Keep its JSON output alongside
the playback observations when investigating a recording or runtime change.

### Proposed recording growth test

**Paused.** This procedure is kept for reference; no new recorder experiment
is planned, and the active device work is the
[edge HUD calibration checkpoint](#bounded-edge-hud-calibration-checkpoint).
No Frame result for it is recorded in [status](status.md).

The question it would answer is whether the vendor camera app publishes a
recording while it is still being written. `scripts/recording_growth_check.py`
reads only top-level MP4 box headers, never media payloads, and decodes no
images. It runs bounded and identity-checked in a supervised worker. With no
mode it does nothing (status `not_run`, exit 3). Its design and offline tests
are in [development](development.md).

If it is approved again, start the watcher first, then start a **new**
recording in the vendor app within the wait window:

```bash
cd "$HOME/framejacker"
python3 scripts/recording_growth_check.py \
  --watch-directory "$HOME/Videos/ArcturusVisionCamera" \
  --wait-for-new 30 --seconds 15
```

The watcher ignores existing clips and waits up to `--wait-for-new` seconds
(at most 120) for exactly one new `*_stereo.mp4`; it refuses an ambiguous
choice. It then observes that file's headers for `--seconds` (at most 30).
`--observe FILE` observes one file the operator chose instead.

A positive result only shows that fragment extents kept growing while the file
was written. It does not prove a complete payload, a usable live source or
that the source excludes our VR panel. Keep the JSON report and record it as a
device-command result, separate from cloud tests.

### Decode one recorded video track

Use the separate file-decoding environment on Frame's Python 3.12. This needs
the standard-library `venv`/`ensurepip` modules and PyPI access. The pinned
`av==16.1.0` release publishes CPython 3.12 Linux ARM64 and x86_64 wheels
(glibc 2.28 or later). Installation permits binary wheels only; it does not
compile FFmpeg or install system packages. If `venv` or a compatible wheel is
unavailable, retain and share the error before choosing another setup route.

```bash
(
  set -e
  cd "$HOME/framejacker"
  git pull --ff-only
  bash scripts/setup_replay.sh
  .venv-replay/bin/python scripts/replay_decode_check.py \
    "$HOME/Videos/ArcturusVisionCamera/2026-10-15_21-20-34_stereo.mp4"
)
```

This downloads PyAV into ignored `.venv-replay/`; its wheel includes FFmpeg
libraries, so a separate `ffmpeg` executable, OpenCV and Qt are not required.
The existing desktop `.venv` remains separate. `FRAMEJACKER_PYTHON` can select
another Python 3.12 executable for setup.

The check reads only the selected file. It defaults to video-stream ordinal
`0`, decodes up to three frames using two software-decoder threads, and converts
them to RGB. The decoder runs in a child process with a 45-second limit. The
report includes actual dimensions, source pixel format, selected stream index,
media timestamps and `frames_decoded`. `status: decoded` with at least one
frame means this limited file check passed. No path argument selects the newest
`*_stereo.mp4`; `--video-stream 1` selects the second video track for a later
explicit test. Track order does not establish left/right identity, independence
of stereo codec views, or synchronized decoding.

This does not access cameras or OpenVR, save images, display a window, verify
HDR appearance, validate the complete file, or measure real-time performance.
Exit `0` means frames were decoded and converted; exit `1` means a missing
dependency, input error, timeout or decode failure. See
[selected-track evidence](status.md#e5-selected-track-decode) and
[cloud validation](status.md#e7-cloud-validation) for prior results. Continue
with the annotated replay window below.

### Open the annotated replay window

Run this from the same normal-user graphical session on Frame:

```bash
(
  set -e
  cd "$HOME/framejacker"
  git pull --ff-only
  bash scripts/setup_window.sh
  .venv-replay/bin/python scripts/replay_window.py \
    "$HOME/Videos/ArcturusVisionCamera/2026-10-15_21-20-34_stereo.mp4"
)
```

Setup extends `.venv-replay` with pinned NumPy, OpenCV headless and PySide6
Essentials and downloads the checksum-verified demo detector. The desktop
`.venv` is separate. The pinned Qt ARM64 wheel requires glibc 2.39 or later and
system Qt/X11 dependencies. Pip installs binary wheels only. Missing libraries
reported by the X11 preflight should be investigated before changing system
packages; share the exact output if setup or launch fails.

The window shows the selected video track with boxes and confidence labels for
the model's 20 common-object classes. Pause/resume, Replay and Loop controls
operate on the file. The short clip loops by default. No path argument uses
the newest stereo recording; the default video-track ordinal is 0. Close the
window to stop the worker and release the file. A JSON summary appears in the
terminal on exit.

Frames are resized to fit within 960 × 960 for processing and shown without
changing aspect ratio. Playback is capped at 10 processed fps by default and
may be slower than the source recording. `--max-fps` changes that cap;
`--threshold` changes the default 0.5 confidence threshold. No detections above
threshold is a valid result for a scene outside the demo model's classes.

`--realtime` follows media time instead of processing every frame. Decoding
runs on its own thread and inference takes only the newest decoded frame. When
decoding falls more than 0.5 s behind (the 2464 × 2464, ~60 fps recordings
decode at about 13 fps on Frame), undecoded packets are skipped to the next
keyframe at the current playback position, so time advances at the recording's
rate while fewer frames are shown. `--decode-threads N` (default 2) sets FFmpeg
frame/slice threads; more threads decode faster but compete with inference and
the compositor. The JSON summary reports `realtime_ratio` (media seconds per wall
second; about 1.0 means real time), `media_seconds_played`, `decode_fps`,
`frames_decoded`, `frames_skipped`, `catch_ups`, `frames_replaced` (decoded but
superseded before inference), `frames_dropped`, `processed_fps`,
`mean_processing_ms`, presentation latency and `max_schedule_lateness_ms`. The
keyframe-skipping version is cloud-tested only; see
[E62](status.md#e62-keyframe-catch-up-realtime-replay-cloud-checks). The earlier
frame-dropping version was decode-bound on Frame, processing about 0.97 fps
versus 5.15 fps unpaced
([E51](status.md#e51-media-time-replay-pacing-unit-tested-only),
[E53](status.md#e53-unattended-replay-regression-and-pacing-rerun)). Without
`--realtime` every frame is processed in order, which plays this recording in
slow motion.

By default the prototype displays an ordinary Qt window without opening camera
nodes, calling OpenVR, changing SteamVR or saving images. Optional `--overlay`
initializes OpenVR in a separate native process; see the
[VR panel guide](vr-overlay.md) for launch commands, controls and shutdown.
[Status](status.md#e6-window-and-vr-panel) records device confirmations and
unmeasured accuracy, color, readability and performance.

### Check an existing SteamVR mirror

This Python-standard-library diagnostic discovers existing virtual mirror
metadata by default. It does not open video devices or call OpenVR in that
mode. Close the replay window and its VR panel before a capture trial so our
output cannot become part of its own input. Keep normal passthrough working.

On Frame:

```bash
(
  set -e
  cd "$HOME/framejacker"
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
[corrected trial](status.md#e12-successful-mirror-frame-delivery) then received
100 complete 1920 × 1080 RGB3 frames in ten seconds, with changing samples and
clean shutdown. The next acceptance check is the explicit preview below;
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
[the preview evidence](status.md#e13-mirror-preview-publication-on-frame).
Reopen that existing file before running another capture. Use an explicit
absolute local-file URL so a browser does not interpret `artifacts/...` as a
web address:

```bash
xdg-open "file://$HOME/framejacker/artifacts/mirror-check/scene-check.png"
```

`xdg-open` uses the system's associated viewer. The reported Chrome launcher
message “Opening in existing browser session” followed by a zygote broken-pipe
error occurs after capture. The user confirmed Chrome opened but treated the
relative path as an address and did not show the PNG. Opening the absolute file
URL then worked: the user recognized a black-and-white photo of the room.
Grayscale is intentional in this preview; the acquired format was RGB3. See
[the visual confirmation](status.md#e14-physical-room-visible-in-the-mirror-preview).
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
Physical camera identity and display feedback remain unknown. See
[the room-image confirmation](status.md#e14-physical-room-visible-in-the-mirror-preview),
[device capture](status.md#e12-successful-mirror-frame-delivery),
[cloud evidence](status.md#e8-mirror-diagnostic-cloud-checks) and the
[active plan](live-capture.md#current-work-order).

### Run recognition on the saved mirror image

The saved room preview can be processed with the existing file detector after
capture has stopped. This is a bounded still-image test, not a live-source
window. Use the already configured `.venv-replay` environment and completed
model fixtures from `setup_window.sh`. It opens no camera, requests no OpenVR
panel and creates no visible Qt window during processing:

```bash
(
  set -e
  cd "$HOME/framejacker"
  QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
    artifacts/mirror-check/scene-check.png --max-frames 1 \
    --preview artifacts/mirror-check/scene-detections.png \
    --report artifacts/mirror-check/scene-detections.json
  xdg-open "file://$HOME/framejacker/artifacts/mirror-check/scene-detections.png"
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
rug mislabeled `sofa`; see [the result](status.md#e15-saved-room-recognition-and-rug-false-positive).
The demo has no rug class. An annotation score such as the earlier `0.70` means
70% model confidence, not 0.7% or a calibrated probability of correctness.
The shared detector now prints explicit percentages in both replay and mirror
annotations. This formatting change does not improve classification. Keep live input and
VR output separate until source freshness, camera identity and capture of our
own output are understood; follow the [active plan](live-capture.md#current-work-order).

### Run bounded recognition from the mirror

The [recorded-input POC](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame)
has now completed with visible VR labels and clean shutdown. The
[bounded live POC](status.md#e48-live-mirror-recognition-poc-and-matching-preview)
also completed: 100 reads, 51 processed frames, clean shutdown and a preview
whose labels/boxes the user confirms match objects, with normal passthrough and
tracking during/after. The command below remains
available for another demonstration. Aim at a supported object such as a chair or TV,
keeping it visible through the final seconds: the saved preview is the last
processed image, while `labels_seen` aggregates the entire run.

This Frame trial passed its first execution/cleanup check at 4.993 processed
fps, and the user reports normal passthrough; see
[the device result](status.md#e17-bounded-mirror-recognition-on-frame). It combines
the existing guarded mirror reader and the same CPU demo detector, with no Qt
window, OpenVR calls or live result display. Use the already configured
`.venv-replay`; no new dependencies or native build are needed. Close the saved
preview tab and any replay/result windows or VR panel, then keep normal
passthrough working before starting:

```bash
(
  set -e
  cd "$HOME/framejacker"
  git pull --ff-only
  mkdir -p artifacts/poc
  framejacker_demo_dir="$(mktemp -d "$PWD/artifacts/poc/live-XXXXXX")"
  .venv-replay/bin/python scripts/mirror_recognize.py --capture \
    --device /dev/video99 --seconds 10 --max-fps 5 \
    --preview "$framejacker_demo_dir/detections.png"
  xdg-open "file://$framejacker_demo_dir/detections.png"
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
The later [E48 result](status.md#e48-live-mirror-recognition-poc-and-matching-preview)
contains a final person detection and user-confirmed matching preview boxes.
That sampled observation does not validate all intermediate boxes or class accuracy.
This initial check measures capture/inference interaction without introducing
our result into its own input. A future live window/VR panel still requires a
display-feedback check. [Cloud evidence](status.md#e16-bounded-mirror-recognition-cloud-checks)
is distinct from the subsequent device execution result.

### Compare detectors on Frame stills

`yolox-nano`, an 80-class COCO candidate, can be selected with `--detector` in
`replay_window.py` and `mirror_recognize.py`; the default remains the validated
`mobilenet-ssd`. Neither model has a rug class. Run setup once after updating,
because it adds the pinned ONNX Runtime wheel and the checksum-verified model:

```bash
cd "$HOME/framejacker" && git pull --ff-only && bash scripts/setup_window.sh
```

Then capture unannotated stills for a private evaluation set. `--still` saves
the last processed input, at the detector's input size, after the source closes;
it is separate from the annotated `--preview`. Aim at the scene to label,
including the rug from [E15](status.md#e15-saved-room-recognition-and-rug-false-positive),
and keep it in view through the final seconds:

```bash
(
  set -e
  cd "$HOME/framejacker"
  mkdir -p evaluation/local
  .venv-replay/bin/python scripts/mirror_recognize.py --capture --device /dev/video99 \
    --seconds 5 --detector yolox-nano \
    --still "evaluation/local/room-$(date +%Y%m%d-%H%M%S).png"
)
```

The earlier grayscale `artifacts/mirror-check/scene-check.png` is also usable.
`evaluation/local/` is ignored by Git, so private room images stay on the
device. Describe each still in `evaluation/local/frame.json`; boxes are optional
source-pixel coordinates (an image viewer's cursor position is enough):

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

`present`/`absent` list canonical COCO names (`couch`, `tv`, `dining table`);
VOC output such as `sofa` is mapped automatically. A rug detected as `couch`
inside the rug's box is counted under `confusions`. Labels a model cannot
produce are listed as unsupported rather than missed. Timing is per still on
Frame's CPU, without capture or display. See
[development](development.md#detector-evaluation) for the full format.

### Check whether the VR panel appears in mirror input

This check confirmed that the panel appears both in the headset and in mirror
input; see [the result](status.md#e18-vr-panel-included-in-mirror-input). It was
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
  cd "$HOME/framejacker"
  test -x artifacts/overlay-bridge/framejacker-overlay-bridge
  mkdir -p artifacts/mirror-check
  artifacts/overlay-bridge/framejacker-overlay-bridge --test-panel --seconds 15 &
  framejacker_panel_pid=$!
  sleep 2
  python3 scripts/mirror_check.py --capture --device /dev/video99 --seconds 5 \
    --preview artifacts/mirror-check/feedback-check.png
  wait "$framejacker_panel_pid"
  xdg-open "file://$HOME/framejacker/artifacts/mirror-check/feedback-check.png"
)
```

During capture, confirm you actually see the small head-relative grid with
white **TOP** text and four colored quadrants. Keep it in view. Its appearance
is described in the [panel guide](vr-overlay.md#first-device-test-a-timed-panel).
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
[acceptance gates](live-capture.md#edge-hud-acceptance-gates). The dedicated
provider stays frozen. Do not assume a crop or a hidden panel proves exclusion.

If it does not appear, the result supports exclusion only for that tested view,
placement and runtime. Repeat deliberately at other poses/positions before a
general claim; ordinary Qt windows need their own check. If no panel appeared in
the headset or it was not visible during capture, this comparison is inconclusive.
The user observed the included panel with both tools reporting clean shutdown.
Arcturus is now confirmed connected; the selected passthrough source remains
unknown. The next proposed device checkpoint is
[bounded HUD calibration](#bounded-edge-hud-calibration-checkpoint), after its
lifecycle prerequisites and scoped authorization; the
[proposed qualification method](hud-qualification.md#proposed-g4-method) defines
the additional evidence. The native provider prerequisites below are deferred
reference material. The mirror can still provide bounded recognition and
snapshots displayed after capture closes. This procedure does not install
layers, switch camera sources or repeat the public tracked-camera probe.

### Record headset observations for a run

`scripts/observation_page.py` serves a short form on the local network. The
person wearing the headset opens it in the headset browser (or a phone on the
same Wi-Fi) and answers fixed questions, so answers are saved against the run
instead of being left unanswered at an SSH prompt. It is standard-library Python
and needs no setup beyond the checkout. Start it before the test and leave it
running:

```bash
cd "$HOME/framejacker"
RUN="artifacts/frame-session/$(date +%Y%m%d-%H%M%S)"
python3 scripts/observation_page.py --run-dir "$RUN" --minutes 20
```

The first line of output is JSON with a `url` that contains a random token.
Open that URL from the headset or phone. Each submission is appended to
`$RUN/observations.json` with a UTC `submitted_at` time and the answers. The
server stops after `--minutes` (maximum 120) or on Ctrl-C, and prints how many
submissions it saved.

Options: `--run-id` sets the label shown on the page (default: the run directory
name); `--port` defaults to 8765; `--host 127.0.0.1` keeps the page local, so
only the Frame itself (for example with `curl` over SSH) can reach it. The default `--host 0.0.0.0` listens on
all interfaces, so the page is reachable from the LAN while it runs. Traffic is
plain HTTP, the token is the only access control, and the server only accepts
the form's fixed fields and choices. Do not leave it running after the session.

Whether the headset browser can reach the printed address is not yet verified.
If it cannot, note the URL, the error, and the network setup in
[status](status.md) rather than assuming the page works. Record the answers in
`observations.json` as user-reported results, not as device checks.

### Bounded edge HUD calibration checkpoint

This is the device step for WP9 in the [POC scope](poc-scope.md#work-packages)
and the first check of the [fixed edge HUD](architecture.md#fixed-edge-hud-decision)
on Frame. It has **not been run on Frame**. It needs its own approval under the
[October 26 handoff](live-capture.md#october-26-evening-handoff); this page is
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

#### Before the device run

Run the offline checks on the reviewed revision, in the cloud or on Frame:

```bash
cd "$HOME/framejacker"
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest \
  tests.test_hud_marker_check tests.test_hud_marker_observer tests.test_hud_mask
```

On Frame, confirm all of the following:

- [Independent SSH](#verify-independent-ssh-access) from the PC works, so the
  run can be stopped and inspected without the headset UI.
- The checkout is clean at the approved revision, `.venv-replay` is set up,
  and the bridge is built on Frame with `bash scripts/build_overlay_bridge.sh`.
- `/dev/video99` is still the verified SteamVR mirror candidate
  ([mirror check](#check-an-existing-steamvr-mirror)).
- No replay window, VR panel, mirror capture or other overlay is running.
- Someone is wearing the headset, with normal passthrough and tracking.
  Optionally start the [observation page](#record-headset-observations-for-a-run)
  for their answers.

Then run the marker's validation mode on Frame. It uses the generated marker
and native timer only, and never initializes VR:

```bash
cd "$HOME/framejacker"
.venv-replay/bin/python scripts/hud_marker_check.py --validate --seconds 3
```

Expect `"status": "completed"`, `"exit_code": 0` and `"cleanup_ok": true`. Stop
here if it reports anything else.

#### Approved run

Only with approval for this run. All three opt-in flags and `--device` are
required; the tools refuse a partial opt-in. Over SSH, use the same graphical
session environment as the overlay stage in
[E55](status.md#e55-annotated-replay-overlay-command-on-frame), which ran
attached to the active `:0` display.

```bash
(
  set -e
  cd "$HOME/framejacker"
  test -z "$(git status --porcelain)"
  test -x artifacts/overlay-bridge/framejacker-overlay-bridge
  mkdir -p artifacts/hud-calibration
  run="$(mktemp -d "$PWD/artifacts/hud-calibration/run-XXXXXX")"
  git rev-parse HEAD > "$run/revision.txt"
  set +e
  .venv-replay/bin/python scripts/hud_marker_observer.py --observe-calibration \
    --allow-mirror-observation --allow-calibration-output \
    --device /dev/video99 --seconds 20 > "$run/observer.json"
  echo "$?" > "$run/exit-code.txt"
  echo "Saved $run"
)
```

During the run, the wearer keeps passthrough visible and looks at the scene
for the first two seconds. A completely unchanging view can end the run with
`baseline_distinct_clear_frames_not_established`. Once the marker tile appears,
they turn their head slowly through a small range and keep the tile in view.
Large movements, or a change in how many copies are visible, end the run
rejected instead of widening the footprint. Ctrl-C (or SIGTERM over SSH) stops
both tools.

#### Reading the result

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
  ([E59](status.md#e59-arcturus-green-input-recurrence-and-operator-recovery))?

If passthrough or tracking regresses, stop and keep the report. Switching to
native passthrough and back is a recovery workaround, not a pass. Hand the
saved directory and the answers to the evidence coordinator for a numbered
entry ([POC scope rules](poc-scope.md#rules-kept-and-changed)). A clean result
supports WP11 (feedback exclusion) planning. It does not enable live HUD input
or production HUD output.

### Check native capture prerequisites

Use the normal Frame terminal to collect the build metadata needed for a
capture-only provider. Arcturus is connected, but its selection as the current
passthrough source is unknown; this command does not select or identify it.
Update a clean checkout and run:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
python3 scripts/device_check.py --capture-prerequisites
```

This standard-library check reports architecture/OS, the C++ compiler version,
Vulkan `pkg-config` metadata, known header/library paths and existing community
capture configuration paths. It inspects file metadata without reading images,
connecting to a socket, loading Vulkan or initializing OpenVR. It does not
install packages, enable a layer, change settings or restart SteamVR.

Share the JSON report. Missing development files describe build prerequisites,
not a camera failure. A runtime `libvulkan.so.1` alone does not establish that
headers or linker files are installed. Search locations are limited, and
symlink targets are not verified. Do not unlock SteamOS or run the upstream
installer to fill gaps; choose the build approach from the report first.
The [development guide](development.md#dedicated-capture-build-prerequisites)
explains the distinction. The Frame report now confirms GCC 15.1.1 and Vulkan
1.4.309 development metadata/headers; see [E21](status.md#e21-capture-prerequisites-on-frame).
Compilation and real capture remain separate checks.

### Build the capture reference without activation

The pinned reference build is an intermediate compiler/linker compatibility
check. It has passed on cloud x86_64 and Frame ARM64, along with all 14 synthetic
reader tests on Frame; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame). Its
unmodified upstream code still needs the lifecycle and publication fixes in
the [capture review](capture-reference-review.md#findings-that-affect-our-implementation)
before deployment. It does not provide a runnable live-capture command.

In the existing Frame checkout, run:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh &&
bash scripts/build_shm_reader.sh &&
.venv-replay/bin/python -m unittest discover -s tests -p test_shm_capture_check.py -v
```

The reference build needs Bash, Git, Python 3, C++17 and a linkable Linux Vulkan
loader. It fetches pinned source from GitHub into ignored `.cache/`, preserving
notices and rejecting changed caches. Existing compiler-search-path Vulkan
headers are used when available; otherwise pinned headers are fetched. No
package installation or upstream script execution occurs. It writes two
non-executable inspection artifacts and `build-report.json` under ignored
`artifacts/capture-reference/`. No manifest or enable marker is generated, and
neither artifact is loaded or run. SteamVR and camera-source settings are untouched.

Share the build report and test summary. The existing shared-memory tests use
synthetic images in temporary ordinary files, not cameras or the compositor.
They check native ARM64 reader behavior without activating a provider. A passed
build/fixture test does not establish live-camera synchronization or compatibility.
Keep the reference artifacts for inspection; do not install them, add execute
permission or run `ptgrab` as the next step.

### Test the transport adaptation without capture

The first provider adaptation repairs descriptor ownership and bounded socket
messages. The adapted build and all seven fixture groups have now passed on
Frame ARM64; see [E25](status.md#e25-descriptor-transport-on-frame). Keep this
procedure for a changed implementation/runtime; the next check is publication below.

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --transport-fixes &&
artifacts/capture-transport-reference/framejacker-transport-fixture --self-test
```

The build applies our versioned patch to a disposable source copy, preserving
the pinned upstream cache and the earlier reference artifacts. It creates
separate non-executable capture inspection binaries plus an original synthetic
test executable. The explicit `--self-test` runs seven test groups using Unix
sockets and small generated `memfd` payloads; no camera or Vulkan calls occur.
The tests include partial records, malformed/extra descriptors, mixed generations,
stalls, disconnects and descriptor closure. A private temporary socket
directory is removed after its test. Share the build report and fixture JSON.

This remains a transport-only adaptation. GPU import, shared-memory publication,
provider lifetime and exact restoration still need work before deployment; no
capture binary is run and no layer configuration is created by this procedure.

### Test owned publication without capture

This adaptation replaces the producer's shared-memory writer and adds bounded
loop controls. All eight tests passed on cloud x86_64 and on Frame in 1.562
seconds; see [E27](status.md#e27-publication-and-loop-controls-on-frame).
Retain this procedure for changed code/runtime. The GPU query and original
import suite below have also passed; remaining engineering tasks are in the
[active plan](live-capture.md#offline-provider-work-while-the-frame-is-unavailable).
In the existing Frame checkout, run:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --publication-fixes &&
bash scripts/build_shm_reader.sh &&
.venv-replay/bin/python -m unittest discover -s tests -p test_capture_publication.py -v
```

This build applies both versioned patches to a disposable source copy and keeps
the pinned cache and earlier builds intact. Capture inspection binaries remain
non-executable in `artifacts/capture-publication-reference/`. The tests run an
original fixture using the same publication/control helpers as the adaptation,
plus the existing native reader. They create generated grayscale frames in
private temporary directories, validate both slots, preserve existing/foreign
files, and check deadlines, signals and parent death. No camera, Vulkan or
OpenVR calls occur. Share the build report and final test summary; expect eight
tests with no skips.

The future provider source is a private runtime file at
`$XDG_RUNTIME_DIR/framejacker-capture/frames.shm`; the upstream
`/dev/shm/mrhand-pt` file is not used by this publication adaptation. The tests
use temporary paths and remove their generated inputs. Normal publisher cleanup
removes only the identity-checked file it created. Abrupt parent death kills
the writer; a stale file can remain and a new publisher refuses to overwrite it.
Recovery/lease handling remains part of provider preparation.

The stream loop has a 1–10 second deadline and signal checks. GPU driver calls,
GPU resource cleanup, registry refresh, layer lifetime and exact restoration
still need work; this is not a live-capture procedure. No layer manifest or
enable marker is generated, and no capture binary is run by these commands.

### Query Vulkan capture capabilities

This check passed on Frame: Turnip Adreno 750 advertises the required import
capabilities, and the user reports normal passthrough throughout and afterward.
See [E30](status.md#e30-vulkan-capabilities-on-frame). Keep this procedure for a
changed tool/runtime; repeating it unchanged is not the next task. It queries
the GPU requirements of the reviewed Arcturus import path:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_gpu_check.sh &&
python3 scripts/capture_gpu_check.py --query-device
```

The builder compiles without loading Vulkan. The Python command starts a child
with a ten-second query budget, then terminates/kills and reaps a stuck worker.
The worker disables core dumps and is killed if its parent dies. Output sizes
and driver-provided counts are capped. With no arguments the Python command
returns `not_run`/exit `3` without starting a worker or loading Vulkan. Use the
Python command for the watchdog; do not run the native binary directly.

The opt-in loads the installed Vulkan loader, creates an instance, and queries
physical-device properties, features, memory/queues and image-format support.
It creates no logical device, queue, image or GPU allocation; it opens no camera,
connects to no provider and imports no FD. It installs no layer, changes no
source selection and restarts no service. Existing Vulkan configuration,
including already configured implicit layers, is honored during instance creation.

Share the complete build and query JSON, plus any change to normal passthrough.
`native.devices` lists GPU identity/API, missing required extensions, sampler
conversion, host-visible/cached memory types, graphics queues, R8 blit features
and image limits, and the NV12 `0x0500000000000001` modifier's **memory**-plane
count. Compressed memory planes are not necessarily the two NV12 color planes.
The external-image query uses DMA-BUF, sampled/transfer-source usage and
exclusive sharing; it reports importability and the dedicated-allocation flag.
The reviewed size is 2464 × 2464, with support for up to two array layers.
The query surveys that broader capability; the initial import adaptation below
accepts only a single layer because the wire record lacks array-stride metadata.

Exit `0`/`queried` means the queries completed, even if a device reports missing
capabilities. `capability_checks_passed` describes only the queried requirements,
not a working import or deployment readiness. Exit `1` means a query/driver,
format-report, timeout or cleanup failure; inspect the reason and native error.
`cleanup_ok: false` after forced termination means graceful Vulkan teardown was
not confirmed. Instance creation is `null` when no complete worker report exists.
Ctrl+C/SIGTERM yields `cancelled` and exit `128 + signal`. Invalid Python options
exit `2`. On success, the instance is destroyed before the report is emitted.

The Frame's loader API reports 1.3.296 and its physical-device API 1.4.362;
these differ from the earlier 1.4.309 development-header/pkg-config metadata.
The NV12 modifier reports two memory planes and dedicated-only DMA-BUF import.
Gamescope WSI/application-info and option-override messages appeared on stderr
alongside successful exit/cleanup; no passthrough change was reported.

The cloud has no usable Vulkan driver (`VK_ERROR_INCOMPATIBLE_DRIVER`); its
fake-loader tests validate dispatch and failure containment. The positive Frame
capability result still leaves actual FD import, producer
completion, camera identity, resource lifetime and layer restoration unverified.
Follow the [active provider work order](live-capture.md#next-provider-step-query-gpu-capabilities)
before any camera trial.

### Test import ownership without capture

The ARM64 build and original ten-test suite passed in 0.015 seconds with no
skips; see [E31](status.md#e31-import-build-and-synthetic-tests-on-frame). The
current suite adds one synthetic test for the two-memory-plane count advertised
by the real GPU. It passes in the cloud and has not yet been rerun on Frame.
Keep this procedure for changed code/runtime or batch it with a later native
validation milestone; there is no need to repeat the original checks unchanged:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --import-fixes &&
python3 -m unittest discover -s tests -p test_capture_import.py -v
```

Expect eleven tests with no skips for the updated suite. The build applies three
patches to a disposable copy and keeps the real capture binaries non-executable. Its original fixture
uses generated `memfd` data and fake Vulkan function pointers; it neither loads
Vulkan nor connects to a provider. It checks metadata rejection, allocation/FD
ownership, rollback, move operations and completion-gated cleanup. No camera,
layer configuration or service is changed. Share the build report and test
summary when convenient; no immediate Frame access is required for development.

The fixture's supported case is synthetic. It does not prove that actual
Arcturus buffers match the conservative profile, import successfully, have
finished being written or can be displayed without feedback. Complete the
[remaining provider work](live-capture.md#offline-provider-work-while-the-frame-is-unavailable)
before activation.

### Test GPU resource ownership without capture

The new resource-lifetime adaptation is cloud-tested; ARM64 execution is pending.
This optional check can be batched with a later milestone. It does not create
a real Vulkan device or open a camera:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --resource-fixes &&
python3 -m unittest discover -s tests -p test_capture_gpu_resources.py -v
```

Expect nine tests with no skips. The builder applies four versioned patches
and keeps the capture layer/producer non-executable. Tests run an original fake
Vulkan backend: startup/resource rollback, memory limits and device/child
lifetimes, including imported-image ownership. They call no actual Vulkan
loader/driver and use no camera data. Neither layer installation nor service
restart is performed. Share the build report and final summary if you run it.

Passing these tests would extend synthetic evidence to ARM64. Actual GPU
startup/import/copy/teardown and restoration on the device remain separate
checks. The newer milestone below implements the watchdog/provider/recovery
prerequisites and can be tested in one batch.

### Test the watchdog and provider without capture

The earlier v2 milestone passed all 28 tests on Frame ARM64; see
[E35](status.md#e35-watchdog-provider-and-recovery-tests-on-frame). The current
full regression includes v3 metadata-only changes. It uses generated files,
private local sockets and fake Vulkan dispatch, without opening a camera or
loading the layer. The actual capture artifacts remain non-executable:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_supervisor.sh &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_supervisor.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_config.py -v
```

Expect **10, 12, 12 and 7 tests**, respectively, with no skips. Share the provider
build report and test summaries when convenient. Existing ARM64 v2 success does not
establish ARM64 execution or live capture. This check changes no camera-source
setting, layer installation or compositor service.

To prepare an exact configuration backup and inactive bundle using this Frame's
home paths, choose a new artifact directory:

```bash
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-new
```

Expect `prepared_inactive_bundle`, with activation/restart both false. Existing
bundles are preserved; use another new directory if that name exists. The plan
backs up exact prior bytes/modes or absence for the proposed Framejacker manifest
and library. It creates no active manifest/library and does not set the required
enable environment variable. No install command is provided yet. Keep the bundle
local; do not commit its backups. Inspection library bytes in the bundle still
do not constitute a tested layer installation.

The recovery commands below are only for known future trials, not routine
setup. A stale publication run has its exact session directory in its supervisor
report; `--recover-session PATH` verifies its recorded process/file identities.
`capture_layer_config.py --recover-provider-runtime` verifies a dead provider's
recorded socket identity in the current `$XDG_RUNTIME_DIR`. Both refuse living
owners or foreign replacements. To restore a staged configuration that still
matches the bundle:

```bash
python3 scripts/capture_layer_config.py --restore artifacts/capture-layer-plan-new
```

Restoration preserves later edits and does not restart SteamVR or unload a
library already loaded by the compositor. A controlled restart is required if
it was loaded. There has been no Framejacker layer activation to restore yet.

The synchronization review found no coordinated producer handoff. The latest
layer therefore retains/exports no image FDs and the capture CLI is blocked.
Next validate metadata-only changes and prepare the controlled layer-load/
restoration procedure. UUID and
layout checks do not identify Arcturus versus built-in cameras. During a future
capture-only trial, verify the selected physical source, orientation, scene
motion, shutdown and normal passthrough before connecting inference or a VR
image panel. See the [active plan](live-capture.md#next-engineering-step-capture-worker-supervision).

### Test metadata-only inspection without capture

This check passed on Frame ARM64: all 24 tests, with no skips; see
[E37](status.md#e37-metadata-only-provider-and-diagnostic-on-frame). No unchanged
rerun is needed. The command is retained for regression testing. It builds
the metadata-only v3 layer and diagnostic, then runs generated peers and fake
driver calls. It does not load the layer, contact a real camera provider or
restart SteamVR:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
git rev-parse HEAD &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v
```

Expect **12 provider/layer tests and 12 metadata diagnostic tests**, with no
skips. The build report should say `provider_protocol_version: 3`,
`image_fd_export_enabled: false`, `layer_retains_image_fds: false` and
`deployment_ready: false`. Share the revision, build report and test summaries.
These checks do not establish actual driver execution or complete camera frames.

### Prepare the metadata-layer trial

The native v3 build and generated-data checks are complete. Next prepare an
inactive bundle from the tested build and collect service metadata. Run on
Frame in the existing checkout:

```bash
cd "$HOME/framejacker" &&
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-v3-new &&
systemctl --user show steamvr.service \
  -p LoadState -p ActiveState -p SubState -p MainPID \
  -p FragmentPath -p DropInPaths -p Type -p KillMode -p TimeoutStopUSec
```

Expect `prepared_inactive_bundle`, with activation and restart both false. If
the directory already exists, preserve it and use another new bundle name.
Keep any earlier v2 bundle unchanged. Share the preparation report and service
properties; keep the bundle's backups local.

The inactive bundle and service properties were received; see
[E38](status.md#e38-inactive-bundle-and-steamvr-service-on-frame). Preparation
writes only the bundle and the service query changes nothing. Keep that bundle
unchanged. The next procedure builds the later loader fallback/timing fixes and
requires a fresh bundle before any activation.

### Run the bounded metadata-layer trial

**Paused after service-control failures on Frame; do not repeat the
trial.** Current `--start-metadata-test` returns `blocked`/exit 3 before any
runtime or file action. Reporting, read-only preflight and known-run recovery
remain available. Normal UI/passthrough work; see
[E41](status.md#e41-frame-trial-report-and-service-control-failures).
The retained procedure below describes the earlier trial for investigation.

The earlier experiment restarted SteamVR for the test and again for restoration.
The headset view/browser/console could disappear. The trial used a separate user service and systemd ran
its post-exit recovery independently of the console. This is the first actual
layer-load/restoration trial; the cloud has tested generated data, not this
device operation. It requests no image data and runs no recognition or overlay.

The earlier preparation command is retained below for investigation; do not
use it to resume activation. Preserve existing bundles:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_service_lifecycle.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_trial.py -v &&
python3 scripts/capture_layer_config.py --prepare artifacts/capture-layer-plan-v3-trial &&
python3 scripts/capture_layer_trial.py --preflight artifacts/capture-layer-plan-v3-trial
```

Current suites contain **14, 12, 9 and 48 tests**, with no skips; the original
preparation expected `prepared_inactive_bundle`
and `preflight_passed`. If the new directory already exists, preserve it and
use a different new name throughout. Preflight requires matching current
build/helper hashes, absent prior Framejacker active targets, no existing
provider/enable flags, and the reported active simple/control-group service
with a 20-second stop timeout and no drop-ins. The revised preflight also needs
an idle job and a live compositor in the original unified cgroup. It refuses changed conditions
without restarting anything. This trial does not support replacing an existing
Framejacker installation.

The earlier activation command is now blocked; it is retained for reference:

```bash
python3 scripts/capture_layer_trial.py --start-metadata-test artifacts/capture-layer-plan-v3-trial
```

Before touching SteamVR, the launcher runs a dummy one-second timeout to verify
this Frame's systemd executes the cleanup command. Failure refuses activation.
After a passing rehearsal it schedules `framejacker-metadata-trial.service`,
which stages only the metadata layer and a temporary runtime drop-in scoped
to `steamvr.service`. It waits for the provider and inspects metadata for two
seconds. Systemd's separate `ExecStopPost` then stops SteamVR, restores the
recorded targets, removes its exact enable drop-in, and starts normal SteamVR.
The normal path can finish sooner; allow **about three minutes** for timeout
and recovery. A launch acknowledgment means scheduled, not completed. If it
says `launch_unconfirmed`, inspect that run before attempting another launch.

Restoring SteamVR does not relaunch the terminal/console application. Open a
terminal independently, or use SSH from another computer, then read reports:

```bash
cd "$HOME/framejacker" &&
python3 scripts/capture_layer_trial.py --report-latest
```

Success is `status: completed`, with `trial.status: inspected`,
`rollback.status: restored`, `configuration_restored: true` and
`normal_runtime_observed: true`, plus either a confirmed normal-service start
or `normal_service_already_running: true`. The metadata peer must be a `vrcompositor` in
the service cgroup. Zero compatible candidates is still a metadata result,
not camera access. `incomplete`/exit 3 means the final recovery report is absent;
`failed`/exit 1 must not be treated as restoration success. Share the report
and whether normal passthrough recovered. No images are saved.

From another computer, read the same report without restarting or activating
anything on Frame:

```bash
ssh steamos@<FRAME_IP> 'cd "$HOME/framejacker" && python3 scripts/capture_layer_trial.py --report-latest'
```

If SSH cannot connect, first establish whether the normal headset interface
and passthrough work and whether the full boot sequence occurred. Console loss
alone does not prove a headset reboot or failed configuration restoration.
Do not restart the trial to recreate the symptom.

If recovery fails, use the known `trial_directory` from the launch/result
report. From a separate terminal/SSH session, stop any remaining trial first:

```bash
systemctl --user stop framejacker-metadata-trial.service
```

Then use `python3 scripts/capture_layer_trial.py --rollback /exact/trial-directory`,
substituting that recorded path. It refuses a living worker and preserves
foreign/later edits; it never guesses paths from a wildcard. Keep the report if
it refuses or normal SteamVR does not start. A failed manager/service stop
cannot guarantee restoration. Backups stay local in ignored private artifacts.

For a **future already running v3 metadata provider**, the diagnostic command is:

```bash
python3 scripts/capture_provider_check.py --inspect --seconds 2
```

Do not run it merely to retry a missing provider; the generated tests above
exercise the consumer now. The default without `--inspect` does nothing and
exits 3. An existing provider is queried for metadata and registry stability
only, with no Vulkan calls, image descriptors, pixel reads or file outputs.
Success is `inspected`/exit 0; zero candidates or unsupported metadata still
produce useful diagnostic results. Missing/old providers, generation changes,
unexpected descriptors and deadline failures exit 1. Cancellation exits
`128 + signal`. Capture remains blocked until the
[producer handoff](live-capture.md#producer-synchronization-review-and-next-checkpoint)
or a synchronized compositor-owned copy is established.

### Read the service failure window

The saved reports are now received. Rollback reports configuration restoration,
SteamVR is active with no drop-ins, and the user confirms working UI/passthrough.
Do not run another trial or rollback to reproduce the error. The failed worker
and rollback operation is not identified by the earlier generic error string;
the supplied SteamVR tail starts after the failure window.

On Frame, read the existing journal for 10:36:30–10:37:05 Eastern on 2026-10-17
(14:36:30–14:37:05 UTC), plus current service policy. No restart, configuration
change or image access is performed:

```bash
journalctl --user -u steamvr.service -u framejacker-metadata-trial.service \
  --since '2026-10-17 14:36:30 UTC' --until '2026-10-17 14:37:05 UTC' \
  --no-pager -o short-iso -n 200 \
  --grep='(?i)fail|error|denied|refus|cancel|dependency|start|stop|job'
systemctl --user show steamvr.service \
  -p RefuseManualStart -p RefuseManualStop -p Restart -p RestartUSec \
  -p NRestarts -p Result -p ExecMainStatus -p ActiveEnterTimestamp
```

Share this output. It does not expose the service environment or backup plan.
If the journal has no matching entries, record that result; do not restart the
experiment to generate more logs. The corrected runner adds phase and safe
operation diagnostics for offline testing; it remains blocked for activation.

### Inspect the service relationships without restarting

The focused journal and policy query were received; see
[E42](status.md#e42-focused-service-window-and-restart-policy-on-frame).
Manual start/stop are allowed. The worker failed while SteamVR was stopping;
SteamVR started again after the rollback error. `Restart=always` alone does
not explain that sequence. Keep the working headset running and do not repeat
the trial or rollback.

Pull the diagnostic update, then query service relationships and executable
paths on Frame. This changes repository files only; the diagnostic performs
one `systemctl show` query and makes no runtime/configuration change:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
python3 scripts/capture_layer_trial.py --service-details
```

Expect `status: service_details`, exit 0, `activation_requested: false`, and
service relationships plus executable paths for start/stop hooks. Command
arguments and environment values are omitted. The corrected inspector preserves
all repeated hooks in order; the earlier E43 report contained only each field's
last hook. Source collection has already supplied the full inventory, so there
is no need to repeat this query just to continue the review. No
rebuild is needed. The activation pause stays in place while the unit
relationships/launcher behavior are reviewed.

### Read the platform launcher definitions

The service relationship report was received; see
[E43](status.md#e43-steamvr-session-relationships-and-platform-hooks).
The initial relationship report identified the platform selector and Bash
hooks, but omitted earlier repeated hooks. The following collector was used
to read the shipped unit and selector without executing them.

**Received and reviewed:** the user supplied this output; see
[E44](status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix).
Keep the command below as a reference, not another required collection step.
The unit requests startup before tracking a waiter and runs health-check and
display cleanup hooks on shutdown. Do not execute the selector as a probe: its
fallback can change the selected SteamVR installation. The
[offline lifecycle model](live-capture.md#steamvr-service-control-acceptance)
is now implemented and cloud-tested. The next device step below reads runtime
metadata without a restart.

```bash
python3 - <<'PY'
from pathlib import Path
import re

for name in ('/usr/lib/systemd/user/steamvr.service',
             '/usr/share/deckard/select_steamvr.sh'):
    print(f'\n--- {name} ---')
    try:
        text = Path(name).read_text()
    except OSError as error:
        print(error)
        continue
    if name.endswith('.service'):
        continuation = False
        for line in text.splitlines():
            selected = continuation or re.match(r'^\s*(?:Exec[A-Za-z]*|Type)\s*=', line)
            if selected:
                print(line)
            continuation = bool(selected) and line.rstrip().endswith('\\')
    else:
        print(text)
PY
```

The unit portion includes only Exec*/Type definitions and
their continuation lines; environment/credential directives are omitted. The
selector is read as static source, not run. No runtime environment or credential
file is read. No additional output from this collector is needed now.

### Check runtime liveness without restarting

The [offline recovery work](status.md#e45-offline-platform-lifecycle-and-guarded-recovery)
passes 65 targeted cloud tests. **Frame result received:** the runtime observation
passes with an active/idle service and a live compositor in its original cgroup;
see [E46](status.md#e46-read-only-runtime-baseline-on-frame). Do not repeat this
successful check just to continue the review. The command remains available as
a read-only reference, without a rebuild or inactive bundle:

```bash
cd "$HOME/framejacker" &&
git pull --ff-only &&
python3 scripts/capture_layer_trial.py --runtime-status
```

Expect `status: runtime_observed`, exit 0, `activation_requested: false`,
`normal_runtime_observed: true`, and `rendering_readiness_verified: false`.
The runtime observation should show active/running/success, no pending job,
a populated original cgroup and a live compositor identity. The JSON is received,
and the user confirms normal passthrough during/after. Independent SSH access
is not verified. A missing/unreadable cgroup or
absent compositor returns `runtime_unconfirmed` or `refused`, exit 1; retain the
report and do not restart the experiment to change the result.

This mode reads only allowlisted service properties and cgroup/proc metadata.
It performs no configuration writes, service start/stop/reload, OpenVR/Vulkan
calls, provider connection, pixel read or image save. It checks process liveness,
not actual rendering. A passing report does not unblock activation. Independent
terminal/SSH access and a reviewed restoration/readiness procedure are still
required before any later device rehearsal.

The offline suites can also run on Frame without touching its VR runtime:

```bash
python3 -m unittest tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

Expect 65 tests. The configuration suite's generated-provider test requires the
previously built `--provider-fixes` fixture; if absent, that test skips. Record
the actual pass/skip totals. These tests do not validate real systemd jobs.

### Verify independent SSH access

The runtime baseline and normal passthrough are confirmed. Before planning a
service-control rehearsal, verify access that does not depend on the headset's
VR console. On **another computer on the same network**, run:

```bash
ssh -o ConnectTimeout=10 steamos@<FRAME_IP> 'hostname && id -un'
```

Expect the Frame hostname (`frame`) and account name (`steamos`). If prompted
for a password, use the Frame account's password. This command reads host/user
information only; it does not restart SteamVR, enable a layer or access the
camera. Run it from the other computer, not from the Frame console itself.

Share the output or connection error. If it fails, preserve the working
headset session and diagnose remote access before any trial. Once access is
verified, review the bounded recovery procedure before reassessing activation;
the access check alone does not validate service restoration.

### Service-only recovery rehearsal result

`scripts/service_rehearsal.py` stops and restarts `steamvr.service` once,
without preparing or installing a capture layer. Its design and tests are in
[development](development.md#service-only-recovery-rehearsal). Independent SSH
from the PC is verified, and the first Frame run is recorded as
[E63](status.md#e63-service-only-rehearsal-and-graphical-session-dependency).

What happened on Frame: the SteamVR stop job was **canceled**. The shipped
Gamescope session is `PartOf=steamvr.service`, so stopping SteamVR also
stopped the graphical session, which then restarted. Independent cleanup later
observed a healthy compositor without issuing another service command. The
rehearsal itself is a failure; the runtime recovered on its own.

Current guard: `--preflight` (and so `--start`) now refuses to run while
Gamescope is `PartOf` SteamVR, reporting
`steamvr_stop_propagates_to_graphical_session`. On this runtime a bare
SteamVR stop is not a safe recovery model. Use the
[platform session test](#prepared-platform-session-test) for session-level
recovery instead. Do not edit service policy to get past the guard.

To confirm the guard or read the saved result, run on Frame:

```bash
cd "$HOME/framejacker"
python3 scripts/service_rehearsal.py --preflight
python3 scripts/service_rehearsal.py --report-latest
```

Both are read-only. With no mode the tool does nothing (status `not_run`,
exit 3). `--start` additionally requires an inbound SSH session from another
computer and refuses if a trial unit already exists. `--recover RUN` acts only
on a known run after its worker has exited. Neither is needed while the guard
holds.

### Prepared platform session test

`scripts/session_rehearsal.py` asks SteamOS Manager for one
`switch-to-game-mode` session cycle (`/usr/bin/steamosctl switch-to-game-mode`)
and observes recovery independently. It never stops or starts SteamVR
directly and never installs a layer. Design and tests are in
[development](development.md#platform-managed-session-rehearsal).

Result: the operator's session test **completed successfully**, with healthy
independent recovery and the wearer confirming a normal headset
([E64](status.md#e64-platform-session-test-completed-and-headset-confirmed)).
A completed run is also the `--session-proof` the
[metadata session trial](#prepared-metadata-session-trial) requires.

A session cycle **logs out the graphical session**: games, the replay window,
VR panels and anything else in it close. Repeat it only for a changed platform
(SteamOS, SteamOS Manager or Gamescope session files), and only with approval.

```bash
cd "$HOME/framejacker"
python3 scripts/session_rehearsal.py --preflight
bundle="artifacts/session-bundle-$(date +%Y%m%d-%H%M%S)"
python3 scripts/session_rehearsal.py --prepare "$bundle"
python3 scripts/session_rehearsal.py --check "$bundle"
```

`--preflight` reads only the platform and runtime. `--prepare` writes a new,
exclusive, inactive bundle of helper sources, hashes and platform fingerprints,
and refuses an existing path. `--check` must report `prepared_test_ready`; it
refuses changed platform files, foreign drop-ins or existing capture
configuration.

The approved run must be started **from an SSH session on another computer**,
so the session that is logged out is not the one running the command:

```bash
cd "$HOME/framejacker"
python3 scripts/session_rehearsal.py --start "$bundle" --allow-session-logout --wait
python3 scripts/session_rehearsal.py --report-latest
```

`--start` without `--allow-session-logout` is refused. The worker sends exactly
one request and never retries, even after a failure or timeout, because the
request may still have started a logout. `--wait` waits at most 180 seconds for
the saved result; disconnecting does not cancel the run. Completion needs new
invocation IDs for Gamescope, SteamVR and both session targets, all healthy
and stable for two seconds. A partial restart cannot pass. Process liveness is
not the wearer's check: ask whether the UI, passthrough and tracking look
normal afterward, and record that separately.

### Prepared metadata session trial

`scripts/metadata_session_trial.py` runs a metadata-only provider activation
and restoration across two platform session cycles. It uses the same vendor
session request as the [platform session test](#prepared-platform-session-test),
not a bare SteamVR stop. No image descriptors, camera bytes, detector or VR
output are involved; the v3 provider still rejects buffer requests. Design,
ownership rules and tests are in
[development](development.md#metadata-trial-across-platform-sessions).

Results so far
([E65](status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed)):

- First run: the provider loaded and normal startup was restored. It received
  20 metadata records, including eight candidate-profile matches, but failed
  with `provider_registry_changed`.
- v2, with bounded settling: three snapshots held identical metadata for the
  same eight candidate IDs, while other allocations appeared and disappeared.
  Full-registry inspection still failed. Software restoration and normal UI,
  passthrough and tracking were confirmed.

**Do not repeat v2 unchanged.** The next step is offline work on scoped
lifetime and version tracking. Use the commands below only for a changed
version that has been reviewed and approved. The trial performs **two**
session logouts, so the same warnings as the session test apply.

Inputs: a verified v3 metadata bundle from
[Prepare the metadata-layer trial](#prepare-the-metadata-layer-trial), the
metadata inspector built with `bash scripts/build_capture_reference.sh
--provider-fixes`, and a completed session-test run directory under
`artifacts/session-rehearsals/` on the same platform.

```bash
cd "$HOME/framejacker"
trial="artifacts/metadata-session-$(date +%Y%m%d-%H%M%S)"
python3 scripts/metadata_session_trial.py --prepare "$trial" \
  --metadata-bundle artifacts/capture-layer-plan-v3-NAME \
  --session-proof artifacts/session-rehearsals/RUN
python3 scripts/metadata_session_trial.py --check "$trial"
```

Replace `NAME` and `RUN` with the existing bundle and completed session run.
`--prepare` refuses a session proof that did not complete or that came from a
different platform. `--check` must report `metadata_session_test_ready`. The
approved run, again from an SSH session on another computer:

```bash
cd "$HOME/framejacker"
python3 scripts/metadata_session_trial.py --start "$trial" --allow-session-logouts --wait
python3 scripts/metadata_session_trial.py --report-latest
```

`--wait` waits at most six minutes. Keep the whole run directory: every raw
inspection attempt and the settling summary are saved, and a later success
does not hide earlier invalidations. Cleanup removes only the trial's own
drop-in and manifest and requests one normal session. Library and source
artifacts are kept for diagnosis. Report the result, the restoration fields and
the wearer's answers about UI, passthrough and tracking separately.

### Deployment preflight and command shape (not authorized)

[Development](development.md#offline-foundation-for-the-minimum-live-poc)
refers to a local eight-entry deployment manifest (the ARM64 bridge plus seven
Python modules) with verified sizes and hashes, and to a placeholder deployment
command. **Neither the manifest nor that command is in this repository.** This
section therefore gives no deployment command. No install, transfer or device
execution is authorized here.

That historical file list is also insufficient for the current adapters:
`scripts/overlay_client.py` now imports `framejacker.overlay`, and the mirror
reader imports `framejacker.errors`. A new manifest must include the complete
package/script dependency closure and matching source revision, plus the
target-built ARM64 bridge. This delivery's local x86_64 bridge is an offline
validation artifact, not a device deployment candidate.

Before any deployment is proposed, the preflight must establish at least:

- The exact commit and a manifest listing every deployed file with its size
  and SHA-256, compared on Frame before use.
- That cross-compiled or Windows-built artifacts are not taken as evidence of
  Frame runtime compatibility. The bridge used on Frame so far has been built
  on Frame with `bash scripts/build_overlay_bridge.sh`.
- Target ARM64 UAPI, `ctypes` and `ioctl` size execution evidence before any
  capture path runs. The cloud ABI test passes with its own compiler/UAPI;
  that does not establish the target headset's ABI.
- Independent SSH, a clean checkout, and nothing else running, as in the
  [calibration checkpoint](#bounded-edge-hud-calibration-checkpoint).

Commit the manifest and its preflight script, then replace this section with
the real command shape when that work is reviewed.

### Check existing luma shared memory

This is a separate diagnostic for an **already running** compatible provider.
Framejacker has not installed or started that provider. With no flags the
command only checks file metadata; it does not read a header, map pixels or
start capture:

```bash
python3 scripts/shm_capture_check.py
```

`source.present: false` is expected when no provider publishes
`/dev/shm/mrhand-pt`. A present file alone does not prove live camera input.
Do not install a layer merely to make this check report a file.

Build the reader on Frame using its existing Bash and C++17 compiler:

```bash
bash scripts/build_shm_reader.sh
```

The build downloads nothing, installs no packages and starts no producer. It
creates native binaries in ignored `artifacts/shm-reader/`; do not copy the
cloud's x86_64 binaries onto ARM64 Frame. To exercise generated images first,
use the [synthetic procedure](development.md#isolated-luma-reader-and-synthetic-producer).
That checks the reader on the device without touching a camera or compositor.

Only when a compatible provider is already running, explicitly read one slot:

```bash
python3 scripts/shm_capture_check.py --read --camera 0 --seconds 10
```

The source must be an ordinary file, not a symlink, device or FIFO. It is opened
read-only. The reader validates the pinned native layout, exact file length,
dimensions, slot count and per-slot sequence before and after copying. Copies
are capped at 10 per second and 16 MiB per slot, with at most two slots. It
rejects incomplete/changing copies, old or invalid observation timestamps,
observable counter resets and changes to the file identity, size or header.
Slot numbers do not establish left/right or physical camera identity.

`native` reports accepted frames, observed rate, dimensions, gray8 format,
sequence/frame counters, stale/busy/rejected copies and cleanup. The default
maximum observation age is 1,000 ms; `--max-age-ms` changes this diagnostic
threshold. `--frames N` permits an earlier exit after N accepted publications.
These are provider observation times on `CLOCK_MONOTONIC`, not exposures.
Writer ordering and original camera-frame integrity remain explicitly
unverified even when a copy succeeds. See [status](status.md#e9-shared-memory-reader-cloud-checks).

No images are saved by default. To request a grayscale still, choose a new PNG
path in an existing directory:

```bash
mkdir -p artifacts/shm-check
python3 scripts/shm_capture_check.py --read --camera 0 --seconds 10 \
  --preview artifacts/shm-check/new-preview.png
```

The input is unmapped and closed before preview output is written. The still
fits within 640 × 640 and is published only after successful reader shutdown;
existing destinations are preserved. Inspect it after the command ends.

The supervisor allows the requested duration plus three seconds, then up to
two seconds of terminate/kill cleanup. Ctrl+C or SIGTERM stops and reaps the
reader. A concurrent truncation can fault a native mapping; the supervisor
reports that failure and never publishes a preview. Forced shutdown does not
claim normal source cleanup. Exit codes are `0` for metadata inspection or
successful capture, `1` for unavailable/stale/malformed input or another error,
`2` for invalid arguments, and `130`/`143` for cancellation. Retain the JSON
instead of restarting services or retrying a disruptive camera path.

### Advanced probe, only for a deliberate future retest

Building the probe does not initialize OpenVR:

```bash
bash scripts/build_camera_probe.sh
artifacts/camera-probe/framejacker-camera-probe --help
```

The build uses Git, Bash and a C++17 compiler. It needs no `uv`, Python packages,
OpenGL or Vulkan development packages. It fetches the pinned Valve OpenVR SDK
sources into `.cache/openvr` and compiles the portable loader for the current
machine. The binary goes in `artifacts/camera-probe/`. Both paths are ignored by
Git. A modified or different cached SDK causes an error rather than a reset.

Running the binary without arguments emits a `not_run` JSON report and exits
with code `3`, before any OpenVR calls. A deliberate runtime test requires the
explicit `--allow-passthrough-interruption` flag. This flag acknowledges the
observed disruption; it does not make the capture route work.

When explicitly enabled, the probe uses the public `IVRTrackedCamera_006`
interface in a background OpenVR application. It queries presence, count/layout,
frame dimensions and calibration statuses, then requests a shared camera stream.
It checks header-only retrieval for all three frame types and retries failed
size queries after acquisition.
Where dimensions are valid, it attempts an actual RGBA frame copy. A successful
handle alone cannot pass the check.

The retrieval loop has a shared three-second polling budget and a 64 MiB buffer
limit; synchronous runtime calls can add time. It reports metadata and named
errors as JSON, saves no images, and attempts to release the stream before
shutting down OpenVR. It does not deliberately change SteamVR settings, open physical video
nodes, install compositor layers, or select an RGB/monochrome source, but its
runtime calls have interrupted passthrough on the user's device. A received
frame would not by itself identify the Arcturus RGB source.

| Exit code | Meaning |
| --- | --- |
| `0` | Explicit stream test copied a frame with valid RGBA metadata and stream release succeeded |
| `1` | Runtime initialization, interface access, cleanup or another probe error; inspect the JSON |
| `2` | Runtime/interface access succeeded, but no valid frame was received |
| `3` | Probe was not run because the interruption flag was absent; no OpenVR calls were made |
| `64` | Invalid command-line arguments; no OpenVR calls were made |

`--help` prints usage and exits `0` without accessing OpenVR.

An unavailable runtime registry or `NoServerForBackgroundApp` is a runtime/session
problem, not proof of unsupported camera hardware. Keep the existing home/XDG
and OpenVR runtime configuration; the loader discovers SteamVR through its
registered paths. Background initialization does not launch SteamVR.

For a deliberate future retest after a material change, this command saves a
report and preserves the probe's exit status. It can black out passthrough; do
not use it to repeat the already failed test on the unchanged runtime:

```bash
(
  set -e
  bash scripts/build_camera_probe.sh
  framejacker_probe_status=0
  artifacts/camera-probe/framejacker-camera-probe --allow-passthrough-interruption \
    > artifacts/camera-probe/report.json || framejacker_probe_status=$?
  python3 -m json.tool artifacts/camera-probe/report.json || cat artifacts/camera-probe/report.json
  printf 'Probe exit code: %s\n' "$framejacker_probe_status"
  exit "$framejacker_probe_status"
)
```

Keep any future report with the tested runtime version. No public live-capture
SDK was found in the sources reviewed. Seek a supported Arcturus or Valve
live-capture contract first. The [capture plan](live-capture.md) also defines
an isolated evaluation of the experimental community Vulkan path, which has
not been deployed by this project. Neither an active service nor listed sensor
nodes replace actual frame retrieval.

Do not copy the cloud machine's x86_64 `.venv` to Frame. The existing dependency
pins and smoke setup were validated on Linux x86_64, not the headset; verify
native interpreter, wheel and library availability before installing them there.
The device inventory requires no such installation. Leave compositor-layer
deployment and camera-source changes out of this initial check.

Use the [overlay guide](vr-overlay.md) to resume recorded playback and the
[status record](status.md) to retain new measurements. For development tasks,
follow the [active capture work order](live-capture.md#current-work-order).
The [architecture milestones](architecture.md#milestones-and-acceptance-checks)
describe later acceptance criteria, including spatial labels and offline splats.

## Context for a new development session

```text
Continue moral-kombat/framejacker on this Steam Frame.
Read docs/README.md and docs/status.md first, then docs/on-device.md.
Confirm where this terminal executes before running device commands.
Use docs/vr-overlay.md to resume recorded replay and the head-relative panel.
The public camera probe returned no frames and interrupted passthrough.
Do not repeat it on the unchanged runtime; its explicit flag is for a deliberate
future retest after a material change.
Read docs/live-capture.md#current-work-order for the next engineering task.
The corrected mirror reader received 100 complete 1920x1080 RGB3 frames from
/dev/video99 in ten seconds with changing samples and normal cleanup.
An explicit 640x360 preview was saved at artifacts/mirror-check/scene-check.png.
The user recognized a black-and-white photo of the room via its absolute file URL.
The one-image detector ran, but a rug was mislabeled sofa; the demo has no rug class.
Bounded mirror recognition then processed 51/100 reads at 4.993 fps with normal
passthrough and clean shutdown. Earlier labels were chair/tvmonitor; the final
frame had no detections. The fixed-panel test then confirmed our panel appears
in mirror input; its grayscale/no-label output was expected because no detector ran.
Arcturus is connected; its selection for passthrough is unknown. The latest
prerequisite report is aarch64 SteamOS 0.5.6 build 20261015.6141207, GCC 15.1.1
and Vulkan 1.4.309 with development headers. Earlier capture tests have their
own runtime evidence; do not silently attach this newer build to them.
The pinned reference compiled/linked on aarch64 with system Vulkan headers; all
14 synthetic reader tests passed in 7.204 seconds. The descriptor transport
adaptation also passed seven fixture groups on ARM64. The publication build
completed and all eight generated-data tests passed in 1.562 seconds on Frame.
The bounded Vulkan query passed on Turnip Adreno 750: required extensions,
graphics family 0, cached host memory, R8 blits and dedicated DMA-BUF imports are
advertised, with two memory planes for the NV12 modifier. Loader API is 1.3.296;
device API is 1.4.362. Normal passthrough stayed working during and after it.
The validated-import inspection adaptation compiled on aarch64 and passed all
ten original synthetic tests in 0.015 seconds. A new two-plane fixture test now
passes in the cloud; that eleventh test has not run on Frame. Actual camera
imports remain unverified. Both original device checks are complete. The v2
watchdog/provider/recovery milestone also passed all 28 tests on Frame ARM64.
Producer synchronization review found no coordinated handoff, so the latest v3
provider records metadata without retaining/exporting image FDs and blocks live
reads. The earlier v3 ARM64 build and both suites (12 provider/layer tests and
12 metadata-diagnostic tests) passed. The inactive bundle and active SteamVR
service properties were received. A bounded metadata-only trial with independent
systemd post-exit recovery was prepared for a trial. The received d5892f4 report
shows a passing dummy recovery rehearsal, followed by worker and rollback
systemctl errors. Configuration restoration is reported; SteamVR was later
active/no drop-ins, with working UI/passthrough. No inspection report was produced
and actual layer loading is unverified. Activation is blocked while a focused
read-only service query diagnoses the service failure. The focused journal
shows stopping at 10:36:41, worker failure at :42, stopped/rollback error at :46,
and startup at :47–48. Manual start/stop are allowed; Restart=always and a zero
restart counter do not establish what initiated startup. Next read service
relationships with --service-details. Do not repeat the trial or rollback on
the working headset. Current trial tests number 24 in cloud.
Physical camera identity and scene freshness remain unverified.
GPU device/resource ownership is implemented in --resource-fixes and has nine
synthetic cloud tests passed, including 57 injected startup/resource errors.
Its native ARM64 and actual driver execution remain untested; real capture
binaries are still non-executable inspection artifacts.
The isolated shared-memory reader and synthetic fixture are implemented and
tested on cloud x86_64 and Frame ARM64; real-provider validation and capture-layer
deployment are pending.
Since 2026-10-25 the replay and mirror commands run on the shared framejacker
pipeline (cloud and CI only so far; reconfirm replay/overlay and mirror recognition
on Frame). Re-run scripts/setup_window.sh, then compare detectors on Frame stills:
docs/on-device.md#compare-detectors-on-frame-stills. Next steps are in
docs/design-review.md#next-steps.
Keep user observations, cloud checks and upstream capture claims distinct.
Record new results in docs/status.md, including unknown dates/versions explicitly.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
