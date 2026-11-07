from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import frame_session_manifest as manifest  # noqa: E402


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "frame.json"
        self.image = self.root / "room.png"
        self.image.write_bytes(b"x")

    def test_creates_and_appends_with_relative_paths(self):
        manifest.add_image(self.path, "rug", self.image, ["chair"], ["couch"])
        manifest.add_image(self.path, "tv", self.image, [], [])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual([entry["id"] for entry in data["images"]], ["rug", "tv"])
        self.assertEqual(data["images"][0], {"id": "rug", "path": "room.png",
                                             "present": ["chair"], "absent": ["couch"]})

    def test_duplicate_id_is_rejected_and_file_unchanged(self):
        manifest.add_image(self.path, "rug", self.image, [], [])
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            manifest.add_image(self.path, "rug", self.image, [], [])
        self.assertEqual(self.path.read_text(), before)

    def still_report(self, **changes):
        report = {"report_kind": "steamvr_mirror_recognition", "status": "recognized", "exit_code": 0,
                  "still_path": str(self.image.resolve()), "still_detector": "yolox-nano",
                  "still_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                  "still_source": "mirror:observed", "still_sequence": 41}
        report.update(changes)
        return report

    def test_still_report_pins_sha256_detector_source_and_sequence(self):
        manifest.add_image(self.path, "rug", self.image, ["chair"], ["couch"], self.still_report())
        self.assertEqual(json.loads(self.path.read_text())["images"][0], {
            "id": "rug", "path": "room.png", "sha256": hashlib.sha256(b"x").hexdigest(),
            "present": ["chair"], "absent": ["couch"],
            "capture": {"detector": "yolox-nano", "source": "mirror:observed", "sequence": 41}})

    def test_mismatched_or_incomplete_still_report_is_refused(self):
        other = self.root / "other.png"
        other.write_bytes(b"y")
        cases = {"digest": dict(still_sha256=hashlib.sha256(b"y").hexdigest()),
                 "invalid digest": dict(still_sha256="X" * 64), "path": dict(still_path=str(other)),
                 "status": dict(status="stale_input"), "exit": dict(exit_code=1), "kind": dict(report_kind="other"),
                 "detector": dict(still_detector=None), "source": dict(still_source=""),
                 "sequence": dict(still_sequence=True), "missing": dict(still_path=None)}
        for name, changes in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    manifest.add_image(self.path, name, self.image, [], [], self.still_report(**changes))
                self.assertFalse(self.path.exists())

    def test_command_reads_the_still_report(self):
        report = self.root / "still-rug.json"
        report.write_text(json.dumps(self.still_report()))
        manifest.main(["--manifest", str(self.path), "--id", "rug", "--image", str(self.image),
                       "--still-report", str(report)])
        self.assertEqual(json.loads(self.path.read_text())["images"][0]["capture"]["sequence"], 41)
        report.write_text("{not json")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            manifest.main(["--manifest", str(self.path), "--id", "tv", "--image", str(self.image),
                           "--still-report", str(report)])
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(len(json.loads(self.path.read_text())["images"]), 1)

    def test_split_trims_and_drops_empty(self):
        self.assertEqual(manifest.split(" chair, ,dining table,"), ["chair", "dining table"])

    def test_objects_are_parsed_from_label_and_source_pixel_corners(self):
        self.assertEqual(manifest.parse_object("chair:10,20,300,400"), {"label": "chair", "box_xyxy": [10, 20, 300, 400]})
        self.assertEqual(manifest.parse_object(" dining table : 0.5,1,2,3"),
                         {"label": "dining table", "box_xyxy": [0.5, 1, 2, 3]})
        for text in ("chair", "chair:1,2,3", "chair:1,2,3,4,5", ":1,2,3,4", "chair:5,5,5,9", "chair:0,9,5,5",
                     "chair:-1,0,2,3", "chair:a,0,2,3", "chair:nan,0,2,3", "chair:inf,0,2,3"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                manifest.parse_object(text)

    def test_boxes_and_completeness_are_written_only_when_given(self):
        chair = {"label": "chair", "box_xyxy": [10, 20, 300, 400]}
        manifest.add_image(self.path, "presence", self.image, ["chair"], [])
        manifest.add_image(self.path, "boxed", self.image, ["chair"], [], objects=[chair])
        manifest.add_image(self.path, "complete", self.image, ["chair"], [], objects=[chair], boxes_complete=True)
        manifest.add_image(self.path, "empty-complete", self.image, [], ["chair"], boxes_complete=True)
        entries = {entry["id"]: entry for entry in json.loads(self.path.read_text())["images"]}
        self.assertEqual(entries["presence"], {"id": "presence", "path": "room.png", "present": ["chair"], "absent": []})
        self.assertEqual(entries["boxed"], {"id": "boxed", "path": "room.png", "present": ["chair"], "absent": [],
                                            "objects": [chair]})
        self.assertEqual(entries["complete"], {"id": "complete", "path": "room.png", "present": ["chair"], "absent": [],
                                               "objects": [chair], "boxes_complete": True})
        self.assertEqual(entries["empty-complete"], {"id": "empty-complete", "path": "room.png", "present": [],
                                                     "absent": ["chair"], "objects": [], "boxes_complete": True})

    def test_boxes_must_fit_the_still_the_report_recorded(self):
        report = self.still_report(still_width=1280, still_height=720)
        inside, wide, tall = ({"label": "chair", "box_xyxy": box} for box in
                              ([0, 0, 1280, 720], [100, 100, 1281, 300], [100, 100, 300, 721]))
        manifest.add_image(self.path, "inside", self.image, ["chair"], [], report, [inside], True)
        entry = json.loads(self.path.read_text())["images"][0]
        self.assertEqual((entry["objects"], entry["boxes_complete"], entry["sha256"]),
                         ([inside], True, hashlib.sha256(b"x").hexdigest()))
        for name, obj in (("wide", wide), ("tall", tall)):
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, "exceeds the 1280x720 still"):
                manifest.add_image(self.path, name, self.image, [], [], report, [obj])
        self.assertEqual(len(json.loads(self.path.read_text())["images"]), 1)
        # A report without a recorded size (not one mirror_recognize writes) cannot check, so it does not refuse.
        manifest.add_image(self.path, "unsized", self.image, [], [], self.still_report(), [wide])

    def test_command_records_objects_and_completeness(self):
        manifest.main(["--manifest", str(self.path), "--id", "two", "--image", str(self.image), "--present", "chair",
                       "--object", "chair:10,20,300,400", "--object", "chair:400,20,700,400", "--boxes-complete"])
        entry = json.loads(self.path.read_text())["images"][0]
        self.assertEqual(entry["objects"], [{"label": "chair", "box_xyxy": [10, 20, 300, 400]},
                                            {"label": "chair", "box_xyxy": [400, 20, 700, 400]}])
        self.assertTrue(entry["boxes_complete"])
        manifest.main(["--manifest", str(self.path), "--id", "plain", "--image", str(self.image), "--present", "chair"])
        self.assertNotIn("objects", json.loads(self.path.read_text())["images"][1])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            manifest.main(["--manifest", str(self.path), "--id", "bad", "--image", str(self.image),
                           "--object", "chair:10,20,5,400"])
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(len(json.loads(self.path.read_text())["images"]), 2)


if __name__ == "__main__":
    unittest.main()
