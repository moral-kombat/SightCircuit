"""The supervision primitive: signal policies, hold-and-arm, the report stream, budgets and the arm handshake."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import unittest
from unittest import mock

from sightcircuit.runtime import supervision
from sightcircuit.runtime.supervision import (
    GRACE, RAISE_ON_SIGNAL, RECORD_AND_POLL, Cancelled, Grace, ReportStream, SignalPolicy, Signals,
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
            self.assertNotIn(signal.SIGHUP, handlers.installed)
            self.assertIn(signal.SIGTERM, handlers.installed)
            self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            os.kill(os.getpid(), signal.SIGHUP)  # Stays ignored: nothing raises, nothing is recorded.
            time.sleep(0.05)
            self.assertFalse(handlers.received)
        with Signals(RECORD_AND_POLL) as handlers:
            self.assertIn(signal.SIGHUP, handlers.installed)
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

    def test_raise_mode_raises_while_armed_only_notes_while_held_and_raises_on_resume(self):
        policy = SignalPolicy("raise", "override")
        with Signals(policy) as handlers:
            armed = handlers.hold()
            self.assertTrue(armed)
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
            with self.assertRaises(Cancelled):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(0.5)
            self.assertTrue(handlers.received)
        self.assertFalse(handlers.hold() and False)  # A held handler reports it was not armed.

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
