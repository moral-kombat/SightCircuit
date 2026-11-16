"""The still panel of device step L-D2 (work-order item L7): a public seed image at the HUD placement, on a timer.

Cloud checks only: the validation bridge and a scripted client. Nothing here
connects to a runtime, and a count from the bridge's report is not a picture
anyone saw.
"""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from sightcircuit.hud import still_panel
from sightcircuit.hud.contract import (FRAME_PREFIX_FORMAT, HUD_CONFIG, OPCODE_FRAME, PACKET_HEADER_FORMAT,
                                       RGBA_BYTES)
from sightcircuit.overlay import DEFAULT_BRIDGE, OverlayClient, PanelSettings

ROOT = Path(__file__).resolve().parents[1]
CV2 = importlib.util.find_spec("cv2") is not None
HORSES = still_panel.SEED_DOWNLOADS / "darknet-horses.jpg"
TILE_BYTES = HUD_CONFIG.width_px * HUD_CONFIG.height_px * RGBA_BYTES
FIXTURE = CV2 and HORSES.is_file()
NEEDS_FIXTURE = "Needs OpenCV and the fetched seed images (scripts/evaluate_detectors.py --fetch)"


class Ticking:
    """A stop event that never fires and a clock that only its waits advance: a run costs no wall time."""

    def __init__(self):
        self.now, self.waits = 100.0, []

    def clock(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return False

    def is_set(self):
        return False


class ScriptedClient:
    """Takes what the driver sends; the writer is modelled as always having taken the previous frame."""

    def __init__(self, validation_only):
        self.validation_only, self.error, self.condition = validation_only, None, threading.Condition()
        self.pending_frame, self.calls, self.frames, self.times = None, [], [], []
        self.clock = None

    def set_settings(self, settings):
        self.calls.append(("settings", settings))

    def start(self):
        self.calls.append(("start",))
        return self

    def submit_frame(self, width, height, rgba, **options):
        self.calls.append(("frame",))
        self.frames.append((width, height, bytes(rgba), tuple(sorted(options))))
        if self.clock:
            self.times.append(self.clock())

    def close(self):
        self.calls.append(("close",))
        return {"error": None, "cleanup_confirmed": True, "native_report": {"frames_received": len(self.frames)}}


@unittest.skipUnless(FIXTURE, NEEDS_FIXTURE)
class StillPanelDriverTests(unittest.TestCase):
    """The driver with a scripted client and the real fixture tile: it builds the tile itself, from the image id."""

    def run_scripted(self, seconds, **options):
        ticking = Ticking()
        made = []

        def factory(validation_only):
            made.append(ScriptedClient(validation_only))
            made[-1].clock = ticking.clock
            return made[-1]

        report = still_panel.run_still_panel(seconds=seconds, client_factory=factory, stop=ticking,
                                             clock=ticking.clock, **options)
        return report, made[0], ticking

    def test_the_documented_run_is_600_updates_at_5_hz_at_the_hud_placement(self):
        report, client, ticking = self.run_scripted(120)
        self.assertEqual((report["status"], report["complete"], report["update_hz"]), ("completed", True, 5))
        self.assertEqual((report["updates_requested"], report["updates_submitted"], report["updates_acknowledged"],
                          report["updates_replaced"]), (600, 600, 600, 0))
        # The HUD placement is set before the client starts, and nothing but frames follows until the close.
        self.assertEqual(client.calls[0], ("settings", still_panel.hud_placement()))
        self.assertEqual(client.calls[1], ("start",))
        self.assertEqual(set(client.calls[2:-1]), {("frame",)})
        self.assertEqual(client.calls[-1], ("close",))
        self.assertEqual(still_panel.hud_placement(),
                         PanelSettings(width=HUD_CONFIG.panel_width_m, distance=HUD_CONFIG.distance_m,
                                       x=HUD_CONFIG.x_m, y=HUD_CONFIG.y_m, opacity=HUD_CONFIG.opacity, visible=True))
        still_panel.hud_placement().encode()  # within the stream mode's panel limits
        # Every update is the 320x128 tile with no deadline, one period apart, and the last stays up a period.
        rgba, source = still_panel.seed_tile()
        self.assertEqual(set(client.frames), {(HUD_CONFIG.width_px, HUD_CONFIG.height_px, rgba, ())})
        self.assertEqual(report["source"], source)
        self.assertEqual(report["source"]["tile_sha256"], hashlib.sha256(rgba).hexdigest())
        gaps = [later - earlier for earlier, later in zip(client.times, client.times[1:])]
        self.assertAlmostEqual(min(gaps), 0.2, places=6)
        self.assertAlmostEqual(max(gaps), 0.2, places=6)
        self.assertAlmostEqual(ticking.now - 100.0, 120.0, places=6)
        self.assertEqual((report["geometry"]["layout_id"], report["geometry"]["width_px"],
                          report["geometry"]["height_px"]), ("fixed-edge-hud-v1", 320, 128))
        self.assertEqual((report["camera_derived"], report["live_source_accepted"], report["images_saved"],
                          report["frames_carry_deadline"], report["physical_display_verified"]),
                         (False, False, False, False, False))
        self.assertEqual((report["validation_only"], report["bridge_mode"]), (True, "--validate-stdin"))

    def test_real_output_needs_its_opt_in_and_uses_the_replay_stream_only(self):
        with self.assertRaisesRegex(ValueError, "still_panel_device_output_not_allowed"):
            self.run_scripted(1, validation_only=False)
        report, client, _ = self.run_scripted(1, validation_only=False, allow_device_output=True)
        self.assertEqual((client.validation_only, report["bridge_mode"], report["updates_submitted"]),
                         (False, "--stdin", 5))
        # The driver never asks for an expiring frame or the fixed-HUD bridge mode, so no gate is involved.
        source = Path(still_panel.__file__).read_text()
        tree = ast.parse(source)
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == "expires_ns"])
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertFalse(names & {"freeze_settings", "submit_bgr", "request_visibility"})
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse({name for name in imported if "source" in name.lower() or "camera" in name.lower()
                          or "mirror" in name.lower() or "gates" in name.lower()})

    def test_bad_arguments_and_a_failed_bridge_are_refused_or_reported(self):
        for seconds in (0, 181, 1.5, True, "120"):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "still_panel_seconds_out_of_range"):
                self.run_scripted(seconds)
        # The driver takes an image id and builds the tile itself: pixels or a source label handed in are
        # refused as an unknown image id or an unexpected argument, before any client exists.
        made = []
        factory = lambda validation_only: made.append(1) or ScriptedClient(validation_only)  # noqa: E731
        for image in (bytes(TILE_BYTES), {"kind": "public_seed_fixture"}, "frame-still-1", None):
            with self.subTest(image=type(image).__name__), self.assertRaises((ValueError, TypeError)):
                still_panel.run_still_panel(image, seconds=1, client_factory=factory)
        for extra in ({"rgba": bytes(TILE_BYTES)}, {"source": {"kind": "public_seed_fixture"}}):
            with self.subTest(extra=list(extra)), self.assertRaises(TypeError):
                still_panel.run_still_panel(seconds=1, client_factory=factory, **extra)
        with tempfile.TemporaryDirectory() as temporary:  # a file of the fixture's name with other bytes
            (Path(temporary) / "darknet-horses.jpg").write_bytes(HORSES.read_bytes() + b"\0")
            with self.assertRaisesRegex(ValueError, "still_panel_seed_image_digest_mismatch"):
                still_panel.run_still_panel(seconds=1, client_factory=factory, directory=temporary)
        self.assertEqual(made, [])

        class Failing(ScriptedClient):
            def submit_frame(self, *args, **options):
                super().submit_frame(*args, **options)
                if len(self.frames) == 3:
                    self.error = "Overlay bridge pipe closed"

        ticking = Ticking()
        report = still_panel.run_still_panel(seconds=2, client_factory=Failing, stop=ticking, clock=ticking.clock)
        self.assertEqual((report["status"], report["error"], report["updates_submitted"], report["complete"]),
                         ("error", "Overlay bridge pipe closed", 3, False))
        self.assertIn("bridge", report)  # closed all the same

        class Slow(ScriptedClient):
            def submit_frame(self, *args, **options):
                super().submit_frame(*args, **options)
                self.pending_frame = b"untaken"

        ticking = Ticking()
        report = still_panel.run_still_panel(seconds=1, client_factory=Slow, stop=ticking, clock=ticking.clock)
        self.assertEqual((report["status"], report["updates_replaced"], report["complete"]), ("completed", 4, False))

    def test_a_stop_ends_the_run_early_and_still_closes(self):
        stop = threading.Event()

        class Stopping(ScriptedClient):
            def submit_frame(self, *args, **options):
                super().submit_frame(*args, **options)
                if len(self.frames) == 2:
                    stop.set()

        report = still_panel.run_still_panel(seconds=60, client_factory=Stopping, stop=stop)
        self.assertEqual((report["status"], report["updates_submitted"], report["complete"]), ("stopped", 2, False))
        self.assertTrue(report["cleanup_confirmed"])


