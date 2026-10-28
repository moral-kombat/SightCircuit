"""Bounded CPU recognition from a verified SteamVR mirror, without live display."""
import argparse
import json
import math
from pathlib import Path
import signal
import sys
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framejacker.convert import to_bgr
from framejacker.detection import DEFAULT_DETECTOR, DETECTORS, create_detector, describe
from framejacker.errors import FramejackerError
from framejacker.pipeline import Pipeline
from framejacker.runtime import output, process
from framejacker.runtime.process import StopRequested, run_bounded
from framejacker.sources.mirror import MirrorSource
from scripts import mirror_check as check
from scripts.mirror_capture import discover, select_candidate

MAXIMUM_INPUT_AGE_MS = 1000


def new_report():
    return {"report_kind": "steamvr_mirror_recognition", "schema_version": 1,
            "status": "not_started", "cleanup_ok": True, "images_saved": False,
            "frames_processed": 0, "labels_seen": [], "stale_inputs_skipped": 0,
            "display_started": False, "overlay_requested": False,
            "maximum_pending_frames": 1, "maximum_input_observation_age_ms": MAXIMUM_INPUT_AGE_MS,
            "physical_camera_identity": "unverified", "camera_timestamps_available": False,
            "confidence_is_calibrated_probability": False,
            "scope": "Composited mirror recognition; source identity, camera freshness and display feedback unverified"}


class RecognitionRecorder:
    """Pipeline sink: last result, optional preview image and one progress report per second."""

    def __init__(self, report, stats, source, progress, want_preview):
        self.report, self.stats, self.source = report, stats, source
        self.progress, self.want_preview = progress, want_preview
        self.image = self.last = None
        self.next_progress = time.monotonic() + 1

    def submit(self, processed):
        frame, image = processed.frame, processed.image
        self.report["frames_processed"] = self.stats.frames_processed
        self.report["last_result"] = {
            "read_sequence": frame.sequence, "observation_time_ns": frame.observed_ns,
            "processing_started_age_ms": round(processed.input_age_ms, 3),
            "inference_ms": round(processed.result.inference_ms, 3),
            "labels": processed.labels,
            "detections": [detection.as_dict() for detection in processed.result.detections[:20]],
            "width": image.shape[1], "height": image.shape[0],
            "box_coordinate_space": "resized_mirror_pixels"}
        if self.want_preview:
            self.image, self.last = image, processed
        if time.monotonic() >= self.next_progress:
            self.report.update(labels_seen=sorted(self.stats.labels_seen), capture=self.source.report)
            self.progress(self.report.copy())
            self.next_progress = time.monotonic() + 1


def mirror_acquirer(candidate, seconds, backend_factory=None):
    """Build the ``MirrorSource`` acquisition callback for a verified candidate."""
    def acquire(stop, on_frame, update):
        try:
            captured, _ = check.capture(candidate, seconds, stop, update,
                                        backend_factory=backend_factory, on_frame=on_frame)
            return captured
        except Exception as error:
            failed = check.new_report()
            failed.update(status="error", reason="capture_thread_failed", error=str(error), cleanup_ok=False)
            return failed

    return acquire


def recognize(candidate, seconds, stop, progress=lambda report: None, want_preview=False,
              *, max_fps=5, threshold=0.5, detector=DEFAULT_DETECTOR, backend_factory=None, detector_factory=None):
    """Run bounded recognition; return the report and, on success, final images.

    Images are ``{"preview": annotated, "still": unannotated detector input}``
    for the last processed frame when ``want_preview`` is set, otherwise None.
    """
    report = new_report()
    report.update(candidate=candidate, phase="loading_detector", maximum_processing_fps=max_fps,
                  threshold=threshold, seconds_requested=seconds)
    progress(report.copy())
    source = pipeline = recorder = None
    try:
        model = detector_factory() if detector_factory else create_detector(detector)
        if not detector_factory:
            report["detector"] = describe(model)
        if stop.is_set():
            report["status"] = "cancelled"
            return report, None

        source = MirrorSource(mirror_acquirer(candidate, seconds, backend_factory))
        pipeline = Pipeline(source, model, threshold=threshold, max_fps=max_fps, latest_only=True,
                            max_input_age_ms=MAXIMUM_INPUT_AGE_MS)
        recorder = RecognitionRecorder(report, pipeline.stats, source, progress, want_preview)
        pipeline.sinks = (recorder,)
        report["phase"] = "recognizing"
        stats = pipeline.run(stop)
        report["status"] = "cancelled" if stats.cancelled else "recognized" if stats.frames_processed else "no_frames"
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
                report.update(status="error", reason=captured.get("reason", "capture_failed"),
                              error=captured.get("error", "Capture failed"))
        if pipeline is not None:
            stats = pipeline.stats
            report.update(frames_processed=stats.frames_processed, stale_inputs_skipped=stats.stale_inputs_skipped,
                          pending_frames_replaced=stats.pending_replaced, labels_seen=sorted(stats.labels_seen),
                          processing_elapsed_ms=round(stats.elapsed_s * 1000, 3),
                          observed_processing_rate_hz=round(stats.frames_processed / max(stats.elapsed_s, 0.001), 3),
                          mean_inference_ms=round(stats.mean_inference_ms, 3),
                          maximum_inference_ms=round(stats.max_inference_ms, 3),
                          mean_processing_ms=round(stats.mean_processing_ms, 3),
                          maximum_processing_started_age_ms=round(stats.max_input_age_ms, 3))
        else:
            report["pending_frames_replaced"] = 0
        report["phase"] = "closed"
    if report["status"] == "recognized":
        age = (time.monotonic_ns() - report["last_result"]["observation_time_ns"]) / 1e6
        report["last_result_age_after_cleanup_ms"] = round(age, 3)
        if age > MAXIMUM_INPUT_AGE_MS:
            report.update(status="stale_input", reason="last_observed_input_too_old")
    if report["status"] != "recognized" or not report["cleanup_ok"] or recorder is None or recorder.last is None:
        return report, None
    return report, {"preview": recorder.image, "still": to_bgr(recorder.last.frame)}


