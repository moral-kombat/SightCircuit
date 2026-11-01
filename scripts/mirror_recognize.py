"""Bounded CPU recognition from a verified SteamVR mirror, without live display."""
import argparse
import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.convert import to_bgr as convert_frame
from framejacker.errors import FrameFormatError
from framejacker.frame import Frame
from framejacker.hud import CaptureFormat, HudMask, HUD_CONFIG
from framejacker.latest import LatestSlot

if __package__:
    from . import mirror_check as check
    from .demo_detector import DemoDetector
    from .mirror_capture import MirrorError, discover, select_candidate
else:
    import mirror_check as check
    from demo_detector import DemoDetector
    from mirror_capture import MirrorError, discover, select_candidate


def new_report():
    return {"report_kind": "steamvr_mirror_recognition", "schema_version": 1,
            "status": "not_started", "cleanup_ok": True, "images_saved": False,
            "frames_processed": 0, "labels_seen": [], "stale_inputs_skipped": 0,
            "display_started": False, "overlay_requested": False,
            "maximum_pending_frames": 1, "maximum_input_observation_age_ms": 1000,
            "physical_camera_identity": "unverified", "camera_timestamps_available": False,
            "confidence_is_calibrated_probability": False,
            "scope": "Composited mirror recognition; source identity, camera freshness and display feedback unverified"}


@dataclass(frozen=True)
class ObservedFrame:
    data: bytes
    format: dict
    read_sequence: int
    observed_ns: int


class LatestFrame(LatestSlot):
    """One pending owned read; slower inference replaces old input, never queues it."""
    def __init__(self):
        super().__init__()
        self.done = threading.Event()
        self.report = {}

    def publish(self, data, fmt, number, observed_ns):
        self.put(ObservedFrame(bytes(data), dict(fmt), number, observed_ns))

    def take(self, timeout=0.05):
        return super().take(0 if self.done.is_set() else timeout)

    def update(self, report):
        with self.condition:
            self.report = report.copy()

    def snapshot(self):
        with self.condition:
            return self.report.copy(), self.replaced


def to_bgr(frame, *, hud=None, source_id=None, capture_layout=None, hud_config=HUD_CONFIG):
    fmt = frame.format
    native_filter = None
    if hud is not None:
        descriptor = CaptureFormat.from_mapping(fmt)
        hud.check_context(source_id, descriptor, capture_layout, hud_config)
        def native_filter(image):
            return hud.prepare(image, source_id=source_id, capture_format=descriptor,
                               capture_layout=capture_layout, config=hud_config)
    if len(frame.data) < fmt["minimum_buffer_bytes"]:
        raise MirrorError("short_frame", "Incomplete mirror image")
    native = Frame(frame.data, fmt["fourcc"], fmt["width"], fmt["height"], fmt["stride_bytes"],
                   source_id or "mirror:observed", frame.read_sequence, frame.observed_ns,
                   metadata={"native_format": dict(fmt)})
    try:
        return convert_frame(native, native_filter=native_filter)
    except FrameFormatError as error:
        raise MirrorError(error.reason, str(error)) from error


