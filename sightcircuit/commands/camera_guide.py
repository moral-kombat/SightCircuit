"""A guided window for one bounded camera recognition run: no keyboard, no terminal bell, no counting of seconds.

The sixteenth command (owner request 2026-11-19: "a program interface I can
work with instead of relying on the bells keyboard and time", as "a window
inside the headset", with "tap to start" accepted). It runs what
``camera_recognize`` runs, ``recognize()`` with that command's own bounds (at
most 30 seconds and 300 frames, one camera, 5 frames a second by default), and
puts a Qt window in front of it that the wearer works with the controller's
pointer on the Frame's desktop. It adds no capability and raises no bound.

The screens, one thing on each. (1) The starting state: what the wearer sees
now, "Colour, stable" or "Something else"; the second answer ends there with
nothing launched. (2) What will happen: the script's steps, before anything
starts. (3) The notice: the helper's export notice as the contract has it,
without its closing instruction to type the answer
(``confirmation.export_notice_body``), the run's bounds, the target, where the
report will be saved, and Start and Cancel. Start can be clicked only once the
notice has been up for a few seconds, counted down on the screen. A click on
Start is the informed start of this one run; the report records
``confirmation`` ``operator_window``. It is not an approval and opens no gate.
(4) The guided run: one instruction at a time with a drawn picture, a progress
bar while a step is recorded, the time left, and Stop. (5) After a run in
which the wearer clicked that the headset was coming off, whether it did and
is back on; then the passthrough watch, 30 seconds with a countdown, answered
"Passthrough unchanged" or "Something changed", the second asked once more to
confirm. (6) The result in plain words, the report's path and Close.

A pointer interface that cannot be worked by accident. No key starts,
answers or stops anything. Every button is dead for a guard interval after
it appears, so a double click or a click already on its way lands on
nothing; consecutive screens put their buttons in different places; and the
two consequential waits (before Start, before Close) are shown as counts.

Nothing camera-derived is shown while the reader is active. The run is in a
second process of this command (``--worker``): it calls ``recognize()`` on
its main thread, as the supervision primitive requires, and launches the
helper as ``camera_recognize`` does (the named binary, ``--parent-confirmed``
after the click, the same owned session and deadline). The worker runs only
for the window that launched it: it must be handed a token on a descriptor
it inherits from that window, by its own parent, before it reads anything
else; run by hand it refuses and launches nothing. That is a procedural
control against mistakes, as the scope record is, not a cryptographic one.
The window's process loads no detector and reads no frame. During the run
the worker tells it one thing, that the helper's ``ready`` was accepted,
with no value attached; what the run screen shows is
``sightcircuit.camera.guide.Guide.view``: the script, the click and the
clock. The report reaches the window only after the helper has ended, and
when the report does not confirm a clean shutdown the window says only that.
No image is ever shown, saved or written: the pictures are shapes drawn by
this file. The window never opens the overlay bridge or SteamVR.

Three scripts (``--script``), each shown to the wearer before Start.
``removal`` needs one click in the run: the target is held at about half a
metre when Start is clicked (mark ``near`` as soon as the reader is ready,
about four seconds), then moved to about a metre when the screen says so
(``far``, made when the instruction appears: about two seconds to move and
four recorded), then the click made just before the headset comes off
(``remove``); the run ends by itself when SteamVR reports standby (with
``--presence-watcher``: ``presence_ended``, see ``camera/presence.py``), when
the helper reports ``headset_removed`` or ``standby``, or on its bound. ``positions``: centre,
left, right, up, down for about four seconds each, then out of view, with no
click after Start. ``scan`` (item L14): one instruction for the whole run,
the target held still straight ahead at about half a metre, while each frame
is seen through the next entry of a fixed schedule of regions and rotations
of the camera image and every ``--scan-detectors`` detector looks at it; the
report's ``scan`` block says, in counts and boxes over the whole frame, where
and on which axis of the image the target was found (a turn and its opposite
are hard to tell apart on a bottle; the positions script settles which way
up). A detector that fails part way is dropped and named, and the other
carries on; a scan's summary has no coverage, which would read as G7's. ``--region`` and ``--rotation`` give
the other two scripts one fixed region (``sightcircuit.camera.region``).
Marks are ``marks.VOCABULARY``'s, recorded with source
``window``; the summaries are ``marks.summarize``'s. Because ``remove`` is
clicked before the headset comes off, each removal entry also has the time
from the run's last frame to the helper's end record; the two limits the
entry is compared with are ``--late-frame-limit-ms`` and
``--removal-end-limit-ms`` (the current values by default). The window shows
the measured values and decides nothing.

The report is ``camera_recognize``'s with a ``guide`` block: the script, the
steps with their marks, the steps not reached, whether the watch was held,
and the wearer's observations from closed choices only (no free text
anywhere). ``--report`` is required, is checked before the window opens (a
new ``.json`` path in an existing directory that can be written to) and is
never overwritten. Stop, closing the window or a signal (SIGINT, SIGTERM or
SIGHUP) ends the helper and still leaves the report. One launch is one run:
a second run is a second launch, a second notice and a second Start.

``--hold-start PATH`` (item L18, after E103, where the wearer clicked Start
before the operator had a companion process running) keeps Start off until
the operator releases it with ``touch PATH``. The path is checked before the
window opens (``check_hold_path``): nothing may be there yet, and its
directory must be a real directory that only the user can write to, or the
report's own. The window looks for the file four times a second from its own
timer (``os.lstat``; the file is never opened), says so in the notice
screen's one line about Start (no line is added, so the export notice keeps
its room), then that the operator has said go, and a release
only enables the button: the wearer's click still starts the run, and the
button is dead for the guard interval after the release as after any
appearance. A hold nobody releases within ten minutes of the window opening
ends as an unstarted run does (``start_hold_not_released``), with nothing
launched. The ``guide`` block's ``start_hold`` has the release time and the
delay to the Start click. Without the option nothing here applies and the
window and its report are what they were.

Camera Data and gates are as ``camera_recognize`` states them: the report
holds counts, closed labels, boxes, timings and format metadata, never a
pixel; every gate stays closed and none is lifted (``gates.claims()`` is in
the report). A run on the Frame is a device step that needs the owner's
approval; the Frame manager launches the window for the wearer after it. The
window is a procedure, not an approval. ``--validate`` runs the whole window
flow offscreen against the helper's fake build with a scripted detector.
"""
import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import secrets
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

if not __package__:  # Run as a file: import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sightcircuit import gates
from sightcircuit.camera import contract, marks, presence
from sightcircuit.camera import guide as guidance
from sightcircuit.camera import region as regions
from sightcircuit.camera.confirmation import OPERATOR_WINDOW, export_notice_body
from sightcircuit.commands import camera_recognize
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, POC_THRESHOLD, class_names
from sightcircuit.errors import SightCircuitError
from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.runtime import guard
from sightcircuit.sources import camera_helper

MAX_SECONDS, MAX_FRAMES, MAX_FPS = camera_recognize.MAX_SECONDS, camera_recognize.MAX_FRAMES, camera_recognize.MAX_FPS
DEFAULT_FPS = camera_recognize.DEFAULT_FPS
DEFAULT_TARGET = "bottle"
LEFT_CAMERA = camera_helper.LEFT_CAMERA
TICK_MS = 50  # The window's clock: how often the run screen and the countdowns are brought up to date.
GUARD_SECONDS = guidance.GUARD_MS / 1000  # A button is dead for this long after it appears.
START_DWELL_SECONDS = 5.0  # The notice is on the screen this long before Start can be clicked.
CLOSE_DWELL_SECONDS = 3.0  # The result is on the screen this long before Close can be clicked.
# After Stop, or past the run's own deadline, how long the window waits for the worker's report before it sends the
# worker SIGTERM, and then before it gives up and reports that. The worker is never killed: its helper is its own.
WORKER_PATIENCE_SECONDS = 25.0
RUN_MARGIN_SECONDS = camera_helper.LAUNCH_MARGIN_SECONDS + 20  # Beyond max_seconds: the launch, the detector, the joins.
# From the Start click, when a helper whose worker died is gone by its own bound and the reader's watchdog.
HELPER_GONE_MARGIN_SECONDS = camera_helper.LAUNCH_MARGIN_SECONDS
EARLY_STOP_GRACE_SECONDS = 3.0  # A stop during the launch waits this long for the helper's ready, so it ends cleanly.
WORKER_FLAG, WORKER_FD_FLAG = "--worker", "--worker-fd"
HANDSHAKE_SECONDS = 5.0
SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))
FAKE_HELPER = "artifacts/camera-helper-fake/sightcircuit-camera-helper-fake"  # scripts/build_camera_helper.sh --fake
DESCRIBE_TIMEOUT_SECONDS = 10
VALIDATION_KIND = "camera_guide_validation"
MIN_UNIT = 10  # The smallest body text, in pixels.
MIN_SIZE = (640, 480)
START_QUESTION = "What do you see in the headset right now?"
START_NOTE = ("Clicking Start starts this one run. It is a start, not an approval: the run was approved before this "
              "window was opened.")
TEXT_START, TEXT_CANCEL, TEXT_STOP, TEXT_CLOSE, TEXT_NEXT = "Start", "Cancel", "Stop", "Close", "Next"
TEXT_COLOUR_STABLE, TEXT_SOMETHING_ELSE = "Colour, stable", "Something else"
TEXT_UNCHANGED, TEXT_CHANGED = "Passthrough unchanged", "Something changed"
TEXT_CONFIRM, TEXT_GO_BACK = "Confirm", "Go back"
TEXT_BACK_ON, TEXT_NOT_REMOVED = "Yes, off and back on", "No, I kept it on"
BACK_QUESTION = "Did you take the headset off? If it is off, put it back on first"
WATCH_QUESTION = "Look around. Is the view through the headset the same as before the run?"
CONFIRM_QUESTION = "You said the passthrough picture changed."
INTERRUPTED_TEXT = "The run was interrupted. Please wait until the camera reader is certain to have ended"
PHASES = (PHASE_STATE, PHASE_PLAN, PHASE_NOTICE, PHASE_RUN, PHASE_INTERRUPTED, PHASE_BACK, PHASE_WATCH,
          PHASE_CONFIRM, PHASE_RESULT) = (
    "starting_state", "plan", "notice", "run", "interrupted", "headset_back", "watch", "confirm_changed", "result")
BANDS = BAND_LOWER, BAND_UPPER = ("lower", "upper")  # Where a screen's buttons sit; consecutive screens alternate.


