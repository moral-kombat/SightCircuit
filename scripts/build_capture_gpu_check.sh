#!/usr/bin/env bash
# Build the capability query; never load Vulkan or execute the output here.
set -euo pipefail
if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
    echo "Usage: bash scripts/build_capture_gpu_check.sh"
    echo "Builds a guarded Vulkan capability query, without running it."
    exit 0
elif [ "$#" -ne 0 ]; then
    echo "Unexpected arguments; use --help." >&2
    exit 64
fi
sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_compiler="${CXX:-g++}"
sightcircuit_headers_revision=952f776f6573aafbb62ea717d871cd1d6816c387
sightcircuit_headers="$sightcircuit_root/.cache/vulkan-headers"
sightcircuit_build="$sightcircuit_root/artifacts/capture-gpu-check"
sightcircuit_stage=""
sightcircuit_fetch=""
cleanup() {
    if [ -n "$sightcircuit_stage" ]; then rm -rf -- "$sightcircuit_stage"; fi
    if [ -n "$sightcircuit_fetch" ]; then rm -rf -- "$sightcircuit_fetch"; fi
}
trap cleanup EXIT
[ "$(uname -s)" = Linux ] || { echo "This query requires Linux." >&2; exit 1; }
for sightcircuit_tool in git python3 "$sightcircuit_compiler"; do command -v "$sightcircuit_tool" >/dev/null; done
mkdir -p "$sightcircuit_root/.cache" "$sightcircuit_build"
sightcircuit_includes=()
sightcircuit_header_source=system
if ! printf '#include <vulkan/vulkan.h>\n' | \
    "$sightcircuit_compiler" -std=c++17 -x c++ -fsyntax-only - >/dev/null 2>&1; then
    if [ ! -e "$sightcircuit_headers" ] && [ ! -L "$sightcircuit_headers" ]; then
        sightcircuit_fetch="$(mktemp -d "$sightcircuit_root/.cache/gpu-headers.XXXXXX")"
        git -C "$sightcircuit_fetch" init --quiet
        git -C "$sightcircuit_fetch" remote add origin https://github.com/KhronosGroup/Vulkan-Headers.git
        git -C "$sightcircuit_fetch" fetch --quiet --filter=blob:none --depth 1 origin "$sightcircuit_headers_revision"
        git -C "$sightcircuit_fetch" sparse-checkout set include LICENSES
        git -C "$sightcircuit_fetch" checkout --quiet --detach "$sightcircuit_headers_revision"
        mv -T -- "$sightcircuit_fetch" "$sightcircuit_headers"
        sightcircuit_fetch=""
    fi
    if [ ! -d "$sightcircuit_headers/.git" ] || \
        [ "$(git -C "$sightcircuit_headers" rev-parse HEAD)" != "$sightcircuit_headers_revision" ] || \
        [ -n "$(git -C "$sightcircuit_headers" status --porcelain --untracked-files=all)" ]; then
        echo "Cached headers differ from the pinned clean checkout; retained: $sightcircuit_headers" >&2
        exit 1
    fi
    sightcircuit_includes=("-I$sightcircuit_headers/include")
    sightcircuit_header_source="$sightcircuit_headers_revision"
fi
sightcircuit_stage="$(mktemp -d "$sightcircuit_build/.build.XXXXXX")"
"$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
    -Wno-missing-field-initializers "${sightcircuit_includes[@]}" \
    "$sightcircuit_root/tools/capture_gpu_check.cpp" -ldl \
    -o "$sightcircuit_stage/sightcircuit-capture-gpu-check"
python3 - "$sightcircuit_stage" "$sightcircuit_root" "$sightcircuit_header_source" "$sightcircuit_compiler" <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
stage, root = map(Path, sys.argv[1:3])
binary = stage / 'sightcircuit-capture-gpu-check'
report = {
    'report_kind': 'capture_gpu_check_build', 'schema_version': 1, 'status': 'built',
    'machine': platform.machine(),
    'compiler_target': subprocess.check_output([sys.argv[4], '-dumpmachine'], text=True).strip(),
    'vulkan_headers': sys.argv[3],
    'source_sha256': hashlib.sha256((root / 'tools/capture_gpu_check.cpp').read_bytes()).hexdigest(),
    'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'vulkan_loaded': False, 'camera_opened': False, 'layer_installation_performed': False,
}
(stage / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
PY
mv -- "$sightcircuit_stage/sightcircuit-capture-gpu-check" "$sightcircuit_build/"
mv -- "$sightcircuit_stage/build-report.json" "$sightcircuit_build/"
cat "$sightcircuit_build/build-report.json"
echo "Built capability query. Run through python3 scripts/capture_gpu_check.py --query-device."
