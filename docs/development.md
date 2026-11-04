# Development setup

This guide owns cloud/desktop setup, dependency environments, build commands
and automated checks. The development smoke check targets Linux x86_64 with
Python 3.12; it does not launch the interactive application or connect to a camera.
Use the [on-device guide](on-device.md) for Frame and [status](status.md) for
validation evidence and compatibility limits.

## Python package and pipeline

Reusable code lives in the `framejacker/` package; `scripts/` keeps the
documented command-line entry points. No active script imports another, and
`framejacker/` never imports `scripts/`; `tests/test_scripts_boundary.py`
enforces both (the paused capture-provider scripts are exempt while frozen). Scripts add the checkout root to `sys.path`
when run as files, so headset commands need no installation step.
`pip install -e '.[window]'` (or `uv pip install -e .`) also works for desktop
development; exact tested pins remain in the `requirements-*.txt` files.

| Module | Responsibility |
| --- | --- |
| `framejacker/frame.py` | `Frame`, the only frame type (pixels, layout, source, sequence, observation time, media time, declared `native_format`), with `Frame.owned_bgr` (validated private BGR copy) and `owned()` (the engine's copy on publish); `DetectionResult`, which every detector returns; `Processed`, the one record every engine submits to sinks (annotated image or HUD tile with `HudOutput` rows and exclusion checks, expiry, completion time, sizes and coordinate space) |
| `framejacker/pipeline.py` | `FrameSource`/`Sink` interfaces and `Pipeline`, the one engine for replay, mirror recognition and the synthetic live HUD: latest-only or every-frame scheduling, rate cap, `CaptureContext` admission, HUD stage or annotation, HUD exclusion counts, result expiry and deadline, optional sink `open`/`clear`/`close` with cleanup results. `PipelineStats.report()` is the one statistics vocabulary, including the [latency statistics](#latency-statistics): the replay, mirror and synthetic live reports embed it as `pipeline` (mirror and synthetic live once the engine is constructed, replay once its engine has started, on error too, with `statistics_final` false if its worker had not stopped; a report that ends earlier has no block and only the fields its command already wrote) and keep their earlier field names only as aliases (`report_fields`) until a schema version bump |
| `framejacker/latest.py` | One-pending-item handoff; newer items replace older ones |
| `framejacker/gates.py` | The closed approval gates (live image input, hardware mapping, live HUD output): each boundary's `require` and every report's gate claims; nothing here opens a gate |
| `framejacker/context.py` | `CaptureContext`: pins declared source identity, frame source/native layout/session metadata and an optional HUD mask binding (calibration source, format, layout, session tokens and lifetime); `admit(frame)` fails closed and latches the first failure |
| `framejacker/convert.py` | One native pixel rule (`buffer_bgr`: BGR24 arrays and padded RGB3/BGR3 buffers), `native_bgr` for frames and `to_bgr` for detector input of at most 960 pixels |
| `framejacker/detection.py` | `Detector` interface, `MobileNetSSD` baseline (model-free `decode_ssd()`), `YoloxDetector` candidate, the `DETECTORS` registry whose classes own their `model_files()` and `preprocessing()` description, `create_detector()`, separate `annotate()` and `require_result()` (rejects malformed detector output before any output) |
| `framejacker/preprocess.py`, `framejacker/labels.py` | Letterbox input and inverse box mapping; COCO vocabulary and VOC-to-canonical label names |
| `framejacker/evaluation.py`, `framejacker/assets.py` | Detector evaluation scoring; checksum-verified model/fixture download and verification |
| `framejacker/mask_fill_evaluation.py` | Offline comparison of HUD mask fills (the runtime's `hud.mask.fill_regions` and the candidates it was chosen over) on the gray sweep and the seed stills, used by `evaluate_mask_fill.py`; the runtime never imports it ([HUD mask fill evaluation](#hud-mask-fill-evaluation)) |
| `framejacker/sources/` | `ReplaySource` (PyAV); `standin` (the synthetic realtime-replay [stand-in clip](#continuous-integration)); `mirror_device` (read-only V4L2 SteamVR mirror discovery and reads); `mirror` (bounded acquisition loop `capture`, `MirrorSource` (its optional `session` declares the HUD session tokens and stamps each frame), and `launch`, the one launcher of both mirror commands: candidate selection, output reservation, the bounded worker and all-or-nothing publication); `recording` (one MP4 box-header decoder for finished-file inspection and the growing-file scan used by `recording_growth_check`; newest recording); `PolledSource` (adapter for `open`/`read`/`close` sources); `session` (read-only [session-token providers](hud-qualification.md#session-tokens-for-the-mirror-source-2026-11-03) for a mirror run: `HostClock`, `RuntimeProcess`, `TrackingObserver` and `MirrorSession`). Adapters forward the wrapped source's declared identity |
| `framejacker/overlay.py` | Overlay bridge client and `OverlaySink`, the one panel sink: with a caller-owned client (replay's interactive panel) it only submits images; otherwise it owns a validation-only client through `open`/`clear`/`close`, optionally with the frozen HUD placement and payload contract |
| `framejacker/hud/` | Fixed edge HUD contract, shared eye/rectangle geometry, the one margin rule, synthetic-only native mask (calibrations from two factories only, with expiry, session tokens and view fields; zeroed regions refilled from their surround by `fill_regions`), tile rendering, the `HudStage` engine stage (and `HudPipelineAdapter`, the stage bound to its sink's fixed output contract, used by `live_pipeline.py`), offline mapping qualification, the calibration marker client, `observation` (empirical marker-copy location in native pixels and the observer report contract, used by `hud_marker_observer.py`), `candidate` (an observer report to an unqualified mapping candidate, used by `hud_validation_report.py --from-observer`) and `validation` (the synthetic HUD run's ledger feed and G5 software counts) |
| `framejacker/runtime/` | `process` (bounded spawned workers, parent-death signalling), `guard` (owned process-group supervision with the Linux parent-loss guardian, and the one stop-on-signal convention: `sigterm_cancels` and `cancelled` for launchers, `stop_on_signals` for workers), `output` (reserve, write, publish all or none, withdraw and discard explicit image outputs; `publish_new` for finished bytes), `live` (`run_report`, the one report runner on `Pipeline`, used by `run_pipeline`, the gated synthetic run, whose HUD run also feeds the validation ledger, and by `run_recognition`, the mirror recognizer), `synthetic` (the generated source and its declared `SyntheticSession`, the fixture calibration factory and its candidate record, `FixtureDetector` and `HeadlessSink`) and `manifest` (the [checkpoint artifact manifest](#checkpoint-artifact-manifest): software identity written and verified on Frame) |
| `framejacker/agent/` | On-device agent support: the fixed task table, the `frame_agent_run.py` runner (timeouts, lock, run budget, `result.json`), the PreToolUse guard and the Frame-only Claude Code settings template; standard library only ([plan](agent-on-frame.md)) |

File replay uses every-frame scheduling, so each decoded frame is processed in
order. Mirror recognition uses latest-only scheduling with a one-second input
age limit. The V4L2 mirror device module and acquisition loop live in
`framejacker/sources/mirror_device.py` and `framejacker/sources/mirror.py`;
`scripts/mirror_check.py` is the diagnostic command. Both mirror commands start
their workers through `sources.mirror.launch`. `tests/test_report_characterization.py`
pins the report keys, value types and status and exit-code mapping of the
synthetic run, the mirror recognizer and both mirror commands. It was written
before these moved into the library and must keep passing unchanged.

Lint with the configuration in `pyproject.toml`:

```bash
uvx ruff@0.14.2 check .
```

### Latency statistics

The engine times every frame that reaches presentation, including a result
later dropped as stale, at the points where it already measured. The
`pipeline` block keeps its earlier mean, total and maximum fields and adds one
distribution per timing (all in milliseconds):

| Block field | Measures |
| --- | --- |
| `input_age_ms` | Observation (`Frame.observed_ns`) to the start of processing |
| `processing_ms` | Start of processing to a presented result: `stage_ms.prepare` + `detect` + `present` |
| `inference_ms` | The detector's own forward time (`DetectionResult.inference_ms`) |
| `output_age_ms` | Observation to the output boundary, before the sinks |
| `stage_ms.prepare` | Conversion to detector input; with a HUD stage, native masking and conversion |
| `stage_ms.detect` | The whole detector call, including its own pre- and post-processing |
| `stage_ms.present` | Result checks plus annotation (and its owned copy) or HUD rendering |
| `stage_ms.submit` | Every sink's admission check and `submit`, for results submitted to all sinks |

Each holds `count` (frames timed), `samples` (values behind the percentiles),
`p50`, `p95` and `max`, rounded to three decimals; the statistics are null
until a frame is timed. `percentile_method` is `nearest_rank`: the
p-percentile of n sorted values is the value at 1-based rank ceil(p × n / 100),
so it is always a measured value (n = 1 gives that value, n = 2 gives the lower
value for p50 and the upper for p95, and ties return the tied value). Memory is
bounded: each timing keeps at most 4,096 values (`TIMING_SAMPLE_CAPACITY`),
which makes percentiles exact up to 4,096 frames; beyond that a seeded
reservoir keeps a uniform sample of the whole run, so `samples` stays 4,096
while `count` and `max` cover every frame. Stage durations are read from
`Pipeline.timer` (`time.monotonic`); `tests/latency_fixture.py` installs a
stepping clock driven by a fake detector with known delays, so tests check exact
percentiles in the replay, mirror and synthetic reports. Ages are measured
against the source's monotonic observation clock.

## Detector evaluation

`scripts/evaluate_detectors.py` runs each detector on the same stills through
the application's conversion path (scaled to at most 960 pixels) and scores
them against a manifest. The default
[seed manifest](../evaluation/seed.json) holds six checksum-pinned outdoor
darknet photos with image-level labels; `--fetch` downloads them into ignored
`.cache/framejacker/evaluation/`. It is a regression check for wiring, label
mapping and gross confusions, not evidence about Frame's indoor input.

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch --expect evaluation/seed-expected.json
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --detector yolox-nano --threshold 0.5 --report /tmp/evaluation.json
```

Each class in `framejacker.detection.DETECTORS` owns what callers need to know
about it: its constructor takes the registry name, `model_files(name)` lists
its checksum-pinned assets and `preprocessing(name)` describes its model input
(input size, stretch or letterbox resize, interpolation, channel order, layout,
dtype, mean, scale, padding value and box mapping). The evaluator's provenance,
the smoke check and the asset tests read these, so adding a detector changes
`framejacker/detection.py` and its `scripts/smoke-assets.json` entries only.
The default detector and the 0.5 threshold are unchanged.

Manifest entries (`schema_version: 1`) need a unique `id` and either `path`
(relative to the manifest) or `file` plus `url`/`sha256` for download. A `path`
entry may also carry `sha256`; any supplied digest is verified before inference,
and `--require-input-digests` needs one for every entry. Entries written by
`scripts/frame_session_manifest.py --still-report` carry `sha256` and
`capture` (`detector`, `source`, `sequence` from the capture report), which the
evaluator does not read. `present`
lists canonical COCO labels that must be detected; a nested list accepts any
alternative, such as `["car", "truck"]`. `absent` lists labels that must not be
reported. Optional `objects` give `label`, source-pixel `box_xyxy` and
`confusable` labels: supported labels are matched at `--iou` (default 0.5); a
confusable detection overlapping the object at IoU 0.3 is a confusion. Set
`boxes_complete: true` only when every supported object is boxed, enabling
false-positive and precision counts. Labels a detector cannot output are
reported as unsupported. An entry may not repeat a `present` or `absent` label,
including under another name for the same canonical label (`sofa` and `couch`),
so the per-label table, the totals and a pin count the same checks. The JSON
report has per-detector totals, per-image detections and timing; a one-line
summary per detector and a per-label table go to stderr. Report
schema version 4 records manifest and resolved image SHA-256 values,
decoded and processed dimensions, detector model hashes, runtime versions and
preprocessing parameters, Python/OpenCV/NumPy versions, the Git revision and
tracked-diff hash, conversion path, participating source-file SHA-256 values
and native inference runtime artifact hashes. It also states annotation coverage explicitly: a
presence-only manifest does not measure absent-label or localization accuracy.
Schema 4 adds, per detector, a `labels` table of present (hit, miss,
unsupported) and absent (clean, violation, unsupported) outcomes, and per image
`label_outcomes` and `best_below_threshold`: the highest score under the
threshold for each supported expected label. That diagnostic comes from one
extra untimed `detect()` per image at a 0.05 floor and is never scored, so
counts are still taken at the requested threshold. A model's own output filter
can hide lower scores: the pinned MobileNet-SSD network keeps nothing below
0.25, so its `null` means below 0.25. In the table, `best miss` is the highest
such score among images where a present label was missed, and `best clean`
among images where an absent label stayed clean.
Each detector/variant performs one untimed warm-up before measured evaluation.
A dirty-worktree flag means the commit alone does not identify the executed
source; retain the exact checkout diff as well as the report. The diff hash is
a fingerprint, not a copy of the changes.
Private Frame stills belong in ignored `evaluation/local/`; see the
[device procedure](on-device.md#compare-detectors-on-frame-stills).

For an authorized existing still, audit actual decoded channels, BGR conversion,
model tensors and inverse geometry without embedding pixel payloads. Variants
are separate from the unchanged baseline, one controlled change at a time:

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --threshold 0.5 --require-input-digests --audit-preprocessing --audit-variant swap-rb \
  --audit-variant gray-replicated --audit-variant native-size --audit-variant mirror-preview \
  --report /tmp/frame-audit.json
```

`mirror-preview` replaces the decoded still with what E61's detector actually
saw: the `mirror_check` preview, which is
`framejacker.sources.mirror_device.grayscale_sample` at a 640-pixel bound
(every third pixel of a 1920-pixel frame, integer luma) replicated to three
channels. The variant calls that function itself rather than a copy of it.
`gray-replicated` keeps full resolution and OpenCV's luma, so it does not
reproduce that input.

`--require-input-digests` requires a supplied SHA-256 for every input and
verifies it before inference. Entries added by
`scripts/frame_session_manifest.py --still-report` carry the SHA-256 their
capture report recorded; see
[bounded mirror recognition](#bounded-mirror-recognition). Reports distinguish `manifest_verified` from
`computed_only` identity; computing a digest does not establish the original
E61 image identity. `--audit-preprocessing` adds fingerprints, channel
statistics and coordinate transforms, whose resize kind and box inverse come
from the detector's own `preprocessing(name)`; `--audit-variant` is repeatable. The report path must be new. SSD
keeps stretched 300 × 300 input and explicitly centers all three BGR channels;
YOLOX keeps its letterbox transform. Rounded resize mapping uses each realized
axis ratio. Synthetic regressions establish those implementation behaviors,
not the cause of the private E61 TV miss. Reproducing E61 still needs the
approved original still, verifiable digest and factual annotations. No detector
default follows from seed or synthetic results alone.

### Pinned seed counts

[`evaluation/seed-expected.json`](../evaluation/seed-expected.json) pins the
seed counts recorded in
[E73](status.md#e73-reproducible-preprocessing-audit-and-ssd-channel-correction):
MobileNet-SSD hits 7 of 9 supported presence checks with 2 absent-label
violations, and YOLOX-Nano 12 of 12 with none. `--expect` refuses, before any
inference, a run whose manifest SHA-256, threshold or IoU differs from the
pin's. After evaluation it exits 1 when a pinned detector was not evaluated,
hits fewer present labels, violates more absent labels, has more confusions,
or scores a different number of presence, absence or image checks (a
vocabulary or label-map change). Results better than the pin pass and are
listed for a reviewed pin update; detectors absent from the pin are listed as
unpinned. The verdict goes to stderr and to the report's `expectation` block.
The pin holds counts, never scores, so small floating-point differences between
x86_64 and ARM64 cannot move it: in the pinned run the closest regression
margin was a clean absent label at 0.437 against the 0.5 threshold.

CI's replay-window job runs the seed comparison with `--expect` and then
`tests/test_seed_pin.py` without skips, which checks that the committed pin
passes and that swapping two VOC labels in the SSD label map fails it. Seed
results remain a wiring and label-mapping regression check on the runner's
CPU, not Frame evidence, and they do not select a detector.

### Tests that need downloaded assets

Tests that need checksum-pinned downloads (models, `dog.jpg`, seed images) use
`tests/asset_gate.py`. When an asset is absent locally the test skips and names
the missing files and the command that fetches them. The gate checks presence
only: the detectors and the evaluator verify model and seed-image checksums
themselves, so an altered file fails the test instead of skipping it. CI
fetches the assets and runs those modules through
`scripts/run_tests_no_skips.py`, so an absent asset fails CI instead of
skipping there.

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
image, reads all 12 frames with OpenCV's FFmpeg backend, runs the default
detector (MobileNet-SSD) on CPU, and requires bicycle, car and dog detections on
each frame. Every registered detector also runs once on the sample image and
must find each of bicycle, dog and car or truck that its vocabulary has. Qt renders the
annotations and writes a nonblank screenshot. A separate bundled ONNX
multiplication graph executes with checked numerical output.

It invalidates the previous `artifacts/smoke/readiness.json` before importing
`framejacker` or any runtime dependency and writes a new report only after
success. The detector fixture is a development check, not a selected production
model.

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
visible desktop UI and exits after checking the frames. Visible presentation
needs a working display server and Qt platform libraries. The interactive
prototype is `scripts/replay_window.py`. OpenCV is installed headless; the
replay app uses Qt for presentation.

OpenCV 4.12 is pinned because the Caffe importer needed by this fixture was
removed from OpenCV 5. The wheel includes its own FFmpeg backend; the current
smoke script does not invoke a separately installed `ffmpeg` executable.

Do not copy an x86_64 virtual environment onto the headset. Device dependencies
and camera/runtime capabilities must be installed and verified for the target
architecture. [Status](status.md) distinguishes device confirmations from cloud
tests and lists unverified capture, geometry and performance capabilities.
See the [camera guide](steam-frame-camera.md) and
[implementation milestones](architecture.md) for their technical constraints.

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

The decoder runs in a child process under `framejacker/runtime/guard.py`
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

Replay and bounded mirror recognition share `framejacker/detection.py` through
the [shared pipeline](#python-package-and-pipeline): the same checked
MobileNet-SSD model, CPU execution, 20 labels, preprocessing and annotation code.
The smoke check uses the same detector. Boxes now show explicit percentage confidence scores. These
scores are not calibrated probabilities; the device's rug-as-sofa false positive
is recorded in [status](status.md#e15-saved-room-recognition-and-rug-false-positive).

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
performance limits are recorded in [status](status.md#e6-window-and-vr-panel).
Linux ARM64 Qt wheels require glibc 2.39+
and graphical-system dependencies; the native X11 path checks `ldd` output for
missing plugin libraries before creating a QApplication.

The summary embeds the engine's `pipeline` block, with the
[latency statistics](#latency-statistics), whenever the engine started: on a
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
second, under 1% of one core. The [stand-in clip](#continuous-integration)
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

## Continuous integration

The [GitHub Actions workflow](../.github/workflows/checks.yml) runs ruff and the
[shared job definitions](../.github/workflows/check-jobs.yml) on x86_64 Ubuntu,
including the same setup and smoke check with Python 3.12. The
[ARM64 workflow](../.github/workflows/arm64.yml) runs the same native and Python
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
[latency statistics](#latency-statistics) and `processed_media_fraction`, then
uploads the two reports, the stand-in's description and the runner's CPU
description as the artifact `replay-pacing-ubuntu-24.04-arm-runner-cpu-not-frame`.
These are runner-CPU numbers on synthetic input, for comparing commits: they are
not Frame measurements and not status evidence. E62's generator was never
committed, so `scripts/replay_standin.py` (`framejacker/sources/standin.py`)
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

## Existing SteamVR mirror diagnostic

`python3 scripts/mirror_check.py` performs sysfs-only discovery. Opt-in capture
uses Python's standard library and the current V4L2 format; no PyAV, OpenCV, Qt,
OpenVR or system package installation is needed. It supports Linux x86_64 and
ARM64 single-plane V4L2 loopback mirrors named `SteamVR` that advertise `read()`.
Format support is intentionally explicit: GREY, NV12/NV21, YU12/YV12,
YUYV/UYVY/YVYU/VYUY and RGB3/BGR3. Other formats/capabilities are reported.

The worker opens the verified node with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`,
uses only `VIDIOC_QUERYCAP` and `VIDIOC_G_FMT`, and calls nonblocking `read()`.
The first read is not gated on `select()`/`poll()`: some v4l2loopback releases
register file-I/O capture inside that call, before readability can be signaled.
The driver can initialize capture internally; no explicit queue or stream-on
ioctl is sent by this tool. Read attempts and would-block/interrupted reads are
reported separately from complete images. A lazy-start backend regression check
exercises first-read registration followed by frame delivery without polling.
No `S_FMT`, buffer-queue setup or explicit stream-on operation is requested.
Raw buffers are bounded at 64 MiB and dimensions at 16 Mi pixels; samples skip
padding and chroma. The supervisor enforces the driver deadline and handles
Ctrl+C/SIGTERM; Linux parent-death signaling stops the worker if its launcher dies.

The launcher is the shared `framejacker/sources/mirror.py` `launch`, which mirror
recognition also uses. An unsuitable `--preview` path stops it before the worker
starts. The worker writes the reserved grayscale PNG after capture has closed,
and the launcher links it into place only after a `captured` run with confirmed
cleanup. A clean run that produced no preview fails with `requested_output_missing`.
A destination created during the run is kept, and the run fails with
`preview_publish_failed`.

```bash
python3 -m unittest discover -s tests -p test_mirror_check.py -v
python3 -m unittest tests.test_mirror_launcher tests.test_report_characterization -v
```

With bare Python the second command covers the launcher and the
`mirror_check.py` characterization; its mirror-recognition and synthetic-run
characterization tests skip without NumPy/OpenCV and run under `.venv-replay`
in [bounded mirror recognition](#bounded-mirror-recognition).
Tests use controlled sysfs/device backends and real subprocesses. The ABI test
compiles a tiny Linux-header program when `g++` and UAPI headers are installed;
otherwise that check is explicitly skipped. With PyAV available, preview testing
also decodes the generated PNG. No test opens an actual camera. Read the
[device procedure](on-device.md#check-an-existing-steamvr-mirror) before opt-in
acquisition, and [status](status.md#e8-mirror-diagnostic-cloud-checks) for actual
validation evidence.

## Bounded mirror recognition

`scripts/mirror_recognize.py` reuses the guarded capture loop and supervised
process deadline, and runs recognition through the latest-only
[shared pipeline](#python-package-and-pipeline). Its worker runs
`framejacker/runtime/live.py` `run_recognition` on `run_report`, the report
runner the synthetic run also uses, and `sources.mirror.launch` starts it and
publishes its outputs. Default execution performs metadata-only discovery. Opt-in
capture/inference needs the existing OpenCV/NumPy environment and checked model
fixtures, with no Qt, PyAV or OpenVR initialization. The recognizer accepts only
RGB3/BGR3, respects row padding and converts to owned BGR images of at most 960
pixels on the longer side. A capture thread retains one latest pending read;
slow inference replaces old input rather than accumulating a queue.

`--detector` picks a `DETECTORS` entry through `create_detector`; the default
stays `mobilenet-ssd`. The report records `detector_requested` and `detector`
(name, runtime and class count). Two optional outputs come from the last
processed result: `--preview`, the annotated image scaled to at most 640
pixels, and `--still`, the unannotated frame at its native mirror size, for
[evaluation sets](#detector-evaluation). Each refuses an existing path, is
written to an owned temporary file after the source closes, and is hard-linked
into place only after a recognized run with confirmed cleanup
(`framejacker/runtime/output.py`). The two need paths that resolve to different
files, and publication is all or nothing: if a later hard link fails, outputs
this run already linked are withdrawn and the report gives
`<kind>_publish_failed`; Ctrl+C or SIGTERM during publication also withdraws
them, and the run is `cancelled`. The still is the native frame, not the
960-pixel detector input that the flag saved before `3bd3d4c` removed it:
`evaluate_detectors.py` applies the same conversion, so its baseline input
equals what the live detector saw, and its `native-size` audit variant keeps a
meaning. The report records `still_sha256` (of the PNG bytes written, checked
again before publication; a still reported without it is not published),
`still_detector`, `still_source`, `still_sequence`
(equal to `last_result.read_sequence`) and `still_width`/`still_height`.
`scripts/frame_session_manifest.py --still-report <report>` checks that the
image still matches and copies the digest, detector, source and sequence into
the manifest entry, which then passes `--require-input-digests`.

```bash
.venv-replay/bin/python -m unittest discover -s tests -p test_mirror_recognize.py -v
.venv-replay/bin/python -m unittest discover -s tests -p "test_frame_session*.py" -v
.venv-replay/bin/python -m unittest tests.test_report_characterization tests.test_mirror_launcher -v
```

Tests cover guards, channel order/padding, overload and exact source/result
association, old/empty input, model/read/close failures, cancellation and real
spawned-model/preview/still publication with the checked fixture. Image encoding is
checked to occur after source closure. Destinations created concurrently remain
untouched. A fixture mirror larger than 960 pixels and a fixture model drive
`--capture --still` end to end: the still equals the native frame of
`last_result`, the evaluator's conversion of it reproduces the live detector
input, and its manifest entry passes `--require-input-digests`.
`tests/test_frame_session_commands.py` parses every `scripts/*.py` command line
in `scripts/frame_session.sh` with that script's own parser and exact option
names, without running it; the session helper's own tests replace Python with a
fake, so they cannot see a removed flag. Dependency-specific tests skip without OpenCV/NumPy; the replay-window
CI job installs these and exercises the complete suite. Existing supervision
tests cover hard deadlines, abnormal worker exit and process reaping.

The final report carries at most 20 detections from the last processed input,
local observation/processing timing, captured/processed counts and replaced
pending inputs. One pending read, one in-flight inference and bounded result
storage limit memory; the kernel driver's own buffering is separate. The
supervisor budget includes 15 seconds beyond capture duration for model startup
and completion, then bounded cleanup. File output is opt-in and published only
after capture/inference finishes. Read [the device procedure](on-device.md#run-bounded-recognition-from-the-mirror)
before the first Frame trial; [status](status.md#e16-bounded-mirror-recognition-cloud-checks)
records cloud-only validation and the model-quality limitation.

## Paused capture-provider track

The dedicated-capture provider track is paused and frozen (see the
[design review](design-review.md#next-steps)); its procedures below and in the
[on-device guide](on-device.md) are kept for when it resumes. Do not activate
layers or restart SteamVR from a cloud session. Its Python commands stay in
`scripts/` under their documented paths and are quarantined in place:

| Script | Role |
| --- | --- |
| `capture_layer_config.py` | Prepare an inactive layer bundle and restore exact files; never restart VR |
| `capture_layer_trial.py` | Bounded metadata-only SteamVR trial, with systemd-owned post-exit recovery |
| `capture_provider_check.py` | Supervise metadata inspection of an already running provider |
| `capture_service_lifecycle.py` | Read-only cgroup observations and bounded service transitions; no VR APIs |
| `capture_session.py` | Own a private capture run and recover only its recorded publication inode |
| `capture_supervisor.py` | Bound an explicitly prepared native capture worker |
| `metadata_session_trial.py` | Prepare an opt-in metadata layer trial across two platform session cycles |
| `service_rehearsal.py` | Rehearse SteamVR stop/start over SSH without installing a capture layer |
| `session_rehearsal.py` | Prepare and rehearse a SteamOS-managed graphical-session logout |

Their tests are `tests/test_capture_layer_config.py`, `test_capture_layer_trial.py`,
`test_capture_provider_check.py`, `test_capture_service_lifecycle.py`,
`test_capture_supervisor.py`, `test_metadata_session_trial.py`,
`test_service_rehearsal.py` and `test_session_rehearsal.py`. Native code lives in
`tools/capture_*` and `patches/mrhand/`, built by `scripts/build_capture_*.sh`.
The read-only diagnostics `shm_capture_check.py` and `capture_gpu_check.py` are
not part of this cluster.

`tests/test_scripts_boundary.py` enforces the quarantine: these modules import
only the standard library and each other; nothing else imports them except
their own tests; the rehearsal and trial source snapshots (`SOURCES`) name only
cluster files; and this section lists every cluster module.

A physical move is deferred until the track resumes, because it cannot be done
without touching frozen material. A top-level directory at the same depth keeps
the code byte-identical (`ROOT` is `parents[1]` and snapshots name sibling
files), but `patches/mrhand/0006-block-unleased-camera-reads.patch` prints the
`scripts/capture_provider_check.py` path, the on-device procedures and
`.github/instructions` globs name these paths, and any bundle already prepared
on Frame records them and would need regenerating.

## Dedicated capture build prerequisites

Collect metadata on the target machine before preparing a native capture build:

```bash
python3 scripts/device_check.py --capture-prerequisites
```

The focused report needs only Python's standard library. It runs bounded
`g++ --version` and `pkg-config` queries and inspects known paths with `lstat`.
It does not load Vulkan, initialize OpenVR, connect to a provider or read images.
The default command without this option still produces the original device
inventory. Missing tools/files are reported rather than installed.

The reviewed community layer needs a C++17 compiler and Vulkan development
headers, including `vulkan/vulkan.h` and `vulkan/vk_layer.h`. Its `ptgrab` producer
also links Vulkan. Runtime `libvulkan.so.1`, unversioned linker `libvulkan.so`,
headers and `pkg-config` metadata are separate prerequisites. Known-path presence
does not prove that a symlink resolves, a build links or the target GPU imports
camera buffers; these searches are not an exhaustive library discovery process.
Use missing-file results to prepare a pinned build dependency in an ignored
cache when appropriate, without changing SteamOS protections as a routine step.

The report also lists metadata for the reviewed community layer manifest,
library, enable marker, runtime socket and luma shared-memory path. Presence
does not establish compatibility or a running provider. No installer or capture
build is invoked. See [the operating procedure](on-device.md#check-native-capture-prerequisites)
and [the capture plan](live-capture.md#current-work-order) for the next device step.

### Compile the unmodified capture reference

```bash
bash scripts/build_capture_reference.sh
```

This build-only script compiles `handcam_layer.cpp` and `ptgrab.cpp` from the
reviewed `ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082` capture subtree. The clean,
pinned source cache is `.cache/capture-reference`; altered caches are retained
and refused. No upstream build/install script is executed. When the compiler
cannot find both Vulkan headers, it fetches Vulkan-Headers v1.4.309 at
`952f776f6573aafbb62ea717d871cd1d6816c387` into `.cache/vulkan-headers`, including
license texts. It links the producer directly to Linux `libvulkan.so.1`; this
checks loader linkage without requiring an unversioned development symlink.
The loader itself must already be installed. No package manager is invoked.

Outputs are `reference-libVkLayer_mrhand.so`, `reference-ptgrab` and
`build-report.json` in `artifacts/capture-reference/`. The report records source
and artifact SHA-256 digests, compiler target and header origin. Both ELF files
lack execute permission. No binary is run/loaded and no layer manifest is
created. `deployment_ready: false` is intentional: the upstream FD/socket,
publication-ordering and cleanup issues remain. These artifacts prove only
that the reference compiles and links on the tested machine. The provider
adaptation, configuration restoration and controlled live trial remain pending.

The dedicated CI job builds these inspection artifacts and checks their hashes,
ELF headers and permissions without executing them. For Frame, follow
[the combined build and reader-test procedure](on-device.md#build-the-capture-reference-without-activation).
That procedure now passed on Frame ARM64; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame).

### Build and test descriptor transport changes

```bash
bash scripts/build_capture_reference.sh --transport-fixes
artifacts/capture-transport-reference/framejacker-transport-fixture --self-test
```

The optional adaptation applies the [versioned patch](../patches/mrhand/README.md)
to a disposable copy. `tools/capture_transport.hpp` supplies bounded Unix-stream
record handling, owned close-on-exec FDs, same-user peer checks and registry
limits. It freezes one generation per layer snapshot and duplicates FDs while
the registry lock protects the originals. The receiver validates generation,
IDs, bounds and one FD per nonterminal record; it rejects truncated ancillary
data and closes received descriptors on rejection or a partial-record timeout.
The whole client exchange and each server send batch have a 500 ms deadline.

Separate inspection artifacts and a build report go to
`artifacts/capture-transport-reference/`; the report records the patch/helper
hashes and `upstream_sources_modified: true`. The two capture binaries retain
non-executable permissions. Only the original fixture is runnable, explicitly
with `--self-test`; it uses generated bytes and temporary sockets. CI runs its
seven groups and checks both build modes. Live layer callbacks, GPU imports and
provider publication are not exercised by those tests. The active plan owns
remaining provider work and the [device guide](on-device.md#test-the-transport-adaptation-without-capture)
owns ARM64 execution.
The user reports the adapted build and seven fixture groups passed on Frame;
see [E25](status.md#e25-descriptor-transport-on-frame).

### Build and test owned publication

```bash
bash scripts/build_capture_reference.sh --publication-fixes
bash scripts/build_shm_reader.sh
python3 -m unittest discover -s tests -p test_capture_publication.py -v
```

This mode applies both pinned adaptation patches and adds
`tools/capture_publication.hpp` plus the existing `shm_common.hpp`. Its separate
inspection artifacts are under `artifacts/capture-publication-reference/`.
The report includes both patch hashes and all helper hashes. No produced capture
binary is loaded/executed. Native fixtures are original test programs.

The publisher keeps the pinned 96-byte/two-slot ABI. It requires an owned private
parent directory, creates a new ordinary file exclusively, maps at most two
16 MiB planes, checks identity/size/permissions before publication and rejects
invalid or regressing metadata. Older or more-than-one-second-old observations
are skipped. The writer places a full barrier after the odd sequence marker
and release-publishes even after payload/metadata. This protects publication;
it does not synchronize the original XRService GPU writes.

Normal cleanup unmaps/closes and removes its identity-checked file; existing or
replaced paths are preserved. The future runtime source is
`$XDG_RUNTIME_DIR/framejacker-capture/frames.shm`. The adaptation drops image-dump
mode, parses explicit duration/rate/opt-in before capture work and checks a
1–10 second deadline/SIGINT/SIGTERM in its stream loop. Parent death uses SIGKILL;
its stale file may remain, and overwrite is refused. No automatic stale-file
recovery is implemented.

The eight process tests use the actual publisher helper, generated 1024 × 1024
luma slots and the existing reader. They check byte/metadata association in
both slots, no-overwrite behavior, replaced-file preservation, signals, deadline
and parent death/reaping. The native self-check also covers invalid bounds,
stale/order/counter handling, truncation before a write and private runtime paths.
CI builds the tools before these tests; absent binaries cause explicit skips
in environments that run discovery without building them.

The loop deadline cannot bound every driver call, and a concurrent truncation
can still fault a mapping between checks. Complete the external process watchdog,
GPU import/resource cleanup, registry refresh, layer lifetime and restoration
before activation. All eight publication tests also passed on Frame ARM64;
retain the [on-device procedure](on-device.md#test-owned-publication-without-capture)
for changed code/runtime.

### Build and test the GPU capability query

```bash
bash scripts/build_capture_gpu_check.sh
python3 -m unittest discover -s tests -p test_capture_gpu_check.py -v
```

The builder uses system Vulkan headers or the same pinned clean Vulkan-Headers
cache as the reference builder. It fetches no capture code and runs no output.
The report and binary are under `artifacts/capture-gpu-check/`. The original
native query uses `VK_NO_PROTOTYPES` and links `libdl`, not Vulkan; `dlopen` and
instance creation occur only after explicit opt-in. The default/invalid-option
tests use a fake loader constructor to verify that it is never loaded.

The opt-in queries Vulkan 1.1 physical-device interfaces; the reviewed path
requires a device advertising 1.3. It enumerates all devices without selecting
the first one, caps devices/extensions/modifiers/queues/memory types, and checks
the reviewed NV12 modifier plus external DMA-BUF sampled/transfer-source usage.
It reports memory planes, importability/dedicated-only flags, extent/layer limits,
sampler conversion, host-visible/cached types, graphics queues and R8 linear blit
features/transfer image limits. No logical device or camera access is involved.

`scripts/capture_gpu_check.py` provides a ten-second worker watchdog with bounded
stdout/stderr, process-group termination, SIGKILL fallback and worker reaping.
The native child sets parent-death SIGKILL and disables core dumps before loading
Vulkan. Normal/error paths destroy the owned instance and unload the library;
forced termination reports unconfirmed API cleanup instead of success. Existing
loader/layer configuration is not overridden, installed or removed.

The eleven tests build a test-only dispatch library in a private temporary
directory. They validate query structures and real function-pointer dispatch,
four memory planes for a two-color-plane format, absent features/extensions,
image/extent limits, oversized counts, driver errors, hangs during creation and
destruction, crash containment, cancellation, parent death and bounded output.
They make no GPU or camera calls. Native tests explicitly skip if the query is
unbuilt; the dedicated CI job builds it before running them. Hosted CI results
are separate from local results. For the real Frame query, use
[the supervised device command](on-device.md#query-vulkan-capture-capabilities).
The first Frame query now passes on Turnip Adreno 750 with clean teardown and
normal passthrough; see [E30](status.md#e30-vulkan-capabilities-on-frame). Its
target modifier reports two memory planes; the generic fake-loader test's four
planes are a separate synthetic case.

### Build and test image import ownership

This work can run without the headset, a camera or a working GPU driver:

```bash
bash scripts/build_capture_reference.sh --import-fixes
python3 -m unittest discover -s tests -p test_capture_import.py -v
```

The fourth build mode applies patches 0001–0003 to a disposable copy of the
same pinned capture source, adds `tools/capture_import.hpp`, and writes separate
inspection artifacts under `artifacts/capture-import-reference/`. The report
includes the import patch/helper hashes. The actual layer/producer stay
non-executable and are never loaded/run by this procedure. The executable
fixture is original synthetic code, defines `VK_NO_PROTOTYPES` and links no
Vulkan loader. It uses function pointers and generated `memfd` payloads.

The helper accepts only the reviewed 2464 × 2464 NV12 profile, **one** array
layer, DMA-BUF, DRM modifier `0x0500000000000001`, sampled/transfer-source usage
and **zero** binding offset. It creates non-disjoint exclusive images. Exporter
flags/sharing mode are absent from this wire contract and remain unverified.
A candidate's
metadata is checked before the adapted producer initializes Vulkan. Declared
allocation size is capped at 1 GiB; active plane offsets/pitches must fit at
least one row within it, and unused plane metadata must be zero. Compressed
memory planes are checked against the driver's modifier list, not inferred
from the format's two color planes. This is a conservative initial profile;
other formats, offsets, flags and source types need separate validation.
Arrayed images are rejected because this record has no per-plane `arrayPitch`;
the importer must not invent that layout. For a single layer and depth one,
the explicit modifier layout's array/depth pitches are correctly zero under
[Vulkan 1.4.309's layout rules][modifier-layout].

At import, bounded modifier and external-image queries validate the actual
physical device's format/usage, importability and extent/layer/resource limits.
The allocation type intersects FD and image memory-type bits with the physical
device's valid type range. Zero/oversized requirements and invalid alignment
are rejected. Dedicated allocation uses the exact image requirement size,
which must fit within the declared allocation, as required by
[Vulkan 1.4.309, VUID 02964][dedicated-allocation]. It does not use the upstream
`max(requirement size, declared size)` allocation. The actual external FD length,
full compressed-plane footprint, and exporter GPU/driver provenance are not
established by these metadata checks.

An imported image is move-only. Its duplicated CLOEXEC FD remains application
owned until `vkAllocateMemory` succeeds; failure closes it, and success transfers
it to Vulkan without touching it again. Failed image creation does not acquire
an undefined output handle. Allocation/binding failure unwinds owned resources;
normal retirement destroys the image before freeing its memory. Partial-batch
failure also retires earlier images.

The shared serial-queue submission helper checks fence reset, queue submission
and its two-second completion wait. Command reset/begin/end and mapped-memory
invalidation results are also checked in the adaptation. Only a successful
completion wait permits imported-image destruction. An uncertain submission
leaves the gate non-idle; cleanup marks deferral and abandons API destruction
to process teardown. The borrowed device and completion gate must outlive all
image owners. They are confined to one worker thread. No unbounded idle wait
is added, but a driver call itself can still hang: the capture supervisor
remains to be implemented. This gate does not synchronize XRService's writes.

Eleven Python tests execute ten native fixture groups plus default/argument guards.
They cover malformed metadata before driver calls, unsupported modifier/memory
planes, format/limits, requirement bounds, memory bit 31, FD transfer and cleanup
order, failure injection at FD query/create/allocate/bind, move/vector ownership,
batch rollback, pending-work deferral, and reset/submit/wait failures. An explicit
two-plane modifier case matches the count advertised in the Frame capability
report; its offsets/pitches are generated, not observed camera metadata. The
generic four-plane supported case remains covered, and a four-plane record is
rejected when the driver advertises two. The native
fixture counts FDs before/after each group. Deferred resources in its fake
backend are reclaimed by simulated process teardown, not described as graceful
Vulkan cleanup. CI builds before running these tests; unbuilt native fixtures
produce explicit skips elsewhere.

The original ten-test suite and import build also passed on Frame ARM64;
the eleventh two-plane case has only been run in the cloud. The GPU query reports
two modifier memory planes and dedicated-only imports on Turnip Adreno 750;
see [E30](status.md#e30-vulkan-capabilities-on-frame) and
[E31](status.md#e31-import-build-and-synthetic-tests-on-frame).
Compile/link and synthetic tests do not validate a real import. The earlier
import-only mode still leaves device and other GPU resources unmanaged; the
resource adaptation below addresses those lifetimes. Capture supervision,
provider metadata/registry and layer lifetime/restoration remain pending. See the
[offline work order](live-capture.md#offline-provider-work-while-the-frame-is-unavailable)
and [optional native fixture procedure](on-device.md#test-import-ownership-without-capture).

[dedicated-allocation]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/memory.adoc#VUID-VkMemoryDedicatedAllocateInfo-image-02964
[modifier-layout]: https://github.com/KhronosGroup/Vulkan-Docs/blob/v1.4.309/chapters/resources.adoc#VkImageDrmFormatModifierExplicitCreateInfoEXT

### Build and test GPU resource ownership

```bash
bash scripts/build_capture_reference.sh --resource-fixes &&
python3 -m unittest discover -s tests -p test_capture_gpu_resources.py -v
```

This fifth mode applies patches 0001–0004 to disposable pinned source and adds
`tools/capture_gpu_resources.hpp`. Separate artifacts/report live under
`artifacts/capture-resources-reference/`; the report includes the new helper and
patch hashes. The real layer/producer remain non-executable inspection binaries
and are never loaded/run by this procedure. Only the original synthetic fixture
runs. It defines `VK_NO_PROTOTYPES`, links no Vulkan loader and uses injected
function pointers, fake handles and a generated memfd for import lifetime tests.

Bootstrap requires loader/device Vulkan 1.3, the four reviewed import extensions,
YCbCr conversion, graphics queues, host-visible/cached and device-local memory,
and the reviewed R8 downscaling capabilities. Counts are bounded. CPU devices
are excluded; multiple compatible hardware candidates are rejected for explicit
selection instead of choosing the first. This capability policy does not prove
which physical device exported a camera FD. NV12/modifier/FD checks still occur
per import through the existing import helper.

One shared state owns instance, device, command pool and the serial-queue
completion gate. Move-only private resources and imported-image wrappers retain
that state until their own destruction. Host buffers own their mapping and
memory; R8 images own dedicated memory; command bundles own buffer/fence pairs.
Normal teardown unmaps, destroys resources before freeing bound memory, and
releases pool/device/instance last. Explicit device close refuses live children.
Imported images also retain the device across wrapper moves and parent scope exit.

Private allocations use exact dedicated requirement sizes, compatible memory
types and a **256 MiB aggregate budget**. Host buffer requests are capped at
64 MiB; scratch dimensions at 2464 × 2464. Coherent cached host memory is
preferred; cached noncoherent memory has checked invalidation over the whole
mapped allocation. The budget covers new private allocations, not the imported
shared camera allocations bounded separately by the import/transport contract.

If submitted work is unconfirmed, all remaining resource/parent API destruction
is deferred to process teardown. A deferred state stays unusable even if a later
caller marks its queue idle; it cannot recover abandoned handles. No unbounded
device-idle wait is added. Individual create/destroy calls can still hang, so the
capture watchdog remains necessary before actual driver execution. This gate
tracks only our queue and does not synchronize XRService's writes.

Nine tests execute eight native groups plus default/argument guards. They
exercise normal and partial teardown, 57 injected failures (three passes over
19 fallible startup/private-resource calls), graphics-family selection,
ambiguous devices, API/count/size/budget limits, noncoherent fallback, memory
bit 31, moves, child-retained devices, cross-context imported-image moves, and
deferred cleanup. The fake backend checks the resource graph and models process
reclamation separately from graceful Vulkan destruction. Missing fixture builds
produce explicit skips; CI builds this mode before running its tests.

This implementation is cloud-tested with fake dispatch, not actual logical-device
creation, command execution or Frame ARM64 validation. The
[native fixture procedure](on-device.md#test-gpu-resource-ownership-without-capture)
can be batched with a later milestone. Follow the
[next work order](live-capture.md#next-engineering-step-capture-worker-supervision)
before deployment.

## Capture watchdog, provider lifecycle and restoration preparation

The sixth inspection mode now applies patches 0001–0006 and builds the v3
metadata-only provider, diagnostic and actual-layer-hook fixtures. The previous
v2 milestone passed 10 watchdog, 11 provider and 7 recovery tests on Frame ARM64;
see [E35](status.md#e35-watchdog-provider-and-recovery-tests-on-frame). For a full
development regression, build the native exec boundary and run:

```bash
bash scripts/build_capture_supervisor.sh &&
bash scripts/build_capture_reference.sh --provider-fixes &&
python3 -m unittest discover -s tests -p test_capture_supervisor.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider.py -v &&
python3 -m unittest discover -s tests -p test_capture_provider_check.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_config.py -v &&
python3 -m unittest discover -s tests -p test_capture_service_lifecycle.py -v &&
python3 -m unittest discover -s tests -p test_capture_layer_trial.py -v
```

The current cloud suites contain **10 watchdog, 14 provider/layer, 12 metadata
diagnostic, 8 configuration/recovery, 9 service-observation and 48 trial tests**. These use generated files,
local sockets and fake driver dispatch. Neither the actual layer nor the
camera producer is loaded/run. The real binaries remain mode 0644 in
`artifacts/capture-provider-reference/`; the report records helper, patch and
artifact hashes and `deployment_ready: false`. All earlier build modes remain
available. The earlier v3 ARM64 build and both 12-test suites passed on Frame;
see [E37](status.md#e37-metadata-only-provider-and-diagnostic-on-frame). The metadata inspector is
a separate executable, linked without Vulkan; it is not the camera producer.
The trial runner was later attempted on Frame and failed during service control.
Its revised lifecycle controller remains cloud-tested; the read-only cgroup/
compositor observation now passes on Frame. Actual service transitions remain
unvalidated; see [E46](status.md#e46-read-only-runtime-baseline-on-frame).

### Worker containment and publication recovery

[`capture_supervisor.py`](../scripts/capture_supervisor.py) defaults to `not_run`
(exit 3). Its explicit `--capture` branch now returns `blocked` (exit 3), with
`producer_access_contract_unavailable`, before starting a process. Patch 0006
also replaces the latest producer with an access-gate stub. There is no
installer or executable-enabling procedure. The internal supervision helper
below remains tested against generated workers for a future synchronized copy.

[`capture_worker_exec.cpp`](../tools/capture_worker_exec.cpp) establishes
`PR_SET_NO_NEW_PRIVS`, SIGKILL parent-death handling, a parent-PID race check and
a zero core-dump limit before loading the worker's libraries. It executes an
already opened, owned, non-writable-by-others ELF descriptor, avoiding a
pathname substitution between the supervisor's check and exec. Only the required
FD is inherited. The stream retains its own signal/deadline checks, but an
external deadline covers startup, imports, copying and destruction too.

The outer budget is the requested 1–10 seconds plus three seconds. On timeout
or cancellation it requests SIGTERM, allows 0.3 seconds, then uses SIGKILL if
needed, with a bounded one-second reap attempt. A kernel task stuck in
uninterruptible I/O may remain unreaped; the report must say so, and its files
are preserved. Output has a 256 KiB total cap, an 8 KiB event-line cap and a
256-event cap. Partial/malformed/inconsistent completion reports are failures.
Clean cancellation is distinguished from forced termination. A killed process
does not establish graceful Vulkan API cleanup.

Each run uses an exclusive mode-0700
`$XDG_RUNTIME_DIR/framejacker-capture-*/` directory and a locked, mode-0600 lease.
The worker publishes to its assigned `frames.shm` and announces device/inode/size.
The supervisor records those identities before recovery and removes a remaining
publication only after reaping that worker and matching the private file. It
preserves replaced, unannounced or unknown files. A very short worker may remove
its publication before the event is consumed; an already absent file needs no
unlink. The reader can later use the report's exact session path with `--source`;
the existing reader default remains the upstream compatibility path.

For a known dead session, `--recover-session PATH` checks the locked lease and
recorded `/proc` process start times before removing only its recorded file.
It refuses a living run or unconfirmed identity. No automatic directory sweep
or unconditional stale-file deletion occurs. The tests cover hangs at each
GPU phase, crashes/output limits, signals, parent death, graceful deadline
retirement, exact recovery and preservation of foreign files.

### Provider protocol and layer ownership

[`capture_provider.hpp`](../tools/capture_provider.hpp) defines a separate v3,
328-byte native little-endian/64-bit packet ABI, not an extension sent to old
MRHAND clients. The endpoint is
`$XDG_RUNTIME_DIR/framejacker-camera-provider/camera.sock`; there is no `/tmp`
fallback. An explicit first request chooses metadata or image buffers; the
server rejects buffer access because no producer handoff is available. Old
protocol clients/providers fail closed. Snapshot records retain
the original 144-byte buffer description inside the new packet and add an
epoch, generation, logical-device token, physical-device/driver UUIDs,
image flags/type/sharing/depth/mips/samples, external handle types, memory flags
and per-plane array/depth pitches. Unknown creation/allocation chains are marked
incomplete and rejected for capture. Current policy accepts only the existing
single-layer profile with exclusive sharing and zero unsupported flags/pitches.

The registry scopes image/memory handles by logical device, bounds counts and
assigns nonreused buffer IDs. The production layer records allocation metadata
without retaining the imported image FD; Vulkan retains its normal ownership
rules. Metadata snapshots duplicate no image descriptors. Descriptor-owning
registry operations remain only for generated ownership fixtures; the server
never grants descriptor access.
Image destruction, binding, memory changes and device teardown advance the
generation. The original layer implementation uses shared callback state and
exact device-handle lookup; two devices may share a dispatch-table pointer.
Device destruction removes only its registry entries. The last device stops
and joins the owned server thread before releasing its callback state. Registry
errors invalidate capture without changing successful downstream Vulkan results.

The metadata consumer holds a bounded socket lease and checks epoch/generation.
Provider loss, timeout, invalidation or changed registry ends the attempt; it
never reconnects to a different provider. Registry stability describes resource
bindings, not XRService frame completion or reuse. UUID metadata identifies a
GPU/driver, not a physical camera or stereo role. Future pixel access must
establish the [producer ownership and synchronization contract](live-capture.md#producer-synchronization-review-and-next-checkpoint)
before reusing the tested import helpers.

The server uses a private directory, same-user peers, an exclusive file lock,
bounded I/O and identity-checked endpoint removal. A crash leaves a recorded
socket identity and owner PID/start time. Explicit provider-runtime recovery
locks that lease and removes the socket only after the recorded process is gone
and the endpoint still matches. Competing/live providers and foreign replacements
are preserved. Server shutdown interrupts an idle client; there is no detached
thread. The opt-in gate permits only `vrcompositor` with
`FRAMEJACKER_CAPTURE_LAYER=1`, and allows at most two accepted starts in 120
seconds using locked private runtime state. Other processes/default invocation
cannot start a provider; the setting must not be enabled globally. The gate is
enforced at device creation as well as negotiation, because the loader can
fall back to legacy entry points after a negotiation failure. Denied processes
forward calls without observing resources or creating a runtime endpoint.

### Inactive configuration bundle

[`capture_layer_config.py`](../scripts/capture_layer_config.py) defaults to no
action. `--prepare NEW_DIRECTORY` creates a private local bundle containing a
disabled-by-default manifest, the inspection library and a checksummed plan.
It snapshots exact existing bytes/modes or absence at the two proposed
Framejacker manifest/library paths. It does not write those active paths,
install anything, set environment variables or restart a runtime. Backups may
contain local configuration and remain in ignored artifacts.

The implemented restoration path preflights both targets and changes only a
file matching either its recorded prior state or staged state. Later edits,
symlinks, hardlinks, FIFOs, parent symlinks, changed hashes and a different home
are refused. Original bytes/modes are restored; originally absent files are
removed. Partial restoration is repeatable. No unrelated MRHAND, Vulkan or
SteamVR configuration is changed. Restoring files cannot unload a library from
a running compositor; a controlled restart remains part of a future approved
device procedure. Tests emulate staging only inside generated temporary homes.

See the [device preparation procedure](on-device.md#test-the-watchdog-and-provider-without-capture)
and [remaining live gates](live-capture.md#next-engineering-step-capture-worker-supervision).

### Metadata-only provider inspection

[`capture_provider_inspect.cpp`](../tools/capture_provider_inspect.cpp) uses the
v3 metadata request and shared profile validators, without linking/loading
Vulkan, requesting image FDs or reading pixels. It reports metadata for at most
64 records: dimensions, format, allocation/binding, modifier/plane layout,
exporter flags, UUIDs, epoch/generation and peer credentials. Profile rejection
is recorded per entry; an empty or unsupported registry can still be inspected
successfully. Compatibility is only a metadata predicate, not successful import,
camera identification or synchronization proof.

The standalone executable defaults to `not_run` (exit 3). Explicit `--inspect`
checks an existing provider for 1–5 seconds (default 1), using 500 ms exchanges.
[`capture_provider_check.py`](../scripts/capture_provider_check.py) supervises it
with a duration-plus-two-second deadline, bounded output, signal/parent-death
handling and a validated report. It installs/starts no provider and writes no
files. `inspected` exits 0; missing/changed/old providers, malformed reports,
unexpected descriptors and timeouts fail with exit 1. Cancellation exits
`128 + signal`; forced termination does not claim graceful cleanup.

The 12 generated-data diagnostic tests cover matching/empty/unsupported metadata,
generation changes, provider loss, endpoint/protocol/descriptor rejection,
invalid/default arguments, corrupt/oversized output, hangs, signals, parent
death and the blocked capture CLI. Build with `--provider-fixes`, then run
`test_capture_provider.py` and `test_capture_provider_check.py`. The fixtures
generate their own peers in private temporary directories; no real provider
or layer is needed. Native v3 execution passed on Frame; controlled layer
activation and restoration remain separate checkpoints.

### Controlled metadata-layer trial and recovery

**Device activation is paused** following service-control failures on Frame;
see [E41](status.md#e41-frame-trial-report-and-service-control-failures). The CLI rejects
`--start-metadata-test` with `blocked`/exit 3 before runtime/file calls. It has no
CLI bypass. Reporting, known-run recovery and read-only preflight remain usable.
The following describes the quarantined implementation, not a current launch
recommendation. The runner restores SteamVR but never relaunches a console app.

[`capture_layer_trial.py`](../scripts/capture_layer_trial.py) defaults to
`not_run`/exit 3. `--preflight BUNDLE` checks a fresh metadata-only build,
matching bundle/checksums/home, absent prior Framejacker targets, no existing
provider or enable flags, and the exact reported SteamVR service properties.
It also requires an idle service job and a live compositor in the original
unified cgroup, with a consistent invocation ID across baseline observations.
It changes nothing. Existing configurations or differing service properties
are refused rather than overwritten or guessed.

`--start-metadata-test BUNDLE` first runs a one-second dummy user unit, requiring
both its started marker and systemd's timeout/`ExecStopPost` completion marker.
The dummy never touches SteamVR. Only then does it launch an independent
transient trial service. The worker arms a private rollback plan before stopping
SteamVR, stages the metadata-only library/manifest plus a runtime service-scoped
enable drop-in, reloads the manager and starts SteamVR. It waits at most ten
seconds for the endpoint, performs a two-second metadata inspection, and checks
the peer's UID, executable and membership in the SteamVR service's cgroup.

Systemd runs a **separate** `ExecStopPost` command after success, failure or
termination. Recovery observes existing jobs before requesting any stop,
removes only the exact trial drop-in,
conditionally restores only recorded targets, recovers an owned stale provider
endpoint if necessary, reloads the manager and starts the normal service.
It preserves later edits, reports failures and refuses a living trial worker.
An unchanged healthy configuration needs no restart if the worker never reached
staging; restored files alone cannot prove that a loaded layer was unloaded.
Repeated recovery of an
already restored active service rechecks liveness without another stop/start.
The trial service
has `RuntimeMaxSec=60`, `TimeoutStopSec=60`, `Restart=no`; execution and recovery
can take about three minutes on a timeout path. A failed service stop, manager
failure or unkillable kernel task cannot be reported as successful restoration.

State, metadata and final reports live in ignored private
`artifacts/metadata-trials/trial-*/` directories. `--report-latest` reads them
without runtime calls. An ambiguous launch acknowledgment is `launch_unconfirmed`,
with the attempted run path retained; inspect/recover that run before retrying.
No image descriptors, pixels, inference or VR panel output are requested.

The 48 trial tests exercise real temporary configuration and generated platform
hook/job events. A separate nine-test suite checks generated cgroup/proc files
and virtual deadlines; eight configuration tests cover exact restoration and
edits during a runtime guard. All 65 targeted tests pass in the cloud. They do
not establish actual user-manager execution or vendor hook side effects; static
unit syntax was verified in the cloud, and the real recovery rehearsal is
mandatory on Frame before the trial. The [device procedure](on-device.md#run-the-bounded-metadata-layer-trial)
contains opt-in, expected reports and manual recovery.

Worker reports now include the failure phase; private worker state checkpoints
the phase before each action. `ServiceCommandError` records operation and exit
code, with at most 4 KiB of stderr for exact service start/stop/daemon-reload
commands, including bounded timeout diagnostics. Rollback records each command's phase separately. Command stdout and
environment-query stderr are never attached; environment-query values are
excluded from diagnostic reports. These diagnostic changes do not remove the
activation pause or establish a fix for the device's service-control failure.

`--service-details` makes a single read-only `systemctl show` query for selected
status, dependency, stop-propagation and policy fields. Exec* properties are
reduced to executable paths. Each repeated property line contributes one
executable in order; duplicate paths are preserved. Only the leading record
path is read, so `path=` text inside argv is excluded. Unrecognized nonempty
hook records return `refused`/exit 1 with the property name, never their raw
content. Command arguments, environment values and unrequested fields are
not returned. Tests cover the shipped hook order, argument exclusion, format
refusal and the single read-only query without activation or file changes.
See [the platform source findings](status.md#e44-platform-launcher-source-and-repeated-hook-diagnostic-fix)
and [required lifecycle coverage](live-capture.md#steamvr-service-control-acceptance).

[`capture_service_lifecycle.py`](../scripts/capture_service_lifecycle.py) queries
no VR API. It observes the original cgroup's `cgroup.events`, including descendant
population, and reads bounded cgroup process lists plus `/proc` executable,
owner, state, start-time and membership metadata. Missing hierarchy/permission
data is unknown, not an empty group. The implementation currently requires
readable unified cgroups. `systemctl show --all` retains an explicitly empty
`Job=` field, which is normalized to idle (`0`); an absent Job remains unknown.

A transition submits at most one service command, then observes its result
within 25 seconds, sharing a 55-second controller deadline. The command timeout
is at most 20 seconds, leaving observation time within the existing 60-second
systemd watchdogs. A successful stop requires no job, an inactive/failed unit,
zero MainPID and an empty original cgroup across two observations. Startup
requires active/running/success, an idle job, an invocation ID and a stable live
compositor. Pending jobs are observed before recovery issues another command.
Intent, phase, command outcome and changed observations are saved before the
next recovery decision.

The worker rechecks stop state before staging files; recovery rechecks it before
each restored file, followed by another file-content check to preserve edits
made during the observation. A competing controller can still act between
observations and writes; these checks do not supply atomic exclusion against
outside service controllers. Failed restoration never triggers an automatic
normal-service start. A failed command followed by an observed transition may
allow cleanup to proceed, but its error remains in the report and recovery
exits 1. Configuration restoration, a confirmed start command and
`normal_runtime_observed` are separate fields. If the unchanged service was
already running, `normal_service_already_running` is true instead of claiming
that recovery started it. Aggregate completion additionally requires runtime
observation evidence; an older `restored` label alone is insufficient.

`--runtime-status` performs only service/cgroup/proc reads and two liveness
observations, with a 12-second controller budget. It needs no build or inactive
bundle. `runtime_observed`/exit 0 establishes process liveness;
`runtime_unconfirmed` or `refused` exits 1. It returns
`rendering_readiness_verified: false` and requires a separate user passthrough
check. It does not restart SteamVR, initialize OpenVR/Vulkan, connect to the
provider or read images. The
[read-only Frame check](status.md#e46-read-only-runtime-baseline-on-frame)
now passes on-device; passing it does not unblock activation or validate the
new controller's actual service transitions.

## Isolated luma reader and synthetic producer

The shared-memory diagnostic is independent of inference, Qt, OpenVR and Vulkan.
Build it with the existing Linux C++17 compiler on x86_64 or ARM64:

```bash
bash scripts/build_shm_reader.sh
python3 -m unittest discover -s tests -p test_shm_capture_check.py -v
```

No SDK download, package installation, capture-layer activation or producer
startup occurs during the build. Native tests skip if the binaries are absent;
the dedicated CI job builds them first. With PyAV installed, the preview test
also decodes the actual PNG. The user reports all 14 tests passed on Frame ARM64
in 7.204 seconds; see [E23](status.md#e23-reference-build-and-controlled-reader-on-frame).

For a manual generated-image check, run this producer in one terminal:

```bash
mkdir -p artifacts/shm-check
artifacts/shm-reader/framejacker-shm-fixture \
  --create artifacts/shm-check/new-synthetic.shm --seconds 10 --mode split
```

While it is running, use a second terminal in the same checkout:

```bash
python3 scripts/shm_capture_check.py --read \
  --source artifacts/shm-check/new-synthetic.shm --camera 1 --seconds 3
```

The fixture publishes two 64 × 48 grayscale slots with differing uniform
intensities and deliberately split writes. It prints explicit synthetic
ready/closed events, runs for a bounded duration and leaves its ordinary file
for inspection. Use a new filename for each run; existing files and the
upstream `/dev/shm/mrhand-pt` path are refused. It does not acquire camera
images. Other controlled modes exercise unfinished, old, stalled, truncated,
replaced and restarted publications. Both tools require explicit arguments;
running either native binary without arguments returns `not_run`/exit `3`.

The pinned [protocol header and notice](../third_party/mrhand/README.md) define
a 96-byte native header with two 32-byte slots, followed by `cams` luma planes.
Only 1–2 slots and at most 16 MiB per plane are accepted. The consumer requires
lock-free aligned 64-bit atomic sequence access, a stable even sequence around
each bounded copy, valid monotonic observation time and an unchanged source.
It maps read-only and performs at most ten copy attempts per second.

The original fixture adds a full writer barrier after the odd marker and a
release-store of the final even sequence. The reader uses acquire loads and a
barrier before its final sequence check. These controlled x86_64 checks do not
validate the upstream writer on ARM64 or original GPU-image completion. That
writer needs review/correction before a dedicated-provider trial. The layout
also lacks a generation field; observable counter regressions are rejected,
but not every restart can be detected between samples.

The Python supervisor contains native mapping faults (including a truncation
race) and enforces a deadline plus bounded terminate/kill cleanup. Linux
parent-death signaling prevents a stranded native reader; core dumps are
disabled in that reader. Optional PNG output is encoded only after input
closure and published atomically to a new path (`runtime/output.py` `publish_new`). No pixels are saved by default.
See [the device procedure](on-device.md#check-existing-luma-shared-memory) for
existing-provider usage and [evidence](status.md#e9-shared-memory-reader-cloud-checks)
for actual validation limits.

## Service-only recovery rehearsal

`scripts/service_rehearsal.py` reuses the service/cgroup observer and private-file
helpers without preparing or installing a capture layer. Its default does
nothing; `--preflight` reads service policy and verifies absent capture targets.
`--report RUN` and `--report-latest` read saved evidence without runtime calls.

The launcher requires independent inbound SSH and an inactive trial unit. It
checks systemd post-exit cleanup with a dummy timeout, snapshots its Python
helpers into a private run, persists recovery intent before stopping SteamVR,
and uses a separate transient unit with a 60-second worker limit and a
60-second stop phase. Recovery observes an already healthy service or starts
it only after confirmed shutdown; pending jobs and surviving compositor
processes prevent competing starts. Service errors remain failures even when
the runtime later recovers. No service configuration or layer files are written.

The first Frame run exposed the shipped Gamescope `PartOf=steamvr.service`
relationship and a canceled stop job. The current preflight refuses that
relationship before scheduling anything. Keep this guard while designing a
session-aware lifecycle; the earlier independent-service model does not fit
this runtime. [Device evidence](status.md#e63-service-only-rehearsal-and-graphical-session-dependency)
separates the failed rehearsal from observed runtime recovery.

Run the focused Linux tests without contacting systemd or a camera:

```bash
python3 -m unittest tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

The rehearsal suite covers interrupted workers, command failures, pending jobs,
surviving processes, source snapshots, repeated recovery, later configuration
edits, SSH gating, session-policy refusal and persisted phase diagnostics. The
observer also handles `ESRCH` when a process exits while its metadata is read,
without treating that as proof the entire cgroup is empty. The configuration
suite's native provider fixture may skip if it has not been built.

## Platform-managed session rehearsal

[`session_rehearsal.py`](../scripts/session_rehearsal.py) is a separate test of
SteamOS Manager's `switch-to-game-mode` operation. It preserves the bare-service
guard above and the metadata activation block. The vendor source at
[`v26.4.1`, commit `a0d399d`](https://gitlab.steamos.cloud/holo/steamos-manager/-/blob/a0d399defebd60db30ba38325d9cf47dbda49bee/steamos-manager/src/session.rs)
selects a temporary game-session login and stops `graphical-session.target`
with `JobMode::Fail`. SDDM starts the next session. This does not promise a
stable stopped interval for layer installation.

The new tool defaults to no action. `--preflight` reads only the platform and
runtime. `--prepare NEW_BUNDLE` saves an inactive, exclusive bundle of helper
sources, hashes, platform fingerprints and baseline metadata. `--check BUNDLE`
validates those helpers, the installed command/daemon/session files, package
version, active D-Bus method, default game mode, absent capture configuration
and stable baseline. It refuses changed platform files or foreign drop-ins.

Only `--start BUNDLE --allow-session-logout` may request a logout, and it
requires independent inbound SSH. It first verifies post-exit cleanup using a
dummy systemd timeout, then creates a private run with its own source snapshot.
Its transient unit is in `app.slice`, with no graphical-session dependencies,
a 120-second runtime limit and a 45-second stop phase. The worker persists
intent before issuing exactly one vendor request. Failures/timeouts are never
retried, since they may still have initiated logout. The optional `--wait`
waits up to 180 seconds for saved results; disconnecting does not cancel the
independent service.

Completion requires new invocation IDs for Gamescope, SteamVR and both session
targets; all must be healthy with no pending jobs, a live compositor and an
active SDDM. A stable identity must persist for two seconds. Unchanged or
partially restarted sessions cannot pass. An independent post-exit observer
waits at most 30 seconds for normal runtime. It never issues another logout or
direct start/stop. If the platform does not recover, it saves a failure for
inspection instead of competing with platform startup. Human confirmation of
UI, passthrough and tracking remains separate from process-liveness evidence.

```bash
python3 -m unittest tests.test_session_rehearsal tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

The 21 new generated-data tests cover full/partial/no-op transitions, identity
churn, observation deadlines, late results, request failures with later recovery,
worker interruption, source/platform drift, conflicting units, capture-file
preservation, session-independent launch properties and duplicate-request
prevention. The [device checkpoint](status.md#e64-platform-session-test-completed-and-headset-confirmed)
now has a successful real session request, healthy independent recovery and
the user's normal-headset confirmation. Metadata activation/restoration
remains separate work.

## Metadata trial across platform sessions

[`metadata_session_trial.py`](../scripts/metadata_session_trial.py) prepares a
separate metadata-only activation/restoration trial using the validated vendor
session request. The old bare-service runner remains blocked. Preparation
requires a completed session-proof run on the matching platform and a verified
v3 metadata bundle; it freezes Python helpers, the native inspector, library,
manifest template and platform fingerprint in an exclusive private directory.

The ownership strategy does not require holding SteamVR stopped:

1. Each run gets a new library path. Its bytes are never replaced or removed,
   including during failed recovery. The normal fixed library path stays absent.
2. The implicit manifest points to that library and retains the existing opt-in
   environment condition. The [Khronos loader contract](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderLayerInterface.md#layer-manifest-file-format)
   gates implicit loading on the application environment. Current compositor
   capture flags must be absent before preparation/activation.
3. An exact runtime drop-in sets only `FRAMEJACKER_CAPTURE_LAYER=1` for future
   `steamvr.service` starts. A manager reload does not modify the current
   process's environment. The worker records cleanup intent before exposing
   configuration, then requests one platform session cycle.
4. After a stable new session, the inspector waits at most eight seconds for
   provider startup, then inspects metadata for two seconds. Peer PID/start
   identity, service cgroup, library device/inode mappings, socket ownership
   and unchanged session generation must match. Zero candidate records is
   a valid metadata result, not evidence of a camera source.
5. Independent `ExecStopPost` cleanup removes only the exact trial drop-in and
   manifest, reloads the manager and requests the normal session once. Foreign
   edits are preserved. Existing startup jobs must settle before that request;
   ambiguous requests are never retried.
6. Restoration requires healthy new runtime, absent capture flags and library
   mappings in its compositors, retirement of the identified provider, and
   absence of the provider endpoint. A stale endpoint is removed only after
   matching its recorded lease/PID/start/device/inode. Unknown endpoints are
   preserved. Library/source artifacts remain for diagnosis.

The transient service runs in `app.slice`, independently of the graphical
session and initiating SSH terminal. The worker has a 150-second runtime limit;
the stop phase has a 180-second timeout. `--wait` waits at most six minutes for
saved results. Camera bytes, image FDs, detector and VR output are not part of
this trial. The native v3 provider still rejects buffer requests.

The new 35-test suite uses temporary files and generated session/provider
events. It covers two cycles, interrupted/partial setup, delayed or missing
providers, command ambiguity, later edits, source drift, peer ownership,
mapping identity, stalled sessions, cleanup failures and duplicate requests.
After the first real trial returned `provider_registry_changed`, the v2
orchestrator adds bounded settling: at most four clean metadata inspections in
12 seconds, with one-, two- and three-second gaps after invalidations, subject
to the shared deadline. Each native attempt still requests a full two-second
unchanged registry. Transport/protocol failures, unclean exits, session/socket/
peer changes, changed epochs and regressing generations stop immediately.
Every raw attempt and a settling summary are retained; later success does not
hide earlier invalidations. A failed final snapshot is never used as stable.
The original native inspector and provider protocol are unchanged.

```bash
python3 -m unittest tests.test_metadata_session_trial tests.test_session_rehearsal tests.test_service_rehearsal tests.test_capture_layer_trial tests.test_capture_service_lifecycle tests.test_capture_layer_config -v
```

Both operator runs loaded the provider and restored normal startup. V2 still
invalidated every registry inspection, but the eight candidate metadata records
match across all three saved snapshots. Its normal headset recovery is confirmed.
See [E65](status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed).
Do not repeat v2 unchanged; investigate scoped lifetime/version tracking offline.

## Edge HUD offline test plan

The earlier implementation session reported the synthetic-only path;
[E69](status.md#e69-offline-edge-hud-and-native-mask-validation) preserves its
reconstructed provenance and the separate cloud recheck. The original session
log is not committed. Generated tests cover full-footprint native masking before resize,
margin/stereo union, out-of-bounds and missing mappings, capture/config changes,
fixed strip containment, owned buffers, source/result association and stale
expiry independent of inference/settings. Use generated inputs without a headset.
Existing lifecycle/expiry tests remain relevant but do not establish HUD mapping,
head-motion leakage or physical hiding. Those require the separately approved
[device checkpoint](on-device.md#bounded-edge-hud-calibration-checkpoint).

For a future justified rerun in the local Linux replay/window environment:

```bash
python -m unittest tests.test_hud_mask tests.test_hud_fill tests.test_live_pipeline tests.test_display_expiry tests.test_mirror_recognize -v
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --validate-overlay --seconds 3
```

These use generated input and native validation only, with the existing built
bridge and detector assets. `--synthetic-hud` and `--detector` require
`--synthetic-check`; they do not load hardware calibration or enable live
input/real output. `--detector` takes a registered model name (`DETECTORS`
through `create_detector`; the default is `mobilenet-ssd`, and `real` remains
an alias for it) or `fixture`. Since the
[synthetic HUD validation counts](#synthetic-hud-validation-counts), the second
command is also a validation run. With the default detector it ends
`completed` (exit 0) since the [neutral mask fill](#hud-mask-fill-evaluation);
with the zero-filled mask it ended `validation_failed` (exit 1), because
MobileNetSSD boxed the black footprint region of the generated frames.
`--detector fixture` reports no detections and checks the mask, ledger and
transport without a model. The earlier session reported 57 tests; that absent
log is not a new execution result.

The later [marker-only harness](status.md#e70-offline-marker-only-harness) adds
`hud_marker_check.py` and native generated-marker validation. Its final saved
Linux log reportedly passed 49 tests; that original log is absent. The
marker-only harness does not itself observe mirror
corners, qualify mapping or demonstrate physical expiry. The later
[ARM64 build and empirical observer](status.md#e71-offline-arm64-build-and-empirical-marker-observer)
have offline evidence. Subsequently recovered [E75](evidence/e075.md) establishes
that the historical V4 ARM64 binary and observer ran on Frame and were rejected;
the software stop is explained but physical recovery remains unresolved. A
future clean observer result stays unqualified. Observer suite counts are implementation-session reports,
including a Windows symlink-privilege error, not an all-platform pass or an
independently inspected standalone log. No device execution is authorized.

Earlier deployment preparation reported a local eight-entry manifest (ARM64
bridge plus seven Python modules), with size/hash checks during that review.
The manifest is not committed and does not pin the current implementation; a
new reviewed artifact manifest is required for a future checkpoint. The
[checkpoint artifact manifest](#checkpoint-artifact-manifest) below now writes
and verifies one on Frame.
See the [preflight and placeholder command](on-device.md#deployment-preflight-and-command-shape-not-authorized).
Target UAPI/ctypes/ioctl size evidence is still required before capture; passing
the cloud ABI test does not establish the headset ABI. Cross-compilation and Windows fixture outcomes do not establish Frame
runtime compatibility. No install, transfer or device execution is implied.

### Checkpoint artifact manifest

`scripts/checkpoint_manifest.py` is the command and
`framejacker/runtime/manifest.py` the library. `--write PATH` pins the git
commit, a clean-tree check, the Python, architecture and OS identity, and
SHA-256 values of the built bridge, every `framejacker/**/*.py`, the
checkpoint scripts, the bridge sources and the assets in
`scripts/smoke-assets.json`. It also pins the installed package versions, all
as deterministic JSON with checkout-relative paths. It creates a new file
(never overwriting) and refuses a dirty tree unless `--allow-dirty` records
`dirty: true`. `--verify PATH` is read-only: it writes nothing, builds nothing
and exits 1 on any difference or a dirty tree, printing the differences as
JSON. Both run on Frame after the native build; built binaries are never
transferred. The [device checkpoint](hud-qualification.md#next-device-checkpoint)
gives the commands, when to run them and what a failure means.

`CHECKPOINT_SCRIPTS` and `CHECKPOINT_PACKAGES` in the library are the single
lists. `tests/test_checkpoint_manifest.py` fails if a script named in a code
block of either checkpoint procedure is missing from the list, if a documented
`checkpoint_manifest.py` command does not parse with the real parser, if a
procedure runs the marker bridge before its first `--verify`, or if the
third-party packages in the checkpoint's import closure differ from the
pinned ones.
Its write/verify cases each build a temporary git checkout. They cover a
changed module, a rebuilt bridge, a modified tracked file, an untracked file,
another architecture, Python or package version, an existing output path
(including one created after the existence check), and a verify that leaves
every file time unchanged. A manifest with a non-finite or overflowing number,
or nested deeper than any manifest, is reported as unreadable, so the
verification report stays strict JSON. These are cloud checks of the tool, not
Frame evidence.

## Observer report to mapping candidate

[`hud_validation_report.py`](../scripts/hud_validation_report.py) has two
offline modes. `--check-candidate` checks a mapping candidate record against a
declared current context and time. `--from-observer` builds a candidate from a
saved `hud_marker_observer.py` report and a declared context, using
`framejacker/hud/candidate.py`. The candidate's session tokens come only from
the tokens the observer recorded (`--record-session-tokens`, off by default).
The declared context holds the other fields and must not carry a token. A
report without recorded tokens, or with a token that was not observed or that
changed, is rejected. The result is at best
`candidate_valid_unqualified` with exit 2; any rejection is a structured report
with exit 1 and writes nothing. Unknown terms are null with a reason, never 0,
and the observer's margin, already inside its union, is counted once. The
[qualification design](hud-qualification.md#observer-report-to-candidate-2026-11-01)
owns the rules. From the checkout:

```bash
python3 scripts/hud_validation_report.py --from-observer /tmp/observer-report.json \
  --declared-context /tmp/declared-context.json --lifetime-seconds 60 \
  --candidate-output /tmp/candidate.json
.venv-replay/bin/python -m unittest tests.test_hud_candidate tests.test_hud_qualification \
  tests.test_hud_marker_observer
```

The tests convert reports from the real observer on generated frames. They are
cloud results, not Frame evidence, and they open no device or gate.

## Synthetic HUD validation counts

A synthetic HUD run, `framejacker/runtime/live.run_pipeline` with a HUD stage,
is also a validation run. `framejacker/hud/validation.py` puts a
`ValidationSink` ahead of the result sink. It records each result in a
`ValidationLedger` built from `synthetic.fixture_candidate(calibration)` with
the run's own maximum age. The engine counts the stage's exclusion checks in
the `pipeline` block's `hud_checks`, which is null without a HUD stage:
frames checked, footprint pixels left non-zero after zeroing (before the
[neutral fill](#hud-mask-fill-evaluation)), and boxes dropped for touching the
mask or crossing an eye. The report adds
`validation_ledger` and `g5_software`, whose counts use the names in
`docs/templates/hud-device-validation-report.json`.

The runtime alone only drops such a box from presentation. In a validation
run, a footprint pixel left after zeroing, a dropped box or any ledger
rejection ends the run `validation_failed` with exit 1, and a rejected result
never reaches the result sink. A run that already failed on its own, for
example on a capture-context change, keeps its status and reason, and
`g5_software` names the validation failure. `MAX_DISPLAY_AGE_MS` in
`framejacker/hud/contract.py` is the one maximum age and default for the
pipeline, the ledger and both commands, and `Pipeline` refuses a HUD stage
with a longer one. The
[qualification design](hud-qualification.md#synthetic-validation-counts-and-one-maximum-age-2026-11-01)
owns the rules. From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_hud_validation tests.test_pipeline tests.test_live_pipeline
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --detector fixture --seconds 3
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --seconds 3
```

Both commands use generated input, with no overlay. The first uses the fixture
detector, which reports no detections and loads no model. The second uses the
default MobileNetSSD. Both end `completed` with exit 0 and
`g5_software.status` `checked_unqualified`, and so does the second with
`--validate-overlay`. With the zero-filled mask (881fea7) the second ended
`validation_failed` with exit 1, because the detector reported the black
footprint region itself as `tvmonitor`. The mask now refills that region from
its surround ([HUD mask fill evaluation](#hud-mask-fill-evaluation)), and a
validation run still fails on any box touching the mask. These are cloud
results, not Frame evidence; they observe no device or physical expiry and open
no gate.

## HUD mask fill evaluation

`HudMask` zeroes each masked region (footprint plus margin, per eye) at native
resolution and counts the footprint pixels still non-zero. Then
`hud.mask.fill_regions` refills each region with one neutral color: the rounded
mean of an 8-pixel ring outside it, in the same eye and outside every region.
Only after that does the resize run. The fill reads only the zeroed copy, so a
count of 0 still proves that no HUD pixel reaches the detector, and the strict
overlap and eye rule is unchanged.

`scripts/evaluate_mask_fill.py` (library `framejacker/mask_fill_evaluation.py`,
outside the runtime path) reproduces the comparison that chose this fill. It
compares six fills:

- `zero`, the baseline;
- `ring_mean`, the runtime fill;
- `nearest`, nearest-pixel replication;
- `inpaint_telea` and `inpaint_ns`, two `cv2.inpaint` modes;
- `surround_blur`, a blurred surround patch.

Each fill runs with every registered detector over two inputs: the synthetic
source's 127 odd gray levels with the fixture calibration (`--gray-levels all`
for all 255), and the seed stills with a mask at five positions. The JSON
report holds, per fill and detector:

- the gray levels and seed boxes that touch the mask, and the artifact-like
  boxes among them;
- changes to detections that touch no mask (lost, added, relabelled, and
  rescored beyond a stated 0.05 tolerance);
- seed pin counts per position, both of the detector's whole output and of the
  boxes the runtime keeps (`runtime_kept`), which shows what the strict rule
  costs next to the HUD;
- leak and determinism checks;
- time per frame at 96x64 and 1920x1080 on the runner CPU.

`--expect-clean-gray-sweep` exits 1 if any detector boxes the mask on the sweep
with the runtime fill. From the checkout:

```bash
.venv-replay/bin/python scripts/evaluate_mask_fill.py --fetch --expect-clean-gray-sweep \
  --report /tmp/mask-fill.json
.venv-replay/bin/python scripts/evaluate_mask_fill.py --no-seeds --gray-levels all --fill zero --fill ring_mean
.venv-replay/bin/python -m unittest tests.test_hud_fill tests.test_mask_fill_evaluation
```

The full run takes about 2.5 minutes on a 4-core x86_64 cloud runner. The
[qualification design](hud-qualification.md#neutral-mask-fill-2026-11-03)
records the results and the choice. They are runner-CPU results on generated
frames and public stills, not Frame evidence.

## Offline foundation for the minimum live POC

[`live_pipeline.py`](../scripts/live_pipeline.py) exercises generated input,
the shared CPU detector and a result sink. `Frame.owned_bgr` and the engine's
copy on publish own contiguous BGR copies; source identity/sequence cannot regress.
Only one frame waits for inference, and annotations remain associated with
their source. Inputs/results older than one second are dropped. Deadlines,
cancellation and errors close source/sink; a process supervisor terminates and
reaps hung workers without claiming clean shutdown after forced termination.

The only enabled source kind is `synthetic`. The overlay adapter only requests
`--validate-stdin`; it cannot initialize real VR output. Preserve these gates
until a source's ownership/publication/synchronization and feedback prerequisites
are met and the user authorizes the concrete live test. Do not disguise a real
source as synthetic to bypass the gate.

In an existing Python 3.12 replay/window development environment:

```bash
python -m unittest tests.test_recording_growth_check tests.test_live_pipeline tests.test_bounded_worker tests.test_overlay_cleanup tests.test_display_expiry -v
bash scripts/build_overlay_bridge.sh
python scripts/fetch_smoke_assets.py
python -m scripts.live_pipeline --synthetic-check --validate-overlay --seconds 3
```

These commands use generated images and native transport validation only.
The detector/decode dependencies use the repository's existing pins; no new
model or API credentials are required. The replay-window CI job on x86_64 and
ARM64 includes these critical offline suites in its explicit no-skip gate.
Local results and their limits are recorded in [E66](status.md#e66-offline-live-pipeline-and-headers-only-input-probe).

[`recording_growth_check.py`](../scripts/recording_growth_check.py) defaults to
no action. Its `--observe MP4` and `--watch-directory DIR` modes are explicit
metadata-only diagnostics; neither imports a decoder or reads payloads. Both
are bounded, identity checked and isolated in a supervised worker. The watcher
ignores existing clips and refuses ambiguous new selections. Positive output
is only evidence of repeated bounded fragment extents while the file grows,
not proof of a completed payload or approved live source.

The [recording operator procedure](on-device.md#proposed-recording-growth-test)
is preserved but paused; no new recorder experiment is planned. The active
work is the [edge HUD acceptance plan](live-capture.md#edge-hud-acceptance-gates). The vendor recorder's live publication behavior and the source's
exclusion of our panel remain unknown.

The [lifecycle audit](status.md#e67-offline-cancellation-and-cleanup-audit)
replaces the pipeline's blocking multiprocessing report receive and the probe's
plain `subprocess.run` with [a shared supervisor](../framejacker/runtime/guard.py).
It caps report bytes before parsing, keeps its deadline independent of report
I/O, handles catchable SIGINT/TERM/HUP without interrupting cleanup, and verifies
exit status against the final report. On Linux, a separate guardian now owns
the worker process group and child subreaping, including helpers left behind by
an exited leader. A private anonymous pipe (write end held only by the outer
supervisor, never inherited by workers) detects abrupt supervisor loss before
or during work. The guardian's own deadline and teardown do not depend on the
supervisor or detector staying alive. Signal handlers and subreaper state are
restored afterward. One supervisor must run on the main thread. Windows retains
single-process supervision and explicitly reports parent-death containment
unavailable; native-helper supervision requires Linux. Each command passes its
own module-level `validate_worker_report` (and options such as
`validation_only`); the guardian imports that validator by dotted module name
from this checkout's `scripts/` or `framejacker/` only, so it holds no table of
commands.

Two supervision policies remain, each with one implementation:
`framejacker/runtime/guard.py` (command workers in an owned process group under
the Linux guardian, used by the live pipeline, HUD marker/observer, recording
growth check and replay decode check) and `framejacker/runtime/process.py`
(spawned Python workers that stream progress, with parent-death signalling and
terminate/kill escalation, used by `sources.mirror.launch` for `mirror_check.py`
and `mirror_recognize.py`). The paused capture track's `capture_supervisor.py`
and its diagnostics keep their own copies while that track is frozen.

Signals follow one convention, in `guard.py`. A launcher runs inside
`sigterm_cancels()`, where SIGTERM raises `StopRequested` as Ctrl+C raises
`KeyboardInterrupt`, and `cancelled(report, error)` records either as
`cancelled` with exit 128 + signal. A guarded worker calls `stop_on_signals()`,
so SIGINT and SIGTERM set its stop event and its own cleanup runs. The live
pipeline, HUD marker check and observer and recording growth workers use it.
`shm_capture_check.py` keeps its own copy of the launcher side.

On cancellation/timeout/parent loss the guardian permits five seconds for cooperative
worker cleanup, then one second for group termination and one for kill/reaping,
plus 0.2 seconds for its report reader. This is additional to the normal worker
deadline (pipeline: 15 seconds startup + requested duration + five seconds
normal teardown; growth: selection wait + observation + five seconds overhead).
The outer supervisor allows a further eight seconds for guardian startup/teardown.
If the guardian itself is unresponsive, the outer supervisor gives it eight
seconds to stop, then one second for termination, one for kill/reaping and 0.2
seconds for report-reader shutdown. Reports distinguish worker and guardian
reaping/forced termination, and overall supervision time from inner worker time.
Forced termination never establishes clean API/resource shutdown. A reaped
worker with a still-running acquisition thread also cannot claim source cleanup.
An unkillable process is reported unconfirmed; these software budgets are not
guarantees about kernel scheduling or blocked kernel I/O.

The new failure fixtures use local subprocesses and handshake files. They
exercise real parent-only/repeated signals, partial and oversized JSON,
contradictory exit status, orphaned helper pipes, a hanging detector/source,
partial sink startup, hide/close errors and late file scans. The orphan test
also checks that an unrelated child is untouched. A mocked KeyboardInterrupt
alone no longer serves as evidence of process reaping.

The [E68 tests](status.md#e68-independent-expiry-and-supervisor-loss-containment)
add actual SIGKILL of the outer supervisor, a closed lifeline before startup,
and a deliberately stopped/resumed guardian. They verify owned-worker/helper
reaping while preserving the lack of a final supervisor cleanup report. The
guardian also writes its worker's process group to a private pipe. If the
guardian alone is killed, the outer supervisor (its subreaper) sends that group
SIGTERM, then SIGKILL, and reaps it. The report stays `guardian_failed` with
`cleanup_ok: false`. A guardian-SIGKILL test covers this path
([lifecycle review](lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01)).
If both supervisor and guardian are killed, the guardian cannot run, or a helper
detaches from its group, this scheme cannot guarantee cleanup. No test forces
real uninterruptible kernel I/O.

Synthetic display frames now carry absolute monotonic expiry to the independent
native validation process. Stalled inference, settings heartbeats and partial
packet reads cannot renew it. Tests inspect validation events/software state,
not a headset. Opcode 4 remains rejected for real output; actual Frame expiry,
launcher behavior and API teardown still need separately authorized validation.
See the [source/output contract review](live-capture.md#synthetic-input-and-feedback-contract-review).

## Native VR panel build and validation

`bash scripts/build_overlay_bridge.sh` builds the native tool into ignored
`artifacts/overlay-bridge/`, using the pinned SDK in `.cache/openvr`. The camera
and overlay builders share `scripts/build_openvr_tool.sh`; neither builder runs
a runtime test. The camera binary's explicit opt-in guard remains in place.

The overlay job checks that the generated contract header is current, strict
C++ syntax, packet bounds, malformed input, idle payload timeouts, signals and
named missing-runtime diagnostics. The replay
window job also builds the bridge so its validation-only mode can check actual
RGBA forwarding and Qt control creation. This mode reports no OpenVR
initialization and zero submitted frames. For the real hardware procedure,
read [VR replay panel](vr-overlay.md).

The bridge's modes are `--test-panel`, `--stdin`, `--validate-stdin`, the
three calibration-marker modes, and two opt-in tracking reporter modes:
`--stdin --report-tracking` and `--report-tracking-only --seconds 1..600`
(no overlay). The reporter's logic is in the OpenVR-free
`tools/tracking_report.hpp`. `tests/test_overlay_bridge.py` compiles two
fake-dispatch fixtures with strict warnings and no OpenVR include path.
`tests/fixtures/overlay_dispatch_fixture.cpp` covers packet decoding,
deadlines and teardown order. `tests/fixtures/tracking_report_fixture.cpp`
covers SHA-256 vectors, the zero-pose digest, the change mapping and the
unavailable reasons from a fake system, and prints its lines for the test to
feed to a real `TrackingObserver`. It also drives the bridge's tracking glue
from the same header: the option decision, the reporter's start and event
hook, the runtime event pass and the reporter-only loop, with a fake runtime
queue, clock and stdin. A source check pins that the bridge calls those
helpers once and nowhere bypasses them. The same module checks that the built bridge
refuses the tracking options outside their modes before any OpenVR call, and it
drives `OverlayClient(tracking=...)` and `TrackingReporter` with a fake bridge
executable. No test runs a tracking mode against a runtime. See [native
tracking reporter](hud-qualification.md#session-tokens-for-the-mirror-source-2026-11-03).

### Generated HUD contract header

[`tools/hud_contract.hpp`](../tools/hud_contract.hpp) is committed and
generated from the Python contract by
[`framejacker/hud/native.py`](../framejacker/hud/native.py). It holds
`HudConfig` and its digest, the calibration `MARKER` (sizes, positions, gray
levels and corner ids), the pipe packet layouts and opcodes, the marker
duration (1–20 s), display age (1 s), frame side (512) and tracking-reporter
lifetime (1–600 s) limits, `PANEL_LIMITS` with the `PanelSettings` defaults,
and the tracking reporter's event and field names and change table
(`TRACKING_CHANGE_EVENTS`: change name, OpenVR event name and id).
`tools/overlay_bridge.cpp`, `tools/overlay_frame.hpp` and
`tools/tracking_report.hpp` include it and keep no copies of those values;
the first two share one little-endian `read_u32`. The bridge, which includes
`openvr.h`, `static_assert`s each generated change id against the `vr::`
enumerator of the name Python gives it. Python call sites import the same
constants from `framejacker/hud/contract.py` (`PANEL_LIMITS` and
`PanelSettings` stay in `framejacker/overlay.py`). Change a value only on the
Python side, then:

```bash
python3 scripts/hud_contract_header.py --write
bash scripts/build_overlay_bridge.sh
python3 scripts/hud_contract_header.py --check   # the overlay CI job runs this
```

`tests/test_hud_contract.py` fails when the committed header differs from the
generated text, when a Python contract value would not reach the header, when
the built bridge reports a different contract digest (rebuild it), and when the
rebuilt bridge or a Python call site does not apply a limit at its boundary.
Each Python marker-duration site must reject with its own reason, and
`tests/fixtures/overlay_dispatch_fixture.cpp` accepts a deadline exactly at the
generated display-age cap and rejects one a nanosecond later.

`framejacker-overlay-bridge --render-calibration-marker NEW_RGBA_FILE` draws
the native marker into a new file (exclusive creation) and reports the contract
and HUD configuration digests. It makes no OpenVR call. The tests require its
RGBA bytes to equal `framejacker.hud.observation.marker_template()` pixel for
pixel, with every pixel opaque. They also replay V4's shape offline: two clear
baseline reads, then the native marker on read 3, which must decode all four
corner tags with zero tag and bit error. The compared marker function is
unchanged from the V4 native source (SHA-256 `5ffc47f5…`, the
`tools/overlay_bridge.cpp` committed in `bfc7aa7`). A passing run therefore
rules out a disagreement between the native generator and the observer
template, so this comparison offers no candidate explanation for V4's frame-3
tag/bit rejection, which remains unexplained. It does not exercise compositor
scaling, color or gamma transforms, mirror capture or timing. These are
generated, validation-only checks, not Frame evidence.

## Generated evidence summaries

Detailed records live under [evidence](evidence/README.md); the coordinator
allocates IDs and updates `docs/evidence/index.json`. Reviewed capability rows
in `docs/status-data.json` generate the status matrix and the marked architecture
progress summary. Preserve all historical evidence headings and E63–E71's
reconstructed uncertainty. From the checkout:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
python3 -m unittest tests.test_generate_status
```

The generator checks identities, provenance, missing history and evidence
references before writing. The root README keeps short entry points; its older
narrative and links remain in [README history](project-history.md). The
[qualification design](hud-qualification.md) owns the proposed G4 method and
separately scoped device-validation evidence path. These offline documents and
schemas do not qualify a mapping or open production output.
