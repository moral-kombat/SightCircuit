"""Convert source frames to the detector's owned BGR input."""
from .errors import FrameFormatError

MAX_PROCESSING_SIDE = 960


def processing_size(width, height, max_side=MAX_PROCESSING_SIDE):
    """Dimensions scaled so the longest side is at most ``max_side``."""
    ratio = min(1.0, max_side / max(width, height))
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def native_bgr(frame):
    """Validate ``frame`` and return its full native-resolution BGR pixels.

    The result may be a read-only view of the source buffer. Exclusion masks
    (``framejacker.hud``) take this before any spatial resize.
    """
    return buffer_bgr(frame.pixels, frame.pixel_format, frame.width, frame.height, frame.stride)


def buffer_bgr(pixels, pixel_format, width, height, stride):
    """The one native pixel rule: a BGR24 array, or a raw RGB3/BGR3 buffer with ``stride``.

    Shared by frames (``native_bgr``) and raw mirror reads (HUD observation), so
    masking and observation measure the same native pixel space.
    """
    import cv2
    import numpy as np

    if pixel_format == "BGR24":
        image = pixels
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise FrameFormatError("invalid_frame", "BGR24 frame must be a uint8 array with three channels")
    elif pixel_format in ("RGB3", "BGR3"):
        if len(pixels) < stride * height or stride < width * 3:
            raise FrameFormatError("short_frame", "Incomplete mirror image")
        image = np.ndarray((height, width, 3), dtype=np.uint8, buffer=pixels, strides=(stride, 3, 1))
    else:
        raise FrameFormatError("unsupported_recognition_format",
                               "Recognition currently accepts RGB3/BGR3 only; no format change requested")
    if image.shape[:2] != (height, width):
        raise FrameFormatError("invalid_frame", "Image dimensions do not match native frame metadata")
    # Channel conversion is pointwise; the acquisition bound is independent of
    # the detector's processing bound.
    if pixel_format == "RGB3":
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    return image


def to_bgr(frame, max_side=MAX_PROCESSING_SIDE):
    """Return a writable uint8 BGR image no larger than ``max_side``.

    An unscaled BGR24 frame is returned without copying; treat the result as
    read-only input. Annotation draws on its own copy.
    """
    import cv2

    image = native_bgr(frame)
    width, height = processing_size(image.shape[1], image.shape[0], max_side)
    if (width, height) != (image.shape[1], image.shape[0]):
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return image if image.flags.writeable else image.copy()  # Raw buffers give read-only views.
