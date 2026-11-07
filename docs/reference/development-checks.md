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

[Development checks](../../.github/workflows/checks.yml) run on x86_64 for
pull requests and main pushes. [ARM64 checks](../../.github/workflows/arm64.yml)
run on main pushes and on demand, with an optional pacing run. ARM64 is the
headset's architecture, but a runner has no Frame drivers, SteamVR, or cameras.
Local checks and GitHub Actions results remain separate evidence.

The [shared workflow](../../.github/workflows/check-jobs.yml) has three jobs:

| Job | Work and limits |
| --- | --- |
| `native` | Lint on x86_64, check generated HUD headers, build native helpers and all capture-reference fixtures, then run the system-Python suite once with native modules pinned against skips. Probe checks use explicit missing-runtime paths; no headset is involved. |
| `replay` | Test the minimal decoder environment, then install window dependencies, build the bridge, fetch seed images, and run the full suite with the relevant modules pinned against skips. Check generated documentation and links; run the smoke environment on x86_64. |
| `replay-pacing` | On-demand ARM64 replay of the synthetic stand-in; publishes runner-CPU latency reports, never Frame measurements. |

A newer push cancels superseded pull-request runs. Changes limited to the
listed documentation paths run [Documentation checks](../../.github/workflows/docs.yml):
local links, preserved anchors, generated summaries, and the checker/generator
unit tests. Documents used by procedure-contract tests stay on the full run.
`tests/test_workflows.py` keeps path lists aligned and checks that the modules
pinned against skips exist. The documentation checker never executes code blocks.

The native suite validates mirror discovery, Linux interfaces, synthetic
shared-memory publication, and bounded worker failures. The replay suite adds
decoding, inference, offscreen rendering, HUD, agent, and seed-regression tests.
`scripts/run_tests_no_skips.py --all` runs the full suite once and fails if a
module named on the command line skips a test; dependency-specific skips in
other modules remain allowed. Read the results rather than inferring coverage
from a successful process exit alone.

The runner is the suite's entry point and does two more things there:

- `--durations FILE` writes each test module's wall time and test count as
  JSON and prints the modules slowest first. A module's time runs from the end
  of the previous test (or the start of the run) to the end of each of its
  tests, so class and module fixtures count with the tests they serve; what
  follows the last test goes to the last module. These are runner-CPU numbers
  for the planned suite time budget, never Frame measurements.
- Before the tests run it normalises SIGINT for the suite's children. A test
  process started with SIGINT ignored (a background job of a shell without job
  control) passes that ignore to every subprocess the tests signal, so the
  subtests that send SIGINT would depend on how the suite was started. An
  ignored SIGINT is replaced by Python's default handler, which a child does
  not inherit, and the runner says so on its error stream. A plain
  `python -m unittest discover` run does not normalise anything; use the
  runner when the suite is started from such a shell. SIGHUP is left as it is:
  the agent runner honours an inherited ignore by design (see its
  `Interruption` class), and a suite started under `nohup` would see its
  SIGHUP subtests fail for that reason.

The optional pacing run uses a generated 2464 × 2464 HEVC clip at 60 fps with
seeded noise. It replays 40 processed frames at a 5 fps cap with two and four
decoder threads, and checks the [latency statistics](pipeline.md#latency-statistics).
Its content differs from the original E62 clip, so timings and bitrate are not
reproductions of that device input. To reproduce one runner configuration:

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
