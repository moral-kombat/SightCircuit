"""The strict export reader against the fake producer's streams: every violation named, every frame built.

Standard library only (``Frame.from_native`` needs no NumPy).
"""
import io
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import contract, fake_export  # noqa: E402
from sightcircuit.camera.export_reader import REASONS, ExportReader, ExportRefused, check_field  # noqa: E402
from sightcircuit.frame import Frame, native_format  # noqa: E402

LIMITS = fake_export.LIMITS


def read_all(data, limits=LIMITS):
    reader = ExportReader(io.BytesIO(data), limits=limits)
    frames = list(reader.frames())
    return reader, frames


class ValidStreamTests(unittest.TestCase):
    def test_frames_carry_identity_layout_and_metadata(self):
        reader, frames = read_all(fake_export.valid_stream())
        self.assertEqual(len(frames), LIMITS["max_frames"])
        self.assertEqual([frame.sequence for frame in frames], list(range(1, LIMITS["max_frames"] + 1)))
        self.assertEqual([frame.source for frame in frames][:2], ["arcturus_camera_left", "arcturus_camera_right"])
        frame = frames[0]
        self.assertIsInstance(frame, Frame)
        self.assertEqual((frame.pixel_format, frame.width, frame.height, frame.stride),
                         ("BGR3", fake_export.WIDTH, fake_export.HEIGHT, fake_export.STRIDE))
        self.assertIsInstance(frame.pixels, bytes)
        self.assertEqual(len(frame.pixels), fake_export.STRIDE * fake_export.HEIGHT)
        self.assertEqual(frame.pixels, fake_export.pixels(1))
        self.assertEqual(frame.observed_ns, fake_export.frame_header(1)["monotonic_ns"])
        self.assertEqual(native_format(frame), {"buffer_bytes": fake_export.STRIDE * fake_export.HEIGHT,
                                                "fourcc": "BGR3", "height": fake_export.HEIGHT,
                                                "minimum_buffer_bytes": fake_export.STRIDE * fake_export.HEIGHT,
                                                "stride_bytes": fake_export.STRIDE, "width": fake_export.WIDTH})
        self.assertEqual(frame.metadata["acquisition_format"], fake_export.ACQUISITION_FORMAT)
        self.assertEqual(frame.metadata["derivation"], "cpu_converted")
        self.assertEqual(frame.metadata["camera_index"], 0)
        self.assertEqual(frame.metadata["frame_id"], 1001)
        self.assertEqual([frame.metadata["frame_set_sequence"] for frame in frames], [1, 1, 2, 2, 3, 3])
        self.assertEqual(report_sets := {(f.metadata["frame_set_sequence"], f.metadata["camera_index"]) for f in frames},
                         {(1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)})
        self.assertEqual(len(report_sets), 6)
        self.assertEqual(frame.metadata["pixel_path"], "cpu_linear")
        self.assertEqual(frame.metadata["backend"], "fake")
        self.assertEqual(frame.metadata["export_packet"], 2)
        self.assertIsNone(frame.media_time)
        report = reader.report()
        self.assertEqual((report["status"], report["packets_read"], report["frames_read"]),
                         ("complete", LIMITS["max_frames"] + 2, LIMITS["max_frames"]))
        self.assertEqual(report["frames_per_camera"], {"arcturus_camera_left": 3, "arcturus_camera_right": 3})
        self.assertEqual(report["ready"]["limits"], LIMITS)
        self.assertEqual(report["end"]["reason"], "max_frames")
        self.assertEqual(report["end"]["counts"]["exported"], LIMITS["max_frames"])
        self.assertIsNone(report["refusal"])
        self.assertEqual(report["bytes_read"], len(fake_export.valid_stream()))

    def test_report_never_holds_pixels(self):
        reader, frames = read_all(fake_export.valid_stream())
        text = repr(reader.report())
        self.assertNotIn(repr(frames[0].pixels[:16])[2:-1], text)
        self.assertLess(len(text), 4096)

    def test_gpu_path_single_camera_and_time_bound(self):
        reader, frames = read_all(fake_export.valid_stream(3, cameras=(1,), pixel_path="gpu_convert",
                                                           reason="max_seconds"))
        self.assertEqual([frame.source for frame in frames], ["arcturus_camera_right"] * 3)
        self.assertEqual([frame.metadata["frame_set_sequence"] for frame in frames], [1, 2, 3])
        self.assertEqual(frames[0].metadata["derivation"], "gpu_converted")
        self.assertEqual((reader.ready["confirmation"], reader.end["confirmation"]), ("parent", "parent"))
        self.assertEqual(reader.report()["end"]["reason"], "max_seconds")

    def test_an_undecided_ready_is_complete_only_with_no_frame(self):
        reader, frames = read_all(fake_export.undecided_stream())
        self.assertEqual(frames, [])
        report = reader.report()
        self.assertEqual((report["status"], report["ready"]["pixel_path"], report["end"]["reason"]),
                         ("complete", "undecided", "watchdog"))
        reader, frames = read_all(fake_export.undecided_stream(reason="headset_removed"))
        self.assertEqual(reader.report()["end"]["reason"], "headset_removed")

    def test_requested_limits_are_optional_and_zero_frames_is_complete(self):
        reader, frames = read_all(fake_export.valid_stream(0), limits=None)
        self.assertEqual(frames, [])
        self.assertEqual(reader.report()["status"], "complete")

    def test_file_descriptor_streams_are_read_the_same(self):
        import os
        read_fd, write_fd = os.pipe()
        data = fake_export.valid_stream()
        os.write(write_fd, data)
        os.close(write_fd)
        try:
            reader = ExportReader(read_fd, limits=LIMITS)
            self.assertEqual(len(list(reader.frames())), LIMITS["max_frames"])
        finally:
            os.close(read_fd)

    def test_a_finished_reader_cannot_be_reused(self):
        reader, _ = read_all(fake_export.valid_stream())
        with self.assertRaises(ValueError):
            list(reader.frames())


