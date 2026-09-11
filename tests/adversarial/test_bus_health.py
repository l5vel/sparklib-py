"""Bus health versus config drift: catalogue class C, driver defect D4.

`status_1_verdict()` is handed one number -- the mean STATUS_1 period over the
sample window -- and from it decides "ok", "reverted" or "absent". Four physically
different events produce the same number:

    a controller really running REV's 250 ms default       (config drift)
    a bus at 90% loss                                       (CD 455329, C1)
    a 3.7 s dropout inside a 4 s window                     (CD 480555, C2)
    a controller that left the bus 0.3 s in                 (C2 / C5)

Only the first is a config problem, and only the first is fixed by re-provisioning
and a flash cycle. The other three are answered by a terminator, a connector, or
an RMA. The evidence that separates them is already in the frames the driver's own
recv loop consumed -- the STATUS_0 cadence measured beside STATUS_1, the largest
inter-arrival gap, the coverage against nominal, and whether the loss is one
controller or all eight -- and `inventory()` already collects the first of those
into the very dict the verdict is read from.

The passing tests here establish that the discriminators are present and that the
driver's structural filters (mfr, disabled frames) work. The xfails are D4: every
one asserts the conclusion an operator needs, over a bus where the mean alone is a
lie.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from sparklib import admin as sa
from sparksim import (ROLES_FLEX, SERIALS_FLEX, SimSpark, attach, build_fleet,
                      factory, spark)
from sparksim import frames as F

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
P159 = F.PARAM_STATUS_1_PERIOD
ROLES = dict(ROLES_FLEX)
SERIALS = dict(SERIALS_FLEX)


def _gaps_ms(bus, dev, api, since=0.0):
    """Inter-arrival gaps in ms over exactly the frames recv() handed the driver.

    `bus.delivered` is appended to only when recv returns a frame, so this is the
    driver's own input stream, not privileged simulator state.
    """
    ts = [t for t, _ in bus.frames(dev, api=api, since=since)]
    return [round((b - a) * 1000.0, 1) for a, b in zip(ts, ts[1:])]


def _largest_gap_ms(bus, dev, api, since=0.0):
    g = _gaps_ms(bus, dev, api, since)
    return max(g) if g else None


def _blames_the_config(problems):
    return [p for p in problems if "factory default" in p]


# -- the controls: what the driver already gets right --------------------------

def test_a_real_config_revert_holds_a_steady_250_ms_cadence(sim):
    """Catalogue A1 -- a controller that lost its Appendix A config and is running
    REV's factory Status 1 Period. https://www.chiefdelphi.com/t/405541

    The only one of the four 250 ms readings that is genuinely a config problem.
    Its signature is that nothing else about the bus is disturbed: STATUS_0 keeps
    its own nominal cadence and no gap exceeds one STATUS_1 period.
    """
    bus = sim([factory(12)])
    adm = attach(bus)

    inv = adm.inventory(4.0)
    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) == "reverted"
    assert bus.controller(12).flash[P159] == sa.REV_DEFAULT_STATUS_1_PERIOD_MS, (
        "the device really is provisioned to the factory value")

    assert inv[12]["periods_ms"][S0] == 10.0, "STATUS_0 was untouched"
    assert _largest_gap_ms(bus, 12, S1) == pytest.approx(250.0, abs=1.0), (
        "a reverted controller is talking constantly, just more slowly\n"
        + bus.explain(12))


def test_a_disabled_status_1_reads_absent_and_not_reverted(sim):
    """Catalogue C3: reading a deliberately disabled status frame produces
    timeouts, not a slow frame. https://www.chiefdelphi.com/t/432129

    The audit has to separate "this controller cannot report faults at all" from
    "this controller reports them slowly" -- the first means every fault finding
    for that id is vacuous.
    """
    bus = sim(build_fleet())
    bus.disable_frame(12, S1)
    adm = attach(bus)

    inv = adm.inventory(4.0)
    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)

    assert S1 not in inv[12]["periods_ms"]
    assert inv[12]["periods_ms"][S0] == 10.0, "the controller is present and healthy"
    assert [p for p in problems if "id 12" in p and "no STATUS_1" in p], problems
    assert not _blames_the_config(problems), (
        "a silent frame is not a slow frame; the two have opposite remedies")


def test_ctre_traffic_on_a_configured_id_is_not_counted_as_a_spark(sim):
    """Catalogue C4: "CANSparkMax object created for CAN ID 3, which is not a
    SPARK MAX". https://www.chiefdelphi.com/t/460286

    A CTRE device sharing the id space broadcasts on the same api/id bit layout;
    only the manufacturer field separates it. The driver filters on it, so the
    foreign traffic neither invents a controller nor perturbs one.
    """
    bus = sim([spark(11), spark(12, "0BADCAFE", mfr=4)])
    inv = attach(bus).inventory(3.0)

    assert bus.frames(12), "the foreign device was broadcasting the whole window"
    assert sorted(inv) == [11], (
        "id 12 belongs to a device from another manufacturer\n" + bus.explain())
    assert sa.audit_problems(inv, {}, {11: "steer/RB"}, {11: SERIALS[11]}) == []


def test_a_dropout_and_a_revert_are_separable_by_their_largest_gap(sim):
    """Catalogue C2, intermittent dropoff: "status frames stop entirely for
    50-500 ms then resume with hasReset NOT set".
    https://www.chiefdelphi.com/t/480555

    The discriminator D4 is missing, measured over the driver's own recv stream:
    a 3.68 s dropout and a genuine factory revert deliver the SAME frame count and
    the SAME mean period, and differ by a factor of nearly fifteen in the largest
    inter-arrival gap. Nothing privileged is read here -- both numbers come from
    frames status_period_ms() already consumed and threw away.

    Also reported at https://www.chiefdelphi.com/t/380379
    """
    dropped = sim([spark(12)])
    dropped.silence(12, 0.16, 3.84, apis=[S1])
    dropout_mean = attach(dropped).status_period_ms(12, S1, seconds=4.0)

    reverted = sim([factory(13)])
    revert_mean = attach(reverted).status_period_ms(13, S1, seconds=4.0)

    assert dropout_mean == revert_mean, (
        "the two events are indistinguishable by the only number the verdict sees")
    assert len(_gaps_ms(dropped, 12, S1)) == len(_gaps_ms(reverted, 13, S1)), (
        "and by the frame count that number was derived from")

    assert _largest_gap_ms(reverted, 13, S1) == pytest.approx(250.0, abs=1.0)
    assert _largest_gap_ms(dropped, 12, S1) > 3000.0, (
        "the dropout is one enormous hole; the revert is an even cadence\n"
        + dropped.explain(12))

    resumed = sa.decode_status_1(dropped.frames(12, api=S1, since=3.84)[0][1].data)
    assert resumed["sticky_warnings"] == [], (
        "it went deaf, it did not reboot -- so nothing volatile was lost either")


def test_congestion_inflates_status_0_alongside_status_1(sim):
    """Catalogue C1, CAN utilisation at 100%: "absent status frames from congestion
    look identical to status frames disabled by config".
    https://www.chiefdelphi.com/t/455329

    Status 0 Period and Status 1 Period are two independent parameters, so a
    config event moves one and a bus event moves both. `inventory()` already
    measures both into one dict -- the coverage discriminator costs no extra
    traffic and no extra code, only a comparison the audit never makes.

    Also reported at https://www.chiefdelphi.com/t/408307
    """
    # full_status=False keeps this controller on Status 0 and 1 alone. The
    # premise is that a congested bus and a reverted config read the SAME on
    # STATUS_1; eight more broadcast frames change how much the congestion model
    # drops, so the two would coincide by luck rather than by the defect.
    congested = sim([spark(12, full_status=False)], congestion=0.90,
                    congestion_seed=1)
    lossy = attach(congested).inventory(5.0)[12]["periods_ms"]

    clean = sim([factory(13)])
    reverted = attach(clean).inventory(5.0)[13]["periods_ms"]

    assert sa.status_1_verdict(lossy[S1]) == sa.status_1_verdict(reverted[S1]), (
        "both read as the factory default on STATUS_1 alone")
    assert reverted[S0] == 10.0, "a reverted Status 1 Period leaves Status 0 alone"
    assert lossy[S0] > 5 * reverted[S0], (
        "on the congested bus every frame class thinned out together\n"
        + congested.explain(12))
    assert congested.dropped and not clean.dropped


# -- D4: the mean is not enough ------------------------------------------------

@pytest.mark.xfail(reason="D4: status_1_verdict() now ACCEPTS evidence "
                          "(max_gap_ms, last_seen_ms, window_ms), but this path "
                          "reaches it with a bare mean, so a saturated bus still "
                          "reads as a reverted config. The same case passes on "
                          "hardware, where inventory() supplies the evidence.",
                   strict=False)
def test_a_saturated_bus_is_not_reported_as_a_reverted_controller(sim):
    """Catalogue C1: a third-party logging library drove CAN utilisation to 100%
    and every controller reported Timeout Waiting for Status X.
    https://www.chiefdelphi.com/t/455329

    Acting on the audit's answer here means a flash cycle on a controller whose
    flash was never wrong, while the bus stays saturated.
    """
    # full_status=False for the same reason as the congestion test above: the
    # defect is that a MEAN period cannot separate loss from reversion, and a
    # controller broadcasting eight more frame classes changes what the
    # congestion model drops. Passing on that would be luck, not a fix.
    bus = sim([spark(12, full_status=False)], congestion=0.90, congestion_seed=1)
    dev = bus.controller(12)
    inv = attach(bus).inventory(5.0)

    assert (dev.ram[P159], dev.flash[P159]) == (20, 20), (
        "ground truth: the provisioned value is intact in RAM and in flash")

    problems = sa.audit_problems(inv, {}, {12: "drive/RF"}, {12: SERIALS[12]})
    assert not _blames_the_config(problems), (
        f"{len(bus.dropped)} frames were lost in transit and the audit called it a "
        f"config revert: {problems}")


def test_an_intermittent_dropoff_is_not_reported_as_a_reverted_controller(sim):
    """Catalogue C2: "REV Spark Flex CAN Timeout. Periodic Status 2"; utilisation
    dips line up exactly with the timeouts, so the device really left the bus and
    came back. https://www.chiefdelphi.com/t/480555

    The remedy is a connector or a current limit (CD 480394 found the break under
    shrink wrap). Re-provisioning fixes nothing and hides the intermittency for
    another match.
    """
    bus = sim([spark(12)])
    bus.silence(12, 0.16, 3.84, apis=[S1])
    dev = bus.controller(12)
    inv = attach(bus).inventory(4.0)

    assert dev.ram[P159] == 20, "ground truth: the cadence config never changed"
    assert _largest_gap_ms(bus, 12, S1) > 3000.0, "and the wire shows one long hole"

    problems = sa.audit_problems(inv, {}, {12: "drive/RF"}, {12: SERIALS[12]})
    assert not _blames_the_config(problems), problems
    assert any("12" in p and ("gap" in p.lower() or "dropout" in p.lower()
                              or "intermittent" in p.lower()) for p in problems), (
        f"a controller that vanished for 3.7 s audited as a config change: {problems}")


def test_a_controller_that_left_the_bus_is_not_reported_as_reverted(sim):
    """Catalogue C2 / C5: a broken link takes the node off the bus permanently --
    TEC climbs past 255 and it goes bus-off. https://www.chiefdelphi.com/t/480394

    Distinct from a revert in that every api stops together and none resumes. The
    audit must not send someone to RHC2 to re-provision a controller that is not
    electrically on the bus any more.
    """
    bus = sim(build_fleet())
    bus.bus_off(12, at=0.32)
    inv = attach(bus).inventory(4.0)

    assert not bus.frames(12, since=0.35), "ground truth: it never spoke again"
    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) == "reverted", (
        "the trap: 0.32 s of a 4 s window averages to REV's default")

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)
    assert not _blames_the_config(problems), problems
    hits = [p for p in problems if "12" in p
            and ("stopped" in p.lower() or "silent" in p.lower()
                 or "left the bus" in p.lower())]
    assert hits, (
        f"drive/RF went bus-off and the audit reported a parameter: {problems}")
    assert not any("dropout" in p.lower() or "intermittent" in p.lower()
                   for p in hits), (
        "it never resumed, so calling it intermittent sends the operator "
        f"hunting a loose connector on a node that is off the bus: {hits}")


def test_the_whole_fleet_losing_status_1_at_once_is_reported_as_one_bus_fault(sim):
    """Catalogue C1 and C7: termination, a shorted trunk or 100% utilisation take
    every controller at once. https://www.chiefdelphi.com/t/455329

    Eight controllers do not independently revert to factory defaults in the same
    second. The simultaneity IS the diagnosis, and it is the one thing a
    per-controller verdict structurally cannot express.

    Also reported at https://www.chiefdelphi.com/t/402177
    """
    bus = sim(build_fleet(), congestion=0.90, congestion_seed=3)
    inv = attach(bus).inventory(5.0)

    assert all(c.ram[P159] == 20 for c in bus.controllers), (
        "ground truth: every one of the eight is still provisioned")
    assert len(bus.dropped) > 1000, "and the bus ate most of the traffic"

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)
    fabricated = _blames_the_config(problems) + [p for p in problems if "swapped" in p]
    assert not fabricated, (
        f"a saturated bus fabricated {len(fabricated)} per-controller findings out of "
        f"frames that never arrived -- reversions and swapped controllers: {fabricated}")
    assert any("bus" in p.lower() or "utilisation" in p.lower()
               or "termination" in p.lower() for p in problems), (
        f"every controller on the bus thinned out together and the audit said "
        f"{problems or 'nothing'}\n" + bus.explain())


def test_a_period_that_is_neither_provisioned_nor_factory_is_reported(sim):
    """Catalogue A1: a write that reported Success and left a different value
    behind. https://www.chiefdelphi.com/t/456184

    120 ms is not Appendix A's 20 and not REV's 250, so it is drift no reset and
    no provisioning run produced -- exactly the state worth escalating. The
    verdict names it 'unexpected' and the audit then says nothing at all, which is
    worse than either wrong answer.

    Also reported at https://www.chiefdelphi.com/t/389553
    Also reported at https://www.chiefdelphi.com/t/515934
    """
    bus = sim([spark(12, s1_ms=120)])
    inv = attach(bus).inventory(4.0)

    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) == "unexpected", (
        "the verdict itself did classify it")
    problems = sa.audit_problems(inv, {}, {12: "drive/RF"}, {12: SERIALS[12]})
    assert any("12" in p for p in problems), (
        "a controller broadcasting at a period nothing provisioned audited clean")


def test_a_cadence_at_more_than_twice_appendix_a_is_not_ok(sim):
    """Catalogue, protocol facts: REVLib's periodic-status timeout floor is 2.1x
    the frame period, so 45 ms on a 20 ms frame is already outside what anything
    downstream tolerates. https://www.chiefdelphi.com/t/405541

    A fixed +/-30 ms band around 20 ms accepts everything up to 49 ms as healthy.
    The band has to scale with the period it is judging.
    """
    bus = sim([spark(12, s1_ms=45)])
    inv = attach(bus).inventory(4.0)
    dev = bus.controller(12)

    assert dev.ram[P159] == 45, "ground truth: 2.25x the provisioned period"
    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) != "ok", (
        "a fault report arriving at less than half the provisioned rate passed as "
        "healthy\n" + bus.explain(12))


def test_a_rev_pdh_on_the_bus_is_not_audited_as_a_spark(sim):
    """Catalogue C4, the same class as "CAN ID 3, which is not a SPARK MAX": the
    device answering is REV's but is not a motor controller.
    https://www.chiefdelphi.com/t/460286

    A Power Distribution Hub sits at id 1 by default and shares the manufacturer
    field. devtype = (arb >> 24) & 0x1F is 2 for a motor controller and 8 for the
    PDH, and it is in every frame the driver already parsed.

    Also reported at https://www.chiefdelphi.com/t/462947
    Also reported at https://www.chiefdelphi.com/t/481557
    """
    pdh = SimSpark(dev=1, serial="0BADCAFE", device_type=F.DEVICE_TYPE_PDH)
    bus = sim(build_fleet() + [pdh])
    inv = attach(bus).inventory(3.0)

    assert F.split_arb(bus.frames(1)[0][1].arbitration_id).device_type == \
        F.DEVICE_TYPE_PDH, "the wire said what it was"
    assert 1 not in inv, (
        "a power distribution hub was enumerated as a motor controller")
    assert sa.audit_problems(inv, {}, ROLES, SERIALS) == [], (
        "and the whole provisioned eight audited dirty because of it")


def test_the_verdict_call_site_is_handed_more_than_a_mean():
    """The call-site guard for every xfail above.

    Widening `status_1_verdict()` fixes nothing while its only caller still hands
    it a single averaged number: the coverage, the largest gap and the fleet
    context have to cross that boundary or the discriminators cannot be reached
    from the audit at all.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(sa.audit_problems)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "status_1_verdict"]

    assert calls, "audit_problems no longer calls status_1_verdict; retarget this test"
    thin = [c for c in calls if len(c.args) + len(c.keywords) < 2]
    assert not thin, (
        f"{len(thin)} call(s) to status_1_verdict pass one averaged period; the "
        "evidence that separates a lossy bus from a lost config never reaches it")


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------
# Six more readings whose arithmetic is right and whose conclusion is wrong. The
# module above proves the discriminators exist on the wire; these are the shapes
# the audit still has no rule for -- a gap it never records, and a fleet-wide or
# structural failure emitted as one accusation per configured id.

