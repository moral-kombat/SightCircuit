"""The one recognition engine: source → admission → masking or conversion → detector → sinks.

File replay, the bounded SteamVR mirror recognizer and the synthetic live HUD
run all use ``Pipeline``. Two scheduling modes keep the architecture's latency
rule explicit:

* ``latest_only=True`` (live sources): the source runs on its own thread and
  publishes into a one-item slot; processing takes only the newest frame and
  discards replaced or too-old input rather than accumulating latency.
* ``latest_only=False`` (file replay): the source thread runs processing for
  each frame it emits, so every decoded frame is processed in order.

Both modes start processing at most ``max_fps`` times per second. When ``run``
returns or raises it has set ``stop``, so the source has been told to finish.

Live runs set ``max_input_age_ms``. They then bind a ``CaptureContext`` (with
the HUD calibration when ``hud`` is given) and admit every frame when capture
publishes it, before inference, after inference and before each sink. A result
expires at its original observation time plus that age, and never after the
optional ``seconds`` deadline. Once a presented result expires, sinks that
implement ``clear()`` are cleared even if the next inference is still running.

Sinks implement ``submit(processed)``; ``open()``, ``clear()`` and
``close() -> True`` are optional and are always called on cleanup, including
after failures. The engine approves no source: callers keep their gates.
"""
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Callable, Protocol

from .context import CaptureContext
from .convert import to_bgr
from .detection import annotate, require_result
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
    frames_received: int = 0
    frames_processed: int = 0
    stale_inputs_skipped: int = 0
    stale_results_skipped: int = 0
    frames_submitted: int = 0
    pending_replaced: int = 0
    total_inference_ms: float = 0.0
    max_inference_ms: float = 0.0
    total_processing_ms: float = 0.0
    max_input_age_ms: float = 0.0
    max_output_age_ms: float = 0.0
    labels_seen: set = field(default_factory=set)
    last: Processed | None = None  # The most recently submitted result.
    hud_report: dict = field(default_factory=dict)
    sink_clear_ok: bool = True
    sink_close_ok: bool = True
    sink_errors: list = field(default_factory=list)  # (operation, message) pairs.
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

    @property
    def sink_cleanup_ok(self):
        return self.sink_clear_ok and self.sink_close_ok


