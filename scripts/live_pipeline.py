"""Offline live-pipeline foundation; only synthetic input is currently enabled."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.frame import OwnedFrame  # noqa: F401 - compatibility export
from framejacker.latest import LatestFrameSlot as LatestSlot  # noqa: F401 - compatibility export
from framejacker.runtime.live import run_pipeline as run_bounded_pipeline

if __package__:
    from .bounded_worker import supervise
    from .hud_mask import Calibration, CaptureFormat, Footprint, HudMask, HUD_CONFIG
else:
    from bounded_worker import supervise
    from hud_mask import Calibration, CaptureFormat, Footprint, HudMask, HUD_CONFIG

class SyntheticSource:
    kind = "synthetic"
    source_id = "synthetic:generated"
    capture_layout = "mono"

    def __init__(self, rate=30):
        import numpy as np
        self.np = np  # Dependency startup occurs before the bounded acquisition interval.
        self.period = 1 / rate
        self.sequence = 0
        self.next_frame = 0
        self.closed = False

    def open(self):
        self.next_frame = time.monotonic()

    def read(self, stop, timeout=0.05):
        delay = self.next_frame - time.monotonic()
        if delay > 0:
            stop.wait(min(timeout, delay))
            return None
        self.sequence += 1
        self.next_frame = time.monotonic() + self.period
        pixels = self.np.full((64, 96, 3), self.sequence % 255, dtype=self.np.uint8)
        return OwnedFrame.copy_from(pixels, self.sequence, time.monotonic_ns(), self.source_id)

    def close(self):
        self.closed = True
        return True


class FixtureDetector:
    def detect(self, image, threshold):
        return {"image": image.copy(), "labels": [], "detections": [], "inference_ms": 0.0}


class HeadlessSink:
    def open(self):
        self.count = self.clears = 0

    def submit(self, frame, detection):
        self.count += 1

    def clear(self):
        self.clears += 1

    def close(self):
        return True


class OverlayValidationSink:
    """Exercise the existing transport; this adapter cannot open a real VR panel."""
    def __init__(self, client_factory=None):
        self.client_factory = client_factory
        self.client = None

    def open(self):
        if __package__:
            from .overlay_client import OverlayClient, PanelSettings
        else:
            from overlay_client import OverlayClient, PanelSettings
        self.settings_type = PanelSettings
        self.client = (self.client_factory or OverlayClient)(validation_only=True)
        self.client.start()

    def submit(self, frame, detection):
        if self.client.error:
            raise RuntimeError(self.client.error)
        self.client.set_settings(self.settings_type(visible=True))
        self.client.submit_bgr(detection["image"], expires_ns=detection["expires_ns"])

    def clear(self):
        if self.client is not None:
            self.client.set_settings(self.settings_type(visible=False))

    def close(self):
        if self.client is None:
            return True
        report = self.client.close()
        self.report = report
        native = report.get("native_report") or {}
        return (report.get("validation_only") is True and not report.get("error") and
                report.get("forced_termination") is False and report.get("worker_reaped") is True and
                report.get("worker_exit_code") == 0 and report.get("transport_threads_stopped") is True and
                native.get("cleanup_ok") is True and native.get("exit_code") == 0 and
                native.get("mode") == "--validate-stdin" and native.get("runtime_initialized") is False)


class HudValidationSink(OverlayValidationSink):
    """Fixed head-relative candidate placement, native validation ONLY."""
    def __init__(self, client_factory=None, config=HUD_CONFIG):
        super().__init__(client_factory)
        self.hud_config = config

    def settings(self, visible):
        c = HUD_CONFIG  # Even failure cleanup must use the frozen placement.
        return self.settings_type(width=c.panel_width_m, distance=c.distance_m,
            x=c.x_m, y=c.y_m, opacity=c.opacity, visible=visible)

    def check_contract(self, config):
        if config != HUD_CONFIG or self.hud_config != config:
            raise ValueError("hud_sink_config_changed")
        if self.client is not None:
            if self.client.validation_only is not True or self.client.settings != self.settings(self.client.settings.visible):
                raise ValueError("hud_sink_mode_or_placement_changed")

    def open(self):
        self.check_contract(HUD_CONFIG)
        super().open()
        self.client.freeze_settings(self.settings(False))

    def submit(self, frame, detection):
        self.check_contract(HUD_CONFIG)
        if self.client.error:
            raise RuntimeError(self.client.error)
        if (detection.get("hud_config") != self.hud_config or
                detection["image"].shape != (self.hud_config.height_px, self.hud_config.width_px, 3)):
            raise ValueError("hud_payload_contract_mismatch")
        self.client.set_settings(self.settings(True))
        self.client.submit_bgr(detection["image"], expires_ns=detection["expires_ns"])

    def clear(self):
        if self.client is not None:
            self.client.set_settings(self.settings(False))


def synthetic_hud_calibration():
    """Only for the fixed generated 96x64 source; no hardware calibration loader."""
    return Calibration("synthetic:generated", CaptureFormat(96, 64, "BGR3", 288, 18432, 18432),
        "mono", (Footprint(0, (68, 42, 89, 56), 3),), validated=True, provenance="synthetic_fixture")


class HudPipelineAdapter:
    """Bind the shared engine to the frozen synthetic-only HUD contract."""

    def __init__(self, source, sink, calibration, config):
        self.source, self.sink = source, sink
        self.calibration, self.config = calibration, config
        self.mask = None

    def start(self):
        self.mask = HudMask(self.calibration, self.config, source_kind=self.source.kind)
        self.mask.check_context(self.source.source_id, self.calibration.capture_format,
                                self.source.capture_layout, self.config)
        if isinstance(self.sink, OverlayValidationSink):
            if not isinstance(self.sink, HudValidationSink):
                raise ValueError("hud_requires_fixed_validation_sink")
            self.sink.check_contract(self.config)
        return dict(hud_mode=True, calibration_scope="synthetic_fixture_only",
                    hardware_mapping_verified=False, mask_stage="native_pixels_before_resize",
                    mask_regions_native=self.mask.regions, output_kind="fixed_names_and_view_arrows",
                    world_locked=False, hud_config_id=self.config.layout_id,
                    hud_pixel_size=[self.config.width_px, self.config.height_px])

    def descriptor(self, frame):
        return (CaptureFormat.from_mapping(dict(frame.native_format)) if frame.native_format
                else CaptureFormat.bgr(frame.image))

    def check_frame(self, frame):
        if self.source.kind != "synthetic" or self.source.source_id != frame.source_id:
            raise ValueError("hud_source_changed")
        self.mask.check_context(frame.source_id, self.descriptor(frame),
                                self.source.capture_layout, self.config)
        if isinstance(self.sink, HudValidationSink):
            self.sink.check_contract(self.config)

    def prepare(self, frame):
        self.check_frame(frame)
        return self.mask.prepare(frame.image, source_id=frame.source_id,
                                 capture_format=self.descriptor(frame),
                                 capture_layout=self.source.capture_layout, config=self.config)

    def render(self, detection, shape, threshold):
        tile, rows = self.mask.render(detection["detections"], shape, threshold)
        return dict(detection, image=tile, labels=[row["label"] for row in rows],
                    detections=rows, hud_config=self.config)


def run_pipeline(source, detector, sink, seconds, *, stop=None, max_fps=5, max_age_ms=1000, threshold=0.5,
                 hud_mode=False, calibration=None, hud_config=HUD_CONFIG):
    hud = HudPipelineAdapter(source, sink, calibration, hud_config) if hud_mode else None
    return run_bounded_pipeline(source, detector, sink, seconds, stop=stop, max_fps=max_fps,
                               max_age_ms=max_age_ms, threshold=threshold, hud=hud)


def _worker(options):
    stopped = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stopped.set())
    try:
        source = SyntheticSource()
        if options.get("detector", "real") == "real":
            if __package__:
                from .demo_detector import DemoDetector
            else:
                from demo_detector import DemoDetector
            detector = DemoDetector()
        else:
            detector = FixtureDetector()
        def hung(*_args, **_kwargs):
            if options.get("test_ready_file"):
                Path(options["test_ready_file"]).write_text(str(os.getpid()))
            time.sleep(120)
        if options.get("test_fault") == "hung_detector":
            detector.detect = hung
        if options.get("test_fault") == "hung_source":
            source.read = hung
        hud_mode = bool(options.get("synthetic_hud"))
        sink = ((HudValidationSink() if hud_mode else OverlayValidationSink())
                if options.get("validate_overlay") else HeadlessSink())
        report = run_pipeline(source, detector, sink, options["seconds"], stop=stopped, hud_mode=hud_mode,
                              calibration=synthetic_hud_calibration() if hud_mode else None)
        report["detector"] = options.get("detector", "real")
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
    parser.add_argument("--synthetic-worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.synthetic_worker:
        report = _worker(json.loads(args.synthetic_worker))
        print(json.dumps(report))
        return report["exit_code"]
    report = {"report_kind": "synthetic_recognition_pipeline", "status": "not_run", "exit_code": 3,
              "live_input_verified": False, "hardware_trial_ready": False}
    if (args.validate_overlay or args.synthetic_hud) and not args.synthetic_check:
        parser.error("--validate-overlay and --synthetic-hud require --synthetic-check")
    if args.synthetic_check:
        report = supervise_synthetic({"seconds": args.seconds, "validate_overlay": args.validate_overlay,
                                      "synthetic_hud": args.synthetic_hud})
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    # Module invocation keeps spawn imports and the existing shared components consistent.
    sys.exit(main())
