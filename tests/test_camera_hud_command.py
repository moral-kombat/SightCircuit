"""``camera_hud``, the fifteenth command (work-order item L9): its three modes, its refusals and its report.

Cloud checks only. The camera is the stand-in producer (``tests/fake_camera_export.py``) or the helper's fake
backend; the display is the validation bridge, which shows nothing, or the bridge's ``--fixed-hud`` mode against
the fake OpenVR runtime that logs its calls. A device mode is reached here only through a test fixture record:
a temporary checkout with its own manifest and ledger (``tests/test_scope_record.py``), whose pinned bridge is
a copy of the bridge built here and whose pinned helper is the stand-in. Nothing in these tests is SteamVR, the
SDK, a headset or a device, and no gate is open before, during or after.
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from sightcircuit import gates
from sightcircuit.camera import confirmation, contract
from sightcircuit.commands import COMMANDS
from sightcircuit.overlay import DEFAULT_BRIDGE
from sightcircuit.runtime import manifest, scope
from sightcircuit.runtime.supervision import RECORD_AND_POLL
from sightcircuit.sources import camera_helper
from tests import fake_camera_export, fake_vrclient
from tests import test_scope_record as scope_tests

ROOT = Path(__file__).resolve().parents[1]
NUMPY_CV2 = bool(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"))
RUNNABLE = sys.platform.startswith("linux") and NUMPY_CV2 and DEFAULT_BRIDGE.is_file()
NEEDS = "Needs Linux, NumPy, OpenCV and scripts/build_overlay_bridge.sh"
if NUMPY_CV2:
    from sightcircuit.commands import camera_hud
    from sightcircuit.hud import camera_stage, synthetic_rows
ALL_CLOSED = scope_tests.ALL_CLOSED
LIMITS = {"fps": 5, "max_seconds": 3, "max_frames": 15}
MS = 1_000_000
HOST_MARGIN_MS = 150  # This host's scheduling of the bridge; never a claim about the bridge or a headset.


class Terminal:
    """A stand-in for the operator's terminal: interactive, and it types ``answer``."""

    def __init__(self, answer="yes\n"):
        self.answer, self.asked = answer, 0

    def isatty(self):
        return True

    def readline(self):
        self.asked += 1
        return self.answer

    def fileno(self):
        raise OSError("not a descriptor")


def run_main(arguments, stdin=None):
    """``main(arguments)`` with the terminal replaced: ``(exit code, report, what the terminal showed)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(sys, "stdin", stdin if stdin is not None else io.StringIO("")):
        try:
            code = camera_hud.main([str(item) for item in arguments])
        except SystemExit as error:
            return error.code, None, err.getvalue()
    return code, json.loads(out.getvalue()), err.getvalue()


class BoundaryTests(unittest.TestCase):
    """What needs no dependency: the command's place among the commands, and what it may import."""

    def test_it_is_the_fifteenth_command_with_a_wrapper_an_entry_point_and_a_manifest_pin(self):
        import tomllib
        # Sixteen commands since ``camera_guide`` (2026-11-19) took the place after it; this one is still the fifteenth.
        self.assertEqual((len(COMMANDS), COMMANDS[14], COMMANDS[13]), (16, "camera_hud", "camera_recognize"))
        entry_points = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertEqual(entry_points["sightcircuit-camera-hud"], "sightcircuit.commands.camera_hud:main")
        self.assertEqual(len(entry_points), 16)
        self.assertTrue((ROOT / "scripts/camera_hud.py").is_file())
        # Its own manifest group: the wrapper beside the helper it launches; the body is in ``modules``.
        self.assertIn("scripts/camera_hud.py", manifest.CAMERA_HUD_FILES)
        self.assertEqual(manifest.CAMERA_HUD_SCRIPTS, ("scripts/build_camera_helper.sh",
                                                       "scripts/build_presence_watcher.sh", "scripts/camera_hud.py",
                                                       "scripts/stage_scope_record.py"))
        self.assertNotIn("scripts/camera_hud.py", manifest.CHECKPOINT_SCRIPTS)
        current, _ = manifest.observe(ROOT, camera_hud=True)  # The group is optional; a scope record requires it.
        self.assertIn("sightcircuit/commands/camera_hud.py", current["modules"])
        self.assertIn("sightcircuit/runtime/scope.py", current["modules"])
        self.assertEqual(sorted(current["camera_hud"]), sorted(manifest.CAMERA_HUD_FILES))

    def test_neither_camera_command_imports_a_command_and_this_one_never_reaches_the_replay_worker_or_the_live_runner(self):
        for name in ("camera_hud", "camera_recognize"):
            tree = ast.parse((ROOT / "sightcircuit/commands" / f"{name}.py").read_text())
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            imported |= {f"{node.module}.{alias.name}" for node in ast.walk(tree)
                         if isinstance(node, ast.ImportFrom) and node.module for alias in node.names}
            imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            with self.subTest(command=name):
                self.assertEqual({item for item in imported if item.startswith(("sightcircuit.commands", "scripts"))},
                                 set())
            if name == "camera_hud":
                for forbidden in ("sightcircuit.runtime.live", "sightcircuit.runtime.synthetic", "sightcircuit.sources.mirror",
                                  "sightcircuit.sources.replay", "sightcircuit.sources.polled"):
                    self.assertFalse([item for item in imported if item.startswith(forbidden)], forbidden)
                used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                self.assertEqual(used & {"ReplayWorker", "run_pipeline", "run_recognition", "require_synthetic",
                                         "write_record", "getenv", "MirrorSource"}, set())
                self.assertEqual({node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                                  and node.name.startswith("confirm")}, set())
        # The informed start is shared, not copied: both camera commands take it from one module.
        recognize = (ROOT / "sightcircuit/commands/camera_recognize.py").read_text()
        self.assertIn("from sightcircuit.camera.confirmation import", recognize)
        self.assertNotIn("def confirm_on_terminal", recognize)

    def test_the_informed_start_prints_the_notices_and_takes_only_the_typed_yes(self):
        limits = {"fps": 5, "max_seconds": 30, "max_frames": 150}
        shown = io.StringIO()
        self.assertIsNone(confirmation.confirm_on_terminal(limits, (0,), stdin=Terminal(), output=shown,
                                                           notices=(confirmation.DISPLAY_NOTICE,)))
        self.assertEqual(shown.getvalue(), contract.EXPORT_NOTICE + "\n" + confirmation.DISPLAY_NOTICE + "\n" +
                         "Bounds: 150 frames, 30 seconds, 5 frames a second, cameras 0.\nType yes to continue: ")
        for notice in (confirmation.DISPLAY_NOTICE, confirmation.BOX_DISPLAY_NOTICE, confirmation.SYNTHETIC_NOTICE):
            self.assertIn("it is not an approval and creates no scope record", notice)
            self.assertIn("Ctrl-C", notice)
            self.assertIn("green input", notice)
        # The box panel's notice, as the owner accepted it on 2026-11-25 (owner decision C of item B5), sentence
        # for sentence: a change to any of them is a change the owner is asked about.
        self.assertEqual([sentence.strip() for sentence in confirmation.BOX_DISPLAY_NOTICE.split(". ")], [
            "camera_hud: draws a box with a name around each detected object, on a transparent panel in the middle "
            "of the view in the headset, for the bounds shown below",
            "A box sits on its object only while the head is still; it trails when the head turns",
            "A box is taken down about one second after the camera frame it came from unless a newer frame shows "
            "it again",
            "The panel shows no camera image",
            "The command saves no image; its report holds counts, labels, timings and format metadata, printed on "
            "this terminal and written to a file only if you named --report",
            "Stop with Ctrl-C at any passthrough change, any green input, or when the wearer asks",
            "Typing yes starts this one run; it is not an approval and creates no scope record."])
        shown = io.StringIO()
        self.assertIsNone(confirmation.confirm_on_terminal(limits, (0,), stdin=Terminal(), output=shown,
                                                           notices=(confirmation.BOX_DISPLAY_NOTICE,)))
        self.assertEqual(shown.getvalue(), contract.EXPORT_NOTICE + "\n" + confirmation.BOX_DISPLAY_NOTICE + "\n" +
                         "Bounds: 150 frames, 30 seconds, 5 frames a second, cameras 0.\nType yes to continue: ")
        # The helper's notice is true of a display command that is given --report (revised with this command).
        self.assertIn("a display command shows labels and approximate arrows in the headset and saves only the report "
                      "you name (counts, labels, boxes and timings)", contract.EXPORT_NOTICE)
        self.assertNotIn("saves nothing", contract.EXPORT_NOTICE)
        self.assertEqual(contract.VERSION, 2)  # The layout is unchanged; only the digest moved.
        self.assertIn("No camera is opened", confirmation.SYNTHETIC_NOTICE)
        self.assertIn("shows no camera image", confirmation.DISPLAY_NOTICE)
        # The recognition command's start is byte for byte what it was: the export notice and the bounds.
        shown = io.StringIO()
        self.assertIsNone(confirmation.confirm_on_terminal(limits, (0, 1), stdin=Terminal(), output=shown))
        self.assertEqual(shown.getvalue(), contract.EXPORT_NOTICE + "\nBounds: 150 frames, 30 seconds, 5 frames a "
                         "second, cameras 0,1.\nType yes to continue: ")
        shown = io.StringIO()
        self.assertIsNone(confirmation.confirm_synthetic_rows(90, 5, stdin=Terminal(), output=shown))
        self.assertEqual(shown.getvalue(), confirmation.SYNTHETIC_NOTICE + "\nBounds: 90 seconds, 5 updates a second, "
                         "no camera.\nType yes to continue: ")
        self.assertNotIn(contract.EXPORT_NOTICE, shown.getvalue())
        for answer, refusal in (("no\n", "confirmation_declined"), ("y\n", "confirmation_declined"),
                                ("YES\n", "confirmation_declined"), ("yes please\n", "confirmation_declined"),
                                ("\n", "confirmation_declined"), ("", "confirmation_unavailable")):
            with self.subTest(answer=answer):
                self.assertEqual(confirmation.confirm_synthetic_rows(90, 5, stdin=Terminal(answer), output=io.StringIO()),
                                 refusal)
        shown = io.StringIO()
        self.assertEqual(confirmation.confirm_synthetic_rows(90, 5, stdin=io.StringIO("yes\n"), output=shown),
                         "confirmation_requires_terminal")
        self.assertEqual(shown.getvalue(), "")  # Nothing is printed to a pipe.


@unittest.skipUnless(RUNNABLE, NEEDS)
class ScheduleTests(unittest.TestCase):
    def test_the_schedule_fills_what_the_stops_leave_and_refuses_what_it_cannot_serve(self):
        plan = synthetic_rows.schedule(90, 3, 3.0)
        self.assertEqual(plan, [("rows", 102), ("stop", 3.0), ("rows", 101), ("stop", 3.0), ("rows", 101),
                                ("stop", 3.0), ("rows", 101)])
        self.assertEqual(sum(value for kind, value in plan if kind == "rows"), 405)  # L-D3 asks for at least 375.
        self.assertGreaterEqual(405, 375)
        self.assertEqual(synthetic_rows.schedule(5, 0, 3.0), [("rows", 25)])
        self.assertEqual((synthetic_rows.UPDATE_HZ, synthetic_rows.MAX_SECONDS, synthetic_rows.EXPIRY_BOUND_MS),
                         (5, scope.STEPS["L-D3"][1], 1020.0))
        for arguments, reason in (((0, 0, 3.0), "seconds"), ((121, 3, 3.0), "seconds"), ((90.0, 3, 3.0), "seconds"),
                                  ((90, -1, 3.0), "stops"), ((90, 9, 3.0), "stops"), ((90, 3, 1.9), "stop_seconds"),
                                  ((90, 3, 10.5), "stop_seconds"), ((90, 3, "3"), "stop_seconds")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, f"synthetic_rows_{reason}_out_of_range"):
                synthetic_rows.schedule(*arguments)
        for arguments in ((6, 3, 2.0), (3, 1, 2.0), (10, 8, 2.0)):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, "synthetic_rows_schedule_too_short"):
                synthetic_rows.schedule(*arguments)
        self.assertEqual(synthetic_rows.counter_label(7), "count 0007")


