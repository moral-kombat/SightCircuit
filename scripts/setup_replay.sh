#!/usr/bin/env bash
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_python="${SIGHTCIRCUIT_PYTHON:-python3}"
sightcircuit_environment="$sightcircuit_root/.venv-replay"

# The pinned sets are tested with CPython 3.12 and 3.14 only (3.14 is the
# Frame's interpreter since SteamOS 0.5.8); docs/development.md explains.
sightcircuit_version="$("$sightcircuit_python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
case "$sightcircuit_version" in
    3.12|3.14) ;;
    "") echo "Replay setup requires Python 3.12 or 3.14; '$sightcircuit_python' did not run. Set SIGHTCIRCUIT_PYTHON to a tested interpreter." >&2; exit 1 ;;
    *) echo "Replay setup requires Python 3.12 or 3.14 (the tested versions); '$sightcircuit_python' is Python $sightcircuit_version. Set SIGHTCIRCUIT_PYTHON to a tested interpreter." >&2; exit 1 ;;
esac
if [ ! -e "$sightcircuit_environment" ]; then
    "$sightcircuit_python" -m venv "$sightcircuit_environment"
fi
if [ ! -x "$sightcircuit_environment/bin/python" ] || \
   ! "$sightcircuit_environment/bin/python" -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)' 2>/dev/null; then
    echo "The existing .venv-replay is not a working virtual environment (its interpreter may have been removed by a system update); retained for inspection. Move it aside to rebuild." >&2
    exit 1
fi
sightcircuit_environment_version="$("$sightcircuit_environment/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$sightcircuit_environment_version" != "$sightcircuit_version" ]; then
    echo "The existing .venv-replay is Python $sightcircuit_environment_version but the selected interpreter is Python $sightcircuit_version; retained for inspection. Move it aside to rebuild." >&2
    exit 1
fi
"$sightcircuit_environment/bin/python" -m pip install --only-binary=:all: \
    --cache-dir "$sightcircuit_root/.cache/pip-replay" \
    -r "$sightcircuit_root/requirements-replay.txt"
"$sightcircuit_environment/bin/python" -m pip check
echo "Replay decoder ready (Python $sightcircuit_version). Run: .venv-replay/bin/python scripts/replay_decode_check.py"
