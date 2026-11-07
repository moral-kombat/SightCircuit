#!/usr/bin/env bash
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_python="${SIGHTCIRCUIT_PYTHON:-python3}"
sightcircuit_environment="$sightcircuit_root/.venv-replay"

if ! "$sightcircuit_python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
    echo "Replay setup requires Python 3.12 (the version reported on Frame)." >&2
    exit 1
fi
if [ ! -e "$sightcircuit_environment" ]; then
    "$sightcircuit_python" -m venv "$sightcircuit_environment"
fi
if [ ! -x "$sightcircuit_environment/bin/python" ] || \
   ! "$sightcircuit_environment/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix)'; then
    echo "The existing .venv-replay is not a Python 3.12 virtual environment; retained for inspection." >&2
    exit 1
fi
"$sightcircuit_environment/bin/python" -m pip install --only-binary=:all: \
    --cache-dir "$sightcircuit_root/.cache/pip-replay" \
    -r "$sightcircuit_root/requirements-replay.txt"
"$sightcircuit_environment/bin/python" -m pip check
echo "Replay decoder ready. Run: .venv-replay/bin/python scripts/replay_decode_check.py"
