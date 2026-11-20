"""The guided window and the presence stop: what the ``removal`` script reads when SteamVR's signal ends a run.

The result's words and the removal entry from stand-in records (standard
library), the worker with the stand-in helper and the stand-in watcher
(``tests/fake_presence_watcher.py``), the window clicked through offscreen,
and the command line's check of the watcher's path. No SteamVR, no camera.
"""
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import guide as guidance  # noqa: E402
from sightcircuit.camera import marks, presence  # noqa: E402
from sightcircuit.commands import camera_guide as command  # noqa: E402
from sightcircuit.commands import camera_recognize  # noqa: E402
from tests import fake_presence_watcher  # noqa: E402
from tests import test_camera_guide as guide_tests  # noqa: E402

MS, SECOND, T0 = guide_tests.MS, guide_tests.SECOND, guide_tests.T0
LINUX = sys.platform.startswith("linux")
E100_LIMITS = {"late_frame_limit_ms": 7000, "removal_end_limit_ms": 10000}  # G7's numbers, as E100 was run.


def removal_report(*, status, reason, helper_end, watch=None, frames_until_ms, limits=E100_LIMITS, launched=True):
    """A finished ``removal`` run from stand-in records: near, far, the remove click at 13 s, frames at 5 a second."""
    recorded = [guide_tests.window_mark(1, "near", T0), guide_tests.window_mark(2, "far", T0 + 4000 * MS),
                guide_tests.window_mark(3, "remove", T0 + 13000 * MS)]
    frames = [guide_tests.target_frame(at_ms) for at_ms in range(0, frames_until_ms, 200)]
    report = camera_recognize.new_report("recognize")
    clock = marks.Clock(T0, 1_795_000_000 * SECOND)
    report.update(status=status, reason=reason, confirmation="operator_window", exit_code=0 if status == "recognized" else 1,
                  timebase=clock.report(), target_label="bottle", frames_processed=len(frames),
                  frames_received=len(frames), cleanup_ok=True, helper_end=helper_end,
                  marks={"recorded": recorded}, inference_ms={"p95": 70.4},
                  summary=marks.summarize(frames, recorded, origin_ns=T0, run_end_ns=T0 + 30 * SECOND,
                                          helper_end=helper_end, with_target=True, presence=watch, **limits))
    if not launched:
        report["helper_launched"] = False
    return report


def watch_summary(signal_ms=None, *, armed=True, ended_run=True, frames_after=0):
    signalled = signal_ms is not None
    return {"armed": armed, "ended_run": bool(signalled and ended_run),
            "signal": "enter_standby_event" if signalled else None, "end_reason": "standby" if signalled else None,
            "monotonic_ns": T0 + signal_ms * MS if signalled else None,
            "frames_received_after_signal": frames_after if signalled else None}


