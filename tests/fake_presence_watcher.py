"""The stand-in for ``sightcircuit-presence-watcher --watch --seconds N``: scripted presence lines, never SteamVR.

Run as a program with the watcher's arguments it writes the watcher's JSON
lines (``presence-watch/1``) on stdout with real host clocks: ``ready``, a
heartbeat every 100 ms, and ``end`` with reason ``stop_signal`` on SIGTERM
or SIGINT (exit 0), or ``max_seconds`` at its bound. ``FAKE_PRESENCE_SCENARIO``
in the environment scripts it, as comma-separated items. Flags:
``noready`` (write nothing and wait), ``error=<reason>`` (one error line, exit
1), ``activity=<level>`` (the headset's level in ``ready``), ``table`` (a
``ready`` whose signal table makes the proximity sensor end runs), ``camera``
(a ``ready`` that claims to read camera data), ``deaf`` (ignore stop
signals), ``exit=<code>`` (the exit code at the end). Timed steps,
``<ms>:<action>`` from ``ready``: a signal name of
``sightcircuit.camera.presence.SIGNALS`` (one presence line), ``die`` (exit 3
with no end line), ``silent`` (stop writing, stay alive), ``trickle`` (stop writing lines and write one byte that ends no
line every 30 ms), ``garbage`` (a line
that is not JSON), ``skip`` (a heartbeat with a wrong sequence number),
``future`` (a heartbeat stamped ten seconds ahead), ``nokey``, ``listsignal``
and ``hugeage`` (a standby line that is valid JSON but lacks ``event_age_ms``,
has a list for its signal, or an absurd age), ``flap=<n>`` (n activity
changes at once) and ``end=<reason>`` (an end line and exit 0). ``FAKE_PRESENCE_PID_FILE`` names a file the process id
is written to. The tests install it as an executable file outside the
checkout. Never SteamVR, never a camera.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import presence  # noqa: E402

HEARTBEAT_NS = 100_000_000
SHEBANG_INSTALL = ("#!{python}\nimport sys\nsys.path.insert(0, {root!r})\n"
                   "from tests.fake_presence_watcher import main\nsys.exit(main())\n")


def install(directory, python=sys.executable):
    """Write the executable stand-in into ``directory`` (outside the checkout); return its path."""
    path = Path(directory) / "sightcircuit-presence-watcher-fake"
    path.write_text(SHEBANG_INSTALL.format(python=python, root=str(ROOT)))
    path.chmod(0o755)
    return path


class Writer:
    def __init__(self):
        self.sequence = 0

    def line(self, event, *, sequence=None, ahead_ns=0, **fields):
        message = dict(fields, event=event, seq=self.sequence if sequence is None else sequence,
                       monotonic_ns=time.monotonic_ns() + ahead_ns, realtime_ns=time.time_ns())
        self.sequence += 1
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != "--watch" or argv[1] != "--seconds" or not argv[2].isdigit():
        return 64
    pid_file = os.environ.get("FAKE_PRESENCE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    flags, steps = {}, []
    for item in filter(None, os.environ.get("FAKE_PRESENCE_SCENARIO", "").split(",")):
        head, _, rest = item.partition(":")
        if head.isdigit():
            steps.append((int(head) * 1_000_000, rest))
        else:
            name, _, value = item.partition("=")
            flags[name] = value
    stopping = []
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, (lambda *_: None) if "deaf" in flags else (lambda *_: stopping.append(1)))
    writer = Writer()
    if "error" in flags:
        code = os.environ.get("FAKE_PRESENCE_INIT_CODE")
        writer.line("error", reason=flags["error"], **({} if code is None else {"init_error_code": int(code)}))
        return 1
    if "noready" in flags:
        while not stopping:
            time.sleep(0.02)
        return 0
    table = presence.signal_table()
    if "table" in flags:
        table = [dict(entry, ends_run=True, end_reason="headset_removed") if entry["signal"] == "proximity_off"
                 else entry for entry in table]
    writer.line("ready", **dict(presence.READY_FACTS, camera_data_read="camera" in flags), tool="fake",
                interfaces_requested=["IVRSystem_026"], signals=table, pid=os.getpid(), max_seconds=int(argv[2]),
                hmd_activity=flags.get("activity", "user_interaction"), hmd_has_proximity_sensor=True)
    origin = time.monotonic_ns()
    deadline = origin + int(argv[2]) * 1_000_000_000
    next_heartbeat = origin + HEARTBEAT_NS
    silent, trickle_at = False, None
    exit_code = int(flags.get("exit", 0))
    while True:
        now = time.monotonic_ns()
        if stopping:
            writer.line("end", reason="stop_signal")
            return exit_code
        if now >= deadline:
            writer.line("end", reason="max_seconds")
            return exit_code
        while steps and now - origin >= steps[0][0]:
            action = steps.pop(0)[1]
            if action in presence.SIGNALS:
                reason = presence.SIGNALS[action]
                writer.line("presence", signal=action, ends_run=reason is not None, end_reason=reason,
                            hmd_activity="standby" if action == "enter_standby_event" else "user_interaction",
                            event_age_ms=None if action == "hmd_activity_changed" else 4)
            elif action in ("nokey", "listsignal", "hugeage"):  # Valid JSON, not a valid presence line.
                fields = {"signal": "enter_standby_event", "ends_run": True, "end_reason": "standby",
                          "hmd_activity": "standby", "event_age_ms": 4}
                if action == "nokey":
                    del fields["event_age_ms"]
                elif action == "listsignal":
                    fields["signal"] = ["enter_standby_event"]
                else:
                    fields["event_age_ms"] = 10**30
                writer.line("presence", **fields)
            elif action.startswith("flap="):  # Many activity changes at once, as a level that flaps while worn.
                for index in range(int(action[5:])):
                    writer.line("presence", signal="hmd_activity_changed", ends_run=False, end_reason=None,
                                hmd_activity="idle" if index % 2 else "user_interaction", event_age_ms=None)
            elif action == "die":
                os._exit(3)
            elif action == "silent":
                silent = True
            elif action == "trickle":
                silent, trickle_at = True, now
            elif action == "garbage":
                sys.stdout.write("Unable to read VR Path Registry\n")
                sys.stdout.flush()
            elif action == "skip":
                writer.line("heartbeat", sequence=writer.sequence + 5)
            elif action == "future":
                writer.line("heartbeat", ahead_ns=10_000_000_000)
            elif action.startswith("end="):
                writer.line("end", reason=action[4:])
                return exit_code
        if not silent and now >= next_heartbeat:
            writer.line("heartbeat")
            next_heartbeat = now + HEARTBEAT_NS
        if trickle_at is not None and now >= trickle_at:
            sys.stdout.write(" ")
            sys.stdout.flush()
            trickle_at = now + 30_000_000
        time.sleep(0.005)


if __name__ == "__main__":
    sys.exit(main())