def _baseline(devs=range(10, 18), firmware="26.1.6"):
    return {d: {"serial": SERIALS[d], "firmware": firmware, "role": ROLES[d],
                "status_1_period_ms": 20.0} for d in devs}


def test_a_multi_second_stall_is_recorded_as_a_gap_not_as_a_reverted_period(sim):
    """CD 458413 <https://www.chiefdelphi.com/t/458413>: one SPARK Flex's STATUS_1
    stalled 1-4 s at a time with the rest of the bus clean and under 60%
    utilisation; it survived swapping the controller and reflashing.

    The mean over the window is the wrong statistic. A 20 ms device that goes
    quiet for 3.6 s of a 4 s window averages to ~235 ms, lands inside the
    factory-default band, and the tool prescribes re-provisioning for a
    link-quality fault. The largest inter-arrival gap separates them, it is
    computable from the frames recv() already handed the driver, and the driver
    keeps neither the gap nor anything derived from it.
    """
    bus = sim([spark(10)])
    bus.silence(10, 0.17, 3.84)
    inv = attach(bus).inventory(4.0)

    mean_ms = inv[10]["periods_ms"][S1]
    assert sa.status_1_verdict(mean_ms) == "reverted", (
        f"fixture drifted: this stall is supposed to masquerade as the factory "
        f"default, got {mean_ms} ms\n" + bus.explain(10))
    assert _largest_gap_ms(bus, 10, S1) > 3000, "a 3.6 s silence is on the wire"

    assert inv[10]["max_gap_ms"][S1] > 3000, (
        "the silence is in the frames inventory() consumed and is not in what it "
        "returns, so no caller can tell this from a config revert")


