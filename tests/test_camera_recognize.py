"""The camera recognition command: validate mode end to end, bounded recognition with a stand-in detector, the report.

The recognition tests launch the executable stand-in helper
(``tests/fake_camera_export.py``) outside the checkout and inject a stand-in
detector; they need NumPy and OpenCV for the frame conversion and skip
without them (the replay environment runs them without skipping in CI). The
validate-mode and argument tests are standard library only. No camera, no
SDK, no display, no file but the report a test names.
"""
import io
import json
import os
from pathlib import Path
import select
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
from sightcircuit.camera import contract, fake_export, marks, region  # noqa: E402
from sightcircuit.commands import camera_recognize as command  # noqa: E402
from sightcircuit.detection import (DETECTORS, LIVE_INFERENCE_P95_BUDGET_MS, POC_THRESHOLD,  # noqa: E402
                                    class_names)
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


class ScriptedDetector(StandInDetector):
    """Finds what its script says on each call: ``None`` for nothing, else the centre of a bottle as frame fractions.

    ``on_call`` maps a call number to a function run before that call's result (a test's key press).
    """
    classes = ("bottle", "chair")

    def __init__(self, script, on_call=None):
        super().__init__()
        self.script, self.on_call = list(script), dict(on_call or {})

    def detect(self, bgr, threshold):
        self.calls += 1
        self.shapes.append(bgr.shape)
        self.on_call.get(self.calls, lambda: None)()
        centre = self.script[self.calls - 1] if self.calls <= len(self.script) else None
        found = [Detection("chair", 0.99, (0, 0, 4, 4))]
        if centre is not None:
            x, y = round(centre[0] * bgr.shape[1]), round(centre[1] * bgr.shape[0])
            found.append(Detection("bottle", 0.8, (x - 2, y - 2, x + 2, y + 2)))
        return DetectionResult(tuple(found), self.inference_ms)


# Keys of the report and of a per-frame record before work-order item L15, and what L15 added.
L15_REPORT_FIELDS = {"timebase", "run_started", "first_frame", "helper_end", "export_returned", "marks", "summary",
                     "target", "target_label"}
L15_FRAME_FIELDS = {"observed_utc_ns", "helper_realtime_ns", "target"}
FORBIDDEN_NAME_PARTS = ("pixel", "image", "still", "preview", "payload", "intrinsic", "extrinsic", "calibrat", "distort",
                        "focal", "principal", "pose", "transform", "matrix", "exposure", "gain", "text", "note",
                        "comment", "typed", "input_bytes")
# The one L15 name that says "image": the constant naming the frame the cell is taken on (``image_as_exported``).
ALLOWED_NAMES = {"cell_frame"}


def closed_strings():
    """Every string an L15 field may hold: vocabulary, sources, cells, end reasons, classes and declared constants."""
    allowed = {*marks.VOCABULARY, marks.KEY_LABEL, *marks.SOURCES, *marks.CELLS, *marks.COLUMNS, *marks.ROWS,
               marks.RUN_END, *marks.BOUNDARIES, *contract.END_REASONS, *marks.TIMEBASE.values(), marks.ORIENTATION_STATEMENT,
               "image_as_exported", "OSError", "ValueError",
               marks.ENDED_BY_HELPER}  # A removal entry's ``run_ended_by`` in a run without the presence watch.
    for name in DETECTORS:
        allowed.update(class_names(name))
    return allowed


def l15_leaves(value, path):
    """``(path, leaf)`` for every scalar under ``value``; containers are walked, never returned."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from l15_leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from l15_leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def l15_names(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from l15_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from l15_names(item)


def l15_number_lists(value):
    """Every list of numbers under ``value`` (a pixel row or a calibration vector would be one)."""
    if isinstance(value, dict):
        for item in value.values():
            yield from l15_number_lists(item)
    elif isinstance(value, list):
        if value and all(type(item) in (int, float) for item in value):
            yield value
        for item in value:
            yield from l15_number_lists(item)


class Terminal:
    """A stand-in for the operator's terminal: ``isatty`` as given, one typed line."""

    def __init__(self, answer="yes\n", interactive=True):
        self.answer, self.interactive = answer, interactive
        self.reads = 0

    def isatty(self):
        return self.interactive

    def readline(self):
        self.reads += 1
        return self.answer


def run_main(argv, terminal=None):
    """Run ``main`` with stdout captured as the report and stdin as ``terminal`` (an operator typing ``yes``)."""
    terminal = Terminal() if terminal is None else terminal
    with mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr, mock.patch("sys.stdin", terminal):
        code = command.main(argv)
    text = "".join(call.args[0] for call in stdout.write.call_args_list)
    notice = "".join(call.args[0] for call in stderr.write.call_args_list)
    return code, json.loads(text), notice