@unittest.skipUnless(RUNNABLE, NEEDS)
class ArgumentTests(unittest.TestCase):
    """The parser refuses a device mode that lacks its opt-ins, its record or its explicit choices."""
    DEVICE = ("--allow-hud-output", "--scope-record", "record.json", "--manifest", "manifest.json", "--seconds", "20")
    CAMERA = ("--camera", "--allow-camera-input", *DEVICE, "--names-only", "--orientation", "identity", "--step", "L-D4")

    def test_argument_refusals(self):
        cases = (
            ((), "one of the arguments --validate --synthetic-rows --camera is required"),
            (("--validate", "--camera"), "not allowed with"),
            (("--validate", "--seconds", "5"), "--validate takes no run arguments"),
            (("--validate", "--allow-hud-output"), "--validate takes no run arguments"),
            (("--validate", "--names-only"), "--validate takes no run arguments"),
            (("--synthetic-rows", *self.DEVICE[1:]), "--allow-hud-output is required"),
            (("--synthetic-rows", "--allow-hud-output", "--seconds", "20"), "--scope-record, --manifest and --seconds"),
            (("--synthetic-rows", *self.DEVICE[:5]), "--scope-record, --manifest and --seconds"),
            (("--synthetic-rows", *self.DEVICE, "--allow-camera-input"), "never opens the camera"),
            (("--synthetic-rows", *self.DEVICE, "--target-label", "bottle"), "never opens the camera"),
            (("--synthetic-rows", *self.DEVICE[:-1], "121"), "--seconds must be 1..110 for --synthetic-rows"),
            (("--synthetic-rows", *self.DEVICE[:-1], "111"), "--seconds must be 1..110 for --synthetic-rows"),
            (("--synthetic-rows", *self.DEVICE, "--fps", "5"), "never opens the camera"),
            (("--synthetic-rows", *self.DEVICE, "--detector", "mobilenet-ssd"), "never opens the camera"),
            (("--synthetic-rows", *self.DEVICE, "--threshold", "0.5"), "never opens the camera"),
            ((*self.CAMERA, "--stops", "2"), "--stops and --stop-seconds belong to --synthetic-rows"),
            ((*self.CAMERA, "--stop-seconds", "3"), "--stops and --stop-seconds belong to --synthetic-rows"),
            (("--validate", "--stops", "1"), "--validate takes no run arguments"),
            ((*self.CAMERA, "--report", "/nonexistent-directory/run.json"), "is not an existing directory"),
            ((*self.CAMERA, "--report", "run.txt"), "--report needs a .json path"),
            ((*self.CAMERA, "--report", str(ROOT / "evaluation/seed.json")),
             "already exists; a report is never overwritten"),
            (("--synthetic-rows", *self.DEVICE[:-1], "8"), "synthetic_rows_schedule_too_short"),
            (("--synthetic-rows", *self.DEVICE, "--helper", "x"), "--helper is for --validate"),
            (("--camera", *self.DEVICE, "--names-only", "--orientation", "identity"), "--allow-camera-input is required"),
            (("--camera", "--allow-camera-input", *self.DEVICE[1:]), "--allow-hud-output is required"),
            (("--camera", "--allow-camera-input", *self.DEVICE, "--names-only", "--orientation", "identity"),
             "--camera needs --step (L-D4 or L-D5)"),
            ((*self.CAMERA[:-1], "L-D3"), "invalid choice"),
            (("--synthetic-rows", *self.DEVICE, "--step", "L-D4"), "never opens the camera"),
            (("--camera", "--allow-camera-input", *self.DEVICE, "--step", "L-D4"), "neither has a default"),
            (("--camera", "--allow-camera-input", *self.DEVICE, "--step", "L-D4", "--names-only"), "neither has a default"),
            (("--camera", "--allow-camera-input", *self.DEVICE, "--step", "L-D4", "--orientation", "identity"),
             "neither has a default"),
            ((*self.CAMERA, "--directions"), "not allowed with"),
            ((*self.CAMERA[:-3], "sideways", "--step", "L-D4"), "invalid choice"),
            ((*self.CAMERA[:8], "91", *self.CAMERA[9:]), "--seconds must be 1..90"),
            ((*self.CAMERA, "--target-label", "unicorn"), "--target-label must be a class"),
            ((*self.CAMERA, "--cue", "5:turn_away"), ""),
            ((*self.CAMERA, "--helper", "/tmp/helper"), "--helper is for --validate"),
            ((*self.CAMERA, "--bridge", "/tmp/bridge"), "unrecognized arguments"),
            ((*self.CAMERA, "--cameras", "0,1"), "unrecognized arguments"),
            ((*self.CAMERA, "--threshold", "2"), "--threshold must be between zero and one"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments[:4], message=message), \
                    mock.patch.object(camera_hud, "run_camera") as camera, \
                    mock.patch.object(camera_hud, "run_synthetic_rows") as rows, \
                    mock.patch.object(camera_hud, "validate") as validate:
                code, report, shown = run_main(arguments, Terminal())
                self.assertEqual((code, report), (2, None))
                self.assertIn(message, shown)
                for called in (camera, rows, validate):
                    called.assert_not_called()

    def test_the_command_has_no_option_that_names_another_binary_camera_or_source_for_a_device_mode(self):
        options = {option for action in camera_hud.build_parser()._actions for option in action.option_strings}
        self.assertEqual(options & {"--bridge", "--cameras", "--helper-arg", "--source", "--device", "--mirror",
                                    "--approve", "--yes", "--force", "--no-confirm", "--run-id"}, set())
        self.assertLessEqual({"--validate", "--synthetic-rows", "--camera", "--allow-hud-output", "--allow-camera-input",
                              "--scope-record", "--manifest", "--seconds", "--step", "--directions", "--names-only",
                              "--orientation", "--target-label", "--cue", "--key-marks", "--mark-labels", "--report"},
                             options)
        self.assertEqual(camera_hud.G10_WINDOW, {"window_seconds": 60.0, "fraction": 0.9, "max_gap_seconds": 2.0})
        self.assertEqual((camera_hud.MAX_SECONDS, camera_hud.RUNNER), (90, "camera_hud"))


@unittest.skipUnless(RUNNABLE, NEEDS)
class GuideAndTemplateTests(unittest.TestCase):
    """The guide's commands parse with the real parsers, and the device report template follows the reports."""
    GUIDE = ROOT / "docs/guides/camera-hud.md"
    TEMPLATE = ROOT / "docs/templates/camera-hud-device-report.json"

    def commands(self, script):
        import re
        import shlex
        blocks = re.findall(r"```bash\n(.*?)```", self.GUIDE.read_text(), re.S)
        found = []
        for line in "\n".join(blocks).replace("\\\n", " ").splitlines():
            if script in line:
                tokens = shlex.split(line.rstrip("&").strip())
                found.append(tokens[tokens.index(script) + 1:])
        return found

    def test_every_documented_command_parses_and_names_its_opt_ins_record_and_manifest(self):
        from scripts import checkpoint_manifest, stage_scope_record
        commands = self.commands("scripts/camera_hud.py")
        self.assertEqual(len(commands), 4)  # Validate, L-D3, L-D4 and L-D5.
        modes = []
        for arguments in commands:
            with self.subTest(arguments=arguments[:1]), contextlib.redirect_stderr(io.StringIO()):
                args = camera_hud.build_parser().parse_args(arguments)
                modes.append("validate" if args.validate else "rows" if args.synthetic_rows else "camera")
                if not args.validate:
                    self.assertTrue(args.allow_hud_output and args.scope_record and args.manifest and args.report)
                    step = args.scope_record.stem.removeprefix("scope-")
                    allowance = camera_hud.SYNTHETIC_ALLOWANCE_SECONDS if args.synthetic_rows else 0
                    self.assertLessEqual(args.seconds + allowance, scope.STEPS[step][1])  # Within the record's bound.
                    self.assertEqual(args.step, None if args.synthetic_rows else step)
                if args.camera:
                    self.assertTrue(args.allow_camera_input and args.orientation and args.directions is False)
                    self.assertEqual(args.target_label, "bottle")
                    from sightcircuit.camera import marks
                    marks.check_cues(marks.parse_cue(text, args.seconds) for text in args.cue)
                    marks.check_mark_labels(item for item in args.mark_labels.split(",") if args.mark_labels)
                if args.synthetic_rows:
                    plan = synthetic_rows.schedule(args.seconds, args.stops, args.stop_seconds)
                    self.assertGreaterEqual(sum(value for kind, value in plan if kind == "rows"), 375)  # Row G9.
        self.assertEqual(modes, ["validate", "rows", "camera", "camera"])
        # The L-D5 command can pass row G10: under an ideal run (the label up from the bridge's ready until the
        # out_of_view cue, down for the cued pause, up again to the end) a contiguous 60 s window qualifies, even
        # when the bridge takes its whole ready wait and the client's bound ends the run that much early.
        from sightcircuit.camera import marks as camera_marks
        from sightcircuit.hud.contract import BRIDGE_READY_WAIT_SECONDS
        final = camera_hud.build_parser().parse_args(commands[-1])
        cued = dict((label, offset_ms / 1000) for offset_ms, label in
                    (camera_marks.parse_cue(text, final.seconds) for text in final.cue))
        self.assertEqual((final.step, final.seconds, sorted(cued)), ("L-D5", 90, ["in_view", "out_of_view"]))
        for late in (0.0, float(BRIDGE_READY_WAIT_SECONDS)):  # The cues count from the first frame.
            ideal = [(late, late + cued["out_of_view"]), (late + cued["in_view"], float(final.seconds))]
            verdict = camera_stage.window_verdict(ideal, float(final.seconds), **camera_hud.G10_WINDOW)
            with self.subTest(bridge_ready_after=late):
                self.assertTrue(verdict["found"], verdict)
                self.assertGreaterEqual(cued["out_of_view"], 60 + 5)  # The pause comes after the window, with margin.
        earlier = camera_stage.window_verdict([(0.0, 40.0), (45.0, 90.0)], 90.0, **camera_hud.G10_WINDOW)
        self.assertFalse(earlier["found"])  # The schedule this replaced: a pause in the middle leaves no window.
        staged = self.commands("scripts/stage_scope_record.py")
        self.assertEqual(len(staged), 2)  # L-D3's, and a camera step's in full (it needs --presence-watch).
        args = stage_scope_record.build_parser().parse_args(staged[0])
        self.assertEqual((args.step, args.valid_minutes, args.layout), ("L-D3", 60, "fixed-edge-hud-v1"))
        # The synthetic step's record names no view: the camera is never opened.
        self.assertEqual((args.region, args.rotation, args.alignment, args.presence_watch), (None, None, None, None))
        args = stage_scope_record.build_parser().parse_args(staged[1])
        self.assertEqual((args.step, args.layout, args.presence_watch, args.region, args.rotation, args.alignment),
                         ("L-D4", "fixed-edge-hud-v1", "none", None, None, None))
        # What the guide's camera command stages is a record today's command runs under: the strip, the whole
        # frame, no watch (the view ``stage_view_bound`` serves).
        view, refusal = scope.view_value(scope.CAMERA_LEFT, camera_stage.HUD_CONFIG, args.region, args.rotation,
                                         args.alignment, stage_scope_record.PRESENCE_WATCH[args.presence_watch])
        self.assertEqual((view, refusal), (camera_stage.STAGE_VIEW, None))
        written = []
        for arguments in self.commands("scripts/checkpoint_manifest.py"):
            parsed = checkpoint_manifest.build_parser().parse_args(arguments)
            written += [parsed.camera_hud] if parsed.write is not None else []
        self.assertEqual(written, [True])  # The manifest is written with the camera HUD's group asked for.
        text = " ".join(self.GUIDE.read_text().split())
        for sentence in ("This guide is a procedure, not an approval.", "Nothing here has run on the Frame.",
                         "not a cryptographic one", "No screen showing the Frame's desktop, the replay window or the "
                         "SteamVR mirror may be in the cameras' view", "no software path in this checkout touches it",
                         "white service LED", "the 30 seconds count from then", "G2 recovery ready",
                         "It is not an approval, it creates no record and it opens nothing"):
            self.assertIn(sentence, text)
        self.assertIn("(guides/camera-hud.md)", (ROOT / "docs/on-device.md").read_text())

    def test_the_device_report_template_follows_the_reports_and_claims_nothing(self):
        template = json.loads(self.TEMPLATE.read_text())
        self.assertEqual((template["report_kind"], template["template_only"], template["status"]),
                         ("camera_hud_device_report_worksheet", True, "not_run"))
        self.assertEqual({name: template[name] for name in gates.claims()}, ALL_CLOSED)
        blocks = camera_hud.CAMERA_BLOCKS
        for name in ("config", "frames", "deadlines", "frame_expired", "end", "cleanup", "scope_record", "command", "target",
                     "view", "boxes", "steps_ms"):
            with self.subTest(block=name):
                self.assertLessEqual(set(template[name]), set(blocks[name]))
        # The record's bound fields and the box layout's blocks are on the worksheet (work-order item B5): the
        # layout, the region and rotation, the alignment's five numbers and the watch, and the boxes' counts.
        self.assertLessEqual({"requested_layout", "hud_config_id", "hud_config_sha256", "region", "rotation", "alignment",
                              "presence_watch"}, set(template["scope_record"]))
        self.assertEqual(set(template["scope_record"]), set(camera_hud.SCOPE_KEYS) - {"requested_runner", "requested_step",
                                                                                     "requested_source"})
        from sightcircuit.hud import boxes as box_selection
        self.assertEqual((set(template["boxes"]["dropped"]), set(template["config"]["alignment"]),
                          set(template["scope_record"]["alignment"])),
                         (set(box_selection.DROP_REASONS), set(box_selection.ALIGNMENT_FIELDS),
                          set(box_selection.ALIGNMENT_FIELDS)))
        self.assertEqual(set(template["steps_ms"]) - {"unit", "clock"}, set(camera_stage.STEPS))
        self.assertLessEqual(set(template["timings"]) - {"timebase"}, set(blocks["timings"]))
        self.assertLessEqual(set(template["bridge"]) - {"final_report"}, set(blocks["bridge"]))
        self.assertLessEqual(set(template["helper"]["ready"]), set(camera_helper.READY_FIELDS))
        self.assertLessEqual(set(template["helper"]["end"]), set(camera_helper.END_FIELDS))
        self.assertLessEqual(set(template["labels"]), set(blocks["labels"]))
        self.assertLessEqual(set(template["marks"]), set(blocks["marks"]))
        self.assertEqual((template["mask_regions"], template["mapping"]), ("none_on_the_camera_route",) * 2)
        self.assertEqual(template["code_properties"], {"red_led_touched_by_software": False, "camera_controls_written": False,
                                                       "images_saved": False, "pixels_in_report": False,
                                                       "calibration_values_recorded": False})
        observations = {**template["wearer_observations"], **template["operator_observations"]}
        self.assertTrue(all(value is None for value in observations.values()))  # People fill these in.
        for field in ("tile_blank_at_each_stop", "tile_cleared_on_headset_removal", "passthrough_unchanged_for_30_s_after",
                      "green_input", "white_service_led_seen", "no_screen_showing_the_frames_output_in_the_cameras_view",
                      "g2_recovery_procedure_ready", "name_gone_within_about_two_seconds_of_each_turn_away"):
            self.assertIn(field, observations)
        text = self.TEMPLATE.read_text()
        for word in ("pixels\":", "intrinsic", "calibration_digest", "still", "preview"):
            self.assertNotIn(word, text)


@unittest.skipUnless(RUNNABLE and shutil.which("g++"), NEEDS + " and g++ for the helper's fake build")
class ValidateTests(unittest.TestCase):
    """``--validate``: the helper's fake backend, a scripted detector and the validation bridge; nothing displayed."""

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

    def test_validate_runs_the_whole_path_with_nothing_real_in_it(self):
        before = os.environ.get("XDG_RUNTIME_DIR")
        code, report, _ = run_main(["--validate", "--helper", self.helper])
        self.assertEqual(os.environ.get("XDG_RUNTIME_DIR"), before)
        self.assertEqual((code, report["report_kind"], report["status"], report["reason"]),
                         (0, "camera_hud_validation", "validated", None))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertEqual((report["validation_only"], report["camera_opened"], report["images_saved"],
                          report["pixels_in_report"]), (True, False, False, False))
        self.assertEqual(report["helper"], {"backend": "fake", "fake": True, "build_dirty": report["helper"]["build_dirty"],
                                            "sdk_release": None, "contract": "camera-helper/1"})
        camera, rows = report["camera"], report["synthetic_rows"]
        self.assertEqual((camera["status"], camera["bridge_mode"], camera["frames_emitted"], camera["ok"],
                          camera["source_cleanup_ok"], camera["bridge_cleanup_ok"]),
                         ("completed", "--validate-stdin", 6, True, True, True))
        self.assertEqual(camera["acknowledged_presented"], camera["frames_sent"])
        self.assertGreater(camera["frames_sent"], 0)
        self.assertEqual(camera["labels"], [{"label": "bottle", "spells": camera["labels"][0]["spells"]}])
        self.assertEqual((rows["status"], rows["bridge_mode"], rows["requested"], rows["acknowledged_presented"],
                          rows["stops_reached"], rows["stops_expired"], rows["ok"]),
                         ("completed", "--validate-stdin", 20, 20, 1, 1, True))
        self.assertEqual((report["command"]["mode"], report["command"]["exit_code"], report["command"]["confirmation"]),
                         ("validate", 0, None))
        camera_stage.check_report(report, camera_hud.VALIDATION_BLOCKS, camera_hud.MAX_REPORT_BYTES)
        # The same path for the box panel (work-order item B5): the fake helper's frames through a region turned
        # a quarter turn, a megabyte per update on the validation bridge, each one acknowledged.
        panel = report["camera_panel"]
        self.assertEqual((panel["status"], panel["bridge_mode"], panel["layout_id"], panel["update_bytes"],
                          panel["region_rotation"], panel["frames_emitted"], panel["ok"], panel["source_cleanup_ok"],
                          panel["bridge_cleanup_ok"]),
                         ("completed", "--validate-stdin", "head-fixed-box-panel-v1", 1_048_576, 90, 6, True, True, True))
        self.assertEqual(panel["acknowledged_presented"], panel["frames_sent"])
        # One box in every result the client accepted. (A result accepted as the run ends can be withdrawn
        # unwritten by the final take-down, so that count is at least, not exactly, the frames written.)
        self.assertEqual(panel["boxes_sent"], panel["results_sent"])
        self.assertGreaterEqual(panel["results_sent"], panel["frames_sent"])
        self.assertGreater(panel["frames_sent"], 0)
        self.assertEqual(panel["labels"], [{"label": "bottle", "spells": panel["labels"][0]["spells"]}])
        self.assertEqual(report["schema_version"], 2)  # 2 since the panel's part.

    def test_validate_refuses_any_helper_that_does_not_describe_itself_as_the_fake(self):
        os.makedirs(self.directory.name + "/stand-in", exist_ok=True)
        stand_in = fake_camera_export.install(self.directory.name + "/stand-in")
        sdk_like = Path(self.directory.name) / "sdk-like"
        sdk_like.write_text("#!/bin/sh\necho '{\"event\":\"describe\",\"backend\":\"sdk\",\"sdk_release\":{\"x\":1}}'\n")
        liar = Path(self.directory.name) / "fake-with-a-release"
        liar.write_text("#!/bin/sh\necho '{\"event\":\"describe\",\"backend\":\"fake\",\"sdk_release\":{\"x\":1}}'\n")
        for path in (sdk_like, liar):
            path.chmod(0o755)
        for helper in (sdk_like, liar, stand_in, Path(sys.executable), Path(self.directory.name) / "absent"):
            with self.subTest(helper=Path(helper).name), \
                    mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")) as exported, \
                    mock.patch.object(camera_hud, "run_camera") as camera, \
                    mock.patch.object(camera_hud, "run_synthetic_rows") as rows:
                report = camera_hud.validate(helper)
                self.assertEqual((report["status"], report["reason"], report["command"]["exit_code"],
                                  report["helper"]["fake"], report["camera"], report["camera_panel"],
                                  report["synthetic_rows"]),
                                 ("validation_failed", "validate_requires_fake_helper", 1, False, None, None, None))
                for called in (exported, camera, rows):
                    called.assert_not_called()

    def test_validate_reports_a_missing_bridge_and_a_failed_part(self):
        report = camera_hud.validate(self.helper, bridge=Path(self.directory.name) / "no-bridge")
        self.assertEqual((report["status"], report["reason"]), ("validation_failed", "overlay_bridge_missing"))
        with mock.patch.object(camera_hud, "VALIDATION_HELPER_ARGS", ("--fake-scenario", "no_such_scenario")):
            report = camera_hud.validate(self.helper)
        self.assertEqual((report["status"], report["reason"], report["camera"]["ok"], report["command"]["exit_code"]),
                         ("validation_failed", "camera_path_failed", False, 1))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)

    def test_validate_fails_its_panel_part_by_name_when_the_boxes_block_is_missing_or_says_nothing_was_sent(self):
        # The panel's pass is judged on its boxes block: without the block, or with one that counts nothing
        # sent, the validation is not ``validated``, whatever else the pass reports. The strip's pass and the
        # synthetic rows are untouched, so the reason is the panel's own.
        run_camera = camera_hud.run_camera

        def spoiled(change):
            def run(*arguments, **options):
                report = run_camera(*arguments, **options)
                if options.get("layout") is not None:  # The panel's pass.
                    change(report)
                return report
            return run

        def emptied(report):
            report["boxes"].update(results_sent=0, boxes_sent=0)

        def unsent(report):
            report["boxes"].update(boxes_sent=report["boxes"]["boxes_sent"] - 1)
        for name, change in (("no block", lambda report: report.update(boxes=None)),
                             ("no block at all", lambda report: report.pop("boxes")),
                             ("nothing sent", emptied), ("a box short", unsent)):
            with self.subTest(spoiled=name), mock.patch.object(camera_hud, "run_camera", spoiled(change)):
                report = camera_hud.validate(self.helper)
            with self.subTest(spoiled=name):
                self.assertEqual((report["status"], report["reason"], report["command"]["exit_code"]),
                                 ("validation_failed", "camera_panel_path_failed", 1))
                self.assertEqual((report["camera"]["ok"], report["camera_panel"]["ok"], report["synthetic_rows"]["ok"]),
                                 (True, False, True))
                self.assertEqual(report["camera_panel"]["status"], "completed")  # The pass ran; its count failed it.
                camera_stage.check_report(report, camera_hud.VALIDATION_BLOCKS, camera_hud.MAX_REPORT_BYTES)


