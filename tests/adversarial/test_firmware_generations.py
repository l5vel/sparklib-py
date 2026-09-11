"""Frame layout keys on firmware version, and these tests hold it to that.

The driver used to select frames by product: STATUS_0 for a SPARK MAX, STATUS_1
for a Flex. REV's machine-readable spec makes no such split. It describes one
device, and the only axis it turns on is `versionImplemented`:

    LEGACY_STATUS_0   api 0x060   implemented 0.0.1   DEPRECATED at 25.0.0
    STATUS_0..8       api 0x2E0+  implemented 25.0.0
    STATUS_9          api 0x2E9   implemented 26.0.0
    UNIQUE_ID         api 0x2F0   implemented 25.0.0

REVLib agrees from the other side: SparkMax and SparkFlex both derive from
SparkBase, and one PeriodicStatus0..9 struct set in SparkLowLevel.h serves both,
with no override in either product header (checked in 2025.0.3 and 2026.0.2).

So a SPARK MAX on firmware 25+ broadcasts 0x2E0/0x2E1 with faults in STATUS_1,
exactly like a Flex. Nothing in this file could be expressed before the
simulator learned firmware generations: it modelled a MAX as 0x060/0x061 always,
so the suite and the defect agreed with each other and the tests stayed green.

NOT yet confirmed on hardware. No SPARK MAX bus in this fleet has been read.
The evidence above is documentary; `candump can0` on rig-max settles it.
"""

from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import attach, spark
from sparksim import frames as F

MODERN_FW = "26.1.6"
LEGACY_FW = "1.6.3"


def test_a_sparkmax_on_firmware_25_broadcasts_the_modern_frames(sim):
    """The premise. A MAX that has been updated is on 0x2E0/0x2E1, not 0x060."""
    bus = sim([spark(10, controller_type="sparkmax", firmware=MODERN_FW)])
    attach(bus).inventory(0.5)

    assert bus.frames(10, api=F.API_STATUS_0), "no STATUS_0 from a 25+ SPARK MAX"
    assert bus.frames(10, api=F.API_STATUS_1), "no STATUS_1 from a 25+ SPARK MAX"
    assert not bus.frames(10, api=F.API_LEGACY_STATUS_1), (
        "0x061 is a pre-25 frame; firmware 25+ retired api class 6 past index 0")


def test_a_healthy_sparkmax_on_firmware_25_is_not_read_as_sixteen_faults(sim):
    """The defect this whole change exists to close.

    frames 2.1.0 pins every signal in LEGACY_STATUS_0 on firmware 25+: applied
    output 0, FAULTS_AND_STICKY_FAULTS 0xFFFFFFFF, other signals 0. REV: "Always
    has all faults set so that old software knows that something is wrong."

    Scoring those bits reports eight faults and eight sticky faults on a
    perfectly healthy controller, every frame, indistinguishable from a dead
    one. That is the 0x06CE failure again, where gating recovery on a misread
    frame wedged a base permanently.
    """
    healthy = spark(10, controller_type="sparkmax", firmware=MODERN_FW,
                    legacy_beacon=True, faults=0, sticky_faults=0)
    bus = sim([healthy])

    reading = sa.collect_status(bus, seconds=0.6)[10]
    beacons = bus.frames(10, api=F.API_LEGACY_STATUS_0)
    assert beacons, "premise: this controller must be emitting the 0x060 beacon"
    assert bytes(beacons[-1][1].data) == F.LEGACY_BEACON_PAYLOAD, (
        "premise: the beacon must carry the all-ones word REV pins it to")
    assert reading["generation"] == sa.GEN_FW25
    r = sa.normalised_reading(reading)

    assert r["faults"] == [], f"healthy 25+ SPARK MAX read as faulted: {r['faults']}"
    assert r["sticky_faults"] == [], (
        f"healthy 25+ SPARK MAX read as sticky-faulted: {r['sticky_faults']}")


def test_the_beacon_is_recognised_even_with_every_modern_frame_switched_off(sim):
    """The hole in inferring generation from which apis arrive.

    A 25+ controller with its 0x2E* periods all disabled shows only 0x060, which
    otherwise reads as pre-25 and puts the all-ones word back into the audit.
    The payload settles it: 0x060 is pinned to a constant on 25+, so the
    constant itself identifies the generation.
    """
    quiet = spark(10, controller_type="sparkmax", firmware=MODERN_FW,
                  legacy_beacon=True, periods_ms={F.API_LEGACY_STATUS_0: 10})
    # set_period_ms writes through to ram where a parameter id owns the cadence;
    # assigning periods_ms directly is ignored for 0x2E0/0x2E1.
    for api in (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID):
        quiet.set_period_ms(api, 0)
    bus = sim([quiet])

    reading = sa.collect_status(bus, seconds=0.6)[10]
    assert bus.frames(10, api=F.API_LEGACY_STATUS_0), (
        "premise: the 0x060 beacon must actually be on the wire")
    apis = {hex(a) for a in (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID)
            if bus.frames(10, api=a)}
    assert not apis, f"premise: every modern frame must be off, saw {apis}"

    assert reading["generation"] == sa.GEN_FW25, (
        "a lone 0x060 carrying the pinned beacon payload is a 25+ controller")
    assert sa.normalised_reading(reading)["faults"] == []


