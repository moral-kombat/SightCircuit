"""Recorded-file source: one selected video track decoded with PyAV."""
import threading
import time

from ..convert import processing_size
from ..frame import Frame


class SourceError(RuntimeError):
    pass


class ReplaySource:
    """Decode a video track to BGR, with thread-safe pause, restart and loop controls.

    Frames are scaled by the decoder so the longest side is at most 960 pixels.
    No left/right meaning is assumed for the track ordinal.
    """

    def __init__(self, path, ordinal):
        self.path, self.ordinal = path, ordinal
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
                        if stop.is_set():
                            return
                    if stop.is_set() or self.restart.is_set():
                        break
                    if decoded.is_corrupt or min(decoded.width, decoded.height) <= 0:
                        raise SourceError("Decoder returned an invalid frame")
                    width, height = processing_size(decoded.width, decoded.height)
                    image = decoded.reformat(width=width, height=height, format="bgr24").to_ndarray()
                    media_time = (float(decoded.pts * decoded.time_base)
                                  if decoded.pts is not None and decoded.time_base is not None else None)
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
