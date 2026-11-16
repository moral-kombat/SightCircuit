"""The replay window's worker is recorded-only: no camera, mirror or other source reaches its sinks.

Closes finding 1 of the 2026-11-09 Arcturus SDK input review (the ungated
live-source hook). Nothing is decoded or displayed here, and no device is
opened.
"""
import ast
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from sightcircuit import gates

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
REASON = "^replay_live_source_not_approved$"


class RecordingSink:
    """Stands where ``OverlaySink`` would: any call means a frame or a clear could reach the VR panel."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.calls.append(name)


class CameraLikeSource:
    """Shaped like a camera frame source (the L5 helper source): a kind, an id and the source protocol."""
    kind = "camera"
    source_id = "arcturus-camera:left"

    def __init__(self):
        self.opened = False
        self.playing, self.restart = threading.Event(), threading.Event()
        self.looping, self.at_end = threading.Event(), threading.Event()
        self.realtime, self.stream_index = True, 0

    def open(self):
        self.opened = True

    def read(self, stop, timeout=0.05):
        raise AssertionError("a refused source is never read")

    def close(self):
        return True


@unittest.skipUnless(AVAILABLE, "Run scripts/setup_window.sh for the replay window's dependencies")
class ReplayWorkerSourceTests(unittest.TestCase):
    def setUp(self):
        from scripts import replay_window
        from sightcircuit.sources.replay import ReplaySource
        self.module, self.ReplaySource = replay_window, ReplaySource
        self.clip = ROOT / "absent-recording.mp4"  # Never opened: the worker is never started here.

    def refused(self, source):
        sink = RecordingSink()
        with patch.object(self.module, "Pipeline", side_effect=AssertionError("no engine for a refused source")), \
                self.assertRaisesRegex(ValueError, REASON):
            self.module.ReplayWorker(self.clip, 0, 0.5, 10, 0, sinks=(sink,), source=source)
        self.assertEqual(sink.calls, [], "nothing may reach a sink before the refusal")

    def test_camera_source_is_refused(self):
        source = CameraLikeSource()
        self.refused(source)
        self.assertFalse(source.opened)

    def test_mirror_source_is_refused(self):
        from sightcircuit.sources.mirror import MirrorSource

        def acquire(stop, on_frame, progress):
            raise AssertionError("a refused mirror is never acquired")

        self.refused(MirrorSource(acquire))

    def test_other_non_replay_sources_are_refused(self):
        from sightcircuit.runtime.synthetic import SyntheticSource

        class Subclass(self.ReplaySource):
            """A subclass could override the decoder with anything, so only the exact type passes."""

        class Renamed:
            """Borrowing the name is not being the class."""
        Renamed.__name__ = Renamed.__qualname__ = "ReplaySource"

        for source in (SyntheticSource(), Subclass(self.clip, 0), Renamed(), object(), "recording.mp4",
                       self.clip, 0, False, True):
            with self.subTest(source=type(source).__name__):
                self.refused(source)

    def test_refusal_goes_through_the_live_image_gate(self):
        with patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as require:
            with self.assertRaisesRegex(ValueError, REASON):
                self.module.ReplayWorker(self.clip, 0, 0.5, 10, 0, source=CameraLikeSource())
        (gate, permitted, reason), _ = require.call_args
        self.assertIs(gate, gates.LIVE_IMAGE_INPUT)
        self.assertIs(permitted, False)
        self.assertEqual(reason, self.module.RECORDED_SOURCE_REQUIRED)
        self.assertEqual(gates.claims(gates.LIVE_IMAGE_INPUT), {"production_live_input_enabled": False})

    def test_recorded_sources_are_accepted(self):
        built = self.module.ReplayWorker(self.clip, 1, 0.5, 10, 0, realtime=True, decode_threads=3)
        self.assertIs(type(built.source), self.ReplaySource)
        self.assertEqual((built.source.path, built.source.ordinal, built.source.realtime,
                          built.source.decode_threads), (self.clip, 1, True, 3))
        self.assertTrue(built.pipeline.latest_only)
        given = self.ReplaySource(self.clip, 0)
        worker = self.module.ReplayWorker(None, 0, 0.5, 10, 0, source=given)
        self.assertIs(worker.source, given)
        self.assertFalse(worker.pipeline.latest_only)
        for name in ("playing", "restart", "looping", "at_end"):
            self.assertIs(getattr(worker, name), getattr(given, name))
        self.assertIsNone(worker.stream_index)
        self.assertFalse(worker.is_alive())

    def test_the_window_builds_its_worker_without_a_source_argument(self):
        tree = ast.parse((ROOT / "sightcircuit/commands/replay_window.py").read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Name) and node.func.id == "ReplayWorker"]
        self.assertEqual(len(calls), 1)
        self.assertNotIn("source", [keyword.arg for keyword in calls[0].keywords])
        self.assertLessEqual(len(calls[0].args), 9)  # ``source`` is the tenth parameter.


if __name__ == "__main__":
    unittest.main()
