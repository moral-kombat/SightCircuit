"""The camera helper source: ``sightcircuit-camera-helper --export-frames`` under the supervision primitive.

``run_export`` launches the helper binary the operator names (never searched
for; a path inside the work tree is refused unless it is under the ignored
``artifacts/`` directory, where ``scripts/build_camera_helper.sh`` writes, so
no tracked file can be run as the helper) through
``runtime.supervision.supervise_owned``: the one owned session, deadline,
signal handling, group termination and cleanup verdict every bounded child
of this checkout gets. The helper's stdout is the camera export v1 packet
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
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from ..camera import contract
from ..camera.export_reader import ExportReader, ExportRefused
from ..errors import SightCircuitError
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

    def __init__(self, fd, limits, on_frame, stop):
        super().__init__(name="camera-helper-frames", daemon=True)
        self.fd, self.limits, self.on_frame, self.stop = fd, limits, on_frame, stop
        self.reader = ExportReader(fd, limits=limits)
        self.frames = 0
        self.error = None
        self.first_frame_monotonic = None

    def run(self):
        try:
            for frame in self.reader.frames():
                self.frames += 1
                if self.first_frame_monotonic is None:
                    self.first_frame_monotonic = time.monotonic()
                self.on_frame(frame)
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
        report.update(frames_delivered=self.frames, handler_error=self.error, finished=not self.is_alive())
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


def run_export(binary, limits, cameras, *, on_frame, stop=None, extra_args=(), timeout=None, confirmed=False):
    """Export frames from the helper at ``binary`` until its bound; return the source report.

    ``limits`` is ``{fps, max_seconds, max_frames}``, passed to the helper and
    required back in its ``ready`` packet; ``cameras`` the camera indices to
    export. ``on_frame(frame)`` receives every validated ``Frame`` on the
    reader thread and must not block for long; an exception from it stops the
    helper and ends the run ``error``. ``stop``, when given, is a
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
    whatever the outcome.
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
    report.update(helper={"path": path, "sha256": helper_digest(path), "arguments": command[1:],
                          "parent_confirmed": bool(confirmed)},
                  limits=limits, cameras=list(cameras), status="launching")
    stop = threading.Event() if stop is None else stop
    frames_read, frames_write = os.pipe()
    diagnostics_read, diagnostics_write = os.pipe()
    reader = FrameReader(frames_read, limits, on_frame, stop)
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
