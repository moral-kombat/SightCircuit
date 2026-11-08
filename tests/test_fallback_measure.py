"""The fallback measurement command (F3b): cloud checks only, no device and no claim about the Frame.

The validation bridge runs end to end (every hide ends missing, every show
returns ``no_image``, nothing shown); a scripted fake marker bridge supplies
the confirmed ``overlay_hidden`` and ``overlay_shown`` lines no cloud runtime
can, with a fixture mirror whose frames follow the fake's visibility; the
bookkeeping, the report contract, the opt-in rules and the manifest check are
exercised without either.
"""
import functools
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from sightcircuit.hud import observation as hud_observation
from sightcircuit.hud.contract import HUD_CONFIG, VISIBILITY_EVENT_BOUND_NS
from sightcircuit.hud.marker import MarkerClient
from sightcircuit.overlay import DEFAULT_BRIDGE, PanelSettings, VisibilityRequest
from sightcircuit.runtime import manifest
from scripts import fallback_measure
from tests.test_checkpoint_manifest import CheckoutCase

ROOT = Path(__file__).resolve().parents[1]
READ = {"sequence": 0, "observed_ns": 0, "digest": "d", "present": True, "decode": "complete_copy"}


def read(sequence, observed_ns, present, digest=None):
    return dict(READ, sequence=sequence, observed_ns=observed_ns, present=present,
                digest=digest or f"{sequence:032x}", decode="complete_copy" if present else "no_complete_copy")


def scene(marker, *, width=1000, height=600):
    image = np.full((height, width, 3), 90, np.uint8)
    if marker:
        image[100:228, 100:420] = hud_observation.marker_template()[:, :, None]
    return image


