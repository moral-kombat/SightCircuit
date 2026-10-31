"""Proposed mapping envelopes and generated telemetry, never hardware evidence."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framejacker.hud_qualification import MappingCandidate, ValidationLedger, config_digest
from scripts.hud_validation_report import main, read_record


def candidate_record():
    return {"schema_version": 1, "status": "observed_unqualified", "mapping_qualified": False,
            "mapping_id": "generated-candidate-v1", "context": {
                "source_id": "synthetic:mirror", "session_id": "generated-session", "runtime_id": "fixture-runtime",
                "timebase_id": "generated-boot:monotonic", "tracking_origin": "seated", "recenter_epoch": 0,
                "native_format": {"width": 1920, "height": 1080, "fourcc": "BGR3", "stride_bytes": 5776,
                                  "buffer_bytes": 6238080, "minimum_buffer_bytes": 6238080},
                "crop_xyxy": [0, 0, 1920, 1080], "orientation_degrees": 0, "capture_layout": "stereo_sbs",
                "hud_config_sha256": "a"*64},
            "observed_ns": 100, "expires_ns": 10_000_000_100,
            "operating_limits": {"yaw_degrees": 15, "pitch_degrees": 10, "roll_degrees": 5,
                                 "angular_speed_degrees_s": 20, "display_mode": "passthrough_only"},
            "copies": [{"copy_id": f"declared-eye-{eye}", "declared_eye": eye,
                        "rect_native": [700+eye*960, 800, 900+eye*960, 900], "margin_px": 16,
                        "maximum_drift_px": 8, "localization_error_px": 2, "filter_support_px": 2}
                       for eye in range(2)],
            "evidence": [{"role": "observer", "artifact_id": "generated-observer-report", "sha256": "b"*64,
                          "kind": "synthetic_fixture"}]}


class QualificationRecordTests(unittest.TestCase):
    def test_complete_schema_and_all_evidence_references_never_grant_production(self):
        record = candidate_record()
        for role in ("composition", "copy_coverage", "motion_margin", "physical_expiry"):
            record["evidence"].append({"role": role, "artifact_id": f"declared-{role}", "sha256": "c"*64,
                                       "kind": "device_report"})
        candidate = MappingCandidate.from_mapping(record)
        candidate.check_context(record["context"], 101)
        report = candidate.report()
        self.assertEqual(report["status"], "candidate_valid_unqualified")
        self.assertEqual(report["missing_evidence_roles"], [])
        for field in ("mapping_qualified", "evidence_contents_verified", "physical_expiry_verified",
                      "production_hud_enabled", "production_live_input_enabled"):
            self.assertIs(report[field], False)
        with self.assertRaisesRegex(ValueError, "hardware_mapping_not_approved"):
            candidate.require_production_qualification()

    def test_record_is_owned_and_mutations_cannot_requalify_or_extend_lifetime(self):
        with self.assertRaises(TypeError):
            MappingCandidate()
        original = candidate_record()
        candidate = MappingCandidate.from_mapping(original)
        original["mapping_qualified"] = True
        original["context"]["source_id"] = "forged"
        returned = candidate.as_dict()
        returned["expires_ns"] += 10**12
        self.assertEqual(candidate.as_dict(), candidate_record())

    def test_boolean_or_edited_qualification_claim_is_rejected(self):
        changes = ({"mapping_qualified": True}, {"status": "qualified"}, {"validated": True},
                   {"schema_version": True}, {"expires_ns": 300_000_000_101})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                MappingCandidate.from_mapping(dict(candidate_record(), **change))

    def test_context_changes_invalidate_source_session_clock_recenter_native_layout_and_hud(self):
        record = candidate_record()
        candidate = MappingCandidate.from_mapping(record)
        changes = {"source_id": "other-source", "session_id": "new-session", "runtime_id": "runtime-updated",
                   "timebase_id": "new-boot:monotonic", "tracking_origin": "standing", "recenter_epoch": 1,
                   "crop_xyxy": [2, 0, 1920, 1080], "orientation_degrees": 180,
                   "capture_layout": "mono", "hud_config_sha256": "d"*64}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "context_changed"):
                candidate.check_context(dict(record["context"], **{key: value}), 101)
        context = deepcopy(record["context"])
        context["native_format"].update(stride_bytes=5760, buffer_bytes=6220800, minimum_buffer_bytes=6220800)
        with self.assertRaisesRegex(ValueError, "context_changed"):
            candidate.check_context(context, 101)
        for now in (99, record["expires_ns"], record["expires_ns"]+1):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, "expired_or_clock_changed"):
                candidate.check_context(record["context"], now)

    def test_missing_eye_bad_margin_cross_eye_or_unbounded_native_buffer_rejected(self):
        base = candidate_record()
        records = []
        item = deepcopy(base); item["copies"].pop(); records.append(item)
        item = deepcopy(base); item["copies"][0]["margin_px"] = 12; records.append(item)
        item = deepcopy(base); item["copies"][0]["rect_native"] = [940, 800, 980, 900]; records.append(item)
        item = deepcopy(base); item["copies"][0]["rect_native"] = [2, 2, 100, 100]; records.append(item)
        item = deepcopy(base); item["copies"][1]["copy_id"] = item["copies"][0]["copy_id"]; records.append(item)
        item = deepcopy(base); item["context"]["native_format"]["buffer_bytes"] = 2**40; records.append(item)
        item = deepcopy(base); item["context"]["native_format"]["minimum_buffer_bytes"] -= 1; records.append(item)
        item = deepcopy(base); item["operating_limits"]["yaw_degrees"] = float("nan"); records.append(item)
        item = deepcopy(base); item["evidence"][0]["sha256"] = "unknown"; records.append(item)
        for index, record in enumerate(records):
            with self.subTest(index=index), self.assertRaises(ValueError):
                MappingCandidate.from_mapping(record)

    def test_full_hud_geometry_digest_changes_with_individual_setting(self):
        self.assertEqual(config_digest({"width": 320, "x": .55}), config_digest({"x": .55, "width": 320}))
        self.assertNotEqual(config_digest({"width": 320, "x": .55}), config_digest({"width": 320, "x": .56}))

    def test_multiple_copies_cannot_exceed_total_per_eye_blind_region_budget(self):
        record = candidate_record()
        record["context"]["capture_layout"] = "mono"
        for copy, rect in zip(record["copies"], ([100, 100, 700, 500], [1100, 100, 1700, 500])):
            copy.update(declared_eye=0, rect_native=rect)
        with self.assertRaisesRegex(ValueError, "blind_region_budget"):
            MappingCandidate.from_mapping(record)

    def test_cli_checks_files_only_and_clean_record_returns_unqualified_exit_2(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate_path, context_path = (Path(directory)/name for name in ("candidate.json", "context.json"))
            record = candidate_record()
            candidate_path.write_text(json.dumps(record))
            context_path.write_text(json.dumps(record["context"]))
            args = ["--check-candidate", str(candidate_path), "--current-context", str(context_path), "--now-ns", "101"]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(args), 2)
            self.assertFalse(json.loads(stdout.getvalue())["mapping_qualified"])
            record["mapping_qualified"] = True
            candidate_path.write_text(json.dumps(record))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 1)
            candidate_path.write_bytes(b" " * (128*1024+1))
            with self.assertRaisesRegex(ValueError, "budget_exceeded"):
                read_record(candidate_path)

    def test_record_reader_rejects_nonregular_input_before_opening(self):
        with tempfile.TemporaryDirectory() as directory, patch("scripts.hud_validation_report.os.open") as opened:
            with self.assertRaisesRegex(ValueError, "regular_record_file"):
                read_record(Path(directory))
            opened.assert_not_called()

    def test_deep_json_within_byte_limit_returns_structured_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"nested.json"
            path.write_text("["*50000 + "0" + "]"*50000)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--check-candidate", str(path), "--current-context", str(path), "--now-ns", "101"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout.getvalue())["reason"], "hud_input_json_nesting_exceeded")


class ValidationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.record = candidate_record()
        self.candidate = MappingCandidate.from_mapping(self.record)
        self.context = self.record["context"]

    def result(self, **changes):
        args = dict(current_context=self.context, source_id=self.context["source_id"], frame_sequence=1,
                    result_sequence=1, observed_ns=100, completed_ns=150, expires_ns=200,
                    boxes_native=[[10, 10, 20, 20]])
        return dict(args, **changes)

    def test_original_frame_deadline_cannot_be_extended_after_inference_or_retransmission(self):
        ledger = ValidationLedger(self.candidate, self.context, 100, max_age_ns=100)
        with self.assertRaisesRegex(ValueError, "result_deadline"):
            ledger.record_result(**self.result(expires_ns=250))
        self.assertEqual(ledger.report()["status"], "invalidated")
        with self.assertRaisesRegex(ValueError, "run_invalidated"):
            ledger.record_result(**self.result())
        ledger = ValidationLedger(self.candidate, self.context, 100, max_age_ns=100)
        ledger.record_result(**self.result())
        with self.assertRaisesRegex(ValueError, "nonmonotonic"):
            ledger.record_result(**self.result(completed_ns=160))

    def test_mask_overlap_eye_crossing_wrong_identity_late_result_invalidate_run(self):
        changes = (dict(boxes_native=[[690, 790, 705, 810]]), dict(boxes_native=[[950, 10, 970, 20]]),
                   dict(result_sequence=2), dict(source_id="other"), dict(completed_ns=200),
                   dict(current_context=dict(self.context, recenter_epoch=1)))
        for change in changes:
            with self.subTest(change=change):
                ledger = ValidationLedger(self.candidate, self.context, 100, max_age_ns=100)
                with self.assertRaises(ValueError):
                    ledger.record_result(**self.result(**change))
                report = ledger.report()
                self.assertEqual(report["results_rejected"], 1)
                self.assertEqual(report["status"], "invalidated")
                self.assertFalse(report["physical_expiry_verified"])

    def test_completion_clock_cannot_precede_start_or_previous_completion(self):
        ledger = ValidationLedger(self.candidate, self.context, 170)
        with self.assertRaisesRegex(ValueError, "completion_clock_regressed"):
            ledger.record_result(**self.result())
        ledger = ValidationLedger(self.candidate, self.context, 100)
        ledger.record_result(**self.result(completed_ns=200, expires_ns=300))
        with self.assertRaisesRegex(ValueError, "completion_clock_regressed"):
            ledger.record_result(**self.result(frame_sequence=2, result_sequence=2, observed_ns=101))

    def test_valid_telemetry_is_bounded_and_does_not_claim_physical_evidence(self):
        with self.assertRaisesRegex(ValueError, "maximum_age"):
            ValidationLedger(self.candidate, self.context, 100, max_age_ns=1_000_000_001)
        ledger = ValidationLedger(self.candidate, self.context, 100, max_age_ns=100, maximum_results=1)
        ledger.record_result(**self.result())
        report = ledger.report()
        self.assertEqual(report["results_checked"], 1)
        self.assertEqual(report["maximum_input_age_ns"], 50)
        self.assertFalse(report["wearer_recovery_verified"])
        self.assertFalse(report["physical_expiry_verified"])
        self.assertFalse(report["production_hud_enabled"])
        with self.assertRaisesRegex(ValueError, "result_budget"):
            ledger.record_result(**self.result(frame_sequence=2, result_sequence=2, observed_ns=101))


if __name__ == "__main__":
    unittest.main()