class StillPanelCommandLineTests(unittest.TestCase):
    def test_the_command_line_takes_a_seed_image_id_and_nothing_that_could_name_a_live_source(self):
        source = Path(still_panel.__file__).read_text()
        flags = sorted(argument.value for node in ast.walk(ast.parse(source))
                       if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"
                       for argument in node.args)
        self.assertEqual(flags, ["--allow-device-output", "--image-id", "--seconds", "--validate"])
        for argv in (["--seconds", "2"], ["--validate"], ["--validate", "--allow-device-output", "--seconds", "2"],
                     ["--validate", "--seconds", "2", "--source", "mirror"],
                     ["--validate", "--seconds", "2", "/dev/video99"]):
            with self.subTest(argv=argv):
                result = subprocess.run([sys.executable, "-m", "sightcircuit.hud.still_panel", *argv], cwd=ROOT,
                                        capture_output=True, timeout=30)
                self.assertEqual((result.returncode, result.stdout), (2, b""))


@unittest.skipUnless(CV2, "Run scripts/setup_window.sh for OpenCV")
class SeedTileTests(unittest.TestCase):
    def test_only_a_verified_image_of_the_seed_manifest_becomes_a_tile(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "still_panel_image_not_in_seed_manifest"):
                still_panel.seed_tile("frame-still-1", directory=temporary)
            with self.assertRaisesRegex(ValueError, "still_panel_image_not_in_seed_manifest"):
                still_panel.seed_tile("../../etc/passwd", directory=temporary)
            with self.assertRaisesRegex(FileNotFoundError, "still_panel_seed_image_missing"):
                still_panel.seed_tile(directory=temporary)
            # A file of the right name with other bytes (a private still, a camera frame) is refused undecoded.
            import cv2
            import numpy as np

            cv2.imwrite(str(Path(temporary) / "darknet-horses.jpg"), np.full((64, 64, 3), 200, dtype=np.uint8))
            with self.assertRaisesRegex(ValueError, "still_panel_seed_image_digest_mismatch"):
                still_panel.seed_tile(directory=temporary)
        self.assertEqual(still_panel.SEED_MANIFEST, ROOT / "evaluation/seed.json")
        self.assertNotIn("manifest", still_panel.seed_tile.__kwdefaults__)

    @unittest.skipUnless(HORSES.is_file(), "Run scripts/evaluate_detectors.py --fetch for the seed images")
    def test_the_horses_fixture_is_letterboxed_whole_into_the_hud_tile(self):
        import numpy as np

        rgba, source = still_panel.seed_tile()
        self.assertEqual(len(rgba), TILE_BYTES)
        seed = {entry["id"]: entry for entry in json.loads((ROOT / "evaluation/seed.json").read_text())["images"]}
        self.assertEqual((source["kind"], source["id"], source["sha256"], source["present_labels"], source["fit"]),
                         ("public_seed_fixture", "darknet-horses", seed["darknet-horses"]["sha256"], ["horse"],
                          "letterbox"))
        tile = np.frombuffer(rgba, dtype=np.uint8).reshape(HUD_CONFIG.height_px, HUD_CONFIG.width_px, RGBA_BYTES)
        self.assertTrue((tile[..., 3] == 255).all())
        width, height = source["picture_px"]
        self.assertEqual(height, HUD_CONFIG.height_px)  # the full height; black bars left and right
        left = (HUD_CONFIG.width_px - width) // 2
        self.assertFalse(tile[:, :left, :3].any())
        self.assertFalse(tile[:, left + width:, :3].any())
        self.assertGreater(tile[:, left:left + width, :3].std(), 20)  # a picture, not a blank


