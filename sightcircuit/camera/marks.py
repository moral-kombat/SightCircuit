"""Operator marks and the counts-only summaries of a bounded camera recognition run (work-order item L15).

A mark is a label from a closed vocabulary (``VOCABULARY``, plus ``KEY_LABEL``
for an unlabelled key press) with the host time it was made; there is no free
text anywhere. Marks come two ways. A cue is scheduled on the command line
(``parse_cue``: ``SECONDS:LABEL``, seconds from the first frame's acquire
time); when it is due ``MarkSession`` prints it on the operator's terminal
with a bell and records the scheduled and the actual time. A key mark is one
Enter on the terminal during the run, read only when the operator asked for
key marks (a session without a ``key_fd`` never touches the terminal): the
bytes typed are counted for line ends and discarded, never stored; each press
takes the next label of the operator's ``--mark-labels`` list, then
``KEY_LABEL``. A third way is for a program that guides the run
(``camera_guide``'s window): ``MarkSession.mark`` records a label from code,
with source ``window``, at the time the caller gives (a click's) or now; it
is bounded as key marks are and may be called from any thread.

Timebase. ``Clock`` samples the host's monotonic clock and the realtime clock
(UTC nanoseconds since the Unix epoch) once, at the run's start; every other
wall-clock time in a report is that anchor plus a monotonic difference
(``Clock.utc``), so the monotonic times stay the measurement and the
wall-clock ones only place it in the day. The camera helper stamps a frame
with the same host monotonic clock at acquisition.

``summarize`` derives counts from the per-frame records and the marks:
1-second windows with the target, coverage over the in-view spans, the 3x3
cell of the target's box centre per mark interval, and what followed a
``remove`` mark. Intervals and spans are bounded by a cue's scheduled time
(a key or window mark's actual time), so a nominal N-second span between two cues is N
windows whatever the wake-up jitter. The cell is taken on the image as exported: nothing here
knows the camera's mounting orientation (``ORIENTATION_STATEMENT``), which a
cued run is meant to reveal. Standard library only; no pixel, no calibration
value and no display. Nothing here opens a camera or lifts a gate.
"""
import os
import re
import select
import sys
import threading
import time

VOCABULARY = ("remove", "reseat", "near", "far", "left", "centre", "right", "up", "down", "out_of_view", "in_view")
KEY_LABEL = "mark"  # A key press beyond (or without) the operator's --mark-labels list.
POSITION_LABELS = ("left", "centre", "right", "up", "down")
IN_VIEW_LABELS = ("in_view", "near", "far", *POSITION_LABELS)  # A mark that says the target is in view from here.
OUT_OF_VIEW_LABELS = ("out_of_view", "remove")  # A mark that ends an in-view span.
REMOVE_LABEL = "remove"
SOURCES = SOURCE_CUE, SOURCE_KEY, SOURCE_WINDOW = ("cue", "key", "window")
MAX_CUES = 32
MAX_MARK_LABELS = 32
# Recorded marks of every kind. Every scheduled cue has its place: key and window marks together take what the cues
# leave (at least ``MAX_MARKS - MAX_CUES``), and further ones are counted, not recorded.
MAX_MARKS = 64
BOUNDARIES = BOUNDARY_SCHEDULED, BOUNDARY_ACTUAL = ("scheduled", "actual")
CUE_PATTERN = re.compile(r"(\d{1,2}(?:\.\d{1,3})?):([a-z_]+)")
WINDOW_NS = 1_000_000_000
MAX_WINDOWS = 64
# The 3x3 cell of a box centre over the full frame (work-order item L6): below 0.4 is the first third's name, above
# 0.6 the last's, on each axis.
CELL_THRESHOLDS = (0.4, 0.6)
COLUMNS = ("left", "centre", "right")
ROWS = ("up", "centre", "down")
CELLS = tuple(column if row == "centre" else row if column == "centre" else f"{row}_{column}"
              for row in ROWS for column in COLUMNS)
