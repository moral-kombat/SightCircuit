# Contributing

Start with the [documentation index](docs/README.md),
[current status](docs/status.md) and [architecture](docs/architecture.md).
The [development guide](docs/development.md) owns full setup/build instructions;
[on-device procedures](docs/on-device.md) apply to the headset.

## Development checks

Use the existing checkout. Codex cloud tasks already have an isolated workspace;
a separate Git worktree is only needed if explicitly requested.

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
git diff --check
```

The smoke check exercises decoding, CPU inference and offscreen rendering using
generated sample video. To run the recording, replay-window and native overlay
tests, install the window dependencies and build the bridge:

```bash
bash scripts/setup_window.sh
bash scripts/build_overlay_bridge.sh
.venv-replay/bin/python -m unittest discover -s tests -v
```

These tests run without a camera or SteamVR. Native transport tests use the
bridge's validation-only mode; they do not validate headset rendering. See the
[development guide](docs/development.md) for coverage and dependency limits.
Add behavioral tests with new application components and keep claims about
passing checks tied to what actually executed.

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
- Keep dependency changes explicit in `requirements-dev.txt`,
  `requirements-replay.txt` and `requirements-window.txt` as appropriate.
  Preserve download verification and include fixture provenance when changing
  sample assets.

Describe the resulting behavior and relevant checks in a pull request. Separate
cloud smoke results from actual camera/headset results. Include source changes
and small reproducible fixtures where appropriate; link large external assets
with checksums instead of committing generated environments or model binaries.

## Documentation maintenance

Update the owning document with the component change; the
[documentation plan](docs/documentation-plan.md) assigns each topic a home.
Put full validation evidence in [status](docs/status.md), including procedure,
date, revision, runtime, result and limits; explicitly mark unknown fields.
Keep cloud checks, CI runs, user device reports and upstream claims distinct.

Maintain the current capture backlog in [live capture](docs/live-capture.md);
dated reviews retain their source pins and decision context. Do not advertise
planned diagnostics as available commands. Preserve existing paths and linked
headings, and check local links, heading anchors, command paths/options and
`git diff --check` before publishing. Keep operating warnings beside affected
commands even when the detailed evidence moves.

## License

Original contributions are under the repository's [MIT license](LICENSE).
Dependencies, models and sample data retain their upstream licenses; see
[THIRD_PARTY.md](THIRD_PARTY.md).
