# SightCircuit session guide

Object recognition for Steam Frame. Start with [docs/README.md](docs/README.md),
[docs/status.md](docs/status.md) (evidence) and the
[next steps](docs/design-review.md#next-steps). Device work follows
[docs/on-device.md](docs/on-device.md); keep cloud checks, CI results and
user-reported headset results distinct, and record evidence in status.

## Layout

- `sightcircuit/`: library (frames, `Pipeline`, sources, detectors, evaluation,
  runtime helpers). See [development](docs/development.md#python-package-and-pipeline).
- `scripts/`: documented command entry points only; they import the checkout
  without installation. Shared code goes in `sightcircuit/`; no active script
  imports another (`tests/test_scripts_boundary.py`). The paused capture-provider
  scripts are quarantined in place; see
  [the capture track](docs/development.md#paused-capture-provider-track).
- `tools/`, `patches/`: native OpenVR and capture-provider code (provider track
  is paused; do not activate layers or restart SteamVR from a cloud session).
- `evaluation/seed.json`: outdoor regression set; private Frame stills go in
  ignored `evaluation/local/`.

## Cloud environment setup

Python 3.12 is required. If `python3` is another version, pass the interpreter:

```bash
sudo apt-get install -y --no-install-recommends libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3
SIGHTCIRCUIT_PYTHON=python3.12 bash scripts/setup_window.sh   # .venv-replay: replay, Qt, OpenCV, ONNX Runtime
bash scripts/setup.sh                                        # .venv: smoke check (needs uv)
bash scripts/build_overlay_bridge.sh && bash scripts/build_shm_reader.sh
bash scripts/build_capture_supervisor.sh && bash scripts/build_capture_gpu_check.sh
```

Qt needs the apt libraries even offscreen. Fixtures and models download from
`raw.githubusercontent.com` and GitHub releases with pinned SHA-256 checksums.
Capture-fixture tests skip unless `scripts/build_capture_reference.sh --<mode>-fixes`
has been run; CI builds all of them.

## Checks before pushing

```bash
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest discover -s tests
uvx ruff@0.14.2 check .
.venv/bin/python scripts/smoke_check.py
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch --expect evaluation/seed-expected.json   # seed pin, as CI
git diff --check
```

CI runs the same jobs on x86_64 (`checks.yml`) and ARM64 (`arm64.yml`) through
`check-jobs.yml`; ARM64 also replays the synthetic stand-in and uploads
runner-CPU latency reports (not Frame results).
