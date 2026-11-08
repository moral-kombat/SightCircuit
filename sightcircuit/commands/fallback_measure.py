"""Measure the fallback's device terms with the calibration marker; never a HUD run (fallback item F3b).

The time-multiplexed fallback (``docs/reference/hud-fallback.md``) needs four
device measurements before its admission window can be built: the
hide-to-absence latency B, the hidden-event delivery, the loopback's repeat
behaviour and the show-to-visible latency. This command produces all four from
one bounded run without opening a gate: it shows nothing but the calibration
marker, through the bridge's marker mode (the one tile the device is approved
to display, and the one pattern ``locate_copies`` reads from the mirror), drives
hide and show cycles on it with F1's visibility opcode (accepted by the marker
mode since F3a) through ``MarkerClient.request_visibility`` and the F2 outcome
reading, and reads the mirror at the capture thread's full rate, digesting every
frame and asking ``locate_copies`` whether a complete marker copy is present.

The report carries counts, nanosecond timestamps and frame digests, never
pixels, with one section per measurement so that one approved device run can
give the four evidence entries the work order lists. Every timestamp is on one
clock: the bridge's ``CLOCK_MONOTONIC`` (``monotonic_ns()``) and the mirror
read's ``time.monotonic_ns()`` on the same host. A time-namespace difference
between the two processes would break the comparison silently; the
``timebase_id`` rule of the session tokens
(``docs/reference/hud-session.md#timebase_id``) states that caveat and the run
record must say both ran in one namespace.

``--validate`` runs the same cycles against the bridge's validation marker
mode: no VR is initialised, no device is read, every hide ends
``visibility_event_missing`` and every show returns ``no_image``, so the
command's own paths are exercised with nothing shown. The device run needs
its own approval and the bridge rebuilt on the Frame first; the checkpoint
manifest is verified before anything starts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

if not __package__:  # Run as a file (self-spawned worker): import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sightcircuit.gates import claims  # noqa: E402
from sightcircuit.hud.contract import (  # noqa: E402
    MARKER_MAX_SECONDS, MARKER_MIN_SECONDS, VISIBILITY_EVENT_BOUND_NS, VISIBILITY_MISSING_LINE)
from sightcircuit.hud.marker import MarkerClient, validate_visibility_evidence  # noqa: E402
from sightcircuit.hud.observation import locate_copies, native_bgr  # noqa: E402
from sightcircuit.overlay import (  # noqa: E402
    BRIDGE_INPUT_POLL_NS, VISIBILITY_TERMINAL_DEADLINE_NS, bridge_closed_cleanly)
from sightcircuit.runtime.guard import stop_on_signals, supervise  # noqa: E402
from sightcircuit.runtime.manifest import verify_manifest  # noqa: E402
from sightcircuit.sources.mirror import capture  # noqa: E402
from sightcircuit.sources.mirror_device import discover, select_candidate  # noqa: E402

REPORT_KIND = "fallback_measurement"
SCHEMA_VERSION = 1
SCOPE = ("Device timing terms of the fallback design measured on the calibration marker in marker mode: counts, "
         "nanoseconds and frame digests only; no HUD output, no live input, no gate opened, no mapping")
APPROVED_SCOPE = "fallback_measurement_marker_only"
# The four measurement kinds, as docs/reference/hud-fallback.md#device-evidence-needed lists them; the report
# has one section under each name.
MEASUREMENTS = ("hide_to_absence", "hidden_event_delivery", "loopback_repeats", "show_to_visible")
# Decode outcomes of locate_copies that mean "no complete copy in this read" rather than an analysis failure.
INCOMPLETE_COPY_REASONS = ("orphan_marker_tag_or_incomplete_copy", "ambiguous_marker_orientation",
                           "ambiguous_overlapping_marker_copies")
DEFAULT_CYCLES = 5
MAX_CYCLES = 20
# The capture thread's full read rate (sources.mirror.capture's ceiling), the rate the design reads at.
READ_RATE_HZ = 10
# The costed pacing floor of the fallback (one request per 500 ms or slower): a cycle's show follows its hide,
# and the next hide follows the show, no sooner than this.
REQUEST_GAP_NS = 500_000_000
# How long a phase waits for the mirror to reflect its transition (the tile absent after a hide, present after
# a show) before it is recorded without one. A cloud choice, four times the illustrative 500 ms cycle, unmeasured
# on the Frame; a transition slower than this is itself the finding.
PHASE_BOUND_NS = 2_000_000_000
# After the bridge's ready line, how long the marker may take to appear in the mirror before the run is
# rejected for cycling: the observer's warm-up (hud_marker_observer.MARKER_WARMUP_SECONDS), for the same path.
MARKER_WARMUP_SECONDS = 2.0
# The marker bridge polls stdin every 10 ms (kMarkerPollMs, F3a) where the stream loop polls every 20 ms
# (BRIDGE_INPUT_POLL_NS): a missing line is written on the first poll at or after the bound, so it arrives within
# 10 ms of VISIBILITY_EVENT_BOUND_NS and the F2 terminal deadline (the bound, one 20 ms stream poll and a 50 ms
# margin) holds for the marker client with the poll difference to spare. Pinned against the bridge header and the
# F2 derivation by tests/test_fallback_measure.py.
MARKER_INPUT_POLL_NS = 10_000_000
assert MARKER_INPUT_POLL_NS <= BRIDGE_INPUT_POLL_NS
# No request may reach the bridge inside the final poll window at the display deadline: a show processed there
# meets Overlay::visibility()'s calibration_deadline_before_show and ends the run with exit 1 instead of a
# missing line (overlay.md, marker mode input). The last request of a run is therefore sent no later than this
# before the deadline: the client's terminal deadline (so the await ends while the bridge still runs and its
# missing line, if any, is still written), one marker poll (the request is decoded within it) and a 100 ms
# margin for the pipe write, the OpenVR call and scheduling. A request that would land inside it is not sent:
# the run is rejected request_inside_display_deadline_margin, fail closed, which CYCLE_RESERVE_NS makes
# unreachable in a run that keeps its pacing.
LAST_REQUEST_MARGIN_NS = VISIBILITY_TERMINAL_DEADLINE_NS + MARKER_INPUT_POLL_NS + 100_000_000
# A cycle starts only with this much of the marker's display left: its hide phase may run to the phase bound
# (or the pacing floor, whichever is longer) before the show request, which must still be LAST_REQUEST_MARGIN_NS
# before the deadline; the show phase that follows makes no call and may run to the deadline.
CYCLE_RESERVE_NS = max(PHASE_BOUND_NS, REQUEST_GAP_NS) + LAST_REQUEST_MARGIN_NS
# The supervisor's bound is --seconds plus this margin, counted from the worker's start (as the observer's).
SUPERVISOR_MARGIN_SECONDS = 12
TIMEBASE_CAVEAT = ("Bridge timestamps (CLOCK_MONOTONIC) and mirror observed_ns (time.monotonic_ns) are compared "
                   "as one clock; both processes must run in the same time namespace with the same offset "
                   "(docs/reference/hud-session.md#timebase_id), which the run record must state")


def new_report(validation_only=False, *, cycles=DEFAULT_CYCLES):
    return {"report_kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "status": "not_run", "exit_code": 3,
            "validation_only": validation_only, "cycles_requested": cycles, "cycles_completed": 0,
            "cycles_not_started": cycles, "cleanup_ok": True, "capture_started": False, "images_saved": False,
            "inference_started": False, **claims(), "physical_display_recovery_verified": False,
            "camera_freshness_verified": False, "scope": SCOPE,
            "clock": {"domain": "CLOCK_MONOTONIC", "bridge": "monotonic_ns() of the bridge process",
                      "mirror": "time.monotonic_ns() when the mirror read returned (an observation time, never "
                                "an exposure time)", "same_host_required": True, "timebase_caveat": TIMEBASE_CAVEAT},
            "pacing": {"read_rate_hz": READ_RATE_HZ, "request_gap_ns": REQUEST_GAP_NS,
                       "phase_bound_ns": PHASE_BOUND_NS, "marker_warmup_seconds": MARKER_WARMUP_SECONDS,
                       "visibility_event_bound_ns": VISIBILITY_EVENT_BOUND_NS,
                       "terminal_deadline_ns": VISIBILITY_TERMINAL_DEADLINE_NS,
                       "marker_input_poll_ns": MARKER_INPUT_POLL_NS, "last_request_margin_ns": LAST_REQUEST_MARGIN_NS,
                       "cycle_reserve_ns": CYCLE_RESERVE_NS},
            **{name: {"measured": False} for name in MEASUREMENTS}}


def frame_record(sequence, observed_ns, image):
    """One mirror read's bookkeeping: its digest and whether a complete marker copy is present; no pixels kept."""
    digest = hashlib.blake2b(memoryview(image), digest_size=16).hexdigest()
    try:
        present = bool(locate_copies(image))
        decode = "complete_copy" if present else "no_complete_copy"
    except ValueError as error:
        if str(error) not in INCOMPLETE_COPY_REASONS:
            raise
        present, decode = False, str(error)
    return {"sequence": sequence, "observed_ns": observed_ns, "digest": digest, "present": present, "decode": decode}


