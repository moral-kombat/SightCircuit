"""Bounded JSON worker supervision and Linux parent-loss guardian for POC tools.

One supervisor on the main thread owns a fresh process group. On Linux it
temporarily becomes a subreaper so an orphaned helper in that group can be
reaped, not merely killed. Never wait for or signal unrelated children.
Linux uses a separate guardian and a private parent lifeline; catchable signals
are not the only trigger for worker cleanup. The guardian reports its worker's
process group, so if the guardian is killed the supervisor (its subreaper)
terminates and reaps that orphaned group itself. Windows callers must be single-
process workers (no native overlay helper) and have no parent-death guarantee.

Callers pass their own module-level ``validate(report, returncode, **options)``
from ``scripts/`` or ``sightcircuit/``. The guardian imports that validator by
dotted module name from this checkout only, so it knows no command and never
imports worker code beyond the validator's module (no detector, decoder or VR
library is loaded at import time).

Stop on a signal, the one convention for both sides of a bounded run: a
launcher runs inside ``sigterm_cancels()``, where SIGTERM raises
``StopRequested`` as Ctrl+C raises ``KeyboardInterrupt`` (together
``CANCELLATION``), and ``cancelled(report, error)`` records either as a
``cancelled`` report with exit 128 + signal. A worker calls
``stop_on_signals()`` once: SIGINT and SIGTERM then set its stop event, so its
own cleanup runs, and the supervisor is told the worker has armed.

The supervision itself is ``sightcircuit.runtime.supervision``, the one
primitive; this module applies the guard's policy to it (record the first
signal and poll it, override an inherited ignore, the ``GRACE`` budgets) at
both of its levels, and adds the guardian, the lifeline and the validators.
"""
import contextlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading

if __name__ == "__main__":
    # Run as a file (the guardian): import the checkout's packages, never siblings of this file.
    sys.path[0] = str(Path(__file__).resolve().parents[2])
from sightcircuit.runtime import supervision  # noqa: E402
from sightcircuit.runtime.supervision import (  # noqa: E402,F401 - the primitive's names, kept where callers found them
    GRACE, MAX_REPORT_BYTES, RAISE_ON_SIGNAL, RECORD_AND_POLL, Cancelled, Grace, SignalPolicy, Signals,
    child_reaper, report_armed, supervise_owned,
)

COOPERATIVE_GRACE = GRACE.cooperative
TERM_GRACE = GRACE.terminate
KILL_GRACE = GRACE.kill
GUARDIAN_STARTUP_SECONDS = 8  # The guardian's own start-up and teardown, beyond the worker's deadline.


class StopRequested(BaseException):
    """SIGTERM on a launcher's main thread, raised by ``stop_on_signal``."""

    def __init__(self, number):
        self.number = number


def stop_on_signal(number, _frame):
    """Signal handler that turns SIGTERM into a catchable cancellation."""
    raise StopRequested(number)


CANCELLATION = (KeyboardInterrupt, StopRequested)


@contextlib.contextmanager
def sigterm_cancels():
    """Within the block SIGTERM raises ``StopRequested``; the previous handler is restored afterwards."""
    previous = signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def signal_number(error):
    """The signal behind a ``CANCELLATION``: its ``StopRequested`` number, else SIGINT."""
    return error.number if isinstance(error, StopRequested) else signal.SIGINT


def cancelled(report, error):
    """Record a launcher's ``CANCELLATION``: status ``cancelled``, reason ``signal`` and exit 128 + signal."""
    number = signal_number(error)
    report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
    return report


def stop_on_signals(stop=None):
    """A worker's cooperative stop: SIGINT and SIGTERM set ``stop`` (by default a new event), which is returned.

    This is also the worker's arm point: once the handlers are installed the
    supervisor is told (``supervision.report_armed``), so a cancellation that
    arrived while the worker was still starting is delivered now, to a
    handler, and not earlier, to the default disposition. The event is set by
    the process's stop relay (``supervision.StopWakeup``), never by the handler
    on the main thread, so a signal that lands inside the worker's own
    ``stop.wait()`` neither deadlocks it nor waits for the wait to time out.
    """
    stop = supervision.stop_on(threading.Event() if stop is None else stop)
    report_armed()
    return stop


def cleanup_budget(default):
    """Seconds a guarded worker may spend on one cleanup join; see ``supervision.cooperative_budget``."""
    return supervision.cooperative_budget(default)


