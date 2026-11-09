"""The camera recognition command: validate mode end to end, bounded recognition with a stand-in detector, the report.

The recognition tests launch the executable stand-in helper
(``tests/fake_camera_export.py``) outside the checkout and inject a stand-in
detector; they need NumPy and OpenCV for the frame conversion and skip
without them (the replay environment runs them without skipping in CI). The
validate-mode and argument tests are standard library only. No camera, no
SDK, no display, no file but the report a test names.
"""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit import gates  # noqa: E402
from sightcircuit.camera import contract, fake_export  # noqa: E402
from sightcircuit.commands import camera_recognize as command  # noqa: E402
from sightcircuit.detection import LIVE_INFERENCE_P95_BUDGET_MS, POC_THRESHOLD  # noqa: E402
from sightcircuit.frame import Detection, DetectionResult  # noqa: E402
from sightcircuit.runtime.supervision import RECORD_AND_POLL  # noqa: E402
from tests import fake_camera_export  # noqa: E402

try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    CONVERSION = True
except ImportError:
    CONVERSION = False

LIMITS = {"fps": 10, "max_seconds": 3, "max_frames": 5}


class StandInDetector:
    """Finds a chair in every frame, and reports a constant inference time; never looks at a pixel."""
    name, runtime, classes = "stand-in", "none", ("chair",)

    def __init__(self, inference_ms=1.5, label="chair"):
        self.inference_ms, self.label = inference_ms, label
        self.calls = 0
        self.shapes = []

    def detect(self, bgr, threshold):
        self.calls += 1
        self.shapes.append(bgr.shape)
        box = (1, 2, bgr.shape[1] - 1, bgr.shape[0] - 1)
        return DetectionResult((Detection(self.label, 0.91, box),), self.inference_ms)


def run_main(argv):
    with mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr"):  # argparse usage text stays out of the test log.
        code = command.main(argv)
    text = "".join(call.args[0] for call in stdout.write.call_args_list)
    return code, json.loads(text)


