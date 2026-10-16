#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
recording_dir="${HOME}/Videos/ArcturusVisionCamera"

cd -- "$project_root"
echo "== Steam Frame inventory =="
python3 scripts/device_check.py
echo "== Latest Arcturus recording metadata =="
if [ ! -d "$recording_dir" ]; then
    echo "Recording directory not found: $recording_dir" >&2
    exit 1
fi
python3 scripts/recording_check.py
