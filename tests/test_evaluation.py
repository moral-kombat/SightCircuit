"""Detector evaluation scoring, manifest validation and the comparison command."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from sightcircuit import assets, detection, evaluation
from sightcircuit.frame import Detection, DetectionResult
from tests.asset_gate import model_assets, requires_assets

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/sightcircuit/assets"
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
VOC = ("sofa", "chair", "dog", "car")
COUNTS = {"images": 6, "present_checks": 9, "present_hits": 7, "absence_checks": 22, "absent_violations": 2,
          "confusions": 0}


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
        self.assertEqual(score["box_outcomes"], {"rug": {"matched": 0, "missed": 0, "unsupported": 1},
                                                 "chair": {"matched": 1, "missed": 0, "unsupported": 0},
                                                 "dog": {"matched": 0, "missed": 1, "unsupported": 0}})
        summary = evaluation.totals([score, evaluation.score_image({"id": "x", "present": ["dog"]}, [], VOC)])
        self.assertEqual(summary["box_recall"], 0.5)
        self.assertEqual(summary["box_precision"], round(1 / 3, 4))
        self.assertEqual((summary["present_recall"], summary["confusions"]), (0.0, 1))
        self.assertEqual(summary["box_recall_by_label"], {
            "chair": {"true_positives": 1, "false_negatives": 0, "box_recall": 1.0},
            "dog": {"true_positives": 0, "false_negatives": 1, "box_recall": 0.0},
            "rug": {"true_positives": 0, "false_negatives": 0, "box_recall": None}})

    def test_per_label_box_recall_separates_the_poc_object_from_the_rest(self):
        chair = {"label": "chair", "box_xyxy": [0, 0, 100, 100]}
        scores = [evaluation.score_image({"id": "a", "objects": [chair, {"label": "dog", "box_xyxy": [200, 0, 300, 100]}]},
                                         [found("chair", [0, 0, 100, 100])], VOC),
                  evaluation.score_image({"id": "b", "objects": [chair, dict(chair, box_xyxy=[300, 0, 400, 100])]},
                                         [found("chair", [0, 0, 100, 100]), found("chair", [300, 0, 400, 100])], VOC),
                  evaluation.score_image({"id": "c", "objects": [chair]}, [found("chair", [50, 50, 150, 150])], VOC)]
        summary = evaluation.totals(scores)
        self.assertEqual((summary["true_positives"], summary["false_negatives"], summary["box_recall"]), (3, 2, 0.6))
        self.assertEqual(summary["box_recall_by_label"], {
            "chair": {"true_positives": 3, "false_negatives": 1, "box_recall": 0.75},
            "dog": {"true_positives": 0, "false_negatives": 1, "box_recall": 0.0}})
        table = evaluation.label_table(scores)
        self.assertEqual(table["boxes"], {"chair": {"matched": 3, "missed": 1, "unsupported": 0, "best_below_threshold": None},
                                          "dog": {"matched": 0, "missed": 1, "unsupported": 0, "best_below_threshold": None}})
        self.assertEqual((table["present"], table["absent"]), ({}, {}))
        lines = evaluation.format_label_table({"ssd": table}, 0.5)
        self.assertEqual([line.split()[:2] for line in lines[2:]], [["chair", "boxes"], ["dog", "boxes"]])
        self.assertIn("3/4 matched", lines[2])
        self.assertIn("0/1 matched", lines[3])
        presence = evaluation.label_table([evaluation.score_image({"id": "p", "present": ["dog"]}, [], VOC)])
        self.assertEqual(presence["boxes"], {})
        self.assertEqual(evaluation.totals([scores[2]])["box_recall_by_label"],
                         {"chair": {"true_positives": 0, "false_negatives": 1, "box_recall": 0.0}})

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

    def test_best_below_threshold_for_each_supported_expected_label(self):
        entry = {"id": "room", "present": ["dog", ["car", "truck"], "giraffe", "cat"], "absent": ["couch", "horse"]}
        candidates = [found("dog", [0, 0, 5, 5], 0.9), found("dog", [5, 5, 9, 9], 0.4), found("truck", [0, 0, 2, 2], 0.3),
                      found("car", [0, 0, 2, 2], 0.2), found("cat", [0, 0, 2, 2], 0.5),
                      found("sofa", [0, 0, 3, 3], 0.31), found("sofa", [0, 0, 3, 3], 0.12)]
        vocabulary = ("dog", "car", "truck", "cat", "sofa", "horse")
        self.assertEqual(evaluation.best_below_threshold(entry, candidates, vocabulary, 0.5), {
            "present": {"dog": 0.4, "car|truck": 0.3, "cat": None}, "absent": {"couch": 0.31, "horse": None}})

    def test_label_outcomes_and_per_label_table(self):
        vocabulary = ("dog", "horse", "sheep", "cat")
        first = evaluation.score_image({"id": "a", "present": ["dog", "horse", "giraffe"], "absent": ["sheep", "cat"]},
                                       [found("dog", [0, 0, 5, 5]), found("sheep", [0, 0, 5, 5], 0.6)], vocabulary)
        self.assertEqual(first["label_outcomes"], {
            "present": {"dog": "hit", "horse": "miss", "giraffe": "unsupported"},
            "absent": {"sheep": "violation", "cat": "clean"}})
        self.assertEqual(first["absent_clean"], ["cat"])
        # Below-threshold scores from a hit or a violation image are not near misses: the horse hit's 0.45 and
        # the sheep violation's 0.48 must not reach the table, only the horse miss and the clean cats.
        first["best_below_threshold"] = {"present": {"dog": None, "horse": 0.41}, "absent": {"sheep": 0.48, "cat": 0.2}}
        second = evaluation.score_image({"id": "b", "present": ["horse"], "absent": ["cat", "zebra"]},
                                        [found("horse", [0, 0, 5, 5])], vocabulary)
        second["best_below_threshold"] = {"present": {"horse": 0.45}, "absent": {"cat": 0.45}}
        table = evaluation.label_table([first, second])
        self.assertEqual(table["present"]["horse"], {"hit": 1, "miss": 1, "unsupported": 0, "best_below_threshold": 0.41})
        self.assertEqual(table["present"]["giraffe"], {"hit": 0, "miss": 0, "unsupported": 1,
                                                       "best_below_threshold": None})
        self.assertEqual(table["absent"]["cat"], {"clean": 2, "violation": 0, "unsupported": 0,
                                                  "best_below_threshold": 0.45})
        self.assertEqual(table["absent"]["sheep"], {"clean": 0, "violation": 1, "unsupported": 0,
                                                    "best_below_threshold": None})
        self.assertEqual(table["absent"]["zebra"]["unsupported"], 1)
        self.assertEqual(evaluation.totals([first, second])["absent_clean"], 2)
        lines = evaluation.format_label_table({"ssd": table, "yolox": evaluation.label_table([second])}, 0.5)
        self.assertIn("ssd", lines[1])
        self.assertIn("yolox", lines[1])
        horse = next(line for line in lines if line.startswith("horse") and "present" in line)
        self.assertIn("1/2 hit (best miss 0.410)", horse)
        giraffe = next(line for line in lines if line.startswith("giraffe"))
        self.assertIn("unsupported", giraffe)
        self.assertTrue(giraffe.endswith(" -"), giraffe)  # Never expected in the second detector's images.
        sheep = next(line for line in lines if line.startswith("sheep"))
        self.assertIn("0/1 clean", sheep)


class PrivateStillsTests(unittest.TestCase):
    """Private Frame stills and their manifests stay out of Git, as the mirror recognition guide promises."""

    def test_local_evaluation_directory_stays_out_of_git(self):
        self.assertIn("/evaluation/local/", (ROOT / ".gitignore").read_text().splitlines())
        # The rule as Git applies it, for the manifest, a still and a report under the directory.
        for path in ("evaluation/local/frame.json", "evaluation/local/room-20261107-120000.png",
                     "evaluation/local/wp1-e61-rerun-20261103/report.json"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", "--", path], cwd=ROOT, capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, f"{path} is not ignored: {result.stderr}")
        tracked = subprocess.run(["git", "ls-files", "--", "evaluation/local"], cwd=ROOT, check=True,
                                 capture_output=True, text=True, timeout=30).stdout
        self.assertEqual(tracked, "", f"private stills are tracked: {tracked}")


class PinTests(unittest.TestCase):
    """Committed count pins: regressions fail, improvements pass, scores never enter."""

    def pin(self, **detectors):
        return {"schema_version": 1, "manifest_sha256": "a" * 64, "threshold": 0.5, "iou_threshold": 0.5,
                "detectors": detectors}

    def test_pin_counts_are_discrete_totals(self):
        score = evaluation.score_image({"id": "a", "present": ["dog", "car", "giraffe"], "absent": ["sofa", "chair"]},
                                       [found("dog", [0, 0, 5, 5], 0.51234), found("chair", [0, 0, 5, 5])], VOC)
        self.assertEqual(evaluation.pin_counts(evaluation.totals([score])), {
            "images": 1, "present_checks": 2, "present_hits": 1, "absence_checks": 2, "absent_violations": 1,
            "confusions": 0})

    def test_regressions_fail_and_improvements_pass(self):
        pin = self.pin(ssd=dict(COUNTS), yolox=dict(COUNTS, present_checks=12, present_hits=12))
        yolox = dict(COUNTS, present_checks=12, present_hits=12)
        self.assertEqual(evaluation.check_pin(pin, {"ssd": dict(COUNTS), "yolox": yolox}),
                         {"status": "passed", "failures": [], "improvements": [], "unpinned": []})
        cases = ((dict(COUNTS, present_hits=6), "ssd present_hits 6 < pinned 7"),
                 (dict(COUNTS, absent_violations=3), "ssd absent_violations 3 > pinned 2"),
                 (dict(COUNTS, confusions=1), "ssd confusions 1 > pinned 0"),
                 (dict(COUNTS, present_checks=8), "ssd present_checks 8 != pinned 9"),
                 (dict(COUNTS, absence_checks=21), "ssd absence_checks 21 != pinned 22"),
                 (dict(COUNTS, images=5), "ssd images 5 != pinned 6"))
        for counts, failure in cases:
            with self.subTest(failure=failure):
                result = evaluation.check_pin(pin, {"ssd": counts, "yolox": yolox})
                self.assertEqual((result["status"], result["failures"]), ("regressed", [failure]))
        better = evaluation.check_pin(pin, {"ssd": dict(COUNTS, present_hits=8, absent_violations=1), "yolox": yolox,
                                            "third": dict(COUNTS)})
        self.assertEqual(better["status"], "passed")
        self.assertEqual(better["improvements"], ["ssd present_hits 8 > pinned 7", "ssd absent_violations 1 < pinned 2"])
        self.assertEqual(better["unpinned"], ["third"])
        missing = evaluation.check_pin(pin, {"ssd": dict(COUNTS)})
        self.assertEqual((missing["status"], missing["failures"]), ("regressed", ["yolox pinned but not evaluated"]))

    def test_invalid_pins_are_rejected(self):
        bad = ({"schema_version": 2}, {"detectors": {}}, {"manifest_sha256": "unknown"}, {"threshold": 0},
               {"iou_threshold": float("nan")}, {"unexpected": 1}, {"detectors": {"ssd": dict(COUNTS, images=True)}},
               {"detectors": {"ssd": dict(COUNTS, present_hits=-1)}},
               {"detectors": {"ssd": dict(COUNTS, present_hits=7.0)}},
               {"detectors": {"ssd": dict(COUNTS, scores=0.9)}},
               {"detectors": {"ssd": {key: value for key, value in COUNTS.items() if key != "confusions"}}},
               {"detectors": {"ssd": dict(COUNTS, present_hits=10)}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pin.json"
            path.write_text(json.dumps(self.pin(ssd=dict(COUNTS))))
            self.assertEqual(evaluation.load_pin(path)["detectors"], {"ssd": COUNTS})
            for fields in bad:
                with self.subTest(fields=fields), self.assertRaises(evaluation.PinError):
                    path.write_text(json.dumps({**self.pin(ssd=dict(COUNTS)), **fields}))
                    evaluation.load_pin(path)

    def test_pin_applies_only_to_its_manifest_threshold_and_iou(self):
        pin = self.pin(ssd=dict(COUNTS))
        evaluation.require_pin_applies(pin, ["a" * 64], 0.5, 0.5)
        for manifests, threshold, iou, message in ((["b" * 64], 0.5, 0.5, "manifest"),
                                                   (["a" * 64, "a" * 64], 0.5, 0.5, "one manifest"),
                                                   (["a" * 64], 0.4, 0.5, "threshold"),
                                                   (["a" * 64], 0.5, 0.3, "IoU")):
            with self.subTest(message=message), self.assertRaisesRegex(evaluation.PinError, message):
                evaluation.require_pin_applies(pin, manifests, threshold, iou)

    def test_committed_seed_pin_matches_the_seed_manifest_and_e73_counts(self):
        pin = evaluation.load_pin(ROOT / "evaluation/seed-expected.json")
        self.assertEqual(pin["manifest_sha256"], assets.checksum(ROOT / "evaluation/seed.json"))
        self.assertEqual((pin["threshold"], pin["iou_threshold"]), (0.5, 0.5))
        self.assertLessEqual(set(pin["detectors"]), set(detection.DETECTORS))
        self.assertEqual(pin["detectors"]["mobilenet-ssd"], COUNTS)
        self.assertEqual(pin["detectors"]["yolox-nano"], dict(COUNTS, present_checks=12, present_hits=12,
                                                               absence_checks=24, absent_violations=0))
        self.assertIn("not Frame evidence", pin["description"])


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

    def test_a_label_repeated_under_any_name_is_rejected(self):
        """One check per canonical label, so per-label tables, totals and pins count the same checks."""
        repeated = (({"present": ["tvmonitor", "tv"]}, "present.*tv"), ({"absent": ["sofa", "couch"]}, "absent.*couch"),
                    ({"present": [["car", "truck"], ["car", "truck"]]}, r"present.*car\|truck"),
                    ({"absent": ["dog", "dog"]}, "absent.*dog"))
        with tempfile.TemporaryDirectory() as folder:
            for fields, message in repeated:
                with self.subTest(fields=fields), self.assertRaisesRegex(evaluation.ManifestError, message):
                    evaluation.load_manifest(self.write(folder, {"schema_version": 1, "images": [
                        {"id": "repeated", "path": "generated.png", **fields}]}))
            # The same label may still be expected present in one entry and absent in another, or be one of
            # several alternatives next to its own separate check.
            manifest = evaluation.load_manifest(self.write(folder, {"schema_version": 1, "images": [
                {"id": "a", "path": "a.png", "present": ["tv", ["car", "truck"], "car"], "absent": ["couch"]},
                {"id": "b", "path": "b.png", "present": ["sofa"], "absent": ["tvmonitor"]}]}))
            self.assertEqual(len(manifest["images"]), 2)

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
        self.assertIn("sightcircuit/preprocess.py", source["source_files_sha256"])

    def test_detector_preprocessing_is_explicit(self):
        from sightcircuit.detection import preprocessing  # Owned by each detector class, not the evaluator.

        ssd = preprocessing("mobilenet-ssd")
        self.assertEqual(ssd["input_size"], [300, 300])
        self.assertEqual(ssd["resize"], "stretch")
        self.assertEqual(ssd["color_order"], "BGR")
        yolox = preprocessing("yolox-nano")
        self.assertEqual(yolox["input_size"], [416, 416])
        self.assertEqual(yolox["resize"], "letterbox_top_left")
        self.assertEqual(yolox["box_mapping"], "inverse realized x/y resize ratios")


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
@requires_assets(*model_assets())
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
            self.assertEqual(report["schema_version"], 5)
            self.assertTrue(report["code"]["revision"])
            self.assertIn("working_tree_dirty", report["code"])
            self.assertIn(str(manifest), report["manifest_sha256"])
            self.assertTrue(report["environment"]["opencv"])
            self.assertTrue(report["environment"]["numpy"])
            self.assertEqual(report["warmup"]["runs_per_detector"], 1)
            self.assertFalse(report["warmup"]["included_in_timing"])
            self.assertEqual(json.loads(report_path.read_text()), report)
            self.assertLessEqual({"mobilenet-ssd", "yolox-nano"}, set(report["detectors"]))
            self.assertEqual(set(report["detectors"]), set(detection.DETECTORS))  # Every registered detector.
            for result in report["detectors"].values():
                self.assertEqual(result["totals"]["present_hits"], 2)
                self.assertEqual(result["totals"]["true_positives"], 1)
                self.assertGreater(result["totals"]["mean_inference_ms"], 0)
                self.assertTrue(result["runtime_version"])
                self.assertTrue(result["runtime_artifacts"])
                self.assertIn("preprocessing", result)
                self.assertTrue(all(len(item["sha256"]) == 64 for item in result["model_files"]))
                self.assertTrue(all(len(item["input_sha256"]) == 64 for item in result["images"]))
                # Schema 5: the input path is the basename, so a device report carries no absolute path.
                self.assertEqual([item["input_path"] for item in result["images"]], ["dog.jpg"])
                self.assertNotIn(str(ASSETS), json.dumps(result["images"]))
                self.assertEqual(result["totals"]["box_recall_by_label"]["dog"]["box_recall"], 1.0)
                self.assertEqual(result["labels"]["boxes"]["dog"]["matched"], 1)
                self.assertEqual(result["labels"]["present"]["dog"]["hit"], 1)
                self.assertEqual(result["labels"]["absent"]["cat"]["clean"], 1)
                self.assertIn("cat", result["images"][0]["best_below_threshold"]["absent"])
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
        from sightcircuit.detection import MobileNetSSD, YoloxDetector
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
        yolox.np, yolox.size, yolox.name = np, 416, "yolox-nano"  # Every Detector has its registry name.
        blob, transform = yolox.prepare_input(bgr)
        np.testing.assert_array_equal(blob[0, :, 0, 0], (10, 80, 240))
        np.testing.assert_array_equal(blob[0, :, -1, -1], (114, 114, 114))
        self.assertEqual(transform.to_source(0, 0, 416, 234), (0, 0, 959, 539))
        trace = preprocessing_audit(yolox, image, bgr, {"stored_channels": 3})
        self.assertEqual(trace["model_tensor"]["shape"], [1, 3, 416, 416])
        self.assertEqual(trace["processing_to_model"]["kind"], "letterbox_top_left")
        self.assertEqual(trace["processing_to_model"]["inverse"], detection.preprocessing("yolox-nano")["box_mapping"])

    def test_audit_mapping_comes_from_the_detector_description(self):
        """A centered letterbox detector is audited as its description says, not as YOLOX's top-left."""
        import numpy as np
        from sightcircuit.preprocess import letterbox
        from scripts.evaluate_detectors import prepare_image, preprocessing_audit

        class Centered:
            name = "centered"

            @classmethod
            def preprocessing(cls, name):
                return {"input_size": [64, 64], "resize": "letterbox_center", "interpolation": "INTER_LINEAR",
                        "color_order": "BGR", "layout": "HWC", "dtype": "uint8", "mean": [0, 0, 0], "scale": 1,
                        "padding_value": 0, "box_mapping": "subtract centered padding, divide by realized scale"}

            def prepare_input(self, bgr):
                return letterbox(bgr, 64, pad_value=0, center=True)

        image = np.full((90, 160, 3), 60, dtype=np.uint8)
        bgr = prepare_image(image)
        with mock.patch.dict(detection.DETECTORS, {"centered": Centered}):
            mapping = preprocessing_audit(Centered(), image, bgr, {})["processing_to_model"]
        self.assertEqual((mapping["kind"], mapping["inverse"]),
                         ("letterbox_center", "subtract centered padding, divide by realized scale"))
        self.assertEqual((mapping["pad_x"], mapping["pad_y"]), (0, 14))

    def test_padded_rgb_and_bgr_convert_identically_without_mixing_padding(self):
        import numpy as np
        from sightcircuit.convert import to_bgr
        from sightcircuit.frame import Frame

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
        from sightcircuit.detection import MobileNetSSD
        from sightcircuit.frame import DetectionResult
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
            # Per case: one untimed warm-up and one measurement at 0.5, then one untimed diagnostic call
            # at the below-threshold floor that is never scored.
            floor = command.evaluation.BELOW_THRESHOLD_FLOOR
            self.assertEqual([threshold for _, threshold in calls], [0.5, 0.5, floor] * 4)
            self.assertEqual(report["below_threshold"]["floor"], floor)
            self.assertEqual(result["images"][0]["best_below_threshold"], {"present": {"tv": None}, "absent": {}})
            self.assertEqual(result["labels"]["present"]["tv"]["miss"], 1)
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


