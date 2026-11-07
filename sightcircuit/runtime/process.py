"""Bounded spawned Python workers: parent-death signalling, cancellation and reaping.

Used by the SteamVR mirror launcher (``sources.mirror.launch``, for
``mirror_check`` and ``mirror_recognize``), whose workers stream progress
reports. Command workers that need an owned process group and the Linux
parent-loss guardian use ``sightcircuit/runtime/guard.py`` (``supervise``).
The cancellation pair ``StopRequested``/``stop_on_signal`` is ``guard``'s,
re-exported here: a launcher runs ``run_bounded`` inside
``guard.sigterm_cancels()`` so that a SIGTERM is recognised as a cancellation.
"""
import ctypes
import json
import multiprocessing
import os
import signal
import time

from .guard import StopRequested, signal_number, stop_on_signal  # noqa: F401 - the shared cancellation pair

PR_SET_PDEATHSIG = 1


def set_parent_death_signal(number):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, number, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not enable parent-death signaling")


def enter_worker(expected_parent):
    """Prepare a spawned worker; False means its launcher has already exited.

    The launcher owns interactive cancellation. A worker must not be stranded if
    its launcher is killed during a driver call, so it receives SIGTERM then.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    set_parent_death_signal(signal.SIGTERM)
    return os.getppid() == expected_parent


def send_report(sender, report, event="progress"):
    sender.send_bytes(json.dumps({"event": event, "report": report}).encode())


def run_bounded(job, seconds, preview_temp=None, *, worker, report_factory, timeout=None,
                success_status):
    """Run ``worker(job, seconds, preview_temp, sender, stop, parent_pid)`` in a spawned process.

    The worker streams ``progress`` reports and one final ``result``. The launcher
    enforces a deadline, escalates terminate/kill and reports whether cooperative
    cleanup was confirmed.
    """
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    process = ctx.Process(target=worker, args=(job, seconds, preview_temp, sender, stop, os.getpid()), daemon=True)
    report = report_factory()
    report.update(seconds_requested=seconds)
    result_seen = False
    forced = False
    interrupted = None
    started = time.monotonic()
    deadline = started + (seconds + 3 if timeout is None else timeout)
    try:
        process.start()
        report["worker_pid"] = process.pid
        sender.close()
        while time.monotonic() < deadline:
            if receiver.poll(min(0.05, max(0, deadline - time.monotonic()))):
                try:
                    message = json.loads(receiver.recv_bytes(16384))
                except EOFError:
                    break
                report.update(message["report"])
                if message["event"] == "result":
                    result_seen = True
                    break
            if not process.is_alive():
                if receiver.poll():
                    continue
                break
        if not result_seen:
            expired = time.monotonic() >= deadline
            report.update(status="timed_out" if expired else "error",
                          reason="worker_deadline" if expired else "worker_exited_without_result")
    except (KeyboardInterrupt, StopRequested) as error:
        interrupted = signal_number(error)
        report.update(status="cancelled", reason="signal", signal=interrupted)
    except Exception as error:
        report.update(status="error", reason="worker_failed", error=str(error))
    finally:
        stop.set()
        if process.pid is not None:
            process.join(timeout=0.5)
            if process.is_alive():
                forced = True
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if process.is_alive():
                report.update(status="error", reason="worker_did_not_exit")
            report["worker_exit_code"] = process.exitcode
            if result_seen and process.exitcode != 0:
                report.update(status="error", reason="worker_exit_failure", worker_exit_code=process.exitcode)
            if not process.is_alive():
                process.close()
        sender.close()
        receiver.close()
    report.update(worker_forced_termination=forced, worker_elapsed_ms=round((time.monotonic() - started) * 1000, 3))
    if forced or not result_seen:
        report["cleanup_ok"] = False  # OS closes FDs, but cooperative close was not confirmed.
    report["exit_code"] = (128 + interrupted if interrupted else
                           0 if report["status"] == success_status and report["cleanup_ok"] else 1)
    return report
