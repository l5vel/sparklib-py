"""Passive device-id sweep of a whole CAN bus, every manufacturer included.

    uv run python tools/can_id_sweep.py
    uv run python tools/can_id_sweep.py --channels can1,can0 --duration 3
    uv run python tools/can_id_sweep.py --range 1-63

`spark status` answers a narrower question on purpose: it filters to REV motor
controllers, so a CTRE CANcoder, a power distribution panel or a squatter of any
other make never shows up there. This sweep decodes the device id out of every
frame it hears and reports whatever is broadcasting, which is the question you
have when an id answers and you do not yet know what is answering on it.

REV SPARK and CTRE devices both use FRC 29-bit CAN addressing, where the device
id is the low 6 bits of the arbitration id:

    arbitration_id = (device_type << 24) | (manufacturer << 16) | (api << 6) | id
    device_id      = arbitration_id & 0x3F          # 0..63
    manufacturer   = (arbitration_id >> 16) & 0xFF  # 4 = CTRE, 5 = REV
    device_type    = (arbitration_id >> 24) & 0x1F

Only a 29-bit frame that a device transmitted carries a device id at all, so
error frames, standard-id frames and frames this host sent are tallied
separately instead of being decoded. Without that split a socketcan
CAN_ERR_TX_TIMEOUT reports as "device 1", and any frame another process on this
host puts on the bus reports as a device that is not there.

This tool never transmits. socketcan local loopback still delivers what other
processes send, so a LIVE row means something is broadcasting and nothing more:
a motor can read LIVE here and still not actuate while host TX is being dropped.

By default it sweeps both configured buses, can.interface for the SPARKs and
cancoder.bus for the CANivore, then labels each live id with its role from the
devices groups. Ids are unique per bus and not per robot, so the devices.cancoder
group is looked for on cancoder.bus and every other group on can.interface, and
an id answering on one bus never stands in for the same id on the other. A
device whose bus was never listened on, or that sits outside --range, is
reported as not looked for instead of as missing.

Each bus must already be UP at its own bitrate. A channel that is down gets
skipped, with the `ip link set ... up` command that brings it up printed.

Exit status: 0 when every configured device answered on its own bus, 1 when one
did not answer or was never looked for, 2 when the run was refused before any
listening, 130 when Ctrl-C cut the sweep short.
"""

import argparse
import collections
import math
import subprocess
import sys
import time

import can

from sparklib import config as cfg
from sparklib import netdev

_MFR_NAMES = {4: "CTRE", 5: "REV"}

# socketcan error classes, from linux/can/error.h.
_ERR_CLASSES = {
    0x001: "TX_TIMEOUT", 0x002: "LOSTARB", 0x004: "CRTL", 0x008: "PROT",
    0x010: "TRX", 0x020: "ACK", 0x040: "BUSOFF", 0x080: "BUSERROR",
    0x100: "RESTARTED", 0x200: "CNT",
}


def _err_classes(arb):
    """Names of the error classes set in an error frame's masked can_id.

    python-can masks an error frame to `can_id & 0x7FF`, which puts these bits
    in the same field a device id is read out of: TX_TIMEOUT reads as device 1,
    PROT as 8, TRX as 16.
    """
    names = [n for bit, n in _ERR_CLASSES.items() if arb & bit]
    return "+".join(names) or f"0x{arb:03X}"


def _not_a_device(msg):
    """Why this frame carries no device id, or None when it does.

    FRC device addressing exists only in a 29-bit frame a device transmitted.
    Decoding anything else invents a device: an error frame's class bits and a
    standard frame's low bits both land in the same 6 bits as a device number,
    and a frame this host sent proves nothing about what is on the wire.
    """
    if msg.is_error_frame:
        return f"error frame ({_err_classes(msg.arbitration_id)})"
    if not msg.is_extended_id:
        return "standard 11-bit id"
    if not msg.is_rx:
        return "sent by this host"
    return None


