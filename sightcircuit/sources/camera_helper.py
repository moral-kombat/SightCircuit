"""The camera helper source: ``sightcircuit-camera-helper --export-frames`` under the supervision primitive.

``run_export`` launches the helper binary the operator names (never searched
for; a path inside the work tree is refused unless it is under the ignored
``artifacts/`` directory, where ``scripts/build_camera_helper.sh`` writes, so
no tracked file can be run as the helper) through
``runtime.supervision.supervise_owned``: the one owned session, deadline,
signal handling, group termination and cleanup verdict every bounded child
of this checkout gets. The helper's stdout is the camera export v2 packet
stream, so it cannot be the primitive's report stream: the launched command
is ``exec_helper``, a trampoline of this module that writes one launch record
on the report stream, moves two pipes of the caller onto its stdout and
stderr and ``execv``s the helper. The frames pipe is read on a thread by the
strict ``ExportReader``; each frame reaches ``on_frame`` on that thread. The
diagnostics pipe (the helper's stderr, JSON lines only) is collected on a
second thread into the report, bounded, never printed. The helper stops
cleanly on its own bound (it is given the same limits), on the first reader
refusal (the reader sets the primitive's ``stop``; the helper is sent SIGTERM
and collects it between SDK calls), on the deadline, and on Ctrl+C or SIGTERM
(the primitive's cancellation). The report carries headers, counts and the
primitive's block, never a pixel. Nothing here lifts a gate.

``CameraHelperSource`` (work-order item L5) wraps ``run_export`` as a frame
source for ``Pipeline``: the left camera only, kind ``arcturus_camera``. No
command uses it yet and ``run_pipeline`` refuses it.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from ..camera import contract
from ..camera.export_reader import ExportReader, ExportRefused, frame_of
from ..errors import SightCircuitError
from ..pipeline import PERCENTILE_METHOD, nearest_rank
from ..runtime import guard
from ..runtime.supervision import (ARMED_FD_VARIABLE, COOPERATIVE_GRACE_VARIABLE, SUPERVISOR_PID_VARIABLE, Grace,
                                   supervise_owned)

HELPER_GRACE = Grace(arm=1.0, cooperative=3.0, terminate=1.0, kill=1.0, report=0.5)
LAUNCH_MARGIN_SECONDS = 10  # Connect, handshake and the end packet, beyond the helper's own max_seconds.
JOIN_SECONDS = 5.0  # For the reader and diagnostics threads after the helper is gone.
MAX_DIAGNOSTIC_LINES = 256
MAX_DIAGNOSTIC_LINE_BYTES = 8192
MAX_HELPER_BYTES = 256 * 1024 * 1024  # Largest binary hashed for the report.
EXEC_FAILED_EXIT = 127
TREE_EXCEPTION = "artifacts"  # The one ignored directory of the checkout a helper may live in.
EXPORT_FLAG = "--export-frames"
EXEC_RECORD = {"report_kind": "camera_helper_exec", "schema_version": 1, "status": "helper_exited",
               "cleanup_ok": True}  # The helper owns nothing that outlives it; the primitive verifies the tree.
EXEC_BOOTSTRAP = ("import sys; sys.path.insert(0, sys.argv[1]); "
                  "from sightcircuit.sources.camera_helper import exec_helper; exec_helper(sys.argv[2:])")
EXIT_BEFORE_END = ("stream_ended_without_ready", "stream_ended_without_end")  # The helper left; see finish_report.
SUPERVISION_FIELDS = ("status", "exit_code", "cleanup_ok", "reason", "worker_pid", "worker_armed", "worker_reaped",
                      "worker_exit_code", "worker_tree_reaped", "report_reader_stopped", "worker_forced_termination",
                      "worker_termination_requested", "supervisor_elapsed_seconds")


def exec_helper(argv):
    """In the launched process: announce the launch on the report stream, redirect, then become the helper.

    ``argv`` is ``frames_fd, diagnostics_fd, helper, *arguments``. The record
    on stdout is the primitive's worker report; stdout then becomes the frames
    pipe and stderr the diagnostics pipe, the supervisor's descriptors and
    variables are dropped so the helper inherits none of them, and ``execv``
    replaces this process. A failed exec writes one JSON line to the
    diagnostics pipe and exits ``EXEC_FAILED_EXIT``.
    """
    frames_fd, diagnostics_fd, helper, arguments = int(argv[0]), int(argv[1]), argv[2], list(argv[3:])
    sys.stdout.write(json.dumps(dict(EXEC_RECORD, helper=helper)) + "\n")
    sys.stdout.flush()
    armed = os.environ.pop(ARMED_FD_VARIABLE, None)
    if armed is not None and armed.isdigit():
        try:
            os.close(int(armed))
        except OSError:
            pass
    for name in (COOPERATIVE_GRACE_VARIABLE, SUPERVISOR_PID_VARIABLE):
        os.environ.pop(name, None)
    os.dup2(frames_fd, 1)
    os.dup2(diagnostics_fd, 2)
    os.close(frames_fd)
    os.close(diagnostics_fd)
    try:
        os.execv(helper, [helper, *arguments])
    except OSError as error:
        os.write(2, json.dumps({"event": "exec_failed", "helper": helper, "error": str(error)}).encode() + b"\n")
        os._exit(EXEC_FAILED_EXIT)


def helper_path(path):
    """The resolved helper binary, or ``SightCircuitError``.

    It must be an existing regular file with execute permission, given
    explicitly (``~`` expands; nothing is searched). A path that resolves
    inside this checkout is accepted only under ``artifacts/``, which git
    ignores, so a tracked file can never be launched as the helper.
    """
    if not isinstance(path, (str, Path)) or not str(path):
        raise SightCircuitError("helper_path_missing", "The helper binary path is required")
    resolved = Path(os.path.realpath(os.path.expanduser(str(path))))
    if not resolved.is_file():
        raise SightCircuitError("helper_not_found", f"{resolved} is not a file")
    root = Path(os.path.realpath(guard.ROOT))
    if resolved.is_relative_to(root) and not resolved.is_relative_to(root / TREE_EXCEPTION):
        raise SightCircuitError("helper_inside_work_tree",
                                f"{resolved} is inside the checkout; only {TREE_EXCEPTION}/ may hold a helper")
    if not os.access(resolved, os.X_OK):
        raise SightCircuitError("helper_not_executable", f"{resolved} is not executable")
    return str(resolved)


def helper_digest(path):
    """SHA-256 of the helper binary, or ``None`` when it exceeds ``MAX_HELPER_BYTES``."""
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                return digest.hexdigest()
            total += len(chunk)
            if total > MAX_HELPER_BYTES:
                return None
            digest.update(chunk)


def check_limits(limits):
    """``limits`` as ``{fps, max_seconds, max_frames}`` of integers within ``contract.LIMITS``, else ``ValueError``."""
    if not isinstance(limits, dict) or set(limits) != set(contract.LIMITS):
        raise ValueError("camera_limits_invalid")
    for name, (low, high) in contract.LIMITS.items():
        if type(limits[name]) is not int or not low <= limits[name] <= high:
            raise ValueError(f"camera_limit_out_of_range:{name}")
    return {name: limits[name] for name in contract.LIMITS}


def check_cameras(cameras):
    """``cameras`` as a non-empty ascending tuple of distinct contract camera indices, else ``ValueError``."""
    selected = tuple(cameras)
    if (not selected or len(set(selected)) != len(selected) or list(selected) != sorted(selected) or
            any(type(index) is not int or index not in contract.CAMERA_INDICES for index in selected)):
        raise ValueError("camera_selection_invalid")
    return selected


def helper_command(path, limits, cameras, extra=(), *, confirmed=False):
    """The helper's argument vector: ``--export-frames`` with the limits and the camera selection, then the
    parent's confirmation flag when the operator confirmed on the parent's terminal (``contract.PARENT_CONFIRMED_FLAG``)."""
    return [path, EXPORT_FLAG, "--fps", str(limits["fps"]), "--max-seconds", str(limits["max_seconds"]),
            "--max-frames", str(limits["max_frames"]), "--cameras", ",".join(str(index) for index in cameras),
            *([contract.PARENT_CONFIRMED_FLAG] if confirmed else []), *[str(item) for item in extra]]


class Diagnostics(threading.Thread):
    """Collects the helper's stderr: JSON object lines, bounded in number and size, never printed."""

    def __init__(self, fd):
        super().__init__(name="camera-helper-diagnostics", daemon=True)
        self.fd = fd
        self.lines = []
        self.bytes = 0
        self.dropped_lines = self.non_json_lines = self.oversize_lines = 0
        self.error = None

    def run(self):
        pending = bytearray()
        discarding = False  # Inside an unterminated line already counted oversize: its tail is the same line.
        try:
            while True:
                try:
                    chunk = os.read(self.fd, 65536)
                except InterruptedError:
                    continue
                if not chunk:
                    break
                self.bytes += len(chunk)
                pending.extend(chunk)
                while True:
                    line, newline, rest = pending.partition(b"\n")
                    if not newline:
                        if len(pending) > MAX_DIAGNOSTIC_LINE_BYTES and not discarding:  # Counted once; the rest is dropped.
                            self.oversize_lines += 1
                            discarding = True
                        if discarding:
                            pending = bytearray()
                        break
                    if discarding:
                        discarding = False  # The oversize line ends here; it was counted when it overflowed.
                    else:
                        self.line(bytes(line))
                    pending = bytearray(rest)
            if pending and not discarding:
                self.line(bytes(pending))
        except OSError as error:
            self.error = str(error)[:256]
        finally:
            os.close(self.fd)

    def line(self, line):
        if len(line) > MAX_DIAGNOSTIC_LINE_BYTES:
            self.oversize_lines += 1
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            message = None
        if not isinstance(message, dict):
            self.non_json_lines += 1
        elif len(self.lines) < MAX_DIAGNOSTIC_LINES:
            self.lines.append(message)
        else:
            self.dropped_lines += 1

    def report(self):
        return {"lines": list(self.lines), "line_count": len(self.lines), "dropped_lines": self.dropped_lines,
                "non_json_lines": self.non_json_lines, "oversize_lines": self.oversize_lines, "bytes": self.bytes,
                "read_error": self.error, "finished": not self.is_alive()}


