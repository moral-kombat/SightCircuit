"""Synthetic native-resolution marker copies; no device or output action."""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

try:
    import cv2
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for HUD marker observer tests") from error
import numpy as np

from scripts import hud_marker_observer as observer
from framejacker.hud import CaptureFormat, HUD_CONFIG
from framejacker.sources import mirror as mirror_check
from framejacker.overlay import DEFAULT_BRIDGE

ROOT = Path(__file__).resolve().parents[1]


def clean_bridge_fixture(seconds=1):
    marker = dict(width=320, height=128, opaque_pixels=40960, corner_ids=[1, 2, 3, 4],
                  border_pixels=4, tag_size_pixels=24, tag_inset_pixels=8,
                  corner_order="top_left,top_right,bottom_left,bottom_right", config_id=HUD_CONFIG.layout_id,
                  visible_in_dashboard=True)
    marker.update({key: getattr(HUD_CONFIG, key) for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity")})
    native = dict(report_kind="framejacker_overlay_bridge", status="validated_marker", exit_code=0, cleanup_ok=True,
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


def scene(positions=((100, 100),), *, width=1000, height=600):
    image = np.full((height, width, 3), 90, np.uint8)
    template = observer.marker_template()
    for x, y in positions:
        image[y:y+128, x:x+320] = template[:, :, None]
    return image


class MarkerGeometryTests(unittest.TestCase):
    def test_all_complete_copies_are_found_without_eye_assignment(self):
        for positions in (((100, 100),), ((70, 80), (570, 270)), ((100, 50), (100, 330))):
            with self.subTest(positions=positions):
                copies = observer.locate_copies(scene(positions))
                self.assertEqual(len(copies), len(positions))
                for quad, (x, y) in zip(copies, sorted(positions)):
                    np.testing.assert_allclose(quad, [[x, y], [x+319, y], [x+319, y+127], [x, y+127]], atol=1)

    def test_perspective_and_rotated_marker_keep_native_corners(self):
        source = np.float32([[0, 0], [319, 0], [319, 127], [0, 127]])
        for target in (np.float32([[100, 100], [450, 110], [430, 270], [110, 250]]),
                       np.float32([[380, 260], [61, 260], [61, 133], [380, 133]])):
            with self.subTest(target=target.tolist()):
                warped = cv2.warpPerspective(observer.marker_template(), cv2.getPerspectiveTransform(source, target),
                                             (800, 500), borderValue=90)
                copies = observer.locate_copies(cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR))
                self.assertEqual(len(copies), 1)
                np.testing.assert_allclose(copies[0], target, atol=2)

    def test_resolvable_scaled_copies_at_native_1080p_and_unresolvable_codes(self):
        for scale in (.25, .4, .5, .75):
            with self.subTest(scale=scale):
                tile = cv2.resize(observer.marker_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image = np.full((1080, 1920, 3), 90, np.uint8)
                for x, y in ((100, 100), (1400, 700)):
                    image[y:y+h, x:x+w] = tile[:, :, None]
                if scale >= .5:
                    copies = observer.locate_copies(image)
                    self.assertEqual(len(copies), 2)
                    np.testing.assert_allclose(copies[1], [[1400, 700], [1400+w-1, 700],
                                               [1400+w-1, 700+h-1], [1400, 700+h-1]], atol=1)
                else:
                    with self.assertRaises(ValueError):
                        observer.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1,
                                                               config_id=HUD_CONFIG.layout_id)

    def test_translucent_or_blurred_marker_does_not_establish_full_footprint(self):
        original = scene()
        for image in ((original.astype(np.float32)*.5 + 45).astype(np.uint8),
                      cv2.GaussianBlur(original, (15, 15), 0)):
            with self.assertRaises(ValueError):
                observer.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1,
                                                        config_id=HUD_CONFIG.layout_id)

    def test_resolvable_partial_scaled_copy_cannot_hide_beside_complete_copy(self):
        for scale in (.5, .75, 1.):
            with self.subTest(scale=scale):
                image = scene()
                tile = cv2.resize(observer.marker_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image[300:300+h, 600:600+w] = tile[:, :, None]
                image[295:300+int(40*scale), 595:600+int(40*scale)] = 90
                with self.assertRaisesRegex(ValueError, "orphan"):
                    observer.locate_copies(image)

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
                    observation = observer.EmpiricalObservation()
                    observation.accept(image, CaptureFormat.bgr(image), 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_no_marker_is_not_mapping_evidence(self):
        image = np.full((600, 1000, 3), 90, np.uint8)
        self.assertEqual(observer.locate_copies(image), [])
        with self.assertRaisesRegex(ValueError, "no_complete_marker"):
            observer.EmpiricalObservation().accept(image, CaptureFormat.bgr(image), 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_empirical_union_and_margin_include_small_movement(self):
        track = observer.EmpiricalObservation()
        for i, offset in enumerate((0, 2, -2), 1):
            image = scene(((100+offset, 100), (600, 300)))
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
        evidence = track.evidence()
        self.assertEqual(evidence["footprint_union_native"], [[82, 84, 438, 244], [584, 284, 936, 444]])
        self.assertEqual(evidence["maximum_corner_drift_px"], 2)
        for key in ("eye_identity_assigned", "composition_verified", "unseen_or_occluded_copies_established", "mapping_qualified"):
            self.assertFalse(evidence[key])

    def test_vertical_copy_assignment_survives_small_horizontal_jitter(self):
        track = observer.EmpiricalObservation()
        for i, positions in enumerate((((100, 50), (100, 330)), ((102, 50), (99, 330))), 1):
            image = scene(positions)
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
        self.assertEqual(track.evidence()["observed_copy_count"], 2)

    def test_large_movement_count_change_and_margin_escape_fail_closed(self):
        first = scene()
        for changed, reason in ((scene(((120, 100),)), "unstable"),
                                (scene(((100, 100), (600, 300))), "count_changed")):
            track = observer.EmpiricalObservation()
            track.accept(first, CaptureFormat.bgr(first), 1, 1, config_id=HUD_CONFIG.layout_id)
            with self.assertRaisesRegex(ValueError, reason):
                track.accept(changed, CaptureFormat.bgr(changed), 2, 2, config_id=HUD_CONFIG.layout_id)
        clipped_margin = scene(((8, 8),))
        with self.assertRaisesRegex(ValueError, "margin_escapes"):
            observer.EmpiricalObservation().accept(clipped_margin, CaptureFormat.bgr(clipped_margin), 1, 1,
                                                    config_id=HUD_CONFIG.layout_id)

    def test_format_config_sequence_and_declared_copy_count_mismatches_reject(self):
        image = scene()
        descriptor = CaptureFormat.bgr(image)
        track = observer.EmpiricalObservation()
        track.accept(image, descriptor, 1, 1, config_id=HUD_CONFIG.layout_id)
        for fmt, sequence, config_id in ((replace(descriptor, fourcc="RGB3"), 2, HUD_CONFIG.layout_id),
                                         (descriptor, 1, HUD_CONFIG.layout_id), (descriptor, 2, "changed")):
            with self.subTest(fmt=fmt, sequence=sequence, config=config_id), self.assertRaises(ValueError):
                track.accept(image, fmt, sequence, 2, config_id=config_id)
        with self.assertRaisesRegex(ValueError, "expected_copy_count"):
            observer.EmpiricalObservation(observer.ObserverConfig(expected_copies=2)).accept(
                image, descriptor, 1, 1, config_id=HUD_CONFIG.layout_id)

    def test_native_rgb_stride_conversion_preserves_pixel_geometry(self):
        image = scene()
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        storage = np.zeros((600, 3008), np.uint8)
        storage[:, :3000] = rgb.reshape(600, 3000)
        fmt = dict(width=1000, height=600, fourcc="RGB3", stride_bytes=3008,
                   minimum_buffer_bytes=600*3008, buffer_bytes=600*3008)
        prepared, _ = observer.native_bgr(storage.tobytes(), fmt)
        np.testing.assert_array_equal(prepared, image)
        self.assertEqual(len(observer.locate_copies(prepared)), 1)


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
        track = observer.EmpiricalObservation()
        refined = False
        for i, phase in enumerate((0, .25, .5, .75), 1):
            gray = cv2.warpPerspective(observer.marker_template(), cv2.getPerspectiveTransform(source, target+phase),
                                       (1920, 1080), borderValue=90)
            image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            track.accept(image, CaptureFormat.bgr(image), i, i, config_id=HUD_CONFIG.layout_id)
            np.testing.assert_allclose(track.last_corners[0], target+phase, atol=2)
            details = track.last_match_diagnostics
            refined |= details["refined_outline_count"] > 0
            for candidate in details["best_candidates"]:
                self.assertLessEqual(candidate["refinement_shift_px"], observer.MAX_CORNER_REFINEMENT_PX)
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
        gray = cv2.warpPerspective(observer.marker_template(), cv2.getPerspectiveTransform(source, target),
                                   (1920, 1080), borderValue=90)
        with patch.object(cv2, "cornerSubPix", side_effect=lambda image, points, *args: points+100), \
                self.assertRaisesRegex(ValueError, "orphan"):
            observer.locate_copies(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        for tag in range(4):
            for bit in range(4):
                with self.subTest(tag=tag, bit=bit):
                    template = observer.marker_template()
                    x = (288 if tag % 2 else 8) + 4+(bit % 2)*8
                    y = (96 if tag // 2 else 8) + 4+(bit // 2)*8
                    template[y:y+6, x:x+6] = 255-template[y:y+6, x:x+6]
                    gray = cv2.warpPerspective(template, cv2.getPerspectiveTransform(source, target),
                                               (1920, 1080), borderValue=90)
                    with self.assertRaises(ValueError):
                        observer.locate_copies(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))

    def test_same_orphan_error_has_distinct_photometric_resolution_and_outline_evidence(self):
        cases = ((90, 1, .4545, {"whole_pattern_error"}),
                 (90, .4, 1, {"outline_brightness", "tag_pattern_error", "code_bit_error"}),
                 (255, 1, 1, {"whole_pattern_error", "outline_brightness", "tag_pattern_error", "code_bit_error"}))
        for background, scale, gamma, failed in cases:
            with self.subTest(background=background, scale=scale, gamma=gamma):
                template = np.round(255*(observer.marker_template().astype(np.float32)/255)**gamma).astype(np.uint8)
                tile = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tile.shape
                image = np.full((1080, 1920, 3), background, np.uint8)
                image[400:400+h, 700:700+w] = tile[:, :, None]
                details = {}
                with self.assertRaisesRegex(ValueError, "orphan_marker_tag_or_incomplete_copy"):
                    observer.locate_copies(image, diagnostics=details)
                self.assertTrue(details["scan_complete"])
                self.assertEqual(details["complete_copy_count"], 0)
                self.assertGreater(details["orphan_fragment_count"], 0)
                self.assertEqual(set(details["best_candidates"][0]["failed_checks"]), failed)
                self.assertEqual((details["width"], details["height"]), (1920, 1080))
                self.assertEqual(details["coordinate_space"], "native_capture_pixels")
                self.assertLess(len(json.dumps(details, allow_nan=False)), 8192)

    def test_complete_copy_beside_partial_copy_is_retained_only_as_rejected_candidate(self):
        image = scene(((70, 80), (570, 270)))
        image[260:320, 560:620] = 90
        details = {}
        with self.assertRaisesRegex(ValueError, "orphan"):
            observer.locate_copies(image, diagnostics=details)
        self.assertEqual(details["complete_copy_count"], 1)
        self.assertEqual(details["status"], "rejected")
        self.assertEqual(details["fragment_quads_scope"], "orphans")
        self.assertTrue(all(quad[0][0] >= 570 for quad in details["fragment_quads_native"]))
        np.testing.assert_allclose(details["complete_copy_corners_native"][0],
                                   [[70, 80], [389, 80], [389, 207], [70, 207]])

    def test_candidate_and_fragment_metadata_are_capped_without_dropping_total_counts(self):
        image = np.full((600, 1000, 3), 90, np.uint8)
        tag = observer.marker_template()[8:32, 8:32]
        for i in range(24):
            x, y = 50+(i%8)*50, 50+(i//8)*50
            image[y:y+24, x:x+24] = tag[:, :, None]
        for i in range(8):
            x, y = 50+(i%4)*180, 250+(i//4)*100
            cv2.rectangle(image, (x, y), (x+120, y+50), (255, 255, 255), 4)
        details = {}
        with self.assertRaisesRegex(ValueError, "orphan"):
            observer.locate_copies(image, diagnostics=details)
        self.assertGreater(details["orphan_fragment_count"], observer.MAX_DIAGNOSTIC_FRAGMENTS)
        self.assertEqual(len(details["fragment_quads_native"]), observer.MAX_DIAGNOSTIC_FRAGMENTS)
        self.assertTrue(details["fragment_quads_truncated"])
        self.assertGreater(details["eligible_outline_count"], observer.MAX_DIAGNOSTIC_CANDIDATES)
        self.assertEqual(len(details["best_candidates"]), observer.MAX_DIAGNOSTIC_CANDIDATES)
        self.assertTrue(details["best_candidates_truncated"])
        self.assertLess(len(json.dumps(details, allow_nan=False)), 8192)

    def test_matching_or_context_failure_clears_stale_corners_and_records_frame_sequence(self):
        first = scene()
        failed = scene()
        failed[95:140, 95:140] = 90
        for image, descriptor in ((failed, CaptureFormat.bgr(failed)),
                                  (first, replace(CaptureFormat.bgr(first), fourcc="RGB3"))):
            track = observer.EmpiricalObservation()
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
        image = np.full((600, 1000, 3), 90, np.uint8)
        for y in range(5, 595, 10):
            for x in range(5, 995, 10):
                image[y:y+2, x:x+2] = 255
        details = {}
        with self.assertRaisesRegex(ValueError, "contour_budget"):
            observer.locate_copies(image, diagnostics=details)
        self.assertFalse(details["scan_complete"])
        self.assertGreater(details["contour_count"], observer.MAX_CONTOURS)
        self.assertEqual(details["best_candidates"], [])
        self.assertLess(len(json.dumps(details)), 1024)


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
        gate = observer.BaselineGate(2)
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
        gate = observer.BaselineGate(2)
        for i in range(10):
            self.assertFalse(gate.accept(self.clear_image(), i+1, i+1, i*.2))
        with self.assertRaisesRegex(ValueError, "baseline_distinct_clear"):
            gate.accept(self.clear_image(91), 11, 11, 2)
        self.assertEqual(gate.report["content_changes"], 0)
        self.assertFalse(gate.report["established"])

    def test_partial_marker_resets_clear_sequence_and_metadata_budgets_fail_immediately(self):
        gate = observer.BaselineGate(2)
        partial = scene()
        partial[95:140, 95:140] = 90
        self.assertFalse(gate.accept(self.clear_image(91), 1, 1, 0))
        self.assertFalse(gate.accept(partial, 2, 2, .2))
        self.assertFalse(gate.accept(self.clear_image(92), 3, 3, .4))
        self.assertTrue(gate.accept(self.clear_image(93), 4, 4, .6))
        with patch.object(observer, "locate_copies", side_effect=ValueError("observer_contour_budget")), \
                self.assertRaisesRegex(ValueError, "contour_budget"):
            observer.BaselineGate(2).accept(self.clear_image(), 1, 1, 0)


class ObserverLifecycleTests(unittest.TestCase):
    def run_fixture(self, *, seconds=1, cancel_at=None, bad_format=False, slow=False, close_failure=False,
                    bridge_change=None, baseline_slow=False, incomplete_marker=False, baseline_mode=None):
        clock = FakeClock()
        stop = FakeStop(clock)
        state = {"marker": False, "source_closed": False, "reads": [], "opened": False}
        class Source:
            kind = "synthetic"
            def __init__(self, candidate):
                self.capabilities = {"driver": "synthetic"}
                self.format = dict(width=1000, height=600, fourcc="BGR3", stride_bytes=3000,
                                   minimum_buffer_bytes=1800000, buffer_bytes=1800000)
            def open(self):
                state["opened"] = True
            def read_frame(self, timeout):
                state["reads"].append(clock.now)
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
                self.end = clock.now + seconds
                self.process = Mock()
                self.process.poll.side_effect = lambda: 0 if clock.now >= self.end else None
            def close(self):
                report = clean_bridge_fixture(seconds)
                if bridge_change:
                    bridge_change(report)
                return report
        original = observer.locate_copies
        def locate(image, **kwargs):
            result = original(image, **kwargs)
            if (slow and state["marker"]) or (baseline_slow and not state["marker"]):
                clock.tick(2)
            return result
        with patch.object(observer.time, "monotonic", clock.monotonic), \
                patch.object(observer.time, "monotonic_ns", clock.monotonic_ns), \
                patch.object(observer, "locate_copies", side_effect=locate):
            report = observer.observe({"node": "synthetic:fixture"}, seconds, stop, backend_factory=Source, marker_client=Marker())
        return report, state, clock

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


def native_synthetic_worker(seconds):
    """Test entry point: generated source plus actual no-VR marker process."""
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


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--synthetic-worker":
        sys.exit(native_synthetic_worker(int(sys.argv[2])))
    unittest.main()
