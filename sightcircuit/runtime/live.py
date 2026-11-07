"""Gated, bounded live recognition runs on the shared ``Pipeline``, as reports.

``run_report`` is the one report runner, for the synthetic run
(``run_pipeline``) and the SteamVR mirror recognizer (``run_recognition``,
the worker of ``scripts/mirror_recognize.py``). It loads the detector after a
command's own checks, ends the run ``cancelled`` if a stop was requested by
then, builds the latest-only, age-bounded ``Pipeline``, maps its outcome to a
status, records the ``pipeline`` statistics block with the command's schema-1
aliases and its last presented result, and then the command's own source
cleanup. A ``ReportStyle`` (``SYNTHETIC``, ``MIRROR``) names what differs: the
success status, the aliases, the failure fields, the last result and whether
the report carries its exit code.

Synthetic run. The only accepted acquisition kind remains synthetic. Hardware
adapters and production output require separate gates; this wrapper lifts none
of them. ``source`` is an ``open``/``read``/``close`` source (adapted by
``PolledSource``); ``sink`` implements ``open``/``submit(processed)``/``clear``/
``close``. ``hud``, when given, is a ``sightcircuit.hud.HudStage`` (optionally
with ``check_sink()``, run at every admission checkpoint, as
``hud.stage.HudPipelineAdapter`` provides).

A synthetic HUD run is also a synthetic validation run: a ``ValidationSink``,
ahead of ``sink``, records every result in a ``ValidationLedger`` built from the
fixture calibration (``synthetic.fixture_candidate``) with the run's own
maximum age. The report adds ``g5_software`` (the device report template's G5
software counts) and ``validation_ledger``. When the ledger rejects a result or
the engine counts a footprint pixel left after masking, or a box dropped for
touching the mask or crossing an eye, the run ends ``validation_failed`` with
exit 1, unless it already failed on its own (see ``add_validation``).
``max_age_ms`` defaults to the contract's ``MAX_DISPLAY_AGE_MS``.

Mirror run. ``run_recognition`` reads a verified SteamVR mirror through
``MirrorSource``, with the contract's one-second age limit and no exit code
(its launcher derives it). Its HUD mode accepts fixture backends only and feeds
no validation ledger. An injected ``session`` (``sources.session.MirrorSession``)
gives the source its session tokens and adds ``session_tokens`` to the report;
no command passes one yet, and it lifts no gate.
"""
from dataclasses import dataclass
import threading
import time
from typing import Callable

from ..detection import DEFAULT_DETECTOR, create_detector, describe
from ..frame import native_format
from ..gates import LIVE_IMAGE_INPUT
from ..hud import HUD_CONFIG, HudStage
from ..hud.contract import MAX_DISPLAY_AGE_MS
from ..hud.validation import ValidationSink
from ..pipeline import Pipeline, report_fields
from ..sources.mirror import MirrorSource, capture
from ..sources.polled import PolledSource
from .guard import cleanup_budget
from .synthetic import fixture_candidate


@dataclass(frozen=True)
class ReportStyle:
    """How one command's run report names what ``run_report`` records."""
    aliases: dict  # Schema-1 field names of ``pipeline`` block keys (``report_fields``).
    success: str  # Status of a run that presented a result.
    exit_codes: bool  # Write ``exit_code`` with each status; otherwise a launcher derives it.
    failure: Callable  # error -> the fields of a run that failed with it.
    last_result: Callable  # Processed -> the report's ``last_result``.

    def set_status(self, report, status):
        report["status"] = status
        if self.exit_codes:
            report["exit_code"] = 0 if status == self.success else 130 if status == "cancelled" else 1


def require_synthetic(source, reason):
    """The live-image gate: only a source that declares the synthetic kind may feed a live run."""
    LIVE_IMAGE_INPUT.require(getattr(source, "kind", None) == "synthetic", reason)


