"""Synthetic counter rows on the fixed HUD (work-order item L9): the driver of device step L-D3, no camera.

``SyntheticRows`` sends the bridge one observed frame five times a second
through ``ObservedFrameClient``: a tile with one names-only row, ``count
NNNN``, drawn by ``hud.render.draw_tile``. Nothing is read from a camera, a
mirror or a file, and no detector runs. Between segments of updates the
schedule holds scheduled stops in which nothing is sent, not even a hide
request, so the bridge itself must take the tile down at the last frame's
deadline, one second after its observation, and say so with a
``frame_expired`` line. The report pairs each stop with that line.

Real output is gated exactly as the stage's: ``run`` asks the client's
``check_start`` first, which refuses ``--fixed-hud`` through the
``LIVE_HUD_OUTPUT`` gate unless a one-run scope record for the ``synthetic``
source verifies (``runtime/scope.py``), before the bridge is launched. On the
validation bridge nothing is displayed and nothing is asked.

The report (kind ``camera_hud_synthetic_rows``) holds counts and timings and
passes ``camera_stage.check_report`` with ``REPORT_BLOCKS`` below. Its times
are when the bridge's calls returned; what a headset displayed is not known
here, and the wearer's observation of the blank tile is the device report's.
"""
import threading
import time

from .. import gates
from ..observed_frames import BRIDGE_COUNTS, ObservedFrameClient
from ..overlay import BRIDGE_INPUT_POLL_NS, CLOSE_REPORT_FIELDS, rgba_from_bgr
from .camera_stage import HOST_CLOCK, MAX_REPORTED_EXPIRIES, _identifier, check_report, report_value
from .contract import (HUD_CONFIG, MAX_DISPLAY_AGE_MS, MAX_DISPLAY_AGE_NS, NO_DIRECTION, HudRow, config_digest,
                       row_label)
from .render import draw_tile

__all__ = ("MAX_SECONDS", "REPORT_BLOCKS", "REPORT_KIND", "UPDATE_HZ", "SyntheticRows", "counter_label", "schedule")

REPORT_KIND = "camera_hud_synthetic_rows"
UPDATE_HZ = 5
MAX_SECONDS = 120  # The L-D3 record's own bound (``scope.STEPS``).
MAX_STOPS = 8
# A stop must outlast the tile's deadline (one second after its observation) with room to read the bridge's line.
MIN_STOP_SECONDS, MAX_STOP_SECONDS = 2.0, 10.0
MIN_SEGMENT_UPDATES = UPDATE_HZ  # Every segment shows at least one second of updates.
POLL_MS = BRIDGE_INPUT_POLL_NS / 1e6
EXPIRY_BOUND_MS = MAX_DISPLAY_AGE_MS + POLL_MS  # G9: within 1,000 ms plus one poll of the last frame's observation.
LINE_WAIT_SECONDS = 0.5  # After a stop, how long to wait for the bridge's line before calling it missing.
STATUSES = ("not_run", "completed", "stopped", "error")
REPORT_BLOCKS = {
    "report_kind": None, "schema_version": None, "status": None, "reason": None, "error_type": None,
    **dict.fromkeys(gates.claims()),
    "validation_only": None, "bridge_mode": None, "images_saved": None, "pixels_in_report": None,
    "physical_display_observed": None, "camera_opened": None, "camera_derived": None, "complete": None,
    "fields_withheld": None,
    "config": ("hud_config_id", "hud_config_sha256", "tile_px", "update_hz", "seconds", "stops", "stop_seconds",
               "max_display_age_ms", "keepalive_seconds", "row"),
    "updates": ("requested", "submitted", "sent", "replaced", "expired_before_send", "acknowledged_presented",
                "acknowledged_refused", "refused_reasons", "unacknowledged", "bridge_received", "bridge_accepted",
                "first_frame_id", "last_frame_id"),
    "stops": ("requested", "reached", "expired", "within_bound", "bound_ms", "bridge_poll_ms", "basis",
              "hide_times_reported", "events"),
    "frame_expired": ("count", "recorded", "dropped", "events"),
    "end": ("run_start_ns", "ended_ns", "timebase", "elapsed_ms"),
    "bridge": (*CLOSE_REPORT_FIELDS, "started", "mode", "frames_submitted", "frames_replaced", "frames_withdrawn",
               "frames_expired_before_send", "frames_retransmitted", "take_downs", "shows", "keepalives",
               "visibility_requests", "settings_sent", "frames_presented", "frames_refused", "error", "lines",
               "final_report"),
    "cleanup": ("bridge_ok", "ok"),
}


