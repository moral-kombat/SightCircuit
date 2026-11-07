"""SteamVR mirror acquisition: the bounded read loop, its pipeline source and the one launcher.

``capture`` reads a verified virtual mirror (``mirror_device.NativeMirror`` or
an injected fixture backend) at a bounded rate until its deadline, reporting
progress and passing each complete read to ``on_frame``. ``MirrorSource`` turns
those reads into owned ``Frame``s carrying their ``native_format`` and retains
the acquisition report; a report with status ``error`` raises, so the pipeline
stops presenting results from a failed capture. With a ``session``
(``sources.session.MirrorSession``) it declares the HUD session tokens its
providers observe and stamps each frame with the tokens read for it.

``launch`` is the one launcher of the mirror commands (``mirror_check.py`` and
``mirror_recognize.py``): it selects the verified candidate, reserves each
requested image output (``runtime.output``), runs the command's worker under
the supervision primitive (``runtime.supervision``, through
``supervise_worker``), publishes the outputs all or nothing and always
discards their temporary files. SIGTERM and Ctrl+C cancel it
(``runtime.guard``).

A worker is a module-level function ``worker(job, seconds, outputs, progress,
stop)`` of this checkout that returns its report; ``launch`` names it by module
and function (``supervision.function_reference``) and the primitive starts an
interpreter that imports it and runs it through ``run_worker``: the worker
enters with the parent-death guard (``supervision.enter_worker``), arms its
stop event (``guard.stop_on_signals``), streams the report snapshots it is
given as ``progress`` events and ends with a ``result`` event carrying the
report and the exit code the launcher used to derive (0 for the success
status with cleanup confirmed, else 1), which is also its process exit code.
Its cleanup budgets (``WORKER_GRACE``) are the bounded spawned worker's:
half a second of cooperative stop, then one second each for SIGTERM and
SIGKILL on the group.
"""
import hashlib
import json
import math
import sys
import threading
import time

from ..context import SOURCE_FIELDS
from ..errors import SightCircuitError
from ..frame import Frame
from ..hud.contract import SESSION_TOKENS
from ..runtime import guard, output
from ..runtime.supervision import (  # noqa: F401 - load_function is the worker bootstrap's import
    Grace, emit_event, enter_worker, function_reference, load_function, supervise_owned,
)
from .mirror_device import NativeMirror, grayscale_sample, select_candidate

WORKER_GRACE = Grace(arm=1.0, cooperative=0.5, terminate=1.0, kill=1.0, report=0.5)
# The worker interpreter: imports this checkout, then the named worker, and runs it on the payload.
WORKER_BOOTSTRAP = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
                    "from sightcircuit.sources.mirror import run_worker, load_function; "
                    "sys.exit(run_worker(load_function(json.loads(sys.argv[2])), json.loads(sys.argv[3])))")


def new_capture_report():
    return {"report_kind": "steamvr_mirror_check", "schema_version": 1,
            "status": "not_started", "images_saved": False,
            "device_open_attempted": False, "acquisition_started": False,
            "frames_read": 0, "grayscale_sample_changes": 0,
            "read_attempts": 0, "would_block_reads": 0, "interrupted_reads": 0,
            "reader_method": "nonblocking_read_without_poll_gate",
            "unchanged_sample_reads": 0, "short_reads": 0,
            "maximum_reads_per_second": 10, "cleanup_ok": True,
            "sample_change_proves_live_camera": False,
            "scope": "Composited mirror only; live passthrough, camera identity and image freshness require device observations",
            "physical_camera_identity": "unverified", "camera_timestamps_available": False}


