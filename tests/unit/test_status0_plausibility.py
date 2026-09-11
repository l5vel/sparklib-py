"""`decode_status_0` must say when its own output is not a measurement.

A transmitter that stops driving the bus leaves it recessive, so the payload
reads all-ones and every field saturates together: 30 V, 150 A, 255 C, every
limit bit closed, and SPARK model 15, which REV has never shipped. Each of those
decodes to a number, and CD 407271 is a controller reporting 30 V until reboot
while the operator read it as a rail measurement.

The verdict is only worth having if something consults it, so the call sites are
read here as source. `spark faults` and `spark voltage` must stop printing the
numbers, `audit_problems` must raise a finding, and the drive gate must not read
a saturated frame's limit bits as four closed interlocks.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

from sparklib import admin as sa

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(REPO_ROOT, "sparklib")

ALL_ONES = bytes([0xFF] * 8)


def _src(name):
    return pathlib.Path(BASE, name).read_text()


# -- the verdict itself --------------------------------------------------------

def test_an_all_ones_frame_is_not_handed_over_as_a_reading():
    r = sa.decode_status_0(ALL_ONES)

    assert r["implausible"], (
        f"{r['voltage_v']:.2f} V {r['current_a']:.1f} A {r['motor_temp_c']} C "
        f"model {r['spark_model']} carried no verdict")
    assert r["implausible"] == ["voltage_v", "current_a", "motor_temp_c"], (
        "an all-ones payload saturates every telemetry field, and the list is "
        f"the decoded keys a caller can act on: {r['implausible']}")


def test_a_real_idle_frame_carries_no_verdict():
    """The check earns nothing if it fires on ordinary traffic. This payload was
    captured off rig-flex's bus, so it is what the check has to stay quiet on."""
    from sparksim import frames as F
    r = sa.decode_status_0(F.CAPTURED_BASE03_IDLE_STATUS_0)

    assert r["implausible"] == [], r["implausible"]
    assert 1.0 < r["voltage_v"] < 30.0 and r["spark_model"] in (1, 2)


@pytest.mark.parametrize("field,payload,needle", [
    ("voltage", bytes([0, 0, 0xFF, 0x0F, 0, 29, 0x40, 0]), "voltage_v"),
    ("current", bytes([0, 0, 0xC0, 0xF6, 0xFF, 29, 0x40, 0]), "current_a"),
    ("temperature", bytes([0, 0, 0xC0, 0x16, 0, 0xFF, 0x40, 0]), "motor_temp_c"),
])
def test_each_saturated_field_is_named_on_its_own(field, payload, needle):
    """One pegged field is enough, and each reason names the decoded key it came
    from so a caller can act on it without parsing prose."""
    why = sa.decode_status_0(payload)["implausible"]

    assert why == [needle], (field, why)


def test_a_model_this_decoder_does_not_recognise_is_not_called_implausible():
    """REV-Specs records that 1 is a SPARK Flex, not the whole set. Judging model
    identity here flagged every zero-model payload; status_problems compares it
    against base.controller_type, which is the check that has the context."""
    r = sa.decode_status_0(bytes(8))

    assert r["spark_model"] == 0
    assert r["implausible"] == [], r["implausible"]


def test_the_producer_always_sets_the_key():
    """A consumer reading `implausible` needs the producer never to omit it."""
    from sparksim import frames as F
    for payload in (ALL_ONES, F.CAPTURED_BASE03_IDLE_STATUS_0, bytes(8)):
        assert "implausible" in sa.decode_status_0(payload), payload.hex()


def test_the_reference_decoder_still_agrees_on_every_shared_field():
    """sparksim keeps an independent decoder; divergence would make it a trap."""
    from sparksim import frames as F

    for payload in (F.CAPTURED_BASE03_IDLE_STATUS_0, bytes(8),
                    F.encode_status_0(applied=0.25, amps=42.0, volts=12.4,
                                      temp_c=31)):
        mine = sa.decode_status_0(payload)
        theirs = F.decode_status_0(payload)
        for key, value in theirs.items():
            assert mine[key] == pytest.approx(value), (key, payload.hex())


