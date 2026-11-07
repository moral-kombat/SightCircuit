> **Historical snapshot.** Preserved during the documentation reorganization on
> 2026-11-07. The original dates and later updates below are retained; statements
> about current state, next work, commands and approvals describe their original
> context. Use the [current work plan](../live-capture.md#current-work-order),
> [reviewed status](../status.md) and [documentation index](../README.md) today.

# Design review: architecture and implementation

Dated snapshot, 2026-10-25, of revision `833c556`. It reviews the documented
architecture against the code, the CI and the documentation, and proposes a
prioritized set of improvements. Like the [capture review](../capture-reference-review.md),
it records reasoning at a point in time; [status](../status.md) still owns
evidence and [live capture](../live-capture.md#current-work-order) owns capture
tasks. Progress on the work order below is noted at the end.

## Summary

The engineering is careful and safety-minded: ownership of resources is
explicit, loops and buffers are bounded, outputs use exclusive creation,
subprocesses are supervised with parent-death signals, the Vulkan work is
tested with fake dispatch, and claims are kept honest. Keep that.

The main problems are structural:

1. The architecture described in [architecture](../architecture.md) did not exist
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
30 fps recording plays in slow motion. [E47](../status.md#e47-recorded-recognition-and-vr-panel-poc-on-frame)
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
`src/sightcircuit/` package, one `pyproject.toml` with optional extras and
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

- Item 1, partly done: [`pyproject.toml`](../../pyproject.toml) with extras and
  ruff configuration, a `sightcircuit/` library package beside the `scripts/`
  commands, a ruff CI job and an [ARM64 workflow](../../.github/workflows/arm64.yml)
  sharing [one job definition](../../.github/workflows/check-jobs.yml) with x86_64.
  The package sits at the repository root rather than under `src/` so that
  headset commands keep working from a plain checkout. A multi-architecture
  lock file, console entry points and failing CI on unexpected skips remain.
- Item 2, done for replay and mirror recognition: `Frame`, `FrameSource`,
  `Detector`, `Sink`, `LatestSlot` and `Pipeline`, plus shared worker
  supervision and output publication. `smoke_check.py` uses the shared
  detector, and detection is separate from annotation. The V4L2 mirror module,
  the overlay client's own pending-frame holder and the capture supervisor's
  process handling are not yet consolidated. `sightcircuit/sources/mirror.py`
  currently adapts callback-based acquisition to `Frame`; the V4L2 read loop
  remains in the scripts.
- Item 3, implemented in the cloud: YOLOX-Nano (ONNX Runtime, OpenCV DNN
  fallback) behind the `Detector` interface with letterbox input; `--detector`
  in replay and mirror recognition; `--still` captures unannotated evaluation
  inputs; `scripts/evaluate_detectors.py` with a seed manifest and a format for
  private Frame stills. The 2026-10-30 consolidation (`9b765ee`) dropped both
  mirror flags; they were restored on 2026-11-01, with the still saved at native
  size and its SHA-256 pinned in the manifest entry
  ([bounded mirror recognition](../development.md#bounded-mirror-recognition)).
  Correction to finding 3: MobileNet-SSD keeps its
  stretched 300 × 300 input, because SSD models are trained that way;
  letterboxing applies to the YOLOX family. The representative evaluation set
  (including the rug) must be captured on Frame.
- Cloud evidence: [E49](../status.md#e49-shared-pipeline-and-package-refactor-cloud-checks),
  [E50](../status.md#e50-candidate-detector-and-evaluation-harness-cloud-checks).

- Replay pacing (item 4): the first `--realtime` implementation dropped frames
  after decoding them and resynced after one-second stalls; Frame measurements
  in [E51](../status.md#e51-media-time-replay-pacing-unit-tested-only) and
  [E53](../status.md#e53-unattended-replay-regression-and-pacing-rerun) show it
  decode-bound at 0.969 processed fps. It now decodes on its own thread with
  FFmpeg frame threading, runs inference on the newest frame only, and skips
  undecoded packets to the next keyframe when more than 0.5 s behind. On a
  synthetic stand-in that reproduces the old Frame result (0.977 fps), it
  reaches a 0.94 real-time ratio at 4.7 processed fps
  ([E62](../status.md#e62-keyframe-catch-up-realtime-replay-cloud-checks)).
  Frame measurement of the new version is pending.
- Branch CI runs through pull requests (`push` is limited to `main`). All
  checks passed on code commit `7e2b67c`; the later documentation-only commit
  `93b4d50` did not trigger checks.

## Next steps

State (2026-10-26): pull requests #1 to #6 are merged into `main`, and the
keyframe catch-up rework of realtime replay was merged from
`claude/peaceful-fermi-8m3bu6` ([E62](../status.md#e62-keyframe-catch-up-realtime-replay-cloud-checks));
see E52/E53 for device-command evidence and limitations.

The POC scope and work packages are in [POC scope](../poc-scope.md), which
supersedes the engineering order below where they differ: the target is a
fixed edge HUD of object names and direction arrows over live mirror input,
and world-locked labels, the provider track, stereo depth and splats are
deferred.

### Needs the headset

These are remaining acceptance inputs, not new device authorization. The
[October 26 handoff](../live-capture.md#october-26-evening-handoff) supersedes the
old command order: obtain approved access to the existing E61 still and its
verifiable digest before diagnosing the miss. A representative factual Frame
evaluation set is still needed before choosing a detector default. Do not
recapture merely because a prior checklist mentioned `--still`.

The next device checkpoint is the bounded marker/observer calibration only
after lifecycle prerequisites and a pinned artifact manifest are ready; see
[the proposed qualification method](../hud-qualification.md) and
[device checkpoint](../on-device.md#bounded-edge-hud-calibration-checkpoint).
Headset feedback exclusion, physical expiry and the 60-second combined live
HUD acceptance remain separate later checks. Replay measurements are optional
follow-up work and do not unblock those gates.

### Engineering, in order

Follow [active WPs](../poc-scope.md#work-packages) and the
[execution prompt](../execution-prompt.md): complete the shared runtime and
lifecycle safeguards, extend the existing evaluator and preprocessing audit,
and prepare mapping qualification and validation reports offline. Keep the
provider frozen; patch-stack consolidation, a new capture route, world-locked
labels and alternate model integrations are outside this pass. Texture-upload
changes need profiling evidence.

WP7 now has a concise README, individual historical evidence records and a
deterministic status/architecture summary generator. The former README
narrative is preserved in [README history](../project-history.md); the migration
supplies no new headset evidence.

### Remaining from items 1–3

- A lock file covering x86_64 and aarch64, console entry points and deeper
  acquisition-module consolidation remain optional engineering follow-ups.
- The critical CI suites use `scripts/run_tests_no_skips.py`; capture-fixture
  jobs build their applicable native mode before running it. A skipped
  required fixture is not successful validation.
- Branch checks run through PRs (`push` is limited to `main`). CI on ARM64
  checks that architecture; it is not a Frame headset result.
- `PANEL_LIMITS` remains the Python source of panel limits; the bridge takes
  them from the [generated contract header](../development.md#generated-hud-contract-header),
  and tests fail when the two differ. Keep native acquisition limits separate from detector
  resize limits and preserve native masking before resize.
