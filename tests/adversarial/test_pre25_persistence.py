"""What a pre-25 SPARK MAX keeps, and what it loses at the next power cycle.

This module exists because until the simulator answered
PARAMETER_WRITE, PERSIST_PARAMETERS, READ_PARAMETER_n and GET_PARAMETER_TYPES on
a pre-25 controller. All four are versionImplemented 25.0.0 in REV-Specs
spark-frames-2.1.0 and a 24.0.1 device carries none of them, so every pre-25
write path in this suite was either untested or tested against behaviour the
hardware does not have.

The property under test is the one that defines the generation. Every CAN write
to a MAX on 24.0.1 is RAM only. Measured on rig-max: eight controllers
set to BRAKE, read back BRAKE, an operator confirmed the brake resistance by
hand, sticky faults cleared to 0x0000, then the motor rail was cut and restored.
All eight came back COAST -- REV's factory default -- with sticky 0x0200
confirming the cycle. That evidence lives in the max.param_writes_are_ram_only
claim in provenance.py; this module is the regression test for it.

The pre-25 burn flash at api 0x072 is settled, and it changes
the SCOPE of the property above rather than the property itself. Measured on
rig-max: with a two-byte payload carrying the magic 15011 little-endian the frame
is answered 0x00 on its own arbitration id and commits the parameter table, ids
0-133, to flash, proven twice against a no-burn control across rail cycles; with
a zero-length payload it draws no reply at all and commits nothing. The status
periods are outside what it commits, measured separately. So a plain CAN write is
still RAM only, which is what the tests below assert, while persistence on this
generation IS reachable -- through a frame nothing in this package sends. See
pre25.burn_flash_api.
"""
from __future__ import annotations

import struct

import pytest

from sparklib import admin as sa
from sparksim import SparkBehaviour, attach, spark
from sparksim import frames as F

PRE25_FW = "24.0.1"
DEV = 12

# Idle Mode. COAST is 0 and REV's factory default; BRAKE is 1. The parameter the
# rig-max rail-cycle proof was run on, chosen there because an operator can feel
# the difference by hand.
P_IDLE_MODE = 6
COAST, BRAKE = 0, 1


def pre25(dev=DEV, **kw):
    """One controller on 24.0.1, throttled the way this package boots a MAX."""
    kw.setdefault("s0_ms", 50)
    kw.setdefault("s1_ms", 100)
    return spark(dev, "AAAA0001", firmware=PRE25_FW, **kw)


# -- the four frames this firmware does not carry -----------------------------

@pytest.mark.parametrize("what, call", [
    ("PARAMETER_WRITE", lambda adm: adm.write_param(DEV, 159, 20)),
    ("PERSIST_PARAMETERS", lambda adm: adm.persist(DEV, wait=0.3)),
    ("READ_PARAMETER pair", lambda adm: adm.read_param_pair(DEV, 158, wait=0.3)),
    ("GET_PARAMETER types", lambda adm: adm.param_types(DEV, 0, wait=0.3)),
])
def test_a_25plus_frame_is_dropped_without_a_nack(sim, what, call):
    """Every one is versionImplemented 25.0.0. An unmatched extended id is
    dropped in silence, so a wrong dialect and a dead controller look identical
    on the wire -- which is exactly why rig-max read as unwritable for a day."""
    adm = attach(sim([pre25()]))
    assert call(adm) is None, (
        f"the simulator answered {what} on firmware {PRE25_FW}. A 24.0.1 "
        "controller carries no such frame, so anything asserting on this answer "
        "is testing a fiction")


def test_the_legacy_dialect_answers_where_the_modern_one_does_not(sim):
    """The capability a MAX has and a Flex does not: every parameter reads back."""
    adm = attach(sim([pre25()]))
    r = adm.read_legacy_param(DEV, sa.PARAM_CAN_ID)
    assert r is not None and r["ok"], "parameter 0 did not answer"
    assert r["raw"] == DEV, (
        f"parameter 0 returned {r['raw']} for id {DEV}. It is the device's own "
        "CAN id and it is the self-check that reads are live and per-device "
        "rather than one cached answer")


