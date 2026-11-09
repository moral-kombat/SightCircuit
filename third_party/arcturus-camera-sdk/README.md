# Arcturus Vision Camera Preview SDK headers

`AV1CameraClient.h` and `AV1CameraIPCProtocol.h` are the two public headers of
the Arcturus Vision Camera Preview SDK, copied without modification.
[`AV1CameraSDK-LICENSE.md`](AV1CameraSDK-LICENSE.md) is the SDK's developer
license agreement, version 2026-11-08. It is included because section 2 of
the agreement permits redistributing the public headers only with the agreement
and the headers' copyright notices preserved. The agreement, not this
repository's MIT license, governs these three files.

## Provenance

The project owner accepted the agreement and obtained the SDK directly from
Arcturus (confirmed 2026-11-09). The copied files come from the release with
source revision `536217ebbc0456df5db53658409b048cdd272785` and agreement version
`2026-11-08`. Their SHA-256 checksums match that release's manifest:

| File | SHA-256 |
| --- | --- |
| `AV1CameraClient.h` | `00c92c19810068c436e96cb1df7ee09327c0f60493cb050a008f71a305b5f6f6` |
| `AV1CameraIPCProtocol.h` | `abdc957cc9dfcd59cbab124e1b8e33d82a79e6415662e0bc3f62d83c145c8f64` |
| `AV1CameraSDK-LICENSE.md` | `317c0bd59f40da6fb42bccd96887fb684e26e412772445f5422063435d5aa142` |

`tests/test_arcturus_sdk_headers.py` pins these checksums. Replace the files
only with a newer release obtained the same way, and update this table, the
test and [THIRD_PARTY.md](../../THIRD_PARTY.md#arcturus-vision-camera-preview-sdk-headers)
together.

## What is not here

- **The Client Library.** `libAV1CameraClient.a` is never committed, cached or
  fetched by this repository or its CI, in any form. Anyone building against it
  obtains their own copy from Arcturus by accepting the agreement. `.gitignore`
  excludes the file name at any depth.
- **Other SDK materials.** The SDK's README, release manifest, CMake module,
  Docker build files and examples are not copied here.

## Status

Two files include these headers: `tools/camera_backend_sdk.cpp`, the camera
helper's SDK translation unit, and `tools/camera_status_map.hpp`, its status
mapping (`tests/test_camera_helper_boundaries.py` keeps that list closed).
`scripts/build_camera_helper.sh --sdk DIR` links a locally supplied Client
Library on aarch64 only, into ignored `artifacts/`, after verifying `DIR`
against its release manifest and these copies; CI builds only the fake backend
(`--fake`). The helper reads metadata only: nothing reads pixels, enables camera
access by default or changes a gate. The live image input gate in
[`sightcircuit/gates.py`](../../sightcircuit/gates.py) stays closed. Any camera
feature built on this SDK is also subject to section 3 of the agreement. That
section covers informed camera use, explicit consent before saving or sending
Camera Data (which the agreement defines to include detections), stopping image
processing when the headset is removed, and not using private or internal
camera APIs. See the [current work order](../../docs/live-capture.md#current-work-order)
for integration status: the SDK camera-input lane is open cloud-only, and each
device probe needs its own approval from the owner.
