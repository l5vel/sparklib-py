"""The read-only parameter sweep, and the proof that it is read-only.

The sweep exists to settle which parameter table a product actually uses.
rev_parameter_index.tsv gives ids 158-165 to Status 0-7 Period and 166-167 to
MAXMotion Max Velocity 0 and Max Accel 0. Appendix A of the assembly manual
configures ten status frames on a SPARK Flex. Ten frames and eight parameters
cannot both be right, and the answer decides whether `spark repair` may write a
status period on rig-max-2 and rig-max.

Reading it off the bus is only safe if the read is a read. The sweep used to run
on PARAMETER_WRITE, where id plus value is a write and id alone is a read, and
one appended byte crossed that line. Since it runs on READ_PARAMETER,
a remote frame with no payload at all, so the property is structural. The tests
below prove it from the wire: nothing the sweep sends reaches the write api, and
the firmware behaviour that would break the old property is still modelled and
still shown to stop the run.
"""

from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import attach, build_fleet, spark
from sparksim import frames as F
from sparksim.faults import SparkBehaviour

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "spark_param_sweep",
    pathlib.Path(__file__).resolve().parents[2] / "tools" / "spark_param_sweep.py")
sweep_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sweep_tool)


def _param_frames(bus):
    """Every frame the host put on the parameter api."""
    return [m for m in bus.sent if F.base_of(m.arbitration_id) == F.PARAM_WRITE]


# -- the read is a read ------------------------------------------------------


def test_a_read_never_reaches_the_write_api_at_all(sim):
    """Structural now, where it used to be one byte of payload discipline."""
    bus = sim(build_fleet([12]))
    adm = attach(bus)
    for pid in (0, 59, 159, 198):
        adm.read_param_pair(12, pid)
    assert not _param_frames(bus), (
        "a parameter read put a frame on PARAMETER_WRITE; on that api one "
        "appended byte is a write to the id being read")
    assert bus.sent, "the sweep's read sent nothing at all"
    for m in bus.sent:
        assert m.is_remote_frame and not len(m.data), (
            f"0x{m.arbitration_id:08X} carried {len(m.data)} bytes; a read "
            "request has no payload to append a value to")


def test_a_read_leaves_every_value_alone(sim):
    """The whole point. Read the fleet's parameters and prove nothing moved."""
    bus = sim(build_fleet([12]))
    ctrl = bus.controllers[0]
    before = dict(ctrl.ram)
    flash_before = dict(ctrl.flash)
    adm = attach(bus)
    for pid in range(0, 256):
        adm.read_param_pair(12, pid)
    assert ctrl.ram == before, (
        "reading the parameters changed RAM: "
        f"{ {k: (before.get(k), v) for k, v in ctrl.ram.items() if before.get(k) != v} }")
    assert ctrl.flash == flash_before, "reading the parameters changed flash"
    assert not ctrl.write_log, (
        f"{len(ctrl.write_log)} write(s) were recorded by a read-only sweep")


def test_the_device_reports_its_own_type_for_every_id(sim):
    """The type code is what settles which table a product uses, so it has to
    come from the controller and survive the decode, never from the index."""
    bus = sim([spark(12, ram={166: 0x40490FDB})])
    adm = attach(bus)
    raw = adm.read_param_value(12, 166)
    assert raw == 0x40490FDB
    assert sweep_tool.decode(raw, 3) == pytest.approx(3.14159, abs=1e-4), (
        "a Float parameter did not survive the decode, so a type disagreement "
        "with the index cannot be read off the value")
    types = adm.param_types(12, start_id=160)
    assert types and 166 in types, "apiClass 13 did not report a type for 166"


def test_a_firmware_that_answers_no_reads_is_reported_not_guessed(sim):
    """Firmware 26.1.6 does answer parameter reads, measured on rig-flex. A controller that answers none of them is still a state the
    driver has to report as silent and never as zero."""
    bus = sim([spark(12, behaviour=SparkBehaviour(answer_param_reads=False))])
    assert attach(bus).read_param_value(12, 159) is None, (
        "a firmware that serves no reads returned a value, which would be "
        "indistinguishable from a real reading of that parameter")


# -- the canary --------------------------------------------------------------


@pytest.mark.parametrize("reports_pre", [False, True], ids=["reports-zero",
                                                            "reports-pre-value"])
