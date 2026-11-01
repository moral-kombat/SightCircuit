"""Shared frame pipeline, latest-item handoff and output helpers; OpenCV only where a test says so."""
import importlib.util
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest

from framejacker.errors import OutputError
from framejacker.frame import Detection, DetectionResult, Frame
from framejacker.latest import LatestSlot
from framejacker.pipeline import LatestResultSink, Pipeline
from framejacker.runtime import output, process
from framejacker.sources.mirror import MirrorSource


def frame(number, observed_ns=None):
    return Frame(number, "TEST", 1, 1, 1, "fixture", number,
                 time.monotonic_ns() if observed_ns is None else observed_ns)


class ListSource:
    """Emit numbered frames, optionally pausing between them."""

    def __init__(self, count, delay=0.0, age_ns=0):
        self.count, self.delay, self.age_ns = count, delay, age_ns
        self.stopped_by_pipeline = False

    def run(self, emit, stop):
        for number in range(1, self.count + 1):
            if stop.is_set():
                self.stopped_by_pipeline = True
                return
            emit(frame(number, time.monotonic_ns() - self.age_ns))
            time.sleep(self.delay)


class EchoDetector:
    """Report the frame number as a box coordinate, after an optional delay."""

    def __init__(self, delay=0.0):
        self.delay = delay

    def detect(self, image, threshold):
        time.sleep(self.delay)
        return DetectionResult((Detection("fixture", 1.0, (image, 0, image + 1, 1)),), self.delay * 1000)


class Recorder:
    def __init__(self):
        self.items = []

    def submit(self, processed):
        self.items.append(processed)


def pipeline(source, detector, **options):
    sink = Recorder()
    options.setdefault("max_fps", 1000)
    return Pipeline(source, detector, [sink], convert=lambda item: item.pixels,
                    annotator=lambda image, result: image, **options), sink


class LatestSlotTests(unittest.TestCase):
    def test_newer_item_replaces_pending_and_close_releases_waiters(self):
        slot = LatestSlot()
        slot.put("first")
        slot.put("second")
        self.assertEqual((slot.take(), slot.replaced), ("second", 1))
        self.assertIsNone(slot.take(0))
        slot.put("last")
        slot.close()
        self.assertEqual(slot.take(), "last")
        started = time.monotonic()
        self.assertIsNone(slot.take(5))
        self.assertLess(time.monotonic() - started, 1)


