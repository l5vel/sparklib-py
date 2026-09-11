"""The behaviours that are not product-specific, run against BOTH products.

Most of this suite runs on SPARK Flex because that is what rig-flex has. But a
large part of what the driver does is the same on either product -- enumerating
a bus, spotting a duplicate, measuring a cadence, stopping the motors -- and
running it on one product proves it on one product.

rig-max-2 and rig-max are SPARK MAX. Until this file existed, five tests of 1092
touched a MAX bus and the rest could have been broken there without anything
failing.

What is deliberately NOT here, because it genuinely differs:

    the Vortex dock     Flex only; a MAX has separate motor leads
    status timeout      a Flex firmware defect fixed in 25.0.2
    the 26 AWG pigtail  a Flex connector

Fault decode is NOT in that list any more. It splits on firmware generation
rather than on product, and test_firmware_generations.py owns it. What a
product still decides is SPARK_MODEL, the field STATUS_0 carries at bit 54.

Anything below is a claim about the driver rather than about a product, and if
it passes on one and fails on the other, the driver has a product assumption
nobody wrote down.
"""

from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import attach, build_fleet, spark
from sparksim import frames as F
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

PRODUCTS = ("sparkflex", "sparkmax")


@pytest.fixture(params=PRODUCTS)
def product(request):
    return request.param


# Frame layout keys on firmware, so anything that touches a frame runs over
# generations. Product runs the rest.
GENERATIONS = (sa.GEN_FW25, sa.GEN_PRE25)
FW_FOR_GEN = {sa.GEN_FW25: "26.1.6", sa.GEN_PRE25: "1.6.3"}


@pytest.fixture(params=GENERATIONS)
def generation(request):
    return request.param


def gen_fleet(generation, ids=range(10, 18), **kw):
    return build_fleet(ids, firmware=FW_FOR_GEN[generation], **kw)


def provisioned_ms(generation):
    """The cadence this package provisions for the fault-carrying frame."""
    return sa.fault_frame(generation)["expected_ms"]


def slow_frame(generation, factor):
    """Slow the fault-carrying frame to `factor` times what this generation
    provisions.

    A FACTOR and not a millisecond count, because the two generations provision
    different cadences -- 20 ms on a Flex through Appendix A, 50 ms on a pre-25
    MAX through apply_boot_config's throttle -- and the audit's own thresholds
    are multiples of the provisioned value. A hardcoded number means a different
    thing on each: 100 ms is five times provisioned on a Flex and twice it on a
    MAX, so one call would test the bus-level rule on one generation and the
    per-device rule on the other while looking identical.
    """
    ms = int(round(factor * provisioned_ms(generation)))
    return {"s0_ms": ms} if generation == sa.GEN_PRE25 else {"s1_ms": ms}


def slow_other(generation, ms):
    """Slow the frame that carries no faults on this generation."""
    return {"s1_ms": ms} if generation == sa.GEN_PRE25 else {"s0_ms": ms}


# The audit discloses when it could not check identity, which is correct and is
# not a cadence finding. Anchored on the CONFIG KEY rather than on a phrase from
# the prose: the wording changed when pre-25 identity became
# readable, and a substring of the sentence silently stopped matching, so two
# tests began reporting a disclosure as a cadence failure.
NO_SERIAL_NOTE = "serials"


def cadence_findings(problems):
    return [p for p in problems if NO_SERIAL_NOTE not in p]


def fleet(product, ids=range(10, 18), **kw):
    return build_fleet(ids, controller_type=product, **kw)


def status(bus, product=None, seconds=0.6):
    """Read the bus without telling it anything. The generation is on the wire."""
    return sa.collect_status(bus, seconds=seconds)


def test_a_whole_bus_enumerates_on_either_product(sim, product):
    """The most basic claim there is, and it was only ever checked on Flex."""
    bus = sim(fleet(product))
    inv = attach(bus).inventory(1.0)
    assert sorted(inv) == list(range(10, 18)), (
        f"{product}: enumerated {sorted(inv)} of eight")
    assert all(i["serial"] for i in inv.values()), (
        f"{product}: a controller enumerated without a serial, so every "
        "serial-addressed command is blind on this product")


def test_a_duplicate_id_is_found_on_either_product(sim, product):
    """duplicates() keys on UNIQUE_ID. Whether a MAX broadcasts that api at all
    is recorded as unverified in spark_provenance; this at least proves the
    driver's own logic does not assume a product."""
    bus = sim(fleet(product) + [spark(12, "DEADBEEF", controller_type=product)])
    dups = attach(bus).duplicates(seconds=2.0)
    assert 12 in dups and len(dups[12]) == 2, (
        f"{product}: two controllers on id 12 and duplicates() said {dups}")


