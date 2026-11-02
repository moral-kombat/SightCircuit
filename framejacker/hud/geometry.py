"""Native-pixel rectangle rules shared by the runtime mask and qualification.

Rectangles are ``(x0, y0, x1, y1)`` in acquisition pixels: x0/y0 inclusive,
x1/y1 exclusive. One eye-partition rule serves both consumers so a mapping
checked offline describes the same eye regions the runtime mask applies.
"""

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
