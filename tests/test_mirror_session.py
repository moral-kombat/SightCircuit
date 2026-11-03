"""Session tokens of the mirror source, from a fake /proc root and injected runtime events only.

No test reads the real /proc of any process, opens a device or talks to a VR
runtime: every provider gets a temporary directory laid out like /proc, and the
tracking observer gets hand-written bridge-shaped events.
"""
from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from framejacker import gates
from framejacker.context import SOURCE_CHANGED, CaptureContext
from framejacker.frame import DetectionResult
from framejacker.hud import CaptureFormat, Footprint, HudMask
from framejacker.hud.contract import SESSION_TOKENS, token_valid
from framejacker.runtime import live, synthetic
from framejacker.sources import mirror as acquisition
from framejacker.sources import session as tokens
from framejacker.sources.mirror import MirrorSource

BOOT_A = "0f0e0d0c-0b0a-4908-8706-050403020100"
BOOT_B = "11111111-2222-4333-8444-555555555555"
TIME_NAMESPACE = 4026531834
INITIAL_OFFSETS = "monotonic           0         0\nboottime            0         0\n"  # As the kernel prints them.
PID, START = 4242, 987654
SOURCE_ID = "synthetic:mirror-session"
FORMAT = {"width": 96, "height": 64, "fourcc": "BGR3", "stride_bytes": 288, "buffer_bytes": 18432,
          "minimum_buffer_bytes": 18432}
FOOTPRINTS = (Footprint(0, (67, 42, 88, 56), 8),)
STATE = {"event": "tracking_state", "universe_id": 1700000000123, "zero_pose_sha256": "ab" * 32}
RECENTER = {"event": "tracking_changed", "change": "standing_zero_pose_reset"}
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))


def stat_line(pid, comm, start, state="S"):
    """A /proc/<pid>/stat line whose field 3 (state) is ``state`` and field 22 (starttime) is ``start``."""
    fields = [state, "1", str(pid), str(pid), "0", "-1", "4194560", "100", "0", "0", "0", "5", "3", "0", "0",
              "20", "0", "12", "0", str(start), "123456789", "1234"]
    return f"{pid} ({comm}) {' '.join(fields)}\n"


class FakeProc:
    """A temporary directory laid out like /proc: boot id, this process's time namespace, processes."""

    def __init__(self, directory, *, boot=BOOT_A):
        self.base = Path(directory)
        self.root = self.base / "proc"
        (self.root / "self/ns").mkdir(parents=True)
        for name in ("time", "time_for_children"):
            os.symlink(f"time:[{TIME_NAMESPACE}]", self.root / "self/ns" / name)
        self.set_offsets(INITIAL_OFFSETS)
        self.set_boot(boot)
        self.binary = self.executable("steamvr/bin/linux64/vrserver", b"running vrserver build 1")
        self.add_process(PID, "vrserver", START, self.binary)

    def set_boot(self, value):
        path = self.root / "sys/kernel/random/boot_id"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n")

    def set_offsets(self, text):
        (self.root / "self/timens_offsets").write_text(text)

    def set_namespace(self, name, inode):
        link = self.root / "self/ns" / name
        link.unlink(missing_ok=True)
        os.symlink(f"time:[{inode}]", link)

    def executable(self, relative, content):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def add_process(self, pid, comm, start, exe, state="S"):
        directory = self.root / str(pid)
        directory.mkdir()
        (directory / "comm").write_text(comm + "\n")
        (directory / "stat").write_text(stat_line(pid, comm, start, state))
        os.symlink(exe, directory / "exe")

    def set_process(self, pid, comm, start, state="S", exe=None):
        """Rewrite a process's comm and stat (an exec, or an exit to a zombie); ``exe`` re-points its link."""
        directory = self.root / str(pid)
        (directory / "comm").write_text(comm + "\n")
        (directory / "stat").write_text(stat_line(pid, comm, start, state))
        if exe is not None:
            (directory / "exe").unlink()
            os.symlink(exe, directory / "exe")

    def remove_process(self, pid):
        shutil.rmtree(self.root / str(pid))


class Identity:
    """What a fixture backend declares besides session tokens."""
    kind, source_id, capture_layout = "synthetic", SOURCE_ID, "mono"


class ProcTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.proc = FakeProc(directory.name)

    def observer(self, *events):
        observer = tokens.TrackingObserver()
        for event in events:
            observer.observe(event)
        return observer

    def session(self, *, clock=True, runtime=True, observer=None):
        providers = []
        if clock:
            providers.append(tokens.HostClock(self.proc.root))
        if runtime:
            providers.append(tokens.RuntimeProcess(self.proc.root))
        if observer is not None:
            providers.append(observer)
        return tokens.MirrorSession(*providers)

    def source(self, session, identity=Identity):
        return MirrorSource(None, SOURCE_ID, identity=identity, session=session)

    def declared(self, source):
        return {name: getattr(source, name) for name in SESSION_TOKENS}

    def mask(self, session_tokens):
        calibration = synthetic.fixture_calibration(SOURCE_ID, CaptureFormat.from_mapping(FORMAT), "mono", FOOTPRINTS,
                                                    session=session_tokens)
        return HudMask(calibration, source_kind="synthetic")

    def read(self, source, *steps, admit=None):
        """Run ``source`` once: before each read its step runs, then one frame is emitted (and admitted).

        Returns ``[(frame, reason)]``, where reason is the admission failure, or None.
        """
        results = []

        def emit(frame):
            reason = None
            if admit is not None:
                try:
                    admit(frame)
                except ValueError as error:
                    reason = str(error)
            results.append((frame, reason))

        def acquire(stop, on_frame, progress):
            for number, step in enumerate(steps, 1):
                step()
                on_frame(bytes(FORMAT["buffer_bytes"]), FORMAT, number, time.monotonic_ns())
            return {"status": "captured", "cleanup_ok": True}

        source.acquire = acquire
        source.run(emit, threading.Event())
        return results


