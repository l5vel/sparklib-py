"""Pre-25 fault decoding and reset classification, injected onto the live bus.

`classify_reset` separates the two readings an operator acts on differently:
hasReset alone is an ordinary power cycle and needs the volatile configuration
re-sent, hasReset beside brownout is a rail collapse and a power-path fault.

Getting that right on this generation needs two things the 2025+ path does not.
The fault word is sixteen bits in LEGACY_STATUS_0 rather than eight in STATUS_1,
so it needs its own name table and a decoder that walks the full width. And
pre-25 files both bits as faults where 25+ files them as warnings, so a
classifier reading only warnings sees nothing.

Nothing here causes a real brownout. The bits are transmitted from this host and
socketcan loops them back, so `SparkAdmin` receives them indistinguishably from a
controller's own broadcast; stopping the stream ends the failure and no
controller is written to. Real rail collapse is rig-flex-2's experiment, and
`LIVE_EXPERIMENTS` refuses this directory there.
"""
from __future__ import annotations

import time

import pytest

from sparklib import admin as sa
from sparksim import frames as F

# Positions in the pre-25 fault word. Sourcing: the pre25.fault_bit_order claim
# in spark_provenance.
HAS_RESET_BIT = 9
BROWNOUT_BIT = 0


@pytest.fixture
def legacy_fleet(adm):
    gen = sa.dominant_generation(sa.collect_status(adm.bus, 2.0))
    if gen != sa.GEN_PRE25:
        pytest.skip(f"this bus reads {gen}; legacy fault frames are pre-25 only")
    return gen


def _read_injected(adm, dev, mask, seconds=1.2):
    """Decode what the driver makes of an injected legacy fault word."""
    reading = sa.collect_status(adm.bus, seconds=seconds).get(dev)
    assert reading is not None, f"id {dev} produced no reading at all"
    return sa.normalised_reading(reading)


# -- the bit table -------------------------------------------------------------

def test_the_legacy_table_is_sixteen_wide_and_not_the_modern_one():
    """A table shorter than the word is how a set bit disappears.

    No bus needed. It guards the shape the tests below depend on.
    """
    assert len(sa._LEGACY_FAULT_BITS) == 16, (
        "the pre-25 fault word is 16 bits; a shorter table silently drops the top "
        f"half, which is where hasReset lives: {sa._LEGACY_FAULT_BITS}")
    assert sa._LEGACY_FAULT_BITS[HAS_RESET_BIT] == "hasReset"
    assert sa._LEGACY_FAULT_BITS[BROWNOUT_BIT] == "brownout"
    assert sa._LEGACY_FAULT_BITS != sa._FAULT_BITS, (
        "the generations order their fault bits differently; one shared table "
        "lets an encoder and a decoder agree while both are wrong")


def test_no_set_bit_can_vanish_however_short_the_table_is():
    """A bit with no name in the table must still reach the caller."""
    named = sa._bits(0xFF00, sa._LEGACY_FAULT_BITS)
    assert len(named) == 8, f"the top half of the word was dropped: {named}"
    beyond = sa._bits(1 << 20, sa._FAULT_BITS)
    assert beyond == ["bit20"], (
        f"a bit with no name in the table must still reach the operator: {beyond}")


# -- injected onto the wire ----------------------------------------------------

@pytest.mark.needs_clean_bus
def test_an_injected_hasreset_reaches_the_driver_by_its_right_name(
        adm, inject, gate, free_id, legacy_fleet):
    """Bit 9 on a pre-25 bus must decode as hasReset and nothing else.

    Injected on a free id so no configured controller is impersonated.
    """
    gate("inject")
    mask = F.legacy_fault_mask("hasReset")
    assert mask == 1 << HAS_RESET_BIT, f"hasReset is not bit 9 in the sim: {mask:#x}"

    frame = (F.arb(F.API_LEGACY_STATUS_0, free_id),
             F.encode_status_0_sparkmax(faults=mask, sticky_faults=mask))
    with inject.stream([frame], period_s=0.02, seconds=6.0):
        time.sleep(0.4)
        r = _read_injected(adm, free_id, mask)

    assert "hasReset" in r["sticky_faults"], (
        f"a set bit 9 did not decode as hasReset: {r['sticky_faults']}")
    assert "firmware" not in r["sticky_faults"], (
        "bit 9 decoded with the 2025+ table would land somewhere else entirely")


@pytest.mark.needs_clean_bus
@pytest.mark.parametrize("names, expect", [
    (("hasReset",), "power-cycle"),
    (("brownout", "hasReset"), "brownout"),
    (("brownout",), "sagged-without-reset"),
])
def test_the_reset_classifier_separates_a_brownout_from_a_power_cycle(
        adm, inject, gate, free_id, legacy_fleet, names, expect):
    """The reading an operator acts on.

    "power-cycle" means re-apply the configuration and carry on. "brownout"
    means stop and chase the power path: state of charge, breaker, lug torque,
    wire gauge. Reporting the second as the first sends someone back to work on
    a robot whose rail is collapsing.

    On pre-25 both bits are faults, so a classifier reading only warnings
    cannot reach any of these three verdicts.
    """
    gate("inject")
    mask = F.legacy_fault_mask(*names)
    frame = (F.arb(F.API_LEGACY_STATUS_0, free_id),
             F.encode_status_0_sparkmax(faults=mask, sticky_faults=mask))
    with inject.stream([frame], period_s=0.02, seconds=6.0):
        time.sleep(0.4)
        r = _read_injected(adm, free_id, mask)

    got = sa.classify_reset(r["sticky_warnings"], r["sticky_faults"])
    assert got == expect, (
        f"injected {sorted(names)} on a pre-25 bus classified as {got!r}, "
        f"expected {expect!r}. Decoded sticky faults were {r['sticky_faults']} "
        f"and warnings {r['sticky_warnings']}.")


@pytest.mark.needs_clean_bus
def test_a_brownout_on_a_configured_id_is_named_by_the_audit(
        adm, inject, gate, roles, serials, writable_id, legacy_fleet):
    """The whole path, on a real controller's id: wire to audit finding.

    Uses a configured id so `audit_problems` has a role to name. The controller
    keeps broadcasting its own frames underneath; the injected ones arrive at
    20 ms against its 50 ms, so the driver's last-frame-wins read sees these.
    """
    gate("inject")
    mask = F.legacy_fault_mask("brownout", "hasReset")
    frame = (F.arb(F.API_LEGACY_STATUS_0, writable_id),
             F.encode_status_0_sparkmax(faults=mask, sticky_faults=mask))

    with inject.stream([frame], period_s=0.02, seconds=8.0):
        time.sleep(0.5)
        status = sa.collect_status(adm.bus, seconds=1.5)
        inv = adm.inventory(1.5)
        problems = sa.audit_problems(inv, {}, roles, serials, status=status,
                                     generation=sa.GEN_PRE25)

    named = [p for p in problems if "brownout" in p.lower()]
    assert named, (
        f"a bus broadcasting brownout on id {writable_id} "
        f"({roles.get(writable_id)}) produced no brownout finding. The audit "
        f"returned: {problems}")
