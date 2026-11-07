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

Box recall is reported over all objects and per label (``totals`` and
``label_table``), so the POC object's own recall can be read from a report.

Diagnostics never change scoring: ``best_below_threshold`` reports how close
each expected label came to the threshold, and ``label_table`` tabulates
per-label outcomes. A count pin (``load_pin``/``check_pin``) fails when a
detector does worse than committed counts; it compares counts, never scores,
so tiny floating-point differences between CPUs cannot move it.
"""
import json
import math
from pathlib import Path

from .labels import canonical

SCHEMA_VERSION = 1
CONFUSION_IOU = 0.3
BELOW_THRESHOLD_FLOOR = 0.05  # Diagnostic detections start here; they are never scored.
PIN_SCHEMA_VERSION = 1
# Each pinned count and the direction in which a new result may differ without failing.
PIN_RULES = {"images": "equal", "present_checks": "equal", "present_hits": "minimum",
             "absence_checks": "equal", "absent_violations": "maximum", "confusions": "maximum"}
PIN_FIELDS = {"schema_version", "description", "manifest", "manifest_sha256", "threshold", "iou_threshold",
              "detectors"}


class ManifestError(ValueError):
    pass


class PinError(ValueError):
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
            # One check per canonical label: "sofa" and "couch" would count twice in totals but once per label.
            names = [expected_options(label)[0] if field == "present" else canonical(label) for label in labels]
            repeated = sorted({name for name in names if names.count(name) > 1})
            if repeated:
                raise ManifestError(f"{path}: image {entry['id']} repeats {field} label {', '.join(repeated)} "
                                    "(possibly under another name for the same canonical label)")
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


def expected_options(expected):
    """A ``present`` entry's report name and canonical alternatives."""
    options = [canonical(label) for label in (expected if isinstance(expected, list) else [expected])]
    return "|".join(options), options


def score_image(entry, detections, vocabulary, scale=1.0, iou_threshold=0.5):
    """Score processed-pixel detections; ``scale`` is a scalar or realized (x, y) ratios."""
    scale_x, scale_y = scale if isinstance(scale, (tuple, list)) else (scale, scale)
    vocabulary = {canonical(label) for label in vocabulary}
    found = [(canonical(item.label), item.confidence, item.box_xyxy) for item in detections]
    reported = {label for label, _, _ in found}
    score = {"id": entry["id"], "reported": sorted(reported), "present_hits": [], "present_misses": [],
             "unsupported": [], "absent_violations": [], "absent_clean": [],
             "annotation_coverage": {"presence_checks": len(entry.get("present", [])),
                                     "absence_checks": len(entry.get("absent", [])),
                                     "objects": len(entry.get("objects", [])),
                                     "boxes_complete": bool(entry.get("boxes_complete", False))}}
    outcomes = score["label_outcomes"] = {"present": {}, "absent": {}}
    for expected in entry.get("present", []):
        name, options = expected_options(expected)
        if not vocabulary & set(options):
            outcome, key = "unsupported", "unsupported"
        elif reported & set(options):
            outcome, key = "hit", "present_hits"
        else:
            outcome, key = "miss", "present_misses"
        score[key].append(name)
        outcomes["present"][name] = outcome
    for label in map(canonical, entry.get("absent", [])):
        if label not in vocabulary:
            outcome, key = "unsupported", "unsupported"
        elif label in reported:
            outcome, key = "violation", "absent_violations"
        else:
            outcome, key = "clean", "absent_clean"
        score[key].append(label)
        outcomes["absent"][label] = outcome

    objects = [dict(obj, label=canonical(obj["label"]),
                    box=[value * ratio for value, ratio in zip(obj["box_xyxy"], (scale_x, scale_y) * 2)])
               for obj in entry.get("objects", [])]
    if objects or entry.get("boxes_complete"):
        unmatched = sorted(range(len(found)), key=lambda index: -found[index][1])
        true_positive = false_negative = 0
        boxes = score["box_outcomes"] = {}  # Per label: how many of its boxes matched, missed or are unsupported.
        for obj in objects:
            row = boxes.setdefault(obj["label"], dict.fromkeys(LABEL_COLUMNS["boxes"], 0))
            if obj["label"] not in vocabulary:
                score["unsupported"].append(obj["label"])
                row["unsupported"] += 1
                continue
            match = next((index for index in unmatched if found[index][0] == obj["label"]
                          and iou(found[index][2], obj["box"]) >= iou_threshold), None)
            if match is None:
                false_negative += 1
                row["missed"] += 1
            else:
                true_positive += 1
                row["matched"] += 1
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
              "absent_clean": count("absent_clean"), "confusions": count("confusions"), "true_positives": count("true_positives"),
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
    result["box_recall_by_label"] = box_recall_by_label(scores)
    complete = [score for score in scores if "false_positives" in score]
    if complete:
        result["false_positives"] = sum(score["false_positives"] for score in complete)
        matched = sum(score["true_positives"] for score in complete)
        predicted = matched + result["false_positives"]
        result["box_precision"] = round(matched / predicted, 4) if predicted else None
    return result


