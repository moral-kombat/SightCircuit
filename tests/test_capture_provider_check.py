"""Real metadata inspector with generated peers; no layer activation or images."""
import array
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts import capture_provider_check as check
from scripts import capture_supervisor

FIXTURE = check.BINARY.parent / "framejacker-provider-fixture"


@unittest.skipUnless(check.BINARY.is_file() and FIXTURE.is_file(), "build --provider-fixes first")
class ProviderMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fj-meta-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @contextlib.contextmanager
    def provider(self, mode="normal"):
        runtime = self.root / ("run-" + mode)
        runtime.mkdir(mode=0o700)
        process = subprocess.Popen([str(FIXTURE), "--serve-generated", str(runtime), mode],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=3))
            self.assertEqual(process.stdout.readline().strip(), "ready")
            yield runtime, process
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=2)

    def fake_program(self, body):
        path = self.root / "fake-worker"
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)
        return path

    def test_default_help_and_invalid_arguments_never_connect(self):
        env = dict(os.environ, XDG_RUNTIME_DIR=str(self.root / "missing"))
        for args, expected in (([], 3), (["--help"], 0), (["--unknown"], 64),
                               (["--seconds", "1"], 64), (["--inspect", "--seconds", "6"], 64),
                               (["--inspect", "--inspect"], 64)):
            result = subprocess.run([str(check.BINARY), *args], env=env, capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertFalse(json.loads(result.stdout)["lease_established"])
        for args, expected in (([], 3), (["--help"], 0), (["--unknown"], 2)):
            result = subprocess.run([sys.executable, "scripts/capture_provider_check.py", *args],
                                    cwd=check.ROOT, env=env, capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, expected)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_metadata_snapshot_and_stability_without_image_access(self):
        with self.provider() as (runtime, peer):
            report = check.inspect(runtime=runtime)
        self.assertEqual(report["status"], "inspected", report)
        self.assertTrue(report["worker_reaped"])
        native = report["native"]
        self.assertEqual(native["provider_protocol_version"], 3)
        self.assertEqual(native["peer_pid"], peer.pid)
        self.assertEqual(native["reviewed_profile_candidates"], 1)
        self.assertGreaterEqual(native["registry_checks"], 2)
        self.assertLessEqual(native["registry_checks"], 12)
        entry = native["records"][0]
        self.assertEqual((entry["width"], entry["height"], entry["plane_count"]), (2464, 2464, 2))
        self.assertEqual(len(entry["planes"]), 2)
        self.assertEqual(native["frames_read"], 0)
        for key in ("image_descriptors_requested", "image_data_accessed", "vulkan_loaded", "gpu_import_attempted",
                    "capture_contract_available", "producer_completion_observed"):
            self.assertIs(native[key], False)

    def test_empty_and_incompatible_metadata_are_diagnostic_successes(self):
        for mode in ("empty", "unsupported"):
            with self.subTest(mode=mode), self.provider(mode) as (runtime, _):
                report = check.inspect(runtime=runtime)
            self.assertEqual(report["status"], "inspected", report)
            self.assertEqual(report["reviewed_profile_candidates"], 0)
            self.assertEqual(report["record_count"], 0 if mode == "empty" else 1)
            self.assertLessEqual(report["native"]["registry_checks"], 12)
            if mode == "unsupported":
                self.assertEqual(report["native"]["records"][0]["profile_rejection_reason"],
                                 "provider_unsupported_exporter_metadata")

    def test_registry_changes_and_provider_loss_reject_the_observation(self):
        for mode in ("change", "drop"):
            with self.subTest(mode=mode), self.provider(mode) as (runtime, _):
                report = check.inspect(runtime=runtime, seconds=2)
            self.assertEqual(report["status"], "inspection_failed", report)
            self.assertFalse(report["native"]["registry_unchanged"])
            self.assertTrue(report["worker_reaped"])
            self.assertEqual(report["frames_read"], 0)

    def test_missing_and_non_socket_endpoints_fail_without_pixel_access(self):
        report = check.inspect(runtime=self.root)
        self.assertEqual(report["status"], "inspection_failed")
        self.assertFalse(report["native"]["lease_established"])
        parent = self.root / "framejacker-camera-provider"
        parent.mkdir(mode=0o700)
        (parent / "camera.sock").symlink_to("/dev/null")
        report = check.inspect(runtime=self.root)
        self.assertEqual(report["native"]["reason"], "provider_endpoint_not_private")
        self.assertFalse(report["image_data_accessed"])

    def test_legacy_protocol_and_unexpected_image_rights_are_rejected(self):
        for version in (2, 3):
            with self.subTest(version=version):
                directory = self.root / f"peer-{version}"
                directory.mkdir(mode=0o700)
                path = directory / "camera.sock"
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(path)); path.chmod(0o600); server.listen(1)
                requests = []
                def serve():
                    with server.accept()[0] as connection:
                        request = bytearray()
                        while len(request) < 328:
                            request.extend(connection.recv(328 - len(request)))
                        requests.append(struct.unpack_from("<IIII", request))
                        data = struct.pack("<IIIIQQ", 0x50434A46, version, 328, 1, 1, 1) + bytes(296)
                        fd = os.memfd_create("generated-unexpected-right", os.MFD_CLOEXEC)
                        try:
                            os.ftruncate(fd, 4096)
                            connection.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
                        finally:
                            os.close(fd)
                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    result = subprocess.run([str(check.BINARY), "--inspect", "--socket", str(path)],
                                            capture_output=True, text=True, timeout=2)
                    self.assertEqual(result.returncode, 1)
                    native = json.loads(result.stdout)
                    self.assertFalse(native["image_data_accessed"])
                    self.assertEqual(native["frames_read"], 0)
                    self.assertEqual(native["reason"], "provider_protocol_mismatch" if version == 2 else "provider_invalid_record")
                finally:
                    thread.join(timeout=2)
                    server.close()
                self.assertFalse(thread.is_alive())
                self.assertEqual(requests[0], (0x50434A46, 3, 328, 6))

    def test_outer_deadline_kills_and_reaps_a_stuck_worker(self):
        binary = self.fake_program("import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n")
        report = check.inspect(binary=binary, timeout=0.1)
        self.assertEqual(report["status"], "timed_out", report)
        self.assertTrue(report["worker_forced_termination"])
        self.assertTrue(report["worker_reaped"])
        self.assertFalse(report["cleanup_ok"])
        self.assertLess(report["elapsed_ms"], 1800)
        with self.assertRaises(ProcessLookupError): os.kill(report["worker_pid"], 0)

    def test_output_limits_crashes_and_malformed_reports_are_contained(self):
        for body in ("import sys\nsys.stdout.write('a'*1048576)\n", "import sys\nsys.stderr.write('b'*1048576)\n",
                     "print('{}')\n", "import os,signal\nos.kill(os.getpid(), signal.SIGKILL)\n"):
            with self.subTest(body=body):
                report = check.inspect(binary=self.fake_program(body), timeout=1)
                self.assertEqual(report["exit_code"], 1)
                self.assertTrue(report["worker_reaped"])
                self.assertFalse(report["cleanup_ok"])
                self.assertLessEqual(len(report.get("worker_stderr", "")), check.MAX_STDERR)

    def test_inconsistent_or_capture_claiming_reports_are_rejected(self):
        with self.provider() as (runtime, _):
            native = check.inspect(runtime=runtime)["native"]
        for field, value in (("metadata_only", False), ("capture_contract_available", True),
                             ("image_data_accessed", True), ("vulkan_loaded", True),
                             ("frames_read", 1), ("record_count", 2), ("registry_unchanged", False)):
            with self.subTest(field=field):
                changed = dict(native, **{field: value})
                binary = self.fake_program("print(" + repr(json.dumps(changed)) + ")\n")
                report = check.inspect(binary=binary)
                self.assertEqual(report["exit_code"], 1)
                self.assertFalse(report["cleanup_ok"])

    def test_capture_cli_is_blocked_before_any_worker_launch(self):
        output = io.StringIO()
        with patch.object(capture_supervisor, "supervise", side_effect=AssertionError("must not start")), contextlib.redirect_stdout(output):
            self.assertEqual(capture_supervisor.main(["--capture"]), 3)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "producer_access_contract_unavailable")
        self.assertFalse(report["worker_started"])

    def test_signals_cancel_and_reap_the_metadata_worker(self):
        with self.provider() as (runtime, _):
            for number in (signal.SIGINT, signal.SIGTERM):
                process = subprocess.Popen([sys.executable, "scripts/capture_provider_check.py", "--inspect", "--seconds", "5"],
                                           cwd=check.ROOT, env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime)),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    time.sleep(0.2)
                    process.send_signal(number)
                    output, error = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 128 + number, error)
                    report = json.loads(output)
                    self.assertEqual(report["status"], "cancelled")
                    self.assertTrue(report["worker_reaped"])
                    self.assertFalse(report["image_data_accessed"])
                finally:
                    if process.poll() is None: process.kill()
                    process.communicate(timeout=2)

    def test_parent_death_kills_the_native_inspector(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(old), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
            self.skipTest("Linux subreaper unavailable")
        launcher = child = None
        try:
            with self.provider() as (runtime, _):
                body = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.argv[1], '--inspect', '--seconds', '5'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
time.sleep(30)
"""
                launcher = subprocess.Popen([sys.executable, "-c", body, str(check.BINARY)],
                                            env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime)),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                with selectors.DefaultSelector() as selector:
                    selector.register(launcher.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=3))
                child = int(launcher.stdout.readline())
                time.sleep(0.1)
                launcher.kill(); launcher.wait(timeout=2)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    found, status = os.waitpid(child, os.WNOHANG)
                    if found:
                        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGKILL)
                        child = None
                        break
                    time.sleep(0.01)
                self.assertIsNone(child)
        finally:
            if launcher:
                if launcher.poll() is None: launcher.kill()
                launcher.communicate(timeout=2)
            if child:
                with contextlib.suppress(ProcessLookupError): os.kill(child, signal.SIGKILL)
                with contextlib.suppress(ChildProcessError): os.waitpid(child, 0)
            libc.prctl(36, old.value, 0, 0, 0)