@contextlib.contextmanager
def cancellation():
    """Record SIGINT, SIGTERM and SIGHUP for the block (``RECORD_AND_POLL``); yields the recorded numbers, first first."""
    with Signals(RECORD_AND_POLL) as handlers:
        yield handlers.recorded


# The names the guard used before the primitive existed, for callers and tests that reached for them.
_reap_members = supervision.reap_members
_group_exists = supervision.group_exists
_reported_group = supervision.reported_group


def _contain_orphaned_group(pgid):
    return supervision.contain_orphaned_group(pgid, GRACE)


def _supervise_owned(command, base_report, timeout, validate, *, descendants=False, parent_fd=None, pass_fds=(),
                     grace=GRACE, group_fd=None, nested_group_fd=None, progress=None):
    """The guard's policy on the primitive: record-and-poll, an inherited ignore overridden, the guard's budgets."""
    return supervise_owned(command, base_report, timeout, validate, descendants=descendants, parent_fd=parent_fd,
                           pass_fds=pass_fds, grace=grace, group_fd=group_fd, nested_group_fd=nested_group_fd,
                           progress=progress, signals=RECORD_AND_POLL)


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PACKAGES = ("scripts", "sightcircuit")


def _dotted(name):
    parts = name.split(".") if isinstance(name, str) else []
    return (len(parts) >= 2 and parts[0] in VALIDATOR_PACKAGES and
            all(part.isidentifier() for part in parts))


def validator_reference(validate, options=None):
    """Name a module-level validator of this checkout so the guardian can import it."""
    module = sys.modules.get(getattr(validate, "__module__", None))
    path = Path(getattr(module, "__file__", None) or "").resolve()
    name = getattr(validate, "__name__", "")
    try:
        dotted = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    except ValueError:
        dotted = ""
    if (path.suffix != ".py" or not _dotted(dotted) or not name.isidentifier() or
            getattr(module, name, None) is not validate):
        raise ValueError("worker_validator_must_be_module_level_in_checkout")
    return {"module": dotted, "function": name, "options": dict(options or {})}


def load_validator(reference):
    """Import a referenced validator from this checkout only; bind its options."""
    module_name, function_name = reference.get("module", ""), reference.get("function", "")
    options = reference.get("options") or {}
    if (not _dotted(module_name) or not isinstance(function_name, str) or not function_name.isidentifier() or
            not isinstance(options, dict)):
        raise ValueError("unsupported_worker_validator")
    try:
        spec = importlib.util.find_spec(module_name)
    except ImportError:
        spec = None
    allowed = tuple(ROOT / package for package in VALIDATOR_PACKAGES)
    if (spec is None or spec.origin is None or
            not any(Path(spec.origin).resolve().is_relative_to(base) for base in allowed)):
        raise ValueError("unsupported_worker_validator")
    validate = getattr(importlib.import_module(module_name), function_name)

    def bound(report, returncode):
        return validate(report, returncode, **options)
    return bound


def _validate_guard_report(report, returncode):
    if (not isinstance(report, dict) or report.get("report_kind") != "bounded_worker_guard" or
            type(report.get("exit_code")) is not int or report["exit_code"] != returncode or
            type(report.get("cleanup_ok")) is not bool or not isinstance(report.get("result"), dict)):
        raise ValueError("invalid_guardian_report")


