"""`spark provision`: restoring a whole declared configuration to one controller.

`spark repair` writes one parameter and proves it by measuring a cadence, which
is the only declared setting a firmware-25+ SPARK broadcasts. This command writes
whatever has drifted and can only prove it by reading each value back. That is
the reason the two are separate verbs rather than one command with a flag: the
blast radius should be readable in the command itself.

The echo is not the check. Firmware 26.1.6 answers Success and echoes 2 for a
BOOL written 2, and stores the 2, so every write here is judged by a read taken
afterwards through the same comparator `spark audit` uses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparksim import attach, build_fleet, build_fleet_pre25
from sparksim import frames as F
from sparksim.faults import SparkBehaviour

DEV = 12
ROLES = {10: "drive/RB", 11: "steer/RB", 12: "drive/RF", 13: "steer/RF",
         14: "drive/LF", 15: "steer/LF", 16: "drive/LB", 17: "steer/LB"}


class _Keep:
    def __init__(self, adm):
        self.adm = adm

    def __enter__(self):
        return self.adm

    def __exit__(self, *exc):
        return False


def _run(monkeypatch, adm, roles=None, **kw):
    monkeypatch.setattr(spark_cli, "_open", lambda: _Keep(adm))
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: roles or ROLES)
    args = SimpleNamespace(id=DEV, write=False, persist=False, wait=0.3,
                           window=1.0)
    for k, v in kw.items():
        setattr(args, k, v)
    return spark_cli.cmd_provision(args)


def _writes(bus, pid=None):
    return [w for c in bus.controllers for w in c.write_log
            if pid is None or w.param_id == pid]


def _persists(bus):
    return [m for m in bus.sent if F.base_of(m.arbitration_id) == F.PERSIST]


def _drifted(pid, value, **kw):
    """rig-flex's eight, with one declared setting moved off its declared value."""
    fleet = build_fleet(ids=sorted(ROLES), **kw)
    for c in fleet:
        if c.dev == DEV:
            c.ram[pid] = value
            c.flash[pid] = value
    return fleet


# -- the survey ---------------------------------------------------------------

def test_a_clean_controller_reports_nothing_to_write(sim, monkeypatch, capsys):
    bus = sim(build_fleet(ids=sorted(ROLES)))
    rc = _run(monkeypatch, attach(bus))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "nothing to write" in out
    assert not _writes(bus), "a survey wrote to a controller"


def test_the_survey_reads_every_declared_setting_not_only_the_deviating_ones(
        sim, monkeypatch, capsys):
    """The encoder counts-per-rev typo hid for weeks in a setting nothing read.

    The audit reads the deviating subset because it pays that cost across eight
    controllers. A provision run touches one, so it compares the whole file.
    """
    bus = sim(build_fleet(ids=sorted(ROLES)))
    _run(monkeypatch, attach(bus))
    out = capsys.readouterr().out
    declared = sa.motor_settings("drive")
    assert len(declared) > len(sa.deviating_settings("drive"))
    for pid, st in declared.items():
        assert f"{pid:>4}  {st['name']}" in out, (
            f"parameter {pid} ({st['name']}) is declared and was not surveyed")


def test_an_enum_is_shown_by_name_and_ordinal(sim, monkeypatch, capsys):
    """"BRAKE" against "1" is what made the audit report a healthy motor."""
    bus = sim(build_fleet(ids=sorted(ROLES)))
    _run(monkeypatch, attach(bus))
    out = capsys.readouterr().out
    assert "BRAKE (1)" in out and "MAIN_ENCODER (1)" in out


# -- the dry run --------------------------------------------------------------

def test_a_dry_run_reports_drift_and_sends_no_write(sim, monkeypatch, capsys):
    bus = sim(_drifted(161, 20))
    rc = _run(monkeypatch, attach(bus))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "would be written" in out and "Status 3 Period" in out
    assert not _writes(bus), (
        "a dry run wrote to the controller: "
        f"{[(w.param_id, w.requested) for w in _writes(bus)]}")