# L-D1's removal acceptance: no frame processed with an acquire time over 2.0 s after the mark, the helper's end at
# most 3.0 s after it, for a reason that is the presence stop. These are ``summarize``'s defaults; a caller whose
# mark is made before the headset comes off (a window's click) passes the limits its acceptance names.
LATE_FRAME_LIMIT_MS = 2000
REMOVAL_END_LIMIT_MS = 3000
PRESENCE_END_REASONS = ("headset_removed", "standby")
ENDED_BY_WATCH, ENDED_BY_HELPER = "presence_watch", "helper"  # A removal entry's ``run_ended_by``.
# What a command reports for a run the presence watch (``presence.py``) ended, lost or could not arm.
STATUS_PRESENCE_ENDED, STATUS_WATCH_LOST = "presence_ended", "presence_watch_lost"
REASON_WATCH_NOT_ARMED = "presence_watch_not_armed"
RUN_END = "run_end"
TIMEBASE = {"monotonic_clock": "host_clock_monotonic_ns", "wall_clock": "utc_ns_since_unix_epoch",
            "wall_clock_derivation": "anchor_utc_ns_plus_monotonic_difference",
            "frame_time": "helper_host_monotonic_ns_at_acquire", "cue_origin": "first_frame_acquire_time"}
ORIENTATION_STATEMENT = (
    "Cells are taken on the image as the helper exported it. Nothing in this command knows the camera's mounting "
    "orientation, so left, right, up and down name sides of the exported image, not of the wearer's view; the cued "
    "run is what shows how the two relate.")
KEY_POLL_SECONDS = 0.05
JOIN_SECONDS = 2.0


def check_label(label, extra=()):
    """``label`` when it is in the vocabulary (or ``extra``), else ``ValueError``."""
    if type(label) is not str or (label not in VOCABULARY and label not in extra):
        raise ValueError(f"mark label must be one of {', '.join(VOCABULARY)}")
    return label


def parse_cue(text, max_seconds):
    """``(offset_ms, label)`` of a ``SECONDS:LABEL`` cue within ``max_seconds``, else ``ValueError``."""
    match = CUE_PATTERN.fullmatch(text) if type(text) is str else None
    if match is None:
        raise ValueError("a cue is SECONDS:LABEL, as 12:far or 7.5:left")
    offset_ms = round(float(match.group(1)) * 1000)
    if offset_ms > max_seconds * 1000:
        raise ValueError(f"a cue must fall within the run's {max_seconds} seconds")
    return offset_ms, check_label(match.group(2))


def check_cues(cues):
    """``cues`` as a tuple of ``(offset_ms, label)`` at strictly rising offsets, at most ``MAX_CUES``."""
    cues = tuple((int(offset), check_label(label)) for offset, label in cues)
    if len(cues) > MAX_CUES:
        raise ValueError(f"at most {MAX_CUES} cues")
    if any(offset < 0 for offset, _ in cues) or any(later[0] <= earlier[0] for earlier, later in zip(cues, cues[1:])):
        raise ValueError("cues must be given at strictly rising times")
    return cues


def check_mark_labels(labels):
    """The operator's key-mark labels, each from the vocabulary, at most ``MAX_MARK_LABELS``."""
    labels = tuple(check_label(label) for label in labels)
    if len(labels) > MAX_MARK_LABELS:
        raise ValueError(f"at most {MAX_MARK_LABELS} mark labels")
    return labels


class Clock:
    """One sample of the monotonic and the realtime clock; wall-clock times derive from it."""

    def __init__(self, monotonic_ns=None, utc_ns=None):
        self.monotonic_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        self.utc_ns = time.time_ns() if utc_ns is None else utc_ns

    def utc(self, monotonic_ns):
        return self.utc_ns + (monotonic_ns - self.monotonic_ns)

    def stamp(self, monotonic_ns=None):
        """``{monotonic_ns, utc_ns}`` of a monotonic time (now when none is given)."""
        monotonic_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        return {"monotonic_ns": monotonic_ns, "utc_ns": self.utc(monotonic_ns)}

    def report(self):
        """The declared timebase with the anchor and a second sample, whose difference is the realtime clock's step."""
        now = Clock()
        return dict(TIMEBASE, anchor={"monotonic_ns": self.monotonic_ns, "utc_ns": self.utc_ns},
                    anchor_at_report={"monotonic_ns": now.monotonic_ns, "utc_ns": now.utc_ns},
                    wall_clock_step_ns=now.utc_ns - self.utc(now.monotonic_ns))


