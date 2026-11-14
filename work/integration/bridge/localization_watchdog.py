"""Monotonic freshness of advancing device timestamps; no epoch assumption."""
import math


class ProgressWatch:
    def __init__(self, timeout=0.5):
        self.timeout = timeout
        self.stamp = None
        self.changed = None

    def update(self, stamp, now, values=()):
        if stamp <= 0 or not all(math.isfinite(v) for v in values):
            self.changed = None
            return
        if self.stamp is not None and stamp < self.stamp:
            self.stamp = stamp
            self.changed = None
            return
        if self.stamp is None or stamp > self.stamp:
            # First sample alone cannot establish a running stream.
            if self.stamp is not None:
                self.changed = now
            self.stamp = stamp

    def fresh(self, now):
        return self.changed is not None and 0 <= now - self.changed < self.timeout
