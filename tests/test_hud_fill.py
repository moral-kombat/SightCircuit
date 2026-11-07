"""The native HUD mask's fill: built only from the surround, never from the footprint.

Generated frames and synthetic fixture calibrations only. The real-detector
tests are runner-CPU cloud results on generated frames, never Frame evidence,
and open no gate.
"""
import time
import unittest
from unittest.mock import patch

try:
    import cv2
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for HUD fill tests") from error
import numpy as np

from sightcircuit import hud
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, create_detector
from sightcircuit.frame import Frame
from sightcircuit.hud.render import draw_tile
from sightcircuit.overlay import DEFAULT_BRIDGE
from sightcircuit.runtime import synthetic
from tests.asset_gate import model_assets, requires_assets
from tests.test_hud_mask import calibration, prepare

ODD_GRAY_LEVELS = range(1, 254, 2)  # E76's sweep: every odd uniform level the synthetic source emits.
RING_PX = 8  # The documented fill ring (docs/hud-qualification.md), written out so a changed constant fails.
# Both eyes' footprint plus margin touch the seam between the eyes, one from each side, so
# the fill ring reaches the other eye unless it is clipped to the region's own eye.
SEAM_FOOTPRINTS = {"stereo_sbs": (hud.Footprint(0, (552, 650, 632, 700), 8), hud.Footprint(1, (648, 650, 728, 700), 8)),
                   "stereo_tb": (hud.Footprint(0, (1180, 302, 1260, 352), 8),
                                 hud.Footprint(1, (1180, 368, 1260, 418), 8))}
STEREO = ("stereo_sbs", "stereo_tb", "stereo_sbs_seam", "stereo_tb_seam")


def calibrations():
    """Generated captures whose masks the leak tests probe.

    The 96x64 fixture, mono, both stereo layouts with the helper geometry (12 px
    from the seam) and with regions touching the seam, and 1080p.
    """
    return {"fixture_96x64": synthetic.synthetic_hud_calibration(), "mono_1280x720": calibration(),
            "stereo_sbs": calibration(layout="stereo_sbs"), "stereo_tb": calibration(layout="stereo_tb"),
            "stereo_sbs_seam": calibration(layout="stereo_sbs", footprints=SEAM_FOOTPRINTS["stereo_sbs"]),
            "stereo_tb_seam": calibration(layout="stereo_tb", footprints=SEAM_FOOTPRINTS["stereo_tb"]),
            "mono_1920x1080": calibration(1920, 1080)}


