"""Operator marks and the counts-only summaries (``sightcircuit.camera.marks``, work-order item L15).

Standard library only: the vocabulary and the cue grammar, the clock, the
cue schedule and the key marks of ``MarkSession`` (Enter presses through a
pseudo-terminal), and ``summarize`` on scripted frame and mark sequences. No
helper, no detector, no camera.
"""
import io
import os
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import marks  # noqa: E402

SECOND = 1_000_000_000
MS = 1_000_000
ORIGIN = 50 * SECOND


class Box:
    """A detection as the detectors give it: a label, a confidence and a pixel box."""

    def __init__(self, label, confidence, box):
        self.label, self.confidence, self.box_xyxy = label, confidence, box


def frame(at_ms, cell_centre=None, *, width=100, height=100):
    """A per-frame record acquired ``at_ms`` after the origin; with the target centred at ``cell_centre`` or absent."""
    detections = []
    if cell_centre is not None:
        x, y = cell_centre
        detections.append(Box("bottle", 0.8, (x - 5, y - 5, x + 5, y + 5)))
    return {"observed_ns": ORIGIN + at_ms * MS, "target": marks.target_of(detections, "bottle", 0.5, width, height)}


def mark(index, label, at_ms, source="cue", late_ns=None):
    """A mark at ``at_ms``; with ``late_ns`` a cue scheduled then and shown that much later."""
    entry = {"index": index, "source": source, "label": label, "monotonic_ns": ORIGIN + at_ms * MS + (late_ns or 0)}
    if late_ns is not None:
        entry["scheduled_monotonic_ns"] = ORIGIN + at_ms * MS
    return entry


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_is_closed_and_is_the_work_orders(self):
        self.assertEqual(marks.VOCABULARY, ("remove", "reseat", "near", "far", "left", "centre", "right", "up", "down",
                                            "out_of_view", "in_view"))
        self.assertEqual(marks.KEY_LABEL, "mark")
        self.assertNotIn(marks.KEY_LABEL, marks.VOCABULARY)
        for group in (marks.IN_VIEW_LABELS, marks.OUT_OF_VIEW_LABELS, marks.POSITION_LABELS):
            self.assertLessEqual(set(group), set(marks.VOCABULARY))
        self.assertFalse(set(marks.IN_VIEW_LABELS) & set(marks.OUT_OF_VIEW_LABELS))

    def test_a_cue_is_seconds_and_a_vocabulary_label_within_the_run(self):
        self.assertEqual(marks.parse_cue("5:near", 30), (5000, "near"))
        self.assertEqual(marks.parse_cue("7.5:left", 30), (7500, "left"))
        self.assertEqual(marks.parse_cue("0:in_view", 30), (0, "in_view"))
        self.assertEqual(marks.parse_cue("30:remove", 30), (30000, "remove"))
        for text in ("near", "5", "5:", ":near", "5:bottle", "5:mark", "5:Near", "5:near ", "-1:near", "31:near",
                     "30.001:near", "5:near:far", "5s:near", "5:the bottle is here", "1e1:near", "5.0000:near", "", None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                marks.parse_cue(text, 30)

    def test_cues_rise_strictly_and_labels_come_from_the_vocabulary(self):
        self.assertEqual(marks.check_cues([(0, "near"), (500, "far")]), ((0, "near"), (500, "far")))
        for cues in ([(500, "near"), (500, "far")], [(900, "near"), (500, "far")], [(-1, "near")], [(0, "note")],
                     [(0, "mark")], [(index, "near") for index in range(marks.MAX_CUES + 1)]):
            with self.subTest(cues=cues[:2]), self.assertRaises(ValueError):
                marks.check_cues(cues)
        self.assertEqual(marks.check_mark_labels(["near", "near", "remove"]), ("near", "near", "remove"))
        for labels in (["bottle"], ["mark"], [""], [5], ["near"] * (marks.MAX_MARK_LABELS + 1)):
            with self.subTest(labels=labels[:2]), self.assertRaises(ValueError):
                marks.check_mark_labels(labels)


class ClockTests(unittest.TestCase):
    def test_wall_clock_times_are_the_anchor_plus_a_monotonic_difference(self):
        clock = marks.Clock(monotonic_ns=1_000, utc_ns=1_700_000_000_000_000_000)
        self.assertEqual(clock.utc(1_500), 1_700_000_000_000_000_500)
        self.assertEqual(clock.utc(400), 1_700_000_000_000_000_000 - 600)
        self.assertEqual(clock.stamp(1_500), {"monotonic_ns": 1_500, "utc_ns": 1_700_000_000_000_000_500})

    def test_the_clock_reads_the_host_clocks_and_declares_the_timebase(self):
        before = time.time_ns(), time.monotonic_ns()
        clock = marks.Clock()
        after = time.time_ns(), time.monotonic_ns()
        self.assertTrue(before[0] <= clock.utc_ns <= after[0] and before[1] <= clock.monotonic_ns <= after[1])
        report = clock.report()
        self.assertEqual({name: report[name] for name in marks.TIMEBASE}, marks.TIMEBASE)
        self.assertEqual(report["anchor"], {"monotonic_ns": clock.monotonic_ns, "utc_ns": clock.utc_ns})
        self.assertGreaterEqual(report["anchor_at_report"]["monotonic_ns"], clock.monotonic_ns)
        self.assertLess(abs(report["wall_clock_step_ns"]), SECOND)  # No clock step during a test.


class CellTests(unittest.TestCase):
    def test_the_cell_is_the_box_centre_against_0_4_and_0_6_on_each_axis(self):
        self.assertEqual(marks.CELL_THRESHOLDS, (0.4, 0.6))
        self.assertEqual(marks.CELLS, ("up_left", "up", "up_right", "left", "centre", "right",
                                       "down_left", "down", "down_right"))
        cases = (((0, 0, 20, 20), "left", "up", "up_left"), ((40, 0, 60, 20), "centre", "up", "up"),
                 ((80, 40, 100, 60), "right", "centre", "right"), ((40, 40, 60, 60), "centre", "centre", "centre"),
                 ((0, 80, 20, 100), "left", "down", "down_left"), ((45, 90, 55, 100), "centre", "down", "down"),
                 ((30, 30, 50, 50), "centre", "centre", "centre"),  # A centre at exactly 0.4 is the middle third.
                 ((50, 50, 70, 70), "centre", "centre", "centre"),  # And at exactly 0.6.
                 ((29, 51, 50, 70), "left", "down", "down_left"))
        for box, column, row, name in cases:
            with self.subTest(box=box):
                cell = marks.cell_of(box, 100, 100)
                self.assertEqual((cell["column"], cell["row"], cell["cell"]), (column, row, name))
        # The frame need not be square: the fractions are per axis, over the full frame.
        self.assertEqual(marks.cell_of((0, 0, 100, 100), 1000, 200)["cell"], "up_left")
        self.assertEqual(marks.cell_of((0, 90, 100, 110), 1000, 200)["cell"], "left")
        self.assertEqual(marks.cell_of((0, 0, 100, 100), 1000, 200)["centre_fraction"], [0.05, 0.25])

    def test_the_target_is_the_best_box_of_the_label_at_or_above_the_threshold(self):
        found = marks.target_of([Box("bottle", 0.55, (0, 0, 10, 10)), Box("person", 0.99, (40, 40, 60, 60)),
                                 Box("bottle", 0.7, (80, 80, 100, 100)), Box("bottle", 0.4, (40, 40, 60, 60))],
                                "bottle", 0.5, 100, 100)
        self.assertEqual(found, {"present": True, "count": 2, "confidence": 0.7, "box_xyxy": [80, 80, 100, 100],
                                 "column": "right", "row": "down", "cell": "down_right", "centre_fraction": [0.9, 0.9]})
        absent = marks.target_of([Box("person", 0.99, (0, 0, 10, 10)), Box("bottle", 0.49, (0, 0, 10, 10))],
                                 "bottle", 0.5, 100, 100)
        self.assertEqual((absent["present"], absent["count"]), (False, 0))
        self.assertTrue(all(absent[name] is None for name in ("confidence", "box_xyxy", "column", "row", "cell",
                                                               "centre_fraction")))
        self.assertEqual(set(absent), set(found))
        self.assertIn("as the helper exported it", marks.ORIENTATION_STATEMENT)
        self.assertIn("orientation", marks.ORIENTATION_STATEMENT)


class SummaryTests(unittest.TestCase):
    """A scripted run: frames every 200 ms for 9 s; the target per the script; cues and a removal."""

    def scripted(self):
        frames = []
        for at_ms in range(0, 9000, 200):
            second = at_ms // 1000
            if second == 0 or second == 3:
                centre = None  # Before the first cue, and one in-view second without a detection.
            elif second < 5:
                centre = (20, 50)  # Left.
            elif second < 7:
                centre = (80, 50) if at_ms != 5000 else (50, 50)  # Right; the first frame still centred.
            else:
                centre = None
            frames.append(frame(at_ms, centre))
        recorded = [mark(1, "left", 1000), mark(2, "right", 5000), mark(3, "out_of_view", 7000),
                    mark(4, "remove", 7500, "key")]
        return frames, recorded

    def summary(self, **overrides):
        frames, recorded = self.scripted()
        options = {"origin_ns": ORIGIN, "run_end_ns": ORIGIN + 9200 * MS,
                   "helper_end": {"monotonic_ns": ORIGIN + 9200 * MS, "reason": "headset_removed"}, "with_target": True}
        options.update(overrides)
        return marks.summarize(frames, recorded, **options)

    def test_the_one_second_windows_count_frames_and_target_frames_from_the_origin(self):
        summary = self.summary()
        self.assertEqual(summary["window_seconds"], 1)
        self.assertEqual([window["index"] for window in summary["windows"]], list(range(9)))
        self.assertEqual([window["frames"] for window in summary["windows"]], [5] * 9)
        self.assertEqual([window["target_frames"] for window in summary["windows"]], [0, 5, 5, 0, 5, 5, 5, 0, 0])

    def test_each_mark_interval_counts_frames_per_cell_as_exported(self):
        left, right, out, remove = self.summary()["intervals"]
        self.assertEqual((left["label"], left["mark_index"], left["source"], left["duration_ms"]), ("left", 1, "cue", 4000.0))
        self.assertEqual((left["frames"], left["target_frames"]), (20, 15))
        self.assertEqual(left["cells"]["left"], 15)
        self.assertEqual(sum(left["cells"].values()), 15)
        self.assertEqual(left["columns"], {"left": 15, "centre": 0, "right": 0})
        self.assertEqual(left["rows"], {"up": 0, "centre": 15, "down": 0})
        self.assertEqual((left["windows"], left["windows_with_frames"], left["windows_with_target"]), (4, 4, 3))
        self.assertEqual((right["frames"], right["target_frames"]), (10, 10))
        self.assertEqual((right["cells"]["right"], right["cells"]["centre"]), (9, 1))
        self.assertEqual(set(right["cells"]), set(marks.CELLS))
        self.assertEqual((out["label"], out["frames"], out["target_frames"], out["duration_ms"]), ("out_of_view", 3, 0, 500.0))
        self.assertEqual((out["windows"], out["remainder_ms"]), (0, 500.0))
        self.assertEqual((remove["label"], remove["source"], remove["frames"], remove["duration_ms"]),
                         ("remove", "key", 7, 1700.0))

    def test_coverage_is_the_whole_windows_of_the_in_view_spans_that_held_the_target(self):
        coverage = self.summary()["coverage"]
        (span,) = coverage["spans"]
        self.assertEqual((span["mark_index"], span["labels"], span["ended_by"]), (1, ["left", "right"], "out_of_view"))
        self.assertEqual((span["duration_ms"], span["frames"], span["target_frames"]), (6000.0, 30, 25))
        self.assertEqual((span["windows"], span["windows_with_frames"], span["windows_with_target"]), (6, 6, 5))
        self.assertEqual((coverage["windows"], coverage["windows_with_target"], coverage["windows_without_frames"]),
                         (6, 5, 0))
        self.assertEqual(coverage["fraction"], round(5 / 6, 4))

    def test_coverage_spans_close_on_remove_or_the_run_end_and_reopen(self):
        frames = [frame(at_ms, (50, 50)) for at_ms in range(0, 6000, 250)]
        recorded = [mark(1, "reseat", 0), mark(2, "near", 500), mark(3, "remove", 2700), mark(4, "mark", 2800, "key"),
                    mark(5, "out_of_view", 3000), mark(6, "far", 4000)]
        coverage = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 6100 * MS,
                                   with_target=True)["coverage"]
        first, second = coverage["spans"]
        self.assertEqual((first["labels"], first["ended_by"], first["windows"], first["remainder_ms"]),
                         (["near"], "remove", 2, 200.0))
        self.assertEqual((second["labels"], second["ended_by"], second["windows"], second["windows_with_target"]),
                         (["far"], "run_end", 2, 2))
        self.assertEqual((coverage["windows"], coverage["windows_with_target"], coverage["fraction"]), (4, 4, 1.0))
        # A window in which no frame was processed counts against the coverage and is reported.
        sparse = marks.summarize([frame(600, (50, 50))], [mark(1, "in_view", 0)], origin_ns=ORIGIN,
                                 run_end_ns=ORIGIN + 3000 * MS, with_target=True)["coverage"]
        self.assertEqual((sparse["windows"], sparse["windows_with_target"], sparse["windows_without_frames"],
                          sparse["fraction"]), (3, 1, 2, round(1 / 3, 4)))
        none = marks.summarize(frames, [mark(1, "remove", 100)], origin_ns=ORIGIN, run_end_ns=ORIGIN + 6100 * MS,
                               with_target=True)["coverage"]
        self.assertEqual((none["spans"], none["windows"], none["fraction"]), ([], 0, None))

    def test_after_a_remove_mark_late_frames_and_the_helpers_end_are_measured(self):
        (removal,) = self.summary()["removal"]
        self.assertEqual((removal["mark_index"], removal["source"]), (4, "key"))
        self.assertEqual((removal["late_frame_limit_ms"], removal["end_limit_ms"]), (2000, 3000))
        self.assertEqual((removal["frames_processed_after_mark"], removal["frames_acquired_over_limit"]), (7, 0))
        self.assertEqual(removal["last_frame_acquire_after_mark_ms"], 1300.0)
        self.assertEqual((removal["helper_end_after_mark_ms"], removal["helper_end_reason"]), (1700.0, "headset_removed"))
        self.assertTrue(removal["helper_end_is_presence_stop"] and removal["helper_end_within_limit"])
        # A helper that ran on to its bound: frames more than 2.0 s after the mark are counted, the end is late.
        frames = [frame(at_ms) for at_ms in range(0, 6000, 500)]
        (late,) = marks.summarize(frames, [mark(1, "remove", 1000)], origin_ns=ORIGIN, run_end_ns=ORIGIN + 6000 * MS,
                                  helper_end={"monotonic_ns": ORIGIN + 6000 * MS, "reason": "max_frames"},
                                  with_target=True)["removal"]
        self.assertEqual((late["frames_processed_after_mark"], late["frames_acquired_over_limit"]), (9, 5))
        self.assertEqual(late["last_frame_acquire_after_mark_ms"], 4500.0)  # 3000 ms is exactly 2.0 s: not over.
        self.assertEqual((late["helper_end_after_mark_ms"], late["helper_end_reason"]), (5000.0, "max_frames"))
        self.assertFalse(late["helper_end_is_presence_stop"] or late["helper_end_within_limit"])
        # No end record was read (the helper was killed): the end fields stay empty, the frame count stands.
        (unknown,) = self.summary(helper_end=None)["removal"]
        self.assertEqual(unknown["frames_processed_after_mark"], 7)
        self.assertTrue(all(unknown[name] is None for name in ("helper_end_after_mark_ms", "helper_end_reason",
                                                                "helper_end_is_presence_stop", "helper_end_within_limit")))

    def test_cue_spans_are_bounded_by_scheduled_times_so_wake_up_jitter_never_drops_a_window(self):
        frames = [frame(at_ms, (50, 50)) for at_ms in range(0, 21000, 200)]
        for near_late, remove_late in ((300_000, 100_000), (100_000, 300_000), (0, 0), (900_000, 1), (5 * MS, 0)):
            with self.subTest(near_late=near_late, remove_late=remove_late):
                recorded = [mark(1, "near", 3000, late_ns=near_late), mark(2, "remove", 20000, late_ns=remove_late)]
                summary = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 21000 * MS,
                                          with_target=True)
                (span,) = summary["coverage"]["spans"]
                self.assertEqual((span["windows"], span["windows_with_target"], span["remainder_ms"], span["duration_ms"]),
                                 (17, 17, 0.0, 17000.0))
                near = summary["intervals"][0]
                self.assertEqual((near["boundary"], near["windows"], near["frames"]), ("scheduled", 17, 85))
                self.assertEqual(summary["coverage"]["fraction"], 1.0)
        # A key mark has no schedule: its actual time bounds the span, and the interval says which was used.
        recorded = [mark(1, "near", 3000, late_ns=300_000), mark(2, "out_of_view", 9999, "key")]
        summary = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 21000 * MS, with_target=True)
        self.assertEqual([interval["boundary"] for interval in summary["intervals"]], ["scheduled", "actual"])
        self.assertEqual((summary["coverage"]["spans"][0]["windows"], summary["coverage"]["spans"][0]["remainder_ms"]),
                         (6, 999.0))
        # A key pressed between a cue's scheduled and actual time is ordered by the boundary it has.
        recorded = [mark(1, "out_of_view", 5001, "key"), mark(2, "near", 5000, late_ns=2 * MS)]
        summary = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 21000 * MS, with_target=True)
        self.assertEqual([interval["label"] for interval in summary["intervals"]], ["near", "out_of_view"])
        self.assertTrue(all(interval["duration_ms"] >= 0 for interval in summary["intervals"]))

    def test_every_remove_mark_cue_or_key_has_its_own_removal_entry_from_its_actual_time(self):
        frames = [frame(at_ms) for at_ms in range(0, 8000, 200)]
        recorded = [mark(1, "remove", 5000, late_ns=400_000), mark(2, "remove", 6100, "key")]
        cue, key = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 8300 * MS,
                                   helper_end={"monotonic_ns": ORIGIN + 8300 * MS, "reason": "headset_removed"})["removal"]
        self.assertEqual((cue["mark_index"], cue["source"], key["mark_index"], key["source"]), (1, "cue", 2, "key"))
        self.assertEqual((cue["helper_end_after_mark_ms"], key["helper_end_after_mark_ms"]), (3299.6, 2200.0))
        self.assertEqual((cue["helper_end_within_limit"], key["helper_end_within_limit"]), (False, True))
        self.assertEqual((cue["frames_acquired_over_limit"], key["frames_acquired_over_limit"]), (4, 0))
        self.assertEqual((cue["last_frame_acquire_after_mark_ms"], key["last_frame_acquire_after_mark_ms"]), (2799.6, 1700.0))
        self.assertEqual(set(cue), set(key))

    def test_without_a_target_only_frame_counts_and_the_removal_remain(self):
        frames, recorded = self.scripted()
        for item in frames:
            del item["target"]
        summary = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 9200 * MS)
        self.assertIsNone(summary["coverage"])
        self.assertEqual(summary["windows"][0], {"index": 0, "frames": 5})
        self.assertEqual(set(summary["intervals"][0]), {"mark_index", "label", "source", "boundary", "duration_ms", "frames", "windows",
                                                        "windows_with_frames", "windows_with_target", "remainder_ms"})
        self.assertIsNone(summary["intervals"][0]["windows_with_target"])
        self.assertEqual(summary["removal"][0]["frames_processed_after_mark"], 7)

    def test_an_empty_run_summarizes_to_nothing(self):
        summary = marks.summarize([], [], origin_ns=None, run_end_ns=ORIGIN, with_target=True)
        self.assertEqual((summary["windows"], summary["intervals"], summary["removal"]), ([], [], []))
        self.assertEqual(summary["coverage"], {"spans": [], "windows": 0, "windows_with_target": 0,
                                               "windows_without_frames": 0, "fraction": None})

    def test_a_window_mark_bounds_its_interval_at_its_actual_time(self):
        # A window mark is made when the wearer clicks: there is no scheduled time, so the click's time is the boundary.
        frames = [frame(at_ms, (50, 50)) for at_ms in range(0, 4000, 200)]
        recorded = [mark(1, "near", 1037, source="window"), mark(2, "far", 2512, source="window")]
        self.assertEqual([marks.boundary_ns(entry) for entry in recorded], [ORIGIN + 1037 * MS, ORIGIN + 2512 * MS])
        summary = marks.summarize(frames, recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 4000 * MS, with_target=True)
        near, far = summary["intervals"]
        self.assertEqual((near["source"], near["boundary"], near["duration_ms"]), ("window", "actual", 1475.0))
        self.assertEqual((near["frames"], far["frames"]), (7, 7))  # 1200..2400 and 2600..3800, every 200 ms.
        self.assertEqual(summary["coverage"]["spans"][0]["labels"], ["near", "far"])

    def test_the_removal_limits_are_parameters_with_the_current_values_as_defaults(self):
        frames = [frame(at_ms) for at_ms in (0, 500, 1000, 1500, 2600, 3900)]
        recorded = [mark(1, "remove", 1000, source="window")]
        end = {"monotonic_ns": ORIGIN + 6000 * MS, "reason": "headset_removed"}
        options = {"origin_ns": ORIGIN, "run_end_ns": end["monotonic_ns"], "helper_end": end}
        (default,) = marks.summarize(frames, recorded, **options)["removal"]
        self.assertEqual((default["late_frame_limit_ms"], default["end_limit_ms"]),
                         (marks.LATE_FRAME_LIMIT_MS, marks.REMOVAL_END_LIMIT_MS))
        self.assertEqual((default["late_frame_limit_ms"], default["end_limit_ms"]), (2000, 3000))
        self.assertEqual((default["frames_acquired_over_limit"], default["helper_end_within_limit"]), (1, False))
        (wider,) = marks.summarize(frames, recorded, late_frame_limit_ms=3500, removal_end_limit_ms=6000,
                                   **options)["removal"]
        self.assertEqual((wider["late_frame_limit_ms"], wider["end_limit_ms"]), (3500, 6000))
        self.assertEqual((wider["frames_acquired_over_limit"], wider["helper_end_within_limit"]), (0, True))
        # What was measured is the same whatever it is compared with.
        measured = ("frames_processed_after_mark", "last_frame_acquire_after_mark_ms", "helper_end_after_mark_ms",
                    "helper_end_reason", "helper_end_is_presence_stop", "helper_end_after_last_frame_ms")
        self.assertEqual([default[name] for name in measured], [wider[name] for name in measured])
        self.assertEqual([default[name] for name in measured], [3, 2900.0, 5000.0, "headset_removed", True, 2100.0])

    def test_the_time_from_the_last_frame_to_the_helpers_end_needs_a_frame_and_an_end_record(self):
        recorded = [mark(1, "remove", 1000, source="window")]
        end = {"monotonic_ns": ORIGIN + 3000 * MS, "reason": "standby"}
        (no_end,) = marks.summarize([frame(500)], recorded, origin_ns=ORIGIN, run_end_ns=ORIGIN + 3000 * MS)["removal"]
        (no_frame,) = marks.summarize([], recorded, origin_ns=None, run_end_ns=end["monotonic_ns"],
                                      helper_end=end)["removal"]
        (before,) = marks.summarize([frame(500)], recorded, origin_ns=ORIGIN, run_end_ns=end["monotonic_ns"],
                                    helper_end=end)["removal"]
        self.assertEqual((no_end["helper_end_after_last_frame_ms"], no_frame["helper_end_after_last_frame_ms"]),
                         (None, None))
        # The last frame was acquired before the click: nothing after the mark, and still 2.5 s to the end record.
        self.assertEqual((before["frames_processed_after_mark"], before["helper_end_after_last_frame_ms"]), (0, 2500.0))