def encode_png(image):
    import cv2

    okay, png = cv2.imencode(".png", image)
    if not okay:
        raise RuntimeError("PNG encoding failed")
    return png.tobytes()


def _worker(job, seconds, outputs, sender, stop, expected_parent, *, backend_factory=None, detector_factory=None):
    """``outputs`` maps "preview"/"still" to reservations made by the launcher."""
    report = new_report()
    outputs = outputs or {}
    try:
        if not process.enter_worker(expected_parent):
            return
        report, images = recognize(job["candidate"], seconds, stop, lambda update: process.send_report(sender, update),
                                   bool(outputs), max_fps=job["max_fps"], threshold=job["threshold"],
                                   detector=job.get("detector", DEFAULT_DETECTOR),
                                   backend_factory=backend_factory, detector_factory=detector_factory)
        if images is not None and "preview" in outputs:
            import cv2

            image = images["preview"]
            ratio = min(1.0, 640 / max(image.shape[:2]))
            if ratio < 1:
                image = cv2.resize(image, (max(1, round(image.shape[1] * ratio)),
                                           max(1, round(image.shape[0] * ratio))), interpolation=cv2.INTER_AREA)
            output.write(outputs["preview"], encode_png(image))
            report.update(preview_ready=True, preview_width=image.shape[1], preview_height=image.shape[0])
        if images is not None and "still" in outputs:
            # Unannotated, at the detector's input size, for building evaluation sets.
            output.write(outputs["still"], encode_png(images["still"]))
            report.update(still_ready=True, still_width=images["still"].shape[1],
                          still_height=images["still"].shape[0])
        process.send_report(sender, report, "result")
    except Exception as error:
        report.update(status="error", reason="worker_failed", error=str(error))
        try:
            process.send_report(sender, report, "result")
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
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR,
                        help="Model; mobilenet-ssd is the device-validated baseline, yolox-nano an 80-class candidate")
    parser.add_argument("--preview", type=Path, help="Explicit new annotated PNG, published only after capture/inference stops")
    parser.add_argument("--still", type=Path,
                        help="Explicit new unannotated PNG of the last processed input, for evaluation sets")
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if not args.capture and (args.device or args.preview or args.still):
        parser.error("--device, --preview and --still require --capture")
    if args.preview and args.still and args.preview.expanduser().absolute() == args.still.expanduser().absolute():
        parser.error("--preview and --still need distinct paths")
    discovery = discover()
    report = new_report()
    report.update(status="discovery_only", discovery=discovery, exit_code=0)
    if not args.capture:
        print(json.dumps(report, indent=2))
        return 0
    outputs = {}
    old_term = signal.signal(signal.SIGTERM, process.stop_on_signal)
    try:
        candidate = select_candidate(discovery, args.device)
        if args.preview:
            outputs["preview"] = output.reserve(args.preview, ".framejacker-recognition-")
        if args.still:
            outputs["still"] = output.reserve(args.still, ".framejacker-still-")
        job = {"candidate": candidate, "max_fps": args.max_fps, "threshold": args.threshold,
               "detector": args.detector}
        report = run_bounded(job, args.seconds, outputs or None, worker=_worker, report_factory=new_report,
                             success_status="recognized", timeout=args.seconds + 15)
        report.update(discovery=discovery, candidate=candidate, detector_requested=args.detector)
        if report["exit_code"] == 0 and all(report.get(f"{kind}_ready") for kind in outputs):
            for kind, reservation in outputs.items():
                report[f"{kind}_path"] = output.publish(reservation)
                report["images_saved"] = True
    except (FramejackerError, OSError, ValueError) as error:
        report.update(status="unavailable" if getattr(error, "reason", None) == "mirror_unavailable" else "error",
                      reason=getattr(error, "reason", "output_or_launch_failed"), error=str(error), exit_code=1)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
    finally:
        for reservation in outputs.values():
            try:
                output.discard(reservation)
            except OSError as error:
                report.update(status="error", reason="preview_cleanup_failed", error=str(error), exit_code=1)
        signal.signal(signal.SIGTERM, old_term)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
