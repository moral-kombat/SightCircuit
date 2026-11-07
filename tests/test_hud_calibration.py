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
from sightcircuit.hud.validation import observed_context
from sightcircuit.runtime import synthetic
from tests.test_hud_qualification import candidate_record

ROOT = Path(__file__).resolve().parents[1]
WIDE = CaptureFormat(1280, 720, "BGR3", 3840, 2764800, 2764800)
CHANGED_TOKENS = {"session_id": "synthetic:other-session", "runtime_id": "synthetic:restarted",
                  "timebase_id": "synthetic:other-clock", "tracking_origin": "synthetic:standing",
                  "recenter_epoch": 1}


# The V4L2 format metadata a SteamVR mirror reports beside the six native-format fields
# (sightcircuit/sources/mirror_device.py get_format); the values are generated, not a Frame reading.
MIRROR_METADATA = {"field": 1, "colorspace_code": 8, "format_flags": 0, "ycbcr_encoding_code": 0,
                   "quantization_code": 0, "transfer_function_code": 0}


def mirror_record(metadata=None):
    """A version 2 candidate record whose native format carries mirror-style metadata."""
    record = candidate_record()
    record["schema_version"] = 2
    record["context"]["hud_config_sha256"] = config_digest(HUD_CONFIG)
    record["context"]["native_format"]["metadata"] = dict(MIRROR_METADATA if metadata is None else metadata)
    return record


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


