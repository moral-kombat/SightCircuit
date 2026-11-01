"""Bounded JSON worker supervision and Linux parent-loss guardian for POC tools.

One supervisor on the main thread owns a fresh process group. On Linux it
temporarily becomes a subreaper so an orphaned helper in that group can be
reaped, not merely killed. Never wait for or signal unrelated children.
Linux uses a separate guardian and a private parent lifeline; catchable signals
are not the only trigger for worker cleanup. Windows callers must be single-
process workers (no native overlay helper) and have no parent-death guarantee.

Callers pass their own module-level ``validate(report, returncode, **options)``
from ``scripts/`` or ``framejacker/``. The guardian imports that validator by
dotted module name from this checkout only, so it knows no command and never
imports worker code beyond the validator's module (no detector, decoder or VR
library is loaded at import time).
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


def _supervise_owned(command, base_report, timeout, validate, *, descendants=False,
                     parent_fd=None, pass_fds=(), cleanup_grace=COOPERATIVE_GRACE):
    """Consume bounded bytes off-thread; a partial report can never block the watchdog.

    SIGINT/TERM request cooperative worker shutdown first. Every escalation and
    surviving process is reflected in cleanup_ok, independently of child claims.
    Descendants must stay in the owned group; worker code cannot detach helpers.
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
            while True:
                try:
                    pid, _ = os.waitpid(-process.pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False

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
    guard_base = {"report_kind": "bounded_worker_guard"}
    options = {"command": command, "base_report": base_report, "timeout": timeout,
               "descendants": descendants, "parent_fd": read_fd, "validator": reference}
    try:
        guarded = _supervise_owned([sys.executable, str(Path(__file__).resolve()),
                                   "--guard", json.dumps(options)], guard_base,
                                  timeout + 8, _validate_guard_report, pass_fds=(read_fd,),
                                  cleanup_grace=8)
    finally:
        os.close(write_fd)
        os.close(read_fd)
    report = guarded.get("result")
    if report is None:
        report = dict(base_report, status="guardian_failed", exit_code=1, cleanup_ok=False,
                      worker_reaped=False, worker_tree_reaped=False,
                      reason=guarded.get("reason", guarded.get("status")))
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
                             parent_fd=options["parent_fd"])
    report = {"report_kind": "bounded_worker_guard", "status": result["status"],
              "exit_code": result["exit_code"], "cleanup_ok": result["cleanup_ok"], "result": result}
    os.close(options["parent_fd"])
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
