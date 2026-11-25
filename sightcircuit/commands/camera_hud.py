"""The camera HUD: object names from the left SDK camera on the fixed HUD tile, bounded and gated.

The fifteenth command (work-order item L9). Three modes, exactly one per run:

``--validate`` runs the whole path with nothing real in it: the camera
helper's fake build as the producer (refused unless ``--describe`` says
``backend: fake``, before it is asked to export anything), a scripted
detector, the stage, and the bridge in validation, which displays nothing;
then the synthetic rows on the validation bridge with one stop. It needs no
record, no terminal and no camera, and is what CI runs.

``--synthetic-rows`` is the driver of device step L-D3: a counter row five
times a second into the bridge's ``--fixed-hud`` mode for ``--seconds``, with
``--stops`` scheduled stops in which no frame and no hide request is sent
(the client's keepalive writes one show request per stop, which the bridge
answers ``no_image``), so the bridge's own expiry is observed. The camera is never opened and no detector is loaded.

``--camera`` is the driver of steps L-D4 and L-D5: the left camera through
``CameraHudStage`` (detector, rows or boxes, tile, one observed frame per
result) for at most 90 seconds. ``--layout`` names what it draws: the strip
(the default) or the box panel, on which a box and a name are drawn for
each detection through ``--alignment``, the wearer's five numbers (the
project's own, never the SDK's calibration; neutral is ``1,1,0,0,0``).
``--region`` and ``--rotation`` give the detector a rectangle of the frame,
turned by quarter turns, for either layout. The layout, the region, the
rotation and the alignment are each compared with the run's scope record
before anything is launched; a difference is a refusal and leaves the record
unspent.

Gates. Every gate stays closed and this command opens none; every report
carries ``gates.claims()``, all false. The two device modes are gated use:
real HUD output, and for ``--camera`` also camera input to the display. Each
is refused before the helper or the bridge is launched unless a one-run scope
record verifies (``runtime/scope.py``): ``--scope-record`` names the record
the Frame manager staged for one approved step and ``--manifest`` the
checkpoint manifest it is bound to. ``--camera`` also names its step
(``--step``), and the record must be for that step. The run's seconds are
bound to what runs: the client ends real output itself when they are over,
and the stage and the helper are bounded within them. Naming them permits nothing, their
content does; the opt-in flags (``--allow-hud-output``,
``--allow-camera-input``) are required and permit nothing either. The record
is a procedural control against mistakes, not a cryptographic one. The device
modes launch only the bridge and the helper at the paths the manifest hashes;
there is no option to name another.

The informed start (``camera/confirmation.py``). A device mode prints its
notices on the terminal, the helper's export notice and the display notice
for ``--camera``, and requires the operator's exact typed ``yes`` on an
interactive stdin. The typed answer starts this one run. It is not an
approval, creates no scope record and opens nothing: without a verifying
record the run is refused whatever was typed.

Supervision and stops. The helper runs under the supervision primitive
(``CameraHelperSource.drive``, on the main thread). In both device modes,
from the typed ``yes`` until the report is printed, SIGINT, SIGTERM and
SIGHUP are recorded and never raised (``guard.cancellation``, the
primitive's record-and-poll policy): the run stops at the next poll, its
teardown finishes, and the report is the run's own, ``cancelled``, however
many signals arrive; the previous handlers are restored afterwards. A
terminal's Ctrl-C also reaches the bridge, which retires by itself; that is
a cancelled run with a clean bridge, not a client failure. G2's green-input
abort is the operator's: at any green input or passthrough change the
operator presses Ctrl-C and the tile is taken down. Nothing here routes
through ``ReplayWorker`` or the live runner.

Reports hold counts, labels, timings and format metadata; never a pixel, a
still or a calibration value. The ``--camera`` report is the stage's report
plus this command's blocks (``command``, ``scope_record``, ``marks``,
``target``, ``timebase``); labels and their display intervals are Camera
Data under the SDK agreement, saved only where the operator names
``--report``. ``--target-label`` adds the verdict of acceptance row G10 for
that class: named for at least 90% of a contiguous 60-second window with no
gap over 2 seconds, from the bridge's own lines. Cues and key marks are
``camera_recognize``'s (``camera/marks.py``): a turn away is marked
``out_of_view``, the return ``in_view``, the removal ``remove``.

Nothing here has run on the Frame. A device run is a step with its own
approval, staged by the Frame manager; see ``docs/guides/camera-hud.md``.
"""
import argparse
import json
import math
import contextlib
import os
from pathlib import Path
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
from sightcircuit.camera import confirmation, marks
from sightcircuit.camera import region as regions
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, POC_THRESHOLD, class_names, create_detector
from sightcircuit.errors import SightCircuitError
from sightcircuit.frame import Detection, DetectionResult
from sightcircuit.hud import camera_stage, synthetic_rows
from sightcircuit.hud.boxes import ALIGNMENT_FIELDS, Alignment
from sightcircuit.hud.camera import ORIENTATIONS
from sightcircuit.hud.camera_stage import CAMERA_INPUT_REFUSAL, CameraHudStage
from sightcircuit.hud.contract import BRIDGE_READY_WAIT_SECONDS, FIXED_LAYOUTS, HUD_CONFIG, fixed_layout
from sightcircuit.observed_frames import FIXED_HUD_REFUSAL, LAYOUT_REFUSAL, ObservedFrameClient
from sightcircuit.overlay import DEFAULT_BRIDGE
from sightcircuit.runtime import guard, manifest, scope
from sightcircuit.sources import camera_helper
from sightcircuit.sources.camera_helper import CameraHelperSource

