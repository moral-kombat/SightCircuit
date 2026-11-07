"""Compare detectors on labelled stills, with the application's own conversion path.

With ``--expect`` the run also fails (exit 1) when any detector does worse than
a committed count pin, such as ``evaluation/seed-expected.json``.
"""
import argparse
import hashlib
import importlib
import json
import math
import platform
from pathlib import Path
import subprocess
import sys
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit import assets, detection, evaluation
from sightcircuit.convert import MAX_PROCESSING_SIDE, to_bgr
from sightcircuit.detection import DETECTORS, create_detector, describe
from sightcircuit.errors import SightCircuitError
from sightcircuit.frame import Frame
from sightcircuit.sources.mirror_device import grayscale_sample, validate_format

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / ".cache/sightcircuit/evaluation"
DEFAULT_MANIFEST = ROOT / "evaluation/seed.json"
MIRROR_PREVIEW_SIDE = 640  # The bound sources/mirror.capture gives the preview mirror_check publishes.
AUDIT_VARIANTS = {
    "swap-rb": "Swap B/R after decode; all geometry and model preprocessing unchanged",
    "gray-replicated": "Convert decoded BGR to grayscale and replicate it; all geometry unchanged",
    "native-size": "Omit only the application's 960-side resize; model preprocessing unchanged",
    "mirror-preview": ("Replace decoded BGR with mirror_check's preview, sources.mirror_device.grayscale_sample "
                       "at a 640-pixel bound (nearest-neighbour stride, integer luma), replicated to three "
                       "channels: E61's detector input; model preprocessing unchanged"),
}


def load_images(manifests, fetch, require_digests=False):
    """Return manifest entries, decoded BGR images and checksums after optional downloads."""
    import cv2

    images = []
    for path in manifests:
        manifest = evaluation.load_manifest(path)
        remote = [entry for entry in manifest["images"] if "file" in entry]
        if fetch and remote:
            assets.fetch([{"name": entry["file"], "url": entry["url"], "sha256": entry["sha256"]}
                          for entry in remote], DOWNLOADS, log=lambda message: print(message, file=sys.stderr))
        for entry in manifest["images"]:
            if require_digests and not entry.get("sha256"):
                raise ValueError(f"{entry['id']}: --require-input-digests needs an independently supplied manifest SHA-256")
            location = evaluation.resolve_image(manifest, entry, DOWNLOADS)
            if not location.is_file():
                hint = " (run with --fetch)" if "file" in entry else ""
                raise FileNotFoundError(f"{entry['id']}: image not found at {location}{hint}")
            digest = assets.checksum(location)
            if entry.get("sha256") and digest != entry["sha256"]:
                raise ValueError(f"{entry['id']}: image failed SHA-256 verification")
            image = cv2.imread(str(location), cv2.IMREAD_COLOR)  # Grayscale stills become three equal channels.
            if image is None:
                raise ValueError(f"{entry['id']}: image could not be decoded")
            stored = cv2.imread(str(location), cv2.IMREAD_UNCHANGED)
            entry = dict(entry, _input_decode={
                "stored_dtype": str(stored.dtype), "stored_shape": list(stored.shape),
                "stored_channels": 1 if stored.ndim == 2 else stored.shape[2],
                "exif_orientation_policy": "IMREAD_COLOR applies supported EXIF orientation; UNCHANGED does not",
                "crop": "full decoded image; no evaluator crop", "upstream_crop": "unknown"})
            images.append((str(path), entry, image, str(location), digest))
    if not images:
        raise ValueError("No images to evaluate")
    return images


def mirror_preview(image):
    """``grayscale_sample`` of a decoded BGR still, the way the mirror check samples a frame, in three channels."""
    import numpy as np

    pixels = np.ascontiguousarray(image)
    height, width = pixels.shape[:2]
    fmt = validate_format(width, height, "BGR3", width * 3, pixels.nbytes)
    sample_width, sample_height, gray = grayscale_sample(pixels.tobytes(), fmt, MIRROR_PREVIEW_SIDE)
    return np.repeat(np.frombuffer(gray, dtype=np.uint8).reshape(sample_height, sample_width, 1), 3, axis=2)


