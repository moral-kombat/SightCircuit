"""Synthetic-only native-pixel exclusion mask and HUD row selection.

No device, file, calibration loader or runtime approval is provided here.
Only ``validated`` synthetic-fixture calibrations are accepted; a calibration
derived from a hardware mapping candidate is rejected until a separate gate.
"""
from dataclasses import dataclass
import math

from .contract import HUD_CONFIG, Calibration, CaptureFormat, Footprint, HudConfig, viewports
from .geometry import contains, expand, overlaps, within_blind_budget
from .render import draw_tile

__all__ = ("HudMask",)


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
                    not within_blind_budget((expanded,), eye)):
                raise ValueError("hud_footprint_or_margin_out_of_bounds")

    @staticmethod
    def expand(footprint):
        return expand(footprint.rect, footprint.margin_px)

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
        rows = self.rows_for(detections, inference_shape, threshold)
        return draw_tile(rows, self.config), rows
