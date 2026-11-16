"""The camera HUD stage (work-order item L8) end to end: fake camera producer, real bridge binary, the report.

``CameraHudStageTests`` runs ``CameraHudStage`` on the camera stand-in
(``tests/fake_camera_export.py``, launched through the supervision primitive)
and the validation bridge, which takes observed frames under the fixed HUD's
rules and displays nothing. ``FixedHudStageTests`` runs the same stage on the
bridge's ``--fixed-hud`` mode against the fake OpenVR runtime
(``tests/fixtures/fake_vrclient.cpp``), with the one permission function
patched for the test: a fixture that logs the bridge's calls, not a device, a
display or SteamVR. No time here is a Frame measurement, and nothing shows
what a headset displays.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from sightcircuit import gates, observed_frames
from sightcircuit.frame import Detection, DetectionResult, Frame, Processed
from sightcircuit.hud.contract import HUD_CONFIG, MAX_DISPLAY_AGE_NS, HudConfig
from sightcircuit.observed_frames import ObservedFrameClient
from sightcircuit.overlay import BRIDGE_INPUT_POLL_NS, DEFAULT_BRIDGE
from sightcircuit.runtime.supervision import RECORD_AND_POLL
from sightcircuit.sources import camera_helper
from sightcircuit.sources.camera_helper import CameraHelperSource
from tests import fake_camera_export, fake_vrclient

NUMPY_CV2 = bool(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"))
RUNNABLE = sys.platform.startswith("linux") and NUMPY_CV2 and DEFAULT_BRIDGE.is_file()
MS = 1_000_000
MARGIN_NS = 150 * MS  # This host's scheduling of the bridge and the threads here; never a claim about the bridge.
PERIOD_S = 0.2  # One processing period at the 5 frames a second these runs use.
LEFT, RIGHT, LOW = (1, 2, 30, 40), (40, 20, 62, 30), (24, 40, 40, 47)  # Boxes in the stand-in's 64 x 48 frames.
BOTTLE = (("bottle", LEFT),)


class ScriptedDetector:
    """Returns the scripted detections call by call (the last entry repeats), never looking at a pixel.

    ``delays`` maps a 1-based call number to seconds the call takes; ``fail_on`` is a call that raises.
    """
    name, runtime, classes = "scripted", "none", ("bottle", "cup")

    def __init__(self, script=(BOTTLE,), delays=None, fail_on=None):
        self.script, self.delays, self.fail_on = list(script), dict(delays or {}), fail_on
        self.called_ns, self.returned_ns = [], []

    def detect(self, image, threshold):
        self.called_ns.append(time.monotonic_ns())
        call = len(self.called_ns)
        if call in self.delays:
            time.sleep(self.delays[call])
        if call == self.fail_on:
            raise RuntimeError("the scripted detector failed on purpose")
        items = self.script[min(call, len(self.script)) - 1]
        self.returned_ns.append(time.monotonic_ns())
        return DetectionResult(tuple(Detection(label, 0.9, box) for label, box in items), 1.0)


def walk(value, path="report"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key, item
            yield from walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield path, index, item
            yield from walk(item, f"{path}[{index}]")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class StageCase(unittest.TestCase):
    """One stage per test on the camera stand-in; records the frames seen, the frames queued and the packets written."""
    LIMITS = {"fps": 5, "max_seconds": 5, "max_frames": 8}
    TWO = {"fps": 5, "max_seconds": 3, "max_frames": 5}  # The ``end:`` scenarios: two frames 200 ms apart, then the end.

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = fake_camera_export.install(cls.directory.name)

    def setUp(self):
        for number in RECORD_AND_POLL.numbers:
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def client(self, **options):
        return ObservedFrameClient(**options)

    def stage(self, detector=None, scenario=None, limits=None, fake=(), client=None, **options):
        from sightcircuit.convert import to_bgr
        from sightcircuit.hud.camera_stage import CameraHudStage
        extra = (("--fake-scenario", scenario) if scenario else ()) + tuple(str(item) for item in fake)
        self.source = CameraHelperSource(self.binary, limits or self.LIMITS, confirmed=True, extra_args=extra)
        self.detector = detector or ScriptedDetector()
        self.frames, self.queued, self.packets, self.end_packet_ns = [], [], [], None

        def convert(frame):
            self.frames.append(frame)
            return to_bgr(frame)
        client = client or self.client()
        options.setdefault("max_fps", 10)
        stage = self.the_stage = CameraHudStage(self.source, self.detector, client, convert=convert, **options)
        queue, record, on_end = client.submit_observed, stage._on_packet, self.source._on_end

        def submit_observed(frame_id, observed_ns, expires_ns, width, height, rgba, *, token=None):
            self.queued.append({"frame_id": frame_id, "observed_ns": observed_ns, "expires_ns": expires_ns,
                                "size": (width, height), "rgba": rgba, "token": token, "at_ns": time.monotonic_ns()})
            return queue(frame_id, observed_ns, expires_ns, width, height, rgba, token=token)

        def listener(event, **fields):
            self.packets.append((event, fields))
            record(event, **fields)

        def end(header):
            self.end_packet_ns = time.monotonic_ns()
            on_end(header)
        client.submit_observed, client.listener, self.source._on_end = submit_observed, listener, end
        return stage

    def named(self, name):
        return [fields for event, fields in self.packets if event == name]

    def assert_deadlines_within_the_cap(self, run_deadline_ns=None):
        """Every frame handed to the client carries its camera frame's observation and a deadline within the cap.

        The deadline is the observation plus the maximum display age, or the
        run's own deadline (``seconds``) when that comes first.
        """
        observed = {frame.sequence: frame.observed_ns for frame in self.frames}
        self.assertEqual([item["frame_id"] for item in self.queued], sorted({item["frame_id"] for item in self.queued}))
        for item in self.queued:
            self.assertEqual(item["observed_ns"], observed[item["frame_id"]])  # Never the send time.
            self.assertLess(item["observed_ns"], item["at_ns"])
            self.assertLessEqual(item["expires_ns"] - item["observed_ns"], MAX_DISPLAY_AGE_NS)
            longest = item["observed_ns"] + MAX_DISPLAY_AGE_NS
            self.assertEqual(item["expires_ns"], longest if run_deadline_ns is None else min(longest, run_deadline_ns))
            self.assertEqual(item["size"], (HUD_CONFIG.width_px, HUD_CONFIG.height_px))
        for fields in self.named("frame_sent"):
            self.assertLess(fields["sent_ns"], fields["expires_ns"])

    def assert_both_children_closed(self, report, *, source_ok=True):
        """The helper is gone and reaped, the bridge exited through its cleanup, and the report says so."""
        supervision = report["source"]["export"]["supervision"]
        self.assertFalse(alive(supervision["worker_pid"]))
        self.assertTrue(supervision["worker_reaped"] and supervision["worker_tree_reaped"])
        client = self.the_stage.client
        self.assertIsNotNone(client.process.poll())
        bridge = report["bridge"]
        self.assertEqual((bridge["worker_reaped"], bridge["worker_exit_code"], bridge["transport_threads_stopped"],
                          bridge["forced_termination"], bridge["cleanup_confirmed"], bridge["settings_sent"]),
                         (True, 0, True, False, True, 0))
        self.assertEqual(bridge["final_report"]["settings_received"], 0)
        self.assertEqual(report["cleanup"], {"source_ok": source_ok, "bridge_ok": True, "sinks_ok": True,
                                             "ok": source_ok})
        self.assertFalse(report["pipeline"]["capture_thread_alive"])

    def assert_no_gate_opened(self, report):
        self.assertEqual({name: report[name] for name in gates.claims()},
                         {"production_live_input_enabled": False, "mapping_qualified": False,
                          "production_hud_enabled": False})
        self.assertEqual((report["images_saved"], report["pixels_in_report"], report["physical_display_observed"]),
                         (False, False, False))


@unittest.skipUnless(RUNNABLE, "Needs Linux, NumPy, OpenCV and scripts/build_overlay_bridge.sh")
class CameraHudStageTests(StageCase):
    def test_every_result_is_sent_with_its_frames_observation_and_a_deadline_within_the_cap(self):
        stage = self.stage()
        report = stage.run()
        self.assertEqual((report["status"], report["reason"], report["error_type"]), ("completed", "max_frames", None))
        self.assertEqual((report["report_kind"], report["schema_version"], report["validation_only"],
                          report["bridge_mode"]), ("camera_hud_stage", 1, True, "--validate-stdin"))
        # The same rows from eight successive observations are eight frames: nothing is held back as unchanged.
        self.assertEqual([frame.sequence for frame in self.frames], list(range(1, 9)))
        self.assertEqual([item["frame_id"] for item in self.queued], list(range(1, 9)))
        self.assertEqual({item["token"] for item in self.queued}, {("bottle",)})
        self.assert_deadlines_within_the_cap()
        self.assertEqual(report["results"], {"results": 8, "results_with_rows": 8, "results_without_rows": 0,
                                             "results_too_old": 0, "results_after_end": 0})
        frames = report["frames"]
        sent = len(self.named("frame_sent"))
        self.assertEqual((frames["submitted"], frames["sent"] + frames["not_sent"], frames["sent"],
                          frames["written_past_deadline"]), (8, 8, sent, 0))
        self.assertEqual((frames["bridge_received"], frames["bridge_presented"], frames["retransmitted"],
                          frames["refused"], frames["expired"], frames["expired_in_upload"]), (sent, sent, 0, 0, 0, 0))
        self.assertEqual(report["identities"], {"frame_identity": "camera_frame_sequence", "first_frame_id": 1,
                                                "last_frame_id": 8, "last_result_index": 8,
                                                "source_id": "arcturus_camera_left"})
        self.assertEqual(report["deadlines"]["within_cap"], True)
        self.assertEqual(report["deadlines"]["max_deadline_after_observation_ms"], 1000.0)
        # The tile was taken down once, at the end, and the bridge closed; nothing expired on the way.
        self.assertEqual((report["bridge"]["take_downs"], report["bridge"]["shows"], report["frame_expired"]["count"]),
                         (1, 0, 0))
        self.assert_both_children_closed(report)
        self.assert_no_gate_opened(report)
        json.dumps(report)

    def test_the_report_declares_each_timing_and_says_what_stands_in_for_the_bridges_acknowledgement(self):
        report = self.stage().run()
        timings = report["timings"]
        self.assertEqual(timings.pop("percentile_method"), "nearest_rank")
        self.assertEqual(set(timings), {"input_age_ms", "inference_ms", "processing_ms", "result_age_ms", "output_age_ms",
                                        "capture_to_display_age_ms"})
        for name, block in timings.items():
            with self.subTest(timing=name):
                self.assertLessEqual({"count", "samples", "p50", "p95", "max", "unit", "timebase", "from", "to"}, set(block))
                self.assertEqual(block["unit"], "ms")
                if block["count"]:
                    self.assertLessEqual(block["p50"], block["p95"])
                    self.assertLessEqual(block["p95"], block["max"])
        self.assertEqual([timings[name]["count"] for name in ("input_age_ms", "inference_ms", "processing_ms",
                                                              "result_age_ms")], [8, 8, 8, 8])
        output = timings["output_age_ms"]
        self.assertEqual((output["count"], output["timebase"], output["from"], output["to"], output["bridge_acknowledged"],
                          output["why"]),
                         (report["frames"]["sent"], "host_clock_monotonic", "camera_frame_observed",
                          "packet_written_to_bridge_pipe", False, "bridge_reports_no_per_frame_acknowledgement"))
        self.assertGreaterEqual(output["p50"], timings["input_age_ms"]["p50"])
        self.assertLess(output["max"], 1000)
        # The bridge acknowledges its first frame only: one sample, and the rest declared unacknowledged.
        display = timings["capture_to_display_age_ms"]
        self.assertEqual((display["count"], display["measured"], display["to"], display["frames_without_acknowledgement"],
                          display["lower_bound_for_other_frames"], display["physical_display_observed"]),
                         (1, "first_frame_only", "bridge_first_frame_line_read", report["frames"]["sent"] - 1,
                          "output_age_ms", False))
        self.assertEqual(report["bridge"]["first_frame"]["event"], "frame_validated")
        first_sent = self.named("frame_sent")[0]
        self.assertAlmostEqual(display["max"], (report["bridge"]["first_frame"]["read_ns"] - first_sent["observed_ns"]) / 1e6,
                               places=2)
        self.assertGreaterEqual(report["bridge"]["first_frame"]["read_ns"], first_sent["sent_ns"] - MARGIN_NS)
        # The source's report as it carries it: ready and end records, the exposure offset as a timing.
        source = report["source"]
        self.assertEqual((source["report_kind"], source["status"], source["end_reason"], source["presence_ended"],
                          source["retried"]), ("camera_helper_source", "completed", "max_frames", False, False))
        self.assertEqual(source["export"]["stream"]["ready"]["cameras"], [0])
        self.assertEqual(source["export"]["stream"]["end"]["reason"], "max_frames")
        self.assertEqual(source["timings"]["exposure_minus_acquire_ms"]["count"], 8)
        self.assertNotIn("definition", source["timings"]["exposure_minus_acquire_ms"])
        self.assertEqual((report["bridge"]["final_report"]["mode"], report["bridge"]["final_report"]["cleanup_ok"],
                          report["bridge"]["final_report"]["exit_code"]), ("--validate-stdin", True, 0))
        self.assertEqual(report["config"]["hud_config_id"], "fixed-edge-hud-v1")

    def test_empty_rows_take_the_tile_down_within_a_period_and_the_next_rows_show_it_again(self):
        detector = ScriptedDetector([BOTTLE, BOTTLE, (), (), BOTTLE])
        report = self.stage(detector).run()
        self.assertEqual(report["status"], "completed")
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2, 5, 6, 7, 8])  # No frame for an empty result.
        self.assertEqual(report["results"]["results_without_rows"], 2)
        order = [event for event, _ in self.packets if event != "frame_not_sent"]
        self.assertEqual(order[:order.index("shown") + 1],
                         ["frame_sent", "frame_sent", "taken_down", "frame_sent", "shown"])
        down, shown = self.named("taken_down")[0], self.named("shown")[0]
        # Taken down by a hide request right after the first empty result, not left to its deadline a second later.
        self.assertGreaterEqual(down["sent_ns"], detector.returned_ns[2])
        self.assertLess(down["sent_ns"] - detector.returned_ns[2], PERIOD_S * 1e9)
        self.assertLess(down["sent_ns"], self.named("frame_sent")[1]["expires_ns"] - 500 * MS)
        again = self.named("frame_sent")[2]
        self.assertEqual((again["frame_id"], again["hidden"]), (5, True))
        self.assertLess(shown["sent_ns"] - detector.returned_ns[4], PERIOD_S * 1e9)
        kept = self.the_stage.client.visibility_lines
        lines = [line["event"] for cycle in sorted(kept) for line in kept[cycle]]
        self.assertEqual([name for name in lines if name in ("hide_requested", "show_requested")],
                         ["hide_requested", "show_requested", "hide_requested"])  # The last one is the end of the run.
        self.assertEqual((report["bridge"]["take_downs"], report["bridge"]["shows"]), (2, 1))
        # The label's two spells and the gap between them, from this side's write times.
        row, = report["labels"]["rows"]
        self.assertEqual((row["label"], row["spells"]), ("bottle", 2))
        self.assertAlmostEqual(row["longest_gap_seconds"], (shown["sent_ns"] - down["sent_ns"]) / 1e9, places=3)
        self.assertGreater(row["longest_gap_seconds"], 0.3)
        self.assertLess(row["longest_gap_seconds"], 0.3 + 2 * PERIOD_S)
        self.assertGreater(row["display_seconds"], 0.6)
        self.assertEqual(report["labels"]["physical_display_observed"], False)
        # The hidden tile still reached its own deadline in the bridge, which is an expiry like any other.
        self.assertLessEqual(report["frame_expired"]["count"], 1)
        self.assert_deadlines_within_the_cap()
        self.assert_both_children_closed(report)

    def test_a_slow_detector_lets_the_tile_expire_by_itself_and_its_late_result_is_never_sent(self):
        detector = ScriptedDetector(delays={2: 1.5})
        report = self.stage(detector).run()
        self.assertEqual(report["status"], "completed")
        # Frame 2 came out of the detector more than a second after its observation: it reached no sink.
        self.assertNotIn(2, [item["frame_id"] for item in self.queued])
        self.assertEqual(self.queued[0]["frame_id"], 1)
        self.assertGreaterEqual(report["pipeline"]["stale_results_skipped"], 1)
        expiry = report["frame_expired"]
        event = expiry["events"][0]
        self.assertEqual((event["frame_id"], event["frame_observed_ns"], event["deadline_ns"], event["validation_only"]),
                         (1, self.queued[0]["observed_ns"], self.queued[0]["expires_ns"], True))
        # The bridge noticed the deadline within one poll, with nothing arriving and before any hide from here.
        self.assertGreaterEqual(event["noticed_after_deadline_ms"], 0)
        self.assertLess(event["noticed_after_deadline_ms"], (BRIDGE_INPUT_POLL_NS + MARGIN_NS) / 1e6)
        self.assertLess(event["observed_ns"], detector.returned_ns[1])
        self.assertTrue(all(fields["sent_ns"] > event["observed_ns"] for fields in self.named("taken_down")))
        self.assertEqual(expiry["hide_times_reported"], False)
        self.assertLess(expiry["max_noticed_after_deadline_ms"], (BRIDGE_INPUT_POLL_NS + MARGIN_NS) / 1e6)
        self.assertEqual((expiry["count"], expiry["dropped"]), (report["frames"]["expired"], 0))
        # Nothing was up between the expiry and the next frame, and the label's gap says so.
        row, = report["labels"]["rows"]
        self.assertGreater(row["longest_gap_seconds"], 0.3)
        self.assert_deadlines_within_the_cap()
        self.assert_both_children_closed(report)

    def test_a_result_older_than_the_maximum_display_age_is_never_handed_to_the_client(self):
        import numpy as np
        stage = self.stage()
        result = ScriptedDetector().detect(None, 0.5)
        tile = stage._tile(np.zeros((48, 64, 3), np.uint8), result)
        old = time.monotonic_ns() - MAX_DISPLAY_AGE_NS - MS
        frame = Frame(b"\0" * 12, "BGR3", 2, 2, 6, "arcturus_camera_left", 1, old)
        stage.submit(Processed(frame, result, tile, 1, 1000.0, 1.0, expires_ns=None))
        # The pipeline's own, later deadline never extends the frame's.
        stage._tile(np.zeros((48, 64, 3), np.uint8), result)
        stage.submit(Processed(frame, result, tile, 2, 1000.0, 1.0, expires_ns=time.monotonic_ns() + MAX_DISPLAY_AGE_NS))
        self.assertEqual((stage.counts["results"], stage.counts["results_too_old"], self.queued), (2, 2, []))
        # A tile drawn for another result is refused rather than paired with this frame.
        stage._tile(np.zeros((48, 64, 3), np.uint8), ScriptedDetector().detect(None, 0.5))
        with self.assertRaisesRegex(ValueError, "camera_hud_result_identity_mismatch"):
            stage.submit(Processed(frame, result, tile, 3, 1.0, 1.0))
        self.assertIsNone(stage.client.process)

    def test_a_presence_end_takes_the_tile_down_at_once_and_ends_the_run_presence_ended(self):
        for reason in ("headset_removed", "standby"):
            with self.subTest(reason=reason):
                # The detector is slow on the second frame: if that frame was taken, its call is in flight when
                # the end packet arrives, and the tile must come down without waiting for it.
                detector = ScriptedDetector(delays={2: 0.8})
                started = time.monotonic()
                report = self.stage(detector, scenario=f"end:{reason}", limits=self.TWO).run()
                self.assertEqual((report["status"], report["reason"]), ("presence_ended", reason))
                self.assertEqual((report["source"]["status"], report["source"]["presence_ended"],
                                  report["source"]["retried"], report["source"]["end_reason"]),
                                 ("presence_ended", True, False, reason))
                self.assertEqual([item["frame_id"] for item in self.queued], [1])
                self.assertEqual([event for event, _ in self.packets], ["frame_sent", "taken_down"])
                down, = self.named("taken_down")
                self.assertGreaterEqual(down["sent_ns"], self.end_packet_ns - MS)
                self.assertLess(down["sent_ns"] - self.end_packet_ns, MARGIN_NS)
                self.assertLess(abs(report["deadlines"]["ended_ns"] - self.end_packet_ns), MARGIN_NS)
                if len(detector.returned_ns) > 1:  # The call was in flight: the hide did not wait for it.
                    self.assertLess(down["sent_ns"], detector.returned_ns[1] - 400 * MS)
                self.assertLess(down["sent_ns"], self.queued[0]["expires_ns"] - 500 * MS)  # Not left to expire.
                self.assertEqual(report["results"]["results"], 1)  # The second frame's result reached no sink.
                row, = report["labels"]["rows"]
                self.assertEqual(row["last_end_ns"], down["sent_ns"])
                self.assert_both_children_closed(report)
                self.assert_no_gate_opened(report)
                self.assertLess(time.monotonic() - started, 6)

    def test_a_stop_takes_the_tile_down_without_waiting_for_the_detector_and_closes_both_children(self):
        detector = ScriptedDetector(delays={3: 1.2})
        stop = threading.Event()
        stage = self.stage(detector, limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        set_ns = []

        def request():
            set_ns.append(time.monotonic_ns())
            stop.set()
        timer = threading.Timer(0.7, request)  # The third detector call started at about 0.4 s and is still running.
        timer.start()
        self.addCleanup(timer.cancel)
        report = stage.run(stop)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "stop_requested"))
        self.assertEqual(report["source"]["status"], "stopped")
        down, = self.named("taken_down")
        self.assertGreaterEqual(down["sent_ns"], set_ns[0])
        self.assertLess(down["sent_ns"] - set_ns[0], MARGIN_NS)
        self.assertLess(down["sent_ns"], detector.returned_ns[2] - 500 * MS)  # The detector was still in its call.
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2])
        self.assert_deadlines_within_the_cap()
        self.assert_both_children_closed(report)

    def test_the_pipelines_own_bounds_end_the_run_completed(self):
        report = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, max_frames=3).run()
        self.assertEqual((report["status"], report["reason"], report["results"]["results"]), ("completed", "max_frames", 3))
        self.assertEqual(len(self.named("taken_down")), 1)
        self.assert_both_children_closed(report)
        report = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, seconds=0.7).run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "seconds"))
        self.assertEqual(len(self.named("taken_down")), 1)
        deadline_ns = int(self.the_stage.pipeline.deadline * 1e9)
        self.assert_deadlines_within_the_cap(deadline_ns)
        self.assertLess(max(item["expires_ns"] for item in self.queued), max(item["observed_ns"] for item in self.queued)
                        + MAX_DISPLAY_AGE_NS)  # The run's end cut the last deadlines short; none outlives the run.
        self.assert_both_children_closed(report)

    def test_a_consumer_error_takes_the_tile_down_and_closes_both_children(self):
        stage = self.stage(ScriptedDetector(fail_on=3), limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        report = stage.run()
        self.assertEqual((report["status"], report["reason"], report["error_type"]),
                         ("error", "camera_hud_run_failed", "RuntimeError"))
        self.assertIsInstance(stage.failure, RuntimeError)
        self.assertNotIn("on purpose", json.dumps(report))  # The message stays on the stage, not in the report.
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2])
        self.assertEqual(len(self.named("taken_down")), 1)
        self.assertLess(self.named("taken_down")[0]["sent_ns"] - self.detector.called_ns[2], MARGIN_NS)
        self.assertEqual(report["source"]["status"], "stopped")
        self.assert_both_children_closed(report)
        self.assert_no_gate_opened(report)

    def test_a_helper_error_takes_the_tile_down_and_closes_both_children(self):
        for scenario, reason, cleanup_ok in (("end:sdk_error", "sdk_error", True), ("invalid:bad_magic", "bad_magic", True),
                                             ("end:watchdog", "watchdog", True)):
            with self.subTest(scenario=scenario):
                stage = self.stage(scenario=scenario, limits=self.TWO)
                report = stage.run()
                self.assertEqual((report["status"], report["reason"], report["error_type"]),
                                 ("error", reason, "SightCircuitError"))
                self.assertEqual((report["source"]["status"], report["source"]["reason"]), ("error", reason))
                self.assertLessEqual(len(self.named("taken_down")), 1)
                if self.named("frame_sent"):
                    self.assertEqual(len(self.named("taken_down")), 1)
                self.assertFalse(self.the_stage.client.hidden and self.named("shown"))
                self.assert_deadlines_within_the_cap()
                self.assert_both_children_closed(report, source_ok=cleanup_ok)

    def test_a_bridge_that_dies_ends_the_run_as_an_error_and_the_helper_is_stopped(self):
        stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        killer = threading.Timer(0.5, lambda: stage.client.process.kill())
        killer.start()
        self.addCleanup(killer.cancel)
        started = time.monotonic()
        report = stage.run()
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual((report["status"], report["reason"]), ("error", "overlay_bridge_failed"))
        self.assertEqual((report["cleanup"]["bridge_ok"], report["cleanup"]["ok"], report["cleanup"]["source_ok"]),
                         (False, False, True))
        self.assertEqual(report["bridge"]["error"], "overlay_bridge_failed")
        supervision = report["source"]["export"]["supervision"]
        self.assertFalse(alive(supervision["worker_pid"]))
        self.assertTrue(supervision["worker_tree_reaped"])

    def test_names_only_rows_and_each_orientation_render(self):
        import numpy as np
        from sightcircuit.hud.camera import ORIENTATIONS, camera_rows
        from sightcircuit.hud.render import draw_tile
        from sightcircuit.overlay import rgba_from_bgr
        result = DetectionResult((Detection("bottle", 0.9, LEFT), Detection("cup", 0.8, LOW)), 1.0)
        image = np.zeros((48, 64, 3), np.uint8)
        tiles = {}
        for orientation in ORIENTATIONS:
            stage = self.stage(orientation=orientation)
            tile = stage._tile(image, result)
            rows = camera_rows(list(result.detections), image.shape, HUD_CONFIG, threshold=0.5, orientation=orientation)
            self.assertEqual([row.direction for row in rows], [ORIENTATIONS[orientation](-1, 0), ORIENTATIONS[orientation](0, 1)])
            self.assertTrue(np.array_equal(tile, draw_tile(rows, HUD_CONFIG)))
            self.assertEqual(tile.shape, (HUD_CONFIG.height_px, HUD_CONFIG.width_px, 3))
            tiles[orientation] = tile.tobytes()
        self.assertEqual(len(set(tiles.values())), len(ORIENTATIONS))  # Each orientation draws its own arrows.
        names = self.stage(directions=False, orientation="rotate_180")
        tile = names._tile(image, result)
        rows = camera_rows(list(result.detections), image.shape, HUD_CONFIG, threshold=0.5, directions=False)
        self.assertEqual([(row.label, row.direction) for row in rows], [("bottle", None), ("cup", None)])
        self.assertTrue(np.array_equal(tile, draw_tile(rows, HUD_CONFIG)))
        self.assertNotIn(tile.tobytes(), tiles.values())
        # End to end: the bytes the bridge is given are that tile's, for names only and for a turned image.
        for options, expected in (({"directions": False}, rgba_from_bgr(tile)[2]),
                                  ({"orientation": "rotate_90_cw"},
                                   rgba_from_bgr(np.frombuffer(tiles["rotate_90_cw"], np.uint8).reshape(tile.shape))[2])):
            with self.subTest(options=options):
                detector = ScriptedDetector([(("bottle", LEFT), ("cup", LOW))])
                report = self.stage(detector, limits={"fps": 10, "max_seconds": 3, "max_frames": 3}, **options).run()
                self.assertEqual(report["status"], "completed")
                self.assertEqual((report["config"]["directions"], report["config"]["orientation"]),
                                 (options.get("directions", True), options.get("orientation", "identity")))
                self.assertEqual({item["rgba"] for item in self.queued}, {expected})
                self.assertEqual({item["token"] for item in self.queued}, {("bottle", "cup")})
                self.assertEqual([row["label"] for row in report["labels"]["rows"]], ["bottle", "cup"])
                self.assertEqual(report["frames"]["bridge_presented"], report["frames"]["sent"])

    def test_the_real_output_start_is_refused_by_the_gate_before_anything_is_launched(self):
        import subprocess
        client = ObservedFrameClient(validation_only=False)
        stage = self.stage(client=client)
        with mock.patch.object(camera_helper, "run_export", wraps=camera_helper.run_export) as exported, \
                mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as launched, \
                mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            with self.assertRaises(ValueError) as caught:
                stage.run()
        self.assertEqual(str(caught.exception), "fixed_hud_output_not_approved")
        self.assertEqual([(call.args[0], call.args[1]) for call in required.call_args_list], [(gates.LIVE_HUD_OUTPUT, False)])
        exported.assert_not_called()
        launched.assert_not_called()
        self.assertEqual((client.process, self.frames, self.queued, self.source.status), (None, [], [], "not_started"))
        self.assertEqual((stage.status, stage.pipeline), ("not_run", None))
        report = stage.report()  # A stage that never ran still reports, and claims nothing.
        self.assertEqual((report["status"], report["bridge"], report["pipeline"], report["cleanup"]["ok"],
                          report["frames"]["sent"], report["validation_only"], report["bridge_mode"]),
                         ("not_run", None, None, False, 0, False, "--fixed-hud"))
        self.assert_no_gate_opened(report)
        with self.assertRaisesRegex(ValueError, "camera_hud_stage_already_run"):
            stage.run()
        self.assertEqual(gates.claims(), {"production_live_input_enabled": False, "mapping_qualified": False,
                                          "production_hud_enabled": False})

    def test_the_stage_takes_only_the_camera_source_the_observed_frame_client_and_the_fixed_tile(self):
        from sightcircuit.hud.camera_stage import CameraHudStage
        from sightcircuit.overlay import OverlayClient
        source = CameraHelperSource(self.binary, self.LIMITS, confirmed=True)
        detector = ScriptedDetector()
        for arguments, options, reason in (
                ((object(), detector, ObservedFrameClient()), {}, "camera_hud_requires_camera_helper_source"),
                ((source, detector, OverlayClient(validation_only=True)), {}, "camera_hud_requires_observed_frame_client"),
                ((source, detector, ObservedFrameClient()), {"config": HudConfig(rows=2)}, "camera_hud_config_not_fixed"),
                ((source, detector, ObservedFrameClient()), {"orientation": "upright"}, "hud_camera_orientation_invalid"),
                ((source, detector, ObservedFrameClient()), {"directions": 1}, "hud_camera_directions_invalid")):
            with self.assertRaisesRegex(ValueError, reason):
                CameraHudStage(*arguments, **options)
        stage = self.stage()
        self.assertEqual(stage.run()["status"], "completed")
        with self.assertRaisesRegex(ValueError, "camera_hud_stage_already_run"):
            stage.run()

    def test_no_field_of_the_report_can_hold_image_bytes_or_free_text(self):
        import numpy as np
        from sightcircuit.hud import camera_stage
        report = self.stage(scenario="hello_noise").run()
        self.assertIs(camera_stage.check_report(report), report)
        text = json.dumps(report)
        self.assertLess(len(text), 16384)
        strings = 0
        for path, key, value in walk(report):
            with self.subTest(path=path, key=key):
                self.assertIn(type(value), (dict, list, str, int, float, bool, type(None)))
                self.assertRegex(str(key), r"\A[A-Za-z0-9_.:/+@-]{1,128}\Z")
                if type(value) is list and value and type(value[0]) is not dict:
                    self.assertLessEqual(len(value), camera_stage.MAX_SCALAR_LIST)
                if type(value) is str:
                    strings += 1
                    self.assertNotRegex(value, r"\s") if key != "label" else self.assertEqual(value, "bottle")
                    self.assertLessEqual(len(value), 128)
        self.assertGreater(strings, 20)
        for word in ("calib", "intrinsic", "offline_cal", "distortion", "extrinsic", "c" * 64, "f" * 64, str(self.binary)):
            self.assertNotIn(word, text.lower())
        for item in self.queued:  # Neither a tile nor a camera frame, in any spelling.
            self.assertNotIn(item["rgba"][:24].hex(), text)
        self.assertNotIn(bytes(self.frames[0].pixels[:24]).hex(), text)
        # What the embedded reports held as prose or paths was left out by name; nothing else had to be withheld.
        self.assertEqual(report["fields_withheld"], 0)
        self.assertNotIn("error", report["source"])
        self.assertEqual(set(report["source"]["export"]["helper"]), {"sha256", "parent_confirmed"})
        # The schema itself: what it refuses, wherever it sits.
        tile = bytes(HUD_CONFIG.width_px * HUD_CONFIG.height_px * 4)
        refused = {"bytes": tile, "bytearray": bytearray(tile), "memoryview": memoryview(tile),
                   "array": np.zeros((2, 2), np.uint8), "tuple": (1, 2), "set": {1}, "long_list": list(range(17)),
                   "tile_as_list": list(tile), "long_string": "a" * 129, "prose": "two words", "newline": "a\nb",
                   "hex_tile": tile.hex(), "nan": float("nan"), "mixed": [{"a": 1}, 2], "object": object(),
                   "records": [{"n": index} for index in range(camera_stage.MAX_RECORD_LIST + 1)],
                   "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}, "label": "bad/label",
                   "nested_bytes": {"inner": [{"pixels": tile}]}, "bad key": 1}
        for name, value in refused.items():
            with self.subTest(refused=name):
                candidate = {name: value} if name != "bad key" else {"bad key": 1}
                with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid:report"):
                    camera_stage.check_report(candidate)
                with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid"):
                    camera_stage.check_report({**report, "extra": candidate})
        accepted = {"label": "cell phone", "box_xyxy": [1.0, 2.0, 3.5, 4.0], "code": "camera-export/2", "none": None,
                    "records": [{"label": "cup", "n": 1}], "flag": True}
        self.assertIs(camera_stage.check_report(accepted), accepted)
        # ``report_value`` reduces an embedded report to the schema and counts what it had to withhold.
        withheld = [0]
        kept = camera_stage.report_value({"reason": "sdk_error", "error": "free text here", "note": "more free text",
                                          "pixels": tile, "path": "/tmp/helper", "list": list(range(40)), "ok": True,
                                          "nested": {"detail": "prose", "count": 3}}, withheld)
        self.assertEqual((kept, withheld[0]), ({"reason": "sdk_error", "note": None, "pixels": None, "list": None,
                                                "ok": True, "nested": {"count": 3}}, 3))
        camera_stage.check_report(kept)

    def test_the_display_ledger_counts_spells_and_gaps_from_write_times_and_deadlines(self):
        from sightcircuit.hud.camera_stage import DisplayLedger
        second = 1_000_000_000
        ledger = DisplayLedger()
        ledger.shown(("bottle",), 0, second)                 # bottle 0 to 0.2 s (replaced)
        ledger.shown(("bottle", "cup"), second // 5, second * 6 // 5)   # both to their deadline at 1.2 s
        ledger.shown(("cup",), 3 * second, 4 * second)       # cup again from 3 s, taken down at 3.5 s
        ledger.down(second * 7 // 2)
        ledger.shown(("bottle",), 5 * second, 5 * second)    # already at its deadline: never up
        ledger.down(9 * second)
        rows = {row["label"]: row for row in ledger.report()}
        self.assertEqual((rows["bottle"]["display_seconds"], rows["bottle"]["spells"], rows["bottle"]["longest_gap_seconds"]),
                         (1.2, 1, 0.0))
        self.assertEqual((rows["cup"]["display_seconds"], rows["cup"]["spells"], rows["cup"]["longest_gap_seconds"]),
                         (1.5, 2, 1.8))
        self.assertEqual((rows["cup"]["first_shown_ns"], rows["cup"]["last_end_ns"]), (second // 5, second * 7 // 2))


@unittest.skipUnless(RUNNABLE and fake_vrclient.available(),
                     "Needs Linux, NumPy, OpenCV, scripts/build_overlay_bridge.sh, its SDK checkout and g++")
class FixedHudStageTests(StageCase):
    """The stage on the bridge's ``--fixed-hud`` mode against the fake runtime: what the bridge calls, and when.

    The permission function is patched to permit for these tests alone, which
    is how the fixture is reached; without the patch the same client is
    refused (``CameraHudStageTests``). Nothing here is SteamVR or a display.
    """
    runs = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.runtime_env, _ = fake_vrclient.runtime(Path(cls.directory.name) / "vr")

    def setUp(self):
        super().setUp()
        permit = mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=True)
        self.permitted = permit.start()
        self.addCleanup(permit.stop)

    def client(self, **fake):
        FixedHudStageTests.runs += 1
        self.log = Path(self.directory.name) / f"calls-{FixedHudStageTests.runs}.jsonl"
        return ObservedFrameClient(validation_only=False,
                                   env={**os.environ, **self.runtime_env, "FAKE_VR_LOG": str(self.log), **fake})

    def calls(self, *names):
        return [call for call in fake_vrclient.calls(self.log) if not names or call["call"] in names]

    def assert_retired(self, report):
        final = report["bridge"]["final_report"]
        self.assertEqual((final["mode"], final["exit_code"], final["cleanup_ok"], final["hide_returned_ok"],
                          final["clear_texture_returned_ok"], final["destroy_returned_ok"], final["settings_received"]),
                         ("--fixed-hud", 0, True, True, True, True, 0))
        drawing = self.calls("SetOverlayRaw", "ShowOverlay", "HideOverlay", "ClearOverlayTexture", "DestroyOverlay")
        self.assertEqual([call["call"] for call in drawing][-3:], ["HideOverlay", "ClearOverlayTexture", "DestroyOverlay"])
        self.assertIs(drawing[-1]["visible"], False)
        # The placement was applied once, at connect: no settings packet ever moved, resized or faded the tile.
        for name in ("SetOverlayWidthInMeters", "SetOverlayAlpha", "SetOverlayTransformTrackedDeviceRelative"):
            self.assertEqual(len(self.calls(name)), 1, name)
        self.assertEqual((report["validation_only"], report["bridge_mode"], report["bridge"]["mode"]),
                         (False, "--fixed-hud", "--fixed-hud"))
        self.assertEqual(final["fixed_hud"]["config_id"], "fixed-edge-hud-v1")
        self.assertEqual(final["fixed_hud"]["settings_accepted"], False)
        self.assert_no_gate_opened(report)  # Running on the fixture claims nothing.

    def assert_nothing_shown_past_its_deadline(self, report):
        """From the fake's log: whenever the overlay was visible, the tile uploaded last was within its deadline.

        The bridge presents the frames it took in order, so the n-th upload is
        the n-th frame written here. A visible spell ends at the first later
        call that leaves the overlay hidden; it may outlast the deadline by
        the bridge's poll and this host's scheduling, no more.
        """
        sent = self.named("frame_sent")
        self.assertEqual(report["frames"]["refused"], 0)
        uploads, deadline, visible_since = 0, None, None
        for call in self.calls():
            if call["call"] == "SetOverlayRaw":
                if visible_since is not None:
                    self.assertLess(call["start_ns"], deadline + BRIDGE_INPUT_POLL_NS + MARGIN_NS)
                deadline = sent[uploads]["expires_ns"]
                uploads += 1
                if visible_since is not None:
                    visible_since = call["end_ns"]
            if call.get("visible") and visible_since is None:
                visible_since = call["end_ns"]
                self.assertLess(visible_since, deadline, call)  # Never shown at or after its deadline.
            elif not call.get("visible") and visible_since is not None:
                self.assertLess(call["end_ns"], deadline + BRIDGE_INPUT_POLL_NS + MARGIN_NS, call)
                visible_since = None
        self.assertIsNone(visible_since)
        self.assertEqual(uploads, len(sent))
        for event in report["frame_expired"]["events"]:
            self.assertEqual((event["outcome"], event["hide_ok"], event["api_visible_after_hide"],
                              event["texture_cleared"]), ("hidden", True, False, True))
            self.assertLess(event["hide_returned_after_deadline_ms"], (BRIDGE_INPUT_POLL_NS + MARGIN_NS) / 1e6)

    def test_rows_are_shown_empty_rows_hide_the_tile_and_the_next_rows_show_it_again(self):
        detector = ScriptedDetector([BOTTLE, BOTTLE, (), (), BOTTLE])
        report = self.stage(detector).run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "max_frames"))
        self.permitted.assert_called_with(self.the_stage.client)
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2, 5, 6, 7, 8])
        sent = self.named("frame_sent")
        self.assertEqual(len(self.calls("SetOverlayRaw")), len(sent))
        self.assertEqual((report["frames"]["bridge_presented"], report["frames"]["sent"]), (len(sent), len(sent)))
        self.assertEqual(report["bridge"]["first_frame"]["event"], "frame_submitted")
        down, shown = self.named("taken_down")[0], self.named("shown")[0]
        self.assertLess(down["sent_ns"] - detector.returned_ns[2], PERIOD_S * 1e9)
        # The runtime was told to hide right after the hide request was written, and to show after the show request.
        hide = next(call for call in self.calls("HideOverlay") if call["start_ns"] >= down["sent_ns"] - MS)
        self.assertLess(hide["start_ns"] - down["sent_ns"], MARGIN_NS)
        self.assertIs(hide["visible"], False)
        self.assertLess(hide["end_ns"], sent[1]["expires_ns"] - 400 * MS)  # Well before its deadline would have.
        # The next rows were uploaded while the tile was down, and only the show request's call brought it up.
        after = [call for call in self.calls("SetOverlayRaw", "ShowOverlay") if call["start_ns"] > hide["end_ns"]]
        self.assertEqual([call["call"] for call in after[:2]], ["SetOverlayRaw", "ShowOverlay"])
        upload, show = after[:2]
        self.assertIs(upload["visible"], False)
        self.assertGreater(upload["start_ns"] - hide["end_ns"], 250 * MS)  # Two empty results at 5 a second.
        self.assertLess(show["start_ns"] - shown["sent_ns"], MARGIN_NS)
        self.assertIs(show["visible"], True)
        self.assertLess(show["start_ns"] - detector.returned_ns[4], PERIOD_S * 1e9)
        self.assert_deadlines_within_the_cap()
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)

    def test_a_slow_detector_leaves_the_bridge_to_hide_the_tile_at_its_deadline(self):
        detector = ScriptedDetector(delays={2: 1.5})
        report = self.stage(detector).run()
        self.assertEqual(report["status"], "completed")
        event = report["frame_expired"]["events"][0]
        self.assertEqual((event["frame_id"], event["deadline_ns"], event["outcome"], event["hide_ok"],
                          event["api_visible_after_hide"], event["texture_cleared"]),
                         (1, self.queued[0]["expires_ns"], "hidden", True, False, True))
        self.assertGreaterEqual(event["hide_called_ns"], event["deadline_ns"])
        self.assertLess(event["hide_returned_after_deadline_ms"], (BRIDGE_INPUT_POLL_NS + MARGIN_NS) / 1e6)
        self.assertEqual(report["frame_expired"]["hide_times_reported"], True)
        # By itself: the detector was still in its call and no hide request had been written.
        self.assertLess(event["hide_returned_ns"], detector.returned_ns[1])
        self.assertTrue(all(fields["sent_ns"] > event["hide_returned_ns"] for fields in self.named("taken_down")))
        # Nothing was uploaded or shown between that hide and the next frame written.
        following = min(fields["sent_ns"] for fields in self.named("frame_sent") if fields["frame_id"] > 1)
        self.assertEqual([call["call"] for call in self.calls("SetOverlayRaw", "ShowOverlay")
                          if event["hide_returned_ns"] < call["start_ns"] < following - MS], [])
        self.assertNotIn(2, [item["frame_id"] for item in self.queued])
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)

    def test_a_presence_end_hides_the_tile_at_once_and_retires_the_overlay(self):
        for reason in ("headset_removed", "standby"):
            with self.subTest(reason=reason):
                report = self.stage(ScriptedDetector(delays={2: 0.8}), scenario=f"end:{reason}", limits=self.TWO).run()
                self.assertEqual((report["status"], report["reason"]), ("presence_ended", reason))
                hide = next(call for call in self.calls("HideOverlay") if call["start_ns"] >= self.end_packet_ns - MS)
                self.assertLess(hide["start_ns"] - self.end_packet_ns, MARGIN_NS)
                self.assertIs(hide["visible"], False)
                self.assertLess(hide["end_ns"], self.queued[0]["expires_ns"] - 500 * MS)
                self.assertEqual([call["call"] for call in self.calls("SetOverlayRaw", "ShowOverlay")
                                  if call["start_ns"] > self.end_packet_ns], [])
                self.assert_nothing_shown_past_its_deadline(report)
                self.assert_retired(report)
                self.assert_both_children_closed(report)

    def test_a_stop_and_a_consumer_error_each_hide_the_tile_and_retire_the_overlay(self):
        long_run = {"fps": 5, "max_seconds": 20, "max_frames": 100}
        stop = threading.Event()
        stage = self.stage(limits=long_run)
        timer = threading.Timer(0.7, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        report = stage.run(stop)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "stop_requested"))
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)
        report = self.stage(ScriptedDetector(fail_on=3), limits=long_run).run()
        self.assertEqual((report["status"], report["error_type"]), ("error", "RuntimeError"))
        down, = self.named("taken_down")
        hide = next(call for call in self.calls("HideOverlay") if call["start_ns"] >= down["sent_ns"] - MS)
        self.assertLess(hide["start_ns"] - self.detector.called_ns[2], MARGIN_NS)
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)


if __name__ == "__main__":
    unittest.main()
