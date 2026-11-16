"""The bridge's ``--fixed-hud`` mode executed for real, against a fake runtime (work-order item L7).

``tests/fixtures/fake_vrclient.cpp`` is a ``vrclient.so`` that the SDK's
loader finds through ``VR_PATHREG_OVERRIDE``. It displays nothing; it logs the
bridge's overlay calls with their ``CLOCK_MONOTONIC`` times and can delay or
fail them. These tests therefore run the real binary's real-output path: the
hide at a deadline, a slow upload, a slow or failing hide, the refusals with
their cleanup. They show what the bridge calls and when. They do not show
what a headset displays, and no time here is a Frame measurement.
"""
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import threading
import time
import unittest

from sightcircuit.hud.contract import (HUD_CONFIG, MAX_DISPLAY_AGE_NS, OBSERVATION_FUTURE_TOLERANCE_NS,
                                       OBSERVED_FRAME_PREFIX_FORMAT, OPCODE_EXPIRING_FRAME, OPCODE_FRAME,
                                       OPCODE_OBSERVED_FRAME, OPCODE_STOP, RGBA_BYTES)
from sightcircuit.overlay import (BRIDGE_INPUT_POLL_NS, DEFAULT_BRIDGE, PanelSettings, observed_frame_packet, packet,
                                  visibility_packet)
from tests import fake_vrclient

MS = 1_000_000
POLL_NS = BRIDGE_INPUT_POLL_NS
MARGIN_NS = 150 * MS  # this host's scheduling of the bridge process; never part of a claim about the bridge
WIDTH, HEIGHT = HUD_CONFIG.width_px, HUD_CONFIG.height_px
DRAWING = ("SetOverlayRaw", "ShowOverlay", "HideOverlay", "ClearOverlayTexture", "DestroyOverlay")


def tile(frame_id, observed_ns, life_ns, mark=0, *, width=WIDTH, height=HEIGHT):
    """An observed frame whose first byte is ``mark``, so the fake's log tells tiles apart."""
    return observed_frame_packet(frame_id, observed_ns, observed_ns + life_ns, width, height,
                                 bytes([mark]) + bytes(width * height * RGBA_BYTES - 1))


