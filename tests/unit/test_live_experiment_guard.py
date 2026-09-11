"""A robot carrying an experiment must refuse the commands that end it.

rig-flex-2 holds the brownout and config-loss experiment. Five controllers are
drifted and are the control, three hold the provisioned 20 ms across a power
cycle and are the subjects. One `spark repair --persist` ends both halves, and
the result is not recoverable by re-running anything.

The hardware test suite has refused to run on rig-flex-2 since the table was
written. The CLI did not, so the single command most likely to destroy the
experiment was the one an operator would reach for from a shell.

Nothing here touches hardware. The guard is checked by reading the code, because
a behavioural test would have to be on rig-flex-2 to observe it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PKG = pathlib.Path(__file__).resolve().parents[2] / "sparklib"
CLI = PKG / "cli.py"
GUARDS = (pathlib.Path(__file__).resolve().parents[1]
          / "support" / "sparkhw" / "guards.py")

# Commands that write to a controller. `clear` is deliberately absent: it erases
# sticky bits rather than configuration, and refusing it on rig-flex-2 would block
# reading the experiment out.
WRITING_COMMANDS = {"cmd_set_id", "cmd_persist", "cmd_repair", "cmd_provision"}


def _table(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "LIVE_EXPERIMENTS"
                for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def test_the_package_carries_the_table_not_only_the_test_suite():
    """A guard that lives in tests/ protects the tests and nothing else."""
    table = _table(CLI)
    assert table, (
        "sparklib/cli.py declares no LIVE_EXPERIMENTS table, "
        "so the CLI writes to an experiment robot without refusing")
    assert "rig-flex-2" in table, "rig-flex-2 is not listed as a live experiment"


def test_the_two_tables_say_the_same_thing():
    """Two copies drift. The test suite would refuse a robot the CLI writes to."""
    cli, suite = _table(CLI), _table(GUARDS)
    assert cli == suite, (
        "the CLI and the hardware suite disagree about which robots carry a live "
        f"experiment: CLI {sorted(cli or {})}, suite {sorted(suite or {})}")


@pytest.mark.parametrize("command", sorted(WRITING_COMMANDS))
def test_every_writing_command_checks_the_guard_before_it_opens_the_bus(command):
    """The call site, not the helper.

    A test of `_refuse_on_a_live_experiment` alone cannot see a command that
    never calls it. This reads each command body and requires the check to come
    before the `with _open()` that starts talking to controllers.
    """
    tree = ast.parse(CLI.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == command), None)
    assert fn, f"{command} no longer exists in cli.py"

    checks = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_refuse_on_a_live_experiment"]
    assert checks, (
        f"{command} never calls _refuse_on_a_live_experiment, so it writes to "
        "rig-flex-2 from a shell with no warning")

    opens = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_open"]
    if opens:
        assert min(checks) < min(opens), (
            f"{command} opens the bus at line {min(opens)} before checking the "
            f"guard at line {min(checks)}")


def test_the_guard_refuses_rig_flex_2_and_allows_this_robot(monkeypatch, capsys):
    """Both directions. A guard that always refuses would pass a one-sided test."""
    from sparklib import cli as spark_cli

    monkeypatch.setattr(spark_cli, "_base_index", lambda: "rig-flex-2")
    assert spark_cli._refuse_on_a_live_experiment("repair") is True
    out = capsys.readouterr().out
    assert "REFUSED" in out and "rig-flex-2" in out
    assert "brownout" in out, "the refusal must say why, not just that it refused"

    monkeypatch.setattr(spark_cli, "_base_index", lambda: "rig-flex")
    assert spark_cli._refuse_on_a_live_experiment("repair") is False
    assert capsys.readouterr().out == "", "a robot with no experiment printed a refusal"


def test_the_refusal_returns_a_non_zero_exit_code():
    """A script that runs `spark repair` in a loop reads the exit code, not stdout."""
    src = CLI.read_text()
    tree = ast.parse(src)
    for command in sorted(WRITING_COMMANDS):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == command)
        guard_if = next(
            (n for n in fn.body
             if isinstance(n, ast.If) and isinstance(n.test, ast.Call)
             and getattr(n.test.func, "id", None) == "_refuse_on_a_live_experiment"),
            None)
        assert guard_if, f"{command}'s guard is not a top-level if in its body"
        returns = [s for s in guard_if.body if isinstance(s, ast.Return)]
        assert returns, f"{command} refuses and then carries on"
        val = returns[0].value
        assert isinstance(val, ast.Constant) and val.value not in (0, None), (
            f"{command} returns {ast.unparse(val)} after refusing, which reads "
            "as success to any caller checking the exit code")
