# Development setup

Set up a Linux development checkout, run a smoke check, then run the checks
needed for your change. No headset is required. Start with [architecture](architecture.md)
for the component overview or [the current plan](live-capture.md) for priorities.

## Prerequisites

Use Python 3.12 or 3.14 (the two tested versions, see
[Python 3.14 on the Frame](#python-314-on-the-frame-2026-11-07)) and a Linux
development host. The smoke environment targets
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

Use an available Python 3.12 or 3.14 path for `SIGHTCIRCUIT_PYTHON` if its
executable has another name; the setup scripts refuse any other version with a
message naming the version found. The replay scripts otherwise use `python3`. Virtual environments
are local to their machine and architecture; do not copy one onto Frame.

### Python 3.14 on the Frame (2026-11-07)

SteamOS 0.5.8 (build 20261106.6144341, applied 2026-11-07) removed Python 3.12
from the Frame and left the distribution's Python 3.14.3 (pacman
`python 3.14.3-1`, aarch64). The device's `.venv-replay` now points at a removed
interpreter, so every Python device step waits for a rebuild, which is a device
step with its own approval. Before it, the owner asked for this review of the
pins against CPython 3.14 on aarch64. Nothing in this section was run on the
Frame: the aarch64 wheels were checked for existence on PyPI only; the x86_64
wheels of the same set were installed and run in the cloud.

**Pin review** (PyPI JSON API, 2026-11-07). "Unchanged" means the pinned
release already publishes a wheel CPython 3.14 on Linux aarch64 can install.

| Package and pin | cp314-capable aarch64 wheel at the pin | Resolution for 3.14 | Notes |
| --- | --- | --- | --- |
| `av==16.1.0` | `av-16.1.0-cp314-cp314-manylinux_2_28_aarch64.whl` | unchanged | 3.12 installs `av-16.1.0-cp312-cp312-manylinux_2_28_aarch64.whl`; `requires_python >=3.10` |
| `numpy==2.2.6` | none; 2.2.6 publishes no cp314 wheel for any platform, nor do 2.3.0 and 2.3.1 | `numpy==2.3.2`, `numpy-2.3.2-cp314-cp314-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl` (released 2025-07-24) | `requires_python` moves from `>=3.10` to `>=3.11`; the wheel needs glibc 2.27 where 2.2.6's cp312 wheel needed 2.17; the 2.3 series is the first with cp314 wheels, see the NumPy 2.3.0 release notes for its API changes |
| `opencv-python-headless==4.12.0.88` | `opencv_python_headless-4.12.0.88-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` installs on 3.14, but the release declares `numpy<2.3.0,>=2`, which excludes numpy 2.3.2; pip refuses the pair | `opencv-python-headless==4.13.0.90`, `opencv_python_headless-4.13.0.90-cp37-abi3-manylinux_2_28_aarch64.whl` (also a `manylinux2014_aarch64.manylinux_2_17_aarch64` build; released 2026-01-18) | first release declaring `numpy>=2` without an upper bound; one OpenCV minor release (4.12 to 4.13); the smoke check below proves the Caffe importer for MobileNet-SSD and the seed pin proves the detector counts on x86_64 |
| `pyside6-essentials==6.12.0` | `pyside6_essentials-6.12.0-cp310-abi3-manylinux_2_39_aarch64.whl` | unchanged | the abi3 wheel 3.12 already installs; `requires_python >=3.10,<3.16`; needs glibc 2.39 (x86_64 wheel: 2.34) |
| `shiboken6==6.12.0` | `shiboken6-6.12.0-cp310-abi3-manylinux_2_39_aarch64.whl` | unchanged | as pyside6-essentials, which pins it exactly |
| `onnxruntime==1.31.0` | `onnxruntime-1.31.0-cp314-cp314-manylinux_2_28_aarch64.whl` | unchanged | declares `numpy>=1.21.6`, `flatbuffers`, `packaging`, `protobuf>=4.25.8`; `requires_python >=3.11` |
| `flatbuffers==25.12.19` | `flatbuffers-25.12.19-py2.py3-none-any.whl` | unchanged | pure Python |
| `packaging==26.3` | `packaging-26.3-py3-none-any.whl` | unchanged | pure Python |
| `protobuf==7.36.2` | `protobuf-7.36.2-cp310-abi3-manylinux2014_aarch64.whl` and `protobuf-7.36.2-py3-none-any.whl` | unchanged | `requires_python >=3.10` |
| `ruff==0.14.2` (dev tooling via `uvx` and CI, not installed on the Frame) | `ruff-0.14.2-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | unchanged | |

So a fully pinned Python 3.14 set exists with exactly two moves, numpy 2.2.6 to
2.3.2 and OpenCV 4.12.0.88 to 4.13.0.90, both forced by numpy's missing cp314
wheel. No source build is involved: setup still installs with
`--only-binary=:all:`.

**What this change does.** The requirement files carry environment markers:
the Python 3.12 pins are byte-identical to before, and an interpreter at 3.14
or later takes the two later releases. `pyproject.toml` requires
`>=3.12,!=3.13.*,<3.15`, the two versions actually tested. The setup scripts
accept 3.12 and 3.14, refuse any other version naming the version they found,
and refuse an existing `.venv-replay` whose interpreter is gone or differs from
the selected one, leaving it for inspection. CI stays on Python 3.12 on both
architectures; a 3.14 job would be a fourth job in the layout that
`tests/test_workflows.py` pins and a second replay run per push, so it is a
planning decision, not part of this change. The checkpoint manifest and
`scripts/device_check.py` already record the interpreter version, so a Frame
run under 3.14 will differ from every 3.12 manifest in `python.version`; that
difference is expected and is not a checkout drift.

**Cloud results**, x86_64, Ubuntu 24.04, CPython 3.14.6 (a uv-managed
python-build-standalone interpreter, not the Frame's pacman build). These are
runner results, not Frame results.

| Step | Result |
| --- | --- |
| `SIGHTCIRCUIT_PYTHON=python3.14 bash scripts/setup_window.sh` | built; `pip check` clean; installed av 16.1.0, numpy 2.3.2, opencv-python-headless 4.13.0.90, PySide6-Essentials 6.12.0, shiboken6 6.12.0, onnxruntime 1.31.0 |
| `SIGHTCIRCUIT_PYTHON=3.14 bash scripts/setup.sh` (uv) | built; `uv pip check` clean |
| `.venv/bin/python scripts/smoke_check.py` | `status: passed`; MobileNet-SSD loads through OpenCV 4.13's Caffe importer, YOLOX through ONNX Runtime, Qt renders offscreen |
| `evaluate_detectors.py --fetch --expect evaluation/seed-expected.json` | passed, 6 images, no failures, improvements or unpinned entries: the seed counts are unchanged under numpy 2.3.2 and OpenCV 4.13.0.90 |
| `QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest discover -s tests` | `Ran 1145 tests in 278.491s`, `OK (skipped=55)`; all 55 skips are capture-reference fixture tests (27 provider, 11 import, 9 resource, 8 publication) whose `build_capture_reference.sh --<mode>-fixes` builds were not run on this host; CI builds them and runs those modules under its no-skip gate on 3.12 |
| Python 3.12 with the same files | `pip install --only-binary=:all: -r requirements-window.txt` under CPython 3.12 resolves to the unchanged pins (numpy 2.2.6, opencv-python-headless 4.12.0.88), `pip check` clean; `uv pip install --dry-run -r requirements-dev.txt` on that environment would make no changes. CI runs the 3.12 suites on the pull request |

**The device step** (not performed here; the Frame manager runs it on the
owner's word, and its first result is new evidence): move the stale
`.venv-replay` aside, run `SIGHTCIRCUIT_PYTHON=python3.14 bash scripts/setup_window.sh`
(or `setup_replay.sh` for the decoder alone), then the device checks of the
guide being followed. The pyside6 wheel needs glibc 2.39 and the av and
onnxruntime wheels glibc 2.28, the same requirements the 3.12 wheels had.

**The alternative the owner can choose instead: a user-space Python 3.12**,
keeping the 3.12 pins on the device. It was not run anywhere in this review.
uv 0.12.23 is the version CI pins; its aarch64 wheel on PyPI is
`uv-0.12.23-py3-none-manylinux_2_28_aarch64.whl`, SHA-256
`9346c741175c7fc0d380f30fcfcb8d5e13f5f900b30536230c8d34208524e47a` (the
`digests.sha256` field of `https://pypi.org/pypi/uv/0.12.23/json`). uv then
downloads a CPython 3.12 build from the python-build-standalone GitHub
releases and verifies its own checksum for it. This adds a second interpreter
that the SteamOS update channel does not maintain.

```bash
python3 -m venv "$HOME/.sightcircuit-uv"
printf 'uv==0.12.23 --hash=sha256:9346c741175c7fc0d380f30fcfcb8d5e13f5f900b30536230c8d34208524e47a\n' > "$HOME/.sightcircuit-uv/uv.txt"
"$HOME/.sightcircuit-uv/bin/pip" install --only-binary=:all: --require-hashes -r "$HOME/.sightcircuit-uv/uv.txt"
"$HOME/.sightcircuit-uv/bin/uv" python install 3.12
SIGHTCIRCUIT_PYTHON="$("$HOME/.sightcircuit-uv/bin/uv" python find 3.12)" bash scripts/setup_window.sh
```

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
