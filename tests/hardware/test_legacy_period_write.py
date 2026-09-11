"""Reconfiguring a pre-25 SPARK controller over CAN.

Two write dialects exist and only one reaches this generation.

  * PARAMETER_WRITE (api class 14) is versionImplemented 25.0.0, so a pre-25
    device does not carry the frame and an unmatched arbitration id is dropped
    without a reply. Silence there is the absence of a frame, not a refusal.
  * The pre-25 period setter (api class 6, `SparkAdmin.set_legacy_status_period`)
    shares the STATUS_N broadcast id and is told apart by DLC. It returns no
    response of any kind, so the only read-back is the cadence on the wire.

The module pins both halves, so a future change that routes a pre-25 fleet
through the parameter path fails here rather than in the field. Measurements and
provenance live in docs/SPARKMAX-BRINGUP.md and in spark_provenance.
"""
from __future__ import annotations

import time

import pytest

from sparklib import admin as sa
from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
from sparksim import frames as F

# Distinct from every value in the boot table, so a measurement that lands here
# cannot be the throttle the boot config would have written anyway.
PROBE_PERIOD_MS = 25

# A cadence measured over a short window carries jitter from arbitration and from
# the adapter, so the tolerance is generous enough not to be flaky and tight
# enough that 25 ms can never be mistaken for the 50 ms it replaced.
TOLERANCE_MS = 8


@pytest.fixture
def legacy_fleet(adm):
    """Skip unless this really is a pre-25 bus. Returns the dominant generation."""
    gen = sa.dominant_generation(sa.collect_status(adm.bus, 2.0))
    if gen != sa.GEN_PRE25:
        pytest.skip(f"this bus reads {gen}; the legacy write path is pre-25 only")
    return gen


def _measure_ms(adm, dev, api, seconds=3.0):
    """Mean gap between broadcasts of one api from one device, or None."""
    want = F.arb(api, dev)
    deadline, stamps = time.time() + seconds, []
    while time.time() < deadline:
        m = adm.bus.recv(0.2)
        if m is None:
            continue
        if m.arbitration_id == want and len(m.data) >= 8:
            stamps.append(m.timestamp)
    if len(stamps) < 4:
        return None
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return 1000.0 * sum(gaps) / len(gaps)


# -- the standing observation, no write ----------------------------------------

@pytest.mark.needs_clean_bus
def test_the_fleet_cadence_is_this_packages_throttle_not_revs_default(
        adm, roles, legacy_fleet):
    """Evidence that a period write already reached every controller.

    This package throttles every pre-25 status frame at boot, because REVLib's
    defaults push enough receive traffic to risk wedging the gs_usb dongle. A
    fleet broadcasting the throttle rather than REV's defaults is one whose
    period writes reached it over CAN.

    Read-only: it measures what is already on the wire.
    """
    inv = adm.inventory(6.0)
    present = sorted(set(inv) & set(roles))
    assert present, "no configured controller is broadcasting"

    declared_s0 = _SPARKMAX_STATUS_PERIODS_MS[0]
    off = {}
    for dev in present:
        measured = inv[dev]["periods_ms"].get(sa.LEGACY_STATUS_0_API)
        if measured is None or abs(measured - declared_s0) > TOLERANCE_MS * 2:
            off[dev] = measured

    assert not off, (
        f"STATUS_0 is not at the boot throttle of {declared_s0} ms on "
        f"{ {d: roles[d] for d in off} }: measured {off}.\n"
        "  If these read REV's default of 10 ms instead, apply_boot_config has "
        "not run since the last power cycle and the periods are volatile -- that "
        "is the finding, not a fault.")


# -- the controlled write ------------------------------------------------------

@pytest.mark.needs_clean_bus
def test_a_legacy_period_write_changes_the_cadence_and_can_be_put_back(
        adm, roles, writable_id, legacy_fleet):
    """The controlled half of max.firmware_honours_period_write.

    One frame index, one controller, restored to the boot throttle before the
    test returns. Nothing is persisted, so flash holds the provisioned value
    throughout and a motor-rail cycle is the backstop if this process dies
    mid-test.
    """
    restore_to = _SPARKMAX_STATUS_PERIODS_MS[0]
    before = _measure_ms(adm, writable_id, sa.LEGACY_STATUS_0_API)
    assert before is not None, (
        f"id {writable_id} ({roles.get(writable_id)}) broadcast no STATUS_0 to "
        "measure against")

    try:
        adm.set_legacy_status_period(writable_id, 0, PROBE_PERIOD_MS)
        time.sleep(0.5)
        during = _measure_ms(adm, writable_id, sa.LEGACY_STATUS_0_API)
    finally:
        adm.set_legacy_status_period(writable_id, 0, restore_to)
        time.sleep(0.5)
    after = _measure_ms(adm, writable_id, sa.LEGACY_STATUS_0_API)

    assert during is not None, (
        f"id {writable_id} stopped broadcasting STATUS_0 after the write. That "
        "is a period write taking effect with a value this test did not intend; "
        "cut and restore motor power to get the boot throttle back.")
    assert abs(during - PROBE_PERIOD_MS) < TOLERANCE_MS, (
        f"id {writable_id} ({roles.get(writable_id)}) was asked for "
        f"{PROBE_PERIOD_MS} ms and broadcast at {during:.1f} ms "
        f"(was {before:.1f} ms), so this generation is not honouring a period "
        "write on api class 6 -- which would also mean apply_boot_config's "
        "throttle is not reaching these controllers.")
    assert after is not None and abs(after - restore_to) < TOLERANCE_MS * 2, (
        f"id {writable_id} did not go back to {restore_to} ms; it reads {after}. "
        "Nothing was persisted, so cut and restore motor power.")


def test_the_parameter_write_dialect_is_still_unanswered_on_this_firmware(
        adm, roles, writable_id, legacy_fleet):
    """The other half of the answer, and the reason `spark repair` refuses.

    PARAMETER_WRITE is how the 25+ tooling reconfigures a controller. Pre-25 does
    not carry the frame, so a caller that waits for a response reads the hardware
    as unwritable when it is only being addressed in the wrong dialect.

    Sends one frame and expects silence, so nothing needs restoring.
    """
    result = adm.write_param(writable_id, F.PARAM_STATUS_0_PERIOD,
                             _SPARKMAX_STATUS_PERIODS_MS[0])
    assert result is None, (
        f"id {writable_id} ({roles.get(writable_id)}) answered PARAMETER_WRITE: "
        f"{result}. If this generation carries the frame after all, the config "
        "injection tier and `spark repair` can use it directly instead of "
        "routing through set_legacy_status_period.")
