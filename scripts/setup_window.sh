#!/usr/bin/env bash
set -euo pipefail

framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$framejacker_root/scripts/setup_replay.sh"
"$framejacker_root/.venv-replay/bin/python" -m pip install --only-binary=:all: \
    --cache-dir "$framejacker_root/.cache/pip-replay" \
    -r "$framejacker_root/requirements-window.txt"
"$framejacker_root/.venv-replay/bin/python" -m pip check
"$framejacker_root/.venv-replay/bin/python" "$framejacker_root/scripts/fetch_smoke_assets.py"
echo "Replay window ready. Run: .venv-replay/bin/python scripts/replay_window.py"
