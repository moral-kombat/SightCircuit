"""Box selection for the box panel: camera detections to panel boxes through the wearer's alignment.

Work-order item B2, beside ``camera_rows``. ``camera_boxes`` takes one
frame's detections in the whole-frame box space of the region of view
(``camera.region.FRAME_PIXELS``), the ``region.Mapping`` the frame was
prepared with (its crop, its rotation and the space's size) and an
``Alignment``, and returns ``PanelBox`` values in panel pixels for
``box_render.draw_boxes``. It displays nothing, approves nothing and opens no
gate.

The alignment is the project's own: at most five numbers the wearer sets
(item B4), neutral by default. It is not the SDK's calibration and is never
derived from it; this module reads no calibration value and imports nothing
that holds one.

The mapping, in exact rational arithmetic (``fractions.Fraction``; rounding
happens once, at the end):

1. **Region.** The region's rectangle in the box space is the mapping's
   native crop scaled by ``space / native_size``. A box whose centre lies
   outside it is dropped (``outside_region``); a kept box is cut to it.
2. **Upright.** A point of the region becomes fractions ``(u, v)`` of the
   rectangle and is turned clockwise by the mapping's ``rotation``, exactly
   as ``region.prepare`` turned the detector's input: after it the region's
   rectangle is the unit square as the detector saw it.
3. **Alignment.** A further clockwise ``turn`` about the centre, then on
   each axis ``0.5 + (p - 0.5) * scale + offset``: a scale about the panel's
   centre and an offset in panel widths (or heights). The neutral alignment
   changes nothing, so the region's rectangle lands exactly on the panel's.
4. **Panel.** Fractions times the panel's size. A box whose centre is off
   the panel is dropped (``off_panel``); a kept box is cut to the panel,
   rounded to whole pixels (halves up) and kept at least one pixel wide and
   tall. A side cut by the panel's edge lies on it, which the renderer draws
   as an open side.

Then the shared hygiene (``contract.select_boxes``): descending confidence,
then name, then rectangle; exact duplicates dropped; at most
``config.max_boxes`` kept.
"""
from dataclasses import dataclass
from fractions import Fraction
import math
import numbers

from ..camera.region import FRAME_PIXELS, ROTATIONS, Mapping
from ..frame import Detection
from .contract import BOX_PANEL_PIXELS, BoxPanelConfig, PanelBox, config_digest, row_label, select_boxes

__all__ = ("ALIGNMENT_FIELDS", "BOX_REPORT_KEYS", "DROP_REASONS", "MAX_DETECTIONS", "NEUTRAL", "OFFSET_LIMIT",
           "SCALE_LIMITS", "Alignment", "BoxSelection", "boxes_report", "camera_boxes", "panel_point", "turned")

MAX_DETECTIONS = 128  # One frame's detection budget, as ``camera_rows``'.
SCALE_LIMITS = (0.25, 4.0)  # The region drawn from a quarter of the panel's side to four times it.
OFFSET_LIMIT = 1.0  # At most one panel width or height either way.
ALIGNMENT_FIELDS = ("scale_x", "scale_y", "offset_x", "offset_y", "turn")
# Why a detection got no box, in the order the rules apply. Closed names; each detection is counted once.
DROP_REASONS = ("invalid", "below_threshold", "unnamed", "outside_region", "off_panel", "duplicate", "over_cap")


