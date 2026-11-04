"""One HUD, marker, packet and limit contract for Python and the native bridge.

``tools/hud_contract.hpp`` is generated from the Python values. The bridge can
render its calibration marker without OpenVR, so its pixels are compared with
the observer's template. Generated and validation-only checks; never headset
evidence.
"""
import contextlib
import dataclasses
import functools
import io
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from framejacker import overlay
from framejacker.hud import contract, native
from framejacker.hud.contract import HUD_CONFIG, MARKER
from framejacker.overlay import DEFAULT_BRIDGE, PANEL_LIMITS, OverlayClient, PanelSettings, packet

ROOT = Path(__file__).resolve().parents[1]
# The tracking reporter's event schema and lifetime limits, shared with tools/tracking_report.hpp.
TRACKING_NAMES = ("TRACKING_STATE_EVENT", "TRACKING_CHANGED_EVENT", "TRACKING_UNAVAILABLE_EVENT",
                  "TRACKING_UNIVERSE_FIELD", "TRACKING_ZERO_POSE_FIELD", "TRACKING_CHANGE_FIELD",
                  "TRACKING_REASON_FIELD", "TRACKING_PROPERTY_ERROR_FIELD", "TRACKING_REPORT_MIN_SECONDS",
                  "TRACKING_REPORT_MAX_SECONDS")
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_CV2 = importlib.util.find_spec("cv2") is not None
LINUX_BRIDGE = os.name == "posix" and DEFAULT_BRIDGE.is_file()


def changed(value):
    """A different value of the same type, for mutation checks."""
    if type(value) is bool:
        return not value
    if type(value) in (int, float):
        return value + 1
    if type(value) is str:
        return value + "x"
    if type(value) is bytes:
        return value[:-1] + b"X"
    if type(value) is tuple:
        return value[:-1] + (changed(value[-1]),)
    raise TypeError(type(value))