class TransitionBoundsTests(unittest.TestCase):
    """The cycle bookkeeping: pure functions of read records and a request time."""

    def test_a_hide_has_its_upper_bound_at_the_first_absent_read_and_its_lower_at_the_last_present(self):
        reads = [read(1, 900, True), read(2, 1_050, True), read(3, 1_150, False), read(4, 1_250, False)]
        bounds = fallback_measure.transition_bounds(reads, 1_000, False)
        self.assertEqual(bounds, {"first_absent_observed_ns": 1_150, "last_present_observed_ns": 1_050,
                                  "reads_after_request_before_transition": 1,
                                  "upper_bound_after_request_ns": 150, "lower_bound_after_request_ns": 50})

    def test_the_lower_bound_is_floored_at_the_request_when_the_last_present_read_preceded_it(self):
        reads = [read(1, 900, True), read(2, 1_150, False)]
        bounds = fallback_measure.transition_bounds(reads, 1_000, False)
        self.assertEqual((bounds["last_present_observed_ns"], bounds["lower_bound_after_request_ns"],
                          bounds["upper_bound_after_request_ns"]), (900, 0, 150))

    def test_a_transition_not_yet_seen_has_no_bounds(self):
        reads = [read(1, 900, True), read(2, 1_100, True)]
        bounds = fallback_measure.transition_bounds(reads, 1_000, False)
        self.assertIsNone(bounds["first_absent_observed_ns"])
        self.assertIsNone(bounds["upper_bound_after_request_ns"])
        self.assertEqual((bounds["last_present_observed_ns"], bounds["reads_after_request_before_transition"]),
                         (1_100, 1))

    def test_a_show_is_the_symmetric_reading(self):
        reads = [read(1, 950, False), read(2, 1_020, False), read(3, 1_120, True)]
        bounds = fallback_measure.transition_bounds(reads, 1_000, True)
        self.assertEqual((bounds["first_present_observed_ns"], bounds["last_absent_observed_ns"],
                          bounds["upper_bound_after_request_ns"], bounds["lower_bound_after_request_ns"]),
                         (1_120, 1_020, 120, 20))

    def test_repeat_counts_are_consecutive_identical_digests(self):
        reads = [read(1, 0, True, "a"), read(2, 100, True, "a"), read(3, 200, True, "a"), read(4, 300, False, "b"),
                 read(5, 400, False, "a"), read(6, 500, False, "a")]
        self.assertEqual(fallback_measure.repeat_counts(reads),
                         {"reads": 6, "identical_consecutive_digests": 3, "longest_repeat_run": 2,
                          "distinct_digests": 2, "maximum_read_gap_ns": 100, "reads_with_complete_copy": 3})
        self.assertEqual(fallback_measure.repeat_counts([])["maximum_read_gap_ns"], None)

    def test_a_frame_record_keeps_a_digest_and_presence_never_pixels(self):
        present = fallback_measure.frame_record(3, 12, scene(True))
        absent = fallback_measure.frame_record(4, 13, scene(False))
        self.assertEqual((present["present"], present["decode"], absent["present"], absent["decode"]),
                         (True, "complete_copy", False, "no_complete_copy"))
        self.assertEqual(set(present), {"sequence", "observed_ns", "digest", "present", "decode"})
        self.assertEqual(len(present["digest"]), 32)
        self.assertNotEqual(present["digest"], absent["digest"])
        with patch.object(hud_observation, "locate_copies", side_effect=ValueError("ambiguous_marker_orientation")), \
                patch.object(fallback_measure, "locate_copies", hud_observation.locate_copies):
            ambiguous = fallback_measure.frame_record(5, 14, scene(True))
        self.assertEqual((ambiguous["present"], ambiguous["decode"]), (False, "ambiguous_marker_orientation"))
        with patch.object(fallback_measure, "locate_copies", side_effect=ValueError("observer_pixel_budget")):
            with self.assertRaisesRegex(ValueError, "observer_pixel_budget"):
                fallback_measure.frame_record(6, 15, scene(True))

    def test_the_sections_follow_the_four_measurement_kinds(self):
        hide = {"visible": False, "requested_monotonic_ns": 1, "request_ns": 1_000, "returned_ns": 1_010,
                "call": "HideOverlay", "terminal": "overlay_hidden", "confirmed": True, "reason": None,
                "event_observed_ns": 1_030, "waited_ns": None, "event_after_returned_ns": 20, "lines": [],
                "mirror": {"measured": True, **fallback_measure.transition_bounds(
                    [read(1, 990, True), read(2, 1_100, False)], 1_000, False)}, "ended_by": "transition_observed"}
        show = dict(hide, visible=True, request_ns=2_000, returned_ns=2_010, call="ShowOverlay",
                    terminal="visibility_event_missing", confirmed=False, reason="visibility_event_missing",
                    event_observed_ns=None, event_after_returned_ns=None, waited_ns=250_000_000,
                    mirror={"measured": True, **fallback_measure.transition_bounds(
                        [read(2, 1_100, False), read(3, 2_150, True)], 2_000, True)})
        records = [read(1, 990, True, "a"), read(2, 1_100, False, "b"), read(3, 2_150, True, "b")]
        sections = fallback_measure.sections([{"cycle": 1, "hide": hide, "show": show}], records, False)
        self.assertEqual(set(sections), set(fallback_measure.MEASUREMENTS))
        absence = sections["hide_to_absence"]
        self.assertEqual((absence["measured"], absence["cycles"], absence["transitions_observed"],
                          absence["maximum_upper_bound_after_request_ns"]), (True, 1, 1, 100))
        self.assertEqual(absence["per_cycle"][0]["upper_bound_after_event_ns"], 70)
        delivery = sections["hidden_event_delivery"]
        self.assertEqual((delivery["events_delivered"], delivery["events_missing"],
                          delivery["maximum_event_after_returned_ns"]), (1, 0, 20))
        visible = sections["show_to_visible"]
        self.assertEqual((visible["transitions_observed"], visible["maximum_upper_bound_after_request_ns"]), (1, 150))
        self.assertEqual((visible["shown_event_delivery"]["events_missing"],
                          visible["shown_event_delivery"]["per_cycle"][0]["waited_ns"]), (1, 250_000_000))
        repeats = sections["loopback_repeats"]
        self.assertEqual((repeats["measured"], repeats["identical_consecutive_digests"], len(repeats["reads_detail"])),
                         (True, 1, 3))
        self.assertEqual(set(repeats["reads_detail"][0]), {"sequence", "observed_ns", "digest", "present", "decode"})
        # Validation: the mirror sections say they were not measured, the event sections still are.
        validation = fallback_measure.sections([{"cycle": 1, "hide": hide, "show": show}], [], True)
        self.assertFalse(validation["hide_to_absence"]["measured"])
        self.assertFalse(validation["loopback_repeats"]["measured"])
        self.assertTrue(validation["hidden_event_delivery"]["measured"])


