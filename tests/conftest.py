"""Shared setup for the sparklib suite.

    tests/unit/         pure functions and source-level guards. No bus, safe anywhere.
    tests/adversarial/  every failure mode in docs/FAILURE-CATALOGUE.md, injected
                        into a simulated bus. No hardware.
    tests/hardware/     needs a real SPARK on a real CAN bus. Opt in with --hardware.
    tests/support/      the simulator and the live-bus guards.

Diagnostics that touch real hardware live in tools/, not here -- see tools/README.md.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "support")


def pytest_addoption(parser):
    parser.addoption("--hardware", action="store_true", default=False,
                     help="run tests marked `hardware` (needs a real SPARK on a "
                          "real CAN bus on this machine)")


def pytest_collection_modifyitems(config, items):
    """Skip `hardware` tests unless asked for.

    Anything that reaches for the CAN bus has to be opted into explicitly rather
    than being one `pytest` away.
    """
    if config.getoption("--hardware"):
        return
    skip = pytest.mark.skip(reason="needs a real SPARK bus; pass --hardware to run")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


# Importable from a bare checkout, in this process and in any child.
for p in (REPO_ROOT, SUPPORT):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["PYTHONPATH"] = os.pathsep.join(
    [REPO_ROOT, SUPPORT]
    + [q for q in os.environ.get("PYTHONPATH", "").split(os.pathsep) if q]
)

# The simulator's fixtures: clock, sim, rig_flex, rig_max.
pytest_plugins = ["sparksim.harness"]


@pytest.fixture(autouse=True)
def _shipped_config():
    """Every test reads one config, reloaded either side so none leaks forward.

    config.get() caches, and a test that installs its own must not leak it into
    the next one.

    SPARKLIB_CONFIG wins when it is set, because the hardware tier runs the real
    CLI against a real bus and the shipped config names `can0`. Pinning the
    shipped file here made every CLI-level hardware test refuse on any rig whose
    netdev is named anything else, which is most of them.
    """
    import os

    from sparklib import config

    path = os.environ.get(config.ENV_VAR, "").strip() or config.SHIPPED_CONFIG
    config.reload(path)
    yield
    config.reload(path)
