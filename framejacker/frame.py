"""Frames and detection results passed between pipeline stages."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import math


MAX_NATIVE_SIDE = 4096
MAX_NATIVE_BYTES = 48 * 1024 * 1024

# What a presented result's coordinates mean.
DETECTOR_PIXELS = "detector_input_pixels"
VIEW_DIRECTION = "approximate_image_view_direction"


@dataclass(frozen=True)
class Frame:
    """One source image plus the provenance the architecture requires.

    ``pixels`` is a uint8 BGR array when ``pixel_format`` is ``"BGR24"``, or an
    immutable raw buffer laid out as a V4L2 FOURCC (``"RGB3"``/``"BGR3"``) with
    ``stride`` bytes per row. ``observed_ns`` is ``time.monotonic_ns()`` when the
    source obtained the image: an observation time, never an exposure timestamp.
    ``media_time`` is the container presentation time for files. When declared,
    ``metadata["native_format"]`` describes the acquisition layout, which may
    differ from an owned copy's contiguous ``stride``.
    """
    pixels: object
    pixel_format: str
    width: int
    height: int
    stride: int
    source: str
    sequence: int
    observed_ns: int
    media_time: float | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def owned_bgr(cls, image, sequence, observed_ns, source, media_time=None, native_format=None):
        """A validated, private, read-only copy of native BGR pixels.

        The acquisition layout survives the copy as ``metadata["native_format"]``
        (derived from ``image`` when not given). Native bounds are separate from
        the detector's processing bound: exclusion masks must see every original
        pixel before any spatial resize.
        """
        import numpy as np
        if (image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or
                min(image.shape[:2]) < 1 or max(image.shape[:2]) > MAX_NATIVE_SIDE or
                image.nbytes > MAX_NATIVE_BYTES or image.strides[0] * image.shape[0] > MAX_NATIVE_BYTES or
                image.strides[0] < image.shape[1] * 3 or image.strides[1:] != (3, 1)):
            raise ValueError("source_requires_bounded_uint8_bgr")
        if type(sequence) is not int or sequence < 1 or type(observed_ns) is not int or observed_ns < 0:
            raise ValueError("invalid_frame_identity_or_observation_time")
        if not isinstance(source, str) or not 1 <= len(source) <= 96:
            raise ValueError("invalid_source_identity")
        if media_time is not None and (type(media_time) not in (int, float) or not math.isfinite(media_time)):
            raise ValueError("invalid_media_time")
        if native_format is None:
            size = image.strides[0] * image.shape[0]
            native_format = dict(width=image.shape[1], height=image.shape[0], fourcc="BGR3",
                                 stride_bytes=image.strides[0], buffer_bytes=size, minimum_buffer_bytes=size)
        descriptor = dict(native_format)
        if (len(descriptor) > 22 or
                any(not isinstance(key, str) or len(key) > 64 or type(value) not in (int, str) or
                    len(str(value)) > 96 for key, value in descriptor.items()) or
                descriptor.get("width") != image.shape[1] or descriptor.get("height") != image.shape[0] or
                descriptor.get("fourcc") not in ("RGB3", "BGR3") or
                type(descriptor.get("stride_bytes")) is not int or descriptor["stride_bytes"] < image.shape[1] * 3 or
                descriptor.get("minimum_buffer_bytes") != descriptor["stride_bytes"] * image.shape[0] or
                type(descriptor.get("buffer_bytes")) is not int or
                not descriptor["minimum_buffer_bytes"] <= descriptor["buffer_bytes"] <= MAX_NATIVE_BYTES):
            raise ValueError("invalid_native_frame_layout")
        owned = np.array(image, dtype=np.uint8, order="C", copy=True)
        owned.flags.writeable = False
        return cls(owned, "BGR24", image.shape[1], image.shape[0], owned.strides[0], source, sequence,
                   observed_ns, media_time, {"native_format": dict(sorted(descriptor.items()))})

    def owned(self):
        """This frame with pixels and metadata no other code holds.

        Array pixels become a private read-only copy, so a source that reuses its
        buffer cannot change a queued frame; a BGR24 image's ``stride`` becomes
        the copy's row stride, while a raw buffer keeps its declared ``stride``.
        Any other buffer (``bytearray``, ``memoryview``, ``array``, ``mmap``)
        becomes ``bytes``; byte strings are immutable already. Identity is not
        re-validated here (replay labels may exceed 96 characters).
        """
        pixels, stride = self.pixels, self.stride
        if hasattr(pixels, "flags") and hasattr(pixels, "strides"):  # NumPy array
            import numpy as np
            pixels = np.array(pixels, order="C", copy=True)
            pixels.flags.writeable = False
            if self.pixel_format == "BGR24":
                stride = pixels.strides[0]
        elif not isinstance(pixels, bytes):
            try:
                pixels = memoryview(pixels).tobytes()
            except TypeError:  # Not a buffer: nothing a source could rewrite in place.
                pass
        return replace(self, pixels=pixels, stride=stride, metadata=deepcopy(self.metadata))


def native_format(frame):
    """The acquisition format a frame declares, as a V4L2-style mapping."""
    fmt = (frame.metadata or {}).get("native_format") if isinstance(frame, Frame) else None
    if not isinstance(fmt, dict):
        raise ValueError("native_format_undeclared")
    return dict(fmt)


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box_xyxy: tuple[int, int, int, int]

    def as_dict(self):
        return {"label": self.label, "confidence": self.confidence, "box_xyxy": list(self.box_xyxy)}


@dataclass(frozen=True)
class DetectionResult:
    """Detections in the pixel space of the image given to the detector."""
    detections: tuple[Detection, ...]
    inference_ms: float  # The model's forward call only; see Processed.processing_ms.

    @property
    def labels(self):
        return sorted({detection.label for detection in self.detections})


@dataclass(frozen=True)
class Processed:
    """The one output record every engine delivers to its sinks.

    ``result`` is the detector's output in its input pixel space. ``image`` is
    what is presented: boxes annotated on an owned copy of the detector input,
    or, when ``hud`` is set, the fixed HUD tile drawn from ``hud.rows`` alone.
    Annotations always belong to ``frame``;
    consumers never pair them with a newer image. ``index`` counts frames
    processed by the run.
    """
    frame: Frame
    result: DetectionResult
    image: object
    index: int
    input_age_ms: float
    processing_ms: float
    expires_ns: int | None = None  # Original observation + maximum age, never inference completion.
    output_age_ms: float | None = None
    detector_input_size: tuple[int, int] | None = None
    native_to_inference_scale: tuple[float, float] | None = None
    hud: object = None  # framejacker.hud.HudOutput when ``image`` is the HUD tile.

    @property
    def labels(self):
        return self.hud.labels if self.hud is not None else self.result.labels

    @property
    def output_space(self):
        """Coordinate meaning of what is presented: boxes or view directions."""
        return VIEW_DIRECTION if self.hud is not None else DETECTOR_PIXELS
