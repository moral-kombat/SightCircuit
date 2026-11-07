"""Synthetic HUD validation: runtime exclusion counters, the ledger feed and the G5 software counts.

Generated frames and fixture calibrations only. These are cloud results, never
Frame evidence, and no gate is opened.
"""
import contextlib
from dataclasses import replace
import importlib.util
import inspect
import io
import json
from pathlib import Path
import re
import shlex
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.hud import HUD_CONFIG, CaptureFormat, Footprint, HudMask, HudStage, config_digest, contract
from sightcircuit.hud.qualification import UNKNOWN_TERMS, ValidationLedger
from sightcircuit.runtime import synthetic

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "templates" / "hud-device-validation-report.json"
# The template's g5_software fields that are not software counts: run limits and device-only fields.
G5_LIMITS = ("maximum_duration_seconds", "maximum_processing_fps", "max_age_ms")
G5_OTHER = ("status", "diagnostic_build_sha256", "expiry_cases", "raw_reports")
MONO_FOOTPRINT_BOX = (67, 42, 88, 56)  # synthetic_hud_calibration's footprint, inside its masked region.
NUMPY_CV2 = importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2")


def template_counts():
    """The G5 software counts the device report template names."""
    g5 = json.loads(TEMPLATE.read_text(encoding="utf-8"))["g5_software"]
    return [name for name in g5 if name not in G5_LIMITS + G5_OTHER]


class StereoSource(synthetic.SyntheticSource):
    """The generated 96x64 frames, declared as a side-by-side stereo capture."""
    source_id = "synthetic:stereo"
    capture_layout = "stereo_sbs"


def stereo_calibration():
    """Eyes (0, 0, 48, 64) and (48, 0, 96, 64); masked regions (22, 38, 44, 60) and (70, 38, 92, 60)."""
    return synthetic.fixture_calibration(
        StereoSource.source_id, CaptureFormat(96, 64, "BGR3", 288, 18432, 18432), "stereo_sbs",
        (Footprint(0, (30, 46, 36, 52), 8), Footprint(1, (78, 46, 84, 52), 8)))


class Boxes(synthetic.FixtureDetector):
    """Returns the same labelled boxes, in detector input pixels, for every frame."""

    def __init__(self, *boxes, delay=0.0):
        self.boxes, self.delay, self.calls = boxes, delay, 0

    def detect(self, image, threshold):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return DetectionResult(tuple(Detection(label, .9, box) for label, box in self.boxes), 0.0)


class Presented(synthetic.HeadlessSink):
    def open(self):
        super().open()
        self.items = []

    def submit(self, processed):
        self.items.append(processed)
        super().submit(processed)


class TemplateTests(unittest.TestCase):
    def test_template_is_json_and_names_exactly_the_reported_g5_software_counts(self):
        from sightcircuit.hud import validation
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        g5 = template["g5_software"]
        self.assertTrue(template["template_only"])
        self.assertEqual(template_counts(), list(validation.G5_SOFTWARE_COUNTS))
        for name in validation.G5_SOFTWARE_COUNTS:
            with self.subTest(count=name):
                self.assertIsNone(g5[name], "an unexecuted worksheet leaves every count unknown")
        self.assertEqual(g5["max_age_ms"], contract.MAX_DISPLAY_AGE_MS)


