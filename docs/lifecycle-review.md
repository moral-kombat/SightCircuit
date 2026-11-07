# Offline lifecycle review, 2026-10-31

This review implements part of WP10; it does not close the lifecycle acceptance
gate. [E59](evidence/e059.md) retains the original V4 and later green-input
observations. [E75](evidence/e075.md) records recovered evidence that now explains
the early software termination and overall cleanup result. The green-input and
tag-mismatch mechanisms remain unresolved. The wearer reported normal native
passthrough after a manual source switch; automatic recovery and recovery of
the Arcturus path were not established.

## Recovered V4 software termination chain

The recovered `v4-once-observation.json` is byte-identical to the originally
recorded report: 10,686 bytes, SHA-256
`9df0170ac49012f0ab9f43981f77cf90c5cae162bc2abde6d53d4857cf5c4b2c`.
All ten recovered source files match their recorded SHA-256 pins. The source
identity is the uncommitted visibility correction on dirty checkout
`833c556abcf4f868849017844f9a8fc857506bd5`, not that commit alone. The archive
and ARM64 binary have recorded digests but were not independently rehashed in
this review. The recovered metadata/source artifacts stay local; no private
images or calibration payloads are committed.

The matched source and report establish this sequence:

1. Two distinct marker-free baseline frames pass. The native bridge starts its
   requested five-second calibration marker and reports one generated submission.
