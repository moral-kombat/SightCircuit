# Replay implementation and reports

For the step-by-step procedure, use [Replay a recording](../guides/replay.md).
This reference describes the dependency environments, file inspection, stream
selection, offscreen checks, and performance/report fields.

## Recorded MP4 inspection

`python3 scripts/recording_check.py /path/to/recording.mp4` inspects container
headers without opening a camera, decoding frames, or installing packages. With
no path it selects the latest `*_stereo.mp4` in
`~/Videos/ArcturusVisionCamera`. Stop the recording before inspection. Track
sample-entry codes and dimensions guide decoder selection; successful metadata
inspection does not establish decoding or detection support for that recording.

On the headset, `bash scripts/frame_replay_check.sh` combines this inspection
with `scripts/device_check.py`. It is intentionally read-only and does not
initialize OpenVR.

Run the dependency-free parser checks with
`python3 -m unittest discover -s tests -v`. They cover track metadata, malformed
containers, parsing limits, media-payload skipping and nonregular-file rejection.
Decoder integration tests are skipped when PyAV is unavailable.


## File-decoding environment

`bash scripts/setup_replay.sh` creates a separate Python 3.12 `.venv-replay` and
installs the pinned binary wheel in `requirements-replay.txt`. Set
`SIGHTCIRCUIT_PYTHON=/path/to/python3.12` if the default `python3` differs.
This check does not need the full desktop requirements or `uv`.

```bash
.venv-replay/bin/python scripts/replay_decode_check.py /path/to/recording.mp4
.venv-replay/bin/python -m unittest discover -s tests -v
```

The decoder runs in a child process under `sightcircuit/runtime/guard.py`
`supervise`: an owned process group, a 45-second deadline, reaping and, on
Linux, the parent-loss guardian. The parent accepts the child's report only
through `validate_worker_report`. The report keeps its fields and adds
`exit_code`, `cleanup_ok` and the supervisor's worker and guardian fields. A
failed supervision (for example `timed_out` or `invalid_worker_report`) exits 1
and still gives `error`, worded as before supervision: `File decode exceeded
the 45-second limit`, or `Decoder process exited with code N` for a decoder
that crashed or exited with a status other than 0 or 1.

PyAV selects the requested video stream and converts a few decoded frames to
RGB without NumPy, OpenCV or Qt. Tests generate real fragmented HEVC with two
different video-track dimensions, exercise both selections, and check failures
for absent streams and invalid/nonregular files. They do not reproduce Arcturus
calibration, metadata payloads, dependent stereo views or HDR color management.


## Annotated replay window

Replay and bounded mirror recognition share `sightcircuit/detection.py` through
the [shared pipeline](pipeline.md#python-package-and-pipeline): the same checked
MobileNet-SSD model, CPU execution, 20 labels, preprocessing and annotation code.
The smoke check uses the same detector. Boxes now show explicit percentage confidence scores. These
scores are not calibrated probabilities; the device's rug-as-sofa false positive
is recorded in [status](../status.md#e15-saved-room-recognition-and-rug-false-positive).

```bash
bash scripts/setup_window.sh
.venv-replay/bin/python scripts/replay_window.py /path/to/recording.mp4
```

Setup extends `.venv-replay` using `requirements-window.txt` and verifies the
existing model/image fixtures. The window runs PyAV and CPU MobileNet-SSD on a
worker thread through the shared pipeline, keeps one latest annotated result,
and presents it on the Qt thread. The optional VR panel is a pipeline sink fed
from the worker thread. The prototype has pause/resume, replay and loop controls. Processing
replay is capped at 10 fps by default and preserves every processed frame's
association with its detections; this is not a real-time source-timing player.

To run a finite, offscreen check with explicit output files:

```bash
mkdir -p artifacts/replay-window
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
  /path/to/recording.mp4 --max-frames 4 \
  --preview artifacts/replay-window/preview.png \
  --report artifacts/replay-window/report.json
```

Output paths must be new and distinct from the input recording. Without these
flags the app saves no images or reports; it prints a JSON summary on exit.
Use a new output path for subsequent checks. `--video-stream` selects a video
track; no stereo-side identity is inferred. `--threshold` selects detection
confidence. The demo has 20 classes and is not a selected production detector.
Device presentation results and remaining orientation, color/HDR, accuracy and
performance limits are recorded in [status](../status.md#e6-window-and-vr-panel).
Linux ARM64 Qt wheels require glibc 2.39+
and graphical-system dependencies; the native X11 path checks `ldd` output for
missing plugin libraries before creating a QApplication.

The summary embeds the engine's `pipeline` block, with the
[latency statistics](pipeline.md#latency-statistics), whenever the engine started: on a
clean finish and also when the run fails afterwards, for example on a decoder or
detector error, so a failed run keeps the statistics of the frames it
processed. `statistics_final` is true when the worker had stopped, and false
when it was still running after the five-second shutdown wait (the run then
fails with "did not shut down"): those statistics are a snapshot that may
still have grown. A run that fails before the engine starts (an invalid path,
no display, missing Qt libraries) has no block. With `--realtime`,
`realtime_ratio` is media seconds advanced per wall second and counts catch-up
jumps as progress. `processed_media_fraction` is the frames processed divided
by `media_frames_spanned`, the source frames the played span covered: each was
emitted, dropped after decoding, or skipped undecoded during a catch-up. It is
1.0 when every frame is processed (every-frame replay), null before any frame,
and small when realtime replay keeps pace by skipping.

Realtime replay's latest-only engine copies every frame the source publishes
(`Frame.owned()`, once per `frames_received`, on the decode thread), including
frames replaced before inference, and `to_bgr` copies the read-only copy of
each processed frame again when no resize is needed; every-frame replay copies
neither. Measured on the cloud runner CPU (Linux x86_64, 4 cores, NumPy 2.2.6;
not Frame), the medians are 0.12 ms (`owned()`) and 0.11 ms (`to_bgr`) at
960 × 540 (what the decoder makes of a 1920 × 1080 recording) and 0.027 and
0.022 ms at 640 × 360. A 1920 × 1080 frame published unscaled takes 0.52 ms to
own, and `to_bgr` resizes it instead of copying. On the decode thread, 60
published frames per second at 960 × 540 cost about 7 ms of copying per
second, under 1% of one core. The [stand-in clip](development-checks.md#continuous-integration)
decodes to 960 × 960 frames (0.22 ms for each copy), and its realtime replays
on the same runner received 1.8 and 3.5 frames per processed frame (2 and 4
decoder threads), so the copies cost about 0.6 and 1.0 ms per processed frame,
2.5% and 4% of a median MobileNet-SSD detector call of about 25 ms on the same
CPU. Only `to_bgr`'s copy, once per processed frame (under 1% of that call at
either size), could be removed without giving up the slot's private copy, so
both stay, with the ownership guarantees the owned-copy tests pin.

Run `.venv-replay/bin/python -m unittest discover -s tests -v` after window
setup for the complete parser, decoder and offscreen display checks. The window
tests generate two-track replay video and require bicycle/car/dog detections,
a nonblank preview, correct track selection and input-file preservation.
