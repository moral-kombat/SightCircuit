"""Observer report to offline mapping candidate; never a qualification.

``observer_candidate`` turns the exact bytes of an accepted
``scripts/hud_marker_observer.py`` report into a ``MappingCandidate``. The
report must pass the observer's own worker contract
(``observation.validate_observer_report``) and agree with its samples. The
observer's footprint union already includes its margin, so each candidate
``rect_native`` is that union shrunk by the margin once, checked against the
sampled corners; ``margin_px`` adds it back. The capture layout, session and
other context fields are declarations the report cannot confirm; only the
native format is checked against it. What the observer cannot establish is
an explicit unknown with a reason (``OBSERVER_UNKNOWNS``), never 0. The
result stays ``observed_unqualified``, and its report is at best
``candidate_valid_unqualified`` with exit 2. No gate is read or changed.
"""
from copy import deepcopy
import hashlib
import math

from ..gates import HARDWARE_MAPPING, claims
from .contract import HUD_CONFIG, NATIVE_FORMAT_FIELDS, CaptureFormat, config_digest, margin_valid
from .geometry import contains, expand, eye_viewports
from .observation import MAX_COPIES, validate_observer_report
from .qualification import OPERATING_LIMIT_FIELDS, MappingCandidate, parse_record, validate_context

REPORT_KIND = "hud_mapping_candidate_from_observer"
# An observer report is printed with indentation and supervisor fields; its
# compact form is bounded at 128 KiB by the observer itself.
MAX_OBSERVER_REPORT_BYTES = 1024 * 1024
MAX_SAMPLES = 100
# Samples record corners rounded to 0.001 px; the union used unrounded corners.
CORNER_ROUNDING_PX = 0.001
OBSERVED_REASON = "composition_and_unseen_content_not_established"
OBSERVER_UNKNOWNS = {
    "eye_composition": "The observer assigns no eye; declared_eye is the declared layout's viewport holding the copy",
    "unseen_or_occluded_copies": "Finite visible-copy samples cannot rule out an unrecognized, clipped or occluded copy",
    "capture_freshness": "A changed clear baseline does not establish mirror or camera freshness",
    "motion_displacement_bound": "maximum_drift_px is drift sampled at up to 5 Hz, not a bound on unobserved motion",
    "localization_error_px": "Corner localization uncertainty was not measured",
    "filter_support_px": "Compositor and raster filter support were not measured",
    "operating_limits": "Head pose, angular speed and display mode were neither measured nor monitored",
}


def observer_candidate(data, *, artifact_id, declared_context, lifetime_seconds):
    """The unqualified candidate an observer report's exact bytes support; ``ValueError`` rejects."""
    if type(data) is not bytes or len(data) > MAX_OBSERVER_REPORT_BYTES:
        raise ValueError("hud_observer_report_budget_exceeded")
    digest = hashlib.sha256(data).hexdigest()
    record = candidate_record(parse_record(data), digest, artifact_id, declared_context, lifetime_seconds)
    return MappingCandidate.from_mapping(record)