2. The observer's frame-3 callback rejects `orphan_marker_tag_or_incomplete_copy`.
   Candidate diagnostics include `tag_pattern_error` and `code_bit_error`;
   no complete copy or accepted sample is recorded. Those checks explain the
   rejection, not why the captured tag pixels failed to match. An offline
   comparison now requires the native marker generator to equal the observer
   template pixel for pixel ([contract header](development.md#generated-hud-contract-header)),
   so a disagreement between them is not the explanation.
3. The capture callback error takes the observer through its rejection handler
   into `finally`, which calls `MarkerClient.close()`. Marker mode deliberately
   has no writer thread. In the matched `OverlayClient.close()`, that condition
   immediately selects `process.terminate()` (SIGTERM) while the bridge is
   still running, and sets `forced_termination: true` before waiting.
4. The native SIGTERM handler requests an orderly stop. The native report ends
   with `status: stopped`, `cleanup_ok: true` and exit 0. Its cutoff timestamp
   is 213.437 ms after native start, 27.741 ms after the rejected frame and
   4,786.563 ms before the requested marker deadline. Cleanup returns another
   2.414 ms later. These are recorded monotonic software timings, not physical
   disappearance measurements.
5. The Python close path retains the forced-termination intent and its cleanup
   error even though native cleanup APIs report success and the native process
   exits 0. The observer therefore reports overall `cleanup_ok: false` and
   exit 1. The outer worker and guardian are reaped without forced termination;
   the outer supervisor finishes in 0.636 seconds. This was not an outer timeout.

This explains early native termination and the difference between native API
cleanup and overall cleanup. It does not explain green passthrough or qualify
physical recovery. The fail-closed tag rejection remains intact; changing its
thresholds to obtain a pass is not justified by these metadata alone.

## Independently demonstrated defects

- Native acquisition and inference bounds were conflated: the synthetic frame
  contract rejected sides above 960 although mirror evidence is 1920 × 1080.
  Native masking must precede resize, including padded-stride input.
- The guarded expiring-frame packet's RGBA begins at byte 16; the dormant real
  upload used byte 8. Decoding now uses the shared tested path. Real expiring
  HUD output remains gated.
- Content lifetime must end at the original observation time plus maximum age,
  or earlier. Completion, retransmission and heartbeats cannot renew old content.
- The guided session helper returned success from failed commands and continued
  later experiments. It now stops after a failed command/stage, retains command
  exit codes, and records physical recovery as unconfirmed. The parallel
  window/mirror stage still waits for its already-started bounded replay before
  returning. Generated-command tests exercise failure, timeout and success;
  no device command is run by those tests.

These are independently demonstrated source and offline behavioral findings.
They are separate from E75's recovered V4 termination chain and have not been
established as causes of its green passthrough or tag mismatch. Native
hide/clear/destroy/shutdown return values, worker/guardian reaping and the
wearer's physical headset check remain separate observations.
Forced termination cannot count as an overall clean run. Successful API returns
cannot establish normal passthrough or tracking.

## Pinned upstream review

Reviewed [frame-perf-overlay](https://github.com/sasaken1102r/frame-perf-overlay/tree/066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94),
MIT, at `066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94`:

- `headset/vr_overlay.cpp`, `VrOverlay::shutdown`, hides, clears and destroys
  overlays before `VR_Shutdown`, then retires Vulkan textures. Its additional
  400 ms wait is a source pattern, not evidence of physical recovery or a
  scheduling guarantee. SightCircuit retains bounded independent supervision
  and individual cleanup outcomes; this review does not adopt a guessed delay.
- The pose-validity check in placement and `readFrameStats` provide useful
  references for a future separately scoped measurement run. Compositor timing
  samples are deduplicated by frame index; CPU/GPU timing and dropped/reprojected
  presents use explicit sample windows. They are distinct from input observation
  age and model inference time. A report must retain runtime/session identity,
  sampling windows, unavailable counters and wearer observations.
- `headset/vk_texture.cpp` checks submission and a bounded one-second fence wait
  for its own uploads. Its texture destruction includes `vkDeviceWaitIdle`.
  This supplies neither producer synchronization for camera allocations nor
  proof that changing SightCircuit's upload method would improve performance.

No upstream source was copied or new dependency activated. No texture-upload
strategy changed; no profiling on Frame was available. Attribution is retained
in [THIRD_PARTY](../THIRD_PARTY.md#research-references). Deferred pose/world-locked
integrations and the frozen camera provider are outside this implementation.

## Marker stop and guardian loss, 2026-11-01

Package B of the [offline handoff](execution-prompt.md#offline-handoff-2026-11-01)
changes the software stop path. Every result below comes from cloud tests with
generated input and the validation bridge; none is Frame evidence.

- **Marker stop.** In marker mode, `tools/overlay_bridge.cpp` never reads
  stdin. It stops only at its display deadline, on SIGINT/SIGTERM or on an
  OpenVR quit event. `MarkerClient.close()` therefore closes stdin as the stop
  request, then waits for the native deadline plus two seconds. A rejection
  such as V4's frame-3 tag/bit mismatch now ends on the native timer, with no
  forced termination and clean software cleanup. The generated marker stays
  until that timer, at most the requested seconds. Cancellation (the client's
  stop event, set by Ctrl-C or SIGTERM), a ready timeout or an overrun still
  escalates to SIGTERM, then SIGKILL. That stop is reported
  `forced_termination: true` with overall cleanup false, even though the
  native SIGTERM handler stops in order. Bridge reports add `stop_requested` and
  `escalated` (equal to `forced_termination`); no field was renamed. Ending the
  marker earlier without a signal would need the bridge to honour stdin EOF in
  marker mode. That native change is not part of this package.
- **Startup.** If cancellation is already set, `start_marker` no longer
  launches the bridge. The eight-second ready timeout is now
  `READY_TIMEOUT_SECONDS`. Cancellation during startup and the ready timeout
  both escalate at once, with no grace wait, and are reported as forced. A
  bridge without an accepted ready line may still be initializing and could
  show the marker after the run had failed. Only a bridge that has already
  sent its final report gets the two-second grace to exit.
- **One cleanup check.** `overlay.bridge_closed_cleanly` is the single "bridge
  closed cleanly" check. The overlay client and sink, the marker report
  validator and both marker commands all use it. Its fields must be explicit,
  so a missing `forced_termination` or an empty error string is no longer
  clean in any caller.
- **Guardian loss.** The review hypothesis held. After SIGKILL of the guardian,
  the worker and its helper kept running, because the guardian and the worker
  each start their own session and the supervisor signalled only the guardian's
  group. The report already said `cleanup_ok: false`. Now the guardian writes
  its worker's process group to a private pipe. If the guardian dies by a
  signal, the supervisor (its subreaper) sends that group SIGTERM, then SIGKILL,
  and reaps it. The report stays `guardian_failed` with `cleanup_ok: false`, and
  adds `orphaned_worker_group`, `orphaned_worker_tree_signalled` and
  `orphaned_worker_tree_reaped`. Cleanup is still not guaranteed if both the
  supervisor and the guardian are lost, or if a helper leaves its group.

Regression tests: `test_frame_three_tag_mismatch_closes_the_real_bridge_without_forced_termination`
(the V4 shape through the real validation bridge, worker and guardian),
`test_requested_stop_waits_for_native_timer_and_is_not_forced`,
`test_cancellation_before_startup_never_launches_the_bridge` and
`test_sigkill_of_guardian_lets_supervisor_contain_the_reported_worker_tree`.
Each failed on the unchanged base. Further tests cover cancellation during a
stop, startup cancellation, the ready timeout, deadline overrun and every
outcome of the cleanup check. None of this explains the green input, the
tag/bit mismatch or physical recovery.

## Evidence needed to advance G2

The original observation report, ten matching source files and software
termination timeline are recovered; obtaining that report again is not the next
prerequisite. G2 remains open. The smallest remaining evidence action is
inspection of already-existing camera/compositor/Arcturus logs around the
reported V4 run on 2026-10-24 at about 22:48 EDT (2026-10-25 02:48 UTC), together
with the exact SteamOS, SteamVR and Arcturus versions. Record any missing logs
or unknown versions explicitly. The original image is unavailable, so these
metadata cannot diagnose the tag/bit mismatch or green-input mechanism.

The [changed marker stop](#marker-stop-and-guardian-loss-2026-11-01) now has
focused failure-path regressions; review it before a changed output proposal.
Do not repeat the unchanged run
to manufacture missing evidence or treat native API success as recovery.
Physical cleanup still needs a later, separately approved bounded run with a
wearer, independent abort and explicit passthrough/tracking observations.
