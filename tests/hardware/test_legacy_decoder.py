"""The 2024-era decoder in controller.py, read against frames this fleet
actually sent.

The catalogue's protocol section records that 2025 firmware split the 16-bit
fault word into Faults{} and Warnings{} with a different layout, so a 2024-era
decoder mis-parses a 2025 frame without ever failing to parse it. That is the
LEGACY defect in the handoff, and `spark_controller.Controller` is the class the
swerve stack instantiates -- `_FAULT_DECODE_TRUSTED` is False for SparkFlex for
exactly this reason.

The simulator makes the same claim with a frame it built. This module makes it
with a frame a controller on this robot broadcast, which is the difference
between "our encoder and our decoder disagree" and "the hardware says one thing
and production code reads another".

On 26.1.6, per REV-Specs spark-frames-2.1.0:

    STATUS_0 bytes 2:6   12-bit bus voltage, 12-bit output current
    STATUS_0 byte 6 bit0 hard forward limit
    STATUS_1 bytes 0/2/3/5   faults, warnings, sticky faults, sticky warnings
    STATUS_1 byte 6 bit0     is-follower

and the legacy reader takes faults from STATUS_0 bytes 2:6, the rail from
STATUS_1 bytes 5:7, and follower mode from STATUS_0 byte 6 bit 0. Every one of
those is a field that exists, decodes, and means something else.
"""
from __future__ import annotations

import time

import os

import pytest

from sparklib import admin as sa
from sparklib.controller import SPARK_FLEX, Controller
from sparksim import frames as F

S0, S1 = F.API_STATUS_0, F.API_STATUS_1


@pytest.fixture
def captured(sniff, writable_id):
    """The last STATUS_0 and STATUS_1 this controller broadcast, as bytes."""
    with sniff() as sniffer:
        time.sleep(2.0)
    frames = {}
    for m in sniffer.from_the_bus:
        if F.is_spark(m.arbitration_id) and F.dev_of(m.arbitration_id) == writable_id:
            frames[F.api_of(m.arbitration_id)] = bytes(m.data)
    for api, name in ((S0, "STATUS_0"), (S1, "STATUS_1")):
        if api not in frames:
            pytest.skip(f"id {writable_id} broadcast no {name} in two seconds")
    return frames


@pytest.mark.needs_clean_bus
def test_the_two_status_frames_carry_the_fields_the_modern_decoder_names(
        captured, writable_id):
    """The control for both xfails below, and the only evidence that says which
    decoder is right. If a real idle frame did not decode to a plausible rail
    and an empty fault set through `spark_admin`, the disagreement below would
    be between two wrong readers rather than between a right one and a wrong
    one.

    Needs a genuinely healthy controller, so it cannot run beside the injectors
    that deliberately fault the whole bus. SPARK_HW_COLLIDE transmits on an id a
    controller owns and SPARK_HW_CONGEST saturates the bus; both leave a sticky
    `can` fault on every device, and that failed this test's own
    premise rather than finding anything.
    """
    s0 = sa.decode_status_0(captured[S0])
    s1 = sa.decode_status_1(captured[S1])

    assert 6.0 <= s0["voltage_v"] <= 30.0, (
        f"STATUS_0 bytes 2:6 decoded to {s0['voltage_v']:.2f} V, which is not a "
        f"rail: {captured[S0].hex()}")
    assert s0["spark_model"] == 1, (
        f"this fleet is provisioned as SPARK Flex (model 1) and id {writable_id} "
        f"reports model {s0['spark_model']}")
    assert s1["faults"] == [] and s1["sticky_faults"] == [], (
        f"id {writable_id} is faulted, so it cannot be the healthy control: {s1}")


@pytest.mark.xfail(reason="the production Controller in controller.py still "
                          "reads faults out of STATUS_0 bytes 2:6, which on 26.1.6 "
                          "are bus voltage and output current; no capability exists "
                          "there to read the 2025+ STATUS_1 layout",
                   strict=False)
