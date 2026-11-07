"""Bounded mirror/inference integration with controlled sources, without hardware."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
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

from sightcircuit.convert import to_bgr
from sightcircuit.detection import DEFAULT_DETECTOR
from sightcircuit.errors import FrameFormatError
from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.runtime import live, process
from sightcircuit.sources import mirror as acquisition
from sightcircuit.sources import mirror_device as device
from scripts import mirror_recognize as command
from tests.asset_gate import model_assets, requires_assets

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
        image = cv2.imread(str(ROOT / ".cache/sightcircuit/assets/dog.jpg"))
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


class PatternMirror(MarkedMirror):
    """1280 x 720 RGB3 reads with padded rows: larger than the 960-pixel processing side."""
    width, height, padding = 1280, 720, 16

    def __init__(self, candidate):
        super().__init__(candidate)
        stride = self.width * 3 + self.padding
        self.format = device.validate_format(self.width, self.height, "RGB3", stride, stride * self.height)

    def read_frame(self, timeout):
        import numpy as np

        self.number += 1
        rows = np.full((self.height, self.width * 3 + self.padding), 77, dtype=np.uint8)
        rows[:, :self.width * 3] = pattern_rgb(self.number, self.width, self.height).reshape(self.height, -1)
        return rows.tobytes()


def pattern_rgb(number, width=PatternMirror.width, height=PatternMirror.height):
    """Read ``number``'s RGB pixels: a pattern whose resize is not trivial."""
    import numpy as np

    y, x = np.mgrid[0:height, 0:width]
    channels = ((x * 7 + y * 3 + number) % 256, (x ^ y) % 256, np.full_like(x, number % 256))
    return np.stack(channels, axis=-1).astype(np.uint8)


class DigestDetector:
    """A named fixture model (no files) whose one label is a digest of its exact input."""
    runtime, classes = "fixture", ("chair",)

    def __init__(self, name):
        self.name = name

    def detect(self, image, threshold):
        import numpy as np

        digest = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()[:16]
        return DetectionResult((Detection(f"input-{digest}", 1.0, (2, 2, 40, 30)),), 0.0)


class FailingMirror(MarkedMirror):
    def read_frame(self, timeout):
        raise device.MirrorError("fixture_read_error", "controlled read failure")


class BadCloseMirror(MarkedMirror):
    def close(self):
        super().close()
        raise RuntimeError("controlled close failure")


def recognize(candidate, seconds, stop, want_preview=False, **options):
    """``runtime.live.run_recognition`` with the annotated image of its last result, as ``--preview`` takes it."""
    report, last = live.run_recognition(candidate, seconds, stop, **options)
    return report, last.image if want_preview and last is not None else None


def fixture_worker(job, seconds, preview_temp, sender, stop, parent):
    import cv2

    encode = cv2.imencode

    def encode_after_close(*args):
        if not FixtureMirror.closed:
            raise AssertionError("Image encoded before source close")
        return encode(*args)

    cv2.imencode = encode_after_close
    command._worker(job, seconds, preview_temp, sender, stop, parent, backend_factory=FixtureMirror)


def pattern_worker(job, seconds, outputs, sender, stop, parent):
    """Spawned worker: real capture loop and pipeline over a fixture mirror and a fixture model."""
    with patch.object(live, "create_detector", DigestDetector):
        command._worker(job, seconds, outputs, sender, stop, parent, backend_factory=PatternMirror)


def digestless_worker(job, seconds, outputs, sender, stop, parent):
    """``pattern_worker`` whose result writes the still but omits ``still_sha256``."""
    send = process.send_report

    def without_digest(sender, report, event="progress"):
        send(sender, {key: value for key, value in report.items() if key != "still_sha256"}, event)

    with patch.object(process, "send_report", without_digest):
        pattern_worker(job, seconds, outputs, sender, stop, parent)


def empty_worker(job, seconds, outputs, sender, stop, parent):
    with patch.object(live, "create_detector", DigestDetector):
        command._worker(job, seconds, outputs, sender, stop, parent, backend_factory=EmptyMirror)