class ProgressSink:
    """Pipeline sink that sends ``progress`` a report snapshot at most once a second.

    A snapshot is the report so far plus the run's statistics block and its
    aliases, this result as ``last_result`` and ``fields()``; the report itself
    is not changed.
    """

    def __init__(self, report, style, progress, fields=dict):
        self.report, self.style, self.progress, self.fields = report, style, progress, fields
        self.stats = None  # The running pipeline's statistics, set once it is built.
        self.next_progress = time.monotonic() + 1

    def submit(self, processed):
        if time.monotonic() < self.next_progress:
            return
        block = self.stats.report()
        # Sinks run before the engine records this result's labels; the snapshot includes them,
        # so the aliases still come from the block it embeds.
        block["labels_seen"] = sorted(set(block["labels_seen"]) | set(processed.labels))
        snapshot = dict(self.report)
        snapshot.update(report_fields(block, self.style.aliases), pipeline=block,
                        last_result=self.style.last_result(processed))
        snapshot.update(self.fields())
        self.progress(snapshot)
        self.next_progress = time.monotonic() + 1


def run_report(report, style, stop, source, load, *, close, sinks=(), hud=None, progress=None,
               progress_fields=dict, threshold, max_fps, max_age_ms, join_timeout, seconds=None):
    """Run one bounded live ``Pipeline`` into ``report``; return the pipeline, or None if none was built.

    ``load()`` returns the detector after the command's own checks, inside the
    failure handling, so a refused gate or a missing model ends the run
    ``error`` before the source opens; a stop requested by then ends it
    ``cancelled``. The engine is latest-only with a ``max_age_ms`` age bound; a
    HUD stage's ``check_sink()``, when it has one, runs at every admission
    checkpoint. ``progress``, when given, receives the report before loading and
    then ``ProgressSink`` snapshots. Afterwards ``stop`` is set, the statistics
    are recorded (``record``) and ``close(report, pipeline)`` records the
    command's own source cleanup. With ``style.exit_codes`` a run that would
    exit 0 but whose cleanup failed ends ``cleanup_failed``.
    """
    if progress is not None:
        progress(report.copy())
    pipeline = None
    try:
        detector = load()
        if stop.is_set():
            style.set_status(report, "cancelled")
            return None
        snapshots = ProgressSink(report, style, progress, progress_fields) if progress is not None else None
        check_sink = getattr(hud, "check_sink", None)
        pipeline = Pipeline(source, detector, (*sinks, snapshots) if snapshots is not None else tuple(sinks),
                            threshold=threshold, max_fps=max_fps, max_input_age_ms=max_age_ms, seconds=seconds,
                            join_timeout=join_timeout, hud=hud,
                            context_validator=(lambda _frame: check_sink()) if check_sink is not None else None)
        if snapshots is not None:
            snapshots.stats = pipeline.stats
        stats = pipeline.run(stop)
        style.set_status(report, "cancelled" if stats.cancelled else
                         style.success if stats.frames_submitted else "no_frames")
    except Exception as error:
        style.set_status(report, "error")
        report.update(style.failure(error))
    finally:
        stop.set()
        if pipeline is not None:
            record(report, style, pipeline.stats)
        close(report, pipeline)
        if style.exit_codes and not report["cleanup_ok"] and report["exit_code"] == 0:
            style.set_status(report, "cleanup_failed")
    return pipeline


def record(report, style, stats):
    """A run's statistics: its HUD stage's fields, the ``pipeline`` block with aliases, and its last result."""
    report.update(stats.hud_report)
    block = stats.report()
    # From the unrounded stats: deriving it from the block's rounded elapsed_ms would round twice.
    report.update(report_fields(block, style.aliases), pipeline=block,
                  elapsed_seconds=round(stats.elapsed_s, 3) if stats.started else 0)
    if stats.last is not None:
        report["last_result"] = style.last_result(stats.last)