def test_a_legacy_parameter_reply_carries_its_own_type_tag(sim):
    """Tag 2 is float32 in this dialect and Uint in the 25+ one. Reading a legacy
    reply through PARAM_TYPE turns every float parameter into an integer and
    reports it as successful."""
    adm = attach(sim([pre25()]))
    assert adm.read_legacy_param(DEV, 13)["type"] == "float32", (
        "parameter 13 is P 0, FLOAT in rev_parameter_index.tsv")
    assert adm.read_legacy_param(DEV, 59)["type"] == "uint32", (
        "parameter 59 is Smart Current Stall Limit, UINT32")


# -- the property that defines the generation ---------------------------------

def test_a_written_parameter_reads_back_before_the_power_cycle(sim):
    """The half that works, and the half that made this look like it persisted."""
    bus = sim([pre25()])
    adm = attach(bus)
    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == COAST, "premise"

    w = adm.write_legacy_param(DEV, P_IDLE_MODE, BRAKE)
    assert w is not None and w["ok"] and w["verified"], f"the write was refused: {w}"
    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == BRAKE, (
        "the value did not take even in RAM")


def test_every_can_write_to_a_max_is_lost_on_the_next_power_cycle(sim):
    """rig-max, re-confirmed. The regression test for
    max.param_writes_are_ram_only.

    Scoped to the write alone: no burn flash is sent here. If this ever fails
    because a write survived, something has taught the simulator that a plain
    legacy write reaches flash, which no measurement supports. Persistence on
    this generation is reachable, but only through the separate burn-flash frame
    below, so a change of outcome here is a defect rather than the new finding.
    """
    bus = sim([pre25()])
    adm = attach(bus)
    adm.write_legacy_param(DEV, P_IDLE_MODE, BRAKE)
    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == BRAKE, "premise"

    bus.controller(DEV).reboot(now=bus.clock.now)

    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == COAST, (
        "a plain CAN write to a pre-25 MAX survived a power cycle. Nothing in "
        "this package burns to flash on this generation: PERSIST_PARAMETERS is "
        "apiClass 63 index 15, versionImplemented 25.0.0, and while api 0x072 "
        "with the magic DOES commit (pre25.burn_flash_api, rig-max), "
        "this package never sends it. A write that survives without a burn "
        "means the model has stopped matching the hardware")


def test_the_reboot_shows_up_as_sticky_bit_9_and_nothing_else(sim):
    """hasReset is the whole warning system on this generation.

    Pre-25 has faults and sticky faults and no warning field, so the 25+ path
    that reads hasReset out of sticky_warnings finds nothing here. Two rail
    cycles on rig-max came back with sticky 0x0200 and no other bit set, and a
    rail cycle is precisely the event that sets hasReset and nothing else.
    """
    bus = sim([pre25()])
    dev = bus.controller(DEV)
    dev.reboot(now=bus.clock.now)

    assert dev.sticky_faults == 0x0200, (
        f"sticky faults read 0x{dev.sticky_faults:04X}, not the measured 0x0200")
    assert dev.sticky_warnings == 0, (
        "pre-25 has no warning field at all, so nothing may land in one")

    r = sa.normalised_reading({"status0": {"sticky_faults": dev.sticky_faults},
                               "generation": sa.GEN_PRE25})
    assert r["sticky_faults"] == ["hasReset"], r["sticky_faults"]
    assert r["sticky_warnings"] == [], (
        "an empty warning list here is the truth about the generation, and any "
        "audit branch keyed on it is unreachable on a MAX")