def _real(value):
    """A finite real number as a plain ``int`` or ``float``, or ``None``.

    Python and NumPy integers and floats are accepted (NumPy registers its
    scalars as ``numbers.Real``) and converted once; ``bool`` and NumPy's
    boolean are refused, as is anything that is not a number.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    value = int(value) if isinstance(value, numbers.Integral) else float(value)
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class Alignment:
    """The wearer's alignment of the region of view to the panel: five numbers, each doing one thing.

    ``scale_x`` and ``scale_y`` stretch the picture about the panel's centre;
    ``offset_x`` and ``offset_y`` move it, in panel widths and heights
    (positive right and down); ``turn`` is a further clockwise quarter turn
    in degrees, applied before the scale. ``Alignment()`` is neutral. The
    numbers are the project's own format metadata: set by the wearer, never
    read from or derived from the SDK's calibration.
    """
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    turn: int = 0

    def __post_init__(self):
        for name in ("scale_x", "scale_y"):
            value = _real(getattr(self, name))
            if value is None or not SCALE_LIMITS[0] <= value <= SCALE_LIMITS[1]:
                raise ValueError("box_alignment_scale_invalid")
            object.__setattr__(self, name, float(value))
        for name in ("offset_x", "offset_y"):
            value = _real(getattr(self, name))
            if value is None or not -OFFSET_LIMIT <= value <= OFFSET_LIMIT:
                raise ValueError("box_alignment_offset_invalid")
            object.__setattr__(self, name, float(value))
        if type(self.turn) is not int or self.turn not in ROTATIONS:
            raise ValueError("box_alignment_turn_invalid")

    @property
    def neutral(self):
        return self == NEUTRAL

    def report(self):
        """The five numbers as the report states them."""
        return {name: getattr(self, name) for name in ALIGNMENT_FIELDS}


NEUTRAL = Alignment()


def turned(u, v, rotation):
    """Fractions ``(u, v)`` of a rectangle after it is turned clockwise by ``rotation`` degrees.

    ``region.Mapping``'s convention as fractions: at 90 a pixel ``(x, y)`` of
    a ``w`` x ``h`` image goes to ``(h - y, x)``, so ``(u, v)`` goes to
    ``(1 - v, u)``; at 270 ``(x, y)`` goes to ``(y, w - x)``.
    """
    if rotation == 0:
        return u, v
    if rotation == 90:
        return 1 - v, u
    if rotation == 180:
        return 1 - u, 1 - v
    if rotation == 270:
        return v, 1 - u
    raise ValueError("a rotation is 0, 90, 180 or 270 degrees")


def _check_mapping(mapping):
    values = ()
    if isinstance(mapping, Mapping):
        parts = (mapping.native_size, mapping.crop, mapping.space)
        if all(type(part) is tuple for part in parts) and tuple(map(len, parts)) == (2, 4, 2):
            values = (*mapping.native_size, *mapping.crop, *mapping.space)
    if (len(values) != 8 or any(type(value) is not int for value in values) or mapping.rotation not in ROTATIONS or
            type(mapping.rotation) is not int or min(mapping.native_size) < 1 or min(mapping.space) < 1 or
            not (0 <= mapping.crop[0] < mapping.crop[2] <= mapping.native_size[0] and
                 0 <= mapping.crop[1] < mapping.crop[3] <= mapping.native_size[1])):
        raise ValueError("box_mapping_invalid")


def _region_rect(mapping):
    """The region's rectangle in the box space, exactly: the native crop scaled by ``space / native_size``."""
    scale_x = Fraction(mapping.space[0], mapping.native_size[0])
    scale_y = Fraction(mapping.space[1], mapping.native_size[1])
    left, top, right, bottom = mapping.crop
    return left * scale_x, top * scale_y, right * scale_x, bottom * scale_y


def _check_config(config):
    if not isinstance(config, BoxPanelConfig):  # A configuration validates itself when it is built.
        raise ValueError("box_panel_config_invalid")


def _check_threshold(threshold):
    value = _real(threshold)
    if value is None or not 0 <= value <= 1:
        raise ValueError("hud_threshold_invalid")
    return float(value)


def panel_point(x, y, mapping, config, alignment=NEUTRAL):
    """A point of the box space as exact panel coordinates (``Fraction`` pixels, not rounded, not clipped)."""
    _check_mapping(mapping)
    _check_config(config)
    if not isinstance(alignment, Alignment):
        raise ValueError("box_alignment_invalid")
    left, top, right, bottom = _region_rect(mapping)
    u, v = (Fraction(x) - left) / (right - left), (Fraction(y) - top) / (bottom - top)
    p, q = turned(*turned(u, v, mapping.rotation), alignment.turn)
    half = Fraction(1, 2)
    p = half + (p - half) * Fraction(alignment.scale_x) + Fraction(alignment.offset_x)
    q = half + (q - half) * Fraction(alignment.scale_y) + Fraction(alignment.offset_y)
    return p * config.width_px, q * config.height_px


@dataclass(frozen=True)
class BoxSelection:
    """One frame's boxes for the panel, what was dropped, and everything they were selected with.

    The selection carries its own settings (the geometry's digest, the
    mapping's space, crop and rotation, the threshold and the alignment), so
    ``boxes_report`` states what was used and cannot be handed another
    alignment or threshold by mistake.
    """
    boxes: tuple
    detections: int
    dropped: tuple  # ``(reason, count)`` for every reason of ``DROP_REASONS``, in that order.
    config_sha256: str
    space: tuple  # (width, height) of the box space the detections were in.
    crop: tuple  # The region as the native crop taken: (left, top, right, bottom).
    rotation: int
    threshold: float
    alignment: Alignment

    @property
    def labels(self):
        return [box.label for box in self.boxes]


def _pixels(low, high, limit):
    """An exact, already clipped interval as whole pixels: halves up, inside ``[0, limit]``, at least one wide."""
    low = min(max(math.floor(low + Fraction(1, 2)), 0), limit - 1)
    return low, min(max(math.floor(high + Fraction(1, 2)), low + 1), limit)


