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
full frame, a nonzero orientation and more than one copy per eye.

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
| `operating_limits` | Declared yaw/pitch/roll ranges, angular-speed limit and display mode; evidence must establish and runtime monitoring must enforce them before device use |
| `copies` | Unique copy IDs, declared eye, full native rectangle, margin, sampled drift, localization uncertainty and filter support; declaration is not eye evidence |
| `evidence` | Role, artifact ID, SHA-256 and kind for each original evidence artifact; a digest establishes identity, not truth or completeness |

The offline `scripts/hud_validation_report.py` command checks a candidate against
a separately supplied current context and explicit monotonic time. It never
opens a device or enables output. A valid check returns exit 2 and
`candidate_valid_unqualified`; rejection returns exit 1. Its bounded `ValidationLedger`
checks frame/result identity, timing, mask/eye rejection and original-frame
deadlines from generated or injected telemetry. It retains counters rather
than an unbounded event history. It does not observe native hide events or
enforce declared motion limits. Physical fields remain unverified.

```bash
python3 scripts/hud_validation_report.py --check-candidate /tmp/candidate.json \
  --current-context /tmp/current-context.json --now-ns 123456789
```

The two files are local metadata records; the example time must be replaced
with a current value from the record's declared monotonic timebase. These
options were checked against the implemented command's `--help`.

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
age, inference and total processing times, replaced/stale/rejected counts,
mapping validity, expiry events and software cleanup. Confirm no feedback from
the entire mask, expiry when the object leaves view, and normal passthrough and
tracking during and for 30 seconds afterward. G6 is not accepted from an ARM64
build, synthetic run or a report with missing physical evidence.

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
and software timeline need not be recovered again. Any changed marker-stop
path needs focused regression coverage; physical recovery requires a later,
separately approved bounded validation with independent abort and a wearer.

The single next proposed device checkpoint is a revised **G3 marker-observer
attempt**, after G2/recovery prerequisites, review of the changed marker-stop
path and a complete artifact manifest are ready, with approval for that bounded
run. V4 was a failed device attempt, not accepted G3 evidence; do not repeat it
unchanged. Pin the final checkout/bridge/module hashes at delivery; do not
substitute a previously built ARM64 artifact.
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
exit code, artifact manifest and wearer observations; Ctrl-C or independent
SSH SIGTERM aborts both tools. Reaping is reported separately from wearer
confirmation of normal passthrough/tracking during and for 30 seconds after.
No G4/G5 command exists yet; that missing diagnostic is explicit.
