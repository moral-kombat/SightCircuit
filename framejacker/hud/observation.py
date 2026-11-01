"""Empirical marker-copy observation in native capture pixels; never a calibration.

Finds complete copies of the fixed calibration marker (``marker_template``) in a
native-resolution mirror read, rejects partial, orphan, ambiguous or overlapping
copies, requires a changed clear baseline before the marker is shown
(``BaselineGate``) and bounds stable copy positions across reads
(``EmpiricalObservation``). Results are unqualified: no eye identity,
composition or unseen copy is inferred, so ``mapping_qualified`` stays False.
Used by ``scripts/hud_marker_observer.py``.
"""
from dataclasses import dataclass
import hashlib
import itertools
import math

from ..convert import buffer_bgr
from ..gates import HARDWARE_MAPPING, claims
from .contract import HUD_CONFIG, CaptureFormat

MAX_PIXELS = 8 * 1024 * 1024
MAX_CONTOURS = 2048
MAX_QUADS = 256
MAX_COPIES = 4
MAX_DIAGNOSTIC_CANDIDATES = 4
MAX_DIAGNOSTIC_FRAGMENTS = 16
MAX_CORNER_REFINEMENT_PX = 2


def marker_template():
    """Test/reference pixels matching the finalized native marker, not a frame read."""
    import numpy as np
    image = np.full((128, 320), 32, dtype=np.uint8)
    image[:4] = image[-4:] = 255
    image[:, :4] = image[:, -4:] = 255
    for tag in range(4):
        x, y = (288 if tag % 2 else 8), (96 if tag // 2 else 8)
        image[y:y+24, x:x+24] = 0
        image[y+2:y+22, x+2:x+22] = 255
        for bit in range(4):
            bx, by = x+4+(bit % 2)*8, y+4+(bit // 2)*8
            image[by:by+6, bx:bx+6] = 255 if ((tag+1) >> bit) & 1 else 0
    return image


@dataclass(frozen=True)
class ObserverConfig:
    margin_px: int = 16
    maximum_drift_px: int = 8
    expected_copies: object = None
    config_id: str = HUD_CONFIG.layout_id

    def validate(self):
        if (type(self.margin_px) is not int or not 8 <= self.margin_px <= 128 or
                type(self.maximum_drift_px) is not int or not 0 <= self.maximum_drift_px <= self.margin_px // 2 or
                (self.expected_copies is not None and
                 (type(self.expected_copies) is not int or not 1 <= self.expected_copies <= MAX_COPIES)) or
                self.config_id != HUD_CONFIG.layout_id):
            raise ValueError("observer_config_invalid")


def native_bgr(data, fmt):
    """Native BGR pixels of one raw mirror read and its validated capture format.

    Uses ``convert.buffer_bgr``, the same pixel rule the HUD mask is applied with,
    and returns a contiguous array so frames can be hashed and compared.
    """
    import numpy as np
    descriptor = CaptureFormat.from_mapping(fmt)
    descriptor.validate()
    if descriptor.width * descriptor.height > MAX_PIXELS:
        raise ValueError("observer_pixel_budget")
    if len(data) < descriptor.minimum_buffer_bytes:
        raise ValueError("observer_short_frame")
    image = buffer_bgr(bytes(data), descriptor.fourcc, descriptor.width, descriptor.height, descriptor.stride_bytes)
    return np.ascontiguousarray(image), descriptor


def locate_copies(bgr, *, diagnostics=None):
    """Strict pattern matching; returns tag-ordered native corners, not eyes.

    Unresolvable copies can be missed. Even a clean result remains empirical
    and unqualified. Recognizable orphan tags explicitly reject partial copies.
    """
    import cv2
    import numpy as np
    details = {} if diagnostics is None else diagnostics
    details.clear()
    details.update(schema_version=1, scope="Unqualified pattern candidates, not HUD or eye identities",
                   coordinate_space="native_capture_pixels", status="analyzing", scan_complete=False,
                   contour_count=0, quad_count=0, eligible_outline_count=0, refined_outline_count=0,
                   complete_copy_count=0, tag_fragment_count=0, orphan_fragment_count=0,
                   best_candidates=[], fragment_quads_native=[], complete_copy_corners_native=[])

    def reject(reason):
        details.update(status="rejected", reason=reason)
        raise ValueError(reason)

    if (bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or
            bgr.shape[0] * bgr.shape[1] > MAX_PIXELS):
        reject("observer_image_invalid")
    details.update(width=bgr.shape[1], height=bgr.shape[0])
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    contours, _ = cv2.findContours((gray >= 180).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    details["contour_count"] = len(contours)
    if len(contours) > MAX_CONTOURS:
        reject("observer_contour_budget")
    quads = []
    for contour in contours:
        if cv2.contourArea(contour) < 25:
            continue
        poly = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
        if len(poly) == 4 and cv2.isContourConvex(poly):
            quads.append(poly[:, 0].astype(np.float32))
            details["quad_count"] = len(quads)
            if len(quads) > MAX_QUADS:
                reject("observer_quad_budget")
    template = marker_template().astype(np.float32)
    target = np.float32([[0, 0], [319, 0], [319, 127], [0, 127]])
    tags = [template[y+2:y+22, x+2:x+22] for x, y in ((8, 8), (288, 8), (8, 96), (288, 96))]

    def pattern_metrics(warped, whole_error):
        outline = float(min(warped[:3].mean(), warped[-3:].mean(), warped[:, :3].mean(), warped[:, -3:].mean()))
        tag_error = float(max(np.mean(np.abs(warped[y:y+24, x:x+24] - template[y:y+24, x:x+24]))
                              for x, y in ((8, 8), (288, 8), (8, 96), (288, 96))))
        bit_error = float(max(np.mean(np.abs(warped[y+5+(bit//2)*8:y+9+(bit//2)*8,
                                                  x+5+(bit%2)*8:x+9+(bit%2)*8] -
                                             template[y+5+(bit//2)*8:y+9+(bit//2)*8,
                                                      x+5+(bit%2)*8:x+9+(bit%2)*8]))
                              for x, y in ((8, 8), (288, 8), (8, 96), (288, 96)) for bit in range(4)))
        failures = (["whole_pattern_error"] if whole_error > 13 else [])
        failures += ["outline_brightness"] if outline < 210 else []
        failures += ["tag_pattern_error"] if tag_error > 45 else []
        failures += ["code_bit_error"] if bit_error > 30 else []
        return {"whole_pattern_mae": round(whole_error, 3), "outline_min_mean": round(outline, 3),
                "maximum_tag_mae": round(tag_error, 3), "maximum_bit_mae": round(bit_error, 3),
                "failed_checks": failures}

    def evaluate_outline(quad, geometry):
        matches, best = [], None
        for order in (quad, quad[::-1]):
            for shift in range(4):
                ordered = np.roll(order, shift, axis=0).copy()
                warped = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(ordered, target),
                                             (320, 128)).astype(np.float32)
                whole_error = float(np.mean(np.abs(warped - template)))
                if best is None or whole_error < best[0]:
                    best = (whole_error, ordered, warped, geometry)
                if whole_error <= 13 and not pattern_metrics(warped, whole_error)["failed_checks"]:
                    matches.append(ordered)
        return matches, best

    found, fragments = [], []
    for quad in quads:
        edges = np.linalg.norm(quad - np.roll(quad, 1, axis=0), axis=1)
        # A white inner tag is a separate contour. Its complete code must match.
        if min(edges) >= 6 and max(edges) / min(edges) < 2.5:
            tag_image = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(quad,
                np.float32([[0, 0], [19, 0], [19, 19], [0, 19]])), (20, 20)).astype(np.float32)
            # Orphan detection is deliberately more conservative than complete
            # copy acceptance: aliased tag borders must not conceal a partial
            # second copy. Still require each bit, not just an average score.
            if any(np.mean(np.abs(rotated - tag)) < 50 and all(
                       np.mean(np.abs(rotated[3+(bit//2)*8:7+(bit//2)*8, 3+(bit%2)*8:7+(bit%2)*8] -
                                      tag[3+(bit//2)*8:7+(bit//2)*8, 3+(bit%2)*8:7+(bit%2)*8])) < 80
                       for bit in range(4))
                   for rotation in range(4) for rotated in (np.rot90(tag_image, rotation),) for tag in tags):
                fragments.append(quad)
        if min(edges) < 32 or max(edges) < 80:
            continue
        details["eligible_outline_count"] += 1
        matches, best = evaluate_outline(quad, "raster_contour")
        refinement_shift = 0.0
        # Rasterized rotated corners can be displaced by a pixel. Refine only
        # failed candidates, in a fixed local window, without inferring missing
        # corners or relaxing any outline, tag, or individual code-bit check.
        if not matches and (quad[:, 0].min() >= 2 and quad[:, 1].min() >= 2 and
                            quad[:, 0].max() < gray.shape[1]-2 and quad[:, 1].max() < gray.shape[0]-2):
            refined = cv2.cornerSubPix(gray, quad.reshape(4, 1, 2).copy(), (2, 2), (-1, -1),
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, .01))[:, 0]
            shift = float(np.max(np.linalg.norm(refined - quad, axis=1)))
            if (np.isfinite(refined).all() and shift <= MAX_CORNER_REFINEMENT_PX and
                    cv2.isContourConvex(refined) and refined[:, 0].min() >= 0 and refined[:, 1].min() >= 0 and
                    refined[:, 0].max() < gray.shape[1] and refined[:, 1].max() < gray.shape[0]):
                details["refined_outline_count"] += 1
                refined_matches, refined_best = evaluate_outline(refined, "bounded_subpixel_refinement")
                if refined_matches or refined_best[0] < best[0]:
                    best, refinement_shift = refined_best, shift
                matches = refined_matches
        details["best_candidates"].append(dict(pattern_metrics(best[2], best[0]),
            quad_native=best[1].round(3).tolist(), geometry=best[3], raw_quad_native=quad.round(3).tolist(),
            refinement_shift_px=round(refinement_shift, 3)))
        details["best_candidates"].sort(key=lambda candidate: candidate["whole_pattern_mae"])
        del details["best_candidates"][MAX_DIAGNOSTIC_CANDIDATES:]
        if len(matches) > 1:
            reject("ambiguous_marker_orientation")
        if matches:
            corners = matches[0]
            if any(np.max(np.abs(corners - previous)) <= 3 for previous in found):
                continue  # Same raster border, not an additional copy.
            found.append(corners)
            if len(found) > MAX_COPIES:
                reject("observer_copy_budget")
    orphans = [fragment for fragment in fragments
               if not any(all(cv2.pointPolygonTest(copy, tuple(map(float, point)), True) >= -2 for point in fragment)
                          for copy in found)]
    details.update(scan_complete=True, complete_copy_count=len(found), tag_fragment_count=len(fragments),
                   orphan_fragment_count=len(orphans),
                   best_candidates_truncated=details["eligible_outline_count"] > MAX_DIAGNOSTIC_CANDIDATES,
                   complete_copy_corners_native=[copy.round(3).tolist() for copy in found],
                   # Orphans first so an incomplete extra copy cannot be hidden
                   # by the metadata cap. Counts still describe all candidates.
                   fragment_quads_native=[fragment.round(3).tolist() for fragment in
                                          (orphans or fragments)[:MAX_DIAGNOSTIC_FRAGMENTS]],
                   fragment_quads_scope="orphans" if orphans else "all_matched_fragments",
                   fragment_quads_truncated=len(orphans or fragments) > MAX_DIAGNOSTIC_FRAGMENTS)
    if orphans:
        reject("orphan_marker_tag_or_incomplete_copy")
    for a, copy in enumerate(found):
        for other in found[a+1:]:
            area, _ = cv2.intersectConvexConvex(copy, other)
            if area > 0:
                reject("ambiguous_overlapping_marker_copies")
    details["status"] = "complete_copies" if found else "no_complete_copy"
    return sorted(found, key=lambda quad: tuple(quad.mean(axis=0)))


class BaselineGate:
    """Bounded evidence of a changed, clear baseline; never proof of exposure freshness."""
    def __init__(self, deadline):
        self.deadline = deadline
        self.previous_digest = self.clear_digest = None
        self.initial_diagnostics = self.last_diagnostics = None
        self.report = {"established": False, "frames_examined": 0, "content_changes": 0,
                       "repeated_frames": 0, "marker_like_frames": 0, "clear_frames": 0,
                       "required_distinct_clear_frames": 2, "camera_freshness_verified": False}

    def check_deadline(self, now):
        if not self.report["established"] and now >= self.deadline:
            raise ValueError("baseline_distinct_clear_frames_not_established")

    def accept(self, image, sequence, observed_ns, now):
        self.check_deadline(now)
        details, blocked = {}, False
        try:
            blocked = bool(locate_copies(image, diagnostics=details))
        except ValueError as error:
            if str(error) not in ("orphan_marker_tag_or_incomplete_copy", "ambiguous_marker_orientation",
                                  "ambiguous_overlapping_marker_copies"):
                raise
            blocked = True
        finally:
            details.update(sequence=sequence, observed_ns=observed_ns)
            self.last_diagnostics = details
            if self.initial_diagnostics is None:
                self.initial_diagnostics = details
        digest = hashlib.blake2b(memoryview(image), digest_size=16).digest()
        self.report["frames_examined"] += 1
        if self.previous_digest is not None:
            self.report["repeated_frames" if digest == self.previous_digest else "content_changes"] += 1
        self.previous_digest = digest
        if blocked:
            self.report["marker_like_frames"] += 1
            self.clear_digest = None
            return False
        self.report["clear_frames"] += 1
        if self.clear_digest is not None and self.clear_digest != digest:
            self.report["established"] = True
        self.clear_digest = digest
        return self.report["established"]


class EmpiricalObservation:
    def __init__(self, config=ObserverConfig()):
        config.validate()
        self.config = config
        self.descriptor = self.first = self.unions = None
        self.samples = []
        self.maximum_drift = 0.0
        self.last_corners = []
        self.last_match_diagnostics = {}

    def accept(self, image, descriptor, sequence, observed_ns, *, config_id):
        import numpy as np
        # A rejected context or match must not present previous-frame corners.
        self.last_corners = []
        self.last_match_diagnostics = {"sequence": sequence, "observed_ns": observed_ns, "status": "context_rejected"}
        self.config.validate()
        descriptor.validate()
        if config_id != self.config.config_id:
            raise ValueError("observer_config_changed")
        if self.descriptor is not None and descriptor != self.descriptor:
            raise ValueError("observer_format_changed")
        if (type(sequence) is not int or type(observed_ns) is not int or observed_ns < 0 or
                (self.samples and (sequence <= self.samples[-1][0] or observed_ns <= self.samples[-1][1]))):
            raise ValueError("observer_sequence_or_time_changed")
        try:
            copies = locate_copies(image, diagnostics=self.last_match_diagnostics)
        finally:
            self.last_match_diagnostics.update(sequence=sequence, observed_ns=observed_ns)
        self.last_corners = [copy.round(3).tolist() for copy in copies]
        if not copies:
            raise ValueError("no_complete_marker")
        if self.config.expected_copies is not None and len(copies) != self.config.expected_copies:
            raise ValueError("expected_copy_count_mismatch")
        if self.first is None:
            self.descriptor = descriptor
            self.first = [copy.copy() for copy in copies]
            self.unions = [[float(copy[:, 0].min()), float(copy[:, 1].min()),
                            float(copy[:, 0].max()+1), float(copy[:, 1].max()+1)] for copy in copies]
        if len(copies) != len(self.first):
            raise ValueError("marker_copy_count_changed")
        assignments = [order for order in itertools.permutations(copies)
                       if all(np.max(np.abs(copy - initial)) <= self.config.maximum_drift_px
                              for copy, initial in zip(order, self.first))]
        if len(assignments) != 1:
            raise ValueError("unstable_marker_movement_or_assignment")
        copies = assignments[0]
        for index, copy in enumerate(copies):
            drift = float(np.max(np.abs(copy - self.first[index])))
            self.maximum_drift = max(self.maximum_drift, drift)
            if drift > self.config.maximum_drift_px:
                raise ValueError("unstable_marker_movement_or_assignment")
            union = self.unions[index]
            union[:] = [min(union[0], float(copy[:, 0].min())), min(union[1], float(copy[:, 1].min())),
                        max(union[2], float(copy[:, 0].max()+1)), max(union[3], float(copy[:, 1].max()+1))]
        regions = self.regions()
        if any(x0 < 0 or y0 < 0 or x1 > descriptor.width or y1 > descriptor.height for x0, y0, x1, y1 in regions):
            raise ValueError("marker_margin_escapes_capture")
        if len(self.samples) >= 100:
            raise ValueError("observer_sample_budget")
        self.samples.append([sequence, observed_ns, [copy.round(3).tolist() for copy in copies]])

    def regions(self):
        m = self.config.margin_px
        return [[math.floor(x0)-m, math.floor(y0)-m, math.ceil(x1)+m, math.ceil(y1)+m]
                for x0, y0, x1, y1 in self.unions or []]

    def evidence(self):
        return {"sample_count": len(self.samples), "observed_copy_count": len(self.first or []),
                "footprint_union_native": self.regions(), "samples": self.samples,
                "maximum_corner_drift_px": self.maximum_drift, "margin_px": self.config.margin_px,
                "last_detected_corners": self.last_corners,
                "last_match_diagnostics": self.last_match_diagnostics,
                "eye_identity_assigned": False, "composition_verified": False,
                "unseen_or_occluded_copies_established": False, **claims(HARDWARE_MAPPING)}