class FrameReader(threading.Thread):
    """Reads the frames pipe with ``ExportReader``; a refusal sets ``stop`` and the pipe is drained until the helper is gone."""

    def __init__(self, fd, limits, on_frame, stop, on_end=None, on_ready=None):
        super().__init__(name="camera-helper-frames", daemon=True)
        self.fd, self.limits, self.on_frame, self.stop = fd, limits, on_frame, stop
        self.on_end, self.on_ready = on_end, on_ready
        self.reader = ExportReader(fd, limits=limits)
        self.frames = 0
        self.error = None
        self.first_frame_monotonic = None
        self.end_received_monotonic_ns = None  # When the helper's end record was read (host monotonic clock).

    def run(self):
        try:
            # Packet by packet, so ``ready`` and ``end`` are acted on when they are accepted: the reader sees the end
            # of the stream only once every writer of the pipe is closed, which is after the helper has exited.
            for packet in self.reader.packets():
                if packet.kind == contract.KIND_FRAME:
                    self.frames += 1
                    if self.first_frame_monotonic is None:
                        self.first_frame_monotonic = time.monotonic()
                    self.on_frame(frame_of(packet, self.reader.ready))
                elif packet.kind == contract.KIND_READY:
                    if self.on_ready is not None:
                        self.on_ready(packet.header)
                else:
                    self.end_received_monotonic_ns = time.monotonic_ns()
                    if self.on_end is not None:
                        self.on_end(packet.header)
        except ExportRefused:
            self.stop.set()
        except Exception as error:  # The frame handler failed: the helper is stopped and the failure reported.
            self.error = str(error)[:1024]
            self.stop.set()
        finally:
            self.drain()

    def drain(self):
        """Read and discard until the helper's end of the pipe is closed, so a stopped helper never blocks on a write."""
        try:
            while True:
                try:
                    if not os.read(self.fd, 1 << 20):
                        break
                except InterruptedError:
                    continue
        except OSError:
            pass
        finally:
            os.close(self.fd)

    def report(self):
        report = self.reader.report()
        report.update(frames_delivered=self.frames, handler_error=self.error, finished=not self.is_alive(),
                      end_received_monotonic_ns=self.end_received_monotonic_ns)
        return report


