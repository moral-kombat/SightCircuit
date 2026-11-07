"""Runtime calibrations: two factories, carried context, expiry, session tokens and one margin rule.

Generated input and fixture records only; nothing here is hardware or Frame evidence.
"""
import ast
from dataclasses import FrozenInstanceError, replace
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sightcircuit.context import CaptureContext
from sightcircuit.frame import Frame
from sightcircuit.gates import claims
from sightcircuit.hud import HUD_CONFIG, Calibration, CaptureFormat, Footprint, HudMask, HudStage, config_digest
from sightcircuit.hud import contract, observation
from sightcircuit.hud import mask as hud_mask
from sightcircuit.hud.geometry import expand
from sightcircuit.hud.qualification import MappingCandidate, validate_context
from sightcircuit.runtime import synthetic
from tests.test_hud_qualification import candidate_record

ROOT = Path(__file__).resolve().parents[1]
WIDE = CaptureFormat(1280, 720, "BGR3", 3840, 2764800, 2764800)
CHANGED_TOKENS = {"session_id": "synthetic:other-session", "runtime_id": "synthetic:restarted",
                  "timebase_id": "synthetic:other-clock", "tracking_origin": "synthetic:standing",
                  "recenter_epoch": 1}


def candidate_calibration(record=None):
    """A calibration from a schema-checked candidate record, the non-synthetic factory."""
    record = record or candidate_record()
    record["context"]["hud_config_sha256"] = config_digest(HUD_CONFIG)
    return MappingCandidate.from_mapping(record).calibration()


def wide_fixture(margin=8):
    """A fixture whose footprint fits any margin up to 200 px, so only the margin rule decides."""
    return synthetic.fixture_calibration("synthetic:margin", WIDE, "mono", (Footprint(0, (600, 340, 680, 380), margin),))


def margin_record(margin, terms=(0, 0, 0)):
    """A candidate record whose copies fit any margin up to 128 px in their eyes and blind budget."""
    record = candidate_record()
    for eye, copy in enumerate(record["copies"]):
        copy.update(rect_native=[380 + eye * 960, 440, 580 + eye * 960, 640], margin_px=margin,
                    maximum_drift_px=terms[0], localization_error_px=terms[1], filter_support_px=terms[2])
    return record


def accepted(call):
    try:
        call()
    except ValueError:
        return False
    return True


class Declared(synthetic.SyntheticSession):
    kind, source_id, capture_layout = "synthetic", "synthetic:generated", "mono"