def transition_bounds(reads, request_ns, want_present):
    """The mirror's bounds on one transition requested at ``request_ns`` (the bridge's clock).

    ``want_present`` is False for a hide (the tile should vanish) and True for a
    show. The upper bound is the first read at or after the request whose
    presence is the wanted one; the lower bound is the last read before it,
    whether before or after the request, that still showed the previous
    state, floored at the request itself (the transition cannot precede its
    request). Returns the two observed times (None when not seen), the bounds
    relative to the request, and how many reads fell between the request and
    the transition.
    """
    first_target = last_opposite = None
    examined = 0
    for read in reads:
        if read["observed_ns"] < request_ns:
            if read["present"] != want_present:
                last_opposite = read["observed_ns"]
            continue
        examined += 1
        if read["present"] == want_present:
            first_target = read["observed_ns"]
            break
        last_opposite = read["observed_ns"]
    target, opposite = ("first_present", "last_absent") if want_present else ("first_absent", "last_present")
    record = {f"{target}_observed_ns": first_target, f"{opposite}_observed_ns": last_opposite,
              "reads_after_request_before_transition": examined - (1 if first_target is not None else 0),
              "upper_bound_after_request_ns": None if first_target is None else first_target - request_ns,
              "lower_bound_after_request_ns": None if first_target is None else
              max(0, (last_opposite if last_opposite is not None else request_ns) - request_ns)}
    return record