def scene(height, width, seed=0):
    """Deterministic textured BGR content: smooth structure plus per-pixel noise."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (max(2, height // 16), max(2, width // 16), 3), dtype=np.uint8)
    smooth = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC).astype(np.int16)
    return np.clip(smooth + rng.integers(-24, 25, (height, width, 3)), 0, 255).astype(np.uint8)


def hud_text(height, width):
    """HUD-like content for a region: the real tile with rows, scaled, plus bright text."""
    rows = (hud.HudRow("chair", .93, (1, 1)), hud.HudRow("person", .81, (-1, 0)), hud.HudRow("tv", .7, (0, 0)))
    tile = cv2.resize(np.array(draw_tile(rows, hud.HUD_CONFIG)), (width, height), interpolation=cv2.INTER_AREA)
    cv2.putText(tile, "tvmonitor 0.81", (0, max(8, height // 2)), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
    return tile


def contaminations(image, regions, seed=1):
    """Copies of ``image`` with different content inside every masked region and nothing changed outside."""
    rng = np.random.default_rng(seed)
    kinds = {"random": lambda h, w: rng.integers(0, 256, (h, w, 3), dtype=np.uint8),
             "hud_text": hud_text, "white": lambda h, w: np.full((h, w, 3), 255, np.uint8),
             "black": lambda h, w: np.zeros((h, w, 3), np.uint8),
             "inverted": None}
    for kind, content in kinds.items():
        changed = image.copy()
        for x0, y0, x1, y1 in regions:
            changed[y0:y1, x0:x1] = 255 - image[y0:y1, x0:x1] if content is None else content(y1 - y0, x1 - x0)
        yield kind, changed


def native_and_input(mask, image):
    """``(native image the inference resize received, detector input)`` of ``HudMask.prepare``.

    Without a resize (native side at most 960) the native image is the input.
    """
    seen = []
    resize = cv2.resize

    def recording(source, *args, **kwargs):
        if source.shape == image.shape:
            seen.append(np.array(source, copy=True))
        return resize(source, *args, **kwargs)

    with patch.object(cv2, "resize", side_effect=recording):
        detector_input = prepare(mask, image)
    return (seen[-1] if seen else np.array(detector_input)), detector_input


class RuntimeFillLeakTests(unittest.TestCase):
    """Changing any pixel inside footprint plus margin leaves the detector input byte-identical."""

    def assert_same(self, mask, clean, changed, label):
        native, final = native_and_input(mask, clean)
        changed_native, changed_final = native_and_input(mask, changed)
        np.testing.assert_array_equal(changed_native, native, err_msg=f"{label}: native input changed")
        np.testing.assert_array_equal(changed_final, final, err_msg=f"{label}: detector input changed")

    def test_region_content_never_reaches_the_native_or_resized_input(self):
        for name, cal in calibrations().items():
            mask = hud.HudMask(cal, source_kind="synthetic")
            fmt = cal.capture_format
            for seed in (0, 5):
                clean = scene(fmt.height, fmt.width, seed)
                for kind, changed in contaminations(clean, mask.regions, seed + 1):
                    with self.subTest(calibration=name, seed=seed, content=kind):
                        self.assert_same(mask, clean, changed, f"{name}/{kind}")

    def test_every_single_pixel_of_the_fixture_region_is_ignored(self):
        """Exhaustive on the 96x64 fixture: each pixel and channel of its masked region, one at a time."""
        mask = hud.HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        clean = scene(64, 96, 3)
        expected = prepare(mask, clean)
        (x0, y0, x1, y1), = mask.regions
        for y in range(y0, y1):
            for x in range(x0, x1):
                for channel in range(3):
                    changed = clean.copy()
                    changed[y, x, channel] = 255 - clean[y, x, channel]
                    actual = prepare(mask, changed)
                    if not np.array_equal(actual, expected):
                        self.fail(f"pixel ({x}, {y}) channel {channel} of the masked region reached the input")

    def test_boundary_pixels_of_every_region_are_ignored_at_1080p_and_in_stereo(self):
        """The region's own edge rows and columns are the pixels a surround fill could most easily read."""
        for name in ("mono_1920x1080", *STEREO):
            cal = calibrations()[name]
            mask = hud.HudMask(cal, source_kind="synthetic")
            clean = scene(cal.capture_format.height, cal.capture_format.width, 9)
            native, final = native_and_input(mask, clean)
            for x0, y0, x1, y1 in mask.regions:
                edge = ([(x, y) for x in range(x0, x1) for y in (y0, y1 - 1)] +
                        [(x, y) for y in range(y0, y1) for x in (x0, x1 - 1)])
                changed = clean.copy()
                for x, y in edge:
                    changed[y, x] = 255 - clean[y, x]
                with self.subTest(calibration=name, region=(x0, y0, x1, y1)):
                    changed_native, changed_final = native_and_input(mask, changed)
                    np.testing.assert_array_equal(changed_native, native)
                    np.testing.assert_array_equal(changed_final, final)

    def test_one_eye_fill_never_reads_the_other_eye(self):
        """Changing everything in the other eye leaves this eye's native and resized input unchanged.

        The seam calibrations put each region against the seam, where the ring
        would cross into the other eye without the clip to the region's own eye.
        At 1280x720 the 4:3 area resize maps the seam onto an output pixel edge,
        so the resized eye depends on its own native eye alone.
        """
        for name in STEREO:
            cal = calibrations()[name]
            mask = hud.HudMask(cal, source_kind="synthetic")
            eyes = hud.viewports(cal.capture_format, cal.capture_layout)
            height, width = mask.inference_shape()
            sx, sy = width / 1280, height / 720
            clean = scene(720, 1280, 11)
            native, final = native_and_input(mask, clean)
            for index, (ex0, ey0, ex1, ey1) in enumerate(eyes):
                other = eyes[1 - index]
                changed = clean.copy()
                changed[other[1]:other[3], other[0]:other[2]] = 255 - clean[other[1]:other[3], other[0]:other[2]]
                changed_native, changed_final = native_and_input(mask, changed)
                rx0, ry0, rx1, ry1 = round(ex0 * sx), round(ey0 * sy), round(ex1 * sx), round(ey1 * sy)
                with self.subTest(calibration=name, eye=index):
                    np.testing.assert_array_equal(changed_native[ey0:ey1, ex0:ex1], native[ey0:ey1, ex0:ex1])
                    np.testing.assert_array_equal(changed_final[ry0:ry1, rx0:rx1], final[ry0:ry1, rx0:rx1])

    def test_seam_regions_touch_the_other_eye_so_the_eye_clip_is_exercised(self):
        """The seam geometry is valid and reaches the seam; the helper geometry stops short of the ring."""
        for layout, footprints in SEAM_FOOTPRINTS.items():
            mask = hud.HudMask(calibrations()[layout + "_seam"], source_kind="synthetic")
            eyes = hud.viewports(mask.calibration.capture_format, layout)
            seam = eyes[1][0] if layout == "stereo_sbs" else eyes[1][1]
            axis = 0 if layout == "stereo_sbs" else 1
            with self.subTest(layout=layout):
                self.assertEqual((mask.regions[0][axis + 2], mask.regions[1][axis]), (seam, seam))
                helper = hud.HudMask(calibrations()[layout], source_kind="synthetic").regions[0]
                self.assertLess(helper[axis + 2] + RING_PX, seam)

    def test_fill_is_deterministic_and_independent_of_the_input_layout(self):
        cal = calibration(1920, 1080, padding=16)
        mask = hud.HudMask(cal, source_kind="synthetic")
        clean = scene(1080, 1920, 13)
        storage = np.full((1080, cal.capture_format.stride_bytes), 211, np.uint8)
        padded = np.ndarray((1080, 1920, 3), np.uint8, buffer=storage, strides=(cal.capture_format.stride_bytes, 3, 1))
        padded[:] = clean
        first = prepare(mask, clean)
        np.testing.assert_array_equal(prepare(mask, clean), first)
        np.testing.assert_array_equal(prepare(mask, padded), first)


