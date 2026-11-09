# Third-party sources

The repository's MIT license covers original SightCircuit code and documentation.
Dependencies, reused code and downloaded fixtures retain their upstream terms.
No model weights, images or SDK binaries are bundled. The protocol header and
adaptation patches identified below are the bundled capture-project code. The
two public headers of the Arcturus Vision Camera Preview SDK and its license
agreement are bundled under that agreement's header terms; its Client Library
is not.

## Development fixtures

The setup script downloads these files into the ignored
`.cache/sightcircuit/assets/` directory. Exact immutable URLs and SHA-256 checksums
are recorded in [scripts/smoke-assets.json](scripts/smoke-assets.json).

| Files | Source | Use |
| --- | --- | --- |
| `MobileNetSSD_deploy.prototxt`, `MobileNetSSD_deploy.caffemodel` | [chuanqi305/MobileNet-SSD](https://github.com/chuanqi305/MobileNet-SSD/tree/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a), [upstream license](https://github.com/chuanqi305/MobileNet-SSD/blob/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a/LICENSE) | OpenCV CPU detector fixture |
| `dog.jpg` | [pjreddie/darknet sample data](https://github.com/pjreddie/darknet/blob/f6afaabcdf85f77e7aff2ec55c020c0e297c77f9/data/dog.jpg), [upstream license](https://github.com/pjreddie/darknet/blob/f6afaabcdf85f77e7aff2ec55c020c0e297c77f9/LICENSE) | Repeated sample-image frames for the smoke check |
| `yolox_nano.onnx` | [Megvii-BaseDetection/YOLOX release 0.1.1rc0](https://github.com/Megvii-BaseDetection/YOLOX/releases/tag/0.1.1rc0), [Apache-2.0 license](https://github.com/Megvii-BaseDetection/YOLOX/blob/0.1.1rc0/LICENSE) | Candidate 80-class COCO detector; trained on COCO, whose annotations are CC BY 4.0 |

The [seed evaluation manifest](evaluation/seed.json) lists six further
[darknet sample images](https://github.com/pjreddie/darknet/tree/f6afaabcdf85f77e7aff2ec55c020c0e297c77f9/data)
by immutable URL and checksum; `scripts/evaluate_detectors.py --fetch` downloads
them into ignored `.cache/sightcircuit/evaluation/`. Their labels are original
SightCircuit annotations.

The license links identify the upstream repositories' notices; they do not
relicense individual assets as original SightCircuit material. Generated sample
videos and screenshots are also excluded from Git.

ONNX Runtime's installed package supplies the small `datasets/mul_1.onnx`
arithmetic example used by the numerical inference check. That file remains in
the installed package and is not copied into the repository.

## Python dependencies

[requirements-dev.txt](requirements-dev.txt) records exact tested versions of
OpenCV headless, NumPy, ONNX Runtime, PySide6 Essentials and their supporting
packages. Refer to each installed package's upstream license and notices.

[requirements-replay.txt](requirements-replay.txt) separately pins
[PyAV 16.1.0](https://github.com/PyAV-Org/PyAV/tree/v16.1.0), distributed under
its [BSD 3-Clause license](https://github.com/PyAV-Org/PyAV/blob/v16.1.0/LICENSE.txt).
PyAV binary wheels also include FFmpeg libraries and their dependencies; consult
the installed wheel's notices and upstream licenses for those components. They
are installed in ignored `.venv-replay/`, not committed to this repository.

[requirements-window.txt](requirements-window.txt) extends that environment
with NumPy, OpenCV headless, PySide6 Essentials, Shiboken and
[ONNX Runtime](https://github.com/microsoft/onnxruntime) (MIT) with its supporting packages. The annotated
replay prototype reuses the checksum-verified MobileNet-SSD files listed above
as a demo detector; their upstream notices continue to apply.

## Native OpenVR tools

The native camera probe and overlay bridge build scripts fetch Valve's OpenVR
SDK at commit `0924064316de3effbcd1acf1e309182a2deb1c05` into ignored
`.cache/openvr` and build its portable loader from source. The SDK's
[BSD 3-Clause license](https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/LICENSE)
and included notices apply to that loader. SDK sources and the compiled tool
binaries remain outside the committed files.

The overlay bridge also uses the SDK's bundled JsonCpp implementation; its
public-domain/MIT notices remain in the cached `src/jsoncpp.cpp` source. No SDK
or JsonCpp sources are copied into the project tree outside that ignored cache.

## MRHAND protocol header

[third_party/mrhand/handcam_proto.h](third_party/mrhand/handcam_proto.h) is copied
unmodified from `spyjacob/steam-frame-hand-tracking` at commit
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. Its
[upstream MIT license](third_party/mrhand/LICENSE) and
[provenance](third_party/mrhand/README.md) are included. SightCircuit uses the
luma shared-memory layout. The adaptation patches below include upstream
source context/deletions; no upstream capture binary, installer, driver
or hand-tracking model is bundled or activated. The controlled producer is
original test code, not a camera source.

## Arcturus Vision Camera Preview SDK headers

[third_party/arcturus-camera-sdk/AV1CameraClient.h](third_party/arcturus-camera-sdk/AV1CameraClient.h)
and [AV1CameraIPCProtocol.h](third_party/arcturus-camera-sdk/AV1CameraIPCProtocol.h)
are copied unmodified from the Arcturus Vision Camera Preview SDK release at
source revision `536217ebbc0456df5db53658409b048cdd272785`, which the project
owner obtained directly from Arcturus after accepting the
[developer license agreement](third_party/arcturus-camera-sdk/AV1CameraSDK-LICENSE.md)
(version 2026-11-08). Section 2 of that agreement permits redistributing the
public headers with the agreement and their copyright notices preserved, so the
agreement is included beside them; it, not this repository's MIT license,
governs these files. Checksums and [provenance](third_party/arcturus-camera-sdk/README.md)
are recorded with the files.

The SDK's Client Library, `libAV1CameraClient.a`, is not bundled, cached or
fetched by this repository or CI; anyone building against it obtains their own
copy from Arcturus. The SDK's README, release manifest, CMake module, Docker
build files and examples are not copied. The one SightCircuit source that
includes the headers is `tools/camera_helper_sdk.cpp`, the SDK translation unit
of the [camera helper](docs/reference/camera-helper.md); CI syntax-checks it
against the vendored headers and never links it, and `scripts/build_camera_helper.sh`
links the SDK-backed helper only against a Client Library the operator names
locally. Any camera feature built on the SDK is subject to section 3 of the
agreement.

## Capture reference build dependencies

`scripts/build_capture_reference.sh` fetches the same MIT-licensed capture project
at `ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082` into ignored
`.cache/capture-reference`, retaining its root license. It compiles unmodified
`handcam_layer.cpp` and `ptgrab.cpp` as non-executable inspection artifacts.
It does not run upstream scripts, install a layer or start a producer. These
sources and binaries are not vendored into tracked files; runtime fixes remain
required before deployment.

[patches/mrhand/0001-bounded-descriptor-transport.patch](patches/mrhand/0001-bounded-descriptor-transport.patch)
contains modifications/context from that MIT-licensed capture source, with
[provenance](patches/mrhand/README.md) and the retained upstream notice above.
The builder's optional `--transport-fixes` mode applies it to a disposable copy
and compiles separate inspection artifacts. The transport helper and synthetic
socket fixture are original SightCircuit code. This mode also installs nothing
and does not run the capture binaries.

[patches/mrhand/0002-owned-bounded-publication.patch](patches/mrhand/0002-owned-bounded-publication.patch)
applies after the transport patch at the same pin and retains the same MIT
attribution. The optional `--publication-fixes` build adds original SightCircuit
publication/control helpers and a generated-data fixture. Capture binaries remain
non-executable inspection artifacts; tests run only the original synthetic fixture
and the existing reader. No upstream installer or camera operation is executed.

[patches/mrhand/0003-validated-image-import.patch](patches/mrhand/0003-validated-image-import.patch)
applies after both earlier patches at the same pin and retains the same upstream
MIT attribution. The optional `--import-fixes` mode adds original import/ownership
helpers and a fake Vulkan dispatch fixture. Real capture binaries remain
non-executable; only generated-data fixture code runs in its tests. The
[provenance](patches/mrhand/README.md) and upstream notice above continue to apply.

The standalone `scripts/build_capture_gpu_check.sh` builds original physical-device
query code. It uses the same header cache and loads the installed Vulkan loader
only through the query tool's explicit opt-in; it fetches no camera capture code.
The synthetic dispatch library in its tests is original test code.

[patches/mrhand/0004-owned-gpu-resources.patch](patches/mrhand/0004-owned-gpu-resources.patch)
applies after patches 0001–0003 at the same capture pin, with the same retained
MIT source/context attribution. The optional `--resource-fixes` mode adds
original GPU ownership helpers and a fake-dispatch fixture. Capture binaries
remain non-executable inspection artifacts. No upstream installer or real
Vulkan/camera operation is executed by this build/test procedure.

[patches/mrhand/0005-provider-lifetime-and-provenance.patch](patches/mrhand/0005-provider-lifetime-and-provenance.patch)
applies after patches 0001–0004 at the same pin and retains upstream MIT
attribution, including the removed layer implementation in its diff. The
`--provider-fixes` mode adds original SightCircuit provider/protocol, layer
dispatch/lifetime, opt-in guard and synthetic fixtures. The worker supervisor,
exec boundary and configuration/recovery tools are original SightCircuit code.
Tests run fake downstream Vulkan dispatch and generated local peers only; no
upstream installer or real layer/camera operation is executed.

[patches/mrhand/0006-block-unleased-camera-reads.patch](patches/mrhand/0006-block-unleased-camera-reads.patch)
applies after patches 0001–0005 and retains MIT attribution for removed
upstream/adapted reader text. The current `--provider-fixes` mode applies it
to block raw camera access before runtime initialization. Its v3 metadata-only
provider and standalone inspector are original SightCircuit code. The inspector
uses Vulkan header types for metadata validation without linking/loading the
Vulkan loader or requesting image descriptors.

If compiler-search-path Vulkan headers are unavailable, either builder fetches
[Khronos Vulkan-Headers v1.4.309](https://github.com/KhronosGroup/Vulkan-Headers/tree/952f776f6573aafbb62ea717d871cd1d6816c387)
at `952f776f6573aafbb62ea717d871cd1d6816c387` into ignored
`.cache/vulkan-headers`. Its [license notice](https://github.com/KhronosGroup/Vulkan-Headers/blob/952f776f6573aafbb62ea717d871cd1d6816c387/LICENSE.md)
and `LICENSES` texts are retained; per-file Apache-2.0/MIT notices apply.
No Vulkan loader/driver binary is downloaded. The existing system loader is
linked for the compile-only capture compatibility check; the query binary links
`libdl` and resolves Vulkan functions only when requested.

## Adapted MIT, BSD and Apache sources

Each row names the pinned commit, the licence and what was taken. Notices are
kept in the files that carry the adapted code. AGPL and GPL sources are cited
in the docs as facts only; nothing from them is copied.

### Overlay bridge lifecycle

| Source | Licence | Taken | Changed |
| --- | --- | --- | --- |
| [JakeGreen2145/frame-testbench `native/observe.cpp`](https://github.com/JakeGreen2145/frame-testbench/blob/ea7f5109c05e56974f3ccc2e0b4af8fbf232579b/native/observe.cpp#L522-L586) at `ea7f5109c05e56974f3ccc2e0b4af8fbf232579b`, L22-L28 and L522-L586 | [MIT](https://github.com/JakeGreen2145/frame-testbench/blob/ea7f5109c05e56974f3ccc2e0b4af8fbf232579b/LICENSE), Copyright (c) 2026 Donn Green | The pattern of a saved stdout duplicate with fd 1 pointed at stderr, and a SIGALRM handler that only calls `write` and `_exit` | Rewritten in [tools/overlay_lifecycle.hpp](tools/overlay_lifecycle.hpp), which carries the notice: a caller-built JSON message swapped in with SIGALRM blocked, a torn line terminated first, per-phase and per-step re-arming, exit code 124 |

The connect order (Background probe, then Overlay) is an idea taken from
[coah80/framecorder `src/openvr.rs` L11-L21](https://github.com/coah80/framecorder/blob/130840c871d3fa740fcf494f96ba046b29d76cf4/src/openvr.rs#L11-L21)
([MIT](https://github.com/coah80/framecorder/blob/130840c871d3fa740fcf494f96ba046b29d76cf4/LICENSE))
and [Sippbox/frame-passthrough-pip `src/app/main.cpp` L94-L110](https://github.com/Sippbox/frame-passthrough-pip/blob/babae0b9d8f1a0955207bcca3d91c4db6e2474b2/src/app/main.cpp#L94-L110)
([MIT](https://github.com/Sippbox/frame-passthrough-pip/blob/babae0b9d8f1a0955207bcca3d91c4db6e2474b2/LICENSE)).
No code was copied from either.

### Run-context sensor sampler

| Source | Licence | Taken | Changed |
| --- | --- | --- | --- |
| [JonahSimon/steam-frame-harness `frame/frame-sensors.py`](https://github.com/JonahSimon/steam-frame-harness/blob/6c16bf39780acbda28dfac41269b8fd82d97b27d/frame/frame-sensors.py) at `6c16bf39780acbda28dfac41269b8fd82d97b27d` | [MIT](https://github.com/JonahSimon/steam-frame-harness/blob/6c16bf39780acbda28dfac41269b8fd82d97b27d/LICENSE), Copyright (c) 2026 Jonah Paul Simon | Sensor discovery and sampling: thermal zones, cooling states, `max34417` power rails, cpufreq policies, GPU devfreq, battery, charger, PMIC VADC skin temperatures, `/proc/stat` load and `MemAvailable` | Ported to [sightcircuit/runtime/sensors.py](sightcircuit/runtime/sensors.py), which carries the notice: blank cells instead of -999 (and instead of 0 for a missing throttle state), Wi-Fi columns and `iw` dropped, one clock reading per row as UTC with offset and monotonic, a `root` for fake trees, bounded rows |
| [sasaken1102r/frame-perf-overlay `headset/sensors.cpp`](https://github.com/sasaken1102r/frame-perf-overlay/blob/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94/headset/sensors.cpp) at `066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94` | [MIT](https://github.com/sasaken1102r/frame-perf-overlay/blob/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94/LICENSE), Copyright (c) 2026 sasaken1102r and contributors | The Frame sysfs paths, through steam-frame-harness, which names it as their source | No code from this file was copied directly; credited because the paths originate there |

### HUD geometric prediction

| Source | Licence | Taken | Changed |
| --- | --- | --- | --- |
| [DeeJanuz/vr-recorder `vr-mask.cpp`](https://github.com/DeeJanuz/vr-recorder/blob/78ab97e0700b6668978722111770b4657ec2ec92/vr-mask.cpp#L917-L977) at `78ab97e0700b6668978722111770b4657ec2ec92`, L917-L977 | [MIT](https://github.com/DeeJanuz/vr-recorder/blob/78ab97e0700b6668978722111770b4657ec2ec92/LICENSE), Copyright (c) 2026 DeeJanuz (the file has no header, so the LICENSE text is carried) | `Project()` (Sutherland-Hodgman clip against a near plane and widened side planes, then pinhole projection) and `Hull()` (monotone-chain convex hull) | Ported to Python in [sightcircuit/hud/geometry.py](sightcircuit/hud/geometry.py), which carries the LICENSE text: the pinhole uses the four `GetProjectionRaw` tangents, points arrive in eye space, and the upstream clamp at four times the larger frame dimension (L949-L952) is replaced by a check that raises |

The bridge's read-only `--headset-view-record` mode follows the list of
`IVRHeadsetView` and `IVRSystem` getters read in the same file at L1381-L1393;
that code was not copied.

## Research references

The offline [lifecycle review](docs/lifecycle-review.md) examines
[frame-perf-overlay](https://github.com/sasaken1102r/frame-perf-overlay/tree/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94)
at `066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94` (MIT):
`headset/vr_overlay.cpp` and `headset/vk_texture.cpp`. That is attributed source
review of cleanup ordering and measurement patterns; nothing from those two
files was incorporated. Its `headset/sensors.cpp` sysfs paths are used, by way
of steam-frame-harness, in the [run-context sensor sampler](#run-context-sensor-sampler)
above. No upload-performance or device-compatibility claim is made.

The [camera guide](docs/steam-frame-camera.md) links Valve's OpenVR SDK, Arcturus's
Spirula fork and community capture/reconstruction projects. Those links provide
research context. Apart from the protocol header, the Arcturus SDK headers and
the patches above, their code and binaries have not been vendored. Consult their licenses and preserve attribution when
integrating any implementation.

The 2026-11-04 documentation pass cites further sources as third-party claims,
kept apart from SightCircuit evidence; no code or text was copied from them:
danthedaniel/roommate-radar `docs/arcturus-av1.md` at
`d6e8a8cbdb6c1197514e382662951231fd641941` (AGPL-3.0; facts restated in our own
words in the [camera guide](docs/steam-frame-camera.md#third-party-camera-claims-2026-11-04)),
coah80/framecorder `AGENTS.md` on `dev` at
`0c624afb008e0f2e1e467eacae9a9206480943ba` (MIT; advice paraphrased in
[on-device](docs/on-device.md#preflight-advice-from-another-frame-project-upstream-not-our-evidence)),
Frametop/frametop `hands/README.md` and `screens/keyboard.cpp` at
`adebd7d6cfc938477bf8e42a8bcb1c41a32a6f9c` (MIT; a format claim and a fallback
reference), Brobert-in-aus/cuff `docs/INTERNALS.md` at
`4b7f54a8cd240f6118dd3c35e41ea0ac22afc787` and nicolas-riera/Quest1-SteamOS
`docs/passthrough.md` at `4e2a7decc8434acb6cec4e6ba2d089c336faedad` (cited
reports only), and the public issues named in
[live capture](docs/live-capture.md) and [architecture](docs/architecture.md).
