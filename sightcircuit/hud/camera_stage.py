"""The camera HUD stage (work-order item L8): camera frame, detector, rows, tile, observed frame, report.

``CameraHudStage`` joins the three parts the camera display work order built
separately: the left-camera frame source (``sources.camera_helper``), the
mask-free row selector (``hud.camera.camera_rows``) with the tile drawing
(``hud.render.draw_tile``), and the bridge's observed-frame path
(``observed_frames.ObservedFrameClient``). For every frame the pipeline processes it
runs the detector, selects rows, draws the tile and hands the bridge one
observed frame whose ``observed_ns`` is the camera frame's own, whose identity
is the camera frame's sequence, and whose deadline is ``observed_ns +
MAX_DISPLAY_AGE_NS`` (earlier only when the run's own deadline is).

Rules, each pinned by ``tests/test_camera_hud_stage.py``:

* A result whose observation is already one maximum display age old is never
  sent. Every newer result with rows is sent: the bridge renews a deadline
  only for a newer observation, so the same rows from a newer frame are what
  keeps a label up.
* A result with no rows takes the tile down at once (a hide request) instead
  of leaving it to expire, and the next result with rows shows it again.
* With nothing to show the client's keepalive holds the connection.
* When the run ends for any reason (its bound, a stop, a presence end, an
  error, the bridge ending) a watcher thread ends the client the moment
  ``stop`` is set, without waiting for the detector: the client then accepts
  no frame and writes its hide. The hide is a packet behind whatever frame
  the writer was already writing, so it can trail the end by that frame's
  write and upload; the report's ``end`` block gives both times.
* A slow or stopped detector sends nothing, and the bridge clears the tile by
  itself at its deadline.
* A bridge that ends or a client that fails sets ``stop`` at once, whatever
  the detector is doing, so the camera helper never runs on without a
  display path.

Real output is gated: ``run`` asks the client's ``check_start`` first, which
refuses ``--fixed-hud`` through the ``LIVE_HUD_OUTPUT`` gate, and then, for a
client in real output, ``camera_display_input_permitted`` through the
``LIVE_CAMERA_INPUT`` gate, both before a helper or a bridge is launched.
Each permits only under a one-run scope record that verifies
(``runtime/scope.py``, work-order item L10). The stage opens no gate, reads
no mapping record and saves no image.

What was on display is taken from the bridge's own lines, never from this
side's writes: a spell starts at a ``frame_presented`` line (or the show that
lets a hidden tile up) and ends at the next one, a hide, or the bridge's
``frame_expired``. A frame the bridge refused or never acknowledged is booked
nowhere. Those are the times the bridge's calls returned; what a headset
displayed is not known here.

Two layouts and a region of view (work-order items B5 and L17). The stage
draws for one of the bridge's fixed layouts, the one its client names: the
strip, as above, or the box panel, for which each result becomes panel
boxes (``boxes.camera_boxes`` through the run's ``Alignment``) drawn by
``box_render.draw_boxes`` and sent as the panel's 512 x 512 RGBA image.
Everything else, the deadline, the acknowledgements, the ledger, the end
and the bridge-death path, is the same code for both. With a ``region``
(``camera.region.Region``) the detector is given that rectangle of the
frame, turned by its quarter turn (``region.prepare``), and its boxes are
taken back to the whole frame (``Mapping.result``) before rows or boxes are
selected; the strip with the whole frame, unturned, is the run it always
was. The rule for a result with nothing to show is one rule: no rows, or no
boxes, takes the tile down at once with a hide request, and nothing is sent
(an all-transparent panel is never uploaded); the next result with
something to show brings it back.

The report (kind ``camera_hud_stage``) holds counts, labels, timings and
format metadata. ``check_report`` is its schema; see its docstring for what
it enforces and what it does not.
"""
import json
import math
import numbers
import operator
import re
import threading
import time

from .. import gates
from ..camera import contract as camera_contract
from ..camera import region as regions
from ..convert import to_bgr
from ..detection import POC_THRESHOLD
from ..frame import Detection
from ..gates import LIVE_CAMERA_INPUT
from ..observed_frames import BRIDGE_COUNTS, MAX_ACKNOWLEDGEMENTS, ObservedFrameClient
from ..overlay import BRIDGE_INPUT_POLL_NS, CLOSE_REPORT_FIELDS, rgba_from_bgr
from ..pipeline import PERCENTILE_METHOD, TIMINGS, Pipeline, TimingSample
from ..runtime.manifest import CAMERA_HELPER as PINNED_HELPER
from ..runtime.scope import CAMERA_INPUT, CAMERA_LEFT, WHOLE_FRAME, RunScope, pinned_binary, view_value
from ..sources.camera_helper import LEFT_CAMERA, CameraHelperSource
from .box_render import draw_boxes
from .boxes import BOX_REPORT_KEYS, Alignment, boxes_report, camera_boxes
from .camera import MAX_DETECTIONS, ORIENTATIONS, camera_rows
from .contract import (FIXED_LAYOUTS, HUD_CONFIG, MAX_DISPLAY_AGE_MS, MAX_DISPLAY_AGE_NS, RGBA_BYTES, ROW_LABEL_CHARS,
                       BoxPanelConfig, config_digest, row_label)
from .render import draw_tile

__all__ = ("CAMERA_INPUT_REFUSAL", "MAX_REPORT_BYTES", "REPORT_KIND", "CameraHudStage", "DisplayLedger",
           "bridge_retired_itself",
           "camera_display_input_permitted", "check_report", "named_in_window", "report_value", "window_verdict")

REPORT_KIND = "camera_hud_stage"
STATUSES = ("not_run", "completed", "stopped", "presence_ended", "cancelled", "error")
WATCHER_JOIN_SECONDS = 5.0
WATCHER_POLL_SECONDS = 0.02  # How often the watcher looks at the client between stops.

# ---- the report's schema ------------------------------------------------------------------------------------------

# A name, code, digest or version: starts with a letter, digit, ``_`` or ``-``, no white space, no path
# separator except one trailing ``/<digits>`` (a protocol version), at most 72 characters (a SHA-256 is 64).
IDENTIFIER = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.:-]{0,71}(/[0-9]{1,4})?\Z")
LABEL_KEY = "label"  # The one key whose string is a row label (``contract.row_label``) and may hold spaces.
MAX_SCALAR_LIST = 8  # A pixel size, a camera list: integers, booleans, identifiers or null; never floats.
MAX_RECORD_LIST = 256  # Records (spell intervals, per-label rows, ``frame_expired`` events).
MAX_KEYS = 48  # Keys of one object.
MAX_DEPTH = 8
INTEGER_RANGE = (-2 ** 63, 2 ** 64)  # What a 64-bit counter or nanosecond clock can hold; nothing wider.
# The whole report, JSON-encoded: 10.2 to 10.9 kB of counts and metadata (the higher figure on the box panel), up
# to MAX_SPELLS intervals (about 45 bytes each, 11.5 kB) and up to MAX_REPORTED_EXPIRIES expiry events (about 400
# bytes each, 12.8 kB), plus margin. Measured in review of item B5 on full runs with 5 labels and every budget
# filled: 35.9 kB on the strip and 36.7 kB on the panel.
MAX_REPORT_BYTES = 40_960
MAX_SPELLS = 256  # Display intervals kept over all labels; later ones are counted in ``spells_dropped``.
# With a target label (the class a run's acceptance verdict is about) the budget is split, so that other labels
# flickering can never drop one of the target's intervals and void its verdict: the target has this many of its
# own (``target_spells_dropped`` counts what exceeds them) and every other label shares the rest.
MAX_TARGET_SPELLS = 128
MAX_REPORTED_EXPIRIES = 32  # ``frame_expired`` events repeated in the report; the count covers them all.
# Keys of the embedded source and bridge reports that hold prose, paths or argument vectors: left out by name.
WITHHELD_KEYS = ("error", "detail", "path", "arguments", "definition", "scope", "observed_ns_clock")
BOXES_BLOCK = (*("boxes_selected" if name == "boxes_shown" else name for name in BOX_REPORT_KEYS),
               "boxes_sent", "results_counted", "results_sent")
