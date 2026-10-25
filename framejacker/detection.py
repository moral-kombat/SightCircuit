"""Detector interface, the checksum-verified MobileNet-SSD baseline and annotation."""
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Protocol

from .frame import Detection, DetectionResult

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/framejacker/assets"
MODEL_FILES = ("MobileNetSSD_deploy.prototxt", "MobileNetSSD_deploy.caffemodel")
CLASSES = (
    "background aeroplane bicycle bird boat bottle bus car cat chair cow "
    "diningtable dog horse motorbike person pottedplant sheep sofa train tvmonitor"
).split()


class Detector(Protocol):
    def detect(self, bgr, threshold: float) -> DetectionResult:
        """Detect objects in a nonempty uint8 BGR image; boxes use its pixel space."""


def verify_model():
    manifest = json.loads((ROOT / "scripts/smoke-assets.json").read_text())
    expected = {asset["name"]: asset["sha256"] for asset in manifest["assets"] if asset["name"] in MODEL_FILES}
    if set(expected) != set(MODEL_FILES):
        raise RuntimeError("Detector model checksums are missing from scripts/smoke-assets.json")
    for name, digest in expected.items():
        path = ASSETS / name
        if not path.is_file():
            raise RuntimeError("Detector model missing. Run bash scripts/setup_window.sh")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                raise RuntimeError(f"Detector model failed SHA-256 verification: {path.name}")


def validate_input(bgr, threshold):
    import numpy as np

    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be between zero and one")
    if bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
        raise ValueError("Detector needs a nonempty uint8 BGR image")


class MobileNetSSD:
    """Pascal VOC MobileNet-SSD through OpenCV DNN on CPU (20 foreground classes)."""

    def __init__(self):
        import cv2
        import numpy as np

        verify_model()
        self.cv2, self.np = cv2, np
        cv2.setNumThreads(2)
        self.net = cv2.dnn.readNetFromCaffe(str(ASSETS / MODEL_FILES[0]), str(ASSETS / MODEL_FILES[1]))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def detect(self, bgr, threshold):
        cv2, np = self.cv2, self.np
        validate_input(bgr, threshold)
        height, width = bgr.shape[:2]
        self.net.setInput(cv2.dnn.blobFromImage(bgr, 0.007843, (300, 300), 127.5))
        started = time.monotonic()
        prediction = self.net.forward()
        elapsed = (time.monotonic() - started) * 1000
        if prediction.ndim != 4 or prediction.shape[-1] != 7:
            raise RuntimeError("Unexpected detector output shape")
        detections = []
        for row in prediction.reshape(-1, 7):
            if not np.isfinite(row).all() or row[2] < threshold:
                continue
            class_id = int(row[1])
            if not 0 < class_id < len(CLASSES):
                continue
            bounds = (np.clip(row[3:7], 0, 1) * np.array([width, height, width, height])).astype(int)
            x1, y1, x2, y2 = map(int, bounds)
            x1, x2 = min(x1, width - 1), min(x2, width - 1)
            y1, y2 = min(y1, height - 1), min(y2, height - 1)
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(Detection(CLASSES[class_id], round(float(row[2]), 5), (x1, y1, x2, y2)))
        return DetectionResult(tuple(detections), elapsed)


def annotate(bgr, result):
    """Draw boxes and labels on an owned copy of the image the detector saw."""
    import cv2

    image = bgr.copy()
    for detection in result.detections:
        x1, y1, x2, y2 = detection.box_xyxy
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 230, 0), 2)
        cv2.putText(image, f"{detection.label} {detection.confidence:.0%}", (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
    return image
