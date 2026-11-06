"""Native-pixel rectangle rules shared by the runtime mask and qualification.

Rectangles are ``(x0, y0, x1, y1)`` in acquisition pixels: x0/y0 inclusive,
x1/y1 exclusive. One eye-partition rule serves both consumers so a mapping
checked offline describes the same eye regions the runtime mask applies.

The second half holds the geometric HUD prediction (``project``, ``hull``,
``predict_hud_footprint``): a cross-check from a recorded view record that is
labelled a prediction and never feeds qualification.
"""
import math

LAYOUTS = ("mono", "stereo_sbs", "stereo_tb")


def eye_viewports(bounds, layout):
    """Split ``bounds`` into one rectangle per eye; uneven splits fail closed."""
    x0, y0, x1, y1 = bounds
    if layout == "mono":
        return ((x0, y0, x1, y1),)
    if layout == "stereo_sbs" and (x1 - x0) % 2 == 0:
        middle = (x0 + x1) // 2
        return ((x0, y0, middle, y1), (middle, y0, x1, y1))
    if layout == "stereo_tb" and (y1 - y0) % 2 == 0:
        middle = (y0 + y1) // 2
        return ((x0, y0, x1, middle), (x0, middle, x1, y1))
    raise ValueError("hud_capture_layout_invalid")


def contains(outer, inner):
    """True when ``inner`` is nonempty and lies entirely inside ``outer``."""
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def exclusion(box, regions, eyes):
    """Why a native box may not become HUD content: ``"mask_overlap"``, ``"eye_crossing"`` or None.

    The runtime mask drops such a box and the validation ledger invalidates
    its run on it; both classify with this one rule, mask overlap first.
    """
    if any(overlaps(box, region) for region in regions):
        return "mask_overlap"
    if sum(contains(eye, box) for eye in eyes) != 1:
        return "eye_crossing"
    return None


def expand(rect, margin):
    x0, y0, x1, y1 = rect
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def union_area(rectangles):
    """Exact pixel area of a few rectangles' union, without allocating an image."""
    xs = sorted({coordinate for rect in rectangles for coordinate in (rect[0], rect[2])})
    total = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((rect[1], rect[3]) for rect in rectangles if rect[0] < right and left < rect[2])
        end, height = -1, 0
        for low, high in intervals:
            height += max(0, high - max(low, end))
            end = max(end, high)
        total += (right - left) * height
    return total


def within_blind_budget(regions, eye):
    """The masked union inside one eye may not exceed a quarter of that eye."""
    return union_area(regions) <= area(eye) / 4


# ---------------------------------------------------------------------------
# Geometric prediction (package M): a cross-check for G3/G4, never evidence.
#
# ``project`` and ``hull`` are ported from DeeJanuz/vr-recorder ``vr-mask.cpp``
# ``Project()`` and ``Hull()`` (L917-L977) at commit
# 78ab97e0700b6668978722111770b4657ec2ec92. The file has no header; its LICENSE:
#
#   MIT License
#
#   Copyright (c) 2026 DeeJanuz
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.
#
# Changes: the pinhole is the four GetProjectionRaw tangents rather than one
# focal length about the centre; points are already in eye coordinates; and the
# upstream clamp at four times the larger frame dimension (L949-L952, despite
# its "no clamping" comment) is replaced by a check. After the near and widened
# side planes every point lies on or inside the widened window, so a point
# outside it means non-finite input or a bug, and raises instead of bending the
# polygon. Everything after ``hull`` is original Framejacker code.

NEAR_Z = -0.02         # metres; the eye looks down -Z
CLIP_MARGIN = 0.25     # fraction of the frame kept beyond each edge
_WINDOW_TOLERANCE = 1e-6
PREDICTION_SCOPE = ("Geometric prediction from a recorded view record: a cross-check only. "
                    "A prediction cannot qualify a mapping (docs/hud-qualification.md) and never feeds qualification.")


def _finite(*values):
    return all(type(value) in (int, float) and math.isfinite(value) for value in values)