# Every top-level field of the report, and for each block the keys it may hold (None: a scalar field).
REPORT_BLOCKS = {
    "report_kind": None, "schema_version": None, "status": None, "reason": None, "error_type": None,
    **dict.fromkeys(gates.claims()),  # The gate claims, named by the gates alone.
    "validation_only": None, "bridge_mode": None, "images_saved": None, "pixels_in_report": None,
    "physical_display_observed": None, "fields_withheld": None,
    "config": ("hud_config_id", "hud_config_sha256", "tile_px", "rows", "max_boxes", "threshold", "directions",
               "orientation", "alignment", "max_fps", "max_frames", "seconds", "max_display_age_ms",
               "keepalive_seconds", "update_bytes"),
    # The region of view the detector was given (item L17): fractions of the native frame, the quarter turn, and
    # where that lay in the first frame. ``prepared`` says whether the frame went through ``region.prepare``.
    "view": ("region", "rotation", "whole_frame", "prepared", "box_coordinate_space", "source_space_px",
             "crop_native_xyxy", "resized_px", "geometry_constant", "invalid_detections"),
    # The box layout's counts (item B5), null on the strip: ``boxes.boxes_report`` of every counted result,
    # summed. ``boxes_selected`` is that block's ``boxes_shown`` under the name of what it counts here: boxes
    # selected and drawn, whether or not the result was then sent. ``results_sent`` and ``boxes_sent`` count the
    # results the client accepted for the bridge and the boxes in them.
    "boxes": BOXES_BLOCK,
    "identities": ("frame_identity", "first_frame_id", "last_frame_id", "last_result_index", "source_id"),
    "results": ("results", "results_with_rows", "results_without_rows", "results_too_old", "results_after_end"),
    "frames": ("submitted", "sent", "not_sent", "written_past_deadline", "acknowledged_presented",
               "acknowledged_refused", "refused_reasons", "unacknowledged", "bridge_received", "bridge_accepted",
               "retransmitted", "refused", "expired", "expired_in_upload"),
    "deadlines": ("cap_ms", "max_deadline_after_observation_ms", "within_cap"),
    "end": ("run_start_ns", "ended_ns", "timebase", "tile_already_down", "hide_written_ns", "hide_written_after_end_ms",
            "bridge_hide_returned_ns", "bridge_hide_after_end_ms", "bridge_ended_first", "bridge_left_on_signal"),
    "timings": ("percentile_method", "input_age_ms", "inference_ms", "processing_ms", "result_age_ms", "output_age_ms",
                "capture_to_display_age_ms"),
    # This side's own work per result, step by step (``STEPS``), beside the pipeline's ``stage_ms``.
    "steps_ms": ("percentile_method", "clock", "unit", *("map", "select", "draw", "pack", "queue")),
    "frame_expired": ("count", "recorded", "dropped", "bridge_poll_ms", "max_noticed_after_deadline_ms",
                      "max_hide_returned_after_deadline_ms", "hide_times_reported", "events"),
    "labels": ("basis", "timebase", "run_start_ns", "run_end_ns", "spells_dropped", "target_label",
               "target_spells_dropped", "physical_display_observed", "rows"),
    "pipeline": ("frames_received", "frames_processed", "frames_submitted", "stale_inputs_skipped",
                 "stale_results_skipped", "pending_replaced", "elapsed_ms", "processed_fps", "cancelled",
                 "sink_clear_ok", "sink_close_ok", "stage_ms", "capture_thread_alive"),
    "source": ("report_kind", "schema_version", "kind", "source_id", "camera_index", "protocol", "status", "reason",
               "end_reason", "presence_ended", "retried", "limits", "frames_emitted", "frames_refused",
               "frames_after_stop", "cleanup_ok", "helper_exit_code", "helper_exit_ok", "images_saved",
               "pixels_in_report", "timings", "export"),
    "bridge": (*CLOSE_REPORT_FIELDS, "started", "mode", "frames_submitted", "frames_replaced", "frames_withdrawn",
               "frames_expired_before_send", "frames_retransmitted", "take_downs", "shows", "keepalives",
               "visibility_requests", "settings_sent", "frames_presented", "frames_refused", "error", "lines",
               "first_frame", "final_report"),
    "cleanup": ("source_ok", "bridge_ok", "sinks_ok", "ok"),
}


def _label(value):
    return isinstance(value, str) and 0 < len(value) <= ROW_LABEL_CHARS and row_label(value) == value


def _leaf(value, key, in_list=False):
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return INTEGER_RANGE[0] <= value < INTEGER_RANGE[1]
    if type(value) is float:
        return math.isfinite(value) and not in_list
    if type(value) is str:
        return _label(value) if key == LABEL_KEY else IDENTIFIER.match(value) is not None
    return False


def _check(value, path, key, depth):
    def refuse():
        raise ValueError(f"camera_hud_report_field_invalid:{path}")
    if depth > MAX_DEPTH:
        refuse()
    if type(value) is dict:
        if len(value) > MAX_KEYS:
            refuse()
        for name, item in value.items():
            if type(name) is not str or IDENTIFIER.match(name) is None:
                refuse()
            _check(item, f"{path}.{name}", name, depth + 1)
    elif type(value) is list:
        records = [item for item in value if type(item) is dict]
        if len(records) not in (0, len(value)) or len(value) > (MAX_RECORD_LIST if records else MAX_SCALAR_LIST):
            refuse()
        for index, item in enumerate(value):
            if records:
                _check(item, f"{path}[{index}]", key, depth + 1)
            elif not _leaf(item, key, in_list=True):
                refuse()
    elif not _leaf(value, key):
        refuse()


def check_report(report, blocks=None, max_bytes=None):
    """Raise ``ValueError('camera_hud_report_field_invalid:<path>')`` unless ``report`` fits the report schema.

    ``blocks`` is the schema's field table, ``REPORT_BLOCKS`` (the stage's
    report) unless a caller with a report of its own passes another: the
    ``camera_hud`` command does, for the stage's report plus its own blocks
    and for the synthetic-rows report. Every rule below applies to any table.
    ``max_bytes`` is the size limit, ``MAX_REPORT_BYTES`` unless that caller
    passes its own for a report with more blocks.

    What is enforced:

    * the top-level fields are exactly those of ``REPORT_BLOCKS``, and each
      block (an object or null) holds only the keys listed for it;
    * below that: null, booleans, integers that fit 64 bits, finite floats;
      strings shaped like an identifier (``IDENTIFIER``: at most 72
      characters, no white space, no path separator), or a sanitised row
      label under the key ``label``; objects of at most ``MAX_KEYS``
      identifier keys; lists of at most ``MAX_SCALAR_LIST`` integers,
      booleans, identifiers or nulls (never floats), or of at most
      ``MAX_RECORD_LIST`` objects; at most ``MAX_DEPTH`` levels;
    * the whole report, JSON-encoded, is at most ``MAX_REPORT_BYTES``
      (40,960) bytes.

    So bytes, buffers, arrays, tuples, prose, paths and wide integers are
    refused wherever they sit, and no arrangement of fields can carry a tile
    (163,840 bytes), a panel update (1,048,576 bytes) or a camera frame: the total is a quarter of a tile.
    What is not enforced, because no check on shapes can: that a number or
    an identifier-shaped string means what its key says. A few kilobytes of
    data spelled as numbers inside the embedded source or bridge report
    would pass. ``report_value`` and the fixed set of fields the stage
    writes are what keep that out, not this function.
    """
    blocks = REPORT_BLOCKS if blocks is None else blocks
    if type(report) is not dict or set(report) != set(blocks):
        raise ValueError("camera_hud_report_field_invalid:report")
    for name, keys in blocks.items():
        value = report[name]
        if keys is None:
            if type(value) in (dict, list):
                raise ValueError(f"camera_hud_report_field_invalid:report.{name}")
        elif value is not None and (type(value) is not dict or not set(value) <= set(keys)):
            raise ValueError(f"camera_hud_report_field_invalid:report.{name}")
        _check(value, f"report.{name}", name, 1)
    if len(json.dumps(report).encode()) > (MAX_REPORT_BYTES if max_bytes is None else max_bytes):
        raise ValueError("camera_hud_report_field_invalid:report_size")
    return report


def report_value(value, withheld, key=None, depth=0):
    """``value`` (a report of the source or the bridge) reduced to what ``check_report`` accepts below a block.

    Keys in ``WITHHELD_KEYS`` are left out; any other value the schema would
    refuse becomes null and is counted in ``withheld[0]``, so a field that
    was dropped is visible as a count, never silently.
    """
    if depth > MAX_DEPTH - 1:
        withheld[0] += 1
        return None
    if isinstance(value, dict):
        kept = {}
        for name, item in value.items():
            if name in WITHHELD_KEYS:
                continue
            if type(name) is not str or IDENTIFIER.match(name) is None or len(kept) >= MAX_KEYS:
                withheld[0] += 1
                continue
            kept[name] = report_value(item, withheld, name, depth + 1)
        return kept
    if isinstance(value, (list, tuple)):
        records = [item for item in value if isinstance(item, dict)]
        if len(records) not in (0, len(value)) or len(value) > (MAX_RECORD_LIST if records else MAX_SCALAR_LIST):
            withheld[0] += 1
            return None
        if records:
            return [report_value(item, withheld, key, depth + 1) for item in value]
        if not all(_leaf(item, key, in_list=True) for item in value):
            withheld[0] += 1
            return None
        return list(value)
    if _leaf(value, key):
        return value
    withheld[0] += 1
    return None


