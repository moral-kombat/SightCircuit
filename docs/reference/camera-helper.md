# Camera helper reference

The camera helper is SightCircuit's containment for the Arcturus Vision Camera
Preview SDK: the one place the SDK's headers are included, built as a native
helper in the style of the [overlay bridge](overlay.md), and exercised in CI
through a fake backend that needs no SDK. It is step 1 of the
[migration plan](../history/2026-11-09-arcturus-sdk-input-review.md#migration-plan)
in the SDK input review (finding 5, "No containment or test seam for a
licence-restricted native SDK"), built for the cloud-only camera-input lane
(lane G) the owner opened. Setup and the build command are in
[development](../development.md#camera-helper-build-lane-g-2026-11-09).

What this revision is and is not:

- **Cloud work only.** Everything here was built and checked on x86_64 in the
  cloud, and CI repeats the checks on ARM64 after a merge. No device step has
  run, and no statement on this page is about the headset's behaviour.
- **No camera access.** The helper's only runnable modes initialise nothing.
  The modes that connect (`--describe`, `--hello`, `--probe-frames`) are
  step 2 of the plan and are not in this revision. Even then, every mode's
  output is counts, enumerators and numbers, never pixels.
- **The gates stay closed.** `sightcircuit/gates.py` is unchanged; nothing here
  feeds a frame to the pipeline or the HUD. The
  [current work order](../live-capture.md#current-work-order) still defers
  dedicated camera capture.
- **The agreement applies.** Section 3 of the
  [SDK agreement](../../third_party/arcturus-camera-sdk/AV1CameraSDK-LICENSE.md)
  (informed camera use, consent before saving or sending Camera Data, stopping
  when the headset is removed, no private camera APIs) binds every SDK path;
  the Client Library is never redistributed. The helper is written so that a
  future mode cannot violate the first three by accident: its interface has no
  pixel path, no control setter and no recording flag.

## Source layout

Five translation units in `tools/`, one entry point plus units as the bridge
has. None is pinned by the checkpoint manifest, so the camera lane leaves the
pinned bridge, agent and command files untouched until lane F completes.

| Unit | Role |
| --- | --- |
| `camera_helper.hpp` | The interface the units share: the `Backend` class, the metadata records (numbers and flags only), the mirrored enumerators, a small ordered JSON value, and the declarations of the reporting and backend units. Includes `overlay_lifecycle.hpp` unchanged for the house lifecycle pattern. No SDK header. |
| `camera_helper.cpp` | `main()`: argument parsing and the validate, report sequence (isolate stdout, watchdog, parent-death signal, one final report). |
| `camera_helper_reporting.cpp` | Every protocol line: the usage, the watchdog message, the report fields and the enumerator names. The only writer of JSON lines. |
| `camera_helper_fake.cpp` | The fake backend: implements `Backend` without the SDK, scripted from a text file, following the SDK's session rules. Every CI build links this unit. |
| `camera_helper_sdk.cpp` | The SDK backend: the only SightCircuit source that includes `AV1CameraClient.h`. Linked only when the operator names a local Client Library; CI syntax-checks it against the vendored headers and never links it. |

The binaries are `artifacts/camera-helper/sightcircuit-camera-helper-fake`
(always built) and `artifacts/camera-helper/sightcircuit-camera-helper-sdk`
(operator-built only). Both have the same modes and write the same report
fields; `backend` on every line says which one wrote it.

### The backend interface

`Backend` is what any mode may ask of a camera session: `connect` (connect
and the mandatory handshake), `disconnect`, `connected`, `hello` (protocol
version, camera count, offline-calibration source and size, control state),
`control_state`, `set_reader_active(bool)`, `acquire_frame_set` (metadata
only), `release_frame_set` and `frame_set_held`. Three things are absent on
purpose and tests keep them absent:

- no accessor for a buffer descriptor, so no unit built on the interface can
  map, read, copy or save a pixel (the SDK unit never calls `cameraFd`);
- no `setControls`, so no shared camera control, including the red LED, can
  change;
- no recording flag: the SDK unit calls `setReaderState(active, false)` and
  nothing else, so exclusive camera control is never requested.

A frame set's metadata record carries the sequence, camera count, pose and
velocity validity flags and, per camera, the frame identifier, exposure
timestamp, width, height, DRM format and modifier, plane count with offsets,
strides and sizes, pixel format, transfer function, primaries, YCbCr
encoding, sensor temperature, exposure, gain, the intrinsics model and
validity flags, and the auxiliary JSON size. The descriptor, intrinsics
coefficients, transforms, pose and auxiliary JSON stay in the SDK's reply,
which is released with the frame set. At most one frame set is outstanding.

The vocabulary (result codes, pixel formats, the error kinds) lives on the
helper side for now, as the plan's step 2 says; the SDK unit `static_assert`s
every mirrored value against the vendored protocol header, so the CI syntax
check fails if the SDK's enumerators move. Step 4 of the plan replaces this
with a generated Python contract.

## Containment rules and the tests that pin them

`tests/test_camera_helper.py` runs in both CI jobs' architectures (the
`native` job of `check-jobs.yml`, which the ARM64 workflow reuses) and may not
skip there.

| Rule | Check |
| --- | --- |
| The SDK headers are included from the SDK unit only | No tracked file under `tools/`, `sightcircuit/`, `scripts/` or `tests/` other than `camera_helper_sdk.cpp` (and the two tests about the SDK) contains `AV1Camera`; the SDK unit includes the client header once and never the protocol header directly |
| No SightCircuit source names the Client Library except the build script | The same tree is searched for `libAV1CameraClient`; `.gitignore` excludes the file name at any depth (`git check-ignore`) |
| The helper never writes pixels | After comments are removed, no helper source contains a descriptor accessor, `mmap`, a file-writing call, an image encoder, `setControls`, `ControlsRequest`, `socket(`, the wire-protocol structures or socket path, or a capture-track, OpenVR or Vulkan header; `setReaderState(` appears exactly once, as `setReaderState(active, false)`; the `Backend` class mentions no recording, descriptor, control or LED |
| The fake binary has no SDK symbol | `nm -C` of the fake binary shows no `AV1CameraClient` or `av1_ipc` symbol |
| The SDK unit needs only the allowed client calls | Compiled to an object (never linked), its undefined symbols are exactly the client's constructor/destructor, `connect`, `disconnect`, `isConnected`, `getControlState`, `setReaderState`, `acquireFrameSet`, `FrameSet::release` and the frame set's destructor; never `setControls` |
| The fake backend follows the SDK's session rules | A C++ fixture (`tests/fixtures/camera_helper_fake_fixture.cpp`) drives scripted success, permission-denied, client-already-connected and protocol-error scenarios through the interface |
| The build script refuses what it must and never downloads | In-tree, missing, non-archive and twice-named library paths exit 64 before anything compiles; a stub archive of the host's architecture passes the checks and fails at the link for lack of the client's symbols; the script contains no fetch command |
| The lifecycle is the house pattern | One JSON line per report with `report_kind`, `schema_version` and `backend`; exit codes 0, 1, 3 (no arguments) and 64 (arguments); `--validate` reports `validation_only: true` and every claim below; reports reach the protocol with stderr closed |

## Build

```bash
bash scripts/build_camera_helper.sh                               # fake binary + SDK unit syntax check
bash scripts/build_camera_helper.sh --client-library /path/to/libAV1CameraClient.a   # operator only
```

The script always builds the fake binary and syntax-checks the SDK unit with
`-fsyntax-only` against `third_party/arcturus-camera-sdk/`; that is the whole
of what CI does. It links the SDK-backed binary only when the operator names a
local copy of the Client Library with `--client-library PATH` or the
environment variable `SIGHTCIRCUIT_AV1_CLIENT_LIBRARY`. Before any compile it
refuses, with exit 64 and a message naming the reason, a path that is not an
existing file, a path inside the work tree (the library is never committed,
cached or built here), a file that is not an `ar` archive, an archive whose
objects are for another architecture than the build machine, and a library
named twice. It never downloads anything. When it links, it first compiles the
SDK unit to an object and refuses if that object references
`AV1CameraClient::setControls`. `--output DIR` builds somewhere other than
`artifacts/camera-helper/` (the tests use it). Each successful run writes
`build-report.json` beside the binaries: the fake binary's SHA-256, whether
the SDK binary was built and from which library path, and the constants
`client_library_downloaded: false` and `camera_accessed: false`.

Who builds the SDK-backed binary: the owner, locally, on the machine that has
the Client Library. Not CI, and not an agent task: the on-device agent guard
does not yet treat SDK-linked artifacts as private (the review's finding 5;
the agent files are pinned by the checkpoint manifest, so that change waits
for lane F). The `--validate` mode of the SDK binary is the first thing to run
after such a build; it constructs no client and opens no socket.

## Protocol

Every line is one JSON object with `report_kind: "sightcircuit_camera_helper"`,
`schema_version: 1` and `backend: "fake" | "sdk"`. The real stdout is kept for
the protocol and fd 1 points at stderr before anything else runs, so library
output cannot corrupt a report. A SIGALRM watchdog (5 s for the validation
step, 5 s for the final write) writes one prepared JSON error
(`watchdog_expired:<phase>`, exit code 124, `cleanup_ok: false`) and calls
`_exit`; a torn line is terminated first. The helper also asks for `SIGTERM`
on parent death.

| Invocation | Exit | Report |
| --- | --- | --- |
| no arguments | 3 | `event: not_run`, `error: choose_a_mode` |
| `--help` | 0 | the usage text, not JSON |
| `--validate [--fake-script FILE]` | 0 | `status: validated`, `validated.fake_script` (the path or null) and, in the fake build, `validated.fake_script_steps` with the per-call counts; in the SDK build `validated.sdk_protocol_version` and `client_constructed: false` |
| `--validate` with an unreadable or malformed script, or `--fake-script` in the SDK build | 1 | `error: fake_script_unreadable`, `fake_script_line_<N>:<reason>` or `fake_script_requires_the_fake_backend` |
| any other mode, a repeated or dangling argument | 64 | `error` names the problem |

Every final report carries the claims `validation_only`, `camera_connected`,
`reader_enabled`, `recording_requested`, `controls_set`, `led_touched`,
`frame_sets_acquired` and `pixels_accessed`, `frame_data_written` and
`cleanup_ok`. In this revision they are constants (`false`, `0`, `true`): the
interface has no path that could make one of them otherwise. They are
reported so that a step-2 mode has fields to fill and a reader has fields to
check.

## The fake backend's script

The fake backend is scripted from a text file given with `--fake-script FILE`
(the SDK build refuses the option). One step per line,
`<call> <outcome> [key=value ...]`; `#` starts a comment.

| Part | Values |
| --- | --- |
| call | `connect`, `control_state`, `reader`, `acquire`, `release` |
| outcome | `ok`; `server <result>`; `not_connected`; `invalid_argument`; `transport [errno=N]`; `timeout` (the socket timeout, which closes the session); `protocol`; `out_of_memory` |
| result (after `server`) | `success`, `unavailable`, `invalid_argument`, `client_protocol_too_new`, `not_ready`, `internal_error`, `timeout` (the server's frame timeout, session open), `resource_denied`, `client_already_connected`, `permission_denied`, `client_protocol_too_old` |
| `repeat=N` or `repeat=forever` | how many calls one step answers (default 1) |
| keys on `connect ok` | `protocol_version`, `camera_count`, `offline_cal_source` (`none`, `eeprom`, `file`, `default`), `offline_cal_size`, and the control-state keys below, which set the handshake's control state |
| control-state keys (`connect ok`, `control_state ok`) | `red_led`, `bit_depth`, `awb` (`on`, `off`, `freeze`), `hdr`, `tone_mapping`, `transfer` (`linear`, `srgb`, `hlg`), `primaries` (`bt709`, `bt2020`), `encoding` (`bt601`, `bt709`, `bt2020`), `resolution` (`auto`, `full_resolution`, `downsampled`), `fps`, `brightness_ev`, `auto_exposure`, `exposure_ms`, `gain` |
| keys on `acquire ok` | `sequence`, `cameras`, `width`, `height`, `format` (`nv12`, `nv12_ubwc`, `p010`, `p010_ubwc`), `drm_format`, `modifier`, `planes`, `stride`, `frame_id`, `exposure_ns`, `timestamp_s`, `exposure_s`, `gain`, `temperature_c`, `transfer`, `primaries`, `encoding`, `model` (`kb4`, `kb_spline`, `kb_polar_spline`, `pinhole`), `intrinsics_valid`, `kb4_valid`, `transform_valid`, `pose_valid`, `velocity_valid`, `aux_json_size` |

Numbers may be decimal or `0x` hexadecimal; named values may also be given
as their numeric enumerator. Steps of one call are consumed in order. An
exhausted call answers `ok`, except `acquire`, which answers the server's
frame timeout so that a run never finds frames the script did not give it.
Defaults for a scripted frame: two cameras of 1280 × 1024 `nv12_ubwc` with
the UBWC modifier `0x0500000000000001` that `tools/capture_import.hpp` pins,
two planes with Y and interleaved chroma sizes, sequence and frame identifiers
incrementing, sRGB transfer, BT.709 primaries and encoding; these are stand-in
values for the fake, not device facts.

The fake applies the SDK's rules as the headers state them: a call before a
successful connect fails `NotConnected`; a transport, protocol or
socket-timeout failure closes the session and invalidates the held frame set;
a server-side result leaves the session as it was; a second connect fails
`InvalidArgument`; frames reach only an active reader (otherwise
`Result_NotReady`); and at most one frame set is outstanding. The fixture's
four scenarios are the ones step 2's modes must handle: success,
permission denied (on connect and on the reader), client already connected,
and a protocol error that ends the session.

## Device steps

None in this revision, and none are authorised by it. The plan's two probe
phases (Phase A: connect, handshake and control state with the reader never
activated; Phase B: the reader active for at most 10 s, metadata only) come
with step 2's modes, each under the owner's separate approval, after the
staged device steps of the other lanes, and never in the same session as
them. Their operator procedure is written with step 2.
