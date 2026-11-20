"""The presence stop: a run without display ends when SteamVR says the headset is no longer worn.

Device step L-D1's first run (E100) showed that the camera service keeps
delivering frames after the headset comes off, so the helper's own stop, a
2.0 s gap in frames, never fired; SteamVR's standby did, 7.0 s after the
wearer's click. The owner decided on 2026-11-20 to take that signal from
SteamVR. ``PresenceWatch`` launches ``sightcircuit-presence-watcher``
(``tools/presence_watcher.cpp``, built by ``scripts/build_presence_watcher.sh``)
beside the camera helper, reads its strict JSON lines and sets the run's stop
event when an ending signal arrives. The helper's frame-gap stop stays as the
second line of defence.

Presence signals only. The watcher joins a running SteamVR as a Background
application, which never starts one; it asks for ``IVRSystem`` alone, creates
no overlay, writes no setting and reads no camera data, image or calibration
value, and its ``ready`` line says so. This module refuses a ``ready`` that
says otherwise. Nothing here opens a camera or lifts a gate.

Which signal ends a run (``SIGNALS``; the same table as
``tools/presence_signals.hpp``, and a line that disagrees with it is
refused). Only ``enter_standby_event``, SteamVR's ``VREvent_EnterStandbyMode``,
with reason ``standby``: it is SteamVR's own statement and the one E100's
journal shows. The faster candidates (the HMD's proximity sensor, its
activity level, the user-interaction events) are recorded with their times
and end nothing: none has been observed on the Frame, and each can fire with
the headset still on. ``leave_standby_event`` is recorded and restarts
nothing: a run is one launch.

Fail closed. A run that asked for the watch does not read the camera without
it: ``start`` returns a refusal, and the caller launches no helper, when the
watcher cannot be launched, does not say ``ready`` in time, cannot connect,
reports no headset, reports a headset already in standby (the ending signal
is an edge that would then never come) or writes a line this module does not
accept. Once armed, a watcher that exits, falls silent (no line for
``HEARTBEAT_TIMEOUT_SECONDS``; it writes a heartbeat every 500 ms) or writes
an unacceptable line is ``lost``, and the run's stop event is set at once. A
run that did not ask for the watch says so (``NOT_REQUESTED``): ``armed`` is
false in its report, so it cannot be mistaken for one that had the stop.

Times. The watcher stamps each line with ``CLOCK_MONOTONIC``, the clock
``time.monotonic_ns`` reads on Linux, so its times sit on the run's timebase;
``received_monotonic_ns`` is when this process read the line. A stamp later
than its own receipt, or earlier than the launch, is refused. The report
holds counts, closed names and times: no free text from the watcher.
"""
import collections
import copy
import json
import os
import re
import select
import signal
import subprocess
import threading
import time

from . import marks

PROTOCOL = "presence-watch/1"
# signal -> the run's end reason, or None for a signal that is recorded and ends nothing.
SIGNALS = {"enter_standby_event": "standby", "leave_standby_event": None, "user_interaction_ended": None,
           "user_interaction_started": None, "proximity_off": None, "proximity_on": None,
           "hmd_activity_changed": None}
ENDING_SIGNALS = tuple(name for name, reason in SIGNALS.items() if reason is not None)
END_REASONS = tuple(sorted({reason for reason in SIGNALS.values() if reason is not None}))  # ("standby",)
ACTIVITY_LEVELS = ("unknown", "idle", "user_interaction", "user_interaction_timeout", "standby", "idle_timeout")
WATCHER_END_REASONS = ("stop_signal", "max_seconds", "runtime_quit", "parent_gone", "headset_not_connected")
WATCHER_ERRORS = ("vrserver_not_running", "vr_init_failed", "ivrsystem_unavailable", "headset_not_connected",
                  "parent_death_signal_unavailable", "usage")