def _identifier(value, fallback):
    return value if type(value) is str and IDENTIFIER.match(value) is not None else fallback


# ---- what was on display, as far as the bridge said -----------------------------------------------------------------

class DisplayLedger:
    """Per label, the intervals in which the bridge had a tile naming it up, from the bridge's own lines.

    Fed in the bridge's order, with the bridge's times (its
    ``CLOCK_MONOTONIC``, the host clock of ``Frame.observed_ns``):

    * ``presented(labels, at_ns, deadline_ns, visible)``: a
      ``frame_presented`` line. With ``visible`` the tile is up from
      ``at_ns``, replacing the one before; without it (the tile was taken
      down) it waits for a show.
    * ``shown(at_ns)``: a ``show_returned`` line with ``ok`` true; a waiting
      tile is up from then.
    * ``hidden(at_ns)``: a ``hide_returned`` line; the tile is down, and
      waits again, since a show could bring it back before its deadline.
    * ``expired(at_ns)``: a ``frame_expired`` line (the time its hide
      returned, in real output); the tile is gone.
    * ``finish(at_ns)``: the run is over; a tile still booked as up ends at
      ``at_ns`` or its deadline, whichever is first.

    A frame the bridge refused or never acknowledged is never passed in, so
    it is never booked. These are the times the bridge's calls returned, not
    what a headset displayed. At most ``MAX_SPELLS`` intervals are kept
    (``dropped`` counts the rest); an interval that begins where the label's
    last one ended extends it.
    """

    def __init__(self, target_label=None):
        self.current = None  # (labels, start_ns, deadline_ns): the tile the bridge has up.
        self.waiting = None  # (labels, deadline_ns): presented, hidden, could still be shown.
        self.spells = {}  # label -> [[start_ns, end_ns], ...] in order.
        self.count = self.dropped = 0  # Of every label but the target.
        self.target_label = target_label  # Its intervals have a budget of their own (``MAX_TARGET_SPELLS``).
        self.target_count = self.target_dropped = 0

    def _end(self, at_ns):
        if self.current is None:
            return
        (labels, start, _), self.current = self.current, None
        if at_ns <= start:
            return
        for label in labels:
            spells = self.spells.get(label)
            if spells and spells[-1][1] == start:
                spells[-1][1] = at_ns
            elif self.target_label is not None and label == self.target_label:
                if self.target_count < MAX_TARGET_SPELLS:
                    self.spells.setdefault(label, []).append([start, at_ns])
                    self.target_count += 1
                else:
                    self.target_dropped += 1
            elif self.count < (MAX_SPELLS if self.target_label is None else MAX_SPELLS - MAX_TARGET_SPELLS):
                self.spells.setdefault(label, []).append([start, at_ns])
                self.count += 1
            else:
                self.dropped += 1

    def presented(self, labels, at_ns, deadline_ns, visible):
        self._end(at_ns)
        self.waiting = None
        if not visible:
            self.waiting = (tuple(labels), deadline_ns)
        elif at_ns < deadline_ns:
            self.current = (tuple(labels), at_ns, deadline_ns)

    def shown(self, at_ns):
        if self.current is None and self.waiting is not None:
            (labels, deadline_ns), self.waiting = self.waiting, None
            if at_ns < deadline_ns:
                self.current = (labels, at_ns, deadline_ns)

    def hidden(self, at_ns):
        if self.current is not None:
            labels, _, deadline_ns = self.current
            self._end(at_ns)
            self.waiting = (labels, deadline_ns)

    def expired(self, at_ns):
        self._end(at_ns)
        self.waiting = None

    def finish(self, at_ns):
        if self.current is not None:
            self._end(min(at_ns, self.current[2]))
        self.waiting = None

    def rows(self, run_start_ns, run_end_ns):
        """Per label: its intervals in milliseconds from ``run_start_ns``, the time displayed and the longest gap.

        The longest gap counts the time from the run's start to the label's
        first interval and from its last to the run's end, as well as the
        time between two intervals.
        """
        rows = []
        for label, spells in sorted(self.spells.items()):
            edges = [run_start_ns, *(edge for spell in spells for edge in spell), max(run_end_ns, spells[-1][1])]
            gaps = [edges[index + 1] - edges[index] for index in range(0, len(edges), 2)]
            rows.append({LABEL_KEY: label, "display_seconds": round(sum(end - start for start, end in spells) / 1e9, 3),
                         "spells": len(spells), "longest_gap_seconds": round(max(0, *gaps) / 1e9, 3),
                         "intervals": [{"start_ms": round((start - run_start_ns) / 1e6, 3),
                                        "end_ms": round((end - run_start_ns) / 1e6, 3)} for start, end in spells]})
        return rows


def window_verdict(intervals, run_seconds, *, window_seconds, fraction, max_gap_seconds):
    """Whether some contiguous window of the run names a label enough: the best window and its numbers.

    ``intervals`` are ``(start, end)`` pairs in seconds from the run's
    start, in order and not overlapping; the run is ``[0, run_seconds]``. A
    window ``[a, a + window_seconds]`` inside the run qualifies when the
    label is up for at least ``fraction`` of it and no stretch of it without
    the label is longer than ``max_gap_seconds``, counting the stretch
    before the first interval in the window and after the last. Returns
    ``{"found", "window_start_seconds", "named_fraction",
    "longest_gap_seconds"}`` for the qualifying window with the largest
    named fraction, or with ``found`` false the best fraction seen. Both
    quantities are piecewise linear in ``a`` and change slope only where a
    window edge meets an interval edge or lies ``max_gap_seconds`` from one,
    so those positions are the ones tried.
    """
    for value in (run_seconds, window_seconds, fraction, max_gap_seconds):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("camera_hud_window_arguments_invalid")
    if not 0 < fraction <= 1 or window_seconds <= 0:
        raise ValueError("camera_hud_window_arguments_invalid")
    spans = [(float(start), float(end)) for start, end in intervals]
    if any(end < start for start, end in spans) or any(spans[index][1] > spans[index + 1][0]
                                                       for index in range(len(spans) - 1)):
        raise ValueError("camera_hud_window_intervals_invalid")
    best = {"found": False, "window_start_seconds": None, "named_fraction": 0.0, "longest_gap_seconds": None}
    last_start = run_seconds - window_seconds
    if last_start < 0:
        return best
    candidates = {0.0, last_start}
    for start, end in spans:
        for edge in (start, end):
            candidates.update((edge, edge - window_seconds, edge - max_gap_seconds,
                               edge + max_gap_seconds - window_seconds))

    def measure(a):
        b = a + window_seconds
        named, gap, cursor = 0.0, 0.0, a
        for start, end in spans:
            start, end = max(start, a), min(end, b)
            if end <= start:
                continue
            gap = max(gap, start - cursor)
            named += end - start
            cursor = end
        return named / window_seconds, max(gap, b - cursor)

    for a in sorted(candidate for candidate in candidates if 0 <= candidate <= last_start):
        named, gap = measure(a)
        found = named >= fraction - 1e-9 and gap <= max_gap_seconds + 1e-9
        if (found, named) > (best["found"], best["named_fraction"]):
            best = {"found": found, "window_start_seconds": round(a, 3), "named_fraction": round(named, 4),
                    "longest_gap_seconds": round(gap, 3)}
    return best


def named_in_window(report, label, *, window_seconds, fraction, max_gap_seconds):
    """``window_verdict`` for ``label`` in a stage report: named at least ``fraction`` of a contiguous window with
    no gap over ``max_gap_seconds``, from the report's own intervals and its run start and end.

    A label the report never booked gives ``found`` false. The verdict is
    void (``ValueError``) when the report dropped intervals of that label:
    the run's target label has a budget of its own
    (``target_spells_dropped``), every other label shares ``spells_dropped``.
    """
    labels = report["labels"]
    # A run's target label has intervals of its own: only its own dropped intervals void its verdict.
    own = labels.get("target_label") is not None and labels.get("target_label") == label
    if labels.get("target_spells_dropped") if own else labels["spells_dropped"]:
        raise ValueError("camera_hud_window_intervals_incomplete")
    if labels["run_start_ns"] is None or labels["run_end_ns"] is None:
        raise ValueError("camera_hud_window_run_not_bounded")
    row = next((row for row in labels["rows"] if row[LABEL_KEY] == label), None)
    intervals = [(item["start_ms"] / 1e3, item["end_ms"] / 1e3) for item in row["intervals"]] if row else []
    return window_verdict(intervals, (labels["run_end_ns"] - labels["run_start_ns"]) / 1e9,
                          window_seconds=window_seconds, fraction=fraction, max_gap_seconds=max_gap_seconds)


