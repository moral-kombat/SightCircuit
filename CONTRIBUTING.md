# Contributing

Start with the [documentation index](docs/README.md),
[current status](docs/status.md) and [architecture](docs/architecture.md).
The [development guide](docs/development.md) owns prerequisites and setup/build instructions;
[on-device procedures](docs/on-device.md) apply to the headset.

## Development checks

Follow the [Linux prerequisites](docs/development.md) before setup.
Use the existing checkout. Codex cloud tasks already have an isolated workspace;
a separate Git worktree is only needed if explicitly requested.

```bash
bash scripts/setup.sh
.venv/bin/python scripts/smoke_check.py
uvx ruff@0.14.2 check .
git diff --check
```

The smoke check exercises decoding, CPU inference and offscreen rendering using
generated sample video. To run the recording, replay-window and native overlay
tests, install the window dependencies and build the bridge:

```bash
bash scripts/setup_window.sh
bash scripts/build_overlay_bridge.sh
bash scripts/build_shm_reader.sh
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest discover -s tests -v
```

These tests run without a camera or SteamVR. Native transport tests use the
bridge's validation-only mode; they do not validate headset rendering. See the
[development guide](docs/development.md) for coverage and dependency limits.
Add behavioral tests with new application components and keep claims about
passing checks tied to what actually executed.

## Changes and pull requests

- Keep capture, preprocessing, inference and display components separate. A
  file/replay input should remain useful without camera hardware. Add reusable
  code to the `sightcircuit/` package (a new input is a `FrameSource`, a new
  output a `Sink`, a new model a `Detector`); keep `scripts/` for commands.
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

Update the document that owns the topic in the same change as its implementation.
The [ownership table and writing rules](docs/documentation-plan.md) identify that
home. Keep procedures in task guides, contracts in references, and dated accounts
in history. [Current work and acceptance](docs/live-capture.md) is the only current
work order; [scope](docs/poc-scope.md) records accepted decisions.

Full validation evidence belongs in a [per-entry record](docs/evidence/README.md).
Include execution date, platform/runtime, revision, procedure, result, and limits;
mark unavailable fields unknown. The coordinator allocates IDs after checking
pending records, adds the entry to the evidence index, and updates reviewed
capabilities in `docs/status-data.json`. Local E78 is not an instruction to reuse
IDs already reserved in the older repository's open PR #23.

Regenerate the status page, architecture summary, and evidence catalog, then
validate them:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
python3 scripts/check_docs.py
python3 -m unittest discover -s tests -p 'test_generate_status.py'
python3 -m unittest discover -s tests -p 'test_check_docs.py'
git diff --check
```

Keep cloud checks, CI, device reports, and upstream claims distinct. Updating a
document or a review date supplies no new headset evidence. Preserve historical
paths, heading anchors, source pins, and unknown fields. When moving a procedure,
leave a forwarding section and update its command-contract tests to read the
canonical location; do not remove their assertions. The offline link checker
also protects the original anchors recorded in `docs/legacy-anchors.json`.

Verify commands against their parsers and source. Do not advertise planned tools
as runnable, or run a headset experiment merely to validate documentation. Keep
operating restrictions beside the affected command. Changes to HUD procedures
also require the existing checkpoint and observer tests described in the
[development guide](docs/development.md).

## License

Original contributions are under the repository's [MIT license](LICENSE).
Dependencies, models and sample data retain their upstream licenses; see
[THIRD_PARTY.md](THIRD_PARTY.md).