def camera_boxes(detections, mapping, config, *, threshold, alignment=NEUTRAL):
    """A ``BoxSelection``: at most ``config.max_boxes`` ``PanelBox`` values for one camera frame's detections.

    ``detections`` are ``Detection`` values whose boxes are in
    ``FRAME_PIXELS`` of ``mapping.space`` (``mapping.result`` produces
    them). As ``camera_rows``, a detection is considered when its score is
    from ``threshold`` to 1, its box is finite, nonempty and inside the
    space, and its label keeps a printable character (``contract.row_label``);
    the caller passes the threshold (``detection.POC_THRESHOLD`` for the
    proof of concept). Numbers differ from ``camera_rows`` in one way: any
    real number is taken, NumPy scalars of every width included, and a
    ``bool`` is not a number here (``camera_rows`` takes ``int`` and
    ``float`` and their subclasses, so ``np.float64`` and ``True`` but not
    ``np.float32`` or ``np.int64``). The module's docstring has the mapping and its two
    drop rules; every detection that gets no box is counted under one of
    ``DROP_REASONS``.
    """
    _check_mapping(mapping)
    _check_config(config)
    if not isinstance(alignment, Alignment):
        raise ValueError("box_alignment_invalid")
    threshold = _check_threshold(threshold)
    if not isinstance(detections, (list, tuple)) or len(detections) > MAX_DETECTIONS:
        raise ValueError("hud_detection_budget_exceeded")
    dropped = dict.fromkeys(DROP_REASONS, 0)
    space_width, space_height = mapping.space
    left, top, right, bottom = _region_rect(mapping)
    width, height = config.width_px, config.height_px
    mapped = []
    for detection in detections:
        box, score, label = (getattr(detection, name, None) for name in ("box_xyxy", "confidence", "label"))
        score = _real(score)
        box = tuple(map(_real, box)) if isinstance(box, (list, tuple)) and len(box) == 4 else (None,)
        if (not isinstance(detection, Detection) or None in box or score is None or not 0 <= score <= 1 or
                not isinstance(label, str) or
                not (0 <= box[0] < box[2] <= space_width and 0 <= box[1] < box[3] <= space_height)):
            dropped["invalid"] += 1
            continue
        if score < threshold:
            dropped["below_threshold"] += 1
            continue
        name = row_label(label)
        if not name:
            dropped["unnamed"] += 1
            continue
        x0, y0, x1, y1 = (Fraction(value) for value in box)
        if not (left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom):
            dropped["outside_region"] += 1
            continue
        ax, ay = panel_point(max(x0, left), max(y0, top), mapping, config, alignment)
        bx, by = panel_point(min(x1, right), min(y1, bottom), mapping, config, alignment)
        (px0, px1), (py0, py1) = sorted((ax, bx)), sorted((ay, by))
        if not (0 <= (px0 + px1) / 2 <= width and 0 <= (py0 + py1) / 2 <= height):
            dropped["off_panel"] += 1
            continue
        x0, x1 = _pixels(max(px0, 0), min(px1, width), width)
        y0, y1 = _pixels(max(py0, 0), min(py1, height), height)
        mapped.append(PanelBox(name, float(score), (x0, y0, x1, y1)))
    kept, dropped["duplicate"], dropped["over_cap"] = select_boxes(mapped, config.max_boxes)
    return BoxSelection(kept, len(detections), tuple(dropped.items()), config_digest(config), mapping.space,
                        mapping.crop, mapping.rotation, threshold, alignment)


# Keys of the report block of one displayed frame: counts and format metadata only. No label, no box and no
# pixel is in it; ``check_report`` of the camera stage accepts it as a block (``{"boxes": BOX_REPORT_KEYS}``).
BOX_REPORT_KEYS = ("layout_id", "config_sha256", "panel_px", "max_boxes", "box_coordinate_space",
                   "source_coordinate_space", "source_space_px", "region_crop_native_xyxy", "region_rotation",
                   "alignment", "alignment_neutral", "threshold", "detections", "boxes_shown", "boxes_dropped",
                   "dropped")


def boxes_report(selection, config):
    """The counts-only block for one frame's ``BoxSelection``: exactly the keys of ``BOX_REPORT_KEYS``.

    The geometry's name and digest, the panel's size, the two coordinate
    spaces, the region and the alignment are format metadata; the rest are
    counts (boxes shown, detections dropped and why). The region, the
    threshold and the alignment are the selection's own, the ones
    ``camera_boxes`` used; ``config`` must be the configuration it was
    selected for (``box_selection_config_changed`` otherwise).
    """
    _check_config(config)
    if (not isinstance(selection, BoxSelection) or tuple(name for name, _count in selection.dropped) != DROP_REASONS or
            any(type(count) is not int or count < 0 for _name, count in selection.dropped) or
            type(selection.detections) is not int or len(selection.boxes) > config.max_boxes or
            len(selection.boxes) + sum(count for _name, count in selection.dropped) != selection.detections or
            not isinstance(selection.alignment, Alignment) or type(selection.threshold) is not float):
        raise ValueError("box_selection_invalid")
    if selection.config_sha256 != config_digest(config):
        raise ValueError("box_selection_config_changed")
    return {"layout_id": config.layout_id, "config_sha256": selection.config_sha256,
            "panel_px": [config.width_px, config.height_px], "max_boxes": config.max_boxes,
            "box_coordinate_space": BOX_PANEL_PIXELS, "source_coordinate_space": FRAME_PIXELS,
            "source_space_px": list(selection.space), "region_crop_native_xyxy": list(selection.crop),
            "region_rotation": selection.rotation, "alignment": selection.alignment.report(),
            "alignment_neutral": selection.alignment.neutral, "threshold": selection.threshold,
            "detections": selection.detections, "boxes_shown": len(selection.boxes),
            "boxes_dropped": selection.detections - len(selection.boxes), "dropped": dict(selection.dropped)}
