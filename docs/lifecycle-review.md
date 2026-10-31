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
`ee87e504f7fb4648c3e472b346a28f1d0b4ba70a`, not that commit alone. The archive
and ARM64 binary have recorded digests but were not independently rehashed in
this review. The recovered metadata/source artifacts stay local; no private
images or calibration payloads are committed.

The matched source and report establish this sequence:

1. Two distinct marker-free baseline frames pass. The native bridge starts its
   requested five-second calibration marker and reports one generated submission.
2. The observer's frame-3 callback rejects `orphan_marker_tag_or_incomplete_copy`.
   Candidate diagnostics include `tag_pattern_error` and `code_bit_error`;
   no complete copy or accepted sample is recorded. Those checks explain the
   rejection, not why the captured tag pixels failed to match.
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
  scheduling guarantee. Framejacker retains bounded independent supervision
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
  proof that changing Framejacker's upload method would improve performance.

No upstream source was copied or new dependency activated. No texture-upload
strategy changed; no profiling on Frame was available. Attribution is retained
in [THIRD_PARTY](../THIRD_PARTY.md#research-references). Deferred pose/world-locked
integrations and the frozen camera provider are outside this implementation.

## Evidence needed to advance G2

The original observation report, ten matching source files and software
termination timeline are recovered; obtaining that report again is not the next
prerequisite. G2 remains open. The smallest remaining evidence action is
inspection of already-existing camera/compositor/Arcturus logs around the
reported V4 run on 2026-10-24 at about 22:48 EDT (2026-10-25 02:48 UTC), together
with the exact SteamOS, SteamVR and Arcturus versions. Record any missing logs
or unknown versions explicitly. The original image is unavailable, so these
metadata cannot diagnose the tag/bit mismatch or green-input mechanism.

Review the writerless-marker stop path and require a focused failure-path
regression before a changed output proposal. Do not repeat the unchanged run
to manufacture missing evidence or treat native API success as recovery.
Physical cleanup still needs a later, separately approved bounded run with a
wearer, independent abort and explicit passthrough/tracking observations.
