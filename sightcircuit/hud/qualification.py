"""Offline HUD mapping records and bounded validation instrumentation.

These records describe proposed operating conditions and evidence references.
Their validity never grants hardware or production-output authority. In
particular, supplied JSON and artifact digests cannot prove eye composition,
coverage of hidden copies, physical visibility, or wearer recovery. A record
names each term it cannot establish in ``unknowns``; such a field is null,
never 0.
"""
from dataclasses import dataclass
import json
import math

from ..gates import HARDWARE_MAPPING, LIVE_HUD_OUTPUT, claims
from .contract import (CANDIDATE_PROVENANCE, CANDIDATE_SCHEMA_VERSION, CANDIDATE_SCHEMA_VERSIONS, HUD_CONFIG,
                       MAX_DISPLAY_AGE_NS, MAX_MAPPING_LIFETIME_NS, NATIVE_FORMAT_FIELDS, NATIVE_FORMAT_METADATA_FIELD,
                       SESSION_TOKENS, Calibration, CaptureFormat, Footprint, HudConfig, config_digest, margin_valid,
                       token_valid)
from .geometry import LAYOUTS, exclusion, expand, eye_viewports, within_blind_budget


MAX_RECORD_BYTES = 128 * 1024
EVIDENCE_ROLES = ("observer", "composition", "copy_coverage", "motion_margin", "physical_expiry")
MAPPING_FIELDS = ("schema_version", "status", "mapping_qualified", "mapping_id", "context", "observed_ns",
                  "expires_ns", "operating_limits", "copies", "evidence")
OPERATING_LIMIT_FIELDS = ("yaw_degrees", "pitch_degrees", "roll_degrees", "angular_speed_degrees_s", "display_mode")
# Per-copy margin terms, in pixels: sampled drift, corner localization and filter support.
COPY_ERROR_TERMS = ("maximum_drift_px", "localization_error_px", "filter_support_px")
# Terms a record may declare unknown, each with a reason. A field-bearing term
# (a copy term, or every operating limit) is then null in the record, never 0.
UNKNOWN_TERMS = ("eye_composition", "unseen_or_occluded_copies", "capture_freshness", "motion_displacement_bound",
                 *COPY_ERROR_TERMS, "operating_limits")
# ValidationLedger failure kinds by reason; a failed context check is "context" whatever its reason.
FAILURE_KINDS = {"hud_frame_sequence_invalid": "identity", "hud_result_identity_mismatch": "identity",
                 "hud_nonmonotonic_result": "identity", "hud_validation_mask_overlap": "exclusion",
                 "hud_validation_cross_eye_detection": "exclusion", "hud_native_detection_box_invalid": "exclusion",
                 "hud_completion_clock_regressed": "timing", "hud_frame_observed_time_invalid": "timing",
                 "hud_result_deadline_invalid": "timing", "hud_validation_result_budget_exceeded": "budget",
                 "hud_validation_detection_budget_exceeded": "budget"}
EXCLUSION_REASONS = {"mask_overlap": "hud_validation_mask_overlap", "eye_crossing": "hud_validation_cross_eye_detection"}


def _object(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"hud_{name}_fields_invalid")


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"hud_{name}_invalid")


def _token(value, name, limit=160):
    if not token_valid(value, limit):
        raise ValueError(f"hud_{name}_invalid")


def _sha256(value, name):
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"hud_{name}_invalid")


def _rect(value, bounds, name):
    if (type(value) is not list or len(value) != 4 or any(type(item) is not int for item in value) or
            not (bounds[0] <= value[0] < value[2] <= bounds[2] and
                 bounds[1] <= value[1] < value[3] <= bounds[3])):
        raise ValueError(f"hud_{name}_invalid")


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"hud_{name}_invalid")


def _reject_constant(name):
    raise ValueError("hud_input_json_nonfinite_number")


def parse_record(data):
    """Decode one size-bounded JSON record; deep nesting and NaN/Infinity are rejected."""
    try:
        return json.loads(data, parse_constant=_reject_constant)
    except RecursionError as error:
        raise ValueError("hud_input_json_nesting_exceeded") from error


