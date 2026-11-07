"""Empirical marker-copy observation in native capture pixels; never a calibration.

Finds complete copies of the fixed calibration marker (``marker_template``) in a
native-resolution mirror read, rejects partial, orphan, ambiguous or overlapping
copies, requires a changed clear baseline before the marker is shown
(``BaselineGate``) and bounds stable copy positions across reads
(``EmpiricalObservation``). Results are unqualified: no eye identity,
composition or unseen copy is inferred, so ``mapping_qualified`` stays False.
Used by ``scripts/hud_marker_observer.py``. ``validate_observer_report`` is
that command's worker report contract, applied by its supervisor and before a
report becomes a mapping candidate (``sightcircuit.hud.candidate``). Every
worker report carries ``session_tokens`` (``validate_session_tokens``): the
report of the run's ``sources.session.MirrorSession`` when the observer was
given one, otherwise why none was recorded. The tokens identify the run; they
qualify nothing. The scene precondition (``scene_census``,
``untraced_rejection``, ``bright_contours``, ``candidate_quads``,
``outline_eligible`` and their limits) lives in ``sightcircuit.hud.scene``; a
frame rejected by any of its checks carries its ``scene_statistics`` in the
match diagnostics, and a report rejected there carries them, once, with their
source (``validate_rejection_scene_statistics``). A failing statistics call
leaves ``scene_statistics_error`` instead, as does a traced contour count that
disagrees with the census (``observer_contour_census_mismatch``).
"""
from dataclasses import dataclass
import hashlib
import itertools
import math
import sys

from ..convert import buffer_bgr
from ..gates import HARDWARE_MAPPING, claims
from .contract import (HUD_CONFIG, MARKER, MARKER_MAX_SECONDS, MARKER_MIN_SECONDS, SESSION_TOKENS, CaptureFormat,
                       margin_valid)
from .marker import new_report as marker_report, validate_worker_report as validate_marker_report
# The scene precondition and its budgets live in ``scene``; re-exported here for the observer's callers.
from .scene import (MAX_CONTOURS, MAX_OUTLINES, MAX_PIXELS, MAX_QUADS, bright_contours,  # noqa: F401
                    candidate_quads, outline_eligible, quad_edges, scene_census, scene_statistics,
                    untraced_rejection, validate_scene_statistics)

MAX_COPIES = 4
MAX_DIAGNOSTIC_CANDIDATES = 4
MAX_DIAGNOSTIC_FRAGMENTS = 16
MAX_CORNER_REFINEMENT_PX = 2
TAG_ORIGINS = tuple(MARKER.tag_origin(tag) for tag in range(len(MARKER.corner_ids)))
# Why a worker report's ``session_tokens`` holds no session report.
SESSION_TOKENS_NOT_REQUESTED = "session_tokens_not_requested"  # No session: the default observer run.
# The supervisor kept its base report: no worker report arrived, the worker's report failed this contract
# (any tokens it carried are discarded with it) or the guardian failed. The supervisor report's status and
# reason say which.
SESSION_TOKENS_NOT_REPORTED = "session_tokens_not_reported"
SESSION_TOKENS_DECLARATION_FAILED = "session_tokens_declaration_failed"
# The run stopped at a preparation check before the declaration (the bridge is not built), so nothing was read
# under /proc; the report's reason names the stop.
SESSION_TOKENS_NOT_DECLARED = "session_tokens_not_declared"
SESSION_TOKENS_REPORT_INVALID = "session_tokens_report_invalid"  # The session report raised or failed this contract.
UNRECORDED_SESSION_REASONS = (SESSION_TOKENS_NOT_REQUESTED, SESSION_TOKENS_NOT_REPORTED,
                              SESSION_TOKENS_DECLARATION_FAILED, SESSION_TOKENS_NOT_DECLARED,
                              SESSION_TOKENS_REPORT_INVALID)
