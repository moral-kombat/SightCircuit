"""Generated native images only; no hardware calibration or VR session."""
from dataclasses import FrozenInstanceError, replace
import contextlib
import io
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

try:
    import cv2
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for HUD mask tests") from error
import numpy as np

from framejacker import hud
from framejacker.frame import Detection, DetectionResult, Frame
from framejacker.runtime import live, synthetic
from scripts import live_pipeline as command
from framejacker.overlay import DEFAULT_BRIDGE, OverlayClient, OverlaySink, PanelSettings, rgba_from_bgr


def run_hud(source, detector, sink, seconds, calibration, *, config=hud.HUD_CONFIG, **options):
    """A synthetic HUD run as ``scripts/live_pipeline.py`` composes it: ``HudPipelineAdapter`` on ``run_pipeline``."""
    stage = hud.HudPipelineAdapter(source, sink, calibration, config)
    return live.run_pipeline(source, detector, sink, seconds, hud=stage, **options)


def recognize(candidate, seconds, stop, want_preview=False, **options):
    """``runtime.live.run_recognition`` with the image (HUD tile) of its last result, as ``--preview`` takes it."""
    report, last = live.run_recognition(candidate, seconds, stop, **options)
    return report, last.image if want_preview and last is not None else None


def calibration(width=1280, height=720, *, layout="mono", fourcc="BGR3", padding=0, footprints=None):
    stride = width * 3 + padding
    fmt = hud.CaptureFormat(width, height, fourcc, stride, stride * height, stride * height)
    if footprints is None:
        footprints = tuple(hud.Footprint(index, (x1 - 100, y1 - 70, x1 - 20, y1 - 20), 8)
                           for index, (x0, y0, x1, y1) in enumerate(hud.viewports(fmt, layout)))
    return synthetic.fixture_calibration("synthetic:mirror", fmt, layout, footprints)


def unvalidated_calibration():
    """A candidate-derived calibration: issued by its factory, never usable by the mask."""
    from tests.test_hud_qualification import candidate_record
    record = candidate_record()
    record["context"]["hud_config_sha256"] = hud.config_digest(hud.HUD_CONFIG)
    return hud.MappingCandidate.from_mapping(record).calibration()


def detected(label, confidence, box):
    return Detection(label, confidence, tuple(box))


def prepare(mask, image, **kwargs):
    context = dict(source_id=mask.calibration.source_id, capture_format=mask.calibration.capture_format,
                   capture_layout=mask.calibration.capture_layout, config=mask.config)
    return mask.prepare(image, **dict(context, **kwargs))


