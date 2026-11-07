"""Synthetic-only native-pixel exclusion mask and HUD row selection.

No device, file, calibration loader or runtime approval is provided here.
Only ``validated`` synthetic-fixture calibrations are accepted; a calibration
derived from a hardware mapping candidate is rejected until a separate gate.
The mask fails closed once its calibration expires and checks the calibration's
session tokens and view fields against the capture context (``check_session``).
It zeroes every masked region, counts the footprint pixels still non-zero after
that step (``prepare_counted``), and then refills each region with one neutral
color taken only from its surround (``fill_regions``), so the detector sees
neither HUD pixels nor a black rectangle. It reports the boxes it drops for
touching the mask or crossing an eye (``select``).
"""
from dataclasses import dataclass
import math
import time

from ..frame import Detection
from ..gates import HARDWARE_MAPPING
from .contract import (HUD_CONFIG, SESSION_TOKENS, VIEW_FIELDS, Calibration, CaptureFormat, Footprint, HudChecks,
                       HudConfig, HudRow, margin_valid, viewports)
from .geometry import contains, exclusion, expand, within_blind_budget
from .render import draw_tile

__all__ = ("FILL_RING_PX", "HudMask", "fill_regions", "mask_regions")

FILL_RING_PX = 8  # Width of the surround the fill averages: the margin floor.


def _comparable(value):
    """A crop given as a JSON list or a tuple of ints compares as a tuple; any other value as itself."""
    if type(value) in (list, tuple) and all(type(item) is int for item in value):
        return tuple(value)
    return value


def mask_regions(calibration):
    """Native regions the mask removes for ``calibration``: the geometry rule alone, never an approval.

    Applies the eye partition, the margin rule and the per-eye blind budget.
    ``HudMask`` applies it after its approval checks, and offline code can
    compare a candidate's geometry with the runtime mask without making it usable.
    """
    cal = calibration
    if not isinstance(cal, Calibration):
        raise ValueError("hud_calibration_required")
    if not isinstance(cal.capture_format, CaptureFormat):
        raise ValueError("hud_capture_format_invalid")
    cal.capture_format.validate()
    eyes = viewports(cal.capture_format, cal.capture_layout)
    if type(cal.footprints) is not tuple or len(cal.footprints) != len(eyes):
        raise ValueError("hud_missing_eye_footprint")
    seen, regions = set(), []
    for footprint in cal.footprints:
        if (not isinstance(footprint, Footprint) or type(footprint.eye) is not int or
                footprint.eye not in range(len(eyes)) or footprint.eye in seen or
                type(footprint.rect) is not tuple or len(footprint.rect) != 4 or
                any(type(value) is not int for value in footprint.rect) or not margin_valid(footprint.margin_px)):
            raise ValueError("hud_mapping_invalid")
        seen.add(footprint.eye)
        expanded = expand(footprint.rect, footprint.margin_px)
        eye = eyes[footprint.eye]
        if (not contains(eye, footprint.rect) or not contains(eye, expanded) or
                not within_blind_budget((expanded,), eye)):
            raise ValueError("hud_footprint_or_margin_out_of_bounds")
        regions.append(expanded)
    return tuple(regions)


def surround(region, regions, eyes, pad):
    """``(crop, usable)``: ``region`` grown by ``pad`` and clipped to its own eye, and the crop pixels a fill may read.

    ``usable`` is True only outside every masked region, and the crop never
    leaves the eye that contains ``region``, so a fill built from it cannot see
    a footprint, a margin or the other eye. Raises when nothing is usable.
    """
    import numpy as np
    eye = next((eye for eye in eyes if contains(eye, region)), None)
    if eye is None:
        raise ValueError("hud_mask_region_outside_eyes")
    x0, y0, x1, y1 = region
    crop = (max(eye[0], x0 - pad), max(eye[1], y0 - pad), min(eye[2], x1 + pad), min(eye[3], y1 + pad))
    usable = np.ones((crop[3] - crop[1], crop[2] - crop[0]), bool)
    for rx0, ry0, rx1, ry1 in regions:
        ix0, iy0, ix1, iy1 = max(rx0, crop[0]), max(ry0, crop[1]), min(rx1, crop[2]), min(ry1, crop[3])
        if ix0 < ix1 and iy0 < iy1:
            usable[iy0 - crop[1]:iy1 - crop[1], ix0 - crop[0]:ix1 - crop[0]] = False
    if not usable.any():
        raise ValueError("hud_mask_fill_without_surround")
    return crop, usable