class ValidateModeTests(unittest.TestCase):
    def test_validate_runs_the_reader_over_every_fake_stream_without_a_helper_or_detector(self):
        with mock.patch.object(command, "create_detector", side_effect=AssertionError("no detector in validate")), \
                mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("no helper")):
            code, report = run_main(["--validate"])
        self.assertEqual(code, 0)
        self.assertEqual((report["report_kind"], report["mode"], report["status"]),
                         ("camera_recognition", "validate", "validated"))
        self.assertTrue(report["valid_stream"]["ok"])
        self.assertEqual(report["valid_stream"]["frames"], fake_export.LIMITS["max_frames"])
        self.assertEqual(report["invalid_stream_count"], len(fake_export.INVALID))
        self.assertEqual(report["invalid_streams_failed"], [])
        self.assertTrue(all(result["ok"] and result["expected"] == result["observed"]
                            for result in report["invalid_streams"].values()))
        self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
        self.assertTrue(report["camera_data"]["detections_are_camera_data"])
        self.assertFalse(report["pixels_in_report"] or report["images_saved"] or report["display_started"])

    def test_validate_fails_when_the_reader_misses_a_violation(self):
        broken = dict(fake_export.INVALID)
        broken["accepted"] = (lambda: fake_export.valid_stream(), "never_refused")
        with mock.patch.object(fake_export, "INVALID", broken):
            report = command.validate()
        self.assertEqual((report["status"], report["exit_code"]), ("validation_failed", 1))
        self.assertEqual(report["invalid_streams_failed"], ["accepted"])
        self.assertIsNone(report["invalid_streams"]["accepted"]["observed"])

    def test_validate_takes_no_run_arguments(self):
        for extra in (["--allow-camera-input"], ["--helper", "/h"], ["--seconds", "1"], ["--max-frames", "1"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                run_main(["--validate", *extra])
            self.assertEqual(caught.exception.code, 2)

    def test_validate_writes_the_report_only_to_a_new_json_path_the_operator_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validate.json"
            code, report = run_main(["--validate", "--report", str(path)])
            self.assertEqual(code, 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["status"], "validated")
            self.assertEqual(saved["camera_data"]["report_path"], str(path))
            self.assertTrue(saved["camera_data"]["report_saved_by_operator"])
            self.assertEqual(report["camera_data"]["report_path"], str(path))
            self.assertEqual(sorted(entry.name for entry in Path(temporary).iterdir()), ["validate.json"])
            code, report = run_main(["--validate", "--report", str(path)])  # Never overwritten.
            self.assertEqual(code, 1)
            self.assertIn("new .json path", report["report_error"])
            self.assertEqual(saved, json.loads(path.read_text()))
            code, report = run_main(["--validate", "--report", str(Path(temporary) / "report.txt")])
            self.assertEqual(code, 1)
            self.assertEqual(sorted(entry.name for entry in Path(temporary).iterdir()), ["validate.json"])


class ArgumentTests(unittest.TestCase):
    def test_a_run_needs_the_opt_in_the_helper_and_both_bounds(self):
        for argv in ([], ["--helper", "/h", "--seconds", "1", "--max-frames", "1"],
                     ["--allow-camera-input", "--seconds", "1", "--max-frames", "1"],
                     ["--allow-camera-input", "--helper", "/h", "--max-frames", "1"],
                     ["--allow-camera-input", "--helper", "/h", "--seconds", "1"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                run_main(argv)
            self.assertEqual(caught.exception.code, 2)

    def test_the_bounds_are_the_work_orders_inside_the_helpers(self):
        self.assertEqual((command.MAX_SECONDS, command.MAX_FRAMES, command.MAX_FPS), (30, 300, 10))
        self.assertLessEqual(command.MAX_SECONDS, contract.LIMITS["max_seconds"][1])
        self.assertLessEqual(command.MAX_FRAMES, contract.LIMITS["max_frames"][1])
        base = ["--allow-camera-input", "--helper", "/h"]
        for argv in (["--seconds", "31", "--max-frames", "1"], ["--seconds", "0", "--max-frames", "1"],
                     ["--seconds", "1", "--max-frames", "301"], ["--seconds", "1", "--max-frames", "0"],
                     ["--seconds", "1", "--max-frames", "1", "--fps", "11"],
                     ["--seconds", "1", "--max-frames", "1", "--cameras", "2"],
                     ["--seconds", "1", "--max-frames", "1", "--cameras", "1,0"],
                     ["--seconds", "1", "--max-frames", "1", "--threshold", "1.5"],
                     ["--seconds", "1", "--max-frames", "1", "--threshold", "nan"],
                     ["--seconds", "1", "--max-frames", "1", "--detector", "other"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                run_main(base + argv)
            self.assertEqual(caught.exception.code, 2)

    def test_a_missing_helper_is_an_error_report_before_any_detector_loads(self):
        with mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            code, report = run_main(["--allow-camera-input", "--helper", "/nonexistent/helper", "--seconds", "1",
                                     "--max-frames", "1"])
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["reason"]), ("error", "helper_not_found"))
        self.assertEqual(report["capture"]["status"], "error")
        self.assertEqual(report["frames_processed"], 0)
        self.assertEqual(report["detector"], {"name": "stand-in", "runtime": "none", "classes": 1})

    def test_a_tracked_file_is_refused_as_the_helper(self):
        tracked = ROOT / "scripts/camera_recognize.py"
        with mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            code, report = run_main(["--allow-camera-input", "--helper", str(tracked), "--seconds", "1", "--max-frames", "1"])
        self.assertEqual((code, report["status"], report["reason"]), (1, "error", "helper_inside_work_tree"))

    def test_a_detector_that_cannot_load_ends_the_run_before_the_helper(self):
        with mock.patch.object(command, "create_detector", side_effect=RuntimeError("no model")), \
                mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")):
            report = command.recognize("/h", LIMITS, (0,))
        self.assertEqual((report["status"], report["reason"], report["exit_code"]), ("error", "detector_unavailable", 1))

    def test_the_docstring_and_report_state_camera_data_and_the_gates(self):
        self.assertIn("Camera Data", command.__doc__)
        self.assertIn("sections 1 and 3.2", command.__doc__)
        report = command.new_report("recognize")
        self.assertIn("Camera Data", report["camera_data"]["statement"])
        self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
        self.assertFalse(report["production_live_input_enabled"])
        self.assertEqual(report["inference_p95_budget_ms"], LIVE_INFERENCE_P95_BUDGET_MS)
        self.assertEqual(report["maximum_input_observation_age_ms"], 1000)

    def test_the_command_never_imports_the_live_pipeline_engine(self):
        import ast
        tree = ast.parse((ROOT / "sightcircuit/commands/camera_recognize.py").read_text())
        names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        self.assertNotIn("Pipeline", names)
        self.assertNotIn("run_report", names)
        self.assertNotIn("run_recognition", names)
        for path in ("sightcircuit/sources/camera_helper.py", "sightcircuit/camera/export_reader.py"):
            text = (ROOT / path).read_text()
            with self.subTest(module=path):
                self.assertNotIn("Pipeline(", text)
                self.assertNotIn("runtime.live", text)
                self.assertNotIn("from ..hud", text)


@unittest.skipUnless(CONVERSION and sys.platform.startswith("linux"), "NumPy, OpenCV and owned sessions")
class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = fake_camera_export.install(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.previous = {number: signal.getsignal(number) for number in RECORD_AND_POLL.numbers}
        for number, handler in self.previous.items():
            self.addCleanup(signal.signal, number, handler)

    def test_a_bounded_run_detects_on_every_exported_frame_and_reports_as_the_mirror_does(self):
        detector = StandInDetector(inference_ms=12.5)
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=lambda: detector)
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("recognized", 0, True))
        self.assertEqual((report["frames_received"], report["frames_processed"]), (5, 5))
        self.assertEqual((report["stale_inputs_skipped"], report["pending_frames_replaced"]), (0, 0))
        self.assertEqual(report["labels_seen"], ["chair"])
        self.assertEqual(detector.calls, 5)
        self.assertEqual(detector.shapes[0], (fake_export.HEIGHT, fake_export.WIDTH, 3))  # Small frames are not resized.
        self.assertEqual(report["inference_ms"], {"count": 5, "samples": 5, "p50": 12.5, "p95": 12.5, "max": 12.5})
        self.assertTrue(report["inference_p95_within_budget"])
        self.assertEqual(report["inference_p95_budget_ms"], LIVE_INFERENCE_P95_BUDGET_MS)
        for name in ("processing_ms", "input_age_ms"):
            self.assertEqual(report[name]["count"], 5)
            self.assertLess(report[name]["max"], 1000)
        self.assertEqual([record["sequence"] for record in report["detections"]], [1, 2, 3, 4, 5])
        first = report["detections"][0]
        self.assertEqual(first["source_id"], "arcturus_camera_left")
        self.assertEqual(first["labels"], ["chair"])
        self.assertEqual(first["detections"], [{"label": "chair", "confidence": 0.91,
                                                "box_xyxy": [1, 2, fake_export.WIDTH - 1, fake_export.HEIGHT - 1]}])
        self.assertEqual(first["box_coordinate_space"], "detector_input_pixels")
        self.assertEqual(first["detector_input_size"], [fake_export.WIDTH, fake_export.HEIGHT])
        self.assertEqual(first["native_size"], [fake_export.WIDTH, fake_export.HEIGHT])
        self.assertEqual(report["last_result"], report["detections"][-1])
        self.assertEqual(report["capture"]["status"], "exported")
        self.assertEqual(report["capture"]["stream"]["end"]["reason"], "max_frames")
        self.assertEqual((report["seconds_requested"], report["max_frames_requested"], report["fps_requested"]),
                         (3, 5, 10))
        self.assertEqual(report["cameras_requested"], [0, 1])
        self.assertEqual(report["threshold"], POC_THRESHOLD)
        self.assertIsNone(report["detector_requested"])  # An injected detector is not described.
        self.assertNotIn("detector", report)
        self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
        self.assertFalse(report["images_saved"] or report["display_started"] or report["overlay_requested"])
        self.assertFalse(report["pixels_in_report"])
        text = json.dumps(report)
        self.assertNotIn(repr(fake_export.pixels(1)[:12])[2:-1], text)
        self.assertLess(len(text), 200_000)

    def test_large_frames_are_converted_to_at_most_960_pixels(self):
        detector = StandInDetector()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 2}, (0,),
                                   detector_factory=lambda: detector,
                                   extra_args=("--width", "1920", "--height", "1080", "--stride", "5760"))
        self.assertEqual(report["status"], "recognized")
        self.assertEqual(detector.shapes[0], (540, 960, 3))
        self.assertEqual(report["detections"][0]["native_size"], [1920, 1080])
        self.assertEqual(report["detections"][0]["detector_input_size"], [960, 540])

    def test_a_slow_detector_processes_the_latest_frame_and_counts_replacements(self):
        import time

        class Slow(StandInDetector):
            def detect(self, bgr, threshold):
                time.sleep(0.35)
                return super().detect(bgr, threshold)
        detector = Slow()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 8}, (0, 1),
                                   detector_factory=lambda: detector)
        self.assertEqual(report["status"], "recognized")
        self.assertEqual(report["frames_received"], 8)
        self.assertLess(report["frames_processed"], 8)
        self.assertGreater(report["pending_frames_replaced"], 0)
        self.assertEqual(report["frames_processed"] + report["pending_frames_replaced"] + report["stale_inputs_skipped"], 8)
        self.assertEqual(report["detections"][-1]["sequence"], 8)  # The last frame is always processed.

    def test_stale_frames_are_skipped_not_detected(self):
        detector = StandInDetector()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 3, "max_frames": 3}, (0,),
                                   detector_factory=lambda: detector,
                                   extra_args=("--fake-scenario", "stale_clock"))
        self.assertEqual((report["status"], report["reason"]), ("no_frames", "all_exported_frames_stale"))
        self.assertEqual((report["frames_received"], report["stale_inputs_skipped"], report["frames_processed"]), (3, 3, 0))
        self.assertEqual(detector.calls, 0)
        self.assertIsNone(report["inference_p95_within_budget"])
        self.assertEqual(report["exit_code"], 1)

    def test_a_refused_stream_and_an_early_helper_end_keep_their_names(self):
        report = command.recognize(self.binary, fake_export.LIMITS, (0, 1), detector_factory=StandInDetector,
                                   extra_args=("--fake-scenario", "invalid:frame_sequence_gap"))
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("refused", "sequence_not_consecutive", 1))
        # The frame before the gap was delivered; the catalogue's synthetic clocks make it stale, so it is skipped.
        self.assertEqual(report["frames_received"], 1)
        self.assertEqual(report["frames_processed"] + report["stale_inputs_skipped"], 1)
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=StandInDetector,
                                   extra_args=("--fake-scenario", "headset_removed"))
        self.assertEqual((report["status"], report["reason"]), ("helper_ended_early", "headset_removed"))
        self.assertEqual(report["frames_processed"], 2)

    def test_a_failing_detector_ends_the_run_error_and_stops_the_helper(self):
        class Broken(StandInDetector):
            def detect(self, bgr, threshold):
                raise RuntimeError("model broke")
        report = command.recognize(self.binary, {"fps": 5, "max_seconds": 30, "max_frames": 300}, (0, 1),
                                   detector_factory=Broken, timeout=40)
        self.assertEqual((report["status"], report["reason"]), ("error", "processing_failed"))
        self.assertIn("model broke", report["error"])
        self.assertIn(report["capture"]["status"], ("stopped", "exported"))
        self.assertEqual(report["exit_code"], 1)

    def test_a_signal_cancels_with_exit_128_plus_signal(self):
        timer = threading.Timer(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        report = command.recognize(self.binary, {"fps": 4, "max_seconds": 30, "max_frames": 300}, (0, 1),
                                   detector_factory=StandInDetector, timeout=40)
        self.assertEqual((report["status"], report["reason"], report["signal"]), ("cancelled", "signal", signal.SIGTERM))
        self.assertEqual(report["exit_code"], 128 + signal.SIGTERM)
        self.assertTrue(report["cleanup_ok"])

    def test_main_runs_end_to_end_and_writes_only_the_named_report(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            path = Path(temporary) / "run.json"
            code, report = run_main(["--allow-camera-input", "--helper", str(self.binary), "--seconds", "3",
                                     "--max-frames", "4", "--fps", "10", "--cameras", "0", "--report", str(path)])
            self.assertEqual(code, 0, report)
            self.assertEqual(report["status"], "recognized")
            self.assertEqual(report["frames_processed"], 4)
            self.assertEqual(report["cameras_requested"], [0])
            self.assertEqual(report["detector"], {"name": "stand-in", "runtime": "none", "classes": 1})
            self.assertEqual(report["detector_requested"], "mobilenet-ssd")
            saved = json.loads(path.read_text())
            self.assertEqual(saved["camera_data"]["report_path"], str(path))
            self.assertTrue(saved["camera_data"]["report_saved_by_operator"])
            self.assertEqual(sorted(entry.name for entry in Path(temporary).iterdir()), ["run.json"])

    def test_the_wrapper_is_the_body(self):
        import importlib
        wrapper = importlib.import_module("scripts.camera_recognize")
        self.assertIs(wrapper, command)


class StandInProgramTests(unittest.TestCase):
    def test_the_stand_in_refuses_the_wrong_arguments_as_the_helper_does(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(fake_camera_export.main(["--probe-frames", "1"]), 64)
            self.assertEqual(fake_camera_export.main(["--export-frames", "--fps", "1"]), 64)

    def test_the_catalogue_limits_are_within_the_contract(self):
        for name, (low, high) in contract.LIMITS.items():
            self.assertTrue(low <= fake_export.LIMITS[name] <= high)


if __name__ == "__main__":
    unittest.main()