def _iface_state(channel):
    """Return (exists, is_up, is_fd) for a CAN netdev via `ip -details link show`."""
    r = subprocess.run(["ip", "-details", "link", "show", channel],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, False, False
    out = r.stdout
    is_up = "state UP" in out or "<NOARP,UP" in out or ",UP," in out or "UP," in out
    is_fd = "<FD>" in out or " fd " in out or "dbitrate" in out
    return True, is_up, is_fd


def _bus_names(conf):
    """(SPARK bus, CANcoder bus) as netdev names, or None where there is none.

    A bus named `rio` is carried by a roboRIO, so this host has nothing to
    listen on for it.
    """
    out = []
    for holder, attr in (("can", "interface"), ("cancoder", "bus")):
        ch = getattr(getattr(conf, holder, None), attr, None)
        ch = "" if ch is None else str(ch).strip()
        out.append(None if ch.lower() in ("", "rio") else ch)
    return out[0], out[1]


def _default_channels(conf):
    """The SPARK bus then the CANcoder bus, skipping a roboRIO-hosted one."""
    chans = []
    for ch in _bus_names(conf):
        if ch and ch not in chans:
            chans.append(ch)
    return chans


def _expectations(conf):
    """Every configured device as (id, 'group/LABEL', bus), plus the config defects.

    Devices come out in the order the config lists them, which is the order a
    person walks the rig, and each one carries the bus it lives on because ids
    repeat across buses. Labels are whatever the config calls them, so a rig
    with one motor reads as well as a swerve base.
    """
    devices = getattr(conf, "devices", None)
    if devices is None:
        return [], []
    if not hasattr(devices, "__dict__"):
        return [], [f"devices is {devices!r}, not a set of named groups"]
    spark_bus, cancoder_bus = _bus_names(conf)
    found, bad = [], []
    for group, members in vars(devices).items():
        if not hasattr(members, "__dict__"):
            bad.append(f"devices.{group} is {members!r}, "
                       "not a group of LABEL: id entries")
            continue
        entries = vars(members)
        if not entries:
            bad.append(f"devices.{group} is empty, so it names no device")
            continue
        bus = cancoder_bus if group == "cancoder" else spark_bus
        for label, dev_id in entries.items():
            try:
                num = int(dev_id)
            except (TypeError, ValueError):
                bad.append(f"devices.{group}.{label} is {dev_id!r}, not a device id")
                continue
            if not 0 <= num <= 63:
                bad.append(f"devices.{group}.{label} is {dev_id!r}, outside the "
                           "6-bit id space 0-63")
                continue
            found.append((num, f"{group}/{label}", bus))
    return found, bad


def _sweep_channel(channel, duration):
    """Listen on `channel` for `duration` s, transmitting nothing.

    Returns ({device_id: {count, mfrs}}, {reason: count}, finished): the
    device-addressed frames, everything that carries no device id at all, and
    whether the full duration ran. None when the channel cannot be listened on.

    Device ids legitimately repeat across two physical buses, a SPARK at id 6 on
    the motor bus and a CANcoder at id 6 on the CANivore, so results are kept per
    channel and only combined at render time.
    """
    exists, is_up, is_fd = _iface_state(channel)
    if not exists:
        present = ", ".join(netdev.present_can_netdevs()) or "none"
        print(f"  [{channel}] does not exist, skipping. CAN netdevs present: {present}")
        return None
    if not is_up:
        print(f"  [{channel}] is DOWN. Bring it up first, e.g.:")
        if is_fd:
            print(f"      sudo ip link set {channel} up   # FD bitrates already configured")
        else:
            print(f"      sudo ip link set {channel} up type can bitrate 1000000 txqueuelen 1000")
        return None

    seen = {}
    rejected = collections.Counter()
    finished = False
    try:
        bus = can.Bus(interface="socketcan", channel=channel, fd=is_fd)
    except Exception as e:                  # noqa: BLE001 - surface any backend error
        print(f"  [{channel}] could not open ({'FD' if is_fd else 'classic'}): {e}")
        return None
    try:
        print(f"  [{channel}] listening {duration:.1f}s "
              f"({'CAN-FD' if is_fd else 'classic CAN'})...")
        deadline = time.time() + duration
        while time.time() < deadline:
            msg = bus.recv(timeout=max(0.0, deadline - time.time()))
            if msg is None:
                continue
            reason = _not_a_device(msg)
            if reason:
                rejected[reason] += 1
                continue
            dev_id = msg.arbitration_id & 0x3F
            mfr = (msg.arbitration_id >> 16) & 0xFF
            entry = seen.setdefault(dev_id, {"count": 0, "mfrs": set()})
            entry["count"] += 1
            entry["mfrs"].add(mfr)
        finished = True
    except KeyboardInterrupt:
        heard = sum(v["count"] for v in seen.values())
        print(f"  [{channel}] interrupted, keeping the {heard} frames it had "
              "already heard.")
    finally:
        bus.shutdown()
    return seen, rejected, finished


def _parse_range(text):
    """A device-id range from '1-24', or a single id from '7'."""
    lo_s, sep, hi_s = text.strip().partition("-")
    hi_s = hi_s if sep else lo_s
    if not lo_s.isdigit() or not hi_s.isdigit():
        raise ValueError(f"{text!r} is not a device id or a LO-HI range of ids")
    lo, hi = int(lo_s), int(hi_s)
    if hi > 63 or lo > hi:
        raise ValueError(f"{text!r} is not a range inside the 6-bit id space 0-63")
    return range(lo, hi + 1)


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="A bus that is down is reported and skipped. A LIVE row means "
               "that id is broadcasting, which is not the same as this host "
               "being able to command it.")
    p.add_argument("--channels", default=None,
                   help="comma-separated CAN netdevs (default: can.interface "
                        "plus cancoder.bus from the config)")
    p.add_argument("--duration", type=float, default=2.0,
                   help="seconds to listen per channel (default 2.0)")
    p.add_argument("--range", dest="id_range", default="1-24",
                   help="device-id range to report, e.g. 1-24 (default) or "
                        "0-63 for the whole address space")
    args = p.parse_args(argv)

    if not math.isfinite(args.duration) or args.duration <= 0:
        print(f"REFUSED: --duration {args.duration} is not a positive number of "
              "seconds, so this would hear nothing and call every configured "
              "device missing.\n"
              "  FIX: pass --duration 2, or more on a quiet bus.")
        return 2
    try:
        id_range = _parse_range(args.id_range)
    except ValueError as err:
        print(f"REFUSED: {err}\n"
              "  FIX: pass --range as LO-HI inside 0-63, e.g. --range 1-24.")
        return 2

    try:
        cfg.load_host()
        conf = cfg.get()
    except Exception as err:                # noqa: BLE001 - any config load failure
        print(f"REFUSED: the sparklib config could not be read: {err}\n"
              "  FIX: repair that spark.yaml, or point SPARKLIB_CONFIG at the "
              "one this rig uses.")
        return 2

    if args.channels is None:
        channels = _default_channels(conf)
        if not channels:
            print("REFUSED: no bus to sweep. This config names neither a SPARK bus "
                  "nor a CANcoder bus.\n"
                  "  FIX: pass --channels can0, or set can.interface in spark.yaml "
                  "(and cancoder.bus when a second adapter carries the CANcoders).")
            return 2
    else:
        channels = [c.strip() for c in args.channels.split(",") if c.strip()]
        if not channels:
            print(f"REFUSED: --channels {args.channels!r} names no netdev.\n"
                  "  FIX: pass --channels can0, or can0,can1 for two adapters.")
            return 2

    expect, defects = _expectations(conf)
    if defects:
        print("REFUSED: the devices section does not read as device ids:")
        for line in defects:
            print(f"    {line}")
        print("  FIX: give every group at least one LABEL: id entry with a whole "
              "number 0-63, e.g. drive: {LF: 14, RF: 12}.")
        return 2

    print(f"=== CAN id sweep -- channels={channels} duration={args.duration}s/ch ===")
    claims = collections.Counter((bus, dev_id) for dev_id, _name, bus in expect)
    for (bus, dev_id), n in claims.items():
        if n > 1:
            names = "+".join(nm for i, nm, b in expect if i == dev_id and b == bus)
            print(f"  WARNING: {names} all claim id {dev_id} on {bus or 'one bus'}, "
                  "where only one device can answer.")

    per_channel = {}
    per_channel_rejected = {}
    interrupted = False
    for ch in channels:
        result = _sweep_channel(ch, args.duration)
        if result is None:
            continue
        per_channel[ch], per_channel_rejected[ch], finished = result
        if not finished:
            interrupted = True
            break

    if not per_channel:
        print("\nREFUSED: none of those channels could be listened on, so this "
              "reports nothing about what is on the bus.\n"
              "  FIX: bring one up, e.g. sudo ip link set can0 up type can "
              "bitrate 1000000 txqueuelen 1000, then re-run.")
        return 2

    def _detail(dev_id):
        """Per-bus detail strings for one id, e.g. 'can1:REV:301'."""
        parts = []
        for ch, devs in per_channel.items():
            e = devs.get(dev_id)
            if not e:
                continue
            mfr = "/".join(_MFR_NAMES.get(m, f"0x{m:02X}") for m in sorted(e["mfrs"]))
            parts.append(f"{ch}:{mfr}:{e['count']}")
        return parts

    # Scoped per bus. Ids repeat across adapters, so a flat map labels a SPARK
    # with the CANcoder that shares its number on the other wire.
    roles = {}
    for dev_id, name, bus in expect:
        if bus not in channels:
            continue
        roles[dev_id] = f"{roles[dev_id]}+{name}" if dev_id in roles else name

    missing, unchecked = [], []
    missing_ids, unchecked_ids = set(), set()
    for dev_id, name, bus in expect:
        if dev_id not in id_range:
            unchecked.append(f"{dev_id} ({name}) sits outside --range {args.id_range}")
            unchecked_ids.add(dev_id)
            continue
        if bus is None:
            answered = any(dev_id in devs for devs in per_channel.values())
        elif bus in per_channel:
            answered = dev_id in per_channel[bus]
        else:
            unchecked.append(f"{dev_id} ({name}) sits on {bus}, which was not swept")
            unchecked_ids.add(dev_id)
            continue
        if not answered:
            missing.append(f"{dev_id} ({name})")
            missing_ids.add(dev_id)

    print()
    print(f"  {'ID':>3}  {'STATUS':<6}  {'WHERE (bus:mfr:frames)':<34} ROLE")
    print(f"  {'-'*3}  {'-'*6}  {'-'*34} {'-'*16}")
    live_ids = []
    for dev_id in id_range:
        detail = _detail(dev_id)
        role = roles.get(dev_id, "")
        if dev_id in missing_ids:
            flag = "  <-- on another bus, not its own" if detail else "  <-- expected, NOT seen"
        elif dev_id in unchecked_ids:
            flag = "  <-- expected, not looked for"
        else:
            flag = ""
        if detail:
            live_ids.append(dev_id)
            status = "LIVE" + ("*" if len(detail) > 1 else "")
            print(f"  {dev_id:>3}  {status:<6}  {'  '.join(detail):<34} {role}{flag}")
        else:
            print(f"  {dev_id:>3}  {'--':<6}  {'':<34} {role}{flag}")

    print()
    print(f"  live: {len(live_ids)}/{len(id_range)}  "
          f"({', '.join(map(str, live_ids)) or 'none'})")
    print("  (* = same id answering on more than one bus)")
    for ch, rej in per_channel_rejected.items():
        if not rej:
            continue
        detail = ", ".join(f"{n} {why}" for why, n in
                           sorted(rej.items(), key=lambda kv: -kv[1]))
        print(f"  [{ch}] carries no device id, not counted above: {detail}")
    unexpected = sorted(set(live_ids) - set(roles))
    if unexpected:
        print(f"  live but NOT in config: {', '.join(map(str, unexpected))}")
    if missing:
        print(f"  MISSING expected ids: {', '.join(missing)}")
    if unchecked:
        print("  never looked for, so this run says nothing about them:")
        for line in unchecked:
            print(f"    {line}")
        print("  (--range 0-63 covers the whole space, and a bus has to be UP "
              "before it can be swept.)")
    if interrupted:
        never = [c for c in channels if c not in per_channel]
        tail = f"; never swept: {', '.join(never)}" if never else ""
        print(f"  INTERRUPTED: the counts above are partial{tail}.")
        return 130
    if missing or unchecked:
        return 1
    if expect:
        print("  all configured ids answered on their own bus.")
    else:
        print("  this config names no devices, so no id carries a role here.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nINTERRUPTED before the sweep finished. Nothing was transmitted.")
        sys.exit(130)
