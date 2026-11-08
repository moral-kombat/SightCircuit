"""The Python side of the bridge's visibility cycle (fallback work item F2): the client's queue and the sink's API.

Against the real bridge in ``--validate-stdin`` no runtime event can arrive, so
every hide ends in ``visibility_event_missing`` and every show returns ``ok``
false with reason ``no_image``: the fail-closed paths. The confirmed paths
(``overlay_hidden``, ``overlay_shown``) run against a scripted fake bridge,
because no runtime exists in the cloud. Nothing here is a device result.
"""
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

from sightcircuit.hud import contract
from sightcircuit.hud.contract import (HUD_CONFIG, MAX_DISPLAY_AGE_NS, VISIBILITY_EVENT_BOUND_NS, VISIBILITY_HIDE_LINES,
                                       VISIBILITY_LINES, VISIBILITY_MISSING_LINE, VISIBILITY_SHOW_LINES)
from sightcircuit.overlay import (BRIDGE_INPUT_POLL_NS, DEFAULT_BRIDGE, VISIBILITY_BRIDGE_ENDED_REASON,
                                  VISIBILITY_DEADLINE_MARGIN_NS, VISIBILITY_DEADLINE_REASON, VISIBILITY_HISTORY_CYCLES,
                                  VISIBILITY_ORDER_REASON, VISIBILITY_TERMINAL_DEADLINE_NS, FallbackCycle, OverlayClient,
                                  OverlaySink, VisibilityOutcome, VisibilityRequest, transition_outcome)

ROOT = Path(__file__).resolve().parents[1]
HIDE_REQUESTED, HIDE_RETURNED, OVERLAY_HIDDEN = VISIBILITY_HIDE_LINES
SHOW_REQUESTED, SHOW_RETURNED, OVERLAY_SHOWN = VISIBILITY_SHOW_LINES
GENEROUS_NS = 2_000_000_000  # A deadline the real bridge's own bound (250 ms plus a poll) always beats on a loaded host.


def line(event, cycle, **fields):
    return {"event": event, "cycle": cycle, "validation_only": True, **fields}


def hide_lines(cycle, terminal=OVERLAY_HIDDEN, ok=True):
    lines = [line(HIDE_REQUESTED, cycle, request_ns=100), line(HIDE_RETURNED, cycle, returned_ns=110, ok=ok, call="HideOverlay")]
    if terminal == OVERLAY_HIDDEN:
        lines.append(line(OVERLAY_HIDDEN, cycle, observed_ns=130))
    elif terminal == VISIBILITY_MISSING_LINE:
        lines.append(line(VISIBILITY_MISSING_LINE, cycle, waited_ns=260_000_000))
    return lines


class ContractGroupTests(unittest.TestCase):
    def test_the_line_groups_are_derived_from_the_one_list(self):
        self.assertEqual(VISIBILITY_HIDE_LINES + VISIBILITY_SHOW_LINES + (VISIBILITY_MISSING_LINE,), VISIBILITY_LINES)
        self.assertEqual(VISIBILITY_HIDE_LINES, ("hide_requested", "hide_returned", "overlay_hidden"))
        self.assertEqual(VISIBILITY_SHOW_LINES, ("show_requested", "show_returned", "overlay_shown"))
        self.assertEqual(VISIBILITY_MISSING_LINE, "visibility_event_missing")
        self.assertEqual(contract.VISIBILITY_HIDE_LINES, VISIBILITY_HIDE_LINES)


