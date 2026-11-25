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
import hashlib
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
from tests import fake_camera_export, fake_presence_watcher, fake_vrclient

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
        self.called_ns, self.returned_ns, self.shapes = [], [], []

    def detect(self, image, threshold):
        self.called_ns.append(time.monotonic_ns())
        self.shapes.append(getattr(image, "shape", None))  # What the detector was given: the region, turned.
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
    # The layout the stage draws for, and what a stage for it needs beside the strip's defaults. The strip here;
    # ``tests/test_camera_hud_stage_panel.py`` runs these same tests with the box panel (work-order item B5).
    LAYOUT = HUD_CONFIG
    STAGE_OPTIONS = {}

    def smallest_frame(self):
        """``(width, height, rgba)`` of the smallest frame the client takes: one pixel for the strip's client,
        which checks no size, and the layout's own size for a client that names its layout."""
        if self.LAYOUT is HUD_CONFIG:
            return 1, 1, bytes(4)
        return self.LAYOUT.width_px, self.LAYOUT.height_px, bytes(self.LAYOUT.width_px * self.LAYOUT.height_px * 4)

    def expected_payload(self, result, mapping=None, **options):
        """``(labels, rgba bytes)`` the stage must send for ``result``: drawn here from the detections alone.

        ``mapping`` is the ``Mapping`` of the frame the detector saw (None: the whole 64 x 48 frame, unprepared).
        For the strip: the rows of the mapped detections over the whole frame's shape, drawn and converted.
        """
        from sightcircuit.hud.camera import camera_rows
        from sightcircuit.hud.render import draw_tile
        from sightcircuit.overlay import rgba_from_bgr
        mapped = result if mapping is None else mapping.result(result)
        rows = camera_rows(list(mapped.detections), (48, 64), HUD_CONFIG, threshold=0.5, **options)
        return tuple(dict.fromkeys(row.label for row in rows)), rgba_from_bgr(draw_tile(rows, HUD_CONFIG))[2]

    def layout_options(self):
        """What a client is given for ``LAYOUT``: nothing for the strip, whose client names no layout."""
        return {} if self.LAYOUT is HUD_CONFIG else {"layout": self.LAYOUT}

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = fake_camera_export.install(cls.directory.name)
        cls.watcher = fake_presence_watcher.install(cls.directory.name)

    def setUp(self):
        for number in RECORD_AND_POLL.numbers:
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def watch(self, scenario=None, seconds=20, binary=None, **options):
        """A presence watch on the watcher's stand-in (``tests/fake_presence_watcher.py``, never SteamVR), scripted
        by ``scenario``. Its stop event is the run's. The stand-in's process id is in ``self.watcher_pid()``."""
        from sightcircuit.camera.presence import PresenceWatch
        self.pid_file = Path(self.directory.name) / f"watcher-{self.id().rsplit('.', 1)[1][:60]}-{time.monotonic_ns()}.pid"
        values = {"FAKE_PRESENCE_PID_FILE": str(self.pid_file)}
        if scenario is not None:
            values["FAKE_PRESENCE_SCENARIO"] = scenario
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)
        if scenario is None:
            os.environ.pop("FAKE_PRESENCE_SCENARIO", None)
        watch = PresenceWatch(binary or self.watcher, seconds, threading.Event(), **options)
        self.addCleanup(watch.close)
        return watch

    def watcher_pid(self):
        return int(self.pid_file.read_text()) if self.pid_file.is_file() else None

    def assert_nothing_was_launched(self, stage, report):
        """A run that ended while its watch was arming: no bridge, no helper, no frame, and a clean report."""
        self.assertEqual((stage.client.process, stage.pipeline, self.source.status), (None, None, "not_started"))
        self.assertEqual((self.frames, self.queued, self.packets, self.bridge_lines), ([], [], [], []))
        self.assertEqual((report["bridge"]["started"], report["bridge"]["worker_exit_code"], report["pipeline"],
                          report["frames"]["sent"], report["end"]["hide_written_ns"]), (False, None, None, 0, None))
        self.assertEqual(report["cleanup"], {"source_ok": True, "bridge_ok": True, "sinks_ok": True, "ok": True})
        pid = self.watcher_pid()
        self.assertFalse(pid is not None and alive(pid))  # No watcher outlives the run.
        self.assert_no_gate_opened(report)

    def client(self, **options):
        return ObservedFrameClient(**self.layout_options(), **options)

    def stage(self, detector=None, scenario=None, limits=None, fake=(), client=None, **options):
        from sightcircuit.convert import to_bgr
        from sightcircuit.hud.camera_stage import CameraHudStage
        extra = (("--fake-scenario", scenario) if scenario else ()) + tuple(str(item) for item in fake)
        self.source = CameraHelperSource(self.binary, limits or self.LIMITS, confirmed=True, extra_args=extra)
        self.detector = detector or ScriptedDetector()
        self.frames, self.queued, self.packets, self.bridge_lines, self.end_packet_ns = [], [], [], [], None

        def convert(frame):
            self.frames.append(frame)
            return to_bgr(frame)
        client = client or self.client()
        options.setdefault("max_fps", 10)
        for name, value in self.STAGE_OPTIONS.items():
            options.setdefault(name, value)

        def seen(frame):  # A frame the stage prepares itself (a region, the panel) never reaches ``convert``.
            if stage.prepares:
                self.frames.append(frame)
        stage = self.the_stage = CameraHudStage(self.source, self.detector, client, convert=convert, on_frame=seen,
                                                **options)
        queue, record, on_end = client.submit_observed, stage._on_packet, self.source._on_end

        def submit_observed(frame_id, observed_ns, expires_ns, width, height, rgba, *, token=None):
            self.queued.append({"frame_id": frame_id, "observed_ns": observed_ns, "expires_ns": expires_ns,
                                "size": (width, height), "rgba": rgba, "token": token, "at_ns": time.monotonic_ns()})
            return queue(frame_id, observed_ns, expires_ns, width, height, rgba, token=token)

        def listener(event, **fields):  # The writer's events; the bridge's lines are kept apart, as read.
            (self.bridge_lines.append(fields["line"]) if event == "bridge_line" else self.packets.append((event, fields)))
            record(event, **fields)

        def end(header):
            self.end_packet_ns = time.monotonic_ns()
            on_end(header)
        client.submit_observed, client.listener, self.source._on_end = submit_observed, listener, end
        return stage

    def named(self, name):
        return [fields for event, fields in self.packets if event == name]

    def run_with_the_end_during_the_third_conversion(self):
        """Run until the third tile's colour conversion, set ``stop`` inside it and let it return only once the
        watcher has ended the client: ``submit`` has passed its own check and now meets the client's latch."""
        from sightcircuit.hud import camera_stage
        stop = threading.Event()
        stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        # Where the drawn image becomes the bytes that are sent: the strip's colour conversion, the panel's pack.
        target, name = (camera_stage, "rgba_from_bgr") if self.LAYOUT is HUD_CONFIG else (stage, "_pack")
        convert, calls = getattr(target, name), []

        def racing(image):
            calls.append(time.monotonic_ns())
            if len(calls) == 3:
                self.assertFalse(stage._ended.is_set())  # ``submit`` passed its own check with the run still live.
                stop.set()
                deadline = time.monotonic() + 3
                while not stage.client.final and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertTrue(stage.client.final)
            return convert(image)
        with mock.patch.object(target, name, racing):
            report = stage.run(stop)
        return report, calls

    def assert_deadlines_within_the_cap(self, run_deadline_ns=None):
        """Every frame handed to the client carries its camera frame's observation and a deadline within the cap.

        The deadline is the observation plus the maximum display age, or the
        run's own deadline (``seconds``) when that comes first. Returns the
        ids of the frames whose write returned at or after their deadline.

        The writer looks at a frame's deadline before it writes
        (``tests/test_observed_frame_client.py`` pins that), and the write
        then takes time, a megabyte more than a strip, so ``sent_ns``, read
        when the write has returned, can lie past the deadline. With a
        second of deadline ahead of every write that never happens here,
        and without a run deadline the helper still says so. With one,
        every frame's deadline is the run's end, and the run's last result
        can be handed over a fraction of a millisecond before it. The helper
        then asserts what the product promises of such a frame, none of it a
        matter of timing: it is the last frame written and the only late
        one, its deadline is the run's, the stage counted it
        (``frames_written_past_deadline``), and the bridge answered for it
        exactly once. Which answer is the bridge's own, by its own clock: it
        usually refuses the frame as expired on arrival, and then it never
        held it; or it took it just before the deadline while this side's
        clock was read just after, and then it holds it under that same
        deadline and expires it there. (Both answers are forced, on every
        run, by the test of a slow write below.)
        """
        observed = {frame.sequence: frame.observed_ns for frame in self.frames}
        self.assertEqual([item["frame_id"] for item in self.queued], sorted({item["frame_id"] for item in self.queued}))
        for item in self.queued:
            self.assertEqual(item["observed_ns"], observed[item["frame_id"]])  # Never the send time.
            self.assertLess(item["observed_ns"], item["at_ns"])
            self.assertLessEqual(item["expires_ns"] - item["observed_ns"], MAX_DISPLAY_AGE_NS)
            longest = item["observed_ns"] + MAX_DISPLAY_AGE_NS
            self.assertEqual(item["expires_ns"], longest if run_deadline_ns is None else min(longest, run_deadline_ns))
            self.assertEqual(item["size"], (self.LAYOUT.width_px, self.LAYOUT.height_px))
        handed = {item["frame_id"]: item["at_ns"] for item in self.queued}
        sent = self.named("frame_sent")
        for fields in sent:
            self.assertLess(handed[fields["frame_id"]], fields["expires_ns"])  # Handed over before its deadline.
        late = [fields for fields in sent if fields["sent_ns"] >= fields["expires_ns"]]
        # Counted, always: this is the report's ``frames.written_past_deadline``.
        self.assertEqual(self.the_stage.counts["frames_written_past_deadline"], len(late))
        if run_deadline_ns is None:
            self.assertEqual(late, [])  # A second of deadline ahead of every write: none returns late.
            return []
        self.assertLessEqual(len(late), 1)  # One writer, one shared deadline: after it nothing more is written.
        for fields in late:
            self.assertIs(fields, sent[-1])
            self.assertEqual(fields["expires_ns"], run_deadline_ns)
            answers = [line for line in self.bridge_lines
                       if line["event"] in ("frame_presented", "frame_refused") and line["frame_id"] == fields["frame_id"]]
            self.assertEqual(len(answers), 1, answers)  # The bridge answered for it, once.
            answer, = answers
            expired = [line["deadline_ns"] for line in self.bridge_lines
                       if line["event"] == "frame_expired" and line.get("frame_id") == fields["frame_id"]]
            if answer["event"] == "frame_refused":  # It reached the bridge after its deadline: refused, never held.
                # (The validation bridge uploads nothing, so it has no refusal after an upload.)
                self.assertIn(answer["reason"], ("expired_on_arrival",) if self.the_stage.client.validation_only
                              else ("expired_on_arrival", "expired_before_show"))
                self.assertGreaterEqual(answer["refused_ns"], fields["expires_ns"])
                self.assertEqual(expired, [])
            else:  # Taken before the deadline by the bridge's clock: held under that deadline and expired at it.
                self.assertEqual(answer["deadline_ns"], fields["expires_ns"])
                self.assertEqual(expired, [fields["expires_ns"]])
        return [fields["frame_id"] for fields in late]

    def hold_a_write(self, stage, *, write_first, run_bound=False):
        """Hold one frame write of the stage's client across that frame's deadline; returns the list the hold
        fills with ``(frame_id, deadline_ns, when the hold ended)``.

        The frame held is the first one written or, with ``run_bound``, the first one written whose deadline
        is the run's own bound. Which frame that is, and its deadline, are read from the packet being written:
        on a loaded host the first frame handed over is not always the first one the writer reaches.

        With ``write_first`` false the bytes go out only after the deadline, as behind a pipe that drains
        slowly: the frame reaches the bridge too late. With it true the bytes go out at once and only the
        write's return is held, as when the writer thread loses the processor after its last byte: the bridge
        has the frame in time and this side's clock reads late. One sleep to an absolute time; nothing polls.
        """
        import struct
        from sightcircuit.hud.contract import OBSERVED_FRAME_PREFIX_FORMAT, OPCODE_OBSERVED_FRAME, PACKET_HEADER_FORMAT
        send, held, prefix_at = stage.client._send, [], struct.calcsize(PACKET_HEADER_FORMAT)

        def slow(data):
            if held or struct.unpack_from(PACKET_HEADER_FORMAT, data)[1] != OPCODE_OBSERVED_FRAME:
                return send(data)
            frame_id, _, deadline_ns = struct.unpack_from(OBSERVED_FRAME_PREFIX_FORMAT, data, prefix_at)[:3]
            if run_bound and deadline_ns != int(stage.pipeline.deadline * 1e9):
                return send(data)  # A frame with its own display age ahead of it: not the one to hold.
            if write_first:
                send(data)
            time.sleep(max(0.0, (deadline_ns - time.monotonic_ns()) / 1e9) + 0.001)
            held.append((frame_id, deadline_ns, time.monotonic_ns()))
            if not write_first:
                send(data)
        stage.client._send = slow
        return held

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
                         {"production_live_input_enabled": False, "production_camera_input_enabled": False,
                          "mapping_qualified": False, "production_hud_enabled": False})
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
        self.assertEqual((frames["bridge_received"], frames["bridge_accepted"], frames["retransmitted"],
                          frames["refused"], frames["expired"], frames["expired_in_upload"]), (sent, sent, 0, 0, 0, 0))
        # The bridge answered for every frame written: one frame_presented line each, none refused, none unanswered.
        self.assertEqual((frames["acknowledged_presented"], frames["acknowledged_refused"], frames["unacknowledged"],
                          frames["refused_reasons"]),
                         (sent, 0, 0, {"stale": 0, "expired_on_arrival": 0, "retransmission": 0, "expired_before_show": 0}))
        self.assertEqual([line["frame_id"] for line in self.bridge_lines if line["event"] == "frame_presented"],
                         [fields["frame_id"] for fields in self.named("frame_sent")])
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
        self.assertEqual((output["count"], output["timebase"], output["from"], output["to"]),
                         (report["frames"]["sent"], "host_clock_monotonic", "camera_frame_observed",
                          "packet_written_to_bridge_pipe"))
        self.assertGreaterEqual(output["p50"], timings["input_age_ms"]["p50"])
        self.assertLess(output["max"], 1000)
        # Capture to display covers every frame the bridge acknowledged, to the time its calls returned, and
        # says that this is not the headset's display.
        display = timings["capture_to_display_age_ms"]
        presented = {line["frame_id"]: line for line in self.bridge_lines if line["event"] == "frame_presented"}
        sent = {fields["frame_id"]: fields for fields in self.named("frame_sent")}
        self.assertEqual(set(presented), set(sent))
        self.assertEqual((display["count"], display["to"], display["acknowledged_by"], display["frames_without_acknowledgement"],
                          display["ends_at_headset_display"], display["physical_display_observed"]),
                         (len(sent), "bridge_present_calls_returned", "bridge_frame_presented_line", 0, False, False))
        ages = sorted((presented[frame_id]["presented_ns"] - sent[frame_id]["observed_ns"]) / 1e6 for frame_id in sent)
        self.assertAlmostEqual(display["max"], ages[-1], places=2)
        self.assertAlmostEqual(display["p50"], ages[(len(ages) + 1) // 2 - 1], places=2)
        for frame_id, line in presented.items():
            self.assertEqual((line["frame_observed_ns"], line["deadline_ns"], line["visible"], line["validation_only"]),
                             (sent[frame_id]["observed_ns"], sent[frame_id]["expires_ns"], True, True))
            self.assertGreater(line["presented_ns"], sent[frame_id]["observed_ns"])
        self.assertEqual(report["bridge"]["first_frame"]["event"], "frame_validated")
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
        self.assertEqual(report["config"]["hud_config_id"], self.LAYOUT.layout_id)

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
        # The label's two spells and the gap between them, from the bridge's own hide and show lines.
        row, = report["labels"]["rows"]
        self.assertEqual((row["label"], row["spells"], len(row["intervals"])), ("bottle", 2, 2))
        hidden = next(line for line in self.bridge_lines if line["event"] == "hide_returned")
        back = next(line for line in self.bridge_lines if line["event"] == "show_returned" and line["ok"])
        start = report["labels"]["run_start_ns"]
        self.assertAlmostEqual(row["intervals"][0]["end_ms"], (hidden["returned_ns"] - start) / 1e6, places=2)
        self.assertAlmostEqual(row["intervals"][1]["start_ms"], (back["returned_ns"] - start) / 1e6, places=2)
        gap = (back["returned_ns"] - hidden["returned_ns"]) / 1e9
        self.assertGreater(gap, 0.3)
        self.assertLess(gap, 0.3 + 2 * PERIOD_S)
        self.assertGreaterEqual(row["longest_gap_seconds"], round(gap, 3))  # Or the lead-in, if that was longer.
        self.assertGreater(row["display_seconds"], 0.6)
        self.assertEqual((report["labels"]["physical_display_observed"], report["labels"]["spells_dropped"],
                          report["labels"]["basis"]), (False, 0, "bridge_frame_presented_hide_show_and_expiry_lines"))
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
        # Drawn and counted, never sent: nothing here is reported as sent (the queue step, and on the panel the
        # boxes, which are counted as selected and not as sent).
        self.assertEqual((stage.steps["queue"].report()["count"], stage.steps["pack"].report()["count"],
                          stage._results_sent, stage._boxes_sent), (0, 0, 0, 0))
        if stage.boxed:
            self.assertEqual((stage._boxes_counted, stage._boxes["boxes_shown"]), (2, 2))
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
                self.assertLess(abs(report["end"]["ended_ns"] - self.end_packet_ns), MARGIN_NS)
                end = report["end"]
                self.assertEqual((end["hide_written_ns"], end["tile_already_down"], end["bridge_ended_first"]),
                                 (down["sent_ns"], False, False))
                self.assertLess(end["hide_written_after_end_ms"], MARGIN_NS / 1e6)
                self.assertGreaterEqual(end["bridge_hide_returned_ns"], end["hide_written_ns"] - MS)
                self.assertLess(end["bridge_hide_after_end_ms"], MARGIN_NS / 1e6)
                if len(detector.returned_ns) > 1:  # The call was in flight: the hide did not wait for it.
                    self.assertLess(down["sent_ns"], detector.returned_ns[1] - 400 * MS)
                self.assertLess(down["sent_ns"], self.queued[0]["expires_ns"] - 500 * MS)  # Not left to expire.
                self.assertEqual(report["results"]["results"], 1)  # The second frame's result reached no sink.
                row, = report["labels"]["rows"]
                self.assertAlmostEqual(row["intervals"][-1]["end_ms"],
                                       (end["bridge_hide_returned_ns"] - report["labels"]["run_start_ns"]) / 1e6, places=2)
                self.assert_both_children_closed(report)
                self.assert_no_gate_opened(report)
                self.assertLess(time.monotonic() - started, 6)

    def test_the_presence_watch_ends_a_run_at_its_signal_and_the_report_times_the_hide_from_the_signal(self):
        # The watch a displayed run holds (work-order item B5): the watcher's standby signal sets the run's stop,
        # the tile comes down at once without waiting for the detector, and the report says how long the hide
        # took from the signal's own clock reading. The watcher here is the stand-in, never SteamVR.
        detector = ScriptedDetector(delays={5: 1.5})  # In its call when the signal arrives, about 1.2 s in.
        watch = self.watch("1200:enter_standby_event")
        stage = self.stage(detector, limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, watch=watch)
        self.assertEqual((stage.presence_watch, stage.view()["presence_watch"], stage.watcher_binary),
                         (True, True, self.watcher))
        started = time.monotonic()
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("presence_ended", "standby"))
        block = report["presence_watch"]
        self.assertEqual(sorted(block), sorted(set(block)))
        self.assertEqual({name: block[name] for name in (
            "requested", "armed", "refusal", "protocol", "ended_run", "run_end_reason", "ending_signal", "lost",
            "lost_ended_run", "forced_kill", "cleanup_ok", "camera_data_read", "exit_code")},
            {"requested": True, "armed": True, "refusal": None, "protocol": "presence-watch/1", "ended_run": True,
             "run_end_reason": "standby", "ending_signal": "enter_standby_event", "lost": None,
             "lost_ended_run": False, "forced_kill": False, "cleanup_ok": True, "camera_data_read": False,
             "exit_code": 0})
        self.assertEqual(block["watcher_sha256"], hashlib.sha256(self.watcher.read_bytes()).hexdigest())
        self.assertGreaterEqual(block["heartbeats"], 5)
        self.assertGreaterEqual(block["event_count"], 1)
        self.assertGreaterEqual(block["signal_received_ns"], block["signal_ns"])
        end, (down,) = report["end"], self.named("taken_down")
        # The run ended at the signal's arrival, and the hide was written then: within this host's margin of
        # the signal, and long before the detector returned or the tile's own deadline.
        self.assertGreaterEqual(end["ended_ns"], block["signal_received_ns"])
        self.assertLess(end["ended_ns"] - block["signal_ns"], MARGIN_NS)
        self.assertEqual((end["hide_written_ns"], end["tile_already_down"], end["bridge_ended_first"]),
                         (down["sent_ns"], False, False))
        self.assertEqual(block["hide_written_after_signal_ms"], round((down["sent_ns"] - block["signal_ns"]) / 1e6, 3))
        self.assertEqual(block["bridge_hide_after_signal_ms"],
                         round((end["bridge_hide_returned_ns"] - block["signal_ns"]) / 1e6, 3))
        self.assertTrue(0 < block["hide_written_after_signal_ms"] < MARGIN_NS / 1e6, block)
        self.assertTrue(0 < block["bridge_hide_after_signal_ms"] < MARGIN_NS / 1e6, block)
        self.assertLess(down["sent_ns"], detector.returned_ns[4] - 500 * MS)  # The detector was still in its call.
        self.assertLess(down["sent_ns"], self.queued[-1]["expires_ns"] - 300 * MS)  # Not left to expire.
        self.assertEqual(type(block["frames_received_after_signal"]), int)
        self.assertEqual(report["source"]["status"], "stopped")
        self.assert_deadlines_within_the_cap()
        self.assert_both_children_closed(report)
        self.assert_no_gate_opened(report)
        self.assertFalse(alive(self.watcher_pid()))  # The watcher was ended with the run.
        self.assertTrue(watch.closed)
        self.assertLess(time.monotonic() - started, 8)

    def test_a_watch_that_is_not_armed_ends_the_run_by_name_before_the_bridge_or_the_helper_is_launched(self):
        # Fail closed: a run that holds a watch does not start without it. Each case is a watcher that cannot
        # be armed; the run ends ``presence_watch_not_armed`` with the watcher's own refusal in the report.
        absent = Path(self.directory.name) / "no-such-watcher"
        cases = (("noready", {"ready_wait": 0.4}, "watcher_not_ready_in_time"),
                 ("error=vrserver_not_running", {}, None),
                 ("activity=standby", {}, None),
                 ("camera", {}, None),
                 (None, {"binary": absent}, None),
                 (None, {"pinned": "0" * 64}, "watcher_not_pinned"))
        for scenario, options, refusal in cases:
            with self.subTest(scenario=scenario, options=sorted(options)):
                pinned = options.pop("pinned", None)
                watch = self.watch(scenario, **options)
                watch.expected_sha256 = pinned
                stage = self.stage(watch=watch)
                started = time.monotonic()
                report = stage.run()
                self.assertEqual((report["status"], report["reason"]), ("error", "presence_watch_not_armed"))
                block = report["presence_watch"]
                self.assertEqual((block["requested"], block["armed"], block["ended_run"], block["lost_ended_run"],
                                  block["cleanup_ok"], block["forced_kill"]), (True, False, False, False, True, False))
                self.assertEqual(block["refusal"], watch.refusal)
                self.assertRegex(block["refusal"], r"^[a-z_]+(:[a-z_]+)?$")  # The watcher's own word for it.
                if refusal is not None:
                    self.assertEqual(block["refusal"], refusal)
                self.assertEqual((block["hide_written_after_signal_ms"], block["run_end_reason"]), (None, None))
                self.assert_nothing_was_launched(stage, report)
                self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(len(cases), 6)

    def test_a_stop_or_a_signal_while_the_watch_is_arming_ends_the_run_with_nothing_launched(self):
        # The caller's stop during the arming is the caller's stop, not a refusal: the run ends ``stopped``.
        watch = self.watch("noready", ready_wait=5)
        stage = self.stage(watch=watch)
        timer = threading.Timer(0.3, watch.stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        report = stage.run(watch.stop)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "stopped_before_launch"))
        self.assertEqual((report["presence_watch"]["armed"], report["presence_watch"]["refusal"]), (False, None))
        self.assert_nothing_was_launched(stage, report)
        self.assertLess(time.monotonic() - started, 2)
        # A stop signal the caller recorded (``signalled``) during the arming: the run was cancelled.
        watch = self.watch("noready", ready_wait=5)
        flag = threading.Event()
        stage = self.stage(watch=watch, signalled=flag.is_set)
        timer = threading.Timer(0.3, flag.set)
        timer.start()
        self.addCleanup(timer.cancel)
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("cancelled", "signal"))
        self.assert_nothing_was_launched(stage, report)

    def test_a_watch_lost_during_the_run_ends_it_by_name_and_takes_the_tile_down(self):
        # A watcher that dies, or goes silent past the heartbeat bound, is no watch: the run ends
        # ``presence_watch_lost`` with the tile hidden at once, never left up on a watch that is not there.
        for scenario, options in (("1000:die", {}), ("1000:silent", {"heartbeat_timeout": 0.5})):
            with self.subTest(scenario=scenario):
                watch = self.watch(scenario, **options)
                stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, watch=watch)
                started = time.monotonic()
                report = stage.run()
                self.assertEqual(report["status"], "presence_watch_lost")
                block = report["presence_watch"]
                self.assertEqual(report["reason"], watch.lost["reason"])
                self.assertEqual((block["armed"], block["ended_run"], block["lost_ended_run"], block["lost"],
                                  block["run_end_reason"], block["hide_written_after_signal_ms"], block["cleanup_ok"]),
                                 (True, False, True, watch.lost["reason"], None, None, True))
                self.assertRegex(block["lost"], r"^[a-z_]+$")
                down, = self.named("taken_down")
                self.assertEqual(report["end"]["hide_written_ns"], down["sent_ns"])
                self.assertLess(down["sent_ns"] - watch.lost["monotonic_ns"], MARGIN_NS)
                self.assertGreaterEqual(len(self.queued), 1)
                self.assert_deadlines_within_the_cap()
                self.assert_both_children_closed(report)
                self.assert_no_gate_opened(report)
                self.assertFalse(alive(self.watcher_pid()))
                self.assertLess(time.monotonic() - started, 8)

    def test_a_run_that_ends_by_its_own_bound_closes_its_watch_and_a_later_signal_ends_nothing(self):
        watch = self.watch("5000:enter_standby_event")
        stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, max_frames=3, watch=watch)
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "max_frames"))
        block = report["presence_watch"]
        self.assertEqual((block["requested"], block["armed"], block["ended_run"], block["lost_ended_run"],
                          block["ending_signal"], block["run_end_reason"], block["lost"], block["exit_code"],
                          block["forced_kill"], block["cleanup_ok"]),
                         (True, True, False, False, None, None, None, 0, False, True))
        self.assertFalse(alive(self.watcher_pid()))
        self.assert_both_children_closed(report)
        # A stage without a watch says so, and its report's block is the same shape.
        plain = self.stage(max_frames=2)
        self.assertEqual((plain.presence_watch, plain.view()["presence_watch"], plain.watcher_binary), (False, False, None))
        without = plain.run()["presence_watch"]
        self.assertEqual(sorted(without), sorted(block))
        self.assertEqual((without["requested"], without["armed"], without["cleanup_ok"], without["watcher_sha256"]),
                         (False, False, True, None))

    def test_the_stage_takes_only_a_presence_watch_and_runs_on_that_watchs_own_stop(self):
        from sightcircuit.camera.presence import PresenceWatch
        for value in (True, object(), "watch", PresenceWatch, self.watcher):
            with self.subTest(watch=repr(value)[:30]), self.assertRaisesRegex(ValueError, "^camera_hud_presence_watch_invalid$"):
                self.stage(watch=value)
        watch = self.watch()
        stage = self.stage(max_frames=2, watch=watch)
        with self.assertRaisesRegex(ValueError, "^camera_hud_presence_stop_mismatch$"):
            stage.run(threading.Event())  # Another event would leave the watch's signal ending nothing.
        self.assertEqual((stage.client.process, self.source.status, self.watcher_pid()), (None, "not_started", None))
        report = stage.run(watch.stop)  # The watch's own event, or none: the run is the watch's.
        self.assertEqual((report["status"], report["presence_watch"]["armed"]), ("completed", True))
        with self.assertRaisesRegex(AttributeError, ""):
            stage.presence_watch = False  # What the sites compare cannot be assigned.

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
        late = self.assert_deadlines_within_the_cap(deadline_ns)
        frames = report["frames"]  # The report says so too, and every frame written was answered.
        self.assertEqual((frames["written_past_deadline"], frames["sent"], frames["unacknowledged"]),
                         (len(late), len(self.named("frame_sent")), 0))
        self.assertEqual(frames["acknowledged_presented"] + frames["acknowledged_refused"], frames["sent"])
        self.assertLess(max(item["expires_ns"] for item in self.queued), max(item["observed_ns"] for item in self.queued)
                        + MAX_DISPLAY_AGE_NS)  # The run's end cut the last deadlines short; none outlives the run.
        self.assert_both_children_closed(report)

    def test_a_frame_whose_write_returns_past_its_deadline_is_counted_and_the_bridge_decides_by_its_own_clock(self):
        # The writer checks a frame's deadline before it writes, and the write then takes time, so its return
        # can lie past the deadline. Both ways that can happen are forced here on every run, for whichever
        # layout this class runs.
        def lines(event, frame_id=None):
            return [line for line in self.bridge_lines
                    if line["event"] == event and (frame_id is None or line.get("frame_id") == frame_id)]
        roomy = {"fps": 5, "max_seconds": 20, "max_frames": 100}
        # 1. The frame reaches the bridge after its deadline (a pipe that drains slowly). The stage counts it
        #    as written past its deadline; the bridge refuses it on arrival; it is never presented, never held
        #    and no label interval is opened for it; the frames that came meanwhile were replaced unsent, and
        #    the run goes on.
        stage = self.stage(limits=roomy, max_frames=12)
        held = self.hold_a_write(stage, write_first=False)
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "max_frames"))
        (late_id, deadline_ns, resumed_ns), = held
        self.assertGreater(resumed_ns, deadline_ns)  # The construction: the write began after the deadline.
        sent = self.named("frame_sent")
        self.assertEqual((sent[0]["frame_id"], sent[0]["expires_ns"]), (late_id, deadline_ns))
        self.assertGreaterEqual(sent[0]["sent_ns"], deadline_ns)
        handed = {item["frame_id"]: item["at_ns"] for item in self.queued}
        self.assertLess(handed[late_id], deadline_ns)  # Handed over, and checked by the writer, in time.
        refused, = lines("frame_refused", late_id)
        self.assertEqual(refused["reason"], "expired_on_arrival")
        self.assertGreaterEqual(refused["refused_ns"], deadline_ns)
        self.assertEqual((lines("frame_presented", late_id), lines("frame_expired", late_id)), ([], []))
        presented = lines("frame_presented")
        self.assertTrue(presented)  # Later frames were presented: the run went on.
        frames = report["frames"]
        late = [fields["frame_id"] for fields in sent if fields["sent_ns"] >= fields["expires_ns"]]
        self.assertIn(late_id, late)
        self.assertEqual((frames["written_past_deadline"], frames["acknowledged_refused"], frames["unacknowledged"]),
                         (len(late), len(lines("frame_refused")), 0))
        self.assertGreaterEqual(frames["refused_reasons"]["expired_on_arrival"], 1)
        self.assertEqual((frames["sent"], frames["acknowledged_presented"]),
                         (len(sent), len(sent) - len(lines("frame_refused"))))
        # The frames that waited behind the slow write were replaced by newer ones without being sent.
        self.assertGreaterEqual(frames["not_sent"], 1)
        self.assertEqual(frames["submitted"], frames["sent"] + frames["not_sent"])
        # No label interval was opened for the refused frame: the label's first interval starts with the first
        # frame the bridge presented, which it read after the refused one, and so after that one's deadline.
        row, = report["labels"]["rows"]
        deadline_ms = (deadline_ns - report["labels"]["run_start_ns"]) / 1e6
        first_presented_ms = (presented[0]["presented_ns"] - report["labels"]["run_start_ns"]) / 1e6
        self.assertEqual(row["label"], "bottle")
        self.assertGreater(first_presented_ms, deadline_ms)
        self.assertAlmostEqual(row["intervals"][0]["start_ms"], first_presented_ms, delta=0.001)  # A microsecond's rounding.
        self.assert_both_children_closed(report)
        self.assert_no_gate_opened(report)
        # 2. The bridge has the frame in time and only this side's clock reads late (the writer loses the
        #    processor after its last byte), for a frame whose deadline is the run's own bound. The count is
        #    this side's clock and not a verdict on what was shown: the bridge presented the frame, held it
        #    under that deadline and expired it there, and the helper's rule for a run's bound takes it.
        stage = self.stage(limits=roomy, seconds=2.0)
        held = self.hold_a_write(stage, write_first=True, run_bound=True)
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "seconds"))
        (late_id, deadline_ns, resumed_ns), = held
        self.assertEqual(deadline_ns, int(stage.pipeline.deadline * 1e9))
        self.assertGreater(resumed_ns, deadline_ns)
        self.assertEqual(self.assert_deadlines_within_the_cap(deadline_ns), [late_id])  # The last written, and late.
        self.assertEqual((len(lines("frame_presented", late_id)), lines("frame_refused", late_id)), (1, []))
        frames = report["frames"]
        self.assertEqual((frames["written_past_deadline"], frames["acknowledged_refused"], frames["unacknowledged"]),
                         (1, len(lines("frame_refused")), 0))
        self.assertEqual(frames["sent"], frames["acknowledged_presented"] + frames["acknowledged_refused"])
        self.assertGreaterEqual(frames["expired"], 1)  # The bridge's own expiry of the frame it held, at least.
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
        self.assertEqual(report["end"]["bridge_ended_first"], True)
        self.assertEqual((report["cleanup"]["bridge_ok"], report["cleanup"]["ok"], report["cleanup"]["source_ok"]),
                         (False, False, True))
        self.assertEqual(report["bridge"]["error"], "overlay_bridge_failed")
        supervision = report["source"]["export"]["supervision"]
        self.assertFalse(alive(supervision["worker_pid"]))
        self.assertTrue(supervision["worker_tree_reaped"])

    def test_a_dead_bridge_met_by_the_pipelines_thread_before_the_watcher_is_still_the_bridge_ending_first(self):
        from sightcircuit.hud import camera_stage
        stage_box = []

        class KillsTheBridge(ScriptedDetector):
            """In its third call it kills the bridge and returns only once the client has read the bridge's end."""
            def detect(self, image, threshold):
                if len(self.called_ns) == 2:
                    client = stage_box[0].client
                    client.process.kill()
                    assert client.done.wait(5) and client.error
                return super().detect(image, threshold)
        stage = self.stage(KillsTheBridge(), limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        stage_box.append(stage)
        # The watcher never reaches a poll in this run, so only ``submit`` can notice: the order a result arriving
        # within the watcher's 20 ms produces by chance, here by construction.
        with mock.patch.object(camera_stage, "WATCHER_POLL_SECONDS", 60.0):
            started = time.monotonic()
            report = stage.run()
        self.assertLess(time.monotonic() - started, 8)
        self.assertIsInstance(stage.failure, RuntimeError)  # Raised by ``submit``; the watcher set nothing.
        self.assertEqual((report["status"], report["reason"]), ("error", "overlay_bridge_failed"))
        self.assertEqual(report["end"]["bridge_ended_first"], True)
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2])
        self.assertEqual((report["cleanup"]["bridge_ok"], report["cleanup"]["source_ok"]), (False, True))
        supervision = report["source"]["export"]["supervision"]
        self.assertFalse(alive(supervision["worker_pid"]))
        self.assertTrue(supervision["worker_tree_reaped"])
        # And a bridge that is only gone after the run has ended is not booked as having ended first.
        self.assertTrue(stage._ended.is_set() and stage._note_bridge_end())
        stage.bridge_ended_first = False
        stage._note_bridge_end()
        self.assertFalse(stage.bridge_ended_first)

    def test_a_region_and_a_rotation_reach_the_detector_and_its_boxes_come_back_to_the_whole_frame(self):
        # Work-order item L17, for whichever layout this class runs: the detector is given the region of the
        # frame, turned; its boxes are taken back to the whole frame before anything is selected; and what is
        # sent is drawn from those mapped boxes alone. The stand-in's frames are 64 x 48.
        from sightcircuit.camera.region import FRAME_PIXELS, Mapping, Region
        centre = (0.25, 0.25, 0.75, 0.75)  # 32 x 24 native pixels at (16, 12).
        # In the detector's input pixels, inside its 24 x 24 corner so that they fit the input at every turn.
        boxes = (("bottle", (0, 8, 6, 16)), ("cup", (14, 2, 22, 10)))
        result = DetectionResult(tuple(Detection(label, 0.9, box) for label, box in boxes), 1.0)
        payloads = {}
        for rotation, shape in ((0, (24, 32, 3)), (90, (32, 24, 3)), (180, (24, 32, 3)), (270, (32, 24, 3))):
            region = Region(*centre, rotation=rotation)
            detector = ScriptedDetector([boxes])
            report = self.stage(detector, limits=self.TWO, scenario="end:max_frames", region=region).run()
            mapping = Mapping((64, 48), (16, 12, 48, 36), (32, 24), rotation, (64, 48))
            labels, expected = self.expected_payload(result, mapping)
            with self.subTest(rotation=rotation):
                self.assertEqual((report["status"], detector.shapes), ("completed", [shape, shape]))
                self.assertEqual([frame.sequence for frame in self.frames], [1, 2])
                self.assertEqual([(item["token"], item["size"], item["rgba"] == expected) for item in self.queued],
                                 [(labels, (self.LAYOUT.width_px, self.LAYOUT.height_px), True)] * 2)
                self.assertEqual(labels, ("bottle", "cup"))
                self.assertEqual(report["view"], {
                    "region": dict(zip(("x0", "y0", "x1", "y1"), centre)), "rotation": rotation, "whole_frame": False,
                    "prepared": True, "box_coordinate_space": FRAME_PIXELS, "source_space_px": [64, 48],
                    "crop_native_xyxy": [16, 12, 48, 36], "resized_px": [32, 24], "geometry_constant": True,
                    "invalid_detections": 0})
                self.assertEqual(self.the_stage.view(), {"region": list(centre), "rotation": rotation,
                                                         "alignment": self.the_stage.config_alignment(),
                                                         "presence_watch": False})
                self.assertEqual(report["frames"]["acknowledged_presented"], 2)
                self.assert_both_children_closed(report)
            payloads[rotation] = expected
        # A quarter turn changes what is sent for the same detector boxes. (How many of the four differ is the
        # layout's: the strip's arrows follow the boxes round the whole frame, so all four; the panel shows the
        # region as the detector saw it, so the two shapes of the detector's input.)
        self.assertNotEqual(payloads[0], payloads[90])
        self.assertNotEqual(payloads[180], payloads[270])
        # By hand, unturned: the first box is 16..22 by 20..28 of the whole frame, the second 30..38 by 14..22.
        unturned = Mapping((64, 48), (16, 12, 48, 36), (32, 24), 0, (64, 48)).result(result)
        self.assertEqual([item.box_xyxy for item in unturned.detections], [(16, 20, 22, 28), (30, 14, 38, 22)])
        # A region of the whole frame, unturned, is no region: the frame goes the way it always went on the strip.
        whole = self.stage(limits=self.TWO, scenario="end:max_frames", region=Region())
        report = whole.run()
        self.assertEqual((report["view"]["whole_frame"], report["view"]["prepared"], whole.prepares),
                         (True, self.LAYOUT is not HUD_CONFIG, self.LAYOUT is not HUD_CONFIG))
        # The whole frame turned is a region like any other.
        turned = self.stage(limits=self.TWO, scenario="end:max_frames", region=Region(rotation=90))
        report = turned.run()
        self.assertEqual((report["view"]["prepared"], report["view"]["crop_native_xyxy"], self.detector.shapes[0]),
                         (True, [0, 0, 64, 48], (64, 48, 3)))

    def test_a_box_that_is_not_one_in_the_detectors_input_is_dropped_before_mapping_whatever_the_view(self):
        # Taking a box back to the whole frame sorts, clamps and rounds it, so a box outside the detector's input,
        # inverted, or not a box at all must be judged before that: otherwise a region or a turn would show an
        # object the whole-frame run does not, or end the run as an error and waste its record. Each shape below
        # is dropped and counted, with every view, and what is sent is drawn from the one valid box alone.
        from sightcircuit.camera.region import Mapping, Region
        from sightcircuit.hud import camera_stage
        good = ("bottle", (2, 4, 14, 16))  # Inside the detector's input at every view below (the smallest is 24 x 32).
        bad = (("cup", (60, 40, 70, 50)),  # Outside the input (and partly outside the largest, 64 x 48).
               ("cup", (14, 16, 2, 4)),  # Inverted.
               ("cup", (-4, 2, 6, 10)),  # Negative.
               ("cup", (5, 5, 5, 9)),  # No width.
               ("cup", (float("nan"), 2, 6, 10)), ("cup", (2, 2, float("inf"), 10)), ("cup", (2, 4, 14)),
               ("cup", None), ("cup", "a box"))
        only = DetectionResult((Detection(good[0], 0.9, good[1]),), 1.0)
        centre = (0.25, 0.25, 0.75, 0.75)
        views = ((None, None), (Region(*centre), Mapping((64, 48), (16, 12, 48, 36), (32, 24), 0, (64, 48))),
                 (Region(*centre, rotation=90), Mapping((64, 48), (16, 12, 48, 36), (32, 24), 90, (64, 48))),
                 (Region(rotation=270), Mapping((64, 48), (0, 0, 64, 48), (64, 48), 270, (64, 48))))
        for region, mapping in views:
            report = self.stage(ScriptedDetector([(good, *bad)]), limits=self.TWO, scenario="end:max_frames",
                                region=region).run()
            labels, expected = self.expected_payload(only, mapping)
            with self.subTest(region=None if region is None else (region.fractions, region.rotation)):
                self.assertEqual((report["status"], report["reason"], labels), ("completed", "max_frames", ("bottle",)))
                self.assertEqual([(item["token"], item["rgba"] == expected) for item in self.queued],
                                 [(("bottle",), True)] * 2)
                self.assertEqual(report["view"]["invalid_detections"], 2 * len(bad))
                self.assertEqual([row["label"] for row in report["labels"]["rows"]], ["bottle"])
                if report["boxes"] is not None:  # The panel's block counts them as detections dropped as invalid.
                    boxes = report["boxes"]
                    self.assertEqual((boxes["detections"], boxes["dropped"]["invalid"], boxes["boxes_dropped"],
                                      boxes["boxes_selected"], boxes["boxes_sent"], boxes["results_sent"]),
                                     (2 * (1 + len(bad)), 2 * len(bad), 2 * len(bad), 2, 2, 2))
                self.assert_both_children_closed(report)
        # The rule is the selector's own, so the whole frame without a mapping judges as it always did: the
        # strip's rows take plain Python numbers (a boolean is one), the panel's boxes any real number but a
        # boolean. An integer too large for a float is judged, not raised on.
        import numpy as np
        plain = self.LAYOUT is HUD_CONFIG
        edge = [Detection("bottle", 0.9, box) for box in (
            (False, False, True, True), (np.float32(2), np.float32(4), np.float32(14), np.float32(16)),
            (2, 4, 10 ** 400, 16), (2.0, 4.0, 64.0, 48.0), (0, 0, 64, 48), (0, 0, 65, 48))]
        valid, invalid = camera_stage.valid_detections(edge, (64, 48), plain)
        self.assertEqual(([edge.index(item) for item in valid], invalid), ([0, 3, 4] if plain else [1, 3, 4], 3))
        self.assertEqual(camera_stage.valid_detections([None, "text", Detection("cup", 0.9, [1, 2, 3, 4])], (64, 48),
                                                       plain)[1], 2)
        # More detections than the selectors' budget is still the error it was, on what the detector returned.
        stage = self.stage()
        crowd = DetectionResult(tuple(Detection("cup", 0.9, None) for _ in range(129)), 1.0)
        with self.assertRaisesRegex(ValueError, "hud_detection_budget_exceeded"):
            stage._tile(np.zeros((48, 64, 3), np.uint8), crowd)

    def test_what_the_sites_compare_cannot_be_assigned_and_is_what_every_frame_uses(self):
        # The layout, the region, the alignment and the watch are what the permission sites compare with the
        # record. None can be assigned once the stage is built, and whether a frame is prepared is derived from
        # the same values on every frame, so the sites cannot approve one view and the run use another.
        from sightcircuit.camera.region import Region
        from sightcircuit.hud.camera_stage import CameraHudStage
        centre = (0.25, 0.25, 0.75, 0.75)
        stage = self.stage()
        for name, value in (("config", HUD_CONFIG), ("region", Region(*centre)), ("alignment", None),
                            ("presence_watch", True), ("boxed", True), ("prepares", True)):
            with self.subTest(assigned=name), self.assertRaises(AttributeError):
                setattr(stage, name, value)
            self.assertIsNone(getattr(CameraHudStage, name).fset)
        self.assertIs(stage.presence_watch, False)  # What the stage holds: no watch.
        self.assertNotIn("presence_watch", vars(stage))  # Not a flag that could be set beside it.
        self.assertEqual((stage.view()["region"], stage.view()["rotation"], stage.prepares),
                         ([0.0, 0.0, 1.0, 1.0], 0, self.LAYOUT is not HUD_CONFIG))
        # Even with the private field forced, the sites' view and the per-frame path are one value: the view
        # states the region, and the detector is given it.
        stage = self.stage(limits=self.TWO, scenario="end:max_frames")
        stage._region = Region(*centre, rotation=90)
        self.assertEqual((stage.view()["region"], stage.view()["rotation"], stage.prepares), (list(centre), 90, True))
        report = stage.run()
        self.assertEqual((self.detector.shapes, report["view"]["rotation"], report["view"]["crop_native_xyxy"],
                          report["view"]["prepared"]), ([(32, 24, 3)] * 2, 90, [16, 12, 48, 36], True))

    def test_with_a_rotation_the_strips_arrows_stay_in_the_unturned_frame_unless_the_paired_orientation_is_given(self):
        # The stage takes boxes back to the unturned whole frame, so a rotation alone does not turn the strip's
        # arrows: they refer to the camera's delivered image. ``ROTATION_ORIENTATION`` is the orientation that
        # makes them refer to the image as the detector saw it; the operator gives both.
        from sightcircuit.camera.region import Mapping, Region
        from sightcircuit.hud.camera import camera_rows
        from sightcircuit.hud.camera_stage import ROTATION_ORIENTATION

        def arrow(box, shape, **options):
            row, = camera_rows([Detection("bottle", 0.9, box)], shape, HUD_CONFIG, threshold=0.5, **options)
            return row.direction
        self.assertEqual(ROTATION_ORIENTATION, {0: "identity", 90: "rotate_90_cw", 180: "rotate_180",
                                                270: "rotate_90_ccw"})
        for rotation, orientation in ROTATION_ORIENTATION.items():
            mapping = Mapping((64, 48), (0, 0, 64, 48), (64, 48), rotation, (64, 48))
            width, height = mapping.input_size
            for box in ((1, 1, 9, 9), (width - 10, 1, width - 1, 9), (1, height - 10, 9, height - 1)):  # Three corners.
                seen = arrow(box, (height, width))  # Where the detector saw it, in its own input.
                mapped = mapping.to_frame(box)
                with self.subTest(rotation=rotation, box=box):
                    self.assertEqual(arrow(mapped, (48, 64), orientation=orientation), seen)
                    if rotation:
                        self.assertNotEqual(arrow(mapped, (48, 64)), seen)  # The rotation alone does not turn it.
        # Through the stage: the same detector box, the whole frame turned a quarter, with and without the pair.
        box = (("bottle", (1, 1, 9, 9)),)
        result = DetectionResult((Detection("bottle", 0.9, box[0][1]),), 1.0)
        mapping = Mapping((64, 48), (0, 0, 64, 48), (64, 48), 90, (64, 48))
        sent = {}
        for orientation in ("identity", "rotate_90_cw"):
            self.stage(ScriptedDetector([box]), limits=self.TWO, scenario="end:max_frames", region=Region(rotation=90),
                       orientation=orientation).run()
            sent[orientation] = self.queued[0]["rgba"]
            self.assertEqual(sent[orientation], self.expected_payload(result, mapping, orientation=orientation)[1])
        self.assertNotEqual(sent["identity"], sent["rotate_90_cw"])

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
                self.assertEqual(report["frames"]["acknowledged_presented"], report["frames"]["sent"])

    def test_the_real_output_start_is_refused_by_the_gate_before_anything_is_launched(self):
        import subprocess
        client = ObservedFrameClient(validation_only=False, **self.layout_options())
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
        self.assertEqual(gates.claims(), {"production_live_input_enabled": False,
                                          "production_camera_input_enabled": False, "mapping_qualified": False,
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
        import base64
        import copy
        import numpy as np
        from sightcircuit.hud import camera_stage
        report = self.stage(scenario="hello_noise").run()
        self.assertIs(camera_stage.check_report(report), report)
        text = json.dumps(report)
        self.assertLess(len(text), 16384)  # A plain run; the check's own cap leaves room for intervals and expiries.
        self.assertEqual(camera_stage.MAX_REPORT_BYTES, 40960)
        self.assertEqual(set(report), set(camera_stage.REPORT_BLOCKS))
        strings = 0
        for path, key, value in walk(report):
            with self.subTest(path=path, key=key):
                self.assertIn(type(value), (dict, list, str, int, float, bool, type(None)))
                self.assertRegex(str(key), r"\A[A-Za-z0-9_.:-]{1,72}\Z")
                if type(value) is list and value and type(value[0]) is not dict:
                    self.assertLessEqual(len(value), camera_stage.MAX_SCALAR_LIST)
                    self.assertFalse(any(type(item) is float for item in value))
                if type(value) is int and type(value) is not bool:
                    self.assertLess(abs(value), 2 ** 64)
                if type(value) is str:
                    strings += 1
                    self.assertNotRegex(value, r"\s") if key != "label" else self.assertEqual(value, "bottle")
                    self.assertLessEqual(len(value), 72)
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

        # The schema itself. Each value below is refused wherever a block would hold it, and as a new top-level field.
        tile = bytes(range(1, 251)) * (HUD_CONFIG.width_px * HUD_CONFIG.height_px * 4 // 250 + 1)
        tile = tile[:HUD_CONFIG.width_px * HUD_CONFIG.height_px * 4]
        chunks = base64.b64encode(tile).decode().replace("+", "A").replace("/", "B").replace("=", "")
        refused = {"bytes": tile, "bytearray": bytearray(tile), "memoryview": memoryview(tile),
                   "array": np.zeros((2, 2), np.uint8), "numpy_int": np.int64(5), "numpy_float": np.float32(0.5),
                   "tuple": (1, 2), "set": {1}, "long_list": list(range(camera_stage.MAX_SCALAR_LIST + 1)),
                   "tile_as_list": list(tile), "float_list": [0.5, 1.5, 2.5], "long_string": "a" * 73,
                   "prose": "two words", "newline": "a\nb", "hex_tile": tile.hex(), "nan": float("nan"),
                   "mixed": [{"a": 1}, 2], "object": object(), "path": "/tmp/helper", "relative_path": "tmp/helper",
                   "records": [{"n": index} for index in range(camera_stage.MAX_RECORD_LIST + 1)],
                   "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}},
                   "nested_bytes": {"inner": [{"pixels": tile}]},
                   # The fan-out forms: many small accepted values that together would carry a picture.
                   "one_integer": int.from_bytes(tile, "big"), "wide_integer": 2 ** 64,
                   "bytes_as_keys": {f"b{index}": value for index, value in enumerate(tile[:4096])},
                   "wide_object": {f"k{index}": 0 for index in range(camera_stage.MAX_KEYS + 1)},
                   "base64_chunks": {f"r{row}": {f"c{column}": chunks[(row * 40 + column) * 72:][:72]
                                                 for column in range(40)} for row in range(40)},
                   "base64_records": [{f"c{column}": chunks[(row * 40 + column) * 72:][:72] for column in range(40)}
                                      for row in range(75)],
                   "integer_records": [{f"v{column}": int.from_bytes(tile[:8], "big") for column in range(40)}
                                       for _ in range(200)]}
        for name, value in refused.items():
            with self.subTest(refused=name):
                inside = copy.deepcopy(report)
                inside["source"]["export"]["extra"] = value
                with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid"):
                    camera_stage.check_report(inside)
                with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid:report"):
                    camera_stage.check_report({**report, name: value})
        # The whitelist: no new top-level field, no new key in a block, no missing field, no block turned scalar.
        for candidate in ({**report, "extra": 1}, {**report, "config": {**report["config"], "note": 1}},
                          {name: value for name, value in report.items() if name != "cleanup"},
                          {**report, "labels": {**report["labels"], "pixels": 1}}, {**report, "status": {"a": 1}},
                          {**report, "frames": [1, 2]}, "report", None, [report]):
            with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid"):
                camera_stage.check_report(candidate)
        # The total size: accepted values only, yet too many of them.
        bulky = copy.deepcopy(report)
        bulky["source"]["export"]["fill"] = [{f"k{column}": "a" * 72 for column in range(40)} for _ in range(20)]
        self.assertGreater(len(json.dumps(bulky)), camera_stage.MAX_REPORT_BYTES)
        with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid:report_size"):
            camera_stage.check_report(bulky)
        # A label is a sanitised row label, under ``label`` only.
        for value in ("bad/label", "x" * 21, " padded", ""):
            labelled = copy.deepcopy(report)
            labelled["labels"]["rows"][0]["label"] = value
            with self.assertRaisesRegex(ValueError, "camera_hud_report_field_invalid"):
                camera_stage.check_report(labelled)
        accepted = copy.deepcopy(report)
        accepted["labels"]["rows"][0]["label"] = "cell phone"
        accepted["source"]["export"]["extra"] = {"code": "camera-export/2", "none": None, "flag": True, "ids": [1, 2, 3],
                                                 "records": [{"label": "cup", "n": 1.5}], "wide": 2 ** 64 - 1}
        self.assertIs(camera_stage.check_report(accepted), accepted)
        # ``report_value`` reduces an embedded report to the schema and counts what it had to withhold.
        withheld = [0]
        kept = camera_stage.report_value({"reason": "sdk_error", "error": "free text here", "note": "more free text",
                                          "pixels": tile, "path": "/tmp/helper", "list": list(range(40)), "ok": True,
                                          "floats": [0.1, 0.2], "big": 2 ** 70, "where": "/dev/video0",
                                          "nested": {"detail": "prose", "count": 3}}, withheld)
        self.assertEqual((kept, withheld[0]), ({"reason": "sdk_error", "note": None, "pixels": None, "list": None,
                                                "ok": True, "floats": None, "big": None, "where": None,
                                                "nested": {"count": 3}}, 6))

    def test_numbers_are_checked_and_made_plain_at_construction_so_the_report_cannot_fail_at_the_end(self):
        import numpy as np
        stage = self.stage(max_fps=np.int64(10), threshold=np.float32(0.5), max_frames=np.int32(3), seconds=np.float64(5))
        self.assertEqual([type(value) for value in (stage.max_fps, stage.threshold, stage.max_frames, stage.seconds)],
                         [float, float, int, float])
        report = stage.run()
        self.assertEqual((report["status"], report["reason"]), ("completed", "max_frames"))
        self.assertEqual((report["config"]["max_fps"], report["config"]["threshold"], report["config"]["max_frames"],
                          report["config"]["seconds"]), (10.0, 0.5, 3, 5.0))
        for options, reason in (({"max_fps": 0}, "camera_hud_limits_invalid"), ({"max_fps": "5"}, "camera_hud_limits_invalid"),
                                ({"max_fps": float("inf")}, "camera_hud_limits_invalid"),
                                ({"max_fps": True}, "camera_hud_limits_invalid"),
                                ({"max_frames": -1}, "camera_hud_limits_invalid"),
                                ({"max_frames": 2.5}, "camera_hud_limits_invalid"),
                                ({"max_frames": True}, "camera_hud_limits_invalid"),
                                ({"seconds": 0}, "camera_hud_limits_invalid"),
                                ({"seconds": float("nan")}, "camera_hud_limits_invalid"),
                                ({"threshold": 1.5}, "hud_threshold_invalid"), ({"threshold": None}, "hud_threshold_invalid"),
                                ({"threshold": float("nan")}, "hud_threshold_invalid")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, reason):
                self.stage(**options)

    def test_an_end_racing_a_submit_cannot_send_a_frame_after_the_final_take_down(self):
        """The stop is set while the third result's tile is being converted, after ``submit`` saw the run as live.

        The conversion waits until the watcher has ended the client, then
        returns: the frame is refused by the client's latch, counted after
        the end, and nothing follows the final hide on the pipe.
        """
        report, calls = self.run_with_the_end_during_the_third_conversion()
        stage = self.the_stage
        self.assertEqual((report["status"], report["reason"]), ("stopped", "stop_requested"))
        self.assertEqual(len(calls), 3)
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2, 3])  # The third was offered,
        self.assertEqual([fields["frame_id"] for fields in self.named("frame_sent")], [1, 2])  # and never written.
        self.assertEqual([event for event, _ in self.packets], ["frame_sent", "frame_sent", "taken_down"])
        self.assertEqual((report["results"]["results_after_end"], report["frames"]["submitted"], report["frames"]["sent"],
                          report["bridge"]["shows"], report["bridge"]["take_downs"]), (1, 2, 2, 0, 1))
        self.assertEqual([line["frame_id"] for line in self.bridge_lines if line["event"] == "frame_presented"], [1, 2])
        after = self.bridge_lines[[line["event"] for line in self.bridge_lines].index("hide_returned"):]
        self.assertEqual([line["event"] for line in after], ["hide_returned"])  # Nothing presented or shown after it.
        # The third was packed and offered, and the client refused it: it is not a result sent. The queue step
        # times two, and on the panel three boxes were selected and two sent.
        self.assertEqual([report["steps_ms"][name]["count"] for name in ("draw", "pack", "queue")], [3, 3, 2])
        if report["boxes"] is not None:
            boxes = report["boxes"]
            self.assertEqual((boxes["results_counted"], boxes["boxes_selected"], boxes["results_sent"], boxes["boxes_sent"]),
                             (3, 3, 2, 2))
        row, = report["labels"]["rows"]
        self.assertEqual(row["spells"], 1)  # One spell, ended by the hide: no second one is booked.
        # The latch itself, directly: after ``end`` the client accepts nothing and a take-down cannot be undone.
        client = stage.client
        now = time.monotonic_ns()
        self.assertFalse(client.submit_observed(99, now, now + MS, *self.smallest_frame()))
        self.assert_both_children_closed(report)

    def test_a_bridge_that_dies_stops_the_helper_at_once_even_while_the_detector_is_busy(self):
        detector = ScriptedDetector(delays={3: 3.0})  # In its third call from about 0.4 s to 3.4 s.
        stage = self.stage(detector, limits={"fps": 5, "max_seconds": 20, "max_frames": 100})
        killed = []

        def kill():
            killed.append(time.monotonic_ns())
            stage.client.process.kill()
        killer = threading.Timer(0.7, kill)
        killer.start()
        self.addCleanup(killer.cancel)
        report = stage.run()
        self.assertEqual((report["status"], report["reason"], report["end"]["bridge_ended_first"]),
                         ("error", "overlay_bridge_failed", True))
        # The stop came with the bridge's end, not with the detector's return two and a half seconds later.
        self.assertLess(report["end"]["ended_ns"] - killed[0], MARGIN_NS)
        self.assertLess(report["end"]["ended_ns"], detector.returned_ns[2] - 2_000 * MS)
        source = report["source"]
        self.assertEqual(source["status"], "stopped")
        self.assertLess(source["export"]["supervision"]["supervisor_elapsed_seconds"], 2.0)
        self.assertLessEqual(source["frames_emitted"], 6)  # About 0.7 s at 5 a second, not the 17 of 3.4 s.
        self.assertFalse(alive(source["export"]["supervision"]["worker_pid"]))
        self.assertEqual((report["cleanup"]["source_ok"], report["cleanup"]["bridge_ok"]), (True, False))

    def test_a_failure_on_the_clients_writer_thread_is_the_runs_error(self):
        stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 12})
        record = stage.client.listener

        def failing(event, **fields):
            if event == "frame_sent" and fields["frame_id"] == 3:
                raise KeyError("injected")
            record(event, **fields)
        stage.client.listener = failing
        started = time.monotonic()
        report = stage.run()
        self.assertLess(time.monotonic() - started, 2.0)  # Stopped at the failure, not at the helper's bound.
        self.assertEqual((report["status"], report["reason"], report["bridge"]["error"]),
                         ("error", "overlay_client_failed", "overlay_client_failed"))
        self.assertIn("injected", stage.client.error)
        self.assertEqual((report["cleanup"]["bridge_ok"], report["cleanup"]["ok"]), (False, False))
        self.assertLessEqual(report["results"]["results"], 4)
        self.assertLessEqual(report["results"]["results_after_end"], 1)
        self.assertLess(report["source"]["frames_emitted"], 8)
        # And a client that is not running while the run has not ended is an error from ``submit``, not a count.
        import numpy as np
        idle = self.stage()
        result = ScriptedDetector().detect(None, 0.5)
        tile = idle._tile(np.zeros((48, 64, 3), np.uint8), result)
        now = time.monotonic_ns()
        with self.assertRaisesRegex(RuntimeError, "observed_frame_client_not_running"):
            idle.submit(Processed(Frame(b"\0" * 12, "BGR3", 2, 2, 6, "arcturus_camera_left", 1, now), result, tile, 1, 1.0, 1.0))
        self.assertEqual((idle.counts["results_after_end"], idle._queued), (0, {}))

    def test_the_display_ledger_books_only_what_the_bridge_said_was_up(self):
        from sightcircuit.hud.camera_stage import MAX_SPELLS, DisplayLedger
        second = 1_000_000_000
        ledger = DisplayLedger()
        ledger.presented(("bottle",), 1 * second, 2 * second, True)            # bottle up at 1.0
        ledger.presented(("bottle", "cup"), second * 6 // 5, second * 11 // 5, True)   # replaced at 1.2: both up
        ledger.expired(second * 11 // 5 + 30_000_000)                          # the bridge's hide returned at 2.23
        ledger.shown(3 * second)                                               # a show with nothing waiting: nothing
        ledger.presented(("cup",), 4 * second, 5 * second, True)               # cup up at 4.0
        ledger.hidden(second * 9 // 2)                                         # taken down at 4.5, pixels kept
        ledger.presented(("bottle",), 6 * second, 7 * second, False)           # presented while down: not up
        ledger.shown(second * 13 // 2)                                         # shown at 6.5
        ledger.finish(20 * second)                                             # never hidden: ends at its deadline, 7.0
        ledger.presented(("ghost",), 8 * second, 8 * second, True)             # at its deadline already: never up
        ledger.presented(("late",), 9 * second, 10 * second, False)            # waiting, then expired unseen
        ledger.expired(10 * second)
        ledger.shown(11 * second)
        rows = {row["label"]: row for row in ledger.rows(0, 12 * second)}
        self.assertEqual(sorted(rows), ["bottle", "cup"])
        self.assertEqual(rows["bottle"]["intervals"], [{"start_ms": 1000.0, "end_ms": 2230.0}, {"start_ms": 6500.0, "end_ms": 7000.0}])
        self.assertEqual(rows["cup"]["intervals"], [{"start_ms": 1200.0, "end_ms": 2230.0}, {"start_ms": 4000.0, "end_ms": 4500.0}])
        self.assertEqual((rows["bottle"]["display_seconds"], rows["bottle"]["spells"]), (1.73, 2))
        self.assertEqual((rows["cup"]["display_seconds"], rows["cup"]["spells"]), (1.53, 2))
        # The longest gap counts the lead-in and the tail: bottle's is the 5 s after 7.0, cup's the 7.5 s after 4.5.
        self.assertEqual((rows["bottle"]["longest_gap_seconds"], rows["cup"]["longest_gap_seconds"]), (5.0, 7.5))
        self.assertEqual(ledger.rows(0, 7 * second)[0]["longest_gap_seconds"], 4.27)  # 2.23 to 6.5 once the tail is gone.
        self.assertEqual({row["label"]: row["longest_gap_seconds"] for row in ledger.rows(-9 * second, 7 * second)},
                         {"bottle": 10.0, "cup": 10.2})  # The lead-in.
        # A hidden tile that a show brings back before its deadline is booked again: that is what the bridge did.
        again = DisplayLedger()
        again.presented(("cup",), 0, second, True)
        again.hidden(second // 5)
        again.shown(second * 2 // 5)
        again.finish(second * 3)
        self.assertEqual(again.rows(0, second)[0]["intervals"], [{"start_ms": 0.0, "end_ms": 200.0},
                                                                 {"start_ms": 400.0, "end_ms": 1000.0}])
        # Bounded: intervals beyond the cap are counted, not kept.
        many = DisplayLedger()
        for index in range(MAX_SPELLS + 5):
            many.presented(("cup",), index * 10, index * 10 + 100, True)
            many.expired(index * 10 + 5)
        self.assertEqual((many.count, many.dropped, len(many.rows(0, 10 ** 6)[0]["intervals"])), (MAX_SPELLS, 5, MAX_SPELLS))

    def test_the_window_verdict_finds_a_contiguous_window_and_counts_leading_trailing_and_inner_gaps(self):
        from sightcircuit.hud import camera_stage
        verdict = camera_stage.window_verdict
        rule = {"window_seconds": 60, "fraction": 0.9, "max_gap_seconds": 2}
        steady = [(5.0, 70.0)]  # Named from 5 s to 70 s of a 75 s run.
        self.assertEqual(verdict(steady, 75, **rule), {"found": True, "window_start_seconds": 5.0, "named_fraction": 1.0,
                                                       "longest_gap_seconds": 0.0})
        # A deliberate out-of-view pause of 5 s in the middle: no 60 s window avoids it, so the gap rule fails,
        # though the label is up more than 90% of the best window.
        paused = [(2.0, 30.0), (35.0, 75.0)]
        result = verdict(paused, 75, **rule)
        self.assertEqual((result["found"], result["named_fraction"] > 0.9), (False, True))
        self.assertEqual(verdict(paused, 75, window_seconds=60, fraction=0.9, max_gap_seconds=5)["found"], True)
        # The same pause in a 90 s run: the window after it qualifies, and only that one.
        long_run = [(2.0, 20.0), (25.0, 90.0)]
        self.assertEqual(verdict(long_run, 90, **rule), {"found": True, "window_start_seconds": 25.0,
                                                         "named_fraction": 1.0, "longest_gap_seconds": 0.0})
        # Leading gap: the label first appears 3 s into the only window there is.
        self.assertEqual(verdict([(3.0, 60.0)], 60, **rule)["found"], False)
        self.assertEqual(verdict([(3.0, 60.0)], 60, **rule)["longest_gap_seconds"], 3.0)
        self.assertEqual(verdict([(2.0, 60.0)], 60, **rule)["found"], True)
        # Trailing gap: the label goes 3 s before the run ends.
        self.assertEqual(verdict([(0.0, 57.0)], 60, **rule)["found"], False)
        self.assertEqual(verdict([(0.0, 58.0)], 60, **rule)["found"], True)
        # Many short gaps: each within the limit, together too much.
        flicker = [(index * 2.0, index * 2.0 + 1.5) for index in range(40)]
        result = verdict(flicker, 80, **rule)
        self.assertEqual((result["found"], result["named_fraction"]), (False, 0.75))
        self.assertEqual(verdict(flicker, 80, window_seconds=60, fraction=0.75, max_gap_seconds=2)["found"], True)
        # A run shorter than the window, and a label never shown.
        self.assertEqual(verdict([(0.0, 30.0)], 30, **rule)["found"], False)
        self.assertEqual(verdict([], 75, **rule), {"found": False, "window_start_seconds": None, "named_fraction": 0.0,
                                                   "longest_gap_seconds": None})
        # The window may start anywhere: here only one starting 1.5 s before an interval satisfies both rules.
        offset = [(0.0, 10.0), (20.0, 81.5)]
        self.assertEqual(verdict(offset, 90, **rule)["found"], True)
        for arguments in (([(2, 1)], 10), ([(0, 5), (4, 6)], 10)):
            with self.assertRaisesRegex(ValueError, "camera_hud_window_intervals_invalid"):
                verdict(*arguments, **rule)
        for bad in ({"window_seconds": 0}, {"fraction": 0}, {"fraction": 1.1}, {"max_gap_seconds": -1}, {"window_seconds": True}):
            with self.assertRaisesRegex(ValueError, "camera_hud_window_arguments_invalid"):
                verdict(steady, 75, **{**rule, **bad})
        # From a report: the run's own start and end bound the windows, and a report that dropped intervals is void.
        report = self.stage(ScriptedDetector([BOTTLE, BOTTLE, (), (), BOTTLE])).run()
        run_seconds = (report["labels"]["run_end_ns"] - report["labels"]["run_start_ns"]) / 1e9
        named = camera_stage.named_in_window(report, "bottle", window_seconds=0.3, fraction=0.9, max_gap_seconds=0.1)
        self.assertEqual((named["found"], named["named_fraction"] >= 0.9), (True, True))
        whole = camera_stage.named_in_window(report, "bottle", window_seconds=round(run_seconds - 0.05, 3), fraction=0.5,
                                             max_gap_seconds=0.1)
        self.assertEqual(whole["found"], False)  # The take-down for the two empty results is a gap of about 0.4 s.
        self.assertGreater(whole["longest_gap_seconds"], 0.3)
        self.assertEqual(camera_stage.named_in_window(report, "cup", window_seconds=0.3, fraction=0.5,
                                                      max_gap_seconds=1)["found"], False)
        report["labels"]["spells_dropped"] = 1
        with self.assertRaisesRegex(ValueError, "camera_hud_window_intervals_incomplete"):
            camera_stage.named_in_window(report, "bottle", window_seconds=0.3, fraction=0.9, max_gap_seconds=0.1)


@unittest.skipUnless(RUNNABLE and fake_vrclient.available(),
                     "Needs Linux, NumPy, OpenCV, scripts/build_overlay_bridge.sh, its SDK checkout and g++")
class FixedHudStageTests(StageCase):
    """The stage on the bridge's ``--fixed-hud`` mode against the fake runtime: what the bridge calls, and when.

    The two permission functions (real output, and camera input to a real
    display) are patched to permit for these tests alone, which is how the
    fixture is reached; without the patches the same client is refused
    (``CameraHudStageTests``, ``tests/test_scope_record.py``). Nothing here
    is SteamVR or a display.
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
        from sightcircuit.hud import camera_stage
        camera = mock.patch.object(camera_stage, "camera_display_input_permitted", return_value=True)
        self.camera_permitted = camera.start()
        self.addCleanup(camera.stop)

    def client(self, keepalive_seconds=observed_frames.KEEPALIVE_SECONDS, **fake):
        FixedHudStageTests.runs += 1
        self.log = Path(self.directory.name) / f"calls-{FixedHudStageTests.runs}.jsonl"
        return ObservedFrameClient(validation_only=False, keepalive_seconds=keepalive_seconds, **self.layout_options(),
                                   env={**os.environ, **self.runtime_env, "FAKE_VR_LOG": str(self.log), **fake})

    def visible_spans(self):
        """From the fake's log: the ``[start_ns, end_ns]`` spans in which the overlay was visible."""
        spans, since = [], None
        for call in self.calls():
            if call.get("visible") and since is None:
                since = call["end_ns"]
            elif not call.get("visible") and since is not None:
                spans.append([since, call["end_ns"]])
                since = None
        self.assertIsNone(since)
        return spans

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
        self.assertEqual(final["fixed_hud"]["config_id"], self.LAYOUT.layout_id)
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
        self.permitted.assert_called_with(self.the_stage.client, self.the_stage.client.layout)  # Its layout value.
        self.assertEqual([item["frame_id"] for item in self.queued], [1, 2, 5, 6, 7, 8])
        sent = self.named("frame_sent")
        self.assertEqual(len(self.calls("SetOverlayRaw")), len(sent))
        self.assertEqual((report["frames"]["acknowledged_presented"], report["frames"]["sent"]), (len(sent), len(sent)))
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


    def test_the_ledger_agrees_with_the_fake_runtimes_visible_time_when_uploads_are_slow(self):
        """A steady label at 5 a second for 5 s, with every upload stalled in the runtime.

        At 1,100 ms every frame outlives its deadline in the upload: the
        bridge shows none, says so per frame, and the ledger books nothing.
        At 600 ms about every third frame is shown for the rest of its
        second: the ledger's time is the fake's visible time, not the time
        from each pipe write.
        """
        limits = {"fps": 5, "max_seconds": 5, "max_frames": 25}
        for delay_ms in (1100, 600):
            with self.subTest(upload_delay_ms=delay_ms):
                client = self.client(FAKE_VR_SetOverlayRaw_DELAY_MS=",".join([str(delay_ms)] * 60))
                report = self.stage(limits=limits, client=client).run()
                self.assertEqual(report["status"], "completed")
                visible = sum(end - start for start, end in self.visible_spans()) / 1e9
                booked = sum(row["display_seconds"] for row in report["labels"]["rows"])
                frames, sent = report["frames"], len(self.named("frame_sent"))
                self.assertEqual(frames["acknowledged_presented"] + frames["acknowledged_refused"] + frames["unacknowledged"],
                                 sent)
                self.assertLessEqual(frames["unacknowledged"], 1)  # At most the frame the stop packet overtook.
                self.assertEqual(frames["acknowledged_presented"], len(self.calls("ShowOverlay")))
                self.assertEqual(report["timings"]["capture_to_display_age_ms"]["count"], frames["acknowledged_presented"])
                self.assertAlmostEqual(booked, visible, delta=0.1)
                if delay_ms == 1100:
                    self.assertEqual((visible, booked, report["labels"]["rows"], frames["acknowledged_presented"]),
                                     (0.0, 0, [], 0))
                    self.assertGreaterEqual(frames["refused_reasons"]["expired_before_show"], 3)
                    self.assertEqual(frames["refused_reasons"]["expired_before_show"], frames["expired_in_upload"])
                    # The bridge's own count of frames taken is not a count of frames presented, and is named so.
                    self.assertEqual(frames["bridge_accepted"], frames["expired_in_upload"])
                else:
                    self.assertGreater(visible, 0.4)
                    self.assertGreaterEqual(report["timings"]["capture_to_display_age_ms"]["p50"], delay_ms)
                    row, = report["labels"]["rows"]
                    spans = self.visible_spans()
                    self.assertEqual(len(row["intervals"]), len(spans))
                    start = report["labels"]["run_start_ns"]
                    for interval, (shown, hidden) in zip(row["intervals"], spans):
                        self.assertAlmostEqual(interval["start_ms"], (shown - start) / 1e6, delta=5)
                        self.assertAlmostEqual(interval["end_ms"], (hidden - start) / 1e6, delta=5)
                self.assert_retired(report)
                self.assert_both_children_closed(report)

    def test_the_end_of_run_hide_waits_behind_a_frame_already_being_written_and_the_report_measures_it(self):
        # Uploads of 600 ms: a stop that lands while a frame is in the pipe finds the bridge inside SetOverlayRaw,
        # and the hide, a packet behind that frame, is acted on only when the upload has returned.
        client = self.client(FAKE_VR_SetOverlayRaw_DELAY_MS=",".join(["600"] * 60))
        stop = threading.Event()
        stage = self.stage(limits={"fps": 5, "max_seconds": 20, "max_frames": 100}, client=client)
        timer = threading.Timer(2.05, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        report = stage.run(stop)
        end = report["end"]
        self.assertEqual((report["status"], end["tile_already_down"], end["bridge_ended_first"]), ("stopped", False, False))
        down = self.named("taken_down")[-1]
        self.assertEqual(end["hide_written_ns"], down["sent_ns"])
        self.assertGreaterEqual(end["hide_written_after_end_ms"], 0)
        self.assertGreaterEqual(end["bridge_hide_after_end_ms"], end["hide_written_after_end_ms"])
        # Bounded by one frame's write and the uploads the bridge still had to finish, never open-ended.
        self.assertLess(end["bridge_hide_after_end_ms"], 2 * 600 + MARGIN_NS / 1e6)
        hide = [call for call in self.calls("HideOverlay") if call["end_ns"] <= end["bridge_hide_returned_ns"]][-1]
        self.assertLess(end["bridge_hide_returned_ns"] - hide["end_ns"], 5 * MS)
        # No frame was accepted after the end: at most the one already in flight was uploaded behind it.
        late = [call for call in self.calls("SetOverlayRaw") if call["start_ns"] > end["ended_ns"]]
        self.assertLessEqual(len(late), 1)
        self.assertEqual(self.calls("SetOverlayRaw", "ShowOverlay")[-1]["end_ns"] < hide["start_ns"], True)
        self.assert_retired(report)
        self.assert_both_children_closed(report)

    def test_an_end_racing_a_submit_uploads_and_shows_nothing_after_the_final_hide(self):
        report, calls = self.run_with_the_end_during_the_third_conversion()
        self.assertEqual((report["status"], len(calls), report["results"]["results_after_end"]), ("stopped", 3, 1))
        self.assertEqual([fields["frame_id"] for fields in self.named("frame_sent")], [1, 2])
        end = report["end"]
        hide = next(call for call in self.calls("HideOverlay") if call["start_ns"] >= end["hide_written_ns"] - MS)
        self.assertLess(hide["start_ns"] - end["ended_ns"], MARGIN_NS)
        self.assertEqual([call["call"] for call in self.calls("SetOverlayRaw", "ShowOverlay")
                          if call["start_ns"] > hide["start_ns"]], [])
        self.assertEqual(len(self.calls("SetOverlayRaw")), 2)
        row, = report["labels"]["rows"]
        self.assertEqual(row["spells"], 1)
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)

    def test_keepalives_on_a_live_tile_and_after_its_expiry_neither_extend_nor_revive_it(self):
        # A detector stalled for 1.8 s with a keepalive every 0.3 s: several keepalives land while the first tile
        # is up, and several after the bridge has hidden it at its deadline.
        detector = ScriptedDetector(delays={2: 1.8})
        report = self.stage(detector, client=self.client(keepalive_seconds=0.3)).run()
        self.assertEqual(report["status"], "completed")
        self.assertGreaterEqual(report["bridge"]["keepalives"], 5)
        event = report["frame_expired"]["events"][0]
        first = self.queued[0]
        self.assertEqual((event["frame_id"], event["deadline_ns"], event["outcome"], event["hide_ok"]),
                         (1, first["expires_ns"], "hidden", True))
        # The deadline held: hidden within one poll of the observation's second, whatever the keepalives did.
        self.assertLess(event["hide_returned_after_deadline_ms"], (BRIDGE_INPUT_POLL_NS + MARGIN_NS) / 1e6)
        returned = [line for line in self.bridge_lines if line["event"] == "show_returned"
                    and line["returned_ns"] < detector.returned_ns[1]]
        live = [line for line in returned if line["returned_ns"] < event["hide_called_ns"]]
        after = [line for line in returned if line["returned_ns"] > event["hide_returned_ns"]]
        self.assertGreaterEqual(len(live), 2)
        self.assertGreaterEqual(len(after), 2)
        self.assertEqual({(line["ok"], line["call"]) for line in live}, {(True, "ShowOverlay")})
        self.assertEqual({(line["ok"], line["call"], line["reason"]) for line in after}, {(False, "HideOverlay", "no_image")})
        # Nothing was uploaded or shown between the expiry and the next frame, and the label's spell ends there.
        following = min(fields["sent_ns"] for fields in self.named("frame_sent") if fields["frame_id"] > 1)
        self.assertEqual([call["call"] for call in self.calls("SetOverlayRaw", "ShowOverlay")
                          if event["hide_returned_ns"] < call["start_ns"] < following - MS], [])
        row, = report["labels"]["rows"]
        self.assertAlmostEqual(row["intervals"][0]["end_ms"],
                               (event["hide_returned_ns"] - report["labels"]["run_start_ns"]) / 1e6, places=2)
        spans = self.visible_spans()
        self.assertAlmostEqual(spans[0][1], event["hide_returned_ns"], delta=5 * MS)
        self.assertAlmostEqual(sum(row["display_seconds"] for row in report["labels"]["rows"]),
                               sum(end - start for start, end in spans) / 1e9, delta=0.1)
        self.assert_nothing_shown_past_its_deadline(report)
        self.assert_retired(report)
        self.assert_both_children_closed(report)


if __name__ == "__main__":
    unittest.main()
