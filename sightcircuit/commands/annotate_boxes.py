"""Draw evaluation boxes on the stills of a directory and write them into the session manifest.

A PySide6 window (the replay window's Qt, in ``.venv-replay``) shows the stills of one
directory one at a time. Drag a rectangle to box an object under the selected canonical
label; boxes are kept in the still's own pixels. Moving to the next or previous still, or
closing the window, writes the still's entry into the manifest through the manifest
helper (``frame_session_manifest.add_image``, the ``--object`` and ``--boxes-complete``
path), under the still's file stem as its id, the name the session procedure uses. A
sibling ``<stem>.json`` capture report from ``mirror_recognize --still`` pins the still's
SHA-256 exactly as ``--still-report`` does. Stills are only read; nothing but the manifest
is written.
"""
import argparse
import json
import os
from pathlib import Path
import sys

if not __package__:  # Run as a file: import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sightcircuit.commands.frame_session_manifest import add_image, parse_object
from sightcircuit.labels import COCO_CLASSES, canonical

ROOT = Path(__file__).resolve().parents[2]
STILL_SUFFIXES = (".png", ".jpg", ".jpeg")
MIN_BOX_SIDE = 2  # Source pixels; a shorter drag is a click, not a box.


def list_stills(directory):
    """The still images directly in ``directory``, by name; subdirectories (invalid runs) are not entered."""
    directory = Path(directory)
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix.lower() in STILL_SUFFIXES)


def manifest_entries(manifest):
    """``{id: entry}`` of an existing manifest; an absent manifest is empty."""
    manifest = Path(manifest)
    if not manifest.exists():
        return {}
    data = json.loads(manifest.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("images"), list):
        raise ValueError("Unsupported manifest")
    return {entry.get("id"): entry for entry in data["images"] if isinstance(entry, dict)}


def canonical_names(labels):
    """Canonical names of a ``present`` list, whose items may be lists of alternatives."""
    names = set()
    for label in labels:
        for item in label if isinstance(label, list) else [label]:
            if isinstance(item, str):
                names.add(canonical(item))
    return names


def snapshot(state):
    """The parts of a still's state that a save writes, for change detection."""
    return json.dumps({key: state[key] for key in ("boxes", "boxes_complete", "absent")}, sort_keys=True)


