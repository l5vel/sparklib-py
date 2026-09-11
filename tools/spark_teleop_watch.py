#!/usr/bin/env python3
"""Watch what the drive stack commands and what the controllers do about it.

Purely passive. It opens a second socket on each bus and only ever receives, so
it can run alongside teleop without adding a frame to either wire.

The question it answers is the one STATUS_0 alone cannot: when a wheel does not
move, is the host not commanding it, or is the controller refusing? Those look
identical from the outside and have nothing in common as repairs. So this reads
three things at once and puts them on one line:

    commanded   the setpoint frame the host sent to that controller
    applied     what the controller says it is applying
    enabled     whether the enable heartbeat lists that CAN id at all

A controller commanded non-zero while applied output stays at zero is refusing.
One never commanded is a host-side problem. One missing from the heartbeat mask
was never going to move at all. Catalogue G3, CD 477176, is the first of those:
`.set()` commanding non-zero while `getAppliedOutput()` reads exactly 0.

It also watches the CANcoder bus, because a swerve module with no absolute
position is a module the stack may hold whatever the SPARKs are doing.

    uv run python tools/spark_teleop_watch.py          # then drive it
    Ctrl-C for the summary

Nothing here transmits, so it is safe to leave running for a whole session.
"""
from __future__ import annotations

import collections
import struct
import sys
import threading
import time

import can

from sparklib.config import get as _spark_config
from sparklib import admin as sa
from sparklib import cli as spark_cli

# A provisioned SPARK sends STATUS_0 every 10 ms, so this much silence means the
# socket is dead rather than the bus being quiet. Starting the drive stack
# rebinds the gs_usb adapter -- can0 went from ifindex 38 to 44 to 45 in
# one evening -- and a socket bound to the netdev that disappeared receives
# nothing forever without ever raising. That is how this watcher missed an entire
# teleop run: frames frozen, heartbeats 0, and no error anywhere.
REOPEN_AFTER_S = 3.0

# A peak is a single 10 ms sample and says almost nothing on its own. What
# matters for a motor is how long it spent up there, so every STATUS_0 sample is
# bucketed against the declared limits and reported as a dwell time. Thresholds
# from sparkflex_motor_defaults.yaml: Smart Current Stall Limit 80 A, Current
# Chop 115 A. The field itself saturates at 150.0 A, so anything in the top
# bucket is a lower bound.
CURRENT_BUCKETS_A = (80.0, 115.0)
STATUS_0_PERIOD_S = 0.010

# controller.py packs every SparkFlex setpoint into one frame: a float and
# a mode byte, 0 duty cycle, 1 velocity, 2 position.
SETPOINT_BASE = 0x02050080
SETPOINT_MODE = {0: "duty", 1: "vel", 2: "pos"}
# SparkBus._heartbeat_runnable. Byte d//8, bit d%8, is CAN id d.
HEARTBEAT = 0x02052C80
S0, S1 = sa.STATUS_0_API, sa.STATUS_1_API


class Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.cmd = {}           # dev -> (value, mode, at)
        self.max_cmd = collections.defaultdict(float)
        self.cmd_frames = collections.Counter()
        self.s0 = {}
        self.max_applied = collections.defaultdict(float)
        self.max_amps = collections.defaultdict(float)
        self.limits_seen = collections.defaultdict(set)
        self.faults_seen = collections.defaultdict(set)
        self.enabled_mask = None
        self.heartbeats = 0
        self.cancoder = collections.Counter()
        self.census = collections.Counter()   # every arbitration base seen
        self.frames = 0
        self.reopens = 0
        self.dwell = collections.defaultdict(collections.Counter)
        self.pegged = collections.defaultdict(int)
        self.stop = threading.Event()


