"""The pre-25 firmware path, which until now nothing exercised.

Api class 6 -- Periodic Status 0 on 0x060 with faults in it, Periodic Status 1
on 0x061 with telemetry -- is what REV's SPARK MAX page documents, and that
page is the pre-25 table. It is stamped "Last updated 1 year ago" and gives no
arbitration ids; 0x060/0x061/0x062 follow from api class 6, indices 0-2.

This file used to be keyed on product, which was the defect. A SPARK MAX on
firmware 25+ broadcasts 0x2E0/0x2E1 like anything else, so "MAX" and "pre-25"
are not the same claim. test_firmware_generations.py owns that distinction;
what is here is the pre-25 layout itself.

Reading a pre-25 frame with the 25+ layout returned a 13.7 V rail as a fault
bitfield on this fleet, and gating recovery on it wedged the base.
"""

from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import attach, spark
from sparksim import frames as F

MAXS0 = F.API_LEGACY_STATUS_0
MAXS1 = F.API_LEGACY_STATUS_1


LEGACY_FW = "1.6.3"


def legacy(*devs):
    """Controllers on pre-25 firmware, whatever product they are."""
    return [spark(d, controller_type="sparkmax", firmware=LEGACY_FW)
            for d in devs]


def test_a_pre25_controller_broadcasts_api_class_6_and_not_the_modern_apis(sim):
    """The premise everything else rests on. If a simulated pre-25 controller
    answered on the 25+ apis, every test below would pass while proving
    nothing."""
    bus = sim(legacy(10, 11))
    attach(bus).inventory(0.5)

    assert bus.frames(10, api=MAXS0), "no pre-25 Periodic Status 0 on the wire"
    assert bus.frames(10, api=MAXS1), "no pre-25 Periodic Status 1"
    assert not bus.frames(10, api=F.API_STATUS_0), (
        "a pre-25 controller answered on the 25+ api; the two generations are "
        "on different api classes and conflating them is the whole defect")


def test_collect_status_reads_a_pre25_bus_without_being_told_anything(sim):
    """The live defect this closes, and it closes further than it used to.

    spark_admin filtered on the 25+ apis alone, so a pre-25 bus returned
    nothing and `spark faults` reported a healthy fleet as "no controllers
    broadcasting" while the frames were on the wire the whole time.

    The old fix was to pass the product in. That still fails on a bus whose
    firmware does not match the config, so the reader now listens to both
    generations and decides per device.
    """
    bus = sim(legacy(10, 11, 12))

    found = sa.collect_status(bus, seconds=0.4)
    assert sorted(found) == [10, 11, 12], (
        f"every controller has to appear with nothing declared: {sorted(found)}")
    assert found[10]["status0"] is not None, "and its Status 0 has to decode"
    assert found[10]["generation"] == sa.GEN_PRE25


def test_a_pre25_fault_is_read_out_of_status_0_where_it_lives(sim):
    """REV: on pre-25, Faults and Sticky Faults are fields of Periodic Status 0.
    On 25+ they moved to STATUS_1. One decoder cannot serve both, and the proof
    is that the same eight bytes mean different things."""
    bus = sim(legacy(12))
    bus.controller(12).faults = 1 << 6          # some fault bit
    bus.controller(12).sticky_faults = 1 << 6
    st = sa.collect_status(bus, seconds=0.4)

    s0 = st[12]["status0"]
    assert s0["active_faults"] == 1 << 6, (
        f"the fault has to come out of Status 0 on pre-25: {s0}")
    assert s0["sticky_faults"] == 1 << 6


def test_the_modern_decoder_misreads_a_pre25_frame_which_is_why_they_are_split(sim):
    """The defect, demonstrated rather than asserted.

    Decoding a pre-25 Periodic Status 0 with the 25+ layout produces telemetry
    out of the fault bytes. This test exists so anyone tempted to use one
    decoder for both generations can see the number it produces.
    """
    bus = sim(legacy(12))
    bus.controller(12).faults = 0
    bus.controller(12).sticky_faults = 0
    attach(bus).inventory(0.4)
    raw = bus.frames(12, api=MAXS0)[-1][1].data

    as_modern = sa.decode_status_0(raw)
    as_legacy = sa.decode_legacy_status_0(raw)

    assert as_legacy["active_faults"] == 0, "the pre-25 decoder reads it right"
    assert as_modern["voltage_v"] != pytest.approx(12.6, abs=0.5), (
        "and the 25+ decoder invents a rail voltage out of the fault bytes, "
        f"reading {as_modern['voltage_v']:.2f} V from a frame that carries none")


def test_a_mixed_firmware_bus_shows_both_generations_at_once(sim):
    """A part-updated fleet is a real configuration, and each controller has to
    be read with its own layout.

    Selecting one generation for the whole bus silently drops the other half,
    which is what a single declared controller_type did. One pass now returns
    both, each labelled with what it actually broadcast.
    """
    bus = sim(legacy(10) + [spark(12, firmware="26.1.6")])
    attach(bus).inventory(0.5)

    st = sa.collect_status(bus, seconds=0.4)
    assert sorted(st) == [10, 12], (
        f"one pass has to see the whole mixed bus: {sorted(st)}")
    assert st[10]["generation"] == sa.GEN_PRE25
    assert st[12]["generation"] == sa.GEN_FW25
    assert st[10]["status0"]["active_faults"] == 0, "pre-25 faults decode"
    assert st[12]["status0"]["voltage_v"] == pytest.approx(12.6, abs=0.2), (
        "and the 25+ controller's telemetry decodes as telemetry")
