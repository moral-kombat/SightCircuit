"""The box panel's contract and renderer (work-order item B1). Synthetic boxes only; no device, no camera, no image file.

The contract tests are standard library only. The renderer tests need NumPy
and OpenCV and skip without them (the replay environment runs them in CI).
"""
import ast
import dataclasses
import hashlib
import inspect
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit import overlay  # noqa: E402
from sightcircuit.hud import contract, native  # noqa: E402
from sightcircuit.hud.contract import (BOX_PANEL_CONFIG, HUD_CONFIG, NO_DIRECTION, HudRow, PanelBox,  # noqa: E402
                                       config_digest, panel_box_valid, select_boxes)
from sightcircuit.labels import COCO_CLASSES  # noqa: E402

try:
    import cv2  # noqa: F401
    import numpy as np
    from sightcircuit.hud import box_render
    from sightcircuit.hud.box_render import draw_boxes, name_drawn, outline_rect, tag_rect
    from sightcircuit.hud.render import draw_tile
    DRAWING = True
except ImportError:
    DRAWING = False

CONFIG = BOX_PANEL_CONFIG
W, H = CONFIG.width_px, CONFIG.height_px
TRANSPARENT = (0, 0, 0, 0)
CORE, CASING = (*CONFIG.outline_rgb, 255), (*CONFIG.casing_rgb, 255)
PLATE, NAME = (*CONFIG.plate_rgb, 255), (*CONFIG.name_rgb, 255)

# The strip as it was on main before the box panel existed (commit c9d6f2a): its fields, its configuration digest,
# the generated header's digest and the four tile digests of tests/test_hud_camera_rows.py. Never re-pin these to
# make a change pass: a change here is a change to the strip.
STRIP_FIELDS = {"layout_id": "fixed-edge-hud-v1", "width_px": 320, "height_px": 128, "rows": 3, "panel_width_m": 0.32,
                "distance_m": 1.25, "x_m": 0.55, "y_m": -0.35, "opacity": 1.0, "inference_max_side": 960}
STRIP_CONFIG_DIGEST = "82f3f630a37c07eda5094ccc38e99b2e022ba732003fb1a76ee4cc9e60d5f392"
HEADER_CONTRACT_DIGEST = "70663bdcb8b2fecb0f653666852142b2eaf2ff342e24874b53e11c66b04f6c98"
STRIP_TILES = {
    "empty": ((), "fb77fdb76eb6451cee6fed944e8f380ded14ef2c5ae114e153c5668ceb425f53"),
    "arrows": ((HudRow("chair", .93, (1, 1)), HudRow("person", .81, (-1, 0)), HudRow("tv", .7, (0, 0))),
               "520d63cbbf0f545f24edd351538c21d57c2bbbf9c6f4a959a726ce84883a4b9b"),
    "names": ((HudRow("bottle", .9, NO_DIRECTION), HudRow("chair", .8, NO_DIRECTION),
               HudRow("person", .7, NO_DIRECTION)),
              "705418f6bafaa9ee519002f65d839e9abd0e414d3ae48110f88ebb413fbe197d"),
    "mixed": ((HudRow("bottle", .9, (1, -1)), HudRow("chair", .8, NO_DIRECTION), HudRow("person", .7, (0, 0))),
              "f75ed443682e2c7deba25c52bd63dcdc2c24f85b82c88505f75f5ce3f0c63352"),
}

# The box panel's own pins. The configuration digest covers every field of ``BoxPanelConfig``.
BOX_PANEL_FIELDS = {
    "layout_id": "head-fixed-box-panel-v1", "width_px": 512, "height_px": 512, "max_boxes": 5, "panel_width_m": 0.9,
    "distance_m": 0.75, "x_m": 0.0, "y_m": 0.0, "opacity": 1.0, "outline_px": 2, "casing_px": 1, "min_box_px": 12,
    "name_font": "hershey_simplex", "name_font_scale": 0.5, "name_thickness_px": 1, "name_pad_px": 3,
    "name_max_px": 192, "outline_rgb": (130, 230, 70), "casing_rgb": (16, 16, 16), "plate_rgb": (16, 16, 16),
    "name_rgb": (235, 235, 235)}