def project(polygon, tangents, size, *, margin=CLIP_MARGIN, near_z=NEAR_Z):
    """Clip an eye-space polygon to the near plane and the widened frame, then project it.

    ``polygon`` is a list of ``(x, y, z)`` in eye coordinates (x right, y up,
    -z forward). ``tangents`` are ``(left, right, top, bottom)`` as
    ``GetProjectionRaw`` returns them: the tangent of each frame edge, with
    image y pointing down, so ``top`` is normally negative. ``size`` is
    ``(width, height)`` in pixels. Returns ``(u, v)`` pixel points (u right, v
    down, origin at the top-left frame corner); an empty list when nothing is in
    front of the eye inside the widened window.
    """
    left, right, top, bottom = tangents
    width, height = size
    if not _finite(left, right, top, bottom, width, height, margin, near_z) or not (
            right > left and bottom > top and width > 0 and height > 0 and margin >= 0 and near_z < 0):
        raise ValueError("hud_projection_invalid")
    span_x, span_y = right - left, bottom - top
    wide = (left - margin * span_x, right + margin * span_x, top - margin * span_y, bottom + margin * span_y)
    w_left, w_right, w_top, w_bottom = wide
    # >= 0 on the kept side of each plane through the eye (the near plane does not pass through it).
    planes = (lambda p: near_z - p[2],
              lambda p: w_right * -p[2] - p[0],
              lambda p: p[0] - w_left * -p[2],
              lambda p: -p[1] - w_top * -p[2],
              lambda p: w_bottom * -p[2] + p[1])
    points = [tuple(float(c) for c in point) for point in polygon]
    if not all(len(point) == 3 and _finite(*point) for point in points):
        raise ValueError("hud_projection_invalid")
    for inside in planes:  # Sutherland-Hodgman, plane by plane
        if not points:
            break
        kept = []
        for index, a in enumerate(points):
            b = points[(index + 1) % len(points)]
            da, db = inside(a), inside(b)
            if da >= 0:
                kept.append(a)
            if (da >= 0) != (db >= 0):
                t = da / (da - db)
                kept.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
        points = kept
    projected = []
    for x, y, z in points:
        tx, ty = x / -z, -y / -z
        if not (w_left - _WINDOW_TOLERANCE <= tx <= w_right + _WINDOW_TOLERANCE and
                w_top - _WINDOW_TOLERANCE <= ty <= w_bottom + _WINDOW_TOLERANCE):
            raise ValueError("hud_projection_outside_window")  # no silent clamp
        projected.append(((tx - left) / span_x * width, (ty - top) / span_y * height))
    return projected


def hull(points):
    """Convex hull by monotone chain, counter-clockwise in (u, v) from the smallest point; duplicates removed."""
    unique = sorted(set((float(u), float(v)) for u, v in points))
    if len(unique) < 3:
        return unique
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def clip_to_frame(points, size):
    """Clip a 2D pixel polygon to ``[0, width] x [0, height]`` (Sutherland-Hodgman)."""
    width, height = size
    edges = (lambda p: p[0], lambda p: width - p[0], lambda p: p[1], lambda p: height - p[1])
    points = list(points)
    for inside in edges:
        if not points:
            break
        kept = []
        for index, a in enumerate(points):
            b = points[(index + 1) % len(points)]
            da, db = inside(a), inside(b)
            if da >= 0:
                kept.append(a)
            if (da >= 0) != (db >= 0):
                t = da / (da - db)
                kept.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        points = kept
    return points


def polygon_area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2