class RemovalReadingTests(unittest.TestCase):
    def test_a_run_steamvrs_signal_ended_is_read_as_a_presence_stop_with_its_times(self):
        # E100's moments with the new stop: the click at 13.0 s, SteamVR's standby 7.0 s later, the helper's end
        # 0.3 s after that with one more frame received.
        report = removal_report(status="presence_ended", reason="standby", watch=watch_summary(20000, frames_after=1),
                                helper_end={"monotonic_ns": T0 + 20300 * MS, "reason": "stop_signal"},
                                frames_until_ms=20300)
        removal = report["summary"]["removal"][0]
        self.assertEqual({name: removal[name] for name in (
            "presence_watch_armed", "presence_signal", "presence_signal_after_mark_ms",
            "helper_end_after_presence_signal_ms", "frames_received_after_presence_signal", "run_ended_by",
            "run_end_reason", "helper_end_reason", "helper_end_is_presence_stop", "helper_end_after_mark_ms",
            "helper_end_within_limit", "helper_end_after_last_frame_ms", "frames_acquired_over_limit")},
            {"presence_watch_armed": True, "presence_signal": "enter_standby_event",
             "presence_signal_after_mark_ms": 7000.0, "helper_end_after_presence_signal_ms": 300.0,
             "frames_received_after_presence_signal": 1, "run_ended_by": "presence_watch", "run_end_reason": "standby",
             "helper_end_reason": "stop_signal", "helper_end_is_presence_stop": True,
             "helper_end_after_mark_ms": 7300.0, "helper_end_within_limit": True,
             "helper_end_after_last_frame_ms": 100.0, "frames_acquired_over_limit": 1})
        lines = guidance.result_lines(report)
        self.assertEqual(lines[0], "The run ended when SteamVR reported the headset off (standby).")
        self.assertEqual(lines[-5:-1], [
            "After your click to take the headset off: the camera reader ended 7.3 s later (stop_signal).",
            "36 frames came after the click, the last 7.2 s after it.",
            "The reader ended 0.1 s after its last frame.",
            "SteamVR's headset-off signal (enter_standby_event) came 7.0 s after the click; the reader ended 0.3 s "
            "after it."])
        self.assertTrue(guidance.watch_needed(report))

    def test_a_run_that_ends_only_on_its_frame_bound_still_fails_though_the_watch_was_armed(self):
        # E100 itself: no signal reached the run, which ended max_frames 13.3 s after the click.
        report = removal_report(status="recognized", reason=None, watch=watch_summary(None),
                                helper_end={"monotonic_ns": T0 + 26272 * MS, "reason": "max_frames"},
                                frames_until_ms=26251)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["helper_end_is_presence_stop"], removal["helper_end_within_limit"],
                          removal["run_ended_by"], removal["run_end_reason"], removal["presence_signal"],
                          removal["presence_watch_armed"]), (False, False, "helper", "max_frames", None, True))
        self.assertEqual(removal["helper_end_after_mark_ms"], 13272.0)
        self.assertGreater(removal["frames_acquired_over_limit"], 0)
        lines = guidance.result_lines(report)
        self.assertEqual(lines[0], "The run finished on its bound.")
        self.assertIn("SteamVR sent no headset-off signal before the run ended.", lines)
        self.assertNotIn("headset off (", " ".join(lines))

    def test_a_signal_that_did_not_end_the_run_does_not_make_a_bound_a_presence_stop(self):
        # The signal came, but the run had already reached its own bound: the helper's end is what ended it.
        report = removal_report(status="recognized", reason=None, watch=watch_summary(26200, ended_run=False),
                                helper_end={"monotonic_ns": T0 + 26272 * MS, "reason": "max_frames"},
                                frames_until_ms=26251)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["helper_end_is_presence_stop"], removal["run_ended_by"], removal["run_end_reason"],
                          removal["presence_signal"]), (False, "helper", "max_frames", "enter_standby_event"))
        self.assertFalse(removal["helper_end_within_limit"])

    def test_a_run_without_the_watch_says_the_signal_was_not_armed(self):
        report = removal_report(status="recognized", reason=None, watch=None,
                                helper_end={"monotonic_ns": T0 + 26272 * MS, "reason": "max_frames"},
                                frames_until_ms=26251)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_watch_armed"], removal["helper_end_is_presence_stop"]), (False, False))
        self.assertIn("SteamVR's headset-off signal was not armed for this run.", guidance.result_lines(report))
        self.assertEqual(report["presence_watch"]["armed"], False)

    def test_the_helpers_own_presence_stop_is_still_read_as_one(self):
        report = removal_report(status="helper_ended_early", reason="headset_removed", watch=watch_summary(None),
                                helper_end={"monotonic_ns": T0 + 17300 * MS, "reason": "headset_removed"},
                                frames_until_ms=15400)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["helper_end_is_presence_stop"], removal["run_ended_by"], removal["run_end_reason"]),
                         (True, "helper", "headset_removed"))
        self.assertEqual(guidance.result_lines(report)[0], "The run ended when the headset came off (headset_removed).")

    def test_runs_the_watch_refused_or_ended_before_any_frame_say_so_in_plain_words(self):
        refused = removal_report(status="error", reason=presence.REASON_NOT_ARMED, watch=None, helper_end=None,
                                 frames_until_ms=0, launched=False)
        self.assertEqual(guidance.result_lines(refused), [
            "Not started: the headset-off watcher could not be armed, so the camera was not read. "
            "Tell the Frame manager.", "No camera frame was read.", "Tell the Frame manager."])
        self.assertFalse(guidance.watch_needed(refused))  # Nothing read the camera: there is nothing to watch for.
        early = removal_report(status="presence_ended", reason="standby", watch=None, helper_end=None,
                               frames_until_ms=0, launched=False)
        self.assertFalse(guidance.watch_needed(early))
        active = removal_report(status="presence_ended", reason="standby", watch=None, frames_until_ms=0,
                                helper_end={"monotonic_ns": T0 + 500 * MS, "reason": "stop_signal"})
        self.assertTrue(guidance.watch_needed(active))  # The reader was launched: watched, as a helper's own end is.
        lost = removal_report(status="presence_watch_lost", reason="watcher_exited", watch=watch_summary(None),
                              helper_end={"monotonic_ns": T0 + 15000 * MS, "reason": "stop_signal"},
                              frames_until_ms=15000)
        self.assertEqual(guidance.result_lines(lost)[0],
                         "The run was ended because the headset-off watcher stopped answering. Tell the Frame manager.")
        self.assertFalse(lost["summary"]["removal"][0]["helper_end_is_presence_stop"])

    def test_a_click_made_after_the_signal_is_worded_as_an_early_end_and_passes_nothing(self):
        # SteamVR's signal at 12.5 s, the click at 13.0 s, the helper's end at 13.5 s: inside every limit by the
        # clock, and still not a removal.
        report = removal_report(status="presence_ended", reason="standby", watch=watch_summary(12500, frames_after=3),
                                helper_end={"monotonic_ns": T0 + 13500 * MS, "reason": "stop_signal"},
                                frames_until_ms=13500)
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["presence_signal_after_mark_ms"], removal["mark_after_presence_signal"],
                          removal["helper_end_is_presence_stop"], removal["helper_end_within_limit"],
                          removal["run_ended_by"]), (-500.0, True, False, False, "presence_watch"))
        lines = guidance.result_lines(report)
        self.assertEqual(lines[0],
                         "The run ended early: SteamVR reported the headset off (standby) before the click to take it off.")
        self.assertIn("SteamVR had already reported the headset off (enter_standby_event) 0.5 s before your click "
                      "to take it off.", lines)
        self.assertFalse([text for text in lines if "-0.5" in text or "After your click" in text])

    def test_reports_the_window_writes_itself_say_the_watcher_was_named(self):
        limits = command.run_limits("removal", 20, command.MAX_FRAMES, 10)
        named = command.Config(script="removal", helper="/x/helper", report_path=Path("/x/r.json"), limits=limits,
                               presence_watcher="/x/watcher")
        plain = command.Config(script="removal", helper="/x/helper", report_path=Path("/x/r.json"), limits=limits)
        unstarted = command.unstarted_report(named, guidance.DECLINED)["presence_watch"]
        self.assertEqual((unstarted["requested"], unstarted["armed"], unstarted["cleanup_ok"]), (True, False, True))
        interrupted = command.interrupted_report(named, "guide_worker_died")
        self.assertEqual((interrupted["presence_watch"]["requested"], interrupted["presence_watch"]["armed"],
                          interrupted["presence_watch"]["cleanup_ok"], interrupted["cleanup_ok"]),
                         (True, None, False, False))
        for report in (command.unstarted_report(plain, guidance.DECLINED), command.interrupted_report(plain, "x")):
            self.assertEqual(report["presence_watch"], presence.NOT_REQUESTED)
        # Arming precedes the helper's launch, so the moment a dead worker's helper is surely gone moves with it.
        self.assertEqual(command.arming_seconds(plain), 0.0)
        self.assertGreaterEqual(command.arming_seconds(named), presence.READY_WAIT_SECONDS)

    def test_a_presence_end_before_the_click_is_not_called_a_removal(self):
        report = camera_recognize.new_report("recognize")
        report.update(status="presence_ended", reason="standby", confirmation="operator_window", cleanup_ok=True,
                      frames_received=3, frames_processed=3, summary={"removal": []})
        self.assertEqual(guidance.status_line(report),
                         "The run ended early: SteamVR reported the headset off (standby) before the click to take it off.")