def test_the_legacy_controller_reads_a_real_idle_frame_as_a_fault_word(
        captured, writable_id):
    """The frame is healthy: STATUS_1 says no fault and no sticky fault, and
    STATUS_0 says a nominal rail. Fed to the class base_handler actually
    instantiates, the same bytes produce a large non-zero active-fault word --
    because on the pre-2025 layout those two bytes were the fault field and on
    26.1.6 they are the twelve-bit bus voltage.

    The consequence runs both ways. A healthy controller reports faults it does
    not have, and a real gate-driver fault, which lives in STATUS_1, is
    invisible. https://www.chiefdelphi.com/t/461037
    """
    legacy = Controller(bus=None, id=writable_id, controller_type=SPARK_FLEX)
    legacy._status0_raw = captured[S0]

    assert (legacy.active_faults, legacy.sticky_faults) == (0, 0), (
        f"a healthy idle frame {captured[S0].hex()} decoded as "
        f"active=0x{legacy.active_faults:04X} sticky=0x{legacy.sticky_faults:04X}; "
        f"the same bytes are {sa.decode_status_0(captured[S0])['voltage_v']:.2f} V "
        f"and {sa.decode_status_0(captured[S0])['current_a']:.2f} A")


def test_the_legacy_reader_does_not_take_the_rail_out_of_the_fault_frame(
        captured, writable_id):
    """`bus_voltage` and `output_current` read STATUS_1 bytes 5 to 7. On 26.1.6
    byte 5 is the sticky-warning byte and byte 6 bit 0 is the follower flag, so
    the rail reads as whatever history the sticky bits happen to hold -- 0.00 V
    on a clean controller, and something arbitrary and non-zero on one that
    browned out last week. `spark voltage` reads the right frame; anything
    holding a legacy Controller does not.
    """
    legacy = Controller(bus=None, id=writable_id, controller_type=SPARK_FLEX)
    legacy._status1_raw = captured[S1]
    truth = sa.decode_status_0(captured[S0])["voltage_v"]

    assert legacy.bus_voltage is None or abs(legacy.bus_voltage - truth) < 1.0, (
        f"the legacy reader reports {legacy.bus_voltage} V from STATUS_1 while "
        f"the rail in STATUS_0 is {truth:.2f} V")


@pytest.mark.xfail(reason="spark_controller.print_diagnostics reads STATUS_0 byte 6 "
                          "bit 0 as is_follower; on 26.1.6 that bit is "
                          "hard_forward_limit and the follower flag is in STATUS_1",
                   strict=False)
def test_a_closed_hard_limit_would_not_be_reported_as_follower_mode(
        adm, gate, spoofed_status, writable_id):
    """Catalogue's data-port interlock beside catalogue B5's follower mode: two
    states with nothing in common, sharing a bit position across a firmware
    generation. A closed hard forward limit is a safety interlock to respect; a
    controller in follower mode is one driving a mechanism no code references.
    The legacy reader cannot tell them apart because it reads the limit bit and
    calls it the follower flag.

    Injected rather than staged, because closing a real limit switch needs a
    person at the robot -- the staged version is
    test_stage_unplug_encoder_a_sensor_fault_appears_and_reaches_the_audit's
    neighbour in test_staged_physical.py.
    """
    gate("inject")
    with spoofed_status(writable_id, S0, hard_fwd=True, volts=12.4):
        reading = sa.collect_status(adm.bus, seconds=1.0)[writable_id]["status0"]
        raw = F.encode_status_0(hard_fwd=True, volts=12.4)

    assert reading["hard_forward_limit"] is True, "the injection never arrived"
    legacy = Controller(bus=None, id=writable_id, controller_type=SPARK_FLEX)
    legacy._status0_raw = raw
    assert legacy.is_follower is False, (
        "a closed hard forward limit reads as follower mode through the class "
        "the swerve stack instantiates")