def supervise(command, base_report, timeout, validate, *, descendants=False, validate_options=None,
              grace=GRACE, progress=None):
    """Run ``command`` and accept its JSON report only if ``validate`` does.

    ``validate`` must be a module-level function of a script in this directory;
    ``validate_options`` are passed to it as keyword arguments on every platform.
    ``grace`` holds the worker's cleanup budgets (the guardian's own are derived
    from them); ``progress`` receives each ``progress`` report the worker
    streams, forwarded unchanged by the guardian.
    """
    reference = validator_reference(validate, validate_options)
    if not sys.platform.startswith("linux"):
        def bound(report, returncode):
            return validate(report, returncode, **reference["options"])
        report = _supervise_owned(command, base_report, timeout, bound, descendants=descendants, grace=grace,
                                  progress=progress)
        report["parent_death_containment"] = "unavailable_on_this_platform"
        return report
    # Only the calling process holds the write end. Worker/helper execs cannot
    # inherit it, so an abrupt parent death is observable without PID polling.
    read_fd, write_fd = os.pipe()
    # The guardian writes its worker's group here, for containment if it is killed.
    try:
        group_read, group_write = os.pipe()
    except OSError:
        os.close(write_fd)
        os.close(read_fd)
        raise
    guard_base = {"report_kind": "bounded_worker_guard"}
    options = {"command": command, "base_report": base_report, "timeout": timeout,
               "descendants": descendants, "parent_fd": read_fd, "group_fd": group_write,
               "validator": reference, "grace": grace.as_dict()}
    try:
        # The guardian gets GUARDIAN_STARTUP_SECONDS beyond the worker's deadline, and a cooperative
        # grace that covers the longest cleanup the worker's budgets allow it (``Grace.for_supervisor_of``).
        guarded = _supervise_owned([sys.executable, str(Path(__file__).resolve()),
                                   "--guard", json.dumps(options)], guard_base,
                                  timeout + GUARDIAN_STARTUP_SECONDS, _validate_guard_report,
                                  pass_fds=(read_fd, group_write), grace=grace.for_supervisor_of(),
                                  nested_group_fd=group_read, progress=progress)
    finally:
        for fd in (write_fd, read_fd, group_write, group_read):
            os.close(fd)
    report = guarded.get("result")
    if report is None:
        # Reaping is confirmed only by containment of a reported orphaned group.
        contained = guarded.get("orphaned_worker_tree_reaped", False)
        report = dict(base_report, status="guardian_failed", exit_code=1, cleanup_ok=False,
                      worker_reaped=contained, worker_tree_reaped=contained,
                      reason=guarded.get("reason", guarded.get("status")))
    for key in ("orphaned_worker_group", "orphaned_worker_tree_signalled", "orphaned_worker_tree_reaped"):
        if key in guarded:
            report[key] = guarded[key]
    report.update(parent_death_containment="linux_lifeline_guardian",
                  guardian_pid=guarded.get("worker_pid"), guardian_reaped=guarded.get("worker_reaped", False),
                  guardian_exit_code=guarded.get("worker_exit_code"),
                  guardian_forced_termination=guarded.get("worker_forced_termination", False),
                  guardian_status=guarded.get("status"), guardian_armed=guarded.get("worker_armed", False),
                  guardian_report_reader_stopped=guarded.get("report_reader_stopped", False),
                  worker_supervision_elapsed_seconds=report.get("supervisor_elapsed_seconds"),
                  supervisor_elapsed_seconds=guarded.get("supervisor_elapsed_seconds"))
    if guarded.get("reason"):
        report["guardian_reason"] = guarded["reason"]
    report["cleanup_ok"] = report.get("cleanup_ok") is True and guarded.get("cleanup_ok") is True
    if guarded.get("status") == "cancelled":
        report.update(status="cancelled", exit_code=guarded["exit_code"])
    elif not report["cleanup_ok"] and report["exit_code"] == 0:
        report.update(status="cleanup_failed", exit_code=1)
    return report


def _guard_main(options):
    # The guardian imports no detector, decoder or VR library, and never runs
    # worker code in this process. Its watchdog survives a stalled inference.
    validate = load_validator(options.get("validator") or {})

    def forward(report):
        # The supervisor parses this line as an event, never as the guardian's report.
        print(json.dumps({"event": "progress", "report": report}), flush=True)
    result = _supervise_owned(options["command"], options["base_report"], options["timeout"],
                             validate, descendants=options["descendants"],
                             parent_fd=options["parent_fd"], group_fd=options.get("group_fd"),
                             grace=Grace.from_dict(options.get("grace") or {}), progress=forward)
    report = {"report_kind": "bounded_worker_guard", "status": result["status"],
              "exit_code": result["exit_code"], "cleanup_ok": result["cleanup_ok"], "result": result}
    os.close(options["parent_fd"])
    if options.get("group_fd") is not None:
        os.close(options["group_fd"])
    try:
        print(json.dumps(report), flush=True)
    except BrokenPipeError:
        # Cleanup is already complete; the dead supervisor cannot receive proof.
        os.close(sys.stdout.fileno())
        os._exit(1)
    return report["exit_code"]


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--guard":
        sys.exit(64)
    sys.exit(_guard_main(json.loads(sys.argv[2])))
