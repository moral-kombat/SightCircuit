"""The camera helper's ``--export-frames`` mode on the fake build: camera export protocol version 1.

The fake backend serves memfd-backed linear NV12 and P010 frame sets with
synthetic content (``tools/camera_backend_fake.cpp``); this module renders the
same content independently in Python, with the fixed-point conversion that
``tools/camera_ycbcr.hpp`` defines, and compares every delivered pixel. It also
pins the protocol constants, the packet framing byte for byte, pacing and the
dropped counts, every end reason the fake can provoke, the refusals, and that
the mode writes no file. The GPU path runs when a software Vulkan driver
(lavapipe) is installed and is skipped otherwise; on the runner CI installs it.
No test touches a camera, the SDK headers or the Client Library.
"""
import glob
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_camera_helper.sh"
PROTOCOL_HEADER = ROOT / "tools/camera_export_protocol.hpp"
MAGIC = b"SCCF"
PREFIX = struct.Struct("<4sIII")
END_REASONS = {"max_seconds", "max_frames", "stop_signal", "headset_removed", "standby", "sdk_error",
               "pixel_path_error", "watchdog", "consumer_stalled"}
# The exact header fields of each kind, as the generated contract header lists them (kReadyFields and so on).
READY_KEYS = ("kind", "protocol", "backend", "pixel_path", "build", "delivered_format", "cameras", "limits", "confirmation")
FRAME_KEYS = ("kind", "sequence", "frame_set_sequence", "frame_id", "camera_index", "exposure_ns", "monotonic_ns",
              "realtime_ns", "width", "height", "fourcc", "stride_bytes", "buffer_bytes", "derivation",
              "acquisition_format", "dropped_since_last")
END_KEYS = ("kind", "reason", "counts", "elapsed_ns", "digests", "confirmation")
ACQUISITION_KEYS = ("drm_fourcc", "modifier", "plane_count", "planes", "pixel_format", "encoding", "transfer", "primaries")
COUNT_KEYS = ("exported", "dropped", "acquire_failures", "release_failures")
# The run facts the helper puts under ready.build beyond --describe's (scalars, as the contract allows).
BUILD_KEYS = {"build_commit", "build_dirty", "sdk_release", "compiler", "contract_digest", "pixel_path_facts",
              "camera_index_mapping", "frame_timeout_ms", "presence_gap_ns", "hello_camera_count", "monotonic_ns",
              "realtime_ns", "controls_digest_start"}
# The fake's export scenarios: (pixel format, encoding, width, height).
SHAPES = {"export_nv12": ("nv12", "bt709", 62, 32), "export_nv12_tiny": ("nv12", "bt709", 8, 4),
          "export_nv12_bt601": ("nv12", "bt601", 62, 32), "export_p010": ("p010", "bt2020", 62, 32)}
KR_KB = {"bt601": (0.299, 0.114), "bt709": (0.2126, 0.0722), "bt2020": (0.2627, 0.0593)}


# ---- the reference rendering: the fake's content and the shared conversion --------------

def q16(encoding):
    kr, kb = KR_KB[encoding]
    kg = 1 - kr - kb
    return [round(v * 65536) for v in (2 * (1 - kr), -2 * (1 - kb) * kb / kg, -2 * (1 - kr) * kr / kg, 2 * (1 - kb))]


def channel(accumulator, sample_max):
    accumulator += 32768
    if accumulator < 0:
        return 0
    value = min(accumulator >> 16, sample_max)
    return value if sample_max == 255 else (value * 255 + 511) // 1023


def to_bgr(y, cb, cr, sample_max, encoding):
    cr_r, cb_g, cr_g, cb_b = q16(encoding)
    half = (sample_max + 1) // 2
    d_cb, d_cr, luma = cb - half, cr - half, y << 16
    return (channel(luma + cb_b * d_cb, sample_max), channel(luma + cb_g * d_cb + cr_g * d_cr, sample_max),
            channel(luma + cr_r * d_cr, sample_max))


def synthetic(x, y, frame, camera, p010):
    """The fake's samples at (x, y) for the delivered frame set ``frame`` (from 1) and ``camera``."""
    if y < 2 and x < 2:
        y8 = 255
    elif y < 2 and x < 4:
        y8 = 0
    else:
        y8 = (x * 4 + y * 2 + frame * 7 + camera * 64) & 0xFF
    cx, cy = x // 2, y // 2
    exact_chroma = cy == 0 and cx < 2
    cb8 = 128 if exact_chroma else (cx * 8 + frame * 3 + 96) & 0xFF
    cr8 = 128 if exact_chroma else (cy * 8 + frame * 5 + camera * 32 + 160) & 0xFF
    if not p010:
        return y8, cb8, cr8
    exact_luma = y < 2 and x < 4
    y10 = (1023 if y8 else 0) if exact_luma else y8 * 4 + ((x + y) & 3)
    return y10, 512 if exact_chroma else cb8 * 4 + 1, 512 if exact_chroma else cr8 * 4 + 2