class GeneratedHeaderTests(unittest.TestCase):
    def test_committed_header_matches_the_python_contract(self):
        self.assertEqual((ROOT / "tools/hud_contract.hpp").read_text(), native.header_text(),
                         "stale header: run python3 scripts/hud_contract_header.py --write")

    def test_every_python_contract_value_reaches_the_header(self):
        committed = native.header_text()
        cases = [(contract, name, dataclasses.replace(record, **{field.name: changed(getattr(record, field.name))}),
                  f"{name}.{field.name}")
                 for name, record in (("HUD_CONFIG", HUD_CONFIG), ("MARKER", MARKER))
                 for field in dataclasses.fields(record)]
        for name in ("MARKER_MIN_SECONDS", "MARKER_MAX_SECONDS", "MAX_DISPLAY_AGE_NS", "MAX_SIDE", "RGBA_BYTES",
                     "OPCODE_FRAME", "OPCODE_SETTINGS", "OPCODE_STOP", "OPCODE_EXPIRING_FRAME", "PACKET_MAGIC",
                     *TRACKING_NAMES):
            cases.append((contract, name, changed(getattr(contract, name)), name))
        changes = contract.TRACKING_CHANGE_EVENTS
        for index, change in enumerate(changes):
            for position, part in enumerate(("name", "openvr event", "id")):
                edited = list(change)
                # An unused id: one more could be another change's id, which the generator refuses.
                edited[position] = edited[position] + 1000 if part == "id" else changed(edited[position])
                cases.append((contract, "TRACKING_CHANGE_EVENTS", changes[:index] + (tuple(edited),) + changes[index + 1:],
                              f"TRACKING_CHANGE_EVENTS {index} {part}"))
        cases.append((contract, "TRACKING_CHANGE_EVENTS", changes[::-1], "TRACKING_CHANGE_EVENTS order"))
        for name in ("PACKET_HEADER_FIELDS", "SETTINGS_FIELDS", "FRAME_PREFIX_FIELDS",
                     "EXPIRING_FRAME_PREFIX_FIELDS"):
            layout = getattr(contract, name)
            cases.append((contract, name, layout[::-1], f"{name} order"))
            cases.append((contract, name, layout[:-1] + ((layout[-1][0], "Q"),), f"{name} width"))
        for index, (name, low, high) in enumerate(PANEL_LIMITS):
            for position, bound in ((1, "low"), (2, "high")):
                limit = list(PANEL_LIMITS[index])
                limit[position] = changed(limit[position])
                limits = PANEL_LIMITS[:index] + (tuple(limit),) + PANEL_LIMITS[index + 1:]
                cases.append((overlay, "PANEL_LIMITS", limits, f"PANEL_LIMITS {name} {bound}"))
        for field in dataclasses.fields(PanelSettings):
            factory = functools.partial(PanelSettings, **{field.name: changed(getattr(PanelSettings(), field.name))})
            cases.append((overlay, "PanelSettings", factory, f"PanelSettings.{field.name}"))
        for module, name, value, label in cases:
            with self.subTest(value=label), patch.object(module, name, value):
                self.assertNotEqual(native.header_text(), committed)

    def test_check_mode_fails_on_a_stale_header_and_write_restores_it(self):
        from scripts import hud_contract_header as command

        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            header = Path(temporary) / "hud_contract.hpp"
            header.write_text((ROOT / "tools/hud_contract.hpp").read_text())
            self.assertEqual(command.main(["--check", "--header", str(header)]), 0)
            header.write_text(header.read_text().replace("kHudWidthPx = 320", "kHudWidthPx = 321"))
            self.assertEqual(command.main(["--check", "--header", str(header)]), 1)
            self.assertEqual(command.main(["--write", "--header", str(header)]), 0)
            self.assertEqual(header.read_text(), native.header_text())
            self.assertEqual(command.main(["--check", "--header", str(header)]), 0)
            self.assertEqual(sorted(path.name for path in Path(temporary).iterdir()), ["hud_contract.hpp"])
            with self.assertRaises(SystemExit):
                command.main([])

    def test_tracking_reporter_schema_is_generated_once(self):
        from framejacker.sources import session

        header = native.header_text()
        changes = contract.TRACKING_CHANGE_EVENTS
        self.assertIs(session.TRACKING_CHANGES, contract.TRACKING_CHANGES)
        # The pairing itself, written out from the pinned openvr.h and the change names' meaning. The
        # generator, the fixture and the bridge's static_assert all read the table, so none of them
        # catches a swapped name.
        self.assertEqual({name: (event, value) for name, event, value in changes}, {
            "chaperone_universe_changed": ("VREvent_ChaperoneUniverseHasChanged", 801),
            "seated_zero_pose_reset": ("VREvent_SeatedZeroPoseReset", 804),
            "standing_zero_pose_reset": ("VREvent_StandingZeroPoseReset", 808),
            "chaperone_room_setup_committed": ("VREvent_ChaperoneRoomSetupCommitted", 807)})
        self.assertEqual(contract.TRACKING_CHANGES, {name: event for name, event, _ in changes})
        self.assertEqual(len({name for name, _, _ in changes}), len(changes))
        self.assertEqual(len({value for _, _, value in changes}), len(changes))
        self.assertIn("constexpr const char* kTrackingChangeNames[] = {" +
                      ", ".join(f'"{name}"' for name, _, _ in changes) + "};", header)
        self.assertIn("constexpr const char* kTrackingChangeOpenvrEvents[] = {" +
                      ", ".join(f'"{event}"' for _, event, _ in changes) + "};", header)
        self.assertIn("constexpr std::uint32_t kTrackingChangeEventIds[] = {" +
                      ", ".join(str(value) for _, _, value in changes) + "};", header)
        self.assertIn(f'constexpr char kTrackingStateEvent[] = "{contract.TRACKING_STATE_EVENT}";', header)
        self.assertIn(f"constexpr int kTrackingReportMaxSeconds = {contract.TRACKING_REPORT_MAX_SECONDS};", header)
        # The observer reads the same names it is generated from.
        observer = session.TrackingObserver()
        with patch.object(session, "TRACKING_STATE_EVENT", "tracking_snapshot"):
            observer.observe({"event": "tracking_snapshot", contract.TRACKING_UNIVERSE_FIELD: 3,
                              contract.TRACKING_ZERO_POSE_FIELD: "ab" * 32})
        self.assertEqual(observer.read().values["recenter_epoch"], 0)
        for bad in (changes + (changes[0],), changes + (("other", "VREvent_Other", changes[0][2]),),
                    changes[:-1] + ((changes[0][0], changes[-1][1], changes[-1][2]),), ()):
            with self.subTest(bad=bad), patch.object(contract, "TRACKING_CHANGE_EVENTS", bad), \
                    self.assertRaisesRegex(ValueError, "hud_contract_tracking_changes_invalid"):
                native.header_text()

    def test_contract_digest_is_in_the_header_and_follows_the_contract(self):
        digest = native.contract_digest()
        self.assertRegex(digest, "^[0-9a-f]{64}$")
        self.assertIn(f'kContractDigest[] = "{digest}";', native.header_text())
        with patch.object(contract, "MAX_SIDE", contract.MAX_SIDE + 1):
            self.assertNotEqual(native.contract_digest(), digest)