class Pipeline:
    def __init__(self, source, detector, sinks=(), *, threshold=0.5, max_fps=5, latest_only=True,
                 max_input_age_ms=None, max_frames=0, seconds=None, join_timeout=1.0, convert=to_bgr,
                 annotator=annotate, context_validator=None, hud=None):
        if (not math.isfinite(max_fps) or max_fps <= 0 or
                (max_input_age_ms is not None and
                 (not math.isfinite(max_input_age_ms) or max_input_age_ms <= 0)) or
                (seconds is not None and (not math.isfinite(seconds) or seconds <= 0))):
            raise ValueError("invalid_pipeline_limits")
        if hud is not None and (max_input_age_ms is None or not latest_only):
            raise ValueError("hud_requires_latest_only_bounded_age")
        self.source, self.detector, self.sinks = source, detector, tuple(sinks)
        self.convert, self.annotator, self.hud = convert, annotator, hud
        self.threshold, self.period = threshold, 1 / max_fps
        self.latest_only, self.max_input_age_ms = latest_only, max_input_age_ms
        self.max_frames = max_frames  # Read per frame; zero means unlimited.
        self.seconds, self.deadline = seconds, None
        self.join_timeout = join_timeout
        self.stats = PipelineStats()
        self.capture_thread_started = False
        self.capture_thread_alive = False
        self.context_validator = context_validator
        self.capture_context = None
        self.last_sequence = None
        self._shown_until_ns = None

    def run(self, stop):
        self.stats.started = time.monotonic()
        try:
            if self.hud is not None:
                self.stats.hud_report = dict(self.hud.start())
            # Live runs pin the source's declared identity before it emits anything.
            self.capture_context = (CaptureContext(self.source, mask=getattr(self.hud, "mask", None))
                                    if self.max_input_age_ms is not None else None)
            for sink in self.sinks:
                opener = getattr(sink, "open", None)
                if opener is not None:
                    opener()
            if self.seconds is not None:
                self.deadline = time.monotonic() + self.seconds
            if self.latest_only:
                self._run_latest(stop)
            else:
                self._run_inline(stop)
        finally:
            stop.set()
            self._close_sinks()
            self.stats.finished = time.monotonic()
        return self.stats

    def _close_sinks(self):
        """Withdraw presented output and release every sink, recording failures."""
        stats = self.stats
        for sink in self.sinks:
            clear = getattr(sink, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception as error:
                    stats.sink_clear_ok = False
                    stats.sink_errors.append(("clear", str(error)[:1024]))
            close = getattr(sink, "close", None)
            if close is not None:
                try:
                    if close() is not True:
                        stats.sink_close_ok = False
                except Exception as error:
                    stats.sink_close_ok = False
                    stats.sink_errors.append(("close", str(error)[:1024]))

    def _limit_reached(self):
        return bool(self.max_frames) and self.stats.frames_processed >= self.max_frames

    def _past_deadline(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _admit(self, frame):
        """The single admission checkpoint: capture identity, then any caller check."""
        if self.capture_context is not None:
            self.capture_context.admit(frame)
        if self.context_validator is not None:
            self.context_validator(frame)

    def _input_age(self, frame):
        age_ms = (time.monotonic_ns() - frame.observed_ns) / 1e6
        if age_ms < 0:
            raise ValueError("frame_observation_is_in_the_future")
        return age_ms

    def _expiry_ns(self, frame):
        if self.max_input_age_ms is None:
            return None
        expires = frame.observed_ns + int(self.max_input_age_ms * 1e6)
        return min(expires, int(self.deadline * 1e9)) if self.deadline is not None else expires

    def _expired(self, expires_ns, stop):
        return stop.is_set() or (expires_ns is not None and time.monotonic_ns() >= expires_ns)

    def _expire_presented(self):
        """Clear sinks once the last presented result has expired."""
        if self._shown_until_ns is not None and time.monotonic_ns() >= self._shown_until_ns:
            self._shown_until_ns = None
            for sink in self.sinks:
                clear = getattr(sink, "clear", None)
                if clear is not None:
                    clear()

    def _prepare(self, frame):
        if self.hud is not None:
            return self.hud.prepare(frame)
        return self.convert(frame)

    def _present(self, bgr, result):
        if self.hud is not None:
            return self.hud.render(result, bgr.shape[:2], self.threshold)
        image = self.annotator(bgr, result)
        return (image.copy() if hasattr(image, "copy") else image), None

    def _process(self, frame, age_ms, stop, capture_failure=()):
        stats = self.stats
        self._admit(frame)
        if self.max_input_age_ms is not None:
            if self.last_sequence is not None and frame.sequence <= self.last_sequence:
                raise ValueError("frame_sequence_regressed")
            self.last_sequence = frame.sequence
        if self.max_input_age_ms is not None and age_ms >= self.max_input_age_ms:
            stats.stale_inputs_skipped += 1
            return
        started = time.monotonic()
        bgr = self._prepare(frame)
        result = self.detector.detect(bgr, self.threshold)
        stats.frames_processed += 1
        if capture_failure:
            raise capture_failure[0]
        self._admit(frame)
        require_result(result)
        image, hud_output = self._present(bgr, result)
        processing_ms = (time.monotonic() - started) * 1000
        stats.total_inference_ms += result.inference_ms
        stats.max_inference_ms = max(stats.max_inference_ms, result.inference_ms)
        stats.max_input_age_ms = max(stats.max_input_age_ms, age_ms)
        stats.total_processing_ms += processing_ms
        # Rendering and ownership copies take time: validate again at the output boundary.
        if capture_failure:
            raise capture_failure[0]
        self._admit(frame)
        output_age = self._input_age(frame)
        stats.max_output_age_ms = max(stats.max_output_age_ms, output_age)
        expires_ns = self._expiry_ns(frame)
        if self._expired(expires_ns, stop):
            stats.stale_results_skipped += 1
            return
        shape = getattr(bgr, "shape", None)
        processed = Processed(frame, result, image, stats.frames_processed, age_ms, processing_ms,
                              expires_ns, output_age,
                              (shape[1], shape[0]) if shape is not None else None,
                              (shape[1] / frame.width, shape[0] / frame.height) if shape is not None else None,
                              hud=hud_output)
        for sink in self.sinks:
            if capture_failure:
                raise capture_failure[0]
            self._admit(frame)
            if self._expired(expires_ns, stop):
                stats.stale_results_skipped += 1
                return
            sink.submit(processed)
        stats.frames_submitted += 1
        stats.labels_seen.update(processed.labels)
        stats.last = processed
        self._shown_until_ns = expires_ns

    def _run_inline(self, stop):
        def emit(frame):
            started = time.monotonic()
            if stop.is_set():
                return
            self._process(frame, self._input_age(frame), stop)
            if self._limit_reached() or self._past_deadline():
                stop.set()
                return
            stop.wait(max(0, self.period - (time.monotonic() - started)))

        try:
            self.source.run(emit, stop)
            self.stats.cancelled = stop.is_set() and not self._limit_reached() and not self._past_deadline()
        finally:
            stop.set()

    def _run_latest(self, stop):
        slot = LatestSlot()
        failure = []

        def capture():
            captured_sequence = None
            try:
                def publish(frame):
                    nonlocal captured_sequence
                    # The slot holds a private copy, so a source cannot change a queued
                    # frame and every checkpoint sees the same pixels and layout.
                    frame = frame.owned()
                    # A context change observed by capture latches the context's
                    # failure, so an older in-flight inference cannot reach a sink.
                    self._admit(frame)
                    if self.max_input_age_ms is not None:
                        if type(frame.sequence) is not int or frame.sequence < 0:
                            raise ValueError("invalid_frame_sequence")
                        if captured_sequence is not None and frame.sequence <= captured_sequence:
                            raise ValueError("frame_sequence_regressed")
                        captured_sequence = frame.sequence
                    slot.put(frame)
                    self.stats.frames_received += 1
                self.source.run(publish, stop)
            except BaseException as error:
                failure.append(error)
            finally:
                slot.close()

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        self.capture_thread_started = True
        try:
            next_start = time.monotonic()
            while not stop.is_set() and not failure and not self._past_deadline():
                self._expire_presented()
                now = time.monotonic()
                if now < next_start:
                    stop.wait(min(0.05, next_start - now))
                    continue
                remaining = self.deadline - now if self.deadline is not None else 0.05
                frame = slot.take(timeout=max(0, min(0.05, remaining)))
                if frame is None:
                    if slot.closed:
                        break
                    continue
                age_ms = self._input_age(frame)
                if self.max_input_age_ms is not None and age_ms > self.max_input_age_ms:
                    self.stats.stale_inputs_skipped += 1
                    continue
                next_start = time.monotonic() + self.period
                self._process(frame, age_ms, stop, failure)
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