@unittest.skipUnless(LINUX and guide_tests.NUMPY_CV2, "Needs Linux, NumPy and OpenCV")
class WorkerPresenceTests(guide_tests.StandInHelper):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.watcher = fake_presence_watcher.install(cls.directory.name)

    def worker(self, config, scenario):
        events = guide_tests.Events()
        worker = command.Worker(config.spec(), events.put, env={**os.environ, "FAKE_PRESENCE_SCENARIO": scenario})
        worker.start()
        self.addCleanup(worker.close)
        return worker, events

    def test_the_spec_carries_the_watcher_and_the_worker_ends_the_run_on_steamvrs_signal(self):
        config = self.config("removal", seconds=20, presence_watcher=str(self.watcher))
        self.assertEqual(config.spec()["presence_watcher"], str(self.watcher))
        worker, events = self.worker(config, "900:enter_standby_event")
        events.wait("ready")
        for label in ("near", "far", "remove"):
            worker.mark(label, time.monotonic_ns())
            time.sleep(0.05)
        started = time.monotonic()
        report = events.wait("report")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual((report["status"], report["reason"], report["cleanup_ok"], report["confirmation"]),
                         ("presence_ended", "standby", True, "operator_window"))
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["source"], removal["run_ended_by"], removal["run_end_reason"],
                          removal["helper_end_is_presence_stop"], removal["helper_end_reason"]),
                         ("window", "presence_watch", "standby", True, "stop_signal"))
        self.assertGreater(removal["presence_signal_after_mark_ms"], 0)
        self.assertTrue(report["presence_watch"]["armed"])
        self.assertEqual(guidance.result_lines(report)[0], "The run ended when SteamVR reported the headset off (standby).")
        self.assertTrue(guidance.watch_needed(report))

    def test_stop_while_the_watch_arms_is_honoured_at_once_and_launches_nothing(self):
        config = self.config("removal", seconds=20, presence_watcher=str(self.watcher))
        worker, events = self.worker(config, "noready")
        time.sleep(0.8)  # The detector is loaded and the watch is waiting for a ready that will not come.
        started = time.monotonic()
        worker.stop()
        report = events.wait("report")
        self.assertLess(time.monotonic() - started, 4.0)  # Not the twelve seconds the watch would wait.
        self.assertEqual((report["status"], report["reason"], report["cleanup_ok"]),
                         ("stopped", "stopped_before_launch", True))
        self.assertNotIn("capture", report)
        self.assertEqual([item[0] for item in events.items], ["report"])
        self.assertEqual((report["presence_watch"]["requested"], report["presence_watch"]["armed"]), (True, False))

    def test_a_worker_cancelled_while_the_watch_arms_reports_the_watcher_as_named(self):
        import signal
        config = self.config("removal", seconds=20, presence_watcher=str(self.watcher))
        worker, events = self.worker(config, "noready")
        time.sleep(0.8)
        worker.process.send_signal(signal.SIGTERM)
        report = events.wait("report")
        self.assertEqual(report["status"], "cancelled")
        watch = report["presence_watch"]
        self.assertEqual((watch["requested"], watch["armed"], watch["cleanup_ok"], report["cleanup_ok"]),
                         (True, False, True, True))
        self.assertIsNotNone(watch["launched"])

    def test_a_watcher_that_cannot_be_armed_starts_no_reader(self):
        config = self.config("removal", seconds=20, presence_watcher=str(self.watcher))
        worker, events = self.worker(config, "error=vrserver_not_running")
        report = events.wait("report")
        self.assertEqual((report["status"], report["reason"], report["frames_received"], report["cleanup_ok"]),
                         ("error", "presence_watch_not_armed", 0, True))
        self.assertNotIn("capture", report)
        self.assertEqual([item[0] for item in events.items], ["report"])  # No ready: the helper was never launched.
        self.assertFalse(guidance.watch_needed(report))

    def test_a_run_without_the_option_is_what_it_was_and_says_not_armed(self):
        config = self.config("positions", seconds=2)
        self.assertIsNone(config.spec()["presence_watcher"])
        worker, events = self.worker(config, "0:enter_standby_event")
        report = events.wait("report")
        self.assertEqual((report["status"], report["presence_watch"]["requested"], report["presence_watch"]["armed"]),
                         ("recognized", False, False))

    def test_the_worker_refuses_a_watcher_that_is_not_a_path(self):
        config = self.config("removal", seconds=20)
        spec = dict(config.spec(), presence_watcher=["sh", "-c", "true"])
        events = guide_tests.Events()
        worker = command.Worker(spec, events.put)
        worker.start()
        self.addCleanup(worker.close)
        report = events.wait("report")
        self.assertEqual((report["status"], report["reason"]), ("error", "guide_worker_arguments_invalid"))


