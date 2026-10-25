"""Adapter from a callback-driven raw acquisition loop to pipeline frames.

The SteamVR mirror's V4L2 handling and bounded acquisition loop live in
``scripts/mirror_capture.py`` and ``scripts/mirror_check.py``. This adapter
keeps that device code behind the source boundary: it turns each raw read into
an owned ``Frame`` and retains the acquisition report for the caller.
"""
import threading

from ..frame import Frame


class MirrorSource:
    def __init__(self, acquire, source="steamvr-mirror"):
        """``acquire(stop, on_frame, progress)`` reads until done and returns its report.

        ``on_frame(data, fmt, number, observed_ns)`` receives owned bytes and the
        validated V4L2 format dictionary; ``progress(report)`` receives snapshots.
        """
        self.acquire, self.source = acquire, source
        self.lock = threading.Lock()
        self._report = {}

    @property
    def report(self):
        with self.lock:
            return self._report.copy()

    def _update(self, report):
        with self.lock:
            self._report = report.copy()

    def run(self, emit, stop):
        def on_frame(data, fmt, number, observed_ns):
            emit(Frame(bytes(data), fmt["fourcc"], fmt["width"], fmt["height"], fmt["stride_bytes"],
                       self.source, number, observed_ns, metadata=dict(fmt)))

        self._update(self.acquire(stop, on_frame, self._update))
