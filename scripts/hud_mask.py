"""Synthetic-only fixed HUD and native-pixel exclusion contract.

No device, file, calibration loader or runtime approval is provided here.
Head-relative panel coordinates do NOT establish a fixed mirror-pixel mapping.
"""
from dataclasses import dataclass
import math


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
                not 1 <= self.width <= 4096 or not 1 <= self.height <= 4096 or
                self.fourcc not in ("RGB3", "BGR3") or self.stride_bytes < self.width * 3 or
                self.minimum_buffer_bytes != self.stride_bytes * self.height or
                not self.minimum_buffer_bytes <= self.buffer_bytes <= 48 * 1024 * 1024):
            raise ValueError("hud_capture_format_invalid")
        if (type(self.metadata) is not tuple or len(self.metadata) > 16 or
                any(type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], str) or
                    len(pair[0]) > 64 or type(pair[1]) not in (int, str) or len(str(pair[1])) > 96
                    for pair in self.metadata)):
            raise ValueError("hud_capture_metadata_invalid")


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


def viewports(fmt, layout):
    w, h = fmt.width, fmt.height
    if layout == "mono":
        return ((0, 0, w, h),)
    if layout == "stereo_sbs" and w % 2 == 0:
        return ((0, 0, w // 2, h), (w // 2, 0, w, h))
    if layout == "stereo_tb" and h % 2 == 0:
        return ((0, 0, w, h // 2), (0, h // 2, w, h))
    raise ValueError("hud_capture_layout_invalid")


def contains(outer, inner):
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@dataclass(frozen=True)
class HudMask:
    calibration: Calibration
    config: HudConfig = HUD_CONFIG
    source_kind: str = "unapproved"

    def __post_init__(self):
        self.validate()

    def validate(self):
        cal = self.calibration
        if not isinstance(cal, Calibration):
            raise ValueError("hud_calibration_required")
        if cal.validated is not True:
            raise ValueError("hud_calibration_unvalidated")
        if (self.source_kind != "synthetic" or cal.provenance != "synthetic_fixture" or
                not isinstance(cal.source_id, str) or not cal.source_id.startswith("synthetic:") or
                len(cal.source_id) > 96):
            raise ValueError("hud_hardware_mapping_not_approved")
        if self.config != HUD_CONFIG or cal.config != self.config:
            raise ValueError("hud_config_changed")
        if not isinstance(cal.capture_format, CaptureFormat):
            raise ValueError("hud_capture_format_invalid")
        cal.capture_format.validate()
        eyes = viewports(cal.capture_format, cal.capture_layout)
        if type(cal.footprints) is not tuple or len(cal.footprints) != len(eyes):
            raise ValueError("hud_missing_eye_footprint")
        seen = set()
        for footprint in cal.footprints:
            if (not isinstance(footprint, Footprint) or type(footprint.eye) is not int or
                    footprint.eye not in range(len(eyes)) or footprint.eye in seen or
                    type(footprint.rect) is not tuple or len(footprint.rect) != 4 or
                    any(type(value) is not int for value in footprint.rect) or
                    type(footprint.margin_px) is not int or not 2 <= footprint.margin_px <= 128):
                raise ValueError("hud_mapping_invalid")
            seen.add(footprint.eye)
            expanded = self.expand(footprint)
            eye = eyes[footprint.eye]
            if (not contains(eye, footprint.rect) or not contains(eye, expanded) or
                    (expanded[2] - expanded[0]) * (expanded[3] - expanded[1]) >
                    (eye[2] - eye[0]) * (eye[3] - eye[1]) / 4):
                raise ValueError("hud_footprint_or_margin_out_of_bounds")

    @staticmethod
    def expand(footprint):
        x0, y0, x1, y1 = footprint.rect
        margin = footprint.margin_px
        return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)

    @property
    def regions(self):
        return tuple(self.expand(footprint) for footprint in self.calibration.footprints)

    def check_context(self, source_id, capture_format, capture_layout, config):
        self.validate()
        if source_id != self.calibration.source_id:
            raise ValueError("hud_source_changed")
        if capture_format != self.calibration.capture_format:
            raise ValueError("hud_capture_format_changed")
        if capture_layout != self.calibration.capture_layout:
            raise ValueError("hud_capture_layout_changed")
        if config != self.config:
            raise ValueError("hud_config_changed")

    def inference_shape(self):
        fmt = self.calibration.capture_format
        ratio = min(1, self.config.inference_max_side / max(fmt.width, fmt.height))
        return (max(1, round(fmt.height * ratio)), max(1, round(fmt.width * ratio)))

    def prepare(self, bgr, *, source_id, capture_format, capture_layout, config=HUD_CONFIG):
        import cv2
        import numpy as np
        self.check_context(source_id, capture_format, capture_layout, config)
        if (bgr.dtype != np.uint8 or bgr.shape != (capture_format.height, capture_format.width, 3)):
            raise ValueError("hud_native_image_mismatch")
        image = np.array(bgr, copy=True, order="C")
        # Mask the WHOLE footprint, including transparent pixels, plus margin.
        # No resize/filter is permitted before this union is removed.
        for x0, y0, x1, y1 in self.regions:
            image[y0:y1, x0:x1] = 0
        height, width = self.inference_shape()
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        image.flags.writeable = False
        return image

    def rows_for(self, detections, inference_shape, threshold):
        self.validate()
        if tuple(inference_shape) != self.inference_shape():
            raise ValueError("hud_inference_mapping_changed")
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("hud_threshold_invalid")
        if not isinstance(detections, (list, tuple)) or len(detections) > 128:
            raise ValueError("hud_detection_budget_exceeded")
        height, width = inference_shape
        fmt = self.calibration.capture_format
        eyes = viewports(fmt, self.calibration.capture_layout)
        rows = []
        for detection in detections:
            box = detection.get("box_xyxy", ())
            score = detection.get("confidence", float("nan"))
            label = detection.get("label", "")
            if (not isinstance(box, (list, tuple)) or len(box) != 4 or
                    any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box) or
                    not isinstance(score, (int, float)) or not math.isfinite(score) or
                    not threshold <= score <= 1 or not isinstance(label, str) or not label or
                    not contains((0, 0, width, height), box)):
                continue
            # Outward rounding accounts conservatively for area-filter support.
            native = (math.floor(box[0] * fmt.width / width), math.floor(box[1] * fmt.height / height),
                      math.ceil(box[2] * fmt.width / width), math.ceil(box[3] * fmt.height / height))
            if any(overlaps(native, region) for region in self.regions):
                continue
            matching = [index for index, eye in enumerate(eyes) if contains(eye, native)]
            if len(matching) != 1:  # Do not turn a cross-eye box into a direction.
                continue
            eye = eyes[matching[0]]
            cx = ((native[0] + native[2]) / 2 - eye[0]) / (eye[2] - eye[0])
            cy = ((native[1] + native[3]) / 2 - eye[1]) / (eye[3] - eye[1])
            dx = -1 if cx < 0.4 else 1 if cx > 0.6 else 0
            dy = -1 if cy < 0.4 else 1 if cy > 0.6 else 0
            name = "".join(c for c in label if c.isascii() and (c.isalnum() or c in " _-"))[:20].strip()
            if name:
                rows.append({"label": name, "confidence": float(score), "direction": (dx, dy)})
        rows.sort(key=lambda row: (-row["confidence"], row["label"], row["direction"]))
        selected, seen = [], set()
        for row in rows:
            key = (row["label"], row["direction"])
            if key not in seen:
                seen.add(key)
                selected.append(row)
            if len(selected) == self.config.rows:
                break
        return selected

    def render(self, detections, inference_shape, threshold):
        import cv2
        import numpy as np
        rows = self.rows_for(detections, inference_shape, threshold)
        # Deliberately no scene-image parameter: only names and local arrows.
        image = np.zeros((self.config.height_px, self.config.width_px, 3), dtype=np.uint8)
        cv2.putText(image, "View direction (approx.)", (10, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (180, 180, 180), 1, cv2.LINE_AA)
        for index, row in enumerate(rows):
            y = 43 + 29 * index
            dx, dy = row["direction"]
            if dx or dy:
                cv2.arrowedLine(image, (23 - 8 * dx, y - 8 * dy), (23 + 8 * dx, y + 8 * dy),
                                (70, 230, 130), 2, cv2.LINE_AA, tipLength=0.4)
            else:
                cv2.circle(image, (23, y), 4, (70, 230, 130), 2, cv2.LINE_AA)
            cv2.putText(image, row["label"], (47, y + 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (235, 235, 235), 1, cv2.LINE_AA)
        image.flags.writeable = False
        return image, rows
