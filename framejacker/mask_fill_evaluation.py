"""Offline comparison of fills for the native HUD mask; the runtime never imports this module.

``scripts/evaluate_mask_fill.py`` runs it and writes a JSON report. The runtime
fill is ``framejacker.hud.mask.fill_regions``; the other candidates live only
here, so the evidence that chose it can be reproduced. Every candidate keeps the
runtime's contract: it runs on a native-resolution copy whose masked regions
(footprint plus margin, ``mask_regions``) are already zero, before any resize,
and fills each region only from pixels outside every masked region in that
region's own eye. Nothing it writes can depend on a pixel inside a region.

Two inputs are evaluated with each detector:

* the gray sweep: the synthetic HUD source's uniform 96x64 frames with the
  real fixture calibration (``synthetic_hud_calibration``), as in E76;
* the seed stills (``evaluation/seed.json``), with a mask pasted at a few
  plausible positions (``seed_placements``). These use the same geometry rules
  and fill functions but never a ``Calibration``: seed photos are not a capture
  source, so nothing here passes one through the synthetic-fixture path.

Results are runner-CPU measurements on generated frames and public stills, never
Frame or headset results.
"""
from collections import Counter
import math
import time

from .hud.contract import MARGIN_MAX_PX, MARGIN_MIN_PX, margin_valid, viewports
from .hud.geometry import contains, exclusion, expand, within_blind_budget
from .hud.mask import FILL_RING_PX, fill_regions, mask_regions, surround

THRESHOLD = 0.5
DIAGNOSTIC_FLOOR = 0.3  # Second, untimed detection for near-threshold diagnostics; never scored.
SCORE_TOLERANCE = 0.05  # A matched detection whose score moves less than this is unchanged.
MATCH_IOU = 0.5  # Same object: same label and at least this IoU in detector-input pixels.
ARTIFACT_INSIDE = 0.25  # An artifact-like box has at least this share of its area on the filled region ...
ARTIFACT_IOU = 0.3  # ... and no unmasked detection of any label at this IoU (evaluation.CONFUSION_IOU).
INPAINT_RADIUS = 3
ODD_GRAY_LEVELS = tuple(range(1, 254, 2))  # E76's sweep: 127 odd uniform levels.
ALL_GRAY_LEVELS = tuple(range(255))  # Every level SyntheticSource emits (sequence % 255).
HUD_WIDTH_FRACTION = 0.16  # The 0.32 m panel at 1.25 m spans about 14.6 degrees of a roughly 90 degree view.
HUD_ASPECT = 320 / 128  # The tile's width over height (HUD_CONFIG).
BLIND_BUDGET_REGION = (960, 540, 1920, 1080)  # The largest region a 1920x1080 mono eye allows (a quarter).


# Candidate fills. Each takes (native, regions, eyes), fills in place and reads
# only what the runtime's ``surround`` rule allows. ``ring_mean`` is the runtime.

def zero_fill(native, regions, eyes):
    """Leave the zeroed regions black (the runtime fill until package J)."""


def nearest(native, regions, eyes):
    """Each pixel copies its nearest usable pixel (approximate Euclidean distance transform)."""
    import cv2
    import numpy as np

    for x0, y0, x1, y1 in regions:
        (a0, b0, a1, b1), usable = surround((x0, y0, x1, y1), regions, eyes, 2)
        crop = native[b0:b1, a0:a1]
        _, labels = cv2.distanceTransformWithLabels((~usable).astype(np.uint8), cv2.DIST_L2, 5,
                                                    labelType=cv2.DIST_LABEL_PIXEL)
        ys, xs = np.nonzero(usable)
        lookup = np.zeros((int(labels.max()) + 1, 2), np.intp)
        lookup[labels[ys, xs]] = np.stack((ys, xs), 1)
        source = lookup[labels[y0 - b0:y1 - b0, x0 - a0:x1 - a0]]
        native[y0:y1, x0:x1] = crop[source[..., 0], source[..., 1]]


def _inpaint(flag):
    def fill(native, regions, eyes):
        import cv2
        import numpy as np

        for x0, y0, x1, y1 in regions:
            (a0, b0, a1, b1), usable = surround((x0, y0, x1, y1), regions, eyes, INPAINT_RADIUS + 2)
            crop = np.ascontiguousarray(native[b0:b1, a0:a1])
            result = cv2.inpaint(crop, (~usable).astype(np.uint8) * 255, INPAINT_RADIUS, flag)
            native[y0:y1, x0:x1] = result[y0 - b0:y1 - b0, x0 - a0:x1 - a0]
    return fill


