#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in
    camera-probe) sightcircuit_source=camera_probe.cpp; sightcircuit_name=sightcircuit-camera-probe ;;
    overlay-bridge) sightcircuit_source=overlay_bridge.cpp; sightcircuit_name=sightcircuit-overlay-bridge ;;
    *) echo "Usage: build_openvr_tool.sh camera-probe|overlay-bridge" >&2; exit 64 ;;
esac
OPENVR_REVISION=0924064316de3effbcd1acf1e309182a2deb1c05
sightcircuit_sdk="$PROJECT_ROOT/.cache/openvr"
sightcircuit_build="$PROJECT_ROOT/artifacts/$1"
sightcircuit_compiler="${CXX:-g++}"
sightcircuit_temporary_sdk=""

cleanup() {
    if [ -n "$sightcircuit_temporary_sdk" ] && [ -d "$sightcircuit_temporary_sdk" ]; then
        rm -rf -- "$sightcircuit_temporary_sdk"
    fi
    rm -f -- "$sightcircuit_build/$sightcircuit_name.tmp"
}
trap cleanup EXIT

if [ "$(uname -s)" != Linux ]; then
    echo "Native OpenVR tools currently build on Linux only." >&2
    exit 1
fi
case "$(uname -m)" in
    aarch64|arm64) sightcircuit_arch_define=LINUXARM64 ;;
    x86_64) sightcircuit_arch_define=LINUX64 ;;
    *) echo "Unsupported native architecture: $(uname -m)" >&2; exit 1 ;;
esac
command -v git >/dev/null
command -v "$sightcircuit_compiler" >/dev/null
mkdir -p "$PROJECT_ROOT/.cache" "$sightcircuit_build"

if [ ! -e "$sightcircuit_sdk" ]; then
    sightcircuit_temporary_sdk="$(mktemp -d "$PROJECT_ROOT/.cache/openvr-fetch.XXXXXX")"
    git clone --quiet --filter=blob:none --depth 1 --no-checkout \
        https://github.com/ValveSoftware/openvr.git "$sightcircuit_temporary_sdk"
    if ! git -C "$sightcircuit_temporary_sdk" cat-file -e "$OPENVR_REVISION^{commit}" 2>/dev/null; then
        git -C "$sightcircuit_temporary_sdk" fetch --quiet --depth 1 origin "$OPENVR_REVISION"
    fi
    # Keep only headers, portable loader sources and root notices in the cache.
    git -C "$sightcircuit_temporary_sdk" sparse-checkout set headers src
    git -C "$sightcircuit_temporary_sdk" checkout --quiet --detach "$OPENVR_REVISION"
    mv -- "$sightcircuit_temporary_sdk" "$sightcircuit_sdk"
    sightcircuit_temporary_sdk=""
fi

if [ ! -d "$sightcircuit_sdk/.git" ] || \
    [ "$(git -C "$sightcircuit_sdk" rev-parse HEAD)" != "$OPENVR_REVISION" ] || \
    [ -n "$(git -C "$sightcircuit_sdk" status --porcelain --untracked-files=all)" ]; then
    echo "The cached OpenVR SDK differs from the pinned clean checkout; retained for inspection." >&2
    exit 1
fi

sightcircuit_sources=(
    "$PROJECT_ROOT/tools/$sightcircuit_source"
    "$sightcircuit_sdk/src/openvr_api_public.cpp"
    "$sightcircuit_sdk/src/jsoncpp.cpp"
)
for sightcircuit_source in dirtools envvartools pathtools sharedlibtools hmderrors vrpathregistry strtools; do
    sightcircuit_sources+=("$sightcircuit_sdk/src/vrcore/${sightcircuit_source}_public.cpp")
done

echo "Building $sightcircuit_name against OpenVR $OPENVR_REVISION."
"$sightcircuit_compiler" -std=c++17 -O2 -pthread \
    -DLINUX -DPOSIX "-D$sightcircuit_arch_define" \
    -DVR_API_PUBLIC -DVRCORE_NO_PLATFORM -DOPENVR_BUILD_STATIC \
    -I"$sightcircuit_sdk/headers" -I"$sightcircuit_sdk/src" -I"$sightcircuit_sdk/src/vrcore" \
    "${sightcircuit_sources[@]}" -ldl \
    -o "$sightcircuit_build/$sightcircuit_name.tmp"
mv -- "$sightcircuit_build/$sightcircuit_name.tmp" "$sightcircuit_build/$sightcircuit_name"
echo "Built: $sightcircuit_build/$sightcircuit_name"
