"""Capture-context identity: one fail-closed rule for every recognition loop.

A result may be presented only while the acquisition context it came from is
exactly the context the run started with. ``CaptureContext`` pins that context
in one place and every engine asks it, through ``admit(frame)``, at its fixed
checkpoints: when capture publishes a frame, before inference and before any
output.

It pins three things:

1. **Declared source identity**: ``SOURCE_FIELDS`` read from the source object
   when the context is created, before the source opens. Missing fields stay
   explicitly ``None``; they are observation context, never a claim of
   exposure timing or of a qualified hardware mapping.
2. **Frame identity**: the first admitted frame's source id, native pixel layout
   (plus ``FORMAT_METADATA_KEYS``) and session metadata (``SESSION_METADATA_KEYS``).
   Per-frame telemetry outside these keys does not invalidate the context.
3. **HUD binding** (optional): the bound ``HudMask`` checks each frame against
   its calibration's source, native format, layout and configuration.

Any mismatch raises ``ValueError``. The first failure is latched: every later
admission fails with the same reason, so an in-flight result cannot outlive a
context change observed on another thread. Nothing here re-pins, relaxes a
check or approves a source; approval gates stay with the callers.
"""
from copy import copy, deepcopy
import threading

from .frame import Frame, OwnedFrame
from .hud.contract import CaptureFormat

SOURCE_CHANGED = "source_or_session_changed"
FORMAT_CHANGED = "native_capture_format_changed"

SOURCE_FIELDS = (
    "kind", "source_id", "capture_layout", "session_id", "runtime_id", "tracking_origin",
    "recenter_generation", "orientation", "crop", "capture_format", "hud_config_id",
    "recenter_epoch", "timebase_id", "orientation_degrees", "crop_xyxy", "hud_config_sha256",
)
SESSION_METADATA_KEYS = frozenset((
    "context", "source_id", "session_id", "runtime_id", "tracking_origin", "recenter_generation",
    "crop", "orientation", "capture_layout", "hud_config_id", "mapping_id", "calibration_id",
    "recenter_epoch", "timebase_id", "orientation_degrees", "crop_xyxy", "hud_config_sha256",
))
FORMAT_METADATA_KEYS = frozenset((
    "native_format", "buffer_bytes", "minimum_buffer_bytes", "field", "colorspace_code",
    "format_flags", "ycbcr_encoding_code", "quantization_code", "transfer_function_code",
))


def declared_identity(source):
    """Deep snapshot of the source's declared identity fields."""
    return tuple(deepcopy(getattr(source, name, None)) for name in SOURCE_FIELDS)


def frame_identity(frame):
    """Return ``(source_id, native_layout, session_metadata)`` as owned copies."""
    if isinstance(frame, OwnedFrame):
        return frame.source_id, frame.native_descriptor, {}
    if isinstance(frame, Frame):
        metadata = frame.metadata or {}
        layout = (frame.pixel_format, frame.width, frame.height, frame.stride,
                  deepcopy({key: value for key, value in metadata.items() if key in FORMAT_METADATA_KEYS}))
        session = deepcopy({key: value for key, value in metadata.items() if key in SESSION_METADATA_KEYS})
        return frame.source, layout, session
    raise ValueError("capture_frame_type_unsupported")


def native_format(frame):
    """The acquisition format a frame declares, as a V4L2-style mapping."""
    if isinstance(frame, OwnedFrame):
        return frame.native_descriptor
    if isinstance(frame, Frame):
        fmt = (frame.metadata or {}).get("native_format")
        if isinstance(fmt, dict):
            return dict(fmt)
    raise ValueError("native_format_undeclared")


class CaptureContext:
    """The acquisition context one run is bound to; see the module docstring."""

    def __init__(self, source, *, mask=None):
        self.source = source
        self.mask = mask
        self._declared = declared_identity(source)
        self._lock = threading.Lock()
        self._pinned = None
        self._failure = None
        if mask is not None:
            # The calibration must describe this source before any frame is read.
            mask.check_context(self.declared("source_id"), mask.calibration.capture_format,
                               self.declared("capture_layout"), mask.config)

    def declared(self, name):
        return copy(self._declared[SOURCE_FIELDS.index(name)])

    @property
    def failure(self):
        with self._lock:
            return self._failure

    def admit(self, frame):
        """Raise ``ValueError`` unless ``frame`` still matches the pinned context."""
        with self._lock:
            if self._failure is not None:
                raise ValueError(self._failure)
        try:
            self._admit(frame)
        except Exception as error:
            reason = str(error) if isinstance(error, ValueError) else "capture_context_invalid"
            with self._lock:
                if self._failure is None:
                    self._failure = reason
            raise

    def _admit(self, frame):
        if declared_identity(self.source) != self._declared:
            raise ValueError(SOURCE_CHANGED)
        identity = frame_identity(frame)
        expected_source = self.declared("source_id")
        if expected_source is not None and identity[0] != expected_source:
            raise ValueError(SOURCE_CHANGED)
        with self._lock:
            if self._pinned is None:
                self._pinned = identity
            pinned = self._pinned
        if identity[0] != pinned[0] or identity[2] != pinned[2]:
            raise ValueError(SOURCE_CHANGED)
        if identity[1] != pinned[1]:
            raise ValueError(FORMAT_CHANGED)
        if self.mask is not None:
            self.mask.check_context(identity[0], CaptureFormat.from_mapping(native_format(frame)),
                                    self.declared("capture_layout"), self.mask.config)
