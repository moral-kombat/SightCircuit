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
it on the terminal with a bell when due; Enter on the terminal during the run
records one with the next ``--mark-labels`` label, or ``mark``. What is typed
is counted for line ends and discarded. ``--target-label`` names a detector
class; the report then carries, as counts only, the 1-second windows that
held it, the coverage over the in-view spans, the 3x3 cell of its best box's
centre per mark interval and what followed a ``remove`` mark. The cell is
taken on the image as exported: nothing here knows the camera's mounting
orientation. Without these options and without a key press the run is what it
was.

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
CONFIRMATION_ANSWER = "yes"
OPERATOR_TERMINAL = "operator_terminal"  # The report's ``confirmation`` once the operator typed the answer here.


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


def frame_record(frame, result, image, input_age_ms, processing_ms, *, clock=None, target_label=None, threshold=0.0):
    """One per-frame result: identity, timings, labels and up to ``MAX_LISTED_DETECTIONS`` boxes in detector pixels.

    With a ``clock`` the acquire time also appears as UTC (``observed_utc_ns``,
    derived) beside the helper's own realtime stamp; with a ``target_label``
    the record carries ``target``, the best box of that label and its cell.
    """
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
    if target_label is not None:
        record["target"] = marks.target_of(result.detections, target_label, threshold, image.shape[1], image.shape[0])
    return record


class Processor(threading.Thread):
    """Takes the latest frame from the slot and runs the detector; stops once the slot is closed and empty."""

    def __init__(self, slot, detector, threshold, max_age_ms=MAX_DISPLAY_AGE_MS, *, clock=None, target_label=None):
        super().__init__(name="camera-recognize-processor", daemon=True)
        self.slot, self.detector, self.threshold, self.max_age_ms = slot, detector, threshold, max_age_ms
        self.clock, self.target_label = clock, target_label
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
        image = to_bgr(frame)
        result = require_result(self.detector.detect(image, self.threshold))
        processing_ms = (time.monotonic() - started) * 1000
        for name, value in (("input_age_ms", input_age_ms), ("processing_ms", processing_ms),
                            ("inference_ms", result.inference_ms)):
            self.timings[name].add(value)
        self.labels.update(result.labels)
        self.records.append(frame_record(frame, result, image, input_age_ms, processing_ms, clock=self.clock,
                                         target_label=self.target_label, threshold=self.threshold))

    def report(self):
        return {name: sample.report() for name, sample in self.timings.items()}


def confirm_on_terminal(limits, cameras, *, stdin=None, output=None):
    """The informed start (owner decision 2026-11-09): print the helper's export notice and read the typed answer.

    The notice (``contract.EXPORT_NOTICE``, verbatim) and the run's bounds go
    to ``output`` (stderr, never the report stream); the answer is read from
    ``stdin``, which must be an interactive terminal, and must be exactly
    ``yes``. No flag, variable or piped text stands in for it. Returns
    ``None`` when confirmed, else the named refusal:
    ``confirmation_requires_terminal``, ``confirmation_unavailable`` (end of
    input) or ``confirmation_declined``.
    """
    stdin = sys.stdin if stdin is None else stdin
    output = sys.stderr if output is None else output
    try:
        interactive = stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return "confirmation_requires_terminal"
    output.write(contract.EXPORT_NOTICE + "\n")
    output.write(f"Bounds: {limits['max_frames']} frames, {limits['max_seconds']} seconds, {limits['fps']} frames a "
                 f"second, cameras {','.join(str(index) for index in cameras)}.\nType {CONFIRMATION_ANSWER} to continue: ")
    output.flush()
    answer = stdin.readline()
    if not answer:
        return "confirmation_unavailable"
    return None if answer.rstrip("\r\n \t") == CONFIRMATION_ANSWER else "confirmation_declined"


def recognize(binary, limits, cameras, *, threshold=POC_THRESHOLD, detector=DEFAULT_DETECTOR, detector_factory=None,
              extra_args=(), stop=None, timeout=None, confirmed=False, cues=(), mark_labels=(), key_fd=None,
              target_label=None, terminal=None):
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
    ``target_label`` must be one of the loaded detector's classes, else the
    run ends ``error``/``target_label_unknown`` before the helper is launched.
    """
    cues, mark_labels = marks.check_cues(cues), marks.check_mark_labels(mark_labels)
    clock = marks.Clock()
    report = new_report("recognize")
    report.update(timebase=clock.report(), run_started=clock.stamp(clock.monotonic_ns), target_label=target_label)
    report["confirmation"] = OPERATOR_TERMINAL if confirmed else None
    report.update(seconds_requested=limits.get("max_seconds"), max_frames_requested=limits.get("max_frames"),
                  fps_requested=limits.get("fps"), cameras_requested=list(cameras), threshold=threshold,
                  detector_requested=detector if detector_factory is None else None, phase="loading_detector")
    try:
        model = detector_factory() if detector_factory is not None else create_detector(detector)
    except Exception as error:
        report.update(status="error", reason="detector_unavailable", error=str(error)[:1024], phase="closed")
        return report
    if detector_factory is None:
        report["detector"] = describe(model)
    if target_label is not None and target_label not in model.classes:
        report.update(status="error", reason="target_label_unknown", phase="closed")
        return report
    slot = LatestSlot()
    processor = Processor(slot, model, threshold, clock=clock, target_label=target_label)
    session = marks.MarkSession(clock, cues, mark_labels, key_fd, sys.stderr if terminal is None else terminal)

    def on_frame(frame):
        session.first_frame(frame.observed_ns)
        slot.put(frame)
    processor.start()
    report["phase"] = "exporting"
    started = time.monotonic()
    session.start()
    try:
        captured = camera_helper.run_export(binary, limits, cameras, on_frame=on_frame, stop=stop, extra_args=extra_args,
                                            timeout=timeout, confirmed=confirmed)
    finally:
        returned_ns = time.monotonic_ns()
        session.stop()
        slot.close()
        processor.join(PROCESSOR_JOIN_SECONDS)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return finish(report, captured, processor, slot, clock=clock, session=session, returned_ns=returned_ns)


def measurements(captured, processor, clock, session, returned_ns):
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
                                         with_target=with_target)}
    if with_target:
        fields["target"] = {"label": processor.target_label, "threshold": processor.threshold,
                            "frames_with_target": sum(1 for record in processor.records if record["target"]["present"]),
                            "cell_thresholds": list(marks.CELL_THRESHOLDS), "cell_frame": "image_as_exported",
                            "orientation_statement": marks.ORIENTATION_STATEMENT}
    return fields


def finish(report, captured, processor, slot, *, clock=None, session=None, returned_ns=None):
    """Fold the source report and the processor's results into the command's report; set status and exit code."""
    timings = processor.report()
    if clock is not None and session is not None:
        report.update(measurements(captured, processor, clock, session, returned_ns))
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
    parser.add_argument("--mark-labels", default="", metavar="LABEL,LABEL",
                        help="Labels, from the same list, that successive Enter presses during the run record; "
                             f"further presses record {marks.KEY_LABEL}")
    parser.add_argument("--target-label", metavar="CLASS",
                        help="A class of the chosen detector to summarize: windows, coverage, cells (counts only)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if args.validate:
        if (args.allow_camera_input or args.helper or args.seconds or args.max_frames or args.cue or args.mark_labels or
                args.target_label):
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
                                       cues=cues, mark_labels=mark_labels, key_fd=marks.terminal_fd(),
                                       target_label=args.target_label)
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