APPLICATION_TYPE = "VRApplication_Background"
MAX_WATCH_SECONDS = 120  # The watcher's own bound (its --seconds range).
WATCH_MARGIN_SECONDS = 20  # Beyond the run's seconds: the helper's launch margin and this module's arming.
READY_WAIT_SECONDS = 12.0  # The watcher gives its connect 10 s before SIGALRM ends it.
HEARTBEAT_TIMEOUT_SECONDS = 3.0  # Six missed heartbeats.
STOP_WAIT_SECONDS = 3.0  # For the watcher to leave after SIGTERM, then it is killed.
KILL_WAIT_SECONDS = 2.0
JOIN_SECONDS = 2.0
POLL_SECONDS = 0.05
MAX_LINE_BYTES = 8192
# Presence events repeated in the report: the first and the last ``EVENTS_KEPT_EACH_END`` of a run, so a signal
# that flaps while the headset is worn cannot push out the events around the removal. ``event_count`` and
# ``signal_counts`` cover them all, and the ending signal is always reported on its own.
EVENTS_KEPT_EACH_END = 32
MAX_EVENTS = 2 * EVENTS_KEPT_EACH_END
MAX_EVENT_AGE_MS = 3_600_000  # The watcher writes null beyond an hour.
MAX_INIT_ERROR_CODE = 9999
INTERFACE_PATTERN = re.compile(r"IVRSystem_[0-9]{3}")
# VR_Init's error codes this module names (pinned openvr.h, EVRInitError; a test holds them to the header). Any
# other code is reported as its number alone: no text from the watcher reaches the report.
INIT_ERROR_NAMES = {100: "VRInitError_Init_InstallationNotFound", 101: "VRInitError_Init_InstallationCorrupt",
                    102: "VRInitError_Init_VRClientDLLNotFound", 105: "VRInitError_Init_InterfaceNotFound",
                    108: "VRInitError_Init_HmdNotFound", 110: "VRInitError_Init_PathRegistryNotFound",
                    121: "VRInitError_Init_NoServerForBackgroundApp", 124: "VRInitError_Init_Internal"}
STOPPED_WHILE_ARMING = "stopped_while_arming"  # ``start``'s answer when the caller's stop came first; not a refusal.
FUTURE_TOLERANCE_NS = 5_000_000
# Statuses a command gives a run the watch ended or failed.
STATUS_PRESENCE_ENDED, STATUS_WATCH_LOST = marks.STATUS_PRESENCE_ENDED, marks.STATUS_WATCH_LOST
REASON_NOT_ARMED = marks.REASON_WATCH_NOT_ARMED
STATEMENT = ("Presence signals from SteamVR only: runtime events and the headset's activity level. The watcher "
             "reads no camera data, image or calibration value, creates no overlay and writes no setting.")
READY_FACTS = {"protocol": PROTOCOL, "application_type": APPLICATION_TYPE, "starts_steamvr": False,
               "overlay_created": False, "settings_written": False, "camera_data_read": False, "hmd_connected": True}
NOT_REQUESTED = {"requested": False, "armed": False, "refusal": None, "protocol": PROTOCOL, "ended_run": False,
                 "ending_signal": None, "lost": None, "event_count": 0, "events": [], "cleanup_ok": True,
                 "statement": "No presence watcher was named: SteamVR's headset-off signal was not armed for this "
                              "run, which could end early only on the helper's own frame-gap stop."}
STATE_UNKNOWN_STATEMENT = ("A presence watcher was named, but the run's process gave no report: whether the watch was "
                           "armed and whether the watcher has ended is not known.")


def not_requested():
    """A fresh ``NOT_REQUESTED`` block: only for a run that named no watcher."""
    return copy.deepcopy(NOT_REQUESTED)


def unstarted_report():
    """The block of a run that named the watcher and ended before launching it: requested, never armed, nothing to clean."""
    return PresenceWatch(None, 1, threading.Event()).report(marks.Clock())


def unknown_report():
    """The block a caller writes for a named watcher whose run left no report: nothing about it is confirmed."""
    return dict(unstarted_report(), armed=None, cleanup_ok=False, state_known=False, statement=STATE_UNKNOWN_STATEMENT)


def carry(error, watch, clock):
    """Attach a closed watch's block to the exception that is ending its run, for the caller's report."""
    try:
        error.presence_watch = watch.report(clock)
    except Exception:  # Never mask the exception that is propagating.
        pass


def apply_failure(report, error, named):
    """Give a report that replaces a failed run's own the truth about its watch: ``error`` may carry the block."""
    if not named:
        return report
    block = getattr(error, "presence_watch", None)
    report["presence_watch"] = block if isinstance(block, dict) else unstarted_report()
    report["cleanup_ok"] = bool(report.get("cleanup_ok", True)) and bool(report["presence_watch"]["cleanup_ok"])
    return report


