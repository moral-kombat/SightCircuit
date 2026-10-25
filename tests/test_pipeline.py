"""Shared frame pipeline, latest-item handoff and output helpers, without OpenCV."""
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from framejacker import detection
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


class DetectorManifestTests(unittest.TestCase):
    def test_missing_manifest_entries_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts/smoke-assets.json").write_text('{"assets": [{"name": "dog.jpg", "sha256": "0"}]}')
            with patch.object(detection, "ROOT", root), self.assertRaisesRegex(RuntimeError, "missing"):
                detection.verify_model()


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
