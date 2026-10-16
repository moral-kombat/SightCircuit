# Framejacker

Framejacker is an early development project for a Linux app that recognizes
objects from the **Arcturus Vision Camera** on **Steam Frame** and displays the
results in a window, with a VR overlay as the eventual target.

This repository currently contains camera-interface research, a proposed
architecture, and reproducible development checks. Live camera capture and the
application itself have not been implemented.

## Project status

| Capability | Status |
| --- | --- |
| Linux development setup | Tested on x86_64 with Python 3.12 |
| Video decoding, CPU detection and offscreen window rendering | Verified with the included smoke check |
| Arcturus app live preview and recording | Confirmed working by the user on Steam Frame |
| Native public OpenVR camera probe | No frames on the tested headset; interrupts passthrough; explicit opt-in required |
| Live camera access from Framejacker | Unimplemented; a usable capture interface is still needed |
| Interactive camera window and VR overlay | Planned |
| Stereo depth and labels anchored in the room | Planned; requires calibration, depth and pose alignment |
| Gaussian splat reconstruction | Optional future experiment using recorded data and a GPU computer |

Valve documents a general OpenVR camera API. Our user-supplied headset reports
show no frames through that API, and the user reports passthrough going black
while the probe runs. The vendor app's preview and recording work. Do not repeat
the probe on the unchanged tested runtime. Community Steam Frame testing also
reports failed public camera capture. A
community implementation instead captures passthrough buffers through an
experimental Vulkan layer. Read the [camera integration guide](docs/steam-frame-camera.md)
before choosing a capture backend; standard USB webcam access is not established.

## Quick start

Use a Linux x86_64 development machine with Bash and
[uv](https://docs.astral.sh/uv/getting-started/installation/) installed. The
setup script creates a dedicated Python 3.12 environment; uv can obtain Python
if it is missing. Internet access to PyPI and GitHub is needed on first setup.

```bash
git clone https://github.com/moral-kombat/framejacker.git
cd framejacker
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

Use the existing checkout if you already have one. The smoke check uses
offscreen Qt rendering by default, so it needs no camera, GPU, SteamVR or display
server. It exits when validation finishes; it is not an interactive application.

The check:

1. Encodes and decodes 12 repeated sample-image frames as MJPEG.
2. Runs MobileNet-SSD through OpenCV DNN on CPU and requires bicycle, car and dog
   detections on every frame.
3. Renders the annotated frames in a Qt window and saves a screenshot.
4. Separately executes a bundled ONNX arithmetic model and verifies its output.

Results appear in `artifacts/smoke/readiness.json` and
`artifacts/smoke/detection-window.png`. The sample is not an Arcturus recording;
these checks do not establish live capture, real-time performance or headset
compatibility.

Dependencies are pinned in [requirements-dev.txt](requirements-dev.txt).
Downloaded model/image fixtures have immutable source URLs and SHA-256 checksums
in [scripts/smoke-assets.json](scripts/smoke-assets.json). Environments, downloads
and generated results are excluded from Git. See [development setup](docs/development.md)
for details and troubleshooting.

## Design and next steps

Continuing from a web terminal or development session on the headset? Start with
the [on-device handoff and inventory](docs/on-device.md) before installing the
desktop development dependencies there.

The intended live path is:

```text
camera or replay input → preprocessing → detector → annotated window → VR overlay
```

Capture, inference and presentation will be separate components. Use a short
recording from the working Arcturus app to build a replay-backed application,
then connect a verified live capture backend. World-space labels come after
depth and pose validation.

Gaussian splats could provide a persistent visual map of a mostly static room.
Reconstruction remains a separate recorded-data workflow; object recognition
still comes from the live detector.

- [Architecture and implementation milestones](docs/architecture.md)
- [Steam Frame and Arcturus camera interfaces and sources](docs/steam-frame-camera.md)
- [Development commands and validation limits](docs/development.md)
- [Contributing](CONTRIBUTING.md)

## License

Original project code and documentation are covered by the [MIT license](LICENSE).
External packages and downloaded fixtures retain their upstream terms; see
[third-party sources](THIRD_PARTY.md). Framejacker is an independent project and
is not affiliated with Valve or Arcturus Vision.