class PythonWireFormatTests(unittest.TestCase):
    def split(self, data):
        header_bytes = struct.calcsize(contract.PACKET_HEADER_FORMAT)
        magic, opcode, size = struct.unpack(contract.PACKET_HEADER_FORMAT, data[:header_bytes])
        self.assertEqual((magic, size), (contract.PACKET_MAGIC, len(data) - header_bytes))
        return opcode, data[header_bytes:]

    def test_marker_tags_match_their_declared_order_and_bits(self):
        self.assertEqual(len(MARKER.corner_ids), len(MARKER.corner_order.split(",")))
        self.assertEqual(len(set(MARKER.corner_ids)), len(MARKER.corner_ids))
        self.assertTrue(all(0 < value < 2 ** MARKER.bits for value in MARKER.corner_ids))
        self.assertEqual(contract.PACKET_HEADER_FIELDS[0], ("magic", f"{len(contract.PACKET_MAGIC)}s"))

    def test_settings_frames_and_stop_use_the_contract_layout(self):
        settings = PanelSettings(width=.3, distance=.6, x=-.5, y=.4, opacity=.7, visible=False)
        opcode, payload = self.split(settings.encode())
        self.assertEqual(opcode, contract.OPCODE_SETTINGS)
        decoded = dict(zip((name for name, _ in contract.SETTINGS_FIELDS),
                           struct.unpack(contract.SETTINGS_FORMAT, payload)))
        self.assertEqual(set(decoded), {field.name for field in dataclasses.fields(PanelSettings)})
        for name, value in decoded.items():
            self.assertAlmostEqual(value, getattr(settings, name), places=6)
        client = OverlayClient(validation_only=True)
        client.submit_frame(3, 2, bytes(3 * 2 * contract.RGBA_BYTES))
        opcode, payload = self.split(client.pending_frame)
        prefix = struct.calcsize(contract.FRAME_PREFIX_FORMAT)
        self.assertEqual(opcode, contract.OPCODE_FRAME)
        self.assertEqual(dict(zip((name for name, _ in contract.FRAME_PREFIX_FIELDS),
                                  struct.unpack(contract.FRAME_PREFIX_FORMAT, payload[:prefix]))),
                         {"width": 3, "height": 2})
        client.frame_expiry_supported = True
        deadline = time.monotonic_ns() + contract.MAX_DISPLAY_AGE_NS // 2
        client.submit_frame(3, 2, bytes(3 * 2 * contract.RGBA_BYTES), expires_ns=deadline)
        opcode, payload = self.split(client.pending_frame)
        prefix = struct.calcsize(contract.EXPIRING_FRAME_PREFIX_FORMAT)
        self.assertEqual(opcode, contract.OPCODE_EXPIRING_FRAME)
        self.assertEqual(dict(zip((name for name, _ in contract.EXPIRING_FRAME_PREFIX_FIELDS),
                                  struct.unpack(contract.EXPIRING_FRAME_PREFIX_FORMAT, payload[:prefix]))),
                         {"expires_ns": deadline, "width": 3, "height": 2})
        self.assertEqual(self.split(packet(contract.OPCODE_STOP)), (contract.OPCODE_STOP, b""))


