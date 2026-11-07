<a id="proposed-hud-mapping-qualification-and-device-validation"></a>

# HUD mapping qualification

The marker observer can find visible copies of a HUD marker in the mirror.
**Qualification** is the separate review that establishes whether a mask covers
all HUD output under known operating conditions. A clean observation or a valid
JSON record is insufficient.

This page owns the proposed qualification method and the planned device
validation design. [Current work and gates](live-capture.md#edge-hud-acceptance-gates)
own acceptance state. The [device checkpoint](guides/hud-checkpoint.md) owns
operator commands. No production gate is opened by this design.

For implemented contracts, use the [candidate-record](reference/hud-candidates.md),
[runtime](reference/hud-runtime.md), [session identity](reference/hud-session.md)
and [observation-tool](reference/hud-observation.md) references.

## Evidence that qualification must add

The existing marker observer is G3: at native resolution it reports visible
tagged copies, unions, drift and conservative candidate margins. It does not
assign eyes, prove capture freshness or find every unrecognizable/occluded
copy. The historical V4 device attempt ran on Frame and was rejected; it did
not establish an accepted G3 mapping observation. [E75](evidence/e075.md) now
records its recovered evidence. Successful observations would still remain
`observed_unqualified`, exit 2, including when later evidence is added beside
their reports. Keep original reports and digests unchanged. The later
November 3 attempt ([E83](evidence/e083.md)) stopped at the scene contour budget
before any marker appeared; it also supplies no mapping observation.

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

1. **Pin and preflight.** Retain G2's accepted recovery procedure and the required
   offline checks. G2 is closed by [E79](evidence/e079.md), with the physical cause
   still unknown. Obtain approval for a specifically scoped G4 trial, separate from
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

## Separately scoped device validation

G5 needs real output to test exclusion and physical expiry, while production
output requires G4/G5. Resolve this by a separately scoped diagnostic path,
default off and distinct from the production CLI. This is a design, not an
implemented device command. It needs its own review, pinned build and one-run
authorization before use; the current production rejection paths stay intact.

The [architecture decisions of 2026-11-07](poc-scope.md#architecture-decisions-2026-11-07)
adopt this diagnostic as a planned **diagnostic tier**: a second, equally
closed gate table whose scope record binds the checkpoint manifest, mapping
record and model digests to an absolute deadline, and whose mask is the union
of every observed marker copy plus the margin, so the 60-second acceptance run
does not require eye identity. Full G4 as written here stays the requirement
for production use. The [planned structure](architecture.md#approval-tiers)
describes the design; nothing of it is implemented and the gate table is
unchanged.

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

## Next device checkpoint

The [canonical HUD checkpoint](guides/hud-checkpoint.md) contains prerequisites,
artifact verification, exact bounded commands, expected reports and shutdown.
Its current acceptance prerequisites come from the [gate table](live-capture.md#edge-hud-acceptance-gates).
Keep existing reports and source digests; do not repeat an unchanged failed run.
The November 3 attempts passed manifest verification and the validation-only
check, then both stopped in under half a second at `observer_contour_budget`
before bridge initialization or marker output ([E83](evidence/e083.md)).
Package O3 replaced that budget with the
[O3 rule](reference/hud-observation.md#the-o3-rule-2026-11-06), cloud-checked
only; a further attempt runs on a checkout that contains it, with its manifest
pinning that checkout, and needs a new approval. G3 stays open. No G4/G5
device diagnostic command exists yet.

**Scene precondition (2026-11-06, package O3; cloud checks only).** The
2026-11-03 attempt ([E83](evidence/e083.md)) stopped at the old 2,048-contour
budget. That budget is replaced by the
[O3 rule](reference/hud-observation.md#the-o3-rule-2026-11-06). The next attempt must run on a checkout
that contains O3, and its manifest pins that checkout. By their contour
totals, and if they were 1920 × 1080 reads like every recorded mirror read,
all three E83 reads would pass the O3 speck and contour limits. No bright
fraction or quad count was recorded for them, so whether they pass saturation
and the quad and outline budgets is unknown. Passing the precondition does not
mean the marker will be found. Bright structure touching a copy often defeats
it: at every scale for a surround, half-plane, corner block or line, and up to
scale 1.5 for a bar on its edge. So the background around the HUD should stay
dark. Before the attempt, a person can run `--analyze-still`
on a native still of the planned view
([procedure](guides/hud-checkpoint.md#measure-the-observers-scene-precondition-on-saved-stills)).
That reads only the file and predicts the precondition's outcome on that view.
If a run is still rejected at the precondition, its report carries the frame's
schema-2 statistics. Those counts, not this note, decide whether a limit
needs revisiting.

## Reference sections

The section addresses below remain valid after the split. Each points to the
maintained reference for that topic. The [full historical guide](history/hud-qualification-2026-11-07.md)
retains dated implementation narratives and detailed experiment results.

<a id="offline-candidate-records"></a>
- [Offline candidate records](reference/hud-candidates.md#offline-candidate-records)

<a id="observer-report-to-candidate-2026-11-01"></a>
- [Observer report to candidate](reference/hud-candidates.md#observer-report-to-candidate-2026-11-01)

<a id="runtime-calibration-and-one-margin-rule-2026-11-01"></a>
- [Runtime calibration and one margin rule](reference/hud-runtime.md#runtime-calibration-and-one-margin-rule-2026-11-01)

<a id="synthetic-validation-counts-and-one-maximum-age-2026-11-01"></a>
- [Synthetic validation counts and one maximum age](reference/hud-runtime.md#synthetic-validation-counts-and-one-maximum-age-2026-11-01)

<a id="neutral-mask-fill-2026-11-03"></a>
- [Neutral mask fill](reference/hud-runtime.md#neutral-mask-fill-2026-11-03)

<a id="session-tokens-for-the-mirror-source-2026-11-03"></a>
- [Session tokens for the mirror source](reference/hud-session.md#session-tokens-for-the-mirror-source-2026-11-03)

<a id="observed-session-tokens-for-the-candidate-2026-11-03"></a>
- [Observed session tokens for the candidate](reference/hud-session.md#observed-session-tokens-for-the-candidate-2026-11-03)

<a id="geometric-prediction-cross-check-2026-11-04"></a>
- [Geometric prediction cross-check](reference/hud-observation.md#geometric-prediction-cross-check-2026-11-04)

<a id="marker-stop-path"></a>
- [Marker stop path](reference/hud-observation.md#marker-stop-path)

<a id="observer-scene-precondition-2026-11-04"></a>
- [Observer scene precondition](reference/hud-observation.md#observer-scene-precondition-2026-11-04)

<a id="one-margin-rule"></a>
- [One margin rule](reference/hud-runtime.md#one-margin-rule).
