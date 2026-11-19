"""The guided bounded recognition run: its two scripts, what the run screen shows, and the result in plain words.

``camera_guide`` walks the wearer through one ``camera_recognize`` run in a
window, so nobody needs a keyboard, a terminal bell or a count of seconds
(owner request 2026-11-19). This module is the part with no window in it:
standard library only, no Qt, no camera, no pixel.

A script is a tuple of ``Step``s. Each step records one mark from the closed
vocabulary (``marks.VOCABULARY``) when it begins: at the wearer's click
(``ADVANCE_CLICK``) or by itself when the step before it has had its time
(``ADVANCE_CLOCK``). ``removal`` is two held distances and the click made just
before the headset comes off; ``positions`` is five places and out of view,
with no click after Start.

What the run screen shows while the camera reader is active is ``Guide.view``:
a function of the script, the run's time bound, the time the reader reported
ready, the wearer's clicks and the clock, and of nothing else. No detection,
label, count or other value derived from a camera frame is an input, so none
can be shown; ``tests/test_camera_guide.py`` pins that. Results are put into
words (``result_lines``) only from the finished report, which the window
receives after the helper has ended.

Nothing here opens a camera, lifts a gate or approves a run.
"""
from dataclasses import dataclass

from . import marks
from .confirmation import OPERATOR_WINDOW

MS = 1_000_000
ADVANCE_CLICK, ADVANCE_CLOCK = "click", "clock"
SCRIPT_REMOVAL, SCRIPT_POSITIONS = "removal", "positions"
# View states of the run screen.
STARTING, WAITING, RECORDING, FINISHED, OUT_OF_TIME, STOPPING = (
    "starting", "waiting", "recording", "finished", "out_of_time", "stopping")
# The wearer's observations: closed choices only, never free text.
START_COLOUR_STABLE, START_SOMETHING_ELSE = STARTING_STATES = ("colour_stable", "something_else")
PASSTHROUGH_UNCHANGED, PASSTHROUGH_CHANGED = PASSTHROUGH_ANSWERS = ("unchanged", "changed")
HEADSET_REMOVED_AND_BACK, HEADSET_NOT_REMOVED, HEADSET_NOT_ASKED = HEADSET_ANSWERS = (
    "removed_and_back", "not_removed", "not_asked")
# How a run's guidance ended.
ENDED_BY = (ENDED_HELPER, ENDED_STOP, ENDED_WINDOW_CLOSED, ENDED_CANCEL, ENDED_SIGNAL, ENDED_NOT_STARTED) = (
    "helper", "stop_button", "window_closed", "cancel_button", "signal", "not_started")
# Reasons of a run that never launched anything.
DECLINED, STARTING_STATE_NOT_STABLE = "confirmation_declined", "starting_state_not_stable"
WATCH_SECONDS = 30  # The passthrough watch after a run (the accepted passthrough rule).
# Pictures the window draws from shapes; none is an image and none comes from a camera.
PICTURES = ("wait", "near", "far", "headset_off", "centre", "left", "right", "up", "down", "out_of_view", "done")
GUIDE_SCHEMA_VERSION = 1


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
    recording: str = ""  # Shown while the step is recorded.


