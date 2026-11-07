# Design review: architecture and implementation

This review began on **2026-10-25**, against revision `833c556`. It explains
why the project extracted a shared pipeline, improved testing and reduced the
emphasis on dedicated camera capture. It is a historical review.

Read the [current work plan](live-capture.md#current-work-order) for next actions,
[architecture](architecture.md) for the implementation, and [status](status.md)
for verified results. The [complete review and its later progress notes](history/2026-11-07-design-review.md)
are preserved. The section links below keep earlier references usable.

## Summary

The review found duplicated processing paths, effort concentrated on a blocked
capture provider, and documentation that mixed design with a growing test log.
It recommended a shared pipeline and clearer evidence and operating guides.

## Findings

The following headings retain the review's original wording. They describe
findings at the reviewed revision, not a list of defects in today's checkout.

### 1. The documented pipeline is not in the code

The review proposed the shared `Frame`, `FrameSource`, detector and sink
interfaces. See the [original finding](history/2026-11-07-design-review.md#1-the-documented-pipeline-is-not-in-the-code)
and [current frame flow](architecture.md#frame-processing).

### 2. Effort is concentrated on a blocked path

The [original finding](history/2026-11-07-design-review.md#2-effort-is-concentrated-on-a-blocked-path)
recommended pausing dedicated capture. That track remains deferred; current
exceptions and priorities belong in the [work plan](live-capture.md).

### 3. Detector mixes inference and drawing; the model is a dead end

The [review](history/2026-11-07-design-review.md#3-detector-mixes-inference-and-drawing-the-model-is-a-dead-end)
proposed separate detection and drawing, and a second detector. Its later
correction matters: MobileNet-SSD retains stretched input; YOLOX uses letterboxing.
See the [current detector guide](architecture.md#recognition-implementation-and-model-replacement).

### 4. Replay contradicts the latest-frame design

The [pacing review](history/2026-11-07-design-review.md#4-replay-contradicts-the-latest-frame-design)
led to media-time playback and frame dropping. [Status](status.md) distinguishes
offline measurements from headset measurements.

### 5. Duplicated infrastructure

The [original inventory](history/2026-11-07-design-review.md#5-duplicated-infrastructure)
identified repeated latest-frame storage, process supervision and output handling.
Use the current code and [development guide](development.md) when changing them.

### 6. Packaging and tooling

The [original proposal](history/2026-11-07-design-review.md#6-packaging-and-tooling)
predates the package, Ruff configuration and shared CI jobs. The package lives
at the repository root so scripts can run from a checkout.

### 7. CI can pass vacuously and does not cover ARM64

The [review](history/2026-11-07-design-review.md#7-ci-can-pass-vacuously-and-does-not-cover-arm64)
predates ARM64 CI and the runner that rejects skipped required tests.
Current checks are documented in [development](development.md).

### 8. VR overlay

The [overlay review](history/2026-11-07-design-review.md#8-vr-overlay)
contains historical suggestions. World-locked labels remain deferred, and an
upload-path change needs profiling evidence; use the [overlay guide](vr-overlay.md)
for supported behavior.

### 9. Documentation weight

The [review](history/2026-11-07-design-review.md#9-documentation-weight)
recommended a short overview and separate evidence records. The
[documentation maintenance plan](documentation-plan.md) owns the present structure.

## Smaller issues

The [original list](history/2026-11-07-design-review.md#smaller-issues) records
revision-specific findings. It is not an active backlog.

## Proposed work order

The [historical order](history/2026-11-07-design-review.md#proposed-work-order)
is superseded by the [current work plan](live-capture.md#current-work-order).

## Progress

The [preserved progress notes](history/2026-11-07-design-review.md#progress)
record subsequent changes and corrections. Use [reviewed status](status.md)
for current capability claims; the old notes are not maintained as a second tracker.

## Next steps

Start with the [current work plan](live-capture.md#current-work-order).
This heading remains for older links and agent handoffs.

### Needs the headset

Device prerequisites and acceptance gates live in the [current work plan](live-capture.md#edge-hud-acceptance-gates).
Procedures live in the [on-device guide](on-device.md).

### Engineering, in order

Use the [current work order](live-capture.md#current-work-order); do not restart
completed work from this review's historical list.

### Remaining from items 1–3

The [historical follow-ups](history/2026-11-07-design-review.md#remaining-from-items-13)
are retained for context. Only items selected in the current plan are active work.
