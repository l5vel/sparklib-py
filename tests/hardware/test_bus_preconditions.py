"""What has to be true of this bus before any injection means anything.

Every test below is a control. An injection suite that reported a duplicate id
on a bus that already had one, or a fault it did not inject, would be reading
the robot rather than the tool -- so the state of the fleet is asserted first,
in the terms the catalogue uses, and the injection modules are read against it.

Four of these are also the only hardware answer to a catalogue entry that cannot
be injected at all:

  A4  flash wear from persisting every boot. Endurance is 1e4 to 1e5 cycles, so
      reproducing the wear outlasts the controllers. What is testable is that no
      start-up path spends a cycle.
  B3/B5  follower mode. Injecting it means writing a leader id, and REVLib 2025
      runs follower mode whether or not user code references the follower, so a
      mis-set leader drives a wheel with nothing on the host holding it. The bit
      is read instead.
  F1/F4  a firmware update that reported success without updating, and a
      controller that reverted on its own. GET_FIRMWARE_VERSION is the one read
      26.1.6 answers, so the version is compared against the baseline.
  F2/F6  a mixed-version bus. 25.0.0 beside a 24.0.x device drives utilisation
      up on its own; this fleet is uniform and that is asserted, not assumed.

Two tests here are static rather than electrical. They sit beside their
behavioural twin rather than in tests/unit because they answer the same
catalogue entry, and splitting an entry across two tiers is how half of it stops
being read.
"""
from __future__ import annotations

import ast
import pathlib
import time

import pytest

from sparklib import admin as sa
from sparksim import frames as F

# REV's enabledByDefault set for a SPARK Flex: STATUS_0, STATUS_1 and the
# unique-id broadcast. The post-power-cycle capture on this fleet showed exactly
# these three and nothing else (docs/spark/runs/-rig-flex-probe-log.md).
DEFAULT_ENABLED_APIS = {F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID}

# Pre-25 broadcasts a different, larger set and no unique id at all. Status 4 is
# absent because it is the alternate-encoder frame and nothing here configures
# one. See docs/SPARKMAX-BRINGUP.md for the observed set.
LEGACY_ENABLED_APIS = {0x060, 0x061, 0x062, 0x063, 0x065, 0x066, 0x067}


def _expected_apis(generation):
    return (LEGACY_ENABLED_APIS if generation == sa.GEN_PRE25
            else DEFAULT_ENABLED_APIS)

PLAUSIBLE_RAIL_V = (6.0, 30.0)
PLAUSIBLE_TEMP_C = 100


def _status(adm, seconds=3.0):
    return sa.collect_status(adm.bus, seconds=seconds)


# -- the fleet -----------------------------------------------------------------

def test_the_configured_fleet_is_the_fleet_on_the_wire(adm, roles, serials):
    """The role label in every message this suite prints comes from
    devices, so it is an expectation and not an observation. It is only
    trustworthy while the configured ids are all present and every serial
    matches -- the serial is the one identity no reset can touch, because it is
    not in REV's 199-entry parameter table at all.
    """
    inv = adm.inventory(4.0)
    missing = sorted(set(roles) - set(inv))
    assert not missing, (
        "these configured ids are not broadcasting: "
        + ", ".join(f"{d} ({roles[d]})" for d in missing)
        + " -- a single GET_FIRMWARE to any one of them wakes the whole bus "
        "without erasing anything, so query first and read `spark faults` "
        "before considering `spark clear`")
    gen = sa.dominant_generation(_status(adm, seconds=2.0))
    if gen == sa.GEN_PRE25:
        # Unique ID Broadcast is apiClass 47, versionImplemented 25.0.0, so this
        # firmware emits no serial and learn-serials cannot populate the config.
        # Identity has to come from somewhere else here: every parameter answers
        # on pre-25, so SparkAdmin.read_legacy_param is the replacement.
        assert not any(i["serial"] for i in inv.values()), (
            "a pre-25 bus broadcast a serial, which contradicts "
            "max.unique_id.api -- check the generation before trusting this")
        return
    assert serials, ("serials is not set; run `spark learn-serials "
                     "--write` or a swapped controller is invisible here")
    shared = sorted(set(roles) & set(serials))
    wrong = {d: (inv[d]["serial"], serials[d]) for d in shared
             if inv[d]["serial"] != serials[d]}
    assert not wrong, f"serial does not match config: {wrong}"


