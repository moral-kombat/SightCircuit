# Overlay bridge reference

Use the [replay panel guide](../vr-overlay.md) to build, launch and stop a panel.
This reference describes the native bridge and its transport; cloud validation
is separate from [device evidence](../status.md). The [current plan](../live-capture.md)
owns remaining device work.

## Presentation path

The native runtime owner uses `IVROverlay_028`. Python sends tightly packed
RGBA images resized to at most 512 × 512 over a bounded pipe; replay defaults
to a cap of 10 processed fps. This CPU-copy prototype is not a measured
low-latency graphics path. A named `SetOverlayRaw` error is runtime evidence,
not proof of general Frame compatibility.

## Connect order, output and watchdog (package K, 2026-11-04)

Every runtime mode (`--test-panel`, `--stdin`, `--calibration-marker`) joins an
already running SteamVR only. (The read-only `--headset-view-record` mode
stops after the Background probe; see
[the prediction cross-check](../hud-qualification.md#geometric-prediction-cross-check-2026-11-04).)

1. `VR_Init` as `VRApplication_Background`. A Background client never starts a
   vrserver. If it fails, the bridge exits with one JSON report whose `error` is
   `vrserver_not_running:<VRInitError symbol>` and never attempts the Overlay
   connect.
2. Otherwise the probe is shut down and the bridge connects as
   `VRApplication_Overlay`, up to three attempts 250 ms and 500 ms apart. Each
   retry re-probes first; a server that went away ends the run as
   `vrserver_lost_before_overlay_connect:<symbol>`. A server that exits in the
   short window between a probe and the Overlay `VR_Init` can still be started
   by that call: the window is narrowed, not closed.

The final report's `connect` object records the probes, the probe result, the
Overlay attempts and their errors.

**Background-only opt-in (owner decision, 2026-11-04).** frame-passthrough-pip's
source says a Background client gets the full `IVROverlay`. If that holds on
Frame, step 2 is unnecessary. To test it, a runtime mode may end with
`--connect-as background`: the bridge then keeps the Background client as its
connection and never connects as Overlay (`connect.sequence` is
`background_only_opt_in`, `application_type` `VRApplication_Background`).
`--connect-as overlay` is the default and is refused, like `background`, by
the validation-only and view-record modes. The device check is a person-run
`--test-panel --seconds 10 --connect-as background` with the owner wearing the
headset, then the HUD, `SetOverlayRaw` and overlay-event checks listed below;
it needs the owner's approval and has not run. Until a Frame result exists the
Python clients keep the default. The validation-only modes make no OpenVR
call and carry no `connect` object.

The bridge keeps a duplicate of the real stdout for the JSON protocol and points
fd 1 at stderr before anything can load the SDK, so SDK output cannot corrupt a
report. A SIGALRM watchdog (6 s for the connect, 5 s for any one loop step,
re-armed per step, and 5 s for cleanup) writes one prepared JSON error. The
connect budget is `kBridgeConnectBudgetSeconds` from the generated contract
header, and the Python clients' 8 s ready wait is derived from the same value
(`BRIDGE_READY_WAIT_SECONDS`), so the two move together
([pre-attempt review](hud-observation.md#pre-attempt-review-of-the-stops-before-a-marker-2026-11-07)). The watchdog
(`watchdog_expired:<phase>`, exit code 124, `cleanup_ok: false`) and calls
`_exit` without running cleanup. It sits beside `sightcircuit/runtime/guard.py`
and the Python client's bounded termination; it does not replace them. A torn
protocol line is terminated before the watchdog line, so the last line always
parses. The handler pattern is adapted from frame-testbench (MIT; see
[THIRD_PARTY](../../THIRD_PARTY.md#overlay-bridge-lifecycle)).

The connect order follows the idea in coah80/framecorder
[`src/openvr.rs` L11-L21](https://github.com/coah80/framecorder/blob/130840c871d3fa740fcf494f96ba046b29d76cf4/src/openvr.rs#L11-L21)
and Sippbox/frame-passthrough-pip
[`src/app/main.cpp` L94-L110](https://github.com/Sippbox/frame-passthrough-pip/blob/babae0b9d8f1a0955207bcca3d91c4db6e2474b2/src/app/main.cpp#L94-L110);
no code was copied. Upstream reports that a SteamVR server started outside its
launcher leaves the Frame black or stuck on passthrough. That is a third-party
claim, not SightCircuit evidence.

Cloud checks cover the no-server refusal in every mode (with and without
`--connect-as background`), the validation modes and scripted fakes of the
connect order, isolation and watchdog (`tests/test_overlay_bridge.py`);
[E84](../evidence/e084.md) records the later review and cloud/CI checks.
[E81](../evidence/e081.md) submitted 400 replay-panel frames on Frame without a
freeze, but used the old connect order and does not qualify the changed
lifecycle or HUD-size output. Still unverified on Frame: the HUD,
`SetOverlayRaw` and overlay events under the new connect order, and a
`SetOverlayRaw` soak past 300 calls at HUD size. Those device follow-ups need
their recorded approval (see [G6 risks](../live-capture.md#risks)).

## Transport and cloud verification

The binary protocol uses little-endian `FJO1`, a uint32 opcode, a uint32 payload
length, and a bounded payload. It is private to these two project components.
The opcodes, layouts and frame-side limit below are defined once in
`sightcircuit/hud/contract.py`, and the settings ranges in
`sightcircuit/overlay.py` (`PANEL_LIMITS`); the bridge reads both from the
generated [`tools/hud_contract.hpp`](../development.md#generated-hud-contract-header).
The ten- and three-second input timeouts are the bridge's own.

| Opcode | Payload |
| --- | --- |
| `1`: frame | uint32 width, uint32 height, tightly packed RGBA bytes; each side 1–512 |
| `2`: settings | five float32 values: width, distance, x, y, opacity; then uint32 visibility (0 or 1) |
| `3`: quit | Empty |
| `4`: expiring frame (validation-only) | uint64 absolute `CLOCK_MONOTONIC` nanosecond expiry, uint32 width/height, RGBA bytes |
| `5`: visibility request (fallback item F1, 2026-11-08) | uint32 cycle, uint32 visible (0 or 1); stream modes answer with the timestamped lines `hide_requested`/`show_requested`, `hide_returned`/`show_returned`, `overlay_hidden`/`overlay_shown` and `visibility_event_missing`, each carrying `cycle` and `validation_only`, as [the fallback reference](hud-fallback.md#bridge-events) specifies; the return line names the call made and a request without its runtime event within `VISIBILITY_EVENT_BOUND_NS` (250 ms, a cloud default, unmeasured on the Frame), or superseded or ended first, gets one `visibility_event_missing` line. Encoded by `sightcircuit.overlay.visibility_packet`, sent and answered through the [visibility cycle API](#visibility-cycle-api-fallback-item-f2-2026-11-08) (F2) |

Settings ranges match the UI: width 0.25–1.5 m, distance 0.5–3 m, offsets −1–1 m,
opacity 0.2–1. Input rejects nonfinite values and malformed lengths. The sender
retains one pending image; a blocked native upload does not block the Qt thread
or build an unbounded frame queue. Settings heartbeat every second keeps paused
sessions alive. Input idles out after ten seconds, and partial packet reads time
out after three seconds; synchronous OpenVR calls are separate from these bounds.

`--validate-stdin` runs the same parser without initializing OpenVR. Reports
explicitly mark `validation_only: true`, `runtime_initialized: false` and zero
submitted frames. The offscreen integration test uses that mode to exercise the
Qt controls and real anonymous-pipe transport. It cannot validate rendering.

The synthetic pipeline uses opcode 4 and requires the bridge's
`frame_expiry_supported` handshake. Expiry is derived from source observation
time, capped at one second; already-expired arrivals are dropped. The native
loop checks it during every input wait (20 ms polling, subject to scheduling),
including partial-header/payload waits. Heartbeats change settings only; they
cannot extend the deadline or revive expired content. A fresh frame replaces
the previous expiry. `frame_expired` events and final presentation fields report
software state; `presentation_state_scope` explicitly disclaims headset evidence.
Expiry is separate from the three/ten-second transport timeouts.

Both Python and the bridge reject opcode 4 for real output. Existing replay
opcode 1 and paused-replay behavior remain available under their existing
authorization. Tests of the new expiry use `--validate-stdin` exclusively; a
stalled native process, blocked API call or stuck kernel I/O can prevent timely
expiry, and no real-time bound or headset hide result is claimed.

```bash
bash scripts/build_overlay_bridge.sh
.venv-replay/bin/python -m unittest discover -s tests -v
```

With window dependencies and model fixtures installed, this covers valid and
malformed packets, named missing-runtime failure, signal/EOF cleanup, read
timeouts, channel order, resizing, Qt forwarding and the existing replay tests.

## Visibility cycle API (fallback item F2, 2026-11-08)

The Python side of one [fallback cycle](hud-fallback.md#bridge-events), in
`sightcircuit/overlay.py`; cloud-checked only, no device run. The client keeps
the bridge's lines and sends the requests; the sink turns them into a hide and
a show that block on their outcome. Nothing here changes the pipeline (F4) or
adds the stage (F5).

| Call | Does |
| --- | --- |
| `OverlayClient.request_visibility(cycle, visible, settings=None)` | Queues the opcode `5` request for the writer thread and returns a `VisibilityRequest` (`cycle`, `visible`, `requested_ns` on `time.monotonic_ns()`, `deadline_ns`). With `settings`, the client's settings change in the same step and are sent right after the request |
| `OverlayClient.await_visibility(request, deadline_ns=None)` | Blocks until the request's terminal line, the deadline (`request.deadline_ns` by default) or the bridge's end, and returns a `VisibilityOutcome`; it never raises for a failed request |
| `OverlayClient.visibility_outcome(cycle, visible)` | The same reading without blocking: `None` while the request is pending |
| `OverlayClient.cycle_lines(cycle)` | Every line the bridge wrote for the cycle, in arrival order; the newest 1,024 cycles are kept (`VISIBILITY_HISTORY_CYCLES`) and dropped cycles are counted in `visibility_cycles_dropped` |
| `transition_outcome(cycle, visible, lines, ended=None)` | The pure reading of a cycle's lines (below) |
| `OverlaySink.hide(cycle, deadline_ns=None)` | Starts a cycle on an owned client: sends the hide with the hidden placement and blocks for its outcome. Cycle numbers increase, and the previous cycle's show must have been requested |
| `OverlaySink.show(cycle, closes_ns, deadline_ns=None)` | Ends the cycle: waits until `closes_ns` (the admission window's close on the same clock, at most `MAX_DISPLAY_AGE_NS` ahead) has passed, sends the show with the visible placement and blocks for its outcome, so the bridge's `request_ns` is at or after `closes_ns` |
| `OverlaySink.cycle` | The current `FallbackCycle`: `cycle`, the `hide` and `show` outcomes, `hidden` (between the two requests) and `hidden_ns`, the confirmed hide's `observed_ns` from which the admission window opens, or `None`: no window this cycle |

A `VisibilityOutcome` is `confirmed` only when the runtime's event line
(`overlay_hidden` for a hide, `overlay_shown` for a show) followed the
request's own requested line and a returned line with `ok` true; `observed_ns`
is then the event's time in the bridge's `CLOCK_MONOTONIC`, the clock of
`Frame.observed_ns` on the same host. Otherwise `reason` names the failure:
the returned line's `reason` (`no_image`, or the call's error), the missing
line's name, `visibility_deadline`, `overlay_bridge_ended` or the client's
error, or `visibility_lines_out_of_order`. `terminal` names the line that ended
the request (`None` when none did) and `lines` are the request's own lines.
The bridge writes a hide's lines before the cycle's show lines and exactly one
terminal line per request, so a hide's lines end at the first show line and a
show's begin at `show_requested`; a missing line belongs to the request whose
lines it falls among, and a returned line with `ok` false ends the request here
even though the bridge still awaits, and later reports missing, its event. The
line names come from `VISIBILITY_HIDE_LINES`, `VISIBILITY_SHOW_LINES` and
`VISIBILITY_MISSING_LINE`, derived from `VISIBILITY_LINES` in the contract.

**Deadline.** `VISIBILITY_TERMINAL_DEADLINE_NS` is 320 ms from the moment a
request is queued: the bridge's `VISIBILITY_EVENT_BOUND_NS` (250 ms), plus one
of its 20 ms input polls (`BRIDGE_INPUT_POLL_NS`; the missing line is written
on the first poll at or after the bound, and `tests/test_visibility_cycle.py`
pins the period against the sensing unit's `poll(2)` call), plus a 50 ms
margin (`VISIBILITY_DEADLINE_MARGIN_NS`) for this side's pipe write, the
bridge's decode, the line's write, the reader's `readline` and the waiter's
wake-up. A cloud choice, **unmeasured on the Frame**; the hidden-event
delivery measurement (F3) replaces the bound it is built on. The deadline
passing is a failed request like a missing line: the caller opens no window.
The deadline counts from the moment the request is queued, not sent: a writer
blocked in a pipe write (bounded at 2 s) can let it lapse before the request
reaches the bridge, and the bridge may then perform and confirm that hide
after the sink has recorded `visibility_deadline`. The sink's recorded
outcome (`sink.cycle.hide`, `sink.cycle.show`) is the one that counts for the
cycle; `client.visibility_outcome` reads the queue as it stands and can
diverge from it after a deadline.

**Why the settings move with the request.** The bridge applies every settings
packet as a show or hide call (`configure()` ends in `visibility()`), and the
client heartbeats the settings every second, so a hide sent by opcode `5`
alone would be undone by the next heartbeat. The cycle API therefore changes
the settings' visibility with each request, and in a writer iteration that
carries a cycle request the order is the pending frame, then the cycle
requests, then the settings: a tile submitted before a show is presented
before it, the request's call is the transition the runtime's event answers,
and the heartbeat then repeats the cycle's state. An iteration without a
cycle request keeps the pre-F2 order, the settings and then the frame, so an
interactive hide on the replay window's client still lands before the frame
it shares an iteration with. While a cycle is hidden, `OverlaySink.submit` leaves the panel down
(`panel_visible` is false), so the tile submitted during the window is
presented by the show. A visibility change through the settings packet alone
remains the pre-F1 path: no cycle, no lines.

**Blocking, not callbacks.** The fallback stage (F5) is a sequential state
machine on the pipeline's own thread that needs the confirmed hidden time
before it opens the window; the client's reader thread already fills the
queue, so a bounded wait on a condition is the smallest consumer that is
correct, and `visibility_outcome` serves a caller that interleaves. Cloud
checks: `tests/test_visibility_cycle.py` against the validation bridge (every
hide ends missing, every show returns `no_image`, the heartbeat keeps a
cycle's hide, the show waits for `closes_ns`) and against a scripted fake for
the confirmed paths, the packet order, a bridge that never writes a terminal
line and one that ends mid-request. No runtime has produced an
`overlay_hidden` line; the confirmed path is exercised by the fake only.

## Source layout

The bridge is one binary built from five translation units in `tools/`
(work-order item E1, 2026-11-08). The split moved code only: the modes, their
protocol lines, report fields, exit codes and error names are those of the
single-file bridge that the accepted device runs used ([E89](../evidence/e089.md),
[E90](../evidence/e090.md)), and the offline tests of every validation-only
mode and no-server refusal pass against the rebuilt binary unchanged.

| File | Unit | Holds |
| --- | --- | --- |
| `overlay_bridge.hpp` | shared header | the `Overlay` state (connection, handle, retirement, event flags, settings), the stop flag, the watchdog budgets, `Settings`, and the declarations of the four units |
| `overlay_bridge.cpp` | entry point | `main`: argument parsing, the opt-in checks, and the connect, run, cleanup, report sequence |
| `overlay_bridge_lifecycle.cpp` | lifecycle | the stop signal, arming a watchdog phase, the Background-then-Overlay connect (`Overlay::init`), error names, and `Overlay::close` |
| `overlay_bridge_rendering.cpp` | rendering | settings decoding and bounds, `marker_settings`, the test pattern and the calibration marker, `SetOverlayRaw`, show and hide, and `--render-calibration-marker` |
| `overlay_bridge_reporting.cpp` | reporting | `emit` (the only JSON writer), the watchdog line, the usage text, the initial, marker, connect and final report fields, and the `ready`, `frame_*` and `test_panel_submitted` lines |
| `overlay_bridge_sensing.cpp` | sensing | `monotonic_ns` (the clock every reported timestamp reads), the stdin protocol loop with its bounded reads and expiry poll, runtime event polling, the visibility queries and the read-only headset view record |

`overlay_lifecycle.hpp` (stdout isolation, the SIGALRM handler, the connect
order) and `overlay_frame.hpp` (packet decoding, retirement) stay the OpenVR-free
helpers that the fixture tests compile on their own; every contract value still
comes from the generated `hud_contract.hpp`. The `Overlay` member functions are
defined in the unit that owns them, as the header notes beside each group.

`scripts/build_openvr_tool.sh` compiles exactly these five units into
`artifacts/overlay-bridge/sightcircuit-overlay-bridge`, and the native CI job's
`-Werror` syntax check names the same five. The checkpoint manifest pins the
units and every local header they include (`NATIVE_SOURCES` in
`sightcircuit/runtime/manifest.py`); `tests/test_checkpoint_manifest.py` fails
when the pin list, the build script's list and the CI line disagree, or when a
pinned header is included by no unit. A bridge rebuilt from the split sources
has a different binary digest from one built before it, so a manifest recorded
after the split differs from an earlier one in `bridge` and `native_sources`:
the expected checkout difference the device procedure records, not drift.

The source-text tests moved with the code: the panel-limit check reads the
rendering unit (`tests/test_overlay_bridge.py`), the connect-budget check
reads the shared header and every pinned source (`tests/test_hud_contract.py`),
and no pinned source may name `SetHeadsetView`. The fallback design's
[bridge events](hud-fallback.md#bridge-events) (item F1) land in the reporting
unit, with the opcode decoded in the sensing unit and the show and hide calls
in the rendering unit.
