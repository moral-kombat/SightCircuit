#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 0 ]; then
    echo "Usage: bash scripts/build_capture_supervisor.sh" >&2
    exit 64
fi
sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_output="$sightcircuit_root/artifacts/capture-supervisor"
mkdir -p "$sightcircuit_output"
sightcircuit_stage="$(mktemp -d "$sightcircuit_output/.build.XXXXXX")"
trap 'rm -rf -- "$sightcircuit_stage"' EXIT
"${CXX:-g++}" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
    "$sightcircuit_root/tools/capture_worker_exec.cpp" -o "$sightcircuit_stage/sightcircuit-capture-worker-exec"
mv -- "$sightcircuit_stage/sightcircuit-capture-worker-exec" "$sightcircuit_output/sightcircuit-capture-worker-exec"
echo "Built capture exec boundary. No worker, Vulkan or camera was started."
