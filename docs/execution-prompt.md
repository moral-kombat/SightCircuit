# Execution prompt: complete the fixed edge HUD work

Prepared 2026-10-31 against `main` `85cf53cd99bb0abfc176b72d279a46dbec52ba4f`,
after [PR #10](https://github.com/moral-kombat/framejacker/pull/10) merged.
This is a copy-ready implementation handoff, not evidence that its work has
executed. The [POC scope](poc-scope.md#work-packages) owns work packages;
[live capture](live-capture.md#edge-hud-acceptance-gates) owns the acceptance
gates; [status](status.md) owns recorded evidence.

The useful outcome is all justified, unblocked offline work completed, with
device-dependent acceptance left explicit. The dated
[execution checkpoint](poc-scope.md#execution-checkpoint-2026-10-31) summarizes
the state when this prompt was prepared. Refresh that state before execution.

## Copy-ready prompt

```text
Work in moral-kombat/framejacker and complete as much of the active fixed-edge-HUD POC as
the available authorized inputs and development environment allow. Implement, test, review
and document the work. Do not stop after analysis or another plan.

1. Refresh the actual starting state

- Inspect the checkout, local changes, current main, open PRs and CI before editing.
  Preserve unrelated work, including observation_page; use the existing isolated checkout.
- Read AGENTS.md, CONTRIBUTING.md, docs/README.md, docs/poc-scope.md, docs/live-capture.md,
  docs/status.md, docs/development.md and the relevant on-device procedures.
- This handoff was prepared against main 85cf53cd99bb0abfc176b72d279a46dbec52ba4f after PR
  #10 merged on 2026-10-31. Recheck current state; do not redo completed work or attempt to
  merge PR #10 again.
- Active WPs: 1, 2, 7, 9, 10, 11, 12. Deferred: 3, 4, 5, 6, 8. The target is a fixed
  head-relative edge tile containing object names and approximate view-relative arrows.
  World-locked labels, dedicated camera access, depth and splats stay deferred.
- Baseline gates: G1 passes offline; G2 lifecycle is open; G3 Frame mapping observation has
  not run; G4 mapping qualification has no defined method; G5 feedback exclusion/physical
  expiry and G6 live acceptance have not run. Verify whether newer evidence changes these
  facts.

2. Organize and keep moving

Use parallel workers with explicit, nonoverlapping file ownership where available:
A. WP10 lifecycle, native/Python overlay transport and WP12 shared runtime.
B. WP1 preprocessing audit and WP2 evaluation.
C. WP7 documentation and WP9/G4 qualification design and device handoff.
One coordinator owns integration, status changes and evidence IDs. Integrate dependent
changes sequentially and review exact diffs.

Make reasonable implementation choices without asking routine questions. If one task needs
missing input, finish its reusable tooling and continue the other tasks. Prefer small
adapters and shared primitives over a wholesale rewrite.

3. WP10 and WP12: complete the offline runtime work

Audit and reconcile framejacker/frame.py, framejacker/pipeline.py, framejacker/overlay.py,
scripts/mirror_recognize.py, scripts/live_pipeline.py, scripts/overlay_client.py,
framejacker/hud/, scripts/bounded_worker.py, tools/overlay_bridge.cpp and their tests.

- Converge on maintained shared frame, detector, pipeline and overlay contracts in
  framejacker/, retaining compatible command wrappers in scripts/. The package pipeline does
  not yet contain all safeguards in the newer synthetic loop. Preserve those safeguards
  before migrating commands.
- Preserve owned image copies, native layout/stride, source and frame identity,
  observation-time provenance, matching results, latest-only buffering and bounded memory.
  Observation timestamps are not exposure timestamps.
- Keep native capture bounds separate from inference-size limits. At the reviewed baseline,
  OwnedFrame rejects any side over 960. Support a bounded full native frame through masking
  before resize, with generated 1920x1080 and padded-stride coverage. Do not solve this by
  resizing before applying the mask.
- Apply the full versioned HUD footprint plus margin at native resolution, including all
  qualified eye copies. Reject detections overlapping a mask or crossing an eye boundary.
  Keep original-to-inference coordinate mapping explicit.
- Revalidate source/session/tracking-origin/format/crop/layout/orientation/HUD configuration
  and result age after inference and before submission. Invalid or expired mappings stop
  inference and presentation and expire previous content.
- Maintain independent output expiry even when inference stalls, settings heartbeats
  continue, reads are partial or a source disappears. Derive expires_ns from the original
  frame.observed_ns + max_age_ns, or an earlier deadline; inference completion and
  retransmission must never refresh old content. Consolidate the two overlay clients without
  losing frozen geometry, readiness negotiation, bounded writes or cleanup reporting.
- Fix and test native packet decoding before any future real expiry support. At the reviewed
  baseline, opcode 4 contains RGBA at payload offset 16, while the unreachable real
  submission uses offset 8. This is a latent integration hazard behind a guard, not a
  demonstrated headset failure. Test the production decoding/expiry logic through fake
  dispatch or injected interfaces while preserving the real-output gate.
- Review native hide/clear/destroy/shutdown ordering, guardian ownership, abort behavior,
  early termination, session-helper continue-after-failure behavior and frame/display
  limits. Extend existing behavioral tests for stalls, late returns, signals, parent loss,
  partial startup and teardown failures where gaps exist.
- Separate process reaping, successful API cleanup and wearer-confirmed physical recovery in
  reports. Investigate V4 using available source and approved existing reports. If the
  causal evidence is absent, record the missing evidence and leave the root cause
  unresolved; fix independently demonstrated defects without claiming they caused V4.
- Add useful measurements for input age, processing/inference time, dropped/stale results
  and expiry. Prepare compositor/CPU/GPU/thermal measurement hooks only where justified; no
  performance claim without measurements.

Reuse the pinned frame-perf-overlay lifecycle and measurement patterns in docs/poc-scope.md
when they improve the existing code. Record precisely what was reused, pin revisions and
preserve notices in THIRD_PARTY.md. Change texture uploads only if profiling justifies it.
Frametop pose history, One Euro Filter and WayVR remain deferred; Frame Control screenshots,
Moondream and llama.cpp are optional later work, not dependencies of this completion pass.

4. WP1 and WP2: advance evaluation without inventing data

- Inspect approved existing local inputs and the existing evaluate_detectors.py/reporting
  before building new tools. Do not recapture, transfer or upload private images merely to
  obtain the E61 still.
- If the original approved E61 still and verifiable digest are available, reproduce the
  comparison with pinned code/model/runtime hashes, threshold 0.5 and recorded warm-up
  policy. Compare grayscale replication, channel order, scaling, crop, SSD stretch, YOLOX
  letterbox and inverse box mapping one controlled change at a time. Preserve the original
  result.
- Diagnose the bottle result only after visual inspection. Add a focused regression when a
  conversion/mapping defect is demonstrated. Do not lower thresholds or choose a detector to
  make one still pass.
- If the still or authorization is unavailable, complete the audit command/report support
  and synthetic fixtures that are useful independently. Report the exact missing input; do
  not claim WP1 is closed.
- Extend the existing evaluation manifest and reports as needed for representative approved
  Frame stills. Distinguish presence labels, factual absent labels and inspected bounding
  boxes. Do not infer localization accuracy from presence-only labels or choose a default
  from synthetic/seed results alone.
- Keep private images, personal calibration, model weights and generated environments out of
  commits. WP2 remains open until representative Frame evidence supports its acceptance.

5. WP9/G4 and WP11: define the missing qualification step

- Reuse the implemented marker harness and empirical observer. A clean observation remains
  observed_unqualified, exit 2; it does not establish eye identity, composition,
  unseen/occluded copies or production exclusion.
- Produce a concrete proposed G4 method with observable evidence, limitations, rejection
  criteria and versioned mapping records. Address eye composition, every possible visible
  HUD copy, margins under the bounded operating conditions, and invalidation when those
  conditions change. If a sufficient method cannot be justified from available evidence, say
  exactly what is missing.
- Implement offline schema/validation support for source/session/runtime identity,
  tracking-origin/recenter changes, native format and stride, crop, eye layout, HUD
  geometry, evidence provenance and calibration lifetime. A boolean validated flag or
  hand-edited JSON is not qualification evidence.
- Resolve the validation dependency explicitly in the design: G5 needs a bounded
  device-validation path, while production HUD output must remain closed until G4/G5 pass.
  Prepare a separately scoped, default-off test design; do not simply remove production
  guards.
- Prepare WP11 instrumentation for feedback exclusion and physical expiry and a WP12
  acceptance report template. Exercise generated/fake inputs offline. Do not call a proposed
  method qualified or claim a headset result.

6. WP7: finish the documentation deliverable

- Establish per-entry evidence files under docs/evidence/, a deterministic generated status
  matrix and a concise root README. Preserve existing linked headings and paths with
  compatibility summaries where necessary.
- Keep E63-E71 identified as reconstructed records with their original uncertainty. Recheck
  the next free evidence ID before allocation; it was E72 at this handoff. Only the
  coordinator allocates IDs and updates the matrix.
- Reconcile stale instructions that imply a dedicated provider is required for the
  qualified-mask HUD path or tell an unsuitable-mirror session to resume the frozen provider
  track. Preserve the historical evidence and current provider freeze.
- Update owning documents and source links alongside the implemented changes. Respect
  generated-summary markers and update their source/generator rather than hand-editing
  generated output. Clearly separate implemented, verified offline, device-confirmed,
  blocked and deferred work. A documentation edit or passing synthetic test does not close a
  device-dependent WP.

7. Verify the resulting implementation

Follow current repository setup and checks: Python 3.12, pinned dependencies and Ruff 0.14.2
unless the repository has deliberately changed them. Build the required native fixtures
rather than counting skipped tests as passes.

Run relevant behavioral regressions and the required repository gates, including the
applicable full unit suite, Ruff, smoke check, seed evaluation and git diff --check. Run
validation-only HUD/native checks for changed paths. Update CI's targeted no-skip coverage
where the changed critical tests are missing. Inspect x86_64 and ARM64 CI for the exact PR
head; ARM64 CI is not a Frame test. Report intentionally disabled jobs separately from
skipped or failed required tests.

Verify local links, preserved anchors and documented command options. Report only checks
actually run, with revision, runtime, result and limits. Do not weaken tests or gates to
obtain a green result. Avoid unrelated refactors and optional integrations.

8. Finish all available work and leave a concrete handoff

Continue until every justified, currently unblocked offline task in this scope is
implemented, reviewed, tested and documented. Commit coherent changes and prepare focused
review-ready PRs through the repository workflow. The coordinator reviews exact diffs and
checks before any merge already authorized in the active session.

Keep existing production live-source, hardware-mapping and real-HUD-output gates closed.
This task does not authorize new headset capture, source changes, device downloads, provider
activation, service/session restarts, private-image transfer or server exposure. Existing
commands and consumed one-run approvals do not authorize a new device operation. Complete
all independent work before presenting any remaining approval-dependent step.

The final report must include:
- Changed files, commits/PRs and precise behavior improvements.
- Actual local/CI results and failures or blocked checks.
- One row per active WP separating completed offline work from remaining acceptance.
- The proposed G4 qualification method and unresolved evidence.
- Each blocker, its exact missing input, the smallest next action and the WPs it unlocks.
- The single next justified device checkpoint, with a pinned artifact/manifest, implemented
  command verified against --help, bounds, independent abort, expected reports and wearer
  checks. If prerequisites are missing, provide the exact missing prerequisite instead of an
  invented command.

Do not stop at the first blocked WP, request blanket hardware approval or claim the live POC
is complete without the 60-second headset acceptance evidence.
```