def new_export_report():
    return {"report_kind": "camera_helper_export", "schema_version": 1, "protocol": contract.PROTOCOL,
            "status": "not_started", "cleanup_ok": True, "images_saved": False, "pixels_in_report": False,
            "frames_read": 0, "helper": None, "supervision": None, "stream": None, "diagnostics": None,
            "scope": ("Frames exported by sightcircuit-camera-helper over its stdout; the helper's camera identity, "
                      "clocks and pixel path are what its headers declare and are not verified here")}


def _validate_launch(candidate, returncode):
    """The primitive's report validator: the trampoline's launch record, carrying the helper's exit code."""
    if not isinstance(candidate, dict) or candidate.get("report_kind") != EXEC_RECORD["report_kind"]:
        raise ValueError("invalid_helper_launch_record")
    candidate["exit_code"] = returncode
    candidate["helper_exit_code"] = returncode


def run_export(binary, limits, cameras, *, on_frame, stop=None, extra_args=(), timeout=None, confirmed=False,
               on_end=None, on_ready=None, expected_sha256=None):
    """Export frames from the helper at ``binary`` until its bound; return the source report.

    ``limits`` is ``{fps, max_seconds, max_frames}``, passed to the helper and
    required back in its ``ready`` packet; ``cameras`` the camera indices to
    export. ``on_frame(frame)`` receives every validated ``Frame`` on the
    reader thread and must not block for long; an exception from it stops the
    helper and ends the run ``error``. ``on_ready(header)`` and
    ``on_end(header)``, when given, receive the validated ``ready`` and ``end``
    headers on the same thread as each packet is accepted, so ``on_end`` runs
    while the helper may still be alive; they are held to the same rule (a
    packet after ``end`` is still refused afterwards). ``stop``, when given, is a
    ``threading.Event`` the caller may set to end the helper early
    (``stopped``). ``confirmed`` says the operator typed the confirmation on
    this process's terminal, which the helper is told with
    ``--parent-confirmed``; without it the helper refuses the export.
    ``extra_args`` follow the contract arguments (a fake
    build's ``--fake-scenario``). The deadline is ``timeout`` or
    ``max_seconds`` + ``LAUNCH_MARGIN_SECONDS``. Statuses: ``exported``,
    ``no_frames``, ``refused`` (the reader's reason), ``helper_ended_early``
    (the helper's end reason), ``cancelled``, ``timed_out``, ``stopped`` and
    ``error``. The helper's exit code and the primitive's block are reported
    whatever the outcome. ``expected_sha256``, when given, is the digest the
    helper must have (the checkpoint manifest's, for a run under a scope
    record): the binary is hashed here, right before the launch, and a
    different digest ends the run ``error``/``helper_digest_mismatch`` with
    nothing launched.
    """
    report = new_export_report()
    try:
        path = helper_path(binary)
        limits = check_limits(limits)
        cameras = check_cameras(cameras)
    except (SightCircuitError, ValueError) as error:
        report.update(status="error", reason=getattr(error, "reason", str(error)), error=str(error))
        return report
    command = helper_command(path, limits, cameras, extra_args, confirmed=confirmed)
    digest = helper_digest(path)
    report.update(helper={"path": path, "sha256": digest, "arguments": command[1:],
                          "parent_confirmed": bool(confirmed)},
                  limits=limits, cameras=list(cameras), status="launching")
    if expected_sha256 is not None and digest != expected_sha256:
        report.update(status="error", reason="helper_digest_mismatch",
                      error="The helper binary is not the one the checkpoint manifest recorded")
        return report
    stop = threading.Event() if stop is None else stop
    frames_read, frames_write = os.pipe()
    diagnostics_read, diagnostics_write = os.pipe()
    reader = FrameReader(frames_read, limits, on_frame, stop, on_end, on_ready)
    diagnostics = Diagnostics(diagnostics_read)
    reader.start()
    diagnostics.start()
    started = time.monotonic()
    try:
        supervised = supervise_owned(
            [sys.executable, "-I", "-c", EXEC_BOOTSTRAP, str(guard.ROOT), str(frames_write), str(diagnostics_write),
             *command],
            {}, limits["max_seconds"] + LAUNCH_MARGIN_SECONDS if timeout is None else timeout, _validate_launch,
            pass_fds=(frames_write, diagnostics_write), grace=HELPER_GRACE, stop=stop)
    finally:
        os.close(frames_write)
        os.close(diagnostics_write)
        reader.join(JOIN_SECONDS)
        diagnostics.join(JOIN_SECONDS)
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return finish_report(report, supervised, reader, diagnostics)


