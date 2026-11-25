"""``camera_guide``, the sixteenth command: the guided window for one bounded camera recognition run.

Cloud checks only; no camera, no SDK, no display (Qt runs offscreen). Four
layers, each skipping only for what it needs. Standard library: the
command's place, its bounds, the notice derived from the contract, the
scripts and what the run screen may show (``guide.Guide``), the command
line. NumPy and OpenCV: the worker process against the executable stand-in
helper (``tests/fake_camera_export.py``). PySide6: the window, driven with
Qt's test tools, a stand-in worker and a clock the test owns. All three and
g++: ``--validate`` against the helper's fake build.
"""
import ast
import contextlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit import gates  # noqa: E402
from sightcircuit.camera import confirmation, contract, marks, native  # noqa: E402
from sightcircuit.camera import guide as guidance  # noqa: E402
from sightcircuit.commands import COMMANDS, camera_recognize  # noqa: E402
from sightcircuit.commands import camera_guide as command  # noqa: E402
from sightcircuit.frame import Detection  # noqa: E402
from sightcircuit.runtime.supervision import RECORD_AND_POLL  # noqa: E402
from tests import fake_camera_export  # noqa: E402
from tests.test_camera_recognize import FORBIDDEN_NAME_PARTS, l15_leaves, l15_names  # noqa: E402