@dataclass
class Config:
    """One launch of the window: the run it may start and where its report goes."""
    script: str
    helper: str
    report_path: Path
    limits: dict
    camera: int = LEFT_CAMERA
    target: str = DEFAULT_TARGET
    detector: str = DEFAULT_DETECTOR
    threshold: float = POC_THRESHOLD
    helper_args: tuple = ()
    late_frame_limit_ms: int = marks.LATE_FRAME_LIMIT_MS
    removal_end_limit_ms: int = marks.REMOVAL_END_LIMIT_MS
    # Shorter only in the checks, which run the whole flow in seconds; the command line sets none of these.
    scale: float = 1.0
    watch_seconds: float = guidance.WATCH_SECONDS
    guard_seconds: float = GUARD_SECONDS
    start_dwell_seconds: float = START_DWELL_SECONDS
    close_dwell_seconds: float = CLOSE_DWELL_SECONDS
    validation: bool = False  # The worker uses the scripted detector; never set from the command line of a run.
    presence_watcher: str = None  # The sightcircuit-presence-watcher binary, or None for a run without the watch.
    worker_env: dict = field(default_factory=dict)
    # Item L14. ``region`` is ``(x0, y0, x1, y1)`` as fractions of the frame, or None for the whole frame; the scan
    # script ignores both and takes its own schedule, with every detector of ``scan_detectors`` on each frame.
    region: tuple | None = None
    rotation: int = 0
    scan_detectors: tuple = ()
    # Item L18. The release file's path, or None for a window whose Start is not held; the worker is not told.
    hold_start: Path | None = None
    hold_timeout_seconds: float = guidance.HOLD_TIMEOUT_SECONDS  # Shorter only in the checks.

    def spec(self):
        """What the worker is told: the run's arguments, nothing else."""
        return {"helper": str(self.helper), "limits": dict(self.limits), "camera": self.camera, "target": self.target,
                "detector": self.detector, "threshold": self.threshold, "helper_args": list(self.helper_args),
                "late_frame_limit_ms": self.late_frame_limit_ms, "removal_end_limit_ms": self.removal_end_limit_ms,
                "validation": bool(self.validation), "region": None if self.region is None else list(self.region),
                "rotation": self.rotation,
                "scan_detectors": list(self.scan_detectors) if self.script == guidance.SCRIPT_SCAN else None,
                "presence_watcher": None if self.presence_watcher is None else str(self.presence_watcher)}


def check_report_path(path):
    """The report's path as it will be written, or ``ValueError``.

    A new ``.json`` file in an existing directory in which a file can be
    made: a probe file is created there and removed, so a run is never held
    for a report that could not be saved.
    """
    try:
        target = Path(path).expanduser()
    except RuntimeError:  # A ``~name`` the system does not know.
        raise ValueError("--report needs a path whose home directory exists") from None
    if target.suffix.lower() != ".json" or not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError("--report needs a new .json path in an existing directory")
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".camera-guide-probe-"):
            pass
    except OSError:
        raise ValueError("--report needs a directory this command can write to") from None
    return target


def check_hold_path(path, report_path):
    """The start hold's release file as the window will look for it, or ``ValueError``. Nothing is created.

    The path is taken as written (made absolute, no link followed) and may
    not have a ``..`` in it: making ``link/..`` absolute would drop the
    link, and the window would watch one place while the operator's
    ``touch`` made a file in another. Nothing may be at it yet, a dangling
    link included, so a file left by an earlier run can never release this
    one. Its directory must exist and be a real directory, not a link to
    one; and it must be either the report's own directory, which
    ``check_report_path`` has just found writable, or a directory the user
    owns and can write to with no write permission for group or others, so
    that nobody else can say go. It may not be the report's own file, by
    whatever name: the two are compared by their resolved directories and
    their names. Only the path and its directory are examined otherwise: a
    link further up the path is the operator's to know about.
    """
    try:
        written, report = Path(path).expanduser(), Path(report_path).expanduser()
    except RuntimeError:  # A ``~name`` the system does not know.
        raise ValueError("--hold-start needs a path whose home directory exists") from None
    if ".." in written.parts:
        raise ValueError("--hold-start needs a path without '..' in it")
    target = Path(os.path.abspath(written))
    if (os.path.realpath(target.parent), target.name) == (os.path.realpath(report.parent), report.name):
        raise ValueError("--hold-start needs a path of its own, not the report's")
    try:
        directory = os.lstat(target.parent)
    except OSError:
        raise ValueError("--hold-start needs a path in an existing directory") from None
    if not stat.S_ISDIR(directory.st_mode):
        raise ValueError("--hold-start needs a path in a real directory, not a link to one")
    if os.path.lexists(target):
        raise ValueError("--hold-start needs a path where nothing exists yet; remove what an earlier run left there")
    mode = stat.S_IMODE(directory.st_mode)
    private = directory.st_uid == os.geteuid() and mode & 0o300 == 0o300 and not mode & 0o022
    try:
        beside_report = os.path.samefile(target.parent, report.parent)
    except OSError:
        beside_report = False
    if not private and not (beside_report and os.access(target.parent, os.W_OK | os.X_OK)):
        raise ValueError("--hold-start needs a directory that only you can write to, or the report's directory")
    return target


def hold_released(path):
    """Whether the release file is there: a regular file at the path itself. Its contents are never read."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def run_limits(script, seconds=None, max_frames=None, fps=DEFAULT_FPS):
    """``{fps, max_seconds, max_frames}`` for a script, inside ``camera_recognize``'s bounds, else ``ValueError``."""
    seconds = guidance.DEFAULT_SECONDS[script] if seconds is None else seconds
    if type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS or type(fps) is not int or not 1 <= fps <= MAX_FPS:
        raise ValueError(f"a run is at most {MAX_SECONDS} seconds at 1 to {MAX_FPS} frames a second")
    max_frames = min(MAX_FRAMES, seconds * fps) if max_frames is None else max_frames
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise ValueError(f"a run is at most {MAX_FRAMES} frames")
    return {"fps": fps, "max_seconds": seconds, "max_frames": max_frames}


def unstarted_report(config, reason):
    """The report of a launch that started no run: ``camera_recognize``'s shape, nothing launched, no model loaded."""
    report = camera_recognize.new_report("recognize")
    report.update(confirmation=None, seconds_requested=config.limits["max_seconds"],
                  max_frames_requested=config.limits["max_frames"], fps_requested=config.limits["fps"],
                  cameras_requested=[config.camera], status="error", reason=reason, phase="closed")
    if config.presence_watcher is not None:  # Named and never launched: requested, not armed, nothing to clean up.
        report["presence_watch"] = presence.unstarted_report()
    return report


def interrupted_report(config, reason):
    """The report the window writes for a started run whose worker gave none: the helper's end is not confirmed.

    Nor is the presence watcher's, when one was named: the block says its state is not known.
    """
    report = dict(unstarted_report(config, reason), confirmation=OPERATOR_WINDOW, cleanup_ok=False)
    if config.presence_watcher is not None:
        report["presence_watch"] = presence.unknown_report()
    return report


def arming_seconds(config):
    """What arming the presence watch may add before the helper is launched: nothing without a watcher."""
    return 0.0 if config.presence_watcher is None else presence.READY_WAIT_SECONDS + presence.STOP_WAIT_SECONDS


# ---- the worker: the run itself, in its own process ---------------------------------------------------------------

class ValidationDetector:
    """The checks' detector: the target where the latest mark says it is held; never looks at a pixel."""
    name, runtime = "camera-guide-validation", "none"
    CENTRES = {"near": (0.5, 0.5), "far": (0.5, 0.5), "centre": (0.5, 0.5), "left": (0.2, 0.5), "right": (0.8, 0.5),
               "up": (0.5, 0.2), "down": (0.5, 0.8)}

    def __init__(self, target, latest_label):
        self.classes, self.target, self.latest_label = (target,), target, latest_label

    def detect(self, bgr, threshold):
        centre = self.CENTRES.get(self.latest_label())
        if centre is None:
            return DetectionResult((), 1.0)
        x, y = round(centre[0] * bgr.shape[1]), round(centre[1] * bgr.shape[0])
        return DetectionResult((Detection(self.target, 0.9, (x - 2, y - 2, x + 2, y + 2)),), 1.0)


class _Discard:
    """Where the mark session's announcements go in the worker: nowhere (the window is the wearer's interface)."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _StoppedBeforeLaunch(Exception):
    """The stop came before the helper was launched: nothing is launched."""


def worker_handshake(descriptor):
    """The token the launching window wrote for this worker, or None: the worker then runs nothing.

    The descriptor must be a pipe this process inherited, carrying one JSON
    line ``{"token", "window_pid"}`` and then its end, where ``window_pid``
    is this process's parent. A worker started by hand has no such
    descriptor. Read before anything is read from stdin.
    """
    try:
        if type(descriptor) is not int or descriptor < 3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            return None
        data, deadline = b"", time.monotonic() + HANDSHAKE_SECONDS
        while len(data) < 4096:
            if not select.select([descriptor], [], [], max(0.0, deadline - time.monotonic()))[0]:
                return None  # The writer never finished: not a window's handover.
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            data += chunk
        os.close(descriptor)
        record = json.loads(data)
        token = record["token"]
        if record["window_pid"] != os.getppid() or type(token) is not str or len(token) != 32:
            return None
        int(token, 16)
        return token
    except (OSError, ValueError, KeyError, TypeError):
        return None


def worker_main(descriptor, stdin=None, stdout=None):
    """``--worker``: one ``recognize()`` run for the window that launched this process.

    Refused, with nothing read from stdin and nothing launched, unless
    ``worker_handshake`` finds the window's token; every line written
    afterwards carries it. The first line on stdin is the run
    (``Config.spec``); later lines are ``{"mark": LABEL, "monotonic_ns": N}``
    (a click or a step, recorded at its time) or ``{"stop": true}``; the end
    of stdin is a stop too, so a window that died takes its run with it. On
    stdout: ``{"event": "ready"}`` when the helper's ``ready`` is accepted,
    with nothing else in it, and after ``recognize()`` has returned (the
    helper has ended) ``{"event": "report", "report": ...}``. Nothing derived
    from a frame is written before that last line. A stop that arrives
    before the helper is launched launches nothing; one that arrives during
    the launch waits up to ``EARLY_STOP_GRACE_SECONDS`` for the helper's
    ``ready``, so that the helper is stopped once it can stop cleanly.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    lock = threading.Lock()
    report = camera_recognize.new_report("recognize")
    report.update(confirmation=None, status="error", reason="guide_worker_not_launched_by_window", phase="closed")
    token = worker_handshake(descriptor)

    def emit(record):
        with lock:
            stdout.write(json.dumps(dict(record, token=token)) + "\n")
            stdout.flush()
    if token is None:  # Run by hand: no window, no notice, no click. Nothing is read and nothing is launched.
        emit({"event": "report", "report": report})
        return 1
    report["reason"] = "guide_worker_arguments_invalid"
    try:
        spec = json.loads(stdin.readline())
        limits = camera_helper.check_limits(spec["limits"])
        if limits["max_seconds"] > MAX_SECONDS or limits["max_frames"] > MAX_FRAMES:
            raise ValueError("beyond camera_recognize's bounds")
        cameras = camera_helper.check_cameras((spec["camera"],))
        target, helper = spec["target"], spec["helper"]
        watcher = spec.get("presence_watcher")
        if watcher is not None and not isinstance(watcher, str):
            raise ValueError("the presence watcher is a path")
        if watcher is not None:  # Whatever this process reports from here on says the watcher was named.
            report["presence_watch"] = presence.unstarted_report()
        region, scan_detectors = None, spec.get("scan_detectors")
        if scan_detectors is not None:  # The scan takes its own schedule: a region with it is a malformed run.
            if (spec.get("region") is not None or spec.get("rotation", 0) != 0 or type(scan_detectors) is not list
                    or spec["detector"] not in scan_detectors):
                raise ValueError("scan arguments invalid")
        elif spec.get("region") is not None or spec.get("rotation", 0) != 0:
            fractions = regions.WHOLE if spec.get("region") is None else spec["region"]
            if len(fractions) != 4 or any(type(value) not in (int, float) for value in fractions):
                raise ValueError("region invalid")
            region = regions.Region(*fractions, rotation=spec.get("rotation", 0))
    except (ValueError, KeyError, TypeError):
        emit({"event": "report", "report": report})
        return 1
    stop, stop_requested, ready_seen = threading.Event(), threading.Event(), threading.Event()
    state = {"session": None, "label": None, "marks_refused": 0}

    def listen():
        try:
            for line in stdin:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("stop"):
                    break
                session = state["session"]
                try:
                    if session is None or not session.mark(message.get("mark"), message.get("monotonic_ns")):
                        state["marks_refused"] += 1
                    else:
                        state["label"] = message["mark"]
                except ValueError:
                    state["marks_refused"] += 1
        finally:
            stop_requested.set()  # A stop line, or the window is gone.

    def relay():
        """Pass a requested stop on to the helper once it can honour it: after its ready, or the grace."""
        stop_requested.wait()
        ready_seen.wait(EARLY_STOP_GRACE_SECONDS)
        stop.set()

    def on_session(session):
        if stop_requested.is_set():
            raise _StoppedBeforeLaunch()
        state["session"] = session

    def on_ready():
        ready_seen.set()
        emit({"event": "ready"})
    for target_function, name in ((listen, "input"), (relay, "stop")):
        threading.Thread(target=target_function, name=f"camera-guide-worker-{name}", daemon=True).start()
    factory = scan_factory = None
    if spec.get("validation") is True:
        def factory():
            return ValidationDetector(target, lambda: state["label"])

        def scan_factory(_name):
            return factory()
    with guard.sigterm_cancels():
        try:
            report = camera_recognize.recognize(
                helper, limits, cameras, threshold=spec["threshold"], detector=spec["detector"],
                detector_factory=factory, extra_args=tuple(spec["helper_args"]), stop=stop, confirmed=True,
                confirmation=OPERATOR_WINDOW, target_label=target, terminal=_Discard(), on_session=on_session,
                on_ready=on_ready, late_frame_limit_ms=spec["late_frame_limit_ms"],
                removal_end_limit_ms=spec["removal_end_limit_ms"], region=region,
                scan_detectors=None if scan_detectors is None else tuple(scan_detectors),
                scan_detector_factory=scan_factory, presence_watcher=watcher,
                abort=stop_requested)  # A Stop click while the watch arms is honoured at once: nothing is launched.
        except _StoppedBeforeLaunch:
            report.update(confirmation=OPERATOR_WINDOW, status="stopped", reason="stopped_before_launch", cleanup_ok=True)
        except guard.CANCELLATION as error:
            report.update(confirmation=OPERATOR_WINDOW)
            guard.cancelled(report, error)
            presence.apply_failure(report, error, watcher is not None)
        except Exception as error:  # Whatever went wrong, the window gets a report and the wearer a result screen.
            report.update(confirmation=OPERATOR_WINDOW, status="error", reason="guide_worker_failed",
                          error=f"{type(error).__name__}: {error}"[:1024])
            presence.apply_failure(report, error, watcher is not None)
    report["window_marks_refused"] = state["marks_refused"]
    emit({"event": "report", "report": report})
    return 0


