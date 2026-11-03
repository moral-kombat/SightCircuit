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
  fixtures pin. A mirror source takes them from read-only providers; see
  [session tokens for the mirror source](#session-tokens-for-the-mirror-source-2026-11-03).
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
  footprint pixels still non-zero after zeroing and before the resize (since
  2026-11-03, before the [neutral fill](#neutral-mask-fill-2026-11-03))
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
  Frame result. **Update 2026-11-03:** the owner chose to try a different fill
  and keep the rule strict. The [neutral mask fill](#neutral-mask-fill-2026-11-03)
  removes this artifact: the default-detector run now ends `completed`, and the
  G1 rerun is back on the default detector.

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

## Session tokens for the mirror source (2026-11-03)

[E76](evidence/e076.md) recorded that no hardware source declares the HUD
session tokens, so a bound mask could never accept a mirror frame.
`framejacker/sources/session.py` now names a read-only source for each token.
`MirrorSource(session=MirrorSession(...))` declares the tokens and stamps every
frame with them. The tests are cloud runs on a fake /proc root (a temporary
directory laid out like /proc) and on hand-written runtime events. Nothing ran
on Frame, no test read the real /proc of any process, and no command passes a
session yet. Nothing here opens a gate. In HUD mode `run_recognition` still
refuses every backend that does not declare the synthetic kind
(`hud_live_mirror_not_approved`, through `LIVE_IMAGE_INPUT`), and `HudMask`
still accepts synthetic fixtures only.

No provider invents a value. A token it cannot observe stays undeclared, with
a named reason in the run report's `session_tokens.undeclared`.

| Token | Real-run source | Value | Unknowns |
| --- | --- | --- | --- |
| `timebase_id` | `HostClock`: `/proc/sys/kernel/random/boot_id`, `/proc/self/ns/time` and that namespace's `CLOCK_MONOTONIC` offset (`/proc/self/timens_offsets`) of the process that stamps `Frame.observed_ns` with `time.monotonic_ns()` (`CLOCK_MONOTONIC`) | `clock_monotonic:boot:<boot id>:timens:<inode>:offset:<s>:<ns>` | The process that writes the calibration (the marker observer) must run in the same time namespace with the same offset, or every frame is rejected; that is unverified on Frame. The offset is part of the token because the kernel reuses a freed namespace's inode number, so two namespaces created one after the other can share an inode with clocks far apart. The offsets file describes the namespace of the process's children, so the token stays undeclared after the process unshares its time namespace (`/proc/self/ns/time_for_children` differs: `host_time_namespace_for_children_differs`). `CLOCK_MONOTONIC` stops during suspend, so a suspend changes neither the token nor a calibration's remaining lifetime. The time-namespace files need Linux 5.6 or later |
| `session_id` | `RuntimeProcess`: the one process whose `/proc/<pid>/comm` is `vrserver`, its start time (`/proc/<pid>/stat` field 22) and the boot id. The kernel keeps the first 15 bytes of a command name, so a longer name is matched by those bytes and confirmed by the executable's full file name | `steamvr:boot:<boot id>:pid:<pid>:start:<ticks>` | `vrserver` is the desktop Linux name; the runtime's process name on Frame is unverified. It is also unverified whether that process is visible from the recognizer's pid namespace (for example inside Steam's container runtime). A session change that does not restart `vrserver` is not seen. When `/proc/<pid>/exe` cannot be inspected, the per-frame check rests on the stat line alone (command name, state, start time): an exec of another program that is also named `vrserver` would not be seen, but `runtime_id` is then undeclared, so a bound mask rejects every frame anyway |
| `runtime_id` | `RuntimeProcess`: SHA-256 of the binary the process runs, opened through `/proc/<pid>/exe`, whose target's file name must be `vrserver`. After an update by rename the link reads `<install path> (deleted)` and the token is still the running build's digest. Never the install path or an installed manifest: the Frame PM reported an installed-manifest version that differed from the running runtime | `steamvr:vrserver:sha256:<digest>` | It covers `vrserver` only, not `vrcompositor`, drivers or shared libraries. Reading the link needs ptrace read access to the process: the same user and a dumpable process (not setuid, no file capabilities), or `CAP_SYS_PTRACE`. Yama's `ptrace_scope` restricts attaching, not this read. Under an emulator such as FEX, or an explicit loader, the link names that program, so the token stays undeclared (`runtime_executable_unexpected`). The binary is hashed once, before the source opens, up to 256 MiB |
| `tracking_origin` | `TrackingObserver`: a `tracking_state` event carrying the HMD's `Prop_CurrentUniverseId_Uint64` and a SHA-256 of the runtime's zero-pose transforms (`GetRawZeroPoseToStandingAbsoluteTrackingPose`, `GetSeatedZeroPoseToStandingAbsoluteTrackingPose`) | `openvr:universe:<id>:zero-pose:<32 hex>` | No reporter emits these events yet (see below). Unverified on Frame: whether an overlay client gets a universe id there, and whether the zero-pose transforms stay bit-identical between recenters (if they drift, every comparison fails closed). A scene application's own tracking space cannot be observed from an overlay process and is not part of the token. Universe id 0, which OpenVR returns on a property error, is rejected. The first `tracking_state` must reach the observer before the `MirrorSource` is created; a state first seen later leaves both tracking tokens undeclared for the run |
| `recenter_epoch` | `TrackingObserver`: the number of `tracking_changed` events (`TRACKING_CHANGES`: universe changed, seated or standing zero pose reset, room setup committed) since its first `tracking_state` | `0`, `1`, ... | The count covers this observer only. A recenter between the calibration run and this run shows only through `tracking_origin`. Unverified: whether Frame's recenter emits one of these events to an overlay client. Events lost while the reporter is down end the observer (`tracking_observer_ended`). A change is seen only once its event reaches `observe()`: a frame captured after a physical recenter but admitted before the event arrives carries the old `tracking_origin` and `recenter_epoch` and passes. That delivery delay is unmeasured |

- **Declared before the source opens.** `MirrorSession.declare()` runs when
  `MirrorSource` is created, so `CaptureContext` checks the declaration
  against the calibration when it is built. An unobservable token is `None`.
  Its reason names the provider's problem: `session_token_provider_missing`,
  `host_boot_id_unreadable`, `host_boot_id_invalid`,
  `host_time_namespace_unreadable`, `host_time_namespace_invalid`,
  `host_time_namespace_for_children_differs`, `host_time_offsets_unreadable`,
  `host_time_offsets_invalid`, `runtime_process_not_found`,
  `runtime_process_ambiguous`, `runtime_process_unreadable`,
  `runtime_process_scan_limit`, `runtime_process_exited`,
  `runtime_process_changed` (the runtime exited, is a zombie, or changed
  while it was being declared, including a pid reused after the scan),
  `runtime_executable_unreadable`, `runtime_executable_unexpected`,
  `runtime_executable_too_large`, `tracking_state_not_observed`,
  `tracking_event_invalid` or `tracking_observer_ended`. A bound mask then
  rejects the first frame with `hud_<token>_undeclared`, and the failure
  latches.
- **The declaration fixes the token set.** A token undeclared at
  `declare()` stays undeclared for the run, even if its provider observes it
  later: it is never stamped on a frame or declared by the source. This
  matters for the tracking tokens: `run_recognition` creates the source
  before the model loads, and a first `tracking_state` that arrives in
  between does not bind. A caller feeds the observer its first state before
  creating the source.
- **Every frame.** Each frame's metadata carries the tokens read for it.
  - `HostClock` re-reads the boot id, the time-namespace links and the
    offset.
  - `RuntimeProcess` re-reads the same pid's stat line (command name, state
    and start time) and, when it could be inspected at declaration, the
    binary's inode. It never rescans or re-hashes. It reports its declared
    tokens as `None` with `runtime_process_exited` (exited, or a zombie not
    yet reaped) or `runtime_process_changed` (pid reused, or another program
    executed).
  - The observer gives its current state. A malformed tracking event or the
    end of its stream loses both tracking tokens for good.
  - A declared token that can no longer be observed is `None`, never left
    out.
- **A change during a run.** Once a frame's tokens differ from the
  declaration, the source declares the latest read. The observer is consulted
  at every admission, so a recenter refuses even the result of a frame read
  before it, without waiting for the next read. `CaptureContext` fails with
  `source_or_session_changed`, and the failure latches. A new context, or the
  old mask checking a later frame's metadata, names the token:
  `hud_recenter_epoch_changed`, `hud_timebase_id_changed` or
  `hud_session_id_changed`. The report's `session_tokens.first_change` gives
  the frame, the token and the reason.
- **One source per token.** A backend identity that declares a session token
  beside a session is refused (`mirror_session_tokens_declared_twice`). A
  `MirrorSession` serves one source (`session_already_declared`).
  `run_recognition` reports either failure before the model loads or anything
  opens. In HUD mode `run_recognition` checks the live-image gate before the
  session is declared, so a refused run scans no /proc and hashes nothing.
- **Fixture calibrations.** `fixture_calibration(..., session=...)` lets a
  synthetic fixture pin the tokens that the tests' fixture providers declare.
  The default is still `SYNTHETIC_SESSION`. The provenance, the `synthetic:`
  source and the mask's synthetic-only rule are unchanged.

**No native reporter yet.** This change adds the Python observer and its event
schema. It does not change `tools/overlay_bridge.cpp`. The bridge's real
`--stdin` mode is the confirmed replay-panel path ([E47](evidence/e047.md)),
and it is the pinned bridge of the next G3 checkpoint. A reporter would add
OpenVR property, pose and event reads to both, unchecked on a device. The
fake-dispatch fixture covers helpers that need no OpenVR: packet decoding,
deadlines and teardown order. What a reporter must get right is runtime
behaviour: which events an overlay client receives on Frame, and what the
universe id and zero poses are. A fake cannot establish that; it would only
test a four-case mapping. A later reporter should emit `tracking_state` at
start and after every change, and `tracking_changed` for each
`TRACKING_CHANGES` event, in real mode only; validation modes stay
runtime-free. `OverlayClient` would forward those lines to a
`TrackingObserver` and call `end()` when the bridge exits.

Follow-ups. None of these has run, and each needs its own review; the device
step needs the user's approval. They are separate from the [next device
checkpoint](#next-device-checkpoint) and do not replace it.

1. **Provider check on Frame (device step, not run).** Run the providers once,
   read-only, against the real /proc while SteamVR runs, with no capture,
   overlay or inference. Record the tokens, their reasons and timing. Check
   the runtime's process name, its visibility from the recognizer's
   namespace, permission to read `/proc/<pid>/exe` and its target (the same
   user and a dumpable `vrserver`, or `CAP_SYS_PTRACE`), and the time
   namespace and its offset. No command for this exists yet.
2. **Native tracking reporter.** As described above, with its own fixture,
   review and device check. The device check also measures the delay from a
   recenter to its event reaching `observe()`, relative to mirror frames.
3. **Observed candidate context.** The marker observer and
   `hud_validation_report.py --from-observer` take session tokens from
   `--declared-context`. They should record the same providers' tokens, so a
   candidate's context is observed rather than declared.
4. **Command path.** No command passes a session to the mirror recognizer.
   HUD input from the mirror stays refused by `LIVE_IMAGE_INPUT` until G4–G6
   pass.

From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_mirror_session tests.test_capture_context \
  tests.test_hud_calibration tests.test_hud_mask
```

## Neutral mask fill (2026-11-03)

This resolves the G5 design question that the
[synthetic validation counts](#synthetic-validation-counts-and-one-maximum-age-2026-11-01)
left open. On the generated 96x64 frames, MobileNetSSD reported the zero-filled
mask itself as `tvmonitor`, so a synthetic validation run with the real
detector failed. The owner chose to try a different fill and to keep the rule
strict. Everything below is a cloud result: runner CPU (x86_64, 4-core Xeon
under KVM, Python 3.12.3, OpenCV 4.12.0, ONNX Runtime 1.31.0), generated frames
and six public seed stills. None of it is Frame, headset or composited-mirror
evidence, and no gate opens.

**What changed.** `HudMask.prepare_counted` still copies the native frame and
zeroes every masked region (footprint plus margin, per eye) at native
resolution, before any resize. It still counts the footprint pixels left
non-zero (`unmasked_footprint_px`), taken from the geometry rule
(`mask_regions`). Then `hud.mask.fill_regions` gives each region one color:
the rounded per-channel mean of the pixels within `FILL_RING_PX` (8) of the
region that lie inside the region's own eye and outside every masked region
(`hud.mask.surround`). The resize comes after that. The fill reads only the
zeroed copy and never the frame it came from. It uses integer arithmetic, so
every CPU gives the same bytes, and its cost is bounded by the ring and the
region, which the blind budget bounds in turn.

**Counters and the strict rule.** `unmasked_footprint_px` now means footprint
pixels still non-zero *after zeroing, before the fill and the resize*. A count
of 0 still proves that no HUD pixel survives into the copy the fill reads, and
`prepare` and the engine still stop on any non-zero count
(`hud_mask_zeroing_failed`). The strict rule is unchanged: `geometry.exclusion`
drops any box that touches a masked region or does not lie inside exactly one
eye, and the validation ledger and the engine counts still fail a validation
run on such a box.

**Leak tests** (`tests/test_hud_fill.py`, `tests/test_mask_fill_evaluation.py`):

- Changing any pixel inside footprint plus margin leaves the detector input
  byte-identical, both the native image the resize receives and the resized
  input. The tests use random content, the real HUD tile with text, white,
  black and inverted content. The calibrations are the 96x64 fixture, 1280x720
  mono, 1920x1080, and both stereo layouts twice: with each region 12 px from
  the seam between the eyes, and with both regions touching the seam, one from
  each side.
- Every single pixel of the fixture's region is checked, one at a time. So are
  the edge rows and columns of every region at 1080p and in stereo.
- Changing everything in the other eye leaves an eye's native and resized
  input unchanged. With the regions on the seam, the 8 px ring would reach the
  other eye if it were not clipped to the region's own eye, so this exercises
  the clip.
- An independent oracle pins the runtime fill to the rule above. It computes
  each region's color without `hud.mask.surround`, as the rounded per-channel
  mean of the pixels within 8 px of the region, inside its eye and outside
  every region. The native image the resize receives must equal that oracle on
  textured frames for every calibration.
- The same leak and other-eye checks run for every candidate fill below.
- In one-off checks, each of these mutations failed the runtime tests:
  - a fill that reads the original region;
  - a mask that keeps one footprint pixel;
  - a ring not clipped to its eye, in `fill_regions` or in `surround`;
  - the mono viewport passed for a stereo frame;
  - a fill that also reads one pixel of the zeroed region;
  - a whole-eye mean;
  - a ring of 4 or 16 px.

**Evaluation.** `scripts/evaluate_mask_fill.py` (library
`framejacker/mask_fill_evaluation.py`, which the runtime never imports) writes
a JSON report; the command is in
[development](development.md#hud-mask-fill-evaluation). It compares six fills,
each run on the zeroed copy under the same surround rule:

- `zero`, the baseline;
- `ring_mean`, the runtime fill;
- `nearest`, nearest-outside-pixel replication;
- `inpaint_telea` and `inpaint_ns`, `cv2.inpaint` with a mask covering exactly
  the region;
- `surround_blur`, a blurred patch: the mean of the surround in a box of
  half-size max(w, h).

Both detectors ran at threshold 0.5. The gray sweep uses the synthetic source's
127 odd uniform levels with the real fixture calibration (region
`(59, 34, 96, 64)`). The seed comparison pastes a mask on the six seed stills at
five positions, 30 cases per fill:

- `lower_right`, the default HUD position: a footprint 16% of the width at the
  tile's 2.5:1 aspect, inset 5% and 8%, with an 8 px margin;
- the same footprint at `lower_left`, `upper_left` and `center`;
- `fixture_scaled`, the fixture's footprint and margin scaled to the still.

Seed photos are not a capture source, so these cases use the same geometry
rules and fill function but never build a `Calibration`.

A box "on the mask" touches the masked region. It is "new" when no unmasked
detection of its label matches it at IoU 0.5 or more. It is "artifact-like"
when it is new, at least 25% of its native box lies on the region, and no
unmasked detection of any label reaches IoU 0.3 with it. That is the closest
proxy for a box the fill itself caused.

"Clear" detections are the ones that touch no mask. For them the report counts
lost, added, relabelled and rescored detections against the unmasked image:

- a rescored detection is a same-label match at IoU 0.5 or more whose score
  moved by more than the stated tolerance of 0.05;
- a lost or added detection is "marginal" when its counterpart on the other
  image scores within 0.05 below the threshold.

Over the 30 cases the unmasked images had 42 clear MobileNetSSD detections and
61 clear YOLOX detections. They had 23 and 24 detections that touch a mask
position, which are real objects under the HUD.

| Detector | Fill | Gray levels with a box on the mask | Seed boxes on the mask (new; artifact-like) | Clear lost (marginal) | Clear added (marginal) | Relabelled | Rescored > 0.05 | Max score change |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mobilenet-ssd | zero | **93/127** | 19 (2; 0) | 0 (0) | 4 (2) | 0 | 3 | 0.33 |
| mobilenet-ssd | ring_mean | **0/127** | 21 (2; 0) | 0 (0) | 2 (1) | 0 | 2 | 0.35 |
| mobilenet-ssd | nearest | 0/127 | 20 (1; 0) | 1 (0) | 1 (0) | 0 | 3 | 0.14 |
| mobilenet-ssd | inpaint_telea | 0/127 | 20 (1; 1) | 0 (0) | 2 (1) | 0 | 3 | 0.24 |
| mobilenet-ssd | inpaint_ns | 0/127 | 19 (1; 1) | 1 (0) | 3 (3) | 0 | 3 | 0.08 |
| mobilenet-ssd | surround_blur | 0/127 | 20 (2; 0) | 0 (0) | 3 (2) | 0 | 2 | 0.33 |
| yolox-nano | zero | 0/127 | 18 (2; 0) | 1 (1) | 3 (1) | 0 | 1 | 0.06 |
| yolox-nano | ring_mean | **0/127** | 22 (3; 0) | 0 (0) | 1 (0) | 0 | 0 | 0.05 |
| yolox-nano | nearest | 0/127 | 20 (2; 0) | 2 (0) | 0 (0) | 0 | 0 | 0.04 |
| yolox-nano | inpaint_telea | 0/127 | 23 (3; 0) | 2 (1) | 1 (0) | 0 | 0 | 0.03 |
| yolox-nano | inpaint_ns | 0/127 | 22 (2; 0) | 1 (0) | 0 (0) | 0 | 0 | 0.02 |
| yolox-nano | surround_blur | 0/127 | 22 (3; 0) | 0 (0) | 1 (0) | 0 | 0 | 0.05 |

- **Gray sweep.** At the zero fill, MobileNetSSD boxed the mask on 93 of 127
  levels, as E76 found. On this runner the confidence was 0.50–0.87; E76
  recorded 0.51–0.81. Every other fill: 0/127 for both detectors, and also
  0/255 over every level 0–254 the source emits (zero: 187/255). On a uniform
  frame the surround fill reproduces the frame exactly, so the detector input
  equals the unmasked input. Neither detector reports anything on any unmasked
  level. MobileNetSSD's highest sub-threshold score for a box touching the mask
  is 0.43 with the ring-mean fill. That is the same as without the mask,
  0.07 below the threshold.
- **Seeds.** Most boxes on the mask are real objects partly under it. Their
  boxes change shape, or the label changes (horse to cow, for example), so a
  few count as new for every fill, zero included. Only the two inpainting fills
  produced an artifact-like box: a `pottedplant` in the foliage on the giraffe
  still at `upper_left` (0.63 with Telea, 0.70 with Navier–Stokes), with no
  unmasked detection near it. With the ring-mean fill neither detector lost a
  clear detection. Its added detections (two for MobileNetSSD, one marginal;
  one for YOLOX) are objects whose unmasked box touched the mask position. With
  the mask, the box covers only the visible part and no longer touches it; one
  of them also changed label, sheep to horse. The differences between the neutral fills are one or
  two events in 30 cases, within the noise of six stills.

**Seed pin counts.** Without a mask the counts (present hits of present checks,
absent violations; no confusions anywhere) are MobileNetSSD 7/9 and 2 and YOLOX
12/12 and 0, which equal the committed pin. Each masked still is scored twice:

- *raw*: the detector's whole output, boxes touching the mask included;
- *kept*: only the boxes the runtime keeps for presentation
  (`mask_fill_evaluation.runtime_kept`, the `geometry.exclusion` rule). The
  boxes it leaves out are the ones a validation run fails on.

Each cell is "raw present hits, raw absent violations / kept present hits,
kept absent violations":

| Detector | Fill | `lower_right` | `lower_left` | `upper_left` | `center` | `fixture_scaled` |
| --- | --- | --- | --- | --- | --- | --- |
| mobilenet-ssd | ring_mean | 8, 2 / 8, 1 | 7, 2 / 4, 2 | 7, 2 / 6, 2 | 7, 1 / 2, 1 | 8, 1 / 5, 1 |
| mobilenet-ssd | zero | 8, 1 / 8, 1 | 7, 2 / 4, 2 | 7, 2 / 6, 2 | 7, 1 / 3, 1 | 8, 2 / 5, 2 |
| yolox-nano | ring_mean | 12, 0 / 9, 0 | 12, 0 / 9, 0 | 12, 0 / 11, 0 | 12, 1 / 5, 0 | 12, 0 / 7, 0 |
| yolox-nano | zero | 12, 0 / 9, 0 | 12, 0 / 9, 0 | 12, 0 / 11, 0 | 11, 1 / 5, 0 | 12, 1 / 9, 0 |

- The raw counts hide what the strict rule costs next to the HUD. At the
  default `lower_right` position YOLOX keeps 9 of its 12 present hits with
  either fill. MobileNetSSD keeps all 8 of its raw present hits there (of 9
  checks), but with the ring-mean fill one of its two raw absent violations is
  a box on the mask.
- The dropped hits are objects whose box covers the mask position, such as the
  horse on the person still and the giraffe. Every fill drops them.
- The neutral fills keep slightly fewer hits than zero at two positions:
  - YOLOX at `fixture_scaled` keeps 7 rather than 9. The horse on the person
    still and the zebra on the giraffe still are now boxed over the neutral
    patch; the black patch had cut their boxes short.
  - MobileNetSSD at `center` keeps 2 rather than 3. With the neutral patch it
    no longer reports the horse on the person still.

  That is one or two events in 30 cases. The zero fill fails the strict rule
  on 93 of 127 gray frames instead.
- Kept absent violations are no higher with the ring-mean fill than with zero:
  7 against 8 over the five positions for MobileNetSSD, and 0 for YOLOX.
- A masked still is a different input, so these counts describe the mask, not
  a regression. `evaluate_detectors.py --expect`, which applies no mask, is
  unchanged and still passes.

**Cost per frame** (milliseconds, p50/p95 of 30 runs, runner CPU, not Frame).
The fill alone runs on an already zeroed copy:

| Fill | 96x64 fixture (1,110 px) | 1920x1080, lower right (44,897 px) | 1920x1080, blind-budget maximum (518,400 px) |
| --- | --- | --- | --- |
| zero | 0.000 | 0.001 | 0.001 |
| ring_mean | 0.06 / 0.11 | 0.69 / 0.87 | 4.1 / 4.7 |
| nearest | 0.11 / 0.15 | 1.6 / 2.1 | 18.6 / 23.5 |
| inpaint_telea | 1.4 / 1.5 | 25.3 / 39.4 | 283 / 337 |
| inpaint_ns | 1.2 / 1.2 | 28.8 / 37.3 | 309 / 369 |
| surround_blur | 0.31 / 0.40 | 9.7 / 10.0 | 97 / 114 |

The whole runtime `HudMask.prepare` with the ring-mean fill takes 0.16 / 0.24,
2.7 / 3.7 and 14.6 / 15.1 ms in the same three cases. That covers validation,
zeroing, the count, the fill and the resize. For comparison, MobileNetSSD
inference took about 26–31 ms per frame in the runs below.

**Choice: `ring_mean`.**

- It clears the gray sweep for both detectors and produced no artifact-like box
  on the seeds. Both inpainting fills did.
- Neither detector lost a clear detection with it.
- It is the cheapest neutral fill. In the table above, `nearest` costs about
  twice as much at 96x64 and at the default 1080p position, and inpainting up
  to 75 times as much at the blind-budget maximum.
- Next to the HUD it keeps, for presentation, at most two present hits fewer
  than zero at any seed position (the kept counts above), and no more absent
  violations.
- Its integer result is identical on any CPU, including ARM64.
- It reads a fixed 8-pixel ring, so it is the easiest fill to reason about for
  leaks.

**Strict-rule validation with the real default detector:**

- On 881fea7 (zero fill), `live_pipeline.py --synthetic-check --synthetic-hud
  --seconds 3` ended `validation_failed`, exit 1, `hud_validation_mask_overlap`
  after 3 frames.
- With the ring-mean fill, the same command ends `completed`, exit 0, with
  clean cleanup. It checked 15 frames and found 0 mask overlaps, 0 eye
  crossings and 0 unmasked footprint pixels; `g5_software.status` is
  `checked_unqualified` and the ledger is `telemetry_checked_unqualified`.
- With `--validate-overlay` the result is the same, over 15 frames.
- `--seconds 60` checks 300 frames over about seven cycles of the source's
  gray levels and ends `completed` with every count 0.
- `tests/test_hud_fill.py` runs the 127-level sweep through `HudStage` with
  both detectors, and both supervised commands with the default detector. It
  failed on 881fea7: MobileNetSSD 93/127, and both commands ended
  `validation_failed`.
- The [G1 rerun](live-capture.md#edge-hud-acceptance-gates) is therefore back
  on the default detector.

**Limits.**

- Frame's mirror content, its real HUD footprint size and its margin are not
  known. Six outdoor stills and uniform gray frames do not show that no scene
  makes a flat patch look like an object.
- If one does, the strict rule still fails that validation run visibly. It is
  not hidden.
- A device G5 run still needs the separately scoped diagnostic and its own
  approval.

```bash
.venv-replay/bin/python -m unittest tests.test_hud_fill tests.test_mask_fill_evaluation tests.test_hud_mask
.venv-replay/bin/python scripts/evaluate_mask_fill.py --fetch --expect-clean-gray-sweep --report /tmp/mask-fill.json
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
receives only a native-resolution owned image after the union mask is applied
(zeroed, then refilled from its surround), then the normal resize. The test
checks every copied pixel in the mask is zero after zeroing, before the fill
and the resize, and rejects boxes touching the mask or eye boundary.
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