def test_the_status_periods_go_back_to_revs_cold_defaults(sim):
    """The periods are not parameters on this generation, so they were never in
    flash to survive, and the burn flash does not put them there either: rig-max set 0x060 on id 3 to a distinctive 77 ms, burned it with the magic
    and was answered 0x00, and read 10.0 ms back after the rail cycle. Between a
    rail cycle and the next driver start the bus runs at REV's defaults, whatever
    this package wrote at boot."""
    bus = sim([pre25()])
    dev = bus.controller(DEV)
    assert dev.period_ms(0x060) == 50, "premise: the boot throttle is applied"

    dev.reboot(now=bus.clock.now)

    assert dev.period_ms(0x060) == 10, (
        "0x060 did not return to REV's 10 ms cold default")
    assert dev.period_ms(0x061) == 20, "0x061 did not return to 20 ms"
    assert dev.period_ms(0x064) is None, (
        "0x064 broadcast after a rail cycle. This firmware does not emit the "
        "alternate-encoder frame in either state, measured on rig-max both "
        "before and after; its silence must never read as a lost write")


def test_flash_resident_provisioning_survives_the_cycle(sim):
    """The other half of the rig-max measurement, and the reason the fleet still
    drives after a rail cycle. The drive P of 2.0 and the 40 A stall limit were
    set over USB-C and survived both cycles; only what this package writes over
    CAN is volatile."""
    dev = pre25(ram={13: 2, 59: 40}, flash={13: 2, 59: 40})
    bus = sim([dev])
    adm = attach(bus)

    # Assert the write TOOK. Discarding this return made the test pass with the
    # driver's write path broken either way -- a dropped type tag or a
    # big-endian value -- because the post-reboot read returns the flash value 40
    # whether RAM was overwritten by flash or the write never left the driver.
    # Found by mutation testing.
    w = adm.write_legacy_param(DEV, 59, 80)
    assert w is not None and w["ok"] and w["verified"], (
        f"the RAM write never took, so this test would prove nothing about what "
        f"the reboot restored: {w}")
    assert adm.read_legacy_param(DEV, 59)["raw"] == 80, "premise: RAM holds 80"

    dev.reboot(now=bus.clock.now)

    assert adm.read_legacy_param(DEV, 59)["raw"] == 40, (
        "a flash-resident value did not come back after the cycle")


# -- the status-period write, which is a different frame entirely -------------

def test_a_period_write_moves_the_cadence_and_is_never_acknowledged(sim):
    """api class 6, two bytes, fire and forget. The arbitration id is shared with
    the LEGACY_STATUS_N broadcast and the SPARK separates them by DLC alone, so
    the only read-back there has ever been is measuring the cadence."""
    bus = sim([pre25()])
    adm = attach(bus)
    dev = bus.controller(DEV)

    sent_before = len(bus.sent)
    assert adm.set_legacy_status_period(DEV, 0, 20) is None, (
        "set_legacy_status_period returned something. Nothing comes back from "
        "this write, ever, and a caller that waits for a reply hangs")
    assert len(bus.sent) == sent_before + 1, "exactly one frame moves a period"
    assert dev.period_ms(0x060) == 20, "the cadence did not move"


def test_the_period_write_is_told_from_status_data_by_length(sim):
    """Two bytes sets the period; eight bytes on the same id is a controller
    broadcasting. A simulator that keyed on the arbitration id alone would take
    a status frame as a period write."""
    bus = sim([pre25()])
    dev = bus.controller(DEV)
    before = dev.period_ms(0x060)

    import can
    bus.send(can.Message(arbitration_id=sa.LEGACY_SET_PERIOD | DEV,
                         data=b"\x00" * 8, is_extended_id=True))

    assert dev.period_ms(0x060) == before, (
        "an eight-byte frame on the period id changed the cadence")


# -- the burn flash, and why nothing in this package sends it -----------------

