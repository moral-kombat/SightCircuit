"""Run file decoding, inference and Qt presentation together in an offscreen UI."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from framejacker import assets
from tests.asset_gate import FIXTURE, model_assets, requires_assets

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/replay_window.py"
AVAILABLE = all(importlib.util.find_spec(name) for name in ("av", "cv2", "numpy", "PySide6"))


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for display integration tests")
@requires_assets(*model_assets())  # The sample image and both detectors' models.
class ReplayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import av
        import cv2
        import numpy as np

        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.clip = cls.directory / "two-video.mp4"
        image = cv2.imread(str(assets.ASSETS / FIXTURE))
        if image is None:  # Present (the gate checked) but unreadable: fail, never skip.
            raise AssertionError(f"Unreadable test fixture {FIXTURE}; run bash scripts/setup_window.sh")
        cls.dimensions = image.shape[:2]
        with av.open(str(cls.clip), mode="w") as output:
            streams = []
            for _ in range(2):
                stream = output.add_stream("mpeg4", rate=5)
                stream.width, stream.height = image.shape[1], image.shape[0]
                stream.pix_fmt = "yuv420p"
                streams.append(stream)
            for _ in range(4):
                for stream, pixels in zip(streams, (image, np.zeros_like(image))):
                    frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
                    for packet in stream.encode(frame):
                        output.mux(packet)
            for stream in streams:
                for packet in stream.encode():
                    output.mux(packet)

    def run_window(self, *arguments):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.clip),
                                 "--max-frames", "4", "--max-fps", "60", *arguments],
                                env=env, capture_output=True, text=True, timeout=30)
        return result, json.loads(result.stdout)

    def test_annotated_window_and_preview(self):
        import cv2

        preview, summary = self.directory / "preview.png", self.directory / "summary.json"
        result, report = self.run_window("--preview", str(preview), "--report", str(summary))
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertEqual(report["status"], "finished")
        self.assertEqual(report["frames_processed"], 4)
        self.assertGreater(report["frames_presented"], 0)
        self.assertTrue({"bicycle", "car", "dog"}.issubset(report["labels_seen"]))
        self.assertEqual(report["qt_platform"], "offscreen")
        self.assertTrue(report["images_saved"])
        self.assertEqual(json.loads(summary.read_text()), report)
        image = cv2.imread(str(preview))
        self.assertIsNotNone(image)
        self.assertGreater(image.std(), 10)

    def test_realtime_replay_reports_pacing_fields(self):
        result, report = self.run_window("--realtime", "--decode-threads", "3")
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertEqual(report["status"], "finished")
        self.assertTrue(report["realtime"])
        self.assertEqual(report["decode_threads"], 3)
        self.assertGreaterEqual(report["frames_decoded"], report["frames_processed"])
        for field in ("realtime_ratio", "media_seconds_played", "frames_skipped", "catch_ups", "frames_replaced",
                      "decode_fps", "mean_presentation_latency_ms", "max_schedule_lateness_ms",
                      "processed_media_fraction", "media_frames_spanned"):
            self.assertIn(field, report)
        self.assertGreater(report["realtime_ratio"], 0)
        # Frames inferred on, as a share of the frames the played span covered.
        self.assertGreaterEqual(report["media_frames_spanned"], report["frames_processed"])
        self.assertEqual(report["processed_media_fraction"],
                         round(report["frames_processed"] / report["media_frames_spanned"], 3))
        from scripts.replay_window import LEGACY_FIELDS
        expected = {"frames_replaced": "pending_replaced", "processed_fps": "processed_fps",
                    "mean_processing_ms": "mean_processing_ms", "max_input_age_ms": "max_input_age_ms"}
        self.assertEqual(LEGACY_FIELDS, expected)  # Literal, so a mis-mapped alias fails here.
        for name, source in expected.items():
            with self.subTest(field=name):
                self.assertEqual(report[name], report["pipeline"][source])
        invalid = subprocess.run([sys.executable, str(SCRIPT), str(self.clip), "--decode-threads", "0"],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(invalid.returncode, 2)

    def test_candidate_detector_is_selectable_and_reported(self):
        result, report = self.run_window("--detector", "yolox-nano")
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertEqual(report["detector"]["name"], "yolox-nano")
        self.assertEqual(report["detector"]["classes"], 80)
        self.assertTrue({"bicycle", "dog"}.issubset(report["labels_seen"]))
        result, report = self.run_window()
        self.assertEqual(report["detector"], {"name": "mobilenet-ssd", "runtime": "opencv-dnn", "classes": 20})

    def test_second_track_is_selectable(self):
        result, report = self.run_window("--video-stream", "1")
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["container_stream_index"], 1)
        self.assertGreater(report["frames_presented"], 0)
        self.assertFalse(report["images_saved"])

    def test_missing_stream_fails_cleanly(self):
        result, report = self.run_window("--video-stream", "2")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "error")
        self.assertIn("absent", report["error"])

    def test_pause_and_restart_from_beginning(self):
        from scripts.replay_window import ReplayWorker

        worker = ReplayWorker(self.clip, 0, 0.5, 10, 0)

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while not predicate():
                self.assertIsNone(worker.error)
                self.assertLess(time.monotonic(), deadline, "Playback state timed out")
                time.sleep(0.01)

        worker.start()
        try:
            wait_for(lambda: worker.frames_processed > 0)
            worker.playing.clear()
            time.sleep(0.2)  # Allow the one in-progress frame to complete.
            paused_count = worker.frames_processed
            time.sleep(0.2)
            self.assertEqual(worker.frames_processed, paused_count)
            worker.take_latest()
            # Stop on the first resumed result so the latest-image handoff cannot
            # replace it before a slow test runner observes the restart timestamp.
            worker.limit = paused_count + 1
            worker.restart.set()
            worker.playing.set()
            wait_for(worker.done.is_set)
            restarted = worker.take_latest()
            self.assertIsNotNone(restarted)
            self.assertEqual(restarted.frame.media_time, 0)
            worker.playing.clear()
        finally:
            worker.stop()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(worker.done.is_set())
        self.assertIsNone(worker.error)

    def test_shutdown_while_paused(self):
        from scripts.replay_window import ReplayWorker

        worker = ReplayWorker(self.clip, 0, 0.5, 10, 0)
        worker.playing.clear()
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while worker.stream_index is None:
                self.assertIsNone(worker.error)
                self.assertLess(time.monotonic(), deadline, "File did not open")
                time.sleep(0.01)
            self.assertEqual(worker.frames_processed, 0)
        finally:
            worker.stop()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(worker.done.is_set())
        self.assertIsNone(worker.error)

    def test_recording_cannot_be_overwritten_by_preview(self):
        before = hashlib.sha256(self.clip.read_bytes()).hexdigest()
        result, report = self.run_window("--preview", str(self.clip))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "error")
        self.assertEqual(hashlib.sha256(self.clip.read_bytes()).hexdigest(), before)

    @unittest.skipUnless((ROOT / "artifacts/overlay-bridge/framejacker-overlay-bridge").is_file(), "Build overlay bridge first")
    def test_overlay_controls_and_real_transport_in_validation_mode(self):
        # Inject only the bridge's explicit non-VR mode. The report must retain
        # validation_only=true and runtime_initialized=false; this is not a
        # headset rendering test.
        code = (
            "import sys\nfrom framejacker import overlay as overlay_client\nfrom scripts import replay_window\n"
            "class ValidationClient(overlay_client.OverlayClient):\n"
            "    def __init__(self): super().__init__(validation_only=True)\n"
            "overlay_client.OverlayClient = ValidationClient\n"
            "sys.exit(replay_window.main(sys.argv[1:]))\n")
        result = subprocess.run([sys.executable, "-c", code, str(self.clip), "--overlay",
                                 "--max-frames", "4", "--max-fps", "10"], cwd=ROOT,
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                                capture_output=True, text=True, timeout=30)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertTrue(report["overlay"]["validation_only"])
        native = report["overlay"]["native_report"]
        self.assertGreater(native["frames_received"], 0)
        self.assertEqual(native["frames_submitted"], 0)
        self.assertFalse(native["runtime_initialized"])
        self.assertTrue(native["cleanup_ok"])

    def test_overlay_rgba_conversion_and_size_limit(self):
        import numpy as np
        from framejacker.overlay import rgba_from_bgr

        image = np.array([[[0, 0, 255], [255, 0, 0]]], dtype=np.uint8)
        width, height, pixels = rgba_from_bgr(image)
        self.assertEqual((width, height), (2, 1))
        self.assertEqual(pixels, bytes([255, 0, 0, 255, 0, 0, 255, 255]))
        width, height, pixels = rgba_from_bgr(np.zeros((600, 1200, 3), dtype=np.uint8))
        self.assertEqual((width, height), (512, 256))
        self.assertEqual(len(pixels), width * height * 4)


if __name__ == "__main__":
    unittest.main()
