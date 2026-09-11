"""A virtual clock, substituted for the `time` module a driver imports.

No test in this package may cost real time. A 5 s inventory window, a 4 s cadence
measurement and a 2 s post-flash blackout are all arithmetic here, so the whole
adversarial suite runs in the time one real `inventory()` would take.
"""
from __future__ import annotations

from types import SimpleNamespace


class VirtualClock:
    """Monotonic virtual seconds. Only `sleep()` and the bus scheduler move it."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self.start = float(start)
        self.sleeps: list[tuple[float, float]] = []
        self.on_advance: list = []

    # -- the four functions a driver calls -----------------------------------

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append((self.now, float(seconds)))
        self.advance(max(0.0, float(seconds)))

    # -- movement ------------------------------------------------------------

    def advance_to(self, t: float) -> None:
        """Move to `t`. A target at or before now is a no-op, never an error."""
        t = float(t)
        if t <= self.now:
            return
        self.now = t
        self._notify()

    def advance(self, seconds: float) -> None:
        self.advance_to(self.now + float(seconds))

    def _notify(self) -> None:
        for fn in tuple(self.on_advance):
            fn(self.now)

    # -- substitution --------------------------------------------------------

    def patch(self, monkeypatch, *modules) -> "VirtualClock":
        """Replace each module's `time` attribute with this clock's functions."""
        ns = SimpleNamespace(time=self.time, sleep=self.sleep,
                             monotonic=self.monotonic, perf_counter=self.perf_counter)
        for mod in modules:
            monkeypatch.setattr(mod, "time", ns)
        return self

    def __repr__(self) -> str:
        return f"VirtualClock(now={self.now:.6f}, sleeps={len(self.sleeps)})"
