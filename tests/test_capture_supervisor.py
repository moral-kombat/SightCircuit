"""Exercise the real exec boundary, watchdog, and identity-based file recovery."""
import contextlib
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from scripts import capture_supervisor as capture
from scripts import capture_session


def keep_another_thread(test):
    """Run the test while another thread leaves SIGINT and SIGTERM unblocked.

    The full suite has such threads: importing numpy starts BLAS threads. The kernel hands a
    process-directed signal (os.kill) that the main thread holds to one of them, and Python
    then runs the handler on the main thread at an arbitrary later point. In-process tests
    therefore send with signal.raise_signal, which targets the calling thread and runs an
    unblocked signal's handler before it returns. This thread makes a single-module run as
    strict as the full suite.
    """
    ready, release = threading.Event(), threading.Event()

    def wait():
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        ready.set()
        release.wait()
    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    test.addCleanup(thread.join, 10)
    test.addCleanup(release.set)
    test.assertTrue(ready.wait(10))


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
def stop(number, frame):
    # The test repeats its signal. Block both from the first delivery on, so a repeat cannot
    # land between supervise() returning and this harness printing; the supervisor's own
    # mask handling restores this block, so it is not affected.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    raise StopRequested(number)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    report = supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['hang_copy'], timeout=5, grace=0.05)
