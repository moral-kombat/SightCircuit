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
The E79–E84 records and claimed device decisions from the old PR #23, now also
proposed in [SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1),
remain pending. Do not assign
those IDs again or update gate acceptance from a relayed claim alone.

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