@unittest.skipUnless(RUNNABLE, NEEDS)
class DeviceModeCase(scope_tests.ScopeCase):
    """A fixture checkout whose pinned bridge is a copy of the bridge built here and whose pinned helper is the
    camera stand-in; ``scope.ROOT`` points at it, so the command's pinned paths are the fixture's."""

    def install_binaries(self):
        shutil.copy2(DEFAULT_BRIDGE, self.bridge)
        self.helper.write_text(fake_camera_export.SHEBANG_INSTALL.format(python=sys.executable, root=str(ROOT)))
        super().install_binaries()

    def setUp(self):
        super().setUp()
        for number in (*RECORD_AND_POLL.numbers, signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, number, signal.getsignal(number))
        self.assertEqual((camera_hud.pinned(manifest.BRIDGE), camera_hud.pinned(manifest.CAMERA_HELPER)),
                         (self.bridge, self.helper))
        # The helper's lock directory, as the helper requires it: a device run checks it before the record.
        self.runtime_directory = self.out / "xdg-runtime"
        self.runtime_directory.mkdir(mode=0o700)
        self.runtime_directory.chmod(0o700)
        environment = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime_directory)})
        environment.start()
        self.addCleanup(environment.stop)

    @contextlib.contextmanager
    def terminal_signal(self, after, number=signal.SIGINT, repeat=1, gap=0.06):
        """What a terminal's Ctrl-C does, ``after`` seconds from now: the signal goes to this process and to the
        bridge, which shares the foreground process group (the helper is supervised in a group of its own).
        The whole group is not signalled here, since the test runner's parents share it. ``repeat`` sends it
        again every ``gap`` seconds."""
        import threading
        real, bridges = subprocess.Popen, []

        def launch(arguments, *more, **options):
            process = real(arguments, *more, **options)
            if "--fixed-hud" in list(map(str, arguments)):
                bridges.append(process)
            return process

        def send():
            for index in range(repeat):
                for process in bridges:
                    with contextlib.suppress(ProcessLookupError):
                        process.send_signal(number)
                os.kill(os.getpid(), number)
                if index + 1 < repeat:
                    time.sleep(gap)
        timer = threading.Timer(after, send)
        with mock.patch.object(subprocess, "Popen", side_effect=launch):
            timer.start()
            try:
                yield bridges
            finally:
                timer.cancel()
                timer.join()

    def camera_scope(self, path, seconds=LIMITS["max_seconds"], step="L-D4"):
        return scope.RunScope(path, self.path, runner="camera_hud", step=step, source=scope.CAMERA_LEFT, seconds=seconds)

    def rows_scope(self, path, seconds=8 + 10):  # The schedule's seconds plus the command's start-up allowance.
        return scope.RunScope(path, self.path, runner="camera_hud", step="L-D3", source=scope.SYNTHETIC, seconds=seconds)

    def camera(self, run_scope, **options):
        options.setdefault("detector_factory", camera_hud.ValidationDetector)
        options.setdefault("confirmed", True)
        return camera_hud.run_camera(dict(LIMITS), run_scope=run_scope, **options)

    def assert_refused(self, report, reason, refusal, blocks):
        self.assertEqual((report["status"], report["reason"], report["command"]["refusal"], report["command"]["exit_code"]),
                         ("refused", reason, refusal, 1))
        self.assertEqual((report["bridge"], report["validation_only"], report["bridge_mode"]), (None, False, "--fixed-hud"))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        camera_stage.check_report(report, blocks, camera_hud.MAX_REPORT_BYTES)


