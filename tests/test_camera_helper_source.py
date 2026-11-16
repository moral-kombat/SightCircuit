"""The camera helper source against the executable stand-in: launch, bounds, refusals, stops and the report.

The stand-in (``tests/fake_camera_export.py``) is installed outside the
checkout and launched through the supervision primitive exactly as the real
helper would be. Standard library only; Linux (owned sessions).
"""
import contextlib
import io
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import contract, fake_export  # noqa: E402
from sightcircuit import pipeline as pipeline_module  # noqa: E402
from sightcircuit.camera.export_reader import ExportReader  # noqa: E402
from sightcircuit.context import CaptureContext  # noqa: E402
from sightcircuit.errors import SightCircuitError  # noqa: E402
from sightcircuit.frame import Detection, DetectionResult  # noqa: E402
from sightcircuit.latest import LatestSlot  # noqa: E402
from sightcircuit.pipeline import Pipeline  # noqa: E402
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

    def run_export(self, scenario=None, limits=LIMITS, cameras=(0, 1), timeout=None, stop=None, on_frame=None,
                   clock=None):
        frames = []
        extra = (("--fake-scenario", scenario) if scenario else ()) + (("--fake-clock", clock) if clock else ())
        report = camera_helper.run_export(self.binary, limits, cameras, on_frame=on_frame or frames.append,
                                          extra_args=extra, timeout=timeout, stop=stop, confirmed=True)
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
                         ["--export-frames", "--fps", "10", "--max-seconds", "3", "--max-frames", "5", "--cameras", "0,1",
                          "--parent-confirmed"])
        self.assertTrue(report["helper"]["parent_confirmed"])
        self.assertEqual([frame.metadata["frame_set_sequence"] for frame in frames], [1, 1, 2, 2, 3])
        self.assertEqual((report["stream"]["ready"]["confirmation"], report["stream"]["end"]["confirmation"]),
                         ("parent", "parent"))
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

    def test_ninety_seconds_at_five_frames_a_second_is_450_frames_ending_on_the_time_bound(self):
        """Contract v2's full time bound on the stand-in's virtual clock: nothing here sleeps 90 s."""
        limits = {"fps": 5, "max_seconds": contract.LIMITS["max_seconds"][1], "max_frames": contract.LIMITS["max_frames"][1]}
        self.assertEqual(limits, {"fps": 5, "max_seconds": 90, "max_frames": 900})
        sequences = []
        started = time.monotonic()
        report, _ = self.run_export(limits=limits, cameras=(0,), clock="virtual",
                                    on_frame=lambda frame: sequences.append(frame.sequence))
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((report["status"], report["cleanup_ok"], report["frames_read"]), ("exported", True, 450))
        self.assertEqual(sequences, list(range(1, 451)))
        self.assertEqual(report["protocol"], "camera-export/2")
        self.assertEqual((report["stream"]["end"]["reason"], report["stream"]["end"]["counts"]["exported"]),
                         ("max_seconds", 450))
        self.assertEqual(report["stream"]["ready"]["limits"], limits)
        self.assertEqual(report["helper"]["arguments"][1:6], ["--fps", "5", "--max-seconds", "90", "--max-frames"])
        # The frame bound, the other way: 900 frames at 10 a second are 90 s of periods, and the frame bound ends it.
        report, _ = self.run_export(limits=dict(limits, fps=10), cameras=(0,), clock="virtual", on_frame=lambda frame: None)
        self.assertEqual((report["status"], report["frames_read"], report["stream"]["end"]["reason"]),
                         ("exported", 900, "max_frames"))

    def test_a_version_1_helper_is_refused_by_name_and_stopped(self):
        report, frames = self.run_export("invalid:version_1_helper", limits=CATALOGUE_LIMITS)
        self.assertEqual((report["status"], report["reason"], frames), ("refused", "unsupported_version", []))
        self.assertIn("declares camera export version 1", report["error"])
        self.assertIn("accepts only version 2 (camera-export/2)", report["error"])
        self.assertIn("rebuild the helper", report["error"])
        self.assertTrue(report["cleanup_ok"])

    def test_one_camera_and_a_large_frame(self):
        frames = []
        report = camera_helper.run_export(self.binary, {"fps": 10, "max_seconds": 5, "max_frames": 2}, (1,),
                                          on_frame=frames.append, confirmed=True,
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

    def test_without_the_parents_confirmation_the_helper_refuses_and_nothing_is_exported(self):
        frames = []
        report = camera_helper.run_export(self.binary, LIMITS, (0, 1), on_frame=frames.append)
        self.assertEqual((report["status"], report["reason"], report["helper_exit_code"]), ("error", "helper_exit_code", 5))
        self.assertFalse(report["helper"]["parent_confirmed"])
        self.assertNotIn("--parent-confirmed", report["helper"]["arguments"])
        self.assertEqual(report["diagnostics"]["lines"][-1]["reason"], "confirmation_required")
        self.assertEqual(frames, [])

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

    def test_a_run_ended_before_the_pixel_path_was_chosen_is_the_helpers_early_end(self):
        report, frames = self.run_export("undecided")
        self.assertEqual((report["status"], report["reason"], report["frames_read"]), ("helper_ended_early", "watchdog", 0))
        self.assertEqual(report["stream"]["ready"]["pixel_path"], "undecided")
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(frames, [])

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
        for limits in ({"fps": 11, "max_seconds": 1, "max_frames": 1}, {"fps": 1, "max_seconds": 91, "max_frames": 1},
                       {"fps": 1, "max_seconds": 1, "max_frames": 901}, {"fps": 1, "max_seconds": 1},
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


class DiagnosticsTests(unittest.TestCase):
    def collect(self, *writes):
        read_fd, write_fd = os.pipe()
        thread = camera_helper.Diagnostics(read_fd)
        thread.start()
        for data in writes:
            os.write(write_fd, data)
        os.close(write_fd)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        return thread.report()

    def test_an_oversize_unterminated_line_counts_once_including_its_tail(self):
        report = self.collect(b"x" * 70000, b"y" * 70000 + b"\n", b'{"event": "after"}\n')
        self.assertEqual((report["oversize_lines"], report["line_count"], report["non_json_lines"]), (1, 1, 0))
        self.assertEqual(report["lines"], [{"event": "after"}])

    def test_a_terminated_oversize_line_and_a_trailing_fragment(self):
        report = self.collect(b"z" * 9000 + b"\n", b'{"event": "one"}\n', b'{"event": "tail"}')
        self.assertEqual((report["oversize_lines"], report["line_count"]), (1, 2))
        self.assertEqual([line["event"] for line in report["lines"]], ["one", "tail"])
        report = self.collect(b"q" * 70000)  # Unterminated at end of stream: one oversize line, nothing else.
        self.assertEqual((report["oversize_lines"], report["line_count"], report["non_json_lines"]), (1, 0, 0))


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
        self.assertEqual(camera_helper.helper_command("/h", {"fps": 5, "max_seconds": 30, "max_frames": 300}, (0, 1),
                                                      confirmed=True)[-3:], ["--cameras", "0,1", "--parent-confirmed"])
        self.assertEqual(camera_helper.check_limits({"max_frames": 1, "fps": 1, "max_seconds": 1}),
                         {"fps": 1, "max_seconds": 1, "max_frames": 1})
        self.assertEqual(camera_helper.check_cameras([0, 1]), (0, 1))
        self.assertEqual(camera_helper.helper_command("/h", camera_helper.check_limits(
            {name: high for name, (_, high) in contract.LIMITS.items()}), (0, 1))[3::2][:3], ["10", "90", "900"])


class StandInDetector:
    """Finds one bottle in every frame without looking at a pixel; records what it was given and when."""
    name, runtime, classes = "stand-in", "none", ("bottle",)

    def __init__(self, fail_on=None):
        self.frames, self.at, self.fail_on = [], [], fail_on

    def detect(self, frame, threshold):
        self.frames.append(frame)
        self.at.append(time.monotonic())
        if self.fail_on == len(self.frames):
            raise RuntimeError("stand-in detector failed")
        return DetectionResult((Detection("bottle", 0.9, (1, 2, 30, 40)),), 1.0)


class RecordingSink:
    """Keeps what was submitted and when, and when it was cleared (the label withdrawn)."""

    def __init__(self):
        self.processed, self.submitted_at, self.cleared_at = [], [], []

    def submit(self, processed):
        self.processed.append(processed)
        self.submitted_at.append(time.monotonic())

    def clear(self):
        self.cleared_at.append(time.monotonic())


class RecordingSlot(LatestSlot):
    """The pipeline's slot, kept so a test can look into it after the run."""
    made = []

    def __init__(self):
        super().__init__()
        self.put_at, self.discarded_at = [], None
        RecordingSlot.made.append(self)

    def put(self, item):
        self.put_at.append(time.monotonic())
        super().put(item)

    def discard(self):
        if self.discarded_at is None:
            self.discarded_at = time.monotonic()
        super().discard()


class RecordingStop(threading.Event):
    """A stop event that remembers when it was first set."""

    def __init__(self):
        super().__init__()
        self.set_at = None

    def set(self):
        if self.set_at is None:
            self.set_at = time.monotonic()
        super().set()


def walk(value, path="report"):
    """Every (path, key, value) of a nested report."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key, item
            yield from walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield path, index, item
            yield from walk(item, f"{path}[{index}]")


def alive(pid):
    """Whether process ``pid`` exists (a reaped helper does not)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@unittest.skipUnless(sys.platform.startswith("linux"), "Owned sessions and process groups")
class CameraHelperSourceTests(unittest.TestCase):
    """``CameraHelperSource`` (work-order item L5) through ``Pipeline(latest_only=True, max_input_age_ms=1000)``.

    The stand-in is launched through the supervision primitive on the main
    thread (``drive``) while the pipeline runs on a worker thread, with the
    pipeline's default ``join_timeout``. The detector and the conversion are
    stand-ins, so no pixel is converted and the tests need the standard
    library only. ``self.end`` records what was true when the source accepted
    the end packet, and ``self.pid`` is the helper's process id.
    """
    FULL = {"fps": 10, "max_seconds": 90, "max_frames": 900}
    SLOW = {"fps": 2, "max_seconds": 3, "max_frames": 5}  # Two frames 500 ms apart in the ``end:`` scenarios.

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
        self.end, self.pid = {}, None

    def source(self, scenario=None, limits=LIMITS, fake=(), **options):
        """A source on the stand-in; ``fake`` are further stand-in options, e.g. ``("--fake-linger", 3)``."""
        extra = (("--fake-scenario", scenario) if scenario else ()) + ("--fake-report-pid",) + tuple(fake)
        self.end, self.pid = {}, None
        source = camera_helper.CameraHelperSource(self.binary, limits, confirmed=True, extra_args=extra, **options)
        on_ready, on_end = source._on_ready, source._on_end

        def ready(header):
            self.pid = header["build"].get("fake_pid")
            on_ready(header)

        def end(header):
            on_end(header)
            self.end = {"at": time.monotonic(), "stop_set": source._stop.is_set(), "helper_alive": self.pid is not None and alive(self.pid),
                        "export_returned": source.export is not None}
        source._on_ready, source._on_end = ready, end
        return source

    def pipeline(self, source, detector=None, **options):
        self.detector, self.sink = detector or StandInDetector(), RecordingSink()
        options.setdefault("max_fps", 10)
        return Pipeline(source, self.detector, [self.sink], latest_only=True, max_input_age_ms=1000,
                        convert=lambda frame: frame, annotator=lambda frame, result: None, **options)

    def drive(self, source, stop=None, **options):
        """``(stats, error)``: the pipeline's statistics, and what ``drive`` raised (None for a clean run)."""
        pipeline = self.pipeline(source, **options)
        self.assertEqual(pipeline.join_timeout, 1.0)  # Nothing below depends on a long capture-thread join.
        try:
            source.drive(pipeline, threading.Event() if stop is None else stop)
        except Exception as error:
            return pipeline.stats, error
        finally:
            self.returned_at = time.monotonic()
            self.capture_thread_alive = pipeline.capture_thread_alive
        return pipeline.stats, None

    def assert_helper_gone(self, source):
        """The helper process no longer exists and the primitive confirmed its tree."""
        supervision = source.report()["export"]["supervision"]
        self.assertFalse(alive(supervision["worker_pid"]))
        if self.pid is not None:
            self.assertEqual(self.pid, supervision["worker_pid"])  # The trampoline became the helper.
        self.assertTrue(supervision["worker_reaped"] and supervision["worker_tree_reaped"])

    # -- identity ---------------------------------------------------------------------------------------------------

    def test_the_source_is_the_left_camera_only(self):
        source = self.source()
        self.assertEqual((source.kind, source.source_id), ("arcturus_camera", "arcturus_camera_left"))
        self.assertEqual((camera_helper.SOURCE_KIND, camera_helper.LEFT_CAMERA), ("arcturus_camera", 0))
        context = CaptureContext(source)
        self.assertEqual((context.declared("kind"), context.declared("source_id")),
                         ("arcturus_camera", "arcturus_camera_left"))
        for cameras in ((0, 1), (1,)):
            with self.subTest(cameras=cameras), self.assertRaises(SightCircuitError) as caught:
                camera_helper.CameraHelperSource(self.binary, LIMITS, cameras=cameras, confirmed=True)
            self.assertEqual(caught.exception.reason, "camera_source_left_only")
        for cameras in ((), (2,), (0, 0)):
            with self.subTest(cameras=cameras), self.assertRaises(ValueError):
                camera_helper.CameraHelperSource(self.binary, LIMITS, cameras=cameras)
        with self.assertRaises(ValueError):
            camera_helper.CameraHelperSource(self.binary, dict(LIMITS, max_seconds=91))
        # The pinned context refuses a right-camera frame on its own, whatever the source checks.
        right = list(ExportReader(io.BytesIO(fake_export.valid_stream(1, cameras=(1,)))).frames())[0]
        self.assertEqual(right.source, "arcturus_camera_right")
        with self.assertRaisesRegex(ValueError, "source_or_session_changed"):
            context.admit(right)
        # And so does the source's own frame check, should a right-camera frame ever pass the reader.
        emitted = []
        source._emit, source._stop = emitted.append, threading.Event()
        with self.assertRaises(SightCircuitError) as caught:
            source._on_frame(right)
        self.assertEqual((caught.exception.reason, source.frames_refused, emitted), ("camera_frame_not_left", 1, []))

    def test_a_long_single_camera_stream_is_admitted(self):
        """The contract's full frame bound, 900 frames of the left camera, on the stand-in's virtual clock."""
        source = self.source(limits=self.FULL, fake=("--fake-clock", "virtual"))
        started = time.monotonic()
        stats, error = self.drive(source)
        self.assertLess(time.monotonic() - started, 60)
        self.assertIsNone(error)
        self.assertEqual((source.status, source.reason, source.end_reason, source.cleanup_ok),
                         ("completed", None, "max_frames", True))
        self.assertEqual((source.frames_emitted, source.frames_refused, source.frames_after_stop, stats.frames_received),
                         (900, 0, 0, 900))
        self.assertGreaterEqual(stats.frames_processed, 1)
        self.assertEqual(stats.frames_received, stats.frames_processed + stats.pending_replaced + stats.stale_inputs_skipped)
        frames = [processed.frame for processed in self.sink.processed]
        self.assertEqual({frame.source for frame in frames}, {"arcturus_camera_left"})
        sequences = [frame.sequence for frame in frames]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertLessEqual(sequences[-1], 900)
        report = source.report()
        self.assertEqual((report["kind"], report["source_id"], report["camera_index"], report["protocol"]),
                         ("arcturus_camera", "arcturus_camera_left", 0, "camera-export/2"))
        self.assertEqual(report["export"]["stream"]["frames_per_camera"],
                         {"arcturus_camera_left": 900, "arcturus_camera_right": 0})
        self.assertEqual(report["export"]["stream"]["ready"]["cameras"], [0])
        self.assertEqual(report["export"]["helper"]["arguments"][7:9], ["--cameras", "0"])
        self.assertFalse(self.capture_thread_alive)
        self.assert_helper_gone(source)

    def test_observed_ns_is_the_acquire_time_and_the_exposure_offset_is_only_a_timing(self):
        source = self.source()
        before = time.monotonic_ns()
        stats, error = self.drive(source)
        after = time.monotonic_ns()
        self.assertIsNone(error)
        self.assertEqual((source.status, source.end_reason, source.frames_emitted), ("completed", "max_frames", 5))
        for processed in self.sink.processed:
            frame = processed.frame
            self.assertTrue(before <= frame.observed_ns <= after)  # The stand-in stamps the host monotonic clock.
            self.assertEqual(frame.metadata["exposure_ns"], frame.observed_ns - 3_000_000)  # Kept, never applied.
        timing = source.report()["timings"]
        self.assertEqual(list(timing), ["exposure_minus_acquire_ms"])
        offset = timing["exposure_minus_acquire_ms"]
        self.assertEqual((offset["count"], offset["p50"], offset["min"], offset["max"]), (5, -3.0, -3.0, -3.0))
        self.assertEqual(offset["percentile_method"], "nearest_rank")
        self.assertIn("never applied", offset["definition"])
        self.assertNotIn("calib", json.dumps(source.report()).lower())
        empty = self.source().report()
        self.assertEqual(empty["timings"]["exposure_minus_acquire_ms"]["count"], 0)
        self.assertIsNone(empty["timings"]["exposure_minus_acquire_ms"]["p50"])
        self.assertEqual((empty["status"], empty["export"], empty["presence_ended"]), ("not_started", None, False))

    # -- refusals ---------------------------------------------------------------------------------------------------

    def test_a_ready_naming_another_camera_is_refused_before_any_frame(self):
        """The stand-in exports the right camera, or both, whatever was asked: refused at ``ready``, nothing emitted."""
        for scenario, cameras in (("right_camera", [1]), ("both_cameras", [0, 1])):
            with self.subTest(scenario=scenario):
                source = self.source(scenario, limits={"fps": 10, "max_seconds": 20, "max_frames": 50})
                started = time.monotonic()
                stats, error = self.drive(source)
                self.assertLess(time.monotonic() - started, 5)  # The helper was stopped, not left to its bound.
                self.assertIsInstance(error, SightCircuitError)
                self.assertEqual(error.reason, "camera_selection_not_honoured")
                self.assertEqual((source.status, source.reason, source.frames_emitted, source.frames_refused),
                                 ("error", "camera_selection_not_honoured", 0, 0))
                self.assertEqual((stats.frames_received, stats.frames_processed, self.detector.frames), (0, 0, []))
                self.assertEqual(self.sink.processed, [])
                self.assertEqual(source.report()["export"]["stream"]["ready"]["cameras"], cameras)
                self.assertTrue(source.cleanup_ok)
                self.assert_helper_gone(source)

    def test_run_pipeline_still_refuses_the_source(self):
        """No gate is opened: the live runner's image gate refuses the kind before anything is launched."""
        from sightcircuit import gates
        from sightcircuit.runtime import live, synthetic
        source = self.source()
        with mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("launched")) as launched, \
                self.assertRaisesRegex(ValueError, "^live_image_source_not_approved$"):
            live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.1)
        launched.assert_not_called()
        with self.assertRaisesRegex(ValueError, "^live_image_source_not_approved$"):
            live.require_synthetic(source, "live_image_source_not_approved")
        self.assertEqual(source.status, "not_started")
        self.assertFalse(any(gates.claims().values()))

    def test_either_half_alone_launches_nothing(self):
        source = self.source(attach_seconds=0.05)
        with mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("launched")):
            report = source.supervise()
        self.assertEqual((report["status"], report["reason"], report["export"], report["cleanup_ok"]),
                         ("error", "camera_source_not_attached", None, True))
        with self.assertRaises(ValueError):
            source.drive(self.pipeline(self.source()), threading.Event())  # A pipeline built on another source.
        # And the other half alone: a pipeline run with nothing supervising ends as the source's error, unlaunched.
        source = self.source(attach_seconds=0.05)
        with mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("launched")), \
                self.assertRaises(SightCircuitError) as caught:
            self.pipeline(source).run(threading.Event())
        self.assertEqual((caught.exception.reason, source.status), ("camera_source_not_supervised", "error"))

    # -- how a run ends ---------------------------------------------------------------------------------------------

    def test_each_end_reason_maps_to_its_status(self):
        expected = {"max_seconds": ("completed", None), "max_frames": ("completed", None),
                    "headset_removed": ("presence_ended", "headset_removed"), "standby": ("presence_ended", "standby"),
                    "sdk_error": ("error", "sdk_error"), "pixel_path_error": ("error", "pixel_path_error"),
                    "watchdog": ("error", "watchdog"), "consumer_stalled": ("error", "consumer_stalled")}
        self.assertEqual(set(expected) | {"stop_signal"}, set(contract.END_REASONS))
        for end_reason, (status, reason) in expected.items():
            with self.subTest(end_reason=end_reason):
                source = self.source(f"end:{end_reason}")
                stats, error = self.drive(source)
                self.assertEqual((source.status, source.reason, source.end_reason), (status, reason, end_reason))
                self.assertEqual(source.frames_emitted, 2)
                self.assertTrue(source.cleanup_ok)
                report = source.report()
                self.assertEqual((report["status"], report["end_reason"], report["presence_ended"], report["retried"]),
                                 (status, end_reason, status == "presence_ended", False))
                self.assertEqual(report["export"]["stream"]["end"]["reason"], end_reason)
                self.assertEqual(self.end["stop_set"], status == "presence_ended")  # Only a presence end stops the run.
                if status == "error":
                    self.assertIsInstance(error, SightCircuitError)
                    self.assertEqual(error.reason, end_reason)
                else:
                    self.assertIsNone(error)
                self.assert_helper_gone(source)
        self.assertEqual(camera_helper.COMPLETED_END_REASONS, ("max_frames", "max_seconds"))
        self.assertEqual(camera_helper.PRESENCE_END_REASONS, ("headset_removed", "standby"))

    def test_the_pipelines_stop_is_the_helpers_stop_signal(self):
        """The pipeline's frame limit sets ``stop``; the helper, which would hang, is sent SIGTERM and ends ``stop_signal``."""
        source = self.source("hang", limits={"fps": 10, "max_seconds": 20, "max_frames": 3})
        started = time.monotonic()
        stats, error = self.drive(source, max_frames=1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNone(error)
        self.assertEqual((source.status, source.reason, source.end_reason), ("stopped", "stop_requested", "stop_signal"))
        self.assertEqual(stats.frames_processed, 1)
        report = source.report()
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual((report["export"]["status"], report["helper_exit_code"], report["helper_exit_ok"]),
                         ("stopped", 130, None))
        self.assertTrue(report["export"]["supervision"]["worker_termination_requested"])
        self.assertFalse(report["export"]["supervision"]["worker_forced_termination"])
        self.assert_helper_gone(source)
        # A stop set before the run launches nothing.
        source, stop = self.source(), threading.Event()
        stop.set()
        with mock.patch.object(camera_helper, "run_export", side_effect=AssertionError("launched")):
            stats, error = self.drive(source, stop=stop)
        self.assertIsNone(error)
        self.assertEqual((source.status, source.reason, source.cleanup_ok, stats.frames_received),
                         ("stopped", "stop_requested", True, 0))

    def test_frames_that_arrive_after_the_stop_are_counted_and_not_emitted(self):
        """A 900-frame burst against a pipeline that stops after one frame: every frame read is either emitted
        before the stop or counted after it, and the pipeline received exactly the emitted ones."""
        source = self.source(limits=self.FULL, fake=("--fake-clock", "virtual"))
        stats, error = self.drive(source, max_frames=1)
        self.assertIsNone(error)
        report = source.report()
        self.assertEqual(stats.frames_processed, 1)
        self.assertEqual(report["frames_emitted"], stats.frames_received)
        self.assertEqual(report["frames_emitted"] + report["frames_after_stop"],
                         report["export"]["stream"]["frames_delivered"])
        self.assertIn(report["status"], ("stopped", "completed"))  # The burst may finish before the stop arrives.
        # Directly: with the stop set, a frame is counted and never reaches the pipeline.
        source = self.source()
        emitted, frame = [], list(ExportReader(io.BytesIO(fake_export.valid_stream(1, cameras=(0,)))).frames())[0]
        source._emit, source._stop = emitted.append, threading.Event()
        source._on_frame(frame)
        source._stop.set()
        source._on_frame(frame)
        self.assertEqual((len(emitted), source.frames_emitted, source.frames_after_stop), (1, 1, 1))
        self.assertEqual(source.report()["timings"]["exposure_minus_acquire_ms"]["count"], 1)

    def test_a_stop_clears_the_sinks_without_waiting_for_a_slow_helper(self):
        """The pipeline's 0.6 s deadline against a helper that takes 2 s to honour SIGTERM: the label is withdrawn
        at the stop, not when the helper is finally gone."""
        source = self.source("hang", limits={"fps": 10, "max_seconds": 20, "max_frames": 50},
                             fake=("--fake-term-delay", 2))
        stop = RecordingStop()
        started = time.monotonic()
        stats, error = self.drive(source, stop=stop, seconds=0.6)
        self.assertIsNone(error)
        self.assertGreaterEqual(len(self.sink.processed), 1)
        self.assertEqual(len(self.sink.cleared_at), 1)
        cleared = self.sink.cleared_at[0]
        self.assertLess(cleared - stop.set_at, 0.5)
        self.assertLess(cleared - started, 1.5)
        self.assertGreater(self.returned_at - cleared, 1.2)  # The helper outlived the cleared sink by its slow stop.
        self.assertGreater(self.returned_at - started, 2.0)
        self.assertFalse(self.capture_thread_alive)
        self.assertEqual((source.status, source.end_reason, source.cleanup_ok), ("stopped", "stop_signal", True))
        self.assertFalse(source.report()["export"]["supervision"]["worker_forced_termination"])
        self.assert_helper_gone(source)

    def test_a_source_error_is_raised_by_drive_whatever_the_pipelines_join_timeout(self):
        """A refused stream from a helper that takes 2 s to die, with the pipeline's default one-second join."""
        source = self.source("invalid:bad_magic", limits=CATALOGUE_LIMITS, fake=("--fake-linger", 5, "--fake-term-delay", 2))
        started = time.monotonic()
        stats, error = self.drive(source)
        self.assertGreater(time.monotonic() - started, 2.0)
        self.assertIsInstance(error, SightCircuitError)
        self.assertEqual((error.reason, source.status, source.reason), ("bad_magic", "error", "bad_magic"))
        self.assertFalse(self.capture_thread_alive)
        self.assertTrue(source.cleanup_ok)
        self.assert_helper_gone(source)

    def test_everything_else_is_a_source_error_with_the_helpers_reason(self):
        cases = (("invalid:bad_magic", CATALOGUE_LIMITS, "bad_magic"), ("invalid:version_1_helper", CATALOGUE_LIMITS,
                                                                        "unsupported_version"),
                 ("wrong_limits", LIMITS, "limits_differ_from_requested"), ("no_end", LIMITS, "stream_ended_without_end"),
                 ("refuse", LIMITS, "helper_exit_code"), ("exit_failure", LIMITS, "helper_exit_code"),
                 ("undecided", LIMITS, "watchdog"), ("end:stop_signal", LIMITS, "helper_exit_code"))
        for scenario, limits, reason in cases:
            with self.subTest(scenario=scenario):
                source = self.source(scenario, limits=limits)
                stats, error = self.drive(source)
                self.assertIsInstance(error, SightCircuitError)
                self.assertEqual((error.reason, source.status, source.reason), (reason, "error", reason))
                self.assertFalse(source.report()["presence_ended"])
                self.assert_helper_gone(source)
        source = self.source("hang", limits={"fps": 10, "max_seconds": 1, "max_frames": 2}, timeout=1.5)
        stats, error = self.drive(source)
        self.assertEqual((error.reason, source.status), ("helper_deadline", "error"))
        self.assert_helper_gone(source)

    def test_a_consumer_failure_while_the_helper_exports_stops_the_helper(self):
        """The detector raises on its second frame while the helper would go on for 20 s."""
        source = self.source(limits={"fps": 10, "max_seconds": 20, "max_frames": 200})
        started = time.monotonic()
        stats, error = self.drive(source, detector=StandInDetector(fail_on=2))
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsInstance(error, RuntimeError)  # The pipeline's own error, not the source's.
        self.assertEqual(str(error), "stand-in detector failed")
        self.assertEqual((source.status, source.reason, source.end_reason), ("stopped", "stop_requested", "stop_signal"))
        self.assertEqual((len(self.sink.processed), len(self.sink.cleared_at)), (1, 1))
        self.assertTrue(source.cleanup_ok)
        self.assert_helper_gone(source)

    def test_an_emit_that_raises_is_the_runs_error_and_stops_the_helper(self):
        """The pipeline's admission refuses the third frame as it is published, on the reader thread."""
        def validator(frame):
            if frame.sequence >= 3 and threading.current_thread().name == "camera-helper-frames":
                raise ValueError("admission_refused_for_the_test")
        source = self.source(limits={"fps": 10, "max_seconds": 20, "max_frames": 200})
        started = time.monotonic()
        stats, error = self.drive(source, context_validator=validator)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "admission_refused_for_the_test")
        self.assertEqual((source.status, source.reason, source.frames_emitted), ("error", "admission_refused_for_the_test", 2))
        self.assertEqual(source.report()["export"]["reason"], "frame_handler_failed")
        self.assertLessEqual({processed.frame.sequence for processed in self.sink.processed}, {1, 2})
        self.assertTrue(source.cleanup_ok)
        self.assert_helper_gone(source)

    def test_the_outcome_table(self):
        def export(status, end=None, refusal=None, reason=None):
            return {"status": status, "reason": reason,
                    "stream": {"end": None if end is None else {"reason": end}, "refusal": refusal}}
        outcome = camera_helper.source_outcome
        self.assertEqual(outcome(export("exported", "max_frames"), False), ("completed", None, "max_frames"))
        self.assertEqual(outcome(export("no_frames", "max_seconds"), False), ("completed", None, "max_seconds"))
        self.assertEqual(outcome(export("exported", "stop_signal"), True), ("stopped", "stop_requested", "stop_signal"))
        self.assertEqual(outcome(export("stopped", "stop_signal"), True), ("stopped", "stop_requested", "stop_signal"))
        self.assertEqual(outcome(export("stopped"), True), ("stopped", "stop_requested", None))
        for status in ("helper_ended_early", "stopped", "exported", "cancelled"):  # Presence is read from the end packet.
            for reason in ("headset_removed", "standby"):
                self.assertEqual(outcome(export(status, reason), True), ("presence_ended", reason, reason))
        self.assertEqual(outcome(export("helper_ended_early", "sdk_error"), False), ("error", "sdk_error", "sdk_error"))
        self.assertEqual(outcome(export("cancelled", "stop_signal"), True), ("cancelled", "signal", "stop_signal"))
        self.assertEqual(outcome(export("cancelled"), True), ("cancelled", "signal", None))
        self.assertEqual(outcome(export("timed_out", reason="helper_deadline"), True), ("error", "helper_deadline", None))
        self.assertEqual(outcome(export("timed_out", "standby", reason="helper_deadline"), True),
                         ("error", "helper_deadline", "standby"))
        self.assertEqual(outcome(export("refused", "headset_removed", {"reason": "packet_after_end"}, "packet_after_end"),
                                 True), ("error", "packet_after_end", "headset_removed"))
        self.assertEqual(outcome(export("error", "max_frames", reason="helper_exit_code"), False),
                         ("error", "helper_exit_code", "max_frames"))
        self.assertEqual(outcome({"status": "error", "reason": "helper_not_found"}, False),
                         ("error", "helper_not_found", None))

    # -- presence -----------------------------------------------------------------------------------------------------

    def presence_run(self, *fake, scenario="end:headset_removed"):
        """Two frames 500 ms apart, then the presence end, against a pipeline that starts one frame every five
        seconds: the second frame is pending when the end packet arrives. Returns ``(source, stats, error, slot, stop)``."""
        RecordingSlot.made.clear()
        source, stop = self.source(scenario, limits=self.SLOW, fake=fake), RecordingStop()
        with mock.patch.object(pipeline_module, "LatestSlot", RecordingSlot), \
                mock.patch.object(camera_helper, "run_export", wraps=camera_helper.run_export) as launched:
            self.started = time.monotonic()
            stats, error = self.drive(source, stop=stop, max_fps=0.2)
            self.launched = launched
            slot, = RecordingSlot.made
            # Never retried: the source runs once, and a second attempt launches nothing.
            for again in (source.supervise, lambda: source.run(lambda frame: None, threading.Event()),
                          lambda: source.drive(Pipeline(source, StandInDetector(), latest_only=True, max_input_age_ms=1000),
                                               threading.Event())):
                with self.assertRaises(SightCircuitError) as caught:
                    again()
                self.assertEqual(caught.exception.reason, "camera_source_already_run")
            self.assertEqual(launched.call_count, 1)
        return source, stats, error, slot, stop

    def assert_stopped_at_the_end_packet(self, stats, slot, stop):
        """The stop and the discard happened when the end packet was accepted, with the helper still alive."""
        self.assertEqual(self.end["stop_set"], True)
        self.assertEqual((self.end["helper_alive"], self.end["export_returned"]), (True, False))
        self.assertLessEqual(stop.set_at, self.end["at"])
        self.assertLessEqual(slot.discarded_at, self.end["at"])
        self.assertEqual(len(slot.put_at), 2)
        self.assertLess(stop.set_at - slot.put_at[-1], 0.5)  # The end packet follows the second frame at once.
        self.assertEqual((stats.frames_received, stats.frames_processed, len(self.detector.frames)), (2, 1, 1))
        self.assertEqual([processed.frame.sequence for processed in self.sink.processed], [1])
        self.assertIsNone(slot.item)  # The pending frame is dropped, not left behind.
        self.assertTrue(slot.discarded)
        slot.put(self.detector.frames[0])
        self.assertIsNone(slot.take(timeout=0))  # And nothing can be queued after the run.
        self.assertEqual(len(self.sink.cleared_at), 1)
        self.assertLess(self.sink.cleared_at[0] - stop.set_at, 0.5)  # The label is withdrawn with the stop.

    def test_a_presence_end_acts_on_the_end_packet_while_the_helper_lives_on(self):
        """After its end packet the stand-in stays alive (it would for 5 s) and takes 1.5 s to honour SIGTERM: the
        stop, the dropped frame and the cleared sink all come with the packet, not with the exit."""
        source, stats, error, slot, stop = self.presence_run("--fake-linger", 5, "--fake-term-delay", 1.5)
        self.assertIsNone(error)
        self.assertEqual((source.status, source.reason, source.end_reason), ("presence_ended", "headset_removed",
                                                                             "headset_removed"))
        self.assert_stopped_at_the_end_packet(stats, slot, stop)
        self.assertGreater(self.returned_at - stop.set_at, 1.2)  # The helper went on living that long after the stop.
        self.assertGreater(self.returned_at - self.sink.cleared_at[0], 1.0)
        report = source.report()
        self.assertEqual((report["status"], report["presence_ended"], report["retried"], report["cleanup_ok"],
                          report["helper_exit_code"], report["helper_exit_ok"]),
                         ("presence_ended", True, False, True, 0, True))
        self.assert_helper_gone(source)

    def test_a_presence_end_is_seen_when_the_helper_closes_its_stdout_and_exits_later(self):
        source, stats, error, slot, stop = self.presence_run("--fake-close-stdout", "--fake-linger", 5, "--fake-term-delay", 1.5,
                                                             scenario="end:standby")
        self.assertIsNone(error)
        self.assertEqual((source.status, source.reason, source.end_reason), ("presence_ended", "standby", "standby"))
        self.assert_stopped_at_the_end_packet(stats, slot, stop)
        self.assertGreater(self.returned_at - stop.set_at, 1.2)
        self.assertTrue(source.cleanup_ok)
        self.assert_helper_gone(source)

    def test_a_presence_end_with_a_nonzero_exit_reports_the_helpers_failed_cleanup(self):
        """The helper exits 1 after a presence end exactly when its own cleanup failed: still ``presence_ended``,
        never raised and never retried, with ``cleanup_ok`` false and the exit code in the report."""
        source, stats, error, slot, stop = self.presence_run("--fake-exit", 1)
        self.assertIsNone(error)
        self.assert_stopped_at_the_end_packet(stats, slot, stop)
        report = source.report()
        self.assertEqual((report["status"], report["reason"], report["presence_ended"], report["retried"]),
                         ("presence_ended", "headset_removed", True, False))
        self.assertEqual((report["cleanup_ok"], report["helper_exit_code"], report["helper_exit_ok"]), (False, 1, False))
        self.assertIn("exited 1 after headset_removed", report["error"])
        self.assertEqual(report["export"]["helper_exit_code"], 1)
        self.assertTrue(report["export"]["cleanup_ok"])  # The primitive's own verdict: the tree is gone.
        self.assert_helper_gone(source)

    def test_a_failure_after_a_presence_end_keeps_the_presence_fact(self):
        """A packet after the end is a refusal, so the run is an error; that the wearer left stays in the report."""
        source, stats, error, slot, stop = self.presence_run(scenario="after_end:headset_removed")
        self.assertIsInstance(error, SightCircuitError)
        self.assertEqual((error.reason, source.status, source.reason), ("packet_after_end", "error", "packet_after_end"))
        self.assert_stopped_at_the_end_packet(stats, slot, stop)
        report = source.report()
        self.assertEqual((report["end_reason"], report["presence_ended"], report["retried"]),
                         ("headset_removed", True, False))
        self.assert_helper_gone(source)

    # -- the consent rule ---------------------------------------------------------------------------------------------

    def test_no_pixel_intrinsics_or_calibration_field_reaches_a_report_or_a_log(self):
        source = self.source("hello_noise")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertNoLogs(level="DEBUG"):
            stats, error = self.drive(source)
        self.assertIsNone(error)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))  # The source prints and logs nothing.
        # The helper did say these things on its diagnostics stream, so the filter below is what keeps them out.
        raw = json.dumps(source.export)
        for word in ("offline_cal_sha256", "intrinsics", '"digests"', "c" * 64):
            self.assertIn(word, raw)
        reports = {"source": source.report(), "pipeline": stats.report()}
        text = json.dumps(reports)  # Serialisable as it stands: no array, no buffer.
        for word in ("calib", "intrinsic", "offline_cal", "digest", "distortion", "extrinsic", "c" * 64, "f" * 64):
            self.assertNotIn(word, text.lower())
        self.assertEqual(sorted(key for _, key, _ in walk(reports) if "pixel" in str(key)),
                         ["pixel_path", "pixels_in_report"])
        self.assertEqual((reports["source"]["pixels_in_report"], reports["source"]["images_saved"]), (False, False))
        for path, key, value in walk(reports):
            with self.subTest(path=path, key=key):
                self.assertNotIsInstance(value, (bytes, bytearray, memoryview))
                self.assertFalse(hasattr(value, "shape") or hasattr(value, "tobytes"))
                if isinstance(value, (list, tuple)):
                    self.assertLessEqual(len(value), 16)
                if isinstance(value, str):
                    self.assertLessEqual(len(value), 1024)
        pixels = self.detector.frames[0].pixels
        self.assertNotIn(pixels[:24].hex(), text)
        self.assertNotIn(repr(pixels[:24]), text)
        self.assertLess(len(text), 8192)
        self.assertEqual(set(reports["source"]["export"]["diagnostics"]), set(camera_helper.DIAGNOSTIC_FIELDS))
        self.assertEqual(set(reports["source"]["export"]["stream"]["end"]), {"reason", "counts", "elapsed_ns", "confirmation"})
        self.assertNotIn("lines", reports["source"]["export"]["diagnostics"])


if __name__ == "__main__":
    unittest.main()
