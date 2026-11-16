"""Mask-free HUD row selection for camera detections.

SDK camera frames image the room before compositing, so there is no HUD
footprint to exclude and no eye partition: ``camera_rows`` turns one frame's
detections into ``HudRow`` values from the box centres alone. It reads no
mapping record, approves nothing and opens no gate; a runner that shows its
rows stays behind the gates in ``sightcircuit/gates.py``.

A direction is the 3x3 cell of the box centre over the full frame, the same
0.4 and 0.6 thresholds the masked selector applies per eye. Whether the camera
image's axes match the wearer's view is not known in this checkout (a device
step measures it), so the mapping from image axes to view axes is the explicit
``orientation`` argument; identity is only the default, not a finding.
"""
import math

from ..frame import Detection
from .contract import NO_DIRECTION, HudConfig, HudRow, row_label, select_rows

__all__ = ("CELL_LOW", "CELL_HIGH", "MAX_DETECTIONS", "ORIENTATIONS", "camera_rows")

CELL_LOW, CELL_HIGH = 0.4, 0.6  # A centre below LOW or above HIGH leaves the middle cell, on each axis.
MAX_DETECTIONS = 128  # One frame's detection budget, as the masked selector's.

# How an image direction (dx right, dy down) becomes a view direction, by the
# operation that would make the image upright for the wearer. The eight cover
# every axis flip and quarter turn; "identity" assumes the image is upright.
ORIENTATIONS = {
    "identity": lambda dx, dy: (dx, dy),
    "flip_horizontal": lambda dx, dy: (-dx, dy),
    "flip_vertical": lambda dx, dy: (dx, -dy),
    "rotate_180": lambda dx, dy: (-dx, -dy),
    "rotate_90_cw": lambda dx, dy: (-dy, dx),
    "rotate_90_ccw": lambda dx, dy: (dy, -dx),
    "transpose": lambda dx, dy: (dy, dx),
    "transverse": lambda dx, dy: (-dy, -dx),
}


def _cell(fraction):
    return -1 if fraction < CELL_LOW else 1 if fraction > CELL_HIGH else 0


def _number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def camera_rows(detections, frame_shape, config, *, threshold, directions=True, orientation="identity"):
    """At most ``config.rows`` ``HudRow`` values for one camera frame's detections.

    ``detections`` are ``Detection`` values in the pixels of the frame whose
    shape is ``frame_shape`` (height, width, then any channels). A detection
    gets a row when its score is from ``threshold`` to 1, its box is finite,
    nonempty and inside the frame, and its label keeps a printable character
    (``contract.row_label``); anything else is skipped, as the masked selector
    skips it. The caller passes the threshold (``detection.POC_THRESHOLD`` for
    the proof of concept); none is assumed here.

    With ``directions`` each row carries the cell of its box centre over the
    full frame, turned by ``orientation`` (a key of ``ORIENTATIONS``) into a
    view direction: one of the eight directions or the centre ``(0, 0)``.
    Without it every row is names only (``contract.NO_DIRECTION``) and
    ``orientation`` changes nothing. The shared hygiene (``select_rows``)
    orders, deduplicates by ``(label, direction)`` and caps the rows.
    """
    if (not isinstance(frame_shape, (list, tuple)) or len(frame_shape) < 2 or
            any(type(side) is not int or side < 1 for side in frame_shape[:2])):
        raise ValueError("hud_camera_frame_shape_invalid")
    if not isinstance(config, HudConfig) or type(config.rows) is not int or config.rows < 1:
        raise ValueError("hud_config_fields_invalid")
    if not _number(threshold) or not 0 <= threshold <= 1:
        raise ValueError("hud_threshold_invalid")
    if type(directions) is not bool:
        raise ValueError("hud_camera_directions_invalid")
    if type(orientation) is not str or orientation not in ORIENTATIONS:
        raise ValueError("hud_camera_orientation_invalid")
    if not isinstance(detections, (list, tuple)) or len(detections) > MAX_DETECTIONS:
        raise ValueError("hud_detection_budget_exceeded")
    height, width = frame_shape[:2]
    view = ORIENTATIONS[orientation]
    rows = []
    for detection in detections:
        if not isinstance(detection, Detection):
            continue
        box, score, label = detection.box_xyxy, detection.confidence, detection.label
        if (not isinstance(box, (list, tuple)) or len(box) != 4 or not all(map(_number, box)) or
                not _number(score) or not threshold <= score <= 1 or not isinstance(label, str) or
                not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
            continue
        name = row_label(label)
        if not name:
            continue
        direction = NO_DIRECTION
        if directions:
            direction = view(_cell((box[0] + box[2]) / 2 / width), _cell((box[1] + box[3]) / 2 / height))
        rows.append(HudRow(name, float(score), direction))
    return select_rows(rows, config.rows)