def finish_report(report, supervised, reader, diagnostics):
    """Compose the source report from the primitive's block, the reader's and the diagnostics'."""
    stream, logged = reader.report(), diagnostics.report()
    outcome = supervised["status"]
    report.update(supervision={name: supervised.get(name) for name in SUPERVISION_FIELDS}, stream=stream,
                  diagnostics=logged, frames_read=stream["frames_read"], helper_exit_code=supervised.get("helper_exit_code"),
                  cleanup_ok=bool(supervised.get("cleanup_ok")) and stream["finished"] and logged["finished"])
    exit_code = supervised.get("helper_exit_code")
    refusal = stream["refusal"]
    truncated = refusal is not None and refusal["reason"] in EXIT_BEFORE_END  # The helper left mid-stream.
    if not stream["finished"] or not logged["finished"]:
        report.update(status="error", reason="helper_streams_still_open", cleanup_ok=False)
    elif stream["handler_error"] is not None:
        report.update(status="error", reason="frame_handler_failed", error=stream["handler_error"])
    elif outcome == "cancelled":  # A stream the stop cut short is the cancellation's consequence.
        report.update(status="cancelled", reason="signal", signal=supervised["exit_code"] - 128)
    elif outcome == "timed_out":
        report.update(status="timed_out", reason="helper_deadline")
    elif outcome == "stopped" and (refusal is None or truncated):
        report.update(status="stopped", reason="stop_requested")
    elif truncated and outcome == EXEC_RECORD["status"] and exit_code != 0:
        # The helper left before finishing its stream and said so with its exit code: its failure, not the stream's.
        report.update(status="error", reason="helper_exit_code",
                      error=f"the helper exited {exit_code} ({refusal['reason']})")
    elif refusal is not None:
        report.update(status="refused", reason=refusal["reason"], error=refusal["detail"])
    elif outcome != EXEC_RECORD["status"]:
        report.update(status="error", reason=supervised.get("reason") or outcome)
    elif stream["status"] != "complete":
        report.update(status="error", reason="stream_incomplete")
    elif stream["end"]["reason"] not in contract.BOUNDED_END_REASONS:
        report.update(status="helper_ended_early", reason=stream["end"]["reason"])
    elif exit_code != 0:
        report.update(status="error", reason="helper_exit_code", error=f"the helper exited {exit_code}")
    else:
        report["status"] = "exported" if stream["frames_read"] else "no_frames"
    return report


