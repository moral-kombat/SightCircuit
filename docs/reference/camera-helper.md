# Camera helper reference

`sightcircuit-camera-helper` is the containment for the Arcturus Vision Camera
Preview SDK: the one SightCircuit binary that links the SDK's Client Library,
built as a native helper in the style of the [overlay bridge](overlay.md), and
exercised in CI through a fake backend that needs no SDK. It is work items S1
(containment and build) and S2 (the metadata-only modes) of the SDK
camera-input lane, lane S of the
[work order](../live-capture.md#architecture-plan-work-order-2026-11-07),
written by the owner's session and re-authored onto `main` as one pull
request, as the work order says. This page describes the code and the checks
that pin it; the [operator guide](../guides/camera-probe.md) owns the device
procedure for the two probe phases (S4 and S5), each under its own approval.
The build command is in [development](../development.md#camera-helper-build-lane-s-2026-11-09).

What the helper is and is not:

- **Cloud-checked, device-reported separately.** Everything on this page is a
  cloud check on x86_64 or a CI result; the one device fact about the helper
  so far is a user-reported note in the work order (the fake build and its
  tests passed on the Frame on 2026-11-08, no SDK, no camera action), and no
  probe has run.
- **Metadata only.** It reads no pixel and maps no buffer (no `cameraFd`, no
  import), writes no camera control, never requests exclusive control (the
  reader's `recording` flag is always false), never touches the red LED,
  saves no image and copies no calibration or auxiliary JSON (only their
  sizes and, for the calibration, a SHA-256). It writes no file.
- **The gates stay closed.** `sightcircuit/gates.py` is unchanged and nothing
  feeds the live pipeline or the HUD. The owner classified the no-pixel probe
  modes as uses outside the live-input gate in the E48 pattern
  ([scope](../poc-scope.md#architecture-decisions-2026-11-07)).
- **The agreement applies.** Section 3 of the
  [SDK agreement](../../third_party/arcturus-camera-sdk/AV1CameraSDK-LICENSE.md)
  binds every run; the Client Library is never redistributed, committed,
  cached, fetched or built by this repository or its CI.

## Source layout

| File | Role |
| --- | --- |
| `tools/camera_backend.hpp` | SightCircuit's own view of the backend: the closed `Outcome` vocabulary, the `HelloFacts`, `ControlSummary` (with its canonical digest input), `CameraMeta` and `FrameSetMeta` records, the bounds every server-supplied count is checked against, and the `CameraBackend` interface. No SDK type. C++11. |
| `tools/camera_backend_sdk.cpp` | The SDK translation unit: the only `.cpp` that includes `AV1CameraClient.h`. Translates the SDK's structures into the records above, acquires each frame set into a local object, checks every count, copies metadata and releases before returning. Linked only by an operator's `--sdk` build. C++11. |
| `tools/camera_status_map.hpp` | The one place an `AV1CameraClient::Status` becomes an `Outcome`; header-only, reads only the inline members of `Status`, so CI compiles and runs it against the vendored headers without the library. Includes the SDK header; compiled into the SDK unit and its test fixture only. |
| `tools/camera_backend_fake.cpp` | The fake backend, built with `-DSIGHTCIRCUIT_CAMERA_BACKEND_FAKE=1`: a scenario table that drives every outcome (success in three pixel formats, permission denied at connect, at the first control read and at the reader, protocol too new and too old, client already connected, resource denied, provider unreachable, out of memory at connect, invalid metadata at connect and an oversized calibration, server timeout then frames, a server timeout on the reader call, not ready, unavailable, an internal error, an invalid argument, transport lost mid-run, a failed release, a standby gap, a format change, a control change mid-run, a sequence gap, bad camera and plane counts, invalid timestamps, a hang in acquire and in release). Records each call in order for the final record's `fake_calls`. Every record it produces says `backend: "fake"`. |
| `tools/camera_helper.cpp` | The entry point and the session: arguments and their bounds, the typed confirmation, the runtime-directory lock, signals, the watchdog, the records. C++17; includes `overlay_lifecycle.hpp` unchanged for stdout isolation and the SIGALRM watchdog. |
| `tools/camera_sha256.hpp` | SHA-256 for the calibration and control-state digests, original code, header-only, so the SDK-linked binary links only the Client Library archive and pthread. |

None of these is pinned by the checkpoint manifest; no gate, pinned file or
`sightcircuit/` module changes with the helper.

## Modes, records and exit codes

| Mode | What runs |
| --- | --- |
| `--describe` | Build and release facts (`build_commit`, `build_dirty`, `sdk_release` with the source revision and agreement version of an `--sdk` build, or null for the fake, the compiler). No connect, no lock, no confirmation, no runtime directory needed. |
| `--hello [--timeout-ms N]` | Connect and the mandatory handshake, one read of the control state, disconnect. The reader stays off. |
| `--probe-frames N --max-seconds S [--interval-ms M] [--timeout-ms T]` | As `--hello`, then the reader is enabled with `recording` false and up to N frame sets (1 to 50) are acquired within S seconds (1 to 10), one pass per M ms (100 to 1000, default 200), each released before the next pass; the control state is read again at the end to show whether anything changed during the run; the reader is disabled and the session closed. |

`--hello` and `--probe-frames` first take a lock, `sightcircuit-camera-helper.lock`
in `$XDG_RUNTIME_DIR` (never `/tmp`; without the variable the run is refused
with `runtime_dir_missing`, and a value that is not an absolute path to an
existing directory owned by the user with mode 0700 is refused with
`runtime_dir_invalid`), then print a notice on the terminal and read a typed `yes` from standard
input, which must be a terminal; nothing skips the confirmation. Stop signals
(`SIGINT`, `SIGTERM`, `SIGHUP`) are blocked around SDK calls and collected
between them, so a cancellation always ends with the reader disabled, the end
control state read and the session closed (`outcome: cancelled`, exit 130). A
SIGALRM watchdog, armed per SDK call for twice the connect timeout plus the
frame timeout plus one second (an acquire at its full socket timeout followed
by its release at the command timeout), ends a hung call with a final record
built in advance (`reason: watchdog_expired`, exit 124, `cleanup_ok: false`).
SIGPIPE is ignored, so a parent that closed the protocol pipe cannot end the
helper before its final record: the write fails, and the exit code says so.

Protocol writes are bounded too, so a consumer that stopped reading cannot
hold the helper past the run bound with the reader active. Each record is
written in chunks of at most 4 KiB, each chunk only after `poll()` reports
room, and a record waits at most one second for the pipe to drain, in probe
mode never past `--max-seconds`. A write that does not complete marks the
protocol stalled: the run ends (`reason: protocol_stalled`, `outcome:
helper_error`, exit 1), the reader is deactivated, the end control state is
read, the session is closed, nothing else is written, and the final record
alone waits up to five seconds for a reader that resumes (a line the stall
tore is terminated first, so the final record parses on its own). A consumer
that never resumes gets no final record and the helper exits 1 within that
budget.

Every record is one JSON line on the real standard output (fd 1 points at
standard error before anything else runs); strings carry well-formed UTF-8
through unchanged, escape control characters, and replace a byte that is not
part of a well-formed sequence with U+FFFD, so every line parses. Each line
has `report_kind`
`sightcircuit_camera_helper`, `schema_version` 1, `backend` (`fake` or `sdk`),
`contract` `camera-helper/1` and `event`:

| Event | Content |
| --- | --- |
| `describe` | The facts above and the false claims. |
| `hello` | `protocol_version`, `camera_count`, `offline_cal_source`, `offline_cal_size`, `offline_cal_size_within_bound`, `offline_cal_sha256` (the calibration JSON itself is never reported). |
| `controls` | `phase` (`hello`, `start` or `end`) and the control summary: the supported and current control bitmasks and capability masks as hex, the named mode values (white balance, transfer function, primaries, YCbCr encoding, resolution mode), LED, bit depth, HDR, tone mapping, brightness, auto exposure, the exposure and gain ranges, `current_fps` and its rounded value, `digest` and `digest_input`. The latest exposure and gain sensor values are not configuration and are not reported. |
| `frame_set` | `pass`, `sequence`, `camera_count`, the pose and velocity validity flags, `cameras` (per camera: `frame_id`, `exposure_timestamp_ns`, `timestamp_seconds`, `width`, `height`, `drm_format`, `pixel_format`, `drm_modifier`, `plane_count`, `planes` with offset, stride and size, the texture-bounds, intrinsics and KB4 validity flags, `camera_model`, `first_camera_from_camera_valid`, `aux_json_size`, `transfer`, `primaries`, `ycbcr_encoding`), and the host clocks (`monotonic_ns`, `boottime_ns`, `realtime_ns`) sampled before and after the acquire. No exposure seconds, gain, sensor temperature, intrinsics values, transforms, pose matrices or auxiliary JSON: those are Camera Data this phase does not report. |
| `outcome` | A call that did not succeed: `call`, `outcome`, `retryable`, `pass` when inside the loop, the SDK's `status_error`, `result`, `system_error` and `sdk_message`, `monotonic_ns`, and an operator `message` for permission denied and camera busy. |
| `final` | `mode`, `outcome`, `reason`, `exit_code`, `cleanup_ok`, `stop_signal`, `outcome_counts` over the whole vocabulary, `passes`, `frame_sets`, `bounds`, `sequence_gaps`, `format_changed` and `first_format_change`, `last_frame_set_monotonic_ns`, `first_retryable_after_last_frame_set_monotonic_ns`, `clock_offsets` (min and max of each host clock minus the exposure timestamp and minus `timestamp_seconds`, and `invalid_timestamps`, the number of cameras whose `timestamp_seconds` was not finite or out of range or whose difference from a host clock would overflow; those take no part in the ranges), `controls_changed_during_run` and `controls_changed_fields`, `controls_digest_start` and `controls_digest_end`, the false claims, `reader_activated`, `reader_deactivated` (`true`, `session_closed`, `deactivation_failed` or `unknown` from the watchdog) and, in the fake build, `fake_calls`. |

The false claims on every `describe` and `final` record are `pixels_read`,
`images_saved`, `controls_written`, `exclusive_control_requested` and
`led_written`, all `false`, with `camera_data_fields: []` and
`persistence: "stdout_only"`.

The `Outcome` vocabulary is closed: `ok`, `no_frame`, `not_ready` and
`unavailable` (retryable, exit 0); `permission_denied` (exit 2);
`camera_busy` (exit 3, for client-already-connected and resource-denied; the
helper never takes over); `protocol_mismatch` (exit 4); `transport_lost`,
`provider_unreachable`, `invalid_metadata` and `helper_error` (exit 1);
`cancelled` (exit 130). A failed release is `transport_lost` whatever its
status, and nothing touches the session again. Refusals before any SDK call
(a missing or invalid runtime directory, a second instance, a declined or
impossible confirmation, an unavailable watchdog) exit 5 with the reason in
the final record (`runtime_dir_missing`, `runtime_dir_invalid`,
`helper_already_running`, `lock_open_failed`, `lock_failed`,
`confirmation_required`, `confirmation_declined`, `watchdog_unavailable`); invalid arguments exit 64 with `reason: invalid_arguments`. The
mapping from an SDK status to an outcome is `camera_status_map.hpp`, run row
by row against the vendored headers by `tests/fixtures/camera_status_map_fixture.cpp`.

## Build

```bash
bash scripts/build_camera_helper.sh --fake [--out DIR]          # any architecture; CI and tests
bash scripts/build_camera_helper.sh --sdk DIR [--check-sdk]     # operator only, aarch64 only
```

`--fake` compiles `camera_helper.cpp` and `camera_backend_fake.cpp` into
`artifacts/camera-helper-fake/sightcircuit-camera-helper-fake` (or `--out DIR`)
and refuses the result if it references any Client Library symbol. `--sdk DIR`
takes the SDK release directory as Arcturus delivered it and, before any
compile, refuses a directory inside the work tree (symlinks resolved), a
missing `release.json`, a file whose SHA-256 differs from the manifest, and a
header that differs from the vendored copy; it then reads the release's
source revision and agreement version, stops there with `--check-sdk`
(usable on any machine), and otherwise builds on aarch64 only: each unit is
compiled to an object, `camera_helper.o` may reference no Client Library
symbol, `camera_backend_sdk.o` may reference only the ten allow-listed
symbols (`connect`, `disconnect`, `isConnected`, `getControlState`,
`setReaderState`, `acquireFrameSet`, `FrameSet::release`, the two destructors
and `Status::message`; a reference to `setControls` fails the build), and the
link writes `artifacts/camera-helper/sightcircuit-camera-helper` and a
`build-report.json` with the release identity, the input digests, the
compiler, glibc and the checkout's commit. `DIR` is never an include path:
only the SDK unit is compiled with the vendored header directory. Nothing is
fetched and no SDK file is copied into the checkout. CI runs `--fake` as a
named step in the `native` job on both architectures; the tests compile the
SDK unit to an object against the vendored headers and never link it.

## Containment rules and the tests that pin them

`tests/test_camera_helper.py` builds the fake through the script into a
temporary directory and runs every scenario, the argument bounds, the
confirmation and runtime-directory refusals (missing, relative, shared,
foreign, absent or not a directory), the lock, SIGTERM and SIGINT
cancellation, the watchdog, the closed-pipe exit, the stalled consumer (a
two-page unread pipe, drained late and never), the connect-time
invalid-metadata and permission-denied paths, every status class that ends
as `helper_error`, invalid timestamps, UTF-8 in records and the 8 KiB line
limit.
`tests/test_camera_helper_boundaries.py` pins the rest. Both run in the
dependency-free `native` job of `check-jobs.yml`, which the ARM64 workflow
reuses, and may not skip there.

| Rule | Check |
| --- | --- |
| The SDK headers are included by the SDK unit, its status map and the map's test fixture only | `SDK_INCLUDERS` in the boundaries test, over every C and C++ source outside the vendored and ignored trees |
| No helper source has a pixel, control, wire-protocol or other-track path | Banned text (`socket(`, `sockaddr_un`, the provider socket path and lock, `MessageHeader`, `setControls`, `ControlsRequest`, an LED write, `cameraFd(`, `mmap`, DMA-BUF and Vulkan import names, a `--yes` or `--no-confirm` flag), banned includes (capture-track headers, `handcam_proto.h`, Vulkan), and `getenv` of nothing but `XDG_RUNTIME_DIR` |
| The reader is never recording | Every `setReaderState(` call has two arguments and the second is `false`; only the SDK unit calls it |
| The C++11 units never include the C++17 lifecycle header | Text check; only `camera_helper.cpp` includes `overlay_lifecycle.hpp` |
| Every vendored enumerator is mapped or listed raw | The enumerators of ten protocol enums and of `Status::Error` are parsed from the vendored headers and each must appear in the SDK unit or the status map, or in the raw-bitmask list |
| The status mapping holds | The fixture compiled against the vendored headers as C++11 without exceptions or RTTI; every row asserted |
| The SDK unit needs only the allowed client calls | Compiled to an object, never linked; `nm -u` must be within the ten-symbol allow-list and must contain the four it cannot do without; never `setControls` |
| The fake unit has no SDK symbol | Its object's undefined symbols name no `AV1CameraClient` |
| The helper's SHA-256 is SHA-256 | The digest fixture against Python's `hashlib` over nine inputs including block boundaries |
| The build script refuses what it must | A matching fake SDK directory outside the checkout passes `--check-sdk` and builds nothing; directories inside the checkout, directly and through a symlink, are refused; a missing manifest, a mismatched hash and an edited header are refused; a real build is refused off aarch64 (`uname` stubbed) after the verification passed; usage errors exit 64 |
| The fake binary is what CI exercises | `--fake` refuses a binary that references the Client Library; every record says `backend: "fake"` |

## What this page does not authorise

No device step. The two probe phases are S4 and S5 of the work order, each
separately approved and run as the [operator guide](../guides/camera-probe.md)
says, never from a cloud session or an agent task. The on-device agent guard
does not yet treat SDK-linked artifacts as private (the review's finding 5;
the agent files are pinned by the checkpoint manifest), so the SDK-backed
binary is built only by the operator, on the Frame, from the SDK directory
Arcturus delivered.
