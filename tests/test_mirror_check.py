"""Mirror discovery, Linux ABI, bounded workers and frame handling without hardware."""
from contextlib import redirect_stdout
import ctypes
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sightcircuit.sources import mirror as acquisition
from sightcircuit.sources import mirror_device as device
from scripts import mirror_check as check


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = {"node": "/dev/video83", "name": "SteamVR", "sysfs_path": "/sys/devices/virtual/video4linux/video83",
             "major": 81, "minor": 83, "classification": "steamvr_virtual_mirror_candidate"}


class FakeMirror:
    closed = False

    def __init__(self, candidate):
        self.candidate = candidate
        self.capabilities = {"driver": "fixture", "card": "SteamVR"}
        self.format = device.validate_format(2, 2, "GREY", 2, 4)
        self.count = 0

    def open(self):
        type(self).closed = False

    def read_frame(self, timeout):
        self.count += 1
        return bytes([self.count % 2 * 200]) * 4

    def close(self):
        type(self).closed = True


class StaticMirror(FakeMirror):
    def read_frame(self, timeout):
        return bytes(4)


class EmptyMirror(FakeMirror):
    def read_frame(self, timeout):
        return None


class ShortMirror(FakeMirror):
    def read_frame(self, timeout):
        return b"short"[:2]


class ErrorMirror(FakeMirror):
    def read_frame(self, timeout):
        raise device.MirrorError("format_changed", "fixture format changed")


def changing_worker(candidate, seconds, outputs, progress, stop):
    acquisition.NativeMirror = FakeMirror
    real_encoder = check.grayscale_png

    def encode_after_close(*args):
        if not FakeMirror.closed:
            raise AssertionError("Preview encoded before source close")
        return real_encoder(*args)

    check.grayscale_png = encode_after_close
    return check._worker(candidate, seconds, outputs, progress, stop)


def stuck_worker(candidate, seconds, outputs, progress, stop):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if candidate.get("ready_file"):
        Path(candidate["ready_file"]).write_text("ready")
    progress({"fixture_started": True})
    time.sleep(30)


def crashed_worker(candidate, seconds, outputs, progress, stop):
    os._exit(7)


def run_bounded(candidate, seconds, outputs=None, **options):
    """The mirror check's worker supervision as ``sources.mirror.launch`` runs it."""
    worker = options.pop("worker")
    return acquisition.supervise_worker(worker, candidate, seconds, outputs, success_status="captured",
                                        report_factory=lambda: dict(acquisition.new_capture_report(), candidate=candidate),
                                        **options)


def bounded_quickly(worker=changing_worker, seconds=0.22):
    """The launcher's supervision with ``worker`` and a short capture, for command-line tests."""
    real = acquisition.supervise_worker

    def run(_worker, job, _seconds, outputs=None, **options):
        return real(worker, job, seconds, outputs, **options)
    return patch.object(acquisition, "supervise_worker", side_effect=run)


class MirrorDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sys = self.root / "sys"
        self.dev = self.root / "dev"
        (self.sys / "class/video4linux").mkdir(parents=True)

    def node(self, name, label, virtual=True):
        kind = "virtual" if virtual else "platform/camera"
        target = self.sys / "devices" / kind / "video4linux" / name
        target.mkdir(parents=True)
        (target / "name").write_text(label)
        (target / "dev").write_text("81:83\n")
        (self.sys / "class/video4linux" / name).symlink_to(target, target_is_directory=True)

    def test_only_named_virtual_video_candidates_are_discovered_without_open(self):
        self.node("video83", "SteamVR\n")
        self.node("video4", "SteamVR", virtual=False)
        self.node("video99", "Other virtual camera")
        self.node("v4l-subdev28", "SteamVR")
        with patch.object(device.os, "open", side_effect=AssertionError("Device open during discovery")):
            result = device.discover(self.sys, self.dev)
        self.assertEqual([item["node"] for item in result["candidates"]], [str(self.dev / "video83")])
        self.assertEqual(result["status"], "candidates_found")
        self.assertIn(str(self.dev / "video4"), [item["node"] for item in result["unverified_named_nodes"]])

    def test_unavailable_and_selection_never_fall_back_to_physical_devices(self):
        self.assertEqual(device.discover(self.sys, self.dev)["status"], "unavailable")
        self.assertEqual(device.discover(self.root / "missing", self.dev)["reason"], "video_sysfs_absent")
        for requested in ("/dev/video0", "/dev/../dev/video83", "/tmp/alias"):
            with self.subTest(requested=requested), self.assertRaises(device.MirrorError):
                device.select_candidate({"candidates": [CANDIDATE]}, requested)
        with self.assertRaisesRegex(device.MirrorError, "Several"):
            device.select_candidate({"candidates": [CANDIDATE, dict(CANDIDATE, node="/dev/video84")]})
        self.assertEqual(device.select_candidate({"candidates": [CANDIDATE]}), CANDIDATE)

    def test_cli_discovery_and_unavailable_capture_have_no_device_calls(self):
        for args, expected in (([], 0), (["--capture"], 1), (["--capture", "--device", "/dev/video0"], 1)):
            with self.subTest(args=args), patch.object(check, "discover", return_value={"status": "unavailable", "candidates": []}), \
                    patch.object(acquisition, "supervise_worker", side_effect=AssertionError("Unverified capture started")):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(check.main(args), expected)
                report = json.loads(output.getvalue())
                self.assertFalse(report["device_open_attempted"])
                self.assertFalse(report["images_saved"])


