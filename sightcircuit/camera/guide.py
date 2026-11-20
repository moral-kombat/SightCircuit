"""The guided bounded recognition run: its three scripts, what the run screen shows, and the result in plain words.

``camera_guide`` walks the wearer through one ``camera_recognize`` run in a
window, so nobody needs a keyboard, a terminal bell or a count of seconds
(owner request 2026-11-19). This module is the part with no window in it:
standard library only, no Qt, no camera, no pixel.

A script is a tuple of ``Step``s. Each step records one mark from the closed
vocabulary (``marks.VOCABULARY``) when it begins: by itself when the step
before it has had its time (``ADVANCE_CLOCK``), or at the wearer's click
(``ADVANCE_CLICK``). ``removal`` needs one click in the run: the target is
already held at about half a metre when Start is clicked, so ``near`` is
marked at once, ``far`` when its instruction appears, and the one click is
the one made just before the headset comes off. ``positions`` is five places
and out of view, with no click after Start. ``scan`` (work-order item L14,
after E100) is one instruction for the whole run, the target held still
straight ahead at about half a metre, while the run tries a fixed schedule of
regions and rotations of the camera image (``camera.region.scan_schedule``,
which this module does not import: the script is only what the wearer is
told). What will happen is shown before Start (``plan_lines``).

Two clocks. The run's time budget is counted from the Start click, which is
before the helper begins its own bound, so the time shown is never more than
the helper has. The script's steps are counted from the moment the helper's
``ready`` was accepted.

What the run screen shows while the camera reader is active is ``Guide.view``:
a function of the script, the run's time bound, the Start click's time, the
time the reader reported ready, the wearer's click and the clock, and of
nothing else. No detection, label, count or other value derived from a camera
frame is an input, so none can be shown; ``tests/test_camera_guide.py`` pins
that. A step that can no longer fit in the time left is taken down when that
becomes true, not when it is clicked, and a click that could not be recorded
as a mark is never accepted. Results are put into words (``result_lines``)
only from the finished report, which the window receives after the helper has
ended; the words give measured values and judge nothing.

Nothing here opens a camera, lifts a gate or approves a run.
"""
from dataclasses import dataclass

from . import marks
from .confirmation import OPERATOR_WINDOW

MS = 1_000_000
ADVANCE_CLICK, ADVANCE_CLOCK = "click", "clock"
SCRIPT_REMOVAL, SCRIPT_POSITIONS, SCRIPT_SCAN = "removal", "positions", "scan"
# View states of the run screen.
STARTING, WAITING, RECORDING, FINISHED, OUT_OF_TIME, STOPPING = (
    "starting", "waiting", "recording", "finished", "out_of_time", "stopping")
# The wearer's observations: closed choices only, never free text.
START_COLOUR_STABLE, START_SOMETHING_ELSE = STARTING_STATES = ("colour_stable", "something_else")
PASSTHROUGH_UNCHANGED, PASSTHROUGH_CHANGED = PASSTHROUGH_ANSWERS = ("unchanged", "changed")
HEADSET_REMOVED_AND_BACK, HEADSET_NOT_REMOVED, HEADSET_NOT_ASKED = HEADSET_ANSWERS = (
    "removed_and_back", "not_removed", "not_asked")
# Whether the 30-second watch was held, and if not why.
WATCH_HELD, WATCH_NOT_STARTED, WATCH_NO_FRAME_READ = WATCH_STATES = ("held", "run_not_started", "no_frame_read")
# How a run's guidance ended.
ENDED_BY = (ENDED_HELPER, ENDED_STOP, ENDED_WINDOW_CLOSED, ENDED_CANCEL, ENDED_SIGNAL, ENDED_NOT_STARTED) = (
    "helper", "stop_button", "window_closed", "cancel_button", "signal", "not_started")
