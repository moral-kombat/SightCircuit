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

[E81](e081.md) records a November 3 Frame rerun of the rewritten mirror path.
It exited 0, and the `person`, `sofa` and `tvmonitor` labels matched the owner's
view. Passthrough and tracking stayed normal throughout the sitting. The
initial attempt lacked wearer confirmation and is preserved as invalid;
the confirmed rerun supplies the result.

## Native head-relative VR replay panel

[E47](e047.md) reports 576 VR images submitted with clean shutdown and no
forced termination. The user confirmed a visible panel and labels, and normal
passthrough. In [E81](e081.md), a November 3 real-time run submitted all 400
frames, closed cleanly and kept changing visibly to the end. This exercises
the upstream reports of an approximately 200-call `SetOverlayRaw` limit; one
device/runtime observation does not settle those reports.

The newer bridge probes as Background before connecting as Overlay, with
watchdog and stdout isolation changes recorded in [E84](e084.md). That newer
connect path has no headset result in the record and needs its listed device
check; the earlier 400-frame run does not validate it.

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

[E81](e081.md) adds a November 3 Frame measurement: 8.4 processed frames per
second on that evening's recording and build. No latency percentiles were
taken from the reports. It is one Frame result, not a general throughput or
latency guarantee.

## Candidate detector and evaluation harness

[E73](../status.md#e73-reproducible-preprocessing-audit-and-ssd-channel-correction)
records corrections to SSD channel normalization and per-axis scoring, with
pinned audit variants and factual annotation coverage. The original still and
reference digest for [E61](../status.md#e61-file-only-detector-comparison-on-the-evening-grayscale-still)
were unavailable during that cloud audit.

[E76](../status.md#e76-offline-work-packages-ah-cloud-checks) adds detector-owned
preprocessing, a mirror-preview variant, per-label diagnostics and a CI seed
pin. These offline changes do not select a Frame detector; the default is
unchanged.

[E80](e080.md) records a file-only November 3 rerun on Frame. The original
640 × 360 gray8 still was present; its digest matched the saved reference and
the required-digest check passed. Both detectors missed the TV at the unchanged
0.5 threshold in the baseline and all four audit variants. Those variants fed
identical bytes on this image, so they do not explain the miss. YOLOX's best TV
score was 0.427. MobileNet-SSD no longer reported E61's bottle; the reason is
untested. WP1 remains open: the miss is reproduced, not explained.

[E81](e081.md) adds five unboxed WP2 stills with owner-provided presence and
absence labels. Both detectors missed the bottle, correctly reported no bottle
when it was removed, did not call the rug a sofa, missed the sofa and found
the chair. The owner switched the POC object to chair on November 4. Five
unboxed stills do not select a detector default, and one rug still does not
resolve the earlier false positive. WP2 and the model choice remain open.

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
they do not explain the green physical outcome.

[E79](e079.md) closes G2 on the owner's November 3 confirmation after a recovery
procedure was rehearsed with normal passthrough and tracking. The green-input
cause remains unknown. The rehearsal had no green input, so it demonstrates
the procedure's observed safety, not that it cures the fault. Production
gates stay closed.

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

[E83](e083.md) records the first G3 attempt and one approved retry on November 3.
Both stopped at the observer's contour budget before the bridge initialized
or any marker was shown. Cleanup, passthrough and tracking were normal. G3
stays open: this failed scene precondition supplies no HUD mapping evidence.
An offline precondition change and separate approval are required before a
further attempt.

[E84](e084.md) records later reviewed cloud work: the bridge's Background-first
connection, watchdog and stdout isolation, run-context sensors, HUD prediction
cross-check and file-only observer scene statistics. Their listed device
follow-ups remain unverified by that offline record.

## Guarded on-device agent runner

[E77](../status.md#e77-claude-code-on-frame-runner-guard-and-installer-cloud-checks)
records cloud tests of the fixed Tier A runner, fail-closed PreToolUse guard
and settings template. The Phase 0 installer's path, verify, signature, smoke
and record stages were tested with a fake `claude`. The signature stage used
locally generated keys and a fake download. Precheck, installation and
interactive login were not tested.

[E82](e082.md) adds the first Frame Phase 0 result on November 3: precheck,
verify, signature, smoke and record passed with zero failures and warnings,
and the guard settings matched the installed template. The signature check
covered Claude Code 2.1.316; the later same-day update to 2.1.317 was not
re-checked. Guard blocking and the Tier A runner have not been tested on Frame.
Installing the setup does not prove automated checks work.

[E84](e084.md) records review of the cycle-3 follow-ups: glob confinement,
update outcomes, lock-file and checkout-state reporting. Its #27 entry is
pending at that recorded checkpoint. These later records do not change
E77's original offline provenance.
