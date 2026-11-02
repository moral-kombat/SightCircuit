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
from ``scripts/`` or ``framejacker/``. The guardian imports that validator by
dotted module name from this checkout only, so it knows no command and never
imports worker code beyond the validator's module (no detector, decoder or VR
library is loaded at import time).

Stop on a signal, the one convention for both sides of a bounded run: a
launcher runs inside ``sigterm_cancels()``, where SIGTERM raises
``StopRequested`` as Ctrl+C raises ``KeyboardInterrupt`` (together
``CANCELLATION``), and ``cancelled(report, error)`` records either as a
``cancelled`` report with exit 128 + signal. A worker calls
``stop_on_signals()`` once: SIGINT and SIGTERM then set its stop event, so its
own cleanup runs.
"""
import contextlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time

MAX_REPORT_BYTES = 256 * 1024
COOPERATIVE_GRACE = 5.0
TERM_GRACE = 1.0
KILL_GRACE = 1.0


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
    """A worker's cooperative stop: SIGINT and SIGTERM set ``stop`` (by default a new event), which is returned."""
    stop = threading.Event() if stop is None else stop
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stop.set())
    return stop


@contextlib.contextmanager
def cancellation():
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("worker_supervisor_requires_main_thread")
    received = []
    previous = {}
    def remember(number, _frame):
        if not received:
            received.append(number)
    try:
        numbers = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            numbers.append(signal.SIGHUP)
        for number in numbers:
            previous[number] = signal.signal(number, remember)
        yield received
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def child_reaper():
    if not sys.platform.startswith("linux"):
        yield
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "cannot_establish_worker_child_reaper")
    try:
        yield
    finally:
        if libc.prctl(36, previous.value, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "cannot_restore_worker_child_reaper")


def _reap_members(pgid):
    """Reap exited members of process group ``pgid`` that are this process's children."""
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            return


def _group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _reported_group(fd):
    """The worker group a guardian wrote to ``fd`` and never confirmed gone, else None."""
    os.set_blocking(fd, False)
    try:
        lines = os.read(fd, 32).decode("ascii", "replace").split()
    except BlockingIOError:
        return None
    if len(lines) != 1 or not lines[0].isdigit() or int(lines[0]) <= 1:
        return None  # Nothing reported, or the guardian confirmed its tree "reaped".
    return int(lines[0])


def _contain_orphaned_group(pgid):
    """SIGTERM, then SIGKILL, a killed guardian's worker group; True once it is gone.

    The guardian never confirmed this group gone. While any member lives, or
    the worker waits unreaped as this subreaper's child, ``pgid`` names it.
    """
    for number, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(pgid, number)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + grace
        while True:
            _reap_members(pgid)
            if not _group_exists(pgid):
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    return False