REMOVAL_STEPS = (
    Step("near", "Hold the {target} about half a metre in front of you, then click Ready", "near", ADVANCE_CLICK,
         5000, 5000, "Ready", "Keep holding it there"),
    Step("far", "Now about a metre away, then click Ready", "far", ADVANCE_CLICK, 5000, 5000, "Ready",
         "Keep holding it there"),
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
SCRIPTS = {SCRIPT_REMOVAL: REMOVAL_STEPS, SCRIPT_POSITIONS: POSITION_STEPS}
# The time bound a script asks for when the operator names none: the removal script waits on the wearer, so it takes
# the command's whole bound; the positions script is its steps and two seconds.
DEFAULT_SECONDS = {SCRIPT_REMOVAL: 30, SCRIPT_POSITIONS: 25}
assert all(step.label in marks.VOCABULARY and step.picture in PICTURES for steps in SCRIPTS.values() for step in steps)
TEXT_STARTING = "Starting. Please wait"
TEXT_STOPPING = "Stopping. Please wait"
TEXT_FINISHED = "That is all. The run ends by itself"
TEXT_OUT_OF_TIME = "There is not enough time left for the next step. The run ends by itself"


@dataclass(frozen=True)
class View:
    """What the run screen shows at one moment. Every field comes from the script, the clicks and the clock."""
    state: str
    instruction: str
    picture: str
    progress: float | None  # 0..1 while a step is recorded, else None.
    remaining_seconds: int | None  # Of the run's time bound; None until the reader is ready.
    button: str  # The click step's button, or "" for none.
    step_number: int
    step_count: int


class Guide:
    """One run of one script. Times are host monotonic nanoseconds the caller reads; nothing here reads a clock.

    ``ready`` starts the script's time (the helper's ``ready`` was accepted:
    the reader is active from here). ``advance`` moves the script on and
    returns the marks to record, ``(label, monotonic_ns)``, each made at the
    time given: a click's, or the tick on which a clock step's instruction
    appears. A step whose ``needs_ms`` is more than the time left does not
    begin: the script stops there (``OUT_OF_TIME``) and the steps from it on
    are ``not_reached``. ``scale`` shortens every step for the checks that
    run the whole flow in seconds; a device run uses 1.
    """

    def __init__(self, script, max_seconds, *, target="bottle", scale=1.0):
        if script not in SCRIPTS:
            raise ValueError(f"script must be one of {', '.join(SCRIPTS)}")
        if not 0 < scale <= 1:
            raise ValueError("scale must be above zero and at most one")
        self.script, self.max_seconds, self.target, self.scale = script, max_seconds, target, scale
        self.steps = SCRIPTS[script]
        self.ready_ns = None
        self.index = 0
        self.step_started_ns = None  # When the current step's mark was made; None while it has not begun.
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
        if self.ready_ns is None:
            return None
        return max(0.0, self.max_seconds * 1000 - (now_ns - self.ready_ns) / MS)

    def advance(self, now_ns, clicked=False):
        """Move the script to ``now_ns`` (``clicked``: the step's button was clicked now); the new marks."""
        new = []
        clicked = clicked and self.step_started_ns is None  # A click answers the step that was waiting for it.
        while self.ready_ns is not None and not (self.finished or self.out_of_time or self.stopping):
            step = self.steps[self.index]
            if self.step_started_ns is None:
                if step.advance == ADVANCE_CLICK and not clicked:
                    break
                clicked = False
                if self.remaining_ms(now_ns) < self._ms(step.needs_ms):
                    self.out_of_time = True
                    break
                self.step_started_ns = now_ns
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
        remaining = self.remaining_ms(now_ns)
        seconds = None if remaining is None else int(-(-remaining // 1000))
        step = self.steps[self.index]

        def shown(state, text, picture, progress=None, button=""):
            return View(state, text.format(target=self.target), picture, progress, seconds, button,
                        min(self.index + 1, count), count)
        if self.stopping:
            return shown(STOPPING, TEXT_STOPPING, "wait")
        if self.ready_ns is None:
            return shown(STARTING, TEXT_STARTING, "wait")
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


def static_texts(script, target="bottle"):
    """Every text the run screen can show for ``script``, apart from the numbers of the time and the step."""
    texts = {TEXT_STARTING, TEXT_STOPPING, TEXT_FINISHED, TEXT_OUT_OF_TIME}
    for step in SCRIPTS[script]:
        texts.update(text.format(target=target) for text in (step.instruction, step.recording, step.button) if text)
    return texts


def bounds_line(limits, camera):
    """The run's bounds as the notice screen states them."""
    side = {0: "left", 1: "right"}.get(camera, str(camera))
    return (f"At most {limits['max_seconds']} seconds and {limits['max_frames']} frames, {limits['fps']} frames a "
            f"second, the {side} camera.")


def guide_block(guide, *, script, started, ended_by, observations, limits_ms, validation_only=False, clock=None,
                recorded_marks=()):
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
            "reader_ready_monotonic_ns": None if guide is None else guide.ready_ns,
            "steps": steps,
            "steps_not_reached": [{"step": entry["step"], "label": entry["label"]} for entry in steps
                                  if not entry["reached"]],
            "out_of_time": bool(guide is not None and guide.out_of_time),
            "observations": dict(observations), "observation_choices": {
                "starting_state": list(STARTING_STATES), "passthrough_after": list(PASSTHROUGH_ANSWERS),
                "headset": list(HEADSET_ANSWERS)},
            "removal_limits_ms": dict(limits_ms), "watch_seconds_required": WATCH_SECONDS,
            "run_screen_inputs": ["script", "clock", "clicks", "reader_ready_time"],
            "shown_during_run": "scripted_instructions_progress_and_time_only"}


# ---- the result in plain words ------------------------------------------------------------------------------------

def _seconds(ms):
    return f"{ms / 1000:.1f} s"


def status_line(report):
    status, reason = report.get("status"), report.get("reason")
    if status == "recognized":
        return "The run finished on its bound."
    if status == "helper_ended_early" and reason in marks.PRESENCE_END_REASONS:
        return f"The run ended when the headset came off ({reason})."
    if status == "helper_ended_early":
        return f"The camera reader ended early ({reason})."
    if status == "stopped":
        return "The run was stopped."
    if status == "cancelled":
        return "The run was cancelled."
    if reason == DECLINED:
        return "Not started. Nothing was launched."
    if reason == STARTING_STATE_NOT_STABLE:
        return "Not started, because the view was not colour and stable. Nothing was launched. Tell the Frame manager."
    return f"The run did not work: {status}" + (f" ({reason})." if reason else ".")


def result_lines(report):
    """The finished report in plain words: counts, closed labels and timings only, each line one fact."""
    lines = [status_line(report)]
    if report.get("confirmation") is None:
        return lines
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
    for entry in summary.get("removal") or ():
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
    p95 = (report.get("inference_ms") or {}).get("p95")
    if p95 is not None:
        lines.append(f"Detection time, 95th percentile: {p95:.0f} ms.")
    missed = (report.get("guide") or {}).get("steps_not_reached") or ()
    if missed:
        lines.append("Steps not reached: " + ", ".join(entry["label"] for entry in missed) + ".")
    return lines
