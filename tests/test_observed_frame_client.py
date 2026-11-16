"""``ObservedFrameClient`` (work-order item L8) against the validation bridge: packets, keepalive, refusals, the gate.

The validation bridge takes the observed frame (opcode 6) under the fixed
HUD's rules and displays nothing, so these tests show what the client writes
and what the bridge says back. Real output is refused here; the fixed-HUD mode
itself runs on the fake runtime in ``tests/test_camera_hud_stage.py``.
Standard library only.
"""
import subprocess
import threading
import time
import unittest
from unittest import mock

from sightcircuit import gates, observed_frames, overlay
from sightcircuit.hud.contract import HUD_CONFIG, MAX_DISPLAY_AGE_NS, RGBA_BYTES
from sightcircuit.observed_frames import ObservedFrameClient
from sightcircuit.overlay import DEFAULT_BRIDGE, PanelSettings

MS = 1_000_000
WIDTH, HEIGHT = HUD_CONFIG.width_px, HUD_CONFIG.height_px
RGBA = bytes(WIDTH * HEIGHT * RGBA_BYTES)


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)
    return True


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Run bash scripts/build_overlay_bridge.sh")
class ObservedFrameClientTests(unittest.TestCase):
    def client(self, **options):
        self.events, self.bridge_lines = [], []

        def listener(event, **fields):  # The writer's events, and apart from them the bridge's lines as read.
            (self.bridge_lines.append(fields["line"]) if event == "bridge_line" else self.events.append((event, fields)))
        client = ObservedFrameClient(listener=listener, **options)
        self.addCleanup(client.close)
        return client.start()

    def send(self, client, frame_id, *, age_ns=0, life_ns=MAX_DISPLAY_AGE_NS, token=None):
        observed = time.monotonic_ns() - age_ns
        self.assertTrue(client.submit_observed(frame_id, observed, observed + life_ns, WIDTH, HEIGHT, RGBA, token=token))
        return observed

    def named(self, name):
        return [fields for event, fields in self.events if event == name]

    def lines(self, client):
        """The bridge's visibility lines in cycle order (cycles rise with each request)."""
        return [line for cycle in sorted(client.visibility_lines) for line in client.visibility_lines[cycle]]

    def test_frames_go_out_with_the_callers_times_and_no_settings_packet_is_ever_sent(self):
        client = self.client()
        self.assertEqual((client.bridge_mode(), client.validation_only, client.frame_expiry_supported),
                         ("--validate-stdin", True, True))
        observed = [self.send(client, frame_id, age_ns=5 * MS, token=("t", frame_id)) for frame_id in (3, 4, 9)]
        self.assertTrue(wait_for(lambda: len(self.named("frame_sent")) + len(self.named("frame_not_sent")) == 3))
        report = client.close()
        native = report["native_report"]
        sent = self.named("frame_sent")
        # A newer frame may replace one still waiting; every frame written carries the caller's own three numbers.
        self.assertEqual(len(sent) + report["frames_replaced"], 3)
        for fields in sent:
            index = (3, 4, 9).index(fields["frame_id"])
            self.assertEqual((fields["observed_ns"], fields["expires_ns"], fields["token"], fields["hidden"]),
                             (observed[index], observed[index] + MAX_DISPLAY_AGE_NS, ("t", fields["frame_id"]), False))
            self.assertGreater(fields["sent_ns"], fields["observed_ns"])
        self.assertEqual((native["mode"], native["settings_received"], native["observed_frames_received"],
                          native["frames_received"], native["stale_frames_dropped"], native["frames_retransmitted"]),
                         ("--validate-stdin", 0, len(sent), len(sent), 0, 0))
        self.assertEqual((report["settings_sent"], report["frames_retransmitted"], report["frames_sent"],
                          report["frames_submitted"], report["mode"], report["started"], report["cleanup_confirmed"]),
                         (0, 0, len(sent), 3, "--validate-stdin", True, True))
        self.assertEqual(report["bridge_counts"]["observed_frames_received"], len(sent))
        # The bridge's only per-frame acknowledgement is its first-frame line.
        self.assertEqual((client.first_frame["event"], client.first_frame["width"], client.first_frame["height"]),
                         ("frame_validated", WIDTH, HEIGHT))
        self.assertGreaterEqual(client.first_frame["read_ns"], sent[0]["observed_ns"])
        self.assertEqual(client.line_counts["ready"], 1)

    def test_the_bridge_answers_for_every_frame_and_the_client_keeps_the_answers(self):
        client = self.client()
        observed = self.send(client, 1, life_ns=300 * MS)
        self.assertTrue(wait_for(lambda: 1 in client.presented))
        # A newer identity with an older observation is stale: refused, and answered with that reason.
        stale = time.monotonic_ns() - 990 * MS
        with client.condition:
            self.assertTrue(client.submit_observed(2, stale, stale + MAX_DISPLAY_AGE_NS - 1, WIDTH, HEIGHT, RGBA))
        self.assertTrue(wait_for(lambda: len(self.named("frame_sent")) + len(self.named("frame_not_sent")) == 2))
        self.send(client, 3)
        self.assertTrue(wait_for(lambda: 3 in client.presented))
        report = client.close()
        presented = [line for line in self.bridge_lines if line["event"] == "frame_presented"]
        self.assertEqual([line["frame_id"] for line in presented], [1, 3])
        self.assertEqual((presented[0]["frame_observed_ns"], presented[0]["deadline_ns"], presented[0]["visible"],
                          presented[0]["validation_only"]), (observed, observed + 300 * MS, True, True))
        self.assertGreaterEqual(presented[0]["presented_ns"], observed)
        self.assertEqual(client.presented[1]["presented_ns"], presented[0]["presented_ns"])
        self.assertGreaterEqual(client.presented[1]["read_ns"], presented[0]["presented_ns"])
        self.assertEqual((report["frames_presented"], report["native_report"]["frames_received"]), (2, 2))
        if self.named("frame_sent")[1:2] and self.named("frame_sent")[1]["frame_id"] == 2:  # It was written in time,
            refused, = [line for line in self.bridge_lines if line["event"] == "frame_refused"]  # so the bridge answered.
            self.assertEqual((refused["frame_id"], refused["reason"], refused["frame_observed_ns"]),
                             (2, "stale", stale))
            self.assertEqual((client.refused, report["frames_refused"], report["refused_reasons"]["stale"]),
                             ({2: "stale"}, 1, 1))
        else:
            self.assertEqual(report["frames_expired_before_send"], 1)
        # A line the client cannot read as an answer fails it instead of being guessed at.
        for line in ({"event": "frame_presented", "frame_id": 1, "presented_ns": 5},
                     {"event": "frame_presented", "frame_id": "1", "presented_ns": 5, "visible": True},
                     {"event": "frame_refused", "frame_id": 1, "reason": "because"}):
            with self.assertRaisesRegex(RuntimeError, "overlay_frame_line_invalid"):
                ObservedFrameClient()._line(line)

    def test_after_the_end_nothing_is_accepted_and_no_show_is_written(self):
        client = self.client(keepalive_seconds=0.15)
        self.send(client, 1)
        self.assertTrue(wait_for(lambda: self.named("frame_sent")))
        self.assertTrue(client.end())
        self.assertTrue(client.final)
        now = time.monotonic_ns()
        self.assertFalse(client.submit_observed(2, now, now + MAX_DISPLAY_AGE_NS, WIDTH, HEIGHT, RGBA))  # Latched.
        self.assertTrue(wait_for(lambda: self.named("taken_down")))
        self.assertEqual(client.final_hide["sent_ns"], self.named("taken_down")[0]["sent_ns"])
        time.sleep(0.5)  # Keepalives go on, as hides.
        self.assertTrue(client.end())
        report = client.close()
        self.assertEqual([event for event, _ in self.events], ["frame_sent", "taken_down"])
        names = [line["event"] for line in self.lines(client)]
        self.assertNotIn("show_requested", names)
        self.assertGreaterEqual(names.count("hide_requested"), 3)
        self.assertEqual((report["frames_submitted"], report["frames_sent"], report["shows"], report["take_downs"],
                          report["native_report"]["observed_frames_received"]), (1, 1, 0, 1, 1))
        hide = next(line for line in client.cycle_lines(client.final_hide["cycle"]) if line["event"] == "hide_returned")
        self.assertGreaterEqual(hide["returned_ns"], client.final_hide["sent_ns"] - MS)
        # The race itself: a frame queued under the lock is dropped by the end taken under the same lock.
        racing = self.client()
        with racing.condition:
            self.send(racing, 1, token="queued")
            racing.end()
        self.assertTrue(wait_for(lambda: racing.hidden))
        report = racing.close()
        self.assertEqual((report["frames_sent"], report["frames_withdrawn"], report["shows"]), (0, 1, 0))

    def test_any_failure_on_the_writer_thread_fails_the_client(self):
        def listener(event, **fields):
            if event == "frame_sent":
                raise KeyError("injected")
        client = ObservedFrameClient(listener=listener)
        self.addCleanup(client.close)
        client.start()
        self.send(client, 1)
        self.assertTrue(wait_for(lambda: client.error))
        self.assertIn("injected", client.error)
        now = time.monotonic_ns()
        self.assertFalse(client.submit_observed(2, now, now + MS, WIDTH, HEIGHT, RGBA))  # The writer is gone.
        self.assertFalse(client.close()["cleanup_confirmed"])

    def test_what_the_client_never_sends_is_refused_by_name(self):
        client = self.client()
        now = time.monotonic_ns()
        for call, reason in ((lambda: client.set_settings(PanelSettings()), "settings"),
                             (lambda: client.freeze_settings(PanelSettings()), "settings"),
                             (lambda: client.submit_frame(WIDTH, HEIGHT, RGBA), "frames_without_observation"),
                             (lambda: client.submit_frame(WIDTH, HEIGHT, RGBA, expires_ns=now + MS),
                              "frames_without_observation"),
                             (lambda: client.request_visibility(1, True), "visibility_cycles")):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(str(caught.exception), f"observed_frame_client_refuses_{reason}")
        # A deadline beyond the observation's bound, and an identity that does not rise.
        with self.assertRaisesRegex(ValueError, "invalid_frame_expiry"):
            client.submit_observed(1, now, now + MAX_DISPLAY_AGE_NS + 1, WIDTH, HEIGHT, RGBA)
        self.send(client, 5)
        for frame_id in (5, 4):
            with self.assertRaisesRegex(ValueError, "observed_frame_identity_not_rising"):
                client.submit_observed(frame_id, now, now + MS, WIDTH, HEIGHT, RGBA)
        report = client.close()
        self.assertEqual((report["native_report"]["settings_received"], report["cleanup_confirmed"]), (0, True))
        self.assertFalse(client.submit_observed(6, now, now + MS, WIDTH, HEIGHT, RGBA))  # Stopped: not queued, no error.
        self.assertFalse(client.take_down())

    def test_a_take_down_hides_and_the_next_frame_is_followed_by_a_show(self):
        client = self.client(keepalive_seconds=0.15)
        self.send(client, 1)
        self.assertTrue(wait_for(lambda: self.named("frame_sent")))
        self.assertTrue(client.take_down())
        self.assertTrue(client.take_down())  # Already down: nothing more is queued.
        self.assertTrue(wait_for(lambda: self.named("taken_down")))
        time.sleep(0.5)  # Several keepalives while the tile is down: each repeats the hide, never a show.
        self.assertTrue(client.hidden)
        down = [line["event"] for line in self.lines(client)]
        self.assertGreaterEqual(down.count("hide_requested"), 3)
        self.assertNotIn("show_requested", down)
        self.send(client, 2)
        self.assertTrue(wait_for(lambda: self.named("shown")))
        self.assertFalse(client.hidden)
        order = [event for event, _ in self.events]
        self.assertEqual(order, ["frame_sent", "taken_down", "frame_sent", "shown"])
        self.assertEqual([fields["hidden"] for fields in self.named("frame_sent")], [False, True])
        self.assertLess(self.named("frame_sent")[1]["sent_ns"], self.named("shown")[0]["sent_ns"])
        time.sleep(0.4)  # And now the keepalive repeats the show.
        after = [line["event"] for line in self.lines(client)][len(down):]
        report = client.close()
        self.assertIn("show_requested", after)
        self.assertNotIn("hide_requested", after[after.index("show_requested"):])
        self.assertEqual((report["take_downs"], report["shows"]), (2, 1))  # The second is the close's own.
        self.assertEqual([event for event, _ in self.events][4:], ["taken_down"])
        self.assertGreaterEqual(report["keepalives"], 4)
        self.assertEqual(report["visibility_requests"], report["native_report"]["visibility_requests_received"])
        self.assertEqual(report["native_report"]["settings_received"], 0)

    def test_a_take_down_drops_the_frame_that_was_waiting(self):
        client = self.client()
        with client.condition:  # The writer cannot dequeue while the test holds its condition.
            self.send(client, 1, token="waiting")
            self.assertTrue(client.take_down())
        self.assertTrue(wait_for(lambda: self.named("taken_down")))
        report = client.close()
        self.assertEqual(self.named("frame_not_sent"), [{"frame_id": 1, "reason": "taken_down_before_send",
                                                         "token": "waiting"}])
        self.assertEqual((report["frames_withdrawn"], report["frames_sent"],
                          report["native_report"]["observed_frames_received"]), (1, 0, 0))

    def test_a_newer_frame_replaces_the_one_waiting_and_a_frame_past_its_deadline_is_not_sent(self):
        client = self.client()
        with client.condition:
            self.send(client, 1)
            self.send(client, 2)
        self.assertTrue(wait_for(lambda: self.named("frame_sent")))
        with client.condition:
            self.send(client, 3, life_ns=30 * MS)
            time.sleep(0.05)  # Its deadline passes while it waits.
        self.assertTrue(wait_for(lambda: len(self.named("frame_not_sent")) == 2))
        report = client.close()
        self.assertEqual([(fields["frame_id"], fields["reason"]) for fields in self.named("frame_not_sent")],
                         [(1, "replaced_before_send"), (3, "expired_before_send")])
        self.assertEqual([fields["frame_id"] for fields in self.named("frame_sent")], [2])
        self.assertEqual((report["frames_replaced"], report["frames_expired_before_send"], report["frames_sent"]), (1, 1, 1))
        native = report["native_report"]
        self.assertEqual((native["observed_frames_received"], native["stale_frames_dropped"]), (1, 0))

    def test_every_frame_expired_line_is_kept_with_its_fields(self):
        client = self.client()
        observed = self.send(client, 7, life_ns=120 * MS)
        self.assertTrue(wait_for(lambda: client.expiry_event_count == 1))
        report = client.close()
        event, = client.expiry_events
        self.assertEqual((event["frame_id"], event["frame_observed_ns"], event["deadline_ns"], event["validation_only"],
                          event["content_current"]), (7, observed, observed + 120 * MS, True, False))
        self.assertGreaterEqual(event["observed_ns"], event["deadline_ns"])
        self.assertLess(event["observed_ns"] - event["deadline_ns"], overlay.BRIDGE_INPUT_POLL_NS + 150 * MS)
        self.assertLessEqual(set(event), set(observed_frames.EXPIRY_EVENT_FIELDS))
        self.assertEqual((report["expiry_events"], report["expiry_events_dropped"],
                          report["native_report"]["expired_frames"]), (1, 0, 1))

    def test_the_keepalive_holds_the_connection_past_the_bridges_ten_second_timeout_and_shows_nothing(self):
        client = self.client()
        self.assertEqual(client.keepalive_seconds, observed_frames.KEEPALIVE_SECONDS)
        started = time.monotonic()
        time.sleep(10.8)  # Longer than the bridge's ten seconds without a packet; nothing is submitted.
        self.assertIsNone(client.process.poll())
        self.assertIsNone(client.error)
        returned = [line for line in self.lines(client) if line["event"] == "show_returned"]
        self.assertGreaterEqual(len(returned), 4)
        self.assertEqual({(line["ok"], line["reason"], line["call"]) for line in returned}, {(False, "no_image", "none")})
        self.assertNotIn("hide_requested", [line["event"] for line in self.lines(client)])
        self.assertEqual(self.events, [])
        self.send(client, 1)  # The tile is still free to show.
        self.assertTrue(wait_for(lambda: self.named("frame_sent")))
        report = client.close()
        self.assertEqual([event for event, _ in self.events], ["frame_sent", "taken_down"])  # Closing takes it down.
        native = report["native_report"]
        self.assertGreaterEqual(time.monotonic() - started, 10.5)
        self.assertEqual((native["status"], native["frames_received"], native["settings_received"],
                          report["keepalives"] >= 4, report["cleanup_confirmed"]), ("validated", 1, 0, True, True))

    def test_keepalive_and_mode_arguments_are_checked(self):
        for seconds in (0, -1, observed_frames.KEEPALIVE_MAX_SECONDS + 0.1, float("nan"), "2"):
            with self.assertRaisesRegex(ValueError, "observed_frame_keepalive_invalid"):
                ObservedFrameClient(keepalive_seconds=seconds)
        for mode in (None, 0, 1, "validate"):
            with self.assertRaisesRegex(ValueError, "observed_frame_client_mode_invalid"):
                ObservedFrameClient(validation_only=mode)
        self.assertLess(observed_frames.KEEPALIVE_MAX_SECONDS, 10)  # The bridge's input timeout.

    def test_close_is_idempotent_from_any_thread_and_harmless_before_start(self):
        idle = ObservedFrameClient()
        report = idle.close()
        self.assertEqual((report["started"], report["cleanup_confirmed"], report["frames_sent"]), (False, True, 0))
        self.assertFalse(idle.take_down())
        client = self.client()
        reports = []
        threads = [threading.Thread(target=lambda: reports.append(client.close())) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(reports), 4)
        self.assertTrue(all(report is reports[0] for report in reports))
        self.assertEqual((reports[0]["cleanup_confirmed"], reports[0]["worker_exit_code"]), (True, 0))


