# Continue development on Steam Frame

Use this guide for device access, inventory and recorded-video replay. Start
with [status and evidence](status.md) for compatibility and verified results,
then the [overlay guide](vr-overlay.md) to run the VR panel. The
[active capture plan](live-capture.md#current-work-order) owns upcoming live-input
work. The mirror diagnostic below is implemented; dedicated camera capture and
its shared-memory reader remain planned.

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

The report includes driver/capabilities, current format, `frames_read`, observed
read rate, maximum read gap, changed/unchanged grayscale samples, short reads
and worker cleanup. The sampled grid is at most 32 × 32. Identical samples can
mean a static scene; changed samples cannot prove a live physical camera.
Neither V4L2 producer sequence nor exposure timestamps are supplied by this
read-based diagnostic. Physical source identity stays explicitly unverified.

| Exit code | Meaning |
| --- | --- |
| `0` | Discovery completed, or capture read images and confirmed normal cleanup; inspect mode/status |
| `1` | Unavailable/unverified input, unsupported format/capabilities, no frames, deadline or another diagnostic error |
| `2` | Invalid command-line arguments; capture did not start |
| `130` / `143` | Cancelled by Ctrl+C / SIGTERM |

Retain the JSON for unavailable/unsupported results. An unsupported format is a
limit of the current reader, not proof that the mirror is unusable or the camera
is faulty. Do not activate a new mirror service or force a physical camera path
to make this check pass. Device capture and ARM64 execution remain pending;
see [cloud evidence](status.md#e8-mirror-diagnostic-cloud-checks) and the
[active plan](live-capture.md#current-work-order).

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
The mirror diagnostic is implemented; run python3 scripts/mirror_check.py first
for metadata-only discovery. Actual mirror acquisition remains unverified.
The dedicated-camera shared-memory reader is still planned.
Keep user observations, cloud checks and upstream capture claims distinct.
Record new results in docs/status.md, including unknown dates/versions explicitly.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