class MarkSession:
    """The marks of one run: a cue thread, a key thread, and the recorded list.

    ``cues`` are ``(offset_ms, label)`` from the origin, which ``first_frame``
    sets to the first frame's acquire time; ``labels`` the key-mark labels;
    ``key_fd`` the terminal's descriptor to read Enter presses from, or
    ``None`` for no key marks; ``output`` where a due cue and a recorded key
    mark are announced (the operator's terminal, never the report stream).
    Without cues and without a key press nothing is written and nothing is
    recorded.
    """

    def __init__(self, clock, cues=(), labels=(), key_fd=None, output=None):
        self.clock, self.cues, self.labels = clock, check_cues(cues), check_mark_labels(labels)
        self.key_fd, self.output = key_fd, output
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.origin_set = threading.Event()
        self.origin_ns = None
        self.recorded = []
        self.cues_fired = 0
        self.key_presses = self.key_recorded = self.dropped = 0
        self.window_marks = 0  # ``mark`` calls, recorded or not.
        self.key_capacity = MAX_MARKS - len(self.cues)
        self.key_error = None
        self.threads = []

    def start(self):
        if self.cues:
            self.threads.append(threading.Thread(target=self._run_cues, name="camera-marks-cues", daemon=True))
        if self.key_fd is not None:
            self._discard_pending_input()
            self.threads.append(threading.Thread(target=self._run_keys, name="camera-marks-keys", daemon=True))
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(JOIN_SECONDS)

    def first_frame(self, observed_ns):
        """Set the cue origin once, to the first frame's acquire time."""
        if self.origin_ns is None:
            self.origin_ns = observed_ns
            self.origin_set.set()

    def _announce(self, text):
        if self.output is None:
            return
        try:
            self.output.write(text)
            self.output.flush()
        except (OSError, ValueError):
            pass

    def _record(self, source, label, monotonic_ns, scheduled=None):
        with self.lock:
            if source != SOURCE_CUE:  # A cue always has its place; key and window marks never take one.
                if self.key_recorded >= self.key_capacity:
                    self.dropped += 1
                    return False
                self.key_recorded += 1
            mark = {"index": len(self.recorded) + 1, "source": source, "label": label, **self.clock.stamp(monotonic_ns),
                    "offset_ms": None if self.origin_ns is None else round((monotonic_ns - self.origin_ns) / 1e6, 3)}
            if scheduled is not None:
                offset_ms, scheduled_ns = scheduled
                mark.update(scheduled_offset_ms=offset_ms, scheduled_monotonic_ns=scheduled_ns,
                            scheduled_utc_ns=self.clock.utc(scheduled_ns),
                            late_ms=round((monotonic_ns - scheduled_ns) / 1e6, 3))
            self.recorded.append(mark)
            return True

    def _run_cues(self):
        while not self.origin_set.wait(KEY_POLL_SECONDS):
            if self.stop_event.is_set():
                return
        for offset_ms, label in self.cues:
            due_ns = self.origin_ns + offset_ms * 1_000_000
            while True:
                remaining = (due_ns - time.monotonic_ns()) / 1e9
                if remaining <= 0:
                    break
                if self.stop_event.wait(remaining):
                    return
            if self.stop_event.is_set():
                return
            self._record(SOURCE_CUE, label, time.monotonic_ns(), (offset_ms, due_ns))
            self._announce(f"\aCUE {label}\n")
            self.cues_fired += 1

    def _discard_pending_input(self):
        """Drop what was typed before the run (after the confirmation's line): only presses during the run are marks."""
        try:
            import termios
        except ImportError:
            return
        try:
            if os.isatty(self.key_fd):
                termios.tcflush(self.key_fd, termios.TCIFLUSH)
        except (OSError, termios.error):  # ``termios.error`` is not an ``OSError`` on every supported Python.
            pass

    def _run_keys(self):
        try:
            while not self.stop_event.is_set():
                readable, _, _ = select.select([self.key_fd], [], [], KEY_POLL_SECONDS)
                if not readable:
                    continue
                now = time.monotonic_ns()
                data = os.read(self.key_fd, 4096)  # Counted for line ends and discarded; never stored.
                if not data:
                    return
                for _ in range(data.count(b"\n")):
                    with self.lock:
                        label = self.labels[self.key_presses] if self.key_presses < len(self.labels) else KEY_LABEL
                        self.key_presses += 1
                    if self._record(SOURCE_KEY, label, now):
                        self._announce(f"MARK {label}\n")
        except (OSError, ValueError) as error:
            self.key_error = type(error).__name__

    def mark(self, label, monotonic_ns=None):
        """Record ``label`` from code (a guiding window's click) with source ``window``; True when recorded.

        ``label`` is from the vocabulary (else ``ValueError``); ``monotonic_ns``
        is when it was made on the host monotonic clock, now when not given.
        Bounded as key marks are (one shared capacity; the overflow is
        counted in ``marks_dropped``); nothing is announced. Any thread may
        call it; a call after ``stop`` records nothing.
        """
        check_label(label)
        if monotonic_ns is not None and type(monotonic_ns) is not int:
            raise ValueError("a mark's time is integer nanoseconds of the host monotonic clock")
        if self.stop_event.is_set():
            return False
        with self.lock:
            self.window_marks += 1
        return self._record(SOURCE_WINDOW, label, time.monotonic_ns() if monotonic_ns is None else monotonic_ns)

    def marks(self):
        """The recorded marks in time order, renumbered from 1."""
        with self.lock:
            ordered = sorted(self.recorded, key=lambda mark: (mark["monotonic_ns"], mark["index"]))
        return [dict(mark, index=index) for index, mark in enumerate(ordered, 1)]

    def report(self):
        return {"vocabulary": list(VOCABULARY), "key_label": KEY_LABEL,
                "cues_requested": [{"offset_ms": offset, "label": label} for offset, label in self.cues],
                "cues_not_reached": [{"offset_ms": offset, "label": label} for offset, label in self.cues[self.cues_fired:]],
                "mark_labels_requested": list(self.labels), "key_marks_enabled": self.key_fd is not None,
                "key_presses": self.key_presses, "key_listener_error": self.key_error, "marks_dropped": self.dropped,
                "maximum_marks": MAX_MARKS, "key_marks_capacity": self.key_capacity, "window_marks": self.window_marks,
                "recorded": self.marks()}