class Run:
    """One ``--fixed-hud`` bridge on the fake runtime: its lines as they arrive, and the fake's call log."""

    count = 0

    def __init__(self, case, **fake):
        self.case = case
        Run.count += 1
        self.log = Path(case.directory.name) / f"calls-{Run.count}.jsonl"
        env = {**os.environ, **case.runtime_env, "FAKE_VR_LOG": str(self.log), **fake}
        self.process = subprocess.Popen([str(DEFAULT_BRIDGE), "--fixed-hud"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        case.addCleanup(self.kill)
        self.lines, self.arrived = [], threading.Condition()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        ready = self.wait("ready")
        case.assertEqual((ready["fixed_hud"], ready["frame_expiry_supported"], ready["observed_frames_supported"],
                          ready["runtime_initialized"], ready["validation_only"]), (True, True, True, True, False))

    def read(self):
        for line in self.process.stdout:
            with self.arrived:
                self.lines.append(json.loads(line))
                self.arrived.notify_all()

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()

    def send(self, data, *, may_be_refused=False):
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except BrokenPipeError:
            if not may_be_refused:  # a packet refused on its header: the bridge is gone before the payload is
                raise

    def wait(self, event, count=1, timeout=8):
        """The ``count``-th line named ``event``; fails when the bridge ends or the time runs out first."""
        end = time.monotonic() + timeout
        with self.arrived:
            while True:
                found = [line for line in self.lines if line["event"] == event]
                if len(found) >= count:
                    return found[count - 1]
                self.case.assertIsNone(self.process.poll(), self.lines[-1:])
                self.case.assertGreater(end - time.monotonic(), 0, f"no {event} #{count}: {self.lines}")
                self.arrived.wait(0.05)

    def finish(self, stop=True, timeout=8):
        """Stop (or just await) the bridge; returns ``(exit code, final report)``."""
        if stop and self.process.poll() is None:
            try:
                self.send(packet(OPCODE_STOP))
                self.process.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
        code = self.process.wait(timeout=timeout)
        self.reader.join(timeout=5)
        return code, self.lines[-1]

    def calls(self, *names):
        return [call for call in fake_vrclient.calls(self.log) if call["call"] in (names or DRAWING)]

    def named(self, name):
        return [line for line in self.lines if line["event"] == name]


@unittest.skipUnless(DEFAULT_BRIDGE.is_file() and fake_vrclient.available(),
                     "Needs scripts/build_overlay_bridge.sh, its SDK checkout and g++")
class FixedHudRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.runtime_env, _ = fake_vrclient.runtime(cls.directory.name)

    def assert_retired(self, run, report, *, code=0):
        """The overlay was hidden, cleared and destroyed, and nothing was shown or uploaded afterwards."""
        self.assertEqual((report["exit_code"], report["cleanup_ok"], report["hide_returned_ok"],
                          report["clear_texture_returned_ok"], report["destroy_returned_ok"]),
                         (code, True, True, True, True), report)
        self.assertEqual([call["call"] for call in run.calls()][-3:],
                         ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
        self.assertIs(run.calls()[-1]["visible"], False)

    def test_the_placement_is_applied_once_and_a_tile_is_hidden_at_its_deadline_with_silent_stdin(self):
        run = Run(self)
        start = time.monotonic_ns()
        run.send(tile(1, start, 250 * MS, mark=9))
        expired = run.wait("frame_expired")
        time.sleep(0.3)  # silence after the expiry: nothing more may be shown
        code, report = run.finish()
        self.assertEqual((code, report["status"], report["frames_received"], report["frames_submitted"],
                          report["observed_frames_received"], report["expired_frames"]), (0, "closed", 1, 1, 1, 1))
        # The deadline, the frame's own identity and observation, and the hide that took it down.
        self.assertEqual((expired["deadline_ns"], expired["frame_id"], expired["frame_observed_ns"],
                          expired["outcome"]), (start + 250 * MS, 1, start, "hidden"))
        self.assertEqual((expired["hide_ok"], expired["api_visible_after_hide"], expired["texture_cleared"]),
                         (True, False, True))
        self.assertGreaterEqual(expired["observed_ns"], expired["deadline_ns"])
        self.assertLess(expired["observed_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
        self.assertLessEqual(expired["observed_ns"], expired["hide_called_ns"])
        self.assertLessEqual(expired["hide_called_ns"], expired["hide_returned_ns"])
        # The fake's own record of the same calls: upload, show, then the hide and the clear at the deadline.
        drawing = run.calls()
        self.assertEqual([call["call"] for call in drawing[:5]],
                         ["HideOverlay", "SetOverlayRaw", "ShowOverlay", "HideOverlay", "ClearOverlayTexture"])
        upload, show, hide = drawing[1], drawing[2], drawing[3]
        self.assertEqual((upload["width"], upload["height"], upload["depth"], upload["first"]), (WIDTH, HEIGHT, 4, 9))
        self.assertLess(show["end_ns"], expired["deadline_ns"])
        self.assertGreaterEqual(hide["start_ns"], expired["deadline_ns"])
        self.assertEqual((hide["start_ns"] >= expired["hide_called_ns"], hide["end_ns"] <= expired["hide_returned_ns"],
                          hide["visible"]), (True, True, False))
        self.assertEqual(len(run.calls("ShowOverlay")), 1)
        self.assert_retired(run, report)
        # The placement: the contract's, set once at connect and never again.
        placement = fake_vrclient.calls(run.log)
        width = [call for call in placement if call["call"] == "SetOverlayWidthInMeters"]
        alpha = [call for call in placement if call["call"] == "SetOverlayAlpha"]
        transform = [call for call in placement if call["call"] == "SetOverlayTransformTrackedDeviceRelative"]
        self.assertEqual((len(width), len(alpha), len(transform)), (1, 1, 1))
        self.assertAlmostEqual(width[0]["value"], HUD_CONFIG.panel_width_m, places=5)
        self.assertAlmostEqual(alpha[0]["value"], HUD_CONFIG.opacity, places=5)
        for axis, value in (("x", HUD_CONFIG.x_m), ("y", HUD_CONFIG.y_m), ("z", -HUD_CONFIG.distance_m)):
            self.assertAlmostEqual(transform[0][axis], value, places=5, msg=axis)

    def test_a_half_sent_next_packet_cannot_hold_a_tile_past_its_deadline(self):
        for split in (5, 40):  # inside the header, inside the payload
            with self.subTest(split=split):
                run = Run(self)
                start = time.monotonic_ns()
                run.send(tile(1, start, 200 * MS))
                run.wait("frame_submitted")
                following = tile(2, time.monotonic_ns(), 400 * MS, mark=2)
                run.send(following[:split])
                expired = run.wait("frame_expired")
                self.assertEqual((expired["frame_id"], expired["outcome"], expired["hide_ok"]), (1, "hidden", True))
                self.assertLess(expired["hide_returned_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
                run.send(following[split:])
                second = run.wait("frame_expired", 2)
                self.assertEqual((second["frame_id"], second["outcome"]), (2, "hidden"))
                code, report = run.finish()
                self.assertEqual((code, report["frames_received"], report["expired_frames"]), (0, 2, 2))
                self.assert_retired(run, report)

    def test_an_upload_that_returns_after_the_deadline_is_never_shown(self):
        # Nothing is up; the tile's upload takes 700 ms and its deadline is 250 ms away.
        run = Run(self, FAKE_VR_SetOverlayRaw_DELAY_MS="700")
        start = time.monotonic_ns()
        run.send(tile(1, start, 250 * MS, mark=1))
        expired = run.wait("frame_expired")
        code, report = run.finish()
        self.assertEqual((expired["frame_id"], expired["deadline_ns"], expired["outcome"], expired["hide_ok"],
                          expired["api_visible_after_hide"], expired["texture_cleared"]),
                         (1, start + 250 * MS, "expired_before_show", True, False, True))
        upload = run.calls("SetOverlayRaw")[0]
        self.assertGreater(upload["end_ns"], expired["deadline_ns"])
        self.assertGreaterEqual(expired["observed_ns"], upload["end_ns"])
        self.assertEqual(run.calls("ShowOverlay"), [])  # the late return initiated no show
        self.assertEqual((code, report["frames_expired_in_upload"], report["expired_frames"],
                          report["content_current"]), (0, 1, 1, False))
        self.assert_retired(run, report)

    def test_a_stalled_upload_records_the_old_tiles_expiry_and_does_not_show_its_late_successor(self):
        # Tile A is up with 300 ms to live; B arrives at 100 ms with 700 ms to live and its upload stalls 1.2 s.
        run = Run(self, FAKE_VR_SetOverlayRaw_DELAY_MS="0,1200")
        start = time.monotonic_ns()
        run.send(tile(1, start, 300 * MS, mark=1))
        run.wait("frame_submitted")
        time.sleep(0.1)
        second = time.monotonic_ns()
        run.send(tile(2, second, 700 * MS, mark=2))
        run.wait("frame_expired", 2)
        code, report = run.finish()
        first, late = run.named("frame_expired")
        stalled = run.calls("SetOverlayRaw")[1]
        # A's expiry is recorded under A's own deadline, with how late it was noticed: the upload's return.
        self.assertEqual((first["frame_id"], first["deadline_ns"], first["outcome"]), (1, start + 300 * MS, "replaced"))
        self.assertNotIn("hide_returned_ns", first)
        self.assertGreaterEqual(first["observed_ns"], stalled["end_ns"])
        self.assertGreater(first["observed_ns"] - first["deadline_ns"], 800 * MS)  # the stall, stated, not hidden
        # B's deadline passed during its own upload: it is withdrawn, never shown by a show call.
        self.assertEqual((late["frame_id"], late["deadline_ns"], late["outcome"], late["hide_ok"],
                          late["api_visible_after_hide"]), (2, second + 700 * MS, "expired_before_show", True, False))
        shows = run.calls("ShowOverlay")
        self.assertEqual(len(shows), 1)
        self.assertLess(shows[0]["end_ns"], stalled["start_ns"])
        hide = [call for call in run.calls("HideOverlay") if call["start_ns"] >= stalled["end_ns"]][0]
        self.assertLess(hide["end_ns"] - stalled["end_ns"], MARGIN_NS)  # taken down as soon as the call returned
        self.assertEqual((code, report["expired_frames"], report["frames_expired_in_upload"]), (0, 2, 1))
        self.assert_retired(run, report)

    def test_a_slow_hide_is_visible_in_the_record(self):
        # The second HideOverlay (the first is the connect's) takes 600 ms.
        run = Run(self, FAKE_VR_HideOverlay_DELAY_MS="0,600")
        start = time.monotonic_ns()
        run.send(tile(1, start, 200 * MS))
        expired = run.wait("frame_expired")
        code, report = run.finish()
        self.assertLess(expired["observed_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
        self.assertGreaterEqual(expired["hide_returned_ns"] - expired["hide_called_ns"], 600 * MS)
        self.assertGreaterEqual(expired["hide_returned_ns"] - expired["deadline_ns"], 600 * MS)
        self.assertEqual((expired["hide_ok"], expired["api_visible_after_hide"]), (True, False))
        self.assert_retired(run, report)
        self.assertEqual(code, 0)

    def test_a_failing_hide_and_an_overlay_that_stays_visible_end_the_run_after_the_record(self):
        for fake, error in (({"FAKE_VR_HideOverlay_FAIL": "2"}, "HideOverlay:VROverlayError_RequestFailed"),
                            ({"FAKE_VR_STICKY_VISIBLE": "1"}, "overlay_visible_after_hide")):
            with self.subTest(error=error):
                run = Run(self, **fake)
                run.send(tile(1, time.monotonic_ns(), 200 * MS))
                expired = run.wait("frame_expired")
                code, report = run.finish(stop=False)
                self.assertEqual((expired["hide_ok"], expired["api_visible_after_hide"]),
                                 (error == "overlay_visible_after_hide", True))
                if not expired["hide_ok"]:
                    self.assertEqual(expired["hide_error"], "VROverlayError_RequestFailed")
                self.assertEqual((code, report["status"], report["error"]), (1, "error", error))
                # Cleanup still runs every retirement call, and the overlay is destroyed.
                self.assertTrue(report["overlay_retirement_attempted"])
                self.assertEqual([call["call"] for call in run.calls()][-3:],
                                 ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
                self.assertTrue(report["destroy_returned_ok"])
                self.assertEqual(len(run.calls("ShowOverlay")), 1)

    def test_only_a_newer_observation_renews_a_deadline(self):
        # A producer whose send loop outlives its detector: the same picture and the same observation, with a
        # new identity every 150 ms and the longest deadline that observation allows. The tile goes when the
        # observation is one maximum display age old, whatever is still being sent.
        run = Run(self)
        start = time.monotonic_ns()
        for frame_id in range(1, 11):
            if run.process.poll() is not None:
                break
            run.send(tile(frame_id, start, MAX_DISPLAY_AGE_NS, mark=1))
            time.sleep(0.15)
        expired = run.wait("frame_expired")
        code, report = run.finish()
        self.assertEqual((expired["deadline_ns"], expired["frame_observed_ns"], expired["outcome"]),
                         (start + MAX_DISPLAY_AGE_NS, start, "hidden"))
        self.assertLess(expired["hide_returned_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
        self.assertEqual((code, report["expired_frames"], report["observed_frames_received"]), (0, 1, 10))
        self.assertEqual(report["frames_received"] + report["stale_frames_dropped"], 10)
        self.assertGreaterEqual(report["stale_frames_dropped"], 2)  # the copies sent after the deadline
        shows = run.calls("ShowOverlay")
        self.assertLess(max(call["end_ns"] for call in shows), expired["deadline_ns"])
        self.assert_retired(run, report)
        # The same producer claiming a longer life for that observation, or re-dating an identity, is stopped.
        for build, error in (
                (lambda now: observed_packet_unchecked(2, now - 600 * MS, now + 600 * MS), "invalid_frame_expiry"),
                (lambda now: tile(1, now - 100 * MS, 900 * MS), "frame_identity_changed"),
                (lambda now: tile(2, now + OBSERVATION_FUTURE_TOLERANCE_NS + 300 * MS, 500 * MS),
                 "frame_observed_in_future")):
            with self.subTest(error=error):
                run = Run(self)
                first = time.monotonic_ns()
                run.send(tile(1, first, 900 * MS))
                run.wait("frame_submitted")
                run.send(build(time.monotonic_ns()), may_be_refused=True)
                code, report = run.finish(stop=False)
                self.assertEqual((code, report["error"], report["frames_received"]), (1, error, 1))
                self.assertEqual(len(run.calls("SetOverlayRaw")), 1)
                self.assert_retired(run, report, code=1)

    def test_a_retransmission_and_an_older_frame_change_nothing(self):
        run = Run(self)
        start = time.monotonic_ns()
        newest = tile(5, start, 400 * MS, mark=5)
        run.send(newest)
        run.wait("frame_submitted")
        run.send(newest + newest)                                      # the same frame, twice more
        run.send(tile(4, start, 900 * MS, mark=4))                     # a lower identity
        run.send(tile(6, start - 50 * MS, 900 * MS, mark=6))           # an earlier observation
        expired = run.wait("frame_expired")
        run.send(newest)                                               # and once more after its deadline
        code, report = run.finish()
        self.assertEqual((expired["frame_id"], expired["deadline_ns"]), (5, start + 400 * MS))
        self.assertEqual((code, report["frames_received"], report["frames_retransmitted"],
                          report["stale_frames_dropped"], report["expired_frames"]), (0, 1, 3, 2, 1))
        self.assertEqual([call["first"] for call in run.calls("SetOverlayRaw")], [5])
        self.assertEqual(len(run.calls("ShowOverlay")), 1)
        self.assert_retired(run, report)

    def test_end_of_input_and_sigterm_take_a_shown_tile_down(self):
        for ending in ("eof", "sigterm"):
            with self.subTest(ending=ending):
                run = Run(self)
                run.send(tile(1, time.monotonic_ns(), 900 * MS))
                run.wait("frame_submitted")
                self.assertTrue(run.calls("ShowOverlay")[0]["visible"])
                asked = time.monotonic_ns()
                if ending == "eof":
                    run.process.stdin.close()
                else:
                    run.process.send_signal(signal.SIGTERM)
                code, report = run.finish(stop=False)
                self.assertEqual((code, report["status"]), (0, "closed" if ending == "eof" else "stopped"))
                self.assertEqual(run.named("frame_expired"), [])  # taken down by cleanup, well before its deadline
                self.assert_retired(run, report)
                hide = run.calls("HideOverlay")[-1]
                self.assertLess(hide["end_ns"] - asked, POLL_NS + MARGIN_NS)

    def test_each_refused_packet_ends_the_run_with_the_overlay_retired(self):
        now = time.monotonic_ns()
        pixels = bytes(WIDTH * HEIGHT * RGBA_BYTES)
        refused = (
            (PanelSettings().encode(), "fixed_hud_refuses_settings"),
            (packet(OPCODE_FRAME, struct.pack("<II", WIDTH, HEIGHT) + pixels), "fixed_hud_requires_observed_frame"),
            (packet(OPCODE_EXPIRING_FRAME, struct.pack("<QII", now + 500 * MS, WIDTH, HEIGHT) + pixels),
             "fixed_hud_requires_observed_frame"),
            (tile(2, now, 500 * MS, width=HEIGHT, height=WIDTH), "fixed_hud_frame_size"),
            (tile(2, now, 500 * MS, width=WIDTH, height=HEIGHT - 1), "fixed_hud_frame_size"),
            (packet(6, bytes(8)), "invalid_packet_size"),
            (packet(7), "invalid_packet_header"),
            (visibility_packet(1, True)[:-4] + bytes([2, 0, 0, 0]), "invalid_visibility"),
        )
        for data, error in refused:
            for shown in (False, True):
                with self.subTest(error=error, shown=shown):
                    run = Run(self)
                    if shown:
                        run.send(tile(1, time.monotonic_ns(), 900 * MS))
                        run.wait("frame_submitted")
                    run.send(data, may_be_refused=True)
                    code, report = run.finish(stop=False)
                    self.assertEqual((code, report["status"], report["error"]), (1, "error", error))
                    self.assertEqual((report["settings_received"], report["frames_received"]), (0, int(shown)))
                    self.assertEqual(len(run.calls("SetOverlayRaw")), int(shown))
                    self.assertEqual(len(run.calls("ShowOverlay")), int(shown))
                    self.assert_retired(run, report, code=1)

    def test_an_expired_arrival_is_refused_without_an_upload(self):
        run = Run(self)
        now = time.monotonic_ns()
        run.send(tile(1, now - 900 * MS, 500 * MS))
        run.send(visibility_packet(1, False))  # a later packet, so the refusal is known to have been processed
        run.wait("hide_returned")
        code, report = run.finish()
        self.assertEqual((code, report["frames_received"], report["stale_frames_dropped"], report["expired_frames"]),
                         (0, 0, 1, 0))
        self.assertEqual(run.calls("SetOverlayRaw", "ShowOverlay"), [])

    def test_show_requests_keep_an_empty_bridge_connected_and_silence_ends_it(self):
        # The stream's ten-second input timeout, with no settings heartbeat in this mode. Both bridges start
        # together: one hears nothing, the other a show request every three seconds while nothing is presented.
        silent, kept = Run(self), Run(self)
        started = time.monotonic()
        for cycle in range(1, 5):
            kept.send(visibility_packet(cycle, True))
            returned = kept.wait("show_returned", cycle)
            self.assertEqual((returned["ok"], returned["reason"], returned["call"]), (False, "no_image", "HideOverlay"))
            time.sleep(3)
        code, report = silent.finish(stop=False)
        self.assertEqual((code, report["status"], report["error"]), (1, "error", "stdin_timeout"))
        self.assertGreaterEqual(time.monotonic() - started, 9.5)
        self.assert_retired(silent, report, code=1)
        # Twelve seconds in, the kept bridge is still there, has shown and uploaded nothing, and still takes a tile.
        self.assertGreaterEqual(time.monotonic() - started, 11.5)
        self.assertIsNone(kept.process.poll())
        self.assertEqual(kept.calls("SetOverlayRaw", "ShowOverlay"), [])
        kept.send(tile(1, time.monotonic_ns(), 200 * MS))
        expired = kept.wait("frame_expired")
        self.assertEqual((expired["outcome"], expired["hide_ok"]), ("hidden", True))
        self.assertEqual(len(kept.calls("ShowOverlay")), 1)  # the keepalive left the tile free to show
        code, report = kept.finish()
        self.assertEqual((code, report["status"], report["visibility_requests_received"]), (0, "closed", 4))
        self.assert_retired(kept, report)


def observed_packet_unchecked(frame_id, observed_ns, expires_ns):
    """An observed frame the Python encoder would refuse (its deadline outlives its observation's bound)."""
    return packet(OPCODE_OBSERVED_FRAME, struct.pack(OBSERVED_FRAME_PREFIX_FORMAT, frame_id, observed_ns, expires_ns,
                                                     WIDTH, HEIGHT) + bytes(WIDTH * HEIGHT * RGBA_BYTES))


if __name__ == "__main__":
    unittest.main()