def spark_reader(w, channel):
    bus = can.Bus(interface="socketcan", channel=channel)
    last = time.time()
    try:
        while not w.stop.is_set():
            try:
                m = bus.recv(timeout=0.2)
            except can.CanError:
                m = None
            if m is None:
                if time.time() - last > REOPEN_AFTER_S:
                    try:
                        bus.shutdown()
                    except Exception:                       # noqa: BLE001
                        pass
                    try:
                        bus = can.Bus(interface="socketcan", channel=channel)
                        with w.lock:
                            w.reopens += 1
                    except Exception:                       # noqa: BLE001
                        time.sleep(0.5)
                    last = time.time()
                continue
            last = time.time()
            arb, data = m.arbitration_id, bytes(m.data)
            with w.lock:
                w.frames += 1
                w.census[arb & ~0x3F] += 1
                if arb == HEARTBEAT and len(data) >= 8:
                    w.enabled_mask = data
                    w.heartbeats += 1
                    continue
                if (arb & ~0x3F) == SETPOINT_BASE and len(data) >= 5:
                    dev = arb & 0x3F
                    value = struct.unpack("<f", data[0:4])[0]
                    w.cmd[dev] = (value, SETPOINT_MODE.get(data[4], data[4]),
                                  time.time())
                    w.cmd_frames[dev] += 1
                    w.max_cmd[dev] = max(w.max_cmd[dev], abs(value))
                    continue
                if (arb >> 16) & 0xFF != sa.REV_MFR:
                    continue
                dev, api = arb & 0x3F, (arb >> 6) & 0x3FF
                if api == S0:
                    decoded = sa.decode_status_0(data)
                    if decoded:
                        w.s0[dev] = decoded
                        w.max_applied[dev] = max(w.max_applied[dev],
                                                 abs(decoded["applied_output"]))
                        amps = decoded["current_a"]
                        if "current_a" in decoded["implausible"]:
                            w.pegged[dev] += 1
                        else:
                            w.max_amps[dev] = max(w.max_amps[dev], amps)
                            for edge in CURRENT_BUCKETS_A:
                                if amps >= edge:
                                    w.dwell[dev][edge] += 1
                        for key, tag in (("hard_forward_limit", "FWD"),
                                         ("hard_reverse_limit", "REV")):
                            if decoded[key]:
                                w.limits_seen[dev].add(tag)
                elif api == S1:
                    decoded = sa.decode_status_1(data)
                    if decoded:
                        w.faults_seen[dev].update(decoded["faults"])
                        w.faults_seen[dev].update(decoded["sticky_faults"])
    finally:
        bus.shutdown()


def cancoder_reader(w, channel):
    try:
        bus = can.Bus(interface="socketcan", channel=channel, fd=True)
    except Exception as exc:                       # noqa: BLE001 - optional bus
        print(f"  (cancoder bus {channel} unavailable: {exc})")
        return
    try:
        while not w.stop.is_set():
            m = bus.recv(timeout=0.2)
            if m is not None:
                with w.lock:
                    w.cancoder[m.arbitration_id & 0x3F] += 1
    finally:
        bus.shutdown()


