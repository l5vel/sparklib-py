"""Fixtures and safety guards for the hardware injection suite.

Everything here exists to make one class of mistake impossible rather than
remembered. The suite writes to controllers on a robot that has to drive
tomorrow, and three of its injections put frames on a live 1 Mbit bus.

Autouse, in the order they run:

  `not_a_live_experiment`  refuses the whole directory on a base index listed in
      sparkhw.guards.LIVE_EXPERIMENTS. rig-flex-2's five drifted and three intact
      controllers are the control and the subjects of a running experiment, and
      one `spark repair --persist` ends it. This also covers
      test_spark_persistence.py, which writes and persists and predates the list.
  `bus_at_rest`  refuses the run if anything is commanding, enabling or writing
      to these controllers. A SparkBus sends SECONDARY_HEARTBEAT every 20 ms, so
      a robot with the drive stack up is visible in one and a half seconds.
  `can_link_healthy`  reads the interface before and after every test. This
      fleet runs restart-ms 0, so a bus-off does not clear itself and the next
      test would measure a dead adapter and call it a dead controller.

The gates (`SPARK_HW_INJECT`, `SPARK_HW_COLLIDE`, `SPARK_HW_FLASH`,
`SPARK_HW_CONGEST`) and the staged runs (`SPARK_HW_STAGE`) are documented in
tests/hardware/README.md and enforced by the fixtures below.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import random

import pytest

from sparklib.config import get as _spark_config
from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparkhw import (Sniffer, StatusPeriodGuard, WireInjector,
                     at_rest_reasons, base_index, link_info, require_gate,
                     require_link, require_stage)
from sparkhw.guards import RestoreFailed, live_experiment_reason
from sparksim import frames as F


def _apply_tier_markers(items):
    """Mark the whole package `hardware`, so a new module cannot join it and
    silently miss the --hardware opt-in the root conftest enforces.

    `inject` and `staged` are applied per module by file name, so that
    `-m "hardware and not inject"` selects the read-only half without every
    module having to remember a pytestmark.

    test_spark_persistence.py is in the map because two of its six tests write a
    parameter and one of those burns it to flash. It predates these markers, so
    for one run it sat inside a selection advertised as read-only and spent a
    flash cycle nobody had asked for -- which is catalogue A4 happening to the
    suite that tests for A4.
    """
    here = pathlib.Path(__file__).parent
    by_module = {"test_wire_injection": "inject", "test_config_injection": "inject",
                 "test_spark_persistence": "inject",
                 "test_legacy_period_write": "inject",
                 "test_legacy_fault_injection": "inject",
                 "test_staged_physical": "staged"}
    for item in items:
        if item.path.parent != here:
            continue
        item.add_marker(pytest.mark.hardware)
        extra = by_module.get(item.path.stem)
        if extra:
            item.add_marker(getattr(pytest.mark, extra))


# -- the bus -------------------------------------------------------------------

@pytest.fixture(scope="session")
def channel():
    """The SPARK bus, from this robot's config unless SPARK_HW_CHANNEL says otherwise.

    The override exists so the wire tier can be exercised against a vcan
    interface with no robot attached: socketcan loops frames between sockets
    whether or not a transceiver is there, so the injector, the sniffer and
    SparkAdmin all behave the same. It does not weaken the live-experiment
    guard, which is keyed on the base index rather than on the interface name.
    """
    name = os.environ.get("SPARK_HW_CHANNEL", "").strip() or _spark_config().can.interface
    reason = require_link(name)
    if reason:
        pytest.skip(reason)
    return name


@pytest.fixture(scope="session", autouse=True)
def not_a_live_experiment():
    reason = live_experiment_reason()
    if reason:
        pytest.skip(f"refusing to touch {base_index()}: {reason}")


@pytest.fixture(scope="session", autouse=True)
def bus_at_rest(channel):
    """One check per session, because it costs a second and a half of listening.

    A robot that starts driving part way through a session is not caught, and
    that is the gap to know about: the writes in this suite trust a reading
    taken once, at the start.
    """
    reasons = at_rest_reasons(channel)
    if reasons:
        pytest.skip("the bus is not at rest -- something else owns these "
                    "controllers: " + "; ".join(reasons))


# Controllers a run wrote to and could not put back. A parameter write is
# undone by its guard and verified on the wire, so this stays empty unless the
# undo itself failed -- at which point every later test would be reading a
# controller that is not the one it thinks it is, and would fail in a way that
# reads like a driver defect. Nothing here is persisted, so a motor-rail power
# cycle clears it.
_LEFT_DIRTY = []


@pytest.fixture(autouse=True)
def no_injection_left_behind():
    """Stop once, clearly, instead of failing every test after a failed undo."""
    if _LEFT_DIRTY:
        pytest.skip("an earlier test could not undo what it wrote, so this bus is "
                    "no longer the one these tests assume. Cut and restore motor "
                    "power -- nothing was persisted -- then re-run.\n  "
                    + "\n  ".join(_LEFT_DIRTY))


@pytest.fixture(autouse=True)
def can_link_healthy(request, channel):
    """Fail a test that left the adapter bus-off, and skip one that started so.

    The `bus_off_expected` marker opts out in both directions. Only the staged
    CANH/CANL swap carries it: with the pair reversed the adapter leaving
    ERROR-ACTIVE is the observation, not a side effect.
    """
    expected = request.node.get_closest_marker("bus_off_expected") is not None
    before = link_info(channel)
    if not expected and before and before.get("state") == "BUS-OFF":
        pytest.skip(
            f"{channel} was already BUS-OFF before this test and restart-ms is "
            f"{before.get('restart_ms')}:\n"
            f"  sudo ip link set {channel} down && sudo ip link set {channel} up")
    yield before
    after = link_info(channel)
    if not expected and after and after.get("state") == "BUS-OFF":
        raise AssertionError(
            f"{channel} went BUS-OFF during {request.node.name} and restart-ms is "
            f"{after.get('restart_ms')}, so it will not recover on its own:\n"
            f"  sudo ip link set {channel} down && sudo ip link set {channel} up")


@pytest.fixture
def adm(channel):
    with sa.SparkAdmin(channel) as session:
        yield session


@pytest.fixture
def sniff(channel):
    """Factory: `with sniff() as s:` records the wire while the block runs."""
    made = []

    def _make():
        s = Sniffer(channel)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()


@pytest.fixture
def inject(channel):
    with WireInjector(channel) as injector:
        yield injector


# -- the fleet this robot is configured for ------------------------------------

@pytest.fixture
def roles():
    return spark_cli._spark_roles()


@pytest.fixture
def serials():
    return spark_cli._known_serials()


@pytest.fixture
def baseline():
    data, path = spark_cli._load_baseline()
    if not data:
        pytest.skip(f"no baseline at {path}; run `spark snapshot --write` on a "
                    "known-good bus")
    return data


@pytest.fixture
def configured_ids(roles):
    return sorted(roles)


@pytest.fixture(scope="session")
def writable_id(request):
    """The one controller this suite is allowed to write a period into.

    Chosen at random once per session, so write and flash wear spreads across the
    fleet rather than landing on one device every run. Per-session rather than
    per-test keeps a failure attributable to a controller, and the summary line
    reports the pick. SPARK_HW_SEED reproduces a choice, SPARK_HW_ID pins one.
    """
    roles = spark_cli._spark_roles()
    override = os.environ.get("SPARK_HW_ID", "").strip()
    if override:
        dev = int(override)
    else:
        seed = os.environ.get("SPARK_HW_SEED", "").strip()
        rng = random.Random(int(seed)) if seed else random.Random()
        dev = rng.choice(sorted(roles))
    if dev not in roles:
        pytest.skip(f"SPARK_HW_ID={dev} is not in devices")
    request.config._spark_writable_id = dev
    return dev


@pytest.fixture
def free_id(roles):
    """A CAN id in the legal range that no configured controller owns.

    Counted down from the top of the range, so a phantom lands well clear of
    both the SPARK ids and the CANcoder ids in devices, and an operator
    reading a candump can tell an injected frame by its address alone.
    """
    for candidate in range(62, 0, -1):
        if candidate not in roles:
            return candidate
    pytest.skip("every legal CAN id is configured; nowhere to put a phantom")


# -- opt-in --------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """Apply the tier markers, then skip clean-bus tests under a live injector.

    ONE hook. There were two, and the second shadowed the first at module level,
    so `hardware`, `inject` and `staged` were never applied. `-m "not inject and
    not staged"` then selected everything, and the documented read-only tier
    included the write-and-persist module. That is catalogue A4, an unwanted
    flash burn, happening to the suite that tests for A4.
    """
    _apply_tier_markers(items)
    _skip_clean_bus_tests_under_an_injector(items)


def _skip_clean_bus_tests_under_an_injector(items):
    """Skip clean-bus tests while a saturating injector is armed.

    SPARK_HW_COLLIDE transmits on an id a real controller owns and
    SPARK_HW_CONGEST fills the bus. Both leave a sticky `can` fault on every
    device and starve the queries an inventory depends on. A test that needs a
    healthy controller or a complete inventory then fails its own premise
    instead of finding anything -- measured on two of them.

    This only catches an injector armed in THIS run. `clean_bus` below reads the
    bus, which is what catches one a previous run left dirty.
    """
    import os

    armed = [g for g in ("SPARK_HW_COLLIDE", "SPARK_HW_CONGEST")
             if os.environ.get(g) == "1"]
    if not armed:
        return
    skip = pytest.mark.skip(
        reason=f"{'/'.join(armed)} armed: this test needs a clean bus, and the "
               "injector faults every controller and thins the inventory")
    for item in items:
        if item.get_closest_marker("needs_clean_bus"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def clean_bus(request, channel):
    """Read the bus before a `needs_clean_bus` test, instead of trusting a gate.

    The env-var skip above tests what THIS run armed. Nothing clears sticky
    faults when an injector tier finishes, so a COLLIDE or CONGEST run leaves a
    sticky `can` on all eight controllers and the next read-only run inherits it.
    On that failed the healthy-control premise in test_legacy_decoder
    with no injector armed at all.
    """
    if not request.node.get_closest_marker("needs_clean_bus"):
        return
    with sa.SparkAdmin(channel) as probe:
        status = sa.collect_status(probe.bus, seconds=1.0)
    dirty = {dev: r["sticky_faults"]
             for dev, r in ((d, sa.normalised_reading(v)) for d, v in status.items())
             if r["sticky_faults"]}
    if dirty:
        pytest.skip(
            "this test needs a clean bus and the fleet carries sticky faults: "
            + ", ".join(f"id {d} {'/'.join(f)}" for d, f in sorted(dirty.items()))
            + " -- run `uv run spark faults` to record them, then `uv run spark clear`")


@pytest.fixture
def gate():
    """Factory: `gate('inject')` skips unless that environment gate is open."""
    def _require(name):
        reason = require_gate(name)
        if reason:
            pytest.skip(reason)
    return _require


@pytest.fixture
def staged():
    """Factory: `staged('cycle-arm')` skips unless this is that staged run."""
    def _require(name):
        reason = require_stage(name)
        if reason:
            pytest.skip(reason)
    return _require


# -- writing, and putting it back ----------------------------------------------

class _Dialect:
    """How this generation carries faults and moves a status period.

    On 25+ a status period is a parameter and PARAMETER_WRITE echoes what it
    took. On pre-25 the periods are not parameters and move on api class 6 with
    no acknowledgement. A test that asks this object rather than a module
    constant runs on either generation.
    """

    def __init__(self, generation):
        self.generation = generation
        self.legacy = generation == sa.GEN_PRE25
        ff = sa.fault_frame(generation)
        # The frame the audit scores, which also carries faults on each
        # generation: 0x2E1 on 25+, 0x060 on pre-25.
        self.fault_api = ff["api"]
        self.frame_label = ff["label"]
        self.expected_ms = ff["expected_ms"]
        self.rev_default_ms = ff["rev_default_ms"]
        # A frame that keeps flowing while the one above is starved.
        self.other_api = (sa.LEGACY_STATUS_0_API + 1 if self.legacy
                          else F.API_STATUS_0)
        # 158..165 run Status 0..7, so the period parameter and the frame index
        # both follow from which frame the audit scores.
        self.param = (sa.PARAM_STATUS_0_PERIOD if self.legacy
                      else sa.PARAM_STATUS_1_PERIOD)
        self.frame_index = (self.param - sa.PARAM_STATUS_0_PERIOD
                            if self.legacy else None)

    @property
    def acknowledges(self):
        """Whether a period write comes back with a response to inspect."""
        return not self.legacy

    def write_period(self, adm, dev, value_ms):
        """Set the period in whichever dialect this generation speaks."""
        if self.legacy:
            adm.set_legacy_status_period(dev, self.frame_index, value_ms)
            return None
        return adm.write_param(dev, self.param, value_ms)

    def writable_param(self):
        """A parameter this generation answers a write on, for tests about write
        mechanics rather than about periods. Idle Mode is read-write and its
        value can be read straight back."""
        return (6, 1) if self.legacy else (self.param, None)


@pytest.fixture
def dialect(adm):
    """Which write dialect this bus speaks, read off the wire."""
    return _Dialect(sa.dominant_generation(sa.collect_status(adm.bus, 2.0),
                                           default=sa.GEN_FW25))


@pytest.fixture
def period_guard(request, adm, roles, gate):
    """Factory: a StatusPeriodGuard that restores on the way out.

    Nothing it writes is persisted, so the value in flash is the provisioned one
    throughout and a motor-rail power cycle is the backstop if this process is
    killed with an injection in place.
    """
    gate("inject")
    made = []

    def _make(dev, param_id=sa.PARAM_STATUS_1_PERIOD):
        guard = StatusPeriodGuard(adm, dev, param_id,
                                  role=roles.get(dev, "drive/?").split("/")[0])
        made.append(guard)
        return guard

    yield _make
    for guard in made:
        try:
            guard.restore()
        except RestoreFailed as exc:
            _LEFT_DIRTY.append(f"{request.node.name}: {exc}")
            raise


def _masked(api, fields):
    """Fault and warning names to the bit masks encode_status_1 packs.

    A name is checked against the table and an unknown one raises, so a typo in
    a test is a collection-time error and never a frame with an empty mask that
    injects nothing and asserts fine.
    """
    if api != F.API_STATUS_1:
        return fields
    out = dict(fields)
    for key in ("faults", "sticky_faults"):
        if key in out:
            out[key] = F.as_fault_mask(out[key])
    for key in ("warnings", "sticky_warnings"):
        if key in out:
            out[key] = F.as_warn_mask(out[key])
    return out


@pytest.fixture
def spoofed_status(adm, inject, period_guard):
    """Factory: take one of a controller's status frames off the air, then speak
    for it.

    `collect_status` keeps the last frame it saw per (device, api), so injecting
    a fault bit alongside a controller broadcasting at 20 ms is a race. Starving
    the real frame first makes it deterministic, and it costs no arbitration
    collision: while the guard holds, the injected frames are the only ones on
    that id and api. The other status frames keep coming from the controller, so
    the audit still reads a real rail voltage beside an injected fault.

        with spoofed_status(12, F.API_STATUS_1, faults=["gateDriver"]):
 ...

    Nothing is persisted. If this process is killed mid-block the controller
    stays quiet on that frame until the motor rail is cycled, which reloads the
    provisioned period from flash.
    """
    @contextlib.contextmanager
    def _spoof(dev, api, *, seconds=20.0, period_s=0.02, **fields):
        param_id = F.PERIOD_PARAM_FOR_API[api]
        encode = {F.API_STATUS_0: F.encode_status_0,
                  F.API_STATUS_1: F.encode_status_1}[api]
        fields = _masked(api, fields)
        guard = period_guard(dev, param_id)
        starve = guard.starve_value()
        response = guard.write(starve)
        if response["result"] != 0:
            pytest.skip(f"id {dev} refused a {starve} ms period for parameter "
                        f"{param_id}: {response['result_text']}")
        frame = (F.arb(api, dev), encode(**fields))
        try:
            with inject.stream([frame], period_s, seconds) as stream:
                yield stream
        finally:
            guard.restore()

    return _spoof


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Name the controller this run wrote to, since the pick is random."""
    dev = getattr(config, "_spark_writable_id", None)
    if dev is not None:
        terminalreporter.write_line(
            f"spark injection target: id {dev} "
            f"({spark_cli._spark_roles().get(dev, '-')}) -- "
            f"pin it with SPARK_HW_ID={dev}")