def repeat_counts(reads):
    """Consecutive identical full-frame digests in ``reads``: the loopback-repeat census."""
    identical = longest = run = 0
    previous = None
    for read in reads:
        if previous is not None and read["digest"] == previous:
            identical += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
        previous = read["digest"]
    gaps = [b["observed_ns"] - a["observed_ns"] for a, b in zip(reads, reads[1:])]
    return {"reads": len(reads), "identical_consecutive_digests": identical, "longest_repeat_run": longest,
            "distinct_digests": len({read["digest"] for read in reads}),
            "maximum_read_gap_ns": max(gaps) if gaps else None,
            "reads_with_complete_copy": sum(read["present"] for read in reads)}


def outcome_record(outcome):
    """The F2 outcome of one request as report fields (names of lines and reasons, nanoseconds; nothing else)."""
    event_ns = outcome.observed_ns if outcome.confirmed else None
    return {"request_ns": outcome.request_ns, "returned_ns": outcome.returned_ns, "call": outcome.call,
            "terminal": outcome.terminal, "confirmed": outcome.confirmed, "reason": outcome.reason,
            "event_observed_ns": event_ns, "waited_ns": outcome.waited_ns,
            "event_after_returned_ns": None if event_ns is None or outcome.returned_ns is None
            else event_ns - outcome.returned_ns,
            "lines": [line.get("event") for line in outcome.lines]}


class MirrorReads:
    """The reads the capture thread records, shared with the cycle driver under one condition."""

    def __init__(self):
        self.condition = threading.Condition()
        self.records = []
        self.descriptor = None
        self.first_present_observed_ns = None

    def add(self, record):
        with self.condition:
            self.records.append(record)
            if record["present"] and self.first_present_observed_ns is None:
                self.first_present_observed_ns = record["observed_ns"]
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return list(self.records)

    def wait_for(self, predicate, deadline_ns, stop):
        """Block until ``predicate(records)`` holds, ``deadline_ns`` (monotonic) passes or ``stop`` is set."""
        with self.condition:
            while not stop.is_set():
                if predicate(self.records):
                    return True
                remaining = deadline_ns - time.monotonic_ns()
                if remaining <= 0:
                    return False
                self.condition.wait(min(remaining / 1e9, .05))
            return False


