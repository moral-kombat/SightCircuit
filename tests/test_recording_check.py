"""Exercise container inspection; these generated headers are not decoder tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts import recording_check as recording


def box(kind, payload=b"", *, extended=False, to_end=False):
    if extended:
        return struct.pack(">I4sQ", 1, kind, len(payload) + 16) + payload
    return struct.pack(">I4s", 0 if to_end else len(payload) + 8, kind) + payload


def track(track_id, handler=b"vide", codec=b"av01", width=640, height=480,
          version=0, timescale=1000, duration=2500):
    tkhd = bytearray(96 if version else 84)
    tkhd[0] = version
    struct.pack_into(">I", tkhd, 20 if version else 12, track_id)
    struct.pack_into(">II", tkhd, 88 if version else 76, width << 16, height << 16)
    mdhd = bytearray(36 if version else 24)
    mdhd[0] = version
    struct.pack_into(">I", mdhd, 20 if version else 12, timescale)
    struct.pack_into(">Q" if version else ">I", mdhd, 24 if version else 16, duration)
    sample = bytearray(78 if handler == b"vide" else 8)
    if handler == b"vide":
        struct.pack_into(">HH", sample, 24, width, height)
    stsd = box(b"stsd", bytes(4) + struct.pack(">I", 1) + box(codec, sample))
    mdia = box(b"mdia", box(b"mdhd", mdhd) +
               box(b"hdlr", bytes(8) + handler + bytes(12)) +
               box(b"minf", box(b"stbl", stsd)))
    return box(b"trak", box(b"tkhd", tkhd) + mdia)


def movie(*tracks, extra=b"", extended=False):
    return box(b"ftyp", b"isom" + bytes(4) + b"isom") + box(
        b"moov", b"".join(tracks) + extra, extended=extended)


class RecordingCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "clip_stereo.mp4"

    def inspect(self, data):
        self.path.write_bytes(data)
        return recording.inspect_recording(self.path)

    def test_stereo_metadata_and_version_one_timing(self):
        report = self.inspect(movie(
            track(1), track(2, width=1280, height=720, version=1),
            track(3, handler=b"meta", codec=b"gpmd", width=0, height=0)))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["video_track_count"], 2)
        self.assertTrue(report["gpmd_sample_entry_present"])
        self.assertEqual([t["track_id"] for t in report["tracks"]], [1, 2, 3])
        video = report["tracks"][1]
        self.assertEqual(video["sample_entries"], [
            {"format": "av01", "coded_width": 1280, "coded_height": 720}])
        self.assertEqual((video["display_width"], video["display_height"]), (1280, 720))
        self.assertEqual(video["duration_seconds"], 2.5)
        self.assertFalse(report["media_payload_read"])
        self.assertEqual(report["decode_validation"], "not_performed")

    def test_extended_and_end_of_file_boxes(self):
        report = self.inspect(movie(track(1), extended=True) +
                              box(b"mdat", b"ignored", to_end=True))
        self.assertEqual(report["video_track_count"], 1)

    def test_unknown_duration(self):
        for version, duration in ((0, 2**32 - 1), (1, 2**64 - 1)):
            with self.subTest(version=version):
                report = self.inspect(movie(track(1, version=version, duration=duration)))
                self.assertIsNone(report["tracks"][0]["duration_seconds"])

    def test_media_payload_is_never_read(self):
        payload = b"private image and pose bytes" * 1000
        data = box(b"mdat", payload) + movie(track(1))
        self.path.write_bytes(data)

        class HeaderOnlyStream(io.BytesIO):
            def read(self, size=-1):
                start = self.tell()
                end = len(data) if size < 0 else start + size
                if start < 8 + len(payload) and end > 8:
                    raise AssertionError("Attempted to read media payload")
                return super().read(size)

        with patch.object(Path, "open", return_value=HeaderOnlyStream(data)):
            report = recording.inspect_recording(self.path)
        self.assertEqual(report["status"], "ok")

    def test_fragmented_and_encrypted_are_explicitly_unsupported(self):
        for data, reason in (
            (movie(track(1), extra=box(b"mvex")), "fragmented_mp4"),
            (movie(track(1)) + box(b"moof"), "fragmented_mp4"),
            (movie(track(1, codec=b"encv")), "encrypted_sample_entries"),
            (movie(track(1, handler=b"meta", codec=b"gpmd")), "no_video_tracks"),
        ):
            with self.subTest(reason=reason):
                report = self.inspect(data)
                self.assertEqual(report["status"], "unsupported")
                self.assertIn(reason, report["unsupported_reasons"])

    def test_malformed_headers_and_bounds(self):
        for data in (
            b"bad", struct.pack(">I4s", 4, b"free"),
            struct.pack(">I4s", 1, b"free"),
            struct.pack(">I4s", 1000, b"moov") + bytes(8),
            movie(track(1))[:-1], movie(track(1, timescale=0)),
            movie(track(1)) + box(b"moov"),
        ):
            with self.subTest(data=data[:16]):
                with self.assertRaises(recording.RecordingError):
                    self.inspect(data)

    def test_metadata_work_is_bounded(self):
        with patch.object(recording, "MAX_BOXES", 2):
            with self.assertRaisesRegex(recording.RecordingError, "box count limit"):
                self.inspect(box(b"free") * 3)
        with patch.object(recording, "MAX_HEADER_BYTES", 8):
            with self.assertRaisesRegex(recording.RecordingError, "read limit"):
                self.inspect(movie(track(1)))

    def test_rejects_nonregular_input_before_opening(self):
        fifo = self.directory / "pipe.mp4"
        os.mkfifo(fifo)
        with patch.object(Path, "open", side_effect=AssertionError("Must not open")):
            for path in (self.directory, fifo):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(recording.RecordingError, "regular file"):
                        recording.inspect_recording(path)

    def test_latest_selection_and_missing_recording_error_json(self):
        with self.assertRaises(recording.RecordingError):
            recording.latest_recording(self.directory)
        for name, timestamp in (("older_stereo.mp4", 100), ("newer_stereo.mp4", 200),
                                ("sample.mp4", 300)):
            path = self.directory / name
            path.write_bytes(movie(track(1)))
            os.utime(path, ns=(timestamp, timestamp))
        self.assertEqual(recording.latest_recording(self.directory).name, "newer_stereo.mp4")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = recording.main([str(self.directory / "missing.mp4")])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()