# Reasons of a run that never launched anything.
DECLINED, STARTING_STATE_NOT_STABLE = "confirmation_declined", "starting_state_not_stable"
WATCH_SECONDS = 30  # The passthrough watch after a run (the accepted passthrough rule).
# Pictures the window draws from shapes; none is an image and none comes from a camera.
PICTURES = ("wait", "near", "far", "headset_off", "centre", "left", "right", "up", "down", "out_of_view", "done")
# 2: one click in the removal script, the budget from Start, the watch state. 3: the scan script (item L14).
GUIDE_SCHEMA_VERSION = 3
GUARD_MS = 1200  # How long a button must have been on the screen before a click on it counts.
STARTING_HELP_SECONDS = 10  # After this long without the reader, the starting screen says what to do.
WAIT_CYCLE_MS = 1500  # The period of the starting screen's moving dot.


@dataclass(frozen=True)
class Step:
    """One step: the mark it records, what the wearer is told, how it begins and how long it is recorded."""
    label: str  # From ``marks.VOCABULARY``.
    instruction: str  # ``{target}`` is the target label's name.
    picture: str  # One of ``PICTURES``.
    advance: str  # ``ADVANCE_CLICK`` or ``ADVANCE_CLOCK``.
    record_ms: int  # Shown with the progress bar once the mark is made.
    needs_ms: int  # The run time that must be left for the step to begin.
    button: str = ""  # The click step's button.
    recording: str = ""  # Shown once the step's mark is made.


REMOVAL_STEPS = (
    Step("near", "Keep holding the {target} about half a metre in front of you", "near", ADVANCE_CLOCK, 4000, 4000,
         "", "Keep holding the {target} about half a metre in front of you"),
    # About two seconds to move it and four recorded; the mark is made when the instruction appears.
    Step("far", "Now move it to about a metre away, and hold it there", "far", ADVANCE_CLOCK, 6000, 6000, "",
         "Now move it to about a metre away, and hold it there"),
    Step("remove", "Click, then take the headset off. Put it back on when you like", "headset_off", ADVANCE_CLICK,
         0, 5000, "I am taking it off now", "You can take the headset off now"),
)
POSITION_STEPS = (
    *(Step(label, text, label, ADVANCE_CLOCK, 4000, 4000, "", text) for label, text in (
        ("centre", "Hold the {target} in the centre of your view"),
        ("left", "Hold the {target} to your left"),
        ("right", "Hold the {target} to your right"),
        ("up", "Hold the {target} up high"),
        ("down", "Hold the {target} down low"))),
    Step("out_of_view", "Take the {target} out of view", "out_of_view", ADVANCE_CLOCK, 3000, 1000, "",
         "Take the {target} out of view"),
)
# One instruction for the whole run: the mark is made when the reader is ready and the bar runs for the run's time
# bound less two seconds, so it is still moving when a run whose launch took up to two seconds ends on its bound.
SCAN_TEXT = "Hold the {target} upright, straight ahead of you, about half a metre away. Keep it still"
SCAN_LAUNCH_ALLOWANCE_MS = 2000
SCAN_NEEDS_MS = 5000
# The bounds a scan can serve, checked on the command line before any window: two passes of its 24-entry schedule
# (48 frames) and ten seconds, so the bar and the step's five seconds always fit the run.
SCAN_MIN_SECONDS, SCAN_MIN_FRAMES = 10, 48


def scan_steps(max_seconds):
    """The scan's one step for a run of ``max_seconds``: its bar is tied to the run's bound."""
    return (Step("near", SCAN_TEXT, "near", ADVANCE_CLOCK, max(1000, max_seconds * 1000 - SCAN_LAUNCH_ALLOWANCE_MS),
                 SCAN_NEEDS_MS, "", SCAN_TEXT),)