class ReportContractTests(unittest.TestCase):
    def good(self, **changes):
        report = dict(fallback_measure.new_report(True, cycles=1), status="measured", exit_code=0, cycles_completed=1,
                      cycles_not_started=0, seconds_requested=3, cycles=[{}],
                      hidden_event_delivery={"measured": True},
                      show_to_visible={"measured": False, "shown_event_delivery": {"measured": True}},
                      bridge={"validation_only": True, "worker_reaped": True, "worker_exit_code": 0,
                              "transport_threads_stopped": True, "forced_termination": False, "error": None,
                              "native_report": {"cleanup_ok": True, "exit_code": 0, "mode": "--validate-calibration-marker",
                                                "runtime_initialized": False, "visibility_requests_received": 2}})
        report.update(changes)
        return report

    def test_the_new_report_claims_nothing(self):
        report = fallback_measure.new_report()
        self.assertEqual((report["status"], report["exit_code"], report["cycles_requested"]), ("not_run", 3, 5))
        for key in ("images_saved", "inference_started", "production_live_input_enabled", "mapping_qualified",
                    "production_hud_enabled", "physical_display_recovery_verified", "camera_freshness_verified"):
            self.assertIs(report[key], False, key)
        self.assertEqual([report[name] for name in fallback_measure.MEASUREMENTS], [{"measured": False}] * 4)
        self.assertIn("timebase_id", report["clock"]["timebase_caveat"])
        self.assertEqual(report["pacing"]["visibility_event_bound_ns"], VISIBILITY_EVENT_BOUND_NS)

    def test_a_clean_validation_report_is_accepted_and_each_inconsistency_refused(self):
        fallback_measure.validate_worker_report(self.good(), 0)
        for changes, error in (({"report_kind": "other"}, "invalid"), ({"exit_code": 2}, "invalid"),
                               ({"cleanup_ok": "yes"}, "invalid"), ({"images_saved": True}, "invalid"),
                               ({"mapping_qualified": True}, "invalid"), ({"cycles_requested": 0}, "invalid"),
                               ({"cycles_completed": 2}, "invalid"), ({"cycles": []}, "invalid"),
                               ({"loopback_repeats": {}}, "invalid"), ({"seconds_requested": 21}, "invalid"),
                               ({"status": "rejected"}, "inconsistent"), ({"cleanup_ok": False}, "inconsistent"),
                               ({"hide_to_absence": {"measured": True}}, "inconsistent")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, error):
                fallback_measure.validate_worker_report(self.good(**changes), 0)
        with self.assertRaisesRegex(ValueError, "invalid"):
            fallback_measure.validate_worker_report(self.good(), 1)
        bad_native = self.good()
        bad_native["bridge"]["native_report"]["visibility_requests_received"] = 3
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            fallback_measure.validate_worker_report(bad_native, 0)
        # A rejected run with exit 1 is a valid report; the exit code alone must agree.
        fallback_measure.validate_worker_report(self.good(status="rejected", exit_code=1, reason="x"), 1)

    def test_measure_refuses_bad_bounds_and_partial_opt_ins_before_anything_starts(self):
        client = MarkerClient(validation_only=True)
        stop = threading.Event()
        for seconds, cycles, error in ((0, 1, "duration"), (21, 1, "duration"), (3, 0, "cycles"),
                                       (3, fallback_measure.MAX_CYCLES + 1, "cycles")):
            with self.subTest(seconds=seconds, cycles=cycles), self.assertRaisesRegex(ValueError, error):
                fallback_measure.measure(None, seconds, cycles, stop, backend_factory=None, marker_client=client,
                                         validation_only=True)
        with self.assertRaisesRegex(ValueError, "fallback_validate_reads_no_device"):
            fallback_measure.measure({"node": "/dev/video99"}, 3, 1, stop, backend_factory=None, marker_client=client,
                                     validation_only=True)
        with self.assertRaisesRegex(ValueError, "fallback_validate_reads_no_device"):
            fallback_measure.measure(None, 3, 1, stop, backend_factory=None,
                                     marker_client=MarkerClient(validation_only=False), validation_only=True)
        with self.assertRaisesRegex(ValueError, "mirror_opt_in_required"):
            fallback_measure.measure({"node": "/dev/video99"}, 3, 1, stop, backend_factory=None, marker_client=client)
        with self.assertRaisesRegex(ValueError, "output_opt_in_required"):
            fallback_measure.measure({"node": "/dev/video99"}, 3, 1, stop, backend_factory=None,
                                     marker_client=MarkerClient(validation_only=False), allow_mirror_observation=True)
        class Backend:
            kind = "native"
        with self.assertRaisesRegex(ValueError, "fixture_backend_required"):
            fallback_measure.measure({"node": "x"}, 3, 1, stop, backend_factory=Backend, marker_client=client)
        self.assertIsNone(client.process)


