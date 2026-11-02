"""Replay source decoding order, keyframe catch-up and pause scheduling with a fake clock."""
from fractions import Fraction
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from framejacker.sources.replay import PlaybackClock, ReplaySource

AVAILABLE = all(importlib.util.find_spec(name) for name in ("av", "numpy"))
FPS, FRAMES, GOP = 30, 90, 10


class SteppingClock(PlaybackClock):
    """Wall time advances by ``step`` on every reading, so decoding always looks slow."""

    def __init__(self, step):
        self.wall, self.step = 0.0, step
        super().__init__(self._now)

    def _now(self):
        self.wall += self.step
        return self.wall


class Collector:
    def __init__(self, stop, limit):
        self.frames, self.stop, self.limit = [], stop, limit

    def __call__(self, frame):
        self.frames.append(frame)
        if len(self.frames) >= self.limit:
            self.stop.set()


class FakeAv:
    """Just enough of PyAV for ``ReplaySource``: ``frames`` packets at ``FPS``, a keyframe every ten.

    The decoder holds back the frames of the packets in ``held`` and returns
    them together with the next frame, as frame threading does.
    """

    def __init__(self, held, frames):
        self.held, self.frames = held, frames

    def open(self, path, mode="r"):
        return FakeContainer(self)


class FakeContainer:
    def __init__(self, av):
        self.av, self.streams = av, SimpleNamespace(video=[FakeStream(av.held)])

    def __enter__(self):
        return self

    def __exit__(self, *details):
        return False

    def demux(self, stream):
        for index in range(self.av.frames):
            yield SimpleNamespace(size=1, pts=index, time_base=Fraction(1, FPS), is_keyframe=index % 10 == 0)
        yield SimpleNamespace(size=0, pts=None, time_base=None, is_keyframe=False)  # Flush.


class FakeStream:
    index, thread_type = 0, None

    def __init__(self, held):
        self.held, self.pending = held, []
        self.codec_context = SimpleNamespace(thread_count=None)

    def decode(self, packet):
        if packet.size:
            self.pending.append(FakeDecoded(packet.pts))
            if packet.pts in self.held:
                return []
        frames, self.pending = self.pending, []
        return frames


class FakeDecoded:
    is_corrupt, width, height = False, 4, 4

    def __init__(self, index):
        self.pts, self.time_base = index, Fraction(1, FPS)

    def reformat(self, width, height, format):
        return self

    def to_ndarray(self):
        import numpy as np
        return np.zeros((self.height, self.width, 3), np.uint8)


