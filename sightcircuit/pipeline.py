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
optional ``seconds`` deadline or the HUD calibration's ``expires_ns``. With a
HUD stage that age may not exceed the contract's ``MAX_DISPLAY_AGE_MS``. Once a
presented result expires, sinks that implement ``clear()`` are cleared even if
the next inference is still running.

Sinks implement ``submit(processed)``; ``open()``, ``clear()`` and
``close() -> True`` are optional and are always called on cleanup, including
after failures. The engine approves no source: callers keep their gates.

With a HUD stage the engine counts its exclusion checks (``HUD_CHECK_FIELDS``):
frames whose masked footprint was checked, footprint pixels left non-zero
(a frame with any stops the run before the detector sees it), and boxes the
stage dropped for touching the mask or crossing an eye.

Every frame that reaches presentation is timed (``TIMINGS`` and ``STAGES``):
input age, processing and inference time, output age, and the prepare, detect,
present and submit stages. ``PipelineStats.report()`` gives each as a count,
nearest-rank p50 and p95 over a bounded sample, and the exact maximum. Stage
durations are read from ``Pipeline.timer`` (seconds, ``time.monotonic``); ages
are measured against frames' ``observed_ns`` on ``time.monotonic_ns``.
"""
from dataclasses import dataclass, field
import math
import random
import threading
import time
from typing import Callable, Protocol

from .context import CaptureContext
from .convert import to_bgr
from .detection import POC_THRESHOLD, annotate, require_result
from .frame import Frame, Processed
from .hud.contract import MAX_DISPLAY_AGE_MS
from .latest import LatestSlot

# PipelineStats.hud_checks: HUD exclusion counts over the run.
HUD_CHECK_FIELDS = ("frames_checked", "unmasked_footprint_px", "mask_zeroing_failures",
                    "mask_overlap_rejections", "eye_crossing_rejections")
# Per-frame timings, each a report block of its own: "input_age_ms" from observation to processing start,
# "processing_ms" from then to a presented result, the detector's own "inference_ms", and "output_age_ms"
# from observation to the output boundary.
TIMINGS = ("input_age_ms", "processing_ms", "inference_ms", "output_age_ms")
# Engine stages ("stage_ms"): prepare (conversion, or HUD masking and conversion), detect (the whole
# detector call), present (result checks and annotation or HUD rendering) and submit (every sink).
# prepare + detect + present = processing_ms.
STAGES = ("prepare", "detect", "present", "submit")
PERCENTILE_METHOD = "nearest_rank"
TIMING_SAMPLE_CAPACITY = 4096  # Values kept per timing for percentiles; count and max use every frame.


def nearest_rank(ordered, percent):
    """The nearest-rank ``percent`` percentile of the ascending list ``ordered``, or None when it is empty.

    The value at 1-based rank ceil(percent * n / 100): the smallest kept value
    with at least ``percent``% of values at or below it. It is always one of the
    values, never an interpolation, so n = 1 gives that value for every
    percentile and p100 is the maximum. ``percent`` is an integer from 1 to 100.
    """
    if type(percent) is not int or not 1 <= percent <= 100:
        raise ValueError("percentile_out_of_range")
    if not ordered:
        return None
    return ordered[-(-percent * len(ordered) // 100) - 1]


class TimingSample:
    """One timing's frame count, exact maximum and a bounded sample for its percentiles.

    The first ``capacity`` values are all kept, so percentiles are exact up to
    there. Beyond it, reservoir sampling keeps a uniform sample of the whole run:
    value number k replaces a random kept value with probability capacity / k.
    The generator is seeded, so one sequence of values always gives one report.
    """

    def __init__(self, capacity=TIMING_SAMPLE_CAPACITY):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("timing_sample_capacity_invalid")
        self.capacity, self.values, self.count, self.maximum = capacity, [], 0, None
        self._random = random.Random(0)

    def add(self, value):
        self.count += 1
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        if len(self.values) < self.capacity:
            self.values.append(value)
            return
        slot = self._random.randrange(self.count)
        if slot < self.capacity:
            self.values[slot] = value

    def report(self):
        """``count`` frames timed, ``samples`` behind p50/p95 and the maximum, in rounded milliseconds."""
        ordered = sorted(self.values)

        def ms(value):
            return None if value is None else round(value, 3)
        return {"count": self.count, "samples": len(ordered), "p50": ms(nearest_rank(ordered, 50)),
                "p95": ms(nearest_rank(ordered, 95)), "max": ms(self.maximum)}


def timing_samples(names):
    return {name: TimingSample() for name in names}


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
    hud_checks: dict | None = None  # HUD_CHECK_FIELDS counts with a HUD stage; None (unknown) without one.
    timings: dict = field(default_factory=lambda: timing_samples(TIMINGS))
    stages: dict = field(default_factory=lambda: timing_samples(STAGES))

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

    def record(self, **values_ms):
        """Add one frame's milliseconds to the named ``TIMINGS`` or ``STAGES`` samples."""
        for name, value in values_ms.items():
            (self.timings[name] if name in self.timings else self.stages[name]).add(value)

    def report(self):
        """Run statistics in the engine's own vocabulary, embedded in reports as "pipeline".

        Durations are milliseconds rounded to three decimals; ``processed_fps``
        divides by at least 1 ms. Commands keep their earlier field names only
        as aliases of these (``report_fields``). Which reports embed the block,
        and when, is up to each command; see ``docs/development.md``.
        ``hud_checks`` is null without a HUD stage. Each ``TIMINGS`` name and
        each ``stage_ms`` stage holds ``count``, ``samples``, nearest-rank
        ``p50`` and ``p95`` and ``max``; the statistics are null before a frame is timed.
        """
        def ms(value):
            return round(value, 3)
        return {"frames_received": self.frames_received, "frames_processed": self.frames_processed,
                "frames_submitted": self.frames_submitted, "stale_inputs_skipped": self.stale_inputs_skipped,
                "stale_results_skipped": self.stale_results_skipped, "pending_replaced": self.pending_replaced,
                "total_inference_ms": ms(self.total_inference_ms), "mean_inference_ms": ms(self.mean_inference_ms),
                "max_inference_ms": ms(self.max_inference_ms), "total_processing_ms": ms(self.total_processing_ms),
                "mean_processing_ms": ms(self.mean_processing_ms), "max_input_age_ms": ms(self.max_input_age_ms),
                "max_output_age_ms": ms(self.max_output_age_ms), "elapsed_ms": ms(self.elapsed_s * 1000),
                "processed_fps": round(self.frames_processed / max(self.elapsed_s, 1e-3), 3),
                "labels_seen": sorted(self.labels_seen), "cancelled": self.cancelled,
                "sink_clear_ok": self.sink_clear_ok, "sink_close_ok": self.sink_close_ok,
                "hud_checks": dict(self.hud_checks) if self.hud_checks is not None else None,
                **{name: sample.report() for name, sample in self.timings.items()},
                "stage_ms": {name: sample.report() for name, sample in self.stages.items()},
                "percentile_method": PERCENTILE_METHOD}