# ``MirrorSession.report()`` fields, plus ``recorded`` and the declaration's duration.
RECORDED_SESSION_FIELDS = ("recorded", "declared", "undeclared", "frames_read", "first_change", "runtime_process",
                           "scope", "declaration_elapsed_ms")
STRING_SESSION_TOKENS = tuple(name for name in SESSION_TOKENS if name != "recenter_epoch")
RUNTIME_PROCESS_FIELDS = {"pid": int, "start_ticks": int, "executable": str, "executable_deleted": bool}
MAX_SESSION_TEXT = 256  # Characters in a token, reason or scope; the record's token rule is stricter.
MAX_EXECUTABLE_PATH = 4096


def bit_sample(bit, inset=1, offset=0):
    """Rows and columns of code bit ``bit``, relative to its tag's origin plus
    ``offset`` pixels, shrunk by ``inset`` per side so resampled edges are not scored."""
    x, y = MARKER.bit_origin(bit)
    low, high = inset - offset, MARKER.bit_size_px - inset - offset
    return slice(y + low, y + high), slice(x + low, x + high)


def marker_template():
    """Reference pixels of the native marker, drawn from the shared ``MARKER``; not a frame read."""
    import numpy as np
    m, size, outline = MARKER, MARKER.tag_size_px, MARKER.tag_outline_px
    image = np.full((HUD_CONFIG.height_px, HUD_CONFIG.width_px), m.background_value, dtype=np.uint8)
    image[:m.border_px] = image[-m.border_px:] = m.border_value
    image[:, :m.border_px] = image[:, -m.border_px:] = m.border_value
    for tag, (x, y) in enumerate(TAG_ORIGINS):
        image[y:y+size, x:x+size] = m.tag_outline_value
        image[y+outline:y+size-outline, x+outline:x+size-outline] = m.tag_fill_value
        for bit in range(m.bits):
            rows, columns = bit_sample(bit, inset=0)
            image[y:y+size, x:x+size][rows, columns] = m.bit_set_value if m.bit_set(tag, bit) else m.bit_clear_value
    return image


