#!/usr/bin/env bash
# Compile the pinned reference or a selected adaptation for inspection.
set -euo pipefail

if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
    echo "Usage: bash scripts/build_capture_reference.sh [--transport-fixes|--publication-fixes]"
    echo "Builds the reference or a selected adaptation as inspection artifacts."
    echo "Does not install, load or run it. Runtime fixes are required before deployment."
    exit 0
fi
framejacker_mode=reference
if [ "$#" -eq 1 ] && [ "$1" = --transport-fixes ]; then
    framejacker_mode=transport
elif [ "$#" -eq 1 ] && [ "$1" = --publication-fixes ]; then
    framejacker_mode=publication
elif [ "$#" -ne 0 ]; then
    echo "Unexpected arguments; use --help." >&2
    exit 64
fi

framejacker_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
framejacker_compiler="${CXX:-g++}"
framejacker_capture_revision=ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082
framejacker_headers_revision=952f776f6573aafbb62ea717d871cd1d6816c387
framejacker_reference="$framejacker_root/.cache/capture-reference"
framejacker_headers="$framejacker_root/.cache/vulkan-headers"
framejacker_build="$framejacker_root/artifacts/capture-reference"
if [ "$framejacker_mode" != reference ]; then
    framejacker_build="$framejacker_root/artifacts/capture-$framejacker_mode-reference"
fi
framejacker_fetch=""
framejacker_stage=""

cleanup() {
    if [ -n "$framejacker_fetch" ]; then rm -rf -- "$framejacker_fetch"; fi
    if [ -n "$framejacker_stage" ]; then rm -rf -- "$framejacker_stage"; fi
}
trap cleanup EXIT

if [ "$(uname -s)" != Linux ]; then
    echo "Capture reference builds require Linux." >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) echo "Unsupported native architecture: $(uname -m)" >&2; exit 1 ;;
esac
for framejacker_tool in git python3 "$framejacker_compiler"; do
    command -v "$framejacker_tool" >/dev/null
done
mkdir -p "$framejacker_root/.cache" "$framejacker_build"

# Never run scripts from downloaded repositories. Retain changed caches for inspection.
fetch_pinned() {
    local cache="$1" url="$2" revision="$3"
    shift 3
    if [ ! -e "$cache" ] && [ ! -L "$cache" ]; then
        framejacker_fetch="$(mktemp -d "$framejacker_root/.cache/capture-fetch.XXXXXX")"
        git -C "$framejacker_fetch" init --quiet
        git -C "$framejacker_fetch" remote add origin "$url"
        git -C "$framejacker_fetch" fetch --quiet --filter=blob:none --depth 1 origin "$revision"
        git -C "$framejacker_fetch" sparse-checkout set "$@"
        git -C "$framejacker_fetch" checkout --quiet --detach "$revision"
        mv -T -- "$framejacker_fetch" "$cache"
        framejacker_fetch=""
    fi
    if [ ! -d "$cache/.git" ] || \
        [ "$(git -C "$cache" rev-parse HEAD)" != "$revision" ] || \
        [ -n "$(git -C "$cache" status --porcelain --untracked-files=all)" ]; then
        echo "Cached source differs from the pinned clean checkout; retained: $cache" >&2
        exit 1
    fi
}

echo "Building $framejacker_mode inspection artifacts; they are not ready for deployment."
framejacker_includes=()
framejacker_header_source=system
if ! printf '#include <vulkan/vulkan.h>\n#include <vulkan/vk_layer.h>\n' | \
    "$framejacker_compiler" -std=c++17 -x c++ -fsyntax-only - >/dev/null 2>&1; then
    fetch_pinned "$framejacker_headers" https://github.com/KhronosGroup/Vulkan-Headers.git \
        "$framejacker_headers_revision" include LICENSES
    framejacker_includes=("-I$framejacker_headers/include")
    framejacker_header_source="$framejacker_headers_revision"
fi
fetch_pinned "$framejacker_reference" https://github.com/spyjacob/steam-frame-hand-tracking.git \
    "$framejacker_capture_revision" capture

