"""Supervise metadata inspection of an already running SightCircuit provider."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

try:
    from .capture_supervisor import StopRequested, cleanup_signals
except ImportError:
    from capture_supervisor import StopRequested, cleanup_signals

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "artifacts/capture-provider-reference/sightcircuit-provider-inspect"
MAX_STDOUT = 128 * 1024
MAX_STDERR = 16 * 1024


def new_report():
    return {"report_kind": "capture_provider_check", "schema_version": 1, "status": "not_run",
            "exit_code": 3, "inspection_started": False, "metadata_only": True,
            "image_descriptors_requested": False, "image_data_accessed": False, "frames_read": 0,
            "images_saved": False, "vulkan_loaded": False, "gpu_import_attempted": False,
            "layer_installation_performed": False, "capture_contract_available": False,
            "producer_completion_observed": False, "physical_camera_identity": "unverified",
            "deployment_ready": False, "cleanup_ok": True,
            "scope": "Metadata and registry stability only; no completed-frame or external ownership proof"}


def validate(native, code):
    if (not isinstance(native, dict) or native.get("report_kind") != "sightcircuit_provider_metadata" or
            native.get("schema_version") != 1 or native.get("provider_protocol_version") != 3 or
            type(native.get("exit_code")) is not int or native["exit_code"] != code or
            (native.get("status"), code) not in (("inspected", 0), ("inspection_failed", 1)) or
            native.get("metadata_only") is not True or native.get("cleanup_ok") is not True or
            native.get("physical_camera_identity") != "unverified" or
            type(native.get("frames_read")) is not int or native["frames_read"] != 0 or
            any(native.get(field) is not False for field in (
                "image_descriptors_requested", "image_data_accessed", "images_saved", "gpu_import_attempted",
                "vulkan_loaded", "logical_device_created", "layer_installation_performed",
                "capture_contract_available", "producer_completion_observed", "deployment_ready")) or
            any(type(native.get(field)) is not bool for field in ("lease_established", "registry_unchanged"))):
        raise ValueError("invalid_metadata_report")
    records = native.get("records")
    if (not isinstance(records, list) or len(records) > 64 or
            type(native.get("record_count")) is not int or native["record_count"] != len(records) or
            any(not isinstance(entry, dict) or type(entry.get("reviewed_profile_compatible")) is not bool for entry in records) or
            type(native.get("reviewed_profile_candidates")) is not int or
            native["reviewed_profile_candidates"] != sum(entry["reviewed_profile_compatible"] for entry in records) or
            type(native.get("registry_checks")) is not int or not 0 <= native["registry_checks"] <= 128 or
            (code == 0 and (not native["lease_established"] or not native["registry_unchanged"] or
                           native["registry_checks"] == 0))):
        raise ValueError("inconsistent_metadata_report")


def inspect(*, binary=BINARY, runtime=None, seconds=1, timeout=None):
    if type(seconds) is not int or not 1 <= seconds <= 5:
        raise ValueError("inspection_duration_out_of_bounds")
    report = new_report()
    report.update(status="inspection_failed", exit_code=1)
    process = None
    forced = False
    output, diagnostics = bytearray(), bytearray()
    started = time.monotonic()
    deadline = started + (seconds + 2 if timeout is None else timeout)
    try:
        if not binary.is_file():
            report.update(reason="inspector_not_built", next_step="bash scripts/build_capture_reference.sh --provider-fixes")
            return report
        environment = dict(os.environ)
        if runtime is not None:
            environment["XDG_RUNTIME_DIR"] = str(runtime)
        process = subprocess.Popen([str(binary), "--inspect", "--seconds", str(seconds)],
                                   env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        report.update(inspection_started=True, worker_pid=process.pid, cleanup_ok=None)
        with selectors.DefaultSelector() as selector:
            for stream, target, limit in ((process.stdout, output, MAX_STDOUT),
                                          (process.stderr, diagnostics, MAX_STDERR)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (target, limit))
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report.update(status="timed_out", reason="metadata_inspection_deadline")
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
                        raise ValueError("metadata_output_limit")
                    target.extend(chunk)
        if not forced:
            native = json.loads(output)
            validate(native, process.returncode)
            report.update(native=native, status=native["status"], exit_code=process.returncode,
                          record_count=native["record_count"], reviewed_profile_candidates=native["reviewed_profile_candidates"],
                          registry_unchanged=native["registry_unchanged"], cleanup_ok=native["cleanup_ok"])
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
        forced = process is not None
    except (OSError, ValueError, TypeError) as error:
        report.update(reason=str(error), cleanup_ok=False)
        forced = process is not None
    finally:
        with cleanup_signals():
            if process is not None:
                if forced or process.poll() is None:
                    forced = True
                    for number, budget in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 1)):
                        try:
                            os.killpg(process.pid, number)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=budget)
                        except subprocess.TimeoutExpired:
                            pass
                    report["cleanup_ok"] = False
                report.update(worker_exit_code=process.poll(), worker_reaped=process.poll() is not None)
                process.stdout.close()
                process.stderr.close()
            report.update(worker_forced_termination=forced, elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            if diagnostics:
                report["worker_stderr"] = diagnostics.decode("utf-8", errors="replace")
            if report["status"] != "cancelled":
                report["exit_code"] = 0 if report["status"] == "inspected" and report["cleanup_ok"] else 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Connect to an existing v3 provider for metadata only; no installation or pixel access")
    parser.add_argument("--seconds", type=int, choices=range(1, 6), default=1)
    args = parser.parse_args(argv)
    previous = {}
    def stop(number, _frame):
        raise StopRequested(number)
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
        report = inspect(seconds=args.seconds) if args.inspect else new_report()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
