#!/usr/bin/env bash
# Compile the pinned reference or a selected adaptation for inspection.
set -euo pipefail

if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
    echo "Usage: bash scripts/build_capture_reference.sh [--transport-fixes|--publication-fixes|--import-fixes|--resource-fixes|--provider-fixes]"
    echo "Builds the reference or a selected adaptation as inspection artifacts."
    echo "Does not install, load or run it. Runtime fixes are required before deployment."
    exit 0
fi
sightcircuit_mode=reference
if [ "$#" -eq 1 ] && [ "$1" = --transport-fixes ]; then
    sightcircuit_mode=transport
elif [ "$#" -eq 1 ] && [ "$1" = --publication-fixes ]; then
    sightcircuit_mode=publication
elif [ "$#" -eq 1 ] && [ "$1" = --import-fixes ]; then
    sightcircuit_mode=import
elif [ "$#" -eq 1 ] && [ "$1" = --resource-fixes ]; then
    sightcircuit_mode=resources
elif [ "$#" -eq 1 ] && [ "$1" = --provider-fixes ]; then
    sightcircuit_mode=provider
elif [ "$#" -ne 0 ]; then
    echo "Unexpected arguments; use --help." >&2
    exit 64
fi

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_compiler="${CXX:-g++}"
sightcircuit_capture_revision=ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082
sightcircuit_headers_revision=952f776f6573aafbb62ea717d871cd1d6816c387
sightcircuit_reference="$sightcircuit_root/.cache/capture-reference"
sightcircuit_headers="$sightcircuit_root/.cache/vulkan-headers"
sightcircuit_build="$sightcircuit_root/artifacts/capture-reference"
if [ "$sightcircuit_mode" != reference ]; then
    sightcircuit_build="$sightcircuit_root/artifacts/capture-$sightcircuit_mode-reference"
fi
sightcircuit_fetch=""
sightcircuit_stage=""