def nothing():
    return None


class HostClockTests(ProcTestCase):
    def test_timebase_names_the_monotonic_clock_of_this_boot_and_time_namespace(self):
        reading = tokens.HostClock(self.proc.root).declare()
        self.assertEqual(reading.reasons, {})
        timebase = reading.values["timebase_id"]
        self.assertTrue(token_valid(timebase))
        self.assertIn("monotonic", timebase)
        self.assertIn(BOOT_A, timebase)
        self.assertIn(str(TIME_NAMESPACE), timebase)

    def test_changed_boot_id_changes_timebase_id(self):
        clock = tokens.HostClock(self.proc.root)
        first = clock.declare().values["timebase_id"]
        self.proc.set_boot(BOOT_B)
        second = tokens.HostClock(self.proc.root).declare().values["timebase_id"]
        self.assertNotEqual(first, second)
        self.assertIn(BOOT_B, second)
        # The per-frame read sees the change too.
        self.assertEqual(clock.read().values["timebase_id"], second)

    def test_unobservable_clock_leaves_timebase_undeclared_with_a_named_reason(self):
        boot = self.proc.root / "sys/kernel/random/boot_id"
        for change, reason in ((lambda: boot.write_text("not-a-boot-id\n"), "host_boot_id_invalid"),
                               (lambda: boot.write_text(BOOT_A.upper() + "\n"), "host_boot_id_invalid"),
                               (lambda: boot.write_text(BOOT_A + "\n" + "0" * 4096), "host_boot_id_invalid"),
                               (boot.unlink, "host_boot_id_unreadable")):
            with self.subTest(reason=reason):
                change()
                reading = tokens.HostClock(self.proc.root).declare()
                self.assertEqual((reading.values, reading.reasons), ({}, {"timebase_id": reason}))
        self.proc.set_boot(BOOT_A)
        namespace = self.proc.root / "self/ns/time"
        namespace.unlink()
        os.symlink("mnt:[4026531841]", namespace)
        reading = tokens.HostClock(self.proc.root).declare()
        self.assertEqual(reading.reasons, {"timebase_id": "host_time_namespace_invalid"})
        namespace.unlink()
        reading = tokens.HostClock(self.proc.root).declare()
        self.assertEqual(reading.reasons, {"timebase_id": "host_time_namespace_unreadable"})

    def test_timebase_includes_the_namespace_offset_because_namespace_inodes_are_reused(self):
        # Two time namespaces created one after the other can share an inode with different CLOCK_MONOTONIC offsets.
        reused = 4026532264
        self.proc.set_namespace("time", reused)
        self.proc.set_namespace("time_for_children", reused)
        timebases = []
        for offsets in ("monotonic        1000         0\nboottime            0         0\n",
                        "monotonic      500000         0\nboottime            0         0\n",
                        "boottime            0         0\nmonotonic          -5         7\n"):
            self.proc.set_offsets(offsets)
            reading = tokens.HostClock(self.proc.root).declare()
            self.assertEqual(reading.reasons, {})
            self.assertTrue(token_valid(reading.values["timebase_id"]))
            self.assertIn(f"timens:{reused}", reading.values["timebase_id"])
            timebases.append(reading.values["timebase_id"])
        self.assertEqual(len(set(timebases)), 3)
        self.assertTrue(timebases[0].endswith(":offset:1000:0"))
        self.assertTrue(timebases[2].endswith(":offset:-5:7"))
        # The boot time offset does not move CLOCK_MONOTONIC.
        self.proc.set_offsets("monotonic        1000         0\nboottime           9         0\n")
        self.assertEqual(tokens.HostClock(self.proc.root).declare().values["timebase_id"], timebases[0])

    def test_unobservable_namespace_offset_leaves_timebase_undeclared_with_a_named_reason(self):
        # After unshare(CLONE_NEWTIME) timens_offsets shows the children's namespace, not this process's clock.
        self.proc.set_namespace("time_for_children", 4026532264)
        self.proc.set_offsets("monotonic          -5         7\nboottime            0         0\n")
        reading = tokens.HostClock(self.proc.root).declare()
        self.assertEqual((reading.values, reading.reasons),
                         ({}, {"timebase_id": "host_time_namespace_for_children_differs"}))
        (self.proc.root / "self/ns/time_for_children").unlink()
        self.assertEqual(tokens.HostClock(self.proc.root).declare().reasons,
                         {"timebase_id": "host_time_namespace_unreadable"})
        self.proc.set_namespace("time_for_children", TIME_NAMESPACE)
        for offsets in ("", "monotonic x 0\n", "monotonic 0 0\nmonotonic 0 0\n", "boottime 0 0\n",
                        "monotonic 0 1234567890\n", "monotonic 0 0\nrealtime 0 0\n", "monotonic 0 0\n" * 300):
            with self.subTest(offsets=offsets[:40]):
                self.proc.set_offsets(offsets)
                self.assertEqual(tokens.HostClock(self.proc.root).declare().reasons,
                                 {"timebase_id": "host_time_offsets_invalid"})
        (self.proc.root / "self/timens_offsets").unlink()
        self.assertEqual(tokens.HostClock(self.proc.root).declare().reasons,
                         {"timebase_id": "host_time_offsets_unreadable"})


