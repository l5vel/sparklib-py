#!/usr/bin/env python3
"""Does the power-distribution panel on this bus report anything useful?

WHY THIS EXISTS

  A CTRE PDP 4.0 shares `can0` on rig-max, broadcasting ten frames at
  40 Hz. It was dismissed as carrying almost no information, because six of the
  ten were byte-for-byte constant over 480 samples. That sample was taken on an
  IDLE robot. A panel measuring nothing correctly reports nothing, and on a bus
  where the rail never moves a field holding a steady 12.8 V is indistinguishable
  from padding. The conclusion was about the sample, not the device.

  It matters because the legacy telemetry scales have never been checked against
  an instrument. REV publish 0.00732600 V and 0.03663003 A per count and nothing
  here has confirmed them. A panel on the same bus, measuring the same rail,
  would settle the voltage half without anyone touching a probe.

WHAT THIS DOES

  Listens. It sends nothing and starts no heartbeat, so it cannot move a motor.
  Over one window it decodes every SPARK's bus voltage, current and applied
  output, and it decodes every frame from a NON-REV-motor device into candidate
  numeric fields: each byte, each 16-bit pair in both endiannesses, and the
  10-bit packed layout CTRE use for channel currents. Then it correlates each
  candidate against the SPARK-reported rail voltage and against total SPARK
  current, and prints what tracks what.

  IT READS BOTH FIRMWARE GENERATIONS, because frame layout keys on generation
  and not on product. Firmware 25+ carries the output, volts and amps together
  in STATUS_0 at api 0x2E0. Pre-25 splits them: the applied output is in 0x060
  and the volts and amps are in 0x061. rig-max is eight MAX on 24.0.1 and reports
  only on the legacy pair, so a 25+-only reader decodes nothing there, which is
  exactly the bus that has a panel worth measuring.

  Bins default to 200 ms because this package throttles pre-25 status 1 to
  100 ms, so a bin holds about two samples per controller.

  Drive the base while it runs. The rail has to MOVE for any of this to work: a
  field that follows the sag is the bus voltage, and channels that follow the
  wheels are the currents.

WHAT A RESULT MEANS

  A candidate correlating strongly with SPARK bus voltage is the panel's own
  measurement of the same rail, and the two scales can be compared directly.
  A candidate correlating with total SPARK current is a channel sum, and the
  comparison is INDIRECT: a PDP channel measures SUPPLY current into a SPARK
  while the SPARK reports PHASE current, and the two differ by roughly the duty
  cycle. Treat a current match as a constraint, not as a calibration.

  The self-check runs whether or not a panel is present. It correlates each
  SPARK's own reported current against its own applied output, which must track
  on a bus that is driving. If the self-check is flat, the window caught no load
  and no other result in the run means anything.

    uv run python tools/spark_pdp_correlate.py --seconds 30
    uv run python tools/spark_pdp_correlate.py --seconds 30 --bin-ms 200
"""
from __future__ import annotations

import argparse
import collections
import math
import sys
import time

import can

from sparklib import admin as sa

REV_MFR = 5
MOTOR_CONTROLLER = 2

# SocketCAN loops the host's own sent frames back to other sockets, so a capture
# taken while the drive stack runs sees both enable heartbeats. 0x02052C80 is
# already a REV motor id; 0x01011840 would otherwise report as a foreign device.
HOST_FRAMES = frozenset({0x01011840, 0x02052C80})


def device_fields(arb):
    """(device_type, manufacturer, api, device_id) from an FRC arbitration id."""
    return (arb >> 24) & 0x1F, (arb >> 16) & 0xFF, (arb >> 6) & 0x3FF, arb & 0x3F


