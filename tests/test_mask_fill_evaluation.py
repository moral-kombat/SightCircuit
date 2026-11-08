"""The HUD mask fill evaluation: every candidate is leak-free, and the evaluator measures the runtime's path.

Generated frames and fake detectors only; the documented real-detector run is
``scripts/evaluate_mask_fill.py``. Nothing here is Frame evidence.
"""
import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import cv2
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for the mask fill evaluation tests") from error
import numpy as np

from sightcircuit import hud
from sightcircuit import mask_fill_evaluation as fills
from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.hud.mask import fill_regions, mask_regions
from sightcircuit.runtime import synthetic
from tests.test_hud_fill import STEREO, calibrations, contaminations, scene
from tests.test_hud_mask import prepare

ROOT = Path(__file__).resolve().parents[1]


def geometry(cal):
    """``(regions, eyes, inference shape)`` of a calibration, by the runtime's own rules."""
    mask = hud.HudMask(cal, source_kind="synthetic")
    return mask_regions(cal), hud.viewports(cal.capture_format, cal.capture_layout), mask.inference_shape()


class BlackBoxDetector:
    """Boxes any pure-black pixels as ``tvmonitor``, the artifact E76 saw on the zero-filled mask."""
    name, runtime, classes = "black-box-fake", "test", ("tvmonitor", "chair")

    def __init__(self):
        self.calls = 0

    def detect(self, image, threshold):
        self.calls += 1
        ys, xs = np.nonzero((image == 0).all(axis=2))
        if not len(ys):
            return DetectionResult((), 0.0)
        return DetectionResult((Detection("tvmonitor", .8, (float(xs.min()), float(ys.min()), float(xs.max() + 1),
                                                            float(ys.max() + 1))),), 0.0)


class CornerDetector:
    """Always boxes a chair over the lower-right quarter of its input, as an object partly under the HUD would be."""
    name, runtime, classes = "corner-fake", "test", ("tvmonitor", "chair")

    def detect(self, image, threshold):
        height, width = image.shape[:2]
        return DetectionResult((Detection("chair", .9, (.75 * width, .75 * height, float(width), float(height))),),
                               0.0)


class CandidateLeakTests(unittest.TestCase):
    """Each fill kept for comparison reads nothing inside footprint plus margin, native and resized."""

    def test_region_content_never_reaches_any_candidates_input(self):
        for name, (fill, _) in fills.FILLS.items():
            for label, cal in calibrations().items():
                regions, eyes, shape = geometry(cal)
                native_shape = (cal.capture_format.height, cal.capture_format.width)
                clean = scene(*native_shape, 21)
                native = fills.masked_input(clean, regions, eyes, fill, native_shape)
                resized = fills.masked_input(clean, regions, eyes, fill, shape)
                for kind, changed in contaminations(clean, regions, 22):
                    with self.subTest(fill=name, calibration=label, content=kind):
                        np.testing.assert_array_equal(fills.masked_input(changed, regions, eyes, fill, native_shape),
                                                      native)
                        np.testing.assert_array_equal(fills.masked_input(changed, regions, eyes, fill, shape), resized)

    def test_every_pixel_of_the_fixture_region_is_ignored_by_every_candidate(self):
        regions, eyes, shape = geometry(synthetic.synthetic_hud_calibration())
        (x0, y0, x1, y1), = regions
        clean = scene(64, 96, 23)
        for name, (fill, _) in fills.FILLS.items():
            expected = fills.masked_input(clean, regions, eyes, fill, shape)
            leaked = []
            for y in range(y0, y1):
                for x in range(x0, x1):
                    changed = clean.copy()
                    changed[y, x] = 255 - clean[y, x]
                    if not np.array_equal(fills.masked_input(changed, regions, eyes, fill, shape), expected):
                        leaked.append((x, y))
            with self.subTest(fill=name):
                self.assertEqual(leaked, [])

    def test_no_candidate_reads_the_other_eye(self):
        """Including regions touching the seam, where even the 8 px ring would reach the other eye unclipped."""
        for layout in STEREO:
            cal = calibrations()[layout]
            regions, eyes, _ = geometry(cal)
            clean = scene(720, 1280, 25)
            for name, (fill, _) in fills.FILLS.items():
                native = fills.masked_input(clean, regions, eyes, fill, (720, 1280))
                for index, (ex0, ey0, ex1, ey1) in enumerate(eyes):
                    ox0, oy0, ox1, oy1 = eyes[1 - index]
                    changed = clean.copy()
                    changed[oy0:oy1, ox0:ox1] = 255 - clean[oy0:oy1, ox0:ox1]
                    result = fills.masked_input(changed, regions, eyes, fill, (720, 1280))
                    with self.subTest(layout=layout, fill=name, eye=index):
                        np.testing.assert_array_equal(result[ey0:ey1, ex0:ex1], native[ey0:ey1, ex0:ex1])

    def test_candidates_are_deterministic_and_the_report_checks_say_so(self):
        for name, (fill, _) in fills.FILLS.items():
            with self.subTest(fill=name):
                self.assertTrue(fills.deterministic(fill))
                self.assertEqual(fills.leak_check(fill), {"cases": 15, "failed": 0, "passed": True})

    def test_the_surround_rule_refuses_a_region_without_usable_pixels_or_eye(self):
        with self.assertRaisesRegex(ValueError, "hud_mask_fill_without_surround"):
            fill_regions(np.zeros((10, 10, 3), np.uint8), ((0, 0, 10, 10),), ((0, 0, 10, 10),))
        with self.assertRaisesRegex(ValueError, "hud_mask_region_outside_eyes"):
            fill_regions(np.zeros((10, 20, 3), np.uint8), ((8, 2, 12, 6),), ((0, 0, 10, 10), (10, 0, 20, 10)))


