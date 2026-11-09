"""Strict reader of the camera export v1 packet stream (``sightcircuit.camera.contract``).

``ExportReader`` reads packets from a binary stream or a file descriptor and
validates every byte of framing and every header field against the contract:
the prefix (magic, version, size bounds), the header (UTF-8, one JSON object
without duplicate keys, exactly the fields of its kind with their types and
bounds), the payload size, and the stream rules (``ready`` first and once,
``end`` last and once, consecutive sequences from 1 within ``max_frames``, a
camera the ``ready`` selected, the derivation of the declared pixel path,
``buffer_bytes`` = ``stride_bytes`` x ``height`` = ``payload_bytes``, the
``end`` counts matching the frames read, and the ``ready`` limits equal to the
ones requested). The first violation raises ``ExportRefused`` with a named
reason (``REASONS``); there is no recovery and no resync, and the reader reads
nothing further. ``frames()`` yields ``Frame``s built with ``Frame.from_native``:
source ``arcturus_camera_left``/``right`` by ``camera_index``, the stream
``sequence``, ``observed_ns`` from the host ``monotonic_ns`` of acquisition,
the ``BGR3`` native format from the header, and the acquisition format,
derivation and the other header facts in the frame's metadata. ``report()``
carries counts and headers, never pixels.
"""
import json
import re

from ..errors import SightCircuitError
from ..frame import Frame
from . import contract

READ_CHUNK = 1 << 20
# Every reason a refusal can name; field reasons carry the field path after the colon.
REASONS = (
    "truncated_prefix", "bad_magic", "unsupported_version", "header_empty", "header_too_large", "payload_too_large",
    "truncated_header", "truncated_payload", "header_not_utf8", "header_not_json", "duplicate_header_key",
    "header_not_object", "missing_kind", "unknown_kind", "ready_not_first", "ready_twice", "packet_after_end",
    "stream_ended_without_ready", "stream_ended_without_end", "missing_field:<path>", "unexpected_field:<path>",
    "invalid_field:<path>", "ready_payload_not_empty", "end_payload_not_empty", "limits_differ_from_requested",
    "stride_too_small", "buffer_bytes_mismatch", "payload_bytes_mismatch", "sequence_not_consecutive",
    "sequence_beyond_max_frames", "camera_not_selected", "derivation_mismatch", "exported_count_mismatch",
    "read_failed",
)


class ExportRefused(SightCircuitError):
    """The stream violated the contract; ``reason`` names the rule and ``packet`` the packet (1-based, 0 before any)."""

    def __init__(self, reason, message, packet=0):
        super().__init__(reason, message)
        self.packet = packet


class _Descriptor:
    """``read(n)`` over a file descriptor."""

    def __init__(self, fd):
        import os
        self.fd, self.os = fd, os

    def read(self, size):
        while True:
            try:
                return self.os.read(self.fd, size)
            except InterruptedError:
                continue


def _nonfinite(_text):
    raise ValueError("header_nonfinite_number")