class PipelineTests(unittest.TestCase):
    def test_inline_mode_processes_every_frame_in_order(self):
        source = ListSource(5)
        runner, sink = pipeline(source, EchoDetector(), latest_only=False)
        stop = threading.Event()
        stats = runner.run(stop)
        self.assertEqual([item.frame.sequence for item in sink.items], [1, 2, 3, 4, 5])
        self.assertEqual([item.index for item in sink.items], [1, 2, 3, 4, 5])
        self.assertEqual(stats.frames_processed, 5)
        self.assertFalse(stats.cancelled)
        self.assertTrue(stop.is_set())

    def test_frame_limit_stops_the_source(self):
        for latest_only in (False, True):
            with self.subTest(latest_only=latest_only):
                source = ListSource(50, delay=0.01)
                runner, sink = pipeline(source, EchoDetector(), latest_only=latest_only, max_frames=3)
                stats = runner.run(threading.Event())
                self.assertEqual(stats.frames_processed, 3)
                self.assertFalse(stats.cancelled)
                self.assertFalse(runner.capture_thread_alive)

    def test_latest_mode_drops_replaced_input_and_keeps_association(self):
        runner, sink = pipeline(ListSource(40, delay=0.005), EchoDetector(delay=0.05), latest_only=True)
        stats = runner.run(threading.Event())
        self.assertGreater(stats.pending_replaced, 0)
        self.assertLess(stats.frames_processed, 40)
        for item in sink.items:
            self.assertEqual(item.result.detections[0].box_xyxy[0], item.frame.sequence)
        sequences = [item.frame.sequence for item in sink.items]
        self.assertEqual(sequences, sorted(sequences))

    def test_rate_cap_and_stale_input(self):
        runner, sink = pipeline(ListSource(30, delay=0.01), EchoDetector(), latest_only=True, max_fps=10)
        stats = runner.run(threading.Event())
        self.assertGreater(stats.frames_processed, 0)
        self.assertLessEqual(stats.frames_processed, int(stats.elapsed_s * 10) + 1)
        runner, sink = pipeline(ListSource(5, age_ns=2_000_000_000), EchoDetector(), latest_only=True,
                                max_input_age_ms=1000)
        stats = runner.run(threading.Event())
        self.assertEqual(stats.frames_processed, 0)
        self.assertGreater(stats.stale_inputs_skipped, 0)

    def test_external_stop_is_reported_as_cancelled(self):
        stop = threading.Event()
        runner, sink = pipeline(ListSource(1000, delay=0.01), EchoDetector(), latest_only=True)
        threading.Timer(0.1, stop.set).start()
        stats = runner.run(stop)
        self.assertTrue(stats.cancelled)
        self.assertFalse(runner.capture_thread_alive)

    def test_detector_and_source_failures_stop_capture_and_propagate(self):
        class Broken:
            def detect(self, image, threshold):
                raise RuntimeError("detector failed")

        source = ListSource(1000, delay=0.01)
        runner, _ = pipeline(source, Broken(), latest_only=True)
        stop = threading.Event()
        with self.assertRaisesRegex(RuntimeError, "detector failed"):
            runner.run(stop)
        self.assertTrue(stop.is_set())
        self.assertFalse(runner.capture_thread_alive)

        class Failing:
            def run(self, emit, stop):
                raise RuntimeError("source failed")

        runner, _ = pipeline(Failing(), EchoDetector(), latest_only=True)
        with self.assertRaisesRegex(RuntimeError, "source failed"):
            runner.run(threading.Event())

    def test_latest_result_sink_is_nonblocking(self):
        sink = LatestResultSink()
        self.assertIsNone(sink.take())
        sink.submit("one")
        sink.submit("two")
        self.assertEqual(sink.take(), "two")

    def test_post_inference_age_check_drops_results_in_both_scheduling_modes(self):
        for latest in (False, True):
            runner, sink = pipeline(ListSource(1), EchoDetector(.03), latest_only=latest,
                                    max_input_age_ms=10)
            stats = runner.run(threading.Event())
            self.assertEqual(stats.frames_processed, 1)
            self.assertEqual(stats.stale_results_skipped, 1)
            self.assertEqual(stats.frames_submitted, 0)
            self.assertEqual(sink.items, [])

    def test_context_validator_runs_again_after_detector_returns(self):
        context = {"valid": True}

        class Changing(EchoDetector):
            def detect(self, *args):
                result = super().detect(*args)
                context["valid"] = False
                return result

        def validate(frame):
            if not context["valid"]:
                raise ValueError("mapping_expired")

        runner, sink = pipeline(ListSource(1), Changing(), context_validator=validate)
        with self.assertRaisesRegex(ValueError, "mapping_expired"):
            runner.run(threading.Event())
        self.assertEqual(sink.items, [])

    def test_cancellation_during_inference_cannot_publish_output(self):
        stop = threading.Event()

        class Cancel(EchoDetector):
            def detect(self, *args):
                stop.set()
                return super().detect(*args)

        runner, sink = pipeline(ListSource(1), Cancel(), latest_only=False)
        stats = runner.run(stop)
        self.assertEqual(stats.stale_results_skipped, 1)
        self.assertEqual(sink.items, [])

    def test_processed_expiry_is_anchored_to_original_observation(self):
        runner, sink = pipeline(ListSource(1), EchoDetector(.01), max_input_age_ms=1000)
        runner.run(threading.Event())
        self.assertEqual(len(sink.items), 1)
        item = sink.items[0]
        self.assertEqual(item.expires_ns, item.frame.observed_ns + 1_000_000_000)
        self.assertGreaterEqual(item.output_age_ms, item.input_age_ms)
        self.assertIsNone(item.hud)
        self.assertEqual(item.output_space, "detector_input_pixels")
        self.assertEqual(item.labels, ["fixture"])

    def test_malformed_or_legacy_detector_output_never_reaches_a_sink(self):
        class Legacy:
            def detect(self, image, threshold):
                return {"image": image, "labels": [], "detections": [], "inference_ms": 0.0}

        class NonFinite:
            def detect(self, image, threshold):
                return DetectionResult((), float("inf"))

        for detector in (Legacy(), NonFinite()):
            for latest in (False, True):
                with self.subTest(detector=type(detector).__name__, latest=latest):
                    runner, sink = pipeline(ListSource(1), detector, latest_only=latest)
                    with self.assertRaisesRegex(ValueError, "invalid_detection_result"):
                        runner.run(threading.Event())
                    self.assertEqual(sink.items, [])

    def test_capture_format_change_invalidates_an_in_flight_result(self):
        entered, changed = threading.Event(), threading.Event()

        class Source:
            def run(self, emit, stop):
                emit(frame(1))
                if not entered.wait(2):
                    raise AssertionError("detector did not start")
                try:
                    emit(Frame(2, "CHANGED", 1, 1, 1, "fixture", 2, time.monotonic_ns()))
                finally:
                    changed.set()

        class Detector(EchoDetector):
            def detect(self, *args):
                entered.set()
                if not changed.wait(2):
                    raise AssertionError("capture did not observe changed format")
                return super().detect(*args)

        runner, sink = pipeline(Source(), Detector(), max_input_age_ms=1000)
        with self.assertRaisesRegex(ValueError, "native_capture_format_changed"):
            runner.run(threading.Event())
        self.assertEqual(sink.items, [])

    def test_future_observations_are_rejected_before_detector(self):
        for latest in (False, True):
            runner, sink = pipeline(ListSource(1, age_ns=-2_000_000_000), EchoDetector(), latest_only=latest)
            with self.assertRaisesRegex(ValueError, "frame_observation_is_in_the_future"):
                runner.run(threading.Event())
            self.assertEqual(sink.items, [])

    def test_latest_capture_cannot_hide_sequence_regression_by_replacing_pending(self):
        class Source:
            def run(self, emit, stop):
                emit(frame(2))
                emit(frame(1))

        runner, sink = pipeline(Source(), EchoDetector(), max_input_age_ms=1000)
        with self.assertRaisesRegex(ValueError, "frame_sequence_regressed"):
            runner.run(threading.Event())
        self.assertEqual(sink.items, [])

    def test_per_frame_telemetry_does_not_invalidate_native_context(self):
        class Source:
            def run(self, emit, stop):
                for number in (1, 2):
                    emit(Frame(number, "TEST", 1, 1, 1, "fixture", number, time.monotonic_ns(),
                               metadata={"schedule_lateness_ms": number * 10}))

        runner, sink = pipeline(Source(), EchoDetector(), latest_only=False, max_input_age_ms=1000)
        runner.run(threading.Event())
        self.assertEqual([item.frame.sequence for item in sink.items], [1, 2])

    def test_qualification_context_metadata_changes_during_inference_fail_closed(self):
        from dataclasses import replace

        for key in ("recenter_epoch", "timebase_id", "orientation_degrees", "crop_xyxy", "hud_config_sha256"):
            with self.subTest(key=key):
                observed = replace(frame(1), metadata={key: "original"})

                class Source:
                    def run(self, emit, stop):
                        emit(observed)

                class Detector(EchoDetector):
                    def detect(self, *args):
                        result = super().detect(*args)
                        observed.metadata[key] = "changed"
                        return result

                runner, sink = pipeline(Source(), Detector(), latest_only=False, max_input_age_ms=1000)
                with self.assertRaisesRegex(ValueError, "source_or_session_changed"):
                    runner.run(threading.Event())
                self.assertEqual(sink.items, [])

    def test_capture_invalidation_during_one_sink_blocks_the_next_sink(self):
        entered, changed = threading.Event(), threading.Event()

        class Source:
            def run(self, emit, stop):
                emit(frame(1))
                if not entered.wait(2):
                    raise AssertionError("first sink did not run")
                try:
                    emit(Frame(2, "CHANGED", 1, 1, 1, "fixture", 2, time.monotonic_ns()))
                finally:
                    changed.set()

        class First:
            def submit(self, processed):
                entered.set()
                if not changed.wait(2):
                    raise AssertionError("capture invalidation did not run")

        runner, sink = pipeline(Source(), EchoDetector(), max_input_age_ms=1000)
        runner.sinks = (First(), sink)
        with self.assertRaisesRegex(ValueError, "native_capture_format_changed"):
            runner.run(threading.Event())
        self.assertEqual(sink.items, [])

    def test_padded_array_frames_are_owned_before_any_checkpoint(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy required for array frames")
        storage = np.zeros((2, 16), np.uint8)
        view = np.ndarray((2, 4, 3), np.uint8, buffer=storage, strides=(16, 3, 1))

        class Source:
            def run(self, emit, stop):
                for number in (1, 2, 3):
                    view[:] = number  # The source reuses one padded buffer.
                    emit(Frame(view, "BGR24", 4, 2, 16, "fixture", number, time.monotonic_ns(),
                               metadata={"native_format": {"stride_bytes": 16}}))
                    time.sleep(.02)

        class Detector:
            def detect(self, image, threshold):
                time.sleep(.03)
                return DetectionResult((), 0.0)

        seen = []
        runner = Pipeline(Source(), Detector(), [Recorder()], max_fps=1000, max_input_age_ms=1000,
                          convert=lambda item: seen.append(item) or item.pixels,
                          annotator=lambda image, result: image)
        stats = runner.run(threading.Event())
        self.assertGreater(stats.frames_submitted, 0)
        for item in seen:
            self.assertEqual(item.stride, 12)
            self.assertFalse(np.shares_memory(item.pixels, storage))
            self.assertEqual(int(item.pixels[0, 0, 0]), item.sequence)

    def test_raw_buffers_keep_their_declared_stride_and_are_frozen(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy required for array frames")
        from array import array
        import ctypes
        flat = np.arange(32, dtype=np.uint8)
        writable = array("B", range(32))
        for pixels in (np.frombuffer(bytes(range(32)), np.uint8), flat, writable, bytearray(range(32))):
            with self.subTest(kind=type(pixels).__name__):
                owned = Frame(pixels, "RGB3", 4, 2, 16, "fixture", 1, 1).owned()
                self.assertEqual(owned.stride, 16)
                self.assertEqual(bytes(owned.pixels), bytes(range(32)))
                if not hasattr(owned.pixels, "strides"):
                    self.assertIs(type(owned.pixels), bytes)
                writable[0] = flat[0] = 99  # The source rewrites its buffer.
                self.assertEqual(bytes(owned.pixels)[0], 0)
                writable[0] = flat[0] = 0

        # The copy is of the bytes themselves; item formats with padding are never re-parsed.
        class Padded(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint8), ("b", ctypes.c_uint32)]

        padded = (Padded * 4)()
        ctypes.memmove(padded, bytes(range(1, 33)), 32)
        for pixels in (np.frombuffer(bytes(range(1, 33)), "V1").copy(), padded):
            with self.subTest(kind=type(pixels).__name__):
                owned = Frame(pixels, "RGB3", 4, 2, 16, "fixture", 1, 1).owned()
                self.assertEqual(memoryview(owned.pixels).tobytes(), bytes(range(1, 33)))

    @unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"),
                         "NumPy and OpenCV required for the real converter")
    def test_latest_only_engine_accepts_a_raw_array_buffer(self):
        import numpy as np

        class Source:
            def run(self, emit, stop):
                for number in (1, 2):
                    emit(Frame(np.frombuffer(bytes(32), np.uint8), "RGB3", 4, 2, 16, "fixture", number,
                               time.monotonic_ns()))

        class Detector:
            def detect(self, image, threshold):
                return DetectionResult((), 0.0)

        # The real converter reads the raw buffer with the frame's declared stride.
        stats = Pipeline(Source(), Detector(), [Recorder()], max_fps=1000, max_input_age_ms=1000,
                         annotator=lambda image, result: image).run(threading.Event())
        self.assertGreater(stats.frames_submitted, 0)

    def test_pending_replaced_is_current_while_the_run_is_going(self):
        observed = []

        class Sink:
            def submit(self, processed):
                observed.append(runner.stats.pending_replaced)

        runner = Pipeline(ListSource(40, delay=0.002), EchoDetector(delay=0.02), [Sink()], max_fps=1000,
                          convert=lambda item: item.pixels, annotator=lambda image, result: image)
        stats = runner.run(threading.Event())
        self.assertGreater(max(observed), 0)
        self.assertLessEqual(max(observed), stats.pending_replaced)

    def test_publishing_a_non_frame_latches_the_context_reason(self):
        class Source:
            def run(self, emit, stop):
                emit({"pixels": b""})

        runner = Pipeline(Source(), EchoDetector(), [Recorder()], max_input_age_ms=1000)
        with self.assertRaisesRegex(ValueError, "capture_frame_type_unsupported"):
            runner.run(threading.Event())
        self.assertEqual(runner.capture_context.failure, "capture_frame_type_unsupported")

    def test_mirror_source_converts_reads_and_retains_report(self):
        def acquire(stop, on_frame, progress):
            progress({"status": "acquiring"})
            on_frame(b"abcdef", {"fourcc": "RGB3", "width": 2, "height": 1, "stride_bytes": 6}, 7, 123)
            return {"status": "captured"}

        frames = []
        source = MirrorSource(acquire)
        source.run(frames.append, threading.Event())
        self.assertEqual(source.report, {"status": "captured"})
        item = frames[0]
        self.assertEqual((item.pixels, item.pixel_format, item.width, item.stride, item.sequence, item.observed_ns),
                         (b"abcdef", "RGB3", 2, 6, 7, 123))
        self.assertEqual(item.metadata["native_format"]["fourcc"], "RGB3")

    def test_mirror_source_owns_reads_declares_identity_and_raises_on_failed_capture(self):
        storage = bytearray(b"abc")
        identity = type("Backend", (), dict(kind="synthetic", source_id="synthetic:mirror", capture_layout="mono"))

        def acquire(stop, on_frame, progress):
            on_frame(storage, {"fourcc": "RGB3", "width": 1, "height": 1, "stride_bytes": 3}, 1, 1)
            storage[:] = b"xyz"
            return {"status": "error", "reason": "close_failed", "error": "controlled"}

        frames = []
        source = MirrorSource(acquire, "synthetic:mirror", identity=identity)
        self.assertEqual((source.kind, source.source_id, source.capture_layout, source.session_id),
                         ("synthetic", "synthetic:mirror", "mono", None))
        with self.assertRaisesRegex(Exception, "controlled") as raised:
            source.run(frames.append, threading.Event())
        self.assertEqual(raised.exception.reason, "close_failed")
        self.assertEqual(frames[0].pixels, b"abc")
        self.assertEqual(source.report["status"], "error")