def test_the_driver_has_never_sent_a_burn_flash_frame(sim):
    """SPARKMAX.md item 1, and the pre25.burn_flash_api claim.

    Nothing in this package sends api 0x072, and after the reason is
    the opposite of the one it used to be: the frame WORKS. Carrying the magic
    15011 little-endian it is answered 0x00 and commits the parameter table to
    flash, so a driver that sent it would spend one of a finite number of flash
    cycles and make whatever happened to be in RAM permanent, across a fleet of
    eight. Whether that is wise as routine practice is a separate question that
    nothing has settled, and there is no `spark persist` path for pre-25 to make
    it with. The api NUMBER's original source is still uncited in this tree --
    every document naming it traces back to one denylist comment, which derives
    the ARITHMETIC and not the number -- but its behaviour is now measured.
    """
    bus = sim([pre25()])
    adm = attach(bus)
    dev = bus.controller(DEV)

    adm.write_legacy_param(DEV, P_IDLE_MODE, BRAKE)
    adm.set_legacy_status_period(DEV, 0, 20)
    adm.persist(DEV, wait=0.3)

    assert dev.burn_flash_attempts == [], (
        "something in this package sent pre-25 burn flash. Measured on rig-max "
        " it is REAL and commits the parameter table, which is exactly "
        "why an accidental send matters: it spends a finite flash cycle and "
        "makes a change that outlives the power cycle, on a generation with no "
        "serial to identify the controller afterwards. See pre25.burn_flash_api")


class _KeepOpen:
    """`with _open() as adm` without the shutdown, so a test can read the bus."""

    def __init__(self, adm):
        self.adm = adm

    def __enter__(self):
        return self.adm

    def __exit__(self, *exc):
        return False


def test_spark_persist_refuses_before_sending_on_pre25(sim, monkeypatch, capsys):
    """A false SUCCESS is worse than a failure, and this command could produce one.

    PERSIST_PARAMETERS is versionImplemented 25.0.0, so a 24.0.1 device drops it
    and SparkAdmin.persist returns None. cmd_persist's None branch then measured
    the cadence against fault_frame's expected_ms -- which on pre-25 is the boot
    throttle -- so a fleet already sitting at the throttle matched, and it printed
    that the burn had landed and exited 0 with nothing written and nothing
    writable. Found by audit.

    The refusal has to come BEFORE the frame goes out, so this asserts on the
    wire as well as on the exit code.
    """
    from types import SimpleNamespace

    from sparklib import cli as spark_cli

    bus = sim([pre25()])
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))

    before = len(bus.sent)
    rc = spark_cli.cmd_persist(SimpleNamespace(id=DEV, window=1.0))
    out = capsys.readouterr().out

    assert rc == 2, f"cmd_persist did not refuse on a pre-25 bus:\n{out}"
    assert "burn landed" not in out, (
        f"cmd_persist reported a burn on a generation that cannot persist:\n{out}")
    sent = [m for m in bus.sent[before:]
            if F.base_of(m.arbitration_id) == F.PERSIST]
    assert not sent, "the refusal still put a PERSIST_PARAMETERS frame on the wire"


def test_the_simulator_models_the_measured_burn_protocol(sim):
    """Three cases, because the hardware distinguishes three.

    Measured on rig-max across ids 1, 2, 5, 6 and 7: only the first two
    bytes are read, little-endian, and they must be the 25+ persist magic. A
    driver that one day reads the accept byte back has to be testable against
    this, which it was not while the model ignored the payload and replied
    nothing.
    """
    import can

    cases = [
        (b"", None, "a zero-length frame draws no reply on 24.0.1"),
        (struct.pack("<H", F.PERSIST_MAGIC), "00", "the magic is accepted"),
        (struct.pack(">H", F.PERSIST_MAGIC), "ff", "big-endian is refused"),
        (b"\xa3", "ff", "a single byte is refused"),
        (struct.pack("<H", 12345), "ff", "a wrong value is refused"),
        (struct.pack("<H", F.PERSIST_MAGIC) + b"\x00\x00", "00",
         "trailing bytes after the magic are ignored"),
    ]
    for payload, want, why in cases:
        bus = sim([pre25()])
        adm = attach(bus)
        adm._drain()
        bus.send(can.Message(arbitration_id=F.LEGACY_BURN_FLASH | DEV,
                             data=payload, is_extended_id=True))
        reply = None
        end = bus.clock.now + 1.0
        while bus.clock.now < end:
            m = bus.recv(timeout=0.2)
            if m is not None and m.arbitration_id == (F.LEGACY_BURN_FLASH | DEV):
                reply = bytes(m.data).hex()
                break
        assert reply == want, f"{why}: expected {want!r}, got {reply!r}"


