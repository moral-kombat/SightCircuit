#!/usr/bin/env bash
# Build the camera helper (tools/camera_helper.cpp).
#
#   --fake [--out DIR]            the fake-backend helper, on any architecture, for tests;
#                                 default output artifacts/camera-helper-fake/
#   --sdk DIR [--check-sdk] [--image-id ID]
#                                 verify a locally supplied Arcturus Vision Camera Preview SDK
#                                 against its release.json and the vendored headers, then (on
#                                 aarch64 only) link lib/libAV1CameraClient.a into ignored
#                                 artifacts/camera-helper/. --check-sdk stops after the checks.
#
# DIR must lie outside the work tree. Nothing is fetched and no SDK file is
# copied into the checkout: each developer obtains their own SDK from Arcturus
# (third_party/arcturus-camera-sdk/README.md). Only the SDK translation unit is
# compiled with the vendored header directory; DIR is never an include path.
set -euo pipefail

sightcircuit_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
sightcircuit_compiler="${CXX:-g++}"
sightcircuit_vendored="$sightcircuit_root/third_party/arcturus-camera-sdk"
sightcircuit_tools="$sightcircuit_root/tools"
sightcircuit_flags=(-std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -pthread)
# Every Client Library symbol the helper may reference, and the one object that may reference them.
sightcircuit_allowed_symbols=(
    _ZN15AV1CameraClient7connectEj
    _ZN15AV1CameraClient10disconnectEv
    _ZNK15AV1CameraClient11isConnectedEv
    _ZN15AV1CameraClient15getControlStateERN7av1_ipc12ControlStateE
    _ZN15AV1CameraClient14setReaderStateEbb
    _ZN15AV1CameraClient15acquireFrameSetERNS_8FrameSetEj
    _ZN15AV1CameraClient8FrameSet7releaseEv
    _ZN15AV1CameraClient8FrameSetD1Ev
    _ZN15AV1CameraClientD1Ev
    _ZNK15AV1CameraClient6Status7messageEv
)

usage() {
    sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"
}
refuse() {
    echo "refused: $*" >&2
    exit 1
}

mode=""
sdk_dir=""
check_only=0
image_id=""
out_dir=""
while [ $# -gt 0 ]; do
    case "$1" in
        --fake) mode=fake ;;
        --sdk) mode=sdk; sdk_dir="${2:?--sdk needs a directory}"; shift ;;
        --check-sdk) check_only=1 ;;
        --image-id) image_id="${2:?--image-id needs a value}"; shift ;;
        --out) out_dir="${2:?--out needs a directory}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 64 ;;
    esac
    shift
done
if [ -z "$mode" ]; then
    usage >&2
    exit 64
fi
if [ "$(uname -s)" != Linux ]; then
    echo "The camera helper builds on Linux only." >&2
    exit 1
fi
for tool in python3 sha256sum nm realpath cmp "$sightcircuit_compiler"; do
    command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

# The checkout's identity, recorded in --describe and build-report.json.
build_commit=""
build_dirty=""
build_commit_reason=""
if command -v git >/dev/null \
    && build_commit="$(git -c "safe.directory=$sightcircuit_root" -C "$sightcircuit_root" rev-parse HEAD 2>/dev/null)" \
    && [[ "$build_commit" =~ ^[0-9a-f]{40}$ ]]; then
    if [ -n "$(git -c "safe.directory=$sightcircuit_root" -C "$sightcircuit_root" status --porcelain 2>/dev/null)" ]; then
        build_dirty=1
    else
        build_dirty=0
    fi
else
    build_commit=""
    build_commit_reason="git_unavailable"
fi
common_defines=()
if [ -n "$build_commit" ]; then
    common_defines+=("-DSIGHTCIRCUIT_BUILD_COMMIT=\"$build_commit\"" "-DSIGHTCIRCUIT_BUILD_DIRTY=$build_dirty")
else
    common_defines+=("-DSIGHTCIRCUIT_BUILD_COMMIT_REASON=\"$build_commit_reason\"")
fi

# Symbols naming AV1CameraClient that an object leaves undefined.
sdk_symbols() {
    nm -u --format=just-symbols -- "$1" | grep 15AV1CameraClient || true
}