class Lifecycle:
    """Sink recording the optional lifecycle calls in order."""

    def __init__(self, close_result=True, clear_error=None):
        self.calls, self.items = [], []
        self.close_result, self.clear_error = close_result, clear_error

    def open(self):
        self.calls.append("open")

    def submit(self, processed):
        self.calls.append("submit")
        self.items.append(processed)

    def clear(self):
        self.calls.append("clear")
        if self.clear_error:
            raise RuntimeError(self.clear_error)

    def close(self):
        self.calls.append("close")
        return self.close_result


class StatsReportTests(unittest.TestCase):
    CANONICAL = {"frames_received", "frames_processed", "frames_submitted", "stale_inputs_skipped",
                 "stale_results_skipped", "pending_replaced", "total_inference_ms", "mean_inference_ms",
                 "max_inference_ms", "total_processing_ms", "mean_processing_ms", "max_input_age_ms",
                 "max_output_age_ms", "elapsed_ms", "processed_fps", "labels_seen", "cancelled",
                 "sink_clear_ok", "sink_close_ok"}

    def test_one_canonical_block_with_rounded_milliseconds(self):
        from framejacker.pipeline import PipelineStats, report_fields

        stats = PipelineStats(frames_processed=2, total_inference_ms=1.23456, max_input_age_ms=9.87654,
                              labels_seen={"dog", "cat"}, started=10.0, finished=12.5)
        block = stats.report()
        self.assertEqual(set(block), self.CANONICAL)
        self.assertEqual((block["total_inference_ms"], block["mean_inference_ms"], block["max_input_age_ms"]),
                         (1.235, 0.617, 9.877))
        self.assertEqual((block["elapsed_ms"], block["processed_fps"], block["labels_seen"]), (2500.0, 0.8, ["cat", "dog"]))
        self.assertEqual(PipelineStats().report()["processed_fps"], 0.0)
        self.assertEqual(report_fields(block, {"seen": "labels_seen", "seconds": lambda b: b["elapsed_ms"] / 1000}),
                         {"seen": ["cat", "dog"], "seconds": 2.5})


