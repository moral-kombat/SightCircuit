"""Physical-device query and supervisor checks with an isolated fake Vulkan loader."""
import contextlib
import ctypes
import json
import os
from pathlib import Path
import signal
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import capture_gpu_check as check


@unittest.skipUnless(check.BINARY.is_file(), "build scripts/build_capture_gpu_check.sh first")
class CaptureGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="sightcircuit-gpu-tests-")
        cls.root = Path(cls.temporary.name)
        cls.library = cls.root / "libvulkan.so.1"
        subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
            "-fPIC", "-shared", f"-I{check.ROOT / '.cache/vulkan-headers/include'}",
            str(check.ROOT / "tests/fixtures/fake_capture_vulkan.cpp"), "-o", str(cls.library),
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.trace = self.root / "trace"
        self.trace.unlink(missing_ok=True)

    @contextlib.contextmanager
    def driver(self, mode="supported"):
        with patch.dict(os.environ, {
            "LD_LIBRARY_PATH": str(self.root), "SIGHTCIRCUIT_FAKE_VULKAN_MODE": mode,
            "SIGHTCIRCUIT_FAKE_VULKAN_TRACE": str(self.trace),
        }):
            yield

    def query(self, mode="supported", **kwargs):
        with self.driver(mode):
            return check.run_query(**kwargs)

    def test_default_and_bad_arguments_never_load_vulkan(self):
        with self.driver():
            for args, code in (([], 3), (["--unknown"], 64),
                               (["--query-device", "--unknown"], 64), (["--help"], 0)):
                result = subprocess.run([str(check.BINARY), *args], capture_output=True, text=True, timeout=2)
                self.assertEqual(result.returncode, code, result.stderr)
                if code != 0:
                    self.assertFalse(json.loads(result.stdout)["vulkan_instance_created"])
            for args, code in (([], 3), (["--unknown"], 2), (["--help"], 0)):
                result = subprocess.run([sys.executable, "scripts/capture_gpu_check.py", *args],
                                        cwd=check.ROOT, capture_output=True, text=True, timeout=2)
                self.assertEqual(result.returncode, code)
            self.assertFalse(self.trace.exists())

    def test_supported_query_checks_structure_chain_and_cleanup(self):
        report = self.query()
        self.assertEqual(report["status"], "queried", report)
        self.assertTrue(report["worker_reaped"])
        self.assertFalse(report["worker_forced_termination"])
        native = report["native"]
        self.assertTrue(native["vulkan_instance_destroyed"])
        gpu = native["devices"][0]
        self.assertEqual(gpu["name"], 'Fake "GPU"\n')
        self.assertTrue(gpu["capability_checks_passed"])
        self.assertEqual(gpu["nv12_target_modifiers"][0]["memory_plane_count"], 4)
        self.assertTrue(gpu["nv12_external_image"]["dedicated_only"])
        self.assertEqual(self.trace.read_text().splitlines(), ["load", "create", "external_format", "destroy", "unload"])

    def test_absent_capabilities_are_diagnostic_results(self):
        for mode in ("missing_extension", "no_modifier", "no_ycbcr", "compute_only", "no_blit",
                     "unsupported_image", "small_extent", "not_importable", "many_planes",
                     "small_r8", "no_host_cache"):
            with self.subTest(mode=mode):
                self.trace.unlink(missing_ok=True)
                report = self.query(mode)
                self.assertEqual(report["exit_code"], 0, report)
                self.assertFalse(report["native"]["devices"][0]["capability_checks_passed"])
                self.assertTrue(report["cleanup_ok"])
                if mode in ("missing_extension", "no_modifier"):
                    self.assertNotIn("external_format", self.trace.read_text())

    def test_bounds_failures_and_driver_errors_destroy_owned_instance(self):
        for mode in ("many_devices", "many_extensions", "many_modifiers", "no_devices", "image_error"):
            with self.subTest(mode=mode):
                self.trace.unlink(missing_ok=True)
                report = self.query(mode)
                self.assertEqual(report["exit_code"], 1, report)
                self.assertEqual(report["status"], "query_failed")
                self.assertTrue(report["cleanup_ok"])
                self.assertTrue(report["native"]["vulkan_instance_destroyed"])
                self.assertTrue(self.trace.read_text().endswith("destroy\nunload\n"))

    def test_early_failures_do_not_claim_instance_creation(self):
        for mode in ("old_loader", "create_failure"):
            with self.subTest(mode=mode):
                report = self.query(mode)
                self.assertEqual(report["status"], "query_failed")
                self.assertFalse(report["vulkan_instance_created"])
                self.assertTrue(report["cleanup_ok"])

    def test_hung_driver_and_destruction_are_killed_and_reaped(self):
        for mode in ("hang", "hang_destroy"):
            with self.subTest(mode=mode):
                started = time.monotonic()
                report = self.query(mode, timeout=0.2)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(report["status"], "timed_out", report)
                self.assertTrue(report["worker_forced_termination"])
                self.assertTrue(report["worker_reaped"])
                self.assertFalse(report["cleanup_ok"])
                self.assertIsNone(report["vulkan_instance_created"])
                self.assertEqual(report["worker_exit_code"], -signal.SIGKILL)
                with self.assertRaises(ProcessLookupError):
                    os.kill(report["worker_pid"], 0)

    def test_native_crash_is_contained(self):
        report = self.query("crash")
        self.assertEqual(report["reason"], "query_terminated_by_signal")
        self.assertEqual(report["worker_signal"], signal.SIGSEGV)
        self.assertTrue(report["worker_reaped"])
        self.assertFalse(report["cleanup_ok"])

    def test_parent_death_kills_native_worker(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(old), 0, 0, 0) != 0 or libc.prctl(36, 1, 0, 0, 0) != 0:
            self.skipTest("Linux child-subreaper control unavailable")
        child_pid = None
        launcher = None
        try:
            body = """
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.argv[1], '--query-device'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
trace = Path(sys.argv[2])
end = time.monotonic() + 2
while not trace.exists() or 'create' not in trace.read_text():
    if time.monotonic() > end: raise RuntimeError('fake driver did not start')
    time.sleep(0.01)
print(child.pid, flush=True)
time.sleep(30)
"""
            with self.driver("hang"):
                launcher = subprocess.Popen([sys.executable, "-c", body, str(check.BINARY), str(self.trace)],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with selectors.DefaultSelector() as selector:
                selector.register(launcher.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=3))
            child_pid = int(launcher.stdout.readline())
            launcher.kill()
            launcher.communicate(timeout=3)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                pid, status = os.waitpid(child_pid, os.WNOHANG)
                if pid:
                    child_pid = None
                    self.assertTrue(os.WIFSIGNALED(status))
                    self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                    break
                time.sleep(0.01)
            self.assertIsNone(child_pid, "orphaned query did not exit")
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

    def test_sigterm_cancels_supervisor_and_reaps_worker(self):
        with self.driver("hang"):
            process = subprocess.Popen([sys.executable, "scripts/capture_gpu_check.py", "--query-device"],
                                       cwd=check.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 3
                while not self.trace.exists() or "create" not in self.trace.read_text():
                    if time.monotonic() > deadline:
                        self.fail("fake driver did not start")
                    time.sleep(0.01)
                process.send_signal(signal.SIGTERM)
                output, error = process.communicate(timeout=3)
                self.assertFalse(error)
                report = json.loads(output)
                self.assertEqual(process.returncode, 128 + signal.SIGTERM)
                self.assertEqual(report["status"], "cancelled")
                self.assertTrue(report["worker_reaped"])
                with self.assertRaises(ProcessLookupError):
                    os.kill(report["worker_pid"], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=3)


class GpuSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sightcircuit-gpu-supervisor-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def worker(self, source):
        path = self.root / "worker"
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o700)
        return path

    def test_missing_binary_and_malformed_or_inconsistent_reports(self):
        report = check.run_query(binary=self.root / "missing")
        self.assertEqual(report["reason"], "query_not_built")
        self.assertFalse(report["query_started"])
        for source in ("print('not json')", "print('[]')", "print('{}')",
                       "print('{\"exit_code\": 0, \"status\": \"queried\"}')"):
            with self.subTest(source=source):
                report = check.run_query(binary=self.worker(source))
                self.assertEqual(report["reason"], "invalid_query_report", report)
                self.assertTrue(report["worker_reaped"])
                self.assertFalse(report["cleanup_ok"])

    def test_stdout_and_stderr_are_bounded_before_allocation_growth(self):
        for fd in (1, 2):
            with self.subTest(fd=fd):
                binary = self.worker(f"import os\nwhile True: os.write({fd}, b'x' * 16384)\n")
                report = check.run_query(binary=binary, timeout=2)
                self.assertEqual(report["reason"], "query_output_limit", report)
                self.assertTrue(report["worker_reaped"])
                self.assertLessEqual(len(report.get("driver_stderr", "")), check.MAX_STDERR)


if __name__ == "__main__":
    unittest.main()
