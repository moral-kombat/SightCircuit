"""A guided window for one bounded camera recognition run: no keyboard, no terminal bell, no counting of seconds.

The sixteenth command (owner request 2026-11-19: "a program interface I can
work with instead of relying on the bells keyboard and time", as "a window
inside the headset", with "tap to start" accepted). It runs what
``camera_recognize`` runs, ``recognize()`` with that command's own bounds (at
most 30 seconds and 300 frames, one camera, 5 frames a second by default), and
puts a Qt window in front of it that the wearer works with the controller's
pointer on the Frame's desktop. It adds no capability and raises no bound.

The screens, one thing on each. (1) The starting state: what the wearer sees
now, "Colour, stable" or "Something else"; the second answer ends there with
nothing launched. (2) The notice: the helper's export notice as the contract
has it, without its closing instruction to type the answer
(``confirmation.export_notice_body``), the run's bounds, the target, where the
report will be saved, and Start and Cancel. A click on Start is the informed
start of this one run; the report records ``confirmation``
``operator_window``. It is not an approval and opens no gate. (3) The guided
run: one instruction at a time with a drawn picture, a progress bar while a
step is recorded, the time left, and Stop. (4) After the run, for the removal
script, whether the headset came off and is back on; then the passthrough
watch, 30 seconds with a countdown, answered "Passthrough unchanged" or
"Something changed". (5) The result in plain words, the report's path and
Close.

Nothing camera-derived is shown while the reader is active. The run is in a
second process of this command (``--worker``, never given by hand): it calls
``recognize()`` on its main thread, as the supervision primitive requires,
and launches the helper as ``camera_recognize`` does (the named binary,
``--parent-confirmed`` after the click, the same owned session and deadline).
The window's process loads no detector and reads no frame. During the run
the worker tells it one thing, that the helper's ``ready`` was accepted,
with no value attached; what the run screen shows is
``sightcircuit.camera.guide.Guide.view``: the script, the clicks and the
clock. The report reaches the window only after the helper has ended.
No image is ever shown, saved or written: the pictures are shapes drawn by
this file. The window never opens the overlay bridge or SteamVR.

Two scripts (``--script``). ``removal``: the target held at about half a
metre and at about a metre (marks ``near`` and ``far``, each at the wearer's
click, then about five seconds recorded), then the click made just before the
headset comes off (``remove``); the run ends by itself when the helper
reports ``headset_removed`` or ``standby``, or on its bound. ``positions``:
centre, left, right, up, down for about four seconds each, then out of view,
each mark made when its instruction appears, with no click after Start. Marks
are ``marks.VOCABULARY``'s, recorded with source ``window``; the summaries are
``marks.summarize``'s. Because ``remove`` is clicked before the headset comes
off, each removal entry also has the time from the run's last frame to the
helper's end record; the two limits the entry is compared with are
``--late-frame-limit-ms`` and ``--removal-end-limit-ms`` (the current values
by default). The window shows the measured values and decides nothing.

The report is ``camera_recognize``'s with a ``guide`` block: the script, the
steps with their marks, the steps not reached, and the wearer's observations
from closed choices only (no free text anywhere). ``--report`` is required,
is checked before the window opens (a new ``.json`` path in an existing
directory) and is never overwritten. Stop, closing the window or a signal
ends the helper and still leaves the report. One launch is one run: a second
run is a second launch, a second notice and a second Start.

Camera Data and gates are as ``camera_recognize`` states them: the report
holds counts, closed labels, boxes, timings and format metadata, never a
pixel; every gate stays closed and none is lifted (``gates.claims()`` is in
the report). A run on the Frame is a device step that needs the owner's
approval; the Frame manager launches the window for the wearer after it. The
window is a procedure, not an approval. ``--validate`` runs the whole window
flow offscreen against the helper's fake build with a scripted detector:
both scripts, Cancel, a declined start, Stop, the window closed mid-run, a
presence end and a run that reaches its bound.
"""
import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

if not __package__:  # Run as a file: import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sightcircuit import gates
from sightcircuit.camera import contract, marks
from sightcircuit.camera import guide as guidance
from sightcircuit.camera.confirmation import OPERATOR_WINDOW, export_notice_body
from sightcircuit.commands import camera_recognize
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, POC_THRESHOLD, class_names
from sightcircuit.errors import SightCircuitError
from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.runtime import guard
from sightcircuit.sources import camera_helper

MAX_SECONDS, MAX_FRAMES, MAX_FPS = camera_recognize.MAX_SECONDS, camera_recognize.MAX_FRAMES, camera_recognize.MAX_FPS
DEFAULT_FPS = camera_recognize.DEFAULT_FPS
DEFAULT_TARGET = "bottle"
LEFT_CAMERA = camera_helper.LEFT_CAMERA
TICK_MS = 50  # The window's clock: how often the run screen and the countdown are brought up to date.
# After Stop, or past the run's own deadline, how long the window waits for the worker's report before it sends the
# worker SIGTERM, and then before it gives up and reports that. The worker is never killed: its helper is its own.
WORKER_PATIENCE_SECONDS = 25.0
RUN_MARGIN_SECONDS = camera_helper.LAUNCH_MARGIN_SECONDS + 20  # Beyond max_seconds: the launch, the detector, the joins.
WORKER_FLAG = "--worker"
FAKE_HELPER = "artifacts/camera-helper-fake/sightcircuit-camera-helper-fake"  # scripts/build_camera_helper.sh --fake
DESCRIBE_TIMEOUT_SECONDS = 10
VALIDATION_KIND = "camera_guide_validation"
START_QUESTION = "What do you see in the headset right now?"
START_NOTE = ("Clicking Start starts this one run. It is a start, not an approval: the run was approved before this "
              "window was opened.")
TEXT_START, TEXT_CANCEL, TEXT_STOP, TEXT_CLOSE = "Start", "Cancel", "Stop", "Close"
TEXT_COLOUR_STABLE, TEXT_SOMETHING_ELSE = "Colour, stable", "Something else"
TEXT_UNCHANGED, TEXT_CHANGED = "Passthrough unchanged", "Something changed"
TEXT_BACK_ON, TEXT_NOT_REMOVED = "I took the headset off and it is back on", "I did not take it off"
WATCH_QUESTION = "Look around. Is the view through the headset the same as before the run?"
PHASES = PHASE_STATE, PHASE_NOTICE, PHASE_RUN, PHASE_BACK, PHASE_WATCH, PHASE_RESULT = (
    "starting_state", "notice", "run", "headset_back", "watch", "result")


