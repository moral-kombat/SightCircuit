"""Compatibility dictionary adapter for the shared checksum-verified CPU detector.

Model preprocessing, inference and annotations live in framejacker.detection;
legacy mirror/live commands retain their existing result shape.
"""
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.assets import ROOT, ASSETS  # noqa: F401 - compatibility exports
from framejacker.detection import CLASSES, MobileNetSSD, annotate, verify_model  # noqa: F401


class DemoDetector(MobileNetSSD):
    def detect(self, bgr, threshold):
        result = super().detect(bgr, threshold)
        return {"image": annotate(bgr, result), "labels": result.labels,
                "detections": [item.as_dict() for item in result.detections],
                "inference_ms": result.inference_ms}