class FixedHudStartGateTests(unittest.TestCase):
    """Real output (``--fixed-hud``) is refused through ``LIVE_HUD_OUTPUT`` before anything is launched."""

    def test_the_fixed_hud_start_is_refused_and_nothing_is_launched(self):
        client = ObservedFrameClient(validation_only=False)
        self.assertEqual(client.bridge_mode(), "--fixed-hud")
        with mock.patch.object(subprocess, "Popen") as launched:
            for start in (client.start, client.check_start):
                with self.assertRaises(ValueError) as caught:
                    start()
                self.assertEqual(str(caught.exception), "fixed_hud_output_not_approved")
            launched.assert_not_called()
        self.assertIsNone(client.process)
        self.assertEqual(gates.claims(), {"production_live_input_enabled": False,
                                          "production_camera_input_enabled": False, "mapping_qualified": False,
                                          "production_hud_enabled": False})

    def test_the_refusal_is_the_gates_and_has_one_permission_site(self):
        client = ObservedFrameClient(validation_only=False)
        self.assertIs(observed_frames.fixed_hud_output_permitted(client), False)
        with mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            with self.assertRaises(ValueError):
                client.check_start()
        (gate, permitted, reason), = [call.args for call in required.call_args_list]
        self.assertEqual((gate, permitted, reason), (gates.LIVE_HUD_OUTPUT, False, "fixed_hud_output_not_approved"))
        # Only the literal True from the one permission function permits: a truthy answer is still refused, and
        # the function is asked once per start, with the client, so L10's record check has one place to go.
        with mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=1) as asked, \
                mock.patch.object(subprocess, "Popen") as launched:
            with self.assertRaisesRegex(ValueError, "fixed_hud_output_not_approved"):
                client.start()
            asked.assert_called_once_with(client)
            launched.assert_not_called()
        # Validation never asks.
        with mock.patch.object(observed_frames, "fixed_hud_output_permitted") as asked:
            ObservedFrameClient().check_start()
            asked.assert_not_called()

    def test_no_environment_or_bridge_line_opens_it(self):
        client = ObservedFrameClient(validation_only=False, env={"SIGHTCIRCUIT_ALLOW_FIXED_HUD": "1"})
        with mock.patch.object(subprocess, "Popen") as launched, self.assertRaises(ValueError):
            client.start()
        launched.assert_not_called()
        # A validation client told by its bridge that it is the fixed HUD fails instead of carrying on.
        validation = ObservedFrameClient()
        with self.assertRaisesRegex(RuntimeError, "overlay_bridge_fixed_hud_mode_mismatch"):
            validation._line({"event": "ready", "observed_frames_supported": True, "frame_expiry_supported": True,
                              "fixed_hud": True})
        with self.assertRaisesRegex(RuntimeError, "overlay_bridge_lacks_observed_frames"):
            validation._line({"event": "ready", "frame_expiry_supported": True})


if __name__ == "__main__":
    unittest.main()