finally:
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
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
                    self.assertNotIn("superseded_error", report)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=2)

    def test_a_graceful_cancellation_still_collects_the_final_event(self):
        # The worker retires on SIGTERM. A cancellation that replaces no error leaves the unread
        # output in the pipe for the drain, so the worker's capture_closed event is still
        # recorded and validated. "handling" calls supervise() while its caller handles an
        # error, which the run must not report as a superseded one.
        body = """
import json, signal, sys
from pathlib import Path
from scripts.capture_supervisor import supervise, StopRequested
def stop(number, frame): raise StopRequested(number)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
def run():
    return supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['cancel'], timeout=8, grace=5)
try:
    if sys.argv[3] == 'handling':
        try:
            raise OSError('caller_error')
        except OSError:
            report = run()
    else:
        report = run()
finally:
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
print(json.dumps(report))
"""
        for caller in ("plain", "handling"):
            with self.subTest(caller=caller), tempfile.TemporaryDirectory(dir=self.root) as runtime:
                process = subprocess.Popen([sys.executable, "-c", body, str(self.worker), runtime, caller],
                                           cwd=capture.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    # The worker installs its SIGTERM handler before it creates frames.shm.
                    deadline = time.monotonic() + 20
                    while (not list(Path(runtime).glob("*/frames.shm")) and process.poll() is None and
                           time.monotonic() < deadline):
                        time.sleep(0.01)
                    self.assertTrue(list(Path(runtime).glob("*/frames.shm")))
                    process.send_signal(signal.SIGTERM)
                    out, err = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 0, err)
                    report = json.loads(out)
                    self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 128 + signal.SIGTERM), report)
                    self.assertNotIn("superseded_error", report)
                    self.assertEqual([event["event"] for event in report["events"]][-1:], ["capture_closed"], report)
                    self.assertTrue(report["api_cleanup_confirmed"], report)
                    self.assertFalse(report["worker_forced_termination"], report)
                    self.assertTrue(report["worker_reaped"], report)
                    self.assertTrue(report["publication_recovery_confirmed"], report)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=2)

    def test_cancellation_while_recording_a_publication_still_removes_it(self):
        # The wrapped step opens a window after the worker's publication_ready event has
        # been read and stays inside it until the cancellation has arrived, delivered or
        # held, so neither outcome depends on scheduling. "read" holds the window just after
        # os.read has taken the line from the pipe, so a chunk already read must still be
        # recorded; "parse" holds it after the line has left the buffer; "publication" holds
        # it inside Session.publication.
        body = """
import json, os, signal, sys, time
from pathlib import Path
from scripts import capture_session
from scripts.capture_supervisor import supervise, StopRequested
def stop(number, frame): raise StopRequested(number)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
def hold():
    Path(sys.argv[3]).touch()
    deadline = time.monotonic() + 20
    while not signal.sigpending() & {signal.SIGINT, signal.SIGTERM}:
        if time.monotonic() > deadline:
            raise RuntimeError('cancellation_not_observed')
        time.sleep(0.01)
def wrap(owner, name, applies):
    original = getattr(owner, name)
    def held(*args, **kwargs):
        if applies(*args):
            hold()
        return original(*args, **kwargs)
    setattr(owner, name, held)
if sys.argv[4] == 'read':
    original_read, pending = os.read, [True]
    def read(fd, size):
        data = original_read(fd, size)
        if pending and b'publication_ready' in data:
            pending.clear()  # Once: the post-cancellation drain must not wait again.
            hold()
        return data
    os.read = read
elif sys.argv[4] == 'parse':
    wrap(json, 'loads', lambda raw: b'publication_ready' in raw)
else:
    wrap(capture_session.Session, 'publication', lambda session, event: True)
report = supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['hang_copy'], timeout=30, grace=0.05)
print(json.dumps(report))
"""
        for point in ("read", "parse", "publication"):
            for number in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(point=point, number=number), tempfile.TemporaryDirectory(dir=self.root) as runtime:
                    marker = Path(runtime) / "window-open"
                    process = subprocess.Popen([sys.executable, "-c", body, str(self.worker), runtime, str(marker),
                                                point], cwd=capture.ROOT, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True)
                    try:
                        deadline = time.monotonic() + 20
                        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(marker.exists(), "the publication window never opened")
                        process.send_signal(number)
                        out, err = process.communicate(timeout=30)
                        self.assertEqual(process.returncode, 0, err)
                        report = json.loads(out)
                        self.assertEqual(report["status"], "cancelled", report)
                        self.assertEqual(report["exit_code"], 128 + number)
                        self.assertIn("publication_ready", [event["event"] for event in report["events"]])
                        self.assertTrue(report["worker_reaped"], report)
                        self.assertTrue(report["publication_recovery_confirmed"], report)
                        self.assertNotIn("recovery_error", report)
                        self.assertNotIn("superseded_error", report)
                        self.assertFalse(Path(report["session_path"]).exists())
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=2)

    def test_cancellation_while_starting_the_worker_still_reaps_it(self):
        # The wrapped Popen returns only after the parent has sent its signal, so the signal
        # arrives after the worker exists but before supervise() holds the process: a handler
        # raising there would leave the worker unsupervised. The worker hangs before it
        # publishes anything, so only reaping is under test here; whether it already ignores
        # SIGTERM when cleanup starts, and so needs SIGKILL, depends on timing.
        body = """
import json, signal, subprocess, sys, time
from pathlib import Path
from scripts.capture_supervisor import supervise, StopRequested
delivered = []
def stop(number, frame):
    # Read the lease as the recorded cancellation is delivered, before anything else can save it.
    leases = Path(sys.argv[2]).glob('framejacker-capture-*/lease.json')
    delivered.extend(json.loads(path.read_text()) for path in leases)
    raise StopRequested(number)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
original = subprocess.Popen
def held(*args, **kwargs):
    process = original(*args, **kwargs)
    Path(sys.argv[3]).touch()
    deadline = time.monotonic() + 20
    while not Path(sys.argv[4]).exists():
        if time.monotonic() > deadline:
            raise RuntimeError('cancellation_not_sent')
        time.sleep(0.01)
    return process
subprocess.Popen = held
try:
    report = supervise(worker=Path(sys.argv[1]), runtime=sys.argv[2], worker_arguments=['hang_startup'],
                       timeout=30, grace=0.05)
finally:
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
print(json.dumps(dict(report, delivered=delivered)))
"""
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(number=number), tempfile.TemporaryDirectory(dir=self.root) as runtime:
                started, sent = Path(runtime) / "worker-started", Path(runtime) / "signal-sent"
                process = subprocess.Popen([sys.executable, "-c", body, str(self.worker), runtime, str(started),
                                            str(sent)], cwd=capture.ROOT, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 20
                    while not started.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.exists(), "the worker never started")
                    process.send_signal(number)
                    sent.touch()  # Only after the signal is queued: the wrapper then returns.
                    out, err = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 0, err)
                    report = json.loads(out)
                    self.assertEqual(report["status"], "cancelled", report)
                    self.assertEqual(report["exit_code"], 128 + number)
                    self.assertTrue(report["worker_started"], report)
                    self.assertTrue(report["worker_reaped"], report)
                    self.assertTrue(report["worker_termination_requested"], report)
                    self.assertTrue(report["publication_recovery_confirmed"], report)
                    self.assertNotIn("superseded_error", report)
                    self.assertFalse(Path(report["session_path"]).exists())
                    # The lease named the worker when the recorded cancellation was delivered, so a
                    # caller whose SIG_DFL ends the process still leaves it for --recover-session.
                    self.assertEqual([lease["worker_pid"] for lease in report["delivered"]], [report["worker_pid"]])
                    self.assertIsNotNone(report["delivered"][0]["worker_start"], report)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(report["worker_pid"], 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=2)

    def run_with_a_rejected_publication(self, cancel):
        # Session.publication rejects the announced identity, after sending SIGTERM to this thread
        # if cancel is set. Either way no later output is read or validated: it is already known
        # to be invalid.
        real_read, rejected, later_reads = os.read, [], []

        def reject(session, event):
            rejected.append(event)
            if cancel:
                signal.raise_signal(signal.SIGTERM)  # This thread only, so it stays held here.
            raise ValueError("invalid_publication_identity")

        def read(fd, size):
            if rejected:
                later_reads.append(fd)
            return real_read(fd, size)
        with mock.patch.object(capture.Session, "publication", reject), mock.patch.object(capture.os, "read", read):
            # The "done" worker finishes at once; the deadline only bounds a stalled run.
            report = self.run_worker(timeout=10)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(later_reads, [], f"output already known to be invalid was read: {report}")
        self.assertNotIn("native", report)
        self.assertFalse(report["api_cleanup_confirmed"])
        self.assertTrue(report["worker_reaped"])
        return report

    def test_a_recording_error_replaced_by_a_held_cancellation_is_kept(self):
        # The cancellation arrives while Session.publication rejects the announced identity. It
        # is held until the deferral ends, so it replaces that error: the run ends cancelled,
        # keeps the error and does not validate the rest of an output already known to be invalid.
        keep_another_thread(self)

        def stop(number, frame):
            raise capture.StopRequested(number)
        previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM)}
        try:
            report = self.run_with_a_rejected_publication(cancel=True)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 128 + signal.SIGTERM), report)
        self.assertEqual(report["superseded_error"], "invalid_publication_identity")

    def test_a_recording_error_without_a_cancellation_fails_the_run(self):
        # Nothing replaces the error, so it fails the run and is named as its reason. A
        # noted_error() that swallowed it would let the run go on reading and could even
        # report the capture as successful.
        report = self.run_with_a_rejected_publication(cancel=False)
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("failed", "invalid_publication_identity", 1), report)
        self.assertNotIn("superseded_error", report)

    def test_a_cancellation_while_the_pid_save_fails_still_cancels_the_run(self):
        # The cancellation arrives while the worker's pid is saved to the lease, and the save
        # then fails. The recorded cancellation is delivered as the start-up block raises, so it
        # replaces the lease error: the run ends cancelled, keeps the error, stops and reaps the
        # worker it started and does not read the output of a run already known to have failed.
        keep_another_thread(self)
        real_read, failed, later_reads = os.read, [], []

        def stop(number, frame):
            raise capture.StopRequested(number)

        def failing_save(session, pid):
            failed.append(pid)
            signal.raise_signal(signal.SIGTERM)  # Recorded: the start-up block swapped the handlers.
            raise OSError("capture_lease_short_write")

        def read(fd, size):
            if failed:
                later_reads.append(fd)
            return real_read(fd, size)
        previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM)}
        try:
            with mock.patch.object(capture.Session, "worker", failing_save), mock.patch.object(capture.os, "read", read):
                report = self.run_worker("hang_startup", timeout=5)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        self.assertEqual((report["status"], report["reason"], report["signal"], report["exit_code"]),
                         ("cancelled", "signal", signal.SIGTERM, 128 + signal.SIGTERM), report)
        self.assertEqual(report.get("superseded_error"), "capture_lease_short_write", report)
        self.assertEqual(failed, [report["worker_pid"]])
        self.assertTrue(report["worker_started"], report)
        self.assertTrue(report["worker_termination_requested"], report)
        self.assertTrue(report["worker_reaped"], report)
        self.assertTrue(report["publication_recovery_confirmed"], report)
        self.assertEqual(later_reads, [], "the drain read the output of a run whose pid save failed")

    def test_corrupt_partial_missing_and_inconsistent_output_fail(self):
        # Each mode ends by itself or at its first error; the deadline only bounds a stalled run,
        # so it cannot stand in for the recording error that "malformed" and "flood" must name.
        decode_modes = ("malformed", "flood")  # Their reason is json's decode error, whatever its exact wording.
        for mode in ("malformed", "partial", "flood", "missing", "inconsistent", "crash"):
            with self.subTest(mode=mode):
                report = self.run_worker(mode, timeout=10)
                self.assertNotEqual(report["exit_code"], 0, report)
                if mode in decode_modes:
                    self.assertEqual(report["status"], "failed", report)
                    self.assertTrue(report["reason"].startswith("Expecting value"), report)
                    self.assertNotEqual(report["reason"], "capture_deadline", report)
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


