"""The box-annotation window writes manifest entries through the manifest helper and touches nothing else.

The still set (the model) is tested without Qt. The window is driven in-process
with ``QT_QPA_PLATFORM=offscreen`` on generated stills: real mouse and key
events through ``QtTest``, so the drag-to-source-pixel path and the shortcuts
are the ones the owner uses on the Frame desktop.
"""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.commands import annotate_boxes  # noqa: E402

AVAILABLE = importlib.util.find_spec("PySide6") is not None


def still_report(image, width, height, **changes):
    report = {"report_kind": "steamvr_mirror_recognition", "status": "recognized", "exit_code": 0,
              "still_path": str(image.resolve()), "still_detector": "yolox-nano",
              "still_sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "still_source": "mirror:observed",
              "still_sequence": 7, "still_width": width, "still_height": height}
    report.update(changes)
    return report


class StillSetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "frame.json"

    def test_lists_only_the_stills_directly_in_the_directory_by_name(self):
        for name in ("room-b.png", "room-a.PNG", "room-c.jpg", "room-d.jpeg", "room-b.json", "notes.txt"):
            (self.root / name).write_bytes(b"x")
        (self.root / "invalid-20261103-220023").mkdir()
        (self.root / "invalid-20261103-220023/room-e.png").write_bytes(b"x")
        self.assertEqual([path.name for path in annotate_boxes.list_stills(self.root)],
                         ["room-a.PNG", "room-b.png", "room-c.jpg", "room-d.jpeg"])
        with self.assertRaises(ValueError):
            annotate_boxes.StillSet(self.root / "invalid-20261103-220023" / "none", self.manifest)

    def test_a_presence_entry_gains_boxes_in_place_and_only_changes_are_written(self):
        for name in ("room-a.png", "room-b.png"):
            (self.root / name).write_bytes(name.encode())
        self.manifest.write_text(json.dumps({"schema_version": 1, "images": [
            {"id": "room-b", "path": "room-b.png", "present": ["chair"], "absent": ["couch"]},
            {"id": "other", "path": "other.png", "present": [], "absent": []}]}))
        stills = annotate_boxes.StillSet(self.root, self.manifest)
        self.assertEqual(stills.path.name, "room-a.png")
        self.assertFalse(stills.save())  # Nothing drawn: nothing written.
        self.assertTrue(stills.move(1))
        self.assertEqual(stills.current["present"], ["chair"])
        self.assertEqual(stills.current["absent"], ["couch"])
        stills.add_box("chair", 10, 20, 110, 220)
        stills.add_box("tv", 300, 20, 400, 120)
        self.assertTrue(stills.undo())
        self.assertTrue(stills.undo())
        self.assertFalse(stills.undo())
        self.assertFalse(stills.changed(), "undoing every new box restores the loaded state")
        stills.add_box("tv", 300, 20, 400, 120)
        stills.set_complete(True)
        self.assertTrue(stills.save())
        self.assertFalse(stills.save())
        data = json.loads(self.manifest.read_text())
        self.assertEqual([entry["id"] for entry in data["images"]], ["room-b", "other"], "replaced in place")
        self.assertEqual(data["images"][0], {
            "id": "room-b", "path": "room-b.png", "present": ["chair", "tv"], "absent": ["couch"],
            "objects": [{"label": "tv", "box_xyxy": [300, 20, 400, 120]}], "boxes_complete": True})
        self.assertFalse(stills.move(1), "the last still saves and stays")
        self.assertEqual(stills.written, ["room-b"])
        with self.assertRaises(ValueError):
            stills.add_box("tv", 5, 5, 5, 9)  # Not a box: the helper's own validation.

    def test_a_boxed_label_listed_absent_is_refused_before_writing(self):
        (self.root / "room-a.png").write_bytes(b"x")
        stills = annotate_boxes.StillSet(self.root, self.manifest)
        stills.add_box("chair", 0, 0, 10, 10)
        stills.set_absent(["couch", " chair "])
        with self.assertRaisesRegex(ValueError, "chair.*absent"):
            stills.move(1)
        self.assertFalse(self.manifest.exists())
        self.assertEqual(stills.index, 0)
        stills.set_absent(["sofa"])
        self.assertTrue(stills.save())
        self.assertEqual(json.loads(self.manifest.read_text())["images"][0]["absent"], ["sofa"])

    def test_a_sibling_capture_report_pins_the_still_and_a_broken_one_is_refused(self):
        image = self.root / "room-a.png"
        image.write_bytes(b"x")
        report = self.root / "room-a.json"
        report.write_text(json.dumps(still_report(image, 400, 300)))
        stills = annotate_boxes.StillSet(self.root, self.manifest)
        self.assertEqual(stills.report_path(), report)
        stills.add_box("chair", 0, 0, 400, 300)
        self.assertTrue(stills.save())
        entry = json.loads(self.manifest.read_text())["images"][0]
        self.assertEqual(entry["sha256"], hashlib.sha256(b"x").hexdigest())
        self.assertEqual(entry["capture"]["sequence"], 7)
        self.assertTrue(stills.current["pinned"])
        stills.add_box("chair", 0, 0, 401, 300)  # Outside the recorded still size.
        with self.assertRaisesRegex(ValueError, "exceeds"):
            stills.save()
        stills.undo()
        stills.add_box("tv", 1, 1, 2, 2)
        report.write_text("{not json")
        with self.assertRaisesRegex(ValueError, "room-a.json"):
            stills.save()
        self.assertEqual(json.loads(self.manifest.read_text())["images"][0], entry, "a refused save writes nothing")

    def test_a_pinned_entry_is_not_rewritten_without_its_capture_report(self):
        """A rewrite through the helper pins from the report alone, so without one the pin would be lost."""
        image = self.root / "room-a.png"
        image.write_bytes(b"x")
        pinned = {"id": "room-a", "path": "room-a.png", "present": ["chair"], "absent": [],
                  "sha256": hashlib.sha256(b"x").hexdigest(),
                  "capture": {"detector": "yolox-nano", "source": "mirror:observed", "sequence": 7}}
        self.manifest.write_text(json.dumps({"schema_version": 1, "images": [pinned]}))
        stills = annotate_boxes.StillSet(self.root, self.manifest)
        self.assertTrue(stills.current["pinned"])
        stills.add_box("chair", 0, 0, 10, 10)
        with self.assertRaisesRegex(ValueError, "pinned.*room-a.json"):
            stills.save()
        self.assertEqual(json.loads(self.manifest.read_text())["images"], [pinned], "nothing written")
        (self.root / "room-a.json").write_text(json.dumps(still_report(image, 400, 300)))
        self.assertTrue(stills.save())
        entry = json.loads(self.manifest.read_text())["images"][0]
        self.assertEqual((entry["sha256"], entry["capture"]["sequence"], entry["objects"]),
                         (pinned["sha256"], 7, [{"label": "chair", "box_xyxy": [0, 0, 10, 10]}]))

    def test_the_command_refuses_a_missing_or_empty_directory_without_a_window(self):
        for directory in (self.root / "missing", self.root):
            with self.subTest(directory=directory.name), redirect_stdout(io.StringIO()) as output:
                code = annotate_boxes.main([str(directory)])
            report = json.loads(output.getvalue())
            self.assertEqual((code, report["status"], report["report_kind"]), (1, "error", "box_annotation"))
            self.assertIn("error", report)
            self.assertEqual(report["manifest"], str(directory / "frame.json"))


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for the annotation window test")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
        from PySide6 import QtCore, QtGui, QtWidgets
        from PySide6.QtTest import QTest

        cls.QtCore, cls.QtGui, cls.QtWidgets, cls.QTest = QtCore, QtGui, QtWidgets, QTest
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "frame.json"
        for name, (width, height), colour in (("room-a.png", (400, 300), (200, 80, 20)),
                                               ("room-b.png", (640, 360), (40, 80, 120)),
                                               ("room-c.png", (2000, 1500), (90, 140, 60))):  # Downscaled on screen.
            image = self.QtGui.QImage(width, height, self.QtGui.QImage.Format.Format_RGB888)
            image.fill(self.QtGui.QColor(*colour))
            self.assertTrue(image.save(str(self.root / name)))
        (self.root / "room-a.json").write_text(json.dumps(still_report(self.root / "room-a.png", 400, 300)))
        self.manifest.write_text(json.dumps({"schema_version": 1, "images": [
            {"id": "room-b", "path": "room-b.png", "present": ["chair"], "absent": ["couch"]}]}))
        self.before = self.listing()
        self.stills = annotate_boxes.StillSet(self.root, self.manifest)
        self.window = annotate_boxes.create_window(self.stills, "chair")
        self.addCleanup(self.window.close)
        self.window.resize(900, 700)
        self.window.show()
        self.QTest.qWaitForWindowExposed(self.window)

    def listing(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()
                if path.name != "frame.json"}

    def drag(self, x1, y1, x2, y2):
        """A left drag between two source-pixel points, through the canvas's own event handlers."""
        canvas, Qt = self.window.canvas, self.QtCore.Qt
        start, end = canvas.to_widget(x1, y1).toPoint(), canvas.to_widget(x2, y2).toPoint()
        self.QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        self.QTest.mouseMove(canvas, end)
        self.QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    def entries(self):
        return {entry["id"]: entry for entry in json.loads(self.manifest.read_text())["images"]}

    def test_drags_become_source_pixel_boxes_and_advancing_writes_them(self):
        Key = self.QtCore.Qt.Key
        canvas = self.window.canvas
        self.assertEqual((canvas.image.width(), canvas.image.height()), (400, 300))
        self.assertIn("1/3 room-a.png", self.window.status.text())
        self.drag(50, 60, 250, 200)
        self.drag(30, 30, 30, 30)  # A click is not a box.
        self.drag(390, 290, 460, 340)  # Beyond the still: clamped to its edge.
        boxes = self.stills.current["boxes"]
        self.assertEqual([obj["box_xyxy"] for obj in boxes], [[50, 60, 250, 200], [390, 290, 400, 300]])
        self.assertEqual({obj["label"] for obj in boxes}, {"chair"})
        self.QTest.keyClick(canvas, Key.Key_Z)  # Undo shortcut.
        self.assertEqual(len(boxes), 1)
        self.window.label_box.setCurrentText("tv")
        self.drag(300, 20, 380, 120)
        self.assertEqual(boxes[1], {"label": "tv", "box_xyxy": [300, 20, 380, 120]})
        self.window.undo_button.click()
        self.assertIn("unsaved", self.window.status.text())
        self.assertFalse(self.manifest.read_text().count("room-a"), "nothing is written before advancing")
        self.QTest.keyClick(canvas, Key.Key_Right)  # Next: saves, then shows the second still.
        entry = self.entries()["room-a"]
        self.assertEqual(entry["objects"], [{"label": "chair", "box_xyxy": [50, 60, 250, 200]}])
        self.assertEqual(entry["present"], ["chair"])
        self.assertEqual(entry["path"], "room-a.png")
        self.assertEqual(entry["sha256"], hashlib.sha256((self.root / "room-a.png").read_bytes()).hexdigest())
        self.assertNotIn("boxes_complete", entry)
        self.assertIn("2/3 room-b.png", self.window.status.text())
        self.assertEqual((canvas.image.width(), canvas.image.height()), (640, 360))
        self.assertEqual(self.window.absent_edit.text(), "couch")
        self.assertFalse(self.window.complete_box.isChecked())
        self.window.label_box.setCurrentText("chair")
        self.drag(100, 100, 500, 350)
        self.window.complete_box.setChecked(True)
        self.window.next_button.click()
        entries = json.loads(self.manifest.read_text())["images"]
        self.assertEqual([entry["id"] for entry in entries], ["room-b", "room-a"], "replaced in place, then appended")
        self.assertEqual(entries[0], {"id": "room-b", "path": "room-b.png", "present": ["chair"], "absent": ["couch"],
                                      "objects": [{"label": "chair", "box_xyxy": [100, 100, 500, 350]}],
                                      "boxes_complete": True})
        # The 2000 × 1500 still is shown smaller than life: a widget pixel is more than one source pixel.
        self.assertIn("3/3 room-c.png", self.window.status.text())
        scale, left, top = canvas.placement()
        self.assertLess(scale, 1)
        start, end = canvas.to_widget(400, 300).toPoint(), canvas.to_widget(1600, 1200).toPoint()
        expected = [round(value) for point in (start, end) for value in canvas.to_source(point)]
        self.drag(400, 300, 1600, 1200)
        box = self.stills.current["boxes"][0]["box_xyxy"]
        self.assertEqual(box, [expected[0], expected[1], expected[2], expected[3]])
        for value, intended in zip(box, (400, 300, 1600, 1200)):
            self.assertLessEqual(abs(value - intended), round(0.5 / scale) + 1, (box, scale))
        self.window.next_button.click()  # The last still: saved, still shown.
        self.assertIn("last still", self.window.status.text())
        self.assertEqual(self.stills.index, 2)
        self.assertEqual(self.entries()["room-c"]["objects"], [{"label": "chair", "box_xyxy": box}])
        self.QTest.keyClick(canvas, Key.Key_Left)
        self.QTest.keyClick(canvas, Key.Key_Left)
        self.assertEqual(self.stills.index, 0)
        self.assertEqual([obj["box_xyxy"] for obj in self.stills.current["boxes"]], [[50, 60, 250, 200]])
        self.assertEqual(self.listing(), self.before, "stills and reports are untouched; only the manifest changes")
        self.assertIsNone(self.window.error)

    def test_a_refused_save_keeps_the_still_on_screen_and_closing_saves(self):
        self.drag(50, 60, 250, 200)
        self.window.absent_edit.setText("chair")
        self.window.next_button.click()
        self.assertEqual(self.stills.index, 0)
        self.assertIn("ERROR", self.window.status.text())
        self.assertNotIn("room-a", self.entries())
        self.window.absent_edit.setText("couch")
        self.window.close()
        self.assertIsNone(self.window.error)
        entry = self.entries()["room-a"]
        self.assertEqual((entry["absent"], entry["objects"]), (["couch"], [{"label": "chair", "box_xyxy": [50, 60, 250, 200]}]))
        self.assertEqual(self.listing(), self.before)


if __name__ == "__main__":
    unittest.main()
