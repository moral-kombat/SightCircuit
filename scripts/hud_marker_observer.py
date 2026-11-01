"""Bounded empirical marker-copy observation, never a production calibration.

Native-resolution capture is retained. Only candidate quads are rectified to
check the fixed marker. No eye identity or unseen/occluded copy is inferred.
The copy-location algorithm lives in ``framejacker.hud.observation``; this
command runs the bounded trial, its marker and its report.
"""
import argparse
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.gates import claims  # noqa: E402
from framejacker.hud import HUD_CONFIG  # noqa: E402

from framejacker.hud.observation import (  # noqa: E402
    BaselineGate, EmpiricalObservation, ObserverConfig, native_bgr)
from framejacker.hud.marker import (  # noqa: E402
    MarkerClient, new_report as marker_report, validate_worker_report as validate_marker_report)
from framejacker.runtime.guard import supervise  # noqa: E402
from framejacker.sources.mirror import capture  # noqa: E402
from framejacker.sources.mirror_device import discover, select_candidate  # noqa: E402

BASELINE_WINDOW_SECONDS = 2.0


def new_report(validation_only=False):
    return {"report_kind": "hud_marker_observation", "status": "not_run", "exit_code": 3,
            "calibration_validation_only": validation_only,
            "cleanup_ok": True, "images_saved": False, "inference_started": False,
            **claims(), "physical_display_recovery_verified": False,
            "camera_freshness_verified": False,
            "scope": "Empirical visible-copy bounds only; unknown composition cannot qualify production masking"}