class ValidateModeTests(unittest.TestCase):
    def test_validate_runs_the_reader_over_every_fake_stream_without_a_helper_or_detector(self):
        terminal = Terminal(interactive=False)
        with mock.patch.object(command, "create_detector", side_effect=AssertionError("no detector in validate")), \
                mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("no helper")):
            code, report, notice = run_main(["--validate"], terminal)
        self.assertEqual(code, 0)
        self.assertEqual((terminal.reads, notice), (0, ""))  # Validate mode asks for no confirmation.
        self.assertEqual((report["report_kind"], report["mode"], report["status"]),
                         ("camera_recognition", "validate", "validated"))
        self.assertTrue(report["valid_stream"]["ok"])
        self.assertEqual(report["valid_stream"]["frames"], fake_export.LIMITS["max_frames"])
        self.assertEqual(report["invalid_stream_count"], len(fake_export.INVALID))
        self.assertTrue(report["undecided_stream"]["ok"])
        self.assertEqual(report["undecided_stream"]["frames"], 0)
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
        for extra in (["--allow-camera-input"], ["--helper", "/h"], ["--seconds", "1"], ["--max-frames", "1"],
                      ["--cue", "1:near"], ["--mark-labels", "near"], ["--key-marks"], ["--target-label", "bottle"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                run_main(["--validate", *extra])
            self.assertEqual(caught.exception.code, 2)

    def test_validate_writes_the_report_only_to_a_new_json_path_the_operator_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validate.json"
            code, report, _ = run_main(["--validate", "--report", str(path)])
            self.assertEqual(code, 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["status"], "validated")
            self.assertEqual(saved["camera_data"]["report_path"], str(path))
            self.assertTrue(saved["camera_data"]["report_saved_by_operator"])
            self.assertEqual(report["camera_data"]["report_path"], str(path))
            self.assertEqual(sorted(entry.name for entry in Path(temporary).iterdir()), ["validate.json"])
            code, report, _ = run_main(["--validate", "--report", str(path)])  # Never overwritten.
            self.assertEqual(code, 1)
            self.assertIn("new .json path", report["report_error"])
            self.assertEqual(saved, json.loads(path.read_text()))
            code, report, _ = run_main(["--validate", "--report", str(Path(temporary) / "report.txt")])
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

    def test_marks_outside_the_vocabulary_and_unknown_targets_are_refused_before_anything_runs(self):
        base = ["--allow-camera-input", "--helper", "/h", "--seconds", "10", "--max-frames", "10", "--cameras", "0"]
        for extra in (["--cue", "5:bottle"], ["--cue", "5:mark"], ["--cue", "5:the wearer sneezed"], ["--cue", "near"],
                      ["--cue", "5"], ["--cue", "11:near"], ["--cue", "-1:near"], ["--cue", "5:near", "--cue", "5:far"],
                      ["--cue", "6:near", "--cue", "5:far"], ["--cue", "5:NEAR"], ["--cue", "5:near;rm"],
                      ["--mark-labels", "near,bottle"], ["--mark-labels", "mark"], ["--mark-labels", "near,"],
                      ["--mark-labels", "a note"], ["--target-label", "wine glass"], ["--target-label", "Bottle"],
                      ["--target-label", ""], ["--detector", "yolox-nano", "--target-label", "sofa"],
                      ["--target-label", "bottle", "--cameras", "0,1"]):
            terminal = Terminal()
            with self.subTest(extra=extra[:2]), \
                    mock.patch.object(command, "create_detector", side_effect=AssertionError("loaded")), \
                    mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")), \
                    self.assertRaises(SystemExit) as caught:
                run_main(base + extra, terminal)
            self.assertEqual((caught.exception.code, terminal.reads), (2, 0))  # Refused before the confirmation is asked.
        # The cue cap: one more than the cap, every one of them a valid cue inside the run, is refused as too many.
        within = ["--allow-camera-input", "--helper", "/h", "--seconds", "30", "--max-frames", "10"]
        for count, refused in ((marks.MAX_CUES + 1, True), (marks.MAX_CUES, False)):
            cues = [f"--cue={index * 0.5}:near" for index in range(count)]
            self.assertTrue(all(marks.parse_cue(cue[6:], 30) for cue in cues))
            errors = io.StringIO()
            with self.subTest(count=count), mock.patch("sys.stderr", errors), mock.patch("sys.stdout"), \
                    mock.patch("sys.stdin", Terminal(interactive=False)), \
                    mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")):
                if refused:
                    with self.assertRaises(SystemExit) as caught:
                        command.main(within + cues)
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn(f"at most {marks.MAX_CUES} cues", errors.getvalue())
                else:  # At the cap the arguments pass; the run then stops at the confirmation (no terminal).
                    self.assertEqual(command.main(within + cues), 1)
        self.assertIn("bottle", class_names("mobilenet-ssd"))
        self.assertIn("wine glass", class_names("yolox-nano"))
        self.assertEqual(class_names("mobilenet-ssd"), DETECTORS["mobilenet-ssd"].classes)
        # The library refuses the same before the detector loads, and a target the loaded detector lacks before the helper.
        with mock.patch.object(command, "create_detector", side_effect=AssertionError("loaded")):
            for options in ({"cues": [(0, "bottle")]}, {"mark_labels": ["free text"]}, {"cues": [(5, "near"), (5, "far")]}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    command.recognize("/h", LIMITS, (0,), **options)
            with self.assertRaises(ValueError):  # A target never pools the two cameras' frames.
                command.recognize("/h", LIMITS, (0, 1), detector_factory=ScriptedDetector, target_label="bottle")
        with mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")):
            report = command.recognize("/h", LIMITS, (0,), detector_factory=StandInDetector, target_label="bottle")
        self.assertEqual((report["status"], report["reason"], report["exit_code"]), ("error", "target_label_unknown", 1))
        self.assertIsNone(report["target_label"])  # A label that was not validated never reaches the report.
        self.assertNotIn("bottle", json.dumps(report))

    def test_the_informed_start_needs_a_terminal_and_the_exact_typed_answer(self):
        base = ["--allow-camera-input", "--helper", "/nonexistent/helper", "--seconds", "1", "--max-frames", "1"]
        cases = ((Terminal(interactive=False), "confirmation_requires_terminal"),
                 (Terminal(answer="no\n"), "confirmation_declined"), (Terminal(answer="YES\n"), "confirmation_declined"),
                 (Terminal(answer="yes please\n"), "confirmation_declined"), (Terminal(answer=""), "confirmation_unavailable"))
        for terminal, reason in cases:
            with self.subTest(reason=reason), \
                    mock.patch.object(command, "create_detector", side_effect=AssertionError("loaded without consent")), \
                    mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")):
                code, report, notice = run_main(base, terminal)
            self.assertEqual((code, report["status"], report["reason"]), (1, "error", reason))
            self.assertIsNone(report["confirmation"])
            self.assertEqual(report["seconds_requested"], 1)
            if terminal.interactive:
                self.assertTrue(notice.startswith(contract.EXPORT_NOTICE + "\n"), notice[:80])
                self.assertIn("Bounds: 1 frames, 1 seconds, 5 frames a second, cameras 0,1.", notice)
        # The answer may carry the newline and trailing blanks the helper also strips, and nothing else.
        self.assertIsNone(command.confirm_on_terminal({"fps": 5, "max_seconds": 1, "max_frames": 1}, (0,),
                                                      stdin=Terminal(answer="yes \r\n"), output=mock.Mock()))
        self.assertEqual(command.confirm_on_terminal({"fps": 5, "max_seconds": 1, "max_frames": 1}, (0,),
                                                     stdin=Terminal(answer=" yes\n"), output=mock.Mock()),
                         "confirmation_declined")
        self.assertIn("Camera Data", contract.EXPORT_NOTICE)

    def test_a_missing_helper_is_an_error_report_before_any_detector_loads(self):
        with mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            code, report, _ = run_main(["--allow-camera-input", "--helper", "/nonexistent/helper", "--seconds", "1",
                                        "--max-frames", "1"])
        self.assertEqual(code, 1)
        self.assertEqual(report["confirmation"], "operator_terminal")
        self.assertEqual((report["status"], report["reason"]), ("error", "helper_not_found"))
        self.assertEqual(report["capture"]["status"], "error")
        self.assertEqual(report["frames_processed"], 0)
        self.assertEqual(report["detector"], {"name": "stand-in", "runtime": "none", "classes": 1})

    def test_a_tracked_file_is_refused_as_the_helper(self):
        tracked = ROOT / "scripts/camera_recognize.py"
        with mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            code, report, _ = run_main(["--allow-camera-input", "--helper", str(tracked), "--seconds", "1", "--max-frames", "1"])
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
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=lambda: detector, confirmed=True)
        self.assertEqual(report["confirmation"], "operator_terminal")
        self.assertEqual([record["frame_set_sequence"] for record in report["detections"]], [1, 1, 2, 2, 3])
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

    def test_without_the_new_options_the_run_is_what_it_was_with_times_beside_it(self):
        terminal = mock.Mock()
        before = marks.Clock()
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=StandInDetector, confirmed=True,
                                   terminal=terminal)
        after = marks.Clock()
        self.assertEqual((report["status"], report["exit_code"], report["frames_processed"]), ("recognized", 0, 5))
        self.assertEqual(report["schema_version"], 4)  # 3: region fields may be present (item L14); 4: the presence fields are.
        self.assertEqual((report["presence_watch"]["requested"], report["presence_watch"]["armed"]), (False, False))
        terminal.write.assert_not_called()  # No cue, no key press: nothing is printed during the run.
        self.assertEqual(threading.active_count(), 1)  # No mark thread was left, and none was needed.
        self.assertEqual(report["marks"]["recorded"], [])
        self.assertEqual((report["marks"]["cues_requested"], report["marks"]["key_marks_enabled"]), ([], False))
        self.assertEqual(report["capture"]["helper"]["arguments"],
                         ["--export-frames", "--fps", "10", "--max-seconds", "3", "--max-frames", "5", "--cameras", "0,1",
                          "--parent-confirmed"])  # The helper is launched exactly as before.
        self.assertIsNone(report["target_label"])
        self.assertNotIn("target", report)
        self.assertEqual(report["summary"]["coverage"], None)
        self.assertEqual((report["summary"]["intervals"], report["summary"]["removal"]), ([], []))
        self.assertEqual(sum(window["frames"] for window in report["summary"]["windows"]), 5)
        # The fields a record had before, unchanged, and the two times beside them.
        first = report["detections"][0]
        self.assertEqual(set(first) - L15_FRAME_FIELDS,
                         {"sequence", "frame_set_sequence", "source_id", "camera_index", "frame_id", "observed_ns",
                          "input_age_ms", "processing_ms", "inference_ms", "labels", "detections", "detection_count",
                          "native_size", "detector_input_size", "box_coordinate_space"})
        self.assertNotIn("target", first)
        # The timebase: declared, anchored at the run's start, every wall-clock time the anchor plus a difference.
        timebase = report["timebase"]
        self.assertEqual({name: timebase[name] for name in marks.TIMEBASE}, marks.TIMEBASE)
        anchor = timebase["anchor"]
        self.assertEqual(report["run_started"], anchor)
        self.assertTrue(before.monotonic_ns <= anchor["monotonic_ns"] <= after.monotonic_ns)
        self.assertTrue(before.utc_ns <= anchor["utc_ns"] <= after.utc_ns)
        self.assertLess(abs(timebase["wall_clock_step_ns"]), 1_000_000_000)

        def derived(stamp):
            return stamp["utc_ns"] - anchor["utc_ns"] == stamp["monotonic_ns"] - anchor["monotonic_ns"]
        for record in report["detections"]:
            self.assertTrue(derived({"monotonic_ns": record["observed_ns"], "utc_ns": record["observed_utc_ns"]}))
            self.assertTrue(before.utc_ns <= record["observed_utc_ns"] <= after.utc_ns)
            self.assertEqual(record["helper_realtime_ns"], 1_762_650_000_000_000_000 + record["sequence"])
        self.assertEqual(report["first_frame"]["monotonic_ns"], report["detections"][0]["observed_ns"])
        for name in ("first_frame", "helper_end", "export_returned"):
            self.assertTrue(derived(report[name]), name)
        self.assertEqual(report["helper_end"]["reason"], "max_frames")
        self.assertEqual(report["helper_end"]["monotonic_ns"], report["capture"]["stream"]["end_received_monotonic_ns"])
        self.assertTrue(report["detections"][-1]["observed_ns"] <= report["helper_end"]["monotonic_ns"]
                        <= report["export_returned"]["monotonic_ns"] <= after.monotonic_ns)

    def test_a_cue_schedule_a_target_and_a_removal_are_summarized_end_to_end(self):
        # 20 frames at 10 a second: the bottle absent, then left (cue at 0.3 s), then right (cue at 1.0 s).
        script = [None] * 3 + [(0.2, 0.5)] * 7 + [(0.9, 0.1)] * 10
        detector = ScriptedDetector(script)
        terminal = mock.Mock()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 20}, (0,),
                                   detector_factory=lambda: detector, confirmed=True, target_label="bottle",
                                   cues=[(300, "left"), (1000, "right"), (4000, "out_of_view")], terminal=terminal)
        self.assertEqual((report["status"], report["exit_code"], report["frames_processed"]), ("recognized", 0, 20))
        self.assertEqual("".join(call.args[0] for call in terminal.write.call_args_list), "\aCUE left\n\aCUE right\n")
        recorded = report["marks"]["recorded"]
        self.assertEqual([(entry["index"], entry["source"], entry["label"]) for entry in recorded],
                         [(1, "cue", "left"), (2, "cue", "right")])
        self.assertEqual(report["marks"]["cues_not_reached"], [{"offset_ms": 4000, "label": "out_of_view"}])
        origin = report["first_frame"]["monotonic_ns"]
        anchor = report["timebase"]["anchor"]
        for entry, offset_ms in zip(recorded, (300, 1000)):
            self.assertEqual(entry["scheduled_offset_ms"], offset_ms)
            self.assertEqual(entry["scheduled_monotonic_ns"], origin + offset_ms * 1_000_000)
            self.assertEqual(entry["scheduled_utc_ns"] - anchor["utc_ns"], entry["scheduled_monotonic_ns"] - anchor["monotonic_ns"])
            self.assertEqual(entry["utc_ns"] - anchor["utc_ns"], entry["monotonic_ns"] - anchor["monotonic_ns"])
            self.assertTrue(0 <= entry["late_ms"] < 500, entry)
        # Per frame: the best target box and its cell on the image as exported.
        targets = [record["target"] for record in report["detections"]]
        self.assertEqual([target["present"] for target in targets], [False] * 3 + [True] * 17)
        self.assertEqual([target["cell"] for target in targets], [None] * 3 + ["left"] * 7 + ["up_right"] * 10)
        self.assertEqual(targets[3]["box_xyxy"], report["detections"][3]["detections"][1]["box_xyxy"])
        self.assertEqual((targets[3]["confidence"], targets[3]["count"]), (0.8, 1))
        self.assertEqual(report["labels_seen"], ["bottle", "chair"])
        self.assertEqual(report["target"], {"label": "bottle", "threshold": POC_THRESHOLD, "frames_with_target": 17,
                                            "cell_thresholds": [0.4, 0.6], "cell_frame": "image_as_exported",
                                            "orientation_statement": marks.ORIENTATION_STATEMENT})
        self.assertEqual(report["target_label"], "bottle")
        summary = report["summary"]
        self.assertEqual(sum(window["frames"] for window in summary["windows"]), 20)
        self.assertEqual(sum(window["target_frames"] for window in summary["windows"]), 17)
        self.assertEqual(summary["windows"][0]["index"], 0)
        left, right = summary["intervals"]
        self.assertEqual((left["label"], right["label"]), ("left", "right"))
        self.assertEqual(left["frames"] + right["frames"],
                         sum(1 for record in report["detections"]
                             if record["observed_ns"] >= recorded[0]["scheduled_monotonic_ns"]))
        self.assertEqual((left["boundary"], left["duration_ms"]), ("scheduled", 700.0))
        # The cue lands between frames, so a frame either side may fall in the neighbouring interval: most, not all.
        self.assertGreaterEqual(left["cells"]["left"], 5)
        self.assertLessEqual(left["cells"]["up_right"], 1)
        self.assertGreaterEqual(right["cells"]["up_right"], 8)
        self.assertEqual(right["cells"]["left"], 0)
        self.assertEqual((right["columns"]["right"], right["rows"]["up"]), (right["cells"]["up_right"],) * 2)
        (span,) = summary["coverage"]["spans"]
        self.assertEqual((span["labels"], span["ended_by"], span["mark_index"]), (["left", "right"], "run_end", 1))
        self.assertEqual((summary["coverage"]["windows"], summary["coverage"]["windows_with_target"],
                          summary["coverage"]["fraction"]), (1, 1, 1.0))  # 1.6 s in view: one whole window.
        self.assertEqual(summary["removal"], [])

    def test_a_remove_cue_measures_the_helpers_presence_stop(self):
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 20}, (0,),
                                   detector_factory=StandInDetector, confirmed=True, cues=[(0, "remove")],
                                   terminal=mock.Mock(), extra_args=("--fake-scenario", "headset_removed"))
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("helper_ended_early", "headset_removed", 1))
        (mark,) = report["marks"]["recorded"]
        (removal,) = report["summary"]["removal"]
        self.assertEqual((mark["label"], removal["mark_index"], removal["source"]), ("remove", 1, "cue"))
        self.assertEqual(report["helper_end"]["reason"], "headset_removed")
        self.assertEqual(removal["helper_end_reason"], "headset_removed")
        self.assertEqual(removal["helper_end_after_mark_ms"],
                         round((report["helper_end"]["monotonic_ns"] - mark["monotonic_ns"]) / 1e6, 3))
        self.assertTrue(0 < removal["helper_end_after_mark_ms"] < 3000)
        self.assertTrue(removal["helper_end_is_presence_stop"] and removal["helper_end_within_limit"])
        self.assertEqual(removal["frames_acquired_over_limit"], 0)
        self.assertLessEqual(removal["frames_processed_after_mark"], 2)
        self.assertEqual((removal["late_frame_limit_ms"], removal["end_limit_ms"]), (2000, 3000))
        # A helper that runs on after the mark to its bound is reported as it was: not a presence stop.
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 6}, (0,),
                                   detector_factory=StandInDetector, confirmed=True, cues=[(100, "remove")],
                                   terminal=mock.Mock())
        (removal,) = report["summary"]["removal"]
        self.assertEqual((report["status"], removal["helper_end_reason"]), ("recognized", "max_frames"))
        self.assertFalse(removal["helper_end_is_presence_stop"])
        self.assertGreaterEqual(removal["frames_processed_after_mark"], 3)

    def test_enter_on_a_pseudo_terminal_marks_the_run(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        os.write(master, b"\n")  # Before the run: never a mark.
        detector = ScriptedDetector([(0.5, 0.5)] * 12, on_call={3: lambda: os.write(master, b"\n"),
                                                               7: lambda: os.write(master, b"secret words\n"),
                                                               10: lambda: os.write(master, b"\n")})
        terminal = mock.Mock()
        report = command.recognize(self.binary, {"fps": 5, "max_seconds": 5, "max_frames": 12}, (0,),
                                   detector_factory=lambda: detector, confirmed=True, target_label="bottle",
                                   mark_labels=["in_view", "out_of_view"], key_fd=slave, terminal=terminal)
        self.assertEqual((report["status"], report["frames_processed"]), ("recognized", 12))
        recorded = report["marks"]["recorded"]
        self.assertEqual([(entry["source"], entry["label"]) for entry in recorded],
                         [("key", "in_view"), ("key", "out_of_view"), ("key", "mark")])
        self.assertEqual("".join(call.args[0] for call in terminal.write.call_args_list),
                         "MARK in_view\nMARK out_of_view\nMARK mark\n")
        self.assertEqual((report["marks"]["key_presses"], report["marks"]["key_marks_enabled"],
                          report["marks"]["mark_labels_requested"]), (3, True, ["in_view", "out_of_view"]))
        for entry, call in zip(recorded, (3, 7, 10)):  # Each press was made while that frame was being processed.
            self.assertGreaterEqual(entry["monotonic_ns"], report["detections"][call - 1]["observed_ns"])
            self.assertLess(entry["monotonic_ns"], report["helper_end"]["monotonic_ns"])
            self.assertNotIn("scheduled_offset_ms", entry)
        (span,) = report["summary"]["coverage"]["spans"]
        self.assertEqual((span["labels"], span["ended_by"]), (["in_view"], "out_of_view"))
        self.assertEqual([interval["label"] for interval in report["summary"]["intervals"]], ["in_view", "out_of_view", "mark"])
        self.assertEqual(report["summary"]["intervals"][0]["cells"]["centre"], report["summary"]["intervals"][0]["frames"])
        self.assertNotIn("secret", json.dumps(report))

    def test_main_takes_the_typed_yes_and_then_key_marks_from_the_same_pseudo_terminal(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        os.write(master, b"yes\n")
        detector = ScriptedDetector([(0.1, 0.9)] * 8, on_call={2: lambda: os.write(master, b"\n"),
                                                              5: lambda: os.write(master, b"\n")})
        with os.fdopen(slave, "r") as stdin, mock.patch.object(command, "create_detector", return_value=detector), \
                mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr, mock.patch("sys.stdin", stdin):
            code = command.main(["--allow-camera-input", "--helper", str(self.binary), "--seconds", "5", "--max-frames",
                                 "8", "--fps", "5", "--cameras", "0", "--target-label", "bottle", "--mark-labels", "down",
                                 "--cue", "0.2:left", "--cue", "4.5:remove"])
        report = json.loads("".join(call.args[0] for call in stdout.write.call_args_list))
        printed = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual((code, report["status"], report["confirmation"]), (0, "recognized", "operator_terminal"))
        self.assertTrue(printed.startswith(contract.EXPORT_NOTICE + "\n"))
        self.assertTrue(report["capture"]["helper"]["parent_confirmed"])
        self.assertEqual(report["capture"]["stream"]["ready"]["confirmation"], "parent")
        self.assertEqual(sorted((entry["source"], entry["label"]) for entry in report["marks"]["recorded"]),
                         [("cue", "left"), ("key", "down"), ("key", "mark")])
        self.assertEqual([entry["index"] for entry in report["marks"]["recorded"]], [1, 2, 3])
        self.assertCountEqual(printed[printed.index("Type yes to continue: ") + 22:].split("\n"),
                              ["", "MARK down", "MARK mark", "\aCUE left"])
        self.assertEqual(report["marks"]["cues_requested"], [{"offset_ms": 200, "label": "left"},
                                                            {"offset_ms": 4500, "label": "remove"}])
        self.assertEqual(report["marks"]["cues_not_reached"], [{"offset_ms": 4500, "label": "remove"}])
        self.assertTrue(report["marks"]["key_marks_enabled"])
        self.assertEqual({record["target"]["cell"] for record in report["detections"]}, {"down_left"})
        self.assertEqual(report["target"]["frames_with_target"], 8)

    def test_main_without_the_new_options_never_reads_the_terminal_after_the_confirmation(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        os.write(master, b"yes\n")
        threads = []

        def typed_during_the_run():
            threads.extend(thread.name for thread in threading.enumerate())
            os.write(master, b"typed during the run\n")
        for extra, keys in (([], False), (["--cue", "0.1:near", "--target-label", "bottle"], False)):
            detector = ScriptedDetector([(0.5, 0.5)] * 6, on_call={2: typed_during_the_run})
            threads.clear()
            with self.subTest(extra=extra), os.fdopen(os.dup(slave), "r") as stdin, \
                    mock.patch.object(command, "create_detector", return_value=detector), \
                    mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr, mock.patch("sys.stdin", stdin):
                code = command.main(["--allow-camera-input", "--helper", str(self.binary), "--seconds", "5",
                                     "--max-frames", "6", "--fps", "5", "--cameras", "0", *extra])
                report = json.loads("".join(call.args[0] for call in stdout.write.call_args_list))
                printed = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertEqual((code, report["status"], report["frames_processed"]), (0, "recognized", 6))
                self.assertNotIn("camera-marks-keys", threads)  # No key thread exists during the run.
                self.assertEqual((report["marks"]["key_marks_enabled"], report["marks"]["key_presses"]), (False, 0))
                self.assertFalse([entry for entry in report["marks"]["recorded"] if entry["source"] == "key"])
                self.assertNotIn("MARK", printed)
                if not extra:
                    self.assertEqual(report["marks"]["recorded"], [])
                    self.assertTrue(printed.endswith("Type yes to continue: "))  # Nothing printed after the question.
                # The line typed during the run was neither read nor flushed: it is still in the terminal's queue.
                self.assertTrue(select.select([slave], [], [], 1.0)[0])
                self.assertEqual(os.read(slave, 4096), b"typed during the run\n")
                os.write(master, b"yes\n")
        os.read(slave, 4096)
        os.close(slave)

    def test_no_l15_field_can_hold_pixels_free_text_or_calibration_values(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        detector = ScriptedDetector([None, (0.5, 0.5)] * 5, on_call={2: lambda: os.write(master, b"free text\n")})
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 10}, (0,),
                                   detector_factory=lambda: detector, confirmed=True, target_label="bottle",
                                   mark_labels=["near"], key_fd=slave, cues=[(0, "in_view"), (300, "remove")],
                                   terminal=mock.Mock())
        self.assertEqual(report["status"], "recognized")
        self.assertEqual(len(report["marks"]["recorded"]), 3)
        plain = command.recognize(self.binary, LIMITS, (0,), detector_factory=StandInDetector, confirmed=True)
        self.assertEqual(set(report) - set(plain), {"target"})
        added = {name: report[name] for name in L15_REPORT_FIELDS}
        self.assertEqual(set(report) - set(command.new_report("recognize")) - L15_REPORT_FIELDS,
                         {"confirmation", "seconds_requested", "max_frames_requested", "fps_requested",
                          "cameras_requested", "threshold", "detector_requested", "phase", "elapsed_seconds", "capture",
                          "detections", "last_result", *command.TIMINGS})  # Everything else is the report as it was.
        for record in report["detections"]:
            self.assertEqual(set(record) & L15_FRAME_FIELDS, L15_FRAME_FIELDS)
            added[f"frame{record['sequence']}"] = {name: record[name] for name in L15_FRAME_FIELDS}
        allowed = closed_strings()
        for path, leaf in l15_leaves(added, "report"):
            with self.subTest(path=path):
                if type(leaf) is str:
                    self.assertIn(leaf, allowed)  # A label, a class or a declared constant: never free text.
                else:
                    self.assertIn(type(leaf), (int, float, bool, type(None)))
        for name in set(l15_names(added)) - ALLOWED_NAMES:
            with self.subTest(name=name):
                self.assertFalse([part for part in FORBIDDEN_NAME_PARTS if part in name.lower()])
        for numbers in l15_number_lists(added):
            self.assertIn(len(numbers), (2, 4), numbers)  # Cell thresholds, a centre fraction, one box: never a buffer.
        self.assertFalse(report["pixels_in_report"] or report["images_saved"] or report["display_started"])
        self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
        text = json.dumps(report)
        self.assertNotIn("free text", text)
        self.assertNotIn(repr(fake_export.pixels(1)[:12])[2:-1], text)
        self.assertLess(len(text), 200_000)

    def test_large_frames_are_converted_to_at_most_960_pixels(self):
        detector = StandInDetector()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 2}, (0,),
                                   detector_factory=lambda: detector, confirmed=True,
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
                                   detector_factory=lambda: detector, confirmed=True)
        self.assertEqual(report["status"], "recognized")
        self.assertEqual(report["frames_received"], 8)
        self.assertLess(report["frames_processed"], 8)
        self.assertGreater(report["pending_frames_replaced"], 0)
        self.assertEqual(report["frames_processed"] + report["pending_frames_replaced"] + report["stale_inputs_skipped"], 8)
        self.assertEqual(report["detections"][-1]["sequence"], 8)  # The last frame is always processed.

    def test_stale_frames_are_skipped_not_detected(self):
        detector = StandInDetector()
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 3, "max_frames": 3}, (0,),
                                   detector_factory=lambda: detector, confirmed=True,
                                   extra_args=("--fake-scenario", "stale_clock"))
        self.assertEqual((report["status"], report["reason"]), ("no_frames", "all_exported_frames_stale"))
        self.assertEqual((report["frames_received"], report["stale_inputs_skipped"], report["frames_processed"]), (3, 3, 0))
        self.assertEqual(detector.calls, 0)
        self.assertIsNone(report["inference_p95_within_budget"])
        self.assertEqual(report["exit_code"], 1)

    def test_a_refused_stream_and_an_early_helper_end_keep_their_names(self):
        report = command.recognize(self.binary, fake_export.LIMITS, (0, 1), detector_factory=StandInDetector,
                                   confirmed=True, extra_args=("--fake-scenario", "invalid:frame_sequence_gap"))
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("refused", "sequence_not_consecutive", 1))
        # The frame before the gap was delivered; the catalogue's synthetic clocks make it stale, so it is skipped.
        self.assertEqual(report["frames_received"], 1)
        self.assertEqual(report["frames_processed"] + report["stale_inputs_skipped"], 1)
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=StandInDetector,
                                   confirmed=True, extra_args=("--fake-scenario", "headset_removed"))
        self.assertEqual((report["status"], report["reason"]), ("helper_ended_early", "headset_removed"))
        self.assertEqual(report["frames_processed"], 2)
        # Without the parent's confirmation the helper refuses; the command reports the helper's exit, not a stream fault.
        report = command.recognize(self.binary, LIMITS, (0, 1), detector_factory=StandInDetector)
        self.assertEqual((report["status"], report["reason"], report["confirmation"]), ("error", "helper_exit_code", None))

    def test_a_failing_detector_ends_the_run_error_and_stops_the_helper(self):
        class Broken(StandInDetector):
            def detect(self, bgr, threshold):
                raise RuntimeError("model broke")
        report = command.recognize(self.binary, {"fps": 5, "max_seconds": 30, "max_frames": 300}, (0, 1),
                                   detector_factory=Broken, timeout=40, confirmed=True)
        self.assertEqual((report["status"], report["reason"]), ("error", "processing_failed"))
        self.assertIn("model broke", report["error"])
        self.assertIn(report["capture"]["status"], ("stopped", "exported"))
        self.assertEqual(report["exit_code"], 1)

    def test_a_signal_cancels_with_exit_128_plus_signal(self):
        timer = threading.Timer(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        report = command.recognize(self.binary, {"fps": 4, "max_seconds": 30, "max_frames": 300}, (0, 1),
                                   detector_factory=StandInDetector, timeout=40, confirmed=True)
        self.assertEqual((report["status"], report["reason"], report["signal"]), ("cancelled", "signal", signal.SIGTERM))
        self.assertEqual(report["exit_code"], 128 + signal.SIGTERM)
        self.assertTrue(report["cleanup_ok"])

    def test_main_runs_end_to_end_and_writes_only_the_named_report(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            path = Path(temporary) / "run.json"
            terminal = Terminal()
            code, report, notice = run_main(["--allow-camera-input", "--helper", str(self.binary), "--seconds", "3",
                                             "--max-frames", "4", "--fps", "10", "--cameras", "0", "--report", str(path)],
                                            terminal)
            self.assertEqual(code, 0, report)
            self.assertEqual(report["status"], "recognized")
            self.assertEqual((terminal.reads, report["confirmation"]), (1, "operator_terminal"))
            self.assertTrue(notice.startswith(contract.EXPORT_NOTICE))
            self.assertTrue(report["capture"]["helper"]["parent_confirmed"])
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


HD = ("--width", "1920", "--height", "1080", "--stride", "5760")  # The stand-in's frames, large enough to be resized.
LOWER_RIGHT = (0.4, 0.4, 1.0, 1.0)


def view_strings():
    """Every string a region or scan field may hold: closed names and declared constants, never free text."""
    return {*closed_strings(), *region.SCAN_REGION_NAMES, *DETECTORS, region.FRAME_PIXELS, region.ORIENTATION_STATEMENT,
            region.AXIS_STATEMENT, region.COVERAGE_OMITTED, *region.SCAN_FAILURE_REASONS, *region.AXES.values(),
            "region", "scan"}


@unittest.skipUnless(CONVERSION, "NumPy and OpenCV are needed to convert frames")
class RegionOfViewTests(unittest.TestCase):
    """Item L14: a region of view, its boxes over the whole frame, the scan schedule, and the default left as it was."""

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

    def run_region(self, look, script, frames=6, **options):
        detector = ScriptedDetector(script)
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": frames}, (0,),
                                   detector_factory=lambda: detector, confirmed=True, target_label="bottle",
                                   extra_args=HD, terminal=mock.Mock(), region=look, **options)
        self.assertEqual((report["status"], report["exit_code"], report["frames_processed"]), ("recognized", 0, frames),
                         report.get("error"))
        return report, detector

    def test_a_region_is_cropped_before_the_resize_and_its_boxes_are_over_the_whole_frame(self):
        report, detector = self.run_region(region.Region(*LOWER_RIGHT), [(0.5, 0.5)] * 6, cues=[(0, "in_view")])
        # 0.6 of 1920 by 1080 is 1152 by 648 native pixels, which the detector sees at 960 by 540: enlarged 1.67 times
        # over the whole frame at 960.
        self.assertEqual(set(detector.shapes), {(540, 960, 3)})
        self.assertEqual(report["view"], {"mode": "region", "box_coordinate_space": "whole_frame_processing_pixels",
                                          "processing_side": 960, "detector": None,
                                          "orientation_statement": region.ORIENTATION_STATEMENT,
                                          "region": [0.4, 0.4, 1.0, 1.0], "rotation": 0,
                                          "crop_native_xyxy": [768, 432, 1920, 1080], "resized_size": [960, 540]})
        self.assertNotIn("scan", report)
        for record in report["detections"]:
            self.assertEqual((record["box_coordinate_space"], record["box_space_size"], record["detector_input_size"],
                              record["native_size"]),
                             ("whole_frame_processing_pixels", [960, 540], [960, 540], [1920, 1080]))
            self.assertEqual(record["view"], {"index": 0})  # The region is stated once, in the report's ``view``.
            self.assertNotIn("scan", record)
            chair, bottle = record["detections"]
            self.assertEqual(chair["box_xyxy"], [384, 216, 386, 218])  # The crop's corner, in the whole frame.
            # The detector put the bottle in the middle of what it saw: 0.7 of the way across and down the frame.
            self.assertEqual(bottle["box_xyxy"], [671, 377, 673, 379])
            target = record["target"]
            self.assertEqual((target["box_xyxy"], target["cell"], target["column"], target["row"]),
                             ([671, 377, 673, 379], "down_right", "right", "down"))
            self.assertEqual(target["centre_fraction"], [0.7, 0.7])
        # Coverage and the cell tallies are taken on those whole-frame boxes.
        (interval,) = report["summary"]["intervals"]
        self.assertEqual((interval["target_frames"], interval["cells"]["down_right"], interval["cells"]["centre"]),
                         (interval["frames"], interval["frames"], 0))
        self.assertEqual(report["target"]["frames_with_target"], 6)
        self.assertEqual(report["summary"]["coverage"]["spans"][0]["target_frames"],
                         report["summary"]["coverage"]["spans"][0]["frames"])

    def test_every_rotation_maps_a_box_back_to_the_same_place(self):
        # Where the detector must see the bottle, in the turned image, for it to be at (0.5, 0.75) of the crop, which
        # is (0.7, 0.85) of the frame: column right, row down.
        seen_at = {0: (0.5, 0.75), 90: (0.25, 0.5), 180: (0.5, 0.25), 270: (0.75, 0.5)}
        for rotation, centre in seen_at.items():
            with self.subTest(rotation=rotation):
                report, detector = self.run_region(region.Region(*LOWER_RIGHT, rotation=rotation), [centre] * 2, frames=2)
                self.assertEqual(set(detector.shapes), {(540, 960, 3) if rotation in (0, 180) else (960, 540, 3)})
                record = report["detections"][0]
                self.assertEqual(record["detector_input_size"], [960, 540] if rotation in (0, 180) else [540, 960])
                self.assertEqual((record["box_space_size"], record["view"], report["view"]["rotation"]),
                                 ([960, 540], {"index": 0}, rotation))
                target = record["target"]
                self.assertAlmostEqual(target["centre_fraction"][0], 0.7, delta=0.002)
                self.assertAlmostEqual(target["centre_fraction"][1], 0.85, delta=0.003)
                self.assertEqual(target["cell"], "down_right")
                box = target["box_xyxy"]
                self.assertTrue(0 <= box[0] < box[2] <= 960 and 0 <= box[1] < box[3] <= 540)
                self.assertLessEqual(max(box[2] - box[0], box[3] - box[1]), 4)

    def test_a_region_at_the_frames_edge_keeps_its_boxes_inside_the_frame(self):
        look = region.Region(0.9, 0.9, 1.0, 1.0)  # 192 by 108 native pixels in the corner: below 960, so not resized.
        report, detector = self.run_region(look, [(1.0, 1.0)] * 2, frames=2)
        self.assertEqual(set(detector.shapes), {(108, 192, 3)})
        record = report["detections"][0]
        self.assertEqual((report["view"]["crop_native_xyxy"], report["view"]["resized_size"]), ([1728, 972, 1920, 1080],
                                                                                              [192, 108]))
        box = record["target"]["box_xyxy"]  # The detector's box hangs over the image's corner: clamped to the frame.
        self.assertEqual((box[2], box[3]), (960, 540))
        self.assertTrue(864 <= box[0] < box[2] and 486 <= box[1] < box[3])
        self.assertEqual(record["target"]["cell"], "down_right")

    def test_without_a_region_and_with_the_whole_frame_the_run_is_what_it_was(self):
        reports = []
        for look in (None, region.Region()):
            detector = ScriptedDetector([(0.2, 0.5)] * 3)
            report = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 3}, (0,),
                                       detector_factory=lambda detector=detector: detector, confirmed=True,
                                       target_label="bottle", extra_args=HD, terminal=mock.Mock(), region=look)
            self.assertEqual(report["status"], "recognized")
            self.assertEqual(set(detector.shapes), {(540, 960, 3)})  # The whole frame, resized as before.
            reports.append(report)
        plain, whole = reports
        self.assertEqual(set(plain), set(whole))
        for report in reports:
            self.assertFalse({"view", "scan"} & set(report))
            for record in report["detections"]:
                self.assertEqual(record["box_coordinate_space"], "detector_input_pixels")
                self.assertFalse({"view", "scan", "box_space_size"} & set(record))
                self.assertEqual((record["target"]["box_xyxy"], record["target"]["cell"]), ([190, 268, 194, 272], "left"))
        self.assertEqual([set(record) for record in plain["detections"]], [set(record) for record in whole["detections"]])
        parameters = __import__("inspect").signature(command.recognize).parameters
        self.assertEqual((parameters["region"].default, parameters["scan_detectors"].default,
                          parameters["scan_detector_factory"].default), (None, None, None))

    def test_a_region_through_the_whole_frame_agrees_with_no_region(self):
        # The same scene two ways: the whole frame turned by 180 degrees, and unturned. The detector is told where the
        # bottle is in what it sees; both runs must report it at the same place in the frame.
        plain, _ = self.run_region(None, [(0.2, 0.3)] * 2, frames=2)
        turned, _ = self.run_region(region.Region(rotation=180), [(0.8, 0.7)] * 2, frames=2)
        self.assertEqual(turned["detections"][0]["target"]["cell"], plain["detections"][0]["target"]["cell"])
        for value, wanted in zip(turned["detections"][0]["target"]["centre_fraction"],
                                 plain["detections"][0]["target"]["centre_fraction"]):
            self.assertAlmostEqual(value, wanted, delta=0.003)
        self.assertEqual(turned["detections"][0]["box_space_size"], plain["detections"][0]["detector_input_size"])

    def test_malformed_regions_and_scans_are_refused_before_anything_is_loaded(self):
        def loaded():
            raise AssertionError("loaded")
        for options in ({"region": (0.4, 0.4, 1.0, 1.0)}, {"region": "0.4,0.4,1,1"},
                        {"region": region.Region(*LOWER_RIGHT), "scan_detectors": ("mobilenet-ssd",)},
                        {"scan_detectors": ()}, {"scan_detectors": ("yolox-nano",)},
                        {"scan_detectors": ("mobilenet-ssd", "mobilenet-ssd")},
                        {"scan_detectors": ("mobilenet-ssd", "other")}):
            with self.subTest(options=options), \
                    mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")), \
                    self.assertRaises(ValueError):
                command.recognize(self.binary, LIMITS, (0,), detector_factory=loaded, confirmed=True,
                                  target_label="bottle", **options)
        with mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")), \
                self.assertRaises(ValueError):
            command.recognize(self.binary, LIMITS, (0,), detector_factory=loaded, confirmed=True,
                              scan_detectors=("mobilenet-ssd",))  # A scan tallies a target: none was named.

    def test_the_command_line_refuses_a_bad_region_before_the_confirmation_and_the_camera(self):
        base = ["--allow-camera-input", "--helper", "/h", "--seconds", "10", "--max-frames", "10", "--cameras", "0"]
        for extra in (["--region", "0.5,0.5,0.4,0.9"], ["--region", "0,0,1"], ["--region", "0,0,1,1.5"],
                      ["--region", "centre"], ["--region", "0,0,0.01,1"], ["--region", "nan,0,1,1"],
                      ["--rotation", "45"], ["--rotation", "-90"], ["--region", "0.2,0.2,0.8,0.8", "--rotation", "360"]):
            terminal = Terminal()
            with self.subTest(extra=extra), \
                    mock.patch.object(command, "create_detector", side_effect=AssertionError("loaded")), \
                    mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")), \
                    self.assertRaises(SystemExit) as caught:
                run_main(base + extra, terminal)
            self.assertEqual((caught.exception.code, terminal.reads), (2, 0))
        for extra in (["--region", "0.2,0.2,0.8,0.8"], ["--rotation", "90"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                run_main(["--validate"] + extra)
            self.assertEqual(caught.exception.code, 2)

    def test_main_passes_the_region_the_rotation_and_the_detector_and_the_report_records_them(self):
        detector = ScriptedDetector([(0.5, 0.5)] * 4)
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(command, "create_detector", return_value=detector) as create:
            path = Path(temporary) / "run.json"
            code, report, _notice = run_main(
                ["--allow-camera-input", "--helper", str(self.binary), "--seconds", "3", "--max-frames", "4", "--fps", "10",
                 "--cameras", "0", "--report", str(path), "--target-label", "bottle", "--detector", "yolox-nano",
                 "--region", "0.25,0.25,0.75,0.75", "--rotation", "270", "--helper-arg=--width", "--helper-arg=1920",
                 "--helper-arg=--height", "--helper-arg=1080", "--helper-arg=--stride", "--helper-arg=5760"])
            self.assertEqual((code, report["status"]), (0, "recognized"), report.get("error"))
            create.assert_called_once_with("yolox-nano")
            self.assertEqual((report["detector_requested"], report["view"]["detector"]), ("yolox-nano", "yolox-nano"))
            self.assertEqual((report["view"]["mode"], report["view"]["region"], report["view"]["rotation"]),
                             ("region", [0.25, 0.25, 0.75, 0.75], 270))
            self.assertEqual(set(detector.shapes), {(960, 540, 3)})  # Half of 1920 by 1080, not resized, turned.
            self.assertEqual(report["detections"][0]["target"]["cell"], "centre")
            self.assertEqual(json.loads(path.read_text())["view"], report["view"])
            self.assertEqual(sorted(entry.name for entry in Path(temporary).iterdir()), ["run.json"])
        # The whole frame unturned, spelled out, is no region at all.
        with mock.patch.object(command, "create_detector", return_value=StandInDetector()):
            code, report, _notice = run_main(
                ["--allow-camera-input", "--helper", str(self.binary), "--seconds", "3", "--max-frames", "2", "--fps", "10",
                 "--cameras", "0", "--region", "0,0,1,1", "--rotation", "0"])
        self.assertEqual((code, "view" in report, report["detections"][0]["box_coordinate_space"]),
                         (0, False, "detector_input_pixels"))

    def test_a_scan_sees_each_frame_through_the_next_entry_with_every_detector(self):
        schedule = region.scan_schedule()
        # "mobilenet-ssd" (the run's detector) finds the bottle in the middle of whatever it sees; "yolox-nano" never.
        models = {"mobilenet-ssd": ScriptedDetector([(0.5, 0.5)] * 60), "yolox-nano": ScriptedDetector([])}
        report = command.recognize(self.binary, {"fps": 10, "max_seconds": 6, "max_frames": 30}, (0,), confirmed=True,
                                   target_label="bottle", extra_args=HD, terminal=mock.Mock(), cues=[(0, "near")],
                                   scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                   scan_detector_factory=lambda name: models[name])
        self.assertEqual((report["status"], report["exit_code"]), ("recognized", 0), report.get("error"))
        frames = report["frames_processed"]
        self.assertGreaterEqual(frames, 25)  # A slow machine may replace a frame; the schedule follows what was processed.
        self.assertEqual((models["mobilenet-ssd"].calls, models["yolox-nano"].calls), (frames, frames))
        self.assertEqual(models["mobilenet-ssd"].shapes, models["yolox-nano"].shapes)  # One prepared image for both.
        self.assertEqual(report["view"], {"mode": "scan", "box_coordinate_space": "whole_frame_processing_pixels",
                                          "processing_side": 960, "detector": "mobilenet-ssd",
                                          "orientation_statement": region.ORIENTATION_STATEMENT,
                                          "scan_detectors": ["mobilenet-ssd", "yolox-nano"], "schedule_length": 24})
        for number, record in enumerate(report["detections"]):
            entry = schedule[number % 24]
            self.assertEqual(record["view"], {"index": number % 24})  # What that entry is: the scan's schedule.
            self.assertEqual(record["box_coordinate_space"], "whole_frame_processing_pixels")
            self.assertEqual(set(record["scan"]), {"yolox-nano"})  # The run's detector's result is the record's own.
            self.assertNotIn("run_detector_failed", record)
            self.assertFalse(record["scan"]["yolox-nano"]["target"]["present"])
            self.assertEqual(record["scan"]["yolox-nano"]["detection_count"], 1)  # Its chair.
            # The middle of each window, in the whole frame, whatever the turn.
            centre = ((entry.x0 + entry.x1) / 2, (entry.y0 + entry.y1) / 2)
            for value, wanted in zip(record["target"]["centre_fraction"], centre):
                self.assertAlmostEqual(value, wanted, delta=0.004)
        scan = report["scan"]
        self.assertEqual((scan["schedule_length"], scan["frames"], scan["box_coordinate_space"], scan["run_detector"],
                          scan["detectors_failed"]), (24, frames, "whole_frame_processing_pixels", "mobilenet-ssd", []))
        for index, (listed, entry) in enumerate(zip(scan["schedule"], schedule)):
            self.assertEqual({key: listed[key] for key in ("index", "region", "rotation", "name")},
                             dict(index=index, **entry.report()))
            self.assertEqual(listed["crop_native_xyxy"], list(entry.pixels(1920, 1080)))  # Stated once, here.
            self.assertLessEqual(max(listed["resized_size"]), 960)
        found, missed = scan["detectors"]["mobilenet-ssd"], scan["detectors"]["yolox-nano"]
        self.assertEqual((found["frames"], found["target_frames"], found["share"], missed["frames"],
                          missed["target_frames"], missed["best_entry"], found["failed"], missed["failed"]),
                         (frames, frames, 1.0, frames, 0, None, None, None))
        self.assertEqual(sum(entry["frames"] for entry in found["entries"]), frames)
        self.assertEqual([entry["frames"] for entry in found["entries"]],
                         [sum(1 for number in range(frames) if number % 24 == index) for index in range(24)])
        self.assertEqual(sum(entry["frames"] for entry in found["by_rotation"]), frames)
        self.assertEqual([entry["key"] for entry in found["by_axis"]], ["0_180", "90_270"])
        self.assertEqual([entry["key"] for entry in found["by_region"]], list(region.SCAN_REGION_NAMES))
        lower_right = next(entry for entry in found["by_region"] if entry["key"] == "lower_right")
        self.assertAlmostEqual(lower_right["median_centre_fraction"][0], 0.7, delta=0.004)
        self.assertAlmostEqual(lower_right["median_centre_fraction"][1], 0.7, delta=0.004)
        # A scan's frames are 24 different views: nothing in its summary can be read as row G7's coverage.
        summary = report["summary"]
        self.assertEqual((summary["coverage"], summary["coverage_omitted"]), (None, "scan_pools_views"))
        self.assertEqual([interval["label"] for interval in summary["intervals"]], ["near"])
        self.assertFalse({"target_frames", "cells", "windows_with_target"} & {
            key for interval in summary["intervals"] for key, value in interval.items() if value is not None})
        self.assertFalse([window for window in summary["windows"] if "target_frames" in window])

    def scan_with(self, models, frames=30, **options):
        return command.recognize(self.binary, {"fps": 10, "max_seconds": 6, "max_frames": frames}, (0,), confirmed=True,
                                 target_label="bottle", extra_args=HD, terminal=mock.Mock(),
                                 scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                 scan_detector_factory=lambda name: models[name], **options)

    def test_one_scan_detector_failing_does_not_cost_the_other_its_run(self):
        class Raising(ScriptedDetector):
            def __init__(self, script, fail_on, bad_result=False):
                super().__init__(script)
                self.fail_on, self.bad_result = fail_on, bad_result

            def detect(self, bgr, threshold):
                if self.calls + 1 == self.fail_on:
                    self.calls += 1
                    if self.bad_result:
                        return "not a result"
                    raise RuntimeError("private detail that must not reach the report")
                return super().detect(bgr, threshold)
        # The second detector raises on its fourth call: the run's detector carries on to the bound.
        models = {"mobilenet-ssd": ScriptedDetector([(0.5, 0.5)] * 60), "yolox-nano": Raising([(0.5, 0.5)] * 60, 4)}
        report = self.scan_with(models)
        frames = report["frames_processed"]
        self.assertEqual((report["status"], report["exit_code"]), ("recognized", 0), report.get("error"))
        self.assertGreaterEqual(frames, 25)
        self.assertEqual((models["mobilenet-ssd"].calls, models["yolox-nano"].calls), (frames, 4))  # Not used again.
        scan = report["scan"]
        self.assertEqual(scan["detectors_failed"], ["yolox-nano"])
        self.assertEqual(scan["detectors"]["yolox-nano"]["failed"], {"reason": "detector_raised", "frame_index": 3})
        self.assertEqual((scan["detectors"]["yolox-nano"]["frames"], scan["detectors"]["yolox-nano"]["target_frames"],
                          scan["detectors"]["mobilenet-ssd"]["frames"], scan["detectors"]["mobilenet-ssd"]["target_frames"],
                          scan["detectors"]["mobilenet-ssd"]["failed"]), (3, 3, frames, frames, None))
        self.assertEqual([set(record["scan"]) for record in report["detections"]],
                         [{"yolox-nano"}] * 3 + [set()] * (frames - 3))
        self.assertNotIn("private detail", json.dumps(report))  # A closed reason, never the exception's text.
        # The run's own detector returns a malformed result on its third call: the other carries on.
        models = {"mobilenet-ssd": Raising([(0.5, 0.5)] * 60, 3, bad_result=True), "yolox-nano": ScriptedDetector([(0.5, 0.5)] * 60)}
        report = self.scan_with(models)
        frames = report["frames_processed"]
        self.assertEqual((report["status"], frames >= 25), ("recognized", True), report.get("error"))
        scan = report["scan"]
        self.assertEqual((scan["detectors_failed"], scan["detectors"]["mobilenet-ssd"]["failed"]),
                         (["mobilenet-ssd"], {"reason": "invalid_detection_result", "frame_index": 2}))
        self.assertEqual((scan["detectors"]["mobilenet-ssd"]["frames"], scan["detectors"]["yolox-nano"]["frames"],
                          scan["detectors"]["yolox-nano"]["target_frames"]), (2, frames, frames))
        self.assertEqual([record.get("run_detector_failed", False) for record in report["detections"]],
                         [False] * 2 + [True] * (frames - 2))
        self.assertEqual([(record["detections"], record["target"]["present"]) for record in report["detections"][2:]],
                         [([], False)] * (frames - 2))
        self.assertEqual(report["inference_ms"]["count"], 2)  # Its timing covers the frames it looked at.
        # Both fail: the run ends as any run with a failing detector does.
        models = {"mobilenet-ssd": Raising([(0.5, 0.5)] * 60, 2), "yolox-nano": Raising([(0.5, 0.5)] * 60, 4)}
        report = self.scan_with(models)
        self.assertEqual((report["status"], report["reason"], report["exit_code"], report["frames_processed"]),
                         ("error", "processing_failed", 1, 3))
        self.assertEqual(report["scan"]["detectors_failed"], ["mobilenet-ssd", "yolox-nano"])

    def test_outside_a_scan_a_failing_detector_still_ends_the_run(self):
        class Failing(StandInDetector):
            classes = ("bottle", "chair")

            def detect(self, bgr, threshold):
                raise RuntimeError("no")
        report = command.recognize(self.binary, LIMITS, (0,), detector_factory=Failing, confirmed=True,
                                   target_label="bottle", region=region.Region(*LOWER_RIGHT))
        self.assertEqual((report["status"], report["reason"], report["frames_processed"]), ("error", "processing_failed", 0))

    def test_a_full_scan_report_stays_small(self):
        # 150 frames, as a 30-second device run at 5 a second gives, with a hit from both detectors in every frame
        # and as many boxes listed as a frame may list: the largest a scan report gets.
        class Crowded(ScriptedDetector):
            def detect(self, bgr, threshold):
                result = super().detect(bgr, threshold)
                extra = tuple(Detection("chair", 0.6, (n, n, n + 50, n + 50)) for n in range(command.MAX_LISTED_DETECTIONS))
                return DetectionResult(result.detections + extra, result.inference_ms)
        models = {name: Crowded([(0.5, 0.5)] * 200) for name in ("mobilenet-ssd", "yolox-nano")}
        scanned = command.recognize(self.binary, {"fps": 10, "max_seconds": 20, "max_frames": 150}, (0,), confirmed=True,
                                    target_label="bottle", extra_args=(*HD, "--fake-clock", "virtual"), terminal=mock.Mock(),
                                    scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                    scan_detector_factory=lambda name: models[name])
        self.assertEqual(scanned["status"], "recognized", scanned.get("error"))
        frames = scanned["frames_processed"]
        plain_models = Crowded([(0.5, 0.5)] * 200)
        plain = command.recognize(self.binary, {"fps": 10, "max_seconds": 20, "max_frames": 150}, (0,), confirmed=True,
                                  target_label="bottle", extra_args=(*HD, "--fake-clock", "virtual"), terminal=mock.Mock(),
                                  detector_factory=lambda: plain_models)
        self.assertGreaterEqual(min(frames, plain["frames_processed"]), 100)
        per_frame = len(json.dumps(scanned)) / frames
        plain_per_frame = len(json.dumps(plain)) / plain["frames_processed"]
        # A scan record adds its view index, its box space and the second detector's target: under a third more.
        self.assertLess(per_frame, plain_per_frame * 1.35)
        self.assertLess(per_frame * 150 + 20_000, 520_000)

    def test_a_scan_detector_that_cannot_load_or_lacks_the_target_ends_the_run_before_the_helper(self):
        def unavailable(name):
            raise RuntimeError("no runtime")
        with mock.patch.object(command.camera_helper, "run_export", side_effect=AssertionError("launched")):
            report = command.recognize(self.binary, LIMITS, (0,), detector_factory=lambda: ScriptedDetector([]),
                                       confirmed=True, target_label="bottle", scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                       scan_detector_factory=unavailable)
            self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                             ("error", "detector_unavailable", 1))
            report = command.recognize(self.binary, LIMITS, (0,), detector_factory=lambda: ScriptedDetector([]),
                                       confirmed=True, target_label="bottle", scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                       scan_detector_factory=lambda name: StandInDetector())  # Chairs only.
            self.assertEqual((report["status"], report["reason"]), ("error", "target_label_unknown"))
            self.assertFalse({"view", "scan", "capture"} & set(report))

    def test_no_region_or_scan_field_can_hold_pixels_free_text_or_calibration_values(self):
        models = {"mobilenet-ssd": ScriptedDetector([(0.5, 0.5)] * 30), "yolox-nano": ScriptedDetector([None, (0.4, 0.6)] * 15)}
        scanned = command.recognize(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 10}, (0,), confirmed=True,
                                    target_label="bottle", terminal=mock.Mock(),
                                    scan_detectors=("mobilenet-ssd", "yolox-nano"),
                                    scan_detector_factory=lambda name: models[name])
        looked, _ = self.run_region(region.Region(*LOWER_RIGHT, rotation=90), [(0.5, 0.5)] * 3, frames=3)
        plain, _ = self.run_region(None, [(0.5, 0.5)] * 3, frames=3)
        self.assertEqual(scanned["status"], "recognized", scanned.get("error"))
        self.assertEqual((set(scanned) - set(plain), set(looked) - set(plain)), ({"view", "scan"}, {"view"}))
        added = {"scan": scanned["scan"], "scan_view": scanned["view"], "view": looked["view"],
                 "summary": scanned["summary"]}
        for name, report in (("scanned", scanned), ("looked", looked)):
            for record in report["detections"]:
                self.assertEqual(set(record) - set(plain["detections"][0]),
                                 {"view", "box_space_size"} | ({"scan"} if name == "scanned" else set()))
                added[f"{name}{record['sequence']}"] = {key: record[key] for key in (
                    "view", "box_space_size", "scan", "box_coordinate_space", "run_detector_failed") if key in record}
        allowed = view_strings()
        for path, leaf in l15_leaves(added, "report"):
            with self.subTest(path=path):
                if type(leaf) is str:
                    self.assertIn(leaf, allowed)
                else:
                    self.assertIn(type(leaf), (int, float, bool, type(None)))
        for name in set(l15_names(added)) - ALLOWED_NAMES - set(DETECTORS):
            with self.subTest(name=name):
                self.assertFalse([part for part in FORBIDDEN_NAME_PARTS if part in name.lower()])
        for numbers in l15_number_lists(added):
            self.assertIn(len(numbers), (2, 4), numbers)  # A size, a centre fraction, one box or region: never a buffer.
        for report in (scanned, looked):
            self.assertFalse(report["pixels_in_report"] or report["images_saved"] or report["display_started"])
            self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
            self.assertLess(len(json.dumps(report)), 200_000)


class StandInProgramTests(unittest.TestCase):
    def test_the_stand_in_refuses_the_wrong_arguments_as_the_helper_does(self):
        # In a child: the stand-in blocks stop signals for its process as the helper does, which a test process
        # must never inherit (every later signal test would hang).
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            binary = fake_camera_export.install(temporary)
            for arguments in (["--probe-frames", "1"], ["--export-frames", "--fps", "1"]):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run([str(binary), *arguments], capture_output=True, timeout=60)
                    self.assertEqual(completed.returncode, 64)
                    # argparse writes its own usage line first; the stand-in's record is the last line.
                    self.assertEqual(json.loads(completed.stderr.splitlines()[-1])["reason"], "invalid_arguments")
                    self.assertEqual(completed.stdout, b"")

    def test_no_camera_test_leaves_stop_signals_blocked_in_this_process(self):
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()) & {signal.SIGINT, signal.SIGTERM}, set())

    def test_the_catalogue_limits_are_within_the_contract(self):
        for name, (low, high) in contract.LIMITS.items():
            self.assertTrue(low <= fake_export.LIMITS[name] <= high)


if __name__ == "__main__":
    unittest.main()
