"""A region of view for camera recognition: crop before the resize, detect, and map the boxes back.

Work-order item L14. The Frame's camera image is 2464 pixels square and very
wide; resized whole to the 960-pixel processing size, a hand-held object is
too small for the detectors (E100: a bottle at 0.5 m in 0 of 150 frames). A
``Region`` is a rectangle of the native frame given as fractions (``x0, y0,
x1, y1``, origin top left) and a quarter-turn ``rotation``. ``prepare`` takes
that rectangle out of the native frame as a view (no pixel outside it is
copied or converted), resizes it to at most the processing size, never up,
turns it clockwise by ``rotation`` degrees and returns the detector's input
with the ``Mapping`` that takes the detector's boxes back.

One box space. Every mapped box is in ``FRAME_PIXELS``: the whole native
frame scaled so that its longest side is at most the processing size, which
is exactly the space the detector's input has when no region is set
(``frame.DETECTOR_PIXELS`` of the whole frame resized to 960). So cells,
sides and coverage computed from mapped boxes mean what they meant before,
and boxes from different regions of one run can be compared. A box is clamped
to the frame and kept at least one pixel wide and tall.

Nothing here knows which way up the camera image is, or where "straight
ahead" falls in it: no calibration value is read. The rotation and the
rectangle are the caller's arguments, and the ``scan`` schedule
(``scan_schedule``) is how one bounded run tries the candidates: each
processed frame takes the next entry of a fixed list of (region, rotation),
every scan detector looks at that one prepared image, and ``scan_summary``
tallies, per detector, by entry, by rotation and by region, the frames
tried, the frames with the target, the best and median confidence and the
median target box in ``FRAME_PIXELS``. Counts, closed names and boxes only:
no pixel leaves ``prepare``'s return value.
"""
from dataclasses import dataclass
import math
import statistics

from ..convert import MAX_PROCESSING_SIDE, buffer_view, processing_size, view_bgr
from ..frame import Detection, DetectionResult

__all__ = ("FRAME_PIXELS", "MAX_PROCESSING_SIDE", "ORIENTATION_STATEMENT", "ROTATIONS", "WHOLE", "MIN_FRACTION", "MIN_PIXELS", "Region", "Mapping", "parse_region",
           "prepare", "box_space", "scan_schedule", "scan_summary", "scan_result", "SCAN_REGIONS", "SCAN_REGION_NAMES",
           "BEST_ENTRY_MIN_FRAMES", "SCAN_FAILURE_REASONS", "AXIS_STATEMENT", "COVERAGE_OMITTED")

FRAME_PIXELS = "whole_frame_processing_pixels"  # The whole native frame, longest side at most the processing size.
ROTATIONS = (0, 90, 180, 270)  # Degrees clockwise the region is turned before the detector sees it.
WHOLE = (0.0, 0.0, 1.0, 1.0)
MIN_FRACTION = 0.05  # A region is at least this much of the frame on each axis,
MIN_PIXELS = 8  # and at least this many native pixels.
ORIENTATION_STATEMENT = (
    "The region and the rotation are arguments, not findings: nothing here knows which way up the camera image is or "
    "where straight ahead falls in it, and no calibration value is read. Boxes are over the image as exported.")


