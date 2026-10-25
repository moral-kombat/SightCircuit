"""Exercise the real bridge protocol without claiming hardware rendering."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest

from framejacker.overlay import DEFAULT_BRIDGE, OverlayClient, PanelSettings, packet


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Build scripts/build_overlay_bridge.sh first")
class OverlayBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(self.temporary.name) / "absent.vrpath"))

    def run_bridge(self, *arguments, data=b""):
        result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], input=data,
                                env=self.env, capture_output=True, timeout=5)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_valid_frames_settings_and_quit_without_openvr(self):
        data = PanelSettings(visible=False).encode() + packet(1, struct.pack("<II", 2, 1) + bytes(8)) + packet(3)
        result, messages = self.run_bridge("--validate-stdin", data=data)
        self.assertEqual(result.returncode, 0, messages)
        self.assertTrue(messages[0]["validation_only"])
        final = messages[-1]
        self.assertEqual(final["status"], "validated")
        self.assertEqual(final["frames_received"], 1)
        self.assertEqual(final["settings_received"], 1)
        self.assertEqual(final["frames_submitted"], 0)
        self.assertFalse(final["runtime_initialized"])
        self.assertTrue(final["cleanup_ok"])

    def test_malformed_packets(self):
        for data in (b"short", struct.pack("<4sII", b"BAD!", 3, 0),
                     struct.pack("<4sII", b"FJO1", 1, 2**31),
                     packet(4), packet(3, b"extra"), packet(2, bytes(4)),
                     packet(1, struct.pack("<II", 0, 1)),
                     packet(1, struct.pack("<II", 513, 1) + bytes(513 * 4)),
                     packet(1, struct.pack("<II", 1, 1) + bytes(3)),
                     packet(2, struct.pack("<5fI", float("nan"), 1.5, 0, 0, 1, 1)),
                     packet(2, struct.pack("<5fI", .8, 1.5, 0, 0, 1, 2))):
            with self.subTest(data=data[:12]):
                result, messages = self.run_bridge("--validate-stdin", data=data)
                self.assertEqual(result.returncode, 1, messages)
                self.assertEqual(messages[-1]["status"], "error")
                self.assertTrue(messages[-1]["cleanup_ok"])
                self.assertFalse(messages[-1]["runtime_initialized"])

    def test_default_and_invalid_arguments_never_initialize_runtime(self):
        for args, expected in (([], 3), (["--unknown"], 64),
                               (["--test-panel", "--seconds", "0"], 64)):
            with self.subTest(args=args):
                result, messages = self.run_bridge(*args)
                self.assertEqual(result.returncode, expected)
                self.assertFalse(messages[-1]["runtime_initialized"])

    def test_missing_runtime_is_named(self):
        result, messages = self.run_bridge("--test-panel", "--seconds", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("VRInitError_Init_PathRegistryNotFound", messages[-1]["error"])
        self.assertEqual(messages[-1]["frames_submitted"], 0)

    def test_sigterm_interrupts_wait_for_input(self):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
            process.terminate()
            process.wait(timeout=3)  # Keep stdin open to test the signal, rather than EOF.
            output, _ = process.communicate(timeout=3)
            final = json.loads(output.splitlines()[-1])
            self.assertEqual(process.returncode, 0)
            self.assertEqual(final["status"], "stopped")
            self.assertTrue(final["cleanup_ok"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_partial_payload_times_out(self):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
            process.stdin.write(struct.pack("<4sII", b"FJO1", 2, 24) + bytes(4))
            process.stdin.flush()
            process.wait(timeout=5)
            output, _ = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 1)
            self.assertIn("stdin_timeout", json.loads(output.splitlines()[-1])["error"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_client_streams_and_closes_cleanly(self):
        client = OverlayClient(validation_only=True, env=self.env).start()
        try:
            client.set_settings(PanelSettings(width=1.2, visible=False))
            client.submit_frame(2, 1, bytes([255, 0, 0, 255]) * 2)
            deadline = time.monotonic() + 3
            while client.frames_sent == 0 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertGreater(client.frames_sent, 0)
        finally:
            summary = client.close()
        self.assertIsNone(summary["error"], summary)
        self.assertEqual(summary["native_report"]["status"], "validated")
        self.assertEqual(summary["native_report"]["frames_received"], 1)
        self.assertFalse(summary["native_report"]["runtime_initialized"])
        self.assertFalse(summary["forced_termination"])

    def test_client_startup_failure_is_actionable(self):
        client = OverlayClient(env=self.env)
        with self.assertRaisesRegex(RuntimeError, "VRInitError_Init_PathRegistryNotFound"):
            client.start()
        self.assertIsNotNone(client.process.poll())


class PanelSettingsTests(unittest.TestCase):
    def test_invalid_settings_are_rejected_before_sending(self):
        for settings in (PanelSettings(width=100), PanelSettings(opacity=float("nan")),
                         PanelSettings(distance=0), PanelSettings(visible="false")):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    settings.encode()


if __name__ == "__main__":
    unittest.main()
