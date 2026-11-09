#!/usr/bin/env bash
# Build the camera helper (docs/reference/camera-helper.md).
# Usage: build_camera_helper.sh [--client-library PATH] [--output DIR]
#
# Always: the fake-backend binary, artifacts/camera-helper/sightcircuit-camera-helper-fake,
# and a syntax check (-fsyntax-only, never a link) of the SDK translation unit
# against the vendored public headers in third_party/arcturus-camera-sdk/.
#
# Only when the operator names a local copy of the Arcturus Client Library
# (--client-library PATH or SIGHTCIRCUIT_AV1_CLIENT_LIBRARY=PATH): the SDK-backed
# binary, sightcircuit-camera-helper-sdk. The library is never downloaded,
# fetched, cached or committed by this repository (THIRD_PARTY.md); the path
# must name an existing archive outside the work tree whose objects match this
# machine's architecture. Anything else is refused before any build starts.
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sightcircuit_build="$PROJECT_ROOT/artifacts/camera-helper"
sightcircuit_sdk_headers="$PROJECT_ROOT/third_party/arcturus-camera-sdk"
sightcircuit_compiler="${CXX:-g++}"
sightcircuit_library="${SIGHTCIRCUIT_AV1_CLIENT_LIBRARY:-}"
sightcircuit_flags=(-std=c++17 -O2 -pthread -Wall -Wextra -Wpedantic -Werror)
sightcircuit_common=("$PROJECT_ROOT/tools/camera_helper.cpp" "$PROJECT_ROOT/tools/camera_helper_reporting.cpp")

usage() {
    cat <<'USAGE'
Usage: build_camera_helper.sh [--client-library PATH] [--output DIR]
  Builds artifacts/camera-helper/sightcircuit-camera-helper-fake (no SDK) and syntax-checks the
  SDK unit against the vendored headers. With --client-library PATH (or the environment variable
  SIGHTCIRCUIT_AV1_CLIENT_LIBRARY), also links sightcircuit-camera-helper-sdk against that local
  copy of libAV1CameraClient.a. The library is never downloaded; the path must be outside the
  work tree. --output DIR builds somewhere other than artifacts/camera-helper (the tests use it).
  Exit 64 on a refused argument, 1 on a build failure.
USAGE
}
refuse() { echo "Refused: $1" >&2; exit 64; }

