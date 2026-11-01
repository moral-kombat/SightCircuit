"""File replay checks with actual encoded, fragmented HEVC video tracks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from framejacker.sources.recording import inspect_recording


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_decode_check.py"


@unittest.skipUnless(importlib.util.find_spec("av"), "Install requirements-replay.txt for decoder tests")
class ReplayDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import av

        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.clip = Path(cls.temporary.name) / "fragmented-stereo.mp4"
        with av.open(str(cls.clip), mode="w", format="mp4",
                     options={"movflags": "frag_keyframe+empty_moov"}) as output:
            streams = []
            for width in (64, 96):
                stream = output.add_stream("libx265", rate=5)
                stream.width, stream.height, stream.pix_fmt = width, 64, "yuv420p"
                stream.options = {"preset": "ultrafast",
                                  "x265-params": "log-level=error:pools=none:frame-threads=1"}
                streams.append(stream)
            for _ in range(4):
                for stream in streams:
                    frame = av.VideoFrame(stream.width, stream.height, "yuv420p")
                    for plane in frame.planes:
                        plane.update(bytes([128]) * plane.buffer_size)
                    for packet in stream.encode(frame):
                        output.mux(packet)
            for stream in streams:
                for packet in stream.encode():
                    output.mux(packet)

    def run_check(self, path, *arguments):
        result = subprocess.run([sys.executable, str(SCRIPT), str(path), *arguments],
                                capture_output=True, text=True, timeout=15)
        return result, json.loads(result.stdout)

    def test_fragmented_hevc_stream_selection_and_rgb_conversion(self):
        # Different dimensions ensure the requested video track was really used.
        self.assertTrue(inspect_recording(self.clip)["fragmented"])
        for ordinal, width in ((0, 64), (1, 96)):
            with self.subTest(ordinal=ordinal):
                result, report = self.run_check(self.clip, "--video-stream", str(ordinal))
                self.assertEqual(result.returncode, 0, report)
                self.assertEqual(report["status"], "decoded")
                self.assertEqual(report["frames_decoded"], 3)
                self.assertEqual(report["codec"], "hevc")
                self.assertEqual(report["video_track_count"], 2)
                self.assertEqual(report["container_stream_index"], ordinal)
                for frame in report["frames"]:
                    self.assertEqual((frame["width"], frame["height"]), (width, 64))
                    self.assertEqual(frame["converted_pixel_format"], "rgb24")
                    self.assertGreaterEqual(frame["rgb_stride_bytes"], width * 3)
                self.assertFalse(report["images_saved"])

    def test_missing_stream_is_a_structured_failure(self):
        result, report = self.run_check(self.clip, "--video-stream", "2")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["frames_decoded"], 0)
        self.assertIn("absent", report["error"])

    def test_invalid_file_is_a_structured_failure(self):
        invalid = Path(self.temporary.name) / "invalid.mp4"
        invalid.write_bytes(b"This is not video")
        result, report = self.run_check(invalid)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["frames_decoded"], 0)

    def test_nonregular_path_is_rejected(self):
        result, report = self.run_check(Path(self.temporary.name))
        self.assertEqual(result.returncode, 1)
        self.assertIn("regular file", report["error"])


if __name__ == "__main__":
    unittest.main()