def measure(candidate, seconds, cycles, stop, *, backend_factory, marker_client, allow_mirror_observation=False,
            allow_calibration_output=False, validation_only=False):
    """One bounded measurement run; the backend and the marker client are injected for offline tests.

    With ``validation_only`` nothing is read (``candidate`` and
    ``backend_factory`` must be None) and the marker client must be the
    validation bridge; otherwise the mirror is read through
    ``backend_factory`` (a synthetic fixture) or, with the explicit opt-in,
    the verified device. The marker bridge is checked for its build first,
    started, and its display timer bounds the whole run; cycles start only
    while enough of it is left for both requests. Every failure is a named
    ``rejected`` reason, never an exception, after the usual cleanup.
    """
    if type(seconds) is not int or not MARKER_MIN_SECONDS <= seconds <= MARKER_MAX_SECONDS:
        raise ValueError("fallback_measure_duration_out_of_bounds")
    if type(cycles) is not int or not 1 <= cycles <= MAX_CYCLES:
        raise ValueError("fallback_measure_cycles_out_of_bounds")
    if validation_only:
        if candidate is not None or backend_factory is not None or marker_client.validation_only is not True:
            raise ValueError("fallback_validate_reads_no_device")
    else:
        if backend_factory is None and allow_mirror_observation is not True:
            raise ValueError("fallback_measure_mirror_opt_in_required")
        if backend_factory is not None and getattr(backend_factory, "kind", None) != "synthetic":
            raise ValueError("fallback_measure_fixture_backend_required")
        if marker_client.validation_only is not True and allow_calibration_output is not True:
            raise ValueError("fallback_measure_output_opt_in_required")
    report = new_report(marker_client.validation_only, cycles=cycles)
    reads = MirrorReads()
    phases = []
    capture_result = {}
    capture_thread = None
    marker_started = False
    try:
        marker_client.check_built()
        marker_client.start_marker(seconds, stop, allow_calibration_output=allow_calibration_output)
        marker_started = True
        deadline_ns = int(marker_client.marker_deadline * 1_000_000_000)  # the bridge's display timer, this clock
        report["marker_ready_ns"] = time.monotonic_ns()
        if not validation_only:
            def on_frame(data, fmt, sequence, observed_ns):
                if stop.is_set() or time.monotonic_ns() >= deadline_ns:
                    return
                image, descriptor = native_bgr(data, fmt)
                if reads.descriptor is None:
                    reads.descriptor = descriptor
                elif descriptor != reads.descriptor:
                    raise ValueError("fallback_measure_format_changed")
                reads.add(frame_record(sequence, observed_ns, image))

            def run_capture():
                capture_result["report"], _ = capture(candidate, seconds, stop, backend_factory=backend_factory,
                                                      on_frame=on_frame, max_fps=READ_RATE_HZ,
                                                      deadline=deadline_ns / 1e9)

            report["capture_started"] = True
            capture_thread = threading.Thread(target=run_capture, name="fallback-measure-capture", daemon=True)
            capture_thread.start()
            warmup_ns = min(deadline_ns, report["marker_ready_ns"] + int(MARKER_WARMUP_SECONDS * 1e9))
            if not reads.wait_for(lambda records: reads.first_present_observed_ns is not None, warmup_ns, stop):
                if stop.is_set():
                    raise InterruptedError("cancelled")
                if capture_thread is not None and not capture_thread.is_alive():
                    captured = capture_result.get("report") or {}
                    if captured.get("status") == "error":
                        raise ValueError(captured.get("error", captured.get("reason", "capture_failed")))
                raise ValueError("marker_not_visible_in_mirror")
            report["marker_first_present_observed_ns"] = reads.first_present_observed_ns
        for cycle in range(1, cycles + 1):
            if stop.is_set():
                raise InterruptedError("cancelled")
            if deadline_ns - time.monotonic_ns() < CYCLE_RESERVE_NS:
                break
            hide = run_phase(marker_client, reads, cycle, False, deadline_ns, stop, validation_only)
            show = run_phase(marker_client, reads, cycle, True, deadline_ns, stop, validation_only)
            phases.append({"cycle": cycle, "hide": hide, "show": show})
            report["cycles_completed"] = len(phases)
        report["cycles_not_started"] = cycles - len(phases)
        if stop.is_set():
            raise InterruptedError("cancelled")
        # The marker's own timer ends the bridge and the capture; wait for both, reading until the end so the
        # loopback census covers the longest static series the run allows.
        if capture_thread is not None:
            capture_thread.join(timeout=max(0.0, (deadline_ns - time.monotonic_ns()) / 1e9) + 3)
        while marker_client.process.poll() is None and not stop.is_set() and time.monotonic_ns() < deadline_ns + 2_000_000_000:
            stop.wait(.02)
        if stop.is_set():
            raise InterruptedError("cancelled")
        captured = capture_result.get("report")
        if captured is not None and captured.get("status") == "error":
            raise ValueError(captured.get("error", captured.get("reason", "capture_failed")))
        report.update(status="measured" if report["cycles_not_started"] == 0 else "measurement_incomplete",
                      exit_code=0 if report["cycles_not_started"] == 0 else 1)
        if report["status"] == "measurement_incomplete":
            report["reason"] = "display_timer_left_no_room_for_every_cycle"
    except InterruptedError:
        report.update(status="cancelled", exit_code=130, reason="signal")
    except Exception as error:
        report.update(status="cancelled" if stop.is_set() else "rejected", exit_code=130 if stop.is_set() else 1,
                      reason=str(error)[:1024])
    finally:
        if capture_thread is not None:
            capture_thread.join(timeout=5)
            if capture_thread.is_alive():
                report.update(cleanup_ok=False, cleanup_error="capture_thread_did_not_stop")
        records = reads.snapshot()
        report.update(sections(phases, records, validation_only))
        report["cycles"] = phases
        if capture_result.get("report") is not None:
            report["capture"] = capture_result["report"]
        try:
            bridge = marker_client.close()
            report["bridge"] = bridge
            marker_clean = (not marker_started and marker_client.process is None) or bridge_closed_cleanly(bridge)
            capture_clean = (not report["capture_started"] or
                             (report.get("capture") or {}).get("cleanup_ok") is True)
            report["cleanup_ok"] = report["cleanup_ok"] and marker_clean and capture_clean
            if report["exit_code"] == 0 and marker_clean:
                try:
                    native = bridge.get("native_report") or {}
                    validate_visibility_evidence(native, marker_client.validation_only)
                    if native.get("visibility_requests_received") != 2 * report["cycles_completed"]:
                        raise ValueError("bridge_request_count_mismatch")
                except (ValueError, TypeError, KeyError) as error:
                    report.update(status="rejected", exit_code=1, reason=str(error)[:1024])
        except Exception as error:
            report.update(cleanup_ok=False, cleanup_error=str(error)[:1024])
        if not report["cleanup_ok"] and report["exit_code"] == 0:
            report.update(status="cleanup_failed", exit_code=1)
        report["seconds_requested"] = seconds
    return report


