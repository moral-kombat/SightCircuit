"""Bounded CPU recognition from frames the Arcturus camera helper exports, without display.

The fourteenth command. ``sources.camera_helper.run_export`` launches the
``sightcircuit-camera-helper`` binary the operator names (``--helper``) in
``--export-frames`` mode under the supervision primitive, with the same
limits the command enforces (``--seconds`` at most 30 and ``--max-frames`` at
most 300, both required; ``--fps`` at most 10), and reads its camera export v1
stream with the strict reader. Each validated frame is kept in a one-frame
latest slot; the processing thread converts it to the detector's input (at
most 960 pixels), skips it when its acquisition is older than the one-second
input limit, runs the chosen detector and records labels, scores, boxes and
the inference timing. The report is shaped as the mirror recognizer's where
the two agree: run limits, per-frame detections, timing statistics with the
p95 against ``LIVE_INFERENCE_P95_BUDGET_MS``, counts and ``gates.claims()``.

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
from sightcircuit.camera import contract, fake_export
from sightcircuit.camera.export_reader import ExportReader, ExportRefused
from sightcircuit.convert import to_bgr
from sightcircuit.detection import (DEFAULT_DETECTOR, DETECTORS, LIVE_INFERENCE_P95_BUDGET_MS, POC_THRESHOLD,
                                    create_detector, describe, require_result)
from sightcircuit.frame import DETECTOR_PIXELS
from sightcircuit.hud.contract import MAX_DISPLAY_AGE_MS
from sightcircuit.latest import LatestSlot
from sightcircuit.pipeline import TimingSample
from sightcircuit.runtime import guard
from sightcircuit.sources import camera_helper

REPORT_KIND = "camera_recognition"
# The command's bounds (work-order item S7), inside the helper's own (``contract.LIMITS``: 60 s, 600 frames).
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
    return {"report_kind": REPORT_KIND, "schema_version": 1, "mode": mode, "status": "not_started", "exit_code": 1,
            "cleanup_ok": True, "images_saved": False, "pixels_in_report": False, "display_started": False,
            "overlay_requested": False, "source_kind": "arcturus_camera_export", "live_input_verified": False,
            "camera_data": {"detections_are_camera_data": True, "statement": CAMERA_DATA_STATEMENT,
                            "report_saved_by_operator": False, "report_path": None},
            "gate_statement": GATE_STATEMENT, **gates.claims(),
            "frames_received": 0, "frames_processed": 0, "stale_inputs_skipped": 0, "pending_frames_replaced": 0,
            "labels_seen": [], "maximum_pending_frames": 1, "maximum_input_observation_age_ms": MAX_DISPLAY_AGE_MS,
            "inference_p95_budget_ms": LIVE_INFERENCE_P95_BUDGET_MS, "inference_p95_within_budget": None,
            "confidence_is_calibrated_probability": False, "scope": SCOPE}


def frame_record(frame, result, image, input_age_ms, processing_ms):
    """One per-frame result: identity, timings, labels and up to ``MAX_LISTED_DETECTIONS`` boxes in detector pixels."""
    return {"sequence": frame.sequence, "source_id": frame.source, "camera_index": frame.metadata.get("camera_index"),
            "frame_id": frame.metadata.get("frame_id"), "observed_ns": frame.observed_ns,
            "input_age_ms": round(input_age_ms, 3), "processing_ms": round(processing_ms, 3),
            "inference_ms": round(result.inference_ms, 3), "labels": result.labels,
            "detections": [item.as_dict() for item in result.detections][:MAX_LISTED_DETECTIONS],
            "detection_count": len(result.detections), "native_size": [frame.width, frame.height],
            "detector_input_size": [image.shape[1], image.shape[0]], "box_coordinate_space": DETECTOR_PIXELS}


class Processor(threading.Thread):
    """Takes the latest frame from the slot and runs the detector; stops once the slot is closed and empty."""

    def __init__(self, slot, detector, threshold, max_age_ms=MAX_DISPLAY_AGE_MS):
        super().__init__(name="camera-recognize-processor", daemon=True)
        self.slot, self.detector, self.threshold, self.max_age_ms = slot, detector, threshold, max_age_ms
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
        self.records.append(frame_record(frame, result, image, input_age_ms, processing_ms))

    def report(self):
        return {name: sample.report() for name, sample in self.timings.items()}


def recognize(binary, limits, cameras, *, threshold=POC_THRESHOLD, detector=DEFAULT_DETECTOR, detector_factory=None,
              extra_args=(), stop=None, timeout=None):
    """One bounded run: load the detector, export frames through the helper, detect on the latest; return the report.

    ``detector`` names a ``DETECTORS`` entry; an injected ``detector_factory``
    replaces it and is not described in the report. ``limits`` is ``{fps,
    max_seconds, max_frames}``. The status is ``recognized`` (exit 0 with
    cleanup confirmed) when at least one frame was processed and the helper
    ended on its bound; else the source's status (``no_frames``, ``refused``,
    ``helper_ended_early``, ``cancelled``, ``timed_out``, ``stopped``,
    ``error``), or ``cleanup_failed``.
    """
    report = new_report("recognize")
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
    slot = LatestSlot()
    processor = Processor(slot, model, threshold)
    processor.start()
    report["phase"] = "exporting"
    started = time.monotonic()
    try:
        captured = camera_helper.run_export(binary, limits, cameras, on_frame=slot.put, stop=stop, extra_args=extra_args,
                                            timeout=timeout)
    finally:
        slot.close()
        processor.join(PROCESSOR_JOIN_SECONDS)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return finish(report, captured, processor, slot)


def finish(report, captured, processor, slot):
    """Fold the source report and the processor's results into the command's report; set status and exit code."""
    timings = processor.report()
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
    report.update(valid_stream=valid, invalid_streams=invalid, invalid_stream_count=len(invalid),
                  invalid_streams_failed=failures, frames_received=valid["frames"], frames_processed=0,
                  status="validated" if valid["ok"] and not failures else "validation_failed")
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
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if args.validate:
        if args.allow_camera_input or args.helper or args.seconds or args.max_frames:
            parser.error("--validate takes no run arguments")
        report = validate()
    else:
        if not args.allow_camera_input:
            parser.error("--allow-camera-input is required to launch the helper (or use --validate)")
        if args.helper is None or args.seconds is None or args.max_frames is None:
            parser.error("--helper, --seconds and --max-frames are required")
        limits = {"fps": args.fps, "max_seconds": args.seconds, "max_frames": args.max_frames}
        report = new_report("recognize")
        with guard.sigterm_cancels():
            try:
                report = recognize(args.helper, limits, args.cameras, threshold=args.threshold, detector=args.detector,
                                   extra_args=args.helper_arg)
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
