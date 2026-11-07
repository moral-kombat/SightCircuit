"""Bounded CPU recognition from a verified SteamVR mirror, without live display.

The worker runs ``sightcircuit.runtime.live.run_recognition``: the shared
``Pipeline`` over ``MirrorSource``, with latest input only, a one-second
input/result age limit and capture-context admission. ``sources.mirror.launch``
bounds it and publishes ``--preview`` and ``--still``, both taken from the last
processed result.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.convert import native_bgr
from sightcircuit.detection import DEFAULT_DETECTOR, DETECTORS, POC_THRESHOLD
from sightcircuit.runtime import output, process
from sightcircuit.runtime.live import new_mirror_report as new_report, run_recognition
from sightcircuit.sources.mirror import launch
from sightcircuit.sources.mirror_device import discover

STILL_SCOPE = ("Unannotated native mirror frame of last_result (not resized or masked); "
               "evaluate_detectors.py applies the same conversion to at most 960 pixels")
OUTPUT_PREFIXES = {"preview": ".sightcircuit-recognition-", "still": ".sightcircuit-still-"}


def encode_png(image):
    import cv2

    okay, png = cv2.imencode(".png", image)
    if not okay:
        raise RuntimeError("PNG encoding failed")
    return png.tobytes()


def write_preview(reservation, image):
    """Write the annotated image, at most 640 pixels on its longer side; return report fields."""
    import cv2

    ratio = min(1.0, 640 / max(image.shape[:2]))
    if ratio < 1:
        image = cv2.resize(image, (max(1, round(image.shape[1] * ratio)),
                                   max(1, round(image.shape[0] * ratio))), interpolation=cv2.INTER_AREA)
    output.write(reservation, encode_png(image))
    return {"preview_ready": True, "preview_width": image.shape[1], "preview_height": image.shape[0]}


def write_still(reservation, processed, detector):
    """Write the unannotated native frame of ``processed``; return its report fields, with the PNG's SHA-256."""
    frame = processed.frame
    image = native_bgr(frame)
    png = encode_png(image)
    output.write(reservation, png)
    return {"still_ready": True, "still_width": image.shape[1], "still_height": image.shape[0],
            "still_sha256": hashlib.sha256(png).hexdigest(), "still_detector": detector,
            "still_source": frame.source, "still_sequence": frame.sequence, "still_scope": STILL_SCOPE}


def _worker(job, seconds, outputs, sender, stop, expected_parent, *, backend_factory=None, detector_factory=None):
    """``outputs`` maps "preview"/"still" to reservations; files are written after the source closes."""
    def send(report, event="progress"):
        process.send_report(sender, report, event)

    outputs = outputs or {}
    report = new_report()
    try:
        if not process.enter_worker(expected_parent):
            return
        report, last = run_recognition(job["candidate"], seconds, stop, send, max_fps=job["max_fps"],
                                       threshold=job["threshold"], detector=job["detector"],
                                       backend_factory=backend_factory, detector_factory=detector_factory)
        if last is not None and "preview" in outputs:
            report.update(write_preview(outputs["preview"], last.image))
        if last is not None and "still" in outputs:
            report.update(write_still(outputs["still"], last, report.get("detector", {}).get("name")))
        send(report, "result")
    except Exception as error:
        report.update(status="error", reason="worker_failed", error=str(error))
        try:
            send(report, "result")
        except (OSError, BrokenPipeError):
            pass
    finally:
        sender.close()


def resolved(path):
    return os.path.realpath(path.expanduser())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="Opt in to bounded mirror capture/inference; default only discovers metadata")
    parser.add_argument("--device", help="Verified SteamVR virtual node; no physical fallback")
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=10, metavar="1..10")
    parser.add_argument("--max-fps", type=int, choices=range(1, 6), default=5, metavar="1..5")
    parser.add_argument("--threshold", type=float, default=POC_THRESHOLD)
    parser.add_argument("--detector", choices=DETECTORS, default=DEFAULT_DETECTOR,
                        help="Model; mobilenet-ssd is the device-validated default, yolox-nano an 80-class candidate")
    parser.add_argument("--preview", type=Path, help="Explicit new annotated PNG, published only after capture/inference stops")
    parser.add_argument("--still", type=Path,
                        help="Explicit new unannotated PNG of the last processed frame at native size, "
                             "for evaluation sets; published like --preview")
    args = parser.parse_args(argv)
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between zero and one")
    if not args.capture and (args.device or args.preview or args.still):
        parser.error("--device, --preview and --still require --capture")
    # Resolved, so two spellings of one file (``..``, a symlinked directory) are refused here.
    if args.preview and args.still and resolved(args.preview) == resolved(args.still):
        parser.error("--preview and --still need distinct paths")
    discovery = discover()
    report = dict(new_report(), status="discovery_only", discovery=discovery, exit_code=0)
    if args.capture:
        job = {"max_fps": args.max_fps, "threshold": args.threshold, "detector": args.detector}
        outputs = {kind: (path, OUTPUT_PREFIXES[kind]) for kind, path in (("preview", args.preview), ("still", args.still))
                   if path}
        report = launch(report, discovery, args.device, args.seconds, worker=_worker, report_factory=new_report,
                        success_status="recognized", timeout=args.seconds + 15, outputs=outputs, digests=("still",),
                        job=lambda candidate: dict(job, candidate=candidate),
                        fields={"detector_requested": args.detector})
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