@dataclass
class Config:
    """One launch of the window: the run it may start and where its report goes."""
    script: str
    helper: str
    report_path: Path
    limits: dict
    camera: int = LEFT_CAMERA
    target: str = DEFAULT_TARGET
    detector: str = DEFAULT_DETECTOR
    threshold: float = POC_THRESHOLD
    helper_args: tuple = ()
    late_frame_limit_ms: int = marks.LATE_FRAME_LIMIT_MS
    removal_end_limit_ms: int = marks.REMOVAL_END_LIMIT_MS
    scale: float = 1.0  # Below 1 only in the checks: shorter steps.
    watch_seconds: float = guidance.WATCH_SECONDS  # Shorter only in the checks.
    validation: bool = False  # The worker uses the scripted detector; never set from the command line of a run.
    worker_env: dict = field(default_factory=dict)

    def spec(self):
        """What the worker is told: the run's arguments, nothing else."""
        return {"helper": str(self.helper), "limits": dict(self.limits), "camera": self.camera, "target": self.target,
                "detector": self.detector, "threshold": self.threshold, "helper_args": list(self.helper_args),
                "late_frame_limit_ms": self.late_frame_limit_ms, "removal_end_limit_ms": self.removal_end_limit_ms,
                "validation": bool(self.validation)}


def check_report_path(path):
    """The report's path as it will be written, or ``ValueError``: a new ``.json`` file in an existing directory."""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".json" or not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError("--report needs a new .json path in an existing directory")
    return target


def run_limits(script, seconds=None, max_frames=None, fps=DEFAULT_FPS):
    """``{fps, max_seconds, max_frames}`` for a script, inside ``camera_recognize``'s bounds, else ``ValueError``."""
    seconds = guidance.DEFAULT_SECONDS[script] if seconds is None else seconds
    if type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS or type(fps) is not int or not 1 <= fps <= MAX_FPS:
        raise ValueError(f"a run is at most {MAX_SECONDS} seconds at 1 to {MAX_FPS} frames a second")
    max_frames = min(MAX_FRAMES, seconds * fps) if max_frames is None else max_frames
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise ValueError(f"a run is at most {MAX_FRAMES} frames")
    return {"fps": fps, "max_seconds": seconds, "max_frames": max_frames}


def unstarted_report(config, reason):
    """The report of a launch that started no run: ``camera_recognize``'s shape, nothing launched, no model loaded."""
    report = camera_recognize.new_report("recognize")
    report.update(confirmation=None, seconds_requested=config.limits["max_seconds"],
                  max_frames_requested=config.limits["max_frames"], fps_requested=config.limits["fps"],
                  cameras_requested=[config.camera], status="error", reason=reason, phase="closed")
    return report


# ---- the worker: the run itself, in its own process ---------------------------------------------------------------

class ValidationDetector:
    """The checks' detector: the target where the latest mark says it is held; never looks at a pixel."""
    name, runtime = "camera-guide-validation", "none"
    CENTRES = {"near": (0.5, 0.5), "far": (0.5, 0.5), "centre": (0.5, 0.5), "left": (0.2, 0.5), "right": (0.8, 0.5),
               "up": (0.5, 0.2), "down": (0.5, 0.8)}

    def __init__(self, target, latest_label):
        self.classes, self.target, self.latest_label = (target,), target, latest_label

    def detect(self, bgr, threshold):
        centre = self.CENTRES.get(self.latest_label())
        if centre is None:
            return DetectionResult((), 1.0)
        x, y = round(centre[0] * bgr.shape[1]), round(centre[1] * bgr.shape[0])
        return DetectionResult((Detection(self.target, 0.9, (x - 2, y - 2, x + 2, y + 2)),), 1.0)


class _Discard:
    """Where the mark session's announcements go in the worker: nowhere (the window is the wearer's interface)."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


def worker_main(stdin=None, stdout=None):
    """``--worker``: one ``recognize()`` run for the window that launched this process.

    The first line on stdin is the run (``Config.spec``); later lines are
    ``{"mark": LABEL, "monotonic_ns": N}`` (a click, recorded at its time) or
    ``{"stop": true}``; the end of stdin is a stop too, so a window that died
    takes its run with it. On stdout: ``{"event": "ready"}`` when the helper's
    ``ready`` is accepted, with nothing else in it, and after ``recognize()``
    has returned (the helper has ended) ``{"event": "report", "report": ...}``.
    Nothing derived from a frame is written before that last line.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    lock = threading.Lock()

    def emit(record):
        with lock:
            stdout.write(json.dumps(record) + "\n")
            stdout.flush()
    report = camera_recognize.new_report("recognize")
    report.update(confirmation=None, status="error", reason="guide_worker_arguments_invalid", phase="closed")
    try:
        spec = json.loads(stdin.readline())
        limits = camera_helper.check_limits(spec["limits"])
        if limits["max_seconds"] > MAX_SECONDS or limits["max_frames"] > MAX_FRAMES:
            raise ValueError("beyond camera_recognize's bounds")
        cameras = camera_helper.check_cameras((spec["camera"],))
        target, helper = spec["target"], spec["helper"]
    except (ValueError, KeyError, TypeError):
        emit({"event": "report", "report": report})
        return 1
    stop = threading.Event()
    state = {"session": None, "label": None, "marks_refused": 0}

    def listen():
        try:
            for line in stdin:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("stop"):
                    break
                session = state["session"]
                try:
                    if session is None or not session.mark(message.get("mark"), message.get("monotonic_ns")):
                        state["marks_refused"] += 1
                    else:
                        state["label"] = message["mark"]
                except ValueError:
                    state["marks_refused"] += 1
        finally:
            stop.set()  # A stop line, or the window is gone.
    threading.Thread(target=listen, name="camera-guide-worker-input", daemon=True).start()
    factory = (lambda: ValidationDetector(target, lambda: state["label"])) if spec.get("validation") is True else None
    with guard.sigterm_cancels():
        try:
            report = camera_recognize.recognize(
                helper, limits, cameras, threshold=spec["threshold"], detector=spec["detector"],
                detector_factory=factory, extra_args=tuple(spec["helper_args"]), stop=stop, confirmed=True,
                confirmation=OPERATOR_WINDOW, target_label=target, terminal=_Discard(),
                on_session=lambda session: state.update(session=session), on_ready=lambda: emit({"event": "ready"}),
                late_frame_limit_ms=spec["late_frame_limit_ms"], removal_end_limit_ms=spec["removal_end_limit_ms"])
        except guard.CANCELLATION as error:
            report.update(confirmation=OPERATOR_WINDOW)
            guard.cancelled(report, error)
        except Exception as error:  # Whatever went wrong, the window gets a report and the wearer a result screen.
            report.update(confirmation=OPERATOR_WINDOW, status="error", reason="guide_worker_failed",
                          error=f"{type(error).__name__}: {error}"[:1024])
    report["window_marks_refused"] = state["marks_refused"]
    emit({"event": "report", "report": report})
    return 0


