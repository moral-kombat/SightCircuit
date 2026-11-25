"""The camera HUD stage on the box panel (work-order item B5): the strip's stage suite, run again with boxes.

``tests/test_camera_hud_stage.py`` is the stage's suite for the strip. Its
two classes are run here a second time with the stage built for
``head-fixed-box-panel-v1``: the same scripted detector, the same camera
stand-in, the validation bridge and the bridge's ``--fixed-hud`` mode on the
fake OpenVR runtime, every update the panel's 1,048,576 bytes. The
assertions are the strip's own, unedited: what is sent and when, the
deadline, the acknowledgements, the ledger, an empty result taking the tile
down, expiry, the end of a run, a dead bridge. That they hold is the
evidence that those are one code path for both layouts. Tests that are
about the strip's rows and arrows are replaced here by the panel's.

Nothing here is a device, a display or SteamVR, and no time is a Frame's.
"""
import unittest

from sightcircuit.hud.contract import BOX_PANEL_CONFIG
from tests import test_camera_hud_stage as strip

PANEL = BOX_PANEL_CONFIG
UPDATE_BYTES = PANEL.width_px * PANEL.height_px * 4


class PanelCase:
    """The strip's ``StageCase`` with the box panel: the layout, a neutral alignment, and the whole frame."""
    LAYOUT = PANEL

    @property
    def STAGE_OPTIONS(self):
        from sightcircuit.hud.boxes import Alignment
        return {"config": PANEL, "alignment": Alignment()}

    def expected_payload(self, result, mapping=None, alignment=None):
        """``(labels, rgba bytes)`` the stage must send for ``result``: the panel drawn from its boxes alone."""
        from sightcircuit.camera.region import Mapping
        from sightcircuit.hud.box_render import draw_boxes
        from sightcircuit.hud.boxes import Alignment, camera_boxes
        mapping = Mapping((64, 48), (0, 0, 64, 48), (64, 48), 0, (64, 48)) if mapping is None else mapping
        selection = camera_boxes(list(mapping.result(result).detections), mapping, PANEL, threshold=0.5,
                                 alignment=Alignment() if alignment is None else alignment)
        return tuple(dict.fromkeys(selection.labels)), draw_boxes(selection.boxes, PANEL).tobytes()

    def stage(self, *arguments, **options):
        from sightcircuit.camera.region import Mapping
        stage = super().stage(*arguments, **options)
        # For the tests that call the stage's hooks by hand, without a frame: the stand-in's 64 x 48 frame, whole.
        stage._mapping = Mapping((64, 48), (0, 0, 64, 48), (64, 48), 0, (64, 48))
        return stage


