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
  version 1 record context, so a candidate's calibration reproduces its
  record's context exactly. Construction checks the tokens, the configuration
  digest, a lifetime of at most 300 seconds, a full-frame crop and
  orientation 0.
- **Native format metadata.** The version 1 record has six native-format
  fields and no V4L2 metadata such as `colorspace_code`, so a candidate's
  calibration carries none. A frame whose format reports extra metadata does
  not equal it, and the mask rejects that frame as
  `hud_capture_format_changed`. Recording the metadata needs a schema change;
  until then the mask fails closed.
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
