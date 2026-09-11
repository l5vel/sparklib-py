"""The hardware tiers are only as real as the hook that applies them.

`tests/hardware/conftest.py` defined `pytest_collection_modifyitems` twice. The
second binding shadowed the first at module level, so `hardware`, `inject` and
`staged` were never applied to anything. `-m "not inject and not staged"` then
deselected nothing, and the selection documented as read-only included
`test_spark_persistence.py`, which writes a parameter and spends a flash cycle.

That is catalogue A4 happening to the suite that tests for A4, and it happened
three times before anyone noticed. It cost three flash cycles out
of an endurance of 1e4 to 1e5, on the lowest configured CAN id.

Nothing here needs a robot. The point is that a marker claim is checked by the
ordinary suite rather than by whoever remembers to look.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

HW = pathlib.Path(__file__).resolve().parents[1] / "hardware"
CONFTEST = HW / "conftest.py"

# Modules whose tests write to a controller. Each has to carry `inject`, and the
# flash-spending one has to gate on SPARK_HW_FLASH as well.
WRITING_MODULES = {"test_wire_injection", "test_config_injection",
                   "test_spark_persistence"}


def _defs(path, name):
    tree = ast.parse(path.read_text())
    return [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def test_the_collection_hook_is_defined_exactly_once():
    """Two definitions is not a syntax error and not a warning. Python keeps the
    last one and silently discards the first."""
    found = _defs(CONFTEST, "pytest_collection_modifyitems")
    assert len(found) == 1, (
        f"tests/hardware/conftest.py defines pytest_collection_modifyitems "
        f"{len(found)} times, at lines {[n.lineno for n in found]}. Only the "
        "last takes effect, so whatever the others did is silently gone.")


def test_the_surviving_hook_still_applies_the_tier_markers():
    """A single hook that dropped the marker pass would pass the test above."""
    src = CONFTEST.read_text()
    hook = _defs(CONFTEST, "pytest_collection_modifyitems")[0]
    body = ast.get_source_segment(src, hook) or ""
    for needed in ("_apply_tier_markers", "_skip_clean_bus_tests_under_an_injector"):
        assert needed in body, (
            f"the collection hook no longer calls {needed}, so that half of the "
            "collection pass does nothing")
    assert _defs(CONFTEST, "_apply_tier_markers"), "_apply_tier_markers is gone"


def test_every_writing_module_is_marked_inject():
    """Read the map the hook uses, not the hook's behaviour.

    A module missing from this map is a module whose writes land inside a
    selection an operator believes is read-only.
    """
    src = CONFTEST.read_text()
    tree = ast.parse(src)
    mapping = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "by_module" for t in node.targets):
            mapping = ast.literal_eval(node.value)
            break
    assert mapping, "the module-to-marker map is gone from the collection hook"

    for mod in sorted(WRITING_MODULES):
        assert mapping.get(mod) == "inject", (
            f"{mod}.py writes to a controller and the map gives it "
            f"{mapping.get(mod)!r}; it must be 'inject' or its tests join the "
            "read-only selection")


def test_the_module_map_names_only_modules_that_exist():
    """A renamed module leaves its marker behind and takes no marker forward."""
    src = CONFTEST.read_text()
    tree = ast.parse(src)
    mapping = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "by_module"
                           for t in n.targets))
    missing = sorted(m for m in mapping if not (HW / f"{m}.py").is_file())
    assert not missing, (
        f"the marker map names modules that do not exist: {missing}. Their "
        "marker is applied to nothing, and any module that replaced them is "
        "unmarked.")


@pytest.mark.parametrize("module", sorted(WRITING_MODULES))
def test_no_writing_test_reaches_a_write_without_asking_for_a_gate(module):
    """The call site, not the marker.

    A marker keeps a test out of a selection. It does not stop someone running
    the whole directory. Every test that writes has to take the `gate` fixture
    and call it, which is what actually refuses the write.
    """
    path = HW / f"{module}.py"
    tree = ast.parse(path.read_text())

    WRITES = {"write_param", "persist", "set_can_id"}

    def refused_writes(fn):
        """Calls inside `with pytest.raises(...)`, which never reach the wire.

        test_protected_parameters_are_refused_on_real_hardware calls write_param
        once per protected id, and every one is expected to raise before a frame
        is built. Counting those as writes would force a gate onto a test that
        proves writes are refused.
        """
        out = set()
        for w in ast.walk(fn):
            if not isinstance(w, ast.With):
                continue
            if not any(isinstance(i.context_expr, ast.Call)
                       and "raises" in ast.dump(i.context_expr.func)
                       for i in w.items):
                continue
            out |= {id(n) for n in ast.walk(w)}
        return out

    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
            continue
        refused = refused_writes(fn)
        writes = {n.func.attr for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in WRITES and id(n) not in refused}
        if not writes:
            continue
        takes_gate = any(a.arg == "gate" for a in fn.args.args)
        calls_gate = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id == "gate" for n in ast.walk(fn))
        skipif = any(
            isinstance(d, ast.Call)
            and "skipif" in ast.dump(d.func)
            for d in fn.decorator_list)
        if not ((takes_gate and calls_gate) or skipif):
            offenders.append(f"{fn.name} (line {fn.lineno}, calls {sorted(writes)})")

    assert not offenders, (
        f"{module}.py has tests that write to a controller without asking for a "
        f"gate or a stage: {offenders}")


def test_the_flash_spending_test_gates_on_flash_specifically():
    """`inject` buys a reversible period write. It does not buy a flash cycle."""
    path = HW / "test_spark_persistence.py"
    tree = ast.parse(path.read_text())

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
            continue
        persists = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "persist" for n in ast.walk(fn))
        if not persists:
            continue
        gated = {n.args[0].value for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "gate" and n.args
                 and isinstance(n.args[0], ast.Constant)}
        staged = any(isinstance(d, ast.Call) and "skipif" in ast.dump(d.func)
                     for d in fn.decorator_list)
        assert "flash" in gated or staged, (
            f"{fn.name} (line {fn.lineno}) calls persist() and neither gates on "
            f"'flash' nor is confined to a staged run; it gates on {sorted(gated)}. "
            "PERSIST_PARAMETERS spends one flash cycle of 1e4 to 1e5.")
