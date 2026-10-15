# Contributing

Framejacker is in early development. Camera research and environment validation
exist; live capture and the application do not yet. Start with the
[README](README.md), [architecture](docs/architecture.md) and
[camera interface research](docs/steam-frame-camera.md).

## Development checks

Use the existing checkout. Codex cloud tasks already have an isolated workspace;
a separate Git worktree is only needed if explicitly requested.

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
git diff --check
```

The smoke check exercises decoding, CPU inference and offscreen rendering using
generated sample video. There is no application unit-test suite yet. Add
behavioral tests with new application components and keep claims about passing
checks tied to what actually executed.

## Changes and pull requests

- Keep capture, preprocessing, inference and display components separate. A
  file/replay input should remain useful without camera hardware.
- Preserve timestamps, timebase provenance, image layout and calibration
  information when adding a camera backend.
- Distinguish official API contracts, community observations and measurements
  from your own hardware. Include source links for research updates.
- For device changes, report SteamOS/SteamVR versions, camera model, frame format,
  validation performed and unresolved timing/compatibility issues. Do not infer
  live capture from a successful handle acquisition or an available device node.
- Keep raw recordings, personal calibration, credentials, downloaded weights and
  generated outputs out of commits. The repository ignores dedicated local
  `recordings/`, `calibration/`, `models/`, `.cache/` and `artifacts/` directories.
- Keep dependency changes explicit in `requirements-dev.txt`. Preserve download
  verification and include fixture provenance when changing sample assets.

Describe the resulting behavior and relevant checks in a pull request. Separate
cloud smoke results from actual camera/headset results. Include source changes
and small reproducible fixtures where appropriate; link large external assets
with checksums instead of committing generated environments or model binaries.

## License

Original contributions are under the repository's [MIT license](LICENSE).
Dependencies, models and sample data retain their upstream licenses; see
[THIRD_PARTY.md](THIRD_PARTY.md).