class DeadlineDerivationTests(unittest.TestCase):
    def test_the_terminal_deadline_is_the_bound_plus_one_bridge_poll_plus_a_margin(self):
        """A cloud default, pinned here and unmeasured on the Frame; F3's hidden-event measurement replaces the bound."""
        self.assertEqual(VISIBILITY_TERMINAL_DEADLINE_NS,
                         VISIBILITY_EVENT_BOUND_NS + BRIDGE_INPUT_POLL_NS + VISIBILITY_DEADLINE_MARGIN_NS)
        self.assertEqual((BRIDGE_INPUT_POLL_NS, VISIBILITY_DEADLINE_MARGIN_NS, VISIBILITY_TERMINAL_DEADLINE_NS),
                         (20_000_000, 50_000_000, 320_000_000))
        # The margin is at least two bridge polls, and the whole stays under the fastest costed cycle (500 ms).
        self.assertGreaterEqual(VISIBILITY_DEADLINE_MARGIN_NS, 2 * BRIDGE_INPUT_POLL_NS)
        self.assertLess(VISIBILITY_TERMINAL_DEADLINE_NS, 500_000_000)
        # The poll period is the sensing unit's poll(2) timeout, which is not in the generated header.
        sensing = (ROOT / "tools/overlay_bridge_sensing.cpp").read_text()
        polls = re.findall(r"poll\(&input,\s*1,\s*(\d+)\)", sensing)
        self.assertEqual(polls, [str(BRIDGE_INPUT_POLL_NS // 1_000_000)])

    def test_a_request_carries_its_default_deadline(self):
        request = VisibilityRequest(3, False, 1_000)
        self.assertEqual(request.deadline_ns, 1_000 + VISIBILITY_TERMINAL_DEADLINE_NS)


class TransitionOutcomeTests(unittest.TestCase):
    """The pure reading of a cycle's lines; the client and the sink build on it."""

    def test_a_hide_is_confirmed_by_its_event_after_its_own_lines(self):
        outcome = transition_outcome(5, False, hide_lines(5))
        self.assertIsInstance(outcome, VisibilityOutcome)
        self.assertEqual((outcome.cycle, outcome.visible, outcome.confirmed, outcome.terminal, outcome.reason),
                         (5, False, True, OVERLAY_HIDDEN, None))
        self.assertEqual((outcome.request_ns, outcome.returned_ns, outcome.observed_ns, outcome.waited_ns, outcome.call),
                         (100, 110, 130, None, "HideOverlay"))
        self.assertEqual(len(outcome.lines), 3)

    def test_a_missing_line_fails_the_hide_with_its_wait(self):
        outcome = transition_outcome(5, False, hide_lines(5, VISIBILITY_MISSING_LINE))
        self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason, outcome.observed_ns, outcome.waited_ns),
                         (False, VISIBILITY_MISSING_LINE, VISIBILITY_MISSING_LINE, None, 260_000_000))

    def test_a_returned_line_with_ok_false_ends_the_request_with_its_reason(self):
        lines = [line(HIDE_REQUESTED, 5, request_ns=1),
                 line(HIDE_RETURNED, 5, returned_ns=2, ok=False, call="HideOverlay", reason="HideOverlay:VROverlayError_InvalidHandle")]
        outcome = transition_outcome(5, False, lines)
        self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason),
                         (False, HIDE_RETURNED, "HideOverlay:VROverlayError_InvalidHandle"))
        self.assertIsNone(transition_outcome(5, False, lines[:1]))  # requested alone: pending

    def test_a_pending_request_is_none_until_ended_names_why_no_line_can_come(self):
        pending = hide_lines(5)[:2]
        self.assertIsNone(transition_outcome(5, False, pending))
        self.assertIsNone(transition_outcome(5, False, []))
        outcome = transition_outcome(5, False, pending, ended=VISIBILITY_DEADLINE_REASON)
        self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason, outcome.request_ns, outcome.returned_ns),
                         (False, None, VISIBILITY_DEADLINE_REASON, 100, 110))
        self.assertEqual(transition_outcome(5, False, [], ended="Overlay bridge pipe closed").reason,
                         "Overlay bridge pipe closed")

    def test_the_show_reads_its_own_lines_and_not_the_hides_missing_line(self):
        # The real validation bridge's sequence: the hide misses, the show returns no_image and later misses too.
        lines = hide_lines(5, VISIBILITY_MISSING_LINE) + [
            line(SHOW_REQUESTED, 5, request_ns=500), line(SHOW_RETURNED, 5, returned_ns=510, ok=False, call="none", reason="no_image"),
            line(VISIBILITY_MISSING_LINE, 5, waited_ns=250_000_000)]
        show = transition_outcome(5, True, lines)
        self.assertEqual((show.confirmed, show.terminal, show.reason, show.request_ns, show.returned_ns, show.call),
                         (False, SHOW_RETURNED, "no_image", 500, 510, "none"))
        self.assertEqual(len(show.lines), 2)
        hide = transition_outcome(5, False, lines)
        self.assertEqual((hide.terminal, hide.waited_ns), (VISIBILITY_MISSING_LINE, 260_000_000))
        self.assertIsNone(transition_outcome(5, True, hide_lines(5, VISIBILITY_MISSING_LINE)))  # no show yet

    def test_a_show_is_confirmed_by_overlay_shown(self):
        lines = hide_lines(5) + [line(SHOW_REQUESTED, 5, request_ns=500),
                                 line(SHOW_RETURNED, 5, returned_ns=510, ok=True, call="ShowOverlay"),
                                 line(OVERLAY_SHOWN, 5, observed_ns=530)]
        show = transition_outcome(5, True, lines)
        self.assertEqual((show.confirmed, show.terminal, show.observed_ns, show.call), (True, OVERLAY_SHOWN, 530, "ShowOverlay"))
        self.assertTrue(transition_outcome(5, False, lines).confirmed)

    def test_the_hides_lines_end_where_the_shows_begin(self):
        # A missing line after show_requested is the show's, so the hide stays pending (and then fails by deadline).
        lines = hide_lines(5)[:2] + [line(SHOW_REQUESTED, 5, request_ns=500), line(VISIBILITY_MISSING_LINE, 5, waited_ns=10)]
        self.assertIsNone(transition_outcome(5, False, lines))
        self.assertEqual(transition_outcome(5, True, lines).reason, VISIBILITY_MISSING_LINE)

    def test_an_event_without_the_request_and_return_lines_does_not_confirm(self):
        for lines in ([line(OVERLAY_HIDDEN, 5, observed_ns=130)],
                      [line(HIDE_REQUESTED, 5, request_ns=100), line(OVERLAY_HIDDEN, 5, observed_ns=130)],
                      hide_lines(5, ok=False)[:1] + [line(HIDE_RETURNED, 5, returned_ns=110, ok=True, call="HideOverlay"),
                                                      line(OVERLAY_HIDDEN, 5, observed_ns="130")]):
            with self.subTest(lines=[entry["event"] for entry in lines]):
                outcome = transition_outcome(5, False, lines)
                self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason, outcome.observed_ns),
                                 (False, OVERLAY_HIDDEN, VISIBILITY_ORDER_REASON, None))

    def test_the_cycle_record_exposes_the_confirmed_hidden_time_only(self):
        confirmed = transition_outcome(5, False, hide_lines(5))
        missing = transition_outcome(5, False, hide_lines(5, VISIBILITY_MISSING_LINE))
        self.assertEqual(FallbackCycle(5, hide=confirmed).hidden_ns, 130)
        self.assertIsNone(FallbackCycle(5, hide=missing).hidden_ns)
        self.assertIsNone(FallbackCycle(5).hidden_ns)
        self.assertTrue(FallbackCycle(5, hide=confirmed).hidden)
        self.assertFalse(FallbackCycle(5, hide=confirmed, show=missing).hidden)


