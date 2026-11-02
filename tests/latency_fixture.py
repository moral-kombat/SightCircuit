"""A fake detector with known delays and the stepping clock it drives, for latency statistics tests.

``KnownDelays`` reports each delay as its ``inference_ms`` and advances its own
clock by it, plus an optional unreported ``overhead_ms``. Installed as
``Pipeline.timer`` (``staticmethod(known.clock)``), that clock makes the
engine's detect stage equal the delays plus the overhead, and processing time
equal the detect stage, because every other stage takes zero time on it.
Imported by the tests and by the replay subprocess they start.
"""
import os
import signal

from framejacker.frame import DetectionResult

# 5, 40, 75, 10, ... ms: each multiple of 5 ms from 5 to 100 once, in a scrambled order.
DELAYS_MS = tuple(5.0 * ((7 * index) % 20 + 1) for index in range(20))
# Nearest-rank percentiles of DELAYS_MS (rank = ceil(p * 20 / 100)): the 10th and 19th of 20 sorted values.
EXPECTED = {"p50": 50.0, "p95": 95.0, "max": 100.0}
HOLD_SECONDS = 30  # Bound on a held detector call.


class KnownDelays:
    """Cycle through ``DELAYS_MS``; optionally stop, fail or interrupt the run on a given call.

    ``stop`` is set on call ``stop_after``; calls after ``fail_after`` raise.
    On call ``interrupt_after`` this process receives SIGINT, as from Ctrl+C,
    and with ``hold`` that call then waits for the event (at most
    ``HOLD_SECONDS``) before it returns, so it outlasts a shutdown wait.
    """
    name, runtime, classes = "known-delays", "fixture", ("fixture",)

    def __init__(self, stop=None, stop_after=0, fail_after=0, overhead_ms=0.0, interrupt_after=0, hold=None):
        self.stop, self.stop_after, self.fail_after = stop, stop_after, fail_after
        self.overhead_ms, self.interrupt_after, self.hold = overhead_ms, interrupt_after, hold
        self.calls, self.now = 0, 1000.0

    def clock(self):
        return self.now

    def detect(self, image, threshold):
        if self.fail_after and self.calls >= self.fail_after:
            raise RuntimeError("known-delays detector failed")
        delay = DELAYS_MS[self.calls % len(DELAYS_MS)]
        self.calls += 1
        self.now += (delay + self.overhead_ms) / 1000
        if self.stop is not None and self.calls == self.stop_after:
            self.stop.set()
        if self.interrupt_after and self.calls == self.interrupt_after:
            os.kill(os.getpid(), signal.SIGINT)
            if self.hold is not None:
                self.hold.wait(HOLD_SECONDS)
        return DetectionResult((), delay)
