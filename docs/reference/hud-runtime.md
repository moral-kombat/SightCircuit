# HUD masking, calibration and expiry

Use this reference when changing native-pixel masking, calibration checks, exclusion counters or stale-result expiry. These contracts are implemented and tested offline; production device use remains gated.

[Architecture](../architecture.md) · [Qualification method](../hud-qualification.md) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

<a id="runtime-calibration-and-one-margin-rule-2026-11-01"></a>

## Runtime calibration and one margin rule

The runtime `Calibration` that `HudMask` applies now comes from exactly two
factories. It carries the context and lifetime of its record, and the mask
fails closed when either no longer holds. Nothing here opens a gate: `HudMask`
still uses synthetic fixtures only, and every gate in `sightcircuit/gates.py`
stays closed. The tests are cloud runs on generated frames and fixture
records, not Frame evidence.

- **Two factories.** `MappingCandidate.calibration()` issues a calibration from
  a schema-checked candidate record, including one built from an observer
  report. `runtime.synthetic.fixture_calibration()` issues one for generated
  frames whose `source_id` starts with `synthetic:`;
  `synthetic_hud_calibration()` is the one for `SyntheticSource`.
  `Calibration(...)` and `dataclasses.replace()` raise `TypeError`, so a
  calibration cannot be hand-built or edited. `validated` is no longer a
  field: it is true only for `synthetic_fixture` provenance. A candidate's
  calibration stays unvalidated, so the mask still rejects it. A test checks
  that no other module calls the factories' private constructor.
- **Carried context.** Each calibration holds its record's `source_id`,
  `session_id`, `runtime_id`, `timebase_id`, `tracking_origin` and
  `recenter_epoch`, the native format (dimensions, fourcc, stride and buffer
  sizes), `crop_xyxy`, `orientation_degrees`, `capture_layout`,
  `hud_config_sha256`, `observed_ns` and `expires_ns`. `context()` returns the
  version 2 record context, so a candidate's calibration reproduces its
  record's context exactly (a version 1 record's, with an empty metadata
  object). Construction checks the tokens, the configuration
  digest, a lifetime of at most 300 seconds, a full-frame crop and
  orientation 0.
