# Design review: architecture and implementation

Dated snapshot, 2026-10-25, of revision `ee87e50`. It reviews the documented
architecture against the code, the CI and the documentation, and proposes a
prioritized set of improvements. Like the [capture review](capture-reference-review.md),
it records reasoning at a point in time; [status](status.md) still owns
evidence and [live capture](live-capture.md#current-work-order) owns capture
tasks. Progress on the work order below is noted at the end.

## Summary

The engineering is careful and safety-minded: ownership of resources is
explicit, loops and buffers are bounded, outputs use exclusive creation,
subprocesses are supervised with parent-death signals, the Vulkan work is
tested with fake dispatch, and claims are kept honest. Keep that.

The main problems are structural:

1. The architecture described in [architecture](architecture.md) did not exist
   as code.
2. Effort is concentrated on a capture path that is currently blocked.
3. Documentation has grown faster than the product.

## Findings

### 1. The documented pipeline is not in the code

The architecture page describes capture adapter → frame metadata → bounded
latest-frame queue → detector → presentation sinks. At this revision there were
two independent hand-built pipelines: `replay_window.py` (decode, inference, Qt
and overlay inside a 250-line `main()` with nested classes) and
`mirror_recognize.py` (its own latest-frame holder, loop, reporting and worker).

Suggestion: make the diagram real.

```python
@dataclass(frozen=True)
class Frame:            # the "Frame metadata" table from architecture.md
    pixels: ...; pixel_format: str; width: int; height: int; stride: int
    source_id: str; sequence: int; observed_ns: int; media_time: float | None

class FrameSource(Protocol):   # replay file, SteamVR mirror, later shared memory
    def run(self, emit: Callable[[Frame], None], stop) -> None: ...
class Detector(Protocol):      # MobileNet-SSD now, ONNX detectors later
    def detect(self, bgr, threshold) -> DetectionResult: ...
class Sink(Protocol):          # latest result for Qt, overlay, reports, previews
    def submit(self, frame: Frame, result: DetectionResult) -> None: ...
```

A single `Pipeline(source, detector, sinks)` runner should replace both
pipelines. Continuous live input into the existing display then becomes
composition rather than a third pipeline.

### 2. Effort is concentrated on a blocked path

The capture-provider track has six stacked patches (about 2,000 lines) against
a pinned upstream commit, six CI build variants, a systemd drop-in trial runner
with 854 lines of tests and 65 lifecycle tests. Its own documentation says live
reads are blocked because the producer has no synchronized handoff, and the
activation trial is paused. Meanwhile the application has one 20-class
detector, no live display and no accuracy evaluation.

Suggestions: freeze the provider track until a vendor interface or a
synchronization answer exists. Collapse the patch stack into a maintained fork
or a single squashed patch, and remove historical build modes
(`--transport-fixes`, `--publication-fixes` and so on) from CI, keeping the
current one. Spend effort on unblocked work: detector quality, real-time replay
and live mirror input into the Qt window. Whether the desktop window appears in
the mirror like the VR panel does is a cheap device test worth running.

### 3. Detector mixes inference and drawing; the model is a dead end

- `DemoDetector.detect()` drew boxes as well as returning them. Separate
  `detect()` (plain data) from `annotate()`. Vector overlay labels and future
  world-space labels need detections without baked-in pixels.
- The 300 × 300 resize stretches 16:9 input; use letterbox padding and invert
  it when mapping boxes back.
- MobileNet-SSD depends on OpenCV's Caffe importer, already pinned as tied to
  OpenCV 4.12. ONNX Runtime is pinned in the development environment but
  unused. Add an ONNX detector (YOLOX-Nano or similar, with NMS) behind the
  detector interface as the second implementation.
- `smoke_check.py` reimplements loading and preprocessing instead of using the
  shared detector, so the smoke check does not test the application's detector.
- `inference_ms` measures only `forward()`; preprocessing, drawing and
  conversion are excluded, so end-to-end latency is under-reported.

### 4. Replay contradicts the latest-frame design

The replay worker runs inference on every decoded frame, capped at 10 fps, so a
30 fps recording plays in slow motion. [E47](status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame)
is consistent with this (576 frames and about 95 one-second heartbeats suggest
roughly 6 fps), though no frame rate was recorded. Pace playback by media time,
skip frames the way the live path drops stale input, and report duration and
processed fps.

### 5. Duplicated infrastructure

- The one-pending-item "latest" holder existed three times: the replay worker,
  the mirror recognizer's `LatestFrame` and the overlay client.
- Bounded subprocess supervision exists at least three times:
  `mirror_check.run_bounded`, `capture_supervisor.supervise` and
  `OverlayClient.close`. Two Python workers call `libc.prctl(1, …)` with a
  magic number.
- The temporary-file and hard-link preview publication was copied between
  `mirror_check` and `mirror_recognize` (both `main()` and the workers).
- Reports are free-form dictionaries built per script; typed report objects
  and one exclusive-create writer would remove repetition.
- Panel limits are defined three times (the Python `PanelSettings`, the Qt spin
  boxes and the C++ bridge); the 960-pixel maximum side and 0.5 threshold are
  repeated. Keep one source or add a consistency test.

### 6. Packaging and tooling

About 35 flat scripts used `if __package__` import shims; there was no
`pyproject.toml`, and three virtual environments are built with two installers
(uv for development, pip for replay). Suggestion: a
`src/framejacker/` package, one `pyproject.toml` with optional extras and
console entry points, and a lock covering x86_64 and aarch64. There was no
linting: add ruff, mypy on the core interfaces, shellcheck and clang-format or
clang-tidy for `tools/`.

### 7. CI can pass vacuously and does not cover ARM64

Test classes skip when prerequisites are missing, and the same `tests/`
directory runs in three jobs with different environments, so a broken build
step can become a green run of skips. Fail CI on unexpected skips.

The target is ARM64, but CI had no ARM64 runner. GitHub's `ubuntu-24.04-arm`
runners can execute the synthetic suites that are currently repeated by hand on
the headset and recorded as evidence (for example E23, E25, E27, E31, E35 and
E37), saving device time and documentation.

### 8. VR overlay

- `SetOverlayRaw` copies each frame through the CPU and suits small or rarely
  updated images. It is adequate for 512-pixel replay; plan on
  `SetOverlayTexture` with a Vulkan image for continuous live output.
- The panel is head-locked only; add a world-locked option with recentering.
- Draw labels and boxes as a separate layer so the panel can be transparent
  and labels can later be positioned spatially.
- `Json::FastWriter` is deprecated in jsoncpp; use `StreamWriterBuilder`.

### 9. Documentation weight

`status.md` is 1,638 lines and `on-device.md` 1,436; the README's design section
is one 60-line paragraph of evidence, and most commits are "Record …". Cut the
README to one screen, store each evidence entry (or its raw JSON report) as its
own file and generate the status matrix, and state each caveat once.

## Smaller issues

- `verify_model()` passed silently if the manifest contained no
  `MobileNetSSD_*` entries.
- `run_bounded(candidate, …)` receives a job dictionary from the mirror
  recognizer, so `report["candidate"]` briefly holds the whole job.
- The default recording directory is hard-coded in `main()`.

## Proposed work order

1. Packaging: `pyproject.toml`, `src/` layout, ruff and ARM64 CI.
2. Extract `Frame`, `FrameSource`, `Detector`, `Sink`, the latest-item holder
   and supervision helpers; rebuild replay and mirror recognition on one
   `Pipeline`.
3. Separate detection from annotation, add letterboxing and an ONNX detector,
   and build a small labelled evaluation set including the rug/sofa case.
4. Fix replay pacing; record end-to-end latency and fps.
5. Try live mirror input in the Qt window. Keep the provider track frozen and
   time-boxed, collapse its patches and trim CI to the current mode.
6. Slim the documentation.

## Progress

- Item 1, partly done: [`pyproject.toml`](../pyproject.toml) with extras and
  ruff configuration, a `framejacker/` library package beside the `scripts/`
  commands, a ruff CI job and an [ARM64 workflow](../.github/workflows/arm64.yml)
  sharing [one job definition](../.github/workflows/check-jobs.yml) with x86_64.
  The package sits at the repository root rather than under `src/` so that
  headset commands keep working from a plain checkout. A multi-architecture
  lock file, console entry points and failing CI on unexpected skips remain.
- Item 2, done for replay and mirror recognition: `Frame`, `FrameSource`,
  `Detector`, `Sink`, `LatestSlot` and `Pipeline`, plus shared worker
  supervision and output publication. `smoke_check.py` uses the shared
  detector, and detection is separate from annotation. The V4L2 mirror module,
  the overlay client's own pending-frame holder and the capture supervisor's
  process handling are not yet consolidated.
- Item 3, implemented in the cloud: YOLOX-Nano (ONNX Runtime, OpenCV DNN
  fallback) behind the `Detector` interface with letterbox input; `--detector`
  in replay and mirror recognition; `--still` captures unannotated evaluation
  inputs; `scripts/evaluate_detectors.py` with a seed manifest and a format for
  private Frame stills. Correction to finding 3: MobileNet-SSD keeps its
  stretched 300 × 300 input, because SSD models are trained that way;
  letterboxing applies to the YOLOX family. The representative evaluation set
  (including the rug) must be captured on Frame.
- Cloud evidence: [E49](status.md#e49-shared-pipeline-and-package-refactor-cloud-checks),
  [E50](status.md#e50-candidate-detector-and-evaluation-harness-cloud-checks).

## Next steps

State at handoff (2026-10-25): items 1–3 are on branch
`claude/peaceful-fermi-8m3bu6` in [pull request #1](https://github.com/moral-kombat/framejacker/pull/1),
head `ce733dd`, with all x86_64 and ARM64 CI green and no review threads. The
PR description predates item 3 and the CI fixes; update it before merging.

### Needs the headset

1. Re-run `bash scripts/setup_window.sh` on Frame (adds ONNX Runtime and the
   YOLOX model).
2. Confirm the refactor on device ([E49](status.md#e49-shared-pipeline-and-package-refactor-cloud-checks)
   is cloud-only): replay window with `--overlay`, then bounded mirror
   recognition with the default detector, as in E47/E48.
3. Run the [detector comparison](on-device.md#compare-detectors-on-frame-stills):
   capture stills with `--still` (including the rug), describe them in
   `evaluation/local/frame.json`, run `evaluate_detectors.py`, and record the
   result and Frame timing as a new status entry. Choose the default detector
   from that, not from the outdoor seed set.

### Engineering, in order

4. **Replay pacing (item 4).** Pace `ReplaySource` by media time (`pts`) and
   drop frames instead of processing every frame; run replay in the pipeline's
   `latest_only` mode. Report duration, processed fps and end-to-end latency
   (`Frame.observed_ns` to presentation).
5. **Live input in the Qt window (item 5).** First test on Frame whether the
   desktop window appears in the SteamVR mirror, as the VR panel did (E18). If
   not, feed `MirrorSource` continuously into the replay window's sinks.
   Keep the capture-provider track frozen: collapse `patches/mrhand/` into one
   patch or a fork, and drop historical `build_capture_reference.sh` modes from CI.
6. **Rug coverage.** Neither model has a rug class. If rugs matter, evaluate an
   open-vocabulary model offline or fine-tune a small detector on labelled
   Frame stills; keep both baselines in the evaluation.
7. **Documentation weight (item 6).** One-screen README; per-entry evidence
   files or raw JSON reports instead of the 1,600-line status page.

### Remaining from items 1–3

- Lock file covering x86_64 and aarch64; console entry points.
- Fail CI on unexpected test skips (fixture tests skip silently when a build
  step is missing).
- CI runs twice per PR push (`push` and `pull_request`); limit `push` to `main`.
- Move the V4L2 mirror module and acquisition loop (`scripts/mirror_capture.py`,
  `scripts/mirror_check.py`) into `framejacker/sources/`; reuse `LatestSlot` in
  the overlay client; consolidate the capture supervisor's process handling.
- One source of truth for VR panel limits (Python, Qt and C++ copies).
