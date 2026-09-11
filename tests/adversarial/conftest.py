"""Fixtures and frame builders for the adversarial suite.

The `clock`, `sim` and `rig-flex` fixtures come from sparksim.harness, registered
once in tests/conftest.py; defining them again here would be a second copy of the
wiring, and a test could then exercise the copy the rest of the suite does not
use. What this file adds is the shorthand every adversarial module wants: an
`admin` factory that attaches the real SparkAdmin to a bus, and builders for
frames a test wants to hand to the driver directly.
"""
from __future__ import annotations

import os
import pathlib
import sys
from types import SimpleNamespace

import pytest

_SUPPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)

from sparksim import frames as F  # noqa: E402
from sparksim.bus import SimMessage  # noqa: E402
from sparksim.fleet import (ROLES_MAX, ROLES_FLEX, SERIALS_MAX,  # noqa: E402
                            SERIALS_FLEX, build_fleet, spark)
from sparksim.harness import attach  # noqa: E402

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID


# -- driver wiring -------------------------------------------------------------

@pytest.fixture
def admin():
    """Factory: admin(bus) -> a real SparkAdmin bound to that bus."""
    return attach


@pytest.fixture
def healthy(rig_flex):
    """(bus, adm) for the provisioned eight -- the control every failure is read
    against."""
    return rig_flex, attach(rig_flex)


def _pl_max_baseline():
    """The MAX rig's recorded baseline, which the shipped one is not."""
    import pathlib as _pl
    return _pl.Path(__file__).resolve().parents[1] / "support" / "baselines" / "rig-max-baseline.yaml"


def _shipped_baseline():
    """The reference baseline that ships beside the example config."""
    from sparklib import config as spark_config
    import pathlib as _pl
    return _pl.Path(spark_config.SHIPPED_CONFIG).with_name("spark-baseline.yaml")


def _install(ns):
    """Install a simulated rig the way a host application would.

    Uses config.set_config rather than patching `_base`, so these tiers exercise
    the same path an embedding application takes, and so the guard that refuses
    to write from the packaged example sees a config someone chose. The declared
    per-product tables still resolve, because load_motor_defaults falls back to
    the packaged copy when the installed config carries none.
    """
    from sparklib import config as spark_config
    import pathlib as _pl
    spark_config.set_config(ns, path=str(_pl.Path(__file__).resolve().parent / "spark.yaml"))


def _namespace_from_roles(roles, serials=None):
    """devices / serials as the config loader would build them.

    Derived from ROLES_FLEX rather than written out again, so the pinned robot
    and the simulated fleet cannot drift apart.
    """
    ids, sers = {}, {}
    for dev, role in roles.items():
        group, corner = role.split("/")
        ids.setdefault(group, {})[corner] = dev
        if serials and dev in serials:
            sers.setdefault(group, {})[corner] = serials[dev]
    # A second-bus group the tooling must never sweep. Ids overlap `drive` on
    # purpose: they are unique per bus, not per robot, which is the rule the
    # group partitioning exists to honour.
    ids["cancoder"] = {"LF": 1, "RF": 3, "LB": 2, "RB": 4}
    ns = lambda d: SimpleNamespace(**{k: SimpleNamespace(**v) for k, v in d.items()})
    return ns(ids), (ns(sers) if sers else None)


@pytest.fixture(autouse=True)
def pinned_to_flex(monkeypatch):
    """Every CLI call in this tier resolves rig-flex, whatever host it runs on.

    The fleet these tests build is rig-flex: eight SPARK Flex on firmware 26.1.6,
    ids 10 through 17, Appendix A periods. The CLI handlers they drive read the
    product, the id map, the serials and the baseline filename from the LIVE
    config, which is whatever robot the developer's hostname maps to. On a rig-max
    workstation that resolved controller_type sparkmax, so `spark repair` refused
    a Flex fleet as a MAX and twenty-four tests in this tier failed on a machine
    rather than on a defect.

    Pinning it here rather than in each module keeps the simulated robot and the
    declared robot the same object for the whole tier. A test that wants the
    other product asks for it explicitly, and its own monkeypatch wins because it
    is applied after this one.
    """
    from sparklib import admin as sa
    from sparklib import cli as spark_cli

    devices, serials = _namespace_from_roles(ROLES_FLEX, SERIALS_FLEX)
    _install(SimpleNamespace(controller_type="sparkflex",
                             can=SimpleNamespace(interface="can0"),
                             devices=devices,
                             serials=serials,
                             rig_name="rig-flex",
                             baseline_path=str(_shipped_baseline())))
    # _base_index reads the config FILENAME, not the config, and it is what
    # selects spark-baseline.yaml. Without this the audit runs baseline-less on
    # any host that is not rig-flex.
    monkeypatch.setattr(spark_cli, "_config_name", lambda: "spark.yaml")
    was = sa.set_controller_type("sparkflex")
    yield
    sa.set_controller_type(was)