def validate_marker_evidence(bridge, seconds, validation_only):
    """Reuse the finalized marker deadline contract, then pin every HUD setting."""
    report = marker_report(validation_only)
    report.update(status="completed", exit_code=0, cleanup_ok=True, seconds_requested=seconds, bridge=bridge)
    validate_marker_report(report, 0, validation_only=validation_only)
    marker = bridge["native_report"].get("marker") or {}
    expected = {"width": HUD_CONFIG.width_px, "height": HUD_CONFIG.height_px,
                "opaque_pixels": HUD_CONFIG.width_px * HUD_CONFIG.height_px,
                "corner_ids": [1, 2, 3, 4], "border_pixels": 4, "tag_size_pixels": 24, "tag_inset_pixels": 8,
                "config_id": HUD_CONFIG.layout_id, "corner_order": "top_left,top_right,bottom_left,bottom_right",
                "visible_in_dashboard": True}
    if (any(marker.get(key) != value for key, value in expected.items()) or
            any(type(marker.get(key)) not in (int, float) or
                not math.isclose(marker[key], getattr(HUD_CONFIG, key), rel_tol=0, abs_tol=1e-6)
                for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"))):
        raise ValueError("native_marker_config_mismatch")


def observe(candidate, seconds, stop, *, backend_factory, marker_client, config=ObserverConfig(),
            allow_mirror_observation=False, allow_calibration_output=False):
    """One combined bounded trial; backend and marker are injected for offline tests."""
    if type(seconds) is not int or not 1 <= seconds <= 20:
        raise ValueError("observer_duration_out_of_bounds")
    config.validate()
    if backend_factory is None and allow_mirror_observation is not True:
        raise ValueError("observer_mirror_opt_in_required")
    if backend_factory is not None and getattr(backend_factory, "kind", None) != "synthetic":
        raise ValueError("observer_fixture_backend_required")
    if marker_client.validation_only is not True and allow_calibration_output is not True:
        raise ValueError("observer_output_opt_in_required")
    report = new_report(marker_client.validation_only)
    observation = EmpiricalObservation(config)
    started = time.monotonic()
    deadline = started + seconds
    baseline = BaselineGate(min(deadline, started + BASELINE_WINDOW_SECONDS))
    marker_started = False
    baseline_descriptor = None
    first_marker_deadline = None
    try:
        def on_frame(data, fmt, sequence, observed_ns):
            nonlocal marker_started, baseline_descriptor, first_marker_deadline
            if time.monotonic() >= deadline or stop.is_set():
                return
            image, descriptor = native_bgr(data, fmt)
            if baseline_descriptor is None:
                baseline_descriptor = descriptor
            if descriptor != baseline_descriptor:
                raise ValueError("observer_format_changed")
            if not marker_started:
                ready = baseline.accept(image, sequence, observed_ns, time.monotonic())
                if stop.is_set() or time.monotonic() >= baseline.deadline:
                    raise ValueError("baseline_processing_exceeded_observation_deadline")
                if not ready:
                    return
                marker_client.start_marker(seconds, stop, allow_calibration_output=allow_calibration_output)
                marker_started = True
                first_marker_deadline = min(deadline, time.monotonic() + 2)
                report["baseline_sequence"] = sequence
                return
            # No inference or downscaled analysis image: these are native corners.
            try:
                observation.accept(image, descriptor, sequence, observed_ns, config_id=config.config_id)
            except ValueError as error:
                if str(error) == "no_complete_marker" and not observation.samples and time.monotonic() < first_marker_deadline:
                    report["warmup_frames_without_marker"] = report.get("warmup_frames_without_marker", 0) + 1
                    return
                raise
            if time.monotonic() >= deadline:
                raise ValueError("marker_processing_exceeded_observation_deadline")
        def on_progress(_report):
            if not marker_started:
                baseline.check_deadline(time.monotonic())
        captured, _ = capture(candidate, seconds, stop, progress=on_progress,
                                           backend_factory=backend_factory, on_frame=on_frame, max_fps=5, deadline=deadline)
        report["capture"] = captured
        if captured["status"] == "error":
            raise ValueError(captured.get("error", captured.get("reason")))
        if stop.is_set():
            report.update(status="cancelled", exit_code=130)
        elif not marker_started:
            raise ValueError("baseline_distinct_clear_frames_not_established")
        elif len(observation.samples) < 3:
            raise ValueError("insufficient_complete_marker_samples")
        else:
            report.update(status="observed_unqualified", exit_code=2,
                          reason="composition_and_unseen_content_not_established")
        # Allow the marker's own timer to close normally; never read more frames.
        while (marker_started and marker_client.process.poll() is None and not stop.is_set()
               and time.monotonic() < deadline + 2):
            stop.wait(.02)
    except Exception as error:
        report.update(status="cancelled" if stop.is_set() else "rejected", exit_code=130 if stop.is_set() else 1,
                      reason=str(error)[:1024])
    finally:
        report.update(observation.evidence())
        report.update(baseline=dict(baseline.report), baseline_initial_match_diagnostics=baseline.initial_diagnostics,
                      baseline_match_diagnostics=baseline.last_diagnostics,
                      baseline_window_seconds=min(seconds, BASELINE_WINDOW_SECONDS))
        try:
            bridge = marker_client.close()
            report["bridge"] = bridge
            native = bridge.get("native_report") or {}
            marker_clean = (marker_client.process is None and not marker_started) or (
                bridge.get("worker_reaped") is True and bridge.get("worker_exit_code") == 0 and
                bridge.get("transport_threads_stopped") is True and not bridge.get("forced_termination") and
                bridge.get("error") is None and native.get("cleanup_ok") is True and native.get("exit_code") == 0)
            report["cleanup_ok"] = (report.get("capture", {}).get("cleanup_ok") is True and
                                    marker_clean)
            if report["exit_code"] == 2 and marker_clean:
                try:
                    validate_marker_evidence(bridge, seconds, marker_client.validation_only)
                except (ValueError, TypeError, KeyError) as error:
                    report.update(status="rejected", exit_code=1, reason=str(error)[:1024])
        except Exception as error:
            report.update(cleanup_ok=False, cleanup_error=str(error)[:1024])
        if not report["cleanup_ok"] and report["exit_code"] == 2:
            report.update(status="cleanup_failed", exit_code=1)
        report["seconds_requested"] = seconds
        report["maximum_processing_fps"] = 5
    if len(json.dumps(report)) > 128 * 1024:
        report.update(samples=[], status="report_budget_exceeded", exit_code=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-calibration", action="store_true")
    parser.add_argument("--allow-mirror-observation", action="store_true")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--bridge", type=Path, help="Exact reviewed native bridge; defaults to the existing bridge path")
    parser.add_argument("--seconds", type=int, choices=range(1, 21), default=20)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        options = json.loads(args.worker)
        if options.get("approved_scope") != "marker_observation_only":
            raise ValueError("observer_explicit_scope_required")
        stop = threading.Event()
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, lambda *_: stop.set())
        client = MarkerClient(options["bridge"], validation_only=False) if options.get("bridge") else MarkerClient(validation_only=False)
        report = observe(options["candidate"], options["seconds"], stop, backend_factory=None,
                         marker_client=client, allow_mirror_observation=True,
                         allow_calibration_output=True)
    else:
        opted = (args.observe_calibration, args.allow_mirror_observation, args.allow_calibration_output, bool(args.device))
        if any(opted) and not all(opted):
            parser.error("calibration observation requires all three explicit flags and a verified --device")
        if args.bridge and not all(opted):
            parser.error("--bridge requires the complete calibration opt-in")
        report = new_report()
        if all(opted):
            candidate = select_candidate(discover(), args.device)
            options = {"approved_scope": "marker_observation_only", "candidate": candidate, "seconds": args.seconds,
                       "bridge": str(args.bridge.resolve()) if args.bridge else None}
            report = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)],
                new_report(), args.seconds + 12, validate_worker_report, descendants=True)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


def validate_worker_report(report, returncode):
    if (not isinstance(report, dict) or report.get("report_kind") != "hud_marker_observation" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (1, 2, 130) or report["exit_code"] != returncode or
            type(report.get("cleanup_ok")) is not bool or
            type(report.get("calibration_validation_only")) is not bool or
            type(report.get("seconds_requested")) is not int or not 1 <= report["seconds_requested"] <= 20 or
            report.get("maximum_processing_fps") != 5 or
            any(report.get(key) is not False for key in (*claims(), "images_saved", "inference_started",
                "physical_display_recovery_verified",
                "eye_identity_assigned", "composition_verified", "unseen_or_occluded_copies_established",
                "camera_freshness_verified")) or
            (report["exit_code"] == 2 and (report.get("status") != "observed_unqualified" or not report["cleanup_ok"] or
                                           not 3 <= report.get("sample_count", 0) <= 100))):
        raise ValueError("invalid_observer_worker_report")
    if report["exit_code"] == 2:
        baseline = report.get("baseline") or {}
        if (baseline.get("established") is not True or baseline.get("camera_freshness_verified") is not False or
                baseline.get("clear_frames", 0) < 2 or baseline.get("content_changes", 0) < 1):
            raise ValueError("invalid_observer_baseline_evidence")
        validate_marker_evidence(report.get("bridge") or {}, report["seconds_requested"],
                                 report["calibration_validation_only"])


if __name__ == "__main__":
    sys.exit(main())
