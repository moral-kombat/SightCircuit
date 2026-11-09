"""Camera export v1: the packet stream of ``sightcircuit-camera-helper --export-frames``, as data.

The helper's stdout is a sequence of packets and nothing else. A packet is a
16-byte prefix (``PREFIX_FORMAT``: the magic ``SCCF``, the version, the header
size and the payload size, little-endian), then ``header_bytes`` of UTF-8 JSON
(one object, at most ``MAX_HEADER_BYTES``), then ``payload_bytes`` of payload
(at most ``MAX_PAYLOAD_BYTES``). The header's ``kind`` is ``ready``, ``frame``
or ``end``; exactly one ``ready`` comes first and exactly one ``end`` comes
last; ``ready`` and ``end`` carry no payload and a ``frame``'s payload is its
``BGR3`` pixels, ``stride_bytes`` per row. The helper's stderr carries JSON
diagnostic lines only.

``HEADERS`` names every field each kind must carry, with its type and bounds
(``Field``), and nothing else may appear. ``LIMITS`` bounds the run limits the
helper accepts and echoes in ``ready``. The format vocabulary (pixel formats,
encodings, transfer functions, primaries) is the helper's spelling of the SDK
enumerators (``tools/camera_backend_sdk.cpp``); ``unknown`` is not a value.
``frame_set_sequence`` names the acquired set a frame belongs to: it starts at
1, rises by one per exported set, is equal on the two cameras of one set and
each camera appears once in a set, so a reader groups the two cameras by it,
never by adjacency. ``camera_index`` 0 is reported as the left camera and 1 as the right
(``SOURCE_IDS``): the assignment is the helper's, made the way the SDK example
does it, from the camera transforms, and nothing in Python verifies it.

``scripts/camera_contract_header.py`` generates ``tools/camera_export_protocol.hpp``
from this module (``sightcircuit.camera.native``); ``export_reader`` enforces
it. Detections derived from these frames are Camera Data under the SDK
agreement (sections 1 and 3.2): saving or sending them needs the operator's
explicit, informed authorization. Nothing here opens a camera or lifts a gate.
"""
from dataclasses import dataclass
import struct

from ..frame import MAX_NATIVE_SIDE

PROTOCOL = "camera-export/1"
MAGIC = b"SCCF"
VERSION = 1
# The packet prefix: magic, version, header_bytes, payload_bytes; little-endian, 16 bytes.
PREFIX_FIELDS = (("magic", "4s"), ("version", "I"), ("header_bytes", "I"), ("payload_bytes", "I"))
PREFIX_FORMAT = "<" + "".join(code for _, code in PREFIX_FIELDS)
PREFIX_BYTES = struct.calcsize(PREFIX_FORMAT)
MAX_HEADER_BYTES = 16384
MAX_PAYLOAD_BYTES = 33554432
FOURCC = "BGR3"
MAX_SIDE = MAX_NATIVE_SIDE  # Frames keep the native bound; the payload bound (32 MiB) is the tighter of the two.
MAX_PLANES = 4

KIND_READY, KIND_FRAME, KIND_END = KINDS = ("ready", "frame", "end")
BACKENDS = ("fake", "sdk")
PIXEL_PATHS = ("cpu_linear", "gpu_convert")
# Which derivation a frame of each pixel path declares.
DERIVATIONS = {"cpu_linear": "cpu_converted", "gpu_convert": "gpu_converted"}
END_REASONS = ("max_seconds", "max_frames", "stop_signal", "headset_removed", "standby", "sdk_error",
               "pixel_path_error", "watchdog", "consumer_stalled")