def candidates(data):
    """{name: value} numeric readings a frame might be carrying.

    The layout is unknown, so every plausible one is tried and correlation picks
    the winner. Nothing here interprets a value; it only has to be monotonic in
    whatever the panel is measuring.
    """
    out = {}
    b = bytes(data)
    for i, v in enumerate(b):
        out[f"u8@{i}"] = v
    for i in range(len(b) - 1):
        out[f"u16le@{i}"] = int.from_bytes(b[i:i + 2], "little")
        out[f"u16be@{i}"] = int.from_bytes(b[i:i + 2], "big")
    # CTRE pack six 10-bit channel currents into eight bytes.
    if len(b) >= 8:
        bits = int.from_bytes(b, "big")
        for ch in range(6):
            out[f"u10#{ch}"] = (bits >> (64 - 10 * (ch + 1))) & 0x3FF
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None                      # a constant series correlates with nothing
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def collect(channel, seconds, bin_ms):
    """Bin every frame by arrival time. Returns (spark_bins, foreign_bins)."""
    bus = can.Bus(interface="socketcan", channel=channel)
    spark = collections.defaultdict(lambda: collections.defaultdict(list))
    foreign = collections.defaultdict(lambda: collections.defaultdict(list))
    start = time.time()
    end = start + seconds
    try:
        while time.time() < end:
            m = bus.recv(timeout=max(0.0, end - time.time()))
            if m is None or m.is_remote_frame:
                continue
            dtype, mfr, api, dev = device_fields(m.arbitration_id)
            slot = int((time.time() - start) * 1000 // bin_ms)
            if mfr == REV_MFR and dtype == MOTOR_CONTROLLER:
                spark[(dev, api)][slot].append(bytes(m.data))
            elif m.arbitration_id not in HOST_FRAMES:
                foreign[(dtype, mfr, api, dev)][slot].append(bytes(m.data))
    finally:
        bus.shutdown()
    return spark, foreign


LEGACY_STATUS_1_API = sa.API_SETS[sa.GEN_PRE25]["status_1"]

# Frame layout keys on FIRMWARE GENERATION, not on product. 25+ carries output,
# volts and amps in one frame; pre-25 splits them across two.
TELEMETRY_APIS = (sa.STATUS_0_API, sa.LEGACY_STATUS_0_API, LEGACY_STATUS_1_API)


def readings(api, data):
    """(applied_output, voltage_v, current_a) from whichever frame this is."""
    if api == sa.STATUS_0_API:
        r = sa.decode_status_0(data) or {}
        if r.get("implausible"):
            return None, None, None
        return r.get("applied_output"), r.get("voltage_v"), r.get("current_a")
    if api == sa.LEGACY_STATUS_0_API:
        r = sa.decode_legacy_status_0(data) or {}
        if r.get("is_beacon"):
            return None, None, None          # 25+ pins every signal in 0x060
        return r.get("applied_output"), None, None
    if api == LEGACY_STATUS_1_API:
        r = sa.decode_legacy_status_1(data) or {}
        return None, r.get("voltage_v"), r.get("current_a")
    return None, None, None


def spark_series(spark):
    """Per-bin rail voltage and total current, and per-device output and amps."""
    per_bin_v = collections.defaultdict(list)
    per_bin_a = collections.defaultdict(list)
    applied = collections.defaultdict(lambda: collections.defaultdict(list))
    dev_amps = collections.defaultdict(lambda: collections.defaultdict(list))
    for (dev, api), bins in spark.items():
        if api not in TELEMETRY_APIS:
            continue
        for slot, frames in bins.items():
            for d in frames:
                o, v, a = readings(api, d)
                if v is not None:
                    per_bin_v[slot].append(v)
                if a is not None:
                    per_bin_a[slot].append(a)
                    dev_amps[dev][slot].append(a)
                if o is not None:
                    applied[dev][slot].append(abs(o))
    mean = lambda b: {s: sum(x) / len(x) for s, x in b.items()}
    return (mean(per_bin_v),
            {s: sum(x) for s, x in per_bin_a.items()},
            {d: mean(b) for d, b in applied.items()},
            {d: mean(b) for d, b in dev_amps.items()})


def aligned(a, b):
    """The two series over the bins they share."""
    keys = sorted(set(a) & set(b))
    return [a[k] for k in keys], [b[k] for k in keys]


def report_self_check(volts, applied, dev_amps):
    print("\nSELF-CHECK: does each SPARK's own current track its own output?")
    print("  A flat result means the window caught no load, and nothing else "
          "in this run means anything.")
    moved = False
    for dev in sorted(dev_amps):
        xs, ys = aligned(applied.get(dev, {}), dev_amps[dev])
        r = pearson(xs, ys)
        span = (max(ys) - min(ys)) if ys else 0.0
        if r is not None and span > 0.5:
            moved = True
        print(f"  id {dev:>3}  r={('  n/a' if r is None else f'{r:+.2f}')}  "
              f"current span {span:5.2f} A  bins {len(xs)}")
    v = list(volts.values())
    print(f"  rail voltage span across the window: "
          f"{(max(v) - min(v)) if v else 0.0:.3f} V")
    print("  A WIDE SPAN WITH A WEAK r IS NORMAL AND STILL COUNTS AS LOAD. "
          "Current follows torque,")
    print("  not duty cycle, so a wheel accelerating draws hard at a modest "
          "output. The span is the")
    print("  gate here; r only says how tightly the two moved together.")
    return moved


def report_no_telemetry(spark):
    """SPARK frames arrived but none of them decoded into readings."""
    seen = sorted({api for _, api in spark})
    print("\nNO TELEMETRY DECODED, though SPARK frames did arrive.")
    print("  apis seen: " + ", ".join(f"0x{a:03X}" for a in seen))
    print(f"  wanted 0x{sa.STATUS_0_API:03X} on firmware 25+, or "
          f"0x{sa.LEGACY_STATUS_0_API:03X} and 0x{LEGACY_STATUS_1_API:03X} "
          "on pre-25.")
    if sa.LEGACY_STATUS_0_API in seen and LEGACY_STATUS_1_API not in seen:
        print("  0x061 carries volts and amps on pre-25 and it is absent. Its "
              "period may be off:")
        print("  run `uv run spark throttle`, or start the drive stack, then "
              "try again.")


# Affine maps a panel might use to encode a rail voltage. The first is CTRE's
# documented PDP bus-voltage scaling; the rest are the obvious alternatives.
VOLT_MAPS = (
    ("x*0.05+4.0", 0.05, 4.0),
    ("x*0.05", 0.05, 0.0),
    ("x*0.1", 0.1, 0.0),
    ("x*0.01", 0.01, 0.0),
    ("x*0.0073260", 0.0073260073260073, 0.0),
    ("x*0.125", 0.125, 0.0),
    ("x/128", 1 / 128.0, 0.0),
)


def voltage_matches(foreign, volts, tol=0.30):
    """Panel fields that READ the rail the SPARKs read, under some scaling.

    Correlation needs the rail to swing, and a shallow sag identifies nothing.
    An absolute match needs no swing: a field whose mean lands on the measured
    rail is a candidate. Each hit carries its correlation against the rail and
    the ratio of its swing to the rail's, which is what separates a real reading
    from a number that happens to land in range. Returns (rows, tested).
    """
    vs = list(volts.values())
    if not vs:
        return [], 0
    rail = sum(vs) / len(vs)
    rail_span = max(vs) - min(vs)
    rows, tested = [], 0
    for (dtype, mfr, api, dev), bins in sorted(foreign.items()):
        series = collections.defaultdict(dict)
        for slot, frames in bins.items():
            for name, val in candidates(frames[-1]).items():
                series[name][slot] = val
        for name, by_slot in series.items():
            xs = list(by_slot.values())
            lo, hi, mean = min(xs), max(xs), sum(xs) / len(xs)
            r = pearson(*aligned(volts, by_slot))
            for label, scale, offset in VOLT_MAPS:
                tested += 1
                v = mean * scale + offset
                span = (hi - lo) * scale
                if abs(v - rail) > tol:
                    continue
                # A field that sits still measures no rail, and one swinging far
                # wider than the rail is measuring something else that happens
                # to land in range.
                if span < 0.05 or span > max(5 * rail_span, 0.5):
                    continue
                rows.append({"off": abs(v - rail), "reads": v, "span": span,
                             "r": r,
                             "ratio": (span / rail_span) if rail_span else None,
                             "dtype": dtype, "mfr": mfr, "api": api, "dev": dev,
                             "field": name, "scaling": label})
    rows.sort(key=lambda d: (-abs(d["r"] or 0.0), d["off"]))
    return rows, tested


def report_absolute_voltage(foreign, volts, top):
    rows, tested = voltage_matches(foreign, volts)
    vs = list(volts.values())
    rail = (sum(vs) / len(vs)) if vs else 0.0
    span = (max(vs) - min(vs)) if vs else 0.0
    print(f"\nDOES ANY PANEL FIELD READ THE RAIL? SPARKs say {rail:.2f} V mean, "
          f"swinging {span:.2f} V.")
    if not rows:
        print(f"  none of {tested} field-and-scaling combinations lands within "
              "0.30 V of it while also moving with the rail.")
        print("  So this window identifies no voltage field. Either the panel "
              "reports the rail somewhere")
        print("  this decoder does not reach, or it does not broadcast one.")
        return False
    print(f"  {len(rows)} of {tested} field-and-scaling combinations land "
          "within 0.30 V of it, ranked by how")
    print("  well each one TRACKS the rail, which is what tells a reading from "
          "a coincidence.")
    print(f"  {'devtype/mfr':<12} {'api':<7} {'dev':<4} {'field':<10} "
          f"{'scaling':<14} {'reads':>8} {'swing':>7} {'r':>6} {'swing/rail':>11}")
    for d in rows[:top]:
        r = "   n/a" if d["r"] is None else f"{d['r']:+.2f}"
        ratio = "        n/a" if d["ratio"] is None else f"{d['ratio']:10.2f}x"
        who = f"{d['dtype']}/{d['mfr']}"
        print(f"  {who:<12} 0x{d['api']:03X}   {d['dev']:<4} {d['field']:<10} "
              f"{d['scaling']:<14} {d['reads']:7.2f}V {d['span']:6.2f}V {r} {ratio}")
    print("\n  READ r FIRST. A field measuring this rail correlates near +1 "
          "with it whatever its")
    print("  scale, because correlation does not care about amplitude. A hit "
          "with r near zero lands")
    print("  in range by arithmetic and measures something else.")
    print("  swing/rail BELOW 1 IS NOT DISQUALIFYING. A panel sits nearer the "
          "battery than a")
    print("  controller does, so wire drop makes the controllers seem to sag "
          "further than the panel.")
    return True


def report_foreign(foreign, volts, amps, top):
    if not foreign:
        print("\nNo non-REV-motor device transmitted on this bus during the "
              "window.")
        print("  rig-flex carries no power-distribution panel; rig-max does. Run "
              "this there.")
        return
    print(f"\nFOREIGN DEVICES: {len(foreign)} arbitration id(s)")
    rows = []
    for (dtype, mfr, api, dev), bins in sorted(foreign.items()):
        series = collections.defaultdict(dict)
        for slot, frames in bins.items():
            for name, val in candidates(frames[-1]).items():
                series[name][slot] = val
        for name, s in series.items():
            for label, target in (("bus_voltage", volts), ("total_current", amps)):
                xs, ys = aligned(target, s)
                r = pearson(xs, ys)
                if r is not None and abs(r) >= 0.5:
                    rows.append((abs(r), r, dtype, mfr, api, dev, name, label,
                                 len(xs)))
    if not rows:
        print("  nothing correlated above 0.5 with rail voltage or total "
              "current.")
        print("  If the self-check showed load, the panel is not reporting "
              "either on this bus.")
        return
    rows.sort(reverse=True)
    print(f"  {'devtype/mfr':<12} {'api':<7} {'dev':<4} {'field':<10} "
          f"{'tracks':<14} {'r':>6}  bins")
    for _, r, dtype, mfr, api, dev, name, label, n in rows[:top]:
        print(f"  {f'{dtype}/{mfr}':<12} 0x{api:03X}   {dev:<4} {name:<10} "
              f"{label:<14} {r:+.2f}  {n}")
    print("\n  A voltage match is DIRECT: both devices measure the same rail, "
          "so the scales compare.")
    print("  A current match is INDIRECT: a panel channel is SUPPLY current "
          "and a SPARK reports PHASE")
    print("  current, and the two differ by roughly the duty cycle.")
    print("  SIGN SEPARATES THEM. The rail sags as current rises, so one field "
          "shows up on both")
    print("  lines with opposite signs. A voltage field is POSITIVE against "
          "bus_voltage; a current")
    print("  channel is POSITIVE against total_current. Read the sign, not the "
          "magnitude.")
    print("  Overlapping candidates share bytes, so u8, u16 and u10 rows repeat "
          "one field. The")
    print("  widest field with the strongest r is the layout; the rest are its "
          "fragments.")


# CTRE publish the PDP bus voltage as raw*0.05 + 4.0. A fit landing here is two
# independently specified scales agreeing.
CTRE_VOLT_SCALE, CTRE_VOLT_OFFSET = 0.05, 4.0


def rail_fits(foreign, volts, min_r=0.5):
    """Fit the SPARK rail against each panel field that tracks it.

    Correlation says a field measures this rail. The fit says in what units, by
    solving for the scale instead of guessing it from a list.
    """
    rows = []
    for (dtype, mfr, api, dev), bins in sorted(foreign.items()):
        series = collections.defaultdict(dict)
        for slot, frames in bins.items():
            for name, val in candidates(frames[-1]).items():
                series[name][slot] = val
        for name, by_slot in series.items():
            xs, ys = aligned(by_slot, volts)
            r = pearson(xs, ys)
            if r is None or r < min_r:
                continue                 # a rail field RISES with the rail
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx == 0:
                continue
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            intercept = my - slope * mx
            resid = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
            rms = math.sqrt(sum(e * e for e in resid) / n)
            rows.append({"api": api, "dev": dev, "dtype": dtype, "mfr": mfr,
                         "field": name, "r": r, "slope": slope,
                         "intercept": intercept, "rms": rms, "n": n,
                         "raw_mean": mx, "raw_span": max(xs) - min(xs)})
    rows.sort(key=lambda d: -d["r"])
    return rows


def report_rail_fit(foreign, volts, top):
    rows = [d for d in rail_fits(foreign, volts) if abs(d["intercept"]) <= 40.0]
    print("\nWHAT SCALE IS THE PANEL USING? Solved, not guessed.")
    if not rows:
        print("  no panel field rises with the rail at 0.5 or better with a "
              "physically possible offset,")
        print("  so there is nothing to fit.")
        return False
    print(f"  {'api':<7} {'field':<10} {'r':>6} {'R2':>5} {'raw mean':>9} "
          f"{'raw span':>9} {'V/count':>9} {'offset':>8} {'resid':>7}")
    for d in rows[:top]:
        print(f"  0x{d['api']:03X}   {d['field']:<10} {d['r']:+.2f} "
              f"{d['r'] ** 2:5.2f} {d['raw_mean']:9.1f} {d['raw_span']:9.1f} "
              f"{d['slope']:9.5f} {d['intercept']:7.2f}V {d['rms']:6.3f}V")
    best = rows[0]
    ctre = best["raw_mean"] * CTRE_VOLT_SCALE + CTRE_VOLT_OFFSET
    vs = list(volts.values())
    rail = sum(vs) / len(vs)
    counts = rail / sa.VOLT_PER_COUNT
    implied = ctre / counts if counts else 0.0
    print(f"\n  RAW MEAN IS EXACT, not fitted: least squares passes through it, "
          "so the comparison below")
    print("  needs no slope and carries none of the slope's noise.")
    print(f"\n  IF {best['field']} of api 0x{best['api']:03X} IS the bus voltage "
          f"under CTRE's raw*{CTRE_VOLT_SCALE}+{CTRE_VOLT_OFFSET}:")
    print(f"    panel reads          {ctre:6.2f} V")
    print(f"    controllers read     {rail:6.2f} V   "
          f"({(rail / ctre - 1) * 100:+.1f} %)")
    print(f"    so a controller count is worth {implied:.10f} V")
    print(f"    REV publish                    {sa.VOLT_PER_COUNT:.10f} V "
          f"({(implied / sa.VOLT_PER_COUNT - 1) * 100:+.1f} %)")
    print(f"    1/128, used here before, is "
          f"{1 / 128:.10f} V "
          f"({(implied / (1 / 128) - 1) * 100:+.1f} %)")
    print("\n  TWO ASSUMPTIONS CARRY THAT, and neither is measured here: that "
          "this field is the bus")
    print("  voltage, and that CTRE's published scaling applies to this panel. "
          "Treat it as a lead.")
    print(f"  READ R2, NOT r. R2 = {best['r'] ** 2:.2f} means the panel field "
          "explains that fraction of the")
    print("  rail's movement. The FITTED V/count is a LOWER BOUND: noise in the "
          "panel field biases a")
    print("  least-squares slope toward zero, while the raw mean above is "
          "unaffected.")
    print("  THIS IS NOT A CALIBRATION OF THE CONTROLLER. The comparison uses "
          "the controller's own")
    print("  reading as its reference. Only a multimeter breaks the circle.")
    return True


def report_field_against_meter(foreign, volts, field, meter):
    """Read one named field straight out, with no correlation needed.

    Correlation identifies a field only while the rail moves. Once it IS
    identified, its raw value stands on its own, and AT REST it can be compared
    against a meter with no wire drop anywhere in the path to argue about.
    """
    print(f"\nFIELD {field} READ DIRECTLY, no correlation required.")
    rows = []
    for (dtype, mfr, api, dev), bins in sorted(foreign.items()):
        xs = []
        for frames in bins.values():
            v = candidates(frames[-1]).get(field)
            if v is not None:
                xs.append(v)
        if xs:
            rows.append((api, dev, sum(xs) / len(xs), min(xs), max(xs), len(xs)))
    if not rows:
        print(f"  no foreign frame carries {field}.")
        return False
    print(f"  {'api':<7} {'dev':<4} {'raw mean':>9} {'raw min':>8} {'raw max':>8} "
          f"{'CTRE volts':>11}")
    for api, dev, mean, lo, hi, n in rows:
        print(f"  0x{api:03X}   {dev:<4} {mean:9.1f} {lo:8d} {hi:8d} "
              f"{mean * CTRE_VOLT_SCALE + CTRE_VOLT_OFFSET:10.2f} V")
    if meter is None:
        print("\n  Pass --meter VOLTS with a multimeter reading taken AT REST to "
              "turn this into a check.")
        return True
    vs = list(volts.values())
    rail = (sum(vs) / len(vs)) if vs else 0.0
    best = min(rows, key=lambda r: abs(r[2] * CTRE_VOLT_SCALE
                                       + CTRE_VOLT_OFFSET - meter))
    ctre = best[2] * CTRE_VOLT_SCALE + CTRE_VOLT_OFFSET
    print(f"\n  meter says              {meter:6.2f} V")
    print(f"  panel 0x{best[0]:03X} {field} says {ctre:6.2f} V   "
          f"({ctre - meter:+.2f} V)")
    print(f"  controllers say         {rail:6.2f} V   ({rail - meter:+.2f} V)")
    # Controllers first: their agreeing is decisive, and the panel agreeing is
    # not, because the panel and the meter both sit upstream of them.
    if abs(rail - meter) < 0.25:
        print("\n  The controllers agree with the meter, so REV's scale stands "
              "and the panel lead was false.")
    elif abs(ctre - meter) < 0.25:
        print("\n  The panel agrees with the meter, so CTRE's scaling holds "
              "and the panel is a usable")
        print("  instrument. That ALONE does not say whether the controllers "
              "are miscalibrated or")
        print("  simply fed less voltage, because the panel sits upstream of "
              "them and so did the meter.")
        print("  The gap-against-current table below is what separates those.")
    else:
        print("\n  NEITHER agrees with the meter, so something in the chain is "
              "not what it is assumed to be.")
    return True


def gap_vs_current(foreign, volts, amps, field):
    """Fit the panel-minus-controller voltage gap against total current.

    This is what separates a wiring drop from a scale error, and it needs only
    one driving window. A resistive drop is proportional to CURRENT, so its gap
    goes to zero as the current does. A scale error is proportional to VOLTAGE,
    so its gap survives at zero current. The intercept tells them apart.
    """
    best, best_n = None, 0
    for (dtype, mfr, api, dev), bins in sorted(foreign.items()):
        per_slot = {}
        for slot, frames in bins.items():
            raw = candidates(frames[-1]).get(field)
            if raw is not None:
                per_slot[slot] = raw * CTRE_VOLT_SCALE + CTRE_VOLT_OFFSET
        keys = sorted(set(per_slot) & set(volts) & set(amps))
        if len(keys) > best_n:
            best, best_n = (api, dev, per_slot), len(keys)
    if best is None or best_n < 3:
        return None
    api, dev, panel = best
    keys = sorted(set(panel) & set(volts) & set(amps))
    xs = [amps[k] for k in keys]
    ys = [panel[k] - volts[k] for k in keys]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return {"api": api, "dev": dev, "intercept": my - slope * mx,
            "slope": slope, "r": pearson(xs, ys), "n": n,
            "amp_lo": min(xs), "amp_hi": max(xs)}


def report_gap_vs_current(foreign, volts, amps, field):
    d = gap_vs_current(foreign, volts, amps, field)
    print("\nDROP OR SCALE? Fit the panel-minus-controller gap against current.")
    if d is None:
        print(f"  not enough shared bins carrying {field} to fit.")
        return None
    print(f"  using 0x{d['api']:03X} {field} over {d['n']} bins, "
          f"{d['amp_lo']:.0f} to {d['amp_hi']:.0f} A")
    print(f"    gap at ZERO current   {d['intercept']:+.2f} V")
    print(f"    gap per amp           {d['slope'] * 1000:+.2f} mV/A "
          f"({d['slope'] * 1000:+.1f} milliohm of feed)")
    print("\n  A RESISTIVE DROP HAS NO GAP AT ZERO CURRENT. If the intercept "
          "is near zero the")
    print("  controllers are simply fed less and their scale is fine. If the "
          "intercept is large the")
    print("  gap survives with no current flowing, which no resistance can do, "
          "and the scale is wrong.")
    if abs(d["intercept"]) > 0.3:
        print(f"\n  THE INTERCEPT IS {d['intercept']:+.2f} V, so the gap does "
              "NOT vanish at zero current.")
        print("  That is a scale error. The slope above is the real wiring "
              "resistance, separately.")
    else:
        print("\n  The intercept is small, so the gap is wiring and the "
              "controller scale is fine.")
    return d


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--channel", default=None)
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--bin-ms", type=int, default=200)
    p.add_argument("--top", type=int, default=12)
    p.add_argument("--field", default="u8@6",
                   help="panel field to read out directly (default u8@6)")
    p.add_argument("--meter", type=float, default=None,
                   help="multimeter reading, taken AT REST, to check against")
    a = p.parse_args(argv)

    channel = a.channel
    if channel is None:
        from sparklib.config import get as _spark_config
        channel = _spark_config().can.interface

    print(f"listening on {channel} for {a.seconds:.0f}s in {a.bin_ms} ms bins; "
          "sending nothing")
    print("DRIVE THE BASE while this runs, or the rail never moves and nothing "
          "can be identified.")
    spark, foreign = collect(channel, a.seconds, a.bin_ms)
    if not spark:
        print("\nno SPARK frames captured; is the bus awake and the rail on?")
        return 1
    volts, amps, applied, dev_amps = spark_series(spark)
    if not volts and not amps:
        report_no_telemetry(spark)
        return 1
    seen = sorted({api for _, api in spark} & set(TELEMETRY_APIS))
    print(f"\ntelemetry from {len(dev_amps)} controller(s) on "
          + ", ".join(f"0x{x:03X}" for x in seen)
          + f"; {len(volts)} of {int(a.seconds * 1000 / a.bin_ms)} bins carry a "
          "voltage")
    moved = report_self_check(volts, applied, dev_amps)
    report_foreign(foreign, volts, amps, a.top)
    checked = False
    if foreign:
        report_absolute_voltage(foreign, volts, a.top)
        report_rail_fit(foreign, volts, a.top)
        checked = (report_field_against_meter(foreign, volts, a.field, a.meter)
                   and a.meter is not None)
        report_gap_vs_current(foreign, volts, amps, a.field)
    if not moved:
        print("\nVERDICT: no load in this window, so nothing here is IDENTIFIED "
              "by correlation.")
        if checked:
            print("  The --meter check above does not need load and stands on "
                  "its own.")
            return 0
        print("  Re-run while driving, or pass --meter to check a known field "
              "at rest.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
