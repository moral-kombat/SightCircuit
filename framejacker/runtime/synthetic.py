"""The synthetic run kit: generated input, its fixture calibration and engine fixtures.

``SyntheticSource`` is the only source kind the live-image gate accepts. It
emits validated 96x64 owned frames; ``synthetic_hud_calibration`` describes
exactly those frames. ``FixtureDetector`` (no detections, no model) and
``HeadlessSink`` (no display) exercise the engine and its lifecycle offline.
Used by ``scripts/live_pipeline.py`` and the tests.
"""
import time

from ..frame import DetectionResult, Frame
from ..hud import Calibration, CaptureFormat, Footprint


class SyntheticSource:
    kind = "synthetic"
    source_id = "synthetic:generated"
    capture_layout = "mono"

    def __init__(self, rate=30):
        import numpy as np
        self.np = np  # Dependency startup occurs before the bounded acquisition interval.
        self.period = 1 / rate
        self.sequence = 0
        self.next_frame = 0
        self.closed = False

    def open(self):
        self.next_frame = time.monotonic()

    def read(self, stop, timeout=0.05):
        delay = self.next_frame - time.monotonic()
        if delay > 0:
            stop.wait(min(timeout, delay))
            return None
        self.sequence += 1
        self.next_frame = time.monotonic() + self.period
        pixels = self.np.full((64, 96, 3), self.sequence % 255, dtype=self.np.uint8)
        return Frame.owned_bgr(pixels, self.sequence, time.monotonic_ns(), self.source_id)

    def close(self):
        self.closed = True
        return True


class FixtureDetector:
    def detect(self, image, threshold):
        return DetectionResult((), 0.0)


class HeadlessSink:
    def open(self):
        self.count = self.clears = 0

    def submit(self, processed):
        self.count += 1

    def clear(self):
        self.clears += 1

    def close(self):
        return True


def synthetic_hud_calibration():
    """Only for the fixed generated 96x64 source; no hardware calibration loader."""
    return Calibration("synthetic:generated", CaptureFormat(96, 64, "BGR3", 288, 18432, 18432),
        "mono", (Footprint(0, (68, 42, 89, 56), 3),), validated=True, provenance="synthetic_fixture")
