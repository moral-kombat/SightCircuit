"""Bounded empirical marker-copy observation, never a production calibration.

Native-resolution capture is retained. Only candidate quads are rectified to
check the fixed marker. No eye identity or unseen/occluded copy is inferred.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import itertools
import math
from pathlib import Path
import signal
import sys
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.hud import CaptureFormat, HUD_CONFIG  # noqa: E402

if __package__:
    from . import mirror_check
    from .bounded_worker import supervise
    from .hud_marker_check import MarkerClient, new_report as marker_report, validate_worker_report as validate_marker_report
    from .mirror_capture import discover, select_candidate
else:
    import mirror_check
    from bounded_worker import supervise
    from hud_marker_check import MarkerClient, new_report as marker_report, validate_worker_report as validate_marker_report
    from mirror_capture import discover, select_candidate

MAX_PIXELS = 8 * 1024 * 1024
MAX_CONTOURS = 2048
MAX_QUADS = 256
MAX_COPIES = 4
MAX_DIAGNOSTIC_CANDIDATES = 4
MAX_DIAGNOSTIC_FRAGMENTS = 16
MAX_CORNER_REFINEMENT_PX = 2
BASELINE_WINDOW_SECONDS = 2.0


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
    import cv2
    import numpy as np
    descriptor = CaptureFormat.from_mapping(fmt)
    descriptor.validate()
    if descriptor.width * descriptor.height > MAX_PIXELS:
        raise ValueError("observer_pixel_budget")
    if len(data) < descriptor.minimum_buffer_bytes:
        raise ValueError("observer_short_frame")
    image = np.ndarray((descriptor.height, descriptor.width, 3), dtype=np.uint8, buffer=bytes(data),
                       strides=(descriptor.stride_bytes, 3, 1))
    return (cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if descriptor.fourcc == "RGB3" else image.copy()), descriptor


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
                "unseen_or_occluded_copies_established": False, "mapping_qualified": False}


def new_report(validation_only=False):
    return {"report_kind": "hud_marker_observation", "status": "not_run", "exit_code": 3,
            "calibration_validation_only": validation_only,
            "cleanup_ok": True, "images_saved": False, "inference_started": False,
            "production_live_input_enabled": False, "production_hud_enabled": False,
            "mapping_qualified": False, "physical_display_recovery_verified": False,
            "camera_freshness_verified": False,
            "scope": "Empirical visible-copy bounds only; unknown composition cannot qualify production masking"}


def validate_marker_evidence(bridge, seconds, validation_only):
    """Reuse the finalized marker deadline contract, then pin every HUD setting."""
    report = marker_report(validation_only)
    report.update(status="completed", exit_code=0, cleanup_ok=True, seconds_requested=seconds, bridge=bridge)
    validate_marker_report(report, 0, validation_only=validation_only)
    marker = bridge["native_report"].get("marker") or {}
    expected = {"width": HUD_CONFIG.width_px, "height": HUD_CONFIG.height_px,
                "opaque_pixels": HUD_CONFIG.width_px * HUD_CONFIG.height_px,
                "corner_ids": [1, 2, 3, 4], "border_pixels": 4, "tag_size_pixels": 24, "tag_inset_pixels": 8,
                "config_id": HUD_CONFIG.layout_id, "corner_order": "top_left,top_right,bottom_left,bottom_right",
                "visible_in_dashboard": True}
    if (any(marker.get(key) != value for key, value in expected.items()) or
            any(type(marker.get(key)) not in (int, float) or
                not math.isclose(marker[key], getattr(HUD_CONFIG, key), rel_tol=0, abs_tol=1e-6)
                for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"))):
        raise ValueError("native_marker_config_mismatch")


def observe(candidate, seconds, stop, *, backend_factory, marker_client, config=ObserverConfig(),
            allow_mirror_observation=False, allow_calibration_output=False):
    """One combined bounded trial; backend and marker are injected for offline tests."""
    if type(seconds) is not int or not 1 <= seconds <= 20:
        raise ValueError("observer_duration_out_of_bounds")
    config.validate()
    if backend_factory is None and allow_mirror_observation is not True:
        raise ValueError("observer_mirror_opt_in_required")
    if backend_factory is not None and getattr(backend_factory, "kind", None) != "synthetic":
        raise ValueError("observer_fixture_backend_required")
    if marker_client.validation_only is not True and allow_calibration_output is not True:
        raise ValueError("observer_output_opt_in_required")
    report = new_report(marker_client.validation_only)
    observation = EmpiricalObservation(config)
    started = time.monotonic()
    deadline = started + seconds
    baseline = BaselineGate(min(deadline, started + BASELINE_WINDOW_SECONDS))
    marker_started = False
    baseline_descriptor = None
    first_marker_deadline = None
    try:
        def on_frame(data, fmt, sequence, observed_ns):
            nonlocal marker_started, baseline_descriptor, first_marker_deadline
            if time.monotonic() >= deadline or stop.is_set():
                return
            image, descriptor = native_bgr(data, fmt)
            if baseline_descriptor is None:
                baseline_descriptor = descriptor
            if descriptor != baseline_descriptor:
                raise ValueError("observer_format_changed")
            if not marker_started:
                ready = baseline.accept(image, sequence, observed_ns, time.monotonic())
                if stop.is_set() or time.monotonic() >= baseline.deadline:
                    raise ValueError("baseline_processing_exceeded_observation_deadline")
                if not ready:
                    return
                marker_client.start_marker(seconds, stop, allow_calibration_output=allow_calibration_output)
                marker_started = True
                first_marker_deadline = min(deadline, time.monotonic() + 2)
                report["baseline_sequence"] = sequence
                return
            # No inference or downscaled analysis image: these are native corners.
            try:
                observation.accept(image, descriptor, sequence, observed_ns, config_id=config.config_id)
            except ValueError as error:
                if str(error) == "no_complete_marker" and not observation.samples and time.monotonic() < first_marker_deadline:
                    report["warmup_frames_without_marker"] = report.get("warmup_frames_without_marker", 0) + 1
                    return
                raise
            if time.monotonic() >= deadline:
                raise ValueError("marker_processing_exceeded_observation_deadline")
        def on_progress(_report):
            if not marker_started:
                baseline.check_deadline(time.monotonic())
        captured, _ = mirror_check.capture(candidate, seconds, stop, progress=on_progress,
                                           backend_factory=backend_factory, on_frame=on_frame, max_fps=5, deadline=deadline)
        report["capture"] = captured
        if captured["status"] == "error":
            raise ValueError(captured.get("error", captured.get("reason")))
        if stop.is_set():
            report.update(status="cancelled", exit_code=130)
        elif not marker_started:
            raise ValueError("baseline_distinct_clear_frames_not_established")
        elif len(observation.samples) < 3:
            raise ValueError("insufficient_complete_marker_samples")
        else:
            report.update(status="observed_unqualified", exit_code=2,
                          reason="composition_and_unseen_content_not_established")
        # Allow the marker's own timer to close normally; never read more frames.
        while (marker_started and marker_client.process.poll() is None and not stop.is_set()
               and time.monotonic() < deadline + 2):
            stop.wait(.02)
    except Exception as error:
        report.update(status="cancelled" if stop.is_set() else "rejected", exit_code=130 if stop.is_set() else 1,
                      reason=str(error)[:1024])
    finally:
        report.update(observation.evidence())
        report.update(baseline=dict(baseline.report), baseline_initial_match_diagnostics=baseline.initial_diagnostics,
                      baseline_match_diagnostics=baseline.last_diagnostics,
                      baseline_window_seconds=min(seconds, BASELINE_WINDOW_SECONDS))
        try:
            bridge = marker_client.close()
            report["bridge"] = bridge
            native = bridge.get("native_report") or {}
            marker_clean = (marker_client.process is None and not marker_started) or (
                bridge.get("worker_reaped") is True and bridge.get("worker_exit_code") == 0 and
                bridge.get("transport_threads_stopped") is True and not bridge.get("forced_termination") and
                bridge.get("error") is None and native.get("cleanup_ok") is True and native.get("exit_code") == 0)
            report["cleanup_ok"] = (report.get("capture", {}).get("cleanup_ok") is True and
                                    marker_clean)
            if report["exit_code"] == 2 and marker_clean:
                try:
                    validate_marker_evidence(bridge, seconds, marker_client.validation_only)
                except (ValueError, TypeError, KeyError) as error:
                    report.update(status="rejected", exit_code=1, reason=str(error)[:1024])
        except Exception as error:
            report.update(cleanup_ok=False, cleanup_error=str(error)[:1024])
        if not report["cleanup_ok"] and report["exit_code"] == 2:
            report.update(status="cleanup_failed", exit_code=1)
        report["seconds_requested"] = seconds
        report["maximum_processing_fps"] = 5
    if len(json.dumps(report)) > 128 * 1024:
        report.update(samples=[], status="report_budget_exceeded", exit_code=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-calibration", action="store_true")
    parser.add_argument("--allow-mirror-observation", action="store_true")
    parser.add_argument("--allow-calibration-output", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--bridge", type=Path, help="Exact reviewed native bridge; defaults to the existing bridge path")
    parser.add_argument("--seconds", type=int, choices=range(1, 21), default=20)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        options = json.loads(args.worker)
        if options.get("approved_scope") != "marker_observation_only":
            raise ValueError("observer_explicit_scope_required")
        stop = threading.Event()
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, lambda *_: stop.set())
        client = MarkerClient(options["bridge"], validation_only=False) if options.get("bridge") else MarkerClient(validation_only=False)
        report = observe(options["candidate"], options["seconds"], stop, backend_factory=None,
                         marker_client=client, allow_mirror_observation=True,
                         allow_calibration_output=True)
    else:
        opted = (args.observe_calibration, args.allow_mirror_observation, args.allow_calibration_output, bool(args.device))
        if any(opted) and not all(opted):
            parser.error("calibration observation requires all three explicit flags and a verified --device")
        if args.bridge and not all(opted):
            parser.error("--bridge requires the complete calibration opt-in")
        report = new_report()
        if all(opted):
            candidate = select_candidate(discover(), args.device)
            options = {"approved_scope": "marker_observation_only", "candidate": candidate, "seconds": args.seconds,
                       "bridge": str(args.bridge.resolve()) if args.bridge else None}
            report = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(options)],
                new_report(), args.seconds + 12, validate_worker_report, descendants=True)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


def validate_worker_report(report, returncode):
    if (not isinstance(report, dict) or report.get("report_kind") != "hud_marker_observation" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (1, 2, 130) or report["exit_code"] != returncode or
            type(report.get("cleanup_ok")) is not bool or
            type(report.get("calibration_validation_only")) is not bool or
            type(report.get("seconds_requested")) is not int or not 1 <= report["seconds_requested"] <= 20 or
            report.get("maximum_processing_fps") != 5 or
            any(report.get(key) is not False for key in ("mapping_qualified", "images_saved", "inference_started",
                "production_live_input_enabled", "production_hud_enabled", "physical_display_recovery_verified",
                "eye_identity_assigned", "composition_verified", "unseen_or_occluded_copies_established",
                "camera_freshness_verified")) or
            (report["exit_code"] == 2 and (report.get("status") != "observed_unqualified" or not report["cleanup_ok"] or
                                           not 3 <= report.get("sample_count", 0) <= 100))):
        raise ValueError("invalid_observer_worker_report")
    if report["exit_code"] == 2:
        baseline = report.get("baseline") or {}
        if (baseline.get("established") is not True or baseline.get("camera_freshness_verified") is not False or
                baseline.get("clear_frames", 0) < 2 or baseline.get("content_changes", 0) < 1):
            raise ValueError("invalid_observer_baseline_evidence")
        validate_marker_evidence(report.get("bridge") or {}, report["seconds_requested"],
                                 report["calibration_validation_only"])


if __name__ == "__main__":
    sys.exit(main())
