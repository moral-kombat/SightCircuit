"""Supervise an explicit Vulkan physical-device query; never access camera data."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "artifacts/capture-gpu-check/sightcircuit-capture-gpu-check"
MAX_STDOUT = 256 * 1024
MAX_STDERR = 32 * 1024


class StopRequested(BaseException):
    def __init__(self, number):
        self.number = number


def new_report():
    return {
        "report_kind": "capture_gpu_check", "schema_version": 1, "status": "not_run",
        "exit_code": 3, "query_started": False, "cleanup_ok": True,
        "vulkan_instance_created": False, "camera_opened": False,
        "logical_device_created": False, "external_memory_imported": False,
        "layer_installation_performed": False, "deployment_ready": False,
        "scope": "Physical-device queries only; live import, source completion and camera identity unverified",
    }


def run_query(*, binary=BINARY, timeout=10):
    report = new_report()
    report.update(status="query_failed", exit_code=1)
    process = None
    forced = False
    started = time.monotonic()
    output, error_output = bytearray(), bytearray()
    try:
        if not binary.is_file():
            report.update(reason="query_not_built", next_step="bash scripts/build_capture_gpu_check.sh")
            return report
        process = subprocess.Popen([str(binary), "--query-device"], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        report.update(query_started=True, worker_pid=process.pid,
                      vulkan_instance_created=None, cleanup_ok=None)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for stream, target, limit in ((process.stdout, output, MAX_STDOUT),
                                          (process.stderr, error_output, MAX_STDERR)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (target, limit))
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report.update(status="timed_out", reason="query_deadline")
                    forced = True
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    try:
                        chunk = os.read(key.fd, 16384)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target, limit = key.data
                    if len(target) + len(chunk) > limit:
                        report.update(reason="query_output_limit")
                        forced = True
                        break
                    target.extend(chunk)
                if forced:
                    break
        if not forced:
            if process.returncode < 0:
                report.update(reason="query_terminated_by_signal", worker_signal=-process.returncode,
                              cleanup_ok=False)
            else:
                try:
                    native = json.loads(output)
                    if (not isinstance(native, dict) or
                            native.get("report_kind") != "sightcircuit_capture_gpu_query" or
                            native.get("schema_version") != 1 or
                            type(native.get("exit_code")) is not int or
                            native["exit_code"] != process.returncode or
                            native.get("status") not in ("queried", "query_failed") or
                            not isinstance(native.get("devices"), list) or
                            any(type(native.get(field)) is not bool for field in (
                                "cleanup_ok", "vulkan_instance_created", "vulkan_instance_destroyed")) or
                            any(native.get(field) is not False for field in (
                                "camera_opened", "logical_device_created", "external_memory_imported",
                                "layer_installation_performed", "deployment_ready")) or
                            (native["vulkan_instance_created"] and
                             not native["vulkan_instance_destroyed"] and native["cleanup_ok"]) or
                            (native["status"] == "queried" and
                             (process.returncode != 0 or not native["devices"] or
                              not native["vulkan_instance_created"] or not native["cleanup_ok"])) or
                            (native["status"] == "query_failed" and process.returncode != 1)):
                        raise ValueError("inconsistent query report")
                    report.update(native=native, status=native["status"],
                                  cleanup_ok=native["cleanup_ok"],
                                  vulkan_instance_created=native["vulkan_instance_created"],
                                  exit_code=process.returncode)
                except (TypeError, ValueError):
                    report.update(reason="invalid_query_report", cleanup_ok=False)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
        forced = process is not None
    except OSError as error:
        report.update(reason="query_launch_or_read_failed", error=str(error), cleanup_ok=False)
        forced = process is not None
    finally:
        if process is not None:
            if forced or process.poll() is None:
                forced = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    report.update(reason="query_did_not_exit")
                report["cleanup_ok"] = False
            report.update(worker_exit_code=process.poll(), worker_reaped=process.poll() is not None)
            process.stdout.close()
            process.stderr.close()
        report.update(worker_forced_termination=forced,
                      elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        # Diagnostics are bounded and retained as text, never a second JSON document.
        if error_output:
            report["driver_stderr"] = error_output.decode("utf-8", errors="replace")
        if report["status"] != "cancelled":
            report["exit_code"] = 0 if report["status"] == "queried" and report["cleanup_ok"] else 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-device", action="store_true",
                        help="initialize Vulkan in a supervised child and query physical-device capabilities")
    args = parser.parse_args(argv)
    if args.query_device:
        previous = {}
        def stop(number, _frame):
            raise StopRequested(number)
        try:
            for number in (signal.SIGINT, signal.SIGTERM):
                previous[number] = signal.signal(number, stop)
            report = run_query()
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    else:
        report = new_report()
        report["required_argument"] = "--query-device"
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