class StillSet:
    """The stills of a directory, their boxes, and the manifest writes.

    One still's state is its ``boxes`` (manifest ``objects`` entries), ``boxes_complete``,
    the ``present`` and ``absent`` lists (loaded from an existing entry, as the session
    script writes them) and whether the entry is ``pinned`` by a still report. ``save``
    writes a changed still through ``add_image`` with ``replace``, so a presence-only
    entry gains its boxes and keeps its position.
    """
    def __init__(self, directory, manifest):
        self.directory = Path(directory)
        self.manifest = Path(manifest)
        if not self.directory.is_dir():
            raise ValueError(f"Not a directory: {self.directory}")
        self.stills = list_stills(self.directory)
        if not self.stills:
            raise ValueError(f"No stills ({', '.join(STILL_SUFFIXES)}) in {self.directory}")
        self.index = 0
        self.states = {}
        self.written = []

    @property
    def path(self):
        return self.stills[self.index]

    @property
    def current(self):
        return self.state(self.index)

    def state(self, index):
        if index not in self.states:
            still = self.stills[index]
            entry = manifest_entries(self.manifest).get(still.stem, {})
            state = {"boxes": [dict(obj) for obj in entry.get("objects", []) if isinstance(obj, dict)],
                     "boxes_complete": bool(entry.get("boxes_complete", False)),
                     "present": list(entry.get("present", [])), "absent": list(entry.get("absent", [])),
                     "pinned": "sha256" in entry, "in_manifest": bool(entry)}
            state["saved"] = snapshot(state)
            self.states[index] = state
        return self.states[index]

    def changed(self, state=None):
        """Whether the still differs from what the manifest holds (undoing every new box is no change)."""
        state = self.current if state is None else state
        return snapshot(state) != state["saved"]

    def report_path(self):
        """The capture report saved beside the still by the session procedure, when there is one."""
        candidate = self.path.with_suffix(".json")
        return candidate if candidate.is_file() else None

    def add_box(self, label, x1, y1, x2, y2):
        """One box in source pixels, validated as ``--object label:x1,y1,x2,y2`` would be."""
        obj = parse_object(f"{label}:{x1},{y1},{x2},{y2}")
        self.current["boxes"].append(obj)
        return obj

    def undo(self):
        """Remove the last box of the current still; False when there was none."""
        if not self.current["boxes"]:
            return False
        self.current["boxes"].pop()
        return True

    def set_complete(self, flag):
        self.current["boxes_complete"] = bool(flag)

    def set_absent(self, labels):
        self.current["absent"] = [label.strip() for label in labels if label.strip()]

    def save(self):
        """Write the current still's entry if it changed; True when a write happened.

        ``present`` keeps the loaded labels and gains each boxed label it lacks. A boxed
        label that is also listed absent is a contradiction and is refused before writing.
        """
        state = self.current
        if not self.changed(state):
            return False
        present = list(state["present"])
        names = canonical_names(present)
        for obj in state["boxes"]:
            if canonical(obj["label"]) not in names:
                present.append(obj["label"])
                names.add(canonical(obj["label"]))
        conflicts = sorted(names & {canonical(label) for label in state["absent"]})
        if conflicts:
            raise ValueError(f"{', '.join(conflicts)} is boxed or present and also listed absent")
        report = None
        report_path = self.report_path()
        if report_path is not None:
            try:
                report = json.loads(report_path.read_text())
            except (OSError, ValueError) as error:
                raise ValueError(f"Still report {report_path.name} could not be read: {error}") from None
            if not isinstance(report, dict):
                raise ValueError(f"Still report {report_path.name} must be a JSON object")
        add_image(self.manifest, self.path.stem, self.path, present, state["absent"], report,
                  state["boxes"], state["boxes_complete"], replace=True)
        state.update(present=present, pinned=report is not None, in_manifest=True)
        state["saved"] = snapshot(state)
        self.written.append(self.path.stem)
        return True

    def move(self, delta):
        """Save the current still, then step ``delta`` stills; False at either end (after saving)."""
        self.save()
        target = self.index + delta
        if not 0 <= target < len(self.stills):
            return False
        self.index = target
        return True


