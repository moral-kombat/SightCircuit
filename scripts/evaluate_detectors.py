"""Compare detectors on labelled stills, with the application's own conversion path."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framejacker import assets, evaluation
from framejacker.convert import to_bgr
from framejacker.detection import DETECTORS, create_detector, describe
from framejacker.frame import Frame

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / ".cache/framejacker/evaluation"
DEFAULT_MANIFEST = ROOT / "evaluation/seed.json"


def load_images(manifests, fetch):
    """Return [(manifest path, entry, BGR image)] after optional download and checksum checks."""
    import cv2

    images = []
    for path in manifests:
        manifest = evaluation.load_manifest(path)
        remote = [entry for entry in manifest["images"] if "file" in entry]
        if fetch and remote:
            assets.fetch([{"name": entry["file"], "url": entry["url"], "sha256": entry["sha256"]}
                          for entry in remote], DOWNLOADS, log=lambda message: print(message, file=sys.stderr))
        for entry in manifest["images"]:
            location = evaluation.resolve_image(manifest, entry, DOWNLOADS)
            if not location.is_file():
                hint = " (run with --fetch)" if "file" in entry else ""
                raise FileNotFoundError(f"{entry['id']}: image not found at {location}{hint}")
            if entry.get("sha256") and assets.checksum(location) != entry["sha256"]:
                raise ValueError(f"{entry['id']}: image failed SHA-256 verification")
            image = cv2.imread(str(location), cv2.IMREAD_COLOR)  # Grayscale stills become three equal channels.
            if image is None:
                raise ValueError(f"{entry['id']}: image could not be decoded")
            images.append((str(path), entry, image))
    return images


def evaluate(detector, images, threshold, iou_threshold):
    scores, inference, processing = [], [], []
    for number, (manifest, entry, image) in enumerate(images, 1):
        height, width = image.shape[:2]
        started = time.monotonic()
        bgr = to_bgr(Frame(image, "BGR24", width, height, image.strides[0], manifest, number, time.monotonic_ns()))
        result = detector.detect(bgr, threshold)
        processing.append((time.monotonic() - started) * 1000)
        inference.append(result.inference_ms)
        score = evaluation.score_image(entry, result.detections, detector.classes, bgr.shape[1] / width,
                                       iou_threshold)
        score.update(manifest=manifest, inference_ms=round(result.inference_ms, 3),
                     detections=[item.as_dict() for item in result.detections[:50]])
        scores.append(score)
    summary = evaluation.totals(scores)
    summary.update(mean_inference_ms=round(sum(inference) / len(inference), 3),
                   maximum_inference_ms=round(max(inference), 3),
                   mean_processing_ms=round(sum(processing) / len(processing), 3))
    return summary, scores


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append",
                        help="Evaluation manifest; repeatable. Default: evaluation/seed.json")
    parser.add_argument("--detector", choices=DETECTORS, action="append", help="Repeatable; default: all")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--iou", type=float, default=0.5, help="Box match threshold for annotated objects")
    parser.add_argument("--fetch", action="store_true", help="Download missing checksum-pinned images")
    parser.add_argument("--report", type=Path, help="Explicitly save the JSON report to a new file")
    args = parser.parse_args(argv)
    for name, value in (("--threshold", args.threshold), ("--iou", args.iou)):
        if not math.isfinite(value) or not 0 < value <= 1:
            parser.error(f"{name} must be in (0, 1]")
    if args.report and args.report.exists():
        parser.error("--report must name a new file")
    report = {"report_kind": "detector_evaluation", "schema_version": 1, "status": "error",
              "threshold": args.threshold, "iou_threshold": args.iou,
              "confusion_iou_threshold": evaluation.CONFUSION_IOU, "detectors": {},
              "confidence_is_calibrated_probability": False,
              "scope": "Stills through the application's conversion path; not live latency, color or headset accuracy"}
    try:
        manifests = args.manifest or [DEFAULT_MANIFEST]
        report["manifests"] = [str(path) for path in manifests]
        images = load_images(manifests, args.fetch)
        report["images"] = len(images)
        for name in args.detector or list(DETECTORS):
            started = time.monotonic()
            detector = create_detector(name)
            startup = (time.monotonic() - started) * 1000
            detector.detect(to_bgr(Frame(images[0][2], "BGR24", images[0][2].shape[1], images[0][2].shape[0],
                                         images[0][2].strides[0], "warmup", 0, time.monotonic_ns())),
                            args.threshold)  # Exclude first-run allocation from timing.
            summary, scores = evaluate(detector, images, args.threshold, args.iou)
            summary["startup_ms"] = round(startup, 3)
            report["detectors"][name] = {"detector": describe(detector), "totals": summary, "images": scores}
            print(f"{name:14s} present {summary['present_hits']}/{summary['present_hits'] + summary['present_misses']}"
                  f"  absent-violations {summary['absent_violations']}  confusions {summary['confusions']}"
                  f"  box-recall {summary['box_recall']}  mean {summary['mean_inference_ms']:.1f} ms",
                  file=sys.stderr)
        report["status"] = "evaluated"
    except (OSError, ValueError, RuntimeError) as error:
        report["error"] = str(error)
    print(json.dumps(report, indent=2))
    if args.report:
        try:
            with args.report.open("x") as output:
                output.write(json.dumps(report, indent=2) + "\n")
        except OSError as error:
            print(f"Could not save report: {error}", file=sys.stderr)
            return 1
    return 0 if report["status"] == "evaluated" else 1


if __name__ == "__main__":
    sys.exit(main())