# Schema-1 field names kept for existing reports and evidence; "pipeline" holds the canonical block.
LEGACY_FIELDS = {
    "frames_processed": "frames_processed", "frames_submitted": "frames_submitted",
    "frames_received": "frames_received", "stale_inputs_dropped": "stale_inputs_skipped",
    "stale_results_dropped": "stale_results_skipped", "pending_inputs_replaced": "pending_replaced",
    "maximum_observation_age_ms": "max_output_age_ms", "total_processing_ms": "total_processing_ms",
    "total_inference_ms": "total_inference_ms", "labels_seen": "labels_seen",
}


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
    result = {"source_id": frame.source, "sequence": frame.sequence,
              "observed_ns": frame.observed_ns, "media_time": frame.media_time,
              "inference_ms": processed.result.inference_ms,
              "observation_age_ms": round(processed.output_age_ms, 3),
              "processing_ms": round(processed.processing_ms, 3), "expires_ns": processed.expires_ns,
              "native_format": native_format(frame),
              "detector_input_size": list(processed.detector_input_size),
              "native_to_inference_scale": list(processed.native_to_inference_scale),
              "box_coordinate_space": processed.output_space}
    if processed.hud is not None:
        result["hud_rows"] = [row.as_dict() for row in processed.hud.rows]
        checks = processed.hud.checks
        if checks is not None:  # This frame's own exclusion checks; the run totals are in pipeline.hud_checks.
            result["hud_checks"] = {"mask_overlap_rejections": checks.mask_overlap_rejections,
                                    "eye_crossing_rejections": checks.eye_crossing_rejections,
                                    "unmasked_footprint_px": checks.unmasked_footprint_px}
    return result


def synthetic_failure(error):
    return {"reason": str(error)[:1024]}


SYNTHETIC = ReportStyle(LEGACY_FIELDS, "completed", True, synthetic_failure, last_result)


def run_pipeline(source, detector, sink, seconds, *, stop=None, max_fps=5, max_age_ms=MAX_DISPLAY_AGE_MS,
                 threshold=0.5, hud=None):
    require_synthetic(source, "live_image_source_not_approved")
    if not (0.05 <= seconds <= 60 and 1 <= max_fps <= 10 and 0 < max_age_ms <= MAX_DISPLAY_AGE_MS and 0 <= threshold <= 1):
        raise ValueError("pipeline_limits_invalid")
    stop = stop or threading.Event()
    report = new_report(max_fps, seconds)
    polled = PolledSource(source)
    # The engine's per-result age bound, int(max_age_ms * 1e6), so the ledger checks the same deadline.
    validation = ValidationSink(hud, fixture_candidate, int(max_age_ms * 1e6)) if hud is not None else None

    def close(report, pipeline):
        if pipeline is None:  # Nothing was opened.
            report.update(source_cleanup_ok=True, sink_cleanup_ok=True, cleanup_ok=True)
            return
        stats = pipeline.stats
        # The capture thread was joined for cleanup_budget(0.5) seconds: under the guard, the supervisor's
        # cooperative grace less its reserve, so a slow thread is a verdict of the deadline, not of a fixed join.
        report["source_cleanup_ok"] = (not pipeline.capture_thread_started or
                                       (not pipeline.capture_thread_alive and polled.cleanup_ok is True))
        report.update(sink_clear_ok=stats.sink_clear_ok, sink_close_ok=stats.sink_close_ok,
                      sink_cleanup_ok=stats.sink_cleanup_ok)
        for operation, message in stats.sink_errors:
            report.setdefault(f"sink_{operation}_error", message)
        if polled.error:
            report["source_error"] = polled.error
        report["cleanup_ok"] = report["source_cleanup_ok"] and report["sink_cleanup_ok"]

    pipeline = run_report(report, SYNTHETIC, stop, polled, lambda: detector, close=close,
                          sinks=(validation, sink) if validation is not None else (sink,), hud=hud,
                          threshold=threshold, max_fps=max_fps, max_age_ms=max_age_ms, seconds=seconds,
                          join_timeout=cleanup_budget(0.5))
    if pipeline is not None and validation is not None:
        add_validation(report, validation, pipeline, max_age_ms=max_age_ms, max_fps=max_fps, seconds=seconds)
    return report


