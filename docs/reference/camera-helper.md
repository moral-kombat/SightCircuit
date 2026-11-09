# Camera helper reference

`sightcircuit-camera-helper` is the containment for the Arcturus Vision Camera
Preview SDK: the one SightCircuit binary that links the SDK's Client Library,
built as a native helper in the style of the [overlay bridge](overlay.md), and
exercised in CI through a fake backend that needs no SDK. It is work items S1
(containment and build) and S2 (the metadata-only modes) of the SDK
camera-input lane, lane S of the
[work order](../live-capture.md#architecture-plan-work-order-2026-11-07),
written by the owner's session and re-authored onto `main` as one pull
request, as the work order says, and S6 (the `--export-frames` mode with its
two pixel paths, described [below](#the-export-mode-s6)). This page describes
the code and the checks that pin it; the [operator guide](../guides/camera-probe.md)
owns the device procedure for the two probe phases (S4 and S5), each under its
own approval.
The build command is in [development](../development.md#camera-helper-build-lane-s-2026-11-09).

What the helper is and is not:

- **Cloud-checked, device-reported separately.** Everything on this page is a
  cloud check on x86_64 or a CI result; the one device fact about the helper
  so far is a user-reported note in the work order (the fake build and its
  tests passed on the Frame on 2026-11-08, no SDK, no camera action), and no
  probe has run.
- **Metadata only, except in `--export-frames`.** The metadata modes read no
  pixel and map no buffer. The export mode reads frames through the SDK's
  borrowed descriptors (the one `cameraFd` read, in the SDK unit) and hands
  the converted pixels to its parent process over a pipe, and nowhere else.
  Every mode writes no camera control, never requests exclusive control (the
  reader's `recording` flag is always false), never touches the red LED,
  saves no image and copies no calibration or auxiliary JSON (only their
  sizes and, for the calibration, a SHA-256). The helper writes no file.
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
| `tools/camera_sha256.hpp` | SHA-256 for the calibration and control-state digests, original code, header-only, so the SDK-linked binary links only the Client Library archive, pthread and `dl`. |
| `tools/camera_export_protocol.hpp` | The constants of camera export protocol version 1 (magic, version, size bounds, the exact field list of each header kind, the vocabulary, the run limits): the header `scripts/camera_contract_header.py` generates from `sightcircuit/camera/contract.py` (work item S7), taken as is. |
| `tools/camera_ycbcr.hpp` | The full-range YCbCr-to-BGR formula in Q16 fixed point for BT.601, 709 and 2020, shared by the CPU path and (as GLSL) the GPU path's shader. C++11. |
| `tools/camera_pixels.hpp` | The pixel-path interface: `SourceFrame` (one camera of a held frame set, with its borrowed descriptor), `BgrImage`, the row-stride rule and the `PixelPath` class. C++11. |
| `tools/camera_pixels_cpu.cpp` | `cpu_linear`: maps a linear buffer read-only with `DMA_BUF_IOCTL_SYNC` around the read and converts on the CPU. C++11. |
| `tools/camera_pixels_gpu.cpp` | `gpu_convert`: opens the Vulkan loader at run time, imports the buffer by its DRM format modifier and plane table, converts with the compute shader and reads the rows back; a linear buffer-copy fallback where the device lacks the modifier extension. C++17. |
| `tools/camera_convert.comp`, `tools/camera_convert_spirv.hpp` | The compute shader and its embedded SPIR-V, generated by `scripts/build_camera_helper.sh --shader`; the header records the source digest. |

None of these is pinned by the checkpoint manifest; no gate, pinned file or
`sightcircuit/` module changes with the helper.

## Modes, records and exit codes

| Mode | What runs |
| --- | --- |
| `--describe` | Build and release facts (`build_commit`, `build_dirty`, `sdk_release` with the source revision and agreement version of an `--sdk` build, or null for the fake, the compiler). No connect, no lock, no confirmation, no runtime directory needed. |
| `--hello [--timeout-ms N]` | Connect and the mandatory handshake, one read of the control state, disconnect. The reader stays off. |
| `--probe-frames N --max-seconds S [--interval-ms M] [--timeout-ms T]` | As `--hello`, then the reader is enabled with `recording` false and up to N frame sets (1 to 50) are acquired within S seconds (1 to 10), one pass per M ms (100 to 1000, default 200), each released before the next pass; the control state is read again at the end to show whether anything changed during the run; the reader is disabled and the session closed. |
| `--export-frames --max-seconds S --max-frames N [--fps F] [--cameras C] [--pixel-path P] [--timeout-ms T] [--parent-confirmed]` | As `--probe-frames`, but each held frame set's selected cameras are converted to BGR3 and written to the parent as packets ([below](#the-export-mode-s6)); S is 1 to 60, N 1 to 600 frames, F 1 to 10 a second (default 5), C the camera indices `0` (default), `1` or `0,1`, P `auto` (default), `cpu_linear` or `gpu_convert`. `--parent-confirmed` (this mode only) records that the parent process gave the confirmation. |

`--hello`, `--probe-frames` and `--export-frames` first take a lock, `sightcircuit-camera-helper.lock`
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

## The export mode (S6)

`--export-frames` writes packets of **camera export protocol version 1** to
the real standard output and nothing else there; every record and note the
helper would otherwise print becomes a JSON line on standard error. Standard
output must be a pipe or a socket (a file or a terminal is refused, exit 5,
`stdout_not_a_pipe`), so a pixel can leave the process only through the pipe
to its parent; the helper writes no file. A packet is the magic `SCCF`, then
three little-endian `u32` fields (protocol version 1, `header_bytes`,
`payload_bytes`), then `header_bytes` of UTF-8 JSON (one object, at most
16384 bytes) and `payload_bytes` of payload (at most 33554432). The header's
`kind` is `ready`, `frame` or `end`; only `frame` carries a payload. Each
header carries exactly the fields the contract lists for its kind, in that
order, and nothing else (`tools/camera_export_protocol.hpp`, the generated
header of the S7 camera contract, pinned by `tests/test_camera_export.py`);
every other fact of a run is in the stderr `final` record.

| Kind | Header |
| --- | --- |
| `ready` (first, once) | `protocol` 1, `backend`, `pixel_path` (`cpu_linear`, `gpu_convert`, or `undecided` under `auto`, whose first frame set chooses the path and is released unexported; the frames' `derivation` names it), `build` (scalars: the `--describe` facts, the contract digest, `pixel_path_facts` with the GPU path's device, extensions and import strategy, `camera_index_mapping` (`avCam0` is taken as left until the transforms say otherwise), `frame_timeout_ms`, `presence_gap_ns`, `hello_camera_count`, `controls_digest_start`, host clocks), `delivered_format` `{"fourcc":"BGR3"}`, `cameras` (the selected indices), `limits` (`fps`, `max_seconds`, `max_frames`, as requested), `confirmation` (`terminal` or `parent`). It is written before the connect, so the handshake's facts in `build` are null, and every run that passed the confirmation writes it first and an `end` last, whatever happens in between (a pixel path that does not open, a refused connect, a hung first acquire). |
| `frame` | `sequence` (from 1, consecutive per run), `frame_id` (the SDK's), `frame_set_sequence` (from 1 per run, one per exported frame set, the same on both cameras of a set), `camera_index`, `exposure_ns`, `monotonic_ns` and `realtime_ns` (host clocks at acquisition), `width`, `height`, `fourcc` `BGR3`, `stride_bytes` (`ceil(width/4)*12`, rows padded to whole four-pixel groups; `width*3` at 2464), `buffer_bytes` (`stride_bytes*height`, the payload size), `derivation` (`cpu_converted` or `gpu_converted`), `acquisition_format` (`drm_fourcc`, `modifier` as hex, `plane_count`, `planes`, `pixel_format`, `encoding`, `transfer`, `primaries`), `dropped_since_last` (frame sets released without export since the previous exported set; the same value on both packets of one set, camera 0 first). |
| `end` (last, once) | `reason`, `counts` (`exported`, `dropped`, `acquire_failures`, `release_failures`), `elapsed_ns`, `digests` (`controls_start`, `controls_end` and `offline_cal`, each present when it was read), `confirmation`. The exit code, `cleanup_ok`, `detail`, `frame_sets`, `passes`, `pixel_path_notes`, `stream_torn` and `end_packet_written` are in the stderr `final` record. |


The run acquires at camera rate with a 100 ms server timeout, releases every
frame set promptly and exports one when its pacing interval (1/F) has
elapsed, dropping the others. It ends with `max_seconds` or `max_frames`
(exit 0; the bound counts frames, not sets), `stop_signal` (exit 130),
`headset_removed` or `standby` (exit 0), `sdk_error` (the outcome's exit
code, 1 at least), `pixel_path_error` (exit 1, the frame set released first)
or `watchdog` (exit 124, the pre-built end packet with the counts at the time
the hung call began), or `consumer_stalled` (exit 1): the protocol descriptor
is non-blocking in this mode and every write waits at most until the run's
deadline and never longer than two seconds without a byte going through, so a
parent that stops reading cannot hold the camera past the bounds; the reader
is deactivated and the end control state read as in every other end, the end
packet is attempted for one second more, and the stderr `final` record says
whether the stream was torn mid-packet (`stream_torn`) and whether the end
packet went out (`end_packet_written`). The informed start is the typed `yes`
of the other modes, in the one `confirm()` call, or, by the owner's decision
of 2026-11-09, the parent's: `--parent-confirmed`, accepted with
`--export-frames` only (every other mode refuses it as invalid arguments),
skips the terminal prompt, still writes the mode's notice as a stderr JSON
line, and is recorded as `confirmation: "parent"` in the `ready` and `end`
headers (`"terminal"` otherwise). No environment variable and no other flag
stands in for the prompt; the boundaries test keeps the helper's flag list
closed. The public headers offer no presence signal, so the
agreement's stop is inferred from frames: when no frame set has arrived for
two seconds the run ends, `headset_removed` if the service kept answering
with server timeouts after frames had flowed (frames stopped without a
reported reason), `standby` if it answered not ready or unavailable, or never
served a frame. Phase B on the device is what will confirm how removal shows
up. A run that cannot start (a refusal, invalid arguments, a pixel path that
does not open, a failed connect or reader activation) writes no packet and
exits non-zero with its final record on standard error.

The two pixel paths implement `PixelPath` in `tools/camera_pixels.hpp`:

- **`cpu_linear`** refuses any modifier but linear, checks the plane table
  against the descriptor's size, maps the planes read-only with
  `DMA_BUF_IOCTL_SYNC` around the read (a descriptor that is not a DMA-BUF,
  such as the fake's memfd, answers `ENOTTY`; that is recorded in
  `pixel_path_notes` and the read goes on), and converts NV12 or P010 at full
  range with the frame's own encoding: Q16 coefficients from Kr and Kb,
  rounded to nearest, chroma taken at (x/2, y/2), 10-bit results scaled to
  8-bit as `(v*255+511)/1023`. No OpenCV.
- **`gpu_convert`** opens `libvulkan.so.1` with `dlopen` (the build never
  links Vulkan), picks the first device with `VK_KHR_external_memory_fd` and
  `VK_EXT_external_memory_dma_buf` (one with `VK_EXT_image_drm_format_modifier`
  wins), and per frame imports the DMA-BUF as a multi-planar image
  (`G8_B8R8_2PLANE_420_UNORM` or `G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16`)
  with the frame's modifier and plane table after checking them against the
  driver's modifier list and image-format properties. A compute shader
  (`tools/camera_convert.comp`) reads the planes through per-plane
  unsigned-integer views and applies the same integer formula as the CPU
  path, writing BGR rows into a host-visible buffer that is read back, so
  both paths deliver identical bytes and the tests can compare them (a
  sampler YCbCr conversion would leave chroma reconstruction and rounding to
  the driver). Where the device lacks the modifier extension and the buffer
  is linear, the import falls back to importing the DMA-BUF as a buffer and
  copying the planes, with the frame's offsets and strides, into an optimally
  tiled image; that is the path the runner's software driver (lavapipe)
  exercises. The import is its own stage, so it can be swapped. `auto`
  stays on the CPU path for a linear modifier and opens the GPU path for
  anything else.

Unverified on the device, in the PR's words: the Adreno driver's support for
the UBWC modifier with these formats and plane counts, mutable-format plane
views on a modifier image, the foreign-queue acquire preserving contents,
whether the SDK's descriptors answer the DMA-BUF sync ioctl, timings at
camera size, and how removal and standby manifest through `acquireFrameSet`.

## Build

```bash
bash scripts/build_camera_helper.sh --fake [--out DIR]          # any architecture; CI and tests
bash scripts/build_camera_helper.sh --sdk DIR [--check-sdk]     # operator only, aarch64 only
bash scripts/build_camera_helper.sh --shader                    # regenerate the SPIR-V header (glslang-tools)
```

`--fake` compiles `camera_helper.cpp`, the two pixel-path units and
`camera_backend_fake.cpp` into
`artifacts/camera-helper-fake/sightcircuit-camera-helper-fake` (or `--out DIR`),
links `-ldl` and refuses the result if it references any Client Library
symbol. Both builds need the Vulkan headers (`libvulkan-dev`, or the pinned
checkout `scripts/build_capture_gpu_check.sh` leaves in `.cache/vulkan-headers`);
the loader is opened at run time, never linked. `--sdk DIR`
takes the SDK release directory as Arcturus delivered it and, before any
compile, refuses a directory inside the work tree (symlinks resolved), a
missing `release.json`, a file whose SHA-256 differs from the manifest, and a
header that differs from the vendored copy; it then reads the release's
source revision and agreement version, stops there with `--check-sdk`
(usable on any machine), and otherwise builds on aarch64 only: each unit is
compiled to an object, `camera_helper.o` and the pixel-path objects may
reference no Client Library symbol, `camera_backend_sdk.o` may reference only the ten allow-listed
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
`tests/test_camera_export.py` does the same for the export mode: the protocol
constants and the Q16 coefficients against their headers, every pixel of NV12
and P010 frames in the three encodings against an independent rendering of the
fake's content, the framing byte for byte, pacing and the dropped counts, every
end reason, the parent's confirmation, the unread pipe (`consumer_stalled`),
the refusals, the argument bounds, that no file is written (the
working directory, `TMPDIR` and the runtime directory are checked), and the
GPU path through the same Vulkan import on the fake's memfd buffers with exact
pixels and byte identity with the CPU path, where a software driver (lavapipe)
is installed as CI does. `tests/test_camera_helper_boundaries.py` pins the
rest. All three run in the dependency-free `native` job of `check-jobs.yml`,
which the ARM64 workflow reuses, and may not skip there.

| Rule | Check |
| --- | --- |
| The SDK headers are included by the SDK unit, its status map and the map's test fixture only | `SDK_INCLUDERS` in the boundaries test, over every C and C++ source outside the vendored and ignored trees |
| No helper source has a control, wire-protocol, file-writing or other-track path | Banned text in every helper source (`socket(`, `sockaddr_un`, the provider socket path and lock, `MessageHeader`, `setControls`, `ControlsRequest`, an LED write, a `--yes` or `--no-confirm` flag, `fopen(`, `ofstream`, `O_WRONLY`, `O_CREAT` except the lock, OpenCV), banned includes (capture-track headers, `handcam_proto.h`), and `getenv` of nothing but `XDG_RUNTIME_DIR` |
| Only the pixel paths read a buffer | `mmap`, DMA-BUF, Vulkan import names, `dlopen` and the Vulkan include appear in `camera_pixels_cpu.cpp` and `camera_pixels_gpu.cpp` only (the fake may `mmap` its own memfd to fill it); `cameraFd(` appears once, in the SDK unit, which never maps, reads or duplicates it; the pixel paths name no SDK type |
| The embedded shader is its source | The SPIR-V header's recorded digest equals `camera_convert.comp`'s SHA-256, its word count matches, the module starts with the SPIR-V magic, and `spirv-val` accepts it where present |
| The pixel paths link nothing new | The CPU path compiles as C++11 with no SDK or Vulkan symbol; the GPU path's object has no undefined `vk*` symbol (the loader is `dlopen`ed) and no SDK symbol |
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
