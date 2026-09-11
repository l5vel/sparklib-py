"""The baseline's parameter table: what a good bus was holding, not what it must.

`sparkflex_motor_defaults.yaml` says what a motor SHOULD hold and the audit
treats a disagreement there as a fault. The baseline answers a different
question over a different set of ids: what the fleet WAS holding when someone
last called it good. Those two must partition the id space, or a deliberate
config edit gets reported twice, once as drift and once as baseline divergence.

The undeclared half cannot produce a fault, because nothing declares it. A
disagreement there is a note. Parameter 153 on rig-flex is the live case: seven
controllers hold 7 and one holds 17, which is REV's own documented default, so
the majority is not an authority and the tooling must not pick a side.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparksim import attach, build_fleet

ROLES = {10: "drive/RB", 11: "steer/RB", 12: "drive/RF", 13: "steer/RF",
         14: "drive/LF", 15: "steer/LF", 16: "drive/LB", 17: "steer/LB"}
UNDECLARED = 153        # Duty Cycle Sensor Prescaler, named by no defaults file


class _Keep:
    def __init__(self, adm):
        self.adm = adm

    def __enter__(self):
        return self.adm

    def __exit__(self, *exc):
        return False


def _fleet(**per_dev):
    """rig-flex's eight, with named ids seeded into every controller's RAM."""
    fleet = build_fleet(ids=sorted(ROLES))
    for c in fleet:
        for pid, val in per_dev.items():
            c.ram[int(pid)] = c.flash[int(pid)] = val
    return fleet


def _table(bus, **kw):
    return sa.fleet_param_table(attach(bus), sorted(ROLES), role_of=ROLES.get,
                                wait=0.2, **kw)


# -- the two files partition the id space -------------------------------------

def test_a_declared_id_never_enters_the_baseline_table(sim):
    """Otherwise one config edit is a finding AND a divergence, and an operator
    fixing the first is told the second is still wrong."""
    table = _table(sim(_fleet()))
    declared = set(sa.motor_settings("drive")) | set(sa.motor_settings("steer"))
    overlap = declared & (set(table["common"]) | set(table["divergent"]))
    assert not overlap, (
        f"parameters {sorted(overlap)} are declared and were also captured into "
        "the baseline, so a drift on them would be reported twice")


def test_the_can_id_is_never_captured(sim):
    """Parameter 0 is each controller's own address and differs by definition."""
    table = _table(sim(_fleet()))
    assert sa.PARAM_CAN_ID not in table["common"]
    assert sa.PARAM_CAN_ID not in table["divergent"]


def test_an_undeclared_id_the_fleet_agrees_on_is_captured(sim):
    table = _table(sim(_fleet(**{str(UNDECLARED): 7})))
    assert table["common"].get(UNDECLARED) == 7


def test_an_undeclared_id_the_fleet_disagrees_on_is_divergent(sim):
    fleet = _fleet(**{str(UNDECLARED): 7})
    for c in fleet:
        if c.dev == 12:
            c.ram[UNDECLARED] = c.flash[UNDECLARED] = 17
    table = _table(sim(fleet))
    assert UNDECLARED not in table["common"], (
        "a parameter the fleet disagrees on was recorded as a fleet value")
    assert table["divergent"][UNDECLARED][12] == 17


# -- snapshot refuses rather than picking a side ------------------------------

def _snapshot(monkeypatch, bus, **kw):
    monkeypatch.setattr(spark_cli, "_open", lambda: _Keep(attach(bus)))
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: ROLES)
    args = SimpleNamespace(window=1.0, write=False, parameters=True, wait=0.2)
    for k, v in kw.items():
        setattr(args, k, v)
    return spark_cli.cmd_snapshot(args)


def test_snapshot_refuses_while_an_undeclared_parameter_diverges(
        sim, monkeypatch, capsys):
    """The rig-flex case. 17 is REV's documented default for 153 and seven
    controllers hold 7, so the seven may be the deliberate ones. Recording the
    majority would write a number nobody chose into the reference file."""
    fleet = _fleet(**{str(UNDECLARED): 7})
    for c in fleet:
        if c.dev == 12:
            c.ram[UNDECLARED] = c.flash[UNDECLARED] = 17
    rc = _snapshot(monkeypatch, sim(fleet))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert f"  {UNDECLARED}" in out and "12:17" in out, out
    assert "majority is not an authority" in out


