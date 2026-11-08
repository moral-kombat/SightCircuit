# Detector evaluation reference

Use this reference when preparing a labelled dataset, comparing detectors, or
interpreting an evaluation report. The [Frame stills procedure](../guides/mirror-recognition.md#compare-detectors-on-frame-stills)
shows how to collect inputs; [development setup](../development.md) installs
the dependencies. Public seed images check regressions and do not select a
model for indoor Frame use.

## Detector evaluation

`scripts/evaluate_detectors.py` runs each detector on the same stills through
the application's conversion path (scaled to at most 960 pixels) and scores
them against a manifest. The default
[seed manifest](../../evaluation/seed.json) holds six checksum-pinned outdoor
darknet photos with image-level labels; `--fetch` downloads them into ignored
`.cache/sightcircuit/evaluation/`. It is a regression check for wiring, label
mapping and gross confusions, not evidence about Frame's indoor input.

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --fetch --expect evaluation/seed-expected.json
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --detector yolox-nano --threshold 0.5 --report /tmp/evaluation.json
```

Each class in `sightcircuit.detection.DETECTORS` owns what callers need to know
about it: its constructor takes the registry name, `model_files(name)` lists
its checksum-pinned assets and `preprocessing(name)` describes its model input
(input size, stretch or letterbox resize, interpolation, channel order, layout,
dtype, mean, scale, padding value and box mapping). The evaluator's provenance,
the smoke check and the asset tests read these, so adding a detector changes
`sightcircuit/detection.py` and its `scripts/smoke-assets.json` entries only.
The default detector is unchanged; the 0.5 threshold is the
[`POC_THRESHOLD` constant](detection.md#poc-threshold-and-live-budget).

Manifest entries (`schema_version: 1`) need a unique `id` and either `path`
(relative to the manifest) or `file` plus `url`/`sha256` for download. A `path`
entry may also carry `sha256`; any supplied digest is verified before inference,
and `--require-input-digests` needs one for every entry. Entries written by
`scripts/frame_session_manifest.py --still-report` carry `sha256` and
`capture` (`detector`, `source`, `sequence` from the capture report), which the
evaluator does not read. `present`
lists canonical COCO labels that must be detected; a nested list accepts any
alternative, such as `["car", "truck"]`. `absent` lists labels that must not be
reported. Optional `objects` give `label`, source-pixel `box_xyxy` and
`confusable` labels: supported labels are matched at `--iou` (default 0.5); a
confusable detection overlapping the object at IoU 0.3 is a confusion. Set
`boxes_complete: true` only when every supported object is boxed, enabling
false-positive and precision counts. The manifest helper's `--object
label:x1,y1,x2,y2` and `--boxes-complete` write these, as does the
box-annotation window (`scripts/annotate_boxes.py`) through the same helper
([procedure](../guides/mirror-recognition.md#compare-detectors-on-frame-stills)).
Labels a detector cannot output are reported as unsupported. An entry may not repeat a `present` or `absent` label,
including under another name for the same canonical label (`sofa` and `couch`),
so the per-label table, the totals and a pin count the same checks. The JSON
report has per-detector totals, per-image detections and timing; a one-line
summary per detector and a per-label table go to stderr. Report
schema version 6 records manifest and resolved image SHA-256 values,
decoded and processed dimensions, detector model hashes, runtime versions and
preprocessing parameters, Python/OpenCV/NumPy versions, the Git revision and
tracked-diff hash, conversion path, participating source-file SHA-256 values
and native inference runtime artifact hashes. Each image's `input_path` is the
file's basename only (schema 4 recorded the resolved path), so a report written
on the Frame carries no absolute device path; the entry `id` and `input_sha256`
identify the input. Each image's `input_path_resolution` (schema 6) says which
file was read: `recorded` when the entry's path existed, `beside_manifest` when
it did not and the file of the same basename in the manifest's directory was
used instead ([repeating a recorded run](#repeat-a-recorded-run)), `download`
for a `file` entry. Manifest paths are recorded as given on the command line. It also states annotation coverage explicitly: a
presence-only manifest does not measure absent-label or localization accuracy.
Per detector, a `labels` table holds present (hit, miss,
unsupported), absent (clean, violation, unsupported) and boxes (matched, missed,
unsupported) outcomes per label, and each image carries `label_outcomes`,
`box_outcomes` and `best_below_threshold`: the highest score under the
threshold for each supported expected label. Box recall is in the totals over
all objects (`box_recall`) and per label (`box_recall_by_label`, with
`true_positives`, `false_negatives` and `box_recall`), so the POC object's
recall, the [D6 rule](../poc-scope.md#architecture-decisions-2026-11-07)'s
number, is read from `box_recall_by_label.chair`; the stderr summary line
prints the per-label values after the overall one. The `best_below_threshold`
diagnostic comes from one
extra untimed `detect()` per image at a 0.05 floor and is never scored, so
counts are still taken at the requested threshold. A model's own output filter
can hide lower scores: the pinned MobileNet-SSD network keeps nothing below
0.25, so its `null` means below 0.25. In the table, `best miss` is the highest
such score among images where a present label was missed, and `best clean`
among images where an absent label stayed clean.
Each detector/variant performs one untimed warm-up before measured evaluation.
A dirty-worktree flag means the commit alone does not identify the executed
source; retain the exact checkout diff as well as the report. The diff hash is
a fingerprint, not a copy of the changes.
Private Frame stills belong in ignored `evaluation/local/`; see the
[device procedure](../guides/mirror-recognition.md#compare-detectors-on-frame-stills).

For an authorized existing still, audit actual decoded channels, BGR conversion,
model tensors and inverse geometry without embedding pixel payloads. Variants
are separate from the unchanged baseline, one controlled change at a time:

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --threshold 0.5 --require-input-digests --audit-preprocessing --audit-variant swap-rb \
  --audit-variant gray-replicated --audit-variant native-size --audit-variant mirror-preview \
  --report /tmp/frame-audit.json
```

`mirror-preview` replaces the decoded still with what E61's detector actually
saw: the `mirror_check` preview, which is
`sightcircuit.sources.mirror_device.grayscale_sample` at a 640-pixel bound
(every third pixel of a 1920-pixel frame, integer luma) replicated to three
channels. The variant calls that function itself rather than a copy of it.
`gray-replicated` keeps full resolution and OpenCV's luma, so it does not
reproduce that input.

`--require-input-digests` requires a supplied SHA-256 for every input and
verifies it before inference. Entries added by
`scripts/frame_session_manifest.py --still-report` carry the SHA-256 their
capture report recorded; see
[bounded mirror recognition](mirror.md#bounded-mirror-recognition). Reports distinguish `manifest_verified` from
`computed_only` identity; computing a digest does not establish the original
E61 image identity. `--audit-preprocessing` adds fingerprints, channel
statistics and coordinate transforms, whose resize kind and box inverse come
from the detector's own `preprocessing(name)`; `--audit-variant` is repeatable. The report path must be new. SSD
keeps stretched 300 × 300 input and explicitly centers all three BGR channels;
YOLOX keeps its letterbox transform. Rounded resize mapping uses each realized
axis ratio. Synthetic regressions establish those implementation behaviors,
not the cause of the private E61 TV miss. [E80](../evidence/e080.md) now records
a file-only rerun on the original still, whose digest matched the saved E61
identity and passed `--require-input-digests`. Both models still missed `tv`
at the unchanged 0.5 threshold; YOLOX-Nano's best below-threshold score was
0.427. All audit variants fed identical bytes for this already-grey, small
preview, so they did not explain the miss. One unboxed still is not an accuracy
measurement; the investigation remains open and defaults are unchanged.

### Repeat a recorded run

A manifest written by `scripts/frame_session_manifest.py` records each still
relative to the manifest, so the directory can move as a whole. A hand-written
manifest may record an absolute path instead: the E80 re-run manifest on the
Frame names its still under the pre-move `/home/steamos/framejacker/` path, so
after the checkout's move the path no longer exists ([E86](../evidence/e086.md)).
The evaluator then looks for a file of the same basename in the manifest's own
directory and, when one is there, reads that file, says so on stderr and records
`input_path_resolution: beside_manifest` for the image; a missing recorded path
with no such file is still reported as not found. The fallback changes which
file is read, never what identity means: a supplied `sha256` is verified against
the file actually read, and `--require-input-digests` still needs one for every
entry, so a different file of the same name fails the digest check.

To repeat E80 on the device, run the recorded command unchanged from the
checkout's current location, including `--require-input-digests`, with a new
`--report` path; no manifest or still is edited and nothing is moved. In the
new report, confirm `input_path_resolution` is `beside_manifest` (or `recorded`
if the manifest has since been rewritten with a relative path),
`input_digest_provenance` is `manifest_verified` and `input_sha256` matches the
E80 report's. The repeat is a device run under its own approval; a cloud
test (`tests/test_evaluation.py`) establishes the fallback on a copied still
with a pre-move absolute path, not the device result.

### Pinned seed counts

[`evaluation/seed-expected.json`](../../evaluation/seed-expected.json) pins the
seed counts recorded in
[E73](../status.md#e73-reproducible-preprocessing-audit-and-ssd-channel-correction):
MobileNet-SSD hits 7 of 9 supported presence checks with 2 absent-label
violations, and YOLOX-Nano 12 of 12 with none. `--expect` refuses, before any
inference, a run whose manifest SHA-256, threshold or IoU differs from the
pin's. After evaluation it exits 1 when a pinned detector was not evaluated,
hits fewer present labels, violates more absent labels, has more confusions,
or scores a different number of presence, absence or image checks (a
vocabulary or label-map change). Results better than the pin pass and are
listed for a reviewed pin update; detectors absent from the pin are listed as
unpinned. The verdict goes to stderr and to the report's `expectation` block.
The pin holds counts, never scores, so small floating-point differences between
x86_64 and ARM64 cannot move it: in the pinned run the closest regression
margin was a clean absent label at 0.437 against the 0.5 threshold.

CI's replay job runs the seed comparison with `--expect` and then the suite
with `tests/test_seed_pin.py` pinned against skips, which checks that the committed pin
passes and that swapping two VOC labels in the SSD label map fails it. The
same step runs `tests/test_observer_scene_seed.py`, which pins the observer's
scene statistics on the six seed stills. Seed
results remain a wiring and label-mapping regression check on the runner's
CPU, not Frame evidence, and they do not select a detector.

### Tests that need downloaded assets

Tests that need checksum-pinned downloads (models, `dog.jpg`, seed images) use
`tests/asset_gate.py`. When an asset is absent locally the test skips and names
the missing files and the command that fetches them. The gate checks presence
only: the detectors and the evaluator verify model and seed-image checksums
themselves, so an altered file fails the test instead of skipping it. CI
fetches the assets and runs those modules through
`scripts/run_tests_no_skips.py`, so an absent asset fails CI instead of
skipping there.