def test_a_silent_bus_is_one_finding_and_not_one_per_configured_id(sim):
    """CD 450513 <https://www.chiefdelphi.com/t/450513>: an unterminated bus works
    below about seven or eight devices and every device fails at once above it.

    No REV traffic at all has exactly one likely cause and it is not eight
    simultaneously unplugged controllers. Today the output is byte-identical
    between "the bus is down" and "someone removed every motor", so the highest
    probability diagnosis -- one missing terminator, one unplugged trunk -- is
    the one the operator never sees.
    """
    inv = attach(sim([])).inventory(2.0)
    assert inv == {}, "nothing at all is on this bus"

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS, _baseline())

    assert len(problems) == 1 and "bus" in problems[0].lower(), (
        f"no REV traffic at all produced {len(problems)} per-device findings: "
        f"{problems}")


def test_a_lost_chain_segment_is_one_finding_and_not_four(sim):
    """CD 460286 <https://www.chiefdelphi.com/t/460286>: a solder joint under
    heatshrink shorted CANH to CANL mid-chain. Continuity tests passed, because
    both wires were also correctly joined; everything past the splice vanished
    from every scan at once.

    Four controllers that went away together, in daisy-chain order, are one
    wiring fault. Four separate lines send the operator to chase four
    controllers that are all fine.
    """
    bus = sim(build_fleet(range(10, 14)))
    inv = attach(bus).inventory(2.0)
    assert sorted(inv) == [10, 11, 12, 13], "the tail of the chain is gone"

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)

    assert len(problems) == 1, (
        f"one splice reported as {len(problems)} independent controller failures: "
        f"{problems}")
    assert any(w in problems[0].lower()
               for w in ("bus", "wiring", "break", "segment", "chain")), problems[0]


