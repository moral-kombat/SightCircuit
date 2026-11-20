"""The presence stop: the watcher's line rules, ``PresenceWatch`` and recognition runs it ends.

The watcher here is the stand-in of ``tests/fake_presence_watcher.py``,
installed as an executable outside the checkout, and the helper is the camera
stand-in. The ``PresenceWatch`` tests are standard library only; the
recognition tests need NumPy and OpenCV as the recognition command's do. No
SteamVR, no camera, no display. The times asserted are this host's and bound
this code's own latency only: none is a Frame measurement.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
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
from sightcircuit.camera import marks, presence  # noqa: E402
from sightcircuit.commands import camera_recognize as command  # noqa: E402
from sightcircuit.runtime.supervision import RECORD_AND_POLL  # noqa: E402
from tests import fake_camera_export, fake_presence_watcher  # noqa: E402
from tests.test_camera_recognize import CONVERSION, StandInDetector  # noqa: E402

LINUX = sys.platform.startswith("linux")
MS = 1_000_000
RUN = {"fps": 10, "max_seconds": 8, "max_frames": 80}  # Long enough that only a stop ends it early.
END_BOUND_SECONDS = 4.0  # A run the watch ends must be over well inside its eight seconds.


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # A zombie still answers the signal; it is not running.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_until(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def line(event, sequence=0, **fields):
    now = time.monotonic_ns()
    return dict(fields, event=event, seq=sequence, monotonic_ns=now, realtime_ns=time.time_ns())


def ready_line(**changes):
    return line("ready", **{**presence.READY_FACTS, "signals": presence.signal_table(),
                            "interfaces_requested": ["IVRSystem_026"], "hmd_activity": "user_interaction",
                            "hmd_has_proximity_sensor": True, **changes})


def check(message, *, sequence=0, ready_seen=False):
    return presence.check_line(message, sequence=sequence, ready_seen=ready_seen, launched_ns=0,
                               received_ns=time.monotonic_ns())


class SignalTableTests(unittest.TestCase):
    def test_only_steamvrs_standby_event_ends_a_run(self):
        self.assertEqual(presence.ENDING_SIGNALS, ("enter_standby_event",))
        self.assertEqual(presence.END_REASONS, ("standby",))
        self.assertLessEqual(set(presence.END_REASONS), set(marks.PRESENCE_END_REASONS))
        for name in ("leave_standby_event", "proximity_off", "proximity_on", "user_interaction_ended",
                     "user_interaction_started", "hmd_activity_changed"):
            self.assertIsNone(presence.SIGNALS[name], name)

    def test_the_native_table_is_the_python_table(self):
        header = (ROOT / "tools/presence_signals.hpp").read_text()
        import re
        native = {name: (ends == "true", None if reason == "nullptr" else reason.strip('"'))
                  for name, ends, reason in re.findall(r'constexpr Signal k\w+\{"(\w+)", (true|false), ("\w+"|nullptr)\};',
                                                       header)}
        self.assertEqual(native, {name: (reason is not None, reason) for name, reason in presence.SIGNALS.items()})
        self.assertIn(f'kProtocol = "{presence.PROTOCOL}"', header)

    def test_the_watch_outlasts_the_run_within_the_watchers_own_bound(self):
        self.assertEqual(presence.watch_seconds(30), 50)
        self.assertEqual(presence.watch_seconds(90), 110)
        self.assertEqual(presence.watch_seconds(120), presence.MAX_WATCH_SECONDS)


class LineRuleTests(unittest.TestCase):
    def test_a_ready_line_is_accepted_only_as_the_watcher_this_module_knows(self):
        self.assertEqual(check(ready_line()), "ready")
        refused = {"camera_data_read": True, "overlay_created": True, "settings_written": True, "starts_steamvr": True,
                   "application_type": "VRApplication_Overlay", "protocol": "presence-watch/0", "hmd_connected": False,
                   "hmd_activity": "asleep", "hmd_has_proximity_sensor": "yes",
                   "interfaces_requested": ["IVRSystem_026", "IVRTrackedCamera_006"],
                   "signals": [dict(entry, ends_run=True) for entry in presence.signal_table()]}
        for name, value in refused.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                check(ready_line(**{name: value}))
        with self.assertRaises(ValueError):
            check(ready_line(camera_data_read=0))  # False by type, not by truthiness.
        with self.assertRaisesRegex(ValueError, "ready_repeated"):
            check(ready_line(), ready_seen=True)

    def test_sequence_times_and_order_are_strict(self):
        beat = line("heartbeat", 3)
        self.assertEqual(check(beat, sequence=3, ready_seen=True), "heartbeat")
        with self.assertRaisesRegex(ValueError, "line_sequence_broken"):
            check(beat, sequence=4, ready_seen=True)
        with self.assertRaisesRegex(ValueError, "line_before_ready"):
            check(line("heartbeat"))
        with self.assertRaisesRegex(ValueError, "line_time_out_of_range"):
            check(dict(beat, monotonic_ns=time.monotonic_ns() + 10**10), sequence=3, ready_seen=True)
        with self.assertRaisesRegex(ValueError, "line_time_out_of_range"):
            presence.check_line(beat, sequence=3, ready_seen=True, launched_ns=beat["monotonic_ns"] + 1,
                                received_ns=time.monotonic_ns())
        for broken in (dict(beat, seq=True), dict(beat, monotonic_ns="1"), dict(beat, realtime_ns=None),
                       dict(beat, event="frame"), [beat], "heartbeat"):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                check(broken, sequence=3, ready_seen=True)

    def test_a_presence_line_must_agree_with_the_table(self):
        good = line("presence", 1, signal="enter_standby_event", ends_run=True, end_reason="standby",
                    hmd_activity="standby", event_age_ms=4)
        self.assertEqual(check(good, sequence=1, ready_seen=True), "presence")
        for change in ({"signal": "camera_stopped"}, {"ends_run": False}, {"end_reason": "headset_removed"},
                       {"hmd_activity": "gone"}, {"event_age_ms": -1}, {"event_age_ms": 1.5}, {"ends_run": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check(dict(good, **change), sequence=1, ready_seen=True)
        quiet = line("presence", 1, signal="proximity_off", ends_run=False, end_reason=None,
                     hmd_activity="user_interaction", event_age_ms=None)
        self.assertEqual(check(quiet, sequence=1, ready_seen=True), "presence")
        with self.assertRaises(ValueError):  # The proximity sensor never ends a run, whatever the watcher says.
            check(dict(quiet, ends_run=True, end_reason="headset_removed"), sequence=1, ready_seen=True)

    def test_end_and_error_lines_carry_closed_reasons(self):
        self.assertEqual(check(line("end", 2, reason="stop_signal"), sequence=2, ready_seen=True), "end")
        self.assertEqual(check(line("error", reason="vrserver_not_running")), "error")
        self.assertEqual(check(line("end", reason="headset_not_connected")), "end")
        for message, seen in ((line("end", reason="tired"), True), (line("error", reason="anything"), False),
                              (line("error", reason="vrserver_not_running"), True)):
            with self.subTest(message=message), self.assertRaises(ValueError):
                check(message, ready_seen=seen)


@unittest.skipUnless(LINUX, "The watcher is a Linux process")
class WatchCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.watcher = fake_presence_watcher.install(cls.directory.name)
        cls.helper = fake_camera_export.install(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.pid_file = Path(self.directory.name) / f"watcher-{self.id().rsplit('.', 1)[1]}.pid"
        self.environment(None)

    def environment(self, scenario):
        values = {"FAKE_PRESENCE_PID_FILE": str(self.pid_file)}
        if scenario is not None:
            values["FAKE_PRESENCE_SCENARIO"] = scenario
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)
        if scenario is None:
            os.environ.pop("FAKE_PRESENCE_SCENARIO", None)

    def watcher_pid(self):
        return int(self.pid_file.read_text()) if self.pid_file.is_file() else None

    def assert_no_watcher_left(self):
        pid = self.watcher_pid()
        if pid is not None:
            self.assertTrue(wait_until(lambda: not alive(pid), 3.0), f"watcher {pid} is still running")


class PresenceWatchTests(WatchCase):
    def watch(self, scenario=None, **options):
        self.environment(scenario)
        self.stop = threading.Event()
        watch = presence.PresenceWatch(self.watcher, 5, self.stop, **options)
        self.addCleanup(watch.close)
        return watch

    def test_an_armed_watch_reports_ready_and_leaves_no_process(self):
        watch = self.watch()
        self.assertIsNone(watch.start())
        self.assertTrue(watch.armed)
        pid = self.watcher_pid()
        self.assertTrue(alive(pid))
        self.assertTrue(wait_until(lambda: watch.heartbeats >= 2))
        self.assertFalse(self.stop.is_set())
        watch.run_ended()
        watch.close()
        watch.close()  # Idempotent.
        self.assertFalse(alive(pid))
        report = watch.report(marks.Clock())
        self.assertEqual((report["requested"], report["armed"], report["refusal"], report["ended_run"], report["lost"]),
                         (True, True, None, False, None))
        self.assertEqual((report["end"]["reason"], report["exit_code"], report["forced_kill"], report["cleanup_ok"]),
                         ("stop_signal", 0, False, True))
        self.assertEqual(report["watcher"]["max_seconds"], presence.watch_seconds(5))
        self.assertEqual(report["ready"]["hmd_activity"], "user_interaction")
        self.assertIs(report["camera_data_read"], False)
        json.dumps(report)

    def test_the_standby_event_sets_the_stop_with_its_times(self):
        watch = self.watch("150:proximity_off,200:enter_standby_event")
        started = time.monotonic_ns()
        self.assertIsNone(watch.start())
        self.assertTrue(self.stop.wait(3.0))
        latency_ns = time.monotonic_ns() - watch.ending["monotonic_ns"]
        self.assertLess(latency_ns, 200 * MS)  # From the watcher's stamp to the stop being set, on this host.
        self.assertEqual((watch.ending["signal"], watch.ending["end_reason"], watch.ending["stop_already_set"]),
                         ("enter_standby_event", "standby", False))
        self.assertGreater(watch.ending["monotonic_ns"], started)
        self.assertGreaterEqual(watch.ending["received_monotonic_ns"], watch.ending["monotonic_ns"])
        watch.frame()
        watch.frame()
        watch.run_ended()
        watch.close()
        report = watch.report(marks.Clock(), helper_end_ns=watch.ending["monotonic_ns"] + 250 * MS)
        self.assertEqual([event["signal"] for event in report["events"]], ["proximity_off", "enter_standby_event"])
        self.assertEqual([event["ends_run"] for event in report["events"]], [False, True])
        self.assertEqual((report["ended_run"], report["run_end_reason"], report["frames_received_after_signal"],
                          report["signal_to_helper_end_ms"]), (True, "standby", 2, 250.0))
        self.assertEqual(report["ending_signal"]["utc_ns"] - report["ending_signal"]["monotonic_ns"],
                         report["launched"]["utc_ns"] - report["launched"]["monotonic_ns"])
        self.assert_no_watcher_left()

    def test_signals_that_do_not_end_a_run_are_recorded_and_set_no_stop(self):
        watch = self.watch("50:proximity_off,80:user_interaction_ended,110:hmd_activity_changed,140:proximity_on,"
                           "170:user_interaction_started,200:leave_standby_event")
        self.assertIsNone(watch.start())
        self.assertTrue(wait_until(lambda: watch.event_count == 6))
        self.assertFalse(self.stop.is_set())
        self.assertIsNone(watch.ending)
        watch.close()
        report = watch.report(marks.Clock())
        self.assertEqual((report["ended_run"], report["ending_signal"], report["lost"]), (False, None, None))
        self.assertEqual({event["ends_run"] for event in report["events"]}, {False})

    def test_a_signal_after_the_run_ended_is_recorded_and_ends_nothing(self):
        watch = self.watch("200:enter_standby_event")
        self.assertIsNone(watch.start())
        watch.run_ended()
        self.assertTrue(wait_until(lambda: watch.event_count == 1))
        self.assertFalse(self.stop.is_set())
        self.assertIsNone(watch.ending)
        watch.close()
        report = watch.report(marks.Clock())
        self.assertEqual((report["ended_run"], report["events"][0]["after_run_end"], report["lost"]), (False, True, None))
        self.assertEqual(watch.summary()["signal"], None)

    def test_a_stop_someone_else_asked_for_stays_theirs(self):
        watch = self.watch("200:enter_standby_event")
        self.assertIsNone(watch.start())
        self.stop.set()  # The wearer's Stop button, before SteamVR's signal.
        self.assertTrue(wait_until(lambda: watch.ending is not None))
        self.assertTrue(watch.ending["stop_already_set"])
        self.assertFalse(watch.ended_run())
        self.assertIsNone(watch.report(marks.Clock())["run_end_reason"])

    def test_leaving_standby_restarts_nothing(self):
        watch = self.watch("100:enter_standby_event,200:leave_standby_event,300:enter_standby_event")
        self.assertIsNone(watch.start())
        self.assertTrue(wait_until(lambda: watch.event_count == 3))
        first = watch.ending["monotonic_ns"]
        self.assertTrue(self.stop.is_set())  # Set once and never cleared: nothing resumes.
        report = watch.report(marks.Clock())
        self.assertEqual([event["signal"] for event in report["events"]],
                         ["enter_standby_event", "leave_standby_event", "enter_standby_event"])
        self.assertEqual(report["ending_signal"]["monotonic_ns"], first)  # The first signal is the run's.

    def test_refusals_before_the_run(self):
        cases = {"error=vrserver_not_running": "watcher_error:vrserver_not_running",
                 "error=headset_not_connected": "watcher_error:headset_not_connected",
                 "activity=standby": "headset_in_standby_at_start",
                 "table": "ready_signal_table_differs",
                 "camera": "ready_camera_data_read_refused",
                 "0:die": None}
        for scenario, expected in cases.items():
            with self.subTest(scenario=scenario):
                watch = self.watch(scenario)
                refusal = watch.start()
                if expected is None:  # Armed and gone at once: either refused at the start or lost right after it.
                    self.assertTrue(refusal is not None or wait_until(lambda: watch.lost is not None))
                    watch.close()
                    continue
                self.assertEqual(refusal, expected)
                self.assertFalse(watch.armed)
                self.assertFalse(self.stop.is_set())  # A refusal sets no stop: the caller launches nothing.
                watch.close()
                report = watch.report(marks.Clock())
                self.assertEqual((report["armed"], report["refusal"], report["cleanup_ok"]), (False, expected, True))
                self.assert_no_watcher_left()

    def test_a_watcher_that_never_says_ready_is_refused_in_time_and_ended(self):
        watch = self.watch("noready", ready_wait=0.4)
        started = time.monotonic()
        self.assertEqual(watch.start(), "watcher_not_ready_in_time")
        self.assertLess(time.monotonic() - started, 2.0)
        pid = self.watcher_pid()
        watch.close()
        self.assertFalse(alive(pid))
        self.assertFalse(watch.armed)

    def test_a_missing_or_tracked_watcher_is_refused_without_a_launch(self):
        for path, expected in ((Path(self.directory.name) / "absent", "watcher_not_found"),
                               (ROOT / "scripts/camera_recognize.py", "watcher_inside_work_tree"),
                               ("", "watcher_path_missing")):
            with self.subTest(path=path):
                stop = threading.Event()
                watch = presence.PresenceWatch(path, 5, stop)
                self.assertEqual(watch.start(), expected)
                self.assertIsNone(watch.process)
                watch.close()
                self.assertTrue(watch.cleanup_ok)
                self.assertEqual(watch.report(marks.Clock())["refusal"], expected)
        plain = Path(self.directory.name) / "not-executable"
        plain.write_text("#!/bin/sh\n")
        self.assertEqual(presence.PresenceWatch(plain, 5, threading.Event()).start(), "watcher_not_executable")

    def test_a_watcher_lost_mid_run_sets_the_stop_with_a_named_reason(self):
        cases = {"200:die": "watcher_exited", "200:garbage": "line_not_json", "200:skip": "line_sequence_broken",
                 "200:future": "line_time_out_of_range", "200:end=runtime_quit": "watcher_ended:runtime_quit",
                 "200:end=max_seconds": "watcher_ended:max_seconds"}
        for scenario, expected in cases.items():
            with self.subTest(scenario=scenario):
                watch = self.watch(scenario)
                self.assertIsNone(watch.start())
                self.assertTrue(self.stop.wait(3.0))
                self.assertEqual(watch.lost["reason"], expected)
                self.assertTrue(watch.lost_ended_run())
                self.assertFalse(watch.ended_run())
                watch.close()
                report = watch.report(marks.Clock())
                self.assertEqual((report["armed"], report["lost"]["reason"], report["lost_ended_run"], report["ended_run"]),
                                 (True, expected, True, False))
                self.assert_no_watcher_left()

    def test_a_silent_watcher_is_lost_on_its_heartbeat(self):
        watch = self.watch("200:silent", heartbeat_timeout=0.5)
        self.assertIsNone(watch.start())
        started = time.monotonic()
        self.assertTrue(self.stop.wait(3.0))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(watch.lost["reason"], "heartbeat_missed")
        pid = self.watcher_pid()
        self.assertTrue(alive(pid))  # Still running, only silent: the close ends it.
        watch.close()
        self.assertFalse(alive(pid))

    def test_a_loss_after_the_run_ended_sets_no_stop(self):
        watch = self.watch("200:die")
        self.assertIsNone(watch.start())
        watch.run_ended()
        self.assertTrue(wait_until(lambda: watch.lost is not None))
        self.assertFalse(self.stop.is_set())
        self.assertEqual((watch.lost["after_run_end"], watch.lost_ended_run()), (True, False))

    def test_a_deaf_watcher_is_killed_and_the_cleanup_is_not_confirmed(self):
        watch = self.watch("deaf")
        self.assertIsNone(watch.start())
        pid = self.watcher_pid()
        with mock.patch.object(presence, "STOP_WAIT_SECONDS", 0.3):
            watch.close()
        self.assertFalse(alive(pid))
        report = watch.report(marks.Clock())
        self.assertEqual((report["forced_kill"], report["cleanup_ok"], report["end"]), (True, False, None))

    def test_the_stop_of_the_run_is_not_a_loss(self):
        watch = self.watch()
        self.assertIsNone(watch.start())
        watch.close()  # Closed with the run not marked ended: a cancellation's path.
        self.assertIsNone(watch.lost)
        self.assertFalse(self.stop.is_set())


@unittest.skipUnless(CONVERSION and LINUX, "NumPy, OpenCV and owned sessions")
class RecognitionPresenceTests(WatchCase):
    def setUp(self):
        super().setUp()
        self.previous = {number: signal.getsignal(number) for number in RECORD_AND_POLL.numbers}
        for number, handler in self.previous.items():
            self.addCleanup(signal.signal, number, handler)

    def run_once(self, scenario, *, limits=RUN, cues=((100, "remove"),), helper_args=(), watcher=None, **options):
        self.environment(scenario)
        self.detector = StandInDetector()
        started = time.monotonic()
        report = command.recognize(self.helper, limits, (0,), detector_factory=lambda: self.detector, confirmed=True,
                                   cues=list(cues), terminal=mock.Mock(), target_label="chair",
                                   extra_args=("--fake-report-pid", *helper_args),
                                   presence_watcher=self.watcher if watcher is None else watcher, **options)
        self.elapsed = time.monotonic() - started
        json.dumps(report)
        self.assertEqual({key: report[key] for key in gates.claims()}, gates.claims())
        self.assertFalse(report["images_saved"] or report["pixels_in_report"] or report["display_started"])
        self.assert_no_watcher_left()
        ready = ((report.get("capture") or {}).get("stream") or {}).get("ready")
        if ready is not None:
            self.assertFalse(alive(ready["build"]["fake_pid"]))
        return report

    def test_steamvrs_signal_ends_the_run_within_a_bound_and_the_removal_entry_reads_it(self):
        report = self.run_once("600:proximity_off,700:enter_standby_event")
        self.assertEqual((report["status"], report["reason"], report["exit_code"], report["cleanup_ok"]),
                         ("presence_ended", "standby", 1, True))
        self.assertLess(self.elapsed, END_BOUND_SECONDS)
        self.assertLess(report["frames_received"], RUN["max_frames"])
        self.assertEqual(report["schema_version"], 3)
        watch = report["presence_watch"]
        self.assertEqual((watch["requested"], watch["armed"], watch["ended_run"], watch["run_end_reason"], watch["lost"]),
                         (True, True, True, "standby", None))
        self.assertEqual(watch["ending_signal"]["signal"], "enter_standby_event")
        self.assertEqual([event["signal"] for event in watch["events"]], ["proximity_off", "enter_standby_event"])
        # The helper itself was stopped: its own end record says so, and its frame-gap stop never had to act.
        self.assertEqual(report["capture"]["status"], "stopped")
        self.assertEqual(report["helper_end"]["reason"], "stop_signal")
        self.assertEqual(report["capture"]["supervision"]["worker_forced_termination"], False)
        self.assertGreaterEqual(watch["signal_to_helper_end_ms"], 0)
        self.assertLess(watch["signal_to_helper_end_ms"], 1500)
        self.assertEqual(len(report["summary"]["removal"]), 1)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_watch_armed"], removal["presence_signal"], removal["run_ended_by"],
                          removal["run_end_reason"], removal["helper_end_reason"], removal["helper_end_is_presence_stop"]),
                         (True, "enter_standby_event", "presence_watch", "standby", "stop_signal", True))
        self.assertGreater(removal["presence_signal_after_mark_ms"], 0)
        self.assertAlmostEqual(removal["helper_end_after_mark_ms"],
                               removal["presence_signal_after_mark_ms"] + removal["helper_end_after_presence_signal_ms"],
                               delta=0.01)
        self.assertEqual(removal["helper_end_after_presence_signal_ms"], watch["signal_to_helper_end_ms"])
        self.assertEqual(removal["frames_received_after_presence_signal"], watch["frames_received_after_signal"])
        self.assertIsNotNone(removal["helper_end_after_last_frame_ms"])
        self.assertIsNotNone(removal["last_frame_acquire_after_mark_ms"])
        self.assertTrue(removal["helper_end_within_limit"])

    def test_frames_that_arrive_after_the_signal_are_counted(self):
        # The helper takes 0.6 s to honour its stop and keeps nothing back: frames already written still arrive.
        report = self.run_once("300:enter_standby_event", helper_args=("--fake-term-delay", "0.6"))
        self.assertEqual((report["status"], report["reason"]), ("presence_ended", "standby"))
        watch = report["presence_watch"]
        self.assertGreaterEqual(watch["signal_to_helper_end_ms"], 500)
        self.assertGreaterEqual(watch["frames_received_after_signal"], 0)
        self.assertLessEqual(watch["frames_received_after_signal"], 2)
        self.assertEqual(report["summary"]["removal"][0]["frames_received_after_presence_signal"],
                         watch["frames_received_after_signal"])

    def test_a_signal_before_the_first_frame_ends_the_run_with_no_frame(self):
        report = self.run_once("300:enter_standby_event", cues=(), helper_args=("--fake-first-frame-delay", "5"))
        self.assertEqual((report["status"], report["reason"], report["frames_received"], report["frames_processed"]),
                         ("presence_ended", "standby", 0, 0))
        self.assertLess(self.elapsed, END_BOUND_SECONDS)
        self.assertEqual(report["helper_end"]["reason"], "stop_signal")
        self.assertIsNone(report["first_frame"])
        self.assertEqual(report["presence_watch"]["frames_received_after_signal"], 0)
        self.assertEqual(self.detector.calls, 0)
        self.assertTrue(report["cleanup_ok"])

    def test_a_signal_before_the_helper_is_launched_launches_nothing(self):
        original = presence.PresenceWatch.start

        def start_and_wait_for_the_signal(watch):
            refusal = original(watch)
            self.assertTrue(wait_until(lambda: watch.ending is not None))
            return refusal
        with mock.patch.object(presence.PresenceWatch, "start", start_and_wait_for_the_signal), \
                mock.patch.object(command.camera_helper, "run_export") as run_export:
            report = self.run_once("0:enter_standby_event", cues=())
        run_export.assert_not_called()
        self.assertEqual((report["status"], report["reason"], report["helper_launched"], report["exit_code"]),
                         ("presence_ended", "standby", False, 1))
        self.assertNotIn("capture", report)
        self.assertEqual((report["frames_received"], report["cleanup_ok"], report["phase"]), (0, True, "closed"))
        self.assertTrue(report["presence_watch"]["ended_run"])

    def test_a_watcher_that_dies_mid_run_ends_the_run_as_lost(self):
        report = self.run_once("500:die")
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("presence_watch_lost", "watcher_exited", 1))
        self.assertLess(self.elapsed, END_BOUND_SECONDS)
        watch = report["presence_watch"]
        self.assertEqual((watch["armed"], watch["ended_run"], watch["lost_ended_run"], watch["exit_code"]),
                         (True, False, True, 3))
        self.assertEqual(report["helper_end"]["reason"], "stop_signal")
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_watch_armed"], removal["presence_signal"], removal["run_ended_by"],
                          removal["helper_end_is_presence_stop"]), (True, None, "helper", False))
        self.assertTrue(report["cleanup_ok"])  # The watcher is gone and reaped; the helper ended cleanly.

    def test_a_watcher_that_falls_silent_mid_run_ends_the_run_as_lost(self):
        with mock.patch.object(presence, "HEARTBEAT_TIMEOUT_SECONDS", 0.5):
            report = self.run_once("300:silent")
        self.assertEqual((report["status"], report["reason"]), ("presence_watch_lost", "heartbeat_missed"))
        self.assertLess(self.elapsed, END_BOUND_SECONDS)
        self.assertEqual(report["presence_watch"]["heartbeat_timeout_ms"], 500)

    def test_a_watcher_that_never_connects_refuses_the_run_before_any_helper(self):
        cases = {"error=vrserver_not_running": "watcher_error:vrserver_not_running",
                 "activity=standby": "headset_in_standby_at_start", "camera": "ready_camera_data_read_refused"}
        for scenario, refusal in cases.items():
            with self.subTest(scenario=scenario), mock.patch.object(command.camera_helper, "run_export") as run_export:
                report = self.run_once(scenario)
                run_export.assert_not_called()
                self.assertEqual((report["status"], report["reason"], report["exit_code"], report["helper_launched"]),
                                 ("error", "presence_watch_not_armed", 1, False))
                self.assertNotIn("capture", report)
                watch = report["presence_watch"]
                self.assertEqual((watch["requested"], watch["armed"], watch["refusal"], watch["ended_run"]),
                                 (True, False, refusal, False))
                self.assertEqual((report["frames_received"], report["cleanup_ok"]), (0, True))
                self.assertEqual(self.detector.calls, 0)

    def test_a_missing_watcher_refuses_the_run_before_any_helper(self):
        with mock.patch.object(command.camera_helper, "run_export") as run_export:
            report = self.run_once(None, watcher=Path(self.directory.name) / "absent")
        run_export.assert_not_called()
        self.assertEqual((report["status"], report["reason"]), ("error", "presence_watch_not_armed"))
        self.assertEqual(report["presence_watch"]["refusal"], "watcher_not_found")

    def test_a_run_that_only_reaches_its_bound_is_not_a_presence_stop_though_the_watch_was_armed(self):
        limits = {"fps": 10, "max_seconds": 3, "max_frames": 5}
        report = self.run_once("100:proximity_off", limits=limits)
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("recognized", 0, True))
        watch = report["presence_watch"]
        self.assertEqual((watch["armed"], watch["ended_run"], watch["ending_signal"], watch["run_end_reason"]),
                         (True, False, None, None))
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_watch_armed"], removal["presence_signal"], removal["run_ended_by"],
                          removal["run_end_reason"], removal["helper_end_is_presence_stop"]),
                         (True, None, "helper", "max_frames", False))

    def test_the_helpers_own_presence_stop_still_ends_a_run_under_the_watch(self):
        report = self.run_once(None, cues=((0, "remove"),), helper_args=("--fake-scenario", "headset_removed"))
        self.assertEqual((report["status"], report["reason"]), ("helper_ended_early", "headset_removed"))
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["run_ended_by"], removal["run_end_reason"], removal["helper_end_is_presence_stop"],
                          removal["presence_watch_armed"]), ("helper", "headset_removed", True, True))
        self.assertFalse(report["presence_watch"]["ended_run"])

    def test_a_run_without_the_watcher_says_the_stop_was_not_armed(self):
        self.detector = StandInDetector()
        report = command.recognize(self.helper, {"fps": 10, "max_seconds": 3, "max_frames": 5}, (0,),
                                   detector_factory=lambda: self.detector, confirmed=True, cues=[(0, "remove")],
                                   terminal=mock.Mock())
        self.assertEqual(report["status"], "recognized")
        self.assertEqual(report["presence_watch"], presence.NOT_REQUESTED)
        self.assertEqual((report["presence_watch"]["requested"], report["presence_watch"]["armed"]), (False, False))
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_watch_armed"], removal["presence_signal"], removal["run_ended_by"]),
                         (False, None, "helper"))
        self.assertIsNone(self.watcher_pid())  # No watcher was launched.

    def test_a_cancelled_run_takes_its_watcher_with_it(self):
        self.environment(None)
        timer = threading.Timer(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        report = self.run_once(None)
        self.assertEqual(report["status"], "cancelled")
        self.assertFalse(report["presence_watch"]["ended_run"])
        self.assertIsNone(report["presence_watch"]["lost"])

    def test_the_command_passes_the_watcher_on_and_validate_refuses_it(self):
        seen = {}

        def recognize(*arguments, **options):
            seen.update(options)
            return dict(command.new_report("recognize"), status="recognized", exit_code=0)
        with mock.patch.object(command, "recognize", recognize), \
                mock.patch.object(command, "confirm_on_terminal", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()):
            code = command.main(["--allow-camera-input", "--helper", str(self.helper), "--seconds", "3", "--max-frames",
                                 "5", "--presence-watcher", str(self.watcher)])
        self.assertEqual(code, 0)
        self.assertEqual(seen["presence_watcher"], self.watcher)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            command.main(["--validate", "--presence-watcher", str(self.watcher)])


if __name__ == "__main__":
    unittest.main()
