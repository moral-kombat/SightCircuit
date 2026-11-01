"""Fixed calibration marker: bridge client, report schema and worker-report validation.

Shared by ``hud_marker_check`` (the marker command) and ``hud_marker_observer``
(which runs the marker while observing the mirror). Validation keeps runtime
flags as observations, never as physical presentation or recovery proof.
"""
import os
import subprocess
import threading
import time

from ..overlay import OverlayClient


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