class RuntimePathTests(unittest.TestCase):
    """The evaluator measures exactly what the runtime mask gives the detector."""

    def test_the_runtime_fill_is_the_evaluated_ring_mean(self):
        self.assertIs(fills.FILLS[fills.RUNTIME_FILL][0], fill_regions)
        self.assertEqual(fills.RUNTIME_FILL, "ring_mean")

    def test_masked_input_with_the_runtime_fill_is_hud_mask_prepare(self):
        for label, cal in calibrations().items():
            regions, eyes, shape = geometry(cal)
            mask = hud.HudMask(cal, source_kind="synthetic")
            fmt = cal.capture_format
            self.assertEqual(fills.inference_shape(fmt.width, fmt.height), shape)
            self.assertEqual(regions, mask.regions)
            for seed in (31, 32):
                frame = scene(fmt.height, fmt.width, seed)
                with self.subTest(calibration=label, seed=seed):
                    np.testing.assert_array_equal(fills.masked_input(frame, regions, eyes, fill_regions, shape),
                                                  prepare(mask, frame))

    def test_classify_agrees_with_hud_mask_select(self):
        cal = calibrations()["stereo_sbs"]
        regions, eyes, shape = geometry(cal)
        mask = hud.HudMask(cal, source_kind="synthetic")
        height, width = shape
        x0, y0, x1, y1 = regions[0]
        scale = width / cal.capture_format.width
        detections = [Detection("mask", .9, (x0 * scale, y0 * scale, (x0 + 2) * scale, (y0 + 2) * scale)),
                      Detection("near", .9, ((x0 - 9) * scale, y0 * scale, (x0 - 1) * scale, (y0 + 4) * scale)),
                      Detection("cross-eye", .8, (width / 2 - 10, 5, width / 2 + 10, 20)),
                      Detection("clear", .7, (10, 10, 20, 20)), Detection("low", .2, (10, 10, 20, 20))]
        found = fills.classify(detections, shape, cal.capture_format.width, cal.capture_format.height, regions, eyes)
        _, checks = mask.select(detections, shape, .5)
        self.assertEqual(tuple(item["native"] for item in found), checks.boxes_native)
        reasons = [item["reason"] for item in found]
        self.assertEqual(reasons.count("mask_overlap"), checks.mask_overlap_rejections)
        self.assertEqual(reasons.count("eye_crossing"), checks.eye_crossing_rejections)
        self.assertEqual([item["label"] for item in found if item["reason"] is None], ["near", "clear"])
        kept = fills.runtime_kept(detections, shape, cal.capture_format.width, cal.capture_format.height, regions,
                                  eyes)
        self.assertEqual([detection.label for detection in kept], ["near", "clear"])
        self.assertEqual(len(found) - len(kept), checks.mask_overlap_rejections + checks.eye_crossing_rejections)


