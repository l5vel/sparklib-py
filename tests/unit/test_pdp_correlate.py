"""The PDP probe, checked without a bus.

Two silent failures shipped in this tool and both printed a clean "nothing
correlated" verdict over an empty series.

The first read "bus_voltage" and "output_current" off decode_status_0, whose
keys are "voltage_v" and "current_a". The second read only api 0x2E0, which is
the firmware-25+ frame. rig-max is SPARK MAX on 24.0.1, where the applied output
is in 0x060 and the volts and amps are in 0x061, so the probe decoded nothing on
the one bus that has a panel to measure. Frame layout keys on FIRMWARE
GENERATION, never on product.

Neither is visible to the keyword sweep in test_spark_callsites.py. Only running
the pipeline over both generations' frames catches them.
"""
import ast
import collections
import importlib.util
import pathlib
import random
import struct

import pytest

from sparklib import admin as sa

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "spark_pdp_correlate.py"

V_LSB, A_LSB, OUT_LSB = 0.0073260073260073, 0.0366300366300366, 3.082369457075716e-05


@pytest.fixture(scope="module")
def pdpc():
    spec = importlib.util.spec_from_file_location("pdpc", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def status0(applied, volts, amps):
    """An 8-byte firmware-25+ STATUS_0 payload carrying all three readings."""
    o = int(round(applied / OUT_LSB)) & 0xFFFF
    rv = int(round(volts / V_LSB)) & 0xFFF
    ra = int(round(amps / A_LSB)) & 0xFFF
    d = bytearray(8)
    d[0], d[1] = o & 0xFF, (o >> 8) & 0xFF
    d[2] = rv & 0xFF
    d[3] = ((rv >> 8) & 0x0F) | ((ra & 0x0F) << 4)
    d[4] = (ra >> 4) & 0xFF
    d[5] = 30
    return bytes(d)


def test_the_payload_builder_round_trips():
    """Guards the fixture: a bad builder would fake every result below."""
    r = sa.decode_status_0(status0(0.5, 12.0, 8.0))
    assert r["applied_output"] == pytest.approx(0.5, abs=1e-3)
    assert r["voltage_v"] == pytest.approx(12.0, abs=0.01)
    assert r["current_a"] == pytest.approx(8.0, abs=0.05)


def legacy_status0(applied):
    """Pre-25 api 0x060. Carries the applied output and the fault word."""
    o = int(round(applied / OUT_LSB)) & 0xFFFF
    return bytes([o & 0xFF, (o >> 8) & 0xFF, 0, 0, 0, 0, 0x10, 0])


def legacy_status1(volts, amps, rpm=0.0):
    """Pre-25 api 0x061. Carries the volts and amps, which 0x060 does not."""
    rv = int(round(volts / V_LSB)) & 0xFFF
    ra = int(round(amps / A_LSB)) & 0xFFF
    d = bytearray(struct.pack("<f", rpm))
    d.append(30)
    d.append(rv & 0xFF)
    d.append(((rv >> 8) & 0x0F) | ((ra & 0x0F) << 4))
    d.append((ra >> 4) & 0xFF)
    return bytes(d)


def test_the_legacy_payload_builders_round_trip():
    """Guards the pre-25 fixtures the way the 25+ one is guarded."""
    r0 = sa.decode_legacy_status_0(legacy_status0(0.5))
    assert r0["applied_output"] == pytest.approx(0.5, abs=1e-3)
    assert r0["is_beacon"] is False
    r1 = sa.decode_legacy_status_1(legacy_status1(12.0, 8.0))
    assert r1["voltage_v"] == pytest.approx(12.0, abs=0.01)
    assert r1["current_a"] == pytest.approx(8.0, abs=0.05)


@pytest.fixture
def driving_window():
    """20 bins of a ramping load: two SPARKs, and a panel frame carrying the
    rail voltage in a big-endian pair at offset 0."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        load = (slot % 10) / 10.0
        volts = 12.8 - 1.5 * load
        for dev in (1, 2):
            spark[(dev, sa.STATUS_0_API)][slot].append(
                status0(load, volts, 12.0 * load))
        mv = int(volts * 100)
        foreign[(8, 4, 0x50, 0)][slot].append(
            bytes([mv >> 8, mv & 0xFF, 7, 7, 7, 7, 7, 7]))
    return spark, foreign


def test_spark_series_decodes_the_window(pdpc, driving_window):
    """The bug: wrong keys made all three series empty and said nothing."""
    spark, _ = driving_window
    volts, amps, applied, dev_amps = pdpc.spark_series(spark)
    assert volts, "no bus voltage decoded; decode_status_0 keys have drifted"
    assert amps, "no output current decoded; decode_status_0 keys have drifted"
    assert applied, "no applied output decoded"
    assert max(volts.values()) - min(volts.values()) > 1.0
    assert max(amps.values()) > 20.0          # two controllers, summed


def test_the_self_check_sees_a_planted_load(pdpc, driving_window):
    """A silent decode failure reports 'no load' on a window that has one."""
    spark, _ = driving_window
    volts, amps, applied, dev_amps = pdpc.spark_series(spark)
    assert pdpc.report_self_check(volts, applied, dev_amps) is True


def test_a_planted_panel_field_is_found_and_named(pdpc, driving_window, capsys):
    spark, foreign = driving_window
    volts, amps, _, _ = pdpc.spark_series(spark)
    pdpc.report_foreign(foreign, volts, amps, top=6)
    out = capsys.readouterr().out
    assert "u16be@0" in out, "the planted voltage field was not identified"
    assert "+1.00" in out


def test_an_idle_window_identifies_nothing(pdpc):
    """The control: a flat rail must NOT produce a correlation."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.0, 12.8, 0.0))
        foreign[(8, 4, 0x50, 0)][slot].append(bytes([5, 0, 7, 7, 7, 7, 7, 7]))
    volts, amps, applied, dev_amps = pdpc.spark_series(spark)
    assert pdpc.report_self_check(volts, applied, dev_amps) is False


@pytest.fixture
def pre25_driving_window():
    """rig-max's shape: eight MAX on 24.0.1. Output in 0x060, telemetry in 0x061,
    and nothing at all on 0x2E0."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    s1 = sa.API_SETS[sa.GEN_PRE25]["status_1"]
    for slot in range(20):
        load = (slot % 10) / 10.0
        volts = 12.8 - 1.5 * load
        for dev in (1, 2):
            spark[(dev, sa.LEGACY_STATUS_0_API)][slot].append(legacy_status0(load))
            spark[(dev, s1)][slot].append(legacy_status1(volts, 12.0 * load))
        mv = int(volts * 100)
        foreign[(8, 4, 0x50, 0)][slot].append(
            bytes([mv >> 8, mv & 0xFF, 7, 7, 7, 7, 7, 7]))
    return spark, foreign


def test_a_pre25_window_decodes(pdpc, pre25_driving_window):
    """The rig-max bug: the probe read only 0x2E0 and saw nothing on a MAX bus."""
    spark, _ = pre25_driving_window
    assert not any(api == sa.STATUS_0_API for _, api in spark), "fixture is pre-25"
    volts, amps, applied, dev_amps = pdpc.spark_series(spark)
    assert volts, "no voltage decoded from 0x061; the probe is 25+ only again"
    assert amps, "no current decoded from 0x061"
    assert applied, "no applied output decoded from 0x060"
    assert max(volts.values()) - min(volts.values()) > 1.0


def test_the_pre25_self_check_sees_a_planted_load(pdpc, pre25_driving_window):
    spark, _ = pre25_driving_window
    volts, amps, applied, dev_amps = pdpc.spark_series(spark)
    assert pdpc.report_self_check(volts, applied, dev_amps) is True


def test_a_pre25_window_finds_the_planted_panel_field(pdpc, pre25_driving_window,
                                                      capsys):
    spark, foreign = pre25_driving_window
    volts, amps, _, _ = pdpc.spark_series(spark)
    pdpc.report_foreign(foreign, volts, amps, top=6)
    out = capsys.readouterr().out
    assert "u16be@0" in out
    assert "+1.00" in out


def test_the_probe_covers_every_generation_the_driver_knows(pdpc):
    """Adding a generation to API_SETS must not leave this tool behind."""
    for gen, apis in sa.API_SETS.items():
        assert apis["status_0"] in pdpc.TELEMETRY_APIS, gen
        assert apis["status_1"] in pdpc.TELEMETRY_APIS or gen == sa.GEN_FW25, gen


def test_a_25plus_beacon_on_0x060_is_not_read_as_a_measurement(pdpc):
    """Firmware 25+ pins every signal in 0x060. Decoding it yields a fake 0.0
    applied output for every controller on the bus."""
    o, v, a = pdpc.readings(sa.LEGACY_STATUS_0_API, sa.LEGACY_BEACON_PAYLOAD)
    assert (o, v, a) == (None, None, None)


def test_no_telemetry_names_the_apis_it_did_see(pdpc, capsys):
    """What the failing rig-max run should have printed instead of a verdict."""
    spark = {(1, 0x055): {0: [bytes(8)]}}
    pdpc.report_no_telemetry(spark)
    out = capsys.readouterr().out
    assert "0x055" in out
    assert f"0x{sa.LEGACY_STATUS_0_API:03X}" in out


def test_the_host_heartbeat_is_not_reported_as_a_panel(pdpc):
    """SocketCAN loops our own frames back; 0x01011840 decodes as device 1/1."""
    assert 0x01011840 in pdpc.HOST_FRAMES
    assert pdpc.device_fields(0x01011840)[:2] == (1, 1)


# -- the absolute rail-voltage match -----------------------------------------

def _volt_window(panel_byte, rail=11.64):
    """A window whose SPARKs read `rail` and whose panel frame carries
    `panel_byte` at offset 5, plus a byte that swings far too wide to be a rail."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        v = rail + (0.1 if slot % 2 else -0.1)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        b = panel_byte(slot)
        foreign[(8, 4, 0x53, 0)][slot].append(
            bytes([0, 0, 0, 0, 0, b, 0, slot * 11 % 256]))
    return spark, foreign


def test_a_planted_rail_field_is_found_by_absolute_value(pdpc):
    """CTRE's PDP scaling is v = raw*0.05 + 4.0, so 11.64 V is raw 153."""
    spark, foreign = _volt_window(lambda slot: 153 + (2 if slot % 2 else 0))
    volts, _, _, _ = pdpc.spark_series(spark)
    rows, tested = pdpc.voltage_matches(foreign, volts)
    assert tested > 0, "no combinations tested; the check is vacuous"
    hits = [(d["field"], d["scaling"]) for d in rows]
    assert ("u8@5", "x*0.05+4.0") in hits, hits


def test_a_rail_field_carries_its_correlation_and_swing_ratio(pdpc):
    """r is the discriminator: a field measuring this rail tracks it whatever
    its scale, because correlation ignores amplitude."""
    spark, foreign = _volt_window(lambda slot: 153 + (2 if slot % 2 else 0))
    volts, _, _, _ = pdpc.spark_series(spark)
    rows, _ = pdpc.voltage_matches(foreign, volts)
    hit = next(d for d in rows
               if (d["field"], d["scaling"]) == ("u8@5", "x*0.05+4.0"))
    assert hit["r"] is not None and hit["r"] > 0.9, hit
    assert hit["ratio"] is not None and hit["ratio"] > 0.1, hit


def test_a_field_landing_in_range_without_tracking_ranks_below_one_that_does(pdpc):
    """The rig-max shape: a byte that reads near the rail but ignores it.

    The decoy lands CLOSER to the rail than the real field does, so ranking by
    absolute closeness alone puts the wrong one first. Only correlation, which
    ignores amplitude, separates them.
    """
    spark, foreign = _volt_window(lambda slot: 153 + (2 if slot % 2 else 0))
    for slot, frames in foreign[(8, 4, 0x53, 0)].items():
        d = bytearray(frames[0])
        d[6] = 152 + (2 if (slot // 5) % 2 else 0)   # changes every 5 slots
        frames[0] = bytes(d)
    volts, _, _, _ = pdpc.spark_series(spark)
    rows, _ = pdpc.voltage_matches(foreign, volts)
    by_field = {d["field"]: d for d in rows if d["scaling"] == "x*0.05+4.0"}
    assert {"u8@5", "u8@6"} <= set(by_field), sorted(by_field)
    assert by_field["u8@6"]["off"] < by_field["u8@5"]["off"], (
        "the decoy must be the closer of the two, or this proves nothing")
    assert abs(by_field["u8@6"]["r"]) < 0.5 < by_field["u8@5"]["r"]
    ranked = [d["field"] for d in rows if d["scaling"] == "x*0.05+4.0"]
    assert ranked[0] == "u8@5", ranked


def test_a_constant_field_is_not_offered_as_the_rail(pdpc):
    """A byte that never moves cannot be measuring a rail that sagged."""
    spark, foreign = _volt_window(lambda slot: 153)
    volts, _, _, _ = pdpc.spark_series(spark)
    rows, _ = pdpc.voltage_matches(foreign, volts)
    assert ("u8@5", "x*0.05+4.0") not in [(d["field"], d["scaling"]) for d in rows]


def test_a_window_with_no_rail_field_reports_none(pdpc, capsys):
    """The rig-max shape: a panel that reports current and no readable rail."""
    spark, foreign = _volt_window(lambda slot: 3)
    volts, _, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_absolute_voltage(foreign, volts, top=5) is False
    out = capsys.readouterr().out
    assert "identifies no voltage field" in out


def test_the_absolute_match_needs_a_spark_rail(pdpc):
    """With no SPARK telemetry there is nothing to match against."""
    assert pdpc.voltage_matches({(8, 4, 0x53, 0): {0: [bytes(8)]}}, {}) == ([], 0)


def test_plausible_junk_does_not_produce_chance_rail_matches(pdpc):
    """Specificity. Eleven frames of random bytes, over rig-max's measured rail
    and sag, must yield nothing. A pass that fires on noise says nothing when
    it fires on a panel."""
    rng = random.Random(7)
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(150):
        v = 11.64 + 0.233 * ((slot % 7) / 3.0 - 1.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        for api in range(0x050, 0x05B):
            foreign[(8, 4, api, 0)][slot].append(
                bytes(rng.randrange(256) for _ in range(8)))
    volts, _, _, _ = pdpc.spark_series(spark)
    rows, tested = pdpc.voltage_matches(foreign, volts)
    assert tested > 2000, tested
    assert rows == [], f"{len(rows)} chance hits: {[d['field'] for d in rows]}"


# -- solving for the panel's scale -------------------------------------------

def test_the_fit_recovers_a_planted_scale(pdpc):
    """Plant CTRE's documented raw*0.05 + 4.0 and check the fit finds it."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(60):
        v = 11.0 + 1.4 * ((slot % 15) / 14.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        raw = int(round((v - 4.0) / 0.05))
        foreign[(8, 4, 0x55, 0)][slot].append(bytes([0, 0, 0, 0, 0, 0, raw, 0]))
    volts, _, _, _ = pdpc.spark_series(spark)
    rows = pdpc.rail_fits(foreign, volts)
    best = next(d for d in rows if d["field"] == "u8@6")
    assert best["slope"] == pytest.approx(0.05, abs=0.005), best
    assert best["intercept"] == pytest.approx(4.0, abs=0.5), best
    assert best["rms"] < 0.05, best


def test_the_fit_ignores_a_field_that_falls_as_the_rail_rises(pdpc):
    """A current channel anticorrelates with the rail and is not a rail field."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(60):
        v = 11.0 + 1.4 * ((slot % 15) / 14.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        falling = int(round((12.4 - v) * 100))
        foreign[(8, 4, 0x55, 0)][slot].append(
            bytes([0, 0, 0, 0, 0, 0, falling & 0xFF, 0]))
    volts, _, _, _ = pdpc.spark_series(spark)
    assert not [d for d in pdpc.rail_fits(foreign, volts) if d["field"] == "u8@6"]


def test_the_fit_reports_the_raw_mean_exactly(pdpc):
    """The raw mean is what the CTRE comparison rests on, so it must be the
    field's actual mean and not something recovered from the fitted line."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    raws = []
    for slot in range(60):
        v = 11.0 + 1.4 * ((slot % 15) / 14.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        raw = int(round((v - 4.0) / 0.05))
        raws.append(raw)
        foreign[(8, 4, 0x55, 0)][slot].append(bytes([0, 0, 0, 0, 0, 0, raw, 0]))
    volts, _, _, _ = pdpc.spark_series(spark)
    best = next(d for d in pdpc.rail_fits(foreign, volts) if d["field"] == "u8@6")
    assert best["raw_mean"] == pytest.approx(sum(raws) / len(raws))
    assert best["raw_span"] == max(raws) - min(raws)


def test_a_degenerate_fit_is_dropped_before_it_is_reported(pdpc, capsys):
    """A 16-bit read across a constant high byte fits with an absurd offset.
    Reporting it as a rail candidate is noise."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(60):
        v = 11.0 + 1.4 * ((slot % 15) / 14.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        raw = int(round((v - 4.0) / 0.05))
        foreign[(8, 4, 0x55, 0)][slot].append(
            bytes([0, 0, 0, 0, 0, 255, raw, 0]))     # byte 5 pinned at 255
    volts, _, _, _ = pdpc.spark_series(spark)
    raw_rows = pdpc.rail_fits(foreign, volts)
    assert any(abs(d["intercept"]) > 40 for d in raw_rows), (
        "the fixture must produce a degenerate fit or this proves nothing")
    pdpc.report_rail_fit(foreign, volts, top=12)
    out = capsys.readouterr().out
    for line in out.splitlines():
        if "u16be@5" in line:
            assert False, f"degenerate fit reported: {line}"


def test_the_fit_reports_the_scale_the_controller_would_imply(pdpc, capsys):
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(60):
        v = 11.0 + 1.4 * ((slot % 15) / 14.0)
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.4, v, 6.0))
        raw = int(round((v - 4.0) / 0.05))
        foreign[(8, 4, 0x55, 0)][slot].append(bytes([0, 0, 0, 0, 0, 0, raw, 0]))
    volts, _, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_rail_fit(foreign, volts, top=5) is True
    out = capsys.readouterr().out
    assert "REV publish" in out
    assert "TWO ASSUMPTIONS" in out
    assert "NOT A CALIBRATION" in out
    assert "LOWER BOUND" in out


def test_the_fit_says_so_when_nothing_tracks(pdpc, capsys):
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.0, 12.8, 0.0))
        foreign[(8, 4, 0x55, 0)][slot].append(bytes(8))
    volts, _, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_rail_fit(foreign, volts, top=5) is False
    assert "nothing to fit" in capsys.readouterr().out


# -- reading one known field against a meter, with no load needed -------------

def _rest_window(panel_raw, shown_volts):
    """A window at rest: the rail sits still, so nothing correlates."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.0, shown_volts, 0.0))
        foreign[(8, 4, 0x52, 0)][slot].append(
            bytes([0, 0, 0, 0, 0, 0, panel_raw, 0]))
    return spark, foreign


def test_the_meter_check_needs_no_load(pdpc, capsys):
    """At rest nothing correlates, and the point is that it does not have to."""
    spark, foreign = _rest_window(panel_raw=164, shown_volts=11.54)
    volts, _, _, dev_amps = pdpc.spark_series(spark)
    assert pdpc.report_self_check(volts, {}, dev_amps) is False, "must be at rest"
    assert pdpc.report_field_against_meter(foreign, volts, "u8@6", 12.20) is True
    out = capsys.readouterr().out
    assert "The panel agrees with the meter" in out
    assert "ALONE does not say" in out


def test_the_meter_check_clears_the_scale_when_controllers_agree(pdpc, capsys):
    """The other outcome must be reachable, or the check only ever says one
    thing and proves nothing. The panel agrees here too, and the controllers
    agreeing has to win, because that is the decisive one."""
    spark, foreign = _rest_window(panel_raw=164, shown_volts=12.20)
    volts, _, _, _ = pdpc.spark_series(spark)
    pdpc.report_field_against_meter(foreign, volts, "u8@6", 12.20)
    out = capsys.readouterr().out
    assert "The controllers agree with the meter" in out
    assert "REV's scale stands" in out


def test_the_meter_check_says_so_when_neither_agrees(pdpc, capsys):
    spark, foreign = _rest_window(panel_raw=100, shown_volts=11.54)
    volts, _, _, _ = pdpc.spark_series(spark)
    pdpc.report_field_against_meter(foreign, volts, "u8@6", 12.20)
    assert "NEITHER agrees" in capsys.readouterr().out


def test_the_field_read_reports_raw_values_without_a_meter(pdpc, capsys):
    spark, foreign = _rest_window(panel_raw=164, shown_volts=11.54)
    volts, _, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_field_against_meter(foreign, volts, "u8@6", None) is True
    out = capsys.readouterr().out
    assert "164" in out
    assert "Pass --meter" in out


def test_an_absent_field_is_reported_rather_than_faked(pdpc, capsys):
    spark, foreign = _rest_window(panel_raw=164, shown_volts=11.54)
    volts, _, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_field_against_meter(foreign, volts, "u8@99", None) is False
    assert "no foreign frame carries" in capsys.readouterr().out


def test_a_bus_with_no_panel_does_not_claim_a_meter_check_ran(pdpc, capsys,
                                                              monkeypatch):
    """rig-flex has no panel. Passing --meter there must not report a check that
    never happened."""
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(20):
        spark[(1, sa.STATUS_0_API)][slot].append(status0(0.0, 11.54, 0.0))
    monkeypatch.setattr(pdpc, "collect", lambda *a, **k: (spark, {}))
    rc = pdpc.main(["-c", "vcan0", "--seconds", "0", "--meter", "12.20"])
    out = capsys.readouterr().out
    assert "does not need load and stands on its own" not in out
    assert rc == 1


# -- the discriminator: does the gap survive at zero current? -----------------

def _loaded_window(scale_error, feed_ohm):
    """A driving window built from a known truth.

    `scale_error` multiplies what the controller reports against reality;
    `feed_ohm` is genuine wiring resistance. Only one of them leaves a gap when
    the current reaches zero.
    """
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    for slot in range(40):
        amps_each = 12.0 * (slot % 10) / 9.0
        total = 2 * amps_each
        source = 12.40
        at_controller = source - total * feed_ohm
        shown = at_controller / scale_error
        for dev in (1, 2):
            spark[(dev, sa.STATUS_0_API)][slot].append(
                status0(0.5, shown, amps_each))
        raw = int(round((source - 4.0) / 0.05))
        foreign[(8, 4, 0x52, 0)][slot].append(
            bytes([0, 0, 0, 0, 0, 0, raw, 0]))
    return spark, foreign


def test_a_pure_wiring_drop_leaves_no_gap_at_zero_current(pdpc, capsys):
    spark, foreign = _loaded_window(scale_error=1.0, feed_ohm=0.020)
    volts, amps, _, _ = pdpc.spark_series(spark)
    d = pdpc.report_gap_vs_current(foreign, volts, amps, "u8@6")
    assert abs(d["intercept"]) < 0.1, d
    assert d["slope"] > 0.010, d          # the resistance is recovered
    assert "gap is wiring" in capsys.readouterr().out


def test_a_scale_error_leaves_a_gap_at_zero_current(pdpc, capsys):
    spark, foreign = _loaded_window(scale_error=1.0664, feed_ohm=0.0)
    volts, amps, _, _ = pdpc.spark_series(spark)
    d = pdpc.report_gap_vs_current(foreign, volts, amps, "u8@6")
    assert d["intercept"] > 0.5, d
    assert abs(d["slope"]) < 0.005, d     # no resistance to find
    assert "That is a scale error" in capsys.readouterr().out


def test_the_fit_separates_a_scale_error_from_wiring_when_both_are_present(pdpc):
    """The case that actually obtains: some real resistance AND a bad scale."""
    spark, foreign = _loaded_window(scale_error=1.0664, feed_ohm=0.020)
    volts, amps, _, _ = pdpc.spark_series(spark)
    d = pdpc.gap_vs_current(foreign, volts, amps, "u8@6")
    assert d["intercept"] > 0.5, d        # the scale half survives
    assert d["slope"] > 0.010, d          # the wiring half is still recovered


def test_the_gap_fit_says_so_when_it_cannot_run(pdpc, capsys):
    spark, foreign = _loaded_window(scale_error=1.0, feed_ohm=0.0)
    volts, amps, _, _ = pdpc.spark_series(spark)
    assert pdpc.report_gap_vs_current(foreign, volts, amps, "u8@99") is None
    assert "not enough shared bins" in capsys.readouterr().out


def test_the_meter_check_no_longer_blames_the_scale_on_its_own(pdpc, capsys):
    """The panel sits upstream of the controllers and so did the meter, so their
    agreeing proves nothing about which side is wrong."""
    spark, foreign = _rest_window(panel_raw=164, shown_volts=11.54)
    volts, _, _, _ = pdpc.spark_series(spark)
    pdpc.report_field_against_meter(foreign, volts, "u8@6", 12.20)
    out = capsys.readouterr().out
    assert "ALONE does not say" in out
    assert "gap-against-current" in out


# -- the general guard: no debug tool may read a key no decoder defines ------

DECODERS = ("decode_status_0", "decode_status_1",
            "decode_legacy_status_0", "decode_legacy_status_1")
DECODER_KEYS = {n: frozenset(getattr(sa, n)(b"\x00" * 8)) for n in DECODERS}
STATUS_0_KEYS = DECODER_KEYS["decode_status_0"]


def _decoder_readers(tree):
    """(function, keys it may legitimately read) for each function that decodes.

    A function that calls more than one decoder may read any of their keys, so
    the allowed set is the union over the decoders it actually calls.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = {getattr(c.func, "attr", None)
                  for c in ast.walk(node) if isinstance(c, ast.Call)}
        used = called & set(DECODERS)
        if used:
            yield node, frozenset().union(*(DECODER_KEYS[n] for n in used)), used


@pytest.mark.parametrize(
    "tool", sorted((REPO / "tools").glob("*.py")), ids=lambda p: p.name)
def test_no_debug_tool_reads_a_decoder_key_that_does_not_exist(tool):
    """A renamed decoder key is silent: .get() returns None and the tool prints
    an empty result that reads like a real negative finding."""
    tree = ast.parse(tool.read_text())
    for fn, allowed, used in _decoder_readers(tree):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            if getattr(call.func, "attr", None) not in ("get", "__getitem__"):
                continue
            for arg in call.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert arg.value in allowed, (
                        f"{tool.name}:{call.lineno} in {fn.name}() reads "
                        f"'{arg.value}', which none of {sorted(used)} returns. "
                        "This is None at runtime and says nothing")


def test_the_decoder_guard_has_real_key_sets():
    """Guards the guard: an empty key set makes every check above vacuous."""
    for name, keys in DECODER_KEYS.items():
        assert len(keys) >= 4, f"{name} returned {keys}"
    assert DECODER_KEYS["decode_legacy_status_1"] >= {"voltage_v", "current_a"}
    assert "is_beacon" in DECODER_KEYS["decode_legacy_status_0"]
    assert "is_beacon" not in STATUS_0_KEYS


def test_the_key_guard_would_catch_the_original_bug():
    """Mutation in-test: the guard must reject the names that shipped."""
    assert "bus_voltage" not in STATUS_0_KEYS
    assert "output_current" not in STATUS_0_KEYS
    assert {"voltage_v", "current_a", "applied_output"} <= STATUS_0_KEYS
