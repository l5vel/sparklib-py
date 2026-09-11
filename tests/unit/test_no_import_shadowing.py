"""No function may bind a name that its module imported.

sparklib/can_bus.py shipped `controller = controller.Controller(...)` inside
init_controller, after the module it came from was renamed. Python decides
local-versus-global from the whole function body, so the assignment made
`controller` local on the line that read it, and every init_controller call
raised UnboundLocalError. Both suites stayed green, because the failure is a
compile-time scoping property that no mock reaches.

Names that shadow an import are checked with symtable, which is the same
analysis the interpreter does, so this test catches the defect at its cause.
"""

import ast
import os
import symtable

import pytest

PACKAGE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "sparklib")

# Names a function may rebind even though the module imports them. Each entry
# is a deliberate parameter name that never reads the module inside its body.
ALLOWED = {
    ("can_bus.py", "apply_boot_config", "controller"),
    ("can_bus.py", "reconfigure_controllers", "controller"),
    ("can_bus.py", "shutdown", "controller"),
}


def _module_files():
    return sorted(f for f in os.listdir(PACKAGE) if f.endswith(".py"))


def _imported_names(source):
    """Names bound by an import at module scope, which is where the trap is.

    An import inside a function binds in that same scope, so it cannot produce
    the unbound read this test looks for.
    """
    names = set()
    stack = list(ast.parse(source).body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            stack.extend(node.body + getattr(node, "orelse", [])
                         + getattr(node, "finalbody", []))
    return names


def _functions(table, prefix=""):
    for child in table.get_children():
        name = f"{prefix}{child.get_name()}"
        if child.get_type() == "function":
            yield name, child
        yield from _functions(child, "" if child.get_type() == "class" else name + ".")


@pytest.mark.parametrize("filename", _module_files())
def test_no_function_rebinds_an_imported_name(filename):
    path = os.path.join(PACKAGE, filename)
    with open(path) as fh:
        source = fh.read()
    imported = _imported_names(source)
    table = symtable.symtable(source, filename, "exec")

    offenders = []
    for func_name, func in _functions(table):
        for symbol in func.get_symbols():
            if symbol.get_name() not in imported or not symbol.is_assigned():
                continue
            if (filename, func_name, symbol.get_name()) in ALLOWED:
                continue
            offenders.append(f"{filename}:{func_name} rebinds {symbol.get_name()!r}")

    assert not offenders, (
        "these functions make an imported module name local, so any read of it "
        "in the same body raises UnboundLocalError:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("entry", sorted(ALLOWED))
def test_every_allowed_shadow_still_exists(entry):
    """An exemption that outlives its function hides the next real defect."""
    filename, func_name, name = entry
    with open(os.path.join(PACKAGE, filename)) as fh:
        table = symtable.symtable(fh.read(), filename, "exec")
    found = {fn for fn, _ in _functions(table)}
    assert func_name in found, (
        f"{filename} has no {func_name}, so the exemption for {name!r} is stale")