class RuntimeProcessTests(ProcTestCase):
    def test_runtime_id_identifies_the_running_binary_not_the_installed_one(self):
        # SteamVR updated on disk while the old build keeps running: /proc/<pid>/exe still names the old binary.
        old = self.proc.executable("steamvr-previous/vrserver", b"running vrserver build 1")
        self.proc.remove_process(PID)
        self.proc.add_process(PID, "vrserver", START, old)
        self.proc.binary.write_bytes(b"installed vrserver build 2")
        reading = tokens.RuntimeProcess(self.proc.root).declare()
        self.assertEqual(reading.reasons, {})
        runtime = reading.values["runtime_id"]
        self.assertTrue(token_valid(runtime))
        self.assertIn(hashlib.sha256(b"running vrserver build 1").hexdigest(), runtime)
        self.assertNotIn(hashlib.sha256(b"installed vrserver build 2").hexdigest(), runtime)

    def test_session_id_names_the_boot_process_and_start_time(self):
        reading = tokens.RuntimeProcess(self.proc.root).declare()
        session = reading.values["session_id"]
        self.assertTrue(token_valid(session))
        for part in (BOOT_A, str(PID), str(START)):
            self.assertIn(part, session)
        # A restarted runtime is a new session.
        self.proc.remove_process(PID)
        self.proc.add_process(PID + 1, "vrserver", START + 500, self.proc.binary)
        restarted = tokens.RuntimeProcess(self.proc.root).declare().values
        self.assertNotEqual(restarted["session_id"], session)
        self.assertEqual(restarted["runtime_id"], reading.values["runtime_id"])

    def test_per_frame_read_loses_both_tokens_when_the_runtime_process_changes(self):
        other = self.proc.executable("steamvr/bin/linux64/other/vrserver", b"another vrserver build")
        cases = (("runtime_process_exited", None),  # The runtime exited.
                 ("runtime_process_changed", (START + 1, self.proc.binary)),  # Its pid was reused.
                 ("runtime_process_changed", (START, other)))  # It executed another binary.
        for reason, replacement in cases:
            with self.subTest(reason=reason, replacement=replacement):
                runtime = tokens.RuntimeProcess(self.proc.root)
                declared = runtime.declare().values
                self.assertEqual(runtime.read().values, declared)
                self.proc.remove_process(PID)
                if replacement is not None:
                    self.proc.add_process(PID, "vrserver", *replacement)
                reading = runtime.read()
                self.assertEqual(reading.values, {"session_id": None, "runtime_id": None})
                self.assertEqual(reading.reasons, {"session_id": reason, "runtime_id": reason})
                if replacement is not None:
                    self.proc.remove_process(PID)
                self.proc.add_process(PID, "vrserver", START, self.proc.binary)

    def test_missing_ambiguous_and_unreadable_runtime_processes_are_named(self):
        both = ("session_id", "runtime_id")
        self.proc.add_process(5000, "vrserver", 1, self.proc.binary)
        self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons,
                         dict.fromkeys(both, "runtime_process_ambiguous"))
        self.proc.remove_process(5000)
        self.proc.remove_process(PID)
        self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons,
                         dict.fromkeys(both, "runtime_process_not_found"))
        self.assertEqual(tokens.RuntimeProcess(self.proc.base / "absent").declare().reasons,
                         dict.fromkeys(both, "runtime_process_unreadable"))
        # Under an emulator or loader, /proc/<pid>/exe names that program, not the runtime.
        emulator = self.proc.executable("usr/bin/FEXInterpreter", b"emulator")
        self.proc.add_process(PID, "vrserver", START, emulator)
        reading = tokens.RuntimeProcess(self.proc.root).declare()
        self.assertIn("session_id", reading.values)
        self.assertEqual(reading.reasons, {"runtime_id": "runtime_executable_unexpected"})
        self.proc.remove_process(PID)
        self.proc.add_process(PID, "vrserver", START, self.proc.base / "missing/vrserver")
        reading = tokens.RuntimeProcess(self.proc.root).declare()
        self.assertIn("session_id", reading.values)
        self.assertEqual(reading.reasons, {"runtime_id": "runtime_executable_unreadable"})
        self.proc.remove_process(PID)
        self.proc.add_process(PID, "vrserver", START, self.proc.binary)
        with patch.object(tokens, "MAX_RUNTIME_EXECUTABLE_BYTES", 4):
            self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons,
                             {"runtime_id": "runtime_executable_too_large"})
        with patch.object(tokens, "MAX_PROCESS_ENTRIES", 2):
            self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons,
                             dict.fromkeys(both, "runtime_process_scan_limit"))

    def test_runtime_id_after_an_update_by_rename_is_the_running_build(self):
        # A rename update: the running build's link reads "<install path> (deleted)", the new build sits there.
        binary = self.proc.binary
        running = binary.with_name(binary.name + " (deleted)")
        binary.rename(running)
        binary.write_bytes(b"installed vrserver build 2")
        self.proc.remove_process(PID)
        self.proc.add_process(PID, "vrserver", START, running)
        runtime = tokens.RuntimeProcess(self.proc.root)
        reading = runtime.declare()
        self.assertEqual(reading.reasons, {})
        self.assertEqual(reading.values["runtime_id"],
                         "steamvr:vrserver:sha256:" + hashlib.sha256(b"running vrserver build 1").hexdigest())
        self.assertEqual((runtime.details["executable"], runtime.details["executable_deleted"]), (str(binary), True))
        self.assertEqual(runtime.read().values, reading.values)

    def test_an_exec_while_the_binary_is_opened_or_hashed_is_refused(self):
        other = self.proc.executable("opt/other/vrserver", b"another vrserver build")
        link = self.proc.root / str(PID) / "exe"
        both = dict.fromkeys(("session_id", "runtime_id"), "runtime_process_changed")

        def repoint(target):
            link.unlink()
            os.symlink(target, link)

        # Between the inode check and open(), and back before the check after the digest.
        def swapping_open(path, *args, **kwargs):
            if Path(path) != link:
                return open(path, *args, **kwargs)
            repoint(other)
            try:
                return open(path, *args, **kwargs)
            finally:
                repoint(self.proc.binary)

        with patch.object(tokens, "open", swapping_open, create=True):
            self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons, both)

        # While the binary is hashed: the process executes another binary in the same pid.
        class Digest:
            def __init__(self):
                self.inner, self.swapped = hashlib.sha256(), False

            def update(self, data):
                if not self.swapped:
                    self.swapped = True
                    repoint(other)
                self.inner.update(data)

            def hexdigest(self):
                return self.inner.hexdigest()

        with patch.object(tokens, "hashlib", Mock(sha256=Digest)):
            runtime = tokens.RuntimeProcess(self.proc.root)
            self.assertEqual(runtime.declare().reasons, both)
            self.assertEqual(runtime.read().values, {})

    def test_a_zombie_runtime_is_an_exited_one(self):
        both = ("session_id", "runtime_id")
        dangling = self.proc.base / "missing/vrserver"  # A zombie's /proc/<pid>/exe cannot be followed.
        for exe in (dangling, self.proc.binary):
            with self.subTest(declared_on_a_zombie=str(exe)):
                self.proc.remove_process(PID)
                self.proc.add_process(PID, "vrserver", START, exe, state="Z")
                reading = tokens.RuntimeProcess(self.proc.root).declare()
                self.assertEqual((reading.values, reading.reasons), ({}, dict.fromkeys(both, "runtime_process_exited")))
        for exe, declared in ((dangling, ("session_id",)), (self.proc.binary, both)):
            for state in ("Z", "X"):
                with self.subTest(exited_after_declaration=str(exe), state=state):
                    self.proc.remove_process(PID)
                    self.proc.add_process(PID, "vrserver", START, exe)
                    runtime = tokens.RuntimeProcess(self.proc.root)
                    self.assertEqual(tuple(runtime.declare().values), declared)
                    self.proc.set_process(PID, "vrserver", START, state, exe=dangling)
                    reading = runtime.read()
                    self.assertEqual(reading.values, dict.fromkeys(declared))
                    self.assertEqual(reading.reasons, dict.fromkeys(declared, "runtime_process_exited"))

    def test_an_exec_is_seen_even_when_the_binary_cannot_be_inspected(self):
        self.proc.remove_process(PID)
        self.proc.add_process(PID, "vrserver", START, self.proc.base / "missing/vrserver")
        runtime = tokens.RuntimeProcess(self.proc.root)
        reading = runtime.declare()
        self.assertEqual(reading.reasons, {"runtime_id": "runtime_executable_unreadable"})
        self.assertEqual(runtime.read().values, reading.values)
        self.proc.set_process(PID, "bash", START)  # Same pid and start time, another program.
        self.assertEqual((runtime.read().values, runtime.read().reasons),
                         ({"session_id": None}, {"session_id": "runtime_process_changed"}))

    def test_a_pid_reused_between_the_scan_and_the_stat_read_is_refused(self):
        runtime = tokens.RuntimeProcess(self.proc.root)
        scan = runtime._find

        def find_then_reuse():
            found = scan()
            self.proc.remove_process(PID)
            self.proc.add_process(PID, "bash", START + 77, self.proc.executable("usr/bin/bash", b"shell"))
            return found

        with patch.object(runtime, "_find", find_then_reuse):
            reading = runtime.declare()
        self.assertEqual((reading.values, reading.reasons),
                         ({}, dict.fromkeys(("session_id", "runtime_id"), "runtime_process_changed")))

    def test_a_long_runtime_name_is_found_by_the_command_name_the_kernel_keeps(self):
        name = "vrserver_aarch64"  # 16 bytes: comm and stat field 2 keep the first 15.
        binary = self.proc.executable(f"steamvr/bin/linuxarm64/{name}", b"arm64 vrserver build")
        self.proc.add_process(6000, name[:15], 500, binary)
        runtime = tokens.RuntimeProcess(self.proc.root, name=name)
        reading = runtime.declare()
        self.assertEqual(reading.reasons, {})
        self.assertIn(":pid:6000:start:500", reading.values["session_id"])
        self.assertEqual(reading.values["runtime_id"],
                         f"steamvr:{name}:sha256:" + hashlib.sha256(b"arm64 vrserver build").hexdigest())
        self.assertEqual(runtime.read().reasons, {})
        # The executable's file name is compared in full.
        self.proc.set_process(6000, name[:15], 500, exe=self.proc.executable("opt/vrserver_aarch65", b"x"))
        self.assertEqual(tokens.RuntimeProcess(self.proc.root, name=name).declare().reasons,
                         {"runtime_id": "runtime_executable_unexpected"})

    def test_stat_parsing_survives_a_command_name_with_parentheses_and_spaces(self):
        self.proc.add_process(7000, "x) S 1 2 (y", 5, self.proc.binary)
        self.assertEqual(tokens.process_start_ticks(self.proc.root, 7000), 5)
        self.assertEqual(tokens.process_start_ticks(self.proc.root, PID), START)
        self.assertEqual(tokens.process_stat(self.proc.root, 7000), (b"x) S 1 2 (y", "S", 5))
        self.assertEqual(tokens.RuntimeProcess(self.proc.root).declare().reasons, {})
        for line in ("4242 vrserver S" + " 1" * 20, "4242 (vrserver)" + " 1" * 20, "4242 (vrserver) S 1\n", ""):
            with self.subTest(line=line[:24]), self.assertRaisesRegex(ValueError, "process_stat_invalid"):
                (self.proc.root / "7000/stat").write_text(line)
                tokens.process_stat(self.proc.root, 7000)