@pytest.mark.xfail(reason="audit_problems reads periods_ms[STATUS_1_API] only, so a "
                          "controller that stopped broadcasting STATUS_0 while "
                          "STATUS_1 keeps perfect time is reported as healthy",
                   strict=False)
def test_a_stopped_status_0_is_reported_while_status_1_keeps_perfect_time(sim):
    """CD 494346 <https://www.chiefdelphi.com/t/494346>: SPARK Flex 25.0.4 latching
    into "Timed out while waiting for Periodic Status 0" every one to two seconds,
    with no fault set anywhere and 20% bus load. Only a power cycle cleared it.

    STATUS_0 is the frame that carries the rail, the current and the limit
    switches, so losing it is losing the telemetry -- and `spark faults` prints
    nothing for that controller while the audit calls the bus clean.
    """
    bus = sim(build_fleet())
    bus.disable_frame(13, S0)
    inv = attach(bus).inventory(2.0)
    assert S0 not in inv[13]["periods_ms"] and S1 in inv[13]["periods_ms"], (
        "id 13 is silent on STATUS_0 and perfect on STATUS_1\n" + bus.explain(13))

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)

    assert any("13" in p and "STATUS_0" in p for p in problems), (
        f"id 13 stopped broadcasting its telemetry frame and the audit said: "
        f"{problems}")


