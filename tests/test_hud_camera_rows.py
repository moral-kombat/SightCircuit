"""The mask-free camera row selector and the names-only tile. Generated values only; no device, no camera."""
import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

try:
    import cv2  # noqa: F401  (draw_tile needs it)
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for HUD tile tests") from error

from sightcircuit import hud
from sightcircuit.frame import Detection
from sightcircuit.hud import camera, contract
from sightcircuit.hud.camera import ORIENTATIONS, camera_rows
from sightcircuit.hud.contract import NO_DIRECTION, HudRow, row_label, select_rows
from sightcircuit.hud.render import draw_tile

ROOT = Path(__file__).resolve().parents[1]
SHAPE = (480, 640)  # height, width
CONFIG = hud.HUD_CONFIG
# Tile digests on the locked OpenCV builds (4.12 on Python 3.12, 4.13 on 3.14).
# EMPTY and ARROWS were taken from the renderer before the names-only row
# existed, so they pin that existing rows draw exactly as they did.
EMPTY_TILE = "fb77fdb76eb6451cee6fed944e8f380ded14ef2c5ae114e153c5668ceb425f53"
ARROWS_TILE = "520d63cbbf0f545f24edd351538c21d57c2bbbf9c6f4a959a726ce84883a4b9b"
NAMES_TILE = "705418f6bafaa9ee519002f65d839e9abd0e414d3ae48110f88ebb413fbe197d"
MIXED_TILE = "f75ed443682e2c7deba25c52bd63dcdc2c24f85b82c88505f75f5ce3f0c63352"
ARROW_ROWS = (HudRow("chair", .93, (1, 1)), HudRow("person", .81, (-1, 0)), HudRow("tv", .7, (0, 0)))
NAME_ROWS = (HudRow("bottle", .9, NO_DIRECTION), HudRow("chair", .8, NO_DIRECTION),
             HudRow("person", .7, NO_DIRECTION))


def digest(tile):
    return hashlib.sha256(tile.tobytes()).hexdigest()


def centred(label, confidence, cx, cy, half=10, shape=SHAPE):
    """A detection whose box centre sits at the fractions ``(cx, cy)`` of the frame."""
    height, width = shape[:2]
    x, y = cx * width, cy * height
    return Detection(label, confidence, (x - half, y - half, x + half, y + half))


def rows(detections, shape=SHAPE, config=CONFIG, *, threshold=.5, **options):
    return camera_rows(detections, shape, config, threshold=threshold, **options)


def direction(cx, cy, shape=SHAPE, **options):
    (row,) = rows([centred("bottle", .9, cx, cy, shape=shape)], shape, **options)
    return row.direction


class SharedRowHygieneTests(unittest.TestCase):
    def test_label_is_ascii_printable_and_at_most_twenty_characters(self):
        self.assertEqual(row_label("W" * 100 + "\n<scene>"), "W" * 20)
        self.assertEqual(row_label("  dining table\x1b[31m "), "dining table31m")
        self.assertEqual(row_label("café-bar_1"), "caf-bar_1")
        self.assertEqual(row_label("x" * 19 + " tail"), "x" * 19)  # Cut at twenty, then stripped.
        self.assertEqual(row_label("\n<>☃"), "")
        self.assertEqual(contract.ROW_LABEL_CHARS, 20)

    def test_rows_are_ordered_deduplicated_by_label_and_direction_and_capped(self):
        given = [HudRow("chair", .6, (0, 0)), HudRow("chair", .9, (0, 0)), HudRow("chair", .8, (1, 0)),
                 HudRow("bottle", .9, (0, 0)), HudRow("tv", .7, NO_DIRECTION), HudRow("tv", .5, NO_DIRECTION)]
        self.assertEqual(select_rows(given, 10),
                         (HudRow("bottle", .9, (0, 0)), HudRow("chair", .9, (0, 0)), HudRow("chair", .8, (1, 0)),
                          HudRow("tv", .7, NO_DIRECTION)))
        self.assertEqual(select_rows(given, 2), (HudRow("bottle", .9, (0, 0)), HudRow("chair", .9, (0, 0))))
        self.assertEqual(select_rows([], 3), ())
        # A directed and a names-only row of one label and score still order without a comparison error.
        self.assertEqual(select_rows([HudRow("tv", .7, (0, 0)), HudRow("tv", .7, NO_DIRECTION)], 3),
                         (HudRow("tv", .7, NO_DIRECTION), HudRow("tv", .7, (0, 0))))

    def test_the_mask_selects_through_the_shared_helper(self):
        tree = ast.parse((ROOT / "sightcircuit/hud/mask.py").read_text())
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertLessEqual({"row_label", "select_rows"}, called)

    def test_row_record_form(self):
        self.assertEqual(HudRow("chair", .9, (-1, 1)).as_dict(),
                         {"label": "chair", "confidence": .9, "direction": [-1, 1]})
        self.assertEqual(HudRow("chair", .9, NO_DIRECTION).as_dict(),
                         {"label": "chair", "confidence": .9, "direction": None})


