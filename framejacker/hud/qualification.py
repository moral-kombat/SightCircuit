"""Offline HUD mapping records and bounded validation instrumentation.

These records describe proposed operating conditions and evidence references.
Their validity never grants hardware or production-output authority. In
particular, supplied JSON and artifact digests cannot prove eye composition,
coverage of hidden copies, physical visibility, or wearer recovery.
"""
from dataclasses import dataclass
import json
import math

from ..gates import HARDWARE_MAPPING, LIVE_HUD_OUTPUT, claims
from .contract import HUD_CONFIG, Calibration, CaptureFormat, Footprint, HudConfig, config_digest
from .geometry import LAYOUTS, contains, expand, eye_viewports, overlaps, within_blind_budget


MAX_RECORD_BYTES = 128 * 1024
MAX_MAPPING_LIFETIME_NS = 300 * 1_000_000_000
EVIDENCE_ROLES = ("observer", "composition", "copy_coverage", "motion_margin", "physical_expiry")


def _object(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"hud_{name}_fields_invalid")


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"hud_{name}_invalid")


def _token(value, name, limit=160):
    if (type(value) is not str or not 1 <= len(value) <= limit or
            any(ord(char) < 32 or ord(char) == 127 for char in value)):
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


def validate_context(context):
    """Validate declared acquisition metadata, never infer it from owned strides."""
    _object(context, ("source_id", "session_id", "runtime_id", "timebase_id", "tracking_origin",
                      "recenter_epoch", "native_format", "crop_xyxy", "orientation_degrees",
                      "capture_layout", "hud_config_sha256"), "context")
    for key in ("source_id", "session_id", "runtime_id", "timebase_id", "tracking_origin"):
        _token(context[key], key)
    _integer(context["recenter_epoch"], 0, 2**63-1, "recenter_epoch")
    _sha256(context["hud_config_sha256"], "config_digest")
    fmt = context["native_format"]
    _object(fmt, ("width", "height", "fourcc", "stride_bytes", "buffer_bytes", "minimum_buffer_bytes"),
            "native_format")
    # The runtime mask's own format rule; a record cannot describe a layout it would reject.
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
        _object(record, ("schema_version", "status", "mapping_qualified", "mapping_id", "context", "observed_ns",
                         "expires_ns", "operating_limits", "copies", "evidence"), "mapping")
        if (type(record["schema_version"]) is not int or record["schema_version"] != 1 or
                record["status"] != "observed_unqualified" or record["mapping_qualified"] is not False):
            raise ValueError("hud_mapping_cannot_claim_qualification")
        _token(record["mapping_id"], "mapping_id")
        validate_context(record["context"])
        _integer(record["observed_ns"], 0, 2**63-1, "observation_time")
        _integer(record["expires_ns"], record["observed_ns"]+1,
                 min(2**63-1, record["observed_ns"]+MAX_MAPPING_LIFETIME_NS), "mapping_expiry")
        limits = record["operating_limits"]
        _object(limits, ("yaw_degrees", "pitch_degrees", "roll_degrees", "angular_speed_degrees_s", "display_mode"),
                "operating_limits")
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
            for key in ("maximum_drift_px", "localization_error_px", "filter_support_px"):
                _number(copy[key], 0, 128, key)
            required_margin = math.ceil(sum(copy[key] for key in
                                           ("maximum_drift_px", "localization_error_px", "filter_support_px"))) + 2
            _integer(copy["margin_px"], max(8, required_margin), 128, "conservative_margin")
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
        validate_context(current)
        record = self.as_dict()
        _integer(now_ns, 0, 2**63-1, "current_time")
        if current != record["context"]:
            raise ValueError("hud_mapping_context_changed")
        if not record["observed_ns"] <= now_ns < record["expires_ns"]:
            raise ValueError("hud_mapping_expired_or_clock_changed")

    def report(self):
        record = self.as_dict()
        roles = {item["role"] for item in record["evidence"]}
        return {"report_kind": "hud_mapping_schema_check", "schema_version": 1,
                "status": "candidate_valid_unqualified", "exit_code": 2,
                "mapping_id": record["mapping_id"], **claims(),
                "evidence_contents_verified": False, "physical_expiry_verified": False,
                "missing_evidence_roles": [role for role in EVIDENCE_ROLES if role not in roles],
                "scope": "Schema and declared context only; artifact references do not qualify hardware"}

    def calibration(self, config=HUD_CONFIG):
        """Express this candidate's geometry as a runtime ``Calibration``.

        The result is always unvalidated with ``observed_unqualified``
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
        return Calibration(context["source_id"], fmt, context["capture_layout"], footprints, config,
                           validated=False, provenance="observed_unqualified")

    def require_production_qualification(self):
        # No qualified mapping exists in this checkout; the candidate is never one.
        HARDWARE_MAPPING.require(False, "hud_hardware_mapping_not_approved")


class ValidationLedger:
    """Bounded offline checks of proposed G5 telemetry, never physical proof.

    Native boxes refer to the original acquisition coordinate system. This
    auditor does not read images, run a detector, submit output or grant gates.
    Any context/timing/feedback error latches invalidation for the entire run.
    """
    def __init__(self, candidate, current_context, now_ns, *, max_age_ns=750_000_000, maximum_results=1000):
        if not isinstance(candidate, MappingCandidate):
            raise ValueError("hud_mapping_candidate_required")
        candidate.check_context(current_context, now_ns)
        _integer(max_age_ns, 1, 1_000_000_000, "maximum_age")
        _integer(maximum_results, 1, 1000, "result_budget")
        self.candidate = candidate
        self.max_age_ns = max_age_ns
        self.maximum_results = maximum_results
        self.results = self.rejected = self.last_sequence = 0
        self.maximum_input_age_ns = 0
        self.last_observed_ns = -1
        self.last_completed_ns = now_ns
        self.invalidated_reason = None

    def record_result(self, *, current_context, source_id, frame_sequence, result_sequence,
                      observed_ns, completed_ns, expires_ns, boxes_native):
        if self.invalidated_reason:
            raise ValueError("hud_validation_run_invalidated")
        try:
            self._record(current_context, source_id, frame_sequence, result_sequence,
                         observed_ns, completed_ns, expires_ns, boxes_native)
        except ValueError as error:
            self.rejected += 1
            self.invalidated_reason = str(error)
            raise

    def _record(self, context, source, sequence, result_sequence, observed, completed, expiry, boxes):
        self.candidate.check_context(context, completed)
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
        for box in boxes:
            _rect(box, context["crop_xyxy"], "native_detection_box")
            if sum(contains(eye, box) for eye in viewports) != 1:
                raise ValueError("hud_validation_cross_eye_detection")
            for copy in record["copies"]:
                if overlaps(box, expand(copy["rect_native"], copy["margin_px"])):
                    raise ValueError("hud_validation_mask_overlap")
        self.results += 1
        self.last_sequence, self.last_observed_ns = sequence, observed
        self.last_completed_ns = completed
        self.maximum_input_age_ns = max(self.maximum_input_age_ns, completed-observed)

    def report(self):
        return {"report_kind": "hud_offline_validation_ledger", "status": "invalidated" if self.invalidated_reason
                else "telemetry_checked_unqualified", **claims(HARDWARE_MAPPING, LIVE_HUD_OUTPUT),
                "results_checked": self.results, "results_rejected": self.rejected,
                "invalidated_reason": self.invalidated_reason, "maximum_input_age_ns": self.maximum_input_age_ns,
                "physical_expiry_verified": False, "wearer_recovery_verified": False,
                "scope": "Declared telemetry only; no image, device or physical visibility observation"}
