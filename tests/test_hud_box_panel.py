"""The box panel's contract and renderer (work-order item B1). Synthetic boxes only; no device, no camera, no image file.

The contract, font and layout tests are standard library only. The pixel
tests need NumPy and skip without it; the strip's tile test needs OpenCV too
(the replay environment runs them all in CI). The box panel's renderer does
not use OpenCV, and its digests hold on every locked build.
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
from sightcircuit.hud.contract import (BOX_PANEL_CONFIG, HUD_CONFIG, NO_DIRECTION, BoxPanelConfig, HudRow,  # noqa: E402
                                       PanelBox, config_digest, panel_box_valid, select_boxes)
from sightcircuit.labels import COCO_CLASSES  # noqa: E402

from sightcircuit.hud import box_font  # noqa: E402
from sightcircuit.hud.box_render import (PLACEMENTS, draw_boxes, layout, name_drawn, outline_rect,  # noqa: E402
                                         outline_strips, plate_places)

try:
    import numpy as np
    DRAWING = True
except ImportError:
    DRAWING = False
try:
    import cv2  # noqa: F401
    from sightcircuit.hud.render import draw_tile
    TILES = DRAWING
except ImportError:
    TILES = False

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
# Moved once, deliberately, by item B3, which put the box panel into the generated header in the change that made
# the bridge use it: before it this was 70663bdcb8b2fecb0f653666852142b2eaf2ff342e24874b53e11c66b04f6c98.
HEADER_CONTRACT_DIGEST = "8192f6451d50b1966cca48e0f3a1b580fc2db6c0610661170a742b13b5860531"
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

# The box panel's own pins. The configuration digest covers every field of ``BoxPanelConfig``, the font's digest
# among them; the font digest covers every glyph.
BOX_PANEL_FIELDS = {
    "layout_id": "head-fixed-box-panel-v1", "width_px": 512, "height_px": 512, "max_boxes": 5, "panel_width_m": 0.9,
    "distance_m": 0.75, "x_m": 0.0, "y_m": 0.0, "opacity": 1.0, "outline_px": 2, "casing_px": 1, "min_box_px": 12,
    "name_font": "sightcircuit-cell-5x9-v1",
    "name_font_sha256": "0e0e1588623f0dca0c67a5227d1e6b5270e7d981c8311853f8585df439dc4c39",
    "name_scale": 2, "name_pad_px": 3, "name_max_px": 190, "outline_rgb": (130, 230, 70),
    "casing_rgb": (16, 16, 16), "plate_rgb": (16, 16, 16), "name_rgb": (235, 235, 235)}
BOX_PANEL_CONFIG_DIGEST = "fdf848dd3c31b8ee80f3b165b0fc997bcee34e5dfd2aa70861d1028ac3c6c9c2"
# Reference scenes, all synthetic: names and rectangles written here, never taken from a picture. Digests are of the
# RGBA bytes and are the same on every build: the renderer fills whole pixels itself and draws names from the
# project's own glyphs, so no rasteriser's version enters. Checked on Python 3.12 (NumPy 2.2.6) and on Python 3.14
# (NumPy 2.3.2), the two locked sets.
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
    "alphabet": (PanelBox("ABCDEFGHIJKLMNOP", .9, (20, 40, 60, 80)), PanelBox("QRSTUVWXYZ _-", .8, (20, 120, 60, 160)),
                 PanelBox("abcdefghijklmnop", .7, (20, 200, 60, 240)), PanelBox("qrstuvwxyz", .6, (20, 280, 60, 320)),
                 PanelBox("0123456789", .5, (20, 360, 60, 400))),
}
SCENE_DIGESTS = {
    "empty": "30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58",
    "one": "c425e67d4bd63033f4dd42c382c632a1e912ed915ef21837cdc40fd6cde05dc8",
    "edges": "2a6b96c60feaf600062d337ba4fce2fd06e9887fdac9f08c9689da2f78c5c364",
    "tiny": "0f2c013479f40095ba079fff5b82a97bbe3b22656bc15e2427ee7348d4f9db08",
    "full": "bbedf96a809826ff21061ecbb803eee5c51d6bb0d7f26b168fec02dc228d0c8c",
    "alphabet": "62c97ada9ffdc35f4d2c639b45f44961d1aa0af5312dcf8bf867405868564ecb",
}
PLATE_HEIGHT = 24  # Nine glyph rows at scale 2 and 3 pixels above and below.
BAND = 4


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


def touch(first, second):
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def area(rects):
    """Pixels covered by a few rectangles, overlaps counted once."""
    return len({(x, y) for x0, y0, x1, y1 in rects for x in range(x0, x1) for y in range(y0, y1)})


def changed_config(**fields):
    return BoxPanelConfig(**{**dataclasses.asdict(CONFIG), **fields})


class StripUnchangedTests(unittest.TestCase):
    def test_the_strip_geometry_and_its_digest_are_what_they_were(self):
        self.assertEqual(dataclasses.asdict(HUD_CONFIG), STRIP_FIELDS)
        self.assertEqual([field.name for field in dataclasses.fields(HUD_CONFIG)], list(STRIP_FIELDS))
        self.assertEqual(config_digest(HUD_CONFIG), STRIP_CONFIG_DIGEST)

    def test_the_generated_header_holds_the_box_panel_since_item_b3_and_the_strips_digest_is_unchanged(self):
        header = (ROOT / "tools/hud_contract.hpp").read_text()
        self.assertEqual(header, native.header_text())
        self.assertEqual(native.contract_digest(), HEADER_CONTRACT_DIGEST)
        self.assertIn(f'kContractDigest[] = "{HEADER_CONTRACT_DIGEST}";', header)
        self.assertIn(f'kHudConfigDigest[] = "{STRIP_CONFIG_DIGEST}";', header)
        # Item B3: the panel's name, its digest and its font's are in the header, under names of their own.
        for line in (f'constexpr char kBoxPanelLayoutId[] = "{CONFIG.layout_id}";',
                     f'constexpr char kBoxPanelConfigDigest[] = "{BOX_PANEL_CONFIG_DIGEST}";',
                     f'constexpr char kBoxPanelNameFont[] = "{box_font.FONT_ID}";'):
            self.assertEqual(header.count(line), 1, line)
        self.assertNotIn("BoxPanel", header.split("// BoxPanelConfig:", 1)[0])  # nothing of it inside the strip's block

    @unittest.skipUnless(TILES, "NumPy and OpenCV are required for the strip's tile")
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
        for name, value in BOX_PANEL_FIELDS.items():
            other = value + "x" if type(value) is str else value[:-1] + (value[-1] + 1,) if type(value) is tuple \
                else value + 1
            with self.subTest(field=name):  # Every field is in the digest.
                self.assertNotEqual(config_digest({**BOX_PANEL_FIELDS, name: other}), BOX_PANEL_CONFIG_DIGEST)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            CONFIG.width_px = 320

    def test_a_configuration_validates_itself_when_built(self):
        self.assertEqual(changed_config(), CONFIG)
        good = (dict(width_px=416, height_px=80), dict(max_boxes=16), dict(panel_width_m=1.5), dict(distance_m=3.0),
                dict(opacity=0.2), dict(x_m=-1.0, y_m=1.0), dict(name_max_px=10), dict(min_box_px=9),
                dict(layout_id="another-panel-v2"), dict(name_scale=3), dict(width_px=200, height_px=120, name_max_px=58))
        for fields in good:
            with self.subTest(fields=fields):
                self.assertEqual(changed_config(**fields).layout_id, fields.get("layout_id", CONFIG.layout_id))
        bad = (dict(width_px=513), dict(height_px=513), dict(width_px=415), dict(height_px=79), dict(width_px=8),
               dict(width_px=0), dict(height_px=-1), dict(width_px=512.0), dict(width_px=True),
               dict(max_boxes=0), dict(max_boxes=17), dict(max_boxes=None),
               dict(panel_width_m=0.2), dict(panel_width_m=1.6), dict(panel_width_m=1), dict(distance_m=0.4),
               dict(distance_m=3.5), dict(distance_m=float("nan")), dict(x_m=1.5), dict(y_m=-1.5), dict(x_m=0),
               dict(opacity=0.1), dict(opacity=1.5), dict(opacity=float("inf")), dict(opacity="1.0"),
               dict(outline_px=0), dict(casing_px=0), dict(min_box_px=8), dict(min_box_px=513),
               dict(name_scale=0), dict(name_scale=2.0), dict(name_pad_px=0), dict(name_max_px=9),
               dict(name_max_px=250),
               dict(name_font="hershey_simplex"), dict(name_font_sha256="0" * 64),
               dict(outline_rgb=(0, 0, 0)), dict(name_rgb=(1, 2)), dict(plate_rgb=(1, 2, 256)),
               dict(casing_rgb=[16, 16, 16]), dict(name_rgb=(1.0, 2, 3)), dict(outline_rgb=None),
               dict(layout_id=""), dict(layout_id="a\nb"), dict(layout_id=7))
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
                changed_config(**fields)
        with self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
            dataclasses.replace(CONFIG, width_px=8)

    def test_the_panel_fits_the_bridge_limits_of_today(self):
        # No limit of the pipe protocol has to move for item B3: the frame-size bound and the panel limits hold.
        self.assertLessEqual(max(W, H), contract.MAX_SIDE)
        self.assertEqual(CONFIG.update_bytes, 1_048_576)
        self.assertEqual(CONFIG.update_bytes, W * H * contract.RGBA_BYTES)
        self.assertEqual(CONFIG.update_bytes / (HUD_CONFIG.width_px * HUD_CONFIG.height_px * contract.RGBA_BYTES), 6.4)
        # The contract's own copy of the placement limits is the bridge's.
        self.assertEqual([(low, high) for _name, low, high in contract.BOX_PANEL_LIMITS],
                         [(low, high) for _name, low, high in overlay.PANEL_LIMITS])
        self.assertEqual([name for name, _low, _high in overlay.PANEL_LIMITS], ["width", "distance", "x", "y", "opacity"])
        self.assertEqual([name for name, _low, _high in contract.BOX_PANEL_LIMITS],
                         ["panel_width_m", "distance_m", "x_m", "y_m", "opacity"])

    def test_the_panel_is_centred_square_and_its_names_are_readable_sizes(self):
        self.assertEqual((CONFIG.x_m, CONFIG.y_m), (0.0, 0.0))
        self.assertEqual(W, H)
        across = math.degrees(2 * math.atan(CONFIG.panel_width_m / 2 / CONFIG.distance_m))
        self.assertAlmostEqual(across, 61.93, places=2)
        # Pixels per degree are fewest at the panel's centre (a flat panel): 7.45 there, 8.27 on average.
        centre = W * CONFIG.distance_m / CONFIG.panel_width_m * math.radians(1)
        self.assertAlmostEqual(centre, 7.45, places=2)
        self.assertAlmostEqual(W / across, 8.27, places=2)
        capital_px = box_font.CAP_ROWS * CONFIG.name_scale
        self.assertEqual(capital_px, 14)
        self.assertAlmostEqual(capital_px / centre, 1.88, places=2)  # Degrees, at the centre.
        self.assertAlmostEqual(5 * CONFIG.name_scale / centre, 1.34, places=2)  # A small letter's body.
        self.assertAlmostEqual(CONFIG.name_scale / centre, 0.27, places=2)  # One stroke.
        self.assertEqual(CONFIG.outline_band_px, BAND)
        self.assertAlmostEqual(BAND / centre, 0.54, places=2)
        self.assertEqual(CONFIG.name_plate_height_px, PLATE_HEIGHT)

    def test_colours_contrast_with_each_other_so_one_edge_always_contrasts_with_the_room(self):
        self.assertGreaterEqual(contrast(CONFIG.outline_rgb, CONFIG.casing_rgb), 7)
        self.assertGreaterEqual(contrast(CONFIG.name_rgb, CONFIG.plate_rgb), 7)
        # Against a white room the dark casing carries the outline, against a black room the bright core does.
        self.assertGreaterEqual(contrast(CONFIG.casing_rgb, (255, 255, 255)), 7)
        self.assertGreaterEqual(contrast(CONFIG.outline_rgb, (0, 0, 0)), 7)

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
        # Who names the panel and who uses the layout mechanism: two lists of files, each with its own words, so
        # that a file allowed one is still forbidden the other. Since item B5 this is no longer what keeps the
        # panel off a headset: a scope record that names the layout does (tests/test_scope_record.py), and the
        # words of the stopgap it replaced are gone from the list because the stopgap is gone. What the lists
        # still hold is the shape: the scope record, the manifest and the staging tool reach layouts only as
        # "one of the contract's fixed layouts" and never name the panel; one module builds a bridge command
        # line with a layout; and since B5's second part exactly one stage draws the panel, while its command
        # reaches layouts only through the contract's list and never names the panel either.
        #
        # The panel by name: its contract, font, renderer, selector and scenes, the header generator and the
        # generated header, and the bridge's table of fixed layouts, which reads the header's constants.
        panel_files = {"sightcircuit/hud/contract.py", "sightcircuit/hud/box_render.py", "sightcircuit/hud/boxes.py",
                       "sightcircuit/hud/box_font.py", "sightcircuit/hud/__init__.py", "sightcircuit/hud/native.py",
                       "sightcircuit/hud/box_scenes.py", "tools/hud_contract.hpp", "tools/overlay_frame.hpp",
                       "sightcircuit/hud/camera_stage.py",  # Since B5's second part: the stage draws the panel,
                       # and the synthetic step's driver draws its orientation scene (no camera, a record that
                       # names the panel like any other run).
                       "sightcircuit/hud/synthetic_boxes.py"}
        panel_words = ("BOX_PANEL_CONFIG", "BoxPanelConfig", "box_render", "draw_boxes", "camera_boxes", "PanelBox",
                       "head-fixed-box-panel", "box_font", "kBoxPanel", "box_scenes", "orientation_scene")
        # The panel as "the second fixed layout": the contract's list, the bridge's table and the units that read
        # a layout from it, the observed-frame client, and since item B5 the three that bind a layout to a run:
        # the scope record, the checkpoint manifest and the staging tool. None of them names the panel itself.
        layout_files = {"sightcircuit/hud/contract.py", "sightcircuit/hud/__init__.py", "sightcircuit/observed_frames.py",
                        "sightcircuit/runtime/scope.py", "sightcircuit/runtime/manifest.py",
                        "scripts/stage_scope_record.py",
                        # Since B5's second part: the stage is built for a layout and the command takes one.
                        "sightcircuit/hud/camera_stage.py", "sightcircuit/commands/camera_hud.py",
                        # Since 2026-11-24: L-D2's still panel shows any fixed layout, chosen by --layout.
                        "sightcircuit/hud/still_panel.py",
                        "tools/overlay_frame.hpp", "tools/overlay_bridge.cpp", "tools/overlay_bridge.hpp",
                        "tools/overlay_bridge_rendering.cpp", "tools/overlay_bridge_reporting.cpp"}
        layout_words = ("FIXED_LAYOUTS", "fixed_layout", "kFixedLayouts", "FixedLayout", "pinned_layout", "layout_index",
                        "layout_in_force", "--layout", "LAYOUT_ARGUMENT", "launch_arguments", "layout_bound(")
        paths = [path for folder in ("sightcircuit", "scripts", "tools")
                 for path in (ROOT / folder).rglob("*") if path.suffix in (".py", ".cpp", ".hpp", ".sh", ".json")]
        self.assertGreater(len(paths), 100)
        used = {"panel": set(), "layout": set()}
        for path in paths:
            name = path.relative_to(ROOT).as_posix()
            text = path.read_text()
            for kind, files, words in (("panel", panel_files, panel_words), ("layout", layout_files, layout_words)):
                found = [word for word in words if word in text]
                if name in files:
                    used[kind].update([name] if found else [])
                else:
                    self.assertEqual(found, [], f"{name} uses the {kind} words {found}")
        # Each list is exact: every file on it uses a word of its kind, so neither list is wider than it needs to be.
        self.assertEqual((used["panel"], used["layout"]), (panel_files, layout_files))
        # The package describes the modules but re-exports none of the box panel's names and nothing of the layout
        # mechanism, under any alias: a stage or command cannot reach either through ``sightcircuit.hud``.
        package = ast.parse((ROOT / "sightcircuit/hud/__init__.py").read_text())
        imported = {alias.name for node in ast.walk(package) if isinstance(node, (ast.ImportFrom, ast.Import))
                    for alias in node.names}
        imported |= {node.module for node in ast.walk(package) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {"BOX_PANEL_CONFIG", "BoxPanelConfig", "PanelBox", "box_render", "boxes",
                                     "box_font", "draw_boxes", "camera_boxes", "box_scenes", "orientation_scene",
                                     "FIXED_LAYOUTS", "fixed_layout", "BOX_PANEL_LIMITS", "BOX_PANEL_PIXELS",
                                     "panel_box_valid", "select_boxes", "observed_frames", "*"})
        assigned = {target.id for node in ast.walk(package) if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
        self.assertEqual(assigned, {"__all__"})  # nothing is rebound under another name either


class BoxFontTests(unittest.TestCase):
    def test_the_font_covers_exactly_what_a_label_can_hold(self):
        allowed = {chr(code) for code in range(128) if contract.row_label("x" + chr(code) + "x") == "x" + chr(code) + "x"}
        self.assertEqual(set(box_font.GLYPHS), allowed)
        self.assertEqual(len(allowed), 65)
        self.assertEqual(set(box_font.CHARACTERS), allowed)
        for label in COCO_CLASSES:
            self.assertLessEqual(set(label), allowed)

    def test_glyphs_are_fixed_cells_distinct_and_pinned(self):
        self.assertEqual((box_font.CELL_WIDTH, box_font.CELL_HEIGHT, box_font.ADVANCE, box_font.CAP_ROWS), (5, 9, 6, 7))
        for character, glyph in box_font.GLYPHS.items():
            with self.subTest(character=character):
                self.assertEqual([len(row) for row in glyph], [5] * 9)
                self.assertLessEqual(set("".join(glyph)), set("#."))
                if character != " ":
                    self.assertIn("#", "".join(glyph))
                if character.isupper() or character.isdigit():
                    self.assertIn("#", glyph[0])  # A capital and a digit fill the seven rows from the top,
                    self.assertIn("#", glyph[6])
                    self.assertEqual(glyph[7:], (".....", "....."))  # and never the descender rows.
        self.assertEqual(len(set(box_font.GLYPHS.values())), 65, "two characters share a glyph")
        self.assertEqual(box_font.FONT_SHA256, BOX_PANEL_FIELDS["name_font_sha256"])
        self.assertEqual(box_font.FONT_ID, BOX_PANEL_FIELDS["name_font"])
        for character in "gjpqy_":
            self.assertIn("#", "".join(box_font.GLYPHS[character][7:]), character)

    def test_text_width_and_cells(self):
        self.assertEqual([box_font.text_width(text) for text in ("", "a", "ab", "W" * 20)], [0, 5, 11, 119])
        self.assertEqual(box_font.text_width("bottle", 2), 70)
        cells = box_font.text_cells("Ag")
        self.assertEqual((len(cells), len(cells[0])), (9, 11))
        self.assertEqual(["".join("#" if cell else "." for cell in row) for row in cells],
                         [".###......."] + ["#...#......"] + ["#...#..####"] + ["#####.#...#"] + ["#...#.#...#"] * 2 +
                         ["#...#..####"] + ["..........#"] + [".......###."])
        self.assertEqual(box_font.text_cells(""), [[]] * 9)
        with self.assertRaises(KeyError):
            box_font.text_cells("é")

    def test_the_font_module_is_data_and_standard_library_only(self):
        tree = ast.parse((ROOT / "sightcircuit/hud/box_font.py").read_text())
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imported, {"hashlib", "json"})
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)])


class BoxPanelLayoutTests(unittest.TestCase):
    """The renderer's decisions as integer geometry: no array is needed to check where things go."""

    def one(self, rect, label="bottle", config=CONFIG):
        (placed,) = layout((PanelBox(label, .9, rect),), config)
        return placed

    def test_a_side_of_the_box_on_the_panel_edge_is_open_and_only_then(self):
        for rect, opened in (((0, 100, 80, 200), "left"), ((100, 0, 200, 80), "top"), ((400, 100, W, 200), "right"),
                             ((100, 400, 200, H), "bottom")):
            with self.subTest(side=opened):
                drawn, flags = outline_rect(rect, CONFIG)
                self.assertEqual(drawn, rect)
                self.assertEqual(dict(zip(("left", "top", "right", "bottom"), flags)),
                                 {side: side == opened for side in ("left", "top", "right", "bottom")})
                casing, core = outline_strips(drawn, flags, CONFIG)
                self.assertEqual((len(casing), len(core)), (3, 3))
        self.assertEqual(outline_strips((0, 100, 80, 200), (True, False, False, False), CONFIG), (
            ((76, 100, 80, 200), (0, 100, 80, 104), (0, 196, 80, 200)),
            ((77, 101, 79, 199), (0, 101, 79, 103), (0, 197, 79, 199))))  # Top and bottom run out to the edge.
        # A tiny box moved inwards keeps its sides, also where it then touches the panel's edge.
        self.assertEqual(outline_rect((3, 300, 4, 301), CONFIG), ((0, 295, 12, 307), (False,) * 4))
        self.assertEqual(self.one((3, 300, 4, 301)).casing[0], (0, 295, 4, 307))
        whole = self.one((0, 0, W, H), "a")
        self.assertEqual((whole.casing, whole.core, whole.placement, whole.plate), ((), (), "inside", (0, 0, 16, 24)))

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

    def test_name_is_whole_or_cut_to_its_longest_prefix(self):
        widest = max(COCO_CLASSES, key=len)
        self.assertEqual((len(widest), box_font.text_width(widest, CONFIG.name_scale)), (14, 166))
        for label in COCO_CLASSES:  # Every class name of the detectors' vocabulary is drawn whole.
            self.assertEqual(name_drawn(label, CONFIG), label)
        self.assertEqual(name_drawn("W" * 20, CONFIG), "W" * 16)
        self.assertEqual(box_font.text_width("W" * 16, 2), CONFIG.name_max_px)
        self.assertEqual(name_drawn("WWWWWWWWWWWWWWW W W W", CONFIG), "WWWWWWWWWWWWWWW")  # No trailing space.
        self.assertEqual(name_drawn("bottle", CONFIG, 46), "bott")
        self.assertEqual(name_drawn("bottle", CONFIG, 0), "b")  # Never empty.
        self.assertEqual(name_drawn("bottle", CONFIG, 10_000), "bottle")

    def test_plate_placement_order(self):
        self.assertEqual(PLACEMENTS, ("above", "below", "above_right", "below_right", "right", "left", "inside"))
        width = 70 + 2 * CONFIG.name_pad_px  # "bottle".
        places = plate_places((200, 150, 300, 400), (False,) * 4, "bottle", CONFIG)
        self.assertEqual(places, (
            ("above", "bottle", (200, 126, 276, 150)), ("below", "bottle", (200, 400, 276, 424)),
            ("above_right", "bottle", (224, 126, 300, 150)), ("below_right", "bottle", (224, 400, 300, 424)),
            ("right", "bottle", (300, 150, 376, 174)), ("left", "bottle", (124, 150, 200, 174)),
            ("inside", "bottle", (204, 154, 280, 178))))
        self.assertEqual(self.one((200, 150, 300, 400)).plate, (200, 126, 200 + width, 150))
        # Exactly enough room above; one row less and the plate goes below, never onto the box.
        self.assertEqual(self.one((200, PLATE_HEIGHT, 300, 400)).plate, (200, 0, 276, PLATE_HEIGHT))
        below = self.one((200, PLATE_HEIGHT - 1, 300, 400))
        self.assertEqual((below.placement, below.plate), ("below", (200, 400, 276, 424)))
        # The reviewer's case: a small box near the top keeps its whole outline.
        small = self.one((200, 22, 240, 40))
        self.assertEqual((small.placement, small.plate), ("below", (200, 40, 276, 64)))
        # No room above or below: beside it, on the right, then on the left.
        tall = self.one((200, 10, 300, 500))
        self.assertEqual((tall.placement, tall.plate), ("right", (300, 10, 376, 34)))
        tall_right = self.one((450, 10, 500, 500))
        self.assertEqual((tall_right.placement, tall_right.plate), ("left", (374, 10, 450, 34)))
        # Near the right edge the plate moves left along the box until it ends at the panel's edge.
        self.assertEqual(self.one((W - 20, 150, W, 400)).plate, (W - width, 126, W, 150))
        # Too tall for above and below and too wide for beside: inside, in the upper left corner, within the band.
        big = self.one((60, 10, 470, 500))
        self.assertEqual((big.placement, big.plate), ("inside", (64, 14, 140, 38)))
        narrow = self.one((197, 5, 315, 500), "W" * 20)  # 16 W fit beside it on the right, just.
        self.assertEqual((narrow.placement, narrow.text, narrow.plate), ("right", "W" * 16, (315, 5, 511, 29)))
        squeezed = self.one((195, 5, 317, 500), "W" * 20)  # No side has room: inside, the name cut to the inside.
        self.assertEqual((squeezed.placement, squeezed.text, squeezed.plate), ("inside", "W" * 9, (199, 9, 311, 33)))

    def test_a_plate_never_touches_its_own_outline_and_is_always_on_the_panel(self):
        # Every place offered for every small box anywhere near an edge or a corner, and a sample of the middle,
        # for a short and the longest name: on the panel, and clear of each strip of the box's own outline. So a
        # lone box always shows every pixel of its core, and at least CORE_MINIMUM of them.
        core_minimum = 40  # A smallest box in a corner of the panel: two sides, each 2 by 11, sharing 2 by 2.
        sizes = ((1, 1), (5, 3), (12, 12), (18, 23), (40, 18), (60, 24), (200, 9))
        near = sorted({*range(0, 34), *range(W - 34, W), *range(150, 330, 7), *range(34, W - 34, 61)})
        checked = 0
        for width, height in sizes:
            for label in ("tv", "W" * 20):
                for y0 in near:
                    for x0 in near:
                        if x0 + width > W or y0 + height > H:
                            continue
                        rect, opened = outline_rect((x0, y0, x0 + width, y0 + height), CONFIG)
                        casing, core = outline_strips(rect, opened, CONFIG)
                        places = plate_places(rect, opened, label, CONFIG)
                        self.assertTrue(places, (x0, y0, width, height))
                        for _name, text, plate in places:
                            self.assertTrue(0 <= plate[0] < plate[2] <= W and 0 <= plate[1] < plate[3] <= H)
                            self.assertEqual(plate[2] - plate[0], box_font.text_width(text, 2) + 6)
                            self.assertFalse(any(touch(plate, strip) for strip in casing), (rect, plate))
                        checked += 1
                        if (x0 % 17, y0 % 13) == (0, 0):
                            self.assertGreaterEqual(area(core), core_minimum, (rect, opened))
        self.assertGreater(checked, 80_000)
        corner = outline_strips(*outline_rect((0, 0, 3, 3), CONFIG), CONFIG)[1]
        self.assertEqual(area(corner), core_minimum)

    def test_a_small_panel_and_every_box_in_its_top_rows(self):
        # Exhaustive on a panel small enough to try every small box: all positions, several sizes.
        small = changed_config(width_px=200, height_px=120, name_max_px=58)
        for width, height in ((1, 1), (12, 12), (30, 20), (64, 23), (64, 24)):
            for y0 in range(0, 120 - height + 1):
                for x0 in range(0, 200 - width + 1):
                    rect, opened = outline_rect((x0, y0, x0 + width, y0 + height), small)
                    casing, _core = outline_strips(rect, opened, small)
                    places = plate_places(rect, opened, "dining table", small)
                    self.assertTrue(places, (x0, y0))
                    plate = places[0][2]
                    self.assertTrue(0 <= plate[0] < plate[2] <= 200 and 0 <= plate[1] < plate[3] <= 120)
                    self.assertFalse(any(touch(plate, strip) for strip in casing))

    def test_a_later_plate_avoids_earlier_plates_and_other_outlines(self):
        # Two boxes sharing an upper left corner: both names are whole.
        first, second = layout((PanelBox("bottle", .9, (100, 100, 200, 200)), PanelBox("vase", .8, (100, 100, 180, 260))),
                               CONFIG)
        self.assertEqual((first.placement, first.plate), ("above", (100, 76, 176, 100)))
        self.assertEqual((second.placement, second.plate), ("below", (100, 260, 152, 284)))
        # A plate that would cover a small box's outline goes elsewhere.
        table, cup = layout((PanelBox("dining table", .9, (100, 100, 400, 300)), PanelBox("cup", .8, (105, 80, 125, 98))),
                            CONFIG)
        self.assertEqual((table.placement, cup.placement), ("below", "above"))
        self.assertFalse(any(touch(table.plate, strip) for strip in cup.casing))
        self.assertFalse(touch(table.plate, cup.plate))
        # Five boxes on one corner use five different places, none on another's plate or outline.
        stack = layout(tuple(PanelBox(name, .9 - index / 10, (200, 200, 300, 300))
                             for index, name in enumerate(("a", "b", "c", "d", "e"))), CONFIG)
        self.assertEqual([box.placement for box in stack], ["above", "below", "above_right", "below_right", "right"])
        for index, box in enumerate(stack):
            for other in stack[:index]:
                self.assertFalse(touch(box.plate, other.plate))
        # When no place is clear of other outlines, a place clear of earlier plates is still taken.
        crowded = layout((PanelBox("a", .9, (100, 100, 400, 400)), PanelBox("b", .8, (0, 0, W, H)),
                          PanelBox("W" * 16, .7, (90, 90, 410, 410)), PanelBox("W" * 16, .6, (80, 80, 420, 420))), CONFIG)
        for index, box in enumerate(crowded):
            for other in crowded[:index]:
                self.assertFalse(touch(box.plate, other.plate), (box, other))
            self.assertFalse(any(touch(box.plate, strip) for strip in box.casing))

    def test_layouts_keep_own_outlines_clear_for_random_scenes(self):
        import random
        generator = random.Random(106)
        names = ("tv", "bottle", "dining table", "W" * 20, "cell phone")
        for _ in range(3000):
            boxes = []
            for index in range(generator.randint(1, CONFIG.max_boxes)):
                x0, y0 = generator.randint(0, W - 1), generator.randint(0, H - 1)
                x1 = generator.randint(x0 + 1, min(W, x0 + generator.choice((3, 30, 200, W))))
                y1 = generator.randint(y0 + 1, min(H, y0 + generator.choice((3, 30, 200, H))))
                boxes.append(PanelBox(generator.choice(names), 1 - index / 10, (x0, y0, x1, y1)))
            placed = layout(boxes, CONFIG)
            self.assertEqual(len(placed), len(boxes))
            for box in placed:
                self.assertIn(box.placement, PLACEMENTS)
                self.assertTrue(0 <= box.plate[0] < box.plate[2] <= W and 0 <= box.plate[1] < box.plate[3] <= H)
                self.assertFalse(any(touch(box.plate, strip) for strip in box.casing), (boxes, box))

    def test_invalid_input_is_refused(self):
        box = PanelBox("bottle", .9, (10, 10, 50, 50))
        for function in (layout, draw_boxes) if DRAWING else (layout,):
            with self.assertRaisesRegex(ValueError, "box_panel_box_budget_exceeded"):
                function((box,) * (CONFIG.max_boxes + 1), CONFIG)
            with self.assertRaisesRegex(ValueError, "box_panel_box_budget_exceeded"):
                function(iter((box,)), CONFIG)
            for bad in (PanelBox("bottle", .9, (10, 10, 50, H + 1)), PanelBox("bott\nle", .9, (10, 10, 50, 50)),
                        PanelBox("bottle", .9, (10.0, 10, 50, 50)), HudRow("bottle", .9, None), None,
                        {"label": "bottle", "confidence": .9, "box_xyxy": (10, 10, 50, 50)}):
                with self.subTest(box=bad), self.assertRaisesRegex(ValueError, "box_panel_box_invalid"):
                    function((box, bad), CONFIG)
            for config in (HUD_CONFIG, None, dataclasses.asdict(CONFIG)):
                with self.subTest(config=config), self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
                    function((box,), config)

    def test_the_renderer_takes_boxes_and_a_config_and_imports_no_frame_camera_or_rasteriser_code(self):
        self.assertEqual(list(inspect.signature(draw_boxes).parameters), ["boxes", "config"])
        text = (ROOT / "sightcircuit/hud/box_render.py").read_text()
        imported = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported.update((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(("." * node.level + (node.module or ""), alias.name) for alias in node.names)
        self.assertEqual(imported, {("numpy", None), ("dataclasses", "dataclass"), (".", "box_font"),
                                    (".contract", "BoxPanelConfig"), (".contract", "panel_box_valid")})
        for word in ("cv2", "putText", "PIL", "freetype"):
            self.assertNotIn(word, text, word)


@unittest.skipUnless(DRAWING, "NumPy is required for the box panel's pixels")
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
        self.assertFalse(image[y0 + 4:y1 - 4, x0 + 4:x1 - 4].any(), "the inside of a box is transparent")
        # Nothing is drawn outside the box except its name plate, which sits on the top left corner.
        (placed,) = layout(SCENES["one"], CONFIG)
        plate = placed.plate
        self.assertEqual((plate[0], plate[3]), (x0, y0))
        outside = np.array(image)
        outside[y0:y1, x0:x1] = 0
        outside[plate[1]:plate[3], plate[0]:plate[2]] = 0
        self.assertFalse(outside.any())
        tag = image[plate[1]:plate[3], plate[0]:plate[2]]
        self.assertEqual({tuple(int(part) for part in colour) for colour in np.unique(tag.reshape(-1, 4), axis=0)},
                         {PLATE, NAME})
        # The name's pixels are the font's cells at the scale, inside the padding.
        cells = np.array(box_font.text_cells("bottle"), dtype=bool).repeat(2, axis=0).repeat(2, axis=1)
        self.assertTrue(((tag[3:21, 3:73] == NAME).all(axis=-1) == cells).all())
        self.assertTrue((tag[:3] == PLATE).all() and (tag[21:] == PLATE).all() and (tag[:, :3] == PLATE).all())

    def test_open_sides_as_pixels(self):
        image = draw_boxes((PanelBox("a", .9, (0, 100, 80, 200)),), CONFIG)
        self.assertEqual(pixel(image, 0, 150), TRANSPARENT)  # The left side is open,
        self.assertEqual([pixel(image, 0, 199 - depth) for depth in range(4)], [CASING, CORE, CORE, CASING])  # and
        self.assertEqual(pixel(image, 79, 150), CASING)  # the bottom runs out to the edge.
        whole = draw_boxes((PanelBox("a", .9, (0, 0, W, H)),), CONFIG)
        clear = np.array(whole)
        clear[0:24, 0:16] = 0
        self.assertFalse(clear.any(), "a box that fills the panel is its name alone")
        tiny = draw_boxes((PanelBox("a", .9, (3, 300, 4, 301)),), CONFIG)
        self.assertEqual(pixel(tiny, 0, 301), CASING)  # A tiny box moved to the edge keeps that side.

    def test_a_lone_box_always_shows_its_whole_core(self):
        # Pixels, for a sample of the boxes the geometric sweep covers: the bright core drawn is the core laid out.
        for rect in ((200, 22, 240, 40), (0, 0, 3, 3), (509, 509, 512, 512), (3, 300, 4, 301), (200, 0, 240, 20),
                     (60, 10, 470, 500), (195, 5, 317, 500), (250, 250, 253, 252), (0, 100, 30, 110),
                     *((x, y, x + 20, y + 15) for x in range(0, 480, 53) for y in range(0, 60, 7))):
            with self.subTest(rect=rect):
                for label in ("tv", "W" * 20):
                    boxes = (PanelBox(label, .9, rect),)
                    (placed,) = layout(boxes, CONFIG)
                    image = draw_boxes(boxes, CONFIG)
                    self.assertEqual(int((image == CORE).all(axis=-1).sum()), area(placed.core))
                    self.assertGreaterEqual(area(placed.core), 40)

    def test_the_first_box_is_on_top(self):
        first, second = PanelBox("bottle", .9, (100, 100, 200, 200)), PanelBox("chair", .8, (150, 60, 250, 160))
        image = draw_boxes((first, second), CONFIG)
        self.assertEqual(pixel(image, 151, 101), CORE)  # The first box's top side over the second box's left side.
        self.assertEqual(pixel(image, 150, 101), CORE)
        swapped = draw_boxes((second, first), CONFIG)
        self.assertEqual(pixel(swapped, 150, 101), CASING)  # Now the second box's left casing is on top.
        self.assertNotEqual(digest(image), digest(swapped))
        cut = (PanelBox("W" * 20, .9, (100, 100, 200, 200)),)
        self.assertEqual(digest(draw_boxes(cut, CONFIG)),
                         digest(draw_boxes((PanelBox("W" * 16, .9, (100, 100, 200, 200)),), CONFIG)))

    def test_another_panel_size_draws_by_the_same_rules(self):
        small = changed_config(width_px=200, height_px=120, name_max_px=58)
        image = draw_boxes((PanelBox("bottle", .9, (150, 60, 200, 120)),), small)
        self.assertEqual(image.shape, (120, 200, 4))
        self.assertEqual(pixel(image, 150, 90), CASING)
        self.assertEqual(pixel(image, 199, 90), TRANSPARENT)
        self.assertEqual(pixel(image, 175, 119), TRANSPARENT)
        (placed,) = layout((PanelBox("bottle", .9, (150, 60, 200, 120)),), small)
        self.assertEqual((placed.text, placed.placement, placed.plate), ("bottl", "above", (136, 36, 200, 60)))


if __name__ == "__main__":
    unittest.main()