# ---- the stage ----------------------------------------------------------------------------------------------------

# The stage's own steps for one result, each timed on this host (``time.perf_counter``): taking the detector's
# boxes back to the whole frame, selecting rows or panel boxes, drawing, packing the image into the bytes that
# are sent, and handing them to the client. ``prepare`` (the crop, resize and turn) is the pipeline's own stage.
STEPS = ("map", "select", "draw", "pack", "queue")
DETECTOR_INPUT_PIXELS = "detector_input_pixels"  # The box space of a frame that was not prepared through a region.
HOST_CLOCK = "host_clock_monotonic"  # ``time.monotonic_ns()``: the clock of ``Frame.observed_ns`` and of the bridge.


def _timing(sample, source, start, end, **more):
    return {**sample, "unit": "ms", "timebase": source, "from": start, "to": end, **more}


def _real(value, reason, *, low, high=None, low_open=False):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(reason)
    value = float(value)
    if not math.isfinite(value) or value < low or (low_open and value == low) or (high is not None and value > high):
        raise ValueError(reason)
    return value


CAMERA_INPUT_REFUSAL = "camera_display_input_not_approved"


def bridge_retired_itself(report, client):
    """Whether the bridge of ``client`` left by itself and cleanly, from the client's ``close()`` report.

    Reaped with exit 0, not forced, and its own final report says the mode
    this client started, exit 0, cleanup, and in real output a returned
    hide, texture clear and destroy. That is everything
    ``cleanup_confirmed`` asks except that this side's writer was still
    writing when the bridge went. A terminal's Ctrl-C goes to the whole
    foreground process group, so the bridge receives the signal too and
    retires before this side has sent its stop; a runner that also saw the
    signal reads that as a cancellation with a clean bridge, never as a
    failure. Without a signal of its own a runner must not use this.
    """
    report = report or {}
    native = report.get("native_report") or {}
    if not (report.get("worker_reaped") is True and report.get("worker_exit_code") == 0
            and report.get("forced_termination") is False and native.get("exit_code") == 0
            and native.get("cleanup_ok") is True and native.get("mode") == client.bridge_mode()):
        return False
    return client.validation_only or all(native.get(name) is True for name in (
        "hide_returned_ok", "clear_texture_returned_ok", "destroy_returned_ok"))


# The view of a stage that states none (the strip from the whole frame, unturned, no alignment, no watch), as a
# scope record states it (``runtime.scope.view_value``).
STAGE_VIEW = {"region": list(WHOLE_FRAME), "rotation": 0, "alignment": None, "presence_watch": False}


# The strip's arrows and a rotation. The stage takes every box back to the unturned whole frame, so with a
# rotation the strip's arrows still say where the object is in the camera image as the camera delivers it; the
# rotation is not applied to them (the panel does apply it: it shows the region as the detector saw it). For
# arrows that refer to the image as the detector saw it, ``orientation`` must be given as well, and this is the
# one that pairs with each rotation. It is not applied for the caller: both are the operator's arguments.
ROTATION_ORIENTATION = {0: "identity", 90: "rotate_90_cw", 180: "rotate_180", 270: "rotate_90_ccw"}


def box_layout():
    """The contract's fixed layout that is drawn as boxes. There is exactly one; any other number is an error
    here and not a guess, so a caller never picks a layout by its place in the list."""
    found = [layout for layout in FIXED_LAYOUTS if isinstance(layout, BoxPanelConfig)]
    if len(found) != 1:
        raise ValueError("camera_hud_box_layout_not_unique")
    return found[0]


def _coordinate(value, plain):
    """Whether ``value`` is a box coordinate the layout's selector takes: the strip's rule or the panel's.

    The strip's rows take a finite Python ``int`` or ``float`` (``plain``);
    the panel's boxes take any finite real number that is not a boolean.
    The two rules are the selectors' own, repeated here so that a detection
    is judged before its box is mapped exactly as it is judged without a
    mapping.
    """
    if plain:
        return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    return isinstance(value, numbers.Integral) or math.isfinite(float(value))


