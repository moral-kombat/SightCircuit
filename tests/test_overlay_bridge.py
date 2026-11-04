"""Exercise the real bridge protocol without claiming hardware rendering.

The tracking reporter is tested only through fake dispatch (a fake system in
``tests/fixtures/tracking_report_fixture.cpp``) and a fake bridge executable; no
test runs either real tracking mode, and none starts a VR runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from framejacker import overlay
from framejacker.hud import contract
from framejacker.overlay import (DEFAULT_BRIDGE, MAX_SIDE, PANEL_LIMITS, OverlayClient, PanelSettings, TrackingReporter,
                                 packet)
from framejacker.sources.session import TrackingObserver


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Build scripts/build_overlay_bridge.sh first")
class OverlayBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(self.temporary.name) / "absent.vrpath"))

    def run_bridge(self, *arguments, data=b""):
        result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], input=data,
                                env=self.env, capture_output=True, timeout=5)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_valid_frames_settings_and_quit_without_openvr(self):
        data = PanelSettings(visible=False).encode() + packet(1, struct.pack("<II", 2, 1) + bytes(8)) + packet(3)
        result, messages = self.run_bridge("--validate-stdin", data=data)
        self.assertEqual(result.returncode, 0, messages)
        self.assertTrue(messages[0]["validation_only"])
        final = messages[-1]
        self.assertEqual(final["status"], "validated")
        self.assertEqual(final["frames_received"], 1)
        self.assertEqual(final["settings_received"], 1)
        self.assertEqual(final["frames_submitted"], 0)
        self.assertFalse(final["runtime_initialized"])
        self.assertTrue(final["cleanup_ok"])

    def test_malformed_packets(self):
        for data in (b"short", struct.pack("<4sII", b"BAD!", 3, 0),
                     struct.pack("<4sII", b"FJO1", 1, 2**31),
                     packet(4), packet(3, b"extra"), packet(2, bytes(4)),
                     packet(1, struct.pack("<II", 0, 1)),
                     packet(1, struct.pack("<II", 513, 1) + bytes(513 * 4)),
                     packet(1, struct.pack("<II", 1, 1) + bytes(3)),
                     packet(2, struct.pack("<5fI", float("nan"), 1.5, 0, 0, 1, 1)),
                     packet(2, struct.pack("<5fI", .8, 1.5, 0, 0, 1, 2))):
            with self.subTest(data=data[:12]):
                result, messages = self.run_bridge("--validate-stdin", data=data)
                self.assertEqual(result.returncode, 1, messages)
                self.assertEqual(messages[-1]["status"], "error")
                self.assertTrue(messages[-1]["cleanup_ok"])
                self.assertFalse(messages[-1]["runtime_initialized"])

    def test_default_and_invalid_arguments_never_initialize_runtime(self):
        for args, expected in (([], 3), (["--unknown"], 64),
                               (["--test-panel", "--seconds", "0"], 64)):
            with self.subTest(args=args):
                result, messages = self.run_bridge(*args)
                self.assertEqual(result.returncode, expected)
                self.assertFalse(messages[-1]["runtime_initialized"])

    def test_help_lists_the_tracking_options(self):
        result = subprocess.run([str(DEFAULT_BRIDGE), "--help"], env=self.env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        usage = result.stdout.decode()
        self.assertIn("framejacker-overlay-bridge --stdin --report-tracking\n", usage)
        self.assertIn(f"framejacker-overlay-bridge --report-tracking-only --seconds "
                      f"{contract.TRACKING_REPORT_MIN_SECONDS}..{contract.TRACKING_REPORT_MAX_SECONDS}", usage)

    def test_tracking_options_are_refused_outside_their_modes_before_any_openvr_call(self):
        """Every misuse exits with the usage error and never reaches VR_Init.

        VR_Init reads the path registry first (``VR_PATHREG_OVERRIDE``; a missing one is
        ``VRInitError_Init_PathRegistryNotFound``, see test_missing_runtime_is_named). Here it is a
        FIFO with no writer, so an attempted read would block until the timeout fails the test, and
        HOME and XDG_CONFIG_HOME are an empty directory that must stay empty.
        """
        home = Path(self.temporary.name) / "home"
        home.mkdir()
        trap = Path(self.temporary.name) / "registry.fifo"
        os.mkfifo(trap)
        env = dict(self.env, HOME=str(home), XDG_CONFIG_HOME=str(home), VR_PATHREG_OVERRIDE=str(trap))
        marker = str(Path(self.temporary.name) / "marker.rgba")
        low, high = contract.TRACKING_REPORT_MIN_SECONDS, contract.TRACKING_REPORT_MAX_SECONDS
        flag, mode = "report_tracking_requires_stdin_mode", "tracking_report_requires_seconds"
        cases = (
            (["--validate-stdin", "--report-tracking"], flag),
            (["--test-panel", "--report-tracking"], flag),
            (["--test-panel", "--seconds", "1", "--report-tracking"], flag),
            (["--validate-calibration-marker", "--seconds", "1", "--report-tracking"], flag),
            (["--calibration-marker", "--seconds", "1", "--allow-calibration-output", "--report-tracking"], flag),
            (["--render-calibration-marker", marker, "--report-tracking"], flag),
            (["--report-tracking"], flag),
            (["--report-tracking", "--stdin"], flag),
            (["--stdin", "--report-tracking", "--report-tracking"], flag),
            (["--stdin", "--report-tracking", "--seconds", "5"], flag),
            (["--report-tracking-only", "--seconds", "5", "--report-tracking"], flag),
            (["--report-tracking-only"], mode),
            (["--report-tracking-only", "--seconds"], mode),
            (["--report-tracking-only", "--seconds", "5", "--seconds", "5"], mode),
            (["--report-tracking-only", "5"], mode),
            (["--report-tracking-only", "--seconds", str(low - 1)], "seconds_out_of_range"),
            (["--report-tracking-only", "--seconds", str(high + 1)], "seconds_out_of_range"),
            (["--report-tracking-only", "--seconds", "5x"], "seconds_out_of_range"),
            (["--report-tracking-only", "--seconds", "-5"], "seconds_out_of_range"),
            (["--report-tracking-only", "--seconds", "99999999999999999999"], "seconds_out_of_range"),
            (["--stdin", "--report-tracking-only"], "Choose --test-panel or --stdin; --help shows usage"),
            (["--validate-stdin", "--report-tracking-only", "--seconds", "5"],
             "Choose --test-panel or --stdin; --help shows usage"),
        )
        for arguments, error in cases:
            with self.subTest(arguments=arguments):
                try:
                    result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], env=env, stdin=subprocess.DEVNULL,
                                            capture_output=True, timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail(f"{arguments} blocked on the VR path registry: VR_Init was attempted")
                messages = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(messages), 1, messages)  # No ready line: refused before startup.
                self.assertEqual(result.returncode, 64)
                self.assertEqual((messages[0]["event"], messages[0]["exit_code"]), ("error", 64))
                self.assertEqual(messages[0]["error"], error)
                self.assertIs(messages[0]["runtime_initialized"], False)
                self.assertNotIn("tracking_report", messages[0])
        self.assertEqual(sorted(path.name for path in Path(self.temporary.name).iterdir()), ["home", "registry.fifo"])
        self.assertEqual(list(home.iterdir()), [])

    def test_validation_lines_are_unchanged_without_the_tracking_flag(self):
        result, messages = self.run_bridge("--validate-stdin", data=packet(3))
        self.assertEqual(result.returncode, 0, messages)
        self.assertEqual([message["event"] for message in messages], ["ready", "closed"])
        self.assertEqual(set(messages[0]), {"event", "validation_only", "frame_expiry_supported", "runtime_initialized",
                                            "overlay_interface", "report_kind", "schema_version"})
        self.assertFalse(any(key.startswith("tracking") for key in messages[-1]))

    def test_missing_runtime_is_named(self):
        result, messages = self.run_bridge("--test-panel", "--seconds", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("VRInitError_Init_PathRegistryNotFound", messages[-1]["error"])
        self.assertEqual(messages[-1]["frames_submitted"], 0)

    def test_sigterm_interrupts_wait_for_input(self):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
            process.terminate()
            process.wait(timeout=3)  # Keep stdin open to test the signal, rather than EOF.
            output, _ = process.communicate(timeout=3)
            final = json.loads(output.splitlines()[-1])
            self.assertEqual(process.returncode, 0)
            self.assertEqual(final["status"], "stopped")
            self.assertTrue(final["cleanup_ok"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_partial_payload_times_out(self):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
            process.stdin.write(struct.pack("<4sII", b"FJO1", 2, 24) + bytes(4))
            process.stdin.flush()
            process.wait(timeout=5)
            output, _ = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 1)
            self.assertIn("stdin_timeout", json.loads(output.splitlines()[-1])["error"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_client_streams_and_closes_cleanly(self):
        client = OverlayClient(validation_only=True, env=self.env).start()
        try:
            client.set_settings(PanelSettings(width=1.2, visible=False))
            client.submit_frame(2, 1, bytes([255, 0, 0, 255]) * 2)
            deadline = time.monotonic() + 3
            while client.frames_sent == 0 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertGreater(client.frames_sent, 0)
        finally:
            summary = client.close()
        self.assertIsNone(summary["error"], summary)
        self.assertEqual(summary["native_report"]["status"], "validated")
        self.assertEqual(summary["native_report"]["frames_received"], 1)
        self.assertFalse(summary["native_report"]["runtime_initialized"])
        self.assertFalse(summary["forced_termination"])

    def test_client_startup_failure_is_actionable(self):
        client = OverlayClient(env=self.env)
        with self.assertRaisesRegex(RuntimeError, "VRInitError_Init_PathRegistryNotFound"):
            client.start()
        self.assertIsNotNone(client.process.poll())


@unittest.skipUnless(os.name == "posix", "Fake bridge uses POSIX shebangs and signals")
class OverlayCloseTests(unittest.TestCase):
    BODIES = {
        "cooperative": "sys.stdin.buffer.read()\nprint(json.dumps({'exit_code': 0, 'cleanup_ok': True}), flush=True)",
        "terminable": "time.sleep(60)",
        "ignores_term": "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)",
    }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def open_fake_bridge(self, behavior):
        path = Path(self.temporary.name) / behavior
        path.write_text("#!" + sys.executable + "\n"
                        "import json, signal, sys, time\n"
                        "sys.stdout.write(json.dumps({'event': 'ready', 'validation_only': False, "
                        "'runtime_initialized': True}) + '\\n')\n"
                        "sys.stdout.flush()\n" + self.BODIES[behavior] + "\n")
        path.chmod(0o755)
        return OverlayClient(binary=path).start()

    def close_fake_bridge(self, behavior):
        client = self.open_fake_bridge(behavior)
        return client, client.close()

    def test_cooperative_exit_is_confirmed_without_forced_termination(self):
        client, summary = self.close_fake_bridge("cooperative")
        self.assertIsNone(summary["error"], summary)
        self.assertFalse(summary["forced_termination"])
        self.assertTrue(summary["cleanup_confirmed"])
        self.assertEqual(client.process.returncode, 0)

    def test_terminate_escalation_is_reported_as_forced(self):
        client, summary = self.close_fake_bridge("terminable")
        self.assertTrue(summary["forced_termination"], summary)
        self.assertFalse(summary["cleanup_confirmed"])
        self.assertIsNotNone(summary["error"])
        self.assertEqual(client.process.returncode, -signal.SIGTERM)

    def test_kill_escalation_is_reported_as_forced(self):
        client, summary = self.close_fake_bridge("ignores_term")
        self.assertTrue(summary["forced_termination"], summary)
        self.assertFalse(summary["cleanup_confirmed"])
        self.assertEqual(client.process.returncode, -signal.SIGKILL)

    def test_full_pipe_write_has_its_own_deadline(self):
        client = self.open_fake_bridge("terminable")
        try:
            started = time.monotonic()
            client.submit_frame(MAX_SIDE, MAX_SIDE, bytes(MAX_SIDE * MAX_SIDE * 4))
            self.assertTrue(client.writer is not None)
            client.writer.join(timeout=3)
            self.assertFalse(client.writer.is_alive(), "Writer stalled on a full anonymous pipe")
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(client.error, "overlay_write_timeout")
        finally:
            report = client.close()
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["transport_threads_stopped"])
        self.assertFalse(report["cleanup_confirmed"])
        self.assertFalse(report["physical_display_recovery_verified"])


def compile_fixture(name, directory):
    """Build a fake-dispatch fixture the way CI does: strict warnings and no OpenVR include path."""
    source = Path(__file__).resolve().parent / "fixtures" / name
    binary = Path(directory) / source.stem
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror", str(source), "-o", str(binary)],
                   check=True, capture_output=True, timeout=60)
    return binary


# The fixture's known zero poses (tests/fixtures/tracking_report_fixture.cpp, kRaw and kSeated), row-major.
KNOWN_RAW = ((1, 0, 0, .25), (0, 1, 0, 1.5), (0, 0, 1, -.75))
KNOWN_SEATED = ((0, 0, -1, .5), (0, 1, 0, 1.25), (1, 0, 0, -2))
# Bit patterns, not floats: 0x7f800001 and 0x7fa00000 are signaling NaNs, which CPython's float32
# struct.pack may quiet, so only "<24I" is exact for them (tools/tracking_report.hpp).
EXTREME_RAW_BITS = (0x7fc00001, 0x7f800000, 0xff800000, 0x80000000, 0x00000001, 0x7f7fffff,
                    0xff7fffff, 0x00800000, 0x3f800000, 0x7f800001, 0, 0xffffffff)
EXTREME_SEATED_BITS = (0x7fc00000, 0x7fa00000, 0, 0, 0, 0x3f800000, 0, 0, 0, 0, 0x3f800000, 0)


def zero_pose_digest(raw, seated):
    """The documented layout: raw standing then seated, row-major, each float little-endian.

    ``<24f`` over the flattened values is exact here because every value is finite.
    """
    return hashlib.sha256(struct.pack("<24f", *(value for row in (*raw, *seated) for value in row))).hexdigest()


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required for fake native dispatch")
class TrackingReportFixtureTests(unittest.TestCase):
    """tools/tracking_report.hpp driven by a fake system; its lines feed a real TrackingObserver."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temporary:
            binary = compile_fixture("tracking_report_fixture.cpp", temporary)
            cls.result = subprocess.run([str(binary)], capture_output=True, timeout=30)
        cls.cases = {}
        for line in cls.result.stdout.splitlines():
            case = json.loads(line)
            cls.cases[case.pop("case")] = case

    def events(self, name):
        return self.cases[name]["events"]

    def test_fixture_assertions_pass_and_every_case_is_printed(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr.decode()[-2000:])
        self.assertEqual(set(self.cases), {"sha256_lengths", "start", "changes", "ignored", "property_error",
                                           "property_error_and_zero", "universe_zero", "extremes", "bridge_glue"})
        # The bridge's glue from the same header, asserted inside the fixture: the option decision, the
        # reporter's start and event hook, the runtime event pass and the reporter-only loop.
        self.assertEqual(self.cases["bridge_glue"]["checks"],
                         ["options", "start_and_hook", "runtime_events", "reporter_only_loop"])

    def test_sha256_matches_hashlib_around_every_padding_boundary(self):
        text = bytes((index * 7 + 3) & 255 for index in range(300))
        digests = self.cases["sha256_lengths"]["digests"]
        self.assertEqual(len(digests), 201)
        for length, digest in enumerate(digests):
            with self.subTest(length=length):
                self.assertEqual(digest, hashlib.sha256(text[:length]).hexdigest())

    def test_zero_pose_digest_is_sha256_of_the_documented_bytes(self):
        (state,) = self.events("start")
        self.assertEqual(state["zero_pose_sha256"], zero_pose_digest(KNOWN_RAW, KNOWN_SEATED))
        # Each change moved the raw pose's y translation by one metre before the fresh state.
        changes = self.events("changes")
        for index, event in enumerate(changes[1::2], 1):
            moved = tuple(tuple(value + (index if (row, column) == (1, 3) else 0) for column, value in enumerate(line))
                          for row, line in enumerate(KNOWN_RAW))
            self.assertEqual(event["zero_pose_sha256"], zero_pose_digest(moved, KNOWN_SEATED))
        # Non-finite values, signed zeros and subnormals: their exact bits, not a normalized value.
        (extreme,) = self.events("extremes")
        self.assertEqual(extreme["zero_pose_sha256"],
                         hashlib.sha256(struct.pack("<24I", *EXTREME_RAW_BITS, *EXTREME_SEATED_BITS)).hexdigest())
        self.assertEqual(extreme["universe_id"], 2**64 - 1)

    def test_events_carry_exactly_the_observer_schema(self):
        keys = {contract.TRACKING_STATE_EVENT: {"event", contract.TRACKING_UNIVERSE_FIELD,
                                                contract.TRACKING_ZERO_POSE_FIELD},
                contract.TRACKING_CHANGED_EVENT: {"event", contract.TRACKING_CHANGE_FIELD}}
        for name in ("start", "changes", "extremes"):
            for event in self.events(name):
                with self.subTest(case=name, event=event):
                    self.assertEqual(set(event), keys[event["event"]])
        self.assertEqual([event["change"] for event in self.events("changes")[::2]],
                         [name for name, _, _ in contract.TRACKING_CHANGE_EVENTS])
        self.assertEqual(self.events("ignored"), [])
        for name, reason, error in (("property_error", "universe_id_property_error", 7),
                                    ("property_error_and_zero", "universe_id_property_error", 9),
                                    ("universe_zero", "universe_id_zero", None)):
            with self.subTest(case=name):
                expected = {"event": contract.TRACKING_UNAVAILABLE_EVENT, contract.TRACKING_REASON_FIELD: reason}
                if error is not None:
                    expected[contract.TRACKING_PROPERTY_ERROR_FIELD] = error
                change = {"event": contract.TRACKING_CHANGED_EVENT,
                          contract.TRACKING_CHANGE_FIELD: contract.TRACKING_CHANGE_EVENTS[0][0]}
                self.assertEqual(self.events(name), [expected, change, expected])

    def test_fixture_lines_drive_a_real_tracking_observer(self):
        observer = TrackingObserver()
        (state,) = self.events("start")
        observer.observe(state)
        origin = f"openvr:universe:7:zero-pose:{state['zero_pose_sha256'][:32]}"
        self.assertEqual(observer.declare().values, {"tracking_origin": origin, "recenter_epoch": 0})
        changes = self.events("changes")
        for epoch, (change, fresh) in enumerate(zip(changes[::2], changes[1::2]), 1):
            observer.observe(change)
            self.assertEqual(observer.read().values["recenter_epoch"], epoch)
            observer.observe(fresh)
            self.assertEqual(observer.read().values, {
                "tracking_origin": f"openvr:universe:7:zero-pose:{fresh['zero_pose_sha256'][:32]}",
                "recenter_epoch": epoch})
        self.assertEqual(observer.read().values["recenter_epoch"], len(contract.TRACKING_CHANGE_EVENTS))
        # The largest universe id is a state the observer accepts.
        extreme = TrackingObserver()
        extreme.observe(self.events("extremes")[0])
        self.assertEqual(extreme.read().values["recenter_epoch"], 0)
        self.assertIn(f":{2**64 - 1}:", extreme.read().values["tracking_origin"])
        # tracking_unavailable loses both tokens for good, at start or after a state.
        for name in ("property_error", "property_error_and_zero", "universe_zero"):
            for prefix in ((), (state,)):
                with self.subTest(case=name, after_state=bool(prefix)):
                    lost = TrackingObserver()
                    for event in (*prefix, *self.events(name), state):
                        lost.observe(event)
                    self.assertEqual(lost.read().values, {"tracking_origin": None, "recenter_epoch": None})
                    self.assertEqual(set(lost.read().reasons.values()), {"tracking_event_invalid"})
                    self.assertEqual(lost.declare().values, {})


