"""Read MP4 recording headers without decoding or reading media/pose payloads.

One box-header decoder (``box_header``) serves two walkers with deliberately
different policies: ``Reader``/``inspect_recording`` for finished files (a cut
or crossing box is an error; a size-0 box runs to its container's end), used by
the replay window and ``recording_check``; and ``scan_headers`` for a file that
is still being written (a cut or open-ended tail is pending), used by
``recording_growth_check``. ``latest_recording`` selects the newest Arcturus
``*_stereo.mp4`` file.
"""
from dataclasses import dataclass
from pathlib import Path
import struct


MAX_BOXES = 10000
MAX_HEADER_BYTES = 2 * 1024 * 1024
MAX_TRACKS = 64
MAX_SAMPLE_ENTRIES = 64


class RecordingError(ValueError):
    pass


def fourcc(value):
    return "".join(chr(byte) if 32 <= byte <= 126 else "?" for byte in value)


@dataclass(frozen=True)
class Box:
    kind: bytes
    payload: int
    end: int


@dataclass(frozen=True)
class BoxHeader:
    """One decoded MP4 box header, before any container policy is applied."""
    kind: bytes = b""
    size: int = 0  # Decoded 32-bit or 64-bit size; 0 for the open-ended marker.
    header_size: int = 8  # 16 when a 64-bit size follows.
    open_ended: bool = False  # 32-bit size 0: the box runs to the end of its container.
    truncated: str | None = None  # "header" or "extended_header" when the bound cuts the header.


def box_header(read, offset, available):
    """Decode the box header at ``offset`` with ``available`` bytes before its bound.

    ``read(offset, count)`` is the caller's bounded reader. Policy stays with the
    caller: a finished file treats truncation as an error and an open-ended box
    as running to its container's end; a growing file reports both as pending.
    """
    if available < 8:
        return BoxHeader(truncated="header")
    size, kind = struct.unpack(">I4s", read(offset, 8))
    if size == 1:
        if available < 16:
            return BoxHeader(kind, truncated="extended_header")
        return BoxHeader(kind, struct.unpack(">Q", read(offset + 8, 8))[0], 16)
    return BoxHeader(kind, size, 8, open_ended=size == 0)


class Reader:
    def __init__(self, stream, size):
        self.stream, self.size = stream, size
        self.box_count = self.header_bytes = 0

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise RecordingError("Header extends beyond the file")
        self.header_bytes += size
        if self.header_bytes > MAX_HEADER_BYTES:
            raise RecordingError("Metadata header read limit exceeded")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise RecordingError("File was truncated while reading a header")
        return data

    def boxes(self, start, end):
        while start < end:
            self.box_count += 1
            if self.box_count > MAX_BOXES:
                raise RecordingError("MP4 box count limit exceeded")
            head = box_header(self.read, start, end - start)
            if head.truncated == "header":
                raise RecordingError("Truncated MP4 box header")
            if head.truncated:
                raise RecordingError("Truncated extended MP4 box header")
            size = end - start if head.open_ended else head.size
            if size < head.header_size or size > end - start:
                raise RecordingError("MP4 box size crosses its parent or file boundary")
            yield Box(head.kind, start + head.header_size, start + size)
            start += size

    def prefix(self, box, size):
        if size > box.end - box.payload:
            raise RecordingError(f"Truncated {fourcc(box.kind)} metadata")
        return self.read(box.payload, size)


def children(reader, box):
    return list(reader.boxes(box.payload, box.end))


def single(boxes, kind):
    matches = [box for box in boxes if box.kind == kind]
    if len(matches) != 1:
        raise RecordingError(f"Expected one {fourcc(kind)} box; found {len(matches)}")
    return matches[0]


def track_metadata(reader, track):
    boxes = children(reader, track)
    tkhd = single(boxes, b"tkhd")
    version = reader.prefix(tkhd, 4)[0]
    if version not in (0, 1):
        raise RecordingError("Unsupported tkhd version")
    header = reader.prefix(tkhd, 96 if version else 84)
    track_id = struct.unpack_from(">I", header, 20 if version else 12)[0]
    width, height = struct.unpack_from(">II", header, 88 if version else 76)
    media = children(reader, single(boxes, b"mdia"))
    handler = fourcc(reader.prefix(single(media, b"hdlr"), 12)[8:12])
    mdhd = single(media, b"mdhd")
    version = reader.prefix(mdhd, 4)[0]
    if version not in (0, 1):
        raise RecordingError("Unsupported mdhd version")
    header = reader.prefix(mdhd, 36 if version else 24)
    timescale = struct.unpack_from(">I", header, 20 if version else 12)[0]
    duration = struct.unpack_from(">Q" if version else ">I", header,
                                  24 if version else 16)[0]
    if duration == (2 ** (64 if version else 32)) - 1:
        duration = None
    if timescale == 0:
        raise RecordingError("Track media timescale is zero")
    sample_table = children(reader, single(children(reader, single(media, b"minf")), b"stbl"))
    stsd = single(sample_table, b"stsd")
    header = reader.prefix(stsd, 8)
    if header[0] != 0:
        raise RecordingError("Unsupported stsd version")
    count = struct.unpack_from(">I", header, 4)[0]
    if count > MAX_SAMPLE_ENTRIES:
        raise RecordingError("Sample entry count limit exceeded")
    entries = list(reader.boxes(stsd.payload + 8, stsd.end))
    if len(entries) != count:
        raise RecordingError("Sample entry count does not match stsd")
    formats = []
    for entry in entries:
        info = {"format": fourcc(entry.kind)}
        if handler == "vide" and entry.kind != b"encv":
            visual = reader.prefix(entry, 78)
            info["coded_width"], info["coded_height"] = struct.unpack_from(">HH", visual, 24)
        formats.append(info)
    return {
        "track_id": track_id,
        "handler_type": handler,
        "display_width": width / 65536,
        "display_height": height / 65536,
        "media_timescale": timescale,
        "media_duration_ticks": duration,
        "duration_seconds": duration / timescale if duration is not None else None,
        "sample_entries": formats,
    }


