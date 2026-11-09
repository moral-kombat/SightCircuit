# Arcturus camera SDK and the input phases, 2026-11-09

> Dated record. It preserves an offline architecture review of how the
> Arcturus Vision Camera Preview SDK would enter SightCircuit's input phases
> (acquisition, buffer import and conversion, native format, admission and
> detector input). File and line references are to revision `4b440e1`, which
> vendored the SDK's public headers into
> [third_party/arcturus-camera-sdk](../../third_party/arcturus-camera-sdk/README.md).
> Nothing here is a device result, opens a gate, changes scope or authorizes a
> device step. Scope stays with the owner in [scope](../poc-scope.md); the work
> order stays in [current work](../live-capture.md#current-work-order), where
> dedicated camera capture is still deferred.

## How the review ran

The owner accepted the SDK agreement (version 2026-11-08) and obtained the SDK
directly from Arcturus. A local session relayed the public headers, the
agreement, the SDK README, the release manifest, the CMake module and the
camera-related example sources to a cloud session. Each file was checked
against the release manifest's SHA-256. Only the two headers and the agreement
are in the repository; the Client Library never left the owner's machine.
Facts from the other SDK materials are restated here in our own words.

The cloud session ran four read-only passes over the checkout:

1. An architecture review in the `/deepen` style, looking for shallow modules,
   needless layers, coupling and incoherence. Four readers covered the source
   seam, native helper patterns, decoding and the recording surface, and
   governance. A proposer drafted ten candidates, and two adversarial verifiers
   checked them, one for evidence accuracy and one for scope and fit. The
   readers assumed AV1-encoded video before the headers arrived, so their
   decode-centred conclusions are recorded below as moot.
2. An inventory of every input-phase fact, sorted into three layers in the
   manner of a design-token hierarchy (described under
   [layered input architecture](#layered-input-architecture)), together with
   an audit of how Python and C++ contracts are kept in step.
3. A design panel. Three designers started from different constraints (GPU
   conversion in a helper, CPU plane copies, contract first). Two judges scored
   the designs, one for device safety, licence compliance and Frame
   performance, and one for architecture depth and testability.
4. The coordinator verified the citations used below against the checkout.

## What the SDK provides

Facts from the vendored headers, [AV1CameraClient.h](../../third_party/arcturus-camera-sdk/AV1CameraClient.h)
and [AV1CameraIPCProtocol.h](../../third_party/arcturus-camera-sdk/AV1CameraIPCProtocol.h):

- **A C++11 pull client over a Unix socket.** It is used in four steps:
  connect with a mandatory handshake, enable frame access with
  `setReaderState(true)`, call `acquireFrameSet` with a timeout, then release
  the frame set. The client never retries or reconnects. A transport or
  protocol failure closes the session and invalidates every frame set it handed
  out.
- **One client at a time.** A second client is refused with
  `Result_ClientAlreadyConnected`. Frame and control calls return
  `Result_PermissionDenied` until the owner enables developer camera access in
  Camera Switch.
- **Raw camera buffers, not video.** Despite the "AV1" name, nothing is
  encoded. Each frame set holds up to two cameras. Each camera arrives as a
  borrowed DMA-BUF descriptor with:
  - width and height, a DRM format and modifier, and up to four planes, each
    with an offset, stride and size;
  - a pixel format of `NV12`, `NV12_UBWC`, `P010` or `P010_UBWC`;
  - full-range YCbCr, with the encoding (BT.601, 709 or 2020), transfer
    function (linear, sRGB or HLG) and primaries (709 or 2020) given per frame.

  The buffer stays reserved until the frame set is released.
- **Per-frame metadata.** Each camera has a `frameId`, an exposure timestamp
  in nanoseconds (the header does not name the clock), intrinsics in one of
  four camera models, a thermally corrected KB4 model, a camera-to-camera
  transform, auxiliary JSON, exposure, gain and sensor temperature. Each frame
  set has a 64-bit sequence and one pose with head velocities. The reply
  structure is 19,304 bytes on x86_64; most of that is the two 8 KiB
  auxiliary JSON buffers.
- **Shared camera controls.** These are sensor bit depth, white balance, HDR,
  tone mapping, transfer function, resolution mode, frame rate, brightness,
  primaries, YCbCr encoding, exposure and gain, and a red recording LED. A
  change affects every application using the cameras. Setting the reader's
  `recording` flag requests exclusive camera control.

The MIT-licensed OpenXR example shows the intended use. It runs one worker
thread that owns the client and reconnects with backoff. It stops for good on
permission or protocol errors. It keeps at most three frame sets outstanding
and queues releases back to the worker. Generation counters drop frames that
belong to an older connection or activity. It pauses when the headset is
removed, turns the red LED on and later restores the previous LED state, and
imports each camera with Vulkan using the frame's own DRM modifier and plane
layout. It tells left from right by the camera transforms, not by camera index.

The [agreement](../../third_party/arcturus-camera-sdk/AV1CameraSDK-LICENSE.md)
adds terms the design must respect:

- **Camera Data (section 1).** This includes detections and other information
  derived from frames.
- **Informed camera use (section 3.1).** Camera activity and the use of Camera
  Data must be obvious to the user.
- **Saving and sending (section 3.2).** Saving or sending Camera Data needs
  explicit, informed authorization. Image processing must stop when the
  headset is removed, unless the user is aware that it continues.
- **No private camera APIs (section 3).** Applications must not use private or
  internal APIs to reach camera functionality.
- **Distribution (section 2).** The Client Library is never redistributed.
  Marketplace distribution is excluded.

## What changes for SightCircuit

1. **There is no decode step.** The replay decoder is not the template. The
   work is buffer import and colour conversion, and UBWC buffers cannot be read
   on the CPU at all. Repository evidence points to UBWC as the likely default:
   - [tools/capture_import.hpp:9](../../tools/capture_import.hpp) pins modifier
     `0x0500000000000001`;
   - two of the three accounts in the
     [camera reference](../steam-frame-camera.md#steam-frame-and-arcturus-camera-integration)
     report UBWC.

   Only a device run can settle this.
2. **The passthrough cameras never see the HUD.** They image the room before
   compositing. The mirror input contains SightCircuit's own panel
   ([E18](../evidence/e018.md)), which is why the mirror path needs masking and
   feedback exclusion. A camera source does not have that feedback path.
   How the acceptance rows and gates, written for mirror input, apply to a
   camera source is the owner's decision.
3. **Passthrough is the first risk.** Third-party notes report that XRService
   closes its own passthrough while an external client owns the colour cameras
   ([camera reference](../steam-frame-camera.md)), and the public probe in
   [E3](../evidence/e003.md) interrupted passthrough. The POC requires that
   passthrough is not disturbed ([scope](../poc-scope.md)).
4. **Any SDK use makes SightCircuit an Application under the agreement.**
   Section 3 then applies to every SDK path. Its ban on private camera APIs
   also bears on the frozen capture-provider track and the shared-memory
   reader. Interpreting this is for the owner and is not legal advice.
5. **Current documents still say no direct camera API exists.** Examples are
   [steam-frame-camera.md](../steam-frame-camera.md) (the overview table, and
   the "no reply is recorded yet" note on the owner's 2026-11-08 inquiry) and
   the deferral statements in [scope](../poc-scope.md) and
   [current work](../live-capture.md#current-work-order). The SDK agreement is
   dated the same day as that inquiry. Updating the scope statements is an
   owner decision. The camera reference can be refreshed afterwards as
   documentation work.

## Layered input architecture

The design-token method separates raw values (primitives) from the project's
named vocabulary (semantics) and from each consumer's derived view
(components). Each source variant then becomes a theme that binds the same
semantic layer in its own way. Python stays the single source of truth for any
contract that crosses into C++, as [sightcircuit/hud/native.py](../../sightcircuit/hud/native.py)
already does when it generates `tools/hud_contract.hpp`.

### Primitive layer: facts owned by exactly one adapter

| Fact | Owner today | Proposed owner |
| --- | --- | --- |
| SDK socket, framing, `Status` codes, frame-set lifetime | None | One C++ translation unit that alone includes `AV1CameraClient.h` |
| DMA-BUF format, modifier, planes, colour metadata | None | The helper's import stage; Python sees only names from a generated vocabulary |
| Exposure timestamp, `frameId`, frame-set sequence | None | Copied into the semantic record with a named, still unverified clock |
| Intrinsics, transforms, pose, auxiliary JSON | None | Kept in the helper; only digests cross in the first version, and left/right comes from transforms |
| V4L2 FOURCC table and buffer sizes | [mirror_device.py:109-130](../../sightcircuit/sources/mirror_device.py) | Unchanged, but bounded by the one semantic rule below |
| Decoder output (PyAV) | [replay.py:111-159](../../sightcircuit/sources/replay.py) | Unchanged; its resize moves out (see finding 3) |

### Semantic layer: SightCircuit's vocabulary

| Term | Today | Proposed |
| --- | --- | --- |
| Accepted layouts | RGB3/BGR3 allow-list written four times: [frame.py:68](../../sightcircuit/frame.py), [convert.py:35-41](../../sightcircuit/convert.py), [hud/contract.py:258](../../sightcircuit/hud/contract.py), [mirror_device.py:109-111](../../sightcircuit/sources/mirror_device.py) | One module (for example `sightcircuit/acquisition.py`) with one layout and stride rule and one bound |
| Native bounds | 4096 px / 48 MiB ([frame.py:7-8](../../sightcircuit/frame.py)) against 8192 px / 64 MiB ([mirror_device.py:19-20, :114](../../sightcircuit/sources/mirror_device.py)) | One bound; both judges favour `frame.py`'s values, which fit every observed mirror and SDK size |
| Processing bound | 960 written three times: [convert.py:4](../../sightcircuit/convert.py), [hud/contract.py:35](../../sightcircuit/hud/contract.py), and a re-implementation in `hud/mask.py` | One constant, applied only in the component layer |
| Native format | One descriptor that must claim RGB3/BGR3 ([frame.py:68](../../sightcircuit/frame.py)) | Two records: the acquisition format (what the camera produced) and the delivered format (what SightCircuit received), with a `derivation` field (`identity`, `decoder_scaled`, `gpu_area_scaled`) |
| Identity | 16 `SOURCE_FIELDS` with alias pairs and unused names ([context.py:41-54](../../sightcircuit/context.py)); no camera, producer sequence or exposure field on `Frame` | Add camera identity, producer sequence and exposure time on a named timebase; a session token minted in Python before the helper starts |
| Stereo | `capture_layout` splits one frame | A frame-set record (two cameras sharing a sequence, pose and session), or per-camera frames that carry the frame-set sequence |

### Component layer: per-consumer views

| Consumer | Derives from | Rule |
| --- | --- | --- |
| Detector input | Delivered format | At most 960 px, converted in the component layer only |
| HUD mask | Delivered format | Refuses any frame whose derivation is not `identity`; a camera source does not need it |
| Preview, still, report | Delivered format and identity | Persisting needs a recorded consent (section 3.2); every report field is classed as operational or Camera Data |

### Themes: how each source binds the semantic layer

| Source | Today | Proposed |
| --- | --- | --- |
| Synthetic | Binds nearly every field; the only kind the live gate accepts ([live.py:72-74](../../sightcircuit/runtime/live.py)) | Unchanged; may gain a synthetic frame-set variant to rehearse stereo plumbing with every gate closed |
| Replay | Resizes to 960 while decoding ([replay.py:150-151](../../sightcircuit/sources/replay.py)), declares no native format or identity, runs without a capture context | Emits native frames with a declared derivation and identity. Arcturus-app recordings come from the same cameras, so replay becomes the offline rehearsal of the SDK theme |
| Mirror | Binds the native format; identity is mostly `None` unless a fixture is injected | Unchanged behaviour; its flat V4L2 descriptor stays byte-identical, or migrates through an explicit, tested step |
| SDK camera | Absent | Binds acquisition and delivered formats, camera identity, sequence, exposure clock and a session token; kind `arcturus_camera`, never synthetic |

### Cross-language contract

Only the HUD contract is generated today, and it is checked in four places
(`tests/test_hud_contract.py`, the CI `--check` step, a mutation test and a
built-binary digest). Every native-to-Python vocabulary is written by hand on
both sides, and no project code streams frames into Python. The proposal:

- A Python camera contract module generates `tools/camera_contract.hpp` with
  the same drift tests. A generated name mapping to the vendored `av1_ipc`
  enumerators, plus a test that parses the vendored protocol header, turns SDK
  enum drift into a CI failure.
- **Demand-driven acquisition.** The helper acquires a frame set only when
  Python asks, at most at the pipeline's `max_fps`. It holds at most one lease,
  imports each frame fresh and releases before publishing. It never acquires
  at camera rate.
- Pixels travel through a memfd slot that Python creates and seals. Control and
  events travel as JSON lines: ready (with contract digest, protocol version,
  camera count and calibration digest), `permission_denied`,
  `protocol_mismatch`, `camera_busy`, `no_frame` or standby, `transport_lost`
  and `format_changed`.
- Status mapping follows the SDK's own rules:
  - Socket timeout, transport, protocol, not-connected and a failed release are
    terminal `transport_lost`; no buffer is touched again.
  - Server timeout, not-ready and unavailable are retried.
  - Permission, protocol mismatch, already-connected and resource-denied are
    terminal, with no retry and no attempt to take over.
- `supervise_owned` refuses to run off the main thread
  ([supervision.py:185-187](../../sightcircuit/runtime/supervision.py)),
  while `FrameSource.run` executes on the pipeline's capture thread. The
  helper client therefore needs its own bounded lifecycle, built from the same
  grace budgets, instead of calling the supervisor from the source.

## Deepening findings

At most seven, ranked by impact relative to effort. One concept per finding.

### 1. Ungated live-source hook in the replay window

**Signal:** missing seam; an unguarded path.
**Evidence:** `ReplayWorker` accepts `source=` and treats any given source as
live ([replay_window.py:27-43](../../sightcircuit/commands/replay_window.py)).
No caller passes it. If used, it would send live frames to the VR panel through
`OverlaySink` without `require_synthetic`, outside the E47 exception, which
covers recorded replay only. `Pipeline(` is built only in `runtime/live.py` and
the replay window.
**Suggestion:** make `ReplayWorker` file-only. Add structural tests: `Pipeline(`
may be built only in `run_report` and around a `ReplaySource`; `run_report(`
may be called only from `runtime/live.py`; inside `sightcircuit/`, only
`runtime/synthetic.py` declares `kind = "synthetic"` or a `synthetic:` id. An
SDK source can then reach the engine only through a new, reviewed runner.
**Impact:** High; small effort. It narrows the gate surface and opens nothing.

### 2. Camera-use policy has no home

**Signal:** missing seam.
**Evidence:** gate claims are stated per command, and no live-recognition report
carries `gates.claims()`. The synthetic report hard-codes its kind
([live.py:175](../../sightcircuit/runtime/live.py)). Section 3 adds rules for
consent, presence and indication that no module owns.
**Suggestion:** add one policy module beside `gates.py` that every SDK path
calls. It requires an informed start (a typed confirmation that states the
camera, the duration and that nothing is saved). It requires a consent record
before persisting Camera Data. It requires a presence signal, or an explicit,
bounded awareness record. It never calls `setControls`, never sets the reader's
`recording` flag and never changes the LED without an owner-approved mode. It
reads the control state at start, pause, resume and end, and reports any change
as made by another party.
**Impact:** High for compliance; small effort.

### 3. Replay does component work during acquisition

**Signal:** layer violation; low coherence.
**Evidence:** decode resizes to the 960 bound
([replay.py:150-151](../../sightcircuit/sources/replay.py)). `Frame.width`
then describes the resized image, `native_format(frame)` raises for replay
frames, and `native_to_inference_scale` reads 1.0. Replay runs without a
capture context.
**Suggestion:** emit native frames with a declared derivation and move the
resize into the component layer. The replay pacing counters and their report
names stay as they are (E53, E62). Replay of Arcturus-app recordings then
rehearses the SDK theme's admission and identity rules offline. Weigh the
cost first: E53 shows realtime HEVC replay at camera size is decode-bound on
the Frame, so keep decoder-side scaling available as a declared `decoder_scaled`
derivation.
**Impact:** High; medium effort.

### 4. The input vocabulary is scattered and closed to camera formats

**Signal:** duplicated knowledge; shallow modules.
**Evidence:** the RGB3/BGR3 allow-list appears four times, the stride and
minimum-buffer rule four times, the native bounds twice with different values,
and the 960 bound three times (see the semantic table above). Eight of the
nine `FORMAT_METADATA_KEYS` never match, because the mirror nests them inside
`native_format` ([context.py:51-54](../../sightcircuit/context.py)).
**Suggestion:** one acquisition module owns the layout rule, the bound, the
derivation vocabulary and the acquisition-versus-delivered split. `Frame`,
`convert`, `CaptureFormat` and `mirror_device` consume it. Whether `convert.py`
learns NV12/P010 or conversion stays in the source is decided after the device
answers linear or UBWC. Both judges prefer source-side conversion if UBWC.
**Impact:** High; medium effort.

### 5. No containment or test seam for a licence-restricted native SDK

**Signal:** missing seam.
**Evidence:** native SDKs are fetched by pinned git revisions into `.cache/`
([build_openvr_tool.sh](../../scripts/build_openvr_tool.sh)). Nothing yet
builds against a locally supplied, ARM64-only archive. The on-device agent may
read any checkout path outside its private directories, including ignored
ones such as `.cache/` and `artifacts/`
([agent/guard.py:109-122](../../sightcircuit/agent/guard.py)).
**Suggestion:**
- `scripts/build_camera_helper.sh --sdk DIR` refuses a directory inside the
  work tree. It refuses anything that does not match `release.json`, builds on
  aarch64 only, writes into ignored `artifacts/`, and offers a verify-only
  `--check-sdk` mode.
- Exactly one translation unit includes `AV1CameraClient.h`, and only the
  translation stage includes the protocol header.
- A fake backend drives everything else in x86_64 CI against the vendored
  headers.
- Source-text bans: no `socket(`, no provider-socket literal, no
  `MessageHeader`, and no capture-track headers in `tools/camera_*`.
- An `nm` allow-list over `AV1CameraClient` symbols that excludes
  `setControls`, plus a single-instance `flock` before connecting.
- The agent guard treats the SDK-linked helper's artifacts as private.
**Impact:** High; medium effort. Everything except the guard change can land
on paths the checkpoint manifest does not pin. The agent files are pinned, so
the guard change waits for lane F. Until then the helper is built only by the
owner, never by an agent task.

### 6. `Frame` cannot carry a camera's identity or clock

**Signal:** missing seam.
**Evidence:** `Frame` has no camera, producer-sequence or exposure field, and
stereo exists only as a split of one frame. Two 2464-pixel cameras side by side
(4928 px) would exceed the native bound
([frame.py:7](../../sightcircuit/frame.py)). Left and right must come from
transforms, as the SDK example does it.
**Suggestion:** a frame-set record, or per-camera frames that carry the
frame-set sequence. Add an exposure time on a named timebase that is never
presented as `observed_ns`, and a Python-minted session token that the
helper's handshake echoes. Narrow the first version to what admission and
reports use; reserve full pose and camera models for a later version with a
named consumer.
**Impact:** High; medium effort; part of it waits for the device probe.

### 7. Native helpers each re-implement their lifecycle and protocol

**Signal:** duplicated knowledge.
**Evidence:** the overlay client, the shared-memory check and the bridge each
handle spawning, line caps, final-report rules and escalation themselves. The
generic stdout-isolation and watchdog helpers are filed as overlay code
(`tools/overlay_lifecycle.hpp`), and only the HUD contract is generated.
**Suggestion:** generalize the contract generator for a camera contract. Give
the camera helper the lifecycle pieces it needs (stdout isolation, parent-death
signal, bounded writes, armed handshake) in a helper-neutral header. Move the
overlay files only after the staged F3 run, because they are pinned by the
checkpoint manifest.
**Impact:** Medium; large effort if done all at once, so do it incrementally
with the camera helper as the first user.

### Considered and set aside

- **One shared worker-report validator.** Dropped. Only about four checks are
  shared, and the rest differs on purpose for each command.
- **A common source contract** (`cleanup_ok`, `report()`). It is real but
  modest. Defer it until the SDK source exists to justify it.
- **Moving the mirror launcher into a neutral module.** The launcher starts a
  Python interpreter, while an SDK command supervises a native helper, so the
  reuse is smaller than it first appears.
- **A shared decoder object.** Moot, because the SDK delivers no encoded
  stream.
- **One owner for the recording directory.** Dropped. The duplication is real
  but small and unrelated to the SDK.

## Recommended design

The judges split. The safety lens chose GPU conversion in the helper. The
testability lens chose the contract-first design with CPU-linear pixels as the
first path. Both agreed on the rest, so the recommendation combines them:

1. **Contract first.** A Python camera contract with a generated C++ header,
   the acquisition-versus-delivered split, the frame-set record and the status
   mapping above.
2. **Probe before plumbing.** The helper's metadata-only modes, the SDK
   translation unit, the fake backend and the build script land first, on paths
   the manifest does not pin, with a temporary helper-side vocabulary. The linear-versus-UBWC answer then decides the
   pixel path, before any planar Python code is written.
3. **GPU conversion if UBWC, which is likely.** Import each frame with Vulkan
   using its own modifier and plane layout, as the MIT example does. Convert
   with a sampler chosen from the frame's encoding, map transfer and primaries,
   crop, rotate upright and area-scale to at most 960 px. Read back into the
   memfd slot. A 960-pixel BGR slot is about 2.7 MB, against about 9 MB of NV12
   or 18 MB of BGR at full size, and the pipeline copies every published frame
   ([pipeline.py:461-462](../../sightcircuit/pipeline.py)). Import per frame;
   enable any import cache only after device evidence and a vendor answer.
   Whether the frozen track's import code (`tools/capture_import.hpp`,
   `tools/capture_gpu_resources.hpp`) or the MIT example is adapted is an
   owner decision.
4. **CPU-linear only if the device reports linear buffers.** The helper maps
   the selected planes with DMA-BUF sync and copies them into the slot. Python
   converts, with golden tests for every matrix and range.

CI never runs the GPU path. The fake build's converter is a reference
implementation, so device evidence remains the only proof of the shipped
converter.

## Migration plan

Each step can be merged on its own. Steps 1–3 touch no path pinned by the
checkpoint manifest ([manifest.py:289-293](../../sightcircuit/runtime/manifest.py)
pins every module under `sightcircuit/`, the checkpoint scripts and the bridge
sources). No camera device step runs before the staged F3, C5/C6 and Phase 2
sittings are complete, and never in the same session as them.

1. Containment and build: `build_camera_helper.sh`, the fake backend, the
   source-text and symbol checks, and CI on both architectures for everything
   except the SDK translation unit (finding 5).
2. The helper's metadata modes: `--describe`, `--hello` (connect, handshake and
   control state only) and `--probe-frames N` (metadata only, no pixel access).
   Until step 4, their vocabulary lives on the helper side and tests read it.
3. The operator procedure for the two probe phases below, in the device guide,
   once the owner opens the lane.
4. After lane F completes: findings 1 and 2, and the Python camera contract
   with its generated header and vendored-enum coverage test, replacing the
   helper-side vocabulary.
5. Finding 4 (the acquisition module and one bound), then finding 3 (replay
   declares native frames), then finding 6 (identity and frame sets).
6. After the probe answers linear or UBWC: the chosen pixel path, the camera
   source in `sightcircuit/sources/`, and a bounded recognition command with no
   display, all unrunnable live until the owner classifies them under the
   gates.

## Device steps, each separately approved and owner-run

1. **Phase A.** Connect, handshake and read the control state, with the reader
   never activated. The wearer watches passthrough during the run and for
   30 seconds after, with the G2 recovery procedure ready. Record the
   observation as an operator field, not a software claim.
2. **Phase B.** Activate the reader for at most 10 seconds and read metadata
   for a few frame sets: format, modifier, planes, sizes, timestamps, camera
   count and sequence gaps. Repeat the passthrough observation, and include a
   headset-removal sub-step to see whether frames stop. Any disturbance in
   either phase stops the SDK track.
3. **Pixel path without saving.** Counts and timings only, after the owner's
   consent and presence decisions.
4. **Bounded recognition without display**, comparable to [E48](../evidence/e048.md),
   after the owner's gate classification.

Evidence identifiers for any of these come from the coordinator
([evidence catalog](../evidence/README.md)).

## Owner decisions, in priority order

1. Whether to open an SDK camera-input lane, and when relative to lane F.
2. The passthrough rule. Approve Phase A and Phase B separately, define what
   counts as disturbance, and accept that any disturbance stops the track.
3. Gate classification:
   - whether the no-pixel probe modes sit outside `LIVE_IMAGE_INPUT`, like the
     E48 mirror diagnostics;
   - for bounded recognition without display, whether to add an E48-style
     exception or to lift the gate on new acceptance rows.
4. The presence source for section 3.2. Candidates are SDK standby behaviour
   (measured in Phase B), an OpenVR activity or proximity signal (unverified on
   the Frame), or a bridge line after lane F. Also decide whether
   operator-attended bounded runs without a presence signal are acceptable.
5. Consent. Decide which data classes (counts, labels, boxes, exposure and
   gain, stills) may be saved locally, and whether any may reach the public
   evidence pages. Also whether Arcturus-app recordings used for rehearsal
   count as Camera Data.
6. Camera-activity indication: leave the red LED untouched, or approve a
   restore-previous LED mode, which is a camera control change.
7. Whether frozen capture-track import code may be adapted for an SDK-linked
   build. Separately, the long-term status of the private camera routes (the
   capture track, the shared-memory reader) and of mirror input under
   section 3.
8. Toolchain: build the helper with the SteamOS host compiler, as the bridge
   is built, or inside the vendor's Steam Runtime 4 image.

## Questions only the device or the vendor can answer

- **Buffers:** the default pixel format and modifier (linear or UBWC), the
  sizes, and whether the resolution mode changes them.
- **Exposure clock:** which clock domain `exposureTimestampNs` uses
  (monotonic, boot time or another), and how it relates to `timestampSeconds`.
- **Passthrough:** whether activating the reader disturbs it, and whether only
  the `recording` flag triggers exclusive ownership.
- **Standby and presence:** whether frames stop on headset removal or standby,
  and whether a resume changes the format or resets the sequence.
- **Payload schemas:** the contents of the offline calibration JSON and the
  auxiliary JSON.
- **Import lifetime:** whether holding a Vulkan import of a buffer after its
  frame set is released is permitted.
- **Signals:** whether the Client Library installs signal handlers or timers
  that conflict with a helper watchdog.