class MirrorNativeTests(unittest.TestCase):
    def ioctl(self, fd, request, data, mutate):
        self.requests.append(request)
        if request == device.QUERYCAP:
            cap = device.Capability()
            cap.driver, cap.card, cap.bus_info = self.driver, b"SteamVR", b"platform:v4l2loopback-000"
            cap.capabilities = device.CAP_DEVICE_CAPS
            cap.device_caps = device.CAP_CAPTURE | device.CAP_READWRITE
            data[:] = bytes(cap)
        elif request == device.G_FMT:
            struct.pack_into("=12I", data, device.VideoFormat.fmt.offset, 2, 2,
                             int.from_bytes(b"GREY", "little"), 1, 2, 4, 0, 0, 0, 0, 0, 0)
        else:
            raise AssertionError("Unexpected mutating ioctl")

    def setUp(self):
        self.requests = []
        self.driver = b"v4l2 loopback"

    def test_verified_device_opens_read_only_and_uses_only_get_queries(self):
        fake_stat = SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(81, 83))
        with patch.object(device, "candidate_metadata", return_value=CANDIDATE), \
                patch.object(device.os, "open", return_value=44) as opened, \
                patch.object(device.os, "fstat", return_value=fake_stat), \
                patch.object(device.os, "close") as closed, \
                patch("fcntl.ioctl", side_effect=self.ioctl):
            source = device.NativeMirror(CANDIDATE)
            source.open()
            self.assertEqual(source.format["fourcc"], "GREY")
            self.assertEqual(opened.call_args.args[1] & os.O_ACCMODE, os.O_RDONLY)
            source.close()
            source.close()
            closed.assert_called_once_with(44)
        self.assertEqual(self.requests, [device.QUERYCAP, device.G_FMT])

    def test_changed_metadata_rejected_before_open_and_wrong_driver_closed(self):
        with patch.object(device, "candidate_metadata", return_value=None), patch.object(device.os, "open") as opened:
            with self.assertRaisesRegex(device.MirrorError, "changed"):
                device.NativeMirror(CANDIDATE).open()
            opened.assert_not_called()
        self.driver = b"physical-camera"
        fake_stat = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(81, 83))
        with patch.object(device, "candidate_metadata", return_value=CANDIDATE), \
                patch.object(device.os, "open", return_value=44), \
                patch.object(device.os, "fstat", return_value=fake_stat), \
                patch.object(device.os, "close") as closed, \
                patch("fcntl.ioctl", side_effect=self.ioctl):
            with self.assertRaisesRegex(device.MirrorError, "loopback"):
                device.NativeMirror(CANDIDATE).open()
            closed.assert_called_once_with(44)
        self.assertEqual(self.requests, [device.QUERYCAP])

    def test_native_read_handles_no_data_end_of_stream_and_format_change(self):
        import errno

        source = device.NativeMirror(CANDIDATE)
        source.fd = 44
        source.format = device.validate_format(2, 2, "GREY", 2, 4)
        for code in (errno.EAGAIN, errno.EINTR):
            with patch.object(device.os, "read", side_effect=OSError(code, "not ready")) as read:
                self.assertIsNone(source.read_frame(0.01))
                read.assert_called_once_with(44, 4)
        self.assertEqual(source.read_diagnostics, {"read_attempts": 2, "would_block_reads": 1, "interrupted_reads": 1})
        with patch.object(device.os, "read", return_value=b""):
            with self.assertRaisesRegex(device.MirrorError, "end-of-stream"):
                source.read_frame(0.01)
        with patch.object(device.os, "read", return_value=bytes(4)), \
                patch.object(source, "get_format", return_value=dict(source.format, width=4)):
            with self.assertRaisesRegex(device.MirrorError, "changed"):
                source.read_frame(0.01)

    def test_first_read_registers_lazy_driver_without_waiting_for_poll(self):
        import errno

        source = device.NativeMirror(CANDIDATE)
        source.fd = 44
        source.format = device.validate_format(2, 2, "GREY", 2, 4)
        registered = False

        def lazy_read(fd, count):
            nonlocal registered
            self.assertEqual((fd, count), (44, 4))
            if not registered:
                registered = True
                raise BlockingIOError(errno.EAGAIN, "reader registered; waiting for producer")
            return bytes([80]) * 4

        # Such a driver's poll would never become ready until its first read.
        with patch("select.select", side_effect=AssertionError("poll must not gate read startup")), \
                patch.object(device.os, "read", side_effect=lazy_read), \
                patch.object(source, "get_format", return_value=source.format):
            self.assertIsNone(source.read_frame(0.05))
            self.assertTrue(registered)
            self.assertEqual(source.read_frame(0.05), bytes([80]) * 4)
        self.assertEqual(source.read_diagnostics["read_attempts"], 2)
        self.assertEqual(source.read_diagnostics["would_block_reads"], 1)

    @unittest.skipUnless(shutil.which("g++") and Path("/usr/include/linux/videodev2.h").is_file(), "Native compiler/UAPI headers needed for ABI check")
    def test_ctypes_abi_matches_native_linux_header(self):
        code = '''#include <cstddef>
#include <iostream>
#include <linux/videodev2.h>
int main() { std::cout << sizeof(v4l2_capability) << " " << sizeof(v4l2_format) << " "
<< offsetof(v4l2_format, fmt) << " " << VIDIOC_QUERYCAP << " " << VIDIOC_G_FMT; }
'''
        with tempfile.TemporaryDirectory() as folder:
            source, binary = Path(folder) / "abi.cpp", Path(folder) / "abi"
            source.write_text(code)
            subprocess.run(["g++", "-std=c++17", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=15)
            actual = list(map(int, subprocess.check_output([str(binary)], text=True, timeout=3).split()))
        self.assertEqual(actual, [ctypes.sizeof(device.Capability), ctypes.sizeof(device.VideoFormat),
                                  device.VideoFormat.fmt.offset, device.QUERYCAP, device.G_FMT])


class MirrorFrameTests(unittest.TestCase):
    def test_bounds_and_unsupported_formats_are_rejected(self):
        for values in ((0, 2, "GREY", 2, 4), (9000, 2, "GREY", 9000, 18000),
                       (2, 2, "MJPG", 0, 1024), (3, 2, "YUYV", 6, 12),
                       (4, 3, "NV12", 4, 18), (2, 2, "GREY", 1, 4),
                       (2, 2, "GREY", 2, 3), (2, 2, "GREY", 2, device.MAX_BUFFER_BYTES + 1)):
            with self.subTest(values=values), self.assertRaises(device.MirrorError):
                device.validate_format(*values)

    def test_luma_sampling_skips_padding_and_chroma_and_handles_rgb_order(self):
        packed = bytes([1, 128, 2, 128, 3, 128, 4, 128, 99, 99, 99, 99]) * 2
        fmt = device.validate_format(4, 2, "YUYV", 12, 24)
        self.assertEqual(device.grayscale_sample(packed, fmt, 640), (4, 2, bytes([1, 2, 3, 4]) * 2))
        nv12 = bytes(range(8)) + bytes([255]) * 4
        self.assertEqual(device.grayscale_sample(nv12, device.validate_format(4, 2, "NV12", 4, 12), 640)[2], bytes(range(8)))
        rgb = bytes([255, 0, 0, 0, 0, 255])
        self.assertEqual(device.grayscale_sample(rgb, device.validate_format(2, 1, "RGB3", 6, 6), 640)[2], bytes([76, 28]))
        self.assertEqual(device.grayscale_sample(rgb, device.validate_format(2, 1, "BGR3", 6, 6), 640)[2], bytes([28, 76]))
        with self.assertRaisesRegex(device.MirrorError, "complete"):
            device.grayscale_sample(b"", fmt, 32)

    def test_capture_changes_static_short_empty_error_and_cancellation(self):
        for factory, expected in ((FakeMirror, "captured"), (StaticMirror, "captured"),
                                  (EmptyMirror, "no_frames"), (ShortMirror, "no_frames"), (ErrorMirror, "error")):
            with self.subTest(factory=factory.__name__):
                report, preview = acquisition.capture(CANDIDATE, 0.22, threading.Event(),
                                                 want_preview=True, backend_factory=factory)
                self.assertEqual(report["status"], expected, report)
                self.assertTrue(factory.closed)
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["sample_change_proves_live_camera"])
                if factory == FakeMirror:
                    self.assertGreater(report["grayscale_sample_changes"], 0)
                    self.assertEqual(preview[:2], (2, 2))
                if factory == StaticMirror:
                    self.assertGreater(report["unchanged_sample_reads"], 0)
                    self.assertEqual(report["grayscale_sample_changes"], 0)
                if factory == ShortMirror:
                    self.assertGreater(report["short_reads"], 0)
        stop = threading.Event()
        stop.set()
        with patch.object(acquisition, "NativeMirror") as native:
            report, preview = acquisition.capture(CANDIDATE, 1, stop)
            native.assert_not_called()
        self.assertEqual(report["status"], "cancelled")
        self.assertFalse(report["device_open_attempted"])


