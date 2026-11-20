"""Bounded CPU recognition from frames the Arcturus camera helper exports, without display.

The fourteenth command. The informed start is the command's (owner decision
2026-11-09): it prints the helper's export notice verbatim on its terminal,
requires an interactive stdin and the operator's exact typed ``yes`` (no flag,
variable or piped answer), and only then launches the helper with
``--parent-confirmed``; the report records ``confirmation``
``operator_terminal``. ``sources.camera_helper.run_export`` launches the
``sightcircuit-camera-helper`` binary the operator names (``--helper``) in
``--export-frames`` mode under the supervision primitive, with the same
limits the command enforces (``--seconds`` at most 30 and ``--max-frames`` at
most 300, both required; ``--fps`` at most 10), and reads its camera export v2
stream with the strict reader. Each validated frame is kept in a one-frame
latest slot; the processing thread converts it to the detector's input (at
most 960 pixels), skips it when its acquisition is older than the one-second
input limit, runs the chosen detector and records labels, scores, boxes and
the inference timing. The report is shaped as the mirror recognizer's where
the two agree: run limits, per-frame detections, timing statistics with the
p95 against ``LIVE_INFERENCE_P95_BUDGET_MS``, counts and ``gates.claims()``.

Times, marks and summaries (work-order item L15, so a device run can be
measured). The report declares its timebase: the host monotonic clock is the
measurement and one realtime sample at the run's start places it in UTC, so
the run's start, each frame's acquire time, each mark and the helper's end
record carry both. A mark is a label from a closed vocabulary
(``sightcircuit.camera.marks.VOCABULARY``), never free text: ``--cue
SECONDS:LABEL`` schedules one from the first frame's acquire time and prints
it on the terminal with a bell when due; with ``--key-marks`` or
``--mark-labels``, and only then, Enter on the terminal during the run
records one with the next ``--mark-labels`` label, or ``mark``. What is typed
is counted for line ends and discarded; without those options the terminal is
never read after the confirmation. ``--target-label`` names a detector class
and needs a single camera (``--cameras 0`` or ``1``), so no tally pools the
two; the report then carries, as counts only, the 1-second windows that
held it, the coverage over the in-view spans, the 3x3 cell of its best box's
centre per mark interval and what followed a ``remove`` mark. The cell is
taken on the image as exported: nothing here knows the camera's mounting
orientation. Without these options and without a key press the run is what it
was.

A region of view (work-order item L14, after E100: the frame is 2464 pixels
square and very wide, so a hand-held object is small in it). ``--region
X0,Y0,X1,Y1`` (fractions of the frame, origin top left) and ``--rotation``
(0, 90, 180 or 270 degrees clockwise) make the detector look at that
rectangle only, taken out of the native frame before the resize and turned
(``sightcircuit.camera.region``). Every box, cell and coverage count is still
over the whole frame: boxes are mapped back and each record names its space
(``whole_frame_processing_pixels``, the frame scaled to at most 960, which is
the detector's input space when no region is set). The report's ``view``
block states the region, the rotation and the detector used. Nothing here
knows where "straight ahead" falls in the image or which way up it is: both
are the operator's arguments. Without the two options nothing is cropped
and the report is unchanged.

Camera Data. Under the SDK agreement (sections 1 and 3.2) the detections,
labels, scores and boxes this command derives from camera frames are Camera
Data, and so is every per-frame record in the report. The report says so.
Nothing is saved unless the operator names ``--report``, which is the
operator's explicit action to save Camera Data; the report never contains
pixels, and no preview, still or other file is written.

Gates. Every gate stays closed and none is lifted here (``gates.claims()`` is
in every report). The owner classified this command on 2026-11-09 as a
confirmed use outside the live-input gate, exactly as E48 mirror recognition
is: counts, labels, boxes and timings only, no display, no saved image, at most
30 seconds. It never touches the live ``Pipeline``, the HUD path or a display.
The owner's consent decision of the same day allows counts, labels, boxes,
inference timings and format metadata to be saved locally on the Frame and
published in evidence; pixels, stills, previews and calibration data are never
saved or published, and the camera LED is untouched. Running it against the
helper's SDK build on the Frame is a device step that needs its own approval;
the cloud checks run it against the fake producer only. ``--validate`` runs
the reader against the fake producer's valid and invalid streams without any
helper or detector.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

if not __package__:  # Run as a file: import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sightcircuit import gates
from sightcircuit.camera import contract, fake_export, marks
from sightcircuit.camera import region as regions
# The informed start, shared with ``camera_hud`` (work-order item L9); the names stay this module's.
from sightcircuit.camera.confirmation import CONFIRMATION_ANSWER, OPERATOR_TERMINAL, confirm_on_terminal  # noqa: F401
from sightcircuit.camera.export_reader import ExportReader, ExportRefused
from sightcircuit.convert import to_bgr
from sightcircuit.detection import (DEFAULT_DETECTOR, DETECTORS, LIVE_INFERENCE_P95_BUDGET_MS, POC_THRESHOLD,
                                    class_names, create_detector, describe, require_result)
from sightcircuit.frame import DETECTOR_PIXELS
from sightcircuit.hud.contract import MAX_DISPLAY_AGE_MS
from sightcircuit.latest import LatestSlot
from sightcircuit.pipeline import TimingSample
from sightcircuit.runtime import guard
from sightcircuit.sources import camera_helper

REPORT_KIND = "camera_recognition"
SCHEMA_VERSION = 2  # 2 adds the timebase, wall-clock times, marks, the target and the summaries (item L15).
# The command's bounds (work-order item S7), inside the helper's own (``contract.LIMITS``: 90 s, 900 frames).
MAX_SECONDS, MAX_FRAMES = 30, 300
MAX_FPS = contract.LIMITS["fps"][1]
assert MAX_SECONDS <= contract.LIMITS["max_seconds"][1] and MAX_FRAMES <= contract.LIMITS["max_frames"][1]
DEFAULT_FPS = 5
MAX_LISTED_DETECTIONS = 20
PROCESSOR_JOIN_SECONDS = 15.0
CAMERA_DATA_STATEMENT = (
    "Detections, labels, scores and boxes derived from camera frames are Camera Data under the Arcturus camera SDK "
    "agreement (sections 1 and 3.2); every per-frame record in this report is Camera Data. The owner's consent "
    "decision of 2026-11-09 allows counts, labels, boxes, inference timings and format metadata to be saved locally "
    "and published in evidence; pixels, stills, previews and calibration data are never saved or published. The "
    "report holds no pixels and is written only to the path the operator names with --report, which is the "
    "operator's explicit action to save it.")
GATE_STATEMENT = ("Confirmed use outside the live-input gate (owner decision 2026-11-09, the E48 pattern): bounded, "
                  "no display, no saved image; every gate stays closed and none is lifted")
SCOPE = ("Bounded recognition on frames the camera helper exported; camera identity, clocks and the pixel path are "
         "what the helper declares; no display, no preview, no still; a device run needs the owner's approval")
TIMINGS = ("input_age_ms", "processing_ms", "inference_ms")


def new_report(mode):
    return {"report_kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "mode": mode, "status": "not_started", "exit_code": 1,
            "cleanup_ok": True, "images_saved": False, "pixels_in_report": False, "display_started": False,
            "overlay_requested": False, "source_kind": "arcturus_camera_export", "live_input_verified": False,
            "camera_data": {"detections_are_camera_data": True, "statement": CAMERA_DATA_STATEMENT,
                            "report_saved_by_operator": False, "report_path": None},
            "gate_statement": GATE_STATEMENT, **gates.claims(),
            "frames_received": 0, "frames_processed": 0, "stale_inputs_skipped": 0, "pending_frames_replaced": 0,
            "labels_seen": [], "maximum_pending_frames": 1, "maximum_input_observation_age_ms": MAX_DISPLAY_AGE_MS,
            "inference_p95_budget_ms": LIVE_INFERENCE_P95_BUDGET_MS, "inference_p95_within_budget": None,
            "confidence_is_calibrated_probability": False, "scope": SCOPE}


def frame_record(frame, result, image, input_age_ms, processing_ms, *, clock=None, target_label=None, threshold=0.0,
                 view=None):
    """One per-frame result: identity, timings, labels and up to ``MAX_LISTED_DETECTIONS`` boxes in detector pixels.

    With a ``clock`` the acquire time also appears as UTC (``observed_utc_ns``,
    derived) beside the helper's own realtime stamp; with a ``target_label``
    the record carries ``target``, the best box of that label and its cell.
    With a ``view`` (``{index, region, mapping, also}``: the frame was seen
    through a region) ``result`` is already mapped to the whole frame, the
    record says so (``box_coordinate_space``, ``box_space_size``), names the
    region and where it lay in native pixels, and the target's cell is taken
    over the whole frame; ``also`` holds each scan detector's mapped result.
    """
    space = (image.shape[1], image.shape[0]) if view is None else view["mapping"].space
    record = {"sequence": frame.sequence, "frame_set_sequence": frame.metadata.get("frame_set_sequence"),
              "source_id": frame.source, "camera_index": frame.metadata.get("camera_index"),
              "frame_id": frame.metadata.get("frame_id"), "observed_ns": frame.observed_ns,
              "input_age_ms": round(input_age_ms, 3), "processing_ms": round(processing_ms, 3),
              "inference_ms": round(result.inference_ms, 3), "labels": result.labels,
              "detections": [item.as_dict() for item in result.detections][:MAX_LISTED_DETECTIONS],
              "detection_count": len(result.detections), "native_size": [frame.width, frame.height],
              "detector_input_size": [image.shape[1], image.shape[0]], "box_coordinate_space": DETECTOR_PIXELS}
    if clock is not None:
        record.update(observed_utc_ns=clock.utc(frame.observed_ns), helper_realtime_ns=frame.metadata.get("realtime_ns"))
    if view is not None:
        record.update(box_coordinate_space=regions.FRAME_PIXELS, box_space_size=list(space),
                      view={"index": view["index"], **view["region"].report(), **view["mapping"].report()})
    if target_label is not None:
        record["target"] = marks.target_of(result.detections, target_label, threshold, *space)
    if view is not None and view["also"] is not None:
        record["scan"] = {name: {"target": marks.target_of(other.detections, target_label, threshold, *space),
                                 "detection_count": len(other.detections), "inference_ms": round(other.inference_ms, 3)}
                          for name, other in view["also"].items()}
    return record


class Processor(threading.Thread):
    """Takes the latest frame from the slot and runs the detector; stops once the slot is closed and empty.

    ``views`` is ``None`` (the whole frame, as the run always was) or a
    tuple of ``region.Region``: processed frame ``n`` is seen through
    ``views[n % len(views)]``, one entry for a fixed region and the schedule
    for a scan. ``scan_detectors`` (name to model, the run's detector among
    them) all look at each frame's one prepared image; the record's own
    fields stay the run's detector's.
    """

    def __init__(self, slot, detector, threshold, max_age_ms=MAX_DISPLAY_AGE_MS, *, clock=None, target_label=None,
                 views=None, scan_detectors=None, detector_name=None):
        super().__init__(name="camera-recognize-processor", daemon=True)
        self.slot, self.detector, self.threshold, self.max_age_ms = slot, detector, threshold, max_age_ms
        self.clock, self.target_label = clock, target_label
        self.views, self.scan_detectors, self.detector_name = views, scan_detectors, detector_name
        self.timings = {name: TimingSample() for name in TIMINGS}
        self.records = []
        self.labels = set()
        self.stale = 0
        self.error = None

    def run(self):
        try:
            while True:
                frame = self.slot.take(0.05)
                if frame is None:
                    if self.slot.closed:
                        frame = self.slot.take(0)  # A frame put between the last take and the close.
                        if frame is None:
                            return
                    else:
                        continue
                self.process(frame)
        except Exception as error:
            self.error = str(error)[:1024]

    def process(self, frame):
        started = time.monotonic()
        input_age_ms = (time.monotonic_ns() - frame.observed_ns) / 1e6
        if input_age_ms > self.max_age_ms:
            self.stale += 1
            return
        view = None
        if self.views is None:
            image = to_bgr(frame)
            result = require_result(self.detector.detect(image, self.threshold))
        else:
            index = len(self.records) % len(self.views)
            image, mapping = regions.prepare(frame, self.views[index])
            result = mapping.result(require_result(self.detector.detect(image, self.threshold)))
            also = None
            if self.scan_detectors is not None:
                also = {name: result if name == self.detector_name else
                        mapping.result(require_result(model.detect(image, self.threshold)))
                        for name, model in self.scan_detectors.items()}
            view = {"index": index, "region": self.views[index], "mapping": mapping, "also": also}
        processing_ms = (time.monotonic() - started) * 1000
        for name, value in (("input_age_ms", input_age_ms), ("processing_ms", processing_ms),
                            ("inference_ms", result.inference_ms)):
            self.timings[name].add(value)
        self.labels.update(result.labels)
        self.records.append(frame_record(frame, result, image, input_age_ms, processing_ms, clock=self.clock,
                                         target_label=self.target_label, threshold=self.threshold, view=view))

    def report(self):
        return {name: sample.report() for name, sample in self.timings.items()}


def recognize(binary, limits, cameras, *, threshold=POC_THRESHOLD, detector=DEFAULT_DETECTOR, detector_factory=None,
              extra_args=(), stop=None, timeout=None, confirmed=False, cues=(), mark_labels=(), key_fd=None,
              target_label=None, terminal=None, confirmation=OPERATOR_TERMINAL, on_session=None, on_ready=None,
              late_frame_limit_ms=marks.LATE_FRAME_LIMIT_MS, removal_end_limit_ms=marks.REMOVAL_END_LIMIT_MS,
              region=None, scan_detectors=None, scan_detector_factory=None):
    """One bounded run: load the detector, export frames through the helper, detect on the latest; return the report.

    ``detector`` names a ``DETECTORS`` entry; an injected ``detector_factory``
    replaces it and is not described in the report. ``limits`` is ``{fps,
    max_seconds, max_frames}``. The status is ``recognized`` (exit 0 with
    cleanup confirmed) when at least one frame was processed and the helper
    ended on its bound; else the source's status (``no_frames``, ``refused``,
    ``helper_ended_early``, ``cancelled``, ``timed_out``, ``stopped``,
    ``error``), or ``cleanup_failed``. ``confirmed`` is the terminal
    confirmation's result (``confirm_on_terminal``), passed to the helper as
    ``--parent-confirmed``; the report's ``confirmation`` names it.

    ``cues`` are ``(offset_ms, label)`` and ``mark_labels`` the key-mark
    labels, both from ``marks.VOCABULARY`` (anything else is a ``ValueError``
    before anything is loaded); ``key_fd`` is the terminal descriptor Enter
    presses are read from during the export, or ``None`` for no key marks;
    ``terminal`` is where a due cue is announced (stderr by default).
    ``target_label`` needs a single camera (else ``ValueError``: the cell and
    coverage tallies never pool two cameras) and must be one of the loaded
    detector's classes, else the run ends ``error``/``target_label_unknown``
    before the helper is launched; the report names it only once validated.

    For a caller that guides the run from a window (``camera_guide``; this
    command passes none of these): ``confirmation`` is the name the report
    records for a confirmed start (``operator_window`` for a click);
    ``on_session(session)`` receives the run's ``marks.MarkSession`` before
    the helper is launched, so the caller can record marks from code
    (``session.mark``); ``on_ready()`` is called, without the header, when
    the helper's ``ready`` is accepted; the two limits are
    ``marks.summarize``'s, recorded in each removal entry.

    ``region`` is a ``region.Region`` (item L14): every frame is seen through
    it and the boxes are mapped back to the whole frame; ``None``, or the
    whole frame unturned, is the run as it was, with nothing added to the
    report. ``scan_detectors`` names the detectors of a scan (``detector``
    among them; a target label is required): frame ``n`` is seen through
    entry ``n`` of ``region.scan_schedule()``, every scan detector looks at
    it, and the report gains ``scan`` (``region.scan_summary``). A region
    and a scan together, or anything malformed, is a ``ValueError`` before
    anything is loaded. ``scan_detector_factory(name)`` replaces
    ``create_detector`` for the scan's models (the checks' stand-ins), the
    run's own included unless a ``detector_factory`` gives that one.
    """
    cues, mark_labels = marks.check_cues(cues), marks.check_mark_labels(mark_labels)
    if target_label is not None and len(tuple(cameras)) != 1:
        raise ValueError("a target label needs a single camera")
    if region is not None and not isinstance(region, regions.Region):
        raise ValueError("region must be a Region")
    views = schedule = None
    if scan_detectors is not None:
        scan_detectors = tuple(scan_detectors)
        if region is not None:
            raise ValueError("a scan chooses its own regions")
        if target_label is None:
            raise ValueError("a scan needs a target label")
        if (not scan_detectors or len(set(scan_detectors)) != len(scan_detectors) or detector not in scan_detectors
                or (scan_detector_factory is None and any(name not in DETECTORS for name in scan_detectors))):
            raise ValueError("scan detectors are distinct registered detectors, the run's detector among them")
        views = schedule = regions.scan_schedule()
    elif region is not None and not region.whole:
        views = (region,)
    clock = marks.Clock()
    report = new_report("recognize")
    report.update(timebase=clock.report(), run_started=clock.stamp(clock.monotonic_ns), target_label=None)
    report["confirmation"] = confirmation if confirmed else None
    report.update(seconds_requested=limits.get("max_seconds"), max_frames_requested=limits.get("max_frames"),
                  fps_requested=limits.get("fps"), cameras_requested=list(cameras), threshold=threshold,
                  detector_requested=detector if detector_factory is None else None, phase="loading_detector")
    injected = detector_factory is not None or (schedule is not None and scan_detector_factory is not None)
    try:
        model = (detector_factory() if detector_factory is not None else
                 scan_detector_factory(detector) if injected else create_detector(detector))
    except Exception as error:
        report.update(status="error", reason="detector_unavailable", error=str(error)[:1024], phase="closed")
        return report
    if not injected:
        report["detector"] = describe(model)
    if target_label is not None and target_label not in model.classes:
        report.update(status="error", reason="target_label_unknown", phase="closed")
        return report
    scan_models = None
    if schedule is not None:
        try:
            scan_models = {name: model if name == detector else
                           (create_detector if scan_detector_factory is None else scan_detector_factory)(name)
                           for name in scan_detectors}
        except Exception as error:
            report.update(status="error", reason="detector_unavailable", error=str(error)[:1024], phase="closed")
            return report
        if any(target_label not in other.classes for other in scan_models.values()):
            report.update(status="error", reason="target_label_unknown", phase="closed")
            return report
    report["target_label"] = target_label
    if views is not None:
        report["view"] = {"mode": "region" if schedule is None else "scan", "box_coordinate_space": regions.FRAME_PIXELS,
                          "processing_side": regions.MAX_PROCESSING_SIDE,
                          "detector": None if detector_factory is not None else detector,  # As detector_requested.
                          "orientation_statement": regions.ORIENTATION_STATEMENT}
        if schedule is None:
            report["view"].update(region.report())
        else:
            report["view"].update(scan_detectors=list(scan_detectors), schedule_length=len(schedule))
            if scan_detector_factory is None:
                report["view"]["detectors"] = {name: describe(other) for name, other in scan_models.items()
                                               if name != detector or not injected}
    slot = LatestSlot()
    processor = Processor(slot, model, threshold, clock=clock, target_label=target_label, views=views,
                          scan_detectors=scan_models, detector_name=detector)
    session = marks.MarkSession(clock, cues, mark_labels, key_fd, sys.stderr if terminal is None else terminal)

    def on_frame(frame):
        session.first_frame(frame.observed_ns)
        slot.put(frame)
    if on_session is not None:
        on_session(session)
    processor.start()
    report["phase"] = "exporting"
    started = time.monotonic()
    try:
        session.start()
        captured = camera_helper.run_export(binary, limits, cameras, on_frame=on_frame, stop=stop, extra_args=extra_args,
                                            timeout=timeout, confirmed=confirmed,
                                            on_ready=None if on_ready is None else lambda _header: on_ready())
    finally:
        returned_ns = time.monotonic_ns()
        session.stop()
        slot.close()
        processor.join(PROCESSOR_JOIN_SECONDS)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if schedule is not None:
        report["scan"] = regions.scan_summary(processor.records, schedule, scan_detectors)
    return finish(report, captured, processor, slot, clock=clock, session=session, returned_ns=returned_ns,
                  late_frame_limit_ms=late_frame_limit_ms, removal_end_limit_ms=removal_end_limit_ms)


def measurements(captured, processor, clock, session, returned_ns, *, late_frame_limit_ms=marks.LATE_FRAME_LIMIT_MS,
                 removal_end_limit_ms=marks.REMOVAL_END_LIMIT_MS):
    """The report's L15 fields: the run's times, the marks and the counts-only summaries (``marks.summarize``)."""
    stream = captured.get("stream") or {}
    end_ns = stream.get("end_received_monotonic_ns")
    helper_end = None
    if end_ns is not None and stream.get("end") is not None:
        helper_end = dict(clock.stamp(end_ns), reason=stream["end"]["reason"])
    recorded = session.marks()
    with_target = processor.target_label is not None
    fields = {"first_frame": None if session.origin_ns is None else clock.stamp(session.origin_ns),
              "helper_end": helper_end, "export_returned": clock.stamp(returned_ns), "marks": session.report(),
              "summary": marks.summarize(processor.records, recorded, origin_ns=session.origin_ns,
                                         run_end_ns=returned_ns if end_ns is None else end_ns, helper_end=helper_end,
                                         with_target=with_target, late_frame_limit_ms=late_frame_limit_ms,
                                         removal_end_limit_ms=removal_end_limit_ms)}
    if with_target:
        fields["target"] = {"label": processor.target_label, "threshold": processor.threshold,
                            "frames_with_target": sum(1 for record in processor.records if record["target"]["present"]),
                            "cell_thresholds": list(marks.CELL_THRESHOLDS), "cell_frame": "image_as_exported",
                            "orientation_statement": marks.ORIENTATION_STATEMENT}
    return fields


def finish(report, captured, processor, slot, *, clock=None, session=None, returned_ns=None,
           late_frame_limit_ms=marks.LATE_FRAME_LIMIT_MS, removal_end_limit_ms=marks.REMOVAL_END_LIMIT_MS):
    """Fold the source report and the processor's results into the command's report; set status and exit code."""
    timings = processor.report()
    if clock is not None and session is not None:
        report.update(measurements(captured, processor, clock, session, returned_ns,
                                   late_frame_limit_ms=late_frame_limit_ms, removal_end_limit_ms=removal_end_limit_ms))
        report["timebase"] = clock.report()
    report.update(capture=captured, frames_received=captured["frames_read"], frames_processed=len(processor.records),
                  stale_inputs_skipped=processor.stale, pending_frames_replaced=slot.replaced,
                  labels_seen=sorted(processor.labels), detections=processor.records,
                  last_result=processor.records[-1] if processor.records else None, phase="closed", **timings)
    p95 = timings["inference_ms"]["p95"]
    report["inference_p95_within_budget"] = None if p95 is None else p95 <= LIVE_INFERENCE_P95_BUDGET_MS
    report["cleanup_ok"] = bool(captured["cleanup_ok"]) and not processor.is_alive()
    if processor.is_alive():
        report.update(status="error", reason="processor_did_not_finish")
    elif processor.error is not None:
        report.update(status="error", reason="processing_failed", error=processor.error)
    elif captured["status"] == "exported":
        report["status"] = "recognized" if processor.records else "no_frames"
        if not processor.records:
            report["reason"] = "all_exported_frames_stale" if processor.stale else "no_frame_processed"
    else:
        report["status"] = captured["status"]
        for name in ("reason", "error", "signal"):
            if name in captured:
                report[name] = captured[name]
    if report["status"] == "recognized" and not report["cleanup_ok"]:
        report["status"] = "cleanup_failed"
    report["exit_code"] = (0 if report["status"] == "recognized" else
                           128 + report["signal"] if report["status"] == "cancelled" and "signal" in report else 1)
    return report


def validate(limits=fake_export.LIMITS):
    """Run the reader over the fake producer's valid stream and every invalid one, in process; return the report."""
    import io
    report = new_report("validate")
    reader = ExportReader(io.BytesIO(fake_export.valid_stream(limits=limits)), limits=limits)
    try:
        frames = [(frame.source, frame.sequence, frame.width, frame.height) for frame in reader.frames()]
        valid = dict(reader.report(), frames=len(frames), ok=reader.report()["status"] == "complete" and
                     len(frames) == limits["max_frames"])
    except ExportRefused as error:
        valid = dict(reader.report(), frames=0, ok=False, refused=error.reason)
    for key in ("ready", "end"):
        valid.pop(key, None)
    reader = ExportReader(io.BytesIO(fake_export.undecided_stream()), limits=limits)
    try:
        undecided_frames = len(list(reader.frames()))
        undecided = dict(reader.report(), frames=undecided_frames, ok=reader.report()["status"] == "complete" and
                         undecided_frames == 0)
    except ExportRefused as error:
        undecided = dict(reader.report(), frames=0, ok=False, refused=error.reason)
    for key in ("ready", "end"):
        undecided.pop(key, None)
    invalid = {}
    for name, (build, expected) in fake_export.INVALID.items():
        reader = ExportReader(io.BytesIO(build()), limits=limits)
        try:
            for _ in reader.frames():
                pass
            observed = None
        except ExportRefused as error:
            observed = error.reason
        invalid[name] = {"expected": expected, "observed": observed, "ok": observed == expected}
    failures = sorted(name for name, result in invalid.items() if not result["ok"])
    report.update(valid_stream=valid, undecided_stream=undecided, invalid_streams=invalid,
                  invalid_stream_count=len(invalid), invalid_streams_failed=failures, frames_received=valid["frames"],
                  frames_processed=0,
                  status="validated" if valid["ok"] and undecided["ok"] and not failures else "validation_failed")
    report["exit_code"] = 0 if report["status"] == "validated" else 1
    return report


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