def terminal_fd(stream=None):
    """The descriptor of ``stream`` (stdin) when it is a real terminal to read key marks from, else ``None``."""
    stream = sys.stdin if stream is None else stream
    try:
        fd = stream.fileno()
        return fd if type(fd) is int and os.isatty(fd) else None
    except (AttributeError, OSError, ValueError):
        return None


# -- the target and its cell -----------------------------------------------------------------------------------------

def third(fraction, names):
    low, high = CELL_THRESHOLDS
    return names[0] if fraction < low else names[2] if fraction > high else names[1]


def cell_name(column, row):
    return column if row == "centre" else row if column == "centre" else f"{row}_{column}"


def cell_of(box_xyxy, width, height):
    """``{column, row, cell, centre_fraction}`` of a box's centre over a ``width`` x ``height`` frame."""
    x = (box_xyxy[0] + box_xyxy[2]) / 2 / width
    y = (box_xyxy[1] + box_xyxy[3]) / 2 / height
    column, row = third(x, COLUMNS), third(y, ROWS)
    return {"column": column, "row": row, "cell": cell_name(column, row), "centre_fraction": [round(x, 4), round(y, 4)]}


def target_of(detections, label, threshold, width, height):
    """The per-frame target record: the best box of ``label`` at or above ``threshold`` and the cell of its centre.

    ``detections`` are the detector's (``label``, ``confidence``, ``box_xyxy``
    in pixels of the ``width`` x ``height`` image it ran on, which has the
    full frame's aspect).
    """
    best = None
    count = 0
    for item in detections:
        if item.label == label and item.confidence >= threshold:
            count += 1
            if best is None or item.confidence > best.confidence:
                best = item
    if best is None:
        return {"present": False, "count": 0, "confidence": None, "box_xyxy": None, "column": None, "row": None,
                "cell": None, "centre_fraction": None}
    return {"present": True, "count": count, "confidence": best.confidence,
            "box_xyxy": [int(value) for value in best.box_xyxy], **cell_of(best.box_xyxy, width, height)}


# -- summaries -------------------------------------------------------------------------------------------------------

def _counts(frames, with_target):
    """Frame and target counts of ``frames``, with the cell tallies when a target was named."""
    counts = {"frames": len(frames)}
    if with_target:
        present = [frame["target"] for frame in frames if frame["target"]["present"]]
        counts.update(target_frames=len(present),
                      cells={name: sum(1 for target in present if target["cell"] == name) for name in CELLS},
                      columns={name: sum(1 for target in present if target["column"] == name) for name in COLUMNS},
                      rows={name: sum(1 for target in present if target["row"] == name) for name in ROWS})
    return counts