def box_recall_by_label(scores):
    """Per canonical label, its matched and missed boxes over ``scores`` and their recall.

    A label whose boxes are all unsupported has no recall (``None``) and zero
    counts; the counts are the ones ``totals`` sums over all labels.
    """
    counts = {}
    for score in scores:
        for label, row in score.get("box_outcomes", {}).items():
            total = counts.setdefault(label, {"true_positives": 0, "false_negatives": 0})
            total["true_positives"] += row["matched"]
            total["false_negatives"] += row["missed"]
    for total in counts.values():
        boxed = total["true_positives"] + total["false_negatives"]
        total["box_recall"] = round(total["true_positives"] / boxed, 4) if boxed else None
    return dict(sorted(counts.items()))


def best_below_threshold(entry, candidates, vocabulary, threshold):
    """The highest candidate score under ``threshold`` for each supported expected label.

    ``candidates`` come from a separate diagnostic detection at a lower
    threshold and are never scored. ``None`` means no candidate between that
    lower threshold (or the model's own output filter) and ``threshold``.
    """
    vocabulary = {canonical(label) for label in vocabulary}
    best = {}
    for item in candidates:
        label = canonical(item.label)
        if item.confidence < threshold:
            best[label] = max(best.get(label, item.confidence), item.confidence)
    result = {"present": {}, "absent": {}}
    for expected in entry.get("present", []):
        name, options = expected_options(expected)
        if vocabulary & set(options):
            found = [best[option] for option in options if option in best]
            result["present"][name] = max(found) if found else None
    for label in map(canonical, entry.get("absent", [])):
        if label in vocabulary:
            result["absent"][label] = best.get(label)
    return result


LABEL_COLUMNS = {"present": ("hit", "miss", "unsupported"), "absent": ("clean", "violation", "unsupported"),
                 "boxes": ("matched", "missed", "unsupported")}
NEAR_OUTCOME = {"present": "miss", "absent": "clean"}  # Where a below-threshold score is informative.


def label_table(scores):
    """Per-label expectations against outcomes over scored images.

    ``best_below_threshold`` is the highest diagnostic score among images where
    a present label was missed, or an absent label stayed clean: how near the
    miss came to a hit, or the clean check to a violation.
    """
    table = {kind: {} for kind in LABEL_COLUMNS}
    for score in scores:
        best = score.get("best_below_threshold", {})
        for kind, outcomes in score.get("label_outcomes", {}).items():
            for label, outcome in outcomes.items():
                row = table[kind].setdefault(label, {**dict.fromkeys(LABEL_COLUMNS[kind], 0),
                                                     "best_below_threshold": None})
                row[outcome] += 1
                value = best.get(kind, {}).get(label)
                if outcome == NEAR_OUTCOME[kind] and value is not None:
                    row["best_below_threshold"] = max(value, row["best_below_threshold"] or 0)
        for label, counts in score.get("box_outcomes", {}).items():  # Boxes: counts per image, no diagnostic.
            row = table["boxes"].setdefault(label, {**dict.fromkeys(LABEL_COLUMNS["boxes"], 0),
                                                    "best_below_threshold": None})
            for outcome, count in counts.items():
                row[outcome] += count
    return {kind: dict(sorted(rows.items())) for kind, rows in table.items()}


def label_cell(kind, row):
    """One detector's outcome for one label, as text."""
    if row is None:
        return "-"
    good, bad = LABEL_COLUMNS[kind][:2]
    checked = row[good] + row[bad]
    if not checked:
        return "unsupported"
    text = f"{row[good]}/{checked} {good}"
    if kind in NEAR_OUTCOME and row["best_below_threshold"] is not None:
        text += f" (best {NEAR_OUTCOME[kind]} {row['best_below_threshold']:.3f})"
    return text + (f", {row['unsupported']} unsupported" if row["unsupported"] else "")