def parse_cameras(text):
    try:
        return camera_helper.check_cameras(tuple(int(item) for item in text.split(",")))
    except ValueError:
        raise argparse.ArgumentTypeError("--cameras takes 0, 1 or 0,1") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-camera-input", action="store_true",
                        help="Opt in to launching the camera helper and running the detectors on its frames (Camera Data)")
    parser.add_argument("--validate", action="store_true",
                        help="Run the export reader against the fake producer's streams instead; no helper, no detector")
    parser.add_argument("--helper", type=Path, help="The sightcircuit-camera-helper binary, given explicitly; never searched")
    parser.add_argument("--seconds", type=int, choices=range(1, MAX_SECONDS + 1), metavar=f"1..{MAX_SECONDS}",
                        help="Time bound the helper enforces and the command checks")
    parser.add_argument("--max-frames", type=int, choices=range(1, MAX_FRAMES + 1), metavar=f"1..{MAX_FRAMES}",
                        help="Frame bound the helper enforces and the command checks")
    parser.add_argument("--fps", type=int, choices=range(1, MAX_FPS + 1), default=DEFAULT_FPS, metavar=f"1..{MAX_FPS}")
    parser.add_argument("--cameras", type=parse_cameras, default=(0, 1), help="Camera indices to export: 0, 1 or 0,1")
    parser.add_argument("--threshold", type=float, default=POC_THRESHOLD)
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR,
                        help="Model; mobilenet-ssd is the device-validated default, yolox-nano an 80-class candidate")
    parser.add_argument("--helper-arg", action="append", default=[], metavar="ARG",
                        help="An extra argument for the helper after the contract ones (a fake build's --fake-scenario)")
    parser.add_argument("--report", type=Path, help="New .json file for the report; saving it is your action (Camera Data)")
    parser.add_argument("--cue", action="append", default=[], metavar="SECONDS:LABEL",
                        help="Print LABEL with a bell SECONDS after the first frame and record the mark; repeatable, "
                             f"rising times; labels: {', '.join(marks.VOCABULARY)}")
    parser.add_argument("--key-marks", action="store_true",
                        help=f"Record a mark ({marks.KEY_LABEL}) for each Enter on the terminal during the run; "
                             "without this or --mark-labels the terminal is not read after the confirmation")
    parser.add_argument("--mark-labels", default="", metavar="LABEL,LABEL",
                        help="Labels, from the same list, that successive Enter presses during the run record; "
                             f"further presses record {marks.KEY_LABEL}; implies --key-marks")
    parser.add_argument("--region", metavar="X0,Y0,X1,Y1",
                        help="Look only at this rectangle of the frame, given as fractions (origin top left), cropped "
                             "before the resize; boxes are reported over the whole frame. Default: the whole frame")
    parser.add_argument("--rotation", type=int, choices=regions.ROTATIONS, default=0,
                        help="Turn what the detector sees by this many degrees clockwise; boxes are mapped back")
    parser.add_argument("--target-label", metavar="CLASS",
                        help="A class of the chosen detector to summarize: windows, coverage, cells (counts only); "
                             "needs a single camera")
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if args.validate:
        if (args.allow_camera_input or args.helper or args.seconds or args.max_frames or args.cue or args.mark_labels or
                args.key_marks or args.target_label or args.region or args.rotation):
            parser.error("--validate takes no run arguments")
        report = validate()
    else:
        if not args.allow_camera_input:
            parser.error("--allow-camera-input is required to launch the helper (or use --validate)")
        if args.helper is None or args.seconds is None or args.max_frames is None:
            parser.error("--helper, --seconds and --max-frames are required")
        limits = {"fps": args.fps, "max_seconds": args.seconds, "max_frames": args.max_frames}
        try:
            cues = marks.check_cues(marks.parse_cue(text, args.seconds) for text in args.cue)
            mark_labels = marks.check_mark_labels(item for item in args.mark_labels.split(",") if args.mark_labels)
        except ValueError as error:
            parser.error(str(error))
        if args.target_label is not None and args.target_label not in class_names(args.detector):
            parser.error(f"--target-label must be a class of {args.detector}: {', '.join(class_names(args.detector))}")
        if args.target_label is not None and len(args.cameras) != 1:
            parser.error("--target-label needs a single camera (--cameras 0 or 1), so no tally pools the two")
        try:  # Refused here, before the confirmation and so before the camera is touched.
            region = regions.Region(*(regions.WHOLE if args.region is None else regions.parse_region(args.region)),
                                    rotation=args.rotation)
        except ValueError as error:
            parser.error(f"--region: {error}")
        report = new_report("recognize")
        report.update(confirmation=None, seconds_requested=args.seconds, max_frames_requested=args.max_frames,
                      fps_requested=args.fps, cameras_requested=list(args.cameras))
        with guard.sigterm_cancels():
            try:
                refusal = confirm_on_terminal(limits, args.cameras)
                if refusal is not None:  # Nothing is launched and no model is loaded without the typed answer.
                    report.update(status="error", reason=refusal, phase="closed")
                else:
                    report = recognize(args.helper, limits, args.cameras, threshold=args.threshold,
                                       detector=args.detector, extra_args=args.helper_arg, confirmed=True,
                                       cues=cues, mark_labels=mark_labels,
                                       key_fd=marks.terminal_fd() if args.key_marks or mark_labels else None,
                                       target_label=args.target_label, region=None if region.whole else region)
            except guard.CANCELLATION as error:
                guard.cancelled(report, error)
    if args.report is not None:  # The operator's explicit action to save Camera Data; the file names itself.
        report["camera_data"].update(report_saved_by_operator=True, report_path=str(Path(args.report).expanduser()))
        try:
            write_report(args.report, report)
        except (OSError, ValueError) as error:
            report["camera_data"].update(report_saved_by_operator=False, report_path=None)
            report.update(report_error=str(error), exit_code=1)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