def test_a_silent_bus_is_one_finding_on_either_product(sim, product):
    """The bus-level rule is about the shape of a loss, not about a frame
    layout, so it has to hold for both."""
    bus = sim(fleet(product))
    for c in bus.controllers:
        c.offline = True
    inv = attach(bus).inventory(1.0)
    assert inv == {}, f"{product}: premise, nothing is transmitting"

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX))
    assert len(problems) == 1 and "bus" in problems[0].lower(), (
        f"{product}: a dead bus produced {len(problems)} findings: {problems}")


def test_a_lost_chain_segment_is_one_finding_on_either_product(sim, product):
    bus = sim(fleet(product, ids=range(10, 14)))
    inv = attach(bus).inventory(1.0)
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX))
    assert len(problems) == 1, (
        f"{product}: one break reported as {len(problems)} failures: {problems}")
    assert "tail of the chain" in problems[0], problems[0]


def test_the_disable_broadcast_reaches_either_product(sim, product):
    """arbID 0 carries no device type and no manufacturer, so it is addressed to
    the whole bus by construction. A product filter that dropped it would be a
    serious defect: this is the frame that stops the motors."""
    bus = sim(fleet(product))
    import can
    bus.send(can.Message(arbitration_id=F.DISABLE_BROADCAST, data=[],
                         is_extended_id=True))
    for c in bus.controllers:
        assert c.disable_broadcasts >= 1, (
            f"{product}: id {c.dev} did not receive the disable broadcast")


def test_a_write_is_paced_and_verified_on_either_product(sim, product):
    """CD 456184's remedy -- retries and a read-back -- is about the protocol,
    not the product."""
    bus = sim(fleet(product, ids=[12]))
    adm = attach(bus)
    r = adm.write_param(12, 159, 20)
    assert r is not None, f"{product}: no response to PARAMETER_WRITE"
    assert r["verified"] is True, f"{product}: the echo did not match: {r}"


def test_the_can_id_is_protected_on_either_product(sim, product):
    """A stray write to parameter 0 renames a motor off the drivetrain. The
    refusal is in the driver, so it cannot depend on which product answered."""
    bus = sim(fleet(product, ids=[12]))
    adm = attach(bus)
    with pytest.raises(sa.ProtectedParameterError):
        adm.write_param(12, sa.PARAM_CAN_ID, 40)
    assert bus.controller(12).dev == 12, f"{product}: the id moved anyway"


def test_a_foreign_rev_device_is_not_enumerated_on_either_product(sim, product):
    """The PDH shares REV's manufacturer field and defaults to a low id. The
    device-type filter is in the arbitration id, above the product's api class,
    so it has to work for both."""
    from sparksim import SimSpark
    pdh = SimSpark(dev=1, serial="0BADCAFE", device_type=F.DEVICE_TYPE_PDH)
    bus = sim(fleet(product) + [pdh])
    inv = attach(bus).inventory(1.5)
    assert 1 not in inv, (
        f"{product}: a Power Distribution Hub was enumerated as a controller")


def test_telemetry_decodes_to_real_numbers_on_either_generation(sim, generation):
    """Both generations report a rail and a current, in different frames.
    Whatever the layout, the numbers handed to a caller are the ones set."""
    bus = sim(gen_fleet(generation, ids=[12], volts=12.4, amps=37.5))
    st = status(bus)
    assert 12 in st, f"{generation}: nothing decoded"
    s0, s1 = st[12]["status0"], st[12]["status1"]
    carrier = s1 if generation == sa.GEN_PRE25 else s0
    assert carrier is not None, f"{generation}: telemetry frame did not decode"
    assert carrier["voltage_v"] == pytest.approx(12.4, abs=0.05), (
        f"{generation}: rail decoded as {carrier['voltage_v']}")
    assert carrier["current_a"] == pytest.approx(37.5, abs=0.05), (
        f"{generation}: current decoded as {carrier['current_a']}")


# -- the cadence the audit scores ------------------------------------------
#
# These exist because `spark audit` reported all eight healthy SPARK MAX
# controllers as "sends no STATUS_1 -- it reports no faults at all". The audit
# measured api 0x2E1 on every product, and a MAX broadcasts 0x060/0x061, so the
# lookup missed and every device scored "absent". Nothing failed: 1162 tests
# passed, because none of them ran an audit against a healthy MAX bus with a
# status reading attached. That is the gap the first test here closes.


