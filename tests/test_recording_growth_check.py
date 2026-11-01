"""Growing-file diagnostics using generated MP4 headers and guarded payloads."""
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from framejacker.runtime import guard as bounded_worker
from scripts import recording_growth_check as growth


def box(kind, payload=b"", size=None):
    return struct.pack(">I4s", len(payload) + 8 if size is None else size, kind) + payload


def fragment(number):
    return box(b"moof", box(b"mfhd", struct.pack(">II", 0, number))) + box(b"mdat", b"private-video" * 10)


class RecordingGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-growth-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "recording.mp4"
        self.path.write_bytes(box(b"ftyp", b"isom") + box(b"moov", b"metadata") + fragment(1))
        self.now = 0.0

    def observe(self, pause=None, **kwargs):
        def step(seconds):
            self.now += seconds
            if pause:
                pause()
        return growth.observe(self.path, seconds=0.65, clock=lambda: self.now, pause=step, **kwargs)

    def test_default_does_not_open_any_input(self):
        with patch.object(growth, "observe") as observe, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(growth.main([]), 3)
        observe.assert_not_called()

    def test_repeated_fragment_growth_is_reported_without_claiming_live_decode_safety(self):
        number = [1]
        def append():
            number[0] += 1
            with self.path.open("ab") as stream:
                stream.write(fragment(number[0]))
        report = self.observe(append)
        self.assertEqual(report["status"], "fragment_growth_observed")
        self.assertGreaterEqual(report["fragment_growth_observations"], 2)
        self.assertFalse(report["media_payload_read"])
        self.assertFalse(report["payload_publication_verified"])
        self.assertFalse(report["live_source_verified"])
        self.assertFalse(report["feedback_exclusion_verified"])
        self.assertEqual(report["images_decoded"], 0)

    def test_finished_clip_and_one_bulk_flush_do_not_pass_growth_check(self):
        self.assertEqual(self.observe()["status"], "insufficient_fragment_growth")
        self.now = 0
        first = [True]
        def once():
            if first[0]:
                first[0] = False
                with self.path.open("ab") as stream:
                    stream.write(fragment(2) + fragment(3))
        report = self.observe(once)
        self.assertEqual(report["new_fragment_pairs"], 2)
        self.assertEqual(report["fragment_growth_observations"], 1)
        self.assertEqual(report["status"], "insufficient_fragment_growth")

    def test_all_box_payloads_are_skipped_including_media_and_pose_metadata(self):
        data = self.path.read_bytes()
        ranges, offset = [], 0
        while offset < len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            ranges.append((offset + 8, offset + length))
            offset += length
        class HeaderOnly(io.BytesIO):
            def read(self, count=-1):
                start = self.tell()
                if count < 0 or any(start < end and start + count > begin for begin, end in ranges):
                    raise AssertionError("Payload read attempted")
                return super().read(count)
        report = growth.scan_headers(HeaderOnly(data), len(data), growth.Budget())
        self.assertEqual(len(report["pairs"]), 1)

    def test_partial_headers_extents_and_open_ended_media_remain_pending(self):
        for data, expected in ((b"123", "partial_header"),
                (struct.pack(">I4s", 1, b"mdat") + b"123", "partial_extended_header"),
                (box(b"mdat", b"partial", size=1000), "incomplete_box_extent"),
                (box(b"mdat", b"payload", size=0), "open_ended_box")):
            with self.subTest(expected=expected):
                report = growth.scan_headers(io.BytesIO(data), len(data), growth.Budget())
                self.assertEqual(report["pending"], expected)
                self.assertEqual(report["pairs"], [])

    def test_partial_fragment_can_finish_on_later_observation(self):
        data = fragment(2)
        with self.path.open("ab") as stream:
            stream.write(data[:5])
        once = [True]
        def finish():
            if once[0]:
                once[0] = False
                with self.path.open("ab") as stream:
                    stream.write(data[5:])
        report = self.observe(finish)
        self.assertEqual(report["new_fragment_pairs"], 1)
        self.assertEqual(report["status"], "insufficient_fragment_growth")

    @unittest.skipIf(os.name == "nt", "Windows denies replacing an open file; exercised on Linux")
    def test_file_replacement_is_rejected(self):
        def replace():
            replacement = self.path.with_suffix(".new")
            replacement.write_bytes(self.path.read_bytes())
            os.replace(replacement, self.path)
        self.assertIn("file_replaced", self.observe(replace)["reason"])

    def test_file_truncation_is_rejected(self):
        report = self.observe(lambda: self.path.write_bytes(b""))
        self.assertIn("shrank", report["reason"])

    def test_backpatch_to_previously_bounded_box_is_not_silently_accepted(self):
        def change():
            with self.path.open("r+b") as stream:
                stream.write(struct.pack(">I", 8))
        self.assertEqual(self.observe(change)["status"], "error")

    def test_budget_malformed_boxes_and_size_limits(self):
        for data in (box(b"free", size=4),):
            with self.assertRaises(growth.GrowthError):
                growth.scan_headers(io.BytesIO(data), len(data), growth.Budget())
        with self.assertRaisesRegex(growth.GrowthError, "file_size"):
            growth.scan_headers(io.BytesIO(), growth.MAX_FILE_BYTES + 1, growth.Budget())
        report = self.observe(budget=growth.Budget(8))
        self.assertIn("budget", report["reason"])
        self.assertLessEqual(report["header_bytes_read"], 8)

    def test_extended_box_headers_and_missing_initial_movie(self):
        data = struct.pack(">I4sQ", 1, b"moov", 16) + fragment(1)
        self.assertEqual(len(growth.scan_headers(io.BytesIO(data), len(data), growth.Budget())["pairs"]), 1)
        data = fragment(1)
        self.assertEqual(growth.scan_headers(io.BytesIO(data), len(data), growth.Budget())["pairs"], [])

    def test_nonregular_file_is_rejected_without_opening(self):
        with patch.object(growth.os, "open", side_effect=AssertionError("Must not open")):
            report = growth.observe(self.path.parent, seconds=0.1)
        self.assertEqual(report["status"], "error")

    def test_cancel_closes_file_and_keeps_payload_gate_disabled(self):
        stop = threading.Event()
        report = self.observe(stop.set, stop=stop)
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["image_access_enabled"])
        self.path.unlink()  # Especially useful on Windows, where an open file prevents deletion.

    def test_watch_selects_only_a_new_recording_and_times_out_without_one(self):
        old = self.path.with_name("old_stereo.mp4")
        old.write_bytes(self.path.read_bytes())
        created = [False]
        def pause(seconds):
            self.now += seconds
            if not created[0]:
                created[0] = True
                old.with_name("new_stereo.mp4").write_bytes(self.path.read_bytes())
        with patch.object(growth, "observe", return_value=growth.new_report()) as observe:
            result = growth.watch_new(self.path.parent, wait_seconds=0.5, seconds=1,
                                      clock=lambda: self.now, pause=pause)
        self.assertEqual(result["selected_name"], "new_stereo.mp4")
        self.assertEqual(observe.call_args.args[0].name, "new_stereo.mp4")
        self.now = 0
        def tick(seconds):
            self.now += seconds
        with patch.object(growth, "observe") as observe:
            result = growth.watch_new(self.path.parent, wait_seconds=0.5, seconds=1,
                                      clock=lambda: self.now, pause=tick)
        self.assertEqual(result["status"], "no_new_recording")
        observe.assert_not_called()

    def test_watch_rejects_ambiguous_new_recordings(self):
        def pause(seconds):
            self.now += seconds
            for name in ("one_stereo.mp4", "two_stereo.mp4"):
                self.path.with_name(name).write_bytes(b"")
        with patch.object(growth, "observe") as observe:
            report = growth.watch_new(self.path.parent, wait_seconds=0.5, seconds=1,
                                      clock=lambda: self.now, pause=pause)
        self.assertIn("multiple_new_recordings", report["reason"])
        observe.assert_not_called()

    def test_wait_defaults_and_accepted_boundaries_use_only_virtual_time(self):
        for wait in (None, 1, 30, 31, 120):
            with self.subTest(wait=wait):
                self.now = 0
                def tick(seconds):
                    self.now = round(self.now + seconds, 6)
                options = {} if wait is None else {"wait_seconds": wait}
                with patch.object(growth, "recording_names", return_value=set()), patch.object(growth, "observe") as observe:
                    report = growth.watch_new(self.path.parent, clock=lambda: self.now, pause=tick, **options)
                self.assertEqual(self.now, 15 if wait is None else wait)
                self.assertEqual(report["status"], "no_new_recording")
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["image_access_enabled"])
                observe.assert_not_called()

    def test_wait_and_observation_limits_remain_separate(self):
        with patch.object(growth, "recording_names") as scanned:
            for wait in (0, -1, 120.001, 121, float("inf"), float("nan")):
                with self.subTest(wait=wait), self.assertRaises(growth.GrowthError):
                    growth.watch_new(self.path.parent, wait_seconds=wait)
            for seconds in (0, 30.001, 31):
                with self.subTest(seconds=seconds), self.assertRaises(growth.GrowthError):
                    growth.watch_new(self.path.parent, wait_seconds=120, seconds=seconds)
            scanned.assert_not_called()
        with patch.object(growth, "supervise") as supervised, contextlib.redirect_stderr(io.StringIO()):
            for option, value in (("--wait-for-new", "0"), ("--wait-for-new", "121"),
                                  ("--wait-for-new", "1.5"), ("--seconds", "31")):
                with self.subTest(option=option, value=value), self.assertRaises(SystemExit) as error:
                    growth.main(["--watch-directory", str(self.path.parent), option, value])
                self.assertEqual(error.exception.code, 2)
            supervised.assert_not_called()

    def test_new_file_must_be_selected_strictly_before_either_wait_deadline(self):
        for wait, arrival in ((30, 29.999), (30, 30), (30, 31),
                              (120, 31), (120, 119.999), (120, 120), (120, 120.001)):
            with self.subTest(wait=wait, arrival=arrival):
                self.now = 0
                calls = 0
                def scan(_):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return {"old_stereo.mp4"}
                    self.now = arrival
                    return {"old_stereo.mp4", "new_stereo.mp4"}
                with patch.object(growth, "recording_names", side_effect=scan), \
                        patch.object(growth, "observe", return_value=growth.new_report()) as observe:
                    report = growth.watch_new(self.path.parent, wait_seconds=wait, clock=lambda: self.now)
                if arrival < wait:
                    self.assertEqual(observe.call_args.args[0].name, "new_stereo.mp4")
                    self.assertEqual(observe.call_args.args[1], 15)  # Full, separately bounded observation.
                    self.assertEqual(report["waited_seconds"], arrival)
                else:
                    observe.assert_not_called()
                    self.assertEqual(report["status"], "no_new_recording")

    def test_cli_forwarding_and_guardian_budgets_are_derived_without_real_waits(self):
        cases = (([], 35, 43, 53.2), (["--wait-for-new", "1"], 21, 29, 39.2),
                 (["--wait-for-new", "30"], 50, 58, 68.2),
                 (["--wait-for-new", "31"], 51, 59, 69.2),
                 (["--wait-for-new", "120"], 140, 148, 158.2),
                 (["--wait-for-new", "120", "--seconds", "30"], 155, 163, 173.2))
        for extra, worker_budget, outer_budget, emergency_budget in cases:
            with self.subTest(extra=extra):
                guarded = {"result": growth.new_report(), "cleanup_ok": True}
                with patch.object(bounded_worker.sys, "platform", "linux"), \
                        patch.object(bounded_worker, "_supervise_owned", return_value=guarded) as owned, \
                        contextlib.redirect_stdout(io.StringIO()):
                    growth.main(["--watch-directory", str(self.path.parent), *extra])
                call = owned.call_args
                options = json.loads(call.args[0][-1])
                self.assertEqual(options["timeout"], worker_budget)
                self.assertEqual(call.args[2], outer_budget)
                command = options["command"]
                self.assertEqual(command[command.index("--wait-for-new") + 1],
                                 extra[1] if extra else "15")
                self.assertEqual(command[command.index("--seconds") + 1],
                                 "30" if "--seconds" in extra else "15")
                # Run the actual emergency path against a non-exiting fake
                # process and virtual clock. No signals, subprocess or wall wait.
                self.assertAlmostEqual(self.emergency_elapsed(outer_budget, call.kwargs["cleanup_grace"]),
                                       emergency_budget)

    def test_selection_near_wait_deadline_gets_full_separate_observation(self):
        for wait in (30, 120):
            with self.subTest(wait=wait):
                self.now = 0
                calls = 0
                def scan(_):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return set()
                    self.path.with_name("new_stereo.mp4").write_bytes(self.path.read_bytes())
                    self.now = wait - 0.1
                    return {"new_stereo.mp4"}
                def tick(seconds):
                    self.now = round(self.now + seconds, 6)
                with patch.object(growth, "recording_names", side_effect=scan):
                    report = growth.watch_new(self.path.parent, wait_seconds=wait, seconds=15,
                        clock=lambda: self.now, pause=tick)
                self.assertAlmostEqual(self.now, wait - 0.1 + 15)
                self.assertEqual(report["seconds_requested"], 15)
                self.assertEqual(report["status"], "insufficient_fragment_growth")
                self.assertGreater(report["observations"], 1)
                self.assertTrue(report["cleanup_ok"])
                self.assertFalse(report["media_payload_read"])

    def emergency_elapsed(self, timeout, grace):
        self.now = 0
        def tick(seconds):
            self.now = round(self.now + seconds, 6)
        process = MagicMock(pid=12345, returncode=None)
        process.poll.return_value = None
        def wait(*, timeout):
            tick(timeout)
            raise bounded_worker.subprocess.TimeoutExpired("local-fake-worker", timeout)
        process.wait.side_effect = wait
        reader = MagicMock()
        reader.is_alive.return_value = True
        reader.join.side_effect = lambda *, timeout: tick(timeout)
        done = MagicMock()
        done.is_set.return_value = False
        done.wait.side_effect = tick
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (bounded_worker, "cancellation", lambda: contextlib.nullcontext([])),
                (bounded_worker, "child_reaper", contextlib.nullcontext),
                (bounded_worker.time, "monotonic", lambda: self.now),
                (bounded_worker.time, "sleep", tick),
                (bounded_worker.subprocess, "Popen", lambda *a, **k: process),
                (bounded_worker.threading, "Thread", lambda **k: reader),
                (bounded_worker.threading, "Event", lambda: done),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.object(bounded_worker.os, "killpg", create=True))
            report = bounded_worker._supervise_owned(["local-fake-worker"], growth.new_report(),
                timeout, growth.validate_worker_report, cleanup_grace=grace)
        self.assertEqual(report["status"], "timed_out")
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["worker_reaped"])
        return report["supervisor_elapsed_seconds"]

    def test_wait_option_does_not_extend_explicit_observation_budget(self):
        with patch.object(growth, "supervise", return_value=growth.new_report()) as supervise, \
                contextlib.redirect_stdout(io.StringIO()):
            growth.main(["--observe", str(self.path), "--wait-for-new", "120"])
        self.assertEqual(supervise.call_args.args[2], 20)
        self.assertNotIn("--wait-for-new", supervise.call_args.args[0])

    def test_preexisting_cancellation_does_not_open_or_scan_input(self):
        stop = threading.Event()
        stop.set()
        with patch.object(growth.os, "open") as opened, patch.object(growth, "recording_names") as scanned:
            self.assertEqual(growth.observe(self.path, 1, stop)["status"], "cancelled")
            self.assertEqual(growth.watch_new(self.path.parent, 1, 1, stop)["status"], "cancelled")
        opened.assert_not_called()
        scanned.assert_not_called()

    def test_close_failure_cannot_claim_clean_reader_shutdown(self):
        real_fdopen = os.fdopen
        class CloseFailure:
            def __init__(self, stream):
                self.stream = stream
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def close(self):
                self.stream.close()
                raise OSError("close failed")
        with patch.object(growth.os, "fdopen", side_effect=lambda *a, **k: CloseFailure(real_fdopen(*a, **k))):
            report = self.observe()
        self.assertFalse(report["cleanup_ok"])
        self.assertEqual(report["status"], "cleanup_failed")

    def test_directory_scan_finishing_after_deadline_does_not_select_file(self):
        def scan(_):
            self.now += 1
            return set() if self.now == 1 else {"late_stereo.mp4"}
        with patch.object(growth, "recording_names", side_effect=scan), patch.object(growth, "observe") as observe:
            report = growth.watch_new(self.path.parent, 0.5, 1, clock=lambda: self.now)
        observe.assert_not_called()
        self.assertEqual(report["status"], "no_new_recording")

    def test_header_scan_finishing_after_deadline_is_not_counted(self):
        original = growth.scan_headers
        def slow(*args):
            result = original(*args)
            self.now += 1
            return result
        with patch.object(growth, "scan_headers", side_effect=slow):
            report = self.observe()
        self.assertEqual(report["observations"], 0)
        self.assertEqual(report["status"], "insufficient_fragment_growth")

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV required for generated HEVC fragment test")
    def test_real_encoded_fragments_are_recognized_without_reading_payloads(self):
        import av
        encoded = self.path.with_name("encoded.mp4")
        with av.open(str(encoded), mode="w", format="mp4", options={"movflags": "frag_keyframe+empty_moov+default_base_moof"}) as output:
            stream = output.add_stream("libx265", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
            stream.options = {"preset": "ultrafast", "tune": "zerolatency",
                "x265-params": "log-level=error:pools=none:frame-threads=1:keyint=2:min-keyint=2:scenecut=0:bframes=0"}
            for index in range(8):
                frame = av.VideoFrame(64, 64, "yuv420p")
                for plane in frame.planes:
                    plane.update(bytes([120 + index]) * plane.buffer_size)
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        data = encoded.read_bytes()
        scanned = growth.scan_headers(io.BytesIO(data), len(data), growth.Budget())
        self.assertGreaterEqual(len(scanned["pairs"]), 3)
        pairs = list(scanned["pairs"])
        self.path.write_bytes(data[:pairs[0][0]])
        def append():
            if pairs:
                start, end = pairs.pop(0)
                with self.path.open("ab") as destination:
                    destination.write(data[start:end])
        report = self.observe(append)
        self.assertEqual(report["status"], "fragment_growth_observed")
        self.assertFalse(report["media_payload_read"])
        self.assertFalse(report["payload_publication_verified"])