def test_the_bus_carries_only_the_frames_this_generation_broadcasts(adm, sniff, roles):
    """Every period this suite measures is a frame count over a window, so a
    status frame nobody expected -- an extra one enabled by a tool that ran
    earlier, or a second admin host writing parameters -- changes the arithmetic
    without changing anything the audit reports. Catalogue C1/C3: absent frames
    from congestion and absent frames from config look identical, and so do
    extra ones.
    """
    gen = sa.dominant_generation(_status(adm, seconds=2.0))
    expected = _expected_apis(gen)

    with sniff() as sniffer:
        time.sleep(4.0)

    apis = {}
    for m in sniffer.from_the_bus:
        if F.is_spark(m.arbitration_id):
            apis.setdefault(F.api_of(m.arbitration_id), set()).add(
                F.dev_of(m.arbitration_id))
    unexpected = {f"0x{api:03X}": sorted(devs) for api, devs in apis.items()
                  if api not in expected}
    assert not unexpected, (
        f"REV device frames outside the {gen} set "
        f"{sorted(hex(a) for a in expected)}: {unexpected}\n" + sniffer.explain())
    assert set(apis) == expected, (
        f"expected the whole {gen} set {sorted(hex(a) for a in expected)}, "
        f"saw {sorted(hex(a) for a in apis)}")
    assert not sniffer.from_this_host, (
        "this host transmitted during a passive listen: "
        + sniffer.explain())


def test_every_controller_reports_a_plausible_rail_and_no_active_fault(adm, roles):
    """The control for every fault injection in this suite. A controller that
    was already faulted would make an injected fault unattributable, and a
    controller already reading 0.01 V would make the telemetry checks
    meaningless. Sticky bits are reported and not asserted on: they are history,
    they are evidence, and this suite must not require them to be cleared.
    """
    status = _status(adm)
    faulted, implausible, sticky = {}, {}, {}
    for dev in sorted(roles):
        reading = status.get(dev)
        assert reading, f"id {dev} ({roles[dev]}) broadcast no status at all"
        # normalised_reading, not the raw frames: the two generations file these
        # fields in different frames, and reaching into status1 for a fault word
        # is a KeyError on pre-25, where 0x061 is telemetry only.
        r = sa.normalised_reading(reading)
        assert r["has_telemetry"], (
            f"id {dev} ({roles[dev]}) broadcast no telemetry frame")
        if r["faults"] or r["warnings"]:
            faulted[dev] = (r["faults"], r["warnings"])
        if r["sticky_faults"] or r["sticky_warnings"]:
            sticky[dev] = (r["sticky_faults"], r["sticky_warnings"])
        low, high = PLAUSIBLE_RAIL_V
        if (r["voltage_v"] is None or not low <= r["voltage_v"] <= high
                or r["motor_temp_c"] > PLAUSIBLE_TEMP_C):
            implausible[dev] = (r["voltage_v"], r["motor_temp_c"])

    assert not faulted, (
        f"controllers are already faulted, so an injected fault cannot be told "
        f"from this one: {faulted}. Sticky history on the bus: {sticky}")
    assert not implausible, (
        f"rail or temperature outside {PLAUSIBLE_RAIL_V} V / {PLAUSIBLE_TEMP_C} C: "
        f"{implausible}")


def test_no_controller_on_this_bus_is_in_follower_mode(adm, roles):
    """Catalogue B5. kFollowerID stores the leader's full 29-bit STATUS_0
    arbitration id, and REVLib 2025 makes follower mode work "even if the
    follower is not referenced in user code" -- so a controller left following
    drives its mechanism with no object in the program to stop it. The bit is in
    STATUS_1 and is already decoded; nothing reads it.
    https://www.chiefdelphi.com/t/378716
    """
    status = _status(adm)
    readings = {dev: sa.normalised_reading(status[dev])
                for dev in sorted(roles) if status.get(dev)}
    readable = [d for d, r in readings.items() if r["is_follower"] is not None]
    if not readable:
        # Pre-25 LEGACY_STATUS_0 carries no follower bit, so there is nothing to
        # read rather than nothing broadcasting. Skipping says which it is.
        gen = sa.dominant_generation(status)
        assert readings, (
            "no controller broadcast anything, so the follower bit could not be "
            "read on any of them. This test filtered on 'controllers that are "
            "broadcasting' and passed over an empty list once already, on a bus "
            "that had come back silent from a rail cycle")
        pytest.skip(f"{gen} carries no follower bit in any frame it broadcasts; "
                    "on this generation the check needs parameter 57 (Follower "
                    "ID) via SparkAdmin.read_legacy_param instead")
    following = [d for d in readable if readings[d]["is_follower"]]
    assert not following, (
        "these controllers are in follower mode: "
        + ", ".join(f"{d} ({roles[d]})" for d in following)
        + " -- a follower drives whether or not any code references it")


# -- firmware ------------------------------------------------------------------

def test_the_whole_fleet_runs_one_firmware_version(adm, roles):
    """Catalogue F2 and F6. The repeated advice on every mixed-version thread is
    to update every device and re-check, and 25.0.0 beside a 24.0.x device
    drives CAN utilisation up on its own -- which then reads here as four
    controllers that reverted their config.
    https://www.chiefdelphi.com/t/456184
    """
    versions = {}
    for dev in sorted(roles):
        version, _ = adm.firmware(dev)
        assert version, (f"id {dev} ({roles[dev]}) did not answer "
                         "GET_FIRMWARE_VERSION, the one read 26.1.6 does answer")
        versions.setdefault(version, []).append(dev)
    assert len(versions) == 1, f"mixed firmware on one bus: {versions}"