RUNNER = scope.RUNNER  # The name a scope record must carry for this command.
VALIDATION_KIND = "camera_hud_validation"
MAX_SECONDS = 90  # The camera mode's bound (the export contract's); a record's step bounds it further.
MAX_FPS, DEFAULT_FPS = 10, 5
CAMERA_STEPS = tuple(step for step, (source, _) in scope.STEPS.items() if source == scope.CAMERA_LEFT)
SYNTHETIC_STEP = next(step for step, (source, _) in scope.STEPS.items() if source == scope.SYNTHETIC)
# The synthetic rows' scope and client bound: the schedule's seconds plus this, for the bridge's start (its
# connect budget) and the last update's answer. The client ends real output itself when the sum is over, so
# ``--seconds`` plus this must fit the record's bound.
SYNTHETIC_ALLOWANCE_SECONDS = 10
MAX_SYNTHETIC_SECONDS = synthetic_rows.MAX_SECONDS - SYNTHETIC_ALLOWANCE_SECONDS  # 110 under an L-D3 record.
# How long the helper's supervision waits for the pipeline to attach. The pipeline opens its sink first, which
# starts the bridge and may take the bridge's whole ready wait, so the attach time covers that with a margin.
ATTACH_SECONDS = BRIDGE_READY_WAIT_SECONDS + 5.0
REPORT_INVALID = "camera_hud_report_field_invalid"
ERROR_BLOCKS = {"report_kind": None, "schema_version": None, "status": None, "reason": None,
                **dict.fromkeys(gates.claims()), "report_replaced": None,
                "command": None, "scope_record": None, "cleanup": ("source_ok", "bridge_ok", "ok")}
# Acceptance row G10: the label named for at least 90% of a contiguous 60 s window with no gap over 2 s.
G10_WINDOW = {"window_seconds": 60.0, "fraction": 0.9, "max_gap_seconds": 2.0}
FAKE_HELPER = "artifacts/camera-helper-fake/sightcircuit-camera-helper-fake"  # scripts/build_camera_helper.sh --fake
DESCRIBE_TIMEOUT_SECONDS = 10
MAX_REPORTED_MARKS = 32  # Marks repeated in the report; the count covers them all.
# The stage's report is at most ``camera_stage.MAX_REPORT_BYTES``; this command's blocks may add half as much.
MAX_REPORT_BYTES = camera_stage.MAX_REPORT_BYTES + 20_480
VALIDATION_LIMITS = {"fps": 5, "max_seconds": 3, "max_frames": 6}
VALIDATION_ROWS = {"seconds": 6, "stops": 1, "stop_seconds": 2.0}
# What the fake backend is asked for: its NV12 scenario through the CPU pixel path, which needs no GPU.
VALIDATION_HELPER_ARGS = ("--fake-scenario", "export_nv12", "--pixel-path", "cpu_linear")
CAMERA_DATA_STATEMENT = "labels_and_display_intervals_are_camera_data"
COMMAND_KEYS = ("name", "mode", "exit_code", "confirmation", "refusal", "seconds_requested", "fps_requested",
                "max_frames_requested", "stops_requested", "detector", "report_saved_by_operator", "camera_data")
SCOPE_KEYS = ("status", "refusal", "consumed", "requested_runner", "requested_step", "requested_source",
              "requested_layout", "step", "source", "run_id", "revision", "manifest_sha256", "deadline_utc",
              "max_seconds", "approval", "hud_config_id", "hud_config_sha256", "region", "rotation", "alignment",
              "presence_watch")
# The validate mode's view for the box panel: the centre half of the frame turned a quarter turn (E103's view; the
# stand-in's frames have no right way up) and the neutral alignment.
VALIDATION_REGION = regions.Region(0.25, 0.25, 0.75, 0.75, rotation=90)
COMMAND_BLOCKS = {"command": COMMAND_KEYS, "scope_record": SCOPE_KEYS}
ERROR_BLOCKS.update(COMMAND_BLOCKS)
CAMERA_BLOCKS = {
    **camera_stage.REPORT_BLOCKS, **COMMAND_BLOCKS,
    "marks": ("cues_requested", "cues_not_reached", "mark_labels_requested", "key_marks_enabled", "key_presses",
              "key_listener_error", "marks_dropped", "recorded_count", "recorded_not_reported", "recorded",
              "first_frame"),
    "target": ("label", "rule", "window_seconds", "fraction", "max_gap_seconds", "found", "window_start_seconds",
               "named_fraction", "longest_gap_seconds", "void", "basis", "physical_display_observed"),
    "timebase": ("monotonic_clock", "wall_clock", "wall_clock_derivation", "frame_time", "cue_origin", "anchor",
                 "anchor_at_report", "wall_clock_step_ns"),
}
SYNTHETIC_BLOCKS = {**synthetic_rows.REPORT_BLOCKS, **COMMAND_BLOCKS}
VALIDATION_BLOCKS = {
    "report_kind": None, "schema_version": None, "status": None, "reason": None, **dict.fromkeys(gates.claims()),
    "validation_only": None, "images_saved": None, "pixels_in_report": None, "camera_opened": None,
    "command": COMMAND_KEYS,
    "helper": ("backend", "fake", "build_dirty", "sdk_release", "contract"),
    "camera": ("status", "reason", "bridge_mode", "frames_emitted", "frames_sent", "acknowledged_presented",
               "labels", "expiries", "tile_already_down", "source_cleanup_ok", "bridge_cleanup_ok", "ok"),
    "camera_panel": ("status", "reason", "bridge_mode", "layout_id", "update_bytes", "region_rotation",
                     "frames_emitted", "frames_sent", "acknowledged_presented", "results_sent", "boxes_sent", "labels",
                     "expiries",
                     "tile_already_down", "source_cleanup_ok", "bridge_cleanup_ok", "ok"),
    "synthetic_rows": ("status", "reason", "bridge_mode", "requested", "acknowledged_presented", "stops_reached",
                       "stops_expired", "stops_within_bound", "bridge_cleanup_ok", "complete", "ok"),
}