@dataclass(frozen=True)
class ObserverConfig:
    margin_px: int = 16
    maximum_drift_px: int = 8
    expected_copies: object = None
    config_id: str = HUD_CONFIG.layout_id

    def validate(self):
        if (not margin_valid(self.margin_px) or
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

    def reject_scene(reason):
        # The frame's precondition statistics (counts only, never pixels) explain a budget rejection.
        # They can never replace or hide it: the reason is recorded first and stays the one raised.
        details.update(status="rejected", reason=reason)
        try:
            details["scene_statistics"] = scene_statistics(bgr, gray=gray, census=census, contours=contours)
        except Exception as error:
            details["scene_statistics_error"] = type(error).__name__
        raise ValueError(reason)

    if (bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or
            bgr.shape[0] * bgr.shape[1] > MAX_PIXELS):
        reject("observer_image_invalid")
    details.update(width=bgr.shape[1], height=bgr.shape[0])
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # The scene precondition (sightcircuit.hud.scene), in its order: saturation, speck density and the
    # contour budget from the census before anything is traced, then the quad and outline budgets.
    contours = None
    census = scene_census(gray)
    details["contour_count"] = census["contour_count"]
    rejection = untraced_rejection(census, gray.size)
    if rejection is not None:
        reject_scene(rejection)
    contours = bright_contours(gray)
    if len(contours) != census["contour_count"]:  # The census is exact; this backs it against a library change.
        # Every check so far, and statistics built on the census, rest on that count, so none are attached.
        details.update(contour_count=len(contours), scene_statistics_error="census_disagrees_with_trace")
        reject("observer_contour_census_mismatch")
    quads = []
    for _, quad in candidate_quads(contours):
        if quad is not None:
            quads.append(quad)
            details["quad_count"] = len(quads)
            if len(quads) > MAX_QUADS:
                reject_scene("observer_quad_budget")
    eligible = [outline_eligible(quad_edges(quad)) for quad in quads]
    details["eligible_outline_count"] = sum(eligible)
    if details["eligible_outline_count"] > MAX_OUTLINES:
        reject_scene("observer_outline_budget")
    template = marker_template().astype(np.float32)
    width, height = HUD_CONFIG.width_px, HUD_CONFIG.height_px
    size, outline_px = MARKER.tag_size_px, MARKER.tag_outline_px
    inner = size - 2 * outline_px  # The bright square inside a tag's outline.
    target = np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]])
    tags = [template[y+outline_px:y+size-outline_px, x+outline_px:x+size-outline_px] for x, y in TAG_ORIGINS]

    def pattern_metrics(warped, whole_error):
        outline = float(min(warped[:3].mean(), warped[-3:].mean(), warped[:, :3].mean(), warped[:, -3:].mean()))
        tag_error = float(max(np.mean(np.abs(warped[y:y+size, x:x+size] - template[y:y+size, x:x+size]))
                              for x, y in TAG_ORIGINS))
        bit_error = float(max(np.mean(np.abs(warped[y:y+size, x:x+size][bit_sample(bit)] -
                                             template[y:y+size, x:x+size][bit_sample(bit)]))
                              for x, y in TAG_ORIGINS for bit in range(MARKER.bits)))
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
                                             (width, height)).astype(np.float32)
                whole_error = float(np.mean(np.abs(warped - template)))
                if best is None or whole_error < best[0]:
                    best = (whole_error, ordered, warped, geometry)
                if whole_error <= 13 and not pattern_metrics(warped, whole_error)["failed_checks"]:
                    matches.append(ordered)
        return matches, best

    found, fragments = [], []
    for quad, is_outline in zip(quads, eligible):
        edges = quad_edges(quad)
        # A white inner tag is a separate contour. Its complete code must match.
        if min(edges) >= 6 and max(edges) / min(edges) < 2.5:
            tag_image = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(quad,
                np.float32([[0, 0], [inner-1, 0], [inner-1, inner-1], [0, inner-1]])), (inner, inner)).astype(np.float32)
            # Orphan detection is deliberately more conservative than complete
            # copy acceptance: aliased tag borders must not conceal a partial
            # second copy. Still require each bit, not just an average score.
            if any(np.mean(np.abs(rotated - tag)) < 50 and all(
                       np.mean(np.abs(rotated[bit_sample(bit, offset=outline_px)] -
                                      tag[bit_sample(bit, offset=outline_px)])) < 80
                       for bit in range(MARKER.bits))
                   for rotation in range(4) for rotated in (np.rot90(tag_image, rotation),) for tag in tags):
                fragments.append(quad)
        if not is_outline:
            continue
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
        # Reads are numbered consecutively; a skipped number is a dropped frame.
        if self.samples and sequence != self.samples[-1][0] + 1:
            raise ValueError("observer_sequence_gap")
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


