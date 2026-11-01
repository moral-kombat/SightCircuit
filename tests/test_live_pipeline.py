"""Owned-frame and bounded-process tests using generated images only."""
import contextlib
from dataclasses import replace
import importlib.util
import io
import threading
import time
import unittest
from unittest.mock import patch

from framejacker.frame import Frame, native_format
from framejacker.overlay import OverlaySink
from framejacker.runtime import synthetic
from scripts import live_pipeline as live


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for generated image tests")
class LivePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy  # noqa: F401 Keep dependency import outside the deliberately short test run windows.

    def frame(self, value=1, sequence=1, source="synthetic:test", observed=None):
        import numpy as np
        return Frame.owned_bgr(np.full((32, 48, 3), value, dtype=np.uint8), sequence,
            time.monotonic_ns() if observed is None else observed, source)

    def assert_legacy_fields_follow_the_block(self, report, aliases):
        block = report["pipeline"]
        for name, source in aliases.items():
            with self.subTest(field=name):
                self.assertEqual(report[name], source(block) if callable(source) else block[source])

    def test_owned_copy_is_independent_read_only_and_keeps_identity(self):
        first = self.frame(1)
        first.pixels.flags.writeable = True
        copied = first.owned()
        first.pixels[:] = 99
        first.metadata["native_format"]["width"] = 0
        self.assertEqual(int(copied.pixels[0, 0, 0]), 1)
        self.assertFalse(copied.pixels.flags.writeable)
        self.assertEqual(native_format(copied)["width"], 48)
        self.assertEqual((copied.sequence, copied.observed_ns, copied.source, copied.pixel_format, copied.stride),
                         (first.sequence, first.observed_ns, first.source, "BGR24", 48 * 3))

    def test_owned_keeps_immutable_bytes_and_freezes_mutable_buffers(self):
        raw = Frame(b"abc", "RGB3", 1, 1, 3, "mirror:observed", 1, 1)
        self.assertIs(raw.owned().pixels, raw.pixels)
        storage = bytearray(b"abc")
        frozen = replace(raw, pixels=storage).owned()
        storage[:] = b"xyz"
        self.assertEqual(frozen.pixels, b"abc")

    def test_engine_owns_published_pixels_when_a_source_reuses_its_buffer(self):
        import numpy as np

        class Reusing(synthetic.SyntheticSource):
            def __init__(source):
                super().__init__(rate=100)
                source.buffer = np.zeros((64, 96, 3), np.uint8)

            def read(source, stop, timeout=0.05):
                frame = super().read(stop, timeout)
                if frame is None:
                    return None
                source.buffer[:] = frame.sequence % 255
                return replace(frame, pixels=source.buffer)

        class Detector(synthetic.FixtureDetector):
            def detect(inner, image, threshold):
                time.sleep(.03)  # The source overwrites its buffer meanwhile.
                return super().detect(image, threshold)

        class Sink(synthetic.HeadlessSink):
            def submit(sink, processed):
                if int(processed.image[0, 0, 0]) != processed.frame.sequence % 255:
                    raise AssertionError("presented pixels do not belong to their frame")
                super().submit(processed)

        report = live.run_pipeline(Reusing(), Detector(), Sink(), .4)
        self.assertEqual(report["status"], "completed", report)
        self.assertGreater(report["frames_submitted"], 1)

    def test_source_restart_and_identity_change_are_rejected(self):
        for change, reason in (("restart", "frame_sequence_regressed"), ("identity", "source_or_session_changed")):
            class Source(synthetic.SyntheticSource):
                def read(source, stop, timeout=0.05):
                    frame = super().read(stop, timeout)
                    if frame is None or frame.sequence < 2:
                        return frame
                    sequence, source_id = (1, frame.source) if change == "restart" else (frame.sequence, "synthetic:other")
                    return Frame.owned_bgr(frame.pixels, sequence, frame.observed_ns, source_id)
            with self.subTest(change=change):
                report = live.run_pipeline(Source(rate=100), synthetic.FixtureDetector(), synthetic.HeadlessSink(), .3)
                self.assertEqual(report["status"], "error", report)
                self.assertEqual(report["reason"], reason)
                self.assertTrue(report["cleanup_ok"])

    def test_invalid_dimensions_dtype_and_metadata_are_rejected(self):
        import numpy as np
        for image in (np.zeros((1, 1), dtype=np.uint8), np.zeros((4097, 1, 3), dtype=np.uint8),
                      np.zeros((1, 1, 3), dtype=np.float32)):
            with self.assertRaises(ValueError):
                Frame.owned_bgr(image, 1, 1, "synthetic:test")
        for identity in ((0, 1, "synthetic:test"), (1, -1, "synthetic:test"), (1, 1, ""), (1, 1, "s" * 97)):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                Frame.owned_bgr(np.zeros((1, 1, 3), dtype=np.uint8), *identity)

    def test_hardware_source_is_blocked_before_open(self):
        source = synthetic.SyntheticSource()
        source.kind = "live_camera"
        with patch.object(source, "open") as opened, self.assertRaisesRegex(ValueError, "live_image_source_not_approved"):
            live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.1)
        opened.assert_not_called()

    def test_native_padded_stride_is_owned_then_fully_masked_before_inference_resize(self):
        import cv2
        import numpy as np
        from framejacker.hud import Calibration, CaptureFormat, Footprint

        width, height, stride = 1920, 1080, 5824
        storage = np.full((height, stride), 231, np.uint8)
        native = np.ndarray((height, width, 3), dtype=np.uint8, buffer=storage,
                            strides=(stride, 3, 1))
        native[:] = 17
        native[850:1020, 1500:1820] = 255
        owned = Frame.owned_bgr(native, 1, time.monotonic_ns(), "synthetic:native")
        expected_native = native.copy()
        expected_native[842:1028, 1492:1828] = 0
        expected = cv2.resize(expected_native, (960, 540), interpolation=cv2.INTER_AREA)
        storage[:] = 99  # Reusing the padded acquisition buffer cannot change the owned frame.
        self.assertEqual(native_format(owned)["stride_bytes"], stride)
        self.assertEqual((owned.pixels.strides[0], owned.stride), (width * 3, width * 3))

        class Source(synthetic.SyntheticSource):
            source_id = "synthetic:native"
            sent = False

            def read(self, stop, timeout=.05):
                if self.sent:
                    stop.wait(timeout)
                    return None
                self.sent = True
                return replace(owned, observed_ns=time.monotonic_ns())

        class Detector(synthetic.FixtureDetector):
            def detect(detector, image, threshold):
                np.testing.assert_array_equal(image, expected)
                self.assertFalse(image.flags.writeable)
                return super().detect(image, threshold)

        calibration = Calibration("synthetic:native", CaptureFormat.from_mapping(native_format(owned)),
                                  "mono", (Footprint(0, (1500, 850, 1820, 1020), 8),),
                                  validated=True, provenance="synthetic_fixture")
        report = live.run_pipeline(Source(), Detector(), synthetic.HeadlessSink(), .5,
                                   hud_mode=True, calibration=calibration)
        self.assertEqual(report["frames_submitted"], 1, report)
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual(report["last_result"]["native_format"]["stride_bytes"], stride)

    def test_source_and_native_context_change_during_inference_fail_closed_without_hud(self):
        for change in ("source_id", "capture_layout", "session_id", "tracking_origin",
                       "recenter_generation", "orientation", "crop", "recenter_epoch", "timebase_id",
                       "orientation_degrees", "crop_xyxy", "hud_config_sha256"):
            source = synthetic.SyntheticSource()

            class Detector(synthetic.FixtureDetector):
                def detect(self, *args):
                    setattr(source, change, "changed")
                    return super().detect(*args)

            report = live.run_pipeline(source, Detector(), synthetic.HeadlessSink(), .2)
            self.assertEqual(report["status"], "error", report)
            self.assertEqual(report["frames_submitted"], 0)
            self.assertTrue(report["cleanup_ok"])

    def test_context_change_during_hud_render_is_checked_at_submission_boundary(self):
        source = synthetic.SyntheticSource(rate=1)
        original = live.HudPipelineAdapter.render

        def render(adapter, *args):
            result = original(adapter, *args)
            source.session_id = "changed-during-render"
            return result

        with patch.object(live.HudPipelineAdapter, "render", render):
            report = live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), .2,
                                       hud_mode=True, calibration=synthetic.synthetic_hud_calibration())
        self.assertEqual(report["status"], "error", report)
        self.assertEqual(report["frames_submitted"], 0)
        self.assertEqual(report["reason"], "source_or_session_changed")
        self.assertTrue(report["cleanup_ok"])

    def test_original_observation_and_run_deadline_bound_result_lifetime(self):
        class Sink(synthetic.HeadlessSink):
            def submit(sink, processed):
                frame = processed.frame
                self.assertGreater(processed.expires_ns, frame.observed_ns)
                self.assertLessEqual(processed.expires_ns, frame.observed_ns + 1_000_000_000)
                sink.expiry = processed.expires_ns
                super().submit(processed)

        sink = Sink()
        started = time.monotonic_ns()
        report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(), sink, .2)
        self.assertEqual(report["status"], "completed", report)
        self.assertLessEqual(sink.expiry, time.monotonic_ns())
        self.assertGreater(sink.expiry, started)
        self.assertFalse(report["physical_recovery_confirmed"])
        self.assertGreaterEqual(report["total_processing_ms"], report["total_inference_ms"])

    def test_engine_annotates_an_owned_copy_and_submits_one_processed_record(self):
        import numpy as np
        from framejacker.frame import Detection, DetectionResult, Processed

        seen = {}

        class Detector(synthetic.FixtureDetector):
            def detect(self, image, threshold):
                seen["input"] = image
                return DetectionResult((Detection("fixture", .9, (1, 1, 20, 20)),), 2.5)

        class Sink(synthetic.HeadlessSink):
            def submit(sink, processed):
                seen["processed"] = processed
                super().submit(processed)

        report = live.run_pipeline(synthetic.SyntheticSource(rate=1), Detector(), Sink(), .2)
        self.assertEqual(report["status"], "completed", report)
        processed = seen["processed"]
        self.assertIsInstance(processed, Processed)
        self.assertEqual(processed.labels, ["fixture"])
        self.assertEqual(processed.result.inference_ms, 2.5)
        self.assertEqual(processed.output_space, "detector_input_pixels")
        self.assertIsNone(processed.hud)
        self.assertEqual(processed.image.shape, seen["input"].shape)
        self.assertFalse(np.shares_memory(processed.image, seen["input"]))
        self.assertGreater(int(processed.image.max()), int(seen["input"].max()))
        self.assertEqual(report["last_result"]["box_coordinate_space"], "detector_input_pixels")

    def test_pipeline_matches_each_output_to_its_own_frame_and_cleans_up(self):
        class Sink(synthetic.HeadlessSink):
            def submit(self, processed):
                self.test.assertEqual(int(processed.image[0, 0, 0]), processed.frame.sequence % 255)
                super().submit(processed)
        sink = Sink()
        sink.test = self
        source = synthetic.SyntheticSource(rate=100)
        report = live.run_pipeline(source, synthetic.FixtureDetector(), sink, 0.5, max_fps=5)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["cleanup_ok"])
        self.assertTrue(source.closed)
        self.assertGreater(report["pending_inputs_replaced"], 0)
        self.assertGreater(report["frames_received"], report["frames_submitted"])
        self.assertFalse(report["live_input_verified"])
        from framejacker.runtime.live import LEGACY_FIELDS
        expected = {"frames_processed": "frames_processed", "frames_submitted": "frames_submitted",
                    "frames_received": "frames_received", "stale_inputs_dropped": "stale_inputs_skipped",
                    "stale_results_dropped": "stale_results_skipped", "pending_inputs_replaced": "pending_replaced",
                    "maximum_observation_age_ms": "max_output_age_ms", "total_processing_ms": "total_processing_ms",
                    "total_inference_ms": "total_inference_ms", "labels_seen": "labels_seen"}
        self.assertEqual(LEGACY_FIELDS, expected)  # Literal, so a mis-mapped alias fails here.
        self.assert_legacy_fields_follow_the_block(report, expected)
        # Rounded once from the stats; the block's rounded milliseconds agree within that rounding.
        self.assertAlmostEqual(report["elapsed_seconds"], report["pipeline"]["elapsed_ms"] / 1000, delta=0.001)

    def test_elapsed_seconds_is_rounded_once_from_the_stats(self):
        from framejacker.pipeline import Pipeline

        def run(pipeline, stop):
            # 1.0014996 s: rounding the block's 1001.5 ms again would give 1.002.
            pipeline.stats.started, pipeline.stats.finished = 10.0, 11.0014996
            pipeline.stats.frames_submitted = 1
            return pipeline.stats
        with patch.object(Pipeline, "run", run):
            report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(),
                                       synthetic.HeadlessSink(), 0.2)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(report["pipeline"]["elapsed_ms"], 1001.5)
        self.assertEqual(report["elapsed_seconds"], 1.001)

    def test_stale_input_and_future_observation_are_not_sent_to_sink(self):
        for age, expected in ((-2000000000, "no_frames"), (2000000000, "error")):
            source = synthetic.SyntheticSource()
            original = source.read
            def shifted(*args, **kwargs):
                frame = original(*args, **kwargs)
                if frame is None:
                    return None
                return replace(frame, observed_ns=frame.observed_ns + age)
            source.read = shifted
            report = live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.2)
            self.assertEqual(report["status"], expected)
            self.assertEqual(report["frames_submitted"], 0)
            self.assertTrue(report["cleanup_ok"])

    def test_slow_inference_does_not_present_stale_results(self):
        class Slow(synthetic.FixtureDetector):
            def detect(self, *args):
                time.sleep(0.15)
                return super().detect(*args)
        report = live.run_pipeline(synthetic.SyntheticSource(), Slow(), synthetic.HeadlessSink(), 0.6, max_age_ms=100)
        self.assertGreater(report["stale_results_dropped"], 0)
        self.assertEqual(report["frames_submitted"], 0)
        self.assertTrue(report["cleanup_ok"])

    def test_malformed_detector_result_still_closes_source_and_sink(self):
        from framejacker.frame import DetectionResult

        for bad in ({"image": None, "labels": [], "detections": [], "inference_ms": 0.0},
                    DetectionResult([], 0.0), DetectionResult((), float("nan")), DetectionResult((), -1.0),
                    DetectionResult(({"label": "dict"},), 0.0)):
            class Bad(synthetic.FixtureDetector):
                def detect(self, *args):
                    return bad
            with self.subTest(bad=bad):
                report = live.run_pipeline(synthetic.SyntheticSource(), Bad(), synthetic.HeadlessSink(), 0.2)
                self.assertEqual(report["status"], "error")
                self.assertEqual(report["reason"], "invalid_detection_result")
                self.assertEqual(report["frames_submitted"], 0)
                self.assertTrue(report["cleanup_ok"])

    def test_source_failure_is_reported_and_closed(self):
        source = synthetic.SyntheticSource()
        source.read = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("generated source failure"))
        report = live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.2)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["source_cleanup_ok"])

    def test_preexisting_cancel_does_not_open_source_or_sink(self):
        stop = threading.Event()
        stop.set()
        source, sink = synthetic.SyntheticSource(), synthetic.HeadlessSink()
        with patch.object(source, "open") as opened, patch.object(sink, "open") as displayed:
            report = live.run_pipeline(source, synthetic.FixtureDetector(), sink, 0.2, stop=stop)
        opened.assert_not_called()
        displayed.assert_not_called()
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_ok"])

    def test_overlay_adapter_requests_validation_only_and_checks_native_cleanup(self):
        class Client:
            def __init__(self, validation_only):
                self.validation_only, self.error, self.count = validation_only, None, 0
            def start(self):
                return self
            def set_settings(self, settings):
                self.visible = settings.visible
            def submit_bgr(self, image, *, expires_ns=None):
                assert expires_ns is not None
                self.count += 1
            def close(self):
                return {"validation_only": self.validation_only, "error": None,
                        "forced_termination": False, "worker_reaped": True, "worker_exit_code": 0,
                        "transport_threads_stopped": True, "native_report": {"cleanup_ok": True,
                        "exit_code": 0, "mode": "--validate-stdin", "runtime_initialized": False}}
        sink = OverlaySink(client_factory=Client)
        report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(), sink, 0.3)
        self.assertTrue(sink.client.validation_only)
        self.assertGreater(sink.client.count, 0)
        self.assertFalse(sink.client.visible)
        self.assertTrue(report["cleanup_ok"])

    def test_hide_failure_still_closes_sink(self):
        class Sink(synthetic.HeadlessSink):
            closed = False
            def clear(self):
                raise OSError("hide failed")
            def close(self):
                self.closed = True
                return True
        sink = Sink()
        report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(), sink, 0.2)
        self.assertTrue(sink.closed)
        self.assertTrue(report["sink_close_ok"])
        self.assertFalse(report["sink_clear_ok"])
        self.assertFalse(report["cleanup_ok"])
        self.assertEqual(report["status"], "cleanup_failed")

    def test_late_source_close_failure_is_not_lost(self):
        class Source(synthetic.SyntheticSource):
            def close(self):
                raise OSError("source close failed")
        report = live.run_pipeline(Source(), synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.2)
        self.assertFalse(report["cleanup_ok"])
        self.assertIn("source close failed", report["source_error"])

    def test_partial_sink_startup_still_attempts_both_cleanup_steps(self):
        class Sink(synthetic.HeadlessSink):
            cleared = closed = False
            def open(self):
                raise OSError("partial startup")
            def clear(self):
                self.cleared = True
            def close(self):
                self.closed = True
                return True
        sink = Sink()
        report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(), sink, 0.2)
        self.assertTrue(sink.cleared and sink.closed)
        self.assertEqual(report["status"], "error")

    def test_cancellation_during_sink_startup_does_not_open_source(self):
        stop = threading.Event()
        class Sink(synthetic.HeadlessSink):
            def open(self):
                super().open()
                stop.set()
        source = synthetic.SyntheticSource()
        with patch.object(source, "open") as opened:
            report = live.run_pipeline(source, synthetic.FixtureDetector(), Sink(), 0.2, stop=stop)
        opened.assert_not_called()
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_ok"])

    def test_worker_report_cannot_upgrade_hardware_or_cleanup_claims(self):
        report = live.run_pipeline(synthetic.SyntheticSource(), synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.1)
        report["vr_runtime_initialized"] = False
        for key in ("live_input_verified", "feedback_exclusion_verified", "hardware_trial_ready",
                    "vr_runtime_initialized"):
            with self.assertRaises(ValueError):
                live.validate_worker_report(dict(report, **{key: True}), report["exit_code"])
        with self.assertRaises(ValueError):
            live.validate_worker_report(report, 7)

    def test_default_does_not_start_pipeline_or_overlay(self):
        with patch.object(live, "supervise_synthetic") as supervised, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(live.main([]), 3)
        supervised.assert_not_called()