def _windows(frames, start_ns, end_ns, with_target):
    """Whole 1-second windows from ``start_ns`` that end by ``end_ns``: how many, with a frame, with the target."""
    whole = max(0, min((end_ns - start_ns) // WINDOW_NS, MAX_WINDOWS))
    with_frames, with_hit = set(), set()
    for frame in frames:
        index = (frame["observed_ns"] - start_ns) // WINDOW_NS
        if 0 <= index < whole:
            with_frames.add(index)
            if with_target and frame["target"]["present"]:
                with_hit.add(index)
    return {"windows": whole, "windows_with_frames": len(with_frames),
            "windows_with_target": len(with_hit) if with_target else None,
            "remainder_ms": round(max(0, end_ns - start_ns - whole * WINDOW_NS) / 1e6, 3)}


def boundary_ns(mark):
    """Where a mark bounds an interval: a cue's scheduled time, a key or window mark's actual time."""
    return mark.get("scheduled_monotonic_ns", mark["monotonic_ns"])


def _between(frames, start_ns, end_ns):
    return [frame for frame in frames if start_ns <= frame["observed_ns"] < end_ns]


def summarize(frames, marks, *, origin_ns, run_end_ns, helper_end=None, with_target=False,
              late_frame_limit_ms=LATE_FRAME_LIMIT_MS, removal_end_limit_ms=REMOVAL_END_LIMIT_MS, presence=None):
    """Counts-only summaries of one run.

    ``frames`` are the per-frame records (``observed_ns``, and ``target`` from
    ``target_of`` when ``with_target``), ``marks`` the recorded marks in time
    order, ``origin_ns`` the first frame's acquire time (``None`` when no frame
    arrived), ``run_end_ns`` where the last interval ends (the helper's end
    record, else the export's return) and ``helper_end`` ``{monotonic_ns,
    reason}`` of the helper's end record when one was read.

    ``windows``: 1-second windows from the origin to the last processed
    frame, each with its frame and target-frame counts. ``intervals``: per
    mark, from it to the next mark or the run's end, the frame counts, the
    cell tallies and the whole windows from the mark. ``coverage``: over the
    spans that open at an in-view mark (``IN_VIEW_LABELS``) and close at the
    next ``OUT_OF_VIEW_LABELS`` mark or the run's end, the whole windows from
    the span's start and how many held the target. ``removal``: per ``remove``
    mark, the frames processed with an acquire time over
    ``late_frame_limit_ms`` after it and the time to the helper's end record
    (compared with ``removal_end_limit_ms``), both from the mark's actual time
    (when the cue was shown, the key pressed or the window clicked), and the
    time from the run's last processed frame to that end record
    (``helper_end_after_last_frame_ms``: what a mark made before the headset
    comes off cannot give). The two limits are the caller's; the defaults are
    L-D1's. Intervals and spans are bounded by ``boundary_ns``: a cue's
    scheduled time, a key or window mark's actual time. The tallies do not separate cameras: the caller
    gives the frames of one camera when a target is named.

    ``presence`` is the presence watch's summary (``PresenceWatch.summary``:
    ``armed``, ``ended_run``, the ending ``signal`` with its ``end_reason``
    and ``monotonic_ns``, ``frames_received_after_signal``), or ``None`` for
    a run without the watch. Each removal entry then says whether the watch
    was armed (``presence_watch_armed``), which signal came
    (``presence_signal``), the time from the mark to it
    (``presence_signal_after_mark_ms``) and from it to the helper's end
    record (``helper_end_after_presence_signal_ms``), the frames received
    after it, what ended the run (``run_ended_by``: ``presence_watch`` or
    ``helper``) and for which reason (``run_end_reason``: the signal's, else
    the helper's own). ``helper_end_reason`` stays the helper's own end
    record (``stop_signal`` when the watch stopped it), and
    ``helper_end_is_presence_stop`` is true when either the helper ended for
    a presence reason or the watch's signal ended the run.
    """
    frames = sorted(frames, key=lambda frame: frame["observed_ns"])
    marks = sorted(marks, key=boundary_ns)
    windows = []
    if origin_ns is not None and frames:
        last = min((frames[-1]["observed_ns"] - origin_ns) // WINDOW_NS, MAX_WINDOWS - 1)
        for index in range(last + 1):
            inside = _between(frames, origin_ns + index * WINDOW_NS, origin_ns + (index + 1) * WINDOW_NS)
            window = {"index": index, "frames": len(inside)}
            if with_target:
                window["target_frames"] = sum(1 for frame in inside if frame["target"]["present"])
            windows.append(window)
    intervals = []
    for position, mark in enumerate(marks):
        start = boundary_ns(mark)
        end = boundary_ns(marks[position + 1]) if position + 1 < len(marks) else max(run_end_ns, start)
        inside = _between(frames, start, end)
        intervals.append({"mark_index": mark["index"], "label": mark["label"], "source": mark["source"],
                          "boundary": BOUNDARY_SCHEDULED if "scheduled_monotonic_ns" in mark else BOUNDARY_ACTUAL,
                          "duration_ms": round((end - start) / 1e6, 3), **_counts(inside, with_target),
                          **_windows(inside, start, end, with_target)})
    spans, opened = [], None
    for mark in marks:
        if opened is None and mark["label"] in IN_VIEW_LABELS:
            opened = {"mark_index": mark["index"], "labels": [mark["label"]], "start": boundary_ns(mark)}
        elif opened is not None and mark["label"] in IN_VIEW_LABELS:
            opened["labels"].append(mark["label"])
        elif opened is not None and mark["label"] in OUT_OF_VIEW_LABELS:
            spans.append(dict(opened, end=boundary_ns(mark), ended_by=mark["label"]))
            opened = None
    if opened is not None:
        spans.append(dict(opened, end=max(run_end_ns, opened["start"]), ended_by=RUN_END))
    coverage = None
    if with_target:
        listed = []
        for span in spans:
            start, end = span.pop("start"), span.pop("end")
            inside = _between(frames, start, end)
            listed.append({**span, "duration_ms": round((end - start) / 1e6, 3), "frames": len(inside),
                           "target_frames": sum(1 for frame in inside if frame["target"]["present"]),
                           **_windows(inside, start, end, True)})
        total = sum(span["windows"] for span in listed)
        hit = sum(span["windows_with_target"] for span in listed)
        coverage = {"spans": listed, "windows": total, "windows_with_target": hit,
                    "windows_without_frames": total - sum(span["windows_with_frames"] for span in listed),
                    "fraction": round(hit / total, 4) if total else None}
    removal = []
    for mark in marks:
        if mark["label"] != REMOVE_LABEL:
            continue
        start = mark["monotonic_ns"]
        after = [frame["observed_ns"] - start for frame in frames if frame["observed_ns"] > start]
        entry = {"mark_index": mark["index"], "source": mark["source"], "frames_processed_after_mark": len(after),
                 "late_frame_limit_ms": late_frame_limit_ms,
                 "frames_acquired_over_limit": sum(1 for delta in after if delta > late_frame_limit_ms * 1_000_000),
                 "last_frame_acquire_after_mark_ms": round(max(after) / 1e6, 3) if after else None,
                 "end_limit_ms": removal_end_limit_ms, "helper_end_after_mark_ms": None, "helper_end_reason": None,
                 "helper_end_is_presence_stop": None, "helper_end_within_limit": None,
                 "helper_end_after_last_frame_ms": None,
                 "presence_watch_armed": bool(presence and presence["armed"]), "presence_signal": None,
                 "presence_signal_after_mark_ms": None, "helper_end_after_presence_signal_ms": None,
                 "frames_received_after_presence_signal": None, "run_ended_by": None, "run_end_reason": None}
        by_watch = bool(presence and presence["ended_run"])
        if presence and presence["signal"] is not None:
            entry.update(presence_signal=presence["signal"],
                         presence_signal_after_mark_ms=round((presence["monotonic_ns"] - start) / 1e6, 3),
                         frames_received_after_presence_signal=presence["frames_received_after_signal"])
        if by_watch:
            entry.update(run_ended_by=ENDED_BY_WATCH, run_end_reason=presence["end_reason"])
        if helper_end is not None:
            elapsed_ms = round((helper_end["monotonic_ns"] - start) / 1e6, 3)
            entry.update(helper_end_after_mark_ms=elapsed_ms, helper_end_reason=helper_end["reason"],
                         helper_end_is_presence_stop=by_watch or helper_end["reason"] in PRESENCE_END_REASONS,
                         helper_end_within_limit=0 <= elapsed_ms <= removal_end_limit_ms)
            if not by_watch:
                entry.update(run_ended_by=ENDED_BY_HELPER, run_end_reason=helper_end["reason"])
            if presence and presence["signal"] is not None:
                entry["helper_end_after_presence_signal_ms"] = round(
                    (helper_end["monotonic_ns"] - presence["monotonic_ns"]) / 1e6, 3)
            if frames:
                entry["helper_end_after_last_frame_ms"] = round(
                    (helper_end["monotonic_ns"] - frames[-1]["observed_ns"]) / 1e6, 3)
        removal.append(entry)
    return {"window_seconds": WINDOW_NS // 1_000_000_000, "windows": windows, "intervals": intervals,
            "coverage": coverage, "removal": removal}