def valid_detections(detections, input_size, plain):
    """``(valid, invalid count)``: the detections whose box lies in the detector's input, in their order.

    A box is valid when it is a list or tuple of four coordinates the
    layout's selector takes, with ``0 <= x0 < x1 <= width`` and
    ``0 <= y0 < y1 <= height`` of ``input_size``, the image the detector was
    given. This is judged before any box is mapped back to the whole frame:
    ``Mapping.to_frame`` sorts, clamps and rounds, so a box outside the
    input, inverted or not a box at all would otherwise come back as a valid
    one, or raise and end the run. A region or a rotation therefore never
    changes which detections count as valid. Score and label are the
    selectors' to judge.
    """
    width, height = input_size
    valid = []
    for detection in detections:
        box = getattr(detection, "box_xyxy", None)
        if (isinstance(detection, Detection) and isinstance(box, (list, tuple)) and len(box) == 4
                and all(_coordinate(value, plain) for value in box)
                and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
            valid.append(detection)
    return valid, len(detections) - len(valid)


def stage_bound(stage):
    """``(layout, view)`` of what ``stage`` runs: the layout it draws for and its view as a scope record states it.

    A ``CameraHudStage`` states both (``config`` and ``view()``). An object
    that states neither is taken to be the strip from the whole frame, which
    is all a stage was before it took a layout and a region.
    """
    view = getattr(stage, "view", None)
    return getattr(stage, "config", HUD_CONFIG), view() if callable(view) else dict(STAGE_VIEW)


def stage_view_bound(scope, stage=None):
    """Whether ``scope`` asks for exactly what ``stage`` runs; True or False, and it changes nothing.

    A scope record binds the layout, the region of the frame, the rotation,
    the alignment and whether the presence watch is required (work-order
    item B5), and a scope is granted only for a request equal to its record.
    The request must in turn be what runs: the scope's layout must be the
    contract's object the stage draws for, and its view, stated as a record
    states it, must equal the stage's own (its region's fractions and turn,
    its alignment's five numbers or none, and whether it holds the presence
    watch). Any difference is refused before the record is consumed, so a
    record is never spent on a run that would not do what it says.
    """
    layout, stated = stage_bound(stage)
    if not any(layout is known for known in FIXED_LAYOUTS) or scope.hud_config is not layout:
        return False
    view, refusal = view_value(scope.source, scope.hud_config, scope.region, scope.rotation, scope.alignment,
                               scope.presence_watch)
    return refusal is None and view == stated


def camera_display_input_preconditions(stage):
    """What the camera site checks before anything is consumed; True or False, and it changes nothing.

    The stage's source is a ``CameraHelperSource`` on the left camera whose
    helper is the checkout's own, at the path the checkpoint manifest
    hashes; the client's ``scope`` is a ``RunScope`` (that exact type) asked
    for ``arcturus_camera_left``; the run is bounded within the scope's
    seconds by what will actually run: the stage's own ``seconds`` (a stage
    with none is refused), the helper's ``max_seconds``, and the helper's
    ``max_frames`` at its frame rate; and the scope asks for the view this
    stage has (``stage_view_bound``).
    """
    return _camera_preconditions(stage, getattr(getattr(stage, "client", None), "scope", None))


def _camera_preconditions(stage, scope):
    """``camera_display_input_preconditions`` on ``scope``, the one object the caller read from the client."""
    source = getattr(stage, "source", None)
    if (type(scope) is not RunScope or scope.source != CAMERA_LEFT or not isinstance(source, CameraHelperSource)
            or source.source_id != camera_contract.source_id(LEFT_CAMERA)
            or not pinned_binary(source.binary, PINNED_HELPER) or not stage_view_bound(scope, stage)):
        return False
    permitted, seconds, limits = scope.seconds, getattr(stage, "seconds", None), source.limits
    if type(permitted) not in (int, float) or type(seconds) not in (int, float) or not 0 < seconds <= permitted:
        return False
    return limits["max_seconds"] <= permitted and limits["max_frames"] <= math.ceil(limits["fps"] * permitted)


def camera_display_input_permitted(stage):
    """Whether ``stage`` may feed SDK camera frames to a real display: only under a one-run scope record.

    The one place that decides it, as ``fixed_hud_output_permitted`` is for
    the output. ``CameraHudStage.run`` hands the answer to the
    ``LIVE_CAMERA_INPUT`` gate's ``require`` whenever its client is in real
    output; a validation client displays nothing, so that run is the bounded
    recognition without display the owner classified outside the gate, and
    is not asked. The answer is ``True`` only when this site's preconditions
    hold (``camera_display_input_preconditions``: the left camera's helper
    source, the pinned helper, bounds within the scope's seconds) and the
    scope, already verified and consumed by the output check, permits camera
    input to this stage, checking the deadline and the helper's digest as it
    answers. The client's ``scope`` is read once and that one object is
    judged and asked. A record for the synthetic step, the mirror or both cameras
    never does. The gate is not open.
    """
    scope = getattr(getattr(stage, "client", None), "scope", None)  # Read once, as the output site reads it.
    if not _camera_preconditions(stage, scope):
        return False
    # After the scope's answer the view it verified against its record must still be the stage's.
    return scope.permit(CAMERA_INPUT, stage) is True and scope.view == stage_bound(stage)[1]


class CameraHudStage:
    """One bounded run of the camera HUD: ``CameraHelperSource`` → detector → rows → tile → observed frame.

    ``source`` is a ``CameraHelperSource`` and ``client`` an
    ``ObservedFrameClient``, neither started; ``detector`` is any detector of
    the pipeline. ``directions`` and ``orientation`` are ``camera_rows``'
    (owner decision 3 and the device step that measures the camera image's
    orientation choose them; the defaults are not findings). ``max_fps``,
    ``max_frames`` and ``seconds`` bound the pipeline as they do elsewhere;
    the helper's own limits are the source's. ``config`` is the layout the
    stage draws for: one of the contract's ``FIXED_LAYOUTS`` objects, the
    strip unless the box panel is named, and it must be the layout the
    client names (a client without one is the strip's). ``region`` is the
    ``camera.region.Region`` the detector is given (None is the whole
    frame, unturned). ``alignment`` is the ``boxes.Alignment`` that maps
    camera boxes onto the panel: required for the box panel, where the
    neutral alignment is a value like any other, and refused for the strip,
    whose rows take none. On the panel ``directions`` and ``orientation``
    are not used. ``on_frame``, when given, is called with every frame the
    pipeline is about to process, before its conversion. ``convert`` is the
    conversion of a frame that is not seen through a region; with a region
    (and always on the panel, whose boxes need the frame's ``Mapping``) the
    stage prepares the frame itself. ``config``, ``region``, ``alignment``
    and ``presence_watch`` are what the permission sites compare with the
    scope record, so they are read-only once the stage is built, and whether
    a frame is prepared (``prepares``) is derived from them on every frame:
    what the sites approved is what runs. ``presence_watch`` is whether the
    stage holds a watch, not a flag; it holds none. The
    numbers are checked and turned into plain ``float`` and ``int`` here, so
    a NumPy scalar cannot fail the report at the end of a run.
    ``target_label``, when given, is the label a run's verdict is about: its
    display intervals get a budget of their own in the ledger.
    ``signalled``, when given, is a callable that says whether the caller
    recorded a stop signal for this run; with the source's own
    ``cancelled`` it is how the stage knows a signal ended the run.

    ``run(stop)`` must be called on the main thread (the supervision
    primitive takes the process's signals). It refuses real output first
    (``client.check_start``) and, in real output, camera input to the
    display (``camera_display_input_permitted``), then drives the pipeline, and returns
    ``report()``; an error of the run is the report's ``status`` and
    ``reason`` (and ``failure`` on the stage), not an exception, so the
    cleanup verdicts are always reported. A stage runs once.

    The stage is the pipeline's only sink: ``open`` starts the bridge,
    ``submit`` sends or withholds one result, ``clear`` takes the tile down
    and ``close`` stops the bridge. A watcher thread ends the client the
    moment ``stop`` is set, and sets ``stop`` itself when the bridge ends or
    the client fails, in both cases without waiting for a detector call in
    flight.
    """

    def __init__(self, source, detector, client, *, threshold=POC_THRESHOLD, directions=True, orientation="identity",
                 max_fps=5, max_frames=0, seconds=None, config=HUD_CONFIG, convert=to_bgr, target_label=None,
                 signalled=None, region=None, alignment=None, on_frame=None):
        if not isinstance(source, CameraHelperSource):
            raise ValueError("camera_hud_requires_camera_helper_source")
        if not isinstance(client, ObservedFrameClient):
            raise ValueError("camera_hud_requires_observed_frame_client")
        if not any(config is known for known in FIXED_LAYOUTS):  # The contract's own object, never a copy.
            raise ValueError("camera_hud_config_not_fixed")
        if (HUD_CONFIG if client.layout is None else client.layout) is not config:
            raise ValueError("camera_hud_layout_mismatch")  # The stage would draw one layout and the client send another.
        boxed = isinstance(config, BoxPanelConfig)
        if region is not None and not isinstance(region, regions.Region):
            raise ValueError("camera_hud_region_invalid")
        if (not isinstance(alignment, Alignment)) if boxed else alignment is not None:
            raise ValueError("camera_hud_alignment_invalid")
        # What the permission sites compare with the scope record: set here, read through properties without setters.
        self._config, self._region, self._alignment = config, regions.Region() if region is None else region, alignment
        self._presence = None  # The presence watch the stage holds: none (the displayed path's watch is item L17's).
        self.on_frame = on_frame
        if type(directions) is not bool:
            raise ValueError("hud_camera_directions_invalid")
        if type(orientation) is not str or orientation not in ORIENTATIONS:
            raise ValueError("hud_camera_orientation_invalid")
        self.threshold = _real(threshold, "hud_threshold_invalid", low=0, high=1)
        self.max_fps = _real(max_fps, "camera_hud_limits_invalid", low=0, low_open=True)
        self.seconds = None if seconds is None else _real(seconds, "camera_hud_limits_invalid", low=0, low_open=True)
        try:
            self.max_frames = operator.index(max_frames) if not isinstance(max_frames, bool) else -1
        except TypeError:
            self.max_frames = -1
        if self.max_frames < 0:
            raise ValueError("camera_hud_limits_invalid")
        self.source, self.detector, self.client = source, detector, client
        self.directions, self.orientation, self.convert = directions, orientation, convert
        self.pipeline = self.failure = None
        self.status, self.reason = "not_run", None
        if target_label is not None and not _label(target_label):
            raise ValueError("camera_hud_target_label_invalid")
        self.ledger = DisplayLedger(target_label)
        self.signalled = signalled  # A callable: whether the caller recorded a stop signal for this run.
        self.counts = dict.fromkeys(("results", "results_with_rows", "results_without_rows", "results_too_old",
                                     "results_after_end", "frames_queued", "frames_sent", "frames_not_sent",
                                     "frames_written_past_deadline", "frames_presented", "frames_refused"), 0)
        self.first_frame_id = self.last_frame_id = self.last_result_index = None
        self.max_deadline_span_ns = 0
        self.output_age, self.display_age = TimingSample(), TimingSample()
        self.steps = {name: TimingSample() for name in STEPS}
        self._mapping = None  # The ``Mapping`` of the frame being processed, from ``_convert`` to ``_tile``.
        self._geometry = None  # ``Mapping`` of the first prepared frame, and whether every later one equalled it.
        self._geometry_constant = True
        self._boxes = None  # ``boxes_report`` of the first counted result with the later ones' counts added.
        self._boxes_counted = self._boxes_sent = self._results_sent = 0
        self._invalid_detections = 0  # Detections dropped before mapping: no valid box in the detector's input.
        self.run_start_ns = None  # When the bridge was ready (``time.monotonic_ns()``).
        self.ended_ns = None  # When the watcher saw ``stop``.
        # The bridge ended or the client failed before the run had ended: the watcher saw it and set ``stop``, or
        # ``submit`` met it first on the pipeline's thread and raised. Whichever thread notices, the answer is one.
        self.bridge_ended_first = False
        self._stop_seen = None
        self._ended = threading.Event()
        self._lock = threading.Lock()
        # (result, labels shown, the panel's selection or None, detections invalid before mapping) of the last tile.
        self._rendered = None
        self._queued = {}  # frame_id -> (labels, observed_ns, expires_ns) until the bridge answers for it.
        self._bridge_report = None
        self._ran = False
        client.listener = self._on_packet

    # -- what the permission sites compare: read-only ---------------------------------------------------------------

    @property
    def config(self):
        """The fixed layout the stage draws for: one of the contract's own objects."""
        return self._config

    @property
    def region(self):
        """The ``Region`` the detector is given."""
        return self._region

    @property
    def alignment(self):
        """The panel's ``Alignment``, or None on the strip."""
        return self._alignment

    @property
    def presence_watch(self):
        """Whether the stage holds a presence watch: what it holds, not what it was told."""
        return self._presence is not None

    @property
    def boxed(self):
        """Whether the layout is the box panel."""
        return isinstance(self._config, BoxPanelConfig)

    @property
    def prepares(self):
        """Whether frames go through ``region.prepare``: always on the panel, and on the strip unless it sees the
        whole frame unturned (which is converted as it always was). Derived from the layout and the region the
        sites compare, on every frame."""
        return self.boxed or not self._region.whole

    # -- the pipeline's hooks ------------------------------------------------------------------------------------

    def config_alignment(self):
        """The alignment's five numbers as the reports and a scope record state them, or None on the strip."""
        return None if self.alignment is None else self.alignment.report()

    def view(self):
        """The stage's view as a scope record states it (``runtime.scope.view_value``): what the sites compare."""
        return {"region": [float(value) for value in self.region.fractions], "rotation": self.region.rotation,
                "alignment": self.config_alignment(), "presence_watch": self.presence_watch}

    def _convert(self, frame):
        """The pipeline's conversion: the frame as the detector sees it, through the region when there is one."""
        if self.on_frame is not None:
            self.on_frame(frame)
        if not self.prepares:
            self._mapping = None
            return self.convert(frame)
        image, mapping = regions.prepare(frame, self.region)
        self._mapping = mapping
        if self._geometry is None:
            self._geometry = mapping
        elif mapping != self._geometry:
            self._geometry_constant = False
        return image

    def _tile(self, bgr, result):
        """The pipeline's annotator: rows or panel boxes from the detector's result, then the image drawn from
        them alone. No pixel of ``bgr`` reaches the image: the strip's and the panel's renderers take none."""
        mapping, timer, steps = self._mapping, time.perf_counter, self.steps
        started = timer()
        if len(result.detections) > MAX_DETECTIONS:  # The selectors' budget, on what the detector returned.
            raise ValueError("hud_detection_budget_exceeded")
        # Judged in the detector's input, before any box is mapped: mapping clamps, and must not make a box valid.
        input_size = (bgr.shape[1], bgr.shape[0]) if mapping is None else mapping.input_size
        valid, invalid = valid_detections(result.detections, input_size, plain=not self.boxed)
        if mapping is not None:  # Boxes back in the whole frame's space.
            valid = [Detection(item.label, item.confidence, mapping.to_frame(item.box_xyxy)) for item in valid]
        at_mapped = timer()
        if self.boxed:
            selection = camera_boxes(valid, mapping, self.config, threshold=self.threshold,
                                     alignment=self.alignment)
            at_selected = timer()
            image = draw_boxes(selection.boxes, self.config)
            labels = tuple(dict.fromkeys(selection.labels))
        else:
            shape = bgr.shape if mapping is None else (mapping.space[1], mapping.space[0])
            rows = camera_rows(valid, shape, self.config, threshold=self.threshold,
                               directions=self.directions, orientation=self.orientation)
            at_selected = timer()
            image = draw_tile(rows, self.config)
            selection, labels = None, tuple(dict.fromkeys(row.label for row in rows))
        at_drawn = timer()
        steps["map"].add((at_mapped - started) * 1e3)
        steps["select"].add((at_selected - at_mapped) * 1e3)
        steps["draw"].add((at_drawn - at_selected) * 1e3)
        self._rendered = (result, labels, selection, invalid)
        return image

    def _count_boxes(self, selection, invalid):
        """Add one result's ``boxes_report`` to the run's: metadata from the first, counts summed. The detections
        dropped before mapping are counted with it, as detections dropped as ``invalid``."""
        block = boxes_report(selection, self.config)
        block["detections"] += invalid
        block["boxes_dropped"] += invalid
        block["dropped"] = {**block["dropped"], "invalid": block["dropped"]["invalid"] + invalid}
        self._boxes_counted += 1
        if self._boxes is None:
            self._boxes = block
            return
        total = self._boxes
        for name in ("detections", "boxes_shown", "boxes_dropped"):
            total[name] += block[name]
        for reason, count in block["dropped"].items():
            total["dropped"][reason] += count

    def _pack(self, image):
        """``(width, height, rgba bytes)`` of the drawn image for the layout: the strip's BGR tile converted, the
        panel's RGBA image as its renderer wrote it, row 0 first."""
        config = self.config
        if not self.boxed:
            width, height, rgba = rgba_from_bgr(image)
        else:
            shape = getattr(image, "shape", None)
            if shape != (config.height_px, config.width_px, RGBA_BYTES) or str(getattr(image, "dtype", "")) != "uint8":
                raise ValueError("camera_hud_tile_size_mismatch")
            width, height, rgba = config.width_px, config.height_px, image.tobytes()
        if (width, height) != (config.width_px, config.height_px):
            raise ValueError("camera_hud_tile_size_mismatch")
        return width, height, rgba

    def open(self):
        self.client.start()
        self.run_start_ns = time.monotonic_ns()

    def submit(self, processed):
        rendered, self._rendered = self._rendered, None
        if rendered is None or rendered[0] is not processed.result:
            raise ValueError("camera_hud_result_identity_mismatch")
        if self.client.error:
            self._note_bridge_end()  # This thread can meet the dead bridge before the watcher's next look.
            raise RuntimeError(self.client.error)
        labels, selection, invalid = rendered[1:]
        frame = processed.frame
        counts = self.counts
        counts["results"] += 1
        self.last_result_index = processed.index
        with self._lock:
            self._invalid_detections += invalid
            if selection is not None:
                self._count_boxes(selection, invalid)
        if self._ended.is_set():
            counts["results_after_end"] += 1
            return
        # The deadline comes from the camera frame's observation alone; the pipeline's is never later.
        expires_ns = frame.observed_ns + MAX_DISPLAY_AGE_NS
        if processed.expires_ns is not None:
            expires_ns = min(expires_ns, processed.expires_ns)
        if time.monotonic_ns() >= expires_ns:
            counts["results_too_old"] += 1
            return
        if not labels:  # No rows, or no boxes: one rule for both layouts. The tile is taken down; nothing is sent.
            counts["results_without_rows"] += 1
            self.client.take_down()
            return
        counts["results_with_rows"] += 1
        timer = time.perf_counter
        started = timer()
        width, height, rgba = self._pack(processed.image)
        packed = timer()
        self.steps["pack"].add((packed - started) * 1e3)
        with self._lock:  # Before the frame can be answered: the bridge's line may be read before the write returns.
            if len(self._queued) >= MAX_ACKNOWLEDGEMENTS:
                del self._queued[next(iter(self._queued))]
            self._queued[frame.sequence] = (labels, frame.observed_ns, expires_ns)
        queued = self.client.submit_observed(frame.sequence, frame.observed_ns, expires_ns, width, height, rgba,
                                             token=labels)
        if queued:
            self.steps["queue"].add((timer() - packed) * 1e3)  # Only a submit the client accepted is a result sent.
            counts["frames_queued"] += 1
            if selection is not None:
                with self._lock:
                    self._results_sent += 1
                    self._boxes_sent += len(selection.boxes)
            return
        with self._lock:
            self._queued.pop(frame.sequence, None)
        # Refused by the client: the end came between the check above and here (the client latched it, so the
        # frame cannot follow the final hide), or the client is no longer running, which is the run's error.
        if not (self._ended.is_set() or self.client.final):
            self._note_bridge_end()
            raise RuntimeError(self.client.error or "observed_frame_client_not_running")
        counts["results_after_end"] += 1

    def clear(self):
        self.client.take_down()

    def close(self):
        """Stop the bridge (once) and close the display ledger; True only for a confirmed clean bridge shutdown."""
        report = self.client.close()  # Every line the bridge wrote has been read when this returns.
        with self._lock:
            if self._bridge_report is None:
                self._bridge_report = report
                self.ledger.finish(time.monotonic_ns())
        return report.get("cleanup_confirmed") is True

    # -- what went out (the writer thread) and what the bridge said of it (the reader thread) ----------------------

    def _on_packet(self, event, **fields):
        with self._lock:
            if event == "bridge_line":
                self._on_line(fields["line"])
            elif event == "frame_sent":
                self.counts["frames_sent"] += 1
                frame_id, observed_ns, expires_ns, sent_ns = (fields[name] for name in
                                                              ("frame_id", "observed_ns", "expires_ns", "sent_ns"))
                if self.first_frame_id is None:
                    self.first_frame_id = frame_id
                self.last_frame_id = frame_id
                self.max_deadline_span_ns = max(self.max_deadline_span_ns, expires_ns - observed_ns)
                self.output_age.add((sent_ns - observed_ns) / 1e6)
                if sent_ns >= expires_ns:  # Written too late: the bridge refuses it on arrival and nothing is shown.
                    self.counts["frames_written_past_deadline"] += 1
            elif event == "frame_not_sent":
                self.counts["frames_not_sent"] += 1
                self._queued.pop(fields["frame_id"], None)

    def _on_line(self, line):
        """One of the bridge's lines, in its order: the only source of what the ledger books."""
        event = line.get("event")
        if event == "frame_presented":
            self.counts["frames_presented"] += 1
            queued = self._queued.pop(line["frame_id"], None)
            if queued is not None:
                labels, observed_ns, expires_ns = queued
                self.display_age.add((line["presented_ns"] - observed_ns) / 1e6)
                self.ledger.presented(labels, line["presented_ns"], expires_ns, line["visible"])
        elif event == "frame_refused":
            self.counts["frames_refused"] += 1
            self._queued.pop(line["frame_id"], None)
        elif event == "frame_expired":
            at = line.get("hide_returned_ns", line.get("observed_ns"))
            if type(at) is int:
                self.ledger.expired(at)
        elif event in ("hide_returned", "show_returned") and line.get("ok") is True:
            at = line.get("returned_ns")
            if type(at) is int:
                (self.ledger.hidden if event == "hide_returned" else self.ledger.shown)(at)

    # -- the run -------------------------------------------------------------------------------------------------

    def _note_bridge_end(self):
        """Record ``bridge_ended_first`` if the bridge is gone, or the client failed, and the run has not ended.

        Called by whichever thread notices: the watcher at its poll, or the
        pipeline's thread in ``submit`` just before it raises the client's
        error. Under the lock the watcher marks the end with, so a bridge
        that ends after the run did is never booked as having ended first.
        Returns whether the client is gone.
        """
        client = self.client
        if not (client.error or client.done.is_set()):
            return False
        with self._lock:
            if not self._ended.is_set():
                self.bridge_ended_first = True
        return True

    def _watch(self, stop):
        client = self.client
        while not stop.wait(WATCHER_POLL_SECONDS):
            if self._note_bridge_end():  # The bridge is gone or the client failed: no display path.
                stop.set()
        with self._lock:
            self._stop_seen, self.ended_ns = time.monotonic(), time.monotonic_ns()
            self._ended.set()
        client.end()  # At once, whatever the pipeline's thread is doing; nothing is accepted after it.

    def run(self, stop=None):
        """Run once, on the main thread; returns ``report()``. Real output is refused before anything is launched."""
        if self._ran:
            raise ValueError("camera_hud_stage_already_run")
        self._ran = True
        real_output = self.client.validation_only is not True  # Fixed at the client's construction.
        if real_output:
            # The output site asks this before it consumes the record, so a wrong helper or an unbounded
            # stage is refused with the record unspent.
            self.client.prerequisites = (lambda: camera_display_input_preconditions(self),)
        try:
            self.client.check_start()
            if real_output:  # Camera frames are about to reach a real display.
                LIVE_CAMERA_INPUT.require(camera_display_input_permitted(self), CAMERA_INPUT_REFUSAL)
                # The launcher hashes the helper once more and ends the run if it is not the manifest's.
                self.source.expected_sha256 = self.client.scope.pins.get(PINNED_HELPER) if type(
                    self.client.scope) is RunScope else None
        except ValueError:
            self.client.withdraw()  # A refused run gives its scope back: nothing can start under it later.
            raise
        stop = threading.Event() if stop is None else stop
        self.pipeline = Pipeline(self.source, self.detector, [self], threshold=self.threshold, max_fps=self.max_fps,
                                 latest_only=True, max_input_age_ms=MAX_DISPLAY_AGE_MS, max_frames=self.max_frames,
                                 seconds=self.seconds, convert=self._convert, annotator=self._tile)
        watcher = threading.Thread(target=self._watch, args=(stop,), name="camera-hud-end", daemon=True)
        watcher.start()
        try:
            self.source.drive(self.pipeline, stop)
        except Exception as error:
            self.failure = error
        finally:
            stop.set()
            watcher.join(WATCHER_JOIN_SECONDS)
            self.client.withdraw()  # A run that failed before the bridge started gives its scope back.
            self.close()  # The pipeline closed it with its sinks; this covers a run that never opened them.
        self.status, self.reason = self._outcome()
        return self.report()

    def _client_reason(self):
        """A code for the client's error: the bridge's own word when it is one, else which side failed."""
        gone = self.client.final_report is None or self.client.forced_termination
        return _identifier(self.client.error, "overlay_bridge_failed" if gone else "overlay_client_failed")

    def _signalled(self):
        """Whether a stop signal ended this run: the helper's supervision took one, or the caller recorded one."""
        if self.source.status == "cancelled":
            return True
        try:
            return self.signalled is not None and self.signalled() is True
        except Exception:
            return False

    def _bridge_retired_itself(self):
        return bridge_retired_itself(self._bridge_report, self.client)

    def _excused(self):
        """A client error that is only the bridge having left on the same signal that ended the run.

        A terminal's Ctrl-C goes to the whole foreground process group, so
        the bridge receives it too, retires its overlay and exits 0 while
        this side's writer is still writing; the write then fails. That run
        was cancelled, not failed, and its bridge cleaned up.
        """
        return bool(self.client.error) and self._signalled() and self._bridge_retired_itself()

    def _outcome(self):
        source, pipeline, failure = self.source, self.pipeline, self.failure
        excused = self._excused()
        if self.client.error and not excused:
            return "error", self._client_reason()
        if failure is not None and not (excused and str(failure) == str(self.client.error)):
            return "error", (_identifier(getattr(failure, "reason", None), None)
                             or _identifier(str(failure), "camera_hud_run_failed"))
        if source.status == "presence_ended":
            return "presence_ended", _identifier(source.reason, "presence_ended")
        if source.status == "cancelled" or self._signalled():
            return "cancelled", "signal"
        if source.status == "error":
            return "error", _identifier(source.reason, "camera_source_failed")
        if self.client.output_bound_reached:  # The client ended real output at its own bound.
            return "completed", "output_bound"
        if pipeline._limit_reached():
            return "completed", "max_frames"
        if pipeline.deadline is not None and self._stop_seen is not None and self._stop_seen >= pipeline.deadline:
            return "completed", "seconds"
        if source.status == "completed":
            return "completed", _identifier(source.end_reason, "helper_bound")
        return "stopped", "stop_requested"

    # -- the report ----------------------------------------------------------------------------------------------

    def _timings(self, stats):
        blocks = {name: stats[name] for name in TIMINGS} if stats is not None else {
            name: TimingSample().report() for name in TIMINGS}
        sent = self.counts["frames_sent"]
        return {
            "percentile_method": PERCENTILE_METHOD,
            "input_age_ms": _timing(blocks["input_age_ms"], HOST_CLOCK, "camera_frame_observed", "processing_started"),
            "inference_ms": _timing(blocks["inference_ms"], "detector_reported_duration", "detector_call",
                                    "detector_return"),
            "processing_ms": _timing(blocks["processing_ms"], "host_monotonic_duration", "processing_started",
                                     "tile_drawn"),
            "result_age_ms": _timing(blocks["output_age_ms"], HOST_CLOCK, "camera_frame_observed",
                                     "result_at_output_boundary"),
            # To the completed write of the frame's packet: this side's last instant, before the bridge's upload.
            "output_age_ms": _timing(self.output_age.report(), HOST_CLOCK, "camera_frame_observed",
                                     "packet_written_to_bridge_pipe"),
            # To the bridge's frame_presented line: its upload and show-or-hide calls returned. That is a runtime
            # call's return, not the headset's display, and a frame presented while the tile was down counts too.
            "capture_to_display_age_ms": _timing(
                self.display_age.report(), HOST_CLOCK, "camera_frame_observed", "bridge_present_calls_returned",
                acknowledged_by="bridge_frame_presented_line",
                frames_without_acknowledgement=max(0, sent - self.display_age.count),
                ends_at_headset_display=False, physical_display_observed=False)}

    def _expiry(self, validation_only):
        events, poll_ms = [], BRIDGE_INPUT_POLL_NS / 1e6
        late_notice = late_hide = None
        for event in self.client.expiry_events:
            deadline, noticed, returned = event.get("deadline_ns"), event.get("observed_ns"), event.get("hide_returned_ns")
            record = dict(event)
            if type(deadline) is int and type(noticed) is int:
                record["noticed_after_deadline_ms"] = round((noticed - deadline) / 1e6, 3)
                late_notice = max(late_notice or 0.0, record["noticed_after_deadline_ms"])
            if type(deadline) is int and type(returned) is int:
                record["hide_returned_after_deadline_ms"] = round((returned - deadline) / 1e6, 3)
                late_hide = max(late_hide or 0.0, record["hide_returned_after_deadline_ms"])
            if len(events) < MAX_REPORTED_EXPIRIES:
                events.append(record)
        return {"count": self.client.expiry_event_count, "recorded": len(events),
                "dropped": self.client.expiry_event_count - len(events), "bridge_poll_ms": poll_ms,
                "max_noticed_after_deadline_ms": late_notice, "max_hide_returned_after_deadline_ms": late_hide,
                "hide_times_reported": not validation_only, "events": events}

    def _end(self):
        """When the run ended, when the final hide was written, and when the bridge's hide returned."""
        client, ended = self.client, self.ended_ns
        final = client.final_hide
        written = final["sent_ns"] if final is not None else None
        returned = None
        if final is not None:
            returned = next((line.get("returned_ns") for line in client.cycle_lines(final["cycle"])
                             if line.get("event") == "hide_returned" and line.get("ok") is True), None)

        def after(at):
            return None if at is None or ended is None else round((at - ended) / 1e6, 3)
        return {"run_start_ns": self.run_start_ns, "ended_ns": ended, "timebase": HOST_CLOCK,
                # No hide was written at the end because one already was (an empty result, an expired tile).
                "tile_already_down": final is None and client.hidden,
                "hide_written_ns": written, "hide_written_after_end_ms": after(written),
                "bridge_hide_returned_ns": returned, "bridge_hide_after_end_ms": after(returned),
                "bridge_ended_first": self.bridge_ended_first,
                # The client's error is only the bridge having retired cleanly on the signal that ended the run.
                "bridge_left_on_signal": self._excused()}

    def report(self):
        """The run's report: counts, labels, timings and format metadata, checked against ``check_report``."""
        client, source, pipeline = self.client, self.source, self.pipeline
        withheld = [0]
        with self._lock:
            bridge = self._bridge_report
            counts = dict(self.counts)
            run_end_ns = self.ended_ns if self.ended_ns is not None else self.run_start_ns
            labels = self.ledger.rows(self.run_start_ns, run_end_ns) if self.run_start_ns is not None else []
            spells_dropped, target_dropped = self.ledger.dropped, self.ledger.target_dropped
            boxes = None
            if self._boxes is not None:
                boxes = {("boxes_selected" if name == "boxes_shown" else name): value
                         for name, value in self._boxes.items()}
                boxes.update(dropped=dict(self._boxes["dropped"]), boxes_sent=self._boxes_sent,
                             results_counted=self._boxes_counted, results_sent=self._results_sent)
            invalid_detections = self._invalid_detections
        geometry = self._geometry
        x0, y0, x1, y1 = self.region.fractions
        view = {"region": {"x0": x0, "y0": y0, "x1": x1, "y1": y1}, "rotation": self.region.rotation,
                "whole_frame": self.region.whole, "prepared": self.prepares,
                "box_coordinate_space": regions.FRAME_PIXELS if self.prepares else DETECTOR_INPUT_PIXELS,
                "source_space_px": None if geometry is None else list(geometry.space),
                "crop_native_xyxy": None if geometry is None else list(geometry.crop),
                "resized_px": None if geometry is None else list(geometry.resized),
                "geometry_constant": None if geometry is None else self._geometry_constant,
                "invalid_detections": invalid_detections}
        stats = pipeline.stats.report() if pipeline is not None else None
        native = (bridge or {}).get("native_report") or {}
        bridge_counts = {name: native.get(name) for name in BRIDGE_COUNTS}
        source_report = source.report()
        source_report.pop("observed_ns", None)  # A sentence about the clock; ``timings`` declares the timebases.
        pipeline_block = None
        if stats is not None:
            pipeline_block = {name: stats[name] for name in (
                "frames_received", "frames_processed", "frames_submitted", "stale_inputs_skipped",
                "stale_results_skipped", "pending_replaced", "elapsed_ms", "processed_fps", "cancelled",
                "sink_clear_ok", "sink_close_ok")}
            pipeline_block["stage_ms"] = stats["stage_ms"]
            pipeline_block["capture_thread_alive"] = pipeline.capture_thread_alive
        bridge_block = None
        if bridge is not None:
            bridge_block = {name: bridge.get(name) for name in (
                *CLOSE_REPORT_FIELDS, "started", "mode", "frames_submitted", "frames_replaced", "frames_withdrawn",
                "frames_expired_before_send", "frames_retransmitted", "take_downs", "shows", "keepalives",
                "visibility_requests", "settings_sent", "frames_presented", "frames_refused")}
            bridge_block["error"] = None if bridge.get("error") is None else self._client_reason()
            bridge_block["lines"] = report_value(dict(client.line_counts), withheld)
            first = client.first_frame
            bridge_block["first_frame"] = None if first is None else {
                "event": first["event"], "width": first["width"], "height": first["height"], "read_ns": first["read_ns"]}
            bridge_block["final_report"] = report_value({**native, "bridge_error": native.get("error")}, withheld)
        source_ok = source_report.get("cleanup_ok") is True
        # Confirmed by the client, or the bridge retired itself cleanly on the signal that ended the run.
        bridge_ok = bridge is not None and (bridge.get("cleanup_confirmed") is True or self._excused())
        expiry = self._expiry(client.validation_only)
        expiry["events"] = report_value(expiry["events"], withheld)
        report = {
            "report_kind": REPORT_KIND, "schema_version": 1, "status": self.status, "reason": self.reason,
            "error_type": _identifier(type(self.failure).__name__, None) if self.failure is not None else None,
            **gates.claims(), "validation_only": client.validation_only, "bridge_mode": client.bridge_mode(),
            "images_saved": False, "pixels_in_report": False, "physical_display_observed": False,
            "config": {"hud_config_id": self.config.layout_id, "hud_config_sha256": config_digest(self.config),
                       "tile_px": [self.config.width_px, self.config.height_px],
                       "rows": None if self.boxed else self.config.rows,
                       "max_boxes": self.config.max_boxes if self.boxed else None,
                       "threshold": self.threshold, "directions": None if self.boxed else self.directions,
                       "orientation": None if self.boxed else self.orientation,
                       "alignment": self.config_alignment(),
                       "max_fps": self.max_fps, "max_frames": self.max_frames, "seconds": self.seconds,
                       "max_display_age_ms": MAX_DISPLAY_AGE_MS, "keepalive_seconds": client.keepalive_seconds,
                       "update_bytes": self.config.width_px * self.config.height_px * RGBA_BYTES},
            "view": view,
            "boxes": boxes,
            "identities": {"frame_identity": "camera_frame_sequence", "first_frame_id": self.first_frame_id,
                           "last_frame_id": self.last_frame_id, "last_result_index": self.last_result_index,
                           "source_id": _identifier(source.source_id, None)},
            "results": {name: counts[name] for name in ("results", "results_with_rows", "results_without_rows",
                                                        "results_too_old", "results_after_end")},
            "frames": {"submitted": counts["frames_queued"], "sent": counts["frames_sent"],
                       "not_sent": counts["frames_not_sent"],
                       "written_past_deadline": counts["frames_written_past_deadline"],
                       # The bridge's answer per frame, from its frame_presented and frame_refused lines.
                       "acknowledged_presented": client.presented_count, "acknowledged_refused": client.refused_count,
                       "refused_reasons": dict(client.refused_reasons),
                       "unacknowledged": max(0, counts["frames_sent"] - client.presented_count - client.refused_count),
                       # The bridge's final counts. ``bridge_accepted`` is its frames_received: frames judged
                       # presentable on arrival, counted before the upload, so it includes any that then expired
                       # in the upload and is not a count of frames presented.
                       "bridge_received": bridge_counts["observed_frames_received"],
                       "bridge_accepted": bridge_counts["frames_received"],
                       "retransmitted": bridge_counts["frames_retransmitted"],
                       "refused": bridge_counts["stale_frames_dropped"],
                       "expired": bridge_counts["expired_frames"],
                       "expired_in_upload": bridge_counts["frames_expired_in_upload"]},
            "deadlines": {"cap_ms": MAX_DISPLAY_AGE_MS,
                          "max_deadline_after_observation_ms": round(self.max_deadline_span_ns / 1e6, 3),
                          "within_cap": self.max_deadline_span_ns <= MAX_DISPLAY_AGE_NS},
            "end": self._end(),
            "timings": self._timings(stats),
            # ``pack`` is timed for every result packed and ``queue`` for every one the client accepted; the
            # others for every result drawn.
            "steps_ms": {"percentile_method": PERCENTILE_METHOD, "clock": "host_perf_counter", "unit": "ms",
                         **{name: self.steps[name].report() for name in STEPS}},
            "frame_expired": expiry,
            "labels": {"basis": "bridge_frame_presented_hide_show_and_expiry_lines", "timebase": HOST_CLOCK,
                       "run_start_ns": self.run_start_ns, "run_end_ns": run_end_ns if self.run_start_ns else None,
                       "spells_dropped": spells_dropped, "target_label": self.ledger.target_label,
                       "target_spells_dropped": target_dropped if self.ledger.target_label is not None else None,
                       "physical_display_observed": False, "rows": labels},
            "pipeline": pipeline_block,
            "source": report_value(source_report, withheld),
            "bridge": bridge_block,
            "cleanup": {"source_ok": source_ok, "bridge_ok": bridge_ok,
                        "sinks_ok": pipeline is not None and pipeline.stats.sink_cleanup_ok,
                        "ok": source_ok and bridge_ok},
        }
        report["fields_withheld"] = withheld[0]
        return check_report(report)