def inpaint_telea(native, regions, eyes):
    """``cv2.inpaint`` (Telea) with a mask covering exactly the masked regions."""
    import cv2
    _inpaint(cv2.INPAINT_TELEA)(native, regions, eyes)


def inpaint_ns(native, regions, eyes):
    """``cv2.inpaint`` (Navier-Stokes) with a mask covering exactly the masked regions."""
    import cv2
    _inpaint(cv2.INPAINT_NS)(native, regions, eyes)


def surround_blur(native, regions, eyes):
    """A blurred patch: each pixel is the rounded mean of the usable pixels in a box of half-size max(w, h).

    Exact integer box sums (integral images), so the result is identical on any CPU.
    """
    import cv2
    import numpy as np

    for x0, y0, x1, y1 in regions:
        half = max(x1 - x0, y1 - y0)
        (a0, b0, a1, b1), usable = surround((x0, y0, x1, y1), regions, eyes, half)
        weights = usable.astype(np.uint8)
        sums = cv2.integral(native[b0:b1, a0:a1] * weights[..., None], sdepth=cv2.CV_32S).astype(np.int64)
        counts = cv2.integral(weights, sdepth=cv2.CV_32S).astype(np.int64)
        ys = np.arange(y0 - b0, y1 - b0)[:, None]
        xs = np.arange(x0 - a0, x1 - a0)[None, :]
        top, bottom = np.clip(ys - half, 0, b1 - b0), np.clip(ys + half + 1, 0, b1 - b0)
        left, right = np.clip(xs - half, 0, a1 - a0), np.clip(xs + half + 1, 0, a1 - a0)
        total = sums[bottom, right] - sums[top, right] - sums[bottom, left] + sums[top, left]
        count = (counts[bottom, right] - counts[top, right] - counts[bottom, left] + counts[top, left])[..., None]
        if not count.all():
            raise ValueError("hud_mask_fill_without_surround")
        native[y0:y1, x0:x1] = ((2 * total + count) // (2 * count)).astype(np.uint8)


FILLS = {
    "zero": (zero_fill, "Baseline: the zeroed region stays black (the runtime fill at 881fea7)"),
    "ring_mean": (fill_regions, f"The runtime fill (hud.mask.fill_regions): one color per region, the rounded "
                                f"mean of the {FILL_RING_PX}-pixel ring outside it in the same eye"),
    "nearest": (nearest, "Nearest-outside-pixel replication (cv2.distanceTransformWithLabels, DIST_L2, 5x5)"),
    "inpaint_telea": (inpaint_telea, f"cv2.inpaint INPAINT_TELEA, radius {INPAINT_RADIUS}, mask exactly the region"),
    "inpaint_ns": (inpaint_ns, f"cv2.inpaint INPAINT_NS, radius {INPAINT_RADIUS}, mask exactly the region"),
    "surround_blur": (surround_blur, "Blurred patch: rounded mean of usable pixels in a box of half-size max(w, h), "
                                     "exact integer box sums"),
}
RUNTIME_FILL = "ring_mean"  # FILLS[RUNTIME_FILL] is the runtime function itself.


# The runtime's preparation with a chosen fill, and the comparisons.

def inference_shape(width, height, max_side=960):
    """``HudMask.inference_shape`` for a native size: ``(height, width)`` with the longer side at most ``max_side``."""
    ratio = min(1, max_side / max(width, height))
    return (max(1, round(height * ratio)), max(1, round(width * ratio)))


def masked_input(native, regions, eyes, fill, shape):
    """``HudMask.prepare``'s detector input with ``fill``: copy, zero the regions, fill, then the one resize."""
    import cv2
    import numpy as np

    image = np.array(native, copy=True, order="C")
    for x0, y0, x1, y1 in regions:
        image[y0:y1, x0:x1] = 0
    fill(image, regions, eyes)
    if image.shape[:2] != tuple(shape):
        image = cv2.resize(image, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)
    return image


def unmasked_input(native, shape):
    """The same resize without any mask."""
    import cv2

    if native.shape[:2] == tuple(shape):
        return native.copy()
    return cv2.resize(native, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)


def native_box(box, shape, width, height):
    """``HudMask.select``'s native box: outward rounding of a detector-input box."""
    rows, columns = shape
    return (math.floor(box[0] * width / columns), math.floor(box[1] * height / rows),
            math.ceil(box[2] * width / columns), math.ceil(box[3] * height / rows))


def classify(detections, shape, width, height, regions, eyes, threshold=THRESHOLD):
    """Each valid detection at or above ``threshold`` with its native box and ``geometry.exclusion`` reason."""
    found = []
    for item in detections:
        box = tuple(float(value) for value in item.box_xyxy)
        if item.confidence < threshold or not contains((0, 0, shape[1], shape[0]), box):
            continue
        native = native_box(box, shape, width, height)
        found.append({"label": item.label, "confidence": round(float(item.confidence), 4), "box": box,
                      "native": native, "reason": exclusion(native, regions, eyes)})
    return found


def runtime_kept(detections, shape, width, height, regions, eyes, threshold=THRESHOLD):
    """The detections ``HudMask.select`` keeps for presentation: valid, at ``threshold``, touching no mask, in one eye.

    The others are the boxes the runtime drops and a validation run fails on.
    """
    return [item for item in detections
            if [entry["reason"] for entry in classify((item,), shape, width, height, regions, eyes, threshold)] == [None]]


def iou(a, b):
    width, height = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    overlap = width * height
    return overlap / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)


