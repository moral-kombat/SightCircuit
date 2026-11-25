"""``camera_boxes`` (work-order item B2): detections in the whole-frame box space to panel boxes through an alignment.

Generated detections and mappings only: no device, no camera, no image.
Standard library, except the three tests that draw the result or prepare a
generated frame, which need NumPy and OpenCV and skip without them. The
property tests draw their cases from a seeded generator, so a failure names a
case that can be run again.
"""
import ast
import dataclasses
from fractions import Fraction
import itertools
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import region  # noqa: E402
from sightcircuit.camera.region import FRAME_PIXELS, ROTATIONS, Mapping, Region  # noqa: E402
from sightcircuit.convert import processing_size  # noqa: E402
from sightcircuit.detection import POC_THRESHOLD  # noqa: E402
from sightcircuit.frame import Detection, DetectionResult, Frame  # noqa: E402
from sightcircuit.hud import boxes, camera  # noqa: E402
from sightcircuit.hud.boxes import (ALIGNMENT_FIELDS, BOX_REPORT_KEYS, DROP_REASONS, NEUTRAL, Alignment,  # noqa: E402
                                    BoxSelection, boxes_report, camera_boxes, panel_point, turned)
from sightcircuit.hud.contract import (BOX_PANEL_CONFIG, HUD_CONFIG, PanelBox, config_digest,  # noqa: E402
                                       panel_box_valid, row_label)

try:
    import cv2  # noqa: F401
    import numpy as np
    DRAWING = True
except ImportError:
    DRAWING = False

CONFIG = BOX_PANEL_CONFIG
W, H = CONFIG.width_px, CONFIG.height_px
NATIVE = 2464  # The Frame's camera image is 2464 pixels square (E100).
HALF = Fraction(1, 2)
# Frame sizes for the property tests: the Frame's, a small square, both non-square ways, and odd sizes.
SIZES = ((NATIVE, NATIVE), (640, 480), (480, 640), (1920, 1080), (97, 311), (960, 960), (1281, 719))
REGIONS = (region.WHOLE, (0.25, 0.25, 0.75, 0.75), (0.0, 0.0, 0.6, 0.6), (0.4, 0.4, 1.0, 1.0), (0.1, 0.3, 0.45, 0.95),
           (0.33, 0.0, 0.67, 1.0))
ALIGNMENTS = (NEUTRAL, Alignment(0.5, 0.5, 0, 0, 0), Alignment(1.25, 0.8, 0.1, -0.05, 0), Alignment(1, 1, 0, 0, 90),
              Alignment(0.75, 1.5, -0.25, 0.125, 270), Alignment(2, 2, 0.5, 0.5, 180))


def mapping_of(native=(NATIVE, NATIVE), fractions=region.WHOLE, rotation=0, max_side=960):
    """The ``Mapping`` ``region.prepare`` would build for this frame size and region, without a frame."""
    crop = Region(*fractions, rotation=rotation).pixels(*native)
    return Mapping(native, crop, processing_size(crop[2] - crop[0], crop[3] - crop[1], max_side), rotation,
                   region.box_space(*native, max_side))


def region_rect(mapping):
    scale_x = Fraction(mapping.space[0], mapping.native_size[0])
    scale_y = Fraction(mapping.space[1], mapping.native_size[1])
    return (mapping.crop[0] * scale_x, mapping.crop[1] * scale_y, mapping.crop[2] * scale_x, mapping.crop[3] * scale_y)


def cases():
    for native, fractions, rotation in itertools.product(SIZES, REGIONS, ROTATIONS):
        yield mapping_of(native, fractions, rotation)


def select(detections, mapping=None, config=CONFIG, *, threshold=0.5, **options):
    return camera_boxes(detections, mapping or mapping_of(), config, threshold=threshold, **options)


def dropped(selection):
    return {name: count for name, count in selection.dropped if count}


CENTRE = mapping_of(fractions=(0.25, 0.25, 0.75, 0.75))  # The centre half of the Frame's image: 240..720 of 960.


class AlignmentTests(unittest.TestCase):
    def test_neutral_default_and_five_numbers(self):
        self.assertEqual(dataclasses.astuple(Alignment()), (1.0, 1.0, 0.0, 0.0, 0))
        self.assertEqual([field.name for field in dataclasses.fields(Alignment)], list(ALIGNMENT_FIELDS))
        self.assertEqual(len(ALIGNMENT_FIELDS), 5)
        self.assertTrue(NEUTRAL.neutral)
        self.assertTrue(Alignment(1, 1, 0, 0, 0).neutral)
        self.assertFalse(Alignment(turn=90).neutral)
        self.assertFalse(Alignment(offset_x=0.01).neutral)
        self.assertEqual(Alignment(2, 1, 0, 1, 180).report(),
                         {"scale_x": 2.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 1.0, "turn": 180})
        self.assertIs(type(Alignment(2, 1, 0, 1).scale_x), float)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            NEUTRAL.scale_x = 2.0

    def test_validation(self):
        for good in (dict(scale_x=0.25), dict(scale_y=4), dict(offset_x=-1), dict(offset_y=1), dict(turn=270),
                     dict(scale_x=Fraction(1, 2))):
            Alignment(**good)
        self.assertEqual(Alignment(scale_x=Fraction(1, 2)), Alignment(scale_x=0.5))
        for bad, code in ((dict(scale_x=0.2499), "scale"), (dict(scale_y=4.01), "scale"), (dict(scale_x=0), "scale"),
                          (dict(scale_x=-1), "scale"), (dict(scale_y=float("nan")), "scale"),
                          (dict(scale_x=float("inf")), "scale"), (dict(scale_x="1"), "scale"),
                          (dict(scale_x=True), "scale"), (dict(scale_y=None), "scale"),
                          (dict(offset_x=1.001), "offset"), (dict(offset_y=-1.5), "offset"),
                          (dict(offset_x=float("nan")), "offset"), (dict(offset_y="0"), "offset"),
                          (dict(offset_x=False), "offset"),
                          (dict(turn=45), "turn"), (dict(turn=360), "turn"), (dict(turn=-90), "turn"),
                          (dict(turn=90.0), "turn"), (dict(turn=True), "turn"), (dict(turn=None), "turn")):
            with self.subTest(alignment=bad), self.assertRaisesRegex(ValueError, f"box_alignment_{code}_invalid"):
                Alignment(**bad)
        for bad in (None, (1, 1, 0, 0, 0), {"scale_x": 1}, "neutral"):
            with self.subTest(alignment=bad), self.assertRaisesRegex(ValueError, "box_alignment_invalid"):
                select([], alignment=bad)
            with self.assertRaisesRegex(ValueError, "box_alignment_invalid"):
                panel_point(0, 0, mapping_of(), CONFIG, bad)


