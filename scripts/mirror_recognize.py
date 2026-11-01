"""Bounded CPU recognition from a verified SteamVR mirror, without live display.

Runs the shared ``framejacker.pipeline.Pipeline`` over ``MirrorSource``: latest
input only, one-second input/result age limit and capture-context admission.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.detection import MobileNetSSD
from framejacker.gates import LIVE_IMAGE_INPUT
from framejacker.hud import HUD_CONFIG, HudStage
from framejacker.pipeline import Pipeline, report_fields
from framejacker.runtime import process
from framejacker.runtime.process import StopRequested, stop_on_signal
from framejacker.sources.mirror import MirrorSource, capture
from framejacker.sources.mirror_device import MirrorError, discover, select_candidate


MAX_INPUT_AGE_MS = 1000
# Schema-1 field names kept for existing reports and evidence; "pipeline" holds the canonical block.
LEGACY_FIELDS = {
    "frames_processed": "frames_processed", "stale_inputs_skipped": "stale_inputs_skipped",
    "stale_results_skipped": "stale_results_skipped", "pending_frames_replaced": "pending_replaced",
    "labels_seen": "labels_seen", "processing_elapsed_ms": "elapsed_ms",
    "observed_processing_rate_hz": "processed_fps", "mean_inference_ms": "mean_inference_ms",
    "maximum_inference_ms": "max_inference_ms", "maximum_processing_started_age_ms": "max_input_age_ms",
}


def new_report():
    return {"report_kind": "steamvr_mirror_recognition", "schema_version": 1,
            "status": "not_started", "cleanup_ok": True, "images_saved": False,
            "frames_processed": 0, "labels_seen": [], "stale_inputs_skipped": 0, "stale_results_skipped": 0,
            "pending_frames_replaced": 0,
            "display_started": False, "overlay_requested": False,
            "maximum_pending_frames": 1, "maximum_input_observation_age_ms": 1000,
            "physical_camera_identity": "unverified", "camera_timestamps_available": False,
            "confidence_is_calibrated_probability": False,
            "scope": "Composited mirror recognition; source identity, camera freshness and display feedback unverified"}


class ReportSink:
    """Pipeline sink: the report's last result, progress snapshots and the preview image."""

    def __init__(self, report, progress, source, want_preview, hud_mode):
        self.report, self.progress, self.source = report, progress, source
        self.want_preview, self.hud_mode = want_preview, hud_mode
        self.pipeline, self.image = None, None
        self.next_progress = time.monotonic() + 1

    def submit(self, processed):
        frame, result = processed.frame, processed.result
        listed = ([row.as_dict() for row in processed.hud.rows] if processed.hud is not None
                  else [item.as_dict() for item in result.detections])
        self.report["last_result"] = {
            "read_sequence": frame.sequence, "observation_time_ns": frame.observed_ns,
            "processing_started_age_ms": round(processed.input_age_ms, 3),
            "inference_ms": round(result.inference_ms, 3), "labels": processed.labels,
            "detections": listed[:20], "width": processed.image.shape[1], "height": processed.image.shape[0],
            "box_coordinate_space": processed.output_space if self.hud_mode else "resized_mirror_pixels"}
        if self.want_preview:
            self.image = processed.image
        if time.monotonic() >= self.next_progress:
            block = self.pipeline.stats.report()
            self.report.update(report_fields(block, LEGACY_FIELDS), pipeline=block, capture=self.source.report)
            # Sinks run before the engine records this result's labels.
            self.report["labels_seen"] = sorted(set(block["labels_seen"]) | set(processed.labels))
            self.progress(self.report.copy())
            self.next_progress = time.monotonic() + 1


def recognize(candidate, seconds, stop, progress=lambda report: None, want_preview=False,
              *, max_fps=5, threshold=0.5, backend_factory=None, detector_factory=None,
              hud_mode=False, calibration=None, hud_config=HUD_CONFIG):
    report = new_report()
    report.update(candidate=candidate, phase="loading_detector", maximum_processing_fps=max_fps,
                  threshold=threshold, seconds_requested=seconds)
    progress(report.copy())
    source = pipeline = sink = stage = None
    try:
        if hud_mode:
            # No CLI/live adapter enables this: fixture backends only.
            LIVE_IMAGE_INPUT.require(backend_factory is not None and getattr(backend_factory, "kind", None) == "synthetic",
                                     "hud_live_mirror_not_approved")
            # Validate the calibration before the model loads or the source opens.
            stage = HudStage(backend_factory, calibration, hud_config)
            stage.start()
            report.update(hud_mode=True, calibration_scope="synthetic_fixture_only",
                          hardware_mapping_verified=False, feedback_exclusion_verified=False)
        detector = (detector_factory or MobileNetSSD)()
        if stop.is_set():
            report["status"] = "cancelled"
            return report, None

        def acquire(capture_stop, on_frame, update):
            return capture(candidate, seconds, capture_stop, update,
                                 backend_factory=backend_factory, on_frame=on_frame)[0]

        source = MirrorSource(acquire, getattr(backend_factory, "source_id", None) or "mirror:observed",
                              identity=backend_factory)
        sink = ReportSink(report, progress, source, want_preview, hud_mode)
        pipeline = Pipeline(source, detector, (sink,), threshold=threshold, max_fps=max_fps,
                            max_input_age_ms=MAX_INPUT_AGE_MS, join_timeout=1, hud=stage)
        sink.pipeline = pipeline
        report["phase"] = "recognizing"
        stats = pipeline.run(stop)
        report["status"] = ("cancelled" if stats.cancelled else
                            "recognized" if stats.frames_submitted else "no_frames")
        if report["status"] == "no_frames" and stats.stale_inputs_skipped:
            report.update(status="stale_input", reason="all_selected_observations_stale")
    except Exception as error:
        report.update(status="error", reason=getattr(error, "reason", "recognition_failed"), error=str(error))
    finally:
        stop.set()
        if pipeline is not None and pipeline.capture_thread_alive:
            report.update(status="error", reason="capture_thread_did_not_exit", cleanup_ok=False)
        captured = source.report if source is not None else {}
        report["capture"] = captured
        if captured:
            report["cleanup_ok"] = report["cleanup_ok"] and captured["cleanup_ok"]
            if captured["status"] == "error":
                report.update(status="error", reason=captured.get("reason", "capture_failed"), error=captured.get("error", "Capture failed"))
        if pipeline is not None:
            block = pipeline.stats.report()
            report.update(report_fields(block, LEGACY_FIELDS), pipeline=block)
        report["phase"] = "closed"
    if report["status"] == "recognized":
        age = (time.monotonic_ns() - report["last_result"]["observation_time_ns"]) / 1e6
        report["last_result_age_after_cleanup_ms"] = round(age, 3)
        if age > MAX_INPUT_AGE_MS:
            report.update(status="stale_input", reason="last_observed_input_too_old")
    image = sink.image if sink is not None else None
    return report, image if report["status"] == "recognized" and report["cleanup_ok"] else None


