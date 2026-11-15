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
two pixel paths, described [below](#the-export-mode-s6)), and the read-only
`--query-gpu` mode with the GPU path's [import strategies](#import-strategies-and-the-gpu-query),
added after the first export on the Frame ended at the import. This page describes
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
  pixel and map no buffer; `--query-gpu` reaches no camera at all. The export mode reads frames through the SDK's
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
| `tools/camera_pixels_gpu.cpp` | `gpu_convert`: opens the Vulkan loader at run time, asks the driver which import strategy it accepts for the frame's format and modifier, imports the buffer by its DRM format modifier and plane table, converts with the compute shader and reads the rows back; a linear buffer-copy fallback where the device lacks the modifier extension; and the driver query behind `--query-gpu`. C++17. |
| `tools/camera_convert.comp`, `tools/camera_convert_spirv.hpp` | The compute shader, one source with a variant for each import strategy, and its three embedded SPIR-V modules, generated by `scripts/build_camera_helper.sh --shader`; the header records the source digest and each module's digest. |

None of these is pinned by the checkpoint manifest; no gate, pinned file or
`sightcircuit/` module changes with the helper.

## Modes, records and exit codes

| Mode | What runs |
| --- | --- |
| `--describe` | Build and release facts (`build_commit`, `build_dirty`, `sdk_release` with the source revision and agreement version of an `--sdk` build, or null for the fake, the compiler). No connect, no lock, no confirmation, no runtime directory needed. |
| `--query-gpu` | What the Vulkan driver answers for the `gpu_convert` path: one `gpu_query` record ([below](#import-strategies-and-the-gpu-query)). Like `--describe`: no backend, no connect, no camera, no lock, no confirmation, no runtime directory needed; no buffer is imported, mapped or read. Exit 0 when a device opened, 1 when none did (the record says why). |
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
| `gpu_query` | `opened`, `error` (null when a device opened), `gpu` (null otherwise; its fields are listed [below](#import-strategies-and-the-gpu-query)) and the false claims. |
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

Standard error carries the helper's own records as JSON lines, and the
helper keeps it that way while text from the libraries it loads arrives
there too. The Vulkan loader, the driver and implicit layers write plain text
straight to fd 2 (and some to stdout, whose fd the helper points at the same
file): on the Frame (a user-reported run on 2026-11-14 at `4a07296`, not
cloud-verified) Mesa's driconf wrote `ATTENTION: default value of option
vk_wsi_force_swapchain_to_current_extent overridden by environment.` and
Gamescope's WSI layer wrote `[Gamescope WSI] Failed to connect to gamescope
socket: gamescope-0. Bypass layer will be unavailable.`, from session
variables the desktop session sets. While the GPU path opens (dlopen of the
loader, instance and device creation, the first pipeline: the window named
`gpu_convert_open`, at start with `--pixel-path gpu_convert` or at the first
frame set under `auto`) and for the whole of `--query-gpu` (`query_gpu`),
fds 1 and 2 point at a non-blocking pipe; when the window closes they are
restored and every captured line becomes a note record on stderr:
`{"event": "note", "source": "library_output", "window": ..., "message": ...,
"truncated": ...}`, with the line's trailing whitespace removed, at most 1024
bytes of it (a cut line says `truncated: true`; a byte that is not UTF-8
becomes U+FFFD and control bytes are escaped, as in every record), at most 32
lines per window. When lines were left out, or the pipe had less room left
than one atomic write (`PIPE_BUF`) so a library's write may have been refused,
one more note says so with `omitted_lines`, `omitted_bytes` and `pipe_full`.
Nothing waits on the pipe: a library that writes more than it holds loses the
rest, and a run does not stall on it. The helper changes no environment
variable to quiet anything, and its own records are as before. Under `auto`
the window opens after the connect, so text the SDK writes during it is
captured the same way; `library_output` names where the text was written, not
which library wrote it. Text written outside a window (a driver thread later,
a message at teardown, a watchdog exit during the window, which loses the
captured text) is not captured and reaches stderr as written; the Python
supervisor's diagnostics reader counts such lines as `non_json_lines` and
keeps none of them, so the promise the tests hold the helper to is: every
line the helper writes is JSON, and library text written while the GPU path
opens becomes JSON. The alternative, documenting third-party lines and letting
the tests skip lines with known driver prefixes, was not taken: it would lose
the text as evidence and need a list of prefixes that drivers and layers do
not keep stable.

| Kind | Header |
| --- | --- |
| `ready` (first, once) | `protocol` 1, `backend`, `pixel_path` (`cpu_linear` or `gpu_convert`; under `auto` the first frame set chooses the path and is released unexported, and the ready follows it; `undecided` only in the ready of a run that ends before the path was chosen, which no frame follows), `build` (scalars: the `--describe` facts, the contract digest, `pixel_path_facts` with the GPU path's device, extensions, `import` (`drm_format_modifier` or `linear_copy_fallback`), `import_strategy`, each strategy's answer in `import_strategy_results` and `chroma_location`, `camera_index_mapping` (`avCam0` is taken as left until the transforms say otherwise), `frame_timeout_ms`, `presence_gap_ns`, `hello_camera_count`, `controls_digest_start`, host clocks), `delivered_format` `{"fourcc":"BGR3"}`, `cameras` (the selected indices), `limits` (`fps`, `max_seconds`, `max_frames`, as requested), `confirmation` (`terminal` or `parent`). It is written once the path is known and the connect has given its facts; a run that ends before then (a pixel path that does not open, a refused connect, no frame set before standby or the watchdog) writes it right before its `end`, with the connect's facts null when they were never read. Every run that passed the confirmation writes exactly one `ready` first and one `end` last, and frames only after a ready that names the path. |
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
  with the frame's modifier and plane table, after checking the modifier
  against the driver's list (listed, the same plane count, sampleable) and
  choosing an [import strategy](#import-strategies-and-the-gpu-query) by the
  driver's image-format answers. A compute shader
  (`tools/camera_convert.comp`, one variant a strategy) reads the stored
  samples and applies the same integer formula as the CPU path, writing BGR
  rows into a host-visible buffer that is read back, so both paths deliver
  identical bytes and the tests can compare them. Where the device lacks the
  modifier extension and the buffer is linear, the import falls back to
  importing the DMA-BUF as a buffer and copying the planes, with the frame's
  offsets and strides, into an optimally tiled image created as the chosen
  strategy wants it; that is the path the runner's software driver (lavapipe)
  exercises. The import is its own stage, so it can be swapped. `auto`
  stays on the CPU path for a linear modifier and opens the GPU path for
  anything else.

### Import strategies and the GPU query

The first export on the Frame (2026-11-13, a user-reported device result,
recorded as evidence E98 separately) ended at the import: the driver, Turnip
on Adreno 750, lists the UBWC modifier `0x0500000000000001` for NV12 as
sampleable with two planes, and then answered
`vkGetPhysicalDeviceImageFormatProperties2` with
`VK_ERROR_FORMAT_NOT_SUPPORTED` (-11) for the image the helper asked for: a
`MUTABLE_FORMAT` image whose format list names `R8_UINT` and `R8G8_UINT`, the
per-plane views the shader read. Mesa's Turnip source says why (read at
releases 25.2.8 and 26.2.6, where the code is the same; the Frame's driver
reports a later development version, which was not read): for the compressed
modifier, `tu_get_image_format_properties` in `tu_formats.cc` refuses any
`MUTABLE_FORMAT` query unless `tu6_mutable_format_list_ubwc_compatible`
accepts its format list, and that function accepts a list of two or more
formats only when every one is in the same UBWC compatibility class
(`fd6_ubwc_compat_mode`); `R8_UINT` and `R8_UNORM` are in none. So no pair of
per-plane view formats, integer or normalized, can pass with UBWC; and
`tu_image_init` in `tu_image.cc` says the same of creation ("NV12 uses a
special compression scheme for the Y channel which doesn't support
reinterpretation"). Without `MUTABLE_FORMAT` the same query goes on to
`ubwc_possible`, which has nothing against a sampled two-plane NV12 image.

The path therefore has three strategies. For each frame layout (format,
modifier, plane count, size) it asks the driver about all three, takes the
first, in this order, whose query succeeds, whose DMA-BUF import the driver
reports possible, whose maximum extent holds the frame and whose features
allow it, and keeps that choice while the layout stays:

| `import_strategy` | Image | Read by the shader |
| --- | --- | --- |
| `plane_views_uint` | `MUTABLE_FORMAT`, format list `R8_UINT`, `R8G8_UINT` (`R16_UINT`, `R16G16_UINT` for P010) | Per-plane integer views, `texelFetch` |
| `plane_views_unorm` | `MUTABLE_FORMAT`, format list `R8_UNORM`, `R8G8_UNORM` (`R16_UNORM`, `R16G16_UNORM`) | Per-plane normalized views, `texelFetch`, each value multiplied by 255 (65535) and rounded back to the stored integer |
| `ycbcr_sampler` | No `MUTABLE_FORMAT`, no format list | One view of the whole image through an immutable combined image sampler with a `VkSamplerYcbcrConversion`: model `RGB_IDENTITY`, range `ITU_FULL`, nearest chroma filter, identity components, the chroma location the format's features offer (midpoint first); needs the `samplerYcbcrConversion` feature and one of the chroma-sample features |

`ycbcr_sampler` is the one expected to be chosen on the Frame, by the reading
above; **that is a reading of source, not a result: no UBWC buffer has been
imported by this helper, and the strategy is unverified on the device until a
run there exports frames** whose content is checked. The conversion model is
`RGB_IDENTITY` rather than `YCBCR_IDENTITY` because the latter subtracts half
the range from the chroma channels before the shader sees them, and Turnip
skips its own conversion code altogether for `RGB_IDENTITY`: the sampler then
converts nothing (green is Y, blue is Cb, red is Cr, as stored) and the
shader applies the fixed-point formula itself after rounding to the sample
range, so the bytes do not depend on a driver's arithmetic. A sampler with a
conversion must be sampled with normalized coordinates, so this variant takes
luma at the pixel's centre and chroma from a second sample 0.75 luma texels
past the even luma coordinate: the chroma position reconstructed from there
lies inside chroma texel (x/2, y/2) for midpoint and for cosited-even chroma
samples alike, which is the texel the CPU path reads.

The chosen strategy is `pixel_path_facts.import_strategy` in the `ready`
header, with every strategy's answer in `import_strategy_results` (for
example `plane_views_uint=VkResult=-11,plane_views_unorm=VkResult=-11,ycbcr_sampler=ok`)
and `chroma_location` (`midpoint`, `cosited_even`, or `not_applicable` for
the plane-view strategies). Under `auto` the path is told the first frame
set's layout before the `ready` is written, so the `ready` names the
strategy; with `--pixel-path gpu_convert` the `ready` precedes the first
frame set and says `undecided`, and the final record's `pixel_path_notes`
names the strategy that imported the frames. When the driver accepts none,
`import_strategy` is `none` and the first conversion ends the run with
`pixel_path_error` and the detail
`camera_N:no_import_strategy:<each strategy's answer>`. A modifier the driver
does not list, lists with another plane count or lists as not sampleable is
refused before any strategy is asked, as before. A strategy the query
accepted can still be refused at the import itself: on the Frame (the same
user-reported run of 2026-11-14) Turnip's query accepted a strategy for the
fake's UBWC frame set and `vkCreateImage` then refused its synthetic plane
table with `camera_0:vkCreateImage(modifier):VkResult=-1000158000`
(`VK_ERROR_INVALID_DRM_FORMAT_MODIFIER_PLANE_LAYOUT_EXT`); the facts then
name the accepted strategy and the run ends with `pixel_path_error` the same
way. The helper has no flag and no environment variable that chooses a
strategy.

`--query-gpu` asks the same driver the same questions without a camera. It
opens the device as `gpu_convert` does and prints one `gpu_query` record
whose `gpu` object holds:

- `device`: the facts `pixel_path_facts` carries (name, API and driver
  version, extensions, the `samplerYcbcrConversion` feature).
- `modifier_extension`, and `programs`: whether each strategy's compute
  pipeline builds on this device (`plane_views_uint`, `plane_views_unorm`,
  `ycbcr_sampler_nv12`, `ycbcr_sampler_p010`; `ok`, `ok:<chroma location>`
  for the sampler conversion, or the failing call and its `VkResult`).
- `formats`, for `nv12` and `p010`: `vk_format`, `modifiers_listed`, and
  `tilings`: the UBWC modifier `0x0500000000000001` first, whether or not the
  driver lists it, then the driver's other modifiers in its order (two at
  most, and fewer when the record would not otherwise stay one line of at
  most 8 KiB; `modifiers_omitted` counts the rest), then optimal tiling. Each tiling has `listed`,
  `plane_count`, the tiling `features` as hex and as named bits
  (`sampled_image`, `midpoint_chroma_samples`, `cosited_chroma_samples`,
  `ycbcr_linear_filter`, `ycbcr_separate_reconstruction_filter`,
  `ycbcr_explicit_reconstruction`, `ycbcr_explicit_forceable`, `disjoint`),
  and `image_format`: twelve rows, one for each combination of
  `MUTABLE_FORMAT` (0, 1), format list (`none`, `uint`, `unorm`) and usage
  (`SAMPLED`, `SAMPLED | TRANSFER_SRC`), each row in the order of `columns`:
  `mutable_format`, `view_formats`, `transfer_src`, `vk_result` (the
  `VkResult` of `vkGetPhysicalDeviceImageFormatProperties2`; null where the
  device has no format list to ask with), `max_width`, `max_height`,
  `external_memory_features` and `compatible_handle_types` (for DMA-BUF;
  zero for optimal tiling, where no external memory is asked about). The
  rows with a format list and without `MUTABLE_FORMAT` describe an image
  `vkCreateImage` would not take; they are there to show what the list alone
  changes.
- `trial_image` (null without the modifier extension): for each strategy the
  query accepts, an image with the layout the Frame's camera service
  delivered (E97: NV12, UBWC, 2464×2464, both strides 2560, the chroma plane
  at 6348800 of a 9564160-byte buffer) is created and destroyed at once, with
  no memory bound; the answer is the memory the driver would want
  (`created:size=…:alignment=…:memory_type_bits=…`), the `VkResult` of
  `vkCreateImage`, or `not_created:query:VkResult=…`. A size above 9564160
  would mean the import is refused later (`buffer_smaller_than_driver_layout`).

One run of `--query-gpu` on the Frame therefore says which strategies the
driver accepts for the delivered format, whether their pipelines build and
whether the delivered plane table makes an image, whatever this change got
wrong; it does not say that imported pixels are right.

Unverified on the device: that Turnip accepts `ycbcr_sampler` for the UBWC
modifier (expected from its source, not run), that an imported UBWC image
samples the camera's content through the conversion with nearest chroma
(lavapipe offers cosited-even chroma samples only, so the midpoint location
Turnip offers has not run anywhere; the shader's chroma coordinate is chosen
to suit both), P010 with UBWC (the Turnip releases read have no P010 format,
so every strategy is expected to be refused for it), the foreign-queue
acquire preserving contents, whether the SDK's descriptors answer the
DMA-BUF sync ioctl, timings at camera size, and how removal and standby
manifest through `acquireFrameSet`.

## Build

```bash
bash scripts/build_camera_helper.sh --fake [--out DIR]          # any architecture; CI and tests
bash scripts/build_camera_helper.sh --sdk DIR [--check-sdk]     # operator only, aarch64 only
bash scripts/build_camera_helper.sh --shader                    # regenerate the three SPIR-V modules (glslang-tools)
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
pixels and byte identity with the CPU path, each import strategy against the
CPU path byte for byte over NV12 and P010 in the three encodings
(`tests/fixtures/camera_gpu_strategy_fixture.cpp` links the two pixel paths
and restricts the GPU path to one strategy at a time, which the helper never
does; lavapipe accepts all three, with optimal tiling and the copy fallback,
so none of them has run over a modifier import or a compressed buffer), and
the `--query-gpu` record's schema, where a software driver (lavapipe)
is installed as CI does; without any driver the query says so and exits 1.
The fake's `export_ubwc` scenario under `auto` holds linear samples under the
UBWC modifier with a synthetic plane table, so no driver can import it
correctly; the test accepts each place a driver can stop it (the path does not
open, the layout is refused before a strategy is chosen, or a chosen
strategy's import refuses the plane table, as Turnip does) and asserts in
every case that the run ends `pixel_path_error` with no frame, the frame set
released, the reader deactivated and the end packet last. Serving Turnip's
real UBWC layout instead was not done: the buffer would still hold linear
bytes, so an import that passed would convert wrong pixels rather than test
anything. The library-output capture is exercised with
`tests/fixtures/camera_chatty_vulkan_icd.cpp`, a fixture driver that the
loader loads from a manifest named by `VK_ICD_FILENAMES` and `VK_DRIVER_FILES`
in the test's environment; it writes the two Frame lines above (one through
stdout), a stray byte, an over-long line and more lines than the bound to
fds 1 and 2, and optionally more bytes than a pipe holds, then refuses the
interface version. Alone it makes the GPU path fail to open, and the test
checks the export mode's stderr is JSON lines only under `auto`, with
`--pixel-path gpu_convert` and with the over-full pipe, and that `--query-gpu`
keeps its one stdout record; listed beside lavapipe, the GPU path converts the
fake's frames exactly while the same notes are recorded. `tests/test_camera_helper_boundaries.py` pins the
rest. All three run in the dependency-free `native` job of `check-jobs.yml`,
which the ARM64 workflow reuses, and may not skip there.

| Rule | Check |
| --- | --- |
| The SDK headers are included by the SDK unit, its status map and the map's test fixture only | `SDK_INCLUDERS` in the boundaries test, over every C and C++ source outside the vendored and ignored trees |
| No helper source has a control, wire-protocol, file-writing or other-track path | Banned text in every helper source (`socket(`, `sockaddr_un`, the provider socket path and lock, `MessageHeader`, `setControls`, `ControlsRequest`, an LED write, a `--yes` or `--no-confirm` flag (the parsed flags are a closed list, `--query-gpu` its one addition), `fopen(`, `ofstream`, `O_WRONLY`, `O_CREAT` except the lock, OpenCV), banned includes (capture-track headers, `handcam_proto.h`), and `getenv` of nothing but `XDG_RUNTIME_DIR` |
| Only the pixel paths read a buffer | `mmap`, DMA-BUF, Vulkan import names, `dlopen` and the Vulkan include appear in `camera_pixels_cpu.cpp` and `camera_pixels_gpu.cpp` only (the fake may `mmap` its own memfd to fill it); `cameraFd(` appears once, in the SDK unit, which never maps, reads or duplicates it; the pixel paths name no SDK type |
| The embedded shader is its source | The SPIR-V header's recorded source digest equals `camera_convert.comp`'s SHA-256; it holds exactly three modules (the strategies' variants), each with its word count, its own recorded digest and the SPIR-V magic, each used once by the GPU path; `spirv-val` accepts each where present, and where the `glslangValidator` the header names is installed the source compiles to the same words |
| The GPU query reaches no camera | `main` returns into `run_query_gpu` before a backend, the lock or the confirmation exists; its body makes one call into the GPU unit and names no backend, session or descriptor; the query's code in the GPU unit allocates, binds, maps and submits nothing; the helper passes no strategy to the GPU path and names none |
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
