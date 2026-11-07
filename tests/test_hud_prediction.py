"""Geometric HUD prediction against hand-computed polygons; a prediction never qualifies."""
import ast
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sightcircuit.hud import HUD_CONFIG
from sightcircuit.hud.geometry import (NEAR_Z, clip_to_frame, head_to_eye, hud_quad, hull, polygon_area,
                                      predict_hud_footprint, project)
from sightcircuit.hud.qualification import MappingCandidate, config_digest
from tests.test_hud_qualification import candidate_record

ROOT = Path(__file__).resolve().parents[1]
# A 90-degree square pinhole: tangent 1 each way, 200 x 200 pixels, so u = 100 * (x/-z + 1)
# and v = 100 * (-y/-z + 1). The widened window reaches tangent 1.5: pixels -50 to 250.
TANGENTS = (-1.0, 1.0, -1.0, 1.0)
SIZE = (200, 200)
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


def rounded(points):
    return [(round(u, 6), round(v, 6)) for u, v in points]


def view_record(mode="left", cropped=False, eye_to_head=None, tangents=TANGENTS, size=SIZE):
    eye = {"eye_to_head": eye_to_head or deepcopy(IDENTITY),
           "projection_raw": dict(zip(("left", "right", "top", "bottom"), tangents))}
    return {"report_kind": "sightcircuit_headset_view_record", "schema_version": 1,
            "headset_view": {"mode": mode, "mode_value": ("left", "right", "both").index(mode),
                             "width": size[0], "height": size[1], "cropped": cropped, "aspect_ratio": 1.0,
                             "blend_range": [0.25, 0.75]},
            "eyes": {"left": eye, "right": deepcopy(eye)}, "set_calls_made": False}


