"""The enum ordinals, pinned to the vendored spec rather than to a memory.

Four declared settings are written by NAME and read back as a number: Motor Type
is BRUSHLESS, Idle Mode is BRAKE, Closed Loop Control Sensor is MAIN_ENCODER and
Voltage Compensation Mode is NO_VOLTAGE_COMP. Until the audit could resolve those
names it compared "BRAKE" against 1 and reported drift on a correctly provisioned
controller, on both generations.

The ordinal is the member's POSITION in REV-SparkParameters-v0.1.2 section 1, and
that file is vendored under tests/support/spec. These tests parse it, so a
hand-typed constant cannot quietly disagree with the source it cites.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from sparklib import admin as sa

SPEC = (pathlib.Path(__file__).resolve().parents[2]
        / "reference" / "REV-SparkParameters-v0.1.2.md")


def _spec_enums():
    """{enum name: (member, ...)} from the vendored parameter spec."""
    out, current = {}, None
    for line in SPEC.read_text().splitlines():
        if line.startswith("## Below is a list of all the configurable"):
            break
        head = re.fullmatch(r"([A-Za-z]+):", line.strip())
        if head:
            current = head.group(1)
            out[current] = []
            continue
        member = re.fullmatch(r"\* ([A-Z0-9_]+)", line.strip())
        if member and current:
            out[current].append(member.group(1))
    return {k: tuple(v) for k, v in out.items() if v}


def _spec_param_enums():
    """{param id: enum name} from the spec's parameter table.

    The DEFAULT column is the authority: it reads `Sensor.NONE` for parameter 9,
    whose description never names an enum at all. The description is a fallback
    for any row whose default is a bare number.
    """
    known = set(_spec_enums())
    out = {}
    for line in SPEC.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 7 or not cells[2].isdigit():
            continue
        m = re.fullmatch(r"([A-Za-z]+)\.[A-Z0-9_]+", cells[5])
        if m and m.group(1) in known:
            out[int(cells[2])] = m.group(1)
            continue
        m = re.search(r"Represented by the ([A-Za-z]+) enum", cells[-2])
        if m and m.group(1) in known:
            out[int(cells[2])] = m.group(1)
    return out


def test_the_vendored_spec_is_present_and_parses():
    """A silent parse failure would make every test below vacuous."""
    assert SPEC.exists(), f"{SPEC} is missing; the enum ordinals have no source"
    enums = _spec_enums()
    assert enums.get("IdleMode") == ("COAST", "BRAKE"), enums
    assert len(enums) >= 8, f"only {len(enums)} enums parsed out of the spec"


@pytest.mark.parametrize("name", sorted(sa.PARAM_ENUM_MEMBERS))
def test_every_enum_matches_the_spec_member_for_member(name):
    """Order is the whole content: the ordinal IS the position."""
    spec = _spec_enums()
    assert name in spec, f"{name} is not an enum in REV-SparkParameters-v0.1.2"
    assert sa.PARAM_ENUM_MEMBERS[name] == spec[name], (
        f"{name} disagrees with the spec.\n"
        f"  spark_admin: {sa.PARAM_ENUM_MEMBERS[name]}\n"
        f"  spec:        {spec[name]}")


def test_every_parameter_mapped_to_an_enum_is_mapped_the_way_the_spec_says():
    for pid, enum_name in sa.PARAM_ENUMS.items():
        spec = _spec_param_enums()
        assert pid in spec, (
            f"parameter {pid} is mapped to {enum_name} here and the spec's table "
            "does not say it carries an enum at all")
        assert spec[pid] == enum_name, (
            f"parameter {pid} is {spec[pid]} in the spec, not {enum_name}")


def test_the_declared_flex_config_resolves_every_enum_name_it_uses():
    """The failure this closes: a declared name the audit cannot turn into a
    number compares unequal to whatever the controller holds, so a correctly
    provisioned motor is reported as drifted."""
    for product in ("sparkflex", "sparkmax"):
        defaults = sa.load_motor_defaults(controller_type=product)
        for role in ("steer", "drive"):
            for pid, st in sa.motor_settings(role, defaults).items():
                value = st.get("value")
                if not isinstance(value, str):
                    continue
                assert sa.param_enum_value(pid, value) is not None, (
                    f"{product} declares {st['name']} (parameter {pid}) as "
                    f"{value!r} and nothing here can turn that into the number "
                    "the controller stores")


@pytest.mark.parametrize("declared, actual, param_id, want", [
    ("BRAKE", 1, 6, True),
    ("BRAKE", 0, 6, False),
    ("MAIN_ENCODER", 1, 9, True),
    ("NO_VOLTAGE_COMP", 0, 74, True),
    ("BRUSHLESS", 1, 2, True),
    ("brake", 1, 6, True),
])
def test_declared_matches_resolves_an_enum_name(declared, actual, param_id, want):
    assert sa._declared_matches(declared, actual, param_id) is want


def test_an_enum_name_without_a_parameter_id_fails_closed():
    """No id means no way to resolve the name, and a comparison that cannot be
    made must read as a mismatch rather than as agreement."""
    assert sa._declared_matches("BRAKE", 1) is False
    assert sa._declared_matches("BRAKE", 1, 999) is False


def test_an_unknown_member_name_fails_closed():
    assert sa.param_enum_value(6, "SLIDE") is None
    assert sa._declared_matches("SLIDE", 0, 6) is False


@pytest.mark.parametrize("product", ["sparkflex", "sparkmax"])
def test_the_declared_file_meta_counts_its_own_entries(product):
    """`spark defaults` prints these, and both were stale on the Flex file: it
    said 44 settings and 9 deviating where the file carried 46 and 10. A number
    that has to be maintained by hand beside the thing it counts goes wrong."""
    defaults = sa.load_motor_defaults(controller_type=product)
    entries = list(defaults.get("common") or []) + list(defaults.get("by_role") or [])
    meta = defaults.get("meta") or {}
    assert meta.get("settings") == len(entries), (
        f"{product} meta.settings says {meta.get('settings')} and the file "
        f"carries {len(entries)} entries")
    deviating = sum(1 for e in entries if e.get("deviates"))
    assert meta.get("deviating_from_factory_default") == deviating, (
        f"{product} meta.deviating_from_factory_default says "
        f"{meta.get('deviating_from_factory_default')} and the file marks "
        f"{deviating}")


@pytest.mark.parametrize("product", ["sparkflex", "sparkmax"])
def test_a_setting_marked_deviating_actually_differs_from_the_factory_default(product):
    """A row whose declared value equals REV's default can never produce a
    finding, so marking it deviating costs a read every audit and reports
    nothing. Parameter 194 is the live case: declared 0, rev_default false, and
    REV's own table gives that UINT32 a boolean default."""
    defaults = sa.load_motor_defaults(controller_type=product)
    entries = list(defaults.get("common") or []) + list(defaults.get("by_role") or [])
    same = []
    for e in entries:
        if not e.get("deviates") or "values" in e:
            continue
        want = sa.declared_raw_value(e.get("value"), e.get("type"), e["param_id"])
        got = sa.declared_raw_value(e.get("rev_default"), e.get("type"), e["param_id"])
        if want is not None and want == got:
            same.append((e["param_id"], e["name"], e.get("value"),
                         e.get("rev_default")))
    assert not same, (
        f"{product} marks these deviating and they equal the factory default, "
        f"so they can never be reported: {same}")