def run_phase(client, reads, cycle, visible, deadline_ns, stop, validation_only):
    """One request of a cycle: send it, await its terminal line, then wait for the mirror and the pacing floor.

    A hide's phase ends early enough for the cycle's show to be sent
    ``LAST_REQUEST_MARGIN_NS`` before the display deadline; a show's phase,
    which makes no call, may run to the deadline. No request is sent inside the
    margin: that is a rejected run, not a late call.
    """
    if time.monotonic_ns() + LAST_REQUEST_MARGIN_NS > deadline_ns:
        raise ValueError("request_inside_display_deadline_margin")
    request = client.request_visibility(cycle, visible)
    outcome = client.await_visibility(request)
    record = {"visible": visible, "requested_monotonic_ns": request.requested_ns, **outcome_record(outcome)}
    request_ns = outcome.request_ns if outcome.request_ns is not None else request.requested_ns
    latest_end = deadline_ns if visible else deadline_ns - LAST_REQUEST_MARGIN_NS
    phase_end = min(request.requested_ns + PHASE_BOUND_NS, latest_end)
    if validation_only:
        record["mirror"] = {"measured": False, "reason": "validation_reads_no_mirror"}
        ended_by = "no_mirror"
    else:
        seen = reads.wait_for(lambda records: transition_bounds(records, request_ns, visible)[
            "first_present_observed_ns" if visible else "first_absent_observed_ns"] is not None, phase_end, stop)
        record["mirror"] = {"measured": True, **transition_bounds(reads.snapshot(), request_ns, visible)}
        ended_by = "transition_observed" if seen else "stopped" if stop.is_set() else \
            "display_deadline" if phase_end == latest_end else "phase_bound"
    record["ended_by"] = ended_by
    # The pacing floor between requests, however fast the mirror answered; never past a hide's latest end.
    pacing_end = min(request.requested_ns + REQUEST_GAP_NS, latest_end)
    while (remaining := pacing_end - time.monotonic_ns()) > 0 and not stop.is_set():
        stop.wait(min(remaining / 1e9, .05))
    return record