class MaximumAgeTests(unittest.TestCase):
    def test_every_consumer_uses_the_contract_maximum_age(self):
        from sightcircuit import overlay
        from sightcircuit.pipeline import Pipeline
        from sightcircuit.runtime import live
        from sightcircuit.sources import mirror_device
        from scripts import live_pipeline, mirror_recognize

        def default(function, name):
            return inspect.signature(function).parameters[name].default

        class Unread:
            """A fixture mirror that never delivers a frame."""
            capabilities = {"driver": "fixture"}
            format = mirror_device.validate_format(2, 2, "GREY", 2, 4)

            def __init__(self, candidate):
                pass

            def open(self):
                pass

            def read_frame(self, timeout):
                return None

            def close(self):
                pass

        self.assertEqual(default(live.run_pipeline, "max_age_ms"), contract.MAX_DISPLAY_AGE_MS)
        self.assertIs(live_pipeline.run_pipeline, live.run_pipeline)  # The command adds no maximum of its own.
        self.assertEqual(default(ValidationLedger.__init__, "max_age_ns"), contract.MAX_DISPLAY_AGE_NS)
        # The mirror recognizer's engine, built by the shared runner, bounds input age by the contract.
        with patch.object(live, "Pipeline", wraps=Pipeline) as built:
            report, _ = live.run_recognition({}, .1, threading.Event(), backend_factory=Unread,
                                             detector_factory=synthetic.FixtureDetector)
        self.assertEqual(report["status"], "no_frames", report)
        self.assertEqual(built.call_args.kwargs["max_input_age_ms"], contract.MAX_DISPLAY_AGE_MS)
        self.assertIs(mirror_recognize.run_recognition, live.run_recognition)
        self.assertEqual(live.new_mirror_report()["maximum_input_observation_age_ms"], contract.MAX_DISPLAY_AGE_MS)
        self.assertIs(overlay.MAX_DISPLAY_AGE_NS, contract.MAX_DISPLAY_AGE_NS)
        self.assertEqual(json.loads(TEMPLATE.read_text(encoding="utf-8"))["g5_software"]["max_age_ms"],
                         contract.MAX_DISPLAY_AGE_MS)
        self.assertEqual(contract.MAX_DISPLAY_AGE_MS * 1_000_000, contract.MAX_DISPLAY_AGE_NS)

    def test_the_engine_refuses_a_hud_result_lifetime_past_the_cap(self):
        from sightcircuit.pipeline import Pipeline
        for age_ms in (contract.MAX_DISPLAY_AGE_MS + 1, contract.MAX_DISPLAY_AGE_MS + .001, 300_000):
            with self.subTest(age_ms=age_ms), self.assertRaisesRegex(ValueError, "hud_max_age_exceeds_contract"):
                Pipeline(object(), synthetic.FixtureDetector(), (), max_input_age_ms=age_ms, hud=object())
        Pipeline(object(), synthetic.FixtureDetector(), (), max_input_age_ms=contract.MAX_DISPLAY_AGE_MS, hud=object())
        # Without a HUD stage the engine keeps its general bounds; replay and tests use longer ages.
        Pipeline(object(), synthetic.FixtureDetector(), (), max_input_age_ms=10_000)


class FixtureCandidateTests(unittest.TestCase):
    def test_fixture_candidate_is_unqualified_and_every_unknown_is_explicit(self):
        calibration = synthetic.synthetic_hud_calibration()
        candidate = synthetic.fixture_candidate(calibration)
        record, report = candidate.as_dict(), candidate.report()
        self.assertEqual((report["status"], report["exit_code"]), ("candidate_valid_unqualified", 2))
        self.assertFalse(report["mapping_qualified"])
        self.assertFalse(report["production_hud_enabled"])
        self.assertEqual(set(report["unknown_error_terms"]), set(UNKNOWN_TERMS))
        self.assertEqual(record["context"], calibration.context())
        self.assertEqual((record["observed_ns"], record["expires_ns"]), (calibration.observed_ns, calibration.expires_ns))
        self.assertEqual([(copy["declared_eye"], tuple(copy["rect_native"]), copy["margin_px"]) for copy in record["copies"]],
                         [(fp.eye, fp.rect, fp.margin_px) for fp in calibration.footprints])
        for copy in record["copies"]:
            self.assertEqual([copy[term] for term in ("maximum_drift_px", "localization_error_px", "filter_support_px")],
                             [None, None, None])
        self.assertEqual(set(record["operating_limits"].values()), {None})
        self.assertEqual([item["kind"] for item in record["evidence"]], ["synthetic_fixture"])
        with self.assertRaisesRegex(ValueError, "hardware_mapping_not_approved"):
            candidate.require_production_qualification()
        with self.assertRaisesRegex(ValueError, "hud_synthetic_fixture_required"):
            synthetic.fixture_candidate(candidate.calibration())


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for HUD geometry")
class OneExclusionRuleTests(unittest.TestCase):
    def test_runtime_and_ledger_classify_every_box_with_one_rule(self):
        calibration = stereo_calibration()
        mask = HudMask(calibration, source_kind="synthetic")
        self.assertEqual(mask.inference_shape(), (64, 96))  # Detector pixels are native pixels here.
        cases = {"clean": ((5, 5, 15, 15), None),
                 "touches_region_boundary": ((44, 40, 47, 50), None),
                 "inside_footprint": ((30, 46, 36, 52), "mask_overlap"),
                 "corner_of_margin": ((43, 59, 46, 62), "mask_overlap"),
                 "crosses_eyes": ((40, 5, 56, 20), "eye_crossing"),
                 "crosses_eyes_and_overlaps": ((40, 40, 56, 50), "mask_overlap")}
        reasons = {None: None, "mask_overlap": "hud_validation_mask_overlap",
                   "eye_crossing": "hud_validation_cross_eye_detection"}
        for name, (box, expected) in cases.items():
            with self.subTest(box=name):
                rows, checks = mask.select([Detection(name, .9, box)], mask.inference_shape(), .5)
                self.assertEqual(checks.boxes_native, (box,))
                runtime = ("mask_overlap" if checks.mask_overlap_rejections else
                           "eye_crossing" if checks.eye_crossing_rejections else None)
                self.assertEqual(runtime, expected)
                self.assertEqual(len(rows), 0 if expected else 1)
                self.assertEqual(mask.rows_for([Detection(name, .9, box)], mask.inference_shape(), .5), rows)
                now = time.monotonic_ns()
                ledger = ValidationLedger(synthetic.fixture_candidate(calibration), calibration.context(), now)
                try:
                    ledger.record_result(current_context=calibration.context(), source_id=calibration.source_id,
                                         frame_sequence=1, result_sequence=1, observed_ns=now,
                                         completed_ns=now + 10, expires_ns=now + 1000, boxes_native=[list(box)])
                    verdict = None
                except ValueError as error:
                    verdict = str(error)
                self.assertEqual(verdict, reasons[expected])
                self.assertEqual(ledger.report()["status"], "invalidated" if expected else "telemetry_checked_unqualified")