def signal_table():
    """``SIGNALS`` as the watcher writes it on ``--describe`` and ``ready``."""
    return [{"end_reason": reason, "ends_run": reason is not None, "signal": name} for name, reason in SIGNALS.items()]


def watch_seconds(max_seconds):
    """The watcher's bound for a run of ``max_seconds``."""
    return min(MAX_WATCH_SECONDS, int(max_seconds) + WATCH_MARGIN_SECONDS)


def _integer(value, maximum=None):
    return type(value) is int and value >= 0 and (maximum is None or value <= maximum)


def _named(value, choices):
    return isinstance(value, str) and value in choices


def check_line(message, *, sequence, ready_seen, launched_ns, received_ns):
    """The validated ``event`` name of one watcher line, else ``ValueError`` with a closed reason.

    Every key this module later reads is required here with its type: a
    closed name is a string from its list, a flag is ``True`` or ``False``
    itself, a number is a bounded integer. Whatever else a line holds is
    never read.
    """
    if not isinstance(message, dict):
        raise ValueError("line_not_an_object")
    event = message.get("event")
    if not _named(event, ("ready", "presence", "heartbeat", "end", "error")):
        raise ValueError("line_event_unknown")
    if type(message.get("seq")) is not int or message["seq"] != sequence:
        raise ValueError("line_sequence_broken")
    if not _integer(message.get("monotonic_ns")) or not _integer(message.get("realtime_ns")):
        raise ValueError("line_time_missing")
    if not launched_ns <= message["monotonic_ns"] <= received_ns + FUTURE_TOLERANCE_NS:
        raise ValueError("line_time_out_of_range")
    if event == "ready":
        if ready_seen:
            raise ValueError("ready_repeated")
        for name, expected in READY_FACTS.items():
            if type(message.get(name)) is not type(expected) or message[name] != expected:
                raise ValueError(f"ready_{name}_refused")
        if message.get("signals") != signal_table():
            raise ValueError("ready_signal_table_differs")
        requested = message.get("interfaces_requested")
        if (not isinstance(requested, list) or len(requested) != 1 or not isinstance(requested[0], str)
                or INTERFACE_PATTERN.fullmatch(requested[0]) is None):
            raise ValueError("ready_interfaces_refused")
        if not _named(message.get("hmd_activity"), ACTIVITY_LEVELS):
            raise ValueError("ready_activity_unknown")
        if "hmd_has_proximity_sensor" not in message or not any(
                message["hmd_has_proximity_sensor"] is allowed for allowed in (True, False, None)):
            raise ValueError("ready_proximity_invalid")
    elif event == "error":
        if ready_seen or not _named(message.get("reason"), WATCHER_ERRORS):
            raise ValueError("error_line_refused")
        if "init_error_code" in message and not _integer(message["init_error_code"], MAX_INIT_ERROR_CODE):
            raise ValueError("error_init_code_invalid")
    elif not ready_seen and not (event == "end" and message.get("reason") == "headset_not_connected"):
        raise ValueError("line_before_ready")
    if event == "presence":
        name = message.get("signal")
        if not _named(name, SIGNALS):
            raise ValueError("presence_signal_unknown")
        reason = message.get("end_reason", "")
        if (message.get("ends_run") is not (SIGNALS[name] is not None) or
                not (reason is None if SIGNALS[name] is None else _named(reason, (SIGNALS[name],)))):
            raise ValueError("presence_signal_table_differs")
        if not _named(message.get("hmd_activity"), ACTIVITY_LEVELS):
            raise ValueError("presence_activity_unknown")
        if "event_age_ms" not in message or not (message["event_age_ms"] is None or
                                                 _integer(message["event_age_ms"], MAX_EVENT_AGE_MS)):
            raise ValueError("presence_event_age_invalid")
    elif event == "end" and not _named(message.get("reason"), WATCHER_END_REASONS):
        raise ValueError("end_reason_unknown")
    return event