class InvalidStreamTests(unittest.TestCase):
    def test_every_invalid_stream_is_refused_with_its_reason_and_no_frame_after_it(self):
        for name, (build, expected) in fake_export.INVALID.items():
            with self.subTest(stream=name):
                reader = ExportReader(io.BytesIO(build()), limits=LIMITS)
                with self.assertRaises(ExportRefused) as caught:
                    list(reader.frames())
                self.assertEqual(caught.exception.reason, expected)
                report = reader.report()
                self.assertEqual(report["status"], "refused")
                self.assertEqual(report["refusal"]["reason"], expected)
                self.assertEqual(report["refusal"]["packet"], caught.exception.packet)
                with self.assertRaises(ValueError):  # No recovery, no resync.
                    list(reader.frames())

    def test_the_catalogue_covers_every_named_reason_and_every_rule(self):
        reasons = {expected.split(":")[0] for _, expected in fake_export.INVALID.values()}
        named = {reason.split(":")[0] for reason in REASONS} - {"read_failed"}
        self.assertEqual(named - reasons, set(), "a reason no fake stream exercises")
        self.assertEqual(reasons - named, set(), "a reason the module does not name")
        rules = {rule for rule in contract.STREAM_RULES}
        self.assertEqual(len(rules), len(contract.STREAM_RULES))

    def test_refusal_stops_before_reading_an_oversize_payload(self):
        build, expected = fake_export.INVALID["payload_too_large"]
        reader = ExportReader(io.BytesIO(build()), limits=LIMITS)
        with self.assertRaises(ExportRefused):
            list(reader.frames())
        self.assertLess(reader.bytes_read, 1024)

    def test_a_header_of_sixteen_thousand_brackets_is_refused_not_raised(self):
        import sys
        data = fake_export.encode(None, header_text=b"[" * 16000)
        reader = ExportReader(io.BytesIO(data), limits=LIMITS)
        with self.assertRaises(ExportRefused) as caught:  # Never a RecursionError out of frames().
            list(reader.frames())
        self.assertEqual(caught.exception.reason, "header_not_json")
        self.assertLess(16000, sys.getrecursionlimit() * 20)  # The case is deep enough to matter on any limit.
        from sightcircuit.camera.export_reader import MAX_HEADER_DEPTH, depth_of
        self.assertEqual(MAX_HEADER_DEPTH, 5)
        self.assertEqual(depth_of(1), 1)
        self.assertEqual(depth_of({"a": [{"b": 1}]}), 4)
        self.assertEqual(depth_of(fake_export.frame_header(1)), 5)  # acquisition_format.planes[0].offset, a scalar
        self.assertEqual(depth_of(fake_export.ready_header()), 4)  # build.sdk_release.source_revision
        self.assertEqual(depth_of(fake_export.end_header(0)), 3)  # counts.exported

    def test_read_failure_is_a_refusal(self):
        class Broken:
            def read(self, size):
                raise OSError("boom")
        reader = ExportReader(Broken())
        with self.assertRaises(ExportRefused) as caught:
            list(reader.frames())
        self.assertEqual(caught.exception.reason, "read_failed")

    def test_frame_after_a_refusal_is_never_delivered(self):
        data = fake_export._ready() + fake_export.frame_packet(1, camera_index=0) + \
            fake_export.frame_packet(3, camera_index=0) + fake_export.frame_packet(4, camera_index=0)
        reader = ExportReader(io.BytesIO(data), limits=LIMITS)
        delivered = []
        with self.assertRaises(ExportRefused):
            for frame in reader.frames():
                delivered.append(frame.sequence)
        self.assertEqual(delivered, [1])