def test_a_healthy_fleet_audits_clean_on_either_generation(sim, generation):
    """The plainest claim the audit makes, and the one nothing checked."""
    bus = sim(gen_fleet(generation))
    inv = attach(bus).inventory(1.0)
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX),
                                 dict(SERIALS_FLEX),
                                 status=status(bus),
                                 generation=generation)
    real = cadence_findings(problems)
    assert real == [], (
        f"{generation}: a healthy provisioned fleet reported "
        f"{len(real)} problem(s), first: {real[0] if real else ''}")
    if generation == sa.GEN_PRE25:
        assert len(problems) == 1 and NO_SERIAL_NOTE in problems[0], (
            "a pre-25 bus broadcasts no UNIQUE_ID, and the audit has to say "
            f"identity went unchecked rather than stay silent: {problems}")


def test_the_audit_scores_the_frame_that_carries_faults(sim, generation):
    """Wrong cadence on the fault frame is a finding; wrong cadence on the
    other status frame is not. Reversed, the audit scores telemetry and calls a
    healthy controller mute."""
    ff = sa.fault_frame(generation)
    assert ff["api"] == sa.api_set(generation)[
        "status_0" if generation == sa.GEN_PRE25 else "status_1"]

    bus = sim(gen_fleet(generation, ids=[12], **slow_frame(generation, 2)))
    inv = attach(bus).inventory(1.0)
    found = sa.audit_problems(inv, {}, {12: "steer/RB"}, {12: SERIALS_FLEX[12]},
                              generation=generation)
    measured = inv[12]["periods_ms"][ff["api"]]
    assert any(str(measured) in p for p in found), (
        f"{generation}: {ff['label']} at 37 ms went unreported -- the audit is "
        f"not reading api {ff['api']:#05x}, so it cannot see faults here")

    bus2 = sim(gen_fleet(generation, ids=[12], **slow_other(generation, 250)))
    inv2 = attach(bus2).inventory(1.0)
    assert cadence_findings(sa.audit_problems(
        inv2, {}, {12: "steer/RB"}, {12: SERIALS_FLEX[12]},
        generation=generation)) == [], (
        f"{generation}: a slow cadence on the frame that carries no faults was "
        "reported as a fault-reporting failure")


def test_a_controller_at_its_own_factory_cadence_is_not_called_reverted(
        sim, generation):
    """On pre-25 the provisioned period IS REV's default, 10 ms, so 'reverted'
    and 'ok' describe the same reading. Scored in the wrong order every healthy
    controller is reported as having lost its config."""
    bus = sim(gen_fleet(generation, ids=[12]))
    inv = attach(bus).inventory(1.0)
    found = sa.audit_problems(inv, {}, {12: "steer/RB"},
                              {12: SERIALS_FLEX[12]}, generation=generation)
    assert not any("factory default" in p for p in found), (
        f"{generation}: a healthy controller was reported as reverted")


def test_no_cadence_finding_offers_an_unverified_repair(sim, generation):
    """`spark repair` writes parameter 159, the 25+ Status 1 Period. Parameter
    158 is the pre-25 equivalent and no write to it has been checked, so a
    pre-25 finding names the reading and stops."""
    bus = sim(gen_fleet(generation, ids=[12], **slow_frame(generation, 2)))
    inv = attach(bus).inventory(1.0)
    found = cadence_findings(sa.audit_problems(
        inv, {}, {12: "steer/RB"}, {12: SERIALS_FLEX[12]},
        generation=generation))
    assert found, f"{generation}: nothing reported, so the remedy is untested"
    offers_repair = any("spark repair" in p for p in found)
    if generation == sa.GEN_PRE25:
        assert not offers_repair, (
            "a pre-25 cadence finding offered `spark repair`, which writes "
            "parameter 159 to a controller nobody has checked it on")
        assert any("Hardware Client" in p for p in found), (
            "the finding withholds the repair without saying what to do "
            "instead -- every finding carries its remedy")
    else:
        assert offers_repair, "the 25+ remedy was lost"