class PresenceWatch:
    """One watcher process for one run: arm it, let it set ``stop``, close it, report it.

    ``binary`` is the watcher, held to the helper's path rules
    (``camera_helper.helper_path``: an executable outside the checkout or under
    the ignored ``artifacts/``). ``stop`` is the run's stop event, the one the
    helper's supervision polls. ``start()`` returns ``None`` once the watcher
    is armed, else the refusal (a closed name); ``frame()`` counts a frame
    received after the ending signal; ``run_ended()`` marks the helper's end,
    after which a signal is recorded and ends nothing; ``close()`` ends the
    watcher and is safe to call more than once; ``report(clock)`` is the
    report's ``presence_watch`` block.
    """

    def __init__(self, binary, max_seconds, stop, *, ready_wait=None, heartbeat_timeout=None, on_signal=None):
        self.binary, self.max_seconds, self.stop = binary, watch_seconds(max_seconds), stop
        self.ready_wait = READY_WAIT_SECONDS if ready_wait is None else ready_wait
        self.heartbeat_timeout = HEARTBEAT_TIMEOUT_SECONDS if heartbeat_timeout is None else heartbeat_timeout
        self.path = self.sha256 = self.process = self.reader = None
        self.launched_ns = self.armed_ns = self.run_over_ns = self.last_line_ns = None
        self.armed = self.closing = self.closed = self.forced_kill = False
        self.refusal = self.ready = self.end = self.ending = self.lost = self.exit_code = None
        self.on_signal = on_signal  # Called once, on the reader thread, when the ending signal sets the stop.
        self.events_first, self.events_last = [], collections.deque(maxlen=EVENTS_KEPT_EACH_END)
        self.signal_counts = dict.fromkeys(SIGNALS, 0)
        self.event_count, self.heartbeats, self.frames_after_signal = 0, 0, 0
        self.init_error_code = None  # VR_Init's error code from the watcher's error line: a number, never its text.
        self.reader_failed = False
        self.lines, self.lines_ignored, self.sequence = 0, 0, 0
        self.stop_set_by = None  # "signal" or "lost": what this watch set the stop event for.
        self._lock = threading.Lock()
        self._decided = threading.Event()  # ready accepted, or the watch refused or lost.

    # -- the run's side -----------------------------------------------------------------------------------------

    def start(self, abort=None):
        """Launch the watcher and wait for its ``ready``; ``None`` when armed, else the refusal.

        Call it on the main thread: the watcher asks the kernel to end it when
        its parent dies, and Linux ties that to the thread that launched it,
        so a watcher started from a thread that then exits would be lost a
        moment later. Any other thread is refused
        (``watcher_start_off_main_thread``) with nothing launched. ``abort``,
        when given, is an event of the caller (a Stop click): once it is set
        the wait ends and the answer is ``STOPPED_WHILE_ARMING``, which is the
        caller's own stop and not a refusal.
        """
        from ..errors import SightCircuitError
        from ..sources import camera_helper  # Here, not at import: the sources package imports this one.
        if threading.current_thread() is not threading.main_thread():
            return self._refuse("watcher_start_off_main_thread")
        try:
            self.path = camera_helper.helper_path(self.binary)
        except SightCircuitError as error:
            return self._refuse("watcher_" + error.reason.removeprefix("helper_"))
        self.sha256 = camera_helper.helper_digest(self.path)
        self.launched_ns = time.monotonic_ns()
        try:
            self.process = subprocess.Popen([self.path, "--watch", "--seconds", str(self.max_seconds)],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return self._refuse("watcher_launch_failed")
        self.reader = threading.Thread(target=self._read, name="presence-watch-reader", daemon=True)
        self.reader.start()
        deadline = time.monotonic() + self.ready_wait
        while not self._decided.wait(POLL_SECONDS):
            if abort is not None and abort.is_set():
                return STOPPED_WHILE_ARMING
            if time.monotonic() >= deadline:
                self._refuse("watcher_not_ready_in_time")
                break
        with self._lock:
            if self.refusal is None and self.lost is not None:  # Armed and lost again before the run began.
                self.refusal = "watcher_lost_before_launch:" + self.lost["reason"]
                self.stop_set_by = None  # Nothing was running for the loss to end: it is a refusal, not an end.
            if self.refusal is not None:
                self.armed = False
            return self.refusal

    def frame(self):
        """Count one frame the run received; after the ending signal it is a frame after the signal."""
        if self.ending is not None:
            with self._lock:
                self.frames_after_signal += 1

    def run_ended(self, monotonic_ns=None):
        """The helper's end: from here a signal or a loss is recorded and sets no stop."""
        with self._lock:
            if self.run_over_ns is None:
                self.run_over_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns

    def close(self):
        """End the watcher: SIGTERM, a bounded wait, then SIGKILL; join the reader. Idempotent."""
        with self._lock:
            if self.closed:
                return
            self.closing = self.closed = True
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            self.forced_kill = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        self.exit_code = process.poll()
        self.reader.join(JOIN_SECONDS)
        if not self.reader.is_alive():
            process.stdout.close()

    @property
    def cleanup_ok(self):
        """Whether the watcher is proven gone: never launched, or reaped with its reader finished and no kill."""
        if self.process is None:
            return True
        return self.closed and self.exit_code is not None and not self.forced_kill and not self.reader.is_alive()

    # -- the reader thread ----------------------------------------------------------------------------------------

    def _refuse(self, reason):
        with self._lock:
            if self.refusal is None and not self.armed:
                self.refusal = reason
        self._decided.set()
        return self.refusal

    def _lose(self, reason, now):
        """The watch stopped being trustworthy: before ``ready`` a refusal, afterwards a loss that ends the run."""
        with self._lock:
            if self.closing or self.lost is not None or self.refusal is not None:
                return
            if not self.armed:
                self.refusal = reason
            else:
                over = self.run_over_ns is not None
                self.lost = {"reason": reason, "monotonic_ns": now, "after_run_end": over}
                if not over and not self.stop.is_set():
                    self.stop_set_by = "lost"
                    self.stop.set()
        self._decided.set()

    def _read(self):
        """The reader thread. Whatever goes wrong in it is a loss: a watch whose reader died is not listening."""
        try:
            self._read_lines()
        except Exception:
            self.reader_failed = True
            self._lose("reader_failed", time.monotonic_ns())
            try:  # Keep the pipe empty so the watcher can still write its end and leave when it is stopped.
                while os.read(self.process.stdout.fileno(), 65536):
                    pass
            except (OSError, ValueError):
                pass

    def _read_lines(self):
        descriptor = self.process.stdout.fileno()
        pending = bytearray()
        while True:
            try:
                readable, _, _ = select.select([descriptor], [], [], POLL_SECONDS)
            except (OSError, ValueError):
                break
            now = time.monotonic_ns()
            if not readable:
                last = self.last_line_ns
                if self.armed and last is not None and now - last > self.heartbeat_timeout * 1e9:
                    self._lose("heartbeat_missed", now)
                continue
            try:
                chunk = os.read(descriptor, 65536)
            except InterruptedError:
                continue
            except OSError:
                break
            if not chunk:
                break
            pending.extend(chunk)
            while True:
                line, newline, rest = pending.partition(b"\n")
                if not newline:
                    break
                pending = bytearray(rest)
                self._line(bytes(line), time.monotonic_ns())
            if len(pending) > MAX_LINE_BYTES:
                pending = bytearray()
                self._lose("line_too_long", now)
        now = time.monotonic_ns()
        if pending:
            self._lose("line_unterminated", now)
        end = self.end
        self._lose("watcher_exited" if end is None else "watcher_ended:" + end["reason"], now)

    def _line(self, line, received_ns):
        self.lines += 1
        if self.lost is not None or self.refusal is not None or self.end is not None:
            self.lines_ignored += 1
            if self.end is not None:
                self._lose("line_after_end", received_ns)
            return
        try:
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("line_too_long")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError("line_not_json") from None
            event = check_line(message, sequence=self.sequence, ready_seen=self.ready is not None,
                               launched_ns=self.launched_ns, received_ns=received_ns)
        except ValueError as error:
            self._lose(str(error), received_ns)
            return
        self.sequence += 1
        self.last_line_ns = received_ns
        stamp = {"monotonic_ns": message["monotonic_ns"], "received_monotonic_ns": received_ns}
        if event == "ready":
            self.ready = dict(stamp, hmd_activity=message["hmd_activity"],
                              hmd_has_proximity_sensor=message["hmd_has_proximity_sensor"],
                              interface=message["interfaces_requested"][0])
            if message["hmd_activity"] == "standby":  # The ending signal is an edge; it would never come.
                self._refuse("headset_in_standby_at_start")
                return
            with self._lock:
                if self.refusal is None:
                    self.armed, self.armed_ns = True, received_ns
            self._decided.set()
        elif event == "error":
            self.init_error_code = message.get("init_error_code")
            self._refuse("watcher_error:" + message["reason"])
        elif event == "heartbeat":
            self.heartbeats += 1
        elif event == "end":
            self.end = dict(stamp, reason=message["reason"])
        else:
            self._presence(message, stamp)

    def _presence(self, message, stamp):
        record = dict(stamp, signal=message["signal"], ends_run=message["ends_run"], end_reason=message["end_reason"],
                      hmd_activity=message["hmd_activity"], event_age_ms=message["event_age_ms"])
        with self._lock:
            over = self.run_over_ns is not None
            record["after_run_end"] = over
            self.event_count += 1
            self.signal_counts[message["signal"]] += 1
            if len(self.events_first) < EVENTS_KEPT_EACH_END:
                self.events_first.append(record)
            else:
                self.events_last.append(record)
            ended = False
            if message["ends_run"] and self.ending is None and not over and not self.closing:
                # The first ending signal of the run. A stop someone else already asked for stays theirs.
                self.ending = dict(record, stop_already_set=self.stop.is_set())
                if not self.stop.is_set():
                    self.stop_set_by = "signal"
                    self.stop.set()
                    ended = True
        if ended and self.on_signal is not None:
            self.on_signal(self.ending["monotonic_ns"])

    # -- the report -----------------------------------------------------------------------------------------------

    def ended_run(self, run_stopped=True):
        """Whether the ending signal is what ended the run: it set the stop, and the run did end ``stopped``."""
        return bool(run_stopped and self.stop_set_by == "signal")

    def lost_ended_run(self, run_stopped=True):
        return bool(run_stopped and self.stop_set_by == "lost")

    def summary(self, run_stopped=True):
        """What ``marks.summarize`` needs for the removal entries; counts, names and times."""
        ending = self.ending
        return {"armed": self.armed, "ended_run": self.ended_run(run_stopped),
                "signal": None if ending is None else ending["signal"],
                "end_reason": None if ending is None else ending["end_reason"],
                "monotonic_ns": None if ending is None else ending["monotonic_ns"],
                "frames_received_after_signal": None if ending is None else self.frames_after_signal}

    def report(self, clock, *, run_stopped=True, helper_end_ns=None):
        """The ``presence_watch`` block. ``run_stopped`` says the run ended on the stop event."""
        def stamped(record, *names):
            entry = dict(record)
            for name in names or ("monotonic_ns",):
                entry[name.replace("monotonic_ns", "utc_ns")] = clock.utc(record[name])
            return entry
        ending = None if self.ending is None else stamped(self.ending)
        with self._lock:
            events = [*self.events_first, *self.events_last]
        signal_to_end_ms = None
        if ending is not None and helper_end_ns is not None:
            signal_to_end_ms = round((helper_end_ns - ending["monotonic_ns"]) / 1e6, 3)
        return {"requested": True, "armed": self.armed, "refusal": self.refusal, "protocol": PROTOCOL,
                "watcher": {"path": self.path, "sha256": self.sha256, "max_seconds": self.max_seconds},
                "launched": None if self.launched_ns is None else clock.stamp(self.launched_ns),
                "armed_at": None if self.armed_ns is None else clock.stamp(self.armed_ns),
                "ready": None if self.ready is None else stamped(self.ready),
                "ending_signals": list(ENDING_SIGNALS), "reported_signals": list(SIGNALS),
                "events": [stamped(record) for record in events], "event_count": self.event_count,
                "events_not_listed": self.event_count - len(events), "events_kept_each_end": EVENTS_KEPT_EACH_END,
                "signal_counts": dict(self.signal_counts),
                "init_error_code": self.init_error_code,
                "init_error_name": INIT_ERROR_NAMES.get(self.init_error_code),
                "ending_signal": ending, "ended_run": self.ended_run(run_stopped),
                "run_end_reason": ending["end_reason"] if ending is not None and self.ended_run(run_stopped) else None,
                "signal_to_helper_end_ms": signal_to_end_ms,
                "frames_received_after_signal": None if ending is None else self.frames_after_signal,
                "lost": None if self.lost is None else stamped(self.lost),
                "lost_ended_run": self.lost_ended_run(run_stopped),
                "heartbeats": self.heartbeats, "lines": self.lines, "lines_ignored": self.lines_ignored,
                "heartbeat_timeout_ms": round(self.heartbeat_timeout * 1000),
                "end": None if self.end is None else stamped(self.end),
                "exit_code": self.exit_code, "forced_kill": self.forced_kill, "cleanup_ok": self.cleanup_ok,
                "timebase": "watcher_clock_monotonic_is_the_host_monotonic_clock",
                "camera_data_read": False, "statement": STATEMENT}