def sections(phases, records, validation_only):
    """The four measurement sections from the cycles' phase records and the mirror reads."""
    hides = [{"cycle": phase["cycle"], **phase["hide"]} for phase in phases]
    shows = [{"cycle": phase["cycle"], **phase["show"]} for phase in phases]
    mirror = not validation_only

    def delivery(requests, line):
        delivered = [item["event_after_returned_ns"] for item in requests if item["confirmed"]]
        return {"measured": bool(requests), "event_line": line, "requests": len(requests),
                "events_delivered": len(delivered),
                "events_missing": sum(item["terminal"] == VISIBILITY_MISSING_LINE for item in requests),
                "returned_not_ok": sum(item["terminal"] is not None and item["terminal"].endswith("_returned")
                                       for item in requests),
                "other_failures": sum(item["terminal"] is None for item in requests),
                "maximum_event_after_returned_ns": max(delivered) if delivered else None,
                "per_cycle": [{key: item[key] for key in ("cycle", "terminal", "reason", "request_ns", "returned_ns",
                                                           "event_observed_ns", "event_after_returned_ns",
                                                           "waited_ns")} for item in requests]}

    def latency(requests, target, opposite):
        upper = [item["mirror"].get("upper_bound_after_request_ns") for item in requests if mirror]
        upper = [value for value in upper if value is not None]
        per_cycle = []
        for item in requests:
            entry = {"cycle": item["cycle"], "request_ns": item["request_ns"], "returned_ns": item["returned_ns"],
                     "event_observed_ns": item["event_observed_ns"], "ended_by": item["ended_by"]}
            if mirror:
                entry.update(item["mirror"])
                first = item["mirror"].get(f"{target}_observed_ns")
                entry["upper_bound_after_event_ns"] = (None if first is None or item["event_observed_ns"] is None
                                                       else first - item["event_observed_ns"])
            per_cycle.append(entry)
        return {"measured": mirror and bool(requests), "transition": f"{opposite} to {target}",
                "cycles": len(requests), "transitions_observed": len(upper),
                "transitions_not_observed_within_bound": sum(item["ended_by"] in ("phase_bound", "display_deadline")
                                                             for item in requests),
                "maximum_upper_bound_after_request_ns": max(upper) if upper else None,
                "minimum_upper_bound_after_request_ns": min(upper) if upper else None, "per_cycle": per_cycle}

    repeats = {"measured": mirror and bool(records), **repeat_counts(records)}
    if records:
        span = records[-1]["observed_ns"] - records[0]["observed_ns"]
        repeats["observed_read_rate_hz"] = round(len(records) / (span / 1e9), 3) if span > 0 else None
        repeats["reads_detail"] = [{key: read[key] for key in ("sequence", "observed_ns", "digest", "present", "decode")}
                                   for read in records]
    return {"hide_to_absence": latency(hides, "first_absent", "last_present"),
            "hidden_event_delivery": delivery(hides, "overlay_hidden"),
            "loopback_repeats": repeats,
            "show_to_visible": {**latency(shows, "first_present", "last_absent"),
                                "shown_event_delivery": delivery(shows, "overlay_shown")}}