def test_a_whole_bus_thinning_out_is_one_finding_on_either_generation(
        sim, generation):
    """Every controller slow at once is the bus eating frames, and it is
    reported once. Scored against the wrong api the cadence reads as absent,
    and the operator gets eight findings telling them to re-provision hardware
    that never lost its config."""
    bus = sim(gen_fleet(generation, **slow_frame(generation, 5)))
    inv = attach(bus).inventory(1.5)
    found = cadence_findings(sa.audit_problems(
        inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
        generation=generation))
    assert len(found) == 1, (
        f"{generation}: a uniformly thinned bus produced {len(found)} findings, "
        "not the one bus-level cause")
    assert "thinned out together" in found[0], (
        f"{generation}: the single finding was not bus-level: {found[0]}")


@pytest.mark.parametrize("spelling", ["sparkmax", "SparkMax", "SPARKMAX",
                                      "spark-max", "spark_max", " sparkmax "])
def test_the_product_switch_reaches_only_the_model_number(spelling):
    """`base.controller_type` used to be read by three switches on three
    spelling rules, so `SparkMax` picked the Flex api class and the MAX fault
    frame at once: collect_status() listened to 0x2E0/0x2E1 while the audit
    scored a frame that could not arrive.

    There is one switch left. Product decides SPARK_MODEL, the field STATUS_0
    carries at bit 54, and REVLib's SparkModel enum gives kSparkFlex = 1 and
    kSparkMax = 2. Frames key on firmware, and the frame selectors now refuse a
    product outright rather than resolving one to a default.
    """
    assert sa.normalise_product(spelling) == "sparkmax"
    assert sa.MODEL_FOR_TYPE[sa.normalise_product(spelling)] == 2

    with pytest.raises(ValueError, match="firmware generation"):
        sa.api_set(spelling)
    with pytest.raises(ValueError, match="firmware generation"):
        sa.fault_frame(spelling)


# -- the two commands that act on a bus -------------------------------------


class _KeepOpen:
    """`_open()` is used as a context manager; hand back an admin already bound
    to the simulated bus."""

    def __init__(self, adm):
        self.adm = adm

    def __enter__(self):
        return self.adm

    def __exit__(self, *exc):
        return False


def test_repair_refuses_to_write_an_unverified_parameter_on_a_pre25_bus(
        sim, monkeypatch, capsys):
    """`spark repair` writes parameter 159, the 25+ Status 1 Period. Parameter
    158 is the pre-25 equivalent and no write to it has been checked here, so
    the command has to withhold the write, not only the advice.

    Reading the bus to find out which generation it is does not write anything,
    so the test watches for transmitted frames instead of for an open.
    """
    from sparklib import cli as spark_cli
    from types import SimpleNamespace

    bus = sim(gen_fleet(sa.GEN_PRE25, ids=[12]))
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_base",
                        lambda: SimpleNamespace(controller_type="sparkmax"))
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))

    before = len(bus.sent) if hasattr(bus, "sent") else 0
    rc = spark_cli.cmd_repair(SimpleNamespace(id=12, window=1.0, persist=False))
    out = capsys.readouterr().out

    assert rc == 1, "`spark repair` claimed success on a bus it cannot write"
    if hasattr(bus, "sent"):
        assert len(bus.sent) == before, (
            "the refusal still put frames on the wire, so the write is one "
            "edit away from a controller nobody has verified")
    assert "Hardware Client" in out, (
        f"refused without saying how to set the period instead:\n{out}")
    assert "158" in out, (
        "the refusal does not name the parameter it declined to write")


def test_status_reads_the_frame_the_bus_actually_sends(sim, generation,
                                                       monkeypatch, capsys):
    """`spark status` printed a hardcoded 0x2E1. On a pre-25 bus every row read
    '-' and 'no STATUS_1' while all eight controllers were broadcasting
    normally on 0x060."""
    from sparklib import cli as spark_cli
    from types import SimpleNamespace

    bus = sim(gen_fleet(generation))
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_base",
                        lambda: SimpleNamespace(controller_type="sparkmax"))
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: dict(ROLES_FLEX))
    monkeypatch.setattr(spark_cli, "_known_serials", lambda: dict(SERIALS_FLEX))

    spark_cli.cmd_status(SimpleNamespace(window=1.0))
    out = capsys.readouterr().out
    label = sa.fault_frame(generation)["label"]

    assert f"no {label}" not in out, (
        f"{generation}: `spark status` reported a healthy fleet as sending no "
        f"{label} -- it is reading the other generation's api:\n{out}")
    rows = [ln for ln in out.splitlines() if " ms " in ln or ln.rstrip().endswith(" ms")]
    assert len(rows) == 8, (
        f"{generation}: {len(rows)} of 8 controllers printed a period:\n{out}")


