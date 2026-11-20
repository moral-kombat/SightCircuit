"""The region of view (work-order item L14): the crop before the resize, the quarter turns, the boxes mapped back, the scan.

Every image here is drawn by the test (a dark frame with one bright
rectangle and a corner marker); the stand-in detector finds the bright
rectangle's bounding box in whatever image it is given, so a mapped box can
be compared with where the rectangle was drawn in the native frame. The
argument, schedule and tally tests are standard library only; the pixel tests
need NumPy and OpenCV and skip without them (the replay environment runs them
in CI). No camera, no private image, no file.
"""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import marks, region  # noqa: E402
from sightcircuit.frame import DETECTOR_PIXELS, Detection, DetectionResult, Frame  # noqa: E402

try:
    import cv2  # noqa: F401
    import numpy as np
    CONVERSION = True
except ImportError:
    CONVERSION = False

NATIVE = 2464  # The Frame's camera image is 2464 pixels square (E100).
SPACE = 960
OBJECT = (1300, 1000, 1500, 1400)  # A 200 by 400 native rectangle a little right of the centre.
CONTAINING = ("whole", "centre_half", "upper_right", "lower_right")  # The scan windows that hold all of ``OBJECT``.


def drawn_frame(width=NATIVE, height=NATIVE, box=OBJECT, pixel_format="BGR3", padding=0):
    """A dark frame with one bright rectangle at ``box`` (native pixels) and a red-channel marker in its top left."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[box[1]:box[3], box[0]:box[2]] = 255
    image[box[1]:box[1] + 8, box[0]:box[0] + 8] = (0, 0, 255) if pixel_format != "RGB3" else (255, 0, 0)
    stride = width * 3 + padding
    rows = np.zeros((height, stride), dtype=np.uint8)
    rows[:, :width * 3] = image.reshape(height, width * 3)
    return Frame(rows.tobytes(), pixel_format, width, height, stride, "drawn", 1, 0)


def bright_box(image):
    """The stand-in detector: the bounding box of the bright rectangle in ``image``, x2 and y2 exclusive."""
    ys, xs = np.nonzero(image[:, :, 2] > 127)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def expected(box=OBJECT, native=NATIVE, space=SPACE):
    return tuple(value * space / native for value in box)


class RegionArgumentTests(unittest.TestCase):
    def test_a_region_is_four_ordered_fractions_and_a_quarter_turn(self):
        self.assertEqual(region.Region().fractions, region.WHOLE)
        self.assertTrue(region.Region().whole)
        self.assertFalse(region.Region(rotation=90).whole)
        self.assertFalse(region.Region(0.25, 0.25, 0.75, 0.75).whole)
        self.assertEqual(region.parse_region("0.25,0.25,0.75,0.75"), (0.25, 0.25, 0.75, 0.75))
        self.assertEqual(region.Region(0, 0, 1, 1, 180).report(), {"region": [0.0, 0.0, 1.0, 1.0], "rotation": 180})
        self.assertEqual(region.ROTATIONS, (0, 90, 180, 270))
        for fractions in ((0.5, 0.5, 0.4, 0.9), (0.5, 0.5, 0.9, 0.4), (0.2, 0.2, 0.2, 0.9), (-0.1, 0, 1, 1), (0, 0, 1, 1.01),
                          (0, 0, 0.04, 1), (0, 0, 1, 0.04), (float("nan"), 0, 1, 1), (0, 0, float("inf"), 1),
                          ("a", 0, 1, 1), (None, 0, 1, 1)):
            with self.subTest(fractions=fractions), self.assertRaises(ValueError):
                region.Region(*fractions)
        for rotation in (45, -90, 360, 90.0, "90", None, True):
            with self.subTest(rotation=rotation), self.assertRaises(ValueError):
                region.Region(rotation=rotation)
        for text in ("", "0,0,1", "0,0,1,1,1", "0 0 1 1", "a,b,c,d", "0.5,0.5,0.4,0.9", "0,0,1,2", None, 5,
                     "0.25;0.25;0.75;0.75", "centre"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                region.parse_region(text)
        with self.assertRaises(ValueError):
            region.Region(name="somewhere")  # A name is one of the scan's closed list, never free text.

    def test_pixels_round_to_the_native_grid_and_refuse_a_frame_too_small(self):
        self.assertEqual(region.Region(0.25, 0.25, 0.75, 0.75).pixels(NATIVE, NATIVE), (616, 616, 1848, 1848))
        self.assertEqual(region.Region(0.4, 0.4, 1, 1).pixels(NATIVE, NATIVE), (986, 986, 2464, 2464))
        self.assertEqual(region.Region().pixels(1920, 1080), (0, 0, 1920, 1080))
        self.assertEqual(region.Region(0.25, 0.25, 0.75, 0.75).pixels(64, 48), (16, 12, 48, 36))
        with self.assertRaises(ValueError):
            region.Region(0, 0, 0.05, 0.05).pixels(64, 48)
        self.assertEqual(region.box_space(NATIVE, NATIVE), (SPACE, SPACE))
        self.assertEqual(region.box_space(1920, 1080), (960, 540))
        self.assertEqual(region.box_space(64, 48), (64, 48))  # Never enlarged.

    def test_the_module_reads_no_calibration_and_writes_nothing(self):
        source = (ROOT / "sightcircuit/camera/region.py").read_text()
        tree = ast.parse(source)
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("imwrite", "imshow", "imread", "imencode", "save", "open", "write", "tofile", "print",
                          "intrinsics", "extrinsics", "distortion", "calibration"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)
        imported = sorted((node.module or "", node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        self.assertEqual(imported, [("convert", 2), ("dataclasses", 0), ("frame", 2)])
        self.assertNotEqual(region.FRAME_PIXELS, DETECTOR_PIXELS)  # A cropped input is not the box space: said so.
        self.assertIn("no calibration value is read", region.ORIENTATION_STATEMENT)


class MappingTests(unittest.TestCase):
    """The inverse of the crop, the resize and the turn, checked on corners by arithmetic alone."""

    def mapping(self, rotation, crop=(616, 616, 1848, 1848), resized=(960, 960)):
        return region.Mapping((NATIVE, NATIVE), crop, resized, rotation, (SPACE, SPACE))

    def test_each_turn_takes_the_crops_corners_back_to_the_same_rectangle(self):
        for rotation in region.ROTATIONS:
            mapping = self.mapping(rotation)
            with self.subTest(rotation=rotation):
                self.assertEqual(mapping.input_size, (960, 960))
                self.assertEqual(mapping.to_frame((0, 0, 960, 960)), (240, 240, 720, 720))
        # A box in the input's top-left quarter comes from a different corner of the crop at each turn.
        quarter = {0: (240, 240, 480, 480), 90: (240, 480, 480, 720), 180: (480, 480, 720, 720), 270: (480, 240, 720, 480)}
        for rotation, box in quarter.items():
            with self.subTest(rotation=rotation):
                self.assertEqual(self.mapping(rotation).to_frame((0, 0, 480, 480)), box)

    def test_a_region_that_is_not_square_swaps_its_sides_when_turned(self):
        mapping = region.Mapping((1920, 1080), (960, 0, 1920, 1080), (853, 960), 90, (960, 540))
        self.assertEqual(mapping.input_size, (960, 853))
        self.assertEqual(region.Mapping((1920, 1080), (960, 0, 1920, 1080), (853, 960), 180, (960, 540)).input_size,
                         (853, 960))
        self.assertEqual(mapping.to_frame((0, 0, 960, 853)), (480, 0, 960, 540))  # The whole input is the whole crop.

    def test_boxes_are_clamped_to_the_frame_and_never_empty(self):
        edge = region.Mapping((NATIVE, NATIVE), (986, 986, 2464, 2464), (960, 960), 0, (SPACE, SPACE))
        self.assertEqual(edge.to_frame((900, 900, 1200, 1200)), (924, 924, 960, 960))  # Past the frame's edge: clamped.
        self.assertEqual(edge.to_frame((-50, -50, 10, 10)), (354, 354, 390, 390))  # Before the crop: still in the frame.
        tiny = edge.to_frame((100, 100, 100, 100))
        self.assertEqual((tiny[2] - tiny[0], tiny[3] - tiny[1]), (1, 1))
        corner = edge.to_frame((960, 960, 960, 960))
        self.assertEqual(corner, (959, 959, 960, 960))
        for box in (edge.to_frame((0, 0, 960, 960)), tiny, corner):
            self.assertTrue(0 <= box[0] < box[2] <= SPACE and 0 <= box[1] < box[3] <= SPACE)
            self.assertTrue(all(type(value) is int for value in box))

    def test_a_result_keeps_labels_scores_and_timing(self):
        result = DetectionResult((Detection("bottle", 0.8, (0, 0, 480, 480)), Detection("chair", 0.6, (480, 480, 960, 960))),
                                 12.5)
        mapped = self.mapping(180).result(result)
        self.assertEqual(mapped.inference_ms, 12.5)
        self.assertEqual([(item.label, item.confidence, item.box_xyxy) for item in mapped.detections],
                         [("bottle", 0.8, (480, 480, 720, 720)), ("chair", 0.6, (240, 240, 480, 480))])


@unittest.skipUnless(CONVERSION, "NumPy and OpenCV are needed for the pixel tests")
class PrepareTests(unittest.TestCase):
    def assert_box_near(self, box, wanted, tolerance=2.0):
        for value, target in zip(box, wanted):
            self.assertLessEqual(abs(value - target), tolerance, (box, wanted))

    def test_boxes_come_back_to_where_the_object_is_for_every_scan_window_and_rotation(self):
        frame = drawn_frame()
        for name, fractions in region.SCAN_REGIONS:
            if name not in CONTAINING:
                continue
            for rotation in region.ROTATIONS:
                with self.subTest(region=name, rotation=rotation):
                    image, mapping = region.prepare(frame, region.Region(*fractions, rotation=rotation, name=name))
                    self.assertLessEqual(max(image.shape[:2]), SPACE)
                    self.assertEqual((image.shape[1], image.shape[0]), mapping.input_size)
                    self.assertEqual(mapping.space, (SPACE, SPACE))
                    self.assert_box_near(mapping.to_frame(bright_box(image)), expected())

    def test_the_turn_is_clockwise_and_really_applied_to_the_pixels(self):
        frame = drawn_frame()
        look = region.Region(0.25, 0.25, 0.75, 0.75)
        upright, _ = region.prepare(frame, look)
        x0, y0, x1, y1 = bright_box(upright)
        self.assertGreater(y1 - y0, x1 - x0)  # Taller than wide, as drawn.

        def marker(image):  # The red marker was drawn in the rectangle's top-left corner.
            ys, xs = np.nonzero((image[:, :, 2] > 127) & (image[:, :, 0] < 128))
            box = bright_box(image)
            return ("left" if xs.mean() < (box[0] + box[2]) / 2 else "right",
                    "top" if ys.mean() < (box[1] + box[3]) / 2 else "bottom")
        seen = {}
        for rotation in region.ROTATIONS:
            image, _mapping = region.prepare(frame, region.Region(*look.fractions, rotation=rotation))
            box = bright_box(image)
            seen[rotation] = (marker(image), (box[3] - box[1]) > (box[2] - box[0]))
        self.assertEqual(seen, {0: (("left", "top"), True), 90: (("right", "top"), False),
                                180: (("right", "bottom"), True), 270: (("left", "bottom"), False)})

    def test_a_region_at_the_frames_edge(self):
        box = (2300, 2200, 2464, 2464)  # Touching the right and bottom edges of the frame.
        frame = drawn_frame(box=box)
        for fractions in ((0.4, 0.4, 1.0, 1.0), (0.9, 0.85, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)):
            for rotation in region.ROTATIONS:
                with self.subTest(fractions=fractions, rotation=rotation):
                    image, mapping = region.prepare(frame, region.Region(*fractions, rotation=rotation))
                    mapped = mapping.to_frame(bright_box(image))
                    self.assert_box_near(mapped, expected(box))
                    self.assertTrue(0 <= mapped[0] < mapped[2] <= SPACE and 0 <= mapped[1] < mapped[3] <= SPACE)
        top_left = (0, 0, 120, 60)
        image, mapping = region.prepare(drawn_frame(box=top_left), region.Region(0.0, 0.0, 0.6, 0.6, 270))
        self.assert_box_near(mapping.to_frame(bright_box(image)), expected(top_left))

    def test_only_the_region_is_taken_and_it_is_never_enlarged(self):
        frame = drawn_frame(padding=8)
        before = bytes(frame.pixels)
        image, mapping = region.prepare(frame, region.Region(0.25, 0.25, 0.75, 0.75))
        self.assertEqual((image.shape, mapping.crop, mapping.resized), ((960, 960, 3), (616, 616, 1848, 1848), (960, 960)))
        third, mapping = region.prepare(frame, region.Region(1 / 3, 1 / 3, 2 / 3, 2 / 3))
        self.assertEqual(third.shape, (822, 822, 3))  # 821.3 native pixels: below 960, so not resized at all.
        self.assertEqual(mapping.resized, (822, 822))
        self.assertTrue(image.flags.writeable and image.flags.c_contiguous and image.flags.owndata)
        self.assertTrue(third.flags.writeable and third.flags.c_contiguous and third.flags.owndata)
        third[:] = 7  # The detector may write to its input: the frame's buffer is not touched.
        self.assertEqual(bytes(frame.pixels), before)
        # An unresized, unturned crop is exactly the frame's pixels there.
        again, _ = region.prepare(frame, region.Region(1 / 3, 1 / 3, 2 / 3, 2 / 3))
        native = np.frombuffer(before, dtype=np.uint8).reshape(NATIVE, NATIVE * 3 + 8)[:, :NATIVE * 3].reshape(
            NATIVE, NATIVE, 3)
        self.assertTrue(np.array_equal(again, native[821:1643, 821:1643]))

    def test_the_whole_frame_unturned_is_the_ordinary_conversion(self):
        from sightcircuit.convert import to_bgr
        for frame in (drawn_frame(), drawn_frame(1920, 1080, (900, 400, 1000, 700)), drawn_frame(64, 48, (10, 10, 30, 40))):
            image, mapping = region.prepare(frame, region.Region())
            plain = to_bgr(frame)
            with self.subTest(size=(frame.width, frame.height)):
                self.assertTrue(np.array_equal(image, plain))
                self.assertEqual(mapping.space, (plain.shape[1], plain.shape[0]))
                self.assertEqual(mapping.to_frame((3, 4, 20, 30)), (3, 4, 20, 30))  # The identity: one space.

    def test_rgb_frames_are_converted_after_the_crop(self):
        box = (1300, 1000, 1500, 1400)
        image, mapping = region.prepare(drawn_frame(pixel_format="RGB3"), region.Region(0.25, 0.25, 0.75, 0.75, 90))
        ys, xs = np.nonzero((image[:, :, 2] > 127) & (image[:, :, 0] < 128))  # Red in BGR order after conversion.
        self.assertGreater(len(xs), 0)
        self.assert_box_near(mapping.to_frame(bright_box(image)), expected(box))

    def test_a_malformed_frame_is_refused_as_the_ordinary_conversion_refuses_it(self):
        from sightcircuit.errors import FrameFormatError
        short = Frame(b"\0" * 100, "BGR3", 64, 48, 192, "short", 1, 0)
        with self.assertRaises(FrameFormatError):
            region.prepare(short, region.Region(0.25, 0.25, 0.75, 0.75))
        with self.assertRaises(FrameFormatError):
            region.prepare(Frame(b"\0" * 64 * 48 * 2, "YUYV", 64, 48, 128, "other", 1, 0), region.Region(rotation=90))


def scan_record(index, hits):
    """A per-frame record as the recognition command writes it: ``hits`` is name to (confidence, box) or None."""
    def target(hit):
        if hit is None:
            return marks.target_of((), "bottle", 0.5, SPACE, SPACE)
        return marks.target_of((Detection("bottle", hit[0], hit[1]),), "bottle", 0.5, SPACE, SPACE)
    return {"view": {"index": index}, "scan": {name: {"target": target(hit), "detection_count": int(hit is not None),
                                                      "inference_ms": 10.0 + index} for name, hit in hits.items()}}


class ScanTests(unittest.TestCase):
    def test_the_schedule_is_every_window_at_every_rotation_once_and_interleaved(self):
        schedule = region.scan_schedule()
        self.assertEqual(len(schedule), 24)
        self.assertEqual([name for name, _ in region.SCAN_REGIONS],
                         ["whole", "centre_half", "upper_left", "upper_right", "lower_left", "lower_right"])
        self.assertEqual({(entry.name, entry.rotation) for entry in schedule},
                         {(name, rotation) for name in region.SCAN_REGION_NAMES for rotation in region.ROTATIONS})
        self.assertEqual(dict(region.SCAN_REGIONS)["centre_half"], (0.25, 0.25, 0.75, 0.75))
        self.assertEqual(dict(region.SCAN_REGIONS)["whole"], region.WHOLE)
        cycle = list(schedule) + [schedule[0]]
        for first, second in zip(cycle, cycle[1:]):  # Consecutive frames never share a rotation, also across the wrap.
            self.assertNotEqual(first.rotation, second.rotation)
        for start in range(0, 24, 6):  # Each pass of six frames visits every window.
            self.assertEqual([entry.name for entry in schedule[start:start + 6]], list(region.SCAN_REGION_NAMES))
        self.assertEqual(schedule[0].report(), {"region": [0.0, 0.0, 1.0, 1.0], "rotation": 0, "name": "whole"})
        self.assertEqual(region.scan_schedule(), schedule)  # Fixed: the same list every time.
        for rotations in ((), (0, 0), (0, 45), (90, 90, 180)):
            with self.subTest(rotations=rotations), self.assertRaises(ValueError):
                region.scan_schedule(rotations)

    def test_the_corner_windows_hold_any_object_up_to_a_fifth_of_the_frame(self):
        corners = [fractions for name, fractions in region.SCAN_REGIONS if name not in ("whole", "centre_half")]
        for x in range(0, 81, 5):
            for y in range(0, 81, 5):
                box = (x / 100, y / 100, x / 100 + 0.2, y / 100 + 0.2)
                self.assertTrue(any(c[0] <= box[0] + 1e-9 and c[1] <= box[1] + 1e-9 and box[2] <= c[2] + 1e-9
                                    and box[3] <= c[3] + 1e-9 for c in corners), box)

    def test_the_frame_budget_gives_every_entry_enough_frames(self):
        schedule = region.scan_schedule()
        for frames, low, high in ((150, 6, 7), (300, 12, 13)):  # 30 s at 5 and at 10 frames a second.
            counts = [sum(1 for n in range(frames) if n % len(schedule) == index) for index in range(len(schedule))]
            self.assertEqual((min(counts), max(counts)), (low, high))
            by_rotation = {rotation: sum(count for entry, count in zip(schedule, counts) if entry.rotation == rotation)
                           for rotation in region.ROTATIONS}
            by_window = {name: sum(count for entry, count in zip(schedule, counts) if entry.name == name)
                         for name in region.SCAN_REGION_NAMES}
            self.assertGreaterEqual(min(by_rotation.values()), frames // 4 - 2)
            self.assertGreaterEqual(min(by_window.values()), frames // 6 - 1)

    def test_the_summary_tallies_each_detector_by_entry_rotation_and_region(self):
        schedule = region.scan_schedule()
        box = (700, 600, 800, 800)
        records = []
        for frame in range(48):  # Two cycles. "a" finds the bottle only at 90 degrees in the lower-right window;
            index = frame % 24  # "b" finds it at 90 degrees in any window, a little more surely the second time.
            entry = schedule[index]
            at_90 = entry.rotation == 90
            records.append(scan_record(index, {
                "a": (0.7 + frame / 1000, box) if at_90 and entry.name == "lower_right" else None,
                "b": (0.6 if frame < 24 else 0.9, (box[0] + frame, box[1], box[2] + frame, box[3])) if at_90 else None}))
        summary = region.scan_summary(records, schedule, ("a", "b"))
        self.assertEqual((summary["schedule_length"], summary["frames"], summary["box_coordinate_space"]),
                         (24, 48, "whole_frame_processing_pixels"))
        self.assertEqual(summary["schedule"][0], {"index": 0, "region": [0.0, 0.0, 1.0, 1.0], "rotation": 0, "name": "whole"})
        self.assertEqual([entry["index"] for entry in summary["schedule"]], list(range(24)))
        a, b = summary["detectors"]["a"], summary["detectors"]["b"]
        target = next(index for index, entry in enumerate(schedule) if (entry.name, entry.rotation) == ("lower_right", 90))
        self.assertEqual((a["frames"], a["target_frames"], a["best_entry"]), (48, 2, target))
        self.assertEqual(a["entries"][target], {
            "index": target, "frames": 2, "target_frames": 2, "best_confidence": 0.7 + (target + 24) / 1000,
            "median_confidence": round(0.7 + (target + 12) / 1000, 4), "median_box_xyxy": list(box),
            "median_centre_fraction": [round(750 / 960, 4), round(700 / 960, 4)]})
        self.assertEqual([entry for entry in a["entries"] if entry["index"] != target and entry["target_frames"]], [])
        self.assertEqual({entry["key"]: (entry["frames"], entry["target_frames"]) for entry in a["by_rotation"]},
                         {0: (12, 0), 90: (12, 2), 180: (12, 0), 270: (12, 0)})
        self.assertEqual({entry["key"]: (entry["frames"], entry["target_frames"]) for entry in a["by_region"]},
                         {name: (8, 2 if name == "lower_right" else 0) for name in region.SCAN_REGION_NAMES})
        self.assertEqual((b["target_frames"], {entry["key"]: entry["target_frames"] for entry in b["by_rotation"]}),
                         (12, {0: 0, 90: 12, 180: 0, 270: 0}))
        self.assertEqual({entry["key"]: (entry["frames"], entry["target_frames"]) for entry in b["by_region"]},
                         {name: (8, 2) for name in region.SCAN_REGION_NAMES})
        at_90 = next(entry for entry in b["by_rotation"] if entry["key"] == 90)
        self.assertEqual((at_90["best_confidence"], at_90["median_confidence"]), (0.9, 0.75))
        self.assertEqual(a["inference_ms"], {"p50": 21.0, "p95": 32.0, "max": 33.0})
        empty = next(entry for entry in a["by_rotation"] if entry["key"] == 0)
        self.assertEqual((empty["best_confidence"], empty["median_confidence"], empty["median_box_xyxy"],
                          empty["median_centre_fraction"]), (None, None, None, None))

    def test_a_scan_with_no_hit_and_a_scan_with_no_frame_say_so(self):
        schedule = region.scan_schedule()
        none = region.scan_summary([scan_record(index, {"a": None}) for index in range(24)], schedule, ("a",))
        self.assertEqual((none["detectors"]["a"]["target_frames"], none["detectors"]["a"]["best_entry"]), (0, None))
        empty = region.scan_summary([], schedule, ("a", "b"))
        self.assertEqual(empty["frames"], 0)
        for block in empty["detectors"].values():
            self.assertEqual((block["frames"], block["target_frames"], block["best_entry"], block["inference_ms"]),
                             (0, 0, None, None))
            self.assertEqual(len(block["entries"]), 24)


if __name__ == "__main__":
    unittest.main()