def test_snapshot_records_the_table_when_the_fleet_agrees(sim, monkeypatch, capsys):
    """PARSED, not string-matched. The first version of this test checked that
    a substring appeared in the rendered text, which a file that does not load
    as YAML passes just as well. It did: every unnamed id past 198 rendered as
    `202: 0 = 0`, with the float decoration appended and no comment marker, and
    the first real `spark audit` against it died on int('0 = 0')."""
    import yaml
    rc = _snapshot(monkeypatch, sim(_fleet(**{str(UNDECLARED): 7})))
    out = capsys.readouterr().out
    assert rc == 0, out
    body = out.split("dry run")[0]
    doc = yaml.safe_load(body)
    assert doc is not None, f"the baseline does not parse as YAML:\n{body}"
    common = doc["parameters"]["common"]
    assert common[UNDECLARED] == 7, common
    assert doc["parameters"]["captured_ids"] == len(common)
    for pid, raw in common.items():
        assert isinstance(pid, int), f"key {pid!r} is not an int"
        assert isinstance(raw, int) and not isinstance(raw, bool), (
            f"parameter {pid} loaded as {raw!r}, which is what a comment marker "
            "left off a decorated line looks like")


def test_every_generated_row_survives_a_yaml_round_trip(sim, monkeypatch, capsys):
    """The float rows are the ones that broke: an id with no name in the
    parameter index gets a `= value` decoration and nothing to attach it to."""
    import yaml
    fleet = _fleet(**{str(UNDECLARED): 7})
    for c in fleet:
        # A float in an id past the index (which stops at 198), so the row has
        # a decoration and no name to hang it on.
        c.ram[202] = c.flash[202] = sa.float_bits(1.5)
        c.param_types[202] = 3        # Float, as a real controller reports
    rc = _snapshot(monkeypatch, sim(fleet))
    out = capsys.readouterr().out
    assert rc == 0, out
    doc = yaml.safe_load(out.split("dry run")[0])
    assert doc["parameters"]["common"][202] == sa.float_bits(1.5), (
        "an unnamed float row did not survive the round trip")


def test_a_baseline_that_does_not_parse_is_reported_and_not_raised(sim):
    """A broken reference file must degrade to a note naming the file, never
    take the audit down with a ValueError mid-run."""
    bus = sim(_fleet(**{str(UNDECLARED): 7}))
    baseline = {"parameters": {"common": {202: "0 = 0", UNDECLARED: 7}}}
    notes = sa.baseline_param_problems(attach(bus), ROLES, baseline, wait=0.2)
    assert len(notes) == 1 and "not whole numbers" in notes[0], notes
    assert "snapshot --write" in notes[0]


def test_no_parameters_captures_identity_without_the_table(sim, monkeypatch, capsys):
    """The escape hatch, so a diverging undeclared id cannot block a re-baseline
    of the identity and cadence a swap check depends on."""
    fleet = _fleet(**{str(UNDECLARED): 7})
    for c in fleet:
        if c.dev == 12:
            c.ram[UNDECLARED] = 17
    rc = _snapshot(monkeypatch, sim(fleet), parameters=False)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "parameters:" not in out
    assert "controllers:" in out and "status_1_period_ms" in out


# -- the audit reads it as a note ---------------------------------------------

def test_a_baseline_divergence_is_a_note_and_not_a_finding(sim):
    """Nothing declares these ids, so the baseline says what a good bus held and
    not what this one must hold. A note that changed the exit code would make a
    stale baseline block a robot."""
    fleet = _fleet(**{str(UNDECLARED): 17})
    bus = sim(fleet)
    baseline = {"parameters": {"common": {UNDECLARED: 7}}}
    notes = sa.baseline_param_problems(attach(bus), ROLES, baseline, wait=0.2)
    assert len(notes) == len(ROLES), notes
    for n in notes:
        assert "note and not a fault" in n