class FakeSsdNet:
    """The Caffe network's place: a fixed DetectionOutput for whatever blob arrives."""

    def __init__(self, rows):
        import numpy as np

        self.output = np.array(rows, dtype=np.float32).reshape(1, 1, -1, 7)

    def setInput(self, blob):
        assert blob.shape == (1, 3, 300, 300)

    def forward(self):
        return self.output


class ThirdDetector:
    """A new registry entry: only detection.py (and its asset entry) would change to add it."""
    runtime, classes = "opencv-dnn", ("dog", "bicycle", "car", "cat")

    def __init__(self, name, **options):
        self.name = name

    @classmethod
    def model_files(cls, name):
        return ("third-detector.bin",)

    @classmethod
    def preprocessing(cls, name):
        return {"input_size": [64, 64], "resize": "stretch", "interpolation": "INTER_AREA", "color_order": "RGB",
                "layout": "NHWC", "dtype": "uint8", "mean": [0, 0, 0], "scale": 1, "padding_value": None,
                "box_mapping": "fixture"}

    def detect(self, bgr, threshold):
        candidates = (Detection("dog", 0.9, (1, 1, 5, 5)), Detection("bicycle", 0.8, (1, 1, 5, 5)),
                      Detection("car", 0.7, (1, 1, 5, 5)), Detection("cat", 0.3, (1, 1, 5, 5)))
        return DetectionResult(tuple(item for item in candidates if item.confidence >= threshold), 1.0)


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class DetectorOwnershipCommandTests(unittest.TestCase):
    def write_still(self, folder, **fields):
        import cv2
        import numpy as np

        cv2.imwrite(str(Path(folder) / "scene.png"), np.full((120, 160, 3), 90, dtype=np.uint8))
        manifest = Path(folder) / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "images": [{"id": "generated", "path": "scene.png",
                                                                         **fields}]}))
        return manifest

    def test_a_registered_third_detector_reaches_the_evaluator_and_smoke_check(self):
        import numpy as np
        from scripts import evaluate_detectors as command
        from scripts import smoke_check

        with tempfile.TemporaryDirectory() as folder:
            manifest = self.write_still(folder, present=["dog", "bicycle"], absent=["cat"])
            (Path(folder) / "third-detector.bin").write_bytes(b"third weights")
            with mock.patch.dict(detection.DETECTORS, {"third": ThirdDetector}, clear=True), \
                    mock.patch.object(assets, "ASSETS", Path(folder)), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                code = command.main(["--manifest", str(manifest)])
                detectors, checked = smoke_check.check_detectors(np.zeros((8, 8, 3), dtype=np.uint8))
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            result = report["detectors"]["third"]
            self.assertEqual(set(report["detectors"]), {"third"})
            self.assertEqual(result["preprocessing"], ThirdDetector.preprocessing("third"))
            self.assertEqual(result["model_files"], [{"name": "third-detector.bin",
                                                      "sha256": assets.checksum(Path(folder) / "third-detector.bin")}])
            self.assertEqual(result["totals"]["present_hits"], 2)
            self.assertEqual(result["images"][0]["best_below_threshold"], {
                "present": {"dog": None, "bicycle": None}, "absent": {"cat": 0.3}})
            self.assertIn("third", errors.getvalue())
            self.assertEqual(list(detectors), ["third"])
            self.assertEqual(checked["third"]["labels"], ["bicycle", "car", "dog"])

    def test_smoke_check_requires_each_fixture_label_a_detector_can_name(self):
        import numpy as np
        from scripts import smoke_check

        class Blind(ThirdDetector):
            def detect(self, bgr, threshold):
                return DetectionResult((Detection("dog", 0.9, (1, 1, 5, 5)),), 1.0)

        with mock.patch.dict(detection.DETECTORS, {"blind": Blind}, clear=True), \
                self.assertRaisesRegex(RuntimeError, "blind.*bicycle"):
            smoke_check.check_detectors(np.zeros((8, 8, 3), dtype=np.uint8))

    def ssd(self, rows):
        import cv2
        import numpy as np

        detector = detection.MobileNetSSD.__new__(detection.MobileNetSSD)
        detector.cv2, detector.np, detector.net = cv2, np, FakeSsdNet(rows)
        return detector

    def test_label_map_break_fails_the_pinned_check(self):
        """Done condition, model-free: swapping two VOC labels turns pinned counts into a failing exit."""
        from scripts import evaluate_detectors as command

        dog, car = detection.CLASSES.index("dog"), detection.CLASSES.index("car")
        detector = self.ssd([[0, dog, 0.9, 0.1, 0.1, 0.5, 0.5], [0, car, 0.8, 0.5, 0.5, 0.9, 0.9]])
        with tempfile.TemporaryDirectory() as folder:
            manifest = self.write_still(folder, present=["dog", "car"], absent=["cat"])
            pin = Path(folder) / "pin.json"
            pin.write_text(json.dumps({
                "schema_version": 1, "description": "generated", "manifest_sha256": assets.checksum(manifest),
                "threshold": 0.5, "iou_threshold": 0.5, "detectors": {"mobilenet-ssd": {
                    "images": 1, "present_checks": 2, "present_hits": 2, "absence_checks": 1, "absent_violations": 0,
                    "confusions": 0}}}))
            arguments = ["--manifest", str(manifest), "--detector", "mobilenet-ssd", "--expect", str(pin)]

            def run():
                with mock.patch.object(command, "create_detector", return_value=detector), \
                        mock.patch.object(command, "model_provenance", return_value={}), \
                        redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                    code = command.main(arguments)
                return code, json.loads(output.getvalue()), errors.getvalue()

            code, report, errors = run()
            self.assertEqual(code, 0, report)
            self.assertEqual(report["expectation"]["status"], "passed")
            self.assertEqual(report["expectation"]["pin_sha256"], assets.checksum(pin))
            self.assertIn("passed", errors)
            swapped = list(detection.CLASSES)
            swapped[dog], swapped[detection.CLASSES.index("cat")] = "cat", "dog"
            with mock.patch.object(detection, "CLASSES", swapped):
                code, report, errors = run()
            self.assertEqual((code, report["status"]), (1, "evaluated"))
            self.assertEqual(report["expectation"]["status"], "regressed")
            self.assertEqual(report["expectation"]["failures"], ["mobilenet-ssd present_hits 1 < pinned 2",
                                                                 "mobilenet-ssd absent_violations 1 > pinned 0"])
            self.assertIn("REGRESSED", errors)

    def test_expect_refuses_another_manifest_or_threshold_before_inference(self):
        from scripts import evaluate_detectors as command

        with tempfile.TemporaryDirectory() as folder:
            manifest = self.write_still(folder, present=["dog"])
            other = Path(folder) / "other.json"
            other.write_text(manifest.read_text().replace("generated", "other"))
            pin = Path(folder) / "pin.json"
            pin.write_text(json.dumps({"schema_version": 1, "manifest_sha256": assets.checksum(manifest),
                                       "threshold": 0.5, "iou_threshold": 0.5,
                                       "detectors": {"mobilenet-ssd": dict(COUNTS)}}))
            for arguments, message in ((["--manifest", str(other)], "manifest"),
                                       (["--manifest", str(manifest), "--threshold", "0.4"], "threshold")):
                with self.subTest(message=message), mock.patch.object(command, "create_detector") as factory, \
                        redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                    self.assertEqual(command.main([*arguments, "--expect", str(pin)]), 1)
                factory.assert_not_called()
                self.assertIn(message, json.loads(output.getvalue())["error"])
            with mock.patch.object(command, "create_detector") as factory, \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(command.main(["--expect", str(Path(folder) / "absent.json")]), 1)
            factory.assert_not_called()


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
@requires_assets(*model_assets())
class RegistryGrowthTests(unittest.TestCase):
    """A real third detector registered beside the current ones needs no edit outside detection.py."""

    def test_registry_dependent_tests_and_smoke_wiring_accept_a_real_third_detector(self):
        import cv2
        from scripts import smoke_check
        from tests.test_detection import AssetTests, DetectorOwnershipTests

        # Another registry entry for the pinned YOLOX-Nano file: real inference, no new download.
        third = type("YoloxCopy", (detection.YoloxDetector,), {"MODELS": {"yolox-copy": ("yolox_nano.onnx", 416)}})
        cases = [CommandTests("test_compares_detectors_on_local_stills_and_refuses_overwrite"),
                 AssetTests("test_absent_downloads_skip_with_their_names"),
                 AssetTests("test_registered_detectors_own_their_pinned_model_files"),
                 DetectorOwnershipTests("test_detectors_describe_their_preprocessing")]
        result = unittest.TestResult()
        with mock.patch.dict(detection.DETECTORS, {"yolox-copy": third}):
            unittest.TestSuite(cases).run(result)
            _, checked = smoke_check.check_detectors(cv2.imread(str(ASSETS / "dog.jpg")))
        problems = [f"{case}: {trace}" for case, trace in (*result.failures, *result.errors)]
        self.assertEqual((problems, result.skipped, result.testsRun), ([], [], len(cases)))
        self.assertEqual(list(checked), [*(name for name in detection.DETECTORS), "yolox-copy"])
        self.assertEqual(checked["yolox-copy"]["labels"], checked["yolox-nano"]["labels"])