class Worker:
    """The window's handle on the run's process: start it, send it marks and a stop, receive its two events.

    ``start`` hands the worker a fresh token on a pipe it inherits (see
    ``worker_handshake``). Events reach ``on_event`` on the reader thread as
    ``("ready", monotonic_ns)``, the time the line was read, and
    ``("report", report)``; a worker that ends without a report gives
    ``("report", None)``. Only those two are passed on, and only when they
    carry the token: any other line the worker writes is counted
    (``unexpected_lines``) and dropped, so nothing else can reach the window.
    """

    def __init__(self, spec, on_event, *, env=None, command=None, token=None):
        self.spec, self.on_event = spec, on_event
        self.env = dict(os.environ, **(env or {}))
        self.command = [sys.executable, str(Path(__file__).resolve()), WORKER_FLAG] if command is None else command
        self.token = secrets.token_hex(16) if token is None else token
        self.process = self.thread = None
        self.unexpected_lines = 0
        self.lock = threading.Lock()

    def start(self):
        read_fd, write_fd = os.pipe()
        try:
            self.process = subprocess.Popen([*self.command, WORKER_FD_FLAG, str(read_fd)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, text=True, bufsize=1, env=self.env,
                                            pass_fds=(read_fd,))
        except OSError:
            os.close(write_fd)
            raise
        finally:
            os.close(read_fd)
        os.write(write_fd, json.dumps({"token": self.token, "window_pid": os.getpid()}).encode() + b"\n")
        os.close(write_fd)
        self._send(self.spec)
        self.thread = threading.Thread(target=self._read, name="camera-guide-worker-output", daemon=True)
        self.thread.start()

    def _send(self, record):
        with self.lock:
            try:
                self.process.stdin.write(json.dumps(record) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass  # The worker has gone; its reader thread reports that.

    def _read(self):
        delivered = False
        for line in self.process.stdout:
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            mine = isinstance(record, dict) and record.get("token") == self.token
            event = record.get("event") if mine else None
            if event == "ready" and not delivered:
                self.on_event(("ready", time.monotonic_ns()))
            elif event == "report" and not delivered and isinstance(record.get("report"), dict):
                delivered = True
                self.on_event(("report", record["report"]))
            else:
                self.unexpected_lines += 1
        self.process.wait()
        if not delivered:
            self.on_event(("report", None))

    def mark(self, label, monotonic_ns):
        self._send({"mark": label, "monotonic_ns": monotonic_ns})

    def stop(self):
        self._send({"stop": True})

    def terminate(self):
        try:
            self.process.send_signal(signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def close(self):
        """After the report: close the pipes and collect the process."""
        if self.process is None:
            return
        with self.lock:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
        if self.thread is not None:
            self.thread.join(camera_helper.JOIN_SECONDS)
            if not self.thread.is_alive():
                self.process.stdout.close()


# ---- the window ----------------------------------------------------------------------------------------------------

def unit_for(width, height):
    """The body text's pixel size for a window: large for a headset, and small enough that button texts stay whole."""
    return max(MIN_UNIT, int(min(height / 30, width / 46)))


def create_window(config, *, worker_factory=None, clock=time.monotonic_ns, size=None):
    """The guide's window for ``config``. ``worker_factory(spec, on_event)`` makes the run's process handle."""
    from PySide6 import QtCore, QtGui, QtWidgets
    Qt = QtCore.Qt
    if worker_factory is None:
        def worker_factory(spec, on_event):
            return Worker(spec, on_event, env=config.worker_env)

    class Picture(QtWidgets.QWidget):
        """A drawn picture for a step. Shapes only: no image is loaded, received or kept."""

        def __init__(self):
            super().__init__()
            self.kind, self.motion, self.unit = "wait", 0.0, MIN_UNIT

        def show_kind(self, kind, motion=0.0):
            if (kind, motion) != (self.kind, self.motion):
                self.kind, self.motion = kind, motion
                self.update()

        def paintEvent(self, _event):
            unit = self.unit
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            side = min(self.width() * 0.6, self.height() * 0.95)
            box = QtCore.QRectF((self.width() - side * 1.6) / 2, (self.height() - side) / 2, side * 1.6, side)
            white, accent, dim = QtGui.QColor("#f4f6f8"), QtGui.QColor("#ffd23f"), QtGui.QColor("#5b6b7c")
            painter.setPen(QtGui.QPen(white, max(3, unit // 8)))
            centre = box.center()
            radius = side * 0.11
            if self.kind in ("centre", "left", "right", "up", "down", "out_of_view"):
                painter.drawRoundedRect(box, unit / 2, unit / 2)  # The wearer's view.
                dx = {"left": -0.32, "right": 0.32}.get(self.kind, 0) * box.width()
                dy = {"up": -0.32, "down": 0.32}.get(self.kind, 0) * box.height()
                if self.kind == "out_of_view":
                    painter.setPen(QtGui.QPen(dim, max(3, unit // 8), Qt.PenStyle.DashLine))
                    painter.drawEllipse(centre, radius, radius)
                    painter.drawLine(QtCore.QPointF(centre.x() - radius * 1.6, centre.y() - radius * 1.6),
                                     QtCore.QPointF(centre.x() + radius * 1.6, centre.y() + radius * 1.6))
                else:
                    if dx or dy:  # An arrow from the middle towards the place.
                        painter.setPen(QtGui.QPen(dim, max(3, unit // 6)))
                        painter.drawLine(centre, QtCore.QPointF(centre.x() + dx * 0.6, centre.y() + dy * 0.6))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(accent)
                    painter.drawEllipse(QtCore.QPointF(centre.x() + dx, centre.y() + dy), radius, radius)
            elif self.kind in ("near", "far"):
                head = QtCore.QPointF(box.left() + side * 0.16, centre.y())
                painter.drawEllipse(head, side * 0.16, side * 0.16)  # The wearer, from the side.
                painter.drawRect(QtCore.QRectF(head.x() + side * 0.08, head.y() - side * 0.07, side * 0.12, side * 0.12))
                reach = box.width() * (0.3 if self.kind == "near" else 0.72)  # Half the distance, about half the gap.
                bottle = QtCore.QRectF(head.x() + side * 0.2 + reach, centre.y() - side * 0.2, side * 0.11, side * 0.36)
                painter.setPen(QtGui.QPen(dim, max(3, unit // 8), Qt.PenStyle.DashLine))
                painter.drawLine(QtCore.QPointF(head.x() + side * 0.24, centre.y()),
                                 QtCore.QPointF(bottle.left(), centre.y()))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(bottle, unit / 4, unit / 4)
                painter.setPen(white)
                painter.drawText(QtCore.QRectF(box.left(), box.bottom() - unit * 1.6, box.width(), unit * 1.6),
                                 Qt.AlignmentFlag.AlignCenter,
                                 "about half a metre" if self.kind == "near" else "about a metre")
            elif self.kind == "headset_off":
                painter.drawEllipse(centre, side * 0.22, side * 0.22)
                visor = QtCore.QRectF(centre.x() - side * 0.3, centre.y() - side * 0.5, side * 0.6, side * 0.18)
                painter.setBrush(accent)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(visor, unit / 3, unit / 3)
                painter.setPen(QtGui.QPen(dim, max(3, unit // 6)))
                painter.drawLine(QtCore.QPointF(centre.x(), centre.y() - side * 0.05),
                                 QtCore.QPointF(centre.x(), centre.y() - side * 0.28))
            elif self.kind == "done":
                painter.setPen(QtGui.QPen(accent, max(4, unit // 4)))
                painter.drawPolyline([QtCore.QPointF(centre.x() - side * 0.25, centre.y()),
                                      QtCore.QPointF(centre.x() - side * 0.05, centre.y() + side * 0.2),
                                      QtCore.QPointF(centre.x() + side * 0.3, centre.y() - side * 0.22)])
            else:  # wait: three dots, the bright one moving with the clock.
                painter.setPen(Qt.PenStyle.NoPen)
                lit = int(self.motion * 3) % 3
                for index, offset in enumerate((-1, 0, 1)):
                    painter.setBrush(accent if index == lit else dim)
                    painter.drawEllipse(QtCore.QPointF(centre.x() + offset * side * 0.25, centre.y()),
                                        radius * 0.6, radius * 0.6)
            painter.end()

    def label(text="", name="body", wrap=True, align=Qt.AlignmentFlag.AlignCenter):
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        widget.setWordWrap(wrap)
        widget.setAlignment(align)
        widget.setTextFormat(Qt.TextFormat.PlainText)
        return widget

    class PointerButton(QtWidgets.QPushButton):
        """A button only a pointer clicks: no key starts, answers or stops anything, wherever the focus is."""

        def keyPressEvent(self, event):
            event.ignore()

        def keyReleaseEvent(self, event):
            event.ignore()

    def button(text, name="plain"):
        widget = PointerButton(text)
        widget.setObjectName(name)
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.setAutoDefault(False)
        widget.setEnabled(False)  # Until its screen has been up for the guard interval.
        policy = widget.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # A hidden button keeps its room: nothing jumps under the pointer.
        widget.setSizePolicy(policy)
        return widget

    def side_by_side(*widgets):
        layout = QtWidgets.QHBoxLayout()
        for widget in widgets:
            layout.addWidget(widget)
        return layout

    def scrolling(widget):
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area

    class Page(QtWidgets.QWidget):
        """One screen: its content, then two bands for its buttons, of which it uses one each time it is shown."""

        def __init__(self, rows, buttons):
            super().__init__()
            self.buttons = tuple(buttons)
            self.content = QtWidgets.QVBoxLayout(self)
            for row in rows:
                if isinstance(row, QtWidgets.QLayout):
                    self.content.addLayout(row)
                elif isinstance(row, tuple):
                    self.content.addWidget(row[0], row[1])
                else:
                    self.content.addWidget(row)
            self.row = QtWidgets.QWidget()
            self.row_layout = QtWidgets.QHBoxLayout(self.row)
            self.row_layout.setContentsMargins(0, 0, 0, 0)
            for widget in self.buttons:
                self.row_layout.addWidget(widget)
            self.slots = {}
            for band in (BAND_UPPER, BAND_LOWER):
                slot = QtWidgets.QWidget()
                slot.setObjectName("slot")
                QtWidgets.QVBoxLayout(slot).setContentsMargins(0, 0, 0, 0)
                self.slots[band] = slot
                self.content.addWidget(slot)
            self.band = None

        def place(self, band):
            """Put the buttons in ``band``; the other band stays empty, with the same height."""
            self.band = band
            self.slots[band].layout().addWidget(self.row)

    class Window(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.config = config
            self.setWindowTitle("SightCircuit camera guide")
            self.setMinimumSize(*MIN_SIZE)
            self.unit = None
            self.phase = None
            self.band = BAND_UPPER  # The first screen takes the other one.
            self.page_shown_ns = clock()
            self.guide = None  # The run's script state, once Start is clicked.
            self.worker = None
            self.workers_started = 0
            self.started_ns = None  # The Start click.
            self.report = None  # The worker's report: set only after the helper has ended.
            self.final = None  # The saved report, with the guide block.
            self.report_error = None
            self.ended_by = guidance.ENDED_NOT_STARTED
            self.unstarted_reason = guidance.DECLINED
            self.watch_state = guidance.WATCH_NOT_STARTED
            self.observations = {"starting_state": None, "passthrough_after": None, "headset": None,
                                 "watch_seconds_elapsed": None, "watch_completed": False}
            self.events = queue.Queue()  # From the worker's reader thread; taken on the window's own clock.
            self.close_requested = False
            self.signal_number = None
            self.stop_sent_ns = self.terminate_sent_ns = self.run_deadline_ns = self.watch_started_ns = None
            self.helper_gone_ns = None
            # The start hold (item L18): when the window opened, when it last looked and when it saw the release.
            self.hold_opened_ns = self.page_shown_ns
            self.hold_checked_ns = self.hold_released_ns = None
            self.hold_timed_out = False
            self.run_screen_log = []  # Every distinct content the run screen showed, for the checks.
            self.stack = QtWidgets.QStackedLayout(self)
            self.stack.setContentsMargins(0, 0, 0, 0)
            self.pages = {}
            self._build()
            if size is not None:
                self.resize(*size)
            self.apply_scale()
            self.show_phase(PHASE_STATE)
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(TICK_MS)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        # -- screens --

        def _page(self, phase, rows, buttons):
            self.pages[phase] = Page(rows, buttons)
            self.stack.addWidget(self.pages[phase])

        def _build(self):
            left_top = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            # The starting state.
            self.stable_button, self.other_button = button(TEXT_COLOUR_STABLE, "go"), button(TEXT_SOMETHING_ELSE)
            self.stable_button.clicked.connect(self.guarded(lambda: self.answer_state(guidance.START_COLOUR_STABLE)))
            self.other_button.clicked.connect(self.guarded(lambda: self.answer_state(guidance.START_SOMETHING_ELSE)))
            self._page(PHASE_STATE, [label("Before we start", "title"), (label(START_QUESTION, "big"), 1)],
                       [self.stable_button, self.other_button])
            # What will happen.
            self.plan_label = label("\n\n".join(guidance.plan_lines(config.script, config.target)), "body", align=left_top)
            self.next_button = button(TEXT_NEXT, "go")
            self.next_button.clicked.connect(self.guarded(lambda: self.show_phase(PHASE_NOTICE)))
            self._page(PHASE_PLAN, [label("What will happen", "title"), (scrolling(self.plan_label), 1)],
                       [self.next_button])
            # The notice.
            self.notice_label = label(export_notice_body(), "small", align=left_top)
            self.notice_area = scrolling(self.notice_label)
            self.bounds_label = label(guidance.bounds_line(config.limits, config.camera), "small")
            self.target_label = label(f"Looking for: {config.target}. Guide: {config.script}.", "small")
            self.path_label = label(f"The report will be saved to: {config.report_path}", "small")
            self.start_note = label(START_NOTE, "small")
            self.reminder_label = label(guidance.start_reminder(config.script, config.target), "body")
            self.dwell_label = label("", "body")  # A held Start is said here too: no line is added for it.
            self.start_button, self.cancel_button = button(TEXT_START, "go"), button(TEXT_CANCEL)
            self.start_button.clicked.connect(self.guarded(self.start_run))
            self.cancel_button.clicked.connect(self.guarded(lambda: self.decline(guidance.ENDED_CANCEL)))
            self._page(PHASE_NOTICE, [label("Please read", "title"), (self.notice_area, 1), self.bounds_label,
                                      self.target_label, self.path_label, self.start_note, self.reminder_label,
                                      self.dwell_label], [self.start_button, self.cancel_button])
            # The run.
            self.step_label = label("", "small", wrap=False, align=Qt.AlignmentFlag.AlignLeft)
            self.time_label = label("", "small", wrap=False, align=Qt.AlignmentFlag.AlignRight)
            self.picture = Picture()
            self.instruction_label = label("", "big")
            self.detail_label = label("", "small")
            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 1000)
            self.progress.setTextVisible(False)
            policy = self.progress.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.progress.setSizePolicy(policy)
            self.step_button = button("", "go")
            self.step_button.clicked.connect(self.guarded(self.step_clicked))
            self.stop_button = button(TEXT_STOP, "stop")
            self.stop_button.clicked.connect(self.guarded(lambda: self.stop_run(guidance.ENDED_STOP)))
            self._page(PHASE_RUN, [side_by_side(self.step_label, self.time_label), (self.picture, 1),
                                   self.instruction_label, self.detail_label, self.progress],
                       [self.step_button, self.stop_button])
            # A run whose helper is not known to have ended.
            self.interrupted_count = label("", "count")
            self._page(PHASE_INTERRUPTED, [label("Please wait", "title"), label(INTERRUPTED_TEXT, "big"),
                                           (self.interrupted_count, 1)], [])
            # The headset question.
            self.back_button, self.not_removed_button = button(TEXT_BACK_ON, "go"), button(TEXT_NOT_REMOVED)
            self.back_button.clicked.connect(self.guarded(lambda: self.answer_headset(guidance.HEADSET_REMOVED_AND_BACK)))
            self.not_removed_button.clicked.connect(
                self.guarded(lambda: self.answer_headset(guidance.HEADSET_NOT_REMOVED)))
            self._page(PHASE_BACK, [label("The run has ended", "title"), (label(BACK_QUESTION, "big"), 1)],
                       [self.back_button, self.not_removed_button])
            # The watch.
            self.count_label = label("", "count")
            self.changed_button, self.unchanged_button = button(TEXT_CHANGED, "stop"), button(TEXT_UNCHANGED, "go")
            self.changed_button.clicked.connect(self.guarded(lambda: self.show_phase(PHASE_CONFIRM)))
            self.unchanged_button.clicked.connect(
                self.guarded(lambda: self.answer_passthrough(guidance.PASSTHROUGH_UNCHANGED)))
            self.watch_hint = label("", "small")
            self._page(PHASE_WATCH, [label("Watch the passthrough", "title"), label(WATCH_QUESTION, "big"),
                                     (self.count_label, 1), self.watch_hint], [self.changed_button, self.unchanged_button])
            # "Something changed" is asked once more: under the passthrough rule that answer stops the camera track.
            self.confirm_button, self.go_back_button = button(TEXT_CONFIRM, "stop"), button(TEXT_GO_BACK)
            self.confirm_button.clicked.connect(self.guarded(lambda: self.answer_passthrough(guidance.PASSTHROUGH_CHANGED)))
            self.go_back_button.clicked.connect(self.guarded(lambda: self.show_phase(PHASE_WATCH)))
            self._page(PHASE_CONFIRM, [label("Please confirm", "title"), (label(CONFIRM_QUESTION, "big"), 1)],
                       [self.go_back_button, self.confirm_button])
            # The result.
            self.result_label = label("", "body", align=left_top)
            self.saved_label = label("", "small")
            self.close_label = label("", "small")
            self.close_button = button(TEXT_CLOSE)
            self.close_button.clicked.connect(self.guarded(self.close))
            self._page(PHASE_RESULT, [label("Result", "title"), (scrolling(self.result_label), 1), self.saved_label,
                                      self.close_label], [self.close_button])

        def apply_scale(self):
            """Size the type from the window's own size; called again when the window is resized."""
            unit = unit_for(self.width(), self.height())
            if unit == self.unit:
                return
            self.unit = self.picture.unit = unit
            self.picture.setMinimumHeight(unit * 5)
            for shown in self.pages.values():
                shown.content.setContentsMargins(unit * 2, unit, unit * 2, unit)
                shown.content.setSpacing(unit // 2)
                shown.row_layout.setSpacing(unit * 2)
            self.setStyleSheet(
                f"QWidget {{ background: #10151b; color: #f4f6f8; font-size: {unit}px; }}"
                f"QLabel#title {{ font-size: {int(unit * 1.5)}px; font-weight: bold; }}"
                f"QLabel#big {{ font-size: {int(unit * 1.4)}px; font-weight: bold; }}"
                f"QLabel#count {{ font-size: {unit * 5}px; font-weight: bold; color: #ffd23f; }}"
                f"QLabel#small {{ font-size: {int(unit * 0.8)}px; }}"
                f"QWidget#slot {{ min-height: {int(unit * 3.4)}px; max-height: {int(unit * 3.4)}px; }}"
                f"QPushButton {{ font-size: {int(unit * 1.3)}px; font-weight: bold; border-radius: {unit // 2}px; "
                f"min-height: {int(unit * 2.6)}px; padding: 0px {unit // 2}px; background: #2d3a4a; "
                "border: 3px solid #5b6b7c; }"
                "QPushButton#go { background: #1f7a44; border-color: #3fbf77; }"
                "QPushButton#stop { background: #a3271f; border-color: #e0635b; }"
                "QPushButton:disabled, QPushButton#go:disabled, QPushButton#stop:disabled "
                "{ background: #1b232d; color: #6b7785; border-color: #2d3a4a; }"
                f"QProgressBar {{ border: 3px solid #5b6b7c; border-radius: {unit // 3}px; min-height: {unit}px; "
                "background: #1b232d; } QProgressBar::chunk { background: #ffd23f; }"
                f"QScrollBar:vertical {{ width: {unit * 2}px; background: #1b232d; }}"
                f"QScrollBar::handle:vertical {{ background: #5b6b7c; min-height: {unit * 3}px; }}")

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.apply_scale()

        def show_phase(self, phase):
            """Show a screen: its buttons go where the last screen's were not, and are dead for the guard interval."""
            self.phase = phase
            self.band = BAND_LOWER if self.band == BAND_UPPER else BAND_UPPER
            shown = self.pages[phase]
            shown.place(self.band)
            self.page_shown_ns = clock()
            for widget in shown.buttons:
                widget.setEnabled(False)
            self.stack.setCurrentWidget(shown)
            self.refresh(self.page_shown_ns)

        def shown_for(self, now_ns):
            return (now_ns - self.page_shown_ns) / 1e9

        def guard_passed(self, now_ns):
            return self.shown_for(now_ns) >= config.guard_seconds

        def guarded(self, action):
            """``action`` for a button: nothing happens for a click inside the screen's guard interval."""
            def run():
                if self.guard_passed(clock()):
                    action()
            return run

        def refresh(self, now_ns):
            """Bring the shown screen's buttons and counts up to ``now_ns``."""
            live = self.guard_passed(now_ns)
            if self.phase == PHASE_RUN:
                self.render_run(now_ns)
                return
            if self.phase == PHASE_NOTICE:
                left = config.start_dwell_seconds - self.shown_for(now_ns)
                self.start_button.setEnabled(live and left <= 0 and not self.start_held(now_ns))
                self.cancel_button.setEnabled(live)
                held, released = config.hold_start is not None, self.hold_released_ns is not None
                if released:  # The button is dead for the guard interval after the release: the count covers it.
                    left = max(left, config.guard_seconds - (now_ns - self.hold_released_ns) / 1e9)
                self.dwell_label.setText(guidance.start_line(left, held=held, released=released))
            elif self.phase == PHASE_WATCH:
                self.render_watch(now_ns, live)
            elif self.phase == PHASE_RESULT:
                left = config.close_dwell_seconds - self.shown_for(now_ns)
                self.close_button.setEnabled(live and left <= 0)
                self.close_label.setText("" if left <= 0 else f"You can close in {int(-(-left // 1))} s")
            elif self.phase == PHASE_INTERRUPTED:
                left = max(0.0, (self.helper_gone_ns - now_ns) / 1e9)
                self.interrupted_count.setText(str(int(-(-left // 1))))
            else:
                for widget in self.pages[self.phase].buttons:
                    widget.setEnabled(live)

        # -- before the run --

        def start_held(self, now_ns):
            """Whether Start is held: the operator has not released it, or did so less than the guard interval ago."""
            if config.hold_start is None:
                return False
            return self.hold_released_ns is None or (now_ns - self.hold_released_ns) / 1e9 < config.guard_seconds

        def poll_hold(self, now_ns):
            """Look for the operator's release, at most every ``HOLD_POLL_MS``; end the window when its bound passes.

            On this timer's tick, so nothing outlives the window. The
            release is kept once seen: removing the file does not hold Start
            again. The last look is made on the tick that finds the bound
            passed, so a release made just inside it counts.
            """
            if (config.hold_start is None or self.hold_released_ns is not None or self.final is not None
                    or self.phase not in (PHASE_STATE, PHASE_PLAN, PHASE_NOTICE)):
                return
            timed_out = (now_ns - self.hold_opened_ns) / 1e9 >= config.hold_timeout_seconds
            if (not timed_out and self.hold_checked_ns is not None
                    and (now_ns - self.hold_checked_ns) / 1e6 < guidance.HOLD_POLL_MS):
                return
            self.hold_checked_ns = now_ns
            if hold_released(config.hold_start):
                self.hold_released_ns = now_ns
            elif timed_out:  # As an unstarted run ends: the report and the result screen, nothing launched.
                self.hold_timed_out = True
                self.unstarted_reason = guidance.HOLD_NOT_RELEASED
                self.ended_by = guidance.ENDED_HOLD_TIMEOUT
                self.finish()

        def answer_state(self, answer):
            if self.phase != PHASE_STATE:
                return
            self.observations["starting_state"] = answer
            if answer == guidance.START_COLOUR_STABLE:
                self.show_phase(PHASE_PLAN)
            else:  # Not the state a run may start from: nothing is launched.
                self.unstarted_reason = guidance.STARTING_STATE_NOT_STABLE
                self.finish()

        def decline(self, ended_by):
            if self.phase in (PHASE_STATE, PHASE_PLAN, PHASE_NOTICE):
                self.ended_by = ended_by
                self.finish()

        def start_run(self):
            """The Start button: the informed start of this one run. The only place a worker is made."""
            now_ns = clock()
            if (self.phase != PHASE_NOTICE or self.worker is not None
                    or self.shown_for(now_ns) < config.start_dwell_seconds or self.start_held(now_ns)):
                return
            self.started_ns = now_ns
            self.guide = guidance.Guide(config.script, config.limits["max_seconds"], started_ns=now_ns,
                                        target=config.target, scale=config.scale,
                                        guard_ms=config.guard_seconds * 1000)
            self.ended_by = guidance.ENDED_HELPER
            # With a presence watcher the helper is launched only after the watch has armed, so both moments move
            # by what arming may take: a helper whose worker died is not declared gone before its own bound.
            arming = arming_seconds(config)
            self.run_deadline_ns = now_ns + int((config.limits["max_seconds"] + RUN_MARGIN_SECONDS + arming) * 1e9)
            self.helper_gone_ns = now_ns + int(
                (config.limits["max_seconds"] + HELPER_GONE_MARGIN_SECONDS + arming) * 1e9)
            self.show_phase(PHASE_RUN)
            self.worker = worker_factory(config.spec(), self.events.put)
            self.workers_started += 1
            try:
                self.worker.start()
            except OSError as error:
                self.events.put(("report", dict(unstarted_report(config, "guide_worker_not_started"),
                                                confirmation=OPERATOR_WINDOW, error=str(error)[:1024])))

        # -- the run --

        def render_run(self, now_ns):
            """Show ``Guide.view``: the script, the click and the clock. Nothing else is an input here."""
            view = self.guide.view(now_ns)
            live = self.guard_passed(now_ns)
            self.step_label.setText(f"Step {view.step_number} of {view.step_count}")
            self.time_label.setText(f"Time left: {view.remaining_seconds} s")
            self.picture.show_kind(view.picture, view.motion)
            self.instruction_label.setText(view.instruction)
            self.detail_label.setText(view.detail)
            self.progress.setVisible(view.progress is not None)
            self.progress.setValue(0 if view.progress is None else round(view.progress * 1000))
            self.step_button.setVisible(bool(view.button))
            self.step_button.setText(view.button)
            self.step_button.setEnabled(live and view.button_enabled)
            self.stop_button.setEnabled(live and view.state != guidance.STOPPING)
            shown = (view.state, self.step_label.text(), self.instruction_label.text(), self.step_button.text(), view.picture)
            if not self.run_screen_log or self.run_screen_log[-1] != shown:
                self.run_screen_log.append(shown)

        def send_marks(self, made):
            for mark_label, monotonic_ns in made:
                self.worker.mark(mark_label, monotonic_ns)

        def step_clicked(self):
            if self.phase == PHASE_RUN and self.report is None:
                now_ns = clock()
                self.send_marks(self.guide.advance(now_ns, clicked=True))
                self.render_run(now_ns)

        def stop_run(self, ended_by):
            """Stop, the window's close or a signal during the run: the helper is ended and the report is kept."""
            if self.phase != PHASE_RUN or self.stop_sent_ns is not None:
                return
            self.ended_by = ended_by
            self.guide.stop()
            self.stop_sent_ns = clock()
            self.worker.stop()
            self.render_run(self.stop_sent_ns)

        def tick(self):
            now_ns = clock()
            if self.signal_number is not None and not self.close_requested:
                self.close_requested = True
                if self.phase == PHASE_RUN:
                    self.stop_run(guidance.ENDED_SIGNAL)
                else:
                    if self.phase in (PHASE_STATE, PHASE_PLAN, PHASE_NOTICE):
                        self.ended_by = guidance.ENDED_SIGNAL
                    self.close()
                    return
            self.poll_hold(now_ns)
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "ready" and self.phase == PHASE_RUN:
                    self.guide.ready(value)  # ``value`` is when the line was read; the worker attached nothing.
                elif kind == "report" and self.phase == PHASE_RUN:
                    self.run_ended(value)
                    return
            if self.phase == PHASE_RUN:
                self.send_marks(self.guide.advance(now_ns))
                self.watch_worker(now_ns)
            if self.phase == PHASE_INTERRUPTED and now_ns >= self.helper_gone_ns:
                self.start_watch()
                return
            if self.final is None or self.phase == PHASE_RESULT:
                self.refresh(now_ns)

        def watch_worker(self, now_ns):
            """A worker that gives no report: ask it to stop, then SIGTERM, then report that it did not answer."""
            patience = int(WORKER_PATIENCE_SECONDS * 1e9)
            if self.stop_sent_ns is None and now_ns > self.run_deadline_ns:
                self.stop_run(guidance.ENDED_HELPER)
            elif self.stop_sent_ns is not None and self.terminate_sent_ns is None and now_ns > self.stop_sent_ns + patience:
                self.terminate_sent_ns = now_ns
                self.worker.terminate()
            elif self.terminate_sent_ns is not None and now_ns > self.terminate_sent_ns + patience:
                self.run_ended(interrupted_report(config, "guide_worker_unresponsive"))

        def run_ended(self, report):
            """The worker's report has arrived, or the worker is gone. Results may be shown only for a clean end."""
            if report is None:  # The worker died: its helper may be alive until its own bound.
                report = interrupted_report(config, "guide_worker_ended_without_report")
            self.report = report
            self.worker.close()
            if self.close_requested:
                self.finish()
                self.close()
            elif not guidance.clean_shutdown(report):  # The helper is not proven ended: wait out its bound first.
                self.observations["headset"] = guidance.HEADSET_NOT_ASKED
                if clock() >= self.helper_gone_ns:
                    self.start_watch()
                else:
                    self.show_phase(PHASE_INTERRUPTED)
            elif not guidance.watch_needed(report):  # Nothing was read: say so at once, in plain words.
                self.watch_state = guidance.WATCH_NO_FRAME_READ
                self.finish()
            elif self.guide.reached(marks.REMOVE_LABEL):
                self.show_phase(PHASE_BACK)
            else:
                self.observations["headset"] = guidance.HEADSET_NOT_ASKED
                self.start_watch()

        # -- after the run --

        def answer_headset(self, answer):
            if self.phase == PHASE_BACK:
                self.observations["headset"] = answer
                self.start_watch()

        def start_watch(self):
            self.watch_started_ns = clock()
            self.watch_state = guidance.WATCH_HELD
            self.show_phase(PHASE_WATCH)

        def watch_elapsed(self, now_ns):
            return max(0.0, (now_ns - self.watch_started_ns) / 1e9)

        def render_watch(self, now_ns, live):
            left = config.watch_seconds - self.watch_elapsed(now_ns)
            done = left <= 0
            self.count_label.setText("0" if done else str(int(-(-left // 1))))
            self.changed_button.setEnabled(live)
            self.unchanged_button.setEnabled(live and done)
            self.watch_hint.setText("The watch is over. Choose one" if done else
                                    f"Keep watching. \"{TEXT_UNCHANGED}\" can be chosen when the count reaches 0")

        def answer_passthrough(self, answer):
            expected = PHASE_CONFIRM if answer == guidance.PASSTHROUGH_CHANGED else PHASE_WATCH
            if self.phase != expected:
                return
            elapsed = self.watch_elapsed(clock())
            if answer == guidance.PASSTHROUGH_UNCHANGED and elapsed < config.watch_seconds:
                return
            self.observations.update(passthrough_after=answer, watch_seconds_elapsed=round(elapsed, 3),
                                     watch_completed=elapsed >= config.watch_seconds)
            self.finish()

        def finish(self):
            """Compose and save the report, once, and show the result screen."""
            if self.final is not None:
                return
            if self.watch_started_ns is not None and self.observations["watch_seconds_elapsed"] is None:
                self.observations["watch_seconds_elapsed"] = round(self.watch_elapsed(clock()), 3)
            if self.report is not None:
                report = self.report
            elif self.worker is not None:  # The loop ended under a run (the session went away): end the helper too.
                self.worker.stop()
                report = interrupted_report(config, "guide_window_ended_during_run")
            else:
                report = unstarted_report(config, self.unstarted_reason)
            anchor = (report.get("timebase") or {}).get("anchor")
            run_clock = None if anchor is None else marks.Clock(anchor["monotonic_ns"], anchor["utc_ns"])
            hold = None if config.hold_start is None else guidance.hold_block(
                released_ns=self.hold_released_ns, started_ns=self.started_ns, timed_out=self.hold_timed_out,
                timeout_seconds=config.hold_timeout_seconds, clock=run_clock)
            report["guide"] = guidance.guide_block(
                self.guide, script=config.script, started=self.workers_started > 0, ended_by=self.ended_by,
                observations=self.observations, validation_only=config.validation, watch=self.watch_state,
                limits_ms={"late_frame_limit_ms": config.late_frame_limit_ms,
                           "removal_end_limit_ms": config.removal_end_limit_ms},
                clock=run_clock, recorded_marks=(report.get("marks") or {}).get("recorded") or (),
                started_ns=self.started_ns, hold=hold)
            report["guide"]["worker_lines_dropped"] = 0 if self.worker is None else getattr(self.worker, "unexpected_lines", 0)
            report["camera_data"].update(report_saved_by_operator=True, report_path=str(config.report_path))
            try:
                camera_recognize.write_report(config.report_path, report)
            except (OSError, ValueError) as error:
                self.report_error = str(error)
                report["camera_data"].update(report_saved_by_operator=False, report_path=None)
                report.update(report_error=self.report_error, exit_code=1)
            self.final = report
            self.result_label.setText("\n".join(guidance.result_lines(report)))
            self.saved_label.setText(f"The report was NOT saved: {self.report_error}" if self.report_error else
                                     f"Report saved to: {config.report_path}")
            self.show_phase(PHASE_RESULT)

        def closeEvent(self, event):
            if self.phase == PHASE_RUN:  # The helper is ended first; the window closes when its report is in.
                self.close_requested = True
                self.stop_run(guidance.ENDED_WINDOW_CLOSED)
                event.ignore()
                return
            if self.phase in (PHASE_STATE, PHASE_PLAN, PHASE_NOTICE) and self.ended_by == guidance.ENDED_NOT_STARTED:
                self.ended_by = guidance.ENDED_WINDOW_CLOSED
            self.finish()
            self.timer.stop()
            event.accept()

    return Window()


# ---- the validate mode --------------------------------------------------------------------------------------------

def describe_helper(binary):
    """The helper's ``--describe`` record (build facts only: no connect, no camera, no confirmation), or None."""
    try:
        completed = subprocess.run([camera_helper.helper_path(binary), "--describe"], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=DESCRIBE_TIMEOUT_SECONDS)
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    except (OSError, ValueError, subprocess.TimeoutExpired, SightCircuitError):
        return None
    return next((line for line in lines if isinstance(line, dict) and line.get("event") == "describe"), None)


VALIDATION_SCALE = 0.06  # A four-second step is 0.24 s.
VALIDATION_WATCH_SECONDS = 0.5
VALIDATION_GUARD_SECONDS = 0.3
VALIDATION_DWELL_SECONDS = 0.5
VALIDATION_WAIT_SECONDS = 40.0
VALIDATION_HELPER_ARGS = ("--pixel-path", "cpu_linear", "--fake-scenario")
VALIDATION_SIZE = (1280, 800)


class Flow:
    """Drives one window with Qt's test tools, as a pointer would: finds a button by its text and clicks it."""

    def __init__(self, config, **options):
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        self.QtCore, self.QtWidgets, self.QTest = QtCore, QtWidgets, QTest
        self.window = create_window(config, size=VALIDATION_SIZE, **options)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)

    def click(self, text):
        """Click the visible, enabled button that says ``text``; False when there is none."""
        for candidate in self.window.findChildren(self.QtWidgets.QPushButton):
            if candidate.text() == text and candidate.isVisible() and candidate.isEnabled():
                self.QTest.mouseClick(candidate, self.QtCore.Qt.MouseButton.LeftButton)
                return True
        return False

    def wait(self, condition, seconds=VALIDATION_WAIT_SECONDS):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            self.QTest.qWait(20)
        return bool(condition())

    def click_when_offered(self, text, seconds=VALIDATION_WAIT_SECONDS):
        return self.wait(lambda: self.click(text), seconds)

    def reach(self, phase, seconds=VALIDATION_WAIT_SECONDS):
        return self.wait(lambda: self.window.phase == phase, seconds)

    def start(self):
        """From the first screen to a started run: the starting state, what will happen, the notice, Start."""
        return all(self.click_when_offered(text) for text in (TEXT_COLOUR_STABLE, TEXT_NEXT, TEXT_START))

    def finish_after_run(self, headset=None, passthrough=TEXT_UNCHANGED):
        """From the run's end to the result screen: the headset answer, the watch and its answer, then Close."""
        if headset is not None and not (self.reach(PHASE_BACK) and self.click_when_offered(headset)):
            return False
        if not (self.reach(PHASE_WATCH) and self.click_when_offered(passthrough)):
            return False
        if passthrough == TEXT_CHANGED and not self.click_when_offered(TEXT_CONFIRM):
            return False
        return self.reach(PHASE_RESULT) and self.close()

    def close(self):
        return self.click_when_offered(TEXT_CLOSE) and self.wait(lambda: not self.window.isVisible())

    def run_screen_texts(self):
        return {text for entry in self.window.run_screen_log for text in entry[2:4] if text}


def validation_config(directory, name, script, helper, scenario, seconds, runtime_directory, scale=VALIDATION_SCALE):
    # The frame bound is left at the command's largest so that a run ends on its time bound or as its scenario says.
    return Config(script=script, helper=str(helper), report_path=Path(directory) / f"{name}.json",
                  limits=run_limits(script, seconds, MAX_FRAMES), helper_args=(*VALIDATION_HELPER_ARGS, scenario),
                  scale=scale, watch_seconds=VALIDATION_WATCH_SECONDS, guard_seconds=VALIDATION_GUARD_SECONDS,
                  start_dwell_seconds=VALIDATION_DWELL_SECONDS, close_dwell_seconds=VALIDATION_DWELL_SECONDS,
                  validation=True, worker_env={"XDG_RUNTIME_DIR": str(runtime_directory)})


def saved(config):
    """The report a flow left at its path, or None."""
    try:
        return json.loads(Path(config.report_path).read_text())
    except (OSError, ValueError):
        return None


def validate(helper=None):
    """The whole window flow with nothing real in it: the fake helper, a scripted detector, an offscreen window.

    The helper must say ``backend: fake`` in its ``--describe`` record before
    any window is made, so this mode can never open a camera. Each flow is
    clicked through with Qt's test tools and leaves its report: both
    recorded scripts and the scan, a run that reaches its bound, a presence end, Stop, the window
    closed mid-run, a launch that fails, Cancel, a declined start and a run
    too short for its script; then the report path that is refused before a
    window exists and the worker that refuses to run by hand. The start
    hold's flows are in their own block, ``start_hold_flows``, and count
    towards ``flows_failed`` like the rest: Start held until the release
    file is made and the run then started by the click, a hold nobody
    releases, and the release paths refused before a window exists. Returns
    the validation report.
    """
    helper = Path(guard.ROOT) / FAKE_HELPER if helper is None else Path(helper)
    report = {"report_kind": VALIDATION_KIND, "schema_version": 2, "status": "validation_failed", "reason": None,
              **gates.claims(), "validation_only": True, "images_saved": False,
              "pixels_in_report": False, "camera_opened": False, "display_started": False, "helper": None,
              "flows": {}, "start_hold_flows": {}, "exit_code": 1}
    described = describe_helper(helper)
    fake = described is not None and described.get("backend") == "fake" and described.get("sdk_release") is None
    report["helper"] = {"fake": fake, "backend": "fake" if fake else "withheld"}
    if not fake:
        report["reason"] = "validate_requires_fake_helper"
        return report
    os.environ["QT_QPA_PLATFORM"] = "offscreen"  # Nothing is ever put on a display by this mode.
    try:
        from PySide6 import QtWidgets
    except ImportError as error:
        report.update(reason="window_dependencies_unavailable", error=str(error))
        return report
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    report["qt_platform"] = application.platformName()
    flows = report["flows"]
    with tempfile.TemporaryDirectory(prefix="sightcircuit-camera-guide-validate-") as directory:
        runtime_directory = Path(directory) / "runtime"
        runtime_directory.mkdir(mode=0o700)

        def make(name, script, scenario="export_nv12", seconds=4, **options):
            return validation_config(directory, name, script, helper, scenario, seconds, runtime_directory, **options)

        def begin(config, **options):
            flow = Flow(config, **options)
            return flow, flow.start()

        def base(flow, config, ok):
            final = saved(config) or {}
            block = final.get("guide") or {}
            return {"ok": bool(ok), "status": final.get("status"), "reason": final.get("reason"),
                    "confirmation": final.get("confirmation"), "ended_by": block.get("ended_by"),
                    "workers_started": flow.window.workers_started, "report_saved": bool(final),
                    "frames_processed": final.get("frames_processed"), "watch": block.get("watch"),
                    "marks": [mark["label"] for mark in (final.get("marks") or {}).get("recorded", [])
                              if mark["source"] == marks.SOURCE_WINDOW],
                    "steps_not_reached": [entry["label"] for entry in block.get("steps_not_reached", [])],
                    "observations": block.get("observations"),
                    "run_screen_only_scripted": flow.run_screen_texts() <= guidance.static_texts(config.script, config.target),
                    "helper_cleanup_ok": final.get("cleanup_ok")}, final

        # 1. The positions script to the run's bound: no click after Start.
        config = make("positions", guidance.SCRIPT_POSITIONS)
        flow, started = begin(config)
        done = started and flow.finish_after_run()
        entry, final = base(flow, config, done)
        coverage = (final.get("summary") or {}).get("coverage") or {}
        entry.update(coverage_windows=coverage.get("windows"), result_lines=len(guidance.result_lines(final)) if final else 0)
        entry["ok"] = (entry["ok"] and entry["status"] == "recognized" and entry["confirmation"] == OPERATOR_WINDOW
                       and entry["marks"] == [step.label for step in guidance.POSITION_STEPS]
                       and entry["run_screen_only_scripted"] and entry["helper_cleanup_ok"] is True
                       and entry["watch"] == guidance.WATCH_HELD
                       and entry["observations"]["passthrough_after"] == guidance.PASSTHROUGH_UNCHANGED)
        flows["positions_to_the_bound"] = entry

        # 2. The removal script with its one click; the fake's frames stop and the helper ends headset_removed.
        config = make("removal", guidance.SCRIPT_REMOVAL, "export_removal", 8, scale=VALIDATION_SCALE / 2)
        flow, started = begin(config)
        clicked = started and flow.click_when_offered(guidance.REMOVAL_STEPS[-1].button)
        done = clicked and flow.finish_after_run(headset=TEXT_BACK_ON)
        entry, final = base(flow, config, done)
        removal = ((final.get("summary") or {}).get("removal") or [{}])[0]
        entry.update(helper_end_reason=removal.get("helper_end_reason"),
                     removal_measured=removal.get("helper_end_after_mark_ms") is not None
                     and removal.get("helper_end_after_last_frame_ms") is not None)
        entry["ok"] = (entry["ok"] and (entry["status"], entry["reason"]) == ("helper_ended_early", "headset_removed")
                       and entry["marks"] == ["near", "far", "remove"] and entry["removal_measured"]
                       and entry["run_screen_only_scripted"] and entry["helper_cleanup_ok"] is True
                       and entry["observations"]["headset"] == guidance.HEADSET_REMOVED_AND_BACK)
        flows["removal_presence_end"] = entry

        # 3. Stop once frames are being read; the watch is answered "Something changed", which is confirmed.
        config = make("stopped", guidance.SCRIPT_POSITIONS, seconds=20)
        flow, started = begin(config)
        stopped = (started and flow.wait(lambda: len(flow.window.guide.made) >= 2)
                   and flow.click_when_offered(TEXT_STOP))
        done = stopped and flow.finish_after_run(passthrough=TEXT_CHANGED)
        entry, final = base(flow, config, done)
        entry["ok"] = (entry["ok"] and entry["status"] == "stopped" and entry["ended_by"] == guidance.ENDED_STOP
                       and entry["marks"][:2] == ["centre", "left"] and bool(entry["steps_not_reached"])
                       and entry["helper_cleanup_ok"] is True
                       and entry["observations"]["passthrough_after"] == guidance.PASSTHROUGH_CHANGED)
        flows["stop_during_the_run"] = entry

        # 4. The window closed during the run: the helper is ended and the report is left.
        config = make("closed", guidance.SCRIPT_POSITIONS, seconds=20)
        flow, started = begin(config)
        closing = started and flow.wait(lambda: flow.window.guide.made) and (flow.window.close() or True)
        done = closing and flow.wait(lambda: not flow.window.isVisible())
        entry, final = base(flow, config, done)
        entry["ok"] = (entry["ok"] and entry["status"] == "stopped" and entry["ended_by"] == guidance.ENDED_WINDOW_CLOSED
                       and entry["helper_cleanup_ok"] is True and entry["observations"]["passthrough_after"] is None)
        flows["window_closed_during_the_run"] = entry

        # 5. A launch that fails (the service cannot be reached): no headset question, no watch, plain words at once.
        config = make("launch_failed", guidance.SCRIPT_REMOVAL, "provider_unreachable")
        flow, started = begin(config)
        done = started and flow.reach(PHASE_RESULT)
        words = flow.window.result_label.text().splitlines() if done else []
        done = done and flow.close()
        entry, final = base(flow, config, done)
        entry.update(words_say_no_frame=guidance.LINE_NO_FRAME in words and guidance.TELL_THE_MANAGER in words)
        entry["ok"] = (entry["ok"] and entry["status"] == "helper_ended_early" and entry["frames_processed"] == 0
                       and entry["watch"] == guidance.WATCH_NO_FRAME_READ and entry["words_say_no_frame"]
                       and entry["observations"]["passthrough_after"] is None and entry["observations"]["headset"] is None)
        flows["launch_failed"] = entry

        # 6. Cancel under the notice, and 7. "Something else" as the starting state: nothing is launched.
        for name, clicks, reason in (("cancel", (TEXT_COLOUR_STABLE, TEXT_NEXT, TEXT_CANCEL), guidance.DECLINED),
                                     ("declined_start", (TEXT_SOMETHING_ELSE,), guidance.STARTING_STATE_NOT_STABLE)):
            config = make(name, guidance.SCRIPT_REMOVAL)
            flow = Flow(config)
            done = all(flow.click_when_offered(text) for text in clicks) and flow.reach(PHASE_RESULT) and flow.close()
            entry, final = base(flow, config, done)
            entry["ok"] = (entry["ok"] and entry["workers_started"] == 0 and entry["confirmation"] is None
                           and (entry["status"], entry["reason"]) == ("error", reason)
                           and "capture" not in final and not flow.window.run_screen_log)
            flows[name] = entry

        # 8. A run too short for its script: the window says so and the report lists the steps not reached.
        config = make("out_of_time", guidance.SCRIPT_POSITIONS, seconds=4, scale=0.6)  # 2.4 s a step: one fits.
        flow, started = begin(config)
        done = started and flow.finish_after_run()
        entry, final = base(flow, config, done)
        entry["said_so"] = guidance.TEXT_OUT_OF_TIME in flow.run_screen_texts()
        entry["ok"] = (entry["ok"] and entry["status"] == "recognized" and bool(entry["steps_not_reached"])
                       and entry["said_so"] and entry["helper_cleanup_ok"] is True)
        flows["run_too_short_for_the_script"] = entry

        # 9. The scan script to the run's bound: one instruction, every frame through the next schedule entry, both
        # stand-in detectors on each, the tallies in the report, and nothing but the script on the run screen.
        config = make("scan", guidance.SCRIPT_SCAN, seconds=3)
        config.scan_detectors = tuple(DETECTORS)
        flow, started = begin(config)
        done = started and flow.finish_after_run()
        entry, final = base(flow, config, done)
        scan, view = final.get("scan") or {}, final.get("view") or {}
        tallies = {name: (block["frames"], block["target_frames"]) for name, block in (scan.get("detectors") or {}).items()}
        entry.update(schedule_length=scan.get("schedule_length"), scan_detectors=sorted(tallies),
                     view_mode=view.get("mode"),
                     every_frame_tallied=bool(tallies) and all(
                         frames == final.get("frames_processed") for frames, _hits in tallies.values()),
                     boxes_over_the_whole_frame=bool(final.get("detections")) and all(
                         record["box_coordinate_space"] == regions.FRAME_PIXELS for record in final["detections"]),
                     coverage_omitted=(final.get("summary") or {}).get("coverage") is None
                     and (final.get("summary") or {}).get("coverage_omitted") == regions.COVERAGE_OMITTED,
                     detectors_failed=scan.get("detectors_failed"))
        entry["ok"] = (entry["ok"] and entry["status"] == "recognized" and entry["marks"] == ["near"]
                       and entry["schedule_length"] == len(regions.scan_schedule()) and entry["view_mode"] == "scan"
                       and entry["scan_detectors"] == sorted(DETECTORS) and entry["every_frame_tallied"]
                       and entry["boxes_over_the_whole_frame"] and entry["run_screen_only_scripted"]
                       and entry["coverage_omitted"] and entry["detectors_failed"] == []
                       and entry["helper_cleanup_ok"] is True)
        flows["scan_to_the_bound"] = entry

        # The start hold (item L18). Start stays off, however it is asked, until the release file is made; the
        # release enables the button and starts nothing; the click starts the run and the report has the times.
        hold_flows = report["start_hold_flows"]
        config = make("held", guidance.SCRIPT_POSITIONS)
        config.hold_start = check_hold_path(Path(directory) / "held.go", config.report_path)
        flow = Flow(config)
        window = flow.window
        held = (all(flow.click_when_offered(text) for text in (TEXT_COLOUR_STABLE, TEXT_NEXT))
                and flow.wait(lambda: window.cancel_button.isEnabled()
                              and window.shown_for(time.monotonic_ns()) > config.start_dwell_seconds)
                and window.dwell_label.text() == guidance.TEXT_HOLD_WAITING)  # Only the hold is left.
        refused = held and not flow.click(TEXT_START)
        window.start_run()
        refused = (refused and window.workers_started == 0 and window.started_ns is None and window.phase == PHASE_NOTICE
                   and window.dwell_label.text() == guidance.TEXT_HOLD_WAITING)
        config.hold_start.touch()  # The operator's one command.
        released = refused and flow.wait(lambda: window.hold_released_ns is not None)
        waiting_for_the_click = released and window.workers_started == 0  # The release started nothing.
        said_so = released and flow.wait(lambda: window.dwell_label.text().startswith(guidance.TEXT_HOLD_RELEASED))
        done = said_so and flow.click_when_offered(TEXT_START) and flow.finish_after_run()
        entry, final = base(flow, config, done)
        hold = (final.get("guide") or {}).get("start_hold") or {}
        entry.update(start_refused_while_held=bool(refused), release_started_nothing=bool(waiting_for_the_click),
                     released=hold.get("released"), timed_out=hold.get("timed_out"),
                     release_file_read=hold.get("release_file_read"),
                     release_to_start_click_ms=hold.get("release_to_start_click_ms"),
                     release_times_recorded=type(hold.get("release_monotonic_ns")) is int
                     and type(hold.get("release_utc_ns")) is int)
        entry["ok"] = (entry["ok"] and entry["start_refused_while_held"] and entry["release_started_nothing"]
                       and entry["status"] == "recognized" and entry["workers_started"] == 1
                       and (entry["released"], entry["timed_out"], entry["release_file_read"]) == (True, False, False)
                       and entry["release_times_recorded"]
                       and (entry["release_to_start_click_ms"] or 0) >= VALIDATION_GUARD_SECONDS * 1000
                       and entry["run_screen_only_scripted"] and entry["helper_cleanup_ok"] is True)
        hold_flows["start_held_until_released"] = entry

        # A hold nobody releases: the window ends as an unstarted run does, with nothing launched.
        config = make("hold_timeout", guidance.SCRIPT_POSITIONS)
        config.hold_start = check_hold_path(Path(directory) / "hold_timeout.go", config.report_path)
        config.hold_timeout_seconds = 1.0
        flow = Flow(config)
        done = flow.reach(PHASE_RESULT) and flow.close()
        entry, final = base(flow, config, done)
        hold = (final.get("guide") or {}).get("start_hold") or {}
        entry.update(released=hold.get("released"), timed_out=hold.get("timed_out"))
        entry["ok"] = (entry["ok"] and entry["workers_started"] == 0 and entry["confirmation"] is None
                       and (entry["status"], entry["reason"]) == ("error", guidance.HOLD_NOT_RELEASED)
                       and entry["ended_by"] == guidance.ENDED_HOLD_TIMEOUT
                       and (entry["released"], entry["timed_out"]) == (False, True)
                       and "capture" not in final and not flow.window.run_screen_log)
        hold_flows["start_hold_not_released"] = entry

        # The release path is checked before a window exists: something already there, a link, a directory others
        # can write to, a directory that cannot be written to, and the report's own path.
        places = Path(directory) / "hold_places"
        for name, mode in (("shared", 0o777), ("read_only", 0o500)):
            (places / name).mkdir(parents=True)
            (places / name).chmod(mode)
        (Path(directory) / "dangling.go").symlink_to(Path(directory) / "nowhere")
        new_report = Path(directory) / "hold_new.json"
        refused = []
        for name, path in (("exists", Path(directory) / "held.go"), ("link", Path(directory) / "dangling.go"),
                           ("shared_directory", places / "shared" / "go"),
                           ("unwritable_directory", places / "read_only" / "go"),
                           ("missing_directory", places / "missing" / "go"), ("the_report", new_report)):
            try:
                check_hold_path(path, new_report)
            except ValueError:
                refused.append(name)
        (places / "read_only").chmod(0o700)  # So that the directory can be removed.
        hold_flows["hold_path_checked_first"] = {
            "ok": refused == ["exists", "link", "shared_directory", "unwritable_directory", "missing_directory",
                              "the_report"], "refused": refused}

        # The report path is checked before a window exists: an existing file, a missing directory, another suffix.
        refused = []
        for path in (Path(directory) / "positions.json", Path(directory) / "missing" / "a.json", Path(directory) / "a.txt"):
            try:
                check_report_path(path)
            except ValueError:
                refused.append(path.name)
        flows["report_path_checked_first"] = {"ok": refused == ["positions.json", "a.json", "a.txt"], "refused": refused}

        # The worker run by hand, as a shell would: it reads nothing, launches nothing and says why.
        by_hand = subprocess.run([sys.executable, str(Path(__file__).resolve()), WORKER_FLAG],
                                 input=json.dumps(make("by_hand", guidance.SCRIPT_REMOVAL).spec()) + "\n",
                                 capture_output=True, text=True, timeout=60)
        try:
            answer = json.loads(by_hand.stdout)["report"]
        except (ValueError, KeyError):
            answer = {}
        flows["worker_refuses_a_hand_launch"] = {
            "ok": (by_hand.returncode == 1 and answer.get("reason") == "guide_worker_not_launched_by_window"
                   and answer.get("confirmation") is None and "capture" not in answer),
            "reason": answer.get("reason"), "exit_code": by_hand.returncode}
    failed = sorted(name for group in (flows, report["start_hold_flows"]) for name, entry in group.items()
                    if not entry["ok"])
    report.update(flows_failed=failed, status="validation_failed" if failed else "validated",
                  reason="flows_failed" if failed else None, exit_code=1 if failed else 0)
    return report


# ---- the command line ---------------------------------------------------------------------------------------------

def run_window(config, *, windowed=False):
    """Open the window for the wearer and run Qt until it closes; the saved report (or the one that could not be)."""
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(guard.ROOT) / ".cache"))
    from PySide6 import QtWidgets
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_window(config, size=(1280, 800) if windowed else None)

    def note_signal(number, _frame):  # Taken up on the window's next tick: the helper is ended, the report kept.
        window.signal_number = number
    # SIGHUP too: a dropped SSH session or a closed terminal must not take the window without its report.
    previous = {number: signal.signal(number, note_signal) for number in SIGNALS}
    try:
        window.show() if windowed else window.showMaximized()
        application.exec()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    window.finish()
    return window.final


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-camera-input", action="store_true",
                        help="Opt in to a window whose Start button launches the camera helper (Camera Data)")
    parser.add_argument("--validate", action="store_true",
                        help="The whole window flow offscreen against the helper's fake build; nothing real, nothing shown")
    parser.add_argument(WORKER_FLAG, action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(WORKER_FD_FLAG, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--script", choices=tuple(guidance.SCRIPTS),
                        help="removal: two distances, then the headset comes off (one click); positions: five places, "
                             "no click; scan: the target held still straight ahead while a fixed schedule of regions "
                             "and rotations of the image is tried, no click")
    parser.add_argument("--region", metavar="X0,Y0,X1,Y1",
                        help="removal and positions: look only at this rectangle of the frame, given as fractions "
                             "(origin top left), cropped before the resize; boxes are reported over the whole frame")
    parser.add_argument("--rotation", type=int, choices=regions.ROTATIONS, default=0,
                        help="removal and positions: turn what the detector sees by this many degrees clockwise")
    parser.add_argument("--scan-detectors", metavar="NAME,NAME",
                        help=f"scan: the detectors that look at every frame; default {','.join(DETECTORS)}. "
                             "--detector, which must be one of them, fills the report's usual fields")
    parser.add_argument("--helper", type=Path, help="The sightcircuit-camera-helper binary, given explicitly; never searched "
                                                    "(--validate: a fake-backend build; default the one under artifacts/)")
    parser.add_argument("--report", type=Path,
                        help="New .json file for the report, checked before the window opens; naming it is your "
                             "action to save Camera Data")
    parser.add_argument("--target-label", default=DEFAULT_TARGET, metavar="CLASS",
                        help=f"The detector class the wearer holds up; default {DEFAULT_TARGET}")
    parser.add_argument("--seconds", type=int, choices=range(1, MAX_SECONDS + 1), metavar=f"1..{MAX_SECONDS}",
                        help=f"The run's time bound; default {MAX_SECONDS}")
    parser.add_argument("--max-frames", type=int, choices=range(1, MAX_FRAMES + 1), metavar=f"1..{MAX_FRAMES}",
                        help="The run's frame bound; default seconds times frames a second")
    parser.add_argument("--fps", type=int, choices=range(1, MAX_FPS + 1), default=DEFAULT_FPS, metavar=f"1..{MAX_FPS}")
    parser.add_argument("--camera", type=int, choices=contract.CAMERA_INDICES, default=LEFT_CAMERA,
                        help="The one camera to export; default 0, the left")
    parser.add_argument("--threshold", type=float, default=POC_THRESHOLD)
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR)
    parser.add_argument("--helper-arg", action="append", default=[], metavar="ARG",
                        help="An extra argument for the helper after the contract ones")
    parser.add_argument("--late-frame-limit-ms", type=int, default=marks.LATE_FRAME_LIMIT_MS, metavar="MS",
                        help="What each removal entry compares frame times after the remove click with")
    parser.add_argument("--removal-end-limit-ms", type=int, default=marks.REMOVAL_END_LIMIT_MS, metavar="MS",
                        help="What each removal entry compares the helper's end after the remove click with")
    parser.add_argument("--presence-watcher", type=Path, metavar="PATH",
                        help="The sightcircuit-presence-watcher binary (scripts/build_presence_watcher.sh), given "
                             "explicitly: the run ends when SteamVR reports standby, and refuses to read the camera "
                             "if the watcher cannot be armed. Presence signals only; no camera data")
    parser.add_argument("--hold-start", type=Path, metavar="PATH",
                        help="Keep Start off until this file exists: the operator releases the run with 'touch PATH' "
                             "once its companions are up, and the wearer's click still starts it. Nothing may be at "
                             "PATH yet; its directory must be one only you can write to, or the report's. The window "
                             f"ends unstarted if no release comes within {guidance.HOLD_TIMEOUT_SECONDS // 60} minutes")
    parser.add_argument("--windowed", action="store_true", help="A 1280 by 800 window in place of a maximized one")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main(args.worker_fd)
    run_arguments = (args.allow_camera_input or args.script or args.report or args.seconds or args.max_frames
                     or args.helper_arg or args.windowed or args.region or args.rotation or args.scan_detectors
                     or args.presence_watcher or args.hold_start)
    if args.validate:
        if run_arguments:
            parser.error("--validate takes only --helper")
        report = validate(args.helper)
        print(json.dumps(report, indent=2))
        return report["exit_code"]
    if not args.allow_camera_input:
        parser.error("--allow-camera-input is required to open the window (or use --validate)")
    if args.helper is None or args.report is None or args.script is None:
        parser.error("--helper, --report and --script are required")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if args.late_frame_limit_ms < 0 or args.removal_end_limit_ms < 0:
        parser.error("the limits are milliseconds, zero or more")
    if args.target_label not in class_names(args.detector):
        parser.error(f"--target-label must be a class of {args.detector}: {', '.join(class_names(args.detector))}")
    # The region, the rotation and the scan's detectors are settled here: before any window, any click, any camera.
    region, scan_detectors = None, ()
    if args.script == guidance.SCRIPT_SCAN:
        if args.region is not None or args.rotation:
            parser.error("--script scan takes its own schedule of regions and rotations: no --region, no --rotation")
        scan_detectors = tuple(DETECTORS) if args.scan_detectors is None else tuple(args.scan_detectors.split(","))
        if (len(set(scan_detectors)) != len(scan_detectors) or any(name not in DETECTORS for name in scan_detectors)
                or args.detector not in scan_detectors):
            parser.error(f"--scan-detectors takes distinct names of {', '.join(DETECTORS)}, --detector among them")
        seconds = guidance.DEFAULT_SECONDS[args.script] if args.seconds is None else args.seconds
        frames = seconds * args.fps if args.max_frames is None else args.max_frames
        if seconds < guidance.SCAN_MIN_SECONDS or frames < guidance.SCAN_MIN_FRAMES:
            parser.error(f"--script scan needs at least {guidance.SCAN_MIN_SECONDS} seconds and "
                         f"{guidance.SCAN_MIN_FRAMES} frames (two passes of its schedule)")
        for name in scan_detectors:
            if args.target_label not in class_names(name):
                parser.error(f"--target-label must be a class of every scan detector; {name} has no {args.target_label}")
    else:
        if args.scan_detectors is not None:
            parser.error("--scan-detectors is for --script scan")
        try:
            if args.region is not None or args.rotation:
                region = regions.Region(*(regions.WHOLE if args.region is None else regions.parse_region(args.region)),
                                        rotation=args.rotation).fractions
        except ValueError as error:
            parser.error(f"--region: {error}")
    try:
        limits = run_limits(args.script, args.seconds, args.max_frames, args.fps)
    except ValueError as error:
        parser.error(str(error))
    config = Config(script=args.script, helper=str(args.helper), report_path=Path(args.report), limits=limits,
                    camera=args.camera, target=args.target_label, detector=args.detector, threshold=args.threshold,
                    helper_args=tuple(args.helper_arg), late_frame_limit_ms=args.late_frame_limit_ms,
                    removal_end_limit_ms=args.removal_end_limit_ms, region=region, rotation=args.rotation,
                    scan_detectors=scan_detectors)
    refusal = None
    try:  # The report's path first: nothing is shown to the wearer for a run whose report could not be saved.
        config.report_path = check_report_path(args.report)
    except ValueError as error:
        refusal = ("report_path_refused", str(error))
    if refusal is None and args.hold_start is not None:
        try:  # The release file's place: nothing there yet, in a directory nobody else can write to.
            config.hold_start = check_hold_path(args.hold_start, config.report_path)
        except ValueError as error:
            refusal = ("hold_start_path_refused", str(error))
    if refusal is None:
        try:
            config.helper = camera_helper.helper_path(args.helper)
        except SightCircuitError as error:
            refusal = (error.reason, str(error))
    if refusal is None and args.presence_watcher is not None:
        try:  # The helper's rules: an executable outside the checkout, or under the ignored artifacts/.
            config.presence_watcher = camera_helper.helper_path(args.presence_watcher)
        except SightCircuitError as error:
            refusal = ("presence_watcher_" + error.reason.removeprefix("helper_"),
                       str(error).replace("helper", "presence watcher"))
    if refusal is None and not os.environ.get("QT_QPA_PLATFORM") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        refusal = ("no_display_session", "No display session found. Run in the Frame's graphical session.")
    if refusal is None:
        try:
            import PySide6  # noqa: F401
        except ImportError as error:
            refusal = ("window_dependencies_unavailable", f"{error}. Run bash scripts/setup_window.sh")
    if refusal is not None:  # Before any window: nothing was shown, launched or saved.
        report = dict(unstarted_report(config, refusal[0]), error=refusal[1])
        report["guide"] = guidance.guide_block(
            None, script=config.script, started=False, ended_by=guidance.ENDED_NOT_STARTED,
            observations={"starting_state": None, "passthrough_after": None, "headset": None,
                          "watch_seconds_elapsed": None, "watch_completed": False},
            watch=guidance.WATCH_NOT_STARTED,
            limits_ms={"late_frame_limit_ms": config.late_frame_limit_ms,
                       "removal_end_limit_ms": config.removal_end_limit_ms},
            hold=None if args.hold_start is None else guidance.hold_block(
                released_ns=None, started_ns=None, timed_out=False, timeout_seconds=config.hold_timeout_seconds))
    else:
        report = run_window(config, windowed=args.windowed)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
