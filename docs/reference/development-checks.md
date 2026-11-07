# Development checks and CI

The [development guide](../development.md) gives the recommended local setup
and check sequence. This reference explains what each check proves, its
artifacts, and the distinction between local tests, CI runners, and Frame results.

## What validation demonstrates

The smoke check generates an MJPEG video from 12 copies of the upstream sample
image, reads all 12 frames with OpenCV's FFmpeg backend, runs the default
detector (MobileNet-SSD) on CPU, and requires bicycle, car and dog detections on
each frame. Every registered detector also runs once on the sample image and
must find each of bicycle, dog and car or truck that its vocabulary has. Qt renders the
annotations and writes a nonblank screenshot. A separate bundled ONNX
multiplication graph executes with checked numerical output.

It invalidates the previous `artifacts/smoke/readiness.json` before importing
`sightcircuit` or any runtime dependency and writes a new report only after
success. The detector fixture is a development check, not a selected production
model.

| Path | Contents | Committed? |
| --- | --- | --- |
| `.venv/` | Dedicated Python environment | No |
| `.cache/uv/` | Default package cache | No |
| `.cache/sightcircuit/assets/` | Verified model, model configuration and sample image | No |
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
visible desktop UI and exits after checking the frames. Visible presentation
needs a working display server and Qt platform libraries. The interactive
prototype is `scripts/replay_window.py`. OpenCV is installed headless; the
replay app uses Qt for presentation.

OpenCV 4.12 is pinned because the Caffe importer needed by this fixture was
removed from OpenCV 5. The wheel includes its own FFmpeg backend; the current
smoke script does not invoke a separately installed `ffmpeg` executable.

Do not copy an x86_64 virtual environment onto the headset. Device dependencies
and camera/runtime capabilities must be installed and verified for the target
architecture. [Status](../status.md) distinguishes device confirmations from cloud
tests and lists unverified capture, geometry and performance capabilities.
See the [camera guide](../steam-frame-camera.md) and
[implementation milestones](../architecture.md) for their technical constraints.


## Continuous integration

The documentation job in `checks.yml` checks local links, all preserved anchors,
and generated-summary freshness, then runs the documentation checker and
generator tests. It uses Python's standard library and needs no headset, model
download, or Qt setup. The application jobs still run under their existing triggers.

The [GitHub Actions workflow](../../.github/workflows/checks.yml) runs ruff and the
[shared job definitions](../../.github/workflows/check-jobs.yml) on x86_64 Ubuntu,
including the same setup and smoke check with Python 3.12. The
[ARM64 workflow](../../.github/workflows/arm64.yml) runs the same native and Python
jobs, except the x86_64 smoke environment, on `ubuntu-24.04-arm`: the
headset's architecture, without its drivers, SteamVR or cameras. It needs network access to
the fixture sources. A file-decoder job installs PyAV and exercises fragmented
HEVC replay and RGB conversion. A separate job builds the native OpenVR probe, verifies that
default and invalid invocations stop before OpenVR initialization, and checks
missing-runtime diagnostics with explicit opt-in. The probe has interrupted
visible passthrough on the tested headset; do not repeat that unchanged hardware
test. CI does not stand in for a headset test. Local execution
of these commands is separate from a successful GitHub Actions run after push.

The replay-window job installs the window requirements and exercises decoding,
inference, Qt offscreen presentation and file-protection behavior end to end.
The dependency-free mirror job exercises discovery, native Linux UAPI layout,
read-only capture guards, pixel bounds, deadlines, cancellation and optional
preview publication without camera hardware.
The standalone shared-memory job builds both native helpers and checks
cross-process publications, stale/malformed input, source mutations, native
fault containment, deadlines and preview publication with synthetic images.

On ARM64 only (`pacing: true` in `arm64.yml`), the replay-pacing job writes the
synthetic realtime-replay stand-in and replays it as E62 did: `--realtime`, 40
processed frames at a 5 fps cap with MobileNet-SSD, once with 2 and once with 4
decoder threads. It checks that each report finished and carries the
[latency statistics](pipeline.md#latency-statistics) and `processed_media_fraction`, then
uploads the two reports, the stand-in's description and the runner's CPU
description as the artifact `replay-pacing-ubuntu-24.04-arm-runner-cpu-not-frame`.
These are runner-CPU numbers on synthetic input, for comparing commits: they are
not Frame measurements and not status evidence. E62's generator was never
committed, so `scripts/replay_standin.py` (`sightcircuit/sources/standin.py`)
encodes a clip to its documented parameters: 2464 × 2464 HEVC, 60 fps, 10 s,
one-second GOP, 320 Mbit/s target. The content is new seeded noise, so bitrate
and decode speed differ from E62's clip. On the 4-core x86_64 cloud runner it
encodes in about 100 s at about 330 Mbit/s. To reproduce the job locally:

```bash
mkdir -p artifacts/replay-pacing
.venv-replay/bin/python scripts/replay_standin.py artifacts/replay-pacing/standin.mp4 \
  > artifacts/replay-pacing/standin.json
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/replay_window.py \
  artifacts/replay-pacing/standin.mp4 --realtime --decode-threads 2 --max-frames 40 --max-fps 5 \
  --report artifacts/replay-pacing/realtime-2-threads.json
```


## Generated evidence summaries

Detailed records live under [evidence](../evidence/README.md); the coordinator
allocates IDs and updates `docs/evidence/index.json`. Reviewed capability rows
in `docs/status-data.json` generate the grouped status matrix and the marked architecture
progress summary. The same generator builds the evidence catalog from the index.
Preserve all historical evidence headings and E63–E71's
reconstructed uncertainty. From the checkout:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
python3 scripts/check_docs.py
python3 -m unittest tests.test_generate_status
python3 -m unittest tests.test_check_docs
```

The generator checks identities, provenance, missing history and evidence
references before writing. The root README keeps short entry points; its older
narrative and links remain in [README history](../project-history.md). The
[qualification design](../hud-qualification.md) owns the proposed G4 method and
separately scoped device-validation evidence path. These offline documents and
schemas do not qualify a mapping or open production output.
