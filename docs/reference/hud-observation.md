# HUD observation and prediction tools

Use this reference for the marker observer, file-only scene analysis, geometric prediction and the pre-attempt review of the stops before a marker. Observation and prediction support qualification; neither qualifies a mapping by itself.

[Architecture](../architecture.md) · [Qualification method](../hud-qualification.md) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

<a id="geometric-prediction-cross-check-2026-11-04"></a>

## Geometric prediction cross-check

Package M adds a geometry-only prediction of where the HUD quad should land in
the mirror's native pixels, to cross-check a G3 observation and the G4
composition contract. **A prediction cannot qualify a mapping.** It is
labelled `kind: geometric_prediction`, `prediction_only: true` and
`qualifies_mapping: false`; `sightcircuit.hud.qualification` and `candidate`
never read it, and a candidate whose evidence names `geometric_prediction` as
its kind is rejected (`hud_evidence_provenance_invalid`).

- `sightcircuit/hud/geometry.py` `project()` clips an eye-space polygon against
  a near plane (z = −0.02 m) and side planes widened by a quarter of the frame,
  then applies the pinhole given by the four `GetProjectionRaw` tangents.
  `hull()` is a monotone-chain convex hull. Both are ported from
  DeeJanuz/vr-recorder `vr-mask.cpp` L917-L977 (MIT; see
  [THIRD_PARTY](../../THIRD_PARTY.md#hud-geometric-prediction)). Upstream's comment
  says "no clamping", but its code clamps at four times the larger frame
  dimension (L949-L952). Here no point is clamped: after the side planes every
  point lies inside the widened window, so `project()` checks that and raises
  `hud_projection_outside_window` instead of bending the polygon.
- `predict_hud_footprint(view_record, config)` places the head-relative quad
  from `HudConfig` (identity rotation at `(x_m, y_m, -distance_m)`, height from
  the texture aspect), moves it into the selected eye with the inverse of
  `GetEyeToHeadTransform`, projects it and clips it to the view size. Its
  `status` is `inside`, `partly_off_frame`, `off_frame`, `behind_eye` (every
  corner at or behind the near plane) or `degenerate` (the projected hull has no
  area: an edge-on quad). A
  `both` view mode or a cropped view is reported as unsupported rather than
  guessed.
- Its assumptions are listed in the prediction: the mirror's native pixels are
  the IVRHeadsetView image of that eye at the view size, uncropped, with no lens
  or orientation change. Whether `/dev/video99` carries the IVRHeadsetView
  output is **unknown**, and the prediction records it under `unknowns`.

The view record comes from a read-only bridge mode:

```bash
artifacts/overlay-bridge/sightcircuit-overlay-bridge --headset-view-record --allow-runtime-query
```

It joins a running SteamVR as `VRApplication_Background` only (never an
Overlay connect, never a vrserver start) and prints `GetHeadsetViewMode`,
`GetHeadsetViewSize`, `GetHeadsetViewCropped`, `GetHeadsetViewAspectRatio`,
`GetHeadsetViewBlendRange`, and `GetEyeToHeadTransform` and
`GetProjectionRaw` for both eyes, in one JSON report under `view_record`. It
never calls `SetHeadsetViewMode` or any other `Set*` call; a test checks the
bridge source for that. The calls follow the read side of vr-recorder
`vr-mask.cpp` L1381-L1393. Without SteamVR it exits with one
`vrserver_not_running` error.

A saved record becomes a prediction with (owner decision, 2026-11-04):

```bash
python3 scripts/hud_view_prediction.py VIEW_RECORD.json
```

It accepts the whole bridge report or just its `view_record` object, reads
one regular file of at most 64 KiB, makes no OpenVR call, and prints a
`hud_view_prediction` report whose `prediction` is the labelled object above
with `mapping_qualified` false. Exit 0 for a prediction (whatever its
`status`), 1 for an unusable record.

Cloud checks: synthetic quads inside the frame, partly off it, behind the eye
and crossing the eye plane match hand-computed polygons, and the command is
driven on saved records (`tests/test_hud_prediction.py`); the view-record
mode's refusals are covered in `tests/test_overlay_bridge.py`. Reading the view record on Frame is
VR contact: it is person-run or Tier B, and it needs the owner's approval.

## Marker stop path

The next G3 attempt requires a changed marker-stop path with regression
coverage. That change was made on 2026-11-01 and is covered by cloud tests
only. The native marker mode ignores stdin. A requested stop, such as an
observer rejection, therefore closes stdin and waits for the bridge's own
display deadline plus two seconds. Until then the marker stays up, at most
`--seconds`. A V4-shaped frame-3 tag/bit rejection, run through the real
validation bridge, worker and guardian, now ends with clean software cleanup
and no forced termination. That run used generated input; it is not Frame
evidence. Ctrl-C, SIGTERM, a ready timeout or an overrun still escalates to
SIGTERM, then SIGKILL. That stop is reported as forced, with `cleanup_ok: false`.
Bridge reports add `stop_requested` and `escalated`. If the guardian is killed,
the supervisor now ends and reaps the worker group itself, and still reports
`cleanup_ok: false`. This does not explain the green input, the tag mismatch or
physical recovery. The [lifecycle review](../lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01)
has the details.

<a id="pre-attempt-review-of-the-stops-before-a-marker-2026-11-07"></a>

## Pre-attempt review of the stops before a marker (2026-11-07)

Item A3 of the [architecture plan work order](../live-capture.md#architecture-plan-work-order-2026-11-07):
an offline review of everything that can stop the next marker-observer attempt
(A4) before a marker appears, plus the planner's addendum of the same day on
the environment differences the device record must carry. File and line references are at revision
`45771fe` unless they say "now". The review ran no device operation and makes
no claim about device behaviour; every result below is a cloud check. **The
headset check of the bridge's connect budget remains a separately approved
device step**, which this review neither runs nor schedules.

### 1. The bridge's connect budget and the client's ready wait

Marker mode works like this. The bridge starts its display timer at process
start, arms its connect watchdog (`tools/overlay_bridge.cpp:631`, 6 s at
`:43`), then connects: up to three `VRApplication_Background` probes and three
`VRApplication_Overlay` `VR_Init` attempts with 250 and 500 ms of backoff
(`tools/overlay_lifecycle.hpp:144-145,160-179`). Only after a connection,
`IVROverlay`, the headset check and `CreateOverlay` (`overlay_bridge.cpp:166-170`)
does it print its ready line (`:648`), and only after that line does it
generate and submit the marker (`:650-660`). If the watchdog expires, the
bridge writes one prepared error line, `watchdog_expired:connect` with
`exit_code` 124, `cleanup_ok: false` and `cleanup_skipped_by_watchdog: true`,
and `_exit`s without `VR_Shutdown` (`overlay_lifecycle.hpp:44-52`). The
Python client (`sightcircuit/hud/marker.py:58-64`) waits 8 s
(`READY_TIMEOUT_SECONDS`, `:16`) for the ready line. Its reader thread
delivers the bridge's error line as the client's error, so the client raises
the bridge's own reason, and `close()` finds the process already gone: no
escalation, `forced_termination: false`. If instead the bridge is silent for
the whole wait, the client raises `marker_ready_timeout` and escalates to
SIGTERM, then SIGKILL, at once, because a bridge without an accepted ready line
could still show the marker after the run had failed
([marker stop path](#marker-stop-path)).

The 6 s budget arrived with the November 6 bridge lifecycle change (E84), after
E83's bridge, and the review of that change asked that the budget and the
client's wait move together: the wait must stay above the budget, or the client
would force a termination with `cleanup_ok: false` and lose the bridge's named
report; and a slow but healthy SteamVR that the old single `VR_Init` would have
finished inside the 8 s wait can now be cut off at 6 s.

**Decision and change (now).** The two numbers have one source.
`sightcircuit/hud/contract.py` defines `BRIDGE_CONNECT_BUDGET_SECONDS = 6`,
`BRIDGE_READY_MARGIN_SECONDS = 2` and
`BRIDGE_READY_WAIT_SECONDS = 8` as their sum. The generated header
(`tools/hud_contract.hpp`, written by `scripts/hud_contract_header.py --write`)
carries `kBridgeConnectBudgetSeconds`, and the bridge's `kWatchdogConnectSeconds`
takes it. `MarkerClient.READY_TIMEOUT_SECONDS` and the `OverlayClient` ready wait
are both `BRIDGE_READY_WAIT_SECONDS`. The margin is the time the client allows for
process start, SDK loading and the delivery of the watchdog's own line.
`tests/test_hud_contract.py` pins the sum, the header value, the bridge
source and both client sites, refuses a literal at any of them, and checks
that the documented 20 s run still leaves at least three reads after the
baseline window, the ready wait and the marker warmup.
`tests/test_hud_marker_check.py` drives a fake bridge that writes the watchdog
line and exits 124: the client raises `watchdog_expired:connect` inside its
wait and escalates nothing. A rebuild of the bridge after the header change is
part of the normal check sequence.

**Why the budget stays at 6 s.** No Frame measurement of the probe-then-connect
order exists. The only Frame timings on this path are older: in V4
([E75](../evidence/e075.md)) the marker was requested and submitted within
213 ms of native start through a single Overlay `VR_Init`, and
[E81](../evidence/e081.md) streamed 400 panel frames with the old order. E83's
runs never reached the bridge. So the budget cannot be justified or refuted
offline, and the budget's cost is bounded: the display timer starts at process
start, so a connect that uses the whole budget leaves 14 s of marker display in
a 20 s run, and a budget expiry stops the run before any marker is generated.
The pairing makes a later change one Python edit, a header regeneration and a
rebuild. Whether 6 s is enough for the Frame's SteamVR is the headset check
listed in the [overlay reference](overlay.md#connect-order-output-and-watchdog-package-k-2026-11-04),
a separately approved device step.

**What an expiry looks like at the next attempt.** The observer report is
`rejected`, exit 1, `reason: watchdog_expired:connect`, `cleanup_ok: false`,
and `bridge.native_report.cleanup_skipped_by_watchdog: true`. No marker was
generated or submitted, because both come after the ready line. The
`cleanup_ok` value is the software statement that the bridge skipped
`VR_Shutdown`; it says nothing about the headset.

### 2. The marker warmup window

After the ready line the observer tolerated reads without any marker candidate
(`no_complete_marker`) for 2 s while no sample existed, counted them as
`warmup_frames_without_marker`, and rejected the first such read after that
(`scripts/hud_marker_observer.py:145,152`). The literal was not named or
documented.

**What the window covers.** The bridge's marker generation and submit after
its ready line, the compositor's pickup, the mirror's frame path and one read
period: the observer reads at most five times a second (`:143` runs inside a
read callback, so the first read after the ready line is the next scheduled
one). The mirror read returns the loopback device's latest frame
(`sightcircuit/sources/mirror_device.py:236-257`, `os.read` on the node), so
no queue of stale frames has to drain after the ready wait blocked the loop.
Recorded evidence is thin: in V4 the first read after the marker request, at
most 185 ms after native start, already carried marker-shaped content whose
tags then failed ([E75](../evidence/e075.md)). That is one timing on an older
code path, and whether the fragment was the marker was never confirmed.

**Change (now).** The window is the explicit constant
`MARKER_WARMUP_SECONDS = 2.0` beside `BASELINE_WINDOW_SECONDS`, with its
derivation in the source, and every report records it as
`marker_warmup_seconds`. The default is unchanged: a longer window never admits
a sample the observer would otherwise refuse, it only delays a no-marker
rejection, so 2 s is the safe side as long as the run still has room for three
samples, which the contract test checks (at 20 s: 20 − 2 − 8 − 2 = 8 s remain,
0.6 s are needed). It is a reviewed constant pinned by the checkpoint manifest,
not a command option, because decision D2 rules out tuning on the device.

**What the window does not tolerate, deliberately.** A read whose matcher
finds an orphan tag or a partial copy (`orphan_marker_tag_or_incomplete_copy`,
V4's shape) rejects at once, with no sample and no warmup count. That check is
the fail-closed guard against a copy hidden beside a complete one, and it is
unchanged. Tolerating it during the window would delay a V4-shaped rejection
by at most the window and replace the first rejected read's diagnostics with
the last one's; that trade is noted for the planner, not made here.
`tests/test_hud_marker_observer.py` shows the count following the constant at
three window lengths and a partial copy rejecting without a count.

### 3. `--record-session-tokens`

**The next attempt should not pass it (the default).** Three reasons:

- Its report cannot become a mapping candidate anyway. The tracking tokens stay
  undeclared until the native tracking reporter exists (item A9), and
  `--from-observer` rejects any report with an undeclared tracking token
  (`sightcircuit/hud/candidate.py:217-219`). A4's purpose is to see a marker
  and close G3, not to produce a candidate; the candidate for the diagnostic
  run comes from a later observation after A9, inside the candidate's 300 s
  lifetime.
- It adds a stop before the marker that has never run on Frame. The
  declaration scans /proc and hashes the running `vrserver` binary (up to
  256 MiB) under a 4 s budget
  ([session tokens](hud-session.md#observed-session-tokens-for-the-candidate-2026-11-03)).
  A slow declaration rejects the run `observer_session_tokens_declaration_too_slow`
  before capture opens. That is a preparation stop and does not count against
  the attempt budget, but it does spend the approved session.
- What it would buy is provenance, not qualification: `runtime_id` (the SHA-256
  of the running `vrserver`) and `session_id` in the record, which E75 lacked.
  A later run that is meant to become a candidate records them anyway.

Without the flag the report says `session_tokens: {recorded: false, reason:
session_tokens_not_requested}`. If the planner wants the runtime digest from
A4 regardless, the flag is the only path today; pass it knowing the budget, and
that the declaration runs before any frame is read.

### 4. Every stop before a marker, and what counts

The [attempt budget counting rule](../poc-scope.md#architecture-decisions-2026-11-07),
as the planner refined it on 2026-11-07 after this review's finding F1: an
attempt counts against the D2 budget when the observer judged the scene. A
rejection at the scene precondition counts, and anything after the marker tile
appears counts. A stop caused by connectivity or preparation does not count,
whatever its position in the code order: a missing bridge build, a failed
manifest verification, a headset not connected, a connect watchdog expiry, a
marker ready timeout, and the vrserver session stops. The precondition runs
inside the matcher on every baseline read (`sightcircuit/hud/observation.py:308`,
through `locate_copies` `:121` and `sightcircuit/hud/scene.py`), so E83's
stops count. In run order:

| Stop (`reason`) | Where (45771fe) | Counts? |
| --- | --- | --- |
| Manifest `--verify` differs: changed module, rebuilt bridge, dirty tree, other interpreter or package | `scripts/checkpoint_manifest.py:27-40`, `sightcircuit/runtime/manifest.py:292-317`; the run script stops before the observer ([procedure](../guides/hud-checkpoint.md#approved-run)) | no (named by the rule) |
| Partial opt-in, `--bridge` or `--record-session-tokens` without the full opt-in | `scripts/hud_marker_observer.py:283-287` (`parser.error`) | no: nothing opens |
| `unverified_device`, `mirror_unavailable`, `ambiguous_device` | `sightcircuit/sources/mirror_device.py:93-104`, called at `hud_marker_observer.py:290` | no: the supervisor never starts |
| Supervisor `timed_out` or `guardian_failed` before a worker report | `sightcircuit/runtime/guard.py:270,451` | no, unless the worker report shows a later phase |
| Worker options: `observer_explicit_scope_required`, `observer_session_tokens_option_invalid` | `hud_marker_observer.py:269,271` | no |
| `observer_duration_out_of_bounds`, `observer_mirror_opt_in_required`, `observer_fixture_backend_required`, `observer_output_opt_in_required` | `hud_marker_observer.py:92-99` | no |
| `native_marker_bridge_not_built` | was `sightcircuit/hud/marker.py:46`, reached at `hud_marker_observer.py:143` only after the baseline; now `MarkerClient.check_built()` runs first in `observe()`, before the declaration and before capture | no (named by the rule); now also before any frame |
| `observer_session_tokens_declaration_failed`, `observer_session_tokens_declaration_too_slow` (flag only) | `hud_marker_observer.py:106-113` | no: before capture |
| Capture open or read error (`device_changed`, `unsupported_driver`, `unsupported_capabilities`, `format_changed`, `stream_ended`, `no_frames`, …) | `sightcircuit/sources/mirror_device.py:183-257`, surfaced at `sightcircuit/sources/mirror.py:107` and `hud_marker_observer.py:166` | no if no read reached the matcher; otherwise yes |
| `observer_format_changed` in the baseline | `hud_marker_observer.py:136` | yes: a read reached the precondition |
| `observer_bright_saturation`, `observer_speck_density`, `observer_contour_budget`, `observer_quad_budget`, `observer_outline_budget`, `observer_contour_census_mismatch` | `scene.py:74-78`, from `observation.py:308` (E83's stop) | yes |
| `baseline_processing_exceeded_observation_deadline`, `baseline_distinct_clear_frames_not_established` (an unchanging view for the 2 s window) | `hud_marker_observer.py:140,170`, `observation.py:300` | yes: the precondition passed on those reads |
| Bridge usage or start-up errors: exit 64, `watchdog_unavailable`, `parent_death_signal_unavailable` | `overlay_bridge.cpp:571-595` | no: a bridge build or launcher fault |
| `vrserver_not_running:<symbol>`, `vrserver_lost_before_overlay_connect:<symbol>`, `overlay_connect_failed:<symbol>` | `overlay_lifecycle.hpp:164-179`, from `overlay_bridge.cpp:638` | no: the runtime, not the scene, stopped the run (see F1) |
| `watchdog_expired:connect` (6 s) | `overlay_bridge.cpp:43,631` | no: as above |
| `marker_ready_timeout` (8 s, forced termination) | `marker.py:58-64` | no: as above |
| `IVROverlay_028_unavailable`, `headset_not_connected`, `CreateOverlay:*`, `SetOverlay*:*` | `overlay_bridge.cpp:166-176` | no (the headset case is named by the rule) |
| `calibration_deadline_before_marker` | `overlay_bridge.cpp:641,653` | no: the connect outlasted `--seconds` (see F2) |
| `overlay_image_failed`, `SetOverlayRaw:*`, `ShowOverlay:*`, `calibration_deadline_before_show`, `calibration_submit_overran_deadline`, `calibration_visibility_query_overran_deadline` | `overlay_bridge.cpp:244,258,284,655-660` | yes: the marker was requested; whether it appeared is the wearer's report |
| `no_complete_marker` after the warmup window | `hud_marker_observer.py:152` (now `MARKER_WARMUP_SECONDS`) | yes: the marker never reached the mirror, or not whole |
| `orphan_marker_tag_or_incomplete_copy` (V4, E75), the other matcher rejections, `insufficient_complete_marker_samples` | `observation.py:280,368`, `hud_marker_observer.py:172` | yes: after a marker appears |

### 5. Environment differences against the E83 attempts

Planner addendum of 2026-11-07, from the Frame report in
[E85](../evidence/e085.md). The E83 attempts ran on SteamOS 0.5.6 (build
20261022.6153909), kernel 6.18.0, SteamVR package `deckard-steamvr-rel`
r25765468+0ab5c24d-1 and a Python 3.12 environment. The Frame moved on
2026-11-07 to SteamOS 0.5.8 (build 20261106.6144341), kernel 7.2.0, SteamVR
package r26496310+24cb4748-1, and the system Python changed from 3.12 to 3.14,
which left `.venv-replay` unusable until a reviewed rebuild (another coder owns
that pin review). None of these is a stop the observer can detect, and this
review makes no claim about how any of them behaves. **The next attempt's
device record must carry the environment differences against E83 as a
recorded item**: the OS build, the kernel release, the SteamVR package version
and the interpreter and its environment, beside the checkout and bridge
digests. The checkpoint manifest now records the SteamVR package identity
(`platform.steamvr_package`, read from the pacman local database by file reads
only, unknown with a reason where absent; `sightcircuit/runtime/manifest.py`,
`tests/test_checkpoint_manifest.py`) next to the kernel and OS release it
already recorded, so a manifest written on Frame for the attempt carries them,
and a SteamVR update between `--write` and `--verify` is a difference. A
manifest written before the rebuild does not verify afterwards (interpreter
and package versions differ), which is the intended stop.

Findings for the planner, outside this item's scope:

- **F1, the rule's examples against the code order, resolved.** Two of the
  rule's originally named preparation failures, the bridge build and the
  headset, were reached only after the baseline had passed the precondition
  on two reads. The bridge build is now checked before anything opens;
  `headset_not_connected` and the other connect stops still come after the
  baseline, because only the bridge's `VR_Init` can detect them. The planner's
  call (2026-11-07, recorded in the scope document): an attempt counts when
  the observer judged the scene, that is a rejection at the scene precondition
  or anything after the marker tile appears; a stop caused by connectivity or
  preparation does not count whatever its position in the code order: missing
  bridge build, failed manifest verification, headset not connected, connect
  watchdog expiry, marker ready timeout, and the vrserver session stops. The
  table above and the [checkpoint guide](../guides/hud-checkpoint.md#reading-the-result)
  follow that wording.
- **F2, short runs.** A `--seconds` at or below the connect budget plus the
  bridge's start can end `calibration_deadline_before_marker` after a healthy
  but slow connect. The documented run is 20 s; `hud_marker_check.py --validate
  --seconds 3` makes no connect and is unaffected.
- **F3.** The V4 tag mismatch is untouched by O3 and by this item, and remains
  the likeliest stop after a marker appears.
- **F4.** A connect watchdog expiry skips `VR_Shutdown` by design
  (`overlay_lifecycle.hpp:44-52`); the process exit releases the client. No
  evidence says how the Frame's runtime treats that.

Cloud checks for this item: `tests/test_hud_contract.py`
(`test_bridge_connect_budget_and_ready_wait_move_together`, the header reach
test with the new value), `tests/test_hud_marker_check.py`
(`test_connect_watchdog_report_names_the_stop_before_the_ready_wait_and_is_not_forced`,
`test_check_built_is_the_preparation_check_start_marker_applies`) and
`tests/test_hud_marker_observer.py`
(`test_missing_bridge_build_rejects_before_the_declaration_capture_or_any_read`,
`test_marker_warmup_window_is_the_documented_constant_and_tolerates_only_absent_markers`).
A worker report that stopped at the pre-flight with a session given records
`session_tokens_not_declared`, a new unrecorded reason in the worker contract.

<a id="observer-scene-precondition-2026-11-04"></a>

## Observer scene precondition

**Recorded device result.** [E83](../evidence/e083.md) records two observer
runs on 2026-11-03 (`--seconds 20`, checkout `7c72a4e`). Both stopped in under
0.5 s with `observer_contour_budget`, before bridge initialization or marker
output. The first run counted 229 contours, then 2,680 with a screen in view;
the retry counted 2,720 while facing a wall that looked dim to the wearer.
Cleanup, passthrough and tracking were normal. The observer saved no images,
so the rejected pixels remain unknown. G3 stays open: neither run observed a
marker or established a mapping. Another attempt needs a reviewed precondition
change and new approval.

**The rule then.** Before looking for a marker, `locate_copies` thresholded
the grey frame at 180, traced every bright contour (`cv2.RETR_LIST`) and
rejected the frame above 2,048 contours. That count included single-pixel
specks, because the 25 px area filter for candidate quads came later. The
budget had been tuned on synthetic frames only. It also bounded nothing: it
was checked after `findContours` had already traced every contour. One
1080p lattice of 518,400 contours took 6 s to trace on the x86 cloud CPU.

**Statistics and still analysis (packages O1 and O2).** The precondition lives
in `sightcircuit/hud/scene.py`, and `locate_copies` calls it, so
`scene_statistics(image)` describes exactly what the observer applies.
[E84](../evidence/e084.md) records the merged implementation's cloud checks,
separately from E83's device result. It holds counts only, never pixels:

- the bright pixel count and fraction;
- grey p50, p90 and p99 (nearest rank);
- the contour count and its split into `cv2.contourArea` bands (below 25, 25
  to below 400, 400 to below 10,000, and 10,000 or more). The area is that of
  the polygon through the pixel centres, so a 5 × 5 blob has area 16;
- the candidate-quad and outline counts;
- the budgets, and the first limit, if any, that rejects the frame.

`hud_marker_observer.py --analyze-still STILL` prints those statistics for one
saved PNG or JPEG still.

- It never discovers or opens the mirror, a camera or the bridge.
- It refuses `/dev`, `/proc` and `/sys` paths, symbolic links, FIFOs,
  directories and other non-regular files before opening them. The file it
  then opens must be the same regular file, or it is closed unread. Only a
  parent directory swapped by another process in between could redirect that
  open.
- It reads at most 64 MiB and checks the image size in the file header before
  decoding.
- A PNG written by `mirror_recognize.py --still` claims to be the native mirror
  frame without loss (`fidelity: lossless_png_origin_unverified`). Its
  statistics equal the observer's on that frame only if it is one; the report
  says `provenance_verified: false` until its SHA-256 is matched to a capture
  report ([procedure](../guides/hud-checkpoint.md#measure-the-observers-scene-precondition-on-saved-stills)).
  A JPEG only approximates a mirror read.

A run stopped by a precondition check reports that frame's
`scene_statistics` once, with `scene_statistics_source`: the `baseline` or
`marker` phase and the read's sequence number. The worker report contract
accepts them only in a rejected report whose reason is the rejection they
name, and never with a rejection of none. There are two exceptions, and in
both the run is still rejected. If the statistics themselves fail, the match
diagnostics name the error in `scene_statistics_error`. If the traced contour
count disagrees with the census, the run is rejected as
`observer_contour_census_mismatch` with no statistics; that cannot happen while
the census is exact.

### The O3 rule (2026-11-06)

O3 replaces the raw count with two limits on marker-detection risk and three
compute bounds. The checks run in this order; the first that fails names the
rejection, and the statistics name the same one:

1. `observer_bright_saturation`: more than half of the pixels are at grey 180
   or above.
2. `observer_speck_density`: more than 1,700 specks per megapixel. A speck is
   a bright 8-connected component whose bounding box fits in 5 × 5 px.
3. `observer_contour_budget`: more than 4,096 bright contours. They are counted
   before any is traced, from connected components: bright 8-connected
   components, plus dark 4-connected components that do not touch the frame
   edge. That sum is exactly the number `findContours` returns, as a property
   test on random masks checks. After tracing, the traced count must equal
   the census, or the frame is rejected as `observer_contour_census_mismatch`,
   which backs the census against a change in OpenCV.
4. `observer_quad_budget`: more than 256 candidate quads. The scan stops at the
   257th.
5. `observer_outline_budget`: more than 16 outline-sized quads (shortest edge
   at least 32 px, longest at least 80 px, the matcher's own test). This is
   checked before any quad is warped.

The matcher after these checks was unchanged by O3; item A5 changed its
stage-two decode ([below](#stage-two-decode-at-the-mirrors-scale-a5-2026-11-07)).

| Constant | Value | Derivation |
| --- | --- | --- |
| `MAX_BRIGHT_FRACTION` | 1/2 | A copy placed at random touches bright structure with probability at least the bright fraction. Above 1/2, a copy is therefore more likely than not to touch it, however the bright area is laid out. Touching is exposure, not certain loss, but it is usually loss. On composites, a white surround, a bright half-plane, a corner block or a thin line reaching the copy defeated detection at every scale from 0.75 to 3. A bar on its top edge did up to scale 1.5, but not at scale 2 and above. At scale 1 and above every such loss was a fail-closed orphan rejection; at 0.75, 1 to 5 of 10 per case were silent misses. |
| `SPECK_EXTENT_PX` | 5 | Any bright component inside a 5 × 5 box has a `contourArea` of at most 16, below the 25 px quad filter. So a speck can never be a candidate quad; it matters only where it lands on a copy. |
| `MAX_SPECKS_PER_MEGAPIXEL` | 1,700 | Suppose specks fall at random with density λ per pixel. The chance that none lands in a copy's harm area A is e^(−λA), which is at least 1/2 while λ ≤ ln 2 / A. Five runs composited a 5 × 5 speck on a copy at scale 0.75 (240 px wide, the low end of the assumed range). They gave A = 279.6 ± 44.2 px² (1,500 draws), 280.2 ± 27.9 (12,000 draws on background 40), 342.7 ± 21.1 (8,000 draws at 64 sub-pixel phases), 353.1 ± 31.3 (12,000 draws on background 25, the median grey of the rug and sofa stills) and 378.1 ± 25.6 (6,000 draws). The runs disagree by more than their errors, so A takes the largest plus one standard error, 403.7 px². Then ln 2 / A = 1,717 per megapixel, rounded down to 1,700. At scale 1 a replication gave 315 ± 38 px². In the 64-phase run, 167 of the harmed draws were fail-closed orphan rejections and 88 were silent misses. No run had a silent miss at scale 1 or above. So this limit mostly trades availability, not safety. The specks here are scene content under the overlay; noise drawn over the copy is covered below. The limit counts every speck as 5 × 5 (1 × 1 and 2 × 2 specks gave 29 and 134 px²), which is the cautious side. |
| `MAX_CONTOURS` | 4,096 | A compute bound, not a risk measure. It is twice the old budget, and the power of two just below the geometric mean (4,158) of the largest relayed Frame still (2,960 contours) and the 600 × 1000 noise grid (5,841). The census before tracing never traces. At 1080p on the x86 cloud CPU it took about 20 ms on a random 30 % mask and 65 to 105 ms on the 518,400-speck lattice; its cost grows with the number of bright components. Tracing costs about 10 µs per contour. |
| `MAX_QUADS` | 256 | Unchanged. A copy has 6 quads in the antialiased composites (scales 1 and 1.5), so 4 copies have 24. When a copy is scaled exactly 3 times by pixel replication, every one of its 17 contours is a quad, so 4 copies have 68. |
| `MAX_OUTLINES` | 16 | Each copy gives 2 outline-sized quads: its outer edge and the inner edge of its 4 px border. 4 copies (`MAX_COPIES`) give 8, and the budget doubles that. This was measured at scales 1 and 1.5 (`tests/test_observer_scene.py`), and also holds at scale 3. Each outline costs 8 perspective warps of the 320 × 128 template (2 corner orders × 4 rotations), or 16 when bounded refinement runs, each followed by the pattern checks. |
| `STATISTICS_MAX_CONTOURS` | 16,384 | A reporting bound, 4 × `MAX_CONTOURS`. Above it the statistics skip tracing, so the bands and the quad and outline counts are null, and they report `contour_scan: skipped_over_statistics_limit`. |

The slowest frame measured that passes every limit took 248 ms to the end of the
matcher on the 2.1 GHz x86 cloud CPU: 4,056 contours, 256 quads and 16
outlines, with no specks. One read period is 200 ms. The Frame's speed is
unmeasured. A flat frame takes 2 ms, and 4,096 non-quad contours take 50 to
100 ms.

Statistics are schema 2. They add `bright_pixel_count`, a `census` of
`bright_components`, `specks` and `enclosed_holes` (bright components plus
holes equal `contour_count`), `contour_scan`, `outline_count`, the five flags
and the schema-2 budgets. Each schema has a frozen budget table
(`SCHEMA_BUDGETS`). A report's declared budgets must equal its schema's table,
and every flag is recomputed from them. A stored O2 report (schema 1, with
2,048 / 256 / 25) still validates, and a forged budget does not.

**Classification.** The relayed Frame stills give no dimensions; they are
assumed to be 1920 × 1080 native mirror reads. The limits do not depend on
that size, but the speck densities below do. The relayed data have no speck
counts, so the count of contours below 25 px is used as an upper bound: every
speck has area 16 or less. Margins are limit ÷ value.

| Frame | Outcome | Reason |
| --- | --- | --- |
| bottle-in (15 contours, 0.37 % bright, 0 quads) | pass | specks ≤ 5.3/Mpx (×320); contours ×273; bright ×135 |
| bottle-out (5, 0.32 %, 0) | pass | specks ≤ 1.9/Mpx (×881); contours ×819; bright ×156 |
| rug (736, 15.8 %, 0) | pass | specks ≤ 247/Mpx (×6.9); contours ×5.6; bright ×3.2 |
| sofa (1,028, 4.9 %, 0) | pass | specks ≤ 474/Mpx (×3.6); contours ×4.0; bright ×10 |
| chair (241, 10.7 %, 0) | pass | specks ≤ 73/Mpx (×23); contours ×17; bright ×4.7 |
| invalid bottle-in (2,562, 28.4 %, 1 quad) | pass, reaches the matcher | specks ≤ 1,144/Mpx (×1.49); contours ×1.60; bright ×1.76; 1 quad, so at most 1 outline |
| invalid bottle-out (2,874, 25.2 %, 3) | pass, reaches the matcher | specks ≤ 1,307/Mpx (×1.30); contours ×1.43; bright ×1.98; at most 3 outlines |
| invalid rug (2,960, 28.1 %, 1) | pass, reaches the matcher | specks ≤ 1,345/Mpx (×1.26); contours ×1.38; bright ×1.78 |
| E61 grey still (640 × 360, not a native read) | pass | specks ≤ 894/Mpx (×1.9); 212 contours; 0.57 % bright; 0 quads |
| E83 run 1, read 1 (229 contours) | count limits pass by bound; saturation, quads and outlines unknown | specks ≤ 110/Mpx (×15); contours ×17.9; no bright fraction or quads were recorded |
| E83 run 1, read 2 (2,680) | count limits pass by bound; the rest unknown | specks ≤ 1,292/Mpx (×1.32); contours ×1.53 |
| E83 run 2, read 1 (2,720) | count limits pass by bound; the rest unknown | specks ≤ 1,312/Mpx (×1.30); contours ×1.51 |

The three invalid stills come from an attempt whose prompts never reached the
wearer. What was in view is unknown, so nothing says they are safe scenes.
They pass because every count they have is inside every limit. The matcher's
own checks then apply to them: orphan tags, ambiguity, overlap and the copy
budget. At 960 × 540 the bounds would no longer show a pass of the speck
limit for them, for the sofa (bounded at 1,896/Mpx) or for the two E83 reads
above 2,600 contours; E83 run 1 read 1 would still be bounded at 442/Mpx.

On the public seed stills (outdoor JPEGs, not mirror reads;
`tests/test_observer_scene_seed.py`):

- dog (2,523 specks/Mpx), person (10,805) and giraffe (12,496) reject on speck
  density;
- kite is 75 % bright and rejects on saturation, the first check (its 2,060
  specks/Mpx are over the limit too);
- horses (389/Mpx) and eagle (445/Mpx) pass.

Every synthetic noise frame still rejects:

- the 600 × 1000 grid of 5,841 specks, at 9,735 per megapixel;
- `dots(1021, 2, 4)` and every denser variant. `dots(1020, 2, 4)` is exactly
  at the limit and passes.

Each limit is tested at its boundary on both sides.

<a id="stage-two-decode-at-the-mirrors-scale-a5-2026-11-07"></a>

## Stage-two decode at the mirror's scale (A5, 2026-11-07)

**Recorded device result (relayed by the planner from the Frame report of the
A4 attempt on 2026-11-07, checkout `6f44260`; E89 is being recorded).** The
marker tile appeared in the SteamVR mirror for the first time. The scene
precondition passed (1,859 contours, 5 quads). The observer matched the whole
pattern on the first marker frame (`whole_pattern_mae` 7.4) and then rejected
it `orphan_marker_tag_or_incomplete_copy`: `tag_pattern_error` and
`code_bit_error` failed (`maximum_tag_mae` 53.8, `maximum_bit_mae` 42.0), the
two left-hand tags were found as orphan fragments of 16 to 17 px and the
right-hand tags were not found. The tile's quad was about (1255, 833),
(1530, 837), (1531, 948), (1255, 946): 276 × 113 px for the 320 × 128 native
tile, scale 0.86, tags about 16 px, with a slope of about 0.8°. No image was
saved, by design. This is the same shape as V4 ([E75](../evidence/e075.md)).

**Why the tags did not decode when the whole pattern did.** The stage-two
checks compared the quad, warped back to the native 320 × 128 tile, with the
crisp template pixel by pixel: the mean absolute error over each 24 × 24 tag
had to stay under 45 and over each bit's 4 × 4 core under 30. Those regions are
thin features, a 2 px outline and 6 px bits, and any resampling of the tile
(a scale other than 1, a slope, a sub-pixel phase) smears them in the warped
image while the template stays sharp. The whole-pattern error averages over a
tile that is mostly flat, so it stays small. Reproduced synthetically from the
recorded geometry (`fixed-edge-hud-v1` rendered into a 1920 × 1080 frame at
the recorded quad, two-times supersampled and area-filtered, grey background):
at scale 0.86 with the 0.8° slope and no blur the tag error was already 59
(whole 6.2), with three 16 px fragments; at scale 1.0 with the same slope it
was 68. A texture blur of 0.5 px plus a screen blur of 0.4 to 0.6 px gave
whole 7.3, tag 64 and bit 33 to 43, close to the recorded 7.4, 53.8 and 42.
So the failure was the metric's sensitivity to resampling at any scale, not
the mirror's scale as such; the mirror's filtering added to it.

**The change (decode only; the marker and the HUD footprint are unchanged).**
`locate_copies` in `sightcircuit/hud/observation.py` now decodes this way:

1. *Whole pattern.* The warped quad is compared with the template passed
   through the same quad transform (drawn onto the quad at the frame's
   resolution over the observed surround, then warped back), so resampling
   cancels; the limit stays 13 (`WHOLE_PATTERN_MAX_MAE`).
2. *Border.* On each side, the brightest of the border's four rows must
   average at least 200 (`OUTLINE_MIN_MEAN`), the row a raster offset, a
   slope or a moderate blur leaves cleanest. A translucent marker, every row
   dim, reads 172 and fails.
3. *Code bits by contrast.* Each tag's bits are decoded inside the tag itself:
   the bright level is the larger of the fill band around the bits and the
   brightest bit core, the dark level the smaller of the outline ring and the
   darkest core (every corner id leaves at least one bit set and two clear).
   A bit is set when its core mean is above the midpoint, its margin is the
   distance from the midpoint over half the contrast. The tag's contrast must
   be at least 60 (`TAG_MIN_CONTRAST`, else `tag_pattern_error`), every one
   of the 16 bits must equal its corner id and every margin must be at least
   0.4 (`BIT_MIN_MARGIN`, else `code_bit_error`).
4. *Minimum scale.* A quad whose shorter relative edge is under 0.45 of the
   native tile (`MIN_MARKER_SCALE`: a tile under 144 px wide, bit cores under
   1.8 px) is refused as `marker_too_small`. The documented resolvable range
   began at 0.5 and 0.4 was never resolvable; the floor sits between.
5. *Fragments.* A bright square whose bits decode by contrast, in some
   rotation, to a corner id with margin at least 0.25 (`FRAGMENT_MIN_MARGIN`)
   is a tag fragment, as before from its own contour. New: when an outline
   candidate fails, its tags that still decode are fragments at their template
   positions (`positional_fragment_count`), so a copy that lost a tag or failed
   a check is rejected as an orphan even where the tags' contours no longer
   form quads. Fragments inside an accepted copy are not orphans, as before.

The orientation search, the corner refinement bound, the copy budget and the
overlap and ambiguity rejections are unchanged. The old `maximum_tag_mae` and
`maximum_bit_mae` stay in the diagnostics (now against the matched reference,
information only), beside `minimum_tag_contrast`, `minimum_bit_margin`,
`decoded_corner_ids` and `marker_scale`. `failed_checks` names
`whole_pattern_error`, `outline_brightness`, `marker_too_small`,
`tag_pattern_error` and `code_bit_error`. The A4 shape (one tag's four bits
inverted) now reports `decoded_corner_ids` `[14, 2, 3, 4]` with
`code_bit_error` and three positional fragments.

**Cloud results (synthetic, `fixed-edge-hud-v1` at the recorded quad position,
grey background 40, two-times supersampled; 12 renders per cell: four
sub-pixel phases by slopes 0°, 0.8° and 3°; blur is a Gaussian σ in mirror
pixels after rendering).** Accepted valid copies per 12:

| Scale | σ 0 | σ 0.5 | σ 0.8 | σ 1.0 | σ 1.3 |
| --- | --- | --- | --- | --- | --- |
| 0.5 | 0 (`marker_too_small` at 0.49) | 0 | 0 | 0 | 0 |
| 0.6 | 12 | 12 | 1 | 0 | 0 |
| 0.7 | 11 | 11 | 10 | 2 | 0 |
| 0.75 | 12 | 12 | 12 | 2 | 0 |
| 0.8 | 12 | 12 | 12 | 10 | 1 |
| 0.86 | 12 | 12 | 12 | 11 | 1 |
| 0.9 | 12 | 12 | 12 | 12 | 3 |
| 1.0 | 12 | 12 | 12 | 11 | 9 |
| 1.1 | 12 | 12 | 12 | 12 | 11 |
| 1.2 | 12 | 12 | 12 | 12 | 12 |

Before the change the same renders were accepted 1 to 5 times in 12 at
σ 0 and at most 4 at σ 0.5, at every scale from 0.75 to 1.1. A single
flipped bit (16 variants per scale and blur, slope 0.8°) was rejected 16 of 16
times at every cell of the table, before and after. A partial copy (its
top-left tag and border corner erased) is rejected as an orphan, per 12:

| Scale | σ 0 | σ 0.5 | σ 0.8 | σ 1.0 |
| --- | --- | --- | --- | --- |
| 0.7 | 11 | 4 | 0 | 0 |
| 0.75 | 12 (before: 12) | 7 (6) | 0 (0) | 0 (0) |
| 0.86 | 12 (12) | 12 (12) | 0 (0) | 0 (0) |
| 1.0 | 12 (12) | 12 (12) | 8 (5) | 0 (0) |
| 1.1 | 12 (12) | 12 (12) | 12 (12) | 0 (0) |
| 1.2 | 12 | 12 | 12 | 10 |

So the decode passes synthetically from scale 0.6 to 1.2 at σ ≤ 0.5 and from
0.75 to 1.2 at σ 0.8; an orphan fragment is detected from 0.7 to 1.2 at σ ≤ 0.5
(12 of 12 at 0.86 and above), and from 1.0 up at σ 0.8. Where the partial copy
is not detected at σ ≥ 0.8 the run ends `no_complete_marker`, as before: the
orphan path is not weakened at any cell, and nothing is accepted there.
The limit at σ ≥ 0.8 is the v1 tag geometry, not the decode: the bright ring
between a tag's outline and its bits is 2 px, so blur merges the inner square
with its bits and its contour stops being a quad; the positional fragments
need an outline candidate, which the erased corner removes. The mirror's real
blur is unmeasured; the recorded metrics fit about σ 0.5.

**Marker revision, measured and not made.** A `fixed-edge-marker-v2` with
32 px tags, a 4 px ring (bit offset 6, pitch 10, 8 px bits) and the same
8 px inset, under the same decode: valid acceptance equal to v1 (12, 11, 8 of
12 at scales 0.86, 1.0 and 0.75 for σ 0.8), orphan detection of the partial
copy 12 of 12 at every scale from 0.75 to 1.0 up to σ 1.3 (11 at 0.75). It
would keep the tile's footprint, placement, scale and opacity, so the
[qualification rule](../hud-qualification.md#proposed-g4-method) holds, but
it changes the generated header and the bridge, and it has never been shown
on the device. It is the prepared follow-up if the next attempt's
`minimum_tag_contrast` or `minimum_bit_margin` reads low, and it is not part
of this change.

**Still unknown.** Why the right-hand tags were not found as fragments on the
device (the positional fragments now cover a copy whose outline is found);
the mirror's filtering; whether the compositor's placement differs between
the eyes. The next attempt is the last in the D2 budget and needs the owner's
approval after this change merges; no device step is part of it.

Cloud checks: `tests/test_hud_marker_observer.py` (the scaled, perspective,
rotated, flipped-bit, partial, orphan, photometric and V4-shaped cases, with
their expectations updated to the new check names), `tests/test_hud_contract.py`
(the native marker decodes with zero error at scale 1).

**What O3 does not close.**

- *Where copies can still be missed silently.* Bright structure touching a
  copy is the mechanism behind most losses on the seed composites. At scale 1
  and above it ends in fail-closed orphan rejections. Below about scale 0.8 (a
  copy under 256 px wide) it can instead remove a copy without an orphan. In a
  multi-copy run, that is a silent short count. Scene counts cannot see this;
  only a matcher change can. Two follow-ups are recommended, outside O3:
  - accept a copy only if at least 3 of its 4 tags are also detected as
    fragments;
  - default `expected_copies` whenever more than one copy is expected.
- *The marker's size in the mirror.* The derivation assumes scale 0.75 to 3
  (240 to 960 px wide) and takes each constant at the low end, where it is
  strictest. The Frame-derived geometry is V4's candidate quad, 277 × 110 px
  (scale about 0.86), and the A4 attempt's quad of 2026-11-07, about 276 × 113
  px at (1255, 833) to (1531, 948) in the 1920 × 1080 mirror, also scale 0.86
  with tags about 16 px; both failed the old tag checks. Before A5, at about
  scale 0.7 (224 px wide) and below the matcher missed copies even on a clean
  frame (clean composites: 22 of 24 found at 0.7, 13 of 24 at 0.65, 1 or 2 of
  12 at 0.6). The A5 decode accepts clean copies from scale 0.6 up and refuses
  anything under 0.45 ([scale table](#stage-two-decode-at-the-mirrors-scale-a5-2026-11-07)).
  A side-by-side stereo mirror would put copies near the low end.
- *The data.* The composites are synthetic: opaque paste, a bilinear warp, no
  mirror resampling, tone mapping or compression. Specks drawn over the copy
  (noise added after compositing, which a rendered mirror should not have)
  give a harm area about 2.5 times larger, 860 ± 126 px² at scale 0.75, which
  by the same rule would put the speck limit near 700 per megapixel. Most of
  that harm was fail-closed (35 orphan rejections against 10 silent misses in
  1,500 draws). Every Frame and E83 entry above comes from relayed aggregates.
  Frame stills analyzed with `--analyze-still` would replace those bounds with
  exact schema-2 counts.