def prepare_image(image, variant="baseline"):
    """One controlled change from the application's BGR conversion path."""
    import cv2

    if variant == "swap-rb":
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    elif variant == "gray-replicated":
        image = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    elif variant == "mirror-preview":
        image = mirror_preview(image)
    elif variant not in ("baseline", "native-size"):
        raise ValueError(f"Unknown audit variant {variant!r}")
    height, width = image.shape[:2]
    return to_bgr(Frame(image, "BGR24", width, height, image.strides[0], "file-audit", 0,
                        time.monotonic_ns()), max_side=max(width, height) if variant == "native-size"
                  else MAX_PROCESSING_SIDE)


def tensor_fingerprint(tensor):
    """Fingerprint model input without including private pixel payloads in reports."""
    import numpy as np

    return {"shape": list(tensor.shape), "dtype": str(tensor.dtype),
            "sha256": hashlib.sha256(np.ascontiguousarray(tensor).tobytes()).hexdigest(),
            "minimum": float(tensor.min()), "maximum": float(tensor.max())}


def preprocessing_audit(detector, image, bgr, decode):
    """Trace the same model-input builder that actual inference calls."""
    from dataclasses import asdict

    import numpy as np

    tensor, transform = detector.prepare_input(bgr)
    height, width = image.shape[:2]
    processing_h, processing_w = bgr.shape[:2]
    description = detection.preprocessing(detector.name)  # The detector's own resize kind and box inverse.
    if transform is None:
        input_w, input_h = description["input_size"]
        geometry = {"scale_x": input_w / processing_w, "scale_y": input_h / processing_h, "pad_x": 0, "pad_y": 0}
    else:
        geometry = asdict(transform)
    mapping = {"kind": description["resize"], **geometry, "inverse": description["box_mapping"]}
    return {"decode": decode, "decoded_bgr": tensor_fingerprint(image),
            "decoded_channels_equal": bool(np.array_equal(image[:, :, 0], image[:, :, 1])
                                           and np.array_equal(image[:, :, 1], image[:, :, 2])),
            "decoded_stride_bytes": image.strides[0],
            "native_to_processing": {"scale_x": processing_w / width, "scale_y": processing_h / height,
                                     "crop_xyxy": [0, 0, width, height],
                                     "interpolation": "INTER_AREA when resized"},
            "processing_bgr": tensor_fingerprint(bgr), "processing_to_model": mapping,
            "model_tensor": tensor_fingerprint(tensor),
            "box_space": "detections are inverse-mapped model boxes in processing pixels"}


def evaluate(detector, images, threshold, iou_threshold, audit=False, variant="baseline"):
    """Score each image at ``threshold``; a second, untimed call at the floor only feeds diagnostics."""
    floor = min(evaluation.BELOW_THRESHOLD_FLOOR, threshold)
    scores, inference, processing = [], [], []
    for manifest, entry, image, image_path, image_sha256 in images:
        height, width = image.shape[:2]
        started = time.monotonic()
        bgr = prepare_image(image, variant)
        result = detector.detect(bgr, threshold)
        processing.append((time.monotonic() - started) * 1000)
        inference.append(result.inference_ms)
        score = evaluation.score_image(entry, result.detections, detector.classes,
                                       (bgr.shape[1] / width, bgr.shape[0] / height),
                                       iou_threshold)
        score.update(manifest=manifest, input_path=image_path, input_sha256=image_sha256,
                     input_digest_provenance="manifest_verified" if entry.get("sha256") else "computed_only",
                     decoded_width=width, decoded_height=height, processing_width=bgr.shape[1],
                     processing_height=bgr.shape[0], inference_ms=round(result.inference_ms, 3),
                     detections=[item.as_dict() for item in result.detections[:50]])
        below = detector.detect(bgr, floor).detections if floor < threshold else ()
        score["best_below_threshold"] = evaluation.best_below_threshold(entry, below, detector.classes, threshold)
        if audit:
            score["preprocessing_audit"] = preprocessing_audit(detector, image, bgr, entry.get("_input_decode"))
        scores.append(score)
    summary = evaluation.totals(scores)
    summary.update(mean_inference_ms=round(sum(inference) / len(inference), 3),
                   maximum_inference_ms=round(max(inference), 3),
                   mean_processing_ms=round(sum(processing) / len(processing), 3))
    return summary, scores


