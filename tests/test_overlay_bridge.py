"""Exercise the real bridge protocol without claiming hardware rendering."""
import json
import os
from pathlib import Path
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from framejacker.overlay import DEFAULT_BRIDGE, MAX_SIDE, PANEL_LIMITS, OverlayClient, PanelSettings, packet


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


@unittest.skipUnless(os.name == "posix", "Fake bridge uses POSIX shebangs and signals")
class OverlayCloseTests(unittest.TestCase):
    BODIES = {
        "cooperative": "sys.stdin.buffer.read()\nprint(json.dumps({'exit_code': 0, 'cleanup_ok': True}), flush=True)",
        "terminable": "time.sleep(60)",
        "ignores_term": "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)",
    }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def open_fake_bridge(self, behavior):
        path = Path(self.temporary.name) / behavior
        path.write_text("#!" + sys.executable + "\n"
                        "import json, signal, sys, time\n"
                        "sys.stdout.write(json.dumps({'event': 'ready', 'validation_only': False, "
                        "'runtime_initialized': True}) + '\\n')\n"
                        "sys.stdout.flush()\n" + self.BODIES[behavior] + "\n")
        path.chmod(0o755)
        return OverlayClient(binary=path).start()

    def close_fake_bridge(self, behavior):
        client = self.open_fake_bridge(behavior)
        return client, client.close()

    def test_cooperative_exit_is_confirmed_without_forced_termination(self):
        client, summary = self.close_fake_bridge("cooperative")
        self.assertIsNone(summary["error"], summary)
        self.assertFalse(summary["forced_termination"])
        self.assertTrue(summary["cleanup_confirmed"])
        self.assertEqual(client.process.returncode, 0)

    def test_terminate_escalation_is_reported_as_forced(self):
        client, summary = self.close_fake_bridge("terminable")
        self.assertTrue(summary["forced_termination"], summary)
        self.assertFalse(summary["cleanup_confirmed"])
        self.assertIsNotNone(summary["error"])
        self.assertEqual(client.process.returncode, -signal.SIGTERM)

    def test_kill_escalation_is_reported_as_forced(self):
        client, summary = self.close_fake_bridge("ignores_term")
        self.assertTrue(summary["forced_termination"], summary)
        self.assertFalse(summary["cleanup_confirmed"])
        self.assertEqual(client.process.returncode, -signal.SIGKILL)

    def test_full_pipe_write_has_its_own_deadline(self):
        client = self.open_fake_bridge("terminable")
        try:
            started = time.monotonic()
            client.submit_frame(MAX_SIDE, MAX_SIDE, bytes(MAX_SIDE * MAX_SIDE * 4))
            self.assertTrue(client.writer is not None)
            client.writer.join(timeout=3)
            self.assertFalse(client.writer.is_alive(), "Writer stalled on a full anonymous pipe")
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(client.error, "overlay_write_timeout")
        finally:
            report = client.close()
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["transport_threads_stopped"])
        self.assertFalse(report["cleanup_confirmed"])
        self.assertFalse(report["physical_display_recovery_verified"])


class PanelLimitConsistencyTests(unittest.TestCase):
    def test_python_limits_match_native_bridge(self):
        import re

        source = (Path(__file__).resolve().parents[1] / "tools/overlay_bridge.cpp").read_text()
        native = {name: (float(low), float(high)) for name, low, high in
                  re.findall(r"bounded\(s\.(\w+),\s*(-?[\d.]+)f?,\s*(-?[\d.]+)f?\)", source)}
        native["opacity"] = native.pop("alpha")
        self.assertEqual({name: (float(low), float(high)) for name, low, high in PANEL_LIMITS}, native)
        self.assertEqual(MAX_SIDE, int(re.search(r"kMaxSide = (\d+)", source).group(1)))

    def test_defaults_are_within_limits(self):
        defaults = PanelSettings()
        for name, low, high in PANEL_LIMITS:
            self.assertTrue(low <= getattr(defaults, name) <= high, name)


class PanelSettingsTests(unittest.TestCase):
    def test_invalid_settings_are_rejected_before_sending(self):
        for settings in (PanelSettings(width=100), PanelSettings(opacity=float("nan")),
                         PanelSettings(distance=0), PanelSettings(visible="false")):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    settings.encode()


class SharedOverlayContractTests(unittest.TestCase):
    def test_commands_use_the_shared_sink_and_frozen_settings(self):
        from framejacker.overlay import OverlaySink
        from scripts import live_pipeline

        self.assertIs(live_pipeline.OverlaySink, OverlaySink)
        self.assertTrue(hasattr(OverlayClient, "freeze_settings"))

    def test_caller_owned_client_keeps_its_placement_visibility_and_close(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from framejacker.hud import HUD_CONFIG
        from framejacker.overlay import OverlaySink

        client = Mock()
        sink = OverlaySink(client)
        sink.open()
        sink.submit(SimpleNamespace(image="tile", expires_ns=None))
        sink.submit(SimpleNamespace(image="tile", expires_ns=7))
        sink.clear()
        self.assertIs(sink.close(), True)
        self.assertEqual([call[0] for call in client.method_calls], ["submit_bgr", "submit_bgr"])
        self.assertEqual(client.submit_bgr.call_args_list[1].kwargs, {"expires_ns": 7})
        with self.assertRaisesRegex(ValueError, "hud_sink_requires_owned_validation_client"):
            OverlaySink(client, hud=HUD_CONFIG)

    def test_owned_hud_sink_freezes_placement_and_accepts_only_its_tiles(self):
        from types import SimpleNamespace
        from framejacker.hud import HUD_CONFIG, HudOutput
        from framejacker.overlay import OverlaySink

        class Client:
            def __init__(self, validation_only):
                self.validation_only, self.error, self.settings, self.sent = validation_only, None, None, []
            def start(self):
                return self
            def freeze_settings(self, settings):
                self.settings = settings
            def set_settings(self, settings):
                self.settings = settings
            def submit_bgr(self, image, *, expires_ns=None):
                self.sent.append(image)

        sink = OverlaySink(hud=HUD_CONFIG, client_factory=Client)
        sink.open()
        self.assertIs(sink.client.validation_only, True)
        self.assertEqual(sink.client.settings, sink.placement(False))
        tile = SimpleNamespace(shape=(HUD_CONFIG.height_px, HUD_CONFIG.width_px, 3))
        for bad in (SimpleNamespace(hud=None, image=tile, expires_ns=1),
                    SimpleNamespace(hud=HudOutput((), HUD_CONFIG), image=SimpleNamespace(shape=(1, 1, 3)),
                                    expires_ns=1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "hud_payload_contract_mismatch"):
                sink.submit(bad)
        self.assertEqual(sink.client.sent, [])
        sink.submit(SimpleNamespace(hud=HudOutput((), HUD_CONFIG), image=tile, expires_ns=1))
        self.assertEqual((sink.client.sent, sink.client.settings.visible), ([tile], True))
        sink.clear()
        self.assertEqual(sink.client.settings, sink.placement(False))

    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required for fake native dispatch")
    def test_fake_dispatch_decodes_pixels_deadlines_and_cleanup_failures(self):
        fixture = Path(__file__).resolve().parent / "fixtures/overlay_dispatch_fixture.cpp"
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "overlay-dispatch"
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                            str(fixture), "-o", str(binary)], check=True, capture_output=True, timeout=20)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=3)


if __name__ == "__main__":
    unittest.main()
