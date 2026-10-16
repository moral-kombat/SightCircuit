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
framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
framejacker_compiler="${CXX:-g++}"
framejacker_headers_revision=952f776f6573aafbb62ea717d871cd1d6816c387
framejacker_headers="$framejacker_root/.cache/vulkan-headers"
framejacker_build="$framejacker_root/artifacts/capture-gpu-check"
framejacker_stage=""
framejacker_fetch=""
cleanup() {
    if [ -n "$framejacker_stage" ]; then rm -rf -- "$framejacker_stage"; fi
    if [ -n "$framejacker_fetch" ]; then rm -rf -- "$framejacker_fetch"; fi
}
trap cleanup EXIT
[ "$(uname -s)" = Linux ] || { echo "This query requires Linux." >&2; exit 1; }
for framejacker_tool in git python3 "$framejacker_compiler"; do command -v "$framejacker_tool" >/dev/null; done
mkdir -p "$framejacker_root/.cache" "$framejacker_build"
framejacker_includes=()
framejacker_header_source=system
if ! printf '#include <vulkan/vulkan.h>\n' | \
    "$framejacker_compiler" -std=c++17 -x c++ -fsyntax-only - >/dev/null 2>&1; then
    if [ ! -e "$framejacker_headers" ] && [ ! -L "$framejacker_headers" ]; then
        framejacker_fetch="$(mktemp -d "$framejacker_root/.cache/gpu-headers.XXXXXX")"
        git -C "$framejacker_fetch" init --quiet
        git -C "$framejacker_fetch" remote add origin https://github.com/KhronosGroup/Vulkan-Headers.git
        git -C "$framejacker_fetch" fetch --quiet --filter=blob:none --depth 1 origin "$framejacker_headers_revision"
        git -C "$framejacker_fetch" sparse-checkout set include LICENSES
        git -C "$framejacker_fetch" checkout --quiet --detach "$framejacker_headers_revision"
        mv -T -- "$framejacker_fetch" "$framejacker_headers"
        framejacker_fetch=""
    fi
    if [ ! -d "$framejacker_headers/.git" ] || \
        [ "$(git -C "$framejacker_headers" rev-parse HEAD)" != "$framejacker_headers_revision" ] || \
        [ -n "$(git -C "$framejacker_headers" status --porcelain --untracked-files=all)" ]; then
        echo "Cached headers differ from the pinned clean checkout; retained: $framejacker_headers" >&2
        exit 1
    fi
    framejacker_includes=("-I$framejacker_headers/include")
    framejacker_header_source="$framejacker_headers_revision"
fi
framejacker_stage="$(mktemp -d "$framejacker_build/.build.XXXXXX")"
"$framejacker_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
    -Wno-missing-field-initializers "${framejacker_includes[@]}" \
    "$framejacker_root/tools/capture_gpu_check.cpp" -ldl \
    -o "$framejacker_stage/framejacker-capture-gpu-check"
python3 - "$framejacker_stage" "$framejacker_root" "$framejacker_header_source" "$framejacker_compiler" <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
stage, root = map(Path, sys.argv[1:3])
binary = stage / 'framejacker-capture-gpu-check'
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
mv -- "$framejacker_stage/framejacker-capture-gpu-check" "$framejacker_build/"
mv -- "$framejacker_stage/build-report.json" "$framejacker_build/"
cat "$framejacker_build/build-report.json"
echo "Built capability query. Run through python3 scripts/capture_gpu_check.py --query-device."
