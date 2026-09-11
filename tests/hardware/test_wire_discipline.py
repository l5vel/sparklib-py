"""What the `spark` tooling actually puts on a live bus.

Not a catalogue failure -- this suite's own discipline, asserted against real
hardware rather than read off the source.

tests/unit/test_spark_callsites.py already checks that no module in the package
contains the bootloader literal or a setpoint base. That is the right check and
it is not this one: a frame can be assembled from parts, an api can be computed
in a loop, and a helper can be handed a base by a caller. The only way to know
what left the adapter is to listen to the adapter.

The reason this module exists at all is on record. On a blind RTR
sweep across all 1024 api values on device 17 reached ENTER_SWDL_CAN_BOOTLOADER
-- an api that needs no payload, so a remote-transmission-request frame invokes
it -- and that controller stopped broadcasting entirely. Clear Faults did not
bring it back; cutting the motor rail did
(docs/spark/runs/-rig-flex-probe-log.md). The frame is one bit pattern
away from GET_FIRMWARE_VERSION, which the same sweep had just used
successfully.

Read-only subcommands only. `spark clear` is deliberately not swept: it erases
the sticky faults and the hasReset bit that the staged tests read, which is D5
and is the defect that already cost a real observation on rig-flex-2.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparkhw.wire import FORBIDDEN_BASES, forbidden_frames
from sparksim import frames as F

# Everything `spark_admin` is allowed to transmit. GET_FIRMWARE doubles as the
# request and the response arbitration id, which is why the response is told
# apart by direction rather than by address.
ADMIN_BASES = {sa.CLEAR_FAULTS: "CLEAR_FAULTS",
               sa.IDENTIFY_UNIQUE: "IDENTIFY_UNIQUE_SPARK",
               sa.SET_CAN_ID: "SET_CAN_ID",
               sa.GET_FIRMWARE: "GET_FIRMWARE_VERSION",
               sa.PARAM_WRITE: "PARAMETER_WRITE",
               sa.PERSIST: "PERSIST_PARAMETERS"}

READ_ONLY_SUBCOMMANDS = ("status", "audit", "faults", "duplicates")

# Every base a read-only subcommand is allowed to transmit. GET_FIRMWARE was the
# whole list until, when the parameter read frames started working and
# `spark audit` began comparing the declared configuration against the hardware.
# These carry no payload and no write api: a READ_PARAMETER request is a remote
# frame, and the frame one index past the last of them is WRITE_PARAMETER_0_AND_1.
READ_REQUEST_BASES = frozenset(
    [sa.GET_FIRMWARE]
    + [sa.READ_PARAM_BASE + (i << 6) for i in range(128)]
    + [sa.GET_PARAM_TYPES + (i << 6) for i in range(16)])


def _is_read_request(base):
    return base in READ_REQUEST_BASES


def _run(name, window=2.0):
    handler = {"status": spark_cli.cmd_status, "audit": spark_cli.cmd_audit,
               "faults": spark_cli.cmd_faults,
               "duplicates": spark_cli.cmd_duplicates}[name]
    return handler(SimpleNamespace(window=window))


def _bases(messages):
    return {m.arbitration_id & ~0x3F for m in messages}


@pytest.mark.parametrize("subcommand", READ_ONLY_SUBCOMMANDS)
def test_no_read_only_subcommand_puts_a_forbidden_frame_on_the_wire(
        sniff, capsys, subcommand):
    """Twenty-one arbitration bases must never leave this host: the seven 26.1.6
    setpoints, the two SparkMax-era ones controller.py still builds, both
    heartbeats, the two resets, the five set-position frames, the two
    software-download frames and the bootloader entry. Any one of them turns a
    read-only command into something that moves a robot or forgets its
    configuration.
    """
    with sniff() as sniffer:
        _run(subcommand)
    capsys.readouterr()

    bad = forbidden_frames(sniffer.from_this_host)
    assert not bad, (
        f"`spark {subcommand}` transmitted "
        + ", ".join(f"{FORBIDDEN_BASES.get(m.arbitration_id & ~0x3F)} "
                    f"(0x{m.arbitration_id:08X})" for m in bad))


@pytest.mark.parametrize("subcommand", READ_ONLY_SUBCOMMANDS)
def test_the_read_only_subcommands_transmit_only_firmware_requests(
        sniff, capsys, subcommand):
    """Stronger than the refusal above, and the one that would catch a new
    subcommand quietly gaining a write. Reading this bus needs GET_FIRMWARE and
    the documented parameter read frames, and nothing else these four print is a
    frame at all -- the rest is broadcast the controllers were already making.

    The allowlist grew and the guard got tighter, not looser. A
    read request has to BE a read: a remote frame carrying dlc 8, on a read api.
    A data frame on a read base would be a payload on a bus where one appended
    byte is the difference between a read and a write.
    """
    with sniff() as sniffer:
        _run(subcommand)
    capsys.readouterr()

    sent = list(sniffer.from_this_host)
    emitted = _bases(sent)
    assert emitted <= READ_REQUEST_BASES, (
        f"`spark {subcommand}` transmitted "
        + ", ".join(f"{ADMIN_BASES.get(b, hex(b))}" for b in sorted(emitted - READ_REQUEST_BASES))
        + ", and a read-only subcommand transmits only GET_FIRMWARE_VERSION and "
        "the documented parameter read frames\n" + sniffer.explain())
    for base in (sa.PARAM_WRITE, sa.PERSIST, sa.SET_CAN_ID):
        assert base not in emitted, (
            f"`spark {subcommand}` transmitted {ADMIN_BASES.get(base, hex(base))}"
            + "\n" + sniffer.explain())

    params = [m for m in sent
              if _is_read_request(m.arbitration_id & ~0x3F)
              and (m.arbitration_id & ~0x3F) != sa.GET_FIRMWARE]
    if subcommand == "audit":
        # Without this the loop below asserts nothing on the one subcommand that
        # actually reads parameters, and a regression to silence would pass.
        assert params, (
            "`spark audit` sent no parameter read frame at all, so it compared "
            "no declared setting against the hardware\n" + sniffer.explain())
    for m in params:
        assert m.is_remote_frame and m.dlc == sa.READ_FRAME_DLC, (
            f"0x{m.arbitration_id:08X} went out as a data frame with dlc "
            f"{m.dlc}; a parameter read is a remote frame carrying dlc "
            f"{sa.READ_FRAME_DLC}, and 26.1.6 answers nothing else\n"
            + sniffer.explain())


def test_no_admin_frame_is_addressed_to_the_unconfigured_id_zero(
        sniff, capsys, roles):
    """Catalogue B2: firmware 1.5.2 and later treat CAN id 0 as unconfigured,
    and a device there can never be enabled. There is no broadcast form of
    CLEAR_FAULTS or of any other admin frame -- each carries a device id -- so a
    frame at id 0 is a frame aimed at an address the fleet does not use, and on
    a bus where a controller had reverted to 0 it would be aimed at that
    controller. https://www.chiefdelphi.com/t/376796
    """
    with sniff() as sniffer:
        for subcommand in READ_ONLY_SUBCOMMANDS:
            _run(subcommand)
    capsys.readouterr()

    addressed = {F.dev_of(m.arbitration_id) for m in sniffer.from_this_host}
    assert 0 not in addressed, (
        "a read-only subcommand addressed CAN id 0\n" + sniffer.explain())
    assert addressed <= set(roles), (
        f"the tooling addressed ids outside devices: "
        f"{sorted(addressed - set(roles))}")


def test_a_protected_parameter_write_never_reaches_the_wire(adm, sniff, free_id):
    """The hard-limit parameters are the data-port safety interlock, and
    `write_param` refuses them rather than trusting callers to remember. The
    existing hardware test asserts the exception; this asserts the frame, which
    is the half a caller could bypass.

    Aimed at a CAN id nothing on this bus owns. If the guard ever regressed,
    this test would otherwise be the thing that disabled a hard limit on a real
    controller -- and with no parameter read on 26.1.6 there would be no way to
    prove it had been put back.
    """
    with sniff() as sniffer:
        for param_id in sorted(sa.PROTECTED_PARAMS):
            with pytest.raises(sa.ProtectedParameterError):
                adm.write_param(free_id, param_id, 0)

    writes = [m for m in sniffer.from_this_host
              if (m.arbitration_id & ~0x3F) == sa.PARAM_WRITE]
    assert not writes, (
        f"{len(writes)} PARAMETER_WRITE frame(s) reached the bus for "
        f"parameters {sorted(sa.PROTECTED_PARAMS)}\n" + sniffer.explain())


def test_no_driver_primitive_emits_a_frame_outside_the_admin_vocabulary(
        adm, sniff, serials, writable_id):
    """The subcommand sweep walks the paths those four commands take. This walks
    the driver's own read primitives, so a primitive that smuggles a frame is
    caught even where no subcommand calls it yet.

    `identify` is included and does something visible: the addressed
    controller's LED blinks for a few seconds. That is the whole effect, it is
    addressed by hardware serial rather than by id, and it is the only way to
    tell two controllers sharing an address apart.
    """
    with sniff() as sniffer:
        adm.inventory(1.5)
        adm.duplicates(1.5)
        adm.firmware(writable_id)
        adm.status_period_ms(writable_id, F.API_STATUS_1, seconds=1.0)
        sa.collect_status(adm.bus, seconds=1.0)
        if serials.get(writable_id):
            adm.identify(serials[writable_id])

    emitted = _bases(sniffer.from_this_host)
    allowed = {sa.GET_FIRMWARE, sa.IDENTIFY_UNIQUE}
    assert emitted <= allowed, (
        "the driver's read primitives transmitted "
        + ", ".join(ADMIN_BASES.get(b, hex(b)) for b in sorted(emitted - allowed))
        + "\n" + sniffer.explain())
