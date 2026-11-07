"""Synthetic HUD validation: the ``ValidationLedger`` fed from a running ``HudStage``, and the G5 software counts.

One event keeps two meanings. The runtime mask drops a box that touches the
mask or crosses an eye, so it is never presented, and the engine counts it in
``PipelineStats.hud_checks``. ``ValidationSink`` hands the same frame's native
boxes, dropped ones included, to the ledger, which invalidates the whole run
on the first such box under the same rule (``geometry.exclusion``). A
validation run with any such box, or with a footprint pixel left non-zero
after masking, therefore fails; outside validation the box is only dropped.

``ValidationSink.software_report`` names its counts as the ``g5_software``
block of ``docs/templates/hud-device-validation-report.json`` does
(``G5_SOFTWARE_COUNTS``). They describe generated frames and declared
telemetry only, never a device, an image file or physical visibility, and
nothing here opens a gate.
"""
import time

from ..frame import native_format
from .contract import SESSION_TOKENS, VIEW_FIELDS, config_digest
from .qualification import ValidationLedger, native_format_record

G5_SOFTWARE_COUNTS = ("frames_checked", "native_mask_zeroing_failures", "hud_footprint_detections",
                      "mask_and_eye_boundary_rejections", "context_invalidations", "frame_result_identity_failures")
# Engine exclusion counts that fail a validation run, with the reason each reports.
EXCLUSION_FAILURES = (("mask_zeroing_failures", "hud_mask_zeroing_failed"),
                      ("mask_overlap_rejections", "hud_validation_mask_overlap"),
                      ("eye_crossing_rejections", "hud_validation_cross_eye_detection"))
SCOPE = "Generated frames and declared telemetry only; no device, image file or physical visibility observation"


def _view(source, reported, name, default):
    """A frame-reported value as it is; otherwise the source's declaration, or ``default`` when undeclared."""
    if name in reported:
        return reported[name]
    value = getattr(source, name, None)
    return default if value is None else value


def observed_context(source, frame_format, config, reported=None):
    """The version 2 context a run observes: the source's declared identity, a native format and the stage config.

    ``frame_format`` is a frame's format as its source reports it or a record's
    ``native_format``; the context carries it in the record form, so the
    mirror's format metadata is compared exactly against the candidate's.

    Session tokens and view fields a frame reports (``reported``) take
    precedence over the source's declarations, which ``CaptureContext``
    already requires to agree. An undeclared crop is the full frame, an
    undeclared orientation 0 and an undeclared HUD digest the stage
    configuration's, which is what the runtime mask applies; a declared or
    reported one must match the record.
    """
    reported = reported or {}
    crop = _view(source, reported, "crop_xyxy", [0, 0, frame_format.get("width"), frame_format.get("height")])
    return {"source_id": getattr(source, "source_id", None),
            **{name: reported.get(name, getattr(source, name, None)) for name in SESSION_TOKENS},
            "native_format": native_format_record(frame_format),
            "crop_xyxy": list(crop) if isinstance(crop, (list, tuple)) else crop,
            "orientation_degrees": _view(source, reported, "orientation_degrees", 0),
            "capture_layout": getattr(source, "capture_layout", None),
            "hud_config_sha256": _view(source, reported, "hud_config_sha256", config_digest(config))}


class ValidationSink:
    """Engine sink that records each result in a ``ValidationLedger`` before any presenting sink receives it.

    Put it first among the sinks. ``candidate_for(calibration)`` turns the
    started stage's calibration into the ledger's candidate, and ``max_age_ns``
    must be the engine's maximum age. A result the ledger rejects raises here,
    so the engine stops and later sinks never see it.
    """

    def __init__(self, stage, candidate_for, max_age_ns):
        self.stage, self.candidate_for, self.max_age_ns = stage, candidate_for, max_age_ns
        self.ledger = None

    def open(self):
        calibration = self.stage.mask.calibration  # HudStage.start validated it before any sink opens.
        context = observed_context(self.stage.source, calibration.context()["native_format"], self.stage.config)
        self.ledger = ValidationLedger(self.candidate_for(calibration), context, time.monotonic_ns(),
                                       max_age_ns=self.max_age_ns)

    def submit(self, processed):
        frame, checks = processed.frame, processed.hud.checks
        reported = {name: value for name, value in (frame.metadata or {}).items()
                    if name in SESSION_TOKENS + VIEW_FIELDS}
        self.ledger.record_result(
            current_context=observed_context(self.stage.source, native_format(frame), self.stage.config, reported),
            source_id=frame.source, frame_sequence=frame.sequence, result_sequence=checks.frame_sequence,
            observed_ns=frame.observed_ns, completed_ns=processed.completed_ns, expires_ns=processed.expires_ns,
            boxes_native=[list(box) for box in checks.boxes_native])

    def clear(self):
        """Nothing is presented here, so nothing is withdrawn."""

    def close(self):
        return True

    def failure_reason(self, checks):
        """Why validation itself failed: the ledger's invalidation, else the first non-zero exclusion count."""
        if self.ledger is not None and self.ledger.invalidated_reason is not None:
            return self.ledger.invalidated_reason
        return next((reason for name, reason in EXCLUSION_FAILURES if checks and checks[name]), None)

    def software_report(self, checks, context_failure, *, completed, max_age_ms, max_fps, seconds):
        """The run's G5 software block: ``G5_SOFTWARE_COUNTS``, status and limits; null counts if it never started.

        ``checks`` is ``PipelineStats.hud_checks`` and ``context_failure`` the
        engine's latched capture-context failure. Status is ``failed`` when any
        failure count is non-zero or the ledger invalidated the run,
        ``checked_unqualified`` for a clean completed run and ``incomplete``
        otherwise.
        """
        fields = {"evidence_kind": "synthetic_fixture", "maximum_duration_seconds": seconds,
                  "maximum_processing_fps": max_fps, "max_age_ms": max_age_ms, "physical_expiry_verified": False,
                  "scope": SCOPE}
        if self.ledger is None or checks is None:
            return {"status": "not_run", **dict.fromkeys(G5_SOFTWARE_COUNTS), "failure_reason": None, **fields}
        kind = self.ledger.failure_kind
        counts = {"frames_checked": checks["frames_checked"],
                  "native_mask_zeroing_failures": checks["mask_zeroing_failures"],
                  "hud_footprint_detections": checks["mask_overlap_rejections"],
                  "mask_and_eye_boundary_rejections": checks["mask_overlap_rejections"] + checks["eye_crossing_rejections"],
                  "context_invalidations": int(context_failure is not None) + int(kind == "context"),
                  "frame_result_identity_failures": int(kind == "identity")}
        reason = self.failure_reason(checks) or context_failure
        failed = reason is not None or any(counts[name] for name in G5_SOFTWARE_COUNTS[1:])
        return {"status": "failed" if failed else "checked_unqualified" if completed else "incomplete",
                **counts, "failure_reason": reason, **fields}
