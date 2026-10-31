"""Checksum-verified CPU demo detector shared by file and mirror checks."""
import hashlib
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/framejacker/assets"
CLASSES = (
    "background aeroplane bicycle bird boat bottle bus car cat chair cow "
    "diningtable dog horse motorbike person pottedplant sheep sofa train tvmonitor"
).split()


def verify_model():
    manifest = json.loads((ROOT / "scripts/smoke-assets.json").read_text())
    for asset in manifest["assets"]:
        if not asset["name"].startswith("MobileNetSSD_"):
            continue
        path = ASSETS / asset["name"]
        if not path.is_file():
            raise RuntimeError("Detector model missing. Run bash scripts/setup_window.sh")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != asset["sha256"]:
                raise RuntimeError(f"Detector model failed SHA-256 verification: {path.name}")


class DemoDetector:
    def __init__(self):
        import cv2
        import numpy as np

        verify_model()
        self.cv2, self.np = cv2, np
        cv2.setNumThreads(2)
        self.net = cv2.dnn.readNetFromCaffe(
            str(ASSETS / "MobileNetSSD_deploy.prototxt"),
            str(ASSETS / "MobileNetSSD_deploy.caffemodel"))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def detect(self, bgr, threshold):
        cv2, np = self.cv2, self.np
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("Threshold must be between zero and one")
        if bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
            raise ValueError("Detector needs a nonempty uint8 BGR image")
        image = bgr.copy()  # Annotations always belong to this owned input copy.
        height, width = image.shape[:2]
        self.net.setInput(cv2.dnn.blobFromImage(image, 0.007843, (300, 300), 127.5))
        started = time.monotonic()
        prediction = self.net.forward()
        elapsed = (time.monotonic() - started) * 1000
        if prediction.ndim != 4 or prediction.shape[-1] != 7:
            raise RuntimeError("Unexpected detector output shape")
        labels, detections = set(), []
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
            label = CLASSES[class_id]
            labels.add(label)
            detections.append({"label": label, "confidence": round(float(row[2]), 5),
                               "box_xyxy": [x1, y1, x2, y2]})
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 230, 0), 2)
            cv2.putText(image, f"{label} {row[2]:.0%}", (x1, max(20, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
        return {"image": image, "labels": sorted(labels), "detections": detections,
                "inference_ms": elapsed}
