"""Bound an explicitly prepared capture worker. Default never starts capture."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import threading

try:
    from .capture_session import Session, recover
except ImportError:
    from capture_session import Session, recover

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "artifacts/capture-supervisor/framejacker-capture-worker-exec"
WORKER = ROOT / "artifacts/capture-provider-reference/reference-ptgrab"
MAX_OUTPUT = 256 * 1024
MAX_LINE = 8192


class StopRequested(BaseException):
    def __init__(self, number):
        self.number = number


@contextlib.contextmanager
def cleanup_signals():
    # Repeated cancellation must not interrupt kill/reap/file-recovery work.
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, signal.SIG_IGN)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def deferred_signals():
    """Hold SIGINT/SIGTERM for the block; a held one is delivered as it ends.

    The caller's previous mask is restored, also on error, including a cancellation raised by
    the call that holds the signals: CPython changes the mask and then runs pending handlers,
    so that call sits inside the try. pthread_sigmask covers only the calling thread, so this
    holds only while no other thread in the process leaves either signal unblocked. The
    supervisor starts no thread; a caller process with other threads, such as a test process
    with BLAS threads, must block both signals in them. Python runs handlers on the main thread
    only, so a signal another thread took would still interrupt the block.
    """
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, ())  # Read only: nothing to undo yet.
    try:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextlib.contextmanager
def recorded_signals():
    """Record SIGINT/SIGTERM for the block; deliver each one to its handler afterwards.

    Used while the worker starts. A handler that raises inside subprocess.Popen after the
    child exists leaves no handle on it, so cleanup could neither stop nor reap it. While the
    block runs only the Python handlers change, never the signal mask: a child inherits the
    mask, while exec resets caught signals. An ignored signal is not swapped, so the kernel
    keeps discarding it and the worker still inherits SIG_IGN. Afterwards each distinct
    recorded signal is delivered once, in arrival order, until a handler raises or SIG_DFL
    ends the process. Off the main thread, or while a handler was not installed from Python,
    this changes nothing.
    """
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    if threading.current_thread() is not threading.main_thread() or None in previous.values():
        yield
        return
    numbers = [number for number, handler in previous.items() if handler != signal.SIG_IGN]
    pending = []
    try:
        for number in numbers:
            signal.signal(number, lambda received, frame: pending.append(received))
        yield
    finally:
        # The block has ended, so no child inherits the mask now. It holds both signals while
        # the handlers go back, so, under the thread condition of deferred_signals(), one landing
        # between the two restores cannot leave the other recording; a held signal reaches its
        # restored handler as the mask is lifted.
        with deferred_signals():
            for number in numbers:
                signal.signal(number, previous[number])
        for number in dict.fromkeys(pending):
            handler = previous[number]
            if handler == signal.SIG_DFL:
                signal.raise_signal(number)
            elif callable(handler):
                handler(number, None)


def new_report():
    return {"report_kind": "capture_supervisor", "schema_version": 1, "status": "not_run",
            "exit_code": 3, "worker_started": False, "worker_reaped": False,
            "worker_forced_termination": False, "api_cleanup_confirmed": False,
            "publication_recovery_confirmed": True, "layer_installation_performed": False,
            "physical_camera_identity": "unverified", "producer_completion": "unverified",
            "deployment_ready": False, "scope": "Bounded capture process; source identity and original producer completion require device validation"}


def supervise(*, worker=WORKER, launcher=LAUNCHER, runtime=None, seconds=10, max_fps=5,
              timeout=None, worker_arguments=None, grace=0.3):
    if not 1 <= seconds <= 10 or not 1 <= max_fps <= 10:
        raise ValueError("invalid_capture_bounds")
    report = new_report()
    report.update(status="failed", exit_code=1)
    session = process = None
    owned_fd = None
    output, diagnostics = bytearray(), bytearray()
    events = []
    forced = killed = invalid_output = False
    replaced = None
    total = 0
    started = time.monotonic()
    # Includes startup, import, copies, and destruction, not just the stream loop.
    deadline = started + (seconds + 3 if timeout is None else timeout)
    def consume(data, target):
        nonlocal total
        total += len(data)
        if total > MAX_OUTPUT:
            raise ValueError("capture_output_limit")
        target.extend(data)
        if target is output:
            while b"\n" in output:
                raw, _, tail = output.partition(b"\n")
                output[:] = tail
                if len(raw) > MAX_LINE or len(events) >= 256:
                    raise ValueError("capture_event_limit")
                event = json.loads(raw)
                if not isinstance(event, dict) or type(event.get("event")) is not str:
                    raise ValueError("invalid_capture_event")
                events.append(event)
                if event["event"] == "publication_ready":
                    session.publication(event)
            if len(output) > MAX_LINE:
                raise ValueError("capture_event_limit")

    @contextlib.contextmanager
    def noted_error():
        # A cancellation held or recorded during the block replaces an error the block raises;
        # name that error here rather than infer it from __context__, which would also pick up
        # an exception that supervise()'s caller is handling.
        nonlocal replaced
        try:
            yield
        except (OSError, ValueError, TypeError) as error:
            replaced = str(error)
            raise

    try:
        owned_fd = os.open(worker, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        info = os.fstat(owned_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or info.st_mode & 0o6022 or not info.st_mode & 0o100):
            raise ValueError("worker_not_prepared_executable")
        session = Session(runtime or os.environ.get("XDG_RUNTIME_DIR", ""))
        report["session_path"] = str(session.path)
        environment = dict(os.environ, FRAMEJACKER_CAPTURE_OUTPUT=str(session.path / "frames.shm"),
                           FRAMEJACKER_CAPTURE_SUPERVISOR_PID=str(os.getpid()))
        arguments = worker_arguments if worker_arguments is not None else [
            "--allow-experimental-capture", "--seconds", str(seconds), "--max-fps", str(max_fps)]
        # A cancellation while the worker starts is recorded (not masked: the worker would
        # inherit the mask) and delivered once its pid is in the report and lease, so cleanup
        # can stop and reap it.
        with recorded_signals(), noted_error():
            process = subprocess.Popen([str(launcher), str(os.getpid()), str(owned_fd), str(worker), *arguments],
                                       pass_fds=(owned_fd,), env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            report.update(worker_started=True, worker_pid=process.pid, publication_recovery_confirmed=False)
            session.worker(process.pid)
        with selectors.DefaultSelector() as selector:
            for stream, target in ((process.stdout, output), (process.stderr, diagnostics)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, target)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report.update(status="timed_out", reason="capture_deadline")
                    forced = True
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    # Bytes taken from the pipe are recorded before a cancellation lands, so an
                    # announced publication identity is never lost; unread bytes stay for the drain.
                    with deferred_signals(), noted_error():
                        try:
                            data = os.read(key.fd, 16384)
                        except BlockingIOError:
                            continue
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        consume(data, key.data)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
        forced = process is not None
        if replaced is not None:
            # A held or recorded cancellation delivered while a spawn or recording error was
            # raised replaces it; keep the error, and do not validate output already known to be
            # invalid.
            report["superseded_error"] = replaced
            invalid_output = True
    except (OSError, ValueError, TypeError) as error:
        report.update(reason=str(error))
        invalid_output = True
        forced = process is not None
    finally:
        with cleanup_signals():
            if owned_fd is not None:
                os.close(owned_fd)
            if process is not None:
                if forced or process.poll() is None:
                    forced = True
                    for number, budget in ((signal.SIGTERM, grace), (signal.SIGKILL, 1)):
                        if number == signal.SIGKILL and process.poll() is None:
                            killed = True
                        try:
                            os.killpg(process.pid, number)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=budget)
                        except subprocess.TimeoutExpired:
                            pass
                report.update(worker_exit_code=process.poll(), worker_reaped=process.poll() is not None)
                # Collect a graceful cancellation's final event without waiting on an
                # inherited pipe held by another process. All reads remain bounded.
                try:
                    if not invalid_output:
                        for stream, target in ((process.stdout, output), (process.stderr, diagnostics)):
                            while True:
                                try:
                                    data = os.read(stream.fileno(), 16384)
                                except BlockingIOError:
                                    break
                                if not data:
                                    break
                                consume(data, target)
                        if output:
                            raise ValueError("partial_capture_event")
                        closed = [event for event in events if event["event"] == "capture_closed"]
                        if len(closed) != 1 or events[-1] is not closed[0]:
                            raise ValueError("missing_capture_completion")
                        final = closed[0]
                        if (process.returncode is None or process.returncode < 0 or
                            type(final.get("exit_code")) is not int or final["exit_code"] != process.returncode or
                                any(type(final.get(field)) is not bool for field in (
                                    "gpu_resource_cleanup_ok", "publication_cleanup_ok", "registry_unchanged"))):
                            raise ValueError("inconsistent_capture_completion")
                        report.update(native=final, api_cleanup_confirmed=final["gpu_resource_cleanup_ok"] and not killed)
                        if report["status"] == "failed":
                            report["status"] = "captured" if (process.returncode == 0 and final["registry_unchanged"] and
                                                                      final["publication_cleanup_ok"]) else "failed"
                except (OSError, ValueError, TypeError) as error:
                    report.setdefault("reason", str(error))
                    report["api_cleanup_confirmed"] = False
                process.stdout.close()
                process.stderr.close()
            if session is not None:
                if process is None or process.poll() is not None:
                    try:
                        session.cleanup()
                        report["publication_recovery_confirmed"] = True
                    except (OSError, ValueError) as error:
                        report.update(publication_recovery_confirmed=False, recovery_error=str(error))
                session.close()
            report.update(worker_forced_termination=killed, worker_termination_requested=forced, events=events,
                          elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            if diagnostics:
                report["worker_stderr"] = diagnostics.decode("utf-8", errors="replace")
            okay = (report["status"] == "captured" and report["api_cleanup_confirmed"] and
                    report["publication_recovery_confirmed"] and report["worker_reaped"] and not forced)
            if report["status"] == "captured" and not okay:
                report["status"] = "failed"
            if report["status"] != "cancelled":
                report["exit_code"] = 0 if okay else 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--capture", action="store_true", help="Blocked pending a coordinated producer access contract; use the metadata diagnostic next")
    mode.add_argument("--recover-session", type=Path, help="Recover a dead session using its recorded process/file identities")
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=10)
    parser.add_argument("--max-fps", type=int, choices=range(1, 11), default=5)
    args = parser.parse_args(argv)
    previous = {}
    def stop(number, _frame):
        raise StopRequested(number)
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
        if args.capture:
            report = new_report()
            report.update(status="blocked", reason="producer_access_contract_unavailable",
                          next_step="python3 scripts/capture_provider_check.py --inspect")
        elif args.recover_session:
            report = new_report()
            try:
                recover(args.recover_session)
                report.update(status="recovered", exit_code=0)
            except (OSError, ValueError, TypeError) as error:
                report.update(status="recovery_refused", exit_code=1, reason=str(error), publication_recovery_confirmed=False)
        else:
            report = new_report()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
