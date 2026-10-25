"""Aspect-preserving model input (letterbox) and its inverse box mapping."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Letterbox:
    """How a source image was scaled and padded into a square model input."""
    scale: float
    pad_x: int
    pad_y: int
    width: int   # Source image size, for clamping mapped boxes.
    height: int

    def to_source(self, x1, y1, x2, y2):
        """Map a model-input box to clamped integer source pixels (x2/y2 exclusive-ish)."""
        def fit(value, offset, limit):
            return int(min(max((value - offset) / self.scale, 0), limit - 1))
        return (fit(x1, self.pad_x, self.width), fit(y1, self.pad_y, self.height),
                fit(x2, self.pad_x, self.width), fit(y2, self.pad_y, self.height))


def letterbox(bgr, size, pad_value=114, center=False):
    """Scale ``bgr`` to fit ``size`` × ``size`` without distortion and pad the rest.

    Returns the padded uint8 image and the ``Letterbox`` transform. Placement is
    top-left by default, matching the YOLOX reference preprocessing.
    """
    import cv2
    import numpy as np

    height, width = bgr.shape[:2]
    scale = min(size / width, size / height)
    resized_w, resized_h = max(1, int(width * scale)), max(1, int(height * scale))
    resized = cv2.resize(bgr, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - resized_w) // 2 if center else 0
    pad_y = (size - resized_h) // 2 if center else 0
    canvas = np.full((size, size, 3), pad_value, dtype=np.uint8)
    canvas[pad_y:pad_y + resized_h, pad_x:pad_x + resized_w] = resized
    return canvas, Letterbox(scale, pad_x, pad_y, width, height)