while [ $# -gt 0 ]; do
    case "$1" in
        --client-library)
            [ $# -ge 2 ] && [ -n "$2" ] || refuse "--client-library needs a path"
            [ -z "$sightcircuit_library" ] || refuse "the client library was named twice (flag and environment)"
            sightcircuit_library="$2"; shift 2 ;;
        --output)
            [ $# -ge 2 ] && [ -n "$2" ] || refuse "--output needs a directory"
            sightcircuit_build="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; refuse "unknown argument $1" ;;
    esac
done

if [ "$(uname -s)" != Linux ]; then
    echo "The camera helper builds on Linux only." >&2
    exit 1
fi
command -v "$sightcircuit_compiler" >/dev/null

sightcircuit_library_resolved=""
if [ -n "$sightcircuit_library" ]; then
    # Every check runs before any compile: a refused library builds nothing.
    [ -f "$sightcircuit_library" ] || refuse "the client library path is not an existing file: $sightcircuit_library"
    sightcircuit_library_resolved="$(realpath -- "$sightcircuit_library")"
    case "$sightcircuit_library_resolved/" in
        "$PROJECT_ROOT"/*) refuse "the client library must stay outside the work tree (it is never committed, cached or built here): $sightcircuit_library_resolved" ;;
    esac
    [ "$(head -c 7 -- "$sightcircuit_library_resolved")" = '!<arch>' ] || refuse "the client library is not an ar archive: $sightcircuit_library_resolved"
    command -v readelf >/dev/null || refuse "readelf is needed to check the archive's architecture"
    case "$(uname -m)" in
        aarch64|arm64) sightcircuit_machine="AArch64" ;;
        x86_64) sightcircuit_machine="Advanced Micro Devices X86-64" ;;
        *) refuse "unsupported architecture $(uname -m)" ;;
    esac
    sightcircuit_machines="$(readelf -h -- "$sightcircuit_library_resolved" 2>/dev/null | sed -n 's/^ *Machine: *//p' | sort -u)"
    [ -n "$sightcircuit_machines" ] || refuse "the archive holds no ELF object: $sightcircuit_library_resolved"
    [ "$sightcircuit_machines" = "$sightcircuit_machine" ] || refuse "the archive's objects are for '$sightcircuit_machines', this machine is $(uname -m): $sightcircuit_library_resolved"
fi

cleanup() { rm -f -- "$sightcircuit_build"/*.tmp "$sightcircuit_build"/camera_helper_sdk.o; }
trap cleanup EXIT
mkdir -p "$sightcircuit_build"
rm -f -- "$sightcircuit_build/build-report.json"  # a failed run leaves no stale report

echo "Building sightcircuit-camera-helper-fake (fake backend, no SDK)."
"$sightcircuit_compiler" "${sightcircuit_flags[@]}" "${sightcircuit_common[@]}" "$PROJECT_ROOT/tools/camera_helper_fake.cpp" \
    -o "$sightcircuit_build/sightcircuit-camera-helper-fake.tmp"
mv -- "$sightcircuit_build/sightcircuit-camera-helper-fake.tmp" "$sightcircuit_build/sightcircuit-camera-helper-fake"
echo "Built: $sightcircuit_build/sightcircuit-camera-helper-fake"

echo "Syntax-checking tools/camera_helper_sdk.cpp against the vendored SDK headers (no link)."
"$sightcircuit_compiler" "${sightcircuit_flags[@]}" -I"$sightcircuit_sdk_headers" -fsyntax-only \
    "$PROJECT_ROOT/tools/camera_helper_sdk.cpp"

sightcircuit_sdk_built=false
if [ -n "$sightcircuit_library_resolved" ]; then
    echo "Compiling the SDK unit and checking the symbols it needs from the Client Library."
    "$sightcircuit_compiler" "${sightcircuit_flags[@]}" -I"$sightcircuit_sdk_headers" -c \
        "$PROJECT_ROOT/tools/camera_helper_sdk.cpp" -o "$sightcircuit_build/camera_helper_sdk.o"
    if nm -C -u -- "$sightcircuit_build/camera_helper_sdk.o" | grep -q 'AV1CameraClient::setControls'; then
        echo "The SDK unit references AV1CameraClient::setControls; the helper never sets a camera control." >&2
        exit 1
    fi
    echo "Linking sightcircuit-camera-helper-sdk against $sightcircuit_library_resolved."
    "$sightcircuit_compiler" "${sightcircuit_flags[@]}" -I"$sightcircuit_sdk_headers" "${sightcircuit_common[@]}" \
        "$sightcircuit_build/camera_helper_sdk.o" "$sightcircuit_library_resolved" \
        -o "$sightcircuit_build/sightcircuit-camera-helper-sdk.tmp"
    mv -- "$sightcircuit_build/sightcircuit-camera-helper-sdk.tmp" "$sightcircuit_build/sightcircuit-camera-helper-sdk"
    sightcircuit_sdk_built=true
    echo "Built: $sightcircuit_build/sightcircuit-camera-helper-sdk"
else
    echo "No client library named: the SDK-backed binary is not built (pass --client-library PATH to a local copy)."
fi

python3 - "$sightcircuit_build" "$sightcircuit_sdk_built" "$sightcircuit_library_resolved" <<'PY'
import hashlib, json, platform, sys
from pathlib import Path
build, sdk_built, library = Path(sys.argv[1]), sys.argv[2] == "true", sys.argv[3]
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
report = {"report_kind": "sightcircuit_camera_helper_build", "schema_version": 1, "machine": platform.machine(),
          "fake_binary": {"name": "sightcircuit-camera-helper-fake", "sha256": digest(build / "sightcircuit-camera-helper-fake")},
          "sdk_unit_syntax_checked": True, "sdk_binary_built": sdk_built,
          "sdk_binary": ({"name": "sightcircuit-camera-helper-sdk", "sha256": digest(build / "sightcircuit-camera-helper-sdk"),
                          "client_library": library} if sdk_built else None),
          "client_library_downloaded": False, "camera_accessed": False}
(build / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
print("Report:", build / "build-report.json")
PY
echo "Validation run: artifacts/camera-helper/sightcircuit-camera-helper-fake --validate"