def inspect_recording(path):
    """Return a report for a single path; malformed/unreadable inputs raise errors."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise RecordingError("Recording path must be a regular file")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        reader = Reader(stream, size)
        top = list(reader.boxes(0, size))
        moov = single(top, b"moov")
        movie = children(reader, moov)
        tracks = [box for box in movie if box.kind == b"trak"]
        if not tracks or len(tracks) > MAX_TRACKS:
            raise RecordingError("Missing tracks or track count limit exceeded")
        metadata = [track_metadata(reader, track) for track in tracks]
        fragmented = any(box.kind == b"moof" for box in top) or any(
            box.kind == b"mvex" for box in movie)
        formats = [entry["format"] for track in metadata for entry in track["sample_entries"]]
        encrypted = any(value in ("encv", "enca") for value in formats)
        videos = sum(track["handler_type"] == "vide" for track in metadata)
        reasons = []
        if fragmented:
            reasons.append("fragmented_mp4")
        if encrypted:
            reasons.append("encrypted_sample_entries")
        if not videos:
            reasons.append("no_video_tracks")
        return {
            "report_kind": "arcturus_recording_metadata",
            "schema_version": 1,
            "scope": "MP4 headers only; video decoding and pose/calibration validation not performed",
            "status": "unsupported" if reasons else "ok",
            "unsupported_reasons": reasons,
            "path": str(path),
            "file_size_bytes": size,
            "video_track_count": videos,
            "gpmd_sample_entry_present": "gpmd" in formats,
            "fragmented": fragmented,
            "encrypted_sample_entries": encrypted,
            "tracks": metadata,
            "media_payload_read": False,
            "decode_validation": "not_performed",
            "notes": [
                "Track order does not establish left/right camera identity.",
                "Container metadata does not prove usable frames or valid calibration.",
                "The recording was opened read-only; media and pose payloads were skipped.",
            ],
        }


def latest_recording(directory):
    candidates = (path for path in directory.glob("*_stereo.mp4") if path.is_file())
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except ValueError as error:
        raise RecordingError(f"No *_stereo.mp4 recordings found in {directory}") from error


# Growing files: one top-level scan per observation, never media payloads.
SCAN_MAX_BOXES = 8192
MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024


class GrowthError(ValueError):
    pass


class Budget:
    """Header bytes one observation run may read, across all of its rescans."""
    def __init__(self, maximum=MAX_HEADER_BYTES):
        self.maximum, self.read_bytes = maximum, 0

    def read(self, stream, offset, count):
        if self.read_bytes + count > self.maximum:
            raise GrowthError("header_read_budget_exhausted")
        stream.seek(offset)
        data = stream.read(count)
        self.read_bytes += len(data)
        if len(data) != count:
            raise GrowthError("file_truncated_during_header_read")
        return data


def scan_headers(stream, size, budget):
    """File extents bound boxes; they do not certify payload completion/ownership."""
    if not 0 <= size <= MAX_FILE_BYTES:
        raise GrowthError("file_size_out_of_bounds")
    offset, pending, movie_seen, fragment = 0, None, False, None
    headers, pairs = [], []
    while offset < size:
        if len(headers) >= SCAN_MAX_BOXES:
            raise GrowthError("box_count_limit")
        head = box_header(lambda at, count: budget.read(stream, at, count), offset, size - offset)
        if head.truncated:
            pending = "partial_header" if head.truncated == "header" else "partial_extended_header"
            break
        length, kind = head.size, head.kind
        if head.open_ended or length == 0:
            pending = "open_ended_box"
            break
        if length < head.header_size:
            raise GrowthError("invalid_box_length")
        if length > size - offset:
            pending = "incomplete_box_extent"
            break
        headers.append((offset, length, kind.hex()))
        if kind == b"moov":
            movie_seen = True
        elif kind == b"moof":
            fragment = offset if movie_seen else None
        elif kind == b"mdat":
            if fragment is not None:
                pairs.append((fragment, offset + length))
            fragment = None
        elif kind not in (b"free", b"skip"):
            fragment = None
        offset += length  # Skip every payload, including moov/moof and mdat.
    return {"headers": headers, "pairs": pairs, "movie_header_present": movie_seen,
            "pending": pending, "bounded_prefix_bytes": offset}
