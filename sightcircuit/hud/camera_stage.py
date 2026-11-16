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
  error) the tile is taken down the moment ``stop`` is set, by a watcher
  thread that does not wait for the detector, and the bridge is then closed.
  A presence end sets ``stop`` when the source accepts the end packet.
* A slow or stopped detector sends nothing, and the bridge clears the tile by
  itself at its deadline.

Real output is not approved here: ``run`` asks the client's ``check_start``
first, which refuses ``--fixed-hud`` through the ``LIVE_HUD_OUTPUT`` gate
before a helper or a bridge is launched. The stage opens no gate, reads no
mapping record and saves no image.

The report (kind ``camera_hud_stage``) holds counts, labels, timings and
format metadata. ``check_report`` is its schema: it accepts numbers, booleans,
null, identifier-shaped strings and sanitised labels, short lists of scalars
and bounded lists of records, and nothing else, so no field can hold image
bytes or free text. ``CameraHudStage.report`` runs it on every report.
"""
import math
import re
import threading
import time

from .. import gates
from ..convert import to_bgr
from ..detection import POC_THRESHOLD
from ..observed_frames import BRIDGE_COUNTS, ObservedFrameClient
from ..overlay import BRIDGE_INPUT_POLL_NS, CLOSE_REPORT_FIELDS, rgba_from_bgr
from ..pipeline import PERCENTILE_METHOD, TIMINGS, Pipeline, TimingSample
from ..sources.camera_helper import CameraHelperSource
from .camera import ORIENTATIONS, camera_rows
from .contract import HUD_CONFIG, MAX_DISPLAY_AGE_MS, MAX_DISPLAY_AGE_NS, ROW_LABEL_CHARS, config_digest, row_label
from .render import draw_tile

__all__ = ("REPORT_KIND", "CameraHudStage", "DisplayLedger", "check_report", "report_value")

REPORT_KIND = "camera_hud_stage"
STATUSES = ("not_run", "completed", "stopped", "presence_ended", "cancelled", "error")
WATCHER_JOIN_SECONDS = 5.0

# ---- the report's schema ------------------------------------------------------------------------------------------

IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/+@-]{1,128}\Z")  # A name, code, digest or version: no space, no free text.
LABEL_KEY = "label"  # The one key whose string is a row label (``contract.row_label``) and may hold spaces.
MAX_SCALAR_LIST = 16  # A box, a pixel size, a camera list: never a buffer.
MAX_RECORD_LIST = 1024  # Records (``frame_expired`` events, per-label rows), each checked like any other object.
MAX_DEPTH = 8
# Keys of the embedded source and bridge reports that hold prose, paths or argument vectors: left out by name.
WITHHELD_KEYS = ("error", "detail", "path", "arguments", "definition", "scope", "observed_ns_clock")


def _label(value):
    return isinstance(value, str) and 0 < len(value) <= ROW_LABEL_CHARS and row_label(value) == value


def _leaf(value, key):
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is str:
        return _label(value) if key == LABEL_KEY else IDENTIFIER.match(value) is not None
    return False


def check_report(value, path="report", key=None, depth=0):
    """Raise ``ValueError('camera_hud_report_field_invalid:<path>')`` unless ``value`` fits the report schema.

    Accepted: null, booleans, integers, finite floats; strings shaped like an
    identifier (``IDENTIFIER``: at most 128 characters, no white space), or a
    sanitised row label under the key ``label``; objects with identifier keys;
    lists of at most ``MAX_SCALAR_LIST`` scalars or at most ``MAX_RECORD_LIST``
    objects. Bytes, buffers, arrays, tuples, long strings, prose, long numeric
    lists and anything nested deeper than ``MAX_DEPTH`` are refused, so
    neither a tile nor a camera frame fits in any field.
    """
    def refuse():
        raise ValueError(f"camera_hud_report_field_invalid:{path}")
    if depth > MAX_DEPTH:
        refuse()
    if type(value) is dict:
        for name, item in value.items():
            if type(name) is not str or IDENTIFIER.match(name) is None:
                refuse()
            check_report(item, f"{path}.{name}", name, depth + 1)
    elif type(value) is list:
        records = [item for item in value if type(item) is dict]
        if len(records) not in (0, len(value)) or len(value) > (MAX_RECORD_LIST if records else MAX_SCALAR_LIST):
            refuse()
        for index, item in enumerate(value):
            if not records and not _leaf(item, key):
                refuse()
            if records:
                check_report(item, f"{path}[{index}]", key, depth + 1)
    elif not _leaf(value, key):
        refuse()
    return value


def report_value(value, withheld, key=None, depth=0):
    """``value`` (a report of the source or the bridge) reduced to what ``check_report`` accepts.

    Keys in ``WITHHELD_KEYS`` are left out; any other value the schema would
    refuse becomes null and is counted in ``withheld[0]``, so a field that
    was dropped is visible as a count, never silently.
    """
    if depth > MAX_DEPTH:
        withheld[0] += 1
        return None
    if isinstance(value, dict):
        kept = {}
        for name, item in value.items():
            if name in WITHHELD_KEYS:
                continue
            if type(name) is not str or IDENTIFIER.match(name) is None:
                withheld[0] += 1
                continue
            kept[name] = report_value(item, withheld, name, depth + 1)
        return kept
    if isinstance(value, (list, tuple)):
        items = [report_value(item, withheld, key, depth + 1) for item in value]
        records = [item for item in items if type(item) is dict]
        if len(records) not in (0, len(items)) or len(items) > (MAX_RECORD_LIST if records else MAX_SCALAR_LIST):
            withheld[0] += 1
            return None
        return items
    if _leaf(value, key):
        return value
    withheld[0] += 1
    return None


def _identifier(value, fallback):
    return value if type(value) is str and IDENTIFIER.match(value) is not None else fallback


# ---- what was on display, as far as this side can tell --------------------------------------------------------------

class DisplayLedger:
    """Per label, how long the sent tiles named it and the longest time between two such spells.

    A tile counts from the moment its packet (or the show request that lets a
    hidden tile up) was written to the bridge's pipe until the earliest of
    its deadline, the next tile's write, a hide request's write and the end
    of the run. Those are this side's times on ``time.monotonic_ns()``; the
    bridge's upload and the runtime's composition come after them, and what a
    headset displayed is not known here.
    """

    def __init__(self):
        self.current = None  # (labels, shown_ns, expires_ns)
        self.labels = {}

    def shown(self, labels, at_ns, expires_ns):
        self.down(at_ns)
        if at_ns < expires_ns:
            self.current = (tuple(labels), at_ns, expires_ns)

    def down(self, at_ns):
        if self.current is None:
            return
        (labels, start, expires), self.current = self.current, None
        end = min(at_ns, expires)
        if end <= start:
            return
        for label in labels:
            entry = self.labels.setdefault(label, {"display_ns": 0, "spells": 0, "longest_gap_ns": 0,
                                                   "first_shown_ns": start, "last_end_ns": None})
            gap = start - entry["last_end_ns"] if entry["last_end_ns"] is not None else None
            if gap is None or gap > 0:
                entry["spells"] += 1
                entry["longest_gap_ns"] = max(entry["longest_gap_ns"], gap or 0)
            entry["display_ns"] += end - start
            entry["last_end_ns"] = end

    def report(self):
        return [{LABEL_KEY: label, "display_seconds": round(entry["display_ns"] / 1e9, 3),
                 "longest_gap_seconds": round(entry["longest_gap_ns"] / 1e9, 3), "spells": entry["spells"],
                 "first_shown_ns": entry["first_shown_ns"], "last_end_ns": entry["last_end_ns"]}
                for label, entry in sorted(self.labels.items())][:MAX_RECORD_LIST]


# ---- the stage ----------------------------------------------------------------------------------------------------

HOST_CLOCK = "host_clock_monotonic"  # ``time.monotonic_ns()``: the clock of ``Frame.observed_ns`` and of the bridge.


def _timing(sample, source, start, end, **more):
    return {**sample, "unit": "ms", "timebase": source, "from": start, "to": end, **more}


class CameraHudStage:
    """One bounded run of the camera HUD: ``CameraHelperSource`` → detector → rows → tile → observed frame.

    ``source`` is a ``CameraHelperSource`` and ``client`` an
    ``ObservedFrameClient``, neither started; ``detector`` is any detector of
    the pipeline. ``directions`` and ``orientation`` are ``camera_rows``'
    (owner decision 3 and the device step that measures the camera image's
    orientation choose them; the defaults are not findings). ``max_fps``,
    ``max_frames`` and ``seconds`` bound the pipeline as they do elsewhere;
    the helper's own limits are the source's. ``config`` must be the fixed
    HUD configuration, the only tile the bridge's fixed mode takes.

    ``run(stop)`` must be called on the main thread (the supervision
    primitive takes the process's signals). It refuses real output first
    (``client.check_start``), then drives the pipeline, and returns
    ``report()``; an error of the run is the report's ``status`` and
    ``reason`` (and ``failure`` on the stage), not an exception, so the
    cleanup verdicts are always reported. A stage runs once.

    The stage is the pipeline's only sink: ``open`` starts the bridge,
    ``submit`` sends or withholds one result, ``clear`` takes the tile down
    and ``close`` stops the bridge. A watcher thread takes the tile down the
    moment ``stop`` is set, without waiting for a detector call in flight.
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
        self.source, self.detector, self.client, self.config = source, detector, client, config
        self.threshold, self.directions, self.orientation = threshold, directions, orientation
        self.max_fps, self.max_frames, self.seconds, self.convert = max_fps, max_frames, seconds, convert
        self.pipeline = self.failure = None
        self.status, self.reason = "not_run", None
        self.ledger = DisplayLedger()
        self.counts = dict.fromkeys(("results", "results_with_rows", "results_without_rows", "results_too_old",
                                     "results_after_end", "frames_queued", "frames_sent", "frames_not_sent",
                                     "frames_written_past_deadline"), 0)
        self.first_frame_id = self.last_frame_id = self.last_result_index = None
        self.first_sent = None  # (frame_id, observed_ns, sent_ns) of the first frame written.
        self.max_deadline_span_ns = 0
        self.output_age = TimingSample()
        self.ended_ns = None  # When the watcher saw ``stop`` (``time.monotonic_ns()``).
        self._stop_seen = None
        self._ended = threading.Event()
        self._lock = threading.Lock()
        self._rendered = None  # (result, rows) of the tile ``_tile`` drew last, for ``submit``.
        self._awaiting_show = None  # (labels, expires_ns) of a frame sent while the tile was taken down.
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
        if self.client.submit_observed(frame.sequence, frame.observed_ns, expires_ns, width, height, rgba, token=labels):
            counts["frames_queued"] += 1
        else:
            counts["results_after_end"] += 1

    def clear(self):
        self.client.take_down()

    def close(self):
        """Stop the bridge (once) and close the display ledger; True only for a confirmed clean bridge shutdown."""
        report = self.client.close()
        with self._lock:
            if self._bridge_report is None:
                self._bridge_report = report
                self.ledger.down(self.ended_ns if self.ended_ns is not None else time.monotonic_ns())
        return report.get("cleanup_confirmed") is True

    # -- the writer thread's record of what went out -------------------------------------------------------------

    def _on_packet(self, event, **fields):
        with self._lock:
            if event == "frame_sent":
                self.counts["frames_sent"] += 1
                frame_id, observed_ns, expires_ns, sent_ns = (fields[name] for name in
                                                              ("frame_id", "observed_ns", "expires_ns", "sent_ns"))
                if self.first_sent is None:
                    self.first_sent, self.first_frame_id = (frame_id, observed_ns, sent_ns), frame_id
                self.last_frame_id = frame_id
                self.max_deadline_span_ns = max(self.max_deadline_span_ns, expires_ns - observed_ns)
                self.output_age.add((sent_ns - observed_ns) / 1e6)
                if sent_ns >= expires_ns:  # Written too late: the bridge refuses it on arrival and nothing is shown.
                    self.counts["frames_written_past_deadline"] += 1
                elif fields["hidden"]:
                    self._awaiting_show = (fields["token"], expires_ns)
                else:
                    self.ledger.shown(fields["token"], sent_ns, expires_ns)
            elif event == "shown":
                if self._awaiting_show is not None:
                    (labels, expires_ns), self._awaiting_show = self._awaiting_show, None
                    self.ledger.shown(labels, fields["sent_ns"], expires_ns)
            elif event == "taken_down":
                self._awaiting_show = None
                self.ledger.down(fields["sent_ns"])
            elif event == "frame_not_sent":
                self.counts["frames_not_sent"] += 1

    # -- the run -------------------------------------------------------------------------------------------------

    def _watch(self, stop):
        stop.wait()
        self._stop_seen, self.ended_ns = time.monotonic(), time.monotonic_ns()
        self._ended.set()
        self.client.take_down()  # At once, whatever the pipeline's thread is doing.

    def run(self, stop=None):
        """Run once, on the main thread; returns ``report()``. Real output is refused before anything is launched."""
        if self._ran:
            raise ValueError("camera_hud_stage_already_run")
        self._ran = True
        self.client.check_start()
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

    def _outcome(self):
        source, pipeline, failure = self.source, self.pipeline, self.failure
        if failure is not None:
            fallback = "overlay_bridge_failed" if self.client.error else "camera_hud_run_failed"
            return "error", _identifier(getattr(failure, "reason", None), None) or _identifier(str(failure), fallback)
        if self.client.error:
            return "error", _identifier(self.client.error, "overlay_bridge_failed")
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

    def _timings(self, stats, bridge_counts):
        blocks = {name: stats[name] for name in TIMINGS} if stats is not None else {
            name: TimingSample().report() for name in TIMINGS}
        first_line = self.client.first_frame
        display = TimingSample()
        # The bridge acknowledges one frame only: its first-frame line. It is the first frame written here when
        # at most that one had been written when the line was read and the bridge refused none.
        attributable = (first_line is not None and self.first_sent is not None
                        and (self.client.frames_sent_before_first_frame_line or 0) <= 1
                        and not (bridge_counts.get("stale_frames_dropped") or 0)
                        and not (bridge_counts.get("frames_expired_in_upload") or 0))
        if attributable:
            display.add((first_line["read_ns"] - self.first_sent[1]) / 1e6)
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
            # The bridge writes no line per presented frame, so the last instant this side knows of a frame is the
            # completed write of its packet to the bridge's pipe; the upload and the show come after it.
            "output_age_ms": _timing(self.output_age.report(), HOST_CLOCK, "camera_frame_observed",
                                     "packet_written_to_bridge_pipe", bridge_acknowledged=False,
                                     why="bridge_reports_no_per_frame_acknowledgement"),
            "capture_to_display_age_ms": _timing(
                display.report(), HOST_CLOCK, "camera_frame_observed", "bridge_first_frame_line_read",
                measured="first_frame_only", frames_without_acknowledgement=max(0, sent - display.count),
                lower_bound_for_other_frames="output_age_ms", why="bridge_reports_no_per_frame_acknowledgement",
                physical_display_observed=False)}

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
            events.append(record)
        return {"count": self.client.expiry_event_count, "recorded": len(events),
                "dropped": self.client.expiry_events_dropped, "bridge_poll_ms": poll_ms,
                "max_noticed_after_deadline_ms": late_notice, "max_hide_returned_after_deadline_ms": late_hide,
                "hide_times_reported": not validation_only, "events": events}

    def report(self):
        """The run's report: counts, labels, timings and format metadata, checked against ``check_report``."""
        client, source, pipeline = self.client, self.source, self.pipeline
        withheld = [0]
        with self._lock:
            bridge = self._bridge_report
            counts = dict(self.counts)
            labels = self.ledger.report()
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
                "visibility_requests", "settings_sent")}
            bridge_block["error"] = None if bridge.get("error") is None else _identifier(bridge["error"],
                                                                                          "overlay_bridge_failed")
            bridge_block["lines"] = report_value(dict(client.line_counts), withheld)
            first = client.first_frame
            bridge_block["first_frame"] = None if first is None else {
                "event": first["event"], "width": first["width"], "height": first["height"], "read_ns": first["read_ns"]}
            bridge_block["final_report"] = report_value({**native, "bridge_error": native.get("error")}, withheld)
        source_ok = source_report.get("cleanup_ok") is True
        bridge_ok = bridge is not None and bridge.get("cleanup_confirmed") is True
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
                       # The bridge's own counts: taken, presented, sent again unchanged, refused as stale or
                       # expired on arrival, and expired after presentation.
                       "bridge_received": bridge_counts["observed_frames_received"],
                       "bridge_presented": bridge_counts["frames_received"],
                       "retransmitted": bridge_counts["frames_retransmitted"],
                       "refused": bridge_counts["stale_frames_dropped"],
                       "expired": bridge_counts["expired_frames"],
                       "expired_in_upload": bridge_counts["frames_expired_in_upload"]},
            "deadlines": {"cap_ms": MAX_DISPLAY_AGE_MS,
                          "max_deadline_after_observation_ms": round(self.max_deadline_span_ns / 1e6, 3),
                          "within_cap": self.max_deadline_span_ns <= MAX_DISPLAY_AGE_NS,
                          "ended_ns": self.ended_ns},
            "timings": self._timings(stats, bridge_counts),
            "frame_expired": self._expiry(client.validation_only),
            "labels": {"basis": "packet_write_to_deadline_or_replacement", "timebase": HOST_CLOCK,
                       "physical_display_observed": False, "rows": labels},
            "pipeline": pipeline_block,
            "source": report_value(source_report, withheld),
            "bridge": bridge_block,
            "cleanup": {"source_ok": source_ok, "bridge_ok": bridge_ok,
                        "sinks_ok": pipeline is not None and pipeline.stats.sink_cleanup_ok,
                        "ok": source_ok and bridge_ok},
        }
        report["fields_withheld"] = withheld[0]
        report["frame_expired"]["events"] = report_value(report["frame_expired"]["events"], withheld)
        report["fields_withheld"] = withheld[0]
        return check_report(report)