def candidate_record(report, report_sha256, artifact_id, declared_context, lifetime_seconds):
    """Build the version 1 record; every check here fails closed before the schema's own checks."""
    capture_format = _accepted_observation(report)
    rects = _footprints(report, capture_format)
    samples, margin = report["samples"], report["margin_px"]
    context = _declared_context(declared_context, capture_format)
    eyes = eye_viewports(tuple(context["crop_xyxy"]), context["capture_layout"])
    declared_eyes = []
    for rect in rects:
        holding = [index for index, eye in enumerate(eyes) if contains(eye, expand(rect, margin))]
        if len(holding) != 1:
            raise ValueError("hud_observer_copy_outside_declared_eye")
        declared_eyes.append(holding[0])
    if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 300:
        raise ValueError("hud_candidate_lifetime_invalid")
    # The lifetime counts from the first sample, so a longer observation cannot extend it.
    observed_ns, expires_ns = samples[-1][1], samples[0][1] + lifetime_seconds * 1_000_000_000
    if expires_ns <= observed_ns:
        raise ValueError("hud_candidate_lifetime_shorter_than_observation")
    # Sampled drift is measured; the two unmeasured terms are null and named in ``unknowns``.
    copies = [{"copy_id": f"observed-copy-{index}", "declared_eye": eye, "rect_native": rect, "margin_px": margin,
               "maximum_drift_px": report["maximum_corner_drift_px"], "localization_error_px": None,
               "filter_support_px": None}
              for index, (eye, rect) in enumerate(zip(declared_eyes, rects))]
    return {"schema_version": 1, "status": "observed_unqualified", **claims(HARDWARE_MAPPING),
            "mapping_id": "observer-" + report_sha256[:16], "context": context,
            "observed_ns": observed_ns, "expires_ns": expires_ns,
            "operating_limits": dict.fromkeys(OPERATING_LIMIT_FIELDS), "copies": copies,
            "evidence": [{"role": "observer", "artifact_id": artifact_id, "sha256": report_sha256,
                          "kind": _evidence_kind(report)}],
            "unknowns": dict(OBSERVER_UNKNOWNS)}


def candidate_report(candidate):
    """The schema report of an observer-derived candidate, with the record itself."""
    record = candidate.as_dict()
    return dict(candidate.report(), report_kind=REPORT_KIND, candidate=record,
                observer_report_sha256=record["evidence"][0]["sha256"],
                scope="Visible-copy footprint from one observer report; declared context and listed unknowns "
                      "do not qualify a mapping or enable output")


def rejection_report(reason):
    return {"report_kind": REPORT_KIND, "schema_version": 1, "status": "rejected", "exit_code": 1,
            "reason": str(reason)[:512], **claims()}


def _accepted_observation(report):
    """A clean ``observed_unqualified`` report under the observer's contract; returns its capture format."""
    if type(report) is not dict:
        raise ValueError("hud_observer_report_invalid")
    if report.get("status") != "observed_unqualified" or report.get("exit_code") != 2:
        raise ValueError("hud_observer_report_not_observed_unqualified")
    validate_observer_report(report, 2)
    if (report.get("reason") != OBSERVED_REASON or "cleanup_error" in report or
            any(report.get(key) is not False for key in ("worker_forced_termination", "guardian_forced_termination")
                if key in report)):
        raise ValueError("hud_observer_report_has_errors")
    capture = report.get("capture")
    if (type(capture) is not dict or capture.get("status") != "captured" or capture.get("cleanup_ok") is not True or
            "error" in capture or type(capture.get("frames_read")) is not int):
        raise ValueError("hud_observer_capture_incomplete")
    fmt = capture.get("format")
    if type(fmt) is not dict or any(key not in fmt for key in NATIVE_FORMAT_FIELDS):
        raise ValueError("hud_observer_capture_format_invalid")
    CaptureFormat.from_mapping(fmt).validate()
    return {key: fmt[key] for key in NATIVE_FORMAT_FIELDS}


def _rounded_edge(edge, value, rounding):
    """Whether ``edge`` is ``rounding`` of a corner bound that sampled to ``value``."""
    return edge in (rounding(value - CORNER_ROUNDING_PX), rounding(value + CORNER_ROUNDING_PX))


def _corner(value):
    if (type(value) is not list or len(value) != 2 or
            any(type(item) not in (int, float) or not math.isfinite(item) for item in value)):
        raise ValueError("hud_observer_samples_invalid")
    return value


