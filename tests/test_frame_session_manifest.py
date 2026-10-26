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

    def test_split_trims_and_drops_empty(self):
        self.assertEqual(manifest.split(" chair, ,dining table,"), ["chair", "dining table"])


if __name__ == "__main__":
    unittest.main()
