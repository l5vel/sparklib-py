"""The suite-wide config fixture must not pin the shipped file over a host's own.

The shipped config names `can0`. An autouse fixture reloaded it before every
test, including the hardware tier, so the real CLI ran against `can0` on a rig
whose netdev is named something else and refused. Nine hardware tests failed
that way, and the failure read as a driver defect rather than as a harness one.

This reads the fixture's source, because a behavioural test of the fixture would
be running under the fixture it is testing.
"""

import ast
import pathlib

import pytest

from sparklib import config

CONFTEST = pathlib.Path(__file__).resolve().parents[1] / "conftest.py"


def _fixture_source(name):
    tree = ast.parse(CONFTEST.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"tests/conftest.py has no {name}")


def test_the_config_fixture_reads_the_host_environment_variable():
    node = _fixture_source("_shipped_config")
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    assert "ENV_VAR" in attrs, (
        "the fixture must fall back to the shipped config, not pin it: read "
        f"{config.ENV_VAR} first so a rig can name its own bus")
    assert "environ" in attrs or "getenv" in names


def test_the_fixture_still_falls_back_to_the_shipped_config():
    """Without the variable set, every test must see one known config."""
    node = _fixture_source("_shipped_config")
    attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    assert "SHIPPED_CONFIG" in attrs


def test_the_fixture_reloads_on_both_sides_of_the_test():
    """One reload leaks a config a test installed into whatever runs next."""
    node = _fixture_source("_shipped_config")
    reloads = [n for n in ast.walk(node)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "reload"]
    assert len(reloads) == 2
    yields = [n for n in ast.walk(node) if isinstance(n, ast.Yield)]
    assert len(yields) == 1
    assert reloads[0].lineno < yields[0].lineno < reloads[1].lineno


def test_the_variable_name_is_the_one_the_library_documents():
    assert config.ENV_VAR == "SPARKLIB_CONFIG"


@pytest.mark.parametrize("env,expected_shipped", [("", True), ("/somewhere.yaml", False)])
def test_the_resolution_rule_matches_what_the_fixture_encodes(env, expected_shipped):
    """The rule itself, stated once so the source check above has a meaning."""
    chosen = env.strip() or config.SHIPPED_CONFIG
    assert (chosen == config.SHIPPED_CONFIG) is expected_shipped