class ExactMappingTests(unittest.TestCase):
    def test_identity_alignment_maps_the_region_rectangle_onto_the_panel_rectangle(self):
        count = 0
        for mapping in cases():
            left, top, right, bottom = region_rect(mapping)
            corners = {panel_point(x, y, mapping, CONFIG) for x in (left, right) for y in (top, bottom)}
            self.assertEqual(corners, {(0, 0), (W, 0), (0, H), (W, H)}, mapping)
            self.assertEqual(panel_point((left + right) / 2, (top + bottom) / 2, mapping, CONFIG), (W * HALF, H * HALF))
            # The whole region as one detection is the whole panel, exactly.
            box = (math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom))
            if box[2] <= mapping.space[0] and box[3] <= mapping.space[1]:
                self.assertEqual(select([Detection("bottle", .9, box)], mapping).boxes,
                                 (PanelBox("bottle", .9, (0, 0, W, H)),), mapping)
                count += 1
        self.assertGreater(count, 100)

    def test_unturned_identity_is_one_affine_map_per_axis(self):
        for native, fractions in itertools.product(SIZES, REGIONS):
            mapping = mapping_of(native, fractions)
            left, top, right, bottom = region_rect(mapping)
            for x, y in ((0, 0), (17, 5), (mapping.space[0], mapping.space[1]), (Fraction(7, 3), Fraction(1, 9))):
                self.assertEqual(panel_point(x, y, mapping, CONFIG),
                                 ((x - left) / (right - left) * W, (y - top) / (bottom - top) * H))

    def test_the_panel_shows_the_detector_input_as_the_mapping_turned_it(self):
        # ``region.Mapping`` takes a point of the detector's input back to the frame (its private ``_unturned``
        # undoes the turn); ``panel_point`` must put that frame point where it was in the detector's input,
        # as a fraction of the input's own size, for every rotation and for non-square crops.
        generator = random.Random(1124)
        for mapping in cases():
            in_width, in_height = mapping.input_size
            left, top, right, bottom = mapping.crop
            for _ in range(4):
                u, v = generator.randint(0, in_width), generator.randint(0, in_height)
                ax, ay = mapping._unturned(u, v)
                x = Fraction(left * mapping.space[0], mapping.native_size[0]) + ax * (
                    Fraction(right - left, mapping.resized[0]) * Fraction(mapping.space[0], mapping.native_size[0]))
                y = Fraction(top * mapping.space[1], mapping.native_size[1]) + ay * (
                    Fraction(bottom - top, mapping.resized[1]) * Fraction(mapping.space[1], mapping.native_size[1]))
                self.assertEqual(panel_point(x, y, mapping, CONFIG),
                                 (Fraction(u, in_width) * W, Fraction(v, in_height) * H), (mapping, u, v))

    def test_boxes_mapped_back_by_the_mapping_land_where_the_detector_saw_them(self):
        # The same property through the public path a stage uses: ``Mapping.result`` rounds a detector box to
        # whole box-space pixels, so the panel box agrees with the detector's box within that rounding.
        generator = random.Random(2411)
        for mapping in cases():
            in_width, in_height = mapping.input_size
            if min(in_width, in_height) < 40:
                continue
            x0, y0 = generator.randint(2, in_width // 2), generator.randint(2, in_height // 2)
            x1, y1 = generator.randint(x0 + 8, in_width - 2), generator.randint(y0 + 8, in_height - 2)
            result = mapping.result(DetectionResult((Detection("bottle", .9, (x0, y0, x1, y1)),), 1.0))
            (box,) = select(list(result.detections), mapping).boxes
            # One box-space pixel of rounding on each side, as panel pixels on the axis it lands on.
            turned_axes = mapping.rotation in (90, 270)
            across = (mapping.crop[3] - mapping.crop[1], mapping.native_size[1], mapping.space[1]) if turned_axes else (
                mapping.crop[2] - mapping.crop[0], mapping.native_size[0], mapping.space[0])
            down = (mapping.crop[2] - mapping.crop[0], mapping.native_size[0], mapping.space[0]) if turned_axes else (
                mapping.crop[3] - mapping.crop[1], mapping.native_size[1], mapping.space[1])
            slack_x = W * across[1] / (across[0] * across[2]) + 1
            slack_y = H * down[1] / (down[0] * down[2]) + 1
            expected = (x0 * W / in_width, y0 * H / in_height, x1 * W / in_width, y1 * H / in_height)
            for got, want, slack in zip(box.box_xyxy, expected, (slack_x, slack_y, slack_x, slack_y)):
                self.assertLessEqual(abs(got - want), slack, (mapping, box, expected))

    def test_turned_follows_the_mapping_convention_and_composes(self):
        # A w x h image turned 90 clockwise sends pixel (x, y) to (h - y, x): the top left corner goes top right.
        self.assertEqual(turned(0, 0, 90), (1, 0))
        self.assertEqual(turned(1, 0, 90), (1, 1))
        self.assertEqual(turned(0, 0, 180), (1, 1))
        self.assertEqual(turned(0, 0, 270), (0, 1))
        point = (Fraction(1, 5), Fraction(2, 7))
        for first, second in itertools.product(ROTATIONS, ROTATIONS):
            self.assertEqual(turned(*turned(*point, first), second), turned(*point, (first + second) % 360))
        for rotation in ROTATIONS:
            self.assertEqual(turned(HALF, HALF, rotation), (HALF, HALF))
        with self.assertRaises(ValueError):
            turned(0, 0, 45)

    def test_each_alignment_number_does_one_thing(self):
        generator = random.Random(24)
        for mapping in itertools.islice(cases(), 0, None, 5):
            for _ in range(3):
                x, y = generator.randint(0, mapping.space[0]), generator.randint(0, mapping.space[1])
                px, py = panel_point(x, y, mapping, CONFIG)
                for scale in (0.25, 0.5, 1.5, 4):
                    exact = Fraction(scale)
                    self.assertEqual(panel_point(x, y, mapping, CONFIG, Alignment(scale_x=scale)),
                                     (W * HALF + (px - W * HALF) * exact, py))
                    self.assertEqual(panel_point(x, y, mapping, CONFIG, Alignment(scale_y=scale)),
                                     (px, H * HALF + (py - H * HALF) * exact))
                for offset in (-1, -0.125, 0.3, 1):
                    exact = Fraction(offset)
                    self.assertEqual(panel_point(x, y, mapping, CONFIG, Alignment(offset_x=offset)),
                                     (px + exact * W, py))
                    self.assertEqual(panel_point(x, y, mapping, CONFIG, Alignment(offset_y=offset)),
                                     (px, py + exact * H))
                for turn in ROTATIONS:
                    p, q = turned(px / W, py / H, turn)
                    self.assertEqual(panel_point(x, y, mapping, CONFIG, Alignment(turn=turn)), (p * W, q * H))
                # Together: the turn first, then the scale about the centre, then the offset.
                both = Alignment(1.25, 0.75, 0.25, -0.5, 90)
                p, q = turned(px / W, py / H, 90)
                self.assertEqual(panel_point(x, y, mapping, CONFIG, both),
                                 ((HALF + (p - HALF) * Fraction(1.25) + Fraction(0.25)) * W,
                                  (HALF + (q - HALF) * Fraction(0.75) - HALF) * H))

    def test_scale_keeps_the_centre_and_offset_keeps_the_size(self):
        centre = [Detection("bottle", .9, (465, 465, 495, 495))]  # Centred on (480, 480), the region's centre: panel 240..272.
        for alignment in (Alignment(0.5, 0.5), Alignment(2, 0.25), Alignment(4, 4)):
            (box,) = select(centre, CENTRE, alignment=alignment).boxes
            x0, y0, x1, y1 = box.box_xyxy
            self.assertEqual((x0 + x1, y0 + y1), (W, H), alignment)
        (plain,) = select(centre, CENTRE).boxes
        (moved,) = select(centre, CENTRE, alignment=Alignment(offset_x=0.25, offset_y=-0.125)).boxes
        self.assertEqual(moved.box_xyxy, (plain.box_xyxy[0] + 128, plain.box_xyxy[1] - 64, plain.box_xyxy[2] + 128,
                                          plain.box_xyxy[3] - 64))
        (wide,) = select(centre, CENTRE, alignment=Alignment(scale_x=2)).boxes
        self.assertEqual((wide.box_xyxy[2] - wide.box_xyxy[0], wide.box_xyxy[3] - wide.box_xyxy[1]),
                         (2 * (plain.box_xyxy[2] - plain.box_xyxy[0]), plain.box_xyxy[3] - plain.box_xyxy[1]))

    def test_one_frame_pixel_in_panel_pixels_by_region(self):
        # Boxes arrive as whole pixels of the box space (``Mapping.to_frame`` rounds them), so a box edge is off by
        # up to half a frame pixel, as panel pixels, plus the half panel pixel of this module's own rounding.
        def frame_pixel(fraction):
            low = (1 - fraction) / 2
            mapping = mapping_of(fractions=(low, low, low + fraction, low + fraction))
            return float(panel_point(481, 480, mapping, CONFIG)[0] - panel_point(480, 480, mapping, CONFIG)[0])
        self.assertAlmostEqual(frame_pixel(1.0), 0.533, places=3)
        self.assertAlmostEqual(frame_pixel(0.5), 1.067, places=3)
        self.assertAlmostEqual(frame_pixel(0.25), 2.133, places=3)
        self.assertAlmostEqual(frame_pixel(0.05), 10.6, places=1)  # The crop taken is 124 of 2464 native pixels.
        self.assertAlmostEqual(frame_pixel(0.05) / 2 + 0.5, 5.8, places=1)  # The worst edge error there.
        # An edge stays within 2 panel pixels while a frame pixel is at most 3 of them: a region of at least
        # 512 / (3 * 960) = 0.178 of the frame on that axis (171 box-space pixels, 438 of the Frame's 2464).
        self.assertLessEqual(frame_pixel(0.18) / 2 + 0.5, 2.0)
        self.assertGreater(frame_pixel(0.17) / 2 + 0.5, 2.0)
        self.assertAlmostEqual(W / (3 * 960), 0.178, places=3)

    def test_another_panel_size_scales_with_it(self):
        small = dataclasses.replace(CONFIG, width_px=200, height_px=120, name_max_px=58)
        self.assertEqual(panel_point(480, 480, CENTRE, small), (100, 60))
        self.assertEqual(select([Detection("bottle", .9, (240, 240, 720, 720))], CENTRE, small).boxes[0].box_xyxy,
                         (0, 0, 200, 120))

    def test_every_output_is_a_valid_panel_box_for_any_frame_region_rotation_and_alignment(self):
        generator = random.Random(20261124)
        shown = 0
        for mapping, alignment in itertools.product(itertools.islice(cases(), 0, None, 3), ALIGNMENTS):
            width, height = mapping.space
            detections = []
            for index in range(12):
                x0, y0 = generator.randint(0, width - 1), generator.randint(0, height - 1)
                detections.append(Detection(f"thing {index % 5}", generator.choice((.4, .5, .7, .9, 1.0)),
                                            (x0, y0, generator.randint(x0 + 1, width), generator.randint(y0 + 1, height))))
            selection = select(detections, mapping, alignment=alignment)
            self.assertLessEqual(len(selection.boxes), CONFIG.max_boxes)
            self.assertEqual(selection.detections, 12)
            self.assertEqual(len(selection.boxes) + sum(count for _name, count in selection.dropped), 12)
            self.assertEqual(tuple(name for name, _count in selection.dropped), DROP_REASONS)
            for box in selection.boxes:
                self.assertTrue(panel_box_valid(box, CONFIG), (mapping, alignment, box))
            self.assertEqual(list(selection.boxes),
                             sorted(selection.boxes, key=lambda box: (-box.confidence, box.label, box.box_xyxy)))
            self.assertEqual(select(detections[::-1], mapping, alignment=alignment), selection)
            shown += len(selection.boxes)
        self.assertGreater(shown, 200)


class DropAndClipRuleTests(unittest.TestCase):
    def test_a_box_is_kept_by_its_centre_and_cut_to_the_region(self):
        inside = Detection("bottle", .9, (300, 300, 400, 420))
        self.assertEqual(select([inside], CENTRE).boxes, (PanelBox("bottle", .9, (64, 64, 171, 192)),))
        # Centre exactly on the region's left edge (x = 240): kept, and cut to the edge, so it lies on the panel's.
        on_edge = select([Detection("bottle", .9, (200, 300, 280, 420))], CENTRE)
        self.assertEqual(on_edge.boxes, (PanelBox("bottle", .9, (0, 64, 43, 192)),))
        # One box-space pixel further out and its centre has left the region.
        outside = select([Detection("bottle", .9, (199, 300, 280, 420))], CENTRE)
        self.assertEqual((outside.boxes, dropped(outside)), ((), {"outside_region": 1}))
        for box in ((0, 0, 100, 100), (700, 700, 960, 960), (300, 0, 400, 470), (721, 300, 800, 400)):
            with self.subTest(box=box):
                self.assertEqual(dropped(select([Detection("cat", 1.0, box)], CENTRE)), {"outside_region": 1})

    def test_a_box_on_the_region_edge_lies_on_the_panel_edge(self):
        for box, expected in (((240, 300, 300, 400), (0, 64, 64, 171)), ((300, 240, 400, 300), (64, 0, 171, 64)),
                              ((660, 300, 720, 400), (448, 64, 512, 171)), ((300, 660, 400, 720), (64, 448, 171, 512)),
                              ((240, 240, 720, 720), (0, 0, 512, 512)), ((0, 0, 960, 960), (0, 0, 512, 512))):
            with self.subTest(box=box):
                self.assertEqual(select([Detection("bottle", .9, box)], CENTRE).boxes[0].box_xyxy, expected)

    def test_off_panel_by_centre_and_cut_to_the_panel(self):
        box = [Detection("bottle", .9, (600, 440, 700, 520))]  # Panel (384, 213, 491, 299) when neutral.
        self.assertEqual(select(box, CENTRE).boxes[0].box_xyxy, (384, 213, 491, 299))
        # Moved right by 0.1 panel widths its right side passes the panel's edge: cut there.
        self.assertEqual(select(box, CENTRE, alignment=Alignment(offset_x=0.1)).boxes[0].box_xyxy, (435, 213, 512, 299))
        gone = select(box, CENTRE, alignment=Alignment(offset_x=0.5))  # Its centre is now off the panel.
        self.assertEqual((gone.boxes, dropped(gone)), ((), {"off_panel": 1}))
        self.assertEqual(dropped(select(box, CENTRE, alignment=Alignment(offset_y=-1))), {"off_panel": 1})
        self.assertEqual(dropped(select(box, CENTRE, alignment=Alignment(scale_x=4))), {"off_panel": 1})
        # Centre exactly on the panel's right edge: kept, one pixel column wide at least.
        edge = [Detection("bottle", .9, (580, 440, 620, 520))]  # Centre x 600: three quarters across the region.
        self.assertEqual(panel_point(600, 480, CENTRE, CONFIG, Alignment(offset_x=0.25)), (W, H * HALF))
        self.assertEqual(select(edge, CENTRE, alignment=Alignment(offset_x=0.25)).boxes[0].box_xyxy, (491, 213, 512, 299))
        self.assertEqual(dropped(select(edge, CENTRE, alignment=Alignment(offset_x=0.2501))), {"off_panel": 1})

    def test_rounding_is_halves_up_and_never_empty(self):
        mapping = mapping_of((1024, 1024), max_side=1024)  # One box-space pixel is half a panel pixel.
        self.assertEqual(mapping.space, (1024, 1024))
        self.assertEqual(select([Detection("a", .9, (1, 3, 5, 9))], mapping).boxes[0].box_xyxy, (1, 2, 3, 5))
        self.assertEqual(select([Detection("a", .9, (100, 100, 101, 101))], mapping).boxes[0].box_xyxy, (50, 50, 51, 51))
        self.assertEqual(select([Detection("a", .9, (1023, 1023, 1024, 1024))], mapping).boxes[0].box_xyxy,
                         (511, 511, 512, 512))
        self.assertEqual(select([Detection("a", .9, (0, 0, 0.25, 0.25))], mapping).boxes[0].box_xyxy, (0, 0, 1, 1))
        tiny = select([Detection("a", .9, (500, 500, 501, 501))], mapping, alignment=Alignment(0.25, 0.25))
        self.assertEqual(tiny.boxes[0].box_xyxy, (255, 255, 256, 256))  # 254.5 to 254.625, halves up.

    def test_rotation_examples(self):
        # A box in the top left of the region. Turned 90 clockwise the detector saw it top right, and so on round.
        box = [Detection("bottle", .9, (240, 240, 360, 300))]
        expected = {0: (0, 0, 128, 64), 90: (448, 0, 512, 128), 180: (384, 448, 512, 512), 270: (0, 384, 64, 512)}
        for rotation, rect in expected.items():
            mapping = mapping_of(fractions=(0.25, 0.25, 0.75, 0.75), rotation=rotation)
            with self.subTest(rotation=rotation):
                self.assertEqual(select(box, mapping).boxes[0].box_xyxy, rect)
                # The alignment's turn does the same on an unturned region, and the two add up.
                self.assertEqual(select(box, CENTRE, alignment=Alignment(turn=rotation)).boxes[0].box_xyxy, rect)
                back = Alignment(turn=(360 - rotation) % 360)
                self.assertEqual(select(box, mapping, alignment=back).boxes[0].box_xyxy, expected[0])

    def test_a_non_square_region_fills_the_panel_on_both_axes(self):
        mapping = mapping_of((1920, 1080), (0.25, 0.0, 0.75, 1.0))  # Space 960 x 540; the region is 240..720 by 0..540.
        self.assertEqual(mapping.space, (960, 540))
        self.assertEqual(select([Detection("tv", .9, (240, 0, 480, 270))], mapping).boxes[0].box_xyxy, (0, 0, 256, 256))
        turned_mapping = mapping_of((1920, 1080), (0.25, 0.0, 0.75, 1.0), 90)
        self.assertEqual(select([Detection("tv", .9, (240, 0, 480, 270))], turned_mapping).boxes[0].box_xyxy,
                         (256, 0, 512, 256))


class SelectionRuleTests(unittest.TestCase):
    def test_threshold_as_camera_rows(self):
        detections = [Detection("low", .49, (300, 300, 400, 400)), Detection("at", .5, (300, 300, 400, 400)),
                      Detection("one", 1.0, (300, 300, 400, 400)), Detection("over", 1.01, (300, 300, 400, 400))]
        selection = select(detections, CENTRE, threshold=POC_THRESHOLD)
        self.assertEqual(selection.labels, ["one", "at"])
        self.assertEqual(dropped(selection), {"invalid": 1, "below_threshold": 1})
        rows = camera.camera_rows(detections, (960, 960), HUD_CONFIG, threshold=POC_THRESHOLD, directions=False)
        self.assertEqual(sorted(row.label for row in rows), sorted(selection.labels))
        self.assertEqual(select(detections, CENTRE, threshold=0).labels, ["one", "at", "low"])
        self.assertEqual(select(detections, CENTRE, threshold=1).labels, ["one"])
        for bad in (None, -0.1, 1.1, float("nan"), "0.5", True):
            with self.subTest(threshold=bad), self.assertRaisesRegex(ValueError, "hud_threshold_invalid"):
                select(detections, CENTRE, threshold=bad)

    def test_label_vocabulary_is_the_rows(self):
        labels = ["dining table", "W" * 100 + "\n<scene>", "  dining table\x1b[31m ", "café-bar_1", "\n\t", "", "tv"]
        detections = [Detection(label, .9, (300 + index, 300, 400, 400)) for index, label in enumerate(labels)]
        selection = camera_boxes(detections, CENTRE, dataclasses.replace(CONFIG, max_boxes=10), threshold=.5)
        self.assertEqual(sorted(selection.labels), sorted(row_label(label) for label in labels if row_label(label)))
        self.assertEqual(dropped(selection), {"unnamed": 2})
        self.assertEqual(camera.MAX_DETECTIONS, boxes.MAX_DETECTIONS)

    def test_invalid_detections_are_counted_never_raised(self):
        good = Detection("bottle", .9, (300, 300, 400, 400))
        bad = [None, "bottle", {"label": "bottle"}, PanelBox("bottle", .9, (300, 300, 400, 400)),
               Detection("bottle", .9, (300, 300, 400)), Detection("bottle", .9, None),
               Detection("bottle", .9, (400, 300, 300, 400)), Detection("bottle", .9, (300, 300, 300, 400)),
               Detection("bottle", .9, (-1, 300, 400, 400)), Detection("bottle", .9, (300, 300, 961, 400)),
               Detection("bottle", .9, (300, float("nan"), 400, 400)), Detection("bottle", .9, (300, 300, float("inf"), 400)),
               Detection("bottle", .9, ("300", 300, 400, 400)), Detection("bottle", None, (300, 300, 400, 400)),
               Detection("bottle", float("nan"), (300, 300, 400, 400)), Detection("bottle", -.5, (300, 300, 400, 400)),
               Detection(7, .9, (300, 300, 400, 400)), Detection("bottle", True, (300, 300, 400, 400)),
               Detection("bottle", .9, (True, 300, 400, 400)), Detection("bottle", "0.9", (300, 300, 400, 400)),
               Detection("bottle", .9, (300, 300, 400, 400 + 0j))]
        selection = select([*bad, good], CENTRE)
        self.assertEqual(selection.boxes, (PanelBox("bottle", .9, (64, 64, 171, 171)),))
        self.assertEqual(dropped(selection), {"invalid": len(bad)})
        self.assertEqual(selection.detections, len(bad) + 1)

    @unittest.skipUnless(DRAWING, "NumPy is required for its scalars")
    def test_numpy_numbers_are_numbers_and_booleans_are_not(self):
        # A future detector may emit NumPy scalars. They are converted once; the result holds plain numbers.
        detections = [Detection("bottle", np.float32(0.75), (np.int64(300), np.float64(300), np.float32(400), 400)),
                      Detection("vase", np.float64(0.9), np.array([500, 300, 600, 400]).tolist()),
                      Detection("cup", np.bool_(True), (300, 300, 400, 400)),
                      Detection("tv", .9, (np.bool_(True), 300, 400, 400)),
                      Detection("fork", np.float64("nan"), (300, 300, 400, 400))]
        selection = select(detections, CENTRE, threshold=np.float64(0.5),
                           alignment=Alignment(scale_x=np.float32(1.0), offset_y=np.int64(0)))
        self.assertEqual(selection.boxes, (PanelBox("vase", 0.9, (277, 64, 384, 171)),
                                           PanelBox("bottle", 0.75, (64, 64, 171, 171))))
        self.assertEqual(dropped(selection), {"invalid": 3})
        for box in selection.boxes:
            self.assertIs(type(box.confidence), float)
            self.assertEqual({type(value) for value in box.box_xyxy}, {int})
        self.assertIs(type(selection.threshold), float)
        self.assertTrue(selection.alignment.neutral)
        json.dumps(boxes_report(selection, CONFIG))
        # Where camera_rows differs: it takes int and float and their subclasses, so np.float64 and Python's True
        # are numbers to it and np.float32 and np.int64 are not.
        rows = camera.camera_rows(detections, (960, 960), HUD_CONFIG, threshold=0.5, directions=False)
        self.assertEqual([row.label for row in rows], ["vase"])
        true_score = [Detection("cup", True, (300, 300, 400, 400))]
        self.assertEqual(len(camera.camera_rows(true_score, (960, 960), HUD_CONFIG, threshold=0.5)), 1)
        self.assertEqual(dropped(select(true_score, CENTRE)), {"invalid": 1})

    def test_cap_by_confidence_then_a_stable_tie_break(self):
        names = ["bottle", "cup", "chair", "person", "tv", "vase", "book"]
        detections = [Detection(name, .9 - index / 100, (300, 300 + index, 400, 400)) for index, name in enumerate(names)]
        selection = select(detections, CENTRE)
        self.assertEqual(selection.labels, names[:CONFIG.max_boxes])
        self.assertEqual(dropped(selection), {"over_cap": 2})
        tied = [Detection(name, .8, (300, 300, 400, 400)) for name in names]
        expected = sorted(names)[:CONFIG.max_boxes]
        generator = random.Random(5)
        for _ in range(20):
            generator.shuffle(tied)
            self.assertEqual(select(tied, CENTRE).labels, expected)
        same_name = [Detection("bottle", .8, (300 + shift, 300, 400, 400)) for shift in (30, 10, 20)]
        self.assertEqual([box.box_xyxy[0] for box in select(same_name, CENTRE).boxes], [75, 85, 96])

    def test_exact_duplicates_are_dropped_but_two_objects_of_one_name_are_two_boxes(self):
        detections = [Detection("bottle", .9, (300, 300, 400, 400)), Detection("bottle", .8, (300, 300, 400, 400)),
                      Detection("bottle", .7, (500, 300, 600, 400)), Detection("bottle", .6, (300.2, 300.2, 400.2, 399.9))]
        selection = select(detections, CENTRE)
        self.assertEqual([(box.confidence, box.box_xyxy) for box in selection.boxes],
                         [(.9, (64, 64, 171, 171)), (.7, (277, 64, 384, 171))])
        self.assertEqual(dropped(selection), {"duplicate": 2})

    def test_arguments_are_checked(self):
        with self.assertRaisesRegex(ValueError, "hud_detection_budget_exceeded"):
            select([Detection("a", .9, (1, 1, 2, 2))] * (boxes.MAX_DETECTIONS + 1))
        with self.assertRaisesRegex(ValueError, "hud_detection_budget_exceeded"):
            select(iter(()))
        self.assertEqual(select((), CENTRE), BoxSelection(
            (), 0, tuple((name, 0) for name in DROP_REASONS), config_digest(CONFIG), (960, 960),
            (616, 616, 1848, 1848), 0, 0.5, NEUTRAL))
        for config in (HUD_CONFIG, None, dataclasses.asdict(CONFIG)):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
                select([], CENTRE, config)
        good = mapping_of()
        for bad in (None, (0, 0, 960, 960), Region(), dataclasses.replace(good, rotation=45),
                    dataclasses.replace(good, rotation=90.0), dataclasses.replace(good, crop=(0, 0, NATIVE + 1, NATIVE)),
                    dataclasses.replace(good, crop=(10, 0, 10, NATIVE)), dataclasses.replace(good, crop=(0, 0, NATIVE)),
                    dataclasses.replace(good, crop=[0, 0, NATIVE, NATIVE]), dataclasses.replace(good, space=(0, 960)),
                    dataclasses.replace(good, space=(960.0, 960)), dataclasses.replace(good, native_size=(0, NATIVE)),
                    dataclasses.replace(good, native_size=(True, NATIVE))):
            with self.subTest(mapping=bad), self.assertRaisesRegex(ValueError, "box_mapping_invalid"):
                camera_boxes([], bad, CONFIG, threshold=.5)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.alignment = Alignment(1.25, 0.8, 0.1, -0.05, 90)
        self.detections = [Detection("bottle", .9, (300, 300, 400, 400)), Detection("zebra", .9, (0, 0, 50, 50)),
                           Detection("kettle", .2, (300, 300, 400, 400)), Detection("\n", .9, (300, 300, 400, 400)),
                           None, Detection("bottle", .9, (300, 300, 400, 400)), Detection("tv", .9, (470, 241, 490, 260))]
        self.selection = select(self.detections, CENTRE, alignment=self.alignment)
        self.report = boxes_report(self.selection, CONFIG)

    def test_block_is_counts_and_format_metadata_with_closed_keys(self):
        self.assertEqual(tuple(self.report), BOX_REPORT_KEYS)
        self.assertEqual(self.report, {
            "layout_id": "head-fixed-box-panel-v1", "config_sha256": config_digest(CONFIG), "panel_px": [512, 512],
            "max_boxes": 5, "box_coordinate_space": "box_panel_pixels", "source_coordinate_space": FRAME_PIXELS,
            "source_space_px": [960, 960], "region_crop_native_xyxy": [616, 616, 1848, 1848], "region_rotation": 0,
            "alignment": {"scale_x": 1.25, "scale_y": 0.8, "offset_x": 0.1, "offset_y": -0.05, "turn": 90},
            "alignment_neutral": False, "threshold": 0.5, "detections": 7, "boxes_shown": 1, "boxes_dropped": 6,
            "dropped": {"invalid": 1, "below_threshold": 1, "unnamed": 1, "outside_region": 1, "off_panel": 1,
                        "duplicate": 1, "over_cap": 0}})
        text = json.dumps(self.report)
        for word in ("bottle", "zebra", "kettle", "box_xyxy", "label", "confidence"):
            self.assertNotIn(word, text, word)  # No label, box or score: counts only.
        self.assertLess(len(text), 800)
        neutral = boxes_report(select(self.detections, CENTRE), CONFIG)
        self.assertTrue(neutral["alignment_neutral"])
        self.assertEqual(neutral["alignment"], NEUTRAL.report())

    def test_the_camera_stage_schema_accepts_it_as_a_block(self):
        try:
            from sightcircuit.hud import camera_stage
        except ImportError as error:
            self.skipTest(f"the camera stage's module needs {error.name}")
        camera_stage.check_report({"boxes": self.report}, blocks={"boxes": BOX_REPORT_KEYS})
        with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid"):
            camera_stage.check_report({"boxes": {**self.report, "pixels": 1}}, blocks={"boxes": BOX_REPORT_KEYS})
        # Wired in by item B5: the stage's report carries the block, summed over a run, with one count of its own.
        self.assertEqual(camera_stage.REPORT_BLOCKS["boxes"], (*BOX_REPORT_KEYS, "results_counted"))

    def test_the_report_states_what_the_selection_was_made_with(self):
        # The region, the threshold and the alignment are the selection's own: the report cannot be handed others.
        self.assertEqual(list(__import__("inspect").signature(boxes_report).parameters), ["selection", "config"])
        self.assertEqual((self.selection.alignment, self.selection.threshold, self.selection.rotation),
                         (self.alignment, 0.5, 0))
        self.assertEqual((self.selection.space, self.selection.crop), (CENTRE.space, CENTRE.crop))
        self.assertEqual(self.selection.config_sha256, config_digest(CONFIG))
        turned_mapping = mapping_of((1920, 1080), (0.25, 0.0, 0.75, 1.0), 270)
        other = boxes_report(camera_boxes([], turned_mapping, CONFIG, threshold=1, alignment=Alignment(turn=180)), CONFIG)
        self.assertEqual((other["source_space_px"], other["region_crop_native_xyxy"], other["region_rotation"]),
                         ([960, 540], [480, 0, 1440, 1080], 270))
        self.assertEqual((other["threshold"], other["alignment"]["turn"], other["alignment_neutral"]), (1.0, 180, False))
        # A report for another geometry than the selection was made for is refused.
        with self.assertRaisesRegex(ValueError, "box_selection_config_changed"):
            boxes_report(self.selection, dataclasses.replace(CONFIG, max_boxes=6))

    def test_an_inconsistent_selection_is_refused(self):
        counts = self.selection.dropped
        for bad in (None, (), dataclasses.replace(self.selection, detections=8),
                    dataclasses.replace(self.selection, dropped=counts[:-1]),
                    dataclasses.replace(self.selection, dropped=counts[::-1]),
                    dataclasses.replace(self.selection, dropped=(("invalid", -1), *counts[1:]), detections=5),
                    dataclasses.replace(self.selection, boxes=self.selection.boxes * 6, detections=12),
                    dataclasses.replace(self.selection, alignment=None),
                    dataclasses.replace(self.selection, alignment=(1, 1, 0, 0, 0)),
                    dataclasses.replace(self.selection, threshold="0.5")):
            with self.subTest(selection=bad), self.assertRaisesRegex(ValueError, "box_selection_invalid"):
                boxes_report(bad, CONFIG)
        with self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
            boxes_report(self.selection, HUD_CONFIG)


class BoundaryTests(unittest.TestCase):
    def test_module_imports_no_calibration_mask_stage_or_transport_code(self):
        text = (ROOT / "sightcircuit/hud/boxes.py").read_text()
        imported = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported.update((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(("." * node.level + (node.module or ""), alias.name) for alias in node.names)
        self.assertEqual(imported, {
            ("dataclasses", "dataclass"), ("fractions", "Fraction"), ("math", None), ("numbers", None),
            ("..camera.region", "FRAME_PIXELS"), ("..camera.region", "ROTATIONS"), ("..camera.region", "Mapping"),
            ("..frame", "Detection"), (".contract", "BOX_PANEL_PIXELS"), (".contract", "BoxPanelConfig"),
            (".contract", "PanelBox"), (".contract", "config_digest"), (".contract", "row_label"),
            (".contract", "select_boxes")})
        for word in ("Calibration", "HudMask", "MappingCandidate", "Footprint", "viewports", "CaptureFormat",
                     "qualification", "candidate", "import_module", "__import__", "camera.contract", "export_reader",
                     "overlay", "gates", "open(", "numpy", "cv2"):
            self.assertNotIn(word, text, word)

    def test_only_the_camera_stage_selects_boxes_and_only_its_command_builds_an_alignment(self):
        # Since item B5 the camera stage is the one caller of ``camera_boxes`` and ``boxes_report``, and the
        # ``camera_hud`` command the one module beside it that imports this one (for ``Alignment``, which it
        # builds from the operator's five numbers). No script, source or other command reaches either.
        users = {}
        for path in [*(ROOT / "sightcircuit").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
            name = path.relative_to(ROOT).as_posix()
            if name not in ("sightcircuit/hud/boxes.py", "sightcircuit/hud/__init__.py"):
                text = path.read_text()
                found = [word for word in ("camera_boxes", "boxes_report", "hud.boxes", "hud import boxes",
                                           "from .boxes import") if word in text]
                if found:
                    users[name] = found
        self.assertEqual(users, {"sightcircuit/hud/camera_stage.py": ["camera_boxes", "boxes_report", "from .boxes import"],
                                 "sightcircuit/commands/camera_hud.py": ["hud.boxes"]})
        command = (ROOT / "sightcircuit/commands/camera_hud.py").read_text()
        self.assertIn("from sightcircuit.hud.boxes import ALIGNMENT_FIELDS, Alignment\n", command)


@unittest.skipUnless(DRAWING, "NumPy and OpenCV are required to draw the panel and prepare a frame")
class EndToEndTests(unittest.TestCase):
    def test_a_selection_draws(self):
        from sightcircuit.hud.box_render import draw_boxes
        detections = [Detection("bottle", .9, (300, 300, 500, 600)), Detection("chair", .7, (600, 240, 720, 400))]
        selection = select(detections, CENTRE)
        self.assertEqual([box.box_xyxy for box in selection.boxes], [(64, 64, 277, 384), (384, 0, 512, 171)])
        image = draw_boxes(selection.boxes, CONFIG)
        self.assertEqual(tuple(int(part) for part in image[200, 64]), (*CONFIG.casing_rgb, 255))
        self.assertEqual(tuple(int(part) for part in image[100, 511]), (0, 0, 0, 0))  # The side on the edge is open.
        self.assertFalse(image[200, 100].any())

    def test_a_drawn_rectangle_found_in_the_prepared_region_lands_on_the_panel_where_it_was_seen(self):
        # A generated frame: dark, one bright rectangle. The stand-in detector is the rectangle's bounding box in
        # whatever image it is given, as tests/test_camera_region.py does.
        native = np.zeros((1200, 1600, 3), dtype=np.uint8)
        native[500:700, 900:1000] = 255  # x 900..1000, y 500..700 in native pixels.
        frame = Frame(pixels=native.tobytes(), width=1600, height=1200, pixel_format="BGR3", stride=4800, source="synthetic:boxes",
                      sequence=1, observed_ns=1)
        for rotation in ROTATIONS:
            with self.subTest(rotation=rotation):
                image, mapping = region.prepare(frame, Region(0.25, 0.25, 0.75, 0.75, rotation=rotation))
                rows, columns = np.nonzero(image[..., 0] > 127)
                seen = (int(columns.min()), int(rows.min()), int(columns.max()) + 1, int(rows.max()) + 1)
                result = mapping.result(DetectionResult((Detection("bottle", .9, seen),), 1.0))
                (box,) = camera_boxes(list(result.detections), mapping, CONFIG, threshold=.5).boxes
                in_width, in_height = mapping.input_size
                expected = (seen[0] * W / in_width, seen[1] * H / in_height, seen[2] * W / in_width,
                            seen[3] * H / in_height)
                for got, want in zip(box.box_xyxy, expected):
                    self.assertLessEqual(abs(got - want), 2, (box, expected))
        # Unturned, the rectangle is at fractions 0.625..0.75 by 0.3333..0.6667 of the centre half.
        _image, mapping = region.prepare(frame, Region(0.25, 0.25, 0.75, 0.75))
        (box,) = camera_boxes([Detection("bottle", .9, (540, 300, 600, 420))], mapping, CONFIG, threshold=.5).boxes
        self.assertEqual(box.box_xyxy, (320, 171, 384, 341))


if __name__ == "__main__":
    unittest.main()
