# SightCircuit

SightCircuit recognizes objects in Steam Frame recordings and short captures of
its SteamVR mirror. The goal is to show object names and approximate direction
arrows in a fixed strip at the edge of the headset view.

**This is an experimental project.** Recorded replay and bounded mirror
recognition have run on Frame. The continuous heads-up display (HUD) is still
being qualified; it is not ready for live use.

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

## Project status

- **Works on the tested headset:** annotated recorded replay, a replay VR panel,
  and bounded mirror recognition with a preview inspected after capture.
- **Verified offline:** the shared processing pipeline, HUD masking, stale-result
  expiry, and failure-handling checks using synthetic input.
- **Still open:** accepted HUD mapping, feedback exclusion,
  physical display expiry, and combined live headset acceptance. Lifecycle
  acceptance is recorded in E79; the green-input cause remains unknown.
- **Deferred:** dedicated camera capture, world-locked labels, depth, and splats.

Read [capabilities and evidence](docs/status.md) for the limits behind these
statements, or [current work and acceptance](docs/live-capture.md) for next steps.

## Quick start

### On Steam Frame: recorded-video replay

Start with [device access and inventory](docs/on-device.md), then follow the
[recorded replay guide](docs/guides/replay.md). The [VR panel guide](docs/vr-overlay.md)
explains placement and shutdown. Device procedures retain their specific
operating restrictions; the public camera probe previously interrupted passthrough.

### In the cloud or on Linux x86_64: development smoke check

Install Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and the Linux libraries listed in [development setup](docs/development.md).
From the repository root:

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

A successful run reports `"status": "passed"` after decoding 12 generated frames,
running CPU inference, and rendering offscreen. It does not require a headset.

## Design and next steps

Start at the [documentation index](docs/README.md). Read [architecture](docs/architecture.md)
for the data flow, [POC scope](docs/poc-scope.md) for the intended result, and
[current work](docs/live-capture.md#current-work-order) to choose a task.
[Contributing](CONTRIBUTING.md) explains checks and review expectations.
The [historical README](docs/project-history.md) retains earlier project context.

## License

[MIT](LICENSE); dependencies retain their [own terms](THIRD_PARTY.md).
Independent project; not affiliated with Valve or Arcturus Vision.
