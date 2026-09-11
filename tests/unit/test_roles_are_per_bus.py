"""A device id labels one bus, because ids repeat across adapters.

Hardware found this. A SPARK MAX rig carries drive and steer at ids 1 to 8 on
one adapter and four CANcoders at ids 1 to 4 on another. Every tool that built
one flat id-to-role map reported the SPARK at id 1 as a CANcoder, because the
cancoder group happened to be read last.

The snapshot picked one silently. The sweep joined both with a '+'. Neither was
right about what produced the frames it had just read.
"""

import ast
import pathlib

import pytest

from sparklib import config as cfg

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


@pytest.fixture
def two_bus_config():
    """A rig whose two adapters both carry ids 1 to 4."""
    ns = cfg._ns({
        "controller_type": "sparkmax",
        "can": {"interface": "gsusb"},
        "cancoder": {"bus": "canivore"},
        "devices": {
            "drive": {"LB": 1, "RB": 4},
            "steer": {"LB": 2, "RB": 3},
            "cancoder": {"LF": 1, "LB": 2, "RB": 3, "RF": 4},
        },
    })
    return ns


def test_the_spark_bus_shows_only_spark_roles(two_bus_config):
    roles = cfg.device_roles("gsusb", two_bus_config)
    assert roles == {1: "drive/LB", 4: "drive/RB", 2: "steer/LB", 3: "steer/RB"}
    assert not any("cancoder" in r for r in roles.values())


def test_the_cancoder_bus_shows_only_cancoder_roles(two_bus_config):
    roles = cfg.device_roles("canivore", two_bus_config)
    assert set(roles.values()) == {"cancoder/LF", "cancoder/LB",
                                   "cancoder/RB", "cancoder/RF"}


def test_asking_for_no_bus_returns_everything(two_bus_config):
    """The unscoped map still exists for a reader tied to no single wire."""
    roles = cfg.device_roles(None, two_bus_config)
    assert len(roles) == 4
    assert all("+" in r for r in roles.values())


def test_a_bus_nothing_lives_on_is_empty(two_bus_config):
    assert cfg.device_roles("can9", two_bus_config) == {}


def test_two_groups_sharing_an_id_on_one_bus_are_both_named():
    """A real collision is worth seeing, not worth resolving silently."""
    ns = cfg._ns({
        "can": {"interface": "can0"},
        "devices": {"drive": {"LF": 5}, "steer": {"LF": 5}},
    })
    assert cfg.device_roles("can0", ns)[5] in ("drive/LF+steer/LF",
                                               "steer/LF+drive/LF")


def test_a_non_numeric_id_is_skipped_rather_than_raising():
    ns = cfg._ns({"can": {"interface": "can0"},
                  "devices": {"drive": {"LF": "14a", "RF": 12}}})
    assert cfg.device_roles("can0", ns) == {12: "drive/RF"}


def test_a_config_with_no_devices_gives_no_roles():
    assert cfg.device_roles("can0", cfg._ns({"can": {"interface": "can0"}})) == {}


@pytest.mark.parametrize("tool", ["spark_passive_snapshot.py",
                                  "can_id_sweep.py",
                                  "can_frame_census.py"])
def test_no_tool_builds_its_own_flat_role_map(tool):
    """Read the call site: a tool that rebuilds the map reintroduces the bug."""
    source = (TOOLS / tool).read_text()
    tree = ast.parse(source)

    flat = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # `for group, members in vars(devices).items()` with no bus test in it.
        target = getattr(node.target, "elts", None)
        if not target or len(target) != 2:
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        # Any of these means the loop knows which wire a group sits on: the
        # shared helper, or a bus resolved per group and carried out with the id.
        bus_aware = {"group_bus", "device_roles", "_bus_names", "bus_names",
                     "spark_bus", "cancoder_bus", "bus"}
        if "vars" in names and not (bus_aware & (names | attrs)):
            flat.append(node.lineno)

    assert not flat, (
        f"{tool} builds an id-to-role map without asking which bus a group is "
        f"on, at line(s) {flat}. Use config.device_roles(channel).")
