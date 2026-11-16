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
from ..convert import to_bgr
from ..detection import POC_THRESHOLD
from ..gates import LIVE_CAMERA_INPUT
from ..observed_frames import BRIDGE_COUNTS, MAX_ACKNOWLEDGEMENTS, ObservedFrameClient
from ..overlay import BRIDGE_INPUT_POLL_NS, CLOSE_REPORT_FIELDS, rgba_from_bgr
from ..pipeline import PERCENTILE_METHOD, TIMINGS, Pipeline, TimingSample
from ..runtime.scope import CAMERA_INPUT, CAMERA_LEFT, RunScope
from ..sources.camera_helper import LEFT_CAMERA, CameraHelperSource
from .camera import ORIENTATIONS, camera_rows
from .contract import HUD_CONFIG, MAX_DISPLAY_AGE_MS, MAX_DISPLAY_AGE_NS, ROW_LABEL_CHARS, config_digest, row_label
from .render import draw_tile

__all__ = ("CAMERA_INPUT_REFUSAL", "MAX_REPORT_BYTES", "REPORT_KIND", "CameraHudStage", "DisplayLedger",
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
# The whole report, JSON-encoded: 9 to 10 kB of counts and metadata, up to MAX_SPELLS intervals (about 45
# bytes each, 11.5 kB) and up to MAX_REPORTED_EXPIRIES expiry events (about 400 bytes each, 12.8 kB), plus margin.
MAX_REPORT_BYTES = 40_960
MAX_SPELLS = 256  # Display intervals kept over all labels; later ones are counted in ``spells_dropped``.
MAX_REPORTED_EXPIRIES = 32  # ``frame_expired`` events repeated in the report; the count covers them all.
# Keys of the embedded source and bridge reports that hold prose, paths or argument vectors: left out by name.
WITHHELD_KEYS = ("error", "detail", "path", "arguments", "definition", "scope", "observed_ns_clock")
# Every top-level field of the report, and for each block the keys it may hold (None: a scalar field).
REPORT_BLOCKS = {
    "report_kind": None, "schema_version": None, "status": None, "reason": None, "error_type": None,
    **dict.fromkeys(gates.claims()),  # The gate claims, named by the gates alone.
    "validation_only": None, "bridge_mode": None, "images_saved": None, "pixels_in_report": None,
    "physical_display_observed": None, "fields_withheld": None,
    "config": ("hud_config_id", "hud_config_sha256", "tile_px", "rows", "threshold", "directions", "orientation",
               "max_fps", "max_frames", "seconds", "max_display_age_ms", "keepalive_seconds"),
    "identities": ("frame_identity", "first_frame_id", "last_frame_id", "last_result_index", "source_id"),
    "results": ("results", "results_with_rows", "results_without_rows", "results_too_old", "results_after_end"),
    "frames": ("submitted", "sent", "not_sent", "written_past_deadline", "acknowledged_presented",
               "acknowledged_refused", "refused_reasons", "unacknowledged", "bridge_received", "bridge_accepted",
               "retransmitted", "refused", "expired", "expired_in_upload"),
    "deadlines": ("cap_ms", "max_deadline_after_observation_ms", "within_cap"),
    "end": ("run_start_ns", "ended_ns", "timebase", "tile_already_down", "hide_written_ns", "hide_written_after_end_ms",
            "bridge_hide_returned_ns", "bridge_hide_after_end_ms", "bridge_ended_first"),
    "timings": ("percentile_method", "input_age_ms", "inference_ms", "processing_ms", "result_age_ms", "output_age_ms",
                "capture_to_display_age_ms"),
    "frame_expired": ("count", "recorded", "dropped", "bridge_poll_ms", "max_noticed_after_deadline_ms",
                      "max_hide_returned_after_deadline_ms", "hide_times_reported", "events"),
    "labels": ("basis", "timebase", "run_start_ns", "run_end_ns", "spells_dropped", "physical_display_observed", "rows"),
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


def check_report(report):
    """Raise ``ValueError('camera_hud_report_field_invalid:<path>')`` unless ``report`` fits the report schema.

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
    (163,840 bytes) or a camera frame: the total is a quarter of a tile.
    What is not enforced, because no check on shapes can: that a number or
    an identifier-shaped string means what its key says. A few kilobytes of
    data spelled as numbers inside the embedded source or bridge report
    would pass. ``report_value`` and the fixed set of fields the stage
    writes are what keep that out, not this function.
    """
    if type(report) is not dict or set(report) != set(REPORT_BLOCKS):
        raise ValueError("camera_hud_report_field_invalid:report")
    for name, keys in REPORT_BLOCKS.items():
        value = report[name]
        if keys is None:
            if type(value) in (dict, list):
                raise ValueError(f"camera_hud_report_field_invalid:report.{name}")
        elif value is not None and (type(value) is not dict or not set(value) <= set(keys)):
            raise ValueError(f"camera_hud_report_field_invalid:report.{name}")
        _check(value, f"report.{name}", name, 1)
    if len(json.dumps(report).encode()) > MAX_REPORT_BYTES:
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

    def __init__(self):
        self.current = None  # (labels, start_ns, deadline_ns): the tile the bridge has up.
        self.waiting = None  # (labels, deadline_ns): presented, hidden, could still be shown.
        self.spells = {}  # label -> [[start_ns, end_ns], ...] in order.
        self.count = self.dropped = 0

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
            elif self.count < MAX_SPELLS:
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
    void (``ValueError``) when the report dropped intervals.
    """
    labels = report["labels"]
    if labels["spells_dropped"]:
        raise ValueError("camera_hud_window_intervals_incomplete")
    if labels["run_start_ns"] is None or labels["run_end_ns"] is None:
        raise ValueError("camera_hud_window_run_not_bounded")
    row = next((row for row in labels["rows"] if row[LABEL_KEY] == label), None)
    intervals = [(item["start_ms"] / 1e3, item["end_ms"] / 1e3) for item in row["intervals"]] if row else []
    return window_verdict(intervals, (labels["run_end_ns"] - labels["run_start_ns"]) / 1e9,
                          window_seconds=window_seconds, fraction=fraction, max_gap_seconds=max_gap_seconds)


# ---- the stage ----------------------------------------------------------------------------------------------------

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


def camera_display_input_permitted(stage):
    """Whether ``stage`` may feed SDK camera frames to a real display: only under a one-run scope record.

    The one place that decides it, as ``fixed_hud_output_permitted`` is for
    the output. ``CameraHudStage.run`` hands the answer to the
    ``LIVE_CAMERA_INPUT`` gate's ``require`` whenever its client is in real
    output; a validation client displays nothing, so that run is the bounded
    recognition without display the owner classified outside the gate, and
    is not asked. The answer is ``True`` only when the stage's source is a
    ``CameraHelperSource`` on the left camera, the client's ``scope`` is a
    ``RunScope`` (that exact type) asked for ``arcturus_camera_left``, and
    that scope, already verified and consumed by the output check, permits
    camera input, which it does once. A record for the synthetic step, the
    mirror or both cameras never does. The gate is not open.
    """
    scope = getattr(getattr(stage, "client", None), "scope", None)
    source = getattr(stage, "source", None)
    if (type(scope) is not RunScope or scope.source != CAMERA_LEFT or not isinstance(source, CameraHelperSource)
            or source.source_id != camera_contract.source_id(LEFT_CAMERA)):
        return False
    return scope.permit(CAMERA_INPUT) is True


class CameraHudStage:
    """One bounded run of the camera HUD: ``CameraHelperSource`` → detector → rows → tile → observed frame.

    ``source`` is a ``CameraHelperSource`` and ``client`` an
    ``ObservedFrameClient``, neither started; ``detector`` is any detector of
    the pipeline. ``directions`` and ``orientation`` are ``camera_rows``'
    (owner decision 3 and the device step that measures the camera image's
    orientation choose them; the defaults are not findings). ``max_fps``,
    ``max_frames`` and ``seconds`` bound the pipeline as they do elsewhere;
    the helper's own limits are the source's. ``config`` must be the fixed
    HUD configuration, the only tile the bridge's fixed mode takes. The
    numbers are checked and turned into plain ``float`` and ``int`` here, so
    a NumPy scalar cannot fail the report at the end of a run.

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
                 max_fps=5, max_frames=0, seconds=None, config=HUD_CONFIG, convert=to_bgr):
        if not isinstance(source, CameraHelperSource):
            raise ValueError("camera_hud_requires_camera_helper_source")
        if not isinstance(client, ObservedFrameClient):
            raise ValueError("camera_hud_requires_observed_frame_client")
        if config != HUD_CONFIG:
            raise ValueError("camera_hud_config_not_fixed")
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
        self.source, self.detector, self.client, self.config = source, detector, client, config
        self.directions, self.orientation, self.convert = directions, orientation, convert
        self.pipeline = self.failure = None
        self.status, self.reason = "not_run", None
        self.ledger = DisplayLedger()
        self.counts = dict.fromkeys(("results", "results_with_rows", "results_without_rows", "results_too_old",
                                     "results_after_end", "frames_queued", "frames_sent", "frames_not_sent",
                                     "frames_written_past_deadline", "frames_presented", "frames_refused"), 0)
        self.first_frame_id = self.last_frame_id = self.last_result_index = None
        self.max_deadline_span_ns = 0
        self.output_age, self.display_age = TimingSample(), TimingSample()
        self.run_start_ns = None  # When the bridge was ready (``time.monotonic_ns()``).
        self.ended_ns = None  # When the watcher saw ``stop``.
        self.bridge_ended_first = False  # The watcher set ``stop`` because the bridge ended or the client failed.
        self._stop_seen = None
        self._ended = threading.Event()
        self._lock = threading.Lock()
        self._rendered = None  # (result, rows) of the tile ``_tile`` drew last, for ``submit``.
        self._queued = {}  # frame_id -> (labels, observed_ns, expires_ns) until the bridge answers for it.
        self._bridge_report = None
        self._ran = False
        client.listener = self._on_packet

    # -- the pipeline's hooks ------------------------------------------------------------------------------------

    def _tile(self, bgr, result):
        """The pipeline's annotator: rows from the detector's result, then the tile drawn from the rows alone."""
        rows = camera_rows(list(result.detections), bgr.shape, self.config, threshold=self.threshold,
                           directions=self.directions, orientation=self.orientation)
        self._rendered = (result, rows)
        return draw_tile(rows, self.config)

    def open(self):
        self.client.start()
        self.run_start_ns = time.monotonic_ns()

    def submit(self, processed):
        rendered, self._rendered = self._rendered, None
        if rendered is None or rendered[0] is not processed.result:
            raise ValueError("camera_hud_result_identity_mismatch")
        if self.client.error:
            raise RuntimeError(self.client.error)
        rows = rendered[1]
        frame = processed.frame
        counts = self.counts
        counts["results"] += 1
        self.last_result_index = processed.index
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
        if not rows:
            counts["results_without_rows"] += 1
            self.client.take_down()
            return
        counts["results_with_rows"] += 1
        width, height, rgba = rgba_from_bgr(processed.image)
        if (width, height) != (self.config.width_px, self.config.height_px):
            raise ValueError("camera_hud_tile_size_mismatch")
        labels = tuple(dict.fromkeys(row.label for row in rows))
        with self._lock:  # Before the frame can be answered: the bridge's line may be read before the write returns.
            if len(self._queued) >= MAX_ACKNOWLEDGEMENTS:
                del self._queued[next(iter(self._queued))]
            self._queued[frame.sequence] = (labels, frame.observed_ns, expires_ns)
        if self.client.submit_observed(frame.sequence, frame.observed_ns, expires_ns, width, height, rgba, token=labels):
            counts["frames_queued"] += 1
            return
        with self._lock:
            self._queued.pop(frame.sequence, None)
        # Refused by the client: the end came between the check above and here (the client latched it, so the
        # frame cannot follow the final hide), or the client is no longer running, which is the run's error.
        if not (self._ended.is_set() or self.client.final):
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

    def _watch(self, stop):
        client = self.client
        while not stop.wait(WATCHER_POLL_SECONDS):
            if client.error or client.done.is_set():  # The bridge is gone or the client failed: no display path.
                self.bridge_ended_first = True
                stop.set()
        self._stop_seen, self.ended_ns = time.monotonic(), time.monotonic_ns()
        self._ended.set()
        client.end()  # At once, whatever the pipeline's thread is doing; nothing is accepted after it.

    def run(self, stop=None):
        """Run once, on the main thread; returns ``report()``. Real output is refused before anything is launched."""
        if self._ran:
            raise ValueError("camera_hud_stage_already_run")
        self._ran = True
        self.client.check_start()
        if self.client.validation_only is not True:  # Camera frames are about to reach a real display.
            LIVE_CAMERA_INPUT.require(camera_display_input_permitted(self), CAMERA_INPUT_REFUSAL)
        stop = threading.Event() if stop is None else stop
        self.pipeline = Pipeline(self.source, self.detector, [self], threshold=self.threshold, max_fps=self.max_fps,
                                 latest_only=True, max_input_age_ms=MAX_DISPLAY_AGE_MS, max_frames=self.max_frames,
                                 seconds=self.seconds, convert=self.convert, annotator=self._tile)
        watcher = threading.Thread(target=self._watch, args=(stop,), name="camera-hud-end", daemon=True)
        watcher.start()
        try:
            self.source.drive(self.pipeline, stop)
        except Exception as error:
            self.failure = error
        finally:
            stop.set()
            watcher.join(WATCHER_JOIN_SECONDS)
            self.close()  # The pipeline closed it with its sinks; this covers a run that never opened them.
        self.status, self.reason = self._outcome()
        return self.report()

    def _client_reason(self):
        """A code for the client's error: the bridge's own word when it is one, else which side failed."""
        gone = self.client.final_report is None or self.client.forced_termination
        return _identifier(self.client.error, "overlay_bridge_failed" if gone else "overlay_client_failed")

    def _outcome(self):
        source, pipeline, failure = self.source, self.pipeline, self.failure
        if self.client.error:
            return "error", self._client_reason()
        if failure is not None:
            return "error", (_identifier(getattr(failure, "reason", None), None)
                             or _identifier(str(failure), "camera_hud_run_failed"))
        if source.status == "presence_ended":
            return "presence_ended", _identifier(source.reason, "presence_ended")
        if source.status == "cancelled":
            return "cancelled", "signal"
        if source.status == "error":
            return "error", _identifier(source.reason, "camera_source_failed")
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
                "bridge_ended_first": self.bridge_ended_first}

    def report(self):
        """The run's report: counts, labels, timings and format metadata, checked against ``check_report``."""
        client, source, pipeline = self.client, self.source, self.pipeline
        withheld = [0]
        with self._lock:
            bridge = self._bridge_report
            counts = dict(self.counts)
            run_end_ns = self.ended_ns if self.ended_ns is not None else self.run_start_ns
            labels = self.ledger.rows(self.run_start_ns, run_end_ns) if self.run_start_ns is not None else []
            spells_dropped = self.ledger.dropped
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
        bridge_ok = bridge is not None and bridge.get("cleanup_confirmed") is True
        expiry = self._expiry(client.validation_only)
        expiry["events"] = report_value(expiry["events"], withheld)
        report = {
            "report_kind": REPORT_KIND, "schema_version": 1, "status": self.status, "reason": self.reason,
            "error_type": _identifier(type(self.failure).__name__, None) if self.failure is not None else None,
            **gates.claims(), "validation_only": client.validation_only, "bridge_mode": client.bridge_mode(),
            "images_saved": False, "pixels_in_report": False, "physical_display_observed": False,
            "config": {"hud_config_id": self.config.layout_id, "hud_config_sha256": config_digest(self.config),
                       "tile_px": [self.config.width_px, self.config.height_px], "rows": self.config.rows,
                       "threshold": self.threshold, "directions": self.directions, "orientation": self.orientation,
                       "max_fps": self.max_fps, "max_frames": self.max_frames, "seconds": self.seconds,
                       "max_display_age_ms": MAX_DISPLAY_AGE_MS, "keepalive_seconds": client.keepalive_seconds},
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
            "frame_expired": expiry,
            "labels": {"basis": "bridge_frame_presented_hide_show_and_expiry_lines", "timebase": HOST_CLOCK,
                       "run_start_ns": self.run_start_ns, "run_end_ns": run_end_ns if self.run_start_ns else None,
                       "spells_dropped": spells_dropped, "physical_display_observed": False, "rows": labels},
            "pipeline": pipeline_block,
            "source": report_value(source_report, withheld),
            "bridge": bridge_block,
            "cleanup": {"source_ok": source_ok, "bridge_ok": bridge_ok,
                        "sinks_ok": pipeline is not None and pipeline.stats.sink_cleanup_ok,
                        "ok": source_ok and bridge_ok},
        }
        report["fields_withheld"] = withheld[0]
        return check_report(report)
