"""The camera helper source against the executable stand-in: launch, bounds, refusals, stops and the report.

The stand-in (``tests/fake_camera_export.py``) is installed outside the
checkout and launched through the supervision primitive exactly as the real
helper would be. Standard library only; Linux (owned sessions).
"""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import contract, fake_export  # noqa: E402
from sightcircuit.errors import SightCircuitError  # noqa: E402
from sightcircuit.runtime.supervision import RECORD_AND_POLL  # noqa: E402
from sightcircuit.sources import camera_helper  # noqa: E402
from tests import fake_camera_export  # noqa: E402

LIMITS = {"fps": 10, "max_seconds": 3, "max_frames": 5}
CATALOGUE_LIMITS = fake_export.LIMITS  # The invalid streams echo these.


@unittest.skipUnless(sys.platform.startswith("linux"), "Owned sessions and process groups")
class RunExportTests(unittest.TestCase):
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

    def run_export(self, scenario=None, limits=LIMITS, cameras=(0, 1), timeout=None, stop=None, on_frame=None):
        frames = []
        extra = ("--fake-scenario", scenario) if scenario else ()
        report = camera_helper.run_export(self.binary, limits, cameras, on_frame=on_frame or frames.append,
                                          extra_args=extra, timeout=timeout, stop=stop)
        return report, frames

    def test_a_bounded_run_exports_every_frame_and_confirms_cleanup(self):
        started = time.monotonic()
        report, frames = self.run_export()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual((report["status"], report["cleanup_ok"], report["frames_read"]), ("exported", True, 5))
        self.assertEqual([frame.sequence for frame in frames], [1, 2, 3, 4, 5])
        self.assertEqual([frame.source for frame in frames][:2], ["arcturus_camera_left", "arcturus_camera_right"])
        self.assertEqual(report["helper_exit_code"], 0)
        self.assertEqual(report["supervision"]["status"], "helper_exited")
        self.assertTrue(report["supervision"]["worker_reaped"] and report["supervision"]["worker_tree_reaped"])
        self.assertFalse(report["supervision"]["worker_forced_termination"])
        self.assertFalse(report["supervision"]["worker_armed"])  # The trampoline closes the arm descriptor.
        self.assertEqual(report["stream"]["end"]["reason"], "max_frames")
        self.assertEqual(report["stream"]["ready"]["limits"], LIMITS)
        self.assertEqual(report["helper"]["path"], str(self.binary.resolve()))
        self.assertEqual(len(report["helper"]["sha256"]), 64)
        self.assertEqual(report["helper"]["arguments"],
                         ["--export-frames", "--fps", "10", "--max-seconds", "3", "--max-frames", "5", "--cameras", "0,1"])
        self.assertEqual(report["diagnostics"]["line_count"], 2)
        self.assertEqual([line["event"] for line in report["diagnostics"]["lines"]], ["ready", "final"])
        self.assertEqual(report["diagnostics"]["non_json_lines"], 0)
        self.assertFalse(report["pixels_in_report"])
        self.assertNotIn(repr(frames[0].pixels[:12])[2:-1], json.dumps(report))
        json.dumps(report)  # Serializable.
        # Frames are recent on the host clock: the stand-in stamps real monotonic time.
        self.assertLess(abs(time.monotonic_ns() - frames[-1].observed_ns), 5_000_000_000)

    def test_the_time_bound_ends_the_run_before_the_frame_bound(self):
        report, frames = self.run_export(limits={"fps": 2, "max_seconds": 1, "max_frames": 300})
        self.assertEqual(report["status"], "exported")
        self.assertEqual(report["stream"]["end"]["reason"], "max_seconds")
        self.assertLessEqual(len(frames), 3)

    def test_one_camera_and_a_large_frame(self):
        frames = []
        report = camera_helper.run_export(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 2}, (1,),
                                          on_frame=frames.append,
                                          extra_args=("--width", "1920", "--height", "1080", "--stride", "5760"))
        self.assertEqual(report["status"], "exported")
        self.assertEqual([frame.source for frame in frames], ["arcturus_camera_right"] * 2)
        self.assertEqual((frames[0].width, frames[0].height, frames[0].stride, len(frames[0].pixels)),
                         (1920, 1080, 5760, 5760 * 1080))

    def test_every_invalid_stream_is_refused_and_the_helper_is_stopped_cleanly(self):
        for name, (_, expected) in sorted(fake_export.INVALID.items()):
            with self.subTest(stream=name):
                report, frames = self.run_export(f"invalid:{name}", limits=CATALOGUE_LIMITS)
                self.assertEqual(report["status"], "refused", report.get("reason"))
                self.assertEqual(report["reason"], expected)
                self.assertTrue(report["cleanup_ok"], report["supervision"])
                self.assertFalse(report["supervision"]["worker_forced_termination"])
                self.assertEqual(report["stream"]["refusal"]["reason"], expected)

    def test_a_refusal_mid_stream_stops_a_running_helper_through_the_primitive(self):
        started = time.monotonic()
        report, frames = self.run_export("wrong_limits", limits={"fps": 2, "max_seconds": 30, "max_frames": 299},
                                         timeout=40)
        self.assertLess(time.monotonic() - started, camera_helper.HELPER_GRACE.total() + 2)
        self.assertEqual((report["status"], report["reason"]), ("refused", "limits_differ_from_requested"))
        self.assertEqual(report["supervision"]["status"], "stopped")
        self.assertTrue(report["supervision"]["worker_termination_requested"])
        self.assertFalse(report["supervision"]["worker_forced_termination"])
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["helper_exit_code"], 130)  # It saw the SIGTERM and ended on stop_signal.
        self.assertEqual(frames, [])

    def test_a_helper_that_leaves_without_its_end_packet(self):
        report, frames = self.run_export("no_end")
        self.assertEqual((report["status"], report["reason"]), ("refused", "stream_ended_without_end"))
        self.assertEqual(len(frames), 5)
        self.assertTrue(report["cleanup_ok"])

    def test_a_helper_that_refuses_or_exits_silently_is_its_failure_not_the_streams(self):
        report, _ = self.run_export("refuse")
        self.assertEqual((report["status"], report["reason"], report["helper_exit_code"]), ("error", "helper_exit_code", 5))
        self.assertEqual(report["stream"]["refusal"]["reason"], "stream_ended_without_ready")
        self.assertEqual(report["diagnostics"]["lines"][-1]["reason"], "confirmation_required")
        report, _ = self.run_export("silent_exit")
        self.assertEqual((report["status"], report["reason"], report["helper_exit_code"]), ("error", "helper_exit_code", 2))
        self.assertEqual(report["diagnostics"]["line_count"], 0)

    def test_a_helper_that_ends_its_stream_but_exits_nonzero(self):
        report, frames = self.run_export("exit_failure")
        self.assertEqual((report["status"], report["reason"], report["helper_exit_code"]), ("error", "helper_exit_code", 1))
        self.assertEqual(report["stream"]["status"], "complete")
        self.assertEqual(len(frames), 5)

    def test_an_early_end_reason_is_reported_as_the_helpers(self):
        report, frames = self.run_export("headset_removed")
        self.assertEqual((report["status"], report["reason"]), ("helper_ended_early", "headset_removed"))
        self.assertEqual(len(frames), 2)
        self.assertTrue(report["cleanup_ok"])

    def test_the_deadline_ends_a_hanging_helper(self):
        started = time.monotonic()
        report, frames = self.run_export("hang", timeout=1.5)
        self.assertEqual((report["status"], report["reason"]), ("timed_out", "helper_deadline"))
        self.assertFalse(report["cleanup_ok"])  # A deadline never confirms cleanup.
        self.assertTrue(report["supervision"]["worker_tree_reaped"])
        self.assertFalse(report["supervision"]["worker_forced_termination"])  # It honoured SIGTERM.
        self.assertLess(time.monotonic() - started, 1.5 + camera_helper.HELPER_GRACE.total() + 2)

    def test_a_deaf_helper_is_forced_and_cleanup_is_not_confirmed(self):
        report, _ = self.run_export("hang_deaf", timeout=1.0)
        self.assertEqual(report["status"], "timed_out")
        self.assertTrue(report["supervision"]["worker_forced_termination"])
        self.assertTrue(report["supervision"]["worker_tree_reaped"])
        self.assertFalse(report["cleanup_ok"])

    def test_the_callers_stop_ends_the_helper(self):
        stop = threading.Event()
        timer = threading.Timer(0.5, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        report, frames = self.run_export(limits={"fps": 4, "max_seconds": 30, "max_frames": 300}, stop=stop, timeout=40)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "stop_requested"))
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["helper_exit_code"], 130)
        self.assertLess(len(frames), 8)

    def test_a_signal_cancels_the_run_and_the_helper_ends_on_stop_signal(self):
        timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        report, frames = self.run_export(limits={"fps": 4, "max_seconds": 30, "max_frames": 300}, timeout=40)
        self.assertEqual((report["status"], report["reason"], report["signal"]), ("cancelled", "signal", signal.SIGTERM))
        self.assertEqual(report["supervision"]["exit_code"], 128 + signal.SIGTERM)
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["stream"]["end"]["reason"], "stop_signal")

    def test_stderr_is_collected_bounded_and_never_raw(self):
        report, _ = self.run_export("stderr_noise")
        self.assertEqual(report["status"], "exported")
        logged = report["diagnostics"]
        self.assertEqual(logged["line_count"], camera_helper.MAX_DIAGNOSTIC_LINES)
        self.assertEqual(logged["non_json_lines"], 1)
        self.assertEqual(logged["oversize_lines"], 1)
        self.assertGreater(logged["dropped_lines"], 0)
        self.assertNotIn("plain text", json.dumps(report))
        self.assertTrue(all(isinstance(line, dict) for line in logged["lines"]))

    def test_a_failing_frame_handler_stops_the_helper_and_is_reported(self):
        def handler(frame):
            raise RuntimeError("handler broke")
        report, _ = self.run_export(limits={"fps": 4, "max_seconds": 30, "max_frames": 300}, timeout=40,
                                    on_frame=handler)
        self.assertEqual((report["status"], report["reason"]), ("error", "frame_handler_failed"))
        self.assertIn("handler broke", report["error"])
        self.assertTrue(report["cleanup_ok"])

    def test_limits_and_cameras_are_checked_before_launch(self):
        for limits in ({"fps": 11, "max_seconds": 1, "max_frames": 1}, {"fps": 1, "max_seconds": 61, "max_frames": 1},
                       {"fps": 1, "max_seconds": 1, "max_frames": 601}, {"fps": 1, "max_seconds": 1},
                       {"fps": True, "max_seconds": 1, "max_frames": 1}, "fast"):
            with self.subTest(limits=limits):
                report = camera_helper.run_export(self.binary, limits, (0,), on_frame=lambda frame: None)
                self.assertEqual(report["status"], "error")
                self.assertTrue(report["reason"].startswith("camera_limit"), report["reason"])
                self.assertIsNone(report["supervision"])
        for cameras in ((), (2,), (0, 0), (1, 0), ("0",)):
            with self.subTest(cameras=cameras):
                report = camera_helper.run_export(self.binary, LIMITS, cameras, on_frame=lambda frame: None)
                self.assertEqual((report["status"], report["reason"]), ("error", "camera_selection_invalid"))