def test_the_burn_commits_the_parameter_table_and_not_the_periods(sim):
    """The split that the whole finding turns on, modelled.

    rig-max: Idle Mode burned and survived a rail cycle on two
    controllers against a no-burn control, while a status period burned at a
    distinctive 77 ms came back at REV's 10 ms. The periods are not in `ram`, so
    committing `ram` into `flash` cannot carry them, which is how the model gets
    this right by construction rather than by a special case.
    """
    import can

    bus = sim([pre25()])
    adm = attach(bus)
    dev = bus.controller(DEV)

    adm.write_legacy_param(DEV, P_IDLE_MODE, BRAKE)
    adm.set_legacy_status_period(DEV, 0, 77)
    assert dev.period_ms(0x060) == 77, "premise: the period write took"

    bus.send(can.Message(arbitration_id=F.LEGACY_BURN_FLASH | DEV,
                         data=struct.pack("<H", F.PERSIST_MAGIC),
                         is_extended_id=True))
    bus.recv(timeout=0.2)
    dev.reboot(now=bus.clock.now)

    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == BRAKE, (
        "the burn did not commit the parameter table")
    assert dev.period_ms(0x060) == 10, (
        "a status period survived the burn. They are not part of what api 0x072 "
        "commits: a controller burned at 77 ms read 10.0 ms after a rail cycle "
        "on rig-max")


def test_a_controller_that_refuses_the_burn_is_modelled_too(sim):
    """The defect case. A correctly-formed burn that is refused answers 0xFF and
    commits nothing, so a driver cannot read a refusal as a success."""
    import can

    dev = pre25(behaviour=SparkBehaviour(legacy_burn_flash_works=False))
    bus = sim([dev])
    adm = attach(bus)

    adm.write_legacy_param(DEV, P_IDLE_MODE, BRAKE)
    adm._drain()
    bus.send(can.Message(arbitration_id=F.LEGACY_BURN_FLASH | DEV,
                         data=struct.pack("<H", F.PERSIST_MAGIC),
                         is_extended_id=True))
    reply, end = None, bus.clock.now + 1.0
    while bus.clock.now < end:
        m = bus.recv(timeout=0.2)
        if m is not None and m.arbitration_id == (F.LEGACY_BURN_FLASH | DEV):
            reply = bytes(m.data).hex()
            break
    assert reply == "ff", f"a refused burn must answer 0xFF, got {reply!r}"
    dev.reboot(now=bus.clock.now)
    assert adm.read_legacy_param(DEV, P_IDLE_MODE)["raw"] == COAST, (
        "a refused burn committed anyway")


# -- the parameter CLI, and the three things it must refuse -------------------

def _params(monkeypatch, adm, **kw):
    from types import SimpleNamespace

    from sparklib import cli as spark_cli

    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: {DEV: "steer/RB"})
    args = SimpleNamespace(id=None, param=None, set=None, all=False,
                           wait=0.2, window=1.0)
    for k, v in kw.items():
        setattr(args, k, v)
    return spark_cli.cmd_params(args)


@pytest.mark.parametrize("pid, must_say", [
    (0, "protected"),
    (2, "protected"),
    (50, "protected"),
    (159, "api class 6"),
    (165, "api class 6"),
    (134, "LEGACY_PARAM_MAX"),
    (255, "Persist Parameters"),
])
def test_the_parameter_command_refuses_what_it_must(sim, monkeypatch, capsys,
                                                    pid, must_say):
    """Three separate hazards, and the refusal has to name which one it is.

    Protected ids cost a controller its identity on a bus with no serial to find
    it again. 158-165 are not parameters on this generation. Above 133 the same
    api range carries COMMANDS, and 0x300 | 255 is byte for byte the persist id.
    """
    adm = attach(sim([pre25()]))
    before = len(adm.bus.sent)
    rc = _params(monkeypatch, adm, id=DEV, param=pid, set=1)
    out = capsys.readouterr().out

    assert rc == 2, f"parameter {pid} was not refused:\n{out}"
    assert must_say in out, f"the refusal does not say why:\n{out}"
    writes = [m for m in adm.bus.sent[before:] if len(m.data) == 5]
    assert not writes, f"the refusal still put a write on the wire for {pid}"