BOX_PANEL_CONFIG_DIGEST = "859e4a0d05b0bf98a198b46f8917ddcef47ee9d4eac2290798985c5a54306908"
# Reference scenes, all synthetic: names and rectangles written here, never taken from a picture. Digests are of the
# RGBA bytes on the locked OpenCV build (4.12 on Python 3.12).
SCENES = {
    "empty": (),
    "one": (PanelBox("bottle", .9, (200, 150, 300, 400)),),
    "edges": (PanelBox("chair", .9, (0, 0, 120, 200)), PanelBox("person", .8, (400, 300, 512, 512)),
              PanelBox("tv", .7, (480, 40, 512, 90))),
    "tiny": (PanelBox("cup", .9, (250, 250, 253, 252)), PanelBox("mouse", .8, (510, 3, 512, 5)),
             PanelBox("fork", .7, (0, 508, 2, 512))),
    "full": (PanelBox("bottle", .95, (64, 64, 277, 384)), PanelBox("dining table", .9, (40, 300, 470, 500)),
             PanelBox("person", .85, (300, 20, 450, 330)), PanelBox("cell phone", .8, (310, 180, 350, 250)),
             PanelBox("W" * 20, .75, (430, 400, 500, 440))),
}
SCENE_DIGESTS = {
    "empty": "30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58",
    "one": "6e392eafac723a27d05fb395321a84b687465afd4242d5a64589a0755863f7e5",
    "edges": "a86b146456d228f7bb21e9bdedb73b8e30242de1138d8889fcdde66d62acf65a",
    "tiny": "9bdcbbb441ad8a8fb720e5e15b9281a1fb9de81893ae5cd08cb6437eade8ffed",
    "full": "fe1e41c2f5b701573d21fc68f8f08f096e79ddf1025c9cc789ecb8515b54e330",
}


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def pixel(image, x, y):
    return tuple(int(value) for value in image[y, x])


def luminance(rgb):
    """WCAG relative luminance of an sRGB colour."""
    parts = [value / 255 for value in rgb]
    return sum(weight * (part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4)
               for weight, part in zip((0.2126, 0.7152, 0.0722), parts))


def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class StripUnchangedTests(unittest.TestCase):
    def test_the_strip_geometry_and_its_digest_are_what_they_were(self):
        self.assertEqual(dataclasses.asdict(HUD_CONFIG), STRIP_FIELDS)
        self.assertEqual([field.name for field in dataclasses.fields(HUD_CONFIG)], list(STRIP_FIELDS))
        self.assertEqual(config_digest(HUD_CONFIG), STRIP_CONFIG_DIGEST)

    def test_the_generated_header_is_unchanged_and_does_not_hold_the_box_panel(self):
        header = (ROOT / "tools/hud_contract.hpp").read_text()
        self.assertEqual(header, native.header_text())
        self.assertEqual(native.contract_digest(), HEADER_CONTRACT_DIGEST)
        self.assertIn(f'kContractDigest[] = "{HEADER_CONTRACT_DIGEST}";', header)
        self.assertIn(f'kHudConfigDigest[] = "{STRIP_CONFIG_DIGEST}";', header)
        for word in ("BoxPanel", "box-panel", "box_panel", CONFIG.layout_id):
            self.assertNotIn(word, header, word)

    @unittest.skipUnless(DRAWING, "NumPy and OpenCV are required for the tile")
    def test_the_strip_renderer_draws_the_pinned_tiles(self):
        for name, (rows, expected) in STRIP_TILES.items():
            with self.subTest(tile=name):
                tile = draw_tile(rows, HUD_CONFIG)
                self.assertEqual(tile.shape, (128, 320, 3))
                self.assertEqual(digest(tile), expected)

    def test_the_strip_digest_pins_in_its_own_test_are_these(self):
        text = (ROOT / "tests/test_hud_camera_rows.py").read_text()
        for name, (_rows, expected) in STRIP_TILES.items():
            self.assertIn(f'{name.upper()}_TILE = "{expected}"', text)


