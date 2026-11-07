#!/usr/bin/env bash
# Regenerate the hashed locks from the requirement files. The locks are in
# requirements format so both `pip install --require-hashes` (the Frame and the
# replay environments) and `uv pip sync --require-hashes` (the smoke
# environment) consume them. A universal resolution keeps the Python-version
# markers of the requirement files and records every published distribution
# hash of each pinned release, so one lock serves x86_64 and aarch64 under
# Python 3.12 and 3.14. Run from any directory; uv 0.12.23 is CI's pin.
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_uv="${SIGHTCIRCUIT_UV:-uv}"
if ! command -v "$sightcircuit_uv" >/dev/null 2>&1; then
    echo "uv is required (CI pins uv==0.12.23); set SIGHTCIRCUIT_UV to its path." >&2
    exit 1
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-$sightcircuit_root/.cache/uv}"
for set in replay window dev; do
    "$sightcircuit_uv" pip compile --quiet --universal --generate-hashes --python-version 3.12 \
        --custom-compile-command "bash scripts/compile_locks.sh" \
        --output-file "$sightcircuit_root/requirements-$set.lock" \
        "$sightcircuit_root/requirements-$set.txt"
done
echo "Locks regenerated with $("$sightcircuit_uv" --version): requirements-{replay,window,dev}.lock"