SCAN_STEPS = scan_steps(30)
SCRIPTS = {SCRIPT_REMOVAL: REMOVAL_STEPS, SCRIPT_POSITIONS: POSITION_STEPS, SCRIPT_SCAN: SCAN_STEPS}
# The time bound a script asks for when the operator names none: the command's whole bound. The budget is counted
# from the Start click, so what the helper's launch takes comes out of it before the first step.
DEFAULT_SECONDS = {SCRIPT_REMOVAL: 30, SCRIPT_POSITIONS: 30, SCRIPT_SCAN: 30}
assert all(step.label in marks.VOCABULARY and step.picture in PICTURES for steps in SCRIPTS.values() for step in steps)
TEXT_STARTING = "Starting the camera reader. Please wait"
TEXT_STARTING_HELP = "If this does not change, click Stop and tell the Frame manager"
TEXT_STOPPING = "Stopping. Please wait"
TEXT_FINISHED = "That is all. The run ends by itself"
TEXT_OUT_OF_TIME = "There is not enough time left for the next step. The run ends by itself"
PLANS = {
    SCRIPT_REMOVAL: (
        "This run takes about 30 seconds. You click once during it.",
        "1. Before you click Start: hold the {target} about half a metre in front of you. Keep holding it when you "
        "click Start.",
        "2. When the screen says so, move it to about a metre away and hold it there.",
        "3. When the screen says so, click the button, then take the headset off. Put it back on when you like."),
    SCRIPT_POSITIONS: (
        "This run takes about 30 seconds. No click is needed after Start.",
        "The screen shows where to hold the {target}: centre, left, right, up, down, each for about 4 seconds.",
        "Then it asks you to take the {target} out of view."),
    SCRIPT_SCAN: (
        "This run takes about 30 seconds. No click is needed after Start.",
        "Before you click Start: hold the {target} upright, straight ahead of you, about half a metre away.",
        "Keep it there, as still as you can, until the screen says the run is over. Keep the headset on."),
}
START_REMINDERS = {
    SCRIPT_REMOVAL: "Hold the {target} about half a metre in front of you, and keep holding it when you click Start.",
    SCRIPT_POSITIONS: "Have the {target} in your hand when you click Start.",
    SCRIPT_SCAN: "Hold the {target} upright, straight ahead of you, about half a metre away, and keep it there when "
                 "you click Start.",
}


def plan_lines(script, target="bottle"):
    """What the run will ask, shown before Start."""
    return [line.format(target=target) for line in PLANS[script]]


def start_reminder(script, target="bottle"):
    return START_REMINDERS[script].format(target=target)


@dataclass(frozen=True)
class View:
    """What the run screen shows at one moment. Every field comes from the script, the clicks and the clock."""
    state: str
    instruction: str
    detail: str  # A second, smaller line: the starting screen's elapsed time and what to do if nothing changes.
    picture: str
    motion: float  # 0..1, the phase of the starting screen's moving dot.
    progress: float | None  # 0..1 while a step is recorded, else None.
    remaining_seconds: int  # Of the run's time bound, counted from the Start click.
    button: str  # The click step's button, or "" for none.
    button_enabled: bool  # False until the button has been on the screen for the guard interval.
    step_number: int
    step_count: int


