"""The supervision primitive: signal policies, hold-and-arm, the report stream, budgets and the arm handshake."""
import io
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

from sightcircuit.runtime import supervision
from sightcircuit.runtime.supervision import (
    GRACE, RAISE_ON_SIGNAL, RECORD_AND_POLL, Cancelled, Grace, OwnedGroup, ReportStream, SignalPolicy, Signals,
)

ROOT = Path(__file__).resolve().parents[1]
POSIX = os.name == "posix"


def accept(report, returncode):
    if report.get("exit_code") != returncode:
        raise ValueError("exit_code_mismatch")


class SignalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.previous = {number: signal.getsignal(number) for number in RECORD_AND_POLL.numbers}
        for number, handler in self.previous.items():
            self.addCleanup(signal.signal, number, handler)

    def test_the_two_policies_and_their_validation(self):
        self.assertEqual((RECORD_AND_POLL.mode, RECORD_AND_POLL.inherited_ignore), ("record", "override"))
        self.assertEqual((RAISE_ON_SIGNAL.mode, RAISE_ON_SIGNAL.inherited_ignore), ("raise", "honour"))
        for mode, inherited in (("poll", "override"), ("record", "ignore")):
            with self.subTest(mode=mode, inherited=inherited), self.assertRaisesRegex(ValueError, "unsupported_signal_policy"):
                SignalPolicy(mode, inherited)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP")
    def test_an_inherited_ignore_is_honoured_or_overridden_as_the_policy_says(self):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with Signals(RAISE_ON_SIGNAL) as handlers:
            self.assertNotIn(signal.SIGHUP, handlers.handled)
            self.assertIn(signal.SIGTERM, handlers.handled)
            self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            os.kill(os.getpid(), signal.SIGHUP)  # Stays ignored: nothing raises, nothing is recorded.
            time.sleep(0.05)
            self.assertFalse(handlers.received)
        with Signals(RECORD_AND_POLL) as handlers:
            self.assertIn(signal.SIGHUP, handlers.handled)
            self.assertIsNot(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(0.05)
            self.assertEqual((handlers.number, handlers.recorded), (signal.SIGHUP, [signal.SIGHUP]))
        self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)  # Restored.

    def test_record_mode_records_the_first_signal_and_counts_the_rest(self):
        with Signals(RECORD_AND_POLL) as handlers:
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
            self.assertEqual(handlers.number, signal.SIGTERM)
            self.assertEqual(handlers.recorded, [signal.SIGTERM, signal.SIGINT])
            self.assertEqual(handlers.count, 2)
            handlers.resume(handlers.hold())  # Never raises in record mode.

    def test_a_freshly_entered_handler_is_held_and_a_signal_then_is_delivered_once_on_arm(self):
        # The window between installing the handlers and the first hold() would otherwise raise with no report.
        policy = SignalPolicy("raise", "override")
        with Signals(policy) as handlers:
            self.assertFalse(handlers.armed)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            self.assertEqual(handlers.number, signal.SIGTERM)  # Noted, not raised.
            with self.assertRaises(Cancelled) as raised:
                handlers.arm()
            self.assertEqual(raised.exception.number, signal.SIGTERM)
            self.assertTrue(handlers.armed)
            handlers.arm()  # Delivered once: arming again raises nothing.

    def test_raise_mode_raises_while_armed_only_notes_while_held_and_raises_on_resume(self):
        policy = SignalPolicy("raise", "override")
        with Signals(policy) as handlers:
            handlers.arm()
            armed = handlers.hold()
            self.assertTrue(armed)
            self.assertFalse(handlers.armed)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            self.assertEqual(handlers.number, signal.SIGTERM)  # Noted, not raised.
            with self.assertRaises(Cancelled) as raised:
                handlers.resume(armed)
            self.assertEqual(raised.exception.number, signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)  # Only the first signal ever raises.
            time.sleep(0.05)
            self.assertEqual(handlers.recorded, [signal.SIGTERM, signal.SIGINT])
        with Signals(policy) as handlers:
            handlers.arm()
            with self.assertRaises(Cancelled):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(0.5)
            self.assertTrue(handlers.received)
            self.assertTrue(handlers.hold())  # hold() reports the state it found: armed...
            self.assertFalse(handlers.hold())  # ...and then held.

    def test_holding_brackets_a_region_and_raises_a_noted_signal_on_exit_with_the_callers_exception(self):
        class Stopped(Cancelled):
            pass
        with Signals(SignalPolicy("raise", "override"), exception=Stopped) as handlers:
            handlers.arm()
            with self.assertRaises(Stopped) as raised:
                with handlers.holding():
                    self.assertFalse(handlers.armed)
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(0.05)
                    self.assertEqual(handlers.number, signal.SIGTERM)  # Noted inside the block.
            self.assertEqual(raised.exception.number, signal.SIGTERM)
            self.assertTrue(handlers.armed)  # Resumed to what hold() found.
        with Signals(SignalPolicy("raise", "override")) as handlers:
            with handlers.holding():
                pass
            self.assertFalse(handlers.armed)  # Held before, held after.

    def test_handlers_require_the_main_thread(self):
        errors = []

        def install():
            try:
                with Signals():
                    pass
            except ValueError as error:
                errors.append(str(error))
        thread = threading.Thread(target=install)
        thread.start()
        thread.join()
        self.assertEqual(errors, ["worker_supervisor_requires_main_thread"])


