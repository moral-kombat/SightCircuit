# Third-party sources

The repository's MIT license covers original Framejacker code and documentation.
Dependencies, reused code and downloaded fixtures retain their upstream terms.
No model weights, images or SDK binaries are bundled. The protocol header and
adaptation patches identified below are the bundled capture-project code.

## Development fixtures

The setup script downloads these files into the ignored
`.cache/framejacker/assets/` directory. Exact immutable URLs and SHA-256 checksums
are recorded in [scripts/smoke-assets.json](scripts/smoke-assets.json).

| Files | Source | Use |
| --- | --- | --- |
| `MobileNetSSD_deploy.prototxt`, `MobileNetSSD_deploy.caffemodel` | [chuanqi305/MobileNet-SSD](https://github.com/chuanqi305/MobileNet-SSD/tree/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a), [upstream license](https://github.com/chuanqi305/MobileNet-SSD/blob/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a/LICENSE) | OpenCV CPU detector fixture |
| `dog.jpg` | [pjreddie/darknet sample data](https://github.com/pjreddie/darknet/blob/f6afaabcdf85f77e7aff2ec55c020c0e297c77f9/data/dog.jpg), [upstream license](https://github.com/pjreddie/darknet/blob/f6afaabcdf85f77e7aff2ec55c020c0e297c77f9/LICENSE) | Repeated sample-image frames for the smoke check |

The license links identify the upstream repositories' notices; they do not
relicense individual assets as original Framejacker material. Generated sample
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
with NumPy, OpenCV headless, PySide6 Essentials and Shiboken. The annotated
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
[provenance](third_party/mrhand/README.md) are included. Framejacker uses the
luma shared-memory layout. The adaptation patches below include upstream
source context; no complete upstream capture layer, `ptgrab`, installer, driver
or hand-tracking model is bundled or activated. The controlled producer is
original test code, not a camera source.

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
socket fixture are original Framejacker code. This mode also installs nothing
and does not run the capture binaries.

[patches/mrhand/0002-owned-bounded-publication.patch](patches/mrhand/0002-owned-bounded-publication.patch)
applies after the transport patch at the same pin and retains the same MIT
attribution. The optional `--publication-fixes` build adds original Framejacker
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

If compiler-search-path Vulkan headers are unavailable, either builder fetches
[Khronos Vulkan-Headers v1.4.309](https://github.com/KhronosGroup/Vulkan-Headers/tree/952f776f6573aafbb62ea717d871cd1d6816c387)
at `952f776f6573aafbb62ea717d871cd1d6816c387` into ignored
`.cache/vulkan-headers`. Its [license notice](https://github.com/KhronosGroup/Vulkan-Headers/blob/952f776f6573aafbb62ea717d871cd1d6816c387/LICENSE.md)
and `LICENSES` texts are retained; per-file Apache-2.0/MIT notices apply.
No Vulkan loader/driver binary is downloaded. The existing system loader is
linked for the compile-only capture compatibility check; the query binary links
`libdl` and resolves Vulkan functions only when requested.

## Research references

The [camera guide](docs/steam-frame-camera.md) links Valve's OpenVR SDK, Arcturus's
Spirula fork and community capture/reconstruction projects. Those links provide
research context. Apart from the protocol header and patches above, their code
and binaries have not been vendored. Consult their licenses and preserve attribution when
integrating any implementation.
