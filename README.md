# SightCircuit

**Look at something. See what it is.**

SightCircuit is an open experiment in giving a Steam Frame a little sight of
its own. It reads the headset's passthrough camera, recognises everyday
objects right on the device, and is working toward one simple thing: a box
and a name drawn around each object in your view, as you look around the
room. No cloud service is involved, and a camera run saves no image. It
leaves counts, labels, boxes and timings, nothing else.

> **Where things stand.** The headset can already recognise objects from its
> own camera: that has run on a real Steam Frame. Drawing the results in your
> view is the part being built now. It works in cloud tests and has **not yet
> run on the device**. Everything below says plainly which is which, so you
> always know what is real today.

Curious, sceptical or keen to help? You are welcome here. You can run the
whole camera-to-display path on your own Linux machine in a few minutes, no
headset required: see the [quick start](#quick-start).

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
 passthrough      camera        object        boxes and     overlay       drawn in
   camera    ──►  helper   ──►  detector ──►  names    ──►  bridge   ──►  your view
 (left, SDK)    (bounded,      (CPU, about   (one per      (each result
                 no pixels      36 ms a       object)       expires 1 s
                 saved)         frame)                      after its frame)
```

The detector already finds a box and a name for each object, many times a
second. What is left is the last step: drawing them where you are looking.

That is being done in two stages. The **first display** is deliberately
modest: a small strip at the edge of your view that lists what was recognised.
It is a holdover from an earlier design, which read frames back from the
headset's mirror and had to keep the display from recognising its own output.
Reading the camera directly should make that problem disappear (one of the
next headset sessions checks exactly that), and the strip stays on as the
simplest safe way to put results in front of your eyes for the first time. The **destination** is boxes drawn around the objects themselves.
That needs one more piece, matching a spot in the camera's picture to the
same spot in your view, and that work has not started.

Three rules shape everything, whichever display is showing:

- **Bounded.** Every camera run has a time limit and starts with a typed
  confirmation. It is built to stop when the headset comes off; proving that
  on the device is the next step.
- **Nothing stale.** A result carries the time its camera frame was taken. The
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
| Show what was recognised, in the headset: the first display, a strip of names (`camera_hud`) | **Cloud tests only** | The whole path runs against stand-ins in CI, including results expiring on a simulated VR runtime. It has not met SteamVR or the camera on the device. [Guide](docs/guides/camera-hud.md) |
| **The goal:** boxes and names drawn around objects in your view | Not started | Needs the camera-to-view matching described above; planned after the first display is accepted on the headset. Depth and splats come later still |

**What comes next** is a short series of headset sessions, each approved
separately by the project's owner: confirm a run stops when the headset comes
off, confirm the camera cannot see what the headset itself displays, confirm
results expire on the real display, then show the first names from the camera
and keep one on screen for 60 seconds. After that, the boxes. [Current work and acceptance](docs/live-capture.md) has
the steps and their pass marks; [capabilities and evidence](docs/status.md)
has the full record, including what failed.

## Quick start

### In the cloud or on Linux x86_64: development smoke check

No headset needed, and a good place to start. Install Python 3.12 or 3.14,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and the Linux
libraries listed in [development setup](docs/development.md). From the
repository root:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

A successful run reports `"status": "passed"` after decoding 12 generated
frames, running CPU inference, and rendering offscreen.

Want to see more? To watch the camera-to-display path run end to end on stand-ins (a fake camera,
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
- [Contributing](CONTRIBUTING.md): how to send a change, and what review
  looks for. Questions and small fixes are as welcome as big ones.
- [Project history](docs/project-history.md): how the project got here.

## License

[MIT](LICENSE); dependencies retain their [own terms](THIRD_PARTY.md).
Independent project; not affiliated with Valve or Arcturus Vision.
