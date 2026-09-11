"""`configurator.apply()` commits CANcoder flash, and exactly one tool sends it.

A default-constructed CANcoderConfiguration carries magnet_offset 0.0, so an
apply() added to a reader wipes the device-side calibration on every run and
shifts every absolute reading by the offset it erased. The flash write lives in
one tool, which says so in its docstring and asks before it writes.

The writer is checked too: it must refresh the device's own configuration first,
so the write preserves sensor_direction and the discontinuity point instead of
sending defaults over them.
"""

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
WRITER = "cancoder_reset_flash.py"


def _sources():
    for folder in ("sparklib", "tools", "examples"):
        for path in (REPO / folder).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def _flash_writes(path):
    """Every `<something>.configurator.apply(...)` in one file, with its line."""
    for node in ast.walk(ast.parse(path.read_text(errors="ignore"))):
        if (isinstance(node, ast.Attribute) and node.attr == "apply"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "configurator"):
            yield f"{path.name}:{node.lineno}"


def test_only_one_tool_writes_cancoder_flash():
    writers = {hit.split(":")[0]
               for path in _sources() for hit in _flash_writes(path)}
    assert writers == {WRITER}, (
        "configurator.apply() commits flash and belongs in exactly one tool, "
        f"behind its confirmation. Found: {sorted(writers)}")


def test_the_scan_reaches_the_package():
    """A guard that walks an empty tree passes for the wrong reason."""
    assert sum(1 for _ in _sources()) > 30


def test_the_writer_refreshes_before_it_applies():
    """Applying a default configuration erases the fields it does not carry."""
    path = REPO / "tools" / WRITER
    tree = ast.parse(path.read_text())
    refreshes = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and n.attr == "refresh"
                 and isinstance(n.value, ast.Attribute)
                 and n.value.attr == "configurator"]
    applies = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr == "apply"
               and isinstance(n.value, ast.Attribute)
               and n.value.attr == "configurator"]
    assert refreshes, "no configurator.refresh() before the flash write"
    assert min(refreshes) < min(applies)


def test_the_writer_asks_before_it_writes():
    """A flash write with no prompt is the failure this whole file is about."""
    source = (REPO / "tools" / WRITER).read_text()
    assert "input(" in source
