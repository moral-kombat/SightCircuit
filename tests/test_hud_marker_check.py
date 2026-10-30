"""Marker-only expiry/control tests: generated pixels, no OpenVR session."""
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from scripts import hud_marker_check as marker
from scripts.hud_mask import HUD_CONFIG
from scripts.overlay_client import DEFAULT_BRIDGE, OverlayClient

ROOT = Path(__file__).resolve().parents[1]


class MarkerGateTests(unittest.TestCase):
    def test_policy_failure_retains_native_evidence_and_truthful_resource_cleanup(self):
        native = {"mode": "--validate-calibration-marker", "status": "validated_marker", "exit_code": 0,
                  "cleanup_ok": True, "marker": {"visible_in_dashboard": False}}
        bridge = {"worker_reaped": True, "worker_exit_code": 0, "transport_threads_stopped": True,
                  "forced_termination": False, "error": None, "native_report": native}
        client = Mock(mode="--validate-calibration-marker")
        client.process.poll.return_value = 0
        client.close.return_value = bridge
        with patch.object(marker, "MarkerClient", return_value=client), patch.object(marker.signal, "signal"), \
                contextlib.redirect_stderr(io.StringIO()):
            report = marker.worker({"seconds": 1, "validation_only": True})
        self.assertEqual(report["status"], "native_failed")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["reason"], "invalid_marker_visibility_evidence")
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["bridge"]["native_report"], native)

    def test_validation_never_claims_runtime_visibility(self):
        native = {"marker": {"visible_in_dashboard": True}, "overlay_shown_event_seen": False,
                  "overlay_hidden_event_seen": False,
                  "visibility_after_submit": {"scope": "runtime_queries_not_physical_visibility", "sampled": False,
                                               "api_visible": None, "dashboard_visible": None, "visible_in_dashboard": None}}
        marker.validate_visibility_evidence(native, True)
        for key in ("api_visible", "dashboard_visible", "visible_in_dashboard"):
            changed = json.loads(json.dumps(native))
            changed["visibility_after_submit"][key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                marker.validate_visibility_evidence(changed, True)
        native["overlay_shown_event_seen"] = True
        with self.assertRaises(ValueError):
            marker.validate_visibility_evidence(native, True)

    def test_real_visibility_policy_is_required_but_api_visibility_is_not_physical_proof(self):
        native = {"marker": {"visible_in_dashboard": True}, "overlay_shown_event_seen": True,
                  "overlay_hidden_event_seen": False,
                  "visibility_after_submit": {"scope": "runtime_queries_not_physical_visibility", "sampled": True,
                                               "api_visible": False, "dashboard_visible": True, "visible_in_dashboard": True}}
        marker.validate_visibility_evidence(native, False)
        for change in (lambda n: n["marker"].update(visible_in_dashboard=False),
                       lambda n: n["visibility_after_submit"].update(visible_in_dashboard=False),
                       lambda n: n["visibility_after_submit"].update(sampled=False),
                       lambda n: n["visibility_after_submit"].update(api_visible=None)):
            changed = json.loads(json.dumps(native))
            change(changed)
            with self.assertRaises(ValueError):
                marker.validate_visibility_evidence(changed, False)

    def test_default_and_incomplete_opt_ins_never_launch(self):
        with patch.object(marker, "supervise") as supervise, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(marker.main([]), 3)
        supervise.assert_not_called()
        for args in (["--calibrate"], ["--allow-calibration-output"],
                     ["--validate", "--allow-calibration-output"], ["--validate", "--seconds", "21"]):
            with self.subTest(args=args), patch.object(marker, "supervise") as supervise, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                marker.main(args)
            supervise.assert_not_called()

    def test_maximum_budget_and_explicit_real_mode_forwarding_without_execution(self):
        for args, validation in ((["--validate"], True), (["--calibrate", "--allow-calibration-output"], False)):
            with self.subTest(args=args), patch.object(marker, "supervise", return_value=marker.new_report()) as supervised, \
                    contextlib.redirect_stdout(io.StringIO()):
                marker.main([*args, "--seconds", "20"])
            call = supervised.call_args
            options = json.loads(call.args[0][-1])
            self.assertEqual(call.args[2], 32)
            self.assertEqual(options["validation_only"], validation)
            self.assertEqual(options["allow_calibration_output"], not validation)
            self.assertTrue(call.kwargs["descendants"])

    def test_client_requires_separate_real_opt_in_and_never_starts_image_writer(self):
        client = marker.MarkerClient(validation_only=False)
        with patch.object(marker.subprocess, "Popen") as popen, self.assertRaises(ValueError):
            client.start_marker(1, threading.Event())
        popen.assert_not_called()
        client.ready.set()
        client.acknowledged = True
        with patch.object(Path, "is_file", return_value=True), patch.object(marker.os, "access", return_value=True), \
                patch.object(marker.subprocess, "Popen") as popen, patch.object(marker.threading, "Thread"):
            client.start_marker(20, threading.Event(), allow_calibration_output=True)
        self.assertEqual(popen.call_args.args[0][1:],
                         ["--calibration-marker", "--seconds", "20", "--allow-calibration-output"])
        self.assertIsNone(client.writer)

    def test_production_hud_gate_remains_closed(self):
        client = OverlayClient(validation_only=False)
        with self.assertRaises(ValueError):
            client.freeze_settings(client.settings)
        self.assertIsNone(client.process)


@unittest.skipUnless(sys.platform.startswith("linux") and DEFAULT_BRIDGE.is_file(), "Linux native bridge required")
class MarkerNativeTests(unittest.TestCase):
    def launch(self, *arguments):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), *arguments], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.close_process, process)
        events = queue.Queue()
        def read():
            for line in process.stdout:
                events.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        return process, events

    def close_process(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def final(self, process, events):
        while True:
            report = events.get(timeout=5)
            if "exit_code" in report:
                break
        self.assertEqual(process.wait(timeout=3), report["exit_code"])
        return report

    def test_native_generated_marker_deadline_bounds_opacity_and_config(self):
        process, events = self.launch("--validate-calibration-marker", "--seconds", "1")
        ready = events.get(timeout=3)
        self.assertTrue(ready["validation_only"])
        self.assertFalse(ready["runtime_initialized"])
        # Arbitrary input is ignored by this mode, never accepted as an image.
        process.stdin.write(b"not a frame or settings packet")
        process.stdin.flush()
        report = self.final(process, events)
        self.assertEqual(report["status"], "validated_marker")
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["display_deadline_ns"] - report["native_started_ns"], 1000000000)
        self.assertLess(report["marker_submit_returned_ns"], report["display_deadline_ns"])
        self.assertGreaterEqual(report["cutoff_requested_ns"], report["display_deadline_ns"])
        self.assertLess(report["software_deadline_overshoot_ms"], 750)
        self.assertGreaterEqual(report["cleanup_returned_ns"], report["cutoff_requested_ns"])
        generated = report["marker"]
        self.assertEqual((generated["width"], generated["height"]), (320, 128))
        self.assertEqual(generated["opaque_pixels"], 320 * 128)
        self.assertEqual(generated["corner_ids"], [1, 2, 3, 4])
        for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"):
            self.assertAlmostEqual(generated[key], getattr(HUD_CONFIG, key))
        self.assertEqual(generated["config_id"], HUD_CONFIG.layout_id)
        self.assertTrue(generated["visible_in_dashboard"])
        marker.validate_visibility_evidence(report, True)
        self.assertFalse(report["visibility_after_submit"]["sampled"])
        self.assertIsNone(report["visibility_after_submit"]["api_visible"])
        self.assertFalse(report["physical_display_recovery_verified"])
        self.assertFalse(report["input_frames_accepted"])
        self.assertEqual(report["frames_submitted"], 0)
        self.assertEqual(report["generated_marker_frames"], 1)

    def test_native_signal_abort_at_twenty_second_configuration(self):
        process, events = self.launch("--validate-calibration-marker", "--seconds", "20")
        self.assertEqual(events.get(timeout=3)["event"], "ready")
        process.send_signal(signal.SIGTERM)
        report = self.final(process, events)
        self.assertEqual(report["status"], "stopped")
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["physical_display_recovery_verified"])
        self.assertLess(report["cutoff_requested_ns"], report["display_deadline_ns"])

    def test_native_rejects_unapproved_or_invalid_modes_before_vr(self):
        for args in (("--calibration-marker", "--seconds", "1"),
                     ("--calibration-marker", "--seconds", "21", "--allow-calibration-output"),
                     ("--validate-calibration-marker", "--seconds", "0"),
                     ("--stdin", "--allow-calibration-output")):
            with self.subTest(args=args):
                process, events = self.launch(*args)
                report = self.final(process, events)
                self.assertEqual(report["exit_code"], 64)
                self.assertFalse(report["runtime_initialized"])

    def test_controller_guardian_success_and_report_contract(self):
        result = subprocess.run([sys.executable, "-m", "scripts.hud_marker_check", "--validate", "--seconds", "1"],
            cwd=ROOT, capture_output=True, timeout=20)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report["cleanup_ok"])
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["worker_tree_reaped"])
        self.assertTrue(report["guardian_reaped"])
        self.assertFalse(report["capture_started"])
        self.assertFalse(report["inference_started"])
        marker.validate_worker_report(report, 0, validation_only=True)
        for key in ("production_hud_enabled", "capture_started", "physical_display_recovery_verified", "mapping_qualified"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                marker.validate_worker_report(dict(report, **{key: True}), 0, validation_only=True)
        late = json.loads(json.dumps(report))
        native = late["bridge"]["native_report"]
        native["marker_submit_returned_ns"] = native["display_deadline_ns"] + 1
        with self.assertRaises(ValueError):
            marker.validate_worker_report(late, 0, validation_only=True)
        with self.assertRaises(ValueError):
            marker.validate_worker_report(report, 0, validation_only=False)
        missing = dict(report)
        missing.pop("seconds_requested")
        with self.assertRaises(ValueError):
            marker.validate_worker_report(missing, 0, validation_only=True)
        for field, value in (("worker_reaped", False), ("forced_termination", True), ("worker_exit_code", 7)):
            changed = json.loads(json.dumps(report))
            changed["bridge"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                marker.validate_worker_report(changed, 0, validation_only=True)

    def test_controller_cancellation_reports_api_cleanup_separately_from_recovery(self):
        process = subprocess.Popen([sys.executable, "-m", "scripts.hud_marker_check", "--validate", "--seconds", "20"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(process.stderr, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=8))
                self.assertIn(b"CALIBRATION MARKER READY", process.stderr.readline())
            process.send_signal(signal.SIGINT)
            output, _ = process.communicate(timeout=15)
            report = json.loads(output)
            self.assertEqual(report["status"], "cancelled")
            self.assertTrue(report["worker_reaped"])
            self.assertTrue(report["guardian_reaped"])
            self.assertFalse(report["physical_display_recovery_verified"])
            self.assertFalse(report["cleanup_ok"])
            self.assertTrue(report["bridge"]["forced_termination"])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_unresponsive_native_helper_is_reaped_without_cleanup_claim(self):
        # SIGSTOP deterministically models a native call that cannot return to
        # the event loop; it is not a simulation of driver/GPU recovery.
        code = """import json, os, signal
from scripts import hud_marker_check as marker
original = marker.MarkerClient.start_marker
def stopped(self, *args, **kwargs):
    result = original(self, *args, **kwargs)
    os.kill(self.process.pid, signal.SIGSTOP)
    return result
marker.MarkerClient.start_marker = stopped
print(json.dumps(marker.worker({'seconds':1, 'validation_only':True})))
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, timeout=15)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "timed_out")
        self.assertFalse(report["cleanup_ok"])
        self.assertTrue(report["bridge"]["forced_termination"])
        self.assertTrue(report["bridge"]["worker_reaped"])
        self.assertFalse(report["physical_display_recovery_verified"])
