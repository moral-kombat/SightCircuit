# Offline lifecycle review, 2026-10-31

This historical investigation explains the failed **V4 marker-observation run**
and the later offline cleanup fixes. “V4” is the recorded run's name; the
lifecycle acceptance gate is **G2** in the [current work plan](live-capture.md#edge-hud-acceptance-gates).

Read the [complete investigation](history/2026-11-07-lifecycle-review.md) for
source matching, exact timings, fix behavior and test names. [E75](evidence/e075.md)
owns the recovered evidence; [E76](evidence/e076.md) records the subsequent
offline work. This summary does not establish a new headset result.

## Recovered V4 software termination chain

The original observation report and ten matched source files establish the
software sequence: the observer rejected a marker sample; Python requested
SIGTERM; the native bridge stopped cleanly, while the overall report truthfully
retained the forced-termination outcome. The outer supervisor did not time out.

This explains the software stop, not the green passthrough or tag mismatch.
See the [full chain, digests and timing limits](history/2026-11-07-lifecycle-review.md#recovered-v4-software-termination-chain)
and [evidence provenance](evidence/e075.md).

## Independently demonstrated defects

Offline work corrected native-image bounds, the expiring packet's pixel offset,
result lifetime and the session helper's failure reporting. These findings were
not established as causes of the physical headset failure. The
[original analysis](history/2026-11-07-lifecycle-review.md#independently-demonstrated-defects)
keeps the distinctions and failure cases.

## Pinned upstream review

The investigation reviewed `frame-perf-overlay` at
`066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94` for cleanup and measurement patterns.
It copied no upstream code and supplied no device profiling evidence. See the
[pinned review](history/2026-11-07-lifecycle-review.md#pinned-upstream-review)
and [third-party attribution](../THIRD_PARTY.md#research-references).

## Marker stop and guardian loss, 2026-11-01

The changed marker client waits for the native timer after a rejected sample;
cancellation and timeout still escalate and report forced termination. Guardian
loss handling contains the reported worker process group. These are offline
software results, with limits described in the
[fix analysis and regression tests](history/2026-11-07-lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01).

## Evidence needed to advance G2

The [current acceptance gates](live-capture.md#edge-hud-acceptance-gates) own the
remaining evidence and next action. The [historical investigation's closing section](history/2026-11-07-lifecycle-review.md#evidence-needed-to-advance-g2)
is preserved, but later owner decisions and reviewed evidence must be read in
the current plan. Software cleanup alone does not prove physical recovery.
