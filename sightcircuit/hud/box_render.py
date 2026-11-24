"""Box panel drawing: a thin outline and a name per box on a fully transparent RGBA image.

Work-order item B1. ``draw_boxes`` takes ``PanelBox`` values (names, scores
and rectangles in panel pixels) and a ``BoxPanelConfig`` and nothing else: no
frame, no scene image and no camera pixel can reach the panel through it. It
displays nothing; what it returns is an array. ``layout`` is the same
decisions without the pixels: where each box, outline strip and name plate
goes.

Everything is whole pixels filled by this module: rectangles, and names from
the project's own glyph bitmaps (``box_font``) scaled by a whole number. No
library draws a line or a letter, so the image does not depend on a
rasteriser's version, and alpha is only 0 or 255. The rules:

* **Transparency.** A pixel that belongs to no outline and no name plate is
  ``(0, 0, 0, 0)``. A drawn pixel has alpha 255. So the image reads the same
  whether a compositor takes its alpha as straight or premultiplied.
* **Outline.** A band ``outline_px + 2 * casing_px`` wide, drawn inwards
  from the box's rectangle: a bright core between two dark casing lines, so
  one edge of the band always contrasts with the room behind it, bright or
  dark. The inside of a box stays transparent.
* **A side of the box that lies on the panel's edge is left open.** The box
  may continue beyond the panel there, and the two neighbouring sides run
  out to the edge. Only the box's own side counts: a tiny box that is moved
  inwards (next rule) keeps its sides, also where it then touches the edge.
  A box that fills the panel has no side left and is its name alone.
* **A tiny box** is drawn ``min_box_px`` wide or tall about its own centre,
  moved back inside the panel when that would leave it.
* **Name.** A dark plate with the name in a light colour. A name wider than
  ``name_max_px`` is cut to its longest prefix that fits, without a
  trailing space.
* **Where the plate goes.** A plate never covers any part of its own box's
  outline. The places are tried in the order of ``PLACEMENTS``: above the
  box from its left corner, below from its left corner, above and below
  from its right corner, beside it on the right, beside it on the left
  (each moved along the box only as far as the panel's edge requires), and
  last inside the box in its upper left corner, the name cut to the
  inside's width if it must be. A place is possible when the plate lies
  whole on the panel. Among the possible places the first is taken that
  touches neither a plate already placed (boxes are placed in their order,
  the most important first) nor the outline of any other box; failing
  that, the first that touches no plate already placed; failing that, the
  first possible. At least one place is always possible.
* **Order.** Every outline is drawn before any plate, and within each pass
  the first box is drawn last: where two outlines cross, or where a plate
  could not avoid another, the earlier box (the more confident, as
  ``contract.select_boxes`` orders them) is the one on top, and no outline
  is ever drawn over a name.

What can still collide: the outlines of two boxes cross where the boxes
overlap; a plate may lie over the transparent inside of another box; and
when every place of a later box is taken, its plate covers part of another
box's outline or lies partly under an earlier plate.
"""
from dataclasses import dataclass

from . import box_font
from .contract import BoxPanelConfig, panel_box_valid

__all__ = ("PLACEMENTS", "BoxLayout", "draw_boxes", "layout", "name_drawn", "outline_rect", "outline_strips",
           "plate_places")

PLACEMENTS = ("above", "below", "above_right", "below_right", "right", "left", "inside")


def _check_config(config):
    if not isinstance(config, BoxPanelConfig):  # A configuration validates itself when it is built.
        raise ValueError("box_panel_config_invalid")


def outline_rect(box_xyxy, config):
    """The rectangle a box is drawn at, and which sides are open: ``((x0, y0, x1, y1), (left, top, right, bottom))``.

    A side is open when the box's own side lies on the panel's edge. A box
    under ``min_box_px`` on an axis is widened about its centre (the odd
    pixel goes to the right or the bottom) and moved inside the panel.
    """
    x0, y0, x1, y1 = box_xyxy
    opened = (x0 == 0, y0 == 0, x1 == config.width_px, y1 == config.height_px)

    def grow(low, high, limit):
        if high - low >= config.min_box_px:
            return low, high
        low -= (config.min_box_px - (high - low)) // 2
        low = min(max(low, 0), limit - config.min_box_px)
        return low, low + config.min_box_px
    x0, x1 = grow(x0, x1, config.width_px)
    y0, y1 = grow(y0, y1, config.height_px)
    return (x0, y0, x1, y1), opened


def outline_strips(rect, opened, config):
    """The filled rectangles of one outline: ``(casing, core)``, each a tuple of ``(x0, y0, x1, y1)``.

    The casing strips are the whole band of each closed side; the core
    strips lie inside them and are drawn over them. A closed side stops
    short of a closed neighbour's casing and runs out to an open one.
    """
    x0, y0, x1, y1 = rect
    left, top, right, bottom = opened
    layers = []
    for inset, thickness in ((0, config.outline_band_px), (config.casing_px, config.outline_px)):
        ya, yb = y0 + (0 if top else inset), y1 - (0 if bottom else inset)
        xa, xb = x0 + (0 if left else inset), x1 - (0 if right else inset)
        strips = []
        if not left:
            strips.append((x0 + inset, ya, x0 + inset + thickness, yb))
        if not right:
            strips.append((x1 - inset - thickness, ya, x1 - inset, yb))
        if not top:
            strips.append((xa, y0 + inset, xb, y0 + inset + thickness))
        if not bottom:
            strips.append((xa, y1 - inset - thickness, xb, y1 - inset))
        layers.append(tuple(strip for strip in strips if strip[0] < strip[2] and strip[1] < strip[3]))
    return tuple(layers)