class CommandLineTests(unittest.TestCase):
    """The launcher's rules; the supervisor and the device discovery are stubbed so nothing runs."""

    def setUp(self):
        self.supervise = patch.object(fallback_measure, "supervise", side_effect=AssertionError("must not run"))
        self.discover = patch.object(fallback_measure, "discover", side_effect=AssertionError("must not discover"))
        self.supervise.start()
        self.discover.start()
        self.addCleanup(self.supervise.stop)
        self.addCleanup(self.discover.stop)

    def test_partial_opt_ins_and_missing_mode_are_argument_errors(self):
        for argv in ([], ["--measure-fallback"], ["--measure-fallback", "--allow-mirror-observation"],
                     ["--measure-fallback", "--allow-mirror-observation", "--allow-calibration-output"],
                     ["--allow-mirror-observation", "--device", "/dev/video99"],
                     ["--validate", "--device", "/dev/video99"], ["--validate", "--allow-calibration-output"],
                     ["--validate", "--seconds", "0"], ["--validate", "--seconds", "21"],
                     ["--validate", "--cycles", "0"], ["--validate", "--cycles", str(fallback_measure.MAX_CYCLES + 1)],
                     # The device run needs the manifest, even with every flag.
                     ["--measure-fallback", "--allow-mirror-observation", "--allow-calibration-output",
                      "--device", "/dev/video99"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                fallback_measure.main(argv)
            self.assertEqual(caught.exception.code, 2)

    def test_the_worker_option_requires_the_explicit_scope(self):
        with self.assertRaisesRegex(ValueError, "fallback_measure_explicit_scope_required"):
            fallback_measure.main(["--worker", json.dumps({"approved_scope": "marker_observation_only"})])

    def test_the_validate_run_passes_a_validation_worker_with_no_candidate(self):
        self.supervise.stop()
        with patch.object(fallback_measure, "supervise", return_value={"exit_code": 0, "status": "measured"}) as run:
            with patch("builtins.print"):
                self.assertEqual(fallback_measure.main(["--validate", "--seconds", "3", "--cycles", "2"]), 0)
        command, base, timeout, validator = run.call_args.args
        options = json.loads(command[command.index("--worker") + 1])
        self.assertEqual(options, {"approved_scope": fallback_measure.APPROVED_SCOPE, "candidate": None, "seconds": 3,
                                   "cycles": 2, "validation_only": True, "bridge": None})
        self.assertEqual((base["validation_only"], base["cycles_requested"], timeout), (True, 2, 15))
        self.assertIs(validator, fallback_measure.validate_worker_report)
        self.assertIs(run.call_args.kwargs["descendants"], True)
        self.assertEqual(command[1], str(Path(fallback_measure.__file__).resolve()))


class ManifestGateTests(CheckoutCase):
    """The checkpoint manifest is verified before anything starts; a dirty tree refuses the run."""

    def setUp(self):
        super().setUp()
        self.verify = patch.object(fallback_measure, "verify_manifest",
                                   functools.partial(manifest.verify_manifest, root=self.root))
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def refuse_everything(self):
        for name in ("supervise", "discover", "MarkerClient"):
            patcher = patch.object(fallback_measure, name, side_effect=AssertionError(f"{name} must not run"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, argv):
        printed = []
        with patch("builtins.print", side_effect=lambda text, **_: printed.append(text)):
            code = fallback_measure.main(argv)
        return code, json.loads(printed[-1])

    def test_a_dirty_tree_refuses_the_run_before_the_bridge_or_the_device(self):
        manifest.write_manifest(self.path, self.root)
        (self.root / "README.md").write_text("Checkout, edited\n")
        self.refuse_everything()
        for argv in (["--validate", "--manifest", str(self.path), "--seconds", "1"],
                     ["--measure-fallback", "--allow-mirror-observation", "--allow-calibration-output",
                      "--device", "/dev/video99", "--manifest", str(self.path)]):
            with self.subTest(argv=argv):
                code, report = self.run_main(argv)
                self.assertEqual((code, report["status"], report["exit_code"], report["reason"]),
                                 (1, "manifest_not_verified", 1, "checkpoint_manifest_differs_or_tree_dirty"))
                self.assertEqual(report["manifest"]["status"], "differs")
                self.assertIn("git.dirty", [item["field"] for item in report["manifest"]["differences"]])
                self.assertEqual(report["cycles_completed"], 0)

    def test_a_changed_bridge_refuses_the_run_too(self):
        manifest.write_manifest(self.path, self.root)
        self.bridge.write_bytes(b"bridge rebuilt since the manifest")
        self.refuse_everything()
        code, report = self.run_main(["--validate", "--manifest", str(self.path), "--seconds", "1"])
        self.assertEqual((code, report["status"]), (1, "manifest_not_verified"))
        self.assertIn("bridge", {item["field"].split(".")[0] for item in report["manifest"]["differences"]})

    def test_a_verified_manifest_lets_the_run_start_and_travels_in_the_report(self):
        manifest.write_manifest(self.path, self.root)
        with patch.object(fallback_measure, "supervise", return_value={"exit_code": 0, "status": "measured"}) as run:
            code, _ = self.run_main(["--validate", "--manifest", str(self.path), "--seconds", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[1]["manifest"]["status"], "verified")


@unittest.skipUnless(DEFAULT_BRIDGE.is_file(), "Build scripts/build_overlay_bridge.sh first")
class MarkerClientRequestTests(unittest.TestCase):
    """``MarkerClient.request_visibility`` against the real validation marker bridge."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = dict(os.environ, VR_PATHREG_OVERRIDE=str(Path(self.temporary.name) / "absent.vrpath"))

    def test_requests_are_written_to_the_marker_and_read_with_the_f2_outcome(self):
        client = MarkerClient(validation_only=True, env=self.env)
        stop = threading.Event()
        with self.assertRaisesRegex(RuntimeError, "marker_client_not_running"):
            client.request_visibility(1, False)
        client.start_marker(3, stop)
        try:
            self.assertIsNone(client.writer)  # still no writer thread: nothing but requests reach the bridge
            with self.assertRaisesRegex(ValueError, "marker_visibility_takes_no_settings"):
                client.request_visibility(1, False, settings=PanelSettings())
            hide = client.request_visibility(1, False)
            self.assertIsInstance(hide, VisibilityRequest)
            outcome = client.await_visibility(hide)
            self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason, outcome.call),
                             (False, "visibility_event_missing", "visibility_event_missing", "none"))
            self.assertGreaterEqual(outcome.waited_ns, VISIBILITY_EVENT_BOUND_NS)
            show = client.request_visibility(2, True)
            outcome = client.await_visibility(show)
            self.assertEqual((outcome.confirmed, outcome.terminal, outcome.reason), (False, "show_returned", "no_image"))
            self.assertEqual([line["event"] for line in client.cycle_lines(1)],
                             ["hide_requested", "hide_returned", "visibility_event_missing"])
            self.assertEqual(client.visibility_requests, 2)
        finally:
            report = client.close()
        self.assertIsNone(report["error"], report)
        native = report["native_report"]
        self.assertEqual((native["status"], native["visibility_requests_received"], native["visibility_events_missing"]),
                         ("validated_marker", 2, 2))
        with self.assertRaisesRegex(RuntimeError, "marker_client_not_running"):
            client.request_visibility(3, False)


@unittest.skipUnless(sys.platform.startswith("linux") and DEFAULT_BRIDGE.is_file(), "Linux native bridge required")
class ValidationRunTests(unittest.TestCase):
    """The ``--validate`` run end to end: the launcher, the guardian, the worker and the real validation bridge."""

    def test_the_validate_run_measures_the_event_paths_and_reads_nothing(self):
        printed = []
        env = patch.dict(os.environ, {"VR_PATHREG_OVERRIDE": str(Path(tempfile.gettempdir()) / "absent-sightcircuit.vrpath")})
        env.start()
        self.addCleanup(env.stop)
        with patch("builtins.print", side_effect=lambda text, **_: printed.append(text)), \
                patch.object(fallback_measure, "discover", side_effect=AssertionError("validate must not discover")):
            started = time.monotonic()
            code = fallback_measure.main(["--validate", "--seconds", "4", "--cycles", "2"])
            elapsed = time.monotonic() - started
        report = json.loads(printed[-1])
        self.assertEqual((code, report["status"], report["exit_code"]), (0, "measured", 0), report)
        self.assertLess(elapsed, 12)
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual((report["validation_only"], report["cycles_requested"], report["cycles_completed"],
                          report["cycles_not_started"], report["capture_started"]), (True, 2, 2, 0, False))
        self.assertNotIn("capture", report)
        for key in ("images_saved", "inference_started", "production_hud_enabled", "mapping_qualified",
                    "production_live_input_enabled", "physical_display_recovery_verified"):
            self.assertIs(report[key], False, key)
        hidden = report["hidden_event_delivery"]
        self.assertEqual((hidden["measured"], hidden["requests"], hidden["events_delivered"], hidden["events_missing"]),
                         (True, 2, 0, 2))
        self.assertTrue(all(entry["waited_ns"] >= VISIBILITY_EVENT_BOUND_NS for entry in hidden["per_cycle"]))
        shown = report["show_to_visible"]["shown_event_delivery"]
        self.assertEqual((shown["events_delivered"], shown["returned_not_ok"]), (0, 2))
        self.assertEqual({entry["reason"] for entry in shown["per_cycle"]}, {"no_image"})
        for name in ("hide_to_absence", "loopback_repeats", "show_to_visible"):
            self.assertIs(report[name]["measured"], False, name)
        self.assertEqual([cycle["cycle"] for cycle in report["cycles"]], [1, 2])
        for cycle in report["cycles"]:
            self.assertEqual(cycle["hide"]["mirror"], {"measured": False, "reason": "validation_reads_no_mirror"})
            self.assertEqual(cycle["hide"]["ended_by"], "no_mirror")
            self.assertLessEqual(cycle["hide"]["request_ns"], cycle["hide"]["returned_ns"])
            self.assertLessEqual(cycle["hide"]["returned_ns"], cycle["show"]["request_ns"])
            # The pacing floor between the hide and the show, on the bridge's clock.
            self.assertGreaterEqual(cycle["show"]["request_ns"] - cycle["hide"]["request_ns"],
                                    fallback_measure.REQUEST_GAP_NS - 50_000_000)
        native = report["bridge"]["native_report"]
        self.assertEqual((native["mode"], native["runtime_initialized"], native["status"],
                          native["visibility_requests_received"], native["visibility_events_missing"]),
                         ("--validate-calibration-marker", False, "validated_marker", 4, 4))
        self.assertTrue(report["guardian_reaped"])
        self.assertTrue(report["worker_tree_reaped"])


@unittest.skipUnless(os.name == "posix", "The fake bridge uses a POSIX shebang")
class FakeBridgeMeasurementTests(unittest.TestCase):
    """A scripted marker bridge confirms every transition, and a fixture mirror follows its visibility.

    The fake writes the lines the cloud's validation bridge never can
    (``overlay_hidden``, ``overlay_shown``) a few milliseconds after each
    request and records its visibility in a state file that the fixture
    backend reads, so the measurement sees the tile vanish and return in the
    frames it reads. No runtime, no device: a check of the command's
    bookkeeping against known transitions, never a Frame measurement.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "visible"
        self.state.write_text("1")

    def fake_bridge(self, *, event_delay_ms=15, confirm=True):
        path = Path(self.temporary.name) / "fake-marker-bridge"
        path.write_text(f"""#!{sys.executable}
import json, os, select, struct, sys, time
HEADER = struct.Struct("<4sII")
now = time.monotonic_ns
seconds = int(sys.argv[sys.argv.index("--seconds") + 1])
validate = sys.argv[1] == "--validate-calibration-marker"
started = now()
deadline = started + seconds * 1000000000
state = {self.state.as_posix()!r}
def emit(obj):
    sys.stdout.write(json.dumps(dict(obj, report_kind="sightcircuit_overlay_bridge", schema_version=1)) + "\\n")
    sys.stdout.flush()
def line(event, cycle, **fields):
    return {{"event": event, "cycle": cycle, "validation_only": validate, **fields}}
emit({{"event": "ready", "validation_only": validate, "runtime_initialized": not validate,
      "frame_expiry_supported": False, "display_deadline_ns": deadline}})
requests = 0
buffer = b""
open_input = True
pending = None
while now() < deadline:
    if pending is not None and now() >= pending[2]:
        kind, cycle, _, request_ns = pending
        if {confirm!r}:
            with open(state, "w") as handle:
                handle.write("0" if kind == "hide" else "1")
            emit(line("overlay_hidden" if kind == "hide" else "overlay_shown", cycle, observed_ns=now()))
        else:  # as the real bridge: a request without its event is reported missing once, after the bound
            emit(line("visibility_event_missing", cycle, waited_ns=now() - request_ns))
        pending = None
    if not open_input:
        time.sleep(0.01)
        continue
    ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.01)
    if not ready:
        continue
    chunk = os.read(0, 20 - len(buffer))
    if not chunk:
        open_input = False
        continue
    buffer += chunk
    if len(buffer) < 20:
        continue
    magic, opcode, size = HEADER.unpack(buffer[:12])
    if opcode != 5 or size != 8:
        emit({{"event": "error", "status": "error", "error": "marker_accepts_visibility_only", "exit_code": 1,
              "cleanup_ok": True}})
        sys.exit(1)
    cycle, visible = struct.unpack("<II", buffer[12:])
    buffer = b""
    requests += 1
    hide = visible == 0
    kind = "hide" if hide else "show"
    request_ns = now()
    emit(line(kind + "_requested", cycle, request_ns=request_ns))
    emit(line(kind + "_returned", cycle, returned_ns=now(), ok=True, call="HideOverlay" if hide else "ShowOverlay"))
    pending = (kind, cycle, now() + ({event_delay_ms} if {confirm!r} else 260) * 1000000, request_ns)
marker = {{"width": 320, "height": 128, "opaque_pixels": 40960, "corner_ids": [1, 2, 3, 4], "border_pixels": 4,
          "tag_size_pixels": 24, "tag_inset_pixels": 8, "corner_order": "top_left,top_right,bottom_left,bottom_right",
          "config_id": {HUD_CONFIG.layout_id!r}, "visible_in_dashboard": True, "panel_width_m": {HUD_CONFIG.panel_width_m},
          "distance_m": {HUD_CONFIG.distance_m}, "x_m": {HUD_CONFIG.x_m}, "y_m": {HUD_CONFIG.y_m}, "opacity": {HUD_CONFIG.opacity}}}
emit({{"event": "closed", "status": "validated_marker" if validate else "marker_complete", "exit_code": 0,
      "cleanup_ok": True, "mode": sys.argv[1], "runtime_initialized": not validate, "calibration_only": True,
      "native_started_ns": started, "marker_request_ns": started + 1, "marker_submit_returned_ns": started + 2,
      "display_deadline_ns": deadline, "cutoff_requested_ns": now(), "cleanup_returned_ns": now() + 1,
      "physical_display_recovery_verified": False, "input_frames_accepted": False, "capture_started": False,
      "inference_started": False, "images_saved": False, "marker": marker,
      "overlay_shown_event_seen": not validate, "overlay_hidden_event_seen": not validate,
      "visibility_requests_received": requests, "visibility_events_missing": 0 if {confirm!r} else requests,
      "visibility_after_submit": {{"scope": "runtime_queries_not_physical_visibility", "sampled": not validate,
                                  "api_visible": None if validate else True, "dashboard_visible": None if validate else False,
                                  "visible_in_dashboard": None if validate else True}}}})
""")
        path.chmod(0o755)
        return path

    def backend(self):
        state = self.state
        reads = []

        class Source:
            kind = "synthetic"

            def __init__(self, candidate):
                self.capabilities = {"driver": "synthetic"}
                self.format = dict(width=1000, height=600, fourcc="BGR3", stride_bytes=3000,
                                   minimum_buffer_bytes=1_800_000, buffer_bytes=1_800_000)

            def open(self):
                pass

            def read_frame(self, timeout):
                visible = state.read_text().strip() == "1"
                reads.append(visible)
                image = scene(visible)
                image[0, 0] = len(reads) % 100  # every read differs, as a moving scene would
                return image.tobytes()

            def close(self):
                reads.append("closed")
        Source.reads = reads
        return Source

    def test_confirmed_cycles_give_bounds_on_every_transition(self):
        client = MarkerClient(self.fake_bridge(), validation_only=False)
        source = self.backend()
        report = fallback_measure.measure({"node": "synthetic:fake"}, 4, 2, threading.Event(), backend_factory=source,
                                          marker_client=client, allow_calibration_output=True)
        self.assertEqual((report["status"], report["exit_code"], report["cycles_completed"]), ("measured", 0, 2), report)
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual(source.reads[-1], "closed")
        self.assertGreater(report["marker_first_present_observed_ns"], report["marker_ready_ns"] - 1)
        fallback_measure.validate_worker_report(report, 0)
        absence, visible = report["hide_to_absence"], report["show_to_visible"]
        self.assertEqual((absence["measured"], absence["cycles"], absence["transitions_observed"],
                          absence["transitions_not_observed_within_bound"]), (True, 2, 2, 0))
        self.assertEqual((visible["measured"], visible["transitions_observed"]), (True, 2))
        for entry in absence["per_cycle"]:
            self.assertEqual(entry["ended_by"], "transition_observed")
            self.assertLessEqual(entry["request_ns"], entry["returned_ns"])
            self.assertLess(entry["returned_ns"], entry["event_observed_ns"])
            self.assertLessEqual(entry["last_present_observed_ns"], entry["first_absent_observed_ns"])
            self.assertGreaterEqual(entry["first_absent_observed_ns"], entry["event_observed_ns"])
            self.assertLessEqual(entry["lower_bound_after_request_ns"], entry["upper_bound_after_request_ns"])
            self.assertLess(entry["upper_bound_after_request_ns"], fallback_measure.PHASE_BOUND_NS)
            self.assertGreaterEqual(entry["upper_bound_after_event_ns"], 0)
        for entry in visible["per_cycle"]:
            self.assertEqual(entry["ended_by"], "transition_observed")
            self.assertGreaterEqual(entry["first_present_observed_ns"], entry["event_observed_ns"])
            self.assertLessEqual(entry["last_absent_observed_ns"], entry["first_present_observed_ns"])
        hidden = report["hidden_event_delivery"]
        self.assertEqual((hidden["events_delivered"], hidden["events_missing"]), (2, 0))
        self.assertGreaterEqual(hidden["maximum_event_after_returned_ns"], 10_000_000)
        self.assertLess(hidden["maximum_event_after_returned_ns"], VISIBILITY_EVENT_BOUND_NS)
        self.assertEqual(visible["shown_event_delivery"]["events_delivered"], 2)
        repeats = report["loopback_repeats"]
        self.assertTrue(repeats["measured"])
        self.assertGreaterEqual(repeats["reads"], 20)
        self.assertEqual(repeats["identical_consecutive_digests"], 0)  # every fixture read differs
        self.assertEqual(repeats["distinct_digests"], repeats["reads"])
        self.assertGreater(repeats["reads_with_complete_copy"], 0)
        self.assertLess(repeats["reads_with_complete_copy"], repeats["reads"])
        self.assertEqual(len(repeats["reads_detail"]), repeats["reads"])
        # Digests and counts only: no per-read value is more than a 32-character digest.
        self.assertTrue(all(len(str(value)) <= 32 for entry in repeats["reads_detail"] for value in entry.values()))
        self.assertLess(len(json.dumps(report)), 128 * 1024)
        native = report["bridge"]["native_report"]
        self.assertEqual((native["visibility_requests_received"], native["mode"]), (4, "--calibration-marker"))
        self.assertEqual(report["capture"]["status"], "captured")

    def test_a_marker_that_never_reaches_the_mirror_rejects_the_run_without_cycling(self):
        self.state.write_text("0")
        client = MarkerClient(self.fake_bridge(), validation_only=False)
        with patch.object(fallback_measure, "MARKER_WARMUP_SECONDS", 0.5):
            report = fallback_measure.measure({"node": "synthetic:fake"}, 2, 2, threading.Event(),
                                              backend_factory=self.backend(), marker_client=client,
                                              allow_calibration_output=True)
        self.assertEqual((report["status"], report["exit_code"], report["reason"], report["cycles_completed"]),
                         ("rejected", 1, "marker_not_visible_in_mirror", 0))
        self.assertTrue(report["cleanup_ok"], report)
        self.assertEqual(report["bridge"]["native_report"]["visibility_requests_received"], 0)
        self.assertFalse(report["hide_to_absence"]["measured"])
        self.assertTrue(report["loopback_repeats"]["measured"])  # the reads taken are still counted

    def test_a_bridge_without_events_leaves_the_transitions_unobserved_within_the_bound(self):
        # The fake toggles nothing and confirms nothing: every hide ends missing and the mirror never changes.
        client = MarkerClient(self.fake_bridge(confirm=False), validation_only=False)
        with patch.object(fallback_measure, "PHASE_BOUND_NS", 400_000_000):
            report = fallback_measure.measure({"node": "synthetic:fake"}, 4, 1, threading.Event(),
                                              backend_factory=self.backend(), marker_client=client,
                                              allow_calibration_output=True)
        self.assertEqual((report["status"], report["cycles_completed"]), ("measured", 1), report)
        absence = report["hide_to_absence"]
        self.assertEqual((absence["transitions_observed"], absence["transitions_not_observed_within_bound"]), (0, 1))
        self.assertEqual(absence["per_cycle"][0]["ended_by"], "phase_bound")
        self.assertIsNone(absence["per_cycle"][0]["first_absent_observed_ns"])
        self.assertEqual(report["hidden_event_delivery"]["events_missing"], 1)

    def test_a_cancellation_ends_the_run_as_cancelled_with_the_bridge_closed(self):
        stop = threading.Event()
        client = MarkerClient(self.fake_bridge(), validation_only=False)
        source = self.backend()
        original = source.read_frame

        def read_then_stop(self_, timeout):
            if len(source.reads) >= 5:
                stop.set()
            return original(self_, timeout)
        source.read_frame = read_then_stop
        report = fallback_measure.measure({"node": "synthetic:fake"}, 4, 3, stop, backend_factory=source,
                                          marker_client=client, allow_calibration_output=True)
        self.assertEqual((report["status"], report["exit_code"], report["reason"]), ("cancelled", 130, "signal"))
        self.assertIn("bridge", report)
        self.assertEqual(source.reads[-1], "closed")

    def test_the_fixture_backend_rejects_a_format_change(self):
        client = MarkerClient(self.fake_bridge(), validation_only=False)
        source = self.backend()
        original = source.read_frame

        def read_changing(self_, timeout):
            data = original(self_, timeout)
            if len(source.reads) == 3:
                self_.format["colorspace_code"] = 1
            return data
        source.read_frame = read_changing
        report = fallback_measure.measure({"node": "synthetic:fake"}, 3, 1, threading.Event(), backend_factory=source,
                                          marker_client=client, allow_calibration_output=True)
        self.assertEqual((report["status"], report["exit_code"]), ("rejected", 1))
        self.assertEqual(report["reason"], "fallback_measure_format_changed")


class DocumentationTests(unittest.TestCase):
    def test_the_guide_states_the_procedure_and_the_approval_boundary(self):
        # The guide is the one canonical procedure; the reference and the index are documentation-route inputs
        # and are not read here (tests/test_workflows.py).
        guide = " ".join((ROOT / "docs/guides/fallback-measurement.md").read_text().split())
        for text in ("scripts/fallback_measure.py", "--validate", "--measure-fallback", "--allow-mirror-observation",
                     "--allow-calibration-output", "--device /dev/video99", "--manifest", "needs its own approval",
                     "rebuilt on the Frame first", "timebase_id", *(f"`{name}`" for name in fallback_measure.MEASUREMENTS)):
            with self.subTest(text=text):
                self.assertIn(text, guide)


if __name__ == "__main__":
    unittest.main()