class RefusalTests(DeviceModeCase):
    """A device mode without a verifying record is refused before the helper or the bridge is launched."""
    CAMERA = ("--camera", "--allow-camera-input", "--allow-hud-output", "--seconds", "3", "--names-only",
              "--orientation", "identity", "--step", "L-D4")
    ROWS = ("--synthetic-rows", "--allow-hud-output", "--seconds", "8", "--stops", "1", "--stop-seconds", "2")

    def arguments(self, mode, record):
        return [*mode, "--scope-record", record, "--manifest", self.path]

    def test_the_layouts_options_are_checked_before_the_confirmation_and_before_any_record(self):
        # What the command line itself can say is wrong, it says before the operator is asked anything: an
        # option that belongs to the other layout, a missing alignment, numbers out of range, the camera's
        # options on a mode that opens no camera. Nothing is consulted, launched or spent.
        record, _ = self.staged("L-D4")
        panel = ["--camera", "--allow-camera-input", "--allow-hud-output", "--seconds", "3", "--step", "L-D4",
                 "--layout", "head-fixed-box-panel-v1"]
        strip = [*panel[:-2], "--names-only", "--orientation", "identity"]
        cases = (
            ([*panel], "needs --alignment"),
            ([*panel, "--alignment", "1,1,0,0"], "--alignment needs five numbers"),
            ([*panel, "--alignment", "1,1,0,0,45"], "--alignment: scales are 0.25 to 4"),
            ([*panel, "--alignment", "9,1,0,0,0"], "--alignment: scales are 0.25 to 4"),
            ([*panel, "--alignment", "1,1,0,0,inf"], "--alignment needs five numbers"),
            ([*panel, "--alignment", "1,1,0,0,90.5"], "--alignment needs five numbers"),
            ([*panel, "--alignment", "1,1,0,0,0", "--names-only"], "belong to the strip"),
            ([*panel, "--alignment", "1,1,0,0,0", "--orientation", "identity"], "belong to the strip"),
            ([*panel, "--alignment", "1,1,0,0,0", "--region", "0.5,0.5,0.52,0.9"], "--region"),
            ([*panel, "--alignment", "1,1,0,0,0", "--region", "0,0,1"], "--region"),
            ([*panel, "--alignment", "1,1,0,0,0", "--rotation", "45"], "--rotation"),
            ([*panel[:-1], "head-fixed-box-panel-v2", "--alignment", "1,1,0,0,0"], "--layout"),
            ([*strip, "--alignment", "1,1,0,0,0"], "--alignment belongs to the box panel"),
            ([*panel[:-2], "--region", "0.25,0.25,0.75,0.75"], "--directions or --names-only"),
            (["--synthetic-rows", "--allow-hud-output", "--seconds", "8", "--layout", "head-fixed-box-panel-v1"],
             "takes no camera arguments"),
            (["--synthetic-rows", "--allow-hud-output", "--seconds", "8", "--region", "0.25,0.25,0.75,0.75"],
             "takes no camera arguments"),
            (["--validate", "--layout", "head-fixed-box-panel-v1"], "--validate takes no run arguments"),
            (["--validate", "--alignment", "1,1,0,0,0"], "--validate takes no run arguments"),
        )
        for index, (arguments, said) in enumerate(cases):
            terminal = Terminal()
            line = arguments if arguments[0] == "--validate" else self.arguments(arguments, record)
            with self.subTest(case=index, said=said), self.launches() as seen, \
                    mock.patch.object(scope.RunScope, "permit", side_effect=AssertionError("consulted")), \
                    mock.patch.object(camera_hud, "create_detector", side_effect=AssertionError("loaded")), \
                    mock.patch.object(camera_hud, "validate", side_effect=AssertionError("validated")):
                code, report, shown = run_main(line, terminal)
                self.assertEqual((code, report, terminal.asked, seen), (2, None, 0, []))
                self.assertIn(said, shown)
        self.assertEqual(self.ledger(), [])
        # The same alignment written three ways is one alignment, and the neutral one is the default object's.
        from sightcircuit.hud.boxes import Alignment
        self.assertEqual({camera_hud.parse_alignment(text) for text in ("1,1,0,0,0", "1.0,1.0,0.0,-0.0,0", "1e0,1,0,0,0.0")},
                         {Alignment()})
        self.assertEqual(camera_hud.parse_alignment("1.25,0.8,0.1,-0.2,90"), Alignment(1.25, 0.8, 0.1, -0.2, 90))

    def test_no_terminal_and_a_declined_answer_start_nothing_and_consult_no_record(self):
        record, _ = self.staged("L-D4")
        rows_record, _ = self.staged("L-D3")
        for mode, path, kind in ((self.CAMERA, record, "camera_hud_stage"), (self.ROWS, rows_record, "camera_hud_synthetic_rows")):
            for stdin, reason in ((io.StringIO("yes\n"), "confirmation_requires_terminal"),
                                  (Terminal("no\n"), "confirmation_declined"), (Terminal(""), "confirmation_unavailable")):
                with self.subTest(mode=mode[0], reason=reason), self.launches() as seen, \
                        mock.patch.object(scope.RunScope, "permit", side_effect=AssertionError("consulted")), \
                        mock.patch.object(camera_hud, "create_detector", side_effect=AssertionError("loaded")), \
                        mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
                    code, report, _ = run_main(self.arguments(mode, path), stdin)
                    self.assertEqual((code, report["report_kind"], report["status"], report["reason"],
                                      report["command"]["refusal"], report["command"]["confirmation"]),
                                     (1, kind, "error", reason, reason, None))
                    self.assertEqual(seen, [])
                    self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertEqual(self.ledger(), [])

    def test_the_typed_yes_without_a_record_is_refused_before_any_launch_and_creates_no_record(self):
        before = sorted(path.name for path in self.out.iterdir())
        for mode, source, blocks in ((self.CAMERA, "camera", camera_hud.CAMERA_BLOCKS),
                                     (self.ROWS, "rows", camera_hud.SYNTHETIC_BLOCKS)):
            terminal = Terminal()
            with self.subTest(mode=source), self.launches() as seen, \
                    mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()), \
                    mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
                code, report, shown = run_main(self.arguments(mode, self.out / "no-record.json"), terminal)
            self.assertEqual((code, terminal.asked), (1, 1))
            self.assert_refused(report, "fixed_hud_output_not_approved", "scope_record_missing", blocks)
            self.assertEqual((seen, report["command"]["confirmation"], report["scope_record"]["status"],
                              report["scope_record"]["consumed"]), ([], "operator_terminal", "refused", False))
            self.assertIn("it is not an approval and creates no scope record", shown)
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), before)  # The yes wrote nothing.
        self.assertEqual(self.ledger(), [])

    def test_a_record_for_the_wrong_source_the_mirror_and_every_other_mistake_are_refused_before_any_launch(self):
        rows_record, _ = self.staged("L-D3")
        camera_record, _ = self.staged("L-D4")
        cases = (
            ("the synthetic record for a camera run", self.camera_scope(rows_record), "scope_step_mismatch"),
            ("the acceptance step's record for the first-light run", self.camera_scope(self.staged("L-D5")[0]),
             "scope_step_mismatch"),
            ("a record staged 200 days ahead", self.camera_scope(self.forged(
                created_utc=scope._stamp(int(time.time()) + 200 * 86400),
                deadline_utc=scope._stamp(int(time.time()) + 200 * 86400 + 600))), "scope_record_from_the_future"),
            ("the mirror", self.camera_scope(self.forged(source="steamvr_mirror")), "scope_source_not_permitted"),
            ("both cameras", self.camera_scope(self.forged(source="arcturus_camera_both")), "scope_source_not_permitted"),
            ("another runner", self.camera_scope(self.forged(runner="mirror_recognize")), "scope_runner_not_permitted"),
            ("a longer run", self.camera_scope(self.staged("L-D4", max_seconds=2)[0]), "scope_duration_over_record"),
            ("an expired record", self.camera_scope(self.forged(
                created_utc=scope._stamp(1_700_000_000), deadline_utc=scope._stamp(1_700_000_600))), "scope_record_expired"),
            ("no scope at all", None, "scope_record_required"),
        )
        for name, run_scope, refusal in cases:
            with self.subTest(case=name), self.launches() as seen, \
                    mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
                report = self.camera(run_scope)
                self.assert_refused(report, "fixed_hud_output_not_approved", refusal, camera_hud.CAMERA_BLOCKS)
                self.assertEqual((seen, report["source"]["status"], report["target"]), ([], "not_started", None))
        with self.launches() as seen:
            report = camera_hud.run_synthetic_rows(8, stops=1, stop_seconds=2.0, run_scope=self.rows_scope(camera_record),
                                                   confirmed=True)
        self.assert_refused(report, "fixed_hud_output_not_approved", "scope_step_mismatch", camera_hud.SYNTHETIC_BLOCKS)
        self.assertEqual((seen, report["complete"], report["updates"]["submitted"]), ([], False, 0))
        # A record that binds another layout or another view than this command runs with (item B5): refused by
        # name before any launch, as a refusal in the report and never as an uncaught error, the record unspent.
        from sightcircuit.hud.contract import BOX_PANEL_CONFIG
        neutral = {"scale_x": 1.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0, "turn": 0}
        bound = (("the panel's record", {"hud_config": BOX_PANEL_CONFIG, "alignment": neutral},
                  "fixed_hud_layout_not_approved", "scope_layout_mismatch"),
                 ("a record with a region", {"region": [0.25, 0.25, 0.75, 0.75]},
                  "fixed_hud_output_not_approved", "scope_region_mismatch"),
                 ("a record with a rotation", {"rotation": 90}, "fixed_hud_output_not_approved", "scope_rotation_mismatch"),
                 ("a record that requires the presence watch", {"presence_watch": True},
                  "fixed_hud_output_not_approved", "scope_presence_watch_mismatch"))
        for name, staged, reason, refusal in bound:
            path, record = self.staged("L-D4", **staged)
            run_scope = self.camera_scope(path)
            with self.subTest(case=name), self.launches() as seen, \
                    mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
                report = self.camera(run_scope)
                self.assert_refused(report, reason, refusal, camera_hud.CAMERA_BLOCKS)
                self.assertEqual((seen, run_scope.consumed, run_scope.status, report["scope_record"]["run_id"]),
                                 ([], False, "refused", record["run_id"]))
        path, _ = self.staged("L-D3", hud_config=BOX_PANEL_CONFIG)
        run_scope = self.rows_scope(path)
        with self.launches() as seen:
            report = camera_hud.run_synthetic_rows(8, stops=1, stop_seconds=2.0, run_scope=run_scope, confirmed=True)
        self.assert_refused(report, "fixed_hud_layout_not_approved", "scope_layout_mismatch", camera_hud.SYNTHETIC_BLOCKS)
        self.assertEqual((seen, report["complete"], run_scope.consumed, self.ledger()), ([], False, False, []))
        # Through the command line as well: a report and exit code 1, with the typed yes given.
        for mode, step, blocks in ((self.CAMERA, "L-D4", camera_hud.CAMERA_BLOCKS), (self.ROWS, "L-D3", camera_hud.SYNTHETIC_BLOCKS)):
            staged = {"hud_config": BOX_PANEL_CONFIG, **({"alignment": neutral} if step == "L-D4" else {})}
            with self.subTest(command_line=step), self.launches() as seen, \
                    mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()), \
                    mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
                code, report, _ = run_main(self.arguments(mode, self.staged(step, **staged)[0]), Terminal())
            self.assertEqual((code, seen), (1, []))
            self.assert_refused(report, "fixed_hud_layout_not_approved", "scope_layout_mismatch", blocks)
        self.assertEqual(self.ledger(), [])
        # Another helper than the one the manifest pins: refused before the record is consulted, so it is unspent.
        elsewhere = self.out / "sightcircuit-camera-helper"
        shutil.copy2(self.helper, elsewhere)
        run_scope = self.camera_scope(self.staged("L-D4")[0])
        with self.launches() as seen, mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("exported")):
            report = self.camera(run_scope, helper=elsewhere)
        self.assert_refused(report, "fixed_hud_output_not_approved", "site_preconditions_failed", camera_hud.CAMERA_BLOCKS)
        self.assertEqual((seen, run_scope.consumed, run_scope.status), ([], False, "revoked"))
        # A run longer than its scope: the stage and the helper are bounded by what the scope was asked for.
        run_scope = self.camera_scope(self.staged("L-D4")[0], seconds=2)
        with self.launches() as seen:
            report = self.camera(run_scope)  # LIMITS ask the helper for three seconds.
        self.assert_refused(report, "fixed_hud_output_not_approved", "site_preconditions_failed", camera_hud.CAMERA_BLOCKS)
        self.assertEqual((seen, run_scope.consumed), ([], False))
        self.assertEqual(self.ledger(), [])  # No record in this test was spent.

    def test_cheap_preconditions_are_checked_before_the_record_is_spent(self):
        record, _ = self.staged("L-D4")
        cases = (("target_label_unknown", dict(target_label="unicorn")),
                 ("detector_unavailable", dict(detector_factory=mock.Mock(side_effect=RuntimeError("no model")))),
                 ("helper_not_found", dict(helper=self.out / "absent-helper")),
                 ("overlay_bridge_missing", dict(bridge=self.out / "absent-bridge")))
        for reason, options in cases:
            with self.subTest(reason=reason), self.launches() as seen:
                report = self.camera(self.camera_scope(record), **options)
                self.assertEqual((report["status"], report["reason"], report["command"]["refusal"], seen,
                                  report["scope_record"]["status"]), ("error", reason, reason, [], "not_consulted"))
        self.assertEqual(self.ledger(), [])


