# HUD session identity

Use this reference when working with source/session identity. The five tokens bind a mapping to one observed context. Unknown tokens stay unknown; a bound mask refuses them.

[Architecture](../architecture.md) · [Qualification method](../hud-qualification.md) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

<a id="session-tokens-for-the-mirror-source-2026-11-03"></a>

## Session tokens for the mirror source

[E76](../evidence/e076.md) recorded that no hardware source declares the HUD
session tokens, so a bound mask could never accept a mirror frame. The cloud
results of this change are [E78](../evidence/e078.md).
`sightcircuit/sources/session.py` now names a read-only source for each token.
`MirrorSource(session=MirrorSession(...))` declares the tokens and stamps every
frame with them. The tests are cloud runs on a fake /proc root (a temporary
directory laid out like /proc) and on hand-written runtime events. Nothing ran
on Frame and no test read the real /proc of any process. The mirror
recognizer passes no session. The marker observer passes one only with its
`--record-session-tokens` opt-in
([observed session tokens](hud-session.md#observed-session-tokens-for-the-candidate-2026-11-03)).
Nothing here opens a gate. In HUD mode `run_recognition` still
refuses every backend that does not declare the synthetic kind
(`hud_live_mirror_not_approved`, through `LIVE_IMAGE_INPUT`), and `HudMask`
still accepts synthetic fixtures only.

No provider invents a value. A token it cannot observe stays undeclared, with
a named reason in the run report's `session_tokens.undeclared`.

### `timebase_id`

**Source:** `HostClock`: `/proc/sys/kernel/random/boot_id`, `/proc/self/ns/time` and that namespace's `CLOCK_MONOTONIC` offset (`/proc/self/timens_offsets`) of the process that stamps `Frame.observed_ns` with `time.monotonic_ns()` (`CLOCK_MONOTONIC`)

**Value:** `clock_monotonic:boot:<boot id>:timens:<inode>:offset:<s>:<ns>`

**Limits:** The process that writes the calibration (the marker observer) must run in the same time namespace with the same offset, or every frame is rejected; that is unverified on Frame. The offset is part of the token because the kernel reuses a freed namespace's inode number, so two namespaces created one after the other can share an inode with clocks far apart. The offsets file describes the namespace of the process's children, so the token stays undeclared after the process unshares its time namespace (`/proc/self/ns/time_for_children` differs: `host_time_namespace_for_children_differs`). `CLOCK_MONOTONIC` stops during suspend, so a suspend changes neither the token nor a calibration's remaining lifetime. The time-namespace files need Linux 5.6 or later

### `session_id`

**Source:** `RuntimeProcess`: the one process whose `/proc/<pid>/comm` is `vrserver`, its start time (`/proc/<pid>/stat` field 22) and the boot id. The kernel keeps the first 15 bytes of a command name, so a longer name is matched by those bytes and confirmed by the executable's full file name

**Value:** `steamvr:boot:<boot id>:pid:<pid>:start:<ticks>`

**Limits:** `vrserver` is the desktop Linux name; the runtime's process name on Frame is unverified. It is also unverified whether that process is visible from the recognizer's pid namespace (for example inside Steam's container runtime). A session change that does not restart `vrserver` is not seen. When `/proc/<pid>/exe` cannot be inspected, the per-frame check rests on the stat line alone (command name, state, start time): an exec of another program that is also named `vrserver` would not be seen, but `runtime_id` is then undeclared, so a bound mask rejects every frame anyway

### `runtime_id`

**Source:** `RuntimeProcess`: SHA-256 of the binary the process runs, opened through `/proc/<pid>/exe`, whose target's file name must be `vrserver`. After an update by rename the link reads `<install path> (deleted)` and the token is still the running build's digest. Never the install path or an installed manifest: the Frame PM reported an installed-manifest version that differed from the running runtime

**Value:** `steamvr:vrserver:sha256:<digest>`

**Limits:** It covers `vrserver` only, not `vrcompositor`, drivers or shared libraries. Reading the link needs ptrace read access to the process: the same user and a dumpable process (not setuid, no file capabilities), or `CAP_SYS_PTRACE`. Yama's `ptrace_scope` restricts attaching, not this read. Under an emulator such as FEX, or an explicit loader, the link names that program, so the token stays undeclared (`runtime_executable_unexpected`). The binary is hashed once, before the source opens, up to 256 MiB

### `tracking_origin`

**Source:** `TrackingObserver`: a `tracking_state` event carrying the HMD's `Prop_CurrentUniverseId_Uint64` and a SHA-256 of the runtime's zero-pose transforms (`GetRawZeroPoseToStandingAbsoluteTrackingPose`, `GetSeatedZeroPoseToStandingAbsoluteTrackingPose`)

**Value:** `openvr:universe:<id>:zero-pose:<32 hex>`

**Limits:** No reporter emits these events yet (see below). Unverified on Frame: whether an overlay client gets a universe id there, and whether the zero-pose transforms stay bit-identical between recenters (if they drift, every comparison fails closed). A scene application's own tracking space cannot be observed from an overlay process and is not part of the token. Universe id 0, which OpenVR returns on a property error, is rejected. The first `tracking_state` must reach the observer before the `MirrorSource` is created; a state first seen later leaves both tracking tokens undeclared for the run

### `recenter_epoch`

**Source:** `TrackingObserver`: the number of `tracking_changed` events (`TRACKING_CHANGES`: universe changed, seated or standing zero pose reset, room setup committed) since its first `tracking_state`

**Value:** `0`, `1`, ...

**Limits:** The count covers this observer only. A recenter between the calibration run and this run shows only through `tracking_origin`. Unverified: whether Frame's recenter emits one of these events to an overlay client. Events lost while the reporter is down end the observer (`tracking_observer_ended`). A change is seen only once its event reaches `observe()`: a frame captured after a physical recenter but admitted before the event arrives carries the old `tracking_origin` and `recenter_epoch` and passes. That delivery delay is unmeasured

- **Declared before the source opens.** `MirrorSession.declare()` runs when
  `MirrorSource` is created, so `CaptureContext` checks the declaration
  against the calibration when it is built. An unobservable token is `None`.
  Its reason names the provider's problem: `session_token_provider_missing`,
  `host_boot_id_unreadable`, `host_boot_id_invalid`,
  `host_time_namespace_unreadable`, `host_time_namespace_invalid`,
  `host_time_namespace_for_children_differs`, `host_time_offsets_unreadable`,
  `host_time_offsets_invalid`, `runtime_process_not_found`,
  `runtime_process_ambiguous`, `runtime_process_unreadable`,
  `runtime_process_scan_limit`, `runtime_process_exited`,
  `runtime_process_changed` (the runtime exited, is a zombie, or changed
  while it was being declared, including a pid reused after the scan),
  `runtime_executable_unreadable`, `runtime_executable_unexpected`,
  `runtime_executable_too_large`, `tracking_state_not_observed`,
  `tracking_event_invalid` or `tracking_observer_ended`. A bound mask then
  rejects the first frame with `hud_<token>_undeclared`, and the failure
  latches.
- **The declaration fixes the token set.** A token undeclared at
  `declare()` stays undeclared for the run, even if its provider observes it
  later: it is never stamped on a frame or declared by the source. This
  matters for the tracking tokens: `run_recognition` creates the source
  before the model loads, and a first `tracking_state` that arrives in
  between does not bind. A caller feeds the observer its first state before
  creating the source. On 2026-11-03 the owner decided that the tracking
  tokens stay bound when `MirrorSource` is declared; late binding was not
  adopted ([owner decisions](../poc-scope.md#owner-decisions-2026-11-03)).
- **Every frame.** Each frame's metadata carries the tokens read for it.
  - `HostClock` re-reads the boot id, the time-namespace links and the
    offset.
  - `RuntimeProcess` re-reads the same pid's stat line (command name, state
    and start time) and, when it could be inspected at declaration, the
    binary's inode. It never rescans or re-hashes. It reports its declared
    tokens as `None` with `runtime_process_exited` (exited, or a zombie not
    yet reaped) or `runtime_process_changed` (pid reused, or another program
    executed).
  - The observer gives its current state. A malformed tracking event or the
    end of its stream loses both tracking tokens for good.
  - A declared token that can no longer be observed is `None`, never left
    out.
- **A change during a run.** Once a frame's tokens differ from the
  declaration, the source declares the latest read. The observer is consulted
  at every admission, so a recenter refuses even the result of a frame read
  before it, without waiting for the next read. `CaptureContext` fails with
  `source_or_session_changed`, and the failure latches. A new context, or the
  old mask checking a later frame's metadata, names the token:
  `hud_recenter_epoch_changed`, `hud_timebase_id_changed` or
  `hud_session_id_changed`. The report's `session_tokens.first_change` gives
  the frame, the token and the reason.
- **One source per token.** A backend identity that declares a session token
  beside a session is refused (`mirror_session_tokens_declared_twice`). A
  `MirrorSession` serves one source (`session_already_declared`).
  `run_recognition` reports either failure before the model loads or anything
  opens. In HUD mode `run_recognition` checks the live-image gate before the
  session is declared, so a refused run scans no /proc for session tokens and
  hashes no runtime binary.
- **Fixture calibrations.** `fixture_calibration(..., session=...)` lets a
  synthetic fixture pin the tokens that the tests' fixture providers declare.
  The default is still `SYNTHETIC_SESSION`. The provenance, the `synthetic:`
  source and the mask's synthetic-only rule are unchanged.

**No native reporter yet.** The Python observer has an event schema, but
`tools/overlay_bridge.cpp` does not emit those tracking events. The bridge's real
`--stdin` mode is the confirmed replay-panel path ([E47](../evidence/e047.md)),
and it is the pinned bridge of the next G3 checkpoint. A reporter would add
OpenVR property, pose and event reads to both, unchecked on a device. The
fake-dispatch fixture covers helpers that need no OpenVR: packet decoding,
deadlines and teardown order. What a reporter must get right is runtime
behaviour: which events an overlay client receives on Frame, and what the
universe id and zero poses are. A fake cannot establish that; it would only
test a four-case mapping. A later reporter should emit `tracking_state` at
start and after every change, and `tracking_changed` for each
`TRACKING_CHANGES` event, in real mode only; validation modes stay
runtime-free. `OverlayClient` would forward those lines to a
`TrackingObserver` and call `end()` when the bridge exits.

Follow-ups. Follow-up 3 is done in the cloud (below); none of the others has
run, and each needs its own review; the device step needs the user's approval. They are separate from the [next device
checkpoint](../guides/hud-checkpoint.md#next-device-checkpoint) and do not replace it.

1. **Provider check on Frame (device step, not run).** Run the providers once,
   read-only, against the real /proc while SteamVR runs, with no capture,
   overlay or inference. Record the tokens, their reasons and timing. Check
   the runtime's process name, its visibility from the recognizer's
   namespace, permission to read `/proc/<pid>/exe` and its target (the same
   user and a dumpable `vrserver`, or `CAP_SYS_PTRACE`), and the time
   namespace and its offset. No command for this exists yet.
2. **Native tracking reporter.** As described above, with its own fixture,
   review and device check. The device check also measures the delay from a
   recenter to its event reaching `observe()`, relative to mirror frames.
3. **Observed candidate context.** Done (2026-11-03, cloud checks only): the
   marker observer records the providers' tokens with
   `--record-session-tokens`, and `hud_validation_report.py --from-observer`
   takes them only from its report
   ([observed session tokens](hud-session.md#observed-session-tokens-for-the-candidate-2026-11-03)).
   What remains: the tracking tokens need a reporter that delivers a first
   `tracking_state` before the session is declared. The marker bridge cannot
   be that reporter, because it starts after the baseline, after the
   declaration. Until one exists, `--from-observer` rejects every real report,
   at `tracking_origin` at the latest (an earlier token is named first if its
   provider also fails on Frame).
4. **Command path.** No command passes a session to the mirror recognizer.
   HUD input from the mirror stays refused by `LIVE_IMAGE_INPUT` until G4–G6
   pass.

From the checkout:

```bash
.venv-replay/bin/python -m unittest tests.test_mirror_session tests.test_capture_context \
  tests.test_hud_calibration tests.test_hud_mask
```

<a id="observed-session-tokens-for-the-candidate-2026-11-03"></a>

## Observed session tokens for the candidate

Follow-up 3 above. The marker observer can now record the session tokens of
its own run, and `--from-observer` takes a candidate's tokens only from them.
These are cloud results only. Every test uses a fake /proc root and
hand-written tracking events, and no test read the real /proc of any process.
`--record-session-tokens` has never run on Frame. Using it there is a separate
device approval. The observer-token addition has no separate committed evidence
record yet; its implementation is distinct from the session-provider evidence in E78.

- **Opt-in, off by default.** `hud_marker_observer.py --record-session-tokens`
  needs the complete calibration opt-in (the three flags and `--device`), like
  `--bridge`. A partial opt-in is a parser error. Only with the flag does the
  worker build `MirrorSession(HostClock(), RuntimeProcess(),
  TrackingObserver())`. Without it the worker builds no provider, reads
  nothing under /proc for session tokens and hashes no runtime binary. Its
  report then records
  `"session_tokens": {"recorded": false, "reason":
  "session_tokens_not_requested"}`, which `--from-observer` rejects. This was
  an owner decision of 2026-11-03
  ([owner decisions](../poc-scope.md#owner-decisions-2026-11-03)).
- **Declared before the observation's clock.** The worker declares the
  session before it computes the observation deadline and before capture
  opens. It then reads the session once for every complete frame read, before
  that frame is converted or checked, so its frame count equals the capture's
  `frames_read`. The declaration scans /proc and hashes the running
  `vrserver` binary (up to 256 MiB). It runs inside the existing supervisor
  bound of `--seconds` plus 12 seconds, which was not extended, and its
  duration is recorded as `session_tokens.declaration_elapsed_ms`. In the
  cloud (x86_64, 4 vCPUs) a 256 MiB declaration on a fake /proc took about
  1.4 s cold and about 0.24 to 0.75 s warm, a figure that varies between cloud
  runs with the host's SHA-256 speed, and a per-frame read about 0.1 ms. On
  Frame both are unmeasured.
- **A declaration budget of 4 s.** The 12 seconds also hold worker start-up,
  the baseline window (at most 2 s), the bridge's process start until its
  display timer runs, the bridge's exit after that timer (the client waits at
  most 2 s more) and cleanup. The bridge starts its timer before it
  initializes the runtime and prints its ready line, so a slow initialization
  does not lengthen the run; a slow process start does. A declaration longer
  than 4 s (`SESSION_DECLARATION_BUDGET_SECONDS`, half of what the baseline
  window and that exit grace leave) rejects the run
  (`observer_session_tokens_declaration_too_slow`) before capture or the
  marker opens. The budget is checked when the declaration returns. A
  declaration that does not return within the supervisor bound ends the run
  `timed_out` with `cleanup_ok` false, also before any capture or marker
  opens; the supervisor forces the worker's termination if the declaration
  still has not returned after its stop request. Without the budget check, a
  slow declaration that did return could let the bound expire while the
  marker was shown. The supervisor then stopped the worker, the marker client
  stopped the bridge before its display timer ended (the forced stop that
  [E75](../evidence/e075.md) records for V4, here reached through a timeout), and
  the run ended `timed_out` with `cleanup_ok` false. Supervised
  cloud runs with the runtime-free validation bridge, `--seconds 5` and a
  fake /proc showed both cases. Before the check, a 12 s declaration ended
  that way after the marker had opened. With it, a 3.5 s declaration still
  completed with exit 2, and 4.5 s and 12 s declarations were rejected with
  exit 1, `cleanup_ok` true and no capture or marker. The budget is a cloud
  choice and is unmeasured on Frame. Without any declaration, the worst cases
  of the baseline window and of a bridge that is slow to start its timer or
  hangs (the client waits up to 8 s for its ready line, then up to 2 s past
  the display deadline it derives from that line) can still reach the bound;
  that predates this change. A longer budget or bound needs its own reviewed
  change.
- **Report.** With the flag, `session_tokens` is `{"recorded": true, ...}`
  plus the fields of `MirrorSession.report()` (`declared`, `undeclared`,
  `frames_read`, `first_change`, `runtime_process`, `scope`) and
  `declaration_elapsed_ms`. No reporter feeds the tracking observer yet
  ([follow-up 2](hud-session.md#session-tokens-for-the-mirror-source-2026-11-03)). On a real
  run both tracking tokens therefore stay undeclared with
  `tracking_state_not_observed`. That is the expected result, and
  `--from-observer` rejects such a report. The owner kept the tracking tokens
  bound at declaration, so a state that arrives later, for example from the
  marker bridge, which starts after the baseline, is never adopted.
- **Worker contract.** `validate_observer_report` now requires
  `session_tokens` in every worker report, so a worker that drops it is
  invalid (`invalid_observer_session_tokens`). The check covers types, the
  five names, bounded reason strings, `recorded` as a bool, a `None` token
  exactly where `undeclared` gives its reason, and a `first_change` that names
  a declared token within the frames read. An unrecorded entry has one of
  four reasons. `session_tokens_not_requested` means no session was asked for.
  `session_tokens_not_reported` means the supervisor kept its base report:
  no worker report arrived (for example the worker crashed or printed
  nothing), the worker's report failed the contract and was discarded with
  any tokens it carried, or the guardian failed. The supervisor report's
  `status` and `reason` say which. A timed-out worker that still printed a
  valid report keeps its own `session_tokens`.
  `session_tokens_declaration_failed` and `session_tokens_report_invalid`
  record a session failure.
- **A failing session.** A session that raises any exception while it is
  declared rejects the run (`observer_session_tokens_declaration_failed`)
  before the capture or marker opens, so nothing needs closing. A declaration
  over the budget is rejected at the same point
  (`observer_session_tokens_declaration_too_slow`); it did complete, so its
  `session_tokens` entry stays recorded with its duration and no frames read.
  One that raises on a read rejects the run
  (`observer_session_tokens_read_failed`), and the capture and marker still
  close normally. A frame whose read ends after the observation deadline is
  still counted by the capture, so the session reads it too and the two frame
  counts agree. A session report that raises any exception or fails the
  contract is replaced by an unrecorded entry
  (`session_tokens_report_invalid`) before the marker closes. A run that would
  otherwise be `observed_unqualified` is then rejected
  (`observer_session_tokens_report_invalid`). A cancelled or already rejected
  run keeps its own status and reason, and only `session_tokens` records the
  failure.
- **Conversion.** `--from-observer` gives a structured rejection, exit 1 and
  no candidate file in these cases:
  - `session_tokens` is missing or not recorded
    (`hud_observer_session_tokens_not_recorded`). That includes a report from
    before this change.
  - A token is undeclared (`hud_observer_session_token_unobserved`). The
    rejection report adds `session_token` (the first missing token) and
    `session_token_reason` (the provider's reason). This holds even when
    `--declared-context` gives the missing token a valid value: there is no
    declared fallback.
  - A token changed during the run (`hud_observer_session_tokens_changed`,
    with the token and reason of `first_change`).
  - The session's frame count differs from the capture's `frames_read`
    (`hud_observer_session_frames_mismatch`).
  - A token fails the record's token rule
    (`hud_observer_session_token_invalid`).
  - `--declared-context` carries a token
    (`hud_observer_context_declares_session_tokens`).

  The record's `context` is the declared fields plus the observed tokens,
  and it must still pass the version 1 schema. The candidate stays at best
  `candidate_valid_unqualified` with exit 2, and the evidence kind rules are
  unchanged. Observed tokens identify the run; they qualify nothing, and no
  gate changes.

The [next device checkpoint](../guides/hud-checkpoint.md#next-device-checkpoint) does not pass the flag,
so the documented G3 command reads nothing under /proc for session tokens, and its
report cannot become a candidate. From the checkout:

```bash
QT_QPA_PLATFORM=offscreen .venv-replay/bin/python -m unittest tests.test_hud_marker_observer \
  tests.test_hud_candidate tests.test_mirror_session
```