def test_the_canary_stops_a_run_whose_reads_are_writes(sim, reports_pre):
    """The residual risk the sweep is built around: firmware that mistakes a
    short frame for a write of zero. Which value such firmware would answer with
    is unknown, so both are modelled -- reporting the zero it just wrote, and
    reporting the value from a moment ago, which makes the FIRST read look
    correct. Either way the run must stop on one parameter, before the sweep
    touches the other 198, and say that RAM is now suspect."""
    bus = sim([spark(12, behaviour=SparkBehaviour(
        param_read_writes_zero=True, param_read_reports_pre_value=reports_pre))])
    ok, why = sweep_tool._canary(attach(bus), 12, {}, [])
    assert ok is not True, f"a mutating read was accepted as safe: {why}"
    assert "STOP" in why and "power cycle" in why, (
        f"the canary refused without saying how to recover:\n{why}")
    # Bounded by the frames sent, not by a count of parameters: READ_PARAMETER
    # is addressed per PAIR, so one mutating read costs two ids and a number
    # written here would go stale the next time the frame shape moves.
    reads = [m for m in bus.sent
             if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST]
    assert len(reads) <= 2, (
        "the canary kept reading after the parameter moved; a mutating read "
        "must cost one pair, not the whole table")
    moved = {r.param_id for r in bus.controllers[0].write_log}
    assert moved <= {sweep_tool.CANARY_PARAM, sweep_tool.CANARY_PARAM ^ 1}, (
        f"a mutating read reached {sorted(moved)}, outside the canary's own pair")


def test_the_canary_passes_on_a_healthy_controller(sim):
    bus = sim(build_fleet([12]))
    ok, why = sweep_tool._canary(attach(bus), 12, {}, [])
    assert ok is True, f"a healthy controller failed the canary: {why}"


def test_an_ambiguous_canary_refuses_rather_than_reporting_clean(sim):
    """A controller already holding 0 cannot prove the read did not write it.
    Saying 'clean' there would be a guess dressed as a check."""
    bus = sim([spark(12, ram={sweep_tool.CANARY_PARAM: 0})])
    ok, why = sweep_tool._canary(attach(bus), 12, {}, [])
    assert ok is None, "an unprovable canary was reported as a pass"
    assert "ambiguous" in why.lower() or "either" in why.lower()


# -- what the sweep concludes ------------------------------------------------


def test_the_sweep_flags_an_id_whose_type_disagrees_with_the_index(sim):
    """The finding the whole exercise is for. If 166 answers Uint while the
    index calls it MAXMotion Max Velocity 0 (Float), the index is not this
    product's table and repair must not trust it."""
    bus = sim([spark(12, ram={166: 20})])
    index = {166: {"name": "MAXMotion Max Velocity 0", "type": "FLOAT",
                   "access": "RW", "default": "0"}}
    rows = sweep_tool.sweep(attach(bus), 12, index, 166, 166, 0.5, [])
    assert rows[0]["answered"]
    assert "TYPE MISMATCH" in rows[0]["note"], (
        f"a device reporting Uint where the index says FLOAT was not flagged: "
        f"{rows[0]}")


def test_the_sweep_marks_an_id_the_device_calls_unused(sim):
    """An id that does not exist on this product answers Unused. That is how
    'the MAX has eight status frames, the Flex has ten' becomes visible."""
    bus = sim([spark(12, behaviour=SparkBehaviour(read_result=0))])
    ctrl = bus.controllers[0]
    ctrl.ram.pop(197, None)
    rows = sweep_tool.sweep(attach(bus), 12, {}, 197, 197, 0.5, [])
    assert rows[0]["note"], "an id absent from the index was reported unremarked"


def test_the_sweep_sends_no_write_and_no_persist(sim):
    """Read the whole table off a real fleet and inspect every frame sent."""
    bus = sim(build_fleet([12]))
    rows = sweep_tool.sweep(attach(bus), 12, sweep_tool.load_index(), 0, 198,
                            0.5, [])
    assert len(rows) == 199
    for m in bus.sent:
        base = F.base_of(m.arbitration_id)
        assert base != F.PERSIST, "the sweep sent PERSIST"
        if base == F.PARAM_WRITE:
            assert len(m.data) == 1, (
                f"the sweep sent a {len(m.data)}-byte parameter frame for id "
                f"{m.data[0]}, which the device reads as a write")
    assert not bus.controllers[0].write_log, "the sweep wrote a parameter"


