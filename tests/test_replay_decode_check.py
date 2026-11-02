"""File replay checks with actual encoded, fragmented HEVC video tracks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from framejacker.sources.recording import inspect_recording
from scripts import replay_decode_check as command


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

    @unittest.skipUnless(sys.platform.startswith("linux"), "The parent-loss guardian is Linux-only")
    def test_decoder_child_runs_under_the_shared_supervisor(self):
        result, report = self.run_check(self.clip)
        self.assertEqual((result.returncode, report["status"], report["exit_code"], report["cleanup_ok"]),
                         (0, "decoded", 0, True), report)
        self.assertEqual((report["worker_reaped"], report["worker_tree_reaped"], report["worker_forced_termination"]),
                         (True, True, False))
        self.assertEqual(report["parent_death_containment"], "linux_lifeline_guardian")


class DecodeSupervisionTests(unittest.TestCase):
    """The parent's acceptance of its decoder child's report, without PyAV."""

    def report(self, **changes):
        decoded = dict(command.new_report(Path("/clip.mp4"), 0, 3), status="decoded", frames_decoded=1, exit_code=0,
                       cleanup_ok=True)
        return dict(decoded, **changes)

    def test_only_a_consistent_decode_report_is_accepted(self):
        command.validate_worker_report(self.report(), 0)
        command.validate_worker_report(self.report(status="error", frames_decoded=0, exit_code=1), 1)
        command.validate_worker_report(self.report(status="dependency_missing", frames_decoded=0, exit_code=1), 1)
        for report, returncode in ((self.report(), 1), (self.report(exit_code=1), 1), (self.report(status="error"), 0),
                                   (self.report(frames_decoded=0), 0), (self.report(images_saved=True), 0),
                                   (self.report(cleanup_ok=None), 0), (self.report(exit_code=2), 2),
                                   (self.report(report_kind="other"), 0), ([], 0)):
            with self.subTest(report=report, returncode=returncode), self.assertRaises(ValueError):
                command.validate_worker_report(report, returncode)
        self.assertEqual(command.worker_report(self.report(exit_code=None, cleanup_ok=None))["exit_code"], 0)
        self.assertEqual(command.worker_report(self.report(status="error"))["exit_code"], 1)

    def test_a_failed_supervision_still_reports_an_error(self):
        for supervised, error in (({"status": "timed_out", "exit_code": 1}, "File decode exceeded the 45-second limit"),
                                  ({"status": "invalid_worker_report", "exit_code": 1, "reason": "bad"}, "bad"),
                                  ({"status": "error", "exit_code": 1, "error": "absent"}, "absent")):
            with self.subTest(status=supervised["status"]), \
                    patch.object(command, "supervise", return_value=dict(supervised)) as supervise:
                report = command.run_worker(Path("/clip.mp4"), 1, 2)
            self.assertEqual(report["error"], error)
            command_line, base, seconds, validate = supervise.call_args.args
            self.assertEqual(command_line[-6:], ["/clip.mp4", "--video-stream", "1", "--frames", "2", "--worker"])
            self.assertEqual((base["frames_requested"], seconds, validate), (2, 45, command.validate_worker_report))

    def test_a_crashed_or_timed_out_decoder_keeps_its_error_message(self):
        """The supervisor's report-parse reason never replaces the decoder's own exit status in ``error``."""
        unparsed = "Expecting value: line 1 column 1 (char 0)"
        for supervised, error in (
                ({"status": "invalid_worker_report", "exit_code": 1, "reason": unparsed, "worker_exit_code": -11},
                 "Decoder process exited with code -11"),
                ({"status": "invalid_worker_report", "exit_code": 1, "reason": "invalid_decode_worker_report",
                  "worker_exit_code": 2}, "Decoder process exited with code 2"),
                ({"status": "timed_out", "exit_code": 1, "reason": unparsed, "worker_exit_code": -15},
                 "File decode exceeded the 45-second limit"),
                ({"status": "cancelled", "exit_code": 143, "reason": unparsed, "worker_exit_code": -15}, unparsed),
                ({"status": "invalid_worker_report", "exit_code": 1, "reason": "bad", "worker_exit_code": 1}, "bad")):
            with self.subTest(supervised=supervised), patch.object(command, "supervise", return_value=dict(supervised)):
                report = command.run_worker(Path("/clip.mp4"), 0, 3)
            self.assertEqual((report["error"], report["status"], report["reason"]),
                             (error, supervised["status"], supervised["reason"]))


if __name__ == "__main__":
    unittest.main()
