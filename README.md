# Framejacker

Framejacker is an early Linux object-recognition app for Steam Frame. It
processes Arcturus recordings and, in a bounded diagnostic, SteamVR mirror
frames. Recorded video appears in a Qt window and an optional head-relative
VR panel. Bounded mirror capture/inference works on Frame; continuous live
display is planned.

Start with the [documentation index](docs/README.md) for task-based navigation.

## Project status

| Area | Current summary |
| --- | --- |
| Recorded-video decoding, replay and VR panel | Confirmed working by the user on Frame, including controls and head-relative placement |
| Live input in the recognition application | Bounded mirror recognition confirmed at 4.993 processed fps with normal passthrough; the mirror also captures our VR panel, so continuous VR image output needs an independent input |
| Detection quality, color and performance | The saved room image ran on Frame, but a rug was mislabeled sofa; the 20-class demo has no rug class; systematic quality/performance evaluation pending |
| Stereo depth, world-space labels and Gaussian splats | Future work; splat reconstruction is a separate recorded-data experiment |

See [status and evidence](docs/status.md) for the complete matrix, device
reports, cloud validation and remaining unknowns. The public tracked-camera
probe returned no frames and interrupted passthrough; do not repeat that
unchanged headset test. Its [operating warning](docs/on-device.md#confirmed-public-probe-result)
and [recorded result](docs/status.md#e3-public-camera-probe) explain the limit.

## Quick start

### On Steam Frame: recorded-video replay

Use an existing checkout in the normal graphical session, with Python 3.12 and
a completed Arcturus recording. For initial access or another checkout location,
follow the [on-device guide](docs/on-device.md).

```bash
cd "$HOME/framejacker"
bash scripts/setup_window.sh
.venv-replay/bin/python scripts/replay_window.py
```

With no path argument, the app selects the newest completed `*_stereo.mp4` in
`~/Videos/ArcturusVisionCamera`. It loops the clip and provides pause, replay
and loop controls. Close the window to stop. See the
[window procedure](docs/on-device.md#open-the-annotated-replay-window) for explicit
file selection, dependencies and limits. To add the VR panel, follow the
[overlay guide](docs/vr-overlay.md), including its native bridge build.

### In the cloud or on Linux x86_64: development smoke check

Use Bash and [uv](https://docs.astral.sh/uv/getting-started/installation/). In an
existing checkout:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

This creates a dedicated Python 3.12 `.venv` and checks generated sample-video
decoding, CPU detection, offscreen Qt rendering and a separate ONNX arithmetic
example. It exits after validation; it does not open the interactive replay app
or prove live camera access. Outputs go to ignored `artifacts/smoke/`.

The Frame replay environment is `.venv-replay`; do not copy the cloud's x86_64
environment onto Frame. [Development setup](docs/development.md) owns full
installation, fixture verification, build and test instructions.

## Design and next steps

The intended application flow is:

```text
camera or replay input → preprocessing → detector → annotated window / VR panel
```

The [active live-capture plan](docs/live-capture.md#current-work-order) owns the
next tasks: prepare a dedicated camera provider and capture-only device trial,
using the implemented isolated shared-memory reader. The
[mirror diagnostic](docs/on-device.md#check-an-existing-steamvr-mirror) is
implemented and has delivered 1920 × 1080 RGB3 frames on Frame at the diagnostic's
10 reads/second limit. The user confirms its saved grayscale preview shows the
room. Recognition on that still completed but mislabeled a rug as sofa. The
[bounded mirror recognizer](docs/on-device.md#run-bounded-recognition-from-the-mirror)
then processed 51 reads at 4.993 fps, with clean shutdown and normal passthrough.
The [fixed-panel check](docs/on-device.md#check-whether-the-vr-panel-appears-in-mirror-input)
confirmed that this mirror captures our VR output. Arcturus is connected; its
selection for passthrough is unknown. The Frame reports GCC 15 and Vulkan 1.4.309
development files. Next run the
[build-only reference check and synthetic ARM64 reader tests](docs/on-device.md#build-the-capture-reference-without-activation),
then finish the provider fixes needed for input independent of our output before
continuous VR image display. Bounded recognition and snapshots shown after
capture remain useful. The model's class coverage and false positives need separate work.
The [isolated shared-memory reader](docs/on-device.md#check-existing-luma-shared-memory)
also passes controlled cloud tests; no dedicated capture provider has been
deployed. Built-in camera capture needs separate validation. The
[dated reference review](docs/capture-reference-review.md)
explains the strategy; [architecture](docs/architecture.md) describes components,
frame ownership and longer-term acceptance criteria.

## License

Original project code and documentation are covered by the [MIT license](LICENSE).
External packages and downloaded fixtures retain their upstream terms; see
[third-party sources](THIRD_PARTY.md). Framejacker is an independent project and
is not affiliated with Valve or Arcturus Vision.
