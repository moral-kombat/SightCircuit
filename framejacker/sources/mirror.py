"""Adapter from a callback-driven raw acquisition loop to pipeline frames.

The SteamVR mirror's V4L2 handling and bounded acquisition loop live in
``scripts/mirror_capture.py`` and ``scripts/mirror_check.py``. This adapter
keeps that device code behind the source boundary: it turns each raw read into
an owned ``Frame`` carrying its ``native_format`` and retains the acquisition
report for the caller. An acquisition report with status ``error`` raises, so
the pipeline stops presenting results from a failed capture.
"""
import threading

from ..context import SOURCE_FIELDS
from ..errors import FramejackerError
from ..frame import Frame


class MirrorSource:
    def __init__(self, acquire, source="steamvr-mirror", *, identity=None):
        """``acquire(stop, on_frame, progress)`` reads until done and returns its report.

        ``on_frame(data, fmt, number, observed_ns)`` receives the read bytes and the
        validated V4L2 format dictionary; ``progress(report)`` receives snapshots.
        ``identity``, when given, declares ``SOURCE_FIELDS`` (for example a fixture
        backend's kind, source_id and capture_layout) to ``CaptureContext``.
        """
        self.acquire, self.source, self.identity = acquire, source, identity
        self.lock = threading.Lock()
        self._report = {}

    def __getattr__(self, name):
        if name in SOURCE_FIELDS:
            return getattr(self.__dict__.get("identity"), name, None)
        raise AttributeError(name)

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
                       self.source, number, observed_ns, metadata={"native_format": dict(fmt)}))

        report = self.acquire(stop, on_frame, self._update)
        self._update(report)
        if report.get("status") == "error":
            raise FramejackerError(report.get("reason", "capture_failed"), report.get("error", "Capture failed"))
