"""Independent presentation expiry using native validation, never a VR session."""
import importlib.util
import json
import os
import queue
import struct
import subprocess
import threading
import time
import unittest

from scripts import live_pipeline as live
from scripts.overlay_client import DEFAULT_BRIDGE, OverlayClient, PanelSettings, packet
from framejacker.overlay import OverlaySink


@unittest.skipUnless(os.name == "posix" and DEFAULT_BRIDGE.is_file(), "Build native validation bridge on Linux")
class DisplayExpiryTests(unittest.TestCase):
    def bridge(self):
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.close_process, process)
        events = queue.Queue()
        def read():
            for line in process.stdout:
                events.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        self.assertEqual(events.get(timeout=3)["event"], "ready")
        return process, events

    def close_process(self, process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def send(self, process, data):
        process.stdin.write(data)
        process.stdin.flush()

    def expiring_frame(self, deadline):
        return packet(4, struct.pack("<QII", deadline, 1, 1) + bytes(4))

    def event(self, events, wanted):
        end = time.monotonic() + 3
        while True:
            event = events.get(timeout=max(0.001, end - time.monotonic()))
            if event["event"] == wanted:
                return event
            self.assertNotEqual(event["event"], "error", event)

    def finish(self, process, events):
        self.send(process, packet(3))
        result = self.event(events, "closed")
        self.assertEqual(process.wait(timeout=3), 0)
        self.assertTrue(result["cleanup_ok"])
        self.assertFalse(result["runtime_initialized"])
        self.assertEqual(result["frames_submitted"], 0)
        return result

    def test_heartbeats_do_not_extend_deadline_or_resurrect_expired_content(self):
        process, events = self.bridge()
        deadline = time.monotonic_ns() + 300000000
        self.send(process, self.expiring_frame(deadline))
        self.event(events, "frame_validated")
        stop = threading.Event()
        def heartbeats():
            while not stop.wait(0.01):
                self.send(process, PanelSettings(visible=True).encode())
        writer = threading.Thread(target=heartbeats)
        writer.start()
        try:
            expired = self.event(events, "frame_expired")
        finally:
            stop.set()
            writer.join(timeout=1)
        self.assertEqual(expired["deadline_ns"], deadline)
        self.assertGreaterEqual(expired["observed_ns"], deadline)
        self.assertLess(expired["observed_ns"] - deadline, 750000000)
        self.send(process, PanelSettings(visible=True).encode())
        report = self.finish(process, events)
        self.assertGreater(report["settings_received"], 1)
        self.assertFalse(report["presentation_visible"])
        self.assertFalse(report["content_current"])
        self.assertEqual(report["expired_frames"], 1)

    def test_partial_next_packet_cannot_block_expiry(self):
        for split in (5, 17):  # Partial header and partial payload, respectively.
            with self.subTest(split=split):
                process, events = self.bridge()
                self.send(process, self.expiring_frame(time.monotonic_ns() + 200000000))
                self.event(events, "frame_validated")
                settings = PanelSettings().encode()
                self.send(process, settings[:split])
                self.event(events, "frame_expired")
                self.send(process, settings[split:])
                self.assertFalse(self.finish(process, events)["presentation_visible"])

    def test_new_frame_replaces_expiry_but_stale_delivery_does_not(self):
        process, events = self.bridge()
        now = time.monotonic_ns()
        self.send(process, self.expiring_frame(now + 150000000))
        self.event(events, "frame_validated")
        second = time.monotonic_ns() + 400000000
        self.send(process, self.expiring_frame(second))
        self.send(process, self.expiring_frame(now - 1))
        self.assertEqual(self.event(events, "frame_expired")["deadline_ns"], second)
        report = self.finish(process, events)
        self.assertEqual(report["frames_received"], 2)
        self.assertEqual(report["stale_frames_dropped"], 1)
        self.assertEqual(report["expired_frames"], 1)

    def test_invalid_deadline_is_rejected_without_vr(self):
        for expiry in (0, time.monotonic_ns() + 60000000000):
            process, events = self.bridge()
            self.send(process, self.expiring_frame(expiry))
            result = events.get(timeout=3)
            self.assertEqual(result["error"], "invalid_frame_expiry")
            self.assertFalse(result["runtime_initialized"])
            self.assertEqual(process.wait(timeout=3), 1)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV is required for the live pipeline detector")
    def test_stalled_inference_cannot_keep_previous_result_current(self):
        blocked, release, stop = threading.Event(), threading.Event(), threading.Event()
        class Detector(live.FixtureDetector):
            calls = 0
            def detect(self, *args):
                self.calls += 1
                if self.calls == 2:
                    blocked.set()
                    if not release.wait(5):
                        raise RuntimeError("fixture_release_timeout")
                return super().detect(*args)
        sink = live.OverlayValidationSink()
        result = {}
        def run():
            result.update(live.run_pipeline(live.SyntheticSource(), Detector(), sink, 5,
                stop=stop, max_fps=10, max_age_ms=500))
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(blocked.wait(4))
            self.assertTrue(sink.client.frame_expired.wait(3))
            self.assertTrue(worker.is_alive())
            self.assertFalse(sink.client.last_expiry["content_current"])
            self.assertTrue(sink.client.last_expiry["validation_only"])
        finally:
            stop.set()
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result["cleanup_ok"], result)
        self.assertEqual(result["frames_submitted"], 1)
        self.assertGreater(result["stale_results_dropped"], 0)
        self.assertEqual(sink.report["native_report"]["expired_frames"], 1)
        self.assertFalse(sink.report["native_report"]["runtime_initialized"])

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV required for sink image conversion")
    def test_shared_sink_forwards_original_absolute_deadline(self):
        import numpy as np
        from types import SimpleNamespace

        client = OverlayClient(validation_only=True).start()
        try:
            deadline = time.monotonic_ns() + 200000000
            processed = SimpleNamespace(image=np.zeros((1, 2, 3), dtype=np.uint8), expires_ns=deadline)
            OverlaySink(client).submit(processed)
            self.assertTrue(client.frame_expired.wait(3))
            self.assertEqual(client.last_expiry["deadline_ns"], deadline)
            self.assertFalse(client.last_expiry["content_current"])
        finally:
            report = client.close()
        self.assertTrue(report["cleanup_confirmed"], report)
        self.assertFalse(report["native_report"]["runtime_initialized"])


class ExpiryGateTests(unittest.TestCase):
    def test_real_output_and_old_bridge_cannot_accept_expiring_live_frames(self):
        for client in (OverlayClient(validation_only=False), OverlayClient(validation_only=True)):
            with self.assertRaisesRegex(ValueError, "not_approved_or_supported"):
                client.submit_frame(1, 1, bytes(4), expires_ns=time.monotonic_ns() + 100000000)
            self.assertIsNone(client.process)
