"""Camera export v1 as data: the contract module, its generated header and the header script.

Standard library only; nothing here opens a camera or builds the helper.
"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import contract, native  # noqa: E402
from sightcircuit.hud import native as hud_native  # noqa: E402

HEADER = ROOT / "tools/camera_export_protocol.hpp"


class ContractTests(unittest.TestCase):
    def test_prefix_layout_is_sixteen_little_endian_bytes(self):
        self.assertEqual(contract.PREFIX_FORMAT, "<4sIII")
        self.assertEqual(contract.PREFIX_BYTES, 16)
        self.assertEqual(contract.MAGIC, b"SCCF")
        self.assertEqual(contract.VERSION, 1)
        prefix = contract.pack_prefix(5, 7)
        self.assertEqual(prefix, b"SCCF" + struct.pack("<III", 1, 5, 7))
        self.assertEqual(contract.unpack_prefix(prefix), (b"SCCF", 1, 5, 7))
        with self.assertRaises(ValueError):
            contract.unpack_prefix(prefix[:-1])

    def test_bounds_and_vocabulary(self):
        self.assertEqual((contract.MAX_HEADER_BYTES, contract.MAX_PAYLOAD_BYTES), (16384, 33554432))
        self.assertEqual(contract.KINDS, ("ready", "frame", "end"))
        self.assertEqual(contract.FOURCC, "BGR3")
        self.assertEqual(contract.END_REASONS, ("max_seconds", "max_frames", "stop_signal", "headset_removed",
                                                "standby", "sdk_error", "pixel_path_error", "watchdog"))
        self.assertTrue(set(contract.BOUNDED_END_REASONS) <= set(contract.END_REASONS))
        self.assertEqual(contract.DERIVATIONS, {"cpu_linear": "cpu_converted", "gpu_convert": "gpu_converted"})
        self.assertEqual(tuple(contract.DERIVATIONS), contract.PIXEL_PATHS)
        self.assertEqual(contract.LIMITS, {"fps": (1, 10), "max_seconds": (1, 60), "max_frames": (1, 600)})
        self.assertEqual(contract.MAX_SIDE, 4096)
        self.assertEqual(contract.source_id(0), "arcturus_camera_left")
        self.assertEqual(contract.source_id(1), "arcturus_camera_right")
        with self.assertRaises(ValueError):
            contract.source_id(2)

    def test_every_kind_names_its_fields_once_starting_with_kind(self):
        for kind in contract.KINDS:
            names = contract.field_names(kind)
            with self.subTest(kind=kind):
                self.assertEqual(names[0], "kind")
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(contract.HEADERS[kind][0].choices, (kind,))
        self.assertEqual(contract.field_names("ready"),
                         ("kind", "protocol", "backend", "pixel_path", "build", "delivered_format", "cameras", "limits"))
        self.assertEqual(contract.field_names("frame"),
                         ("kind", "sequence", "frame_id", "camera_index", "exposure_ns", "monotonic_ns", "realtime_ns",
                          "width", "height", "fourcc", "stride_bytes", "buffer_bytes", "derivation",
                          "acquisition_format", "dropped_since_last"))
        self.assertEqual(contract.field_names("end"), ("kind", "reason", "counts", "elapsed_ns", "digests"))

    def test_field_kinds_are_the_documented_ones(self):
        kinds = set()

        def visit(field):
            kinds.add(field.kind)
            for nested in field.fields:
                visit(nested)
            if field.items is not None:
                visit(field.items)
        for fields in contract.HEADERS.values():
            for field in fields:
                visit(field)
        self.assertEqual(kinds, {"int", "enum", "str", "object", "array", "object_of", "scalars"})


class HeaderTests(unittest.TestCase):
    def test_committed_header_matches_the_python_contract(self):
        self.assertEqual(HEADER.read_text(), native.header_text(),
                         "stale header: run python3 scripts/camera_contract_header.py --write")

    def test_every_contract_value_reaches_the_header(self):
        text = native.header_text()
        for name, value in (("kMagic", '"SCCF"'), ("kVersion", "1"), ("kPrefixBytes", "16"),
                            ("kMaxHeaderBytes", "16384"), ("kMaxPayloadBytes", "33554432"), ("kFourcc", '"BGR3"'),
                            ("kMaxSide", "4096"), ("kMaxFrames", "600"), ("kMaxSeconds", "60"), ("kMaxFps", "10"),
                            ("kMinFps", "1"), ("kCameraCount", "2"), ("kSourceIdLeft", '"arcturus_camera_left"')):
            with self.subTest(name=name):
                self.assertIn(f" {name}[] = {value};" if value.startswith('"') else f" {name} = {value};", text)
        for value in (*contract.END_REASONS, *contract.PIXEL_FORMATS, *contract.ENCODINGS, *contract.TRANSFERS,
                      *contract.PRIMARIES, *contract.BACKENDS, *contract.PIXEL_PATHS, *contract.DERIVATIONS.values()):
            with self.subTest(value=value):
                self.assertIn(f'"{value}";', text)
        for kind in contract.KINDS:
            self.assertIn(f'"{",".join(contract.field_names(kind))}";', text)
        self.assertIn('"' + ",".join(contract.STREAM_RULES) + '";', text)
        self.assertIn(f'kContractDigest[] = "{native.contract_digest()}";', text)
        self.assertIn("namespace sightcircuit_camera_export {", text)

    def test_digest_follows_every_value(self):
        digest = native.contract_digest()
        original = contract.MAX_HEADER_BYTES
        try:
            contract.MAX_HEADER_BYTES = original + 1
            self.assertNotEqual(native.contract_digest(), digest)
        finally:
            contract.MAX_HEADER_BYTES = original
        self.assertEqual(native.contract_digest(), digest)
        self.assertNotEqual(digest, hud_native.contract_digest(), "the two contracts must not share a digest")

    def test_check_mode_fails_on_a_stale_header_and_write_restores_it(self):
        from scripts import camera_contract_header as command
        with tempfile.TemporaryDirectory() as temporary:
            header = Path(temporary) / "camera_export_protocol.hpp"
            header.write_text(HEADER.read_text())
            self.assertEqual(command.main(["--check", "--header", str(header)]), 0)
            header.write_text(header.read_text().replace("kMaxFrames = 600", "kMaxFrames = 601"))
            self.assertEqual(command.main(["--check", "--header", str(header)]), 1)
            self.assertEqual(command.main(["--write", "--header", str(header)]), 0)
            self.assertEqual(header.read_text(), native.header_text())
            self.assertEqual(command.main(["--check", "--header", str(header)]), 0)
            self.assertEqual(command.main(["--check", "--header", str(Path(temporary) / "missing.hpp")]), 1)
            self.assertEqual(sorted(path.name for path in Path(temporary).iterdir()), ["camera_export_protocol.hpp"])

    def test_unrenderable_values_fail_closed(self):
        from sightcircuit import native_header
        for ctype, value in (("char[]", 'quote"d'), ("char[]", "back\\slash"), ("std::uint32_t", -1),
                             ("std::uint32_t", 2**32), ("float", float("nan")), ("int", 2**31), ("bool", 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                native_header.literal(ctype, value)
        with self.assertRaises(ValueError):
            native_header.entries_of((("a", [("kX", "int", 1), ("kX", "int", 2)]),))

    def test_hud_header_still_renders_through_the_shared_renderer(self):
        self.assertEqual((ROOT / "tools/hud_contract.hpp").read_text(), hud_native.header_text())

    def test_header_is_valid_c_plus_plus_shape(self):
        text = HEADER.read_text()
        self.assertTrue(text.startswith("// Generated by scripts/camera_contract_header.py"))
        self.assertIn("#pragma once", text)
        declarations = [line for line in text.splitlines() if line.startswith("constexpr ")]
        self.assertTrue(all(line.endswith(";") for line in declarations))
        self.assertEqual(len(declarations), len({line.split("=")[0] for line in declarations}))
        json.dumps(declarations)  # ASCII, printable.


if __name__ == "__main__":
    unittest.main()
