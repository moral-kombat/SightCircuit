"""Bounded empirical marker-copy observation, never a production calibration.

Native-resolution capture is retained. Only candidate quads are rectified to
check the fixed marker. No eye identity or unseen/occluded copy is inferred.
The copy-location algorithm and the worker report contract live in
``framejacker.hud.observation``; this command runs the bounded trial, its
marker and its report.

``--record-session-tokens`` (off by default, only with the complete
calibration opt-in) also records the HUD session tokens of the run from the
read-only providers of ``framejacker.sources.session``: the SteamVR server
process and the SHA-256 of its running binary, this process's clock, and a
tracking observer that nothing feeds yet. Without it nothing is read under
/proc for session tokens, and the report records that no tokens were
requested.
"""
import argparse
import json
from pathlib import Path
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.gates import claims  # noqa: E402
from framejacker.hud.contract import MARKER_MAX_SECONDS, MARKER_MIN_SECONDS  # noqa: E402

from framejacker.hud.observation import (  # noqa: E402
    SESSION_TOKENS_DECLARATION_FAILED, SESSION_TOKENS_NOT_REPORTED, SESSION_TOKENS_NOT_REQUESTED,
    SESSION_TOKENS_REPORT_INVALID, BaselineGate, EmpiricalObservation, ObserverConfig, native_bgr,
    session_tokens_record, unrecorded_session_tokens, validate_marker_evidence,
    validate_observer_report as validate_worker_report)
from framejacker.hud.marker import STOP_GRACE_SECONDS, MarkerClient  # noqa: E402
from framejacker.overlay import bridge_closed_cleanly  # noqa: E402
from framejacker.runtime.guard import stop_on_signals, supervise  # noqa: E402
from framejacker.sources.mirror import capture  # noqa: E402
from framejacker.sources.mirror_device import discover, select_candidate  # noqa: E402
from framejacker.sources.session import HostClock, MirrorSession, RuntimeProcess, TrackingObserver  # noqa: E402

BASELINE_WINDOW_SECONDS = 2.0
# The supervisor's bound is --seconds plus this margin, counted from the worker's start.
SUPERVISOR_MARGIN_SECONDS = 12
# Longest session declaration accepted, checked before capture or the marker opens. The margin also
# holds worker start-up, the baseline window, the bridge's process start until its display timer
# runs, the bridge's exit after that timer (the client waits at most STOP_GRACE_SECONDS more) and
# cleanup. The bridge starts its timer before it initializes the runtime and prints its ready line,
# so a slow initialization does not lengthen the run. Half of what the baseline window and the grace
# leave goes to the declaration (4 s), the rest to the other steps. A slower declaration rejects the
# run here; otherwise the supervisor's timeout could stop a marker already shown and force its
# termination.
SESSION_DECLARATION_BUDGET_SECONDS = (SUPERVISOR_MARGIN_SECONDS - BASELINE_WINDOW_SECONDS - STOP_GRACE_SECONDS) / 2


def new_report(validation_only=False, *, record_session_tokens=False):
    """The report before a run; a supervisor base report says requested tokens were not reported."""
    return {"report_kind": "hud_marker_observation", "status": "not_run", "exit_code": 3,
            "calibration_validation_only": validation_only,
            "cleanup_ok": True, "images_saved": False, "inference_started": False,
            **claims(), "physical_display_recovery_verified": False,
            "camera_freshness_verified": False,
            "session_tokens": unrecorded_session_tokens(SESSION_TOKENS_NOT_REPORTED if record_session_tokens
                                                        else SESSION_TOKENS_NOT_REQUESTED),
            "scope": "Empirical visible-copy bounds only; unknown composition cannot qualify production masking"}