class RecognitionGuardTests(unittest.TestCase):
    def test_discovery_does_not_load_model_or_open_device(self):
        output = io.StringIO()
        with patch.object(command, "discover", return_value=DISCOVERY), \
                patch.object(live, "create_detector", side_effect=AssertionError("model must not load")), \
                patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")), redirect_stdout(output):
            self.assertEqual(command.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "discovery_only")
        self.assertFalse(report["display_started"])
        self.assertFalse(report["images_saved"])

    def test_invalid_options_and_unavailable_source_stop_before_worker(self):
        for arguments in (["--preview", "x.png"], ["--still", "x.png"], ["--max-fps", "6"], ["--seconds", "11"],
                          ["--threshold", "nan"], ["--detector", "unknown"],
                          ["--capture", "--preview", "x.png", "--still", "./x.png"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    command.main(arguments)
                self.assertEqual(error.exception.code, 2)
        output = io.StringIO()
        with patch.object(command, "discover", return_value={"candidates": []}), redirect_stdout(output):
            self.assertEqual(command.main(["--capture"]), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "unavailable")

    def test_two_spellings_of_one_file_are_refused_before_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sub").mkdir()
            (root / "link").symlink_to(root, target_is_directory=True)
            for preview in (root / "sub" / ".." / "x.png", root / "link" / "x.png"):
                with self.subTest(preview=preview), redirect_stderr(io.StringIO()), \
                        patch.object(command, "discover", side_effect=AssertionError("discovery must not start")):
                    with self.assertRaises(SystemExit) as error:
                        command.main(["--capture", "--preview", str(preview), "--still", str(root / "x.png")])
                    self.assertEqual(error.exception.code, 2)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["link", "sub"])

    def test_existing_still_is_refused_before_the_worker_and_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            still = Path(directory) / "taken.png"
            still.write_bytes(b"keep")
            with patch.object(command, "discover", return_value=DISCOVERY), \
                    patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(command.main(["--capture", "--still", str(still)]), 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report["reason"], "invalid_still_path")
            self.assertEqual(still.read_bytes(), b"keep")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["taken.png"])

    def test_progress_snapshot_aliases_come_from_the_block_it_embeds(self):
        from types import SimpleNamespace
        from sightcircuit.pipeline import PipelineStats

        snapshots = []
        report = live.new_mirror_report()
        sink = live.ProgressSink(report, live.MIRROR, snapshots.append, lambda: {"capture": {}})
        sink.stats = PipelineStats(frames_processed=1, labels_seen={"cup"}, started=1.0)
        sink.next_progress = 0
        frame = SimpleNamespace(sequence=2, observed_ns=1)
        # The engine records this result's labels only after its sinks run.
        sink.submit(SimpleNamespace(frame=frame, result=DetectionResult((), 1.0), labels=["person"], hud=None,
                                    image=SimpleNamespace(shape=(1, 1, 3)), input_age_ms=0.0,
                                    output_space="detector_input_pixels"))
        snapshot = snapshots[0]
        self.assertEqual(snapshot["pipeline"]["labels_seen"], ["cup", "person"])
        self.assertEqual((snapshot["capture"], snapshot["last_result"]["read_sequence"]), ({}, 2))
        for name, source in live.MIRROR_LEGACY_FIELDS.items():
            with self.subTest(field=name):
                self.assertEqual(snapshot[name], snapshot["pipeline"][source])
        self.assertEqual(report, live.new_mirror_report())  # A snapshot is a copy; the report is unchanged.



