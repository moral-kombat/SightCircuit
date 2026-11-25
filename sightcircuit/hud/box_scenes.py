"""Synthetic scenes for the box panel: boxes written here, never detections (work-order item B3).

``orientation_scene`` is the scene a later synthetic displayed step can show
to settle what the cloud cannot: which way up and which way round the Frame
shows a panel image, and whether its compositor honours the image's alpha.
Each box is named by where it is drawn in the image (row 0 is the image's
top, column 0 its left), and the four corner boxes differ in size and shape
as well, so the answer does not depend on reading the names:

* ``top left`` is the largest box, wider than tall, and is drawn in the
  image's first rows and first columns. A wearer who sees the largest box
  at the top left, lying wider than tall, with every name reading left to
  right, is looking at an image whose row 0 is displayed at the top and
  whose column 0 is at the left. Both halves of the rule are needed: a flip
  about the diagonal leaves the largest box at the top left but stands it
  taller than wide. Of the seven ways a picture can be flipped or turned by
  quarter turns, none leaves the largest box at the top left wider than
  tall (a test checks all seven), and the names say which one happened.
* everything that is not an outline or a name plate is fully transparent in
  the image. A wearer who sees the room between the boxes and inside them is
  looking at a panel whose per-pixel alpha is honoured; a dark or opaque
  square 0.9 m wide is one whose alpha is not.

This module only lists boxes. It draws nothing, displays nothing and is
used by no stage or command: the displayed step itself is device work with
its own approval. The tests draw the scene with ``box_render.draw_boxes``
and check what the bridge hands to the overlay call on the fake runtime.
"""
from .contract import BoxPanelConfig, PanelBox

# name -> (the corner of the panel the box sits in, its width and height in sixty-fourths of the panel's).
# Sizes differ so that the corners can be told apart without reading: the largest, wider than tall, is the image's
# top left.
ORIENTATION_CORNERS = (("top left", (0, 0), (20, 12)), ("top right", (1, 0), (12, 12)),
                       ("bottom left", (0, 1), (20, 8)), ("bottom right", (1, 1), (8, 8)))
ORIENTATION_CENTRE = ("centre", (12, 12))
ORIENTATION_INSET = 6  # Sixty-fourths of the panel between a corner box and the panel's edges: no side is open.
ORIENTATION_PARTS = 64


def orientation_scene(config):
    """The five boxes of the orientation scene for ``config``, most important first (``top left``).

    Rectangles are whole pixels of ``box_panel_pixels``. A configuration
    whose panel is too small for five distinct boxes is refused
    (``box_scene_panel_too_small``); the contract's own panel is not.
    """
    if not isinstance(config, BoxPanelConfig):
        raise ValueError("box_panel_config_invalid")
    width, height = config.width_px, config.height_px
    if min(width, height) < 4 * ORIENTATION_PARTS or config.max_boxes < len(ORIENTATION_CORNERS) + 1:
        raise ValueError("box_scene_panel_too_small")

    def part(value, size):
        return value * size // ORIENTATION_PARTS

    boxes = []
    for index, (name, (right, bottom), (w, h)) in enumerate(ORIENTATION_CORNERS):
        box_w, box_h = part(w, width), part(h, height)
        x0 = width - part(ORIENTATION_INSET, width) - box_w if right else part(ORIENTATION_INSET, width)
        y0 = height - part(ORIENTATION_INSET, height) - box_h if bottom else part(ORIENTATION_INSET, height)
        boxes.append(PanelBox(name, 1.0 - index / 10, (x0, y0, x0 + box_w, y0 + box_h)))
    name, (w, h) = ORIENTATION_CENTRE
    box_w, box_h = part(w, width), part(h, height)
    x0, y0 = (width - box_w) // 2, (height - box_h) // 2
    boxes.append(PanelBox(name, 0.5, (x0, y0, x0 + box_w, y0 + box_h)))
    return tuple(boxes)
