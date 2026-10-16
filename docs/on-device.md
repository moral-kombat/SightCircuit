# Continue development on Steam Frame

The Arcturus Camera app has been confirmed by the user to show live passthrough
and record video on the headset. Our public OpenVR probe returns no frames and
blacks out passthrough while it runs. File decoding and the annotated replay
window now work on Frame, as confirmed by the user. The repository is published at
[moral-kombat/framejacker](https://github.com/moral-kombat/framejacker).
The file-replay window also passes offscreen cloud tests. The live capture
backend is unimplemented. The user has also confirmed the native VR panel's
playback, controls and image following their head. See the
[overlay guide](vr-overlay.md) to launch the confirmed replay path or test a new
runtime. Next, check detection quality, readability and playback performance.

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

The first user-supplied inventory confirms an `aarch64` SteamOS 0.4.5 session
(build `20261007.6125817`), Python 3.12.3, an active `steamvr.service`, native
`g++`/CMake/Ninja tools, a render node and `arcimx616` sensor-driver nodes. This
establishes the target context; device-node presence alone does not demonstrate
working capture.

Establish the baseline using the [official Arcturus Camera app][arcturus-camera]
and [vendor setup instructions][arcturus-howto]. The user has confirmed that this
app displays live passthrough and records video. This demonstrates a working
vendor camera path, even though our public API probe fails. A file/replay backend
can use a short recording to develop detection and the output window while live
capture remains unresolved. Preserve the original recording and its metadata;
inspect its video tracks before choosing a decoder or assuming a single stream.

### Confirmed public-probe result

The user's reports before and after toggling passthrough are unchanged:

- Background OpenVR initialization and `IVRTrackedCamera_006` access succeed.
- `HasCamera` returns true, but camera count is zero. All three frame-size
  queries return `OperationFailed` (`100`), including after stream acquisition.
- Acquisition reports success with a nonzero handle. Header-only retrieval and
  stream release return `InvalidHandle` (`101`); no image frames are copied.
- **Passthrough goes black while the probe runs.** The official camera app's
  live view and recording work outside that test.

These observations establish that this public probe is unusable on the tested
runtime and can interrupt the displayed camera view. They do not identify the
particular runtime call responsible or prove a camera hardware fault. The
schema-1 `handle_valid` field means only that the returned handle was nonzero;
it is not evidence that the runtime accepts it. Do not repeat the unchanged
probe on this runtime. A future retest needs a material change, such as a runtime
update or a revised capture implementation.

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
the input to the replay implementation. A recording is replay input, not a live
capture backend.

The user has listed two completed recordings on Frame:
`2026-10-15_21-20-34_stereo.mp4` (15,923,629 bytes) and
`2026-10-15_21-17-18_stereo.mp4` (149,778,747 bytes). No `ffprobe` executable was
reported on PATH. The metadata command is:

```bash
python3 scripts/recording_check.py \
  "$HOME/Videos/ArcturusVisionCamera/2026-10-15_21-20-34_stereo.mp4"
```

The user ran the inspector on the smaller recording. It reported two `hvc1`
(HEVC) video tracks at 2464 × 2464, one `gpmd` metadata track, one `mp4a` audio
track, fragmented MP4 structure and no encrypted sample entries. Its
`unsupported: fragmented_mp4` result describes the metadata inspector's limited
sample/timeline handling. Fragmentation alone does not establish a corrupt or
undecodable file. The video `mdhd` headers report 0.515174 seconds; the full
fragmented timeline has not been validated.

The user subsequently installed PyAV 16.1.0 on Frame and decoded three frames
from video-stream ordinal 0, container stream index 0, track ID 1. All were
2464 × 2464, converted from full-range `yuvj420p` to `rgb24` with a 7,392-byte
stride. Reported media timestamps were 0, 0.016787 and 0.033532 seconds. This
confirms that selected-track file decoding and RGB conversion work on Frame.
The user then ran window setup and confirmed that the annotated replay window
works on Frame. Detailed accuracy, color and performance measurements remain
open. Avoid assuming the separate desktop development environment is installed.

For the next session, one command runs the read-only inventory and selects the
newest completed stereo recording automatically:

```bash
cd "$HOME/framejacker"
bash scripts/frame_replay_check.sh
```

This does not initialize OpenVR, start or stop SteamVR, open camera device
nodes, decode video, or modify the recording. Save its JSON output for the
replay/window implementation.

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
dependency, input error, timeout or decode failure. The generated fragmented
HEVC cloud tests pass, and the user's three-frame ARM64 check also passed.
Continue with the annotated replay window below.

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
nodes, calling OpenVR, changing SteamVR or saving images. Offscreen
cloud tests verify decoding, object labels, stream selection and a nonblank
rendered window. The user confirms the window works on Frame and has also
confirmed replay with `--overlay`: video playback, controls and the image
following their head work. That optional mode initializes OpenVR in a separate
process; see the [VR panel guide](vr-overlay.md) for launch commands. The timed
panel test has also passed on Frame. Orientation, color appearance, detection
accuracy, readability and performance have not been systematically measured.
Live capture is investigated separately.

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
SDK was found in the sources reviewed. A supported Arcturus or Valve live-capture
contract remains necessary before selecting a live backend. The experimental
community Vulkan path in the [camera guide](steam-frame-camera.md) has not been
deployed by this project. Neither an active service nor listed sensor nodes
replace actual frame retrieval.

Do not copy the cloud machine's x86_64 `.venv` to Frame. The existing dependency
pins and smoke setup were validated on Linux x86_64, not the headset; verify
native interpreter, wheel and library availability before installing them there.
The device inventory requires no such installation. Leave compositor-layer
deployment and camera-source changes out of this initial check.

Continue with the [replay/window milestone](architecture.md#milestones-and-acceptance-checks)
using an Arcturus recording. The window and VR replay panel are confirmed
working. Use the [overlay guide](vr-overlay.md) to resume playback and check
object labels, readability and processing performance. A verified live adapter
remains separate. The existing cloud smoke fixture does not test vendor-recording
decoding.
Offline splat reconstruction can proceed separately on a suitable GPU computer.

## Context for a new development session

```text
Continue moral-kombat/framejacker on this Steam Frame.
Read README.md, docs/on-device.md and docs/steam-frame-camera.md.
First confirm where this terminal executes and run python3 scripts/device_check.py.
The official Arcturus Camera app's live view and recording work. Our public
OpenVR probe gets no frames and blacks out passthrough while running. Do not
repeat it on the unchanged runtime; it now requires an explicit interruption
flag. File decoding of three HEVC frames works on Frame. The annotated Qt replay
window works on Frame according to the user and is tested offscreen in the
cloud. The ten-second native panel test passed. The user also confirmed controls,
video playback and the image following their head with replay_window.py --overlay.
Read docs/vr-overlay.md to resume replay. Next check object labels, readability
and playback performance using the recording. Live capture is unresolved;
accuracy, color and performance still need measurements.
Treat device presence as inventory and keep vendor capture separate from public API results.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