def item(label, confidence, box, reason=None, native=None):
    return {"label": label, "confidence": confidence, "box": box, "native": native or box, "reason": reason}


class CompareTests(unittest.TestCase):
    def test_counts_lost_added_relabelled_rescored_and_touching_detections(self):
        region = (50, 50, 80, 80)
        unmasked = [item("dog", .9, (0, 0, 20, 20)), item("cat", .8, (30, 0, 45, 15)),
                    item("car", .7, (0, 30, 10, 45)), item("bike", .6, (20, 30, 35, 45)),
                    item("cow", .55, (60, 60, 70, 70), "mask_overlap")]
        masked = [item("dog", .82, (0, 0, 20, 20)), item("cat", .78, (30, 0, 45, 15)),
                  item("truck", .7, (0, 30, 10, 45)), item("tv", .6, (85, 85, 95, 95)),
                  item("horse", .7, (60, 60, 70, 70), "mask_overlap"),
                  item("tvmonitor", .8, (52, 52, 78, 78), "mask_overlap")]
        diagnostic_masked = [item("bike", .47, (20, 30, 35, 45))]
        result = fills.compare(unmasked, masked, (region,), (), diagnostic_masked)
        expected = {"unmasked_clear": 4, "matched": 2, "lost": 1, "added": 1, "label_changed": 1, "score_changed": 1,
                    "max_abs_score_delta": .08, "lost_marginal": 1, "added_marginal": 0, "mask_overlap": 2,
                    "mask_overlap_new": 2, "mask_overlap_artifact_like": 1, "eye_crossing": 0,
                    "unmasked_touching_region": 1}
        self.assertEqual({key: result[key] for key in expected}, expected)
        touching = {entry["label"]: entry for entry in result["details"]["mask_overlap"]}
        self.assertEqual((touching["horse"]["inside_fraction"], touching["horse"]["best_unmasked_iou"]), (1.0, 1.0))
        self.assertEqual(touching["tvmonitor"]["best_unmasked_iou"], round(100 / 676, 4))

    def test_seed_placements_follow_the_runtime_geometry_rules(self):
        for width, height in ((768, 576), (640, 424), (773, 512), (500, 500), (1352, 900), (1920, 1080)):
            placements = fills.seed_placements(width, height)
            self.assertEqual(set(placements), set(fills.PLACEMENTS))
            (x0, y0, x1, y1), margin = placements["lower_right"]
            with self.subTest(size=(width, height)):
                self.assertGreater(x0, width / 2)
                self.assertGreater(y0, height / 2)
                self.assertTrue(hud.contract.margin_valid(margin))
                self.assertAlmostEqual((x1 - x0) / (y1 - y0), 2.5, delta=.2)
        with self.assertRaisesRegex(ValueError, "breaks the mask geometry rules"):
            fills.seed_placements(96, 64)  # Too small for an inset footprint with the margin floor.