class CalibrationFactoryTests(unittest.TestCase):
    def test_hand_built_or_copied_calibration_is_rejected(self):
        fixture, candidate = synthetic.synthetic_hud_calibration(), candidate_calibration()
        builds = (
            lambda: Calibration(fixture.source_id, fixture.capture_format, "mono", fixture.footprints,
                                validated=True, provenance="synthetic_fixture"),
            lambda: Calibration(fixture.source_id, fixture.capture_format, "mono", fixture.footprints),
            lambda: Calibration(),
            lambda: replace(fixture, validated=True),
            lambda: replace(candidate, validated=True, provenance="synthetic_fixture"),
            lambda: replace(candidate, provenance="synthetic_fixture", source_id="synthetic:mirror"),
            lambda: replace(fixture, expires_ns=fixture.expires_ns + 10**12),
            lambda: replace(fixture, session_id="synthetic:other-session"),
            lambda: replace(fixture, footprints=candidate.footprints),
        )
        for index, build in enumerate(builds):
            with self.subTest(index=index), self.assertRaisesRegex(TypeError, "hud_calibration_factory_required"):
                build()
        # ``validated`` is derived from provenance, never a settable flag.
        with self.assertRaises((AttributeError, FrozenInstanceError)):
            candidate.validated = True
        with self.assertRaises(FrozenInstanceError):
            candidate.provenance = "synthetic_fixture"
        self.assertIs(candidate.validated, False)
        with self.assertRaisesRegex(ValueError, "hud_calibration_unvalidated"):
            HudMask(candidate, source_kind="synthetic")

    def test_only_the_two_factories_issue_calibrations(self):
        callers = set()
        for path in sorted(list((ROOT / "sightcircuit").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")) +
                           list((ROOT / "tests").glob("*.py"))):
            tree = ast.parse(path.read_text(), str(path))
            if any(isinstance(node, ast.Attribute) and node.attr == "_issue" for node in ast.walk(tree)):
                callers.add(str(path.relative_to(ROOT)))
        self.assertEqual(callers, {"sightcircuit/hud/qualification.py", "sightcircuit/runtime/synthetic.py"})

    def test_fixture_calibration_carries_context_tokens_and_lifetime(self):
        before = time.monotonic_ns()
        calibration = synthetic.synthetic_hud_calibration()
        after = time.monotonic_ns()
        self.assertEqual(calibration.provenance, "synthetic_fixture")
        self.assertIs(calibration.validated, True)
        self.assertEqual({name: getattr(calibration, name) for name in contract.SESSION_TOKENS},
                         synthetic.SYNTHETIC_SESSION)
        self.assertEqual({name: getattr(synthetic.SyntheticSource, name) for name in contract.SESSION_TOKENS},
                         synthetic.SYNTHETIC_SESSION)
        self.assertEqual(calibration.crop_xyxy, calibration.capture_format.bounds)
        self.assertEqual(calibration.orientation_degrees, 0)
        self.assertEqual(calibration.hud_config_sha256, config_digest(HUD_CONFIG))
        self.assertTrue(before <= calibration.observed_ns <= after)
        self.assertEqual(calibration.expires_ns - calibration.observed_ns, contract.MAX_MAPPING_LIFETIME_NS)
        context = calibration.context()
        validate_context(context)
        self.assertEqual(context["native_format"], {"width": 96, "height": 64, "fourcc": "BGR3", "stride_bytes": 288,
                                                    "buffer_bytes": 18432, "minimum_buffer_bytes": 18432})
        short = synthetic.synthetic_hud_calibration(lifetime_ns=5)
        self.assertEqual(short.expires_ns - short.observed_ns, 5)

    def test_fixture_refuses_hardware_sources_unbounded_lifetimes_and_inconsistent_records(self):
        footprints = (Footprint(0, (600, 340, 680, 380), 8),)
        cases = (
            (dict(source_id="frame:mirror"), "synthetic_fixture_source_required"),
            (dict(source_id=None), "synthetic_fixture_source_required"),
            (dict(lifetime_ns=0), "calibration_lifetime_invalid"),
            (dict(lifetime_ns=contract.MAX_MAPPING_LIFETIME_NS + 1), "calibration_lifetime_invalid"),
            (dict(lifetime_ns=1.5), "calibration_lifetime_invalid"),
            (dict(capture_format={"width": 1280}), "capture_format_invalid"),
            (dict(capture_format=replace(WIDE, stride_bytes=1)), "capture_format_invalid"),
            (dict(capture_layout="stereo_sbs_guess"), "capture_layout_invalid"),
            (dict(config={"layout_id": HUD_CONFIG.layout_id}), "config"),
        )
        for changes, reason in cases:
            arguments = dict(source_id="synthetic:margin", capture_format=WIDE, capture_layout="mono",
                             footprints=footprints)
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                synthetic.fixture_calibration(**arguments)

    def test_candidate_calibration_carries_the_record_context_and_expiry(self):
        record = candidate_record()
        record["context"]["hud_config_sha256"] = config_digest(HUD_CONFIG)
        calibration = MappingCandidate.from_mapping(record).calibration()
        self.assertEqual(calibration.context(), record["context"])
        self.assertEqual((calibration.observed_ns, calibration.expires_ns), (record["observed_ns"], record["expires_ns"]))
        self.assertEqual(calibration.provenance, "observed_unqualified")
        self.assertIs(calibration.validated, False)
        # The version 1 record pins six native-format fields and no V4L2 metadata.
        self.assertEqual(calibration.capture_format.metadata, ())
        self.assertFalse(any(claims().values()))
        with self.assertRaisesRegex(ValueError, "hud_calibration_unvalidated"):
            HudMask(calibration, source_kind="synthetic")

    def test_mask_regions_compare_candidate_geometry_without_approval(self):
        record = candidate_record()
        regions = hud_mask.mask_regions(candidate_calibration(record))
        self.assertEqual(regions, tuple(expand(copy["rect_native"], copy["margin_px"]) for copy in record["copies"]))
        fixture = synthetic.synthetic_hud_calibration()
        self.assertEqual(hud_mask.mask_regions(fixture), HudMask(fixture, source_kind="synthetic").regions)
        self.assertEqual(hud_mask.mask_regions(fixture), ((59, 34, 96, 64),))


class MarginRuleTests(unittest.TestCase):
    """One rule (``contract.margin_valid``) for the runtime mask, the observer and candidate records."""

    VALUES = (-1, 0, 1, 2, 3, 7, 8, 9, 16, 64, 127, 128, 129, 200, True, 8.0, "8", None)

    def consumers(self, margin):
        return {
            "mask": accepted(lambda: HudMask(wide_fixture(margin), source_kind="synthetic")),
            "observer": accepted(lambda: observation.ObserverConfig(margin_px=margin, maximum_drift_px=0).validate()),
            "qualification": accepted(lambda: MappingCandidate.from_mapping(margin_record(margin))),
        }

    def test_three_consumers_apply_one_margin_rule(self):
        for margin in self.VALUES:
            expected = type(margin) is int and 8 <= margin <= 128
            with self.subTest(margin=margin):
                self.assertIs(contract.margin_valid(margin), expected)
                self.assertEqual(self.consumers(margin), dict.fromkeys(("mask", "observer", "qualification"), expected))
        self.assertEqual((contract.MARGIN_MIN_PX, contract.MARGIN_MAX_PX, contract.MARGIN_GUARD_PX), (8, 128, 2))
        # The runtime mask's own reason: the margin rule, not geometry.
        with self.assertRaisesRegex(ValueError, "hud_mapping_invalid"):
            HudMask(wide_fixture(7), source_kind="synthetic")

    def test_every_site_follows_the_contract_bounds(self):
        with patch.object(contract, "MARGIN_MIN_PX", 12), patch.object(contract, "MARGIN_MAX_PX", 20):
            for margin in range(0, 140):
                expected = 12 <= margin <= 20
                with self.subTest(margin=margin):
                    self.assertEqual(self.consumers(margin), dict.fromkeys(("mask", "observer", "qualification"), expected))

    def test_known_error_terms_raise_the_minimum_once(self):
        terms = (8, 2, 2)
        self.assertEqual(contract.required_margin_px(terms), 14)
        self.assertEqual(contract.required_margin_px(()), 8)
        self.assertEqual(contract.required_margin_px((0.2, 0.3)), 8)
        self.assertEqual(contract.required_margin_px((7.5, 0.25)), 10)
        for bad in ((float("nan"),), (float("inf"),), (-1,), ("2",), (True,), (None,)):
            with self.subTest(terms=bad), self.assertRaisesRegex(ValueError, "margin_error_terms_invalid"):
                contract.margin_valid(16, bad)
        for margin, expected in ((13, False), (14, True), (128, True)):
            with self.subTest(margin=margin):
                self.assertIs(accepted(lambda: MappingCandidate.from_mapping(margin_record(margin, terms))), expected)
        with patch.object(contract, "MARGIN_GUARD_PX", 5):
            self.assertEqual(contract.required_margin_px(terms), 17)
            self.assertFalse(accepted(lambda: MappingCandidate.from_mapping(margin_record(16, terms))))
            self.assertTrue(accepted(lambda: MappingCandidate.from_mapping(margin_record(17, terms))))


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for owned-frame tests")
class SessionAndExpiryTests(unittest.TestCase):
    """The mask checks lifetime and session tokens at the capture-context seam."""

    def frame(self, sequence=1, **session):
        import numpy as np
        frame = Frame.owned_bgr(np.zeros((64, 96, 3), np.uint8), sequence, time.monotonic_ns(), "synthetic:generated")
        return replace(frame, metadata=dict(frame.metadata, **session))

    def test_declared_session_and_recenter_tokens_must_match_at_creation(self):
        mask = HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        context = CaptureContext(Declared(), mask=mask)
        context.admit(self.frame(1))
        for name, value in list(CHANGED_TOKENS.items()) + [("recenter_epoch", False), ("recenter_epoch", "0")]:
            source = type("Changed", (Declared,), {name: value})()
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, f"hud_{name}_changed"):
                CaptureContext(source, mask=mask)

    def test_frame_session_metadata_is_checked_and_a_missing_token_fails_closed(self):
        mask = HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        bare = type("Bare", (), dict(kind="synthetic", source_id="synthetic:generated", capture_layout="mono"))
        # An undeclared session cannot be assumed: the first frame is rejected and the failure latches.
        context = CaptureContext(bare(), mask=mask)
        with self.assertRaisesRegex(ValueError, "hud_session_id_undeclared"):
            context.admit(self.frame(1))
        with self.assertRaisesRegex(ValueError, "hud_session_id_undeclared"):
            context.admit(self.frame(2, **synthetic.SYNTHETIC_SESSION))
        self.assertEqual(context.failure, "hud_session_id_undeclared")
        partial = dict(synthetic.SYNTHETIC_SESSION)
        del partial["recenter_epoch"]
        with self.assertRaisesRegex(ValueError, "hud_recenter_epoch_undeclared"):
            CaptureContext(bare(), mask=mask).admit(self.frame(1, **partial))
        # Tokens reported by frames instead of declared by the source are accepted when they match.
        context = CaptureContext(bare(), mask=mask)
        context.admit(self.frame(1, **synthetic.SYNTHETIC_SESSION))
        context.admit(self.frame(2, **synthetic.SYNTHETIC_SESSION))
        for name, value in CHANGED_TOKENS.items():
            with self.subTest(name=name):
                fresh = CaptureContext(bare(), mask=mask)
                with self.assertRaisesRegex(ValueError, f"hud_{name}_changed"):
                    fresh.admit(self.frame(1, **dict(synthetic.SYNTHETIC_SESSION, **{name: value})))
                self.assertEqual(fresh.failure, f"hud_{name}_changed")
                # A frame contradicting a matching declaration is rejected too.
                declared = CaptureContext(Declared(), mask=mask)
                with self.assertRaisesRegex(ValueError, f"hud_{name}_changed"):
                    declared.admit(self.frame(1, **{name: value}))

    def test_declared_or_reported_crop_orientation_and_hud_digest_must_match_from_the_first_frame(self):
        mask = HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        # Undeclared means the full, upright frame of this HUD; equal values are accepted, a list crop included.
        matching = {"crop_xyxy": [0, 0, 96, 64], "orientation_degrees": 0, "hud_config_sha256": config_digest(HUD_CONFIG)}
        CaptureContext(type("Matching", (Declared,), matching)(), mask=mask).admit(self.frame(1, **matching))
        CaptureContext(Declared(), mask=mask).admit(self.frame(1, crop_xyxy=(0, 0, 96, 64)))
        changed = (("crop_xyxy", (0, 0, 48, 64)), ("crop_xyxy", [0, 0, 96, 64.0]), ("crop_xyxy", [0, 0, 96]),
                   ("crop_xyxy", "0,0,96,64"), ("orientation_degrees", 90), ("orientation_degrees", False),
                   ("orientation_degrees", 0.0), ("hud_config_sha256", "0" * 64), ("hud_config_sha256", None))
        reasons = {"crop_xyxy": "hud_crop_xyxy_changed", "orientation_degrees": "hud_orientation_degrees_changed",
                   "hud_config_sha256": "hud_config_sha256_changed"}
        for name, value in changed:
            reason = reasons[name]
            if value is not None:  # An undeclared source field is None; only a frame can report None.
                with self.subTest(name=name, value=value, where="declared"), self.assertRaisesRegex(ValueError, reason):
                    CaptureContext(type("Changed", (Declared,), {name: value})(), mask=mask)
            with self.subTest(name=name, value=value, where="reported"):
                context = CaptureContext(Declared(), mask=mask)
                with self.assertRaisesRegex(ValueError, reason):
                    context.admit(self.frame(1, **{name: value}))
                self.assertEqual(context.failure, reason)

    def test_expired_calibration_is_unusable_everywhere_the_mask_validates(self):
        calibration = synthetic.synthetic_hud_calibration()
        mask = HudMask(calibration, source_kind="synthetic")
        context = CaptureContext(Declared(), mask=mask)
        context.admit(self.frame(1))
        for now in (calibration.expires_ns, calibration.expires_ns + 1, calibration.observed_ns - 1):
            clock = SimpleNamespace(monotonic_ns=lambda now=now: now)
            calls = (mask.validate, lambda: HudMask(calibration, source_kind="synthetic"),
                     lambda: mask.rows_for([], mask.inference_shape(), .5),
                     lambda: CaptureContext(Declared(), mask=mask), lambda: context.admit(self.frame(2)))
            with patch.object(hud_mask, "time", clock):
                for index, call in enumerate(calls):
                    with self.subTest(now=now, call=index), self.assertRaisesRegex(
                            ValueError, "hud_calibration_expired_or_clock_changed"):
                        call()
        # The admission failure latched: the context stays failed after the clock is real again.
        with self.assertRaisesRegex(ValueError, "hud_calibration_expired_or_clock_changed"):
            context.admit(self.frame(3))


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"),
                     "NumPy and OpenCV required for the synthetic HUD pipeline")
