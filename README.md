# SightCircuit

Object recognition for Steam Frame: recorded-video replay and bounded SteamVR
mirror recognition work on Frame. The active POC is a fixed head-relative edge
HUD with names and approximate direction arrows. Native masking and expiry have
offline tests; mapping, feedback exclusion and live headset acceptance remain open.

## Project status

[Capabilities and evidence](docs/status.md) · [Documentation](docs/README.md) ·
[Active work and gates](docs/live-capture.md#edge-hud-acceptance-gates).
Dedicated camera capture, world-locked labels, depth and splats are deferred.

## Quick start

### On Steam Frame: recorded-video replay

Use the [device setup and replay procedure](docs/on-device.md#open-the-annotated-replay-window).
New device operations need their own scoped authorization. The unchanged
[public camera probe interrupted passthrough](docs/on-device.md#confirmed-public-probe-result).

### In the cloud or on Linux x86_64: development smoke check

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
```

Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/) are
required. [Development setup](docs/development.md) owns builds and checks.

## Design and next steps

See [POC scope](docs/poc-scope.md), [architecture](docs/architecture.md) and the
[current work order](docs/live-capture.md#current-work-order). Detailed historical
results and links moved to [README history](docs/project-history.md).

## License

[MIT](LICENSE); dependencies retain their [own terms](THIRD_PARTY.md).
Independent project; not affiliated with Valve or Arcturus Vision.