def _check_fractions(values):
    try:
        x0, y0, x1, y1 = (float(value) for value in values)
    except (TypeError, ValueError):
        raise ValueError("a region is four fractions of the frame: x0,y0,x1,y1") from None
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("a region needs 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    if x1 - x0 < MIN_FRACTION - 1e-9 or y1 - y0 < MIN_FRACTION - 1e-9:
        raise ValueError(f"a region is at least {MIN_FRACTION} of the frame on each axis")
    return x0, y0, x1, y1


@dataclass(frozen=True)
class Region:
    """A rectangle of the native frame as fractions, and the quarter turn applied before detection."""
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 1.0
    y1: float = 1.0
    rotation: int = 0
    name: str = ""  # A closed name from the scan's list, or "" for an operator's region.

    def __post_init__(self):
        fractions = _check_fractions((self.x0, self.y0, self.x1, self.y1))
        for field, value in zip(("x0", "y0", "x1", "y1"), fractions):
            object.__setattr__(self, field, value)
        if type(self.rotation) is not int or self.rotation not in ROTATIONS:
            raise ValueError("a rotation is 0, 90, 180 or 270 degrees")
        if type(self.name) is not str or (self.name and self.name not in SCAN_REGION_NAMES):
            raise ValueError("a region's name is one of the scan's")

    @property
    def fractions(self):
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def whole(self):
        """Whether this is the whole frame, unturned: then nothing is cropped and the run is what it was."""
        return self.fractions == WHOLE and self.rotation == 0

    def pixels(self, width, height):
        """``(left, top, right, bottom)`` in native pixels, or ``ValueError`` when the frame is too small for it."""
        left, top = round(self.x0 * width), round(self.y0 * height)
        right, bottom = round(self.x1 * width), round(self.y1 * height)
        if right - left < min(MIN_PIXELS, width) or bottom - top < min(MIN_PIXELS, height):
            raise ValueError("region_too_small_for_frame")
        return left, top, right, bottom

    def report(self):
        """The region as the report states it: fractions, the rotation and, for a scan entry, its closed name."""
        entry = {"region": [round(value, 6) for value in self.fractions], "rotation": self.rotation}
        if self.name:
            entry["name"] = self.name
        return entry


def parse_region(text):
    """``X0,Y0,X1,Y1`` as fractions of the frame, checked; ``ValueError`` otherwise."""
    parts = text.split(",") if isinstance(text, str) else ()
    if len(parts) != 4:
        raise ValueError("a region is four fractions of the frame: x0,y0,x1,y1")
    return _check_fractions(parts)


def box_space(width, height, max_side=MAX_PROCESSING_SIDE):
    """The size of ``FRAME_PIXELS`` for a native ``width`` x ``height`` frame."""
    return processing_size(width, height, max_side)


@dataclass(frozen=True)
class Mapping:
    """How one frame's detector input relates to the whole frame: enough to take a box back, and nothing else."""
    native_size: tuple  # (width, height) of the frame.
    crop: tuple  # (left, top, right, bottom) in native pixels.
    resized: tuple  # (width, height) the crop was resized to, before the turn.
    rotation: int
    space: tuple  # (width, height) of ``FRAME_PIXELS``.

    @property
    def input_size(self):
        """(width, height) of the detector's input: the resized crop after the turn."""
        return self.resized if self.rotation in (0, 180) else (self.resized[1], self.resized[0])

    def _unturned(self, u, v):
        width, height = self.resized
        if self.rotation == 0:
            return u, v
        if self.rotation == 90:  # Clockwise: a point (x, y) went to (height - y, x).
            return v, height - u
        if self.rotation == 180:
            return width - u, height - v
        return width - v, u  # 270 clockwise: (x, y) went to (y, width - x).

    def to_frame(self, box_xyxy):
        """A box in the detector's input pixels as integer ``FRAME_PIXELS``, clamped and at least one pixel each way."""
        ax, ay = self._unturned(box_xyxy[0], box_xyxy[1])
        bx, by = self._unturned(box_xyxy[2], box_xyxy[3])
        left, top, right, bottom = self.crop
        scale_x = (right - left) / self.resized[0] * self.space[0] / self.native_size[0]
        scale_y = (bottom - top) / self.resized[1] * self.space[1] / self.native_size[1]
        offset_x = left * self.space[0] / self.native_size[0]
        offset_y = top * self.space[1] / self.native_size[1]

        def fit(low, high, offset, scale, limit):
            low, high = sorted((offset + low * scale, offset + high * scale))
            low = int(min(max(round(low), 0), limit - 1))
            return low, int(min(max(round(high), low + 1), limit))
        x0, x1 = fit(ax, bx, offset_x, scale_x, self.space[0])
        y0, y1 = fit(ay, by, offset_y, scale_y, self.space[1])
        return x0, y0, x1, y1

    def result(self, result):
        """``result`` with every box in ``FRAME_PIXELS``."""
        return DetectionResult(tuple(Detection(item.label, item.confidence, self.to_frame(item.box_xyxy))
                                     for item in result.detections), result.inference_ms)

    def report(self):
        return {"crop_native_xyxy": list(self.crop), "resized_size": list(self.resized)}


def prepare(frame, region, max_side=MAX_PROCESSING_SIDE):
    """The detector's input for ``region`` of ``frame`` and its ``Mapping``.

    The rectangle is sliced from the frame's buffer as a view; only it is
    converted to BGR, resized (area interpolation, never enlarged) and
    turned. What is returned is an owned, writable, contiguous image no
    larger than ``max_side``; the frame's buffer is not written to.
    """
    import cv2
    import numpy as np

    view, order = buffer_view(frame.pixels, frame.pixel_format, frame.width, frame.height, frame.stride)
    left, top, right, bottom = region.pixels(frame.width, frame.height)
    image = view_bgr(view[top:bottom, left:right], order)
    width, height = processing_size(right - left, bottom - top, max_side)
    if (width, height) != (right - left, bottom - top):
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    if region.rotation:
        image = cv2.rotate(image, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                                   270: cv2.ROTATE_90_COUNTERCLOCKWISE}[region.rotation])
    if not (image.flags.writeable and image.flags.c_contiguous and image.flags.owndata):
        image = np.ascontiguousarray(image).copy() if not image.flags.c_contiguous else image.copy()
    return image, Mapping((frame.width, frame.height), (left, top, right, bottom), (width, height), region.rotation,
                          box_space(frame.width, frame.height, max_side))