class RuntimeFillNeutralityTests(unittest.TestCase):
    """The masked region carries no structure of its own: it continues the surround."""

    def test_uniform_frames_reach_the_detector_unchanged(self):
        """A uniform frame has nothing to fill in: the masked input equals the unmasked input exactly."""
        for name, cal in calibrations().items():
            mask = hud.HudMask(cal, source_kind="synthetic")
            fmt = cal.capture_format
            height, width = mask.inference_shape()
            for level in (0, 1, 37, 128, 253, 254):
                frame = np.full((fmt.height, fmt.width, 3), level, np.uint8)
                frame[..., 1] = 255 - level  # Channels filled independently.
                unmasked = frame if (height, width) == frame.shape[:2] else cv2.resize(
                    frame, (width, height), interpolation=cv2.INTER_AREA)
                with self.subTest(calibration=name, level=level):
                    np.testing.assert_array_equal(prepare(mask, frame), unmasked)

    def test_filled_values_stay_within_the_surrounding_range(self):
        for name, cal in calibrations().items():
            mask = hud.HudMask(cal, source_kind="synthetic")
            fmt = cal.capture_format
            eyes = hud.viewports(fmt, cal.capture_layout)
            frame = scene(fmt.height, fmt.width, 17)
            native, _ = native_and_input(mask, frame)
            for x0, y0, x1, y1 in mask.regions:
                eye = next(eye for eye in eyes if eye[0] <= x0 and x1 <= eye[2] and eye[1] <= y0 and y1 <= eye[3])
                outside = np.zeros((fmt.height, fmt.width), bool)
                outside[eye[1]:eye[3], eye[0]:eye[2]] = True
                for rx0, ry0, rx1, ry1 in mask.regions:
                    outside[ry0:ry1, rx0:rx1] = False
                low, high = frame[outside].min(axis=0), frame[outside].max(axis=0)
                filled = native[y0:y1, x0:x1].reshape(-1, 3)
                with self.subTest(calibration=name, region=(x0, y0, x1, y1)):
                    self.assertTrue(((filled >= low) & (filled <= high)).all())


