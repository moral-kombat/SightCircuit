# HUD mapping candidate records

Use this reference when inspecting or converting a mapping record. A valid candidate remains unqualified; it does not enable device use.

[Architecture](../architecture.md) · [Qualification method](../hud-qualification.md) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

## Offline candidate records

`sightcircuit/hud/qualification.py` provides `MappingCandidate.from_mapping()`
and `check_context(current, now_ns)` to reject malformed, changed or expired
candidate records. This validates structure and context; it supplies no
production adapter or device authorization. Every accepted candidate remains
`observed_unqualified`, including when every evidence role is filled.

Records and the runtime mask share one contract in `sightcircuit/hud/`: the
native format rule, the eye partition (`geometry.eye_viewports`) and the
per-eye blind-region budget. `MappingCandidate.calibration()` expresses a
candidate as a runtime `Calibration` that is always unvalidated with
`observed_unqualified` provenance, so `HudMask` rejects it. Conditions the
runtime mask cannot yet represent fail closed there: a crop other than the
full frame, a nonzero orientation and more than one copy per eye. The
calibration carries the record's context tokens and lifetime, and every
consumer applies one margin rule; see
[runtime calibration](hud-runtime.md#runtime-calibration-and-one-margin-rule-2026-11-01).

The record carries (version 2 since 2026-11-07; version 1 records are still
read, see [record versions](#record-versions)):

| Field | Required meaning |
| --- | --- |
| `mapping_id`, `schema_version` | Candidate identity and schema version, 1 or 2 |
| `status`, `mapping_qualified` | Exactly `observed_unqualified` and `false`; qualification claims are rejected |
| `context` | Exact `source_id`, `session_id`, `runtime_id`, `timebase_id`, `tracking_origin` and `recenter_epoch` |
| `context.native_format` | Acquired width, height, fourcc, stride, buffer bytes and minimum buffer bytes; keep padded acquisition stride even after copying to a contiguous owned image. In version 2 also `metadata`: the source's other format metadata as an object (for the SteamVR mirror its V4L2 `field`, `colorspace_code`, `format_flags`, `ycbcr_encoding_code`, `quantization_code` and `transfer_function_code`), empty when the source reports none; at most 16 entries, keys up to 64 characters, integer or string values up to 96 characters |
| `context.crop_xyxy`, `orientation_degrees`, `capture_layout` | Explicit native crop, orientation and declared mono/stereo composition; dimensions are insufficient evidence |
| `context.hud_config_sha256` | Digest of all fixed HUD settings, including placement, dimensions and opacity |
| `observed_ns`, `expires_ns` | Bounded monotonic lifetime, at most 300 seconds, in the exact declared timebase; a diagnostic may require an earlier deadline |
| `operating_limits` | Declared yaw/pitch/roll ranges, angular-speed limit and display mode; evidence must establish and runtime monitoring must enforce them before device use. All five are null when `operating_limits` is an unknown term |
| `copies` | Unique copy IDs, declared eye, full native rectangle, margin, sampled drift, localization uncertainty and filter support; declaration is not eye evidence. A drift, localization or filter term is null in every copy when it is an unknown term |
| `evidence` | Role, artifact ID, SHA-256 and kind for each original evidence artifact; a digest establishes identity, not truth or completeness |
| `unknowns` | Optional. Each unknown term (`eye_composition`, `unseen_or_occluded_copies`, `capture_freshness`, `motion_displacement_bound`, the three copy terms or `operating_limits`) mapped to its reason. A null field needs its term here, and a listed field must be null, never 0. Known copy terms still bound the margin from below; an unknown one adds nothing and is reported in `unknown_error_terms`, so the margin stays underived |

<a id="record-versions"></a>

### Record versions

Version 1 pinned the six native-format fields and nothing else, while a real
mirror format carries V4L2 metadata beside them: `CaptureFormat.from_mapping`
kept that metadata, the runtime mask's equality check failed on it, and
`Calibration.context()` refused to describe it
(`hud_capture_metadata_unrepresentable`), so no real mirror frame could ever
match a record. Version 2 (item A6 of the
[architecture plan work order](../live-capture.md#architecture-plan-work-order-2026-11-07),
2026-11-07) makes the metadata part of the record and of every comparison:

- A version 2 `native_format` is the six fields plus the `metadata` object,
  exactly; metadata written beside the six fields is rejected
  (`hud_native_format_fields_invalid`). A version 1 `native_format` is the six
  fields exactly, and a version 1 record with a `metadata` key is rejected the
  same way. Any other `schema_version` is rejected
  (`hud_mapping_cannot_claim_qualification`).
- `CaptureFormat.from_mapping` reads a source's flat format (the metadata
  beside the six fields, as `sources.mirror_device` reports it) and the record
  form alike, and `CaptureFormat.record()` writes the record form. Two formats
  are equal only when every field and every metadata value agrees.
- `check_context(current, now_ns)` compares the current context and the
  record's context in the version 2 form, whatever form either was written in:
  a `--current-context` file may carry the native format as the source
  reports it, as six fields or as a version 2 object. A version 1 record
  therefore matches a frame without metadata only; a real mirror frame still
  fails it, precisely (`hud_mapping_context_changed`), instead of failing on
  the record's shape.
- `Calibration.context()` returns the version 2 context. A version 1 record's
  calibration carries no metadata, so its context is the record's with an
  empty `metadata` object.
- New records are version 2: the observer conversion below and
  `runtime.synthetic.fixture_candidate`. The synthetic 96 × 64 fixture has no
  metadata, so its record's `metadata` is `{}`; the HUD mask still accepts
  synthetic fixtures only, and `HARDWARE_MAPPING` still refuses every
  non-synthetic calibration, metadata or not. The schema report
  (`hud_mapping_schema_check`) names the record's version as
  `record_schema_version`.
- The generated native header is unchanged by this: the bridge draws no mask
  and reads no record.

Cloud checks: `tests/test_hud_calibration.py` (`NativeFormatMetadataTests`,
which pins both record versions, the exact context comparison and the mask's
refusal) and `tests/test_hud_candidate.py`
(`test_mirror_format_metadata_is_carried_into_the_record_and_must_be_declared_exactly`).
The metadata values in those tests are generated, not a Frame reading; no
mirror frame has been checked against a version 2 record on the device.

The offline `scripts/hud_validation_report.py` command checks a candidate against
a separately supplied current context and explicit monotonic time. It never
opens a device or enables output. A valid check returns exit 2 and
`candidate_valid_unqualified`; rejection returns exit 1. Its bounded `ValidationLedger`
checks frame/result identity, timing, mask/eye rejection and original-frame
deadlines from generated or injected telemetry. It retains counters rather
than an unbounded event history. It does not observe native hide events or
enforce declared motion limits. Physical fields remain unverified. Synthetic
HUD runs now feed it; see
[synthetic validation counts](hud-runtime.md#synthetic-validation-counts-and-one-maximum-age-2026-11-01).

```bash
python3 scripts/hud_validation_report.py --check-candidate /tmp/candidate.json \
  --current-context /tmp/current-context.json --now-ns 123456789
```

The two files are local metadata records; the example time must be replaced
with a current value from the record's declared monotonic timebase. These
options were checked against the implemented command's `--help`.

<a id="observer-report-to-candidate-2026-11-01"></a>

## Observer report to candidate

`scripts/hud_validation_report.py --from-observer` builds a version 2
candidate record from one saved `hud_marker_observer.py` report. The
conversion is `sightcircuit/hud/candidate.py`; the command only reads files and
writes the result. Its tests use reports from the real observer and
observation algorithm on generated frames, run in the cloud. No Frame observer
report has been converted, and no accepted G3 observation exists. The later
device attempts in [E83](../evidence/e083.md) stopped at the scene budget before
marker output; those rejected reports cannot supply a mapping candidate.

- **Accepted input.** Only a clean `observed_unqualified` report with exit 2
  converts. It must pass the observer's own worker-report contract
  (`observation.validate_observer_report`, which its supervisor applies), carry
  the observer's expected reason, a captured and cleanly closed mirror read and
  clean marker cleanup, and have no cleanup error or forced termination. It
  must also carry the session tokens the observer recorded (see Context). A
  rejected, cancelled, incomplete or erroneous report, or one whose samples
  disagree with its union or drift, gets a structured rejection with exit 1
  and no candidate file. So does a report whose `baseline`, `bridge` or a
  native object the contract reads is not a JSON object; a test replaces every
  field of a generated report with each other JSON type.
- **Samples.** Read numbers must be consecutive, times must increase and the
  copy count must stay the same. The observer itself now rejects a skipped read
  between marker samples as `observer_sequence_gap`; the conversion also
  rejects a gap in an older report.
- **Margin.** The observer's `footprint_union_native` already includes
  `margin_px`. Each candidate `rect_native` is that union shrunk by the margin
  once, and it must equal the samples' outward-rounded corner bounds, within
  their 0.001 px rounding. The candidate keeps the observer's `margin_px`, so
  its expanded rectangle, the region a mask would exclude, is exactly the
  observer's union. A union that counts the margin twice, omits it or cuts
  into a sampled corner is rejected.
- **Context.** Since 2026-11-03 the five session tokens (`session_id`,
  `runtime_id`, `timebase_id`, `tracking_origin`, `recenter_epoch`) are
  observed, not declared. They come only from the report's `session_tokens`,
  which the observer records with `--record-session-tokens`. A report without
  them, a token the providers could not observe, or a token that changed
  during the run rejects, with no declared fallback
  ([observed session tokens](hud-session.md#observed-session-tokens-for-the-candidate-2026-11-03)).
  `--declared-context` carries only the non-token context fields: source,
  native format, crop, orientation, capture layout and `hud_config_sha256`. A
  context file that carries any session token is rejected
  (`hud_observer_context_declares_session_tokens`), so each token has one
  source. Only the declared `native_format` is checked against the observer's
  report: it must equal the observer's capture format, metadata included. The
  observer's `capture.format` carries the mirror's V4L2 metadata beside the
  six fields, and the record keeps all of it in the version 2 form. The
  declared native format may be written as the source reports it, as six
  fields or as a version 2 object; six fields alone no longer describe a
  mirror format that reports metadata (`hud_observer_format_context_mismatch`).
  `hud_config_sha256`
  must be the current `HudConfig` digest, which the observer's marker evidence
  pins. `declared_eye` is the declared layout's eye viewport that holds the
  expanded copy. A copy outside exactly one viewport, or a declared eye
  without a copy, rejects.
- **Lifetime.** `observed_ns` is the last marker sample's read time and
  `expires_ns` is the first sample's read time plus `--lifetime-seconds`
  (1-300). A lifetime that ends before the last sample rejects. These are the
  observer's monotonic read times, not exposure times.
- **Evidence.** One `observer` entry names the report file and the SHA-256 of
  its exact bytes. Its kind is `synthetic_fixture` when the report came from
  the validation bridge or a synthetic driver, and `device_report` otherwise;
  either way the digest establishes identity, not truth. The other four roles
  stay missing.

What the observer cannot establish is an explicit unknown, null with its
reason, never 0:

| Unknown term | Why it is unknown | Record field |
| --- | --- | --- |
| `eye_composition` | The observer assigns no eye; `declared_eye` is positional | None |
| `unseen_or_occluded_copies` | Finite visible-copy samples cannot rule out an unrecognized, clipped or occluded copy | None |
| `capture_freshness` | A changed clear baseline does not establish mirror or camera freshness | None |
| `motion_displacement_bound` | Drift sampled at up to 5 Hz does not bound unobserved motion | None; `maximum_drift_px` is the sampled drift |
| `localization_error_px` | Corner localization uncertainty was not measured | Null in every copy |
| `filter_support_px` | Compositor and raster filter support were not measured | Null in every copy |
| `operating_limits` | Head pose, angular speed and display mode were neither measured nor monitored | All five limits null |

The result is at best `candidate_valid_unqualified` with exit 2, whatever the
input claims. `MappingCandidate.calibration()` stays unvalidated, `HudMask`
rejects it and no gate changes. Under the [G4 method](../hud-qualification.md#proposed-g4-method) a
candidate with any unknown error term cannot qualify.

```bash
python3 scripts/hud_validation_report.py --from-observer /tmp/observer-report.json \
  --declared-context /tmp/declared-context.json --lifetime-seconds 60 \
  --candidate-output /tmp/candidate.json
python3 scripts/hud_validation_report.py --check-candidate /tmp/candidate.json \
  --current-context /tmp/current-context.json --now-ns 123456789
```

`/tmp/declared-context.json` holds only the six declared fields and must not
carry a session token. `--check-candidate` still compares a complete current
context, all eleven fields including the current run's session tokens, so it
takes a different file. `--candidate-output` is optional and is created
exclusively; an existing file is never replaced. The example paths are local
metadata records, and the time must come from the record's declared timebase.
These options were checked against the command's `--help`.