class HudMaskTests(unittest.TestCase):
    def test_native_1920_padded_stride_mask_precedes_960_inference_resize(self):
        cal = calibration(1920, 1080, padding=16)
        mask = hud.HudMask(cal, source_kind="synthetic")
        # Model real acquisition layout with padding that is not image pixels.
        storage = np.full((1080, cal.capture_format.stride_bytes), 173, np.uint8)
        native = np.ndarray((1080, 1920, 3), np.uint8, buffer=storage,
                            strides=(cal.capture_format.stride_bytes, 3, 1))
        native[:] = 20
        clean = native.copy()
        x0, y0, x1, y1 = mask.regions[0]
        native[y0:y1, x0:x1] = 255
        resize = cv2.resize
        def checked(image, *args, **kwargs):
            self.assertEqual(image.shape, (1080, 1920, 3))
            self.assertFalse(image[y0:y1, x0:x1].any())
            return resize(image, *args, **kwargs)
        with patch.object(cv2, "resize", side_effect=checked):
            actual = prepare(mask, native)
        self.assertEqual(actual.shape, (540, 960, 3))
        np.testing.assert_array_equal(actual, prepare(mask, clean))
        self.assertTrue((storage[:, 5760:] == 173).all())
        self.assertFalse(np.shares_memory(actual, native))

    def test_native_mask_precedes_resize_and_excludes_transparent_edges_and_filter_fringe(self):
        cal = calibration()
        mask = hud.HudMask(cal, source_kind="synthetic")
        clean = np.full((720, 1280, 3), 20, dtype=np.uint8)
        contaminated = clean.copy()
        x0, y0, x1, y1 = mask.regions[0]
        # Entire reserved rectangle and its margin: includes transparent glyph
        # gaps, translucent background and an antialias/filter fringe.
        contaminated[y0:y1, x0:x1] = np.random.default_rng(4).integers(
            0, 256, (y1 - y0, x1 - x0, 3), dtype=np.uint8)
        original = contaminated.copy()
        resize = cv2.resize
        def checked(image, *args, **kwargs):
            self.assertEqual(image.shape, (720, 1280, 3))
            self.assertFalse(image[y0:y1, x0:x1].any())
            return resize(image, *args, **kwargs)
        with patch.object(cv2, "resize", side_effect=checked) as resized:
            actual = prepare(mask, contaminated)
        self.assertEqual(resized.call_count, 1)
        np.testing.assert_array_equal(actual, prepare(mask, clean))
        np.testing.assert_array_equal(contaminated, original)
        self.assertEqual(actual.shape, (540, 960, 3))
        self.assertFalse(actual.flags.writeable)
        self.assertFalse(np.shares_memory(actual, contaminated))
        # The model's subsequent resize/normalization cannot recover removed HUD pixels.
        np.testing.assert_array_equal(cv2.dnn.blobFromImage(actual, size=(300, 300)),
                                      cv2.dnn.blobFromImage(prepare(mask, clean), size=(300, 300)))

    def test_stereo_masks_union_of_both_full_footprints(self):
        for layout in ("stereo_sbs", "stereo_tb"):
            with self.subTest(layout=layout):
                mask = hud.HudMask(calibration(layout=layout), source_kind="synthetic")
                image = np.ones((720, 1280, 3), np.uint8) * 37
                polluted = image.copy()
                for x0, y0, x1, y1 in mask.regions:
                    polluted[y0:y1, x0:x1] = 255
                np.testing.assert_array_equal(prepare(mask, polluted), prepare(mask, image))

    def test_missing_unvalidated_and_hardware_claims_fail_closed(self):
        cal = calibration()
        for candidate, kind in ((None, "synthetic"), (unvalidated_calibration(), "synthetic"), (cal, "live_camera")):
            with self.subTest(candidate=candidate, kind=kind), self.assertRaises(ValueError):
                hud.HudMask(candidate, source_kind=kind)
        # No edited provenance, flag or source: a calibration comes only from its factories.
        for changes in (dict(validated=False), dict(provenance="operator_approved"), dict(source_id="frame:mirror")):
            with self.subTest(changes=changes), self.assertRaisesRegex(TypeError, "factory_required"):
                replace(cal, **changes)
        with self.assertRaisesRegex(ValueError, "synthetic_fixture_source_required"):
            synthetic.fixture_calibration("frame:mirror", cal.capture_format, "mono", cal.footprints)

    def test_mask_keeps_its_own_hardware_mapping_gate_behind_a_forced_validated_flag(self):
        """Defence in depth: even if ``validated`` were compromised, provenance and source still refuse."""
        cal = calibration()
        # Test-only copy with a hardware source id; production code has no path to build one.
        hardware = object.__new__(hud.Calibration)
        for name in hud.Calibration.__dataclass_fields__:
            object.__setattr__(hardware, name, getattr(cal, name))
        object.__setattr__(hardware, "source_id", "frame:mirror")
        with patch.object(hud.Calibration, "validated", property(lambda self: True)):
            for candidate in (unvalidated_calibration(), hardware):
                with self.subTest(provenance=candidate.provenance, source=candidate.source_id), \
                        self.assertRaisesRegex(ValueError, "hud_hardware_mapping_not_approved"):
                    hud.HudMask(candidate, source_kind="synthetic")
            hud.HudMask(cal, source_kind="synthetic")  # The fixture itself still passes the gate.

    def test_invalid_regions_margins_and_missing_eye_are_rejected(self):
        for footprint in (hud.Footprint(0, (-1, 10, 40, 40), 8),
                          hud.Footprint(0, (2, 2, 30, 30), 8),
                          hud.Footprint(0, (10, 10, 30, 30), 0),
                          hud.Footprint(0, (10, 10, 10, 30), 8),
                          hud.Footprint(0, (1.5, 10, 30, 30), 8),
                          hud.Footprint(0, (20, 20, 1260, 700), 8),
                          hud.Footprint(1, (10, 10, 30, 30), 8)):
            with self.subTest(footprint=footprint), self.assertRaises(ValueError):
                hud.HudMask(calibration(footprints=(footprint,)), source_kind="synthetic")
        stereo = calibration(layout="stereo_sbs")
        with self.assertRaises(ValueError):
            hud.HudMask(calibration(layout="stereo_sbs", footprints=stereo.footprints[:1]), source_kind="synthetic")

    def test_format_layout_identity_config_changes_reject_before_resize(self):
        cal = calibration()
        mask = hud.HudMask(cal, source_kind="synthetic")
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        changes = (dict(source_id="synthetic:restarted"), dict(capture_layout="stereo_sbs"),
            dict(config=replace(hud.HUD_CONFIG, x_m=0.4)),
            dict(capture_format=replace(cal.capture_format, width=1279)),
            dict(capture_format=replace(cal.capture_format, fourcc="RGB3")),
            dict(capture_format=replace(cal.capture_format, stride_bytes=3844)),
            dict(capture_format=replace(cal.capture_format, metadata=(("colorspace_code", 1),))))
        with patch.object(cv2, "resize", side_effect=AssertionError("must validate first")) as resize:
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    prepare(mask, image, **change)
        resize.assert_not_called()
        with self.assertRaises(FrozenInstanceError):
            mask.config = replace(hud.HUD_CONFIG, x_m=0.4)
        with self.assertRaises(FrozenInstanceError):
            cal.footprints[0].margin_px = 0

    def test_detection_filter_removes_mask_margin_invalid_boxes_and_cross_eye_boxes(self):
        mask = hud.HudMask(calibration(layout="stereo_sbs"), source_kind="synthetic")
        h, w = mask.inference_shape()
        x0, y0, x1, y1 = mask.regions[0]
        scale = w / mask.calibration.capture_format.width
        box = [x0 * scale, y0 * scale, (x0 + 2) * scale, (y0 + 2) * scale]
        inputs = [detected("mask", 1, box),
            detected("cross-eye", 1, [w/2-10, 5, w/2+10, 20]),
            detected("low", .1, [10, 10, 20, 20]),
            detected("bad", float("nan"), [10, 10, 20, 20]),
            detected("outside", 1, [-1, 0, 20, 20]),
            {"label": "untyped", "confidence": 1, "box_xyxy": [10, 10, 20, 20]},
            detected("good", .9, [10, 10, 20, 20])]
        rows = mask.rows_for(inputs, (h, w), .5)
        self.assertEqual([row.label for row in rows], ["good"])
        self.assertEqual(rows[0].direction, (-1, -1))
        with self.assertRaises(ValueError):
            mask.rows_for(inputs, (h, w + 1), .5)

    def test_hud_is_bounded_metadata_only_and_has_no_stale_label_memory(self):
        mask = hud.HudMask(calibration(), source_kind="synthetic")
        detections = [detected("W" * 100 + "\n<scene>", .99, [5, 5, 50, 50]),
                      detected("chair", .9, [450, 240, 480, 280]),
                      detected("bottle", .8, [800, 20, 830, 50]),
                      detected("person", .7, [20, 350, 50, 380])]
        tile, rows = mask.render(detections, mask.inference_shape(), .5)
        self.assertEqual(tile.shape, (128, 320, 3))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(isinstance(row, hud.HudRow) and len(row.label) <= 20 for row in rows))
        self.assertFalse(tile[:4].any() or tile[-4:].any() or tile[:, :4].any() or tile[:, -4:].any())
        self.assertFalse(tile.flags.writeable)
        w, h, rgba = rgba_from_bgr(tile)
        self.assertEqual((w, h), (320, 128))
        self.assertTrue(all(alpha == 255 for alpha in rgba[3::4]))
        empty, current = mask.render([], mask.inference_shape(), .5)
        self.assertEqual(current, ())
        self.assertFalse(empty[25:].any())

    def test_eye_relative_directions_deduplicate_without_world_geometry(self):
        mask = hud.HudMask(calibration(layout="stereo_sbs"), source_kind="synthetic")
        detections = [detected("chair", .8, [10, 10, 20, 20]), detected("chair", .9, [490, 10, 500, 20])]
        self.assertEqual(mask.rows_for(detections, mask.inference_shape(), .5),
                         (hud.HudRow("chair", .9, (-1, -1)),))


