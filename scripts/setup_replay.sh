#!/usr/bin/env bash
set -euo pipefail

framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
framejacker_python="${FRAMEJACKER_PYTHON:-python3}"
framejacker_environment="$framejacker_root/.venv-replay"

if ! "$framejacker_python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
    echo "Replay setup requires Python 3.12 (the version reported on Frame)." >&2
    exit 1
fi
if [ ! -e "$framejacker_environment" ]; then
    "$framejacker_python" -m venv "$framejacker_environment"
fi
if [ ! -x "$framejacker_environment/bin/python" ] || \
   ! "$framejacker_environment/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix)'; then
    echo "The existing .venv-replay is not a Python 3.12 virtual environment; retained for inspection." >&2
    exit 1
fi
"$framejacker_environment/bin/python" -m pip install --only-binary=:all: \
    --cache-dir "$framejacker_root/.cache/pip-replay" \
    -r "$framejacker_root/requirements-replay.txt"
"$framejacker_environment/bin/python" -m pip check
echo "Replay decoder ready. Run: .venv-replay/bin/python scripts/replay_decode_check.py"
