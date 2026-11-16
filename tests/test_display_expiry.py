"""Independent presentation expiry using native validation, never a VR session."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time
import unittest

from sightcircuit.runtime import live, synthetic
from sightcircuit.overlay import DEFAULT_BRIDGE, OverlayClient, PanelSettings, packet
from sightcircuit.overlay import OverlaySink
from sightcircuit.hud.contract import MAX_DISPLAY_AGE_NS
from sightcircuit.overlay import BRIDGE_INPUT_POLL_NS, VISIBILITY_DEADLINE_MARGIN_NS

ROOT = Path(__file__).resolve().parents[1]


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

    # One input poll of the bridge's loop (20 ms) is the design bound on noticing a deadline; the margin is
    # for this host's scheduling of the bridge process, as VISIBILITY_DEADLINE_MARGIN_NS is for a request.
    POLL_NS = BRIDGE_INPUT_POLL_NS
    SCHEDULING_MARGIN_NS = VISIBILITY_DEADLINE_MARGIN_NS

    def test_expiry_is_observed_within_one_poll_of_the_deadline(self):
        # Work-order item L7. With no input at all the loop still polls: five frames, each left to expire.
        process, events = self.bridge()
        overshoots = []
        for _ in range(5):
            deadline = time.monotonic_ns() + 120_000_000
            self.send(process, self.expiring_frame(deadline))
            expired = self.event(events, "frame_expired")
            self.assertEqual(expired["deadline_ns"], deadline)
            self.assertGreaterEqual(expired["observed_ns"], deadline)  # never early
            overshoots.append(expired["observed_ns"] - deadline)
            self.assertIs(expired["content_current"], False)
        self.assertLess(min(overshoots), self.POLL_NS, overshoots)
        self.assertLess(max(overshoots), self.POLL_NS + self.SCHEDULING_MARGIN_NS, overshoots)
        report = self.finish(process, events)
        self.assertEqual((report["frames_received"], report["expired_frames"], report["stale_frames_dropped"]),
                         (5, 5, 0))
        self.assertIs(report["content_current"], False)
        self.assertIs(report["presentation_visible"], False)

    def test_a_retransmission_never_renews_the_deadline(self):
        # The same packet again, before the deadline and after it: the deadline it was first sent with stands,
        # one expiry is recorded at it, and the late copy is refused, never presented.
        process, events = self.bridge()
        deadline = time.monotonic_ns() + 300_000_000
        frame = self.expiring_frame(deadline)
        self.send(process, frame)
        self.event(events, "frame_validated")
        for _ in range(4):
            time.sleep(0.05)
            self.send(process, frame)
        expired = self.event(events, "frame_expired")
        self.assertEqual(expired["deadline_ns"], deadline)
        self.assertLess(expired["observed_ns"] - deadline, self.POLL_NS + self.SCHEDULING_MARGIN_NS)
        self.send(process, frame)
        self.send(process, frame)
        report = self.finish(process, events)
        self.assertEqual((report["frames_received"], report["expiring_frames_received"], report["expired_frames"],
                          report["stale_frames_dropped"]), (5, 7, 1, 2))
        self.assertIs(report["content_current"], False)
        self.assertIs(report["presentation_visible"], False)
        while not events.empty():  # nothing was presented or expired a second time
            self.assertNotIn(events.get_nowait()["event"], ("frame_expired", "frame_validated"))

    def test_an_expired_arrival_is_refused_and_an_over_long_deadline_ends_the_run(self):
        process, events = self.bridge()
        self.send(process, self.expiring_frame(time.monotonic_ns() - 1))  # expired before it is observed
        self.send(process, self.expiring_frame(1))  # long expired
        report = self.finish(process, events)
        self.assertEqual((report["frames_received"], report["expiring_frames_received"],
                          report["stale_frames_dropped"], report["expired_frames"]), (0, 2, 2, 0))
        self.assertIs(report["content_current"], False)
        # One second past the observed time is the limit: a little under it is taken, a little over refused.
        process, events = self.bridge()
        self.send(process, self.expiring_frame(time.monotonic_ns() + MAX_DISPLAY_AGE_NS - 100_000_000))
        self.event(events, "frame_validated")
        self.send(process, self.expiring_frame(time.monotonic_ns() + MAX_DISPLAY_AGE_NS + 100_000_000))
        result = self.event_or_error(events)
        self.assertEqual((result["event"], result["error"]), ("error", "invalid_frame_expiry"))
        self.assertEqual((result["frames_received"], result["frames_submitted"]), (1, 0))
        self.assertEqual(process.wait(timeout=3), 1)

    def event_or_error(self, events):
        while True:
            event = events.get(timeout=3)
            if "exit_code" in event:
                return event

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV is required for the live pipeline detector")
    def test_stalled_inference_cannot_keep_previous_result_current(self):
        blocked, release, stop = threading.Event(), threading.Event(), threading.Event()
        class Detector(synthetic.FixtureDetector):
            calls = 0
            def detect(self, *args):
                self.calls += 1
                if self.calls == 2:
                    blocked.set()
                    if not release.wait(5):
                        raise RuntimeError("fixture_release_timeout")
                return super().detect(*args)
        sink = OverlaySink()
        result = {}
        def run():
            result.update(live.run_pipeline(synthetic.SyntheticSource(), Detector(), sink, 5,
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
            # The sink delivers through the client's writer thread. The bridge drops
            # a frame that arrives after its deadline as stale and then emits no
            # frame_expired event, so the margin must cover delivery under CPU load.
            deadline = time.monotonic_ns() + 1000000000
            processed = SimpleNamespace(image=np.zeros((1, 2, 3), dtype=np.uint8), expires_ns=deadline)
            OverlaySink(client).submit(processed)
            self.assertTrue(client.frame_expired.wait(5))
            self.assertEqual(client.last_expiry["deadline_ns"], deadline)
            self.assertFalse(client.last_expiry["content_current"])
        finally:
            report = client.close()
        self.assertTrue(report["cleanup_confirmed"], report)
        self.assertFalse(report["native_report"]["runtime_initialized"])


class ExpiryGateTests(unittest.TestCase):
    def test_expiring_real_output_through_the_sink_is_refused_whatever_the_bridge_reports(self):
        # Work-order item L7: the bridge's --fixed-hud mode reports frame_expiry_supported in real output, and
        # Python's gate check stays the outer guard. A real-output client that has seen such a ready line still
        # refuses an expiring frame, through the sink and directly, before anything is queued for the bridge.
        from types import SimpleNamespace
        from sightcircuit import gates

        self.assertEqual(gates.claims(gates.LIVE_HUD_OUTPUT), {"production_hud_enabled": False})
        client = OverlayClient(validation_only=False)
        client.frame_expiry_supported = True
        sink = OverlaySink(client)
        deadline = time.monotonic_ns() + 100_000_000
        with self.assertRaisesRegex(ValueError, "^expiring_live_output_not_approved_or_supported$"):
            client.submit_frame(1, 1, bytes(4), expires_ns=deadline)
        if importlib.util.find_spec("cv2"):
            import numpy as np

            with self.assertRaisesRegex(ValueError, "^expiring_live_output_not_approved_or_supported$"):
                sink.submit(SimpleNamespace(image=np.zeros((128, 320, 3), dtype=np.uint8), expires_ns=deadline))
        self.assertIsNone(client.pending_frame)
        self.assertIsNone(client.process)
        # The fixed placement stays gated for real output too, and a HUD sink still owns a validation client.
        with self.assertRaisesRegex(ValueError, "^fixed_live_hud_not_approved$"):
            client.freeze_settings(PanelSettings())
        # The client can start only the two stream modes it always could; it never names the new one.
        source = (ROOT / "sightcircuit/overlay.py").read_text()
        self.assertIn('mode = "--validate-stdin" if self.validation_only else "--stdin"', source)
        self.assertNotIn("fixed-hud", source)

    def test_real_output_and_old_bridge_cannot_accept_expiring_live_frames(self):
        for client in (OverlayClient(validation_only=False), OverlayClient(validation_only=True)):
            with self.assertRaisesRegex(ValueError, "not_approved_or_supported"):
                client.submit_frame(1, 1, bytes(4), expires_ns=time.monotonic_ns() + 100000000)
            self.assertIsNone(client.process)
