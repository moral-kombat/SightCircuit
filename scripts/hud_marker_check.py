"""Separately opted-in fixed calibration marker; no capture, inference or frame files."""
import argparse
import json
from pathlib import Path
import sys
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.hud.contract import MARKER_MAX_SECONDS, MARKER_MIN_SECONDS  # noqa: E402
from sightcircuit.hud.marker import (  # noqa: E402
    MarkerClient, new_report, validate_visibility_evidence, validate_worker_report)
from sightcircuit.overlay import bridge_closed_cleanly  # noqa: E402
from sightcircuit.runtime.guard import stop_on_signals, supervise  # noqa: E402


def worker(options):
    seconds = options["seconds"]
    validation_only = options["validation_only"]
    if type(validation_only) is not bool or type(seconds) is not int or not MARKER_MIN_SECONDS <= seconds <= MARKER_MAX_SECONDS:
        raise ValueError("invalid_marker_options")
    report = new_report(validation_only)
    report.update(status="error", exit_code=1, cleanup_ok=False, seconds_requested=seconds)
    stop = stop_on_signals()
    client = MarkerClient(validation_only=validation_only)
    started = time.monotonic()
    try:
        client.start_marker(seconds, stop, allow_calibration_output=options.get("allow_calibration_output", False))
        print(f"CALIBRATION MARKER READY; native_pid={client.process.pid}; validation_only={validation_only}. "
              "Ctrl-C aborts; there is no capture or inference.", file=sys.stderr, flush=True)
        # Native owns its <=20s activity deadline. This check detects overrun of
        # native teardown/blocked calls; the independent guardian remains outermost.
        while client.process.poll() is None and not stop.is_set() and time.monotonic() < started + seconds + 2:
            stop.wait(.02)
        if stop.is_set():
            report.update(status="cancelled", exit_code=130)
        elif client.process.poll() is None:
            report.update(status="timed_out", reason="native_marker_or_cleanup_overran")
        else:
            report.update(status="completed", exit_code=0)
    except InterruptedError as error:
        report.update(status="cancelled", exit_code=130, reason=str(error))
    except Exception as error:
        report["reason"] = str(error)[:1024]
    finally:
        try:
            bridge = client.close()
            report["bridge"] = bridge
            native = bridge.get("native_report") or {}
            report["native_cleanup_report_ok"] = native.get("cleanup_ok") is True
            clean = bridge_closed_cleanly(bridge)
            report["cleanup_ok"] = clean
            if report["status"] == "completed" and (
                    not clean or native.get("mode") != client.mode or
                    native.get("status") != ("validated_marker" if validation_only else "marker_complete")):
                report.update(status="native_failed", exit_code=1)
            if report["status"] == "completed":
                try:
                    validate_visibility_evidence(native, validation_only)
                except ValueError as error:
                    # Keep the native report and resource-cleanup result even
                    # when the requested display policy was not confirmed.
                    report.update(status="native_failed", exit_code=1, reason=str(error))
        except Exception as error:
            report.update(cleanup_ok=False, cleanup_error=str(error)[:1024])
            if report["exit_code"] == 0:
                report.update(status="cleanup_failed", exit_code=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate", action="store_true", help="Generated marker and native timer only; never initializes VR")
    modes.add_argument("--calibrate", action="store_true", help="Separate real-marker path; requires explicit output opt-in")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--seconds", type=int, choices=range(MARKER_MIN_SECONDS, MARKER_MAX_SECONDS + 1), default=10)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = worker(json.loads(args.worker))
    else:
        if args.calibrate != args.allow_calibration_output:
            parser.error("--calibrate and --allow-calibration-output must be supplied together")
        report = new_report()
        if args.validate or args.calibrate:
            options = {"seconds": args.seconds, "validation_only": args.validate,
                       "allow_calibration_output": args.allow_calibration_output}
            base = new_report(args.validate)
            report = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)],
                base, args.seconds + 12, validate_worker_report, descendants=True,
                validate_options={"validation_only": base["calibration_validation_only"]})
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