class TrackingObserverTests(unittest.TestCase):
    def test_tracking_tokens_are_undeclared_until_the_runtime_state_is_observed(self):
        observer = tokens.TrackingObserver()
        observer.observe(RECENTER)  # A change before any state describes nothing yet.
        reading = observer.declare()
        self.assertEqual(reading.values, {})
        self.assertEqual(reading.reasons, dict.fromkeys(("tracking_origin", "recenter_epoch"),
                                                        "tracking_state_not_observed"))
        observer.observe(STATE)  # The first state starts the epoch at 0; the earlier change is not counted.
        self.assertEqual(observer.read().values["recenter_epoch"], 0)

    def test_recenter_and_universe_events_bump_recenter_epoch(self):
        observer = tokens.TrackingObserver()
        observer.observe(STATE)
        origin = observer.declare().values["tracking_origin"]
        self.assertTrue(token_valid(origin))
        self.assertIn(str(STATE["universe_id"]), origin)
        self.assertEqual(observer.declare().values["recenter_epoch"], 0)
        for epoch, change in enumerate(sorted(tokens.TRACKING_CHANGES), 1):
            observer.observe({"event": "tracking_changed", "change": change})
            self.assertEqual(observer.read().values, {"tracking_origin": origin, "recenter_epoch": epoch})
        # A new state after the change replaces the origin and never resets the epoch.
        observer.observe(dict(STATE, zero_pose_sha256="cd" * 32))
        values = observer.read().values
        self.assertNotEqual(values["tracking_origin"], origin)
        self.assertEqual(values["recenter_epoch"], len(tokens.TRACKING_CHANGES))

    def test_malformed_events_or_an_ended_stream_lose_both_tokens_for_good(self):
        invalid = [dict(STATE, universe_id=value) for value in (0, -1, 2**64, True, "1", 1.0, None)]
        invalid += [dict(STATE, zero_pose_sha256=value) for value in ("ab" * 31, "AB" * 32, "zz" * 32, None)]
        invalid += [{"event": "tracking_changed", "change": "teleported"}, {"event": "tracking_changed"},
                    {"event": "tracking_mystery"}, ["tracking_state"], "tracking_state"]
        for event in invalid:
            with self.subTest(event=event):
                observer = tokens.TrackingObserver()
                observer.observe(STATE)
                observer.observe(event)
                lost = observer.read()
                self.assertEqual(lost.values, {"tracking_origin": None, "recenter_epoch": None})
                self.assertEqual(set(lost.reasons.values()), {"tracking_event_invalid"})
                observer.observe(STATE)
                self.assertEqual(observer.read().values, lost.values)
        observer = tokens.TrackingObserver()
        observer.observe(STATE)
        observer.observe({"event": "frame_expired"})  # Other bridge events are not tracking events.
        self.assertEqual(observer.read().values["recenter_epoch"], 0)
        observer.end("bridge_exited")
        self.assertEqual(observer.read().values, {"tracking_origin": None, "recenter_epoch": None})
        self.assertEqual(set(observer.read().reasons.values()), {"tracking_observer_ended"})


