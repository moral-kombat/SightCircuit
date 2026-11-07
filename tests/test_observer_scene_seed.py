"""Observer scene-precondition statistics pinned on the public seed stills (downloaded, checksum-verified).

The seed stills are outdoor JPEG photos, not Frame mirror reads: these pins
check that the statistics are stable and computed on the real decode path, and
give a first non-synthetic reference. They are not evidence about the Frame's
scene. CI runs this module after ``evaluate_detectors.py --fetch``, through
``scripts/run_tests_no_skips.py``.
"""
import hashlib
import json
import unittest

try:
    import cv2  # noqa: F401  (read_still decodes with OpenCV)
except ImportError as error:  # The dependency-free discovery jobs have no OpenCV.
    raise unittest.SkipTest("OpenCV is required for observer scene seed tests") from error

from sightcircuit.hud import scene
from tests.asset_gate import SEED_IMAGES, SEED_MANIFEST, requires_seed_images

# id: ((width, height), bright pixels, (bright components, specks, enclosed holes), contours,
#      (below 25, 25 to 400, 400 to 10000, from 10000), quads, outlines, bright fraction, (p50, p90, p99),
#      precondition rejection). Speck densities per megapixel: dog 2523, person 10805, horses 389,
#      giraffe 12496, eagle 445, kite 2060 (the kite is three-quarters bright, so saturation names it first).
SEED_STATISTICS = {
    "darknet-dog": ((768, 576), 44110, (1313, 1116, 322), 1635, (1566, 59, 9, 1), 1, 0, 0.099713,
                    (139, 179, 254), "observer_speck_density"),
    "darknet-person": ((640, 424), 97141, (3265, 2932, 341), 3606, (3560, 43, 1, 2), 0, 0, 0.357978,
                       (168, 207, 234), "observer_speck_density"),
    "darknet-horses": ((773, 512), 17173, (178, 154, 85), 263, (248, 11, 4, 0), 0, 0, 0.043391,
                       (135, 165, 201), None),
    "darknet-giraffe": ((500, 500), 19717, (3315, 3124, 153), 3468, (3393, 70, 5, 0), 0, 0, 0.078868,
                        (94, 171, 234), "observer_speck_density"),
    "darknet-eagle": ((773, 512), 9746, (254, 176, 66), 320, (285, 32, 3, 0), 0, 0, 0.024625,
                      (78, 114, 206), None),
    "darknet-kite": ((1352, 900), 912928, (2824, 2506, 6551), 9375, (8768, 567, 39, 1), 1, 0, 0.75027,
                     (205, 240, 252), "observer_bright_saturation"),
}


@requires_seed_images()
class SeedSceneStatisticsTests(unittest.TestCase):
    def test_seed_stills_give_the_pinned_statistics(self):
        entries = json.loads(SEED_MANIFEST.read_text())["images"]
        self.assertEqual({entry["id"] for entry in entries}, set(SEED_STATISTICS))
        for entry in entries:
            with self.subTest(still=entry["id"]):
                path = SEED_IMAGES / entry["file"]
                # The asset gate checks presence only; an altered still must fail, not pass.
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
                report = scene.analyze_still(path)
                self.assertEqual((report["status"], report["exit_code"]), ("analyzed", 0), report)
                self.assertEqual((report["source"]["format"], report["source"]["lossless"]), ("jpeg", False))
                self.assertEqual(report["source"]["sha256"], entry["sha256"])
                statistics = report["statistics"]
                scene.validate_scene_statistics(statistics)
                self.assertEqual((statistics["schema_version"], statistics["contour_scan"]), (2, "complete"))
                bands = statistics["contour_area_bands"]
                percentiles = statistics["gray_percentiles"]
                census = statistics["census"]
                self.assertEqual(((statistics["width"], statistics["height"]), statistics["bright_pixel_count"],
                                  (census["bright_components"], census["specks"], census["enclosed_holes"]),
                                  statistics["contour_count"],
                                  (bands["below_25"], bands["from_25_below_400"], bands["from_400_below_10000"],
                                   bands["from_10000"]),
                                  statistics["quad_count"], statistics["outline_count"],
                                  statistics["bright_pixel_fraction"],
                                  (percentiles["p50"], percentiles["p90"], percentiles["p99"]),
                                  statistics["precondition"]["rejection"]),
                                 SEED_STATISTICS[entry["id"]])


if __name__ == "__main__":
    unittest.main()