# -- the call sites, read as source --------------------------------------------

def _reads_implausible(tree, func_name):
    """True when `func_name` subscripts or.get()s 'implausible' anywhere."""
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name != func_name:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and node.value == "implausible":
                return True
    return False


@pytest.mark.parametrize("module,func", [
    ("cli.py", "cmd_voltage"),
])
def test_the_verdict_is_consulted_where_status_0_is_used(module, func):
    tree = ast.parse(_src(module))

    assert _reads_implausible(tree, func), (
        f"{module}::{func} reads STATUS_0 and never asks whether it is a "
        "measurement")


def test_cmd_voltage_keeps_a_saturated_rail_out_of_the_pack_summary():
    """30 V from a dead transmitter must not become a state-of-charge input."""
    tree = ast.parse(_src("cli.py"))
    fn = next(f for f in ast.walk(tree)
              if isinstance(f, ast.FunctionDef) and f.name == "cmd_voltage")
    volts = next(n for n in ast.walk(fn) if isinstance(n, ast.DictComp))

    assert "implausible" in ast.unparse(volts), ast.unparse(volts)


# -- the two call sites worth exercising rather than reading -------------------

def _saturated_status(dev=10):
    return {dev: {"status0": sa.decode_status_0(ALL_ONES),
                  "status1": {"faults": [], "warnings": [], "sticky_faults": [],
                              "sticky_warnings": [], "is_follower": False}}}


def test_the_audit_raises_a_finding_for_a_frame_that_is_not_a_measurement():
    problems = sa.status_problems(_saturated_status(), {10: "steer/RB"})

    assert any("not a measurement" in p for p in problems), problems


def test_the_audit_does_not_also_report_the_saturated_frames_limit_bits():
    """All four limit bits are set in an all-ones frame and none is an interlock."""
    problems = sa.status_problems(_saturated_status(), {10: "steer/RB"})

    assert not any("limit closed" in p or "heartbeat source" in p
                   for p in problems), problems


def test_cmd_faults_prints_no_numbers_for_a_frame_that_is_not_a_measurement(
        capsys, monkeypatch):
    from types import SimpleNamespace
    from sparklib import cli as spark_cli

    class _NullAdmin:
        bus = None
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(spark_cli, "_open", lambda: _NullAdmin())
    monkeypatch.setattr(spark_cli, "collect_status",
                        lambda bus, w, **kw: _saturated_status())

    rc = spark_cli.cmd_faults(SimpleNamespace(window=0.1))
    out = capsys.readouterr().out

    assert rc != 0, "a controller that stopped driving the bus must not exit 0"
    assert "UNREADABLE" in out, out
    assert "149.9" not in out and "30.00" not in out and "255" not in out, out


# -- every finding names its fix -----------------------------------------------

def test_every_audit_finding_carries_a_remedy():
    """A message saying what is wrong without saying what to do costs hours; the
    whole of was spent learning that. Read as source so a finding added
    without a FIX fails here rather than at 2am."""
    tree = ast.parse(_src("admin.py"))
    bare = [ast.unparse(n)[:90] for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and getattr(n.func.value, "id", "") == "problems"
            and "FIX" not in ast.unparse(n)]

    assert bare == [], bare


def test_every_decoded_bit_has_a_remedy_and_no_remedy_is_orphaned():
    """BIT_REMEDIES is keyed by the names decode_status_1 emits, so a new bit
    added to the decoder without a remedy is a finding an operator cannot act on."""
    bits = set(sa._FAULT_BITS) | set(sa._WARNING_BITS)

    assert bits - set(sa.BIT_REMEDIES) == set(), "decoded bits with no remedy"
    assert set(sa.BIT_REMEDIES) - bits == set(), "remedies for a bit nothing emits"