class MirrorSessionTokenTests(ProcTestCase):
    """The package's done conditions, at the CaptureContext and HudMask seam."""

    def full(self):
        session = self.session(observer=self.observer(STATE))
        source = self.source(session)
        return session, source, self.declared(source)

    def test_every_token_comes_from_its_provider_and_reaches_each_frame(self):
        session, source, declared = self.full()
        self.assertTrue(all(token_valid(declared[name]) for name in SESSION_TOKENS if name != "recenter_epoch"))
        self.assertEqual(declared["recenter_epoch"], 0)
        report = source.session_report
        self.assertEqual(report["declared"], declared)
        self.assertEqual(report["undeclared"], {})
        ((frame, _),) = self.read(source, nothing)
        self.assertEqual({name: frame.metadata[name] for name in SESSION_TOKENS}, declared)
        self.assertEqual(frame.metadata["native_format"], FORMAT)

    def test_recenter_event_bumps_recenter_epoch_and_later_frames_fail_closed(self):
        session, source, declared = self.full()
        observer = session.providers[-1]
        mask = self.mask(declared)
        context = CaptureContext(source, mask=mask)
        results = self.read(source, nothing, nothing, lambda: observer.observe(RECENTER), nothing,
                            admit=context.admit)
        self.assertEqual([reason for _, reason in results], [None, None, SOURCE_CHANGED, SOURCE_CHANGED])
        self.assertEqual([frame.metadata["recenter_epoch"] for frame, _ in results], [0, 0, 1, 1])
        self.assertEqual(source.recenter_epoch, 1)
        # A result of a frame read before the recenter is refused afterwards as well.
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            context.admit(results[0][0])
        # The old mask names the token: for a new context, and for a later frame's session metadata.
        with self.assertRaisesRegex(ValueError, "hud_recenter_epoch_changed"):
            CaptureContext(source, mask=mask)
        later = {name: results[2][0].metadata[name] for name in SESSION_TOKENS}
        with self.assertRaisesRegex(ValueError, "hud_recenter_epoch_changed"):
            mask.check_session({}, later)
        self.assertEqual(source.session_report["first_change"],
                         {"frame": 3, "token": "recenter_epoch", "reason": "token_changed"})

    def test_recenter_refuses_an_in_flight_result_before_the_next_read(self):
        session, source, declared = self.full()
        context = CaptureContext(source, mask=self.mask(declared))
        ((frame, reason),) = self.read(source, nothing, admit=context.admit)
        self.assertIsNone(reason)
        session.providers[-1].observe(RECENTER)
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            context.admit(frame)

    def test_changed_boot_id_changes_timebase_id_and_fails_frames_closed(self):
        _, source, declared = self.full()
        mask = self.mask(declared)
        context = CaptureContext(source, mask=mask)
        results = self.read(source, nothing, lambda: self.proc.set_boot(BOOT_B), admit=context.admit)
        self.assertEqual([reason for _, reason in results], [None, SOURCE_CHANGED])
        before, after = (frame.metadata["timebase_id"] for frame, _ in results)
        self.assertNotEqual(before, after)
        self.assertIn(BOOT_B, after)
        self.assertEqual(results[1][0].metadata["session_id"], declared["session_id"])
        with self.assertRaisesRegex(ValueError, "hud_timebase_id_changed"):
            mask.check_session({}, {name: results[1][0].metadata[name] for name in SESSION_TOKENS})
        # A source created on the new boot cannot bind the old calibration at all; a reboot is also a new session.
        rebooted = self.source(self.session(observer=self.observer(STATE)))
        self.assertIn(BOOT_B, rebooted.timebase_id)
        self.assertNotEqual(rebooted.timebase_id, declared["timebase_id"])
        with self.assertRaisesRegex(ValueError, "hud_session_id_changed"):
            CaptureContext(rebooted, mask=mask)
        with self.assertRaisesRegex(ValueError, "hud_timebase_id_changed"):
            mask.check_session(dict(declared, timebase_id=rebooted.timebase_id))

    def test_missing_provider_rejects_frames_with_a_named_reason(self):
        _, _, declared = self.full()
        mask = self.mask(declared)
        cases = ((dict(clock=False, observer=self.observer(STATE)), ("timebase_id",), "hud_timebase_id_undeclared",
                  "session_token_provider_missing"),
                 (dict(runtime=False, observer=self.observer(STATE)), ("session_id", "runtime_id"),
                  "hud_session_id_undeclared", "session_token_provider_missing"),
                 (dict(), ("tracking_origin", "recenter_epoch"), "hud_tracking_origin_undeclared",
                  "session_token_provider_missing"),
                 (dict(observer=self.observer()), ("tracking_origin", "recenter_epoch"),
                  "hud_tracking_origin_undeclared", "tracking_state_not_observed"))
        for options, missing, rejection, reason in cases:
            with self.subTest(missing=missing, reason=reason):
                source = self.source(self.session(**options))
                self.assertEqual(source.session_report["undeclared"], dict.fromkeys(missing, reason))
                for name in missing:
                    self.assertIsNone(getattr(source, name))
                context = CaptureContext(source, mask=mask)
                results = self.read(source, nothing, nothing, admit=context.admit)
                self.assertEqual([reason for _, reason in results], [rejection, rejection])
                for frame, _ in results:
                    self.assertFalse(set(missing) & set(frame.metadata))
                self.assertEqual(context.failure, rejection)

    def test_runtime_exit_during_a_run_fails_frames_closed(self):
        _, source, declared = self.full()
        context = CaptureContext(source, mask=self.mask(declared))
        results = self.read(source, nothing, lambda: self.proc.remove_process(PID), admit=context.admit)
        self.assertEqual([reason for _, reason in results], [None, SOURCE_CHANGED])
        self.assertEqual((results[1][0].metadata["session_id"], results[1][0].metadata["runtime_id"]), (None, None))
        self.assertEqual(source.session_report["first_change"],
                         {"frame": 2, "token": "session_id", "reason": "runtime_process_exited"})

    def test_a_tracking_state_first_seen_after_declaration_stays_undeclared(self):
        # The first state can arrive between source creation and the context (while the model loads).
        _, _, declared = self.full()
        mask = self.mask(declared)
        observer = self.observer()
        source = self.source(self.session(observer=observer))
        observer.observe(STATE)
        self.assertIsNone(source.tracking_origin)
        self.assertIsNone(source.recenter_epoch)
        context = CaptureContext(source, mask=mask)
        results = self.read(source, nothing, nothing, admit=context.admit)
        self.assertEqual([reason for _, reason in results], ["hud_tracking_origin_undeclared"] * 2)
        unbound = CaptureContext(source)
        ((frame, reason),) = self.read(source, nothing, admit=unbound.admit)
        self.assertIsNone(reason)
        observer.end("bridge_exited")
        unbound.admit(frame)  # Nothing tracking-related was ever bound, so nothing was lost.
        ((later, _),) = self.read(source, nothing)
        for checked in (frame, later, *(item for item, _ in results)):
            self.assertFalse({"tracking_origin", "recenter_epoch"} & set(checked.metadata))
        self.assertEqual((source.tracking_origin, source.recenter_epoch), (None, None))
        report = source.session_report
        self.assertEqual(report["undeclared"], dict.fromkeys(("tracking_origin", "recenter_epoch"),
                                                              "tracking_state_not_observed"))
        self.assertIsNone(report["first_change"])

    def test_a_clock_lost_during_a_run_is_none_in_the_frame_metadata(self):
        _, source, declared = self.full()
        mask = self.mask(declared)
        context = CaptureContext(source, mask=mask)
        boot = self.proc.root / "sys/kernel/random/boot_id"
        results = self.read(source, nothing, boot.unlink, admit=context.admit)
        self.assertEqual([reason for _, reason in results], [None, SOURCE_CHANGED])
        metadata = results[1][0].metadata
        self.assertIn("timebase_id", metadata)
        self.assertIsNone(metadata["timebase_id"])
        self.assertIsNone(source.timebase_id)
        with self.assertRaisesRegex(ValueError, "hud_timebase_id_changed"):
            mask.check_session({}, {name: metadata[name] for name in SESSION_TOKENS})
        self.assertEqual(source.session_report["first_change"],
                         {"frame": 2, "token": "timebase_id", "reason": "host_boot_id_unreadable"})

    def test_after_a_runtime_exit_the_source_declares_the_lost_tokens(self):
        _, source, declared = self.full()
        context = CaptureContext(source, mask=self.mask(declared))
        results = self.read(source, nothing, lambda: self.proc.remove_process(PID))
        self.assertEqual((source.session_id, source.runtime_id), (None, None))
        self.assertEqual(source.timebase_id, declared["timebase_id"])
        # A result of a frame read before the exit is refused once the exit was seen.
        with self.assertRaisesRegex(ValueError, SOURCE_CHANGED):
            context.admit(results[0][0])

    def test_synthetic_run_with_every_token_declared_still_passes(self):
        _, source, declared = self.full()
        context = CaptureContext(source, mask=self.mask(declared))
        results = self.read(source, *[nothing] * 5, admit=context.admit)
        self.assertEqual([reason for _, reason in results], [None] * 5)
        self.assertIsNone(context.failure)
        self.assertIsNone(source.session_report["first_change"])

    def test_a_session_token_has_one_source_and_a_session_serves_one_source(self):
        session = self.session(observer=self.observer(STATE))
        with self.assertRaisesRegex(ValueError, "mirror_session_tokens_declared_twice"):
            self.source(session, identity=type("Declared", (synthetic.SyntheticSession, Identity), {}))
        session = self.session(observer=self.observer(STATE))
        self.source(session)
        with self.assertRaisesRegex(ValueError, "session_already_declared"):
            self.source(session)
        for providers in ((tokens.HostClock(self.proc.root), tokens.HostClock(self.proc.root)), (object(),)):
            with self.subTest(providers=providers), self.assertRaisesRegex(ValueError, "session_token_providers_invalid"):
                tokens.MirrorSession(*providers)

    def test_fixture_calibration_session_must_name_every_token(self):
        _, _, declared = self.full()
        self.assertEqual({name: getattr(self.mask(declared).calibration, name) for name in SESSION_TOKENS}, declared)
        for session in ({name: declared[name] for name in SESSION_TOKENS[:-1]}, dict(declared, extra="x"), None, []):
            with self.subTest(session=session), self.assertRaisesRegex(ValueError, "hud_synthetic_session_invalid"):
                self.mask(session)
        with self.assertRaisesRegex(ValueError, "hud_calibration_context_invalid"):
            self.mask(dict(declared, session_id="\n"))


