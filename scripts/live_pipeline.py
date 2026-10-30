"""Offline live-pipeline foundation; only synthetic input is currently enabled."""
import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time

if __package__:
    from .bounded_worker import supervise
    from .hud_mask import Calibration, CaptureFormat, Footprint, HudMask, HUD_CONFIG
else:
    from bounded_worker import supervise
    from hud_mask import Calibration, CaptureFormat, Footprint, HudMask, HUD_CONFIG


@dataclass(frozen=True)
class OwnedFrame:
    image: object
    sequence: int
    observed_ns: int
    source_id: str
    media_time: object = None

    @classmethod
    def copy_from(cls, image, sequence, observed_ns, source_id, media_time=None):
        import numpy as np
        if (image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or
                min(image.shape[:2]) < 1 or max(image.shape[:2]) > 960):
            raise ValueError("source_requires_bounded_uint8_bgr")
        if type(sequence) is not int or sequence < 1 or type(observed_ns) is not int or observed_ns < 0:
            raise ValueError("invalid_frame_identity_or_observation_time")
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 96:
            raise ValueError("invalid_source_identity")
        if media_time is not None and (not isinstance(media_time, (int, float)) or not math.isfinite(media_time)):
            raise ValueError("invalid_media_time")
        owned = np.array(image, dtype=np.uint8, order="C", copy=True)
        owned.flags.writeable = False
        return cls(owned, sequence, observed_ns, source_id, media_time)


class LatestSlot:
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.source_id = None
        self.sequence = 0
        self.replaced = 0

    def publish(self, frame):
        frame = OwnedFrame.copy_from(frame.image, frame.sequence, frame.observed_ns, frame.source_id, frame.media_time)
        with self.condition:
            if self.source_id not in (None, frame.source_id) or frame.sequence <= self.sequence:
                raise ValueError("source_changed_or_sequence_regressed")
            self.source_id, self.sequence = frame.source_id, frame.sequence
            self.replaced += self.pending is not None
            self.pending = frame
            self.condition.notify()

    def take(self, timeout=0.05):
        with self.condition:
            if self.pending is None:
                self.condition.wait(timeout)
            frame, self.pending = self.pending, None
            return frame


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


