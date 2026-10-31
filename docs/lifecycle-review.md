# Offline lifecycle review, 2026-10-31

This review implements part of WP10; it does not close the lifecycle acceptance
gate. [E59](evidence/e059.md) remains the owning account of V4 and the later
green-input recurrence. The original V4 report, exact tested source/binary,
termination timeline and runtime logs are absent from this checkout. The report
was described as early native termination and overall cleanup false despite
successful API cleanup calls and process reaping. Those observations do not
identify a cause, and the later manual source-toggle workaround does not prove
physical recovery after V4.

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

These are source and offline behavioral findings. None has been established as
the cause of V4. Native hide/clear/destroy/shutdown return values, worker/guardian
reaping and the wearer's physical headset check remain separate observations.
Forced termination cannot count as clean API teardown. Successful API returns
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

The smallest evidence action is authorized inspection of the already-existing
V4 report and corresponding source/binary hashes, startup/termination records
and any saved runtime diagnostics. Preserve their original values and unknowns;
do not repeat an unchanged device run to recover missing logs. A causal account
and a focused failure-path regression must precede a proposed output checkpoint.
Physical cleanup still needs a separately approved bounded run with a wearer,
independent abort and explicit normal-passthrough/tracking observations.