def test_a_matching_fleet_produces_no_note(sim):
    bus = sim(_fleet(**{str(UNDECLARED): 7}))
    baseline = {"parameters": {"common": {UNDECLARED: 7}}}
    assert sa.baseline_param_problems(attach(bus), ROLES, baseline, wait=0.2) == []


def test_a_baseline_with_no_parameter_section_reads_clean(sim):
    """Every baseline captured before this feature existed has no such section,
    and loading one must not crash or invent findings."""
    bus = sim(_fleet())
    for baseline in (None, {}, {"controllers": {}}, {"parameters": {}}):
        assert sa.baseline_param_problems(attach(bus), ROLES, baseline,
                                          wait=0.2) == []


# -- what the adversarial review caught ---------------------------------------

def test_a_controller_that_missed_ids_refuses_rather_than_shortening_the_table(
        sim, monkeypatch, capsys):
    """`seen` is the union over devices, so an id one controller failed to answer
    lands in `divergent`. An id NO controller answered is simply absent, and a
    shorter table reads like a smaller fleet configuration instead of a lost
    read. One apiClass 13 timeout costs a whole 16-id block."""
    bus = sim(_fleet(**{str(UNDECLARED): 7}))
    adm = attach(bus)
    real = adm.read_param_value

    def flaky(dev, pid, wait=0.5):
        return None if (dev == 12 and pid == UNDECLARED) else real(dev, pid, wait)

    monkeypatch.setattr(adm, "read_param_value", flaky)
    table = sa.fleet_param_table(adm, sorted(ROLES), role_of=ROLES.get, wait=0.2)
    assert table["short"].get(12) == [UNDECLARED], table["short"]
    assert UNDECLARED not in table["common"], (
        "an id one controller never answered was recorded as a fleet value")


def test_the_incomplete_bus_refusal_runs_before_the_sweep(sim, monkeypatch, capsys):
    """The refusal and the sweep used to share a duplicated predicate, so a
    46-frame sweep ran against a bus the next line was about to refuse."""
    fleet = [c for c in _fleet() if c.dev != 12]
    bus = sim(fleet)
    before = len(bus.sent)
    rc = _snapshot(monkeypatch, bus)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "incomplete bus" in out, out
    reads = [m for m in bus.sent[before:]
             if sa.READ_PARAM_BASE <= (m.arbitration_id & ~0x3F) <= sa.READ_PARAM_LAST]
    assert not reads, (
        f"{len(reads)} parameter read frame(s) went out before the bus was "
        "refused as incomplete")


def test_a_healthy_fixture_holds_what_the_baseline_records(sim):
    """A healthy bus has to mean the same thing in the simulator and on the
    robot. The audit compares two files now: the declared defaults for the ids
    they name, and the baseline for the ids they do not. Seeding only the first
    made every healthy simulated audit emit 138 baseline notes."""
    from sparksim.fleet import _baseline_param_table
    recorded = _baseline_param_table()
    assert recorded, (
        "the checked-in rig-flex baseline carries no parameter table, so this "
        "fixture cannot be faithful to it; re-capture with `spark snapshot`")
    bus = sim(build_fleet(ids=sorted(ROLES)))
    notes = sa.baseline_param_problems(attach(bus), ROLES,
                                       {"parameters": {"common": recorded}},
                                       wait=0.2)
    assert notes == [], (
        f"a healthy simulated fleet disagrees with the baseline on "
        f"{len(notes)} id(s): {notes[:3]}")


def test_the_simulator_reports_the_type_a_declared_setting_says_it_is(sim):
    """Every id used to report Uint, so both branches that key on a parameter's
    type were unreachable in this suite. That is how a baseline row rendered
    `202: 0 = 0` and reached hardware before anything noticed."""
    bus = sim(build_fleet(ids=sorted(ROLES)))
    adm = attach(bus)
    # Each types frame carries sixteen ids, so read the block each one sits in.
    first = adm.param_types(12, start_id=0)
    assert first[13] == "Float", (
        f"parameter 13 is declared FLOAT and the simulator reports {first[13]}")
    assert first[2] == "Uint", first[2]
    assert adm.param_types(12, start_id=32)[45] == "Boolean"