class BoxPanelContractTests(unittest.TestCase):
    def test_the_geometry_is_named_versioned_and_pinned(self):
        self.assertEqual(dataclasses.asdict(CONFIG), BOX_PANEL_FIELDS)
        self.assertEqual(config_digest(CONFIG), BOX_PANEL_CONFIG_DIGEST)
        self.assertNotEqual(config_digest(CONFIG), config_digest(HUD_CONFIG))
        self.assertRegex(CONFIG.layout_id, r"^[a-z-]+-v[0-9]+$")
        for field in dataclasses.fields(CONFIG):
            value = getattr(CONFIG, field.name)
            changed = value + "x" if type(value) is str else value[:-1] + (value[-1] + 1,) if type(value) is tuple \
                else value + 1
            with self.subTest(field=field.name):
                self.assertNotEqual(config_digest(dataclasses.replace(CONFIG, **{field.name: changed})),
                                    BOX_PANEL_CONFIG_DIGEST)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            CONFIG.width_px = 320

    def test_the_panel_fits_the_bridge_limits_of_today(self):
        # No limit of the pipe protocol has to move for item B3: the frame-size bound and the panel limits hold.
        self.assertLessEqual(max(W, H), contract.MAX_SIDE)
        self.assertEqual(CONFIG.update_bytes, 1_048_576)
        self.assertEqual(CONFIG.update_bytes, W * H * contract.RGBA_BYTES)
        self.assertEqual(CONFIG.update_bytes / (HUD_CONFIG.width_px * HUD_CONFIG.height_px * contract.RGBA_BYTES), 6.4)
        limits = {name: (low, high) for name, low, high in overlay.PANEL_LIMITS}
        for name, value in (("width", CONFIG.panel_width_m), ("distance", CONFIG.distance_m), ("x", CONFIG.x_m),
                            ("y", CONFIG.y_m), ("opacity", CONFIG.opacity)):
            with self.subTest(setting=name):
                self.assertTrue(limits[name][0] <= value <= limits[name][1])

    def test_the_panel_is_centred_square_and_its_names_are_a_degree_tall(self):
        self.assertEqual((CONFIG.x_m, CONFIG.y_m), (0.0, 0.0))
        self.assertEqual(W, H)
        across = math.degrees(2 * math.atan(CONFIG.panel_width_m / 2 / CONFIG.distance_m))
        self.assertAlmostEqual(across, 61.93, places=2)
        # Pixels per degree are fewest at the panel's centre (a flat panel): 7.45 there, 8.27 on average.
        centre = W * CONFIG.distance_m / CONFIG.panel_width_m * math.radians(1)
        self.assertAlmostEqual(centre, 7.45, places=2)
        self.assertAlmostEqual(W / across, 8.27, places=2)
        cap_px = 12  # The capital height of the name's font at ``name_font_scale`` (checked against OpenCV below).
        self.assertGreaterEqual(cap_px / centre, 1.5)
        self.assertLessEqual((CONFIG.outline_px + 2 * CONFIG.casing_px) / centre, 0.6)

    def test_colours_contrast_with_each_other_so_one_edge_always_contrasts_with_the_room(self):
        self.assertGreaterEqual(contrast(CONFIG.outline_rgb, CONFIG.casing_rgb), 7)
        self.assertGreaterEqual(contrast(CONFIG.name_rgb, CONFIG.plate_rgb), 7)
        # Against a white room the dark casing carries the outline, against a black room the bright core does.
        self.assertGreaterEqual(contrast(CONFIG.casing_rgb, (255, 255, 255)), 7)
        self.assertGreaterEqual(contrast(CONFIG.outline_rgb, (0, 0, 0)), 7)
        for colour in (CONFIG.outline_rgb, CONFIG.casing_rgb, CONFIG.plate_rgb, CONFIG.name_rgb):
            self.assertTrue(any(colour))  # Never the transparent pixel's RGB.

    def test_panel_box_rule(self):
        good = PanelBox("dining table", .5, (0, 0, W, H))
        self.assertTrue(panel_box_valid(good, CONFIG))
        self.assertEqual(good.as_dict(), {"label": "dining table", "confidence": .5, "box_xyxy": [0, 0, W, H]})
        bad = [PanelBox("", .5, (0, 0, 1, 1)), PanelBox("a\n", .5, (0, 0, 1, 1)), PanelBox(" a", .5, (0, 0, 1, 1)),
               PanelBox("a" * 21, .5, (0, 0, 1, 1)), PanelBox("a", 1.1, (0, 0, 1, 1)), PanelBox("a", -.1, (0, 0, 1, 1)),
               PanelBox("a", float("nan"), (0, 0, 1, 1)), PanelBox("a", True, (0, 0, 1, 1)),
               PanelBox("a", .5, [0, 0, 1, 1]), PanelBox("a", .5, (0, 0, 1)), PanelBox("a", .5, (0., 0, 1, 1)),
               PanelBox("a", .5, (1, 0, 1, 1)), PanelBox("a", .5, (0, 2, 1, 1)), PanelBox("a", .5, (-1, 0, 1, 1)),
               PanelBox("a", .5, (0, 0, W + 1, 1)), PanelBox("a", .5, (0, 0, 1, H + 1)),
               PanelBox("a", .5, (False, 0, 1, 1)), HudRow("a", .5, None), None]
        for box in bad:
            with self.subTest(box=box):
                self.assertFalse(panel_box_valid(box, CONFIG))

    def test_select_boxes_orders_dedupes_and_caps(self):
        boxes = [PanelBox("b", .5, (0, 0, 9, 9)), PanelBox("a", .5, (0, 0, 9, 9)), PanelBox("a", .5, (0, 0, 8, 9)),
                 PanelBox("z", .9, (1, 1, 2, 2)), PanelBox("a", .5, (0, 0, 9, 9)), PanelBox("c", .1, (0, 0, 9, 9))]
        expected = (boxes[3], boxes[2], boxes[1], boxes[0], boxes[5])
        self.assertEqual(select_boxes(boxes, 5), (expected, 1, 0))
        self.assertEqual(select_boxes(boxes[::-1], 5), (expected, 1, 0))
        self.assertEqual(select_boxes(boxes, 2), (expected[:2], 1, 3))
        self.assertEqual(select_boxes((), 5), ((), 0, 0))

    def test_nothing_selects_the_box_panel(self):
        # Items B3 and B5 are where a bridge mode and a command take the geometry; until then only the contract,
        # the renderer and the selector may name it.
        allowed = {"sightcircuit/hud/contract.py", "sightcircuit/hud/box_render.py", "sightcircuit/hud/boxes.py",
                   "sightcircuit/hud/__init__.py"}
        words = ("BOX_PANEL_CONFIG", "BoxPanelConfig", "box_render", "draw_boxes", "camera_boxes", "PanelBox",
                 "head-fixed-box-panel")
        paths = [path for folder in ("sightcircuit", "scripts", "tools")
                 for path in (ROOT / folder).rglob("*") if path.suffix in (".py", ".cpp", ".hpp", ".sh", ".json")]
        self.assertGreater(len(paths), 100)
        for path in paths:
            name = path.relative_to(ROOT).as_posix()
            if name not in allowed:
                text = path.read_text()
                for word in words:
                    self.assertNotIn(word, text, f"{name} names {word}")
        # The package describes the modules but imports none of the box panel's names.
        package = ast.parse((ROOT / "sightcircuit/hud/__init__.py").read_text())
        imported = {alias.name for node in ast.walk(package) if isinstance(node, ast.ImportFrom) for alias in node.names}
        imported |= {node.module for node in ast.walk(package) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {"BOX_PANEL_CONFIG", "BoxPanelConfig", "PanelBox", "box_render", "boxes",
                                     "draw_boxes", "camera_boxes"})


