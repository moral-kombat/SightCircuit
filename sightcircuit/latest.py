"""One-pending-item handoff: newer items replace older ones instead of queueing."""
import threading


class LatestSlot:
    def __init__(self):
        self.condition = threading.Condition()
        self.item = None
        self.replaced = 0
        self.closed = False
        self.discarded = False

    def put(self, item):
        with self.condition:
            if self.discarded:
                return
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

    def discard(self):
        """Close, drop the pending item and drop every later one: a finished run keeps no item."""
        with self.condition:
            self.closed = self.discarded = True
            self.item = None
            self.condition.notify_all()
