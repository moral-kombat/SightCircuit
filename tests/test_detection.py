"""Detector registry, YOLOX decoding, letterbox mapping, labels and asset checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from framejacker import assets, detection
from framejacker.labels import COCO_CLASSES, canonical
from framejacker.preprocess import Letterbox

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
MODELS = ROOT / ".cache/framejacker/assets"


class AssetTests(unittest.TestCase):
    def test_missing_entries_and_checksum_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "model.bin").write_bytes(b"weights")
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps({"assets": [{"name": "model.bin", "url": "", "sha256": "0" * 64}]}))
            with self.assertRaisesRegex(RuntimeError, "missing"):
                assets.verify(["other.bin"], folder, manifest)
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                assets.verify(["model.bin"], folder, manifest)
            manifest.write_text(json.dumps({"assets": [{"name": "model.bin", "url": "",
                                                        "sha256": assets.checksum(folder / "model.bin")}]}))
            self.assertEqual(assets.verify(["model.bin"], folder, manifest), [folder / "model.bin"])
            with self.assertRaisesRegex(RuntimeError, "missing: absent.bin"):
                manifest.write_text(json.dumps({"assets": [{"name": "absent.bin", "url": "", "sha256": "0"}]}))
                assets.verify(["absent.bin"], folder, manifest)

    def test_every_detector_model_has_a_pinned_checksum(self):
        names = [*detection.MODEL_FILES, *(spec[0] for spec in detection.YoloxDetector.MODELS.values())]
        entries = assets.manifest_entries(names)
        self.assertTrue(all(len(entry["sha256"]) == 64 for entry in entries.values()))


class LabelAndTransformTests(unittest.TestCase):
    def test_voc_names_map_onto_coco_vocabulary(self):
        self.assertEqual(len(COCO_CLASSES), 80)
        for label in detection.MobileNetSSD.classes:
            self.assertIn(canonical(label), COCO_CLASSES, label)
        self.assertEqual(canonical("sofa"), "couch")
        self.assertEqual(canonical("rug"), "rug")

    def test_letterbox_inverse_maps_and_clamps_boxes(self):
        transform = Letterbox(scale_x=0.5, scale_y=0.5, pad_x=0, pad_y=52, width=832, height=624)
        self.assertEqual(transform.to_source(10, 62, 110, 162), (20, 20, 220, 220))
        self.assertEqual(transform.to_source(-5, 0, 1000, 1000), (0, 0, 831, 623))

    def test_letterbox_inverse_uses_realized_resize_ratio(self):
        from framejacker.preprocess import letterbox_geometry

        resized_w, resized_h, transform = letterbox_geometry(1440, 1080, 416)
        self.assertEqual((resized_w, resized_h), (415, 311))
        self.assertEqual(transform.to_source(0, 0, resized_w, resized_h), (0, 0, 1439, 1079))
        self.assertEqual(transform.to_source(0, 300, 0, 300)[1], 1041)

    def test_letterbox_inverse_error_is_subpixel_for_common_sources(self):
        from framejacker.preprocess import letterbox_geometry

        for width, height in ((640, 360), (960, 540), (1280, 720), (1920, 1080), (1440, 1080), (1366, 768)):
            resized_w, resized_h, transform = letterbox_geometry(width, height, 416)
            far_x, far_y = transform.to_source(0, 0, resized_w, resized_h)[2:]
            self.assertLessEqual(width - 1 - far_x, 1, (width, height))
            self.assertLessEqual(height - 1 - far_y, 1, (width, height))

    def test_unknown_detector_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown detector"):
            detection.create_detector("rug-detector")


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class LetterboxImageTests(unittest.TestCase):
    def test_aspect_preserved_with_padding(self):
        import numpy as np
        from framejacker.preprocess import letterbox

        image = np.full((100, 200, 3), 7, dtype=np.uint8)
        padded, transform = letterbox(image, 64)
        self.assertEqual(padded.shape, (64, 64, 3))
        self.assertEqual((transform.scale_x, transform.scale_y, transform.pad_x, transform.pad_y), (0.32, 0.32, 0, 0))
        self.assertTrue((padded[:32] == 7).all() and (padded[32:] == 114).all())
        padded, transform = letterbox(image, 64, center=True)
        self.assertEqual(transform.pad_y, 16)
        self.assertEqual(transform.to_source(0, 16, 64, 48), (0, 0, 199, 99))


class FakeYolox(detection.YoloxDetector):
    """Decode a hand-built raw output without loading a model."""

    def __init__(self, output, size=64):
        import cv2
        import numpy as np

        self.cv2, self.np, self.size = cv2, np, size
        self.name, self.runtime, self.classes, self.nms_threshold = "fake", "fake", COCO_CLASSES, 0.45
        grids, strides = [], []
        for stride in self.STRIDES:
            cells = size // stride
            ys, xs = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
            grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
            strides.append(np.full((cells * cells, 1), stride))
        self.grid, self.stride = np.concatenate(grids), np.concatenate(strides)
        self.output = output(len(self.grid))

    def _forward(self, blob):
        assert blob.shape == (1, 3, self.size, self.size) and blob.dtype == self.np.float32
        return self.output


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class YoloxDecodeTests(unittest.TestCase):
    def raw(self, rows):
        import numpy as np

        def build(count):
            output = np.zeros((1, count, 85), dtype=np.float32)
            for index, (offset, log_size, objectness, class_id, score) in rows.items():
                output[0, index, :2], output[0, index, 2:4] = offset, log_size
                output[0, index, 4], output[0, index, 5 + class_id] = objectness, score
            return output
        return build

    def test_grid_decoding_nms_threshold_and_source_mapping(self):
        import numpy as np

        # Stride-8 cell (x=2, y=3) has index 3 * 8 + 2 = 26 on a 64-pixel input.
        rows = {26: ((0.5, 0.5), (np.log(2), np.log(2)), 0.9, 57, 0.9),   # couch, centre (20, 28), 16 px
                27: ((0.0, 0.5), (np.log(2), np.log(2)), 0.9, 57, 0.8),   # overlapping duplicate
                30: ((0.5, 0.5), (0.0, 0.0), 0.5, 0, 0.5)}                # below 0.5 after objectness
        detector = FakeYolox(self.raw(rows))
        image = np.zeros((32, 64, 3), dtype=np.uint8)  # Scale 1, padded below.
        result = detector.detect(image, 0.5)
        self.assertEqual([(item.label, item.box_xyxy) for item in result.detections], [("couch", (12, 20, 28, 31))])
        self.assertAlmostEqual(result.detections[0].confidence, 0.81, places=5)

    def test_unexpected_output_shape_and_nonfinite_rows(self):
        import numpy as np

        detector = FakeYolox(lambda count: np.zeros((1, count, 84), dtype=np.float32))
        with self.assertRaisesRegex(RuntimeError, "output shape"):
            detector.detect(np.zeros((8, 8, 3), dtype=np.uint8), 0.5)
        detector = FakeYolox(self.raw({0: ((np.nan, 0), (0, 0), 1.0, 1, 1.0)}))
        self.assertEqual(detector.detect(np.zeros((8, 8, 3), dtype=np.uint8), 0.5).detections, ())

    @unittest.skipUnless((MODELS / "yolox_nano.onnx").is_file(), "Run scripts/setup_window.sh for the model")
    def test_real_model_runtimes_agree_on_fixture(self):
        import cv2

        image = cv2.imread(str(MODELS / "dog.jpg"))
        runtimes = ["opencv-dnn"] + (["onnxruntime"] if importlib.util.find_spec("onnxruntime") else [])
        results = {}
        for runtime in runtimes:
            detector = detection.create_detector("yolox-nano", runtime=runtime)
            self.assertEqual(detection.describe(detector), {"name": "yolox-nano", "runtime": runtime, "classes": 80})
            results[runtime] = detector.detect(image, 0.5)
            self.assertTrue({"dog", "bicycle"}.issubset(results[runtime].labels))
            self.assertTrue({"car", "truck"} & set(results[runtime].labels))
        if len(results) == 2:
            first, second = (result.detections for result in results.values())
            self.assertEqual([item.label for item in first], [item.label for item in second])
            for a, b in zip(first, second):
                self.assertLessEqual(max(abs(x - y) for x, y in zip(a.box_xyxy, b.box_xyxy)), 2)


if __name__ == "__main__":
    unittest.main()