class WindowMarkTests(unittest.TestCase):
    """``MarkSession.mark``: a mark recorded from code (a guiding window's click), beside cues and keys."""

    def session(self, **options):
        session = marks.MarkSession(marks.Clock(), **options)
        self.addCleanup(session.stop)
        return session

    def test_the_sources_are_cue_key_and_window(self):
        self.assertEqual(marks.SOURCES, ("cue", "key", "window"))
        self.assertEqual((marks.SOURCE_CUE, marks.SOURCE_KEY, marks.SOURCE_WINDOW), marks.SOURCES)

    def test_a_window_mark_is_recorded_at_the_time_given_with_its_own_source_and_no_announcement(self):
        output = io.StringIO()
        session = self.session(output=output)
        session.start()
        self.assertEqual(session.threads, [])  # No cue and no key source: a window mark starts no thread.
        session.first_frame(ORIGIN)
        self.assertTrue(session.mark("near", ORIGIN + 1500 * MS))
        before = time.monotonic_ns()
        self.assertTrue(session.mark("far"))
        after = time.monotonic_ns()
        near, far = session.marks()
        self.assertEqual({name: near[name] for name in ("index", "source", "label", "monotonic_ns", "offset_ms")},
                         {"index": 1, "source": "window", "label": "near", "monotonic_ns": ORIGIN + 1500 * MS,
                          "offset_ms": 1500.0})
        self.assertEqual(near["utc_ns"], session.clock.utc(ORIGIN + 1500 * MS))
        self.assertNotIn("scheduled_monotonic_ns", near)  # Its actual time is its boundary.
        self.assertEqual(marks.boundary_ns(near), near["monotonic_ns"])
        self.assertTrue(before <= far["monotonic_ns"] <= after)
        self.assertEqual(output.getvalue(), "")
        report = session.report()
        self.assertEqual((report["window_marks"], report["key_presses"], report["key_marks_enabled"]), (2, 0, False))

    def test_only_vocabulary_labels_and_integer_times_are_recorded(self):
        session = self.session()
        for label in ("bottle", "mark", "", None, 3, "Near"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                session.mark(label)
        for when in (1.5, "1", True):
            with self.subTest(when=when), self.assertRaises(ValueError):
                session.mark("near", when)
        self.assertEqual((session.marks(), session.report()["window_marks"]), ([], 0))

    def test_window_marks_are_bounded_as_key_marks_are_and_every_cue_keeps_its_place(self):
        cues = [(index * 1000, "left") for index in range(4)]
        session = self.session(cues=cues)
        self.assertEqual(session.key_capacity, marks.MAX_MARKS - 4)
        results = [session.mark("near", ORIGIN + index) for index in range(marks.MAX_MARKS)]
        self.assertEqual((results.count(True), results.count(False)), (marks.MAX_MARKS - 4, 4))
        report = session.report()
        self.assertEqual((len(report["recorded"]), report["marks_dropped"], report["window_marks"]),
                         (marks.MAX_MARKS - 4, 4, marks.MAX_MARKS))

    def test_any_thread_may_mark_and_a_stopped_session_records_nothing(self):
        session = self.session()
        threads = [threading.Thread(target=lambda: [session.mark("centre") for _ in range(4)]) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        recorded = session.marks()
        self.assertEqual([entry["index"] for entry in recorded], list(range(1, 33)))
        self.assertEqual({entry["source"] for entry in recorded}, {"window"})
        self.assertEqual([entry["monotonic_ns"] for entry in recorded], sorted(entry["monotonic_ns"] for entry in recorded))
        session.stop()
        self.assertFalse(session.mark("far"))
        self.assertEqual(len(session.marks()), 32)


class MarkSessionTests(unittest.TestCase):
    def session(self, **options):
        session = marks.MarkSession(marks.Clock(), **options)
        self.addCleanup(session.stop)
        return session

    def test_a_cue_schedule_announces_each_cue_with_a_bell_and_records_scheduled_and_actual_times(self):
        terminal = io.StringIO()
        session = self.session(cues=[(0, "near"), (150, "far"), (300, "remove"), (20000, "reseat")], output=terminal)
        session.start()
        time.sleep(0.1)
        self.assertEqual((session.marks(), terminal.getvalue()), ([], ""))  # Nothing is due before the first frame.
        origin = time.monotonic_ns()
        session.first_frame(origin)
        session.first_frame(origin + 5 * SECOND)  # Only the first frame sets the origin.
        self.assertTrue(wait_for(lambda: len(session.marks()) == 3))
        session.stop()
        self.assertEqual(terminal.getvalue(), "\aCUE near\n\aCUE far\n\aCUE remove\n")
        report = session.report()
        self.assertEqual([entry["label"] for entry in report["recorded"]], ["near", "far", "remove"])
        self.assertEqual(report["cues_not_reached"], [{"offset_ms": 20000, "label": "reseat"}])
        self.assertEqual(len(report["cues_requested"]), 4)
        for entry, offset in zip(report["recorded"], (0, 150, 300)):
            self.assertEqual((entry["source"], entry["scheduled_offset_ms"]), ("cue", offset))
            self.assertEqual(entry["scheduled_monotonic_ns"], origin + offset * MS)
            self.assertEqual(entry["scheduled_utc_ns"], session.clock.utc(entry["scheduled_monotonic_ns"]))
            self.assertEqual(entry["utc_ns"], session.clock.utc(entry["monotonic_ns"]))
            self.assertGreaterEqual(entry["monotonic_ns"], entry["scheduled_monotonic_ns"])
            self.assertEqual(entry["late_ms"], round((entry["monotonic_ns"] - entry["scheduled_monotonic_ns"]) / 1e6, 3))
            self.assertLess(entry["late_ms"], 1000)
            self.assertAlmostEqual(entry["offset_ms"], offset + entry["late_ms"], places=2)
        self.assertEqual([entry["index"] for entry in report["recorded"]], [1, 2, 3])
        self.assertFalse(report["key_marks_enabled"])

    def test_no_cue_and_no_key_starts_no_thread_and_writes_nothing(self):
        terminal = io.StringIO()
        session = self.session(output=terminal)
        session.start()
        session.first_frame(time.monotonic_ns())
        session.stop()
        self.assertEqual((session.threads, terminal.getvalue(), session.marks()), ([], "", []))
        report = session.report()
        self.assertEqual((report["recorded"], report["cues_requested"], report["key_presses"]), ([], [], 0))

    @unittest.skipUnless(sys.platform.startswith("linux"), "pseudo-terminals as on the Frame")
    def test_enter_on_the_terminal_records_the_next_label_then_mark_and_keeps_nothing_typed(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        os.write(master, b"\n")  # Typed before the run: discarded, never a mark.
        time.sleep(0.05)
        terminal = io.StringIO()
        session = self.session(labels=["near", "remove"], key_fd=slave, output=terminal)
        session.start()
        time.sleep(0.2)
        self.assertEqual(session.marks(), [])
        before = time.monotonic_ns()
        os.write(master, b"\n")
        self.assertTrue(wait_for(lambda: len(session.marks()) == 1))
        session.first_frame(time.monotonic_ns())
        os.write(master, b"my passphrase and other free text\n")
        self.assertTrue(wait_for(lambda: len(session.marks()) == 2))
        os.write(master, b"\n")
        self.assertTrue(wait_for(lambda: len(session.marks()) == 3))
        os.write(master, b"typed but never entered")
        time.sleep(0.2)
        session.stop()
        report = session.report()
        self.assertEqual([(entry["source"], entry["label"]) for entry in report["recorded"]],
                         [("key", "near"), ("key", "remove"), ("key", "mark")])
        first, second, third = report["recorded"]
        self.assertTrue(before <= first["monotonic_ns"] <= second["monotonic_ns"] <= third["monotonic_ns"])
        self.assertIsNone(first["offset_ms"])  # Pressed before the first frame: no origin yet.
        self.assertGreaterEqual(second["offset_ms"], 0)
        self.assertNotIn("scheduled_monotonic_ns", first)
        self.assertEqual((report["key_presses"], report["key_marks_enabled"], report["key_listener_error"]), (3, True, None))
        self.assertEqual(terminal.getvalue(), "MARK near\nMARK remove\nMARK mark\n")
        self.assertNotIn("passphrase", repr(report) + repr(vars(session)))

    @unittest.skipUnless(sys.platform.startswith("linux"), "pseudo-terminals as on the Frame")
    def test_marks_are_bounded_and_the_overflow_is_counted(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        session = self.session(key_fd=slave)
        session.start()
        time.sleep(0.1)
        for _ in range(marks.MAX_MARKS + 3):
            os.write(master, b"\n")
        self.assertTrue(wait_for(lambda: session.key_presses == marks.MAX_MARKS + 3))
        session.stop()
        report = session.report()
        self.assertEqual((len(report["recorded"]), report["marks_dropped"], report["maximum_marks"]),
                         (marks.MAX_MARKS, 3, marks.MAX_MARKS))

    @unittest.skipUnless(sys.platform.startswith("linux"), "pseudo-terminals as on the Frame")
    def test_every_scheduled_cue_keeps_its_place_whatever_the_key_presses(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        terminal = io.StringIO()
        cues = [(0, "near"), (50, "far"), (100, "remove"), (150, "reseat")]
        session = self.session(cues=cues, key_fd=slave, output=terminal)
        session.start()
        time.sleep(0.1)
        for _ in range(marks.MAX_MARKS + 2):  # A held Enter key before any cue is due.
            os.write(master, b"\n")
        self.assertTrue(wait_for(lambda: session.key_presses == marks.MAX_MARKS + 2))
        session.first_frame(time.monotonic_ns())
        self.assertTrue(wait_for(lambda: session.cues_fired == 4))
        session.stop()
        report = session.report()
        recorded = report["recorded"]
        self.assertEqual([entry["label"] for entry in recorded if entry["source"] == "cue"],
                         ["near", "far", "remove", "reseat"])
        self.assertEqual(sum(1 for entry in recorded if entry["source"] == "key"), marks.MAX_MARKS - 4)
        self.assertEqual((len(recorded), report["marks_dropped"], report["key_marks_capacity"], report["cues_not_reached"]),
                         (marks.MAX_MARKS, 6, marks.MAX_MARKS - 4, []))
        self.assertEqual(terminal.getvalue().count("\aCUE "), 4)  # Each cue was announced as well as recorded.
        self.assertGreaterEqual(marks.MAX_MARKS - marks.MAX_CUES, 32)

    @unittest.skipUnless(sys.platform.startswith("linux"), "pseudo-terminals as on the Frame")
    def test_a_terminal_that_refuses_the_flush_does_not_stop_the_session(self):
        import termios
        from unittest import mock
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        for failure in (termios.error(25, "Inappropriate ioctl for device"), OSError(5, "Input/output error")):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(termios, "tcflush", side_effect=failure):
                session = marks.MarkSession(marks.Clock(), key_fd=slave)
                session.start()
                os.write(master, b"\n")
                self.assertTrue(wait_for(lambda: len(session.marks()) == 1))
                session.stop()

    def test_only_a_real_terminal_is_a_key_source(self):
        class Typed:
            def isatty(self):
                return True

        self.assertIsNone(marks.terminal_fd(Typed()))  # No descriptor: the confirmation's stand-in, never read for keys.
        self.assertIsNone(marks.terminal_fd(io.StringIO("yes\n")))
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        with os.fdopen(os.dup(read_end)) as piped:
            self.assertIsNone(marks.terminal_fd(piped))
        if sys.platform.startswith("linux"):
            master, slave = os.openpty()
            self.addCleanup(os.close, master)
            with os.fdopen(slave) as terminal:
                self.assertEqual(marks.terminal_fd(terminal), terminal.fileno())


if __name__ == "__main__":
    unittest.main()