class EngineLifecycleTests(unittest.TestCase):
    def run_with(self, sink, source=None, detector=None, **options):
        options.setdefault("max_fps", 1000)
        runner = Pipeline(source or ListSource(1), detector or EchoDetector(), (sink,),
                          convert=lambda item: item.pixels, annotator=lambda image, result: image, **options)
        return runner, runner.run(threading.Event())

    def test_sinks_open_before_frames_and_always_clear_and_close(self):
        sink = Lifecycle()
        _, stats = self.run_with(sink)
        self.assertEqual(sink.calls, ["open", "submit", "clear", "close"])
        self.assertTrue(stats.sink_cleanup_ok)

        class Failing(EchoDetector):
            def detect(self, *args):
                raise RuntimeError("detector failed")

        sink = Lifecycle()
        runner = Pipeline(ListSource(1), Failing(), (sink,), convert=lambda item: item.pixels)
        with self.assertRaisesRegex(RuntimeError, "detector failed"):
            runner.run(threading.Event())
        self.assertEqual(sink.calls, ["open", "clear", "close"])

    def test_sink_cleanup_failures_are_recorded_not_hidden(self):
        sink = Lifecycle(close_result=None, clear_error="clear failed")
        _, stats = self.run_with(sink)
        self.assertFalse(stats.sink_clear_ok)
        self.assertFalse(stats.sink_close_ok)
        self.assertFalse(stats.sink_cleanup_ok)
        self.assertEqual(stats.sink_errors, [("clear", "clear failed")])

    def test_expired_presented_result_is_cleared_while_waiting_for_newer_input(self):
        class Paused:
            def run(self, emit, stop):
                emit(frame(1))
                stop.wait(.3)

        sink = Lifecycle()
        _, stats = self.run_with(sink, Paused(), max_input_age_ms=100, seconds=.25)
        self.assertEqual(stats.frames_submitted, 1)
        self.assertEqual(sink.calls[:3], ["open", "submit", "clear"])
        self.assertEqual(sink.calls[-2:], ["clear", "close"])
        self.assertEqual(sink.calls.count("clear"), 2)

    def test_deadline_ends_the_run_without_cancelling_and_bounds_expiry(self):
        class Endless:
            def run(self, emit, stop):
                number = 0
                while not stop.is_set():
                    number += 1
                    emit(frame(number))
                    stop.wait(.01)

        sink = Lifecycle()
        started = time.monotonic_ns()
        runner, stats = self.run_with(sink, Endless(), max_fps=20, max_input_age_ms=10_000, seconds=.2)
        self.assertFalse(stats.cancelled)
        self.assertGreater(stats.frames_submitted, 0)
        self.assertLess(stats.elapsed_s, 1.5)
        self.assertLessEqual(max(item.expires_ns for item in sink.items), started + 1_000_000_000)
        self.assertGreaterEqual(stats.frames_received, stats.frames_submitted)

    def test_hud_requires_latest_only_bounded_age(self):
        for options in ({}, {"max_input_age_ms": 100, "latest_only": False}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "hud_requires"):
                Pipeline(ListSource(1), EchoDetector(), hud=object(), **options)


