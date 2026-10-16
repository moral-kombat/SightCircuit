# Development setup

The supported development check currently targets Linux x86_64 with Python 3.12.
It exercises the video, inference and window dependencies; it does not launch a
Framejacker application or connect to a camera.

## Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) using its
documented instructions. Setup was tested with uv 0.12.23. From the repository:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

The setup script resolves its working directory from its own location, creates
`.venv` with Python 3.12, synchronizes the pinned development packages, checks
dependency compatibility, and downloads the three smoke-test fixtures. Running
setup again reuses the environment and verifies cached fixture checksums. The
dedicated `.venv` is managed by this script; dependency synchronization removes
packages absent from `requirements-dev.txt`.

The first run needs PyPI package downloads and
`raw.githubusercontent.com` for the model/image fixtures. If uv needs to obtain
Python, its managed-Python download also needs GitHub release access. No API keys
are required for the current checks.

Setup uses normal TLS verification. The fixture downloader verifies SHA-256
against `scripts/smoke-assets.json` before making a download available. An
existing cached fixture with a different checksum causes an error. Inspect and
remove that specific generated file before downloading it again; do not change
the expected checksum just to make the check pass.

## What validation demonstrates

The smoke check generates an MJPEG video from 12 copies of the upstream sample
image, reads all 12 frames with OpenCV's FFmpeg backend, runs MobileNet-SSD on CPU,
and requires bicycle, car and dog detections on each frame. Qt renders the
annotations and writes a nonblank screenshot. A separate bundled ONNX
multiplication graph executes with checked numerical output.

It invalidates the previous `artifacts/smoke/readiness.json` before importing
runtime dependencies and writes a new report only after success. The detector
fixture is a development check, not a selected production model.

| Path | Contents | Committed? |
| --- | --- | --- |
| `.venv/` | Dedicated Python environment | No |
| `.cache/uv/` | Default package cache | No |
| `.cache/framejacker/assets/` | Verified model, model configuration and sample image | No |
| `artifacts/smoke/readiness.json` | Current passing validation report | No |
| `artifacts/smoke/detection-window.png` | Rendered detection result | No |
| `artifacts/smoke/sample.avi` | Generated repeated-image MJPEG video | No |
| `scripts/smoke-assets.json` | Immutable fixture URLs and SHA-256 checksums | Yes |

Set `UV_CACHE_DIR` or `XDG_CACHE_HOME` before setup to reuse another writable
package/font cache. The fixture directory stays repository-local. The downloader
also accepts `--asset-dir` for isolated download checks; the smoke check uses the
default fixture directory.

## Window rendering and hardware

The default smoke check sets `QT_QPA_PLATFORM=offscreen` when unset. It shows no
visible desktop UI and exits after checking the frames. A visible window on a
Linux desktop needs a working display server and Qt platform libraries; that
interactive application is future work. OpenCV is installed headless, so use Qt
for display rather than `cv2.imshow`.

OpenCV 4.12 is pinned because the Caffe importer needed by this fixture was
removed from OpenCV 5. The wheel includes its own FFmpeg backend; the current
smoke script does not invoke a separately installed `ffmpeg` executable.

Do not copy an x86_64 virtual environment onto the headset. Device dependencies
and camera/runtime capabilities must be installed and verified for the target
architecture. Live capture, stereo synchronization, depth, tracking-space
alignment, performance, acceleration and VR presentation remain untested here.
See the [camera guide](steam-frame-camera.md) and
[implementation milestones](architecture.md).

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
`FRAMEJACKER_PYTHON=/path/to/python3.12` if the default `python3` differs.
This check does not need the full desktop requirements or `uv`.

```bash
.venv-replay/bin/python scripts/replay_decode_check.py /path/to/recording.mp4
.venv-replay/bin/python -m unittest discover -s tests -v
```

PyAV selects the requested video stream and converts a few decoded frames to
RGB without NumPy, OpenCV or Qt. Tests generate real fragmented HEVC with two
different video-track dimensions, exercise both selections, and check failures
for absent streams and invalid/nonregular files. They do not reproduce Arcturus
calibration, metadata payloads, dependent stereo views or HDR color management.

## Continuous integration

The [GitHub Actions workflow](../.github/workflows/checks.yml) runs the same setup
and smoke check on an Ubuntu runner with Python 3.12. It needs network access to
the fixture sources. A file-decoder job installs PyAV and exercises fragmented
HEVC replay and RGB conversion. A separate job builds the native OpenVR probe, verifies that
default and invalid invocations stop before OpenVR initialization, and checks
missing-runtime diagnostics with explicit opt-in. The probe has interrupted
visible passthrough on the tested headset; do not repeat that unchanged hardware
test. CI does not stand in for a headset test. Local execution
of these commands is separate from a successful GitHub Actions run after push.
