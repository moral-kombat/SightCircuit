"""One-pending-item handoff: newer items replace older ones instead of queueing."""
import threading

from .frame import OwnedFrame


class LatestSlot:
    def __init__(self):
        self.condition = threading.Condition()
        self.item = None
        self.replaced = 0
        self.closed = False

    def put(self, item):
        with self.condition:
            if self.item is not None:
                self.replaced += 1
            self.item = item
            self.condition.notify()

    def take(self, timeout=0.05):
        """Return the pending item, waiting up to ``timeout``; None if still empty."""
        with self.condition:
            if self.item is None and not self.closed and timeout:
                self.condition.wait(timeout)
            item, self.item = self.item, None
            return item

    def close(self):
        """Mark that no further items will arrive; a pending item stays takeable."""
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class LatestFrameSlot(LatestSlot):
    """A one-frame slot that owns pixels and rejects identity regression."""

    def __init__(self):
        super().__init__()
        self.source_id = None
        self.sequence = 0

    def publish(self, frame):
        owned = OwnedFrame.copy_from(frame.image, frame.sequence, frame.observed_ns,
                                     frame.source_id, frame.media_time, frame.native_format)
        with self.condition:
            if self.closed:
                raise ValueError("frame_slot_closed")
            if self.source_id not in (None, owned.source_id) or owned.sequence <= self.sequence:
                raise ValueError("source_changed_or_sequence_regressed")
            self.source_id, self.sequence = owned.source_id, owned.sequence
            self.replaced += self.item is not None
            self.item = owned
            self.condition.notify()