class Recorder:
    """A tracking observer that records each call, in order."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def observe(self, event):
        with self.lock:
            self.calls.append(("observe", event))

    def end(self, reason):
        with self.lock:
            self.calls.append(("end", reason))

    def snapshot(self):
        with self.lock:
            return list(self.calls)

    def wait_for(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        while len(self.snapshot()) < count and time.monotonic() < deadline:
            time.sleep(.01)
        return self.snapshot()


STATE = {"event": "tracking_state", "universe_id": 7, "zero_pose_sha256": "ab" * 32}
CHANGED = {"event": "tracking_changed", "change": "seated_zero_pose_reset"}
MOVED = dict(STATE, zero_pose_sha256="cd" * 32)


@unittest.skipUnless(os.name == "posix", "Fake bridge uses POSIX shebangs and signals")
class TrackingClientTests(unittest.TestCase):
    """OverlayClient(tracking=...) and TrackingReporter against a fake bridge executable."""

    HEAD = ("import json, signal, sys, time\n"
            "def say(**message):\n"
            "    sys.stdout.write(json.dumps(message) + '\\n')\n"
            "    sys.stdout.flush()\n"
            "def closed(status='closed'):\n"
            "    say(event='closed', status=status, exit_code=0, cleanup_ok=True)\n"
            "STATE = " + repr(STATE) + "\n"
            "CHANGED = " + repr(CHANGED) + "\n"
            "MOVED = " + repr(MOVED) + "\n"
            "def wait_go():  # The test creates <this file>.go once start() has returned.\n"
            "    while not __import__('os').path.exists(sys.argv[0] + '.go'):\n"
            "        time.sleep(.01)\n"
            "validate = sys.argv[1] == '--validate-stdin'\n"
            "ready = dict(event='ready', validation_only=validate, runtime_initialized=not validate, argv=sys.argv[1:])\n"
            "if '--report-tracking' in sys.argv or sys.argv[1] == '--report-tracking-only':\n"
            "    ready['tracking_report'] = True\n")
    BODIES = {
        "waits": "say(**ready)\nsay(**STATE)\nsay(**CHANGED)\nsay(**MOVED)\nsys.stdin.buffer.read()\nclosed()",
        "exits": "say(**ready)\nsay(**STATE)\nwait_go()\nsay(**CHANGED)\nsay(**MOVED)\nclosed('lifetime_elapsed')",
        "fails": ("say(**ready)\nsay(**STATE)\nwait_go()\n"
                  "say(event='error', status='error', error='fake_failure', exit_code=1, cleanup_ok=True)\nsys.exit(1)"),
        # exit_code 0 with failed or unreported cleanup; the shipped bridge pairs cleanup_ok false with
        # exit_code 1 (tools/overlay_bridge.cpp), so these pin the client's defensive half of the check.
        "cleanup_fails": ("say(**ready)\nsay(**STATE)\nwait_go()\n"
                          "say(event='closed', status='closed', exit_code=0, cleanup_ok=False)"),
        "cleanup_unreported": "say(**ready)\nsay(**STATE)\nwait_go()\nsay(event='closed', status='closed', exit_code=0)",
        "disconnects": "say(**ready)\nsay(**STATE)\nwait_go()\nsys.exit(1)",
        "garbage": ("say(**ready)\nsay(**STATE)\nwait_go()\nprint('not json', flush=True)\nsys.stdin.buffer.read()\n"
                    "closed()"),
        "slow_state": "say(**ready)\ntime.sleep(.4)\nsay(**STATE)\nsys.stdin.buffer.read()\nclosed()",
        "silent": "say(**ready)\nsys.stdin.buffer.read()\nclosed()",
        "unavailable": ("say(**ready)\nsay(event='tracking_unavailable', reason='universe_id_zero')\n"
                        "sys.stdin.buffer.read()\nclosed()"),
        "no_flag": "ready.pop('tracking_report', None)\nsay(**ready)\nsys.stdin.buffer.read()\nclosed()",
        # A recenter between close()'s end() and the bridge reading its stop packet or EOF.
        "late": "say(**ready)\nsay(**STATE)\nsys.stdin.buffer.read()\nsay(**CHANGED)\nsay(**MOVED)\nclosed()",
        "ignores_eof": "say(**ready)\nsay(**STATE)\nsys.stdin.buffer.read()\ntime.sleep(60)",
        "ignores_term": "signal.signal(signal.SIGTERM, signal.SIG_IGN)\nsay(**ready)\nsay(**STATE)\ntime.sleep(60)",
    }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def fake(self, behavior):
        path = Path(tempfile.mkdtemp(dir=self.temporary.name)) / behavior
        path.write_text("#!" + sys.executable + "\n" + self.HEAD + self.BODIES[behavior] + "\n")
        path.chmod(0o755)
        return path

    def reporter(self, behavior, recorder, seconds=30):
        client = TrackingReporter(recorder, seconds=seconds, binary=self.fake(behavior))
        self.addCleanup(client.close)
        return client

    def overlay_client(self, behavior, **options):
        client = OverlayClient(binary=self.fake(behavior), **options)
        self.addCleanup(client.close)
        return client

    def test_without_tracking_the_command_line_and_report_are_unchanged(self):
        for validation_only, mode in ((False, "--stdin"), (True, "--validate-stdin")):
            with self.subTest(mode=mode):
                client = self.overlay_client("no_flag", validation_only=validation_only).start()
                report = client.close()
                self.assertEqual(client.process.args, [str(client.binary), mode])
                self.assertNotIn("tracking", report)
                self.assertTrue(report["cleanup_confirmed"], report)

    def test_tracking_needs_a_real_bridge_and_an_observer(self):
        with self.assertRaisesRegex(ValueError, "tracking_report_requires_real_bridge"):
            OverlayClient(validation_only=True, tracking=TrackingObserver())
        for tracking in (object(), "observer", Recorder.observe):
            with self.subTest(tracking=tracking), self.assertRaisesRegex(ValueError, "tracking_observer_invalid"):
                OverlayClient(tracking=tracking)
            with self.subTest(tracking=tracking), self.assertRaisesRegex(ValueError, "tracking_observer_invalid"):
                TrackingReporter(tracking, seconds=5)
        # A reporter-only bridge needs an observer; None is refused here, not left to time out in start().
        with self.assertRaisesRegex(ValueError, "tracking_observer_invalid"):
            TrackingReporter(None, seconds=5)

    def test_overlay_client_forwards_tracking_lines_in_order_and_ends_once_on_close(self):
        recorder, observer = Recorder(), TrackingObserver()
        client = self.overlay_client("waits", tracking=recorder).start()
        self.assertEqual(client.process.args[1:], ["--stdin", "--report-tracking"])
        self.assertEqual(recorder.wait_for(3), [("observe", event) for event in (STATE, CHANGED, MOVED)])
        for _, event in recorder.snapshot():
            observer.observe(event)
        self.assertEqual(observer.read().values["recenter_epoch"], 1)
        report = client.close()
        self.assertEqual(recorder.snapshot()[3:], [("end", "tracking_client_closed")])
        self.assertTrue(report["cleanup_confirmed"], report)
        self.assertEqual(report["tracking"], {"events_forwarded": 3, "first_event": "tracking_state",
                                              "ended": "tracking_client_closed"})
        client.close()
        self.assertEqual(len(recorder.snapshot()), 4)

    def test_a_real_observer_loses_its_tokens_when_the_client_closes(self):
        observer = TrackingObserver()
        client = self.overlay_client("waits", tracking=observer).start()
        deadline = time.monotonic() + 5
        while observer.events < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(observer.read().values["recenter_epoch"], 1)
        client.close()
        self.assertEqual(observer.read().values, {"tracking_origin": None, "recenter_epoch": None})
        self.assertEqual(observer.ended, "tracking_client_closed")

    def test_end_is_called_exactly_once_when_the_bridge_exits_fails_or_disconnects(self):
        for behavior, reason, forwarded in (("exits", "tracking_bridge_exited", 3),
                                            ("fails", "tracking_bridge_failed", 1),
                                            ("cleanup_fails", "tracking_bridge_failed", 1),
                                            ("cleanup_unreported", "tracking_bridge_failed", 1),
                                            ("disconnects", "tracking_bridge_disconnected", 1),
                                            ("garbage", "tracking_bridge_stream_invalid", 1)):
            for make in ("overlay", "reporter"):
                with self.subTest(behavior=behavior, client=make):
                    recorder = Recorder()
                    if make == "overlay":
                        client = self.overlay_client(behavior, tracking=recorder).start()
                    else:
                        client = self.reporter(behavior, recorder).start()
                    Path(str(client.binary) + ".go").touch()
                    self.assertTrue(client.done.wait(5))
                    calls = recorder.wait_for(forwarded + 1)
                    self.assertEqual(calls[-1], ("end", reason))
                    self.assertEqual([call for call, _ in calls], ["observe"] * forwarded + ["end"])
                    report = client.close()
                    self.assertEqual(recorder.snapshot(), calls)
                    self.assertEqual(report["tracking"]["ended"], reason)

    def test_lines_after_close_began_are_not_forwarded(self):
        for make in ("overlay", "reporter"):
            with self.subTest(client=make):
                recorder = Recorder()
                client = (self.overlay_client("late", tracking=recorder) if make == "overlay" else
                          self.reporter("late", recorder)).start()
                self.assertEqual(recorder.wait_for(1), [("observe", STATE)])
                report = client.close()
                # The final report followed the late lines on stdout, so the reader did read them.
                self.assertTrue(report["cleanup_confirmed"], report)
                self.assertEqual(recorder.snapshot(), [("observe", STATE), ("end", "tracking_client_closed")])
                self.assertEqual(report["tracking"], {"events_forwarded": 1, "first_event": "tracking_state",
                                                      "ended": "tracking_client_closed"})

    def test_an_observer_whose_end_fails_does_not_stall_the_client(self):
        class Failing(Recorder):
            def end(self, reason):
                super().end(reason)
                raise RuntimeError("observer broke")

        recorder = Failing()
        client = self.reporter("exits", recorder).start()
        Path(str(client.binary) + ".go").touch()
        self.assertTrue(client.done.wait(5))
        report = client.close()
        self.assertIn("Tracking observer end failed", report["error"])
        self.assertFalse(report["cleanup_confirmed"])
        self.assertEqual([call for call, _ in recorder.snapshot()], ["observe"] * 3 + ["end"])

    def test_startup_failure_ends_the_observer_once(self):
        recorder = Recorder()
        client = OverlayClient(binary=self.fake("no_flag"), tracking=recorder)
        with self.assertRaisesRegex(RuntimeError, "tracking reporter"):
            client.start()
        self.assertIsNotNone(client.process.poll())
        # The reader stopped at the ready line that lacked the reporter, before close().
        self.assertEqual(recorder.snapshot(), [("end", "tracking_bridge_stream_invalid")])

    def test_reporter_starts_the_reporter_only_mode_and_waits_for_the_first_state(self):
        observer = TrackingObserver()
        client = self.reporter("slow_state", observer, seconds=37)  # Neither bound nor the helper's default.
        started = time.monotonic()
        self.assertIs(client.start(), client)
        self.assertGreaterEqual(time.monotonic() - started, .3)
        self.assertEqual(client.process.args[1:], ["--report-tracking-only", "--seconds", "37"])
        # The first state reached the observer before start() returned, so a session declared now binds it.
        self.assertEqual(observer.declare().values["recenter_epoch"], 0)
        self.assertEqual(client.first_tracking_event, "tracking_state")
        report = client.close()
        self.assertTrue(report["cleanup_confirmed"], report)
        self.assertFalse(report["forced_termination"])
        self.assertTrue(report["stop_requested"])
        self.assertEqual(report["frames_sent"], 0)
        self.assertIsNone(client.writer)
        self.assertEqual(observer.ended, "tracking_client_closed")

    def test_reporter_returns_after_tracking_unavailable_with_the_observer_lost(self):
        observer = TrackingObserver()
        client = self.reporter("unavailable", observer).start()
        self.assertEqual(client.first_tracking_event, "tracking_unavailable")
        self.assertEqual(observer.declare().reasons,
                         dict.fromkeys(("tracking_origin", "recenter_epoch"), "tracking_event_invalid"))
        self.assertTrue(client.close()["cleanup_confirmed"])

    def test_reporter_without_a_first_tracking_line_fails_within_its_bound(self):
        recorder = Recorder()
        client = self.reporter("silent", recorder)
        with patch.object(overlay, "TRACKING_READY_TIMEOUT_SECONDS", .5):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "tracking_reporter_first_event_timeout"):
                client.start()
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNotNone(client.process.poll())
        self.assertEqual(recorder.snapshot(), [("end", "tracking_client_closed")])

    def test_reporter_refuses_a_bridge_without_the_tracking_flag(self):
        recorder = Recorder()
        client = self.reporter("no_flag", recorder)
        with self.assertRaisesRegex(RuntimeError, "tracking reporter"):
            client.start()
        self.assertIsNotNone(client.process.poll())
        self.assertEqual(recorder.snapshot(), [("end", "tracking_bridge_stream_invalid")])

    def test_reporter_close_escalation_is_reported_as_forced(self):
        for behavior, code in (("ignores_eof", -signal.SIGTERM), ("ignores_term", -signal.SIGKILL)):
            with self.subTest(behavior=behavior):
                recorder = Recorder()
                client = self.reporter(behavior, recorder).start()
                report = client.close()
                self.assertTrue(report["forced_termination"], report)
                self.assertFalse(report["cleanup_confirmed"])
                self.assertEqual(client.process.returncode, code)
                self.assertTrue(report["worker_reaped"])
                self.assertEqual(recorder.snapshot(), [("observe", STATE), ("end", "tracking_client_closed")])

    def test_reporter_lifetime_is_bounded_before_any_process_starts(self):
        low, high = contract.TRACKING_REPORT_MIN_SECONDS, contract.TRACKING_REPORT_MAX_SECONDS
        for seconds in (low - 1, high + 1, True, 5.0, "5", None):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "tracking_report_duration"):
                TrackingReporter(TrackingObserver(), seconds=seconds)
        for seconds in (low, high):  # Both bounds are accepted; no process starts.
            with self.subTest(seconds=seconds):
                self.assertEqual(TrackingReporter(TrackingObserver(), seconds=seconds).seconds, seconds)
        missing = TrackingReporter(TrackingObserver(), seconds=low, binary=Path(self.temporary.name) / "absent")
        with self.assertRaisesRegex(RuntimeError, "build_overlay_bridge"):
            missing.start()
        self.assertIsNone(missing.process)


class TrackingWiringSourceTests(unittest.TestCase):
    """The bridge reaches the reporter only through the helpers the tracking fixture covers.

    No test may run either real tracking mode, so the few OpenVR-side lines that call those helpers
    in tools/overlay_bridge.cpp are checked as source: each helper is called once, nothing else
    makes, starts or feeds a reporter, and the reporter-only mode initializes no overlay.
    """

    def setUp(self):
        import re

        source = (Path(__file__).resolve().parents[1] / "tools" / "overlay_bridge.cpp").read_text()
        self.code = re.sub(r"//[^\n]*", "", source)
        self.flat = re.sub(r"\s+", "", self.code)  # Calls are matched whatever their line breaks.

    def test_each_tested_helper_is_the_only_path(self):
        for call in ("framejacker_tracking::parse_options(argc,argv)", "constbooltracking=tracking_options.enabled();",
                     "seconds=tracking_options.seconds;",
                     "framejacker_tracking::lifetime_deadline_ns(tracking_start,seconds)",
                     "framejacker_tracking::start_if_requested(tracking_options,reporter,tracking_system,emit_tracking,"
                     "overlay.on_system_event)",
                     "framejacker_tracking::drain_runtime_events(poll,vr::VREvent_Quit,on_system_event)",
                     "framejacker_tracking::run_reporter_only(input,deadline_ns)", "PollNextEvent("):
            with self.subTest(call=call):
                self.assertEqual(self.flat.count(call), 1)
        for bypass in ("emplace(", "->start(", "->event(", "on_system_event(", "Reporter<TrackingSystem>(",
                       "any_of(", "--report-tracking\""):
            with self.subTest(bypass=bypass):
                self.assertNotIn(bypass, self.flat)

    def test_the_reporter_only_mode_initializes_no_overlay(self):
        import re

        (body,) = re.findall(r"void init_reporter\(\) \{(.*?)\n    \}", self.code, re.S)
        self.assertIn("runtime();", body)
        for overlay_call in ("VROverlay", "CreateOverlay", "api", "init("):
            with self.subTest(call=overlay_call):
                self.assertNotIn(overlay_call, body)
        self.assertEqual(re.findall(r"if \(reporter_only\) overlay\.(\w+)\(\);", self.code), ["init_reporter"])
        # The ready line and closing report measure the handle rather than assert a constant.
        self.assertNotIn('["overlay_created"]=false', self.code.replace(" ", ""))


class PanelLimitConsistencyTests(unittest.TestCase):
    def test_python_limits_match_native_bridge(self):
        import re

        tools = Path(__file__).resolve().parents[1] / "tools"
        # The bridge applies the generated header's values, never its own literals.
        header = (tools / "hud_contract.hpp").read_text()
        values = {name: float(value) for name, value in
                  re.findall(r"constexpr float (kPanel\w+) = (-?[\d.]+)f;", header)}
        source = (tools / "overlay_bridge.cpp").read_text()
        native = {name: (values[low], values[high]) for name, low, high in
                  re.findall(r"bounded\(s\.(\w+),\s*contract::(kPanel\w+Min),\s*contract::(kPanel\w+Max)\)", source)}
        native["opacity"] = native.pop("alpha")
        self.assertEqual({name: (float(low), float(high)) for name, low, high in PANEL_LIMITS}, native)
        self.assertEqual(MAX_SIDE, int(re.search(r"kMaxSide = (\d+)", header).group(1)))

    def test_defaults_are_within_limits(self):
        defaults = PanelSettings()
        for name, low, high in PANEL_LIMITS:
            self.assertTrue(low <= getattr(defaults, name) <= high, name)


class PanelSettingsTests(unittest.TestCase):
    def test_invalid_settings_are_rejected_before_sending(self):
        for settings in (PanelSettings(width=100), PanelSettings(opacity=float("nan")),
                         PanelSettings(distance=0), PanelSettings(visible="false")):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    settings.encode()


class SharedOverlayContractTests(unittest.TestCase):
    def test_commands_use_the_shared_sink_and_frozen_settings(self):
        from framejacker.overlay import OverlaySink
        from scripts import live_pipeline

        self.assertIs(live_pipeline.OverlaySink, OverlaySink)
        self.assertTrue(hasattr(OverlayClient, "freeze_settings"))

    def test_caller_owned_client_keeps_its_placement_visibility_and_close(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from framejacker.hud import HUD_CONFIG
        from framejacker.overlay import OverlaySink

        client = Mock()
        sink = OverlaySink(client)
        sink.open()
        sink.submit(SimpleNamespace(image="tile", expires_ns=None))
        sink.submit(SimpleNamespace(image="tile", expires_ns=7))
        sink.clear()
        self.assertIs(sink.close(), True)
        self.assertEqual([call[0] for call in client.method_calls], ["submit_bgr", "submit_bgr"])
        self.assertEqual(client.submit_bgr.call_args_list[1].kwargs, {"expires_ns": 7})
        with self.assertRaisesRegex(ValueError, "hud_sink_requires_owned_validation_client"):
            OverlaySink(client, hud=HUD_CONFIG)

    def test_owned_hud_sink_freezes_placement_and_accepts_only_its_tiles(self):
        from types import SimpleNamespace
        from framejacker.hud import HUD_CONFIG, HudOutput
        from framejacker.overlay import OverlaySink

        class Client:
            def __init__(self, validation_only):
                self.validation_only, self.error, self.settings, self.sent = validation_only, None, None, []
            def start(self):
                return self
            def freeze_settings(self, settings):
                self.settings = settings
            def set_settings(self, settings):
                self.settings = settings
            def submit_bgr(self, image, *, expires_ns=None):
                self.sent.append(image)

        sink = OverlaySink(hud=HUD_CONFIG, client_factory=Client)
        sink.open()
        self.assertIs(sink.client.validation_only, True)
        self.assertEqual(sink.client.settings, sink.placement(False))
        tile = SimpleNamespace(shape=(HUD_CONFIG.height_px, HUD_CONFIG.width_px, 3))
        for bad in (SimpleNamespace(hud=None, image=tile, expires_ns=1),
                    SimpleNamespace(hud=HudOutput((), HUD_CONFIG), image=SimpleNamespace(shape=(1, 1, 3)),
                                    expires_ns=1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "hud_payload_contract_mismatch"):
                sink.submit(bad)
        self.assertEqual(sink.client.sent, [])
        sink.submit(SimpleNamespace(hud=HudOutput((), HUD_CONFIG), image=tile, expires_ns=1))
        self.assertEqual((sink.client.sent, sink.client.settings.visible), ([tile], True))
        sink.clear()
        self.assertEqual(sink.client.settings, sink.placement(False))
        # A failed transport is reported before a payload mismatch, and nothing more is sent.
        sink.client.error = "Overlay bridge disconnected without a final cleanup report"
        with self.assertRaisesRegex(RuntimeError, "Overlay bridge disconnected"):
            sink.submit(SimpleNamespace(hud=None, image=tile, expires_ns=1))
        self.assertEqual((sink.client.sent, sink.client.settings.visible), ([tile], False))
        # A changed contract is reported before the transport error.
        sink.client.validation_only = False
        with self.assertRaisesRegex(ValueError, "hud_sink_mode_or_placement_changed"):
            sink.submit(SimpleNamespace(hud=None, image=tile, expires_ns=1))
        self.assertEqual(sink.client.sent, [tile])

    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required for fake native dispatch")
    def test_fake_dispatch_decodes_pixels_deadlines_and_cleanup_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = compile_fixture("overlay_dispatch_fixture.cpp", temporary)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=3)


if __name__ == "__main__":
    unittest.main()