class CommandTests(unittest.TestCase):
    """``scripts/evaluate_mask_fill.py`` with a fake detector on generated input; no model or download."""

    def run_command(self, arguments, detector_factory=lambda name: BlackBoxDetector()):
        from scripts import evaluate_mask_fill

        with patch.object(evaluate_mask_fill, "create_detector", side_effect=detector_factory), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            code = evaluate_mask_fill.main(arguments)
        return code, json.loads(output.getvalue()), errors.getvalue()

    def manifest(self, directory):
        """A local manifest of two generated stills."""
        directory = Path(directory)
        entries = []
        for index, (width, height) in enumerate(((320, 240), (400, 300))):
            path = directory / f"still-{index}.png"
            cv2.imwrite(str(path), np.maximum(scene(height, width, 40 + index), 16))  # No black pixel.
            entries.append({"id": f"generated-{index}", "path": path.name, "present": ["chair"],
                            "absent": ["tvmonitor"]})
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "images": entries}))
        return manifest

    def test_report_flags_the_zero_fill_artifact_and_clears_the_runtime_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            code, report, errors = self.run_command(
                ["--detector", "mobilenet-ssd", "--manifest", str(self.manifest(directory)), "--timing-runs", "2",
                 "--fill", "zero", "--fill", "ring_mean", "--fill", "nearest", "--report", str(report_path),
                 "--expect-clean-gray-sweep"])
            self.assertEqual(json.loads(report_path.read_text()), report)
        self.assertEqual((code, report["status"]), (0, "evaluated"), report.get("error"))
        self.assertEqual(report["report_kind"], "hud_mask_fill_evaluation")
        self.assertIn("not Frame", report["scope"])
        self.assertEqual(report["runtime_fill"], "ring_mean")
        sweep = report["gray_sweep"]["mobilenet-ssd"]
        self.assertEqual(sweep["levels"], list(range(1, 254, 2)))
        self.assertEqual(sweep["region_native"], [[59, 34, 96, 64]])
        self.assertEqual(sweep["fills"]["zero"]["mask_overlap_level_count"], 127)
        self.assertEqual(sweep["fills"]["zero"]["labels"], {"tvmonitor": 127})
        self.assertEqual(set(report["fills"]), {"zero", "ring_mean", "nearest"})
        for name in report["fills"]:
            self.assertTrue(report["fills"][name]["leak_check"]["passed"], name)
            self.assertTrue(report["fills"][name]["deterministic"], name)
            if name != "zero":
                self.assertEqual(sweep["fills"][name]["mask_overlap_level_count"], 0, name)
        seeds = report["seeds"]["mobilenet-ssd"]
        self.assertEqual(seeds["images"], ["generated-0", "generated-1"])
        self.assertEqual(seeds["fills"]["zero"]["cases"], 2 * len(fills.PLACEMENTS))
        self.assertEqual(seeds["fills"]["zero"]["cases_with_artifact_like_overlap"], 2 * len(fills.PLACEMENTS))
        self.assertEqual(seeds["fills"]["ring_mean"]["mask_overlap"], 0)
        self.assertEqual(set(seeds["fills"]["ring_mean"]["pin_counts"]), set(fills.PLACEMENTS))
        for placement, pin in seeds["fills"]["zero"]["pin_counts"].items():
            with self.subTest(placement=placement):
                # The fake's tvmonitor on the black mask counts in the raw output; the runtime drops it.
                self.assertEqual((pin["absent_violations"], pin["absent_violations_change"]), (2, 2))
                self.assertEqual((pin["runtime_kept"]["absent_violations"],
                                  pin["runtime_kept"]["absent_violations_change"]), (0, 0))
        timing = report["timing"]["cases"]
        self.assertEqual(set(timing), {"96x64_fixture", "1920x1080_lower_right", "1920x1080_blind_budget_max"})
        self.assertEqual(timing["1920x1080_blind_budget_max"]["region_px"], 1920 * 1080 // 4)
        self.assertEqual(timing["96x64_fixture"]["runtime_hud_mask_prepare_ms"]["samples"], 2)
        self.assertIn("ring_mean", errors)

    def test_expect_clean_gray_sweep_fails_when_the_runtime_fill_boxes_the_mask(self):
        with patch.object(fills, "RUNTIME_FILL", "zero"):
            code, report, errors = self.run_command(["--detector", "mobilenet-ssd", "--no-seeds", "--fill", "zero",
                                                     "--timing-runs", "1", "--expect-clean-gray-sweep"])
        self.assertEqual((code, report["status"]), (1, "evaluated"))
        self.assertIn("mask boxed on the gray sweep", errors)

    def test_errors_are_reported_and_exit_one(self):
        code, report, _ = self.run_command(["--manifest", "/nonexistent/manifest.json", "--timing-runs", "1"])
        self.assertEqual((code, report["status"]), (1, "error"))
        self.assertIn("error", report)


class BoundaryTests(unittest.TestCase):
    def test_no_runtime_module_imports_the_evaluation(self):
        for path in sorted([*(ROOT / "sightcircuit").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]):
            if path.name in ("mask_fill_evaluation.py", "evaluate_mask_fill.py"):
                continue
            tree = ast.parse(path.read_text(), str(path))
            names = [alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                     for alias in node.names] + [node.module or "" for node in ast.walk(tree)
                                                 if isinstance(node, ast.ImportFrom)]
            with self.subTest(module=str(path.relative_to(ROOT))):
                self.assertFalse([name for name in names if "mask_fill_evaluation" in name])

    def test_evaluation_never_builds_a_calibration_for_a_seed_still(self):
        """Seed photos are not a capture source: only generated frames go through the fixture factory."""
        detector = BlackBoxDetector()
        image = scene(240, 320, 50)
        with patch.object(synthetic, "fixture_calibration", side_effect=AssertionError("calibration built")):
            fills.seed_comparison(detector, [({"id": "x", "present": [], "absent": []}, image)], ("ring_mean",))
        self.assertGreater(detector.calls, 0)

    def test_a_moved_seed_manifest_reads_the_still_beside_it_and_says_so(self):
        """The detector evaluator's path fallback (C8) is announced and reported here as well, never silent."""
        with tempfile.TemporaryDirectory() as folder:
            still = Path(folder) / "scene.png"
            self.assertTrue(cv2.imwrite(str(still), scene(240, 320, 5)))
            manifest = Path(folder) / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "images": [
                {"id": "moved", "path": "/home/steamos/framejacker/evaluation/local/old/scene.png"},
                {"id": "here", "path": "scene.png"}]}))
            messages = []
            images = fills.load_seed_images(manifest, Path(folder) / "downloads", log=messages.append)
            self.assertEqual([entry["_input_resolution"] for entry, _ in images], ["beside_manifest", "recorded"])
            self.assertEqual(len(messages), 1)
            self.assertIn("moved: the recorded path does not exist; using scene.png beside the manifest", messages[0])
            result = fills.seed_comparison(BlackBoxDetector(), images, ("zero",))
            self.assertEqual(result["input_path_resolution"], {"moved": "beside_manifest", "here": "recorded"})
            manifest.write_text(json.dumps({"schema_version": 1, "images": [
                {"id": "gone", "path": "/home/steamos/framejacker/evaluation/local/old/other.png"}]}))
            with self.assertRaises(FileNotFoundError):
                fills.load_seed_images(manifest, Path(folder) / "downloads", log=messages.append)

    def test_seed_pin_counts_separate_the_raw_output_from_what_the_runtime_keeps(self):
        """A present object boxed over the lower-right corner is a raw hit everywhere, kept only off the mask."""
        detector = CornerDetector()
        entry = {"id": "corner", "present": ["chair"], "absent": ["tvmonitor"]}
        result = fills.seed_comparison(detector, [(entry, scene(240, 320, 51))], ("ring_mean", "zero"))
        self.assertEqual(result["unmasked_pin_counts"]["present_hits"], 1)
        for name in ("ring_mean", "zero"):
            pins = result["fills"][name]["pin_counts"]
            with self.subTest(fill=name):
                self.assertEqual({placement: pin["present_hits"] for placement, pin in pins.items()},
                                 dict.fromkeys(fills.PLACEMENTS, 1))
                self.assertEqual({placement: pin["runtime_kept"]["present_hits"] for placement, pin in pins.items()},
                                 {"lower_right": 0, "lower_left": 1, "upper_left": 1, "center": 1,
                                  "fixture_scaled": 0})
                self.assertEqual(pins["lower_right"]["runtime_kept"]["present_hits_change"], -1)
                self.assertEqual(pins["lower_right"]["present_hits_change"], 0)

    def test_timing_reports_runner_cpu_milliseconds(self):
        started = time.monotonic()
        report = fills.timing(("zero", "ring_mean"), runs=1)
        self.assertLess(time.monotonic() - started, 30)
        for case in report.values():
            for result in case["fills"].values():
                self.assertEqual(set(result), {"fill_ms", "prepare_ms"})
                self.assertGreaterEqual(result["fill_ms"]["p50"], 0)


if __name__ == "__main__":
    unittest.main()
