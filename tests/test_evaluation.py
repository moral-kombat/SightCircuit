"""Detector evaluation scoring, manifest validation and the comparison command."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from framejacker import evaluation
from framejacker.frame import Detection

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/framejacker/assets"
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
VOC = ("sofa", "chair", "dog", "car")


def found(label, box, confidence=0.9):
    return Detection(label, confidence, tuple(box))


class ScoringTests(unittest.TestCase):
    def test_image_labels_alternatives_unsupported_and_absent(self):
        entry = {"id": "room", "present": ["dog", ["car", "truck"], "giraffe"], "absent": ["couch", "bed"]}
        score = evaluation.score_image(entry, [found("dog", [0, 0, 5, 5]), found("sofa", [5, 5, 9, 9])], VOC)
        self.assertEqual(score["present_hits"], ["dog"])
        self.assertEqual(score["present_misses"], ["car|truck"])
        self.assertEqual(score["unsupported"], ["bed", "giraffe"])
        self.assertEqual(score["absent_violations"], ["couch"])  # VOC "sofa" is canonical "couch".
        self.assertEqual(score["reported"], ["couch", "dog"])

    def test_boxes_matching_scale_confusions_and_false_positives(self):
        entry = {"id": "rug", "boxes_complete": True, "objects": [
            {"label": "rug", "box_xyxy": [0, 100, 200, 200], "confusable": ["couch"]},
            {"label": "chair", "box_xyxy": [200, 0, 300, 100]},
            {"label": "dog", "box_xyxy": [300, 100, 400, 200]}]}
        detections = [found("sofa", [0, 50, 100, 100], 0.7),   # Rug at half scale: confusion.
                      found("chair", [100, 0, 150, 50]),        # Matches the chair.
                      found("chair", [150, 0, 200, 50], 0.6)]   # Duplicate: false positive.
        score = evaluation.score_image(entry, detections, VOC, scale=0.5)
        self.assertEqual((score["true_positives"], score["false_negatives"], score["false_positives"]), (1, 1, 2))
        self.assertEqual(score["confusions"], [{"object": "rug", "detected": "couch", "confidence": 0.7}])
        self.assertEqual(score["unsupported"], ["rug"])
        summary = evaluation.totals([score, evaluation.score_image({"id": "x", "present": ["dog"]}, [], VOC)])
        self.assertEqual(summary["box_recall"], 0.5)
        self.assertEqual(summary["box_precision"], round(1 / 3, 4))
        self.assertEqual((summary["present_recall"], summary["confusions"]), (0.0, 1))

    def test_incomplete_boxes_do_not_count_false_positives(self):
        entry = {"id": "partial", "objects": [{"label": "dog", "box_xyxy": [0, 0, 10, 10]}]}
        score = evaluation.score_image(entry, [found("car", [20, 20, 30, 30])], VOC)
        self.assertNotIn("false_positives", score)
        self.assertNotIn("box_precision", evaluation.totals([score]))

    def test_iou(self):
        self.assertEqual(evaluation.iou([0, 0, 10, 10], [10, 10, 20, 20]), 0)
        self.assertAlmostEqual(evaluation.iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)

    def test_realized_axes_are_used_for_rounded_resize_ground_truth(self):
        entry = {"id": "tall", "objects": [{"label": "dog", "box_xyxy": [0, 1000, 1920, 1081]}]}
        # 1920x1081 becomes 960x540: applying x's 0.5 ratio to y falsely fails exact IoU.
        score = evaluation.score_image(entry, [found("dog", [0, 1000 * 540 / 1081, 960, 540])],
                                       VOC, scale=(0.5, 540 / 1081), iou_threshold=0.999)
        self.assertEqual((score["true_positives"], score["false_negatives"]), (1, 0))

    def test_presence_only_and_explicit_empty_complete_boxes_remain_distinct(self):
        presence = evaluation.score_image({"id": "tv", "present": ["tv"]}, [], ("tv",))
        summary = evaluation.totals([presence])
        self.assertEqual(summary["annotation_coverage"], {
            "presence_checks": 1, "absence_checks": 0, "objects": 0, "images_with_complete_boxes": 0})
        self.assertIsNone(summary["box_recall"])
        self.assertNotIn("box_precision", summary)
        empty = evaluation.score_image({"id": "empty", "objects": [], "boxes_complete": True},
                                       [found("dog", [0, 0, 10, 10])], VOC)
        self.assertEqual(empty["false_positives"], 1)
        self.assertEqual(evaluation.totals([empty])["box_precision"], 0)


class ManifestTests(unittest.TestCase):
    def write(self, folder, manifest):
        path = Path(folder) / "manifest.json"
        path.write_text(json.dumps(manifest))
        return path

    def test_invalid_manifests_are_rejected(self):
        bad = ({"schema_version": 2, "images": []},
               {"schema_version": 1, "images": [{"id": "a", "path": "a.png"}, {"id": "a", "path": "b.png"}]},
               {"schema_version": 1, "images": [{"id": "a", "path": "a.png", "file": "a.png"}]},
               {"schema_version": 1, "images": [{"id": "a", "path": "a.png",
                                                 "objects": [{"label": "rug", "box_xyxy": [5, 0, 1, 1]}]}]})
        with tempfile.TemporaryDirectory() as folder:
            for manifest in bad:
                with self.subTest(manifest=manifest), self.assertRaises(evaluation.ManifestError):
                    evaluation.load_manifest(self.write(folder, manifest))

    def test_paths_resolve_relative_to_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = evaluation.load_manifest(self.write(folder, {"schema_version": 1, "images": [
                {"id": "local", "path": "stills/room.png"}, {"id": "remote", "file": "r.jpg", "url": "u"}]}))
            local, remote = manifest["images"]
            self.assertEqual(evaluation.resolve_image(manifest, local, "/downloads"), Path(folder) / "stills/room.png")
            self.assertEqual(evaluation.resolve_image(manifest, remote, "/downloads"), Path("/downloads/r.jpg"))

    def test_seed_manifest_is_valid_and_pinned(self):
        manifest = evaluation.load_manifest(ROOT / "evaluation/seed.json")
        for entry in manifest["images"]:
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertTrue(entry["url"].startswith("https://raw.githubusercontent.com/pjreddie/darknet/"))

    def test_malformed_annotations_and_digests_fail_before_scoring(self):
        invalid = ({"present": "tv"}, {"present": [[]]}, {"absent": [["dog"]]},
                   {"boxes_complete": "true"}, {"objects": [None]}, {"sha256": "unknown"},
                   {"objects": [{"label": "dog", "box_xyxy": [0, 0, float("inf"), 1]}]},
                   {"objects": [{"label": "dog", "box_xyxy": [0, 0, True, 1]}]},
                   {"objects": [{"label": "dog", "box_xyxy": [0, 0, 2, 2], "confusable": "cat"}]})
        with tempfile.TemporaryDirectory() as folder:
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaises(evaluation.ManifestError):
                    evaluation.load_manifest(self.write(folder, {"schema_version": 1, "images": [
                        {"id": "bad", "path": "generated.png", **fields}]}))


class ProvenanceTests(unittest.TestCase):
    def test_source_revision_reports_checkout_and_dirty_state(self):
        from scripts.evaluate_detectors import source_revision

        source = source_revision()
        if source["revision"] is None:
            self.assertTrue(source["error"])
            return
        self.assertRegex(source["revision"], r"^[0-9a-f]{40,64}$")
        self.assertIsInstance(source["working_tree_dirty"], bool)
        self.assertRegex(source["tracked_diff_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(source["working_tree_paths"], list)
        self.assertIn("framejacker/preprocess.py", source["source_files_sha256"])

    def test_detector_preprocessing_is_explicit(self):
        from scripts.evaluate_detectors import model_preprocessing

        ssd = model_preprocessing(SimpleNamespace(name="mobilenet-ssd"))
        self.assertEqual(ssd["input_size"], [300, 300])
        self.assertEqual(ssd["resize"], "stretch")
        self.assertEqual(ssd["color_order"], "BGR")
        yolox = model_preprocessing(SimpleNamespace(name="yolox-nano", size=416))
        self.assertEqual(yolox["input_size"], [416, 416])
        self.assertEqual(yolox["resize"], "letterbox_top_left")
        self.assertEqual(yolox["box_mapping"], "inverse realized x/y resize ratios")


@unittest.skipUnless(AVAILABLE and (ASSETS / "yolox_nano.onnx").is_file(), "Run scripts/setup_window.sh first")
class CommandTests(unittest.TestCase):
    def test_compares_detectors_on_local_stills_and_refuses_overwrite(self):
        from scripts import evaluate_detectors

        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "local.json"
            manifest.write_text(json.dumps({"schema_version": 1, "images": [{
                "id": "dog", "path": str(ASSETS / "dog.jpg"), "present": ["dog", "bicycle"], "absent": ["cat"],
                "objects": [{"label": "dog", "box_xyxy": [130, 205, 325, 540]}]}]}))
            report_path = Path(folder) / "report.json"
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                code = evaluate_detectors.main(["--manifest", str(manifest), "--report", str(report_path)])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            self.assertEqual(report["schema_version"], 3)
            self.assertTrue(report["code"]["revision"])
            self.assertIn("working_tree_dirty", report["code"])
            self.assertIn(str(manifest), report["manifest_sha256"])
            self.assertTrue(report["environment"]["opencv"])
            self.assertTrue(report["environment"]["numpy"])
            self.assertEqual(report["warmup"]["runs_per_detector"], 1)
            self.assertFalse(report["warmup"]["included_in_timing"])
            self.assertEqual(json.loads(report_path.read_text()), report)
            self.assertEqual(set(report["detectors"]), {"mobilenet-ssd", "yolox-nano"})
            for result in report["detectors"].values():
                self.assertEqual(result["totals"]["present_hits"], 2)
                self.assertEqual(result["totals"]["true_positives"], 1)
                self.assertGreater(result["totals"]["mean_inference_ms"], 0)
                self.assertTrue(result["runtime_version"])
                self.assertTrue(result["runtime_artifacts"])
                self.assertIn("preprocessing", result)
                self.assertTrue(all(len(item["sha256"]) == 64 for item in result["model_files"]))
                self.assertTrue(all(len(item["input_sha256"]) == 64 for item in result["images"]))
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                evaluate_detectors.main(["--manifest", str(manifest), "--report", str(report_path)])
            missing = Path(folder) / "missing.json"
            missing.write_text(json.dumps({"schema_version": 1, "images": [{"id": "x", "path": "absent.png"}]}))
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(evaluate_detectors.main(["--manifest", str(missing)]), 1)
            self.assertIn("not found", json.loads(output.getvalue())["error"])


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class PreprocessingAuditTests(unittest.TestCase):
    def test_model_inputs_retain_bgr_order_scaling_padding_and_inverse_geometry(self):
        import cv2
        import numpy as np
        from framejacker.detection import MobileNetSSD, YoloxDetector
        from scripts.evaluate_detectors import preprocessing_audit

        image = np.full((1081, 1920, 3), (10, 80, 240), dtype=np.uint8)
        from scripts.evaluate_detectors import prepare_image

        bgr = prepare_image(image)
        ssd = MobileNetSSD.__new__(MobileNetSSD)
        ssd.cv2 = cv2
        blob, _ = ssd.prepare_input(bgr)
        np.testing.assert_allclose(blob[0, :, 0, 0], (np.array([10, 80, 240]) - 127.5) * 0.007843,
                                   rtol=1e-6)
        trace = preprocessing_audit(ssd, image, bgr, {"stored_channels": 3})
        self.assertEqual(trace["processing_to_model"]["kind"], "stretch")
        self.assertEqual(trace["native_to_processing"]["scale_y"], 540 / 1081)
        self.assertEqual(trace["native_to_processing"]["crop_xyxy"], [0, 0, 1920, 1081])
        yolox = YoloxDetector.__new__(YoloxDetector)
        yolox.np, yolox.size = np, 416
        blob, transform = yolox.prepare_input(bgr)
        np.testing.assert_array_equal(blob[0, :, 0, 0], (10, 80, 240))
        np.testing.assert_array_equal(blob[0, :, -1, -1], (114, 114, 114))
        self.assertEqual(transform.to_source(0, 0, 416, 234), (0, 0, 959, 539))
        trace = preprocessing_audit(yolox, image, bgr, {"stored_channels": 3})
        self.assertEqual(trace["model_tensor"]["shape"], [1, 3, 416, 416])
        self.assertEqual(trace["processing_to_model"]["kind"], "letterbox_top_left")

    def test_padded_rgb_and_bgr_convert_identically_without_mixing_padding(self):
        import numpy as np
        from framejacker.convert import to_bgr
        from framejacker.frame import Frame

        width, height, stride = 1920, 1081, 1920 * 3 + 17
        data = bytearray([255]) * (height * stride)
        rgb = np.ndarray((height, width, 3), dtype=np.uint8, buffer=data, strides=(stride, 3, 1))
        rgb[:] = (240, 80, 10)
        actual = to_bgr(Frame(data, "RGB3", width, height, stride, "generated", 0, 1))
        expected = to_bgr(Frame(rgb[:, :, ::-1].copy(), "BGR24", width, height, width * 3, "generated", 0, 1))
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.shape, (540, 960, 3))

    def test_gray_file_replication_identity_digest_and_controlled_warmups(self):
        import cv2
        import numpy as np
        from framejacker.detection import MobileNetSSD
        from framejacker.frame import DetectionResult
        from scripts import evaluate_detectors as command

        detector = MobileNetSSD.__new__(MobileNetSSD)
        detector.cv2 = cv2
        calls = []

        def detect(bgr, threshold):
            calls.append((bgr.copy(), threshold))
            return DetectionResult((), 1.0)

        detector.detect = detect
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "gray.png"
            cv2.imwrite(str(image), np.arange(80, dtype=np.uint8).reshape(8, 10))
            manifest = Path(folder) / "manifest.json"
            digest = command.assets.checksum(image)
            manifest.write_text(json.dumps({"schema_version": 1, "images": [
                {"id": "generated-gray", "path": "gray.png", "sha256": digest, "present": ["tv"]}]}))
            with mock.patch.object(command, "create_detector", return_value=detector), \
                    mock.patch.object(command, "model_provenance", return_value={}), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                code = command.main(["--manifest", str(manifest), "--detector", "mobilenet-ssd",
                                     "--audit-preprocessing", "--audit-variant", "swap-rb",
                                     "--audit-variant", "gray-replicated", "--audit-variant", "native-size"])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            result = report["detectors"]["mobilenet-ssd"]
            self.assertEqual(len(calls), 8)  # One untimed warm-up + one measurement for each of four cases.
            self.assertTrue(all(threshold == 0.5 for _, threshold in calls))
            baseline = result["images"][0]
            self.assertEqual(baseline["input_sha256"], digest)
            self.assertEqual(baseline["input_digest_provenance"], "manifest_verified")
            trace = baseline["preprocessing_audit"]
            self.assertEqual(trace["decode"]["stored_channels"], 1)
            self.assertTrue(trace["decoded_channels_equal"])
            self.assertEqual(trace["decoded_bgr"]["sha256"], trace["processing_bgr"]["sha256"])
            for comparison in result["audit_variants"].values():
                self.assertEqual(comparison["warmup_runs"], 1)
                self.assertFalse(comparison["warmup_included_in_timing"])
                self.assertEqual(comparison["images"][0]["preprocessing_audit"]["model_tensor"],
                                 trace["model_tensor"])
            # A supplied digest is checked before any inference, including audit variants.
            manifest.write_text(manifest.read_text().replace(digest, "0" * 64))
            with mock.patch.object(command, "create_detector") as factory, \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(command.main(["--manifest", str(manifest)]), 1)
            factory.assert_not_called()
            self.assertIn("SHA-256", json.loads(output.getvalue())["error"])

    def test_reproduction_requires_reference_digest_when_requested(self):
        import cv2
        import numpy as np
        from scripts import evaluate_detectors as command

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "generated.png"
            cv2.imwrite(str(image), np.zeros((10, 10, 3), dtype=np.uint8))
            manifest = Path(folder) / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "images": [
                {"id": "unverified", "path": "generated.png"}]}))
            with self.assertRaisesRegex(ValueError, "independently supplied"):
                command.load_images([manifest], False, require_digests=True)
            self.assertEqual(len(command.load_images([manifest], False)), 1)


if __name__ == "__main__":
    unittest.main()