class Guide:
    """One run of one script. Times are host monotonic nanoseconds the caller reads; nothing here reads a clock.

    ``started_ns`` is the Start click: the run's time budget is counted
    from it. ``ready`` starts the script's steps (the helper's ``ready`` was
    accepted). ``advance`` moves the script on and returns the marks to
    record, ``(label, monotonic_ns)``, each made at the time given: the tick
    on which a clock step's instruction appears, or the click. A step whose
    ``needs_ms`` is more than the time left does not begin: the script stops
    there (``OUT_OF_TIME``), on the tick that finds it so, and the steps
    from it on are ``not_reached``. A click counts only for a step that is
    waiting, has shown its button for ``guard_ms`` and can still be
    recorded. ``scale`` shortens every step for the checks that run the
    whole flow in seconds; a device run uses 1.
    """

    def __init__(self, script, max_seconds, *, started_ns, target="bottle", scale=1.0, guard_ms=GUARD_MS):
        if script not in SCRIPTS:
            raise ValueError(f"script must be one of {', '.join(SCRIPTS)}")
        if not 0 < scale <= 1:
            raise ValueError("scale must be above zero and at most one")
        self.script, self.max_seconds, self.target, self.scale = script, max_seconds, target, scale
        self.started_ns, self.guard_ms = started_ns, guard_ms
        self.steps = scan_steps(max_seconds) if script == SCRIPT_SCAN else SCRIPTS[script]
        self.ready_ns = None
        self.index = 0
        self.step_started_ns = None  # When the current step's mark was made; None while it has not begun.
        self.waiting_since_ns = None  # When the current click step's button appeared.
        self.made = []  # ``{"step", "label", "monotonic_ns"}`` per mark made, in order.
        self.finished = self.out_of_time = self.stopping = False

    def _ms(self, value):
        return value * self.scale

    def ready(self, now_ns):
        if self.ready_ns is None:
            self.ready_ns = now_ns

    def stop(self):
        """The wearer stopped the run: no further mark is made."""
        self.stopping = True

    def remaining_ms(self, now_ns):
        return max(0.0, self.max_seconds * 1000 - (now_ns - self.started_ns) / MS)

    def _button_enabled(self, now_ns):
        return self.waiting_since_ns is not None and (now_ns - self.waiting_since_ns) / MS >= self.guard_ms

    def advance(self, now_ns, clicked=False):
        """Move the script to ``now_ns`` (``clicked``: the step's button was clicked now); the new marks."""
        new = []
        # A click answers the step that was waiting for it, once its button has been up for the guard interval.
        clicked = clicked and self.step_started_ns is None and self._button_enabled(now_ns)
        while self.ready_ns is not None and not (self.finished or self.out_of_time or self.stopping):
            step = self.steps[self.index]
            if self.step_started_ns is None:
                if self.remaining_ms(now_ns) < self._ms(step.needs_ms):  # Found on this tick, not on a later click.
                    self.out_of_time = True
                    self.waiting_since_ns = None
                    break
                if step.advance == ADVANCE_CLICK and not clicked:
                    if self.waiting_since_ns is None:
                        self.waiting_since_ns = now_ns
                    break
                clicked = False
                self.step_started_ns = now_ns
                self.waiting_since_ns = None
                self.made.append({"step": self.index + 1, "label": step.label, "monotonic_ns": now_ns})
                new.append((step.label, now_ns))
            if (now_ns - self.step_started_ns) / MS < self._ms(step.record_ms):
                break
            if self.index + 1 == len(self.steps):
                self.finished = True
                break
            self.index += 1
            self.step_started_ns = None
        return new

    def view(self, now_ns):
        """The run screen at ``now_ns``."""
        count = len(self.steps)
        seconds = int(-(-self.remaining_ms(now_ns) // 1000))
        step = self.steps[self.index]

        def shown(state, text, picture, progress=None, button="", detail="", motion=0.0):
            return View(state, text.format(target=self.target), detail, picture, motion, progress, seconds, button,
                        bool(button) and self._button_enabled(now_ns), min(self.index + 1, count), count)
        if self.stopping:
            return shown(STOPPING, TEXT_STOPPING, "wait")
        if self.ready_ns is None:
            elapsed_ms = max(0, (now_ns - self.started_ns) // MS)
            waited = int(elapsed_ms // 1000)
            detail = f"Waiting {waited} s" + (f". {TEXT_STARTING_HELP}" if waited >= STARTING_HELP_SECONDS else "")
            return shown(STARTING, TEXT_STARTING, "wait", detail=detail,
                         motion=round((elapsed_ms % WAIT_CYCLE_MS) / WAIT_CYCLE_MS, 3))
        if self.out_of_time:
            return shown(OUT_OF_TIME, TEXT_OUT_OF_TIME, "done")
        if self.finished:
            return shown(FINISHED, step.recording if step.record_ms == 0 else TEXT_FINISHED,
                         step.picture if step.record_ms == 0 else "done")
        if self.step_started_ns is None:
            return shown(WAITING, step.instruction, step.picture, None, step.button)
        fraction = min(1.0, max(0.0, (now_ns - self.step_started_ns) / MS / max(self._ms(step.record_ms), 1e-9)))
        return shown(RECORDING, step.recording, step.picture, round(fraction, 3))

    def steps_report(self):
        """Per step: its label, how it begins, its recorded time and, once begun, when its mark was made."""
        made = {entry["step"]: entry for entry in self.made}
        return [{"step": number, "label": step.label, "advance": step.advance,
                 "record_ms": round(self._ms(step.record_ms), 3), "reached": number in made,
                 "monotonic_ns": made[number]["monotonic_ns"] if number in made else None}
                for number, step in enumerate(self.steps, 1)]

    def not_reached(self):
        return [{"step": entry["step"], "label": entry["label"]} for entry in self.steps_report() if not entry["reached"]]

    def reached(self, label):
        return any(entry["label"] == label for entry in self.made)


def static_texts(script, target="bottle"):
    """Every instruction and button text the run screen can show for ``script``."""
    texts = {TEXT_STARTING, TEXT_STOPPING, TEXT_FINISHED, TEXT_OUT_OF_TIME}
    for step in SCRIPTS[script]:
        texts.update(text.format(target=target) for text in (step.instruction, step.recording, step.button) if text)
    return texts


def bounds_line(limits, camera):
    """The run's bounds as the notice screen states them."""
    side = {0: "left", 1: "right"}.get(camera, str(camera))
    return (f"At most {limits['max_seconds']} seconds and {limits['max_frames']} frames, {limits['fps']} frames a "
            f"second, the {side} camera.")


def clean_shutdown(report):
    """Whether the report says the helper is proven ended: only then may anything frame-derived be put into words."""
    return report.get("cleanup_ok") is True


def frame_read(report):
    """Whether the run read at least one camera frame."""
    return bool(report.get("frames_received"))


def watch_needed(report):
    """Whether the 30-second passthrough watch follows this run.

    Not after a launch that started nothing, and not after a clean run that
    read no frame and did not end on a presence stop (a failed launch: the
    wearer is told at once, in plain words). A run that read no frame but
    ended ``headset_removed`` or ``standby`` had a reader that was active,
    and a run without a clean shutdown may still have one: both are watched.
    """
    if report.get("confirmation") is None:
        return False
    if not clean_shutdown(report):
        return True
    return frame_read(report) or presence_end(report)


def presence_end(report):
    """Whether a camera reader that was launched ended on a presence stop: the helper's own, or the presence watch's."""
    if (report.get("helper_end") or {}).get("reason") in marks.PRESENCE_END_REASONS:
        return True
    return report.get("status") == marks.STATUS_PRESENCE_ENDED and report.get("helper_launched") is not False


def guide_block(guide, *, script, started, ended_by, observations, limits_ms, watch, validation_only=False, clock=None,
                recorded_marks=(), started_ns=None):
    """The report's ``guide`` block: the script, the steps with their marks, the wearer's closed answers.

    ``recorded_marks`` are the run's recorded marks (the report's
    ``marks.recorded``): a step's ``mark_index`` is the window mark made at
    its time, or ``None`` when the run recorded none for it.
    """
    by_time = {mark["monotonic_ns"]: mark["index"] for mark in recorded_marks if mark.get("source") == marks.SOURCE_WINDOW}
    steps = guide.steps_report() if guide is not None else [
        {"step": number, "label": step.label, "advance": step.advance, "record_ms": step.record_ms, "reached": False,
         "monotonic_ns": None} for number, step in enumerate(SCRIPTS[script], 1)]
    for entry in steps:
        entry["mark_index"] = by_time.get(entry["monotonic_ns"])
        entry["utc_ns"] = None if clock is None or entry["monotonic_ns"] is None else clock.utc(entry["monotonic_ns"])
    return {"schema_version": GUIDE_SCHEMA_VERSION, "script": script,
            "confirmation": OPERATOR_WINDOW if started else None, "started_by_start_button": bool(started),
            "ended_by": ended_by, "validation_only": bool(validation_only),
            "scale": 1.0 if guide is None else guide.scale,
            "start_click_monotonic_ns": started_ns,
            "reader_ready_monotonic_ns": None if guide is None else guide.ready_ns,
            "steps": steps,
            "steps_not_reached": [{"step": entry["step"], "label": entry["label"]} for entry in steps
                                  if not entry["reached"]],
            "out_of_time": bool(guide is not None and guide.out_of_time),
            "watch": watch, "watch_states": list(WATCH_STATES),
            "observations": dict(observations), "observation_choices": {
                "starting_state": list(STARTING_STATES), "passthrough_after": list(PASSTHROUGH_ANSWERS),
                "headset": list(HEADSET_ANSWERS)},
            "removal_limits_ms": dict(limits_ms), "watch_seconds_required": WATCH_SECONDS,
            "run_screen_inputs": ["script", "clock", "click", "start_click_time", "reader_ready_time"],
            "shown_during_run": "scripted_instructions_progress_and_time_only"}


# ---- the result in plain words ------------------------------------------------------------------------------------

TELL_THE_MANAGER = "Tell the Frame manager."
LINE_UNCLEAN = "The run ended without a clean shutdown."
LINE_INTERRUPTED = "The run was interrupted."
LINE_NO_FRAME = "No camera frame was read."


def _seconds(ms):
    return f"{ms / 1000:.1f} s"


def remove_marked(report):
    """Whether the run has a remove click that came before its end was decided (a click after SteamVR's signal is not one)."""
    return any(not entry.get("mark_after_presence_signal") for entry in (report.get("summary") or {}).get("removal") or ())


def status_line(report):
    status, reason = report.get("status"), report.get("reason")
    if status == "recognized":
        return "The run finished on its bound."
    if status == "helper_ended_early" and reason in marks.PRESENCE_END_REASONS and remove_marked(report):
        return f"The run ended when the headset came off ({reason})."
    if status == "helper_ended_early" and reason in marks.PRESENCE_END_REASONS:
        return f"The camera reader ended early ({reason}), before the click to take the headset off."
    if status == "helper_ended_early":
        return f"The camera reader ended early ({reason})."
    if status == marks.STATUS_PRESENCE_ENDED and remove_marked(report):
        return f"The run ended when SteamVR reported the headset off ({reason})."
    if status == marks.STATUS_PRESENCE_ENDED:
        return f"The run ended early: SteamVR reported the headset off ({reason}) before the click to take it off."
    if status == marks.STATUS_WATCH_LOST:
        return "The run was ended because the headset-off watcher stopped answering. Tell the Frame manager."
    if reason == marks.REASON_WATCH_NOT_ARMED:
        return ("Not started: the headset-off watcher could not be armed, so the camera was not read. "
                "Tell the Frame manager.")
    if status == "stopped":
        return "The run was stopped."
    if status == "cancelled":
        return "The run was cancelled."
    if reason == DECLINED:
        return "Not started. Nothing was launched."
    if reason == STARTING_STATE_NOT_STABLE:
        return "Not started, because the view was not colour and stable. Nothing was launched. Tell the Frame manager."
    return f"The run did not work: {status}" + (f" ({reason})." if reason else ".")


def _ranked(rows):
    """Tally rows that held the target, largest share first (groups differ in size, so never by count)."""
    return sorted((row for row in rows if row["target_frames"]),
                  key=lambda row: (-row["share"], -row["target_frames"], str(row["key"])))


def _row(row, unit=""):
    return f"{row['key']}{unit} {row['target_frames']} of {row['frames']}"


def scan_lines(scan, target):
    """A scan's tallies in plain words: per detector its count, then where it found the target, by share.

    The leading rotation is given with its runner-up, and a tie is called a
    tie: a detector finds an object that looks alike from both ends at a
    turn and at its opposite, so the words say the scan gives the axis and
    not which way up. A detector that failed part way is named.
    """
    lines = []
    for name, block in (scan.get("detectors") or {}).items():
        failed = block.get("failed")
        if failed:
            lines.append(f"{name} failed after {block['frames']} frames ({failed['reason']}). {TELL_THE_MANAGER}")
        lines.append(f"{name}: {target} in {block['target_frames']} of {block['frames']} frames.")
        turns, regions = _ranked(block["by_rotation"]), _ranked(block["by_region"])
        if not turns:
            continue
        tied = len(turns) > 1 and turns[0]["share"] == turns[1]["share"]
        line = f"{name}, by turn of the image: " + ", ".join(_row(row, " degrees") for row in turns[:2])
        lines.append(line + (" (a tie)." if tied else "."))
        tied = len(regions) > 1 and regions[0]["share"] == regions[1]["share"]
        lines.append(f"{name}, by region: " + ", ".join(_row(row) for row in regions[:2]) + (" (a tie)." if tied else "."))
    if lines:
        lines.append("A turn and its opposite look alike to the detectors: this gives the axis, not which way up.")
        lines.append("Regions and turns are the camera image's as exported, which may not be yours.")
    return lines


def result_lines(report):
    """The finished report in plain words: counts, closed labels and timings only, each line one fact.

    A launch that started nothing says only that. A run whose shutdown is
    not confirmed says only that and to tell the Frame manager: the helper
    is not proven ended, so nothing derived from a frame is put into words.
    A run that read no frame says what happened and that none was read.
    """
    if report.get("confirmation") is None:
        return [status_line(report)]
    if not clean_shutdown(report):
        return [LINE_INTERRUPTED if (report.get("reason") or "").startswith("guide_worker") else LINE_UNCLEAN,
                TELL_THE_MANAGER]
    lines = [status_line(report)]
    if not frame_read(report):
        return lines + [LINE_NO_FRAME] + ([] if report.get("status") == "stopped" else [TELL_THE_MANAGER])
    lines.append(f"{report.get('frames_processed', 0)} frames were looked at.")
    target = report.get("target_label")
    summary = report.get("summary") or {}
    coverage = summary.get("coverage")
    if target and coverage and coverage.get("windows"):
        lines.append(f"{target} seen in {coverage['windows_with_target']} of {coverage['windows']} seconds "
                     "while it was held in view.")
    for interval in summary.get("intervals") or ():
        if interval["label"] not in marks.IN_VIEW_LABELS or not target or "target_frames" not in interval:
            continue
        line = f"{interval['label']}: {target} in {interval['target_frames']} of {interval['frames']} frames"
        if interval["label"] in marks.POSITION_LABELS:
            cells = sorted(((count, name) for name, count in interval["cells"].items() if count),
                           key=lambda item: (-item[0], item[1]))
            if cells:
                line += "; in the image: " + ", ".join(f"{name} {count}" for count, name in cells)
        lines.append(line + ".")
    if any(interval["label"] in marks.POSITION_LABELS for interval in summary.get("intervals") or ()):
        lines.append("Image sides are the camera image's as exported, which may not be yours.")
    lines.extend(scan_lines(report.get("scan") or {}, target))
    for entry in summary.get("removal") or ():
        if entry.get("mark_after_presence_signal"):
            # The end was decided before the click: nothing is measured from it, and it is not called a removal.
            lines.append(f"SteamVR had already reported the headset off ({entry['presence_signal']}) "
                         f"{_seconds(-entry['presence_signal_after_mark_ms'])} before your click to take it off.")
            continue
        if entry["helper_end_after_mark_ms"] is None:
            lines.append("After your click to take the headset off: the camera reader left no end record.")
            continue
        lines.append(f"After your click to take the headset off: the camera reader ended "
                     f"{_seconds(entry['helper_end_after_mark_ms'])} later ({entry['helper_end_reason']}).")
        last = entry["last_frame_acquire_after_mark_ms"]
        lines.append(f"{entry['frames_processed_after_mark']} frames came after the click"
                     + (f", the last {_seconds(last)} after it." if last is not None else "."))
        if entry["helper_end_after_last_frame_ms"] is not None:
            lines.append(f"The reader ended {_seconds(entry['helper_end_after_last_frame_ms'])} after its last frame.")
        if entry.get("presence_signal") is not None:
            line = (f"SteamVR's headset-off signal ({entry['presence_signal']}) came "
                    f"{_seconds(entry['presence_signal_after_mark_ms'])} after the click")
            if entry["helper_end_after_presence_signal_ms"] is not None:
                line += f"; the reader ended {_seconds(entry['helper_end_after_presence_signal_ms'])} after it"
            lines.append(line + ".")
        elif entry.get("presence_watch_armed"):
            lines.append("SteamVR sent no headset-off signal before the run ended.")
        else:
            lines.append("SteamVR's headset-off signal was not armed for this run.")
    p95 = (report.get("inference_ms") or {}).get("p95")
    if p95 is not None:
        lines.append(f"Detection time, 95th percentile: {p95:.0f} ms.")
    missed = (report.get("guide") or {}).get("steps_not_reached") or ()
    if missed:
        lines.append("Steps not reached: " + ", ".join(entry["label"] for entry in missed) + ".")
    return lines