@unittest.skipUnless(DRAWING, "NumPy and OpenCV are required for the box panel's renderer")
class BoxPanelRendererTests(unittest.TestCase):
    def test_reference_scenes_are_pinned_and_deterministic(self):
        for name, boxes in SCENES.items():
            with self.subTest(scene=name):
                image = draw_boxes(boxes, CONFIG)
                self.assertEqual(image.shape, (H, W, 4))
                self.assertEqual(image.dtype, np.uint8)
                self.assertFalse(image.flags.writeable)
                self.assertEqual(image.nbytes, CONFIG.update_bytes)
                self.assertEqual(digest(image), SCENE_DIGESTS[name])
                self.assertEqual(digest(draw_boxes(list(boxes), CONFIG)), SCENE_DIGESTS[name])

    def test_everything_not_drawn_is_fully_transparent_and_alpha_is_binary(self):
        self.assertFalse(draw_boxes((), CONFIG).any())
        for name, boxes in SCENES.items():
            with self.subTest(scene=name):
                image = draw_boxes(boxes, CONFIG)
                alpha = image[..., 3]
                self.assertTrue(np.isin(alpha, (0, 255)).all())
                self.assertFalse(image[alpha == 0].any(), "a transparent pixel has RGB 0")
                colours = {tuple(int(part) for part in colour) for colour in np.unique(image.reshape(-1, 4), axis=0)}
                self.assertLessEqual(colours, {TRANSPARENT, CORE, CASING, PLATE, NAME})
                if boxes:
                    # Outlines and names only: most of the panel stays clear, the inside of a box included.
                    self.assertGreater((alpha == 0).mean(), 0.85)

    def test_outline_is_a_bright_core_between_two_dark_lines_drawn_inwards(self):
        image = draw_boxes(SCENES["one"], CONFIG)
        x0, y0, x1, y1 = SCENES["one"][0].box_xyxy
        y = (y0 + y1) // 2
        self.assertEqual([pixel(image, x, y) for x in range(x0 - 1, x0 + 5)],
                         [TRANSPARENT, CASING, CORE, CORE, CASING, TRANSPARENT])
        self.assertEqual([pixel(image, x, y) for x in range(x1 - 5, x1 + 1)],
                         [TRANSPARENT, CASING, CORE, CORE, CASING, TRANSPARENT])
        x = (x0 + x1) // 2
        self.assertEqual([pixel(image, x, row) for row in range(y1 - 5, y1 + 1)],
                         [TRANSPARENT, CASING, CORE, CORE, CASING, TRANSPARENT])
        self.assertEqual([pixel(image, x1 - 2, row) for row in range(y0 - 1, y0 + 5)][1:],
                         [CASING, CORE, CORE, CORE, CORE])  # The right side's core turns the corner.
        self.assertEqual(pixel(image, x1 - 1, y0), CASING)
        inside = image[y0 + 4:y1 - 4, x0 + 4:x1 - 4]
        self.assertFalse(inside.any(), "the inside of a box is transparent")
        # Nothing is drawn outside the box except its name plate, which sits on the top left corner.
        plate = tag_rect(outline_rect((x0, y0, x1, y1), CONFIG)[0], "bottle", CONFIG)
        self.assertEqual((plate[0], plate[3]), (x0, y0))
        outside = np.array(image)
        outside[y0:y1, x0:x1] = 0
        outside[plate[1]:plate[3], plate[0]:plate[2]] = 0
        self.assertFalse(outside.any())
        self.assertEqual(pixel(image, plate[0], plate[1]), PLATE)
        tag = image[plate[1]:plate[3], plate[0]:plate[2]].reshape(-1, 4)
        self.assertEqual({tuple(int(part) for part in colour) for colour in np.unique(tag, axis=0)}, {PLATE, NAME})

    def test_a_side_on_the_panel_edge_is_left_open(self):
        for rect, opened in (((0, 100, 80, 200), "left"), ((100, 0, 200, 80), "top"), ((400, 100, W, 200), "right"),
                             ((100, 400, 200, H), "bottom")):
            with self.subTest(side=opened):
                drawn, flags = outline_rect(rect, CONFIG)
                self.assertEqual(drawn, rect)
                self.assertEqual(dict(zip(("left", "top", "right", "bottom"), flags)),
                                 {side: side == opened for side in ("left", "top", "right", "bottom")})
                image = draw_boxes((PanelBox("a", .9, rect),), CONFIG)
                x0, y0, x1, y1 = rect
                middle = {"left": (x0, (y0 + y1) // 2), "right": (x1 - 1, (y0 + y1) // 2),
                          "top": ((x0 + x1) // 2 + 20, y0), "bottom": ((x0 + x1) // 2, y1 - 1)}
                for side, (x, y) in middle.items():
                    self.assertEqual(pixel(image, x, y), TRANSPARENT if side == opened else CASING, side)
                # The neighbouring sides run out to the panel's edge with their core.
                if opened == "left":
                    self.assertEqual([pixel(image, 0, y1 - 1 - depth) for depth in range(4)], [CASING, CORE, CORE, CASING])
                if opened == "bottom":
                    self.assertEqual([pixel(image, x0 + depth, H - 1) for depth in range(4)], [CASING, CORE, CORE, CASING])
        whole = draw_boxes((PanelBox("a", .9, (0, 0, W, H)),), CONFIG)
        plate = tag_rect((0, 0, W, H), "a", CONFIG)
        self.assertEqual(plate[:2], (0, 0))
        clear = np.array(whole)
        clear[plate[1]:plate[3], plate[0]:plate[2]] = 0
        self.assertFalse(clear.any(), "a box that fills the panel is its name alone")

    def test_a_tiny_box_is_drawn_at_the_minimum_size_inside_the_panel(self):
        size = CONFIG.min_box_px
        self.assertEqual(outline_rect((250, 250, 253, 252), CONFIG), ((246, 245, 258, 257), (False,) * 4))
        self.assertEqual(outline_rect((100, 100, 112, 112), CONFIG)[0], (100, 100, 112, 112))
        self.assertEqual(outline_rect((100, 100, 111, 300), CONFIG)[0], (100, 100, 112, 300))
        self.assertEqual(outline_rect((510, 3, 512, 5), CONFIG), ((500, 0, 512, 12), (False, False, True, False)))
        self.assertEqual(outline_rect((0, 508, 2, 512), CONFIG), ((0, 500, 12, 512), (True, False, False, True)))
        self.assertEqual(outline_rect((3, 3, 4, 4), CONFIG)[0], (0, 0, 12, 12))
        for x in range(W):
            for width in (1, 2, 5, 11):
                if x + width <= W:
                    (x0, _y0, x1, _y1), _opened = outline_rect((x, 40, x + width, 60), CONFIG)
                    self.assertEqual(x1 - x0, size)
                    self.assertTrue(0 <= x0 <= x and x + width <= x1 <= W)
        image = draw_boxes((PanelBox("cup", .9, (250, 250, 253, 252)),), CONFIG)
        self.assertEqual(pixel(image, 246, 251), CASING)
        self.assertEqual(pixel(image, 257, 251), CASING)
        self.assertEqual(pixel(image, 251, 251), TRANSPARENT)  # Still a hollow box.
        self.assertFalse(image[249:253, 250:254].any())

    def test_name_plate_placement(self):
        text = "bottle"
        x0, y0, x1, y1 = tag_rect((200, 150, 300, 400), text, CONFIG)
        tag_w, tag_h = x1 - x0, y1 - y0
        self.assertEqual((x0, y1), (200, 150))  # Above the box, on its top left corner.
        self.assertEqual(tag_h, 12 + 5 + 2 * CONFIG.name_pad_px)
        self.assertEqual(tag_rect((200, tag_h, 300, 400), text, CONFIG)[1], 0)  # Exactly enough room above.
        self.assertEqual(tag_rect((200, tag_h - 1, 300, 400), text, CONFIG)[:2], (200, tag_h - 1))  # Inside, under the top.
        self.assertEqual(tag_rect((200, 0, 300, 400), text, CONFIG)[:2], (200, 0))
        self.assertEqual(tag_rect((W - 20, 150, W, 400), text, CONFIG)[::2], (W - tag_w, W))  # Moved left to fit.
        self.assertEqual(tag_rect((0, H - 12, 12, H), text, CONFIG), (0, H - 12 - tag_h, tag_w, H - 12))
        for label in ("a", "W" * 20, *COCO_CLASSES):
            for rect in ((0, 0, 12, 12), (W - 12, 0, W, 12), (0, H - 12, 12, H), (W - 12, H - 12, W, H), (0, 0, W, H)):
                x0, y0, x1, y1 = tag_rect(rect, name_drawn(label, CONFIG), CONFIG)
                self.assertTrue(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H, (label, rect))

    def test_a_name_that_does_not_fit_is_cut_to_its_longest_prefix(self):
        def text_width(text):
            return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, CONFIG.name_font_scale, CONFIG.name_thickness_px)[0][0]
        (_width, cap), baseline = cv2.getTextSize("Ag", cv2.FONT_HERSHEY_SIMPLEX, CONFIG.name_font_scale,
                                                  CONFIG.name_thickness_px)
        self.assertEqual((cap, baseline), (12, 5))
        self.assertEqual(box_render.FONTS, {"hershey_simplex": cv2.FONT_HERSHEY_SIMPLEX})
        for label in COCO_CLASSES:  # Every class name of the detectors' vocabulary is drawn whole.
            self.assertEqual(name_drawn(label, CONFIG), label)
            self.assertLessEqual(text_width(label), CONFIG.name_max_px)
        cut = name_drawn("W" * 20, CONFIG)
        self.assertEqual(cut, "W" * 15)
        self.assertLessEqual(text_width(cut), CONFIG.name_max_px)
        self.assertGreater(text_width(cut + "W"), CONFIG.name_max_px)
        self.assertEqual(name_drawn("WWWWWWWWWWWWWW W W W", CONFIG), "WWWWWWWWWWWWWW W")  # No trailing space.
        self.assertEqual(digest(draw_boxes((PanelBox("W" * 20, .9, (100, 100, 200, 200)),), CONFIG)),
                         digest(draw_boxes((PanelBox("W" * 15, .9, (100, 100, 200, 200)),), CONFIG)))

    def test_the_first_box_is_on_top_and_no_outline_crosses_a_name(self):
        first, second = PanelBox("bottle", .9, (100, 100, 200, 200)), PanelBox("chair", .8, (100, 60, 200, 160))
        image = draw_boxes((first, second), CONFIG)
        plate = tag_rect(first.box_xyxy, "bottle", CONFIG)
        alone = draw_boxes((first,), CONFIG)
        # The second box's left side runs through the first box's name plate, and the plate wins.
        self.assertTrue((image[plate[1]:plate[3], plate[0]:plate[2]] == alone[plate[1]:plate[3], plate[0]:plate[2]]).all())
        self.assertEqual(pixel(image, 100, 150), CASING)
        self.assertEqual(pixel(image, 150, 101), CORE)  # The first box's top side, over the second box's inside.
        swapped = draw_boxes((second, first), CONFIG)
        self.assertNotEqual(digest(image), digest(swapped))
        same = (PanelBox("bottle", .9, (100, 100, 200, 200)), PanelBox("chair", .8, (100, 100, 200, 200)))
        self.assertEqual(digest(draw_boxes(same, CONFIG)), digest(draw_boxes(same[:1], CONFIG)))

    def test_invalid_input_is_refused(self):
        box = PanelBox("bottle", .9, (10, 10, 50, 50))
        with self.assertRaisesRegex(ValueError, "box_panel_box_budget_exceeded"):
            draw_boxes((box,) * (CONFIG.max_boxes + 1), CONFIG)
        with self.assertRaisesRegex(ValueError, "box_panel_box_budget_exceeded"):
            draw_boxes(iter((box,)), CONFIG)
        for bad in (PanelBox("bottle", .9, (10, 10, 50, H + 1)), PanelBox("bott\nle", .9, (10, 10, 50, 50)),
                    PanelBox("bottle", .9, (10.0, 10, 50, 50)), HudRow("bottle", .9, None), None,
                    {"label": "bottle", "confidence": .9, "box_xyxy": (10, 10, 50, 50)}):
            with self.subTest(box=bad), self.assertRaisesRegex(ValueError, "box_panel_box_invalid"):
                draw_boxes((box, bad), CONFIG)
        for config in (HUD_CONFIG, None, dataclasses.replace(CONFIG, name_font="arial"),
                       dataclasses.replace(CONFIG, min_box_px=8), dataclasses.replace(CONFIG, outline_px=0),
                       dataclasses.replace(CONFIG, width_px=8), dataclasses.replace(CONFIG, outline_rgb=(0, 0, 0)),
                       dataclasses.replace(CONFIG, name_rgb=(1, 2)), dataclasses.replace(CONFIG, plate_rgb=(1, 2, 256)),
                       dataclasses.replace(CONFIG, name_font_scale=0)):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
                draw_boxes((box,), config)

    def test_another_panel_size_draws_by_the_same_rules(self):
        small = dataclasses.replace(CONFIG, width_px=200, height_px=120)
        image = draw_boxes((PanelBox("bottle", .9, (150, 60, 200, 120)),), small)
        self.assertEqual(image.shape, (120, 200, 4))
        self.assertEqual(pixel(image, 150, 90), CASING)
        self.assertEqual(pixel(image, 199, 90), TRANSPARENT)
        self.assertEqual(pixel(image, 175, 119), TRANSPARENT)

    def test_the_renderer_takes_boxes_and_a_config_and_imports_no_frame_or_camera_code(self):
        self.assertEqual(list(inspect.signature(draw_boxes).parameters), ["boxes", "config"])
        tree = ast.parse((ROOT / "sightcircuit/hud/box_render.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(("." * node.level + (node.module or ""), alias.name) for alias in node.names)
        self.assertEqual(imported, {("cv2", None), ("numpy", None), (".contract", "BoxPanelConfig"),
                                    (".contract", "panel_box_valid")})


if __name__ == "__main__":
    unittest.main()