class HudIntegrationTests(unittest.TestCase):
    def test_pipeline_absent_or_unvalidated_calibration_never_opens_or_infers(self):
        for cal in (None, unvalidated_calibration()):
            source, sink, detector = synthetic.SyntheticSource(), synthetic.HeadlessSink(), synthetic.FixtureDetector()
            with patch.object(source, "open") as opened, patch.object(sink, "open") as shown, \
                    patch.object(detector, "detect") as detect:
                report = run_hud(source, detector, sink, .2, cal)
            opened.assert_not_called()
            shown.assert_not_called()
            detect.assert_not_called()
            self.assertEqual(report["status"], "error")

    def test_pipeline_inference_sees_mask_and_sink_receives_only_hud(self):
        cal = synthetic.synthetic_hud_calibration()
        class Detector(synthetic.FixtureDetector):
            def detect(inner, image, threshold):
                self.assertFalse(image[34:64, 59:96].any())
                self.assertFalse(image.flags.writeable)
                return DetectionResult((detected("chair", .9, [10, 10, 20, 20]),), 0.0)
        class Sink(synthetic.HeadlessSink):
            def submit(inner, processed):
                frame = processed.frame
                self.assertEqual(processed.image.shape, (128, 320, 3))
                self.assertEqual(processed.labels, ["chair"])
                self.assertEqual(processed.hud.rows, (hud.HudRow("chair", .9, (-1, -1)),))
                self.assertEqual(processed.hud.config, hud.HUD_CONFIG)
                self.assertEqual(processed.output_space, "approximate_image_view_direction")
                self.assertLessEqual(processed.expires_ns, frame.observed_ns + 1000000000)
                self.assertGreater(processed.expires_ns, frame.observed_ns)
                self.assertEqual(frame.source, cal.source_id)
                super().submit(processed)
        report = run_hud(synthetic.SyntheticSource(), Detector(), Sink(), .3, cal)
        self.assertEqual(report["status"], "completed", report)
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["hardware_mapping_verified"])
        self.assertFalse(report["feedback_exclusion_verified"])
        self.assertFalse(report["world_locked"])

    def test_worker_report_cannot_upgrade_synthetic_calibration_to_hardware_evidence(self):
        report = run_hud(synthetic.SyntheticSource(), synthetic.FixtureDetector(), synthetic.HeadlessSink(), .1,
                         synthetic.synthetic_hud_calibration())
        report["vr_runtime_initialized"] = False
        command.validate_worker_report(report, report["exit_code"])
        for key, value in (("hardware_mapping_verified", True), ("world_locked", True),
                           ("calibration_scope", "hardware"), ("hud_config_id", "changed")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                command.validate_worker_report(dict(report, **{key: value}), report["exit_code"])

    def test_changed_source_layout_or_format_prevents_first_inference(self):
        for mode in ("source", "layout", "format", "config"):
            with self.subTest(mode=mode):
                source = synthetic.SyntheticSource()
                cal = synthetic.synthetic_hud_calibration()
                if mode == "source":
                    source.source_id = "synthetic:another"
                if mode == "layout":
                    source.capture_layout = "stereo_sbs"
                if mode == "format":
                    cal = synthetic.fixture_calibration(cal.source_id, replace(cal.capture_format, fourcc="RGB3"),
                                                        cal.capture_layout, cal.footprints)
                config = replace(hud.HUD_CONFIG, x_m=.4) if mode == "config" else hud.HUD_CONFIG
                detector = synthetic.FixtureDetector()
                with patch.object(detector, "detect") as detect:
                    report = run_hud(source, detector, synthetic.HeadlessSink(), .2, cal, config=config)
                detect.assert_not_called()
                self.assertEqual(report["status"], "error", report)

    def test_layout_changed_during_inference_drops_result(self):
        source = synthetic.SyntheticSource()
        class Detector(synthetic.FixtureDetector):
            def detect(self, *args):
                source.capture_layout = "stereo_sbs"
                return super().detect(*args)
        report = run_hud(source, Detector(), synthetic.HeadlessSink(), .2, synthetic.synthetic_hud_calibration())
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["frames_submitted"], 0)
        self.assertTrue(report["cleanup_ok"])

    def test_native_format_changed_while_inference_runs_drops_result(self):
        entered, closed = threading.Event(), threading.Event()
        class Source(synthetic.SyntheticSource):
            def read(self, *args, **kwargs):
                if entered.is_set():
                    return Frame.owned_bgr(np.zeros((32, 96, 3), np.uint8), 2, time.monotonic_ns(), self.source_id)
                return super().read(*args, **kwargs)
            def close(self):
                closed.set()
                return super().close()
        class Detector(synthetic.FixtureDetector):
            def detect(self, *args):
                entered.set()
                if not closed.wait(3):
                    raise AssertionError("format-change fixture did not close")
                return super().detect(*args)
        report = run_hud(Source(), Detector(), synthetic.HeadlessSink(), .5, synthetic.synthetic_hud_calibration())
        self.assertEqual(report["frames_submitted"], 0)
        self.assertEqual("native_capture_format_changed", report["reason"])
        self.assertTrue(report["cleanup_ok"])

    def test_slow_hud_result_is_not_submitted(self):
        class Slow(synthetic.FixtureDetector):
            def detect(self, *args):
                time.sleep(.08)
                return super().detect(*args)
        report = run_hud(synthetic.SyntheticSource(), Slow(), synthetic.HeadlessSink(), .25,
                         synthetic.synthetic_hud_calibration(), max_age_ms=30)
        self.assertGreater(report["stale_results_dropped"], 0)
        self.assertEqual(report["frames_submitted"], 0)
        self.assertTrue(report["cleanup_ok"])

    def test_hud_requires_fixed_sink_and_cli_never_enables_live_source(self):
        detector = synthetic.FixtureDetector()
        with patch.object(detector, "detect") as detect, patch.object(OverlaySink, "open") as opened:
            report = run_hud(synthetic.SyntheticSource(), detector, OverlaySink(), .2,
                             synthetic.synthetic_hud_calibration())
        opened.assert_not_called()
        detect.assert_not_called()
        self.assertEqual(report["status"], "error")
        with patch.object(command, "supervise_synthetic") as run, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            command.main(["--synthetic-hud"])
        run.assert_not_called()

    def test_raw_mirror_rgb_padding_is_masked_at_native_resolution(self):
        cal = calibration(fourcc="RGB3", padding=8)
        fmt = {key: getattr(cal.capture_format, key) for key in hud.CaptureFormat.__dataclass_fields__ if key != "metadata"}
        pixels = np.full((720, cal.capture_format.stride_bytes), 199, dtype=np.uint8)
        image = np.ndarray((720, 1280, 3), dtype=np.uint8, buffer=pixels, strides=(cal.capture_format.stride_bytes, 3, 1))
        image[:] = (10, 20, 30)
        source = type("Source", (), dict(kind="synthetic", source_id=cal.source_id, capture_layout="mono"))()
        stage = hud.HudStage(source, cal)
        stage.start()
        frame = Frame(pixels.tobytes(), "RGB3", 1280, 720, cal.capture_format.stride_bytes, cal.source_id, 1,
                      time.monotonic_ns(), metadata={"native_format": fmt})
        result, unmasked = stage.prepare(frame)
        self.assertEqual(unmasked, 0)
        self.assertEqual(result[0, 0].tolist(), [30, 20, 10])
        self.assertEqual(result.shape, (540, 960, 3))
        self.assertFalse(result.flags.writeable)
        x0, y0, x1, y1 = (round(value * 0.75) for value in stage.mask.regions[0])
        self.assertFalse(result[y0 + 1:y1 - 1, x0 + 1:x1 - 1].any())
        changed = replace(frame, metadata={"native_format": dict(fmt, colorspace_code=1)})
        with self.assertRaisesRegex(ValueError, "format_changed"):
            stage.prepare(changed)

    def test_mirror_hud_mode_refuses_default_live_backend_before_model_or_open(self):
        factory = Mock()
        report, preview = recognize({}, .2, threading.Event(), hud_mode=True,
            calibration=calibration(), detector_factory=factory)
        factory.assert_not_called()
        self.assertIsNone(preview)
        self.assertEqual(report["status"], "error")
        self.assertIn("hud_live_mirror_not_approved", report["error"])
        for cal in (None, unvalidated_calibration()):
            backend = Mock(kind="synthetic", source_id="synthetic:mirror", capture_layout="mono")
            factory = Mock()
            report, _ = recognize({}, .2, threading.Event(), hud_mode=True, calibration=cal,
                                  backend_factory=backend, detector_factory=factory)
            backend.assert_not_called()
            factory.assert_not_called()
            self.assertEqual(report["status"], "error")

    def test_mirror_hud_generated_native_input_and_changed_format(self):
        cal = calibration()
        class Source(synthetic.SyntheticSession):
            kind, source_id, capture_layout = "synthetic", cal.source_id, "mono"
            bad = False
            closed = False
            def __init__(self, _):
                self.capabilities = {"driver": "synthetic"}
                self.format = {key: getattr(cal.capture_format, key)
                    for key in hud.CaptureFormat.__dataclass_fields__ if key != "metadata"}
                if self.bad:
                    self.format["colorspace_code"] = 1
                self.data = bytes([63]) * cal.capture_format.buffer_bytes
            def open(self):
                type(self).closed = False
            def read_frame(self, timeout):
                return self.data
            def close(self):
                type(self).closed = True
        class Detector(synthetic.FixtureDetector):
            def detect(inner, image, threshold):
                self.assertEqual(image.shape, (540, 960, 3))
                self.assertFalse(image[490:520, 890:940].any())
                self.assertEqual(int(image[0, 0, 0]), 63)
                return DetectionResult((detected("chair", .9, [10, 10, 20, 20]),), 0.0)
        detector = Detector()
        report, tile = recognize({"node": "synthetic:only"}, .3, threading.Event(),
            want_preview=True, hud_mode=True, calibration=cal, backend_factory=Source,
            detector_factory=lambda: detector)
        self.assertEqual(report["status"], "recognized", report)
        self.assertTrue(Source.closed)
        self.assertEqual(tile.shape, (128, 320, 3))
        self.assertEqual(report["last_result"]["labels"], ["chair"])
        self.assertEqual(report["last_result"]["box_coordinate_space"], "approximate_image_view_direction")
        self.assertFalse(report["display_started"])
        Source.bad = True
        with patch.object(detector, "detect") as detect:
            report, tile = recognize({"node": "synthetic:only"}, .3, threading.Event(),
                hud_mode=True, calibration=cal, backend_factory=Source, detector_factory=lambda: detector)
        detect.assert_not_called()
        self.assertTrue(Source.closed)
        self.assertEqual(report["status"], "error")
        self.assertIsNone(tile)

    def test_mirror_hud_source_kind_change_during_inference_blocks_output(self):
        cal = calibration()
        class Source(synthetic.SyntheticSession):
            kind, source_id, capture_layout = "synthetic", cal.source_id, "mono"
            def __init__(self, _):
                self.capabilities = {"driver": "synthetic"}
                self.format = {key: getattr(cal.capture_format, key)
                    for key in hud.CaptureFormat.__dataclass_fields__ if key != "metadata"}
                self.data = bytes([63]) * cal.capture_format.buffer_bytes
            def open(self):
                pass
            def read_frame(self, timeout):
                return self.data
            def close(self):
                pass
        class Detector(synthetic.FixtureDetector):
            def detect(inner, image, threshold):
                Source.kind = "live_camera"  # The approved declaration is pinned at start.
                return super().detect(image, threshold)
        report, tile = recognize({"node": "synthetic:only"}, .3, threading.Event(), want_preview=True,
            hud_mode=True, calibration=cal, backend_factory=Source, detector_factory=Detector)
        self.assertEqual(report["status"], "error", report)
        self.assertIn("source_or_session_changed", report["error"])
        self.assertNotIn("last_result", report)
        self.assertIsNone(tile)

    def test_frozen_transport_rejects_geometry_changes_before_queueing(self):
        client = OverlayClient(validation_only=True)
        settings = PanelSettings(width=.32, distance=1.25, x=.55, y=-.35, opacity=1, visible=False)
        client.freeze_settings(settings)
        for field, value in (("width", .4), ("distance", 1.3), ("x", .4), ("y", -.2), ("opacity", .8)):
            with self.subTest(field=field):
                changed = replace(settings, **{field: value})
                with self.assertRaisesRegex(ValueError, "overlay_settings_frozen"):
                    client.set_settings(changed)
                with self.assertRaisesRegex(ValueError, "overlay_settings_frozen"):
                    client.freeze_settings(changed)
                self.assertEqual(client.settings, settings)
        client.set_settings(replace(settings, visible=True))
        self.assertTrue(client.settings.visible)
        real = OverlayClient(validation_only=False)
        with self.assertRaisesRegex(ValueError, "fixed_live_hud_not_approved"):
            real.freeze_settings(settings)
        self.assertIsNone(real.process)