def report_fields(block, aliases):
    """A command's legacy report fields, derived from a ``PipelineStats.report()`` block.

    ``aliases`` maps each legacy name to a block key, or to a function of the block.
    """
    return {name: source(block) if callable(source) else block[source] for name, source in aliases.items()}


class Pipeline:
    timer = staticmethod(time.monotonic)  # Seconds for stage durations; tests install a stepping clock.

    def __init__(self, source, detector, sinks=(), *, threshold=POC_THRESHOLD, max_fps=5, latest_only=True,
                 max_input_age_ms=None, max_frames=0, seconds=None, join_timeout=1.0, convert=to_bgr,
                 annotator=annotate, context_validator=None, hud=None):
        if (not math.isfinite(max_fps) or max_fps <= 0 or
                (max_input_age_ms is not None and
                 (not math.isfinite(max_input_age_ms) or max_input_age_ms <= 0)) or
                (seconds is not None and (not math.isfinite(seconds) or seconds <= 0))):
            raise ValueError("invalid_pipeline_limits")
        if hud is not None and (max_input_age_ms is None or not latest_only):
            raise ValueError("hud_requires_latest_only_bounded_age")
        if hud is not None and max_input_age_ms > MAX_DISPLAY_AGE_MS:  # One display cap for every HUD result.
            raise ValueError("hud_max_age_exceeds_contract")
        self.source, self.detector, self.sinks = source, detector, tuple(sinks)
        self.convert, self.annotator, self.hud = convert, annotator, hud
        self.threshold, self.period = threshold, 1 / max_fps
        self.latest_only, self.max_input_age_ms = latest_only, max_input_age_ms
        self.max_frames = max_frames  # Read per frame; zero means unlimited.
        self.seconds, self.deadline = seconds, None
        self.join_timeout = join_timeout
        self.stats = PipelineStats(hud_checks=dict.fromkeys(HUD_CHECK_FIELDS, 0) if hud is not None else None)
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

    def _input_age(self, frame, now_ns=None):
        age_ms = ((time.monotonic_ns() if now_ns is None else now_ns) - frame.observed_ns) / 1e6
        if age_ms < 0:
            raise ValueError("frame_observation_is_in_the_future")
        return age_ms

    def _expiry_ns(self, frame):
        if self.max_input_age_ms is None:
            return None
        expires = frame.observed_ns + int(self.max_input_age_ms * 1e6)
        calibration_expiry = getattr(self.hud, "expires_ns", None)
        if calibration_expiry is not None:
            expires = min(expires, calibration_expiry)  # A HUD result never outlives its calibration.
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
        if self.hud is None:
            return self.convert(frame)
        image, unmasked = self.hud.prepare(frame)
        checks = self.stats.hud_checks
        checks["frames_checked"] += 1
        checks["unmasked_footprint_px"] += unmasked
        if unmasked:  # The detector never sees a footprint pixel the mask left behind.
            checks["mask_zeroing_failures"] += 1
            raise ValueError("hud_mask_zeroing_failed")
        return image

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
        started = self.timer()
        bgr = self._prepare(frame)
        prepared = self.timer()
        result = self.detector.detect(bgr, self.threshold)
        detected = self.timer()
        stats.frames_processed += 1
        if capture_failure:
            raise capture_failure[0]
        self._admit(frame)
        require_result(result)
        image, hud_output = self._present(bgr, result)
        if hud_output is not None:
            stats.hud_checks["mask_overlap_rejections"] += hud_output.checks.mask_overlap_rejections
            stats.hud_checks["eye_crossing_rejections"] += hud_output.checks.eye_crossing_rejections
        presented = self.timer()
        processing_ms = (presented - started) * 1000
        stats.total_inference_ms += result.inference_ms
        stats.max_inference_ms = max(stats.max_inference_ms, result.inference_ms)
        stats.max_input_age_ms = max(stats.max_input_age_ms, age_ms)
        stats.total_processing_ms += processing_ms
        stats.record(input_age_ms=age_ms, processing_ms=processing_ms, inference_ms=result.inference_ms,
                     prepare=(prepared - started) * 1000, detect=(detected - prepared) * 1000,
                     present=(presented - detected) * 1000)
        # Rendering and ownership copies take time: validate again at the output boundary.
        if capture_failure:
            raise capture_failure[0]
        self._admit(frame)
        completed_ns = time.monotonic_ns()
        output_age = self._input_age(frame, completed_ns)
        stats.max_output_age_ms = max(stats.max_output_age_ms, output_age)
        stats.record(output_age_ms=output_age)
        expires_ns = self._expiry_ns(frame)
        if self._expired(expires_ns, stop):
            stats.stale_results_skipped += 1
            return
        shape = getattr(bgr, "shape", None)
        processed = Processed(frame, result, image, stats.frames_processed, age_ms, processing_ms,
                              expires_ns, output_age,
                              (shape[1], shape[0]) if shape is not None else None,
                              (shape[1] / frame.width, shape[0] / frame.height) if shape is not None else None,
                              hud=hud_output, completed_ns=completed_ns)
        submitting = self.timer()
        for sink in self.sinks:
            if capture_failure:
                raise capture_failure[0]
            self._admit(frame)
            if self._expired(expires_ns, stop):
                stats.stale_results_skipped += 1
                return
            sink.submit(processed)
        stats.record(submit=(self.timer() - submitting) * 1000)
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
                    # frame and every checkpoint sees the same pixels and layout. Anything
                    # else is left as-is: a capture context, when present, rejects it with
                    # its own reason, and otherwise the frame fails later as before.
                    if isinstance(frame, Frame):
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
                self.stats.pending_replaced = slot.replaced  # Current for progress snapshots.
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