def format_label_table(tables, threshold):
    """Human-readable ``label_table`` rows with one column per detector (``{name: table}``)."""
    order = list(LABEL_COLUMNS)  # present, then absent, then boxes
    rows = sorted({(kind, label) for table in tables.values() for kind in LABEL_COLUMNS
                   for label in table.get(kind, {})}, key=lambda row: (order.index(row[0]), row[1]))
    cells = [["label", "expected", *tables]]
    cells += [[label, kind, *(label_cell(kind, table.get(kind, {}).get(label)) for table in tables.values())]
              for kind, label in rows]
    widths = [max(len(row[column]) for row in cells) for column in range(len(cells[0]))]
    return [f"per-label outcomes at threshold {threshold}; (best ...) is the highest diagnostic score below it",
            *("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip() for row in cells)]


def pin_counts(summary):
    """The discrete totals a pin compares; confidence scores never enter a pin."""
    return {"images": summary["images"], "present_checks": summary["present_hits"] + summary["present_misses"],
            "present_hits": summary["present_hits"],
            "absence_checks": summary["absent_violations"] + summary["absent_clean"],
            "absent_violations": summary["absent_violations"], "confusions": summary["confusions"]}


def load_pin(path):
    """Validate a committed count pin for ``evaluate_detectors.py --expect``."""
    path = Path(path)
    try:
        pin = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise PinError(f"{path}: {error}") from None
    if not isinstance(pin, dict) or pin.get("schema_version") != PIN_SCHEMA_VERSION or set(pin) - PIN_FIELDS:
        raise PinError(f"{path}: expected schema_version {PIN_SCHEMA_VERSION} with only "
                       f"{', '.join(sorted(PIN_FIELDS))}")
    digest = pin.get("manifest_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)):
        raise PinError(f"{path}: manifest_sha256 must be the manifest's SHA-256")
    for field in ("threshold", "iou_threshold"):
        value = pin.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not 0 < value <= 1:
            raise PinError(f"{path}: {field} must be in (0, 1]")
    detectors = pin.get("detectors")
    if not isinstance(detectors, dict) or not detectors:
        raise PinError(f"{path}: detectors must pin at least one detector")
    for name, counts in detectors.items():
        if (not isinstance(counts, dict) or set(counts) != set(PIN_RULES)
                or any(type(value) is not int or value < 0 for value in counts.values())
                or counts["present_hits"] > counts["present_checks"]
                or counts["absent_violations"] > counts["absence_checks"]):
            raise PinError(f"{path}: {name} needs consistent non-negative integer {', '.join(PIN_RULES)}")
    return pin


def require_pin_applies(pin, manifest_digests, threshold, iou_threshold):
    """Fail before any inference unless the run uses the pin's manifest, threshold and IoU."""
    if len(manifest_digests) != 1:
        raise PinError("--expect compares exactly one manifest")
    if manifest_digests[0] != pin["manifest_sha256"]:
        raise PinError("--expect pin was recorded for a different manifest (SHA-256 mismatch)")
    if threshold != pin["threshold"]:
        raise PinError(f"--expect pin was recorded at threshold {pin['threshold']}, not {threshold}")
    if iou_threshold != pin["iou_threshold"]:
        raise PinError(f"--expect pin was recorded at box IoU {pin['iou_threshold']}, not {iou_threshold}")


def check_pin(pin, counts):
    """Compare ``{detector: pin_counts}`` with ``pin``: regressions fail; improvements and new detectors pass."""
    failures, improvements = [], []
    for name, pinned in pin["detectors"].items():
        if name not in counts:
            failures.append(f"{name} pinned but not evaluated")
            continue
        for key, rule in PIN_RULES.items():
            actual, expected = counts[name][key], pinned[key]
            if actual == expected:
                continue
            if rule == "equal":
                failures.append(f"{name} {key} {actual} != pinned {expected}")
                continue
            change = f"{name} {key} {actual} {'<' if actual < expected else '>'} pinned {expected}"
            (failures if (actual < expected) == (rule == "minimum") else improvements).append(change)
    return {"status": "regressed" if failures else "passed", "failures": failures, "improvements": improvements,
            "unpinned": sorted(set(counts) - set(pin["detectors"]))}