def _unknowns(value):
    """Explicit unknown terms: each a schema term with a reason, so an absent value is never read as 0."""
    if (type(value) is not dict or not 1 <= len(value) <= len(UNKNOWN_TERMS) or
            any(term not in UNKNOWN_TERMS for term in value)):
        raise ValueError("hud_unknowns_invalid")
    for reason in value.values():
        _token(reason, "unknown_reason", 240)


def _unknown_null(value):
    if value is not None:
        raise ValueError("hud_unknown_term_has_value")


def validate_context(context, schema_version=CANDIDATE_SCHEMA_VERSION):
    """Validate declared acquisition metadata, never infer it from owned strides.

    ``native_format`` carries the six native-format fields in a version 1
    context, and those six plus the ``metadata`` object (the source's other
    format metadata, possibly empty) in a version 2 context.
    """
    _object(context, ("source_id", "session_id", "runtime_id", "timebase_id", "tracking_origin",
                      "recenter_epoch", "native_format", "crop_xyxy", "orientation_degrees",
                      "capture_layout", "hud_config_sha256"), "context")
    if schema_version not in CANDIDATE_SCHEMA_VERSIONS:
        raise ValueError("hud_mapping_schema_version_invalid")
    for key in ("source_id", "session_id", "runtime_id", "timebase_id", "tracking_origin"):
        _token(context[key], key)
    _integer(context["recenter_epoch"], 0, 2**63-1, "recenter_epoch")
    _sha256(context["hud_config_sha256"], "config_digest")
    fmt = context["native_format"]
    _object(fmt, NATIVE_FORMAT_FIELDS + ((NATIVE_FORMAT_METADATA_FIELD,) if schema_version >= 2 else ()),
            "native_format")
    if schema_version >= 2 and type(fmt[NATIVE_FORMAT_METADATA_FIELD]) is not dict:
        raise ValueError("hud_native_format_fields_invalid")
    # The runtime mask's own format and metadata rules; a record cannot describe a layout it would reject.
    CaptureFormat.from_mapping(fmt).validate()
    _rect(context["crop_xyxy"], (0, 0, fmt["width"], fmt["height"]), "crop")
    if type(context["orientation_degrees"]) is not int or context["orientation_degrees"] not in (0, 90, 180, 270):
        raise ValueError("hud_orientation_invalid")
    if context["capture_layout"] not in LAYOUTS:
        raise ValueError("hud_capture_layout_invalid")
    x0, y0, x1, y1 = context["crop_xyxy"]
    if ((context["capture_layout"] == "stereo_sbs" and (x1-x0) % 2) or
            (context["capture_layout"] == "stereo_tb" and (y1-y0) % 2)):
        raise ValueError("hud_eye_partition_invalid")


def native_format_record(fmt):
    """The record form of a native format written as a source reports it (its metadata beside the six
    fields), as a version 1 record (the six fields) or as a version 2 record (the six plus ``metadata``)."""
    if type(fmt) is not dict:
        raise ValueError("hud_native_format_fields_invalid")
    try:
        return CaptureFormat.from_mapping(fmt).record()
    except (KeyError, TypeError, ValueError):
        raise ValueError("hud_native_format_fields_invalid") from None


def canonical_context(context):
    """A validated context in the version 2 form, whatever form its native format was written in, so a
    record's context and a run's observed context compare exactly, metadata included."""
    if type(context) is not dict or "native_format" not in context:
        validate_context(context)  # Names the missing fields.
    context = dict(context, native_format=native_format_record(context["native_format"]))
    validate_context(context, CANDIDATE_SCHEMA_VERSION)
    return context


def _viewports(context):
    """Eye regions inside the declared crop, by the runtime mask's partition rule."""
    return eye_viewports(tuple(context["crop_xyxy"]), context["capture_layout"])


