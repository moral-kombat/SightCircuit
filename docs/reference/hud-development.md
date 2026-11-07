# HUD development and offline validation

Start with [development setup](../development.md). This reference covers
generated-input checks, candidate reports, observer scene statistics, mask
fill evaluation, native builds, and the checkpoint manifest. The
[operator checkpoint](../guides/hud-checkpoint.md) is the one device procedure.
Offline success does not qualify mapping or physical display recovery.

## Edge HUD offline test plan

The earlier implementation session reported the synthetic-only path;
[E69](../status.md#e69-offline-edge-hud-and-native-mask-validation) preserves its
reconstructed provenance and the separate cloud recheck. The original session
log is not committed. Generated tests cover full-footprint native masking before resize,
margin/stereo union, out-of-bounds and missing mappings, capture/config changes,
fixed strip containment, owned buffers, source/result association and stale
expiry independent of inference/settings. Use generated inputs without a headset.
Existing lifecycle/expiry tests remain relevant but do not establish HUD mapping,
head-motion leakage or physical hiding. Those require the separately approved
[device checkpoint](../guides/hud-checkpoint.md#bounded-edge-hud-calibration-checkpoint).

For a future justified rerun in the local Linux replay/window environment:

```bash
python -m unittest tests.test_hud_mask tests.test_hud_fill tests.test_live_pipeline tests.test_display_expiry tests.test_mirror_recognize -v
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --validate-overlay --seconds 3
```

These use generated input and native validation only, with the existing built
bridge and detector assets. `--synthetic-hud` and `--detector` require
`--synthetic-check`; they do not load hardware calibration or enable live
input/real output. `--detector` takes a registered model name (`DETECTORS`
through `create_detector`; the default is `mobilenet-ssd`, and `real` remains
an alias for it) or `fixture`. Since the
[synthetic HUD validation counts](#synthetic-hud-validation-counts), the second
command is also a validation run. With the default detector it ends
`completed` (exit 0) since the [neutral mask fill](#hud-mask-fill-evaluation);
with the zero-filled mask it ended `validation_failed` (exit 1), because
MobileNetSSD boxed the black footprint region of the generated frames.
`--detector fixture` reports no detections and checks the mask, ledger and
transport without a model. The earlier session reported 57 tests; that absent
log is not a new execution result.

The later [marker-only harness](../status.md#e70-offline-marker-only-harness) adds
`hud_marker_check.py` and native generated-marker validation. Its final saved
Linux log reportedly passed 49 tests; that original log is absent. The
marker-only harness does not itself observe mirror
corners, qualify mapping or demonstrate physical expiry. The later
[ARM64 build and empirical observer](../status.md#e71-offline-arm64-build-and-empirical-marker-observer)
have offline evidence. Subsequently recovered [E75](../evidence/e075.md) establishes
that the historical V4 ARM64 binary and observer ran on Frame and were rejected;
the software stop is explained but physical recovery remains unresolved. A
future clean observer result stays unqualified. Observer suite counts are implementation-session reports,
including a Windows symlink-privilege error, not an all-platform pass or an
independently inspected standalone log. No device execution is authorized.

Earlier deployment preparation reported a local eight-entry manifest (ARM64
bridge plus seven Python modules), with size/hash checks during that review.
The manifest is not committed and does not pin the current implementation; a
new reviewed artifact manifest is required for a future checkpoint. The
[checkpoint artifact manifest](#checkpoint-artifact-manifest) below now writes
and verifies one on Frame.
See the [preflight and placeholder command](../history/device-handoffs.md#deployment-preflight-and-command-shape-not-authorized).
Target UAPI/ctypes/ioctl size evidence is still required before capture; passing
the cloud ABI test does not establish the headset ABI. Cross-compilation and Windows fixture outcomes do not establish Frame
runtime compatibility. No install, transfer or device execution is implied.

### Checkpoint artifact manifest

`scripts/checkpoint_manifest.py` is the command and
`sightcircuit/runtime/manifest.py` the library. `--write PATH` pins the git
commit, a clean-tree check, the Python, architecture and OS identity, and
SHA-256 values of the built bridge, every `sightcircuit/**/*.py`, the
checkpoint scripts, the bridge sources and the assets in
`scripts/smoke-assets.json`. It also pins the installed package versions, all
as deterministic JSON with checkout-relative paths. It creates a new file
(never overwriting) and refuses a dirty tree unless `--allow-dirty` records
`dirty: true`. `--verify PATH` is read-only: it writes nothing, builds nothing
and exits 1 on any difference or a dirty tree, printing the differences as
JSON. Both run on Frame after the native build; built binaries are never
transferred. The [device checkpoint](../guides/hud-checkpoint.md)
gives the commands, when to run them and what a failure means.

`CHECKPOINT_SCRIPTS` and `CHECKPOINT_PACKAGES` in the library are the single
lists. `tests/test_checkpoint_manifest.py` fails if a script named in a code
block of the canonical checkpoint procedure is missing from the list, if a documented
`checkpoint_manifest.py` command does not parse with the real parser, if a
procedure runs the marker bridge before its first `--verify`, or if the
third-party packages in the checkpoint's import closure differ from the
pinned ones.
Its write/verify cases each build a temporary git checkout. They cover a
changed module, a rebuilt bridge, a modified tracked file, an untracked file,
another architecture, Python or package version, an existing output path
(including one created after the existence check), and a verify that leaves
every file time unchanged. A manifest with a non-finite or overflowing number,
or nested deeper than any manifest, is reported as unreadable, so the
verification report stays strict JSON. These are cloud checks of the tool, not
Frame evidence.


## Observer report to mapping candidate

[`hud_validation_report.py`](../../scripts/hud_validation_report.py) has two
offline modes. `--check-candidate` checks a mapping candidate record against a
declared current context and time. `--from-observer` builds a candidate from a
saved `hud_marker_observer.py` report and a declared context, using
`sightcircuit/hud/candidate.py`. The candidate's session tokens come only from
the tokens the observer recorded (`--record-session-tokens`, off by default).
The declared context holds the other fields and must not carry a token. A
report without recorded tokens, or with a token that was not observed or that
changed, is rejected. The result is at best
`candidate_valid_unqualified` with exit 2; any rejection is a structured report
with exit 1 and writes nothing. Unknown terms are null with a reason, never 0,
and the observer's margin, already inside its union, is counted once. The
[qualification design](../hud-qualification.md#observer-report-to-candidate-2026-11-01)
owns the rules. From the checkout:

```bash
python3 scripts/hud_validation_report.py --from-observer /tmp/observer-report.json \
  --declared-context /tmp/declared-context.json --lifetime-seconds 60 \
  --candidate-output /tmp/candidate.json
.venv-replay/bin/python -m unittest tests.test_hud_candidate tests.test_hud_qualification \
  tests.test_hud_marker_observer
```

The tests convert reports from the real observer on generated frames. They are
cloud results, not Frame evidence, and they open no device or gate.


## Observer scene precondition statistics

`sightcircuit/hud/scene.py` holds the observer's scene precondition (the grey
threshold, the bright-contour budget and the candidate-quad filter and
budget) and `scene_statistics`, which describes one frame against it in counts
only. `locate_copies` uses the same functions, so the statistics cannot drift
from what the observer applies. `hud_marker_observer.py --analyze-still STILL`
prints them for one saved still and refuses device, `/proc` and `/sys` paths,
links and non-regular files before opening them; an observer run stopped by
either budget carries them as `scene_statistics`. The
[qualification design](../hud-qualification.md#observer-scene-precondition-2026-11-04)
has the background and the seed-still reference.

```bash
.venv-replay/bin/python scripts/hud_marker_observer.py --analyze-still evaluation/local/still.png
.venv-replay/bin/python -m unittest tests.test_observer_scene tests.test_hud_marker_observer
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch > /dev/null  # the seed stills, once
.venv-replay/bin/python -m unittest tests.test_observer_scene_seed
```


## Synthetic HUD validation counts

A synthetic HUD run, `sightcircuit/runtime/live.run_pipeline` with a HUD stage,
is also a validation run. `sightcircuit/hud/validation.py` puts a
`ValidationSink` ahead of the result sink. It records each result in a
`ValidationLedger` built from `synthetic.fixture_candidate(calibration)` with
the run's own maximum age. The engine counts the stage's exclusion checks in
the `pipeline` block's `hud_checks`, which is null without a HUD stage:
frames checked, footprint pixels left non-zero after zeroing (before the
[neutral fill](#hud-mask-fill-evaluation)), and boxes dropped for touching the
mask or crossing an eye. The report adds
`validation_ledger` and `g5_software`, whose counts use the names in
`docs/templates/hud-device-validation-report.json`.

The runtime alone only drops such a box from presentation. In a validation
run, a footprint pixel left after zeroing, a dropped box or any ledger
rejection ends the run `validation_failed` with exit 1, and a rejected result
never reaches the result sink. A run that already failed on its own, for
example on a capture-context change, keeps its status and reason, and
`g5_software` names the validation failure. `MAX_DISPLAY_AGE_MS` in
`sightcircuit/hud/contract.py` is the one maximum age and default for the
pipeline, the ledger and both commands, and `Pipeline` refuses a HUD stage
with a longer one. The
[qualification design](../hud-qualification.md#synthetic-validation-counts-and-one-maximum-age-2026-11-01)
owns the rules. From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_hud_validation tests.test_pipeline tests.test_live_pipeline
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --detector fixture --seconds 3
python -m scripts.live_pipeline --synthetic-check --synthetic-hud --seconds 3
```

Both commands use generated input, with no overlay. The first uses the fixture
detector, which reports no detections and loads no model. The second uses the
default MobileNetSSD. Both end `completed` with exit 0 and
`g5_software.status` `checked_unqualified`, and so does the second with
`--validate-overlay`. With the zero-filled mask (33cd83d) the second ended
`validation_failed` with exit 1, because the detector reported the black
footprint region itself as `tvmonitor`. The mask now refills that region from
its surround ([HUD mask fill evaluation](#hud-mask-fill-evaluation)), and a
validation run still fails on any box touching the mask. These are cloud
results, not Frame evidence; they observe no device or physical expiry and open
no gate.


## HUD mask fill evaluation

`HudMask` zeroes each masked region (footprint plus margin, per eye) at native
resolution and counts the footprint pixels still non-zero. Then
`hud.mask.fill_regions` refills each region with one neutral color: the rounded
mean of an 8-pixel ring outside it, in the same eye and outside every region.
Only after that does the resize run. The fill reads only the zeroed copy, so a
count of 0 still proves that no HUD pixel reaches the detector, and the strict
overlap and eye rule is unchanged.

`scripts/evaluate_mask_fill.py` (library `sightcircuit/mask_fill_evaluation.py`,
outside the runtime path) reproduces the comparison that chose this fill. It
compares six fills:

- `zero`, the baseline;
- `ring_mean`, the runtime fill;
- `nearest`, nearest-pixel replication;
- `inpaint_telea` and `inpaint_ns`, two `cv2.inpaint` modes;
- `surround_blur`, a blurred surround patch.

Each fill runs with every registered detector over two inputs: the synthetic
source's 127 odd gray levels with the fixture calibration (`--gray-levels all`
for all 255), and the seed stills with a mask at five positions. The JSON
report holds, per fill and detector:

- the gray levels and seed boxes that touch the mask, and the artifact-like
  boxes among them;
- changes to detections that touch no mask (lost, added, relabelled, and
  rescored beyond a stated 0.05 tolerance);
- seed pin counts per position, both of the detector's whole output and of the
  boxes the runtime keeps (`runtime_kept`), which shows what the strict rule
  costs next to the HUD;
- leak and determinism checks;
- time per frame at 96x64 and 1920x1080 on the runner CPU.

`--expect-clean-gray-sweep` exits 1 if any detector boxes the mask on the sweep
with the runtime fill. From the checkout:

```bash
.venv-replay/bin/python scripts/evaluate_mask_fill.py --fetch --expect-clean-gray-sweep \
  --report /tmp/mask-fill.json
.venv-replay/bin/python scripts/evaluate_mask_fill.py --no-seeds --gray-levels all --fill zero --fill ring_mean
.venv-replay/bin/python -m unittest tests.test_hud_fill tests.test_mask_fill_evaluation
```

The full run takes about 2.5 minutes on a 4-core x86_64 cloud runner. The
[qualification design](../hud-qualification.md#neutral-mask-fill-2026-11-03)
records the results and the choice. They are runner-CPU results on generated
frames and public stills, not Frame evidence.


## Native VR panel build and validation

`bash scripts/build_overlay_bridge.sh` builds the native tool into ignored
`artifacts/overlay-bridge/`, using the pinned SDK in `.cache/openvr`. The camera
and overlay builders share `scripts/build_openvr_tool.sh`; neither builder runs
a runtime test. The camera binary's explicit opt-in guard remains in place.

The overlay job checks that the generated contract header is current, strict
C++ syntax, packet bounds, malformed input, idle payload timeouts, signals and
named missing-runtime diagnostics. The replay
window job also builds the bridge so its validation-only mode can check actual
RGBA forwarding and Qt control creation. This mode reports no OpenVR
initialization and zero submitted frames. For the real hardware procedure,
read [VR replay panel](../vr-overlay.md).

### Generated HUD contract header

[`tools/hud_contract.hpp`](../../tools/hud_contract.hpp) is committed and
generated from the Python contract by
[`sightcircuit/hud/native.py`](../../sightcircuit/hud/native.py). It holds
`HudConfig` and its digest, the calibration `MARKER` (sizes, positions, gray
levels and corner ids), the pipe packet layouts and opcodes, the marker
duration (1–20 s), display age (1 s) and frame side (512) limits, and
`PANEL_LIMITS` with the `PanelSettings` defaults. `tools/overlay_bridge.cpp`
and `tools/overlay_frame.hpp` include it and keep no copies of those values;
they share one little-endian `read_u32`. Python call sites import the same
constants from `sightcircuit/hud/contract.py` (`PANEL_LIMITS` and
`PanelSettings` stay in `sightcircuit/overlay.py`). Change a value only on the
Python side, then:

```bash
python3 scripts/hud_contract_header.py --write
bash scripts/build_overlay_bridge.sh
python3 scripts/hud_contract_header.py --check   # the overlay CI job runs this
```

`tests/test_hud_contract.py` fails when the committed header differs from the
generated text, when a Python contract value would not reach the header, when
the built bridge reports a different contract digest (rebuild it), and when the
rebuilt bridge or a Python call site does not apply a limit at its boundary.
Each Python marker-duration site must reject with its own reason, and
`tests/fixtures/overlay_dispatch_fixture.cpp` accepts a deadline exactly at the
generated display-age cap and rejects one a nanosecond later.

`sightcircuit-overlay-bridge --render-calibration-marker NEW_RGBA_FILE` draws
the native marker into a new file (exclusive creation) and reports the contract
and HUD configuration digests. It makes no OpenVR call. The tests require its
RGBA bytes to equal `sightcircuit.hud.observation.marker_template()` pixel for
pixel, with every pixel opaque. They also replay V4's shape offline: two clear
baseline reads, then the native marker on read 3, which must decode all four
corner tags with zero tag and bit error. The compared marker function is
unchanged from the V4 native source (SHA-256 `5ffc47f5…`, the
`tools/overlay_bridge.cpp` committed in `8fb6e58`). A passing run therefore
rules out a disagreement between the native generator and the observer
template, so this comparison offers no candidate explanation for V4's frame-3
tag/bit rejection, which remains unexplained. It does not exercise compositor
scaling, color or gamma transforms, mirror capture or timing. These are
generated, validation-only checks, not Frame evidence.


## Checkpoint manifest identity and verification

`scripts/checkpoint_manifest.py` (library `sightcircuit/runtime/manifest.py`)
writes and verifies that manifest. Both commands run on Frame: `--write` right
after the bridge is built there, and `--verify` immediately before the
validation and observer commands. Built binaries are never transferred, so a
manifest written in the cloud or for another build does not verify on Frame.
The manifest records the full git commit, whether the tree is clean (tracked
changes and untracked, non-ignored files count as dirty), the Python version
and implementation, `platform.machine()`, the OS (with its os-release ID,
version and build) and kernel release, and SHA-256 values of the built
`artifacts/overlay-bridge/sightcircuit-overlay-bridge`, every
`sightcircuit/**/*.py`, the scripts this procedure runs, the bridge sources
including `tools/hud_contract.hpp`, and the models and fixture listed in
`scripts/smoke-assets.json`. It also records the installed `numpy` and
`opencv-python-headless` versions. Paths are relative to the checkout. The JSON
is deterministic (sorted keys), with `schema_version` 1, `report_kind`
`checkpoint_artifact_manifest` and a `scope` stating that it pins software
identity only. It is not device or qualification evidence.
`checkpoint_manifest.py --help` confirms these flags:

```bash
bash scripts/build_overlay_bridge.sh
mkdir -p artifacts/hud-calibration
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --write artifacts/hud-calibration/checkpoint-manifest.json
# Immediately before the run (read-only):
.venv-replay/bin/python scripts/checkpoint_manifest.py \
  --verify artifacts/hud-calibration/checkpoint-manifest.json
```

`--write` creates a new file and never overwrites one, so move an earlier
manifest aside first. It refuses a dirty tree and a path inside the checkout
that git does not ignore (`artifacts/` is ignored). `--allow-dirty` records
`dirty: true` instead, and such a manifest never verifies. A missing bridge,
model or package is recorded as null and listed under `problems`. `--verify`
writes and builds nothing. It prints a JSON report whose `differences` name
each changed, missing or unexpected field, and it exits 0 only when nothing
differs, the tree is clean and every pinned file and package is present. A
failure means the software on Frame is not what was pinned: a changed module or
script, a rebuilt bridge, a dirty tree, or a different interpreter,
architecture or package. Stop before opening the device and keep the report.
Re-pin only after a deliberate rebuild of the approved revision, never to
silence a difference. A pass shows only that the pinned software is unchanged,
not that the bridge or observer behaves correctly or that the headset
recovers. `hud/qualification.py` still checks evidence digests for format only;
`manifest.verify_file_digest` checks a file's content against a digest, for a
later change to use.