def test_the_parameter_command_reads_a_25plus_bus(sim, monkeypatch, capsys):
    """It used to refuse this bus outright, because 26.1.6 was believed to answer
    no parameter read. Measured on rig-flex, it answers all of 0-255,
    including ids a pre-25 controller cannot reach at all."""
    from sparksim.fleet import spark as _spark

    adm = attach(sim([_spark(DEV, "AAAA0001", firmware="26.1.6",
                             ram={6: 1, 59: 80, 224: 100})]))
    rc = _params(monkeypatch, adm, id=DEV, all=True)
    out = capsys.readouterr().out

    assert rc == 0, f"the command refused a bus that answers reads:\n{out}"
    assert "Idle Mode" in out and "Smart Current Stall Limit" in out, out
    assert "Status 9 Period" in out, (
        "parameter 224 is past LEGACY_PARAM_MAX and unreachable on pre-25, which "
        "is the coverage this generation adds\n" + out)


def test_a_25plus_write_uses_the_25plus_dialect(sim, monkeypatch, capsys):
    """The call site the read change exposed.

    cmd_params stopped refusing a 25+ bus, so --set reached _params_write, which
    read and wrote through the pre-25 legacy api. A 26.1.6 controller does not
    carry those frames, so the write would have gone out and been dropped.
    """
    from sparksim.fleet import spark as _spark

    bus = sim([_spark(DEV, "AAAA0001", firmware="26.1.6", ram={159: 20})])
    adm = attach(bus)
    before = len(bus.sent)
    rc = _params(monkeypatch, adm, id=DEV, param=159, set=50)
    out = capsys.readouterr().out

    assert rc == 0, out
    assert bus.controllers[0].ram[159] == 50, out
    legacy = [m for m in bus.sent[before:]
              if F.legacy_param_id_of(m.arbitration_id) is not None]
    assert not legacy, (
        f"{len(legacy)} legacy parameter frame(s) went to a 26.1.6 controller, "
        "which does not carry that api")


def test_a_write_needs_both_an_id_and_a_parameter(sim, monkeypatch, capsys):
    """A write is aimed at one controller and one parameter, never at a fleet."""
    adm = attach(sim([pre25()]))
    assert _params(monkeypatch, adm, param=6, set=1) == 2
    assert "needs both" in capsys.readouterr().out


def test_a_read_is_non_destructive(sim, monkeypatch):
    """Reading the whole table must put no write frame on the wire at all."""
    adm = attach(sim([pre25()]))
    before = len(adm.bus.sent)
    assert _params(monkeypatch, adm, id=DEV) == 0
    writes = [m for m in adm.bus.sent[before:] if len(m.data) == 5]
    assert not writes, f"{len(writes)} write frame(s) went out during a read"


# -- identify, which is addressed differently on each generation --------------

def test_pre25_identify_is_addressed_by_id_with_no_payload(sim):
    """The form REV Hardware Client actually sends on 24.0.1.

    Recovered by capturing RHC's LED button on rig-max: exactly three
    DLC-0 frames at 02051D81, 02051D82 and 02051D83 in 9831 lines of candump, one
    per controller blinked. Reproducing them from this driver blinked the same
    controllers, confirmed by an operator.
    """
    bus = sim([pre25()])
    adm = attach(bus)
    dev = bus.controller(DEV)

    adm.identify_by_id(DEV)
    assert dev.identify_count == 1, "the pre-25 identify form did not reach it"