class GraceTests(unittest.TestCase):
    def test_budgets_round_trip_and_the_supervisor_of_a_cleaner_covers_its_longest_cleanup(self):
        grace = Grace(arm=0.5, cooperative=2, terminate=0.25, kill=0.25, report=0.5)
        self.assertEqual(Grace.from_dict(grace.as_dict()), grace)
        self.assertEqual(Grace.from_dict({"cooperative": 7}), Grace(cooperative=7.0))
        self.assertEqual(grace.total(), 3.5)
        outer = grace.for_supervisor_of()
        self.assertEqual(outer.cooperative, 4.5)
        self.assertEqual((outer.arm, outer.terminate, outer.kill, outer.report), (0.5, 0.25, 0.25, 0.5))
        self.assertEqual((GRACE.arm, GRACE.cooperative, GRACE.terminate, GRACE.kill, GRACE.report), (1, 5, 1, 1, 1))

    def test_a_worker_join_shares_the_supervisor_cooperative_grace_less_a_reserve(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(supervision.COOPERATIVE_GRACE_VARIABLE, None)
            self.assertEqual(supervision.cooperative_budget(0.5), 0.5)
            for value, expected in (("5.0", 4.0), ("1.2", 0.5), ("0.3", 0.5), ("garbage", 0.5), ("12", 11.0)):
                os.environ[supervision.COOPERATIVE_GRACE_VARIABLE] = value
                with self.subTest(value=value):
                    self.assertEqual(supervision.cooperative_budget(0.5), expected)
            os.environ[supervision.COOPERATIVE_GRACE_VARIABLE] = "5"
            self.assertEqual(supervision.cooperative_budget(0.5, reserve=2), 3.0)


@unittest.skipUnless(POSIX, "Inherited descriptors")
class ArmHandshakeTests(unittest.TestCase):
    def test_report_armed_writes_once_closes_the_descriptor_and_forgets_the_variable(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        with mock.patch.dict(os.environ, {supervision.ARMED_FD_VARIABLE: str(writer)}):
            self.assertTrue(supervision.report_armed())
            self.assertNotIn(supervision.ARMED_FD_VARIABLE, os.environ)
            self.assertFalse(supervision.report_armed())  # Nothing to tell twice: a descendant cannot misuse it.
        self.assertEqual(os.read(reader, 16), b"armed\n")
        with self.assertRaises(OSError):
            os.fstat(writer)  # Closed.

    def test_report_armed_without_a_supervisor_or_with_a_bad_descriptor_is_a_quiet_no(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(supervision.ARMED_FD_VARIABLE, None)
            self.assertFalse(supervision.report_armed())
            os.environ[supervision.ARMED_FD_VARIABLE] = "not-a-number"
            self.assertFalse(supervision.report_armed())
            reader, writer = os.pipe()
            os.close(reader)
            os.close(writer)
            os.environ[supervision.ARMED_FD_VARIABLE] = str(writer)
            self.assertFalse(supervision.report_armed())


@unittest.skipUnless(os.name == "posix", "The stop relay uses a wakeup pipe")
class StopRelayTests(unittest.TestCase):
    """The worker's stop event is set by the relay thread, so the two ways the old handler lost a stop are gone."""

    def worker(self, body, timeout=8):
        command = [sys.executable, "-c", f"import json, os, signal, sys, threading, time; sys.path.insert(0, {str(ROOT)!r}); "
                   "from sightcircuit.runtime.guard import stop_on_signals; " + body]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self.fail(f"the worker hung: {error.stdout!r} {error.stderr!r}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_signal_inside_the_events_own_critical_section_does_not_deadlock_the_worker(self):
        # Event.wait() holds the event's condition lock while it checks the flag and registers its waiter; a
        # handler that called Event.set() there hung on that non-reentrant lock (the lane B flake's worker
        # traceback: wait -> set -> __enter__). The relay sets the event from its own thread once the lock is free.
        report = self.worker(
            "stop = stop_on_signals(); started = time.monotonic()\n"
            "with stop._cond:\n"
            "    os.kill(os.getpid(), signal.SIGTERM); time.sleep(0.05)\n"
            "print(json.dumps({'set': stop.wait(2), 'seconds': time.monotonic() - started}))")
        self.assertTrue(report["set"], report)
        self.assertLess(report["seconds"], 1.5, report)

    def test_a_signal_the_main_thread_cannot_handle_yet_still_wakes_its_wait(self):
        # Only a signal arriving during the wait's lock acquire interrupts it; a signal delivered just before
        # it, or to another thread, used to be handled after the acquire returned, 10 s later for a worker.
        # Delivering to another thread makes that deterministic: the main thread's acquire is never interrupted.
        report = self.worker(
            "stop = stop_on_signals()\n"
            "def deliver_here(): time.sleep(0.2); signal.pthread_kill(threading.get_ident(), signal.SIGTERM)\n"
            "threading.Thread(target=deliver_here, daemon=True).start(); started = time.monotonic()\n"
            "print(json.dumps({'set': stop.wait(6), 'seconds': time.monotonic() - started}))")
        self.assertTrue(report["set"], report)
        self.assertLess(report["seconds"], 2, report)

    def test_a_forked_child_forgets_the_relay_and_can_make_its_own(self):
        # After os.fork the child has no relay thread and must not write into its parent's pipe: the inherited
        # handler is inert, the descriptors are closed once and forgotten, and a fresh stop_on_signals in the
        # child makes a new relay whose signals work; the parent's relay is untouched by the child's fork callbacks.
        report = self.worker(
            "import warnings; warnings.simplefilter('ignore', DeprecationWarning)\n"
            "from sightcircuit.runtime import supervision\n"
            "stop = stop_on_signals(); relay = supervision._stop_wakeup; pid = os.fork()\n"
            "if pid == 0:\n"
            "    inert = supervision._stop_wakeup is None and relay.write_fd == -1 and relay.read_fd == -1\n"
            "    relay.handle(signal.SIGTERM, None)\n"  # Must neither raise nor write anywhere.
            "    child_stop = stop_on_signals(); fresh = supervision._stop_wakeup is not relay\n"
            "    grandchild = os.fork()\n"
            "    if grandchild == 0:\n"
            "        os._exit(0 if supervision._stop_wakeup is None and relay.write_fd == -1 else 3)\n"
            "    _, status = os.waitpid(grandchild, 0)\n"
            "    os.kill(os.getpid(), signal.SIGTERM); child_ok = child_stop.wait(2)\n"
            "    os._exit(0 if inert and fresh and child_ok and os.waitstatus_to_exitcode(status) == 0 else 1)\n"
            "_, status = os.waitpid(pid, 0)\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
            "print(json.dumps({'child': os.waitstatus_to_exitcode(status), 'parent_set': stop.wait(2), "
            "'parent_relay_intact': supervision._stop_wakeup is relay and relay.write_fd >= 0}))")
        self.assertEqual(report, {"child": 0, "parent_set": True, "parent_relay_intact": True})

    def test_the_handler_takes_no_lock_and_later_events_join_the_relay(self):
        # In-process: the installed handler is the relay's write, not Event.set; every registered event is set.
        previous = {number: signal.getsignal(number) for number in supervision.STOP_SIGNALS}
        for number, handler in previous.items():
            self.addCleanup(signal.signal, number, handler)
        first, second = threading.Event(), threading.Event()
        self.assertIs(supervision.stop_on(first), first)
        self.assertIs(supervision.stop_on(second), second)
        relay = supervision._stop_wakeup
        for number in supervision.STOP_SIGNALS:
            self.assertEqual(signal.getsignal(number), relay.handle)
        self.assertTrue(relay.thread.is_alive() and relay.thread.daemon)
        self.assertEqual(signal.set_wakeup_fd(relay.write_fd), relay.write_fd)  # The wakeup descriptor is the relay's pipe.
        with first._cond:  # Held, as Event.wait holds it: the handler must still return at once.
            relay.handle(signal.SIGTERM, None)
        self.assertTrue(first.wait(2) and second.wait(2))
        # A byte of another signal (the supervisors' SIGHUP handler also writes the wakeup descriptor) sets nothing,
        # and one real signal's two bytes (the wakeup descriptor's and the handler's) are drained together, so an
        # event registered afterwards is not set by a stale byte.
        third = supervision.stop_on(threading.Event())
        os.write(relay.write_fd, bytes([signal.SIGHUP]))
        self.assertFalse(third.wait(0.2))
        signal.raise_signal(signal.SIGTERM)
        self.assertTrue(third.wait(2))
        time.sleep(0.1)
        fourth = supervision.stop_on(threading.Event())
        self.assertFalse(fourth.wait(0.3))


class WorkerSideTests(unittest.TestCase):
    def test_emit_event_writes_one_flushed_json_line(self):
        stream = io.StringIO()
        supervision.emit_event("progress", {"n": 1}, stream)
        supervision.emit_event("result", {"status": "done"}, stream)
        lines = stream.getvalue().splitlines()
        self.assertEqual([json.loads(line) for line in lines],
                         [{"event": "progress", "report": {"n": 1}}, {"event": "result", "report": {"status": "done"}}])

    def test_function_reference_names_a_module_level_function_of_the_checkout_by_its_file(self):
        self.assertEqual(supervision.function_reference(supervision.enter_worker),
                         {"module": "sightcircuit.runtime.supervision", "function": "enter_worker"})
        reference = supervision.function_reference(accept)  # A test module, however it was imported.
        self.assertEqual(reference, {"module": "tests.test_supervision", "function": "accept"})
        loaded = supervision.load_function(reference)  # The same function, by name and file: under discovery the
        self.assertEqual((loaded.__name__, loaded.__code__.co_filename),  # module may be imported a second time.
                         (accept.__name__, accept.__code__.co_filename))
        for function in (lambda: None, json.loads, supervision.Signals.hold):
            with self.subTest(function=function), self.assertRaisesRegex(ValueError, "module_level_in_checkout"):
                supervision.function_reference(function)
        for reference in ({"module": "json", "function": "loads"}, {"module": "os.path", "function": "join"},
                          {"module": "tests.missing", "function": "x"}, {"module": "", "function": "x"},
                          {"module": "tests.test_supervision", "function": "not an identifier"}):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "unsupported_worker_reference"):
                supervision.load_function(reference)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Parent-death signalling")
    def test_enter_worker_arms_the_parent_death_signal_and_checks_the_supervisor(self):
        with mock.patch.dict(os.environ, {supervision.SUPERVISOR_PID_VARIABLE: str(os.getppid())}):
            self.assertTrue(supervision.enter_worker())
        with mock.patch.dict(os.environ, {supervision.SUPERVISOR_PID_VARIABLE: "1"}):
            self.assertFalse(supervision.enter_worker())  # The supervisor named is not this process's parent.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(supervision.SUPERVISOR_PID_VARIABLE, None)
            self.assertTrue(supervision.enter_worker())  # Outside a supervisor: only the request is made.
        # The child asks for SIGTERM on parent death: kill an intermediate parent and the grandchild ends.
        code = ("import os, subprocess, sys, time; sys.path.insert(0, sys.argv[1]); "
                "child = subprocess.Popen([sys.executable, '-c', 'import sys, time; sys.path.insert(0, sys.argv[1]); "
                "from sightcircuit.runtime.supervision import enter_worker; enter_worker(); "
                "open(sys.argv[2], \"w\").write(str(__import__(\"os\").getpid())); time.sleep(30)', sys.argv[1], sys.argv[2]]); "
                "time.sleep(30)")
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "pid"
            parent = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(pid_file)])
            try:
                deadline = time.monotonic() + 10
                while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
                    time.sleep(0.02)
                grandchild = int(pid_file.read_text())
                parent.kill()
                parent.wait(timeout=5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        os.kill(grandchild, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    os.kill(grandchild, signal.SIGKILL)
                    self.fail("the grandchild outlived its parent")
            finally:
                if parent.poll() is None:
                    parent.kill()


class ReportStreamTests(unittest.TestCase):
    def stream_of(self, data):
        reader, writer = os.pipe()
        stream = ReportStream(os.fdopen(reader, "rb", buffering=0))
        stream.start()  # Reading first: the data can exceed the pipe's buffer.
        with os.fdopen(writer, "wb", buffering=0) as sink:
            try:
                sink.write(data)
            except BrokenPipeError:
                pass  # The reader stopped at its byte limit.
        self.assertTrue(stream.done.wait(5))
        return stream

    def test_events_are_queued_in_order_and_everything_else_is_the_report(self):
        stream = self.stream_of(b'{"event": "progress", "report": {"n": 1}}\n'
                                b'not json\n'
                                b'{"event": "progress", "report": {"n": 2}}\n'
                                b'{"event": "ignored"}\n'
                                b'{"status": "done", "exit_code": 0}\n')
        seen = []
        stream.dispatch(seen.append)
        self.assertEqual(seen, [{"n": 1}, {"n": 2}])
        self.assertEqual(stream.errors, [])
        with self.assertRaises(ValueError):
            stream.candidate()  # "not json" is part of the report text.
        stream = self.stream_of(b'{"event": "progress", "report": {"n": 1}}\n{"status": "done"}')
        stream.dispatch(None)
        self.assertEqual(stream.candidate(), {"status": "done"})

    def test_a_result_event_is_the_report_and_an_indented_report_still_parses(self):
        stream = self.stream_of(b'{"event": "result", "report": {"status": "done", "exit_code": 2}}\n')
        stream.dispatch(None)
        self.assertEqual(stream.candidate(), {"status": "done", "exit_code": 2})
        stream = self.stream_of(json.dumps({"status": "done", "nested": {"a": [1, 2]}}, indent=2).encode())
        stream.dispatch(None)
        self.assertEqual(stream.candidate(), {"status": "done", "nested": {"a": [1, 2]}})

    def test_the_byte_limit_bounds_a_pending_line_and_the_report_text(self):
        stream = self.stream_of(b"x" * (supervision.MAX_REPORT_BYTES + 4096))
        self.assertEqual(stream.errors, ["worker_report_byte_limit"])
        lines = b"".join(b"y" * 1024 + b"\n" for _ in range(supervision.MAX_REPORT_BYTES // 1024 + 2))
        stream = self.stream_of(lines)
        self.assertEqual(stream.errors, ["worker_report_byte_limit"])
        # Events are dispatched, not kept, so a long stream of them is not bounded by the report limit.
        events = b"".join(b'{"event": "progress", "report": {"n": %d}}\n' % n for n in range(400))
        stream = self.stream_of(events + b'{"status": "done"}\n')
        self.assertEqual(stream.errors, [])
        self.assertEqual(len(stream.events), 400)


@unittest.skipUnless(sys.platform.startswith("linux"), "Process groups and subreaping")
class OwnedGroupTests(unittest.TestCase):
    def test_alive_wait_and_terminate_cover_the_whole_group_with_reaping(self):
        # A leader that starts a SIGTERM-ignoring child and exits at once; the child is reparented to this process.
        with supervision.child_reaper():
            leader = subprocess.Popen([sys.executable, "-c", (
                "import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
                "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); sys.exit(0)")],
                start_new_session=True)
            group = OwnedGroup(leader)
            self.assertEqual(group.pgid, leader.pid)
            self.assertTrue(group.alive())
            self.assertFalse(group.wait_gone(0.3))  # The leader is reaped; its child keeps the group alive.
            self.assertIsNotNone(leader.returncode)
            started = time.monotonic()
            self.assertTrue(group.terminate(Grace(terminate=0.3, kill=1.0)))
            self.assertGreaterEqual(time.monotonic() - started, 0.3)  # SIGTERM was ignored: KILL after the grace.
            self.assertFalse(group.alive())
            with self.assertRaises(ProcessLookupError):
                os.killpg(leader.pid, 0)

    def test_a_group_that_exits_on_sigterm_is_gone_before_the_kill(self):
        with supervision.child_reaper():
            leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
            group = OwnedGroup(leader)
            started = time.monotonic()
            self.assertTrue(group.terminate(Grace(terminate=5, kill=5)))
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(leader.returncode, -signal.SIGTERM)


def worker_command(body):
    return [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); " + body]


COOPERATIVE = ("import json, sys; from sightcircuit.runtime.guard import stop_on_signals; stop = stop_on_signals(); "
               "stop.wait(10); print(json.dumps({'status': 'cancelled', 'exit_code': 130, 'cleanup_ok': True})); "
               "sys.exit(130)")


@unittest.skipUnless(sys.platform.startswith("linux"), "Owned sessions and process groups")
class SupervisedOwnedTests(unittest.TestCase):
    def setUp(self):
        self.previous = {number: signal.getsignal(number) for number in RECORD_AND_POLL.numbers}
        for number, handler in self.previous.items():
            self.addCleanup(signal.signal, number, handler)

    def signal_self_soon(self, number, delay):
        timer = threading.Timer(delay, lambda: os.kill(os.getpid(), number))
        timer.start()
        self.addCleanup(timer.cancel)

    def test_a_worker_report_and_the_report_block(self):
        report = supervision.supervise_owned(
            worker_command("import json; print(json.dumps({'status': 'done', 'exit_code': 0, 'cleanup_ok': True}))"),
            {"report_kind": "test"}, 10, accept)
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("done", 0, True))
        for key in ("worker_pid", "worker_armed", "worker_reaped", "worker_exit_code", "worker_tree_reaped",
                    "report_reader_stopped", "worker_forced_termination", "worker_termination_requested",
                    "supervisor_elapsed_seconds"):
            self.assertIn(key, report)
        self.assertFalse(report["worker_armed"])  # It never called stop_on_signals.
        self.assertTrue(report["worker_reaped"] and report["worker_tree_reaped"] and report["report_reader_stopped"])
        self.assertFalse(report["worker_forced_termination"] or report["worker_termination_requested"])

    def test_raise_mode_completes_the_cleanup_and_then_raises_with_the_report(self):
        self.signal_self_soon(signal.SIGTERM, 0.1)  # Before the worker has armed: the arm wait delivers it to a handler.
        with self.assertRaises(Cancelled) as raised:
            supervision.supervise_owned(worker_command(COOPERATIVE), {"report_kind": "test"}, 10, accept,
                                        signals=RAISE_ON_SIGNAL)
        report = raised.exception.report
        self.assertEqual(raised.exception.number, signal.SIGTERM)
        self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 128 + signal.SIGTERM))
        self.assertTrue(report["cleanup_ok"], report)
        self.assertTrue(report["worker_armed"], report)
        self.assertFalse(report["worker_forced_termination"], report)
        self.assertEqual(report["worker_exit_code"], 130)
        with self.assertRaises(ProcessLookupError):
            os.killpg(report["worker_pid"], 0)

    def test_a_signal_during_process_start_is_held_and_raised_once_the_worker_can_be_ended(self):
        real_popen = supervision.subprocess.Popen
        seen = []

        def popen(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)  # Arrives while the handler is held: only noted.
            time.sleep(0.05)
            seen.append(signal.getsignal(signal.SIGTERM))
            return real_popen(*args, **kwargs)
        with mock.patch.object(supervision.subprocess, "Popen", side_effect=popen), self.assertRaises(Cancelled) as raised:
            supervision.supervise_owned(worker_command(COOPERATIVE), {"report_kind": "test"}, 10, accept,
                                        signals=RAISE_ON_SIGNAL)
        self.assertEqual(len(seen), 1)  # Popen returned: the signal did not interrupt it.
        report = raised.exception.report
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["worker_reaped"] and report["worker_tree_reaped"], report)
        self.assertTrue(report["cleanup_ok"], report)

    def test_a_signal_in_the_error_path_is_delivered_once_with_the_finished_report(self):
        # Popen fails right after a signal lands: raise mode still ends with one Cancelled carrying the report.
        def popen(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            raise OSError("fixture_popen_failed")
        with mock.patch.object(supervision.subprocess, "Popen", side_effect=popen), self.assertRaises(Cancelled) as raised:
            supervision.supervise_owned(worker_command(COOPERATIVE), {"report_kind": "test"}, 10, accept,
                                        signals=RAISE_ON_SIGNAL)
        report = raised.exception.report
        self.assertIsNotNone(report)
        self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 128 + signal.SIGTERM))
        self.assertEqual(report["reason"], "fixture_popen_failed")
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["worker_reaped"])
        self.assertIs(signal.getsignal(signal.SIGTERM), self.previous[signal.SIGTERM])  # Handlers restored.

    def test_record_mode_returns_the_cancelled_report_and_a_never_arming_worker_is_forced_after_the_arm_wait(self):
        self.signal_self_soon(signal.SIGTERM, 0.3)
        grace = Grace(arm=0.5, cooperative=0.5, terminate=0.3, kill=0.3, report=0.3)
        started = time.monotonic()
        report = supervision.supervise_owned(
            worker_command("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"),
            {"report_kind": "test"}, 10, accept, grace=grace)
        elapsed = time.monotonic() - started
        self.assertEqual(report["status"], "cancelled")
        self.assertFalse(report["worker_armed"])
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])
        self.assertTrue(report["worker_tree_reaped"])
        self.assertGreaterEqual(elapsed, 0.3 + grace.arm + grace.cooperative + grace.terminate)
        self.assertLess(elapsed, 0.3 + grace.total() + 2)

    def test_progress_events_reach_the_callback_on_the_supervising_thread(self):
        body = ("import json, threading; main = threading.main_thread(); "
                "[print(json.dumps({'event': 'progress', 'report': {'n': n}}), flush=True) for n in range(3)]; "
                "print(json.dumps({'event': 'result', 'report': {'status': 'done', 'exit_code': 0, 'cleanup_ok': True, 'n': 3}}))")
        seen = []
        report = supervision.supervise_owned(worker_command(body), {"report_kind": "test"}, 10, accept,
                                             progress=lambda snapshot: seen.append((snapshot, threading.current_thread())))
        self.assertEqual([snapshot for snapshot, _ in seen], [{"n": 0}, {"n": 1}, {"n": 2}])
        self.assertTrue(all(thread is threading.main_thread() for _, thread in seen))
        self.assertEqual((report["status"], report["n"], report["cleanup_ok"]), ("done", 3, True))

    def test_a_callers_stop_ends_a_cooperative_worker_with_cleanup_confirmed(self):
        stop = threading.Event()
        timer = threading.Timer(0.4, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        report = supervision.supervise_owned(worker_command(COOPERATIVE), {"report_kind": "test"}, 10, accept,
                                             stop=stop)
        self.assertEqual((report["status"], report["exit_code"]), ("stopped", 1))
        self.assertTrue(report["cleanup_ok"])
        self.assertTrue(report["worker_termination_requested"])
        self.assertFalse(report["worker_forced_termination"])
        self.assertTrue(report["worker_reaped"] and report["worker_tree_reaped"])
        self.assertEqual(report["worker_exit_code"], 130)
        self.assertLess(time.monotonic() - started, 4)
        for number in RECORD_AND_POLL.numbers:  # The stop is not a signal: none was recorded.
            self.assertEqual(signal.getsignal(number), self.previous[number])

    def test_a_callers_stop_escalates_on_a_deaf_worker_and_does_not_confirm_cleanup(self):
        stop = threading.Event()
        stop.set()  # Already set: the worker is ended as soon as the loop sees it.
        grace = Grace(arm=0.2, cooperative=0.3, terminate=0.3, kill=0.3, report=0.3)
        report = supervision.supervise_owned(
            worker_command("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"),
            {"report_kind": "test"}, 10, accept, grace=grace, stop=stop)
        self.assertEqual(report["status"], "stopped")
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])
        self.assertTrue(report["worker_tree_reaped"])

    def test_the_worker_sees_its_cooperative_grace(self):
        report = supervision.supervise_owned(
            worker_command("import json, os; print(json.dumps({'status': 'done', 'exit_code': 0, 'cleanup_ok': True, "
                           "'grace': os.environ.get('SIGHTCIRCUIT_COOPERATIVE_GRACE'), "
                           "'armed_fd': 'SIGHTCIRCUIT_ARMED_FD' in os.environ}))"),
            {"report_kind": "test"}, 10, accept, grace=Grace(cooperative=2.5))
        self.assertEqual(report["grace"], "2.5")
        self.assertTrue(report["armed_fd"])


if __name__ == "__main__":
    unittest.main()
