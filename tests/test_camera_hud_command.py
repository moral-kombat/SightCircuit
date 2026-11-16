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
        self.assertEqual((len(COMMANDS), COMMANDS[-1], COMMANDS[-2]), (15, "camera_hud", "camera_recognize"))
        entry_points = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertEqual(entry_points["sightcircuit-camera-hud"], "sightcircuit.commands.camera_hud:main")
        self.assertEqual(len(entry_points), 15)
        self.assertTrue((ROOT / "scripts/camera_hud.py").is_file())
        # Its own manifest group: the wrapper beside the helper it launches; the body is in ``modules``.
        self.assertIn("scripts/camera_hud.py", manifest.CAMERA_HUD_FILES)
        self.assertEqual(manifest.CAMERA_HUD_SCRIPTS, ("scripts/build_camera_helper.sh", "scripts/camera_hud.py",
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
        for notice in (confirmation.DISPLAY_NOTICE, confirmation.SYNTHETIC_NOTICE):
            self.assertIn("it is not an approval and creates no scope record", notice)
            self.assertIn("Ctrl-C", notice)
            self.assertIn("green input", notice)
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
            (("--synthetic-rows", *self.DEVICE[:-1], "121"), "synthetic_rows_seconds_out_of_range"),
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
        staged = self.commands("scripts/stage_scope_record.py")
        self.assertEqual(len(staged), 1)
        args = stage_scope_record.build_parser().parse_args(staged[0])
        self.assertEqual((args.step, args.valid_minutes), ("L-D3", 60))
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
        for name in ("config", "frames", "deadlines", "frame_expired", "end", "cleanup", "scope_record", "command", "target"):
            with self.subTest(block=name):
                self.assertLessEqual(set(template[name]), set(blocks[name]))
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
                                  report["helper"]["fake"], report["camera"], report["synthetic_rows"]),
                                 ("validation_failed", "validate_requires_fake_helper", 1, False, None, None))
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
                          stops["hide_times_reported"], stops["bound_ms"]), (2, 2, 2, "runtime_hide_returned", True, 1020.0))
        self.assertEqual([event["last_frame_id"] for event in stops["events"]], [7, 14])
        for event in stops["events"]:
            self.assertEqual((event["expired"], event["hide_ok"], event["api_visible_after_hide"], event["texture_cleared"],
                              event["deadline_after_observation_ms"]), (True, True, False, True, 1000.0))
            self.assertGreaterEqual(event["hide_returned_after_observation_ms"], 1000.0)
            self.assertLessEqual(event["hide_returned_after_observation_ms"], 1020.0 + HOST_MARGIN_MS)
            self.assertLessEqual(event["hide_called_after_observation_ms"], event["hide_returned_after_observation_ms"])
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


if __name__ == "__main__":
    unittest.main()