def observe(candidate, seconds, stop, *, backend_factory, marker_client, config=ObserverConfig(),
            allow_mirror_observation=False, allow_calibration_output=False, session=None):
    """One combined bounded trial; backend, marker and session are injected for offline tests.

    A ``session`` (``sources.session.MirrorSession``) is declared before the
    observation's clock starts and before capture opens, and read first for
    every complete frame read. Its report becomes ``session_tokens``. A session
    that raises while it is declared or read rejects the run with a named reason
    after the usual cleanup. A report that raises or fails the contract is
    recorded as ``session_tokens_report_invalid``; it rejects a run that would
    otherwise be ``observed_unqualified`` (``observer_session_tokens_report_invalid``),
    while a cancelled or already rejected run keeps its status and reason.
    ``SESSION_DECLARATION_BUDGET_SECONDS`` is checked when the declaration
    returns: a longer one rejects the run before capture or the marker opens. A
    declaration that does not return within the supervisor's bound ends the run
    ``timed_out`` with ``cleanup_ok`` false, also before anything opens, and its
    termination is forced if it still has not returned after the stop request.
    Without a session nothing is read for session tokens.
    """
    if type(seconds) is not int or not MARKER_MIN_SECONDS <= seconds <= MARKER_MAX_SECONDS:
        raise ValueError("observer_duration_out_of_bounds")
    config.validate()
    if backend_factory is None and allow_mirror_observation is not True:
        raise ValueError("observer_mirror_opt_in_required")
    if backend_factory is not None and getattr(backend_factory, "kind", None) != "synthetic":
        raise ValueError("observer_fixture_backend_required")
    if marker_client.validation_only is not True and allow_calibration_output is not True:
        raise ValueError("observer_output_opt_in_required")
    report = new_report(marker_client.validation_only)
    observation = EmpiricalObservation(config)
    session_failure, declaration_failed, declared_at = None, False, time.monotonic()
    if session is not None:
        # Outside the timed observation: a declaration scans /proc and may hash up to 256 MiB.
        try:
            session.declare()
        except Exception:
            session_failure, declaration_failed = "observer_session_tokens_declaration_failed", True
    declaration_elapsed_ms = round((time.monotonic() - declared_at) * 1000, 3)
    if (session is not None and session_failure is None and
            declaration_elapsed_ms > SESSION_DECLARATION_BUDGET_SECONDS * 1000):
        # Rejected before capture or the marker opens, not by the supervisor while the marker is shown.
        session_failure = "observer_session_tokens_declaration_too_slow"
    started = time.monotonic()
    deadline = started + seconds
    baseline = BaselineGate(min(deadline, started + BASELINE_WINDOW_SECONDS))
    marker_started = capture_started = False
    baseline_descriptor = None
    first_marker_deadline = None
    try:
        if session_failure is not None:
            raise ValueError(session_failure)
        def on_frame(data, fmt, sequence, observed_ns):
            nonlocal marker_started, baseline_descriptor, first_marker_deadline
            if session is not None:
                try:
                    session.read()  # This read's tokens, before anything else processes it.
                except Exception as error:
                    raise ValueError("observer_session_tokens_read_failed") from error
            if time.monotonic() >= deadline or stop.is_set():
                return
            image, descriptor = native_bgr(data, fmt)
            if baseline_descriptor is None:
                baseline_descriptor = descriptor
            if descriptor != baseline_descriptor:
                raise ValueError("observer_format_changed")
            if not marker_started:
                ready = baseline.accept(image, sequence, observed_ns, time.monotonic())
                if stop.is_set() or time.monotonic() >= baseline.deadline:
                    raise ValueError("baseline_processing_exceeded_observation_deadline")
                if not ready:
                    return
                marker_client.start_marker(seconds, stop, allow_calibration_output=allow_calibration_output)
                marker_started = True
                first_marker_deadline = min(deadline, time.monotonic() + 2)
                report["baseline_sequence"] = sequence
                return
            # No inference or downscaled analysis image: these are native corners.
            try:
                observation.accept(image, descriptor, sequence, observed_ns, config_id=config.config_id)
            except ValueError as error:
                if str(error) == "no_complete_marker" and not observation.samples and time.monotonic() < first_marker_deadline:
                    report["warmup_frames_without_marker"] = report.get("warmup_frames_without_marker", 0) + 1
                    return
                raise
            if time.monotonic() >= deadline:
                raise ValueError("marker_processing_exceeded_observation_deadline")
        def on_progress(_report):
            if not marker_started:
                baseline.check_deadline(time.monotonic())
        capture_started = True
        captured, _ = capture(candidate, seconds, stop, progress=on_progress,
                                           backend_factory=backend_factory, on_frame=on_frame, max_fps=5, deadline=deadline)
        report["capture"] = captured
        if captured["status"] == "error":
            raise ValueError(captured.get("error", captured.get("reason")))
        if stop.is_set():
            report.update(status="cancelled", exit_code=130)
        elif not marker_started:
            raise ValueError("baseline_distinct_clear_frames_not_established")
        elif len(observation.samples) < 3:
            raise ValueError("insufficient_complete_marker_samples")
        else:
            report.update(status="observed_unqualified", exit_code=2,
                          reason="composition_and_unseen_content_not_established")
        # Allow the marker's own timer to close normally; never read more frames.
        while (marker_started and marker_client.process.poll() is None and not stop.is_set()
               and time.monotonic() < deadline + 2):
            stop.wait(.02)
    except Exception as error:
        report.update(status="cancelled" if stop.is_set() else "rejected", exit_code=130 if stop.is_set() else 1,
                      reason=str(error)[:1024])
    finally:
        report.update(observation.evidence())
        report.update(baseline=dict(baseline.report), baseline_initial_match_diagnostics=baseline.initial_diagnostics,
                      baseline_match_diagnostics=baseline.last_diagnostics,
                      baseline_window_seconds=min(seconds, BASELINE_WINDOW_SECONDS))
        if session is not None:
            if declaration_failed:
                report["session_tokens"] = unrecorded_session_tokens(SESSION_TOKENS_DECLARATION_FAILED)
            else:  # A declaration that was too slow did complete, so its report and duration are kept.
                report["session_tokens"] = session_tokens_record(session, declaration_elapsed_ms)
            if report["session_tokens"].get("reason") == SESSION_TOKENS_REPORT_INVALID and report["exit_code"] == 2:
                report.update(status="rejected", exit_code=1, reason="observer_session_tokens_report_invalid")
        try:
            bridge = marker_client.close()
            report["bridge"] = bridge
            marker_clean = ((marker_client.process is None and not marker_started) or
                            bridge_closed_cleanly(bridge))
            # A failed or too slow declaration opens no capture, so there is none to close.
            capture_clean = not capture_started or report.get("capture", {}).get("cleanup_ok") is True
            report["cleanup_ok"] = capture_clean and marker_clean
            if report["exit_code"] == 2 and marker_clean:
                try:
                    validate_marker_evidence(bridge, seconds, marker_client.validation_only)
                except (ValueError, TypeError, KeyError) as error:
                    report.update(status="rejected", exit_code=1, reason=str(error)[:1024])
        except Exception as error:
            report.update(cleanup_ok=False, cleanup_error=str(error)[:1024])
        if not report["cleanup_ok"] and report["exit_code"] == 2:
            report.update(status="cleanup_failed", exit_code=1)
        report["seconds_requested"] = seconds
        report["maximum_processing_fps"] = 5
    if len(json.dumps(report)) > 128 * 1024:
        report.update(samples=[], status="report_budget_exceeded", exit_code=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-calibration", action="store_true")
    parser.add_argument("--allow-mirror-observation", action="store_true")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--bridge", type=Path, help="Exact reviewed native bridge; defaults to the existing bridge path")
    parser.add_argument("--seconds", type=int, choices=range(MARKER_MIN_SECONDS, MARKER_MAX_SECONDS + 1), default=20)
    parser.add_argument("--record-session-tokens", action="store_true",
                        help="Also record the HUD session tokens, read-only from /proc (the SteamVR server process "
                             "and the SHA-256 of its running binary, this process's clock), for "
                             "hud_validation_report.py --from-observer; requires the complete calibration opt-in; "
                             "off by default. Tracking tokens are not observed yet, so --from-observer rejects the "
                             "report until a tracking reporter runs before the declaration")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        options = json.loads(args.worker)
        if options.get("approved_scope") != "marker_observation_only":
            raise ValueError("observer_explicit_scope_required")
        if type(options.get("record_session_tokens")) is not bool:
            raise ValueError("observer_session_tokens_option_invalid")
        stop = stop_on_signals()
        # Providers are built only on request; nothing here feeds the tracking observer yet.
        session = (MirrorSession(HostClock(), RuntimeProcess(), TrackingObserver())
                   if options["record_session_tokens"] else None)
        client = MarkerClient(options["bridge"], validation_only=False) if options.get("bridge") else MarkerClient(validation_only=False)
        report = observe(options["candidate"], options["seconds"], stop, backend_factory=None,
                         marker_client=client, allow_mirror_observation=True,
                         allow_calibration_output=True, session=session)
    else:
        opted = (args.observe_calibration, args.allow_mirror_observation, args.allow_calibration_output, bool(args.device))
        if any(opted) and not all(opted):
            parser.error("calibration observation requires all three explicit flags and a verified --device")
        if args.bridge and not all(opted):
            parser.error("--bridge requires the complete calibration opt-in")
        if args.record_session_tokens and not all(opted):
            parser.error("--record-session-tokens requires the complete calibration opt-in")
        report = new_report()
        if all(opted):
            candidate = select_candidate(discover(), args.device)
            options = {"approved_scope": "marker_observation_only", "candidate": candidate, "seconds": args.seconds,
                       "bridge": str(args.bridge.resolve()) if args.bridge else None,
                       "record_session_tokens": args.record_session_tokens}
            # The declaration runs inside this bound, before the observation's own clock starts.
            report = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)],
                new_report(record_session_tokens=args.record_session_tokens), args.seconds + SUPERVISOR_MARGIN_SECONDS,
                validate_worker_report, descendants=True)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
