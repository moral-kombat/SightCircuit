"""Gated, bounded synthetic recognition run on the shared ``Pipeline``, as a report.

The only accepted acquisition kind remains synthetic. Hardware adapters and
production output require separate gates; this wrapper lifts none of them.

``source`` is an ``open``/``read``/``close`` source (adapted by
``PolledSource``); ``sink`` implements ``open``/``submit(processed)``/``clear``/
``close``. ``hud``, when given, is a ``framejacker.hud.HudStage`` (optionally
with ``check_sink()``, run at every admission checkpoint).
"""
import threading

from ..gates import LIVE_IMAGE_INPUT
from ..pipeline import Pipeline
from ..sources.polled import PolledSource


def new_report(max_fps, seconds):
    return {"report_kind": "synthetic_recognition_pipeline", "status": "error", "exit_code": 1,
            "source_kind": "synthetic", "live_input_verified": False, "feedback_exclusion_verified": False,
            "hardware_trial_ready": False, "frames_processed": 0, "frames_submitted": 0,
            "stale_inputs_dropped": 0, "stale_results_dropped": 0, "maximum_pending_frames": 1,
            "maximum_processing_fps": max_fps, "seconds_requested": seconds, "images_saved": False,
            "source_cleanup_ok": False, "sink_cleanup_ok": False, "cleanup_ok": False,
            "maximum_observation_age_ms": 0.0, "labels_seen": [],
            "total_processing_ms": 0.0, "total_inference_ms": 0.0,
            "physical_recovery_confirmed": False, "cleanup_scope": "owned_software_resources_only"}


def last_result(processed):
    frame = processed.frame
    result = {"source_id": frame.source_id, "sequence": frame.sequence,
              "observed_ns": frame.observed_ns, "media_time": frame.media_time,
              "inference_ms": processed.result.inference_ms,
              "observation_age_ms": round(processed.output_age_ms, 3),
              "processing_ms": round(processed.processing_ms, 3), "expires_ns": processed.expires_ns,
              "native_format": frame.native_descriptor,
              "detector_input_size": list(processed.detector_input_size),
              "native_to_inference_scale": list(processed.native_to_inference_scale),
              "box_coordinate_space": processed.output_space}
    if processed.hud is not None:
        result["hud_rows"] = [row.as_dict() for row in processed.hud.rows]
    return result


def run_pipeline(source, detector, sink, seconds, *, stop=None, max_fps=5, max_age_ms=1000, threshold=0.5,
                 hud=None):
    LIVE_IMAGE_INPUT.require(getattr(source, "kind", None) == "synthetic", "live_image_source_not_approved")
    if not (0.05 <= seconds <= 60 and 1 <= max_fps <= 10 and 0 < max_age_ms <= 1000 and 0 <= threshold <= 1):
        raise ValueError("pipeline_limits_invalid")
    stop = stop or threading.Event()
    report = new_report(max_fps, seconds)
    if stop.is_set():
        report.update(status="cancelled", exit_code=130, source_cleanup_ok=True, sink_cleanup_ok=True, cleanup_ok=True)
        return report
    polled = PolledSource(source)
    check_sink = getattr(hud, "check_sink", None)
    pipeline = Pipeline(polled, detector, (sink,), threshold=threshold, max_fps=max_fps,
                        max_input_age_ms=max_age_ms, seconds=seconds, join_timeout=0.5, hud=hud,
                        context_validator=(lambda _frame: check_sink()) if check_sink is not None else None)
    try:
        stats = pipeline.run(stop)
        if stats.cancelled:
            report.update(status="cancelled", exit_code=130)
        elif stats.frames_submitted:
            report.update(status="completed", exit_code=0)
        else:
            report.update(status="no_frames", exit_code=1)
    except Exception as error:
        report.update(status="error", exit_code=1, reason=str(error)[:1024])
    stats = pipeline.stats
    report.update(stats.hud_report)
    report.update(frames_processed=stats.frames_processed, frames_submitted=stats.frames_submitted,
                  stale_inputs_dropped=stats.stale_inputs_skipped, stale_results_dropped=stats.stale_results_skipped,
                  maximum_observation_age_ms=stats.max_output_age_ms, total_processing_ms=stats.total_processing_ms,
                  total_inference_ms=stats.total_inference_ms, frames_received=stats.frames_received,
                  pending_inputs_replaced=stats.pending_replaced, labels_seen=sorted(stats.labels_seen),
                  elapsed_seconds=round(stats.elapsed_s, 3) if stats.started else 0)
    if stats.last is not None:
        report["last_result"] = last_result(stats.last)
    report["source_cleanup_ok"] = (not pipeline.capture_thread_started or
                                   (not pipeline.capture_thread_alive and polled.cleanup_ok is True))
    report.update(sink_clear_ok=stats.sink_clear_ok, sink_close_ok=stats.sink_close_ok,
                  sink_cleanup_ok=stats.sink_cleanup_ok)
    for operation, message in stats.sink_errors:
        report.setdefault(f"sink_{operation}_error", message)
    if polled.error:
        report["source_error"] = polled.error
    report["cleanup_ok"] = report["source_cleanup_ok"] and report["sink_cleanup_ok"]
    if not report["cleanup_ok"] and report["exit_code"] == 0:
        report.update(status="cleanup_failed", exit_code=1)
    return report