@unittest.skipUnless(RUNNABLE and fake_vrclient.available(), NEEDS + ", the OpenVR SDK checkout and g++")
class FixedHudCommandTests(DeviceModeCase):
    """The device modes on the bridge's ``--fixed-hud`` against the fake runtime, under a test fixture record."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.runtime_env, cls.log = fake_vrclient.runtime(Path(cls.directory.name) / "vr")

    def setUp(self):
        super().setUp()
        self.log.unlink(missing_ok=True)
        self.env = {**os.environ, **self.runtime_env}

    def drawing(self):
        return [call for call in fake_vrclient.calls(self.log)
                if call["call"] in ("SetOverlayRaw", "ShowOverlay", "HideOverlay", "ClearOverlayTexture", "DestroyOverlay")]

    def assert_retired(self, report):
        final = report["bridge"]["final_report"]
        self.assertEqual((final["mode"], final["exit_code"], final["cleanup_ok"], final["hide_returned_ok"],
                          final["clear_texture_returned_ok"], final["destroy_returned_ok"], final["settings_received"]),
                         ("--fixed-hud", 0, True, True, True, True, 0))
        self.assertEqual([call["call"] for call in self.drawing()][-3:], ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])

    def test_synthetic_rows_under_a_fixture_record_expire_at_each_stop_and_return_after_it(self):
        path, record = self.staged("L-D3")
        report = camera_hud.run_synthetic_rows(8, stops=2, stop_seconds=2.0, run_scope=self.rows_scope(path),
                                               confirmed=True, client_env=self.env)
        camera_stage.check_report(report, camera_hud.SYNTHETIC_BLOCKS, camera_hud.MAX_REPORT_BYTES)
        self.assertEqual((report["report_kind"], report["status"], report["reason"], report["bridge_mode"],
                          report["validation_only"], report["camera_opened"], report["camera_derived"]),
                         ("camera_hud_synthetic_rows", "completed", "schedule", "--fixed-hud", False, False, False))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        updates, stops = report["updates"], report["stops"]
        self.assertEqual((updates["requested"], updates["submitted"], updates["acknowledged_presented"],
                          updates["acknowledged_refused"], updates["replaced"]), (20, 20, 20, 0, 0))
        self.assertEqual((stops["requested"], stops["reached"], stops["expired"], stops["basis"],
                          stops["hide_times_reported"], stops["bound_source"], stops["hide_called_within_ms"],
                          stops["hide_returned_allowance_ms"], stops["as_written_bound_ms"]),
                         (2, 2, 2, "runtime_hide_called_and_returned", True, "acceptance_row_G9", 20.0, 250.0, 1020.0))
        self.assertEqual([event["last_frame_id"] for event in stops["events"]], [7, 14])
        for event in stops["events"]:
            self.assertEqual((event["expired"], event["hide_ok"], event["api_visible_after_hide"], event["texture_cleared"],
                              event["deadline_after_observation_ms"]), (True, True, False, True, 1000.0))
            self.assertGreaterEqual(event["hide_returned_after_observation_ms"], 1000.0)
            self.assertLessEqual(event["hide_returned_after_observation_ms"], 1020.0 + HOST_MARGIN_MS)
            self.assertLessEqual(event["hide_called_after_observation_ms"], event["hide_returned_after_observation_ms"])
            # Both measured values from the deadline, and a verdict for each (the bound's two parameters).
            self.assertGreaterEqual(event["hide_called_after_deadline_ms"], 0)
            self.assertLessEqual(event["hide_called_after_deadline_ms"], event["hide_returned_after_deadline_ms"])
            self.assertLessEqual(event["hide_returned_after_deadline_ms"], 250.0)
            self.assertIs(event["hide_returned_within_allowance"], True)
            self.assertEqual(event["hide_called_within_poll"], event["hide_called_after_deadline_ms"] <= 20.0)
            self.assertEqual(event["within_bound"], event["hide_called_within_poll"])
            self.assertEqual(event["within_bound_as_written"], event["hide_returned_after_observation_ms"] <= 1020.0)
        self.assertGreaterEqual(stops["keepalives_sent"], 0)
        # From the fake runtime's own log: the overlay went down in each stop and came back after it.
        visible, changes = None, []
        for call in fake_vrclient.calls(self.log):
            if "visible" in call and call["visible"] != visible:
                visible = call["visible"]
                changes.append(visible)
        self.assertEqual(changes, [False, True, False, True, False, True, False])  # Down, then up thrice.
        self.assertEqual(len([call for call in self.drawing() if call["call"] == "SetOverlayRaw"]), 20)
        self.assert_retired(report)
        self.assertEqual((report["scope_record"]["status"], report["scope_record"]["consumed"], report["scope_record"]["step"],
                          report["scope_record"]["run_id"], report["command"]["confirmation"]),
                         ("granted", True, "L-D3", record["run_id"], "operator_terminal"))
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])
        self.assertEqual(report["cleanup"], {"bridge_ok": True, "ok": True})
        # The record is spent: the same step again is refused, and the bridge is not launched.
        self.log.unlink()
        with self.launches() as seen:
            again = camera_hud.run_synthetic_rows(8, stops=2, stop_seconds=2.0, run_scope=self.rows_scope(path),
                                                  confirmed=True, client_env=self.env)
        self.assert_refused(again, "fixed_hud_output_not_approved", "scope_run_id_used", camera_hud.SYNTHETIC_BLOCKS)
        self.assertEqual((seen, fake_vrclient.calls(self.log)), ([], []))

    def test_a_signal_stops_the_synthetic_rows_takes_the_tile_down_and_leaves_nothing_behind(self):
        import threading
        import time
        path, record = self.staged("L-D3")
        arguments = ["--synthetic-rows", "--allow-hud-output", "--scope-record", path, "--manifest", self.path,
                     "--seconds", "20", "--stops", "1", "--stop-seconds", "2"]
        threads, handlers = threading.active_count(), {number: signal.getsignal(number)
                                                       for number in (signal.SIGINT, signal.SIGTERM)}
        timer = threading.Timer(1.5, os.kill, (os.getpid(), signal.SIGINT))  # The operator's Ctrl-C.
        started = time.monotonic()
        timer.start()
        try:
            with mock.patch.dict(os.environ, self.runtime_env):
                code, report, shown = run_main(arguments, Terminal())
        finally:
            timer.cancel()
            timer.join()
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((code, report["status"], report["reason"], report["complete"], report["command"]["exit_code"]),
                         (128 + signal.SIGINT, "cancelled", "signal", False, 130))
        self.assertIn(confirmation.SYNTHETIC_NOTICE, shown)
        self.assertNotIn(contract.EXPORT_NOTICE, shown)
        self.assertGreater(report["updates"]["acknowledged_presented"], 2)
        self.assertLess(report["updates"]["submitted"], report["updates"]["requested"])
        self.assert_retired(report)
        self.assertEqual((report["cleanup"]["ok"], report["scope_record"]["consumed"]), (True, True))
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])  # A stopped run still spent its record.
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        # Nothing of the run outlives it: no thread, and the handlers that were there before.
        self.assertEqual(threading.active_count(), threads)
        self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)

    def test_the_camera_mode_end_to_end_on_the_stand_in_under_a_fixture_record(self):
        path, record = self.staged("L-D4")
        terminal = io.StringIO()
        report = self.camera(self.camera_scope(path), client_env=self.env, directions=True, orientation="identity",
                             target_label="bottle", cues=((400, "out_of_view"), (800, "in_view")), terminal=terminal)
        camera_stage.check_report(report, camera_hud.CAMERA_BLOCKS, camera_hud.MAX_REPORT_BYTES)
        self.assertEqual(set(report), set(camera_stage.REPORT_BLOCKS) | {"command", "scope_record", "marks", "target",
                                                                         "timebase"})
        self.assertEqual((report["report_kind"], report["status"], report["bridge_mode"], report["validation_only"],
                          report["images_saved"], report["pixels_in_report"]),
                         ("camera_hud_stage", "completed", "--fixed-hud", False, False, False))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertEqual((report["source"]["source_id"], report["source"]["camera_index"],
                          report["config"]["directions"], report["config"]["orientation"]),
                         ("arcturus_camera_left", 0, True, "identity"))
        # The run's three seconds, the helper's own three-second bound and the client's own bound on real output
        # end together; any of them may come first, and each is a completed run.
        self.assertIn((report["reason"], report["source"]["status"]),
                      (("seconds", "stopped"), ("max_seconds", "completed"), ("seconds", "completed"),
                       ("output_bound", "stopped"), ("output_bound", "completed")))
        frames = report["frames"]
        self.assertGreater(frames["sent"], 5)
        self.assertEqual((frames["acknowledged_presented"], frames["acknowledged_refused"]), (frames["sent"], 0))
        self.assertEqual([row["label"] for row in report["labels"]["rows"]], ["bottle"])
        self.assertEqual(report["cleanup"], {"source_ok": True, "bridge_ok": True, "sinks_ok": True, "ok": True})
        self.assertEqual((report["command"]["mode"], report["command"]["exit_code"], report["command"]["confirmation"],
                          report["command"]["refusal"], report["command"]["camera_data"]),
                         ("camera", 0, "operator_terminal", None, "labels_and_display_intervals_are_camera_data"))
        self.assertEqual((report["scope_record"]["status"], report["scope_record"]["consumed"], report["scope_record"]["step"],
                          report["scope_record"]["source"], report["scope_record"]["approval"]),
                         ("granted", True, "L-D4", "arcturus_camera_left", "owner-2026-11-16-a1"))
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])
        # The G10 rule is computed for the named class; a three-second run holds no 60-second window.
        target = report["target"]
        self.assertEqual((target["label"], target["rule"], target["window_seconds"], target["fraction"],
                          target["max_gap_seconds"], target["found"], target["void"]),
                         ("bottle", "g10_named_in_window", 60.0, 0.9, 2.0, False, None))
        marks = report["marks"]
        self.assertEqual([(mark["source"], mark["label"]) for mark in marks["recorded"]],
                         [("cue", "out_of_view"), ("cue", "in_view")])
        self.assertEqual((marks["recorded_count"], marks["cues_not_reached"], marks["key_marks_enabled"]), (2, [], False))
        self.assertEqual(terminal.getvalue(), "\aCUE out_of_view\n\aCUE in_view\n")
        self.assertEqual(report["timebase"]["monotonic_clock"], "host_clock_monotonic_ns")
        self.assert_retired(report)
        self.assertGreater(len([call for call in self.drawing() if call["call"] == "SetOverlayRaw"]), 5)
        text = json.dumps(report)
        self.assertLess(len(text), camera_hud.MAX_REPORT_BYTES)
        for word in ("intrinsic", "calibration", "rgba", "pixels\":", str(self.root), str(self.out)):
            self.assertNotIn(word, text)

    def test_a_headset_removal_ends_the_camera_run_with_the_tile_down_and_exit_zero(self):
        path, _ = self.staged("L-D4")
        report = self.camera(self.camera_scope(path), client_env=self.env, directions=False, orientation="identity",
                             extra_args=("--fake-scenario", "end:headset_removed"), mark_labels=("remove",))
        self.assertEqual((report["status"], report["reason"], report["source"]["end_reason"], report["command"]["exit_code"],
                          report["cleanup"]["ok"]), ("presence_ended", "headset_removed", "headset_removed", 0, True))
        self.assert_retired(report)
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)

    def test_the_command_line_runs_the_camera_mode_only_after_the_typed_yes_and_saves_only_where_named(self):
        path, record = self.staged("L-D4")
        saved = self.out / "run.json"
        arguments = ["--camera", "--allow-camera-input", "--allow-hud-output", "--scope-record", path, "--manifest",
                     self.path, "--seconds", "3", "--step", "L-D4", "--directions", "--orientation", "identity",
                     "--target-label", "bottle",
                     "--cue", "0.4:out_of_view", "--report", saved]
        terminal = Terminal()
        with mock.patch.dict(os.environ, self.runtime_env), \
                mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()):
            code, report, shown = run_main(arguments, terminal)
        self.assertEqual((code, terminal.asked, report["status"], report["command"]["confirmation"],
                          report["command"]["report_saved_by_operator"], report["scope_record"]["run_id"]),
                         (0, 1, "completed", "operator_terminal", True, record["run_id"]))
        self.assertIn(contract.EXPORT_NOTICE, shown)
        self.assertIn(confirmation.DISPLAY_NOTICE, shown)
        self.assertIn("Bounds: 15 frames, 3 seconds, 5 frames a second, cameras 0.", shown)
        self.assertEqual(json.loads(saved.read_text()), report)
        self.assertEqual(report["source"]["export"]["stream"]["ready"]["cameras"], [0])
        self.assert_retired(report)
        # The second launch needs a new record: the typed yes does not stand in for one.
        with mock.patch.dict(os.environ, self.runtime_env), self.launches() as seen, \
                mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()):
            code, again, _ = run_main([*arguments[:-2], "--report", self.out / "again.json"], Terminal())
        self.assertEqual((code, again["status"], again["command"]["refusal"], seen),
                         (1, "refused", "scope_run_id_used", []))


    def test_the_command_line_runs_the_box_panel_through_a_region_under_a_record_that_names_all_of_it(self):
        from sightcircuit.hud.contract import BOX_PANEL_CONFIG as PANEL, config_digest
        neutral = {"scale_x": 1.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0, "turn": 0}
        centre = [0.25, 0.25, 0.75, 0.75]
        bound = {"hud_config": PANEL, "region": centre, "rotation": 90, "alignment": neutral, "presence_watch": False}
        base = ["--camera", "--allow-camera-input", "--allow-hud-output", "--manifest", self.path, "--seconds", "3",
                "--step", "L-D4", "--target-label", "bottle"]
        view = ["--layout", PANEL.layout_id, "--region", "0.25,0.25,0.75,0.75", "--rotation", "90",
                "--alignment", "1,1,0,0,0"]

        def run(record, options, name):
            with mock.patch.dict(os.environ, self.runtime_env), \
                    mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()):
                return run_main([*base, "--scope-record", record, *options, "--report", self.out / f"{name}.json"], Terminal())

        # Every difference between the command line and the record is refused before anything is launched, by
        # name, with the record unspent. Each case is the right command line with one thing changed.
        path, record = self.staged("L-D4", **bound)
        differing = (
            ("the strip", ["--directions", "--orientation", "identity", "--region", "0.25,0.25,0.75,0.75", "--rotation",
                           "90"], "fixed_hud_layout_not_approved", "scope_layout_mismatch"),
            ("another rotation", [*view[:5], "270", *view[6:]], "fixed_hud_output_not_approved", "scope_rotation_mismatch"),
            ("another region", [*view[:3], "0.2,0.25,0.75,0.75", *view[4:]], "fixed_hud_output_not_approved",
             "scope_region_mismatch"),
            ("no region", [*view[:2], *view[6:]], "fixed_hud_output_not_approved", "scope_region_mismatch"),
            ("another alignment", [*view[:7], "1,1,0,0,90"], "fixed_hud_output_not_approved", "scope_alignment_mismatch"),
            ("an alignment off by a little", [*view[:7], "1,1,0.01,0,0"], "fixed_hud_output_not_approved",
             "scope_alignment_mismatch"))
        for name, options, reason, refusal in differing:
            with self.subTest(differs=name), self.launches() as seen:
                code, report, _ = run(path, options, f"refused-{len(list(self.out.iterdir()))}")
                self.assertEqual((code, seen, self.ledger()), (1, [], []))
                self.assert_refused(report, reason, refusal, camera_hud.CAMERA_BLOCKS)
                self.assertEqual((report["scope_record"]["consumed"], report["scope_record"]["hud_config_id"]),
                                 (False, PANEL.layout_id))
        self.assertEqual(fake_vrclient.calls(self.log) if self.log.exists() else [], [])  # The runtime saw nothing.
        # The command line that asks for what the record binds runs, on the fake runtime.
        code, report, shown = run(path, view, "run")
        self.assertEqual((code, report["status"], report["command"]["confirmation"], report["scope_record"]["run_id"]),
                         (0, "completed", "operator_terminal", record["run_id"]))
        self.assertEqual(json.loads((self.out / "run.json").read_text()), report)
        camera_stage.check_report(report, camera_hud.CAMERA_BLOCKS, camera_hud.MAX_REPORT_BYTES)
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        # The record's bound fields are in the report, as the record states them.
        block = report["scope_record"]
        self.assertEqual((block["status"], block["consumed"], block["requested_layout"], block["hud_config_id"],
                          block["hud_config_sha256"], block["region"], block["rotation"], block["alignment"],
                          block["presence_watch"]),
                         ("granted", True, PANEL.layout_id, PANEL.layout_id, config_digest(PANEL),
                          dict(zip(("x0", "y0", "x1", "y1"), centre)), 90, neutral, False))
        # What ran is what the record names: the layout, the megabyte, the region and turn, the alignment.
        self.assertEqual((report["config"]["hud_config_id"], report["config"]["update_bytes"], report["config"]["alignment"],
                          report["view"]["region"], report["view"]["rotation"], report["view"]["crop_native_xyxy"],
                          report["boxes"]["region_rotation"], report["boxes"]["alignment_neutral"]),
                         (PANEL.layout_id, 1_048_576, neutral, block["region"], 90, [16, 12, 48, 36], 90, True))
        # The wearer-facing notice was the panel's, not the strip's.
        self.assertIn(confirmation.BOX_DISPLAY_NOTICE, shown)
        self.assertNotIn(confirmation.DISPLAY_NOTICE, shown)
        self.assertIn(contract.EXPORT_NOTICE, shown)
        self.assertGreater(report["frames"]["sent"], 0)
        # Every frame written was answered by the bridge, and the boxes block agrees with the frames block:
        # one box in every result the client accepted. (This run may end on its own bound, where the last
        # frame's deadline is the run's end: a result accepted then can be withdrawn unwritten, and one written
        # across the bound can be refused as expired. Neither is asserted away; at most that one frame is refused.)
        frames = report["frames"]
        self.assertEqual((frames["acknowledged_presented"] + frames["acknowledged_refused"], frames["unacknowledged"]),
                         (frames["sent"], 0))
        self.assertLessEqual(frames["acknowledged_refused"], 1)
        self.assertEqual(frames["acknowledged_refused"], frames["refused_reasons"]["expired_on_arrival"]
                         + frames["refused_reasons"]["expired_before_show"])
        self.assertEqual((report["boxes"]["boxes_sent"], report["boxes"]["results_sent"]), (frames["submitted"],) * 2)
        self.assertEqual([row["label"] for row in report["labels"]["rows"]], ["bottle"])
        final = report["bridge"]["final_report"]
        self.assertEqual((final["mode"], final["fixed_hud"]["config_id"], final["fixed_hud"]["config_digest"],
                          final["fixed_hud"]["width"], final["fixed_hud"]["height"]),
                         ("--fixed-hud", PANEL.layout_id, config_digest(PANEL), 512, 512))
        uploads = [call for call in fake_vrclient.calls(self.log) if call["call"] == "SetOverlayRaw"]
        self.assertEqual(len(uploads), report["frames"]["acknowledged_presented"])
        self.assert_retired(report)
        self.assertNotIn("calibration", json.dumps(report).lower())
        # Spent: the same command line again is refused, and nothing more is launched.
        with self.launches() as seen:
            code, again, _ = run(path, view, "again")
        self.assertEqual((code, again["status"], again["command"]["refusal"], seen), (1, "refused", "scope_run_id_used", []))
        # The strip through the same region, under a strip record that names it: the region is the strip's too.
        strip_bound = {"region": centre, "rotation": 90, "presence_watch": False}
        path, _ = self.staged("L-D4", **strip_bound)
        code, report, _ = run(path, ["--names-only", "--orientation", "identity", "--region", "0.25,0.25,0.75,0.75",
                                     "--rotation", "90"], "strip")
        self.assertEqual((code, report["status"], report["config"]["hud_config_id"], report["view"]["rotation"],
                          report["view"]["prepared"], report["boxes"], report["config"]["update_bytes"]),
                         (0, "completed", "fixed-edge-hud-v1", 90, True, None, 163_840))
        self.assertEqual([row["label"] for row in report["labels"]["rows"]], ["bottle"])


@unittest.skipUnless(RUNNABLE, NEEDS)
class LedgerBudgetTests(unittest.TestCase):
    """The target label's intervals have a budget of their own: other labels cannot void its verdict."""

    def test_other_labels_flickering_never_drop_the_targets_intervals(self):
        ledger = camera_stage.DisplayLedger("bottle")
        second = 1_000_000_000
        # Ninety seconds at five results a second: the bottle up in about 92% of them (a gap every 2.4 s), and
        # three other classes flickering on and off with every result.
        at, others = 0, ("cup", "chair", "person")
        for index in range(450):
            labels = [] if index % 12 == 11 else ["bottle"]
            labels += [name for offset, name in enumerate(others) if (index + offset) % 2]
            ledger.presented(labels, at, at + second, True)
            at += second // 5
        ledger.finish(at)
        self.assertGreater(ledger.dropped, 100)  # The others overran their shared budget...
        self.assertEqual((ledger.target_dropped, ledger.target_count), (0, 38))  # ...and the target lost nothing.
        self.assertEqual(ledger.count, camera_stage.MAX_SPELLS - camera_stage.MAX_TARGET_SPELLS)
        rows = ledger.rows(0, at)
        report = {"labels": {"spells_dropped": ledger.dropped, "target_label": "bottle",
                             "target_spells_dropped": ledger.target_dropped, "run_start_ns": 0, "run_end_ns": at,
                             "rows": rows}}
        verdict = camera_stage.named_in_window(report, "bottle", **camera_hud.G10_WINDOW)
        self.assertEqual((verdict["found"], verdict["named_fraction"] >= 0.9, verdict["longest_gap_seconds"]),
                         (True, True, 0.2))
        with self.assertRaisesRegex(ValueError, "camera_hud_window_intervals_incomplete"):
            camera_stage.named_in_window(report, "cup", **camera_hud.G10_WINDOW)  # Another label's are incomplete.
        # The target's own budget is its own limit: beyond it, and only then, its verdict is void.
        ledger = camera_stage.DisplayLedger("bottle")
        for index in range(camera_stage.MAX_TARGET_SPELLS + 3):
            ledger.presented(["bottle"], index * 2 * second, index * 2 * second + second, True)
            ledger.expired(index * 2 * second + second)  # Up for a second, then down for a second.
        ledger.finish(10 ** 13)
        self.assertEqual((ledger.target_count, ledger.target_dropped, ledger.dropped),
                         (camera_stage.MAX_TARGET_SPELLS, 3, 0))
        report["labels"].update(spells_dropped=0, target_spells_dropped=3)
        with self.assertRaisesRegex(ValueError, "camera_hud_window_intervals_incomplete"):
            camera_stage.named_in_window(report, "bottle", **camera_hud.G10_WINDOW)
        # Without a target the ledger is what it was: one budget for every label.
        plain = camera_stage.DisplayLedger()
        for index in range(camera_stage.MAX_SPELLS + 5):
            plain.presented(["bottle"], index * 2 * second, index * 2 * second + second, True)
            plain.expired(index * 2 * second + second)
        plain.finish(10 ** 13)
        self.assertEqual((plain.count, plain.dropped, plain.target_dropped), (camera_stage.MAX_SPELLS, 5, 0))