def _supervise_owned(command, base_report, timeout, validate, *, descendants=False,
                     parent_fd=None, pass_fds=(), cleanup_grace=COOPERATIVE_GRACE,
                     group_fd=None, nested_group_fd=None):
    """Consume bounded bytes off-thread; a partial report can never block the watchdog.

    SIGINT/TERM request cooperative worker shutdown first. Every escalation and
    surviving process is reflected in cleanup_ok, independently of child claims.
    Descendants must stay in the owned group; worker code cannot detach helpers.
    A guardian writes its worker's group to ``group_fd``, then "reaped" once
    that tree is gone. The supervisor reads ``nested_group_fd`` and contains an
    unconfirmed group if the guardian is killed.
    """
    if descendants and not sys.platform.startswith("linux"):
        raise ValueError("native_helper_supervision_requires_linux")
    report = dict(base_report, status="error", exit_code=1, cleanup_ok=False)
    process = reader = None
    owned = {}
    output = bytearray()
    read_done = threading.Event()
    read_errors = []
    forced, termination_requested, tree_gone = False, False, False
    failure = None
    started = time.monotonic()

    def read_report():
        try:
            while True:
                data = process.stdout.read(min(8192, MAX_REPORT_BYTES + 1 - len(output)))
                if not data:
                    break
                output.extend(data)
                if len(output) > MAX_REPORT_BYTES:
                    raise ValueError("worker_report_byte_limit")
        except (OSError, ValueError) as error:
            read_errors.append(str(error))
        finally:
            process.stdout.close()
            read_done.set()

    def group_exists():
        if os.name != "posix":
            return process.poll() is None
        # poll reaps our direct child first; only then reap adopted group members.
        if process.poll() is not None and sys.platform.startswith("linux"):
            _reap_members(process.pid)
        return _group_exists(process.pid)

    def signal_group(number):
        try:
            if os.name == "posix":
                os.killpg(process.pid, number)
            elif process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass

    def wait_tree(seconds):
        deadline = time.monotonic() + seconds
        while group_exists():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    with cancellation() as received, child_reaper():
        try:
            def parent_lost():
                return parent_fd is not None and bool(select.select([parent_fd], [], [], 0)[0])
            if parent_lost():
                raise ValueError("supervisor_lifeline_closed_before_start")
            extra = {"pass_fds": pass_fds} if os.name == "posix" else {}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       bufsize=0, start_new_session=(os.name == "posix"), **extra)
            report["worker_pid"] = process.pid
            if group_fd is not None:
                # A dead supervisor is detected below through the lifeline.
                with contextlib.suppress(BrokenPipeError):
                    os.write(group_fd, b"%d\n" % process.pid)
            reader = threading.Thread(target=read_report, daemon=True)
            reader.start()
            deadline = started + timeout
            while True:
                if parent_lost():
                    failure = "supervisor_lost"
                    break
                if received:
                    failure = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    failure = "timed_out"
                    break
                if read_errors:
                    raise ValueError(read_errors[0])
                if process.poll() is not None:
                    # A descendant may keep stdout open after the worker exits.
                    if read_done.wait(0.05):
                        break
                    failure = "worker_exited_with_open_report_pipe"
                    break
                read_done.wait(0.02) if not read_done.is_set() else time.sleep(0.02)
        except (OSError, ValueError) as error:
            failure = "error"
            report["reason"] = str(error)[:1024]
        finally:
            if process is not None:
                if process.poll() is None:
                    termination_requested = True
                    if os.name == "posix":
                        try:
                            process.send_signal(signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    else:
                        forced = True  # Windows terminate cannot run Python finally blocks.
                        process.terminate()
                    try:
                        process.wait(timeout=cleanup_grace)
                    except subprocess.TimeoutExpired:
                        pass
                tree_gone = not group_exists()
                if not tree_gone:
                    forced = True
                    signal_group(signal.SIGTERM)
                    tree_gone = wait_tree(TERM_GRACE)
                    if not tree_gone:
                        signal_group(getattr(signal, "SIGKILL", signal.SIGTERM))
                        tree_gone = wait_tree(KILL_GRACE)
                if group_fd is not None and tree_gone:
                    with contextlib.suppress(BrokenPipeError):
                        os.write(group_fd, b"reaped\n")
                orphan = (_reported_group(nested_group_fd) if nested_group_fd is not None and
                          process.returncode is not None and process.returncode < 0 else None)
                if orphan is not None:
                    # A killed guardian ran no cleanup; its worker group is ours to end.
                    _reap_members(orphan)
                    signalled = _group_exists(orphan)
                    orphan_gone = not signalled or _contain_orphaned_group(orphan)
                    forced = forced or signalled
                    tree_gone = tree_gone and orphan_gone
                    owned.update(orphaned_worker_group=orphan, orphaned_worker_tree_signalled=signalled,
                                 orphaned_worker_tree_reaped=orphan_gone)
                reader.join(timeout=0.2)
                forced = forced or (process.returncode is not None and process.returncode < 0)
                owned.update(worker_pid=process.pid, worker_reaped=process.poll() is not None,
                              worker_exit_code=process.returncode,
                              worker_tree_reaped=tree_gone, report_reader_stopped=not reader.is_alive())
            else:
                owned.update(worker_reaped=False, worker_tree_reaped=False, report_reader_stopped=True)

        if received:
            failure = "cancelled"
        try:
            if read_errors:
                raise ValueError(read_errors[0])
            if process is None or not read_done.is_set():
                raise ValueError("worker_report_incomplete")
            candidate = json.loads(output)
            validate(candidate, process.returncode)
            report.update(candidate)
        except (ValueError, TypeError) as error:
            report.setdefault("reason", str(error)[:1024])
            failure = failure or "invalid_worker_report"
        report.update(owned)
        report.update(worker_forced_termination=forced, worker_termination_requested=termination_requested,
                      supervisor_elapsed_seconds=round(time.monotonic() - started, 3))
        if failure:
            report.update(status=failure, exit_code=128 + received[0] if received else 1)
        # A cooperative cancellation may confirm cleanup, but timeout, invalid
        # reports, nonzero contradictions or forced kills never establish it.
        report["cleanup_ok"] = (report.get("cleanup_ok") is True and tree_gone and
            read_done.is_set() and owned.get("report_reader_stopped") is True and
            owned.get("worker_reaped") is True and not forced and failure in (None, "cancelled"))
        if not report["cleanup_ok"] and report["exit_code"] == 0:
            report.update(status="cleanup_failed", exit_code=1)
    return report


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PACKAGES = ("scripts", "framejacker")


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


def supervise(command, base_report, timeout, validate, *, descendants=False, validate_options=None):
    """Run ``command`` and accept its JSON report only if ``validate`` does.

    ``validate`` must be a module-level function of a script in this directory;
    ``validate_options`` are passed to it as keyword arguments on every platform.
    """
    reference = validator_reference(validate, validate_options)
    if not sys.platform.startswith("linux"):
        def bound(report, returncode):
            return validate(report, returncode, **reference["options"])
        report = _supervise_owned(command, base_report, timeout, bound, descendants=descendants)
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
               "validator": reference}
    try:
        guarded = _supervise_owned([sys.executable, str(Path(__file__).resolve()),
                                   "--guard", json.dumps(options)], guard_base,
                                  timeout + 8, _validate_guard_report, pass_fds=(read_fd, group_write),
                                  cleanup_grace=8, nested_group_fd=group_read)
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
                  guardian_status=guarded.get("status"),
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
    result = _supervise_owned(options["command"], options["base_report"], options["timeout"],
                             validate, descendants=options["descendants"],
                             parent_fd=options["parent_fd"], group_fd=options.get("group_fd"))
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
    # Run as a file: import validators as checkout packages, never as siblings of this file.
    sys.path[0] = str(ROOT)
    sys.exit(_guard_main(json.loads(sys.argv[2])))
