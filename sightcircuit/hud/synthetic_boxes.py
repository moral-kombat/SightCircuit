"""Synthetic boxes on the box panel: the synthetic rows' driver, drawing boxes written here (work-order item B5).

The first displayed step on the panel needs a picture that comes from no
camera: scripted boxes through the real output path, under a one-run scope
record that names the panel. ``SyntheticBoxes`` is ``SyntheticRows`` with
another picture and nothing else: the same schedule of updates and stops,
the same observed-frame path, the same deadlines (each update is observed
when it is drawn and expires one second later), the same expiry observed
in each stop, the same report and the same end. It ends by itself at its
bound and on a stop signal exactly as the rows do, and in both cases the
client's close takes the panel down by name. No camera is opened, no
detector is loaded and no presence watch is held: nothing here is derived
from a camera.

The picture. Every update draws ``box_scenes.orientation_scene``: four
corner boxes that differ in size and shape and a centre box, each named by
where it is in the image, so that a wearer can say which way up and which
way round the panel is shown and whether the room is visible between the
boxes. Until the first stop the picture does not change. After it the
centre box's name is the update's counter (``count 0031``), so that a
picture that has stopped updating can be told from one that has not, while
the corners stay where they were. A run with no stop would never show the
counter, so it is refused by name (``synthetic_boxes_need_a_stop``); the
rows, whose counter runs from the first update, keep their own rule.
"""
from .box_render import draw_boxes
from .box_scenes import orientation_scene
from .contract import BOX_PANEL_CONFIG, PanelBox
from .synthetic_rows import SyntheticRows, counter_label

__all__ = ("REPORT_KIND", "STOPS_REFUSAL", "SyntheticBoxes")

REPORT_KIND = "camera_hud_synthetic_boxes"
STOPS_REFUSAL = "synthetic_boxes_need_a_stop"  # No stop, no counter: a frozen picture could not be told from a live one.


class SyntheticBoxes(SyntheticRows):
    """One bounded run of the orientation scene into the bridge's observed-frame path, with scheduled stops.

    ``client`` is an ``ObservedFrameClient`` that names the box panel, not
    started. Everything else is ``SyntheticRows``.
    """
    LAYOUT, KIND, PICTURE = BOX_PANEL_CONFIG, REPORT_KIND, "orientation_scene_then_counter"

    def __init__(self, client, **options):
        if getattr(client, "layout", None) is not BOX_PANEL_CONFIG:
            raise ValueError("synthetic_boxes_client_layout_mismatch")  # The bridge would be launched for the strip.
        super().__init__(client, config=BOX_PANEL_CONFIG, **options)
        if not any(kind == "stop" for kind, _ in self.plan):
            raise ValueError(STOPS_REFUSAL)
        self.scene = orientation_scene(self.config)
        self.first_segment = self.plan[0][1]  # Updates before the first stop: the scene as it is.

    def _tile(self, number):
        boxes = self.scene
        if number > self.first_segment:
            *corners, centre = boxes
            boxes = (*corners, PanelBox(counter_label(number), centre.confidence, centre.box_xyxy))
        image = draw_boxes(boxes, self.config)
        if image.shape != (self.config.height_px, self.config.width_px, 4):
            raise ValueError("synthetic_rows_tile_size_mismatch")
        return self.config.width_px, self.config.height_px, image.tobytes()
