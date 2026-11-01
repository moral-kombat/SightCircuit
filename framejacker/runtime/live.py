"""Bounded latest-only recognition engine, independent of command and HUD UI.

The only accepted acquisition kind remains synthetic. Hardware adapters and
production output require separate gates; moving this engine does not lift them.

Detectors return ``DetectionResult``; the engine annotates an owned copy, or
the ``hud`` adapter renders the tile, and sinks receive one ``Processed``.
``hud``, when given, is an adapter exposing ``start()`` (returns report fields
and sets ``mask``), ``check_sink()``, ``prepare(frame)`` and
``render(result, shape, threshold) -> (tile, HudOutput)``.
Capture-context identity, including the HUD calibration, is enforced by one
``CaptureContext`` admitted on publish, before inference, after inference and
before submission.
"""
import threading
import time

from ..context import CaptureContext
from ..convert import to_bgr
from ..detection import annotate, require_result
from ..frame import Processed
from ..latest import LatestFrameSlot as LatestSlot


def run_pipeline(source, detector, sink, seconds, *, stop=None, max_fps=5, max_age_ms=1000, threshold=0.5,
                 hud=None):
    if getattr(source, "kind", None) != "synthetic":
        raise ValueError("live_image_source_not_approved")
    if not (0.05 <= seconds <= 60 and 1 <= max_fps <= 10 and 0 < max_age_ms <= 1000 and 0 <= threshold <= 1):
        raise ValueError("pipeline_limits_invalid")
    stop = stop or threading.Event()
    pending, done = LatestSlot(), threading.Event()
    source_state = {"cleanup_ok": False, "frames_received": 0, "error": None}
    context = None

    def admit(frame):
        context.admit(frame)
        if hud is not None:
            hud.check_sink()
    report = {"report_kind": "synthetic_recognition_pipeline", "status": "error", "exit_code": 1,
        "source_kind": "synthetic", "live_input_verified": False, "feedback_exclusion_verified": False,
        "hardware_trial_ready": False, "frames_processed": 0, "frames_submitted": 0,
        "stale_inputs_dropped": 0, "stale_results_dropped": 0, "maximum_pending_frames": 1,
        "maximum_processing_fps": max_fps, "seconds_requested": seconds, "images_saved": False,
        "source_cleanup_ok": False, "sink_cleanup_ok": False, "cleanup_ok": False,
        "maximum_observation_age_ms": 0.0, "labels_seen": [],
        "total_processing_ms": 0.0, "total_inference_ms": 0.0,
        "physical_recovery_confirmed": False, "cleanup_scope": "owned_software_resources_only"}
    capture = None
    labels = set()
    started = None
    if stop.is_set():
        report.update(status="cancelled", exit_code=130, source_cleanup_ok=True, sink_cleanup_ok=True, cleanup_ok=True)
        return report
    try:
        if hud is not None:
            report.update(hud.start())
        # Pins the declared source identity before the source opens.
        context = CaptureContext(source, mask=hud.mask if hud is not None else None)
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
                        admit(frame)
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
            input_age = age
            next_inference = time.monotonic() + 1 / max_fps
            processing_started = time.monotonic()
            admit(frame)
            prepared = hud.prepare(frame) if hud is not None else to_bgr(frame.as_frame())
            result = detector.detect(prepared, threshold)
            report["frames_processed"] += 1
            if source_state["error"]:
                raise ValueError(source_state["error"])
            admit(frame)
            require_result(result)
            if hud is not None:
                image, hud_output = hud.render(result, prepared.shape[:2], threshold)
            else:
                # Owned annotations: the detector's input buffer is never presented.
                image, hud_output = annotate(prepared, result), None
            processing_ms = (time.monotonic() - processing_started) * 1000
            report["total_processing_ms"] += processing_ms
            report["total_inference_ms"] += result.inference_ms
            # Rendering and ownership copies may invoke adapters or take time.
            # Validate once more at the final output boundary.
            if source_state["error"]:
                raise ValueError(source_state["error"])
            admit(frame)
            age = (time.monotonic_ns() - frame.observed_ns) / 1e6
            report["maximum_observation_age_ms"] = max(report["maximum_observation_age_ms"], age)
            if age > max_age_ms or time.monotonic() >= deadline or stop.is_set():
                report["stale_results_dropped"] += 1
                continue
            processed = Processed(
                frame, result, image, report["frames_processed"], input_age, processing_ms,
                expires_ns=min(frame.observed_ns + int(max_age_ms * 1e6), int(deadline * 1e9)),
                output_age_ms=age, detector_input_size=(prepared.shape[1], prepared.shape[0]),
                native_to_inference_scale=(prepared.shape[1] / frame.image.shape[1],
                                           prepared.shape[0] / frame.image.shape[0]),
                hud=hud_output)
            sink.submit(processed)
            displayed_ns = frame.observed_ns
            labels.update(processed.labels)
            report["frames_submitted"] += 1
            report["last_result"] = {"source_id": frame.source_id, "sequence": frame.sequence,
                "observed_ns": frame.observed_ns, "media_time": frame.media_time,
                "inference_ms": result.inference_ms, "observation_age_ms": round(age, 3),
                "processing_ms": round(processing_ms, 3), "expires_ns": processed.expires_ns,
                "native_format": frame.native_descriptor,
                "detector_input_size": list(processed.detector_input_size),
                "native_to_inference_scale": list(processed.native_to_inference_scale),
                "box_coordinate_space": processed.output_space}
            if hud_output is not None:
                report["last_result"]["hud_rows"] = [row.as_dict() for row in hud_output.rows]
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
