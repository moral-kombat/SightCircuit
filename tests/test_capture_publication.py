"""Test the adapted publisher itself with generated frames and real processes."""
import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from scripts import shm_capture_check as check

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "artifacts/capture-publication-reference/sightcircuit-publication-fixture"


@unittest.skipUnless(FIXTURE.is_file() and check.READER.is_file(),
                     "build publication reference and shared-memory reader first")
class CapturePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sightcircuit-publication-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_fixture(self, *args, timeout=5):
        return subprocess.run([str(FIXTURE), *map(str, args)], capture_output=True, text=True, timeout=timeout)

    def producer(self, seconds=4):
        source = self.root / "frames.shm"
        process = subprocess.Popen([str(FIXTURE), "--create", str(source), "--seconds", str(seconds)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=3), "publisher did not become ready")
        report = json.loads(process.stdout.readline())
        self.assertEqual(report.get("event"), "publication_ready", report)
        return source, process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=3)

    def test_default_guard_and_production_option_parser(self):
        self.assertEqual(self.run_fixture().returncode, 3)
        cases = (
            ([], 3), (["--help"], 0),
            (["--allow-experimental-capture", "--seconds", "1"], 0),
            (["--allow-experimental-capture", "--seconds", "10", "--max-fps", "10"], 0),
            (["--seconds", "1"], 64),
            (["--allow-experimental-capture"], 64),
            (["--allow-experimental-capture", "--seconds", "0"], 64),
            (["--allow-experimental-capture", "--seconds", "11"], 64),
            (["--allow-experimental-capture", "--seconds", "1", "--max-fps", "0"], 64),
            (["--allow-experimental-capture", "--seconds", "1", "--max-fps", "11"], 64),
            (["--allow-experimental-capture", "--seconds", "1", "--seconds", "1"], 64),
            (["identify", "out"], 64), (["--help", "--allow-experimental-capture"], 64),
        )
        for args, code in cases:
            with self.subTest(args=args):
                result = self.run_fixture("--check-options", *args)
                self.assertEqual(result.returncode, code, result.stderr)
                if code != 64:
                    report = json.loads(result.stdout)
                    self.assertFalse(report["runtime_initialized"])
                    self.assertFalse(report["capture_started"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_native_publisher_validation_and_control(self):
        result = self.run_fixture("--self-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "passed")

    def test_real_two_slot_copies_match_publication_metadata(self):
        source, process = self.producer()
        for camera in (0, 1):
            preview = self.root / f"slot-{camera}.pgm"
            report = check.run_reader(source, 2, camera, frames=3, preview_pgm=preview)
            self.assertEqual(report["status"], "captured", report)
            native = report["native"]
            self.assertEqual(native["frames_received"], 3)
            self.assertTrue(native["cleanup_ok"])
            self.assertEqual(native["last_first_pixel"], (native["last_frame_count"] + 17 * camera) % 256)
            pixels = preview.read_bytes().split(b"\n", 3)[3]
            self.assertEqual(pixels, bytes([native["last_first_pixel"]]) * len(pixels))
        process.terminate()
        output, _ = process.communicate(timeout=3)
        self.assertTrue(json.loads(output)["cleanup_ok"])
        self.assertFalse(source.exists())

    def test_existing_files_symlinks_and_fifo_are_preserved(self):
        target = self.root / "existing"
        target.write_bytes(b"retain original")
        link = self.root / "link"
        link.symlink_to(target)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (target, link, fifo):
            result = self.run_fixture("--create", path, "--seconds", "1")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["reason"], "publication_create_failed")
        self.assertEqual(target.read_bytes(), b"retain original")
        self.assertTrue(link.is_symlink())
        self.assertTrue(fifo.exists())

    def test_sigint_and_sigterm_close_and_remove_owned_file(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                source, process = self.producer()
                process.send_signal(number)
                output, error = process.communicate(timeout=3)
                self.assertFalse(error)
                report = json.loads(output)
                self.assertEqual(process.returncode, 128 + number)
                self.assertEqual(report["reason"], "signal")
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(source.exists())

    def test_deadline_closes_owned_file(self):
        started = time.monotonic()
        source, process = self.producer(seconds=1)
        output, error = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(json.loads(output)["reason"], "deadline")
        self.assertTrue(json.loads(output)["cleanup_ok"])
        self.assertFalse(source.exists())
        self.assertLess(time.monotonic() - started, 2.5)

    def test_replacement_stops_writer_and_preserves_foreign_file(self):
        source, process = self.producer()
        original = self.root / "original"
        source.rename(original)
        source.write_bytes(b"retain replacement")
        output, _ = process.communicate(timeout=3)
        report = json.loads(output)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["reason"], "publication_source_changed")
        self.assertFalse(report["cleanup_ok"])
        self.assertEqual(source.read_bytes(), b"retain replacement")

    def test_parent_death_kills_writer_and_stale_file_is_not_overwritten(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(old), 0, 0, 0) != 0 or libc.prctl(36, 1, 0, 0, 0) != 0:
            self.skipTest("Linux child-subreaper control unavailable")
        source = self.root / "orphan.shm"
        child_pid = None
        launcher = None
        try:
            body = """
import json, subprocess, sys, time
child = subprocess.Popen([sys.argv[1], '--create', sys.argv[2], '--seconds', '10'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
ready = json.loads(child.stdout.readline())
print(json.dumps({'pid': child.pid, 'ready': ready}), flush=True)
time.sleep(30)
"""
            launcher = subprocess.Popen([sys.executable, "-c", body, str(FIXTURE), str(source)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with selectors.DefaultSelector() as selector:
                selector.register(launcher.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=3))
            report = json.loads(launcher.stdout.readline())
            child_pid = report["pid"]
            self.assertEqual(report["ready"]["event"], "publication_ready")
            launcher.kill()
            launcher.communicate(timeout=3)
            end = time.monotonic() + 3
            while time.monotonic() < end:
                pid, status = os.waitpid(child_pid, os.WNOHANG)
                if pid:
                    child_pid = None
                    self.assertTrue(os.WIFSIGNALED(status))
                    self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                    break
                time.sleep(0.01)
            self.assertIsNone(child_pid, "orphaned publisher did not exit")
            identity = source.stat()
            retry = self.run_fixture("--create", source, "--seconds", "1")
            self.assertEqual(retry.returncode, 1)
            self.assertEqual(source.stat().st_ino, identity.st_ino)
            self.assertEqual(source.stat().st_size, identity.st_size)
        finally:
            if launcher and launcher.poll() is None:
                launcher.kill()
                launcher.communicate(timeout=3)
            if child_pid:
                try:
                    pid, _ = os.waitpid(child_pid, os.WNOHANG)
                    if not pid:
                        os.kill(child_pid, signal.SIGKILL)
                        os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass
            libc.prctl(36, old.value, 0, 0, 0)


if __name__ == "__main__":
    unittest.main()