class MirrorWorkerTests(unittest.TestCase):
    def test_sigterm_cancels_launcher_and_reaps_its_worker(self):
        code = '''import json, sys
sys.path[:0] = sys.argv[1:3]
from sightcircuit.runtime import guard
from test_mirror_check import CANDIDATE, run_bounded, stuck_worker
with guard.sigterm_cancels():
    report = run_bounded(dict(CANDIDATE, ready_file=sys.argv[3]), 10, worker=stuck_worker)
print(json.dumps(report), flush=True)
sys.exit(report['exit_code'])
'''
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / "ready"
            process = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(ROOT / "tests"), str(ready)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "Fixture worker failed to start")
                process.terminate()
                output, error = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 143, error)
                report = json.loads(output)
                self.assertEqual(report["status"], "cancelled", report)
                self.assertEqual(report["worker_exit_code"], -signal.SIGKILL)
                with self.assertRaises(ProcessLookupError):
                    os.kill(report["worker_pid"], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    def test_real_worker_pipeline_closes_and_exits_without_hardware(self):
        report = run_bounded(CANDIDATE, 0.22, worker=changing_worker)
        self.assertEqual(report["status"], "captured", report)
        self.assertGreaterEqual(report["frames_read"], 2)
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["worker_forced_termination"])
        self.assertEqual(report["worker_exit_code"], 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(report["worker_pid"], 0)

    def test_deadline_escalates_when_worker_ignores_termination(self):
        report = run_bounded(CANDIDATE, 10, worker=stuck_worker, timeout=0.7)
        self.assertTrue(report.get("fixture_started"), report)
        self.assertEqual(report["status"], "timed_out", report)
        self.assertEqual(report["worker_exit_code"], -signal.SIGKILL)
        self.assertTrue(report["worker_forced_termination"])
        self.assertFalse(report["cleanup_ok"])
        self.assertLess(report["worker_elapsed_ms"], 4000)

    def test_abnormal_worker_exit_is_reported(self):
        report = run_bounded(CANDIDATE, 1, worker=crashed_worker)
        self.assertEqual(report["status"], "error", report)
        self.assertEqual(report["worker_exit_code"], 7)
        self.assertEqual(report["reason"], "worker_exited_without_result")

    def test_optional_png_is_published_after_close_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "mirror.png"
            with patch.object(check, "discover", return_value={"status": "candidates_found", "candidates": [CANDIDATE]}), \
                    bounded_quickly():
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(check.main(["--capture", "--preview", str(target)]), 0)
                report = json.loads(output.getvalue())
                self.assertTrue(report["images_saved"])
                png = target.read_bytes()
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(struct.unpack_from(">II", png, 16), (2, 2))
                try:
                    import av
                except ImportError:
                    pass
                else:
                    with av.open(io.BytesIO(png), format="image2pipe") as image:
                        frame = next(image.decode(video=0))
                        self.assertEqual((frame.width, frame.height), (2, 2))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(check.main(["--capture", "--preview", str(target)]), 1)
                self.assertEqual(target.read_bytes(), png)
                self.assertFalse(list(Path(folder).glob("*.part")))

    def test_output_race_cannot_overwrite_an_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "mirror.png"

            def raced(_worker, candidate, seconds, outputs, **_options):
                Path(outputs["preview"]["path"]).write_bytes(b"fixture")
                target.write_bytes(b"keep me")
                return dict(check.new_report(), status="captured", preview_ready=True, exit_code=0)

            with patch.object(check, "discover", return_value={"candidates": [CANDIDATE]}), \
                    patch.object(acquisition, "supervise_worker", side_effect=raced), redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(["--capture", "--preview", str(target)]), 1)
            self.assertEqual(target.read_bytes(), b"keep me")
            self.assertFalse(list(Path(folder).glob("*.part")))


if __name__ == "__main__":
    unittest.main()
