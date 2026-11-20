"""``sightcircuit-presence-watcher`` executed for real: offline, and against the fake OpenVR runtime.

``tests/fixtures/fake_vrclient.cpp`` is the ``vrclient.so`` the overlay
bridge's tests use; here it scripts runtime events and the headset's activity
level, and logs what the watcher asks the runtime for. These tests show what
the watcher calls and writes. They do not show what SteamVR on a Frame sends
when a headset comes off, and no time here is a Frame measurement.
"""
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import marks, presence  # noqa: E402
from tests import fake_vrclient  # noqa: E402

WATCHER = ROOT / "artifacts/presence-watcher/sightcircuit-presence-watcher"
SOURCE = ROOT / "tools/presence_watcher.cpp"
HEADER = ROOT / "tools/presence_signals.hpp"
# The pinned header's numbers (openvr.h): event types, the proximity sensor's button, activity levels.
ENTER_STANDBY, LEAVE_STANDBY, INTERACTION_STARTED, INTERACTION_ENDED = 106, 107, 103, 104
BUTTON_PRESS, BUTTON_UNPRESS, QUIT, PROXIMITY, TRIGGER = 200, 201, 700, 31, 33
APPLICATION_BACKGROUND = 3
BUILT = WATCHER.is_file()


def run(*arguments, env=None, timeout=20):
    done = subprocess.run([str(WATCHER), *arguments], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                          env=env)
    return done.returncode, [json.loads(line) for line in done.stdout.splitlines()]


class SourceTests(unittest.TestCase):
    """What the watcher's source may name: presence only. Standard library; needs no build."""

    def test_the_source_names_no_camera_overlay_or_settings_interface(self):
        for path in (SOURCE, HEADER):
            code = "\n".join(line.split("//", 1)[0] for line in path.read_text().splitlines())
            for forbidden in ("TrackedCamera", "IVROverlay", "VROverlay(", "IVRSettings", "VRSettings(", "IVRCompositor",
                              "VRCompositor(", "IVRScreenshots", "IVRInput", "SetOverlay", "CreateOverlay",
                              "VRApplication_Scene", "VRApplication_Overlay", "VRApplication_Other"):
                with self.subTest(path=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, code)
        code = SOURCE.read_text()
        self.assertEqual(code.count("vr::VR_Init("), 1)
        self.assertIn("vr::VR_Init(&error, vr::VRApplication_Background)", code)

    def test_every_api_name_the_watcher_uses_is_in_the_pinned_header(self):
        openvr = ROOT / ".cache/openvr/headers/openvr.h"
        if not openvr.is_file():
            self.skipTest("Needs the pinned OpenVR checkout (any OpenVR build script fetches it)")
        declared = openvr.read_text()
        for name in ("VREvent_EnterStandbyMode", "VREvent_LeaveStandbyMode", "VREvent_TrackedDeviceUserInteractionStarted",
                     "VREvent_TrackedDeviceUserInteractionEnded", "VREvent_ButtonPress", "VREvent_ButtonUnpress",
                     "k_EButton_ProximitySensor", "GetTrackedDeviceActivityLevel", "k_EDeviceActivityLevel_Standby",
                     "Prop_ContainsProximitySensor_Bool", "VRApplication_Background", "AcknowledgeQuit_Exiting"):
            with self.subTest(name=name):
                self.assertIn(name, declared)
                self.assertIn(name, SOURCE.read_text() + HEADER.read_text())
        for name, value in (("VREvent_EnterStandbyMode", ENTER_STANDBY), ("VREvent_LeaveStandbyMode", LEAVE_STANDBY),
                            ("VREvent_ButtonUnpress", BUTTON_UNPRESS), ("k_EButton_ProximitySensor", PROXIMITY),
                            ("VRApplication_Background", APPLICATION_BACKGROUND)):
            self.assertRegex(declared, rf"{name}\s*=\s*{value}\b")

    def test_the_build_script_builds_this_one_unit_under_werror(self):
        build = (ROOT / "scripts/build_openvr_tool.sh").read_text()
        case = build.split("presence-watcher)", 1)[1].split(";;", 1)[0]
        self.assertIn("presence_watcher.cpp", case)
        self.assertIn("sightcircuit_strict=1", case)
        self.assertIn("-Werror", build)
        self.assertIn("build_openvr_tool.sh\" presence-watcher", (ROOT / "scripts/build_presence_watcher.sh").read_text())


