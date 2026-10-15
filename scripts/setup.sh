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

if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    uv venv --python 3.12 "$PROJECT_ROOT/.venv"
fi

if ! "$PROJECT_ROOT/.venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
    echo "The existing .venv must use Python 3.12. Recreate that environment before running setup." >&2
    exit 1
fi

uv pip sync --python "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/requirements-dev.txt"
uv pip check --python "$PROJECT_ROOT/.venv/bin/python"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/fetch_smoke_assets.py"

echo "Setup complete. Run: .venv/bin/python scripts/smoke_check.py"
