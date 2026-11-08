"""Fixed calibration marker: bridge client, report schema and worker-report validation.

Shared by ``hud_marker_check`` (the marker command) and ``hud_marker_observer``
(which runs the marker while observing the mirror). Validation keeps runtime
flags as observations, never as physical presentation or recovery proof.
"""
import os
import subprocess
import threading
import time

from ..gates import claims
from ..overlay import OverlayClient, VisibilityRequest, bridge_closed_cleanly, visibility_packet
from .contract import BRIDGE_READY_WAIT_SECONDS, MARKER_MAX_SECONDS, MARKER_MIN_SECONDS

# The bridge's connect budget plus its margin (hud.contract); a bridge that
# gives up on its connect names the stop before this wait ends.
READY_TIMEOUT_SECONDS = BRIDGE_READY_WAIT_SECONDS
# After the native display deadline, time allowed for native cleanup and exit.
STOP_GRACE_SECONDS = 2


def new_report(validation_only=True):
    return {"report_kind": "hud_calibration_marker", "status": "not_run", "exit_code": 3,
        "calibration_validation_only": validation_only, "cleanup_ok": True,
        "capture_started": False, "inference_started": False, "frames_read": 0,
        "images_saved": False, **claims(), "physical_display_recovery_verified": False,
        "scope": "Generated fixed marker only; best-effort software deadline, not physical display recovery proof"}


class MarkerClient(OverlayClient):
    """Reuse the bounded native JSON reader/cleanup; never create a frame writer.

    Native marker mode reads stdin for the fallback's visibility request only
    (F3a) and its display timer ignores end of input, so a requested stop
    (``close``) closes stdin and waits for the bridge's own display deadline
    plus ``STOP_GRACE_SECONDS``. Cancellation (the ``stop`` event), a ready
    timeout or an overrun escalates to SIGTERM then SIGKILL, reported as forced.
    ``request_visibility`` (F3b) writes a hide or show request of one fallback
    cycle straight to the bridge, and the inherited ``await_visibility``,
    ``visibility_outcome`` and ``cycle_lines`` read its lines as for the stream
    client. The observer and the marker command never call it.
    """
    cancel = None
    marker_deadline = None

    def __init__(self, *arguments, **options):
        super().__init__(*arguments, **options)
        self.write_lock = threading.Lock()

    def check_built(self):
        """The bridge binary exists and is executable: a preparation check, before any frame is read."""
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError("native_marker_bridge_not_built")

    def start_marker(self, seconds, stop, *, allow_calibration_output=False):
        if type(seconds) is not int or not MARKER_MIN_SECONDS <= seconds <= MARKER_MAX_SECONDS:
            raise ValueError("marker_duration_out_of_bounds")
        if not self.validation_only and allow_calibration_output is not True:
            raise ValueError("separate_calibration_output_opt_in_required")
        self.check_built()
        self.cancel = stop
        if stop.is_set():
            raise InterruptedError("cancelled_before_marker_startup")
        self.mode = "--validate-calibration-marker" if self.validation_only else "--calibration-marker"
        arguments = [str(self.binary), self.mode, "--seconds", str(seconds)]
        if not self.validation_only:
            arguments.append("--allow-calibration-output")
        self.process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        env=self.env, bufsize=0)
        if os.name == "posix":  # request_visibility's bounded write, as the stream client's writer has.
            os.set_blocking(self.process.stdin.fileno(), False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while not self.ready.is_set() and not stop.is_set() and time.monotonic() < deadline:
            self.ready.wait(.02)
        if stop.is_set():
            raise InterruptedError("cancelled_during_marker_startup")
        if not self.acknowledged or self.error:
            raise RuntimeError(self.error or "marker_ready_timeout")
        # The native timer started before its ready line, so this bounds its
        # deadline from above: a stop never escalates before the timer ends.
        # The ready line's display_deadline_ns (``self.display_deadline_ns``, the
        # bridge's own clock) is the timer itself, earlier by the connect time;
        # the measurement command (F3b) takes its margins from that.
        self.marker_deadline = time.monotonic() + seconds
        # Native marker mode takes no frame or settings packet (it creates its own opaque pixels and
        # refuses both), only the fallback's visibility request, which this client never sends.
        # In particular, no inherited OverlayClient._write thread is started.
        return self

    def request_visibility(self, cycle, visible, *, settings=None):
        """Write the show (``visible``) or hide request of fallback cycle ``cycle`` to the marker bridge.

        Marker mode takes no settings packet (its placement is fixed and the
        bridge refuses one), so ``settings`` must be None, and there is no
        writer thread: the packet is written here, bounded as the stream
        client's writes are, and the returned ``VisibilityRequest`` is awaited
        with the inherited ``await_visibility``. Raises when the bridge is not
        running or has ended.
        """
        if settings is not None:
            raise ValueError("marker_visibility_takes_no_settings")
        data = visibility_packet(cycle, visible)
        with self.write_lock:
            if (self.process is None or self.process.poll() is not None or self.stopping.is_set() or
                    self.done.is_set() or self.error):
                raise RuntimeError("marker_client_not_running")
            request = VisibilityRequest(cycle, visible, time.monotonic_ns())
            self._send(data)
            self.visibility_requests += 1
        return request

    def _request_stop(self):
        """Close stdin as the stop request; marker mode ignores the close and keeps its timer."""
        self.process.stdin.close()
        return True

    def _wait_cooperative(self):
        """Wait for the native deadline plus a grace; cancellation stops the wait.

        Without an accepted ready line the bridge may still be initializing and
        could show the marker after the run failed, so escalate at once unless
        its final report shows it is already exiting.
        """
        if self.marker_deadline is None and self.final_report is None:
            return False
        start = self.marker_deadline if self.marker_deadline is not None else time.monotonic()
        limit = start + STOP_GRACE_SECONDS
        while self.cancel is None or not self.cancel.is_set():
            try:
                self.process.wait(timeout=.02)
                return True
            except subprocess.TimeoutExpired:
                if time.monotonic() >= limit:
                    break
        return False


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
            type(report.get("seconds_requested")) is not int or not MARKER_MIN_SECONDS <= report["seconds_requested"] <= MARKER_MAX_SECONDS or
            type(report.get("calibration_validation_only")) is not bool or
            (validation_only is not None and report["calibration_validation_only"] != validation_only) or
            any(report.get(key) is not False for key in ("capture_started", "inference_started", "images_saved",
                *claims(), "physical_display_recovery_verified")) or
            type(report.get("frames_read")) is not int or report["frames_read"] != 0):
        raise ValueError("invalid_marker_worker_report")
    if report["exit_code"] == 0:
        bridge = report.get("bridge") or {}
        native = bridge.get("native_report") or {}
        mode = "--validate-calibration-marker" if report["calibration_validation_only"] else "--calibration-marker"
        if (report.get("status") != "completed" or not report["cleanup_ok"] or
                native.get("report_kind") != "sightcircuit_overlay_bridge" or
                any(type(native.get(key)) is not int or native[key] < 0 for key in (
                    "native_started_ns", "marker_request_ns", "marker_submit_returned_ns",
                    "display_deadline_ns", "cutoff_requested_ns", "cleanup_returned_ns")) or
                bridge.get("validation_only") != report["calibration_validation_only"] or
                not bridge_closed_cleanly(bridge) or
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
