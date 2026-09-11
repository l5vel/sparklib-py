"""pytest fixtures, driver wiring and shared assertion vocabulary.

The only module here that may import the driver, and only to wire and to patch.
"""
from __future__ import annotations

import pytest

from . import frames as F
from .bus import SimMessage, SparkBusSim
from .clock import VirtualClock
from .fleet import build_fleet, build_fleet_pre25, spark


@pytest.fixture
def clock(monkeypatch) -> VirtualClock:
    """A virtual clock, substituted for spark_admin's time module."""
    from sparklib import admin as sa
    return VirtualClock().patch(monkeypatch, sa)


@pytest.fixture
def sim(clock):
    """Factory: sim([controllers], **bus_kwargs) -> SparkBusSim, sharing `clock`."""
    made = []

    def _make(controllers=None, **kw):
        kw.setdefault("clock", clock)
        bus = SparkBusSim(controllers, **kw)
        made.append(bus)
        return bus

    _make.buses = made
    return _make


@pytest.fixture
def rig_flex(sim) -> SparkBusSim:
    """The healthy provisioned eight: ids 10-17, STATUS_1 at 20 ms, no faults."""
    return sim(build_fleet())


@pytest.fixture
def rig_max(sim) -> SparkBusSim:
    """The other generation's healthy eight: rig-max's ids on 24.0.1.

    Same role in its tier as rig-flex: the control that every failure is read
    against. A tier that only ever builds rig-flex tests one firmware generation
    and calls the result general.
    """
    return sim(build_fleet_pre25())


def attach(bus, channel="sim0"):
    """A SparkAdmin bound to `bus`, bypassing the socketcan open."""
    from sparklib.admin import SparkAdmin
    a = object.__new__(SparkAdmin)
    a.channel = channel
    a.bus = bus
    return a


def patch_can_bus(monkeypatch, bus) -> None:
    """Make can.Bus(...) return `bus`, for tests that drive spark_cli's _open()."""
    import can
    monkeypatch.setattr(can, "Bus", lambda *a, **kw: bus)
    if hasattr(can, "interface"):
        monkeypatch.setattr(can.interface, "Bus", lambda *a, **kw: bus, raising=False)


# -- assertion helpers -------------------------------------------------------

def assert_no_setpoints(bus) -> None:
    bad = [m for m in bus.sent if F.base_of(m.arbitration_id) in F.SETPOINT_BASES]
    assert not bad, ("a setpoint frame reached the bus: "
                     + ", ".join(f"0x{m.arbitration_id:08X}" for m in bad))


def assert_quiet_after(bus, t) -> None:
    late = [(ts, m) for ts, m in bus.sent_at if ts > t]
    assert not late, (f"{len(late)} frame(s) were sent after {t:.3f}s\n"
                      + bus.explain())


def periods_on_the_wire(bus, since=0.0) -> dict:
    """{(dev, api): mean period in ms} measured from delivered frames."""
    times = {}
    for t, m in bus.delivered:
        if t < since:
            continue
        f = F.split_arb(m.arbitration_id)
        times.setdefault((f.dev, f.api), []).append(t)
    out = {}
    for key, ts in times.items():
        if len(ts) > 1:
            out[key] = round((ts[-1] - ts[0]) * 1000.0 / (len(ts) - 1), 1)
    return out


def explain(bus, dev=None) -> str:
    return bus.explain(dev)