# ---- the pipeline frame source (work-order item L5) --------------------------------------------------------------

SOURCE_KIND = "arcturus_camera"
LEFT_CAMERA = 0  # ``contract.SOURCE_IDS[0]``, reported by the helper as the left camera.
# The helper's end reasons, by what they mean for a pipeline run.
COMPLETED_END_REASONS = ("max_frames", "max_seconds")  # The run's own bound: the source finishes cleanly.
PRESENCE_END_REASONS = ("headset_removed", "standby")  # The wearer is gone: ``presence_ended``, never retried.
STOP_END_REASON = "stop_signal"  # The pipeline's stop (or a signal) reached the helper: a clean stop.
ATTACH_SECONDS = 5.0  # How long each half waits for the other before giving up with nothing launched.
PIPELINE_JOIN_SECONDS = 10.0  # ``drive``'s wait for the pipeline thread once the helper is gone.
EXPOSURE_OFFSET = "exposure_minus_acquire_ms"
EXPOSURE_OFFSET_DEFINITION = (
    "Each emitted frame's exposure_ns minus its monotonic_ns (the host clock at acquire), as the helper's headers "
    "declare them, in milliseconds: a timing of this run, verified against nothing and never applied to a frame")
SOURCE_STATUSES = ("not_started", "running", "completed", "stopped", "cancelled", "presence_ended", "error")
# What the source's report keeps of the export report: counts, timings, the helper's identity and format metadata.
# Left out on purpose: the diagnostic lines (the helper's hello record is among them) and the end packet's digests.
EXPORT_FIELDS = ("status", "reason", "error", "frames_read", "helper_exit_code", "elapsed_ms", "cleanup_ok", "signal")
STREAM_FIELDS = ("status", "packets_read", "frames_read", "bytes_read", "frames_per_camera", "dropped_since_last_total",
                 "frames_delivered", "refusal")
READY_FIELDS = ("protocol", "backend", "pixel_path", "delivered_format", "cameras", "limits", "confirmation")
END_FIELDS = ("reason", "counts", "elapsed_ns", "confirmation")
DIAGNOSTIC_FIELDS = ("line_count", "dropped_lines", "non_json_lines", "oversize_lines", "bytes")


def source_outcome(export, stop_requested):
    """``(status, reason, end_reason)`` of a finished export for the pipeline source; see ``CameraHelperSource``.

    ``export`` is ``run_export``'s report. The helper's own end reason, read
    from its validated ``end`` packet, decides first: a bound of the run is
    ``completed``, a presence end is ``presence_ended``, a stop signal is
    ``stopped`` and every other reason is an ``error`` with that reason. With
    no end packet, a stop the caller asked for is ``stopped``, a signal is
    ``cancelled`` and anything else is an ``error`` with the export's reason.
    A presence end under an export that failed otherwise (a refusal after the
    end, the deadline) is an ``error`` whose ``end_reason`` still names it.
    """
    stream = export.get("stream") or {}
    end = stream.get("end")
    status = export.get("status")
    if end is not None and stream.get("refusal") is None and status in ("exported", "no_frames", "stopped",
                                                                         "helper_ended_early", "cancelled"):
        reason = end["reason"]
        if reason in PRESENCE_END_REASONS:
            return "presence_ended", reason, reason
        if status == "cancelled":
            return "cancelled", "signal", reason
        if reason in COMPLETED_END_REASONS:
            return "completed", None, reason
        if reason == STOP_END_REASON:
            return "stopped", "stop_requested" if stop_requested else STOP_END_REASON, reason
        return "error", reason, reason
    end_reason = end["reason"] if end is not None else None
    if status == "cancelled":
        return "cancelled", "signal", end_reason
    if status == "stopped":
        return "stopped", "stop_requested", end_reason
    return "error", export.get("reason") or status or "export_failed", end_reason


