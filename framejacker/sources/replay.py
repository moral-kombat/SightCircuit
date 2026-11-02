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
    No left/right meaning is assumed for the track ordinal. Decoding uses FFmpeg
    frame and slice threading with ``decode_threads`` threads.

    Without ``realtime`` every frame is emitted in order. With ``realtime`` the
    source follows media time: early frames wait for their scheduled time. When
    decoding falls more than ``catch_up_after`` seconds behind schedule, packets
    are skipped without decoding until the next keyframe at or after the current
    schedule position, so playback keeps real-time progress even when the
    decoder cannot keep up with the recording (``catch_ups``/``frames_skipped``).
    Pair it with a latest-only pipeline so slow inference never stalls decoding.

    ``media_seconds`` counts catch-up jumps as progress. ``frames_spanned``
    counts the source frames the played span covered instead: each one was
    emitted, dropped after decoding or skipped undecoded, so
    ``processed_fraction`` shows how much of that media was inferred on.
    """

    def __init__(self, path, ordinal, realtime=False, catch_up_after=0.5, clock=None, decode_threads=2):
        self.path, self.ordinal = path, ordinal
        self.realtime, self.catch_up_after, self.decode_threads = realtime, catch_up_after, decode_threads
        self.clock = clock or PlaybackClock()
        self.frames_decoded = self.frames_dropped = self.frames_skipped = self.catch_ups = 0
        self.media_seconds = 0.0  # Media time advanced by emitted frames, including catch-up jumps.
        self.playing, self.restart = threading.Event(), threading.Event()
        self.looping, self.at_end = threading.Event(), threading.Event()
        self.playing.set()
        self.looping.set()
        self.stream_index = None
        self.sequence = 0  # Frames emitted.

    @property
    def frames_spanned(self):
        """Source frames in the played media span: emitted, dropped after decoding or skipped undecoded."""
        return self.sequence + self.frames_dropped + self.frames_skipped

    def processed_fraction(self, frames_processed):
        """``frames_processed`` (frames inferred on) divided by ``frames_spanned``; None before any frame.

        1.0 when every frame is processed. Unlike ``media_seconds``, a catch-up
        jump adds the frames it passes over to the span, not progress.
        """
        if type(frames_processed) is not int or not 0 <= frames_processed <= self.sequence:
            raise ValueError("processed_frame_count_invalid")
        spanned = self.frames_spanned
        return frames_processed / spanned if spanned else None

    def run(self, emit, stop):
        import av

        label = f"file:{self.path.name}#video{self.ordinal}"
        while not stop.is_set():
            self.restart.clear()
            self.at_end.clear()
            pass_frames = 0
            rebase = True
            skip_until = discard_before = previous_media = None
            with av.open(str(self.path), mode="r") as container:
                videos = list(container.streams.video)
                if self.ordinal >= len(videos):
                    raise SourceError("Selected video stream is absent")
                stream = videos[self.ordinal]
                self.stream_index = stream.index
                stream.thread_type = "AUTO"
                stream.codec_context.thread_count = self.decode_threads
                for packet in container.demux(stream):
                    if stop.is_set() or self.restart.is_set():
                        break
                    while not self.playing.wait(0.1):
                        rebase = True  # Resume on schedule instead of catching up the pause.
                        if stop.is_set():
                            return
                    if stop.is_set() or self.restart.is_set():
                        break
                    if skip_until is not None and packet.size:
                        when = (float(packet.pts * packet.time_base)
                                if packet.pts is not None and packet.time_base is not None else None)
                        if not packet.is_keyframe or when is None or when < skip_until:
                            self.frames_skipped += 1
                            continue
                        skip_until = None
                    decoded_frames = list(stream.decode(packet))
                    for position, decoded in enumerate(decoded_frames):
                        self.frames_decoded += 1
                        media_time = (float(decoded.pts * decoded.time_base)
                                      if decoded.pts is not None and decoded.time_base is not None else None)
                        if discard_before is not None:
                            # Frames still in the decoder from before a catch-up, or leading
                            # pictures that reference skipped data.
                            if media_time is None or media_time < discard_before or decoded.is_corrupt:
                                self.frames_dropped += 1
                                continue
                            discard_before = None
                        if decoded.is_corrupt or min(decoded.width, decoded.height) <= 0:
                            raise SourceError("Decoder returned an invalid frame")
                        lateness = None
                        if self.realtime and media_time is not None:
                            if rebase:
                                self.clock.rebase()
                                rebase = False
                            lateness = self.clock.lateness(media_time)
                            if lateness > self.catch_up_after:
                                skip_until = discard_before = media_time + lateness
                                self.catch_ups += 1
                                # This frame and any the same call decoded after it are dropped.
                                later = len(decoded_frames) - position - 1
                                self.frames_decoded += later
                                self.frames_dropped += 1 + later
                                break
                            if lateness < -0.002 and stop.wait(-lateness):
                                return
                        if stop.is_set() or self.restart.is_set():
                            break
                        width, height = processing_size(decoded.width, decoded.height)
                        image = decoded.reformat(width=width, height=height, format="bgr24").to_ndarray()
                        if media_time is not None and previous_media is not None and media_time > previous_media:
                            self.media_seconds += media_time - previous_media
                        previous_media = media_time
                        pass_frames += 1
                        self.sequence += 1
                        metadata = {} if lateness is None else {"schedule_lateness_ms": round(lateness * 1000, 3)}
                        emit(Frame(image, "BGR24", width, height, image.strides[0], label, self.sequence,
                                   time.monotonic_ns(), media_time, metadata))
            if stop.is_set():
                return
            if not pass_frames and not self.restart.is_set():
                raise SourceError("No video frames decoded")
            self.at_end.set()
            while not self.looping.is_set() and not self.restart.wait(0.1):
                if stop.is_set():
                    return