class LedgerFailureKindTests(unittest.TestCase):
    """Each ledger invalidation names the G5 count it belongs to; the first one latches."""

    def setUp(self):
        from tests.test_hud_qualification import candidate_record
        from sightcircuit.hud.qualification import MappingCandidate
        self.record = candidate_record()
        self.candidate = MappingCandidate.from_mapping(self.record)
        self.context = self.record["context"]

    def failed(self, **changes):
        ledger = ValidationLedger(self.candidate, self.context, 100, max_age_ns=100)
        result = dict(current_context=self.context, source_id=self.context["source_id"], frame_sequence=1,
                      result_sequence=1, observed_ns=100, completed_ns=150, expires_ns=200,
                      boxes_native=[[10, 10, 20, 20]])
        with self.assertRaises(ValueError):
            ledger.record_result(**dict(result, **changes))
        return ledger

    def test_every_invalidation_has_a_failure_kind(self):
        cases = ((dict(current_context=dict(self.context, recenter_epoch=1)), "context"),
                 (dict(current_context=dict(self.context, session_id="")), "context"),
                 (dict(completed_ns=10_000_000_100), "context"),
                 (dict(result_sequence=2), "identity"), (dict(source_id="other"), "identity"),
                 (dict(frame_sequence=0, result_sequence=0), "identity"),
                 (dict(expires_ns=250), "timing"), (dict(observed_ns=99), "timing"),
                 (dict(boxes_native=[[690, 790, 705, 810]]), "exclusion"),
                 (dict(boxes_native=[[950, 10, 970, 20]]), "exclusion"),
                 (dict(boxes_native=[[10, 10, 10, 20]]), "exclusion"),
                 (dict(boxes_native=[[10, 10, 20, 20]] * 129), "budget"))
        for changes, kind in cases:
            with self.subTest(changes=list(changes)):
                report = self.failed(**changes).report()
                self.assertEqual((report["status"], report["failure_kind"]), ("invalidated", kind))
        ledger = ValidationLedger(self.candidate, self.context, 100)
        self.assertIsNone(ledger.report()["failure_kind"])
        self.assertEqual(ledger.report()["max_age_ns"], contract.MAX_DISPLAY_AGE_NS)

    def test_ledger_context_failure_is_a_counted_context_invalidation(self):
        from sightcircuit.hud.validation import ValidationSink
        sink = ValidationSink(None, None, 100)
        sink.ledger = self.failed(current_context=dict(self.context, recenter_epoch=1))
        checks = dict.fromkeys(("frames_checked", "unmasked_footprint_px", "mask_zeroing_failures",
                                "mask_overlap_rejections", "eye_crossing_rejections"), 0)
        report = sink.software_report(dict(checks, frames_checked=1), None, completed=False, max_age_ms=1000,
                                      max_fps=5, seconds=1)
        self.assertEqual((report["status"], report["context_invalidations"], report["failure_reason"]),
                         ("failed", 1, "hud_mapping_context_changed"))
        self.assertEqual(report["frame_result_identity_failures"], 0)
        sink.ledger = ValidationLedger(self.candidate, self.context, 100)
        clean = sink.software_report(dict(checks, frames_checked=1), None, completed=True, max_age_ms=1000,
                                     max_fps=5, seconds=1)
        self.assertEqual((clean["status"], clean["failure_reason"]), ("checked_unqualified", None))
        self.assertEqual(sink.software_report(checks, None, completed=False, max_age_ms=1000, max_fps=5,
                                              seconds=1)["status"], "incomplete")


