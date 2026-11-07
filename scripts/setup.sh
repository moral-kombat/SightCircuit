#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required. Install it using https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.cache/uv}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$PROJECT_ROOT/.cache}"
mkdir -p "$UV_CACHE_DIR" "$XDG_CACHE_HOME"

# The pinned sets are tested with CPython 3.12 (the default here) and 3.14;
# SIGHTCIRCUIT_PYTHON selects a uv-resolvable version or interpreter path.
if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    uv venv --python "${SIGHTCIRCUIT_PYTHON:-3.12}" "$PROJECT_ROOT/.venv"
fi

venv_version="$("$PROJECT_ROOT/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$venv_version" in
    3.12|3.14) ;;
    *) echo "The existing .venv is Python $venv_version; the tested versions are 3.12 and 3.14. Remove it and rerun setup." >&2; exit 1 ;;
esac

uv pip sync --require-hashes --python "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/requirements-dev.lock"
uv pip check --python "$PROJECT_ROOT/.venv/bin/python"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/fetch_smoke_assets.py"

echo "Setup complete (Python $venv_version). Run: .venv/bin/python scripts/smoke_check.py"
