"""Is the simulator faithful? Every adversarial module is worthless if it is not.

This file tests the simulator, not the driver -- except where proving the
simulator means driving the real SparkAdmin against it, which is the only way to
show that a healthy modelled fleet is one a real tool reads correctly. The four
things every later module leans on:

  * a healthy eight measures to its nominal cadence exactly, so any deviation a
    test asserts came from the failure it injected and not from the scheduler;
  * the clock is virtual and total -- a 5 s inventory costs no real time and
    `clock.now` is itself assertable;
  * ground truth (`ram`/`flash`, `write_log`, `persist_log`, `clear_log`) says
    what the device did, which no CAN frame reveals;
  * the frame encoders round-trip through the driver's own decoders, so a
    simulator bug shows up here rather than as a mysteriously passing test.

Section numbers (S1-S13) are the conformance self-check in the SPARKSIM spec.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from sparklib import admin as sa
from sparksim import (SimSpark, SparkBehaviour, SparkBusSim, VirtualClock,
                      assert_no_setpoints, attach, build_fleet, factory, spark)
from sparksim import frames as F
from sparksim.faults import (AmbiguousDeviceError, BootloaderFrameForbidden,
                             ProtectedWriteReachedBus, SetpointFrameForbidden,
                             SimFrameBudgetExceeded)
from sparksim.bus import SimMessage
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
P159 = F.PARAM_STATUS_1_PERIOD
SPARKSIM_DIR = pathlib.Path(__file__).resolve().parents[1] / "support" / "sparksim"


# -- S1: arbitration id --------------------------------------------------------

@pytest.mark.parametrize("api, dev, expected", [
    (S0, 12, 0x0205B80C),
    (S1, 12, 0x0205B84C),
    (UID, 17, 0x0205BC11),
])
def test_arb_matches_the_frame_layout_verified_on_hardware(api, dev, expected):
    assert F.arb(api, dev) == expected
    assert F.split_arb(expected) == (2, 5, api, dev)


def test_is_spark_rejects_other_device_types_and_manufacturers():
    assert F.is_spark(F.arb(S0, 10))
    assert not F.is_spark(F.arb(S0, 10, device_type=F.DEVICE_TYPE_PDH)), (
        "a REV PDH legitimately shares id 10; it is not a SPARK")
    assert not F.is_spark(F.arb(S0, 10, mfr=3))


# -- S2: encoder vectors, byte for byte ----------------------------------------

def test_status_0_encodes_the_rig_flex_idle_frame():
    """The fleet-derived idle frame in sparksim.frames, byte for byte.

    Assembled from the candump rather than captured whole; see the
    comment on CAPTURED_BASE03_IDLE_STATUS_0 for which controller gave which
    field.
    """
    volts = F.CAPTURED_BASE03_IDLE_VOLTS          # raw 1804, i.e. 13.216 V
    assert F.encode_status_0(volts=volts, temp_c=31, model=1) ==\
        F.CAPTURED_BASE03_IDLE_STATUS_0
    assert sa.decode_status_0(F.encode_status_0(volts=volts, temp_c=31))[
        "voltage_v"] == pytest.approx(13.2, abs=0.3)


def test_the_remaining_encoder_vectors():
    assert F.encode_status_1(faults=F.FAULT["gateDriver"])[0] == 0x20
    assert F.encode_firmware("26.1.6", hw_rev=3)[:6] == bytes([26, 1, 0, 6, 0, 3])
    assert F.encode_param_resp(159, 20, 0) == bytes([159, 2, 20, 0, 0, 0, 0])
    assert len(F.encode_unique_id("6B029ADD")) == 4
    assert len(F.encode_persist_resp(0)) == 1


def test_fault_mask_raises_on_a_typo():
    """A silent 0 would turn an adversarial test into a test of a healthy bus."""
    assert F.fault_mask("gateDriver", "sensor") == 0x24
    with pytest.raises(KeyError):
        F.fault_mask("gateDrive")
    with pytest.raises(KeyError):
        F.warn_mask("brownouts")


# -- S3: the driver's own decoders round-trip the encoders ---------------------

def test_status_0_round_trips_through_the_drivers_decoder():
    d = F.encode_status_0(applied=-0.5, volts=11.75, amps=42.0, temp_c=64, model=1,
                          hard_fwd=True, soft_rev=True, inverted=True,
                          heartbeat_lock=True)
    out = sa.decode_status_0(d)
    assert out["applied_output"] == pytest.approx(-0.5, abs=1e-4)
    assert out["voltage_v"] == pytest.approx(11.75, abs=0.01)
    assert out["current_a"] == pytest.approx(42.0, abs=0.05)
    assert out["motor_temp_c"] == 64
    assert out["spark_model"] == 1
    assert (out["hard_forward_limit"], out["soft_reverse_limit"]) == (True, True)
    assert (out["hard_reverse_limit"], out["soft_forward_limit"]) == (False, False)
    assert out["inverted"] and out["primary_heartbeat_lock"]


@pytest.mark.parametrize("name", F.FAULT_BITS)
def test_every_fault_bit_survives_the_round_trip(name):
    m = F.FAULT[name]
    out = sa.decode_status_1(F.encode_status_1(faults=m, sticky_faults=m))
    assert out["faults"] == [name] and out["sticky_faults"] == [name]


@pytest.mark.parametrize("name", F.WARNING_BITS)
def test_every_warning_bit_survives_the_round_trip(name):
    m = F.WARN[name]
    out = sa.decode_status_1(F.encode_status_1(warnings=m, sticky_warnings=m))
    assert out["warnings"] == [name] and out["sticky_warnings"] == [name]


def test_follower_flag_round_trips():
    assert sa.decode_status_1(F.encode_status_1(follower=True))["is_follower"] is True


# -- S4: drift guard, the one place the two constant sets are compared ---------

@pytest.mark.parametrize("name", ["CLEAR_FAULTS", "IDENTIFY_UNIQUE", "SET_CAN_ID",
                                  "GET_FIRMWARE", "PARAM_WRITE", "PARAM_WRITE_RESP",
                                  "PERSIST", "PERSIST_RESP", "PERSIST_MAGIC"])
def test_simulator_and_driver_agree_on_the_frame_bases(name):
    assert getattr(F, name) == getattr(sa, name), (
        f"{name} differs between sparksim.frames and spark_admin; one of them "
        "drifted from REV-Specs 2.1.0")


def test_simulator_and_driver_agree_on_apis_and_protected_params():
    assert (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID) ==\
        (sa.STATUS_0_API, sa.STATUS_1_API, sa.UNIQUE_ID_API)
    assert F.PROTECTED_PARAMS == set(sa.PROTECTED_PARAMS)
    assert F.PARAM_STATUS_1_PERIOD == sa.PARAM_STATUS_1_PERIOD
    assert F.PARAM_CAN_ID == sa.PARAM_CAN_ID
    assert F.FAULT_BITS == sa._FAULT_BITS and F.WARNING_BITS == sa._WARNING_BITS


# -- S5: a healthy eight, read by the real driver ------------------------------

def test_inventory_finds_all_eight_with_the_right_serials_and_cadence(rig_flex, clock):
    adm = attach(rig_flex)
    inv = adm.inventory(5.0)

    assert sorted(inv) == list(range(10, 18)), rig_flex.explain()
    for dev, info in inv.items():
        assert info["serial"] == SERIALS_FLEX[dev], (
            f"id {dev} broadcast the wrong UNIQUE_ID payload\n" + rig_flex.explain(dev))
        assert info["periods_ms"][S0] == 10.0
        assert info["periods_ms"][S1] == 20.0, (
            "a healthy provisioned controller must measure to Appendix A exactly, "
            "or every cadence finding in this suite is scheduler noise\n"
            + rig_flex.explain(dev))
        assert info["periods_ms"][UID] == 1000.0
        assert sa.status_1_verdict(info["periods_ms"][S1]) == "ok"
    assert clock.now == 5.0, "the window cost exactly its own virtual length"
    assert rig_flex.elapsed() == 5.0


def test_the_five_second_window_costs_no_real_time(rig_flex, clock):
    """The whole point of the virtual clock: no sleeps, no wall time."""
    attach(rig_flex).inventory(5.0)
    assert clock.sleeps == [], "nothing slept; the window was pure arithmetic"
    # Derived, not written down: a provisioned controller broadcasts Status 0-9
    # plus UNIQUE_ID, and hardcoding the total went stale the day the fleet
    # started modelling the full declared configuration.
    expected = 0
    for c in rig_flex.controllers:
        for api in c.enabled_apis():
            expected += int(5000 / c.period_ms(api))
    assert abs(len(rig_flex.delivered) - expected) <= len(rig_flex.controllers) * 12, (
        f"delivered {len(rig-flex.delivered)}, derived {expected} from the "
        "controllers' own enabled apis and periods")


def test_a_reverted_controller_reads_as_the_factory_default(sim):
    bus = sim([factory(12)])
    adm = attach(bus)
    period = adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)
    assert period == 250.0
    assert sa.status_1_verdict(period) == "reverted"


def test_a_disabled_status_frame_reads_as_absent(sim):
    bus = sim([spark(12)])
    bus.disable_frame(12, S1)
    adm = attach(bus)
    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=2.0) is None
    assert sa.status_1_verdict(None) == "absent"
    assert bus.frames(12, api=S0), "only STATUS_1 went quiet"


def test_firmware_answers_an_rtr_request(rig_flex):
    adm = attach(rig_flex)
    assert adm.firmware(12) == ("26.1.6", 3)
    assert adm.firmware(17) == ("26.1.6", 3)


def test_firmware_is_silent_when_the_controller_does_not_answer(sim):
    bus = sim([spark(12, behaviour=SparkBehaviour(answer_firmware=False))])
    assert attach(bus).firmware(12) == (None, None)
    assert any(why == "firmware not answered" for _, _, why in bus.ignored)


def test_duplicates_sees_two_controllers_on_one_id(sim):
    bus = sim(build_fleet() + [spark(12, "DEADBEEF")])
    adm = attach(bus)
    dups = adm.duplicates(6.0)
    assert dups == {12: ["6B029ADD", "DEADBEEF"]}, (
        "an id scan cannot see a duplicate; two UNIQUE_ID payloads on one id can\n"
        + bus.explain(12))
    with pytest.raises(AmbiguousDeviceError):
        bus.controller(12)
    assert len(bus.controllers_at(12)) == 2


def test_write_param_round_trips_and_changes_the_cadence_on_the_wire(sim, clock):
    """The hinge of the design: config state and wire behaviour are one variable."""
    bus = sim([factory(12)])
    adm = attach(bus)
    dev = bus.controller(12)
    assert dev.ram[P159] == 250

    r = adm.write_param(12, P159, 20)
    assert r["result"] == 0 and r["result_text"] == "Success"
    assert r["param_id"] == P159 and r["value"] == 20 and r["type"] == "Uint"
    assert dev.ram[P159] == 20, "RAM took the requested value"
    assert dev.write_log[-1].requested == dev.write_log[-1].echoed == 20

    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0) == 20.0, (
        "a write that commits must be readable as a cadence change\n"
        + bus.explain(12))
    assert clock.now < 5.0


def test_a_write_that_only_echoes_leaves_the_cadence_alone(sim):
    """CD 456184's shape: a perfect response over a device that committed nothing."""
    bus = sim([factory(12, behaviour=SparkBehaviour(ignore_writes_for={P159}))])
    adm = attach(bus)
    dev = bus.controller(12)

    r = adm.write_param(12, P159, 20)
    assert r["result"] == 0 and r["value"] == 20, "the bus reported a clean success"
    assert dev.ram[P159] == 250, "and the device kept the old value"
    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0) == 250.0