@dataclass(frozen=True, init=False)
class MappingCandidate:
    """Immutable, schema-checked evidence envelope; always unqualified."""
    _json: str

    def __init__(self):
        raise TypeError("hud_mapping_from_mapping_required")

    @classmethod
    def from_mapping(cls, record):
        # ``unknowns`` is optional; when present it names every null term.
        declared = type(record) is dict and "unknowns" in record
        _object(record, MAPPING_FIELDS + (("unknowns",) if declared else ()), "mapping")
        unknowns = record["unknowns"] if declared else {}
        if declared:
            _unknowns(unknowns)
        if (type(record["schema_version"]) is not int or record["schema_version"] not in CANDIDATE_SCHEMA_VERSIONS or
                record["status"] != "observed_unqualified" or record["mapping_qualified"] is not False):
            raise ValueError("hud_mapping_cannot_claim_qualification")
        _token(record["mapping_id"], "mapping_id")
        validate_context(record["context"], record["schema_version"])
        _integer(record["observed_ns"], 0, 2**63-1, "observation_time")
        _integer(record["expires_ns"], record["observed_ns"]+1,
                 min(2**63-1, record["observed_ns"]+MAX_MAPPING_LIFETIME_NS), "mapping_expiry")
        limits = record["operating_limits"]
        _object(limits, OPERATING_LIMIT_FIELDS, "operating_limits")
        if "operating_limits" in unknowns:
            for key in OPERATING_LIMIT_FIELDS:
                _unknown_null(limits[key])
        else:
            for key in ("yaw_degrees", "pitch_degrees", "roll_degrees"):
                _number(limits[key], 0, 180, key)
            _number(limits["angular_speed_degrees_s"], 0, 360, "angular_speed")
            if limits["display_mode"] not in ("passthrough_only", "dashboard_only"):
                raise ValueError("hud_display_mode_invalid")
        copies = record["copies"]
        if type(copies) is not list or not 1 <= len(copies) <= 4:
            raise ValueError("hud_copy_budget_invalid")
        viewports = _viewports(record["context"])
        identifiers, eyes = set(), set()
        regions_by_eye = [[] for _ in viewports]
        for copy in copies:
            _object(copy, ("copy_id", "declared_eye", "rect_native", "margin_px", "maximum_drift_px",
                           "localization_error_px", "filter_support_px"), "copy")
            _token(copy["copy_id"], "copy_id", 64)
            if copy["copy_id"] in identifiers:
                raise ValueError("hud_copy_id_duplicate")
            identifiers.add(copy["copy_id"])
            _integer(copy["declared_eye"], 0, len(viewports)-1, "declared_eye")
            eyes.add(copy["declared_eye"])
            eye = viewports[copy["declared_eye"]]
            _rect(copy["rect_native"], eye, "copy_rect")
            for key in COPY_ERROR_TERMS:
                if key in unknowns:
                    _unknown_null(copy[key])
                else:
                    _number(copy[key], 0, 128, key)
            # Known terms bound the margin from below (the contract's one margin
            # rule). An unknown term cannot raise that bound and is given no
            # value, 0 or otherwise: it stays listed in ``unknowns`` and
            # ``report()``, so the margin is underived.
            if not margin_valid(copy["margin_px"], [copy[key] for key in COPY_ERROR_TERMS if key not in unknowns]):
                raise ValueError("hud_conservative_margin_invalid")
            expanded = list(expand(copy["rect_native"], copy["margin_px"]))
            _rect(expanded, eye, "expanded_copy")
            regions_by_eye[copy["declared_eye"]].append(expanded)
        if eyes != set(range(len(viewports))):
            raise ValueError("hud_missing_declared_eye")
        for eye, regions in zip(viewports, regions_by_eye):
            if not within_blind_budget(regions, eye):
                raise ValueError("hud_blind_region_budget_exceeded")
        evidence = record["evidence"]
        if type(evidence) is not list or not 1 <= len(evidence) <= 12:
            raise ValueError("hud_evidence_budget_invalid")
        evidence_ids = set()
        for item in evidence:
            _object(item, ("role", "artifact_id", "sha256", "kind"), "evidence")
            if item["role"] not in EVIDENCE_ROLES or item["kind"] not in (
                    "synthetic_fixture", "device_report", "runtime_query", "wearer_report"):
                raise ValueError("hud_evidence_provenance_invalid")
            _token(item["artifact_id"], "artifact_id")
            _sha256(item["sha256"], "evidence_digest")
            if item["artifact_id"] in evidence_ids:
                raise ValueError("hud_evidence_id_duplicate")
            evidence_ids.add(item["artifact_id"])
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            raise ValueError("hud_mapping_record_budget_exceeded")
        candidate = object.__new__(cls)
        object.__setattr__(candidate, "_json", encoded)
        return candidate

    def as_dict(self):
        # Never expose mutable internal context or evidence references.
        return json.loads(self._json)

    def check_context(self, current, now_ns):
        """``current`` (a version 1 or 2 context) must equal the record's context exactly, metadata included."""
        current = canonical_context(current)
        record = self.as_dict()
        _integer(now_ns, 0, 2**63-1, "current_time")
        if current != canonical_context(record["context"]):
            raise ValueError("hud_mapping_context_changed")
        if not record["observed_ns"] <= now_ns < record["expires_ns"]:
            raise ValueError("hud_mapping_expired_or_clock_changed")

    def report(self):
        record = self.as_dict()
        roles = {item["role"] for item in record["evidence"]}
        return {"report_kind": "hud_mapping_schema_check", "schema_version": 1,
                "status": "candidate_valid_unqualified", "exit_code": 2,
                "mapping_id": record["mapping_id"], "record_schema_version": record["schema_version"], **claims(),
                "evidence_contents_verified": False, "physical_expiry_verified": False,
                "missing_evidence_roles": [role for role in EVIDENCE_ROLES if role not in roles],
                "unknown_error_terms": record.get("unknowns", {}),
                "scope": "Schema and declared context only; artifact references do not qualify hardware"}

    def calibration(self, config=HUD_CONFIG):
        """Issue this candidate as a runtime ``Calibration``, one of its two factories.

        The result carries the record's context tokens, ``observed_ns`` and
        ``expires_ns``. It is always unvalidated with ``observed_unqualified``
        provenance, so ``HudMask`` rejects it: this never enables masking,
        inference or output from a hardware mapping. Conditions the runtime
        mask cannot represent fail closed here instead of being dropped.
        """
        if not isinstance(config, HudConfig):
            raise ValueError("hud_config_fields_invalid")
        record = self.as_dict()
        context = record["context"]
        if context["hud_config_sha256"] != config_digest(config):
            raise ValueError("hud_config_changed")
        fmt = CaptureFormat.from_mapping(context["native_format"])
        if tuple(context["crop_xyxy"]) != fmt.bounds:
            raise ValueError("hud_runtime_crop_unsupported")
        if context["orientation_degrees"] != 0:
            raise ValueError("hud_runtime_orientation_unsupported")
        copies = sorted(record["copies"], key=lambda copy: copy["declared_eye"])
        if len({copy["declared_eye"] for copy in copies}) != len(copies):
            raise ValueError("hud_runtime_multiple_copies_per_eye_unsupported")
        footprints = tuple(Footprint(copy["declared_eye"], tuple(copy["rect_native"]), copy["margin_px"])
                           for copy in copies)
        return Calibration._issue(
            source_id=context["source_id"], capture_format=fmt, capture_layout=context["capture_layout"],
            footprints=footprints, config=config, provenance=CANDIDATE_PROVENANCE,
            **{name: context[name] for name in SESSION_TOKENS}, crop_xyxy=tuple(context["crop_xyxy"]),
            orientation_degrees=context["orientation_degrees"], hud_config_sha256=context["hud_config_sha256"],
            observed_ns=record["observed_ns"], expires_ns=record["expires_ns"])

    def require_production_qualification(self):
        # No qualified mapping exists in this checkout; the candidate is never one.
        HARDWARE_MAPPING.require(False, "hud_hardware_mapping_not_approved")