def create_window(stills, default_label="chair"):
    """The annotation window over ``stills`` (a ``StillSet``); needs a ``QApplication``."""
    from PySide6 import QtCore, QtGui, QtWidgets

    class Canvas(QtWidgets.QWidget):
        """The still, scaled to fit; drags are reported in source pixels."""
        def __init__(self, on_box):
            super().__init__()
            self.image = QtGui.QImage()
            self.boxes = []
            self.drag = None
            self.on_box = on_box
            self.setMinimumSize(320, 240)

        def set_image(self, image, boxes):
            self.image, self.boxes, self.drag = image, boxes, None
            self.update()

        def placement(self):
            """``(scale, left, top)`` of the still inside the widget, or None without an image."""
            if self.image.isNull():
                return None
            scale = min(self.width() / self.image.width(), self.height() / self.image.height())
            return (scale, (self.width() - self.image.width() * scale) / 2,
                    (self.height() - self.image.height() * scale) / 2)

        def to_source(self, point):
            """Widget position to source pixels, clamped to the still."""
            scale, left, top = self.placement()
            x = min(max((point.x() - left) / scale, 0), self.image.width())
            y = min(max((point.y() - top) / scale, 0), self.image.height())
            return x, y

        def to_widget(self, x, y):
            scale, left, top = self.placement()
            return QtCore.QPointF(left + x * scale, top + y * scale)

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("black"))
            if self.image.isNull():
                painter.setPen(QtGui.QColor("white"))
                painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "The still could not be decoded")
                return
            scale, left, top = self.placement()
            painter.drawImage(QtCore.QRectF(left, top, self.image.width() * scale, self.image.height() * scale),
                              self.image)
            pen = QtGui.QPen(QtGui.QColor(0, 220, 0), 2)
            painter.setPen(pen)
            for obj in self.boxes:
                x1, y1, x2, y2 = obj["box_xyxy"]
                rect = QtCore.QRectF(self.to_widget(x1, y1), self.to_widget(x2, y2))
                painter.drawRect(rect)
                painter.drawText(rect.topLeft() + QtCore.QPointF(3, -4), obj["label"])
            if self.drag:
                (x1, y1), (x2, y2) = self.drag
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 220, 0), 1, QtCore.Qt.PenStyle.DashLine))
                painter.drawRect(QtCore.QRectF(self.to_widget(x1, y1), self.to_widget(x2, y2)))

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.MouseButton.LeftButton and not self.image.isNull():
                start = self.to_source(event.position())
                self.drag = (start, start)
                self.update()

        def mouseMoveEvent(self, event):
            if self.drag:
                self.drag = (self.drag[0], self.to_source(event.position()))
                self.update()

        def mouseReleaseEvent(self, event):
            if self.drag and event.button() == QtCore.Qt.MouseButton.LeftButton:
                (ax, ay), (bx, by) = self.drag[0], self.to_source(event.position())
                self.drag = None
                x1, x2 = sorted((round(ax), round(bx)))
                y1, y2 = sorted((round(ay), round(by)))
                if x2 - x1 >= MIN_BOX_SIDE and y2 - y1 >= MIN_BOX_SIDE:
                    self.on_box(x1, y1, x2, y2)
                self.update()

    class Window(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.stills = stills
            self.error = None
            self.setWindowTitle("SightCircuit — box annotation")
            self.resize(1100, 820)
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            layout = QtWidgets.QVBoxLayout(central)
            controls = QtWidgets.QHBoxLayout()
            self.previous_button = QtWidgets.QPushButton("◀ Previous")
            self.next_button = QtWidgets.QPushButton("Next ▶")
            self.undo_button = QtWidgets.QPushButton("Undo box")
            self.label_box = QtWidgets.QComboBox()
            self.label_box.addItems(COCO_CLASSES)
            self.label_box.setCurrentText(default_label)
            self.complete_box = QtWidgets.QCheckBox("Boxes complete")
            self.complete_box.setToolTip("Every object a detector could name is boxed; unmatched detections "
                                         "then count as false positives")
            self.absent_edit = QtWidgets.QLineEdit()
            self.absent_edit.setPlaceholderText("absent labels, comma-separated")
            for widget in (self.previous_button, self.next_button, self.undo_button,
                           QtWidgets.QLabel("Label:"), self.label_box, self.complete_box,
                           QtWidgets.QLabel("Absent:"), self.absent_edit):
                controls.addWidget(widget)
            layout.addLayout(controls)
            self.canvas = Canvas(self.add_box)
            layout.addWidget(self.canvas, 1)
            self.status = QtWidgets.QLabel()
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.previous_button.clicked.connect(lambda: self.go(-1))
            self.next_button.clicked.connect(lambda: self.go(1))
            self.undo_button.clicked.connect(self.undo)
            self.complete_box.toggled.connect(self.toggle_complete)
            self.absent_edit.editingFinished.connect(self.absent_edited)
            for keys, action in ((("Left", "P"), lambda: self.go(-1)), (("Right", "Space", "N"), lambda: self.go(1)),
                                 (("Z", "Backspace"), self.undo)):
                for key in keys:
                    QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=action)
            self.load_current()

        def load_current(self):
            state = self.stills.current
            image = QtGui.QImage(str(self.stills.path))
            self.canvas.set_image(image, state["boxes"])
            self.complete_box.blockSignals(True)
            self.complete_box.setChecked(state["boxes_complete"])
            self.complete_box.blockSignals(False)
            self.absent_edit.setText(", ".join(state["absent"]))
            self.refresh_status()

        def refresh_status(self, note=None):
            state, image = self.stills.current, self.canvas.image
            size = "undecodable" if image.isNull() else f"{image.width()}×{image.height()}"
            parts = [f"{self.stills.index + 1}/{len(self.stills.stills)} {self.stills.path.name}", size,
                     f"{len(state['boxes'])} box{'es' if len(state['boxes']) != 1 else ''}",
                     "capture report beside it" if self.stills.report_path() else "no capture report",
                     "unsaved" if self.stills.changed() else (
                         "in manifest" if state["in_manifest"] else "not in manifest")]
            if self.error:
                parts.append(f"ERROR: {self.error}")
            elif note:
                parts.append(note)
            self.status.setText(" · ".join(parts))

        def add_box(self, x1, y1, x2, y2):
            self.error = None
            try:
                self.stills.add_box(self.label_box.currentText(), x1, y1, x2, y2)
            except ValueError as error:
                self.error = str(error)
            self.canvas.update()
            self.refresh_status()

        def undo(self):
            self.error = None
            removed = self.stills.undo()
            self.canvas.update()
            self.refresh_status(None if removed else "nothing to undo")

        def toggle_complete(self, checked):
            self.stills.set_complete(checked)
            self.refresh_status()

        def absent_edited(self):
            self.stills.set_absent(self.absent_edit.text().split(","))
            self.refresh_status()

        def go(self, delta):
            """Save the current still, then show the neighbour; a refused save keeps the still on screen."""
            self.absent_edited()
            self.error = None
            try:
                moved = self.stills.move(delta)
            except (OSError, ValueError) as error:
                self.error = str(error)
                self.refresh_status()
                return
            if moved:
                self.load_current()
            else:
                self.refresh_status("saved; first still" if delta < 0 else "saved; last still")

        def closeEvent(self, event):
            self.absent_edited()
            self.error = None
            try:
                self.stills.save()
            except (OSError, ValueError) as error:
                self.error = str(error)
            event.accept()

    return Window()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", nargs="?", type=Path, default=ROOT / "evaluation/local",
                        help="Directory of stills; default evaluation/local in the checkout")
    parser.add_argument("--manifest", type=Path, help="Session manifest; default <directory>/frame.json")
    parser.add_argument("--label", default="chair", choices=COCO_CLASSES, metavar="LABEL",
                        help="Label selected when the window opens (a canonical COCO name); default chair")
    args = parser.parse_args(argv)
    manifest = args.manifest or args.directory / "frame.json"
    report = {"report_kind": "box_annotation", "status": "error", "directory": str(args.directory),
              "manifest": str(manifest), "stills": 0, "entries_written": []}
    try:
        stills = StillSet(args.directory, manifest)
        report["stills"] = len(stills.stills)
        if not os.environ.get("QT_QPA_PLATFORM") and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("No display session found. Run in the Frame's graphical session.")
        os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
        try:
            from PySide6 import QtWidgets
        except ImportError as error:
            raise RuntimeError(f"Window dependencies unavailable: {error}. Run bash scripts/setup_window.sh") from error
        app = QtWidgets.QApplication([])
        window = create_window(stills, args.label)
        window.show()
        exit_code = app.exec()
        report.update(entries_written=sorted(set(stills.written)), qt_platform=app.platformName())
        if window.error:
            raise RuntimeError(window.error)
        if exit_code:
            raise RuntimeError(f"Qt exited with code {exit_code}")
        report["status"] = "closed"
    except (OSError, ValueError, RuntimeError) as error:
        report["error"] = str(error)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "closed" else 1


if __name__ == "__main__":
    sys.exit(main())
