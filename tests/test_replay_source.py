"""Replay source decoding order, keyframe catch-up and pause scheduling with a fake clock."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest

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