class HelperPathTests(unittest.TestCase):
    def test_the_path_must_be_an_existing_executable_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "none"
            with self.assertRaises(SightCircuitError) as caught:
                camera_helper.helper_path(missing)
            self.assertEqual(caught.exception.reason, "helper_not_found")
            plain = Path(temporary) / "plain"
            plain.write_text("#!/bin/sh\n")
            with self.assertRaises(SightCircuitError) as caught:
                camera_helper.helper_path(plain)
            self.assertEqual(caught.exception.reason, "helper_not_executable")
            plain.chmod(plain.stat().st_mode | stat.S_IXUSR)
            self.assertEqual(camera_helper.helper_path(plain), os.path.realpath(plain))
            with self.assertRaises(SightCircuitError) as caught:
                camera_helper.helper_path(temporary)
            self.assertEqual(caught.exception.reason, "helper_not_found")
        for empty in ("", None, 3):
            with self.subTest(path=empty), self.assertRaises(SightCircuitError) as caught:
                camera_helper.helper_path(empty)
            self.assertEqual(caught.exception.reason, "helper_path_missing")

    def test_a_path_inside_the_work_tree_is_refused_except_under_artifacts(self):
        tracked = ROOT / "scripts/camera_recognize.py"
        self.assertTrue(tracked.is_file())
        with self.assertRaises(SightCircuitError) as caught:
            camera_helper.helper_path(tracked)
        self.assertEqual(caught.exception.reason, "helper_inside_work_tree")
        (ROOT / "artifacts").mkdir(exist_ok=True)  # Ignored by git; the smoke check writes here too.
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            built = Path(temporary) / "sightcircuit-camera-helper"
            built.write_text("#!/bin/sh\n")
            built.chmod(0o755)
            self.assertEqual(camera_helper.helper_path(built), str(built.resolve()))
        # A symbolic link outside the tree that points into it is resolved and refused.
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "helper"
            link.symlink_to(tracked)
            with self.assertRaises(SightCircuitError) as caught:
                camera_helper.helper_path(link)
            self.assertEqual(caught.exception.reason, "helper_inside_work_tree")

    def test_the_ignored_artifacts_directory_is_the_one_exception(self):
        self.assertEqual(camera_helper.TREE_EXCEPTION, "artifacts")
        self.assertIn("/artifacts/", (ROOT / ".gitignore").read_text())


