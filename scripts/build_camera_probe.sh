#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OPENVR_REVISION=0924064316de3effbcd1acf1e309182a2deb1c05
framejacker_sdk="$PROJECT_ROOT/.cache/openvr"
framejacker_build="$PROJECT_ROOT/artifacts/camera-probe"
framejacker_compiler="${CXX:-g++}"
framejacker_temporary_sdk=""

cleanup() {
    if [ -n "$framejacker_temporary_sdk" ] && [ -d "$framejacker_temporary_sdk" ]; then
        rm -rf -- "$framejacker_temporary_sdk"
    fi
    rm -f -- "$framejacker_build/framejacker-camera-probe.tmp"
}
trap cleanup EXIT

if [ "$(uname -s)" != Linux ]; then
    echo "The camera probe currently builds on Linux only." >&2
    exit 1
fi
case "$(uname -m)" in
    aarch64|arm64) framejacker_arch_define=LINUXARM64 ;;
    x86_64) framejacker_arch_define=LINUX64 ;;
    *) echo "Unsupported native architecture: $(uname -m)" >&2; exit 1 ;;
esac
command -v git >/dev/null
command -v "$framejacker_compiler" >/dev/null
mkdir -p "$PROJECT_ROOT/.cache" "$framejacker_build"

if [ ! -e "$framejacker_sdk" ]; then
    framejacker_temporary_sdk="$(mktemp -d "$PROJECT_ROOT/.cache/openvr-fetch.XXXXXX")"
    git clone --quiet --filter=blob:none --depth 1 --no-checkout \
        https://github.com/ValveSoftware/openvr.git "$framejacker_temporary_sdk"
    if ! git -C "$framejacker_temporary_sdk" cat-file -e "$OPENVR_REVISION^{commit}" 2>/dev/null; then
        git -C "$framejacker_temporary_sdk" fetch --quiet --depth 1 origin "$OPENVR_REVISION"
    fi
    # Keep only headers, portable loader sources and root notices in the cache.
    git -C "$framejacker_temporary_sdk" sparse-checkout set headers src
    git -C "$framejacker_temporary_sdk" checkout --quiet --detach "$OPENVR_REVISION"
    mv -- "$framejacker_temporary_sdk" "$framejacker_sdk"
    framejacker_temporary_sdk=""
fi

if [ ! -d "$framejacker_sdk/.git" ] || \
    [ "$(git -C "$framejacker_sdk" rev-parse HEAD)" != "$OPENVR_REVISION" ] || \
    [ -n "$(git -C "$framejacker_sdk" status --porcelain --untracked-files=all)" ]; then
    echo "The cached OpenVR SDK differs from the pinned clean checkout; retained for inspection." >&2
    exit 1
fi

framejacker_sources=(
    "$PROJECT_ROOT/tools/camera_probe.cpp"
    "$framejacker_sdk/src/openvr_api_public.cpp"
    "$framejacker_sdk/src/jsoncpp.cpp"
)
for framejacker_source in dirtools envvartools pathtools sharedlibtools hmderrors vrpathregistry strtools; do
    framejacker_sources+=("$framejacker_sdk/src/vrcore/${framejacker_source}_public.cpp")
done

echo "Building the native camera probe against OpenVR $OPENVR_REVISION."
"$framejacker_compiler" -std=c++17 -O2 -pthread \
    -DLINUX -DPOSIX "-D$framejacker_arch_define" \
    -DVR_API_PUBLIC -DVRCORE_NO_PLATFORM -DOPENVR_BUILD_STATIC \
    -I"$framejacker_sdk/headers" -I"$framejacker_sdk/src" -I"$framejacker_sdk/src/vrcore" \
    "${framejacker_sources[@]}" -ldl \
    -o "$framejacker_build/framejacker-camera-probe.tmp"
mv -- "$framejacker_build/framejacker-camera-probe.tmp" \
    "$framejacker_build/framejacker-camera-probe"
echo "Built: $framejacker_build/framejacker-camera-probe"
echo "The probe has interrupted headset passthrough; default execution makes no runtime calls."
echo "Use --help to read the explicit opt-in. Do not repeat the unchanged headset test."
