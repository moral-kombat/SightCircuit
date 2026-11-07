#!/usr/bin/env bash
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$sightcircuit_root/scripts/setup_replay.sh"
"$sightcircuit_root/.venv-replay/bin/python" -m pip install --only-binary=:all: \
    --cache-dir "$sightcircuit_root/.cache/pip-replay" \
    -r "$sightcircuit_root/requirements-window.txt"
"$sightcircuit_root/.venv-replay/bin/python" -m pip check
"$sightcircuit_root/.venv-replay/bin/python" "$sightcircuit_root/scripts/fetch_smoke_assets.py"
echo "Replay window ready. Run: .venv-replay/bin/python scripts/replay_window.py"