def test_the_shipped_index_and_appendix_a_disagree_on_status_frame_count():
    """Pin the discrepancy that motivated the sweep, so it cannot be quietly
    'fixed' by editing one side. Appendix A configures Status 0-9 on a SPARK
    Flex. The index stops at Status 7 and gives 166/167 to MAXMotion."""
    index = sweep_tool.load_index()
    assert index, "the parameter index did not load"
    periods = {pid: e["name"] for pid, e in index.items()
               if e["name"].startswith("Status ") and e["name"].endswith(" Period")}
    assert sorted(periods) == list(range(158, 166)), (
        f"the index's status-period ids moved: {sorted(periods)}")
    assert index[166]["name"] == "MAXMotion Max Velocity 0", (
        "166 is no longer MAXMotion in the index -- if a source settled this, "
        "update spark_provenance and the sweep's docstring together")
    assert index[166]["type"] == "FLOAT"


def test_the_sweep_selects_the_spark_groups_and_no_others():
    """devices carries drive, steer AND cancoder ids. A CANcoder is not a
    REV controller and answers no SPARK parameter api, so sweeping one sends 199
    frames to a device that cannot reply and reports it as a dead controller.

    The selection is by GROUP, and it has to be. Ids are unique per bus, not per
    robot: rig-max puts CANcoders on the CANivore at 1, 2, 3, 4 and drive SPARKs
    on can0 at 1 and 4, so on that robot the two id sets overlap and are
    both correct. A rule written as "no cancoder id" would refuse to sweep half
    of rig-max's drivetrain, and would pass on rig-flex only because that robot
    happens to number them apart.
    """
    from sparklib.config import get as _spark_config

    groups = vars(_spark_config().devices)
    assert "cancoder" in groups, (
        "this config has no cancoders, so the test proves nothing")

    ids = set(sweep_tool.spark_ids(_spark_config().devices))
    spark = set()
    for group in sweep_tool.SPARK_GROUPS:
        spark |= set(vars(groups[group]).values())
    assert ids == spark, (
        f"the sweep addresses {sorted(ids)} where the SPARK groups hold "
        f"{sorted(spark)}")

    cancoder_only = set(vars(groups["cancoder"]).values()) - spark
    assert not (ids & cancoder_only), (
        f"the sweep would address CANcoders {sorted(ids & cancoder_only)}, which "
        "are on no SPARK bus at all")


def test_status_8_and_9_periods_are_not_166_and_167():
    """The simulator mapped Status 8 and 9 to parameters 166 and 167 because
    they follow 165 and nobody checked. REVLib puts them at 199 and 224; 166
    and 167 are MAXMotion Max Velocity 0 and Max Accel 0, both FLOAT. Under the
    old map a period write would have landed on a motion limit, and the sim
    would have modelled it as working.

    The fleet's parameter table stops at 198, so it could never have caught
    this -- which is why the primary source is pinned here instead.
    """
    assert F.PERIOD_PARAM_FOR_API[0x2E8] == 199
    assert F.PERIOD_PARAM_FOR_API[0x2E9] == 224
    index = sweep_tool.load_index()
    assert index[166]["type"] == "FLOAT" and index[167]["type"] == "FLOAT", (
        "166/167 are no longer FLOAT in the index; re-check the mapping")
    assert 199 not in index and 224 not in index, (
        "the index now reaches 199/224, so it can carry Status 8 and 9 -- "
        "update it and this test together")
    for api, pid in F.PERIOD_PARAM_FOR_API.items():
        entry = index.get(pid)
        if entry is not None:
            assert entry["name"].endswith("Period"), (
                f"api {api:#05x} maps to parameter {pid}, which the index calls "
                f"{entry['name']!r} -- that is not a status period")


def test_revlib_headers_are_preserved_not_left_in_a_scratchpad():
    """The only primary source for 199 and 224 was a volatile session
    scratchpad. Evidence that disappears on reboot cannot be re-checked."""
    header = (pathlib.Path(__file__).resolve().parents[2]
              / "reference" / "revlib-2026.0.2" / "SparkParameters.h")
    assert header.is_file(), f"REVLib primary source is missing: {header}"
    text = header.read_text()
    assert "kStatus8Period = 199" in text
    assert "kStatus9Period = 224" in text
    assert text.count("enum SparkParameter") == 1, (
        "more than one parameter enum; the single-enum claim needs re-checking")