NUMPY_CV2 = bool(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"))
PYSIDE = importlib.util.find_spec("PySide6") is not None
MS = 1_000_000
SECOND = 1_000_000_000
T0 = 1_000 * SECOND
# The camera export contract's digest at 7f3afc9, the revision the Frame's helper was rebuilt at on 2026-11-19.
# This command changes no contract value, so a helper built there stays valid; a deliberate contract change moves
# this value with the generated header.
CONTRACT_DIGEST = "f3afd174074dd70fcfde4cbdf4cd4f039bb4510c8da4fbc1785c0bebb0e600a4"
ALL_CLOSED = {name: False for name in gates.claims()}


def run_main(arguments):
    """``main(arguments)``: ``(exit code, report or None, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = command.main([str(item) for item in arguments])
        except SystemExit as error:
            return error.code, None, err.getvalue()
    return code, json.loads(out.getvalue()), err.getvalue()


def imported_modules(path):
    tree = ast.parse(Path(path).read_text())
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


class BoundaryTests(unittest.TestCase):
    """The command's place among the commands, what it may import and the bounds it takes from ``camera_recognize``."""

    def test_it_is_the_sixteenth_command_with_a_wrapper_and_an_entry_point(self):
        import tomllib
        self.assertEqual((len(COMMANDS), COMMANDS[-1], COMMANDS[-2]), (16, "camera_guide", "camera_hud"))
        entry_points = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertEqual(entry_points["sightcircuit-camera-guide"], "sightcircuit.commands.camera_guide:main")
        self.assertEqual(len(entry_points), 16)
        self.assertTrue((ROOT / "scripts/camera_guide.py").is_file())

    def test_it_never_reaches_the_bridge_steamvr_the_pipeline_or_a_gate_check(self):
        for path in (ROOT / "sightcircuit/commands/camera_guide.py", ROOT / "sightcircuit/camera/guide.py"):
            modules = imported_modules(path)
            for part in ("hud", "overlay", "openvr", "pipeline", "mirror", "scope", "manifest", "replay", "bridge"):
                with self.subTest(module=path.name, part=part):
                    self.assertFalse([name for name in modules if part in name.lower()])
        # The part with no window in it imports the marks and the confirmation's name, and nothing else.
        tree = ast.parse((ROOT / "sightcircuit/camera/guide.py").read_text())
        self.assertEqual(sorted((node.module or "", node.level, tuple(alias.name for alias in node.names))
                                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)),
                         [("", 1, ("marks",)), ("confirmation", 1, ("OPERATOR_WINDOW",)), ("dataclasses", 0, ("dataclass",))])
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Import)])

    def test_no_image_is_loaded_shown_saved_or_grabbed_anywhere_in_it(self):
        for path in (ROOT / "sightcircuit/commands/camera_guide.py", ROOT / "sightcircuit/camera/guide.py"):
            code = "".join(line for line in path.read_text().splitlines(True))
            tree = ast.parse(code)
            names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            for forbidden in ("QImage", "QPixmap", "QIcon", "QMovie", "QImageReader", "QImageWriter", "QClipboard",
                              "grab", "grabWindow", "save", "imwrite", "imread", "imshow", "drawImage", "drawPixmap",
                              "to_bgr", "payload", "QMediaPlayer", "QCamera"):
                with self.subTest(module=path.name, name=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_the_bounds_are_camera_recognizes_and_are_not_raised(self):
        self.assertEqual((command.MAX_SECONDS, command.MAX_FRAMES, command.MAX_FPS, command.DEFAULT_FPS),
                         (camera_recognize.MAX_SECONDS, camera_recognize.MAX_FRAMES, camera_recognize.MAX_FPS,
                          camera_recognize.DEFAULT_FPS))
        self.assertEqual((command.MAX_SECONDS, command.MAX_FRAMES, command.DEFAULT_FPS, command.LEFT_CAMERA), (30, 300, 5, 0))
        self.assertEqual(command.run_limits("removal"), {"fps": 5, "max_seconds": 30, "max_frames": 150})
        self.assertEqual(command.run_limits("positions"), {"fps": 5, "max_seconds": 30, "max_frames": 150})
        self.assertEqual(command.run_limits("removal", 30, None, 10), {"fps": 10, "max_seconds": 30, "max_frames": 300})
        for arguments in (("removal", 31), ("removal", 0), ("removal", 30, 301), ("removal", 30, 0), ("removal", 30, None, 11),
                          ("removal", 30.0), ("removal", 30, None, 0)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                command.run_limits(*arguments)
        parser = command.build_parser()
        for arguments in (["--seconds", "31"], ["--max-frames", "301"], ["--fps", "11"], ["--camera", "2"],
                          ["--script", "other"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_each_script_fits_the_time_it_asks_for_and_uses_only_the_vocabulary(self):
        self.assertEqual(tuple(guidance.SCRIPTS), ("removal", "positions", "scan"))
        # The scan (item L14) is one instruction for the whole run, with no click: the bar runs for 28 of the 30 s,
        # and for the run's own bound less two seconds when the bound is shorter.
        self.assertEqual([(step.label, step.advance, step.record_ms, step.needs_ms, step.button)
                          for step in guidance.SCAN_STEPS], [("near", "clock", 28000, 5000, "")])
        self.assertEqual([guidance.scan_steps(seconds)[0].record_ms for seconds in (30, 20, 10, 2)],
                         [28000, 18000, 8000, 1000])
        self.assertEqual((guidance.SCAN_MIN_SECONDS, guidance.SCAN_MIN_FRAMES), (10, 48))
        self.assertEqual(command.run_limits("scan"), {"fps": 5, "max_seconds": 30, "max_frames": 150})
        self.assertEqual([step.label for step in guidance.REMOVAL_STEPS], ["near", "far", "remove"])
        self.assertEqual([step.label for step in guidance.POSITION_STEPS],
                         ["centre", "left", "right", "up", "down", "out_of_view"])
        self.assertEqual([(step.advance, step.record_ms) for step in guidance.REMOVAL_STEPS],
                         [("clock", 4000), ("clock", 6000), ("click", 0)])  # One click, the last.
        self.assertEqual(sum(step.advance == "click" for step in guidance.REMOVAL_STEPS + guidance.POSITION_STEPS), 1)
        # The removal's one button comes ten seconds after the reader is ready: at least fifteen are left even after
        # a five-second launch.
        self.assertEqual(sum(step.record_ms for step in guidance.REMOVAL_STEPS), 10_000)
        self.assertEqual((command.GUARD_SECONDS, command.START_DWELL_SECONDS, command.CLOSE_DWELL_SECONDS), (1.2, 5.0, 3.0))
        self.assertEqual([(step.advance, step.record_ms) for step in guidance.POSITION_STEPS],
                         [("clock", 4000)] * 5 + [("clock", 3000)])
        for script, steps in guidance.SCRIPTS.items():
            self.assertLessEqual(guidance.DEFAULT_SECONDS[script], command.MAX_SECONDS)
            self.assertLessEqual(sum(step.record_ms for step in steps), guidance.DEFAULT_SECONDS[script] * 1000)
            for step in steps:
                self.assertIn(step.label, marks.VOCABULARY)
                self.assertIn(step.picture, guidance.PICTURES)
        self.assertEqual(guidance.WATCH_SECONDS, 30)

    def test_camera_recognize_keeps_its_own_start_and_limits_by_default(self):
        parameters = inspect.signature(camera_recognize.recognize).parameters
        self.assertEqual(parameters["confirmation"].default, "operator_terminal")
        self.assertEqual((parameters["on_session"].default, parameters["on_ready"].default), (None, None))
        self.assertEqual((parameters["late_frame_limit_ms"].default, parameters["removal_end_limit_ms"].default), (2000, 3000))
        # The terminal command passes none of the guide's parameters, and reads no click.
        tree = ast.parse((ROOT / "sightcircuit/commands/camera_recognize.py").read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "recognize"]
        self.assertEqual(len(calls), 1)
        self.assertFalse({keyword.arg for keyword in calls[0].keywords}
                         & {"confirmation", "on_session", "on_ready", "late_frame_limit_ms", "removal_end_limit_ms"})
        self.assertNotIn("camera_guide", (ROOT / "sightcircuit/commands/camera_recognize.py").read_text().replace(
            "``camera_guide``", ""))

    def test_no_contract_value_moved(self):
        self.assertEqual(native.contract_digest(), CONTRACT_DIGEST)
        header = (ROOT / "tools/camera_export_protocol.hpp").read_text()
        self.assertIn(f'kContractDigest[] = "{CONTRACT_DIGEST}";', header)
        self.assertEqual(contract.PARENT_CONFIRMED_FLAG, "--parent-confirmed")


class NoticeTests(unittest.TestCase):
    """The window shows the contract's notice without its instruction to type; the two cannot drift apart."""

    def test_the_body_is_the_export_notice_without_its_typed_closing(self):
        body = confirmation.export_notice_body()
        self.assertEqual(confirmation.TYPED_CLOSING, " Type yes to continue.")
        self.assertEqual(body + confirmation.TYPED_CLOSING, contract.EXPORT_NOTICE)
        self.assertTrue(body.endswith("for 30 seconds afterwards."))
        self.assertNotIn("Type ", body)
        self.assertNotIn("yes", body)
        self.assertEqual((confirmation.OPERATOR_WINDOW, confirmation.OPERATOR_TERMINAL),
                         ("operator_window", "operator_terminal"))

    def test_a_notice_that_no_longer_ends_that_way_is_refused_not_shown(self):
        for text in (contract.EXPORT_NOTICE + " More.", contract.EXPORT_NOTICE.replace("Type yes", "Say yes"), ""):
            with self.subTest(text=text[-30:]), mock.patch.object(contract, "EXPORT_NOTICE", text), \
                    self.assertRaises(ValueError):
                confirmation.export_notice_body()

    def test_the_typed_start_of_the_terminal_commands_is_unchanged(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        shown = io.StringIO()
        limits = {"fps": 5, "max_seconds": 30, "max_frames": 150}
        self.assertIsNone(confirmation.confirm_on_terminal(limits, (0,), stdin=Terminal("yes\n"), output=shown))
        self.assertEqual(shown.getvalue(), contract.EXPORT_NOTICE + "\nBounds: 150 frames, 30 seconds, 5 frames a "
                         "second, cameras 0.\nType yes to continue: ")
        self.assertEqual(confirmation.confirm_on_terminal(limits, (0,), stdin=Terminal("start\n"), output=io.StringIO()),
                         "confirmation_declined")


def guide(script="removal", seconds=30, **options):
    """A guide whose Start click and reader ready are both ``T0``, unless said otherwise."""
    made = guidance.Guide(script, seconds, started_ns=options.pop("started_ns", T0), **options)
    return made


class GuideTests(unittest.TestCase):
    """The script's state: what the run screen shows is the script, the click and the clock, and nothing else."""

    def test_before_the_reader_is_ready_the_screen_counts_and_moves_and_then_says_what_to_do(self):
        waiting = guide("positions")
        self.assertEqual(waiting.advance(T0), [])
        self.assertEqual(waiting.advance(T0 + 8 * SECOND, clicked=True), [])
        view = waiting.view(T0 + 3200 * MS)
        self.assertEqual((view.state, view.instruction, view.detail, view.picture, view.progress, view.button),
                         ("starting", guidance.TEXT_STARTING, "Waiting 3 s", "wait", None, ""))
        self.assertEqual(view.remaining_seconds, 27)  # The budget is counted from the Start click, ready or not.
        self.assertEqual([waiting.view(T0 + ms * MS).motion for ms in (0, 500, 750, 1500, 2250)], [0.0, 0.333, 0.5, 0.0, 0.5])
        late = waiting.view(T0 + 12 * SECOND)
        self.assertEqual(late.detail, f"Waiting 12 s. {guidance.TEXT_STARTING_HELP}")
        self.assertEqual(waiting.made, [])

    def test_the_time_left_is_counted_from_the_start_click_and_the_steps_from_ready(self):
        late = guide("positions")
        late.ready(T0 + 5 * SECOND)  # The helper took five seconds to be ready.
        self.assertEqual(late.advance(T0 + 5 * SECOND), [("centre", T0 + 5 * SECOND)])
        view = late.view(T0 + 7 * SECOND)
        self.assertEqual((view.remaining_seconds, view.progress, view.picture), (23, 0.5, "centre"))
        self.assertEqual(late.advance(T0 + 9 * SECOND), [("left", T0 + 9 * SECOND)])
        self.assertEqual(late.view(T0 + 31 * SECOND).remaining_seconds, 0)

    def test_the_positions_script_advances_by_itself_and_marks_each_place_when_it_appears(self):
        run = guide("positions")
        run.ready(T0)
        made, shown = [], []
        for tick in range(0, 25_000, 50):  # A 50 ms clock, as the window's.
            now = T0 + tick * MS
            made += run.advance(now)
            shown.append(run.view(now))
        self.assertEqual(made, [("centre", T0), ("left", T0 + 4000 * MS), ("right", T0 + 8000 * MS),
                                ("up", T0 + 12000 * MS), ("down", T0 + 16000 * MS), ("out_of_view", T0 + 20000 * MS)])
        self.assertTrue(run.finished and not run.out_of_time)
        self.assertEqual(run.not_reached(), [])
        self.assertEqual({view.button for view in shown}, {""})  # No click after Start.
        at = {tick: shown[tick // 50] for tick in (0, 2000, 4000, 9000, 22000, 23000)}
        self.assertEqual((at[0].state, at[0].instruction, at[0].picture, at[0].progress, at[0].remaining_seconds),
                         ("recording", "Hold the bottle in the centre of your view", "centre", 0.0, 30))
        self.assertEqual((at[2000].progress, at[2000].step_number, at[2000].step_count), (0.5, 1, 6))
        self.assertEqual((at[4000].instruction, at[4000].picture, at[4000].progress), ("Hold the bottle to your left", "left", 0.0))
        self.assertEqual((at[9000].picture, at[9000].progress, at[9000].remaining_seconds), ("right", 0.25, 21))
        self.assertEqual((at[22000].instruction, at[22000].picture), ("Take the bottle out of view", "out_of_view"))
        self.assertEqual((at[23000].state, at[23000].instruction, at[23000].picture, at[23000].progress),
                         ("finished", guidance.TEXT_FINISHED, "done", None))

    def test_the_removal_script_needs_one_click_and_leaves_time_for_the_removal(self):
        run = guide("removal", target="cup")
        run.ready(T0)
        # ``near`` at once: the cup is already held at half a metre when Start is clicked.
        self.assertEqual(run.advance(T0), [("near", T0)])
        view = run.view(T0 + 2000 * MS)
        self.assertEqual((view.state, view.instruction, view.button, view.picture, view.progress),
                         ("recording", "Keep holding the cup about half a metre in front of you", "", "near", 0.5))
        self.assertEqual(run.advance(T0 + 3000 * MS, clicked=True), [])  # There is nothing to click yet.
        # ``far`` when its instruction appears, by itself: about two seconds to move and four recorded.
        self.assertEqual(run.advance(T0 + 4000 * MS), [("far", T0 + 4000 * MS)])
        view = run.view(T0 + 7000 * MS)
        self.assertEqual((view.state, view.instruction, view.button, view.picture, view.progress),
                         ("recording", "Now move it to about a metre away, and hold it there", "", "far", 0.5))
        # The one button appears ten seconds after ready, with twenty of the thirty seconds left.
        self.assertEqual(run.advance(T0 + 10000 * MS), [])
        view = run.view(T0 + 10000 * MS)
        self.assertEqual((view.state, view.instruction, view.button, view.button_enabled, view.picture,
                          view.remaining_seconds),
                         ("waiting", "Click, then take the headset off. Put it back on when you like",
                          "I am taking it off now", False, "headset_off", 20))
        self.assertGreaterEqual(view.remaining_seconds, 15)
        # A click before the button has been up for the guard interval is not the wearer's answer to it.
        self.assertEqual(run.advance(T0 + 10600 * MS, clicked=True), [])
        self.assertTrue(run.view(T0 + 11200 * MS).button_enabled)
        self.assertEqual(run.advance(T0 + 12000 * MS, clicked=True), [("remove", T0 + 12000 * MS)])
        view = run.view(T0 + 12000 * MS)
        self.assertEqual((view.state, view.instruction, view.button, view.progress),
                         ("finished", "You can take the headset off now", "", None))
        self.assertEqual(run.advance(T0 + 13000 * MS, clicked=True), [])
        self.assertEqual([entry["label"] for entry in run.made], ["near", "far", "remove"])
        self.assertTrue(run.reached("remove") and not guide("removal").reached("remove"))
        self.assertEqual([(entry["step"], entry["advance"], entry["reached"], entry["monotonic_ns"])
                          for entry in run.steps_report()],
                         [(1, "clock", True, T0), (2, "clock", True, T0 + 4000 * MS), (3, "click", True, T0 + 12000 * MS)])

    def test_a_step_that_can_no_longer_fit_is_taken_down_when_that_becomes_true_not_on_the_click(self):
        run = guide("removal")
        run.ready(T0)
        for tick in range(0, 24_950, 50):
            run.advance(T0 + tick * MS)
        view = run.view(T0 + 24_900 * MS)
        self.assertEqual((view.state, view.button, view.button_enabled, view.remaining_seconds),
                         ("waiting", "I am taking it off now", True, 6))
        # At 25 s there are 5 s left, which the step still fits; one tick later it does not, and the button goes.
        self.assertEqual(run.advance(T0 + 25_000 * MS), [])
        self.assertEqual(run.view(T0 + 25_000 * MS).state, "waiting")
        self.assertEqual(run.advance(T0 + 25_050 * MS), [])
        view = run.view(T0 + 25_050 * MS)
        self.assertEqual((view.state, view.instruction, view.button, view.button_enabled),
                         ("out_of_time", guidance.TEXT_OUT_OF_TIME, "", False))
        self.assertEqual(run.advance(T0 + 25_100 * MS, clicked=True), [])  # No mark could be recorded: none is made.
        self.assertEqual(run.not_reached(), [{"step": 3, "label": "remove"}])
        # A click on the very tick that finds the time gone is not accepted either.
        edge = guide("removal")
        edge.ready(T0)
        for tick in range(0, 25_050, 50):
            edge.advance(T0 + tick * MS)
        self.assertEqual(edge.advance(T0 + 25_050 * MS, clicked=True), [])
        self.assertTrue(edge.out_of_time)
        # A reader that is ready late leaves less: the positions script then stops where it no longer fits.
        short = guide("positions", 10)
        short.ready(T0)
        labels = [label for tick in range(0, 10_000, 50) for label, _ in short.advance(T0 + tick * MS)]
        self.assertEqual(labels, ["centre", "left"])  # At 8 s, 2 s are left and the next place needs 4.
        self.assertEqual([entry["label"] for entry in short.not_reached()], ["right", "up", "down", "out_of_view"])

    def test_stop_ends_the_marks(self):
        run = guide("positions")
        run.ready(T0)
        run.advance(T0)
        run.stop()
        self.assertEqual(run.advance(T0 + 9000 * MS), [])
        view = run.view(T0 + 9000 * MS)
        self.assertEqual((view.state, view.instruction, view.button), ("stopping", guidance.TEXT_STOPPING, ""))

    def test_the_view_is_a_function_of_the_script_the_click_and_the_clock(self):
        # The same script, start, ready time and click give the same screen at every instant, in a guide that was
        # never shown anything else: ``Guide`` has no input a camera frame could reach.
        for script, clicks in (("removal", {13000}), ("positions", set())):
            first, second = guide(script), guide(script)
            for run in (first, second):
                run.ready(T0 + 700 * MS)
            for tick in range(0, 30_000, 50):
                now = T0 + tick * MS
                self.assertEqual(first.advance(now, tick in clicks), second.advance(now, tick in clicks))
                self.assertEqual(first.view(now), second.view(now))
        self.assertEqual(list(inspect.signature(guidance.Guide.view).parameters), ["self", "now_ns"])
        self.assertEqual(list(inspect.signature(guidance.Guide.advance).parameters), ["self", "now_ns", "clicked"])
        self.assertEqual(list(inspect.signature(guidance.Guide.ready).parameters), ["self", "now_ns"])
        self.assertEqual([field for field in guidance.View.__dataclass_fields__],
                         ["state", "instruction", "detail", "picture", "motion", "progress", "remaining_seconds", "button",
                          "button_enabled", "step_number", "step_count"])

    def test_the_scan_is_one_instruction_from_ready_to_the_end_of_the_run(self):
        run = guide("scan")
        self.assertEqual(guidance.plan_lines("scan", "cup")[1],
                         "Before you click Start: hold the cup upright, straight ahead of you, about half a metre away.")
        self.assertIn("keep it there when you click Start", guidance.start_reminder("scan"))
        self.assertEqual(run.view(T0 + SECOND).state, "starting")
        run.ready(T0 + 500 * MS)
        self.assertEqual(run.advance(T0 + 500 * MS), [("near", T0 + 500 * MS)])  # The one mark, when the reader is ready.
        text = "Hold the bottle upright, straight ahead of you, about half a metre away. Keep it still"
        for at_ms in (600, 5000, 14500, 28400):
            self.assertEqual(run.advance(T0 + at_ms * MS, clicked=True), [])  # No click does anything.
            view = run.view(T0 + at_ms * MS)
            self.assertEqual((view.state, view.instruction, view.picture, view.button, view.step_number, view.step_count),
                             ("recording", text, "near", "", 1, 1))
        self.assertEqual(run.view(T0 + 14500 * MS).progress, 0.5)
        self.assertEqual(run.view(T0 + 14500 * MS).remaining_seconds, 16)
        run.advance(T0 + 28600 * MS)
        self.assertEqual((run.view(T0 + 28600 * MS).state, run.view(T0 + 28600 * MS).instruction),
                         ("finished", guidance.TEXT_FINISHED))
        self.assertEqual((run.not_reached(), run.out_of_time, [entry["label"] for entry in run.made]), ([], False, ["near"]))
        short = guide("scan", seconds=12)  # The bar follows the run's bound: ten of twelve seconds.
        short.ready(T0)
        short.advance(T0)
        self.assertEqual((short.view(T0 + 5 * SECOND).progress, short.view(T0 + 5 * SECOND).remaining_seconds,
                          short.steps_report()[0]["record_ms"]), (0.5, 7, 10000))
        short.advance(T0 + 9900 * MS)
        self.assertEqual(short.view(T0 + 9900 * MS).state, "recording")
        short.advance(T0 + 10100 * MS)
        self.assertEqual(short.view(T0 + 10100 * MS).state, "finished")
        late = guide("scan")  # A launch that left under five seconds: the step does not begin.
        late.ready(T0 + 26 * SECOND)
        self.assertEqual(late.advance(T0 + 26 * SECOND), [])
        self.assertTrue(late.out_of_time)
        self.assertEqual(guidance.static_texts("scan"), {text, guidance.TEXT_STARTING, guidance.TEXT_STOPPING,
                                                          guidance.TEXT_FINISHED, guidance.TEXT_OUT_OF_TIME})

    def test_every_text_the_run_screen_can_show_is_the_scripts(self):
        for script in guidance.SCRIPTS:
            texts = guidance.static_texts(script)
            run = guide(script)
            seen = {run.view(T0).instruction}
            run.ready(T0)
            for tick in range(0, 31_000, 50):
                run.advance(T0 + tick * MS, clicked=tick == 14000)
                view = run.view(T0 + tick * MS)
                seen.update(text for text in (view.instruction, view.button) if text)
                self.assertEqual(view.detail, "")  # Only the starting screen has a second line.
            run.stop()
            seen.add(run.view(T0).instruction)
            self.assertLessEqual(seen, texts)
            self.assertLessEqual({guidance.TEXT_STARTING, guidance.TEXT_STOPPING}, seen)

    def test_only_the_two_scripts_and_a_scale_up_to_one(self):
        for arguments in (("other", 30, 1.0), ("removal", 30, 0), ("removal", 30, 1.5)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                guidance.Guide(arguments[0], arguments[1], started_ns=T0, scale=arguments[2])
        fast = guide("positions", 3, scale=0.1)
        fast.ready(T0)
        self.assertEqual([label for tick in range(0, 3000, 10) for label, _ in fast.advance(T0 + tick * MS)],
                         [step.label for step in guidance.POSITION_STEPS])

    def test_the_plan_shown_before_start_says_what_each_script_will_ask(self):
        removal = guidance.plan_lines("removal", "cup")
        self.assertEqual(removal[0], "This run takes about 30 seconds. You click once during it.")
        self.assertIn("hold the cup about half a metre in front of you. Keep holding it when you click Start", removal[1])
        self.assertEqual(len(removal), 4)
        positions = guidance.plan_lines("positions")
        self.assertEqual(positions[0], "This run takes about 30 seconds. No click is needed after Start.")
        self.assertIn("centre, left, right, up, down", positions[1])
        self.assertEqual(guidance.start_reminder("removal"),
                         "Hold the bottle about half a metre in front of you, and keep holding it when you click Start.")


def window_mark(index, label, monotonic_ns):
    return {"index": index, "source": "window", "label": label, "monotonic_ns": monotonic_ns, "utc_ns": monotonic_ns + 5,
            "offset_ms": 0.0}


def target_frame(at_ms, centre=(50, 50)):
    class Box:
        label, confidence = "bottle", 0.8

        def __init__(self, x, y):
            self.box_xyxy = (x - 5, y - 5, x + 5, y + 5)
    found = [] if centre is None else [Box(*centre)]
    return {"observed_ns": T0 + at_ms * MS, "target": marks.target_of(found, "bottle", 0.5, 100, 100)}


FRAMES = tuple(target_frame(at_ms) for at_ms in range(0, 3000, 200))


def finished_report(recorded=(), *, status="recognized", reason=None, frames=FRAMES, helper_end=None, target="bottle",
                    cleanup_ok=True):
    """A report shaped as ``recognize()``'s, from stand-in records: no helper, no detector."""
    report = camera_recognize.new_report("recognize")
    clock = marks.Clock(T0, 1_795_000_000 * SECOND)
    if helper_end is None and reason in marks.PRESENCE_END_REASONS:
        helper_end = {"monotonic_ns": T0 + 20 * SECOND, "reason": reason}
    report.update(status=status, reason=reason, confirmation="operator_window", exit_code=0 if status == "recognized" else 1,
                  timebase=clock.report(), target_label=target, frames_processed=len(frames), frames_received=len(frames),
                  cleanup_ok=cleanup_ok, helper_end=helper_end,
                  marks={"recorded": list(recorded)}, inference_ms={"p95": 41.6},
                  summary=marks.summarize(list(frames), list(recorded), origin_ns=T0, run_end_ns=T0 + 30 * SECOND,
                                          helper_end=helper_end, with_target=True))
    return report


class ResultTests(unittest.TestCase):
    """The result screen's words and the report's ``guide`` block: counts, closed labels and timings only."""

    def test_a_removal_run_is_put_into_plain_words_with_the_measured_values(self):
        recorded = [window_mark(1, "near", T0 + 1000 * MS), window_mark(2, "far", T0 + 7000 * MS),
                    window_mark(3, "remove", T0 + 13000 * MS)]
        frames = [target_frame(at_ms, None if 4000 <= at_ms < 5000 else (50, 50)) for at_ms in range(0, 15400, 200)]
        report = finished_report(recorded, status="helper_ended_early", reason="headset_removed", frames=frames,
                                 helper_end={"monotonic_ns": T0 + 17300 * MS, "reason": "headset_removed"})
        self.assertEqual(guidance.result_lines(report), [
            "The run ended when the headset came off (headset_removed).",
            "77 frames were looked at.",
            "bottle seen in 11 of 12 seconds while it was held in view.",
            "near: bottle in 25 of 30 frames.",
            "far: bottle in 30 of 30 frames.",
            "After your click to take the headset off: the camera reader ended 4.3 s later (headset_removed).",
            "11 frames came after the click, the last 2.2 s after it.",
            "The reader ended 2.1 s after its last frame.",
            "SteamVR's headset-off signal was not armed for this run.",  # No presence watcher was named (2026-11-20).
            "Detection time, 95th percentile: 42 ms."])
        self.assertTrue(guidance.watch_needed(report))

    def test_a_presence_end_before_the_remove_click_does_not_say_the_headset_came_off(self):
        for reason in marks.PRESENCE_END_REASONS:
            report = finished_report([window_mark(1, "near", T0)], status="helper_ended_early", reason=reason)
            line = guidance.result_lines(report)[0]
            self.assertEqual(line, f"The camera reader ended early ({reason}), before the click to take the headset off.")
            self.assertNotIn("came off", line)

    def test_a_positions_run_tallies_each_place_on_the_image_as_exported(self):
        steps = (("centre", (50, 50)), ("left", (80, 50)), ("right", (20, 50)), ("up", (50, 20)), ("down", (50, 80)))
        recorded = [window_mark(index + 1, label, T0 + index * 4000 * MS) for index, (label, _) in enumerate(steps)]
        recorded.append(window_mark(6, "out_of_view", T0 + 20000 * MS))
        frames = [target_frame(at_ms, steps[at_ms // 4000][1] if at_ms < 20000 else None) for at_ms in range(100, 23000, 200)]
        report = finished_report(recorded, frames=frames)
        report["guide"] = {"steps_not_reached": []}
        self.assertEqual(guidance.result_lines(report), [
            "The run finished on its bound.",
            "115 frames were looked at.",
            "bottle seen in 20 of 20 seconds while it was held in view.",
            "centre: bottle in 20 of 20 frames; in the image: centre 20.",
            "left: bottle in 20 of 20 frames; in the image: right 20.",  # The image's side, as the last line says.
            "right: bottle in 20 of 20 frames; in the image: left 20.",
            "up: bottle in 20 of 20 frames; in the image: up 20.",
            "down: bottle in 20 of 20 frames; in the image: down 20.",
            "Image sides are the camera image's as exported, which may not be yours.",
            "Detection time, 95th percentile: 42 ms."])

    def test_runs_that_did_not_start_say_so_and_claim_nothing(self):
        config = command.Config("removal", "/nonexistent", Path("/nonexistent/r.json"), command.run_limits("removal"))
        for reason, line in ((guidance.DECLINED, "Not started. Nothing was launched."),
                             (guidance.STARTING_STATE_NOT_STABLE, "Not started, because the view was not colour and "
                              "stable. Nothing was launched. Tell the Frame manager.")):
            report = command.unstarted_report(config, reason)
            self.assertEqual(guidance.result_lines(report), [line])
            self.assertEqual((report["status"], report["exit_code"], report["confirmation"], report["cleanup_ok"]),
                             ("error", 1, None, True))
            self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
            self.assertNotIn("capture", report)
            self.assertFalse(guidance.watch_needed(report))

    def test_a_run_that_read_no_frame_says_what_happened_and_is_not_watched_unless_the_reader_was_active(self):
        failed = finished_report(status="helper_ended_early", reason="sdk_error", frames=(),
                                 helper_end={"monotonic_ns": T0 + 200 * MS, "reason": "sdk_error"})
        self.assertEqual(guidance.result_lines(failed), ["The camera reader ended early (sdk_error).",
                                                         "No camera frame was read.", "Tell the Frame manager."])
        self.assertFalse(guidance.watch_needed(failed))
        unavailable = finished_report(status="error", reason="detector_unavailable", frames=())
        self.assertEqual(guidance.result_lines(unavailable), ["The run did not work: error (detector_unavailable).",
                                                              "No camera frame was read.", "Tell the Frame manager."])
        stopped = finished_report(status="stopped", reason="stopped_before_launch", frames=())
        self.assertEqual(guidance.result_lines(stopped), ["The run was stopped.", "No camera frame was read."])
        self.assertFalse(guidance.watch_needed(stopped))
        # No frame, but the helper ended on a presence stop: its reader was active, so the run is watched.
        standby = finished_report(status="helper_ended_early", reason="standby", frames=())
        self.assertTrue(guidance.watch_needed(standby))
        self.assertEqual(guidance.result_lines(standby)[1], "No camera frame was read.")

    def test_without_a_clean_shutdown_nothing_frame_derived_is_put_into_words(self):
        recorded = [window_mark(1, "near", T0), window_mark(2, "remove", T0 + 1000 * MS)]
        unclean = finished_report(recorded, status="helper_ended_early", reason="headset_removed", cleanup_ok=False)
        self.assertEqual(guidance.result_lines(unclean), ["The run ended without a clean shutdown.", "Tell the Frame manager."])
        self.assertTrue(guidance.watch_needed(unclean))
        for value in (None, "true", 1, 0):
            self.assertEqual(len(guidance.result_lines(dict(unclean, cleanup_ok=value))), 2, value)
        config = command.Config("removal", "/nonexistent", Path("/nonexistent/r.json"), command.run_limits("removal"))
        for reason in ("guide_worker_ended_without_report", "guide_worker_unresponsive"):
            died = command.interrupted_report(config, reason)
            self.assertEqual(guidance.result_lines(died), ["The run was interrupted.", "Tell the Frame manager."])
            self.assertEqual((died["confirmation"], died["cleanup_ok"], guidance.watch_needed(died)),
                             ("operator_window", False, True))

    def test_steps_not_reached_are_listed(self):
        report = finished_report()
        report["guide"] = {"steps_not_reached": [{"step": 2, "label": "far"}, {"step": 3, "label": "remove"}]}
        self.assertEqual(guidance.result_lines(report)[-1], "Steps not reached: far, remove.")
        self.assertEqual(guidance.status_line({"status": "stopped", "reason": "stop_requested"}), "The run was stopped.")
        self.assertEqual(guidance.status_line({"status": "helper_ended_early", "reason": "transport_lost"}),
                         "The camera reader ended early (transport_lost).")

    def test_the_guide_block_holds_steps_marks_and_closed_answers_and_no_free_text(self):
        run = guide("removal", started_ns=T0 - 900 * MS)
        run.ready(T0)
        run.advance(T0)
        recorded = [window_mark(1, "near", T0)]
        observations = {"starting_state": "colour_stable", "passthrough_after": "unchanged", "headset": "not_asked",
                        "watch_seconds_elapsed": 30.2, "watch_completed": True}
        block = guidance.guide_block(run, script="removal", started=True, ended_by="stop_button", observations=observations,
                                     limits_ms={"late_frame_limit_ms": 2000, "removal_end_limit_ms": 3000}, watch="held",
                                     clock=marks.Clock(T0, 77), recorded_marks=recorded, started_ns=T0 - 900 * MS)
        self.assertEqual((block["schema_version"], block["script"], block["confirmation"], block["started_by_start_button"],
                          block["ended_by"], block["watch"]), (3, "removal", "operator_window", True, "stop_button", "held"))
        self.assertEqual(block["steps"], [
            {"step": 1, "label": "near", "advance": "clock", "record_ms": 4000, "reached": True,
             "monotonic_ns": T0, "mark_index": 1, "utc_ns": 77},
            {"step": 2, "label": "far", "advance": "clock", "record_ms": 6000, "reached": False, "monotonic_ns": None,
             "mark_index": None, "utc_ns": None},
            {"step": 3, "label": "remove", "advance": "click", "record_ms": 0, "reached": False, "monotonic_ns": None,
             "mark_index": None, "utc_ns": None}])
        self.assertEqual(block["steps_not_reached"], [{"step": 2, "label": "far"}, {"step": 3, "label": "remove"}])
        self.assertEqual((block["observations"], block["start_click_monotonic_ns"], block["reader_ready_monotonic_ns"],
                          block["watch_seconds_required"]), (observations, T0 - 900 * MS, T0, 30))
        self.assertEqual(block["removal_limits_ms"], {"late_frame_limit_ms": 2000, "removal_end_limit_ms": 3000})
        unstarted = guidance.guide_block(None, script="positions", started=False, ended_by="cancel_button",
                                         observations=dict(observations, passthrough_after=None), limits_ms={},
                                         watch="run_not_started")
        self.assertEqual((unstarted["confirmation"], unstarted["started_by_start_button"],
                          [entry["label"] for entry in unstarted["steps_not_reached"]]),
                         (None, False, [step.label for step in guidance.POSITION_STEPS]))
        closed = {*marks.VOCABULARY, *guidance.SCRIPTS, *guidance.STARTING_STATES, *guidance.PASSTHROUGH_ANSWERS,
                  *guidance.HEADSET_ANSWERS, *guidance.ENDED_BY, *guidance.WATCH_STATES, "click", "clock",
                  "operator_window", "script", "start_click_time", "reader_ready_time",
                  "scripted_instructions_progress_and_time_only"}
        for block in (block, unstarted):
            for path, leaf in l15_leaves(block, "guide"):
                if isinstance(leaf, str):
                    self.assertIn(leaf, closed, path)
            for name in l15_names(block):
                self.assertFalse([part for part in FORBIDDEN_NAME_PARTS if part in name], name)


class ScanResultTests(unittest.TestCase):
    """The scan's result in plain words: counts and closed names from the finished report, nothing else."""

    def scanned(self, hits, frames=48, failures=None, run_detector="mobilenet-ssd"):
        from sightcircuit.camera import region
        schedule = region.scan_schedule()
        records = []
        for number in range(frames):
            entry = schedule[number % 24]

            def target(name):
                found = hits[name](entry, number)
                return marks.target_of((Detection("bottle", 0.9, (700, 600, 800, 800)),) if found else (), "bottle", 0.5,
                                       960, 960)
            stopped = {name: failure["frame_index"] for name, failure in (failures or {}).items()}
            record = {"view": {"index": number % 24}, "inference_ms": 20.0,
                      "scan": {name: {"target": target(name), "detection_count": 0, "inference_ms": 20.0}
                               for name in hits if name != run_detector and number < stopped.get(name, frames)}}
            if number >= stopped.get(run_detector, frames):
                record.update(run_detector_failed=True, target=marks.target_of((), "bottle", 0.5, 960, 960))
            else:
                record["target"] = target(run_detector)
            records.append(record)
        report = finished_report([window_mark(1, "near", T0)])
        report["summary"] = marks.summarize(list(FRAMES), [window_mark(1, "near", T0)], origin_ns=T0,
                                            run_end_ns=T0 + 30 * SECOND, with_target=False)
        report["summary"]["coverage_omitted"] = region.COVERAGE_OMITTED
        report.update(scan=region.scan_summary(records, schedule, tuple(hits), run_detector, failures=failures),
                      frames_processed=frames, frames_received=frames)
        return report

    def test_the_words_rank_by_share_and_give_the_runner_up(self):
        # Found at 90 degrees in two windows and, less often, at 270: the opposite turn, as a bottle is.
        report = self.scanned({
            "mobilenet-ssd": lambda entry, number: False,
            "yolox-nano": lambda entry, number: (entry.rotation == 90 and entry.name in ("lower_right", "whole"))
            or (entry.rotation == 270 and entry.name == "lower_right" and number < 24)})
        lines = guidance.result_lines(report)
        self.assertIn("mobilenet-ssd: bottle in 0 of 48 frames.", lines)
        self.assertIn("yolox-nano: bottle in 5 of 48 frames.", lines)
        self.assertIn("yolox-nano, by turn of the image: 90 degrees 4 of 12, 270 degrees 1 of 12.", lines)
        self.assertIn("yolox-nano, by region: lower_right 3 of 8, whole 2 of 8.", lines)
        self.assertIn("A turn and its opposite look alike to the detectors: this gives the axis, not which way up.", lines)
        self.assertIn("Regions and turns are the camera image's as exported, which may not be yours.", lines)
        self.assertEqual(sum(line.startswith("mobilenet-ssd") for line in lines), 1)  # Nothing found: one line only.
        # A scan's frames are 24 views: no line reads as G7's coverage or as an in-view interval's count.
        self.assertFalse([line for line in lines if "while it was held in view" in line or line.startswith("near:")])
        for line in lines:
            self.assertLess(len(line), 160)

    def test_a_tie_is_called_a_tie_and_unequal_groups_are_compared_by_share(self):
        tie = self.scanned({"yolox-nano": lambda entry, number: entry.rotation in (0, 180)}, run_detector="yolox-nano")
        lines = guidance.result_lines(tie)
        self.assertIn("yolox-nano, by turn of the image: 0 degrees 12 of 12, 180 degrees 12 of 12 (a tie).", lines)
        # 150 frames: 38, 38, 37 and 37 frames per rotation. 180 degrees holds the target in all 37 of its frames and
        # 0 degrees in 37 of its 38: the same count, and 180 leads on share.
        skipped = {"n": None}

        def found(entry, number):
            if entry.rotation == 0 and skipped["n"] in (None, number):
                skipped["n"] = number
                return False
            return entry.rotation in (0, 180)
        uneven = self.scanned({"yolox-nano": found}, frames=150, run_detector="yolox-nano")
        rows = {row["key"]: (row["frames"], row["target_frames"]) for row in uneven["scan"]["detectors"]["yolox-nano"]["by_rotation"]}
        self.assertEqual((rows[0], rows[180]), ((38, 37), (37, 37)))
        self.assertIn("yolox-nano, by turn of the image: 180 degrees 37 of 37, 0 degrees 37 of 38.",
                      guidance.result_lines(uneven))

    def test_a_detector_that_failed_is_named_plainly(self):
        report = self.scanned({"mobilenet-ssd": lambda entry, number: entry.rotation == 90,
                               "yolox-nano": lambda entry, number: True},
                              failures={"yolox-nano": {"reason": "detector_raised", "frame_index": 7}})
        lines = guidance.result_lines(report)
        self.assertIn("yolox-nano failed after 7 frames (detector_raised). Tell the Frame manager.", lines)
        self.assertIn("yolox-nano: bottle in 7 of 7 frames.", lines)
        self.assertIn("mobilenet-ssd: bottle in 12 of 48 frames.", lines)

    def test_a_scan_without_a_clean_shutdown_puts_nothing_frame_derived_into_words(self):
        report = self.scanned({"yolox-nano": lambda entry, number: True}, run_detector="yolox-nano")
        report["cleanup_ok"] = False
        self.assertEqual(guidance.result_lines(report), [guidance.LINE_UNCLEAN, guidance.TELL_THE_MANAGER])
        plain = finished_report([window_mark(1, "near", T0)])  # A run that was no scan says nothing about one.
        self.assertFalse([line for line in guidance.result_lines(plain) if "turn" in line or "Regions" in line])


class StandInHelper(unittest.TestCase):
    """A base: the executable stand-in helper outside the checkout, and signal handlers restored."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = fake_camera_export.install(cls.directory.name)

    def setUp(self):
        for number in RECORD_AND_POLL.numbers:
            self.addCleanup(signal.signal, number, signal.getsignal(number))
        self.reports = tempfile.TemporaryDirectory()
        self.addCleanup(self.reports.cleanup)

    def config(self, script="positions", name="report", scenario=None, seconds=4, **options):
        options.setdefault("scale", 0.05)
        options.setdefault("watch_seconds", 0.4)
        for name_, value in (("guard_seconds", 0.2), ("start_dwell_seconds", 0.3), ("close_dwell_seconds", 0.3)):
            options.setdefault(name_, value)
        return command.Config(script=script, helper=str(self.binary), report_path=Path(self.reports.name) / f"{name}.json",
                              limits=command.run_limits(script, seconds, command.MAX_FRAMES, 10), validation=True,
                              helper_args=() if scenario is None else ("--fake-scenario", scenario), **options)


class Events:
    """Collects a worker's events from its reader thread."""

    def __init__(self):
        self.items, self.changed = [], threading.Condition()

    def put(self, event):
        with self.changed:
            self.items.append(event)
            self.changed.notify_all()

    def wait(self, kind, seconds=30.0):
        deadline = time.monotonic() + seconds
        with self.changed:
            while not any(item[0] == kind for item in self.items):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"no {kind} event; got {[item[0] for item in self.items]}")
                self.changed.wait(remaining)
            return next(item[1] for item in self.items if item[0] == kind)


def handover(record):
    """A descriptor as a window hands one to its worker: a pipe carrying ``record`` and then its end."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (record if isinstance(record, bytes) else json.dumps(record).encode()) + b"\n")
    os.close(write_fd)
    return read_fd


TOKEN = "0123456789abcdef" * 2


class HandshakeTests(unittest.TestCase):
    """``--worker`` runs only for the window that launched it: a token on an inherited pipe, from its own parent."""

    def test_the_windows_handover_is_accepted_once(self):
        descriptor = handover({"token": TOKEN, "window_pid": os.getppid()})
        self.assertEqual(command.worker_handshake(descriptor), TOKEN)
        with self.assertRaises(OSError):  # One shot: the descriptor is closed.
            os.fstat(descriptor)

    def test_anything_else_is_refused(self):
        regular = tempfile.TemporaryFile()
        self.addCleanup(regular.close)
        regular.write(json.dumps({"token": TOKEN, "window_pid": os.getppid()}).encode())
        regular.seek(0)
        open_pipe = os.pipe()  # A pipe whose writer never finishes.
        self.addCleanup(os.close, open_pipe[1])
        with mock.patch.object(command, "HANDSHAKE_SECONDS", 0.2):
            for name, descriptor in (
                    ("none", None), ("stdin", 0), ("a string", "7"), ("closed", 987654), ("a file", regular.fileno()),
                    ("another parent", handover({"token": TOKEN, "window_pid": os.getpid()})),
                    ("no pid", handover({"token": TOKEN})), ("short token", handover({"token": "abc", "window_pid": os.getppid()})),
                    ("not hex", handover({"token": "z" * 32, "window_pid": os.getppid()})),
                    ("not json", handover(b"hello")), ("a list", handover([TOKEN, os.getppid()])),
                    ("nothing written", open_pipe[0])):
                with self.subTest(name=name):
                    self.assertIsNone(command.worker_handshake(descriptor))

    def test_a_worker_run_by_hand_reads_nothing_launches_nothing_and_says_why(self):
        class Unread(io.StringIO):
            def readline(self, *arguments):
                raise AssertionError("the spec was read")
            __iter__ = __next__ = readline
        with mock.patch.object(camera_recognize, "recognize", side_effect=AssertionError("a run was started")):
            for descriptor in (None, 0):
                out = io.StringIO()
                self.assertEqual(command.worker_main(descriptor, Unread(), out), 1)
                (line,) = out.getvalue().splitlines()
                record = json.loads(line)
                self.assertEqual((record["event"], record["token"], record["report"]["status"], record["report"]["reason"],
                                  record["report"]["confirmation"]),
                                 ("report", None, "error", "guide_worker_not_launched_by_window", None))
                self.assertNotIn("capture", record["report"])

    def test_the_command_line_by_hand_is_refused_as_a_shell_would_run_it(self):
        spec = command.Config("removal", "/nonexistent/helper", Path("/nonexistent/r.json"),
                              command.run_limits("removal")).spec()
        for arguments in ([], ["--worker-fd", "0"], ["--worker-fd", "9"]):
            done = subprocess.run([sys.executable, str(ROOT / "scripts/camera_guide.py"), "--worker", *arguments],
                                  input=json.dumps(spec) + "\n", capture_output=True, text=True, timeout=60)
            with self.subTest(arguments=arguments):
                report = json.loads(done.stdout)["report"]
                self.assertEqual((done.returncode, report["reason"], report["confirmation"]),
                                 (1, "guide_worker_not_launched_by_window", None))


class GatedLines:
    """A worker's stdin: the spec at once, then ``later`` lines only once ``gate`` is set, then the end."""

    def __init__(self, spec, later, gate):
        self.spec, self.later, self.gate = spec, list(later), gate

    def readline(self):
        return json.dumps(self.spec) + "\n"

    def __iter__(self):
        self.gate.wait(10)
        yield from (line + "\n" for line in self.later)


class WorkerMainTests(unittest.TestCase):
    """The worker's own logic with ``recognize`` replaced: what it passes on, and a stop before the launch."""

    def run_worker(self, recognize, lines=(), spec=None, gate=None):
        config = command.Config("removal", "/nonexistent/helper", Path("/nonexistent/r.json"), command.run_limits("removal"))
        spec = dict(config.spec(), **(spec or {}))
        out = io.StringIO()
        if gate is None:
            gate = threading.Event()
            gate.set()
        with mock.patch.object(camera_recognize, "recognize", recognize):
            code = command.worker_main(handover({"token": TOKEN, "window_pid": os.getppid()}),
                                       GatedLines(spec, lines, gate), out)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_the_device_run_uses_the_named_detector_never_the_scripted_one(self):
        seen = {}

        def recognize(helper, limits, cameras, **options):
            seen.update(options, helper=helper, limits=limits, cameras=cameras)
            options["on_ready"]()
            return camera_recognize.new_report("recognize")
        code, records = self.run_worker(recognize, spec={"detector": "mobilenet-ssd"})
        self.assertEqual((seen["detector_factory"], seen["detector"], seen["confirmed"], seen["confirmation"]),
                         (None, "mobilenet-ssd", True, "operator_window"))
        self.assertEqual((seen["cameras"], seen["target_label"], seen["late_frame_limit_ms"], seen["removal_end_limit_ms"]),
                         ((0,), "bottle", 2000, 3000))
        self.assertNotIn("cues", seen)
        self.assertNotIn("key_fd", seen)
        self.assertEqual((code, [(record["event"], record["token"]) for record in records]),
                         (0, [("ready", TOKEN), ("report", TOKEN)]))
        self.assertEqual(sorted(records[0]), ["event", "token"])  # Ready carries nothing else.

    def test_a_stop_before_the_helper_is_launched_launches_nothing_and_is_a_clean_stop(self):
        def recognize(helper, limits, cameras, **options):
            time.sleep(0.3)  # The detector loads; the stop line has been read by now.
            options["on_session"](object())  # ``recognize`` calls this just before it launches the helper.
            raise AssertionError("the helper was launched after a stop")
        code, records = self.run_worker(recognize, lines=[json.dumps({"stop": True})])
        (record,) = records
        report = record["report"]
        self.assertEqual((code, report["status"], report["reason"], report["cleanup_ok"], report["confirmation"]),
                         (0, "stopped", "stopped_before_launch", True, "operator_window"))
        self.assertNotIn("capture", report)

    def test_a_stop_during_the_launch_reaches_the_helper_only_once_it_is_ready_or_the_grace_is_over(self):
        seen, gate = {}, threading.Event()

        def recognize(helper, limits, cameras, **options):
            options["on_session"](mock.Mock())  # The launch has begun.
            gate.set()  # Now the stop line arrives.
            stop = options["stop"]
            seen["set_before_ready"] = stop.wait(0.4)  # The helper is not ready yet: it is not signalled.
            options["on_ready"]()
            seen["set_after_ready"] = stop.wait(5)
            return camera_recognize.new_report("recognize")
        with mock.patch.object(command, "EARLY_STOP_GRACE_SECONDS", 5.0):
            started = time.monotonic()
            self.run_worker(recognize, lines=[json.dumps({"stop": True})], gate=gate)
        self.assertEqual(seen, {"set_before_ready": False, "set_after_ready": True})
        self.assertLess(time.monotonic() - started, 4)
        gate = threading.Event()

        def never_ready(helper, limits, cameras, **options):  # Still stopped, when the grace is over.
            options["on_session"](mock.Mock())
            gate.set()
            seen["set_without_ready"] = options["stop"].wait(5)
            return camera_recognize.new_report("recognize")
        with mock.patch.object(command, "EARLY_STOP_GRACE_SECONDS", 0.3):
            self.run_worker(never_ready, lines=[json.dumps({"stop": True})], gate=gate)
        self.assertTrue(seen["set_without_ready"])

    def test_the_worker_passes_the_region_or_the_scan_and_no_stand_in_on_a_device_run(self):
        from sightcircuit.camera import region
        seen = {}

        def recognize(helper, limits, cameras, **options):
            seen.update(options)
            return camera_recognize.new_report("recognize")
        self.run_worker(recognize)
        self.assertEqual((seen["region"], seen["scan_detectors"], seen["scan_detector_factory"]), (None, None, None))
        self.run_worker(recognize, spec={"region": [0.25, 0.25, 0.75, 0.75], "rotation": 270})
        self.assertEqual((seen["region"], seen["scan_detectors"]), (region.Region(0.25, 0.25, 0.75, 0.75, 270), None))
        self.run_worker(recognize, spec={"rotation": 180})
        self.assertEqual(seen["region"], region.Region(rotation=180))
        self.run_worker(recognize, spec={"scan_detectors": ["mobilenet-ssd", "yolox-nano"], "detector": "yolox-nano"})
        self.assertEqual((seen["region"], seen["scan_detectors"], seen["scan_detector_factory"], seen["detector_factory"],
                          seen["detector"]), (None, ("mobilenet-ssd", "yolox-nano"), None, None, "yolox-nano"))

    def test_the_worker_refuses_a_run_beyond_the_commands_bounds(self):
        for change in ({"limits": {"fps": 5, "max_seconds": 31, "max_frames": 150}},
                       {"region": [0.5, 0.5, 0.4, 0.9]}, {"region": [0, 0, 1]}, {"region": "0,0,1,1"}, {"rotation": 45},
                       {"region": [0, 0, 1, 1], "rotation": "90"},
                       {"scan_detectors": ["yolox-nano"]}, {"scan_detectors": "mobilenet-ssd"},
                       {"scan_detectors": ["mobilenet-ssd"], "region": [0.25, 0.25, 0.75, 0.75]},
                       {"scan_detectors": ["mobilenet-ssd"], "rotation": 90},
                       {"limits": {"fps": 5, "max_seconds": 30, "max_frames": 301}}, {"camera": 2}, {"limits": None}):
            with self.subTest(change=change):
                code, records = self.run_worker(mock.Mock(side_effect=AssertionError("a run was started")), spec=change)
                (record,) = records
                self.assertEqual((code, record["report"]["status"], record["report"]["reason"], record["report"]["confirmation"]),
                                 (1, "error", "guide_worker_arguments_invalid", None))


@unittest.skipUnless(sys.platform.startswith("linux") and NUMPY_CV2, "Needs Linux, NumPy and OpenCV")
class WorkerTests(StandInHelper):
    """The run's process: ``recognize()`` launched as ``camera_recognize`` launches it, marks by line, two events out."""

    def worker(self, config, **options):
        events = Events()
        worker = command.Worker(config.spec(), events.put, **options)
        worker.start()
        self.addCleanup(worker.close)
        return worker, events

    def test_a_run_reports_ready_then_its_report_and_records_window_marks_at_their_times(self):
        worker, events = self.worker(self.config(seconds=2))
        ready_ns = events.wait("ready")
        self.assertIsInstance(ready_ns, int)  # When the line was read here: the worker attached no value.
        clicks = [(label, time.monotonic_ns()) for label in ("centre", "left")]
        for label, when in clicks:
            worker.mark(label, when)
        worker.mark("bottle", time.monotonic_ns())  # Not in the vocabulary: refused, counted.
        report = events.wait("report")
        self.assertEqual([item[0] for item in events.items], ["ready", "report"])
        self.assertEqual((report["status"], report["exit_code"], report["confirmation"], report["report_kind"]),
                         ("recognized", 0, "operator_window", "camera_recognition"))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        helper = report["capture"]["helper"]
        self.assertEqual((helper["parent_confirmed"], helper["arguments"][:9]),
                         (True, ["--export-frames", "--fps", "10", "--max-seconds", "2", "--max-frames", "300",
                                 "--cameras", "0"]))
        self.assertIn("--parent-confirmed", helper["arguments"])
        self.assertEqual(report["capture"]["supervision"]["worker_tree_reaped"], True)
        recorded = report["marks"]["recorded"]
        self.assertEqual([(entry["source"], entry["label"], entry["monotonic_ns"]) for entry in recorded],
                         [("window", label, when) for label, when in clicks])
        self.assertEqual((report["marks"]["window_marks"], report["window_marks_refused"]), (2, 1))
        self.assertEqual((report["marks"]["key_marks_enabled"], report["marks"]["cues_requested"]), (False, []))
        self.assertEqual([interval["boundary"] for interval in report["summary"]["intervals"]], ["actual", "actual"])
        self.assertEqual((report["target_label"], report["detector_requested"]), ("bottle", None))
        self.assertGreater(report["target"]["frames_with_target"], 0)  # The scripted detector follows the marks.
        self.assertEqual((report["images_saved"], report["pixels_in_report"], report["display_started"]), (False,) * 3)
        self.assertEqual(worker.unexpected_lines, 0)

    def test_the_positions_and_removal_scripts_keep_their_arithmetic_over_the_whole_frame_with_a_region(self):
        # The scripted detector puts the bottle where the latest mark says, as a fraction of the image it is given,
        # which is now the lower-right 0.6 of the frame turned by 90 degrees. "left" is (0.2, 0.5) of that image:
        # unturned, (0.5, 0.8) of the crop, so (0.7, 0.88) of the frame: column right, row down.
        config = self.config(seconds=3, region=(0.4, 0.4, 1.0, 1.0), rotation=90)
        self.assertEqual((config.spec()["region"], config.spec()["rotation"], config.spec()["scan_detectors"]),
                         ([0.4, 0.4, 1.0, 1.0], 90, None))
        worker, events = self.worker(config)
        events.wait("ready")
        worker.mark("left", time.monotonic_ns())
        time.sleep(1.0)
        worker.mark("out_of_view", time.monotonic_ns())
        report = events.wait("report")
        self.assertEqual((report["status"], report["confirmation"]), ("recognized", "operator_window"), report.get("error"))
        self.assertEqual((report["view"]["mode"], report["view"]["region"], report["view"]["rotation"]),
                         ("region", [0.4, 0.4, 1.0, 1.0], 90))
        left = report["summary"]["intervals"][0]
        self.assertGreaterEqual(left["target_frames"], 5)
        self.assertEqual((left["label"], left["cells"]["down_right"], left["columns"]["right"], left["rows"]["down"]),
                         ("left", left["target_frames"], left["target_frames"], left["target_frames"]))
        self.assertEqual(report["summary"]["intervals"][1]["target_frames"], 0)
        (span,) = report["summary"]["coverage"]["spans"]
        self.assertEqual((span["labels"], span["ended_by"]), (["left"], "out_of_view"))
        self.assertGreaterEqual(span["target_frames"], 5)
        hit = next(record for record in report["detections"] if record["target"]["present"])
        self.assertEqual(hit["box_coordinate_space"], "whole_frame_processing_pixels")
        self.assertEqual(hit["box_space_size"], hit["native_size"])  # The stand-in's small frames are not resized.
        self.assertAlmostEqual(hit["target"]["centre_fraction"][0], 0.7, delta=0.03)
        self.assertAlmostEqual(hit["target"]["centre_fraction"][1], 0.88, delta=0.03)
        # The removal script with a region: the presence end is measured as before.
        worker, events = self.worker(self.config("removal", name="removal-region", scenario="headset_removed", seconds=20,
                                                 region=(0.25, 0.25, 0.75, 0.75)))
        report = events.wait("report")
        self.assertEqual((report["status"], report["reason"], report["helper_end"]["reason"], report["view"]["region"]),
                         ("helper_ended_early", "headset_removed", "headset_removed", [0.25, 0.25, 0.75, 0.75]))

    def test_the_scan_script_runs_the_schedule_with_every_scan_detector(self):
        config = self.config("scan", seconds=3, scan_detectors=("mobilenet-ssd", "yolox-nano"))
        self.assertEqual(config.spec()["scan_detectors"], ["mobilenet-ssd", "yolox-nano"])
        worker, events = self.worker(config)
        events.wait("ready")
        worker.mark("near", time.monotonic_ns())
        report = events.wait("report")
        self.assertEqual((report["status"], report["confirmation"]), ("recognized", "operator_window"), report.get("error"))
        self.assertEqual((report["view"]["mode"], report["view"]["scan_detectors"], report["view"]["schedule_length"]),
                         ("scan", ["mobilenet-ssd", "yolox-nano"], 24))
        frames = report["frames_processed"]
        self.assertGreaterEqual(frames, 20)
        self.assertEqual([record["view"]["index"] for record in report["detections"]], [n % 24 for n in range(frames)])
        scan = report["scan"]
        self.assertEqual([record["view"] for record in report["detections"]], [{"index": n % 24} for n in range(frames)])
        self.assertEqual((report["summary"]["coverage"], report["summary"]["coverage_omitted"], scan["detectors_failed"]),
                         (None, "scan_pools_views", []))
        self.assertEqual({name: block["frames"] for name, block in scan["detectors"].items()},
                         {"mobilenet-ssd": frames, "yolox-nano": frames})
        self.assertGreater(scan["detectors"]["yolox-nano"]["target_frames"], 0)
        self.assertEqual(report["marks"]["recorded"][0]["label"], "near")
        self.assertNotIn("detector", report)  # The checks' stand-ins are not described as a model.
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertEqual((report["images_saved"], report["pixels_in_report"], report["display_started"]), (False,) * 3)

    def test_a_stop_line_ends_the_helper_cleanly_and_the_report_still_comes(self):
        worker, events = self.worker(self.config(scenario="hang", seconds=20))
        events.wait("ready")
        worker.stop()
        report = events.wait("report")
        self.assertEqual((report["status"], report["reason"], report["cleanup_ok"], report["confirmation"]),
                         ("stopped", "stop_requested", True, "operator_window"))
        self.assertEqual(report["capture"]["stream"]["end"]["reason"], "stop_signal")

    def test_a_stop_sent_at_once_is_still_a_clean_stop(self):
        for attempt in range(3):
            worker, events = self.worker(self.config(name=f"early-{attempt}", scenario="hang", seconds=20))
            worker.stop()  # Before the detector is loaded, the helper launched or its ready seen.
            report = events.wait("report")
            with self.subTest(attempt=attempt):
                self.assertEqual((report["status"], report["cleanup_ok"], report["confirmation"]),
                                 ("stopped", True, "operator_window"))
                self.assertIn(report["reason"], ("stopped_before_launch", "stop_requested"))
                if "capture" in report:
                    self.assertFalse(report["capture"]["supervision"]["worker_forced_termination"])

    def test_a_window_that_is_gone_takes_its_run_with_it(self):
        worker, events = self.worker(self.config(scenario="hang", seconds=20))
        events.wait("ready")
        worker.process.stdin.close()  # What the worker sees when the window's process has died.
        report = events.wait("report")
        self.assertEqual((report["status"], report["cleanup_ok"]), ("stopped", True))
        self.assertEqual(worker.process.wait(10), 0)

    def test_a_presence_end_is_reported_as_the_helper_gave_it(self):
        worker, events = self.worker(self.config("removal", scenario="headset_removed", seconds=20))
        report = events.wait("report")
        self.assertEqual((report["status"], report["reason"], report["helper_end"]["reason"]),
                         ("helper_ended_early", "headset_removed", "headset_removed"))


class WorkerLineTests(unittest.TestCase):
    """What the window's process accepts from the worker: two events with its token, nothing attached to the first."""

    def run_lines(self, lines):
        events = Events()
        script = "import sys\nsys.stdin.readline()\n" + "".join(f"print({line!r}, flush=True)\n" for line in lines)
        worker = command.Worker({"any": "spec"}, events.put, command=[sys.executable, "-c", script, "--"], token=TOKEN)
        worker.start()
        self.addCleanup(worker.close)
        report = events.wait("report")
        worker.thread.join(10)
        return worker, events, report

    def test_only_ready_and_the_report_are_passed_on_and_ready_carries_nothing_from_the_worker(self):
        worker, events, report = self.run_lines([
            json.dumps({"event": "report", "report": {"status": "no token"}}),
            json.dumps({"event": "ready", "token": "f" * 32}),
            json.dumps({"event": "ready", "token": TOKEN, "labels": ["bottle"], "frames": 12, "monotonic_ns": 5}),
            json.dumps({"event": "frame", "token": TOKEN, "detections": [{"label": "bottle"}]}),
            json.dumps({"event": "progress", "token": TOKEN, "report": {"labels_seen": ["bottle"]}}),
            json.dumps({"labels_seen": ["chair"]}), "not json", json.dumps(["ready"]),
            json.dumps({"event": "report", "token": TOKEN, "report": {"status": "recognized"}}),
            json.dumps({"event": "ready", "token": TOKEN}),
            json.dumps({"event": "report", "token": TOKEN, "report": {"status": "second"}})])
        self.assertEqual([item[0] for item in events.items], ["ready", "report"])
        ready = events.items[0][1]
        self.assertTrue(type(ready) is int and abs(ready - time.monotonic_ns()) < 60 * SECOND)  # This process's clock.
        self.assertEqual(report, {"status": "recognized"})
        self.assertEqual(worker.unexpected_lines, 9)

    def test_a_worker_that_ends_without_a_report_is_reported_as_that(self):
        worker, events, report = self.run_lines([json.dumps({"event": "ready", "token": TOKEN}),
                                                 json.dumps({"event": "report", "token": TOKEN})])
        self.assertIsNone(report)
        self.assertEqual([item[0] for item in events.items], ["ready", "report"])

    def test_each_worker_gets_a_fresh_token(self):
        tokens = {command.Worker({}, print).token for _ in range(8)}
        self.assertEqual((len(tokens), {len(token) for token in tokens}), (8, {32}))


class StandInWorker:
    """The window's worker in the window tests: records what the window asks of it; never a process."""

    def __init__(self, spec, on_event):
        self.spec, self.on_event = spec, on_event
        self.started = self.stops = self.terminations = self.closed = 0
        self.marks = []
        self.unexpected_lines = 0

    def start(self):
        self.started += 1

    def mark(self, label, monotonic_ns):
        self.marks.append((label, monotonic_ns))

    def stop(self):
        self.stops += 1

    def terminate(self):
        self.terminations += 1

    def close(self):
        self.closed += 1


@unittest.skipUnless(PYSIDE, "Needs PySide6: bash scripts/setup_window.sh")
class WindowTests(unittest.TestCase):
    """The window with a stand-in worker and a clock the test owns: real clicks through ``QtTest``, offscreen.

    Clicks are sent to the top-level window's handle at a point, as a pointer
    sends them, so a click lands on whatever is there at that moment.
    """
    GUARD_MS = 1200
    BETWEEN_CLICKS_MS = (0, 100, 200, 300, 200, 100)  # A second click 0, 100, 300, 600, 800 and 900 ms after the first.

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        cls.QtCore, cls.QtWidgets, cls.QTest = QtCore, QtWidgets, QTest
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = T0
        self.workers = []

    def factory(self, spec, on_event):
        self.workers.append(StandInWorker(spec, on_event))
        return self.workers[-1]

    def window(self, script="removal", seconds=None, size=(1280, 800), **options):
        self.config = command.Config(script=script, helper="/nonexistent/helper",
                                     report_path=Path(self.directory.name) / "report.json",
                                     limits=command.run_limits(script, seconds), **options)
        window = command.create_window(self.config, worker_factory=self.factory, clock=lambda: self.now, size=size)
        window.timer.stop()  # The test owns the clock: it moves ``self.now`` and calls ``tick``.
        window.show()
        self.QTest.qWaitForWindowExposed(window)
        self.app.processEvents()
        self.addCleanup(self.discard, window)
        self.shown = window
        return window

    def discard(self, window):
        window.phase = command.PHASE_RESULT  # Let it close without composing another report.
        window.final = window.final or {}
        window.close()

    def buttons(self):
        """The buttons on the screen now: ``{text: button}`` of those visible, enabled or not."""
        return {candidate.text(): candidate for candidate in self.shown.findChildren(self.QtWidgets.QPushButton)
                if candidate.isVisible()}

    def offered(self):
        """The texts of the buttons a pointer can click now."""
        return sorted(text for text, candidate in self.buttons().items() if candidate.isEnabled())

    def point(self, text):
        """Where the button that says ``text`` is, in the window."""
        candidate = self.buttons()[text]
        return candidate.mapTo(self.shown, candidate.rect().center())

    def click_at(self, point):
        """A pointer click at ``point`` of the window, whatever is there."""
        self.app.processEvents()
        self.QTest.mouseClick(self.shown.windowHandle(), self.QtCore.Qt.MouseButton.LeftButton,
                              self.QtCore.Qt.KeyboardModifier.NoModifier, point)
        self.app.processEvents()

    def click(self, text):
        self.assertIn(text, self.offered(), f"no button to click that says {text!r}")
        self.click_at(self.point(text))

    def visible_texts(self):
        return [widget.text() for widget in self.shown.findChildren(self.QtWidgets.QLabel) if widget.isVisible() and widget.text()]

    def advance(self, ms, step=50):
        for _ in range(0, ms, step):
            self.now += step * MS
            self.shown.tick()
        self.app.processEvents()

    def press(self, text):
        """Wait out the screen's guard interval, then click."""
        self.advance(self.GUARD_MS)
        self.click(text)

    def to_notice(self, script="removal", **options):
        window = self.window(script, **options)
        self.press("Colour, stable")
        self.press("Next")
        return window

    def start(self, script="removal", **options):
        window = self.to_notice(script, **options)
        self.advance(5000)
        self.click("Start")
        return window, self.workers[0]

    def run_to(self, label, worker):
        """From Start: the reader ready, and the script on the clock until ``label`` is marked or its button is up."""
        worker.on_event(("ready", self.now))
        for _ in range(700):
            if any(mark == label for mark, _ in worker.marks) or self.shown.guide.waiting_since_ns is not None:
                return
            self.advance(50)
        self.fail(f"the script never reached {label}")

    def end_run(self, worker, report):
        worker.on_event(("report", report))
        self.advance(50)

    def saved(self):
        return json.loads(self.config.report_path.read_text())

    # -- before the run --

    def test_the_screens_before_start_ask_the_state_show_the_plan_and_then_the_contracts_notice(self):
        window = self.window()
        self.assertEqual((window.phase, self.offered(), sorted(self.buttons())),
                         ("starting_state", [], ["Colour, stable", "Something else"]))
        self.assertIn(command.START_QUESTION, self.visible_texts())
        self.press("Colour, stable")
        self.assertEqual((window.phase, sorted(self.buttons())), ("plan", ["Next"]))
        self.assertIn("\n\n".join(guidance.plan_lines("removal")), self.visible_texts())
        self.press("Next")
        self.assertEqual((window.phase, sorted(self.buttons()), self.offered()), ("notice", ["Cancel", "Start"], []))
        texts = self.visible_texts()
        self.assertIn(confirmation.export_notice_body(), texts)
        self.assertNotIn(contract.EXPORT_NOTICE, texts)
        self.assertFalse([text for text in texts if "Type yes" in text])
        self.assertIn("At most 30 seconds and 150 frames, 5 frames a second, the left camera.", texts)
        self.assertIn("Looking for: bottle. Guide: removal.", texts)
        self.assertIn(f"The report will be saved to: {self.config.report_path}", texts)
        self.assertIn(command.START_NOTE, texts)
        self.assertIn(guidance.start_reminder("removal"), texts)
        self.assertEqual((self.workers, window.observations["starting_state"]), ([], "colour_stable"))
        self.assertFalse(self.config.report_path.exists())

    def test_start_can_be_clicked_only_after_the_notice_has_been_up_for_five_seconds_with_a_visible_count(self):
        window = self.to_notice()
        self.assertEqual((window.dwell_label.text(), self.offered()), ("You can start in 5 s", []))
        start = self.point("Start")
        self.advance(1300)
        self.assertEqual((window.dwell_label.text(), self.offered()), ("You can start in 4 s", ["Cancel"]))
        for _ in range(7):  # 1.3 s to 4.8 s: Start is on the screen and dead.
            self.click_at(start)
            window.start_run()  # However it is asked.
            self.advance(500)
        self.assertEqual((window.phase, self.workers, window.dwell_label.text()), ("notice", [], "You can start in 1 s"))
        self.advance(250)
        self.assertEqual((window.dwell_label.text(), self.offered()), ("You can start now", ["Cancel", "Start"]))
        self.click_at(start)
        self.assertEqual((window.phase, len(self.workers), self.workers[0].started, window.started_ns), ("run", 1, 1, self.now))

    def test_a_double_click_on_colour_stable_does_not_start_the_run(self):
        # The reviewer's case: the first screen's button and Start once shared a rectangle.
        window = self.window()
        self.advance(self.GUARD_MS)
        point = self.point("Colour, stable")
        self.QTest.mouseDClick(window.windowHandle(), self.QtCore.Qt.MouseButton.LeftButton,
                               self.QtCore.Qt.KeyboardModifier.NoModifier, point)
        self.app.processEvents()
        self.assertEqual((window.phase, self.workers), ("plan", []))
        for pause in (600, 600, 600, 600, 5000, 600, 600):  # Clicks at that same point for ten more seconds.
            self.advance(pause)
            self.click_at(point)
        self.assertEqual(self.workers, [])
        self.assertIn(window.phase, ("plan", "notice"))
        self.assertIsNone(window.final)

    def assert_dead_after_the_transition(self, came_from):
        """Right after a screen appears: clicks where the last button was, and on every new button, do nothing."""
        window = self.shown
        phase, observations = window.phase, dict(window.observations)
        marks_before = None if not self.workers else list(self.workers[0].marks)
        points = [came_from, *[self.point(text) for text in self.buttons()]]
        for pause in self.BETWEEN_CLICKS_MS:
            self.advance(pause)
            for point in points:
                self.click_at(point)
            self.assertEqual((window.phase, window.observations), (phase, observations), (phase, pause))
        if marks_before is not None:
            self.assertEqual(self.workers[0].marks, marks_before)
        # No button of this screen sits where the last screen's button was clicked.
        for text, candidate in self.buttons().items():
            area = self.QtCore.QRect(candidate.mapTo(window, self.QtCore.QPoint(0, 0)), candidate.size())
            self.assertFalse(area.contains(came_from), (phase, text))

    def test_on_every_transition_a_second_click_lands_on_nothing(self):
        window = self.window()
        self.advance(self.GUARD_MS)
        steps = []

        def go(text, expect):
            point = self.point(text)
            self.click_at(point)
            self.assertEqual(window.phase, expect, text)
            steps.append(expect)
            self.assert_dead_after_the_transition(point)
            self.advance(self.GUARD_MS)
        go("Colour, stable", "plan")
        go("Next", "notice")
        self.advance(5000)
        go("Start", "run")
        worker = self.workers[0]
        self.run_to("remove", worker)
        self.advance(self.GUARD_MS)
        point = self.point("I am taking it off now")
        self.click_at(point)
        self.assertEqual([label for label, _ in worker.marks], ["near", "far", "remove"])
        for pause in self.BETWEEN_CLICKS_MS:  # Further clicks there: one mark was made, and Stop was not pressed.
            self.advance(pause)
            self.click_at(point)
        self.assertEqual((len(worker.marks), worker.stops, window.phase), (3, 0, "run"))
        # The run ends by itself under a click in flight: the answer buttons are not where the pointer is.
        self.end_run(worker, finished_report([window_mark(3, "remove", worker.marks[2][1])],
                                             status="helper_ended_early", reason="headset_removed"))
        self.assertEqual(window.phase, "headset_back")
        self.assert_dead_after_the_transition(point)
        self.advance(self.GUARD_MS)
        go("No, I kept it on", "watch")
        go("Something changed", "confirm_changed")
        go("Go back", "watch")
        go("Something changed", "confirm_changed")
        go("Confirm", "result")
        self.assertEqual(self.offered(), [])  # Close is still dead: the result has to be seen first.
        self.assertTrue(window.isVisible())
        self.assertEqual(self.saved()["guide"]["observations"]["passthrough_after"], "changed")

    def test_after_stop_a_second_click_in_the_same_place_answers_nothing(self):
        window, worker = self.start()
        self.run_to("far", worker)
        self.advance(self.GUARD_MS)
        point = self.point("Stop")
        self.click_at(point)
        self.assertEqual((worker.stops, self.offered()), (1, []))
        self.end_run(worker, finished_report(status="stopped", reason="stop_requested"))
        self.assertEqual(window.phase, "watch")  # The remove click was never made: the headset is not asked about.
        self.assert_dead_after_the_transition(point)
        self.assertEqual((window.observations["passthrough_after"], window.observations["headset"]), (None, "not_asked"))

    def test_cancel_launches_nothing_and_leaves_a_report_that_says_so(self):
        window = self.to_notice()
        self.press("Cancel")
        self.assertEqual((window.phase, self.workers, sorted(self.buttons())), ("result", [], ["Close"]))
        report = self.saved()
        self.assertEqual((report["status"], report["reason"], report["confirmation"], report["exit_code"]),
                         ("error", "confirmation_declined", None, 1))
        self.assertEqual((report["guide"]["ended_by"], report["guide"]["confirmation"],
                          report["guide"]["started_by_start_button"], report["guide"]["watch"]),
                         ("cancel_button", None, False, "run_not_started"))
        self.assertEqual(report["camera_data"]["report_path"], str(self.config.report_path))
        self.assertNotIn("capture", report)
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertIn("Not started. Nothing was launched.", self.visible_texts())
        self.assertIn(f"Report saved to: {self.config.report_path}", self.visible_texts())

    def test_close_can_be_clicked_only_after_the_result_has_been_shown_for_three_seconds(self):
        window = self.to_notice()
        self.press("Cancel")
        close = self.point("Close")
        self.assertEqual((self.offered(), window.close_label.text()), ([], "You can close in 3 s"))
        for _ in range(5):
            self.advance(550)
            self.click_at(close)
        self.assertTrue(window.isVisible())
        self.advance(300)
        self.assertEqual((self.offered(), window.close_label.text()), (["Close"], ""))
        self.click_at(close)
        self.assertFalse(window.isVisible())

    def test_something_else_as_the_starting_state_ends_there_with_nothing_launched(self):
        window = self.window("positions")
        self.press("Something else")
        self.assertEqual((window.phase, self.workers), ("result", []))
        report = self.saved()
        self.assertEqual((report["status"], report["reason"], report["confirmation"]),
                         ("error", "starting_state_not_stable", None))
        self.assertEqual(report["guide"]["observations"]["starting_state"], "something_else")
        self.assertEqual([entry["label"] for entry in report["guide"]["steps_not_reached"]],
                         [step.label for step in guidance.POSITION_STEPS])

    def test_closing_the_window_before_start_is_a_declined_start(self):
        for clicks in ((), ("Colour, stable",), ("Colour, stable", "Next")):
            with self.subTest(clicks=clicks):
                window = self.window()
                for text in clicks:
                    self.press(text)
                window.close()
                self.assertFalse(window.isVisible())
                report = self.saved()
                self.assertEqual((report["reason"], report["guide"]["ended_by"], self.workers),
                                 ("confirmation_declined", "window_closed", []))
                self.config.report_path.unlink()

    def test_no_key_starts_a_run_and_start_is_the_only_way_to_a_worker(self):
        window = self.to_notice()
        self.advance(6000)
        Key = self.QtCore.Qt.Key
        for key in (Key.Key_Return, Key.Key_Enter, Key.Key_Space, Key.Key_Y, Key.Key_Tab):
            self.QTest.keyClick(window, key)
            self.QTest.keyClick(window.start_button, key)
        self.advance(500)
        self.assertEqual((window.phase, self.workers), ("notice", []))
        self.click("Start")
        self.assertEqual((window.phase, len(self.workers), self.workers[0].started, window.workers_started),
                         ("run", 1, 1, 1))
        window.start_run()  # A second start in one launch is nothing: one launch, one run.
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.workers[0].spec, self.config.spec())
        self.assertEqual((self.workers[0].spec["validation"], self.workers[0].spec["limits"], self.workers[0].spec["camera"]),
                         (False, {"fps": 5, "max_seconds": 30, "max_frames": 150}, 0))

    def test_the_type_is_sized_from_the_window_and_every_button_keeps_its_text_whole(self):
        self.assertEqual([command.unit_for(*size) for size in ((1920, 1080), (1280, 800), (1024, 768), (1280, 1024),
                                                                (800, 1280), (640, 480), (200, 100))],
                         [36, 26, 22, 27, 17, 13, 10])
        step_texts = [step.button for steps in guidance.SCRIPTS.values() for step in steps if step.button]
        for size in ((1920, 1080), (1024, 768), (1280, 1024), (800, 1280), (640, 480)):
            window = self.window(size=size)
            self.assertEqual(window.unit, command.unit_for(*size))
            window.guide = guide("removal")
            for phase, shown in window.pages.items():
                shown.place(command.BAND_LOWER)
                window.stack.setCurrentWidget(shown)
                self.app.processEvents()
                for candidate in shown.buttons:
                    for text in (step_texts if candidate is window.step_button else [candidate.text()]):
                        candidate.setText(text)
                        candidate.setVisible(True)
                        self.app.processEvents()
                        needed = candidate.fontMetrics().horizontalAdvance(text)
                        with self.subTest(size=size, phase=phase, text=text):
                            self.assertLessEqual(needed, candidate.contentsRect().width() - window.unit)
                            self.assertGreaterEqual(candidate.height(), int(window.unit * 2.6))
                            self.assertLessEqual(candidate.mapTo(window, candidate.rect().bottomRight()).y(), size[1])
            self.discard(window)
        resized = self.window(size=(1280, 800))
        resized.resize(800, 1280)
        self.app.processEvents()
        self.assertEqual(resized.unit, 17)  # From the window's own size, again when it changes.

    # -- the run --

    def test_the_removal_script_runs_on_the_clock_and_its_one_click_is_sent_as_a_mark_at_its_time(self):
        window, worker = self.start()
        started = self.now
        self.advance(3000)  # The helper is still starting: the screen counts, moves, and offers Stop.
        self.assertEqual((self.offered(), window.instruction_label.text(), window.detail_label.text(), window.time_label.text()),
                         (["Stop"], guidance.TEXT_STARTING, "Waiting 3 s", "Time left: 27 s"))
        worker.on_event(("ready", self.now))
        ready = self.now
        self.advance(50)
        self.assertEqual(worker.marks, [("near", ready + 50 * MS)])
        self.assertEqual((self.offered(), window.instruction_label.text(), window.step_label.text(), window.detail_label.text()),
                         (["Stop"], "Keep holding the bottle about half a metre in front of you", "Step 1 of 3", ""))
        self.assertTrue(window.progress.isVisible())
        self.advance(4000)
        self.assertEqual([label for label, _ in worker.marks], ["near", "far"])
        self.assertEqual(window.instruction_label.text(), "Now move it to about a metre away, and hold it there")
        self.advance(6000)
        self.assertEqual((sorted(self.buttons()), self.offered(), window.progress.isVisible()),
                         (["I am taking it off now", "Stop"], ["Stop"], False))
        self.assertEqual(window.time_label.text(), "Time left: 17 s")  # Thirty, less the launch and the ten of the script.
        self.advance(1250)
        self.assertEqual(self.offered(), ["I am taking it off now", "Stop"])
        self.click("I am taking it off now")
        self.assertEqual(worker.marks[2], ("remove", self.now))
        self.assertEqual((self.offered(), window.instruction_label.text()), (["Stop"], "You can take the headset off now"))
        self.advance(3000)
        self.assertEqual((window.phase, len(worker.marks), worker.stops, window.started_ns), ("run", 3, 0, started))

    def test_the_remove_button_is_taken_down_when_its_mark_could_no_longer_be_recorded(self):
        window, worker = self.start()
        self.advance(12_000)  # A slow launch: twelve of the thirty seconds are gone before the reader is ready.
        self.run_to("remove", worker)
        self.advance(1500)
        self.assertEqual((self.offered(), window.time_label.text()), (["I am taking it off now", "Stop"], "Time left: 7 s"))
        point = self.point("I am taking it off now")
        self.advance(1500)
        self.assertEqual((self.offered(), window.instruction_label.text()), (["Stop"], guidance.TEXT_OUT_OF_TIME))
        self.click_at(point)  # The wearer clicks where the button was: no mark is made, and none is claimed.
        self.assertEqual([label for label, _ in worker.marks], ["near", "far"])
        self.end_run(worker, finished_report())
        self.assertEqual(window.phase, "watch")
        self.press("Something changed")
        self.press("Confirm")
        report = self.saved()
        self.assertEqual((report["guide"]["out_of_time"], report["guide"]["steps_not_reached"]),
                         (True, [{"step": 3, "label": "remove"}]))
        self.assertIn("Steps not reached: remove.", window.result_label.text())

    def test_the_positions_script_needs_no_click_after_start(self):
        window, worker = self.start("positions")
        worker.on_event(("ready", self.now))
        ready = self.now
        self.advance(24000)
        self.assertEqual([label for label, _ in worker.marks], [step.label for step in guidance.POSITION_STEPS])
        for (label, when), index in zip(worker.marks, range(6)):
            self.assertTrue(0 <= when - (ready + index * 4000 * MS) <= 50 * MS, (label, when))
        self.assertEqual({entry[3] for entry in window.run_screen_log}, {""})  # No step button was ever offered.
        self.assertEqual((self.offered(), window.instruction_label.text()), (["Stop"], guidance.TEXT_FINISHED))

    def test_while_the_reader_is_active_the_run_screen_is_the_script_the_click_and_the_clock_only(self):
        window, worker = self.start()
        replay = guide("removal", started_ns=window.started_ns)
        # Everything a worker could try to hand over mid-run, with camera-derived content, reaches nothing.
        intruders = [("frame", {"labels": ["bottle"], "detection_count": 3}), ("detections", [{"label": "chair"}]),
                     ("progress", {"frames_processed": 12, "labels_seen": ["bottle", "person"]}),
                     ("ready", self.now), ("summary", {"coverage": {"windows_with_target": 9}})]
        self.advance(700)
        worker.on_event(("ready", self.now))
        replay.ready(self.now)
        allowed = guidance.static_texts("removal")
        for tick in range(0, 20000, 50):
            if tick % 1000 == 0:
                for intruder in intruders:
                    window.events.put(intruder)
            self.now += 50 * MS
            window.tick()
            replay.advance(self.now)
            if tick == 13000:
                self.click("I am taking it off now")
                replay.advance(self.now, clicked=True)
            view = replay.view(self.now)
            self.assertEqual((window.instruction_label.text(), window.step_button.text() if window.step_button.isVisible() else "",
                              window.step_label.text(), window.time_label.text(), window.picture.kind,
                              window.progress.isVisible(), window.progress.value()),
                             (view.instruction, view.button, f"Step {view.step_number} of {view.step_count}",
                              f"Time left: {view.remaining_seconds} s", view.picture, view.progress is not None,
                              0 if view.progress is None else round(view.progress * 1000)))
            self.assertEqual(self.visible_texts(), [window.step_label.text(), window.time_label.text(), view.instruction])
            self.assertLessEqual(set(self.offered()), {"Stop", view.button})
            self.assertIsNone(window.report)
        self.assertEqual(replay.ready_ns, window.guide.ready_ns)  # The second "ready" changed nothing.
        self.assertEqual((window.phase, window.result_label.text(), window.saved_label.text()), ("run", "", ""))
        self.assertLessEqual({text for entry in window.run_screen_log for text in entry[2:4] if text}, allowed)
        self.assertEqual([label for label, _ in worker.marks], ["near", "far", "remove"])

    def test_stop_ends_the_helper_once_and_the_report_is_kept(self):
        window, worker = self.start()
        self.run_to("far", worker)
        self.press("Stop")
        self.assertEqual((worker.stops, window.instruction_label.text(), self.offered()), (1, guidance.TEXT_STOPPING, []))
        window.stop_run(guidance.ENDED_WINDOW_CLOSED)
        self.advance(3000)
        self.assertEqual((worker.stops, worker.terminations, len(worker.marks), window.phase), (1, 0, 2, "run"))
        self.end_run(worker, finished_report([window_mark(1, "near", worker.marks[0][1])], status="stopped",
                                             reason="stop_requested"))
        self.assertEqual((window.phase, worker.closed, self.config.report_path.exists()), ("watch", 1, False))
        self.press("Something changed")
        self.press("Confirm")
        report = self.saved()
        self.assertEqual((report["status"], report["guide"]["ended_by"], report["guide"]["steps"][0]["mark_index"]),
                         ("stopped", "stop_button", 1))
        self.assertEqual((report["guide"]["observations"]["passthrough_after"], report["guide"]["observations"]["headset"]),
                         ("changed", "not_asked"))

    def test_closing_the_window_during_the_run_ends_the_helper_and_leaves_the_report(self):
        window, worker = self.start("positions")
        worker.on_event(("ready", self.now))
        self.advance(5000)
        window.close()
        self.assertTrue(window.isVisible())  # Not yet: the helper is ended first.
        self.assertEqual((worker.stops, window.phase, window.instruction_label.text()), (1, "run", guidance.TEXT_STOPPING))
        self.advance(1000)
        self.assertFalse(self.config.report_path.exists())
        self.end_run(worker, finished_report(status="stopped", reason="stop_requested"))
        self.assertFalse(window.isVisible())
        report = self.saved()
        self.assertEqual((report["status"], report["guide"]["ended_by"], report["confirmation"]),
                         ("stopped", "window_closed", "operator_window"))
        self.assertEqual(report["guide"]["observations"],
                         {"starting_state": "colour_stable", "passthrough_after": None, "headset": None,
                          "watch_seconds_elapsed": None, "watch_completed": False})
        self.assertEqual([entry["reached"] for entry in report["guide"]["steps"]], [True, True] + [False] * 4)

    def test_a_signal_during_the_run_does_what_closing_the_window_does(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number):
                window, worker = self.start()
                worker.on_event(("ready", self.now))
                self.advance(500)
                window.signal_number = number
                self.advance(100)
                self.assertEqual((worker.stops, window.phase), (1, "run"))
                self.end_run(worker, finished_report(status="stopped", reason="stop_requested"))
                self.assertFalse(window.isVisible())
                self.assertEqual(self.saved()["guide"]["ended_by"], "signal")
                self.config.report_path.unlink()
                self.workers.clear()

    def test_a_loop_that_ends_under_a_run_stops_the_helper_and_says_what_happened(self):
        window, worker = self.start()
        worker.on_event(("ready", self.now))
        self.advance(500)
        window.finish()  # What ``run_window`` does when Qt's loop returns with the run still going.
        report = self.saved()
        self.assertEqual((worker.stops, report["status"], report["reason"], report["cleanup_ok"], report["confirmation"]),
                         (1, "error", "guide_window_ended_during_run", False, "operator_window"))
        self.assertEqual(report["guide"]["started_by_start_button"], True)
        self.assertEqual(window.result_label.text().splitlines(), ["The run ended without a clean shutdown.", "Tell the Frame manager."])

    # -- a run that did not go as scripted --

    def test_a_launch_that_fails_goes_straight_to_plain_words_with_no_headset_question_and_no_watch(self):
        window, worker = self.start()
        self.advance(200)
        worker.on_event(("ready", self.now))  # The helper says ready before it connects.
        self.end_run(worker, finished_report(status="helper_ended_early", reason="sdk_error", frames=(),
                                             helper_end={"monotonic_ns": self.now, "reason": "sdk_error"}))
        self.assertEqual(window.phase, "result")
        self.assertEqual(window.result_label.text().splitlines(),
                         ["The camera reader ended early (sdk_error).", "No camera frame was read.", "Tell the Frame manager."])
        report = self.saved()
        self.assertEqual((report["guide"]["watch"], report["guide"]["observations"]["passthrough_after"],
                          report["guide"]["observations"]["headset"]), ("no_frame_read", None, None))
        self.assertIsNone(window.watch_started_ns)

    def test_a_presence_end_before_the_remove_click_is_watched_but_not_called_a_removal(self):
        window, worker = self.start()
        self.run_to("near", worker)
        self.end_run(worker, finished_report([window_mark(1, "near", worker.marks[0][1])],
                                             status="helper_ended_early", reason="headset_removed"))
        self.assertEqual((window.phase, window.observations["headset"]), ("watch", "not_asked"))
        self.advance(30_000)
        self.click("Passthrough unchanged")
        self.assertEqual(window.result_label.text().splitlines()[0],
                         "The camera reader ended early (headset_removed), before the click to take the headset off.")

    def test_a_report_without_a_clean_shutdown_waits_out_the_helpers_bound_and_shows_nothing_frame_derived(self):
        window, worker = self.start()
        self.run_to("remove", worker)
        self.press("I am taking it off now")
        ended = self.now
        self.end_run(worker, finished_report([window_mark(3, "remove", worker.marks[2][1])], status="helper_ended_early",
                                             reason="headset_removed", cleanup_ok=False))
        self.assertEqual((window.phase, self.buttons()), ("interrupted", {}))
        self.assertIn(command.INTERRUPTED_TEXT, self.visible_texts())
        gone = window.started_ns + (30 + command.HELPER_GONE_MARGIN_SECONDS) * SECOND
        self.assertEqual(window.interrupted_count.text(), str(-(-(gone - self.now) // SECOND)))
        self.advance((gone - ended) // MS - 1000, step=250)
        self.assertEqual(window.phase, "interrupted")  # The helper may still be alive: no watch yet.
        self.advance(1500, step=250)
        self.assertEqual((window.phase, window.observations["headset"]), ("watch", "not_asked"))
        self.advance(30_000)
        self.click("Passthrough unchanged")
        self.assertEqual(window.result_label.text().splitlines(), ["The run ended without a clean shutdown.", "Tell the Frame manager."])
        self.assertEqual(self.saved()["guide"]["watch"], "held")

    def test_a_worker_that_dies_is_called_an_interruption_and_the_watch_waits_for_the_helpers_bound(self):
        window, worker = self.start("positions")
        self.run_to("centre", worker)
        self.end_run(worker, None)
        self.assertEqual((window.phase, window.report["reason"], window.report["cleanup_ok"], window.report["exit_code"]),
                         ("interrupted", "guide_worker_ended_without_report", False, 1))
        self.advance(41_000, step=500)
        self.assertEqual(window.phase, "watch")
        self.advance(30_000)
        self.click("Passthrough unchanged")
        self.assertEqual(window.result_label.text().splitlines(), ["The run was interrupted.", "Tell the Frame manager."])

    def test_a_worker_that_never_answers_is_asked_to_stop_then_terminated_then_reported(self):
        window, worker = self.start()
        worker.on_event(("ready", self.now))
        margin = (30 + command.RUN_MARGIN_SECONDS) * 1000
        self.advance(margin, step=500)
        self.assertEqual((worker.stops, worker.terminations), (0, 0))
        self.advance(1000, step=500)
        self.assertEqual((worker.stops, worker.terminations, window.phase), (1, 0, "run"))
        self.advance(int(command.WORKER_PATIENCE_SECONDS * 1000) + 1000, step=500)
        self.assertEqual((worker.stops, worker.terminations, window.phase), (1, 1, "run"))
        self.advance(int(command.WORKER_PATIENCE_SECONDS * 1000) + 1000, step=500)
        self.assertEqual((worker.terminations, window.phase), (1, "watch"))  # The helper's bound is long past.
        self.assertEqual((window.report["status"], window.report["reason"], window.report["cleanup_ok"]),
                         ("error", "guide_worker_unresponsive", False))

    # -- after the run --

    def test_after_the_remove_click_the_headset_is_asked_about_and_then_the_watch_counts_thirty_seconds(self):
        window, worker = self.start()
        self.run_to("remove", worker)
        self.press("I am taking it off now")
        self.end_run(worker, finished_report([window_mark(3, "remove", worker.marks[2][1])],
                                             status="helper_ended_early", reason="headset_removed"))
        self.assertEqual((window.phase, sorted(self.buttons())), ("headset_back", ["No, I kept it on", "Yes, off and back on"]))
        self.assertIn(command.BACK_QUESTION, self.visible_texts())
        self.advance(120_000, step=1000)  # The headset is off: nothing moves until the wearer is back.
        self.assertEqual(window.phase, "headset_back")
        self.click("Yes, off and back on")
        self.assertEqual((window.phase, window.count_label.text(), self.offered()), ("watch", "30", []))
        self.advance(12_000)
        self.assertEqual((window.count_label.text(), self.offered()), ("18", ["Something changed"]))
        window.answer_passthrough(guidance.PASSTHROUGH_UNCHANGED)  # Not before the watch is over, however it is asked.
        window.answer_passthrough(guidance.PASSTHROUGH_CHANGED)  # And "changed" only from its confirmation.
        self.assertEqual((window.phase, window.observations["passthrough_after"]), ("watch", None))
        self.advance(18_000)
        self.assertEqual((window.count_label.text(), self.offered()), ("0", ["Passthrough unchanged", "Something changed"]))
        self.click("Passthrough unchanged")
        self.assertEqual((window.phase, sorted(self.buttons())), ("result", ["Close"]))
        report = self.saved()
        self.assertEqual(report["guide"]["observations"],
                         {"starting_state": "colour_stable", "passthrough_after": "unchanged",
                          "headset": "removed_and_back", "watch_seconds_elapsed": 30.0, "watch_completed": True})
        self.assertEqual((report["status"], report["reason"], report["confirmation"], report["guide"]["confirmation"],
                          report["guide"]["watch"]),
                         ("helper_ended_early", "headset_removed", "operator_window", "operator_window", "held"))
        self.assertEqual((report["camera_data"]["report_saved_by_operator"], report["camera_data"]["report_path"]),
                         (True, str(self.config.report_path)))
        self.assertEqual(window.result_label.text().splitlines()[0], "The run ended when the headset came off (headset_removed).")

    def test_something_changed_is_asked_once_more_and_go_back_returns_to_the_same_watch(self):
        window, worker = self.start("positions")
        self.run_to("centre", worker)
        self.end_run(worker, finished_report())
        self.assertEqual(window.phase, "watch")
        self.advance(4000)
        self.click("Something changed")
        self.assertEqual((window.phase, sorted(self.buttons()), window.observations["passthrough_after"]),
                         ("confirm_changed", ["Confirm", "Go back"], None))
        self.assertIn(command.CONFIRM_QUESTION, self.visible_texts())
        self.assertFalse(self.config.report_path.exists())
        self.press("Go back")
        self.assertEqual((window.phase, window.count_label.text()), ("watch", "25"))  # The count went on meanwhile.
        self.press("Something changed")
        self.press("Confirm")
        observations = self.saved()["guide"]["observations"]
        self.assertEqual((observations["passthrough_after"], observations["headset"], observations["watch_completed"]),
                         ("changed", "not_asked", False))
        self.assertTrue(7.5 <= observations["watch_seconds_elapsed"] <= 7.7)  # Counted from the watch's start.

    def test_closing_the_window_during_the_watch_keeps_the_report_with_the_answer_missing(self):
        window, worker = self.start("positions")
        self.run_to("centre", worker)
        self.end_run(worker, finished_report())
        self.advance(7000)
        window.close()
        self.assertFalse(window.isVisible())
        observations = self.saved()["guide"]["observations"]
        self.assertEqual((observations["passthrough_after"], observations["watch_completed"]), (None, False))
        self.assertTrue(7.0 <= observations["watch_seconds_elapsed"] <= 7.1)

    def test_the_report_is_never_overwritten(self):
        window, worker = self.start("positions")
        self.run_to("centre", worker)
        self.end_run(worker, finished_report())
        self.config.report_path.write_text("someone else's file\n")  # It appeared after the window's first check.
        self.press("Something changed")
        self.press("Confirm")
        self.assertEqual(self.config.report_path.read_text(), "someone else's file\n")
        self.assertEqual((window.final["exit_code"], window.final["camera_data"]["report_saved_by_operator"],
                          window.final["camera_data"]["report_path"]), (1, False, None))
        self.assertIn("report_error", window.final)
        self.assertTrue(window.saved_label.text().startswith("The report was NOT saved"))
        window.finish()  # Composing is once: a later close writes nothing.
        self.assertEqual(self.config.report_path.read_text(), "someone else's file\n")


    # -- the start hold (item L18) --

    def hold_window(self, name="report"):
        """A window asked to hold its Start, on its first screen; the release file's path is ``self.release``."""
        self.now, self.workers = T0, []
        self.release = Path(self.directory.name) / f"go-{name}"
        self.config = command.Config("removal", "/nonexistent/helper", Path(self.directory.name) / f"{name}.json",
                                     command.run_limits("removal"), hold_start=self.release)
        window = command.create_window(self.config, worker_factory=self.factory, clock=lambda: self.now, size=(1280, 800))
        window.timer.stop()
        window.show()
        self.QTest.qWaitForWindowExposed(window)
        self.app.processEvents()
        self.addCleanup(self.discard, window)
        self.shown = window
        return window

    def held(self, **options):
        """A window asked to hold its Start, on the notice screen."""
        self.release = Path(self.directory.name) / "go"
        return self.to_notice(hold_start=self.release, **options)

    def test_start_is_held_until_the_operator_releases_it_and_the_click_still_starts_the_run(self):
        window = self.held()
        self.assertIn(guidance.TEXT_HOLD_WAITING, self.visible_texts())
        self.assertEqual((window.dwell_label.text(), self.offered()), ("You can start in 5 s", []))
        start = self.point("Start")
        for _ in range(12):  # Twelve seconds: the dwell and the guard are long over, and Start is on the screen, dead.
            self.advance(1000)
            self.click_at(start)
            window.start_run()  # However it is asked.
        self.assertEqual((window.phase, self.workers, window.started_ns, window.workers_started), ("notice", [], None, 0))
        self.assertEqual((window.dwell_label.text(), window.hold_label.text(), self.offered()),
                         (guidance.TEXT_HOLD_DWELL, guidance.TEXT_HOLD_WAITING, ["Cancel"]))
        self.assertIsNone(window.hold_released_ns)
        self.release.write_text("anything: it is never read")  # The operator's touch.
        self.advance(250)
        released_ns = window.hold_released_ns
        self.assertGreater(released_ns, self.now - 300 * MS)
        self.assertLessEqual(released_ns, self.now)
        self.assertIn(guidance.TEXT_HOLD_RELEASED, self.visible_texts())
        self.assertNotIn(guidance.TEXT_HOLD_WAITING, self.visible_texts())
        # The release enabled nothing for the guard interval and started nothing at all.
        self.assertEqual((self.offered(), window.dwell_label.text()), (["Cancel"], guidance.TEXT_HOLD_DWELL))
        self.click_at(start)
        window.start_run()
        self.advance(self.GUARD_MS)
        self.assertEqual((self.offered(), window.dwell_label.text(), self.workers, window.phase),
                         (["Cancel", "Start"], "You can start now", [], "notice"))
        self.advance(3000)  # The wearer takes their time: nothing starts by itself.
        self.assertEqual((self.workers, window.phase), ([], "notice"))
        self.click_at(start)
        self.assertEqual((window.phase, len(self.workers), self.workers[0].started, window.started_ns), ("run", 1, 1, self.now))
        started_ns, worker = self.now, self.workers[0]
        worker.on_event(("ready", self.now))
        self.advance(200)
        self.end_run(worker, finished_report())
        self.advance(30_000 + self.GUARD_MS)
        self.click("Passthrough unchanged")
        report = self.saved()
        self.assertEqual(report["guide"]["start_hold"], {
            "requested": True, "released": True, "release_monotonic_ns": released_ns,
            "release_utc_ns": 1_795_000_000 * SECOND + released_ns - T0,
            "release_to_start_click_ms": (started_ns - released_ns) / MS, "timed_out": False, "timeout_seconds": 600,
            "poll_interval_ms": 250, "release_file_read": False})
        self.assertEqual((report["guide"]["start_click_monotonic_ns"], report["guide"]["started_by_start_button"],
                          report["guide"]["ended_by"], report["status"]), (started_ns, True, "helper", "recognized"))
        self.assertEqual(report["guide"]["schema_version"], 3)
        self.assertEqual(self.release.read_text(), "anything: it is never read")  # Neither changed nor removed.

    def test_a_release_made_before_the_notice_leaves_start_to_the_notices_own_wait(self):
        self.release = Path(self.directory.name) / "go"
        window = self.window(hold_start=self.release)
        self.release.touch()
        self.advance(self.GUARD_MS)
        released_ns = window.hold_released_ns
        self.assertLessEqual(T0, released_ns)
        self.assertLessEqual(released_ns, T0 + 250 * MS)
        self.click("Colour, stable")
        self.press("Next")
        self.assertIn(guidance.TEXT_HOLD_RELEASED, self.visible_texts())
        self.advance(4900)
        self.assertEqual((self.offered(), window.dwell_label.text()), (["Cancel"], "You can start in 1 s"))
        self.advance(100)
        self.click("Start")
        self.assertEqual((window.phase, len(self.workers)), ("run", 1))
        self.release.unlink()  # The release is kept once seen.
        self.end_run(self.workers[0], dict(command.unstarted_report(self.config, "x"), confirmation="operator_window",
                                           cleanup_ok=True))
        hold = self.saved()["guide"]["start_hold"]
        self.assertEqual((hold["released"], hold["release_monotonic_ns"], hold["release_to_start_click_ms"]),
                         (True, released_ns, (window.started_ns - released_ns) / MS))
        self.assertIsNone(hold["release_utc_ns"])  # This report has no timebase: there is no run clock to give it by.

    def test_removing_the_release_file_does_not_hold_start_again(self):
        window = self.held()
        self.release.touch()
        self.advance(5000)
        self.release.unlink()
        self.advance(5000)
        self.assertEqual((self.offered(), window.hold_label.text()), (["Cancel", "Start"], guidance.TEXT_HOLD_RELEASED))

    def test_only_a_regular_file_at_the_path_itself_is_a_release(self):
        elsewhere = Path(self.directory.name) / "elsewhere"
        elsewhere.write_text("a file, but not at the path")
        for name, make in (("link", lambda path: path.symlink_to(elsewhere)), ("directory", lambda path: path.mkdir())):
            with self.subTest(name=name):
                window = self.hold_window(name)
                make(self.release)
                self.press("Colour, stable")
                self.press("Next")
                self.advance(8000)
                self.assertEqual((window.hold_released_ns, self.offered(), self.workers), (None, ["Cancel"], []))
                window.hide()

    def test_the_window_looks_for_the_release_four_times_a_second_on_its_own_tick_and_never_opens_the_file(self):
        before = threading.active_count()
        with mock.patch.object(command, "hold_released", return_value=False) as looked:
            window = self.held()
            self.assertEqual(threading.active_count(), before)  # The timer's tick does the looking: no thread.
            looks = looked.call_count
            self.advance(5000)
            self.assertEqual(looked.call_count - looks, 20)
            self.assertEqual({call.args for call in looked.call_args_list}, {(self.release,)})
            looked.return_value = True
            self.advance(250)
            self.assertIsNotNone(window.hold_released_ns)
            looks = looked.call_count
            self.advance(self.GUARD_MS)
            self.click("Start")
            self.advance(2000)
            self.assertEqual((looked.call_count, window.phase), (looks, "run"))  # Not once more after the release.
        self.release.write_text("contents")
        with mock.patch("builtins.open", side_effect=AssertionError("the release file was opened")), \
                mock.patch.object(os, "open", side_effect=AssertionError("the release file was opened")):
            self.assertTrue(command.hold_released(self.release))

    def test_a_hold_nobody_releases_ends_the_window_unstarted_after_ten_minutes_with_nothing_launched(self):
        for name, clicks in (("on_the_first_screen", ()), ("on_the_notice", ("Colour, stable", "Next"))):
            with self.subTest(name=name):
                window = self.hold_window(name)
                for text in clicks:
                    self.press(text)
                self.advance((T0 - self.now) // MS + 599_900, step=100)
                self.assertEqual((window.final, self.config.report_path.exists()), (None, False))
                self.assertIn(window.phase, ("starting_state", "notice"))
                self.advance(100, step=100)
                self.assertEqual((window.phase, self.workers, sorted(self.buttons())), ("result", [], ["Close"]))
                report = self.saved()
                self.assertEqual((report["status"], report["reason"], report["confirmation"], report["exit_code"]),
                                 ("error", "start_hold_not_released", None, 1))
                self.assertEqual((report["guide"]["ended_by"], report["guide"]["started_by_start_button"],
                                  report["guide"]["watch"], report["guide"]["start_click_monotonic_ns"]),
                                 ("start_hold_timeout", False, "run_not_started", None))
                self.assertEqual(report["guide"]["start_hold"], {
                    "requested": True, "released": False, "release_monotonic_ns": None, "release_utc_ns": None,
                    "release_to_start_click_ms": None, "timed_out": True, "timeout_seconds": 600,
                    "poll_interval_ms": 250, "release_file_read": False})
                self.assertNotIn("capture", report)
                self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
                self.assertIn("Not started: the Frame manager did not say go in time. Nothing was launched. "
                              "Tell the Frame manager.", self.visible_texts())
                self.release.touch()  # Too late: the window has ended and stays ended.
                self.advance(5000)
                self.assertEqual((window.phase, self.workers, window.hold_released_ns), ("result", [], None))
                window.hide()

    def test_a_release_on_the_last_tick_of_the_bound_still_counts(self):
        window = self.held(hold_timeout_seconds=20.0)
        self.advance((T0 - self.now) // MS + 19_950)
        self.assertIsNone(window.final)
        self.release.touch()
        self.advance(50)
        self.assertEqual((window.phase, window.hold_released_ns, window.final), ("notice", self.now, None))
        self.advance(60_000, step=250)  # Once released, the bound no longer applies: the wearer starts when ready.
        self.assertEqual((window.phase, self.offered()), ("notice", ["Cancel", "Start"]))

    def test_cancel_under_a_hold_leaves_the_usual_report_with_the_hold_in_it(self):
        window = self.held()
        self.press("Cancel")
        report = self.saved()
        self.assertEqual((report["reason"], report["guide"]["ended_by"]), ("confirmation_declined", "cancel_button"))
        self.assertEqual((report["guide"]["start_hold"]["released"], report["guide"]["start_hold"]["timed_out"]),
                         (False, False))
        self.advance(700_000, step=250)  # The bound passing afterwards changes nothing.
        self.assertEqual((window.phase, self.saved()), ("result", report))

    def test_without_the_option_the_window_never_looks_and_its_notice_and_report_are_what_they_were(self):
        with mock.patch.object(command, "hold_released", side_effect=AssertionError("looked for a release")):
            window = self.to_notice()
            self.assertIsNone(window.hold_label)
            self.assertFalse({guidance.TEXT_HOLD_WAITING, guidance.TEXT_HOLD_RELEASED, guidance.TEXT_HOLD_DWELL}
                             & set(self.visible_texts()))
            self.advance(700_000, step=250)  # Far past the hold's bound: there is no hold, so nothing ends.
            self.assertEqual((window.phase, window.dwell_label.text(), self.offered()),
                             ("notice", "You can start now", ["Cancel", "Start"]))
            self.click("Cancel")
        self.assertNotIn("start_hold", self.saved()["guide"])
        held = command.Config("removal", "/h", Path("/r.json"), command.run_limits("removal"), hold_start=Path("/go"))
        plain = command.Config("removal", "/h", Path("/r.json"), command.run_limits("removal"))
        self.assertEqual(held.spec(), plain.spec())  # The worker is not told: the hold is the window's alone.


@unittest.skipUnless(PYSIDE and sys.platform.startswith("linux"), "Needs Linux and PySide6")
class RunWindowTests(unittest.TestCase):
    """``run_window``, the command's own Qt loop: a signal is taken up on the window's clock and the report is left."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore, QtWidgets
        self.QtCore = QtCore
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for number in command.SIGNALS:
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def test_a_signal_before_start_closes_the_window_launches_nothing_and_leaves_the_report(self):
        self.assertEqual(command.SIGNALS, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
        for number in command.SIGNALS:  # SIGHUP is what a dropped SSH session or a closed terminal sends.
            with self.subTest(signal=number):
                config = command.Config("removal", "/nonexistent/helper", Path(self.directory.name) / f"{number}.json",
                                        command.run_limits("removal"))
                before = signal.getsignal(number)
                # Sent from inside the loop, so the command's handlers are in place when it arrives.
                self.QtCore.QTimer.singleShot(200, lambda number=number: os.kill(os.getpid(), number))
                with mock.patch.object(command, "Worker", side_effect=AssertionError("a worker was made")):
                    report = command.run_window(config, windowed=True)
                self.assertIs(signal.getsignal(number), before)  # The command's handlers are gone with its loop.
                self.assertEqual((report["status"], report["reason"], report["confirmation"], report["exit_code"]),
                                 ("error", "confirmation_declined", None, 1))
                self.assertEqual((report["guide"]["ended_by"], report["guide"]["started_by_start_button"]), ("signal", False))
                self.assertEqual(json.loads(config.report_path.read_text()), report)


@unittest.skipUnless(PYSIDE and NUMPY_CV2 and sys.platform.startswith("linux"), "Needs Linux, PySide6, NumPy and OpenCV")
class FlowTests(StandInHelper):
    """The window with the real worker process and the stand-in helper, clicked through offscreen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def begin(self, config):
        flow = command.Flow(config)
        self.addCleanup(flow.window.close)
        self.assertTrue(flow.start())
        return flow

    def test_the_positions_script_runs_to_its_bound_and_the_result_follows_the_helpers_end(self):
        config = self.config("positions")
        flow = self.begin(config)
        self.assertTrue(flow.reach("watch"))
        self.assertEqual((flow.window.result_label.text(), config.report_path.exists()), ("", False))
        self.assertTrue(flow.click_when_offered("Passthrough unchanged") and flow.reach("result"))
        report = json.loads(config.report_path.read_text())
        self.assertEqual((report["status"], report["exit_code"], report["confirmation"]), ("recognized", 0, "operator_window"))
        self.assertEqual([entry["label"] for entry in report["marks"]["recorded"]],
                         [step.label for step in guidance.POSITION_STEPS])
        self.assertEqual({entry["source"] for entry in report["marks"]["recorded"]}, {"window"})
        self.assertEqual([(entry["reached"], entry["mark_index"]) for entry in report["guide"]["steps"]],
                         [(True, index) for index in range(1, 7)])
        self.assertEqual((report["guide"]["validation_only"], report["guide"]["watch"]), (True, "held"))
        self.assertEqual(report["capture"]["helper"]["parent_confirmed"], True)
        self.assertLessEqual(report["guide"]["start_click_monotonic_ns"], report["guide"]["reader_ready_monotonic_ns"])
        self.assertLessEqual(flow.run_screen_texts(), guidance.static_texts("positions"))
        self.assertEqual(flow.window.result_label.text().splitlines(), guidance.result_lines(report))
        self.assertTrue(flow.close())

    def test_a_presence_end_before_the_remove_click_ends_the_run_by_itself_and_is_watched(self):
        config = self.config("removal", scenario="headset_removed", seconds=20)
        flow = self.begin(config)
        self.assertTrue(flow.finish_after_run())
        report = json.loads(config.report_path.read_text())
        self.assertEqual((report["status"], report["reason"], report["guide"]["ended_by"], report["guide"]["watch"]),
                         ("helper_ended_early", "headset_removed", "helper", "held"))
        self.assertEqual(report["guide"]["observations"]["headset"], "not_asked")
        self.assertIn("before the click to take the headset off", guidance.result_lines(report)[0])
        self.assertTrue(report["cleanup_ok"])

    def test_a_helper_that_refuses_goes_straight_to_the_result(self):
        config = self.config("removal", scenario="refuse")
        flow = self.begin(config)
        self.assertTrue(flow.reach("result"))
        report = json.loads(config.report_path.read_text())
        self.assertEqual((report["frames_received"], report["guide"]["watch"], report["guide"]["observations"]["passthrough_after"]),
                         (0, "no_frame_read", None))
        self.assertEqual(flow.window.result_label.text().splitlines()[1:], ["No camera frame was read.", "Tell the Frame manager."])
        self.assertTrue(flow.close())

    def test_stop_and_a_closed_window_each_end_the_real_helper_and_leave_the_report(self):
        for name, act, ended_by in (("stop", lambda flow: flow.click_when_offered("Stop"), "stop_button"),
                                    ("close", lambda flow: flow.window.close() or True, "window_closed")):
            with self.subTest(name=name):
                config = self.config("removal", name=name, scenario="hang", seconds=20)
                flow = self.begin(config)
                self.assertTrue(flow.wait(lambda: flow.window.guide.ready_ns is not None))  # The reader is active.
                self.assertTrue(act(flow))
                if name == "stop":  # Frames were read: the watch follows, here answered and confirmed.
                    self.assertTrue(flow.finish_after_run(passthrough="Something changed"))
                else:
                    self.assertTrue(flow.wait(lambda: not flow.window.isVisible()))
                report = json.loads(config.report_path.read_text())
                self.assertEqual((report["status"], report["cleanup_ok"], report["guide"]["ended_by"]),
                                 ("stopped", True, ended_by))
                self.assertEqual(report["capture"]["stream"]["end"]["reason"], "stop_signal")
                self.assertEqual(report["capture"]["supervision"]["worker_tree_reaped"], True)


class CommandLineTests(unittest.TestCase):
    """The checks made before any window exists."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.helper = fake_camera_export.install(self.directory.name)
        self.report = Path(self.directory.name) / "report.json"
        opened = mock.patch.object(command, "run_window", side_effect=AssertionError("a window was opened"))
        self.run_window = opened.start()
        self.addCleanup(opened.stop)

    def arguments(self, **changes):
        values = {"--helper": self.helper, "--report": self.report, "--script": "removal"}
        values.update(changes)
        return ["--allow-camera-input", *[item for name, value in values.items() if value is not None
                                          for item in (name, value)]]

    def test_the_run_arguments_are_required_and_bounded(self):
        for arguments in ([], ["--script", "removal", "--helper", self.helper, "--report", self.report],
                          self.arguments(**{"--helper": None}), self.arguments(**{"--report": None}),
                          self.arguments(**{"--script": None}), [*self.arguments(), "--seconds", "31"],
                          [*self.arguments(), "--max-frames", "301"], [*self.arguments(), "--target-label", "unicorn"],
                          [*self.arguments(), "--threshold", "2"], [*self.arguments(), "--late-frame-limit-ms", "-1"],
                          ["--validate", "--script", "removal"], ["--validate", "--allow-camera-input"]):
            with self.subTest(arguments=[str(item) for item in arguments][-3:]):
                code, report, _ = run_main(arguments)
                self.assertEqual((code, report), (2, None))
        self.assertFalse(self.report.exists())

    def test_a_bad_region_or_scan_is_refused_before_any_window(self):
        scan = self.arguments(**{"--script": "scan"})
        for arguments in ([*self.arguments(), "--region", "0.5,0.5,0.4,0.9"], [*self.arguments(), "--region", "0,0,1"],
                          [*self.arguments(), "--region", "0,0,1.2,1"], [*self.arguments(), "--region", "centre"],
                          [*self.arguments(), "--rotation", "45"], [*self.arguments(), "--scan-detectors", "yolox-nano"],
                          [*scan, "--region", "0.25,0.25,0.75,0.75"], [*scan, "--rotation", "90"],
                          [*scan, "--scan-detectors", "yolox-nano"],  # --detector (mobilenet-ssd) is not among them.
                          [*scan, "--scan-detectors", "mobilenet-ssd,other"],
                          [*scan, "--scan-detectors", "mobilenet-ssd,mobilenet-ssd"], [*scan, "--scan-detectors", ""],
                          [*scan, "--target-label", "sofa"],  # A class of one scan detector only.
                          [*scan, "--seconds", "9"], [*scan, "--max-frames", "47"],  # Bounds a scan cannot serve.
                          [*scan, "--seconds", "10", "--fps", "4"],
                          ["--validate", "--region", "0,0,1,1"], ["--validate", "--scan-detectors", "yolox-nano"]):
            with self.subTest(arguments=[str(item) for item in arguments][-3:]):
                code, report, _ = run_main(arguments)
                self.assertEqual((code, report), (2, None))
        self.assertFalse(self.report.exists())
        self.run_window.assert_not_called()

    def test_the_region_the_rotation_the_detector_and_the_scan_reach_the_window(self):
        seen = []

        def opened(config, windowed=False):
            seen.append(config)
            return command.unstarted_report(config, guidance.DECLINED)
        self.run_window.side_effect = opened
        import types
        runs = ((["--region", "0.25,0.25,0.75,0.75", "--rotation", "90", "--detector", "yolox-nano"], "removal"),
                (["--rotation", "180"], "positions"), ([], "positions"), ([], "scan"),
                (["--scan-detectors", "yolox-nano", "--detector", "yolox-nano"], "scan"))
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), \
                mock.patch.dict(sys.modules, {"PySide6": sys.modules.get("PySide6") or types.ModuleType("PySide6")}):
            for extra, script in runs:
                run_main([*self.arguments(**{"--script": script}), *extra])
        self.assertEqual([(config.script, config.region, config.rotation, config.detector, config.scan_detectors)
                          for config in seen],
                         [("removal", (0.25, 0.25, 0.75, 0.75), 90, "yolox-nano", ()),
                          ("positions", (0.0, 0.0, 1.0, 1.0), 180, "mobilenet-ssd", ()),
                          ("positions", None, 0, "mobilenet-ssd", ()),
                          ("scan", None, 0, "mobilenet-ssd", ("mobilenet-ssd", "yolox-nano")),
                          ("scan", None, 0, "yolox-nano", ("yolox-nano",))])
        self.assertEqual([(config.spec()["region"], config.spec()["rotation"], config.spec()["scan_detectors"])
                          for config in seen],
                         [([0.25, 0.25, 0.75, 0.75], 90, None), ([0.0, 0.0, 1.0, 1.0], 180, None), (None, 0, None),
                          (None, 0, ["mobilenet-ssd", "yolox-nano"]), (None, 0, ["yolox-nano"])])
        self.assertEqual(seen[3].limits, {"fps": 5, "max_seconds": 30, "max_frames": 150})
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), \
                mock.patch.dict(sys.modules, {"PySide6": sys.modules.get("PySide6") or types.ModuleType("PySide6")}):
            run_main([*self.arguments(**{"--script": "scan"}), "--seconds", "10"])  # The least a scan can be given.
        self.assertEqual(seen[-1].limits, {"fps": 5, "max_seconds": 10, "max_frames": 50})

    def test_the_report_path_is_checked_first_and_before_any_window(self):
        existing = Path(self.directory.name) / "existing.json"
        existing.write_text("{}\n")
        link = Path(self.directory.name) / "link.json"
        link.symlink_to(Path(self.directory.name) / "nowhere.json")
        for path in (existing, Path(self.directory.name) / "missing" / "report.json",
                     Path(self.directory.name) / "report.txt", link):
            for helper in (self.helper, Path(self.directory.name) / "no-such-helper"):  # The path is refused first.
                with self.subTest(path=path.name, helper=helper.name):
                    code, report, _ = run_main(self.arguments(**{"--report": path, "--helper": helper}))
                    self.assertEqual((code, report["status"], report["reason"], report["confirmation"]),
                                     (1, "error", "report_path_refused", None))
                    self.assertEqual((report["guide"]["started_by_start_button"], report["guide"]["ended_by"],
                                      report["camera_data"]["report_saved_by_operator"]), (False, "not_started", False))
                    self.assertNotIn("capture", report)
        self.assertEqual(existing.read_text(), "{}\n")
        self.run_window.assert_not_called()

    def test_a_directory_that_cannot_be_written_to_is_refused_before_any_window(self):
        directory = Path(self.directory.name) / "reports"
        directory.mkdir()
        self.assertEqual(command.check_report_path(directory / "a.json"), directory / "a.json")
        self.assertEqual(list(directory.iterdir()), [])  # The probe is gone.
        with mock.patch.object(tempfile, "NamedTemporaryFile", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(ValueError, "can write to"):
                command.check_report_path(directory / "a.json")
            code, report, _ = run_main(self.arguments(**{"--report": directory / "a.json"}))
        self.assertEqual((code, report["reason"]), (1, "report_path_refused"))
        self.assertIn("can write to", report["error"])
        self.run_window.assert_not_called()
        if os.geteuid() != 0:  # A real read-only directory, where the test is not root.
            directory.chmod(0o555)
            self.addCleanup(directory.chmod, 0o755)
            with self.assertRaises(ValueError):
                command.check_report_path(directory / "a.json")

    def test_a_helper_that_cannot_be_the_helper_is_refused_before_any_window(self):
        tracked = ROOT / "scripts/camera_guide.py"
        for helper, reason in ((Path(self.directory.name) / "no-such-helper", "helper_not_found"),
                               (tracked, "helper_inside_work_tree")):
            code, report, _ = run_main(self.arguments(**{"--helper": helper}))
            self.assertEqual((code, report["reason"]), (1, reason))
        self.assertFalse(self.report.exists())
        self.run_window.assert_not_called()

    def test_without_a_display_session_nothing_is_opened(self):
        with mock.patch.dict(os.environ):
            for name in ("QT_QPA_PLATFORM", "DISPLAY", "WAYLAND_DISPLAY"):
                os.environ.pop(name, None)
            code, report, _ = run_main(self.arguments())
        self.assertEqual((code, report["reason"]), (1, "no_display_session"))
        self.run_window.assert_not_called()

    def test_without_the_window_dependencies_nothing_is_opened(self):
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), mock.patch.dict(sys.modules, {"PySide6": None}):
            code, report, _ = run_main(self.arguments())
        self.assertEqual((code, report["reason"]), (1, "window_dependencies_unavailable"))
        self.run_window.assert_not_called()

    def test_an_accepted_command_line_opens_the_window_with_the_scripts_bounds(self):
        seen = {}

        def opened(config, windowed=False):
            seen.update(config=config, windowed=windowed)
            return command.unstarted_report(config, guidance.DECLINED)
        self.run_window.side_effect = opened
        import types
        # The window's dependency is only looked for here: the stand-in lets the dependency-free suite run this.
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), \
                mock.patch.dict(sys.modules, {"PySide6": sys.modules.get("PySide6") or types.ModuleType("PySide6")}):
            code, report, _ = run_main([*self.arguments(**{"--script": "positions"}), "--removal-end-limit-ms", "6000",
                                        "--windowed"])
        config = seen["config"]
        self.assertEqual((code, report["reason"], seen["windowed"]), (1, "confirmation_declined", True))
        self.assertEqual((config.script, config.limits, config.camera, config.target, config.validation, config.scale,
                          config.watch_seconds, config.guard_seconds, config.start_dwell_seconds, config.close_dwell_seconds),
                         ("positions", {"fps": 5, "max_seconds": 30, "max_frames": 150}, 0, "bottle", False, 1.0, 30,
                          1.2, 5.0, 3.0))
        self.assertEqual((config.late_frame_limit_ms, config.removal_end_limit_ms, config.report_path),
                         (2000, 6000, self.report))
        self.assertEqual(config.helper, os.path.realpath(self.helper))

    def test_validate_refuses_any_helper_that_does_not_describe_itself_as_the_fake(self):
        liar = Path(self.directory.name) / "fake-with-a-release"
        liar.write_text("#!/bin/sh\necho '{\"event\":\"describe\",\"backend\":\"fake\",\"sdk_release\":{\"x\":1}}'\n")
        liar.chmod(0o755)
        for helper in (self.helper, liar, Path(self.directory.name) / "absent"):
            with self.subTest(helper=helper.name), mock.patch.object(command, "create_window") as created:
                code, report, _ = run_main(["--validate", "--helper", helper])
                self.assertEqual((code, report["report_kind"], report["status"], report["reason"], report["flows"]),
                                 (1, "camera_guide_validation", "validation_failed", "validate_requires_fake_helper", {}))
                created.assert_not_called()


@unittest.skipUnless(PYSIDE and NUMPY_CV2 and sys.platform.startswith("linux") and shutil.which("g++"),
                     "Needs Linux, PySide6, NumPy, OpenCV and g++ for the helper's fake build")
class ValidateTests(unittest.TestCase):
    """``--validate``: every flow of the window against the helper's fake backend; nothing real, nothing displayed."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        built = subprocess.run(["bash", str(ROOT / "scripts/build_camera_helper.sh"), "--fake", "--out", cls.directory.name],
                               cwd=ROOT, capture_output=True, text=True, timeout=600)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        cls.helper = Path(cls.directory.name) / "sightcircuit-camera-helper-fake"

    def setUp(self):
        for number in RECORD_AND_POLL.numbers:
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def test_validate_clicks_through_every_flow_with_nothing_real_in_it(self):
        with mock.patch.dict(os.environ):
            code, report, _ = run_main(["--validate", "--helper", self.helper])
        self.assertEqual((code, report["report_kind"], report["status"], report["reason"], report["flows_failed"]),
                         (0, "camera_guide_validation", "validated", None, []), report)
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertEqual((report["validation_only"], report["camera_opened"], report["images_saved"],
                          report["pixels_in_report"], report["display_started"], report["qt_platform"]),
                         (True, False, False, False, False, "offscreen"))
        flows = report["flows"]
        self.assertEqual(list(flows), ["positions_to_the_bound", "removal_presence_end", "stop_during_the_run",
                                       "window_closed_during_the_run", "launch_failed", "cancel", "declined_start",
                                       "run_too_short_for_the_script", "scan_to_the_bound", "report_path_checked_first",
                                       "worker_refuses_a_hand_launch"])
        scan = flows["scan_to_the_bound"]
        self.assertEqual((scan["status"], scan["marks"], scan["schedule_length"], scan["view_mode"], scan["scan_detectors"],
                          scan["every_frame_tallied"], scan["boxes_over_the_whole_frame"], scan["run_screen_only_scripted"]),
                         ("recognized", ["near"], 24, "scan", ["mobilenet-ssd", "yolox-nano"], True, True, True))
        self.assertEqual((scan["coverage_omitted"], scan["detectors_failed"]), (True, []))
        self.assertEqual({name: entry["ok"] for name, entry in flows.items()}, dict.fromkeys(flows, True))
        self.assertEqual([flows[name]["workers_started"] for name in ("cancel", "declined_start")], [0, 0])
        self.assertEqual((flows["removal_presence_end"]["helper_end_reason"], flows["removal_presence_end"]["marks"]),
                         ("headset_removed", ["near", "far", "remove"]))
        self.assertEqual((flows["positions_to_the_bound"]["status"], flows["stop_during_the_run"]["ended_by"],
                          flows["window_closed_during_the_run"]["ended_by"], flows["launch_failed"]["watch"]),
                         ("recognized", "stop_button", "window_closed", "no_frame_read"))


class HoldTests(unittest.TestCase):
    """The start hold (item L18) without a window: its part of the report, its words and the release path's check."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(lambda: [path.chmod(0o700) for path in self.root.iterdir()
                                 if path.is_dir() and not path.is_symlink()])
        self.report = self.root / "report.json"

    def place(self, name, mode=0o700):
        directory = self.root / name
        directory.mkdir()
        directory.chmod(mode)
        return directory

    def test_the_hold_block_has_times_and_closed_values_only_and_other_launches_have_none(self):
        clock = marks.Clock(T0, 1_795_000_000 * SECOND)
        block = guidance.hold_block(released_ns=T0 + 7 * SECOND, started_ns=T0 + 9 * SECOND + 500 * MS, timed_out=False,
                                    timeout_seconds=600, clock=clock)
        self.assertEqual(block, {"requested": True, "released": True, "release_monotonic_ns": T0 + 7 * SECOND,
                                 "release_utc_ns": 1_795_000_007 * SECOND, "release_to_start_click_ms": 2500.0,
                                 "timed_out": False, "timeout_seconds": 600, "poll_interval_ms": 250,
                                 "release_file_read": False})
        never = guidance.hold_block(released_ns=None, started_ns=None, timed_out=True, timeout_seconds=600, clock=clock)
        self.assertEqual((never["released"], never["release_monotonic_ns"], never["release_utc_ns"],
                          never["release_to_start_click_ms"], never["timed_out"]), (False, None, None, None, True))
        cancelled = guidance.hold_block(released_ns=T0, started_ns=None, timed_out=False, timeout_seconds=600)
        self.assertEqual((cancelled["released"], cancelled["release_utc_ns"], cancelled["release_to_start_click_ms"]),
                         (True, None, None))
        arguments = dict(script="positions", started=False, ended_by="start_hold_timeout",
                         observations={"starting_state": None}, limits_ms={}, watch="run_not_started")
        plain, held = guidance.guide_block(None, **arguments), guidance.guide_block(None, **arguments, hold=never)
        self.assertNotIn("start_hold", plain)
        self.assertEqual((held["start_hold"], {name: held[name] for name in plain}, list(held)[-1]),
                         (never, plain, "start_hold"))
        self.assertEqual(held["schema_version"], 3)
        for path, leaf in l15_leaves(held["start_hold"], "guide.start_hold"):
            self.assertNotIsInstance(leaf, str, path)  # Booleans, numbers and nulls: no name, no path, no content.
        for name in l15_names(held["start_hold"]):
            self.assertFalse([part for part in FORBIDDEN_NAME_PARTS if part in name.lower()])
        self.assertEqual((guidance.HOLD_TIMEOUT_SECONDS, guidance.HOLD_POLL_MS, guidance.ENDED_HOLD_TIMEOUT,
                          guidance.HOLD_NOT_RELEASED), (600, 250, "start_hold_timeout", "start_hold_not_released"))
        self.assertIn(guidance.ENDED_HOLD_TIMEOUT, guidance.ENDED_BY)

    def test_an_unreleased_hold_is_put_into_words_as_nothing_launched(self):
        report = {"confirmation": None, "status": "error", "reason": guidance.HOLD_NOT_RELEASED}
        self.assertEqual(guidance.result_lines(report), [
            "Not started: the Frame manager did not say go in time. Nothing was launched. Tell the Frame manager."])

    def test_a_new_path_in_a_private_directory_or_beside_the_report_is_accepted_and_nothing_is_created(self):
        private, shared = self.place("private"), self.place("shared", 0o777)
        self.assertEqual(command.check_hold_path(private / "go", self.report), private / "go")
        # The report's own directory is accepted whatever its mode: it is where the run's files already go.
        self.assertEqual(command.check_hold_path(shared / "go", shared / "report.json"), shared / "go")
        self.assertEqual(command.check_hold_path(self.root / "go", self.report), self.root / "go")
        self.assertEqual((list(private.iterdir()), list(shared.iterdir())), ([], []))
        with mock.patch.dict(os.environ, {"HOME": str(private)}):
            self.assertEqual(command.check_hold_path("~/go", self.report), private / "go")

    def test_a_path_that_exists_a_link_and_a_directory_that_is_shared_or_unwritable_are_each_refused(self):
        private = self.place("private")
        (private / "there").write_text("left by an earlier run")
        (private / "to-a-file").symlink_to(private / "there")
        (private / "dangling").symlink_to(private / "nowhere")
        (private / "a-directory").mkdir()
        (self.root / "link-to-private").symlink_to(private, target_is_directory=True)
        refusals = {
            "exists": private / "there", "link_to_a_file": private / "to-a-file", "dangling_link": private / "dangling",
            "is_a_directory": private / "a-directory",
            "directory_is_a_link": self.root / "link-to-private" / "go",
            "group_writable_directory": self.place("group", 0o770) / "go",
            "world_writable_directory": self.place("world", 0o777) / "go",
            "sticky_shared_directory": self.place("sticky", 0o1777) / "go",
            "unwritable_directory": self.place("read-only", 0o500) / "go",
            "unsearchable_directory": self.place("no-search", 0o600) / "go",
            "missing_directory": self.root / "missing" / "go", "the_reports_own_path": self.report}
        for name, path in refusals.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "^--hold-start needs "):
                    command.check_hold_path(path, self.report)
        self.assertFalse((private / "nowhere").exists())
        self.assertEqual((private / "there").read_text(), "left by an earlier run")

    def test_a_directory_owned_by_someone_else_is_refused_unless_it_is_the_reports(self):
        private = self.place("private")
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(ValueError, "only you can write to"):
                command.check_hold_path(private / "go", self.report)
            self.assertEqual(command.check_hold_path(private / "go", private / "report.json"), private / "go")

    def test_the_release_is_a_regular_file_at_the_path_itself(self):
        private = self.place("private")
        path = private / "go"
        self.assertFalse(command.hold_released(path))
        path.touch()
        self.assertTrue(command.hold_released(path))
        (private / "link").symlink_to(path)
        (private / "directory").mkdir()
        self.assertEqual([command.hold_released(private / name) for name in ("link", "directory", "absent")],
                         [False, False, False])


class HoldCommandLineTests(unittest.TestCase):
    """``--hold-start`` on the command line: its path is settled before any window exists."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.helper = fake_camera_export.install(self.directory.name)
        self.report = self.root / "report.json"
        opened = mock.patch.object(command, "run_window", side_effect=AssertionError("a window was opened"))
        self.run_window = opened.start()
        self.addCleanup(opened.stop)
        created = mock.patch.object(command, "create_window", side_effect=AssertionError("a window was made"))
        created.start()
        self.addCleanup(created.stop)

    def arguments(self, *extra):
        return ["--allow-camera-input", "--helper", self.helper, "--report", self.report, "--script", "removal", *extra]

    def test_a_release_path_that_exists_a_link_and_an_unwritable_directory_are_refused_before_any_window(self):
        (self.root / "there").touch()
        (self.root / "link").symlink_to(self.root / "nowhere")
        read_only = self.root / "read-only"
        read_only.mkdir()
        read_only.chmod(0o500)
        self.addCleanup(read_only.chmod, 0o700)
        shared = self.root / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        for name, path in (("exists", self.root / "there"), ("link", self.root / "link"),
                           ("unwritable_directory", read_only / "go"), ("shared_directory", shared / "go"),
                           ("missing_directory", self.root / "missing" / "go"), ("the_report", self.report)):
            with self.subTest(name=name):
                code, report, _ = run_main(self.arguments("--hold-start", path))
                self.assertEqual((code, report["status"], report["reason"], report["confirmation"]),
                                 (1, "error", "hold_start_path_refused", None))
                self.assertTrue(report["error"].startswith("--hold-start needs "), report["error"])
                self.assertEqual((report["guide"]["ended_by"], report["guide"]["started_by_start_button"]),
                                 ("not_started", False))
                self.assertEqual((report["guide"]["start_hold"]["requested"], report["guide"]["start_hold"]["released"],
                                  report["guide"]["start_hold"]["timed_out"]), (True, False, False))
                self.assertNotIn("capture", report)
                self.assertNotIn(str(path), json.dumps(report))
                self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.run_window.assert_not_called()
        self.assertFalse(self.report.exists())

    def test_a_good_release_path_reaches_the_window_and_validate_does_not_take_one(self):
        seen = []

        def opened(config, windowed=False):
            seen.append(config)
            return command.unstarted_report(config, guidance.DECLINED)
        self.run_window.side_effect = opened
        import types
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), \
                mock.patch.dict(sys.modules, {"PySide6": sys.modules.get("PySide6") or types.ModuleType("PySide6")}):
            run_main(self.arguments("--hold-start", self.root / "go"))
            run_main(self.arguments())
        self.assertEqual([(config.hold_start, config.hold_timeout_seconds) for config in seen],
                         [(self.root / "go", 600), (None, 600)])
        self.assertFalse((self.root / "go").exists())
        self.assertEqual(run_main(["--validate", "--hold-start", self.root / "go"])[:2], (2, None))

    def test_a_refused_report_path_is_reported_first_and_without_the_option_a_refusal_has_no_hold_block(self):
        self.report.touch()
        code, report, _ = run_main(self.arguments("--hold-start", self.root / "go"))
        self.assertEqual((code, report["reason"], report["guide"]["start_hold"]["requested"]),
                         (1, "report_path_refused", True))
        code, report, _ = run_main(self.arguments())
        self.assertEqual((code, report["reason"]), (1, "report_path_refused"))
        self.assertNotIn("start_hold", report["guide"])


@unittest.skipUnless(PYSIDE and NUMPY_CV2 and sys.platform.startswith("linux") and shutil.which("g++"),
                     "Needs Linux, PySide6, NumPy, OpenCV and g++ for the helper's fake build")
class ValidateHoldTests(unittest.TestCase):
    """``--validate``'s start-hold flows, in their own block of the validation report."""

    def setUp(self):
        for number in RECORD_AND_POLL.numbers:
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def test_validate_holds_start_until_the_release_and_ends_an_unreleased_hold_unstarted(self):
        with tempfile.TemporaryDirectory() as directory:
            built = subprocess.run(["bash", str(ROOT / "scripts/build_camera_helper.sh"), "--fake", "--out", directory],
                                   cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            with mock.patch.dict(os.environ):
                code, report, _ = run_main(["--validate", "--helper", Path(directory) / "sightcircuit-camera-helper-fake"])
        self.assertEqual((code, report["status"], report["flows_failed"]), (0, "validated", []), report)
        flows = report["start_hold_flows"]
        self.assertEqual(list(flows), ["start_held_until_released", "start_hold_not_released", "hold_path_checked_first"])
        self.assertEqual({name: entry["ok"] for name, entry in flows.items()}, dict.fromkeys(flows, True))
        held, unreleased = flows["start_held_until_released"], flows["start_hold_not_released"]
        self.assertEqual((held["start_refused_while_held"], held["release_started_nothing"], held["released"],
                          held["timed_out"], held["release_file_read"], held["release_times_recorded"], held["status"],
                          held["workers_started"], held["run_screen_only_scripted"]),
                         (True, True, True, False, False, True, "recognized", 1, True))
        self.assertGreaterEqual(held["release_to_start_click_ms"], command.VALIDATION_GUARD_SECONDS * 1000)
        self.assertEqual((unreleased["status"], unreleased["reason"], unreleased["ended_by"], unreleased["workers_started"],
                          unreleased["confirmation"], unreleased["released"], unreleased["timed_out"]),
                         ("error", "start_hold_not_released", "start_hold_timeout", 0, None, False, True))
        self.assertEqual(flows["hold_path_checked_first"]["refused"],
                         ["exists", "link", "shared_directory", "unwritable_directory", "missing_directory", "the_report"])


if __name__ == "__main__":
    unittest.main()
