"""Bounded mirror/inference integration with controlled sources, without hardware."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dataclasses import replace

from framejacker.convert import to_bgr
from framejacker.errors import FrameFormatError
from framejacker.frame import Detection, DetectionResult
from framejacker.sources import mirror_device as device
from scripts import mirror_recognize as live

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = {"node": "/dev/fixture", "name": "SteamVR"}
DISCOVERY = {"status": "candidates_found", "candidates": [CANDIDATE]}
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))


class MarkedMirror:
    closed = True

    def __init__(self, candidate):
        self.capabilities = {"driver": "fixture", "card": "SteamVR"}
        self.format = device.validate_format(32, 24, "RGB3", 96, 2304)
        self.number = 0

    def open(self):
        type(self).closed = False

    def read_frame(self, timeout):
        self.number += 1
        return bytes([self.number]) * self.format["buffer_bytes"]

    def close(self):
        type(self).closed = True


class FixtureMirror(MarkedMirror):
    def open(self):
        import cv2

        super().open()
        image = cv2.imread(str(ROOT / ".cache/framejacker/assets/dog.jpg"))
        if image is None:
            raise AssertionError("Missing checked development fixture")
        height, width = image.shape[:2]
        self.format = device.validate_format(width, height, "RGB3", width * 3, width * height * 3)
        self.pixels = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()

    def read_frame(self, timeout):
        return self.pixels


class MarkedDetector:
    delay = 0

    def detect(self, image, threshold):
        value = int(image[0, 0, 0])
        time.sleep(self.delay)
        return DetectionResult((Detection("fixture", 1.0, (value, 0, value + 1, 1)),), self.delay * 1000)


class SlowDetector(MarkedDetector):
    delay = 0.23


class EmptyDetector(MarkedDetector):
    def detect(self, image, threshold):
        return DetectionResult((), super().detect(image, threshold).inference_ms)


class EmptyMirror(MarkedMirror):
    def read_frame(self, timeout):
        return None


class StalledMirror(MarkedMirror):
    def read_frame(self, timeout):
        return super().read_frame(timeout) if not self.number else None


class FailingMirror(MarkedMirror):
    def read_frame(self, timeout):
        raise device.MirrorError("fixture_read_error", "controlled read failure")


class BadCloseMirror(MarkedMirror):
    def close(self):
        super().close()
        raise RuntimeError("controlled close failure")


def fixture_worker(job, seconds, preview_temp, sender, stop, parent):
    import cv2

    encode = cv2.imencode

    def encode_after_close(*args):
        if not FixtureMirror.closed:
            raise AssertionError("Image encoded before source close")
        return encode(*args)

    cv2.imencode = encode_after_close
    live._worker(job, seconds, preview_temp, sender, stop, parent, backend_factory=FixtureMirror)


class RecognitionGuardTests(unittest.TestCase):
    def test_discovery_does_not_load_model_or_open_device(self):
        output = io.StringIO()
        with patch.object(live, "discover", return_value=DISCOVERY), \
                patch.object(live, "MobileNetSSD", side_effect=AssertionError("model must not load")), \
                patch.object(live.process, "run_bounded", side_effect=AssertionError("worker must not start")), redirect_stdout(output):
            self.assertEqual(live.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "discovery_only")
        self.assertFalse(report["display_started"])
        self.assertFalse(report["images_saved"])

    def test_invalid_options_and_unavailable_source_stop_before_worker(self):
        for arguments in (["--preview", "x.png"], ["--max-fps", "6"], ["--seconds", "11"], ["--threshold", "nan"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    live.main(arguments)
                self.assertEqual(error.exception.code, 2)
        output = io.StringIO()
        with patch.object(live, "discover", return_value={"candidates": []}), redirect_stdout(output):
            self.assertEqual(live.main(["--capture"]), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "unavailable")



@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for mirror/inference checks")
class RecognitionIntegrationTests(unittest.TestCase):
    def test_rgb_order_padding_and_explicit_unsupported_format(self):
        def mirror_frame(raw, fmt):
            frames = []
            source = live.MirrorSource(lambda stop, on_frame, progress: on_frame(raw, fmt, 1, time.monotonic_ns())
                                       or {"status": "captured"})
            source.run(frames.append, threading.Event())
            return frames[0]

        image = to_bgr(mirror_frame(bytes([255, 0, 0, 0, 0, 255, 99, 99]) * 2,
                                    device.validate_format(2, 2, "RGB3", 8, 16)))
        self.assertEqual(image[0].tolist(), [[0, 0, 255], [255, 0, 0]])
        self.assertTrue(image.flags.writeable)
        with self.assertRaisesRegex(FrameFormatError, "RGB3/BGR3"):
            to_bgr(mirror_frame(bytes(4), device.validate_format(2, 2, "GREY", 2, 4)))

    def test_slow_inference_discards_old_input_and_keeps_boxes_on_its_frame(self):
        report, image = live.recognize(CANDIDATE, 0.8, threading.Event(), want_preview=True,
                                        backend_factory=MarkedMirror, detector_factory=SlowDetector)
        self.assertEqual(report["status"], "recognized", report)
        self.assertTrue(MarkedMirror.closed)
        self.assertGreater(report["pending_frames_replaced"], 0)
        last = report["last_result"]
        self.assertEqual(last["read_sequence"], last["detections"][0]["box_xyxy"][0])
        self.assertEqual(int(image[0, 0, 0]), last["read_sequence"])
        self.assertLess(report["frames_processed"], report["capture"]["frames_read"])

    def test_empty_labels_are_successful_inference_without_image_output(self):
        report, image = live.recognize(CANDIDATE, 0.3, threading.Event(), backend_factory=MarkedMirror,
                                        detector_factory=EmptyDetector)
        self.assertEqual(report["status"], "recognized", report)
        self.assertEqual(report["labels_seen"], [])
        self.assertIsNone(image)
        self.assertFalse(report["images_saved"])

    def test_empty_failing_and_unclean_sources(self):
        for factory, status in ((EmptyMirror, "no_frames"), (FailingMirror, "error"), (BadCloseMirror, "error")):
            with self.subTest(factory=factory):
                report, image = live.recognize(CANDIDATE, 0.3, threading.Event(), want_preview=True,
                                                backend_factory=factory, detector_factory=MarkedDetector)
                self.assertEqual(report["status"], status, report)
                self.assertIsNone(image)
                self.assertTrue(factory.closed)
                if factory == BadCloseMirror:
                    self.assertFalse(report["cleanup_ok"])

    def test_model_failure_does_not_open_source_and_cancel_is_bounded(self):
        factory = unittest.mock.Mock(side_effect=RuntimeError("model missing"))
        backend = unittest.mock.Mock(side_effect=AssertionError("source must not open"))
        report, image = live.recognize(CANDIDATE, 1, threading.Event(), backend_factory=backend, detector_factory=factory)
        self.assertEqual(report["status"], "error")
        backend.assert_not_called()
        stop = threading.Event()
        stop.set()
        report, image = live.recognize(CANDIDATE, 1, stop, backend_factory=backend, detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "cancelled")
        backend.assert_not_called()

    def test_stalled_input_is_stale_and_not_published(self):
        report, image = live.recognize(CANDIDATE, 1.2, threading.Event(), want_preview=True,
                                        backend_factory=StalledMirror, detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "stale_input", report)
        self.assertEqual(report["frames_processed"], 1)
        self.assertIsNone(image)

    def test_old_selected_frames_are_skipped(self):
        class OldMirrorSource(live.MirrorSource):
            def run(self, emit, stop):
                return super().run(lambda frame: emit(replace(frame, observed_ns=frame.observed_ns - 2000000000)), stop)

        with patch.object(live, "MirrorSource", OldMirrorSource):
            report, image = live.recognize(CANDIDATE, 0.3, threading.Event(), backend_factory=MarkedMirror,
                                            detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "stale_input", report)
        self.assertEqual(report["frames_processed"], 0)
        self.assertGreater(report["stale_inputs_skipped"], 0)

    def test_real_model_spawned_capture_and_post_close_png_publication(self):
        import cv2

        real_link = os.link
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "result.png"
            with patch.object(live, "discover", return_value=DISCOVERY), patch.object(live, "_worker", fixture_worker), redirect_stdout(io.StringIO()) as output:
                code = live.main(["--capture", "--seconds", "1", "--preview", str(target)])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            self.assertEqual(report["status"], "recognized")
            self.assertTrue(report["cleanup_ok"])
            self.assertEqual(report["worker_exit_code"], 0)
            self.assertTrue({"bicycle", "car", "dog"}.issubset(report["labels_seen"]))
            self.assertFalse(report["display_started"])
            self.assertTrue(report["images_saved"])
            image = cv2.imread(str(target))
            self.assertIsNotNone(image)
            self.assertLessEqual(max(image.shape[:2]), 640)
            self.assertGreater(image.std(), 1)
            old = target.read_bytes()
            with patch.object(live, "discover", return_value=DISCOVERY), redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(["--capture", "--preview", str(target)]), 1)
            self.assertEqual(target.read_bytes(), old)
            target = Path(directory) / "race.png"

            def race_link(name, destination, **kwargs):
                target.write_bytes(b"preserve concurrent output")
                return real_link(name, destination, **kwargs)

            with patch.object(live, "discover", return_value=DISCOVERY), patch.object(live, "_worker", fixture_worker), \
                    patch.object(live.os, "link", side_effect=race_link), redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(["--capture", "--seconds", "1", "--preview", str(target)]), 1)
            self.assertEqual(target.read_bytes(), b"preserve concurrent output")
            self.assertEqual(list(Path(directory).glob(".framejacker-recognition-*")), [])


if __name__ == "__main__":
    unittest.main()
