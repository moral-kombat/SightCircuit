#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
recording_dir="${HOME}/Videos/ArcturusVisionCamera"

cd -- "$project_root"
echo "== Steam Frame inventory =="
python3 scripts/device_check.py
echo "== Latest Arcturus recording metadata =="
python3 scripts/recording_check.py "$recording_dir"