class RuntimeFillOracleTests(unittest.TestCase):
    """The runtime fill is exactly the documented rule, computed here without ``hud.mask.surround``."""

    @staticmethod
    def expected_native(frame, regions, eyes):
        """Each region holds the rounded per-channel mean of the pixels within 8 px of it, in its eye, off every region.

        Outside the regions the zeroed copy equals the frame, so the ring is read
        from the frame itself.
        """
        height, width = frame.shape[:2]
        expected = frame.copy()
        for x0, y0, x1, y1 in regions:
            ex0, ey0, ex1, ey1 = next(eye for eye in eyes
                                      if eye[0] <= x0 and x1 <= eye[2] and eye[1] <= y0 and y1 <= eye[3])
            ring = np.zeros((height, width), bool)
            ring[max(ey0, y0 - RING_PX):min(ey1, y1 + RING_PX), max(ex0, x0 - RING_PX):min(ex1, x1 + RING_PX)] = True
            for rx0, ry0, rx1, ry1 in regions:
                ring[ry0:ry1, rx0:rx1] = False
            pixels = frame[ring].astype(np.int64)
            expected[y0:y1, x0:x1] = np.floor(pixels.sum(axis=0) / len(pixels) + .5).astype(np.uint8)
        return expected

    def test_native_input_is_the_ring_mean_of_the_surround(self):
        self.assertEqual(hud.mask.FILL_RING_PX, RING_PX)
        for name, cal in calibrations().items():
            mask = hud.HudMask(cal, source_kind="synthetic")
            fmt = cal.capture_format
            eyes = hud.viewports(fmt, cal.capture_layout)
            for seed in (0, 1, 2, 3):
                frame = scene(fmt.height, fmt.width, 40 + seed)
                native, _ = native_and_input(mask, frame)
                with self.subTest(calibration=name, seed=seed):
                    np.testing.assert_array_equal(native, self.expected_native(frame, mask.regions, eyes))

    def test_a_region_beside_the_seam_takes_its_color_from_its_own_eye(self):
        """Each eye uniform in its own color: a ring that crossed the seam would mix in the other eye's."""
        for layout in ("stereo_sbs", "stereo_tb"):
            mask = hud.HudMask(calibrations()[layout + "_seam"], source_kind="synthetic")
            eyes = hud.viewports(mask.calibration.capture_format, layout)
            frame = np.empty((720, 1280, 3), np.uint8)
            colors = ((10, 200, 30), (240, 15, 120))
            for (ex0, ey0, ex1, ey1), color in zip(eyes, colors):
                frame[ey0:ey1, ex0:ex1] = color
            native, _ = native_and_input(mask, frame)
            for (x0, y0, x1, y1), color in zip(mask.regions, colors):
                with self.subTest(layout=layout, region=(x0, y0, x1, y1)):
                    self.assertTrue((native[y0:y1, x0:x1] == color).all())


