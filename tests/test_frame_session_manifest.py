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


if __name__ == "__main__":
    unittest.main()