def source_revision():
    """Identify the checkout and whether local tracked edits affect this run."""
    try:
        revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT,
                                check=True, capture_output=True, text=True, timeout=5).stdout
        diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=ROOT, check=True,
                              capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as error:
        return {"revision": None, "error": str(error)}
    source_files = ("sightcircuit/convert.py", "sightcircuit/detection.py", "sightcircuit/evaluation.py",
                    "sightcircuit/frame.py", "sightcircuit/labels.py", "sightcircuit/preprocess.py",
                    "sightcircuit/sources/mirror_device.py", "scripts/evaluate_detectors.py",
                    "scripts/smoke-assets.json")
    return {"revision": revision, "working_tree_dirty": bool(status),
            "working_tree_paths": [line[3:] for line in status.splitlines()],
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "source_files_sha256": {path: assets.checksum(ROOT / path) for path in source_files}}


def image_runtime_versions():
    import cv2
    import numpy as np

    return {"opencv": cv2.__version__, "numpy": np.__version__}


def model_provenance(detector):
    """Hashes of the files the detector loads and runs, from what its class declares."""
    if detector.runtime not in detection.RUNTIME_MODULES:
        raise ValueError(f"Unknown detector runtime {detector.runtime!r}")
    module = importlib.import_module(detection.RUNTIME_MODULES[detector.runtime])
    runtime_version = module.__version__
    package = Path(module.__file__).parent
    artifacts = sorted({Path(module.__file__), *package.glob("*.so"), *package.glob("capi/*.so"),
                        *package.glob("*.pyd"), *package.glob("capi/*.pyd")})
    return {"detector": describe(detector), "runtime_version": runtime_version,
            "runtime_artifacts": [{"name": str(path.relative_to(package)), "sha256": assets.checksum(path)}
                                  for path in artifacts],
            "runtime_hash_scope": "Python package entry and native extension files; system dependencies not hashed",
            "preprocessing": detection.preprocessing(detector.name),
            "model_files": [{"name": name, "sha256": assets.checksum(assets.ASSETS / name)}
                            for name in detection.model_files(detector.name)]}


