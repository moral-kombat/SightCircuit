"""Adapter from an ``open``/``read``/``close`` source to the pipeline's ``run(emit, stop)``.

The wrapped source keeps its declared identity: ``CaptureContext`` reads
``SOURCE_FIELDS`` (kind, source_id, capture_layout, ...) through this adapter,
so a change on the wrapped object is still observed.
"""
from ..context import SOURCE_FIELDS


class PolledSource:
    def __init__(self, source, read_timeout=0.05):
        self.source, self.read_timeout = source, read_timeout
        self.cleanup_ok = None  # True only when close() returned True (or nothing was opened).
        self.error = None

    def __getattr__(self, name):
        if name in SOURCE_FIELDS:
            return getattr(self.__dict__["source"], name, None)
        raise AttributeError(name)

    def run(self, emit, stop):
        if stop.is_set():
            self.cleanup_ok = True
            return
        try:
            self.source.open()
            while not stop.is_set():
                frame = self.source.read(stop, timeout=self.read_timeout)
                if frame is not None:
                    emit(frame)
        except BaseException as error:
            self.error = str(error)[:1024]
            raise
        finally:
            try:
                self.cleanup_ok = self.source.close() is True
            except Exception as error:
                self.cleanup_ok = False
                if self.error is None:
                    self.error = str(error)[:1024]
                    raise