def capture(candidate, seconds, stop, progress=lambda report: None, want_preview=False,
            backend_factory=None, on_frame=None, *, max_fps=10, deadline=None):
    """Acquisition loop; injectable backend permits real lifecycle/failure tests."""
    if not math.isfinite(max_fps) or not 0 < max_fps <= 10 or (deadline is not None and not math.isfinite(deadline)):
        raise ValueError("invalid_capture_pacing_or_deadline")
    report = new_capture_report()
    report.update(status="opening", candidate=candidate, seconds_requested=seconds)
    report["maximum_reads_per_second"] = max_fps
    progress(report.copy())
    backend = None
    last_frame = None
    last_digest = None
    started = last_read = None
    max_gap = 0.0
    try:
        if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
            report["status"] = "cancelled" if stop.is_set() else "deadline_expired"
            return report, None
        backend = (backend_factory or NativeMirror)(candidate)
        report["device_open_attempted"] = True
        backend.open()
        report.update(acquisition_started=True, status="acquiring",
                      capabilities=backend.capabilities, format=backend.format)
        progress(report.copy())
        started = time.monotonic()
        deadline = min(started + seconds, deadline) if deadline is not None else started + seconds
        next_read, next_progress = started, started + 1
        while not stop.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_read:
                stop.wait(min(next_read - now, deadline - now, 0.05))
                continue
            next_read = now + 1 / max_fps
            report["read_attempts"] += 1
            frame = backend.read_frame(min(0.05, deadline - now))
            report.update(getattr(backend, "read_diagnostics", {}))
            if frame is not None:
                if len(frame) < backend.format["minimum_buffer_bytes"]:
                    report["short_reads"] += 1
                    continue
                _, _, sample = grayscale_sample(frame, backend.format, 32)
                digest = hashlib.blake2b(sample, digest_size=16).digest()
                if last_digest is not None:
                    report["grayscale_sample_changes" if digest != last_digest else "unchanged_sample_reads"] += 1
                last_digest = digest
                report["frames_read"] += 1
                observed = time.monotonic()
                if last_read is None:
                    report["first_frame_after_ms"] = round((observed - started) * 1000, 3)
                else:
                    max_gap = max(max_gap, observed - last_read)
                last_read = observed
                if on_frame is not None:
                    on_frame(frame, backend.format, report["frames_read"], time.monotonic_ns())
                if want_preview:
                    last_frame = frame
            if time.monotonic() >= next_progress:
                progress(report.copy())
                next_progress = time.monotonic() + 1
        report["status"] = "cancelled" if stop.is_set() else ("captured" if report["frames_read"] else "no_frames")
    except Exception as error:
        report.update(status="error", reason=getattr(error, "reason", "acquisition_failed"), error=str(error))
    finally:
        if backend is not None:
            report.update(getattr(backend, "read_diagnostics", {}))
            if backend.capabilities is not None:
                report["capabilities"] = backend.capabilities
            if getattr(backend, "observed_format", None) is not None:
                report["observed_format"] = backend.observed_format
            try:
                backend.close()
            except Exception as error:
                report.update(status="error", cleanup_ok=False, reason="close_failed", error=str(error))
        if started is not None:
            elapsed = time.monotonic() - started
            report["acquisition_elapsed_ms"] = round(elapsed * 1000, 3)
            report["observed_read_rate_hz"] = round(report["frames_read"] / max(elapsed, 0.001), 3)
            report["maximum_read_gap_ms"] = round(max(max_gap, time.monotonic() - last_read if last_read else elapsed) * 1000, 3)
    # This is sampled image change, not a producer sequence or exposure check.
    preview = None
    if report["status"] == "captured" and last_frame is not None:
        preview = grayscale_sample(last_frame, backend.format, 640)
    return report, preview


class MirrorSource:
    def __init__(self, acquire, source="steamvr-mirror", *, identity=None, session=None):
        """``acquire(stop, on_frame, progress)`` reads until done and returns its report.

        ``on_frame(data, fmt, number, observed_ns)`` receives the read bytes and the
        validated V4L2 format dictionary; ``progress(report)`` receives snapshots.
        ``identity``, when given, declares ``SOURCE_FIELDS`` (for example a fixture
        backend's kind, source_id and capture_layout) to ``CaptureContext``.

        ``session``, when given, is a ``sources.session.MirrorSession`` and the
        only source of the session tokens (``hud.contract.SESSION_TOKENS``):
        an ``identity`` that also declares one is refused. It is declared here,
        before the source opens; a token no provider observed then stays
        undeclared (``None``) for the whole run. Each frame's metadata then
        carries the tokens read for it, and a declared token lost since is
        ``None``. Once a read differs from the declaration the source declares
        the latest read, and a live provider (the tracking observer) is
        consulted at every admission, so ``CaptureContext`` fails closed and
        ``HudMask.check_session`` names the token.
        """
        self.acquire, self.source, self.identity, self.session = acquire, source, identity, session
        self.lock = threading.Lock()
        self._report = {}
        if session is not None:
            if any(getattr(identity, name, None) is not None for name in SESSION_TOKENS):
                raise ValueError("mirror_session_tokens_declared_twice")
            session.declare()

    def __getattr__(self, name):
        if name in SOURCE_FIELDS:
            session = self.__dict__.get("session")
            if session is not None and name in SESSION_TOKENS:
                return session.current().get(name)
            return getattr(self.__dict__.get("identity"), name, None)
        raise AttributeError(name)

    @property
    def report(self):
        with self.lock:
            return self._report.copy()

    @property
    def session_report(self):
        """Where each session token came from and the first change seen, or ``None`` without a session."""
        return self.session.report() if self.session is not None else None

    def _update(self, report):
        with self.lock:
            self._report = report.copy()

    def run(self, emit, stop):
        def on_frame(data, fmt, number, observed_ns):
            metadata = {"native_format": dict(fmt)}
            if self.session is not None:
                metadata.update(self.session.read())
            emit(Frame(bytes(data), fmt["fourcc"], fmt["width"], fmt["height"], fmt["stride_bytes"],
                       self.source, number, observed_ns, metadata=metadata))

        report = self.acquire(stop, on_frame, self._update)
        self._update(report)
        if report.get("status") == "error":
            raise SightCircuitError(report.get("reason", "capture_failed"), report.get("error", "Capture failed"))