class RuntimeFillCounterTests(unittest.TestCase):
    """``unmasked_footprint_px`` still proves that no footprint pixel survives into the fill."""

    def test_count_is_zero_for_any_region_content(self):
        mask = hud.HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        context = dict(source_id=mask.calibration.source_id, capture_format=mask.calibration.capture_format,
                       capture_layout=mask.calibration.capture_layout)
        for kind, frame in contaminations(scene(64, 96, 19), mask.regions):
            with self.subTest(content=kind):
                image, unmasked = mask.prepare_counted(frame, **context)
                self.assertEqual(unmasked, 0)
                self.assertFalse(image.flags.writeable)

    def test_a_zeroing_defect_is_counted_before_the_fill_and_prepare_refuses_it(self):
        mask = hud.HudMask(synthetic.synthetic_hud_calibration(), source_kind="synthetic")
        context = dict(source_id=mask.calibration.source_id, capture_format=mask.calibration.capture_format,
                       capture_layout=mask.calibration.capture_layout)
        frame = np.full((64, 96, 3), 90, np.uint8)
        with patch.object(hud.HudMask, "regions", property(lambda mask: ((60, 35, 96, 64),))):
            _, unmasked = mask.prepare_counted(frame, **context)
            self.assertEqual(unmasked, 37 + 29)  # One row and one column the zeroing missed.
            with self.assertRaisesRegex(ValueError, "hud_mask_zeroing_failed"):
                mask.prepare(frame, **context)


@requires_assets(*model_assets())
class RealDetectorStrictRuleTests(unittest.TestCase):
    """E76's gray sweep with the real detectors: no box may touch the mask (runner CPU, generated frames).

    The strict rule is unchanged: any box touching a masked region, or not
    inside exactly one eye, fails a validation run.
    """

    def test_no_detector_boxes_the_mask_on_any_odd_gray_level(self):
        source = synthetic.SyntheticSource()
        for name in (DEFAULT_DETECTOR, *[name for name in DETECTORS if name != DEFAULT_DETECTOR]):
            stage = hud.HudStage(source, synthetic.synthetic_hud_calibration())
            stage.start()
            detector = create_detector(name)
            touching = []
            for level in ODD_GRAY_LEVELS:
                frame = Frame.owned_bgr(np.full((64, 96, 3), level, np.uint8), level, time.monotonic_ns(),
                                        source.source_id)
                image, unmasked = stage.prepare(frame)
                self.assertEqual(unmasked, 0)
                result = detector.detect(image, .5)
                _, output = stage.render(result, image.shape[:2], .5)
                if output.checks.mask_overlap_rejections or output.checks.eye_crossing_rejections:
                    touching.append((level, [(item.label, round(item.confidence, 2)) for item in result.detections]))
            with self.subTest(detector=name):
                self.assertEqual(touching, [], f"{len(touching)} of {len(ODD_GRAY_LEVELS)} levels")

    def assert_passes(self, report):
        self.assertEqual((report["status"], report["exit_code"], report["detector"]),
                         ("completed", 0, DEFAULT_DETECTOR), report)
        self.assertTrue(report["cleanup_ok"], report)
        g5 = report["g5_software"]
        self.assertEqual((g5["status"], g5["failure_reason"]), ("checked_unqualified", None))
        self.assertGreater(g5["frames_checked"], 0)
        self.assertEqual((g5["native_mask_zeroing_failures"], g5["hud_footprint_detections"],
                          g5["mask_and_eye_boundary_rejections"]), (0, 0, 0))
        self.assertEqual(report["pipeline"]["hud_checks"]["unmasked_footprint_px"], 0)
        self.assertEqual(report["validation_ledger"]["status"], "telemetry_checked_unqualified")
        self.assertIs(report["vr_runtime_initialized"], False)

    def test_synthetic_hud_check_with_the_default_detector_passes_the_strict_rule(self):
        """``live_pipeline.py --synthetic-check --synthetic-hud`` with the default model, through its supervisor."""
        from scripts import live_pipeline
        self.assert_passes(live_pipeline.supervise_synthetic(
            {"seconds": 3, "synthetic_hud": True, "detector": DEFAULT_DETECTOR}))

    @unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Native bridge required: bash scripts/build_overlay_bridge.sh")
    def test_g1_rerun_with_the_default_detector_and_native_transport_passes(self):
        from scripts import live_pipeline
        self.assert_passes(live_pipeline.supervise_synthetic(
            {"seconds": 3, "synthetic_hud": True, "validate_overlay": True, "detector": DEFAULT_DETECTOR}))


if __name__ == "__main__":
    unittest.main()