def test_one_dead_controller_produces_one_finding_and_not_two(sim):
    """CD 473666 <https://www.chiefdelphi.com/t/473666>: a NEO 550 internal short
    killed two SPARK MAXes outright -- dead on CAN, dead on USB, recovery mode no
    help.

    One device, listed in both the role map and the baseline, is one fault. Two
    lines for one dead controller reads as two separate failures and inflates the
    count an operator triages by, which is the number `spark audit` prints first.
    """
    bus = sim(build_fleet([d for d in range(10, 18) if d != 11]))
    inv = attach(bus).inventory(2.0)

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS, _baseline())

    about_11 = [p for p in problems if "id 11" in p]
    assert len(about_11) == 1, (
        f"one dead controller, {len(about_11)} findings: {about_11}")


@pytest.mark.xfail(reason="inventory() counts every REV frame on an id into "
                          "periods_ms, including PARAM_WRITE traffic from another "
                          "host, and the audit has no rule that names the writer",
                   strict=False)
def test_another_host_writing_parameters_is_named_instead_of_a_reprovision_loop(sim):
    """CD 416428 <https://www.chiefdelphi.com/t/416428>: robot code re-applied
    factory defaults plus its own values at every boot -- a vendored swerve
    library called restoreFactoryDefaults() at construction -- so provisioning
    could never win and the smart current limit and idle mode kept coming back.

    The PARAM_WRITE frames are on the bus, addressed to the very id the audit is
    about to prescribe a re-provision for. "Re-provision it" loses to the next
    boot every time; the finding an operator can act on is that something else is
    writing to this controller.
    """
    writer = SimSpark(dev=10, serial="0BADCAFE",
                      ram={F.PARAM_STATUS_0_PERIOD: 0, F.PARAM_STATUS_1_PERIOD: 0},
                      periods_ms={UID: 0, 0x0E0: 20, 0x0E1: 20})
    bus = sim([factory(10), writer])
    inv = attach(bus).inventory(3.0)
    assert 0x0E0 in inv[10]["periods_ms"], (
        "the foreign write traffic is in the reading the audit is built on\n"
        + bus.explain(10))

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)

    assert any(w in p.lower() for p in problems
               for w in ("writer", "writing", "another host", "roborio")), (
        f"another host is rewriting this controller and the audit said: {problems}")
    assert not _blames_the_config(problems), (
        "re-provisioning a controller another host rewrites at every boot")
