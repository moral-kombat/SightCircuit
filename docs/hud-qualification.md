# Proposed HUD mapping qualification and device validation

This is the proposed G4 method and the G5/G6 report design for the
[fixed edge HUD](poc-scope.md). It records no new device result. A schema-valid
record, an edited JSON file, or a clean marker observation cannot qualify a
mapping. Production live input, hardware mappings and real expiring HUD output
remain closed. The [acceptance gates](live-capture.md#edge-hud-acceptance-gates)
and [device procedure](on-device.md#bounded-edge-hud-calibration-checkpoint)
remain authoritative.

## Evidence that qualification must add

The existing marker observer is G3: at native resolution it reports visible
tagged copies, unions, drift and conservative candidate margins. It does not
assign eyes, prove capture freshness or find every unrecognizable/occluded
copy. The historical V4 device attempt ran on Frame and was rejected; it did
not establish an accepted G3 mapping observation. [E75](evidence/e075.md) now
records its recovered evidence. Successful observations would still remain
`observed_unqualified`, exit 2, including when later evidence is added beside
their reports. Keep original reports and digests unchanged.

G4 requires two independent kinds of evidence. First, an inspected runtime and
mirror composition contract must identify which eye or eyes are captured, the
crop/orientation, every compositor placement of this overlay, and the conditions
under which copies can appear, disappear, move or be occluded. Second, an
approved bounded device trial must observe that declared composition and bound
its footprint over the intended operating envelope. Finite observations alone
cannot establish that an unobserved copy is impossible.

The current both-eye marker cannot resolve eye identity by itself. A proposed
follow-up uses a separately reviewed eye-selective marker sequence, only if the
runtime exposes and documents that selection for the exact capture path. Match
each eye-specific phase to independently recorded compositor settings and to
native mirror copies. A physical eye cover or a wearer saying "one tile" does
not identify the captured eye. If eye-selective output is unsupported, require
an equally direct compositor/source contract and independent evidence; otherwise
stop at G3. Do not infer stereo side-by-side from image dimensions.

## Proposed G4 method

1. **Pin and preflight.** Complete G2's lifecycle investigation and offline
   checks. Obtain approval for a specifically scoped G4 trial, separate from
   G3. Pin the checkout, bridge, Python modules and native dependencies by
   SHA-256; record SteamOS/SteamVR, source node identity, source/session identity,
   tracking origin and recenter generation. Verify independent SSH/abort and
   normal passthrough before starting. Preserve the provider freeze.
2. **Establish composition.** Review the capture/compositor contract above.
   Enumerate all allowed display states, including dashboard visibility,
   clipping and occlusion. Exclude unsupported states from the operating
   envelope and make changes observable at runtime. Use the existing opaque
   tagged marker at the exact HUD geometry; introduce an eye-specific phase
   only through a separately reviewed diagnostic extension. The marker and
   eventual HUD must have identical full opaque footprint, placement, scale,
   opacity and dashboard policy.
3. **Observe each allowed state.** Reuse the observer's native-pixel matching,
   orphan-tag rejection, strict count/orientation checks and bounded samples.
   Record baseline, marker phases and transitions. For every declared eye/copy,
   keep native corner samples and the union across the predeclared range of
   head motion and display states. A fully occluded, clipped, unrecognized,
   overlapping or unexplained copy is a rejection, not an inferred location.
   A dropped frame, changed copy count, ambiguous assignment or geometry
   outside the candidate mask also rejects the trial.
4. **Derive the mask.** Take the union of every full background, text, arrow,
   antialiased edge and compositor copy in each qualified viewport. Expand
   outwards at native resolution by a documented error budget: corner
   measurement uncertainty, raster/filter support, and the maximum possible
   displacement between observations under the operating limits. The current
   observer's default 16 px margin and maximum 8 px sampled drift are candidate
   settings, not a proof of that displacement. Its 5 Hz sampling cannot bound
   unobserved rapid motion without additional evidence. Reject when any error
   term is unknown, when the margin escapes the qualified viewport, or when
   the blind region exceeds the implementation's permitted mask area.
5. **Test invalidation.** Verify, using injected changes offline and approved
   device transitions where needed, that any source/session/runtime, tracking
   origin/recenter, native dimensions/stride/format, crop/orientation, eye
   composition, HUD configuration or operating-limit change stops inference
   and presentation. An expired calibration stops the run. Previous content
   keeps its original frame-based expiry; neither a fresh mapping nor a
   heartbeat renews it. Requalification starts from a new baseline.
6. **Review the evidence.** A reviewer checks original reports, artifact and
   input digests, composition evidence, operating limits and margin derivation.
   Record separately the software result, process reaping, API cleanup and
   wearer-confirmed physical behavior. Qualification is a reviewed evidence
   decision for the pinned session and limits, never a mutable `validated`
   flag. Keep G5 and production acceptance pending.

The narrow initial envelope is one pinned runtime/session, one verified mirror
configuration, fixed HUD geometry, no recenter and only the measured/monitored
motion and display states. No unmeasured angular-speed or pose limit is implied.
If the necessary limit cannot be measured and monitored by available tooling,
that is a missing prerequisite for qualification. A runtime restart, source
replacement, new session, unsupported dashboard transition or loss of required
metadata invalidates the mapping immediately. A bounded monotonic lifetime
must end no later than the approved trial; it cannot survive a reboot or be
extended by rewriting a timestamp.

## Offline candidate records

`framejacker/hud/qualification.py` provides `MappingCandidate.from_mapping()`
and `check_context(current, now_ns)` to reject malformed, changed or expired
candidate records. This validates structure and context; it supplies no
production adapter or device authorization. Every accepted candidate remains
`observed_unqualified`, including when every evidence role is filled.

Records and the runtime mask share one contract in `framejacker/hud/`: the
native format rule, the eye partition (`geometry.eye_viewports`) and the
per-eye blind-region budget. `MappingCandidate.calibration()` expresses a
candidate as a runtime `Calibration` that is always unvalidated with
`observed_unqualified` provenance, so `HudMask` rejects it. Conditions the
runtime mask cannot yet represent fail closed there: a crop other than the
full frame, a nonzero orientation and more than one copy per eye. The
calibration carries the record's context tokens and lifetime, and every
consumer applies one margin rule; see
[runtime calibration](#runtime-calibration-and-one-margin-rule-2026-11-01).

The version 1 record carries:

| Field | Required meaning |
| --- | --- |
| `mapping_id`, `schema_version` | Candidate identity and schema version 1 |
| `status`, `mapping_qualified` | Exactly `observed_unqualified` and `false`; qualification claims are rejected |
| `context` | Exact `source_id`, `session_id`, `runtime_id`, `timebase_id`, `tracking_origin` and `recenter_epoch` |
| `context.native_format` | Acquired width, height, fourcc, stride, buffer bytes and minimum buffer bytes; keep padded acquisition stride even after copying to a contiguous owned image |
| `context.crop_xyxy`, `orientation_degrees`, `capture_layout` | Explicit native crop, orientation and declared mono/stereo composition; dimensions are insufficient evidence |
| `context.hud_config_sha256` | Digest of all fixed HUD settings, including placement, dimensions and opacity |
| `observed_ns`, `expires_ns` | Bounded monotonic lifetime, at most 300 seconds, in the exact declared timebase; a diagnostic may require an earlier deadline |
| `operating_limits` | Declared yaw/pitch/roll ranges, angular-speed limit and display mode; evidence must establish and runtime monitoring must enforce them before device use. All five are null when `operating_limits` is an unknown term |
| `copies` | Unique copy IDs, declared eye, full native rectangle, margin, sampled drift, localization uncertainty and filter support; declaration is not eye evidence. A drift, localization or filter term is null in every copy when it is an unknown term |
| `evidence` | Role, artifact ID, SHA-256 and kind for each original evidence artifact; a digest establishes identity, not truth or completeness |
| `unknowns` | Optional. Each unknown term (`eye_composition`, `unseen_or_occluded_copies`, `capture_freshness`, `motion_displacement_bound`, the three copy terms or `operating_limits`) mapped to its reason. A null field needs its term here, and a listed field must be null, never 0. Known copy terms still bound the margin from below; an unknown one adds nothing and is reported in `unknown_error_terms`, so the margin stays underived |

The offline `scripts/hud_validation_report.py` command checks a candidate against
a separately supplied current context and explicit monotonic time. It never
opens a device or enables output. A valid check returns exit 2 and
`candidate_valid_unqualified`; rejection returns exit 1. Its bounded `ValidationLedger`
checks frame/result identity, timing, mask/eye rejection and original-frame
deadlines from generated or injected telemetry. It retains counters rather
than an unbounded event history. It does not observe native hide events or
enforce declared motion limits. Physical fields remain unverified. Synthetic
HUD runs now feed it; see
[synthetic validation counts](#synthetic-validation-counts-and-one-maximum-age-2026-11-01).

```bash
python3 scripts/hud_validation_report.py --check-candidate /tmp/candidate.json \
  --current-context /tmp/current-context.json --now-ns 123456789
```

The two files are local metadata records; the example time must be replaced
with a current value from the record's declared monotonic timebase. These
options were checked against the implemented command's `--help`.

## Observer report to candidate (2026-11-01)

`scripts/hud_validation_report.py --from-observer` builds a version 1
candidate record from one saved `hud_marker_observer.py` report. The
conversion is `framejacker/hud/candidate.py`; the command only reads files and
writes the result. Its tests use reports from the real observer and
observation algorithm on generated frames, run in the cloud. No Frame observer
report has been converted, and no accepted G3 observation exists.

- **Accepted input.** Only a clean `observed_unqualified` report with exit 2
  converts. It must pass the observer's own worker-report contract
  (`observation.validate_observer_report`, which its supervisor applies), carry
  the observer's expected reason, a captured and cleanly closed mirror read and
  clean marker cleanup, and have no cleanup error or forced termination. A
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
- **Context.** Only the native format is checked against the observer's
  capture. Source, session, runtime, timebase, tracking origin, recenter epoch,
  crop, orientation and capture layout come from `--declared-context` as
  declarations. `hud_config_sha256` must be the current `HudConfig` digest,
  which the observer's marker evidence pins. `declared_eye` is the declared
  layout's eye viewport that holds the expanded copy. A copy outside exactly
  one viewport, or a declared eye without a copy, rejects.
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
rejects it and no gate changes. Under the [G4 method](#proposed-g4-method) a
candidate with any unknown error term cannot qualify.

```bash
python3 scripts/hud_validation_report.py --from-observer /tmp/observer-report.json \
  --declared-context /tmp/declared-context.json --lifetime-seconds 60 \
  --candidate-output /tmp/candidate.json
python3 scripts/hud_validation_report.py --check-candidate /tmp/candidate.json \
  --current-context /tmp/declared-context.json --now-ns 123456789
```

`--candidate-output` is optional and is created exclusively; an existing file
is never replaced. The example paths are local metadata records, and the time
must come from the record's declared timebase. These options were checked
against the command's `--help`.

## Runtime calibration and one margin rule (2026-11-01)

The runtime `Calibration` that `HudMask` applies now comes from exactly two
factories. It carries the context and lifetime of its record, and the mask
fails closed when either no longer holds. Nothing here opens a gate: `HudMask`
still uses synthetic fixtures only, and every gate in `framejacker/gates.py`
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
  fixtures pin.
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
- **Reports.** HUD run reports add `calibration_provenance` and
  `calibration_expires_ns`. No existing field changed.

### One margin rule

Every consumer applies `margin_valid` in `framejacker/hud/contract.py`: a
margin is an integer from `max(8, ceil(sum of measured error terms) + 2)` to
128 pixels.

| Consumer | Before | Now |
| --- | --- | --- |
| Runtime mask (`HudMask`) | 2-128 | 8-128; it has no error terms |
| Marker observer (`ObserverConfig`) | 8-128 | 8-128, from the contract |
| Candidate record (`MappingCandidate.from_mapping`) | `max(8, ceil(terms) + 2)`-128 | The same rule, from the contract |
| Observer report to candidate (`candidate.py`) | 8-128 | 8-128, from the contract |

Step 4 of the [G4 method](#proposed-g4-method) derives the margin from an
error budget: corner measurement uncertainty, raster/filter support and the
displacement between observations. The candidate rule already did that. The
measured terms set the minimum. An unknown term adds nothing and stays a named
unknown, which the method rejects. The 2 px guard and the 8 px floor are the
values the observer and the version 1 schema already used. This change keeps
them and does not re-derive them; they are policy values, not measurements.
The runtime mask was the outlier. Its 2 px floor was below any margin an
observer report or a candidate record can carry, so it would have accepted a
mapping that no evidence path can produce. The mask sees no error terms, so it
applies the floor. The 128 px ceiling bounds each blind region before the
per-eye quarter budget applies. The rule has no native copy: the bridge draws
no mask, so `tools/hud_contract.hpp` is unchanged.

The synthetic fixture's margin moved from 3 to 8 px. Its footprint is now
`(67, 42, 88, 56)` and its masked region `(59, 34, 96, 64)` in the 96x64
frame. Tests check that the mask, the observer and the record accept exactly
the same margins, and that each of them, and the observer-report conversion,
follows the contract when its bounds change. From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_hud_calibration tests.test_hud_mask \
  tests.test_hud_qualification tests.test_hud_candidate tests.test_capture_context
```

## Synthetic validation counts and one maximum age (2026-11-01)

Synthetic HUD runs now feed the `ValidationLedger` and report the G5 software
counts that the [device report template](templates/hud-device-validation-report.json)
names. The tests are cloud runs on generated frames. They are not Frame
evidence, they observe no device, native hide or physical expiry, and every
gate in `framejacker/gates.py` stays closed.

- **Engine counters.** With a HUD stage, the report's `pipeline` block gains
  `hud_checks`; without one it is null. It counts `frames_checked`, the
  footprint pixels still non-zero after masking and before the resize
  (`unmasked_footprint_px`, which should be 0, and `mask_zeroing_failures`,
  the frames that had any), and the boxes the stage dropped
  (`mask_overlap_rejections` and `eye_crossing_rejections`). The mask counts
  pixels in the regions of its geometry rule (`mask_regions`), not in the
  regions it zeroed. A frame with any such pixel stops the run before the
  detector sees it, with `hud_mask_zeroing_failed`. Each result carries its own
  frame's checks in `HudOutput.checks`, and `last_result.hud_checks` reports
  them.
- **One event, two meanings.** The runtime mask drops a box that touches a
  masked region or does not lie inside exactly one eye, so it is never
  presented, and counts it. In a validation run the ledger also records that
  box and invalidates the whole run. Both apply one rule,
  `geometry.exclusion`, which checks mask overlap first; the ledger used to
  check the eye boundary first and so named a box failing both differently.
  Outside validation, for example in the mirror recognizer, the box is only
  dropped. A validation run with any such box fails.
- **Ledger feed.** `runtime.live.run_pipeline` with a HUD stage puts a
  `ValidationSink` (`framejacker/hud/validation.py`) ahead of the presenting
  sink. Its candidate is `synthetic.fixture_candidate(calibration)`: the
  fixture calibration's context, lifetime and footprints as an unqualified
  version 1 record. Every observation term is an explicit unknown, and the one
  `synthetic_fixture` evidence entry is the digest of those values. For each
  result the sink records the context the run observes, the frame and the
  sequence of the frame the stage masked, the observation, completion and
  deadline times, and every valid native box, dropped ones included. The
  observed context is the source's declared identity and view fields, any
  session token or view field the frame reports, and the frame's native
  format; an undeclared HUD digest is the stage configuration's. A rejected
  result raises in the sink, so the run stops and the presenting sink never
  receives it. A declared crop, orientation or HUD digest that differs from
  the calibration already stops the run at the mask, before the source opens,
  and a frame that reports one fails its first admission.
- **Run status.** The run ends `validation_failed` with exit 1 when the ledger
  invalidates it, or when the engine counts a zeroing failure, a mask overlap
  or an eye crossing. That includes a box whose result was too late to
  present, which the ledger never sees. `validation_failed` replaces only a
  run that completed, saw no frames or stopped on that same rejection. A run
  that failed on its own keeps its status, exit code and reason: a
  capture-context failure stays `error` and is counted, and `cleanup_failed`
  and `cancelled` (exit 130) stay too. `g5_software.status` is then `failed`
  and its `failure_reason` names the validation failure.
- **The mask itself can be detected.** In a cloud probe, generated 96x64
  frames of each odd uniform gray level were masked with the fixture
  calibration and passed to MobileNetSSD at threshold 0.5. On 93 of the 127
  levels it reported the zeroed region itself as `tvmonitor`, with confidence
  0.51-0.81; the same frames without the mask gave no detection. The runtime
  drops that box, so it is never presented, but a validation run fails on it.
  `live_pipeline.py --synthetic-check --synthetic-hud`, with or without
  `--validate-overlay`, therefore now ends `validation_failed` with exit 1 and
  clean cleanup within its first few processed frames. A box touching the mask
  cannot tell this artifact of a black-filled mask from feedback of HUD pixels.
  Whether the G5 diagnostic should treat the two differently, or fill the mask
  differently, is an open design question; this change keeps the rule strict.
  The same artifact could fail any validation run that uses this detector, a
  device G5 run included, so the question must be settled before one. The
  [G1 rerun](live-capture.md#edge-hud-acceptance-gates) instead passes
  `--detector fixture`, which reports no detections and loads no model; that
  form checks the mask, the ledger and the transport, and ends `completed`
  with exit 0 and `g5_software.status` `checked_unqualified`. This is not a
  Frame result.

The report adds `validation_ledger`, the ledger's own report with
`failure_kind` and `max_age_ns`, and `g5_software`:

| Template count | Synthetic run value |
| --- | --- |
| `frames_checked` | `hud_checks.frames_checked` |
| `native_mask_zeroing_failures` | `hud_checks.mask_zeroing_failures` |
| `hud_footprint_detections` | `hud_checks.mask_overlap_rejections` |
| `mask_and_eye_boundary_rejections` | Mask overlap plus eye-crossing rejections |
| `context_invalidations` | 1 for a latched capture-context failure, plus 1 for a ledger context failure |
| `frame_result_identity_failures` | 1 when the ledger rejected a result's frame identity or order |

`g5_software.status` is `checked_unqualified` for a clean completed run,
`failed` when any failure count is non-zero or the ledger invalidated the run,
`incomplete` otherwise, and `not_run` with null counts when validation never
started. The block also carries `failure_reason`, the run's `max_age_ms`,
`maximum_processing_fps` and `maximum_duration_seconds`,
`evidence_kind: synthetic_fixture` and `physical_expiry_verified: false`. The
template's device-only fields (`diagnostic_build_sha256`, `expiry_cases` and
`raw_reports`) have no synthetic value. A test parses the template and checks
that its counts are exactly the ones the synthetic report provides. The field
names already matched, so the template is unchanged.

**One maximum age.** `MAX_DISPLAY_AGE_NS` and `MAX_DISPLAY_AGE_MS` (1,000 ms) in
`framejacker/hud/contract.py` are the single cap and now also the default. The
ledger defaulted to 750 ms with no recorded derivation. The pipeline, the
mirror recognizer, the native bridge, this design (`observed_ns + max_age_ns`,
at most 1,000 ms) and the template use 1,000 ms. No smaller operating default
is justified, so none is defined. The ledger defaults to the cap; `run_pipeline`,
`live_pipeline.py` and `mirror_recognize.py` take theirs from the contract; and a
synthetic run's ledger uses the run's own maximum age. `Pipeline` itself
refuses a HUD stage with a longer maximum age (`hud_max_age_exceeds_contract`),
so no caller can give a HUD result a longer lifetime; without a HUD stage the
engine keeps its general bounds. A test checks that every consumer and the
template agree. From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_hud_validation tests.test_hud_mask \
  tests.test_hud_qualification tests.test_live_pipeline tests.test_pipeline
```

## Separately scoped device validation

G5 needs real output to test exclusion and physical expiry, while production
output requires G4/G5. Resolve this by a separately scoped diagnostic path,
default off and distinct from the production CLI. This is a design, not an
implemented device command. It needs its own review, pinned build and one-run
authorization before use; the current production rejection paths stay intact.

The proposed diagnostic consumes only the reviewed G4 mapping and exact
context. It has no generic frame-file/stdin output mode and cannot be enabled
by changing a mapping JSON flag. A one-run scope binds the diagnostic version,
mapping/evidence digests, source/session, detector/configuration, maximum
duration and absolute deadline. Reject unknown fields, mismatched context,
expired scope or attempts to reuse a consumed run identifier. These controls
bound scope; they do not substitute for the evidence review.

Use a generated opaque HUD tile with a diagnostic identifier plus controlled
names/arrows, and the same masking/rendering code as production. The detector
receives only a native-resolution owned image after the union mask is applied,
then the normal resize. The test checks every copied pixel in the mask is
zero before resize and rejects boxes touching the mask or eye boundary.
Record the checks as counts, frame/result identities and context hashes; keep
raw private images local unless separately authorized. Count post-mask zeroing
failures and HUD-footprint detections separately. A detector failing to recognize
unmasked HUD text cannot by itself prove exclusion.

For expiry, submit one frame with `expires_ns` no later than its original
`observed_ns + max_age_ns` (maximum 1,000 ms). Stop supplying results and test
inference stall, continued settings heartbeats and retransmission of the same
result. Later cases may exercise source loss and supervisor loss only if their
specific fault scope and recovery prerequisites are approved. A separate native
timer hides/clears content independent of inference and partial reads. Log
observation, submit request/return, expiry request/return and cleanup times in
the declared monotonic domain. API timestamps alone do not establish physical
disappearance.

Physical expiry needs an independent observation with stated time resolution:
for example, an approved external observer/recording synchronized to an emitted
test cue, or a wearer report explicitly limited to its precision. Retain the
observation locally with its digest and consent scope. If clock alignment or
measurement uncertainty cannot show disappearance by the declared bound,
record physical expiry as unverified. Never treat `HideOverlay` return,
`DestroyOverlay` return, process reaping or a missing panel in a composited
mirror as physical headset recovery.

The diagnostic is bounded to at most 20 seconds, at most 5 inference inputs per
second, one pending frame and one HUD result, with a separate guardian and
independent SSH/SIGTERM abort. Any context change, mapping expiry, pixel check
failure, unexpected copy, native error or wearer concern stops it. No automatic
source switching, service restart or recovery workaround is part of a pass.
Ask the wearer about tile disappearance, passthrough, tracking and green input
during the trial and for 30 seconds afterward. A failed run is preserved and
investigated before proposing a changed run.

## Reports and G6 acceptance

The [device report template](templates/hud-device-validation-report.json)
separates G3/G4 provenance, software exclusion/expiry, physical observations and
G6 acceptance. It is an unexecuted worksheet; empty/null fields are unknown.
Filling it does not qualify a mapping, enable output or grant device approval.
Keep original tool reports and an artifact manifest beside it, with relative
paths and SHA-256 values. Record execution time, exact source/build, platform,
runtime, procedure, result and limitations. The coordinator alone assigns a
numbered evidence entry.

After G2–G5 pass, a separately approved G6 run uses one object class supported
by the evaluated detector and a wearer-agreed approximate-arrow tolerance.
Record at least 60 seconds with head turns, source/result identities, input
age, inference and processing times (the worksheet's `g6_acceptance.latency`
names each statistic as a field of the run report, such as
`pipeline.input_age_ms.p95` in milliseconds), replaced/stale/rejected counts,
mapping validity, expiry events and software cleanup. Confirm no feedback from
the entire mask, expiry when the object leaves view, and normal passthrough and
tracking during and for 30 seconds afterward. G6 is not accepted from an ARM64
build, synthetic run or a report with missing physical evidence.

## Marker stop path

The next G3 attempt requires a changed marker-stop path with regression
coverage. That change was made on 2026-11-01 and is covered by cloud tests
only. The native marker mode ignores stdin. A requested stop, such as an
observer rejection, therefore closes stdin and waits for the bridge's own
display deadline plus two seconds. Until then the marker stays up, at most
`--seconds`. A V4-shaped frame-3 tag/bit rejection, run through the real
validation bridge, worker and guardian, now ends with clean software cleanup
and no forced termination. That run used generated input; it is not Frame
evidence. Ctrl-C, SIGTERM, a ready timeout or an overrun still escalates to
SIGTERM, then SIGKILL. That stop is reported as forced, with `cleanup_ok: false`.
Bridge reports add `stop_requested` and `escalated`. If the guardian is killed,
the supervisor now ends and reaps the worker group itself, and still reports
`cleanup_ok: false`. This does not explain the green input, the tag mismatch or
physical recovery. The [lifecycle review](lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01)
has the details.

## Next device checkpoint

G4 remains unqualified: the exact eye/compositor capture contract, evidence
ruling out additional or occluded copies, measured motion/filter error budget,
monitorable operating limits and a reviewed device mapping are missing. G5
also needs the separately reviewed diagnostic implementation and independent
physical-expiry observation method. [E75](evidence/e075.md) now explains V4's
early software termination: the frame-3 tag/bit rejection enters observer cleanup, and the writerless marker
client immediately requests SIGTERM. Native cleanup APIs report success/exit 0;
the Python close path retains forced-termination intent and overall cleanup
false. The recovered 10,686-byte report matches its original SHA-256 and all
ten source pins match. There was no outer timeout. This is separate from the
still-unresolved green-input mechanism, tag mismatch and physical recovery, so
G2 remains open; the latent packet fix is not their demonstrated cause.

The remaining evidence action is inspection of existing camera/compositor/
Arcturus logs around 2026-10-24 at about 22:48 EDT (2026-10-25 02:48 UTC), with
exact runtime versions and the wearer recovery timeline. The original report
and software timeline need not be recovered again. The changed marker-stop
path now has focused cloud regressions ([marker stop path](#marker-stop-path));
physical recovery requires a later, separately approved bounded validation
with independent abort and a wearer.

The single next proposed device checkpoint is a revised **G3 marker-observer
attempt**, after G2/recovery prerequisites, review of the changed marker-stop
path and a complete artifact manifest are ready, with approval for that bounded
run. V4 was a failed device attempt, not accepted G3 evidence; do not repeat it
unchanged. Pin the final checkout/bridge/module hashes at delivery with the
checkpoint artifact manifest below; do not substitute a previously built ARM64
artifact.

`scripts/checkpoint_manifest.py` (library `framejacker/runtime/manifest.py`)
writes and verifies that manifest. Both commands run on Frame: `--write` right
after the bridge is built there, and `--verify` immediately before the
validation and observer commands. Built binaries are never transferred, so a
manifest written in the cloud or for another build does not verify on Frame.
The manifest records the full git commit, whether the tree is clean (tracked
changes and untracked, non-ignored files count as dirty), the Python version
and implementation, `platform.machine()`, the OS (with its os-release ID,
version and build) and kernel release, and SHA-256 values of the built
`artifacts/overlay-bridge/framejacker-overlay-bridge`, every
`framejacker/**/*.py`, the scripts this procedure runs, the bridge sources
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

`hud_marker_observer.py --help` and `hud_marker_check.py --help` confirm the
existing flags below. This is the existing procedure for a future approved run,
not an instruction to execute it in the current task:

```bash
.venv-replay/bin/python scripts/hud_marker_check.py --validate --seconds 3
.venv-replay/bin/python scripts/hud_marker_observer.py --observe-calibration \
  --allow-mirror-observation --allow-calibration-output \
  --device /dev/video99 --seconds 20
```

Use `/dev/video99` only if it is still independently verified as the mirror
candidate; otherwise stop before opening. The validation command must finish
with exit 0 and `cleanup_ok: true`. The observer is at most 20 seconds/5 reads
per second, uses no inference or saved images, and expects
`observed_unqualified`, exit 2, with clean software cleanup. Preserve its report,
exit code, the artifact manifest with its `--verify` report, and wearer
observations; Ctrl-C or independent SSH SIGTERM aborts both tools. Reaping is reported separately from wearer
confirmation of normal passthrough/tracking during and for 30 seconds after.
No G4/G5 command exists yet; that missing diagnostic is explicit.