class PythonLimitSitesTests(unittest.TestCase):
    """Each Python limit site follows the single library constant, not a copy."""

    @staticmethod
    def outcome(call, seconds):
        """The error a site raised for ``seconds`` (its own reason), or None when it returned."""
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                call(seconds)
        except (ValueError, RuntimeError) as error:
            return type(error).__name__, str(error)
        except SystemExit as error:
            return "SystemExit", error.code, "argument --seconds: invalid choice" in stderr.getvalue()
        return None

    def test_marker_duration_sites(self):
        from framejacker.hud import marker, observation
        from scripts import hud_marker_check, hud_marker_observer

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        absent_bridge = Path(temporary.name) / "absent-bridge"
        stop = threading.Event()
        observer_report = dict(hud_marker_observer.new_report(), status="rejected", exit_code=1,
                               maximum_processing_fps=5, eye_identity_assigned=False, composition_verified=False,
                               unseen_or_occluded_copies_established=False)
        argparse_rejected = ("SystemExit", 2, True)
        past_bound = object()  # Any outcome except the bound's own rejection.

        def worker(seconds):
            # Stubs stop a worker that passed its bound before it installs handlers or starts a marker.
            with patch.object(signal, "signal"), patch.object(
                    hud_marker_check, "MarkerClient", side_effect=RuntimeError("marker_run_not_started")):
                return hud_marker_check.worker({"seconds": seconds, "validation_only": True})

        # (module, call, outcome below and above the bounds, outcome at each bound)
        sites = (
            (marker, lambda s: marker.MarkerClient(absent_bridge, validation_only=True).start_marker(s, stop),
             ("ValueError", "marker_duration_out_of_bounds"), ("RuntimeError", "native_marker_bridge_not_built")),
            (marker, lambda s: marker.validate_worker_report(
                dict(marker.new_report(), status="error", exit_code=1, seconds_requested=s), 1),
             ("ValueError", "invalid_marker_worker_report"), None),
            (hud_marker_check, worker, ("ValueError", "invalid_marker_options"), past_bound),
            (hud_marker_check, lambda s: hud_marker_check.main(["--seconds", str(s)]), argparse_rejected, None),
            (hud_marker_observer, lambda s: hud_marker_observer.observe(
                None, s, stop, backend_factory=None, marker_client=None),
             ("ValueError", "observer_duration_out_of_bounds"), ("ValueError", "observer_mirror_opt_in_required")),
            (hud_marker_observer, lambda s: hud_marker_observer.main(["--seconds", str(s)]), argparse_rejected, None),
            # The observer command's report contract, also applied before a mapping candidate.
            (observation, lambda s: observation.validate_observer_report(
                dict(observer_report, seconds_requested=s), 1),
             ("ValueError", "invalid_observer_worker_report"), None),
        )
        for index, (module, call, rejected, accepted) in enumerate(sites):
            for low, high in ((contract.MARKER_MIN_SECONDS, contract.MARKER_MAX_SECONDS), (2, 3)):
                with patch.object(module, "MARKER_MIN_SECONDS", low), patch.object(module, "MARKER_MAX_SECONDS", high):
                    for seconds in (low - 1, high + 1):
                        with self.subTest(site=index, bounds=(low, high), seconds=seconds):
                            self.assertEqual(self.outcome(call, seconds), rejected)
                    for seconds in (low, high):
                        with self.subTest(site=index, bounds=(low, high), seconds=seconds):
                            result = self.outcome(call, seconds)
                            if accepted is past_bound:
                                self.assertNotEqual(result, rejected)
                            else:
                                self.assertEqual(result, accepted)

    def test_display_age_sites(self):
        from framejacker.hud import qualification
        from framejacker.runtime import live

        client = OverlayClient(validation_only=True)
        client.frame_expiry_supported = True
        frame = (1, 1, bytes(contract.RGBA_BYTES))
        for cap in (contract.MAX_DISPLAY_AGE_NS, 50_000_000):
            with self.subTest(cap=cap), patch.object(overlay, "MAX_DISPLAY_AGE_NS", cap):
                with self.assertRaisesRegex(ValueError, "invalid_frame_expiry"):
                    client.submit_frame(*frame, expires_ns=time.monotonic_ns() + cap + 250_000_000)
                # The site reads the clock after this one, so exactly now + cap is within its bound.
                client.submit_frame(*frame, expires_ns=time.monotonic_ns() + cap)
        source, stopped = SimpleNamespace(kind="synthetic"), threading.Event()
        stopped.set()
        candidate = Mock(spec=qualification.MappingCandidate)
        for cap_ms in (contract.MAX_DISPLAY_AGE_MS, 20):
            with self.subTest(cap_ms=cap_ms), patch.object(live, "MAX_DISPLAY_AGE_MS", cap_ms), \
                    patch.object(qualification, "MAX_DISPLAY_AGE_NS", cap_ms * 1_000_000):
                with self.assertRaisesRegex(ValueError, "pipeline_limits_invalid"):
                    live.run_pipeline(source, None, None, 1, max_age_ms=cap_ms + 1, stop=stopped)
                self.assertEqual(live.run_pipeline(source, None, None, 1, max_age_ms=cap_ms, stop=stopped)["status"],
                                 "cancelled")
                with self.assertRaisesRegex(ValueError, "maximum_age"):
                    qualification.ValidationLedger(candidate, {}, 0, max_age_ns=cap_ms * 1_000_000 + 1)
                qualification.ValidationLedger(candidate, {}, 0, max_age_ns=cap_ms * 1_000_000)
        self.assertEqual(contract.MAX_DISPLAY_AGE_MS * 1_000_000, contract.MAX_DISPLAY_AGE_NS)

    def test_frame_side_site(self):
        client = OverlayClient(validation_only=True)
        side = contract.MAX_SIDE
        client.submit_frame(side, 1, bytes(side * contract.RGBA_BYTES))
        with self.assertRaisesRegex(ValueError, "Invalid RGBA overlay frame"):
            client.submit_frame(side + 1, 1, bytes((side + 1) * contract.RGBA_BYTES))
        with patch.object(overlay, "MAX_SIDE", 4), self.assertRaisesRegex(ValueError, "Invalid RGBA overlay frame"):
            client.submit_frame(5, 1, bytes(5 * contract.RGBA_BYTES))


