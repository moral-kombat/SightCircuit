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
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, create_detector, describe
from sightcircuit.pipeline import LatestResultSink, Pipeline, report_fields
from sightcircuit.sources.replay import ReplaySource
from sightcircuit.sources.recording import RecordingError, latest_recording

ROOT = Path(__file__).resolve().parents[1]
WORKER_JOIN_SECONDS = 5  # Shutdown wait; a worker still running afterwards fails the run.


class ReplayWorker(threading.Thread):
    """Run file replay through the shared pipeline; retain one latest result for the UI."""
    def __init__(self, path, ordinal, threshold, max_fps, max_frames, sinks=(), detector=DEFAULT_DETECTOR,
                 realtime=False, decode_threads=2, source=None):
        super().__init__(daemon=True)
        self.detector_name = detector
        live = source is not None
        self.source = source or ReplaySource(path, ordinal, realtime=realtime, decode_threads=decode_threads)
        # Live sources have no transport; the controls become inert events.
        self.playing = getattr(self.source, "playing", threading.Event())
        self.restart = getattr(self.source, "restart", threading.Event())
        self.looping = getattr(self.source, "looping", threading.Event())
        self.at_end = getattr(self.source, "at_end", threading.Event())
        self.stopping, self.done = threading.Event(), threading.Event()
        self.results = LatestResultSink()
        # Realtime decoding runs on its own thread and only the newest frame is
        # processed; otherwise every decoded frame is processed in order.
        self.pipeline = Pipeline(self.source, None, (self.results, *sinks), threshold=threshold,
                                 max_fps=max_fps, latest_only=live or realtime, max_frames=max_frames, join_timeout=5)
        self.error = None

    @property
    def frames_processed(self):
        return self.pipeline.stats.frames_processed

    @property
    def stream_index(self):
        return getattr(self.source, "stream_index", None)

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


# Schema field names kept for existing reports and evidence; "pipeline" holds the canonical block.
LEGACY_FIELDS = {"frames_replaced": "pending_replaced", "processed_fps": "processed_fps",
                 "mean_processing_ms": "mean_processing_ms", "max_input_age_ms": "max_input_age_ms"}


def pacing_report(worker, args, latencies, schedule_lateness):
    """Throughput and timing fields: decode, skipping, processing and presentation.

    ``realtime_ratio`` is media seconds per wall second, counting catch-up jumps
    as progress; ``processed_media_fraction`` is frames processed per frame the
    played span covered (``ReplaySource.processed_fraction``), null before any.
    ``statistics_final`` is false when the worker was still running, so the
    counts are a snapshot that the engine may since have extended.
    """
    source, stats = worker.source, worker.pipeline.stats
    elapsed = max(stats.elapsed_s, 1e-9)
    block = stats.report()
    processed = source.processed_fraction(stats.frames_processed)
    fields = {"realtime": args.realtime, "decode_threads": args.decode_threads,
              "frames_decoded": source.frames_decoded, "decode_fps": round(source.frames_decoded / elapsed, 3),
              "frames_dropped": source.frames_dropped, "frames_skipped": source.frames_skipped,
              "catch_ups": source.catch_ups, "media_seconds_played": round(source.media_seconds, 3),
              "realtime_ratio": round(source.media_seconds / elapsed, 3),
              "media_frames_spanned": source.frames_spanned,
              "processed_media_fraction": round(processed, 3) if processed is not None else None,
              **report_fields(block, LEGACY_FIELDS), "pipeline": block,
              "statistics_final": not worker.is_alive()}
    if latencies:
        fields.update(mean_presentation_latency_ms=round(sum(latencies) / len(latencies), 3),
                      max_presentation_latency_ms=round(max(latencies), 3))
    if schedule_lateness:
        fields["max_schedule_lateness_ms"] = round(max(schedule_lateness), 3)
    return fields


def engine_started(worker):
    """True once the worker's engine started, so the report carries its statistics (final or not)."""
    return worker is not None and worker.pipeline.stats.started is not None


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
    parser.add_argument("--realtime", action="store_true",
                        help="Follow media time: process the newest decoded frame and skip to keyframes when decoding falls behind")
    parser.add_argument("--decode-threads", type=int, default=2,
                        help="Decoder frame/slice threads; default 2. More decode faster but compete with inference")
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
    if not 1 <= args.decode_threads <= 16:
        parser.error("Decoder threads must be between 1 and 16")
    report = {"report_kind": "annotated_file_replay", "status": "error",
              "video_stream_ordinal": args.video_stream, "frames_presented": 0,
              "labels_seen": [], "images_saved": False,
              "overlay_requested": args.overlay, "detector_requested": args.detector,
              "scope": "File replay; live camera, HDR appearance, stereo alignment and VR overlay unvalidated"}
    worker = None
    overlay = None
    latencies, schedule_lateness = [], []
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
            from sightcircuit.overlay import PANEL_LIMITS, OverlayClient, OverlaySink, PanelSettings
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
        window.setWindowTitle("SightCircuit — annotated replay")
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
            labels = {"width": "Width (m)", "distance": "Distance (m)", "x": "Horizontal (m)",
                      "y": "Vertical (m)", "opacity": "Opacity"}
            defaults = PanelSettings()
            for index, (name, low, high) in enumerate(PANEL_LIMITS):
                label, value = labels[name], getattr(defaults, name)
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
                              realtime=args.realtime, decode_threads=args.decode_threads,
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
                    # Decode completion to presentation, including inference and UI polling.
                    latencies.append((time.monotonic_ns() - result.frame.observed_ns) / 1e6)
                    if "schedule_lateness_ms" in result.frame.metadata:
                        schedule_lateness.append(result.frame.metadata["schedule_lateness_ms"])
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
            worker.join(timeout=WORKER_JOIN_SECONDS)
            window.close()
        if worker.is_alive():
            raise RuntimeError(f"Replay worker did not shut down within {WORKER_JOIN_SECONDS:g} seconds")
        if failures or worker.error or exit_code:
            raise RuntimeError("; ".join(failures) or worker.error or f"Qt exited with code {exit_code}")
        report.update(status="finished" if args.max_frames and worker.frames_processed >= args.max_frames else "closed",
                      frames_processed=worker.frames_processed, labels_seen=sorted(labels_seen),
                      detector=describe(worker.pipeline.detector),
                      **pacing_report(worker, args, latencies, schedule_lateness),
                      container_stream_index=worker.stream_index, qt_platform=app.platformName())
    except Exception as error:
        report["error"] = str(error)
        if engine_started(worker):  # A failed run keeps the statistics of the frames it processed.
            try:
                report.update(frames_processed=worker.frames_processed,
                              **pacing_report(worker, args, latencies, schedule_lateness))
            except Exception as failure:  # The error report itself is never lost to its statistics.
                report["statistics_error"] = str(failure)
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
