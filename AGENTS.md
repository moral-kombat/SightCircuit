# SightCircuit session guide

Object recognition for Steam Frame. Start with [docs/README.md](docs/README.md),
[docs/status.md](docs/status.md) (evidence) and the
[current work and acceptance](docs/live-capture.md#current-work-order). Device work follows
[docs/on-device.md](docs/on-device.md); keep cloud checks, CI results and
user-reported headset results distinct. Record evidence in `docs/evidence/`
and regenerate status; do not edit generated summaries by hand.

## Layout

- `sightcircuit/`: library (frames, `Pipeline`, sources, detectors, evaluation,
  runtime helpers). See [development](docs/development.md#python-package-and-pipeline).
- `scripts/`: documented command files only; they import the checkout
  without installation. The thirteen active commands' bodies are in
  `sightcircuit/commands/` with `scripts/<name>.py` as thin wrappers and
  `sightcircuit-<name>` console entry points; shared code goes in
  `sightcircuit/`; no active script imports another (`tests/test_scripts_boundary.py`). The paused capture-provider
  scripts are quarantined in place; see
  [the capture track](docs/development.md#paused-capture-provider-track).
- `tools/`, `patches/`: native OpenVR and capture-provider code (provider track
  is paused; do not activate layers or restart SteamVR from a cloud session).
- `evaluation/seed.json`: outdoor regression set; private Frame stills go in
  ignored `evaluation/local/`.

## Cloud environment setup

Python 3.12 or 3.14 is required (the tested versions; see
[Python 3.14 on the Frame](docs/development.md#python-314-on-the-frame-2026-11-07)).
If `python3` is another version, pass the interpreter:

```bash
sudo apt-get install -y --no-install-recommends libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3
SIGHTCIRCUIT_PYTHON=python3.12 bash scripts/setup_window.sh   # .venv-replay: replay, Qt, OpenCV, ONNX Runtime
bash scripts/setup.sh                                        # .venv: smoke check (needs uv)
bash scripts/build_overlay_bridge.sh && bash scripts/build_shm_reader.sh
bash scripts/build_capture_supervisor.sh && bash scripts/build_capture_gpu_check.sh
bash scripts/build_camera_helper.sh                          # fake backend only; the SDK unit is syntax-checked
```

Qt needs the apt libraries even offscreen. Fixtures and models download from
`raw.githubusercontent.com` and GitHub releases with pinned SHA-256 checksums.
Capture-fixture tests skip unless `scripts/build_capture_reference.sh --<mode>-fixes`
has been run; CI builds all of them.

## Checks before pushing

```bash
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest discover -s tests
python3 scripts/check_docs.py
python3 scripts/generate_status.py --check
uvx ruff@0.14.2 check .
.venv/bin/python scripts/smoke_check.py
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch --expect evaluation/seed-expected.json   # seed pin, as CI
git diff --check
```

CI runs `check-jobs.yml` on x86_64 (`checks.yml`) for pull requests and main
pushes, and on ARM64 (`arm64.yml`) for main pushes and on demand. The optional
ARM64 pacing run reports runner-CPU latency, not Frame measurements.
Documentation-only changes run `docs.yml`; `tests/test_workflows.py` protects
which document inputs require the full suite.

## Documentation navigation

[Development setup](docs/development.md) owns environment and check instructions.
[Device access](docs/on-device.md) routes to focused [task guides](docs/README.md#use-steam-frame).
[Scope](docs/poc-scope.md) owns accepted decisions; only
[current work](docs/live-capture.md) owns the next work order and gate state.
Historical reviews and archived handoffs explain earlier decisions, not current
assignments. Evidence E79–E84 from the former Framejacker PR #23 is now merged
through SightCircuit PR #1. Use the current evidence catalog and check pending
allocations before assigning an ID; historical handoffs are not allocation authority.
See [documentation ownership](docs/documentation-plan.md) before moving content.