def _footprints(report, capture_format):
    """Native rectangles without margin, recomputed from every sample; rejects gaps and edits."""
    samples, count, margin = report.get("samples"), report.get("observed_copy_count"), report.get("margin_px")
    if (type(samples) is not list or len(samples) != report["sample_count"] or not 3 <= len(samples) <= MAX_SAMPLES or
            type(count) is not int or not 1 <= count <= MAX_COPIES or
            not margin_valid(margin)):
        raise ValueError("hud_observer_samples_invalid")
    bounds = [[math.inf, math.inf, -math.inf, -math.inf] for _ in range(count)]
    first, previous, drift = None, None, 0.0
    for sample in samples:
        if (type(sample) is not list or len(sample) != 3 or type(sample[0]) is not int or sample[0] < 1 or
                type(sample[1]) is not int or sample[1] < 0 or type(sample[2]) is not list or len(sample[2]) != count):
            raise ValueError("hud_observer_samples_invalid")
        if previous is not None and sample[0] != previous[0] + 1:
            raise ValueError("hud_observer_sequence_gap")
        if previous is not None and sample[1] <= previous[1]:
            raise ValueError("hud_observer_time_not_increasing")
        quads = []
        for quad in sample[2]:
            if type(quad) is not list or len(quad) != 4:
                raise ValueError("hud_observer_samples_invalid")
            quads.append([_corner(point) for point in quad])
        first = first or quads
        for box, quad, start in zip(bounds, quads, first):
            drift = max(drift, max(abs(a - b) for point, origin in zip(quad, start) for a, b in zip(point, origin)))
            xs, ys = [x for x, _ in quad], [y for _, y in quad]
            box[:] = [min(box[0], *xs), min(box[1], *ys), max(box[2], max(xs) + 1), max(box[3], max(ys) + 1)]
        previous = sample
    baseline = report.get("baseline_sequence")
    if (type(baseline) is not int or not baseline < samples[0][0] or
            samples[-1][0] > report["capture"]["frames_read"]):
        raise ValueError("hud_observer_samples_invalid")
    reported = report.get("maximum_corner_drift_px")
    if (type(reported) not in (int, float) or not math.isfinite(reported) or not 0 <= reported <= margin // 2 or
            abs(reported - drift) > 2 * CORNER_ROUNDING_PX):
        raise ValueError("hud_observer_drift_inconsistent")
    unions = report.get("footprint_union_native")
    if type(unions) is not list or len(unions) != count:
        raise ValueError("hud_observer_footprint_inconsistent")
    rects, frame = [], (0, 0, capture_format["width"], capture_format["height"])
    for region, box in zip(unions, bounds):
        if type(region) is not list or len(region) != 4 or any(type(value) is not int for value in region):
            raise ValueError("hud_observer_footprint_inconsistent")
        # The observer's union already includes the margin; remove it exactly once.
        # What remains must be the samples' outward-rounded corner bounds.
        rect = [region[0] + margin, region[1] + margin, region[2] - margin, region[3] - margin]
        if not (all(_rounded_edge(edge, value, math.floor) for edge, value in zip(rect[:2], box[:2])) and
                all(_rounded_edge(edge, value, math.ceil) for edge, value in zip(rect[2:], box[2:]))):
            raise ValueError("hud_observer_footprint_inconsistent")
        if not contains(frame, region):
            raise ValueError("hud_observer_footprint_escapes_capture")
        rects.append(rect)
    return rects


def _declared_context(context, capture_format):
    """The declared acquisition context, which must describe the observed native format and HUD."""
    validate_context(context)
    if context["native_format"] != capture_format:
        raise ValueError("hud_observer_format_context_mismatch")
    if context["hud_config_sha256"] != config_digest(HUD_CONFIG):
        raise ValueError("hud_config_changed")
    return deepcopy(context)


def _evidence_kind(report):
    """Never label a validation-bridge or synthetic-driver observation as a device report."""
    capabilities = report["capture"].get("capabilities")
    synthetic = (report["calibration_validation_only"] is not False or type(capabilities) is not dict or
                 capabilities.get("driver") == "synthetic")
    return "synthetic_fixture" if synthetic else "device_report"
