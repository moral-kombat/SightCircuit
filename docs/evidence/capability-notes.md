# Capability details

These maintained notes hold the detail behind the [status summaries](../status.md).
They summarize existing records and implemented changes; they are not new test
results. The linked evidence owns its execution context, numbers and unknowns.
Use [current work](../live-capture.md#current-work-order) for pending decisions.

## Bounded mirror recognition

[E48](../status.md#e48-live-mirror-recognition-poc-and-matching-preview) reports
100 captured reads and 51 processed reads, 4.993 processed fps and 40.065 ms mean
inference, with clean shutdown. The user confirmed matching preview labels and
boxes, and normal passthrough and tracking during and after the run. Broader
accuracy and continuous display remain separate acceptance questions.

[E76](../status.md#e76-offline-work-packages-ah-cloud-checks) records the offline
restoration of `--detector` and `--still`, including pinned still digests for
the WP2 still-image work.

## Real-time recorded replay

The earlier Frame result was 0.97 processed fps
([E53](../status.md#e53-unattended-replay-regression-and-pacing-rerun)). In
[E62](../status.md#e62-keyframe-catch-up-realtime-replay-cloud-checks), keyframe
catch-up and newest-frame inference reached a 0.94 real-time ratio on a
synthetic stand-in where the previous version reproduced that slow behavior.
This is a cloud comparison, not a new Frame measurement.

[E76](../status.md#e76-offline-work-packages-ah-cloud-checks) adds latency
percentiles, processed-media fraction and an ARM64 runner-CPU stand-in job.
Its measurements use a separately generated clip; they are not repeated
measurements of E62's clip.

## Candidate detector and evaluation harness

[E73](../status.md#e73-reproducible-preprocessing-audit-and-ssd-channel-correction)
records corrections to SSD channel normalization and per-axis scoring, with
pinned audit variants and factual annotation coverage. The original still and
reference digest for [E61](../status.md#e61-file-only-detector-comparison-on-the-evening-grayscale-still)
are absent, so that result has not been reproduced or explained.

[E76](../status.md#e76-offline-work-packages-ah-cloud-checks) adds detector-owned
preprocessing, a mirror-preview variant, per-label diagnostics and a CI seed
pin. These offline changes do not select a Frame detector; the default is
unchanged.

## Capture watchdog, provider/layer lifecycle and exact recovery

[E35](../status.md#e35-watchdog-provider-and-recovery-tests-on-frame) covers
bounded workers, metadata/GPU provenance, registry changes, owned endpoints
and conditional restoration. Its 28 generated-data tests on Frame did not
establish real driver/layer execution or physical device restoration. Later
metadata-only loading and restoration are recorded separately in
[E65](../status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed).

Two supervisor cancellation races were fixed on November 4, 2026, with cloud
checks; the publication race was also reproduced on a CI runner. There is no
Frame result for those fixes. See [worker containment and publication recovery](../development.md#worker-containment-and-publication-recovery).

## Metadata-only provider and producer synchronization review

[E36](../status.md#e36-metadata-only-provider-and-synchronization-review) and
[E37](../status.md#e37-metadata-only-provider-and-diagnostic-on-frame) cover
generated metadata-only checks, without layer activation. The v3 ARM64 build
and all 24 generated-data tests passed on Frame.

Later real metadata loading and restoration appear in
[E65](../status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed).
The layer retains and exports no image file descriptors. Live image access
stays blocked, and the provider track remains paused.

## Bounded metadata-layer trial and post-exit recovery

The bare SteamVR stop was refused after the session coupling was found
([E63](../status.md#e63-service-only-rehearsal-and-graphical-session-dependency)).
The platform session test subsequently passed, with a normal headset reported
([E64](../status.md#e64-platform-session-test-completed-and-headset-confirmed)).

Two metadata trials loaded the provider and restored normal startup. They
found eight stable candidate records, but the full registry did not settle
([E65](../status.md#e65-v2-recovered-candidate-snapshots-compared-and-visual-check-passed)).
Image access remains blocked. [E46](../status.md#e46-read-only-runtime-baseline-on-frame)
preserves the earlier runtime baseline.

## Fixed edge HUD, marker and offline live pipeline

Native masking, original-frame expiry and the qualification schema were
verified offline in [E72](../status.md#e72-shared-hud-runtime-and-independent-lifecycle-fixes)
and [E74](../status.md#e74-evidence-generation-and-offline-mapping-qualification-support).
The recovered V4 report and source explain the software stop
([E75](../status.md#e75-recovered-v4-evidence-and-software-termination-analysis));
they do not explain the green physical outcome. G2 remains open in committed
evidence, and production gates stay closed.

[E76](../status.md#e76-offline-work-packages-ah-cloud-checks) verifies cooperative
marker stop, guardian-loss containment, one generated HUD contract,
observer-to-candidate conversion, factory-only calibrations and exclusion
counters offline. [E78](../status.md#e78-mirror-session-tokens-and-neutral-hud-mask-fill-cloud-checks)
adds mirror session tokens from read-only providers and neutral mask fill
that passes the strict synthetic run with the default detector. The mirror
recognizer passes no session; tracking tokens await a native reporter.

The marker observer's off-by-default `--record-session-tokens` option and the
`--from-observer` conversion that takes tokens only from the observer record
are cloud-checked. Their evidence record is pending; the
[observed-token reference](../hud-qualification.md#observed-session-tokens-for-the-candidate-2026-11-03)
documents the implementation.

## Guarded on-device agent runner

[E77](../status.md#e77-claude-code-on-frame-runner-guard-and-installer-cloud-checks)
records cloud tests of the fixed Tier A runner, fail-closed PreToolUse guard
and settings template. The Phase 0 installer's path, verify, signature, smoke
and record stages were tested with a fake `claude`. The signature stage used
locally generated keys and a fake download. Precheck, installation and
interactive login were not tested.

The runner, guard, settings and installer have no Frame run in this checkout's
committed evidence. The review's cycle-3 follow-ups remain open. Relayed device
reports in the [pending evidence work](README.md#pending-evidence) are tracked
separately; they do not change this record's offline provenance.