def print_expectation(path, result):
    """The pin verdict for people reading CI logs; the JSON report holds the same result."""
    verdict = "passed (counts no worse than pinned)" if result["status"] == "passed" else "REGRESSED"
    print(f"pin {path}: {verdict}", file=sys.stderr)
    for label, key in (("regression", "failures"), ("better than pinned; update the pin", "improvements"),
                       ("not pinned", "unpinned")):
        for item in result[key]:
            print(f"  {label}: {item}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append",
                        help="Evaluation manifest; repeatable. Default: evaluation/seed.json")
    parser.add_argument("--detector", choices=DETECTORS, action="append", help="Repeatable; default: all")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--iou", type=float, default=0.5, help="Box match threshold for annotated objects")
    parser.add_argument("--fetch", action="store_true", help="Download missing checksum-pinned images")
    parser.add_argument("--report", type=Path, help="Explicitly save the JSON report to a new file")
    parser.add_argument("--audit-preprocessing", action="store_true",
                        help="Fingerprint actual decoded, converted and model inputs and geometry")
    parser.add_argument("--require-input-digests", action="store_true",
                        help="Require a supplied SHA-256 for every image before evaluation")
    parser.add_argument("--audit-variant", choices=AUDIT_VARIANTS, action="append",
                        help="Add one-factor audit comparison; repeatable, with separate warm-up and results")
    parser.add_argument("--expect", type=Path,
                        help="Committed count pin, such as evaluation/seed-expected.json; exit 1 on any regression")
    args = parser.parse_args(argv)
    for name, value in (("--threshold", args.threshold), ("--iou", args.iou)):
        if not math.isfinite(value) or not 0 < value <= 1:
            parser.error(f"{name} must be in (0, 1]")
    if args.report and args.report.exists():
        parser.error("--report must name a new file")
    report = {"report_kind": "detector_evaluation", "schema_version": 4, "status": "error",
              "threshold": args.threshold, "iou_threshold": args.iou,
              "confusion_iou_threshold": evaluation.CONFUSION_IOU, "detectors": {},
              "code": source_revision(),
              "environment": {"python": platform.python_version(), "platform": platform.platform()},
              "input_decode": "cv2.imread(..., cv2.IMREAD_COLOR) -> BGR24",
              "conversion": {"function": "sightcircuit.convert.to_bgr",
                             "maximum_processing_side": MAX_PROCESSING_SIDE},
              "warmup": {"runs_per_detector": 1, "input_index": 0, "included_in_timing": False},
              "confidence_is_calibrated_probability": False,
              "below_threshold": {"floor": min(evaluation.BELOW_THRESHOLD_FLOOR, args.threshold), "scored": False,
                                  "method": "one untimed detect() per image at the floor after the measured call",
                                  "limits": "a model's own output filter can hide lower scores"},
              "audit_preprocessing": bool(args.audit_preprocessing or args.audit_variant),
              "require_input_digests": args.require_input_digests,
              "annotation_limits": "Only supplied labels/boxes are scored; zero unannotated checks is not accuracy",
              "scope": "Stills through the application's conversion path; not live latency, color or headset accuracy"}
    try:
        manifests = args.manifest or [DEFAULT_MANIFEST]
        report["manifests"] = [str(path) for path in manifests]
        report["manifest_sha256"] = {str(path): assets.checksum(path) for path in manifests}
        pin = None
        if args.expect:
            pin = evaluation.load_pin(args.expect)
            evaluation.require_pin_applies(pin, [assets.checksum(path) for path in manifests], args.threshold,
                                           args.iou)
            report["expectation"] = {"pin": str(args.expect), "pin_sha256": assets.checksum(args.expect),
                                     "status": "not_checked"}
        images = load_images(manifests, args.fetch, args.require_input_digests)
        report["environment"].update(image_runtime_versions())
        report["images"] = len(images)
        report["warmup"]["image_id"] = images[0][1]["id"]
        for name in args.detector or list(DETECTORS):
            started = time.monotonic()
            detector = create_detector(name)
            startup = (time.monotonic() - started) * 1000
            detector.detect(prepare_image(images[0][2]), args.threshold)  # Exclude allocation from timing.
            audit = bool(args.audit_preprocessing or args.audit_variant)
            summary, scores = evaluate(detector, images, args.threshold, args.iou, audit=audit)
            summary["startup_ms"] = round(startup, 3)
            report["detectors"][name] = {**model_provenance(detector), "totals": summary,
                                         "labels": evaluation.label_table(scores), "images": scores}
            if args.audit_variant:
                comparisons = report["detectors"][name]["audit_variants"] = {}
                for variant in dict.fromkeys(args.audit_variant):
                    detector.detect(prepare_image(images[0][2], variant), args.threshold)
                    variant_totals, variant_scores = evaluate(detector, images, args.threshold, args.iou,
                                                             audit=True, variant=variant)
                    comparisons[variant] = {"change": AUDIT_VARIANTS[variant], "baseline": "baseline",
                                            "warmup_runs": 1, "warmup_included_in_timing": False,
                                            "totals": variant_totals,
                                            "labels": evaluation.label_table(variant_scores),
                                            "images": variant_scores}
            print(f"{name:14s} present {summary['present_hits']}/{summary['present_hits'] + summary['present_misses']}"
                  f"  absent-violations {summary['absent_violations']}  confusions {summary['confusions']}"
                  f"  box-recall {summary['box_recall']}  mean {summary['mean_inference_ms']:.1f} ms",
                  file=sys.stderr)
        tables = {name: result["labels"] for name, result in report["detectors"].items()}
        for line in evaluation.format_label_table(tables, args.threshold):
            print(line, file=sys.stderr)
        if pin is not None:
            counts = {name: evaluation.pin_counts(result["totals"]) for name, result in report["detectors"].items()}
            report["expectation"].update(evaluation.check_pin(pin, counts))
            print_expectation(args.expect, report["expectation"])
        report["status"] = "evaluated"
    except (ImportError, OSError, ValueError, RuntimeError, SightCircuitError) as error:
        report["error"] = str(error)
    print(json.dumps(report, indent=2))
    if args.report:
        try:
            with args.report.open("x") as output:
                output.write(json.dumps(report, indent=2) + "\n")
        except OSError as error:
            print(f"Could not save report: {error}", file=sys.stderr)
            return 1
    expectation = report.get("expectation", {"status": "passed"})["status"]
    return 0 if report["status"] == "evaluated" and expectation == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