class PolledSourceTests(unittest.TestCase):
    class Source:
        kind, source_id, capture_layout = "synthetic", "synthetic:polled", "mono"

        def __init__(self, frames=(), read_error=None, close_result=True, close_error=None):
            self.frames, self.read_error = list(frames), read_error
            self.close_result, self.close_error = close_result, close_error
            self.opened = self.closed = False

        def open(self):
            self.opened = True

        def read(self, stop, timeout=0.05):
            if self.read_error:
                raise RuntimeError(self.read_error)
            if not self.frames:
                stop.set()
                return None
            return self.frames.pop(0)

        def close(self):
            self.closed = True
            if self.close_error:
                raise RuntimeError(self.close_error)
            return self.close_result

    def test_emits_until_stopped_and_forwards_declared_identity(self):
        from framejacker.sources.polled import PolledSource

        inner = self.Source([frame(1), frame(2)])
        polled = PolledSource(inner)
        self.assertEqual((polled.kind, polled.source_id, polled.capture_layout, polled.session_id),
                         ("synthetic", "synthetic:polled", "mono", None))
        inner.session_id = "changed"
        self.assertEqual(polled.session_id, "changed")
        emitted = []
        polled.run(emitted.append, threading.Event())
        self.assertEqual([item.sequence for item in emitted], [1, 2])
        self.assertTrue(inner.closed)
        self.assertIs(polled.cleanup_ok, True)

    def test_read_and_close_failures_are_kept_and_never_skip_close(self):
        from framejacker.sources.polled import PolledSource

        stopped = threading.Event()
        stopped.set()
        untouched = self.Source()
        polled = PolledSource(untouched)
        polled.run(lambda item: None, stopped)
        self.assertFalse(untouched.opened)
        self.assertIs(polled.cleanup_ok, True)
        for inner, message in ((self.Source(read_error="read failed", close_error="close failed"), "read failed"),
                               (self.Source(close_error="close failed"), "close failed")):
            polled = PolledSource(inner)
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                polled.run(lambda item: None, threading.Event())
            self.assertTrue(inner.closed)
            self.assertIs(polled.cleanup_ok, False)
            self.assertEqual(polled.error, message)
        polled = PolledSource(self.Source(close_result=None))
        polled.run(lambda item: None, threading.Event())
        self.assertIs(polled.cleanup_ok, False)


