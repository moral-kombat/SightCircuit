"""Detector registry, SSD and YOLOX decoding, letterbox mapping, labels and asset checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sightcircuit import assets, detection
from sightcircuit.labels import COCO_CLASSES, canonical
from sightcircuit.preprocess import Letterbox
from tests.asset_gate import model_assets, requires_assets

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
MODELS = ROOT / ".cache/sightcircuit/assets"


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

    def test_registered_detectors_own_their_pinned_model_files(self):
        for name in detection.DETECTORS:
            with self.subTest(detector=name):
                files = detection.model_files(name)
                self.assertTrue(files and all(isinstance(item, str) for item in files))
                entries = assets.manifest_entries(files)
                self.assertTrue(all(len(entry["sha256"]) == 64 for entry in entries.values()))
        self.assertEqual(detection.model_files("mobilenet-ssd"), detection.MODEL_FILES)
        self.assertEqual(detection.model_files("yolox-nano"), ("yolox_nano.onnx",))
        with self.assertRaisesRegex(ValueError, "Unknown detector"):
            detection.model_files("rug-detector")

    def test_absent_downloads_skip_with_their_names(self):
        skipped = requires_assets("absent-model.bin")(lambda self: None)
        self.assertTrue(skipped.__unittest_skip__)
        self.assertIn("absent-model.bin", skipped.__unittest_skip_why__)
        self.assertIn("setup_window.sh", skipped.__unittest_skip_why__)
        self.assertEqual(model_assets("yolox-nano"), ("dog.jpg", "yolox_nano.onnx"))
        self.assertEqual(model_assets("mobilenet-ssd", "yolox-nano", "mobilenet-ssd"),
                         ("dog.jpg", *detection.MODEL_FILES, "yolox_nano.onnx"))
        # Every registered detector, however many there are: the fixture first, then each file once.
        everything = model_assets()
        self.assertEqual((everything[0], len(set(everything))), ("dog.jpg", len(everything)))
        self.assertEqual(set(everything), {"dog.jpg", *(item for name in detection.DETECTORS
                                                        for item in detection.model_files(name))})


class DetectorOwnershipTests(unittest.TestCase):
    """Each registered class owns its model files and preprocessing; callers branch on neither."""
    KEYS = {"input_size", "resize", "interpolation", "color_order", "layout", "dtype", "mean", "scale",
            "padding_value", "box_mapping"}

    def test_detectors_describe_their_preprocessing(self):
        for name in detection.DETECTORS:
            with self.subTest(detector=name):
                description = detection.preprocessing(name)
                self.assertEqual(set(description), self.KEYS)
                self.assertEqual(json.loads(json.dumps(description)), description)
        ssd = detection.preprocessing("mobilenet-ssd")
        self.assertEqual((ssd["input_size"], ssd["resize"], ssd["color_order"], ssd["layout"], ssd["dtype"]),
                         ([300, 300], "stretch", "BGR", "NCHW", "float32"))
        self.assertEqual((ssd["mean"], ssd["scale"], ssd["padding_value"]), ([127.5, 127.5, 127.5], 0.007843, None))
        yolox = detection.preprocessing("yolox-nano")
        self.assertEqual((yolox["input_size"], yolox["resize"], yolox["color_order"]),
                         ([416, 416], "letterbox_top_left", "BGR"))
        self.assertEqual((yolox["mean"], yolox["scale"], yolox["padding_value"]), ([0, 0, 0], 1, 114))
        self.assertEqual(yolox["box_mapping"], "inverse realized x/y resize ratios")
        with self.assertRaisesRegex(ValueError, "Unknown detector"):
            detection.preprocessing("rug-detector")

    def test_factory_passes_each_registered_name_to_its_class(self):
        calls = []

        class Third:
            def __init__(self, name, **options):
                calls.append((name, options))

        with mock.patch.dict(detection.DETECTORS, {"third": Third}):
            detection.create_detector("third", threads=1)
        self.assertEqual(calls, [("third", {"threads": 1})])
        with self.assertRaisesRegex(ValueError, "Unknown MobileNet-SSD model"):
            detection.MobileNetSSD("yolox-nano")


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
        from sightcircuit.preprocess import letterbox_geometry

        resized_w, resized_h, transform = letterbox_geometry(1440, 1080, 416)
        self.assertEqual((resized_w, resized_h), (415, 311))
        self.assertEqual(transform.to_source(0, 0, resized_w, resized_h), (0, 0, 1439, 1079))
        self.assertEqual(transform.to_source(0, 300, 0, 300)[1], 1041)

    def test_letterbox_inverse_error_is_subpixel_for_common_sources(self):
        from sightcircuit.preprocess import letterbox_geometry

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
        from sightcircuit.preprocess import letterbox

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

    @requires_assets(*model_assets("yolox-nano"))
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


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class PreprocessingDescriptionTests(unittest.TestCase):
    def test_descriptions_match_the_tensors_each_detector_builds(self):
        import cv2
        import numpy as np

        image = np.full((90, 160, 3), (10, 80, 240), dtype=np.uint8)
        ssd = detection.MobileNetSSD.__new__(detection.MobileNetSSD)
        ssd.cv2 = cv2
        spec = detection.preprocessing("mobilenet-ssd")
        blob, transform = ssd.prepare_input(image)
        self.assertIsNone(transform)
        self.assertEqual((blob.shape, str(blob.dtype)), ((1, 3, *spec["input_size"][::-1]), spec["dtype"]))
        np.testing.assert_allclose(blob[0, :, 0, 0], (np.array([10, 80, 240]) - spec["mean"]) * spec["scale"],
                                   rtol=1e-6)
        yolox = detection.YoloxDetector.__new__(detection.YoloxDetector)
        yolox.np, yolox.size = np, 416
        spec = detection.preprocessing("yolox-nano")
        blob, transform = yolox.prepare_input(image)
        self.assertEqual((blob.shape, str(blob.dtype)), ((1, 3, *spec["input_size"][::-1]), spec["dtype"]))
        np.testing.assert_array_equal(blob[0, :, 0, 0], (np.array([10, 80, 240]) - spec["mean"]) * spec["scale"])
        np.testing.assert_array_equal(blob[0, :, -1, -1], [spec["padding_value"]] * 3)


class FakeSsdNet:
    """Stands in for the Caffe network: records the blob and returns a hand-built DetectionOutput."""

    def __init__(self, output):
        self.output, self.blob = output, None

    def setInput(self, blob):
        self.blob = blob

    def forward(self):
        return self.output


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class SsdDecodeTests(unittest.TestCase):
    """MobileNet-SSD postprocessing on synthetic network output, with no downloaded model."""

    @staticmethod
    def output(rows):
        import numpy as np

        return np.array(rows, dtype=np.float32).reshape(1, 1, -1, 7)

    def test_rows_are_filtered_labelled_and_mapped_to_input_pixels(self):
        import numpy as np

        dog, car, chair, person = (detection.CLASSES.index(name) for name in ("dog", "car", "chair", "person"))
        rows = [[0, dog, 0.9, 0.1, 0.2, 0.5, 0.75],        # (64, 96, 320, 360) on 640 x 480
                [0, car, 0.5, 0.25, 0.25, 0.5, 0.5],        # Exactly the threshold: kept.
                [0, car, 0.49999, 0.0, 0.0, 0.5, 0.5],      # Below the threshold.
                [0, 0, 0.99, 0.0, 0.0, 1.0, 1.0],           # Background.
                [0, len(detection.CLASSES), 0.99, 0.0, 0.0, 1.0, 1.0],  # Outside the label map.
                [0, person, np.nan, 0.0, 0.0, 1.0, 1.0],    # Non-finite score.
                [0, person, 0.7, -0.2, -0.1, 1.3, 1.2],     # Clamped to the input.
                [0, chair, 0.8, 0.5, 0.5, 0.5, 0.9]]        # Empty after mapping.
        detections = detection.decode_ssd(self.output(rows), 640, 480, 0.5)
        self.assertEqual([(item.label, item.confidence, item.box_xyxy) for item in detections],
                         [("dog", 0.9, (64, 96, 320, 360)), ("car", 0.5, (160, 120, 320, 240)),
                          ("person", 0.7, (0, 0, 639, 479))])
        self.assertEqual(detection.decode_ssd(self.output(rows), 640, 480, 0.95), ())

    def test_decode_reads_the_label_map_at_call_time(self):
        swapped = list(detection.CLASSES)
        dog, cat = swapped.index("dog"), swapped.index("cat")
        swapped[dog], swapped[cat] = swapped[cat], swapped[dog]
        with mock.patch.object(detection, "CLASSES", swapped):
            detections = detection.decode_ssd(self.output([[0, dog, 0.9, 0.1, 0.1, 0.5, 0.5]]), 10, 10, 0.5)
        self.assertEqual([item.label for item in detections], ["cat"])

    def test_unexpected_output_shape_is_rejected(self):
        import numpy as np

        with self.assertRaisesRegex(RuntimeError, "output shape"):
            detection.decode_ssd(np.zeros((1, 1, 2, 6), dtype=np.float32), 10, 10, 0.5)

    def test_detect_feeds_the_real_blob_to_the_network_and_decodes_its_output(self):
        import cv2
        import numpy as np

        tv = detection.CLASSES.index("tvmonitor")
        ssd = detection.MobileNetSSD.__new__(detection.MobileNetSSD)
        ssd.cv2, ssd.np = cv2, np
        ssd.net = FakeSsdNet(self.output([[0, tv, 0.6, 0.5, 0.5, 1.0, 1.0]]))
        result = ssd.detect(np.zeros((540, 960, 3), dtype=np.uint8), 0.5)
        self.assertEqual(ssd.net.blob.shape, (1, 3, 300, 300))
        self.assertEqual([(item.label, item.box_xyxy) for item in result.detections],
                         [("tvmonitor", (480, 270, 959, 539))])
        self.assertGreaterEqual(result.inference_ms, 0)


if __name__ == "__main__":
    unittest.main()


class PocContractTests(unittest.TestCase):
    """The POC threshold and the live budget are defined once and reach every command and engine default."""

    def test_constants_carry_the_accepted_values(self):
        self.assertEqual(detection.POC_THRESHOLD, 0.5)
        self.assertEqual(detection.LIVE_INFERENCE_P95_BUDGET_MS, 200)  # Owner's budget of 2026-11-07.

    def test_every_command_defaults_its_threshold_to_the_constant(self):
        import argparse

        from scripts import evaluate_detectors, mirror_recognize, replay_window

        class Parsed(Exception):
            pass

        def intercept(parser, args=None, namespace=None):
            raise Parsed(parser.get_default("threshold"))

        for module in (evaluate_detectors, mirror_recognize, replay_window):
            with self.subTest(command=module.__name__), mock.patch.object(argparse.ArgumentParser, "parse_args", intercept):
                with self.assertRaises(Parsed) as parsed:
                    module.main([])
                self.assertIs(parsed.exception.args[0], detection.POC_THRESHOLD)

    def test_engine_and_runners_default_to_the_constant(self):
        import inspect

        from sightcircuit import mask_fill_evaluation, pipeline
        from sightcircuit.runtime import live

        for name, function in (("Pipeline", pipeline.Pipeline), ("run_pipeline", live.run_pipeline),
                               ("run_recognition", live.run_recognition)):
            with self.subTest(callable=name):
                self.assertIs(inspect.signature(function).parameters["threshold"].default, detection.POC_THRESHOLD)
        self.assertIs(mask_fill_evaluation.THRESHOLD, detection.POC_THRESHOLD)
        report = live.new_mirror_report()
        self.assertEqual(report["inference_p95_budget_ms"], detection.LIVE_INFERENCE_P95_BUDGET_MS)
        self.assertIsNone(report["inference_p95_within_budget"])  # Nothing timed yet.

    def test_no_command_or_module_repeats_the_threshold_literal_as_a_default(self):
        """A new ``default=0.5``/``threshold=0.5`` must import the constant; IoU 0.5 is a different number."""
        import re

        pattern = re.compile(r"(?<![A-Za-z_])(default|threshold|THRESHOLD)\s*=\s*0\.5\b")
        for path in sorted([*(ROOT / "scripts").glob("*.py"), *(ROOT / "sightcircuit").rglob("*.py")]):
            with self.subTest(file=str(path.relative_to(ROOT))):
                repeated = [line for line in path.read_text().splitlines()
                            if pattern.search(line) and "iou" not in line.lower()]
                self.assertEqual(repeated, [])