class ValidationLedger:
    """Bounded offline checks of proposed G5 telemetry, never physical proof.

    Native boxes refer to the original acquisition coordinate system. This
    auditor does not read images, run a detector, submit output or grant gates.
    Any context/timing/feedback error latches invalidation for the entire run,
    and ``failure_kind`` names its class (``FAILURE_KINDS``). A box that touches
    the mask or crosses an eye invalidates the run under the runtime mask's own
    rule (``geometry.exclusion``): the runtime drops that box from presentation,
    and a validation run with it fails. The default maximum age is the
    contract's ``MAX_DISPLAY_AGE_NS``, the pipeline's cap.
    """
    def __init__(self, candidate, current_context, now_ns, *, max_age_ns=MAX_DISPLAY_AGE_NS, maximum_results=1000):
        if not isinstance(candidate, MappingCandidate):
            raise ValueError("hud_mapping_candidate_required")
        candidate.check_context(current_context, now_ns)
        _integer(max_age_ns, 1, MAX_DISPLAY_AGE_NS, "maximum_age")
        _integer(maximum_results, 1, 1000, "result_budget")
        self.candidate = candidate
        self.max_age_ns = max_age_ns
        self.maximum_results = maximum_results
        self.results = self.rejected = self.last_sequence = 0
        self.maximum_input_age_ns = 0
        self.last_observed_ns = -1
        self.last_completed_ns = now_ns
        self.invalidated_reason = self.failure_kind = None

    def record_result(self, *, current_context, source_id, frame_sequence, result_sequence,
                      observed_ns, completed_ns, expires_ns, boxes_native):
        if self.invalidated_reason:
            raise ValueError("hud_validation_run_invalidated")
        kind = "context"
        try:
            self.candidate.check_context(current_context, completed_ns)
            kind = None
            self._record(current_context, source_id, frame_sequence, result_sequence,
                         observed_ns, completed_ns, expires_ns, boxes_native)
        except ValueError as error:
            self.rejected += 1
            self.invalidated_reason = str(error)
            self.failure_kind = kind or FAILURE_KINDS.get(self.invalidated_reason, "other")
            raise

    def _record(self, context, source, sequence, result_sequence, observed, completed, expiry, boxes):
        if completed < self.last_completed_ns:
            raise ValueError("hud_completion_clock_regressed")
        record = self.candidate.as_dict()
        _integer(sequence, 1, 2**63-1, "frame_sequence")
        if source != record["context"]["source_id"] or type(result_sequence) is not int or result_sequence != sequence:
            raise ValueError("hud_result_identity_mismatch")
        _integer(observed, record["observed_ns"], completed, "frame_observed_time")
        if sequence <= self.last_sequence or observed <= self.last_observed_ns:
            raise ValueError("hud_nonmonotonic_result")
        _integer(expiry, completed+1, min(observed+self.max_age_ns, record["expires_ns"]), "result_deadline")
        if self.results >= self.maximum_results:
            raise ValueError("hud_validation_result_budget_exceeded")
        if type(boxes) is not list or len(boxes) > 128:
            raise ValueError("hud_validation_detection_budget_exceeded")
        viewports = _viewports(context)
        regions = [expand(copy["rect_native"], copy["margin_px"]) for copy in record["copies"]]
        for box in boxes:
            _rect(box, context["crop_xyxy"], "native_detection_box")
            reason = exclusion(box, regions, viewports)
            if reason is not None:
                raise ValueError(EXCLUSION_REASONS[reason])
        self.results += 1
        self.last_sequence, self.last_observed_ns = sequence, observed
        self.last_completed_ns = completed
        self.maximum_input_age_ns = max(self.maximum_input_age_ns, completed-observed)

    def report(self):
        return {"report_kind": "hud_offline_validation_ledger", "status": "invalidated" if self.invalidated_reason
                else "telemetry_checked_unqualified", **claims(HARDWARE_MAPPING, LIVE_HUD_OUTPUT),
                "results_checked": self.results, "results_rejected": self.rejected,
                "invalidated_reason": self.invalidated_reason, "failure_kind": self.failure_kind,
                "max_age_ns": self.max_age_ns, "maximum_input_age_ns": self.maximum_input_age_ns,
                "physical_expiry_verified": False, "wearer_recovery_verified": False,
                "scope": "Declared telemetry only; no image, device or physical visibility observation"}
