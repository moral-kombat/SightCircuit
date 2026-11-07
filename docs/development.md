# Development setup

Set up a Linux development checkout, run a smoke check, then run the checks
needed for your change. No headset is required. Start with [architecture](architecture.md)
for the component overview or [the current plan](live-capture.md) for priorities.

## Prerequisites

Use Python 3.12 and a Linux development host. The smoke environment targets
x86_64; CI also runs replay/native checks on ARM64. The headset has additional
runtime requirements described in [device access](on-device.md).

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) for the
smoke environment. On Debian/Ubuntu development hosts, Qt needs these runtime
libraries even when tests run offscreen:

```bash
sudo apt-get install -y --no-install-recommends libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3
```

This package command is for the development host. Follow the device guide for
Frame; do not change SteamOS system protections as a setup shortcut. Native
fixtures also need a C++17 compiler; the paused capture-reference builds link
`libvulkan.so.1`.

First setup downloads pinned Python packages and checksum-verified fixtures
from PyPI, GitHub releases, and `raw.githubusercontent.com`. No API key is needed.
Keep failed checksum results for investigation instead of changing the expected hash.

## Choose an environment

| Environment | Setup | Use |
| --- | --- | --- |
| `.venv` | `bash scripts/setup.sh` | uv-managed smoke/development environment; synchronization removes unlisted packages |
| `.venv-replay` | `bash scripts/setup_replay.sh` | Minimal completed-file decoder |
| `.venv-replay` | `SIGHTCIRCUIT_PYTHON=python3.12 bash scripts/setup_window.sh` | Extends replay with Qt, detectors, and model fixtures; recommended for the full Python suite |

Use an available Python 3.12 path for `SIGHTCIRCUIT_PYTHON` if its executable has
another name. The replay scripts otherwise use `python3`. Virtual environments
are local to their machine and architecture; do not copy one onto Frame.

## Install and run

From the checkout root:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