def recognize(candidate, seconds, stop, progress=lambda report: None, want_preview=False,
              *, max_fps=5, threshold=0.5, backend_factory=None, detector_factory=None,
              hud_mode=False, calibration=None, hud_config=HUD_CONFIG):
    report = new_report()
    report.update(candidate=candidate, phase="loading_detector", maximum_processing_fps=max_fps,
                  threshold=threshold, seconds_requested=seconds)
    progress(report.copy())
    latest = LatestFrame()
    thread = None
    last_image = None
    started = None
    total_inference = max_inference = max_age = 0.0
    labels = set()
    hud = None
    try:
        if hud_mode:
            # No CLI/live adapter enables this: fixture backends only.
            if backend_factory is None or getattr(backend_factory, "kind", None) != "synthetic":
                raise ValueError("hud_live_mirror_not_approved")
            hud = HudMask(calibration, hud_config, source_kind="synthetic")
            hud.check_context(backend_factory.source_id, calibration.capture_format,
                              backend_factory.capture_layout, hud_config)
            report.update(hud_mode=True, calibration_scope="synthetic_fixture_only",
                          hardware_mapping_verified=False, feedback_exclusion_verified=False,
                          stale_results_skipped=0)
        detector = (detector_factory or DemoDetector)()
        if stop.is_set():
            report["status"] = "cancelled"
            return report, None

        def acquire():
            def publish(data, fmt, number, observed_ns):
                if hud is not None:
                    hud.check_context(backend_factory.source_id, CaptureFormat.from_mapping(fmt),
                                      backend_factory.capture_layout, hud_config)
                latest.publish(data, fmt, number, observed_ns)
            try:
                captured, _ = check.capture(candidate, seconds, stop, latest.update,
                                            backend_factory=backend_factory, on_frame=publish)
                latest.update(captured)
            except Exception as error:
                failed = check.new_report()
                failed.update(status="error", reason="capture_thread_failed", error=str(error), cleanup_ok=False)
                latest.update(failed)
            finally:
                latest.done.set()
                with latest.condition:
                    latest.condition.notify_all()

        started = time.monotonic()
        report["phase"] = "recognizing"
        thread = threading.Thread(target=acquire, daemon=True)
        thread.start()
        next_inference, next_progress = started, started + 1
        while not stop.is_set():
            if hud is not None and latest.snapshot()[0].get("status") == "error":
                raise ValueError("hud_capture_failed_or_changed")
            now = time.monotonic()
            if now < next_inference:
                stop.wait(min(0.05, next_inference - now))
                continue
            frame = latest.take()
            if frame is None:
                if latest.done.is_set():
                    break
                continue
            age = (time.monotonic_ns() - frame.observed_ns) / 1e6
            if age > 1000:
                report["stale_inputs_skipped"] += 1
                continue
            next_inference = time.monotonic() + 1 / max_fps
            if hud is not None and getattr(backend_factory, "kind", None) != "synthetic":
                raise ValueError("hud_live_mirror_not_approved")
            prepared = to_bgr(frame, hud=hud,
                source_id=getattr(backend_factory, "source_id", None),
                capture_layout=getattr(backend_factory, "capture_layout", None), hud_config=hud_config)
            detected = detector.detect(prepared, threshold)
            if hud is not None:
                if (getattr(backend_factory, "kind", None) != "synthetic" or
                        latest.snapshot()[0].get("status") == "error"):
                    raise ValueError("hud_capture_failed_or_changed")
                hud.check_context(backend_factory.source_id, CaptureFormat.from_mapping(frame.format),
                                  backend_factory.capture_layout, hud_config)
                if detected["image"].shape != prepared.shape or detected["image"].dtype != prepared.dtype:
                    raise ValueError("hud_detection_image_mismatch")
                if stop.is_set() or (time.monotonic_ns() - frame.observed_ns) / 1e6 > 1000:
                    report["stale_results_skipped"] += 1
                    continue
                tile, rows = hud.render(detected["detections"], prepared.shape[:2], threshold)
                detected = dict(detected, image=tile, labels=[row["label"] for row in rows], detections=rows)
            image = detected["image"]
            report["frames_processed"] += 1
            labels.update(detected["labels"])
            total_inference += detected["inference_ms"]
            max_inference = max(max_inference, detected["inference_ms"])
            max_age = max(max_age, age)
            report["last_result"] = {"read_sequence": frame.read_sequence,
                                     "observation_time_ns": frame.observed_ns,
                                     "processing_started_age_ms": round(age, 3),
                                     "inference_ms": round(detected["inference_ms"], 3),
                                     "labels": detected["labels"], "detections": detected["detections"][:20],
                                     "width": image.shape[1], "height": image.shape[0],
                                     "box_coordinate_space": "approximate_image_view_direction" if hud else "resized_mirror_pixels"}
            if want_preview:
                last_image = image
            if time.monotonic() >= next_progress:
                report.update(labels_seen=sorted(labels), capture=latest.snapshot()[0])
                progress(report.copy())
                next_progress = time.monotonic() + 1
        report["status"] = "cancelled" if stop.is_set() else "recognized" if report["frames_processed"] else "no_frames"
        if report["status"] == "no_frames" and report["stale_inputs_skipped"]:
            report.update(status="stale_input", reason="all_selected_observations_stale")
    except Exception as error:
        report.update(status="error", reason=getattr(error, "reason", "recognition_failed"), error=str(error))
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=1)
            if thread.is_alive():
                report.update(status="error", reason="capture_thread_did_not_exit", cleanup_ok=False)
        captured, replaced = latest.snapshot()
        report.update(capture=captured, pending_frames_replaced=replaced, labels_seen=sorted(labels))
        if captured:
            report["cleanup_ok"] = report["cleanup_ok"] and captured["cleanup_ok"]
            if captured["status"] == "error":
                report.update(status="error", reason=captured.get("reason", "capture_failed"), error=captured.get("error", "Capture failed"))
        if started is not None:
            elapsed = time.monotonic() - started
            report.update(processing_elapsed_ms=round(elapsed * 1000, 3),
                          observed_processing_rate_hz=round(report["frames_processed"] / max(elapsed, 0.001), 3),
                          mean_inference_ms=round(total_inference / max(report["frames_processed"], 1), 3),
                          maximum_inference_ms=round(max_inference, 3), maximum_processing_started_age_ms=round(max_age, 3))
        report["phase"] = "closed"
    if report["status"] == "recognized":
        age = (time.monotonic_ns() - report["last_result"]["observation_time_ns"]) / 1e6
        report["last_result_age_after_cleanup_ms"] = round(age, 3)
        if age > 1000:
            report.update(status="stale_input", reason="last_observed_input_too_old")
    return report, last_image if report["status"] == "recognized" and report["cleanup_ok"] else None


def _worker(job, seconds, preview_temp, sender, stop, expected_parent, *, backend_factory=None, detector_factory=None):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def send(report, event="progress"):
        sender.send_bytes(json.dumps({"event": event, "report": report}).encode())

    report = new_report()
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Could not enable parent-death signaling")
        if os.getppid() != expected_parent:
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
    old_term = signal.signal(signal.SIGTERM, check._stop_signal)
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
        report = check.run_bounded(job, args.seconds, temporary, worker=_worker,
                                   report_factory=new_report, success_status="recognized", timeout=args.seconds + 15)
        report.update(discovery=discovery, candidate=candidate)
        if temporary and report.get("preview_ready") and report["exit_code"] == 0:
            os.link(temporary["path"], target, follow_symlinks=False)
            report.update(images_saved=True, preview_path=str(target))
    except (MirrorError, OSError, ValueError) as error:
        report.update(status="unavailable" if getattr(error, "reason", None) == "mirror_unavailable" else "error",
                      reason=getattr(error, "reason", "output_or_launch_failed"), error=str(error), exit_code=1)
    except (KeyboardInterrupt, check.StopRequested) as error:
        number = error.number if isinstance(error, check.StopRequested) else signal.SIGINT
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
