"""The committed seed pin with the real models: it holds as committed and fails on a label-map break.

Seed results are a wiring and label-mapping regression check on the runner's
CPU, not Frame evidence. CI runs this module without skips after
``evaluate_detectors.py --fetch`` has downloaded the seed images.
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from framejacker import detection
from tests.asset_gate import model_assets, requires_assets, requires_seed_images

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "evaluation/seed-expected.json"
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy", "onnxruntime"))


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV and ONNX Runtime")
@requires_seed_images()
@requires_assets(*model_assets())
class SeedPinTests(unittest.TestCase):
    def evaluate(self):
        from scripts import evaluate_detectors

        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            code = evaluate_detectors.main(["--expect", str(PIN)])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_committed_seed_counts_meet_the_pin(self):
        code, report, errors = self.evaluate()
        self.assertEqual(code, 0, report.get("expectation", report.get("error")))
        self.assertEqual(report["expectation"]["status"], "passed")
        self.assertEqual(report["expectation"]["failures"], [])
        self.assertIn("seed-expected.json: passed", errors)
        for name, result in report["detectors"].items():
            with self.subTest(detector=name):
                self.assertIn("labels", result)
                self.assertIn("best_below_threshold", result["images"][0])

    def test_swapped_voc_labels_fail_the_pin(self):
        """Done condition: a deliberate label-map break exits non-zero against the committed pin."""
        swapped = list(detection.CLASSES)
        dog, cat = swapped.index("dog"), swapped.index("cat")
        swapped[dog], swapped[cat] = swapped[cat], swapped[dog]
        with mock.patch.object(detection, "CLASSES", swapped):
            code, report, errors = self.evaluate()
        self.assertEqual((code, report["status"]), (1, "evaluated"))
        failures = report["expectation"]["failures"]
        self.assertEqual(report["expectation"]["status"], "regressed")
        self.assertTrue(any(item.startswith("mobilenet-ssd present_hits") for item in failures), failures)
        self.assertTrue(any(item.startswith("mobilenet-ssd absent_violations") for item in failures), failures)
        self.assertFalse([item for item in failures if not item.startswith("mobilenet-ssd")], failures)
        self.assertIn("REGRESSED", errors)


if __name__ == "__main__":
    unittest.main()
