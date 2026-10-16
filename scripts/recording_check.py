"""Inspect MP4 recording headers without decoding or reading media/pose payloads."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import sys


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
            if end - start < 8:
                raise RecordingError("Truncated MP4 box header")
            size, kind = struct.unpack(">I4s", self.read(start, 8))
            header = 8
            if size == 1:
                if end - start < 16:
                    raise RecordingError("Truncated extended MP4 box header")
                size = struct.unpack(">Q", self.read(start + 8, 8))[0]
                header = 16
            elif size == 0:
                size = end - start
            if size < header or size > end - start:
                raise RecordingError("MP4 box size crosses its parent or file boundary")
            yield Box(kind, start + header, start + size)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="MP4 path; defaults to newest Arcturus stereo recording")
    args = parser.parse_args(argv)
    path = None
    try:
        path = Path(args.path).expanduser() if args.path else latest_recording(
            Path.home() / "Videos" / "ArcturusVisionCamera")
        report = inspect_recording(path)
    except (OSError, RecordingError) as error:
        report = {"report_kind": "arcturus_recording_metadata", "schema_version": 1,
                  "scope": "MP4 headers only; no video decoding performed", "status": "error",
                  "path": str(path) if path is not None else None, "error": str(error)}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
