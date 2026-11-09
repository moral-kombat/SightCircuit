# Probe the Arcturus camera SDK without reading a pixel (Phase A and Phase B)

This is the operator procedure for the two device probes of the SDK
camera-input lane (lane S of the
[work order](../live-capture.md#architecture-plan-work-order-2026-11-07), steps
S4 and S5): Phase A, which connects to the camera service and reads its
control state with the frame reader never activated, and Phase B, which
activates the reader for at most 10 seconds and records the metadata of a few
frame sets. The [dated input review](../history/2026-11-09-arcturus-sdk-input-review.md#device-steps-each-separately-approved-and-owner-run)
defines both phases; the [camera reference](../steam-frame-camera.md#arcturus-camera-preview-sdk-2026-11-09)
summarises what the SDK provides and what it changes for SightCircuit. The
tool is `sightcircuit-camera-helper`, the one SightCircuit binary that links
the SDK's Client Library, written by the owner's session and re-authored onto
`main` as the lane's pull request (work items S1 and S2). **No device run has
happened**, and this page is the procedure, not an approval.

Each phase is a separate device step, approved by the owner for that specific
run and run by the owner or by the Frame manager with the owner present; a
historical approval does not carry over. As the E89 and E90 runs did, the owner
approves in the planner's session and confirms in the Frame manager's session,
and the run starts on the owner's Ready with the headset on. Phase B needs its
own approval after Phase A has been recorded. Neither phase runs in the same
sitting as the F3 measurement or the C5/C6 evaluation session
([scope row](../poc-scope.md#architecture-decisions-2026-11-07)).

What the helper does: in `--hello` mode it connects to the camera service over
the SDK client, completes the handshake, reads the shared control state once,
disconnects and prints what it saw. In `--probe-frames N --max-seconds S` mode
it does the same, then enables this client's frame reader, acquires up to N
frame sets within S seconds (S at most 10), copies each frame set's format
metadata, timestamps and validity flags, releases it before the next pass, and
at the end reads the control state again to show whether anything changed
during the run. Every record is a JSON line on standard output; the helper
writes no file. Before either camera mode starts it asks for a typed `yes` on
the terminal and takes a lock so that only one helper can run.

What it does not do, by construction and checked by its tests: it reads no
pixel and maps no buffer (no `cameraFd`, no import), writes no camera control
and never asks for exclusive control (the reader's `recording` flag is always
false), never touches the red recording LED, saves no image and copies no
calibration JSON or auxiliary JSON, only their sizes and, for the calibration,
a SHA-256. It makes no OpenVR call and does not start, stop or restart any
service. The three software gates in `sightcircuit/gates.py` are untouched.
The owner classified the helper's no-pixel probe modes on 2026-11-09 as uses
outside the live-input gate, in the pattern of the E48 mirror diagnostics
([E48](../evidence/e048.md)), with the gates unchanged (decision 3 of the
[review's list](../history/2026-11-09-arcturus-sdk-input-review.md#owner-decisions-in-priority-order));
the probes establish facts about the camera service, nothing about live input.

## What this guide does not authorise

- Any pixel read, the pixel path (the review's device step 3) or bounded
  recognition (step 4). Those need the owner's consent, presence and gate
  decisions first.
- A control change, an LED change or an exclusive-control request. The helper
  cannot make them; the operator does not make them by other means during a
  probe either.
- Running the Arcturus Vision Camera app, the mirror observer, the marker
  bridge, the replay window, the VR panel, the agent runner or any other
  SightCircuit process in the same sitting.
- Running the helper from a cloud session, from the on-device agent runner (no
  task exists for it, and the guard refuses it) or by anyone but the operator at
  a terminal with the owner present.
- Copying the SDK release directory, the Client Library or the linked helper
  binary into the checkout or off the Frame (agreement section 2: the Client
  Library is never redistributed).
- A repeat after a passthrough disturbance. One disturbance in either phase
  stops the SDK track until the owner reopens it (the owner's passthrough
  rule, below); what happens next is the owner's decision, recorded in scope,
  not a rerun.

## The owner's rules for the probes (2026-11-09)

The owner answered the review's open decisions for these phases on
2026-11-09, in the planner's session; the guide states them as rules, and the
scope page records them when the planner updates it:

1. **Passthrough rule.** The owner approves Phase A and Phase B separately.
   Any change the wearer sees in passthrough during a probe or in the
   30 seconds after it counts as a disturbance: grey, a freeze, black, a
   stutter or a colour shift. One disturbance stops the SDK track until the
   owner reopens it.
2. **Gate classification.** The helper's no-pixel probe modes are uses outside
   the live-input gate, in the E48 pattern; the gates are unchanged.
3. **Consent under the agreement.** Counts, labels, boxes, timings and format
   metadata may be saved locally on the Frame and published in evidence pages.
   No pixels, stills, previews or calibration data are saved or published
   anywhere. The red camera LED is left untouched.
4. **Toolchain.** The helper is built with the SteamOS host compiler, as the
   bridge is; this is what the build script does.

## The agreement's terms that bind the operator

Using the SDK makes SightCircuit an Application under the
[developer agreement](../../third_party/arcturus-camera-sdk/AV1CameraSDK-LICENSE.md)
(version 2026-11-08), and section 3 applies to every run, including a test:

- **Camera activity obvious to the user (section 3.1).** The wearer is told
  before each run what the helper will do, in the words of the helper's own
  notice, and gives the typed confirmation themselves or hears the operator
  read it. The red recording LED stays as the service has it: the helper never
  writes it (`led_written` is false in every final record), the owner's rule
  is that it stays untouched, and the operator reports the LED's state before,
  during and after as an observation.
- **Processing stops when the headset is removed (section 3.2).** No image
  processing happens in either phase. During Phase B's headset-removal
  sub-step the helper keeps reading metadata until its time bound, and the
  notice says so; the wearer is aware of the continuation before the run,
  which is what the section requires. Whether frames stop on removal is one of
  the questions the sub-step exists to answer (the presence source, owner
  decision 4).
- **Saving and sending Camera Data (sections 1 and 3.2).** Camera Data includes
  frames and anything derived from them, detections included. The helper's
  records hold none by construction, and its `camera_data_fields` list is
  empty; the report section below says what may leave the device all the same.
  Redirecting the helper's output to a file is a save of its records, not of
  Camera Data; the notice reminds the operator that anything redirected or
  published falls under the owner's consent rule, which allows counts,
  timings and format metadata to be saved on the Frame and published, and
  nothing of pixels, stills, previews or calibration data.
- **No private camera APIs (section 3).** The helper reaches the cameras only
  through the SDK client: its build checks that the one SDK translation unit
  references nothing but ten allow-listed Client Library symbols, and the
  source tests ban socket code and the frozen capture track's headers in the
  camera tools. The capture-provider track and the shared-memory reader stay
  frozen and are not used in a probe.
- **The Client Library is never redistributed (section 2).** The SDK directory
  stays where Arcturus delivered it, outside the work tree; `.gitignore`
  excludes `libAV1CameraClient.a` at any depth; the linked binary lives in
  ignored `artifacts/` and never leaves the Frame.

## Preconditions

On Frame, confirm all of the following before the preparation steps:

- [Independent SSH](../on-device.md#verify-independent-ssh-access) from the PC
  works, so the run can be stopped and inspected without the headset UI.
- `main` is clean at the approved revision (`git status --porcelain` empty,
  `git rev-parse HEAD` noted), and that revision contains the helper sources
  (`tools/camera_helper.cpp`, `tools/camera_backend_sdk.cpp`) and
  `scripts/build_camera_helper.sh`.
- No SightCircuit process is running (observer, bridge, replay window, VR
  panel, mirror capture, agent runner, a leftover journal watcher of ours), and
  no other device step is in progress.
- The Arcturus Vision Camera app is not running. The SDK admits one client at
  a time; with the app connected the helper ends `camera_busy` (exit 3) and
  never takes over.
- SteamVR is in its normal state, as the baseline reads it: `vrserver` running,
  colour passthrough on, the `deckard-steamvr-rel` package version recorded
  (the same reading the checkpoint manifest and the Frame manager's baseline
  take, [E85](../evidence/e085.md)). Nothing in a probe restarts SteamVR.
- The SDK release directory is on the Frame **outside the work tree**, with its
  `release.json`, the two public headers, the agreement and
  `lib/libAV1CameraClient.a`; the build refuses a directory inside the
  checkout, and the directory is never committed. Each developer obtains their
  own copy from Arcturus ([vendored headers](../../third_party/arcturus-camera-sdk/README.md)).
  Its headers must be byte-identical to the vendored copies, so the release
  must be the one the repository pins (source revision `536217eb…`, agreement
  version 2026-11-08).
- **Developer camera access is enabled by the owner** in the headset's Camera
  Switch setting. The owner does this, not the operator, and says when it is
  done. Without it the helper connects and then ends `permission_denied`
  (exit 2) at the control-state read; that result is kept and reported, and
  the Camera Switch change is not made during a run.
- `XDG_RUNTIME_DIR` is set in the operator's shell and names an existing
  directory (`echo "$XDG_RUNTIME_DIR"`). The helper's lock lives there and it
  refuses to run without it (`runtime_dir_missing`, exit 5). If an SSH session
  lacks it but `/run/user/$(id -u)` exists, export it to that path; never to
  `/tmp`.
- Baselines before, as the HUD guides take them
  ([E89](../evidence/e089.md), [E90](../evidence/e090.md)): the Frame
  manager's baseline record (boot id, OS build, kernel, SteamVR package,
  checkout, ICP firmware, Arcturus build, camera-switch hash) and the G2
  log-watch counts (`IPE_FRAME_PROCESS` failures, failed recoveries,
  colour-camera connects and disconnects, **external camera ownership
  requests**, IOVA faults, ICP IRQs, suspend exits). Take the "before" counts
  only after the wearer has put the headset on and the counts have stopped
  moving: both E89 and E90 saw an ownership request one to two seconds after a
  wake from standby with nothing of ours running, and a probe's connect must
  not be confused with that.
- The [G2 recovery procedure](../evidence/e079.md#recovery-procedure-and-rehearsal)
  is ready: switch to the grey tracking view in Camera Switch, wait at least
  10 s, switch back to colour and watch for 30 s; if green persists or returns,
  switch to grey, stop and take the headset off, with no SteamVR restart and no
  reboot.
- Someone is wearing the headset with normal colour passthrough and tracking,
  and knows what to watch for (below). Optionally start the
  [observation page](frame-sessions.md#record-headset-observations-for-a-run)
  for their answers.

## Preparation

Preparation touches no camera. Steps 1 to 4 can be run before the approval of
the phase; nothing in them needs the owner's Camera Switch change.

1. **Verify the SDK directory, anywhere.** Replace `DIR` with the release
   directory's path. The check reads `release.json`, compares every input's
   SHA-256 with the manifest and the two headers with the vendored copies, and
   compiles nothing:

   ```bash
   cd "$HOME/sightcircuit"
   bash scripts/build_camera_helper.sh --sdk DIR --check-sdk
   ```

   Expect `SDK verified: source_revision … agreement_version …; headers match
   the vendored copies.` then `--check-sdk: stopping before any compile`. A
   `refused:` line names what is wrong (a directory inside the work tree, a
   missing file, a hash mismatch, a header that differs from the vendored
   copy); fix the directory, never the checkout.

2. **Build the linked helper on the Frame.** The build runs on aarch64 only,
   compiles the helper and the SDK unit separately, checks with `nm` that only
   the SDK unit references the Client Library and only through the allow-list
   (a reference to `setControls` fails the build), links the library and
   writes into ignored `artifacts/camera-helper/`:

   ```bash
   cd "$HOME/sightcircuit"
   test -z "$(git status --porcelain)"
   bash scripts/build_camera_helper.sh --sdk DIR
   ```

   Expect `Built …/artifacts/camera-helper/sightcircuit-camera-helper` and the
   reminder that the binary and the library are never committed, uploaded or
   shared. `artifacts/camera-helper/build-report.json` records the SDK release,
   the four input hashes (the library's included), the compiler, the C library,
   the build commit, whether the tree was dirty and `client_library_copied`
   false. The script uses the SteamOS host compiler (`CXX`, default `g++`),
   as the bridge is built, which is the owner's toolchain rule; `--image-id`
   exists for a build made inside a container image and is not used here. The
   build's duration on the Frame is unmeasured.

3. **Describe, without connecting.** `--describe` needs no lock, no runtime
   directory and no confirmation, and makes no SDK call:

   ```bash
   cd "$HOME/sightcircuit"
   artifacts/camera-helper/sightcircuit-camera-helper --describe
   ```

   Expect one record with `"event": "describe"`, `"backend": "sdk"`,
   `build_commit` equal to the approved revision, `build_dirty` false, and
   `sdk_release` naming the pinned source revision and agreement version. A
   `"backend": "fake"` here is the test build, not the linked helper: stop.

4. **Offline tests on the Frame's Python.** The helper's tests build the fake
   backend into a temporary directory and drive every outcome, refusal, the
   lock, cancellation and the watchdog; they touch no camera and need no
   `.venv-replay`:

   ```bash
   cd "$HOME/sightcircuit"
   python3 -m unittest tests.test_camera_helper tests.test_camera_helper_boundaries
   ```

   Expect `OK`. (The fake build and these tests passed on the Frame on
   2026-11-08 in a throwaway worktree, a note in the work order and not
   evidence.) A failure is a stop: the device's compiler or Python differs
   from the cloud's, and the linked helper is not run until the coder has
   seen the output.

5. **Baselines before**, as in the preconditions, once the wearer is settled.

6. **The wearer's briefing.** Read them the helper's notice for the mode about
   to run (it appears on the terminal before the `yes` prompt and is quoted
   under each phase), and the observation list: colour passthrough unchanged,
   or grey (the tracking-camera view), frozen, black, stuttering or shifted in
   colour, with the time it started and ended against the run's start;
   tracking normal or not;
   the red LED on or off before, during and after; any green input
   ([E59](../status.md#e59-arcturus-green-input-recurrence-and-operator-recovery)).
   They watch during the run and for 30 seconds after the helper exits.

## Phase A: connect, handshake and control state, reader never activated

Only with the owner's approval for this run, confirmed in the Frame manager's
session, and after step 5 of the preparation. Run it from an interactive
terminal (a `tmux` window over SSH is fine) because the confirmation is read
from the terminal; do not pipe the answer. Standard output goes to the run
directory, standard error and the prompt stay on the terminal:

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  test -z "$(git status --porcelain)"
  test -x artifacts/camera-helper/sightcircuit-camera-helper
  test -d "$XDG_RUNTIME_DIR"
  mkdir -p artifacts/camera-probe
  run="$(mktemp -d "$PWD/artifacts/camera-probe/phase-a-XXXXXX")"
  git rev-parse HEAD > "$run/revision.txt"
  cp artifacts/camera-helper/build-report.json "$run/"
  artifacts/camera-helper/sightcircuit-camera-helper --describe > "$run/describe.json"
  date -u +%Y-%m-%dT%H:%M:%S.%NZ > "$run/started.txt"
  set +e
  artifacts/camera-helper/sightcircuit-camera-helper --hello > "$run/hello.ndjson"
  echo "$?" > "$run/exit-code.txt"
  date -u +%Y-%m-%dT%H:%M:%S.%NZ > "$run/ended.txt"
  echo "Saved $run"
)
```

The helper prints its notice and `Type yes to continue:` on the terminal:

> sightcircuit-camera-helper --hello: connects to the camera service; the
> reader stays off; no frames are read; watch passthrough during the run and
> for 30 seconds afterwards; this helper writes no file.

Type `yes` exactly (anything else, including `y` or `YES`, is
`confirmation_declined`, exit 5, with no SDK call made). The helper then
connects (connect timeout 2000 ms, the helper's default; `--timeout-ms` sets
1 to 10000), prints the `hello` record, reads the control state, prints the
`controls` record with `"phase": "hello"`, disconnects and prints the `final`
record. A clean run lasts well under a second on the cloud fake; its duration
on the Frame is unmeasured. The frame reader is never enabled:
`reader_activated` is false in the final record.

During the run and for 30 seconds after the helper exits, the wearer watches
passthrough and says at once if anything changes. Ctrl-C (or SIGTERM over SSH)
is collected between SDK calls, never during one, and ends the run with the
disconnect done and exit 130.

**Stop rule.** Any change the wearer sees in passthrough, in the run or in the
30 seconds after (grey, a freeze, black, a stutter or a colour shift), is a
disturbance under the owner's passthrough rule, and one disturbance stops the
SDK track until the owner reopens it. Keep the run directory, follow the G2
recovery procedure if passthrough does not return on its own, and report; do
not run Phase B.

## Phase B: the reader active for at most 10 seconds, metadata only

Only with the owner's separate approval, after Phase A has been recorded with
passthrough unchanged, in a later sitting or the same one as the owner's
approval says. Phase B is two runs under that approval: a short one, and the
headset-removal sub-step. Both use the same command shape with `--max-seconds`
at most 10 (the helper refuses more, exit 64):

```bash
(
  set -e
  cd "$HOME/sightcircuit"
  test -z "$(git status --porcelain)"
  test -x artifacts/camera-helper/sightcircuit-camera-helper
  test -d "$XDG_RUNTIME_DIR"
  mkdir -p artifacts/camera-probe
  run="$(mktemp -d "$PWD/artifacts/camera-probe/phase-b-XXXXXX")"
  git rev-parse HEAD > "$run/revision.txt"
  cp artifacts/camera-helper/build-report.json "$run/"
  artifacts/camera-helper/sightcircuit-camera-helper --describe > "$run/describe.json"
  date -u +%Y-%m-%dT%H:%M:%S.%NZ > "$run/started.txt"
  set +e
  artifacts/camera-helper/sightcircuit-camera-helper \
    --probe-frames 10 --max-seconds 10 > "$run/probe.ndjson"
  echo "$?" > "$run/exit-code.txt"
  date -u +%Y-%m-%dT%H:%M:%S.%NZ > "$run/ended.txt"
  echo "Saved $run"
)
```

The notice for this mode names the bounds it was given:

> sightcircuit-camera-helper --probe-frames: activates this client's frame
> reader for both passthrough cameras (avCam0 and avCam1) and reads the
> metadata of up to 10 frame sets within 10 seconds, one pass every 200 ms.
> Metadata only; no images are read; this helper writes no file; anything you
> redirect or publish falls under the owner's consent decision (licence
> section 3.2); if you remove the headset during the run, metadata reading
> continues until the time bound. Watch passthrough during the run and for 30
> seconds afterwards.

What the command passes, and why:

- `--probe-frames 10`: a few frame sets, enough to see the format, the plane
  layout, the sequence step and the timestamp offsets more than once. The
  helper allows 1 to 50; the run ends at the count or at the time bound,
  whichever comes first.
- `--max-seconds 10`: the bound the review set for Phase B; the helper's
  maximum. With frames arriving every pass, ten frame sets take about two
  seconds at the default pacing; without frames the run lasts the full bound.
- `--interval-ms` left at its default of 200 ms (allowed 100 to 1000): one
  acquire per pass, well below camera rate, so the helper never drains the
  service. Each acquire waits at most 50 ms for a frame set (`frame_timeout_ms`,
  a helper constant). Both numbers are cloud defaults, unmeasured on the Frame.
- Each SDK call runs under a watchdog of the connect timeout plus the frame
  timeout, rounded up to whole seconds, plus one second (4 s at the defaults).
  A call that hangs that long ends the process with a pre-built final record
  (`watchdog_expired`, exit 124, `cleanup_ok` false, `reader_deactivated`
  `"unknown"`).

Order of SDK calls: connect, control state (`"phase": "start"`), reader on
(the `recording` flag false), the acquire passes, reader off, control state
(`"phase": "end"`), disconnect. A pass that returns no frame set
(`no_frame`, `not_ready` or `unavailable`) is counted in an `outcome` record
with its pass number and the loop goes on; `permission_denied`, `camera_busy`,
`protocol_mismatch`, `transport_lost` and `invalid_metadata` end the run, and
after a transport loss no further SDK call is made but the disconnect. Ctrl-C
is collected between calls and the cleanup still runs (exit 130).

**The headset-removal sub-step.** Only if the first run ended with passthrough
unchanged and the reader deactivated. Run the same command with
`--probe-frames 50 --max-seconds 10` into its own `phase-b-…` directory so
that the time bound, not the count, ends the run. About three seconds after
the first `frame_set` line appears, on the operator's word, the wearer takes
the headset off and holds it still, in view of the room; the operator notes the
time. The helper keeps acquiring until the bound: whether frames stop shows in
the final record as `last_frame_set_monotonic_ns` and
`first_retryable_after_last_frame_set_monotonic_ns` (the first pass after the
last frame set that returned no frame), in the `outcome` records' pass numbers
and in `outcome_counts`. A standby that resumes with a changed format or a
sequence jump shows in `format_changed`, `first_format_change` and
`sequence_gaps`. The wearer puts the headset back on after the helper exits
and watches passthrough for the 30 seconds as before; the operator records
what passthrough did on re-donning. The helper does not and cannot read a
proximity or standby signal; it only reports what the acquire calls returned.

**Stop rule.** As in Phase A: any change the wearer sees in passthrough in
either run or in the 30 seconds after is a disturbance, and one disturbance
stops the SDK track until the owner reopens it; do not run the second run
after a disturbance in the first.

## Reading the result

| Exit code and final record | Meaning | Next |
| --- | --- | --- |
| `0`, `"outcome": "ok"`, `cleanup_ok` true | Clean run; in Phase B `reader_deactivated` true and the `end` control state read. Retryable outcomes may be counted and `frame_sets` may be below the count asked for | Record the fields below and the wearer's report |
| `2`, `permission_denied` | Developer camera access is not enabled, or was withdrawn; the message says `Enable Developer camera access in Camera Switch, close Arcturus Vision Camera, then rerun` | Keep the report. The Camera Switch change is the owner's; a run after it is its own device step under the owner's approval |
| `3`, `camera_busy` | Another client holds the cameras (`Result_ClientAlreadyConnected`) or the reader was refused (`Result_ResourceDenied`); the helper never takes over | Keep the report; find and close the other client (the Arcturus app, usually). A run after that is its own device step |
| `4`, `protocol_mismatch` | The service's protocol version is not the one the vendored headers declare | Keep the report; the SDK release or the runtime changed, and the lane's code must be reviewed before any rerun |
| `1`, `transport_lost`, `provider_unreachable`, `invalid_metadata` or `helper_error` | The socket failed or closed, the service was not reachable, or a count in the reply exceeded the protocol bounds (nothing beyond the checked counts was read) | Keep the report; `reader_deactivated` may read `session_closed` and `cleanup_ok` false after a transport loss, which means the service closed the session, not that the reader is known off |
| `124`, `watchdog_expired` | An SDK call hung; the pre-built final record is all there is, and the reader state is unknown | Keep the report; the wearer watches the full 30 s; no rerun in the sitting |
| `130`, `cancelled` | Stopped by Ctrl-C or SIGTERM between SDK calls, with cleanup done | Keep the report; note why it was stopped |
| `5`, `confirmation_required`, `confirmation_declined`, `runtime_dir_missing`, `helper_already_running`, `lock_open_failed`, `lock_failed`, `watchdog_unavailable` | Refused before any SDK call | Fix the terminal, the runtime directory or the leftover helper; the camera was not touched, so the run can be repeated under the same approval |
| `64`, `invalid_arguments` | A flag or bound was wrong; no prompt, no lock, no SDK call | Correct the command; same approval |

A clean exit is a process result. It does not show that passthrough stayed
normal, that the camera service recovered, or that the reader is physically
off; the wearer's report and the baselines after are what say so, and they are
recorded apart from the software fields, as every device record does.

## The report

The helper's output is NDJSON: one JSON object per line, each with
`report_kind` `sightcircuit_camera_helper`, `schema_version` 1,
`backend` (`sdk` on the Frame), `contract` `camera-helper/1` and an `event`.
The records and the fields that matter for the lane's questions
([the review's list](../history/2026-11-09-arcturus-sdk-input-review.md#questions-only-the-device-or-the-vendor-can-answer)):

| Record | Fields | Answers |
| --- | --- | --- |
| `describe` | `build_commit`, `build_dirty`, `sdk_release.source_revision`, `sdk_release.agreement_version`, `compiler` | Software identity of the run, beside `revision.txt` and `build-report.json` |
| `hello` | `protocol_version`, `camera_count`, `offline_cal_source`, `offline_cal_size`, `offline_cal_size_within_bound`, `offline_cal_sha256` | The handshake; the calibration JSON itself is never printed, only its size and digest |
| `controls` (`phase` `hello`, `start` or `end`) | The shared control state as configured: the supported and current control bitmasks (hex), sensor bit depth, white balance, HDR and tone mapping flags, transfer function, primaries, YCbCr encoding, resolution mode, brightness EV, auto-exposure flag, exposure and gain limits, `current_fps`, **`red_recording_led_enabled`**, and a `digest` with its `digest_input`. The latest exposure and gain values are not reported | Control state and the LED as the service has them; the digest lets two reads be compared without the fields |
| `frame_set` | `pass`, `sequence`, `camera_count`, the pose and transform validity flags, `host_clocks_before` and `host_clocks_after` (monotonic, boot-time and real-time nanoseconds around the acquire), and per camera: `frame_id`, `exposure_timestamp_ns`, `timestamp_seconds`, `width`, `height`, `drm_format`, **`pixel_format`** (`nv12`, `nv12_ubwc`, `p010`, `p010_ubwc`), **`drm_modifier`** (`0x0000000000000000` is linear; `0x0500000000000001` is the UBWC modifier the frozen capture track pinned), `plane_count` and each plane's `offset`, `stride` and `size`, the texture-bounds, intrinsics and KB4 validity flags, `camera_model`, `aux_json_size`, `transfer`, `primaries`, `ycbcr_encoding` | **Linear or UBWC**, the sizes and plane layout, and whether a resolution mode changes them |
| `outcome` | `call`, `outcome`, `retryable`, `pass`, `status_error`, `result`, `system_error`, `sdk_message`, `monotonic_ns` | Every SDK call that did not return a frame set or succeed, with the SDK's own status names |
| `final` | `mode`, `outcome`, `reason`, `exit_code`, `cleanup_ok`, `stop_signal`, `outcome_counts`, `passes`, `frame_sets`, `bounds`, `sequence_gaps` (`count`, `first`), `format_changed`, `first_format_change`, `last_frame_set_monotonic_ns`, `first_retryable_after_last_frame_set_monotonic_ns`, `clock_offsets` (for each host clock, the minimum and maximum of host-after minus `exposure_timestamp_ns` and minus `timestamp_seconds`), `controls_changed_during_run`, `controls_changed_fields`, `controls_digest_start`, `controls_digest_end`, `reader_activated`, `reader_deactivated`, and the false claims `pixels_read`, `images_saved`, `controls_written`, `exclusive_control_requested`, `led_written`, with `camera_data_fields` `[]` and `persistence` `stdout_only` | **Sequence gaps**; **standby** (frames stopping after removal); **control-state drift** (a field that changed between the start and end reads was changed by another party); **the exposure clock** (the host clock whose offset range is small and stable is the clock domain `exposure_timestamp_ns` uses; a range in the order of 10^17 ns against real time means it is not wall-clock time) |

`timestamp_seconds` and `exposure_timestamp_ns` come from the SDK as given; the
header does not name their clock, and the offsets against the three host
clocks are the measurement that answers it. A sequence gap counts a jump of
more than one between consecutive frame sets the helper saw; at one pass per
200 ms and a camera rate the helper does not know, gaps are expected when the
camera runs faster than the pacing, so the sequence step per pass, not the gap
count alone, is what to read. The fake build's 60 fps is a scenario value, not
a Frame measurement.

### What leaves the device, and what does not

May leave the Frame, to the evidence coordinator:

- the helper's records as printed (`describe.json`, `hello.ndjson`,
  `probe.ndjson`): they hold no pixels, no calibration JSON, no auxiliary JSON,
  no intrinsics values, no transforms or pose matrices, and no per-frame
  Camera Data beyond format metadata, timestamps, sequence and validity flags;
- `build-report.json`, `revision.txt`, `exit-code.txt`, `started.txt`,
  `ended.txt`, and the baselines and log-watch counts before and after as the
  Frame manager reports them;
- the wearer's and operator's fields (below).

Does not leave the Frame: the linked binary, the Client Library and anything
else from the SDK directory (agreement section 2); any path outside the
checkout; the SDK's `sdk_message` strings if one carries a path (redact the
path and say so). Nothing in a probe produces an image, a recording or a
detection, so no Camera Data exists to be sent. The owner's consent rule
(2026-11-09) allows counts, labels, boxes, timings and format metadata to be
saved on the Frame and published in evidence pages, and no pixels, stills,
previews or calibration data anywhere; the helper's records fall inside the
first class, and the coordinator applies the rule when writing the evidence
entry.

### Wearer and operator fields

Recorded as the wearer's words and the operator's clock, never as a software
claim, in `owner-notes.txt` in the run directory and in the hand-off:

| Field | Values |
| --- | --- |
| `passthrough_during_run` | `unchanged`, `grey`, `frozen`, `black`, `stutter`, `colour_shift`, `other: <words>`, with start and end times relative to `started.txt`; anything but `unchanged` is a disturbance under the owner's rule |
| `passthrough_after_30s` | the same vocabulary for the 30 seconds after the helper exited |
| `tracking` | `normal` or `<words>`, with times |
| `red_led_before`, `red_led_during`, `red_led_after` | `on`, `off`, `not_checked`; the `controls` records' `red_recording_led_enabled` is the software reading beside it, not a substitute |
| `green_input` | `none` or the time it appeared |
| `headset_removed_at` (Phase B sub-step only) | the operator's time, and `passthrough_on_redonning` in the same vocabulary |
| `camera_switch_changed_during_run` | `no`, or what the owner changed and when (the G2 procedure, if it was run) |
| `other_clients_running` | `none`, or the name of what was closed before the run |

## Baselines after

After each run and the 30-second watch, take the baseline record and the G2
log-watch counts again and record the differences against the "before" set.
A camera-ownership request, a colour-camera disconnect and reconnect, or an
IPE failure in the run window is a device fact to record, whatever passthrough
looked like; the owner's passthrough rule is written in terms of what the
wearer sees, so a logged line without a visible change is recorded as a log
fact for the owner, not counted as a disturbance by the operator. E89 and E90 recorded such
lines before their runs with nothing of ours running, so the times matter:
compare against `started.txt` and `ended.txt`.

If passthrough or tracking regressed, or the view turned green, follow the
[G2 recovery procedure](../evidence/e079.md#recovery-procedure-and-rehearsal)
within the approved run scope and record each step with its time; recovery
does not make the run a pass, and the SDK track is stopped.

## The evidence note

Hand the run directory's contents listed above, the wearer and operator
fields, and the baseline differences to the evidence coordinator
([evidence maintenance](../evidence/README.md#maintaining-evidence)). The
coordinator allocates the record identifier; this guide reserves none, and
nothing on this page is evidence. A clean Phase A supports the owner's
approval of Phase B. A clean Phase B answers the questions the work order
holds F4 to F8 for (linear or UBWC, standby behaviour and the passthrough
question) and feeds the owner's presence decision and the later phases; it
enables no live input, no pixel path and no gate change.

## Helper identity and what this page follows

This page documents the helper as written in the owner's branch
`sdk-helper/metadata-probe` (commit `107c893`) and re-authored onto `main` as
the lane's pull request. The flag names, the bounds (1 to 50 frame sets, 1 to
10 seconds, 100 to 1000 ms between passes, a 1 to 10000 ms connect timeout),
the 50 ms frame timeout, the watchdog formula, the exit codes, the record
events and field names, the notice texts and the lock's location are the
helper's as of that commit. If the pull request's review changes any of them,
the matching line here changes with it, in the same pull request or the next;
the guide never describes a helper that is not on `main`.