def expected_rows(scenario, frame, camera):
    pixel_format, encoding, width, height = SHAPES[scenario]
    p010 = pixel_format == "p010"
    sample_max = 1023 if p010 else 255
    stride = ((width + 3) // 4) * 12
    rows = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            rows[y * stride + x * 3: y * stride + x * 3 + 3] = bytes(to_bgr(*synthetic(x, y, frame, camera, p010),
                                                                          sample_max, encoding))
    return bytes(rows), stride


def parse_packets(data):
    """Every packet of a stream, as (header, payload); fails on any trailing or torn bytes."""
    packets = []
    offset = 0
    while offset < len(data):
        magic, version, header_bytes, payload_bytes = PREFIX.unpack_from(data, offset)
        assert magic == MAGIC, (magic, offset)
        assert version == 1, version
        assert header_bytes <= 16384 and payload_bytes <= 33554432
        offset += PREFIX.size
        header = json.loads(data[offset:offset + header_bytes].decode("utf-8"))
        offset += header_bytes
        payload = data[offset:offset + payload_bytes]
        assert len(payload) == payload_bytes, "torn payload"
        offset += payload_bytes
        packets.append((header, payload))
    assert offset == len(data)
    return packets


def software_vulkan_present():
    """A software ICD (lavapipe) that always yields a device, or an explicit driver override that exists.

    Pointing ``VK_ICD_FILENAMES`` and ``VK_DRIVER_FILES`` at a missing file runs this module as a machine
    without any Vulkan driver would: the GPU tests skip and the driverless cases are exercised.
    """
    overrides = [os.environ.get(name) for name in ("VK_ICD_FILENAMES", "VK_DRIVER_FILES") if os.environ.get(name)]
    if overrides:
        return any(Path(path).is_file() for value in overrides for path in value.split(os.pathsep))
    for pattern in ("/usr/share/vulkan/icd.d/*.json", "/etc/vulkan/icd.d/*.json", "/usr/local/share/vulkan/icd.d/*.json"):
        for path in glob.glob(pattern):
            try:
                if "lvp" in json.loads(Path(path).read_text()).get("ICD", {}).get("library_path", ""):
                    return True
            except (OSError, ValueError):
                continue
    return False


class ProtocolConstantsTests(unittest.TestCase):
    def test_the_protocol_header_pins_camera_export_v1(self):
        """The generated header (sightcircuit.camera.contract, S7) carries the values this module tests against."""
        text = PROTOCOL_HEADER.read_text()
        strings = dict(re.findall(r"constexpr char (\w+)\[\] = \"([^\"]*)\";", text))
        numbers = {name: int(value) for name, value in re.findall(r"constexpr std::uint32_t (\w+) = (\d+);", text)}
        self.assertEqual(strings["kMagic"], "SCCF")
        self.assertEqual(strings["kProtocol"], "camera-export/1")
        self.assertEqual((numbers["kVersion"], numbers["kPrefixBytes"], numbers["kMaxHeaderBytes"], numbers["kMaxPayloadBytes"]),
                         (1, 16, 16384, 33554432))
        self.assertEqual((numbers["kPrefixMagicOffset"], numbers["kPrefixVersionOffset"], numbers["kPrefixHeaderBytesOffset"],
                          numbers["kPrefixPayloadBytesOffset"]), (0, 4, 8, 12))
        self.assertEqual((strings["kKindReady"], strings["kKindFrame"], strings["kKindEnd"]), ("ready", "frame", "end"))
        self.assertEqual(tuple(strings["kReadyFields"].split(",")), READY_KEYS)
        self.assertEqual(tuple(strings["kFrameFields"].split(",")), FRAME_KEYS)
        self.assertEqual(tuple(strings["kEndFields"].split(",")), END_KEYS)
        self.assertEqual(tuple(strings["kAcquisitionFormatFields"].split(",")), ACQUISITION_KEYS)
        self.assertEqual(tuple(strings["kCountFields"].split(",")), COUNT_KEYS)
        self.assertEqual(strings["kPlaneFields"], "offset,stride,size")
        self.assertEqual(strings["kLimitFields"], "fps,max_seconds,max_frames")
        self.assertEqual(strings["kFourcc"], "BGR3")
        self.assertEqual((strings["kPixelPathCpuLinear"], strings["kPixelPathGpuConvert"]), ("cpu_linear", "gpu_convert"))
        self.assertEqual((strings["kDerivationCpuConverted"], strings["kDerivationGpuConverted"]), ("cpu_converted", "gpu_converted"))
        self.assertEqual({strings[f"kEndReason{name}"] for name in ("MaxSeconds", "MaxFrames", "StopSignal", "HeadsetRemoved",
                                                                     "Standby", "SdkError", "PixelPathError", "Watchdog",
                                                                     "ConsumerStalled")},
                         END_REASONS)
        self.assertEqual(strings["kPixelPathUndecided"], "undecided")
        self.assertEqual((numbers["kMinFps"], numbers["kMaxFps"], numbers["kMinSeconds"], numbers["kMaxSeconds"],
                          numbers["kMinFrames"], numbers["kMaxFrames"]), (1, 10, 1, 60, 1, 600))
        self.assertEqual((numbers["kMaxSide"], numbers["kMaxPlanes"], numbers["kCameraCount"]), (4096, 4, 2))
        self.assertRegex(strings["kContractDigest"], r"^[0-9a-f]{64}$")
        # Constants only: no function, no class, no include beyond <cstdint>.
        body = text.split("namespace sightcircuit_camera_export {", 1)[1]
        self.assertFalse(re.search(r"\b(class|struct|inline|return)\b", body))
        self.assertEqual(re.findall(r"#include <(\w+)>", text), ["cstdint"])

    def test_the_reference_coefficients_match_the_header(self):
        header = (ROOT / "tools/camera_ycbcr.hpp").read_text()
        for encoding in KR_KB:
            cr_r, cb_g, cr_g, cb_b = q16(encoding)
            self.assertIn(f"case YcbcrEncoding::{encoding}: c.cr_r = {cr_r}; c.cb_g = {cb_g}; c.cr_g = {cr_g}; c.cb_b = {cb_b};",
                          header)


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required to build the fake camera helper")
class CameraExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        result = subprocess.run(["bash", str(SCRIPT), "--fake", "--out", cls.build.name], cwd=ROOT,
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            cls.build.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.binary = Path(cls.build.name) / "sightcircuit-camera-helper-fake"

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.cwd = tempfile.TemporaryDirectory()
        self.tmp = tempfile.TemporaryDirectory()
        for directory in (self.runtime, self.cwd, self.tmp):
            self.addCleanup(directory.cleanup)
        self.env = {key: value for key, value in os.environ.items() if key not in ("XDG_RUNTIME_DIR", "TMPDIR")}
        self.env["XDG_RUNTIME_DIR"] = self.runtime.name
        self.env["TMPDIR"] = self.tmp.name

    # -- running the helper ----------------------------------------------------------

    def start(self, args, scenario=None, tty=True, env=None, stdout=subprocess.PIPE):
        command = [str(self.binary), *map(str, args)]
        if scenario:
            command += ["--fake-scenario", scenario]
        if tty:
            master, slave = pty.openpty()
            process = subprocess.Popen(command, stdin=slave, stdout=stdout, stderr=subprocess.PIPE, env=env or self.env,
                                       start_new_session=True, cwd=self.cwd.name)
            os.close(slave)
            process.pty_master = master
        else:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE,
                                       env=env or self.env, start_new_session=True, cwd=self.cwd.name)
            process.pty_master = None
        self.addCleanup(self.reap, process)
        return process

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if process.pty_master is not None:
            try:
                os.close(process.pty_master)
            except OSError:
                pass
            process.pty_master = None

    def finish(self, process, timeout=30):
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("the helper did not exit")
        lines = [line for line in err.decode().splitlines() if line.strip()]
        records = []
        for line in lines:
            self.assertTrue(line.startswith("{"), f"stderr carries JSON lines only; got {line[:120]!r}")
            self.assertLessEqual(len(line), 8192)
            record = json.loads(line)
            self.assertEqual(record["report_kind"], "sightcircuit_camera_helper")
            self.assertEqual(record["backend"], "fake")
            records.append(record)
        return process.returncode, out, records

    def export(self, scenario, *extra, max_seconds=8, max_frames=4, fps=10, answer="yes\n", timeout=30):
        args = ["--export-frames", "--max-seconds", max_seconds, "--max-frames", max_frames, "--fps", fps, *extra]
        process = self.start(args, scenario)
        if answer is not None:
            os.write(process.pty_master, answer.encode())
        code, out, records = self.finish(process, timeout)
        packets = parse_packets(out) if out else []
        return code, packets, records

    def export_parent_confirmed(self, scenario, *extra, max_seconds=8, max_frames=4, fps=10, timeout=30):
        """As the Python supervisor runs it: stdin is /dev/null and the parent gives the confirmation."""
        args = ["--export-frames", "--max-seconds", max_seconds, "--max-frames", max_frames, "--fps", fps,
                "--parent-confirmed", *extra]
        process = self.start(args, scenario, tty=False)
        code, out, records = self.finish(process, timeout)
        return code, parse_packets(out) if out else [], records

    @staticmethod
    def final(records):
        finals = [record for record in records if record["event"] == "final"]
        assert len(finals) == 1, finals
        return finals[0]

    # -- schema ------------------------------------------------------------------------

    def check_stream(self, packets, scenario=None, camera_indices=(0,), pixel_path="cpu_linear", undecided=False,
                     controls_read=True):
        """One ready first, one end last, frames in between, each with exactly the contract's fields in its order.

        The ready names the pixel path; ``undecided`` is the ready of a run that ended under ``auto`` before its
        first frame set chose the path, and no frame follows it. ``controls_read`` says whether the start control
        state had been read when the ready went out (the handshake's camera count is known once the connect succeeded).
        """
        self.assertGreaterEqual(len(packets), 2, packets)
        ready, end = packets[0][0], packets[-1][0]
        self.assertEqual(ready["kind"], "ready")
        self.assertEqual(end["kind"], "end")
        self.assertEqual([header["kind"] for header, _ in packets[1:-1]], ["frame"] * (len(packets) - 2))
        self.assertEqual(tuple(ready), READY_KEYS)
        self.assertEqual(ready["protocol"], 1)
        self.assertEqual(ready["backend"], "fake")
        self.assertEqual(ready["pixel_path"], "undecided" if undecided else pixel_path)
        if undecided:
            self.assertEqual(len(packets), 2, "a frame never follows an undecided ready")
        self.assertEqual(ready["delivered_format"], {"fourcc": "BGR3"})
        self.assertEqual(ready["cameras"], list(camera_indices))
        self.assertEqual(tuple(ready["limits"]), ("fps", "max_seconds", "max_frames"))
        self.assertIn(ready["confirmation"], ("terminal", "parent"))
        self.assertEqual(end["confirmation"], ready["confirmation"])
        build = ready["build"]
        self.assertEqual(set(build), BUILD_KEYS | ({"build_commit_reason"} if build["build_commit"] is None else set()))
        self.assertIsNone(build["sdk_release"])
        if build["pixel_path_facts"]:  # empty when the named path did not open
            self.assertEqual(build["pixel_path_facts"]["path"], "cpu_linear" if undecided else pixel_path)
        if controls_read:
            self.assertEqual(build["hello_camera_count"], 2)
            self.assertRegex(build["controls_digest_start"], r"^[0-9a-f]{64}$")
            self.assertEqual(end["digests"]["controls_start"], build["controls_digest_start"])
        else:
            self.assertIn(build["hello_camera_count"], (None, 2))  # null when the run ended before the connect
            self.assertIsNone(build["controls_digest_start"])
        self.assertRegex(build["contract_digest"], r"^[0-9a-f]{64}$")
        for value in build.values():  # scalars, or objects of scalars: no arrays anywhere
            for scalar in (value.values() if isinstance(value, dict) else [value]):
                self.assertIsInstance(scalar, (str, int, bool, type(None)), build)
        self.assertEqual(packets[0][1], b"")
        self.assertEqual(packets[-1][1], b"")
        self.assertEqual(tuple(end), END_KEYS)
        self.assertIn(end["reason"], END_REASONS)
        self.assertEqual(tuple(end["counts"]), COUNT_KEYS)
        self.assertEqual(end["counts"]["exported"], len(packets) - 2)
        self.assertLessEqual(set(end["digests"]), {"controls_start", "controls_end", "offline_cal"})
        for digest in end["digests"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        derivation = "gpu_converted" if pixel_path == "gpu_convert" else "cpu_converted"
        sequence = 0
        last_set = 0
        for header, payload in packets[1:-1]:
            sequence += 1
            self.assertEqual(tuple(header), FRAME_KEYS)
            self.assertEqual(header["sequence"], sequence)
            # One frame set per exported set, from 1, the same on both cameras of a set.
            self.assertIn(header["frame_set_sequence"], (last_set, last_set + 1))
            self.assertGreaterEqual(header["frame_set_sequence"], 1)
            if header["frame_set_sequence"] == last_set:
                self.assertEqual(header["camera_index"], 1)
            last_set = header["frame_set_sequence"]
            self.assertIn(header["camera_index"], camera_indices)
            self.assertEqual(header["fourcc"], "BGR3")
            self.assertEqual(header["derivation"], derivation)
            self.assertGreaterEqual(header["stride_bytes"], header["width"] * 3)
            self.assertEqual(header["buffer_bytes"], header["stride_bytes"] * header["height"])
            self.assertEqual(len(payload), header["buffer_bytes"])
            self.assertEqual(tuple(header["acquisition_format"]), ACQUISITION_KEYS)
            self.assertEqual(header["acquisition_format"]["plane_count"], len(header["acquisition_format"]["planes"]))
            self.assertRegex(header["acquisition_format"]["modifier"], r"^0x[0-9a-f]{16}$")
            self.assertGreater(header["monotonic_ns"], header["exposure_ns"])
            self.assertGreater(header["realtime_ns"], 10 ** 17)
            if scenario:
                pixel_format, encoding, width, height = SHAPES[scenario]
                self.assertEqual((header["width"], header["height"]), (width, height))
                self.assertEqual(header["acquisition_format"]["pixel_format"], pixel_format)
                self.assertEqual(header["acquisition_format"]["encoding"], encoding)
                self.assertEqual(header["acquisition_format"]["drm_fourcc"], "0x30313050" if pixel_format == "p010" else "0x3231564e")
                self.assertEqual(header["acquisition_format"]["modifier"], "0x0000000000000000")
        return ready, end

    def check_pixels(self, scenario, header, payload):
        """Every pixel against the reference rendering; the fake's frame_id is pass * 10 + camera."""
        frame = header["frame_id"] // 10
        expected, stride = expected_rows(scenario, frame, header["camera_index"])
        self.assertEqual(header["stride_bytes"], stride)
        self.assertEqual(payload, expected, f"{scenario}: frame {frame} camera {header['camera_index']} differs")
        # The exact blocks: white and black at the top-left, whatever the frame or encoding.
        self.assertEqual(payload[0:6], b"\xff\xff\xff\xff\xff\xff")
        self.assertEqual(payload[stride:stride + 6], b"\xff\xff\xff\xff\xff\xff")
        self.assertEqual(payload[6:12], b"\x00" * 6)
        self.assertEqual(payload[stride + 6:stride + 12], b"\x00" * 6)

    # -- the CPU path ------------------------------------------------------------------------

    def test_cpu_linear_converts_nv12_and_p010_exactly_in_every_encoding(self):
        for scenario in ("export_nv12", "export_nv12_bt601", "export_p010"):
            with self.subTest(scenario=scenario):
                code, packets, records = self.export(scenario, "--cameras", "0,1", "--pixel-path", "cpu_linear")
                self.assertEqual(code, 0)
                ready, end = self.check_stream(packets, scenario, camera_indices=(0, 1))
                self.assertEqual(end["reason"], "max_frames")
                self.assertEqual(end["counts"]["exported"], 4)
                self.assertEqual(end["digests"]["controls_end"], end["digests"]["controls_start"])
                frames = packets[1:-1]
                self.assertEqual([header["camera_index"] for header, _ in frames], [0, 1, 0, 1])
                self.assertEqual([header["frame_set_sequence"] for header, _ in frames], [1, 1, 2, 2])
                self.assertEqual(frames[0][0]["frame_id"] // 10, frames[1][0]["frame_id"] // 10)  # one frame set
                self.assertLess(frames[1][0]["frame_id"] // 10, frames[2][0]["frame_id"] // 10)
                self.assertEqual(ready["confirmation"], "terminal")
                for header, payload in frames:
                    self.check_pixels(scenario, header, payload)
                final = self.final(records)
                self.assertEqual(final["pixel_path_notes"], {"dma_buf_sync": "not_supported_by_descriptor"})
                self.assertEqual(final["cameras"], [0, 1])
                self.assertEqual((final["reason"], final["exported"], final["pixel_path"]), ("max_frames", 4, "cpu_linear"))
                self.assertIs(final["cleanup_ok"], True)
                self.assertIs(final["reader_deactivated"], True)
                self.assertIs(final["images_saved"], False)
                self.assertIs(final["pixels_read"], True)
                self.assertEqual(final["persistence"], "stdout_pipe_to_parent_only")
                self.assertEqual(final["fake_calls"][:3], ["connect", "control_state", "set_reader:true"])
                self.assertEqual(final["fake_calls"][-3:], ["set_reader:false", "control_state", "disconnect"])
                self.assertEqual(final["fake_calls"].count("acquire"), final["fake_calls"].count("release"))

    def test_auto_picks_the_cpu_path_for_a_linear_buffer(self):
        code, packets, records = self.export("export_nv12", max_frames=1)
        self.assertEqual(code, 0)
        ready, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(ready["pixel_path"], "cpu_linear")  # named by the ready, once the first set chose it
        self.assertEqual(ready["cameras"], [0])
        self.assertEqual(ready["build"]["camera_index_mapping"], "assumed_avcam0_left")
        self.assertEqual(ready["limits"], {"fps": 10, "max_seconds": 8, "max_frames": 1})
        self.check_pixels("export_nv12", *packets[1])
        # The first frame set chose the path and was released unexported; the second was exported.
        self.assertEqual(packets[1][0]["frame_id"] // 10, 2)
        self.assertEqual(packets[1][0]["dropped_since_last"], 1)
        self.assertEqual(end["counts"]["dropped"], 1)
        final = self.final(records)
        self.assertEqual(final["pixel_path"], "cpu_linear")
        self.assertEqual(final["fake_calls"][3:5], ["acquire", "release"])

    def test_tiny_frame_framing_byte_for_byte(self):
        process = self.start(["--export-frames", "--max-seconds", 5, "--max-frames", 1, "--fps", 10, "--pixel-path",
                              "cpu_linear"], "export_nv12_tiny")
        os.write(process.pty_master, b"yes\n")
        code, out, _ = self.finish(process)
        self.assertEqual(code, 0)
        # Walk the raw bytes: three packets, each prefix exactly as the header says.
        offset = 0
        seen = []
        while offset < len(out):
            self.assertEqual(out[offset:offset + 4], MAGIC)
            version, header_bytes, payload_bytes = struct.unpack_from("<III", out, offset + 4)
            self.assertEqual(version, 1)
            header_text = out[offset + 16:offset + 16 + header_bytes]
            header = json.loads(header_text.decode("utf-8"))
            self.assertEqual(header_text, header_text.strip())  # no newline or padding around the JSON
            payload = out[offset + 16 + header_bytes:offset + 16 + header_bytes + payload_bytes]
            seen.append((header, payload))
            offset += 16 + header_bytes + payload_bytes
        self.assertEqual(offset, len(out))
        self.assertEqual([header["kind"] for header, _ in seen], ["ready", "frame", "end"])
        header, payload = seen[1]
        expected, stride = expected_rows("export_nv12_tiny", 1, 0)
        self.assertEqual((header["width"], header["height"], header["stride_bytes"], header["buffer_bytes"]), (8, 4, 24, 96))
        self.assertEqual(stride, 24)
        self.assertEqual(payload, expected)
        self.assertEqual(len(payload), 96)
        self.assertEqual(header["frame_id"], 10)
        self.assertEqual(header["sequence"], 1)
        self.assertEqual(header["dropped_since_last"], 0)
        self.assertEqual(header["acquisition_format"]["planes"], [{"offset": 0, "stride": 8, "size": 32},
                                                                  {"offset": 32, "stride": 8, "size": 16}])
        # Rebuild the frame packet from its parts: the prefix is exactly these sixteen bytes.
        prefix = out[out.index(b"SCCF", 16):out.index(b"SCCF", 16) + 16]
        self.assertEqual(prefix, MAGIC + struct.pack("<III", 1, len(json.dumps(header, separators=(",", ":"))), 96))
        self.assertEqual(seen[0][1], b"")
        self.assertEqual(seen[2][1], b"")

    # -- pacing and bounds ------------------------------------------------------------------

    def test_pacing_drops_frame_sets_and_counts_them(self):
        code, packets, records = self.export("export_nv12", "--pixel-path", "cpu_linear", fps=5, max_frames=3)
        self.assertEqual(code, 0)
        ready, end = self.check_stream(packets, "export_nv12")
        final = self.final(records)
        frames = [header for header, _ in packets[1:-1]]
        self.assertEqual(frames[0]["dropped_since_last"], 0)
        # The fake runs at about 60 Hz; at 5 frames a second about eleven sets fall between exports.
        for header in frames[1:]:
            self.assertGreaterEqual(header["dropped_since_last"], 5, header)
            self.assertLessEqual(header["dropped_since_last"], 20, header)
        self.assertEqual(end["counts"]["dropped"], sum(header["dropped_since_last"] for header in frames))
        self.assertEqual(final["frame_sets"], end["counts"]["dropped"] + 3)
        self.assertEqual(final["passes"], final["frame_sets"])
        self.assertEqual(final["dropped"], end["counts"]["dropped"])
        self.assertGreaterEqual(end["elapsed_ns"], 350_000_000)
        self.assertLess(end["elapsed_ns"], 1_500_000_000)
        self.assertGreater(frames[1]["monotonic_ns"] - frames[0]["monotonic_ns"], 150_000_000)
        self.assertLess(frames[1]["frame_id"] // 10 - frames[0]["frame_id"] // 10 - 1, 21)

    def test_max_frames_and_max_seconds_end_the_run(self):
        code, packets, _ = self.export("export_nv12", "--cameras", "0,1", max_frames=3)
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12", camera_indices=(0, 1))
        self.assertEqual((end["reason"], end["counts"]["exported"]), ("max_frames", 3))
        self.assertEqual([header["camera_index"] for header, _ in packets[1:-1]], [0, 1, 0])
        started = time.monotonic()
        code, packets, _ = self.export("export_nv12", max_seconds=1, max_frames=600, fps=1)
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["reason"], "max_seconds")
        self.assertIn(end["counts"]["exported"], (1, 2))
        self.assertGreaterEqual(end["elapsed_ns"], 1_000_000_000)
        self.assertLess(end["elapsed_ns"], 2_500_000_000)
        self.assertLess(elapsed, 6)
        self.assertGreater(end["counts"]["dropped"], 20)

    # -- the ends the agreement asks for, and the others --------------------------------------

    def test_headset_removal_and_standby_end_the_export(self):
        code, packets, records = self.export("export_removal", max_frames=50)
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["reason"], "headset_removed")
        self.assertGreaterEqual(end["counts"]["exported"], 1)
        self.assertGreater(end["counts"]["acquire_failures"], 5)
        final = self.final(records)
        self.assertRegex(final["detail"], r"^no_frame_set_for_ns:\d+$")
        self.assertGreater(int(final["detail"].split(":")[1]), 2_000_000_000)
        self.assertEqual(final["outcome"], "ok")
        self.assertEqual(final["reason"], "headset_removed")
        self.assertIs(final["cleanup_ok"], True)
        self.assertIs(final["reader_deactivated"], True)
        self.assertEqual(final["outcome_counts"]["no_frame"], end["counts"]["acquire_failures"])

        code, packets, records = self.export("export_standby", max_frames=50)
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["reason"], "standby")
        self.assertGreaterEqual(end["counts"]["exported"], 1)
        self.assertRegex(self.final(records)["detail"], r"^no_frame_set_for_ns:\d+$")
        self.assertEqual(self.final(records)["outcome_counts"]["unavailable"], end["counts"]["acquire_failures"])

    def test_stop_signals_end_cleanly_with_the_end_packet_last(self):
        cases = [(signal.SIGTERM, "cpu_linear"), (signal.SIGINT, "cpu_linear")]
        if software_vulkan_present():
            cases.append((signal.SIGTERM, "gpu_convert"))  # the driver's threads must inherit the blocked mask
        for stop, pixel_path in cases:
            with self.subTest(signal=stop.name, pixel_path=pixel_path):
                process = self.start(["--export-frames", "--max-seconds", 20, "--max-frames", 600, "--fps", 10,
                                      "--pixel-path", pixel_path], "export_nv12")
                os.write(process.pty_master, b"yes\n")
                time.sleep(1.2)
                process.send_signal(stop)
                started = time.monotonic()
                code, out, records = self.finish(process)
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(code, 130)
                packets = parse_packets(out)
                _, end = self.check_stream(packets, "export_nv12", pixel_path=pixel_path)
                self.assertEqual(end["reason"], "stop_signal")
                self.assertGreaterEqual(end["counts"]["exported"], 1)
                final = self.final(records)
                self.assertEqual((final["reason"], final["stop_signal"]), ("stop_signal", stop.name))
                self.assertIs(final["cleanup_ok"], True)
                self.assertEqual(final["fake_calls"][-3:], ["set_reader:false", "control_state", "disconnect"])

    def test_the_watchdog_ends_a_hung_acquire_with_an_end_packet(self):
        for pixel_path, undecided in (("cpu_linear", False), ("auto", True)):
            with self.subTest(pixel_path=pixel_path):
                started = time.monotonic()
                code, packets, records = self.export("export_hang_in_acquire", "--pixel-path", pixel_path, "--timeout-ms", 1,
                                                     max_seconds=5, max_frames=5, timeout=20)
                self.assertEqual(code, 124)
                self.assertLess(time.monotonic() - started, 8)
                # The watchdog's pre-built message carries the ready too when none went out yet (auto).
                self.check_stream(packets, undecided=undecided)
                end = packets[-1][0]
                self.assertEqual(tuple(end), END_KEYS)
                self.assertEqual(end["reason"], "watchdog")
                self.assertEqual(end["counts"]["exported"], 0)
                self.assertEqual(end["counts"]["acquire_failures"], 0)
                self.assertIn("controls_start", end["digests"])
                self.assertFalse([record for record in records if record["event"] == "final"])

    def test_transport_loss_and_bad_metadata_end_with_sdk_error(self):
        code, packets, records = self.export("export_transport_lost", "--pixel-path", "cpu_linear", max_frames=50)
        self.assertEqual(code, 1)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["reason"], "sdk_error")
        self.assertEqual(end["counts"]["exported"], 1)
        self.assertNotIn("controls_end", end["digests"])  # the session was lost before the end read
        final = self.final(records)
        self.assertEqual((final["detail"], final["outcome"], final["exit_code"]), ("transport_lost", "transport_lost", 1))
        self.assertIs(final["cleanup_ok"], False)
        self.assertEqual(final["reader_deactivated"], "session_closed")
        self.assertEqual(final["fake_calls"][-1], "disconnect")

    def test_pixel_path_errors_release_the_frame_set_and_end(self):
        cases = (("export_ubwc", ["--pixel-path", "cpu_linear"], "camera_0:modifier_not_linear"),
                 ("export_bad_fd", ["--pixel-path", "cpu_linear"], "camera_0:buffer_descriptor_missing"))
        for scenario, extra, detail in cases:
            with self.subTest(scenario=scenario):
                code, packets, records = self.export(scenario, *extra)
                self.assertEqual(code, 1)
                _, end = self.check_stream(packets)
                self.assertEqual(end["reason"], "pixel_path_error")
                self.assertEqual(end["counts"]["exported"], 0)
                final = self.final(records)
                self.assertEqual((final["reason"], final["detail"], final["exit_code"]), ("pixel_path_error", detail, 1))
                self.assertIs(final["cleanup_ok"], True)
                calls = final["fake_calls"]
                self.assertEqual(calls[calls.index("acquire") + 1], "release")
                self.assertEqual(calls[-3:], ["set_reader:false", "control_state", "disconnect"])
        # A tiled buffer under auto needs the GPU path, and this runner's driver (if any) cannot import
        # the Adreno modifier: either the path does not open (the ready is then "undecided") or the import
        # is refused (the ready names gpu_convert), never a wrong frame. The stream is complete either way.
        code, packets, records = self.export("export_ubwc")
        self.assertEqual(code, 1)
        final = self.final(records)
        self.assertEqual(final["reason"], "pixel_path_error")
        self.assertTrue(final["detail"].startswith(("gpu_convert_open_failed:", "camera_0:modifier_import_unsupported_by_device:",
                                                    "camera_0:modifier_unknown_to_driver:")), final["detail"])
        self.assertEqual(final["exported"], 0)
        opened = not final["detail"].startswith("gpu_convert_open_failed:")
        _, end = self.check_stream(packets, pixel_path="gpu_convert", undecided=not opened)
        self.assertEqual(end["reason"], "pixel_path_error")
        # Without any Vulkan driver (the loader is pointed at a driver file that does not exist) the GPU path
        # does not open: the path was never chosen, so the ready says "undecided" and the end follows at once.
        process = self.start(["--export-frames", "--max-seconds", 8, "--max-frames", 4, "--fps", 10], "export_ubwc",
                             env=dict(self.env, VK_ICD_FILENAMES="/nonexistent/icd.json", VK_DRIVER_FILES="/nonexistent/icd.json"))
        os.write(process.pty_master, b"yes\n")
        code, out, records = self.finish(process)
        self.assertEqual(code, 1)
        _, end = self.check_stream(parse_packets(out), undecided=True)
        self.assertEqual(end["reason"], "pixel_path_error")
        self.assertTrue(self.final(records)["detail"].startswith("gpu_convert_open_failed:"), self.final(records)["detail"])
        self.assertEqual(self.final(records)["fake_calls"][3:5], ["acquire", "release"])
        # Named, the path is known whether or not it opens: the ready says gpu_convert, no frame, the end at once,
        # before any connect (the connect's facts are null).
        process = self.start(["--export-frames", "--max-seconds", 8, "--max-frames", 4, "--fps", 10, "--pixel-path",
                              "gpu_convert"], "export_nv12",
                             env=dict(self.env, VK_ICD_FILENAMES="/nonexistent/icd.json", VK_DRIVER_FILES="/nonexistent/icd.json"))
        os.write(process.pty_master, b"yes\n")
        code, out, records = self.finish(process)
        self.assertEqual(code, 1)
        packets = parse_packets(out)
        _, end = self.check_stream(packets, pixel_path="gpu_convert", controls_read=False)
        self.assertEqual(end["reason"], "pixel_path_error")
        self.assertEqual(packets[0][0]["build"]["pixel_path_facts"], {})
        final = self.final(records)
        self.assertTrue(final["detail"].startswith("pixel_path_open_failed:"), final["detail"])
        self.assertEqual(final["fake_calls"], [])
        # A plane table that claims fewer bytes than its rows need is refused before any read, on both paths.
        paths = ["cpu_linear"] + (["gpu_convert"] if software_vulkan_present() else [])
        for pixel_path in paths:
            with self.subTest(scenario="export_bad_plane_size", pixel_path=pixel_path):
                code, packets, records = self.export("export_bad_plane_size", "--pixel-path", pixel_path)
                self.assertEqual(code, 1)
                _, end = self.check_stream(packets, pixel_path=pixel_path)
                self.assertEqual(end["reason"], "pixel_path_error")
                self.assertEqual(self.final(records)["detail"], "camera_0:plane_size_too_small")

    def test_camera_selection(self):
        code, packets, _ = self.export("export_nv12", "--cameras", "1", "--pixel-path", "cpu_linear", max_frames=2)
        self.assertEqual(code, 0)
        ready, end = self.check_stream(packets, "export_nv12", camera_indices=(1,))
        for header, payload in packets[1:-1]:
            self.assertEqual(header["camera_index"], 1)
            self.assertEqual(header["frame_id"] % 10, 1)
            self.check_pixels("export_nv12", header, payload)
        code, packets, _ = self.export("export_nv12", "--cameras", "1,0", "--pixel-path", "cpu_linear", max_frames=2)
        self.assertEqual(code, 0)
        ready, _ = self.check_stream(packets, "export_nv12", camera_indices=(0, 1))
        self.assertEqual([header["camera_index"] for header, _ in packets[1:-1]], [0, 1])
        code, packets, records = self.export("export_one_camera", "--cameras", "1", "--pixel-path", "cpu_linear")
        self.assertEqual(code, 1)
        _, end = self.check_stream(packets, camera_indices=(1,))
        self.assertEqual(end["reason"], "sdk_error")
        self.assertTrue(self.final(records)["detail"].startswith("camera_missing:--cameras 1"), self.final(records)["detail"])
        self.assertEqual(end["counts"]["exported"], 0)
        code, packets, records = self.export("export_one_camera", "--cameras", "0,1")  # auto: the same end, after the first set
        self.assertEqual(code, 1)
        _, end = self.check_stream(packets, camera_indices=(0, 1))
        self.assertEqual(end["reason"], "sdk_error")

    def test_a_run_that_ends_before_its_path_is_chosen_still_writes_ready_then_end(self):
        """Frames come only after a ready that names the path; an early end gets a ready (undecided under auto) and an end."""
        # The service never serves a frame set: standby after the presence gap, no frame, exit 0.
        for pixel_path, undecided in (("auto", True), ("cpu_linear", False)):
            with self.subTest(case="never_ready", pixel_path=pixel_path):
                code, packets, records = self.export_parent_confirmed("export_never_ready", "--pixel-path", pixel_path,
                                                                       max_frames=50)
                self.assertEqual(code, 0)
                _, end = self.check_stream(packets, pixel_path="cpu_linear", undecided=undecided)
                self.assertEqual((end["reason"], end["counts"]["exported"]), ("standby", 0))
                self.assertGreater(end["counts"]["acquire_failures"], 10)
                self.assertEqual(self.final(records)["reason"], "standby")
        # The connect's control read is refused: no frame set, exit 2, and the connect's facts stay null.
        for pixel_path, undecided in (("auto", True), ("cpu_linear", False)):
            with self.subTest(case="permission_denied", pixel_path=pixel_path):
                code, packets, records = self.export_parent_confirmed("permission_denied", "--pixel-path", pixel_path)
                self.assertEqual(code, 2)
                _, end = self.check_stream(packets, pixel_path="cpu_linear", undecided=undecided, controls_read=False)
                self.assertEqual(end["reason"], "sdk_error")
                self.assertEqual(end["digests"], {"offline_cal": end["digests"]["offline_cal"]})
                final = self.final(records)
                self.assertEqual((final["outcome"], final["exit_code"]), ("permission_denied", 2))
                self.assertEqual(final["fake_calls"], ["connect", "control_state", "disconnect"])
        code, packets, _ = self.export("export_one_camera", "--pixel-path", "cpu_linear", max_frames=2)
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["counts"]["exported"], 2)

    def test_the_parent_may_give_the_confirmation_in_the_export_mode_only(self):
        code, packets, records = self.export_parent_confirmed("export_nv12", "--cameras", "0,1", "--pixel-path", "cpu_linear",
                                                               max_frames=4)
        self.assertEqual(code, 0, records[-1] if records else None)
        ready, end = self.check_stream(packets, "export_nv12", camera_indices=(0, 1))
        self.assertEqual((ready["confirmation"], end["confirmation"]), ("parent", "parent"))
        for header, payload in packets[1:-1]:
            self.check_pixels("export_nv12", header, payload)
        final = self.final(records)
        self.assertEqual((final["confirmation"], final["reason"], final["exported"]), ("parent", "max_frames", 4))
        # The notice is still recorded, as a JSON line, with the parent named as the confirming party.
        notices = [record["message"] for record in records if record["event"] == "note"]
        self.assertTrue(any("licence section 3.2" in message and "parent process" in message for message in notices), notices)
        self.assertEqual(final["fake_calls"][:3], ["connect", "control_state", "set_reader:true"])
        # Nowhere else: every other mode refuses the flag before any prompt or connect.
        for args in (["--hello", "--parent-confirmed"], ["--probe-frames", 1, "--max-seconds", 2, "--parent-confirmed"],
                     ["--describe", "--parent-confirmed"], ["--parent-confirmed"]):
            with self.subTest(args=args):
                result = subprocess.run([str(self.binary), *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True,
                                        env=self.env, timeout=20)
                self.assertEqual(result.returncode, 64)
                record = json.loads(result.stdout.decode().splitlines()[0])
                self.assertEqual(record["reason"], "invalid_arguments")
        self.assertEqual(os.listdir(self.runtime.name), ["sightcircuit-camera-helper.lock"])

    def test_a_parent_that_stops_reading_ends_the_run_within_the_bound(self):
        """The pipe is left unread: the writes stall, and the run ends as consumer_stalled with the reader off."""
        process = self.start(["--export-frames", "--max-seconds", 20, "--max-frames", 600, "--fps", 10, "--cameras", "0,1",
                              "--pixel-path", "cpu_linear", "--parent-confirmed"], "export_nv12", tty=False)
        started = time.monotonic()
        try:
            code = process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("the helper kept running against an unread pipe")
        elapsed = time.monotonic() - started
        with process.stdout, process.stderr:
            out = process.stdout.read()
            err = process.stderr.read()
        self.assertLess(elapsed, 8)  # a 64 KiB pipe fills within a second; the stall bound is two seconds
        self.assertEqual(code, 1)
        records = [json.loads(line) for line in err.decode().splitlines() if line.strip()]
        final = self.final(records)
        self.assertEqual((final["reason"], final["consumer_stalled"], final["exit_code"]), ("consumer_stalled", True, 1))
        self.assertEqual(final["detail"], "no_progress_for_ns:2000000000")
        self.assertIs(final["cleanup_ok"], True)
        self.assertIs(final["reader_deactivated"], True)
        self.assertEqual(final["fake_calls"][-3:], ["set_reader:false", "control_state", "disconnect"])
        self.assertGreaterEqual(final["exported"], 1)
        # What reached the pipe: a ready, whole frames and, unless a frame was torn, the end packet.
        offset = 0
        kinds = []
        torn = False
        while offset < len(out):
            if len(out) - offset < PREFIX.size:
                torn = True
                break
            magic, version, header_bytes, payload_bytes = PREFIX.unpack_from(out, offset)
            self.assertEqual((magic, version), (MAGIC, 1))
            if offset + PREFIX.size + header_bytes + payload_bytes > len(out):
                torn = True
                break
            kinds.append(json.loads(out[offset + PREFIX.size:offset + PREFIX.size + header_bytes])["kind"])
            offset += PREFIX.size + header_bytes + payload_bytes
        self.assertEqual(kinds[0], "ready")
        self.assertEqual(torn, final["stream_torn"])
        if torn:
            self.assertIs(final["end_packet_written"], False)
            self.assertNotIn("end", kinds)
        else:
            self.assertIs(final["end_packet_written"], True)
            self.assertEqual(kinds[-1], "end")
            packets = parse_packets(out)
            self.assertEqual(packets[-1][0]["reason"], "consumer_stalled")
            self.assertEqual(packets[-1][0]["counts"]["exported"], final["exported"])
        self.assertEqual(kinds.count("frame"), final["exported"])

    # -- refusals and bounds --------------------------------------------------------------------

    def test_refusals_write_no_packet_and_one_stderr_final(self):
        # stdout that is a file: refused before the lock and the prompt, and the file stays empty
        with open(Path(self.tmp.name) / "stdout.bin", "wb") as sink:
            process = self.start(["--export-frames", "--max-seconds", 2, "--max-frames", 1], "export_nv12", stdout=sink)
            os.write(process.pty_master, b"yes\n")
            code, _, records = self.finish(process)
        self.assertEqual(code, 5)
        self.assertEqual(self.final(records)["reason"], "stdout_not_a_pipe")
        self.assertEqual(self.final(records)["fake_calls"], [])
        self.assertEqual((Path(self.tmp.name) / "stdout.bin").stat().st_size, 0)
        self.assertEqual(list(Path(self.runtime.name).iterdir()), [])
        # stdin that is not a terminal
        process = self.start(["--export-frames", "--max-seconds", 2, "--max-frames", 1], "export_nv12", tty=False)
        code, out, records = self.finish(process)
        self.assertEqual((code, out), (5, b""))
        self.assertEqual(self.final(records)["reason"], "confirmation_required")
        self.assertEqual(self.final(records)["fake_calls"], [])
        notices = [record for record in records if record["event"] == "note"]
        self.assertTrue(any("licence section 3.2" in record["message"] for record in notices), notices)
        # a declined confirmation
        for answer in ("no\n", "y\n", "YES\n", "\n"):
            with self.subTest(answer=answer):
                code, packets, records = self.export("export_nv12", answer=answer, max_frames=1)
                self.assertEqual((code, packets), (5, []))
                self.assertEqual(self.final(records)["reason"], "confirmation_declined")
                self.assertEqual(self.final(records)["fake_calls"], [])
        # no runtime directory
        env = {key: value for key, value in self.env.items() if key != "XDG_RUNTIME_DIR"}
        process = self.start(["--export-frames", "--max-seconds", 2, "--max-frames", 1], "export_nv12", env=env)
        os.write(process.pty_master, b"yes\n")
        code, out, records = self.finish(process)
        self.assertEqual((code, out), (5, b""))
        self.assertEqual(self.final(records)["reason"], "runtime_dir_missing")

    def test_every_out_of_range_argument_is_refused_before_any_prompt(self):
        base = ["--export-frames", "--max-seconds", 2, "--max-frames", 3]
        cases = (
            ["--export-frames"], ["--export-frames", "--max-seconds", 2], ["--export-frames", "--max-frames", 3],
            ["--export-frames", "--max-seconds", 0, "--max-frames", 3], ["--export-frames", "--max-seconds", 61, "--max-frames", 3],
            ["--export-frames", "--max-seconds", 2, "--max-frames", 0], ["--export-frames", "--max-seconds", 2, "--max-frames", 601],
            base + ["--fps", 0], base + ["--fps", 11], base + ["--fps", "5.0"], base + ["--fps"],
            base + ["--cameras", "2"], base + ["--cameras"], base + ["--cameras", "left"], base + ["--cameras", "0,0"],
            base + ["--cameras", "0,1,"], base + ["--cameras", ",1"], base + ["--cameras", "0, 1"], base + ["--camera", "0"],
            base + ["--pixel-path", "magic"], base + ["--pixel-path"], base + ["--pixel-path", "cpu"],
            base + ["--interval-ms", 200], base + ["--probe-frames", 1], base + ["--hello"], base + ["--describe"],
            base + ["--parent-confirmed", "yes"], base + ["--yes"], base + ["--no-confirm"],
            base + ["--timeout-ms", 0], base + ["--timeout-ms", 10001], base + ["--fake-scenario", "no_such_scenario"],
            base + ["--unknown"],
        )
        for args in cases:
            with self.subTest(args=args):
                process = self.start(args, tty=False)
                code, out, records = self.finish(process)
                self.assertEqual((code, out), (64, b""))
                final = self.final(records)
                self.assertEqual(final["reason"], "invalid_arguments")
                self.assertIsNone(final["outcome"])
                self.assertTrue(final["detail"])
        self.assertEqual(list(Path(self.runtime.name).iterdir()), [])
        # The export-only arguments are refused on the metadata modes, which keep NDJSON on stdout.
        for args in (["--probe-frames", 1, "--max-seconds", 2, "--fps", 2], ["--hello", "--max-frames", 1],
                     ["--hello", "--cameras", "0"], ["--describe", "--pixel-path", "auto"],
                     ["--probe-frames", 1, "--max-seconds", 11]):
            with self.subTest(args=args):
                result = subprocess.run([str(self.binary), *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True,
                                        env=self.env, timeout=20)
                self.assertEqual(result.returncode, 64)
                record = json.loads(result.stdout.decode().splitlines()[0])
                self.assertEqual(record["reason"], "invalid_arguments")

    def test_the_mode_writes_no_file_anywhere(self):
        code, packets, _ = self.export("export_nv12", "--cameras", "0,1", "--pixel-path", "cpu_linear", max_frames=6)
        self.assertEqual(code, 0)
        self.assertEqual(len(packets), 8)
        self.assertEqual(os.listdir(self.cwd.name), [])
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assertEqual(os.listdir(self.runtime.name), ["sightcircuit-camera-helper.lock"])
        self.assertEqual((Path(self.runtime.name) / "sightcircuit-camera-helper.lock").stat().st_size, 0)
        # Nothing of a pixel reaches stderr: every line is JSON, and far too short for a frame.
        process = self.start(["--export-frames", "--max-seconds", 3, "--max-frames", 2, "--pixel-path", "cpu_linear"],
                             "export_nv12")
        os.write(process.pty_master, b"yes\n")
        code, out, records = self.finish(process)
        self.assertEqual(code, 0)
        self.assertEqual([record["event"] for record in records if record["event"] != "note"],
                         ["hello", "controls", "controls", "final"])
        self.assertNotIn("acquisition_format", json.dumps(records))
        # --help in the export mode is a JSON line too, and writes no packet.
        process = self.start(["--export-frames", "--help"], tty=False)
        code, out, records = self.finish(process)
        self.assertEqual((code, out), (0, b""))
        self.assertTrue(any(record["event"] == "note" and "--parent-confirmed" in record["message"] for record in records))

    def test_a_long_run_keeps_the_final_record_within_its_line_bound(self):
        """At camera rate the fake's call log grows two entries a set; the final record must still fit 8 KiB."""
        code, packets, records = self.export_parent_confirmed("export_nv12", "--pixel-path", "cpu_linear", max_seconds=3,
                                                               max_frames=600, fps=10, timeout=20)
        self.assertEqual(code, 0)
        _, end = self.check_stream(packets, "export_nv12")
        self.assertEqual(end["reason"], "max_seconds")
        final = self.final(records)  # parses, so it was not replaced by the oversized-record outcome
        self.assertEqual(final["reason"], "max_seconds")
        calls = final["fake_calls"]
        self.assertGreater(final["frame_sets"], 60)
        self.assertLess(len(calls), 60)
        omitted = [call for call in calls if call.endswith("calls omitted...")]
        self.assertEqual(len(omitted), 1, calls)
        self.assertEqual(calls[:3], ["connect", "control_state", "set_reader:true"])
        self.assertEqual(calls[-3:], ["set_reader:false", "control_state", "disconnect"])
        self.assertLessEqual(max(len(json.dumps(record, separators=(",", ":"))) for record in records), 8192)

    # -- the GPU path on a software device ---------------------------------------------------

    @unittest.skipUnless(software_vulkan_present(), "a software Vulkan driver (lavapipe) is required")
    def test_gpu_convert_imports_a_memfd_through_vulkan_and_matches_the_cpu_path(self):
        for scenario in ("export_nv12", "export_p010", "export_nv12_bt601"):
            with self.subTest(scenario=scenario):
                code, packets, records = self.export(scenario, "--cameras", "0,1", "--pixel-path", "gpu_convert", max_frames=2)
                self.assertEqual(code, 0, self.final(records) if records else None)
                ready, end = self.check_stream(packets, scenario, camera_indices=(0, 1), pixel_path="gpu_convert")
                facts = ready["build"]["pixel_path_facts"]
                self.assertIn(facts["import"], ("drm_format_modifier", "linear_copy_fallback"))
                self.assertTrue(facts["device_name"])
                self.assertIn("VK_EXT_external_memory_dma_buf", facts["device_extensions"].split(","))
                self.assertEqual(facts["shader_source_sha256"],
                                 hashlib.sha256((ROOT / "tools/camera_convert.comp").read_bytes()).hexdigest())
                for header, payload in packets[1:-1]:
                    self.assertEqual(header["derivation"], "gpu_converted")
                    self.check_pixels(scenario, header, payload)
                notes = self.final(records)["pixel_path_notes"]
                self.assertEqual((notes["frames_imported"], notes["import"]), (2, facts["import"]))
        # The first frame set of a run has the same content on either path: the bytes agree exactly.
        _, gpu, _ = self.export("export_nv12", "--pixel-path", "gpu_convert", max_frames=1)
        _, cpu, _ = self.export("export_nv12", "--pixel-path", "cpu_linear", max_frames=1)
        self.assertEqual(gpu[1][1], cpu[1][1])
        self.assertEqual(gpu[1][0]["frame_id"], cpu[1][0]["frame_id"])


if __name__ == "__main__":
    unittest.main()