def test_the_25plus_form_does_nothing_on_pre25(sim):
    """Why the shipped command reported success and never blinked.

    A broadcast carrying a serial is the firmware-25 model. A 24.0.1 controller
    ignores it, and nothing acknowledges either form, so the CLI had no way to
    notice.
    """
    bus = sim([pre25()])
    adm = attach(bus)
    dev = bus.controller(DEV)

    adm.identify("AAAA0001")
    assert dev.identify_count == 0, (
        "a 25+ broadcast-and-serial identify blinked a pre-25 controller, which "
        "would mean the model no longer matches the hardware")


def test_pre25_identify_reaches_only_the_addressed_controller(sim):
    """It is addressed by CAN id, so it must not blink the whole fleet."""
    bus = sim([pre25(3), pre25(4)])
    adm = attach(bus)

    adm.identify_by_id(3)
    assert bus.controller(3).identify_count == 1
    assert bus.controller(4).identify_count == 0, (
        "identify addressed to id 3 also reached id 4")


def test_the_cli_refuses_a_serial_on_pre25_and_an_id_on_fw25(sim, monkeypatch,
                                                             capsys):
    """Each generation names the argument the OTHER one needs, so a wrong flag
    has to be refused with the right advice rather than sent and ignored."""
    from types import SimpleNamespace

    from sparklib import cli as spark_cli
    from sparksim.fleet import spark as _spark

    adm = attach(sim([pre25()]))
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))
    rc = spark_cli.cmd_identify(SimpleNamespace(serial="AAAA0001", id=None,
                                                window=1.0))
    out = capsys.readouterr().out
    assert rc == 2 and "--id" in out, out

    adm2 = attach(sim([_spark(DEV, "AAAA0001", firmware="26.1.6")]))
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm2))
    rc = spark_cli.cmd_identify(SimpleNamespace(serial=None, id=DEV, window=1.0))
    out = capsys.readouterr().out
    assert rc == 2 and "--serial" in out, out


# -- duplicate CAN ids, which is what the identity work was actually for ------

def test_a_duplicate_id_is_found_on_pre25(sim):
    """The problem the whole identity thread exists to solve.

    Two controllers on one CAN id are invisible to an id-based scan: they look
    like one, and every addressed write reaches both while only the first reply
    is read. Until this was undetectable on pre-25, because detection
    keyed on UNIQUE_ID broadcasts and this generation sends none.

    The fingerprint at api 0x094 is REQUESTED rather than broadcast, so one
    request to a shared id draws one answer per controller sitting on it.
    """
    bus = sim([pre25(7), spark(7, "BBBB0002", firmware=PRE25_FW)])
    adm = attach(bus)

    dups = adm.duplicates(generation=sa.GEN_PRE25, ids=[7])
    assert 7 in dups, (
        "two controllers share id 7 and duplicate detection did not see it")
    assert len(dups[7]) == 2, f"expected two distinct fingerprints, got {dups[7]}"


def test_a_clean_pre25_bus_reports_no_duplicates(sim):
    """The other direction. A detector that always fires is not a detector."""
    bus = sim([pre25(3), spark(4, "BBBB0002", firmware=PRE25_FW)])
    adm = attach(bus)
    assert adm.duplicates(generation=sa.GEN_PRE25, ids=[3, 4]) == {}


def test_the_fingerprint_is_unique_and_addressed(sim):
    """It must answer the id it was asked, and differ between controllers."""
    bus = sim([pre25(3), spark(4, "BBBB0002", firmware=PRE25_FW)])
    adm = attach(bus)

    a, b = adm.read_fingerprint(3), adm.read_fingerprint(4)
    assert a and b and a != b, f"fingerprints not distinct: {a} {b}"


def test_duplicate_detection_is_now_available_on_both_generations():
    """It was 25+ only, and `spark duplicates` printed CANNOT TELL on a MAX."""
    assert sa.duplicate_detection_available(sa.GEN_PRE25) is True
    assert sa.duplicate_detection_available(sa.GEN_FW25) is True