# How the informed start was given: by the parent process on the operator's terminal (``--parent-confirmed``) or on
# the helper's own terminal; ``ready`` and ``end`` say which.
CONFIRMATIONS = ("parent", "terminal")
PARENT_CONFIRMED_FLAG = "--parent-confirmed"  # Accepted by the helper only together with --export-frames.
# The export mode's notice, printed verbatim by whichever side asks for the typed confirmation (owner decision 2026-11-09).
EXPORT_NOTICE = (
    "sightcircuit-camera-helper --export-frames: activates this client's frame reader for the selected passthrough "
    "cameras and exports their frames, within the frame and time bounds shown below, to the parent process, which runs "
    "object detection on them. Detections are Camera Data: the parent saves counts, labels, boxes and timings only to "
    "the report you name, and no image is saved or shown anywhere. No camera control is written and the recording LED "
    "is untouched. The export ends on its bound, on a stop signal, when the headset is removed or on standby. Watch "
    "passthrough during the run and for 30 seconds afterwards. Type yes to continue.")
# End reasons that complete a run the operator asked for; every other reason is a failure of the helper's.
BOUNDED_END_REASONS = ("max_seconds", "max_frames", "stop_signal")
CAMERA_INDICES = (0, 1)
SOURCE_IDS = ("arcturus_camera_left", "arcturus_camera_right")  # Indexed by camera_index.
PIXEL_FORMATS = ("nv12", "nv12_ubwc", "p010", "p010_ubwc")
ENCODINGS = ("bt601", "bt709", "bt2020")
TRANSFERS = ("linear", "srgb", "hlg")
PRIMARIES = ("bt709", "bt2020")
HEX_U32 = r"0x[0-9a-f]{8}"
HEX_U64 = r"0x[0-9a-f]{16}"
SHA256_HEX = r"[0-9a-f]{64}"
U32_MAX = 2**32 - 1
U64_MAX = 2**64 - 1
I64_MIN, I64_MAX = -2**63, 2**63 - 1

# The run limits the helper accepts (``--fps``, ``--max-seconds``, ``--max-frames``) and echoes in ``ready``: the
# helper's own bounds (work-order item S6). ``camera_recognize`` asks for at most 30 s and 300 frames (item S7).
LIMITS = {"fps": (1, 10), "max_seconds": (1, 60), "max_frames": (1, 600)}


@dataclass(frozen=True)
class Field:
    """One header field: its JSON type and bounds.

    ``kind`` is ``int`` (a JSON integer, never a boolean, within ``minimum`` ..
    ``maximum``), ``enum`` (one of ``choices``), ``str`` (a string of 1 ..
    ``maximum`` characters, matching ``pattern`` when one is given), ``object``
    (exactly the fields ``fields``), ``array`` (``minimum`` .. ``maximum``
    items, each an ``items`` field), ``object_of`` (an object of at most
    ``maximum`` entries, each value an ``items`` field, for the end digests) or
    ``scalars`` (an object of at most ``maximum`` entries whose values are
    strings, integers, booleans or null, or objects of such values, for the
    helper's build facts).
    """
    name: str
    kind: str
    minimum: int = 0
    maximum: int = U64_MAX
    choices: tuple = ()
    pattern: str = ""
    fields: tuple = ()
    items: object = None


def _int(name, minimum=0, maximum=U64_MAX):
    return Field(name, "int", minimum, maximum)


def _enum(name, choices):
    return Field(name, "enum", choices=tuple(choices))


def _str(name, maximum=96, pattern=""):
    return Field(name, "str", 1, maximum, pattern=pattern)


PLANE_FIELDS = (_int("offset", 0, U32_MAX), _int("stride", 0, U32_MAX), _int("size", 0, U32_MAX))
ACQUISITION_FORMAT_FIELDS = (
    _str("drm_fourcc", 10, HEX_U32),  # The DRM FourCC code as the helper spells it, "0x" and eight hex digits.
    _str("modifier", 18, HEX_U64),  # The DRM format modifier, "0x" and sixteen hex digits.
    _int("plane_count", 1, MAX_PLANES),
    Field("planes", "array", 1, MAX_PLANES, items=Field("plane", "object", fields=PLANE_FIELDS)),
    _enum("pixel_format", PIXEL_FORMATS),
    _enum("encoding", ENCODINGS),
    _enum("transfer", TRANSFERS),
    _enum("primaries", PRIMARIES),
)
LIMIT_FIELDS = tuple(_int(name, low, high) for name, (low, high) in LIMITS.items())
COUNT_FIELDS = (_int("exported", 0, U32_MAX), _int("dropped", 0, U32_MAX), _int("acquire_failures", 0, U32_MAX),
                _int("release_failures", 0, U32_MAX))

