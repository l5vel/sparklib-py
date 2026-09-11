"""The config-write, recovery and identity tiers, on the OTHER generation.

Those three modules build rig-flex and only rig-flex: eight SPARK Flex on 26.1.6.
So every scenario in them is a firmware-25 scenario, and the MAX path through
`spark repair`, `spark audit`, `spark clear` and `spark set-id` had no
adversarial treatment at all.

That matters because every generation defect this package has shipped was a Flex
assumption reaching a MAX, and none of them could have failed a test that only
ever built a Flex:

  * the startup drive gate decoded a legacy buffer with the firmware-25 pair, so
    it was blind on rig-max and passed a hard-limited controller
  * `print_diagnostics` read pre-25 byte 6 with the firmware-25 bit layout
  * `coverage_note` scored a MAX bus against the Flex fleet's deviation list
  * `_defaults_path` resolved the Flex file whatever product was declared

This module runs the same SCENARIOS as those tiers against a pre-25 fleet. It is
not a dialect test: `test_sparkmax_bus.py` and `test_pre25_persistence.py` cover
the dialect. What is new here is the CLI behaviour on the other generation.
"""
from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparksim import attach, spark
from sparksim import frames as F
from sparksim.fleet import (BASE02_FIRMWARE, ROLES_MAX, SERIALS_MAX,
                            build_fleet_pre25)

from types import SimpleNamespace


pytestmark = pytest.mark.usefixtures("pinned_to_max")


def _cli(monkeypatch, bus):
    def _open():
        adm = attach(bus)
        adm.close = lambda: None
        return adm
    monkeypatch.setattr(spark_cli, "_open", _open)


def _args(**kw):
    kw.setdefault("window", 1.0)
    return SimpleNamespace(**kw)


def _problems(capsys):
    return [ln.strip()[2:].strip() for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("  - ")]


# -- the premise ---------------------------------------------------------------

def test_the_pinned_robot_and_the_simulated_fleet_are_the_same_pre25_machine(
        rig_max, roles_pre25):
    """Without this the rest of the module tests a fleet no robot has."""
    assert spark_cli._base().controller_type == "sparkmax"
    assert sa.motor_defaults_key() == "sparkmax", (
        "the declared configuration still resolves the Flex block, so every "
        "comparison below would be against the wrong robot's settings")

    ids = {c.dev for c in rig_max.controllers}
    assert ids == set(roles_pre25), f"simulated {sorted(ids)} != configured"
    for c in rig_max.controllers:
        assert c.is_pre25(), (
            f"id {c.dev} is on {c.firmware}. A fleet that is not pre-25 tests "
            "nothing this module exists for")


def test_a_healthy_pre25_bus_audits_clean_and_exits_zero(
        rig_max, monkeypatch, capsys):
    """The control. Every failure below is read against this."""
    _cli(monkeypatch, rig_max)

    rc = spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out

    assert rc == 0, f"a healthy pre-25 fleet did not audit clean:\n{out}"


# -- config write, the tier the MAX path never had -----------------------------

@pytest.mark.parametrize("param_id", sorted(sa.PROTECTED_PARAMS))
def test_the_protected_parameters_never_reach_a_pre25_bus(rig_max, param_id):
    """Same guard, other dialect.

    The 25+ tier proves PARAMETER_WRITE never carries these. Pre-25 does not use
    that frame at all: it writes through the legacy parameter dialect, so the
    guard has to hold on a second code path. Parameters 50-53 are the hard-limit
    interlock, measured on rig-max to withhold output entirely.
    """
    adm = attach(rig_max)
    before = len(rig_max.sent)

    with pytest.raises(Exception):
        adm.write_legacy_param(3, param_id, 1)

    assert len(rig_max.sent) == before, (
        f"a write to protected parameter {param_id} "
        f"({sa.PROTECTED_PARAMS[param_id]}) reached the wire through the legacy "
        "dialect. The 25+ guard does not cover this path")


def test_a_refused_legacy_write_is_not_reported_as_success(rig_max):
    """Measured on rig-max: parameter 11 accepts 100 and refuses 1.0
    with STATUS 4, keeping its old value. A caller that reads the echo without
    reading its status believes it set a value the controller rejected, and an
    experiment built on that write measures nothing."""
    from sparksim import SparkBehaviour

    dev = 3
    rig_max.controller(dev).behaviour = SparkBehaviour(legacy_param_refuse_status=4)

    adm = attach(rig_max)
    reply = adm.write_legacy_param(dev, 11, 1)

    assert reply is None or reply.get("ok") is False, (
        "a refused legacy parameter write reported success. The status byte in "
        "the echo is the only thing that distinguishes a refusal from a write "
        "that took")


def test_no_firmware_25_write_or_persist_frame_reaches_a_pre25_bus(
        rig_max, monkeypatch, capsys):
    """PARAMETER_WRITE and PERSIST_PARAMETERS are both versionImplemented
    25.0.0. Sending either at a pre-25 controller is not merely useless, it is
    silent: the frame is dropped and the caller reads the silence as a dead
    controller rather than as a wrong dialect."""
    _cli(monkeypatch, rig_max)
    before = list(rig_max.sent)

    spark_cli.cmd_audit(_args())
    capsys.readouterr()

    new = rig_max.sent[len(before):]
    for m in new:
        api = (m.arbitration_id >> 6) & 0x3FF
        assert api not in (F.PARAM_WRITE >> 6 & 0x3FF, F.PERSIST >> 6 & 0x3FF), (
            f"api 0x{api:03X} went out at a pre-25 fleet; it is "
            "versionImplemented 25.0.0 and is dropped in silence there")


