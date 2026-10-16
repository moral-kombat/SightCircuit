#!/usr/bin/env bash
set -euo pipefail

framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
framejacker_build="$framejacker_root/artifacts/shm-reader"
framejacker_compiler="${CXX:-g++}"
if [ "$(uname -s)" != Linux ]; then
    echo "Shared-memory diagnostics build on Linux only." >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) echo "Unsupported native architecture: $(uname -m)" >&2; exit 1 ;;
esac
command -v "$framejacker_compiler" >/dev/null
mkdir -p "$framejacker_build"
trap 'rm -f -- "$framejacker_build/framejacker-shm-reader.tmp" "$framejacker_build/framejacker-shm-fixture.tmp"' EXIT
for framejacker_tool in reader fixture; do
    "$framejacker_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
        -I"$framejacker_root/third_party/mrhand" \
        "$framejacker_root/tools/shm_${framejacker_tool}.cpp" \
        -o "$framejacker_build/framejacker-shm-${framejacker_tool}.tmp"
done
for framejacker_tool in reader fixture; do
    mv -- "$framejacker_build/framejacker-shm-${framejacker_tool}.tmp" \
        "$framejacker_build/framejacker-shm-${framejacker_tool}"
done
echo "Built read-only reader and synthetic fixture in $framejacker_build. No producer was started."