# -- the write path -----------------------------------------------------------

def test_a_drifted_setting_is_written_and_read_back(sim, monkeypatch, capsys):
    bus = sim(_drifted(161, 20))
    rc = _run(monkeypatch, attach(bus), write=True)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "landed" in out
    assert bus.controller(DEV).ram[161] == 50
    assert not _persists(bus), "a write without --persist burned to flash"


def test_a_write_that_reports_success_and_did_not_land_is_not_persisted(
        sim, monkeypatch, capsys):
    """CD 456184, and the reason the readback exists. The controller answers
    Success and echoes what was asked, and holds the old value."""
    bus = sim(_drifted(161, 20, behaviour=SparkBehaviour(
        ignore_writes_for=frozenset({161}))))
    rc = _run(monkeypatch, attach(bus), write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "NOT HELD" in out, out
    assert not _persists(bus), (
        "a run whose write did not land was burned to flash, which makes a "
        "half-applied configuration permanent")


def test_a_run_stops_at_the_first_bad_row_and_never_burns(sim, monkeypatch, capsys):
    """A refusal repeated is still a refusal, and the writes after it would be
    aimed at a controller no longer behaving the way the dialect says."""
    fleet = build_fleet(ids=sorted(ROLES),
                        behaviour=SparkBehaviour(write_result=1))
    for c in fleet:
        if c.dev == DEV:
            for pid, val in ((161, 20), (163, 20), (164, 20)):
                c.ram[pid] = c.flash[pid] = val
    bus = sim(fleet)
    rc = _run(monkeypatch, attach(bus), write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "STOPPED at parameter" in out, out
    assert len({w.param_id for w in _writes(bus)}) == 1, (
        "the run kept writing past a refusal: "
        f"{sorted({w.param_id for w in _writes(bus)})}")
    assert not _persists(bus)


def test_a_landed_run_burns_exactly_once(sim, monkeypatch, capsys):
    """Flash endurance is finite (CD 455171), so one burn per run and never one
    per parameter."""
    fleet = build_fleet(ids=sorted(ROLES))
    for c in fleet:
        if c.dev == DEV:
            for pid, val in ((161, 20), (163, 20), (164, 20)):
                c.ram[pid] = c.flash[pid] = val
    bus = sim(fleet)
    rc = _run(monkeypatch, attach(bus), write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert len(_persists(bus)) == 1, (
        f"{len(_persists(bus))} burn(s) for one run")


# -- the refusals -------------------------------------------------------------

def test_a_drifted_protected_parameter_stops_the_whole_run(sim, monkeypatch, capsys):
    """Parameter 2 is Motor Type, which is CD 424550: a controller that lights
    up, answers the client and will not turn. The ORDINARY drift beside it must
    not be written either, because a controller this tooling cannot fully
    restore must not be left holding a configuration nobody declared."""
    fleet = build_fleet(ids=sorted(ROLES))
    for c in fleet:
        if c.dev == DEV:
            c.ram[2] = c.flash[2] = 0        # BRUSHED, declared BRUSHLESS
            c.ram[161] = c.flash[161] = 20   # an ordinary drift beside it
    bus = sim(fleet)
    rc = _run(monkeypatch, attach(bus), write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "protected parameter" in out and "Motor Type" in out, out
    assert not _writes(bus), (
        "the run wrote past a protected-parameter finding: "
        f"{sorted({w.param_id for w in _writes(bus)})}")
    assert not _persists(bus)


def test_an_id_with_no_role_is_refused_before_the_bus_opens(sim, monkeypatch, capsys):
    """P 0 is the one setting declared by role. Provisioning an id with no role
    would write every common setting and silently skip that gain."""
    bus = sim(build_fleet(ids=sorted(ROLES)))
    rc = _run(monkeypatch, attach(bus), id=99, write=True)
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "not in devices" in out
    assert not bus.sent, "a refused run put frames on the bus"


def test_a_silent_target_is_refused(sim, monkeypatch, capsys):
    """The dialect comes from the wire, and a controller that broadcast nothing
    offers no wire. Defaulting the generation picks a write dialect by guess, and
    PARAMETER_WRITE is versionImplemented 25.0.0, so a 24.0.1 device drops it
    in silence, which reads exactly like a dead controller. The reverse
    direction is unmeasured and the generations do overlap on api class 6."""
    fleet = [c for c in build_fleet(ids=sorted(ROLES)) if c.dev != DEV]
    bus = sim(fleet)
    rc = _run(monkeypatch, attach(bus), write=True)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "broadcast nothing" in out, out
    assert not _writes(bus)


def test_persist_is_refused_on_a_pre25_bus(sim, monkeypatch, capsys):
    """PERSIST_PARAMETERS is versionImplemented 25.0.0, so 24.0.1 drops it in
    silence and a burn that never happened reads like one whose reply was lost."""
    from sparksim.fleet import ROLES_MAX
    monkeypatch.setattr(spark_cli, "_use_declared_product", lambda: "sparkmax")
    bus = sim(build_fleet_pre25())
    dev = sorted(ROLES_MAX)[0]
    rc = _run(monkeypatch, attach(bus), roles=ROLES_MAX, id=dev,
              write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "apiClass 63" in out, out
    assert not _persists(bus) and not _writes(bus)


# -- the two commands must agree ----------------------------------------------

def test_audit_and_provision_name_the_same_drift(sim, monkeypatch, capsys):
    """Both go through declared_rows, so they cannot disagree about what has
    drifted. A second comparator with its own float tolerance would make one
    command report a gain the other calls clean."""
    bus = sim(_drifted(161, 20))
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_open", lambda: _Keep(adm))
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: ROLES)
    spark_cli.cmd_audit(SimpleNamespace(window=1.0))
    audit_out = capsys.readouterr().out
    _run(monkeypatch, adm)
    provision_out = capsys.readouterr().out

    assert "parameter 161" in audit_out, audit_out
    assert "Status 3 Period" in provision_out
    assert "provision" in audit_out, (
        "the audit remedy has to name the command that fixes it")


# -- the encoder is the production one ----------------------------------------

@pytest.mark.parametrize("product", ["sparkflex", "sparkmax"])
@pytest.mark.parametrize("role", ["steer", "drive"])
def test_every_declared_value_round_trips_through_the_encoder(product, role):
    """declared_raw_value and declared_param_value are inverses over both files.

    The simulator's provisioned RAM is built from the same encoder, so a
    controller the tests call provisioned holds what a provision run writes.
    """
    defaults = sa.load_motor_defaults(controller_type=product)
    for pid, st in sa.motor_settings(role, defaults).items():
        declared, kind = st.get("value"), st.get("type")
        raw = sa.declared_raw_value(declared, kind, pid)
        assert raw is not None, (
            f"{product} declares {st['name']} (parameter {pid}) as "
            f"{declared!r} and nothing can encode it")
        back = sa.declared_param_value(raw, kind)
        assert sa._declared_matches(declared, back, pid), (
            f"{product} {st['name']} (parameter {pid}) does not round trip: "
            f"{declared!r} -> {raw} -> {back!r}")


def test_the_simulator_holds_no_second_encoder():
    """One operation, one implementation. A copy here would let a fixture the
    tests call provisioned hold different words than production writes."""
    import ast
    import pathlib
    src = (pathlib.Path(sa.__file__).resolve().parents[1]
           / "tests" / "support" / "sparksim" / "fleet.py")
    tree = ast.parse(src.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_declared_ram")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "float_bits" not in called and "param_enum_value" not in called, (
        "the simulator encodes declared values itself instead of going through "
        "spark_admin.declared_raw_value")
    assert "declared_raw_value" in called


# -- what the adversarial review caught after the first implementation --------

def test_a_broken_fleet_assumption_warns_and_provisions_anyway(
        sim, monkeypatch, capsys):
    """`unexpected_generation` is documented as a warning, and cmd_status and
    cmd_audit both print it and continue. Refusing here would block a legitimate
    run: a SPARK MAX updated to 25.0.0 trips it, and the MAX file's values are
    still the right ones for that controller. The VALUES come from the product's
    file and the DIALECT comes from the wire, so neither is being guessed."""
    monkeypatch.setattr(spark_cli, "_use_declared_product", lambda: "sparkmax")
    bus = sim(_drifted(161, 20))
    rc = _run(monkeypatch, attach(bus), write=True)
    out = capsys.readouterr().out
    assert "FLEET ASSUMPTION BROKEN" in out, out
    assert rc == 0, out
    assert bus.controller(DEV).ram[161] == 50, (
        "a fleet-assumption warning stopped a run it should only have annotated")


def test_the_duplicate_check_is_told_the_generation(sim, monkeypatch, capsys):
    """`duplicates()` resolves `generation or DEFAULT_GENERATION`, so the bare
    call detects nothing on a pre-25 bus, where the tell is two differing
    STATUS_0 payloads and not a UNIQUE_ID broadcast. A duplicate-blind write
    reaches two controllers and reads one reply."""
    seen = {}
    bus = sim(_drifted(161, 20))
    adm = attach(bus)
    real = adm.duplicates

    def spy(seconds=6.0, generation=None, ids=None):
        seen["generation"] = generation
        return real(seconds, generation=generation, ids=ids)

    monkeypatch.setattr(adm, "duplicates", spy)
    _run(monkeypatch, adm, write=True)
    capsys.readouterr()
    assert seen.get("generation") is not None, (
        "the duplicate check was called without the generation already measured "
        "in this command, so it cannot see a pre-25 duplicate")


def test_a_run_that_could_not_write_everything_is_never_burned(
        sim, monkeypatch, capsys):
    """A row skipped because its declared value does not encode leaves the
    controller partly restored. RAM is undone by a power cycle; flash is not."""
    fleet = build_fleet(ids=sorted(ROLES))
    for c in fleet:
        if c.dev == DEV:
            c.ram[161] = c.flash[161] = 20      # writable drift
            c.ram[9] = c.flash[9] = 0           # declared MAIN_ENCODER, drifted
    bus = sim(fleet)
    # Only parameter 9 becomes unencodable, so it lands in `skipped` while 161
    # stays writable. Patching the whole enum map would also break Motor Type
    # and the run would refuse earlier, for a different and better reason.
    real = sa.declared_raw_value
    monkeypatch.setattr(sa, "declared_raw_value",
                        lambda v, t, pid=None: None if pid == 9 else real(v, t, pid))
    rc = _run(monkeypatch, attach(bus), write=True, persist=True)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "NOT burning" in out, out
    assert not _persists(bus), (
        "a controller that could not be fully restored was burned to flash")


def test_repair_carries_the_same_two_gates_provision_does(sim, monkeypatch, capsys):
    """One operation, one set of guards. `cmd_repair` had the generation-blind
    duplicate call and no at-rest re-check, and fixing only the copy next door
    is the two-implementations trap the code rules name."""
    import ast
    import pathlib
    src = pathlib.Path(spark_cli.__file__).read_text()
    tree = ast.parse(src)
    for name in ("cmd_repair", "cmd_provision"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        dup = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "duplicates"]
        assert dup, f"{name} does not check for a duplicated id at all"
        for call in dup:
            assert any(k.arg == "generation" for k in call.keywords), (
                f"{name} calls duplicates() without the generation, so it "
                "detects nothing on a pre-25 bus")
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "require_at_rest" for n in ast.walk(fn)), (
            f"{name} writes without re-checking at rest, and the earlier "
            "reading is two full listening windows old by then")