@unittest.skipUnless(os.name == "posix" and DEFAULT_BRIDGE.is_file(), "Linux native validation bridge required")
class HudNativeTests(unittest.TestCase):
    def test_hud_native_transport_fixed_placement_expiry_and_clean_shutdown(self):
        sink = OverlaySink(hud=hud.HUD_CONFIG)
        report = run_hud(synthetic.SyntheticSource(), synthetic.FixtureDetector(), sink, .3,
                         synthetic.synthetic_hud_calibration())
        self.assertEqual(report["status"], "completed", report)
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual(sink.client.settings, PanelSettings(width=.32, distance=1.25, x=.55, y=-.35,
                                                           opacity=1, visible=False))
        self.assertFalse(sink.report["native_report"]["runtime_initialized"])
        self.assertEqual(sink.report["native_report"]["frames_submitted"], 0)
        self.assertGreater(sink.report["native_report"]["expiring_frames_received"], 0)

    def test_placement_change_during_inference_is_rejected_before_submission(self):
        sink = OverlaySink(hud=hud.HUD_CONFIG)
        class Detector(synthetic.FixtureDetector):
            def detect(self, *args):
                sink.client.set_settings(replace(sink.client.settings, x=.4))
                return super().detect(*args)
        report = run_hud(synthetic.SyntheticSource(), Detector(), sink, .3, synthetic.synthetic_hud_calibration())
        self.assertEqual(report["status"], "error")
        self.assertIn("overlay_settings_frozen", report["reason"])
        self.assertEqual(report["frames_submitted"], 0)
        self.assertEqual(sink.report["native_report"]["frames_received"], 0)
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(sink.client.settings.visible)

    def test_stalled_inference_expires_hud_without_python_clear(self):
        sink = OverlaySink(hud=hud.HUD_CONFIG)
        blocked, release, stop = threading.Event(), threading.Event(), threading.Event()
        class Detector(synthetic.FixtureDetector):
            calls = 0
            def detect(self, *args):
                self.calls += 1
                if self.calls == 2:
                    blocked.set()
                    release.wait(5)
                return super().detect(*args)
        report = {}
        thread = threading.Thread(target=lambda: report.update(run_hud(synthetic.SyntheticSource(),
            Detector(), sink, 5, synthetic.synthetic_hud_calibration(), stop=stop, max_fps=10, max_age_ms=500)))
        thread.start()
        try:
            self.assertTrue(blocked.wait(4))
            self.assertTrue(sink.client.frame_expired.wait(3))
            self.assertTrue(thread.is_alive())
        finally:
            stop.set()
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual(report["frames_submitted"], 1)
        self.assertEqual(sink.report["native_report"]["expired_frames"], 1)