@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for mirror/inference checks")
class RecognitionIntegrationTests(unittest.TestCase):
    def test_rgb_order_padding_and_explicit_unsupported_format(self):
        def mirror_frame(raw, fmt):
            frames = []
            source = acquisition.MirrorSource(lambda stop, on_frame, progress: on_frame(raw, fmt, 1, time.monotonic_ns())
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
        report, image = recognize(CANDIDATE, 0.8, threading.Event(), want_preview=True,
                                  backend_factory=MarkedMirror, detector_factory=SlowDetector)
        self.assertEqual(report["status"], "recognized", report)
        self.assertTrue(MarkedMirror.closed)
        self.assertGreater(report["pending_frames_replaced"], 0)
        last = report["last_result"]
        self.assertEqual(last["read_sequence"], last["detections"][0]["box_xyxy"][0])
        self.assertEqual(int(image[0, 0, 0]), last["read_sequence"])
        self.assertLess(report["frames_processed"], report["capture"]["frames_read"])
        expected = {"frames_processed": "frames_processed", "stale_inputs_skipped": "stale_inputs_skipped",
                    "stale_results_skipped": "stale_results_skipped", "pending_frames_replaced": "pending_replaced",
                    "labels_seen": "labels_seen", "processing_elapsed_ms": "elapsed_ms",
                    "observed_processing_rate_hz": "processed_fps", "mean_inference_ms": "mean_inference_ms",
                    "maximum_inference_ms": "max_inference_ms",
                    "maximum_processing_started_age_ms": "max_input_age_ms"}
        self.assertEqual(live.MIRROR_LEGACY_FIELDS, expected)  # Literal, so a mis-mapped alias fails here.
        block = report["pipeline"]
        for name, source in expected.items():
            with self.subTest(field=name):
                self.assertEqual(report[name], block[source])

    def test_empty_labels_are_successful_inference_without_image_output(self):
        report, image = recognize(CANDIDATE, 0.3, threading.Event(), backend_factory=MarkedMirror,
                                  detector_factory=EmptyDetector)
        self.assertEqual(report["status"], "recognized", report)
        self.assertEqual(report["labels_seen"], [])
        self.assertIsNone(image)
        self.assertFalse(report["images_saved"])

    def test_empty_failing_and_unclean_sources(self):
        for factory, status in ((EmptyMirror, "no_frames"), (FailingMirror, "error"), (BadCloseMirror, "error")):
            with self.subTest(factory=factory):
                report, image = recognize(CANDIDATE, 0.3, threading.Event(), want_preview=True,
                                          backend_factory=factory, detector_factory=MarkedDetector)
                self.assertEqual(report["status"], status, report)
                self.assertIsNone(image)
                self.assertTrue(factory.closed)
                if factory == BadCloseMirror:
                    self.assertFalse(report["cleanup_ok"])

    def test_model_failure_does_not_open_source_and_cancel_is_bounded(self):
        factory = unittest.mock.Mock(side_effect=RuntimeError("model missing"))
        backend = unittest.mock.Mock(side_effect=AssertionError("source must not open"))
        report, image = recognize(CANDIDATE, 1, threading.Event(), backend_factory=backend, detector_factory=factory)
        self.assertEqual(report["status"], "error")
        backend.assert_not_called()
        stop = threading.Event()
        stop.set()
        report, image = recognize(CANDIDATE, 1, stop, backend_factory=backend, detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "cancelled")
        backend.assert_not_called()

    def test_stalled_input_is_stale_and_not_published(self):
        report, image = recognize(CANDIDATE, 1.2, threading.Event(), want_preview=True,
                                  backend_factory=StalledMirror, detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "stale_input", report)
        self.assertEqual(report["frames_processed"], 1)
        self.assertIsNone(image)

    def test_old_selected_frames_are_skipped(self):
        class OldMirrorSource(acquisition.MirrorSource):
            def run(self, emit, stop):
                return super().run(lambda frame: emit(replace(frame, observed_ns=frame.observed_ns - 2000000000)), stop)

        with patch.object(live, "MirrorSource", OldMirrorSource):
            report, image = recognize(CANDIDATE, 0.3, threading.Event(), backend_factory=MarkedMirror,
                                      detector_factory=MarkedDetector)
        self.assertEqual(report["status"], "stale_input", report)
        self.assertEqual(report["frames_processed"], 0)
        self.assertGreater(report["stale_inputs_skipped"], 0)

    @requires_assets(*model_assets(DEFAULT_DETECTOR))
    def test_real_model_spawned_capture_and_post_close_png_publication(self):
        import cv2
        import numpy as np

        real_link = os.link
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "result.png"
            still = Path(directory) / "input.png"
            with patch.object(command, "discover", return_value=DISCOVERY), patch.object(command, "_worker", fixture_worker), redirect_stdout(io.StringIO()) as output:
                code = command.main(["--capture", "--seconds", "1", "--preview", str(target), "--still", str(still)])
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
            self.assertEqual(report["detector"]["name"], "mobilenet-ssd")  # The default is unchanged.
            fixture = cv2.imread(str(ROOT / ".cache/sightcircuit/assets/dog.jpg"))
            self.assertTrue(np.array_equal(cv2.imread(str(still)), fixture))  # Unannotated, lossless.
            self.assertEqual(report["still_path"], str(still))
            self.assertEqual(report["still_sha256"], hashlib.sha256(still.read_bytes()).hexdigest())
            old = target.read_bytes()
            with patch.object(command, "discover", return_value=DISCOVERY), redirect_stdout(io.StringIO()):
                self.assertEqual(command.main(["--capture", "--preview", str(target)]), 1)
            self.assertEqual(target.read_bytes(), old)
            target = Path(directory) / "race.png"

            def race_link(name, destination, **kwargs):
                target.write_bytes(b"preserve concurrent output")
                return real_link(name, destination, **kwargs)

            with patch.object(command, "discover", return_value=DISCOVERY), patch.object(command, "_worker", fixture_worker), \
                    patch.object(os, "link", side_effect=race_link), redirect_stdout(io.StringIO()):
                self.assertEqual(command.main(["--capture", "--seconds", "1", "--preview", str(target)]), 1)
            self.assertEqual(target.read_bytes(), b"preserve concurrent output")
            self.assertEqual(list(Path(directory).glob(".sightcircuit-recognition-*")), [])
            self.assertEqual(list(Path(directory).glob(".sightcircuit-still-*")), [])

    def test_still_capture_report_and_manifest_entry_pass_required_digests(self):
        """Done condition: --capture --still, its report, its manifest entry, --require-input-digests."""
        import cv2
        import numpy as np
        from scripts import evaluate_detectors, frame_session_manifest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            still, preview = root / "rug.png", root / "rug-boxes.png"
            with patch.object(command, "discover", return_value=DISCOVERY), patch.object(command, "_worker", pattern_worker), \
                    redirect_stdout(io.StringIO()) as output:
                code = command.main(["--capture", "--seconds", "1", "--detector", "yolox-nano",
                                     "--still", str(still), "--preview", str(preview)])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            self.assertEqual((report["status"], report["cleanup_ok"], report["images_saved"]), ("recognized", True, True))
            self.assertEqual(report["detector_requested"], "yolox-nano")
            self.assertEqual(report["detector"], {"name": "yolox-nano", "runtime": "fixture", "classes": 1})
            last = report["last_result"]
            self.assertEqual((last["width"], last["height"]), (960, 540))
            # The still is the native, unannotated frame of last_result: rows unpadded, RGB stored as BGR.
            saved = cv2.imread(str(still), cv2.IMREAD_UNCHANGED)
            expected = cv2.cvtColor(pattern_rgb(last["read_sequence"]), cv2.COLOR_RGB2BGR)
            self.assertTrue(np.array_equal(saved, expected))
            self.assertEqual(report["still_sha256"], hashlib.sha256(still.read_bytes()).hexdigest())
            self.assertEqual({key: report[key] for key in ("still_path", "still_width", "still_height", "still_detector",
                                                           "still_source", "still_sequence")},
                             {"still_path": str(still), "still_width": 1280, "still_height": 720,
                              "still_detector": "yolox-nano", "still_source": "mirror:observed",
                              "still_sequence": last["read_sequence"]})
            # The evaluator's own conversion turns the still into the exact detector input of last_result.
            self.assertEqual(DigestDetector("check").detect(evaluate_detectors.prepare_image(saved), 0.5).labels,
                             last["labels"])
            self.assertLessEqual(max(cv2.imread(str(preview)).shape[:2]), 640)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["rug-boxes.png", "rug.png"])

            saved_report = root / "still-rug.json"
            saved_report.write_text(output.getvalue())
            manifest = root / "frame.json"
            frame_session_manifest.main(["--manifest", str(manifest), "--id", "rug", "--image", str(still),
                                         "--present", "chair", "--absent", "couch", "--still-report", str(saved_report)])
            entry = json.loads(manifest.read_text())["images"][0]
            self.assertEqual(entry["sha256"], report["still_sha256"])
            self.assertEqual(entry["capture"], {"detector": "yolox-nano", "source": "mirror:observed",
                                                "sequence": last["read_sequence"]})
            images = evaluate_detectors.load_images([manifest], False, require_digests=True)
            self.assertEqual([image[4] for image in images], [report["still_sha256"]])
            _, scores = evaluate_detectors.evaluate(DigestDetector("yolox-nano"), images, 0.5, 0.5)
            self.assertEqual(scores[0]["input_digest_provenance"], "manifest_verified")
            self.assertEqual(scores[0]["reported"], last["labels"])
            still.write_bytes(still.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256 verification"):
                evaluate_detectors.load_images([manifest], False, require_digests=True)

    def test_failed_still_link_withdraws_the_published_preview(self):
        """All or nothing: a destination created during the run stops the still and withdraws the preview."""
        real_link = os.link
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            still, preview = root / "rug.png", root / "rug-boxes.png"

            def race_link(name, destination, **kwargs):
                if Path(destination) == still:
                    still.write_bytes(b"preserve concurrent output")
                return real_link(name, destination, **kwargs)

            with patch.object(command, "discover", return_value=DISCOVERY), patch.object(command, "_worker", pattern_worker), \
                    patch.object(os, "link", side_effect=race_link), redirect_stdout(io.StringIO()) as output:
                code = command.main(["--capture", "--seconds", "1", "--preview", str(preview), "--still", str(still)])
            report = json.loads(output.getvalue())
            self.assertEqual((code, report["status"], report["reason"]), (1, "error", "still_publish_failed"))
            self.assertFalse(report["images_saved"])
            self.assertNotIn("preview_path", report)
            self.assertNotIn("still_path", report)
            self.assertEqual(still.read_bytes(), b"preserve concurrent output")
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["rug.png"])

    def test_a_still_without_its_reported_digest_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            still, preview = root / "rug.png", root / "rug-boxes.png"
            with patch.object(command, "discover", return_value=DISCOVERY), \
                    patch.object(command, "_worker", digestless_worker), redirect_stdout(io.StringIO()) as output:
                code = command.main(["--capture", "--seconds", "1", "--preview", str(preview), "--still", str(still)])
            report = json.loads(output.getvalue())
            self.assertEqual((code, report["status"], report["reason"]), (1, "error", "still_changed"), report)
            self.assertTrue(report["still_ready"])
            self.assertNotIn("still_sha256", report)
            self.assertEqual((report["images_saved"], "preview_path" in report, "still_path" in report),
                             (False, False, False))
            self.assertEqual(list(root.iterdir()), [])

    def test_unrecognized_run_publishes_no_still(self):
        with tempfile.TemporaryDirectory() as directory:
            still = Path(directory) / "none.png"
            with patch.object(command, "discover", return_value=DISCOVERY), patch.object(command, "_worker", empty_worker), \
                    redirect_stdout(io.StringIO()) as output:
                code = command.main(["--capture", "--seconds", "1", "--still", str(still)])
            report = json.loads(output.getvalue())
            self.assertEqual((code, report["status"], report["images_saved"]), (1, "no_frames", False))
            self.assertNotIn("still_sha256", report)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