class RecordedSignalsTests(unittest.TestCase):
    """The handler swap used while the worker starts; it needs no native boundary."""

    def setUp(self):
        keep_another_thread(self)
        self.calls = []
        self.saved = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        for number in self.saved:
            signal.signal(number, lambda number, frame: self.calls.append(number))

    def tearDown(self):
        for number, handler in self.saved.items():
            signal.signal(number, handler)

    def test_a_signal_during_the_block_reaches_the_previous_handler_only_afterwards(self):
        handlers = {number: signal.getsignal(number) for number in self.saved}
        for number in (signal.SIGINT, signal.SIGTERM):
            self.calls.clear()
            with capture.recorded_signals():
                signal.raise_signal(number)  # Runs the recording handler, not the previous one.
                inside = list(self.calls)
            self.assertEqual(inside, [])
            self.assertEqual(self.calls, [number])
            self.assertEqual({n: signal.getsignal(n) for n in self.saved}, handlers)

    def test_each_distinct_signal_is_delivered_once_in_arrival_order(self):
        with capture.recorded_signals():
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGTERM):
                signal.raise_signal(number)
        self.assertEqual(self.calls, [signal.SIGTERM, signal.SIGINT])

    def test_a_raising_handler_ends_delivery(self):
        def stop(number, frame):
            self.calls.append(number)
            raise capture.StopRequested(number)
        signal.signal(signal.SIGTERM, stop)
        with self.assertRaises(capture.StopRequested) as caught:
            with capture.recorded_signals():
                for number in (signal.SIGTERM, signal.SIGINT):
                    signal.raise_signal(number)
        self.assertEqual(caught.exception.number, signal.SIGTERM)
        self.assertEqual(self.calls, [signal.SIGTERM])

    def test_an_ignored_signal_does_not_hide_a_later_cancellation(self):
        def stop(number, frame):
            raise capture.StopRequested(number)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, stop)
        with self.assertRaises(capture.StopRequested) as caught:
            with capture.recorded_signals():
                inside = signal.getsignal(signal.SIGINT)  # Not swapped: the worker inherits it.
                for number in (signal.SIGINT, signal.SIGTERM):
                    signal.raise_signal(number)
        self.assertIs(inside, signal.SIG_IGN)
        self.assertEqual(caught.exception.number, signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)

    def test_handlers_are_restored_before_a_raising_handler_runs(self):
        def stop(number, frame):
            raise capture.StopRequested(number)
        signal.signal(signal.SIGTERM, stop)
        handlers = {number: signal.getsignal(number) for number in self.saved}
        with self.assertRaises(capture.StopRequested):
            with capture.recorded_signals():
                signal.raise_signal(signal.SIGTERM)
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, handlers)

    def test_a_signal_between_the_two_restores_leaves_neither_recording(self):
        def stop(number, frame):
            raise capture.StopRequested(number)
        for number in self.saved:
            signal.signal(number, stop)
        install, sent = signal.signal, []

        def restore_then_cancel(number, handler):
            old = install(number, handler)
            if not sent and handler is stop:
                # The first restore, whichever signal it is: the signal lands before the second.
                # It is sent to this thread only, which holds it: os.kill would let another
                # thread take it and run the handler on this one at any later point.
                sent.append(number)
                signal.raise_signal(number)
            return old
        with self.assertRaises(capture.StopRequested) as caught:
            with mock.patch.object(capture.signal, "signal", restore_then_cancel), capture.recorded_signals():
                pass
        self.assertEqual([caught.exception.number], sent)
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, dict.fromkeys(self.saved, stop))

    def test_handlers_are_restored_when_the_block_raises(self):
        handlers = {number: signal.getsignal(number) for number in self.saved}
        with self.assertRaisesRegex(OSError, "synthetic spawn failure"):
            with capture.recorded_signals():
                raise OSError("synthetic spawn failure")
        self.assertEqual({n: signal.getsignal(n) for n in self.saved}, handlers)
        self.assertEqual(self.calls, [])

    def test_a_signal_recorded_before_the_block_raises_is_still_delivered(self):
        def stop(number, frame):
            raise capture.StopRequested(number)
        signal.signal(signal.SIGTERM, stop)
        handlers = {number: signal.getsignal(number) for number in self.saved}
        with self.assertRaises(capture.StopRequested) as caught:
            with capture.recorded_signals():
                signal.raise_signal(signal.SIGTERM)  # Recorded; the block then fails as a spawn would.
                raise OSError("synthetic spawn failure")
        self.assertEqual(caught.exception.number, signal.SIGTERM)
        self.assertIsInstance(caught.exception.__context__, OSError)
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, handlers)

    def supervise_with_a_failing_spawn(self, cancel):
        # supervise() itself, with a spawn that fails, after sending SIGTERM to this thread if
        # cancel is set. No worker exists, so the session is still removed.
        def failing_spawn(*args, **kwargs):
            if cancel:
                signal.raise_signal(signal.SIGTERM)
            raise OSError("synthetic spawn failure")
        with tempfile.TemporaryDirectory(prefix="framejacker-watchdog-tests-") as runtime:
            worker = Path(runtime) / "worker"
            worker.write_bytes(b"")
            worker.chmod(0o700)
            with mock.patch.object(capture.subprocess, "Popen", failing_spawn):
                report = capture.supervise(worker=worker, runtime=runtime, worker_arguments=["done"], timeout=5)
            self.assertFalse(Path(report["session_path"]).exists())
        self.assertFalse(report["worker_started"])
        self.assertTrue(report["publication_recovery_confirmed"], report)
        return report

    def test_a_cancellation_while_the_worker_fails_to_start_still_cancels_the_run(self):
        # The spawn fails after the cancellation arrived: the run must end cancelled, not
        # failed, and keep the spawn error it replaced.
        def stop(number, frame):
            raise capture.StopRequested(number)
        for number in self.saved:
            signal.signal(number, stop)
        report = self.supervise_with_a_failing_spawn(cancel=True)
        self.assertEqual((report["status"], report["reason"], report["signal"], report["exit_code"]),
                         ("cancelled", "signal", signal.SIGTERM, 128 + signal.SIGTERM), report)
        self.assertEqual(report["superseded_error"], "synthetic spawn failure")
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, dict.fromkeys(self.saved, stop))

    def test_a_worker_that_fails_to_start_without_a_cancellation_fails_the_run(self):
        # Nothing replaces the spawn error, so it fails the run and is named as its reason.
        handlers = {number: signal.getsignal(number) for number in self.saved}
        report = self.supervise_with_a_failing_spawn(cancel=False)
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("failed", "synthetic spawn failure", 1), report)
        self.assertNotIn("superseded_error", report)
        self.assertEqual(self.calls, [])
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, handlers)

    def test_an_ignored_signal_stays_ignored(self):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with capture.recorded_signals():
            signal.raise_signal(signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
        self.assertEqual(self.calls, [])

    def test_a_default_handler_still_ends_the_process_after_the_block(self):
        body = """
import signal
from scripts.capture_supervisor import recorded_signals
signal.signal(signal.SIGTERM, signal.SIG_DFL)
with recorded_signals():
    signal.raise_signal(signal.SIGTERM)
    print("inside", flush=True)
print("after", flush=True)
"""
        result = subprocess.run([sys.executable, "-c", body], cwd=capture.ROOT, capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, -signal.SIGTERM, result.stderr)
        self.assertEqual(result.stdout, "inside\n")

    def test_a_handler_not_installed_from_python_is_left_alone(self):
        handlers = {number: signal.getsignal(number) for number in self.saved}
        reported = {**handlers, signal.SIGTERM: None}
        real_getsignal = signal.getsignal
        with mock.patch.object(capture.signal, "getsignal",
                               side_effect=lambda number: reported.get(number, real_getsignal(number))):
            with capture.recorded_signals():
                signal.raise_signal(signal.SIGINT)  # Nothing was swapped, so the caller's handler runs.
                inside = list(self.calls)
        self.assertEqual(inside, [signal.SIGINT])
        self.assertEqual(self.calls, [signal.SIGINT])
        self.assertEqual({number: signal.getsignal(number) for number in self.saved}, handlers)

    def test_off_the_main_thread_nothing_changes(self):
        seen = {}

        def run():
            with capture.recorded_signals():
                seen.update({number: signal.getsignal(number) for number in self.saved})
        handlers = {number: signal.getsignal(number) for number in self.saved}
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(10)
        self.assertEqual(seen, handlers)


class SignalDeferralTests(unittest.TestCase):
    """The deferral used while a worker's output is recorded; it needs no native boundary."""
    HELD = frozenset((signal.SIGINT, signal.SIGTERM))

    def setUp(self):
        keep_another_thread(self)

    @staticmethod
    def mask():
        return signal.pthread_sigmask(signal.SIG_BLOCK, ())

    def test_mask_is_restored_when_recording_fails(self):
        before = self.mask()
        inside = None
        with self.assertRaisesRegex(ValueError, "invalid_publication_identity"):
            with capture.deferred_signals():
                inside = self.mask()
                raise ValueError("invalid_publication_identity")
        self.assertLessEqual(self.HELD, inside)
        self.assertEqual(self.mask(), before)

    def test_mask_is_restored_when_the_block_call_itself_is_cancelled(self):
        # CPython's pthread_sigmask changes the mask and then runs pending handlers, so a
        # cancellation that tripped just before entry raises from the call that holds the
        # signals, after the mask has already changed.
        real = signal.pthread_sigmask
        before = self.mask()
        self.addCleanup(real, signal.SIG_SETMASK, before)

        def hold_then_cancel(how, mask):
            previous = real(how, mask)
            if how == signal.SIG_BLOCK and set(mask):
                raise capture.StopRequested(signal.SIGTERM)
            return previous
        with mock.patch.object(capture.signal, "pthread_sigmask", hold_then_cancel):
            with self.assertRaises(capture.StopRequested):
                with capture.deferred_signals():
                    self.fail("the block must not run")
        self.assertEqual(self.mask(), before)

    def test_previous_mask_is_restored_rather_than_cleared(self):
        before = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            with capture.deferred_signals():
                pass
            self.assertEqual(self.mask(), before | {signal.SIGTERM})
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, before)
        self.assertEqual(self.mask(), before)

    def test_held_signal_is_delivered_when_the_block_ends(self):
        for number in sorted(self.HELD):
            with self.subTest(number=number):
                received, observed = [], None

                def stop(signum, _frame):
                    received.append(signum)
                    raise capture.StopRequested(signum)
                previous = signal.signal(number, stop)
                try:
                    with self.assertRaises(capture.StopRequested) as caught:
                        with capture.deferred_signals():
                            signal.raise_signal(number)  # This thread only, so it stays held here.
                            observed = (number in signal.sigpending(), list(received))
                    # Observations are checked outside: a failure inside would be replaced by the stop.
                    self.assertEqual(observed, (True, []))
                    self.assertEqual(caught.exception.number, number)
                    self.assertEqual(received, [number])
                finally:
                    signal.signal(number, previous)

    def test_only_the_calling_thread_holds_signals(self):
        # pthread_sigmask is per thread. Python runs handlers on the main thread only, so a
        # supervisor running elsewhere cannot be interrupted there and the main thread is untouched.
        entered, release, seen = threading.Event(), threading.Event(), {}

        def run():
            seen["before"] = self.mask()
            with capture.deferred_signals():
                seen["inside"] = self.mask()
                entered.set()
                release.wait(10)
            seen["after"] = self.mask()
        main_before = self.mask()
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            main_during = self.mask()
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertLessEqual(self.HELD, seen["inside"])
        self.assertEqual(seen["after"], seen["before"])
        self.assertEqual(main_during, main_before)