# Every field each kind must carry, and no other. ``kind`` itself is checked first.
HEADERS = {
    KIND_READY: (
        _enum("kind", (KIND_READY,)),
        _int("protocol", VERSION, VERSION),
        _enum("backend", BACKENDS),
        _enum("pixel_path", PIXEL_PATHS),
        Field("build", "scalars", 0, 32),
        Field("delivered_format", "object", fields=(_enum("fourcc", (FOURCC,)),)),
        Field("cameras", "array", 1, len(CAMERA_INDICES), items=_enum("camera", CAMERA_INDICES)),
        Field("limits", "object", fields=LIMIT_FIELDS),
        _enum("confirmation", CONFIRMATIONS),
    ),
    KIND_FRAME: (
        _enum("kind", (KIND_FRAME,)),
        _int("sequence", 1, LIMITS["max_frames"][1]),
        _int("frame_set_sequence", 1, U64_MAX),
        _int("frame_id", 0, U64_MAX),
        _enum("camera_index", CAMERA_INDICES),
        _int("exposure_ns", I64_MIN, I64_MAX),
        _int("monotonic_ns", 0, I64_MAX),
        _int("realtime_ns", 0, I64_MAX),
        _int("width", 1, MAX_SIDE),
        _int("height", 1, MAX_SIDE),
        _enum("fourcc", (FOURCC,)),
        _int("stride_bytes", 3, MAX_PAYLOAD_BYTES),
        _int("buffer_bytes", 3, MAX_PAYLOAD_BYTES),
        _enum("derivation", tuple(DERIVATIONS.values())),
        Field("acquisition_format", "object", fields=ACQUISITION_FORMAT_FIELDS),
        _int("dropped_since_last", 0, U32_MAX),
    ),
    KIND_END: (
        _enum("kind", (KIND_END,)),
        _enum("reason", END_REASONS),
        Field("counts", "object", fields=COUNT_FIELDS),
        _int("elapsed_ns", 0, I64_MAX),
        Field("digests", "object_of", 0, 16, items=_str("digest", 64, SHA256_HEX)),
        _enum("confirmation", CONFIRMATIONS),
    ),
}

# Rules the reader enforces across packets, named for the report; listed so the header carries them.
STREAM_RULES = (
    "ready_first", "ready_once", "end_last", "end_once", "sequence_consecutive_from_1",
    "sequence_within_max_frames", "camera_in_ready_cameras", "derivation_matches_pixel_path",
    "buffer_bytes_is_stride_times_height", "payload_bytes_is_buffer_bytes", "stride_at_least_width_times_3",
    "ready_payload_empty", "end_payload_empty", "end_exported_equals_frames", "limits_equal_requested",
    "frame_set_sequence_consecutive_from_1", "frame_set_cameras_distinct",
)


def field_names(kind):
    """The exact header fields of ``kind``, in contract order."""
    return tuple(field.name for field in HEADERS[kind])


def source_id(camera_index):
    """The frame source identity of a camera index, or ``ValueError``."""
    if camera_index not in CAMERA_INDICES:
        raise ValueError("camera_index_unsupported")
    return SOURCE_IDS[CAMERA_INDICES.index(camera_index)]


def pack_prefix(header_bytes, payload_bytes, *, magic=MAGIC, version=VERSION):
    """The 16-byte packet prefix; no bound is checked here (the fake producer writes invalid prefixes with it)."""
    return struct.pack(PREFIX_FORMAT, magic, version, header_bytes, payload_bytes)


def unpack_prefix(data):
    """``(magic, version, header_bytes, payload_bytes)`` of a 16-byte prefix."""
    if len(data) != PREFIX_BYTES:
        raise ValueError("prefix_size")
    return struct.unpack(PREFIX_FORMAT, data)