if [ "$mode" = fake ]; then
    fake_out="${out_dir:-$sightcircuit_root/artifacts/camera-helper-fake}"
    mkdir -p -- "$fake_out"
    fake_binary="$fake_out/sightcircuit-camera-helper-fake"
    trap 'rm -f -- "$fake_binary.tmp"' EXIT
    "$sightcircuit_compiler" "${sightcircuit_flags[@]}" -DSIGHTCIRCUIT_CAMERA_BACKEND_FAKE=1 "${common_defines[@]}" \
        -I"$sightcircuit_tools" \
        "$sightcircuit_tools/camera_helper.cpp" "$sightcircuit_tools/camera_backend_fake.cpp" \
        -o "$fake_binary.tmp"
    if [ -n "$(sdk_symbols "$fake_binary.tmp")" ]; then
        refuse "the fake build references the Client Library"
    fi
    mv -- "$fake_binary.tmp" "$fake_binary"
    echo "Built the fake-backend camera helper in $fake_out. No camera was touched and the Client Library was not used."
    exit 0
fi

# ---- --sdk DIR: verify, then build on aarch64 ----------------------------------

sdk_real="$(realpath -e -- "$sdk_dir" 2>/dev/null)" || refuse "SDK directory not found: $sdk_dir"
case "$sdk_real" in
    "$sightcircuit_root"|"$sightcircuit_root"/*)
        refuse "the SDK directory must lie outside the work tree ($sightcircuit_root); keep the SDK where Arcturus delivered it" ;;
esac
release="$sdk_real/release.json"
[ -f "$release" ] || refuse "$release is missing; the SDK directory must hold the release manifest"
sdk_inputs=(AV1CameraClient.h AV1CameraIPCProtocol.h AV1CameraSDK-LICENSE.md lib/libAV1CameraClient.a)
for name in "${sdk_inputs[@]}"; do
    [ -f "$sdk_real/$name" ] || refuse "$sdk_real/$name is missing"
done

# The manifest's SHA-256 for each input, each validated as 64 hex digits before use.
expected_hashes="$(python3 -I - "$release" "${sdk_inputs[@]}" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
files = manifest.get("files") if isinstance(manifest, dict) else None
if not isinstance(files, dict):
    sys.exit("release.json has no files table")
for name in sys.argv[2:]:
    digest = files.get(name)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        sys.exit(f"release.json has no SHA-256 for {name}")
    print(name, digest)
PY
)" || refuse "release.json is not a usable manifest"
declare -A input_hash=()
while read -r name digest; do
    actual="$(sha256sum -- "$sdk_real/$name" | cut -d' ' -f1)"
    [ "$actual" = "$digest" ] || refuse "$name does not match the SHA-256 in release.json"
    input_hash["$name"]="$actual"
done <<< "$expected_hashes"
for name in AV1CameraClient.h AV1CameraIPCProtocol.h; do
    cmp -s -- "$sdk_real/$name" "$sightcircuit_vendored/$name" \
        || refuse "$name differs from the vendored copy in third_party/arcturus-camera-sdk; the release the repository pins is the only one this build knows"
done

# Only now, with every hash verified, read the release identity.
release_fields="$(python3 -I - "$release" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
print(manifest.get("source_revision", ""))
print(manifest.get("agreement_version", ""))
PY
)"
source_revision="$(sed -n 1p <<< "$release_fields")"
agreement_version="$(sed -n 2p <<< "$release_fields")"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || refuse "release.json source_revision is not a 40-digit hex revision"
[[ "$agreement_version" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || refuse "release.json agreement_version is not a YYYY-MM-DD date"
echo "SDK verified: source_revision $source_revision, agreement_version $agreement_version; headers match the vendored copies."
if [ "$check_only" = 1 ]; then
    echo "--check-sdk: stopping before any compile. Nothing was built."
    exit 0
fi
[ "$(uname -m)" = aarch64 ] \
    || refuse "the Client Library is Linux ARM64 only; build on aarch64 (this machine is $(uname -m)). --check-sdk verifies an SDK directory anywhere."

sightcircuit_build="$sightcircuit_root/artifacts/camera-helper"
mkdir -p -- "$sightcircuit_build"
helper_binary="$sightcircuit_build/sightcircuit-camera-helper"
trap 'rm -f -- "$helper_binary.tmp" "$sightcircuit_build/camera_helper.o" "$sightcircuit_build/camera_backend_sdk.o"' EXIT
sdk_defines=("-DSIGHTCIRCUIT_SDK_SOURCE_REVISION=\"$source_revision\"" "-DSIGHTCIRCUIT_SDK_AGREEMENT_VERSION=\"$agreement_version\"")
# Each unit on its own (no LTO), so the object check below sees what each one references.
# Only the SDK unit sees the vendored headers; DIR is never an include path.
"$sightcircuit_compiler" "${sightcircuit_flags[@]}" "${common_defines[@]}" "${sdk_defines[@]}" \
    -I"$sightcircuit_tools" -c "$sightcircuit_tools/camera_helper.cpp" -o "$sightcircuit_build/camera_helper.o"
"$sightcircuit_compiler" "${sightcircuit_flags[@]}" "${common_defines[@]}" "${sdk_defines[@]}" \
    -I"$sightcircuit_tools" -I"$sightcircuit_vendored" -c "$sightcircuit_tools/camera_backend_sdk.cpp" \
    -o "$sightcircuit_build/camera_backend_sdk.o"

# Object check, before linking: Client Library symbols come only from the SDK
# unit and only from the allow-list. A reference to setControls fails the build.
if [ -n "$(sdk_symbols "$sightcircuit_build/camera_helper.o")" ]; then
    refuse "camera_helper.o references the Client Library; only camera_backend_sdk.cpp may"
fi
object_symbols="$(sdk_symbols "$sightcircuit_build/camera_backend_sdk.o")"
[ -n "$object_symbols" ] || refuse "camera_backend_sdk.o references no Client Library symbol; the build is not what it claims"
while read -r symbol; do
    allowed=0
    for candidate in "${sightcircuit_allowed_symbols[@]}"; do
        [ "$symbol" = "$candidate" ] && allowed=1
    done
    [ "$allowed" = 1 ] || refuse "camera_backend_sdk.o references $symbol, which is not on the allow-list"
done <<< "$object_symbols"

"$sightcircuit_compiler" -O2 -pthread "$sightcircuit_build/camera_helper.o" "$sightcircuit_build/camera_backend_sdk.o" \
    "$sdk_real/lib/libAV1CameraClient.a" -o "$helper_binary.tmp"
mv -- "$helper_binary.tmp" "$helper_binary"

compiler_version="$("$sightcircuit_compiler" --version | head -n 1)"
glibc_version="$(getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -n 1 || echo unknown)"
python3 -I - "$sightcircuit_build/build-report.json" "$source_revision" "$agreement_version" "$compiler_version" \
    "$glibc_version" "$build_commit" "$build_dirty" "$build_commit_reason" "$image_id" "$(uname -m)" \
    "${input_hash[AV1CameraClient.h]}" "${input_hash[AV1CameraIPCProtocol.h]}" \
    "${input_hash[AV1CameraSDK-LICENSE.md]}" "${input_hash[lib/libAV1CameraClient.a]}" <<'PY'
import json
import sys

(path, source_revision, agreement_version, compiler, glibc, commit, dirty, reason, image_id, machine,
 client_h, protocol_h, licence, archive) = sys.argv[1:]
report = {
    "report_kind": "camera_helper_build_report",
    "schema_version": 1,
    "status": "built_sdk_helper",
    "binary": "sightcircuit-camera-helper",
    "backend": "sdk",
    "machine": machine,
    "sdk": {"source_revision": source_revision, "agreement_version": agreement_version},
    "input_sha256": {"AV1CameraClient.h": client_h, "AV1CameraIPCProtocol.h": protocol_h,
                     "AV1CameraSDK-LICENSE.md": licence, "lib/libAV1CameraClient.a": archive},
    "compiler": compiler,
    "glibc": glibc,
    "build_commit": commit or None,
    "build_dirty": None if dirty == "" else dirty == "1",
    "build_commit_reason": reason or None,
    "container_image_id": image_id or None,
    "client_library_copied": False,
    "pixels_read": False,
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
echo "Built $helper_binary (ignored artifacts/). Report: $sightcircuit_build/build-report.json."
echo "Reminder (agreement section 2): each developer obtains their own SDK from Arcturus. This build fetched nothing and copied no SDK file into the checkout; never commit, upload or share the linked binary or the Client Library."