def counter_label(number):
    """The one row's text for update ``number``: ``count 0001``. A fixed word and a number, nothing else."""
    return row_label(f"count {number:04d}")


def schedule(seconds, stops, stop_seconds):
    """The run's plan: ``[("rows", updates), ("stop", seconds), ..., ("rows", updates)]``, ``stops`` stops in all.

    ``seconds`` is the whole run. The updates fill what the stops leave, at
    ``UPDATE_HZ``, split evenly over ``stops + 1`` segments (earlier ones
    take the remainder). Refuses (``ValueError``) a run over ``MAX_SECONDS``,
    more than ``MAX_STOPS`` stops, a stop outside ``MIN_STOP_SECONDS`` to
    ``MAX_STOP_SECONDS``, and a plan whose segments would hold less than one
    second of updates each.
    """
    if type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS:
        raise ValueError("synthetic_rows_seconds_out_of_range")
    if type(stops) is not int or not 0 <= stops <= MAX_STOPS:
        raise ValueError("synthetic_rows_stops_out_of_range")
    if type(stop_seconds) not in (int, float) or not MIN_STOP_SECONDS <= stop_seconds <= MAX_STOP_SECONDS:
        raise ValueError("synthetic_rows_stop_seconds_out_of_range")
    updates = int(round((seconds - stops * stop_seconds) * UPDATE_HZ))
    each, extra = divmod(updates, stops + 1)
    if updates <= 0 or each < MIN_SEGMENT_UPDATES:
        raise ValueError("synthetic_rows_schedule_too_short")
    plan = []
    for segment in range(stops + 1):
        plan.append(("rows", each + (1 if segment < extra else 0)))
        if segment < stops:
            plan.append(("stop", float(stop_seconds)))
    return plan