class FieldTests(unittest.TestCase):
    def test_booleans_floats_and_wrong_types_are_refused(self):
        fields = {field.name: field for field in contract.HEADERS["frame"]}
        field = fields["sequence"]
        for value in (True, 1.0, "1", None, [1], 0, contract.LIMITS["max_frames"][1] + 1):
            with self.subTest(value=value), self.assertRaises(ExportRefused):
                check_field(field, value, "sequence")
        check_field(field, 1, "sequence")
        check_field(field, contract.LIMITS["max_frames"][1], "sequence")
        enum = fields["camera_index"]
        with self.assertRaises(ExportRefused):
            check_field(enum, False, "camera_index")  # False == 0 but is not an int choice.
        check_field(enum, 0, "camera_index")

    def test_scalars_object_accepts_one_level_of_nesting_only(self):
        build = {field.name: field for field in contract.HEADERS["ready"]}["build"]
        check_field(build, {"a": 1, "b": "x", "c": None, "d": True, "e": {"f": 2}}, "build")
        for value in ({"a": {"b": {"c": 1}}}, {"a": 1.5}, {"a": [1]}, {"": 1}, {"a": "x" * 257}, "x"):
            with self.subTest(value=value), self.assertRaises(ExportRefused):
                check_field(build, value, "build")

    def test_unknown_field_kind_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            check_field(contract.Field("x", "float"), 1.0, "x")


class FromNativeTests(unittest.TestCase):
    def test_from_native_validates_identity_layout_and_size(self):
        layout = {"width": 2, "height": 2, "fourcc": "BGR3", "stride_bytes": 8, "buffer_bytes": 16,
                  "minimum_buffer_bytes": 16}
        frame = Frame.from_native(bytearray(16), layout, "arcturus_camera_left", 1, 5, metadata={"derivation": "x"})
        self.assertIsInstance(frame.pixels, bytes)
        self.assertEqual((frame.width, frame.height, frame.stride, frame.pixel_format), (2, 2, 8, "BGR3"))
        self.assertEqual(frame.metadata, {"native_format": dict(sorted(layout.items())), "derivation": "x"})
        cases = (
            (bytes(15), layout, "arcturus_camera_left", 1, 5, None, "native_pixels_size_mismatch"),
            (bytes(16), layout, "arcturus_camera_left", 0, 5, None, "invalid_frame_identity_or_observation_time"),
            (bytes(16), layout, "", 1, 5, None, "invalid_source_identity"),
            (bytes(16), dict(layout, fourcc="NV12"), "s", 1, 5, None, "invalid_native_frame_layout"),
            (bytes(16), dict(layout, stride_bytes=5, minimum_buffer_bytes=10, buffer_bytes=16), "s", 1, 5, None,
             "invalid_native_frame_layout"),
            (bytes(16), dict(layout, width=0), "s", 1, 5, None, "invalid_native_frame_layout"),
            (bytes(16), [], "s", 1, 5, None, "invalid_native_frame_layout"),
            (object(), layout, "s", 1, 5, None, "native_pixels_not_a_buffer"),
            (bytes(16), layout, "s", 1, 5, {"native_format": {}}, "native_format_declared_twice"),
        )
        for pixels, native, source, sequence, observed, metadata, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(ValueError) as caught:
                Frame.from_native(pixels, native, source, sequence, observed, metadata=metadata)
            self.assertEqual(str(caught.exception), reason)


if __name__ == "__main__":
    unittest.main()