@unittest.skipUnless(CV2 and HORSES.is_file() and DEFAULT_BRIDGE.is_file(),
                     "Needs OpenCV, the fetched seed images and scripts/build_overlay_bridge.sh")
class StillPanelValidationBridgeTests(unittest.TestCase):
    def test_the_documented_invocation_against_the_validation_bridge(self):
        # python -m sightcircuit.hud.still_panel --validate --seconds N, the L-D2 invocation with --validate in
        # place of the device opt-in and a short N: 5 Hz, every update acknowledged, a clean validation close.
        result = subprocess.run([sys.executable, "-m", "sightcircuit.hud.still_panel", "--validate", "--seconds", "3"],
                                cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        report = json.loads(lines[0])
        self.assertEqual((report["report_kind"], report["status"], report["complete"]),
                         ("sightcircuit_still_panel", "completed", True))
        self.assertEqual((report["updates_requested"], report["updates_submitted"], report["updates_acknowledged"],
                          report["updates_replaced"]), (15, 15, 15, 0))
        self.assertEqual((report["source"]["id"], report["camera_derived"]), ("darknet-horses", False))
        bridge = report["bridge"]
        native = bridge["native_report"]
        self.assertTrue(bridge["cleanup_confirmed"])
        self.assertEqual((native["mode"], native["status"], native["runtime_initialized"], native["frames_received"],
                          native["frames_submitted"], native["expiring_frames_received"], native["expired_frames"]),
                         ("--validate-stdin", "validated", False, 15, 0, 0, 0))
        self.assertGreaterEqual(native["settings_received"], 1)

    def test_the_bridge_receives_the_hud_placement_and_the_tile_as_plain_frames(self):
        # The bytes on the pipe, through the real client: the first packet is the HUD placement, and every
        # frame packet is opcode 1 at 320x128.
        sent = []

        class Recording(OverlayClient):
            def _send(self, data):
                sent.append(bytes(data))
                super()._send(data)

        rgba, _ = still_panel.seed_tile()
        report = still_panel.run_still_panel(seconds=1, client_factory=Recording)
        self.assertTrue(report["complete"], report)
        header = struct.calcsize(PACKET_HEADER_FORMAT)
        self.assertEqual(sent[0], still_panel.hud_placement().encode())
        frames = [data for data in sent if struct.unpack_from(PACKET_HEADER_FORMAT, data)[1] == OPCODE_FRAME]
        self.assertEqual(len(frames), 5)
        for data in frames:
            self.assertEqual(struct.unpack_from(FRAME_PREFIX_FORMAT, data, header), (320, 128))
            self.assertEqual(data[header + struct.calcsize(FRAME_PREFIX_FORMAT):], rgba)
        placements = {data for data in sent if len(data) == len(sent[0]) and data not in frames and data[4:8] == sent[0][4:8]}
        self.assertEqual(placements, {sent[0]})  # heartbeats repeat the HUD placement, never another


if __name__ == "__main__":
    unittest.main()
