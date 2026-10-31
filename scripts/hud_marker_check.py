"""Separately opted-in fixed calibration marker; no capture, inference or frame files."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

if __package__:
    from .bounded_worker import supervise
    from .overlay_client import OverlayClient
else:
    from bounded_worker import supervise
    from overlay_client import OverlayClient


def new_report(validation_only=True):
    return {"report_kind": "hud_calibration_marker", "status": "not_run", "exit_code": 3,
        "calibration_validation_only": validation_only, "cleanup_ok": True,
        "capture_started": False, "inference_started": False, "frames_read": 0,
        "images_saved": False, "production_live_input_enabled": False, "production_hud_enabled": False,
        "physical_display_recovery_verified": False, "mapping_qualified": False,
        "scope": "Generated fixed marker only; best-effort software deadline, not physical display recovery proof"}


class MarkerClient(OverlayClient):
    """Reuse the bounded native JSON reader/cleanup; never create a frame writer."""
    def start_marker(self, seconds, stop, *, allow_calibration_output=False):
        if type(seconds) is not int or not 1 <= seconds <= 20:
            raise ValueError("marker_duration_out_of_bounds")
        if not self.validation_only and allow_calibration_output is not True:
            raise ValueError("separate_calibration_output_opt_in_required")
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError("native_marker_bridge_not_built")
        self.mode = "--validate-calibration-marker" if self.validation_only else "--calibration-marker"
        arguments = [str(self.binary), self.mode, "--seconds", str(seconds)]
        if not self.validation_only:
            arguments.append("--allow-calibration-output")
        self.process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        env=self.env, bufsize=0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + 8
        while not self.ready.is_set() and not stop.is_set() and time.monotonic() < deadline:
            self.ready.wait(.02)
        if stop.is_set():
            raise InterruptedError("cancelled_during_marker_startup")
        if not self.acknowledged or self.error:
            raise RuntimeError(self.error or "marker_ready_timeout")
        # Native marker mode ignores stdin and creates its own opaque pixels.
        # In particular, no inherited OverlayClient._write thread is started.
        return self


def worker(options):
    seconds = options["seconds"]
    validation_only = options["validation_only"]
    if type(validation_only) is not bool or type(seconds) is not int or not 1 <= seconds <= 20:
        raise ValueError("invalid_marker_options")
    report = new_report(validation_only)
    report.update(status="error", exit_code=1, cleanup_ok=False, seconds_requested=seconds)
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stop.set())
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
            clean = (bridge.get("worker_reaped") is True and bridge.get("worker_exit_code") == 0 and
                     bridge.get("transport_threads_stopped") is True and not bridge.get("forced_termination") and
                     not bridge.get("error") and native.get("cleanup_ok") is True and native.get("exit_code") == 0)
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


def validate_visibility_evidence(native, validation_only):
    """Runtime flags are observations, never physical presentation/recovery proof."""
    visibility = native.get("visibility_after_submit") or {}
    if ((native.get("marker") or {}).get("visible_in_dashboard") is not True or
            visibility.get("scope") != "runtime_queries_not_physical_visibility" or
            visibility.get("sampled") is not (not validation_only) or
            any(type(native.get(key)) is not bool for key in ("overlay_shown_event_seen", "overlay_hidden_event_seen"))):
        raise ValueError("invalid_marker_visibility_evidence")
    if validation_only:
        if (any(visibility.get(key) is not None for key in ("api_visible", "dashboard_visible", "visible_in_dashboard")) or
                native["overlay_shown_event_seen"] or native["overlay_hidden_event_seen"]):
            raise ValueError("validation_cannot_claim_runtime_visibility")
    elif (type(visibility.get("api_visible")) is not bool or type(visibility.get("dashboard_visible")) is not bool or
          visibility.get("visible_in_dashboard") is not True):
        raise ValueError("calibration_visibility_policy_not_confirmed")


def validate_worker_report(report, returncode, *, validation_only=None):
    if (not isinstance(report, dict) or report.get("report_kind") != "hud_calibration_marker" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (0, 1, 130) or
            report["exit_code"] != returncode or type(report.get("cleanup_ok")) is not bool or
            type(report.get("seconds_requested")) is not int or not 1 <= report["seconds_requested"] <= 20 or
            type(report.get("calibration_validation_only")) is not bool or
            (validation_only is not None and report["calibration_validation_only"] != validation_only) or
            any(report.get(key) is not False for key in ("capture_started", "inference_started", "images_saved",
                "production_live_input_enabled", "production_hud_enabled", "physical_display_recovery_verified", "mapping_qualified")) or
            type(report.get("frames_read")) is not int or report["frames_read"] != 0):
        raise ValueError("invalid_marker_worker_report")
    if report["exit_code"] == 0:
        bridge = report.get("bridge") or {}
        native = bridge.get("native_report") or {}
        mode = "--validate-calibration-marker" if report["calibration_validation_only"] else "--calibration-marker"
        if (report.get("status") != "completed" or not report["cleanup_ok"] or
                native.get("report_kind") != "framejacker_overlay_bridge" or
                any(type(native.get(key)) is not int or native[key] < 0 for key in (
                    "native_started_ns", "marker_request_ns", "marker_submit_returned_ns",
                    "display_deadline_ns", "cutoff_requested_ns", "cleanup_returned_ns")) or
                bridge.get("validation_only") != report["calibration_validation_only"] or
                bridge.get("forced_termination") is not False or bridge.get("error") or
                bridge.get("worker_reaped") is not True or bridge.get("worker_exit_code") != 0 or
                bridge.get("transport_threads_stopped") is not True or native.get("cleanup_ok") is not True or
                native.get("exit_code") != 0 or
                native.get("mode") != mode or native.get("runtime_initialized") != (not report["calibration_validation_only"]) or
                native.get("status") != ("validated_marker" if report["calibration_validation_only"] else "marker_complete") or
                native.get("calibration_only") is not True or native.get("physical_display_recovery_verified") is not False or
                any(native.get(key) is not False for key in ("input_frames_accepted", "capture_started", "inference_started", "images_saved")) or
                not (native.get("native_started_ns", -1) <= native.get("marker_request_ns", -2) <=
                     native.get("marker_submit_returned_ns", -3) < native.get("display_deadline_ns", -4)) or
                native["display_deadline_ns"] - native["native_started_ns"] != report["seconds_requested"] * 1000000000 or
                native.get("cutoff_requested_ns", -1) < native["marker_submit_returned_ns"] or
                native.get("cleanup_returned_ns", -1) < native["cutoff_requested_ns"]):
            raise ValueError("inconsistent_marker_deadline_or_mode")
        validate_visibility_evidence(native, report["calibration_validation_only"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate", action="store_true", help="Generated marker and native timer only; never initializes VR")
    modes.add_argument("--calibrate", action="store_true", help="Separate real-marker path; requires explicit output opt-in")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--seconds", type=int, choices=range(1, 21), default=10)
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
            report = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)],
                new_report(args.validate), args.seconds + 12, validate_worker_report, descendants=True)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