class SyntheticRows:
    """One bounded run of counter rows into the bridge's observed-frame path, with scheduled stops.

    ``client`` is an ``ObservedFrameClient``, not started. ``run(stop)``
    refuses real output first (``client.check_start``), then follows
    ``schedule(seconds, stops, stop_seconds)`` and returns ``report()``. An
    error of the run is the report's ``status`` and ``reason``, not an
    exception; only the gate's refusal is raised, before anything is
    launched. ``stop`` (an event) ends the run early; the client is always
    closed, which takes the tile down. A driver runs once.
    """

    def __init__(self, client, *, seconds, stops=3, stop_seconds=3.0, config=HUD_CONFIG):
        if not isinstance(client, ObservedFrameClient):
            raise ValueError("synthetic_rows_requires_observed_frame_client")
        if config != HUD_CONFIG:
            raise ValueError("synthetic_rows_config_not_fixed")
        self.plan = schedule(seconds, stops, stop_seconds)
        self.client, self.config = client, config
        self.seconds, self.stops, self.stop_seconds = seconds, stops, float(stop_seconds)
        self.requested = sum(value for kind, value in self.plan if kind == "rows")
        self.status, self.reason, self.failure = "not_run", None, None
        self.submitted = 0
        self.first_frame_id = self.last_frame_id = None
        self.stop_records = []  # One per stop reached: the segment's last frame and when the stop began and ended.
        self.run_start_ns = self.ended_ns = None
        self._bridge_report = None
        self._ran = False

    def _tile(self, number):
        rows = (HudRow(counter_label(number), 1.0, NO_DIRECTION),)
        width, height, rgba = rgba_from_bgr(draw_tile(rows, self.config))
        if (width, height) != (self.config.width_px, self.config.height_px):
            raise ValueError("synthetic_rows_tile_size_mismatch")
        return width, height, rgba

    def _check_client(self):
        if self.client.error:
            raise RuntimeError(self.client.error)
        if self.client.done.is_set():
            raise RuntimeError("overlay_bridge_ended")

    def _drive(self, stop):
        client = self.client
        client.start()
        self.run_start_ns = time.monotonic_ns()
        due, number, last = time.monotonic(), 0, None
        for kind, value in self.plan:
            if kind == "stop":
                record = {"last_frame_id": last[0], "last_observed_ns": last[1], "began_ns": time.monotonic_ns()}
                self.stop_records.append(record)
                due += value
                while (remaining := due - time.monotonic()) > 0:
                    if stop.wait(min(remaining, 0.1)):
                        return
                    self._check_client()
                record["ended_ns"] = time.monotonic_ns()
                continue
            for _ in range(value):
                while (remaining := due - time.monotonic()) > 0:
                    if stop.wait(remaining):
                        return
                if stop.is_set():
                    return
                self._check_client()
                number += 1
                width, height, rgba = self._tile(number)
                observed_ns = time.monotonic_ns()
                if not client.submit_observed(number, observed_ns, observed_ns + MAX_DISPLAY_AGE_NS, width, height, rgba):
                    raise RuntimeError(client.error or "observed_frame_client_not_running")
                self.submitted += 1
                if self.first_frame_id is None:
                    self.first_frame_id = number
                self.last_frame_id, last = number, (number, observed_ns)
                due += 1 / UPDATE_HZ
        # The last update's own period, so it is written and answered before the end.
        while (remaining := due - time.monotonic()) > 0:
            if stop.wait(remaining):
                return

    def run(self, stop=None):
        """Run once; returns ``report()``. Real output is refused before the bridge is launched."""
        if self._ran:
            raise ValueError("synthetic_rows_already_run")
        self._ran = True
        self.client.check_start()
        stop = threading.Event() if stop is None else stop
        try:
            self._drive(stop)
            self.status, self.reason = ("stopped", "stop_requested") if stop.is_set() else ("completed", "schedule")
        except Exception as error:
            self.failure = error
            self.status = "error"
            self.reason = _identifier(self.client.error, None) or _identifier(str(error), "synthetic_rows_failed")
        finally:
            self.ended_ns = time.monotonic_ns()
            self.client.withdraw()  # A run that failed before the bridge started gives its scope back.
            self._bridge_report = self.client.close()  # Every line the bridge wrote has been read when this returns.
        if self.status != "error" and self.client.error:
            self.status, self.reason = "error", _identifier(self.client.error, "overlay_bridge_failed")
        return self.report()

    def _stops(self, validation_only):
        """Each stop paired with the bridge's ``frame_expired`` line for the frame that was up when it began."""
        expired = {event.get("frame_id"): event for event in self.client.expiry_events}
        events, count_expired, all_within = [], 0, True
        for index, record in enumerate(self.stop_records, 1):
            event, observed = expired.get(record["last_frame_id"]), record["last_observed_ns"]

            def after(name, event=event, observed=observed):
                value = None if event is None else event.get(name)
                return None if type(value) is not int else round((value - observed) / 1e6, 3)
            # Real output: the time the runtime's hide call returned. Validation: when the bridge noticed.
            measured = after("observed_ns") if validation_only else after("hide_returned_ns")
            within = (event is not None and measured is not None and measured <= EXPIRY_BOUND_MS and
                      (validation_only or (event.get("hide_ok") is True and event.get("api_visible_after_hide") is False)))
            count_expired += event is not None
            all_within = all_within and within
            events.append({
                "index": index, "last_frame_id": record["last_frame_id"], "last_observed_ns": observed,
                "stop_began_ns": record["began_ns"], "stop_ended_ns": record.get("ended_ns"),
                "expired": event is not None,
                "deadline_after_observation_ms": after("deadline_ns"),
                "noticed_after_observation_ms": after("observed_ns"),
                "hide_called_after_observation_ms": after("hide_called_ns"),
                "hide_returned_after_observation_ms": after("hide_returned_ns"),
                "hide_ok": None if event is None else event.get("hide_ok"),
                "api_visible_after_hide": None if event is None else event.get("api_visible_after_hide"),
                "texture_cleared": None if event is None else event.get("texture_cleared"),
                "within_bound": within})
        return {"requested": self.stops, "reached": len(events), "expired": count_expired,
                "within_bound": bool(events) and all_within if self.stops else True,
                "bound_ms": EXPIRY_BOUND_MS, "bridge_poll_ms": POLL_MS,
                "basis": "bridge_noticed_expiry" if validation_only else "runtime_hide_returned",
                "hide_times_reported": not validation_only, "events": events}

    def report(self):
        """The run's report: counts and timings, checked against ``check_report`` with this module's blocks."""
        client, bridge, withheld = self.client, self._bridge_report, [0]
        native = (bridge or {}).get("native_report") or {}
        bridge_counts = {name: native.get(name) for name in BRIDGE_COUNTS}
        bridge_block = None
        if bridge is not None:
            bridge_block = {name: bridge.get(name) for name in (
                *CLOSE_REPORT_FIELDS, "started", "mode", "frames_submitted", "frames_replaced", "frames_withdrawn",
                "frames_expired_before_send", "frames_retransmitted", "take_downs", "shows", "keepalives",
                "visibility_requests", "settings_sent", "frames_presented", "frames_refused")}
            bridge_block["error"] = None if bridge.get("error") is None else _identifier(client.error, "overlay_bridge_failed")
            bridge_block["lines"] = report_value(dict(client.line_counts), withheld)
            bridge_block["final_report"] = report_value({**native, "bridge_error": native.get("error")}, withheld)
        bridge_ok = bridge is not None and bridge.get("cleanup_confirmed") is True
        stops = self._stops(client.validation_only)
        stops["events"] = report_value(stops["events"], withheld)
        sent = client.frames_sent
        complete = (self.status == "completed" and bridge_ok and self.submitted == self.requested == client.presented_count
                    and client.frames_replaced == 0 and client.refused_count == 0 and stops["reached"] == self.stops
                    and stops["within_bound"])
        report = {
            "report_kind": REPORT_KIND, "schema_version": 1, "status": self.status, "reason": self.reason,
            "error_type": _identifier(type(self.failure).__name__, None) if self.failure is not None else None,
            **gates.claims(), "validation_only": client.validation_only, "bridge_mode": client.bridge_mode(),
            "images_saved": False, "pixels_in_report": False, "physical_display_observed": False,
            "camera_opened": False, "camera_derived": False, "complete": complete,
            "config": {"hud_config_id": self.config.layout_id, "hud_config_sha256": config_digest(self.config),
                       "tile_px": [self.config.width_px, self.config.height_px], "update_hz": UPDATE_HZ,
                       "seconds": self.seconds, "stops": self.stops, "stop_seconds": self.stop_seconds,
                       "max_display_age_ms": MAX_DISPLAY_AGE_MS, "keepalive_seconds": client.keepalive_seconds,
                       "row": "names_only_counter"},
            "updates": {"requested": self.requested, "submitted": self.submitted, "sent": sent,
                        "replaced": client.frames_replaced, "expired_before_send": client.frames_expired_before_send,
                        "acknowledged_presented": client.presented_count, "acknowledged_refused": client.refused_count,
                        "refused_reasons": dict(client.refused_reasons),
                        "unacknowledged": max(0, sent - client.presented_count - client.refused_count),
                        "bridge_received": bridge_counts["observed_frames_received"],
                        "bridge_accepted": bridge_counts["frames_received"],
                        "first_frame_id": self.first_frame_id, "last_frame_id": self.last_frame_id},
            "stops": stops,
            "frame_expired": {"count": client.expiry_event_count,
                              "recorded": min(len(client.expiry_events), MAX_REPORTED_EXPIRIES),
                              "dropped": client.expiry_event_count - min(len(client.expiry_events), MAX_REPORTED_EXPIRIES),
                              "events": report_value([dict(event) for event in
                                                      client.expiry_events[:MAX_REPORTED_EXPIRIES]], withheld)},
            "end": {"run_start_ns": self.run_start_ns, "ended_ns": self.ended_ns, "timebase": HOST_CLOCK,
                    "elapsed_ms": None if self.run_start_ns is None or self.ended_ns is None else
                    round((self.ended_ns - self.run_start_ns) / 1e6, 3)},
            "bridge": bridge_block,
            "cleanup": {"bridge_ok": bridge_ok, "ok": bridge_ok},
        }
        report["fields_withheld"] = withheld[0]
        return check_report(report, REPORT_BLOCKS)