def fill_regions(native, regions, eyes):
    """Fill each region of a zeroed native image in place with one neutral color from its surround.

    The color is the rounded per-channel mean of the pixels within
    ``FILL_RING_PX`` of the region, inside the region's own eye and outside
    every masked region (``surround``). Nothing inside a footprint or its
    margin is read, so a region's content cannot reach the result. Integer
    arithmetic gives the same bytes on every CPU, and the cost is bounded by
    the ring and the region, which the blind budget bounds in turn.
    """
    import numpy as np
    for region in regions:
        (a0, b0, a1, b1), usable = surround(region, regions, eyes, FILL_RING_PX)
        pixels = native[b0:b1, a0:a1][usable]
        count = len(pixels)
        x0, y0, x1, y1 = region
        native[y0:y1, x0:x1] = ((2 * pixels.sum(axis=0, dtype=np.int64) + count) // (2 * count)).astype(np.uint8)


@dataclass(frozen=True)
class HudMask:
    calibration: Calibration
    config: HudConfig = HUD_CONFIG
    source_kind: str = "unapproved"

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Approval, record, geometry and lifetime; an expired calibration is unusable."""
        cal = self.calibration
        if not isinstance(cal, Calibration):
            raise ValueError("hud_calibration_required")
        if cal.validated is not True:
            raise ValueError("hud_calibration_unvalidated")
        HARDWARE_MAPPING.require(
            self.source_kind == "synthetic" and cal.provenance == "synthetic_fixture" and
            isinstance(cal.source_id, str) and cal.source_id.startswith("synthetic:") and len(cal.source_id) <= 96,
            "hud_hardware_mapping_not_approved")
        if self.config != HUD_CONFIG or cal.config != self.config:
            raise ValueError("hud_config_changed")
        cal.validate()
        mask_regions(cal)
        if not cal.observed_ns <= time.monotonic_ns() < cal.expires_ns:
            raise ValueError("hud_calibration_expired_or_clock_changed")

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

    def check_session(self, declared, reported=None):
        """The calibration's session tokens (``SESSION_TOKENS``) and view fields (``VIEW_FIELDS``).

        ``declared`` maps source field names to the source's declared values
        (None when undeclared); ``reported`` is one frame's session metadata.
        Each value present must equal the calibration's, with its type; a crop
        may be a list or a tuple of ints. With a frame, each session token must
        also be present in one of them. An undeclared view field is the
        calibration's full, upright frame and HUD, which is what the mask applies.
        """
        self.validate()
        for name in SESSION_TOKENS + VIEW_FIELDS:
            token = getattr(self.calibration, name)
            values = [declared[name]] if declared.get(name) is not None else []
            if reported is not None and name in reported:
                values.append(reported[name])
            if any(type(value) is not type(token) or value != token for value in map(_comparable, values)):
                raise ValueError("hud_" + name.removeprefix("hud_") + "_changed")
            if name in SESSION_TOKENS and reported is not None and not values:
                raise ValueError(f"hud_{name}_undeclared")

    def inference_shape(self):
        fmt = self.calibration.capture_format
        ratio = min(1, self.config.inference_max_side / max(fmt.width, fmt.height))
        return (max(1, round(fmt.height * ratio)), max(1, round(fmt.width * ratio)))

    def prepare(self, bgr, **context):
        """Read-only masked detector input; any footprint pixel left non-zero raises ``hud_mask_zeroing_failed``."""
        image, unmasked = self.prepare_counted(bgr, **context)
        if unmasked:
            raise ValueError("hud_mask_zeroing_failed")
        return image

    def prepare_counted(self, bgr, *, source_id, capture_format, capture_layout, config=HUD_CONFIG):
        """Masked detector input and its count of footprint pixels still non-zero after zeroing.

        The count should be 0. It is taken after the zeroing step and before
        the neutral fill and the resize, from the geometry rule
        (``mask_regions``), not from the regions the zeroing used, and the
        caller decides what a non-zero count stops. The fill then reads only
        this zeroed copy, outside every region in each region's own eye, so a
        count of 0 means no HUD pixel can reach the detector.
        """
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
        unmasked = self.unmasked_pixels(image)
        # A neutral fill, so the detector sees no black rectangle (E76): only this zeroed copy is read.
        fill_regions(image, self.regions, viewports(self.calibration.capture_format, self.calibration.capture_layout))
        height, width = self.inference_shape()
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        image.flags.writeable = False
        return image, unmasked

    def unmasked_pixels(self, native):
        """Footprint pixels with any non-zero channel in a zeroed native image, before the fill (one region per eye)."""
        import numpy as np
        return sum(int(np.count_nonzero(native[y0:y1, x0:x1].any(axis=2)))
                   for x0, y0, x1, y1 in mask_regions(self.calibration))

    def rows_for(self, detections, inference_shape, threshold):
        """Select at most ``config.rows`` ``HudRow``s from ``Detection``s in inference pixels."""
        return self.select(detections, inference_shape, threshold)[0]

    def select(self, detections, inference_shape, threshold):
        """``(rows, HudChecks)``: the HUD rows and every valid box in native pixels, with the ones dropped.

        A box that touches a masked region or does not lie inside exactly one
        eye (``geometry.exclusion``) is dropped for presentation and counted.
        """
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
        rows, boxes, dropped = [], [], {"mask_overlap": 0, "eye_crossing": 0}
        for detection in detections:
            if not isinstance(detection, Detection):
                continue
            box, score, label = detection.box_xyxy, detection.confidence, detection.label
            if (not isinstance(box, (list, tuple)) or len(box) != 4 or
                    any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box) or
                    not isinstance(score, (int, float)) or not math.isfinite(score) or
                    not threshold <= score <= 1 or not isinstance(label, str) or not label or
                    not contains((0, 0, width, height), box)):
                continue
            # Outward rounding accounts conservatively for area-filter support.
            native = (math.floor(box[0] * fmt.width / width), math.floor(box[1] * fmt.height / height),
                      math.ceil(box[2] * fmt.width / width), math.ceil(box[3] * fmt.height / height))
            boxes.append(native)
            reason = exclusion(native, self.regions, eyes)
            if reason is not None:  # Never a direction from the HUD's own pixels or from two eyes.
                dropped[reason] += 1
                continue
            eye = next(eye for eye in eyes if contains(eye, native))
            cx = ((native[0] + native[2]) / 2 - eye[0]) / (eye[2] - eye[0])
            cy = ((native[1] + native[3]) / 2 - eye[1]) / (eye[3] - eye[1])
            dx = -1 if cx < 0.4 else 1 if cx > 0.6 else 0
            dy = -1 if cy < 0.4 else 1 if cy > 0.6 else 0
            name = "".join(c for c in label if c.isascii() and (c.isalnum() or c in " _-"))[:20].strip()
            if name:
                rows.append(HudRow(name, float(score), (dx, dy)))
        rows.sort(key=lambda row: (-row.confidence, row.label, row.direction))
        selected, seen = [], set()
        for row in rows:
            key = (row.label, row.direction)
            if key not in seen:
                seen.add(key)
                selected.append(row)
            if len(selected) == self.config.rows:
                break
        return tuple(selected), HudChecks(tuple(boxes), dropped["mask_overlap"], dropped["eye_crossing"])

    def render(self, detections, inference_shape, threshold):
        rows = self.rows_for(detections, inference_shape, threshold)
        return draw_tile(rows, self.config), rows