A passing smoke check decodes generated input, runs the pinned detectors,
renders Qt offscreen, and writes `artifacts/smoke/readiness.json`. It does not
open a camera or initialize SteamVR. See [check details and artifacts](reference/development-checks.md#what-validation-demonstrates).

## Run the development checks

For Python and native-overlay work, prepare the replay environment and native
helpers, then run the suite:

```bash
SIGHTCIRCUIT_PYTHON=python3.12 bash scripts/setup_window.sh
bash scripts/build_overlay_bridge.sh
bash scripts/build_shm_reader.sh
bash scripts/build_capture_supervisor.sh
bash scripts/build_capture_gpu_check.sh
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest discover -s tests
uvx ruff@0.14.2 check .
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch --expect evaluation/seed-expected.json
python3 scripts/generate_status.py --check
python3 scripts/check_docs.py
python3 scripts/hud_contract_header.py --check
git diff --check
```

Some capture-fixture tests skip unless their corresponding
`build_capture_reference.sh --<mode>-fixes` build exists. Missing downloaded
assets also produce explicit skips. Read the output: a passing suite with skips
is not the same as CI's required suites passing without skips. CI builds each
fixture mode; [the check reference](reference/development-checks.md#continuous-integration)
explains the jobs and their limits. Run the focused suites for the part you change.

For documentation changes, run the [offline documentation checks](documentation-plan.md#verification-and-continuing-maintenance).
If you move or edit command examples, also run their existing contract tests:

```bash
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python scripts/run_tests_no_skips.py \
  tests/test_scripts_boundary.py tests/test_checkpoint_manifest.py \
  tests/test_hud_marker_observer.py
```

These tests use fixtures and the validation bridge, not headset operations.

## Find the next guide

| Task | Guide |
| --- | --- |
| Replay a completed recording | [Replay](guides/replay.md) |
| Understand frames, sources, reports, or cleanup | [Pipeline reference](reference/pipeline.md) |
| Compare detectors or add labels | [Evaluation reference](reference/detector-evaluation.md) |
| Develop the fixed HUD offline | [HUD development](reference/hud-development.md) |
| Work with mirror acquisition | [Mirror reference](reference/mirror.md) |
| Record reviewed results | [Evidence maintenance](evidence/README.md) |
| Inspect the paused provider track | [Capture-provider reference](reference/capture-provider.md) |

## Earlier section links

<details>
<summary>Expand links from the previous guide</summary>

Older links remain valid. Each section below points to its maintained guide or
retained reference.

## Python package and pipeline

See [Python package and pipeline](reference/pipeline.md#python-package-and-pipeline).

### Latency statistics

See [Latency statistics](reference/pipeline.md#latency-statistics).

### Run-context sensor sidecar

See [Run-context sensor sidecar](reference/pipeline.md#run-context-sensor-sidecar).

## Detector evaluation

See [Detector evaluation](reference/detector-evaluation.md#detector-evaluation).

### Pinned seed counts

See [Pinned seed counts](reference/detector-evaluation.md#pinned-seed-counts).

### Tests that need downloaded assets

See [Tests that need downloaded assets](reference/detector-evaluation.md#tests-that-need-downloaded-assets).

## What validation demonstrates

See [What validation demonstrates](reference/development-checks.md#what-validation-demonstrates).

## Window rendering and hardware

See [Window rendering and hardware](reference/development-checks.md#window-rendering-and-hardware).

## Recorded MP4 inspection

See [Recorded MP4 inspection](reference/replay.md#recorded-mp4-inspection).

## File-decoding environment

See [File-decoding environment](reference/replay.md#file-decoding-environment).

## Annotated replay window

See [Annotated replay window](reference/replay.md#annotated-replay-window).

## Continuous integration

See [Continuous integration](reference/development-checks.md#continuous-integration).

## Existing SteamVR mirror diagnostic

See [Existing SteamVR mirror diagnostic](reference/mirror.md#existing-steamvr-mirror-diagnostic).

## Bounded mirror recognition

See [Bounded mirror recognition](reference/mirror.md#bounded-mirror-recognition).

## Paused capture-provider track

See [Paused capture-provider track](reference/capture-provider.md#paused-capture-provider-track).

## Dedicated capture build prerequisites

See [Dedicated capture build prerequisites](reference/capture-provider.md#dedicated-capture-build-prerequisites).

### Compile the unmodified capture reference

See [Compile the unmodified capture reference](reference/capture-provider.md#compile-the-unmodified-capture-reference).

### Build and test descriptor transport changes

See [Build and test descriptor transport changes](reference/capture-provider.md#build-and-test-descriptor-transport-changes).

### Build and test owned publication

See [Build and test owned publication](reference/capture-provider.md#build-and-test-owned-publication).

### Build and test the GPU capability query

See [Build and test the GPU capability query](reference/capture-provider.md#build-and-test-the-gpu-capability-query).

### Build and test image import ownership

See [Build and test image import ownership](reference/capture-provider.md#build-and-test-image-import-ownership).

### Build and test GPU resource ownership

See [Build and test GPU resource ownership](reference/capture-provider.md#build-and-test-gpu-resource-ownership).

## Capture watchdog, provider lifecycle and restoration preparation

See [Capture watchdog, provider lifecycle and restoration preparation](reference/capture-provider.md#capture-watchdog-provider-lifecycle-and-restoration-preparation).

### Worker containment and publication recovery

See [Worker containment and publication recovery](reference/capture-provider.md#worker-containment-and-publication-recovery).

### Provider protocol and layer ownership

See [Provider protocol and layer ownership](reference/capture-provider.md#provider-protocol-and-layer-ownership).

### Inactive configuration bundle

See [Inactive configuration bundle](reference/capture-provider.md#inactive-configuration-bundle).

### Metadata-only provider inspection

See [Metadata-only provider inspection](reference/capture-provider.md#metadata-only-provider-inspection).

### Controlled metadata-layer trial and recovery

See [Controlled metadata-layer trial and recovery](reference/capture-provider.md#controlled-metadata-layer-trial-and-recovery).

## Isolated luma reader and synthetic producer

See [Isolated luma reader and synthetic producer](reference/shared-memory.md#isolated-luma-reader-and-synthetic-producer).

## Service-only recovery rehearsal

See [Service-only recovery rehearsal](reference/capture-provider.md#service-only-recovery-rehearsal).

## Platform-managed session rehearsal

See [Platform-managed session rehearsal](reference/capture-provider.md#platform-managed-session-rehearsal).

## Metadata trial across platform sessions

See [Metadata trial across platform sessions](reference/capture-provider.md#metadata-trial-across-platform-sessions).

## Edge HUD offline test plan

See [Edge HUD offline test plan](reference/hud-development.md#edge-hud-offline-test-plan).

### Checkpoint artifact manifest

See [Checkpoint artifact manifest](reference/hud-development.md#checkpoint-artifact-manifest).

## Observer report to mapping candidate

See [Observer report to mapping candidate](reference/hud-development.md#observer-report-to-mapping-candidate).

## Observer scene precondition statistics

See [Observer scene precondition statistics](reference/hud-development.md#observer-scene-precondition-statistics).

## Synthetic HUD validation counts

See [Synthetic HUD validation counts](reference/hud-development.md#synthetic-hud-validation-counts).

## HUD mask fill evaluation

See [HUD mask fill evaluation](reference/hud-development.md#hud-mask-fill-evaluation).

## Offline foundation for the minimum live POC

See [Offline foundation for the minimum live POC](reference/pipeline.md#offline-foundation-for-the-minimum-live-poc).

## Native VR panel build and validation

See [Native VR panel build and validation](reference/hud-development.md#native-vr-panel-build-and-validation).

### Generated HUD contract header

See [Generated HUD contract header](reference/hud-development.md#generated-hud-contract-header).

## Generated evidence summaries

See [Generated evidence summaries](reference/development-checks.md#generated-evidence-summaries).

</details>
