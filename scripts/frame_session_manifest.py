"""Add one still to a private detector-evaluation manifest (evaluation/local/frame.json).

The image path is stored relative to the manifest, as ``evaluate_detectors.py`` expects.
Labels are canonical COCO names. ``--object label:x1,y1,x2,y2`` (repeatable) records a
box in source pixels of the still, as an ``objects`` entry the evaluator scores by IoU;
``--boxes-complete`` states that every supported object in the still is boxed, so
unmatched detections count as false positives. Without them the entry stays
presence-only, as before. With ``--still-report`` (the JSON that
``mirror_recognize.py --still`` printed), the entry also pins the still's SHA-256 from
that capture report, which ``evaluate_detectors.py`` verifies (and
``--require-input-digests`` requires), plus its detector, source and sequence, and
every box must fit the still's recorded size.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


def split(text):
    return [item.strip() for item in text.split(",") if item.strip()]


def coordinate(text):
    """A non-negative finite pixel coordinate: an integer when written as one."""
    try:
        value = int(text)
    except ValueError:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"Box coordinates must be numbers, not {text.strip()!r}") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Box coordinates must be non-negative pixels, not {text.strip()!r}")
    return value


def parse_object(text):
    """``label:x1,y1,x2,y2`` in source pixels, as an evaluation manifest ``objects`` entry."""
    label, separator, box = text.rpartition(":")
    values = box.split(",")
    if not separator or not label.strip() or len(values) != 4:
        raise ValueError(f"--object needs label:x1,y1,x2,y2, not {text!r}")
    x1, y1, x2, y2 = (coordinate(value) for value in values)
    if not x1 < x2 or not y1 < y2:
        raise ValueError(f"--object {text!r} needs x1 < x2 and y1 < y2")
    return {"label": label.strip(), "box_xyxy": [x1, y1, x2, y2]}


def require_boxes_fit(objects, report):
    """Every box lies inside the still size the capture report recorded, when it recorded one."""
    width, height = report.get("still_width"), report.get("still_height")
    if type(width) is not int or type(height) is not int:
        return
    for obj in objects:
        if obj["box_xyxy"][2] > width or obj["box_xyxy"][3] > height:
            raise ValueError(f"Object {obj['label']} box {obj['box_xyxy']} exceeds the {width}x{height} still")


def still_fields(report, image):
    """Manifest fields pinning ``image`` to the capture report that saved it.

    The SHA-256 comes from the report, computed when the still was written, and
    the file must still match it; nothing is pinned from hashing the file alone.
    """
    if (report.get("report_kind") != "steamvr_mirror_recognition" or report.get("status") != "recognized"
            or report.get("exit_code") != 0 or not isinstance(report.get("still_path"), str)):
        raise ValueError("Still report is not a successful mirror_recognize.py --still report")
    if Path(report["still_path"]).resolve() != Path(image).resolve():
        raise ValueError(f"Still report describes {report['still_path']}, not {image}")
    digest = report.get("still_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Still report has no valid still_sha256")
    with Path(image).open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
            raise ValueError("Image does not match the still report's SHA-256")
    capture = {"detector": report.get("still_detector"), "source": report.get("still_source"),
               "sequence": report.get("still_sequence")}
    if (not isinstance(capture["detector"], str) or not capture["detector"] or not isinstance(capture["source"], str)
            or not capture["source"] or type(capture["sequence"]) is not int or capture["sequence"] < 1):
        raise ValueError("Still report lacks the still's detector, source or sequence")
    return {"sha256": digest, "capture": capture}


def add_image(manifest, image_id, image, present, absent, still_report=None, objects=(), boxes_complete=False):
    """Append one entry; ``objects`` and ``boxes_complete`` are written only when given."""
    manifest = Path(manifest)
    data = json.loads(manifest.read_text()) if manifest.exists() else {"schema_version": 1, "images": []}
    if data.get("schema_version") != 1 or not isinstance(data.get("images"), list):
        raise ValueError("Unsupported manifest")
    if any(entry.get("id") == image_id for entry in data["images"]):
        raise ValueError(f"Image id already present: {image_id}")
    pinned = still_fields(still_report, image) if still_report is not None else {}
    objects = list(objects)
    if still_report is not None:
        require_boxes_fit(objects, still_report)
    boxes = {"objects": objects} if objects or boxes_complete else {}
    if boxes_complete:
        boxes["boxes_complete"] = True
    relative = os.path.relpath(Path(image).resolve(), manifest.parent.resolve())
    data["images"].append({"id": image_id, "path": Path(relative).as_posix(),
                           "present": present, "absent": absent, **boxes, **pinned})
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=manifest.parent, suffix=".tmp", delete=False) as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(handle.name, manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--present", default="", help="Comma-separated labels that are in the scene")
    parser.add_argument("--absent", default="", help="Comma-separated labels that must not be reported")
    parser.add_argument("--object", action="append", default=[], metavar="LABEL:X1,Y1,X2,Y2",
                        help="One boxed object in source pixels of the still; repeatable")
    parser.add_argument("--boxes-complete", action="store_true",
                        help="Every supported object in the still is boxed (unmatched detections become false positives)")
    parser.add_argument("--still-report", type=Path,
                        help="JSON report of the mirror_recognize.py --still run that saved --image")
    args = parser.parse_args(argv)
    if not args.image.is_file():
        sys.exit(f"Image not found: {args.image}")
    try:
        objects = [parse_object(text) for text in args.object]
        report = json.loads(args.still_report.read_text()) if args.still_report else None
        if report is not None and not isinstance(report, dict):
            raise ValueError("Still report must be a JSON object")
        add_image(args.manifest, args.id, args.image, split(args.present), split(args.absent), report,
                  objects, args.boxes_complete)
    except (OSError, ValueError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