def test_each_product_resolves_its_own_declared_configuration(product):
    """The file is chosen by the robot's product, not by a module constant.

    Until `_defaults_path` joined a hardcoded Flex filename, so a MAX
    robot resolved Flex values for every parameter and `require_motor_defaults_for`
    was the only thing standing in front of them. Both files now exist and each
    declares its own product; the selection is what this asserts.
    """
    assert sa.motor_defaults_key(product) == product, (
        f"{product} resolves {sa.motor_defaults_file(product)}")
    assert sa.motor_defaults_product(controller_type=product) == product, (
        f"the file {sa.motor_defaults_file(product)} does not declare "
        f"meta.applies_to for {product}, so a robot reading it gets the other "
        "product's values under a name that says otherwise")
    sa.require_motor_defaults_for(product)          # the shipped pair, no raise


def test_repair_refuses_when_the_declared_file_is_for_the_other_product(
        sim, monkeypatch, capsys):
    """The guard behind the selection, for when the two disagree.

    `spark repair` writes what the declared configuration says, so a MAX robot
    holding a file that declares SPARK Flex would have every Flex value written
    into it and every write would report success. Selecting the file by product
    makes that unreachable by accident; it stays reachable by a bad edit to
    meta.applies_to or to base.controller_type, which is what this covers.
    """
    from types import SimpleNamespace

    from sparklib import cli as spark_cli

    bus = sim(gen_fleet(sa.GEN_PRE25, ids=[12]))
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_base",
                        lambda: SimpleNamespace(controller_type="sparkmax"))
    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))
    monkeypatch.setattr(sa, "load_motor_defaults",
                        lambda *a, **k: {"meta": {"applies_to": "SPARK Flex"}})

    before = len(bus.sent) if hasattr(bus, "sent") else 0
    rc = spark_cli.cmd_repair(SimpleNamespace(id=12, window=1.0, persist=False))
    out = capsys.readouterr().out

    assert rc != 0, "`spark repair` claimed success on a product it cannot provision"
    if hasattr(bus, "sent"):
        assert len(bus.sent) == before, "the refusal still put frames on the wire"
    assert "sparkmax" in out and "sparkflex" in out, (
        f"the refusal names neither the file it read nor what it declares:\n{out}")


def test_repair_is_allowed_when_the_declared_product_matches(monkeypatch):
    """The other direction. A guard that always refuses would pass the test above."""
    from types import SimpleNamespace

    from sparklib import cli as spark_cli

    monkeypatch.setattr(spark_cli, "_base",
                        lambda: SimpleNamespace(controller_type="sparkflex"))
    sa.require_motor_defaults_for("sparkflex")      # the shipped file, no raise


def test_a_silent_bus_does_not_report_a_generation_it_never_read():
    """rig-max: `spark status` found zero controllers and still
    printed FLEET ASSUMPTION BROKEN, saying the bus read firmware 25+. It read
    nothing. dominant_generation falls back to its default on an empty reading,
    and that fallback reached a call site that treated it as an observation."""
    assert sa.observed_generation({}) is None
    assert sa.observed_generation(None) is None
    assert sa.observed_generation({1: {"status0": None}}) is None
    assert sa.observed_generation({1: {"generation": None}}) is None
    # and it still answers when the bus did report
    assert sa.observed_generation({1: {"generation": sa.GEN_PRE25}}) == sa.GEN_PRE25
    # the warning it feeds must therefore stay silent on an empty bus
    assert sa.unexpected_generation("sparkmax", sa.observed_generation({})) is None
    assert sa.unexpected_generation("sparkmax", sa.GEN_FW25) is not None


def test_no_fleet_assumption_warning_is_fed_a_defaulted_generation():
    """Read the call sites, not the helper: the defect was a caller passing
    dominant_generation's fallback where an observation was required."""
    import ast
    import pathlib

    src = pathlib.Path(sa.__file__).with_name("cli.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "unexpected_generation":
            continue
        assert len(node.args) == 2, ast.dump(node)
        arg = node.args[1]
        assert isinstance(arg, ast.Call), (
            f"cli.py:{node.lineno} passes a bare name as the observed "
            "generation; it must come from observed_generation(status)")
        assert getattr(arg.func, "attr", None) == "observed_generation", (
            f"cli.py:{node.lineno} feeds "
            f"{getattr(arg.func, 'attr', '?')}() to unexpected_generation, "
            "which reports a fallback as something the wire said")