# -- recovery ------------------------------------------------------------------

def test_the_coverage_note_is_scored_against_the_pre25_declared_file(rig_max):
    """The denominator has to come from the product actually on the bus.

    It came from a hardcoded list that is byte for byte the FLEX file's
    deviations, so a MAX bus was scored against settings its own declared file
    does not carry, while the ones it does carry went uncounted.
    """
    note = sa.coverage_note(8, sa.GEN_PRE25)
    declared = sa.declared_deviations()

    assert f"of {len(declared)} settings" in note, (
        f"the note scores against something other than the {len(declared)} "
        f"settings sparkmax_motor_defaults.yaml declares:\n{note}")
    assert "Idle Mode" not in note, (
        "the note lists Idle Mode, which the Flex file marks as deviating and "
        "the MAX file does not. The Flex list has reached a MAX bus again")


def test_a_clean_pre25_audit_still_says_what_it_could_not_check(
        rig_max, monkeypatch, capsys):
    """A clean audit is not a healthy motor, on either generation."""
    _cli(monkeypatch, rig_max)

    rc = spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "coverage:" in out, "a clean pre-25 audit disclosed nothing"
    assert "status periods are not parameters on this generation" in out, (
        "the note has to say WHY the rest is unchecked here, because the reason "
        "differs from the Flex one: periods move on LEGACY_SET_PERIOD and refuse "
        "a parameter read")


def test_a_dark_pre25_controller_is_named_by_its_role(
        rig_max, monkeypatch, capsys, roles_pre25):
    _cli(monkeypatch, rig_max)
    gone = 3
    rig_max.controllers = [c for c in rig_max.controllers if c.dev != gone]

    rc = spark_cli.cmd_audit(_args())
    problems = _problems(capsys)

    assert rc == 1, "an eight-controller fleet missing one exited zero"
    assert any(str(gone) in p for p in problems), (
        f"id {gone} ({roles_pre25[gone]}) went dark and the audit did not name "
        f"it: {problems}")


# -- identity ------------------------------------------------------------------

def test_identity_on_pre25_comes_from_the_fingerprint_not_a_broadcast(rig_max):
    """Pre-25 sends no UNIQUE_ID at all -- measured on rig-max, zero frames on
    0x2F0 in four seconds -- so an inventory that does not ASK gets nothing and
    the swapped-controller check has nothing to compare."""
    adm = attach(rig_max)

    passive = adm.inventory(1.0)
    assert all(i["serial"] is None for i in passive.values()), (
        "a pre-25 fleet reported serials without being asked, so this fixture "
        "is broadcasting something the hardware does not")

    asked = adm.inventory(1.0, with_fingerprint=True)
    assert any(i["serial"] for i in asked.values()), (
        "with_fingerprint=True returned no identity on a pre-25 fleet, so "
        "set-id, duplicates and the baseline all have nothing to key on")


def test_duplicate_detection_works_on_pre25(rig_max):
    """The problem serials were wanted for in the first place."""
    assert sa.duplicate_detection_available(sa.GEN_PRE25), (
        "duplicate detection is reported unavailable on pre-25, which is the "
        "generation rig-max runs and the one where a duplicate id is hardest to "
        "see, because there is no serial in any broadcast to tell twins apart")


def test_a_pre25_setting_that_drifted_from_the_declared_file_is_reported(
        rig_max, monkeypatch, capsys):
    """The capability the coverage note used to admit it did not have.

    The audit used to compare only what a controller broadcasts, because the 25+
    reads were going out in a form 26.1.6 ignores. Pre-25 answers reads for
    parameters 0-133 on its own dialect and the audit consults them here. Without
    this test the whole pre-25 read path could be deleted and every other test in
    this module would still pass -- checked by mutation, and it did.
    """
    dev, param = 3, 59                      # Smart Current Stall Limit
    declared = sa.deviating_settings("steer")[param]["value"]
    rig_max.controller(dev).ram[param] = int(declared) + 7

    _cli(monkeypatch, rig_max)
    rc = spark_cli.cmd_audit(_args())
    problems = _problems(capsys)

    assert rc == 1, "a controller holding the wrong declared value audited clean"
    assert any(f"id {dev}" in p and str(param) in p for p in problems), (
        f"parameter {param} drifted on id {dev} and the audit did not name it. "
        f"It is readable over CAN on this generation, so reporting it as "
        f"unverifiable is a driver limit stated as a hardware fact: {problems}")


def test_the_pre25_coverage_note_counts_what_it_actually_read(
        rig_max, monkeypatch, capsys):
    """The numerator has to move when the audit reads something, or the note is
    a fixed admission rather than a measurement of its own coverage."""
    _cli(monkeypatch, rig_max)
    spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out

    assert "read over CAN and compared against the declared file:" in out, (
        f"the pre-25 audit reported nothing as read over CAN, so either it "
        f"stopped consulting read_legacy_param or the note stopped saying "
        f"so:\n{out}")
    assert "Smart Current Stall Limit" in out and "P 0" in out, (
        "both readable deviating settings should be named as compared")