def _worker(job, seconds, preview_temp, sender, stop, expected_parent, *, backend_factory=None, detector_factory=None):
    def send(report, event="progress"):
        process.send_report(sender, report, event)

    report = new_report()
    try:
        if not process.enter_worker(expected_parent):
            return
        report, image = recognize(job["candidate"], seconds, stop, send, bool(preview_temp),
                                  max_fps=job["max_fps"], threshold=job["threshold"],
                                  backend_factory=backend_factory, detector_factory=detector_factory)
        if image is not None and preview_temp:
            import cv2

            ratio = min(1.0, 640 / max(image.shape[:2]))
            if ratio < 1:
                image = cv2.resize(image, (max(1, round(image.shape[1] * ratio)),
                                           max(1, round(image.shape[0] * ratio))), interpolation=cv2.INTER_AREA)
            okay, png = cv2.imencode(".png", image)
            if not okay:
                raise RuntimeError("Preview encoding failed")
            fd = os.open(preview_temp["path"], os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != tuple(preview_temp["identity"]):
                    raise RuntimeError("Owned preview temporary file was replaced")
                with os.fdopen(fd, "wb") as output:
                    fd = None
                    output.write(png.tobytes())
            finally:
                if fd is not None:
                    os.close(fd)
            report.update(preview_ready=True, preview_width=image.shape[1], preview_height=image.shape[0])
        send(report, "result")
    except Exception as error:
        report.update(status="error", reason="worker_failed", error=str(error))
        try:
            send(report, "result")
        except (OSError, BrokenPipeError):
            pass
    finally:
        sender.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="Opt in to bounded mirror capture/inference; default only discovers metadata")
    parser.add_argument("--device", help="Verified SteamVR virtual node; no physical fallback")
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=10, metavar="1..10")
    parser.add_argument("--max-fps", type=int, choices=range(1, 6), default=5, metavar="1..5")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--preview", type=Path, help="Explicit new annotated PNG, published only after capture/inference stops")
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if not args.capture and (args.device or args.preview):
        parser.error("--device and --preview require --capture")
    discovery = discover()
    report = new_report()
    report.update(status="discovery_only", discovery=discovery, exit_code=0)
    if not args.capture:
        print(json.dumps(report, indent=2))
        return 0
    temporary = None
    old_term = signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        candidate = select_candidate(discovery, args.device)
        if args.preview:
            target = args.preview.expanduser().absolute()
            if target.suffix.lower() != ".png" or not target.parent.is_dir() or target.exists() or target.is_symlink():
                raise ValueError("Preview needs a new .png path in an existing directory")
            fd, name = tempfile.mkstemp(prefix=".framejacker-recognition-", suffix=".png.part", dir=target.parent)
            info = os.fstat(fd)
            os.close(fd)
            temporary = {"path": name, "identity": [info.st_dev, info.st_ino]}
        job = {"candidate": candidate, "max_fps": args.max_fps, "threshold": args.threshold}
        report = process.run_bounded(job, args.seconds, temporary, worker=_worker,
                                     report_factory=new_report, success_status="recognized", timeout=args.seconds + 15)
        report.update(discovery=discovery, candidate=candidate)
        if temporary and report.get("preview_ready") and report["exit_code"] == 0:
            os.link(temporary["path"], target, follow_symlinks=False)
            report.update(images_saved=True, preview_path=str(target))
    except (MirrorError, OSError, ValueError) as error:
        report.update(status="unavailable" if getattr(error, "reason", None) == "mirror_unavailable" else "error",
                      reason=getattr(error, "reason", "output_or_launch_failed"), error=str(error), exit_code=1)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
    finally:
        if temporary:
            try:
                info = Path(temporary["path"]).lstat()
                if (info.st_dev, info.st_ino) == tuple(temporary["identity"]):
                    Path(temporary["path"]).unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                report.update(status="error", reason="preview_cleanup_failed", error=str(error), exit_code=1)
        signal.signal(signal.SIGTERM, old_term)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
