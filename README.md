# SightCircuit

**Names for the things you look at, inside a Steam Frame.**

SightCircuit reads the headset's own passthrough camera, recognises everyday
objects on the device's CPU, and is being built to show their names, each
with an approximate direction arrow, on a small strip at the edge of your
view. No cloud service is involved, and a camera run saves no image: it
leaves counts, labels, boxes and timings, nothing else.

> **Experimental, and honest about it.** Recognising objects from the headset's
> camera has run on a real Steam Frame. Showing the labels in the headset is
> built and tested in the cloud, and has **not yet run on the device**. Every
> claim below says which kind of evidence stands behind it.

```text
   .        *            .       +            .          *        .

 ○─────┐     _____ _       __    __  _______                 _ __    ┌────○
       └─── / ___/(_)___ _/ /_  / /_/ ____(_)___________  __(_) /_ ──┘
            \__ \/ / __ `/ __ \/ __/ /   / / ___/ ___/ / / / / __/ ────────●
 ●──────── ___/ / / /_/ / / / / /_/ /___/ / /  / /__/ /_/ / / /_ ─────┐
 ◉───┐    /____/_/\__, /_/ /_/\__/\____/_/_/   \___/\__,_/_/\__/      └───◉
     └────────── /____/    R7 ●──▐██▌──●     C3 ●──┤├──●

             U1 ┌─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┐
  ●─────────────┤•   OBJECT RECOGNITION FOR STEAM FRAME   ├──────────────●
                └─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┘

    *               ┌─ sun 0.99                       ─┐              +
                                  ▄▄▄▄▄▄▄▄
◄ person 0.91                 ████████████████                    dog 0.87 ►
                           ██████████████████████
               .         ██████████████████████████         *      ●
         ●              ████████████████████████████              / \
        / \            ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀            /   \
   ●   /   \   ●      ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀       ●  /     \  ●
  / \ /     \ / \    ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀     / \/       \/ \
 /   /       /   \  └─                                ─┘   /  /        /   \
════════════════════════════════════════════════════════════════════════════
__/__/__/__/___/__/__/__/__/___/__/___|__\__\___\__\__\__\__\___\__\__\__\__
     /     /      /     /      /      |     \      \     \      \     \
________/_________/________/__________|_________\________\_________\________
           /            /             |            \            \
     /               /                |               \               \
__________________/___________________|__________________\__________________
               /                      |                     \
```

## How it works

```text
 passthrough      camera        object        label        overlay       strip at the
   camera    ──►  helper   ──►  detector ──►  rows    ──►  bridge   ──►  edge of view
 (left, SDK)    (bounded,      (CPU, about   (name and    (each label
                 no pixels      36 ms a       arrow)       expires 1 s
                 saved)         frame)                     after its frame)
```

Three rules shape everything:

- **Bounded.** Every camera run has a time limit and starts with a typed
  confirmation. It is built to stop when the headset comes off; proving that
  on the device is the next step.
- **Nothing stale.** A label carries the time its camera frame was taken. The
  display drops it one second later unless a newer frame confirms it.
- **Closed until proven.** Live display sits behind gates in
  [`sightcircuit/gates.py`](sightcircuit/gates.py). They open only after
  recorded headset results, never by a flag. Today all of them are closed.

## Project status

| What | Where it has run | Evidence and limits |
| --- | --- | --- |
| Recognise objects from the headset camera, bounded, nothing displayed | **On a Steam Frame** (reported by the wearer) | 100 of 100 frames in a 20-second run, detection in about 39 ms at the 95th percentile, passthrough undisturbed. It missed a small bottle at a distance and reported one unlikely `car`; accuracy is not yet measured. [E99](docs/evidence/e099.md) |
| Replay a recording with labels, in a window and on a VR panel | **On a Steam Frame** | A 400-frame panel run updated to the end and shut down cleanly; label accuracy unevaluated. [E47](docs/evidence/e047.md), [E81](docs/evidence/e081.md) |
| Recognise objects in a short capture of the SteamVR mirror | **On a Steam Frame** | An earlier route, kept as a diagnostic. [E48](docs/evidence/e048.md), [E81](docs/evidence/e081.md) |
| Show camera labels on the headset strip (`camera_hud`) | **Cloud tests only** | The whole path runs against stand-ins in CI, including label expiry on a simulated VR runtime. It has not met SteamVR or the camera on the device. [Guide](docs/guides/camera-hud.md) |
| Labels fixed to objects in the room, depth, splats | Not started | Deferred until the strip is accepted |

**What comes next** is a short series of headset sessions, each approved
separately by the project's owner: confirm a run stops when the headset comes
off, confirm the camera cannot see the strip's own labels, confirm labels
expire on the real display, then show the first camera labels and hold one
named for 60 seconds. [Current work and acceptance](docs/live-capture.md) has
the steps and their pass marks; [capabilities and evidence](docs/status.md)
has the full record, including what failed.

## Quick start

### In the cloud or on Linux x86_64: development smoke check

No headset needed. Install Python 3.12 or 3.14,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and the Linux
libraries listed in [development setup](docs/development.md). From the
repository root:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

A successful run reports `"status": "passed"` after decoding 12 generated
frames, running CPU inference, and rendering offscreen.

To watch the camera-to-label path run end to end on stand-ins (a fake camera,
a scripted detector and a bridge that displays nothing), set up the replay
environment as [development setup](docs/development.md) describes, then:

```bash
bash scripts/build_overlay_bridge.sh && bash scripts/build_camera_helper.sh --fake
.venv-replay/bin/python scripts/camera_hud.py --validate
```

It prints one JSON report with `"status": "validated"`.

### On Steam Frame: recorded-video replay

The gentlest first run on the headset plays back a recording you already
have. Start with [device access and inventory](docs/on-device.md), then follow
the [recorded replay guide](docs/guides/replay.md); the
[VR panel guide](docs/vr-overlay.md) explains placement and shutdown.

Camera runs are deliberately more guarded. Read
[camera recognition](docs/guides/camera-recognize.md) and
[camera HUD](docs/guides/camera-hud.md) first: each is a procedure, not a
permission, and every device procedure keeps its own operating restrictions
(an early public camera probe interrupted passthrough, which is why).

## Design and next steps

- [Documentation index](docs/README.md): everything, grouped by what you want
  to do.
- [Architecture](docs/architecture.md): the data flow in detail.
- [POC scope](docs/poc-scope.md): the intended result and the decisions made
  along the way.
- [Current work](docs/live-capture.md#current-work-order): what is open and
  what each step must show.
- [Contributing](CONTRIBUTING.md): checks and review expectations.
- [Project history](docs/project-history.md): how the project got here.

## License

[MIT](LICENSE); dependencies retain their [own terms](THIRD_PARTY.md).
Independent project; not affiliated with Valve or Arcturus Vision.
