"""Show annotated file replay in Qt, optionally mirrored into a public OpenVR panel."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framejacker.detection import DEFAULT_DETECTOR, DETECTORS, create_detector, describe
from framejacker.pipeline import LatestResultSink, Pipeline
from framejacker.sources.replay import ReplaySource
from scripts.recording_check import RecordingError, latest_recording

ROOT = Path(__file__).resolve().parents[1]


class ReplayWorker(threading.Thread):
    """Run file replay through the shared pipeline; retain one latest result for the UI."""
    def __init__(self, path, ordinal, threshold, max_fps, max_frames, sinks=(), detector=DEFAULT_DETECTOR):
        super().__init__(daemon=True)
        self.detector_name = detector
        self.source = ReplaySource(path, ordinal)
        self.playing, self.restart = self.source.playing, self.source.restart
        self.looping, self.at_end = self.source.looping, self.source.at_end
        self.stopping, self.done = threading.Event(), threading.Event()
        self.results = LatestResultSink()
        self.pipeline = Pipeline(self.source, None, (self.results, *sinks), threshold=threshold,
                                 max_fps=max_fps, latest_only=False, max_frames=max_frames)
        self.error = None

    @property
    def frames_processed(self):
        return self.pipeline.stats.frames_processed

    @property
    def stream_index(self):
        return self.source.stream_index

    @property
    def limit(self):
        return self.pipeline.max_frames

    @limit.setter
    def limit(self, value):
        self.pipeline.max_frames = value

    def take_latest(self):
        return self.results.take()

    def stop(self):
        self.stopping.set()
        self.playing.set()
        self.restart.set()

    def run(self):
        try:
            self.pipeline.detector = create_detector(self.detector_name)
            self.pipeline.run(self.stopping)
        except Exception as error:
            self.error = str(error)
        finally:
            self.done.set()


def check_display_libraries(QtCore):
    """Report missing XCB libraries before Qt's native fatal-error path."""
    platform = os.environ.get("QT_QPA_PLATFORM", "").split(":")[0]
    if platform in ("offscreen", "minimal") or not os.environ.get("DISPLAY"):
        return
    if platform and platform != "xcb":
        return
    if os.environ.get("WAYLAND_DISPLAY") and platform != "xcb":
        return
    ldd = shutil.which("ldd")
    plugin = Path(QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/libqxcb.so"
    if ldd and plugin.is_file():
        result = subprocess.run([ldd, str(plugin)], capture_output=True, text=True, timeout=10)
        missing = [line.strip() for line in result.stdout.splitlines() if "not found" in line]
        if missing:
            raise RuntimeError("Qt X11 libraries are missing: " + "; ".join(missing))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Defaults to newest Arcturus stereo recording")
    parser.add_argument("--video-stream", type=int, default=0, help="Video-track ordinal; no left/right assumption")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR,
                        help="Model; mobilenet-ssd is the device-validated baseline, yolox-nano an 80-class candidate")
    parser.add_argument("--max-fps", type=float, default=10, help="Processing/playback cap; default 10")
    parser.add_argument("--max-frames", type=int, default=0, help="Exit after this many processed frames; default unlimited")
    parser.add_argument("--preview", type=Path, help="Explicitly save the last displayed window as PNG")
    parser.add_argument("--report", type=Path, help="Explicitly save the final JSON summary")
    parser.add_argument("--overlay", action="store_true", help="Also show a head-relative OpenVR panel; test the native panel first")
    args = parser.parse_args(argv)
    if args.video_stream < 0 or args.max_frames < 0:
        parser.error("Stream ordinal and frame limit must be nonnegative")
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("Threshold must be between zero and one")
    if not math.isfinite(args.max_fps) or not 1 <= args.max_fps <= 60:
        parser.error("Processing cap must be between 1 and 60 fps")
    report = {"report_kind": "annotated_file_replay", "status": "error",
              "video_stream_ordinal": args.video_stream, "frames_presented": 0,
              "labels_seen": [], "images_saved": False,
              "overlay_requested": args.overlay, "detector_requested": args.detector,
              "scope": "File replay; live camera, HDR appearance, stereo alignment and VR overlay unvalidated"}
    worker = None
    overlay = None
    try:
        path = (Path(args.path).expanduser() if args.path else latest_recording(
            Path.home() / "Videos/ArcturusVisionCamera")).resolve()
        if not path.is_file():
            raise RecordingError("Recording path must be a regular file")
        for output in (args.preview, args.report):
            if output and (output.exists() or output.resolve() == path):
                raise RuntimeError(f"Output already exists or overlaps the recording: {output}")
        if args.preview and args.preview.suffix.lower() != ".png":
            raise RuntimeError("Preview output must use .png")
        if args.preview and args.report and args.preview.resolve() == args.report.resolve():
            raise RuntimeError("Preview and report need distinct paths")
        if not os.environ.get("QT_QPA_PLATFORM") and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("No display session found. Run in the Frame's graphical session.")
        os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError as error:
            raise RuntimeError(f"Window dependencies unavailable: {error}. Run bash scripts/setup_window.sh") from error
        check_display_libraries(QtCore)
        if args.overlay:
            from framejacker.overlay import OverlayClient, OverlaySink, PanelSettings
            overlay = OverlayClient()
            overlay.start()

        class Canvas(QtWidgets.QWidget):
            def __init__(self):
                super().__init__()
                self.image = QtGui.QImage()
                self.setMinimumSize(320, 240)

            def paintEvent(self, event):
                painter = QtGui.QPainter(self)
                painter.fillRect(self.rect(), QtGui.QColor("black"))
                if not self.image.isNull():
                    image = self.image.scaled(self.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                              QtCore.Qt.TransformationMode.SmoothTransformation)
                    painter.drawImage((self.width() - image.width()) // 2,
                                      (self.height() - image.height()) // 2, image)

        app = QtWidgets.QApplication([])
        window = QtWidgets.QWidget()
        window.setWindowTitle("Framejacker — annotated replay")
        window.resize(960, 800)
        layout = QtWidgets.QVBoxLayout(window)
        title = QtWidgets.QLabel(f"{path.name} · video track {args.video_stream} · {args.detector} · "
                                 f"replay up to {args.max_fps:g} fps")
        title.setWordWrap(True)
        layout.addWidget(title)
        canvas = Canvas()
        layout.addWidget(canvas, 1)
        status = QtWidgets.QLabel("Loading demo detector…")
        status.setWordWrap(True)
        layout.addWidget(status)
        controls = QtWidgets.QHBoxLayout()
        pause, replay = QtWidgets.QPushButton("Pause"), QtWidgets.QPushButton("Replay")
        loop = QtWidgets.QCheckBox("Loop")
        loop.setChecked(True)
        for control in (pause, replay, loop):
            controls.addWidget(control)
        layout.addLayout(controls)
        if overlay:
            panel = QtWidgets.QGroupBox("VR panel — follows your head")
            grid = QtWidgets.QGridLayout(panel)
            visible = QtWidgets.QCheckBox("Show VR panel")
            visible.setChecked(True)
            grid.addWidget(visible, 0, 0, 1, 2)
            fields = {}
            for index, (name, label, low, high, value) in enumerate((
                    ("width", "Width (m)", .25, 1.5, .8),
                    ("distance", "Distance (m)", .5, 3, 1.5),
                    ("x", "Horizontal (m)", -1, 1, 0),
                    ("y", "Vertical (m)", -1, 1, -.2),
                    ("opacity", "Opacity", .2, 1, .95))):
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(low, high)
                spin.setSingleStep(.05)
                spin.setValue(value)
                row, column = 1 + index // 3, (index % 3) * 2
                grid.addWidget(QtWidgets.QLabel(label), row, column)
                grid.addWidget(spin, row, column + 1)
                fields[name] = spin

            def configure_panel():
                overlay.set_settings(PanelSettings(
                    **{name: field.value() for name, field in fields.items()},
                    visible=visible.isChecked()))

            for field in fields.values():
                field.valueChanged.connect(configure_panel)
            visible.toggled.connect(configure_panel)
            layout.addWidget(panel)
        worker = ReplayWorker(path, args.video_stream, args.threshold, args.max_fps, args.max_frames,
                              sinks=(OverlaySink(overlay),) if overlay else (), detector=args.detector)

        def toggle_pause():
            if worker.playing.is_set():
                worker.playing.clear()
                pause.setText("Resume")
            else:
                worker.playing.set()
                pause.setText("Pause")

        def restart():
            worker.restart.set()
            worker.playing.set()
            pause.setText("Pause")

        pause.clicked.connect(toggle_pause)
        replay.clicked.connect(restart)
        loop.toggled.connect(lambda enabled: worker.looping.set() if enabled else worker.looping.clear())
        timer = QtCore.QTimer()
        labels_seen = set()
        failures = []

        def poll():
            try:
                if overlay and overlay.error:
                    raise RuntimeError("VR panel: " + overlay.error)
                if overlay and overlay.done.is_set():
                    app.quit()
                    return
                result = worker.take_latest()
                if result:
                    image = result.image
                    canvas.image = QtGui.QImage(image.data, image.shape[1], image.shape[0], image.strides[0],
                                               QtGui.QImage.Format.Format_BGR888).copy()
                    canvas.update()
                    report["frames_presented"] += 1
                    labels_seen.update(result.labels)
                    media_time = result.frame.media_time
                    media = f"{media_time:.3f}s" if media_time is not None else "unknown time"
                    status.setText(f"Frame {result.index} · {media} · inference {result.result.inference_ms:.0f} ms · "
                                   + (", ".join(result.labels) or "No objects above threshold"))
                if worker.done.is_set():
                    if worker.error:
                        raise RuntimeError(worker.error)
                    app.quit()
            except Exception as error:
                failures.append(str(error))
                app.exit(1)

        timer.timeout.connect(poll)
        timer.start(16)
        window.show()
        worker.start()
        previous_sigint = signal.signal(signal.SIGINT, lambda *_: app.quit())
        try:
            exit_code = app.exec()
            if args.preview and report["frames_presented"] and not failures:
                if not window.grab().save(str(args.preview)):
                    raise RuntimeError("Preview PNG could not be saved")
                report["images_saved"] = True
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            timer.stop()
            worker.stop()
            worker.join(timeout=5)
            window.close()
        if worker.is_alive():
            raise RuntimeError("Replay worker did not shut down within five seconds")
        if failures or worker.error or exit_code:
            raise RuntimeError("; ".join(failures) or worker.error or f"Qt exited with code {exit_code}")
        report.update(status="finished" if args.max_frames and worker.frames_processed >= args.max_frames else "closed",
                      frames_processed=worker.frames_processed, labels_seen=sorted(labels_seen),
                      detector=describe(worker.pipeline.detector),
                      container_stream_index=worker.stream_index, qt_platform=app.platformName())
    except Exception as error:
        report["error"] = str(error)
    finally:
        if overlay:
            report["overlay"] = overlay.close()
            if report["overlay"]["error"]:
                report["status"] = "error"
                report.setdefault("error", "VR panel: " + report["overlay"]["error"])
    print(json.dumps(report, indent=2))
    if args.report:
        # Exclusive creation prevents overwriting a prior report or input file.
        try:
            with args.report.open("x") as output:
                output.write(json.dumps(report, indent=2) + "\n")
        except OSError as error:
            print(f"Could not save report: {error}", file=sys.stderr)
            return 1
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
