"""A silent encoder must not be reported as a magnet mounted wrong.

Hardware found this. Four CANcoders on firmware too old for the installed
phoenix6 answered nothing. Every raw reading came back 0.000, warmup accepted
that because a constant is the stillest reading there is, and magnet health,
the one field with no plausible default, read INVALID. The audit then told the
operator to check the magnet on four modules when the fix was a firmware
upgrade.
"""

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
AUDIT = REPO / "tools" / "cancoder_audit.py"


def _main():
    tree = ast.parse(AUDIT.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("cancoder_audit has no main")


def test_presence_is_checked_before_any_angle_is_read():
    """Read the call site: the gate has to come before the reads it protects."""
    main = _main()
    present = [n.lineno for n in ast.walk(main)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "present"]
    warmup = [n.lineno for n in ast.walk(main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "warmup"]
    assert present, "cancoder_audit never asks whether an id is answering"
    assert warmup, "expected a warmup call to guard"
    assert min(present) < min(warmup), (
        "presence must be established before warmup, which settles happily on "
        "the constant 0.000 a silent encoder returns")


def test_a_silent_encoder_stops_the_run():
    """It must refuse, not carry on and report defaults as measurements."""
    main = _main()
    source = ast.get_source_segment(AUDIT.read_text(), main)
    assert "silent" in source
    assert "return 2" in source


def test_an_invalid_reading_is_not_called_a_mounting_problem():
    source = AUDIT.read_text()
    i = source.index("INVALID")
    window = source[i:i + 500]
    assert "no reading" in window, (
        "MAGNET_INVALID means the encoder produced no reading; saying the "
        "magnet is too far or off-centre is a confident wrong diagnosis")


def test_the_mounting_advice_survives_for_a_real_verdict():
    """A genuine non-green reading still has to say what to check."""
    source = AUDIT.read_text()
    assert "too far, too close or off-centre" in source


@pytest.mark.parametrize("reading,mounting", [
    ("MagnetHealthValue.MAGNET_GREEN", False),
    ("MagnetHealthValue.MAGNET_ORANGE", True),
    ("MagnetHealthValue.MAGNET_RED", True),
    ("MagnetHealthValue.MAGNET_INVALID", False),
    ("-", False),
])
def test_only_a_real_colour_accuses_the_mounting(reading, mounting):
    """The rule the tool encodes, stated once so the source check has meaning."""
    upper = reading.upper()
    invalid = "INVALID" in upper or "UNKNOWN" in upper
    accuses = (not invalid) and "MAGNET" in upper and "GREEN" not in upper
    assert accuses is mounting