@unittest.skipUnless(guide_tests.PYSIDE and guide_tests.NUMPY_CV2 and LINUX, "Needs Linux, PySide6, NumPy and OpenCV")
class FlowPresenceTests(guide_tests.StandInHelper):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.watcher = fake_presence_watcher.install(cls.directory.name)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_the_removal_script_ends_by_itself_on_steamvrs_signal_after_the_click(self):
        config = self.config("removal", seconds=20, presence_watcher=str(self.watcher),
                             worker_env={**os.environ, "FAKE_PRESENCE_SCENARIO": "4000:enter_standby_event"})
        flow = command.Flow(config)
        self.addCleanup(flow.window.close)
        self.assertTrue(flow.start())
        self.assertTrue(flow.click_when_offered(guidance.REMOVAL_STEPS[-1].button))
        self.assertTrue(flow.finish_after_run(headset=command.TEXT_BACK_ON))
        report = json.loads(config.report_path.read_text())
        self.assertEqual((report["status"], report["reason"], report["guide"]["ended_by"], report["guide"]["watch"]),
                         ("presence_ended", "standby", "helper", "held"))
        self.assertEqual([entry["label"] for entry in report["marks"]["recorded"]], ["near", "far", "remove"])
        removal = report["summary"]["removal"][0]
        self.assertEqual((removal["run_ended_by"], removal["helper_end_is_presence_stop"], removal["presence_watch_armed"]),
                         ("presence_watch", True, True))
        self.assertEqual(report["guide"]["observations"]["headset"], "removed_and_back")
        self.assertEqual(flow.window.result_label.text().splitlines(), guidance.result_lines(report))
        self.assertIn("SteamVR's headset-off signal (enter_standby_event) came", flow.window.result_label.text())
        self.assertLessEqual(flow.run_screen_texts(), guidance.static_texts("removal"))  # Nothing new on the run screen.
        self.assertTrue(report["cleanup_ok"])