@pytest.fixture
def pinned_to_max(monkeypatch):
    """Flip the tier to rig-max: eight SPARK MAX on 24.0.1, its real ids.

    Applied AFTER the autouse rig-flex pin, so it wins, exactly as that fixture's
    docstring says a test wanting the other product should do. Ask for this
    fixture and the `rig-max` bus together and the simulated robot and the
    declared robot are the same object again, on the other generation.

    This exists because the whole tier ran against one firmware generation. Every
    generation defect this package has shipped -- the drive gate, the byte 6
    decode, the coverage universe -- was a Flex assumption reaching a MAX, and
    none of them could fail a test that only ever built a Flex.
    """
    from sparklib import admin as sa
    from sparklib import cli as spark_cli

    devices, serials = _namespace_from_roles(ROLES_MAX, SERIALS_MAX)
    _install(SimpleNamespace(controller_type="sparkmax",
                             can=SimpleNamespace(interface="can0"),
                             devices=devices,
                             serials=serials,
                             rig_name="rig-max",
                             baseline_path=str(_pl_max_baseline())))
    monkeypatch.setattr(spark_cli, "_config_name", lambda: "spark.yaml")
    was = sa.set_controller_type("sparkmax")
    yield
    sa.set_controller_type(was)


@pytest.fixture
def roles_pre25():
    return dict(ROLES_MAX)


@pytest.fixture
def serials_pre25():
    return dict(SERIALS_MAX)


@pytest.fixture
def roles():
    return dict(ROLES_FLEX)


@pytest.fixture
def serials():
    return dict(SERIALS_FLEX)


# -- frame builders ------------------------------------------------------------

def frame(api, dev, data=b"", t=0.0, device_type=F.DEVICE_TYPE_MOTOR_CONTROLLER):
    """A broadcast frame as the bus would deliver it."""
    return SimMessage(F.arb(api, dev, device_type), data, timestamp=t)


def status_0_frame(dev, t=0.0, **kw):
    return frame(S0, dev, F.encode_status_0(**kw), t)


def status_1_frame(dev, t=0.0, **kw):
    return frame(S1, dev, F.encode_status_1(**kw), t)


def unique_id_frame(dev, serial, t=0.0):
    return frame(UID, dev, F.encode_unique_id(serial), t)


def firmware_frame(dev, version="26.1.6", hw_rev=3, t=0.0):
    return SimMessage(F.GET_FIRMWARE | dev, F.encode_firmware(version, hw_rev=hw_rev),
                      timestamp=t)


def param_resp_frame(dev, param_id, value, result=0, t=0.0):
    return SimMessage(F.PARAM_WRITE_RESP | dev,
                      F.encode_param_resp(param_id, value, result), timestamp=t)


def request(arb, data=b"", is_remote_frame=False, is_extended_id=True):
    """A frame a test sends into the bus itself, bypassing the driver."""
    return SimMessage(arb, data, is_remote_frame=is_remote_frame,
                      is_extended_id=is_extended_id)


def fleet_with(*extra, ids=range(10, 18), **kw):
    """The rig-flex eight plus whatever this test is adding (a duplicate, a PDH)."""
    return build_fleet(ids, **kw) + list(extra)


__all__ = ["frame", "status_0_frame", "status_1_frame", "unique_id_frame",
           "firmware_frame", "param_resp_frame", "request", "fleet_with",
           "spark", "attach", "F", "S0", "S1", "UID"]


def pytest_collection_modifyitems(items):
    """Mark the whole package, so `-m adversarial` and `-m 'not adversarial'` work.

    Applied here rather than per file: a new module must not be able to join the
    package and silently miss the marker every selection relies on.
    """
    for item in items:
        if item.path.parent == pathlib.Path(__file__).parent:
            item.add_marker(pytest.mark.adversarial)