def test_a_pre25_controller_still_reports_its_faults_out_of_0x060(sim):
    """The other half. Conceding the beacon must not lose real pre-25 faults."""
    faulted = spark(10, controller_type="sparkmax", firmware=LEGACY_FW,
                    faults=F.LEGACY_FAULT["sensor"],
                    sticky_faults=F.LEGACY_FAULT["sensor"])
    bus = sim([faulted])

    reading = sa.collect_status(bus, seconds=0.6)[10]
    assert reading["generation"] == sa.GEN_PRE25
    r = sa.normalised_reading(reading)
    assert "sensor" in r["faults"], (
        f"a real pre-25 fault was dropped: {r['faults']}")
    assert "sensor" in r["sticky_faults"]


def test_generation_is_read_off_the_wire_and_not_from_the_config(sim):
    """A mixed-firmware fleet decodes per device.

    rig-max-2 and rig-max may be part-updated, and one declared controller_type
    cannot describe such a bus. Nothing here is told a generation.
    """
    bus = sim([spark(10, controller_type="sparkmax", firmware=MODERN_FW),
               spark(11, controller_type="sparkmax", firmware=LEGACY_FW)])

    status = sa.collect_status(bus, seconds=0.6)
    assert status[10]["generation"] == sa.GEN_FW25
    assert status[11]["generation"] == sa.GEN_PRE25
    assert status[10]["generation_observed"] is True
    assert status[11]["generation_observed"] is True


@pytest.mark.parametrize("product", ["sparkmax", "sparkflex"])
def test_the_fault_frame_is_status_1_on_25_plus_for_both_products(sim, product):
    """Product decides the model number, and decides nothing about frames."""
    bus = sim([spark(10, controller_type=product, firmware=MODERN_FW)])
    reading = sa.collect_status(bus, seconds=0.5)[10]

    ff = sa.fault_frame(reading["generation"])
    assert ff["api"] == sa.STATUS_1_API
    assert ff["label"] == "STATUS_1"


def test_passing_a_product_where_a_generation_belongs_is_refused():
    """Every old call site fails loudly instead of resolving to a default.

    `fault_frame("sparkmax")` returning a legacy frame is how the misread got
    in. A silent fallback would let the next one in the same way.
    """
    for name in ("sparkmax", "sparkflex", "SparkMax", "spark_max"):
        with pytest.raises(ValueError, match="firmware generation"):
            sa.fault_frame(name)
        with pytest.raises(ValueError, match="firmware generation"):
            sa.api_set(name)


def test_pre25_has_no_unique_id_frame(sim):
    """UNIQUE_ID is implemented at 25.0.0, so serial-addressed commands cannot
    reach a pre-25 controller. The old API_SETS offered 0x2F0 alongside 0x060,
    a pairing no single firmware version produces."""
    bus = sim([spark(10, controller_type="sparkmax", firmware=LEGACY_FW)])
    attach(bus).inventory(0.5)

    assert not bus.frames(10, api=F.API_UNIQUE_ID)
    assert sa.API_SETS[sa.GEN_PRE25]["unique_id"] is None


def test_a_25_plus_beacon_pinned_as_pre25_still_reports_no_faults(sim):
    """The guard that catches a wrong pin, and it needs its own test.

    When generation detection works, `normalised_reading` takes the 25+ branch
    and never looks at the beacon. The pre-25 branch is reached only when a
    caller pins the generation -- a stale config, or an operator passing
    --generation pre25 -- against a controller that is actually on 25+.

    Mutation-checked: blanking the `is_beacon` guard leaves every other test in
    this file green, so without this one the guard is unguarded.
    """
    healthy = spark(10, controller_type="sparkmax", firmware=MODERN_FW,
                    legacy_beacon=True, faults=0, sticky_faults=0)
    bus = sim([healthy])

    reading = sa.collect_status(bus, seconds=0.6, generation=sa.GEN_PRE25)[10]
    assert reading["generation"] == sa.GEN_PRE25, "premise: the pin must hold"
    assert reading["status0"]["is_beacon"] is True, (
        "premise: the payload under the wrong pin must be the 25+ beacon")

    r = sa.normalised_reading(reading)
    assert r["faults"] == [], (
        f"a pinned-wrong 25+ beacon was scored as faults: {r['faults']}")
    assert r["sticky_faults"] == []
    assert "legacy_beacon_not_a_reading" in r["implausible"], (
        "the misread must be reported, not silently swallowed")