class SmokeCheckOrderTests(unittest.TestCase):
    def test_previous_readiness_is_invalidated_before_sightcircuit_is_imported(self):
        """A broken sightcircuit import must not leave the last passing readiness report in place."""
        import os
        import subprocess
        import sys

        program = (
            "import importlib.abc, importlib.util, sys\n"
            "from pathlib import Path\n"
            "class Broken(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'sightcircuit':\n"
            "            raise ImportError('generated sightcircuit import failure')\n"
            "sys.meta_path.insert(0, Broken())\n"
            "spec = importlib.util.spec_from_file_location('smoke_check', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "try:\n"
            "    spec.loader.exec_module(module)\n"
            "    module.RESULTS = Path(sys.argv[2])\n"
            "    module.main()\n"
            "except ImportError as error:\n"
            "    sys.exit(f'import failed: {error}')\n")
        with tempfile.TemporaryDirectory() as folder:
            results = Path(folder) / "smoke"
            results.mkdir()
            report = results / "readiness.json"
            report.write_text(json.dumps({"status": "passed"}))
            completed = subprocess.run(
                [sys.executable, "-I", "-c", program, str(ROOT / "scripts/smoke_check.py"), str(results)],
                capture_output=True, text=True, timeout=120, cwd=folder,
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "XDG_CACHE_HOME": folder})
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertIn("import failed", completed.stderr)
            self.assertFalse(report.exists(), "a stale passing readiness report survived a failed run")


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for OpenCV checks")
class MirrorPreviewVariantTests(unittest.TestCase):
    def test_variant_is_the_mirror_check_grayscale_sample_replicated(self):
        """E61's detector input: grayscale_sample's every-third-pixel integer luma, in three channels."""
        import numpy as np
        from sightcircuit.sources.mirror_device import grayscale_sample, validate_format
        from scripts.evaluate_detectors import AUDIT_VARIANTS, prepare_image

        self.assertIn("mirror-preview", AUDIT_VARIANTS)
        image = np.random.default_rng(61).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
        width, height, gray = grayscale_sample(image.tobytes(), validate_format(1920, 1080, "BGR3", 5760, image.nbytes),
                                               640)
        variant = prepare_image(image, "mirror-preview")
        self.assertEqual((variant.shape, variant.dtype), ((height, width, 3), np.uint8))
        self.assertEqual((width, height), (640, 360))
        for channel in range(3):
            self.assertEqual(np.ascontiguousarray(variant[:, :, channel]).tobytes(), gray)
        sampled = image[::3, ::3].astype(np.uint32)  # Independently: every third pixel, integer BT.601 luma.
        luma = (77 * sampled[:, :, 2] + 150 * sampled[:, :, 1] + 29 * sampled[:, :, 0]) >> 8
        np.testing.assert_array_equal(variant[:, :, 0], luma.astype(np.uint8))

    def test_variant_runs_through_the_command_with_its_own_results(self):
        import cv2
        import numpy as np
        from sightcircuit.sources.mirror_device import grayscale_sample, validate_format
        from scripts import evaluate_detectors as command

        seen = []
        recording = detection.MobileNetSSD.__new__(detection.MobileNetSSD)
        recording.cv2 = cv2

        def detect(bgr, threshold):
            seen.append(bgr.copy())
            return ThirdDetector("third").detect(bgr, threshold)

        recording.detect = detect

        with tempfile.TemporaryDirectory() as folder:
            pixels = np.random.default_rng(1).integers(0, 256, (90, 1300, 3), dtype=np.uint8)
            cv2.imwrite(str(Path(folder) / "wide.png"), pixels)
            manifest = Path(folder) / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "images": [
                {"id": "wide", "path": "wide.png", "present": ["dog"]}]}))
            with mock.patch.object(command, "create_detector", return_value=recording), \
                    mock.patch.object(command, "model_provenance", return_value={}), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                code = command.main(["--manifest", str(manifest), "--detector", "mobilenet-ssd",
                                     "--audit-variant", "mirror-preview"])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            comparison = report["detectors"]["mobilenet-ssd"]["audit_variants"]["mirror-preview"]
            self.assertIn("grayscale_sample", comparison["change"])
            self.assertEqual(comparison["images"][0]["processing_width"], 434)  # ceil(1300 / 3)
            self.assertEqual(comparison["labels"]["present"]["dog"]["hit"], 1)
            _, _, gray = grayscale_sample(pixels.tobytes(), validate_format(1300, 90, "BGR3", 3900, pixels.nbytes), 640)
            self.assertEqual(np.ascontiguousarray(seen[-1][:, :, 1]).tobytes(), gray)


if __name__ == "__main__":
    unittest.main()
