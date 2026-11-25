"""The bridge's second fixed geometry, the box panel (work-order item B3), on the fake runtime and in validation.

Three kinds of test, none of them a device or a display:

* ``BoxPanelRuntimeTests`` is the strip's whole fake-runtime suite
  (``tests/test_fixed_hud_runtime.py``, unedited) run a second time with the
  bridge launched as ``--fixed-hud --layout head-fixed-box-panel-v1`` and
  every tile a 512 x 512 megabyte. The safety behaviour (the hide at a
  deadline, a slow or failing hide, a stalled upload, renewal, refusals,
  end of input, SIGTERM, the input timeout) is one code path for both
  layouts, and this is the proof: the same assertions hold.
* ``BoxPanelBridgeTests`` adds what only the box panel raises: the megabyte
  in flight, the bytes handed to the overlay call, the report block, the
  size refusals, five updates a second, and the launcher's death.
* ``BoxPanelClientTests`` and ``BoxPanelPermissionTests`` are the Python
  end: ``ObservedFrameClient`` with a named layout, and that no real output
  can be started with the box panel.

Every time printed or asserted here is this cloud host's, never a Frame's.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from sightcircuit import gates, observed_frames
from sightcircuit.hud.contract import (BOX_PANEL_CONFIG, FIXED_LAYOUTS, HUD_CONFIG, MAX_DISPLAY_AGE_NS,
                                       OBSERVATION_FUTURE_TOLERANCE_NS, RGBA_BYTES, config_digest, fixed_layout)
from sightcircuit.hud import box_scenes, native
from sightcircuit.observed_frames import ObservedFrameClient
from sightcircuit.overlay import DEFAULT_BRIDGE, observed_frame_packet, packet, visibility_packet
from sightcircuit.runtime import manifest
from tests import fake_vrclient

try:
    import numpy as np
    from sightcircuit.hud.box_render import draw_boxes, layout as box_layout
    DRAWING = True
except ImportError:
    DRAWING = False

ROOT = Path(__file__).resolve().parents[1]
PANEL, STRIP = BOX_PANEL_CONFIG, HUD_CONFIG
LAYOUT_ARGUMENTS = ("--layout", PANEL.layout_id)
W, H = PANEL.width_px, PANEL.height_px
UPDATE_BYTES = W * H * RGBA_BYTES
MS = 1_000_000
ORIENTATION_SCENE_SHA256 = "b4219d8d90e6de1fe25d40b0a100e0eb30ac09406a40bbb92fb13bd320198d0e"
# The generated header's digest with the box panel in it. Before item B3 it was
# 70663bdcb8b2fecb0f653666852142b2eaf2ff342e24874b53e11c66b04f6c98 (the strip, marker, packets and limits alone).
HEADER_CONTRACT_DIGEST = "8192f6451d50b1966cca48e0f3a1b580fc2db6c0610661170a742b13b5860531"
FIXED_HUD_KEYS = {"config_id", "config_digest", "width", "height", "panel_width_m", "distance_m", "x_m", "y_m",
                  "opacity", "max_display_age_ns", "observation_future_tolerance_ns", "settings_accepted",
                  "frames_without_deadline_accepted", "frames_without_observation_accepted"}
RUNTIME = DEFAULT_BRIDGE.is_file() and fake_vrclient.available()
NEEDS_RUNTIME = "Needs scripts/build_overlay_bridge.sh, its SDK checkout and g++"


def strip_suite_on(layout):
    """``tests/test_fixed_hud_runtime.py`` loaded a second time, its bridge launched with ``layout`` and its tiles sized for it.

    The module's source is executed unchanged under another name. Three of
    its globals are then pointed at the layout: the configuration its
    assertions read (both configurations name their size and placement
    alike), the tile size, and ``subprocess``, replaced by a stand-in whose
    ``Popen`` adds ``--layout <id>`` to the bridge's command line.
    """
    source = ROOT / "tests/test_fixed_hud_runtime.py"
    spec = importlib.util.spec_from_file_location(f"tests._fixed_hud_runtime_on_{layout.width_px}", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def popen(arguments, **options):
        assert list(arguments) == [str(DEFAULT_BRIDGE), "--fixed-hud"], arguments
        return subprocess.Popen([*arguments, "--layout", layout.layout_id], **options)

    strip_tile = module.tile

    def tile(frame_id, observed_ns, life_ns, mark=0, *, width=layout.width_px, height=layout.height_px):
        return strip_tile(frame_id, observed_ns, life_ns, mark, width=width, height=height)

    module.subprocess = types.SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL)
    module.HUD_CONFIG, module.WIDTH, module.HEIGHT, module.tile = layout, layout.width_px, layout.height_px, tile
    return module


strip_suite = strip_suite_on(PANEL)
Run, tile = strip_suite.Run, strip_suite.tile
POLL_NS, MARGIN_NS = strip_suite.POLL_NS, strip_suite.MARGIN_NS


class BoxPanelRuntimeTests(strip_suite.FixedHudRuntimeTests):
    """Every test of the strip's fake-runtime suite, on the box panel; one test is restated for a square tile."""

    def test_the_suite_really_ran_on_the_box_panel(self):
        run = Run(self)
        self.assertEqual(run.wait("ready")["layout_id"], PANEL.layout_id)
        run.send(tile(1, time.monotonic_ns(), 300 * MS, mark=3))
        run.wait("frame_presented")
        code, report = run.finish()
        self.assertEqual((code, report["fixed_hud"]["config_id"], report["fixed_hud"]["width"]), (0, PANEL.layout_id, W))
        upload, = run.calls("SetOverlayRaw")
        self.assertEqual((upload["width"], upload["height"], upload["depth"], upload["first"]), (W, H, 4, 3))

    def test_each_refused_packet_ends_the_run_with_the_overlay_retired(self):
        # The strip's cases, with its swapped-sides tile (a square has none) replaced by the sizes a box-panel
        # run refuses: one row short, the half-size square, and the strip's own tile, which is named as such.
        now = time.monotonic_ns()
        pixels = bytes(UPDATE_BYTES)
        refused = (
            (strip_suite.PanelSettings().encode(), "fixed_hud_refuses_settings"),
            (packet(strip_suite.OPCODE_FRAME, strip_suite.struct.pack("<II", W, H) + pixels),
             "fixed_hud_requires_observed_frame"),
            (packet(strip_suite.OPCODE_EXPIRING_FRAME, strip_suite.struct.pack("<QII", now + 500 * MS, W, H) + pixels),
             "fixed_hud_requires_observed_frame"),
            (tile(2, now, 500 * MS, width=W, height=H - 1), "fixed_hud_frame_size"),
            (tile(2, now, 500 * MS, width=W - 1, height=H), "fixed_hud_frame_size"),
            (tile(2, now, 500 * MS, width=W // 2, height=H // 2), "fixed_hud_frame_size"),
            (tile(2, now, 500 * MS, width=1, height=1), "fixed_hud_frame_size"),
            (tile(2, now, 500 * MS, width=STRIP.width_px, height=STRIP.height_px), "frame_for_other_layout"),
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
                    self.assertEqual(len(run.calls("SetOverlayRaw")), int(shown))  # the refused frame: never uploaded
                    self.assertEqual(len(run.calls("ShowOverlay")), int(shown))
                    self.assert_retired(run, report, code=1)


def percentile(values, share):
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * share // 100) - 1)]


@unittest.skipUnless(RUNTIME, NEEDS_RUNTIME)
class BoxPanelBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.runtime_env, _ = fake_vrclient.runtime(cls.directory.name)

    assert_retired = strip_suite.FixedHudRuntimeTests.assert_retired

    def test_a_megabyte_in_flight_cannot_hold_a_tile_past_its_deadline(self):
        # The next panel is partly written (in its prefix, well inside its megabyte, and seven bytes short of its
        # end) when the shown tile's deadline passes. The bridge notices a deadline on its next 20 ms input poll, so
        # how late the hide is depends on where in a poll the deadline falls: anything from nothing to a whole poll.
        # A life that is a whole number of polls always lands at the same place and measures only that place, so
        # the lives here are spread across a poll and the bound is asserted over all of them.
        late = []
        for split in (12 + 32 + 1, 300_000, UPDATE_BYTES - 7):
            for phase_ns in (0, 5 * MS, 10 * MS + MS // 2, 16 * MS):
                with self.subTest(split=split, phase_ms=phase_ns / MS):
                    run = Run(self)
                    start = time.monotonic_ns()
                    run.send(tile(1, start, 200 * MS + phase_ns))
                    run.wait("frame_submitted")
                    following = tile(2, time.monotonic_ns(), 600 * MS, mark=2)
                    run.send(following[:split])
                    expired = run.wait("frame_expired")
                    self.assertEqual((expired["frame_id"], expired["outcome"], expired["hide_ok"],
                                      expired["api_visible_after_hide"]), (1, "hidden", True, False))
                    self.assertGreaterEqual(expired["observed_ns"], expired["deadline_ns"])
                    self.assertLess(expired["observed_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
                    self.assertLess(expired["hide_returned_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
                    late.append(expired["hide_returned_ns"] - expired["deadline_ns"])
                    self.assertEqual(len(run.calls("SetOverlayRaw")), 1)  # the part-arrived panel was not uploaded
                    run.send(following[split:])
                    second = run.wait("frame_expired", 2)
                    self.assertEqual((second["frame_id"], second["outcome"]), (2, "hidden"))
                    code, report = run.finish()
                    self.assertEqual((code, report["frames_received"], report["expired_frames"]), (0, 2, 2))
                    self.assert_retired(run, report)
        # More than one place in the poll was sampled: the latenesses differ by milliseconds, not by noise.
        self.assertGreater(max(late) - min(late), 2 * MS, late)
        if os.environ.get("SIGHTCIRCUIT_PRINT_MEASUREMENTS"):
            print(f"\ndeadline to hide returned, a panel part-arrived, {len(late)} runs on this host: min "
                  f"{min(late) / 1e6:.2f} ms, median {statistics.median(late) / 1e6:.2f} ms, max {max(late) / 1e6:.2f} ms",
                  file=sys.stderr)

    def test_a_writer_that_stalls_mid_panel_delays_nothing_but_its_own_panel(self):
        # The sender stops for half a second in the middle of a megabyte. The tile that is up is hidden at its own
        # deadline meanwhile, and the stalled panel, once whole, is presented because its own deadline still stands.
        run = Run(self)
        start = time.monotonic_ns()
        run.send(tile(1, start, 250 * MS, mark=1))
        run.wait("frame_presented")
        observed = time.monotonic_ns()
        following = tile(2, observed, 950 * MS, mark=2)
        run.send(following[:400_000])
        time.sleep(0.5)
        expired = run.wait("frame_expired")
        self.assertEqual((expired["frame_id"], expired["outcome"], expired["deadline_ns"]), (1, "hidden", start + 250 * MS))
        self.assertLess(expired["hide_returned_ns"] - expired["deadline_ns"], POLL_NS + MARGIN_NS)
        self.assertEqual(len(run.calls("SetOverlayRaw")), 1)
        run.send(following[400_000:])
        presented = run.wait("frame_presented", 2)
        self.assertEqual((presented["frame_id"], presented["visible"]), (2, True))
        self.assertLess(presented["presented_ns"], observed + 950 * MS)
        second = run.wait("frame_expired", 2)
        self.assertEqual((second["frame_id"], second["outcome"], second["deadline_ns"]), (2, "hidden", observed + 950 * MS))
        code, report = run.finish()
        self.assertEqual((code, report["frames_received"], report["expired_frames"]), (0, 2, 2))
        self.assert_retired(run, report)

    def test_a_panel_that_arrives_whole_only_after_its_deadline_is_refused_without_an_upload(self):
        run = Run(self)
        observed = time.monotonic_ns()
        late = tile(1, observed, 250 * MS, mark=1)
        run.send(late[:400_000])
        time.sleep(0.5)
        run.send(late[400_000:])
        refused = run.wait("frame_refused")
        self.assertEqual((refused["frame_id"], refused["reason"]), (1, "expired_on_arrival"))
        code, report = run.finish()
        self.assertEqual((code, report["frames_received"], report["stale_frames_dropped"], report["expired_frames"]),
                         (0, 0, 1, 0))
        self.assertEqual(run.calls("SetOverlayRaw", "ShowOverlay"), [])
        self.assert_retired(run, report)

    def test_thirty_panels_written_back_to_back_are_each_presented_in_order(self):
        run = Run(self)
        now = time.monotonic_ns()
        run.send(b"".join(tile(frame_id, now + frame_id, 900 * MS, mark=frame_id) for frame_id in range(1, 31)))
        run.wait("frame_presented", 30)
        code, report = run.finish()
        self.assertEqual([line["frame_id"] for line in run.named("frame_presented")], list(range(1, 31)))
        self.assertEqual(run.named("frame_refused"), [])
        self.assertEqual((code, report["frames_received"], report["frames_submitted"], report["stale_frames_dropped"]),
                         (0, 30, 30, 0))
        self.assertEqual([call["first"] for call in run.calls("SetOverlayRaw")], list(range(1, 31)))
        self.assert_retired(run, report)

    def test_input_that_ends_in_the_middle_of_a_panel_ends_the_run_with_the_tile_taken_down(self):
        run = Run(self)
        run.send(tile(1, time.monotonic_ns(), 900 * MS, mark=1))
        run.wait("frame_presented")
        self.assertTrue(run.calls("ShowOverlay")[0]["visible"])
        run.send(tile(2, time.monotonic_ns(), 900 * MS, mark=2)[:600_000])
        ended = time.monotonic_ns()
        run.process.stdin.close()
        code, report = run.finish(stop=False)
        self.assertEqual((code, report["status"], report["error"], report["frames_received"]),
                         (1, "error", "truncated_packet", 1))
        self.assertEqual(len(run.calls("SetOverlayRaw")), 1)
        self.assertLess(run.calls("HideOverlay")[-1]["end_ns"] - ended, POLL_NS + MARGIN_NS)
        self.assert_retired(run, report, code=1)

    def test_a_hide_request_written_behind_a_megabyte_is_carried_out_once_the_frame_is_up(self):
        # In-band order: the frame, then the hide. The hide's delay behind the megabyte is the frame's read and
        # upload; on this host that is a few milliseconds, and the bound asserted is this host's scheduling margin.
        run = Run(self)
        delays = []
        for cycle in range(1, 6):
            written = time.monotonic_ns()
            run.send(tile(cycle, written, 900 * MS, mark=cycle) + visibility_packet(cycle, False))
            returned = run.wait("hide_returned", cycle)
            self.assertEqual((returned["cycle"], returned["ok"], returned["call"]), (cycle, True, "HideOverlay"))
            delays.append(returned["returned_ns"] - written)
            self.assertLess(delays[-1], MARGIN_NS)
            run.send(visibility_packet(100 + cycle, True))
            run.wait("show_returned", cycle)
        code, report = run.finish()
        self.assertEqual((code, report["frames_received"], report["expired_frames"]), (0, 5, 0))
        # Each frame was uploaded and presented before its hide, and nothing was shown between a hide and the
        # show request that followed it (the fourth call of each cycle).
        names = [call["call"] for call in run.calls("SetOverlayRaw", "ShowOverlay", "HideOverlay")][1:21]
        self.assertEqual(names, ["SetOverlayRaw", "ShowOverlay", "HideOverlay", "ShowOverlay"] * 5)
        self.assert_retired(run, report)

    def test_five_updates_a_second_are_each_presented_and_the_tile_stays_up(self):
        run = Run(self)
        period, frames = 0.2, 10
        latencies, writes = [], []
        for frame_id in range(1, frames + 1):
            began = time.monotonic()
            observed = time.monotonic_ns()
            data = tile(frame_id, observed, 600 * MS, mark=frame_id)
            before = time.monotonic_ns()
            run.send(data)
            writes.append(time.monotonic_ns() - before)
            presented = run.wait("frame_presented", frame_id)
            self.assertEqual((presented["frame_id"], presented["visible"], presented["deadline_ns"]),
                             (frame_id, True, observed + 600 * MS))
            latencies.append(presented["presented_ns"] - before)
            time.sleep(max(0.0, period - (time.monotonic() - began)))
        expired = run.wait("frame_expired")
        code, report = run.finish()
        self.assertEqual((expired["frame_id"], expired["outcome"]), (frames, "hidden"))
        self.assertEqual((code, report["frames_received"], report["frames_submitted"], report["expired_frames"],
                          report["stale_frames_dropped"], report["frames_expired_in_upload"]), (0, frames, frames, 1, 0, 0))
        uploads = run.calls("SetOverlayRaw")
        self.assertEqual([(call["width"], call["height"], call["depth"], call["first"]) for call in uploads],
                         [(W, H, 4, frame_id) for frame_id in range(1, frames + 1)])
        # One hide between the first show and cleanup: the last frame's deadline. No update hid the tile.
        hides = [call for call in run.calls("HideOverlay") if call["start_ns"] > uploads[0]["start_ns"]]
        self.assertEqual(len(hides), 2)  # the deadline's, and cleanup's
        self.assertLess(max(latencies), MARGIN_NS)
        self.assert_retired(run, report)
        if os.environ.get("SIGHTCIRCUIT_PRINT_MEASUREMENTS"):
            print(f"\nbox panel at 5 Hz on this host: write median {statistics.median(writes) / 1e6:.3f} ms, "
                  f"max {max(writes) / 1e6:.3f} ms; write to presented median "
                  f"{statistics.median(latencies) / 1e6:.3f} ms, p95 {percentile(latencies, 95) / 1e6:.3f} ms, "
                  f"max {max(latencies) / 1e6:.3f} ms", file=sys.stderr)

    @unittest.skipUnless(DRAWING, "NumPy is required to draw the panel")
    def test_the_overlay_call_is_handed_the_renderers_bytes_unchanged_row_zero_first(self):
        image = draw_boxes(box_scenes.orientation_scene(PANEL), PANEL)
        rgba = image.tobytes()
        self.assertEqual((len(rgba), hashlib.sha256(rgba).hexdigest()), (UPDATE_BYTES, ORIENTATION_SCENE_SHA256))
        dump = Path(self.directory.name) / "handed-over.rgba"
        run = Run(self, FAKE_VR_RAW_DUMP=str(dump))
        observed = time.monotonic_ns()
        run.send(observed_frame_packet(1, observed, observed + 400 * MS, W, H, rgba))
        run.wait("frame_presented")
        code, report = run.finish()
        self.assertEqual(code, 0, report)
        upload, = run.calls("SetOverlayRaw")
        self.assertEqual((upload["width"], upload["height"], upload["depth"]), (W, H, RGBA_BYTES))
        handed = dump.read_bytes()
        # Size, bytes and order: what the overlay call received is the renderer's buffer, byte for byte. So row 0
        # of the image is the first W * 4 bytes of the call's buffer, pixels are R, G, B, A, and alpha arrives as
        # drawn. What the runtime then does with row 0 and with alpha is not something the fake shows.
        self.assertEqual(len(handed), UPDATE_BYTES)
        self.assertEqual(handed, rgba)
        received = np.frombuffer(handed, np.uint8).reshape(H, W, RGBA_BYTES)
        self.assertEqual(received[0].tobytes(), image[0].tobytes())
        self.assertEqual(set(np.unique(received[..., 3]).tolist()), {0, 255})
        self.assertFalse(received[received[..., 3] == 0].any())  # transparent pixels carry no colour
        # The overlay's own opacity is the contract's 1.0, set once: transparency is per pixel alone.
        alpha, = [call for call in fake_vrclient.calls(run.log) if call["call"] == "SetOverlayAlpha"]
        self.assertAlmostEqual(alpha["value"], 1.0, places=6)
        # In the bytes handed over, the box named "top left" lies in the first rows and first columns.
        drawn = {entry.text: entry for entry in box_layout(box_scenes.orientation_scene(PANEL), PANEL)}
        x0, y0, x1, y1 = drawn["top left"].rect
        self.assertTrue(x1 <= W // 2 and y1 <= H // 2)
        self.assertEqual(tuple(received[y0 + 1, x0 + 1]), (*PANEL.outline_rgb, 255))
        self.assertEqual(tuple(received[(y0 + y1) // 2, (x0 + x1) // 2]), (0, 0, 0, 0))  # its inside is see-through

    def test_the_report_block_names_the_layout_the_tile_and_the_placement_applied(self):
        run = Run(self)
        ready = run.wait("ready")
        self.assertEqual(ready["layout_id"], PANEL.layout_id)
        run.send(tile(1, time.monotonic_ns(), 100 * MS))
        run.wait("frame_expired")
        code, report = run.finish()
        self.assertEqual((code, report["mode"]), (0, "--fixed-hud"))
        hud = report["fixed_hud"]
        self.assertEqual(set(hud), FIXED_HUD_KEYS)  # the strip's keys, no more: closed
        self.assertEqual((hud["config_id"], hud["config_digest"], hud["width"], hud["height"]),
                         (PANEL.layout_id, config_digest(PANEL), W, H))
        for field in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"):
            self.assertAlmostEqual(hud[field], getattr(PANEL, field), places=6, msg=field)
        self.assertEqual((hud["max_display_age_ns"], hud["observation_future_tolerance_ns"]),
                         (MAX_DISPLAY_AGE_NS, OBSERVATION_FUTURE_TOLERANCE_NS))
        self.assertEqual((hud["settings_accepted"], hud["frames_without_deadline_accepted"],
                          hud["frames_without_observation_accepted"]), (False, False, False))
        # Numbers, booleans and two closed names; nothing in the block is derived from a frame.
        for key, value in hud.items():
            self.assertIn(type(value), (int, float, bool) if key not in ("config_id", "config_digest") else (str,), key)
        self.assertNotIn("validation_layout", report)
        # The placement reported is the placement the runtime was given, once.
        placement = fake_vrclient.calls(run.log)
        width, = [call for call in placement if call["call"] == "SetOverlayWidthInMeters"]
        transform, = [call for call in placement if call["call"] == "SetOverlayTransformTrackedDeviceRelative"]
        self.assertAlmostEqual(width["value"], hud["panel_width_m"], places=5)
        self.assertEqual((round(transform["x"], 5), round(transform["y"], 5), round(transform["z"], 5)),
                         (0.0, 0.0, -0.75))

    def test_a_named_strip_is_the_strip_and_only_a_named_run_says_which_layout_it_took(self):
        strip = strip_suite_on(STRIP)
        run = strip.Run(self)
        self.assertEqual(run.wait("ready")["layout_id"], STRIP.layout_id)
        run.send(strip.tile(1, time.monotonic_ns(), 100 * MS))
        run.wait("frame_expired")
        code, named = run.finish()
        self.assertEqual(code, 0, named)
        # The same run without the argument: the report block is the same, and the ready line names no layout.
        process = subprocess.run([str(DEFAULT_BRIDGE), "--fixed-hud"], input=packet(3), capture_output=True, timeout=10,
                                 env={**os.environ, **self.runtime_env, "FAKE_VR_LOG": os.devnull})
        ready, unnamed = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertNotIn("layout_id", ready)
        self.assertEqual(named["fixed_hud"], unnamed["fixed_hud"])
        self.assertEqual(unnamed["fixed_hud"]["config_id"], STRIP.layout_id)
        # A panel-sized frame: the unnamed strip keeps the one name it has always given, the named strip says more.
        for arguments, error in (((), "fixed_hud_frame_size"), (("--layout", STRIP.layout_id), "frame_for_other_layout")):
            with self.subTest(arguments=arguments):
                now = time.monotonic_ns()
                process = subprocess.run(
                    [str(DEFAULT_BRIDGE), "--fixed-hud", *arguments], input=tile(1, now, 500 * MS), capture_output=True,
                    timeout=10, env={**os.environ, **self.runtime_env, "FAKE_VR_LOG": os.devnull})
                report = json.loads(process.stdout.splitlines()[-1])
                self.assertEqual((process.returncode, report["error"], report["frames_received"],
                                  report["hide_returned_ok"], report["destroy_returned_ok"]), (1, error, 0, True, True))

    def test_the_overlay_is_retired_when_the_launcher_is_killed_with_a_panel_up(self):
        # Nothing but the bridge is left to take the panel down: its launcher is killed outright while a panel
        # with most of a second to live is shown.
        log = Path(self.directory.name) / "orphan.jsonl"
        launcher = (
            "import subprocess, sys, time\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from sightcircuit.overlay import observed_frame_packet\n"
            f"bridge = subprocess.Popen([{str(DEFAULT_BRIDGE)!r}, '--fixed-hud', '--layout', {PANEL.layout_id!r}],\n"
            "                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)\n"
            "bridge.stdout.readline()\n"
            "now = time.monotonic_ns()\n"
            f"bridge.stdin.write(observed_frame_packet(1, now, now + 900_000_000, {W}, {H}, bytes({UPDATE_BYTES})))\n"
            "bridge.stdin.flush()\n"
            "while b'frame_presented' not in bridge.stdout.readline():\n"
            "    pass\n"
            "print(bridge.pid, flush=True)\n"
            "time.sleep(30)\n")
        process = subprocess.Popen([sys.executable, "-c", launcher], stdout=subprocess.PIPE,
                                   env={**os.environ, **self.runtime_env, "FAKE_VR_LOG": str(log)})
        self.addCleanup(lambda: (process.poll() is None and process.kill(), process.wait(5), process.stdout.close()))
        bridge = int(process.stdout.readline())
        self.addCleanup(lambda: Path(f"/proc/{bridge}").exists() and os.kill(bridge, signal.SIGKILL))
        shown = [call for call in fake_vrclient.calls(log) if call["call"] == "ShowOverlay"]
        self.assertTrue(shown and shown[-1]["visible"])
        killed = time.monotonic_ns()
        process.kill()
        process.wait(5)
        end = time.monotonic() + 5
        while Path(f"/proc/{bridge}/stat").exists() and Path(f"/proc/{bridge}/stat").read_text().split()[2] != "Z":
            self.assertLess(time.monotonic(), end, "the bridge outlived its launcher")
            time.sleep(0.01)
        drawing = [call for call in fake_vrclient.calls(log) if call["call"] in strip_suite.DRAWING]
        self.assertEqual([call["call"] for call in drawing][-3:], ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
        self.assertIs(drawing[-1]["visible"], False)
        self.assertLess(drawing[-3]["end_ns"] - killed, POLL_NS + MARGIN_NS)  # well before the panel's own deadline


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Run bash scripts/build_overlay_bridge.sh")
class BoxPanelArgumentTests(unittest.TestCase):
    """The layout argument and the validation stream; no runtime is reached."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(directory.name) / "absent.vrpath"))

    def run_bridge(self, *arguments, data=b""):
        result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], input=data, env=self.env, capture_output=True,
                                timeout=10)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_the_layout_is_named_at_launch_from_the_contracts_two_and_only_where_it_means_something(self):
        refused = (("--fixed-hud", "--layout"), ("--fixed-hud", "--layout", "head-fixed-box-panel-v2"),
                   ("--fixed-hud", "--layout", ""), ("--fixed-hud", "--layout", PANEL.layout_id, "extra"),
                   ("--fixed-hud", "--layout", PANEL.layout_id, "--layout", STRIP.layout_id),
                   ("--fixed-hud", PANEL.layout_id), ("--fixed-hud", "--connect-as", "background", *LAYOUT_ARGUMENTS),
                   ("--stdin", *LAYOUT_ARGUMENTS), ("--test-panel", *LAYOUT_ARGUMENTS),
                   ("--calibration-marker", *LAYOUT_ARGUMENTS), ("--validate-calibration-marker", *LAYOUT_ARGUMENTS),
                   ("--headset-view-record", *LAYOUT_ARGUMENTS), ("--layout", PANEL.layout_id),
                   ("--validate-stdin", "--layout", "nope"))
        for arguments in refused:
            with self.subTest(arguments=arguments):
                result, messages = self.run_bridge(*arguments)
                self.assertEqual((result.returncode, len(messages)), (64, 1), messages)
                self.assertFalse(messages[0]["runtime_initialized"])
                self.assertNotIn("fixed_hud", messages[0])
        # Named, the mode gets as far as its Background probe, and the report already carries the panel.
        for arguments in (("--fixed-hud", *LAYOUT_ARGUMENTS),
                          ("--fixed-hud", *LAYOUT_ARGUMENTS, "--connect-as", "background")):
            result, (report,) = self.run_bridge(*arguments)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(report["error"].startswith("vrserver_not_running:"), report["error"])
            self.assertEqual((report["mode"], report["fixed_hud"]["config_id"], report["fixed_hud"]["config_digest"]),
                             ("--fixed-hud", PANEL.layout_id, config_digest(PANEL)))
        usage = subprocess.run([str(DEFAULT_BRIDGE), "--help"], env=self.env, capture_output=True, timeout=5).stdout.decode()
        self.assertIn(f"--layout ID, one of: {STRIP.layout_id} (320x128) {PANEL.layout_id} (512x512).", usage)

    def test_validation_with_a_named_layout_takes_that_layouts_frame_alone_and_places_nothing(self):
        now = time.monotonic_ns()
        result, messages = self.run_bridge("--validate-stdin", *LAYOUT_ARGUMENTS,
                                           data=tile(1, now, 300 * MS) + tile(2, now + 1, 300 * MS) + packet(3))
        self.assertEqual(result.returncode, 0, messages[-1])
        ready, final = messages[0], messages[-1]
        self.assertEqual((ready["layout_id"], ready["validation_only"], ready["runtime_initialized"]),
                         (PANEL.layout_id, True, False))
        self.assertNotIn("fixed_hud", ready)
        self.assertEqual([line["frame_id"] for line in messages if line["event"] == "frame_presented"], [1, 2])
        self.assertEqual((final["status"], final["frames_received"], final["observed_frames_received"],
                          final["runtime_initialized"]), ("validated", 2, 2, False))
        self.assertEqual(final["validation_layout"], {"config_id": PANEL.layout_id, "config_digest": config_digest(PANEL),
                                                      "width": W, "height": H})
        self.assertNotIn("fixed_hud", final)
        for width, height, error in ((W, H - 1, "fixed_hud_frame_size"), (64, 64, "fixed_hud_frame_size"),
                                     (STRIP.width_px, STRIP.height_px, "frame_for_other_layout")):
            with self.subTest(width=width, height=height):
                result, messages = self.run_bridge("--validate-stdin", *LAYOUT_ARGUMENTS,
                                                   data=tile(1, time.monotonic_ns(), 300 * MS, width=width, height=height))
                self.assertEqual((result.returncode, messages[-1]["error"], messages[-1]["frames_received"]),
                                 (1, error, 0))
        # Without the argument the validation stream is what it was: any size, no layout on any line.
        result, messages = self.run_bridge("--validate-stdin", data=tile(1, time.monotonic_ns(), 300 * MS, width=7, height=5)
                                           + tile(2, time.monotonic_ns(), 300 * MS) + packet(3))
        self.assertEqual((result.returncode, messages[-1]["frames_received"]), (0, 2))
        self.assertFalse([line for line in messages if "layout_id" in line or "validation_layout" in line])


class BoxPanelNativeContractTests(unittest.TestCase):
    """The generated header and the bridge's sources; nothing is run."""

    def setUp(self):
        self.units = {Path(path).name: (ROOT / path).read_text() for path in manifest.NATIVE_SOURCES}
        self.header = self.units["hud_contract.hpp"]

    def test_the_header_carries_the_panel_and_the_strips_values_did_not_move(self):
        self.assertEqual(self.header, native.header_text())
        self.assertEqual(native.contract_digest(), HEADER_CONTRACT_DIGEST)
        self.assertIn(f'constexpr char kContractDigest[] = "{HEADER_CONTRACT_DIGEST}";', self.header)
        for line in ('constexpr char kBoxPanelLayoutId[] = "head-fixed-box-panel-v1";',
                     "constexpr std::uint32_t kBoxPanelWidthPx = 512;", "constexpr std::uint32_t kBoxPanelHeightPx = 512;",
                     "constexpr float kBoxPanelPanelWidthM = 0.9f;", "constexpr float kBoxPanelDistanceM = 0.75f;",
                     "constexpr float kBoxPanelXM = 0.0f;", "constexpr float kBoxPanelYM = 0.0f;",
                     "constexpr float kBoxPanelOpacity = 1.0f;",
                     f'constexpr char kBoxPanelConfigDigest[] = "{config_digest(PANEL)}";'):
            self.assertIn(line + "\n", self.header)
        # Every field of the configuration is in the header, so kContractDigest pins the whole record.
        import dataclasses
        declared = set(re.findall(r"constexpr [\w:]+ (kBoxPanel\w+?)(?:\[\])? = ", self.header))
        self.assertEqual(declared, {"kBoxPanel" + "".join(part.title() for part in field.name.split("_"))
                                    for field in dataclasses.fields(PANEL)} | {"kBoxPanelConfigDigest"})
        # The strip's block, as it was before the panel was added (main at c525e02), line for line.
        strip = self.header.split("// HudConfig: sightcircuit.hud.contract.HUD_CONFIG\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(strip.splitlines(), [
            'constexpr char kHudLayoutId[] = "fixed-edge-hud-v1";', "constexpr std::uint32_t kHudWidthPx = 320;",
            "constexpr std::uint32_t kHudHeightPx = 128;", "constexpr std::uint32_t kHudRows = 3;",
            "constexpr float kHudPanelWidthM = 0.32f;", "constexpr float kHudDistanceM = 1.25f;",
            "constexpr float kHudXM = 0.55f;", "constexpr float kHudYM = -0.35f;", "constexpr float kHudOpacity = 1.0f;",
            "constexpr std::uint32_t kHudInferenceMaxSide = 960;",
            'constexpr char kHudConfigDigest[] = "82f3f630a37c07eda5094ccc38e99b2e022ba732003fb1a76ee4cc9e60d5f392";'])
        self.assertEqual(MAX_DISPLAY_AGE_NS, 1_000_000_000)
        self.assertIn("constexpr std::uint32_t kMaxSide = 512;", self.header)

    def test_the_contracts_layouts_are_the_bridges_layouts_strip_first(self):
        self.assertEqual(FIXED_LAYOUTS, (HUD_CONFIG, BOX_PANEL_CONFIG))
        self.assertIs(fixed_layout(PANEL.layout_id), PANEL)
        self.assertIs(fixed_layout(STRIP.layout_id), STRIP)
        for name in ("head-fixed-box-panel-v2", "", None, 1, PANEL):
            with self.assertRaisesRegex(ValueError, "fixed_layout_unknown"):
                fixed_layout(name)
        frame = self.units["overlay_frame.hpp"]
        table = frame.split("constexpr FixedLayout kFixedLayouts[] = {", 1)[1].split("};", 1)[0]
        self.assertEqual(re.findall(r"sightcircuit_contract::(k\w+LayoutId)", table), ["kHudLayoutId", "kBoxPanelLayoutId"])
        self.assertIn("constexpr int kStripLayout = 0, kNoLayout = -1;", frame)
        self.assertNotRegex(table, r"\d\.\d|\b\d{2,}\b")  # the contract's names only: no size or metre typed here
        self.assertEqual(UPDATE_BYTES, PANEL.update_bytes)
        self.assertEqual(UPDATE_BYTES, 512 * 512 * 4)  # exactly the largest frame the packet format carries

    def test_the_layout_is_read_at_launch_by_the_size_rule_the_placement_and_the_report_and_by_nothing_else(self):
        # The stream loop, the expiry, the acknowledgements, the hide and the watchdogs are in the sensing and
        # lifecycle units, and neither reads the layout: there is no second copy of the safety logic to diverge.
        for name in ("overlay_bridge_sensing.cpp", "overlay_bridge_lifecycle.cpp", "overlay_lifecycle.hpp"):
            self.assertNotRegex(self.units[name], r"(?i)layout|BoxPanel", name)
        entry = self.units["overlay_bridge.cpp"]
        self.assertEqual(len(re.findall(r"pinned_layout\s*=[^=]", entry)), 1)  # written once, while arguments are read
        self.assertLess(entry.index("sightcircuit_overlay::pinned_layout=sightcircuit_overlay::layout_index(argv[3]);"),
                        entry.index("Overlay overlay;"))
        for name, text in self.units.items():
            if name not in ("overlay_bridge.cpp", "overlay_frame.hpp"):
                self.assertNotIn("pinned_layout", text, name)
        frame = self.units["overlay_frame.hpp"]
        self.assertEqual(len(re.findall(r"pinned_layout\s*=[^=]", frame)), 1)  # its declaration, as kNoLayout
        self.assertIn("inline int pinned_layout = kNoLayout;", frame)
        # No unit names the panel's contract values but the table: the bridge reads a layout, never "the box panel".
        for name, text in self.units.items():
            if name not in ("hud_contract.hpp", "overlay_frame.hpp"):
                self.assertNotIn("kBoxPanel", text, name)
        # The pixels of a frame go to SetOverlayRaw as they arrived: one upload site, four bytes a pixel.
        rendering = self.units["overlay_bridge_rendering.cpp"]
        self.assertEqual(re.findall(r'api->SetOverlayRaw\((.*?)\), "SetOverlayRaw"\)', rendering),
                         ["handle,const_cast<void*>(rgba),w,h,4"])


class BoxPanelClientTests(unittest.TestCase):
    """``ObservedFrameClient`` with a named layout: checked before anything is launched, then on the validation bridge."""

    def test_the_layout_is_one_of_the_contracts_objects_and_fixed_at_construction(self):
        import dataclasses
        for layout in (PANEL.layout_id, dataclasses.replace(PANEL), dataclasses.replace(PANEL, max_boxes=4),
                       dataclasses.replace(STRIP, rows=2), object(), 1, True):
            with self.subTest(layout=layout), mock.patch.object(subprocess, "Popen") as launched:
                with self.assertRaisesRegex(ValueError, "observed_frame_layout_unknown"):
                    ObservedFrameClient(layout=layout)
                launched.assert_not_called()
        client = ObservedFrameClient(layout=PANEL)
        self.assertIs(client.layout, PANEL)
        with self.assertRaises(AttributeError):
            client.layout = STRIP
        arguments = observed_frames.launch_arguments
        self.assertEqual(arguments(True, PANEL), ("--validate-stdin", "--layout", PANEL.layout_id))
        self.assertEqual(arguments(False, PANEL), ("--fixed-hud", "--layout", PANEL.layout_id))
        # Without a layout the command line is the one it has always been, and only the literal True is validation.
        self.assertIsNone(ObservedFrameClient().layout)
        self.assertEqual((arguments(True, None), arguments(False, None)), (("--validate-stdin",), ("--fixed-hud",)))
        self.assertEqual((arguments(1, None), arguments("yes", STRIP)),
                         (("--fixed-hud",), ("--fixed-hud", "--layout", STRIP.layout_id)))
        # The command line is a function of the mode and the layout value; the client has no method that builds it.
        self.assertFalse(hasattr(ObservedFrameClient, "bridge_arguments"))

    def test_a_bridge_that_names_another_layout_or_none_fails_the_client(self):
        ready = {"event": "ready", "observed_frames_supported": True, "frame_expiry_supported": True}
        ObservedFrameClient(layout=PANEL)._line({**ready, "layout_id": PANEL.layout_id})
        ObservedFrameClient()._line(ready)
        for client, line in ((ObservedFrameClient(layout=PANEL), ready),
                             (ObservedFrameClient(layout=PANEL), {**ready, "layout_id": STRIP.layout_id}),
                             (ObservedFrameClient(layout=STRIP), {**ready, "layout_id": PANEL.layout_id}),
                             (ObservedFrameClient(), {**ready, "layout_id": PANEL.layout_id})):
            with self.assertRaisesRegex(RuntimeError, "overlay_bridge_layout_mismatch"):
                client._line(line)

    @unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Run bash scripts/build_overlay_bridge.sh")
    def test_the_validation_bridge_takes_the_panels_megabyte_and_only_that(self):
        client = ObservedFrameClient(layout=PANEL)
        self.addCleanup(client.close)
        client.start()
        self.assertEqual(client.process.args, [str(DEFAULT_BRIDGE), "--validate-stdin", "--layout", PANEL.layout_id])
        rgba = bytes(UPDATE_BYTES)
        now = time.monotonic_ns()
        for width, height, pixels in ((STRIP.width_px, STRIP.height_px, bytes(STRIP.width_px * STRIP.height_px * 4)),
                                      (W, H - 1, bytes(W * (H - 1) * 4)), (W, H, rgba[:-4]), (W, H, rgba + bytes(4)),
                                      (float(W), H, rgba), (H, W + 0.0, rgba)):
            with self.subTest(width=width, height=height, bytes=len(pixels)):
                with self.assertRaisesRegex(ValueError, "observed_frame_not_layout_size"):
                    client.submit_observed(1, now, now + 500 * MS, width, height, pixels)
        self.assertEqual(client.frames_submitted, 0)
        for frame_id in (1, 2, 3):
            observed = time.monotonic_ns()
            self.assertTrue(client.submit_observed(frame_id, observed, observed + 500 * MS, W, H, rgba))
            end = time.monotonic() + 5
            while frame_id not in client.presented and time.monotonic() < end:
                time.sleep(0.005)
            self.assertIn(frame_id, client.presented, client.error)
        report = client.close()
        native_report = report["native_report"]
        self.assertEqual((report["cleanup_confirmed"], report["mode"], report["frames_sent"], report["frames_presented"],
                          report["layout_id"], report["layout_digest"]),
                         (True, "--validate-stdin", 3, 3, PANEL.layout_id, config_digest(PANEL)))
        self.assertEqual(native_report["validation_layout"]["config_id"], PANEL.layout_id)
        self.assertIs(native_report["runtime_initialized"], False)
        # A client without a layout reports none.
        plain = ObservedFrameClient()
        self.assertFalse({"layout_id", "layout_digest"} & set(plain.close()))

    def test_cleanup_is_confirmed_only_when_the_bridge_reports_the_layout_that_was_named(self):
        native_report = {"mode": "--validate-stdin", "runtime_initialized": False, "cleanup_ok": True, "exit_code": 0,
                         "validation_layout": {"config_id": PANEL.layout_id, "config_digest": config_digest(PANEL),
                                               "width": W, "height": H}}
        clean = {"requested": True, "validation_only": True, "error": None, "native_report": native_report,
                 "forced_termination": False, "worker_reaped": True, "worker_exit_code": 0,
                 "transport_threads_stopped": True}

        def closed(changes):
            client = ObservedFrameClient(layout=PANEL)
            client.process = mock.Mock()  # "started": the report is the bridge's
            report = {**clean, "native_report": {**native_report, **changes}}
            with mock.patch.object(observed_frames.OverlayClient, "close", return_value=report), \
                    mock.patch.object(client, "end"):
                return client.close()["cleanup_confirmed"]

        self.assertIs(closed({}), True)
        for changes in ({"validation_layout": None}, {"validation_layout": {**native_report["validation_layout"], "width": 320}},
                        {"validation_layout": {**native_report["validation_layout"], "config_id": STRIP.layout_id}},
                        {"validation_layout": {**native_report["validation_layout"], "config_digest": config_digest(STRIP)}}):
            with self.subTest(changes=changes):
                self.assertIs(closed(changes), False)


class BoxPanelPermissionTests(unittest.TestCase):
    """No real output with the box panel: refused before the scope is asked and before anything is launched."""

    def test_real_output_with_the_box_panel_is_refused_even_where_real_output_is_permitted(self):
        client = ObservedFrameClient(layout=PANEL, validation_only=False, max_output_seconds=5)
        self.assertIs(observed_frames.layout_output_permitted(client.layout), False)
        with mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=True) as permitted, \
                mock.patch.object(subprocess, "Popen") as launched:
            for start in (client.check_start, client.start):
                with self.assertRaises(ValueError) as caught:
                    start()
                self.assertEqual(str(caught.exception), "fixed_hud_layout_not_approved")
            permitted.assert_not_called()  # the scope was never asked, so no run id was consumed
            launched.assert_not_called()
        self.assertIsNone(client.process)
        self.assertEqual(gates.claims(), {"production_live_input_enabled": False,
                                          "production_camera_input_enabled": False, "mapping_qualified": False,
                                          "production_hud_enabled": False})

    def test_the_refusal_comes_before_the_gate_and_only_the_strip_is_a_real_output_layout(self):
        client = ObservedFrameClient(layout=PANEL, validation_only=False)
        with mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            with self.assertRaisesRegex(ValueError, "^fixed_hud_layout_not_approved$"):
                client.check_start()
        required.assert_not_called()  # refused before the gate's one require call, which stays the permission's
        # A layout that is let pass still meets the gate and the permission, exactly as an unnamed client does.
        with mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            with self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
                ObservedFrameClient(layout=STRIP, validation_only=False).check_start()
        (gate, permitted, reason), = [call.args for call in required.call_args_list]
        self.assertEqual((gate, permitted, reason), (gates.LIVE_HUD_OUTPUT, False, "fixed_hud_output_not_approved"))
        self.assertEqual(observed_frames.REAL_OUTPUT_LAYOUT_IDS, ("fixed-edge-hud-v1",))
        import dataclasses
        self.assertIs(observed_frames.layout_output_permitted(STRIP), True)
        # It judges the layout value itself, by identity with the contract's object: not a name, a copy, a
        # look-alike, a client or nothing.
        for other in (PANEL, object(), None, STRIP.layout_id, PANEL.layout_id, dataclasses.replace(STRIP),
                      types.SimpleNamespace(layout_id=STRIP.layout_id), ObservedFrameClient(layout=STRIP),
                      types.SimpleNamespace(layout=STRIP)):
            self.assertIs(observed_frames.layout_output_permitted(other), False)
        observed_frames.require_real_output_layout(None)  # no layout named: the strip's client, nothing to refuse
        observed_frames.require_real_output_layout(STRIP)
        with self.assertRaisesRegex(ValueError, "^fixed_hud_layout_not_approved$"):
            observed_frames.require_real_output_layout(PANEL)
        # Only the literal True permits, and a named strip is still asked for its scope afterwards.
        with mock.patch.object(observed_frames, "layout_output_permitted", return_value=1), \
                mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=True):
            with self.assertRaisesRegex(ValueError, "fixed_hud_layout_not_approved"):
                client.check_start()
        named_strip = ObservedFrameClient(layout=STRIP, validation_only=False)
        with self.assertRaisesRegex(ValueError, "fixed_hud_output_not_approved"):
            named_strip.check_start()
        # Validation never asks either function.
        with mock.patch.object(observed_frames, "layout_output_permitted") as layout_asked, \
                mock.patch.object(observed_frames, "fixed_hud_output_permitted") as output_asked:
            ObservedFrameClient(layout=PANEL).check_start()
            layout_asked.assert_not_called()
            output_asked.assert_not_called()

    def test_the_existing_permission_alone_would_have_let_the_box_panel_through(self):
        # Why the layout check exists: the permission to drive real output knows nothing of layouts. Asked about a
        # box-panel client whose scope permits (a stand-in for a strip's record that verifies), it says yes.
        client = ObservedFrameClient(layout=PANEL, validation_only=False, max_output_seconds=5)
        class PermittingScope:
            seconds, asked = 5, 0

            def permit(self, use, holder):
                PermittingScope.asked += 1
                return True

        client.scope = PermittingScope()
        with mock.patch.object(observed_frames, "RunScope", PermittingScope), \
                mock.patch.object(observed_frames, "pinned_binary", return_value=True):
            self.assertIs(observed_frames.fixed_hud_output_permitted(client), True)
            self.assertEqual(PermittingScope.asked, 1)
            with mock.patch.object(subprocess, "Popen") as launched:
                with self.assertRaisesRegex(ValueError, "fixed_hud_layout_not_approved"):
                    client.start()
                launched.assert_not_called()
            self.assertEqual(PermittingScope.asked, 1)  # the start itself never reached the scope

    @unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Run bash scripts/build_overlay_bridge.sh")
    def test_the_layout_launched_is_the_layout_judged_whatever_a_subclass_overrides(self):
        # The start reads the layout once, judges that value and builds the command line from it. Two subclasses
        # try to part the two, each with the permission to drive real output patched to permit (a strip's record).
        class SaysStrip(ObservedFrameClient):  # holds the panel, answers "the strip" to anyone who asks
            layout = property(lambda self: STRIP)

            def check_start(self):
                pass

        class ArguesPanel(ObservedFrameClient):  # holds no layout, would put the panel on the command line
            def bridge_arguments(self):
                return ("--fixed-hud", "--layout", PANEL.layout_id)

            def bridge_mode(self):
                return "--fixed-hud --layout " + PANEL.layout_id

        launched_with = []

        def launch(arguments, **options):
            launched_with.append(list(arguments))
            raise RuntimeError("stop")

        with mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=True), \
                mock.patch.object(subprocess, "Popen", side_effect=launch):
            tricked = SaysStrip(layout=PANEL, validation_only=False, max_output_seconds=5)
            self.assertIs(tricked.layout, STRIP)
            with self.assertRaisesRegex(ValueError, "^fixed_hud_layout_not_approved$"):
                tricked.start()
            self.assertEqual(launched_with, [])
            for layout in (None, STRIP):
                argued = ArguesPanel(layout=layout, validation_only=False, max_output_seconds=5)
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    argued.start()
            named = [str(DEFAULT_BRIDGE), "--fixed-hud", "--layout", STRIP.layout_id]
            self.assertEqual(launched_with, [[str(DEFAULT_BRIDGE), "--fixed-hud"], named])
            # A layout swapped in after construction is the one judged, and refused: nothing judges one value and
            # launches another.
            swapped = ObservedFrameClient(layout=STRIP, validation_only=False, max_output_seconds=5)
            swapped._layout = PANEL
            with self.assertRaisesRegex(ValueError, "^fixed_hud_layout_not_approved$"):
                swapped.start()
            self.assertEqual(len(launched_with), 2)
        # In the source: one read of the layout in start, and the judgement and the command line both take that local.
        source = (ROOT / "sightcircuit/observed_frames.py").read_text()
        start = source.split("    def start(self):", 1)[1].split("\n    def ", 1)[0]
        self.assertEqual(start.count("self._layout"), 1)
        self.assertIn("layout, validation_only = self._layout, self.validation_only is True", start)
        self.assertIn("require_real_output_layout(layout)", start)
        self.assertIn("*launch_arguments(validation_only, layout)]", start)
        self.assertLess(start.index("require_real_output_layout(layout)"), start.index("subprocess.Popen("))
        self.assertNotIn("bridge_mode()", start)

    def test_nothing_that_can_drive_real_output_names_a_layout(self):
        # A static scan of the package and the scripts, and what it can and cannot see.
        #
        # Seen: any call at all that passes a keyword named ``layout`` (so a construction through an alias, through
        # ``functools.partial`` or through a subclass's ``super().__init__`` is caught as well as a direct one); any
        # ``**options`` handed to a call that names the client, to ``partial`` or to ``super().__init__`` of a
        # subclass of it; any subclass of the client; in a file that names the client, any string "layout" that
        # could be a key of such options; and any reach for ``_layout``. Only ``observed_frames.py`` may do any of it.
        #
        # Not seen: code that builds the keyword at run time under another spelling ("lay" + "out"), ``setattr``
        # or ``getattr`` with computed names, ``exec``, and code outside these two folders. A scan is a tripwire for
        # honest mistakes; the refusal in ``start`` is what holds.
        owner = "sightcircuit/observed_frames.py"
        constructions, scanned = 0, 0
        for folder in ("sightcircuit", "scripts"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                name = path.relative_to(ROOT).as_posix()
                tree = ast.parse(path.read_text())
                scanned += 1
                names_client = "ObservedFrameClient" in path.read_text()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        callee = getattr(node.func, "id", getattr(node.func, "attr", ""))
                        constructions += callee == "ObservedFrameClient"
                        if name != owner:
                            self.assertNotIn("layout", {keyword.arg for keyword in node.keywords}, f"{name}:{node.lineno}")
                            if callee in ("ObservedFrameClient", "partial") or (names_client and callee == "__init__"):
                                self.assertFalse(any(keyword.arg is None for keyword in node.keywords),
                                                 f"{name}:{node.lineno} passes **options")
                    if name == owner:
                        continue
                    if isinstance(node, ast.ClassDef):
                        bases = {getattr(base, "id", getattr(base, "attr", "")) for base in node.bases}
                        self.assertNotIn("ObservedFrameClient", bases, f"{name}:{node.lineno} subclasses the client")
                    if names_client and isinstance(node, ast.Constant) and node.value == "layout":
                        self.fail(f'{name}:{node.lineno} holds the string "layout" beside the client')
                    if isinstance(node, ast.Attribute) and node.attr == "_layout":
                        self.fail(f"{name}:{node.lineno} reaches for _layout")
        self.assertGreaterEqual(constructions, 2)
        self.assertGreater(scanned, 100)
        # And by name: what may drive real output knows no word by which the panel or a layout can be reached.
        for name in ("sightcircuit/runtime/scope.py", "sightcircuit/runtime/manifest.py", "sightcircuit/hud/camera_stage.py",
                     "sightcircuit/hud/camera.py", "sightcircuit/hud/synthetic_rows.py", "sightcircuit/commands/camera_hud.py",
                     "sightcircuit/gates.py", "scripts/camera_hud.py", "scripts/stage_scope_record.py"):
            text = (ROOT / name).read_text()
            for word in ("FIXED_LAYOUTS", "fixed_layout", "BOX_PANEL", "BoxPanel", "box_panel", "box-panel", "box_scenes",
                         "orientation_scene", "--layout", "LAYOUT_ARGUMENT", "layout_output_permitted",
                         "require_real_output_layout", "launch_arguments", "bridge_arguments", "REAL_OUTPUT_LAYOUT_IDS"):
                self.assertNotIn(word, text, f"{name} names {word}")

    @unittest.skipUnless(RUNTIME, NEEDS_RUNTIME)
    def test_on_the_fake_runtime_the_client_shows_the_panel_reports_it_and_survives_the_bridges_death(self):
        # Reached only with both permission functions patched, as the strip's fake-runtime tests patch theirs.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        runtime_env, _ = fake_vrclient.runtime(directory.name)
        for patched in ("fixed_hud_output_permitted", "layout_output_permitted"):
            permit = mock.patch.object(observed_frames, patched, return_value=True)
            permit.start()
            self.addCleanup(permit.stop)

        def started(name):
            log = Path(directory.name) / f"{name}.jsonl"
            client = ObservedFrameClient(layout=PANEL, validation_only=False, max_output_seconds=30,
                                         env={**os.environ, **runtime_env, "FAKE_VR_LOG": str(log)})
            self.addCleanup(client.close)
            return client.start(), log

        def present(client, frame_id, life_ns):
            observed = time.monotonic_ns()
            self.assertTrue(client.submit_observed(frame_id, observed, observed + life_ns, W, H, bytes(UPDATE_BYTES)))
            end = time.monotonic() + 5
            while frame_id not in client.presented and time.monotonic() < end:
                time.sleep(0.005)
            self.assertIn(frame_id, client.presented, client.error)

        client, log = started("shown")
        self.assertEqual(client.process.args, [str(DEFAULT_BRIDGE), "--fixed-hud", "--layout", PANEL.layout_id])
        present(client, 1, 600 * MS)
        present(client, 2, 250 * MS)  # renewed by a newer observation, then left to expire
        end = time.monotonic() + 5
        while not client.expiry_events and time.monotonic() < end:
            time.sleep(0.005)
        report = client.close()
        self.assertEqual([(event["frame_id"], event["outcome"], event["hide_ok"]) for event in client.expiry_events],
                         [(2, "hidden", True)])
        self.assertEqual((report["cleanup_confirmed"], report["mode"], report["layout_id"], report["frames_presented"],
                          report["settings_sent"]), (True, "--fixed-hud", PANEL.layout_id, 2, 0))
        self.assertEqual(report["native_report"]["fixed_hud"]["config_digest"], config_digest(PANEL))
        calls = [call["call"] for call in fake_vrclient.calls(log) if call["call"] in strip_suite.DRAWING]
        self.assertEqual(calls[-3:], ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
        # The bridge is killed with a panel up: the client says so, stops, and confirms no cleanup.
        client, log = started("killed")
        present(client, 1, 900 * MS)
        client.process.kill()
        end = time.monotonic() + 5
        while client.error is None and time.monotonic() < end:
            time.sleep(0.005)
        self.assertEqual(client.error, "Overlay bridge disconnected without a final cleanup report")
        began = time.monotonic()
        report = client.close()
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual((report["cleanup_confirmed"], report["worker_reaped"], report["transport_threads_stopped"]),
                         (False, True, True))


class OrientationSceneTests(unittest.TestCase):
    """The scene a later synthetic device step can show to settle row direction and alpha."""

    def test_each_box_is_named_by_where_it_is_drawn_and_the_corners_differ_in_size(self):
        boxes = box_scenes.orientation_scene(PANEL)
        self.assertEqual([box.label for box in boxes], ["top left", "top right", "bottom left", "bottom right", "centre"])
        from sightcircuit.hud.contract import panel_box_valid, select_boxes
        self.assertTrue(all(panel_box_valid(box, PANEL) for box in boxes))
        self.assertEqual(select_boxes(boxes, PANEL.max_boxes), (boxes, 0, 0))  # already in the renderer's order
        by_name = {box.label: box.box_xyxy for box in boxes}
        for name, (x0, y0, x1, y1) in by_name.items():
            self.assertTrue(0 < x0 < x1 < W and 0 < y0 < y1 < H, name)  # no side on the panel's edge: none is open
            if name != "centre":
                self.assertEqual(("left" in name, "top" in name), (x1 <= W // 2, y1 <= H // 2), name)
                self.assertEqual(("right" in name, "bottom" in name), (x0 >= W // 2, y0 >= H // 2), name)
        x0, y0, x1, y1 = by_name["centre"]
        self.assertEqual((x0 + x1, y0 + y1), (W, H))
        sizes = {name: (x1 - x0, y1 - y0) for name, (x0, y0, x1, y1) in by_name.items() if name != "centre"}
        self.assertEqual(len(set(sizes.values())), 4)
        self.assertEqual(max(sizes, key=lambda name: sizes[name][0] * sizes[name][1]), "top left")
        width, height = sizes["top left"]
        self.assertGreater(width, height)  # the largest box lies wider than tall
        # All seven ways a square picture can be flipped or turned by quarter turns. The rule a wearer applies is
        # "the largest box is at the top left and wider than tall"; it must hold for the picture as drawn and fail
        # for every one of the seven. Both halves are needed: a flip about the main diagonal leaves the largest box
        # at the top left but stands it taller than wide.
        self.assertEqual(W, H)
        changes = {"left-right flip": lambda x0, y0, x1, y1: (W - x1, y0, W - x0, y1),
                   "top-bottom flip": lambda x0, y0, x1, y1: (x0, H - y1, x1, H - y0),
                   "half turn": lambda x0, y0, x1, y1: (W - x1, H - y1, W - x0, H - y0),
                   "quarter turn clockwise": lambda x0, y0, x1, y1: (H - y1, x0, H - y0, x1),
                   "quarter turn anticlockwise": lambda x0, y0, x1, y1: (y0, W - x1, y1, W - x0),
                   "flip about the main diagonal": lambda x0, y0, x1, y1: (y0, x0, y1, x1),
                   "flip about the other diagonal": lambda x0, y0, x1, y1: (H - y1, W - x1, H - y0, W - x0)}

        def reads_upright(rectangles):
            x0, y0, x1, y1 = max(rectangles, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
            return x1 <= W // 2 and y1 <= H // 2 and (x1 - x0) > (y1 - y0)

        corners = [rectangle for name, rectangle in by_name.items() if name != "centre"]
        rectangles = set(by_name.values())
        self.assertTrue(reads_upright(corners))
        self.assertEqual(len(changes), 7)
        seen = []
        for name, change in changes.items():
            with self.subTest(change=name):
                changed = [change(*rectangle) for rectangle in corners]
                self.assertTrue(all(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H for x0, y0, x1, y1 in changed))
                self.assertFalse(reads_upright(changed), name)
                self.assertNotEqual({change(*rectangle) for rectangle in rectangles}, rectangles, name)
                seen.append(frozenset(changed))
        self.assertEqual(len(set(seen)), 7)  # seven different pictures, none of them the one drawn
        diagonal = [changes["flip about the main diagonal"](*rectangle) for rectangle in corners]
        x0, y0, x1, y1 = max(diagonal, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        self.assertTrue(x1 <= W // 2 and y1 <= H // 2 and (x1 - x0) < (y1 - y0))  # still top left, now taller than wide
        for config in (STRIP, None):
            with self.assertRaisesRegex(ValueError, "box_panel_config_invalid"):
                box_scenes.orientation_scene(config)

    def test_the_module_lists_boxes_and_imports_nothing_that_draws_displays_or_reads_a_camera(self):
        tree = ast.parse((ROOT / "sightcircuit/hud/box_scenes.py").read_text())
        imported = {("." * node.level + (node.module or ""), alias.name) for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) for alias in node.names}
        imported |= {(alias.name, None) for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imported, {(".contract", "BoxPanelConfig"), (".contract", "PanelBox")})

    @unittest.skipUnless(DRAWING, "NumPy is required to draw the panel")
    def test_the_drawn_scene_is_pinned_and_every_name_is_drawn_whole_above_its_box(self):
        boxes = box_scenes.orientation_scene(PANEL)
        image = draw_boxes(boxes, PANEL)
        self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), ORIENTATION_SCENE_SHA256)
        for box, drawn in zip(boxes, box_layout(boxes, PANEL)):
            self.assertEqual((drawn.text, drawn.placement, drawn.opened), (box.label, "above", (False,) * 4))
        self.assertFalse(image[0].any())  # row 0 itself is transparent; the first drawn row is the top plates'
        alpha = image[..., 3]
        quadrant = {"top left": alpha[:H // 2, :W // 2], "top right": alpha[:H // 2, W // 2:],
                    "bottom left": alpha[H // 2:, :W // 2], "bottom right": alpha[H // 2:, W // 2:]}
        drawn_pixels = {name: int((part > 0).sum()) for name, part in quadrant.items()}
        self.assertEqual(max(drawn_pixels, key=drawn_pixels.get), "top left")
        self.assertGreater((alpha == 0).mean(), 0.9)  # the room shows through nine tenths of the panel


if __name__ == "__main__":
    unittest.main()