class SessionGateTests(ProcTestCase):
    """The tokens open nothing: the live-image gate still refuses the mirror for HUD input."""

    def test_live_image_gate_still_refuses_the_mirror_for_hud_input(self):
        declared = self.declared(self.source(self.session(observer=self.observer(STATE))))
        calibration = self.mask(declared).calibration
        mirror_backend = Mock(spec=["kind", "source_id", "capture_layout"], kind="steamvr_mirror",
                              source_id="mirror:observed", capture_layout="mono")
        for backend in (None, mirror_backend):
            with self.subTest(backend=backend), patch.object(acquisition, "NativeMirror") as native:
                detector = Mock()
                report, last = live.run_recognition({}, .2, threading.Event(), hud_mode=True,
                                                    calibration=calibration, backend_factory=backend,
                                                    detector_factory=detector,
                                                    session=self.session(observer=self.observer(STATE)))
                native.assert_not_called()
                detector.assert_not_called()
                if backend is not None:
                    backend.assert_not_called()
                self.assertIsNone(last)
                self.assertEqual(report["status"], "error")
                self.assertIn("hud_live_mirror_not_approved", report["error"])
        source = MirrorSource(None, identity=None, session=self.session(observer=self.observer(STATE)))
        with self.assertRaisesRegex(ValueError, "live_image_source_not_approved"):
            live.require_synthetic(source, "live_image_source_not_approved")
        # A source whose tokens would come from two places is a failed run, reported before anything opens.
        backend = type("Declared", (synthetic.SyntheticSession, Identity), {})
        with patch.object(acquisition, "NativeMirror") as native:
            detector = Mock()
            report, last = live.run_recognition({}, .2, threading.Event(), hud_mode=True, calibration=calibration,
                                                backend_factory=backend, detector_factory=detector,
                                                session=self.session(observer=self.observer(STATE)))
        native.assert_not_called()
        detector.assert_not_called()
        self.assertIsNone(last)
        self.assertEqual((report["status"], report["reason"]), ("error", "mirror_session_tokens_declared_twice"))
        self.assertEqual(gates.claims(), dict.fromkeys(gates.claims(), False))

    def test_a_refused_hud_run_reads_nothing_for_its_session_tokens(self):
        calibration = self.mask(self.declared(self.source(self.session(observer=self.observer(STATE))))).calibration
        mirror_backend = Mock(spec=["kind", "source_id", "capture_layout"], kind="steamvr_mirror",
                              source_id="mirror:observed", capture_layout="mono")
        for backend in (None, mirror_backend):
            session = self.session(observer=self.observer(STATE))
            with self.subTest(backend=backend), ExitStack() as stack:
                native = stack.enter_context(patch.object(acquisition, "NativeMirror"))
                work = [stack.enter_context(patch.object(owner, method, side_effect=AssertionError("provider work")))
                        for owner in (session, *session.providers) for method in ("declare", "read")]
                report, last = live.run_recognition({}, .2, threading.Event(), hud_mode=True,
                                                    calibration=calibration, backend_factory=backend,
                                                    detector_factory=Mock(), session=session)
                for called in work:
                    called.assert_not_called()
                native.assert_not_called()
                self.assertIsNone(last)
                # The same refusal as a run without a session.
                plain, _ = live.run_recognition({}, .2, threading.Event(), hud_mode=True, calibration=calibration,
                                                backend_factory=backend, detector_factory=Mock())
                self.assertEqual(report, plain)
                self.assertEqual((report["status"], report["reason"], report["phase"], report["capture"]),
                                 ("error", "recognition_failed", "closed", {}))
                self.assertIn("hud_live_mirror_not_approved", report["error"])
                self.assertNotIn("session_tokens", report)


