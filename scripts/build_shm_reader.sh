#!/usr/bin/env bash
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_build="$sightcircuit_root/artifacts/shm-reader"
sightcircuit_compiler="${CXX:-g++}"
if [ "$(uname -s)" != Linux ]; then
    echo "Shared-memory diagnostics build on Linux only." >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) echo "Unsupported native architecture: $(uname -m)" >&2; exit 1 ;;
esac
command -v "$sightcircuit_compiler" >/dev/null
mkdir -p "$sightcircuit_build"
trap 'rm -f -- "$sightcircuit_build/sightcircuit-shm-reader.tmp" "$sightcircuit_build/sightcircuit-shm-fixture.tmp"' EXIT
for sightcircuit_tool in reader fixture; do
    "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
        -I"$sightcircuit_root/third_party/mrhand" \
        "$sightcircuit_root/tools/shm_${sightcircuit_tool}.cpp" \
        -o "$sightcircuit_build/sightcircuit-shm-${sightcircuit_tool}.tmp"
done
for sightcircuit_tool in reader fixture; do
    mv -- "$sightcircuit_build/sightcircuit-shm-${sightcircuit_tool}.tmp" \
        "$sightcircuit_build/sightcircuit-shm-${sightcircuit_tool}"
done
echo "Built read-only reader and synthetic fixture in $sightcircuit_build. No producer was started."
