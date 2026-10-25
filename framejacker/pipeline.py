"""Source → conversion → detector → sinks, shared by file replay and live input.

Two scheduling modes keep the architecture's latency rule explicit:

* ``latest_only=True`` (live sources): the source runs on its own thread and
  publishes into a one-item slot; processing takes only the newest frame and
  discards replaced or too-old input rather than accumulating latency.
* ``latest_only=False`` (file replay): the source thread runs processing for
  each frame it emits, so every decoded frame is processed in order.

Both modes start processing at most ``max_fps`` times per second. When ``run``
returns or raises it has set ``stop``, so the source has been told to finish.
"""
from dataclasses import dataclass, field
import threading
import time
from typing import Callable, Protocol

from .convert import to_bgr
from .detection import annotate
from .frame import Frame, Processed
from .latest import LatestSlot


class FrameSource(Protocol):
    def run(self, emit: Callable[[Frame], None], stop) -> None:
        """Call ``emit`` with owned frames until exhausted or ``stop`` is set."""


class Sink(Protocol):
    def submit(self, processed: Processed) -> None:
        """Accept one processed frame; must not block the pipeline for long."""


class LatestResultSink:
    """Retain one latest result for a polling consumer such as a Qt timer."""

    def __init__(self):
        self.slot = LatestSlot()

    def submit(self, processed):
        self.slot.put(processed)

    def take(self):
        return self.slot.take(timeout=0)


@dataclass
class PipelineStats:
    frames_processed: int = 0
    stale_inputs_skipped: int = 0
    pending_replaced: int = 0
    total_inference_ms: float = 0.0
    max_inference_ms: float = 0.0
    total_processing_ms: float = 0.0
    max_input_age_ms: float = 0.0
    labels_seen: set = field(default_factory=set)
    started: float | None = None
    finished: float | None = None
    cancelled: bool = False

    @property
    def elapsed_s(self):
        if self.started is None:
            return 0.0
        return (self.finished if self.finished is not None else time.monotonic()) - self.started

    @property
    def mean_inference_ms(self):
        return self.total_inference_ms / max(self.frames_processed, 1)

    @property
    def mean_processing_ms(self):
        return self.total_processing_ms / max(self.frames_processed, 1)


class Pipeline:
    def __init__(self, source, detector, sinks=(), *, threshold=0.5, max_fps=5, latest_only=True,
                 max_input_age_ms=None, max_frames=0, join_timeout=1.0, convert=to_bgr, annotator=annotate):
        self.source, self.detector, self.sinks = source, detector, tuple(sinks)
        self.convert, self.annotator = convert, annotator
        self.threshold, self.period = threshold, 1 / max_fps
        self.latest_only, self.max_input_age_ms = latest_only, max_input_age_ms
        self.max_frames = max_frames  # Read per frame; zero means unlimited.
        self.join_timeout = join_timeout
        self.stats = PipelineStats()
        self.capture_thread_alive = False

    def run(self, stop):
        self.stats.started = time.monotonic()
        try:
            if self.latest_only:
                self._run_latest(stop)
            else:
                self._run_inline(stop)
        finally:
            self.stats.finished = time.monotonic()
        return self.stats

    def _limit_reached(self):
        return bool(self.max_frames) and self.stats.frames_processed >= self.max_frames

    def _process(self, frame, age_ms):
        started = time.monotonic()
        bgr = self.convert(frame)
        result = self.detector.detect(bgr, self.threshold)
        image = self.annotator(bgr, result)
        stats = self.stats
        stats.frames_processed += 1
        stats.total_inference_ms += result.inference_ms
        stats.max_inference_ms = max(stats.max_inference_ms, result.inference_ms)
        stats.max_input_age_ms = max(stats.max_input_age_ms, age_ms)
        stats.labels_seen.update(result.labels)
        processing_ms = (time.monotonic() - started) * 1000
        stats.total_processing_ms += processing_ms
        processed = Processed(frame, result, image, stats.frames_processed, age_ms, processing_ms)
        for sink in self.sinks:
            sink.submit(processed)

    def _run_inline(self, stop):
        def emit(frame):
            started = time.monotonic()
            self._process(frame, (time.monotonic_ns() - frame.observed_ns) / 1e6)
            if self._limit_reached():
                stop.set()
                return
            stop.wait(max(0, self.period - (time.monotonic() - started)))

        try:
            self.source.run(emit, stop)
            self.stats.cancelled = stop.is_set() and not self._limit_reached()
        finally:
            stop.set()

    def _run_latest(self, stop):
        slot = LatestSlot()
        failure = []

        def capture():
            try:
                self.source.run(slot.put, stop)
            except BaseException as error:
                failure.append(error)
            finally:
                slot.close()

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        try:
            next_start = time.monotonic()
            while not stop.is_set():
                now = time.monotonic()
                if now < next_start:
                    stop.wait(min(0.05, next_start - now))
                    continue
                frame = slot.take()
                if frame is None:
                    if slot.closed:
                        break
                    continue
                age_ms = (time.monotonic_ns() - frame.observed_ns) / 1e6
                if self.max_input_age_ms is not None and age_ms > self.max_input_age_ms:
                    self.stats.stale_inputs_skipped += 1
                    continue
                next_start = time.monotonic() + self.period
                self._process(frame, age_ms)
                if self._limit_reached():
                    break
            self.stats.cancelled = stop.is_set()
        finally:
            stop.set()
            thread.join(timeout=self.join_timeout)
            self.capture_thread_alive = thread.is_alive()
            self.stats.pending_replaced = slot.replaced
        if failure:
            raise failure[0]