def enabled(w, dev):
    if w.enabled_mask is None:
        return "?"
    return "yes" if w.enabled_mask[dev // 8] & (1 << (dev % 8)) else "NO"


def render(w, roles, cancoder_ids):
    with w.lock:
        rows = [f"  {'id':>3} {'role':<10} {'enab':>4} {'commanded':>12} "
                f"{'applied':>8} {'amps':>6}  {'limits':<8} faults"]
        for dev in sorted(roles):
            value, mode, at = w.cmd.get(dev, (None, "", 0))
            fresh = value is not None and time.time() - at < 1.0
            cmd = f"{value:+.3f} {mode}" if fresh else (
                f"({value:+.3f})" if value is not None else "-")
            s0 = w.s0.get(dev)
            applied = f"{s0['applied_output']:+.3f}" if s0 else "-"
            amps = f"{s0['current_a']:.2f}" if s0 else "-"
            lim = ",".join(sorted(w.limits_seen[dev])) or "-"
            flt = ",".join(sorted(w.faults_seen[dev])) or "-"
            rows.append(f"  {dev:>3} {roles[dev]:<10} {enabled(w, dev):>4} "
                        f"{cmd:>12} {applied:>8} {amps:>6}  {lim:<8} {flt}")
        cc = ", ".join(f"{d}:{n}" for d, n in sorted(w.cancoder.items())) or "silent"
        setpoints = sum(w.cmd_frames.values())
        stack = ("DRIVE STACK IS RUNNING" if w.heartbeats else
                 "no enable heartbeat -- THE DRIVE STACK IS NOT RUNNING, so "
                 "nothing here can move")
        rows.append(f"\n  {stack}")
        rows.append(f"  frames {w.frames}   heartbeats {w.heartbeats}   "
                    f"setpoints {setpoints}   cancoder bus: {cc} "
                    f"(configured {cancoder_ids})")
        if w.reopens:
            rows.append(f"  reopened the SPARK socket {w.reopens} time(s) -- the "
                        "adapter was rebound under us")
    return "\n".join(rows)


def summary(w, roles):
    print("\n\n=== summary ===")
    print(f"  {'id':>3} {'role':<10} {'enab':>4} {'cmd frames':>11} "
          f"{'max cmd':>8} {'max applied':>12} {'max amps':>9}  limits   verdict")
    for dev in sorted(roles):
        n = w.cmd_frames[dev]
        mc, ma = w.max_cmd[dev], w.max_applied[dev]
        lim = ",".join(sorted(w.limits_seen[dev])) or "-"
        if enabled(w, dev) == "NO":
            verdict = "NOT IN THE ENABLE MASK -- it was never going to move"
        elif n == 0:
            verdict = "never commanded -- the host did not ask it to move"
        elif mc > 0.02 and ma < 0.005:
            verdict = ("COMMANDED AND DID NOT APPLY -- the controller refused"
                       + (f" (limits {lim})" if lim != "-" else ""))
        elif mc > 0.02:
            verdict = "commanded and applied"
        else:
            verdict = "only ever commanded zero"
        print(f"  {dev:>3} {roles[dev]:<10} {enabled(w, dev):>4} {n:>11} "
              f"{mc:>8.3f} {ma:>12.3f} {w.max_amps[dev]:>9.2f}  {lim:<8} {verdict}")
    known = {SETPOINT_BASE: "SETPOINT", HEARTBEAT: "enable heartbeat",
             sa.PARAM_WRITE: "PARAMETER_WRITE", sa.PARAM_WRITE_RESP: "param resp",
             sa.CLEAR_FAULTS: "CLEAR_FAULTS", sa.GET_FIRMWARE: "GET_FIRMWARE",
             sa.PERSIST: "PERSIST", sa.PERSIST_RESP: "persist resp",
             0x02050000 | (S0 << 6): "STATUS_0",
             0x02050000 | (S1 << 6): "STATUS_1",
             0x02050000 | (0x2F0 << 6): "UNIQUE_ID"}
    print(f"\n  every arbitration base seen on the SPARK bus "
          f"({w.frames} frames total):")
    for base, n in w.census.most_common(12):
        print(f"    0x{base:08X}  {n:>7}  {known.get(base, 'UNRECOGNISED')}")
    if w.heartbeats == 0:
        print("\n  NO ENABLE HEARTBEAT was seen. SparkBus sends 0x02052C80 every "
              "20 ms from the moment it is constructed, so the drive stack was "
              "not running for any of this window -- nothing could move, and "
              "every 'never commanded' above says only that. Start this watcher "
              "FIRST, leave it running, then start teleop and drive.")
    if any(w.dwell.values()):
        print(f"\n  time spent above each declared current limit, from "
              f"{STATUS_0_PERIOD_S * 1000:.0f} ms STATUS_0 samples:")
        if any(w.pegged.values()):
            print("\n  CURRENT FIELD PEGGED -- these samples are not readings. "
                  "The STATUS_0 current")
            print("  field is 12 bits at 0.03663 A/LSB, so it saturates at "
                  "150.00 A and cannot report")
            print("  what flows above that. Draw is >=150 A and unknown:")
            for dev in sorted(w.pegged):
                secs = w.pegged[dev] * STATUS_0_PERIOD_S
                print(f"    {dev:>3} {roles.get(dev, '-'):<10} "
                      f"{w.pegged[dev]:>6} samples  ~{secs:.1f}s pegged")
        head = "  ".join(f">{e:.0f}A" for e in CURRENT_BUCKETS_A)
        print(f"  {'id':>3} {'role':<10} {head}      (145 A is the top of the "
              "measurable range, so that column is a lower bound)")
        for dev in sorted(roles):
            counts = w.dwell.get(dev) or {}
            if not counts:
                continue
            cells = "  ".join(
                f"{counts.get(e, 0) * STATUS_0_PERIOD_S:5.2f}s"
                for e in CURRENT_BUCKETS_A)
            print(f"  {dev:>3} {roles[dev]:<10} {cells}")
        print("  A brief peak at the current limit is ordinary on a stationary "
              "wheel. Seconds above it is not, and is what would cook a motor.")

    if w.reopens:
        print(f"\n  The SPARK socket was reopened {w.reopens} time(s): the gs_usb "
              "adapter was rebound while this ran, which the drive stack does at "
              "startup. Frames between the rebind and the reopen were missed.")
    if not w.cancoder:
        print("  The CANcoder bus carried nothing for the whole run. A swerve "
              "module with no absolute position may be held by the stack "
              "whatever its SPARKs report.")


def main():
    roles = spark_cli._spark_roles()
    cancoder_ids = sorted(
        int(v) for v in vars(getattr(_spark_config().devices, "cancoder", object()))
        .values()) if hasattr(_spark_config().devices, "cancoder") else []
    spark_ch = _spark_config().can.interface
    cc_ch = getattr(_spark_config(), "cancoder_bus", None)

    w = Watch()
    threads = [threading.Thread(target=spark_reader, args=(w, spark_ch), daemon=True)]
    if cc_ch:
        threads.append(threading.Thread(target=cancoder_reader, args=(w, cc_ch),
                                        daemon=True))
    for t in threads:
        t.start()

    print(f"watching {spark_ch}"
          + (f" and {cc_ch}" if cc_ch else "")
          + " passively -- drive it now, Ctrl-C for the summary\n")
    try:
        while True:
            sys.stdout.write("\033[H\033[J" if sys.stdout.isatty() else "\n")
            print(render(w, roles, cancoder_ids))
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        w.stop.set()
        for t in threads:
            t.join(timeout=2.0)
        summary(w, roles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
