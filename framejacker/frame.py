"""Owned frames and detection results passed between pipeline stages."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Frame:
    """One owned source image plus the provenance the architecture requires.

    ``pixels`` is an owned uint8 BGR array when ``pixel_format`` is ``"BGR24"``,
    or an immutable raw buffer laid out as a V4L2 FOURCC (``"RGB3"``/``"BGR3"``)
    with ``stride`` bytes per row. ``observed_ns`` is ``time.monotonic_ns()``
    when the source obtained the image: an observation time, never an exposure
    timestamp. ``media_time`` is the container presentation time for files.
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
    """A frame, its detections and the owned annotated image derived from it.

    Annotations always belong to ``frame``; consumers never pair them with a
    newer image. ``index`` counts frames processed by the pipeline run.
    """
    frame: Frame
    result: DetectionResult
    image: object
    index: int
    input_age_ms: float
    processing_ms: float

    @property
    def labels(self):
        return self.result.labels