class ExpiringPipelineTests(unittest.TestCase):
    def test_expired_calibration_stops_inference_and_presentation_and_withdraws_output(self):
        from sightcircuit.runtime import live
        lifetime_ns, slack_ns = 1_000_000_000, 50_000_000
        calibration = synthetic.synthetic_hud_calibration(lifetime_ns=lifetime_ns)
        events = []

        class Detector(synthetic.FixtureDetector):
            def detect(self, image, threshold):
                events.append(("detect", time.monotonic_ns()))
                return super().detect(image, threshold)

        class Sink(synthetic.HeadlessSink):
            def submit(self, processed):
                events.append(("submit", time.monotonic_ns(), processed.expires_ns))
                super().submit(processed)

            def clear(self):
                events.append(("clear", time.monotonic_ns()))
                super().clear()

        source = synthetic.SyntheticSource()
        started = time.monotonic()
        report = live.run_pipeline(source, Detector(), Sink(), 4, max_fps=10, hud=HudStage(source, calibration))
        self.assertLess(time.monotonic() - started, 3, "the calibration, not the run deadline, ended the run")
        self.assertEqual(report["status"], "error", report)
        self.assertEqual(report["reason"], "hud_calibration_expired_or_clock_changed")
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual((report["calibration_provenance"], report["calibration_expires_ns"]),
                         ("synthetic_fixture", calibration.expires_ns))
        submits = [event for event in events if event[0] == "submit"]
        self.assertTrue(submits, "the run presented results before the calibration expired")
        # No presented result outlives the calibration, and nothing starts after it expires.
        self.assertTrue(all(event[2] <= calibration.expires_ns for event in submits), submits)
        self.assertTrue(all(event[1] < calibration.expires_ns + slack_ns for event in events
                            if event[0] in ("detect", "submit")), events)
        last = max(index for index, event in enumerate(events) if event[0] == "submit")
        self.assertIn("clear", [event[0] for event in events[last + 1:]])


if __name__ == "__main__":
    unittest.main()
