# Mirror acquisition and recognition reference

Use [the mirror guide](../guides/mirror-recognition.md) for device commands.
This reference describes source validation, bounded acquisition, output
publication, report fields, and offline regression tests.

## Existing SteamVR mirror diagnostic

`python3 scripts/mirror_check.py` performs sysfs-only discovery. Opt-in capture
uses Python's standard library and the current V4L2 format; no PyAV, OpenCV, Qt,
OpenVR or system package installation is needed. It supports Linux x86_64 and
ARM64 single-plane V4L2 loopback mirrors named `SteamVR` that advertise `read()`.
Format support is intentionally explicit: GREY, NV12/NV21, YU12/YV12,
YUYV/UYVY/YVYU/VYUY and RGB3/BGR3. Other formats/capabilities are reported.

The worker opens the verified node with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`,
uses only `VIDIOC_QUERYCAP` and `VIDIOC_G_FMT`, and calls nonblocking `read()`.
The first read is not gated on `select()`/`poll()`: some v4l2loopback releases
register file-I/O capture inside that call, before readability can be signaled.
The driver can initialize capture internally; no explicit queue or stream-on
ioctl is sent by this tool. Read attempts and would-block/interrupted reads are
reported separately from complete images. A lazy-start backend regression check
exercises first-read registration followed by frame delivery without polling.
No `S_FMT`, buffer-queue setup or explicit stream-on operation is requested.
Raw buffers are bounded at 64 MiB and dimensions at 16 Mi pixels; samples skip
padding and chroma. The supervisor enforces the driver deadline and handles
Ctrl+C/SIGTERM; Linux parent-death signaling stops the worker if its launcher dies.

The launcher is the shared `sightcircuit/sources/mirror.py` `launch`, which mirror
recognition also uses. An unsuitable `--preview` path stops it before the worker
starts. The worker writes the reserved grayscale PNG after capture has closed,
and the launcher links it into place only after a `captured` run with confirmed
cleanup. A clean run that produced no preview fails with `requested_output_missing`.
A destination created during the run is kept, and the run fails with
`preview_publish_failed`.

```bash
python3 -m unittest discover -s tests -p test_mirror_check.py -v
python3 -m unittest tests.test_mirror_launcher tests.test_report_characterization -v
```

With bare Python the second command covers the launcher and the
`mirror_check.py` characterization; its mirror-recognition and synthetic-run
characterization tests skip without NumPy/OpenCV and run under `.venv-replay`
in [bounded mirror recognition](#bounded-mirror-recognition).
Tests use controlled sysfs/device backends and real subprocesses. The ABI test
compiles a tiny Linux-header program when `g++` and UAPI headers are installed;
otherwise that check is explicitly skipped. With PyAV available, preview testing
also decodes the generated PNG. No test opens an actual camera. Read the
[device procedure](../guides/mirror-recognition.md#check-an-existing-steamvr-mirror) before opt-in
acquisition, and [status](../status.md#e8-mirror-diagnostic-cloud-checks) for actual
validation evidence.


## Bounded mirror recognition

`scripts/mirror_recognize.py` reuses the guarded capture loop and supervised
process deadline, and runs recognition through the latest-only
[shared pipeline](pipeline.md#python-package-and-pipeline). Its worker runs
`sightcircuit/runtime/live.py` `run_recognition` on `run_report`, the report
runner the synthetic run also uses, and `sources.mirror.launch` starts it and
publishes its outputs. Default execution performs metadata-only discovery. Opt-in
capture/inference needs the existing OpenCV/NumPy environment and checked model
fixtures, with no Qt, PyAV or OpenVR initialization. The recognizer accepts only
RGB3/BGR3, respects row padding and converts to owned BGR images of at most 960
pixels on the longer side. A capture thread retains one latest pending read;
slow inference replaces old input rather than accumulating a queue.

`--detector` picks a `DETECTORS` entry through `create_detector`; the default
stays `mobilenet-ssd`. The report records `detector_requested` and `detector`
(name, runtime and class count). Two optional outputs come from the last
processed result: `--preview`, the annotated image scaled to at most 640
pixels, and `--still`, the unannotated frame at its native mirror size, for
[evaluation sets](detector-evaluation.md#detector-evaluation). Each refuses an existing path, is
written to an owned temporary file after the source closes, and is hard-linked
into place only after a recognized run with confirmed cleanup
(`sightcircuit/runtime/output.py`). The two need paths that resolve to different
files, and publication is all or nothing: if a later hard link fails, outputs
this run already linked are withdrawn and the report gives
`<kind>_publish_failed`; Ctrl+C or SIGTERM during publication also withdraws
them, and the run is `cancelled`. The still is the native frame, not the
960-pixel detector input that the flag saved before `9b765ee` removed it:
`evaluate_detectors.py` applies the same conversion, so its baseline input
equals what the live detector saw, and its `native-size` audit variant keeps a
meaning. The report records `still_sha256` (of the PNG bytes written, checked
again before publication; a still reported without it is not published),
`still_detector`, `still_source`, `still_sequence`
(equal to `last_result.read_sequence`) and `still_width`/`still_height`.
`scripts/frame_session_manifest.py --still-report <report>` checks that the
image still matches and copies the digest, detector, source and sequence into
the manifest entry, which then passes `--require-input-digests`.

```bash
.venv-replay/bin/python -m unittest discover -s tests -p test_mirror_recognize.py -v
.venv-replay/bin/python -m unittest discover -s tests -p "test_frame_session*.py" -v
.venv-replay/bin/python -m unittest tests.test_report_characterization tests.test_mirror_launcher -v
```

Tests cover guards, channel order/padding, overload and exact source/result
association, old/empty input, model/read/close failures, cancellation and real
spawned-model/preview/still publication with the checked fixture. Image encoding is
checked to occur after source closure. Destinations created concurrently remain
untouched. A fixture mirror larger than 960 pixels and a fixture model drive
`--capture --still` end to end: the still equals the native frame of
`last_result`, the evaluator's conversion of it reproduces the live detector
input, and its manifest entry passes `--require-input-digests`.
`tests/test_frame_session_commands.py` parses every `scripts/*.py` command line
in `scripts/frame_session.sh` with that script's own parser and exact option
names, without running it; the session helper's own tests replace Python with a
fake, so they cannot see a removed flag. Dependency-specific tests skip without OpenCV/NumPy; the replay
CI job installs these and exercises the complete suite. Existing supervision
tests cover hard deadlines, abnormal worker exit and process reaping.

The final report carries at most 20 detections from the last processed input,
local observation/processing timing, captured/processed counts and replaced
pending inputs. One pending read, one in-flight inference and bounded result
storage limit memory; the kernel driver's own buffering is separate. The
supervisor budget includes 15 seconds beyond capture duration for model startup
and completion, then bounded cleanup. File output is opt-in and published only
after capture/inference finishes. Read [the device procedure](../guides/mirror-recognition.md#run-bounded-recognition-from-the-mirror)
before the first Frame trial; [status](../status.md#e16-bounded-mirror-recognition-cloud-checks)
records cloud-only validation and the model-quality limitation.
