"""Check the video/inference/window stack using generated video, not live capture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(ROOT))
# sightcircuit is imported only inside functions, after main() has invalidated the previous report.
ASSETS = ROOT / ".cache" / "sightcircuit" / "assets"
RESULTS = ROOT / "artifacts" / "smoke"
FRAME_COUNT = 12
THRESHOLD = 0.5
# What the sample image shows, in canonical names; each detector must find every label it can name.
FIXTURE = {"id": "dog.jpg", "present": ["bicycle", "dog", ["car", "truck"]]}


def require_fixture_labels(detector, result):
    """Fail unless ``result`` holds every fixture label ``detector``'s vocabulary has."""
    from sightcircuit import evaluation

    score = evaluation.score_image(FIXTURE, result.detections, detector.classes)
    if score["present_misses"] or not score["present_hits"]:
        missing = ", ".join(score["present_misses"]) or "any fixture label"
        raise RuntimeError(f"{detector.name} expected {missing}; got {result.labels}")


def check_detectors(image):
    """Create every registered detector, with no per-name code, and check it on the fixture."""
    from sightcircuit.detection import DETECTORS, create_detector, describe

    detectors, report = {}, {}
    for name in DETECTORS:
        detector = create_detector(name)
        result = detector.detect(image, THRESHOLD)
        require_fixture_labels(detector, result)
        detectors[name] = detector
        report[name] = {**describe(detector), "labels": result.labels}
    return detectors, report


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS / "readiness.json"
    report_path.unlink(missing_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
    Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)

    import cv2
    import numpy as np
    import onnxruntime as ort
    from PySide6 import QtCore, QtGui, QtWidgets

    from sightcircuit.detection import DEFAULT_DETECTOR, annotate, describe

    ort.disable_telemetry_events()

    # Execute an actual bundled ONNX graph, checking numerical results.
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(Path(ort.__file__).parent / "datasets" / "mul_1.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    matrix = np.arange(1, 7, dtype=np.float32).reshape(3, 2)
    actual = session.run(None, {"X": matrix})[0]
    np.testing.assert_array_equal(actual, matrix * matrix)

    image = cv2.imread(str(ASSETS / "dog.jpg"))
    if image is None:
        raise RuntimeError("Sample image did not decode")
    height, width = image.shape[:2]
    video_path = RESULTS / "sample.avi"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError("MJPEG encoder unavailable")
    try:
        for _ in range(FRAME_COUNT):
            writer.write(image)
    finally:
        writer.release()

    # The application's shared, checksum-verified detectors rather than copies of them.
    detectors, checked = check_detectors(image)
    detector = detectors[DEFAULT_DETECTOR]
    capture = cv2.VideoCapture(str(video_path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError("FFmpeg video capture failed")
    backend = capture.getBackendName()

    app = QtWidgets.QApplication([])
    window = QtWidgets.QLabel()
    window.setWindowTitle("SightCircuit environment validation")
    window.resize(width, height)
    window.show()
    state = {"frames": 0, "labels": [], "confidence": {}}
    failures: list[Exception] = []
    screenshot_path = RESULTS / "detection-window.png"
    screenshot_path.unlink(missing_ok=True)
    timer = QtCore.QTimer()

    def abort(exc: Exception) -> None:
        failures.append(exc)
        timer.stop()
        app.exit(1)

    def tick() -> None:
        try:
            ok, frame = capture.read()
            if not ok:
                if state["frames"] != FRAME_COUNT:
                    raise RuntimeError(f"Expected {FRAME_COUNT} frames, got {state['frames']}")
                pixmap = window.grab()
                if pixmap.isNull() or not pixmap.save(str(screenshot_path)):
                    raise RuntimeError("Window screenshot failed")
                timer.stop()
                app.quit()
                return
            result = detector.detect(frame, THRESHOLD)
            found = {detection.label: round(detection.confidence, 4) for detection in result.detections}
            frame = annotate(frame, result)
            require_fixture_labels(detector, result)
            rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            qt_image = QtGui.QImage(
                rgb.data, width, height, rgb.strides[0], QtGui.QImage.Format.Format_RGB888
            ).copy()
            window.setPixmap(QtGui.QPixmap.fromImage(qt_image))
            state["frames"] += 1
            state["labels"] = sorted(found)
            state["confidence"] = found
        except Exception as exc:
            abort(exc)

    timer.timeout.connect(tick)
    timer.start(1)
    QtCore.QTimer.singleShot(30_000, lambda: abort(RuntimeError("Validation timed out")))
    try:
        exit_code = app.exec()
    finally:
        capture.release()
        window.close()
    if failures:
        raise failures[0]
    if exit_code:
        raise RuntimeError(f"Qt exited with status {exit_code}")
    screenshot = cv2.imread(str(screenshot_path))
    if screenshot is None or screenshot.std() < 10:
        raise RuntimeError("Rendered screenshot is blank or invalid")
    report = {
        "status": "passed",
        "fixture": "12 repeated sample-image frames encoded as MJPEG; not an Arcturus recording",
        "python": os.sys.version.split()[0],
        "opencv": cv2.__version__,
        "qt": QtCore.qVersion(),
        "onnxruntime": ort.__version__,
        "onnx_numerical_inference": "passed",
        "detector": describe(detector),
        "detectors": checked,
        "capture_backend": backend,
        "qt_platform": app.platformName(),
        **state,
        "screenshot": str(screenshot_path),
        "hardware_checks": "Camera, headset, VR overlay and device acceleration not tested",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
