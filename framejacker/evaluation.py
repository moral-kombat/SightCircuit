"""Compare detectors on a labelled image set using canonical (COCO) label names.

A manifest lists images and what a correct detector should report:

* ``present``: labels that must be detected somewhere in the image. An entry
  may be a list of acceptable alternatives, such as ``["car", "truck"]``.
* ``absent``: labels that must not be reported (false-positive checks).
* ``objects``: optional boxes ``{"label", "box_xyxy", "confusable"}`` in source
  pixels. Objects whose label a detector supports are matched by IoU; a
  detection with a ``confusable`` label overlapping the object is a confusion
  (for example, a rug detected as a couch). Set ``boxes_complete`` when every
  supported object is boxed, so unmatched detections count as false positives.

Labels a detector cannot produce are reported as unsupported, not as misses.
"""
import json
import math
from pathlib import Path

from .labels import canonical

SCHEMA_VERSION = 1
CONFUSION_IOU = 0.3


class ManifestError(ValueError):
    pass


def load_manifest(path):
    path = Path(path)
    manifest = json.loads(path.read_text())
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION
            or not isinstance(manifest.get("images"), list)):
        raise ManifestError(f"{path}: expected schema_version {SCHEMA_VERSION} with an images list")
    seen = set()
    for entry in manifest["images"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                or not entry["id"] or entry["id"] in seen):
            raise ManifestError(f"{path}: every image needs a unique string id")
        seen.add(entry["id"])
        if ("path" in entry) == ("file" in entry):
            raise ManifestError(f"{path}: image {entry['id']} needs exactly one of path or file")
        if not isinstance(entry.get("path", entry.get("file")), str):
            raise ManifestError(f"{path}: image {entry['id']} has an invalid path")
        digest = entry.get("sha256")
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                   or any(c not in "0123456789abcdef" for c in digest)):
            raise ManifestError(f"{path}: image {entry['id']} has an invalid SHA-256")
        for field in ("present", "absent"):
            labels = entry.get(field, [])
            if not isinstance(labels, list):
                raise ManifestError(f"{path}: {field} must be a list")
            for label in labels:
                options = label if field == "present" and isinstance(label, list) else [label]
                if not options or any(not isinstance(item, str) or not item.strip() for item in options):
                    raise ManifestError(f"{path}: invalid {field} label")
        if not isinstance(entry.get("boxes_complete", False), bool):
            raise ManifestError(f"{path}: boxes_complete must be a boolean")
        if not isinstance(entry.get("objects", []), list):
            raise ManifestError(f"{path}: objects must be a list")
        for obj in entry.get("objects", []):
            if not isinstance(obj, dict):
                raise ManifestError(f"{path}: invalid object")
            box = obj.get("box_xyxy")
            if (not isinstance(obj.get("label"), str) or not isinstance(box, list) or len(box) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) or v < 0 for v in box)
                    or not box[0] < box[2] or not box[1] < box[3]):
                raise ManifestError(f"{path}: image {entry['id']} has an invalid object")
            if (not isinstance(obj.get("confusable", []), list)
                    or any(not isinstance(label, str) for label in obj.get("confusable", []))):
                raise ManifestError(f"{path}: invalid confusable labels")
    manifest["_directory"] = path.parent
    return manifest


def resolve_image(manifest, entry, download_directory):
    if "path" in entry:
        location = Path(entry["path"]).expanduser()
        return location if location.is_absolute() else manifest["_directory"] / location
    return Path(download_directory) / entry["file"]


def iou(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    return intersection / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def score_image(entry, detections, vocabulary, scale=1.0, iou_threshold=0.5):
    """Score processed-pixel detections; ``scale`` is a scalar or realized (x, y) ratios."""
    scale_x, scale_y = scale if isinstance(scale, (tuple, list)) else (scale, scale)
    vocabulary = {canonical(label) for label in vocabulary}
    found = [(canonical(item.label), item.confidence, item.box_xyxy) for item in detections]
    reported = {label for label, _, _ in found}
    score = {"id": entry["id"], "reported": sorted(reported), "present_hits": [], "present_misses": [],
             "unsupported": [], "absent_violations": [],
             "annotation_coverage": {"presence_checks": len(entry.get("present", [])),
                                     "absence_checks": len(entry.get("absent", [])),
                                     "objects": len(entry.get("objects", [])),
                                     "boxes_complete": bool(entry.get("boxes_complete", False))}}
    for expected in entry.get("present", []):
        options = [canonical(label) for label in (expected if isinstance(expected, list) else [expected])]
        name = "|".join(options)
        if not vocabulary & set(options):
            score["unsupported"].append(name)
        elif reported & set(options):
            score["present_hits"].append(name)
        else:
            score["present_misses"].append(name)
    for label in map(canonical, entry.get("absent", [])):
        if label not in vocabulary:
            score["unsupported"].append(label)
        elif label in reported:
            score["absent_violations"].append(label)

    objects = [dict(obj, label=canonical(obj["label"]),
                    box=[value * ratio for value, ratio in zip(obj["box_xyxy"], (scale_x, scale_y) * 2)])
               for obj in entry.get("objects", [])]
    if objects or entry.get("boxes_complete"):
        unmatched = sorted(range(len(found)), key=lambda index: -found[index][1])
        true_positive = false_negative = 0
        for obj in objects:
            if obj["label"] not in vocabulary:
                score["unsupported"].append(obj["label"])
                continue
            match = next((index for index in unmatched if found[index][0] == obj["label"]
                          and iou(found[index][2], obj["box"]) >= iou_threshold), None)
            if match is None:
                false_negative += 1
            else:
                true_positive += 1
                unmatched.remove(match)
        score["confusions"] = [
            {"object": obj["label"], "detected": label, "confidence": confidence}
            for obj in objects for label, confidence, box in found
            if label in map(canonical, obj.get("confusable", [])) and iou(box, obj["box"]) >= CONFUSION_IOU]
        score.update(true_positives=true_positive, false_negatives=false_negative)
        if entry.get("boxes_complete"):
            score["false_positives"] = len(unmatched)
    score["unsupported"] = sorted(set(score["unsupported"]))
    return score


def totals(scores):
    def count(key):
        return sum(len(score[key]) if isinstance(score.get(key), list) else score.get(key, 0) for score in scores)

    result = {"images": len(scores), "present_hits": count("present_hits"),
              "present_misses": count("present_misses"), "absent_violations": count("absent_violations"),
              "confusions": count("confusions"), "true_positives": count("true_positives"),
              "false_negatives": count("false_negatives")}
    result["annotation_coverage"] = {
        "presence_checks": sum(s.get("annotation_coverage", {}).get("presence_checks", 0) for s in scores),
        "absence_checks": sum(s.get("annotation_coverage", {}).get("absence_checks", 0) for s in scores),
        "objects": sum(s.get("annotation_coverage", {}).get("objects", 0) for s in scores),
        "images_with_complete_boxes": sum(bool(s.get("annotation_coverage", {}).get("boxes_complete"))
                                          for s in scores)}
    expected = result["present_hits"] + result["present_misses"]
    result["present_recall"] = round(result["present_hits"] / expected, 4) if expected else None
    boxed = result["true_positives"] + result["false_negatives"]
    result["box_recall"] = round(result["true_positives"] / boxed, 4) if boxed else None
    complete = [score for score in scores if "false_positives" in score]
    if complete:
        result["false_positives"] = sum(score["false_positives"] for score in complete)
        matched = sum(score["true_positives"] for score in complete)
        predicted = matched + result["false_positives"]
        result["box_precision"] = round(matched / predicted, 4) if predicted else None
    return result
