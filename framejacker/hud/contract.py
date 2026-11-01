"""Versioned HUD layout, native capture format and calibration records.

These types describe a mapping; they never approve one. ``HudMask`` decides
whether a calibration may be used, and today it accepts synthetic fixtures only.
Head-relative panel coordinates do NOT establish a fixed mirror-pixel mapping.
"""
from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json

from ..frame import MAX_NATIVE_BYTES, MAX_NATIVE_SIDE
from .geometry import eye_viewports


@dataclass(frozen=True)
class HudConfig:
    layout_id: str = "fixed-edge-hud-v1"
    width_px: int = 320
    height_px: int = 128
    rows: int = 3
    panel_width_m: float = 0.32
    distance_m: float = 1.25
    x_m: float = 0.55
    y_m: float = -0.35
    opacity: float = 1.0
    inference_max_side: int = 960


HUD_CONFIG = HudConfig()


def config_digest(config):
    """Pin all HUD fields, not merely the human-readable layout name."""
    value = asdict(config) if is_dataclass(config) else config
    if type(value) is not dict:
        raise ValueError("hud_config_fields_invalid")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CaptureFormat:
    width: int
    height: int
    fourcc: str
    stride_bytes: int
    buffer_bytes: int
    minimum_buffer_bytes: int
    metadata: tuple = ()

    @classmethod
    def from_mapping(cls, fmt):
        keys = tuple(key for key in cls.__dataclass_fields__ if key != "metadata")
        metadata = tuple(sorted((key, value) for key, value in fmt.items() if key not in keys))
        return cls(*(fmt[key] for key in keys), metadata=metadata)

    @classmethod
    def bgr(cls, image):
        return cls(image.shape[1], image.shape[0], "BGR3", image.strides[0],
                   image.nbytes, image.strides[0] * image.shape[0])

    def validate(self):
        values = (self.width, self.height, self.stride_bytes, self.buffer_bytes, self.minimum_buffer_bytes)
        if (any(type(value) is not int for value in values) or
                not 1 <= self.width <= MAX_NATIVE_SIDE or not 1 <= self.height <= MAX_NATIVE_SIDE or
                self.fourcc not in ("RGB3", "BGR3") or self.stride_bytes < self.width * 3 or
                self.minimum_buffer_bytes != self.stride_bytes * self.height or
                not self.minimum_buffer_bytes <= self.buffer_bytes <= MAX_NATIVE_BYTES):
            raise ValueError("hud_capture_format_invalid")
        if (type(self.metadata) is not tuple or len(self.metadata) > 16 or
                any(type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], str) or
                    len(pair[0]) > 64 or type(pair[1]) not in (int, str) or len(str(pair[1])) > 96
                    for pair in self.metadata)):
            raise ValueError("hud_capture_metadata_invalid")

    @property
    def bounds(self):
        return (0, 0, self.width, self.height)


@dataclass(frozen=True)
class Footprint:
    eye: int
    rect: tuple  # Native capture pixels, x0/y0 inclusive and x1/y1 exclusive.
    margin_px: int


@dataclass(frozen=True)
class Calibration:
    source_id: str
    capture_format: CaptureFormat
    capture_layout: str
    footprints: tuple
    config: HudConfig = HUD_CONFIG
    validated: bool = False
    provenance: str = "unvalidated"


@dataclass(frozen=True)
class HudRow:
    """One HUD line: a sanitized name and an approximate view direction, never a position."""
    label: str
    confidence: float
    direction: tuple  # (dx, dy), each -1, 0 or 1 relative to the eye's view centre.

    def as_dict(self):
        return {"label": self.label, "confidence": self.confidence, "direction": list(self.direction)}


@dataclass(frozen=True)
class HudOutput:
    """What one result contributes to the fixed HUD, and the layout it was drawn for."""
    rows: tuple
    config: HudConfig

    @property
    def labels(self):
        return [row.label for row in self.rows]


def viewports(fmt, layout):
    """Eye rectangles of a full, uncropped capture; the runtime mask's frame."""
    return eye_viewports(fmt.bounds, layout)