def validate_marker_evidence(bridge, seconds, validation_only):
    """Reuse the finalized marker deadline contract, then pin every HUD setting.

    The bridge report and the native objects the contract reads must be JSON
    objects, so a malformed report is a ``ValueError``, never another exception.
    """
    native = bridge.get("native_report") if type(bridge) is dict else None
    if type(native) is not dict or any(type(native.get(key, {})) is not dict
                                       for key in ("marker", "visibility_after_submit")):
        raise ValueError("invalid_marker_worker_report")
    report = marker_report(validation_only)
    report.update(status="completed", exit_code=0, cleanup_ok=True, seconds_requested=seconds, bridge=bridge)
    validate_marker_report(report, 0, validation_only=validation_only)
    marker = bridge["native_report"].get("marker") or {}
    expected = {"width": HUD_CONFIG.width_px, "height": HUD_CONFIG.height_px,
                "opaque_pixels": HUD_CONFIG.width_px * HUD_CONFIG.height_px,
                "corner_ids": list(MARKER.corner_ids), "border_pixels": MARKER.border_px,
                "tag_size_pixels": MARKER.tag_size_px, "tag_inset_pixels": MARKER.tag_inset_px,
                "config_id": HUD_CONFIG.layout_id, "corner_order": MARKER.corner_order,
                "visible_in_dashboard": True}
    if (any(marker.get(key) != value for key, value in expected.items()) or
            any(type(marker.get(key)) not in (int, float) or
                not math.isclose(marker[key], getattr(HUD_CONFIG, key), rel_tol=0, abs_tol=1e-6)
                for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"))):
        raise ValueError("native_marker_config_mismatch")


def unrecorded_session_tokens(reason=SESSION_TOKENS_NOT_REQUESTED):
    """``session_tokens`` of a report that holds no session report, with the reason."""
    return {"recorded": False, "reason": reason}


def session_tokens_record(session, declaration_elapsed_ms):
    """``session_tokens`` of a run whose ``session`` was declared: its report, if this contract accepts it."""
    try:
        record = {"recorded": True, **session.report(), "declaration_elapsed_ms": declaration_elapsed_ms}
        validate_session_tokens(record)
    except Exception:
        return unrecorded_session_tokens(SESSION_TOKENS_REPORT_INVALID)
    return record


def _text(value, *, minimum=0, limit=MAX_SESSION_TEXT):
    return type(value) is str and minimum <= len(value) <= limit


def _count(value):
    return type(value) is int and value >= 0


def _duration(value):
    """A non-negative ``int`` or finite ``float``; a ``bool`` is not one.

    An ``int`` is compared with the largest finite float, never converted to
    one, so a huge integer is refused here instead of overflowing.
    """
    if type(value) is int:
        return 0 <= value <= sys.float_info.max
    return type(value) is float and math.isfinite(value) and value >= 0


def validate_session_tokens(value):
    """The shape of a worker report's ``session_tokens``; never a check that a token is true.

    Unrecorded: ``recorded`` false and one of ``UNRECORDED_SESSION_REASONS``.
    Recorded: exactly ``RECORDED_SESSION_FIELDS``. ``declared`` names all five
    session tokens; a token is ``None`` exactly when ``undeclared`` gives its
    reason. ``first_change``, when set, names a declared token and a frame
    within ``frames_read``. Token values are bounded strings (a non-negative
    integer for ``recenter_epoch``); the candidate record applies the stricter
    token rule. ``declaration_elapsed_ms`` is a non-negative int or finite float.
    """
    if type(value) is not dict or type(value.get("recorded")) is not bool:
        raise ValueError("invalid_observer_session_tokens")
    if value["recorded"] is False:
        if set(value) != {"recorded", "reason"} or value["reason"] not in UNRECORDED_SESSION_REASONS:
            raise ValueError("invalid_observer_session_tokens")
        return
    declared, undeclared, frames = value.get("declared"), value.get("undeclared"), value.get("frames_read")
    change, process, elapsed = value.get("first_change"), value.get("runtime_process"), value.get("declaration_elapsed_ms")
    if (set(value) != set(RECORDED_SESSION_FIELDS) or
            type(declared) is not dict or set(declared) != set(SESSION_TOKENS) or
            type(undeclared) is not dict or not set(undeclared) <= set(SESSION_TOKENS) or
            not all(_text(reason, minimum=1) for reason in undeclared.values()) or
            any((declared[name] is None) != (name in undeclared) for name in SESSION_TOKENS) or
            any(declared[name] is not None and not _text(declared[name]) for name in STRING_SESSION_TOKENS) or
            not (declared["recenter_epoch"] is None or _count(declared["recenter_epoch"])) or
            not _count(frames) or not _text(value["scope"]) or not _duration(elapsed)):
        raise ValueError("invalid_observer_session_tokens")
    if change is not None and (type(change) is not dict or set(change) != {"frame", "token", "reason"} or
                               type(change["frame"]) is not int or not 1 <= change["frame"] <= frames or
                               change["token"] not in SESSION_TOKENS or declared[change["token"]] is None or
                               not _text(change["reason"], minimum=1)):
        raise ValueError("invalid_observer_session_tokens")
    if process is not None and (type(process) is not dict or not set(process) <= set(RUNTIME_PROCESS_FIELDS) or
                                any(type(process[key]) is not kind for key, kind in RUNTIME_PROCESS_FIELDS.items()
                                    if key in process) or
                                any(process.get(key, 0) < 0 for key in ("pid", "start_ticks")) or
                                len(process.get("executable", "")) > MAX_EXECUTABLE_PATH):
        raise ValueError("invalid_observer_session_tokens")


SCENE_STATISTICS_PHASES = ("baseline", "marker")


def rejection_scene_statistics(diagnostics, phase):
    """``scene_statistics`` and its source for a report rejected at the scene precondition, else ``{}``.

    The statistics move out of ``diagnostics``, so a report carries them once. A
    report stored before this change may also hold the copy in its diagnostics.
    """
    if type(diagnostics) is not dict or "scene_statistics" not in diagnostics:
        return {}
    return {"scene_statistics": diagnostics.pop("scene_statistics"),
            "scene_statistics_source": {"phase": phase, "sequence": diagnostics.get("sequence")}}


def validate_rejection_scene_statistics(report):
    """``scene_statistics`` appears only in a rejected report, with its source, and names that rejection."""
    if "scene_statistics" not in report and "scene_statistics_source" not in report:
        return
    statistics, source = report.get("scene_statistics"), report.get("scene_statistics_source")
    validate_scene_statistics(statistics)
    if (report["exit_code"] != 1 or report.get("status") != "rejected" or
            statistics["precondition"]["rejection"] is None or
            report.get("reason") != statistics["precondition"]["rejection"] or
            type(source) is not dict or set(source) != {"phase", "sequence"} or
            source["phase"] not in SCENE_STATISTICS_PHASES or
            type(source["sequence"]) is not int or source["sequence"] < 0):
        raise ValueError("invalid_observer_scene_statistics")


def validate_observer_report(report, returncode):
    """The ``hud_marker_observer`` worker report contract; never evidence of qualification."""
    if (not isinstance(report, dict) or report.get("report_kind") != "hud_marker_observation" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (1, 2, 130) or report["exit_code"] != returncode or
            type(report.get("cleanup_ok")) is not bool or
            type(report.get("calibration_validation_only")) is not bool or
            type(report.get("seconds_requested")) is not int or not MARKER_MIN_SECONDS <= report["seconds_requested"] <= MARKER_MAX_SECONDS or
            report.get("maximum_processing_fps") != 5 or
            any(report.get(key) is not False for key in (*claims(), "images_saved", "inference_started",
                "physical_display_recovery_verified",
                "eye_identity_assigned", "composition_verified", "unseen_or_occluded_copies_established",
                "camera_freshness_verified")) or
            (report["exit_code"] == 2 and (report.get("status") != "observed_unqualified" or not report["cleanup_ok"] or
                                           not 3 <= report.get("sample_count", 0) <= 100))):
        raise ValueError("invalid_observer_worker_report")
    # Required in every report, so a worker that drops it is invalid rather than silently unrecorded.
    validate_session_tokens(report.get("session_tokens"))
    validate_rejection_scene_statistics(report)
    if report["exit_code"] == 2:
        baseline = report.get("baseline")
        if (type(baseline) is not dict or baseline.get("established") is not True or
                baseline.get("camera_freshness_verified") is not False or
                baseline.get("clear_frames", 0) < 2 or baseline.get("content_changes", 0) < 1):
            raise ValueError("invalid_observer_baseline_evidence")
        validate_marker_evidence(report.get("bridge"), report["seconds_requested"],
                                 report["calibration_validation_only"])
