"""The synthetic run kit: generated input, its fixture calibration and engine fixtures.

``SyntheticSource`` is the only source kind the live-image gate accepts. It
emits validated 96x64 owned frames and declares ``SyntheticSession``'s tokens;
``synthetic_hud_calibration`` describes exactly those frames.
``fixture_calibration`` is one of the two ``Calibration`` factories, for
generated frames only (the other is ``MappingCandidate.calibration()``).
``fixture_candidate`` describes a fixture calibration as the unqualified
candidate record a synthetic run's ``ValidationLedger`` checks against.
``FixtureDetector`` (no detections, no model) and ``HeadlessSink`` (no display)
exercise the engine and its lifecycle offline. Used by
``scripts/live_pipeline.py``, ``runtime.live`` and the tests.
"""
import hashlib
import json
import time

from ..frame import DetectionResult, Frame
from ..gates import HARDWARE_MAPPING, claims
from ..hud import HUD_CONFIG, Calibration, CaptureFormat, Footprint, MappingCandidate, config_digest
from ..hud.contract import CANDIDATE_SCHEMA_VERSION, MAX_MAPPING_LIFETIME_NS, SESSION_TOKENS, SYNTHETIC_PROVENANCE
from ..hud.qualification import COPY_ERROR_TERMS, OPERATING_LIMIT_FIELDS, UNKNOWN_TERMS

FIXTURE_UNKNOWN_REASON = "Synthetic fixture: the footprint is declared for generated frames; nothing was observed"


class SyntheticSession:
    """Session tokens generated input declares; ``fixture_calibration`` pins the same values."""
    session_id = "synthetic:session"
    runtime_id = "synthetic:runtime"
    timebase_id = "synthetic:monotonic_ns"
    tracking_origin = "synthetic:none"
    recenter_epoch = 0


SYNTHETIC_SESSION = {name: getattr(SyntheticSession, name) for name in SESSION_TOKENS}


class SyntheticSource(SyntheticSession):
    kind = "synthetic"
    source_id = "synthetic:generated"
    capture_layout = "mono"

    def __init__(self, rate=30):
        import numpy as np
        self.np = np  # Dependency startup occurs before the bounded acquisition interval.
        self.period = 1 / rate
        self.sequence = 0
        self.next_frame = 0
        self.closed = False

    def open(self):
        self.next_frame = time.monotonic()

    def read(self, stop, timeout=0.05):
        delay = self.next_frame - time.monotonic()
        if delay > 0:
            stop.wait(min(timeout, delay))
            return None
        self.sequence += 1
        self.next_frame = time.monotonic() + self.period
        pixels = self.np.full((64, 96, 3), self.sequence % 255, dtype=self.np.uint8)
        return Frame.owned_bgr(pixels, self.sequence, time.monotonic_ns(), self.source_id)

    def close(self):
        self.closed = True
        return True


class FixtureDetector:
    def detect(self, image, threshold):
        return DetectionResult((), 0.0)


class HeadlessSink:
    def open(self):
        self.count = self.clears = 0

    def submit(self, processed):
        self.count += 1

    def clear(self):
        self.clears += 1

    def close(self):
        return True


def fixture_calibration(source_id, capture_format, capture_layout, footprints, *, config=HUD_CONFIG,
                        lifetime_ns=MAX_MAPPING_LIFETIME_NS, session=SYNTHETIC_SESSION):
    """A synthetic-fixture ``Calibration`` of generated frames, valid from now for ``lifetime_ns``.

    ``source_id`` must be ``synthetic:...``; the tokens are ``session``, a
    mapping of exactly ``SESSION_TOKENS`` (default ``SYNTHETIC_SESSION``; tests
    pass the tokens their fixture providers declare), and the crop is the full,
    upright frame. Never a hardware mapping.
    """
    if type(source_id) is not str or not source_id.startswith("synthetic:"):
        raise ValueError("hud_synthetic_fixture_source_required")
    if type(lifetime_ns) is not int or not 1 <= lifetime_ns <= MAX_MAPPING_LIFETIME_NS:
        raise ValueError("hud_calibration_lifetime_invalid")
    if not isinstance(session, dict) or set(session) != set(SESSION_TOKENS):
        raise ValueError("hud_synthetic_session_invalid")
    observed_ns = time.monotonic_ns()
    return Calibration._issue(
        source_id=source_id, capture_format=capture_format, capture_layout=capture_layout, footprints=footprints,
        config=config, provenance=SYNTHETIC_PROVENANCE, **{name: session[name] for name in SESSION_TOKENS},
        crop_xyxy=getattr(capture_format, "bounds", None), orientation_degrees=0,
        hud_config_sha256=config_digest(config), observed_ns=observed_ns, expires_ns=observed_ns + lifetime_ns)


def synthetic_hud_calibration(lifetime_ns=MAX_MAPPING_LIFETIME_NS):
    """Only for the fixed generated 96x64 source; no hardware calibration loader."""
    return fixture_calibration(SyntheticSource.source_id, CaptureFormat(96, 64, "BGR3", 288, 18432, 18432),
                               "mono", (Footprint(0, (67, 42, 88, 56), 8),), lifetime_ns=lifetime_ns)


def fixture_candidate(calibration):
    """The version 2 candidate record of a synthetic-fixture calibration, for a synthetic run's ledger.

    Its context, lifetime and copies are the calibration's. Every term an
    observation would establish is an explicit unknown (null, never 0), and its
    one ``synthetic_fixture`` evidence entry is the digest of those values. Like
    every candidate it is unqualified, and its own ``calibration()`` is unusable.
    """
    if not isinstance(calibration, Calibration) or calibration.provenance != SYNTHETIC_PROVENANCE:
        raise ValueError("hud_synthetic_fixture_required")
    copies = [{"copy_id": f"fixture-eye-{footprint.eye}", "declared_eye": footprint.eye,
               "rect_native": list(footprint.rect), "margin_px": footprint.margin_px,
               **dict.fromkeys(COPY_ERROR_TERMS)} for footprint in calibration.footprints]
    declared = {"context": calibration.context(), "observed_ns": calibration.observed_ns,
                "expires_ns": calibration.expires_ns, "copies": copies}
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return MappingCandidate.from_mapping({
        "schema_version": CANDIDATE_SCHEMA_VERSION, "status": "observed_unqualified", **claims(HARDWARE_MAPPING),
        "mapping_id": f"synthetic-fixture-{digest[:16]}", **declared,
        "operating_limits": dict.fromkeys(OPERATING_LIMIT_FIELDS),
        "evidence": [{"role": "observer", "artifact_id": "synthetic:fixture-calibration", "sha256": digest,
                      "kind": "synthetic_fixture"}],
        "unknowns": dict.fromkeys(UNKNOWN_TERMS, FIXTURE_UNKNOWN_REASON)})