class NativeFormatMetadataTests(unittest.TestCase):
    """A6 (2026-11-07): the record carries the mirror's native-format metadata and compares it exactly."""
    FLAT = {"width": 1920, "height": 1080, "fourcc": "BGR3", "stride_bytes": 5776, "buffer_bytes": 6238080,
            "minimum_buffer_bytes": 6238080, **MIRROR_METADATA}

    def test_capture_format_reads_the_flat_source_form_and_the_record_form_alike(self):
        flat = CaptureFormat.from_mapping(self.FLAT)
        record = CaptureFormat.from_mapping(mirror_record()["context"]["native_format"])
        self.assertEqual(flat, record)
        self.assertEqual(flat.metadata, tuple(sorted(MIRROR_METADATA.items())))
        self.assertEqual(flat.record(), mirror_record()["context"]["native_format"])
        self.assertEqual(CaptureFormat.from_mapping(flat.record()), flat)
        flat.validate()
        # No metadata: the six fields alone, the version 1 form, or an empty metadata object.
        six = {key: value for key, value in self.FLAT.items() if key not in MIRROR_METADATA}
        self.assertEqual(CaptureFormat.from_mapping(six), CaptureFormat.from_mapping(dict(six, metadata={})))
        self.assertEqual(CaptureFormat.from_mapping(six).record(), dict(six, metadata={}))
        # A differing or missing metadata value is a different format.
        self.assertNotEqual(flat, CaptureFormat.from_mapping(dict(self.FLAT, colorspace_code=1)))
        self.assertNotEqual(flat, CaptureFormat.from_mapping(six))
        # The record form carries every extra key inside the metadata object, none beside it.
        for mixed in (dict(six, metadata={"field": 1}, colorspace_code=8), dict(six, metadata=[("field", 1)]),
                      dict(six, metadata={1: 1})):
            with self.subTest(mixed=mixed), self.assertRaisesRegex(ValueError, "hud_capture_metadata_invalid"):
                CaptureFormat.from_mapping(mixed)
        with self.assertRaisesRegex(ValueError, "hud_capture_metadata_invalid"):
            CaptureFormat.from_mapping(dict(six, metadata={"note": "x" * 97})).validate()

    def test_version_2_record_with_mirror_metadata_is_accepted_and_version_1_stays_six_fields(self):
        record = mirror_record()
        candidate = MappingCandidate.from_mapping(record)
        self.assertEqual(candidate.as_dict(), record)
        self.assertEqual(candidate.report()["record_schema_version"], 2)
        self.assertIs(candidate.report()["mapping_qualified"], False)
        v1 = candidate_record()
        self.assertEqual(MappingCandidate.from_mapping(v1).report()["record_schema_version"], 1)
        cases = (("v1 with metadata", dict(v1, context=dict(v1["context"], native_format=dict(
                      v1["context"]["native_format"], metadata={}))), "native_format_fields_invalid"),
                 ("v2 without metadata", dict(record, context=dict(record["context"], native_format={
                      key: value for key, value in record["context"]["native_format"].items() if key != "metadata"})),
                  "native_format_fields_invalid"),
                 ("v2 metadata list", dict(record, context=dict(record["context"], native_format=dict(
                      record["context"]["native_format"], metadata=[]))), "native_format_fields_invalid"),
                 ("v2 flat metadata", dict(record, context=dict(record["context"], native_format=dict(
                      record["context"]["native_format"], colorspace_code=8))), "native_format_fields_invalid"),
                 ("schema 3", dict(record, schema_version=3), "cannot_claim_qualification"),
                 ("schema 0", dict(record, schema_version=0), "cannot_claim_qualification"))
        for label, changed, reason in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, reason):
                MappingCandidate.from_mapping(changed)

    def test_check_context_compares_the_mirror_metadata_exactly_in_either_form(self):
        record = mirror_record()
        candidate = MappingCandidate.from_mapping(record)
        now = record["observed_ns"] + 1
        candidate.check_context(record["context"], now)
        # The flat form a frame reports (observed_context) equals the record form.
        flat = observed_context(SimpleNamespace(**{"source_id": "synthetic:mirror", "capture_layout": "stereo_sbs",
                                                   **{name: record["context"][name] for name in contract.SESSION_TOKENS}}),
                                self.FLAT, HUD_CONFIG)
        self.assertEqual(flat["native_format"], record["context"]["native_format"])
        candidate.check_context(flat, now)
        for change in ({"colorspace_code": 1}, {"field": 0}, {}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "context_changed"):
                current = dict(flat, native_format={key: value for key, value in self.FLAT.items()
                                                    if key not in MIRROR_METADATA})
                current["native_format"].update(change and dict(MIRROR_METADATA, **change))
                candidate.check_context(current, now)
        # A version 1 record matches a frame without metadata only: a real mirror frame still fails, precisely.
        v1 = candidate_calibration()  # Builds from candidate_record(), a version 1 record.
        v1_candidate = MappingCandidate.from_mapping(dict(candidate_record(), context=v1.context() | {
            "native_format": {key: value for key, value in v1.context()["native_format"].items() if key != "metadata"}}))
        v1_candidate.check_context(v1.context(), now)
        with self.assertRaisesRegex(ValueError, "context_changed"):
            v1_candidate.check_context(dict(v1.context(), native_format=dict(v1.context()["native_format"],
                                                                               metadata=MIRROR_METADATA)), now)

    def test_runtime_mask_compares_a_fixture_format_with_metadata_exactly_and_refuses_hardware(self):
        fmt = CaptureFormat.from_mapping(self.FLAT)
        calibration = synthetic.fixture_calibration("synthetic:mirror-shaped", fmt, "stereo_sbs",
                                                    (Footprint(0, (700, 800, 900, 900), 16),
                                                     Footprint(1, (1660, 800, 1860, 900), 16)))
        self.assertEqual(calibration.context()["native_format"], fmt.record())
        mask = HudMask(calibration, source_kind="synthetic")
        mask.check_context("synthetic:mirror-shaped", CaptureFormat.from_mapping(dict(self.FLAT)), "stereo_sbs", HUD_CONFIG)
        mask.check_context("synthetic:mirror-shaped", CaptureFormat.from_mapping(fmt.record()), "stereo_sbs", HUD_CONFIG)
        for changed in (dict(self.FLAT, colorspace_code=1), {k: v for k, v in self.FLAT.items() if k not in MIRROR_METADATA}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "hud_capture_format_changed"):
                mask.check_context("synthetic:mirror-shaped", CaptureFormat.from_mapping(changed), "stereo_sbs", HUD_CONFIG)
        # HARDWARE_MAPPING still refuses every non-synthetic calibration, metadata or not.
        for record in (mirror_record(), candidate_record() | {"context": candidate_record()["context"] | {
                "hud_config_sha256": config_digest(HUD_CONFIG)}}):
            with self.subTest(schema=record["schema_version"]):
                hardware = MappingCandidate.from_mapping(record).calibration()
                with self.assertRaisesRegex(ValueError, "hud_calibration_unvalidated"):
                    HudMask(hardware, source_kind="synthetic")
                with patch.object(Calibration, "validated", property(lambda self: True)), \
                        self.assertRaisesRegex(ValueError, "hardware_mapping_not_approved"):
                    HudMask(hardware, source_kind="synthetic")
        # The synthetic fixture candidate is a version 2 record whose context is the calibration's.
        fixture = synthetic.fixture_candidate(calibration).as_dict()
        self.assertEqual(fixture["schema_version"], 2)
        self.assertEqual(fixture["context"], calibration.context())
        self.assertEqual(fixture["context"]["native_format"]["metadata"], MIRROR_METADATA)


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
        validate_context(context)  # The version 2 form, with the (empty) metadata object.
        self.assertEqual(context["native_format"], {"width": 96, "height": 64, "fourcc": "BGR3", "stride_bytes": 288,
                                                    "buffer_bytes": 18432, "minimum_buffer_bytes": 18432,
                                                    "metadata": {}})
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
        # A version 1 record pins six native-format fields and no V4L2 metadata; the calibration's context is
        # the version 2 form of the same record, with an empty metadata object.
        self.assertEqual(calibration.capture_format.metadata, ())
        self.assertEqual(calibration.context(),
                         dict(record["context"], native_format=dict(record["context"]["native_format"], metadata={})))
        self.assertEqual((calibration.observed_ns, calibration.expires_ns), (record["observed_ns"], record["expires_ns"]))
        self.assertEqual(calibration.provenance, "observed_unqualified")
        self.assertIs(calibration.validated, False)
        # A version 2 record carries the mirror's format metadata into the calibration and back out exactly.
        record = mirror_record()
        calibration = MappingCandidate.from_mapping(record).calibration()
        self.assertEqual(calibration.capture_format.metadata, tuple(sorted(MIRROR_METADATA.items())))
        self.assertEqual(calibration.context(), record["context"])
        self.assertIs(calibration.validated, False)
        with self.assertRaisesRegex(ValueError, "hud_calibration_unvalidated"):
            HudMask(calibration, source_kind="synthetic")
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