@unittest.skipUnless(sys.platform.startswith("linux"), "execv and descriptor plumbing")
class ExecHelperTests(unittest.TestCase):
    def test_the_trampoline_announces_redirects_and_execs(self):
        frames_read, frames_write = os.pipe()
        diagnostics_read, diagnostics_write = os.pipe()
        code = ("import os, sys; sys.path.insert(0, sys.argv[1]); os.environ['SIGHTCIRCUIT_ARMED_FD'] = '99'; "
                "os.environ['SIGHTCIRCUIT_SUPERVISOR_PID'] = '1'; "
                "from sightcircuit.sources.camera_helper import exec_helper; exec_helper(sys.argv[2:])")
        script = ("import os, sys; sys.stdout.write('frames'); sys.stderr.write('diagnostics'); "
                  "sys.stdout.flush(); sys.stderr.flush(); "
                  "sys.exit(3 if 'SIGHTCIRCUIT_SUPERVISOR_PID' in os.environ else 0)")
        completed = subprocess.run([sys.executable, "-c", code, str(ROOT), str(frames_write), str(diagnostics_write),
                                    sys.executable, "-c", script],
                                   pass_fds=(frames_write, diagnostics_write), capture_output=True, timeout=30)
        os.close(frames_write)
        os.close(diagnostics_write)
        try:
            self.assertEqual(json.loads(completed.stdout)["report_kind"], "camera_helper_exec")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(os.read(frames_read, 64), b"frames")
            self.assertEqual(os.read(diagnostics_read, 64), b"diagnostics")
        finally:
            os.close(frames_read)
            os.close(diagnostics_read)

    def test_a_failed_exec_writes_one_diagnostic_line_and_exits_127(self):
        frames_read, frames_write = os.pipe()
        diagnostics_read, diagnostics_write = os.pipe()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from sightcircuit.sources.camera_helper import exec_helper; exec_helper(sys.argv[2:])")
        completed = subprocess.run([sys.executable, "-c", code, str(ROOT), str(frames_write), str(diagnostics_write),
                                    "/nonexistent/helper", "--export-frames"],
                                   pass_fds=(frames_write, diagnostics_write), capture_output=True, timeout=30)
        os.close(frames_write)
        os.close(diagnostics_write)
        try:
            self.assertEqual(completed.returncode, camera_helper.EXEC_FAILED_EXIT)
            line = json.loads(os.read(diagnostics_read, 4096))
            self.assertEqual(line["event"], "exec_failed")
            self.assertEqual(os.read(frames_read, 1), b"")
        finally:
            os.close(frames_read)
            os.close(diagnostics_read)


class CommandLineTests(unittest.TestCase):
    def test_the_helper_command_carries_the_contract_arguments(self):
        self.assertEqual(camera_helper.helper_command("/h", {"fps": 5, "max_seconds": 30, "max_frames": 300}, (0,),
                                                      ("--fake-scenario", "x")),
                         ["/h", "--export-frames", "--fps", "5", "--max-seconds", "30", "--max-frames", "300",
                          "--cameras", "0", "--fake-scenario", "x"])
        self.assertEqual(camera_helper.check_limits({"max_frames": 1, "fps": 1, "max_seconds": 1}),
                         {"fps": 1, "max_seconds": 1, "max_frames": 1})
        self.assertEqual(camera_helper.check_cameras([0, 1]), (0, 1))
        self.assertEqual(camera_helper.helper_command("/h", camera_helper.check_limits(
            {name: high for name, (_, high) in contract.LIMITS.items()}), (0, 1))[3::2][:3], ["10", "60", "600"])


if __name__ == "__main__":
    unittest.main()