def add_validation(report, validation, pipeline, **limits):
    """Add ``g5_software`` and ``validation_ledger``; a failed validation ends the run ``validation_failed``.

    ``validation_failed`` (exit 1) and the validation's reason replace a run
    that completed, saw no frames, or stopped on that same rejection. A run
    that failed on its own (``error``, for example a capture-context failure,
    ``cleanup_failed`` or ``cancelled``) keeps its status, exit code and
    reason; ``g5_software`` then reports ``failed`` and names the validation's
    reason, and a capture-context failure is counted there.
    """
    context, checks = pipeline.capture_context, pipeline.stats.hud_checks
    report["g5_software"] = validation.software_report(
        checks, context.failure if context is not None else None, completed=report["status"] == "completed",
        **limits)
    report["validation_ledger"] = validation.ledger.report() if validation.ledger is not None else None
    reason = validation.failure_reason(checks)
    if reason is not None and (report["status"] in ("completed", "no_frames") or report.get("reason") == reason):
        report.update(status="validation_failed", exit_code=1, reason=reason)


# Schema-1 field names kept for existing reports and evidence; "pipeline" holds the canonical block.
MIRROR_LEGACY_FIELDS = {
    "frames_processed": "frames_processed", "stale_inputs_skipped": "stale_inputs_skipped",
    "stale_results_skipped": "stale_results_skipped", "pending_frames_replaced": "pending_replaced",
    "labels_seen": "labels_seen", "processing_elapsed_ms": "elapsed_ms",
    "observed_processing_rate_hz": "processed_fps", "mean_inference_ms": "mean_inference_ms",
    "maximum_inference_ms": "max_inference_ms", "maximum_processing_started_age_ms": "max_input_age_ms",
}


def new_mirror_report():
    return {"report_kind": "steamvr_mirror_recognition", "schema_version": 1,
            "status": "not_started", "cleanup_ok": True, "images_saved": False,
            "frames_processed": 0, "labels_seen": [], "stale_inputs_skipped": 0, "stale_results_skipped": 0,
            "pending_frames_replaced": 0,
            "display_started": False, "overlay_requested": False,
            "maximum_pending_frames": 1, "maximum_input_observation_age_ms": MAX_DISPLAY_AGE_MS,
            "physical_camera_identity": "unverified", "camera_timestamps_available": False,
            "confidence_is_calibrated_probability": False,
            "scope": "Composited mirror recognition; source identity, camera freshness and display feedback unverified"}


def mirror_last_result(processed):
    """The reported result; boxes are in resized mirror pixels, or HUD rows in HUD mode."""
    frame, result = processed.frame, processed.result
    listed = ([row.as_dict() for row in processed.hud.rows] if processed.hud is not None
              else [item.as_dict() for item in result.detections])
    return {"read_sequence": frame.sequence, "observation_time_ns": frame.observed_ns,
            "processing_started_age_ms": round(processed.input_age_ms, 3),
            "inference_ms": round(result.inference_ms, 3), "labels": processed.labels,
            "detections": listed[:20], "width": processed.image.shape[1], "height": processed.image.shape[0],
            "box_coordinate_space": processed.output_space if processed.hud is not None else "resized_mirror_pixels"}


def mirror_failure(error):
    return {"reason": getattr(error, "reason", "recognition_failed"), "error": str(error)}


MIRROR = ReportStyle(MIRROR_LEGACY_FIELDS, "recognized", False, mirror_failure, mirror_last_result)