@unittest.skipUnless(AVAILABLE, "Install requirements-replay.txt for decoder tests")
class ReplaySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import av
        import numpy as np

        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.clip = Path(cls.temporary.name) / "gop.mp4"
        with av.open(str(cls.clip), mode="w") as output:
            stream = output.add_stream("mpeg4", rate=FPS)
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            stream.codec_context.gop_size = GOP
            for index in range(FRAMES):
                pixels = np.full((64, 64, 3), index * 2 % 256, dtype=np.uint8)
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="bgr24")):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        with av.open(str(cls.clip)) as source:
            video = source.streams.video[0]
            cls.keyframes = {round(float(packet.pts * packet.time_base) * FPS)
                             for packet in source.demux(video) if packet.size and packet.is_keyframe}

    def play(self, source, limit):
        stop = threading.Event()
        collector = Collector(stop, limit)
        source.looping.clear()
        thread = threading.Thread(target=source.run, args=(collector, stop), daemon=True)
        thread.start()
        source.at_end.wait(20)
        stop.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        return collector.frames

    def test_unpaced_replay_emits_every_frame_in_order(self):
        source = ReplaySource(self.clip, 0)
        frames = self.play(source, FRAMES + 1)
        self.assertEqual(len(frames), FRAMES)
        self.assertEqual([frame.sequence for frame in frames], list(range(1, FRAMES + 1)))
        self.assertEqual(source.frames_decoded, FRAMES)
        self.assertEqual((source.frames_skipped, source.catch_ups, source.frames_dropped), (0, 0, 0))
        self.assertAlmostEqual(source.media_seconds, (FRAMES - 1) / FPS, places=3)
        self.assertNotIn("schedule_lateness_ms", frames[0].metadata)

    def test_slow_decoding_skips_to_keyframes_and_keeps_real_time_progress(self):
        # Each frame costs 0.2 s of wall time against 1/30 s of media: far behind real time.
        source = ReplaySource(self.clip, 0, realtime=True, clock=SteppingClock(0.2))
        frames = self.play(source, FRAMES + 1)
        self.assertGreater(source.catch_ups, 0)
        self.assertGreater(source.frames_skipped, 0)
        self.assertLess(source.frames_decoded, FRAMES)
        times = [frame.media_time for frame in frames]
        self.assertEqual(times, sorted(times))
        # After each catch-up the next emitted frame is a keyframe; skipped frames are not decoded.
        jumps = [later for earlier, later in zip(times, times[1:]) if later - earlier > 1.5 / FPS]
        self.assertTrue(jumps)
        self.assertGreater(len(self.keyframes), 3)
        for media_time in jumps:
            self.assertIn(round(media_time * FPS), self.keyframes)
        self.assertTrue(all(frame.metadata["schedule_lateness_ms"] <= 500 for frame in frames))
        self.assertGreater(source.media_seconds, (len(frames) - 1) / FPS)

    def test_every_frame_replay_spans_each_frame_once(self):
        source = ReplaySource(self.clip, 0)
        self.assertIsNone(source.processed_fraction(0))  # Nothing played yet: unknown, not zero.
        frames = self.play(source, FRAMES + 1)
        self.assertEqual(source.frames_spanned, FRAMES)
        self.assertEqual(source.processed_fraction(len(frames)), 1.0)
        self.assertEqual(source.processed_fraction(FRAMES // 3), 1 / 3)
        for count in (-1, FRAMES + 1, 1.0, True):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "processed_frame_count_invalid"):
                source.processed_fraction(count)

    def test_catch_up_jumps_count_as_media_progress_but_not_as_processed_media(self):
        # The synthetic clock makes every frame 0.2 s late against 1/30 s of media, as in the catch-up test.
        source = ReplaySource(self.clip, 0, realtime=True, clock=SteppingClock(0.2))
        frames = self.play(source, FRAMES + 1)
        self.assertGreater(source.catch_ups, 0)
        # Every frame of the clip was emitted, dropped after decoding or skipped undecoded.
        self.assertEqual(source.frames_spanned, FRAMES)
        self.assertEqual(source.frames_spanned, source.sequence + source.frames_dropped + source.frames_skipped)
        processed = source.processed_fraction(len(frames))
        self.assertEqual(processed, len(frames) / FRAMES)
        # Media time advanced over most of the clip (the jumps), while far fewer frames were inferred on.
        progressed = source.media_seconds / ((FRAMES - 1) / FPS)
        self.assertGreater(progressed, 0.7)
        self.assertLess(processed, progressed / 2)

    def test_frames_decoded_after_a_catch_up_frame_in_one_call_count_as_dropped(self):
        # Frames 3 and 4 are held in the decoder and come out with frame 5; frame 3 is already
        # late enough to catch up, so all three are dropped. Sixty frames, keyframes every ten.
        decoder = FakeAv(held={3, 4}, frames=60)
        source = ReplaySource(Path("fake.mp4"), 0, realtime=True, clock=SteppingClock(0.25))
        with patch.dict(sys.modules, {"av": decoder}):
            frames = self.play(source, 61)
        self.assertEqual([round(frame.media_time * FPS) for frame in frames], [0, 1, 2, 30, 31, 32])
        self.assertEqual(source.catch_ups, 2)
        self.assertEqual((source.frames_decoded, source.frames_dropped, source.frames_skipped), (10, 4, 50))
        self.assertEqual(source.frames_spanned, 60)
        self.assertEqual(source.processed_fraction(len(frames)), 0.1)

    def test_pause_rebases_the_schedule_instead_of_catching_up(self):
        source = ReplaySource(self.clip, 0, realtime=True, clock=SteppingClock(0.0))
        stop = threading.Event()
        emitted = []

        def emit(frame):
            emitted.append(frame)
            if len(emitted) == 3:
                source.playing.clear()
                source.clock.wall += 60  # A long pause on the wall clock.
                threading.Timer(0.3, source.playing.set).start()
            if len(emitted) == 6:
                stop.set()

        thread = threading.Thread(target=source.run, args=(emit, stop), daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(emitted), 6)
        self.assertEqual(source.catch_ups, 0)
        self.assertEqual([frame.sequence for frame in emitted], list(range(1, 7)))


if __name__ == "__main__":
    unittest.main()