class CommandLinePresenceTests(unittest.TestCase):
    def setUp(self):
        base = guide_tests.CommandLineTests
        base.setUp(self)
        self.arguments = lambda **changes: base.arguments(self, **changes)
        self.watcher = fake_presence_watcher.install(self.directory.name)

    def test_a_watcher_that_cannot_be_the_watcher_is_refused_before_any_window(self):
        for watcher, reason in ((Path(self.directory.name) / "absent", "presence_watcher_not_found"),
                                (ROOT / "scripts/camera_guide.py", "presence_watcher_inside_work_tree")):
            code, report, _ = guide_tests.run_main([*self.arguments(), "--presence-watcher", str(watcher)])
            self.assertEqual((code, report["reason"], report["confirmation"]), (1, reason, None))
            self.assertNotIn("helper", report["error"].replace("presence watcher", ""))
        self.assertFalse(self.report.exists())
        self.run_window.assert_not_called()

    def test_an_accepted_watcher_reaches_the_window_resolved_and_validate_refuses_it(self):
        seen = {}

        def opened(config, windowed=False):
            seen.update(config=config)
            return command.unstarted_report(config, guidance.DECLINED)
        self.run_window.side_effect = opened
        import types
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), \
                mock.patch.dict(sys.modules, {"PySide6": sys.modules.get("PySide6") or types.ModuleType("PySide6")}):
            guide_tests.run_main([*self.arguments(), "--presence-watcher", str(self.watcher)])
            self.assertEqual(seen["config"].presence_watcher, os.path.realpath(self.watcher))
            guide_tests.run_main(self.arguments())
            self.assertIsNone(seen["config"].presence_watcher)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            command.main(["--validate", "--presence-watcher", str(self.watcher)])


if __name__ == "__main__":
    unittest.main()