cleanup() {
    if [ -n "$sightcircuit_fetch" ]; then rm -rf -- "$sightcircuit_fetch"; fi
    if [ -n "$sightcircuit_stage" ]; then rm -rf -- "$sightcircuit_stage"; fi
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
for sightcircuit_tool in git python3 "$sightcircuit_compiler"; do
    command -v "$sightcircuit_tool" >/dev/null
done
mkdir -p "$sightcircuit_root/.cache" "$sightcircuit_build"

# Never run scripts from downloaded repositories. Retain changed caches for inspection.
fetch_pinned() {
    local cache="$1" url="$2" revision="$3"
    shift 3
    if [ ! -e "$cache" ] && [ ! -L "$cache" ]; then
        sightcircuit_fetch="$(mktemp -d "$sightcircuit_root/.cache/capture-fetch.XXXXXX")"
        git -C "$sightcircuit_fetch" init --quiet
        git -C "$sightcircuit_fetch" remote add origin "$url"
        git -C "$sightcircuit_fetch" fetch --quiet --filter=blob:none --depth 1 origin "$revision"
        git -C "$sightcircuit_fetch" sparse-checkout set "$@"
        git -C "$sightcircuit_fetch" checkout --quiet --detach "$revision"
        mv -T -- "$sightcircuit_fetch" "$cache"
        sightcircuit_fetch=""
    fi
    if [ ! -d "$cache/.git" ] || \
        [ "$(git -C "$cache" rev-parse HEAD)" != "$revision" ] || \
        [ -n "$(git -C "$cache" status --porcelain --untracked-files=all)" ]; then
        echo "Cached source differs from the pinned clean checkout; retained: $cache" >&2
        exit 1
    fi
}

echo "Building $sightcircuit_mode inspection artifacts; they are not ready for deployment."
sightcircuit_includes=()
sightcircuit_header_source=system
if ! printf '#include <vulkan/vulkan.h>\n#include <vulkan/vk_layer.h>\n' | \
    "$sightcircuit_compiler" -std=c++17 -x c++ -fsyntax-only - >/dev/null 2>&1; then
    fetch_pinned "$sightcircuit_headers" https://github.com/KhronosGroup/Vulkan-Headers.git \
        "$sightcircuit_headers_revision" include LICENSES
    sightcircuit_includes=("-I$sightcircuit_headers/include")
    sightcircuit_header_source="$sightcircuit_headers_revision"
fi
fetch_pinned "$sightcircuit_reference" https://github.com/spyjacob/steam-frame-hand-tracking.git \
    "$sightcircuit_capture_revision" capture

sightcircuit_stage="$(mktemp -d "$sightcircuit_build/.build.XXXXXX")"
sightcircuit_sources="$sightcircuit_reference"
if [ "$sightcircuit_mode" != reference ]; then
    sightcircuit_sources="$sightcircuit_stage/source"
    mkdir -p "$sightcircuit_sources/capture"
    cp -- "$sightcircuit_reference/LICENSE" "$sightcircuit_sources/LICENSE"
    for sightcircuit_file in handcam_layer.cpp ptgrab.cpp handcam_proto.h; do
        cp -- "$sightcircuit_reference/capture/$sightcircuit_file" "$sightcircuit_sources/capture/"
    done
    cp -- "$sightcircuit_root/tools/capture_transport.hpp" "$sightcircuit_sources/capture/"
    # Apply only our checked-in patch to a disposable copy, never to the pinned cache.
    git -C "$sightcircuit_sources" init --quiet
    git -C "$sightcircuit_sources" apply --check \
        "$sightcircuit_root/patches/mrhand/0001-bounded-descriptor-transport.patch"
    git -C "$sightcircuit_sources" apply \
        "$sightcircuit_root/patches/mrhand/0001-bounded-descriptor-transport.patch"
    if [ "$sightcircuit_mode" = publication ] || [ "$sightcircuit_mode" = import ] || [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
        for sightcircuit_header in capture_publication.hpp shm_common.hpp; do
            cp -- "$sightcircuit_root/tools/$sightcircuit_header" "$sightcircuit_sources/capture/"
        done
        git -C "$sightcircuit_sources" apply --check \
            "$sightcircuit_root/patches/mrhand/0002-owned-bounded-publication.patch"
        git -C "$sightcircuit_sources" apply \
            "$sightcircuit_root/patches/mrhand/0002-owned-bounded-publication.patch"
        "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
            -I"$sightcircuit_root/tools" -I"$sightcircuit_root/third_party/mrhand" \
            "$sightcircuit_root/tools/capture_publication_fixture.cpp" \
            -o "$sightcircuit_stage/sightcircuit-publication-fixture"
    fi
    if [ "$sightcircuit_mode" = import ] || [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
        cp -- "$sightcircuit_root/tools/capture_import.hpp" "$sightcircuit_sources/capture/"
        git -C "$sightcircuit_sources" apply --check \
            "$sightcircuit_root/patches/mrhand/0003-validated-image-import.patch"
        git -C "$sightcircuit_sources" apply \
            "$sightcircuit_root/patches/mrhand/0003-validated-image-import.patch"
        "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
            -Wno-missing-field-initializers "${sightcircuit_includes[@]}" \
            -I"$sightcircuit_root/tools" -I"$sightcircuit_root/third_party/mrhand" \
            "$sightcircuit_root/tools/capture_import_fixture.cpp" \
            -o "$sightcircuit_stage/sightcircuit-import-fixture"
    fi
    if [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
        cp -- "$sightcircuit_root/tools/capture_gpu_resources.hpp" "$sightcircuit_sources/capture/"
        git -C "$sightcircuit_sources" apply --check \
            "$sightcircuit_root/patches/mrhand/0004-owned-gpu-resources.patch"
        git -C "$sightcircuit_sources" apply \
            "$sightcircuit_root/patches/mrhand/0004-owned-gpu-resources.patch"
        "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
            -Wno-missing-field-initializers "${sightcircuit_includes[@]}" \
            -I"$sightcircuit_root/tools" -I"$sightcircuit_root/third_party/mrhand" \
            "$sightcircuit_root/tools/capture_gpu_resources_fixture.cpp" \
            -o "$sightcircuit_stage/sightcircuit-gpu-resources-fixture"
    fi
    if [ "$sightcircuit_mode" = provider ]; then
        for sightcircuit_header in capture_provider.hpp capture_layer_gate.hpp capture_layer_impl.hpp; do
            cp -- "$sightcircuit_root/tools/$sightcircuit_header" "$sightcircuit_sources/capture/"
        done
        git -C "$sightcircuit_sources" apply --check \
            "$sightcircuit_root/patches/mrhand/0005-provider-lifetime-and-provenance.patch"
        git -C "$sightcircuit_sources" apply \
            "$sightcircuit_root/patches/mrhand/0005-provider-lifetime-and-provenance.patch"
        git -C "$sightcircuit_sources" apply --check \
            "$sightcircuit_root/patches/mrhand/0006-block-unleased-camera-reads.patch"
        git -C "$sightcircuit_sources" apply \
            "$sightcircuit_root/patches/mrhand/0006-block-unleased-camera-reads.patch"
        cp -- "$sightcircuit_root/tools/capture_provider_inspect.cpp" "$sightcircuit_sources/capture/"
        "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
            -Wno-missing-field-initializers "${sightcircuit_includes[@]}" \
            "$sightcircuit_sources/capture/capture_provider_inspect.cpp" \
            -o "$sightcircuit_stage/sightcircuit-provider-inspect"
        for sightcircuit_fixture in provider layer; do
            "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
                -Wno-missing-field-initializers "${sightcircuit_includes[@]}" \
                -I"$sightcircuit_root/tools" -I"$sightcircuit_root/third_party/mrhand" \
                "$sightcircuit_root/tools/capture_${sightcircuit_fixture}_fixture.cpp" \
                -o "$sightcircuit_stage/sightcircuit-${sightcircuit_fixture}-fixture"
        done
    fi
    "$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
        -I"$sightcircuit_root/tools" -I"$sightcircuit_root/third_party/mrhand" \
        "$sightcircuit_root/tools/capture_transport_fixture.cpp" \
        -o "$sightcircuit_stage/sightcircuit-transport-fixture"
fi
"$sightcircuit_compiler" -std=c++17 -O2 -fPIC -shared -fvisibility=hidden -Wall \
    -Wno-missing-field-initializers -pthread "${sightcircuit_includes[@]}" \
    "$sightcircuit_sources/capture/handcam_layer.cpp" \
    -o "$sightcircuit_stage/reference-libVkLayer_mrhand.so"
"$sightcircuit_compiler" -std=c++17 -O2 -Wall -Wno-missing-field-initializers -pthread \
    "${sightcircuit_includes[@]}" "$sightcircuit_sources/capture/ptgrab.cpp" \
    -l:libvulkan.so.1 -o "$sightcircuit_stage/reference-ptgrab"
# These are inspection artifacts. Do not offer an executable capture tool yet.
chmod 0644 "$sightcircuit_stage/reference-libVkLayer_mrhand.so" "$sightcircuit_stage/reference-ptgrab"
python3 - "$sightcircuit_stage" "$sightcircuit_sources" "$sightcircuit_capture_revision" \
    "$sightcircuit_header_source" "$sightcircuit_compiler" "$sightcircuit_mode" "$sightcircuit_root" <<'PY'
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
if mode in ("publication", "import", "resources", "provider"):
    report["sources"].extend(digest(reference / "capture" / name)
                             for name in ("capture_publication.hpp", "shm_common.hpp"))
    report["publication_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0002-owned-bounded-publication.patch")
    report["notes"][2] = "Publication and loop controls added; GPU import/resource cleanup, registry refresh and layer lifetime/restoration remain pending."
if mode in ("import", "resources", "provider"):
    report["sources"].append(digest(reference / "capture/capture_import.hpp"))
    report["import_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0003-validated-image-import.patch")
    report["notes"][2] = "Image import validation/ownership added; real GPU support, other GPU resource cleanup/watchdog, registry and layer lifetime/restoration remain pending."
if mode in ("resources", "provider"):
    report["sources"].append(digest(reference / "capture/capture_gpu_resources.hpp"))
    report["resource_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0004-owned-gpu-resources.patch")
    report["notes"][2] = "GPU ownership/bootstrap added; real driver execution, capture watchdog, provider metadata/registry and layer lifetime/restoration remain pending."
if mode == "provider":
    report["sources"].extend(digest(reference / "capture" / name) for name in
                             ("capture_provider.hpp", "capture_layer_gate.hpp", "capture_layer_impl.hpp"))
    report["provider_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0005-provider-lifetime-and-provenance.patch")
    report["access_gate_patch"] = digest(Path(sys.argv[7]) / "patches/mrhand/0006-block-unleased-camera-reads.patch")
    report["sources"].append(digest(reference / "capture/capture_provider_inspect.cpp"))
    report["metadata_inspector"] = digest(stage / "sightcircuit-provider-inspect")
    report["provider_protocol_version"] = 3
    report["image_fd_export_enabled"] = False
    report["layer_retains_image_fds"] = False
    report["notes"][2] = "Metadata-only provider/diagnostic; original producer ownership contract is unavailable, so live image reads and image FD export are blocked."
(stage / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
PY
if [ "$sightcircuit_mode" != reference ]; then
    mv -- "$sightcircuit_stage/sightcircuit-transport-fixture" "$sightcircuit_build/"
fi
if [ "$sightcircuit_mode" = publication ] || [ "$sightcircuit_mode" = import ] || [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
    mv -- "$sightcircuit_stage/sightcircuit-publication-fixture" "$sightcircuit_build/"
fi
if [ "$sightcircuit_mode" = import ] || [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
    mv -- "$sightcircuit_stage/sightcircuit-import-fixture" "$sightcircuit_build/"
fi
if [ "$sightcircuit_mode" = resources ] || [ "$sightcircuit_mode" = provider ]; then
    mv -- "$sightcircuit_stage/sightcircuit-gpu-resources-fixture" "$sightcircuit_build/"
fi
if [ "$sightcircuit_mode" = provider ]; then
    for sightcircuit_fixture in provider layer; do
        mv -- "$sightcircuit_stage/sightcircuit-${sightcircuit_fixture}-fixture" "$sightcircuit_build/"
    done
    mv -- "$sightcircuit_stage/sightcircuit-provider-inspect" "$sightcircuit_build/"
fi
for sightcircuit_file in reference-libVkLayer_mrhand.so reference-ptgrab build-report.json; do
    mv -- "$sightcircuit_stage/$sightcircuit_file" "$sightcircuit_build/$sightcircuit_file"
done
cat "$sightcircuit_build/build-report.json"
echo "Built inspection artifacts in $sightcircuit_build. Nothing was installed or run."