def documented_live_pipeline_commands(document):
    """``(logical line, arguments)`` of each ``scripts.live_pipeline`` command in a document's code blocks."""
    commands = []
    for block in re.findall(r"```bash\n(.*?)```", (ROOT / "docs" / document).read_text(encoding="utf-8"), re.S):
        for line in block.replace("\\\n", " ").splitlines():
            words = shlex.split(line)
            if "scripts.live_pipeline" in words:
                commands.append((line, words[words.index("scripts.live_pipeline") + 1:]))
    return commands


class G1RerunTests(unittest.TestCase):
    """The documented G1 rerun runs the default detector on the masked generated frames.

    With the zero-filled mask (881fea7) MobileNetSSD boxed the footprint itself,
    so the rerun used the fixture detector. The neutral fill removed that
    artifact (``tests/test_hud_fill.py`` runs the real form, runner CPU); the
    fixture form still checks the mask, ledger and transport without a model.
    """

    def run_main(self, arguments):
        from scripts import live_pipeline
        with patch.object(live_pipeline, "supervise_synthetic", return_value={"exit_code": 0}) as supervised, \
                contextlib.redirect_stdout(io.StringIO()):
            code = live_pipeline.main(arguments)
        return code, supervised

    def test_cli_passes_the_detector_choice_to_the_worker(self):
        from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS
        # "real" stays accepted as the default model's earlier name.
        choices = [([], DEFAULT_DETECTOR), (["--detector", "real"], DEFAULT_DETECTOR),
                   (["--detector", "fixture"], "fixture")]
        choices += [(["--detector", name], name) for name in DETECTORS]
        for arguments, detector in choices:
            with self.subTest(arguments=arguments):
                code, supervised = self.run_main(["--synthetic-check", "--synthetic-hud", *arguments])
                self.assertEqual(code, 0)
                self.assertEqual(supervised.call_args.args[0]["detector"], detector)
        for arguments in (["--synthetic-check", "--detector", "mobilenet"], ["--detector", "fixture"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                self.run_main(arguments)

    def test_documented_commands_parse_and_the_g1_rerun_uses_the_default_detector(self):
        commands = {document: documented_live_pipeline_commands(document)
                    for document in ("reference/pipeline.md", "reference/hud-development.md")}
        self.assertGreaterEqual(sum(map(len, commands.values())), 4)
        for document, found in commands.items():
            for line, arguments in found:
                with self.subTest(document=document, line=line):
                    code, supervised = self.run_main(arguments)
                    self.assertEqual(code, 0)
                    supervised.assert_called_once()
        # The duplicated live-capture example now links to one canonical G1 rerun.
        for document, marker in (("reference/hud-development.md", "For a future justified rerun"),):
            text = (ROOT / "docs" / document).read_text(encoding="utf-8")
            block = re.search(r"```bash\n(.*?)```", text[text.index(marker):], re.S).group(1)
            hud_runs = [line for line in block.replace("\\\n", " ").splitlines() if "--synthetic-hud" in line]
            with self.subTest(document=document):
                self.assertEqual(len(hud_runs), 1)
                self.assertNotIn("--detector", hud_runs[0])  # The default model, not the fixture.
                self.assertIn("--validate-overlay", hud_runs[0])

    @unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for the synthetic HUD pipeline")
    def test_fixture_detector_synthetic_hud_check_passes_through_the_supervisor(self):
        from scripts import live_pipeline
        report = live_pipeline.supervise_synthetic({"seconds": 1, "synthetic_hud": True, "detector": "fixture"})
        self.assertEqual((report["status"], report["exit_code"], report["detector"]), ("completed", 0, "fixture"), report)
        self.assertTrue(report["cleanup_ok"], report)
        g5 = report["g5_software"]
        self.assertEqual((g5["status"], g5["failure_reason"]), ("checked_unqualified", None))
        self.assertGreater(g5["frames_checked"], 0)
        self.assertEqual([g5[name] for name in template_counts()[1:]], [0] * 5)
        self.assertEqual(report["validation_ledger"]["results_checked"], report["frames_submitted"])
        self.assertIs(report["vr_runtime_initialized"], False)


class RunStatusPrecedenceTests(unittest.TestCase):
    """``validation_failed`` replaces a clean or empty run's status; a run's own failure keeps its status and reason."""

    def outcome(self, status, exit_code, reason=None, *, ledger_reason=None, overlaps=1):
        from sightcircuit.pipeline import HUD_CHECK_FIELDS
        from sightcircuit.runtime import live
        from sightcircuit.hud.validation import ValidationSink
        calibration = synthetic.synthetic_hud_calibration()
        validation = ValidationSink(None, None, contract.MAX_DISPLAY_AGE_NS)
        validation.ledger = ValidationLedger(synthetic.fixture_candidate(calibration), calibration.context(),
                                             time.monotonic_ns())
        validation.ledger.invalidated_reason = ledger_reason
        checks = dict(dict.fromkeys(HUD_CHECK_FIELDS, 0), frames_checked=2, mask_overlap_rejections=overlaps)
        failure = "source_or_session_changed" if reason == "source_or_session_changed" else None
        pipeline = SimpleNamespace(capture_context=SimpleNamespace(failure=failure),
                                   stats=SimpleNamespace(hud_checks=checks))
        report = {"status": status, "exit_code": exit_code, **({"reason": reason} if reason else {})}
        live.add_validation(report, validation, pipeline, max_age_ms=1000, max_fps=5, seconds=1)
        return report

    def test_validation_failure_replaces_only_a_clean_empty_or_its_own_status(self):
        overlap = "hud_validation_mask_overlap"
        for status, code in (("completed", 0), ("no_frames", 1)):
            with self.subTest(status=status):
                report = self.outcome(status, code)
                self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                                 ("validation_failed", 1, overlap))
        # The run stopped on the ledger's own rejection, which raised through the engine.
        report = self.outcome("error", 1, overlap, ledger_reason=overlap)
        self.assertEqual((report["status"], report["exit_code"], report["reason"]), ("validation_failed", 1, overlap))
        for status, code, reason in (("error", 1, "source_or_session_changed"), ("error", 1, "detector failed"),
                                     ("cleanup_failed", 1, None), ("cancelled", 130, None)):
            with self.subTest(status=status, reason=reason):
                report = self.outcome(status, code, reason)
                self.assertEqual((report["status"], report["exit_code"], report.get("reason")), (status, code, reason))
                self.assertEqual((report["g5_software"]["status"], report["g5_software"]["failure_reason"]),
                                 ("failed", overlap))
        clean = self.outcome("completed", 0, overlaps=0)
        self.assertEqual((clean["status"], clean["exit_code"], clean.get("reason")), ("completed", 0, None))
        self.assertEqual(clean["g5_software"]["status"], "checked_unqualified")
        # Only a completed run is checked; a clean run that presented nothing is incomplete.
        empty = self.outcome("no_frames", 1, overlaps=0)
        self.assertEqual((empty["status"], empty["g5_software"]["status"]), ("no_frames", "incomplete"))


class ObservedContextTests(unittest.TestCase):
    def test_observed_context_takes_declared_and_reported_view_fields(self):
        from sightcircuit.hud.validation import observed_context
        calibration = synthetic.synthetic_hud_calibration()
        source = type("Declared", (synthetic.SyntheticSession,),
                      {"source_id": calibration.source_id, "capture_layout": calibration.capture_layout})()
        fmt = calibration.context()["native_format"]
        self.assertEqual(observed_context(synthetic.SyntheticSession(), fmt, HUD_CONFIG)["hud_config_sha256"],
                         config_digest(HUD_CONFIG))
        declared = type("Declared", (), {"crop_xyxy": (0, 0, 48, 64), "orientation_degrees": 90,
                                         "hud_config_sha256": "1" * 64})()
        context = observed_context(declared, fmt, HUD_CONFIG)
        self.assertEqual((context["crop_xyxy"], context["orientation_degrees"], context["hud_config_sha256"]),
                         ([0, 0, 48, 64], 90, "1" * 64))
        reported = {"crop_xyxy": (0, 0, 96, 32), "orientation_degrees": 180, "hud_config_sha256": "2" * 64}
        context = observed_context(declared, fmt, HUD_CONFIG, reported)
        self.assertEqual((context["crop_xyxy"], context["orientation_degrees"], context["hud_config_sha256"]),
                         ([0, 0, 96, 32], 180, "2" * 64))
        # The ledger compares what the run observes, not the stage's own configuration.
        for changes in ({"crop_xyxy": (0, 0, 48, 64)}, {"orientation_degrees": 90}, {"hud_config_sha256": "0" * 64}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "hud_mapping_context_changed"):
                ValidationLedger(synthetic.fixture_candidate(calibration),
                                 observed_context(source, fmt, HUD_CONFIG, changes), time.monotonic_ns())
        ValidationLedger(synthetic.fixture_candidate(calibration), observed_context(source, fmt, HUD_CONFIG),
                         time.monotonic_ns())

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for generated frames")
    def test_sink_hands_the_ledger_the_view_fields_a_frame_reports(self):
        """Defence in depth behind the mask: the ledger sees what each frame reports, not only the source."""
        import numpy as np
        from sightcircuit.frame import Frame
        from sightcircuit.hud.contract import HudChecks, HudOutput
        from sightcircuit.hud.validation import ValidationSink
        calibration = synthetic.synthetic_hud_calibration()
        source = type("Declared", (synthetic.SyntheticSession,),
                      {"source_id": calibration.source_id, "capture_layout": calibration.capture_layout})()
        stage = SimpleNamespace(source=source, config=HUD_CONFIG, mask=SimpleNamespace(calibration=calibration))

        def processed(sequence, **reported):
            frame = Frame.owned_bgr(np.zeros((64, 96, 3), np.uint8), sequence, time.monotonic_ns(), calibration.source_id)
            frame = replace(frame, metadata=dict(frame.metadata, **reported))
            return SimpleNamespace(frame=frame, hud=HudOutput((), HUD_CONFIG, HudChecks(frame_sequence=sequence)),
                                   completed_ns=frame.observed_ns + 1, expires_ns=frame.observed_ns + 1_000_000)

        for sequence, reported in enumerate(({"crop_xyxy": [0, 0, 48, 64]}, {"orientation_degrees": 90},
                                             {"hud_config_sha256": "0" * 64}), start=1):
            with self.subTest(reported=reported):
                sink = ValidationSink(stage, synthetic.fixture_candidate, contract.MAX_DISPLAY_AGE_NS)
                sink.open()
                sink.submit(processed(1, crop_xyxy=[0, 0, 96, 64]))  # The calibration's own crop is accepted.
                with self.assertRaisesRegex(ValueError, "hud_mapping_context_changed"):
                    sink.submit(processed(2, **reported))
                self.assertEqual((sink.ledger.results, sink.ledger.failure_kind), (1, "context"))


@unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for the synthetic HUD pipeline")
class PipelineCounterTests(unittest.TestCase):
    def test_runtime_drops_rejected_boxes_and_counts_them_per_frame_and_in_total(self):
        from sightcircuit.pipeline import Pipeline
        from sightcircuit.sources.polled import PolledSource

        source, sink = StereoSource(), Presented()
        detector = Boxes(("footprint", (30, 46, 36, 52)), ("between-eyes", (40, 5, 56, 20)), ("chair", (5, 5, 15, 15)))
        pipeline = Pipeline(PolledSource(source), detector, (sink,), max_input_age_ms=1000, seconds=.3,
                            hud=HudStage(source, stereo_calibration()))
        stats = pipeline.run(threading.Event())
        self.assertGreater(stats.frames_submitted, 0)
        processed = stats.frames_processed
        self.assertEqual(stats.report()["hud_checks"],
                         {"frames_checked": processed, "unmasked_footprint_px": 0, "mask_zeroing_failures": 0,
                          "mask_overlap_rejections": processed, "eye_crossing_rejections": processed})
        for item in sink.items:  # Outside a validation run the runtime only drops the boxes for presentation.
            checks = item.hud.checks
            self.assertEqual([row.label for row in item.hud.rows], ["chair"])
            self.assertEqual((checks.mask_overlap_rejections, checks.eye_crossing_rejections,
                              checks.unmasked_footprint_px, checks.frame_sequence),
                             (1, 1, 0, item.frame.sequence))
            self.assertEqual(checks.boxes_native, ((30, 46, 36, 52), (40, 5, 56, 20), (5, 5, 15, 15)))
            self.assertLessEqual(item.frame.observed_ns, item.completed_ns)
            self.assertLess(item.completed_ns, item.expires_ns)
        plain = Pipeline(PolledSource(synthetic.SyntheticSource()), synthetic.FixtureDetector(), (Presented(),),
                         max_input_age_ms=1000, seconds=.1)
        self.assertIsNone(plain.run(threading.Event()).report()["hud_checks"])


@unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for the synthetic HUD pipeline")
class SyntheticValidationRunTests(unittest.TestCase):
    def run_hud(self, detector, *, source=None, calibration=None, sink=None, **options):
        from sightcircuit.runtime import live
        source = source or synthetic.SyntheticSource()
        calibration = calibration or synthetic.synthetic_hud_calibration()
        options.setdefault("seconds", .3)
        return live.run_pipeline(source, detector, sink or Presented(), options.pop("seconds"),
                                 hud=HudStage(source, calibration), **options)

    def assert_failed(self, report, reason):
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("validation_failed", 1, reason), report)
        self.assertEqual(report["g5_software"]["status"], "failed")
        self.assertTrue(report["cleanup_ok"], report)

    def test_clean_synthetic_hud_report_carries_every_g5_software_count(self):
        report = self.run_hud(Boxes(("chair", (5, 5, 15, 15))), max_fps=5, max_age_ms=800, seconds=.4)
        self.assertEqual((report["status"], report["exit_code"]), ("completed", 0), report)
        g5, ledger = report["g5_software"], report["validation_ledger"]
        for name in template_counts():
            with self.subTest(count=name):
                self.assertIs(type(g5[name]), int)
        self.assertGreater(g5["frames_checked"], 0)
        self.assertEqual(g5["frames_checked"], report["pipeline"]["hud_checks"]["frames_checked"])
        self.assertEqual([g5[name] for name in template_counts()[1:]], [0] * 5)
        self.assertEqual(g5["status"], "checked_unqualified")
        self.assertEqual((g5["max_age_ms"], g5["maximum_processing_fps"], g5["maximum_duration_seconds"]), (800, 5, .4))
        self.assertEqual(g5["evidence_kind"], "synthetic_fixture")
        self.assertIs(g5["physical_expiry_verified"], False)
        self.assertEqual(ledger["status"], "telemetry_checked_unqualified")
        self.assertEqual((ledger["results_checked"], ledger["results_rejected"]), (report["frames_submitted"], 0))
        self.assertGreater(ledger["results_checked"], 0)
        for claim in ("mapping_qualified", "production_hud_enabled", "physical_expiry_verified",
                      "wearer_recovery_verified"):
            self.assertIs(ledger[claim], False)
        self.assertEqual(report["last_result"]["hud_checks"],
                         {"mask_overlap_rejections": 0, "eye_crossing_rejections": 0, "unmasked_footprint_px": 0})

    def test_detector_boxing_the_hud_footprint_fails_the_run(self):
        from sightcircuit.hud import HudPipelineAdapter
        from sightcircuit.runtime import live
        from scripts import live_pipeline
        sink = Presented()
        report = self.run_hud(Boxes(("hud", MONO_FOOTPRINT_BOX), ("chair", (5, 5, 15, 15))), sink=sink)
        self.assert_failed(report, "hud_validation_mask_overlap")
        checks, g5 = report["pipeline"]["hud_checks"], report["g5_software"]
        self.assertGreaterEqual(checks["mask_overlap_rejections"], 1)
        self.assertEqual(g5["hud_footprint_detections"], checks["mask_overlap_rejections"])
        self.assertEqual(g5["mask_and_eye_boundary_rejections"], checks["mask_overlap_rejections"])
        self.assertEqual(g5["native_mask_zeroing_failures"], 0)
        self.assertEqual(report["validation_ledger"]["status"], "invalidated")
        self.assertEqual(report["validation_ledger"]["invalidated_reason"], "hud_validation_mask_overlap")
        self.assertEqual((report["validation_ledger"]["results_checked"],
                          report["validation_ledger"]["results_rejected"]), (0, 1))
        self.assertEqual((report["frames_submitted"], sink.items), (0, []))  # Never presented.
        live_pipeline.validate_worker_report(dict(report, vr_runtime_initialized=False), 1)
        # The command's own composition, with its sink-bound stage, fails the same way.
        source, sink = synthetic.SyntheticSource(), synthetic.HeadlessSink()
        report = live.run_pipeline(source, Boxes(("hud", MONO_FOOTPRINT_BOX)), sink, .3,
                                   hud=HudPipelineAdapter(source, sink, synthetic.synthetic_hud_calibration()))
        self.assert_failed(report, "hud_validation_mask_overlap")

    def test_detector_box_across_the_eye_boundary_fails_the_run(self):
        report = self.run_hud(Boxes(("between-eyes", (40, 5, 56, 20))), source=StereoSource(),
                              calibration=stereo_calibration())
        self.assert_failed(report, "hud_validation_cross_eye_detection")
        checks, g5 = report["pipeline"]["hud_checks"], report["g5_software"]
        self.assertGreaterEqual(checks["eye_crossing_rejections"], 1)
        self.assertEqual((checks["mask_overlap_rejections"], g5["hud_footprint_detections"]), (0, 0))
        self.assertEqual(g5["mask_and_eye_boundary_rejections"], checks["eye_crossing_rejections"])

    def test_footprint_pixels_left_after_masking_stop_the_run_before_the_detector(self):
        detector = Boxes()
        # A zeroing defect: the mask clears one row and one column less than its geometry rule.
        with patch.object(HudMask, "regions", property(lambda mask: ((60, 35, 96, 64),))):
            report = self.run_hud(detector)
        self.assert_failed(report, "hud_mask_zeroing_failed")
        self.assertEqual(detector.calls, 0)
        self.assertEqual(report["pipeline"]["hud_checks"],
                         {"frames_checked": 1, "unmasked_footprint_px": 37 + 29, "mask_zeroing_failures": 1,
                          "mask_overlap_rejections": 0, "eye_crossing_rejections": 0})
        self.assertEqual(report["g5_software"]["native_mask_zeroing_failures"], 1)
        self.assertEqual(report["frames_processed"], 0)

    def test_footprint_box_in_a_result_too_late_to_present_still_fails_the_run(self):
        report = self.run_hud(Boxes(("hud", MONO_FOOTPRINT_BOX), delay=.08), max_age_ms=30, seconds=.4)
        self.assert_failed(report, "hud_validation_mask_overlap")
        self.assertEqual(report["frames_submitted"], 0)
        self.assertGreaterEqual(report["pipeline"]["hud_checks"]["mask_overlap_rejections"], 1)
        self.assertEqual(report["validation_ledger"]["results_checked"], 0)
        self.assertIsNone(report["validation_ledger"]["invalidated_reason"])  # The counters alone fail it.

    def test_result_paired_with_another_frame_is_an_identity_failure(self):
        original = HudStage.render

        def render(stage, *args):
            tile, output = original(stage, *args)
            checks = output.checks
            return tile, type(output)(output.rows, output.config,
                                      type(checks)(checks.boxes_native, checks.mask_overlap_rejections,
                                                   checks.eye_crossing_rejections, checks.unmasked_footprint_px,
                                                   checks.frame_sequence + 1))

        with patch.object(HudStage, "render", render):
            report = self.run_hud(Boxes())
        self.assert_failed(report, "hud_result_identity_mismatch")
        self.assertEqual(report["g5_software"]["frame_result_identity_failures"], 1)
        self.assertEqual(report["frames_submitted"], 0)

    def test_runtime_context_invalidation_is_counted_and_keeps_its_error(self):
        source = synthetic.SyntheticSource(rate=1)
        original = HudStage.render

        def render(stage, *args):
            result = original(stage, *args)
            source.session_id = "changed-during-render"
            return result

        with patch.object(HudStage, "render", render):
            report = self.run_hud(synthetic.FixtureDetector(), source=source)
        self.assertEqual((report["status"], report["reason"]), ("error", "source_or_session_changed"), report)
        self.assertEqual(report["g5_software"]["context_invalidations"], 1)
        self.assertEqual(report["g5_software"]["status"], "failed")
        self.assertEqual(report["validation_ledger"]["status"], "telemetry_checked_unqualified")

    def test_declared_crop_orientation_or_hud_digest_stops_the_run_before_the_source_opens(self):
        for name, value in (("crop_xyxy", (0, 0, 48, 64)), ("orientation_degrees", 90),
                            ("hud_config_sha256", "0" * 64)):
            with self.subTest(name=name):
                source = synthetic.SyntheticSource()
                setattr(source, name, value)  # The runtime mask applies the full, upright frame of this HUD.
                with patch.object(source, "open") as opened:
                    report = self.run_hud(Boxes(), source=source)
                opened.assert_not_called()
                self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                                 ("error", 1, "hud_" + name.removeprefix("hud_") + "_changed"), report)
                self.assertEqual(report["g5_software"]["status"], "not_run")
                self.assertIsNone(report["validation_ledger"])

    def test_view_fields_frames_report_from_the_first_frame_fail_the_run(self):
        for name, value in (("crop_xyxy", [0, 0, 48, 64]), ("orientation_degrees", 90),
                            ("hud_config_sha256", "0" * 64)):
            class Reporting(synthetic.SyntheticSource):
                def read(self, stop, timeout=0.05):
                    frame = super().read(stop, timeout)
                    return None if frame is None else replace(frame, metadata=dict(frame.metadata, **{name: value}))

            with self.subTest(name=name):
                sink = Presented()
                report = self.run_hud(Boxes(("chair", (5, 5, 15, 15))), source=Reporting(), sink=sink)
                reason = "hud_" + name.removeprefix("hud_") + "_changed"
                self.assertEqual((report["status"], report["exit_code"], report["reason"]), ("error", 1, reason), report)
                g5 = report["g5_software"]
                self.assertEqual((g5["status"], g5["context_invalidations"], g5["failure_reason"]), ("failed", 1, reason))
                self.assertEqual((report["frames_submitted"], sink.items), (0, []))
                self.assertTrue(report["cleanup_ok"], report)

    def test_unstarted_validation_reports_unknown_counts_never_zero(self):
        unvalidated = synthetic.fixture_candidate(synthetic.synthetic_hud_calibration()).calibration()
        report = self.run_hud(synthetic.FixtureDetector(), calibration=unvalidated)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["g5_software"]["status"], "not_run")
        self.assertEqual({report["g5_software"][name] for name in template_counts()}, {None})
        self.assertIsNone(report["validation_ledger"])


if __name__ == "__main__":
    unittest.main()