class OperatorDefectTests(DeviceModeCase):
    """Mistakes that would spend a one-run record for nothing: each is refused before the record is consulted."""
    CAMERA = RefusalTests.CAMERA
    ROWS = RefusalTests.ROWS

    def arguments(self, mode, record, *more):
        return [*mode, "--scope-record", record, "--manifest", self.path, *more]

    def test_the_report_path_is_checked_before_the_confirmation_and_a_declined_run_writes_no_file(self):
        record, _ = self.staged("L-D4")
        taken = self.out / "taken.json"
        taken.write_text("{}\n")
        for path, message in ((taken, "already exists"), (self.out / "absent" / "run.json", "not an existing directory"),
                              (self.out / "run.txt", "needs a .json path")):
            terminal = Terminal()
            with self.subTest(path=path.name), self.launches() as seen:
                code, report, shown = run_main(self.arguments(self.CAMERA, record, "--report", path), terminal)
                self.assertEqual((code, report, terminal.asked, seen), (2, None, 0, []))  # Before the question.
                self.assertIn(message, shown)
        self.assertEqual((taken.read_text(), self.ledger()), ("{}\n", []))
        # The reviewer's sequence: decline with ``y``, then run the same line and type yes. The declined run
        # wrote no file, so the path is still free and the real run's report lands in it.
        saved = self.out / "run.json"
        line = self.arguments(self.ROWS, self.staged("L-D3")[0], "--report", saved)
        with self.launches() as seen:
            code, report, _ = run_main(line, Terminal("y\n"))
        self.assertEqual((code, report["reason"], report["command"]["report_saved_by_operator"], saved.exists(), seen),
                         (1, "confirmation_declined", False, False, []))
        self.assertEqual(self.ledger(), [])
        # Refused before the record was consulted (here: the lock directory): no file either, record unspent.
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "relative/path"}), self.launches() as seen:
            code, report, _ = run_main(self.arguments(self.CAMERA, record, "--report", saved), Terminal())
        self.assertEqual((code, report["status"], report["reason"], saved.exists(), seen, self.ledger()),
                         (1, "error", "helper_runtime_dir_invalid", False, [], []))
        # A refusal by the record itself is a consulted record: that report is saved where it was named.
        with self.launches() as seen:
            code, report, _ = run_main(self.arguments(self.ROWS, self.out / "no-record.json", "--report", saved),
                                       Terminal())
        self.assertEqual((code, report["command"]["refusal"], report["command"]["report_saved_by_operator"], seen),
                         (1, "scope_record_missing", True, []))
        self.assertEqual(json.loads(saved.read_text())["command"]["refusal"], "scope_record_missing")

    def test_the_helpers_lock_directory_is_a_precondition(self):
        record, _ = self.staged("L-D4")
        loose = self.out / "loose"
        loose.mkdir(mode=0o755)
        loose.chmod(0o755)
        link = self.out / "link"
        link.symlink_to(self.runtime_directory)
        cases = ((None, "helper_runtime_dir_missing"), ("", "helper_runtime_dir_missing"),
                 ("relative", "helper_runtime_dir_invalid"), (f"{self.runtime_directory}/", "helper_runtime_dir_invalid"),
                 (f"{self.runtime_directory}/.", "helper_runtime_dir_invalid"), (str(loose), "helper_runtime_dir_invalid"),
                 (str(link), "helper_runtime_dir_invalid"), (str(self.out / "absent"), "helper_runtime_dir_invalid"),
                 (str(self.path), "helper_runtime_dir_invalid"))
        for value, reason in cases:
            environment = dict(os.environ)
            environment.pop("XDG_RUNTIME_DIR", None)
            if value is not None:
                environment["XDG_RUNTIME_DIR"] = value
            run_scope = self.camera_scope(record)
            with self.subTest(value=value), mock.patch.dict(os.environ, environment, clear=True), self.launches() as seen:
                self.assertEqual(camera_hud.runtime_dir_refusal(), reason)
                report = self.camera(run_scope)
                self.assertEqual((report["status"], report["reason"], seen, run_scope.status, self.ledger()),
                                 ("error", reason, [], "not_consulted", []))
        self.assertIsNone(camera_hud.runtime_dir_refusal())

    def test_a_report_that_does_not_fit_its_schema_is_replaced_and_keeps_the_cleanup_and_the_scope(self):
        failure = ValueError("camera_hud_report_field_invalid:report.frames.sent")
        with mock.patch.object(camera_stage.CameraHudStage, "report", side_effect=failure):
            report = camera_hud.run_camera(dict(LIMITS), helper=self.helper, bridge=DEFAULT_BRIDGE, confirmed=True,
                                           detector_factory=camera_hud.ValidationDetector, validation_only=True)
        self.assertEqual((report["status"], report["reason"], report["report_replaced"], report["command"]["refusal"],
                          report["command"]["exit_code"], report["cleanup"]),
                         ("error", "camera_hud_report_field_invalid:report.frames.sent", True, "report_invalid", 1,
                          {"source_ok": True, "bridge_ok": True, "ok": True}))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        path, record = self.staged("L-D3")
        run_scope = self.rows_scope(path)
        with mock.patch.object(synthetic_rows.SyntheticRows, "report", side_effect=failure), self.launches() as seen:
            report = camera_hud.run_synthetic_rows(8, stops=1, stop_seconds=2.0, run_scope=self.rows_scope(
                self.out / "no-record.json"), confirmed=True)
        self.assertEqual((report["status"], report["report_replaced"], report["scope_record"]["refusal"], seen),
                         ("error", True, "scope_record_missing", []))
        self.assertEqual((run_scope.status, self.ledger()), ("not_consulted", []))
        oversize = {**camera_hud.refused_before_start("camera", "x", camera_hud.CAMERA_BLOCKS, "camera_hud_stage"),
                    "marks": {"recorded": [{"label": "remove", "index": index} for index in range(200)] * 40}}
        replaced = camera_hud.finish(oversize, camera_hud.CAMERA_BLOCKS, "camera", cleanup={"bridge_ok": True, "ok": True})
        self.assertEqual((replaced["status"], replaced["report_replaced"], replaced["cleanup"]),
                         ("error", True, {"bridge_ok": True, "ok": True}))