def test_every_controller_answers_the_firmware_version_the_baseline_recorded(
        adm, roles, baseline):
    """Catalogue F1 and F4: an update that reported success without updating,
    and a controller that went back to an older version on its own. Neither is
    injectable without a USB reflash, so the baseline is the witness.
    https://www.chiefdelphi.com/t/341470
    https://www.chiefdelphi.com/t/372085
    """
    recorded = {int(d): (v or {}).get("firmware")
                for d, v in (baseline.get("controllers") or {}).items()}
    drifted = {}
    for dev in sorted(set(roles) & set(recorded)):
        want = recorded[dev]
        if want in (None, "unknown"):
            continue
        live, _ = adm.firmware(dev)
        if live != want:
            drifted[dev] = (live, want)
    assert not drifted, (
        f"firmware differs from {baseline['meta']['base_index']}_baseline.yaml "
        f"(live, baseline): {drifted}")


# -- the adapter ---------------------------------------------------------------

def test_the_can_interface_is_error_active_and_carrying_traffic(channel,
                                                                can_link_healthy):
    """The congestion injections can drive a gs_usb adapter to bus-off, and this
    fleet runs restart-ms 0, so the controller does not recover on its own. If
    that has already happened, every later test reads a dead adapter and reports
    it as eight dead controllers.
    """
    info = can_link_healthy
    assert info is not None, f"`ip link show {channel}` returned nothing"
    if info["kind"] != "can":
        pytest.skip(f"{channel} is a {info['kind']} interface, which has no CAN "
                    "controller and so no error state to read")
    assert info["state"] in ("ERROR-ACTIVE", "ERROR-WARNING"), (
        f"{channel} is in CAN state {info['state']} with restart-ms "
        f"{info['restart_ms']}:\n"
        f"  sudo ip link set {channel} down && sudo ip link set {channel} up")
    assert info["rx_packets"], f"{channel} has received nothing since it came up"


# -- the two static halves -----------------------------------------------------

def _package_root():
    return pathlib.Path(sa.__file__).resolve().parent


def test_no_start_up_path_commits_parameters_to_flash():
    """Catalogue A4, CD 455171: flash endurance is ten to hundreds of thousands
    of cycles, and some REV examples called burn on every boot. The wear itself
    outlasts any test, so what is asserted is the shape that produces it -- a
    persist reachable from anything that runs at start-up. Today the only two
    callers are the two subcommands whose entire job is to persist.
    https://www.chiefdelphi.com/t/455171
    """
    # set_can_id burns deliberately, and CD 425589 is why: "every time I
    # reconnect to the motor controller, the ID resets". A renumbering that is
    # not burned has not renumbered anything, it has armed the same collision
    # for the next power cycle. It is an explicit operator command, never a
    # start-up path, which is what this test is actually about.
    allowed = {"cmd_persist", "cmd_repair", "cmd_provision", "set_can_id"}
    callers = {}
    for path in _package_root().rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "persist"):
                    callers.setdefault(node.name, []).append(
                        f"{path.name}:{inner.lineno}")
    unexpected = {k: v for k, v in callers.items() if k not in allowed}
    assert not unexpected, (
        "PERSIST_PARAMETERS is reachable from something other than the persist "
        f"subcommands: {unexpected}")

    # The allowlist is only safe while nothing on a start-up path calls those.
    # A flash cycle is finite and a boot that burns one is how they get spent.
    STARTUP = {"__init__", "init", "init_controller", "apply_boot_config",
               "connect", "start", "_verify_controllers_live", "bus_monitor"}
    reached = {}
    for path in _package_root().rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in STARTUP:
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("persist", "set_can_id")):
                    reached.setdefault(node.name, []).append(
                        f"{path.name}:{inner.lineno} -> {inner.func.attr}")
    assert not reached, (
        "a start-up path reaches flash. Booting the robot would spend a flash "
        f"cycle: {reached}")


def test_the_startup_path_clears_the_sticky_evidence_this_suite_reads():
    """rig-flex-2: `spark clear` ran before `spark faults` after a power
    cycle and erased the hasReset the cycle had just produced, costing a data
    point. The drive stack does the same thing without being asked --
    SparkBus.apply_boot_config sends Clear Faults to every SparkFlex it
    initialises -- so a staged run that starts BaseHandler first has nothing
    left to read.

    Static, because proving it electrically means starting the drive stack on a
    robot mid-test. It sits here rather than in tests/unit because it is the
    precondition the staged power-cycle tests depend on.
    """
    from sparklib import can_bus as spark_can, controller as spark_controller

    class _Recording:
        controller_type = spark_controller.SPARK_FLEX
        cleared = 0

        def clear_faults(self):
            type(self).cleared += 1

    spark_can.SparkBus.apply_boot_config(None, _Recording())
    assert _Recording.cleared == 1, (
        "apply_boot_config no longer clears sticky faults on a SparkFlex; the "
        "ordering warning in tests/hardware/README.md and in rig-flex-2's protocol "
        "is now stale and should be corrected rather than carried")