@unittest.skipUnless(AVAILABLE, "OpenCV and NumPy required for a masked recognition run")
class SessionRecognitionTests(ProcTestCase):
    """``runtime.live.run_recognition`` in HUD mode with a fixture backend and every provider."""

    def backend(self, on_read=None):
        class Backend(Identity):
            reads = 0
            closed = False

            def __init__(self, _candidate):
                self.capabilities = {"driver": "synthetic"}
                self.format = dict(FORMAT)

            def open(self):
                type(self).closed = False

            def read_frame(self, timeout):
                type(self).reads += 1
                if on_read is not None:
                    on_read(type(self).reads)
                return bytes([63]) * FORMAT["buffer_bytes"]

            def close(self):
                type(self).closed = True
        return Backend

    def run_hud(self, backend, session, calibration):
        class Detector(synthetic.FixtureDetector):
            def detect(self, image, threshold):
                return DetectionResult((), 0.0)
        return live.run_recognition({"node": "synthetic:only"}, .5, threading.Event(), hud_mode=True,
                                    calibration=calibration, backend_factory=backend, detector_factory=Detector,
                                    session=session)

    def test_synthetic_run_with_every_token_declared_still_passes(self):
        session = self.session(observer=self.observer(STATE))
        declared = self.declared(self.source(self.session(observer=self.observer(STATE))))
        backend = self.backend()
        report, last = self.run_hud(backend, session, self.mask(declared).calibration)
        self.assertEqual(report["status"], "recognized", report)
        self.assertIsNotNone(last)
        self.assertTrue(backend.closed)
        self.assertEqual(report["session_tokens"]["declared"], declared)
        self.assertEqual(report["session_tokens"]["undeclared"], {})
        self.assertIsNone(report["session_tokens"]["first_change"])

    def test_recenter_during_the_run_stops_recognition(self):
        observer = self.observer(STATE)
        session = self.session(observer=observer)
        declared = self.declared(self.source(self.session(observer=self.observer(STATE))))
        backend = self.backend(lambda reads: observer.observe(RECENTER) if reads == 3 else None)
        report, last = self.run_hud(backend, session, self.mask(declared).calibration)
        self.assertEqual(report["status"], "error", report)
        self.assertIn(SOURCE_CHANGED, report["error"])
        self.assertIsNone(last)
        self.assertTrue(backend.closed)
        self.assertEqual(report["session_tokens"]["first_change"]["token"], "recenter_epoch")


if __name__ == "__main__":
    unittest.main()