@unittest.skipUnless(RUNNABLE and fake_vrclient.available(), NEEDS + ", the OpenVR SDK checkout and g++")
class SignalTests(DeviceModeCase):
    """Signals during a device run on ``--fixed-hud`` against the fake runtime: the report is always the run's own."""
    LONG = {"fps": 5, "max_seconds": 20, "max_frames": 100}

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.runtime_env, cls.log = fake_vrclient.runtime(Path(cls.directory.name) / "vr")

    def setUp(self):
        super().setUp()
        self.log.unlink(missing_ok=True)
        environment = mock.patch.dict(os.environ, self.runtime_env)
        environment.start()
        self.addCleanup(environment.stop)

    def camera_line(self, record):
        return ["--camera", "--allow-camera-input", "--allow-hud-output", "--scope-record", record, "--manifest",
                self.path, "--seconds", "20", "--step", "L-D4", "--names-only", "--orientation", "identity",
                "--target-label", "bottle"]

    def assert_a_real_cancelled_run(self, code, report, record, number):
        self.assertEqual((code, report["status"], report["reason"], report["command"]["exit_code"],
                          report["command"]["refusal"]), (128 + number, "cancelled", "signal", 128 + number, None))
        self.assertEqual((report["scope_record"]["status"], report["scope_record"]["consumed"],
                          report["scope_record"]["run_id"]), ("granted", True, record["run_id"]))
        self.assertIsNotNone(report["bridge"])
        self.assertEqual((report["bridge"]["final_report"]["mode"], report["bridge"]["final_report"]["exit_code"],
                          report["cleanup"]["bridge_ok"]), ("--fixed-hud", 0, True))
        calls = [call["call"] for call in fake_vrclient.calls(self.log)]
        self.assertEqual(calls[-3:], ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)

    def run_signalled(self, line, after, number=signal.SIGINT, repeat=1, to_bridge=False):
        import threading
        threads = threading.active_count()
        # A signal the timer sends after the command has returned must not reach the test runner's own handler.
        for item in (signal.SIGINT, signal.SIGTERM):
            signal.signal(item, lambda number, frame: None)
        handlers = {item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)}
        with mock.patch.object(camera_hud, "create_detector", return_value=camera_hud.ValidationDetector()):
            if to_bridge:
                with self.terminal_signal(after, number, repeat):
                    result = run_main(line, Terminal())
            else:
                def send():
                    for index in range(repeat):
                        os.kill(os.getpid(), number)
                        time.sleep(0.06)
                timer = threading.Timer(after, send)
                timer.start()
                try:
                    result = run_main(line, Terminal())
                finally:
                    timer.cancel()
                    timer.join()
        self.assertEqual(threading.active_count(), threads)  # Nothing of the run outlives it.
        self.assertEqual({item: signal.getsignal(item) for item in handlers}, handlers)
        return result

    def test_any_number_of_signals_leave_the_camera_runs_own_report(self):
        for name, number, repeat, after in (("two SIGINTs 60 ms apart", signal.SIGINT, 2, 2.0),
                                            ("two SIGTERMs", signal.SIGTERM, 2, 2.0),
                                            ("five SIGINTs", signal.SIGINT, 5, 2.0)):
            path, record = self.staged("L-D4")
            self.log.unlink(missing_ok=True)
            with self.subTest(case=name):
                code, report, _ = self.run_signalled(self.camera_line(path), after, number, repeat)
                self.assert_a_real_cancelled_run(code, report, record, number)
                self.assertGreater(report["frames"]["sent"], 0)  # Uploads were made, and the report says so.
                self.assertIsNotNone(report["source"])
                self.assertEqual(report["cleanup"]["ok"], True)

    def test_a_signal_before_the_helper_is_driven_still_ends_with_the_runs_own_report(self):
        # During the record's verification and the bridge's start: the stage has not reached the helper yet.
        path, record = self.staged("L-D4")
        code, report, _ = self.run_signalled(self.camera_line(path), 0.05, signal.SIGINT, 2)
        self.assertEqual((code, report["status"], report["reason"], report["report_kind"]),
                         (130, "cancelled", "signal", "camera_hud_stage"))
        self.assertEqual((report["scope_record"]["run_id"], report["command"]["confirmation"]),
                         (record["run_id"], "operator_terminal"))
        self.assertIsNotNone(report["frames"])
        self.assertIsNotNone(report["scope_record"]["status"])

    def test_a_terminals_ctrl_c_is_a_cancelled_run_with_a_clean_bridge_in_both_modes(self):
        # The signal reaches the bridge as well as this process, as a terminal sends it to the foreground group:
        # the bridge retires and exits 0 by itself while this side is still writing.
        for attempt in range(3):
            path, record = self.staged("L-D4")
            self.log.unlink(missing_ok=True)
            with self.subTest(mode="camera", attempt=attempt):
                code, report, _ = self.run_signalled(self.camera_line(path), 2.0, to_bridge=True)
                self.assert_a_real_cancelled_run(code, report, record, signal.SIGINT)
                self.assertEqual(report["cleanup"]["ok"], True)
                if report["bridge"]["error"] is not None:  # The write failed because the bridge had already left.
                    self.assertIs(report["end"]["bridge_left_on_signal"], True)
        for attempt in range(3):
            path, record = self.staged("L-D3")
            self.log.unlink(missing_ok=True)
            line = ["--synthetic-rows", "--allow-hud-output", "--scope-record", path, "--manifest", self.path,
                    "--seconds", "20", "--stops", "1", "--stop-seconds", "2"]
            with self.subTest(mode="synthetic_rows", attempt=attempt):
                code, report, _ = self.run_signalled(line, 1.5, to_bridge=True)
                self.assertEqual((code, report["status"], report["reason"], report["cleanup"]),
                                 (130, "cancelled", "signal", {"bridge_ok": True, "ok": True}))
                self.assertEqual(report["bridge"]["final_report"]["exit_code"], 0)
                if report["bridge"]["error"] is not None:
                    self.assertIs(report["end"]["bridge_left_on_stop"], True)

    def test_a_bridge_failure_without_a_signal_is_still_an_error(self):
        path, _ = self.staged("L-D4")
        real = subprocess.Popen
        bridges = []

        def launch(arguments, *more, **options):
            process = real(arguments, *more, **options)
            if "--fixed-hud" in list(map(str, arguments)):
                bridges.append(process)
            return process
        import threading
        timer = threading.Timer(2.0, lambda: [process.send_signal(signal.SIGINT) for process in bridges])
        with mock.patch.object(subprocess, "Popen", side_effect=launch):
            timer.start()
            try:
                report = self.camera(self.camera_scope(path, seconds=20, step="L-D4"), **{"detector_factory": camera_hud.ValidationDetector})
            finally:
                timer.cancel()
                timer.join()
        # The bridge left cleanly, but nothing signalled this run: it is not excused as a cancellation.
        self.assertNotEqual(report["status"], "cancelled")
        self.assertIs(report["end"]["bridge_left_on_signal"], False)


