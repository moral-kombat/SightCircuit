"""Convert source frames to the detector's owned BGR input."""
from .errors import FrameFormatError

MAX_PROCESSING_SIDE = 960


def processing_size(width, height, max_side=MAX_PROCESSING_SIDE):
    """Dimensions scaled so the longest side is at most ``max_side``."""
    ratio = min(1.0, max_side / max(width, height))
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def to_bgr(frame, max_side=MAX_PROCESSING_SIDE):
    """Return a writable uint8 BGR image no larger than ``max_side``.

    An unscaled BGR24 frame is returned without copying; treat the result as
    read-only input. Annotation draws on its own copy.
    """
    import cv2
    import numpy as np

    if frame.pixel_format == "BGR24":
        image = frame.pixels
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise FrameFormatError("invalid_frame", "BGR24 frame must be a uint8 array with three channels")
    elif frame.pixel_format in ("RGB3", "BGR3"):
        if len(frame.pixels) < frame.stride * frame.height or frame.stride < frame.width * 3:
            raise FrameFormatError("short_frame", "Incomplete mirror image")
        image = np.ndarray((frame.height, frame.width, 3), dtype=np.uint8,
                           buffer=frame.pixels, strides=(frame.stride, 3, 1))
    else:
        raise FrameFormatError("unsupported_recognition_format",
                               "Recognition currently accepts RGB3/BGR3 only; no format change requested")
    width, height = processing_size(image.shape[1], image.shape[0], max_side)
    if (width, height) != (image.shape[1], image.shape[0]):
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    if frame.pixel_format == "RGB3":
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    return image if image.flags.writeable else image.copy()  # Raw buffers give read-only views.