def _rigid(matrix):
    """A 3x4 rigid transform (rows of rotation | translation), validated."""
    if (type(matrix) is not list or len(matrix) != 3 or
            any(type(row) is not list or len(row) != 4 or not _finite(*row) for row in matrix)):
        raise ValueError("hud_view_record_transform_invalid")
    rotation = [row[:3] for row in matrix]
    for i in range(3):
        for j in range(3):
            dot = sum(rotation[i][k] * rotation[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > 1e-4:
                raise ValueError("hud_view_record_transform_not_rigid")
    return rotation, [row[3] for row in matrix]


def head_to_eye(point, eye_to_head):
    """Head-space point into eye space: the inverse of ``eye_to_head`` (rotation transposed)."""
    rotation, translation = _rigid(eye_to_head)
    offset = [point[k] - translation[k] for k in range(3)]
    return tuple(sum(rotation[k][i] * offset[k] for k in range(3)) for i in range(3))


def hud_quad(config):
    """Head-relative HUD panel corners (top-left, top-right, bottom-right, bottom-left) in metres.

    The bridge places the panel with identity rotation at ``(x_m, y_m,
    -distance_m)`` relative to the HMD; its height follows the texture aspect.
    """
    half_w = config.panel_width_m / 2
    half_h = config.panel_width_m * config.height_px / config.width_px / 2
    x, y, z = config.x_m, config.y_m, -config.distance_m
    return [(x - half_w, y + half_h, z), (x + half_w, y + half_h, z),
            (x + half_w, y - half_h, z), (x - half_w, y - half_h, z)]


VIEW_MODES = ("left", "right", "both")


def _view_record(record):
    if type(record) is not dict or record.get("report_kind") != "framejacker_headset_view_record":
        raise ValueError("hud_view_record_invalid")
    view = record.get("headset_view")
    if type(view) is not dict or view.get("mode") not in VIEW_MODES:
        raise ValueError("hud_view_record_mode_invalid")
    size = (view.get("width"), view.get("height"))
    if any(type(side) is not int or not 0 < side <= 16384 for side in size):
        raise ValueError("hud_view_record_size_invalid")
    if type(view.get("cropped")) is not bool:
        raise ValueError("hud_view_record_cropped_invalid")
    return view, size


def predict_hud_footprint(record, config):
    """Predict where the HUD quad lands in the mirror's native pixels from a recorded view record.

    The result is a prediction, labelled ``prediction_only`` with
    ``qualifies_mapping`` false: a prediction cannot qualify a mapping and is
    never read by ``framejacker.hud.qualification`` or ``candidate``. It
    assumes the mirror's native pixels are the IVRHeadsetView image of the
    view record's eye at the view size, uncropped. Whether ``/dev/video99``
    carries the IVRHeadsetView output is unknown, so that is recorded as an
    unknown, not assumed true.
    """
    view, size = _view_record(record)
    prediction = {
        "kind": "geometric_prediction", "prediction_only": True, "qualifies_mapping": False,
        "scope": PREDICTION_SCOPE, "layout_id": config.layout_id, "frame": list(size),
        "assumptions": ["mirror native pixels are the IVRHeadsetView image at the view size",
                        "single-eye view mode, uncropped, no lens or orientation change"],
        "unknowns": {"mirror_carries_headset_view": "unknown: not established for /dev/video99"},
        "eye": None, "status": None, "projected_polygon": [], "in_frame_polygon": [],
        "in_frame_bounds": None, "in_frame_fraction": None,
    }
    if view["mode"] == "both":
        prediction["status"] = "unsupported_view_mode"
        return prediction
    if view["cropped"]:
        prediction["status"] = "unsupported_cropped_view"
        return prediction
    eye = view["mode"]
    eye_record = (record.get("eyes") or {}).get(eye)
    if type(eye_record) is not dict:
        raise ValueError("hud_view_record_eye_missing")
    raw = eye_record.get("projection_raw")
    if type(raw) is not dict:
        raise ValueError("hud_view_record_projection_invalid")
    tangents = tuple(raw.get(key) for key in ("left", "right", "top", "bottom"))
    corners = [head_to_eye(corner, eye_record.get("eye_to_head")) for corner in hud_quad(config)]
    projected = hull(project(corners, tangents, size))
    inside = clip_to_frame(projected, size)
    prediction.update(eye=eye, projected_polygon=[[round(u, 3), round(v, 3)] for u, v in projected])
    if not projected:
        # project() returns nothing both when every corner is at or behind the near plane
        # and when the quad lies wholly outside the widened window; only the first is "behind".
        in_front = any(z <= NEAR_Z for _, _, z in corners)
        prediction["status"] = "off_frame" if in_front else "behind_eye"
        return prediction
    total = polygon_area(projected)
    if len(projected) < 3 or total <= 0:
        prediction["status"] = "degenerate"  # an edge-on quad: a hull with no area
        return prediction
    visible = polygon_area(inside) if len(inside) >= 3 else 0.0
    if visible <= 0:
        prediction["status"] = "off_frame"
        return prediction
    prediction.update(
        in_frame_polygon=[[round(u, 3), round(v, 3)] for u, v in inside],
        in_frame_bounds=[round(min(u for u, _ in inside), 3), round(min(v for _, v in inside), 3),
                         round(max(u for u, _ in inside), 3), round(max(v for _, v in inside), 3)],
        in_frame_fraction=round(visible / total, 6) if total > 0 else None,
        status="inside" if total > 0 and abs(visible - total) <= 1e-9 * max(1.0, total) else "partly_off_frame")
    return prediction