def run_worker(worker, payload):
    """A worker interpreter's main: run ``worker`` on ``payload`` and end with its ``result``; returns the exit code.

    ``payload`` carries ``job``, ``seconds``, ``outputs`` (reservations by kind)
    and ``success_status``. The report's ``exit_code`` is 0 for the success
    status with ``cleanup_ok`` true, else 1, as the launcher derived it before,
    and is the process exit code too, so the launcher's validator can check
    one against the other. A worker whose supervisor is already gone does not
    start (exit 1, no result).
    """
    if not enter_worker():
        return 1
    stop = guard.stop_on_signals()
    report = worker(payload["job"], payload["seconds"], payload.get("outputs") or {},
                    lambda snapshot: emit_event("progress", snapshot), stop)
    report["exit_code"] = 0 if (report.get("status") == payload["success_status"] and
                                report.get("cleanup_ok") is True) else 1
    emit_event("result", report)
    return report["exit_code"]


def supervise_worker(worker, job, seconds, outputs=None, *, report_factory, success_status, timeout=None):
    """Run ``worker`` on ``job`` in its own supervised interpreter; return the launcher's report.

    The worker's ``progress`` snapshots fill the report as they arrive and its
    ``result`` replaces them; the supervision block (``worker_pid``,
    ``worker_exit_code``, ``worker_forced_termination``, ``worker_elapsed_ms``,
    ``cleanup_ok``, ``exit_code`` and the primitive's fields) is the
    primitive's. The deadline is ``timeout`` or ``seconds`` + 3. Outcomes keep
    the launcher's names: ``timed_out`` with reason ``worker_deadline``, and
    ``error`` with ``worker_exited_without_result`` or ``worker_exit_failure``;
    a cancellation is ``cancelled`` with ``reason`` ``signal``, ``signal`` and
    exit 128 + signal.
    """
    reference = function_reference(worker)
    base = dict(report_factory(), seconds_requested=seconds)
    payload = {"job": job, "seconds": seconds, "outputs": outputs or {}, "success_status": success_status}
    snapshots, seen = {}, {}

    def validate(candidate, returncode):
        seen["result"] = True
        if (not isinstance(candidate, dict) or type(candidate.get("exit_code")) is not int or
                type(candidate.get("cleanup_ok")) is not bool):
            raise ValueError("invalid_worker_report")
        if candidate["exit_code"] != returncode:
            raise ValueError("worker_exit_failure")

    supervised = supervise_owned(
        [sys.executable, "-c", WORKER_BOOTSTRAP, str(guard.ROOT), json.dumps(reference), json.dumps(payload)],
        base, seconds + 3 if timeout is None else timeout, validate, grace=WORKER_GRACE, progress=snapshots.update)
    report = {**snapshots, **supervised}
    if report["status"] == "timed_out":
        report["reason"] = "worker_deadline"
    elif report["status"] in ("invalid_worker_report", "supervisor_lost", "cleanup_failed"):
        report["status"] = "error"
        if not seen:
            report["reason"] = "worker_exited_without_result"
    elif report["status"] == "cancelled":
        report.update(reason="signal", signal=report["exit_code"] - 128)
    report["worker_elapsed_ms"] = round(report["supervisor_elapsed_seconds"] * 1000, 3)
    return report


def launch(report, discovery, device, seconds, *, worker, report_factory, success_status, job=None,
           outputs=None, digests=(), timeout=None, fields=None):
    """Run ``worker`` bounded on the verified candidate and publish its outputs; return the final report.

    ``report`` is the command's discovery report; it is returned, with the
    failure, when no worker report replaces it. ``outputs`` maps an output kind
    ("preview", "still") to ``(path, prefix)``. Each is reserved before the
    worker starts, so an unsuitable path stops the launch, and reaches the
    worker as ``{kind: reservation}``; all are published only after a run with
    exit code 0, and every temporary file is discarded. A kind in ``digests``
    is published only if it still matches the worker's ``<kind>_sha256``
    (``output.publish_all``). ``job(candidate)`` is
    the worker's job (default: the candidate); ``fields`` are added to the
    worker's report. SIGTERM and Ctrl+C cancel (exit 128 + signal); a selection,
    output or launch failure exits 1, ``unavailable`` without a candidate.
    """
    reservations = {}
    with guard.sigterm_cancels():
        try:
            candidate = select_candidate(discovery, device)
            for kind, (path, prefix) in (outputs or {}).items():
                reservations[kind] = output.reserve(path, prefix, kind=kind)
            report = supervise_worker(worker, job(candidate) if job is not None else candidate, seconds,
                                      reservations or None, timeout=timeout,
                                      report_factory=lambda: dict(report_factory(), candidate=candidate),
                                      success_status=success_status)
            report.update(discovery=discovery, candidate=candidate, **(fields or {}))
            if report["exit_code"] == 0:
                output.publish_all(reservations, report, digests=digests)
        except (SightCircuitError, OSError, ValueError) as error:
            reason = getattr(error, "reason", "output_or_launch_failed")
            report.update(status="unavailable" if reason == "mirror_unavailable" else "error",
                          reason=reason, error=str(error), exit_code=1)
        except guard.CANCELLATION as error:
            guard.cancelled(report, error)
        finally:
            for kind, reservation in reservations.items():
                try:
                    output.discard(reservation)
                except OSError as error:
                    report.update(status="error", reason=f"{kind}_cleanup_failed", error=str(error), exit_code=1)
    return report
