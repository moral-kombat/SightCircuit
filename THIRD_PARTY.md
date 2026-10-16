# Third-party sources

The repository's MIT license covers original Framejacker code and documentation.
Dependencies and downloaded fixtures retain their upstream terms. No upstream
model weights, images, SDK binaries or source trees are bundled in this commit.

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

## Research references

The [camera guide](docs/steam-frame-camera.md) links Valve's OpenVR SDK, Arcturus's
Spirula fork and community capture/reconstruction projects. Those links provide
research context; their code and binaries have not been vendored. Consult their
licenses and preserve attribution when integrating any implementation.