def test_a_zeroed_echo_hides_the_value_the_device_actually_took(sim):
    """CD 456184: RAM takes the requested value, the reply carries something else.

    write_param() never compares the echoed d[2:6] against raw_u32, so the driver
    reports the device's number back to the caller as if it were the outcome.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(echo_value=0))])
    adm = attach(bus)
    dev = bus.controller(12)

    r = adm.write_param(12, P159, 20)
    assert r["result"] == 0, "Success"
    assert r["value"] == 0, "and a readback that is not the value we asked for"
    assert dev.ram[P159] == 20, "while RAM took the requested 20"
    assert (dev.write_log[-1].requested, dev.write_log[-1].echoed) == (20, 0)
    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0) == 20.0, (
        "the wire follows what the device committed, not what it echoed\n"
        + bus.explain(12))


def test_an_echo_transform_can_corrupt_one_field(sim):
    bus = sim([spark(12, behaviour=SparkBehaviour(
        echo_transform=lambda pid, v: v >> 1))])
    r = attach(bus).write_param(12, P159, 20)
    assert (r["value"], bus.controller(12).ram[P159]) == (10, 20)


def test_an_in_flight_ram_commit_is_not_visible_yet(sim, clock):
    """apply_delay_s: the response landed, the commit had not."""
    bus = sim([factory(12, behaviour=SparkBehaviour(apply_delay_s=0.05))])
    adm = attach(bus)
    dev = bus.controller(12)
    assert adm.write_param(12, P159, 20)["result"] == 0
    assert dev.ram[P159] == 250, "the write is answered but not yet applied"
    assert dev.write_log[-1].commit_at == pytest.approx(clock.now + 0.05)
    bus.measured_period_ms(12, S1, 0.2)
    assert dev.ram[P159] == 20, "and it lands a moment later"


def test_a_write_nothing_ever_answers_returns_none_after_its_retries(sim, clock):
    """One dropped response is recovered by the retry loop (CD 456184), so a
    device that never answers has to drop every attempt. Each attempt spends
    the full wait, and the wait itself is longer than the inter-write pacing,
    so no extra delay is added between them.
    """
    bus = sim([spark(12, behaviour=SparkBehaviour(drop_write_responses=99))])
    adm = attach(bus)
    assert adm.write_param(12, P159, 20, wait=1.5) is None
    assert clock.now == pytest.approx(sa.WRITE_ATTEMPTS * 1.5, abs=1e-6), (
        "the driver burned its whole wait window on a device that never answered")


# -- S6/S7: the scheduler contract SparkAdmin depends on -----------------------

def test_nonblocking_recv_returns_none_and_does_not_move_the_clock(rig_flex, clock):
    assert rig_flex.recv(timeout=0) is None
    assert clock.now == 0.0
    attach(rig_flex)._drain()
    assert clock.now == 0.0, "_drain() must not consume the reply that is not due yet"


def test_drain_terminates_in_backlog_mode_and_discards_telemetry(sim, clock):
    bus = sim([spark(12)], nonblocking_drain="backlog")
    adm = attach(bus)
    clock.sleep(0.1)
    adm._drain()
    assert clock.now == pytest.approx(0.1)
    assert bus.frames(12, api=S0), "a real socketcan buffer holds what arrived"
    assert bus.recv(timeout=0) is None, "and the drain emptied it"


def test_recv_advances_to_exactly_the_deadline_and_no_further(sim, clock):
    bus = sim([])
    assert bus.recv(timeout=0.25) is None
    assert clock.now == pytest.approx(0.25, abs=1e-9)
    t = clock.now
    bus.recv(timeout=1.0)
    assert clock.now <= t + 1.0 + 1e-9


def test_a_blocking_recv_never_overshoots_a_frame_deadline(rig_flex, clock):
    m = rig_flex.recv(timeout=1.0)
    assert m is not None and m.timestamp == clock.now
    assert clock.now < 1.0, "it returned at the frame, not at the deadline"


def test_awaiting_deadline_terminates_even_with_no_traffic(sim, clock):
    """SparkAdmin._await spins on time.time(); a recv that does not move the clock
    would hang the suite forever."""
    bus = sim([])
    adm = attach(bus)
    assert adm._await(F.PARAM_WRITE_RESP | 12, 0.5) is None
    assert clock.now >= 0.5


# -- S8: determinism -----------------------------------------------------------

def _congested_run(seed):
    clock = VirtualClock()
    bus = SparkBusSim(build_fleet(), clock, congestion=0.35, congestion_seed=seed)
    end = clock.now + 2.0
    while clock.now < end:
        bus.recv(timeout=max(0.0, end - clock.now))
    return bus


def test_congestion_is_reproducible_from_the_seed():
    a, b = _congested_run(7), _congested_run(7)
    assert a.dropped == b.dropped, "same seed, same fleet, same drop set"
    assert len(a.dropped) > 100, "35% of a busy bus is a lot of frames"
    assert all(why == "congestion" for _, _, why in a.dropped)
    c = _congested_run(8)
    assert c.dropped != a.dropped, "a different seed must produce a different set"


def test_congestion_inflates_the_period_the_driver_measures(sim):
    bus = sim([spark(12)], congestion=0.5, congestion_seed=3)
    period = attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=4.0)
    assert period > 20.0, "half the frames never arrived"
    assert bus.dropped, bus.explain(12)


# -- S9: persist settling, the ground truth no frame carries -------------------

def test_persist_inside_the_settling_window_flashes_the_pre_write_value(sim):
    bus = sim([factory(12, behaviour=SparkBehaviour(persist_settle_s=0.200))])
    adm = attach(bus)
    dev = bus.controller(12)

    assert adm.write_param(12, P159, 20)["result"] == 0
    # settle=0 deliberately: this is the simulator's contract, not the driver's.
    # The driver holds the CD 432129 floor, so it can no longer land inside the
    # window that this test exists to prove the simulator models.
    assert adm.persist(12, settle=0) == 0, "PERSIST_PARAMETERS reported Success"

    gap = dev.persist_log[-1].at - dev.write_log[-1].response_at
    assert gap < 0.200
    assert dev.persist_log[-1].stale == (P159,), bus.explain(12)
    assert dev.flash[P159] == 250, (
        f"flash took the pre-write value while the bus reported Success twice; the "
        f"persist landed {gap * 1e3:.0f} ms after the write response (CD 432129)")

    bus.power_cycle(12)
    assert dev.ram[P159] == 250, "the reboot loaded the stale flash value"
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "reverted"


def test_a_settled_persist_commits_the_new_value(sim):
    """The control. Without it, a bug that staled every persist would pass above."""
    bus = sim([factory(12, behaviour=SparkBehaviour(persist_settle_s=0.0))])
    adm = attach(bus)
    dev = bus.controller(12)

    adm.write_param(12, P159, 20)
    assert adm.persist(12) == 0
    assert dev.persist_log[-1].stale == ()
    assert dev.flash[P159] == 20

    bus.power_cycle(12)
    assert dev.ram[P159] == 20
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "ok"


def test_a_persist_with_the_wrong_magic_does_nothing(sim):
    bus = sim([spark(12)])
    bus.send(SimMessage(F.PERSIST | 12, b"\x00\x00"))
    assert bus.controller(12).persist_log == []
    assert ("bad magic" in [why for _, _, why in bus.ignored])


# -- S10: a dropout is a gap, not a slower cadence -----------------------------

def test_a_silence_window_has_the_endpoints_it_was_given(sim):
    bus = sim([spark(12)])
    bus.silence(12, 1.0, 2.0)
    adm = attach(bus)
    period = adm.status_period_ms(12, sa.STATUS_1_API, seconds=3.0)

    stamps = [t for t, _ in bus.frames(12, api=S1)]
    before = [t for t in stamps if t < 1.0]
    after = [t for t in stamps if t >= 2.0]
    assert before and after
    assert max(before) == pytest.approx(0.981, abs=1e-6)
    assert min(after) == pytest.approx(2.001, abs=1e-6)
    assert not [t for t in stamps if 1.0 <= t < 2.0], "the window was silent"

    resumed = sa.decode_status_1(bus.frames(12, api=S1, since=2.0)[0][1].data)
    assert resumed["sticky_warnings"] == [], (
        "a dropout is not a reboot: nothing sets hasReset")
    assert 20.0 < period < 40.0, (
        f"the driver reads a dropout as a slower average ({period} ms), which is "
        "the reading that becomes 'its config has reverted'")


def test_a_power_cycle_sets_has_reset_and_reloads_flash(sim):
    bus = sim([spark(12)])
    dev = bus.controller(12)
    dev.ram[P159] = 250                       # a RAM-only change, never persisted
    bus.power_cycle(12, offline_s=0.4)
    assert dev.ram[P159] == 20, "flash won"
    assert F.WARN["hasReset"] & dev.sticky_warnings

    adm = attach(bus)
    adm.status_period_ms(12, sa.STATUS_1_API, seconds=1.0)
    gap = [t for t, _ in bus.frames(12, api=S1)]
    assert min(gap) >= 0.4, "the controller was offline while it rebooted"
    assert sa.decode_status_1(bus.frames(12, api=S1)[0][1].data)["sticky_warnings"]\
        == ["hasReset"]


def test_silent_until_cleared_is_revived_by_clear_faults(sim, clock):
    """The confirmed rig-flex phenomenon: after a rail cycle all eight were silent
    and one CLEAR_FAULTS per id brought them back."""
    bus = sim(build_fleet())
    adm = attach(bus)
    for d in range(10, 18):
        bus.silent_until_cleared(d)
    assert adm.inventory(1.0) == {}, "a silent bus looks like an absent bus"

    adm.clear_faults(range(10, 18))
    assert [s for _, s in clock.sleeps] == [0.05] * 8, "one frame per id, paced"
    inv = adm.inventory(1.0)
    assert sorted(inv) == list(range(10, 18)), (
        "CLEAR_FAULTS revived every controller\n" + bus.explain())


def test_a_brownout_latches_a_sticky_warning_that_outlives_it(sim):
    bus = sim([spark(12)])
    bus.brownout(12, at=1.0, volts=5.8, duration=0.25)
    attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=2.0)

    during = sa.decode_status_0(bus.frames(12, api=S0, since=1.05, until=1.2)[0][1].data)
    after0 = sa.decode_status_0(bus.frames(12, api=S0, since=1.5)[0][1].data)
    after1 = sa.decode_status_1(bus.frames(12, api=S1, since=1.5)[0][1].data)
    assert during["voltage_v"] == pytest.approx(5.8, abs=0.02)
    assert after0["voltage_v"] == pytest.approx(12.6, abs=0.02), "the rail came back"
    assert after1["warnings"] == [], "and the active warning went with it"
    assert after1["sticky_warnings"] == ["brownout"], (
        "the sticky bit is the only evidence left that it ever happened")


def test_thermal_foldback_recovers_and_leaves_nothing_sticky(sim):
    bus = sim([spark(12)])
    bus.thermal_foldback(12, at=1.0, temp_c=95, duration=0.5)
    attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=2.0)

    during = sa.decode_status_1(bus.frames(12, api=S1, since=1.05, until=1.4)[0][1].data)
    after = sa.decode_status_1(bus.frames(12, api=S1, since=1.6)[0][1].data)
    assert during["faults"] == ["temperature"]
    assert sa.decode_status_0(
        bus.frames(12, api=S0, since=1.05)[0][1].data)["motor_temp_c"] == 95
    assert after["faults"] == [] and after["sticky_faults"] == [], (
        "a foldback that recovered leaves no sticky evidence, so an audit run "
        "afterwards cannot see it at all")


def test_a_chain_segment_lost_at_once(sim):
    bus = sim(build_fleet())
    for d in (14, 15, 16, 17):
        bus.bus_off(d)
    inv = attach(bus).inventory(1.0)
    assert sorted(inv) == [10, 11, 12, 13], (
        "one broken link takes everything downstream of it\n" + bus.explain())
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX))
    assert len(problems) == 1, (
        f"one break reported as {len(problems)} independent failures: {problems}")
    assert "14-17" in problems[0] and "tail of the chain" in problems[0], problems[0]


def test_revert_to_defaults_puts_the_cadence_back_to_the_factory_value(sim):
    bus = sim([spark(12)])
    adm = attach(bus)
    bus.revert_to_defaults(12)
    assert bus.controller(12).ram.get(P159) is None
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "reverted"


def test_revert_to_id_zero_moves_the_traffic_off_the_expected_id(sim):
    bus = sim([spark(12)])
    bus.revert_to_id_zero(12)
    inv = attach(bus).inventory(2.0)
    assert sorted(inv) == [0], "the expected id went silent; id 0 appeared"
    assert inv[0]["serial"] == "6B029ADD"


# -- S11: SET_CAN_ID names every rejection -------------------------------------

def test_set_can_id_moves_exactly_the_addressed_controller(sim, clock):
    bus = sim(build_fleet())
    dev = bus.controller(12)
    attach(bus).set_can_id(12, "6B029ADD", 20)
    assert dev.dev == 20 and dev.ram[F.PARAM_CAN_ID] == 20
    assert dev.flash[F.PARAM_CAN_ID] == 20
    assert dev.can_id_log[-1].accepted and dev.can_id_log[-1].reason == "accepted"
    assert clock.now >= 1.5, (
        "the driver's settle is virtual; it now also verifies who is "
        "broadcasting and burns the id, so the clock runs past the settle")


def test_a_ram_only_id_change_is_gone_after_the_power_cycle(sim):
    """The simulator's contract, exercised on the wire rather than through the
    driver. SparkAdmin.set_can_id now follows the move with a persist precisely
    so this outcome cannot happen, so reproducing CD 391976 means sending the
    frame directly -- which is what any other tool on the bus would do.
    """
    import can
    from sparklib import admin as sa

    bus = sim([spark(12, behaviour=SparkBehaviour(set_can_id_ram_only=True))])
    dev = bus.controller(12)
    bus.send(can.Message(arbitration_id=sa.SET_CAN_ID | 12,
                         data=bytes.fromhex("6B029ADD") + bytes([20]),
                         is_extended_id=True))
    bus.clock.advance(1.0)
    assert dev.dev == 20, "the bus says the move worked"
    assert dev.flash[F.PARAM_CAN_ID] == 12, "but flash never took it"
    bus.power_cycle(dev)
    assert dev.dev == 12, "and the next power cycle undoes it (CD 391976)"


@pytest.mark.parametrize("current, serial, reason", [
    (12, "DD9A026B", "serial mismatch"),
    (0, "6B029ADD", "broadcast id"),
])
def test_set_can_id_rejections_name_themselves(sim, current, serial, reason):
    bus = sim(build_fleet())
    dev = bus.controller(12)
    attach(bus).set_can_id(current, serial, 20)
    assert dev.dev == 12, "no state changed"
    assert reason in [why for _, _, why in bus.ignored], bus.explain()
    assert any(r.reason == reason and not r.accepted for r in dev.can_id_log)


def test_set_can_id_aimed_at_another_id_never_reaches_this_controller(sim):
    bus = sim(build_fleet())
    twelve, thirteen = bus.controller(12), bus.controller(13)
    attach(bus).set_can_id(13, "6B029ADD", 20)
    assert twelve.dev == 12 and twelve.can_id_log == [], "it never heard the frame"
    assert thirteen.dev == 13
    assert thirteen.can_id_log[-1].reason == "serial mismatch"


def test_identify_is_addressed_by_serial_not_by_id(sim):
    bus = sim(build_fleet())
    attach(bus).identify("6B029ADD")
    assert bus.controller(12).identify_count == 1
    assert sum(c.identify_count for c in bus.controllers) == 1


# -- S12: the invariants the bus enforces --------------------------------------

def test_the_bootloader_frame_is_refused(rig_flex):
    with pytest.raises(BootloaderFrameForbidden):
        rig_flex.send(SimMessage(F.ENTER_SWDL_CAN_BOOTLOADER, b""))


@pytest.mark.parametrize("base", F.SETPOINT_BASES)
def test_every_setpoint_frame_is_refused(rig_flex, base):
    with pytest.raises(SetpointFrameForbidden):
        rig_flex.send(SimMessage(base | 12, bytes(8)))


def test_a_full_admin_session_sends_no_setpoint(rig_flex):
    adm = attach(rig_flex)
    adm.inventory(1.0)
    adm.firmware(12)
    adm.write_param(12, P159, 20)
    adm.persist(12)
    adm.clear_faults([12])
    assert_no_setpoints(rig_flex)


def test_a_protected_parameter_write_is_refused_by_the_driver(rig_flex):
    adm = attach(rig_flex)
    with pytest.raises(sa.ProtectedParameterError):
        adm.write_param(12, 52, 0)
    assert rig_flex.sent == [], "the frame never reached the bus"


def test_a_protected_write_that_reached_the_bus_is_an_invariant_failure(sim):
    bus = sim([spark(12)], forbid_protected_writes=True)
    with pytest.raises(ProtectedWriteReachedBus):
        bus.send(SimMessage(F.PARAM_WRITE | 12, F.encode_param_write(52, 0)))


def test_the_frame_budget_stops_a_runaway_window(sim):
    bus = sim(build_fleet(), max_frames=500)
    with pytest.raises(SimFrameBudgetExceeded):
        attach(bus).inventory(5.0)


# -- S13: no real time anywhere in the package ---------------------------------

def test_no_sparksim_module_touches_the_real_clock():
    """An AST walk, not a grep: `from time import sleep` must not slip past."""
    offenders = []
    for path in sorted(SPARKSIM_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [f"{path.name}:{node.lineno} import {a.name}"
                              for a in node.names if a.name.split(".")[0] == "time"]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "time":
                    offenders.append(f"{path.name}:{node.lineno} from time import")
            elif isinstance(node, ast.Attribute):
                if (isinstance(node.value, ast.Name) and node.value.id == "time"
                        and node.attr in ("time", "sleep", "monotonic",
                                          "perf_counter")):
                    offenders.append(f"{path.name}:{node.lineno} time.{node.attr}")
    assert not offenders, (
        "sparksim must never cost real time; found " + ", ".join(offenders))


def test_the_package_entry_point_is_the_same_simulator():
    """tests/adversarial/simulator.py re-exports sparksim rather than restating it:
    two implementations of one bus would mean the tests could pass against the
    copy the suite does not use."""
    from adversarial import simulator

    assert simulator.SparkBusSim is SparkBusSim
    assert simulator.spark is spark and simulator.attach is attach
    assert simulator.F is F


def test_the_clock_is_monotonic_and_records_every_sleep():
    c = VirtualClock()
    c.sleep(0.05)
    c.advance_to(0.01)
    assert c.now == pytest.approx(0.05), "advance_to backwards is a no-op"
    c.advance(0.2)
    assert c.sleeps == [(0.0, 0.05)]
    assert c.time() == c.monotonic() == c.perf_counter() == c.now


# -- ground truth the frames do not carry --------------------------------------

def test_clear_faults_cannot_tell_a_latch_from_a_transient(sim):
    """D5: fire-and-forget. The clear_log is the only place the difference lives."""
    latched = SparkBehaviour(latched_sticky_faults=F.FAULT["gateDriver"])
    bus = sim([spark(12, behaviour=latched), spark(13)])
    for c in bus.controllers:
        c.sticky_faults = F.FAULT["gateDriver"]
    adm = attach(bus)
    adm.clear_faults([12, 13])

    assert bus.controller(12).sticky_faults == F.FAULT["gateDriver"], "still latched"
    assert bus.controller(13).sticky_faults == 0, "the transient cleared"
    assert [r.after[2] for r in bus.controller(12).clear_log] == [F.FAULT["gateDriver"]]
    status = sa.collect_status(bus, seconds=0.5)
    assert status[12]["status1"]["sticky_faults"] == ["gateDriver"]
    assert status[13]["status1"]["sticky_faults"] == []


def test_a_fault_raised_mid_window_appears_in_the_next_frame(sim):
    bus = sim([spark(12)])
    bus.set_fault(12, faults=["gateDriver"], at=1.0)
    adm = attach(bus)
    adm.status_period_ms(12, sa.STATUS_1_API, seconds=2.0)
    before = sa.decode_status_1(bus.frames(12, api=S1, until=0.9)[-1][1].data)
    after = sa.decode_status_1(bus.frames(12, api=S1, since=1.1)[0][1].data)
    assert before["faults"] == [] and after["faults"] == ["gateDriver"]
    assert after["sticky_faults"] == ["gateDriver"]


def test_the_schedule_escape_hatch_runs_at_its_virtual_time(sim, clock):
    bus = sim([spark(12)])
    seen = []
    bus.schedule(1.5, lambda s: seen.append(s.clock.now))
    attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=2.0)
    assert seen == [pytest.approx(1.5)]


def test_a_duplicate_id_answers_a_request_twice(sim):
    """CD 495329: one PARAM_WRITE, two responses, and _await takes the first."""
    a = spark(12, "6B029ADD")
    b = spark(12, "DEADBEEF", behaviour=SparkBehaviour(write_result=3))
    bus = sim([a, b])
    r = attach(bus).write_param(12, P159, 20)
    assert r["result"] == 0, "the driver saw the first answer and stopped listening"
    assert b.write_log and b.write_log[-1].result == 3, (
        "the second controller took the write too and refused it, unseen\n"
        + bus.explain())


def test_a_foreign_device_type_on_a_configured_id_is_not_a_spark(sim):
    pdh = SimSpark(dev=10, serial="0BADCAFE", device_type=F.DEVICE_TYPE_PDH)
    bus = sim([spark(11), pdh])
    inv = attach(bus).inventory(2.0)
    assert 10 not in inv, (
        "device type 8 is a Power Distribution Hub; enumerating it as a motor "
        "controller is what the manufacturer-only filter used to do")
    assert F.split_arb(bus.frames(10)[0][1].arbitration_id).device_type == 8
    assert attach(bus).firmware(10) == (None, None), (
        "and a SPARK-addressed request does not reach it")