class Worker:
    """The window's handle on the run's process: start it, send it marks and a stop, receive its two events.

    Events reach ``on_event`` on the reader thread as ``("ready",
    monotonic_ns)``, the time the line was read, and ``("report", report)``;
    a worker that ends without a report gives ``("report", None)``. Only
    those two are passed on: any other line the worker writes is counted
    (``unexpected_lines``) and dropped, so nothing else can reach the window.
    """

    def __init__(self, spec, on_event, *, env=None, command=None):
        self.spec, self.on_event = spec, on_event
        self.env = dict(os.environ, **(env or {}))
        self.command = [sys.executable, str(Path(__file__).resolve()), WORKER_FLAG] if command is None else command
        self.process = self.thread = None
        self.unexpected_lines = 0
        self.lock = threading.Lock()

    def start(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                        bufsize=1, env=self.env)
        self._send(self.spec)
        self.thread = threading.Thread(target=self._read, name="camera-guide-worker-output", daemon=True)
        self.thread.start()

    def _send(self, record):
        with self.lock:
            try:
                self.process.stdin.write(json.dumps(record) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass  # The worker has gone; its reader thread reports that.

    def _read(self):
        delivered = False
        for line in self.process.stdout:
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            event = record.get("event") if isinstance(record, dict) else None
            if event == "ready" and not delivered:
                self.on_event(("ready", time.monotonic_ns()))
            elif event == "report" and not delivered and isinstance(record.get("report"), dict):
                delivered = True
                self.on_event(("report", record["report"]))
            else:
                self.unexpected_lines += 1
        self.process.wait()
        if not delivered:
            self.on_event(("report", None))

    def mark(self, label, monotonic_ns):
        self._send({"mark": label, "monotonic_ns": monotonic_ns})

    def stop(self):
        self._send({"stop": True})

    def terminate(self):
        try:
            self.process.send_signal(signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def close(self):
        """After the report: close the pipes and collect the process."""
        if self.process is None:
            return
        with self.lock:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
        if self.thread is not None:
            self.thread.join(camera_helper.JOIN_SECONDS)
            if not self.thread.is_alive():
                self.process.stdout.close()


# ---- the window ----------------------------------------------------------------------------------------------------

def create_window(config, *, worker_factory=None, clock=time.monotonic_ns, size=None):
    """The guide's window for ``config``. ``worker_factory(spec, on_event)`` makes the run's process handle."""
    from PySide6 import QtCore, QtGui, QtWidgets
    Qt = QtCore.Qt
    if worker_factory is None:
        def worker_factory(spec, on_event):
            return Worker(spec, on_event, env=config.worker_env)
    screen = QtGui.QGuiApplication.primaryScreen()
    height = size[1] if size is not None else (screen.availableGeometry().height() if screen is not None else 1080)
    unit = max(16, int(height / 30))  # The body text's pixel size: large type for a headset.

    class Picture(QtWidgets.QWidget):
        """A drawn picture for a step. Shapes only: no image is loaded, received or kept."""

        def __init__(self):
            super().__init__()
            self.kind = "wait"
            self.setMinimumHeight(unit * 6)

        def show_kind(self, kind):
            if kind != self.kind:
                self.kind = kind
                self.update()

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            side = min(self.width() * 0.6, self.height() * 0.95)
            box = QtCore.QRectF((self.width() - side * 1.6) / 2, (self.height() - side) / 2, side * 1.6, side)
            white, accent, dim = QtGui.QColor("#f4f6f8"), QtGui.QColor("#ffd23f"), QtGui.QColor("#5b6b7c")
            pen = QtGui.QPen(white, max(3, unit // 8))
            painter.setPen(pen)
            centre = box.center()
            radius = side * 0.11
            if self.kind in ("centre", "left", "right", "up", "down", "out_of_view"):
                painter.drawRoundedRect(box, unit / 2, unit / 2)  # The wearer's view.
                dx = {"left": -0.32, "right": 0.32}.get(self.kind, 0) * box.width()
                dy = {"up": -0.32, "down": 0.32}.get(self.kind, 0) * box.height()
                if self.kind == "out_of_view":
                    painter.setPen(QtGui.QPen(dim, max(3, unit // 8), Qt.PenStyle.DashLine))
                    painter.drawEllipse(centre, radius, radius)
                    painter.drawLine(QtCore.QPointF(centre.x() - radius * 1.6, centre.y() - radius * 1.6),
                                     QtCore.QPointF(centre.x() + radius * 1.6, centre.y() + radius * 1.6))
                else:
                    if dx or dy:  # An arrow from the middle towards the place.
                        painter.setPen(QtGui.QPen(dim, max(3, unit // 6)))
                        painter.drawLine(centre, QtCore.QPointF(centre.x() + dx * 0.6, centre.y() + dy * 0.6))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(accent)
                    painter.drawEllipse(QtCore.QPointF(centre.x() + dx, centre.y() + dy), radius, radius)
            elif self.kind in ("near", "far"):
                head = QtCore.QPointF(box.left() + side * 0.16, centre.y())
                painter.drawEllipse(head, side * 0.16, side * 0.16)  # The wearer, from the side.
                painter.drawRect(QtCore.QRectF(head.x() + side * 0.08, head.y() - side * 0.07, side * 0.12, side * 0.12))
                reach = box.width() * (0.3 if self.kind == "near" else 0.72)  # Half the distance, about half the gap.
                bottle = QtCore.QRectF(head.x() + side * 0.2 + reach, centre.y() - side * 0.2, side * 0.11, side * 0.36)
                painter.setPen(QtGui.QPen(dim, max(3, unit // 8), Qt.PenStyle.DashLine))
                painter.drawLine(QtCore.QPointF(head.x() + side * 0.24, centre.y()), QtCore.QPointF(bottle.left(), centre.y()))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(bottle, unit / 4, unit / 4)
                painter.setPen(white)
                painter.drawText(QtCore.QRectF(box.left(), box.bottom() - unit * 1.6, box.width(), unit * 1.6),
                                 Qt.AlignmentFlag.AlignCenter, "about half a metre" if self.kind == "near" else "about a metre")
            elif self.kind == "headset_off":
                painter.drawEllipse(centre, side * 0.22, side * 0.22)
                visor = QtCore.QRectF(centre.x() - side * 0.3, centre.y() - side * 0.5, side * 0.6, side * 0.18)
                painter.setBrush(accent)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(visor, unit / 3, unit / 3)
                painter.setPen(QtGui.QPen(dim, max(3, unit // 6)))
                painter.drawLine(QtCore.QPointF(centre.x(), centre.y() - side * 0.05), QtCore.QPointF(centre.x(), centre.y() - side * 0.28))
            elif self.kind == "done":
                painter.setPen(QtGui.QPen(accent, max(4, unit // 4)))
                painter.drawPolyline([QtCore.QPointF(centre.x() - side * 0.25, centre.y()),
                                      QtCore.QPointF(centre.x() - side * 0.05, centre.y() + side * 0.2),
                                      QtCore.QPointF(centre.x() + side * 0.3, centre.y() - side * 0.22)])
            else:  # wait
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(dim)
                for offset in (-1, 0, 1):
                    painter.drawEllipse(QtCore.QPointF(centre.x() + offset * side * 0.25, centre.y()), radius * 0.6, radius * 0.6)
            painter.end()

    def label(text="", name="body", wrap=True, align=Qt.AlignmentFlag.AlignCenter):
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        widget.setWordWrap(wrap)
        widget.setAlignment(align)
        widget.setTextFormat(Qt.TextFormat.PlainText)
        return widget

    class PointerButton(QtWidgets.QPushButton):
        """A button only a pointer clicks: no key starts, answers or stops anything, wherever the focus is."""

        def keyPressEvent(self, event):
            event.ignore()

        def keyReleaseEvent(self, event):
            event.ignore()

    def button(text, name="plain"):
        widget = PointerButton(text)
        widget.setObjectName(name)
        widget.setMinimumHeight(int(unit * 3.2))
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.setAutoDefault(False)
        return widget

    def page(*rows):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(unit * 2, unit, unit * 2, unit)
        layout.setSpacing(unit)
        for row in rows:
            if isinstance(row, QtWidgets.QLayout):
                layout.addLayout(row)
            elif isinstance(row, tuple):
                layout.addWidget(row[0], row[1])
            else:
                layout.addWidget(row)
        return widget

    def side_by_side(*widgets):
        layout = QtWidgets.QHBoxLayout()
        layout.setSpacing(unit * 2)
        for widget in widgets:
            layout.addWidget(widget)
        return layout

    def scrolling(widget):
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area

    class Window(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.config = config
            self.setWindowTitle("SightCircuit camera guide")
            self.setStyleSheet(
                f"QWidget {{ background: #10151b; color: #f4f6f8; font-size: {unit}px; }}"
                f"QLabel#title {{ font-size: {int(unit * 1.5)}px; font-weight: bold; }}"
                f"QLabel#big {{ font-size: {int(unit * 1.4)}px; font-weight: bold; }}"
                f"QLabel#count {{ font-size: {unit * 5}px; font-weight: bold; color: #ffd23f; }}"
                f"QLabel#small {{ font-size: {int(unit * 0.8)}px; }}"
                f"QPushButton {{ font-size: {int(unit * 1.3)}px; font-weight: bold; border-radius: {unit // 2}px; "
                f"padding: {unit // 2}px {unit}px; background: #2d3a4a; border: 3px solid #5b6b7c; }}"
                "QPushButton#go { background: #1f7a44; border-color: #3fbf77; }"
                "QPushButton#stop { background: #a3271f; border-color: #e0635b; }"
                "QPushButton:disabled, QPushButton#go:disabled, QPushButton#stop:disabled "
                "{ background: #1b232d; color: #6b7785; border-color: #2d3a4a; }"
                f"QProgressBar {{ border: 3px solid #5b6b7c; border-radius: {unit // 3}px; min-height: {unit}px; "
                "background: #1b232d; } QProgressBar::chunk { background: #ffd23f; }"
                f"QScrollBar:vertical {{ width: {unit * 2}px; background: #1b232d; }}"
                f"QScrollBar::handle:vertical {{ background: #5b6b7c; min-height: {unit * 3}px; }}")
            self.phase = PHASE_STATE
            self.guide = None  # The run's script state, once Start is clicked.
            self.worker = None
            self.workers_started = 0
            self.report = None  # The worker's report: set only after the helper has ended.
            self.final = None  # The saved report, with the guide block.
            self.report_error = None
            self.ended_by = guidance.ENDED_NOT_STARTED
            self.unstarted_reason = guidance.DECLINED
            self.observations = {"starting_state": None, "passthrough_after": None, "headset": None,
                                 "watch_seconds_elapsed": None, "watch_completed": False}
            self.events = queue.Queue()  # From the worker's reader thread; taken on the window's own clock.
            self.close_requested = False
            self.signal_number = None
            self.stop_sent_ns = self.terminate_sent_ns = self.run_deadline_ns = self.watch_started_ns = None
            self.run_screen_log = []  # Every distinct content the run screen showed, for the checks.
            self.stack = QtWidgets.QStackedLayout(self)
            self.stack.setContentsMargins(0, 0, 0, 0)
            self._build_state()
            self._build_notice()
            self._build_run()
            self._build_back()
            self._build_watch()
            self._build_result()
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(TICK_MS)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        # -- screens --

        def _build_state(self):
            self.stable_button, self.other_button = button(TEXT_COLOUR_STABLE, "go"), button(TEXT_SOMETHING_ELSE)
            self.stable_button.clicked.connect(lambda: self.answer_state(guidance.START_COLOUR_STABLE))
            self.other_button.clicked.connect(lambda: self.answer_state(guidance.START_SOMETHING_ELSE))
            self.state_page = page(label("Before we start", "title"), (label(START_QUESTION, "big"), 1),
                                   side_by_side(self.stable_button, self.other_button))
            self.stack.addWidget(self.state_page)

        def _build_notice(self):
            self.notice_label = label(export_notice_body(), "small", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.bounds_label = label(guidance.bounds_line(config.limits, config.camera), "small")
            self.target_label = label(f"Looking for: {config.target}. Guide: {config.script}.", "small")
            self.path_label = label(f"The report will be saved to: {config.report_path}", "small")
            self.start_note = label(START_NOTE, "small")
            self.start_button, self.cancel_button = button(TEXT_START, "go"), button(TEXT_CANCEL)
            self.start_button.clicked.connect(self.start_run)
            self.cancel_button.clicked.connect(lambda: self.decline(guidance.ENDED_CANCEL))
            self.notice_page = page(label("Please read", "title"), (scrolling(self.notice_label), 1), self.bounds_label,
                                    self.target_label, self.path_label, self.start_note,
                                    side_by_side(self.start_button, self.cancel_button))
            self.stack.addWidget(self.notice_page)

        def _build_run(self):
            self.step_label = label("", "small", wrap=False, align=Qt.AlignmentFlag.AlignLeft)
            self.time_label = label("", "small", wrap=False, align=Qt.AlignmentFlag.AlignRight)
            self.picture = Picture()
            self.instruction_label = label("", "big")
            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 1000)
            self.progress.setTextVisible(False)
            self.step_button = button("", "go")
            self.step_button.clicked.connect(self.step_clicked)
            self.stop_button = button(TEXT_STOP, "stop")
            self.stop_button.clicked.connect(lambda: self.stop_run(guidance.ENDED_STOP))
            # The bar and the step's button keep their room when hidden, so nothing jumps under the pointer.
            for widget in (self.progress, self.step_button):
                policy = widget.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                widget.setSizePolicy(policy)
            self.run_page = page(side_by_side(self.step_label, self.time_label), (self.picture, 1), self.instruction_label,
                                 self.progress, side_by_side(self.step_button, self.stop_button))
            self.stack.addWidget(self.run_page)

        def _build_back(self):
            self.back_button, self.not_removed_button = button(TEXT_BACK_ON, "go"), button(TEXT_NOT_REMOVED)
            self.back_button.clicked.connect(lambda: self.answer_headset(guidance.HEADSET_REMOVED_AND_BACK))
            self.not_removed_button.clicked.connect(lambda: self.answer_headset(guidance.HEADSET_NOT_REMOVED))
            self.back_page = page(label("The run has ended", "title"),
                                  (label("Put the headset back on if it is off. Then choose one", "big"), 1),
                                  self.back_button, self.not_removed_button)
            self.stack.addWidget(self.back_page)

        def _build_watch(self):
            self.count_label = label("", "count")
            self.changed_button, self.unchanged_button = button(TEXT_CHANGED, "stop"), button(TEXT_UNCHANGED, "go")
            self.changed_button.clicked.connect(lambda: self.answer_passthrough(guidance.PASSTHROUGH_CHANGED))
            self.unchanged_button.clicked.connect(lambda: self.answer_passthrough(guidance.PASSTHROUGH_UNCHANGED))
            self.watch_hint = label("", "small")
            self.watch_page = page(label("Watch the passthrough", "title"), label(WATCH_QUESTION, "big"),
                                   (self.count_label, 1), self.watch_hint,
                                   side_by_side(self.changed_button, self.unchanged_button))
            self.stack.addWidget(self.watch_page)

        def _build_result(self):
            self.result_label = label("", "body", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.saved_label = label("", "small")
            self.close_button = button(TEXT_CLOSE)
            self.close_button.clicked.connect(self.close)
            self.result_page = page(label("Result", "title"), (scrolling(self.result_label), 1), self.saved_label,
                                    self.close_button)
            self.stack.addWidget(self.result_page)

        def show_phase(self, phase):
            self.phase = phase
            self.stack.setCurrentWidget({PHASE_STATE: self.state_page, PHASE_NOTICE: self.notice_page,
                                         PHASE_RUN: self.run_page, PHASE_BACK: self.back_page,
                                         PHASE_WATCH: self.watch_page, PHASE_RESULT: self.result_page}[phase])

        # -- before the run --

        def answer_state(self, answer):
            if self.phase != PHASE_STATE:
                return
            self.observations["starting_state"] = answer
            if answer == guidance.START_COLOUR_STABLE:
                self.show_phase(PHASE_NOTICE)
            else:  # Not the state a run may start from: nothing is launched.
                self.unstarted_reason = guidance.STARTING_STATE_NOT_STABLE
                self.finish()

        def decline(self, ended_by):
            if self.phase in (PHASE_STATE, PHASE_NOTICE):
                self.ended_by = ended_by
                self.finish()

        def start_run(self):
            """The Start button: the informed start of this one run. The only place a worker is made."""
            if self.phase != PHASE_NOTICE or self.worker is not None:
                return
            self.guide = guidance.Guide(config.script, config.limits["max_seconds"], target=config.target,
                                        scale=config.scale)
            self.ended_by = guidance.ENDED_HELPER
            self.run_deadline_ns = clock() + int((config.limits["max_seconds"] + RUN_MARGIN_SECONDS) * 1e9)
            self.show_phase(PHASE_RUN)
            self.render_run(clock())
            self.worker = worker_factory(config.spec(), self.events.put)
            self.workers_started += 1
            try:
                self.worker.start()
            except OSError as error:
                self.events.put(("report", dict(unstarted_report(config, "guide_worker_not_started"),
                                                error=str(error)[:1024])))

        # -- the run --

        def render_run(self, now_ns):
            """Show ``Guide.view``: the script, the clicks and the clock. Nothing else is an input here."""
            view = self.guide.view(now_ns)
            self.step_label.setText(f"Step {view.step_number} of {view.step_count}")
            self.time_label.setText("" if view.remaining_seconds is None else f"Time left: {view.remaining_seconds} s")
            self.picture.show_kind(view.picture)
            self.instruction_label.setText(view.instruction)
            self.progress.setVisible(view.progress is not None)
            self.progress.setValue(0 if view.progress is None else round(view.progress * 1000))
            self.step_button.setVisible(bool(view.button))
            self.step_button.setText(view.button)
            self.stop_button.setEnabled(view.state != guidance.STOPPING)
            shown = (view.state, self.step_label.text(), self.instruction_label.text(), self.step_button.text(), view.picture)
            if not self.run_screen_log or self.run_screen_log[-1] != shown:
                self.run_screen_log.append(shown)

        def send_marks(self, made):
            for mark_label, monotonic_ns in made:
                self.worker.mark(mark_label, monotonic_ns)

        def step_clicked(self):
            if self.phase == PHASE_RUN and self.report is None:
                now_ns = clock()
                self.send_marks(self.guide.advance(now_ns, clicked=True))
                self.render_run(now_ns)

        def stop_run(self, ended_by):
            """Stop, the window's close or a signal during the run: the helper is ended and the report is kept."""
            if self.phase != PHASE_RUN or self.stop_sent_ns is not None:
                return
            self.ended_by = ended_by
            self.guide.stop()
            self.stop_sent_ns = clock()
            self.worker.stop()
            self.render_run(self.stop_sent_ns)

        def tick(self):
            now_ns = clock()
            if self.signal_number is not None and not self.close_requested:
                self.close_requested = True
                if self.phase == PHASE_RUN:
                    self.stop_run(guidance.ENDED_SIGNAL)
                else:
                    if self.phase in (PHASE_STATE, PHASE_NOTICE):
                        self.ended_by = guidance.ENDED_SIGNAL
                    self.close()
                    return
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "ready" and self.phase == PHASE_RUN:
                    self.guide.ready(value)  # ``value`` is when the line was read; the worker attached nothing.
                elif kind == "report" and self.phase == PHASE_RUN:
                    self.run_ended(value)
                    return
            if self.phase == PHASE_RUN:
                self.send_marks(self.guide.advance(now_ns))
                self.render_run(now_ns)
                self.watch_worker(now_ns)
            elif self.phase == PHASE_WATCH:
                self.render_watch(now_ns)

        def watch_worker(self, now_ns):
            """A worker that gives no report: ask it to stop, then SIGTERM, then report that it did not answer."""
            patience = int(WORKER_PATIENCE_SECONDS * 1e9)
            if self.stop_sent_ns is None and now_ns > self.run_deadline_ns:
                self.stop_run(guidance.ENDED_HELPER)
            elif self.stop_sent_ns is not None and self.terminate_sent_ns is None and now_ns > self.stop_sent_ns + patience:
                self.terminate_sent_ns = now_ns
                self.worker.terminate()
            elif self.terminate_sent_ns is not None and now_ns > self.terminate_sent_ns + patience:
                self.run_ended(dict(unstarted_report(config, "guide_worker_unresponsive"),
                                    confirmation=OPERATOR_WINDOW, cleanup_ok=False))

        def run_ended(self, report):
            """The worker's report has arrived: the helper has ended. From here results may be shown."""
            if report is None:
                report = dict(unstarted_report(config, "guide_worker_ended_without_report"),
                              confirmation=OPERATOR_WINDOW, cleanup_ok=False)
            self.report = report
            self.worker.close()
            if self.close_requested:
                self.finish()
                self.close()
            elif config.script == guidance.SCRIPT_REMOVAL:
                self.show_phase(PHASE_BACK)
            else:
                self.observations["headset"] = guidance.HEADSET_NOT_ASKED
                self.start_watch()

        # -- after the run --

        def answer_headset(self, answer):
            if self.phase == PHASE_BACK:
                self.observations["headset"] = answer
                self.start_watch()

        def start_watch(self):
            self.watch_started_ns = clock()
            self.show_phase(PHASE_WATCH)
            self.render_watch(self.watch_started_ns)

        def watch_elapsed(self, now_ns):
            return max(0.0, (now_ns - self.watch_started_ns) / 1e9)

        def render_watch(self, now_ns):
            left = config.watch_seconds - self.watch_elapsed(now_ns)
            done = left <= 0
            self.count_label.setText("0" if done else str(int(-(-left // 1))))
            self.unchanged_button.setEnabled(done)
            self.watch_hint.setText("The watch is over. Choose one" if done else
                                    f"Keep watching. \"{TEXT_UNCHANGED}\" can be chosen when the count reaches 0")

        def answer_passthrough(self, answer):
            if self.phase != PHASE_WATCH:
                return
            elapsed = self.watch_elapsed(clock())
            if answer == guidance.PASSTHROUGH_UNCHANGED and elapsed < config.watch_seconds:
                return
            self.observations.update(passthrough_after=answer, watch_seconds_elapsed=round(elapsed, 3),
                                     watch_completed=elapsed >= config.watch_seconds)
            self.finish()

        def finish(self):
            """Compose and save the report, once, and show the result screen."""
            if self.final is not None:
                return
            if self.phase == PHASE_WATCH and self.observations["watch_seconds_elapsed"] is None:
                self.observations["watch_seconds_elapsed"] = round(self.watch_elapsed(clock()), 3)
            if self.report is not None:
                report = self.report
            elif self.worker is not None:  # The loop ended under a run (the session went away): end the helper too.
                self.worker.stop()
                report = dict(unstarted_report(config, "guide_window_ended_during_run"), confirmation=OPERATOR_WINDOW,
                              cleanup_ok=False)
            else:
                report = unstarted_report(config, self.unstarted_reason)
            anchor = (report.get("timebase") or {}).get("anchor")
            report["guide"] = guidance.guide_block(
                self.guide, script=config.script, started=self.workers_started > 0, ended_by=self.ended_by,
                observations=self.observations, validation_only=config.validation,
                limits_ms={"late_frame_limit_ms": config.late_frame_limit_ms,
                           "removal_end_limit_ms": config.removal_end_limit_ms},
                clock=None if anchor is None else marks.Clock(anchor["monotonic_ns"], anchor["utc_ns"]),
                recorded_marks=(report.get("marks") or {}).get("recorded") or ())
            report["guide"]["worker_lines_dropped"] = 0 if self.worker is None else getattr(self.worker, "unexpected_lines", 0)
            report["camera_data"].update(report_saved_by_operator=True, report_path=str(config.report_path))
            try:
                camera_recognize.write_report(config.report_path, report)
            except (OSError, ValueError) as error:
                self.report_error = str(error)
                report["camera_data"].update(report_saved_by_operator=False, report_path=None)
                report.update(report_error=self.report_error, exit_code=1)
            self.final = report
            self.result_label.setText("\n".join(guidance.result_lines(report)))
            self.saved_label.setText(f"The report was NOT saved: {self.report_error}" if self.report_error else
                                     f"Report saved to: {config.report_path}")
            self.show_phase(PHASE_RESULT)

        def closeEvent(self, event):
            if self.phase == PHASE_RUN:  # The helper is ended first; the window closes when its report is in.
                self.close_requested = True
                self.stop_run(guidance.ENDED_WINDOW_CLOSED)
                event.ignore()
                return
            if self.phase in (PHASE_STATE, PHASE_NOTICE) and self.ended_by == guidance.ENDED_NOT_STARTED:
                self.ended_by = guidance.ENDED_WINDOW_CLOSED
            self.finish()
            self.timer.stop()
            event.accept()

    window = Window()
    if size is not None:
        window.resize(*size)
    return window


# ---- the validate mode --------------------------------------------------------------------------------------------

def describe_helper(binary):
    """The helper's ``--describe`` record (build facts only: no connect, no camera, no confirmation), or None."""
    try:
        completed = subprocess.run([camera_helper.helper_path(binary), "--describe"], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=DESCRIBE_TIMEOUT_SECONDS)
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    except (OSError, ValueError, subprocess.TimeoutExpired, SightCircuitError):
        return None
    return next((line for line in lines if isinstance(line, dict) and line.get("event") == "describe"), None)


VALIDATION_SCALE = 0.06  # A five-second step is 0.3 s, a four-second one 0.24 s.
VALIDATION_WATCH_SECONDS = 0.3
VALIDATION_WAIT_SECONDS = 40.0
VALIDATION_HELPER_ARGS = ("--pixel-path", "cpu_linear", "--fake-scenario")
VALIDATION_SIZE = (1280, 800)


class Flow:
    """Drives one window with Qt's test tools, as a pointer would: finds a button by its text and clicks it."""

    def __init__(self, config, **options):
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        self.QtCore, self.QtWidgets, self.QTest = QtCore, QtWidgets, QTest
        self.window = create_window(config, size=VALIDATION_SIZE, **options)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)

    def click(self, text):
        """Click the visible, enabled button that says ``text``; False when there is none."""
        for candidate in self.window.findChildren(self.QtWidgets.QPushButton):
            if candidate.text() == text and candidate.isVisible() and candidate.isEnabled():
                self.QTest.mouseClick(candidate, self.QtCore.Qt.MouseButton.LeftButton)
                return True
        return False

    def wait(self, condition, seconds=VALIDATION_WAIT_SECONDS):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            self.QTest.qWait(20)
        return bool(condition())

    def click_when_offered(self, text, seconds=VALIDATION_WAIT_SECONDS):
        return self.wait(lambda: self.click(text), seconds)

    def reach(self, phase, seconds=VALIDATION_WAIT_SECONDS):
        return self.wait(lambda: self.window.phase == phase, seconds)

    def finish_after_run(self, headset=None, passthrough=TEXT_UNCHANGED):
        """From the run's end to the result screen: the headset answer, the watch and its answer, then Close."""
        if headset is not None and not (self.reach(PHASE_BACK) and self.click(headset)):
            return False
        if not (self.reach(PHASE_WATCH) and self.click_when_offered(passthrough) and self.reach(PHASE_RESULT)):
            return False
        return self.close()

    def close(self):
        self.click(TEXT_CLOSE) or self.window.close()
        return self.wait(lambda: not self.window.isVisible())

    def run_screen_texts(self):
        return {text for entry in self.window.run_screen_log for text in entry[2:4] if text}


def validation_config(directory, name, script, helper, scenario, seconds, runtime_directory, scale=VALIDATION_SCALE):
    # The frame bound is left at the command's largest so that a run ends on its time bound or as its scenario says.
    return Config(script=script, helper=str(helper), report_path=Path(directory) / f"{name}.json",
                  limits=run_limits(script, seconds, MAX_FRAMES), helper_args=(*VALIDATION_HELPER_ARGS, scenario),
                  scale=scale, watch_seconds=VALIDATION_WATCH_SECONDS, validation=True,
                  worker_env={"XDG_RUNTIME_DIR": str(runtime_directory)})


def saved(config):
    """The report a flow left at its path, or None."""
    try:
        return json.loads(Path(config.report_path).read_text())
    except (OSError, ValueError):
        return None


def validate(helper=None):
    """The whole window flow with nothing real in it: the fake helper, a scripted detector, an offscreen window.

    The helper must say ``backend: fake`` in its ``--describe`` record before
    any window is made, so this mode can never open a camera. Seven flows,
    each clicked through with Qt's test tools and each leaving its report:
    both scripts, a run that reaches its bound, a presence end, Stop, the
    window closed mid-run, Cancel and a declined start; then the report path
    that is refused before a window exists. Returns the validation report.
    """
    helper = Path(guard.ROOT) / FAKE_HELPER if helper is None else Path(helper)
    report = {"report_kind": VALIDATION_KIND, "schema_version": 1, "status": "validation_failed", "reason": None,
              **gates.claims(), "validation_only": True, "images_saved": False,
              "pixels_in_report": False, "camera_opened": False, "display_started": False, "helper": None,
              "flows": {}, "exit_code": 1}
    described = describe_helper(helper)
    fake = described is not None and described.get("backend") == "fake" and described.get("sdk_release") is None
    report["helper"] = {"fake": fake, "backend": "fake" if fake else "withheld"}
    if not fake:
        report["reason"] = "validate_requires_fake_helper"
        return report
    os.environ["QT_QPA_PLATFORM"] = "offscreen"  # Nothing is ever put on a display by this mode.
    try:
        from PySide6 import QtWidgets
    except ImportError as error:
        report.update(reason="window_dependencies_unavailable", error=str(error))
        return report
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    report["qt_platform"] = application.platformName()
    flows = report["flows"]
    with tempfile.TemporaryDirectory(prefix="sightcircuit-camera-guide-validate-") as directory:
        runtime_directory = Path(directory) / "runtime"
        runtime_directory.mkdir(mode=0o700)

        def make(name, script, scenario="export_nv12", seconds=3, **options):
            return validation_config(directory, name, script, helper, scenario, seconds, runtime_directory, **options)

        def begin(config, **options):
            flow = Flow(config, **options)
            started = flow.click(TEXT_COLOUR_STABLE) and flow.reach(PHASE_NOTICE) and flow.click(TEXT_START)
            return flow, started

        def base(flow, config, ok):
            final = saved(config) or {}
            block = final.get("guide") or {}
            return {"ok": bool(ok), "status": final.get("status"), "reason": final.get("reason"),
                    "confirmation": final.get("confirmation"), "ended_by": block.get("ended_by"),
                    "workers_started": flow.window.workers_started, "report_saved": bool(final),
                    "frames_processed": final.get("frames_processed"),
                    "marks": [mark["label"] for mark in (final.get("marks") or {}).get("recorded", [])
                              if mark["source"] == marks.SOURCE_WINDOW],
                    "steps_not_reached": [entry["label"] for entry in block.get("steps_not_reached", [])],
                    "observations": block.get("observations"),
                    "run_screen_only_scripted": flow.run_screen_texts() <= guidance.static_texts(config.script, config.target),
                    "helper_cleanup_ok": final.get("cleanup_ok")}, final

        # 1. The positions script to the run's bound: no click after Start.
        config = make("positions", guidance.SCRIPT_POSITIONS)
        flow, started = begin(config)
        done = started and flow.finish_after_run()
        entry, final = base(flow, config, done)
        coverage = (final.get("summary") or {}).get("coverage") or {}
        entry.update(coverage_windows=coverage.get("windows"), result_lines=len(guidance.result_lines(final)) if final else 0)
        entry["ok"] = (entry["ok"] and entry["status"] == "recognized" and entry["confirmation"] == OPERATOR_WINDOW
                       and entry["marks"] == [step.label for step in guidance.POSITION_STEPS]
                       and entry["run_screen_only_scripted"] and entry["helper_cleanup_ok"] is True
                       and entry["observations"]["passthrough_after"] == guidance.PASSTHROUGH_UNCHANGED)
        flows["positions_to_the_bound"] = entry

        # 2. The removal script, clicked through; the fake's frames stop and the helper ends headset_removed.
        config = make("removal", guidance.SCRIPT_REMOVAL, "export_removal", 6, scale=VALIDATION_SCALE / 2)
        flow, started = begin(config)
        clicked = started and all(flow.click_when_offered(step.button) for step in guidance.REMOVAL_STEPS)
        done = clicked and flow.finish_after_run(headset=TEXT_BACK_ON)
        entry, final = base(flow, config, done)
        removal = ((final.get("summary") or {}).get("removal") or [{}])[0]
        entry.update(helper_end_reason=removal.get("helper_end_reason"),
                     removal_measured=removal.get("helper_end_after_mark_ms") is not None
                     and removal.get("helper_end_after_last_frame_ms") is not None)
        entry["ok"] = (entry["ok"] and (entry["status"], entry["reason"]) == ("helper_ended_early", "headset_removed")
                       and entry["marks"] == ["near", "far", "remove"] and entry["removal_measured"]
                       and entry["run_screen_only_scripted"] and entry["helper_cleanup_ok"] is True
                       and entry["observations"]["headset"] == guidance.HEADSET_REMOVED_AND_BACK)
        flows["removal_presence_end"] = entry

        # 3. Stop during the run; the watch is then answered "Something changed".
        config = make("stopped", guidance.SCRIPT_REMOVAL, seconds=20)
        flow, started = begin(config)
        stopped = started and flow.click_when_offered(guidance.REMOVAL_STEPS[0].button) and flow.click(TEXT_STOP)
        done = stopped and flow.finish_after_run(headset=TEXT_NOT_REMOVED, passthrough=TEXT_CHANGED)
        entry, final = base(flow, config, done)
        entry["ok"] = (entry["ok"] and entry["status"] == "stopped" and entry["ended_by"] == guidance.ENDED_STOP
                       and entry["marks"] == ["near"] and entry["steps_not_reached"] == ["far", "remove"]
                       and entry["helper_cleanup_ok"] is True
                       and entry["observations"]["passthrough_after"] == guidance.PASSTHROUGH_CHANGED)
        flows["stop_during_the_run"] = entry

        # 4. The window closed during the run: the helper is ended and the report is left.
        config = make("closed", guidance.SCRIPT_POSITIONS, seconds=20)
        flow, started = begin(config)
        closing = started and flow.wait(lambda: flow.window.guide.made) and (flow.window.close() or True)
        done = closing and flow.wait(lambda: not flow.window.isVisible())
        entry, final = base(flow, config, done)
        entry["ok"] = (entry["ok"] and entry["status"] == "stopped" and entry["ended_by"] == guidance.ENDED_WINDOW_CLOSED
                       and entry["helper_cleanup_ok"] is True and entry["observations"]["passthrough_after"] is None)
        flows["window_closed_during_the_run"] = entry

        # 5. Cancel under the notice, and 6. "Something else" as the starting state: nothing is launched.
        for name, clicks, reason in (("cancel", (TEXT_COLOUR_STABLE, TEXT_CANCEL), guidance.DECLINED),
                                     ("declined_start", (TEXT_SOMETHING_ELSE,), guidance.STARTING_STATE_NOT_STABLE)):
            config = make(name, guidance.SCRIPT_REMOVAL)
            flow = Flow(config)
            done = all(flow.click(text) for text in clicks) and flow.reach(PHASE_RESULT) and flow.close()
            entry, final = base(flow, config, done)
            entry["ok"] = (entry["ok"] and entry["workers_started"] == 0 and entry["confirmation"] is None
                           and (entry["status"], entry["reason"]) == ("error", reason)
                           and "capture" not in final and not flow.window.run_screen_log)
            flows[name] = entry

        # 7. A run too short for its script: the window says so and the report lists the steps not reached.
        config = make("out_of_time", guidance.SCRIPT_POSITIONS, seconds=2, scale=0.3)  # 1.2 s a step: one fits.
        flow, started = begin(config)
        done = started and flow.finish_after_run()
        entry, final = base(flow, config, done)
        entry["said_so"] = guidance.TEXT_OUT_OF_TIME in flow.run_screen_texts()
        entry["ok"] = (entry["ok"] and entry["status"] == "recognized" and bool(entry["steps_not_reached"])
                       and entry["said_so"] and entry["helper_cleanup_ok"] is True)
        flows["run_too_short_for_the_script"] = entry

        # The report path is checked before a window exists: an existing file, a missing directory, another suffix.
        refused = []
        for path in (Path(directory) / "positions.json", Path(directory) / "missing" / "a.json", Path(directory) / "a.txt"):
            try:
                check_report_path(path)
            except ValueError:
                refused.append(path.name)
        flows["report_path_checked_first"] = {"ok": refused == ["positions.json", "a.json", "a.txt"], "refused": refused}
    failed = sorted(name for name, entry in flows.items() if not entry["ok"])
    report.update(flows_failed=failed, status="validation_failed" if failed else "validated",
                  reason="flows_failed" if failed else None, exit_code=1 if failed else 0)
    return report


# ---- the command line ---------------------------------------------------------------------------------------------

def run_window(config, *, windowed=False):
    """Open the window for the wearer and run Qt until it closes; the saved report (or the one that could not be)."""
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(guard.ROOT) / ".cache"))
    from PySide6 import QtWidgets
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_window(config, size=(1280, 800) if windowed else None)

    def note_signal(number, _frame):  # Taken up on the window's next tick: the helper is ended, the report kept.
        window.signal_number = number
    previous = {number: signal.signal(number, note_signal) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        window.show() if windowed else window.showMaximized()
        application.exec()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    window.finish()
    return window.final


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-camera-input", action="store_true",
                        help="Opt in to a window whose Start button launches the camera helper (Camera Data)")
    parser.add_argument("--validate", action="store_true",
                        help="The whole window flow offscreen against the helper's fake build; nothing real, nothing shown")
    parser.add_argument(WORKER_FLAG, action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--script", choices=tuple(guidance.SCRIPTS),
                        help="removal: two distances, then the headset comes off; positions: five places, no clicks")
    parser.add_argument("--helper", type=Path, help="The sightcircuit-camera-helper binary, given explicitly; never searched "
                                                    "(--validate: a fake-backend build; default the one under artifacts/)")
    parser.add_argument("--report", type=Path,
                        help="New .json file for the report, checked before the window opens; naming it is your "
                             "action to save Camera Data")
    parser.add_argument("--target-label", default=DEFAULT_TARGET, metavar="CLASS",
                        help=f"The detector class the wearer holds up; default {DEFAULT_TARGET}")
    parser.add_argument("--seconds", type=int, choices=range(1, MAX_SECONDS + 1), metavar=f"1..{MAX_SECONDS}",
                        help="The run's time bound; default 30 for removal, 25 for positions")
    parser.add_argument("--max-frames", type=int, choices=range(1, MAX_FRAMES + 1), metavar=f"1..{MAX_FRAMES}",
                        help="The run's frame bound; default seconds times frames a second")
    parser.add_argument("--fps", type=int, choices=range(1, MAX_FPS + 1), default=DEFAULT_FPS, metavar=f"1..{MAX_FPS}")
    parser.add_argument("--camera", type=int, choices=contract.CAMERA_INDICES, default=LEFT_CAMERA,
                        help="The one camera to export; default 0, the left")
    parser.add_argument("--threshold", type=float, default=POC_THRESHOLD)
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR)
    parser.add_argument("--helper-arg", action="append", default=[], metavar="ARG",
                        help="An extra argument for the helper after the contract ones")
    parser.add_argument("--late-frame-limit-ms", type=int, default=marks.LATE_FRAME_LIMIT_MS, metavar="MS",
                        help="What each removal entry compares frame times after the remove click with")
    parser.add_argument("--removal-end-limit-ms", type=int, default=marks.REMOVAL_END_LIMIT_MS, metavar="MS",
                        help="What each removal entry compares the helper's end after the remove click with")
    parser.add_argument("--windowed", action="store_true", help="A 1280 by 800 window in place of a maximized one")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main()
    run_arguments = (args.allow_camera_input or args.script or args.report or args.seconds or args.max_frames
                     or args.helper_arg or args.windowed)
    if args.validate:
        if run_arguments:
            parser.error("--validate takes only --helper")
        report = validate(args.helper)
        print(json.dumps(report, indent=2))
        return report["exit_code"]
    if not args.allow_camera_input:
        parser.error("--allow-camera-input is required to open the window (or use --validate)")
    if args.helper is None or args.report is None or args.script is None:
        parser.error("--helper, --report and --script are required")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if args.late_frame_limit_ms < 0 or args.removal_end_limit_ms < 0:
        parser.error("the limits are milliseconds, zero or more")
    if args.target_label not in class_names(args.detector):
        parser.error(f"--target-label must be a class of {args.detector}: {', '.join(class_names(args.detector))}")
    try:
        limits = run_limits(args.script, args.seconds, args.max_frames, args.fps)
    except ValueError as error:
        parser.error(str(error))
    config = Config(script=args.script, helper=str(args.helper), report_path=Path(args.report).expanduser(), limits=limits,
                    camera=args.camera, target=args.target_label, detector=args.detector, threshold=args.threshold,
                    helper_args=tuple(args.helper_arg), late_frame_limit_ms=args.late_frame_limit_ms,
                    removal_end_limit_ms=args.removal_end_limit_ms)
    refusal = None
    try:  # The report's path first: nothing is shown to the wearer for a run whose report could not be saved.
        config.report_path = check_report_path(args.report)
    except ValueError as error:
        refusal = ("report_path_refused", str(error))
    if refusal is None:
        try:
            config.helper = camera_helper.helper_path(args.helper)
        except SightCircuitError as error:
            refusal = (error.reason, str(error))
    if refusal is None and not os.environ.get("QT_QPA_PLATFORM") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        refusal = ("no_display_session", "No display session found. Run in the Frame's graphical session.")
    if refusal is None:
        try:
            import PySide6  # noqa: F401
        except ImportError as error:
            refusal = ("window_dependencies_unavailable", f"{error}. Run bash scripts/setup_window.sh")
    if refusal is not None:  # Before any window: nothing was shown, launched or saved.
        report = dict(unstarted_report(config, refusal[0]), error=refusal[1])
        report["guide"] = guidance.guide_block(
            None, script=config.script, started=False, ended_by=guidance.ENDED_NOT_STARTED,
            observations={"starting_state": None, "passthrough_after": None, "headset": None,
                          "watch_seconds_elapsed": None, "watch_completed": False},
            limits_ms={"late_frame_limit_ms": config.late_frame_limit_ms,
                       "removal_end_limit_ms": config.removal_end_limit_ms})
    else:
        report = run_window(config, windowed=args.windowed)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
