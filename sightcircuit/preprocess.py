"""Aspect-preserving model input (letterbox) and its inverse box mapping."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Letterbox:
    """How a source image was scaled and padded into a square model input."""
    scale_x: float  # Resized size / source size per axis; integer truncation can make them differ.
    scale_y: float
    pad_x: int
    pad_y: int
    width: int   # Source image size, for clamping mapped boxes.
    height: int

    def to_source(self, x1, y1, x2, y2):
        """Map a model-input box to clamped integer source pixels (x2/y2 exclusive-ish)."""
        def fit(value, offset, scale, limit):
            return int(min(max((value - offset) / scale, 0), limit - 1))
        return (fit(x1, self.pad_x, self.scale_x, self.width), fit(y1, self.pad_y, self.scale_y, self.height),
                fit(x2, self.pad_x, self.scale_x, self.width), fit(y2, self.pad_y, self.scale_y, self.height))


def letterbox_geometry(width, height, size, center=False):
    """Resized dimensions and transform for fitting a source into ``size``; no pixels touched.

    The resized size is truncated exactly as the YOLOX reference does, so model
    input is unchanged. The inverse uses the realized per-axis ratio, not the
    nominal scale, which would otherwise misplace boxes by up to 1/scale pixels.
    """
    scale = min(size / width, size / height)
    resized_w, resized_h = max(1, int(width * scale)), max(1, int(height * scale))
    pad_x = (size - resized_w) // 2 if center else 0
    pad_y = (size - resized_h) // 2 if center else 0
    return resized_w, resized_h, Letterbox(resized_w / width, resized_h / height, pad_x, pad_y, width, height)


def letterbox(bgr, size, pad_value=114, center=False):
    """Scale ``bgr`` to fit ``size`` × ``size`` without distortion and pad the rest.

    Returns the padded uint8 image and the ``Letterbox`` transform. Placement is
    top-left by default, matching the YOLOX reference preprocessing.
    """
    import cv2
    import numpy as np

    height, width = bgr.shape[:2]
    resized_w, resized_h, transform = letterbox_geometry(width, height, size, center)
    resized = cv2.resize(bgr, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), pad_value, dtype=np.uint8)
    canvas[transform.pad_y:transform.pad_y + resized_h, transform.pad_x:transform.pad_x + resized_w] = resized
    return canvas, transform