@unittest.skipUnless(strip.RUNNABLE, "Needs Linux, NumPy, OpenCV and scripts/build_overlay_bridge.sh")
class BoxPanelStageTests(PanelCase, strip.CameraHudStageTests):
    # The strip's rows, arrows and orientations: not the panel's. Its own drawing is tested below.
    test_names_only_rows_and_each_orientation_render = None

    def test_boxes_are_drawn_where_the_mapping_puts_them_and_the_bytes_sent_are_the_renderers_alone(self):
        import numpy as np
        from sightcircuit.camera.region import FRAME_PIXELS
        from sightcircuit.hud.boxes import Alignment
        from sightcircuit.hud.contract import config_digest
        script = (("bottle", strip.LEFT), ("cup", strip.LOW))
        result = strip.DetectionResult(tuple(strip.Detection(label, 0.9, box) for label, box in script), 1.0)
        report = self.stage(strip.ScriptedDetector([script])).run()
        labels, expected = self.expected_payload(result)
        self.assertEqual((report["status"], report["reason"], labels), ("completed", "max_frames", ("bottle", "cup")))
        self.assertEqual(len(self.queued), 8)
        for item in self.queued:
            self.assertEqual((item["size"], len(item["rgba"]), item["token"]), ((512, 512), UPDATE_BYTES, labels))
            self.assertEqual(item["rgba"], expected)  # Drawn from the boxes alone: no pixel of a frame is in it.
        # Where the boxes are, by hand: a 64 x 48 frame on a 512 x 512 panel is 8 panel pixels a frame pixel
        # across and 10 2/3 down. LEFT is (1, 2, 30, 40) and LOW (24, 40, 40, 47), halves rounded up.
        image = np.frombuffer(expected, np.uint8).reshape(512, 512, 4)
        self.assertEqual(sorted(set(image[..., 3].ravel().tolist())), [0, 255])  # Binary alpha, as drawn.
        for (x0, y0, x1, y1) in ((8, 21, 240, 427), (192, 427, 320, 501)):
            self.assertTrue(image[y0 + 1, x0 + 1:x1 - 1, 3].all() and image[y0 + 1:y1 - 1, x0 + 1, 3].all())  # Outline.
        self.assertFalse(image[200:250, 60:180, 3].any())  # Inside the first box: transparent.
        self.assertEqual(report["config"], {
            "hud_config_id": "head-fixed-box-panel-v1", "hud_config_sha256": config_digest(PANEL), "tile_px": [512, 512],
            "rows": None, "max_boxes": 5, "threshold": 0.5, "directions": None, "orientation": None,
            "alignment": {"scale_x": 1.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0, "turn": 0},
            "max_fps": 10.0, "max_frames": 0, "seconds": None, "max_display_age_ms": report["config"]["max_display_age_ms"],
            "keepalive_seconds": report["config"]["keepalive_seconds"], "update_bytes": 1_048_576})
        self.assertEqual(report["view"], {
            "region": {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}, "rotation": 0, "whole_frame": True, "prepared": True,
            "box_coordinate_space": FRAME_PIXELS, "source_space_px": [64, 48], "crop_native_xyxy": [0, 0, 64, 48],
            "resized_px": [64, 48], "geometry_constant": True})
        # The boxes block: ``boxes_report`` of every counted result, summed. Counts and format metadata only.
        boxes = report["boxes"]
        self.assertEqual((boxes["results_counted"], boxes["detections"], boxes["boxes_shown"], boxes["boxes_dropped"]),
                         (8, 16, 16, 0))
        self.assertEqual((boxes["layout_id"], boxes["panel_px"], boxes["max_boxes"], boxes["alignment_neutral"],
                          boxes["source_space_px"], boxes["region_crop_native_xyxy"], boxes["region_rotation"],
                          set(boxes["dropped"].values())),
                         ("head-fixed-box-panel-v1", [512, 512], 5, True, [64, 48], [0, 0, 64, 48], 0, {0}))
        self.assertEqual([(row["label"], row["spells"]) for row in report["labels"]["rows"]], [("bottle", 1), ("cup", 1)])
        # This side's steps, timed for every result drawn and, for pack and queue, every result sent.
        steps = report["steps_ms"]
        self.assertEqual([steps[name]["count"] for name in ("map", "select", "draw", "pack", "queue")], [8] * 5)
        self.assertEqual((steps["unit"], steps["clock"]), ("ms", "host_perf_counter"))
        self.assertEqual(report["frames"]["acknowledged_presented"], 8)
        self.assert_both_children_closed(report)
        self.assert_no_gate_opened(report)
        # The alignment is applied: other numbers put the same boxes elsewhere, and the report states them.
        aligned = Alignment(scale_x=0.5, scale_y=0.75, offset_x=0.1, offset_y=-0.05, turn=90)
        report = self.stage(strip.ScriptedDetector([script]), limits=self.TWO, scenario="end:max_frames",
                            alignment=aligned).run()
        _, turned = self.expected_payload(result, alignment=aligned)
        self.assertEqual([item["rgba"] == turned for item in self.queued], [True, True])
        self.assertNotEqual(turned, expected)
        self.assertEqual((report["config"]["alignment"], report["boxes"]["alignment"], report["boxes"]["alignment_neutral"]),
                         (aligned.report(), aligned.report(), False))

    def test_a_result_with_no_box_to_show_takes_the_panel_down_and_sends_nothing_as_no_rows_does(self):
        # The strip's rule, kept: a result with nothing to show takes the tile down at once with a hide request
        # and no frame is sent; an all-transparent megabyte is never uploaded. Whatever leaves a result without
        # a box counts: no detection, a score under the threshold, more than the cap. Each is counted under its reason.
        from sightcircuit.camera.region import Region
        inside = ("bottle", (26, 18, 38, 30))
        many = tuple((f"box{index}", (20 + index, 16 + index, 30 + index, 26 + index)) for index in range(7))

        class Scores(strip.ScriptedDetector):
            def detect(self, image, threshold):
                result = super().detect(image, threshold)
                call = len(self.called_ns)
                return result if call != 3 else strip.DetectionResult(
                    tuple(strip.Detection(item.label, 0.2, item.box_xyxy) for item in result.detections), 1.0)

        detector = Scores([(inside,), (), (inside,), (inside,), many, (inside,)])
        report = self.stage(detector, limits={"fps": 5, "max_seconds": 5, "max_frames": 6}).run()
        self.assertEqual((report["status"], [item["frame_id"] for item in self.queued]), ("completed", [1, 4, 5, 6]))
        self.assertEqual((report["results"]["results_with_rows"], report["results"]["results_without_rows"]), (4, 2))
        self.assertEqual([event for event, _ in self.packets],
                         ["frame_sent", "taken_down", "frame_sent", "shown", "frame_sent", "frame_sent", "taken_down"])
        self.assertTrue(all(len(item["rgba"]) == UPDATE_BYTES and any(item["rgba"]) for item in self.queued))
        boxes = report["boxes"]
        self.assertEqual((boxes["results_counted"], boxes["detections"], boxes["boxes_shown"], boxes["boxes_dropped"]),
                         (6, 11, 8, 3))
        self.assertEqual({name: count for name, count in boxes["dropped"].items() if count},
                         {"below_threshold": 1, "over_cap": 2})
        self.assertEqual(len(self.queued[2]["token"]), 5)  # The cap: five of the seven.
        self.assert_both_children_closed(report)
        # Through a region with no detection at all: the same rule, and the block still states the region.
        centre = Region(0.25, 0.25, 0.75, 0.75)
        report = self.stage(strip.ScriptedDetector([()]), limits=self.TWO, scenario="end:max_frames", region=centre).run()
        self.assertEqual((self.queued, report["results"]["results_without_rows"], report["boxes"]["detections"],
                          report["bridge"]["take_downs"], report["frames"]["sent"]), ([], 2, 0, 1, 0))  # One hide.
        self.assertEqual((report["boxes"]["results_counted"], report["boxes"]["region_crop_native_xyxy"]),
                         (2, [16, 12, 48, 36]))


@unittest.skipUnless(strip.RUNNABLE and strip.fake_vrclient.available(), "Needs the bridge, its SDK checkout and g++")
class BoxPanelFixedHudStageTests(PanelCase, strip.FixedHudStageTests):
    pass


if __name__ == "__main__":
    unittest.main()
