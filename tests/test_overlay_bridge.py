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

from sightcircuit.hud.contract import VISIBILITY_EVENT_BOUND_NS, VISIBILITY_LINES
from sightcircuit.overlay import (DEFAULT_BRIDGE, MAX_SIDE, PANEL_LIMITS, OverlayClient, PanelSettings, packet,
                                  visibility_packet)
from sightcircuit.runtime.manifest import NATIVE_SOURCES

ROOT = Path(__file__).resolve().parents[1]
# The bridge's rendering unit decodes the settings packet and applies the panel limits.
RENDERING_UNIT = ROOT / "tools/overlay_bridge_rendering.cpp"


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

    def test_validation_runs_without_a_stderr(self):
        # Launched with fd 2 closed (2>&-), the bridge sends SDK chatter nowhere instead of refusing.
        data = PanelSettings(visible=False).encode() + packet(1, struct.pack("<II", 2, 1) + bytes(8)) + packet(3)
        result = subprocess.run([str(DEFAULT_BRIDGE), "--validate-stdin"], input=data, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=lambda: os.close(2), timeout=5)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 0, messages)
        self.assertEqual((messages[-1]["status"], messages[-1]["frames_received"]), ("validated", 1))
        self.assertTrue(messages[-1]["cleanup_ok"])

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
        self.assertEqual(messages[-1]["error"], "vrserver_not_running:VRInitError_Init_PathRegistryNotFound")
        self.assertEqual(messages[-1]["frames_submitted"], 0)

    RUNTIME_MODES = (("--test-panel",), ("--test-panel", "--seconds", "1"), ("--stdin",),
                     ("--calibration-marker", "--seconds", "3", "--allow-calibration-output"))

    def fake_installed_runtime(self):
        """A path registry naming a runtime directory without SteamVR in it."""
        root = Path(self.temporary.name)
        for name in ("runtime", "config", "log"):
            (root / name).mkdir()
        registry = root / "openvrpaths.vrpath"
        registry.write_text(json.dumps({"runtime": [str(root / "runtime")], "config": [str(root / "config")],
                                        "log": [str(root / "log")], "external_drivers": None,
                                        "jsonid": "vrpathreg", "version": 1}))
        return registry

    def test_every_runtime_mode_refuses_without_a_vrserver(self):
        # stdin stays open, so a mode that waited for input or a server would hang here.
        for registry, symbol in ((None, "VRInitError_Init_PathRegistryNotFound"),
                                 (self.fake_installed_runtime(), None)):
            env = self.env if registry is None else dict(self.env, VR_PATHREG_OVERRIDE=str(registry))
            for arguments in self.RUNTIME_MODES:
                with self.subTest(arguments=arguments, registry=registry):
                    started = time.monotonic()
                    process = subprocess.Popen([str(DEFAULT_BRIDGE), *arguments], stdin=subprocess.PIPE,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                    try:
                        output, _ = process.communicate(timeout=3)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=3)
                    self.assertLess(time.monotonic() - started, 3)
                    lines = output.splitlines()
                    self.assertEqual(len(lines), 1, output)  # exactly one parseable report
                    report = json.loads(lines[0])
                    self.assertEqual(process.returncode, 1)
                    self.assertEqual((report["event"], report["status"], report["exit_code"]), ("error", "error", 1))
                    connect = report["connect"]
                    if symbol is None:  # The loader's exact symbol for a missing vrclient varies by layout.
                        symbol = connect["background_probe_result"]
                        self.assertRegex(symbol, r"^VRInitError_Init_")
                        self.assertNotIn(symbol, ("VRInitError_Init_PathRegistryNotFound", "VRInitError_None"))
                    self.assertEqual(report["error"], "vrserver_not_running:" + symbol)
                    self.assertEqual(connect["sequence"], "background_probe_then_overlay")
                    self.assertEqual((connect["background_probes"], connect["background_probe_result"]), (1, symbol))
                    self.assertIs(connect["vrserver_running"], False)
                    self.assertIs(connect["overlay_connect_attempted"], False)
                    self.assertEqual((connect["overlay_connect_attempts"], connect["overlay_connect_errors"]), (0, []))
                    self.assertFalse(report["runtime_initialized"])
                    self.assertFalse(report["overlay_retirement_attempted"])
                    self.assertFalse(report["runtime_shutdown_returned"])
                    self.assertTrue(report["cleanup_ok"])
                    self.assertTrue(report["watchdog_armed_for_cleanup"])
                    self.assertEqual(report["frames_submitted"], 0)

    def test_connect_as_background_is_an_opt_in_for_runtime_modes_only(self):
        for arguments in self.RUNTIME_MODES:
            with self.subTest(arguments=arguments):
                result, messages = self.run_bridge(*arguments, "--connect-as", "background")
                self.assertEqual((result.returncode, len(messages)), (1, 1), messages)
                connect = messages[0]["connect"]
                self.assertEqual(connect["sequence"], "background_only_opt_in")
                self.assertEqual(connect["application_type"], "VRApplication_Background")
                self.assertIs(connect["background_only_requested"], True)
                self.assertIs(connect["overlay_connect_attempted"], False)
                self.assertTrue(messages[0]["error"].startswith("vrserver_not_running:"))
        result, messages = self.run_bridge("--stdin", "--connect-as", "overlay")
        self.assertEqual(messages[-1]["connect"]["sequence"], "background_probe_then_overlay")
        self.assertIs(messages[-1]["connect"]["background_only_requested"], False)
        for arguments in (("--validate-stdin", "--connect-as", "background"),
                          ("--validate-calibration-marker", "--seconds", "1", "--connect-as", "background"),
                          ("--headset-view-record", "--allow-runtime-query", "--connect-as", "background"),
                          ("--stdin", "--connect-as", "sideways"), ("--stdin", "--connect-as"),
                          ("--connect-as", "background")):
            with self.subTest(arguments=arguments):
                result, messages = self.run_bridge(*arguments)
                self.assertEqual(result.returncode, 64, messages)
                self.assertFalse(messages[-1]["runtime_initialized"])

    def test_headset_view_record_is_opt_in_background_only_and_never_sets(self):
        result, messages = self.run_bridge("--headset-view-record")
        self.assertEqual((result.returncode, len(messages)), (64, 1))
        self.assertEqual(messages[0]["error"], "view_record_requires_explicit_opt_in")
        self.assertFalse(messages[0]["runtime_initialized"])
        for registry in (None, self.fake_installed_runtime()):
            env = self.env if registry is None else dict(self.env, VR_PATHREG_OVERRIDE=str(registry))
            with self.subTest(registry=registry):
                result = subprocess.run([str(DEFAULT_BRIDGE), "--headset-view-record", "--allow-runtime-query"],
                                        env=env, capture_output=True, timeout=3)
                lines = result.stdout.splitlines()
                self.assertEqual((result.returncode, len(lines)), (1, 1), result.stdout)
                report = json.loads(lines[0])
                self.assertTrue(report["error"].startswith("vrserver_not_running:VRInitError_Init_"))
                self.assertEqual(report["connect"]["sequence"], "background_only")
                self.assertIs(report["connect"]["overlay_connect_attempted"], False)
                self.assertIsNone(report["view_record"])
                self.assertIs(report["set_calls_made"], False)
                self.assertFalse(report["runtime_initialized"])
        for path in NATIVE_SOURCES:  # GetHeadsetView* only, in every bridge unit; the mirror's mode is never changed
            with self.subTest(path=path):
                self.assertNotIn("SetHeadsetView", (ROOT / path).read_text())

    def test_validation_modes_stay_runtime_free_with_protocol_on_stdout(self):
        data = packet(1, struct.pack("<II", 1, 1) + bytes(4)) + packet(3)
        for arguments, payload in ((("--validate-stdin",), data),
                                   (("--validate-calibration-marker", "--seconds", "1"), b"")):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], input=payload, env=self.env,
                                        capture_output=True, timeout=5)
                messages = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(result.returncode, 0, messages)
                self.assertEqual(messages[0]["event"], "ready")
                self.assertNotIn("connect", messages[-1])
                self.assertFalse(messages[-1]["runtime_initialized"])
                self.assertFalse(messages[-1]["runtime_shutdown_returned"])
        result = subprocess.run([str(DEFAULT_BRIDGE), "--help"], env=self.env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"never starts a vrserver", result.stdout)

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


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Build scripts/build_overlay_bridge.sh first")
class VisibilityLineTests(unittest.TestCase):
    """The fallback's visibility lines (work item F1) on the validation bridge: software timing, no runtime.

    Without a runtime no overlay event can arrive, so every hide ends in
    ``visibility_event_missing``: the fail-closed path is what these tests
    exercise. ``overlay_hidden`` and ``overlay_shown`` need a device run.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(self.temporary.name) / "absent.vrpath"))

    def run_steps(self, steps):
        """Write each payload, wait the given seconds, then close stdin; returns the parsed lines and the exit code."""
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        try:
            for data, wait in steps:
                process.stdin.write(data)
                process.stdin.flush()
                time.sleep(wait)
            output, _ = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        return [json.loads(line) for line in output.splitlines()], process.returncode

    @staticmethod
    def visibility_lines(messages):
        return [message for message in messages if message["event"] in VISIBILITY_LINES]

    def test_a_hide_without_its_event_fails_closed_after_the_bound_and_a_show_does_not(self):
        bound_seconds = VISIBILITY_EVENT_BOUND_NS / 1e9
        frame = packet(1, struct.pack("<II", 2, 1) + bytes(8))
        messages, code = self.run_steps([(frame, 0), (visibility_packet(7, False), bound_seconds + 0.2),
                                         (visibility_packet(8, True), bound_seconds + 0.2), (packet(3), 0)])
        self.assertEqual(code, 0, messages)
        lines = self.visibility_lines(messages)
        self.assertEqual([(line["event"], line["cycle"]) for line in lines],
                         [("hide_requested", 7), ("hide_returned", 7), ("visibility_event_missing", 7),
                          ("show_requested", 8), ("show_returned", 8)])
        requested, returned, missing, show_requested, show_returned = lines
        self.assertEqual(set(requested), {"event", "cycle", "request_ns", "validation_only", "report_kind", "schema_version"})
        self.assertEqual(set(returned), {"event", "cycle", "returned_ns", "ok", "validation_only", "report_kind", "schema_version"})
        self.assertEqual(set(missing), {"event", "cycle", "waited_ns", "validation_only", "report_kind", "schema_version"})
        self.assertTrue(all(line["validation_only"] is True for line in lines))
        self.assertIs(returned["ok"], True)
        self.assertIs(show_returned["ok"], True)
        self.assertLessEqual(requested["request_ns"], returned["returned_ns"])
        self.assertLessEqual(returned["returned_ns"], show_requested["request_ns"])
        self.assertGreaterEqual(missing["waited_ns"], VISIBILITY_EVENT_BOUND_NS)
        self.assertLess(missing["waited_ns"], VISIBILITY_EVENT_BOUND_NS + 1_000_000_000)
        # The bound is polled, not slept: the stdin loop went on reading, and the show after it carried the
        # tile's presentation state while the hide had switched it off.
        self.assertEqual(messages.index(missing), messages.index(show_requested) - 1)
        final = messages[-1]
        self.assertEqual((final["status"], final["frames_received"], final["visibility_requests_received"],
                          final["visibility_events_missing"]), ("validated", 1, 2, 1))
        self.assertIs(final["presentation_visible"], True)  # shown again, the frame still current
        self.assertNotIn("overlay_hidden", [line["event"] for line in lines])
        self.assertNotIn("overlay_shown", [line["event"] for line in lines])

    def test_a_hide_leaves_the_tile_off_in_the_report(self):
        frame = packet(1, struct.pack("<II", 2, 1) + bytes(8))
        messages, code = self.run_steps([(frame + visibility_packet(3, False) + packet(3), 0)])
        self.assertEqual(code, 0, messages)
        final = messages[-1]
        self.assertIs(final["content_current"], True)
        self.assertIs(final["presentation_visible"], False)
        # Input ended before the bound: the pending hide is still reported missing, fail closed.
        self.assertEqual([line["event"] for line in self.visibility_lines(messages)],
                         ["hide_requested", "hide_returned", "visibility_event_missing"])
        self.assertLess(self.visibility_lines(messages)[-1]["waited_ns"], VISIBILITY_EVENT_BOUND_NS)
        self.assertEqual(final["visibility_events_missing"], 1)

    def test_a_new_request_settles_a_pending_hide_before_its_own_lines_and_a_pending_show_silently(self):
        messages, code = self.run_steps([(visibility_packet(1, False), 0.05), (visibility_packet(2, False), 0.05),
                                         (visibility_packet(3, True), 0.05), (visibility_packet(4, False), 0.05),
                                         (packet(3), 0)])
        self.assertEqual(code, 0, messages)
        self.assertEqual([(line["event"], line["cycle"]) for line in self.visibility_lines(messages)],
                         [("hide_requested", 1), ("hide_returned", 1), ("visibility_event_missing", 1),
                          ("hide_requested", 2), ("hide_returned", 2), ("visibility_event_missing", 2),
                          ("show_requested", 3), ("show_returned", 3),
                          ("hide_requested", 4), ("hide_returned", 4), ("visibility_event_missing", 4)])
        missing = [line for line in self.visibility_lines(messages) if line["event"] == "visibility_event_missing"]
        self.assertTrue(all(line["waited_ns"] < VISIBILITY_EVENT_BOUND_NS for line in missing))
        self.assertEqual((messages[-1]["visibility_requests_received"], messages[-1]["visibility_events_missing"]), (4, 3))

    def test_marker_mode_and_the_settings_packet_are_unchanged(self):
        result = subprocess.run([str(DEFAULT_BRIDGE), "--validate-calibration-marker", "--seconds", "1"],
                                env=self.env, capture_output=True, timeout=5)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 0, messages)
        self.assertEqual([message["event"] for message in messages], ["ready", "closed"])
        self.assertNotIn("visibility_requests_received", messages[-1])
        self.assertNotIn("visibility_events_missing", messages[-1])
        self.assertIn("overlay_hidden_event_seen", messages[-1])
        # A visibility change through the settings packet is the pre-F1 path: no cycle, no lines.
        data = PanelSettings(visible=False).encode() + PanelSettings(visible=True).encode() + packet(3)
        result = subprocess.run([str(DEFAULT_BRIDGE), "--validate-stdin"], input=data, env=self.env,
                                capture_output=True, timeout=5)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 0, messages)
        self.assertEqual(self.visibility_lines(messages), [])
        self.assertEqual((messages[-1]["settings_received"], messages[-1]["visibility_requests_received"],
                          messages[-1]["visibility_events_missing"]), (2, 0, 0))


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

        tools = Path(__file__).resolve().parents[1] / "tools"
        # The bridge applies the generated header's values, never its own literals.
        header = (tools / "hud_contract.hpp").read_text()
        values = {name: float(value) for name, value in
                  re.findall(r"constexpr float (kPanel\w+) = (-?[\d.]+)f;", header)}
        source = RENDERING_UNIT.read_text()
        native = {name: (values[low], values[high]) for name, low, high in
                  re.findall(r"bounded\(s\.(\w+),\s*contract::(kPanel\w+Min),\s*contract::(kPanel\w+Max)\)", source)}
        native["opacity"] = native.pop("alpha")
        self.assertEqual({name: (float(low), float(high)) for name, low, high in PANEL_LIMITS}, native)
        self.assertEqual(MAX_SIDE, int(re.search(r"kMaxSide = (\d+)", header).group(1)))

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
        from sightcircuit.overlay import OverlaySink
        from scripts import live_pipeline

        self.assertIs(live_pipeline.OverlaySink, OverlaySink)
        self.assertTrue(hasattr(OverlayClient, "freeze_settings"))

    def test_caller_owned_client_keeps_its_placement_visibility_and_close(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from sightcircuit.hud import HUD_CONFIG
        from sightcircuit.overlay import OverlaySink

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
        from sightcircuit.hud import HUD_CONFIG, HudOutput
        from sightcircuit.overlay import OverlaySink

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
        # A failed transport is reported before a payload mismatch, and nothing more is sent.
        sink.client.error = "Overlay bridge disconnected without a final cleanup report"
        with self.assertRaisesRegex(RuntimeError, "Overlay bridge disconnected"):
            sink.submit(SimpleNamespace(hud=None, image=tile, expires_ns=1))
        self.assertEqual((sink.client.sent, sink.client.settings.visible), ([tile], False))
        # A changed contract is reported before the transport error.
        sink.client.validation_only = False
        with self.assertRaisesRegex(ValueError, "hud_sink_mode_or_placement_changed"):
            sink.submit(SimpleNamespace(hud=None, image=tile, expires_ns=1))
        self.assertEqual(sink.client.sent, [tile])

    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required for fake native dispatch")
    def test_fake_dispatch_decodes_pixels_deadlines_and_cleanup_failures(self):
        fixture = Path(__file__).resolve().parent / "fixtures/overlay_dispatch_fixture.cpp"
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "overlay-dispatch"
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                            str(fixture), "-o", str(binary)], check=True, capture_output=True, timeout=20)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=3)


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required for the lifecycle fixture")
class OverlayLifecycleFixtureTests(unittest.TestCase):
    """The bridge's isolation, watchdog and connect helpers, driven by scripted fakes."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixture = Path(__file__).resolve().parent / "fixtures/overlay_lifecycle_fixture.cpp"
        cls.binary = Path(cls.temporary.name) / "overlay-lifecycle"
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                        str(fixture), "-o", str(cls.binary)], check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_fixture(self, mode, timeout=5):
        started = time.monotonic()
        result = subprocess.run([str(self.binary), mode], capture_output=True, timeout=timeout)
        return result, time.monotonic() - started

    def test_connect_order_retry_bound_and_refusal(self):
        result, _ = self.run_fixture("connect")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"connect checks passed", result.stdout)

    def test_sdk_chatter_goes_to_stderr_and_protocol_to_real_stdout(self):
        result, _ = self.run_fixture("isolate")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'{"event":"protocol"}\n')
        self.assertIn(b"sdk chatter on fd 1", result.stderr)
        self.assertIn(b"raw chatter", result.stderr)

    def test_watchdog_writes_one_json_error_and_exits(self):
        for mode, torn in (("watchdog", False), ("watchdog-torn", True)):
            with self.subTest(mode=mode):
                result, elapsed = self.run_fixture(mode)
                self.assertEqual(result.returncode, 124)
                self.assertLess(elapsed, 3)
                lines = result.stdout.splitlines()
                self.assertEqual(json.loads(lines[-1]), {"event": "error", "error": "watchdog_expired:run"})
                self.assertEqual(len(lines), 2 if torn else 1)
                if torn:
                    with self.assertRaises(json.JSONDecodeError):
                        json.loads(lines[0])

    def test_rearmed_watchdog_lets_a_healthy_loop_finish(self):
        result, elapsed = self.run_fixture("watchdog-rearm")
        self.assertEqual(result.returncode, 0)
        self.assertGreater(elapsed, 1.2)
        self.assertEqual(result.stdout, b'{"event":"closed"}\n')

    def test_watchdog_rejects_oversized_or_zero_budgets(self):
        result, _ = self.run_fixture("watchdog-oversize")
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