def _match(item, candidates, same_label=True):
    """Index of the best-IoU candidate at ``MATCH_IOU`` or more (same label unless ``same_label`` is False)."""
    best, best_iou = None, MATCH_IOU
    for index, other in enumerate(candidates):
        if same_label and other["label"] != item["label"]:
            continue
        overlap = iou(item["box"], other["box"])
        if overlap >= best_iou:
            best, best_iou = index, overlap
    return best


def _near(item, diagnostic, threshold):
    """Whether a same-label detection within ``SCORE_TOLERANCE`` below ``threshold`` matches ``item``."""
    close = [other for other in diagnostic if other["confidence"] >= threshold - SCORE_TOLERANCE]
    return _match(item, close) is not None


def compare(unmasked, masked, regions, diagnostic_unmasked=(), diagnostic_masked=(), threshold=THRESHOLD):
    """How detections that do not touch the mask change, and which detections touch it.

    ``unmasked`` and ``masked`` come from ``classify`` at ``threshold``; the
    diagnostic lists from ``classify`` at ``DIAGNOSTIC_FLOOR``. A lost or added
    detection whose counterpart sits within ``SCORE_TOLERANCE`` of the threshold
    on the other image is also counted as marginal. A detection touching the
    mask is new when no unmasked detection of its label matches it. A new one
    with at least ``ARTIFACT_INSIDE`` of its native box on ``regions`` and no
    unmasked detection of any label at ``ARTIFACT_IOU`` IoU or more is the
    closest proxy for a box the fill itself caused (``mask_overlap_artifact_like``);
    the others are real objects partly under the mask, often relabelled.
    """
    clear_u = sorted((item for item in unmasked if item["reason"] is None), key=lambda item: -item["confidence"])
    clear_m = [item for item in masked if item["reason"] is None]
    matched, lost = [], []
    for item in clear_u:
        index = _match(item, clear_m)
        if index is None:
            lost.append(item)
        else:
            matched.append((item, clear_m.pop(index)))
    added, relabelled = clear_m, []
    for item in list(lost):
        index = _match(item, added, same_label=False)
        if index is not None:
            relabelled.append((item, added.pop(index)))
            lost.remove(item)
    deltas = [m["confidence"] - u["confidence"] for u, m in matched]
    touching = [item for item in masked if item["reason"] == "mask_overlap"]
    new = [item for item in touching if _match(item, unmasked) is None]
    inside = {id(item): _inside(item["native"], regions) for item in touching}
    nearest_iou = {id(item): round(max([iou(item["box"], other["box"]) for other in unmasked] + [0.0]), 4)
                   for item in touching}
    return {
        "unmasked_clear": len(clear_u), "matched": len(matched), "lost": len(lost), "added": len(added),
        "label_changed": len(relabelled),
        "score_changed": sum(abs(delta) > SCORE_TOLERANCE for delta in deltas),
        "max_abs_score_delta": round(max(map(abs, deltas)), 4) if deltas else 0.0,
        "lost_marginal": sum(_near(item, diagnostic_masked, threshold) for item in lost),
        "added_marginal": sum(_near(item, diagnostic_unmasked, threshold) for item in added),
        "mask_overlap": len(touching), "mask_overlap_new": len(new),
        "mask_overlap_artifact_like": sum(inside[id(item)] >= ARTIFACT_INSIDE and
                                          nearest_iou[id(item)] < ARTIFACT_IOU for item in new),
        "eye_crossing": sum(item["reason"] == "eye_crossing" for item in masked),
        "unmasked_touching_region": sum(item["reason"] == "mask_overlap" for item in unmasked),
        "details": {"lost": [_brief(item) for item in lost], "added": [_brief(item) for item in added],
                    "label_changed": [[_brief(u), _brief(m)] for u, m in relabelled],
                    "score_changed": [[_brief(u), _brief(m)] for u, m in matched
                                      if abs(m["confidence"] - u["confidence"]) > SCORE_TOLERANCE],
                    "mask_overlap": [dict(_brief(item), new=item in new, inside_fraction=inside[id(item)],
                                          best_unmasked_iou=nearest_iou[id(item)]) for item in touching]}}


