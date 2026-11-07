"""Versioned HUD layout, native capture format and calibration records.

These types describe a mapping; they never approve one. ``HudMask`` decides
whether a calibration may be used, and today it accepts synthetic fixtures only.
Head-relative panel coordinates do NOT establish a fixed mirror-pixel mapping.
A ``Calibration`` has no public constructor: only the synthetic fixture and a
schema-checked mapping candidate issue one. ``margin_valid`` is the one margin
rule for the runtime mask, the marker observer and candidate records.

This module also owns the values the native bridge shares: ``HUD_CONFIG``, the
calibration ``MARKER``, the pipe packet layout and opcodes, and the duration,
age and frame-size limits. ``tools/hud_contract.hpp`` is generated from them
(``sightcircuit.hud.native``); neither side keeps its own copy.
"""
from dataclasses import asdict, dataclass, fields, is_dataclass
import hashlib
import json
import math

from ..frame import MAX_NATIVE_BYTES, MAX_NATIVE_SIDE
from .geometry import eye_viewports


@dataclass(frozen=True)
class HudConfig:
    layout_id: str = "fixed-edge-hud-v1"
    width_px: int = 320
    height_px: int = 128
    rows: int = 3
    panel_width_m: float = 0.32
    distance_m: float = 1.25
    x_m: float = 0.55
    y_m: float = -0.35
    opacity: float = 1.0
    inference_max_side: int = 960


HUD_CONFIG = HudConfig()


