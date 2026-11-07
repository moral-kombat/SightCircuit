"""Known delays give the expected latency percentiles in the replay, mirror and synthetic reports.

The fake detector in ``tests/latency_fixture.py`` reports known delays and
drives ``Pipeline.timer``, so each report's ``pipeline`` block carries exact
nearest-rank percentiles. The device report template names latency statistics
that a synthetic report carries. The replay stand-in generator writes the clip
the ARM64 pacing job replays. No device, model or downloaded asset is used.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from sightcircuit import detection
from sightcircuit.pipeline import Pipeline
from tests.latency_fixture import DELAYS_MS, EXPECTED, KnownDelays

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "templates" / "hud-device-validation-report.json"
EXACT = {"count": len(DELAYS_MS), "samples": len(DELAYS_MS), **EXPECTED}
WINDOW = all(importlib.util.find_spec(name) for name in ("av", "cv2", "numpy", "PySide6"))
MIRROR = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
ENCODER = all(importlib.util.find_spec(name) for name in ("av", "numpy"))
# Replace the replay command's detector and stage clock with the fixture, then run it. argv[1] holds the
# fixture's options as JSON; "hold" makes the interrupted call wait until the command has returned, and
# "join_seconds" shortens the command's wait for its worker. Afterwards the held worker is released and joined.
REPLAY_WITH_KNOWN_DELAYS = (
    "import json, sys, threading\n"
    "from sightcircuit import pipeline\n"
    "from scripts import replay_window\n"
    "from tests.latency_fixture import KnownDelays\n"
    "options, release = json.loads(sys.argv[1]), threading.Event()\n"
    "if 'join_seconds' in options:\n"
    "    replay_window.WORKER_JOIN_SECONDS = options.pop('join_seconds')\n"
    "known = KnownDelays(hold=release if options.pop('hold', False) else None, **options)\n"
    "pipeline.Pipeline.timer = staticmethod(known.clock)\n"
    "replay_window.create_detector = lambda name: known\n"
    "code = replay_window.main(sys.argv[2:])\n"
    "release.set()\n"
    "for thread in threading.enumerate():\n"
    "    if thread is not threading.main_thread():\n"
    "        thread.join(10)\n"
    "sys.exit(code)\n")


class KnownDelayAssertions:
    def assert_known_delay_percentiles(self, block, frames=len(DELAYS_MS)):
        """Inference, processing and the detect stage equal the delays; input and output ages are ordered."""
        for name, statistics in (("inference_ms", block["inference_ms"]), ("processing_ms", block["processing_ms"]),
                                 ("stage_ms.detect", block["stage_ms"]["detect"])):
            with self.subTest(statistic=name):
                self.assertEqual(statistics, EXACT)
        for name in ("input_age_ms", "output_age_ms"):
            statistics = block[name]
            with self.subTest(statistic=name):
                self.assertEqual((statistics["count"], statistics["samples"]), (frames, frames))
                self.assertLessEqual(0, statistics["p50"])
                self.assertLessEqual(statistics["p50"], statistics["p95"])
                self.assertLessEqual(statistics["p95"], statistics["max"])
        self.assertEqual(block["percentile_method"], "nearest_rank")


def resolve(report, dotted):
    """The report value a template field such as ``pipeline.input_age_ms.p95`` names."""
    value = report
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted)
        value = value[part]
    return value


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for generated synthetic frames")
class SyntheticReportTests(KnownDelayAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sightcircuit.hud import HudStage
        from sightcircuit.runtime import live, synthetic

        stop = threading.Event()
        known = KnownDelays(stop, stop_after=len(DELAYS_MS))
        source = synthetic.SyntheticSource()
        with patch.object(Pipeline, "timer", staticmethod(known.clock)):
            cls.report = live.run_pipeline(source, known, synthetic.HeadlessSink(), 10, stop=stop, max_fps=10,
                                           hud=HudStage(source, synthetic.synthetic_hud_calibration()))

    def test_synthetic_hud_report_carries_the_known_delay_percentiles(self):
        # The detector stops the run on its 20th call, so the report is "cancelled" with every frame timed.
        self.assertEqual((self.report["status"], self.report["exit_code"]), ("cancelled", 130), self.report)
        self.assertEqual(self.report["frames_processed"], len(DELAYS_MS))
        self.assertTrue(self.report["cleanup_ok"], self.report)
        self.assert_known_delay_percentiles(self.report["pipeline"])

    def test_template_names_latency_statistics_that_a_synthetic_report_carries(self):
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        latency = template["g6_acceptance"]["latency"]
        self.assertEqual(latency["report"], {"path": None, "sha256": None})
        fields = [entry["field"] for entry in latency["statistics"]]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertTrue({"pipeline.input_age_ms.p95", "pipeline.inference_ms.p95",
                         "pipeline.processing_ms.p95", "pipeline.percentile_method"} <= set(fields))
        for entry in latency["statistics"]:
            with self.subTest(field=entry["field"]):
                self.assertEqual(set(entry), {"field", "unit", "value"})
                self.assertIsNone(entry["value"], "an unexecuted worksheet leaves every statistic unknown")
                self.assertTrue(entry["field"].startswith("pipeline."))
                value = resolve(self.report, entry["field"])
                if entry["unit"] == "ms":
                    self.assertIn(type(value), (int, float))
                    self.assertGreaterEqual(value, 0)
                elif entry["unit"] == "frames":
                    self.assertIs(type(value), int)
                    self.assertGreater(value, 0)
                else:
                    self.assertIsNone(entry["unit"])
                    self.assertEqual(value, "nearest_rank")


@unittest.skipUnless(MIRROR, "Run scripts/setup_window.sh for mirror recognition checks")
class MirrorReportTests(KnownDelayAssertions, unittest.TestCase):
    def test_mirror_report_carries_the_known_delay_percentiles(self):
        from sightcircuit.sources import mirror_device as device
        from sightcircuit.runtime import live

        class Mirror:
            closed = True

            def __init__(self, candidate):
                self.capabilities = {"driver": "fixture", "card": "SteamVR"}
                self.format = device.validate_format(32, 24, "RGB3", 96, 2304)
                self.number = 0

            def open(self):
                type(self).closed = False

            def read_frame(self, timeout):
                self.number += 1
                return bytes([self.number % 256]) * self.format["buffer_bytes"]

            def close(self):
                type(self).closed = True
                return True

        stop = threading.Event()
        known = KnownDelays(stop, stop_after=len(DELAYS_MS))
        with patch.object(Pipeline, "timer", staticmethod(known.clock)):
            report, _ = live.run_recognition({"node": "/dev/fixture", "name": "SteamVR"}, 5, stop,
                                                  backend_factory=Mirror, detector_factory=lambda: known,
                                                  max_fps=100)
        self.assertEqual(report["status"], "cancelled", report)
        self.assertTrue(Mirror.closed)
        self.assertEqual(report["frames_processed"], len(DELAYS_MS))
        self.assert_known_delay_percentiles(report["pipeline"])
        # The run's inference p95 against the live budget constant; a cloud verdict is a fixture result, not Frame evidence.
        self.assertEqual(report["inference_p95_budget_ms"], detection.LIVE_INFERENCE_P95_BUDGET_MS)
        self.assertEqual(report["inference_p95_within_budget"],
                         report["pipeline"]["inference_ms"]["p95"] <= detection.LIVE_INFERENCE_P95_BUDGET_MS)


@unittest.skipUnless(WINDOW, "Run scripts/setup_window.sh for replay window checks")
class ReplayReportTests(KnownDelayAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import av
        import numpy as np

        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.clip = cls.directory / "pattern.mp4"
        with av.open(str(cls.clip), mode="w") as output:
            stream = output.add_stream("mpeg4", rate=30)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            for index in range(24):
                pixels = np.full((48, 64, 3), index * 10 % 256, dtype=np.uint8)
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="bgr24")):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)

    def run_replay(self, options, *arguments):
        result = subprocess.run([sys.executable, "-c", REPLAY_WITH_KNOWN_DELAYS, json.dumps(options), str(self.clip),
                                 "--max-fps", "60", *arguments], cwd=ROOT,
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                                capture_output=True, text=True, timeout=60)
        return result, json.loads(result.stdout)

    def test_replay_report_carries_the_known_delay_percentiles(self):
        result, report = self.run_replay({}, "--max-frames", str(len(DELAYS_MS)))
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertEqual(report["status"], "finished")
        self.assertEqual(report["detector"], {"name": "known-delays", "runtime": "fixture", "classes": 1})
        self.assertEqual(report["frames_processed"], len(DELAYS_MS))
        self.assert_known_delay_percentiles(report["pipeline"])
        # Every-frame replay infers on each frame it spans.
        self.assertEqual((report["media_frames_spanned"], report["processed_media_fraction"]), (len(DELAYS_MS), 1.0))
        self.assertIs(report["statistics_final"], True)

    def test_a_replay_that_fails_after_the_engine_started_still_reports_its_statistics(self):
        result, report = self.run_replay({"fail_after": 5})
        self.assertEqual(result.returncode, 1, (report, result.stderr))
        self.assertEqual(report["status"], "error")
        self.assertIn("known-delays detector failed", report["error"])
        block = report["pipeline"]
        self.assertEqual(block["frames_processed"], 5)
        # The first five delays, 5, 40, 75, 10 and 45 ms: ranks 3 and 5 of five.
        self.assertEqual(block["inference_ms"], {"count": 5, "samples": 5, "p50": 40.0, "p95": 75.0, "max": 75.0})
        self.assertEqual(block["processing_ms"], block["inference_ms"])
        self.assertEqual(report["processed_fps"], block["processed_fps"])
        self.assertIn("processed_media_fraction", report)
        self.assertIs(report["statistics_final"], True)

    def test_a_replay_cancelled_with_sigint_reports_its_statistics(self):
        # The clip loops by default, so only the SIGINT on the 20th detector call ends this run.
        result, report = self.run_replay({"interrupt_after": len(DELAYS_MS)})
        self.assertEqual(result.returncode, 0, (report, result.stderr))
        self.assertEqual(report["status"], "closed")
        self.assertNotIn("error", report)
        block = report["pipeline"]
        statistics = block["inference_ms"]
        # Frames already in flight may finish after the interrupt; every one of them is counted.
        self.assertGreaterEqual(statistics["count"], len(DELAYS_MS))
        self.assertEqual((statistics["count"], statistics["samples"]), (block["frames_processed"],) * 2)
        self.assertEqual(report["frames_processed"], block["frames_processed"])
        self.assertEqual(statistics["max"], EXPECTED["max"])
        self.assertLessEqual(statistics["p50"], statistics["p95"])
        self.assertEqual(block["processing_ms"], statistics)
        self.assertIs(report["statistics_final"], True)
        self.assertIsNotNone(report["processed_media_fraction"])

    def test_a_replay_whose_worker_does_not_stop_reports_its_statistics_as_not_final(self):
        # The 10th detector call interrupts the run and then outlasts the command's wait for its worker.
        result, report = self.run_replay({"interrupt_after": 10, "hold": True, "join_seconds": 0.2})
        self.assertEqual(result.returncode, 1, (report, result.stderr))
        self.assertEqual(report["status"], "error")
        self.assertIn("did not shut down within 0.2 seconds", report["error"])
        block = report["pipeline"]
        # Nine frames finished before the held call: 5, 40, 75, 10, 45, 80, 15, 50 and 85 ms; ranks 5 and 9 of nine.
        self.assertEqual(block["frames_processed"], 9)
        self.assertEqual(block["inference_ms"], {"count": 9, "samples": 9, "p50": 45.0, "p95": 85.0, "max": 85.0})
        self.assertEqual(report["frames_processed"], 9)
        self.assertIs(report["statistics_final"], False)  # The worker was still running when it was read.

    def test_a_replay_that_fails_before_the_engine_starts_has_no_block(self):
        result, report = self.run_replay({}, "--video-stream", "0", "--preview", str(self.clip))
        self.assertEqual(result.returncode, 1, (report, result.stderr))
        self.assertEqual(report["status"], "error")
        self.assertNotIn("pipeline", report)
        self.assertNotIn("statistics_final", report)


@unittest.skipUnless(ENCODER, "Install requirements-window.txt for the stand-in encoder")
class ReplayStandinTests(unittest.TestCase):
    def test_defaults_are_the_documented_e62_clip(self):
        from sightcircuit.sources import standin

        self.assertEqual(standin.STANDIN, {"side": 2464, "fps": 60, "seconds": 10, "gop_frames": 60,
                                           "bitrate_kbps": 320_000})

    def test_writes_a_new_hevc_clip_with_fixed_keyframes_and_refuses_to_overwrite(self):
        import av
        from sightcircuit.sources import standin

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "standin.mp4"
            report = standin.write_standin(path, side=128, fps=10, seconds=2, gop_frames=5, bitrate_kbps=2000)
            self.assertEqual({key: report[key] for key in ("codec", "width", "height", "fps", "frames", "gop_frames")},
                             {"codec": "hevc", "width": 128, "height": 128, "fps": 10, "frames": 20, "gop_frames": 5})
            self.assertEqual(report["bytes"], path.stat().st_size)
            self.assertIn("not a Frame recording", report["scope"])
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                self.assertEqual(stream.codec_context.name, "hevc")
                keyframes = sorted(round(float(packet.pts * packet.time_base) * 10) for packet in container.demux(stream)
                                   if packet.size and packet.is_keyframe)
            self.assertEqual(keyframes, [0, 5, 10, 15])
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(FileExistsError):
                standin.write_standin(path, side=128, fps=10, seconds=1, gop_frames=5)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            for option in ({"side": 127}, {"side": 32}, {"fps": 0}, {"seconds": 0}, {"gop_frames": 0},
                           {"gop_frames": 21}, {"bitrate_kbps": 0}, {"side": True}):
                with self.subTest(option=option), self.assertRaisesRegex(ValueError, "standin_parameters_invalid"):
                    standin.write_standin(Path(directory) / "invalid.mp4",
                                          **{"side": 128, "fps": 10, "seconds": 2, "gop_frames": 5, **option})
            self.assertEqual(sorted(item.name for item in Path(directory).iterdir()), ["standin.mp4"])

    def test_command_prints_the_clip_report_and_refuses_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "standin.mp4"
            command = [sys.executable, str(ROOT / "scripts/replay_standin.py"), str(path), "--side", "128",
                       "--seconds", "1"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, (report, result.stderr))
            self.assertEqual((report["status"], report["frames"], report["fps"], report["gop_frames"]),
                             ("written", 60, 60, 60))
            size = path.stat().st_size
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            report = json.loads(result.stdout)
            self.assertEqual((result.returncode, report["status"]), (1, "error"))
            self.assertIn("exists", report["error"])
            self.assertEqual(path.stat().st_size, size)


if __name__ == "__main__":
    unittest.main()
