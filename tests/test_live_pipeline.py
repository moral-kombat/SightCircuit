"""Owned-frame and bounded-process tests using generated images only."""
import contextlib
import importlib.util
import io
import threading
import time
import unittest
from unittest.mock import patch

from scripts import live_pipeline as live


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for generated image tests")
class LivePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy  # noqa: F401 Keep dependency import outside the deliberately short test run windows.

    def frame(self, value=1, sequence=1, source="synthetic:test", observed=None):
        import numpy as np
        return live.OwnedFrame.copy_from(np.full((32, 48, 3), value, dtype=np.uint8), sequence,
            time.monotonic_ns() if observed is None else observed, source)

    def test_owned_copy_survives_source_reuse_and_slot_replaces_old_input(self):
        slot = live.LatestSlot()
        first = self.frame(1)
        second = self.frame(2, 2)
        slot.publish(first)
        slot.publish(second)
        second.image.flags.writeable = True
        second.image[:] = 99
        result = slot.take()
        self.assertEqual(result.sequence, 2)
        self.assertEqual(int(result.image[0, 0, 0]), 2)
        self.assertFalse(result.image.flags.writeable)
        self.assertEqual(slot.replaced, 1)
        self.assertIsNone(slot.take(timeout=0))

    def test_source_restart_and_identity_change_are_rejected(self):
        slot = live.LatestSlot()
        slot.publish(self.frame(sequence=2))
        for frame in (self.frame(sequence=1), self.frame(sequence=3, source="synthetic:other")):
            with self.assertRaisesRegex(ValueError, "source_changed_or_sequence_regressed"):
                slot.publish(frame)

    def test_invalid_dimensions_dtype_and_metadata_are_rejected(self):
        import numpy as np
        for image in (np.zeros((1, 1), dtype=np.uint8), np.zeros((961, 1, 3), dtype=np.uint8),
                      np.zeros((1, 1, 3), dtype=np.float32)):
            with self.assertRaises(ValueError):
                live.OwnedFrame.copy_from(image, 1, 1, "synthetic:test")
        with self.assertRaises(ValueError):
            live.OwnedFrame.copy_from(np.zeros((1, 1, 3), dtype=np.uint8), 0, 1, "synthetic:test")

    def test_hardware_source_is_blocked_before_open(self):
        source = live.SyntheticSource()
        source.kind = "live_camera"
        with patch.object(source, "open") as opened, self.assertRaisesRegex(ValueError, "live_image_source_not_approved"):
            live.run_pipeline(source, live.FixtureDetector(), live.HeadlessSink(), 0.1)
        opened.assert_not_called()

    def test_pipeline_matches_each_output_to_its_own_frame_and_cleans_up(self):
        class Sink(live.HeadlessSink):
            def submit(self, frame, detection):
                self.test.assertEqual(int(detection["image"][0, 0, 0]), frame.sequence % 255)
                super().submit(frame, detection)
        sink = Sink()
        sink.test = self
        source = live.SyntheticSource(rate=100)
        report = live.run_pipeline(source, live.FixtureDetector(), sink, 0.5, max_fps=5)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["cleanup_ok"])
        self.assertTrue(source.closed)
        self.assertGreater(report["pending_inputs_replaced"], 0)
        self.assertGreater(report["frames_received"], report["frames_submitted"])
        self.assertFalse(report["live_input_verified"])

    def test_stale_input_and_future_observation_are_not_sent_to_sink(self):
        for age, expected in ((-2000000000, "no_frames"), (2000000000, "error")):
            source = live.SyntheticSource()
            original = source.read
            def shifted(*args, **kwargs):
                frame = original(*args, **kwargs)
                if frame is None:
                    return None
                return live.OwnedFrame(frame.image, frame.sequence, frame.observed_ns + age, frame.source_id)
            source.read = shifted
            report = live.run_pipeline(source, live.FixtureDetector(), live.HeadlessSink(), 0.2)
            self.assertEqual(report["status"], expected)
            self.assertEqual(report["frames_submitted"], 0)
            self.assertTrue(report["cleanup_ok"])

    def test_slow_inference_does_not_present_stale_results(self):
        class Slow(live.FixtureDetector):
            def detect(self, *args):
                time.sleep(0.15)
                return super().detect(*args)
        report = live.run_pipeline(live.SyntheticSource(), Slow(), live.HeadlessSink(), 0.6, max_age_ms=100)
        self.assertGreater(report["stale_results_dropped"], 0)
        self.assertEqual(report["frames_submitted"], 0)
        self.assertTrue(report["cleanup_ok"])

    def test_detector_shape_failure_still_closes_source_and_sink(self):
        class Bad(live.FixtureDetector):
            def detect(self, *args):
                result = super().detect(*args)
                result["image"] = result["image"][:1]
                return result
        report = live.run_pipeline(live.SyntheticSource(), Bad(), live.HeadlessSink(), 0.2)
        self.assertEqual(report["status"], "error")
        self.assertIn("does_not_match_source", report["reason"])
        self.assertTrue(report["cleanup_ok"])

    def test_source_failure_is_reported_and_closed(self):
        source = live.SyntheticSource()
        source.read = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("generated source failure"))
        report = live.run_pipeline(source, live.FixtureDetector(), live.HeadlessSink(), 0.2)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["source_cleanup_ok"])

    def test_preexisting_cancel_does_not_open_source_or_sink(self):
        stop = threading.Event()
        stop.set()
        source, sink = live.SyntheticSource(), live.HeadlessSink()
        with patch.object(source, "open") as opened, patch.object(sink, "open") as displayed:
            report = live.run_pipeline(source, live.FixtureDetector(), sink, 0.2, stop=stop)
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
        sink = live.OverlayValidationSink(client_factory=Client)
        report = live.run_pipeline(live.SyntheticSource(), live.FixtureDetector(), sink, 0.3)
        self.assertTrue(sink.client.validation_only)
        self.assertGreater(sink.client.count, 0)
        self.assertFalse(sink.client.visible)
        self.assertTrue(report["cleanup_ok"])

    def test_hide_failure_still_closes_sink(self):
        class Sink(live.HeadlessSink):
            closed = False
            def clear(self):
                raise OSError("hide failed")
            def close(self):
                self.closed = True
                return True
        sink = Sink()
        report = live.run_pipeline(live.SyntheticSource(), live.FixtureDetector(), sink, 0.2)
        self.assertTrue(sink.closed)
        self.assertTrue(report["sink_close_ok"])
        self.assertFalse(report["sink_clear_ok"])
        self.assertFalse(report["cleanup_ok"])
        self.assertEqual(report["status"], "cleanup_failed")

    def test_late_source_close_failure_is_not_lost(self):
        class Source(live.SyntheticSource):
            def close(self):
                raise OSError("source close failed")
        report = live.run_pipeline(Source(), live.FixtureDetector(), live.HeadlessSink(), 0.2)
        self.assertFalse(report["cleanup_ok"])
        self.assertIn("source close failed", report["source_error"])

    def test_partial_sink_startup_still_attempts_both_cleanup_steps(self):
        class Sink(live.HeadlessSink):
            cleared = closed = False
            def open(self):
                raise OSError("partial startup")
            def clear(self):
                self.cleared = True
            def close(self):
                self.closed = True
                return True
        sink = Sink()
        report = live.run_pipeline(live.SyntheticSource(), live.FixtureDetector(), sink, 0.2)
        self.assertTrue(sink.cleared and sink.closed)
        self.assertEqual(report["status"], "error")

    def test_cancellation_during_sink_startup_does_not_open_source(self):
        stop = threading.Event()
        class Sink(live.HeadlessSink):
            def open(self):
                super().open()
                stop.set()
        source = live.SyntheticSource()
        with patch.object(source, "open") as opened:
            report = live.run_pipeline(source, live.FixtureDetector(), Sink(), 0.2, stop=stop)
        opened.assert_not_called()
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_ok"])

    def test_worker_report_cannot_upgrade_hardware_or_cleanup_claims(self):
        report = live.run_pipeline(live.SyntheticSource(), live.FixtureDetector(), live.HeadlessSink(), 0.1)
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
