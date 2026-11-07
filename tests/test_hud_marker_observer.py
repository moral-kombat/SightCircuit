"""Synthetic native-resolution marker copies; no device or output action.

Session-token tests use a fake /proc root (``tests.test_mirror_session.FakeProc``)
and hand-written tracking events; no test reads the real /proc of any process.
"""
import contextlib
from contextlib import ExitStack
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import queue
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

try:
    import cv2
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for HUD marker observer tests") from error
import numpy as np

from sightcircuit.hud import observation as hud_observation
from scripts import hud_marker_observer as observer
from sightcircuit.hud import CaptureFormat, HUD_CONFIG
from sightcircuit.hud.contract import SESSION_TOKENS, token_valid
from sightcircuit.sources import mirror as mirror_check
from sightcircuit.sources import session as session_tokens
from sightcircuit.overlay import DEFAULT_BRIDGE, bridge_closed_cleanly
from tests.test_mirror_session import PID, STATE, FakeProc

ROOT = Path(__file__).resolve().parents[1]
FULL_OPT_IN = ["--observe-calibration", "--allow-mirror-observation", "--allow-calibration-output",
               "--device", "verified-fixture"]
PROVIDERS = ("HostClock", "RuntimeProcess", "TrackingObserver", "MirrorSession")


def fake_session(proc, *events, tracking=None):
    """Every session provider on the fake /proc root ``proc``; ``events`` reach the tracking observer first."""
    tracking = session_tokens.TrackingObserver() if tracking is None else tracking
    for event in events:
        tracking.observe(event)
    return session_tokens.MirrorSession(session_tokens.HostClock(proc.root), session_tokens.RuntimeProcess(proc.root),
                                        tracking)