class OutputTests(unittest.TestCase):
    def test_reserve_write_publish_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "still.png"
            reservation = output.reserve(target, ".fixture-")
            output.write(reservation, b"image")
            self.assertEqual(output.publish(reservation), str(target))
            output.discard(reservation)
            self.assertEqual(target.read_bytes(), b"image")
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["still.png"])
            for path in (target, Path(folder) / "still.jpg", Path(folder) / "absent/still.png"):
                with self.subTest(path=path), self.assertRaises(OutputError) as error:
                    output.reserve(path, ".fixture-")
                self.assertEqual(error.exception.reason, "invalid_preview_path")

    def test_replaced_temporary_file_is_neither_written_nor_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            reservation = output.reserve(Path(folder) / "still.png", ".fixture-")
            os.rename(reservation["path"], reservation["path"] + ".moved")  # Keep the inode allocated.
            Path(reservation["path"]).write_bytes(b"foreign")
            with self.assertRaises(OutputError) as error:
                output.write(reservation, b"image")
            self.assertEqual(error.exception.reason, "preview_changed")
            output.discard(reservation)
            self.assertEqual(Path(reservation["path"]).read_bytes(), b"foreign")


class ProcessTests(unittest.TestCase):
    def test_stop_signal_handler_raises_catchable_cancellation(self):
        with self.assertRaises(process.StopRequested) as error:
            process.stop_on_signal(signal.SIGTERM, None)
        self.assertEqual(error.exception.number, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