def _pairs(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise KeyError("duplicate_header_key")
    return dict(pairs)


def check_field(field, value, path):
    """Validate ``value`` against ``field`` (``contract.Field``); raise ``ExportRefused`` naming ``path``."""
    kind = field.kind
    if kind == "int":
        if type(value) is not int or not field.minimum <= value <= field.maximum:
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be an integer in {field.minimum}..{field.maximum}")
    elif kind == "enum":
        if not any(type(value) is type(choice) and value == choice for choice in field.choices):
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be one of {list(field.choices)}")
    elif kind == "str":
        if (type(value) is not str or not field.minimum <= len(value) <= field.maximum or
                (field.pattern and not re.fullmatch(field.pattern, value))):
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be a string of at most {field.maximum} characters")
    elif kind == "object":
        if type(value) is not dict:
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be an object")
        check_fields(field.fields, value, path)
    elif kind == "array":
        if type(value) is not list or not field.minimum <= len(value) <= field.maximum:
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be a list of {field.minimum}..{field.maximum} items")
        for index, item in enumerate(value):
            check_field(field.items, item, f"{path}[{index}]")
    elif kind == "object_of":
        if (type(value) is not dict or len(value) > field.maximum or
                any(type(key) is not str or not 1 <= len(key) <= 64 for key in value)):
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be an object of at most {field.maximum} named entries")
        for key, item in value.items():
            check_field(field.items, item, f"{path}.{key}")
    elif kind == "scalars":
        if not _scalars(value, field.maximum, nested=True):
            raise ExportRefused(f"invalid_field:{path}", f"{path} must be an object of scalar facts")
    else:
        raise ValueError("contract_field_kind_unsupported")


def _scalars(value, maximum, nested):
    if type(value) is not dict or len(value) > maximum:
        return False
    for key, item in value.items():
        if type(key) is not str or not 1 <= len(key) <= 64:
            return False
        if type(item) is dict:
            if not nested or not _scalars(item, maximum, nested=False):
                return False
        elif item is not None and type(item) not in (str, int, bool):
            return False
        elif type(item) is str and len(item) > 256:
            return False
    return True


def check_fields(fields, header, path=""):
    """Exactly the contract's fields, in contract order: a missing or unexpected field is a refusal."""
    prefix = f"{path}." if path else ""
    names = [field.name for field in fields]
    for name in header:
        if name not in names:
            raise ExportRefused(f"unexpected_field:{prefix}{name}", f"{prefix}{name} is not a field of this header")
    for field in fields:
        if field.name not in header:
            raise ExportRefused(f"missing_field:{prefix}{field.name}", f"{prefix}{field.name} is required")
        check_field(field, header[field.name], f"{prefix}{field.name}")


class Packet:
    __slots__ = ("index", "kind", "header", "payload")

    def __init__(self, index, kind, header, payload):
        self.index, self.kind, self.header, self.payload = index, kind, header, payload


class ExportReader:
    """Packets and frames of one export stream; see the module docstring.

    ``stream`` has ``read(size)`` returning bytes (empty at end of stream), or
    is an integer file descriptor. ``limits``, when given, are the run limits
    the caller requested (``fps``, ``max_seconds``, ``max_frames``); the
    ``ready`` header must echo them exactly. After ``packets()`` or ``frames()``
    finishes or refuses, ``report()`` describes what was read.
    """

    def __init__(self, stream, *, limits=None):
        self.stream = _Descriptor(stream) if isinstance(stream, int) else stream
        self.limits = dict(limits) if limits is not None else None
        self.ready = None
        self.end = None
        self.packets_read = 0
        self.frames_read = 0
        self.bytes_read = 0
        self.dropped_total = 0
        self.camera_frames = {index: 0 for index in contract.CAMERA_INDICES}
        self.last_sequence = 0
        self.refusal = None
        self.complete = False

    # -- framing -------------------------------------------------------------------

    def _read_exactly(self, size, reason):
        chunks, remaining = [], size
        while remaining:
            try:
                chunk = self.stream.read(min(remaining, READ_CHUNK))
            except OSError as error:
                raise self._refuse("read_failed", f"reading the stream failed: {error}") from error
            if not chunk:
                raise self._refuse(reason, f"the stream ended {size - remaining} of {size} bytes into a packet part")
            chunks.append(chunk)
            remaining -= len(chunk)
            self.bytes_read += len(chunk)
        return b"".join(chunks)

    def _refuse(self, reason, message, packet=None):
        error = ExportRefused(reason, message, self.packets_read if packet is None else packet)
        self.refusal = {"reason": reason, "packet": error.packet, "detail": message}
        return error

    def _read_packet(self):
        """The next packet, or ``None`` at a clean end of stream."""
        try:
            head = self.stream.read(contract.PREFIX_BYTES)
        except OSError as error:
            raise self._refuse("read_failed", f"reading the stream failed: {error}") from error
        if not head:
            return None
        self.bytes_read += len(head)
        if len(head) < contract.PREFIX_BYTES:
            head += self._read_exactly(contract.PREFIX_BYTES - len(head), "truncated_prefix")
        index = self.packets_read + 1
        magic, version, header_bytes, payload_bytes = contract.unpack_prefix(head)
        if magic != contract.MAGIC:
            raise self._refuse("bad_magic", f"packet {index} does not start with {contract.MAGIC!r}", index)
        if version != contract.VERSION:
            raise self._refuse("unsupported_version", f"packet {index} declares version {version}", index)
        if header_bytes == 0:
            raise self._refuse("header_empty", f"packet {index} has no header", index)
        if header_bytes > contract.MAX_HEADER_BYTES:
            raise self._refuse("header_too_large", f"packet {index} header is {header_bytes} bytes", index)
        if payload_bytes > contract.MAX_PAYLOAD_BYTES:
            raise self._refuse("payload_too_large", f"packet {index} payload is {payload_bytes} bytes", index)
        header_text = self._read_exactly(header_bytes, "truncated_header")
        payload = self._read_exactly(payload_bytes, "truncated_payload")
        self.packets_read = index
        return Packet(index, None, header_text, payload)

    def _decode_header(self, packet):
        try:
            text = packet.header.decode("utf-8")
        except UnicodeDecodeError:
            raise self._refuse("header_not_utf8", f"packet {packet.index} header is not UTF-8") from None
        try:
            header = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        except KeyError:
            raise self._refuse("duplicate_header_key", f"packet {packet.index} header repeats a key") from None
        except ValueError:
            raise self._refuse("header_not_json", f"packet {packet.index} header is not JSON") from None
        if type(header) is not dict:
            raise self._refuse("header_not_object", f"packet {packet.index} header is not a JSON object")
        kind = header.get("kind")
        if "kind" not in header:
            raise self._refuse("missing_kind", f"packet {packet.index} header has no kind")
        if type(kind) is not str or kind not in contract.KINDS:
            raise self._refuse("unknown_kind", f"packet {packet.index} kind is not ready, frame or end")
        return kind, header

    # -- the stream rules ----------------------------------------------------------------

    def packets(self):
        """Yield every packet (``Packet`` with ``kind``, ``header`` and ``payload``) until the end of the stream."""
        if self.refusal is not None or self.complete:
            raise ValueError("export_reader_finished")
        while True:
            packet = self._read_packet()
            if packet is None:
                if self.ready is None:
                    raise self._refuse("stream_ended_without_ready", "the stream ended before a ready packet")
                if self.end is None:
                    raise self._refuse("stream_ended_without_end", "the stream ended before an end packet")
                self.complete = True
                return
            if self.end is not None:
                raise self._refuse("packet_after_end", f"packet {packet.index} follows the end packet")
            kind, header = self._decode_header(packet)
            packet.kind, packet.header = kind, header
            if kind == contract.KIND_READY:
                self._accept_ready(packet)
            elif self.ready is None:
                raise self._refuse("ready_not_first", f"packet {packet.index} ({kind}) precedes the ready packet")
            elif kind == contract.KIND_FRAME:
                self._accept_frame(packet)
            else:
                self._accept_end(packet)
            yield packet

    def _accept_ready(self, packet):
        if self.ready is not None:
            raise self._refuse("ready_twice", f"packet {packet.index} is a second ready packet")
        if packet.index != 1:
            raise self._refuse("ready_not_first", f"the ready packet is packet {packet.index}")
        header = packet.header
        try:
            check_fields(contract.HEADERS[contract.KIND_READY], header)
        except ExportRefused as error:
            raise self._refuse(error.reason, f"packet {packet.index}: {error}") from None
        cameras = header["cameras"]
        if cameras != sorted(set(cameras)):
            raise self._refuse("invalid_field:cameras", f"packet {packet.index}: cameras must be unique and ascending")
        if packet.payload:
            raise self._refuse("ready_payload_not_empty", f"packet {packet.index} carries {len(packet.payload)} payload bytes")
        if self.limits is not None and header["limits"] != self.limits:
            raise self._refuse("limits_differ_from_requested",
                               f"packet {packet.index}: the helper's limits {header['limits']} are not the requested {self.limits}")
        self.ready = header

    def _accept_frame(self, packet):
        header = packet.header
        try:
            check_fields(contract.HEADERS[contract.KIND_FRAME], header)
        except ExportRefused as error:
            raise self._refuse(error.reason, f"packet {packet.index}: {error}") from None
        index = packet.index
        if header["stride_bytes"] < header["width"] * 3:
            raise self._refuse("stride_too_small", f"packet {index}: stride_bytes is below width x 3")
        if header["buffer_bytes"] != header["stride_bytes"] * header["height"]:
            raise self._refuse("buffer_bytes_mismatch", f"packet {index}: buffer_bytes is not stride_bytes x height")
        if len(packet.payload) != header["buffer_bytes"]:
            raise self._refuse("payload_bytes_mismatch",
                               f"packet {index}: payload_bytes {len(packet.payload)} is not buffer_bytes {header['buffer_bytes']}")
        if header["sequence"] != self.last_sequence + 1:
            raise self._refuse("sequence_not_consecutive",
                               f"packet {index}: sequence {header['sequence']} after {self.last_sequence}")
        if header["sequence"] > self.ready["limits"]["max_frames"]:
            raise self._refuse("sequence_beyond_max_frames",
                               f"packet {index}: sequence {header['sequence']} exceeds max_frames")
        if header["camera_index"] not in self.ready["cameras"]:
            raise self._refuse("camera_not_selected", f"packet {index}: camera {header['camera_index']} was not selected")
        if header["derivation"] != contract.DERIVATIONS[self.ready["pixel_path"]]:
            raise self._refuse("derivation_mismatch",
                               f"packet {index}: derivation {header['derivation']} does not match {self.ready['pixel_path']}")
        self.last_sequence = header["sequence"]
        self.frames_read += 1
        self.camera_frames[header["camera_index"]] += 1
        self.dropped_total += header["dropped_since_last"]

    def _accept_end(self, packet):
        header = packet.header
        try:
            check_fields(contract.HEADERS[contract.KIND_END], header)
        except ExportRefused as error:
            raise self._refuse(error.reason, f"packet {packet.index}: {error}") from None
        if packet.payload:
            raise self._refuse("end_payload_not_empty", f"packet {packet.index} carries {len(packet.payload)} payload bytes")
        if header["counts"]["exported"] != self.frames_read:
            raise self._refuse("exported_count_mismatch",
                               f"packet {packet.index}: counts.exported {header['counts']['exported']} but {self.frames_read} frames were read")
        self.end = header

    # -- frames ----------------------------------------------------------------------

    def frames(self):
        """Yield a ``Frame`` for every frame packet (``frame_of``); ``ready`` and ``end`` are kept on the reader."""
        for packet in self.packets():
            if packet.kind == contract.KIND_FRAME:
                yield frame_of(packet, self.ready)

    def report(self):
        """Counts and the ready/end headers of what was read; never a pixel."""
        return {"protocol": contract.PROTOCOL,
                "status": "refused" if self.refusal is not None else "complete" if self.complete else "reading",
                "packets_read": self.packets_read, "frames_read": self.frames_read, "bytes_read": self.bytes_read,
                "frames_per_camera": {contract.source_id(index): count for index, count in self.camera_frames.items()},
                "dropped_since_last_total": self.dropped_total, "ready": self.ready, "end": self.end,
                "refusal": self.refusal}


def frame_of(packet, ready):
    """The ``Frame`` of a validated frame packet (``Frame.from_native``); see the module docstring for its fields."""
    header = packet.header
    layout = {"width": header["width"], "height": header["height"], "fourcc": header["fourcc"],
              "stride_bytes": header["stride_bytes"], "buffer_bytes": header["buffer_bytes"],
              "minimum_buffer_bytes": header["stride_bytes"] * header["height"]}
    metadata = {"acquisition_format": header["acquisition_format"], "derivation": header["derivation"],
                "camera_index": header["camera_index"], "frame_id": header["frame_id"],
                "exposure_ns": header["exposure_ns"], "realtime_ns": header["realtime_ns"],
                "dropped_since_last": header["dropped_since_last"], "pixel_path": ready["pixel_path"],
                "backend": ready["backend"], "export_packet": packet.index}
    return Frame.from_native(packet.payload, layout, contract.source_id(header["camera_index"]), header["sequence"],
                             header["monotonic_ns"], metadata=metadata)