@unittest.skipUnless(BUILT, "Build scripts/build_presence_watcher.sh first")
class OfflineTests(unittest.TestCase):
    def test_describe_states_the_table_python_holds_and_what_the_tool_does_not_do(self):
        code, lines = run("--describe")
        self.assertEqual((code, len(lines)), (0, 1))
        described = lines[0]
        self.assertEqual(described["signals"], presence.signal_table())
        for name, expected in presence.READY_FACTS.items():
            if name != "hmd_connected":
                self.assertEqual(described[name], expected, name)
        self.assertEqual(len(described["interfaces_requested"]), 1)
        self.assertTrue(described["interfaces_requested"][0].startswith("IVRSystem_"))
        self.assertEqual(described["seconds_range"], [1, presence.MAX_WATCH_SECONDS])
        self.assertLess(described["heartbeat_ms"] * 2, presence.HEARTBEAT_TIMEOUT_SECONDS * 1000)

    def test_bad_arguments_are_refused_with_one_line(self):
        for arguments in ((), ("--watch",), ("--watch", "--seconds", "0"), ("--watch", "--seconds", "121"),
                          ("--watch", "--seconds", "5x"), ("--watch", "--seconds", "5", "--extra"), ("--stdin",),
                          ("--describe", "more")):
            with self.subTest(arguments=arguments):
                code, lines = run(*arguments)
                self.assertEqual((code, [entry["event"] for entry in lines], lines[0]["reason"]), (64, ["error"], "usage"))

    def test_without_a_runtime_the_watcher_reports_it_and_starts_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "HOME": directory, "XDG_CONFIG_HOME": directory,
                   "VR_PATHREG_OVERRIDE": str(Path(directory) / "absent.vrpath")}
            code, lines = run("--watch", "--seconds", "2", env=env)
        self.assertEqual((code, [entry["event"] for entry in lines]), (1, ["error"]))
        self.assertEqual(lines[0]["reason"], "vrserver_not_running")
        self.assertIn(lines[0]["reason"], presence.WATCHER_ERRORS)
        self.assertTrue(lines[0]["init_error"].startswith("VRInitError_"))


@unittest.skipUnless(BUILT and fake_vrclient.available(),
                     "Needs scripts/build_presence_watcher.sh, its SDK checkout and g++")
class FakeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.runtime_env, _ = fake_vrclient.runtime(cls.directory.name)
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def env(self, **fake):
        type(self).count += 1
        self.log = Path(self.directory.name) / f"watcher-calls-{self.count}.jsonl"
        return {**os.environ, **self.runtime_env, "FAKE_VR_LOG": str(self.log), "FAKE_VR_LOG_SYSTEM": "1", **fake}

    def calls(self):
        return fake_vrclient.calls(self.log)

    def check_lines(self, lines):
        """Every line passes the Python side's own rules, in order."""
        ready = False
        for sequence, entry in enumerate(lines):
            event = presence.check_line(entry, sequence=sequence, ready_seen=ready, launched_ns=0,
                                        received_ns=time.monotonic_ns())
            ready = ready or event == "ready"

    def test_a_bounded_watch_reads_presence_and_touches_nothing_else(self):
        events = (f"200:{BUTTON_UNPRESS}:0:{PROXIMITY},300:{INTERACTION_ENDED}:0,400:{ENTER_STANDBY},"
                  f"600:{LEAVE_STANDBY},700:{INTERACTION_STARTED}:0,800:{BUTTON_PRESS}:0:{PROXIMITY}")
        started = time.monotonic_ns()
        code, lines = run("--watch", "--seconds", "1", env=self.env(FAKE_VR_EVENTS=events, FAKE_VR_ACTIVITY="350:3,650:1"))
        ended = time.monotonic_ns()
        self.assertEqual(code, 0)
        self.check_lines(lines)
        self.assertEqual((lines[0]["event"], lines[-1]["event"], lines[-1]["reason"]), ("ready", "end", "max_seconds"))
        ready = lines[0]
        self.assertEqual((ready["hmd_activity"], ready["hmd_has_proximity_sensor"], ready["max_seconds"]),
                         ("user_interaction", True, 1))
        seen = [(entry["signal"], entry["ends_run"], entry["end_reason"], entry["hmd_activity"])
                for entry in lines if entry["event"] == "presence"]
        self.assertEqual(seen, [("proximity_off", False, None, "user_interaction"),
                                ("user_interaction_ended", False, None, "user_interaction"),
                                ("hmd_activity_changed", False, None, "standby"),
                                ("enter_standby_event", True, "standby", "standby"),
                                ("leave_standby_event", False, None, "standby"),
                                ("hmd_activity_changed", False, None, "user_interaction"),
                                ("user_interaction_started", False, None, "user_interaction"),
                                ("proximity_on", False, None, "user_interaction")])
        self.assertGreaterEqual(sum(1 for entry in lines if entry["event"] == "heartbeat"), 1)
        times = [entry["monotonic_ns"] for entry in lines]
        self.assertEqual(times, sorted(times))
        self.assertTrue(started < times[0] and times[-1] < ended)
        standby = next(entry for entry in lines if entry.get("signal") == "enter_standby_event")
        self.assertLess(abs((standby["monotonic_ns"] - ready["monotonic_ns"]) / 1e6 - 400), 150)  # Polled every 20 ms.
        self.assertEqual(standby["event_age_ms"], 4)
        self.assertLess(abs(standby["realtime_ns"] - time.time_ns()), 60 * 10**9)
        self.assertEqual((lines[-1]["presence_lines"], lines[-1]["events_polled"], lines[-1]["shutdown_returned"]),
                         (8, 6, True))
        # What the runtime was asked for: one Background connect, IVRSystem alone, and a shutdown. No overlay call.
        calls = self.calls()
        self.assertEqual([call["call"] for call in calls], ["Init", "GetGenericInterface", "Cleanup"])
        self.assertEqual(calls[0]["application_type"], APPLICATION_BACKGROUND)
        self.assertTrue(calls[1]["name"].startswith("IVRSystem_"))

    def test_events_of_other_devices_and_other_buttons_are_not_presence(self):
        events = (f"100:{BUTTON_UNPRESS}:1:{PROXIMITY},150:{BUTTON_UNPRESS}:0:{TRIGGER},200:{INTERACTION_ENDED}:2,"
                  f"250:{INTERACTION_STARTED}:1,300:{BUTTON_PRESS}:3:{PROXIMITY}")
        code, lines = run("--watch", "--seconds", "1", env=self.env(FAKE_VR_EVENTS=events))
        self.assertEqual(code, 0)
        self.assertEqual([entry for entry in lines if entry["event"] == "presence"], [])
        self.assertEqual((lines[-1]["events_polled"], lines[-1]["presence_lines"]), (5, 0))

    def start(self, seconds="30", **fake):
        process = subprocess.Popen([str(WATCHER), "--watch", "--seconds", seconds], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env(**fake))
        self.addCleanup(process.stdout.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def test_a_stop_signal_ends_the_watch_cleanly(self):
        for number in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=number):
                process = self.start()
                self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
                sent = time.monotonic()
                process.send_signal(number)
                rest = [json.loads(entry) for entry in process.stdout.read().splitlines()]
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertLess(time.monotonic() - sent, 1.0)
                self.assertEqual((rest[-1]["event"], rest[-1]["reason"]), ("end", "stop_signal"))
                self.assertEqual(self.calls()[-1]["call"], "Cleanup")

    def test_the_runtimes_quit_is_acknowledged_and_ends_the_watch(self):
        code, lines = run("--watch", "--seconds", "30", env=self.env(FAKE_VR_EVENTS=f"200:{QUIT}"))
        self.assertEqual((code, lines[-1]["reason"]), (0, "runtime_quit"))
        self.check_lines(lines)
        self.assertEqual([call["call"] for call in self.calls()][-2:], ["AcknowledgeQuit_Exiting", "Cleanup"])

    def test_no_server_and_no_headset_are_errors_before_ready(self):
        code, lines = run("--watch", "--seconds", "5", env=self.env(FAKE_VR_INIT_FAIL="1"))
        self.assertEqual((code, [entry["event"] for entry in lines]), (1, ["error"]))
        self.assertEqual((lines[0]["reason"], lines[0]["init_error"]),
                         ("vrserver_not_running", "VRInitError_Init_NoServerForBackgroundApp"))
        code, lines = run("--watch", "--seconds", "5", env=self.env(FAKE_VR_HMD_DISCONNECTED="1"))
        self.assertEqual((code, [entry["event"] for entry in lines]), (1, ["error", "end"]))
        self.assertEqual((lines[0]["reason"], lines[1]["reason"]), ("headset_not_connected", "headset_not_connected"))
        self.check_lines(lines)
        self.assertEqual(self.calls()[-1]["call"], "Cleanup")

    def test_a_blocked_runtime_call_ends_the_watcher_instead_of_leaving_it_silent(self):
        process = self.start(FAKE_VR_POLL_BLOCK_MS="200:60000")
        self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
        started = time.monotonic()
        self.assertEqual(process.wait(timeout=10), -signal.SIGALRM)
        self.assertLess(time.monotonic() - started, 7.0)

    def test_the_watcher_ends_with_its_parent(self):
        # A shell that starts the watcher in the background and is then killed: the watcher must not outlive it.
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pid"
            parent = subprocess.Popen(["/bin/sh", "-c", f'"{WATCHER}" --watch --seconds 60 >/dev/null 2>&1 & '
                                       f'echo $! > "{pid_file}"; exec sleep 60'], env=self.env())
            self.addCleanup(lambda: parent.poll() is None and parent.kill())
            end = time.monotonic() + 5
            while not (pid_file.is_file() and pid_file.read_text().strip()) and time.monotonic() < end:
                time.sleep(0.02)
            pid = int(pid_file.read_text())
            time.sleep(0.3)
            self.assertTrue(Path(f"/proc/{pid}").exists())
            parent.kill()
            parent.wait(timeout=5)
            end = time.monotonic() + 5
            while Path(f"/proc/{pid}").exists() and time.monotonic() < end:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_python_arms_on_the_real_watcher_and_its_standby_line_sets_the_stop(self):
        stop = threading.Event()
        env = self.env(FAKE_VR_EVENTS=f"300:{BUTTON_UNPRESS}:0:{PROXIMITY},400:{ENTER_STANDBY},500:{LEAVE_STANDBY}")
        previous = {name: os.environ.get(name) for name in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
                                 for name, value in previous.items()])
        watch = presence.PresenceWatch(WATCHER, 5, stop)
        self.addCleanup(watch.close)
        self.assertIsNone(watch.start())
        self.assertTrue(stop.wait(5.0))
        self.assertLess((time.monotonic_ns() - watch.ending["monotonic_ns"]) / 1e6, 200)
        end = time.monotonic() + 3
        while watch.event_count < 3 and time.monotonic() < end:
            time.sleep(0.01)
        watch.run_ended()
        watch.close()
        report = watch.report(marks.Clock())
        self.assertEqual([event["signal"] for event in report["events"]],
                         ["proximity_off", "enter_standby_event", "leave_standby_event"])
        self.assertEqual((report["armed"], report["ended_run"], report["run_end_reason"], report["lost"]),
                         (True, True, "standby", None))
        self.assertEqual((report["end"]["reason"], report["exit_code"], report["cleanup_ok"]), ("stop_signal", 0, True))
        self.assertEqual(report["ready"]["interface"], self.calls()[1]["name"])

    def test_python_refuses_the_real_watcher_when_the_headset_is_already_in_standby(self):
        env = self.env(FAKE_VR_ACTIVITY="0:3")
        previous = {name: os.environ.get(name) for name in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
                                 for name, value in previous.items()])
        watch = presence.PresenceWatch(WATCHER, 5, threading.Event())
        self.addCleanup(watch.close)
        self.assertEqual(watch.start(), "headset_in_standby_at_start")
        watch.close()
        self.assertTrue(watch.cleanup_ok)


if __name__ == "__main__":
    unittest.main()
