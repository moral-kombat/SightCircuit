"""A stand-in for ``sightcircuit-presence-watcher``, for ``camera_hud --validate`` only: never SteamVR.

The presence watcher joins a running SteamVR, which a cloud runner and a
validation run do not have. The validation still has to drive the displayed
path with a watch armed and with a watch that ends the run, so this module
is the watcher's protocol (``presence-watch/1``) spoken by a process that
asks nothing of anyone: a ``ready`` line, a heartbeat every 100 ms, at most
one scripted ``enter_standby_event``, and ``end`` on SIGTERM, SIGINT or its
bound. The standby is scripted by a delay after ``ready`` or, for a run that
must not depend on its host's speed, by a trigger: a file the run itself
creates when it has come far enough, looked for once per heartbeat. Its ``ready`` names its tool ``stand_in``. ``install`` writes the
executable into a directory outside the checkout; no device mode ever
launches it, because a device run launches only the watcher the checkpoint
manifest pins, at that path and with that digest.
"""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import presence

HEARTBEAT_SECONDS = 0.1
NAME = "sightcircuit-presence-watcher-standin"
LAUNCHER = ("#!{python}\nimport sys\nsys.path.insert(0, {root!r})\n"
            "from sightcircuit.camera.presence_standin import main\n"
            "sys.exit(main(standby_after_ms={standby!r}, standby_trigger={trigger!r}))\n")


def install(directory, *, standby_after_ms=None, standby_trigger=None, python=sys.executable):
    """Write the executable stand-in into ``directory`` and return its path.

    With ``standby_after_ms`` it writes SteamVR's standby signal that long
    after its ``ready``. With ``standby_trigger``, a path, it writes the
    signal at the first heartbeat after a file exists there, so the caller
    decides when from its own run and no delay is guessed. Without either
    it only keeps the watch armed.
    """
    if standby_after_ms is not None and standby_trigger is not None:
        raise ValueError("presence_standin_one_standby_script")
    scripted = standby_after_ms is not None or standby_trigger is not None
    path = Path(directory) / (f"{NAME}-standby" if scripted else NAME)
    path.write_text(LAUNCHER.format(python=python, root=str(Path(__file__).resolve().parents[2]),
                                    standby=standby_after_ms,
                                    trigger=None if standby_trigger is None else str(standby_trigger)))
    path.chmod(0o755)
    return path


def main(argv=None, standby_after_ms=None, standby_trigger=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != "--watch" or argv[1] != "--seconds" or not argv[2].isdigit():
        return 64
    stop, sequence = threading.Event(), [0]
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())

    def line(event, **fields):
        message = dict(fields, event=event, seq=sequence[0], monotonic_ns=time.monotonic_ns(), realtime_ns=time.time_ns())
        sequence[0] += 1
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()
    line("ready", **presence.READY_FACTS, tool="stand_in", interfaces_requested=["IVRSystem_026"],
         signals=presence.signal_table(), pid=os.getpid(), max_seconds=int(argv[2]), hmd_activity="user_interaction",
         hmd_has_proximity_sensor=None)
    origin = time.monotonic()
    deadline = origin + int(argv[2])
    standby_at = None if standby_after_ms is None else origin + standby_after_ms / 1000
    while True:
        now = time.monotonic()
        if standby_trigger is not None and os.path.exists(standby_trigger):  # Once per heartbeat: no loop of its own.
            standby_trigger, standby_at = None, now
        if standby_at is not None and now >= standby_at:
            standby_at = None
            line("presence", signal="enter_standby_event", ends_run=True, end_reason="standby", hmd_activity="standby",
                 event_age_ms=0)
        if now >= deadline:
            line("end", reason="max_seconds")
            return 0
        wake = min(HEARTBEAT_SECONDS, deadline - now, *(() if standby_at is None else (max(0.0, standby_at - now),)))
        if stop.wait(wake):  # One timed wait per line: the heartbeat's period, or sooner for the scripted signal.
            line("end", reason="stop_signal")
            return 0
        if standby_at is None or time.monotonic() < standby_at:
            line("heartbeat")