def name_drawn(label, config, max_px=None):
    """The name as drawn: ``label``, or its longest prefix at most ``max_px`` (``name_max_px``) wide, no trailing space."""
    limit = config.name_max_px if max_px is None else min(max_px, config.name_max_px)
    text = label
    while len(text) > 1 and box_font.text_width(text, config.name_scale) > limit:
        text = text[:-1].rstrip()
    return text


def _plate(text, config):
    return box_font.text_width(text, config.name_scale) + 2 * config.name_pad_px, config.name_plate_height_px


def _touch(first, second):
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def plate_places(rect, opened, label, config):
    """Every possible place for the name plate of the box drawn at ``rect``: ``(placement, text, plate)`` in order.

    Each plate lies whole on the panel and touches no part of the box's own
    outline: the first six are outside ``rect``, the last inside the
    outline's band.
    """
    x0, y0, x1, y1 = rect
    panel_width, panel_height = config.width_px, config.height_px
    text = name_drawn(label, config)
    width, height = _plate(text, config)
    from_left, from_right = max(0, min(x0, panel_width - width)), max(0, min(x1 - width, panel_width - width))
    down = max(0, min(y0, panel_height - height))
    places = []
    if y0 - height >= 0:
        places += [("above", from_left, y0 - height), ("above_right", from_right, y0 - height)]
    if y1 + height <= panel_height:
        places += [("below", from_left, y1), ("below_right", from_right, y1)]
    if x1 + width <= panel_width:
        places.append(("right", x1, down))
    if x0 - width >= 0:
        places.append(("left", x0 - width, down))
    found = [(name, text, (left, top, left + width, top + height)) for name, left, top in places]
    band = config.outline_band_px
    ix0, iy0 = x0 + (0 if opened[0] else band), y0 + (0 if opened[1] else band)
    ix1, iy1 = x1 - (0 if opened[2] else band), y1 - (0 if opened[3] else band)
    inner = name_drawn(label, config, ix1 - ix0 - 2 * config.name_pad_px)
    inner_width, _height = _plate(inner, config)
    if inner_width <= ix1 - ix0 and height <= iy1 - iy0:
        found.append(("inside", inner, (ix0, iy0, ix0 + inner_width, iy0 + height)))
    return tuple(sorted(found, key=lambda place: PLACEMENTS.index(place[0])))


@dataclass(frozen=True)
class BoxLayout:
    """Where one box is drawn: its rectangle, open sides, outline strips, the name as drawn and its plate."""
    rect: tuple
    opened: tuple
    casing: tuple
    core: tuple
    text: str
    placement: str
    plate: tuple


def layout(boxes, config):
    """One ``BoxLayout`` per box, in the boxes' order. Pure integer geometry; nothing is drawn.

    ``boxes`` is at most ``config.max_boxes`` valid ``PanelBox`` values
    (``contract.panel_box_valid``), most important first; anything else is a
    ``ValueError``.
    """
    _check_config(config)
    if not isinstance(boxes, (list, tuple)) or len(boxes) > config.max_boxes:
        raise ValueError("box_panel_box_budget_exceeded")
    if not all(panel_box_valid(box, config) for box in boxes):
        raise ValueError("box_panel_box_invalid")
    outlines = []
    for box in boxes:
        rect, opened = outline_rect(box.box_xyxy, config)
        outlines.append((rect, opened, *outline_strips(rect, opened, config)))
    placed, plates = [], []
    for index, (box, (rect, opened, casing, core)) in enumerate(zip(boxes, outlines)):
        places = plate_places(rect, opened, box.label, config)
        others = [strip for other, outline in enumerate(outlines) if other != index for strip in outline[2]]

        def clear_of(plate, rects):
            return not any(_touch(plate, rect) for rect in rects)
        choice = next((place for place in places if clear_of(place[2], plates) and clear_of(place[2], others)), None)
        choice = choice or next((place for place in places if clear_of(place[2], plates)), None) or places[0]
        plates.append(choice[2])
        placed.append(BoxLayout(rect, opened, casing, core, choice[1], choice[0], choice[2]))
    return tuple(placed)


def draw_boxes(boxes, config):
    """An RGBA image (``height_px`` x ``width_px`` x 4, ``uint8``, read-only) of ``boxes`` on the panel of ``config``.

    ``boxes`` is as ``layout`` takes them. Deliberately takes no scene image.
    """
    import numpy as np

    placed = layout(boxes, config)
    image = np.zeros((config.height_px, config.width_px, 4), dtype=np.uint8)

    def fill(rect, rgb):
        image[rect[1]:rect[3], rect[0]:rect[2]] = (*rgb, 255)

    for box in reversed(placed):
        for strip in box.casing:
            fill(strip, config.casing_rgb)
        for strip in box.core:
            fill(strip, config.outline_rgb)
    scale, pad = config.name_scale, config.name_pad_px
    for box in reversed(placed):
        fill(box.plate, config.plate_rgb)
        cells = np.array(box_font.text_cells(box.text), dtype=bool).repeat(scale, axis=0).repeat(scale, axis=1)
        x, y = box.plate[0] + pad, box.plate[1] + pad
        image[y:y + cells.shape[0], x:x + cells.shape[1]][cells] = (*config.name_rgb, 255)
    image.flags.writeable = False
    return image