framejacker_stage="$(mktemp -d "$framejacker_build/.build.XXXXXX")"
framejacker_sources="$framejacker_reference"
if [ "$framejacker_mode" != reference ]; then
    framejacker_sources="$framejacker_stage/source"
    mkdir -p "$framejacker_sources/capture"
    cp -- "$framejacker_reference/LICENSE" "$framejacker_sources/LICENSE"
    for framejacker_file in handcam_layer.cpp ptgrab.cpp handcam_proto.h; do
        cp -- "$framejacker_reference/capture/$framejacker_file" "$framejacker_sources/capture/"
    done
    cp -- "$framejacker_root/tools/capture_transport.hpp" "$framejacker_sources/capture/"
    # Apply only our checked-in patch to a disposable copy, never to the pinned cache.
    git -C "$framejacker_sources" init --quiet
    git -C "$framejacker_sources" apply --check \
        "$framejacker_root/patches/mrhand/0001-bounded-descriptor-transport.patch"
    git -C "$framejacker_sources" apply \
        "$framejacker_root/patches/mrhand/0001-bounded-descriptor-transport.patch"
    if [ "$framejacker_mode" = publication ]; then
        for framejacker_header in capture_publication.hpp shm_common.hpp; do
            cp -- "$framejacker_root/tools/$framejacker_header" "$framejacker_sources/capture/"
        done
        git -C "$framejacker_sources" apply --check \
            "$framejacker_root/patches/mrhand/0002-owned-bounded-publication.patch"
        git -C "$framejacker_sources" apply \
            "$framejacker_root/patches/mrhand/0002-owned-bounded-publication.patch"
        "$framejacker_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
            -I"$framejacker_root/tools" -I"$framejacker_root/third_party/mrhand" \
            "$framejacker_root/tools/capture_publication_fixture.cpp" \
            -o "$framejacker_stage/framejacker-publication-fixture"
    fi
    "$framejacker_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
        -I"$framejacker_root/tools" -I"$framejacker_root/third_party/mrhand" \
        "$framejacker_root/tools/capture_transport_fixture.cpp" \
        -o "$framejacker_stage/framejacker-transport-fixture"
fi
"$framejacker_compiler" -std=c++17 -O2 -fPIC -shared -fvisibility=hidden -Wall \
    -Wno-missing-field-initializers -pthread "${framejacker_includes[@]}" \
    "$framejacker_sources/capture/handcam_layer.cpp" \
    -o "$framejacker_stage/reference-libVkLayer_mrhand.so"
"$framejacker_compiler" -std=c++17 -O2 -Wall -Wno-missing-field-initializers -pthread \
    "${framejacker_includes[@]}" "$framejacker_sources/capture/ptgrab.cpp" \
    -l:libvulkan.so.1 -o "$framejacker_stage/reference-ptgrab"
# These are inspection artifacts. Do not offer an executable capture tool yet.
chmod 0644 "$framejacker_stage/reference-libVkLayer_mrhand.so" "$framejacker_stage/reference-ptgrab"
python3 - "$framejacker_stage" "$framejacker_sources" "$framejacker_capture_revision" \
    "$framejacker_header_source" "$framejacker_compiler" "$framejacker_mode" "$framejacker_root" <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

stage, reference = map(Path, sys.argv[1:3])
mode = sys.argv[6]
adapted = mode != "reference"
def digest(path):
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size}
report = {
    "report_kind": f"capture_{mode}_reference_build" if adapted else "capture_reference_build",
    "schema_version": 1, "status": f"built_{mode}_reference" if adapted else "built_reference",
    "machine": platform.machine(),
    "compiler_target": subprocess.check_output([sys.argv[5], "-dumpmachine"], text=True).strip(),
    "capture_revision": sys.argv[3], "vulkan_headers": sys.argv[4],
    "vulkan_link_argument": "-l:libvulkan.so.1", "upstream_sources_modified": adapted,
    "sources": [digest(reference / "capture" / name) for name in
                ("handcam_layer.cpp", "ptgrab.cpp", "handcam_proto.h")],
    "artifacts": [digest(stage / name) for name in
                  ("reference-libVkLayer_mrhand.so", "reference-ptgrab")],
    "runtime_initialized": False, "capture_started": False,
    "layer_installation_performed": False, "deployment_ready": False,
    "notes": ["Compile/link check only; neither produced binary was loaded or executed.",
              "Reference artifacts lack execute permission; no layer manifest was generated.",
              "Descriptor, socket, publication and cleanup fixes remain required before deployment."]
}
if adapted:
    report["sources"].append(digest(reference / "capture/capture_transport.hpp"))
    report["transport_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0001-bounded-descriptor-transport.patch")
    report["notes"][2] = "Transport fixes only; GPU import, writer ordering, provider lifecycle and restoration remain pending."
if mode == "publication":
    report["sources"].extend(digest(reference / "capture" / name)
                             for name in ("capture_publication.hpp", "shm_common.hpp"))
    report["publication_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0002-owned-bounded-publication.patch")
    report["notes"][2] = "Publication and loop controls added; GPU import/resource cleanup, registry refresh and layer lifetime/restoration remain pending."
(stage / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
PY
if [ "$framejacker_mode" != reference ]; then
    mv -- "$framejacker_stage/framejacker-transport-fixture" "$framejacker_build/"
fi
if [ "$framejacker_mode" = publication ]; then
    mv -- "$framejacker_stage/framejacker-publication-fixture" "$framejacker_build/"
fi
for framejacker_file in reference-libVkLayer_mrhand.so reference-ptgrab build-report.json; do
    mv -- "$framejacker_stage/$framejacker_file" "$framejacker_build/$framejacker_file"
done
cat "$framejacker_build/build-report.json"
echo "Built inspection artifacts in $framejacker_build. Nothing was installed or run."