def validate_worker_report(report, returncode):
    """The report contract the supervisor accepts: fail closed on any shape the launcher would misread."""
    if (not isinstance(report, dict) or report.get("report_kind") != REPORT_KIND or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (0, 1, 130) or
            report["exit_code"] != returncode or type(report.get("cleanup_ok")) is not bool or
            type(report.get("validation_only")) is not bool or
            type(report.get("seconds_requested")) is not int or
            not MARKER_MIN_SECONDS <= report["seconds_requested"] <= MARKER_MAX_SECONDS or
            type(report.get("cycles_requested")) is not int or not 1 <= report["cycles_requested"] <= MAX_CYCLES or
            type(report.get("cycles_completed")) is not int or
            not 0 <= report["cycles_completed"] <= report["cycles_requested"] or
            any(report.get(key) is not False for key in ("images_saved", "inference_started", *claims(),
                                                         "physical_display_recovery_verified",
                                                         "camera_freshness_verified")) or
            any(not isinstance(report.get(name), dict) or type(report[name].get("measured")) is not bool
                for name in MEASUREMENTS) or
            not isinstance(report.get("cycles"), list) or len(report["cycles"]) != report["cycles_completed"]):
        raise ValueError("invalid_fallback_measurement_report")
    if report["exit_code"] == 0:
        bridge = report.get("bridge") or {}
        native = bridge.get("native_report") or {}
        mode = "--validate-calibration-marker" if report["validation_only"] else "--calibration-marker"
        if (report.get("status") != "measured" or not report["cleanup_ok"] or
                report["cycles_completed"] != report["cycles_requested"] or not bridge_closed_cleanly(bridge) or
                native.get("mode") != mode or native.get("runtime_initialized") != (not report["validation_only"]) or
                native.get("visibility_requests_received") != 2 * report["cycles_completed"] or
                (report["validation_only"] and any(report[name]["measured"] for name in
                                                   ("hide_to_absence", "loopback_repeats", "show_to_visible"))) or
                (not report["validation_only"] and report.get("capture_started") is not True)):
            raise ValueError("inconsistent_fallback_measurement_report")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--measure-fallback", action="store_true", help="the device run (its own approval)")
    parser.add_argument("--allow-mirror-observation", action="store_true")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--device", help="the verified SteamVR mirror node, for example /dev/video99")
    parser.add_argument("--validate", action="store_true",
                        help="run the cycles against the bridge's validation marker mode: no VR, no device")
    parser.add_argument("--manifest", type=Path,
                        help="checkpoint manifest to verify first (required for the device run)")
    parser.add_argument("--seconds", type=int, choices=range(MARKER_MIN_SECONDS, MARKER_MAX_SECONDS + 1),
                        default=MARKER_MAX_SECONDS, help="the marker's display time, the run's bound (default 20)")
    parser.add_argument("--cycles", type=int, choices=range(1, MAX_CYCLES + 1), default=DEFAULT_CYCLES,
                        help=f"hide and show cycles (default {DEFAULT_CYCLES})")
    parser.add_argument("--bridge", type=Path, help="Exact reviewed native bridge; defaults to the existing bridge path")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        options = json.loads(args.worker)
        if options.get("approved_scope") != APPROVED_SCOPE:
            raise ValueError("fallback_measure_explicit_scope_required")
        stop = stop_on_signals()
        validation_only = options["validation_only"] is True
        client = (MarkerClient(options["bridge"], validation_only=validation_only) if options.get("bridge")
                  else MarkerClient(validation_only=validation_only))
        report = measure(options["candidate"], options["seconds"], options["cycles"], stop, backend_factory=None,
                         marker_client=client, allow_mirror_observation=not validation_only,
                         allow_calibration_output=not validation_only, validation_only=validation_only)
        print(json.dumps(report, indent=2))
        return report["exit_code"]
    device = (args.measure_fallback, args.allow_mirror_observation, args.allow_calibration_output, bool(args.device))
    if args.validate:
        if any(device):
            parser.error("--validate takes no device flag: it reads no device and initialises no VR")
    elif not all(device):
        if any(device):
            parser.error("the device run requires --measure-fallback, both explicit opt-in flags and a verified --device")
        parser.error("choose --validate, or the device run with its four flags")
    elif args.manifest is None:
        parser.error("the device run requires --manifest, the checkpoint manifest to verify first")
    report = new_report(args.validate, cycles=args.cycles)
    if args.manifest is not None:
        verification = verify_manifest(args.manifest)
        report["manifest"] = verification
        if verification.get("exit_code") != 0:
            report.update(status="manifest_not_verified", exit_code=1,
                          reason="checkpoint_manifest_differs_or_tree_dirty")
            print(json.dumps(report, indent=2))
            return 1
    candidate = None if args.validate else select_candidate(discover(), args.device)
    options = {"approved_scope": APPROVED_SCOPE, "candidate": candidate, "seconds": args.seconds,
               "cycles": args.cycles, "validation_only": args.validate,
               "bridge": str(args.bridge.resolve()) if args.bridge else None}
    base = new_report(args.validate, cycles=args.cycles)
    if "manifest" in report:
        base["manifest"] = report["manifest"]
    result = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)], base,
                       args.seconds + SUPERVISOR_MARGIN_SECONDS, validate_worker_report, descendants=True)
    print(json.dumps(result, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