class CameraRowsTests(unittest.TestCase):
    def test_nine_cells_over_the_full_frame(self):
        centres = {-1: .2, 0: .5, 1: .8}
        for dy, cy in centres.items():
            for dx, cx in centres.items():
                with self.subTest(dx=dx, dy=dy):
                    self.assertEqual(direction(cx, cy), (dx, dy))
        # The cell is taken over the whole frame, never per eye: a centre at
        # three quarters of the width is right of centre, not left of an eye.
        self.assertEqual(direction(.75, .5, shape=(480, 1280)), (1, 0))
        self.assertEqual(direction(.25, .5, shape=(480, 1280, 3)), (-1, 0))

    def test_cell_boundaries_at_0_4_and_0_6_belong_to_the_middle(self):
        self.assertEqual((camera.CELL_LOW, camera.CELL_HIGH), (.4, .6))
        shape = (1000, 1000)  # Centres are exact pixel values here.

        def at(cx, cy):
            return rows([Detection("bottle", .9, (cx - 5, cy - 5, cx + 5, cy + 5))], shape)[0].direction

        self.assertEqual(at(400, 500), (0, 0))
        self.assertEqual(at(399.5, 500), (-1, 0))
        self.assertEqual(at(600, 500), (0, 0))
        self.assertEqual(at(600.5, 500), (1, 0))
        self.assertEqual(at(500, 400), (0, 0))
        self.assertEqual(at(500, 399.5), (0, -1))
        self.assertEqual(at(500, 600), (0, 0))
        self.assertEqual(at(500, 600.5), (0, 1))
        self.assertEqual(at(399.5, 600.5), (-1, 1))

    def test_threshold_and_invalid_detections(self):
        given = [centred("low", .49, .5, .5), centred("edge", .5, .5, .5), centred("over", 1.01, .5, .5),
                 centred("nan", float("nan"), .5, .5), Detection("outside", .9, (-1, 0, 20, 20)),
                 Detection("past", .9, (600, 400, 641, 470)), Detection("empty", .9, (10, 10, 10, 20)),
                 Detection("inf", .9, (0, 0, float("inf"), 20)), Detection("short", .9, (0, 0, 20)),
                 Detection("", .9, (0, 0, 20, 20)), Detection("\n☃", .9, (0, 0, 20, 20)),
                 Detection(7, .9, (0, 0, 20, 20)),
                 {"label": "untyped", "confidence": 1, "box_xyxy": [10, 10, 20, 20]},
                 Detection("whole", 1, (0, 0, 640, 480))]
        self.assertEqual(rows(given), (HudRow("whole", 1.0, (0, 0)), HudRow("edge", .5, (0, 0))))
        self.assertEqual([row.label for row in rows(given, threshold=0)], ["whole", "edge", "low"])
        self.assertEqual(rows([]), ())

    def test_dedupe_cap_and_sanitising(self):
        given = [centred("chair", .8, .2, .2), centred("chair", .9, .25, .25), centred("chair", .7, .8, .2),
                 centred("W" * 100 + "\n<scene>", .99, .5, .5), centred("bottle", .6, .5, .8),
                 centred("person", .55, .5, .5)]
        self.assertEqual(rows(given),
                         (HudRow("W" * 20, .99, (0, 0)), HudRow("chair", .9, (-1, -1)), HudRow("chair", .7, (1, -1))))
        self.assertEqual(len(rows(given, config=replace(CONFIG, rows=5))), 5)
        self.assertEqual(rows(given, config=replace(CONFIG, rows=1)), (HudRow("W" * 20, .99, (0, 0)),))
        self.assertEqual(CONFIG.rows, 3)

    def test_names_only_rows_carry_no_direction_and_deduplicate_by_name(self):
        given = [centred("chair", .8, .2, .2), centred("chair", .9, .8, .8), centred("bottle", .7, .5, .5)]
        expected = (HudRow("chair", .9, NO_DIRECTION), HudRow("bottle", .7, NO_DIRECTION))
        self.assertEqual(rows(given, directions=False), expected)
        for orientation in ORIENTATIONS:  # Nothing to turn.
            self.assertEqual(rows(given, directions=False, orientation=orientation), expected)
        self.assertIsNone(NO_DIRECTION)

    def test_each_orientation_maps_image_axes_to_view_axes(self):
        # Image right, image down and the image's lower right corner, in view axes (dx right, dy down).
        expected = {"identity": ((1, 0), (0, 1), (1, 1)),
                    "flip_horizontal": ((-1, 0), (0, 1), (-1, 1)),
                    "flip_vertical": ((1, 0), (0, -1), (1, -1)),
                    "rotate_180": ((-1, 0), (0, -1), (-1, -1)),
                    "rotate_90_cw": ((0, 1), (-1, 0), (-1, 1)),
                    "rotate_90_ccw": ((0, -1), (1, 0), (1, -1)),
                    "transpose": ((0, 1), (1, 0), (1, 1)),
                    "transverse": ((0, -1), (-1, 0), (-1, -1))}
        self.assertEqual(set(expected), set(ORIENTATIONS))
        self.assertEqual(len(set(expected.values())), 8)
        for orientation, (right, down, corner) in expected.items():
            with self.subTest(orientation=orientation):
                self.assertEqual(direction(.8, .5, orientation=orientation), right)
                self.assertEqual(direction(.5, .8, orientation=orientation), down)
                self.assertEqual(direction(.8, .8, orientation=orientation), corner)
                self.assertEqual(direction(.5, .5, orientation=orientation), (0, 0))
                # Every orientation is a bijection of the nine cells.
                cells = {direction(cx, cy, orientation=orientation) for cx in (.2, .5, .8) for cy in (.2, .5, .8)}
                self.assertEqual(len(cells), 9)
        self.assertEqual(direction(.8, .5), direction(.8, .5, orientation="identity"))

    def test_invalid_arguments_are_refused(self):
        one = [centred("bottle", .9, .5, .5)]
        cases = (("hud_camera_frame_shape_invalid", dict(shape=(480,))),
                 ("hud_camera_frame_shape_invalid", dict(shape=(0, 640))),
                 ("hud_camera_frame_shape_invalid", dict(shape=(480.0, 640))),
                 ("hud_camera_frame_shape_invalid", dict(shape=None)),
                 ("hud_config_fields_invalid", dict(config=None)),
                 ("hud_config_fields_invalid", dict(config=replace(CONFIG, rows=0))),
                 ("hud_threshold_invalid", dict(threshold=float("nan"))),
                 ("hud_threshold_invalid", dict(threshold=1.5)),
                 ("hud_threshold_invalid", dict(threshold=None)),
                 ("hud_camera_directions_invalid", dict(directions=None)),
                 ("hud_camera_directions_invalid", dict(directions=1)),
                 ("hud_camera_orientation_invalid", dict(orientation="rotate_45")),
                 ("hud_camera_orientation_invalid", dict(orientation=None)),
                 ("hud_camera_orientation_invalid", dict(orientation=90)))
        for reason, options in cases:
            with self.subTest(reason=reason, options=options), self.assertRaisesRegex(ValueError, f"^{reason}$"):
                rows(one, **options)
        for detections in (iter(one), None, one * 129):
            with self.assertRaisesRegex(ValueError, "^hud_detection_budget_exceeded$"):
                rows(detections)
        with self.assertRaises(TypeError):  # The threshold is the caller's choice; none is assumed.
            camera_rows(one, SHAPE, CONFIG)

    def test_selector_imports_no_mask_calibration_or_qualification_code(self):
        text = (ROOT / "sightcircuit/hud/camera.py").read_text()
        imported = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported.update((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(("." * node.level + (node.module or ""), alias.name) for alias in node.names)
        self.assertEqual(imported, {("math", None), ("..frame", "Detection"), (".contract", "NO_DIRECTION"),
                                    (".contract", "HudConfig"), (".contract", "HudRow"),
                                    (".contract", "row_label"), (".contract", "select_rows")})
        for word in ("Calibration", "HudMask", "MappingCandidate", "Footprint", "viewports", "CaptureFormat",
                     "qualification", "candidate", "import_module", "__import__"):
            self.assertNotIn(word, text, word)
        self.assertIs(hud.camera_rows, camera_rows)


class TileTests(unittest.TestCase):
    def test_existing_tiles_are_unchanged(self):
        self.assertEqual(digest(draw_tile((), CONFIG)), EMPTY_TILE)
        self.assertEqual(digest(draw_tile(ARROW_ROWS, CONFIG)), ARROWS_TILE)

    def test_names_only_tile_is_pinned_and_draws_no_arrow_or_dot(self):
        tile = draw_tile(NAME_ROWS, CONFIG)
        self.assertEqual(tile.shape, (128, 320, 3))
        self.assertFalse(tile.flags.writeable)
        self.assertEqual(digest(tile), NAMES_TILE)
        self.assertFalse(tile[25:, :47].any(), "the marker column stays blank")
        self.assertTrue(tile[25:, 47:].any(), "the names are drawn")
        centre = draw_tile(tuple(replace(row, direction=(0, 0)) for row in NAME_ROWS), CONFIG)
        self.assertTrue(centre[25:, :47].any())
        # The names sit where a directed row's name sits: only the marker column differs.
        self.assertTrue((tile[:, 47:] == centre[:, 47:]).all())

    def test_camera_rows_render_to_the_pinned_tiles(self):
        detections = [centred("bottle", .9, .5, .5), centred("chair", .8, .2, .8), centred("person", .7, .8, .2)]
        self.assertEqual(rows(detections, directions=False), NAME_ROWS)
        self.assertEqual(digest(draw_tile(rows(detections, directions=False), CONFIG)), NAMES_TILE)
        mixed = (HudRow("bottle", .9, (1, -1)), HudRow("chair", .8, NO_DIRECTION), HudRow("person", .7, (0, 0)))
        self.assertEqual(digest(draw_tile(mixed, CONFIG)), MIXED_TILE)
        directed = rows([centred("chair", .93, .8, .8), centred("person", .81, .2, .5), centred("tv", .7, .5, .5)])
        self.assertEqual(directed, ARROW_ROWS)
        self.assertEqual(digest(draw_tile(directed, CONFIG)), ARROWS_TILE)


if __name__ == "__main__":
    unittest.main()
