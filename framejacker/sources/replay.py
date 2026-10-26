"""Recorded-file source: one selected video track decoded with PyAV."""
import threading
import time

from ..convert import processing_size
from ..frame import Frame


class SourceError(RuntimeError):
    pass


class PlaybackClock:
    """Map media time to wall time so late frames can be skipped and early ones delayed.

    ``lateness`` is positive when a frame is behind schedule. ``rebase`` restarts the
    schedule at a media time, for example after a pause or restart.
    """

    def __init__(self, now=time.monotonic):
        self.now = now
        self.origin = None

    def rebase(self):
        self.origin = None

    def lateness(self, media_time):
        wall = self.now()
        if self.origin is None:
            self.origin = wall - media_time
        return wall - (self.origin + media_time)


class ReplaySource:
    """Decode a video track to BGR, with thread-safe pause, restart and loop controls.

    Frames are scaled by the decoder so the longest side is at most 960 pixels.
    No left/right meaning is assumed for the track ordinal.

    With ``realtime`` the source follows media time: frames that arrive more than
    ``max_lateness`` seconds behind schedule (because processing was slower than the
    recording) are dropped and counted in ``frames_dropped``, and early frames wait.
    If nothing has been emitted for ``max_stall`` seconds (decoding itself is slower
    than real time) the next frame is emitted and the schedule resyncs to it, counted
    in ``resyncs``. Without ``realtime`` every frame is emitted, as before.
    """

    def __init__(self, path, ordinal, realtime=False, max_lateness=0.1, clock=None, max_stall=1.0):
        self.path, self.ordinal = path, ordinal
        self.realtime, self.max_lateness, self.max_stall = realtime, max_lateness, max_stall
        self.clock = clock or PlaybackClock()
        self.frames_dropped = 0
        self.resyncs = 0
        self.playing, self.restart = threading.Event(), threading.Event()
        self.looping, self.at_end = threading.Event(), threading.Event()
        self.playing.set()
        self.looping.set()
        self.stream_index = None
        self.sequence = 0

    def run(self, emit, stop):
        import av

        label = f"file:{self.path.name}#video{self.ordinal}"
        while not stop.is_set():
            self.restart.clear()
            self.at_end.clear()
            pass_frames = 0
            was_playing = False
            last_emit = self.clock.now()
            self.clock.rebase()
            with av.open(str(self.path), mode="r") as container:
                videos = list(container.streams.video)
                if self.ordinal >= len(videos):
                    raise SourceError("Selected video stream is absent")
                stream = videos[self.ordinal]
                self.stream_index = stream.index
                stream.codec_context.thread_count = 2
                for decoded in container.decode(stream):
                    if stop.is_set() or self.restart.is_set():
                        break
                    while not self.playing.wait(0.1):
                        was_playing = False
                        if stop.is_set():
                            return
                    if stop.is_set() or self.restart.is_set():
                        break
                    if decoded.is_corrupt or min(decoded.width, decoded.height) <= 0:
                        raise SourceError("Decoder returned an invalid frame")
                    media_time = (float(decoded.pts * decoded.time_base)
                                  if decoded.pts is not None and decoded.time_base is not None else None)
                    if self.realtime and media_time is not None:
                        if not was_playing:
                            self.clock.rebase()
                        lateness = self.clock.lateness(media_time)
                        if lateness < -0.002 and stop.wait(-lateness):
                            return
                        if lateness > self.max_lateness:
                            # Decoding slower than real time would otherwise drop every frame forever.
                            if self.clock.now() - last_emit < self.max_stall:
                                self.frames_dropped += 1
                                continue
                            self.resyncs += 1
                            self.clock.rebase()
                            self.clock.lateness(media_time)
                    was_playing = True
                    last_emit = self.clock.now()
                    width, height = processing_size(decoded.width, decoded.height)
                    image = decoded.reformat(width=width, height=height, format="bgr24").to_ndarray()
                    pass_frames += 1
                    self.sequence += 1
                    emit(Frame(image, "BGR24", width, height, image.strides[0], label, self.sequence,
                               time.monotonic_ns(), media_time))
            if stop.is_set():
                return
            if not pass_frames and not self.restart.is_set():
                raise SourceError("No video frames decoded")
            self.at_end.set()
            while not self.looping.is_set() and not self.restart.wait(0.1):
                if stop.is_set():
                    return
