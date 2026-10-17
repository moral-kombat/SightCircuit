"""Exercise the real exec boundary, watchdog, and identity-based file recovery."""
import contextlib
import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from scripts import capture_supervisor as capture
from scripts import capture_session


@unittest.skipUnless(capture.LAUNCHER.is_file(), "build scripts/build_capture_supervisor.sh first")
class CaptureSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="framejacker-watchdog-tests-")
        cls.root = Path(cls.temp.name)
        cls.worker = cls.root / "worker"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                        f"-I{capture.ROOT / 'tools'}", f"-I{capture.ROOT / 'third_party/mrhand'}",
                        str(capture.ROOT / "tests/fixtures/capture_worker_fixture.cpp"), "-o", str(cls.worker)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_worker(self, mode="done", **options):
        with tempfile.TemporaryDirectory(dir=self.root) as runtime:
            return capture.supervise(worker=self.worker, runtime=runtime, worker_arguments=[mode],
                                     timeout=options.pop("timeout", 0.2), grace=0.05, **options)

    def test_default_and_inspection_worker_stay_disabled(self):
        for args, code in (([], 3), (["--help"], 0), (["--unknown"], 2)):
            done = subprocess.run([sys.executable, "scripts/capture_supervisor.py", *args],
                                  cwd=capture.ROOT, capture_output=True, text=True, timeout=2)
            self.assertEqual(done.returncode, code, done.stderr)
            if code == 3:
                self.assertFalse(json.loads(done.stdout)["worker_started"])
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            binary = Path(directory) / "inspection"
            binary.write_bytes(self.worker.read_bytes())
            binary.chmod(0o644)
            report = capture.supervise(worker=binary, runtime=directory)
            self.assertFalse(report["worker_started"])
            self.assertEqual(report["reason"], "worker_not_prepared_executable")

    def test_normal_completion_reaps_and_removes_only_owned_session(self):
        report = self.run_worker(timeout=2)
        self.assertEqual(report["status"], "captured", report)
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["api_cleanup_confirmed"])
        self.assertTrue(report["publication_recovery_confirmed"])
        self.assertFalse(Path(report["session_path"]).exists())
        self.assertFalse(report["worker_forced_termination"])

    def test_deadline_covers_startup_import_copy_and_destruction(self):
        for mode in ("hang_startup", "hang_import", "hang_copy", "hang_destroy"):
            with self.subTest(mode=mode):
                report = self.run_worker(mode)
                self.assertEqual(report["status"], "timed_out", report)
                self.assertTrue(report["worker_reaped"])
                self.assertTrue(report["worker_forced_termination"])
                self.assertFalse(report["api_cleanup_confirmed"])
                self.assertTrue(report["publication_recovery_confirmed"])
                self.assertLess(report["elapsed_ms"], 1800)
                with self.assertRaises(ProcessLookupError):
                    os.kill(report["worker_pid"], 0)

    def test_deadline_can_retire_gracefully_without_claiming_capture_success(self):
        report = self.run_worker("cancel")
        self.assertEqual(report["status"], "timed_out", report)
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["worker_termination_requested"])
        self.assertFalse(report["worker_forced_termination"])
        self.assertTrue(report["api_cleanup_confirmed"])
        self.assertTrue(report["publication_recovery_confirmed"])

    def test_sigint_and_sigterm_reap_the_worker(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(number=number), tempfile.TemporaryDirectory(dir=self.root) as runtime:
                body = """
import json, signal, sys
from pathlib import Path
from scripts.capture_supervisor import supervise, StopRequested
def stop(number, frame): raise StopRequested(number)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
report = supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['hang_copy'], timeout=5, grace=0.05)
print(json.dumps(report))
"""
                process = subprocess.Popen([sys.executable, "-c", body, str(self.worker), runtime], cwd=capture.ROOT,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 3
                    while not list(Path(runtime).glob("*/frames.shm")) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(list(Path(runtime).glob("*/frames.shm")))
                    process.send_signal(number)
                    time.sleep(0.01)
                    if process.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.send_signal(number)  # Cleanup must survive repeated cancellation.
                    out, err = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 0, err)
                    report = json.loads(out)
                    self.assertEqual(report["status"], "cancelled")
                    self.assertEqual(report["exit_code"], 128 + number)
                    self.assertTrue(report["worker_reaped"])
                    self.assertTrue(report["publication_recovery_confirmed"])
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=2)

    def test_corrupt_partial_missing_and_inconsistent_output_fail(self):
        for mode in ("malformed", "partial", "flood", "missing", "inconsistent", "crash"):
            with self.subTest(mode=mode):
                report = self.run_worker(mode, timeout=2)
                self.assertNotEqual(report["exit_code"], 0, report)
                self.assertTrue(report["worker_reaped"])
                self.assertFalse(report["api_cleanup_confirmed"])
                self.assertLess(sum(len(json.dumps(e)) for e in report["events"]), capture.MAX_OUTPUT)

    def test_unannounced_or_replaced_publication_is_preserved(self):
        for mode in ("unannounced", "replace"):
            with self.subTest(mode=mode):
                # Keep this directory until after assertions on the refused recovery.
                with tempfile.TemporaryDirectory(dir=self.root) as runtime:
                    report = capture.supervise(worker=self.worker, runtime=runtime, worker_arguments=[mode],
                                               timeout=0.2, grace=0.05)
                    self.assertFalse(report["publication_recovery_confirmed"])
                    self.assertTrue((Path(report["session_path"]) / "frames.shm").is_file())

    def test_recovery_refuses_a_living_run_and_foreign_paths(self):
        with tempfile.TemporaryDirectory(dir=self.root) as runtime:
            session = capture_session.Session(runtime)
            try:
                with self.assertRaises((ValueError, BlockingIOError)):
                    capture_session.recover(session.path)
                session.close()
                with self.assertRaisesRegex(ValueError, "still_alive"):
                    capture_session.recover(session.path)
                with self.assertRaisesRegex(ValueError, "invalid_session_path"):
                    capture_session.recover(Path(runtime) / "other")
            finally:
                session.close()

    def test_dead_session_identity_recovery_and_replacement_preservation(self):
        for replace in (False, True):
            with tempfile.TemporaryDirectory(dir=self.root) as runtime:
                session = capture_session.Session(runtime)
                file = session.path / "frames.shm"
                file.write_bytes(b"owned")
                file.chmod(0o600)
                session.publication(capture_session.identity(file.stat()))
                session.state.update(supervisor_pid=2147483647, supervisor_start=1)
                session.save()
                path = session.path
                session.close()
                if replace:
                    file.unlink()
                    file.symlink_to(self.worker)
                    with self.assertRaisesRegex(ValueError, "unconfirmed"):
                        capture_session.recover(path)
                    self.assertTrue(file.is_symlink())
                else:
                    capture_session.recover(path)
                    self.assertFalse(path.exists())

    def test_parent_death_kills_worker_even_before_its_own_setup(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(old), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
            self.skipTest("Linux subreaper unavailable")
        supervisor = child = None
        try:
            with tempfile.TemporaryDirectory(dir=self.root) as runtime:
                body = """
import os, sys
from pathlib import Path
from scripts.capture_supervisor import supervise
supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['hang_startup'], timeout=30)
"""
                supervisor = subprocess.Popen([sys.executable, "-c", body, str(self.worker), runtime],
                                              cwd=capture.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    leases = list(Path(runtime).glob("*/lease.json"))
                    try:
                        state = json.loads(leases[0].read_text()) if leases else {}
                        child = state.get("worker_pid")
                    except (ValueError, IndexError):
                        pass
                    if child:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(child)
                # Allow the tiny exec boundary to establish PR_SET_PDEATHSIG.
                time.sleep(0.05)
                supervisor.kill()
                supervisor.wait(timeout=2)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    found, status = os.waitpid(child, os.WNOHANG)
                    if found:
                        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGKILL)
                        child = None
                        break
                    time.sleep(0.01)
                self.assertIsNone(child)
                capture_session.recover(leases[0].parent)
        finally:
            if supervisor and supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=2)
            if supervisor and supervisor.stderr:
                supervisor.stderr.close()
            if child:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child, signal.SIGKILL)
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(child, 0)
            libc.prctl(36, old.value, 0, 0, 0)