@unittest.skipUnless(LINUX_BRIDGE, "Build scripts/build_overlay_bridge.sh first")
class NativeContractTests(unittest.TestCase):
    """The rebuilt bridge applies the generated values: validation modes, no OpenVR."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(self.directory / "absent.vrpath"))

    def run_bridge(self, *arguments, data=b""):
        result = subprocess.run([str(DEFAULT_BRIDGE), *arguments], input=data, env=self.env,
                                capture_output=True, timeout=10)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def render(self, name):
        path = self.directory / name
        result, messages = self.run_bridge("--render-calibration-marker", str(path))
        self.assertEqual(result.returncode, 0, messages)
        report = messages[-1]
        self.assertEqual((report["status"], report["exit_code"]), ("rendered", 0))
        self.assertIs(report["runtime_initialized"], False)
        self.assertIs(report["validation_only"], True)
        self.assertEqual(report["contract_digest"], native.contract_digest(), "rebuild the bridge")
        return path.read_bytes(), report

    def test_built_bridge_reports_the_current_contract(self):
        _, report = self.render("marker.rgba")
        self.assertEqual(report["config_digest"], contract.config_digest(HUD_CONFIG))
        described = report["marker"]
        self.assertEqual((described["width"], described["height"]), (HUD_CONFIG.width_px, HUD_CONFIG.height_px))
        self.assertEqual(described["corner_ids"], list(MARKER.corner_ids))
        self.assertEqual(described["corner_order"], MARKER.corner_order)
        self.assertEqual((described["border_pixels"], described["tag_size_pixels"], described["tag_inset_pixels"]),
                         (MARKER.border_px, MARKER.tag_size_px, MARKER.tag_inset_px))
        self.assertEqual(described["config_id"], HUD_CONFIG.layout_id)
        for key in ("panel_width_m", "distance_m", "x_m", "y_m", "opacity"):
            self.assertAlmostEqual(described[key], getattr(HUD_CONFIG, key), places=6)

    def test_render_is_validation_only_and_never_replaces_a_file(self):
        existing = self.directory / "existing.rgba"
        existing.write_bytes(b"keep")
        for arguments, code in (((str(existing),), 1), ((), 64), (("",), 64),
                                ((str(self.directory / "a"), str(self.directory / "b")), 64)):
            with self.subTest(arguments=arguments):
                result, messages = self.run_bridge("--render-calibration-marker", *arguments)
                self.assertEqual((result.returncode, messages[-1]["exit_code"]), (code, code))
                self.assertEqual(messages[-1]["status"], "error")
                self.assertIs(messages[-1]["runtime_initialized"], False)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ["existing.rgba"])

    @unittest.skipUnless(HAVE_NUMPY, "NumPy is required for the observer template")
    def test_native_marker_matches_observer_template_pixel_for_pixel(self):
        import numpy as np
        from framejacker.hud.observation import marker_template

        template = marker_template()
        self.assertEqual(template.shape, (HUD_CONFIG.height_px, HUD_CONFIG.width_px))
        for frame in (1, 2, 3):
            with self.subTest(frame=frame):
                data, _ = self.render(f"frame-{frame}.rgba")
                self.assertEqual(len(data), HUD_CONFIG.width_px * HUD_CONFIG.height_px * contract.RGBA_BYTES)
                rgba = np.frombuffer(data, np.uint8).reshape(HUD_CONFIG.height_px, HUD_CONFIG.width_px, 4)
                self.assertTrue((rgba[:, :, 3] == 255).all(), "native marker must be opaque")
                for channel in range(3):
                    mismatched = np.argwhere(rgba[:, :, channel] != template)
                    self.assertEqual(len(mismatched), 0, f"channel {channel}: first (y, x) {mismatched[:5].tolist()}")

    @unittest.skipUnless(HAVE_NUMPY and HAVE_CV2, "NumPy and OpenCV are required for the observer")
    def test_frame_three_decodes_the_native_tag_bits(self):
        """V4's shape: two clear baseline reads, then the marker analysed on read 3."""
        import numpy as np
        from framejacker.hud.contract import CaptureFormat
        from framejacker.hud.observation import BaselineGate, EmpiricalObservation, ObserverConfig

        data, report = self.render("marker.rgba")
        gray = np.frombuffer(data, np.uint8).reshape(HUD_CONFIG.height_px, HUD_CONFIG.width_px, 4)[:, :, 0]
        decoded = []
        for tag in range(len(MARKER.corner_ids)):
            x, y = MARKER.tag_origin(tag)
            value = 0
            for bit in range(MARKER.bits):
                bx, by = MARKER.bit_origin(bit)
                centre = gray[y + by + MARKER.bit_size_px // 2, x + bx + MARKER.bit_size_px // 2]
                value |= int(centre == MARKER.bit_set_value) << bit
            decoded.append(value)
        self.assertEqual(decoded, list(MARKER.corner_ids))
        self.assertEqual(decoded, report["marker"]["corner_ids"])

        frames = [np.full((360, 640, 3), level, np.uint8) for level in (40, 41, 40)]
        left, top = 100, 80
        frames[2][top:top + HUD_CONFIG.height_px, left:left + HUD_CONFIG.width_px] = gray[:, :, None]
        baseline = BaselineGate(deadline=10 ** 18)
        self.assertFalse(baseline.accept(frames[0], 1, 1, 0))
        self.assertTrue(baseline.accept(frames[1], 2, 2, 0))
        observation = EmpiricalObservation(ObserverConfig(expected_copies=1))
        observation.accept(frames[2], CaptureFormat.bgr(frames[2]), 3, 3, config_id=HUD_CONFIG.layout_id)
        diagnostics = observation.last_match_diagnostics
        self.assertEqual((diagnostics["sequence"], diagnostics["status"]), (3, "complete_copies"))
        self.assertEqual((diagnostics["complete_copy_count"], diagnostics["orphan_fragment_count"]), (1, 0))
        best = diagnostics["best_candidates"][0]
        self.assertEqual(best["failed_checks"], [])
        self.assertEqual((best["maximum_tag_mae"], best["maximum_bit_mae"]), (0.0, 0.0))
        right, bottom = left + HUD_CONFIG.width_px - 1, top + HUD_CONFIG.height_px - 1
        corners = np.array(observation.last_corners)
        expected = np.array([[[left, top], [right, top], [right, bottom], [left, bottom]]])
        self.assertEqual(corners.shape, expected.shape)
        self.assertLessEqual(np.abs(corners - expected).max(), 1)

    def test_marker_duration_bounds_follow_the_contract(self):
        for seconds in (contract.MARKER_MIN_SECONDS - 1, contract.MARKER_MAX_SECONDS + 1):
            with self.subTest(seconds=seconds):
                result, messages = self.run_bridge("--validate-calibration-marker", "--seconds", str(seconds))
                self.assertEqual(result.returncode, 64)
                self.assertEqual(messages[-1]["error"], "seconds_out_of_range")
        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-calibration-marker", "--seconds",
                                    str(contract.MARKER_MAX_SECONDS)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
            process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
        report = json.loads(output.splitlines()[-1])
        self.assertEqual((report["status"], report["seconds_requested"]), ("stopped", contract.MARKER_MAX_SECONDS))
        self.assertEqual(report["display_deadline_ns"] - report["native_started_ns"],
                         contract.MARKER_MAX_SECONDS * 1_000_000_000)

    def test_packet_limits_follow_the_contract(self):
        def frame(width, height):
            return packet(contract.OPCODE_FRAME, struct.pack(contract.FRAME_PREFIX_FORMAT, width, height) +
                          bytes(width * height * contract.RGBA_BYTES))

        def settings(**values):
            fields = dict(dataclasses.asdict(PanelSettings()), **values)
            return packet(contract.OPCODE_SETTINGS, struct.pack(
                contract.SETTINGS_FORMAT, *(fields[name] for name, _ in contract.SETTINGS_FIELDS)))

        accepted = frame(contract.MAX_SIDE, 1) + frame(1, contract.MAX_SIDE)
        for name, low, high in PANEL_LIMITS:
            accepted += settings(**{name: low}) + settings(**{name: high})
        result, messages = self.run_bridge("--validate-stdin", data=accepted + packet(contract.OPCODE_STOP))
        self.assertEqual(result.returncode, 0, messages)
        self.assertEqual((messages[-1]["frames_received"], messages[-1]["settings_received"]),
                         (2, 2 * len(PANEL_LIMITS)))
        opcodes = (contract.OPCODE_FRAME, contract.OPCODE_SETTINGS, contract.OPCODE_STOP,
                   contract.OPCODE_EXPIRING_FRAME)
        rejected = [(frame(contract.MAX_SIDE + 1, 1), "invalid_rgba_frame"),
                    (frame(1, contract.MAX_SIDE + 1), "invalid_rgba_frame"),
                    (packet(max(opcodes) + 1), "invalid_packet_header"), (packet(0), "invalid_packet_header"),
                    (packet(contract.OPCODE_SETTINGS, bytes(struct.calcsize(contract.SETTINGS_FORMAT) - 1)),
                     "invalid_packet_size"),
                    (b"FJO0" + packet(contract.OPCODE_STOP)[4:], "invalid_packet_magic")]
        for name, low, high in PANEL_LIMITS:
            rejected += [(settings(**{name: low - .01}), "panel_setting_out_of_range"),
                         (settings(**{name: high + .01}), "panel_setting_out_of_range")]
        for data, error in rejected:
            with self.subTest(data=data[:12], error=error):
                result, messages = self.run_bridge("--validate-stdin", data=data)
                self.assertEqual(result.returncode, 1, messages)
                self.assertIn(error, messages[-1]["error"])

    def test_expiry_cap_follows_the_contract(self):
        def expiring(deadline):
            return packet(contract.OPCODE_EXPIRING_FRAME,
                          struct.pack(contract.EXPIRING_FRAME_PREFIX_FORMAT, deadline, 1, 1) +
                          bytes(contract.RGBA_BYTES))

        process = subprocess.Popen([str(DEFAULT_BRIDGE), "--validate-stdin"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, env=self.env)
        events = queue.Queue()
        reader = threading.Thread(target=lambda: [events.put(json.loads(line)) for line in process.stdout],
                                  daemon=True)
        try:
            reader.start()
            self.assertEqual(events.get(timeout=3)["event"], "ready")
            # The bridge reads CLOCK_MONOTONIC after this, so exactly now + cap is within its bound and a
            # lower native cap rejects it. Rejection one nanosecond past the cap is in overlay_dispatch_fixture.cpp.
            process.stdin.write(expiring(time.monotonic_ns() + contract.MAX_DISPLAY_AGE_NS))
            process.stdin.flush()
            self.assertEqual(events.get(timeout=3)["event"], "frame_validated")
            process.stdin.write(expiring(time.monotonic_ns() + contract.MAX_DISPLAY_AGE_NS + 400_000_000))
            process.stdin.flush()
            while (event := events.get(timeout=3))["event"] == "frame_expired":
                pass
            self.assertEqual(event["error"], "invalid_frame_expiry")
            self.assertEqual(process.wait(timeout=3), 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdin.close()
            reader.join(timeout=3)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
