# HUD fallback input: time-multiplexed exclusion

Use this reference when the marker-observation budget of decision D2 is
exhausted and the project switches to the fallback input design. It is a
design document (work order item A11, 2026-11-07). **Of the work below, only
F1 (the bridge events, 2026-11-08) is implemented; no device step is implied,
and no gate changes.** File and line references are at revision `e4019a4`
unless a paragraph says otherwise.

[Architecture](../architecture.md#fallback-input-design) · [Council record](../history/2026-11-07-architecture-council.md#fallback-time-multiplexed-exclusion) · [Decisions](../poc-scope.md#architecture-decisions-2026-11-07) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

## Why this design exists

The production HUD path keeps the fixed tile visible and removes its mirror
footprint from every frame before the detector sees it. That needs a qualified
mapping of where the tile lands in the mirror (gates G3 and G4), which the
observer has not yet established ([E75](../evidence/e075.md),
[E83](../evidence/e083.md)). Decision D2 caps further marker-observation
attempts, and if G3 is still open after them the project switches to this
design instead of tuning the observer further.

Time-multiplexed exclusion needs no mapping. The tile is hidden before the
mirror is read, the frame is admitted only when it was observed after the hide
was confirmed plus a device-measured bound and differs from the last frame read
with the tile shown, the detector runs on that frame, and the tile is shown
again after the capture window closes. Exclusion comes from time, not from
pixels, so the whole mask, candidate and qualification stack is bypassed.

## One cycle

```
show ──────► hide requested ─► hide returned ─► overlay hidden ─┬─ B ─┬── window W ──┬─ inference ─► show ──► …
 tile on     (settings packet)   (HideOverlay)   (runtime event) │     │  admit first  │  tile off     tile on
                                                                  │     │  frame that   │
                                                 confirmed hide ──┘     │  passes the   │
                                                                        │  two rules    │
```

1. **Hide.** The Python side sends visibility off. The bridge records the
   request time, calls `HideOverlay`, records the return, and reports the
   runtime's overlay-hidden event when it polls it. The confirmed hide is the
   event time; without the event within a bound there is no window this cycle.
2. **Wait the bound B.** B is the device-measured time from the confirmed hide
   until the tile is absent from the mirror as read by this process. It is not
   known today (see [device evidence](#device-evidence-needed)).
3. **Window.** Frames observed inside `[confirmed_hide + B, confirmed_hide + B + W)`
   are candidates. One is admitted when its full-frame digest differs from the
   digest of the last frame read before the hide was requested. Everything
   else is rejected and counted.
4. **Infer and show.** The admitted frame goes through the existing conversion,
   detector and tile renderer. The tile is shown again only after the window
   closes, with the result's expiry on the existing clock.
5. **Expiry.** The result's `expires_ns` is `observed_ns` plus the run's maximum
   age, capped at the shared `MAX_DISPLAY_AGE_NS` (1 s), and the bridge hides
   the tile itself at that deadline whether or not a new result arrives. The
   pipeline applies that cap only when a HUD stage is present
   (`pipeline.py:244-246`), so the fallback stage must keep that condition or
   the run relies on the sink's and the bridge's own checks alone.

## What is reused

| Piece | Where (`e4019a4`) | Role in the fallback |
| --- | --- | --- |
| Mirror capture loop and source | `sightcircuit/sources/mirror.py:46-129` (`capture`), `:131-192` (`MirrorSource`) | Unchanged reads at up to 10 per second on the capture thread (`max_fps`, `:46-49`), each a nonblocking `read()` of the node (`sightcircuit/sources/mirror_device.py:236-257`). The design assumes each read returns the loopback's latest frame, so that no queue of stale frames has to drain after a hide; no evidence record establishes that, and the loopback-repeat measurement below tests it |
| Per-read sample digest | `mirror.py:86-91` (`grayscale_sample`, 32 px, `blake2b`; `unchanged_sample_reads`) | Already counts identical consecutive reads; the loopback-repeat measurement reads it |
| Latest-frame handling | `sightcircuit/latest.py` (`LatestSlot`), `sightcircuit/pipeline.py:448-514` (`_run_latest`) | One pending frame; newer frames replace older ones; the stale-input skip at `:498-501` stays |
| Admission checkpoint | `pipeline.py:313-318` (`_admit`), `sightcircuit/context.py:76-113` (`CaptureContext` without a mask) | Source identity is still pinned and checked at every checkpoint; the window rule is added as the caller check |
| Expiry on the existing clock | `pipeline.py:325-345` (`_expiry_ns`, `_expired`, `_expire_presented`), `:406-429`; `sightcircuit/hud/contract.py:89` and `tools/hud_contract.hpp:69` (`MAX_DISPLAY_AGE_NS`, `kMaxDisplayAgeNs`) | The one display cap, shared with the bridge, unchanged |
| Tile renderer | `sightcircuit/hud/render.py:4-26` (`draw_tile`), `HUD_CONFIG` | Names and view arrows only; takes no scene image |
| Overlay settings path | `sightcircuit/overlay.py:207-226` (`set_settings`, `freeze_settings`: only visibility may change once frozen), `:335-340` (`OverlaySink.placement`), `:376-378` (`clear`) | Hide and show are visibility changes on the frozen placement |
| Bridge show and hide | `tools/overlay_bridge.cpp:241-250` (`visibility`), `:377-381` (settings in `stream`) | `ShowOverlay` and `HideOverlay` as today |
| Bridge expiry hide and events | `overlay_bridge.cpp:339-355` (`expire`, `frame_expired`), `:274-288` (`events`: `VREvent_OverlayShown`, `VREvent_OverlayHidden` seen as flags) | The expiry hide is reused as is; the event flags become timestamped lines (below) |
| Digest change detection | `sightcircuit/hud/observation.py:294-336` (`BaselineGate`: full-image `blake2b`, `repeated_frames`, `content_changes`) | The "differs from the last pre-hide frame" rule is this digest applied to one reference frame |
| Lifecycle | `sightcircuit/runtime/guard.py`, the bridge watchdog, `sightcircuit/runtime/manifest.py` | Supervision, bounded termination and the checkpoint manifest apply unchanged |

## What is bypassed

The mask and neutral fill (`sightcircuit/hud/mask.py`), the mapping candidate
and its conversion (`hud/qualification.py`, `hud/candidate.py`,
`scripts/hud_validation_report.py`), the marker and its observer
(`hud/marker.py`, `hud/observation.py`, `scripts/hud_marker_check.py`,
`scripts/hud_marker_observer.py`), the scene precondition (`hud/scene.py`), the
geometric prediction (`hud/geometry.py` projection, `scripts/hud_view_prediction.py`),
the mask-related G5 counters of the validation ledger (`hud/validation.py`) and
the mask-fill evaluation (`sightcircuit/mask_fill_evaluation.py`). The session
tokens (`sightcircuit/sources/session.py`) bind a mapping to its context; the
fallback has no mapping to bind, so it does not need them for exclusion. They
remain useful provenance and are not part of this design.

## The new contract

### Bridge events

Today the bridge reports `overlay_shown_event_seen` and
`overlay_hidden_event_seen` as two flags in its final report, set when
`events()` polls them (`overlay_bridge.cpp:283-284`) and reported only in
marker mode (`:694-696`, under `if (marker)`); the stream mode the fallback
would use reports neither,
and it emits JSON lines for `ready`, `frame_submitted` and `frame_expired`
(`:339-355`, `:400-405`). The fallback needs the visibility transitions as
timestamped lines, in the bridge's `CLOCK_MONOTONIC` nanoseconds (the clock
`monotonic_ns()` reads at `:68-72`, the same clock as `Frame.observed_ns` on
the same host; the time-namespace caveat of
[`timebase_id`](hud-session.md#timebase_id) applies):

| Line | When | Fields |
| --- | --- | --- |
| `hide_requested` | A visibility-off request is decoded, before `HideOverlay` | `cycle`, `request_ns` |
| `hide_returned` | `HideOverlay` returned | `cycle`, `returned_ns`, `ok` |
| `overlay_hidden` | `VREvent_OverlayHidden` polled | `cycle`, `observed_ns` |
| `show_requested`, `show_returned`, `overlay_shown` | The symmetric show transitions | as above |
| `visibility_event_missing` | No hidden event within the bound after a hide request | `cycle`, `waited_ns` |

The `cycle` number lets the Python side correlate lines with its requests.
The settings packet (`OPCODE_SETTINGS`, fixed layout) carries no such field,
so the visibility request becomes its own opcode with a cycle number, added
to `sightcircuit/hud/contract.py` and generated into the header by
`scripts/hud_contract_header.py --write`; the bridge is rebuilt and the
packet tests extended. `OverlayClient._read` (`overlay.py:126-159`) gains an
event queue the fallback stage consumes; today it keeps only the last expiry.
Whether the Frame's runtime delivers `VREvent_OverlayHidden` to the overlay's
own client, and how soon, is unverified; the design fails closed on a missing
event.

**Implemented (F1, 2026-11-08; cloud-checked, no device run).** The request is
opcode `5` of the pipe protocol, payload `uint32 cycle, uint32 visible`
(`VISIBILITY_FIELDS`, `OPCODE_VISIBILITY` in `sightcircuit/hud/contract.py`,
`kVisibility*` and `kOpcodeVisibility` in the regenerated header), encoded by
`sightcircuit.overlay.visibility_packet(cycle, visible)`; no client sends it
yet (F2). The bridge's stream modes (`--stdin`, `--validate-stdin`) write the
seven lines above with the fields in the table, each also carrying
`validation_only` as `frame_expired` does; the marker and test-panel modes are
unchanged. The request line is written before `ShowOverlay`/`HideOverlay`, the
return line after it (`ok` false and then the run ends on a call error, as for
every other call), and the event line when the runtime's overlay event is
polled while that cycle is pending; an event with no pending request stays a
flag in the marker report. The bound for `visibility_event_missing` is
`VISIBILITY_EVENT_BOUND_NS` = 250 ms (`kVisibilityEventBoundNs`), a cloud
default pinned by `tests/test_hud_contract.py`: at least a dozen of the
bridge's 20 ms event polls, ten times the illustrative 25 ms request-to-hidden
term below, and half of the fastest costed cycle; **it is unmeasured on the
Frame**, and the hidden-event delivery measurement replaces it. The wait does
not block the stdin loop: the bound is polled on every input wait beside the
frame expiry, so frames and further requests keep arriving. A hide whose
hidden event has not been polled within the bound, or that is still pending
when input ends or when the next request arrives, is reported missing once
and opens no window; a pending show that is never confirmed is forgotten
without a line (the table specifies the missing line for hides). Visibility
changes through the settings packet remain the pre-F1 path: no cycle, no
lines. The stream report adds `visibility_requests_received` and
`visibility_events_missing`. `OverlayClient` still keeps only the last expiry;
the event queue is F2. Cloud checks: `tests/test_overlay_bridge.py`
(`VisibilityLineTests`), `tests/test_hud_contract.py` (layout, header, bound,
packet limits, the lines named only in the reporting unit).

### Admission window

A window is opened per cycle from the confirmed hide:

- `opens_ns = overlay_hidden.observed_ns + B`, where B is the device-measured
  hide-to-absence bound plus its margin;
- `closes_ns = opens_ns + W`, with W at least one capture read period
  (100 ms at the capture thread's ten reads per second, `mirror.py:46-49`) so
  at least one read falls inside;
- `reference_digest` is the full-frame digest of the last frame read before
  `hide_requested`, taken with the `BaselineGate` digest.

A frame is admitted when `opens_ns <= observed_ns < closes_ns` and its digest
differs from `reference_digest`. `observed_ns` is the read's return time in this
process (`mirror.py:99`), not an exposure time, so B is measured on the same
path. The rule is the caller check at the single admission checkpoint
(`pipeline.py:313-318`), applied again at the output boundary like every
admission check. Rejections are counted by reason: `fallback_frame_before_window`,
`fallback_frame_after_window`, `fallback_frame_repeats_pre_hide` (identical
digest), `fallback_hide_not_confirmed`, `fallback_window_without_frame`.

**The loopback-repeat hazard.** The mirror node is a v4l2loopback device. The
design assumes its `read()` returns the latest buffer the writer produced; the
one recorded read series ([E12](../evidence/e012.md)) shows only that a 32 px
sample changed on 99 of 99 reads, so this is the assumption the loopback
measurement tests, not an established fact. Under it, if SteamVR's mirror
writer stalls, or produces frames more slowly than this process reads, two
reads return the same bytes. A frame identical to the pre-hide reference is
rejected whether the cause is a repeated buffer or a tile still composited in
an unchanged scene; both are the fail-closed outcome. A frame that differs
proves only that something changed, not that the tile is gone: that is why B
is a measured bound and not inferred from content. The capture report's
`sample_change_proves_live_camera` stays false (`mirror.py:41-42`), and so
does the baseline's `camera_freshness_verified` (`observation.py:302`): a
changed frame is not a fresh camera exposure.

### Show after the window, expiry on the existing clock

The tile is shown only after `closes_ns`, never while a frame can still be
admitted, so no admitted frame can carry the tile. The shown result's
`expires_ns` is set as today (`pipeline.py:325-333`), capped by
`MAX_DISPLAY_AGE_NS`, and the bridge's own expiry hides the tile at that
deadline (`overlay_bridge.cpp:339-355`), independent of the next cycle. The
next hide request starts the next cycle; a hide that arrives while the tile is
already hidden by expiry still needs its confirmed hidden event or the window
is not opened.

## Device evidence needed

Each is a separately approved device step; none has run. Each would be recorded
as a numbered evidence entry with the checkpoint manifest, which now records
the kernel, OS build and SteamVR package.

| Measurement | How | What it decides |
| --- | --- | --- |
| Hide-to-absence latency (B) | A bridge test mode toggles a high-contrast test tile on and off at known monotonic times while a reader takes the mirror at ten reads per second and digests each frame; the wearer holds still so the tile is the only change. For each hide, the first read whose digest changes after the hidden event gives one latency sample; many cycles give the distribution. B is the largest sample plus a margin | The bound; whether the overlay appears in the mirror at all (if no read ever changes, the fallback is moot and so is the mask) |
| Hidden-event delivery | The same run records `overlay_hidden.observed_ns - hide_returned.returned_ns` per cycle and the count of cycles without an event | Whether the confirmed hide can rest on the event, and its bound |
| Loopback repeat behaviour | Ten reads per second against a static scene and a moving scene, counting identical consecutive full-frame digests (`unchanged_sample_reads` already counts the 32 px sample: 0 of 99 in [E12](../evidence/e012.md), moving scene) | Whether windows can starve; the writer's effective rate |
| Show-to-visible latency | `show_returned` to the wearer's report, or to the first changed read with the tile present | The duty-cycle table below |
| Frame inference time | Item C5's `mirror_recognize` p95 | The window-to-show time |

The wearer's answers (tile visible, passthrough and tracking normal, no green
input, [E59](../evidence/e059.md)) are recorded apart from the measurements,
as every device record does.

## Costs

Two rates are involved and must be kept apart. The capture thread reads the
mirror at up to ten reads per second (`mirror.py:46-49`, `max_fps`); the
pipeline paces inference at its own period `P = 1 / max_fps`
(`pipeline.py:250`), five per second today, and one cycle of the fallback is
one pipeline period. The window W is counted in capture reads (at least one
capture read period, 100 ms at ten reads per second), not in pipeline periods.

Take the hide request as time 0 and the terms: `h`, request to confirmed
hidden; `B`, the bound; `W`, the window; `I`, inference; `s`, show request to
shown. The worst case for age is the earliest admissible frame, observed at
`h + B`. Inference is counted after the window closes, which is the
conservative order (overlapping it with the window only shortens the times).
Then, per cycle:

- the tile is off from 0 to `h + B + W + I + s` and on from there to the next
  hide request at `P`, so the on time is `P − (h + B + W + I + s)`, and a
  cycle whose off time exceeds `P` cannot complete at that pacing;
- the age of the result when it is shown is `W + I + s`;
- the age of the result when the next hide request takes it down is
  `P − (h + B)`;
- the age of the result when the next result replaces it on the tile, after
  being hidden in between, is `P + W + I + s`;
- the display cap acts only if the result is still shown at
  `observed_ns + MAX_DISPLAY_AGE_NS`, that is only when `P − (h + B)` reaches
  1,000 ms.

The terms other than W and P are unmeasured on Frame. The table is
illustrative with `h = 25 ms`, `B = 100 ms`, `W = 200 ms`, `I = 100 ms` and
`s = 25 ms` (off time 450 ms per cycle), to be recomputed from the
measurements; at the capture thread's ten reads per second W could be 100 ms,
which shortens every age below by 100 ms.

| Pipeline pacing | P | On time per cycle (duty) | Age when shown | Age at the next hide | Age when replaced |
| --- | --- | --- | --- | --- | --- |
| 5 per second | 200 ms | cycle cannot complete (450 > 200): the stage would run at the rate the off time allows, about 2 per second | | | |
| 3 per second | 333 ms | cycle cannot complete (450 > 333) | | | |
| 2 per second | 500 ms | 50 ms (10 %) | 325 ms | 375 ms | 825 ms |
| 1 per second | 1,000 ms | 550 ms (55 %) | 325 ms | 875 ms | 1,325 ms, hidden since 875 ms; the cap at 1,000 ms is not reached while shown |

So the fallback runs at one to two cycles per second, not five. At two per
second the tile flickers at 2 Hz with a 10 % duty cycle and each result is
shown for 50 ms; at one per second the tile is off for about half of every
second and the shown result is up to 875 ms old before it goes down. The
detector's own latency on Frame adds directly to the off time and to every
age. Each capture read also costs one full-frame digest (about 6 MB of bytes
hashed) and the bridge emits a handful of extra lines per cycle.

## Risks

- **B too small admits a frame that still carries the tile.** Nothing in the
  content rule detects the tile, so the measured bound and its margin are the
  only guard. The measurement must cover the slowest path seen, and the margin
  must be stated.
- **The hidden event may not arrive, or arrive late.** The design fails closed
  (no window), so the HUD then shows nothing new; a run without any admitted
  frame is a stop, not a silent pass.
- **Repeated loopback frames starve the windows.** Every repeat is counted;
  a run whose windows mostly starve ends with a named reason.
- **Flicker and comfort.** A tile that blinks at one to two hertz near the
  wearer's view edge is a product cost, not a safety one, and the owner judges
  it.
- **Latency.** Recognition at one to two frames per second with results near
  the one-second cap is slower than the five reads per second the mask path
  allows.
- **Gates and acceptance rows.** The three software gates stay closed. Their
  `lifted_by` rows name G4, G5 and G6; G3 and G4 do not apply to a design with
  no mapping, so adopting the fallback needs a reviewed revision of the
  acceptance rows and of the gate table's `lifted_by` references, by the
  planner and owner, before any fallback run on the device. This document does
  not propose those rows.
- **Clock domains.** Bridge timestamps and `Frame.observed_ns` are the same
  `CLOCK_MONOTONIC` on the same host; a time namespace difference would break
  the comparison silently unless the `timebase_id` rule is kept.

## What would become dead

Line counts are `wc -l` at `e4019a4`, including docstrings and blank lines.

| Code | Lines | Tests | Lines |
| --- | --- | --- | --- |
| `sightcircuit/hud/mask.py` | 286 | `tests/test_hud_mask.py` | 560 |
| `sightcircuit/hud/candidate.py` | 260 | `tests/test_hud_candidate.py` | 775 |
| `sightcircuit/hud/qualification.py` | 392 | `tests/test_hud_qualification.py` | 319 |
| `sightcircuit/hud/observation.py` | 575 | `tests/test_hud_marker_observer.py` | 1,426 |
| `sightcircuit/hud/scene.py` | 462 | `tests/test_observer_scene.py`, `test_observer_scene_seed.py` | 786 |
| `sightcircuit/hud/marker.py` | 153 | `tests/test_hud_marker_check.py` | 482 |
| `sightcircuit/hud/geometry.py` (projection and eye partition) | 333 | `tests/test_hud_prediction.py` | 267 |
| `sightcircuit/hud/stage.py` (`prepare`, the mask half) | 100 | `tests/test_hud_calibration.py` | 479 |
| `sightcircuit/hud/validation.py` (mask counters) | 130 | `tests/test_hud_fill.py`, `test_hud_validation.py` | 1,000 |
| `sightcircuit/mask_fill_evaluation.py` | 653 | `tests/test_mask_fill_evaluation.py` | 336 |
| `scripts/hud_marker_observer.py`, `hud_marker_check.py`, `hud_view_prediction.py`, `hud_validation_report.py` | 629 | | |
| **About** | **4,000** | | **6,400** |

Kept: `hud/contract.py` (configuration, packet layouts, limits, the display-age
cap), `hud/render.py`, `hud/native.py` and the generated header, `overlay.py`,
the bridge, the pipeline, the mirror source, the session tokens and the
lifecycle modules. `scene.py`'s file-only `--analyze-still` and the marker
bridge mode may stay as diagnostics; the marker mode is the natural test tile
for the hide-to-absence measurement. Retirement is a separate, reviewed step:
nothing is deleted by adopting this design.

## Work breakdown

Sizes follow the work order (S, M, L). "Device" marks a step that runs on the
Frame and needs the owner's approval for that specific run.

| Step | Work | Size | Depends on |
| --- | --- | --- | --- |
| F1 | Bridge visibility opcode with a cycle number; `hide_requested`, `hide_returned`, `overlay_hidden`, the show counterparts and `visibility_event_missing` as timestamped lines; header regeneration, rebuild, packet and source-text tests | M | E1 (the bridge split) first, so the lines land in the reporting unit |
| F2 | `OverlayClient` event queue and a cycle API on the sink (hide, await confirmation, show after a deadline) | S | F1 |
| F3 | Device measurements: hide-to-absence latency, hidden-event delivery, loopback repeats, show-to-visible; one evidence entry each | S each, device | F1, approvals |
| F4 | Capture window and admission rule in the pipeline: reference digest, `opens_ns`/`closes_ns`, rejection counters, report fields, boundary tests at both ends of the window | M | F3 (the value of B) |
| F5 | The fallback stage: hide, window, infer, show state machine on `_run_latest`; tile rendering without a mask; run reports | M | F2, F4 |
| F6 | Acceptance rows and gate references for the fallback (replacing G3 and G4 for this path), a reviewed edit to the scope, the work order and `sightcircuit/gates.py` | S | the planner and owner |
| F7 | Command and operator guide; the 60-second run under the diagnostic tier's scope record | M, then device | F5, F6, A7 |
| F8 | Retire or quarantine the dead stack and its documentation | M | an accepted fallback run |

The whole is the "L later" of item A11. Nothing above starts unless decision
D2's exit rule is reached.