def run_pipeline(source, detector, sink, seconds, *, stop=None, max_fps=5, max_age_ms=1000, threshold=0.5,
                 hud_mode=False, calibration=None, hud_config=HUD_CONFIG):
    if getattr(source, "kind", None) != "synthetic":
        raise ValueError("live_image_source_not_approved")
    if not (0.05 <= seconds <= 60 and 1 <= max_fps <= 10 and 0 < max_age_ms <= 1000 and 0 <= threshold <= 1):
        raise ValueError("pipeline_limits_invalid")
    stop = stop or threading.Event()
    pending, done = LatestSlot(), threading.Event()
    source_state = {"cleanup_ok": False, "frames_received": 0, "error": None}
    report = {"report_kind": "synthetic_recognition_pipeline", "status": "error", "exit_code": 1,
        "source_kind": "synthetic", "live_input_verified": False, "feedback_exclusion_verified": False,
        "hardware_trial_ready": False, "frames_processed": 0, "frames_submitted": 0,
        "stale_inputs_dropped": 0, "stale_results_dropped": 0, "maximum_pending_frames": 1,
        "maximum_processing_fps": max_fps, "seconds_requested": seconds, "images_saved": False,
        "source_cleanup_ok": False, "sink_cleanup_ok": False, "cleanup_ok": False,
        "maximum_observation_age_ms": 0.0, "labels_seen": []}
    capture = None
    labels = set()
    started = None
    hud = None
    if stop.is_set():
        report.update(status="cancelled", exit_code=130, source_cleanup_ok=True, sink_cleanup_ok=True, cleanup_ok=True)
        return report
    try:
        if hud_mode:
            hud = HudMask(calibration, hud_config, source_kind=source.kind)
            hud.check_context(source.source_id, calibration.capture_format, source.capture_layout, hud_config)
            if isinstance(sink, OverlayValidationSink):
                if not isinstance(sink, HudValidationSink):
                    raise ValueError("hud_requires_fixed_validation_sink")
                sink.check_contract(hud_config)
            report.update(hud_mode=True, calibration_scope="synthetic_fixture_only",
                hardware_mapping_verified=False, mask_stage="native_pixels_before_resize",
                mask_regions_native=hud.regions, output_kind="fixed_names_and_view_arrows",
                world_locked=False, hud_config_id=hud_config.layout_id,
                hud_pixel_size=[hud_config.width_px, hud_config.height_px])
        sink.open()
        started = time.monotonic()
        deadline = started + seconds

        def acquire():
            if stop.is_set():
                source_state["cleanup_ok"] = True
                done.set()
                return
            try:
                source.open()
                while not stop.is_set() and time.monotonic() < deadline:
                    frame = source.read(stop, timeout=0.05)
                    if frame is not None:
                        if hud is not None:
                            hud.check_context(frame.source_id, CaptureFormat.bgr(frame.image),
                                              source.capture_layout, hud_config)
                        pending.publish(frame)
                        source_state["frames_received"] += 1
            except Exception as error:
                source_state["error"] = str(error)[:1024]
            finally:
                try:
                    source_state["cleanup_ok"] = source.close() is True
                except Exception as error:
                    source_state["error"] = str(error)[:1024]
                done.set()

        capture = threading.Thread(target=acquire, daemon=True)
        capture.start()
        next_inference = started
        displayed_ns = None
        while not stop.is_set() and time.monotonic() < deadline:
            if source_state["error"]:
                raise RuntimeError(source_state["error"])
            now_ns = time.monotonic_ns()
            if displayed_ns is not None and (now_ns - displayed_ns) / 1e6 > max_age_ms:
                sink.clear()
                displayed_ns = None
            if time.monotonic() < next_inference:
                stop.wait(min(0.02, max(0, next_inference - time.monotonic())))
                continue
            frame = pending.take(timeout=min(0.05, max(0, deadline - time.monotonic())))
            if frame is None:
                if done.is_set():
                    break
                continue
            age = (time.monotonic_ns() - frame.observed_ns) / 1e6
            if age < 0:
                raise ValueError("frame_observation_is_in_the_future")
            if age > max_age_ms:
                report["stale_inputs_dropped"] += 1
                continue
            next_inference = time.monotonic() + 1 / max_fps
            prepared = frame.image
            if hud is not None:
                if source.kind != "synthetic" or source.source_id != frame.source_id:
                    raise ValueError("hud_source_changed")
                if isinstance(sink, HudValidationSink):
                    sink.check_contract(hud_config)
                prepared = hud.prepare(frame.image, source_id=frame.source_id,
                    capture_format=CaptureFormat.bgr(frame.image), capture_layout=source.capture_layout,
                    config=hud_config)
            detection = detector.detect(prepared, threshold)
            report["frames_processed"] += 1
            if hud is not None:
                if source_state["error"]:
                    raise ValueError(source_state["error"])
                if source.kind != "synthetic" or source.source_id != frame.source_id:
                    raise ValueError("hud_source_changed")
                hud.check_context(frame.source_id, CaptureFormat.bgr(frame.image),
                                  source.capture_layout, hud_config)
                if isinstance(sink, HudValidationSink):
                    sink.check_contract(hud_config)
            if not math.isfinite(detection["inference_ms"]) or detection["inference_ms"] < 0:
                raise ValueError("invalid_inference_measurement")
            if detection["image"].shape != prepared.shape or detection["image"].dtype != prepared.dtype:
                raise ValueError("detection_image_does_not_match_source")
            if hud is not None:
                tile, rows = hud.render(detection["detections"], prepared.shape[:2], threshold)
                detection = dict(detection, image=tile, labels=[row["label"] for row in rows],
                                 detections=rows, hud_config=hud_config)
            # Own annotations before another detector call can reuse its storage.
            detection = dict(detection, image=detection["image"].copy(),
                             expires_ns=frame.observed_ns + int(max_age_ms * 1e6))
            age = (time.monotonic_ns() - frame.observed_ns) / 1e6
            report["maximum_observation_age_ms"] = max(report["maximum_observation_age_ms"], age)
            if age > max_age_ms or time.monotonic() >= deadline or stop.is_set():
                report["stale_results_dropped"] += 1
                continue
            sink.submit(frame, detection)
            displayed_ns = frame.observed_ns
            labels.update(detection["labels"])
            report["frames_submitted"] += 1
            report["last_result"] = {"source_id": frame.source_id, "sequence": frame.sequence,
                "observed_ns": frame.observed_ns, "media_time": frame.media_time,
                "inference_ms": detection["inference_ms"], "observation_age_ms": round(age, 3)}
            if hud is not None:
                report["last_result"]["hud_rows"] = detection["detections"]
        if source_state["error"]:
            raise RuntimeError(source_state["error"])
        if stop.is_set():
            report.update(status="cancelled", exit_code=130)
        elif report["frames_submitted"]:
            report.update(status="completed", exit_code=0)
        else:
            report.update(status="no_frames", exit_code=1)
    except Exception as error:
        report.update(status="error", exit_code=1, reason=str(error)[:1024])
    finally:
        stop.set()
        if capture is not None:
            capture.join(timeout=0.5)
        report["source_cleanup_ok"] = capture is None or (not capture.is_alive() and source_state["cleanup_ok"])
        report["sink_clear_ok"] = report["sink_close_ok"] = False
        try:
            sink.clear()
            report["sink_clear_ok"] = True
        except Exception as error:
            report["sink_clear_error"] = str(error)[:1024]
        try:
            report["sink_close_ok"] = sink.close() is True
        except Exception as error:
            report["sink_close_error"] = str(error)[:1024]
        report["sink_cleanup_ok"] = report["sink_clear_ok"] and report["sink_close_ok"]
        if source_state["error"]:
            report["source_error"] = source_state["error"]
        report.update(frames_received=source_state["frames_received"], pending_inputs_replaced=pending.replaced,
                      labels_seen=sorted(labels), elapsed_seconds=round(time.monotonic() - started, 3) if started else 0)
        report["cleanup_ok"] = report["source_cleanup_ok"] and report["sink_cleanup_ok"]
        if not report["cleanup_ok"] and report["exit_code"] == 0:
            report.update(status="cleanup_failed", exit_code=1)
    return report


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
