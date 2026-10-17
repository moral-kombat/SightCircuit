#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 0 ]; then
    echo "Usage: bash scripts/build_capture_supervisor.sh" >&2
    exit 64
fi
framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
framejacker_output="$framejacker_root/artifacts/capture-supervisor"
mkdir -p "$framejacker_output"
framejacker_stage="$(mktemp -d "$framejacker_output/.build.XXXXXX")"
trap 'rm -rf -- "$framejacker_stage"' EXIT
"${CXX:-g++}" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
    "$framejacker_root/tools/capture_worker_exec.cpp" -o "$framejacker_stage/framejacker-capture-worker-exec"
mv -- "$framejacker_stage/framejacker-capture-worker-exec" "$framejacker_output/framejacker-capture-worker-exec"
echo "Built capture exec boundary. No worker, Vulkan or camera was started."