def _inside(native, regions):
    """The largest fraction of a native box's area that lies on one region."""
    area = (native[2] - native[0]) * (native[3] - native[1])
    best = 0
    for x0, y0, x1, y1 in regions:
        width = max(0, min(native[2], x1) - max(native[0], x0))
        best = max(best, width * max(0, min(native[3], y1) - max(native[1], y0)))
    return round(best / area, 4) if area else 0.0


def _brief(item):
    return {"label": item["label"], "confidence": item["confidence"], "native": list(item["native"])}


# Inputs.

def generated_scene(height, width, seed=0):
    """Deterministic textured BGR content for timing and leak checks."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (max(2, height // 16), max(2, width // 16), 3), dtype=np.uint8)
    smooth = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC).astype(np.int16)
    return np.clip(smooth + rng.integers(-24, 25, (height, width, 3)), 0, 255).astype(np.uint8)


def gray_sweep_geometry():
    """``(regions, eyes, shape, native size)`` of the real synthetic fixture calibration."""
    from .runtime.synthetic import synthetic_hud_calibration

    calibration = synthetic_hud_calibration()
    fmt = calibration.capture_format
    return (mask_regions(calibration), viewports(fmt, calibration.capture_layout),
            inference_shape(fmt.width, fmt.height), (fmt.width, fmt.height))


def seed_placements(width, height):
    """Footprints ``(rect, margin)`` pasted on a seed still, validated with the runtime's geometry rules.

    ``lower_right`` is the default HUD position: a footprint ``HUD_WIDTH_FRACTION``
    of the width at the tile's aspect, inset 5% from the right and 8% from the
    bottom, with the margin floor. ``lower_left``, ``upper_left`` and ``center``
    move the same footprint. ``fixture_scaled`` is the 96x64 fixture footprint
    and margin scaled to the still (the margin capped at the rule's ceiling),
    touching its lower-right corner unless capped.
    """
    footprint_w = max(1, round(HUD_WIDTH_FRACTION * width))
    footprint_h = max(1, round(footprint_w / HUD_ASPECT))
    right, bottom = width - round(0.05 * width), height - round(0.08 * height)
    left, top = round(0.05 * width), round(0.08 * height)
    cx, cy = width // 2, height // 2
    margin = MARGIN_MIN_PX
    placements = {
        "lower_right": ((right - footprint_w, bottom - footprint_h, right, bottom), margin),
        "lower_left": ((left, bottom - footprint_h, left + footprint_w, bottom), margin),
        "upper_left": ((left, top, left + footprint_w, top + footprint_h), margin),
        "center": ((cx - footprint_w // 2, cy - footprint_h // 2, cx - footprint_w // 2 + footprint_w,
                    cy - footprint_h // 2 + footprint_h), margin),
        "fixture_scaled": ((round(67 * width / 96), round(42 * height / 64), round(88 * width / 96),
                            round(56 * height / 64)),
                           min(MARGIN_MAX_PX, round(8 * width / 96), round(8 * height / 64))),
    }
    eye = (0, 0, width, height)
    for name, (rect, margin) in placements.items():
        region = expand(rect, margin)
        if not (margin_valid(margin) and contains(eye, rect) and contains(eye, region) and
                within_blind_budget((region,), eye)):
            raise ValueError(f"seed placement {name} breaks the mask geometry rules at {width}x{height}")
    return placements


PLACEMENTS = {
    "lower_right": "Default HUD position: footprint 16% of the width at the tile's 2.5:1 aspect, inset 5% right, "
                   "8% bottom, 8 px margin",
    "lower_left": "The same footprint mirrored to the lower left",
    "upper_left": "The same footprint at the upper left",
    "center": "The same footprint centred on the still",
    "fixture_scaled": "The 96x64 fixture footprint and margin scaled to the still, at its lower-right corner",
}


def load_seed_images(manifest_path, downloads, fetch=False, log=None):
    """``[(entry, bgr)]`` from a seed manifest, each image checked against its manifest SHA-256."""
    import cv2

    from . import assets, evaluation

    manifest = evaluation.load_manifest(manifest_path)
    remote = [entry for entry in manifest["images"] if "file" in entry]
    if fetch and remote:
        assets.fetch([{"name": entry["file"], "url": entry["url"], "sha256": entry["sha256"]} for entry in remote],
                     downloads, log=log or (lambda message: None))
    images = []
    for entry in manifest["images"]:
        location = evaluation.resolve_image(manifest, entry, downloads)
        if not location.is_file():
            raise FileNotFoundError(f"{entry['id']}: image not found at {location} (run with --fetch)")
        if entry.get("sha256") and assets.checksum(location) != entry["sha256"]:
            raise ValueError(f"{entry['id']}: image failed SHA-256 verification")
        image = cv2.imread(str(location), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"{entry['id']}: image could not be decoded")
        images.append((entry, image))
    if not images:
        raise ValueError("No seed images to evaluate")
    return images


# Evaluation runs.

def _detect(detector, image, threshold):
    return detector.detect(image, threshold).detections


def gray_sweep(detector, fills, levels=ODD_GRAY_LEVELS, threshold=THRESHOLD):
    """Per fill: the levels on which a detection touches the mask, with labels and confidences."""
    import numpy as np

    regions, eyes, shape, (width, height) = gray_sweep_geometry()
    unmasked_levels, results = [], {}
    near = {name: 0.0 for name in fills}
    for name in fills:
        results[name] = {"levels_with_mask_overlap": [], "labels": Counter(), "min_confidence": None,
                         "max_confidence": 0.0, "levels_with_eye_crossing": []}
    for level in levels:
        frame = np.full((height, width, 3), level, np.uint8)
        if classify(_detect(detector, unmasked_input(frame, shape), threshold), shape, width, height, regions, eyes):
            unmasked_levels.append(level)
        for name in fills:
            image = masked_input(frame, regions, eyes, FILLS[name][0], shape)
            found = classify(_detect(detector, image, threshold), shape, width, height, regions, eyes, threshold)
            touching = [item for item in found if item["reason"] == "mask_overlap"]
            result = results[name]
            if touching:
                result["levels_with_mask_overlap"].append(level)
                result["labels"].update(item["label"] for item in touching)
                scores = [item["confidence"] for item in touching]
                result["max_confidence"] = max(result["max_confidence"], *scores)
                result["min_confidence"] = min([result["min_confidence"] or 1.0, *scores])
            if any(item["reason"] == "eye_crossing" for item in found):
                result["levels_with_eye_crossing"].append(level)
            diagnostic = classify(_detect(detector, image, DIAGNOSTIC_FLOOR), shape, width, height, regions, eyes,
                                  DIAGNOSTIC_FLOOR)
            near[name] = max([near[name]] + [item["confidence"] for item in diagnostic
                                             if item["reason"] == "mask_overlap"])
    for name, result in results.items():
        result.update(levels=len(levels), mask_overlap_level_count=len(result["levels_with_mask_overlap"]),
                      labels=dict(sorted(result["labels"].items())),
                      max_confidence_touching_mask_at_floor=round(near[name], 4))
    return {"levels": list(levels), "region_native": [list(region) for region in regions],
            "native_size": [width, height], "unmasked_levels_with_detections": unmasked_levels, "fills": results}


def seed_comparison(detector, images, fills, threshold=THRESHOLD):
    """Per fill: mask-overlap and clear-detection changes over every seed still and placement, plus pin counts.

    Each placement's pin counts score the detector's whole output on the masked
    still, boxes touching the mask included. Its ``runtime_kept`` counts score
    only the boxes the runtime keeps (``runtime_kept``), which shows what the
    strict rule costs next to the HUD. Both compare with the unmasked pin.
    """
    from . import evaluation

    totals = {name: Counter() for name in fills}
    pins = {name: {} for name in fills}
    cases = []
    unmasked_scores = []
    scored = {name: {placement: [] for placement in PLACEMENTS} for name in fills}
    kept_scores = {name: {placement: [] for placement in PLACEMENTS} for name in fills}
    for entry, image in images:
        height, width = image.shape[:2]
        shape = inference_shape(width, height)
        eyes = ((0, 0, width, height),)
        scale = (shape[1] / width, shape[0] / height)
        plain = unmasked_input(image, shape)
        plain_found = _detect(detector, plain, threshold)
        unmasked_scores.append(evaluation.score_image(entry, plain_found, detector.classes, scale))
        plain_diagnostic = _detect(detector, plain, DIAGNOSTIC_FLOOR)
        for placement, (rect, margin) in seed_placements(width, height).items():
            regions = (expand(rect, margin),)
            unmasked = classify(plain_found, shape, width, height, regions, eyes, threshold)
            diagnostic_u = classify(plain_diagnostic, shape, width, height, regions, eyes, DIAGNOSTIC_FLOOR)
            for name in fills:
                masked_image = masked_input(image, regions, eyes, FILLS[name][0], shape)
                found = _detect(detector, masked_image, threshold)
                masked = classify(found, shape, width, height, regions, eyes, threshold)
                diagnostic_m = classify(_detect(detector, masked_image, DIAGNOSTIC_FLOOR), shape, width, height,
                                        regions, eyes, DIAGNOSTIC_FLOOR)
                result = compare(unmasked, masked, regions, diagnostic_u, diagnostic_m, threshold)
                scored[name][placement].append(evaluation.score_image(entry, found, detector.classes, scale))
                kept = runtime_kept(found, shape, width, height, regions, eyes, threshold)
                kept_scores[name][placement].append(evaluation.score_image(entry, kept, detector.classes, scale))
                counts = {key: value for key, value in result.items()
                          if key not in ("details", "max_abs_score_delta")}
                totals[name].update(counts)
                totals[name]["cases"] += 1
                totals[name]["cases_with_mask_overlap"] += bool(result["mask_overlap"])
                totals[name]["cases_with_new_mask_overlap"] += bool(result["mask_overlap_new"])
                totals[name]["cases_with_artifact_like_overlap"] += bool(result["mask_overlap_artifact_like"])
                totals[name]["max_abs_score_delta"] = max(totals[name]["max_abs_score_delta"],
                                                          result["max_abs_score_delta"])
                cases.append({"image": entry["id"], "placement": placement, "fill": name,
                              "region_native": list(regions[0]), **result})
    unmasked_pin = evaluation.pin_counts(evaluation.totals(unmasked_scores))

    def pin(scores):
        counts = evaluation.pin_counts(evaluation.totals(scores))
        return dict(counts, **{f"{key}_change": counts[key] - unmasked_pin[key]
                               for key in ("present_hits", "absent_violations")})
    for name in fills:
        for placement, scores in scored[name].items():
            pins[name][placement] = dict(pin(scores), runtime_kept=pin(kept_scores[name][placement]))
    summary = {name: dict(sorted(counter.items()), pin_counts=pins[name]) for name, counter in totals.items()}
    return {"images": [entry["id"] for entry, _ in images], "unmasked_pin_counts": unmasked_pin,
            "fills": summary, "cases": cases}


def leak_check(fill, seed=7):
    """Region content never reaches the native or resized input: random, HUD-like, black, white and inverted."""
    import cv2
    import numpy as np

    regions, eyes, shape, (width, height) = gray_sweep_geometry()
    geometries = [("fixture_96x64", generated_scene(height, width, seed), regions, eyes, shape)]
    big = generated_scene(1080, 1920, seed)
    for name in ("lower_right", "center"):
        rect, margin = seed_placements(1920, 1080)[name]
        geometries.append((f"1920x1080_{name}", big, (expand(rect, margin),), ((0, 0, 1920, 1080),),
                           inference_shape(1920, 1080)))
    rng = np.random.default_rng(seed)
    checked = failed = 0
    for _name, clean, regions, eyes, shape in geometries:
        reference_native = masked_input(clean, regions, eyes, fill, clean.shape[:2])
        reference = masked_input(clean, regions, eyes, fill, shape)
        for kind in ("random", "hud_text", "white", "black", "inverted"):
            changed = clean.copy()
            for x0, y0, x1, y1 in regions:
                if kind == "random":
                    changed[y0:y1, x0:x1] = rng.integers(0, 256, (y1 - y0, x1 - x0, 3), dtype=np.uint8)
                elif kind == "hud_text":
                    changed[y0:y1, x0:x1] = 0
                    cv2.putText(changed[y0:y1, x0:x1], "chair 0.93", (0, max(8, (y1 - y0) // 2)),
                                cv2.FONT_HERSHEY_SIMPLEX, .5, (235, 235, 235), 1, cv2.LINE_AA)
                elif kind in ("white", "black"):
                    changed[y0:y1, x0:x1] = 255 if kind == "white" else 0
                else:
                    changed[y0:y1, x0:x1] = 255 - clean[y0:y1, x0:x1]
            checked += 1
            same = (np.array_equal(masked_input(changed, regions, eyes, fill, clean.shape[:2]), reference_native) and
                    np.array_equal(masked_input(changed, regions, eyes, fill, shape), reference))
            failed += not same
    return {"cases": checked, "failed": failed, "passed": failed == 0}


def _percentiles(samples):
    ordered = sorted(samples)

    def rank(fraction):
        return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 3)
    return {"samples": len(ordered), "p50": rank(.5), "p95": rank(.95), "max": round(ordered[-1], 3)}


def timing(fills, runs=30, seed=3):
    """Milliseconds per frame on this CPU for the fill alone and for the whole preparation (runner CPU, not Frame)."""
    import numpy as np

    regions, eyes, shape, (width, height) = gray_sweep_geometry()
    rect, margin = seed_placements(1920, 1080)["lower_right"]
    cases = {
        "96x64_fixture": (generated_scene(height, width, seed), regions, eyes, shape),
        "1920x1080_lower_right": (generated_scene(1080, 1920, seed), (expand(rect, margin),), ((0, 0, 1920, 1080),),
                                  inference_shape(1920, 1080)),
        "1920x1080_blind_budget_max": (generated_scene(1080, 1920, seed), (BLIND_BUDGET_REGION,),
                                       ((0, 0, 1920, 1080),), inference_shape(1920, 1080)),
    }
    report = {}
    for case, (frame, regions, eyes, shape) in cases.items():
        area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in regions)
        report[case] = {"native_size": [frame.shape[1], frame.shape[0]],
                        "region_native": [list(region) for region in regions], "region_px": area, "fills": {}}
        for name in fills:
            fill = FILLS[name][0]
            fill_ms, prepare_ms = [], []
            for run in range(runs + 2):  # Two untimed warm-up runs.
                image = np.array(frame, copy=True)
                for x0, y0, x1, y1 in regions:
                    image[y0:y1, x0:x1] = 0
                started = time.perf_counter()
                fill(image, regions, eyes)
                filled = time.perf_counter()
                masked_input(frame, regions, eyes, fill, shape)
                prepared = time.perf_counter()
                if run >= 2:
                    fill_ms.append((filled - started) * 1000)
                    prepare_ms.append((prepared - filled) * 1000)
            report[case]["fills"][name] = {"fill_ms": _percentiles(fill_ms), "prepare_ms": _percentiles(prepare_ms)}
        report[case]["runtime_hud_mask_prepare_ms"] = _runtime_prepare_ms(frame, regions, runs)
    return report


def _runtime_prepare_ms(frame, regions, runs):
    """``HudMask.prepare`` itself (validation, zeroing, count, fill, resize) on a generated mono frame."""
    from .hud.contract import CaptureFormat, Footprint
    from .hud.mask import HudMask
    from .runtime.synthetic import fixture_calibration

    height, width = frame.shape[:2]
    stride = width * 3
    fmt = CaptureFormat(width, height, "BGR3", stride, stride * height, stride * height)
    (x0, y0, x1, y1), = regions
    margin = MARGIN_MIN_PX  # The footprint is the region less the margin floor.
    footprint = Footprint(0, (x0 + margin, y0 + margin, x1 - margin, y1 - margin), margin)
    mask = HudMask(fixture_calibration("synthetic:fill-timing", fmt, "mono", (footprint,)), source_kind="synthetic")
    if mask.regions != tuple(regions):
        raise ValueError("timing calibration does not reproduce the timed region")
    context = dict(source_id=mask.calibration.source_id, capture_format=fmt, capture_layout="mono")
    samples = []
    for run in range(runs + 2):
        started = time.perf_counter()
        mask.prepare(frame, **context)
        if run >= 2:
            samples.append((time.perf_counter() - started) * 1000)
    return _percentiles(samples)


def deterministic(fill, seed=5):
    """The same input gives byte-identical output twice, at 96x64 and at 1920x1080."""
    import numpy as np

    regions, eyes, shape, (width, height) = gray_sweep_geometry()
    rect, margin = seed_placements(1920, 1080)["lower_right"]
    cases = ((generated_scene(height, width, seed), regions, eyes, shape),
             (generated_scene(1080, 1920, seed), (expand(rect, margin),), ((0, 0, 1920, 1080),),
              inference_shape(1920, 1080)))
    return all(np.array_equal(masked_input(frame, regions, eyes, fill, shape),
                              masked_input(frame, regions, eyes, fill, shape))
               for frame, regions, eyes, shape in cases)


def evaluate(detectors, images, fills=tuple(FILLS), levels=ODD_GRAY_LEVELS, threshold=THRESHOLD, timing_runs=30):
    """The report body: per fill and detector, the gray sweep, the seed comparison, and timing and leak checks.

    ``detectors`` maps names to detector objects; ``images`` is
    ``load_seed_images``'s list (it may be empty to skip the seed comparison).
    """
    unknown = [name for name in fills if name not in FILLS]
    if unknown:
        raise ValueError(f"Unknown fill(s): {', '.join(unknown)}")
    report = {"runtime_fill": RUNTIME_FILL, "threshold": threshold, "diagnostic_floor": DIAGNOSTIC_FLOOR,
              "score_tolerance": SCORE_TOLERANCE, "match_iou": MATCH_IOU,
              "fills": {name: {"description": FILLS[name][1], "leak_check": leak_check(FILLS[name][0]),
                               "deterministic": deterministic(FILLS[name][0])} for name in fills},
              "placements": PLACEMENTS, "gray_sweep": {}, "seeds": {}}
    for name, detector in detectors.items():
        report["gray_sweep"][name] = gray_sweep(detector, fills, levels, threshold)
        if images:
            report["seeds"][name] = seed_comparison(detector, images, fills, threshold)
    report["timing"] = {"unit": "milliseconds per frame", "runs": timing_runs,
                        "scope": "runner CPU; not Frame", "cases": timing(fills, timing_runs)}
    return report


def summary(report):
    """One row per detector and fill: the headline numbers the documentation quotes."""
    rows = []
    for detector, sweep in report["gray_sweep"].items():
        seeds = report["seeds"].get(detector, {}).get("fills", {})
        for name, result in sweep["fills"].items():
            seed = seeds.get(name, {})
            row = {"detector": detector, "fill": name,
                   "gray_levels_with_mask_overlap": f"{result['mask_overlap_level_count']}/{result['levels']}",
                   "gray_max_confidence_at_mask": result["max_confidence"]}
            if seed:
                row.update({key: seed.get(key, 0) for key in (
                    "cases", "cases_with_mask_overlap", "cases_with_new_mask_overlap",
                    "cases_with_artifact_like_overlap", "mask_overlap", "mask_overlap_new",
                    "mask_overlap_artifact_like", "unmasked_clear", "matched", "lost", "lost_marginal", "added",
                    "added_marginal", "label_changed", "score_changed", "max_abs_score_delta")})
            rows.append(row)
    return rows
