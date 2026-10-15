"""Check the video/inference/window stack using generated video, not live capture."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache" / "framejacker" / "assets"
RESULTS = ROOT / "artifacts" / "smoke"
CLASSES = (
    "background aeroplane bicycle bird boat bottle bus car cat chair cow "
    "diningtable dog horse motorbike person pottedplant sheep sofa train tvmonitor"
).split()
FRAME_COUNT = 12


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

    cv2.setNumThreads(2)
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

    net = cv2.dnn.readNetFromCaffe(
        str(ASSETS / "MobileNetSSD_deploy.prototxt"),
        str(ASSETS / "MobileNetSSD_deploy.caffemodel"),
    )
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    capture = cv2.VideoCapture(str(video_path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError("FFmpeg video capture failed")
    backend = capture.getBackendName()

    app = QtWidgets.QApplication([])
    window = QtWidgets.QLabel()
    window.setWindowTitle("Framejacker environment validation")
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
            net.setInput(cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5))
            prediction = net.forward()
            if prediction.shape != (1, 1, 100, 7):
                raise RuntimeError(f"Unexpected detector output shape: {prediction.shape}")
            found = {}
            for detection in prediction[0, 0]:
                confidence = float(detection[2])
                if confidence < 0.5:
                    continue
                name = CLASSES[int(detection[1])]
                found[name] = round(confidence, 4)
                coords = detection[3:7] * np.array([width, height, width, height])
                x1, y1, x2, y2 = coords.astype(int)
                if x2 <= x1 or y2 <= y1:
                    raise RuntimeError("Invalid detection bounding box")
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 230, 0), 3)
                cv2.putText(frame, f"{name} {confidence:.2f}", (x1, max(22, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 230, 0), 2)
            if not {"bicycle", "car", "dog"}.issubset(found):
                raise RuntimeError(f"Expected bicycle, car, dog detections; got {found}")
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
        "detector": "MobileNet-SSD (OpenCV DNN CPU)",
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
