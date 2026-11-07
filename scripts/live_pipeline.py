"""Offline live-pipeline foundation; only synthetic input is currently enabled."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, create_detector
from sightcircuit.hud import HUD_CONFIG, HudPipelineAdapter
from sightcircuit.overlay import OverlaySink
from sightcircuit.runtime.guard import stop_on_signals, supervise
from sightcircuit.runtime.live import run_pipeline
from sightcircuit.runtime.synthetic import FixtureDetector, HeadlessSink, SyntheticSource, synthetic_hud_calibration

# "real" names the default model, as before the registry choices existed.
DETECTOR_CHOICES = (*DETECTORS, "real", "fixture")


def _worker(options):
    stopped = stop_on_signals()
    try:
        source = SyntheticSource()
        name = options.get("detector", DEFAULT_DETECTOR)
        detector = FixtureDetector() if name == "fixture" else create_detector(name)
        def hung(*_args, **_kwargs):
            if options.get("test_ready_file"):
                Path(options["test_ready_file"]).write_text(str(os.getpid()))
            time.sleep(120)
        if options.get("test_fault") == "hung_detector":
            detector.detect = hung
        if options.get("test_fault") == "hung_source":
            source.read = hung
        hud_mode = bool(options.get("synthetic_hud"))
        sink = (OverlaySink(hud=HUD_CONFIG if hud_mode else None)
                if options.get("validate_overlay") else HeadlessSink())
        hud = HudPipelineAdapter(source, sink, synthetic_hud_calibration(), HUD_CONFIG) if hud_mode else None
        report = run_pipeline(source, detector, sink, options["seconds"], stop=stopped, hud=hud)
        report["detector"] = name
        report["overlay_validation_only"] = bool(options.get("validate_overlay"))
        report["sink_kind"] = "native_overlay_validation" if options.get("validate_overlay") else "headless"
        if options.get("validate_overlay"):
            report["overlay"] = getattr(sink, "report", None)
    except Exception as error:
        report = {"report_kind": "synthetic_recognition_pipeline", "status": "error", "exit_code": 1,
                  "reason": str(error)[:1024], "cleanup_ok": False}
    report.update(live_input_verified=False, feedback_exclusion_verified=False,
                  hardware_trial_ready=False, vr_runtime_initialized=False, source_kind="synthetic")
    return report


def validate_worker_report(report, returncode):
    if (not isinstance(report, dict) or report.get("report_kind") != "synthetic_recognition_pipeline" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (0, 1, 130) or
            report["exit_code"] != returncode or type(report.get("cleanup_ok")) is not bool or
            report.get("source_kind") != "synthetic" or
            any(report.get(key) is not False for key in ("live_input_verified", "feedback_exclusion_verified",
                                                        "hardware_trial_ready", "vr_runtime_initialized")) or
            (report.get("hud_mode") is True and (
                report.get("calibration_scope") != "synthetic_fixture_only" or
                report.get("hardware_mapping_verified") is not False or report.get("world_locked") is not False or
                report.get("hud_config_id") != HUD_CONFIG.layout_id)) or
            (report["exit_code"] == 0 and (report.get("status") != "completed" or not report["cleanup_ok"]))):
        raise ValueError("invalid_pipeline_worker_report")


def supervise_synthetic(options, *, startup_seconds=15, cleanup_seconds=5):
    if not 0.05 <= options.get("seconds", 0) <= 60:
        raise ValueError("synthetic_duration_out_of_bounds")
    base = {"report_kind": "synthetic_recognition_pipeline", "source_kind": "synthetic",
            "live_input_verified": False, "feedback_exclusion_verified": False,
            "hardware_trial_ready": False, "vr_runtime_initialized": False}
    return supervise([sys.executable, str(Path(__file__).resolve()), "--synthetic-worker", json.dumps(options)],
                     base, startup_seconds + options["seconds"] + cleanup_seconds,
                     validate_worker_report, descendants=bool(options.get("validate_overlay")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-check", action="store_true")
    parser.add_argument("--seconds", type=int, choices=range(1, 61), default=3)
    parser.add_argument("--validate-overlay", action="store_true", help="Native transport validation only; no OpenVR session")
    parser.add_argument("--synthetic-hud", action="store_true", help="Generated-input HUD/mask fixture only; not hardware calibration")
    parser.add_argument("--detector", choices=DETECTOR_CHOICES,
                        help=f"A registered model (default {DEFAULT_DETECTOR}; real is an alias for it) or fixture: "
                             "no detections and no model, which checks the mask, validation and transport without "
                             "a detector's view of the masked frames")
    parser.add_argument("--synthetic-worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.synthetic_worker:
        report = _worker(json.loads(args.synthetic_worker))
        print(json.dumps(report))
        return report["exit_code"]
    report = {"report_kind": "synthetic_recognition_pipeline", "status": "not_run", "exit_code": 3,
              "live_input_verified": False, "hardware_trial_ready": False}
    if (args.validate_overlay or args.synthetic_hud or args.detector) and not args.synthetic_check:
        parser.error("--validate-overlay, --synthetic-hud and --detector require --synthetic-check")
    if args.synthetic_check:
        detector = DEFAULT_DETECTOR if args.detector in (None, "real") else args.detector
        report = supervise_synthetic({"seconds": args.seconds, "validate_overlay": args.validate_overlay,
                                      "synthetic_hud": args.synthetic_hud, "detector": detector})
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    # Module invocation keeps spawn imports and the existing shared components consistent.
    sys.exit(main())
