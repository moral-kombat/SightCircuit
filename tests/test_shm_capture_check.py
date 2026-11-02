"""Exercise actual cross-process luma publication without camera/runtime access."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import shm_capture_check as check

ROOT = Path(__file__).resolve().parents[1]
READER = check.READER
FIXTURE = READER.with_name("framejacker-shm-fixture")


class ShmTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def helper(self, body):
        path = self.root / "fake-reader"
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)
        return path


class ShmSupervisorTests(ShmTestCase):
    def test_default_only_inspects_metadata(self):
        source = self.root / "not-present"
        output = io.StringIO()
        with patch.object(check, "run_reader", side_effect=AssertionError("must not read")), redirect_stdout(output):
            self.assertEqual(check.main(["--source", str(source)]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "discovery_only")
        self.assertFalse(report["source"]["present"])
        self.assertFalse(report["source"]["header_read"])
        self.assertFalse(report["read_attempted"])
        source.symlink_to(self.root / "also-absent")
        self.assertEqual(check.inspect_source(source)["kind"], "symlink")

    def test_missing_binary_and_invalid_reports(self):
        report = check.run_reader(self.root / "source", 1, binary=self.root / "absent")
        self.assertEqual(report["reason"], "reader_not_built")
        self.assertFalse(report["read_attempted"])
        fake = self.helper("print('invalid')\n")
        report = check.run_reader(self.root / "source", 1, binary=fake)
        self.assertEqual(report["reason"], "invalid_reader_report")
        self.assertFalse(report["cleanup_ok"])

    def test_native_crash_is_contained(self):
        fake = self.helper("import os, resource, signal\nresource.setrlimit(resource.RLIMIT_CORE, (0, 0))\nos.kill(os.getpid(), signal.SIGBUS)\n")
        # A system crash handler (such as apport) may hold the dying process for
        # seconds regardless of RLIMIT_CORE; the deadline must not mask the signal.
        report = check.run_reader(self.root / "source", 1, binary=fake, timeout=20)
        self.assertEqual(report["reason"], "reader_terminated_by_signal")
        self.assertEqual(report["native_signal"], signal.SIGBUS)
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["images_saved"])

    def test_deadline_kills_and_reaps_stuck_reader(self):
        fake = self.helper("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n")
        started = time.monotonic()
        report = check.run_reader(self.root / "source", 1, binary=fake, timeout=0.2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(report["status"], "timed_out")
        self.assertTrue(report["reader_forced_termination"])
        self.assertEqual(report["reader_exit_code"], -signal.SIGKILL)
        with self.assertRaises(ProcessLookupError):
            os.kill(report["reader_pid"], 0)

    def test_preview_bounds_and_atomic_no_overwrite(self):
        pgm = self.root / "preview.pgm"
        pgm.write_bytes(b"P5\n2 2\n255\n" + b"\x55" * 4)
        png, width, height = check.encode_preview(pgm)
        self.assertEqual((width, height), (2, 2))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        for data in (b"P5\n999999 2\n255\n", b"P5\n2 2\n255\nshort", b"P6\n"):
            pgm.write_bytes(data)
            with self.assertRaises(ValueError):
                check.encode_preview(pgm)
        target = self.root / "existing.png"
        target.write_bytes(b"preserve")
        output = io.StringIO()
        with redirect_stdout(output):
            code = check.main(["--read", "--source", str(pgm), "--preview", str(target)])
        self.assertEqual(code, 1)
        self.assertEqual(target.read_bytes(), b"preserve")
        self.assertFalse(json.loads(output.getvalue())["read_attempted"])


@unittest.skipUnless(READER.is_file() and FIXTURE.is_file(), "build with bash scripts/build_shm_reader.sh")
class ShmNativeTests(ShmTestCase):
    def producer(self, mode="live", **options):
        path = self.root / (mode + "-" + str(time.monotonic_ns()) + ".shm")
        command = [str(FIXTURE), "--create", str(path), "--seconds", "4", "--mode", mode]
        for key, value in options.items():
            command += ["--" + key, str(value)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_producer, process)
        self.assertEqual(json.loads(process.stdout.readline())["event"], "fixture_ready")
        return path, process

    @staticmethod
    def stop_producer(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=2)

    def native(self, *args):
        result = subprocess.run([str(READER), *map(str, args)], capture_output=True, text=True, timeout=14)
        report = json.loads(result.stdout)
        self.assertEqual(report["exit_code"], result.returncode)
        self.assertFalse(result.stderr)
        return report

    def test_native_default_and_nonregular_sources(self):
        self.assertEqual(self.native()["status"], "not_run")
        self.assertEqual(self.native("--read", self.root / "absent")["reason"], "source_missing")
        regular = self.root / "file"
        regular.write_bytes(bytes(96))
        link = self.root / "link"
        link.symlink_to(regular)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for source in (link, fifo, self.root, Path("/dev/null")):
            self.assertEqual(self.native("--read", source)["reason"], "source_not_regular")

    def test_layout_bounds_and_camera_slot(self):
        path = self.root / "layout"
        for magic, width, height, cameras, payload, reason in (
            (0, 2, 2, 2, 8, "invalid_header"),
            (0x4D525054, 0, 2, 2, 0, "invalid_header"),
            (0x4D525054, 8192, 8192, 2, 8, "invalid_header"),
            (0x4D525054, 2, 2, 3, 12, "invalid_header"),
            (0x4D525054, 2, 2, 2, 7, "invalid_file_size"),
            (0x4D525054, 2, 2, 1, 4, "camera_slot_absent"),
        ):
            with self.subTest(reason=reason, width=width):
                path.write_bytes(struct.pack("=IIII", magic, width, height, cameras) + bytes(80 + payload))
                self.assertEqual(self.native("--read", path, "--camera", 1)["reason"], reason)

    def test_real_cross_process_copies_and_preview_both_slots(self):
        for camera in (0, 1):
            with self.subTest(camera=camera):
                source, _ = self.producer("split", width=256, height=256, fps=60)
                pgm = self.root / (str(camera) + ".pgm")
                report = check.run_reader(source, 2, camera, frames=3, preview_pgm=pgm)
                self.assertEqual(report["status"], "captured", report)
                native = report["native"]
                self.assertEqual(native["frames_received"], 3)
                self.assertTrue(native["cleanup_ok"])
                self.assertFalse(native["writer_ordering_verified"])
                expected = (native["last_frame_count"] + 17 * camera) % 256
                self.assertEqual(native["last_first_pixel"], expected)
                self.assertEqual(pgm.read_bytes().split(b"\n", 3)[3], bytes([expected]) * (256 * 256))

    def test_busy_and_stale_publications_are_not_frames(self):
        for mode, status in (("odd", "no_frames"), ("stale", "stale_input")):
            with self.subTest(mode=mode):
                source, _ = self.producer(mode)
                report = check.run_reader(source, 1)
                self.assertEqual(report["status"], status, report)
                self.assertEqual(report["native"]["frames_received"], 0)
                self.assertFalse(report["images_saved"])

    def test_stalled_producer_is_stale_at_end(self):
        source, _ = self.producer("one")
        report = check.run_reader(source, 1, max_age_ms=100)
        self.assertEqual(report["status"], "stale_input", report)
        self.assertEqual(report["native"]["frames_received"], 1)

    def test_truncation_replacement_and_restart_are_structured_failures(self):
        for mode, reason in (("truncate", "source_resized"), ("replace", "source_replaced"), ("restart", "source_restarted")):
            with self.subTest(mode=mode):
                source, _ = self.producer(mode)
                report = check.run_reader(source, 2)
                self.assertEqual(report["status"], "error", report)
                self.assertIn(report["reason"], (reason, "reader_terminated_by_signal"))
                self.assertEqual(report["exit_code"], 1)

    def test_png_publication_and_publication_race(self):
        source, _ = self.producer()
        target = self.root / "result.png"
        output = io.StringIO()
        with redirect_stdout(output):
            code = check.main(["--read", "--source", str(source), "--frames", "2", "--preview", str(target)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["images_saved"])
        self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))
        try:
            import av
        except ImportError:
            pass
        else:
            with av.open(str(target)) as container:
                frame = next(container.decode(video=0))
                self.assertEqual((frame.width, frame.height), (64, 48))
        race = self.root / "race.png"
        real_link = os.link

        def racing_link(name, destination, **kwargs):
            race.write_bytes(b"preserve concurrent file")
            return real_link(name, destination, **kwargs)

        with patch.object(os, "link", side_effect=racing_link), redirect_stdout(io.StringIO()):
            code = check.main(["--read", "--source", str(source), "--frames", "1", "--preview", str(race)])
        self.assertEqual(code, 1)
        self.assertEqual(race.read_bytes(), b"preserve concurrent file")
        self.assertEqual(list(self.root.glob(".framejacker-shm-*")), [])

    def test_fixture_refuses_existing_and_reserved_outputs(self):
        path = self.root / "preserve"
        path.write_bytes(b"private")
        alias = self.root / "shared"
        alias.symlink_to("/dev/shm", target_is_directory=True)
        for target in (path, Path("/dev/shm/mrhand-pt"), alias / "mrhand-pt"):
            result = subprocess.run([str(FIXTURE), "--create", str(target)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 1)
        self.assertEqual(path.read_bytes(), b"private")

    def test_supervisor_sigterm_reaps_native_reader(self):
        source, _ = self.producer()
        ready = self.root / "reader-ready"
        program = ("from pathlib import Path\nfrom scripts import shm_capture_check as c\n"
                   "class ReadyPopen(c.subprocess.Popen):\n"
                   " def communicate(self, *args, **kwargs):\n"
                   f"  Path({str(ready)!r}).write_text(str(self.pid))\n"
                   "  return super().communicate(*args, **kwargs)\n"
                   "c.subprocess.Popen = ReadyPopen\nraise SystemExit(c.main())\n")
        process = subprocess.Popen([sys.executable, "-c", program, "--read", "--source", str(source)],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not ready.exists():
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "native reader was not started")
            process.terminate()
            output, error = process.communicate(timeout=3)
            report = json.loads(output)
            self.assertEqual(process.returncode, 143, (output, error))
            self.assertEqual(report["status"], "cancelled")
            with self.assertRaises(ProcessLookupError):
                os.kill(report["reader_pid"], 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