def clean_bridge_fixture(seconds=1):
    marker = dict(width=320, height=128, opaque_pixels=40960, corner_ids=[1, 2, 3, 4],
                  border_pixels=4, tag_size_pixels=24, tag_inset_pixels=8,
                  corner_order="top_left,top_right,bottom_left,bottom_right", config_id=HUD_CONFIG.layout_id,
                  visible_in_dashboard=True)
    marker.update({key: getattr(HUD_CONFIG, key) for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity")})
    native = dict(report_kind="sightcircuit_overlay_bridge", status="validated_marker", exit_code=0, cleanup_ok=True,
                  mode="--validate-calibration-marker", runtime_initialized=False, calibration_only=True,
                  native_started_ns=0, marker_request_ns=1, marker_submit_returned_ns=2,
                  display_deadline_ns=seconds*1000000000, cutoff_requested_ns=seconds*1000000000,
                  cleanup_returned_ns=seconds*1000000000+1, physical_display_recovery_verified=False,
                  input_frames_accepted=False, capture_started=False, inference_started=False, images_saved=False,
                  marker=marker, overlay_shown_event_seen=False, overlay_hidden_event_seen=False,
                  visibility_after_submit={"scope": "runtime_queries_not_physical_visibility", "sampled": False,
                                           "api_visible": None, "dashboard_visible": None, "visible_in_dashboard": None})
    return dict(validation_only=True, worker_reaped=True, worker_exit_code=0, transport_threads_stopped=True,
                forced_termination=False, error=None, native_report=native)


def scene(positions=((100, 100),), *, width=1000, height=600, template=None):
    image = np.full((height, width, 3), 90, np.uint8)
    template = hud_observation.marker_template() if template is None else template
    for x, y in positions:
        image[y:y+128, x:x+320] = template[:, :, None]
    return image


def tag_mismatch_template():
    """The generated marker with the top-left tag's four code bits inverted.

    Its rejection matches V4's recorded shape (E75): no complete copy, orphan
    fragments and failed tag-pattern/code-bit checks. It is not a
    reconstruction of V4's unsaved image.
    """
    template = hud_observation.marker_template()
    for bit in range(4):
        x, y = 8 + 4 + (bit % 2) * 8, 8 + 4 + (bit // 2) * 8
        template[y:y+6, x:x+6] = 255 - template[y:y+6, x:x+6]
    return template


class MarkerGeometryTests(unittest.TestCase):
    def test_all_complete_copies_are_found_without_eye_assignment(self):
        for positions in (((100, 100),), ((70, 80), (570, 270)), ((100, 50), (100, 330))):
            with self.subTest(positions=positions):
                copies = hud_observation.locate_copies(scene(positions))
                self.assertEqual(len(copies), len(positions))
                for quad, (x, y) in zip(copies, sorted(positions)):
                    np.testing.assert_allclose(quad, [[x, y], [x+319, y], [x+319, y+127], [x, y+127]], atol=1)

    def test_perspective_and_rotated_marker_keep_native_corners(self):
        source = np.float32([[0, 0], [319, 0], [319, 127], [0, 127]])
        for target in (np.float32([[100, 100], [450, 110], [430, 270], [110, 250]]),
                       np.float32([[380, 260], [61, 260], [61, 133], [380, 133]])):
            with self.subTest(target=target.tolist()):
                warped = cv2.warpPerspective(hud_observation.marker_template(), cv2.getPerspectiveTransform(source, target),
                                             (800, 500), borderValue=90)
                copies = hud_observation.locate_copies(cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR))
                self.assertEqual(len(copies), 1)
                np.testing.assert_allclose(copies[0], target, atol=2)

    def test_resolvable_scaled_copies_at_native_1080p_and_unresolvable_codes(self):
        for scale in (.25, .4, .5, .75):
            with self.subTest(scale=scale):
                tile = cv2.resize(hud_observation.marker_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image = np.full((1080, 1920, 3), 90, np.uint8)
                for x, y in ((100, 100), (1400, 700)):
                    image[y:y+h, x:x+w] = tile[:, :, None]
                if scale >= .5:
                    copies = hud_observation.locate_copies(image)
                    self.assertEqual(len(copies), 2)
                    np.testing.assert_allclose(copies[1], [[1400, 700], [1400+w-1, 700],
                                               [1400+w-1, 700+h-1], [1400, 700+h-1]], atol=1)
                else:
                    with self.assertRaises(ValueError):
                        hud_observation.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1,
                                                               config_id=HUD_CONFIG.layout_id)

    def test_translucent_or_blurred_marker_does_not_establish_full_footprint(self):
        original = scene()
        for image in ((original.astype(np.float32)*.5 + 45).astype(np.uint8),
                      cv2.GaussianBlur(original, (15, 15), 0)):
            with self.assertRaises(ValueError):
                hud_observation.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1,
                                                        config_id=HUD_CONFIG.layout_id)

    def test_resolvable_partial_scaled_copy_cannot_hide_beside_complete_copy(self):
        for scale in (.5, .75, 1.):
            with self.subTest(scale=scale):
                image = scene()
                tile = cv2.resize(hud_observation.marker_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image[300:300+h, 600:600+w] = tile[:, :, None]
                image[295:300+int(40*scale), 595:600+int(40*scale)] = 90
                with self.assertRaisesRegex(ValueError, "orphan"):
                    hud_observation.locate_copies(image)

    def test_missing_corner_corrupt_code_and_partial_additional_copy_reject(self):
        for mode in ("missing", "wrong_code", "partial_extra"):
            with self.subTest(mode=mode):
                image = scene()
                if mode == "missing":
                    image[95:140, 95:140] = 90
                elif mode == "wrong_code":
                    image[100+8+4:100+8+10, 100+8+4:100+8+10] = 0
                else:
                    image = scene(((70, 80), (570, 270)))
                    image[260:320, 560:620] = 90
                with self.assertRaises(ValueError):
                    observation = hud_observation.EmpiricalObservation()
                    observation.accept(image, CaptureFormat.bgr(image), 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_no_marker_is_not_mapping_evidence(self):
        image = np.full((600, 1000, 3), 90, np.uint8)
        self.assertEqual(hud_observation.locate_copies(image), [])
        with self.assertRaisesRegex(ValueError, "no_complete_marker"):
            hud_observation.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_empirical_union_and_margin_include_small_movement(self):
        track = hud_observation.EmpiricalObservation()
        for i, offset in enumerate((0, 2, -2), 1):
            image = scene(((100+offset, 100), (600, 300)))
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
        evidence = track.evidence()
        self.assertEqual(evidence["footprint_union_native"], [[82, 84, 438, 244], [584, 284, 936, 444]])
        self.assertEqual(evidence["maximum_corner_drift_px"], 2)
        for key in ("eye_identity_assigned", "composition_verified", "unseen_or_occluded_copies_established", "mapping_qualified"):
            self.assertFalse(evidence[key])

    def test_vertical_copy_assignment_survives_small_horizontal_jitter(self):
        track = hud_observation.EmpiricalObservation()
        for i, positions in enumerate((((100, 50), (100, 330)), ((102, 50), (99, 330))), 1):
            image = scene(positions)
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
        self.assertEqual(track.evidence()["observed_copy_count"], 2)

    def test_large_movement_count_change_and_margin_escape_fail_closed(self):
        first = scene()
        for changed, reason in ((scene(((120, 100),)), "unstable"),
                                (scene(((100, 100), (600, 300))), "count_changed")):
            track = hud_observation.EmpiricalObservation()
            track.accept(first, CaptureFormat.bgr(first), 1, 1, config_id=HUD_CONFIG.layout_id)
            with self.assertRaisesRegex(ValueError, reason):
                track.accept(changed, CaptureFormat.bgr(changed), 2, 2, config_id=HUD_CONFIG.layout_id)
        clipped_margin = scene(((8, 8),))
        with self.assertRaisesRegex(ValueError, "margin_escapes"):
            hud_observation.EmpiricalObservation().accept(clipped_margin, CaptureFormat.bgr(clipped_margin), 1, 1,
                                                    config_id=HUD_CONFIG.layout_id)

    def test_format_config_sequence_and_declared_copy_count_mismatches_reject(self):
        image = scene()
        descriptor = CaptureFormat.bgr(image)
        track = hud_observation.EmpiricalObservation()
        track.accept(image, descriptor, 1, 1, config_id=HUD_CONFIG.layout_id)
        for fmt, sequence, config_id in ((replace(descriptor, fourcc="RGB3"), 2, HUD_CONFIG.layout_id),
                                         (descriptor, 1, HUD_CONFIG.layout_id), (descriptor, 2, "changed")):
            with self.subTest(fmt=fmt, sequence=sequence, config=config_id), self.assertRaises(ValueError):
                track.accept(image, fmt, sequence, 2, config_id=config_id)
        with self.assertRaisesRegex(ValueError, "expected_copy_count"):
            hud_observation.EmpiricalObservation(hud_observation.ObserverConfig(expected_copies=2)).accept(
                image, descriptor, 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_skipped_read_between_marker_samples_rejects(self):
        # Reads are numbered consecutively, so a gap is a dropped frame (G4 method step 3).
        image = scene()
        descriptor = CaptureFormat.bgr(image)
        track = hud_observation.EmpiricalObservation()
        track.accept(image, descriptor, 3, 1, config_id=HUD_CONFIG.layout_id)
        track.accept(image, descriptor, 4, 2, config_id=HUD_CONFIG.layout_id)
        with self.assertRaisesRegex(ValueError, "observer_sequence_gap"):
            track.accept(image, descriptor, 6, 3, config_id=HUD_CONFIG.layout_id)
        evidence = track.evidence()
        self.assertEqual([sample[0] for sample in evidence["samples"]], [3, 4])
        self.assertEqual(evidence["last_detected_corners"], [])

    def test_native_rgb_stride_conversion_preserves_pixel_geometry(self):
        image = scene()
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        storage = np.zeros((600, 3008), np.uint8)
        storage[:, :3000] = rgb.reshape(600, 3000)
        fmt = dict(width=1000, height=600, fourcc="RGB3", stride_bytes=3008,
                   minimum_buffer_bytes=600*3008, buffer_bytes=600*3008)
        prepared, _ = hud_observation.native_bgr(storage.tobytes(), fmt)
        np.testing.assert_array_equal(prepared, image)
        self.assertEqual(len(hud_observation.locate_copies(prepared)), 1)


class FakeClock:
    now = 0
    def tick(self, seconds):
        self.now = round(self.now + max(seconds, .000001), 6)
    def monotonic(self):
        return self.now
    def monotonic_ns(self):
        return int(self.now * 1e9)


class MarkerDiagnosticTests(unittest.TestCase):
    def test_reported_geometry_across_subpixel_phases_has_conservative_bounds(self):
        source = np.float32([[0, 0], [319, 0], [319, 127], [0, 127]])
        # Coordinates from the metadata-only Frame report; all pixels below
        # are generated locally, not a reconstruction of a saved capture.
        target = np.float32([[1309, 731], [1582, 683], [1601, 791], [1329, 839]])
        track = hud_observation.EmpiricalObservation()
        refined = False
        for i, phase in enumerate((0, .25, .5, .75), 1):
            gray = cv2.warpPerspective(hud_observation.marker_template(), cv2.getPerspectiveTransform(source, target+phase),
                                       (1920, 1080), borderValue=90)
            image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
            np.testing.assert_allclose(track.last_corners[0], target+phase, atol=2)
            details = track.last_match_diagnostics
            refined |= details["refined_outline_count"] > 0
            for candidate in details["best_candidates"]:
                self.assertLessEqual(candidate["refinement_shift_px"], hud_observation.MAX_CORNER_REFINEMENT_PX)
            x0, y0, x1, y1 = track.regions()[0]
            self.assertLessEqual(x0, (target+phase)[:, 0].min()-8)
            self.assertLessEqual(y0, (target+phase)[:, 1].min()-8)
            self.assertGreaterEqual(x1, (target+phase)[:, 0].max()+8)
            self.assertGreaterEqual(y1, (target+phase)[:, 1].max()+8)
        self.assertTrue(refined)
        self.assertFalse(track.evidence()["mapping_qualified"])

    def test_excessive_corner_adjustment_and_each_corrupted_bit_remain_rejected(self):
        source = np.float32([[0, 0], [319, 0], [319, 127], [0, 127]])
        target = np.float32([[1309, 731], [1582, 683], [1601, 791], [1329, 839]]) + .5
        gray = cv2.warpPerspective(hud_observation.marker_template(), cv2.getPerspectiveTransform(source, target),
                                   (1920, 1080), borderValue=90)
        with patch.object(cv2, "cornerSubPix", side_effect=lambda image, points, *args: points+100), \
                self.assertRaisesRegex(ValueError, "orphan"):
            hud_observation.locate_copies(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        for tag in range(4):
            for bit in range(4):
                with self.subTest(tag=tag, bit=bit):
                    template = hud_observation.marker_template()
                    x = (288 if tag % 2 else 8) + 4+(bit % 2)*8
                    y = (96 if tag // 2 else 8) + 4+(bit // 2)*8
                    template[y:y+6, x:x+6] = 255-template[y:y+6, x:x+6]
                    gray = cv2.warpPerspective(template, cv2.getPerspectiveTransform(source, target),
                                               (1920, 1080), borderValue=90)
                    with self.assertRaises(ValueError):
                        hud_observation.locate_copies(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))

    def test_same_orphan_error_has_distinct_photometric_resolution_and_outline_evidence(self):
        # A marker on a white block (a white frame is now refused as saturated; see below).
        cases = (("grey", 1, .4545, {"whole_pattern_error"}),
                 ("grey", .4, 1, {"outline_brightness", "tag_pattern_error", "code_bit_error"}),
                 ("white_block", 1, 1, {"whole_pattern_error", "outline_brightness", "tag_pattern_error",
                                        "code_bit_error"}))
        for surround, scale, gamma, failed in cases:
            with self.subTest(surround=surround, scale=scale, gamma=gamma):
                template = np.round(255*(hud_observation.marker_template().astype(np.float32)/255)**gamma).astype(np.uint8)
                tile = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image = np.full((1080, 1920, 3), 90, np.uint8)
                if surround == "white_block":
                    image[300:700, 600:1300] = 255
                image[400:400+h, 700:700+w] = tile[:, :, None]
                details = {}
                with self.assertRaisesRegex(ValueError, "orphan_marker_tag_or_incomplete_copy"):
                    hud_observation.locate_copies(image, diagnostics=details)
                self.assertTrue(details["scan_complete"])
                self.assertEqual(details["complete_copy_count"], 0)
                self.assertGreater(details["orphan_fragment_count"], 0)
                self.assertEqual(set(details["best_candidates"][0]["failed_checks"]), failed)
                self.assertEqual((details["width"], details["height"]), (1920, 1080))
                self.assertEqual(details["coordinate_space"], "native_capture_pixels")
                self.assertLess(len(json.dumps(details, allow_nan=False)), 8192)
        white = np.full((1080, 1920, 3), 255, np.uint8)
        white[400:528, 700:1020] = hud_observation.marker_template()[:, :, None]
        with self.assertRaisesRegex(ValueError, "^observer_bright_saturation$"):
            hud_observation.locate_copies(white)

    def test_complete_copy_beside_partial_copy_is_retained_only_as_rejected_candidate(self):
        image = scene(((70, 80), (570, 270)))
        image[260:320, 560:620] = 90
        details = {}
        with self.assertRaisesRegex(ValueError, "orphan"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["complete_copy_count"], 1)
        self.assertEqual(details["status"], "rejected")
        self.assertEqual(details["fragment_quads_scope"], "orphans")
        self.assertTrue(all(quad[0][0] >= 570 for quad in details["fragment_quads_native"]))
        np.testing.assert_allclose(details["complete_copy_corners_native"][0],
                                   [[70, 80], [389, 80], [389, 207], [70, 207]])

    def test_candidate_and_fragment_metadata_are_capped_without_dropping_total_counts(self):
        image = np.full((600, 1000, 3), 90, np.uint8)
        tag = hud_observation.marker_template()[8:32, 8:32]
        for i in range(24):
            x, y = 50+(i%8)*50, 50+(i//8)*50
            image[y:y+24, x:x+24] = tag[:, :, None]
        for i in range(8):
            x, y = 50+(i%4)*180, 250+(i//4)*100
            cv2.rectangle(image, (x, y), (x+120, y+50), (255, 255, 255), 4)
        details = {}
        with self.assertRaisesRegex(ValueError, "orphan"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertGreater(details["orphan_fragment_count"], hud_observation.MAX_DIAGNOSTIC_FRAGMENTS)
        self.assertEqual(len(details["fragment_quads_native"]), hud_observation.MAX_DIAGNOSTIC_FRAGMENTS)
        self.assertTrue(details["fragment_quads_truncated"])
        self.assertGreater(details["eligible_outline_count"], hud_observation.MAX_DIAGNOSTIC_CANDIDATES)
        self.assertEqual(len(details["best_candidates"]), hud_observation.MAX_DIAGNOSTIC_CANDIDATES)
        self.assertTrue(details["best_candidates_truncated"])
        self.assertLess(len(json.dumps(details, allow_nan=False)), 8192)

    def test_matching_or_context_failure_clears_stale_corners_and_records_frame_sequence(self):
        first = scene()
        failed = scene()
        failed[95:140, 95:140] = 90
        for image, descriptor in ((failed, CaptureFormat.bgr(failed)),
                                  (first, replace(CaptureFormat.bgr(first), fourcc="RGB3"))):
            track = hud_observation.EmpiricalObservation()
            track.accept(first, CaptureFormat.bgr(first), 1, 100, config_id=HUD_CONFIG.layout_id)
            with self.assertRaises(ValueError):
                track.accept(image, descriptor, 2, 200, config_id=HUD_CONFIG.layout_id)
            evidence = track.evidence()
            self.assertEqual(evidence["last_detected_corners"], [])
            self.assertEqual(evidence["last_match_diagnostics"]["sequence"], 2)
            self.assertEqual(evidence["last_match_diagnostics"]["observed_ns"], 200)
            self.assertEqual(evidence["sample_count"], 1)
            self.assertFalse(evidence["mapping_qualified"])

    def test_contour_budget_reject_reports_incomplete_scan_without_unbounded_metadata(self):
        # 5000 bright 1 x 7 lines: too many contours, though none is a speck or a quad.
        image = np.full((600, 1000, 3), 90, np.uint8)
        for index in range(5000):
            y, x = 1 + (index // 332)*9, 1 + (index % 332)*3
            image[y:y+7, x] = 255
        details = {}
        with self.assertRaisesRegex(ValueError, "^observer_contour_budget$"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertFalse(details["scan_complete"])
        self.assertEqual(details["contour_count"], 5000)
        self.assertGreater(details["contour_count"], hud_observation.MAX_CONTOURS)
        self.assertEqual(details["best_candidates"], [])
        # The scan metadata stays small; the frame's statistics are a fixed set of counts beside it.
        statistics = details.pop("scene_statistics")
        self.assertLess(len(json.dumps(details)), 1024)
        self.assertLess(len(json.dumps(statistics)), 2048)  # schema 2: a fixed set of counts and budgets
        self.assertEqual(statistics["contour_count"], details["contour_count"])


class FakeStop:
    def __init__(self, clock):
        self.clock, self.stopped = clock, False
    def is_set(self):
        return self.stopped
    def set(self):
        self.stopped = True
    def wait(self, seconds):
        self.clock.tick(seconds)
        return self.stopped


class BaselineGateTests(unittest.TestCase):
    def clear_image(self, value=90):
        image = scene(())
        image[0, 0] = value
        return image

    def test_cached_marker_then_two_distinct_clear_frames_only_establishes_content_change(self):
        gate = hud_observation.BaselineGate(2)
        self.assertFalse(gate.accept(scene(), 1, 1, 0))
        self.assertFalse(gate.accept(scene(), 2, 2, .2))
        self.assertFalse(gate.accept(self.clear_image(91), 3, 3, .4))
        self.assertTrue(gate.accept(self.clear_image(92), 4, 4, .6))
        self.assertEqual(gate.report["marker_like_frames"], 2)
        self.assertEqual(gate.report["repeated_frames"], 1)
        self.assertEqual(gate.report["content_changes"], 2)
        self.assertFalse(gate.report["camera_freshness_verified"])
        self.assertEqual(gate.initial_diagnostics["complete_copy_count"], 1)
        self.assertEqual(gate.last_diagnostics["status"], "no_complete_copy")

    def test_repeated_clear_frames_and_exact_deadline_cannot_start_output(self):
        gate = hud_observation.BaselineGate(2)
        for i in range(10):
            self.assertFalse(gate.accept(self.clear_image(), i+1, i+1, i*.2))
        with self.assertRaisesRegex(ValueError, "baseline_distinct_clear"):
            gate.accept(self.clear_image(91), 11, 11, 2)
        self.assertEqual(gate.report["content_changes"], 0)
        self.assertFalse(gate.report["established"])

    def test_partial_marker_resets_clear_sequence_and_metadata_budgets_fail_immediately(self):
        gate = hud_observation.BaselineGate(2)
        partial = scene()
        partial[95:140, 95:140] = 90
        self.assertFalse(gate.accept(self.clear_image(91), 1, 1, 0))
        self.assertFalse(gate.accept(partial, 2, 2, .2))
        self.assertFalse(gate.accept(self.clear_image(92), 3, 3, .4))
        self.assertTrue(gate.accept(self.clear_image(93), 4, 4, .6))
        with patch.object(hud_observation, "locate_copies", side_effect=ValueError("observer_contour_budget")), \
                self.assertRaisesRegex(ValueError, "contour_budget"):
            hud_observation.BaselineGate(2).accept(self.clear_image(), 1, 1, 0)


class ObserverFixture:
    def run_fixture(self, *, seconds=1, cancel_at=None, bad_format=False, slow=False, close_failure=False,
                    bridge_change=None, baseline_slow=False, incomplete_marker=False, baseline_mode=None,
                    session=None, clock=None, log=None, on_read=None, on_marker=None, frame=None):
        """One ``observe`` run on generated frames; ``session`` is passed only when given.

        ``log`` records "backend" when the fixture backend is constructed and "frame"
        for each read; ``on_read(number)`` runs before a read's frame is generated
        and ``on_marker()`` when the marker starts. ``frame(number, marker_shown)``
        may return a BGR image that replaces the generated frame of that read.
        """
        clock = FakeClock() if clock is None else clock
        stop = FakeStop(clock)
        state = {"marker": False, "source_closed": False, "reads": [], "opened": False, "constructed": False}
        class Source:
            kind = "synthetic"
            def __init__(self, candidate):
                state["constructed"] = True
                if log is not None:
                    log.append("backend")
                self.capabilities = {"driver": "synthetic"}
                self.format = dict(width=1000, height=600, fourcc="BGR3", stride_bytes=3000,
                                   minimum_buffer_bytes=1800000, buffer_bytes=1800000)
            def open(self):
                state["opened"] = True
            def read_frame(self, timeout):
                state["reads"].append(clock.now)
                if log is not None:
                    log.append("frame")
                if on_read is not None:
                    on_read(len(state["reads"]))
                if cancel_at is not None and clock.now >= cancel_at:
                    stop.set()
                if bad_format and state["marker"]:
                    self.format["colorspace_code"] = 1
                if baseline_mode == "format_change" and len(state["reads"]) > 1:
                    self.format["colorspace_code"] = 1
                if baseline_mode == "no_frames":
                    return None
                cached = baseline_mode == "persistent_marker" or (
                    baseline_mode == "cached_then_clear" and len(state["reads"]) <= 2)
                image = scene(((100, 100),) if state["marker"] or cached else ())
                if baseline_mode != "static_clear":
                    image[0, 0] = len(state["reads"]) % 100
                if incomplete_marker and state["marker"]:
                    image[95:140, 95:140] = 90
                if frame is not None:
                    replacement = frame(len(state["reads"]), state["marker"])
                    if replacement is not None:
                        image = replacement
                return image.tobytes()
            def close(self):
                state["source_closed"] = True
                if close_failure:
                    raise OSError("synthetic_close_failed")
        class Marker:
            validation_only = True
            process = None
            def start_marker(self, seconds, stop, **kwargs):
                state["marker"] = True
                if on_marker is not None:
                    on_marker()
                self.end = clock.now + seconds
                self.process = Mock()
                self.process.poll.side_effect = lambda: 0 if clock.now >= self.end else None
            def close(self):
                state["marker_closed"] = True
                report = clean_bridge_fixture(seconds)
                if bridge_change:
                    bridge_change(report)
                return report
        original = hud_observation.locate_copies
        def locate(image, **kwargs):
            result = original(image, **kwargs)
            if (slow and state["marker"]) or (baseline_slow and not state["marker"]):
                clock.tick(2)
            return result
        options = {} if session is None else {"session": session}
        with patch.object(observer.time, "monotonic", clock.monotonic), \
                patch.object(observer.time, "monotonic_ns", clock.monotonic_ns), \
                patch.object(hud_observation, "locate_copies", side_effect=locate):
            report = observer.observe({"node": "synthetic:fixture"}, seconds, stop, backend_factory=Source,
                                      marker_client=Marker(), **options)
        return report, state, clock


def speckled(height=600, width=1000, step=10, size=2, value=255):
    """A clear grey frame with a regular grid of small bright specks, like sensor noise."""
    image = np.full((height, width, 3), 90, np.uint8)
    for y in range(5, height - 5, step):
        for x in range(5, width - 5, step):
            image[y:y+size, x:x+size] = value
    return image


class SceneRejectionReportTests(ObserverFixture, unittest.TestCase):
    """A run stopped by the scene precondition reports that frame's statistics, never its pixels."""

    def test_baseline_speck_density_rejection_carries_the_frame_statistics(self):
        noisy = speckled()
        noisy[0, 0] = 7  # Distinct from the fixture's own clear frames.
        report, state, _ = self.run_fixture(seconds=5, frame=lambda number, shown: noisy)
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("rejected", 1, "observer_speck_density"))
        self.assertFalse(state["marker"])  # Rejected before any marker was shown.
        statistics = report["scene_statistics"]
        self.assertEqual(statistics, hud_observation.scene_statistics(noisy))
        self.assertEqual(statistics["census"]["specks"], 59 * 99)
        self.assertEqual(statistics["contour_count"], 59 * 99)
        self.assertEqual(statistics["contour_area_bands"]["below_25"], 59 * 99)
        self.assertEqual(statistics["precondition"]["rejection"], "observer_speck_density")
        self.assertEqual(report["scene_statistics_source"], {"phase": "baseline", "sequence": 1})
        # Written once: the statistics moved out of the phase's match diagnostics.
        self.assertNotIn("scene_statistics", report["baseline_match_diagnostics"])
        self.assertEqual(report["baseline_match_diagnostics"]["reason"], "observer_speck_density")
        self.assertFalse(report["images_saved"])
        self.assertLess(len(json.dumps(statistics)), 2048)
        observer.validate_worker_report(report, 1)

    def test_marker_phase_quad_budget_rejection_names_its_phase_and_read(self):
        squares = speckled(step=20, size=10)  # 30 x 50 = 1500 bright 10 x 10 quads.
        report, state, _ = self.run_fixture(seconds=5, frame=lambda number, shown: squares if shown else None)
        self.assertTrue(state["marker"])
        self.assertEqual((report["status"], report["reason"]), ("rejected", "observer_quad_budget"))
        statistics = report["scene_statistics"]
        self.assertEqual(statistics["quad_count"], 1500)  # Complete, though the observer stopped at 257.
        self.assertEqual(report["last_match_diagnostics"]["quad_count"], hud_observation.MAX_QUADS + 1)
        self.assertEqual(report["scene_statistics_source"]["phase"], "marker")
        self.assertEqual(report["scene_statistics_source"]["sequence"], report["last_match_diagnostics"]["sequence"])
        observer.validate_worker_report(report, 1)

    def test_other_outcomes_carry_no_scene_statistics(self):
        for options, status in (({}, "observed_unqualified"), (dict(incomplete_marker=True), "rejected"),
                                (dict(seconds=5, baseline_mode="static_clear"), "rejected"),
                                (dict(cancel_at=.4), "cancelled")):
            with self.subTest(options=options):
                report, _, _ = self.run_fixture(**options)
                self.assertEqual(report["status"], status)
                self.assertNotIn("scene_statistics", report)
                self.assertNotIn("scene_statistics_source", report)
                observer.validate_worker_report(report, report["exit_code"])

    def test_a_later_close_failure_keeps_the_statistics_out_and_the_report_valid(self):
        # capture() replaces the budget error with the close error, so the reasons differ.
        noisy = speckled()
        report, _, _ = self.run_fixture(seconds=5, frame=lambda number, shown: noisy, close_failure=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertNotEqual(report["reason"], "observer_speck_density")
        self.assertNotIn("scene_statistics", report)
        self.assertNotIn("scene_statistics_source", report)
        observer.validate_worker_report(report, 1)

    def test_a_stop_while_the_statistics_run_cancels_without_them(self):
        noisy = speckled()
        stops = []
        original = hud_observation.scene_statistics
        def stop_midway(*args, **kwargs):
            statistics = original(*args, **kwargs)
            stops[0].set()  # SIGTERM arriving while the statistics are computed
            return statistics
        clock = FakeClock()
        real_fake_stop = FakeStop
        def capturing_stop(fake_clock):
            stop = real_fake_stop(fake_clock)
            stops.append(stop)
            return stop
        with patch(f"{__name__}.FakeStop", side_effect=capturing_stop), \
                patch.object(hud_observation, "scene_statistics", side_effect=stop_midway):
            report, _, _ = self.run_fixture(seconds=5, clock=clock, frame=lambda number, shown: noisy)
        self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 130))
        self.assertNotIn("scene_statistics", report)
        observer.validate_worker_report(report, 130)

    def test_contract_accepts_only_a_consistent_rejection_with_statistics(self):
        noisy = speckled()
        report, _, _ = self.run_fixture(seconds=5, frame=lambda number, shown: noisy)
        observer.validate_worker_report(report, 1)
        def changed(edit):
            altered = json.loads(json.dumps(report))
            edit(altered)
            return altered
        bad = (
            lambda r: r.update(reason="orphan_marker_tag_or_incomplete_copy"),  # Statistics name another rejection.
            lambda r: r.pop("scene_statistics_source"),
            lambda r: r.pop("scene_statistics"),
            lambda r: r["scene_statistics_source"].update(phase="detector"),
            lambda r: r["scene_statistics_source"].update(sequence=-1),
            lambda r: r["scene_statistics_source"].update(extra=1),
            lambda r: r["scene_statistics"].update(pixels_bgr=[1, 2, 3]),  # No pixel data, ever.
            lambda r: r["scene_statistics"].update(contour_count=10),  # Bands no longer sum to the count.
            lambda r: r["scene_statistics"]["contour_area_bands"].update(below_25=-1),
            lambda r: r["scene_statistics"]["precondition"].update(rejection=None),
            lambda r: r["scene_statistics"]["budgets"].update(max_contours=99999),
            lambda r: r["scene_statistics"]["gray_percentiles"].update(p50=256),
            lambda r: r["scene_statistics"]["gray_percentiles"].update(p50=200, p90=100),
            lambda r: r["scene_statistics"].update(bright_pixel_fraction=True),
            lambda r: r["scene_statistics"].update(bright_pixel_fraction=1.5),
            lambda r: r["scene_statistics"].update(width=1),
        )
        for index, edit in enumerate(bad):
            with self.subTest(change=index), self.assertRaises(ValueError):
                observer.validate_worker_report(changed(edit), 1)
        # Statistics in a report that was not rejected at the precondition are refused too.
        clean, _, _ = self.run_fixture()
        clean.update(scene_statistics=report["scene_statistics"],
                     scene_statistics_source=report["scene_statistics_source"])
        with self.assertRaises(ValueError):
            observer.validate_worker_report(clean, 2)
        # Each clause on its own: not rejected, the wrong exit code, or statistics naming no rejection.
        quiet = hud_observation.scene_statistics(np.full((600, 1000, 3), 90, np.uint8))
        for edit, code in ((lambda r: r.update(status="report_budget_exceeded"), 1),
                           (lambda r: r.update(exit_code=130), 130),
                           (lambda r: (r.pop("reason"), r.update(scene_statistics=quiet)), 1),
                           (lambda r: r.update(reason=None, scene_statistics=quiet), 1)):
            with self.subTest(code=code), self.assertRaises(ValueError):
                observer.validate_worker_report(changed(edit), code)


class ObserverLifecycleTests(ObserverFixture, unittest.TestCase):
    def test_combined_baseline_marker_observation_cleanup_and_five_fps(self):
        report, state, clock = self.run_fixture()
        self.assertEqual(report["status"], "observed_unqualified", report)
        self.assertEqual(report["exit_code"], 2)
        self.assertTrue(report["cleanup_ok"])
        self.assertTrue(state["source_closed"])
        self.assertLessEqual(len(state["reads"]), 5)
        self.assertTrue(all(b-a >= .199 for a, b in zip(state["reads"], state["reads"][1:])))
        self.assertEqual(report["sample_count"], 3)
        self.assertFalse(report["mapping_qualified"])
        self.assertFalse(report["images_saved"])
        self.assertFalse(report["inference_started"])
        observer.validate_worker_report(report, 2)

    def test_cached_baseline_is_bounded_and_never_launches_on_persistent_or_missing_evidence(self):
        for mode in ("persistent_marker", "static_clear", "no_frames", "format_change"):
            with self.subTest(mode=mode):
                report, state, clock = self.run_fixture(seconds=5, baseline_mode=mode)
                self.assertEqual(report["status"], "rejected", report)
                self.assertFalse(state["marker"])
                self.assertTrue(state["source_closed"])
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["baseline"]["established"])
                self.assertFalse(report["camera_freshness_verified"])
                self.assertLess(clock.now, 2.1)

    def test_cancellation_during_cached_baseline_and_later_clear_transition(self):
        report, state, _ = self.run_fixture(seconds=5, baseline_mode="persistent_marker", cancel_at=.4)
        self.assertEqual(report["status"], "cancelled")
        self.assertFalse(state["marker"])
        self.assertTrue(report["cleanup_ok"])
        report, state, _ = self.run_fixture(seconds=5, baseline_mode="cached_then_clear")
        self.assertEqual(report["status"], "observed_unqualified", report)
        self.assertEqual(report["baseline_sequence"], 4)
        self.assertEqual(report["baseline"]["marker_like_frames"], 2)
        self.assertTrue(report["baseline"]["established"])
        self.assertFalse(report["camera_freshness_verified"])
        observer.validate_worker_report(report, 2)

    def test_rejected_trial_retains_baseline_and_failure_metadata_without_images_or_approval(self):
        report, state, _ = self.run_fixture(incomplete_marker=True)
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["reason"], "orphan_marker_tag_or_incomplete_copy")
        self.assertTrue(state["source_closed"])
        self.assertEqual(report["baseline_match_diagnostics"]["status"], "no_complete_copy")
        self.assertEqual(report["baseline_match_diagnostics"]["sequence"], 2)
        self.assertEqual(report["last_match_diagnostics"]["status"], "rejected")
        self.assertEqual(report["last_match_diagnostics"]["sequence"], 3)
        self.assertGreater(report["last_match_diagnostics"]["orphan_fragment_count"], 0)
        self.assertEqual(report["sample_count"], 0)
        for key in ("images_saved", "inference_started", "mapping_qualified", "production_live_input_enabled",
                    "production_hud_enabled", "physical_display_recovery_verified"):
            self.assertFalse(report[key])
        self.assertLess(len(json.dumps(report, allow_nan=False)), 128 * 1024)
        observer.validate_worker_report(report, 1)

    def test_cancel_format_change_and_processing_overrun_remain_unqualified(self):
        for options, status in ((dict(cancel_at=.4), "cancelled"), (dict(bad_format=True), "rejected"),
                                (dict(slow=True), "rejected")):
            with self.subTest(options=options):
                report, state, _ = self.run_fixture(**options)
                self.assertEqual(report["status"], status, report)
                self.assertTrue(state["source_closed"])
                self.assertFalse(report["mapping_qualified"])
        self.assertIn("deadline", report["reason"])

    def test_default_missing_opt_ins_and_api_live_calls_never_open(self):
        with patch.object(observer, "discover") as discover, patch.object(observer, "supervise") as supervised, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(observer.main([]), 3)
        discover.assert_not_called()
        supervised.assert_not_called()
        for args in (["--observe-calibration"], ["--allow-mirror-observation"], ["--seconds", "21"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                observer.main(args)
        with patch.object(mirror_check, "capture") as capture, self.assertRaises(ValueError):
            observer.observe({}, 1, threading.Event(), backend_factory=None, marker_client=Mock(validation_only=False))
        capture.assert_not_called()

    def test_capture_pacing_default_and_absolute_deadline_guard(self):
        backend = Mock()
        with patch.object(mirror_check.time, "monotonic", return_value=10):
            report, _ = mirror_check.capture({}, 20, threading.Event(), backend_factory=backend, deadline=10, max_fps=5)
        backend.assert_not_called()
        self.assertEqual(report["status"], "deadline_expired")
        with self.assertRaises(ValueError):
            mirror_check.capture({}, 1, threading.Event(), backend_factory=backend, max_fps=11)

    def test_twenty_second_observation_is_at_most_one_hundred_reads_without_wall_wait(self):
        report, state, clock = self.run_fixture(seconds=20)
        self.assertEqual(report["exit_code"], 2, report)
        self.assertEqual(len(state["reads"]), 100)
        self.assertEqual(report["sample_count"], 98)
        self.assertLessEqual(clock.now, 20.201)
        self.assertLess(len(json.dumps(report)), 128 * 1024)

    def test_report_cannot_promote_observation_to_production_approval(self):
        report, _, _ = self.run_fixture()
        for key in ("mapping_qualified", "composition_verified", "eye_identity_assigned", "production_hud_enabled",
                    "camera_freshness_verified"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                observer.validate_worker_report(dict(report, **{key: True}), 2)
        for baseline in (dict(report["baseline"], established=False),
                         dict(report["baseline"], camera_freshness_verified=True)):
            with self.assertRaisesRegex(ValueError, "baseline_evidence"):
                observer.validate_worker_report(dict(report, baseline=baseline), 2)

    def test_native_config_and_deadline_mismatch_reject_without_falsifying_cleanup(self):
        for change in (lambda b: b["native_report"]["marker"].update(x_m=0),
                       lambda b: b["native_report"]["marker"].update(width=321),
                       lambda b: b["native_report"]["marker"].update(visible_in_dashboard=False),
                       lambda b: b["native_report"].update(display_deadline_ns=21000000000),
                       lambda b: b["native_report"].update(status="stopped")):
            with self.subTest(change=change):
                report, _, _ = self.run_fixture(bridge_change=change)
                self.assertEqual(report["status"], "rejected", report)
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["mapping_qualified"])

    def test_close_failure_forced_native_and_slow_baseline_never_claim_completion(self):
        for options in (dict(close_failure=True), dict(bridge_change=lambda b: b.update(forced_termination=True)),
                        dict(baseline_slow=True)):
            with self.subTest(options=options):
                report, state, _ = self.run_fixture(**options)
                self.assertNotEqual(report["exit_code"], 2, report)
                self.assertTrue(state["source_closed"])
                if options.get("baseline_slow"):
                    self.assertFalse(state["marker"])
                    self.assertTrue(report["cleanup_ok"])
                else:
                    self.assertFalse(report["cleanup_ok"])

    def test_maximum_trial_budget_and_reviewed_bridge_forwarding_without_execution(self):
        with patch.object(observer, "discover", return_value={}), \
                patch.object(observer, "select_candidate", return_value={"node": "verified-fixture"}), \
                patch.object(observer, "supervise", return_value=observer.new_report()) as supervised, \
                contextlib.redirect_stdout(io.StringIO()):
            observer.main(["--observe-calibration", "--allow-mirror-observation", "--allow-calibration-output",
                           "--device", "verified-fixture", "--seconds", "20", "--bridge", str(DEFAULT_BRIDGE)])
        call = supervised.call_args
        self.assertEqual(call.args[2], 32)
        self.assertTrue(call.kwargs["descendants"])
        options = json.loads(call.args[0][-1])
        self.assertEqual(options["seconds"], 20)
        self.assertEqual(Path(options["bridge"]), DEFAULT_BRIDGE.resolve())
        self.assertEqual(options["approved_scope"], "marker_observation_only")


def documented_observer_commands():
    """``(document, logical line, arguments)`` of each observer command in the docs' bash blocks."""
    commands = []
    for name in ("docs/hud-qualification.md", "docs/on-device.md", "docs/development.md"):
        for block in re.findall(r"```bash\n(.*?)```", (ROOT / name).read_text(encoding="utf-8"), re.S):
            for line in block.replace("\\\n", " ").splitlines():
                if "scripts/hud_marker_observer.py" not in line:
                    continue
                words = shlex.split(line)
                arguments = words[words.index("scripts/hud_marker_observer.py") + 1:]
                for index, word in enumerate(arguments):
                    if word[:1] in (">", "|", "&", ";"):
                        arguments = arguments[:index]
                        break
                commands.append((name, line.strip(), arguments))
    return commands


class ObserverSessionTokenTests(ObserverFixture, unittest.TestCase):
    """``observe(..., session=...)`` on a fake /proc root, and the ``--record-session-tokens`` opt-in."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.proc = FakeProc(directory.name)

    def test_an_injected_session_is_declared_before_capture_and_read_first_for_each_frame(self):
        session = fake_session(self.proc, STATE)
        clock, log = FakeClock(), []
        declare, read, convert = session.declare, session.read, observer.native_bgr

        def slow_declare():
            log.append("declare")
            # At the budget, which is accepted, and longer than the whole observation: the capture
            # deadline starts after it.
            clock.tick(observer.SESSION_DECLARATION_BUDGET_SECONDS)
            return declare()

        def logged_read():
            log.append("read")
            return read()

        def logged_convert(data, fmt):
            log.append("convert")
            return convert(data, fmt)

        with patch.object(session, "declare", side_effect=slow_declare), \
                patch.object(session, "read", side_effect=logged_read), \
                patch.object(observer, "native_bgr", side_effect=logged_convert):
            report, state, _ = self.run_fixture(session=session, clock=clock, log=log)
        self.assertEqual((report["status"], report["exit_code"]), ("observed_unqualified", 2), report)
        self.assertEqual(log[:2], ["declare", "backend"])
        self.assertEqual(log.count("declare"), 1)
        # Each complete read is read for its tokens before anything else processes it.
        frames = [index for index, entry in enumerate(log) if entry == "frame"]
        self.assertEqual(len(frames), report["capture"]["frames_read"])
        for index in frames:
            self.assertEqual(log[index + 1], "read", log)
        for index, entry in enumerate(log):
            if entry == "convert":
                self.assertEqual(log[index - 1], "read", log)
        tokens = report["session_tokens"]
        self.assertIs(tokens["recorded"], True)
        self.assertEqual(set(tokens["declared"]), set(SESSION_TOKENS))
        for name in SESSION_TOKENS[:-1]:
            self.assertTrue(token_valid(tokens["declared"][name]), name)
        self.assertEqual(tokens["declared"]["recenter_epoch"], 0)
        self.assertEqual(tokens["undeclared"], {})
        self.assertIsNone(tokens["first_change"])
        self.assertEqual(tokens["frames_read"], report["capture"]["frames_read"])
        self.assertEqual(tokens["frames_read"], log.count("read"))
        self.assertEqual(tokens["runtime_process"]["pid"], PID)
        self.assertEqual(tokens["declaration_elapsed_ms"], 4000)
        observer.validate_worker_report(report, 2)
        self.assertLess(len(json.dumps(report, allow_nan=False)), 128 * 1024)
        self.assertFalse(report["mapping_qualified"])

    def test_a_declaration_over_its_budget_rejects_before_capture_or_marker(self):
        # Otherwise the supervisor's bound could expire while the marker is shown and force its termination.
        self.assertEqual(observer.SESSION_DECLARATION_BUDGET_SECONDS, 4)  # As docs/hud-qualification.md states.
        self.assertEqual(observer.SUPERVISOR_MARGIN_SECONDS, 12)
        session = fake_session(self.proc, STATE)
        clock, log, declare = FakeClock(), [], session.declare

        def slow_declare():
            clock.tick(observer.SESSION_DECLARATION_BUDGET_SECONDS + .001)
            return declare()

        with patch.object(session, "declare", side_effect=slow_declare):
            report, state, _ = self.run_fixture(seconds=20, session=session, clock=clock, log=log)
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("rejected", 1, "observer_session_tokens_declaration_too_slow"))
        self.assertEqual(log, [])  # No backend was constructed and no frame read.
        self.assertFalse(state["constructed"])
        self.assertFalse(state["marker"])
        self.assertTrue(state["marker_closed"])
        self.assertNotIn("capture", report)
        self.assertTrue(report["cleanup_ok"], report)
        # The declaration did complete, so its report and measured duration stay in the worker report.
        tokens = report["session_tokens"]
        self.assertIs(tokens["recorded"], True)
        self.assertEqual((tokens["frames_read"], tokens["declaration_elapsed_ms"]), (0, 4001))
        self.assertEqual(tokens["undeclared"], {})
        observer.validate_worker_report(report, 1)

    def test_a_frame_delivered_after_the_deadline_is_still_read_for_its_tokens(self):
        # capture() checks its deadline before each read; the read, its format query and its digest can end
        # after it, and that frame is still counted and passed on. The observation ignores it, but the
        # session must read it so that both frame counts agree.
        for late in (6, 8, 10):
            session = fake_session(self.proc, STATE)
            clock, log, read, convert = FakeClock(), [], session.read, observer.native_bgr

            def logged_read():
                log.append("read")
                return read()

            def logged_convert(data, fmt):
                log.append("convert")
                return convert(data, fmt)

            def on_read(number, late=late, clock=clock):
                if number == late:
                    clock.tick(1.5)  # This read ends past the two-second deadline.

            with self.subTest(late=late), patch.object(session, "read", side_effect=logged_read), \
                    patch.object(observer, "native_bgr", side_effect=logged_convert):
                report, _, _ = self.run_fixture(seconds=2, session=session, clock=clock, log=log, on_read=on_read)
                self.assertEqual((report["status"], report["exit_code"]), ("observed_unqualified", 2), report)
                self.assertEqual(report["capture"]["frames_read"], late)
                self.assertEqual(report["session_tokens"]["frames_read"], late)
                self.assertEqual(log.count("read"), late)
                self.assertEqual(log[-2:], ["frame", "read"])  # Read for its tokens, never converted.
                observer.validate_worker_report(report, 2)

    def test_without_a_session_nothing_is_read_under_proc(self):
        touched = []

        def guarded(real):
            def call(path=".", *args, **kwargs):
                if not isinstance(path, int) and os.fsdecode(path).startswith(("/proc", str(self.proc.root))):
                    touched.append((real.__name__, os.fsdecode(path)))
                return real(path, *args, **kwargs)
            return call

        with ExitStack() as stack:
            for name in PROVIDERS:
                for owner in (session_tokens, observer):
                    stack.enter_context(patch.object(owner, name, side_effect=AssertionError("provider built"),
                                                     create=True))
            # pathlib opens through io.open, a binding separate from builtins.open, and Path.iterdir lists
            # through os.listdir.
            for owner, name in ((os, "scandir"), (os, "listdir"), (os, "readlink"), (os, "stat"), (os, "lstat"),
                                (os, "open"), (io, "open")):
                stack.enter_context(patch.object(owner, name, guarded(getattr(owner, name))))
            stack.enter_context(patch("builtins.open", guarded(open)))
            report, state, _ = self.run_fixture()
        self.assertEqual((report["status"], report["exit_code"]), ("observed_unqualified", 2), report)
        self.assertEqual(touched, [])
        self.assertEqual(report["session_tokens"], {"recorded": False, "reason": "session_tokens_not_requested"})
        self.assertEqual(observer.new_report()["session_tokens"], report["session_tokens"])
        observer.validate_worker_report(report, 2)

    def test_a_failing_session_rejects_the_run_with_a_named_reason_and_cleans_up(self):
        # A declaration that raises: nothing opens and nothing needs closing. MirrorSession's own errors are
        # ValueErrors (a second declaration raises session_already_declared), so any exception is named.
        def provider_failure(error):
            session = fake_session(self.proc, STATE)
            return session, patch.object(session.providers[1], "declare", side_effect=error)

        def declared_twice():
            session = fake_session(self.proc, STATE)
            session.declare()
            return session, contextlib.nullcontext()

        def slow_then_failing():
            # A declaration that runs past its budget and then raises is named as a failed declaration.
            session = fake_session(self.proc, STATE)

            def declare():
                clock.tick(observer.SESSION_DECLARATION_BUDGET_SECONDS + 1)
                raise OSError("synthetic slow /proc failure")
            return session, patch.object(session, "declare", side_effect=declare)

        for name, prepare in (("OSError", lambda: provider_failure(OSError("synthetic /proc failure"))),
                              ("ValueError", declared_twice),
                              ("RuntimeError", lambda: provider_failure(RuntimeError("synthetic provider bug"))),
                              ("slow_then_failing", slow_then_failing)):
            clock = FakeClock()
            session, failure = prepare()
            with self.subTest(failure=name), failure:
                report, state, _ = self.run_fixture(session=session, clock=clock)
                self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                                 ("rejected", 1, "observer_session_tokens_declaration_failed"))
                self.assertFalse(state["constructed"])
                self.assertFalse(state["marker"])
                self.assertTrue(state["marker_closed"])
                self.assertTrue(report["cleanup_ok"], report)
                self.assertEqual(report["session_tokens"],
                                 {"recorded": False, "reason": "session_tokens_declaration_failed"})
                observer.validate_worker_report(report, 1)
        # A read that raises after the marker started (an OSError, the session's own error or another
        # exception): capture and marker still close cleanly.
        for failure in (OSError("synthetic read failure"), ValueError("session_not_declared"),
                        RuntimeError("synthetic provider bug")):
            session = fake_session(self.proc, STATE)
            read, calls = session.read, []

            def failing_read(read=read, calls=calls, failure=failure):
                calls.append(1)
                if len(calls) == 4:
                    raise failure
                return read()

            with self.subTest(failure=type(failure).__name__), patch.object(session, "read", side_effect=failing_read):
                report, state, _ = self.run_fixture(session=session)
                self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                                 ("rejected", 1, "observer_session_tokens_read_failed"))
                self.assertTrue(state["marker"])
                self.assertTrue(state["source_closed"])
                self.assertTrue(bridge_closed_cleanly(report["bridge"]))
                self.assertTrue(report["cleanup_ok"], report)
                self.assertEqual(report["session_tokens"]["frames_read"], 3)
                self.assertEqual(report["capture"]["frames_read"], 4)
                observer.validate_worker_report(report, 1)
        # A session report the contract would refuse is never put into the worker report.
        session = fake_session(self.proc, STATE)
        with patch.object(session, "report", return_value={"declared": {}, "undeclared": {}}):
            report, state, _ = self.run_fixture(session=session)
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("rejected", 1, "observer_session_tokens_report_invalid"))
        self.assertEqual(report["session_tokens"], {"recorded": False, "reason": "session_tokens_report_invalid"})
        self.assertTrue(report["cleanup_ok"], report)
        observer.validate_worker_report(report, 1)

    def test_a_session_report_that_raises_rejects_an_observed_run_and_still_closes_the_marker(self):
        # session_tokens_record catches any exception from report(), which runs in observe()'s finally before
        # the marker closes: none may escape, skip the close or leave an observed status.
        unrecorded = {"recorded": False, "reason": "session_tokens_report_invalid"}
        for failure in (RuntimeError("synthetic report bug"), KeyError("declared"), TypeError("synthetic type")):
            session = fake_session(self.proc, STATE)
            with self.subTest(failure=type(failure).__name__), patch.object(session, "report", side_effect=failure):
                report, state, _ = self.run_fixture(session=session)
                self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                                 ("rejected", 1, "observer_session_tokens_report_invalid"), report)
                self.assertEqual(report["session_tokens"], unrecorded)
                self.assertTrue(state["marker"])
                self.assertTrue(state["marker_closed"])
                self.assertTrue(state["source_closed"])
                self.assertTrue(bridge_closed_cleanly(report["bridge"]))
                self.assertIs(report["cleanup_ok"], True, report)
                observer.validate_worker_report(report, 1)
        # A run that is not otherwise observed keeps its own status and reason; only session_tokens records
        # the failure.
        for options, status, code in ((dict(cancel_at=.4), "cancelled", 130), (dict(bad_format=True), "rejected", 1)):
            expected, _, _ = self.run_fixture(session=fake_session(self.proc, STATE), **options)
            self.assertEqual((expected["status"], expected["exit_code"]), (status, code), expected)
            self.assertNotEqual(expected.get("reason"), "observer_session_tokens_report_invalid")
            for failure in (RuntimeError("synthetic report bug"), {"declared": {}, "undeclared": {}}):
                session = fake_session(self.proc, STATE)
                invalid = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with self.subTest(options=options, failure=failure), patch.object(session, "report", **invalid):
                    report, state, _ = self.run_fixture(session=session, **options)
                    self.assertEqual((report["status"], report["exit_code"], report.get("reason")),
                                     (status, code, expected.get("reason")), report)
                    self.assertEqual(report["session_tokens"], unrecorded)
                    self.assertTrue(state["source_closed"])
                    self.assertTrue(state["marker_closed"])
                    observer.validate_worker_report(report, code)

    def test_unrecorded_session_tokens_shape_is_part_of_the_worker_contract(self):
        # Required in every worker report: observed, rejected and cancelled runs alike.
        for options, status, code in ((dict(), "observed_unqualified", 2), (dict(bad_format=True), "rejected", 1),
                                      (dict(cancel_at=.4), "cancelled", 130)):
            report, _, _ = self.run_fixture(**options)
            self.assertEqual((report["status"], report["exit_code"]), (status, code), report)
            observer.validate_worker_report(report, code)
            unrecorded = report["session_tokens"]
            cases = [{key: value for key, value in report.items() if key != "session_tokens"}]
            for substitute in ("x", [1], 7, 1.5, True, None, {}):
                cases.append(dict(report, session_tokens=substitute))
                for key in unrecorded:
                    if type(substitute) is not type(unrecorded[key]):
                        cases.append(dict(report, session_tokens=dict(unrecorded, **{key: substitute})))
            cases += [dict(report, session_tokens=dict(unrecorded, reason="session_tokens_maybe")),
                      dict(report, session_tokens=dict(unrecorded, extra=1)),
                      dict(report, session_tokens={"recorded": False}),
                      dict(report, session_tokens={"recorded": True, "reason": unrecorded["reason"]})]
            for index, case in enumerate(cases):
                with self.subTest(exit_code=code, case=index, session_tokens=case.get("session_tokens", "absent")), \
                        self.assertRaisesRegex(ValueError, "invalid_observer_session_tokens"):
                    observer.validate_worker_report(case, code)
        # A recorded entry in a rejected report is checked as well.
        report, _, _ = self.run_fixture(session=fake_session(self.proc, STATE), bad_format=True)
        self.assertEqual((report["exit_code"], report["session_tokens"]["recorded"]), (1, True), report)
        observer.validate_worker_report(report, 1)
        recorded = report["session_tokens"]
        for case in (dict(recorded, frames_read=-1), dict(recorded, declared={}), dict(recorded, scope=None)):
            with self.subTest(recorded=case), self.assertRaisesRegex(ValueError, "invalid_observer_session_tokens"):
                observer.validate_worker_report(dict(report, session_tokens=case), 1)

    @unittest.skipUnless(sys.platform.startswith("linux"), "the guardian supervisor runs on Linux")
    def test_the_supervisor_keeps_its_base_tokens_when_no_valid_worker_report_arrives(self):
        # session_tokens_not_reported: no worker report, or one this contract refused, whose tokens are
        # discarded with it. The supervisor report's status and reason say which.
        report, _, _ = self.run_fixture(session=fake_session(self.proc, STATE), bad_format=True)
        self.assertEqual((report["exit_code"], report["session_tokens"]["recorded"]), (1, True), report)
        printer = "import sys; sys.stdout.write(sys.argv[1]); sys.exit(1)"  # The exit code the report states.
        cases = (("tokens_refused", dict(report, session_tokens=dict(report["session_tokens"], frames_read=-1)),
                  "invalid_observer_session_tokens"),
                 ("report_refused", dict(report, maximum_processing_fps=6), "invalid_observer_worker_report"),
                 ("silent", None, None))
        for name, printed, reason in cases:
            command = [sys.executable, "-c", printer if printed else "pass", json.dumps(printed)]
            with self.subTest(case=name):
                supervised = observer.supervise(command, observer.new_report(record_session_tokens=True), 10,
                                                observer.validate_worker_report)
                self.assertEqual(supervised["status"], "invalid_worker_report", supervised)
                if reason is not None:
                    self.assertEqual(supervised["reason"], reason)
                self.assertEqual(supervised["session_tokens"], {"recorded": False,
                                                                "reason": "session_tokens_not_reported"})

    def test_record_session_tokens_requires_the_complete_opt_in(self):
        flag_error = "--record-session-tokens requires the complete calibration opt-in"
        partial_error = "calibration observation requires all three explicit flags and a verified --device"
        for args, error in ((["--record-session-tokens"], flag_error),
                            (["--record-session-tokens", "--seconds", "3"], flag_error),
                            (["--record-session-tokens", *FULL_OPT_IN[:3]], partial_error),
                            (["--record-session-tokens", *FULL_OPT_IN[3:]], partial_error)):
            stderr = io.StringIO()
            with self.subTest(args=args), patch.object(observer, "discover") as discover, \
                    patch.object(observer, "supervise") as supervised, contextlib.redirect_stderr(stderr), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                observer.main(args)
            self.assertEqual(raised.exception.code, 2)
            discover.assert_not_called()
            supervised.assert_not_called()
            self.assertIn(error, stderr.getvalue())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit):
            observer.main(["--help"])
        self.assertIn("--record-session-tokens", stdout.getvalue())

    def test_record_session_tokens_is_forwarded_to_the_worker_without_execution(self):
        for extra, expected, base_reason in (([], False, "session_tokens_not_requested"),
                                             (["--record-session-tokens"], True, "session_tokens_not_reported")):
            with self.subTest(extra=extra), patch.object(observer, "discover", return_value={}), \
                    patch.object(observer, "select_candidate", return_value={"node": "verified-fixture"}), \
                    patch.object(observer, "supervise", return_value=observer.new_report()) as supervised, \
                    contextlib.redirect_stdout(io.StringIO()):
                observer.main([*FULL_OPT_IN, *extra])
            call = supervised.call_args
            options = json.loads(call.args[0][-1])
            self.assertIs(options["record_session_tokens"], expected)
            self.assertEqual(call.args[2], 32)  # The supervisor's bound is unchanged: --seconds 20 plus 12.
            # A worker that never reports leaves the supervisor's base report, which records no tokens.
            self.assertEqual(call.args[1]["session_tokens"], {"recorded": False, "reason": base_reason})

    def test_the_worker_builds_session_providers_only_when_asked(self):
        base = {"approved_scope": "marker_observation_only", "candidate": {"node": "verified-fixture"},
                "seconds": 20, "bridge": None}
        for record in (False, True):
            with self.subTest(record=record), ExitStack() as stack:
                observe = stack.enter_context(patch.object(observer, "observe", return_value=observer.new_report()))
                stack.enter_context(patch.object(observer, "stop_on_signals", return_value=threading.Event()))
                stack.enter_context(patch.object(observer, "MarkerClient"))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                # No provider reads anything here; without the option none is even constructed.
                for name in PROVIDERS:
                    for method in ("declare", "read", "report", "observe", "_find", "_observe"):
                        if hasattr(getattr(session_tokens, name), method):
                            stack.enter_context(patch.object(getattr(session_tokens, name), method,
                                                             side_effect=AssertionError("provider work")))
                    if not record:
                        stack.enter_context(patch.object(observer, name, side_effect=AssertionError("provider built"),
                                                         create=True))
                observer.main(["--worker", json.dumps(dict(base, record_session_tokens=record))])
            session = observe.call_args.kwargs.get("session")
            if not record:
                self.assertIsNone(session)
                continue
            self.assertIsInstance(session, session_tokens.MirrorSession)
            self.assertEqual([type(provider) for provider in session.providers],
                             [session_tokens.HostClock, session_tokens.RuntimeProcess, session_tokens.TrackingObserver])
            self.assertEqual([provider.proc_root for provider in session.providers[:2]], ["/proc", "/proc"])
            self.assertEqual(session.providers[1].name, session_tokens.RUNTIME_PROCESS)  # The SteamVR server.
            self.assertEqual(session.providers[2].events, 0)  # No reporter feeds it yet.
        for options in ({key: value for key, value in base.items()}, dict(base, record_session_tokens="yes"),
                        dict(base, record_session_tokens=1), dict(base, record_session_tokens=None)):
            with self.subTest(options=options), patch.object(observer, "observe") as observe, \
                    patch.object(observer, "stop_on_signals") as signals, patch.object(observer, "MarkerClient"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(ValueError, "observer_session_tokens_option_invalid"):
                observer.main(["--worker", json.dumps(options)])
            observe.assert_not_called()
            signals.assert_not_called()

    def test_documented_observer_commands_parse_and_record_no_session_tokens(self):
        commands = documented_observer_commands()
        self.assertGreaterEqual(len([command for command in commands if "--analyze-still" not in command[2]]), 2)
        for document, line, arguments in commands:
            with self.subTest(document=document, line=line), patch.object(observer, "discover", return_value={}), \
                    patch.object(observer, "select_candidate", return_value={"node": "verified-fixture"}), \
                    patch.object(observer, "supervise", return_value=observer.new_report()) as supervised, \
                    contextlib.redirect_stdout(io.StringIO()):
                observer.main(arguments)
                if "--analyze-still" in arguments:
                    # File-only analysis: never the supervised device run, so no device and no session tokens.
                    supervised.assert_not_called()
                    continue
                # The approved G3 run is unchanged: it does not pass the new flag, so it reads nothing under /proc for session tokens.
                self.assertNotIn("--record-session-tokens", arguments)
                self.assertIs(json.loads(supervised.call_args.args[0][-1])["record_session_tokens"], False)

def native_synthetic_worker(seconds, tag_mismatch=False):
    """Test entry point: generated source plus actual no-VR marker process.

    With ``tag_mismatch``, every marker frame carries the V4-shaped tag/bit
    mismatch, so the first marker frame (frame 3) is rejected.
    """
    client = observer.MarkerClient(validation_only=True)
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stop.set())
    state = {"source_closed": False, "ready_sent": False, "reads": 0}
    class Source:
        kind = "synthetic"
        def __init__(self, candidate):
            self.capabilities = {"driver": "synthetic"}
            self.format = dict(width=1000, height=600, fourcc="BGR3", stride_bytes=3000,
                               minimum_buffer_bytes=1800000, buffer_bytes=1800000)
        def open(self):
            pass
        def read_frame(self, timeout):
            state["reads"] += 1
            visible = client.process is not None
            if visible and not state["ready_sent"]:
                print("SYNTHETIC OBSERVER READY", file=sys.stderr, flush=True)
                state["ready_sent"] = True
            if tag_mismatch:
                image = scene(((100, 100),) if visible else (), template=tag_mismatch_template())
            else:
                image = scene(((70, 80), (570, 270)) if visible else ())
            image[0, 0] = state["reads"] % 100
            return image.tobytes()
        def close(self):
            state["source_closed"] = True
    report = observer.observe({"node": "synthetic:combined-native"}, seconds, stop, backend_factory=Source,
                              marker_client=client)
    report["synthetic_source_closed"] = state["source_closed"]
    print(json.dumps(report), flush=True)
    return report["exit_code"]


@unittest.skipUnless(sys.platform.startswith("linux") and DEFAULT_BRIDGE.is_file(), "Linux native bridge required")
class CombinedNativeTests(unittest.TestCase):
    def test_generated_source_observer_native_timer_and_guardian_complete_together(self):
        report = observer.supervise([sys.executable, "-m", "tests.test_hud_marker_observer", "--synthetic-worker", "2"],
                                    observer.new_report(), 14, observer.validate_worker_report, descendants=True)
        self.assertEqual(report["exit_code"], 2, report)
        self.assertTrue(report["cleanup_ok"], report)
        self.assertTrue(report["synthetic_source_closed"])
        self.assertTrue(report["worker_tree_reaped"])
        self.assertTrue(report["guardian_reaped"])
        self.assertTrue(report["report_reader_stopped"])
        self.assertEqual(report["observed_copy_count"], 2)
        self.assertFalse(report["bridge"]["native_report"]["runtime_initialized"])
        self.assertFalse(report["mapping_qualified"])

    def test_parent_cancellation_at_twenty_second_configuration_reaps_whole_trial(self):
        # Keep the supervisor as light as production: NumPy/OpenCV belong in
        # the test worker, not in the parent or independent guardian.
        command = ("import json,sys; from scripts import hud_marker_observer as o; "
                   "r=o.supervise([sys.executable,'-m','tests.test_hud_marker_observer','--synthetic-worker','20'],"
                   "o.new_report(True),32,o.validate_worker_report,descendants=True); "
                   "print(json.dumps(r),flush=True); sys.exit(r['exit_code'])")
        process = subprocess.Popen([sys.executable, "-c", command],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready = queue.Queue()
        def read_stderr():
            for line in process.stderr:
                if b"SYNTHETIC OBSERVER READY" in line:
                    ready.put(True)
        reader = threading.Thread(target=read_stderr, daemon=True)
        reader.start()
        try:
            self.assertTrue(ready.get(timeout=12))
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
            report = json.loads(process.stdout.read())
            self.assertEqual(report["status"], "cancelled", report)
            for key in ("synthetic_source_closed", "worker_reaped", "worker_tree_reaped", "guardian_reaped",
                        "report_reader_stopped", "guardian_report_reader_stopped"):
                self.assertTrue(report[key], (key, report))
            # Native termination is conservative evidence, never recovery proof.
            self.assertFalse(report["cleanup_ok"])
            self.assertTrue(report["bridge"]["forced_termination"])
            self.assertFalse(report["physical_display_recovery_verified"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()


@unittest.skipUnless(sys.platform.startswith("linux") and DEFAULT_BRIDGE.is_file(),
                     "real validation bridge not built: run bash scripts/build_overlay_bridge.sh (CI builds it)")
class V4ShapedRejectionNativeTests(unittest.TestCase):
    """E75's V4 sequence against the real validation bridge, worker and guardian."""

    def test_frame_three_tag_mismatch_closes_the_real_bridge_without_forced_termination(self):
        report = observer.supervise([sys.executable, "-m", "tests.test_hud_marker_observer",
                                     "--synthetic-tag-mismatch-worker", "2"],
                                    observer.new_report(), 14, observer.validate_worker_report, descendants=True)
        # Two clear baseline frames start the marker; frame 3 fails the tag/bit checks.
        self.assertEqual(report["status"], "rejected", report)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["reason"], "orphan_marker_tag_or_incomplete_copy")
        self.assertEqual(report["baseline_sequence"], 2)
        details = report["last_match_diagnostics"]
        self.assertEqual(details["sequence"], 3)
        self.assertEqual(details["complete_copy_count"], 0)
        self.assertGreater(details["orphan_fragment_count"], 0)
        self.assertTrue(any({"tag_pattern_error", "code_bit_error"} <= set(candidate["failed_checks"])
                            for candidate in details["best_candidates"]), details)
        self.assertEqual(report["sample_count"], 0)
        # The rejection requests a stop; the bridge ends on its own timer, unforced.
        bridge = report["bridge"]
        self.assertFalse(bridge["forced_termination"], bridge)
        self.assertTrue(bridge_closed_cleanly(bridge), bridge)
        self.assertTrue(bridge["stop_requested"])
        self.assertFalse(bridge["escalated"])
        native = bridge["native_report"]
        self.assertEqual(native["status"], "validated_marker")
        self.assertGreaterEqual(native["cutoff_requested_ns"], native["display_deadline_ns"])
        self.assertFalse(native["runtime_initialized"])
        self.assertTrue(report["cleanup_ok"], report)
        for key in ("worker_forced_termination", "guardian_forced_termination"):
            self.assertFalse(report[key], key)
        for key in ("synthetic_source_closed", "worker_reaped", "worker_tree_reaped", "guardian_reaped"):
            self.assertTrue(report[key], key)
        self.assertNotIn("orphaned_worker_group", report)  # The guardian ran its own cleanup.
        for key in ("physical_display_recovery_verified", "mapping_qualified", "images_saved", "inference_started"):
            self.assertFalse(report[key], key)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in ("--synthetic-worker", "--synthetic-tag-mismatch-worker"):
        sys.exit(native_synthetic_worker(int(sys.argv[2]), tag_mismatch=sys.argv[1] != "--synthetic-worker"))
    unittest.main()
