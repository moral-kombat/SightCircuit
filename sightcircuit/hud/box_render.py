"""Box panel drawing: a thin outline and a name per box on a fully transparent RGBA image.

Work-order item B1. ``draw_boxes`` takes ``PanelBox`` values (names, scores
and rectangles in panel pixels) and a ``BoxPanelConfig`` and nothing else: no
frame, no scene image and no camera pixel can reach the panel through it. It
displays nothing; what it returns is an array.

The drawing rules, all in integer pixels and none anti-aliased, so the image
is the same on every build and alpha is only 0 or 255:

* **Transparency.** A pixel that belongs to no outline and no name tag is
  ``(0, 0, 0, 0)``. A drawn pixel has alpha 255. So the image reads the same
  whether a compositor takes its alpha as straight or premultiplied.
* **Outline.** A band ``outline_px + 2 * casing_px`` wide, drawn inwards
  from the box's rectangle: a bright core between two dark casing lines, so
  one edge of the band always contrasts with the room behind it, bright or
  dark. The inside of a box stays transparent.
* **A side on the panel's edge is left open.** An edge of the panel is never
  an outline: a box that reaches it may continue beyond it, and the two
  neighbouring sides run out to the edge.
* **A tiny box** is drawn ``min_box_px`` wide or tall about its own centre,
  moved back inside the panel when that would leave it.
* **Name.** A dark plate with the name in a light colour, its lower left
  corner on the box's upper left corner. With no room above the box the
  plate sits inside it, under the top side; with no room to the right it
  moves left until it ends at the panel's edge. A name wider than
  ``name_max_px`` is cut to its longest prefix that fits.
* **Order.** Every outline is drawn before any plate, and within each pass
  the first box is drawn last: where two overlap, the earlier box (the more
  confident, as ``contract.select_boxes`` orders them) is the one on top,
  and no outline crosses a name.
"""
from .contract import BoxPanelConfig, panel_box_valid

__all__ = ("draw_boxes", "name_drawn", "outline_rect", "tag_rect")

FONTS = {"hershey_simplex": 0}  # ``name_font`` to the OpenCV face (FONT_HERSHEY_SIMPLEX).


def _check_config(config):
    if not isinstance(config, BoxPanelConfig):
        raise ValueError("box_panel_config_invalid")
    integers = (config.width_px, config.height_px, config.max_boxes, config.outline_px, config.casing_px,
                config.min_box_px, config.name_thickness_px, config.name_pad_px, config.name_max_px)
    colours = (config.outline_rgb, config.casing_rgb, config.plate_rgb, config.name_rgb)
    if (any(type(value) is not int or value < 1 for value in integers) or
            config.name_font not in FONTS or type(config.name_font_scale) is not float or
            not 0 < config.name_font_scale <= 4 or
            # A grown box keeps a transparent inside, and fits the panel with its name plate.
            config.min_box_px <= 2 * (config.outline_px + 2 * config.casing_px) or
            config.min_box_px > min(config.width_px, config.height_px) or
            any(type(colour) is not tuple or len(colour) != 3 or
                any(type(part) is not int or not 0 <= part <= 255 for part in colour) or not any(colour)
                for colour in colours)):
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


def _text_size(text, config):
    import cv2
    (width, height), baseline = cv2.getTextSize(text, FONTS[config.name_font], config.name_font_scale,
                                                config.name_thickness_px)
    return width, height, baseline


def name_drawn(label, config):
    """The name as drawn: ``label``, or its longest prefix at most ``name_max_px`` wide, without trailing spaces."""
    text = label
    while len(text) > 1 and _text_size(text, config)[0] > config.name_max_px:
        text = text[:-1].rstrip()
    return text


def tag_rect(rect, text, config):
    """The name plate of the box drawn at ``rect``: ``(x0, y0, x1, y1)`` in panel pixels, always inside the panel."""
    width, height, baseline = _text_size(text, config)
    width = min(width + 2 * config.name_pad_px, config.width_px)
    height = min(height + baseline + 2 * config.name_pad_px, config.height_px)
    top = rect[1] - height
    if top < 0:  # No room above: inside the box, under its top side.
        top = min(rect[1], config.height_px - height)
    left = max(0, min(rect[0], config.width_px - width))
    return left, top, left + width, top + height


def draw_boxes(boxes, config):
    """An RGBA image (``height_px`` x ``width_px`` x 4, ``uint8``, read-only) of ``boxes`` on the panel of ``config``.

    ``boxes`` is at most ``config.max_boxes`` valid ``PanelBox`` values
    (``contract.panel_box_valid``), most important first; anything else is a
    ``ValueError``. Deliberately takes no scene image.
    """
    import cv2
    import numpy as np

    _check_config(config)
    if not isinstance(boxes, (list, tuple)) or len(boxes) > config.max_boxes:
        raise ValueError("box_panel_box_budget_exceeded")
    if not all(panel_box_valid(box, config) for box in boxes):
        raise ValueError("box_panel_box_invalid")
    image = np.zeros((config.height_px, config.width_px, 4), dtype=np.uint8)

    def fill(x0, y0, x1, y1, rgb):
        if x0 < x1 and y0 < y1:
            image[y0:y1, x0:x1] = (*rgb, 255)

    band, casing = config.outline_px + 2 * config.casing_px, config.casing_px
    drawn = [(box, *outline_rect(box.box_xyxy, config)) for box in boxes]
    for _box, (x0, y0, x1, y1), (left, top, right, bottom) in reversed(drawn):
        for inset, thickness, rgb in ((0, band, config.casing_rgb), (casing, config.outline_px, config.outline_rgb)):
            # A drawn side stops ``inset`` short of a drawn neighbour's outer edge; an open neighbour is the
            # panel's edge, and the side runs out to it.
            ya, yb = y0 + (0 if top else inset), y1 - (0 if bottom else inset)
            xa, xb = x0 + (0 if left else inset), x1 - (0 if right else inset)
            if not left:
                fill(x0 + inset, ya, x0 + inset + thickness, yb, rgb)
            if not right:
                fill(x1 - inset - thickness, ya, x1 - inset, yb, rgb)
            if not top:
                fill(xa, y0 + inset, xb, y0 + inset + thickness, rgb)
            if not bottom:
                fill(xa, y1 - inset - thickness, xb, y1 - inset, rgb)
    for box, rect, _opened in reversed(drawn):
        text = name_drawn(box.label, config)
        x0, y0, x1, y1 = tag_rect(rect, text, config)
        fill(x0, y0, x1, y1, config.plate_rgb)
        _width, height, _baseline = _text_size(text, config)
        mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        cv2.putText(mask, text, (config.name_pad_px, config.name_pad_px + height), FONTS[config.name_font],
                    config.name_font_scale, 255, config.name_thickness_px, cv2.LINE_8)
        image[y0:y1, x0:x1][mask > 0] = (*config.name_rgb, 255)
    image.flags.writeable = False
    return image