def run_recognition(candidate, seconds, stop, progress=lambda report: None, *, max_fps=5, threshold=0.5,
                    detector=DEFAULT_DETECTOR, backend_factory=None, detector_factory=None,
                    hud_mode=False, calibration=None, hud_config=HUD_CONFIG, session=None):
    """Bounded recognition from a verified SteamVR mirror; return the report and its last result, or None.

    The result (a ``Processed``) is returned only for a recognized run with
    confirmed cleanup, so outputs taken from it (preview, still) always
    describe the reported ``last_result``. ``detector`` names a ``DETECTORS``
    entry; an injected ``detector_factory`` replaces it and is not described
    in the report. ``progress`` receives the report before the model loads and
    then a snapshot at most once a second. ``session`` is declared when the
    source is created, before the model loads. In HUD mode the gate is checked
    first: a backend that does not declare the synthetic kind is refused
    before any session provider reads ``/proc`` or hashes the runtime binary.
    """
    report = new_mirror_report()
    report.update(candidate=candidate, phase="loading_detector", maximum_processing_fps=max_fps,
                  threshold=threshold, seconds_requested=seconds)
    stage = HudStage(backend_factory, calibration, hud_config) if hud_mode else None

    def load():
        if stage is not None:
            # No CLI/live adapter enables this: fixture backends only.
            require_synthetic(backend_factory, "hud_live_mirror_not_approved")
            # Validate the calibration before the model loads or the source opens.
            stage.start()
            report.update(hud_mode=True, calibration_scope="synthetic_fixture_only",
                          hardware_mapping_verified=False, feedback_exclusion_verified=False)
        model = detector_factory() if detector_factory is not None else create_detector(detector)
        if detector_factory is None:
            report["detector"] = describe(model)
        report["phase"] = "recognizing"
        return model

    def acquire(capture_stop, on_frame, update):
        return capture(candidate, seconds, capture_stop, update, backend_factory=backend_factory, on_frame=on_frame)[0]

    if stage is not None and session is not None:
        try:  # The same refusal as in load(), before the session's providers do any work.
            require_synthetic(backend_factory, "hud_live_mirror_not_approved")
        except ValueError as error:
            MIRROR.set_status(report, "error")
            report.update(mirror_failure(error), capture={}, phase="closed")
            return report, None
    try:
        source = MirrorSource(acquire, getattr(backend_factory, "source_id", None) or "mirror:observed",
                              identity=backend_factory, session=session)
    except ValueError as error:  # A session that cannot be declared stops the run before anything opens.
        MIRROR.set_status(report, "error")
        report.update(mirror_failure(error), reason=str(error), phase="closed")
        return report, None

    def close(report, pipeline):
        if pipeline is not None and pipeline.capture_thread_alive:
            report.update(status="error", reason="capture_thread_did_not_exit", cleanup_ok=False)
        captured = source.report
        report["capture"] = captured
        if session is not None:
            report["session_tokens"] = source.session_report
        if captured:
            report["cleanup_ok"] = report["cleanup_ok"] and captured["cleanup_ok"]
            if captured["status"] == "error":
                report.update(status="error", reason=captured.get("reason", "capture_failed"),
                              error=captured.get("error", "Capture failed"))
        report["phase"] = "closed"

    pipeline = run_report(report, MIRROR, stop, source, load, close=close, hud=stage, progress=progress,
                          progress_fields=lambda: {"capture": source.report}, threshold=threshold,
                          max_fps=max_fps, max_age_ms=MAX_DISPLAY_AGE_MS, join_timeout=1)
    if report["status"] == "no_frames" and pipeline.stats.stale_inputs_skipped:
        report.update(status="stale_input", reason="all_selected_observations_stale")
    if report["status"] == "recognized":
        age = (time.monotonic_ns() - report["last_result"]["observation_time_ns"]) / 1e6
        report["last_result_age_after_cleanup_ms"] = round(age, 3)
        if age > MAX_DISPLAY_AGE_MS:
            report.update(status="stale_input", reason="last_observed_input_too_old")
    recognized = report["status"] == "recognized" and report["cleanup_ok"]
    return report, pipeline.stats.last if recognized else None
