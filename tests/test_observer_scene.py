"""Observer scene-precondition statistics and the file-only ``--analyze-still`` mode; no device access."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

try:
    import cv2
except ImportError as error:  # The dependency-free discovery jobs have no OpenCV; CI's no-skip steps do.
    raise unittest.SkipTest("OpenCV is required for observer scene tests") from error
import numpy as np

from sightcircuit.hud import observation as hud_observation
from sightcircuit.hud import scene
from scripts import hud_marker_observer as observer

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = scene.SCHEMA_BUDGETS[2]
NO_REJECTION = {"bright_saturated": False, "speck_density_exceeded": False, "contour_budget_exceeded": False,
                "quad_budget_exceeded": False, "outline_budget_exceeded": False, "rejection": None}


def grey(height, width, value):
    return np.full((height, width, 3), value, np.uint8)


def banded_frame():
    """200 x 300 grey 90 with, by ``cv2.contourArea`` (polygon through pixel centres):
    20 specks of 2 x 2 (area 1), three 10 x 10 squares (81), one 30 x 30 (841), one 110 x 110 (11881)."""
    image = grey(200, 300, 90)
    for k in range(20):
        image[5:7, 5 + 12 * k:7 + 12 * k] = 255
    for x in (10, 40, 70):
        image[20:30, x:x + 10] = 255
    image[50:80, 10:40] = 255
    image[50:160, 120:230] = 255
    return image


BANDED = {
    "schema_version": 2, "scope": scene.STATISTICS_SCOPE, "width": 300, "height": 200, "pixels": 60000,
    "bright_threshold": 180, "bright_pixel_count": 13380,  # 80 + 300 + 900 + 12100
    "bright_pixel_fraction": 0.223,
    "gray_percentiles": {"p50": 90, "p90": 255, "p99": 255},  # 46620 at 90; rank 54000 is past them
    # 25 solid blobs, no holes; the twenty 2 x 2 specks fit a 5 x 5 box, the squares do not.
    "census": {"bright_components": 25, "specks": 20, "enclosed_holes": 0}, "contour_count": 25,
    "contour_scan": "complete",
    "contour_area_bands": {"below_25": 20, "from_25_below_400": 3, "from_400_below_10000": 1, "from_10000": 1},
    # Five squares are quads; only the 110 x 110 one (edges 109) is outline-sized (edges >= 32, one >= 80).
    "quad_count": 5, "outline_count": 1, "budgets": BUDGETS, "precondition": NO_REJECTION,
}


def dots(count, size, pitch, height=600, width=1000):
    """``count`` bright ``size`` x ``size`` squares, row by row at ``pitch`` pixels."""
    image = grey(height, width, 90)
    columns = (width - 2) // pitch
    for index in range(count):
        y, x = 1 + (index // columns) * pitch, 1 + (index % columns) * pitch
        image[y:y + size, x:x + size] = 255
    return image


def lines(count, height=1080, width=1920, length=7, pitch=3):
    """``count`` bright vertical lines 1 px wide and ``length`` px long: each is one contour of area 0,
    neither a speck (taller than 5 px) nor a quad."""
    image = grey(height, width, 90)
    columns = (width - 2) // pitch
    for index in range(count):
        y, x = 1 + (index // columns) * (length + 2), 1 + (index % columns) * pitch
        image[y:y + length, x] = 255
    return image


def rectangles(count, height=1080, width=1920):
    """``count`` solid 90 x 40 bright rectangles: each one outline-sized quad (edges 89 and 39)."""
    image = grey(height, width, 90)
    for index in range(count):
        y, x = 20 + (index // 15) * 60, 20 + (index % 15) * 120
        image[y:y + 40, x:x + 90] = 255
    return image


def with_markers(origins, scale=1.0, height=1080, width=1920):
    tile = hud_observation.marker_template()
    if scale != 1.0:
        tile = cv2.resize(tile, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    image = grey(height, width, 90)
    for y, x in origins:
        image[y:y + tile.shape[0], x:x + tile.shape[1]] = tile[:, :, None]
    return image


def speckled_grid():
    """600 x 1000 grey 90 with a 2 x 2 speck every 10 px: 59 x 99 = 5841 specks."""
    image = grey(600, 1000, 90)
    for y in range(5, 595, 10):
        for x in range(5, 995, 10):
            image[y:y + 2, x:x + 2] = 255
    return image


class SceneStatisticsTests(unittest.TestCase):
    def test_census_bands_quads_outlines_fraction_and_percentiles_match_hand_counts(self):
        self.assertEqual(scene.scene_statistics(banded_frame()), BANDED)
        scene.validate_scene_statistics(BANDED)

    def test_threshold_speck_and_quad_area_boundaries(self):
        # Grey 179 is not bright. A 5 x 5 blob at 180 is a speck with area 16 (no quad); a 6 x 6 blob is
        # not a speck and has area 25 (a quad); a 5 x 7 blob is not a speck but has area 24 (no quad).
        image = grey(100, 100, 179)
        image[10:15, 10:15] = 180
        image[40:46, 40:46] = 180
        image[70:75, 70:77] = 180
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["census"], {"bright_components": 3, "specks": 1, "enclosed_holes": 0})
        self.assertEqual(statistics["contour_count"], 3)
        self.assertEqual(statistics["contour_area_bands"],
                         {"below_25": 2, "from_25_below_400": 1, "from_400_below_10000": 0, "from_10000": 0})
        self.assertEqual(statistics["quad_count"], 1)
        self.assertEqual((statistics["bright_pixel_count"], statistics["bright_pixel_fraction"]), (96, 0.0096))
        self.assertEqual(statistics["gray_percentiles"], {"p50": 179, "p90": 179, "p99": 179})

    def test_nearest_rank_percentiles(self):
        # 100 pixels holding 0..99: ranks 50, 90 and 99 hold 49, 89 and 98.
        image = np.repeat(np.arange(100, dtype=np.uint8).reshape(10, 10, 1), 3, axis=2)
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["gray_percentiles"], {"p50": 49, "p90": 89, "p99": 98})
        self.assertEqual((statistics["contour_count"], statistics["bright_pixel_fraction"]), (0, 0.0))
        one = scene.scene_statistics(grey(1, 1, 200))
        self.assertEqual(one["gray_percentiles"], {"p50": 200, "p90": 200, "p99": 200})

    def assert_boundary(self, image, rejection, **counts):
        statistics = scene.scene_statistics(image)
        for key, value in counts.items():
            actual = statistics["census"][key] if key in scene.CENSUS_FIELDS else statistics[key]
            self.assertEqual(actual, value, key)
        self.assertEqual(statistics["precondition"]["rejection"], rejection)
        scene.validate_scene_statistics(statistics)
        details = {}
        if rejection is None:
            hud_observation.locate_copies(image, diagnostics=details)
            self.assertNotIn("scene_statistics", details)
        else:
            with self.assertRaisesRegex(ValueError, f"^{rejection}$"):
                hud_observation.locate_copies(image, diagnostics=details)
            self.assertEqual(details["scene_statistics"], statistics)

    def test_every_limit_is_exact_on_both_sides(self):
        cases = (
            # Saturation: exactly half the pixels bright passes, one more rejects (10 x 10 frame).
            ("half", None, {"bright_pixel_count": 50}),
            ("half_plus_one", "observer_bright_saturation", {"bright_pixel_count": 51}),
            # Speck density: 1700 per 10**6 of 600000 pixels is 1020 specks.
            ("specks_1020", None, {"specks": 1020}),
            ("specks_1021", "observer_speck_density", {"specks": 1021}),
            # Contour budget, with no specks and no quads: 4096 lines pass, 4097 reject before tracing.
            ("lines_4096", None, {"contour_count": 4096, "specks": 0, "quad_count": 0}),
            ("lines_4097", "observer_contour_budget", {"contour_count": 4097, "specks": 0}),
            # Quad budget: 10 x 10 squares are not specks; 256 pass, 257 reject.
            ("quads_256", None, {"quad_count": 256, "specks": 0}),
            ("quads_257", "observer_quad_budget", {"quad_count": 257}),
            # Outline budget: 16 outline-sized rectangles pass (and are warped), 17 reject before any warp.
            ("outlines_16", None, {"outline_count": 16}),
            ("outlines_17", "observer_outline_budget", {"outline_count": 17}),
        )
        half = grey(10, 10, 90)
        half[:5] = 255
        over = half.copy()
        over[5, 0] = 255
        frames = {"half": half, "half_plus_one": over, "specks_1020": dots(1020, 2, 4),
                  "specks_1021": dots(1021, 2, 4), "lines_4096": lines(4096), "lines_4097": lines(4097),
                  "quads_256": dots(256, 10, 12), "quads_257": dots(257, 10, 12),
                  "outlines_16": rectangles(16), "outlines_17": rectangles(17)}
        for name, rejection, counts in cases:
            with self.subTest(frame=name):
                self.assert_boundary(frames[name], rejection, **counts)

    def test_a_frame_over_several_limits_names_the_first_in_order(self):
        # 3800 lines plus 300 squares: over the contour budget (4100) and the quad budget (300).
        image = lines(3800)
        for index in range(300):
            y, x = 600 + (index // 60) * 14, 10 + (index % 60) * 14
            image[y:y + 10, x:x + 10] = 255
        statistics = scene.scene_statistics(image)
        flags = statistics["precondition"]
        self.assertEqual((statistics["contour_count"], statistics["quad_count"]), (4100, 300))
        self.assertEqual((flags["contour_budget_exceeded"], flags["quad_budget_exceeded"]), (True, True))
        self.assertEqual(flags["rejection"], "observer_contour_budget")
        with self.assertRaisesRegex(ValueError, "^observer_contour_budget$"):
            hud_observation.locate_copies(image)
        # 20 outline-sized rectangles plus 300 squares: over the quad budget (320) and the outline budget
        # (20); the quad budget comes first, in the statistics and in the observer alike.
        image = rectangles(20)
        for index in range(300):
            y, x = 600 + (index // 60) * 14, 10 + (index % 60) * 14
            image[y:y + 10, x:x + 10] = 255
        statistics = scene.scene_statistics(image)
        flags = statistics["precondition"]
        self.assertEqual((statistics["quad_count"], statistics["outline_count"]), (320, 20))
        self.assertEqual((flags["quad_budget_exceeded"], flags["outline_budget_exceeded"]), (True, True))
        self.assertEqual(flags["rejection"], "observer_quad_budget")
        details = {}
        with self.assertRaisesRegex(ValueError, "^observer_quad_budget$"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["scene_statistics"], statistics)
        # A speckled frame that is also saturated names saturation, the first check.
        saturated = speckled_grid()
        saturated[:400] = 255
        self.assertEqual(scene.scene_statistics(saturated)["precondition"]["rejection"],
                         "observer_bright_saturation")

    def test_noise_grid_still_rejects(self):
        image = speckled_grid()
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["census"], {"bright_components": 5841, "specks": 5841, "enclosed_holes": 0})
        self.assertEqual(statistics["bright_pixel_fraction"], 0.03894)  # 4 x 5841 of 600000
        self.assertEqual(statistics["gray_percentiles"], {"p50": 90, "p90": 90, "p99": 255})
        self.assertEqual(statistics["precondition"]["rejection"], "observer_speck_density")
        self.assertIs(statistics["precondition"]["contour_budget_exceeded"], True)
        details = {}
        with self.assertRaisesRegex(ValueError, "^observer_speck_density$"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["scene_statistics"], statistics)
        self.assertFalse(details["scan_complete"])

    def test_the_census_counts_exactly_the_contours_findcontours_traces(self):
        rng = np.random.default_rng(20261106)
        for trial in range(60):
            height, width = rng.integers(8, 120, size=2)
            density = rng.uniform(0.01, 0.95)
            gray = np.where(rng.random((height, width)) < density, 255, 30).astype(np.uint8)
            if trial % 3 == 0:  # Solid blocks with holes, some touching the frame edge.
                for _ in range(rng.integers(1, 5)):
                    y, x = rng.integers(0, height), rng.integers(0, width)
                    gray[y:y + 12, x:x + 12] = 255
                    gray[y + 3:y + 6, x + 3:x + 6] = 30
            with self.subTest(trial=trial):
                census = scene.scene_census(gray)
                self.assertEqual(census["contour_count"], len(scene.bright_contours(gray)))
        for gray in (np.zeros((5, 5), np.uint8), np.full((5, 5), 255, np.uint8)):
            self.assertEqual(scene.scene_census(gray)["contour_count"], len(scene.bright_contours(gray)))

    def test_outline_eligibility_edges_are_exact(self):
        # Shortest edge 31 or 32 (a 32 or 33 px tall blob, 90 wide); longest edge 79 or 80 (40 tall).
        for height, width, outlines in ((32, 90, 0), (33, 90, 1), (40, 80, 0), (40, 81, 1)):
            image = grey(300, 300, 90)
            image[50:50 + height, 50:50 + width] = 255
            with self.subTest(height=height, width=width):
                statistics = scene.scene_statistics(image)
                self.assertEqual((statistics["quad_count"], statistics["outline_count"]), (1, outlines))
                details = {}
                hud_observation.locate_copies(image, diagnostics=details)
                self.assertEqual(details["eligible_outline_count"], outlines)

    def test_one_marker_copy_is_two_outline_candidates(self):
        # MAX_OUTLINES = 16 is MAX_COPIES (4) x 2 outlines per copy x 2.
        for scale in (1.0, 1.5):
            with self.subTest(scale=scale):
                statistics = scene.scene_statistics(with_markers(((300, 400),), scale))
                self.assertEqual((statistics["contour_count"], statistics["quad_count"],
                                  statistics["outline_count"]), (17, 6, 2))
                self.assertIsNone(statistics["precondition"]["rejection"])
        four = with_markers(((100, 100), (100, 1000), (600, 100), (600, 1000)))
        statistics = scene.scene_statistics(four)
        self.assertEqual((statistics["quad_count"], statistics["outline_count"]), (24, 8))
        self.assertEqual(len(hud_observation.locate_copies(four)), 4)
        self.assertLessEqual(hud_observation.MAX_COPIES * 2, scene.MAX_OUTLINES)

    def test_statistics_are_the_observer_precondition(self):
        # The quad budget the observer applies, at the same frame, with the complete count alongside.
        image = dots(300, 10, 12)
        details = {}
        with self.assertRaisesRegex(ValueError, "observer_quad_budget"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["quad_count"], scene.MAX_QUADS + 1)
        self.assertEqual(details["scene_statistics"]["quad_count"], 300)
        # A frame that passes the precondition carries no statistics in its diagnostics.
        details = {}
        hud_observation.locate_copies(banded_frame(), diagnostics=details)
        self.assertNotIn("scene_statistics", details)
        self.assertEqual((details["contour_count"], details["eligible_outline_count"]), (25, 1))
        for name in ("MAX_CONTOURS", "MAX_QUADS", "MAX_OUTLINES"):
            self.assertIs(getattr(hud_observation, name), getattr(scene, name))

    def test_outline_budget_rejects_before_any_warp(self):
        with patch.object(hud_observation.cv2 if hasattr(hud_observation, "cv2") else cv2, "warpPerspective",
                          side_effect=AssertionError("warped")) as warp, \
                self.assertRaisesRegex(ValueError, "^observer_outline_budget$"):
            hud_observation.locate_copies(rectangles(17))
        warp.assert_not_called()

    def test_contour_budget_rejects_before_any_trace(self):
        with patch.object(cv2, "findContours", side_effect=AssertionError("traced")) as traced, \
                patch.object(scene, "STATISTICS_MAX_CONTOURS", 0), \
                self.assertRaisesRegex(ValueError, "^observer_contour_budget$"):
            hud_observation.locate_copies(lines(4097))
        traced.assert_not_called()

    def test_a_trace_that_disagrees_with_the_census_rejects_without_statistics(self):
        # Any disagreement, above the budget or one contour more on a frame that would reach the quad budget.
        for image, count in ((grey(100, 100, 90), scene.MAX_CONTOURS + 1), (dots(300, 10, 12), 301)):
            traced = scene.bright_contours(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
            traced = list(traced) + [np.zeros((1, 1, 2), np.int32)] * (count - len(traced))
            details = {}
            with self.subTest(count=count), patch.object(hud_observation, "bright_contours", return_value=traced), \
                    self.assertRaisesRegex(ValueError, "^observer_contour_census_mismatch$"):
                hud_observation.locate_copies(image, diagnostics=details)
            self.assertEqual((details["status"], details["contour_count"]), ("rejected", count))
            self.assertEqual(details["scene_statistics_error"], "census_disagrees_with_trace")
            self.assertNotIn("scene_statistics", details)

    def test_statistics_skip_tracing_above_their_own_limit(self):
        with patch.object(scene, "STATISTICS_MAX_CONTOURS", 100), \
                patch.dict(scene.SCHEMA_BUDGETS[2], statistics_max_contours=100):
            statistics = scene.scene_statistics(lines(150))
            self.assertEqual(statistics["contour_scan"], "skipped_over_statistics_limit")
            self.assertEqual((statistics["contour_area_bands"], statistics["quad_count"],
                              statistics["outline_count"]), (None, None, None))
            self.assertEqual((statistics["precondition"]["quad_budget_exceeded"],
                              statistics["precondition"]["outline_budget_exceeded"]), (None, None))
            scene.validate_scene_statistics(statistics)

    def test_array_subclasses_keep_the_budget_rejection(self):
        image = dots(1141, 2, 4)
        with tempfile.TemporaryDirectory() as temporary:
            mapped = np.memmap(Path(temporary) / "frame", dtype=np.uint8, mode="w+", shape=image.shape)
            mapped[:] = image
            class View(np.ndarray):
                pass
            for frame in (mapped, image.view(View)):
                with self.subTest(kind=type(frame).__name__):
                    details = {}
                    with self.assertRaisesRegex(ValueError, "^observer_speck_density$"):
                        hud_observation.locate_copies(frame, diagnostics=details)
                    self.assertEqual((details["status"], details["reason"]), ("rejected", "observer_speck_density"))
                    self.assertEqual(details["scene_statistics"], scene.scene_statistics(image))
            del mapped

    def test_failing_statistics_cannot_replace_the_rejection(self):
        details = {}
        with patch.object(hud_observation, "scene_statistics", side_effect=MemoryError), \
                self.assertRaisesRegex(ValueError, "^observer_quad_budget$"):
            hud_observation.locate_copies(dots(257, 10, 12), diagnostics=details)
        self.assertEqual((details["status"], details["reason"]), ("rejected", "observer_quad_budget"))
        self.assertEqual(details["scene_statistics_error"], "MemoryError")
        self.assertNotIn("scene_statistics", details)

    def test_invalid_images_are_refused(self):
        for image in (np.zeros((4, 4), np.uint8), np.zeros((4, 4, 3), np.float32), np.zeros((0, 4, 3), np.uint8),
                      np.zeros((4, 4, 4), np.uint8), [[0]]):
            with self.subTest(shape=getattr(image, "shape", None)), self.assertRaises(ValueError):
                scene.scene_statistics(image)
        with patch.object(scene, "MAX_PIXELS", 99), self.assertRaises(ValueError):
            scene.scene_statistics(grey(10, 10, 0))

    def test_the_documented_constants_are_the_code_constants(self):
        # docs/reference/hud-observation.md derives one constant per limit; a changed constant must change that table.
        text = (ROOT / "docs" / "reference" / "hud-observation.md").read_text()
        section = text[text.index("### The O3 rule (2026-11-06)"):]
        documented = {}
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0].startswith("`") and cells[0].endswith("`"):
                documented[cells[0].strip("`")] = cells[1]
        self.assertEqual(set(documented), {"MAX_BRIGHT_FRACTION", "SPECK_EXTENT_PX", "MAX_SPECKS_PER_MEGAPIXEL",
                                           "MAX_CONTOURS", "MAX_QUADS", "MAX_OUTLINES", "STATISTICS_MAX_CONTOURS"})
        for name, value in documented.items():
            with self.subTest(constant=name):
                code = getattr(scene, name)
                expected = f"{code[0]}/{code[1]}" if isinstance(code, tuple) else f"{code:,}"
                self.assertEqual(value, expected)


# The schema-1 statistics of a report stored before O3 (the speckled noise grid, as #25 reported it).
STORED_SCHEMA_1 = {
    "schema_version": 1, "scope": scene.STATISTICS_SCOPE, "width": 1000, "height": 600, "pixels": 600000,
    "bright_threshold": 180, "bright_pixel_fraction": 0.03894,
    "gray_percentiles": {"p50": 90, "p90": 90, "p99": 255}, "contour_count": 5841,
    "contour_area_bands": {"below_25": 5841, "from_25_below_400": 0, "from_400_below_10000": 0, "from_10000": 0},
    "quad_count": 0, "budgets": {"max_contours": 2048, "max_quads": 256, "min_quad_area": 25},
    "precondition": {"contour_budget_exceeded": True, "quad_budget_exceeded": False,
                     "rejection": "observer_contour_budget"}}


def forged(width, height, bright, components, specks, holes, *, bands=None, quads=0, outlines=0,
           scan="complete", percentiles=None):
    """Schema-2 statistics built from counts, consistent except where a test breaks one identity."""
    pixels, contours = width * height, components + holes
    if scan == "complete" and bands is None:
        bands = {"below_25": contours, "from_25_below_400": 0, "from_400_below_10000": 0, "from_10000": 0}
    if scan != "complete":
        quads = outlines = None
    if percentiles is None:  # Nearest rank: bright exactly when the rank falls among the bright pixels.
        percentiles = {f"p{p}": 255 if pixels - bright < max(1, -(-p * pixels // 100)) else 90
                       for p in scene.PERCENTILES}
    budgets = json.loads(json.dumps(scene.SCHEMA_BUDGETS[2]))
    return {"schema_version": 2, "scope": scene.STATISTICS_SCOPE, "width": width, "height": height,
            "pixels": pixels, "bright_threshold": 180, "bright_pixel_count": bright,
            "bright_pixel_fraction": round(bright / pixels, 6), "gray_percentiles": percentiles,
            "census": {"bright_components": components, "specks": specks, "enclosed_holes": holes},
            "contour_count": contours, "contour_scan": scan, "contour_area_bands": bands, "quad_count": quads,
            "outline_count": outlines, "budgets": budgets,
            "precondition": scene._precondition(bright, pixels, specks, contours, quads, outlines, budgets)}


class StatisticsContractTests(unittest.TestCase):
    def test_stored_schema_1_statistics_validate_against_their_own_budgets(self):
        scene.validate_scene_statistics(STORED_SCHEMA_1)
        # 3000 contours: over schema 1's 2048 though under today's 4096, so the frozen budget decides.
        between = json.loads(json.dumps(STORED_SCHEMA_1))
        between.update(contour_count=3000, contour_area_bands={"below_25": 3000, "from_25_below_400": 0,
                                                               "from_400_below_10000": 0, "from_10000": 0})
        scene.validate_scene_statistics(between)
        between["precondition"].update(contour_budget_exceeded=False, rejection=None)
        with self.assertRaises(ValueError):
            scene.validate_scene_statistics(between)
        # Under schema 1's own budgets 2049 contours exceeded 2048, though today's limit is 4096.
        for change in (lambda s: s["budgets"].update(max_contours=4096),  # forged: not schema 1's budgets
                       lambda s: s["precondition"].update(rejection="observer_speck_density"),
                       lambda s: s.update(census={"bright_components": 1, "specks": 0, "enclosed_holes": 0}),
                       lambda s: s["precondition"].update(contour_budget_exceeded=1)):  # 1 == True, but not a flag
            stored = json.loads(json.dumps(STORED_SCHEMA_1))
            change(stored)
            with self.subTest(stored=stored["budgets"]), self.assertRaises(ValueError):
                scene.validate_scene_statistics(stored)

    def test_schema_2_contract_refuses_every_inconsistency(self):
        good = scene.scene_statistics(speckled_grid())
        scene.validate_scene_statistics(good)
        changes = (
            lambda s: s.update(schema_version=3),
            lambda s: s.update(schema_version=True),
            lambda s: s.update(scope="other"),
            lambda s: s.update(bright_threshold=181),
            lambda s: s.update(width=-1000, height=-600),
            lambda s: s.update(contour_count=float(s["contour_count"])),
            lambda s: s.update(bright_pixel_count=s["pixels"] + 1),
            lambda s: s.update(bright_pixel_fraction=0.5),
            lambda s: s["census"].update(specks=s["census"]["bright_components"] + 1),
            lambda s: s["census"].update(enclosed_holes=1),  # no longer sums to contour_count
            lambda s: s["census"].update(extra=0),
            lambda s: s.update(contour_scan="partial"),
            lambda s: s.update(contour_scan="skipped_over_statistics_limit"),  # below the limit: must be complete
            lambda s: s.update(quad_count=None),
            lambda s: s.update(quad_count=1),  # every contour is below 25 px: no quad is possible
            lambda s: s.update(outline_count=1),  # more outlines than quads
            lambda s: s["contour_area_bands"].update(below_25=-1, from_25_below_400=1),
            lambda s: s["budgets"].update(max_specks_per_megapixel=99999),
            lambda s: s["budgets"].update(max_bright_fraction=[1, 1]),
            lambda s: s["precondition"].update(rejection="observer_contour_budget"),  # not the first in order
            lambda s: s["precondition"].update(speck_density_exceeded=False),
            lambda s: s["gray_percentiles"].update(p50=200, p90=100),
            lambda s: s.update(pixels_bgr=[1, 2, 3]),  # never pixel data
            lambda s: s["precondition"].update(speck_density_exceeded=1, contour_budget_exceeded=1),
            lambda s: s["precondition"].update(quad_budget_exceeded=0),
            lambda s: s["budgets"].update(statistics_max_contours=1),
        )
        for index, change in enumerate(changes):
            altered = json.loads(json.dumps(good))
            change(altered)
            with self.subTest(change=index), self.assertRaises(ValueError):
                scene.validate_scene_statistics(altered)
        # Forged statistics, each consistent but for one identity a genuine frame always satisfies.
        for name, statistics in (
                ("consistent", None),
                ("more_bright_than_pixels", forged(4000, 2000, 8_000_001, 1, 0, 0)),
                ("skipped_below_the_limit", forged(100, 100, 300, 300, 300, 0, scan="skipped_over_statistics_limit")),
                ("more_components_than_bright_pixels", forged(10, 10, 2, 5, 5, 0)),
                ("complete_above_the_statistics_limit", forged(4000, 2000, 40000, 20000, 0, 0)),
                ("holes_without_bright_pixels", forged(1000, 1000, 0, 0, 0, 5000)),
                ("bright_without_components", forged(100, 100, 50, 0, 0, 0)),
                ("more_holes_than_dark_pixels", forged(10, 10, 95, 1, 0, 6)),
                ("saturated_with_dark_median", forged(100, 100, 10000, 1, 0, 0,
                                                      percentiles={"p50": 0, "p90": 0, "p99": 0})),
                ("specks_without_small_contours", forged(1000, 1000, 50000, 2000, 2000, 0, bands={
                    "below_25": 0, "from_25_below_400": 2000, "from_400_below_10000": 0, "from_10000": 0}))):
            with self.subTest(forged=name):
                if statistics is None:
                    scene.validate_scene_statistics(forged(1000, 600, 4000, 300, 200, 10))
                else:
                    with self.assertRaises(ValueError):
                        scene.validate_scene_statistics(statistics)
        # A dark frame's fraction is 0.0; False compares equal to it but is not a fraction.
        dark = scene.scene_statistics(grey(10, 10, 0))
        scene.validate_scene_statistics(dark)
        dark["bright_pixel_fraction"] = False
        with self.assertRaises(ValueError):
            scene.validate_scene_statistics(dark)


def png_bytes(image):
    okay, data = cv2.imencode(".png", image)
    assert okay
    return data.tobytes()


def png_header(width, height):
    """A PNG signature and IHDR claiming ``width`` x ``height``, then nothing decodable."""
    body = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(body)) + b"IHDR" + body + struct.pack(">I", zlib.crc32(b"IHDR" + body))
    return b"\x89PNG\r\n\x1a\n" + chunk


class AnalyzeStillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def run_main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = observer.main(list(arguments))
        return code, json.loads(output.getvalue())

    @contextlib.contextmanager
    def no_device_or_bridge(self):
        """Fail on any discovery, capture, bridge or marker call, and record every ``os.open`` path.

        ``self.open_flags`` keeps the flags of each open, in the same order.
        """
        opened = []
        self.open_flags = []
        real_open = os.open
        def recording_open(path, flags, *args, **kwargs):
            opened.append(os.fspath(path))
            self.open_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)
        forbidden = AssertionError("device, mirror or bridge touched")
        with patch.object(observer, "discover", side_effect=forbidden), \
                patch.object(observer, "select_candidate", side_effect=forbidden), \
                patch.object(observer, "capture", side_effect=forbidden), \
                patch.object(observer, "supervise", side_effect=forbidden), \
                patch.object(observer, "MarkerClient", side_effect=forbidden), \
                patch.object(scene.os, "open", side_effect=recording_open):
            yield opened
        self.assertFalse([path for path in opened if path.startswith(("/dev", "/proc", "/sys"))], opened)

    def test_png_still_reports_exact_statistics_and_its_source(self):
        data = png_bytes(banded_frame())
        path = self.write("still.png", data)
        with self.no_device_or_bridge() as opened:
            code, report = self.run_main("--analyze-still", str(path))
        self.assertEqual(opened, [str(path)])
        # A link swapped in last cannot be followed to a device, and a FIFO swapped in cannot block the open.
        for flag in (os.O_NOFOLLOW, os.O_NONBLOCK, os.O_CLOEXEC):
            self.assertTrue(self.open_flags[0] & flag, flag)
        self.assertEqual((code, report["status"], report["exit_code"]), (0, "analyzed", 0))
        self.assertEqual(report["report_kind"], "hud_observer_scene_analysis")
        self.assertEqual(report["statistics"], BANDED)
        self.assertEqual(report["source"], {"format": "png", "lossless": True, "bytes": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest(), "width": 300, "height": 200})
        self.assertEqual(report["fidelity"], "lossless_png_origin_unverified")
        self.assertIs(report["provenance_verified"], False)
        for key in ("device_opened", "images_saved", "mapping_qualified", "production_live_input_enabled",
                    "production_hud_enabled"):
            self.assertIs(report[key], False, key)

    def test_rejecting_scene_is_still_an_analysis_not_a_failure(self):
        path = self.write("noise.png", png_bytes(dots(1141, 2, 4)))
        code, report = self.run_main("--analyze-still", str(path))
        self.assertEqual(code, 0)
        self.assertEqual(report["statistics"]["precondition"]["rejection"], "observer_speck_density")

    def test_jpeg_fill_bytes_before_a_marker_are_skipped(self):
        # FF D8, two fill bytes, then SOF0 (length 17, 8-bit, 64 high, 96 wide).
        data = b"\xff\xd8" + b"\xff\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 64, 96) + bytes(10)
        self.assertEqual(scene._header(data), ("jpeg", 96, 64))

    def test_a_decode_failure_is_a_refusal_not_a_traceback(self):
        still = self.write("still.png", png_bytes(banded_frame()))
        with patch("cv2.imdecode", side_effect=RuntimeError("boom")):
            report = scene.analyze_still(str(still))
        self.assertEqual((report["status"], report["exit_code"]), ("refused", 1))
        self.assertEqual(report["reason"], "observer_still_analysis_error:RuntimeError")

    def test_jpeg_still_is_marked_lossy(self):
        okay, data = cv2.imencode(".jpg", banded_frame(), [cv2.IMWRITE_JPEG_QUALITY, 95])
        self.assertTrue(okay)
        code, report = self.run_main("--analyze-still", str(self.write("still.jpg", data.tobytes())))
        self.assertEqual(code, 0)
        self.assertEqual((report["source"]["format"], report["source"]["lossless"]), ("jpeg", False))
        self.assertEqual((report["source"]["width"], report["source"]["height"]), (300, 200))
        self.assertEqual(report["fidelity"], "lossy_approximation_of_a_native_mirror_read")
        scene.validate_scene_statistics(report["statistics"])

    def test_a_proc_or_dev_path_is_refused_before_anything_resolves_it(self):
        for path in ("/proc/self/status", "/dev/video99", "/sys/kernel/notes", "//proc/self/status", "//dev/null",
                     "//sys/kernel/notes", "//dev/../dev/null"):
            with self.subTest(path=path), \
                    patch.object(scene.os.path, "realpath", side_effect=AssertionError("resolved")) as resolved, \
                    patch.object(scene.os, "lstat", side_effect=AssertionError("stat")) as stated:
                report = scene.analyze_still(path)
            self.assertEqual(report["reason"], "observer_still_device_path_refused")
            resolved.assert_not_called()
            stated.assert_not_called()

    def test_a_fifo_swapped_in_after_the_check_is_opened_without_blocking_and_refused(self):
        fifo = self.root / "late.png"
        os.mkfifo(fifo)
        regular = os.lstat(self.write("checked.png", png_bytes(banded_frame())))
        # lstat sees the checked regular file; the open then reaches the FIFO, which must not block.
        reports = []
        with patch.object(scene.os, "lstat", return_value=regular):
            worker = threading.Thread(target=lambda: reports.append(scene.analyze_still(fifo)), daemon=True)
            worker.start()
            worker.join(timeout=5)
        if worker.is_alive():  # Blocked in open: a writer releases it, so the failure is reported, not hung.
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            worker.join(timeout=5)
            self.fail("read_still blocked opening a FIFO")
        self.assertEqual((reports[0]["status"], reports[0]["reason"]),
                         ("refused", "observer_still_changed_while_opening"))

    def test_device_proc_sys_links_fifos_and_directories_are_refused_before_opening(self):
        target = self.write("real.png", png_bytes(banded_frame()))
        link_to_device = self.root / "camera.png"
        link_to_device.symlink_to("/dev/null")
        link_to_file = self.root / "link.png"
        link_to_file.symlink_to(target)
        fifo = self.root / "pipe.png"
        os.mkfifo(fifo)
        cases = [("/dev/null", "observer_still_device_path_refused"),
                 ("/dev/video99", "observer_still_device_path_refused"),
                 ("/dev/shm/frames.png", "observer_still_device_path_refused"),
                 ("/proc/self/status", "observer_still_device_path_refused"),
                 ("/sys/kernel/notes", "observer_still_device_path_refused"),
                 ("/dev/../dev/null", "observer_still_device_path_refused"),
                 (str(link_to_device), "observer_still_device_path_refused"),
                 (str(link_to_file), "observer_still_symlink_refused"),
                 (str(fifo), "observer_still_not_regular_file"),
                 (str(self.root), "observer_still_not_regular_file"),
                 (str(self.root / "missing.png"), "observer_still_unreadable")]
        for path, reason in cases:
            with self.subTest(path=path), self.no_device_or_bridge() as opened:
                code, report = self.run_main("--analyze-still", path)
                self.assertEqual((code, report["status"], report["exit_code"], report["reason"]),
                                 (1, "refused", 1, reason))
                self.assertEqual(opened, [])  # Refused before any open: no device node or FIFO is opened.
                self.assertNotIn("statistics", report)
                self.assertIs(report["device_opened"], False)

    def test_bytes_paths_are_checked_like_text(self):
        data = png_bytes(banded_frame())
        path = self.write("still.png", data)
        report = scene.analyze_still(os.fsencode(path))
        self.assertEqual((report["status"], report["statistics"]), ("analyzed", BANDED))
        with self.no_device_or_bridge() as opened:
            report = scene.analyze_still(b"/dev/null")
        self.assertEqual((report["status"], report["reason"]), ("refused", "observer_still_device_path_refused"))
        self.assertEqual(opened, [])

    def test_unusable_files_are_refused(self):
        cases = [(self.write("text.png", b"not an image at all"), "observer_still_format_unsupported"),
                 (self.write("empty.png", b""), "observer_still_format_unsupported"),
                 (self.write("huge.png", png_header(10000, 10000)), "observer_pixel_budget"),
                 (self.write("zero.png", png_header(0, 10)), "observer_pixel_budget"),
                 (self.write("broken.png", png_header(64, 64) + b"\x00" * 64), "observer_still_undecodable"),
                 (self.write("cut.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"), "observer_still_format_unsupported")]
        for path, reason in cases:
            with self.subTest(path=path.name):
                code, report = self.run_main("--analyze-still", str(path))
                self.assertEqual((code, report["reason"]), (1, reason))
        large = self.write("large.png", png_bytes(banded_frame()))
        with patch.object(scene, "MAX_STILL_BYTES", 100):
            code, report = self.run_main("--analyze-still", str(large))
        self.assertEqual((code, report["reason"]), (1, "observer_still_too_large"))

    def test_file_swapped_while_opening_is_refused(self):
        first = self.write("first.png", png_bytes(banded_frame()))
        second = self.write("second.png", png_bytes(banded_frame()))
        real_lstat = os.lstat
        with patch.object(scene.os, "lstat", side_effect=lambda path: real_lstat(second)):
            code, report = self.run_main("--analyze-still", str(first))
        self.assertEqual((code, report["reason"]), (1, "observer_still_changed_while_opening"))

    def test_analysis_takes_no_observation_device_bridge_or_session_option(self):
        path = str(self.write("still.png", png_bytes(banded_frame())))
        for extra in (["--observe-calibration"], ["--allow-mirror-observation"], ["--allow-calibration-output"],
                      ["--device", "/dev/video99"], ["--device", ""], ["--bridge", "bridge"],
                      ["--record-session-tokens"], ["--seconds", "20"], ["--worker", "{}"]):
            with self.subTest(extra=extra), self.no_device_or_bridge(), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                observer.main(["--analyze-still", path, *extra])
            self.assertEqual(raised.exception.code, 2)

    def test_runs_as_a_file_and_prints_one_json_report(self):
        path = self.write("still.png", png_bytes(banded_frame()))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/hud_marker_observer.py"),
                                 "--analyze-still", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["statistics"], BANDED)


if __name__ == "__main__":
    unittest.main()