class ClientQueueTests(unittest.TestCase):
    def test_lines_are_kept_by_cycle_and_the_oldest_cycles_drop_past_the_history_bound(self):
        client = OverlayClient()
        for cycle in range(VISIBILITY_HISTORY_CYCLES + 2):
            for message in hide_lines(cycle, VISIBILITY_MISSING_LINE):
                client._keep_visibility_line(message)
        self.assertEqual(len(client.visibility_lines), VISIBILITY_HISTORY_CYCLES)
        self.assertEqual(client.visibility_cycles_dropped, 2)
        self.assertNotIn(0, client.visibility_lines)
        self.assertEqual(len(client.cycle_lines(VISIBILITY_HISTORY_CYCLES + 1)), 3)
        self.assertEqual(client.cycle_lines(0), ())
        self.assertEqual(client.visibility_outcome(VISIBILITY_HISTORY_CYCLES + 1, False).reason, VISIBILITY_MISSING_LINE)
        self.assertIsNone(client.visibility_outcome(VISIBILITY_HISTORY_CYCLES + 1, True))
        with self.assertRaises(RuntimeError):
            client._keep_visibility_line({"event": OVERLAY_HIDDEN, "cycle": "7", "observed_ns": 1})
        with self.assertRaises(ValueError):
            client.cycle_lines(-1)

    def test_a_request_needs_a_running_client(self):
        with self.assertRaises(RuntimeError):
            OverlayClient().request_visibility(1, False)
        with self.assertRaises(ValueError):
            OverlayClient().request_visibility(2 ** 32, False)


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Build scripts/build_overlay_bridge.sh first")
class RealBridgeCycleTests(unittest.TestCase):
    """The validation bridge: real transport, real lines, no runtime, so every cycle fails closed."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(self.temporary.name) / "absent.vrpath"))
        self.sink = OverlaySink(hud=HUD_CONFIG, client_factory=lambda **kw: OverlayClient(env=self.env, **kw))
        self.sink.open()
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            return self.sink.close()

    def tearDown(self):
        self.close()

    def test_a_hide_and_a_show_each_end_in_their_fail_closed_line(self):
        sink, client = self.sink, self.sink.client
        started = time.monotonic_ns()
        hide = sink.hide(1, deadline_ns=started + GENEROUS_NS)
        self.assertEqual((hide.cycle, hide.visible, hide.confirmed, hide.terminal, hide.reason),
                         (1, False, False, VISIBILITY_MISSING_LINE, VISIBILITY_MISSING_LINE))
        self.assertEqual(hide.call, "none")
        self.assertIsNone(hide.observed_ns)
        self.assertIsNone(sink.cycle.hidden_ns)
        self.assertTrue(sink.cycle.hidden)
        self.assertFalse(sink.panel_visible)
        self.assertGreaterEqual(hide.waited_ns, VISIBILITY_EVENT_BOUND_NS)
        self.assertLessEqual(started, hide.request_ns)
        self.assertLessEqual(hide.request_ns, hide.returned_ns)
        self.assertEqual([entry["event"] for entry in hide.lines], list(VISIBILITY_HIDE_LINES[:2]) + [VISIBILITY_MISSING_LINE])
        closes_ns = time.monotonic_ns() + 60_000_000
        show = sink.show(1, closes_ns, deadline_ns=closes_ns + GENEROUS_NS)
        self.assertEqual((show.confirmed, show.terminal, show.reason, show.call), (False, SHOW_RETURNED, "no_image", "none"))
        self.assertGreaterEqual(show.request_ns, closes_ns)  # sent only after the window closed, same clock
        self.assertFalse(sink.cycle.hidden)
        self.assertTrue(sink.panel_visible)
        self.assertIs(sink.cycle.hide, hide)
        self.assertIs(sink.cycle.show, show)
        # The bridge still awaits the show's event and reports it missing; every line is kept under the cycle.
        deadline = time.monotonic() + GENEROUS_NS / 1e9
        while len(client.cycle_lines(1)) < 6 and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual([entry["event"] for entry in client.cycle_lines(1)],
                         [HIDE_REQUESTED, HIDE_RETURNED, VISIBILITY_MISSING_LINE, SHOW_REQUESTED, SHOW_RETURNED,
                          VISIBILITY_MISSING_LINE])
        self.assertEqual(client.visibility_outcome(1, True).terminal, SHOW_RETURNED)
        self.assertEqual(client.visibility_requests, 2)
        self.assertTrue(self.close(), sink.report)
        native = sink.report["native_report"]
        self.assertEqual((native["visibility_requests_received"], native["visibility_events_missing"]), (2, 2))

    def test_the_default_deadline_fails_closed_either_way_and_is_honoured_in_wall_time(self):
        # Either the bridge's missing line (its bound plus a poll) or this side's deadline ends the hide; which one
        # depends on host load, and both are the fail-closed outcome. Neither path takes longer than the deadline
        # plus a scheduling allowance.
        started = time.monotonic_ns()
        hide = self.sink.hide(4)
        elapsed = time.monotonic_ns() - started
        self.assertFalse(hide.confirmed)
        self.assertIn(hide.reason, (VISIBILITY_MISSING_LINE, VISIBILITY_DEADLINE_REASON))
        self.assertLess(elapsed, VISIBILITY_TERMINAL_DEADLINE_NS + 500_000_000)
        if hide.reason == VISIBILITY_DEADLINE_REASON:
            self.assertGreaterEqual(elapsed, VISIBILITY_TERMINAL_DEADLINE_NS)
            self.assertIsNone(hide.terminal)

    def test_the_heartbeat_repeats_the_cycles_hidden_state(self):
        # A frame is presented and the panel shown as after a result; the cycle's hide must survive the settings
        # heartbeat the writer sends every second, which the bridge applies as a visibility call too.
        client = self.sink.client
        client.set_settings(self.sink.placement(True))
        client.submit_frame(2, 1, bytes(8))
        hide = self.sink.hide(2, deadline_ns=time.monotonic_ns() + GENEROUS_NS)
        self.assertEqual(hide.reason, VISIBILITY_MISSING_LINE)
        time.sleep(1.3)
        self.assertTrue(self.close(), self.sink.report)
        native = self.sink.report["native_report"]
        self.assertIs(native["content_current"], True)
        self.assertIs(native["presentation_visible"], False)
        self.assertGreaterEqual(native["settings_received"], 3)

    def test_the_cycle_rules(self):
        sink = self.sink
        with self.assertRaisesRegex(ValueError, "hud_cycle_not_hidden"):
            sink.show(1, time.monotonic_ns())
        sink.hide(3, deadline_ns=time.monotonic_ns() + GENEROUS_NS)
        with self.assertRaisesRegex(ValueError, "hud_cycle_show_pending"):
            sink.hide(4)
        with self.assertRaisesRegex(ValueError, "hud_cycle_not_hidden"):
            sink.show(4, time.monotonic_ns())
        with self.assertRaisesRegex(ValueError, "hud_window_close_invalid"):
            sink.show(3, 1.5)
        with self.assertRaisesRegex(ValueError, "hud_window_close_too_far_ahead"):
            sink.show(3, time.monotonic_ns() + MAX_DISPLAY_AGE_NS + 100_000_000)
        sink.show(3, time.monotonic_ns(), deadline_ns=time.monotonic_ns() + GENEROUS_NS)
        with self.assertRaisesRegex(ValueError, "hud_cycle_already_shown"):
            sink.show(3, time.monotonic_ns())
        for cycle in (3, 2):
            with self.assertRaisesRegex(ValueError, "hud_cycle_not_increasing"):
                sink.hide(cycle)
        with self.assertRaises(ValueError):
            sink.hide(-1)
        with self.assertRaisesRegex(RuntimeError, "hud_sink_cycle_requires_owned_client"):
            OverlaySink(sink.client).hide(5)


@unittest.skipUnless(os.name == "posix", "The fake bridge uses a POSIX shebang")
class FakeBridgeCycleTests(unittest.TestCase):
    """A scripted bridge writes the confirmed lines the cloud's validation bridge never can, or none at all."""

    CONFIRM = ("emit(line(kind + '_returned', cycle, returned_ns=now(), ok=True, call=call))\n"
               "emit(line('overlay_hidden' if hide else 'overlay_shown', cycle, observed_ns=now()))\n")
    SILENT = "emit(line(kind + '_returned', cycle, returned_ns=now(), ok=True, call=call))\n"
    DIES = "os._exit(3)\n"
    BAD_CYCLE = "emit({'event': 'hide_returned', 'cycle': 'seven', 'returned_ns': now(), 'ok': True, 'call': call})\n"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log = Path(self.temporary.name) / "packets.log"

    def fake_bridge(self, behaviour):
        path = Path(self.temporary.name) / "fake-bridge"
        body = "\n".join("        " + row for row in behaviour.splitlines())
        path.write_text(f"""#!{sys.executable}
import json, os, struct, sys, time
HEADER = struct.Struct("<4sII")
now = time.monotonic_ns
log = open({str(self.log)!r}, "a")
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
def line(event, cycle, **fields):
    return {{"event": event, "cycle": cycle, "validation_only": True, **fields}}
def read(size):
    data = b""
    while len(data) < size:
        chunk = sys.stdin.buffer.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data
emit({{"event": "ready", "validation_only": True, "runtime_initialized": False, "frame_expiry_supported": True}})
while True:
    header = read(HEADER.size)
    if header is None:
        break
    magic, opcode, size = HEADER.unpack(header)
    payload = read(size) if size else b""
    if opcode == 3:
        break
    if opcode == 1:
        log.write("frame\\n")
    elif opcode == 2:
        log.write("settings %d\\n" % struct.unpack("<fffffI", payload)[5])
    elif opcode == 5:
        cycle, visible = struct.unpack("<II", payload)
        log.write("visibility %d %d\\n" % (cycle, visible))
        hide = visible == 0
        kind = "hide" if hide else "show"
        call = "HideOverlay" if hide else "ShowOverlay"
        emit(line(kind + "_requested", cycle, request_ns=now()))
{body}
    log.flush()
emit({{"exit_code": 0, "cleanup_ok": True, "status": "validated", "mode": "--validate-stdin",
      "validation_only": True, "runtime_initialized": False}})
""")
        path.chmod(0o755)
        return path

    def open_sink(self, behaviour):
        path = self.fake_bridge(behaviour)
        sink = OverlaySink(hud=HUD_CONFIG, client_factory=lambda **kw: OverlayClient(binary=path, **kw))
        sink.open()
        self.addCleanup(sink.close)
        return sink

    def packets(self):
        return self.log.read_text().splitlines()

    def test_confirmed_hide_and_show_with_the_packet_order_the_bridge_needs(self):
        sink = self.open_sink(self.CONFIRM)
        client = sink.client
        client.set_settings(sink.placement(True))  # as after a shown result
        time.sleep(.1)
        before = time.monotonic_ns()
        hide = sink.hide(1)
        self.assertTrue(hide.confirmed)
        self.assertEqual((hide.terminal, hide.reason, hide.call), (OVERLAY_HIDDEN, None, "HideOverlay"))
        self.assertEqual(sink.cycle.hidden_ns, hide.observed_ns)
        self.assertLessEqual(before, hide.request_ns)
        self.assertLessEqual(hide.request_ns, hide.returned_ns)
        self.assertLessEqual(hide.returned_ns, hide.observed_ns)
        self.assertLessEqual(hide.observed_ns, time.monotonic_ns())  # the bridge's clock is this one
        self.assertLess(time.monotonic_ns() - before, VISIBILITY_TERMINAL_DEADLINE_NS)
        client.submit_frame(2, 1, bytes(8))  # the tile, while hidden
        closes_ns = time.monotonic_ns() + 80_000_000
        show = sink.show(1, closes_ns)
        self.assertTrue(show.confirmed)
        self.assertEqual((show.terminal, show.call), (OVERLAY_SHOWN, "ShowOverlay"))
        self.assertGreaterEqual(show.request_ns, closes_ns)
        time.sleep(.1)
        packets = self.packets()
        hide_at, show_at, frame_at = packets.index("visibility 1 0"), packets.index("visibility 1 1"), packets.index("frame")
        # The hide request precedes the hidden settings that keep it, the tile precedes the show request, and the
        # show request precedes the visible settings; no settings packet shows the panel while it is hidden.
        self.assertEqual(packets[hide_at + 1], "settings 0")
        self.assertLess(frame_at, show_at)
        self.assertEqual(packets[show_at + 1], "settings 1")
        self.assertNotIn("settings 1", packets[hide_at:show_at])
        self.assertEqual(len(client.cycle_lines(1)), 6)
        self.assertEqual(client.visibility_outcome(1, False), hide)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV required for sink image conversion")
    def test_submit_keeps_the_panel_down_while_a_cycle_is_hidden(self):
        from sightcircuit.hud.contract import HudOutput
        import numpy as np

        sink = self.open_sink(self.CONFIRM)
        sink.hide(1)

        class Processed:
            image = np.zeros((HUD_CONFIG.height_px, HUD_CONFIG.width_px, 3), dtype=np.uint8)
            hud = HudOutput(rows=(), config=HUD_CONFIG)
            expires_ns = None

        sink.submit(Processed())
        self.assertFalse(sink.client.settings.visible)
        sink.show(1, time.monotonic_ns())
        self.assertTrue(sink.client.settings.visible)
        sink.submit(Processed())
        self.assertTrue(sink.client.settings.visible)
        time.sleep(.1)
        packets = self.packets()
        self.assertNotIn("settings 1", packets[packets.index("visibility 1 0"):packets.index("visibility 1 1")])

    def test_a_bridge_that_never_writes_the_terminal_line_fails_the_cycle_at_the_deadline(self):
        sink = self.open_sink(self.SILENT)
        started = time.monotonic_ns()
        hide = sink.hide(1)
        elapsed = time.monotonic_ns() - started
        self.assertEqual((hide.confirmed, hide.terminal, hide.reason), (False, None, VISIBILITY_DEADLINE_REASON))
        self.assertEqual([entry["event"] for entry in hide.lines], [HIDE_REQUESTED, HIDE_RETURNED])
        self.assertIsNone(sink.cycle.hidden_ns)
        self.assertGreaterEqual(elapsed, VISIBILITY_TERMINAL_DEADLINE_NS)
        self.assertLess(elapsed, VISIBILITY_TERMINAL_DEADLINE_NS + 500_000_000)
        # An explicit deadline is honoured too, and the show after a failed hide goes out at once.
        started = time.monotonic_ns()
        show = sink.show(1, started, deadline_ns=started + 100_000_000)
        self.assertEqual(show.reason, VISIBILITY_DEADLINE_REASON)
        self.assertLess(time.monotonic_ns() - started, 600_000_000)
        self.assertIsNone(sink.client.error)

    def test_a_bridge_that_ends_mid_request_fails_the_cycle_at_once(self):
        sink = self.open_sink(self.DIES)
        started = time.monotonic_ns()
        hide = sink.hide(1)
        self.assertFalse(hide.confirmed)
        self.assertIsNone(hide.terminal)
        self.assertIn(hide.reason, (sink.client.error, VISIBILITY_BRIDGE_ENDED_REASON))
        self.assertIsNotNone(sink.client.error)
        self.assertLess(time.monotonic_ns() - started, VISIBILITY_TERMINAL_DEADLINE_NS)
        with self.assertRaises(RuntimeError):
            sink.show(1, time.monotonic_ns())
        self.assertFalse(sink.close())

    def test_a_line_with_an_invalid_cycle_is_a_client_failure(self):
        sink = self.open_sink(self.BAD_CYCLE)
        hide = sink.hide(1)
        self.assertFalse(hide.confirmed)
        self.assertEqual(sink.client.error, "overlay_visibility_line_invalid")
        self.assertEqual(hide.reason, "overlay_visibility_line_invalid")

