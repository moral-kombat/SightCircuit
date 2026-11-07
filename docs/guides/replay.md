# Replay a recording

Use a completed video or still image to check decoding and object recognition.
These steps run on Frame; a Linux desktop with the replay dependencies can use
the same file-based tools. Start with [Frame access](../on-device.md) or
[development setup](../development.md). The optional headset panel has its own
[VR panel guide](../vr-overlay.md).

Choose your input file, then follow inspection, decoding, and window playback
in order. Save reports to new paths. [Replay internals](../reference/replay.md)
explain stream selection, reports, timing, and offscreen tests.

## Prepare a recording for replay

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
[status](../status.md#e4-recordings-and-container-metadata). The fragmented-MP4
inspector result describes its limited timeline handling; it is not by itself
a corrupt-file diagnosis.

To inspect a new recording, one command runs the read-only inventory and selects the
newest completed stereo recording automatically:

```bash
cd "$HOME/sightcircuit"
bash scripts/frame_replay_check.sh
```

This does not initialize OpenVR, start or stop SteamVR, open camera device
nodes, decode video, or modify the recording. Keep its JSON output alongside
the playback observations when investigating a recording or runtime change.


## Decode one recorded video track

Use the separate file-decoding environment on Frame's Python 3.12. This needs
the standard-library `venv`/`ensurepip` modules and PyPI access. The pinned
`av==16.1.0` release publishes CPython 3.12 Linux ARM64 and x86_64 wheels
(glibc 2.28 or later). Installation permits binary wheels only; it does not
compile FFmpeg or install system packages. If `venv` or a compatible wheel is
unavailable, retain and share the error before choosing another setup route.

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  git pull --ff-only
  bash scripts/setup_replay.sh
  .venv-replay/bin/python scripts/replay_decode_check.py \
    "$HOME/Videos/ArcturusVisionCamera/REPLACE_stereo.mp4"
)
```

This downloads PyAV into ignored `.venv-replay/`; its wheel includes FFmpeg
libraries, so a separate `ffmpeg` executable, OpenCV and Qt are not required.
The existing desktop `.venv` remains separate. `SIGHTCIRCUIT_PYTHON` can select
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
[selected-track evidence](../status.md#e5-selected-track-decode) and
[cloud validation](../status.md#e7-cloud-validation) for prior results. Continue
with the annotated replay window below.


## Open the annotated replay window

Run this from the same normal-user graphical session on Frame:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  git pull --ff-only
  bash scripts/setup_window.sh
  .venv-replay/bin/python scripts/replay_window.py \
    "$HOME/Videos/ArcturusVisionCamera/REPLACE_stereo.mp4"
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
`mean_processing_ms`, presentation latency and `max_schedule_lateness_ms`, plus
`processed_media_fraction` (frames inferred on per frame the played span
covered; skipped frames lower it while `realtime_ratio` counts them as
progress) and the `pipeline` block's p50/p95/max latency statistics, which a
run that fails after starting also reports (`statistics_final` is false when
its worker had not stopped). The
keyframe-skipping version is cloud-tested only; see
[E62](../status.md#e62-keyframe-catch-up-realtime-replay-cloud-checks). The earlier
frame-dropping version was decode-bound on Frame, processing about 0.97 fps
versus 5.15 fps unpaced
([E51](../status.md#e51-media-time-replay-pacing-unit-tested-only),
[E53](../status.md#e53-unattended-replay-regression-and-pacing-rerun)). Without
`--realtime` every frame is processed in order, which plays this recording in
slow motion.

A latency run can optionally carry the read-only
[run-context sensor sidecar](../reference/pipeline.md#run-context-sensor-sidecar)
(`scripts/sample_run_context.py`) in a second shell, to record load, heat and
throttling beside the `pipeline` statistics. It is not yet approved on Frame:
its sysfs paths are unconfirmed on SteamOS 0.5.6, and a read-only sysfs
listing comes first as its own approved step. It is never a runner task.

By default the prototype displays an ordinary Qt window without opening camera
nodes, calling OpenVR, changing SteamVR or saving images. Optional `--overlay`
initializes OpenVR in a separate native process; see the
[VR panel guide](../vr-overlay.md) for launch commands, controls and shutdown.
[Status](../status.md#e6-window-and-vr-panel) records device confirmations and
unmeasured accuracy, color, readability and performance.


[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