@dataclass(frozen=True)
class MarkerGeometry:
    """The native calibration marker, in pixels of a ``HudConfig``-sized tile.

    An opaque gray tile with a bright border and one binary tag per corner,
    in ``corner_order``. A tag is a dark outline around a bright square that
    holds a grid of code bits: bit ``b`` sits in column ``b % bit_columns`` and
    row ``b // bit_columns``, and is bright when bit ``b`` of the corner's id is
    set. Values are gray levels; the native bridge writes them to R, G and B.
    """
    background_value: int = 32
    border_px: int = 4
    border_value: int = 255
    tag_size_px: int = 24
    tag_inset_px: int = 8
    tag_outline_px: int = 2
    tag_outline_value: int = 0
    tag_fill_value: int = 255
    bit_offset_px: int = 4
    bit_pitch_px: int = 8
    bit_size_px: int = 6
    bit_columns: int = 2
    bits: int = 4
    bit_set_value: int = 255
    bit_clear_value: int = 0
    corner_ids: tuple = (1, 2, 3, 4)
    corner_order: str = "top_left,top_right,bottom_left,bottom_right"

    def tag_origin(self, tag, config=HUD_CONFIG):
        """Top-left pixel of the tag at index ``tag`` of ``corner_ids``."""
        far = self.tag_inset_px + self.tag_size_px
        return (config.width_px - far if tag % 2 else self.tag_inset_px,
                config.height_px - far if tag // 2 else self.tag_inset_px)

    def bit_origin(self, bit):
        """Top-left pixel of code bit ``bit``, relative to its tag's origin."""
        return (self.bit_offset_px + (bit % self.bit_columns) * self.bit_pitch_px,
                self.bit_offset_px + (bit // self.bit_columns) * self.bit_pitch_px)

    def bit_set(self, tag, bit):
        return bool((self.corner_ids[tag] >> bit) & 1)


MARKER = MarkerGeometry()

# Limits shared with the native bridge.
MARKER_MIN_SECONDS = 1  # Bounded calibration-marker display duration.
MARKER_MAX_SECONDS = 20
MAX_DISPLAY_AGE_NS = 1_000_000_000  # Longest a result or presented frame may stay current.
MAX_DISPLAY_AGE_MS = MAX_DISPLAY_AGE_NS // 1_000_000
MAX_SIDE = 512  # Largest RGBA frame side sent to the bridge.
RGBA_BYTES = 4

# The one footprint margin rule, from the G4 method's error budget
# (docs/hud-qualification.md#runtime-calibration-and-one-margin-rule-2026-11-01):
# at least the measured error terms rounded up plus a guard, never below the
# floor, never above the ceiling. Python only; the native bridge draws no mask.
MARGIN_MIN_PX = 8
MARGIN_MAX_PX = 128
MARGIN_GUARD_PX = 2
MAX_MAPPING_LIFETIME_NS = 300 * 1_000_000_000  # Longest candidate or calibration lifetime.

# Anonymous-pipe packets to the bridge: a little-endian header, then the payload.
# Frame payloads are a prefix followed by width * height RGBA pixels.
PACKET_MAGIC = b"FJO1"
PACKET_HEADER_FIELDS = (("magic", "4s"), ("opcode", "I"), ("payload_bytes", "I"))
SETTINGS_FIELDS = (("width", "f"), ("distance", "f"), ("x", "f"), ("y", "f"), ("opacity", "f"), ("visible", "I"))
FRAME_PREFIX_FIELDS = (("width", "I"), ("height", "I"))
EXPIRING_FRAME_PREFIX_FIELDS = (("expires_ns", "Q"), ("width", "I"), ("height", "I"))
OPCODE_FRAME = 1
OPCODE_SETTINGS = 2
OPCODE_STOP = 3
OPCODE_EXPIRING_FRAME = 4  # Validation bridge only.


def layout_format(fields):
    """``struct`` format of a little-endian field layout."""
    return "<" + "".join(code for _, code in fields)


PACKET_HEADER_FORMAT = layout_format(PACKET_HEADER_FIELDS)
SETTINGS_FORMAT = layout_format(SETTINGS_FIELDS)
FRAME_PREFIX_FORMAT = layout_format(FRAME_PREFIX_FIELDS)
EXPIRING_FRAME_PREFIX_FORMAT = layout_format(EXPIRING_FRAME_PREFIX_FIELDS)


def required_margin_px(error_terms_px=()):
    """Smallest margin for measured error terms (px): their sum rounded up plus the guard, at least the floor."""
    terms = tuple(error_terms_px)
    if any(type(term) not in (int, float) or not math.isfinite(term) or term < 0 for term in terms):
        raise ValueError("hud_margin_error_terms_invalid")
    return max(MARGIN_MIN_PX, math.ceil(sum(terms)) + MARGIN_GUARD_PX)


def margin_valid(margin_px, error_terms_px=()):
    """The margin rule: an int from ``required_margin_px(error_terms_px)`` to ``MARGIN_MAX_PX``.

    Pass only measured terms. An unknown term adds nothing here; it must stay a
    named unknown, and the G4 method rejects any mapping that has one.
    """
    return type(margin_px) is int and required_margin_px(error_terms_px) <= margin_px <= MARGIN_MAX_PX


def token_valid(value, limit=160):
    """A bounded, printable identity token (source, session, runtime, timebase, tracking origin)."""
    return (type(value) is str and 1 <= len(value) <= limit and
            not any(ord(char) < 32 or ord(char) == 127 for char in value))


def config_digest(config):
    """Pin all HUD fields, not merely the human-readable layout name."""
    value = asdict(config) if is_dataclass(config) else config
    if type(value) is not dict:
        raise ValueError("hud_config_fields_invalid")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


NATIVE_FORMAT_FIELDS = ("width", "height", "fourcc", "stride_bytes", "buffer_bytes", "minimum_buffer_bytes")
# Candidate record versions (docs/reference/hud-candidates.md). Version 1 carries the six native-format
# fields only; version 2 (2026-11-07) adds ``native_format.metadata``, the source's other format metadata,
# so a mirror format is representable and compared exactly. Both are read; new records are version 2.
CANDIDATE_SCHEMA_VERSIONS = (1, 2)
CANDIDATE_SCHEMA_VERSION = 2
NATIVE_FORMAT_METADATA_FIELD = "metadata"


@dataclass(frozen=True)
class CaptureFormat:
    """One acquisition layout: the six native-format fields and the source's other format metadata.

    ``metadata`` holds every further key the source reports with its format
    (the mirror's V4L2 ``field``, ``colorspace_code``, ``format_flags``,
    ``ycbcr_encoding_code``, ``quantization_code`` and
    ``transfer_function_code``), as sorted ``(key, value)`` pairs, so two
    formats are equal only when every field and every metadata value agrees.
    ``from_mapping`` reads a source's flat format (extra keys beside the six)
    or a record's ``native_format`` (the six keys plus a ``metadata`` object,
    the version 2 record form); ``record()`` writes the record form.
    """
    width: int
    height: int
    fourcc: str
    stride_bytes: int
    buffer_bytes: int
    minimum_buffer_bytes: int
    metadata: tuple = ()

    @classmethod
    def from_mapping(cls, fmt):
        keys = tuple(key for key in cls.__dataclass_fields__ if key != "metadata")
        extra = {key: value for key, value in fmt.items() if key not in keys}
        if NATIVE_FORMAT_METADATA_FIELD in extra:
            # The record form: the metadata object carries every extra key, none sits beside it.
            nested = extra.pop(NATIVE_FORMAT_METADATA_FIELD)
            if type(nested) is not dict or extra or any(type(key) is not str for key in nested):
                raise ValueError("hud_capture_metadata_invalid")
            extra = nested
        metadata = tuple(sorted(extra.items()))
        return cls(*(fmt[key] for key in keys), metadata=metadata)

    def record(self):
        """The version 2 ``native_format`` record form: the six fields plus the ``metadata`` object."""
        return {**{name: getattr(self, name) for name in NATIVE_FORMAT_FIELDS},
                NATIVE_FORMAT_METADATA_FIELD: dict(self.metadata)}

    @classmethod
    def bgr(cls, image):
        return cls(image.shape[1], image.shape[0], "BGR3", image.strides[0],
                   image.nbytes, image.strides[0] * image.shape[0])

    def validate(self):
        values = (self.width, self.height, self.stride_bytes, self.buffer_bytes, self.minimum_buffer_bytes)
        if (any(type(value) is not int for value in values) or
                not 1 <= self.width <= MAX_NATIVE_SIDE or not 1 <= self.height <= MAX_NATIVE_SIDE or
                self.fourcc not in ("RGB3", "BGR3") or self.stride_bytes < self.width * 3 or
                self.minimum_buffer_bytes != self.stride_bytes * self.height or
                not self.minimum_buffer_bytes <= self.buffer_bytes <= MAX_NATIVE_BYTES):
            raise ValueError("hud_capture_format_invalid")
        if (type(self.metadata) is not tuple or len(self.metadata) > 16 or
                any(type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], str) or
                    len(pair[0]) > 64 or type(pair[1]) not in (int, str) or len(str(pair[1])) > 96
                    for pair in self.metadata)):
            raise ValueError("hud_capture_metadata_invalid")

    @property
    def bounds(self):
        return (0, 0, self.width, self.height)


@dataclass(frozen=True)
class Footprint:
    eye: int
    rect: tuple  # Native capture pixels, x0/y0 inclusive and x1/y1 exclusive.
    margin_px: int


SYNTHETIC_PROVENANCE = "synthetic_fixture"
CANDIDATE_PROVENANCE = "observed_unqualified"
# Session tokens a calibration pins; ``HudMask.check_session`` compares them with the capture context.
SESSION_TOKENS = ("session_id", "runtime_id", "timebase_id", "tracking_origin", "recenter_epoch")
# View fields a calibration also pins. A source may declare, or a frame report, each of them; undeclared
# means the calibration's own full, upright frame and HUD configuration.
VIEW_FIELDS = ("crop_xyxy", "orientation_degrees", "hud_config_sha256")


@dataclass(frozen=True, init=False)
class Calibration:
    """A runtime mask mapping, bound to the acquisition context and lifetime it describes.

    Only two factories issue one: ``runtime.synthetic.fixture_calibration`` for
    generated frames and ``MappingCandidate.calibration()`` for a schema-checked
    candidate record. ``Calibration(...)`` and ``dataclasses.replace`` raise
    ``TypeError``, so no caller sets ``validated``, edits a token or extends
    ``expires_ns``. ``observed_ns`` and ``expires_ns`` are ``time.monotonic_ns()``
    values in ``timebase_id``, the clock of ``Frame.observed_ns``.
    """
    source_id: str
    capture_format: CaptureFormat
    capture_layout: str
    footprints: tuple
    config: HudConfig
    provenance: str
    session_id: str
    runtime_id: str
    timebase_id: str
    tracking_origin: str
    recenter_epoch: int
    crop_xyxy: tuple
    orientation_degrees: int
    hud_config_sha256: str
    observed_ns: int
    expires_ns: int

    def __init__(self, *args, **kwargs):
        raise TypeError("hud_calibration_factory_required")

    @classmethod
    def _issue(cls, **values):
        """The two factories' constructor (a test enforces that no other module calls it)."""
        if set(values) != {field.name for field in fields(cls)}:
            raise ValueError("hud_calibration_fields_invalid")
        calibration = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(calibration, name, value)
        calibration.validate()
        return calibration

    @property
    def validated(self):
        """True only for the synthetic fixture; no hardware mapping is validated in this checkout."""
        return self.provenance == SYNTHETIC_PROVENANCE

    def validate(self):
        """The record's own consistency. ``HudMask`` adds approval, geometry and the current time."""
        if self.provenance not in (SYNTHETIC_PROVENANCE, CANDIDATE_PROVENANCE):
            raise ValueError("hud_calibration_provenance_invalid")
        if (not all(token_valid(getattr(self, name)) for name in ("source_id", *SESSION_TOKENS[:-1])) or
                type(self.recenter_epoch) is not int or not 0 <= self.recenter_epoch < 2**63):
            raise ValueError("hud_calibration_context_invalid")
        if not isinstance(self.config, HudConfig) or self.hud_config_sha256 != config_digest(self.config):
            raise ValueError("hud_config_changed")
        if not isinstance(self.capture_format, CaptureFormat):
            raise ValueError("hud_capture_format_invalid")
        self.capture_format.validate()
        viewports(self.capture_format, self.capture_layout)
        # The runtime mask's frame is the full, upright capture.
        if type(self.crop_xyxy) is not tuple or self.crop_xyxy != self.capture_format.bounds:
            raise ValueError("hud_runtime_crop_unsupported")
        if type(self.orientation_degrees) is not int or self.orientation_degrees != 0:
            raise ValueError("hud_runtime_orientation_unsupported")
        if (type(self.observed_ns) is not int or type(self.expires_ns) is not int or
                not 0 <= self.observed_ns < self.expires_ns <= self.observed_ns + MAX_MAPPING_LIFETIME_NS):
            raise ValueError("hud_calibration_lifetime_invalid")

    def context(self):
        """The version 2 candidate ``context`` this calibration describes, format metadata included.

        A version 1 record's calibration has no metadata, so its context is the
        record's with an empty ``native_format.metadata`` object.
        """
        return {"source_id": self.source_id, **{name: getattr(self, name) for name in SESSION_TOKENS},
                "native_format": self.capture_format.record(),
                "crop_xyxy": list(self.crop_xyxy), "orientation_degrees": self.orientation_degrees,
                "capture_layout": self.capture_layout, "hud_config_sha256": self.hud_config_sha256}


@dataclass(frozen=True)
class HudRow:
    """One HUD line: a sanitized name and an approximate view direction, never a position."""
    label: str
    confidence: float
    direction: tuple  # (dx, dy), each -1, 0 or 1 relative to the eye's view centre.

    def as_dict(self):
        return {"label": self.label, "confidence": self.confidence, "direction": list(self.direction)}


@dataclass(frozen=True)
class HudChecks:
    """One frame's exclusion telemetry, never presented.

    ``boxes_native`` holds every valid detection at or above the threshold in
    native pixels, including those dropped for touching a masked region
    (``mask_overlap_rejections``) or for not lying inside exactly one eye
    (``eye_crossing_rejections``). ``unmasked_footprint_px`` counts footprint
    pixels still non-zero after the zeroing step, before the neutral fill and
    the resize, which is 0 unless masking failed.
    ``frame_sequence`` is the frame the stage masked, so a result cannot be
    paired with another frame unnoticed.
    """
    boxes_native: tuple = ()
    mask_overlap_rejections: int = 0
    eye_crossing_rejections: int = 0
    unmasked_footprint_px: int = 0
    frame_sequence: int | None = None


@dataclass(frozen=True)
class HudOutput:
    """What one result contributes to the fixed HUD, the layout it was drawn for and its ``HudChecks``."""
    rows: tuple
    config: HudConfig
    checks: HudChecks | None = None

    @property
    def labels(self):
        return [row.label for row in self.rows]


def viewports(fmt, layout):
    """Eye rectangles of a full, uncropped capture; the runtime mask's frame."""
    return eye_viewports(fmt.bounds, layout)