# ---- the scan ------------------------------------------------------------------------------------------------------

# Six windows. The whole frame is the run as it was (E100). The centre half is the owner's choice of 2026-11-20 (the
# middle of the view, enlarged). The four corner windows are 0.6 of the frame and overlap by 0.2, so an object up to
# a fifth of the frame lies whole in at least one of them wherever it is, and one near the middle lies in all five
# enlarged windows.
SCAN_REGIONS = (("whole", WHOLE), ("centre_half", (0.25, 0.25, 0.75, 0.75)),
                ("upper_left", (0.0, 0.0, 0.6, 0.6)), ("upper_right", (0.4, 0.0, 1.0, 0.6)),
                ("lower_left", (0.0, 0.4, 0.6, 1.0)), ("lower_right", (0.4, 0.4, 1.0, 1.0)))
SCAN_REGION_NAMES = tuple(name for name, _fractions in SCAN_REGIONS)


def scan_schedule(rotations=ROTATIONS):
    """The fixed scan schedule: every scan window at every rotation, as ``Region``s.

    Frame ``n`` of a run takes entry ``n % len(schedule)``. One cycle is
    ``len(rotations)`` passes over the windows; in pass ``p`` window ``w`` is
    turned by ``rotations[(w - p) % len(rotations)]``, so every stretch of
    six frames visits every window and consecutive frames never share a
    rotation. With four rotations that is 24 entries, 4.8 seconds at 5
    frames a second: a 150-frame run gives each entry 6 or 7 frames, each
    rotation 37 or 38 and each window 25, for every scan detector,
    because each detector looks at every frame.
    """
    rotations = tuple(rotations)
    if not rotations or len(set(rotations)) != len(rotations) or any(turn not in ROTATIONS for turn in rotations):
        raise ValueError("scan rotations are distinct values of 0, 90, 180 and 270")
    return tuple(Region(*fractions, rotation=rotations[(position - turn) % len(rotations)], name=name)
                 for turn in range(len(rotations)) for position, (name, fractions) in enumerate(SCAN_REGIONS))


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


# An entry can be ``best_entry`` only with this many frames tried. A 150-frame run gives every entry 6 or 7, so 3 is
# under half an entry's share: a run cut that short for an entry says little about it, and one lucky frame (1 of 1)
# can no longer outrank 5 of 6.
BEST_ENTRY_MIN_FRAMES = 3
# Why a scan detector stopped being used: closed names, never the exception's text.
SCAN_FAILURE_REASONS = FAILURE_RAISED, FAILURE_INVALID_RESULT = ("detector_raised", "invalid_detection_result")
AXES = {0: "0_180", 180: "0_180", 90: "90_270", 270: "90_270"}
AXIS_STATEMENT = (
    "A turn and its opposite are hard to tell apart on an object that looks alike from both ends: read by_axis for "
    "the axis of the image, and settle which way up with a run that marks up and down (the positions script).")
COVERAGE_OMITTED = "scan_pools_views"  # Why a scan's summary has no coverage: its frames are 24 different views.


def _share(hits, frames):
    return round(hits / frames, 4) if frames else None


