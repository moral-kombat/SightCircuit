"""Offline capture watchdog validation. No live-capture command is exposed."""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'artifacts/capture-publication-reference/framejacker-publication-fixture'
GUARDED_WORKER = ROOT / 'artifacts/capture-import-reference/framejacker-guarded-worker'
MAX_OUTPUT = 64 * 1024  # per stream; drain concurrently, never communicate unbounded data
TERM_GRACE = 0.3
KILL_GRACE = 1.0


@contextmanager
def cancellation():
    """Latch the first signal; repeated signals cannot interrupt child cleanup."""
    requested = []
    previous = {}
    def stop(number, _frame):
        if not requested:
            requested.append(number)
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
        yield requested
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def signal_group(pid, number):
    try:
        os.killpg(pid, number)
        return True
    except ProcessLookupError:
        return False


def supervise(command, *, timeout, requested):
    """Internal process primitive for future provider integration, not deployment.

    The native worker must install its own parent-death guard before runtime
    initialization. A process exit does not prove GPU or publication cleanup.
    No retry, file deletion, runtime configuration or executable chmod occurs.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise ValueError('watchdog timeout must be finite and in (0, 15]')
    started = time.monotonic()
    deadline = started + timeout
    report = dict(report_kind='capture_watchdog', schema_version=1,
                  status='failed', reason='launch_failed', exit_code=1,
                  worker_started=False, worker_reaped=False,
                  termination_requested=False, kill_requested=False,
                  resource_cleanup_confirmed=False, deployment_ready=False)
    process = None
    outputs = [bytearray(), bytearray()]
    try:
        if requested:
            report.update(status='cancelled', reason='signal', signal=requested[0])
            return report
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        report.update(worker_started=True, worker_pid=process.pid)
        with selectors.DefaultSelector() as selector:
            for stream, output in zip((process.stdout, process.stderr), outputs):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, output)
            while True:
                code = process.poll()
                if requested:
                    report.update(status='cancelled', reason='signal', signal=requested[0])
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report.update(status='timed_out', reason='worker_deadline')
                    break
                if code is not None and not selector.get_map():
                    report.update(status='finished' if code == 0 else 'failed',
                                  reason='worker_exit' if code >= 0 else 'worker_signal')
                    break
                overflow = False
                for key, _ in selector.select(min(remaining, 0.05)):
                    try:
                        chunk = os.read(key.fd, 16384)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(key.data) + len(chunk) > MAX_OUTPUT:
                        overflow = True
                        break
                    else:
                        key.data.extend(chunk)
                if overflow:
                    report.update(status='failed', reason='worker_output_limit')
                    break
    except OSError as error:
        report.update(status='failed', reason='launch_or_read_failed', error=str(error))
    finally:
        if process is not None:
            # Even an exited leader may leave descendants holding pipes or working.
            # Never signal unrelated processes or use an executable-name kill.
            if signal_group(process.pid, signal.SIGTERM):
                report['termination_requested'] = True
                if report['status'] == 'finished':
                    report.update(status='failed', reason='surviving_process_group')
                grace_end = time.monotonic() + TERM_GRACE
                while time.monotonic() < grace_end:
                    process.poll()
                    if not signal_group(process.pid, 0):
                        break
                    time.sleep(0.01)
                report['kill_requested'] = signal_group(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                report.update(status='failed', reason='worker_not_reaped')
            report.update(worker_exit_code=process.poll(), worker_reaped=process.poll() is not None)
            process.stdout.close()
            process.stderr.close()
        report.update(stdout=outputs[0].decode('utf-8', errors='replace'),
                      stderr=outputs[1].decode('utf-8', errors='replace'),
                      elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        report['exit_code'] = (128 + report['signal'] if report['status'] == 'cancelled' else
                               0 if report['status'] == 'finished' and report['worker_reaped'] else 1)
    return report


def synthetic_check(requested):
    if not FIXTURE.is_file():
        return dict(status='not_run', reason='publication_fixture_not_built', exit_code=1,
                    synthetic=True, capture_started=False, deployment_ready=False)
    with tempfile.TemporaryDirectory(prefix='framejacker-watchdog-') as directory:
        source = Path(directory) / 'frames.shm'
        report = supervise([str(FIXTURE), '--create', str(source), '--seconds', '1'],
                           timeout=3, requested=requested)
        report.update(synthetic=True, capture_started=False, publication_cleanup_confirmed=False)
        if report['status'] == 'finished':
            try:
                events = [json.loads(line) for line in report['stdout'].splitlines()]
                ready, closed = events
                valid = (ready.get('event') == 'publication_ready' and ready.get('synthetic') is True and
                         closed.get('event') == 'publication_closed' and closed.get('synthetic') is True and
                         closed.get('cleanup_ok') is True and type(closed.get('exit_code')) is int and
                         closed['exit_code'] == 0 and type(closed.get('frames_per_slot')) is int and
                         closed['frames_per_slot'] > 0 and not source.exists())
                if not valid:
                    raise ValueError('inconsistent fixture report')
                report['publication_cleanup_confirmed'] = True
            except (ValueError, TypeError, AttributeError):
                report.update(status='failed', reason='invalid_fixture_report', exit_code=1)
        # This temporary directory contains generated test data only. The general
        # supervisor never removes a real provider's possibly stale publication.
        return report


def check_guarded_worker(requested):
    """Exercise the real native argument/source-sync gate through the watchdog."""
    report = supervise([str(GUARDED_WORKER), '--allow-experimental-capture', '--seconds', '1'],
                       timeout=3, requested=requested)
    report.update(capture_started=None if report['worker_started'] else False)
    if report['reason'] == 'worker_exit' and report.get('worker_exit_code') == 3:
        try:
            native = json.loads(report['stdout'])
            if (not isinstance(native, dict) or native.get('report_kind') != 'capture_worker' or
                    native.get('schema_version') != 1 or native.get('status') != 'blocked' or
                    native.get('reason') != 'exporter_synchronization_unavailable' or
                    native.get('runtime_initialized') is not False or
                    native.get('capture_started') is not False or native.get('cleanup_ok') is not True or
                    type(native.get('exit_code')) is not int or native['exit_code'] != 3):
                raise ValueError('invalid worker report')
            report.update(status='blocked', reason=native['reason'], exit_code=3, native=native, capture_started=False)
        except (TypeError, ValueError):
            report.update(status='failed', reason='invalid_worker_report', exit_code=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-worker', action='store_true',
                      help='verify the native worker rejects capture before registry/GPU access')
    mode.add_argument('--synthetic-check', action='store_true',
                        help='supervise generated luma publication only; no Vulkan or camera')
    args = parser.parse_args(argv)
    if not args.synthetic_check and not args.check_worker:
        report = dict(status='not_run', exit_code=3, capture_started=False,
                      deployment_ready=False, required_argument='--synthetic-check')
    else:
        with cancellation() as requested:
            report = check_guarded_worker(requested) if args.check_worker else synthetic_check(requested)
    print(json.dumps(report, indent=2))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