@unittest.skipUnless(RUNNABLE and fake_vrclient.available(), NEEDS + ", the OpenVR SDK checkout and g++")
class SlowBridgeTests(DeviceModeCase):
    """A bridge that takes six seconds to become ready: the helper's supervision waits for it."""
    DELAY_SECONDS = 6

    def install_binaries(self):
        real = self.bridge.with_name("real-bridge")
        shutil.copy2(DEFAULT_BRIDGE, real)
        self.bridge.write_text(f"#!/bin/sh\nsleep {self.DELAY_SECONDS}\nexec '{real}' \"$@\"\n")
        self.helper.write_text(fake_camera_export.SHEBANG_INSTALL.format(python=sys.executable, root=str(ROOT)))
        scope_tests.ScopeCase.install_binaries(self)

    def test_a_slow_starting_bridge_does_not_end_the_run_after_the_record_is_spent(self):
        self.assertGreater(self.DELAY_SECONDS, camera_helper.ATTACH_SECONDS)  # Longer than the source's default.
        self.assertGreater(camera_hud.ATTACH_SECONDS, 8)  # The bridge's ready wait, with margin.
        with tempfile.TemporaryDirectory() as directory:
            env, _ = fake_vrclient.runtime(Path(directory) / "vr")
            path, record = self.staged("L-D4")
            limits = {"fps": 5, "max_seconds": 12, "max_frames": 60}
            report = camera_hud.run_camera(limits, run_scope=self.camera_scope(path, seconds=12), confirmed=True,
                                           detector_factory=camera_hud.ValidationDetector,
                                           client_env={**os.environ, **env})
        self.assertNotEqual(report["reason"], "camera_source_not_attached")
        self.assertEqual((report["status"], report["scope_record"]["consumed"], report["cleanup"]["ok"]),
                         ("completed", True, True))
        self.assertGreater(report["frames"]["acknowledged_presented"], 5)  # Frames were shown after the slow start.
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])


if __name__ == "__main__":
    unittest.main()