def _tally(results):
    """Frames tried, frames with the target and their share, best and median confidence, the median target box."""
    hits = [result["target"] for result in results if result["target"]["present"]]
    tally = {"frames": len(results), "target_frames": len(hits), "share": _share(len(hits), len(results)),
             "best_confidence": None, "median_confidence": None, "median_box_xyxy": None, "median_centre_fraction": None}
    if hits:
        tally.update(best_confidence=max(hit["confidence"] for hit in hits),
                     median_confidence=round(statistics.median(hit["confidence"] for hit in hits), 4),
                     median_box_xyxy=[int(round(statistics.median(hit["box_xyxy"][side] for hit in hits)))
                                      for side in range(4)],
                     median_centre_fraction=[round(statistics.median(hit["centre_fraction"][axis] for hit in hits), 4)
                                             for axis in range(2)])
    return tally


def scan_result(record, name, run_detector):
    """One detector's result in a scan record, ``{target, inference_ms}``, or ``None`` when it did not look.

    The run's own detector's result is the record's own (``target``,
    ``inference_ms``), not repeated under ``scan``; it did not look at a
    frame marked ``run_detector_failed``. Another detector's is
    ``record["scan"][name]``, absent once that detector has failed.
    """
    if name == run_detector:
        return None if record.get("run_detector_failed") else record
    return (record.get("scan") or {}).get(name)


def scan_summary(records, schedule, detectors, run_detector, *, geometry=None, failures=None):
    """Counts-only tallies of a scan, per detector: by entry, by rotation, by axis and by region.

    ``records`` are the per-frame records of one camera, each with ``view``
    (``index`` into ``schedule``) and each detector's result as
    ``scan_result`` finds it (``target`` from ``marks.target_of`` on the
    mapped boxes, so every box is in ``FRAME_PIXELS``). Every tally row
    gives the frames tried, the frames with the target and their ``share``:
    groups differ in size (38, 38, 37 and 37 frames per rotation in a
    150-frame run), so rows are compared by share, never by count.
    ``by_axis`` pools each rotation with its opposite (``AXIS_STATEMENT``).
    ``best_entry`` is the entry, among those tried at least
    ``BEST_ENTRY_MIN_FRAMES`` times, with the largest share (then the most
    frames with the target, then the best confidence), or ``None`` when no
    such entry held the target: a reading aid, not a verdict.

    ``geometry`` (entry index to ``Mapping.report()``) is stated once per
    schedule entry instead of in every record. ``failures`` (detector name
    to ``{reason, frame_index}``) are the detectors that stopped being used
    part way: their tallies cover the frames before that.
    """
    geometry, failures = geometry or {}, failures or {}
    listed = [dict(index=index, **entry.report(), **geometry.get(index, {})) for index, entry in enumerate(schedule)]
    blocks = {}
    for name in detectors:
        by_index, times, hits = {}, [], 0
        for record in records:
            result = scan_result(record, name, run_detector)
            if result is None:
                continue
            by_index.setdefault(record["view"]["index"], []).append(result)
            times.append(result["inference_ms"])
            hits += bool(result["target"]["present"])

        def group(key, by_index=by_index):
            keys = list(dict.fromkeys(key(entry) for entry in schedule))
            return [{"key": value, **_tally([result for index, entry in enumerate(schedule) if key(entry) == value
                                             for result in by_index.get(index, ())])} for value in keys]
        entries = [{"index": index, **_tally(by_index.get(index, ()))} for index in range(len(schedule))]
        eligible = [entry for entry in entries if entry["frames"] >= BEST_ENTRY_MIN_FRAMES and entry["target_frames"]]
        best = max(eligible, key=lambda entry: (entry["share"], entry["target_frames"], entry["best_confidence"],
                                                -entry["index"]), default=None)
        blocks[name] = {"frames": len(times), "target_frames": hits, "share": _share(hits, len(times)),
                        "failed": failures.get(name),
                        "best_entry": None if best is None else best["index"],
                        "inference_ms": {"p50": _percentile(times, 0.5), "p95": _percentile(times, 0.95),
                                         "max": max(times)} if times else None,
                        "by_rotation": group(lambda entry: entry.rotation),
                        "by_axis": group(lambda entry: AXES[entry.rotation]),
                        "by_region": group(lambda entry: entry.name), "entries": entries}
    return {"schedule": listed, "schedule_length": len(listed), "frames": len(records), "run_detector": run_detector,
            "detectors_failed": [name for name in detectors if name in failures],
            "best_entry_min_frames": BEST_ENTRY_MIN_FRAMES, "axis_statement": AXIS_STATEMENT,
            "detectors": blocks, "box_coordinate_space": FRAME_PIXELS}
