"""Passive read-only snapshot of the SPARK bus. Never transmits.

Records, per REV motor-controller device ID: the 0x2F0 hardware serial, the
measured broadcast period of every status API, and the last STATUS_0 payload.
Emits YAML to stdout or -o FILE.

    uv run python tools/spark_passive_snapshot.py -d 20 -o baseline.yaml
"""

import argparse
import os
import collections
import datetime
import getpass
import socket
import subprocess
import sys
import time

import can

REV_MFR = 5
CTRE_MFR = 4
DEVTYPE_MOTOR = 2


def _git_rev(repo):
    r = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def _iface_is_fd(channel):
    r = subprocess.run(["ip", "-details", "link", "show", channel],
                       capture_output=True, text=True)
    return "dbitrate" in r.stdout


def capture(channel, duration):
    """Listen only. Returns {dev_id: {api: [count, last_payload_hex]}}."""
    bus = can.Bus(interface="socketcan", channel=channel, fd=_iface_is_fd(channel))
    seen = collections.defaultdict(dict)
    mfrs = collections.defaultdict(set)
    deadline = time.time() + duration
    try:
        while time.time() < deadline:
            msg = bus.recv(timeout=max(0.0, deadline - time.time()))
            if msg is None:
                continue
            a = msg.arbitration_id
            dev, api = a & 0x3F, (a >> 6) & 0x3FF
            mfrs[dev].add((a >> 16) & 0xFF)
            entry = seen[dev].setdefault(api, [0, ""])
            entry[0] += 1
            entry[1] = bytes(msg.data).hex().upper()
    finally:
        bus.shutdown()
    return seen, mfrs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--channel", default="can0")
    p.add_argument("-d", "--duration", type=float, default=10.0)
    p.add_argument("-o", "--out", default=None)
    args = p.parse_args(argv)

    from sparklib import config as _cfg

    # Resolve the config BEFORE listening. A config problem found afterwards
    # throws away however long the capture ran for.
    conf = _cfg.get()
    config_name = os.path.basename(_cfg.config_path())
    # Scoped to the channel being listened on. Ids repeat across adapters, so a
    # flat map labels these frames with whichever group was read last.
    roles = _cfg.device_roles(args.channel, conf)

    seen, mfrs = capture(args.channel, args.duration)

    lines = [
        "# Passive read-only snapshot of the SPARK bus. Nothing was transmitted.",
        "meta:",
        f"  captured_utc: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"  hostname: {socket.gethostname()}",
        f"  user: {getpass.getuser()}",
        f"  config: {config_name}",
        f"  controller_type: {conf.controller_type}",
        f"  channel: {args.channel}",
        f"  duration_s: {args.duration}",
        f"  git_rev: {_git_rev(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}",
        "devices:",
    ]
    rev_devices = [d for d in sorted(seen) if REV_MFR in mfrs[d]]
    if not rev_devices:
        others = sorted({m for s in mfrs.values() for m in s})
        heard = sum(c for apis in seen.values() for c, _ in apis.values())
        lines.append(f"  # none. Listened {args.duration:g} s on {args.channel} "
                     f"and heard {heard} frame(s) from {len(seen)} device id(s), "
                     "none of them a REV motor controller.")
        lines.append("  # This tool reports REV controllers only, so a bus "
                     "carrying other makes reads as empty here.")
        if CTRE_MFR in others:
            lines.append("  # CTRE traffic is present: for CANcoders use "
                         "tools/cancoder_audit.py, which reads them through "
                         "phoenix6.")

    for dev in rev_devices:
        apis = seen[dev]
        # Pre-25 firmware broadcasts no identity and puts status 0 on 0x060.
        pre25 = 0x060 in apis and 0x2E0 not in apis
        serial = apis.get(0x2F0, [0, ""])[1]
        status_0 = apis.get(0x060 if pre25 else 0x2E0, [0, ""])[1]
        lines.append(f"  {dev}:")
        lines.append(f"    role: {roles.get(dev, 'UNCONFIGURED')}")
        lines.append(f"    generation: {'pre25' if pre25 else 'fw25+'}")
        lines.append(f"    serial: '{serial or ('not broadcast on pre25' if pre25 else 'unknown')}'")
        lines.append(f"    status_0_payload: '{status_0}'")
        lines.append("    frame_periods_ms:")
        for api in sorted(apis):
            count = apis[api][0]
            period = round(args.duration * 1000.0 / count, 1) if count else 0
            lines.append(f"      '0x{api:03X}': {period}   # {count} frames")
    text = "\n".join(lines) + "\n"

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"wrote {args.out}  ({len(seen)} devices)")
    else:
        print(text)


if __name__ == "__main__":
    main()