class CameraHelperSource:
    """The left Arcturus camera as a ``Pipeline`` frame source, around ``run_export``.

    ``kind`` is ``arcturus_camera`` and ``source_id`` ``arcturus_camera_left``:
    ``CaptureContext`` pins both before the helper is launched, and the source
    accepts nothing else (``cameras`` other than ``(0,)`` is refused when the
    source is built; a ``ready`` naming other cameras is refused when it is
    accepted, before any frame, and a frame of another camera ends the run as
    an error). It approves nothing: ``run_pipeline`` refuses it
    (``live_image_source_not_approved``) and no gate knows it.

    Two threads drive it, because the supervision primitive takes signals and
    so needs the main thread, while a latest-only ``Pipeline`` calls
    ``run(emit, stop)`` on its capture thread:

    * ``run(emit, stop)`` (the pipeline's capture thread) attaches the
      pipeline's ``emit`` and ``stop`` and returns when the export has finished
      or ``stop`` is set, whichever is first, so the pipeline clears its sinks
      at the stop and never waits for the helper's teardown. It raises a
      failure already known by then (``camera_source_not_supervised`` when
      nothing supervises in time).
    * ``supervise()`` (the main thread) waits for that attachment, then runs
      ``run_export`` with the pipeline's ``stop`` as the helper's stop signal
      and returns ``report()`` once the helper is gone. Frames go from the
      reader thread straight to ``emit``; a frame that arrives after ``stop``
      is counted (``frames_after_stop``) and not emitted.
    * ``drive(pipeline, stop)`` does both, the pipeline on a worker thread and
      the supervision on the calling one, and raises the run's error after
      both have finished: the pipeline's, else the source's. With the two
      halves used separately, the caller reads the status from ``supervise()``.

    How a run ends (``status``): the helper's own bound (``max_frames``,
    ``max_seconds``) is ``completed``; a stop the pipeline asked for is
    ``stopped``; ``headset_removed`` and ``standby`` are ``presence_ended``:
    when the reader accepts that end packet, while the helper may still be
    alive, the source drops the pipeline's pending frame (``drive``) and sets
    ``stop``, so no further frame is taken and a result still in flight
    reaches no sink. It is never retried, as nothing is (a source runs once; a
    second ``run`` or ``supervise`` raises ``camera_source_already_run``
    without launching), and ``report()['presence_ended']`` follows the end
    reason alone, whatever else then goes wrong. Everything else is an
    ``error`` carrying the helper's reason (its end reason, the reader's
    refusal, or the export's), as ``SightCircuitError``. A signal taken by the
    primitive is ``cancelled``.

    Cleanup: ``cleanup_ok`` is the export's verdict and, after a presence end,
    also the helper's own: the helper exits non-zero after ``headset_removed``
    or ``standby`` exactly when its cleanup failed, so that run stays
    ``presence_ended`` (and is not raised) with ``cleanup_ok`` false,
    ``helper_exit_ok`` false and ``helper_exit_code`` in the report. A caller
    treats a false ``cleanup_ok`` as a failed run whatever the status.

    Clocks: a frame's ``observed_ns`` stays the host monotonic time of its
    acquisition and ``exposure_ns`` stays in its metadata. Their difference is
    reported per run as the timing ``exposure_minus_acquire_ms`` (count, p50,
    min, max); it is not a correction and nothing uses it. The report holds
    counts, timings, the helper's identity and format metadata: no pixel, no
    diagnostic line and no digest of the end packet.
    """
    kind = SOURCE_KIND

    def __init__(self, binary, limits, *, cameras=(LEFT_CAMERA,), confirmed=False, extra_args=(), timeout=None,
                 attach_seconds=ATTACH_SECONDS):
        if check_cameras(cameras) != (LEFT_CAMERA,):
            raise SightCircuitError("camera_source_left_only", "The camera helper source reads the left camera (index 0) only")
        self.source_id = contract.source_id(LEFT_CAMERA)
        self.binary, self.limits = binary, check_limits(limits)
        self.confirmed, self.extra_args, self.timeout = bool(confirmed), tuple(extra_args), timeout
        self.attach_seconds = attach_seconds
        self.expected_sha256 = None  # The manifest's digest of the helper, set by a run under a scope record.
        self.status, self.reason, self.end_reason, self.error = "not_started", None, None, None
        self.cleanup_ok = None  # The export's verdict (and the helper's, after a presence end); True when nothing was launched.
        self.helper_exit_code = self.helper_exit_ok = None
        self.frames_emitted = self.frames_refused = self.frames_after_stop = 0
        self.offsets_ms = []  # One value per emitted frame; at most ``contract.LIMITS['max_frames']``.
        self.export = None
        self._lock = threading.Lock()
        self._run_called = self._supervise_called = False
        self._attached, self._finished = threading.Event(), threading.Event()
        self._abandoned = None  # ``drive``'s signal that the pipeline ended before it attached.
        self._pipeline = None  # ``drive``'s pipeline, whose pending frame a presence end drops.
        self._emit = self._stop = self._failure = None

    # -- the pipeline's half -----------------------------------------------------------------------------------

    def run(self, emit, stop):
        """Attach the pipeline; return when the export has finished or ``stop`` is set. Capture thread."""
        with self._lock:
            if self._run_called:
                raise SightCircuitError("camera_source_already_run", "A camera helper source runs once and is never restarted")
            self._run_called = True
        self._emit, self._stop = emit, stop
        self._attached.set()
        deadline = time.monotonic() + self.attach_seconds
        while not self._finished.wait(0.02) and not stop.is_set():
            if not self._supervise_called and time.monotonic() >= deadline:
                self._finish("error", "camera_source_not_supervised", "Nothing called supervise() on the main thread",
                             cleanup_ok=True)
                break
        if self._failure is not None:
            raise self._failure

    # -- the supervising half ----------------------------------------------------------------------------------

    def supervise(self):
        """Launch and supervise the helper once the pipeline is attached; return ``report()``. Main thread."""
        with self._lock:
            if self._supervise_called:
                raise SightCircuitError("camera_source_already_run", "A camera helper source runs once and is never restarted")
            self._supervise_called = True
        try:
            if not self._wait_attached():
                self._finish("error", "camera_source_not_attached", "No pipeline called run() on the source", cleanup_ok=True)
            elif self._stop.is_set():
                self._finish("stopped", "stop_requested", None, cleanup_ok=True)
            else:
                self.status = "running"
                self.export = run_export(self.binary, self.limits, (LEFT_CAMERA,), on_frame=self._on_frame,
                                         on_end=self._on_end, on_ready=self._on_ready, stop=self._stop,
                                         extra_args=self.extra_args, timeout=self.timeout, confirmed=self.confirmed,
                                         expected_sha256=self.expected_sha256)
                self._conclude()
        except BaseException as error:
            self._finish("error", getattr(error, "reason", "camera_source_failed"), str(error)[:1024], cleanup_ok=False,
                         failure=error)
            raise
        finally:
            if self._stop is not None and self.status != "completed":
                self._stop.set()  # Whatever ended the export early ends the pipeline run with it.
            self._finished.set()
        return self.report()

    def drive(self, pipeline, stop):
        """Run ``pipeline`` (built on this source) on a worker thread and ``supervise`` here; return its statistics.

        Call it on the main thread. After both halves have finished it raises
        the pipeline's exception, else the source's failure (a reader refusal,
        a helper error), whatever the pipeline's ``join_timeout``. A presence
        end and a stop are not failures; read ``report()`` for ``cleanup_ok``.
        """
        if pipeline.source is not self or not pipeline.latest_only:
            raise ValueError("camera_source_pipeline_mismatch")
        outcome = {}
        self._abandoned = threading.Event()
        self._pipeline = pipeline

        def work():
            try:
                outcome["stats"] = pipeline.run(stop)
            except BaseException as error:
                outcome["error"] = error
            finally:
                self._abandoned.set()

        worker = threading.Thread(target=work, name="camera-source-pipeline", daemon=True)
        worker.start()
        try:
            self.supervise()
        except BaseException:
            stop.set()
            worker.join(PIPELINE_JOIN_SECONDS)
            raise
        worker.join(PIPELINE_JOIN_SECONDS)  # A completed export lets the pipeline finish its last frame by itself.
        if worker.is_alive():
            stop.set()
            worker.join(PIPELINE_JOIN_SECONDS)
        if worker.is_alive():
            raise SightCircuitError("camera_source_pipeline_still_running", "The pipeline did not finish after the helper")
        if "error" in outcome:
            raise outcome["error"]
        if self._failure is not None:
            raise self._failure
        return outcome["stats"]

    def _wait_attached(self):
        deadline = time.monotonic() + self.attach_seconds
        while not self._attached.wait(0.01):
            if time.monotonic() >= deadline or (self._abandoned is not None and self._abandoned.is_set()):
                return self._attached.is_set()
        return True

    # -- the reader thread -------------------------------------------------------------------------------------

    def _refuse(self, error):
        self._failure = error
        raise error

    def _on_ready(self, ready):
        if ready["cameras"] != [LEFT_CAMERA]:  # Before any frame: the helper is not exporting what was asked for.
            self._refuse(SightCircuitError("camera_selection_not_honoured", "The helper's ready names other cameras"))

    def _on_frame(self, frame):
        if frame.source != self.source_id or frame.metadata.get("camera_index") != LEFT_CAMERA:
            self.frames_refused += 1
            self._refuse(SightCircuitError("camera_frame_not_left", "The helper exported a frame of another camera"))
        if self._stop.is_set():  # The run has stopped; the helper has not noticed yet.
            self.frames_after_stop += 1
            return
        try:
            self._emit(frame)
        except BaseException as error:
            self._failure = error
            raise
        self.frames_emitted += 1
        self.offsets_ms.append((frame.metadata["exposure_ns"] - frame.observed_ns) / 1e6)

    def _on_end(self, end):
        """The end packet as the reader accepts it; the helper may still be alive."""
        self.end_reason = end["reason"]
        if end["reason"] in PRESENCE_END_REASONS:
            self.status, self.reason = "presence_ended", end["reason"]
            if self._pipeline is not None:
                self._pipeline.discard_pending()  # The frame waiting to be processed is dropped now.
            self._stop.set()  # No frame is taken after the wearer is gone, and a result in flight reaches no sink.

    # -- the outcome -------------------------------------------------------------------------------------------

    def _conclude(self):
        export = self.export
        status, reason, end_reason = source_outcome(export, self._stop.is_set())
        cleanup_ok = bool(export.get("cleanup_ok"))
        self.helper_exit_code = export.get("helper_exit_code")
        error = None
        if self._failure is not None:  # The pipeline's admission, the ready check or the left-camera check refused.
            status, reason = "error", getattr(self._failure, "reason", None) or str(self._failure)[:256]
            error = str(self._failure)[:1024]
        elif status == "error":
            error = export.get("error") or f"The camera helper export ended {export.get('status')} ({reason})"
        elif status == "presence_ended":
            # The helper exits non-zero after a presence end exactly when its own cleanup failed.
            self.helper_exit_ok = self.helper_exit_code == 0
            if not self.helper_exit_ok:
                cleanup_ok = False
                error = f"The helper exited {self.helper_exit_code} after {end_reason}: its own cleanup is not confirmed"
        failure = self._failure
        if status == "error" and failure is None:
            failure = SightCircuitError(reason, error)
        self._finish(status, reason, error, cleanup_ok=cleanup_ok, end_reason=end_reason, failure=failure)

    def _finish(self, status, reason, error, *, cleanup_ok, end_reason=None, failure=None):
        self.status, self.reason, self.error, self.cleanup_ok = status, reason, error, cleanup_ok
        self.end_reason = end_reason if end_reason is not None else self.end_reason
        if status == "error" and self._failure is None:
            self._failure = failure if failure is not None else SightCircuitError(reason, error or reason)

    def report(self):
        """The source's report: status, counts, the exposure offset timing and the export's counts; never a pixel."""
        ordered = sorted(self.offsets_ms)

        def ms(value):
            return None if value is None else round(value, 3)
        report = {"report_kind": "camera_helper_source", "schema_version": 1, "kind": self.kind,
                  "source_id": self.source_id, "camera_index": LEFT_CAMERA, "protocol": contract.PROTOCOL,
                  "status": self.status, "reason": self.reason, "end_reason": self.end_reason, "error": self.error,
                  # From the end reason alone: a presence end is never retried, whatever else went wrong after it.
                  "presence_ended": self.end_reason in PRESENCE_END_REASONS, "retried": False,
                  "limits": dict(self.limits), "frames_emitted": self.frames_emitted,
                  "frames_refused": self.frames_refused, "frames_after_stop": self.frames_after_stop,
                  "cleanup_ok": self.cleanup_ok, "helper_exit_code": self.helper_exit_code,
                  "helper_exit_ok": self.helper_exit_ok, "images_saved": False, "pixels_in_report": False,
                  "observed_ns": "host monotonic clock at acquire (the frame header's monotonic_ns)",
                  "timings": {EXPOSURE_OFFSET: {"count": len(ordered), "p50": ms(nearest_rank(ordered, 50)),
                                                "min": ms(ordered[0] if ordered else None),
                                                "max": ms(ordered[-1] if ordered else None),
                                                "percentile_method": PERCENTILE_METHOD,
                                                "definition": EXPOSURE_OFFSET_DEFINITION}},
                  "export": None}
        export = self.export
        if export is not None:
            stream, logged, helper = export.get("stream") or {}, export.get("diagnostics") or {}, export.get("helper")
            ready, end = stream.get("ready"), stream.get("end")
            report["export"] = {
                **{name: export.get(name) for name in EXPORT_FIELDS},
                "helper": dict(helper) if helper is not None else None,
                "supervision": dict(export["supervision"]) if export.get("supervision") is not None else None,
                "stream": {**{name: stream.get(name) for name in STREAM_FIELDS},
                           "ready": {name: ready[name] for name in READY_FIELDS} if ready is not None else None,
                           "end": {name: end[name] for name in END_FIELDS} if end is not None else None},
                "diagnostics": {name: logged.get(name) for name in DIAGNOSTIC_FIELDS}}
        return report
