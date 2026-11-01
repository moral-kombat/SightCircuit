"""Real subprocess cancellation, pipe failure and owned-helper reaping."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from framejacker.runtime import guard as bounded_worker
from scripts import recording_growth_check as growth

ROOT = Path(__file__).resolve().parents[1]
HAS_OPENCV = importlib.util.find_spec("cv2") is not None
FIXTURE = Path(__file__).with_name("worker_failure_fixture.py")


def child_pids(pid):
    """Observe real parentage even on kernels without the proc children file."""
    try:
        return Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except FileNotFoundError:
        # CONFIG_CHECKPOINT_RESTORE controls the children file; ordinary proc
        # status remains available. Retain the same real-process readiness check.
        children = []
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                status = (process / "status").read_text()
            except (FileNotFoundError, ProcessLookupError):
                continue
            parent = next((line.split()[1] for line in status.splitlines() if line.startswith("PPid:")), None)
            if parent == str(pid):
                children.append(process.name)
        return children


class ValidatorReferenceTests(unittest.TestCase):
    """The guardian imports only a caller-named validator of this checkout, never a command table."""

    def test_reference_names_a_module_level_validator_in_the_checkout_only(self):
        from framejacker.hud import marker

        self.assertEqual(bounded_worker.validator_reference(growth.validate_worker_report, {"limit": 1}),
                         {"module": "scripts.recording_growth_check", "function": "validate_worker_report",
                          "options": {"limit": 1}})
        self.assertEqual(bounded_worker.validator_reference(marker.validate_worker_report)["module"],
                         "framejacker.hud.marker")
        for validate in (lambda report, code: None, json.loads, bounded_worker.load_validator.__call__,
                         child_pids):
            with self.subTest(validate=validate), self.assertRaisesRegex(ValueError, "module_level_in_checkout"):
                bounded_worker.validator_reference(validate)

    def test_loader_imports_only_checkout_packages_and_binds_options(self):
        from framejacker.hud import marker

        for reference in ({"module": "json", "function": "loads"}, {"module": "os.path", "function": "join"},
                          {"module": "tests.test_bounded_worker", "function": "child_pids"},
                          {"module": "scripts..recording_growth_check", "function": "validate_worker_report"},
                          {"module": "scripts.missing_command", "function": "validate_worker_report"},
                          {"module": "scripts.recording_growth_check", "function": "validate worker"},
                          {"module": "scripts.recording_growth_check", "function": "validate_worker_report",
                           "options": ["not", "a", "mapping"]}, {}):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "unsupported_worker_validator"):
                bounded_worker.load_validator(reference)
        report = dict(marker.new_report(False), exit_code=1, status="error", seconds_requested=1)
        for validation_only, accepted in ((False, True), (True, False)):
            validate = bounded_worker.load_validator(bounded_worker.validator_reference(
                marker.validate_worker_report, {"validation_only": validation_only}))
            with self.subTest(validation_only=validation_only):
                if accepted:
                    validate(report, 1)
                else:
                    with self.assertRaisesRegex(ValueError, "invalid_marker_worker_report"):
                        validate(report, 1)


@unittest.skipUnless(sys.platform.startswith("linux"), "Frame/Linux process groups and subreaping")
class BoundedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def wait_file(self, name, process):
        path = self.directory / name
        deadline = time.monotonic() + 8
        while not path.exists() or not path.read_text():
            self.assertIsNone(process.poll(), "fixture exited before handshake")
            self.assertLess(time.monotonic(), deadline, "fixture handshake timed out")
            time.sleep(0.005)
        return path.read_text()

    def launch(self, mode):
        process = subprocess.Popen([sys.executable, str(FIXTURE), "parent", mode, str(self.directory)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.finish, process)
        return process

    def finish(self, process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)

    def result(self, process):
        output, error = process.communicate(timeout=12)
        self.assertFalse(error, error)
        report = json.loads(output)
        self.assertTrue(report["worker_reaped"], report)
        self.assertTrue(report["worker_tree_reaped"], report)
        self.assertTrue(report["report_reader_stopped"], report)
        self.assertTrue(report["guardian_reaped"], report)
        self.assertEqual(report["parent_death_containment"], "linux_lifeline_guardian")
        with self.assertRaises(ProcessLookupError):
            os.kill(report["worker_pid"], 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(report["worker_pid"], 0)
        return report

    def parent_death(self, pause_guardian=False):
        with bounded_worker.child_reaper():
            process = self.launch("parent_death")
            worker = int(self.wait_file("ready", process))
            helper = int(self.wait_file("helper", process))
            status = Path(f"/proc/{worker}/status").read_text()
            guardian = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
            if pause_guardian:
                os.kill(guardian, signal.SIGSTOP)
                deadline = time.monotonic() + 2
                while "\nState:\tT" not in Path(f"/proc/{guardian}/status").read_text():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.005)
            try:
                process.kill()  # SIGKILL cannot execute the supervisor's Python finally.
                process.wait(timeout=2)
                if pause_guardian:
                    os.kill(worker, 0)
                    os.kill(helper, 0)
                    os.kill(guardian, signal.SIGCONT)
                deadline = time.monotonic() + 10
                while True:
                    reaped, _ = os.waitpid(guardian, os.WNOHANG)
                    if reaped:
                        break
                    self.assertLess(time.monotonic(), deadline, "guardian failed to finish containment")
                    time.sleep(0.01)
                for pid in (worker, helper):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                with self.assertRaises(ProcessLookupError):
                    os.killpg(worker, 0)
                output, error = process.communicate(timeout=2)
                self.assertEqual(output, b"")  # A dead supervisor provides no clean API-shutdown proof.
                self.assertEqual(error, b"")
            finally:
                # Failure-only recovery of this fixture's exact owned group.
                for pid, number in ((guardian, signal.SIGCONT), (guardian, signal.SIGTERM)):
                    try:
                        os.kill(pid, number)
                    except ProcessLookupError:
                        pass
                try:
                    os.killpg(worker, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_sigkill_of_supervisor_leaves_guardian_to_reap_worker_and_helper(self):
        self.parent_death()

    def test_stopped_guardian_needs_scheduling_before_it_can_clean_up(self):
        self.parent_death(pause_guardian=True)

    def test_closed_lifeline_before_guardian_start_never_launches_worker(self):
        reader, writer = os.pipe()
        os.close(writer)
        options = {"command": [sys.executable, str(FIXTURE), "worker", "cooperative", str(self.directory)],
                   "base_report": growth.new_report(), "timeout": 2, "descendants": False, "parent_fd": reader,
                   "validator": bounded_worker.validator_reference(growth.validate_worker_report)}
        try:
            result = subprocess.run([sys.executable, str(ROOT / "framejacker/runtime/guard.py"),
                "--guard", json.dumps(options)], pass_fds=(reader,), capture_output=True, timeout=5)
        finally:
            os.close(reader)
        report = json.loads(result.stdout)["result"]
        self.assertIn("lifeline_closed_before_start", report["reason"])
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse((self.directory / "ready").exists())

    def test_partial_report_does_not_suspend_deadline(self):
        started = time.monotonic()
        report = self.result(self.launch("partial"))
        self.assertEqual(report["status"], "timed_out")
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])

        self.assertLess(time.monotonic() - started, 12)

    def test_oversized_report_is_bounded_before_parsing(self):
        report = self.result(self.launch("oversized"))
        self.assertIn("worker_report_byte_limit", report["reason"])
        self.assertFalse(report["cleanup_ok"])

    def test_exited_leader_does_not_leave_helper_or_open_pipe(self):
        report = self.result(self.launch("orphan_helper"))
        helper = int((self.directory / "helper").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(helper, 0)
        self.assertTrue(report["unrelated_child_untouched"])
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])

    def test_exit_status_and_closed_gates_override_child_claims(self):
        for mode in ("nonzero_after_report", "unsafe_claim"):
            with self.subTest(mode=mode):
                report = self.result(self.launch(mode))
                self.assertEqual(report["status"], "invalid_worker_report")
                self.assertFalse(report["cleanup_ok"])
                self.assertFalse(report["image_access_enabled"])

    def test_parent_only_signals_and_repeated_cancellation_finish_cleanup(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(number=number):
                for path in self.directory.iterdir():
                    path.unlink()
                process = self.launch("cooperative")
                self.wait_file("ready", process)
                process.send_signal(number)
                self.wait_file("stopping", process)
                process.send_signal(number)
                (self.directory / "finish").write_text("continue")
                report = self.result(process)
                self.assertEqual(report["status"], "cancelled")
                self.assertEqual(report["exit_code"], 128 + number)
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["worker_forced_termination"])

    @unittest.skipUnless(HAS_OPENCV, "OpenCV is required for the live pipeline fixture")
    def test_cancellation_during_real_hung_detector_reaps_worker(self):
        process = self.launch("pipeline_hung_detector")
        self.wait_file("ready", process)
        process.send_signal(signal.SIGTERM)
        report = self.result(process)
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])

    @unittest.skipUnless(HAS_OPENCV, "OpenCV is required for the live pipeline fixture")
    def test_hung_source_thread_cannot_claim_cleanup(self):
        process = self.launch("pipeline_hung_source")
        self.wait_file("ready", process)
        process.send_signal(signal.SIGTERM)
        report = self.result(process)
        self.assertFalse(report["source_cleanup_ok"])
        self.assertFalse(report["cleanup_ok"])

    def test_recording_cli_cancellation_while_armed(self):
        import selectors
        for wait, number in ((30, signal.SIGTERM), (120, signal.SIGINT), (120, signal.SIGTERM)):
            with self.subTest(wait=wait, signal=number):
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts/recording_growth_check.py"),
                    "--watch-directory", str(self.directory), "--wait-for-new", str(wait), "--seconds", "15"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.addCleanup(self.finish, process)
                # Signal only after the real worker arms; never wait 120 seconds.
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stderr, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=5))
                    self.assertIn(b"Start recording now", process.stderr.readline())
                process.send_signal(number)
                report = self.result(process)
                self.assertEqual(process.returncode, 128 + number)
                self.assertEqual(report["status"], "cancelled")
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["worker_forced_termination"])
                self.assertFalse(report["guardian_forced_termination"])
                self.assertFalse(report["media_payload_read"])
                self.assertFalse(report["image_access_enabled"])
                self.assertFalse(report["live_source_verified"])
                self.assertEqual(report["images_decoded"], 0)
                self.assertEqual(report["observations"], 0)

    @unittest.skipUnless(HAS_OPENCV, "OpenCV is required for the live pipeline fixture")
    def test_pipeline_cli_parent_cancellation(self):
        command = "from scripts.live_pipeline import supervise_synthetic; import json; print(json.dumps(supervise_synthetic({'seconds':30,'detector':'fixture'})))"
        process = subprocess.Popen([sys.executable, "-c", command], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.finish, process)
        deadline = time.monotonic() + 8
        # Wait for the real worker's SIGTERM handler, not an arbitrary sleep.
        while True:
            self.assertIsNone(process.poll())
            children = child_pids(process.pid)
            if children:
                children = child_pids(children[0])
            if children:
                status = Path(f"/proc/{children[0]}/status").read_text()
                mask = int(next(line.split()[1] for line in status.splitlines() if line.startswith("SigCgt:")), 16)
                if mask & (1 << (signal.SIGTERM - 1)):
                    break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        process.send_signal(signal.SIGTERM)
        report = self.result(process)
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["vr_runtime_initialized"])
