"""A bounded FIFO queue with capacity reserved for current reading pages."""
import threading
import time
from collections import OrderedDict


class ImageCapacityError(RuntimeError):
    """Local saturation is not an upstream source-health failure."""


class ImageBudget:
    def __init__(self, limit=8, foreground_reserved=2):
        if not 0 < foreground_reserved < limit:
            raise ValueError('Invalid foreground reservation')
        self.limit, self.background_limit = limit, limit - foreground_reserved
        self.condition = threading.Condition()
        self.waiting, self.active, self.promoted = [], {}, OrderedDict()
        self.background = 0

    def _dispatch(self):
        # The oldest eligible request wins. A background request cannot take
        # reserved capacity, but newer foreground requests cannot starve an
        # older background request when shared capacity becomes available.
        while len(self.active) < self.limit:
            ticket = next((item for item in self.waiting
                           if item['foreground'] or self.background < self.background_limit), None)
            if ticket is None:
                break
            self.waiting.remove(ticket)
            ticket['running'] = True
            self.active[ticket['key']] = ticket
            self.background += not ticket['foreground']
        self.condition.notify_all()

    def acquire(self, key, *, foreground=False, timeout=5):
        deadline = time.monotonic() + timeout
        with self.condition:
            promoted_at = self.promoted.pop(key, None)
            ticket = {'key': key, 'foreground': foreground or (promoted_at is not None and time.monotonic() - promoted_at < 5), 'running': False}
            self.waiting.append(ticket)
            self._dispatch()
            while not ticket['running']:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.waiting.remove(ticket)
                    self._dispatch()
                    return None
                self.condition.wait(remaining)
            return ticket

    def promote(self, key):
        with self.condition:
            if key in self.active:
                return  # Already fetching; no new request is necessary.
            ticket = next((item for item in self.waiting if item['key'] == key), None)
            if ticket is not None:
                ticket['foreground'] = True
                self._dispatch()
            else:
                # A coalesced reader may arrive just after Cache creates its
                # Future and just before the owner registers with this queue.
                self.promoted[key] = time.monotonic()
                # Completion can race with a waiter's promotion after the
                # fetch releases its lease. Those hints expire and are bounded.
                while len(self.promoted) > 128:
                    self.promoted.popitem(last=False)

    def release(self, ticket):
        with self.condition:
            if self.active.pop(ticket['key'], None) is not ticket:
                raise RuntimeError('Unknown image budget lease')
            self.background -= not ticket['foreground']
            self._dispatch()