class ValidationDetector:
    """The validate mode's detector: one fixed box named ``bottle`` for every frame; it never reads a pixel."""
    name, runtime, classes = "validation-scripted", "none", ("bottle",)

    def detect(self, image, threshold):
        height, width = image.shape[:2]
        return DetectionResult((Detection("bottle", 0.9, (width // 8, height // 8, width // 2, height // 2)),), 0.0)


def pinned(path):
    """A file of the checkout the scope record is bound to, at its manifest path."""
    return Path(scope.ROOT) / path


def command_block(mode, **fields):
    block = dict.fromkeys(COMMAND_KEYS)
    block.update(name=RUNNER, mode=mode, exit_code=1, report_saved_by_operator=False, **fields)
    return block


def error_report(kind, mode, reason, *, command=None, run_scope=None, cleanup=None):
    """The short report that stands in for one that did not fit its schema: what failed, and what must not be
    lost with it (the command's block, the scope's, and the cleanup verdicts)."""
    command = command_block(mode) if command is None else command
    command.update(refusal="report_invalid", exit_code=1)
    reason = reason if type(reason) is str and camera_stage.IDENTIFIER.match(reason) else REPORT_INVALID
    return camera_stage.check_report(
        {"report_kind": kind, "schema_version": 1, "status": "error", "reason": reason, **gates.claims(),
         "report_replaced": True, "command": command, "scope_record": None if run_scope is None else run_scope.report(),
         "cleanup": cleanup}, ERROR_BLOCKS, MAX_REPORT_BYTES)


def finish(report, blocks, mode, *, run_scope=None, cleanup=None):
    """Check the report against its schema; a report that does not fit is replaced by one that says so."""
    try:
        return camera_stage.check_report(report, blocks, MAX_REPORT_BYTES)
    except ValueError as error:
        return error_report(report.get("report_kind"), mode, str(error), command=report.get("command"),
                            run_scope=run_scope, cleanup=cleanup)


@contextlib.contextmanager
def relayed_stop(recorded):
    """A stop event that is set once ``recorded`` (``guard.cancellation``'s list) holds a signal.

    The relay is a thread that looks every 50 ms and is joined when the
    block ends, so it leaves nothing behind. The event is a real
    ``threading.Event``, which the stage, the pipeline and the helper's
    supervision wait on.
    """
    stop, done = threading.Event(), threading.Event()

    def relay():
        while not done.wait(RecordedStop.POLL_SECONDS):
            if recorded:
                stop.set()
                return
    thread = threading.Thread(target=relay, name="camera-hud-signal-relay", daemon=True)
    thread.start()
    try:
        yield stop
    finally:
        done.set()
        thread.join(2.0)


def runtime_dir_refusal():
    """Why the helper would refuse its lock directory, or None: the helper's own rule, checked before the record
    is spent. ``XDG_RUNTIME_DIR`` must be set, absolute, without a trailing ``/``, ``/.`` or ``/..``, and a
    directory (not a link) owned by this user with mode 0700."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        return "helper_runtime_dir_missing"
    if not value.startswith("/") or value.endswith(("/", "/.", "/..")):
        return "helper_runtime_dir_invalid"
    try:
        status = os.stat(value, follow_symlinks=False)
    except OSError:
        return "helper_runtime_dir_invalid"
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o700:
        return "helper_runtime_dir_invalid"
    return None


def report_path_refusal(path):
    """Why ``--report PATH`` cannot be written, or None: a new ``.json`` path in an existing directory."""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".json":
        return "--report needs a .json path"
    if not target.parent.is_dir():
        return f"--report: {target.parent} is not an existing directory"
    if target.exists() or target.is_symlink():
        return f"--report: {target} already exists; a report is never overwritten"
    return None


def record_consulted(report):
    """Whether the run got as far as its scope record: verified, refused by its content, or spent."""
    block = report.get("scope_record") or {}
    return block.get("consumed") is True or block.get("status") in ("granted", "refused")


class RecordedStop:
    """The synthetic rows' stop: set once a signal was recorded (``guard.cancellation``), or by ``set``.

    Event-like (``is_set``, ``set``, ``wait``) and polled every ``POLL_SECONDS``, so it needs no thread and no
    descriptor and leaves nothing behind when the run is over.
    """
    POLL_SECONDS = 0.05

    def __init__(self, recorded):
        self.recorded, self._set = recorded, False

    def is_set(self):
        return self._set or bool(self.recorded)

    def set(self):
        self._set = True

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = self.POLL_SECONDS if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.POLL_SECONDS, remaining))
        return self.is_set()


def scope_refusal(run_scope):
    """Why a gate refused, for the report: the scope's own reason, or that a site's preconditions failed."""
    if run_scope is None:
        return "scope_record_required"
    if run_scope.refusal in (None, "scope_revoked"):  # Refused before the record was consulted, then given back.
        return "site_preconditions_failed"
    return run_scope.refusal


def output_bound(run_scope):
    """The real-output client's own bound: the scope's seconds, which the client enforces by ending output."""
    seconds = getattr(run_scope, "seconds", None)
    return seconds if type(seconds) in (int, float) and 0 < seconds < float("inf") else None


def bridge_usable(binary):
    return Path(binary).is_file() and os.access(binary, os.X_OK)


# ---- the camera mode ------------------------------------------------------------------------------------------------

def marks_block(session, withheld):
    recorded = session.marks()
    report = session.report()
    return {"cues_requested": report["cues_requested"], "cues_not_reached": report["cues_not_reached"],
            "mark_labels_requested": [{"label": label} for label in report["mark_labels_requested"]],
            "key_marks_enabled": report["key_marks_enabled"], "key_presses": report["key_presses"],
            "key_listener_error": camera_stage._identifier(report["key_listener_error"], None),
            "marks_dropped": report["marks_dropped"], "recorded_count": len(recorded),
            "recorded_not_reported": max(0, len(recorded) - MAX_REPORTED_MARKS),
            "recorded": camera_stage.report_value(recorded[:MAX_REPORTED_MARKS], withheld),
            "first_frame": None if session.origin_ns is None else session.clock.stamp(session.origin_ns)}


def target_block(report, label):
    """The G10 window rule for ``label`` over the stage report's own intervals; ``void`` says why there is none."""
    block = {"label": label, "rule": "g10_named_in_window", **G10_WINDOW, "found": None,
             "window_start_seconds": None, "named_fraction": None, "longest_gap_seconds": None, "void": None,
             "basis": "bridge_frame_presented_hide_show_and_expiry_lines", "physical_display_observed": False}
    try:
        block.update(camera_stage.named_in_window(report, label, **G10_WINDOW))
    except (ValueError, KeyError, TypeError) as error:
        block["void"] = camera_stage._identifier(str(error), "camera_hud_window_unavailable")
    return block


def exit_code(report, signals=()):
    """0 for a run that ended on its bound or on the wearer's removal with clean cleanup; 128 + the signal for a
    cancelled run (the recorded one, else the helper's supervision's, else SIGINT); else 1."""
    if report["status"] in ("completed", "presence_ended") and (report.get("cleanup") or {}).get("ok") is True:
        return 0
    if report["status"] != "cancelled":
        return 1
    number = signals[0] if signals else ((report.get("source") or {}).get("export") or {}).get("signal")
    return 128 + (number if type(number) is int and 0 < number < 65 else signal.SIGINT)


def run_camera(limits, *, run_scope=None, helper=None, bridge=None, detector=DEFAULT_DETECTOR, detector_factory=None,
               threshold=POC_THRESHOLD, directions=True, orientation="identity", target_label=None, cues=(),
               mark_labels=(), key_fd=None, terminal=None, confirmed=False, validation_only=False, client_env=None,
               extra_args=(), stop=None, signals=None, mode="camera", layout=HUD_CONFIG, region=None, alignment=None):
    """One bounded camera HUD run; returns the report (the stage's plus this command's blocks). Main thread only.

    ``layout`` is the fixed layout the run draws for (the strip unless the
    box panel is named), ``region`` the ``Region`` the detector is given and
    ``alignment`` the panel's ``Alignment``; the stage and the client are
    built from them, and a device run's ``run_scope`` must have been asked
    for the same, or the sites refuse.

    ``run_scope`` is the ``RunScope`` of a device run (None in validation,
    where nothing is displayed). ``helper`` and ``bridge`` default to the
    files the checkpoint manifest hashes; the permission sites refuse real
    output with any other. ``confirmed`` is the terminal confirmation's
    result, passed to the helper as its parent's confirmation. Everything
    cheap is checked before the stage runs, because the stage's first act in
    real output is the record check, which consumes the record: the helper
    and the bridge exist, the detector loads, the target label is one of its
    classes, and for a device run the helper's lock directory is one it
    accepts. A refusal by a gate ends ``refused`` with the gate's reason and
    the scope's own; nothing was launched. ``signals`` is the list
    ``guard.cancellation`` records into, for a caller that covers the whole
    run with it: a recorded signal makes the run ``cancelled`` and the report
    is still the run's own.
    """
    cues, mark_labels = marks.check_cues(cues), marks.check_mark_labels(mark_labels)
    helper = pinned(manifest.CAMERA_HELPER) if helper is None else helper
    bridge = (DEFAULT_BRIDGE if validation_only else pinned(manifest.BRIDGE)) if bridge is None else bridge
    clock = marks.Clock()
    session = marks.MarkSession(clock, cues, mark_labels, key_fd, sys.stderr if terminal is None else terminal)
    command = command_block(mode, confirmation=confirmation.OPERATOR_TERMINAL if confirmed else None,
                            seconds_requested=limits.get("max_seconds"), fps_requested=limits.get("fps"),
                            max_frames_requested=limits.get("max_frames"),
                            detector=detector if detector_factory is None else None,
                            camera_data=CAMERA_DATA_STATEMENT)
    signals = [] if signals is None else signals
    source = CameraHelperSource(helper, limits, confirmed=confirmed, extra_args=extra_args,
                                attach_seconds=ATTACH_SECONDS)
    # The strip's client names no layout, so its bridge is launched exactly as it always was.
    client = ObservedFrameClient(bridge, validation_only=validation_only, env=client_env, scope=run_scope,
                                 max_output_seconds=None if validation_only else output_bound(run_scope),
                                 layout=None if layout is HUD_CONFIG else layout)
    refusal = model = None
    try:
        camera_helper.helper_path(helper)
    except SightCircuitError as error:
        refusal = error.reason
    if refusal is None and not bridge_usable(bridge):
        refusal = "overlay_bridge_missing"
    if refusal is None and not validation_only:
        refusal = runtime_dir_refusal()
    if refusal is None:
        try:
            model = detector_factory() if detector_factory is not None else create_detector(detector)
        except Exception:
            refusal = "detector_unavailable"
    if refusal is None and target_label is not None and target_label not in model.classes:
        refusal = "target_label_unknown"

    stage = CameraHudStage(source, model, client, threshold=threshold, directions=directions, orientation=orientation,
                           max_fps=limits["fps"], seconds=limits["max_seconds"], config=layout, region=region,
                           alignment=alignment, on_frame=lambda frame: session.first_frame(frame.observed_ns),
                           target_label=target_label if refusal is None else None, signalled=lambda: bool(signals))
    status = invalid = None
    if refusal is not None:
        status = ("error", refusal)
    else:
        session.start()
        try:
            stage.run(stop)
        except ValueError as error:
            if str(error) in (FIXED_HUD_REFUSAL, LAYOUT_REFUSAL, CAMERA_INPUT_REFUSAL):
                status = ("refused", str(error))  # A gate refused: nothing was launched.
                refusal = scope_refusal(run_scope)
            elif str(error).startswith(REPORT_INVALID):
                invalid = str(error)  # The run happened; only its report did not fit.
            else:
                raise
        finally:
            session.stop()
    cleanup = {"source_ok": source.cleanup_ok is True,
               "bridge_ok": (stage._bridge_report or {}).get("cleanup_confirmed") is True}
    cleanup["ok"] = cleanup["source_ok"] and cleanup["bridge_ok"]
    try:
        report = stage.report() if invalid is None else None
    except ValueError as error:
        invalid = str(error)
    if invalid is not None:
        return error_report(camera_stage.REPORT_KIND, mode, invalid, command=command, run_scope=run_scope, cleanup=cleanup)
    if status is not None:
        report.update(status=status[0], reason=status[1])
    elif signals and report["status"] in ("stopped", "cancelled"):
        report.update(status="cancelled", reason="signal")
    command["refusal"] = refusal
    withheld = [report["fields_withheld"]]
    report.update(command=command, scope_record=None if run_scope is None else run_scope.report(),
                  marks=marks_block(session, withheld), timebase=camera_stage.report_value(clock.report(), withheld),
                  target=None if target_label is None or status is not None else target_block(report, target_label))
    report["fields_withheld"] = withheld[0]
    command["exit_code"] = exit_code(report, signals)
    return finish(report, CAMERA_BLOCKS, mode, run_scope=run_scope, cleanup=cleanup)


# ---- the synthetic rows ---------------------------------------------------------------------------------------------

def run_synthetic_rows(seconds, *, stops=3, stop_seconds=3.0, run_scope=None, bridge=None, confirmed=False,
                       validation_only=False, client_env=None, stop=None, mode="synthetic_rows"):
    """One bounded run of counter rows with scheduled stops; returns the report. The camera is never opened."""
    bridge = (DEFAULT_BRIDGE if validation_only else pinned(manifest.BRIDGE)) if bridge is None else bridge
    command = command_block(mode, confirmation=confirmation.OPERATOR_TERMINAL if confirmed else None,
                            seconds_requested=seconds, fps_requested=synthetic_rows.UPDATE_HZ, stops_requested=stops)
    client = ObservedFrameClient(bridge, validation_only=validation_only, env=client_env, scope=run_scope,
                                 max_output_seconds=None if validation_only else output_bound(run_scope))
    driver = synthetic_rows.SyntheticRows(client, seconds=seconds, stops=stops, stop_seconds=stop_seconds)
    status = None
    if not bridge_usable(bridge):
        status, command["refusal"] = ("error", "overlay_bridge_missing"), "overlay_bridge_missing"
    else:
        try:
            driver.run(stop)
        except ValueError as error:
            if str(error).startswith(REPORT_INVALID):
                pass  # The run happened; its report is asked for again below and replaced there.
            elif str(error) not in (FIXED_HUD_REFUSAL, LAYOUT_REFUSAL):
                raise
            else:
                status = ("refused", str(error))  # The gate refused: the bridge was not launched.
                client.withdraw()  # A refused run gives its scope back: nothing can start under it later.
                command["refusal"] = scope_refusal(run_scope)
    bridge_ok = (driver._bridge_report or {}).get("cleanup_confirmed") is True or driver.bridge_left_on_stop
    cleanup = {"bridge_ok": bridge_ok, "ok": bridge_ok}
    try:
        report = driver.report()
    except ValueError as error:
        if not str(error).startswith(REPORT_INVALID):
            raise
        return error_report(synthetic_rows.REPORT_KIND, mode, str(error), command=command, run_scope=run_scope,
                            cleanup=cleanup)
    if status is not None:
        report.update(status=status[0], reason=status[1])
    report.update(command=command, scope_record=None if run_scope is None else run_scope.report())
    command["exit_code"] = 0 if report["complete"] else 1
    recorded = getattr(stop, "recorded", None)
    if recorded and report["status"] == "stopped":  # A signal stopped it: say so, as the camera mode does.
        report.update(status="cancelled", reason="signal")
        command["exit_code"] = 128 + recorded[0]
    return finish(report, SYNTHETIC_BLOCKS, mode, run_scope=run_scope, cleanup=cleanup)


# ---- the validate mode ----------------------------------------------------------------------------------------------

def describe_helper(binary):
    """The helper's ``--describe`` record: build facts only, no connect, no camera, no confirmation; or None."""
    try:
        completed = subprocess.run([camera_helper.helper_path(binary), "--describe"], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=DESCRIBE_TIMEOUT_SECONDS)
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    except (OSError, ValueError, subprocess.TimeoutExpired, SightCircuitError):
        return None
    return next((line for line in lines if isinstance(line, dict) and line.get("event") == "describe"), None)


def _positive(value):
    return type(value) is int and value > 0


def validate(helper=None, bridge=None):
    """The whole path with nothing real in it: the fake helper, a scripted detector, the validation bridge.

    The helper must say ``backend: fake`` in its ``--describe`` record
    before it is asked to export anything; any other helper is refused
    without being launched for an export, so this mode can never open a
    camera. The fake keeps its lock in ``XDG_RUNTIME_DIR``; it gets a
    temporary one for the run, and the variable is restored afterwards.
    Returns the validation report; ``validated`` when both parts ran clean.
    """
    helper = Path(guard.ROOT) / FAKE_HELPER if helper is None else helper
    bridge = DEFAULT_BRIDGE if bridge is None else bridge
    command = command_block("validate", detector=ValidationDetector.name)
    report = {"report_kind": VALIDATION_KIND, "schema_version": 2, "status": "validation_failed", "reason": None,
              **gates.claims(), "validation_only": True, "images_saved": False, "pixels_in_report": False,
              "camera_opened": False, "command": command, "helper": None, "camera": None, "camera_panel": None,
              "synthetic_rows": None}
    described = describe_helper(helper)
    fake = described is not None and described.get("backend") == "fake" and described.get("sdk_release") is None
    report["helper"] = {"backend": camera_stage._identifier((described or {}).get("backend"), None), "fake": fake,
                        "build_dirty": (described or {}).get("build_dirty"),
                        "sdk_release": None if fake else "withheld",
                        "contract": camera_stage._identifier((described or {}).get("contract"), None)}
    if not fake:
        report["reason"] = command["refusal"] = "validate_requires_fake_helper"
        return finish(report, VALIDATION_BLOCKS, "validate")
    if not bridge_usable(bridge):
        report["reason"] = command["refusal"] = "overlay_bridge_missing"
        return finish(report, VALIDATION_BLOCKS, "validate")
    # The fake backend has no camera and no wearer: its parent's confirmation is this check that it is the fake.
    previous = os.environ.get("XDG_RUNTIME_DIR")
    panel = camera_stage.box_layout()  # By what it is, never by its place in the contract's list.
    with tempfile.TemporaryDirectory(prefix="sightcircuit-camera-hud-validate-") as runtime_directory:
        os.environ["XDG_RUNTIME_DIR"] = runtime_directory
        try:
            stage = run_camera(VALIDATION_LIMITS, helper=helper, bridge=bridge, detector_factory=ValidationDetector,
                               confirmed=True, validation_only=True, extra_args=VALIDATION_HELPER_ARGS,
                               target_label="bottle", mode="validate")
            # The same path for the box panel, through a region turned a quarter turn: a megabyte per update.
            boxed = run_camera(VALIDATION_LIMITS, helper=helper, bridge=bridge, detector_factory=ValidationDetector,
                               confirmed=True, validation_only=True, extra_args=VALIDATION_HELPER_ARGS,
                               target_label="bottle", mode="validate", layout=panel, region=VALIDATION_REGION,
                               alignment=Alignment())
        finally:
            if previous is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = previous
    frames, cleanup, source = stage.get("frames") or {}, stage.get("cleanup") or {}, stage.get("source") or {}
    camera = {"status": stage["status"], "reason": stage["reason"], "bridge_mode": stage.get("bridge_mode"),
              "frames_emitted": source.get("frames_emitted"), "frames_sent": frames.get("sent"),
              "acknowledged_presented": frames.get("acknowledged_presented"),
              "labels": [{"label": row["label"], "spells": row["spells"]} for row in (stage.get("labels") or {}).get("rows", [])],
              "expiries": (stage.get("frame_expired") or {}).get("count"),
              "tile_already_down": (stage.get("end") or {}).get("tile_already_down"),
              "source_cleanup_ok": cleanup.get("source_ok"), "bridge_cleanup_ok": cleanup.get("bridge_ok")}
    camera["ok"] = (stage["status"] == "completed" and cleanup.get("ok") is True and stage.get("validation_only") is True
                    and (frames.get("sent") or 0) > 0 and frames.get("acknowledged_presented") == frames.get("sent")
                    and [row["label"] for row in camera["labels"]] == ["bottle"])
    report["camera"] = camera
    frames, cleanup, source = boxed.get("frames") or {}, boxed.get("cleanup") or {}, boxed.get("source") or {}
    config, counted = boxed.get("config") or {}, boxed.get("boxes")
    counted = counted if isinstance(counted, dict) else {}  # A missing block is a failed part, not an empty one.
    camera_panel = {
        "status": boxed["status"], "reason": boxed["reason"], "bridge_mode": boxed.get("bridge_mode"),
        "layout_id": config.get("hud_config_id"), "update_bytes": config.get("update_bytes"),
        "region_rotation": (boxed.get("view") or {}).get("rotation"),
        "frames_emitted": source.get("frames_emitted"), "frames_sent": frames.get("sent"),
        "acknowledged_presented": frames.get("acknowledged_presented"),
        "results_sent": counted.get("results_sent"), "boxes_sent": counted.get("boxes_sent"),
        "labels": [{"label": row["label"], "spells": row["spells"]} for row in (boxed.get("labels") or {}).get("rows", [])],
        "expiries": (boxed.get("frame_expired") or {}).get("count"),
        "tile_already_down": (boxed.get("end") or {}).get("tile_already_down"),
        "source_cleanup_ok": cleanup.get("source_ok"), "bridge_cleanup_ok": cleanup.get("bridge_ok")}
    camera_panel["ok"] = (
        boxed["status"] == "completed" and cleanup.get("ok") is True and boxed.get("validation_only") is True
        and config.get("hud_config_id") == panel.layout_id
        and config.get("update_bytes") == panel.width_px * panel.height_px * 4
        and (frames.get("sent") or 0) > 0 and frames.get("acknowledged_presented") == frames.get("sent")
        # The boxes block is there and agrees with the frames block: every result the client accepted
        # (``submitted``; the run's last may be withdrawn unwritten when the end comes, so not ``sent``)
        # carried the one scripted box.
        and _positive(counted.get("results_sent")) and counted.get("results_sent") == frames.get("submitted")
        and counted.get("boxes_sent") == counted.get("results_sent")
        and counted.get("boxes_selected") == counted.get("results_counted")
        and [row["label"] for row in camera_panel["labels"]] == ["bottle"])
    report["camera_panel"] = camera_panel
    rows = run_synthetic_rows(validation_only=True, bridge=bridge, mode="validate", **VALIDATION_ROWS)
    updates, stops = rows.get("updates") or {}, rows.get("stops") or {}
    report["synthetic_rows"] = {
        "status": rows["status"], "reason": rows["reason"], "bridge_mode": rows.get("bridge_mode"),
        "requested": updates.get("requested"), "acknowledged_presented": updates.get("acknowledged_presented"),
        "stops_reached": stops.get("reached"), "stops_expired": stops.get("expired"),
        "stops_within_bound": stops.get("within_bound"), "bridge_cleanup_ok": (rows.get("cleanup") or {}).get("ok"),
        "complete": rows.get("complete"),
        # Expiry timing on a loaded CI runner is not a pass criterion here: the stop must be reached and expire.
        "ok": (rows["status"] == "completed" and rows.get("validation_only") is True
               and updates.get("acknowledged_presented") == updates.get("requested")
               and stops.get("expired") == stops.get("reached") == VALIDATION_ROWS["stops"]
               and (rows.get("cleanup") or {}).get("ok") is True)}
    if camera["ok"] and camera_panel["ok"] and report["synthetic_rows"]["ok"]:
        report["status"] = "validated"
    else:
        report["reason"] = ("camera_path_failed" if not camera["ok"] else "camera_panel_path_failed"
                            if not camera_panel["ok"] else "synthetic_rows_failed")
    command["exit_code"] = 0 if report["status"] == "validated" else 1
    return finish(report, VALIDATION_BLOCKS, "validate")


# ---- the command line -----------------------------------------------------------------------------------------------

def write_report(path, report):
    """Write the report to a new ``.json`` file the operator named; refuses to overwrite anything."""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".json" or not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError("--report needs a new .json path in an existing directory")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
    with os.fdopen(descriptor, "w") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return str(target)


def emit(args, report, save):
    """Save the report where the operator named, if this run's report is to be saved, and print it; the exit code."""
    if args.report is not None and save:  # The operator's explicit action to save the report; the file says so.
        report["command"]["report_saved_by_operator"] = True
        try:
            write_report(args.report, report)
        except (OSError, ValueError) as error:
            report["command"].update(report_saved_by_operator=False, exit_code=1)
            print(f"Report not written: {error}", file=sys.stderr)
    print(json.dumps(report, indent=2))
    return report["command"]["exit_code"]


def refused_before_start(mode, reason, blocks, kind):
    """A report for a run that never began (no terminal, a declined answer, a signal before the start)."""
    report = dict.fromkeys(blocks)
    report.update(report_kind=kind, schema_version=1, status="error", reason=reason, **gates.claims(),
                  command=command_block(mode, refusal=reason))
    for name, value in (("validation_only", False), ("images_saved", False), ("pixels_in_report", False),
                        ("physical_display_observed", False), ("fields_withheld", 0), ("camera_opened", False),
                        ("camera_derived", False), ("complete", False),
                        ("bridge_mode", "--fixed-hud")):
        if name in blocks:
            report[name] = value
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate", action="store_true",
                       help="The fake helper, a scripted detector and the validation bridge: nothing real, nothing displayed")
    modes.add_argument("--synthetic-rows", action="store_true",
                       help="Device step L-D3: a counter row at 5 Hz on the fixed HUD with scheduled stops; no camera")
    modes.add_argument("--camera", action="store_true",
                       help="Device steps L-D4 and L-D5: names from the left camera on the fixed HUD, at most 90 s")
    parser.add_argument("--allow-hud-output", action="store_true",
                        help="Opt in to real HUD output; required by both device modes and permits nothing by itself")
    parser.add_argument("--allow-camera-input", action="store_true",
                        help="Opt in to launching the camera helper (Camera Data); required by --camera, permits nothing by itself")
    parser.add_argument("--scope-record", type=Path, metavar="PATH",
                        help="The one-run scope record the Frame manager staged for this approved step")
    parser.add_argument("--manifest", type=Path, metavar="PATH",
                        help="The checkpoint manifest the record is bound to; it must carry the camera HUD's group and verify")
    parser.add_argument("--step", choices=CAMERA_STEPS,
                        help="--camera: the device step this run is; the record must be for the same step")
    parser.add_argument("--seconds", type=int, metavar="SECONDS",
                        help=f"The run's bound: 1..{MAX_SECONDS} for --camera, up to {MAX_SYNTHETIC_SECONDS} "
                             f"for --synthetic-rows (its record's 120 s less {SYNTHETIC_ALLOWANCE_SECONDS} s for the "
                             "bridge's start); the record's own bound is checked too")
    parser.add_argument("--fps", type=int, choices=range(1, MAX_FPS + 1), metavar=f"1..{MAX_FPS}",
                        help=f"--camera: frames a second the helper exports (default {DEFAULT_FPS})")
    parser.add_argument("--stops", type=int, metavar=f"0..{synthetic_rows.MAX_STOPS}",
                        help="--synthetic-rows: scheduled stops in which no frame is sent (default 3)")
    parser.add_argument("--stop-seconds", type=float, metavar="SECONDS",
                        help="--synthetic-rows: the length of each stop (default 3)")
    rows = parser.add_mutually_exclusive_group()
    rows.add_argument("--directions", dest="directions", action="store_const", const=True, default=None,
                      help="--camera: rows carry the coarse arrow from the box position (owner decision 3)")
    rows.add_argument("--names-only", dest="directions", action="store_const", const=False,
                      help="--camera: rows carry names only")
    parser.add_argument("--orientation", choices=sorted(ORIENTATIONS),
                        help="--camera: the camera image's orientation relative to the view, as the device step measured it")
    parser.add_argument("--layout", choices=[layout.layout_id for layout in FIXED_LAYOUTS],
                        help=f"--camera: the fixed layout the run draws for (default {HUD_CONFIG.layout_id}, the "
                             "strip); the record must name the same")
    parser.add_argument("--region", metavar="X0,Y0,X1,Y1",
                        help="--camera: the region of the frame the detector is given, as fractions (default: the "
                             "whole frame); the record must name the same")
    parser.add_argument("--rotation", type=int, choices=regions.ROTATIONS,
                        help="--camera: the clockwise quarter turn applied to the region (default 0); the record "
                             "must name the same")
    parser.add_argument("--alignment", metavar="SX,SY,OX,OY,TURN",
                        help="--camera on the box panel, required there: the wearer's alignment, the project's own "
                             "five numbers (neutral is 1,1,0,0,0); the record must name the same")
    parser.add_argument("--detector", choices=DETECTORS, help=f"--camera: the model (default {DEFAULT_DETECTOR})")
    parser.add_argument("--threshold", type=float, help=f"--camera: the detection threshold (default {POC_THRESHOLD})")
    parser.add_argument("--target-label", metavar="CLASS",
                        help="--camera: a class of the detector for the report's G10 window verdict")
    parser.add_argument("--cue", action="append", default=[], metavar="SECONDS:LABEL",
                        help="--camera: print LABEL with a bell SECONDS after the first frame and record the mark; "
                             f"labels: {', '.join(marks.VOCABULARY)}")
    parser.add_argument("--key-marks", action="store_true",
                        help=f"--camera: record a mark ({marks.KEY_LABEL}) for each Enter on the terminal during the run")
    parser.add_argument("--mark-labels", default="", metavar="LABEL,LABEL",
                        help="--camera: labels successive Enter presses record; implies --key-marks")
    parser.add_argument("--helper", type=Path,
                        help="--validate only: a fake-backend helper (default: the --fake build under artifacts/)")
    parser.add_argument("--report", type=Path, help="New .json file for the report; saving it is your action (Camera Data)")
    return parser


def parse_alignment(text):
    """``SX,SY,OX,OY,TURN`` as an ``Alignment``; ``ValueError`` with a sentence for anything else."""
    parts = text.split(",") if isinstance(text, str) else ()
    try:
        values = [float(part) for part in parts]
    except ValueError:
        values = []
    if len(values) != len(ALIGNMENT_FIELDS) or not all(math.isfinite(value) for value in values) or values[4] != int(values[4]):
        raise ValueError("--alignment needs five numbers: scale_x,scale_y,offset_x,offset_y and a turn of 0, 90, 180 or 270")
    try:
        return Alignment(*values[:4], int(values[4]))
    except ValueError:
        raise ValueError("--alignment: scales are 0.25 to 4, offsets -1 to 1, the turn 0, 90, 180 or 270") from None


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.threshold is not None and (not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1):
        parser.error("--threshold must be between zero and one")
    camera_only = (args.allow_camera_input or args.directions is not None or args.orientation or args.target_label
                   or args.cue or args.key_marks or args.mark_labels or args.step or args.fps is not None
                   or args.detector is not None or args.threshold is not None or args.layout is not None
                   or args.region is not None or args.rotation is not None or args.alignment is not None)
    rows_only = args.stops is not None or args.stop_seconds is not None
    device_only = args.allow_hud_output or args.scope_record or args.manifest or args.seconds
    if args.report is not None:  # Before anything else: a path that cannot be written never costs a run.
        refusal = report_path_refusal(args.report)
        if refusal is not None:
            parser.error(refusal)
    if args.validate:
        if camera_only or rows_only or device_only:
            parser.error("--validate takes no run arguments")
        report = validate(args.helper)
        save = True
    else:
        if args.helper is not None:
            parser.error("--helper is for --validate; a device run launches the helper the manifest pins")
        if not args.allow_hud_output:
            parser.error("--allow-hud-output is required for a device mode (or use --validate)")
        if args.scope_record is None or args.manifest is None or args.seconds is None:
            parser.error("--scope-record, --manifest and --seconds are required for a device mode")
        if args.synthetic_rows:
            if camera_only:
                parser.error("--synthetic-rows never opens the camera and takes no camera arguments")
            if not 1 <= args.seconds <= MAX_SYNTHETIC_SECONDS:
                parser.error(f"--seconds must be 1..{MAX_SYNTHETIC_SECONDS} for --synthetic-rows: its record allows "
                             f"{synthetic_rows.MAX_SECONDS} s and {SYNTHETIC_ALLOWANCE_SECONDS} s are the bridge's start")
            stops = 3 if args.stops is None else args.stops
            stop_seconds = 3.0 if args.stop_seconds is None else args.stop_seconds
            try:
                synthetic_rows.schedule(args.seconds, stops, stop_seconds)
            except ValueError as error:
                parser.error(str(error))
            blocks, kind, mode = SYNTHETIC_BLOCKS, synthetic_rows.REPORT_KIND, "synthetic_rows"
        else:
            if rows_only:
                parser.error("--stops and --stop-seconds belong to --synthetic-rows")
            if not args.allow_camera_input:
                parser.error("--allow-camera-input is required to launch the camera helper")
            if not 1 <= args.seconds <= MAX_SECONDS:
                parser.error(f"--seconds must be 1..{MAX_SECONDS} for --camera")
            if args.step is None:
                parser.error(f"--camera needs --step ({' or '.join(CAMERA_STEPS)}): the record must be for the step that runs")
            layout = HUD_CONFIG if args.layout is None else fixed_layout(args.layout)
            alignment = None
            if layout is HUD_CONFIG:
                if args.directions is None or args.orientation is None:
                    parser.error("--camera needs --directions or --names-only, and --orientation: neither has a default")
                if args.alignment is not None:
                    parser.error("--alignment belongs to the box panel; the strip's rows take none")
            else:
                if args.directions is not None or args.orientation is not None:
                    parser.error("--directions, --names-only and --orientation belong to the strip; the box panel "
                                 "draws a box where the object is")
                if args.alignment is None:
                    parser.error("--camera on the box panel needs --alignment: it has no default (neutral is 1,1,0,0,0)")
                try:
                    alignment = parse_alignment(args.alignment)
                except ValueError as error:
                    parser.error(str(error))
            try:
                region = regions.Region(*(regions.WHOLE if args.region is None else regions.parse_region(args.region)),
                                        rotation=0 if args.rotation is None else args.rotation)
            except ValueError as error:
                parser.error(f"--region: {error}")
            fps = DEFAULT_FPS if args.fps is None else args.fps
            detector = DEFAULT_DETECTOR if args.detector is None else args.detector
            threshold = POC_THRESHOLD if args.threshold is None else args.threshold
            try:
                cues = marks.check_cues(marks.parse_cue(text, args.seconds) for text in args.cue)
                mark_labels = marks.check_mark_labels(item for item in args.mark_labels.split(",") if args.mark_labels)
            except ValueError as error:
                parser.error(str(error))
            if args.target_label is not None and args.target_label not in class_names(detector):
                parser.error(f"--target-label must be a class of {detector}: {', '.join(class_names(detector))}")
            limits = {"fps": fps, "max_seconds": args.seconds, "max_frames": args.seconds * fps}
            blocks, kind, mode = CAMERA_BLOCKS, camera_stage.REPORT_KIND, "camera"
        report = refused_before_start(mode, "not_started", blocks, kind)
        refusal = None
        with guard.sigterm_cancels():  # The confirmation: a signal here ends a run that never began.
            try:
                if args.synthetic_rows:
                    refusal = confirmation.confirm_synthetic_rows(args.seconds, synthetic_rows.UPDATE_HZ)
                else:
                    notice = confirmation.DISPLAY_NOTICE if layout is HUD_CONFIG else confirmation.BOX_DISPLAY_NOTICE
                    refusal = confirmation.confirm_on_terminal(limits, (camera_helper.LEFT_CAMERA,), notices=(notice,))
                if refusal is not None:  # Nothing is launched, loaded or consulted without the typed answer.
                    report = refused_before_start(mode, refusal, blocks, kind)
            except guard.CANCELLATION as error:
                refusal = "signal"
                report = refused_before_start(mode, "signal", blocks, kind)
                report.update(status="cancelled")
                report["command"]["exit_code"] = 128 + guard.signal_number(error)
        if refusal is None:
            # From here to the report every signal is recorded, never raised: the run stops, its teardown
            # finishes, and the report is the run's own however many signals arrive.
            with guard.cancellation() as recorded:
                if args.synthetic_rows:
                    run_scope = scope.RunScope(args.scope_record, args.manifest, runner=RUNNER, step=SYNTHETIC_STEP,
                                               source=scope.SYNTHETIC,
                                               seconds=args.seconds + SYNTHETIC_ALLOWANCE_SECONDS)
                    report = run_synthetic_rows(args.seconds, stops=stops, stop_seconds=stop_seconds,
                                                run_scope=run_scope, confirmed=True, stop=RecordedStop(recorded))
                else:
                    # The scope is asked for exactly what the stage is built from; each is compared with the
                    # record before anything is launched (the scope's steps 1 and 3, which consume nothing).
                    run_scope = scope.RunScope(args.scope_record, args.manifest, runner=RUNNER, step=args.step,
                                               source=scope.CAMERA_LEFT, seconds=args.seconds, hud_config=layout,
                                               region=list(region.fractions), rotation=region.rotation,
                                               alignment=None if alignment is None else alignment.report(),
                                               presence_watch=False)
                    with relayed_stop(recorded) as stop:
                        report = run_camera(limits, run_scope=run_scope, detector=detector, threshold=threshold,
                                            layout=layout, region=region, alignment=alignment,
                                            directions=True if args.directions is None else args.directions,
                                            orientation="identity" if args.orientation is None else args.orientation,
                                            target_label=args.target_label, cues=cues, mark_labels=mark_labels,
                                            key_fd=marks.terminal_fd() if args.key_marks or mark_labels else None,
                                            confirmed=True, stop=stop, signals=recorded)
                # Still inside the guard: a further signal cannot interrupt the saving or the printing.
                return emit(args, report, record_consulted(report))
        # A run that was declined, or refused before its record was consulted, writes no file: the report is on
        # the terminal and the named path stays free for the real run.
        save = record_consulted(report)
    return emit(args, report, save)


if __name__ == "__main__":
    sys.exit(main())
