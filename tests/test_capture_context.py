"""CaptureContext: one fail-closed capture identity, using generated frames only."""
import importlib.util
import time
import unittest

from framejacker.context import (FORMAT_CHANGED, FORMAT_METADATA_KEYS, SESSION_METADATA_KEYS, SOURCE_CHANGED,
                                 SOURCE_FIELDS, CaptureContext)
from framejacker.frame import Frame


def frame(number=1, *, source="fixture", pixel_format="TEST", width=4, height=2, stride=12, metadata=None):
    return Frame(b"", pixel_format, width, height, stride, source, number, time.monotonic_ns(),
                 metadata={} if metadata is None else metadata)


class Declared:
    kind, source_id, capture_layout = "synthetic", "synthetic:context", "mono"


class CaptureContextTests(unittest.TestCase):
    def test_every_declared_source_field_is_pinned_before_the_first_frame(self):
        for name in SOURCE_FIELDS:
            with self.subTest(name=name):
                source = Declared()
                context = CaptureContext(source)
                setattr(source, name, "changed")
                with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
                    context.admit(frame(source="synthetic:context"))

    def test_declared_source_id_binds_frames_and_undeclared_source_pins_first_frame(self):
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            CaptureContext(Declared()).admit(frame(source="synthetic:other"))
        context = CaptureContext(object())
        context.admit(frame(1, source="replay:a"))
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            context.admit(frame(2, source="replay:b"))

    def test_layout_and_format_metadata_change_the_native_format(self):
        changes = [dict(pixel_format="OTHER"), dict(width=5), dict(height=3), dict(stride=16)]
        changes += [dict(metadata={key: "changed"}) for key in sorted(FORMAT_METADATA_KEYS)]
        for change in changes:
            with self.subTest(change=change):
                context = CaptureContext(object())
                context.admit(frame(1, metadata={key: "original" for key in FORMAT_METADATA_KEYS}))
                options = dict(change)
                metadata = {key: "original" for key in FORMAT_METADATA_KEYS}
                metadata.update(options.pop("metadata", {}))
                with self.assertRaisesRegex(ValueError, FORMAT_CHANGED):
                    context.admit(frame(2, metadata=metadata, **options))

    def test_session_metadata_change_is_a_source_or_session_change(self):
        for key in sorted(SESSION_METADATA_KEYS):
            with self.subTest(key=key):
                context = CaptureContext(object())
                context.admit(frame(1, metadata={key: "original"}))
                with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
                    context.admit(frame(2, metadata={key: "changed"}))
        self.assertFalse(SESSION_METADATA_KEYS & FORMAT_METADATA_KEYS)

    def test_per_frame_telemetry_is_not_context(self):
        context = CaptureContext(object())
        for number in (1, 2, 3):
            context.admit(frame(number, metadata={"schedule_lateness_ms": number}))
        self.assertIsNone(context.failure)

    def test_pinned_metadata_is_owned_so_in_place_mutation_is_detected(self):
        metadata = {"crop_xyxy": [0, 0, 4, 2]}
        observed = frame(metadata=metadata)
        context = CaptureContext(object())
        context.admit(observed)
        metadata["crop_xyxy"][2] = 3
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            context.admit(observed)

    def test_first_failure_latches_and_rejects_an_unchanged_in_flight_frame(self):
        context = CaptureContext(object())
        original = frame(1)
        context.admit(original)
        with self.assertRaisesRegex(ValueError, FORMAT_CHANGED):
            context.admit(frame(2, width=8))
        self.assertEqual(context.failure, FORMAT_CHANGED)
        with self.assertRaisesRegex(ValueError, FORMAT_CHANGED):
            context.admit(original)

    def test_unsupported_frame_type_fails_closed(self):
        context = CaptureContext(object())
        with self.assertRaisesRegex(ValueError, "capture_frame_type_unsupported"):
            context.admit({"pixels": b""})
        with self.assertRaisesRegex(ValueError, "capture_frame_type_unsupported"):
            context.admit(frame())


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for owned-frame tests")
class ValidatedFrameAndHudBindingTests(unittest.TestCase):
    """Validated owned frames (``Frame.owned_bgr``) and the HUD binding."""

    def owned(self, sequence=1, width=96, height=64, source="synthetic:generated"):
        import numpy as np
        return Frame.owned_bgr(np.zeros((height, width, 3), np.uint8), sequence, time.monotonic_ns(), source)

    def calibration(self):
        from framejacker.runtime.synthetic import synthetic_hud_calibration
        return synthetic_hud_calibration()

    def source(self, **changes):
        from framejacker.runtime.synthetic import SyntheticSession
        attributes = dict(kind="synthetic", source_id="synthetic:generated", capture_layout="mono")
        attributes.update(changes)
        return type("Source", (SyntheticSession,), attributes)()

    def test_owned_native_descriptor_change_is_a_format_change(self):
        context = CaptureContext(self.source())
        context.admit(self.owned(1))
        with self.assertRaisesRegex(ValueError, FORMAT_CHANGED):
            context.admit(self.owned(2, width=48))

    def test_hud_binding_checks_calibration_at_creation_and_every_frame(self):
        from framejacker.hud import HudMask
        mask = HudMask(self.calibration(), source_kind="synthetic")
        for changes, reason in ((dict(source_id="synthetic:other"), "hud_source_changed"),
                                (dict(capture_layout="stereo_sbs"), "hud_capture_layout_changed")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                CaptureContext(self.source(**changes), mask=mask)
        context = CaptureContext(self.source(), mask=mask)
        context.admit(self.owned(1))
        # A first frame that differs from the calibration cannot pin a new format.
        fresh = CaptureContext(self.source(), mask=mask)
        with self.assertRaisesRegex(ValueError, "hud_capture_format_changed"):
            fresh.admit(self.owned(1, width=48))
        self.assertEqual(fresh.failure, "hud_capture_format_changed")


if __name__ == "__main__":
    unittest.main()