class ProjectTests(unittest.TestCase):
    def test_quad_inside_the_frame(self):
        quad = [(-0.2, 0.1, -1), (0.2, 0.1, -1), (0.2, -0.1, -1), (-0.2, -0.1, -1)]
        self.assertEqual(rounded(project(quad, TANGENTS, SIZE)), [(80, 90), (120, 90), (120, 110), (80, 110)])

    def test_quad_partly_off_frame_is_cut_at_the_widened_edge(self):
        quad = [(0.5, 0.1, -1), (2.0, 0.1, -1), (2.0, -0.1, -1), (0.5, -0.1, -1)]
        projected = hull(project(quad, TANGENTS, SIZE))
        # x/-z = 2.0 lies beyond the widened tangent 1.5, so the right side is cut at u = 250.
        self.assertEqual(rounded(projected), [(150, 90), (250, 90), (250, 110), (150, 110)])
        self.assertEqual(rounded(hull(clip_to_frame(projected, SIZE))), [(150, 90), (200, 90), (200, 110), (150, 110)])

    def test_quad_behind_the_eye_projects_to_nothing(self):
        quad = [(-0.2, 0.1, 1), (0.2, 0.1, 1), (0.2, -0.1, 1), (-0.2, -0.1, 1)]
        self.assertEqual(project(quad, TANGENTS, SIZE), [])
        # Just in front of the eye but nearer than the near plane is also gone.
        near = [(x, y, NEAR_Z / 2) for x, y, _ in quad]
        self.assertEqual(project(near, TANGENTS, SIZE), [])

    def test_quad_crossing_the_eye_plane_is_clipped_before_projection(self):
        # A floor strip from 1 m ahead to 1 m behind, 0.5 m below the eye. The near plane keeps
        # z <= -0.02; the widened bottom plane (tangent 1.5) then cuts at -z = 0.5 / 1.5 = 1/3,
        # where x/-z = +-0.3: u = 70 and 130, v = 250.
        strip = [(-0.1, -0.5, -1), (0.1, -0.5, -1), (0.1, -0.5, 1), (-0.1, -0.5, 1)]
        projected = hull(project(strip, TANGENTS, SIZE))
        self.assertEqual(rounded(projected), [(70, 250), (90, 150), (110, 150), (130, 250)])
        # In the frame, v = 200 is tangent 1, where -z = 0.5 and x/-z = +-0.2: u = 80 and 120.
        self.assertEqual(rounded(hull(clip_to_frame(projected, SIZE))), [(80, 200), (90, 150), (110, 150), (120, 200)])

    def test_asymmetric_tangents_and_non_square_frame(self):
        # left -1.5, right 0.5 (span 2), top -0.5, bottom 0.5 (span 1), 400 x 100 pixels:
        # u = 200 * (tx + 1.5), v = 100 * (ty + 0.5).
        points = project([(0.0, 0.0, -2), (0.4, 0.0, -2), (0.4, 0.4, -2)], (-1.5, 0.5, -0.5, 0.5), (400, 100))
        self.assertEqual(rounded(points), [(300, 50), (340, 50), (340, 30)])

    def test_projection_never_clamps(self):
        # Every projected point stays within the widened window, so there is nothing to clamp;
        # the upstream 4 x max(W, H) clamp is replaced by this guarantee and a raise.
        far = [(-50.0, 30.0, -0.03), (80.0, 30.0, -0.03), (80.0, -60.0, -0.03), (-50.0, -60.0, -0.03)]
        points = project(far, TANGENTS, SIZE)
        self.assertTrue(points)
        for u, v in points:
            self.assertTrue(-50 - 1e-6 <= u <= 250 + 1e-6 and -50 - 1e-6 <= v <= 250 + 1e-6, (u, v))
        for bad in ([(math.nan, 0, -1)] * 3, [(0, 0, -1), (1, 0)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                project(bad, TANGENTS, SIZE)
        for tangents in ((1.0, -1.0, -1.0, 1.0), (-1.0, 1.0, 1.0, -1.0), (-1.0, math.inf, -1.0, 1.0)):
            with self.subTest(tangents=tangents), self.assertRaisesRegex(ValueError, "hud_projection_invalid"):
                project([(0, 0, -1)] * 3, tangents, SIZE)

    def test_hull_is_monotone_chain(self):
        points = [(0, 0), (2, 0), (1, 1), (2, 2), (0, 2), (1, 0), (2, 2)]
        self.assertEqual(hull(points), [(0, 0), (2, 0), (2, 2), (0, 2)])
        self.assertEqual(hull([(1, 1), (1, 1), (0, 0)]), [(0, 0), (1, 1)])
        self.assertEqual(polygon_area(hull(points)), 4)


class PredictionTests(unittest.TestCase):
    PLACEMENT = replace(HUD_CONFIG, width_px=200, height_px=100, panel_width_m=0.4, distance_m=1.0,
                        x_m=0.05, y_m=-0.2)
    # Left eye 5 cm left of the head origin.
    LEFT_EYE = [[1.0, 0.0, 0.0, -0.05], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]

    def test_hud_quad_follows_the_panel_and_texture_aspect(self):
        self.assertEqual(rounded([(x, y) for x, y, _ in hud_quad(self.PLACEMENT)]),
                         [(-0.15, -0.1), (0.25, -0.1), (0.25, -0.3), (-0.15, -0.3)])
        self.assertEqual({z for *_, z in hud_quad(self.PLACEMENT)}, {-1.0})
        self.assertEqual(head_to_eye((0.05, -0.2, -1.0), self.LEFT_EYE), (0.1, -0.2, -1.0))

    def test_quad_inside_through_a_view_record(self):
        # Eye-space corners x -0.1..0.3, y -0.3..-0.1 at z = -1: u 90..130, v 110..130.
        prediction = predict_hud_footprint(view_record(eye_to_head=self.LEFT_EYE), self.PLACEMENT)
        self.assertEqual(prediction["status"], "inside")
        self.assertEqual(prediction["eye"], "left")
        self.assertEqual(prediction["projected_polygon"], [[90, 110], [130, 110], [130, 130], [90, 130]])
        self.assertEqual(prediction["in_frame_bounds"], [90, 110, 130, 130])
        self.assertEqual(prediction["in_frame_fraction"], 1.0)

    def test_quad_partly_off_frame_through_a_view_record(self):
        placement = replace(self.PLACEMENT, x_m=0.95)  # eye-space x 0.8..1.2: u 180..220
        prediction = predict_hud_footprint(view_record(eye_to_head=self.LEFT_EYE), placement)
        self.assertEqual(prediction["status"], "partly_off_frame")
        self.assertEqual(prediction["projected_polygon"], [[180, 110], [220, 110], [220, 130], [180, 130]])
        self.assertEqual(prediction["in_frame_polygon"], [[180, 110], [200, 110], [200, 130], [180, 130]])
        self.assertEqual(prediction["in_frame_fraction"], 0.5)

    def test_quad_off_frame_and_behind_the_eye(self):
        off = predict_hud_footprint(view_record(eye_to_head=self.LEFT_EYE), replace(self.PLACEMENT, x_m=1.35))
        self.assertEqual((off["status"], off["in_frame_polygon"], off["in_frame_bounds"]), ("off_frame", [], None))
        # An eye turned 180 degrees about y sees the panel behind it.
        turned = [[-1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, -1.0, 0.0]]
        behind = predict_hud_footprint(view_record(eye_to_head=turned), self.PLACEMENT)
        self.assertEqual((behind["status"], behind["projected_polygon"]), ("behind_eye", []))

    def test_in_front_but_outside_the_window_is_off_frame_not_behind(self):
        # x 4.8..5.2 at z = -1 is far beyond the widened window: nothing projects, yet the
        # quad is in front of the eye, so the label is off_frame, not behind_eye.
        far = predict_hud_footprint(view_record(eye_to_head=self.LEFT_EYE), replace(self.PLACEMENT, x_m=5.0))
        self.assertEqual((far["status"], far["projected_polygon"], far["in_frame_bounds"]), ("off_frame", [], None))

    def test_a_quad_without_area_is_degenerate(self):
        flat = predict_hud_footprint(view_record(eye_to_head=self.LEFT_EYE), replace(self.PLACEMENT, panel_width_m=0.0))
        self.assertEqual((flat["status"], flat["in_frame_polygon"], flat["in_frame_bounds"]), ("degenerate", [], None))

    def test_right_eye_and_unsupported_views(self):
        record = view_record(mode="right")
        record["eyes"]["right"]["eye_to_head"] = [[1.0, 0.0, 0.0, 0.05], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        # Right eye 5 cm right: eye-space x -0.2..0.2, u 80..120.
        right = predict_hud_footprint(record, self.PLACEMENT)
        self.assertEqual((right["eye"], right["in_frame_bounds"]), ("right", [80, 110, 120, 130]))
        for record, status in ((view_record(mode="both"), "unsupported_view_mode"),
                               (view_record(cropped=True), "unsupported_cropped_view")):
            with self.subTest(status=status):
                prediction = predict_hud_footprint(record, self.PLACEMENT)
                self.assertEqual((prediction["status"], prediction["projected_polygon"]), (status, []))

    def test_malformed_view_records_are_rejected(self):
        changes = (lambda r: r.update(report_kind="observer"), lambda r: r["headset_view"].update(mode="up"),
                   lambda r: r["headset_view"].update(width=0), lambda r: r["headset_view"].update(cropped=None),
                   lambda r: r["eyes"].pop("left"),
                   lambda r: r["eyes"]["left"].update(eye_to_head=[[2.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]),
                   lambda r: r["eyes"]["left"].update(eye_to_head=[[1.0, 0, 0]]),
                   lambda r: r["eyes"]["left"]["projection_raw"].update(top=None))
        for index, change in enumerate(changes):
            record = view_record()
            change(record)
            with self.subTest(change=index), self.assertRaises(ValueError):
                predict_hud_footprint(record, self.PLACEMENT)

    def test_prediction_is_labelled_and_never_feeds_qualification(self):
        prediction = predict_hud_footprint(view_record(), HUD_CONFIG)
        self.assertEqual(prediction["kind"], "geometric_prediction")
        self.assertIs(prediction["prediction_only"], True)
        self.assertIs(prediction["qualifies_mapping"], False)
        self.assertIn("cannot qualify a mapping", prediction["scope"])
        self.assertTrue(prediction["unknowns"]["mirror_carries_headset_view"].startswith("unknown"))
        record = candidate_record()
        record["evidence"].append({"role": "composition", "artifact_id": "view-prediction", "sha256": "d" * 64,
                                   "kind": "geometric_prediction"})
        with self.assertRaisesRegex(ValueError, "hud_evidence_provenance_invalid"):
            MappingCandidate.from_mapping(record)
        prediction_names = {"predict_hud_footprint", "project", "hull", "clip_to_frame", "hud_quad", "head_to_eye"}
        for module in ("qualification", "candidate", "mask", "stage", "observation", "validation", "marker"):
            tree = ast.parse((ROOT / f"sightcircuit/hud/{module}.py").read_text())
            used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            used |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
            used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertFalse(used & prediction_names, module)


class ViewPredictionCommandTests(unittest.TestCase):
    """``scripts/hud_view_prediction.py``: a saved record in, a labelled prediction out."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_command(self, *arguments):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/hud_view_prediction.py"), *arguments],
                                capture_output=True, text=True, timeout=30)
        return result, json.loads(result.stdout) if result.stdout else None

    def test_bridge_report_or_bare_record_gives_the_same_prediction(self):
        record = view_record()
        bare = self.root / "record.json"
        bare.write_text(json.dumps(record))
        wrapped = self.root / "report.json"
        wrapped.write_text(json.dumps({"report_kind": "sightcircuit_overlay_bridge", "status": "view_recorded",
                                       "view_record": record}))
        results = []
        for path in (bare, wrapped):
            result, report = self.run_command(str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((report["report_kind"], report["status"], report["exit_code"]),
                             ("hud_view_prediction", "predicted", 0))
            self.assertIs(report["mapping_qualified"], False)
            self.assertEqual(report["hud_config_sha256"], config_digest(HUD_CONFIG))
            prediction = report["prediction"]
            self.assertIs(prediction["prediction_only"], True)
            self.assertIs(prediction["qualifies_mapping"], False)
            self.assertEqual(prediction, predict_hud_footprint(record, HUD_CONFIG))
            results.append(prediction)
        self.assertEqual(results[0], results[1])

    def test_unusable_records_exit_1_and_bad_arguments_64(self):
        cases = []
        cases.append((self.root / "missing.json", "FileNotFoundError"))
        cases.append((self.root / "directory", "hud_view_record_not_regular_file"))
        (self.root / "directory").mkdir()
        big = self.root / "big.json"
        big.write_text("[" + "0," * 40000 + "0]")
        cases.append((big, "hud_view_record_too_large"))
        not_json = self.root / "text.json"
        not_json.write_text("not json")
        cases.append((not_json, None))
        list_doc = self.root / "list.json"
        list_doc.write_text("[1]")
        cases.append((list_doc, "hud_view_record_invalid"))
        no_record = self.root / "no-record.json"
        no_record.write_text(json.dumps({"report_kind": "sightcircuit_overlay_bridge", "view_record": None}))
        cases.append((no_record, "hud_view_record_missing"))
        bad_mode = self.root / "mode.json"
        bad_mode.write_text(json.dumps(view_record(mode="left")).replace('"left"', '"up"', 1))
        cases.append((bad_mode, "hud_view_record_mode_invalid"))
        for path, error in cases:
            with self.subTest(path=path.name):
                result, report = self.run_command(str(path))
                self.assertEqual((result.returncode, report["status"], report["exit_code"]), (1, "record_unusable", 1))
                self.assertIs(report["mapping_qualified"], False)
                if error:
                    self.assertEqual(report["error"], error)
        for arguments in ((), ("a", "b")):
            with self.subTest(arguments=arguments):
                result, report = self.run_command(*arguments)
                self.assertEqual((result.returncode, report), (2, None))

    def test_unsupported_view_is_still_a_prediction_report(self):
        path = self.root / "both.json"
        path.write_text(json.dumps(view_record(mode="both")))
        result, report = self.run_command(str(path))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["prediction"]["status"], "unsupported_view_mode")


if __name__ == "__main__":
    unittest.main()
