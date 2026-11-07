# Contributor handoff

Start a coding session from the current checkout and maintained guides. The
older prompts below are historical records; their tasks, branch names and
approvals must not be replayed as a current work order.

## Start here

1. Read the [project overview](../README.md), [documentation index](README.md)
   and [contribution workflow](../CONTRIBUTING.md).
2. Check [current work](live-capture.md#current-work-order), the
   [accepted scope](poc-scope.md) and [recorded status](status.md).
3. Read [development setup](development.md), then the focused guide/reference
   for the component being changed.
4. Check the working tree, current branch and relevant merged/open work before
   editing. Preserve unrelated changes and do not infer device acceptance from
   code or CI.

## What the old handoffs no longer describe

The November 1 packages A–H, mirror session tokens and neutral fill, and
observer token recording are implemented. PRs #22, #24 and #25 are also merged.
Their old “wait for merge” and implementation instructions are obsolete.

The native tracking reporter and other remaining follow-ups are described by
[current work](live-capture.md#implementation-and-evidence-reconciliation).
[SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1) merged
E79–E84. G2 is closed by the owner-rehearsed recovery procedure; the green-input
cause stays unknown. The original E61 still was verified and re-run, five WP2
stills support the owner’s chair choice, and G3 stopped at the scene precondition
before marker output. These records do not close G3–G6 or enable production.

The agent-runner/guard follow-ups (SightCircuit PR #2) and SightCircuit PR #3
remain open. The native tracking reporter is carried on
`work/native-tracking-reporter-unintegrated`, awaiting integration and review.
See the [current review follow-ups](live-capture.md#offline-review-follow-ups)
and the source handoff below; do not infer a merge from former-repository status.

## Copy-ready prompt

```text
Work on the agreed SightCircuit task using the current checkout.
Read AGENTS.md, CONTRIBUTING.md, docs/README.md and the relevant focused guide.
Use docs/live-capture.md for current work and acceptance gates, docs/poc-scope.md
for scope and accepted decisions, and docs/status.md for recorded evidence.
Check current code and merged/open work before reusing a historical handoff.
Preserve unrelated changes and existing production gates. Keep implementation,
offline checks, CI and headset evidence distinct. Update the owning docs with
behavior changes and run the documented relevant checks. Record results with
provenance; do not turn historical one-run approvals into a new device run.
```

## Ship pipeline follow-ups, 2026-11-06

[Current work](live-capture.md#offline-review-follow-ups) owns the remaining
work order. The [full review table](history/coder-handoff-2026-11-07.md#ship-pipeline-follow-ups-2026-11-06)
preserves findings, deferral reasons and source PRs; [E84](evidence/e084.md)
records merged changes and validation limits.

## Coder handoff, 2026-11-07 (agent-runner coder; session closed after the move)

The [full November 7 handoff](history/coder-handoff-2026-11-07.md#coder-handoff-2026-11-07-agent-runner-coder-session-closed-after-the-move)
preserves the unfinished reporter integration, source pins, completed work
and open review findings. Its former #27 is open SightCircuit PR #2.

### Item 3: the native tracking reporter, carried over unfinished

See the [integration steps and binding decisions](history/coder-handoff-2026-11-07.md#item-3-the-native-tracking-reporter-carried-over-unfinished).
The branch requires integration with the merged bridge lifecycle changes,
remaining source-pin fixes, the SightCircuit rename, full checks and review.

### What that session finished

See the [recorded session outcome](history/coder-handoff-2026-11-07.md#what-that-session-finished).
Current merged state is reconciled [here](live-capture.md#implementation-and-evidence-reconciliation).

### Still open

See [current follow-ups](live-capture.md#offline-review-follow-ups) and the
[remaining session findings](history/coder-handoff-2026-11-07.md#still-open).
The old e008 link defect has been corrected by this documentation work; optional
lock-file and console-entry-point work remains outside it.

## Historical handoffs

The [preserved handoff collection](history/execution-prompt-2026-11-07.md)
contains the full October 31 and November 1/3/4 prompts, package findings,
source pins and session lessons. It is useful for understanding why code changed,
not for selecting today's priorities or permissions.

## Earlier sections

The complete earlier text is preserved in a [dated snapshot](history/execution-prompt-2026-11-07.md).
These links retain previous section addresses; use the current sections above for guidance.

<details>
<summary>Historical section links</summary>

<a id="execution-prompt-complete-the-fixed-edge-hud-work"></a>
- [Execution prompt: complete the fixed edge HUD work](history/execution-prompt-2026-11-07.md#execution-prompt-complete-the-fixed-edge-hud-work)

<a id="coder-handoff-2026-11-04"></a>
- [Coder handoff, 2026-11-04](history/execution-prompt-2026-11-07.md#coder-handoff-2026-11-04)

<a id="merged-in-this-session"></a>
- [Merged in this session](history/execution-prompt-2026-11-07.md#merged-in-this-session)

<a id="open-work"></a>
- [Open work](history/execution-prompt-2026-11-07.md#open-work)

<a id="owner-decisions-taken-in-this-session-2026-11-03-and-2026-11-04"></a>
- [Owner decisions taken in this session (2026-11-03 and 2026-11-04)](history/execution-prompt-2026-11-07.md#owner-decisions-taken-in-this-session-2026-11-03-and-2026-11-04)

<a id="open-questions-for-the-owner"></a>
- [Open questions for the owner](history/execution-prompt-2026-11-07.md#open-questions-for-the-owner)

<a id="follow-ups-found-not-started"></a>
- [Follow-ups found (not started)](history/execution-prompt-2026-11-07.md#follow-ups-found-not-started)

<a id="how-this-session-ran-the-work"></a>
- [How this session ran the work](history/execution-prompt-2026-11-07.md#how-this-session-ran-the-work)

<a id="coder-handoff-2026-11-03"></a>
- [Coder handoff, 2026-11-03](history/execution-prompt-2026-11-07.md#coder-handoff-2026-11-03)

<a id="what-is-done"></a>
- [What is done](history/execution-prompt-2026-11-07.md#what-is-done)

<a id="owner-decisions-2026-11-03"></a>
- [Owner decisions (2026-11-03)](history/execution-prompt-2026-11-07.md#owner-decisions-2026-11-03)

<a id="open-decisions-for-the-owner"></a>
- [Open decisions for the owner](history/execution-prompt-2026-11-07.md#open-decisions-for-the-owner)

<a id="offline-work-that-is-ready-to-pick-up"></a>
- [Offline work that is ready to pick up](history/execution-prompt-2026-11-07.md#offline-work-that-is-ready-to-pick-up)

<a id="device-steps-each-needs-the-owners-approval"></a>
- [Device steps (each needs the owner's approval)](history/execution-prompt-2026-11-07.md#device-steps-each-needs-the-owners-approval)

<a id="how-the-work-was-run"></a>
- [How the work was run](history/execution-prompt-2026-11-07.md#how-the-work-was-run)

<a id="environment-gotchas"></a>
- [Environment gotchas](history/execution-prompt-2026-11-07.md#environment-gotchas)

<a id="offline-handoff-2026-11-01"></a>
- [Offline handoff, 2026-11-01](history/execution-prompt-2026-11-07.md#offline-handoff-2026-11-01)

<a id="baseline"></a>
- [Baseline](history/execution-prompt-2026-11-07.md#baseline)

<a id="blocker-found-in-the-review"></a>
- [Blocker found in the review](history/execution-prompt-2026-11-07.md#blocker-found-in-the-review)

<a id="findings"></a>
- [Findings](history/execution-prompt-2026-11-07.md#findings)

<a id="packages"></a>
- [Packages](history/execution-prompt-2026-11-07.md#packages)

<a id="a-restore-still-capture-and-pin-still-digests-blocker-small"></a>
- [A. Restore still capture and pin still digests (blocker, small)](history/execution-prompt-2026-11-07.md#a-restore-still-capture-and-pin-still-digests-blocker-small)

<a id="b-marker-stop-guardian-loss-and-startup-cancel-g2-software-small-to-medium"></a>
- [B. Marker stop, guardian loss and startup cancel (G2 software, small to medium)](history/execution-prompt-2026-11-07.md#b-marker-stop-guardian-loss-and-startup-cancel-g2-software-small-to-medium)

<a id="c-device-checkpoint-artifact-manifest-g3-prerequisite-small-to-medium"></a>
- [C. Device checkpoint artifact manifest (G3 prerequisite, small to medium)](history/execution-prompt-2026-11-07.md#c-device-checkpoint-artifact-manifest-g3-prerequisite-small-to-medium)

<a id="d-one-hud-and-marker-contract-for-python-and-c-g3g4-medium"></a>
- [D. One HUD and marker contract for Python and C++ (G3/G4, medium)](history/execution-prompt-2026-11-07.md#d-one-hud-and-marker-contract-for-python-and-c-g3g4-medium)

<a id="e-wire-qualification-into-the-runtime-g4g5-offline-medium-to-large-after-d"></a>
- [E. Wire qualification into the runtime (G4/G5 offline, medium to large; after D)](history/execution-prompt-2026-11-07.md#e-wire-qualification-into-the-runtime-g4g5-offline-medium-to-large-after-d)

<a id="f-one-live-report-runner-and-one-mirror-launcher-structural-medium-after-a-and-b"></a>
- [F. One live report runner and one mirror launcher (structural, medium; after A and B)](history/execution-prompt-2026-11-07.md#f-one-live-report-runner-and-one-mirror-launcher-structural-medium-after-a-and-b)

<a id="g-detector-ownership-and-evaluator-diagnostics-medium-after-a"></a>
- [G. Detector ownership and evaluator diagnostics (medium; after A)](history/execution-prompt-2026-11-07.md#g-detector-ownership-and-evaluator-diagnostics-medium-after-a)

<a id="h-latency-statistics-for-wp12-small-to-medium-after-e3-because-both-change-pipelinestats"></a>
- [H. Latency statistics for WP12 (small to medium; after E3, because both change `PipelineStats`)](history/execution-prompt-2026-11-07.md#h-latency-statistics-for-wp12-small-to-medium-after-e3-because-both-change-pipelinestats)

<a id="lanes-and-order"></a>
- [Lanes and order](history/execution-prompt-2026-11-07.md#lanes-and-order)

<a id="rules-for-every-package"></a>
- [Rules for every package](history/execution-prompt-2026-11-07.md#rules-for-every-package)

<a id="device-work-these-packages-unlock-for-the-project-manager"></a>
- [Device work these packages unlock (for the project manager)](history/execution-prompt-2026-11-07.md#device-work-these-packages-unlock-for-the-project-manager)

</details>
