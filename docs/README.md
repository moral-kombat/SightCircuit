# Documentation

New to SightCircuit? Read the [project overview](../README.md), then choose the
development or Steam Frame path below. [Status](status.md) explains what has
been verified; [current work and acceptance](live-capture.md) owns next steps.

<a id="start-by-task"></a>

## Start here

- [What works and what remains open](status.md) — capabilities with supporting evidence.
- [Architecture](architecture.md) — the processing paths and how components fit together.
- [POC scope](poc-scope.md) — the intended proof of concept and accepted decisions.
- [Glossary](glossary.md) — project terms, gate numbers, and evidence IDs.

## Develop

1. [Set up Linux development](development.md) and run the first smoke check.
2. [Choose current work](live-capture.md#current-work-order), or use the
   [session handoff](execution-prompt.md) to resume a task.
3. [Contribute a change](../CONTRIBUTING.md), including checks and documentation updates.

## Use Steam Frame

Begin with [device access and inventory](on-device.md). A browser on Frame can
still display a cloud terminal: confirm where commands run before proceeding.

| Task | Guide |
| --- | --- |
| Inspect or replay a completed recording | [Recorded replay](guides/replay.md) |
| Show a replay in the headset | [VR panel](vr-overlay.md) |
| Run bounded recognition and compare detectors | [Mirror recognition and evaluation](guides/mirror-recognition.md) |
| Run a selected test session and record observations | [Frame sessions](guides/frame-sessions.md) |
| Prepare the separately approved mapping observation | [HUD checkpoint](guides/hud-checkpoint.md) |
| Measure the fallback's device terms with the calibration marker (`fallback_measure`, approval needed) | [Fallback measurement](guides/fallback-measurement.md) |
| Test the agent guard's enforcement on the Frame (Phase 2, approval needed) | [Agent guard enforcement test](guides/agent-enforcement-test.md) |

The checkpoint is experimental. It does not enable the production live HUD.
Each device guide describes prerequisites, operating limits, and shutdown.

## Technical reference

- [Development reference map](development.md) — pipeline, detector evaluation,
  builds, tests, and paused provider tools.
- [HUD qualification](hud-qualification.md) — mapping requirements, evidence,
  and the limits of offline validation.
- [HUD fallback input](reference/hud-fallback.md) — the time-multiplexed
  exclusion design, costed and not built, for when the marker-observation
  budget is exhausted.
- [Camera interfaces and research](steam-frame-camera.md) — mirror input,
  dedicated capture constraints, and pinned upstream sources.
- [Camera helper](reference/camera-helper.md) — the containment for the
  Arcturus camera SDK: one SDK translation unit, a fake backend for CI, an
  operator-only build against the Client Library; cloud work, no device steps.
- [Agent-on-Frame design](agent-on-frame.md) and
  [third-party attribution](../THIRD_PARTY.md).

## Project history

- [Evidence catalog](evidence/README.md) — numbered reports and their provenance.
- [Historical reviews and handoffs](history/README.md) — dated context, not current instructions.
- [Previous README](project-history.md) — preserved project background.
- [Documentation ownership and maintenance](documentation-plan.md).

## Reading order for a new session

For development: **overview → status → development setup → current work → contributing**.
For headset use: **status → device access → one task guide**.
Read a historical report only when you need the reasoning or evidence it preserves.
Planned capabilities and upstream claims are not verified SightCircuit behavior.