- **Native format metadata (version 2, 2026-11-07).** `CaptureFormat` holds
  the six native-format fields and, as sorted pairs, every other key the
  source reports with its format: for the SteamVR mirror the V4L2 `field`,
  `colorspace_code`, `format_flags`, `ycbcr_encoding_code`,
  `quantization_code` and `transfer_function_code`. A version 2 candidate
  record carries them as `native_format.metadata`, so a candidate's
  calibration carries them too, and `HudMask.check_context` compares a frame's
  format with the calibration's exactly: the same six fields and the same
  metadata values, or `hud_capture_format_changed`. A version 1 record carries
  no metadata, so its calibration matches a frame without metadata only, and
  a real mirror frame fails it precisely. `Calibration.context()` no longer
  raises `hud_capture_metadata_unrepresentable`; the record form is written by
  `CaptureFormat.record()`. See
  [record versions](hud-candidates.md#record-versions). This changes no gate:
  the mask accepts synthetic fixtures only, and `HARDWARE_MAPPING` refuses
  every non-synthetic calibration whether or not it carries metadata.
- **Expiry.** The mask requires `observed_ns <= time.monotonic_ns() <
  expires_ns` every time it validates: when it is built, at every
  capture-context admission (when capture publishes a frame, before
  inference, after inference and before each sink) and before it renders
  rows. Otherwise it raises `hud_calibration_expired_or_clock_changed`.
  `CaptureContext` latches that failure, so inference and presentation stop,
  and the engine clears its sinks as the run ends. Each HUD result's deadline
  is also capped at the calibration's `expires_ns`, and the overlay bridge
  receives the capped deadline. Content already shown is therefore withdrawn
  on time even when no new frame arrives. A new calibration never extends an
  earlier result. The clock is the one `Frame.observed_ns` uses.
- **Session tokens.** `CaptureContext` passes the source's declared identity,
  when it is created, and each frame's session metadata to
  `HudMask.check_session`. Wherever the source declares, or a frame reports,
  `session_id`, `runtime_id`, `timebase_id`, `tracking_origin` or
  `recenter_epoch`, the value must equal the calibration's, with the same
  type; `False` is not recenter epoch 0. Once a frame arrives, each token must
  come from one of the two. Otherwise admission fails with
  `hud_<token>_changed` or `hud_<token>_undeclared`, and the failure latches.
  `SyntheticSource` declares the synthetic session (`SyntheticSession`) that
  fixtures pin. A mirror source takes them from read-only providers; see
  [session tokens for the mirror source](hud-session.md#session-tokens-for-the-mirror-source-2026-11-03).
- **View fields.** `HudMask.check_session` also compares `crop_xyxy`,
  `orientation_degrees` and `hud_config_sha256` (`VIEW_FIELDS` in
  `contract.py`) wherever the source declares them or a frame reports them,
  from the first frame on, inside and outside validation runs. Each must equal
  the calibration's value with its type; a crop may be a JSON list or a tuple
  of integers. A mismatch fails with `hud_crop_xyxy_changed`,
  `hud_orientation_degrees_changed` or `hud_config_sha256_changed`, when the
  capture context is created or at admission, where the failure latches. An
  undeclared view field is the calibration's full, upright frame
  and HUD configuration, which is what the mask applies.
- **Reports.** HUD run reports include `calibration_provenance` and
  `calibration_expires_ns`.

### One margin rule

Every consumer applies `margin_valid` in `sightcircuit/hud/contract.py`: a
margin is an integer from `max(8, ceil(sum of measured error terms) + 2)` to
128 pixels. These are policy bounds; qualification still requires measured
error terms.

| Consumer | Current bound in native pixels |
| --- | --- |
| Runtime mask (`HudMask`) | 8–128; it has no measured error terms |
| Marker observer (`ObserverConfig`) | 8–128 |
| Candidate record (`MappingCandidate.from_mapping`) | `max(8, ceil(terms) + 2)` through 128 |
| Observer report to candidate (`candidate.py`) | 8–128 |

Step 4 of the [G4 method](../hud-qualification.md#proposed-g4-method) derives the margin from an
error budget: corner measurement uncertainty, raster/filter support and the
displacement between observations. Measured terms set the minimum. An unknown
term adds nothing to the structural check and stays a named unknown, which
qualification rejects. The 2 px guard and 8 px floor are policy values, not
measurements. The runtime mask sees no error terms, so it applies the floor.
The 128 px ceiling bounds each blind region before the per-eye quarter budget
applies. The native bridge draws no mask, so it does not duplicate this rule.

The synthetic fixture has an 8 px margin. Its footprint is
`(67, 42, 88, 56)` and its masked region `(59, 34, 96, 64)` in the 96x64
frame. Tests check that the mask, the observer and the record accept exactly
the same margins, and that each of them, and the observer-report conversion,
follows the contract when its bounds change. From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_hud_calibration tests.test_hud_mask \
  tests.test_hud_qualification tests.test_hud_candidate tests.test_capture_context
```

<a id="synthetic-validation-counts-and-one-maximum-age-2026-11-01"></a>

## Synthetic validation counts and one maximum age

Synthetic HUD runs feed a bounded `ValidationLedger`. They check software
contracts using generated input; they do not observe physical headset expiry
or open a production gate.

With a HUD stage, `pipeline.hud_checks` records frames checked, footprint pixels
left non-zero after zeroing and before filling/resizing, mask-overlap rejections
and eye-boundary rejections. Without a HUD stage it is null. A non-zero pixel
count stops the run before inference with `hud_mask_zeroing_failed`.

The runtime drops a detection touching the mask or crossing an eye boundary,
so it cannot be presented. Validation is stricter: any such detection fails the
whole run, including one whose result arrived too late to present. Both use
`geometry.exclusion`, with mask overlap checked first.

`runtime.live.run_pipeline` installs a `ValidationSink` before the presenting
sink. It checks context, matching frame/result identity, sequence, timing and
original-frame deadlines. It uses a fixture candidate with explicit unknown
observation terms. An invalid result stops the run before presentation.

| Device-template counter | Synthetic source |
| --- | --- |
| `frames_checked` | `hud_checks.frames_checked` |
| `native_mask_zeroing_failures` | `hud_checks.mask_zeroing_failures` |
| `hud_footprint_detections` | `hud_checks.mask_overlap_rejections` |
| `mask_and_eye_boundary_rejections` | Mask overlap plus eye-crossing rejections |
| `context_invalidations` | Latched capture-context failure plus ledger context failure |
| `frame_result_identity_failures` | Ledger identity/order rejection |

A validation rejection produces `validation_failed`, exit 1, when it is the
run's failure. Existing errors, `cleanup_failed` and cancellation keep their
own status/reason; cancellation keeps exit 130. `g5_software.status` is
`checked_unqualified` for a clean completed run, `failed` when validation
fails, `incomplete` otherwise, and `not_run` with null counts before validation.
Reports include the failure reason, configured bounds,
`evidence_kind: synthetic_fixture` and `physical_expiry_verified: false`.

`MAX_DISPLAY_AGE_NS` is 1,000 ms and is the shared maximum and default for HUD
results. The deadline begins at the original frame's observation time; neither
inference completion nor retransmission renews it. A shorter configured age
applies to the ledger too. Calibration expiry can shorten the deadline further.
`Pipeline` rejects a HUD stage with a greater maximum age.

The bridge enforces its side of this in two modes only, `--validate-stdin`
and the real-output [`--fixed-hud`](overlay.md#fixed-hud-mode-work-order-item-l7-2026-11-16)
(work-order item L7, 2026-11-16, cloud-checked against a fake runtime only).
There it takes the tile down itself within one 20 ms input poll of the
deadline and writes `frame_expired` with the time the hide returned. In
`--fixed-hud` every frame is an [observed frame](overlay.md#the-observed-frame-opcode-6)
carrying its identity and its source's observation time, so the bridge refuses
a deadline later than `observed_ns + MAX_DISPLAY_AGE_NS`: a retransmission
changes nothing, and only a newer observation renews a deadline. A frame that
arrives expired, or whose upload returns after its deadline, is never shown.
Every other real-output mode still refuses a frame with a deadline, Python's
`LIVE_HUD_OUTPUT` check still refuses to send one to a real bridge, and the
Frame's bridge is rebuilt with the mode only under a device approval. That
`observed_ns` is truly the source frame's capture time stays the producer's
rule; the bridge can only hold it to the number it sends.

The default-detector synthetic run now passes after the neutral-fill change
below. The earlier zero-fill failure and its measurement details remain in
[the historical analysis](../history/hud-qualification-2026-11-07.md#synthetic-validation-counts-and-one-maximum-age-2026-11-01).

<a id="neutral-mask-fill-2026-11-03"></a>

## Neutral mask fill

`HudMask.prepare_counted` copies the native frame, zeroes each footprint plus
margin, and counts any surviving footprint pixels before resizing. It then
replaces each region with one neutral color: the rounded per-channel mean of
pixels in an 8-pixel surrounding ring, inside the same eye and outside every
masked region. Only the zeroed copy is read. Integer arithmetic makes the
result deterministic across CPUs.

This preserves the exclusion contract: changing any HUD pixel must leave the
detector input unchanged. The fill cannot read another eye or any masked
region. Detections that touch the masked region still fail a validation run;
neutral fill does not weaken that rule.

The default `ring_mean` fill removed a synthetic artifact where MobileNet-SSD
recognized a black-filled mask as a TV. Cloud evaluation compared six fills
on uniform frames and six public seed images. It supports this implementation
choice, not a claim about unseen Frame scenes. Full methods, result tables,
costs and limitations are retained in [E78](../evidence/e078.md) and the
[historical evaluation](../history/hud-qualification-2026-11-07.md#neutral-mask-fill-2026-11-03).

`scripts/evaluate_mask_fill.py` compares fills without changing the runtime.
Its library is never imported by the live path. The
[development procedure](../development.md#hud-mask-fill-evaluation) owns its commands.

## Row selection

A HUD row (`HudRow`) is a sanitised name, a score and an approximate view
direction; it is never a position. Two selectors produce rows, and both end in
the same hygiene, which `sightcircuit/hud/contract.py` owns:

- `row_label(label)` keeps ASCII letters, digits, spaces, `_` and `-`, cuts
  the result to 20 characters (`ROW_LABEL_CHARS`) and strips it. A label with
  nothing left gets no row.
- `select_rows(rows, limit)` orders rows by descending score, then label, then
  direction, keeps the first of each `(label, direction)` pair and stops at
  `limit` rows (`HudConfig.rows`, 3 in `fixed-edge-hud-v1`).

`HudMask.select` is the masked selector for mirror-style input: it drops a box
that touches a masked region or crosses an eye, and takes the direction from
the box centre inside its own eye.

`camera_rows(detections, frame_shape, config, *, threshold, directions=True,
orientation="identity")` in `sightcircuit/hud/camera.py` is the mask-free
selector for camera detections
([work order item L6](../live-capture.md#camera-display-work-order-adopted-2026-11-16)).
It uses no calibration, footprint or eye split, and a test pins that the module
imports only `Detection` and the shared row names. A detection gets a row when
its score is from `threshold` to 1 and its box is finite, nonempty and inside
the frame; the caller passes the threshold (`POC_THRESHOLD`, 0.5, for the proof
of concept). At most 128 detections are accepted per frame.

- **With directions.** The direction is the 3x3 cell of the box centre over
  the full frame: a centre fraction below 0.4 or above 0.6 on an axis leaves
  the middle cell, and exactly 0.4 or 0.6 stays in it. The nine cells are the
  eight directions and the centre `(0, 0)`, which the tile draws as a dot.
- **Names only.** With `directions=False` every row carries `NO_DIRECTION`
  (`None`), and rows deduplicate by name alone. `draw_tile` draws such a row's
  name in its usual place with no arrow and no dot; rows that carry a
  direction draw exactly as before, which pinned tile digests check. The
  tile's fixed header line still reads "View direction (approx.)" on a
  names-only tile; changing that is left to the runner item.
- **Orientation.** Whether the camera image's axes match the wearer's view is
  not measured; a device step decides it. `orientation` names the operation
  that would make the image upright: `identity`, `flip_horizontal`,
  `flip_vertical`, `rotate_180`, `rotate_90_cw`, `rotate_90_ccw`, `transpose`
  or `transverse` (`ORIENTATIONS`). It turns the image cell into the view
  direction. Identity is the default, not a finding.

`camera_boxes` stands beside `camera_rows` for the box panel: positions,
where a row has only a direction. It shares `row_label` and the threshold
rule and has its own page, the [box panel reference](box-panel.md); no stage
or command selects it yet, and the strip's geometry, renderer and digests are
unchanged by it.

The selector opens no gate and displays nothing. A runner that shows its rows
remains subject to `sightcircuit/gates.py`; the stage that sends its tiles to
the bridge is the [camera HUD stage](camera-hud.md). Its tests
(`tests/test_hud_camera_rows.py`) are cloud runs on generated detections, not
Frame evidence.
