"""System-level CAN diagnostics. Read-only, no sudo, no CAN traffic sent.

Collects everything SocketCAN exposes about bus health so a base can be
triaged over SSH without touching the robot:

  1. link state + controller error counters   ip -details -statistics link
  2. protocol stack counters                  /proc/net/can/stats
  3. TX queue health                          tc -s qdisc
  4. driver / USB events + re-enumerations    journalctl -k
  5. error frames on the wire                 candump -e
  6. live device inventory                    passive listen

Every check prints PASS / WARN / FAIL and the exit code is non-zero if any
check FAILs, so it can be dropped into a monitor.

    uv run python tools/can_diag.py
    uv run python tools/can_diag.py --channels can0 --errmon 15
"""

import argparse
import re
import subprocess
import sys
import time

VERDICTS = []


def emit(level, msg):
    VERDICTS.append(level)
    print(f"  [{level:4}] {msg}")


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def can_interfaces():
    out = run(["ip", "-br", "link", "show", "type", "can"])
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def check_link(ch):
    print(f"\n--- {ch}: link state and error counters ---")
    out = run(["ip", "-details", "-statistics", "link", "show", ch])
    if not out.strip():
        emit("FAIL", f"{ch} does not exist")
        return
    print("\n".join("      " + line for line in out.strip().splitlines()))

    state = re.search(r"can state (\S+)", out)
    state = state.group(1) if state else "UNKNOWN"
    if state == "ERROR-ACTIVE":
        emit("PASS", f"controller state {state} (healthy)")
    elif state == "BUS-OFF":
        emit("FAIL", f"controller state {state} -- bus is down, needs a restart")
    else:
        emit("WARN", f"controller state {state}")

    # The counter row follows the header row naming the six counters.
    m = re.search(r"re-started\s+bus-errors\s+arbit-lost\s+error-warn\s+"
                  r"error-pass\s+bus-off\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+"
                  r"(\d+)\s+(\d+)\s+(\d+)", out)
    if m:
        names = ["re-started", "bus-errors", "arbit-lost",
                 "error-warn", "error-pass", "bus-off"]
        vals = [int(g) for g in m.groups()]
        bad = [f"{n}={v}" for n, v in zip(names, vals) if v]
        if bad:
            emit("WARN", "non-zero error counters: " + ", ".join(bad))
        else:
            emit("PASS", "all controller error counters zero")

    restart = re.search(r"restart-ms (\d+)", out)
    if restart and int(restart.group(1)) == 0:
        if "gs_usb" in out:
            emit("WARN", "restart-ms 0 -- no automatic recovery from BUS-OFF, "
                         "and this adapter REJECTS restart-ms, so do not try to "
                         "set it. Recover a wedged gs_usb with `uv run spark "
                         "canfix`, which rebinds the driver")
        else:
            emit("WARN", "restart-ms 0 -- no automatic recovery from BUS-OFF. "
                         f"set with: sudo ip link set {ch} type can restart-ms 100")

    m = re.search(r"RX:.*?\n\s*\S+\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)", out, re.S)
    if m and any(int(g) for g in m.groups()):
        emit("WARN", f"RX errors/dropped/missed = {'/'.join(m.groups())}")
    m = re.search(r"TX:.*?\n\s*\S+\s+\S+\s+(\d+)\s+(\d+)", out, re.S)
    if m and any(int(g) for g in m.groups()):
        emit("WARN", f"TX errors/dropped = {'/'.join(m.groups())}")


def check_qdisc(ch):
    print(f"\n--- {ch}: TX queue ---")
    out = run(["tc", "-s", "qdisc", "show", "dev", ch])
    print("\n".join("      " + line for line in out.strip().splitlines()))
    m = re.search(r"backlog (\d+)b (\d+)p", out)
    if m and int(m.group(2)):
        emit("FAIL", f"{int(m.group(2))} frames stuck in the TX backlog -- "
                     "nothing is draining, adapter is wedged")
    else:
        emit("PASS", "TX backlog empty")
    m = re.search(r"dropped (\d+)", out)
    if m and int(m.group(1)):
        emit("WARN", f"{m.group(1)} frames dropped by the qdisc")


def check_stack():
    print("\n--- CAN protocol stack (/proc/net/can/stats) ---")
    out = run(["cat", "/proc/net/can/stats"])
    if not out.strip():
        emit("WARN", "/proc/net/can/stats unreadable (can module loaded?)")
        return
    print("\n".join("      " + line for line in out.strip().splitlines() if line.strip()))


def check_kernel_log(ch):
    print("\n--- kernel log (journalctl -k, no sudo needed) ---")
    out = run(["journalctl", "-k", "--no-pager", "--since", "today"])
    if not out.strip():
        emit("WARN", "kernel log unreadable")
        return
    reenum = len(re.findall(r"Configuring for \d+ interfaces", out))
    if reenum > 1:
        emit("WARN", f"CAN adapter re-enumerated {reenum} times today -- "
                     "USB instability, check cable/hub/power")
    else:
        emit("PASS", f"adapter re-enumerations today: {reenum}")

    hits = [l for l in out.splitlines()
            if re.search(r"gs_usb|slcan|bus-off|xmit fail|echo id|"
                         r"canivore|" + re.escape(ch), l, re.I)]
    for line in hits[-12:]:
        print("      " + line)
    if any(re.search(r"xmit fail|echo id|bus-off", l, re.I) for l in hits):
        emit("FAIL", "driver reported transmit failures or bus-off in the log")


def check_error_frames(channels, seconds):
    print(f"\n--- error frames on the wire ({seconds}s) ---")
    # One "<iface>,<filter>" arg per bus; error frames only.
    args = [f"{c},0~0,#FFFFFFFF" for c in channels]
    out = run(["candump", "-T", str(int(seconds * 1000)), "-e"] + args,
              timeout=seconds + 10)
    frames, problems = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        (frames if re.search(r"\s[0-9A-Fa-f]{3,8}\s+\[", line) else problems).append(line)
    for line in problems:
        emit("WARN", f"candump: {line.strip()}")
    if frames:
        emit("FAIL", f"{len(frames)} CAN error frames captured")
        for line in frames[:10]:
            print("      " + line)
    else:
        emit("PASS", "no error frames")


def inventory(ch, seconds):
    print(f"\n--- live device inventory on {ch} ({seconds}s, passive) ---")
    try:
        import can
    except ImportError:
        emit("WARN", "python-can not importable, skipping inventory")
        return
    fd = "dbitrate" in run(["ip", "-details", "link", "show", ch])
    try:
        bus = can.Bus(interface="socketcan", channel=ch, fd=fd)
    except Exception as e:
        emit("FAIL", f"cannot open {ch}: {e}")
        return
    devs, serial = {}, {}
    end = time.time() + seconds
    try:
        while time.time() < end:
            m = bus.recv(timeout=max(0.0, end - time.time()))
            if m is None:
                continue
            a = m.arbitration_id
            dev, api, mfr = a & 0x3F, (a >> 6) & 0x3FF, (a >> 16) & 0xFF
            devs.setdefault((mfr, dev), 0)
            devs[(mfr, dev)] += 1
            if api == 0x2F0:
                serial[dev] = bytes(m.data).hex().upper()
    finally:
        bus.shutdown()
    if not devs:
        emit("FAIL", f"no devices broadcasting on {ch}")
        return
    names = {4: "CTRE", 5: "REV"}
    for (mfr, dev) in sorted(devs):
        print(f"      {names.get(mfr, f'0x{mfr:02X}'):5} id={dev:<3} "
              f"frames={devs[(mfr, dev)]:<6} serial={serial.get(dev, '-')}")
    emit("PASS", f"{len(devs)} devices broadcasting on {ch}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--channels", default=None, help="comma-separated (default: all CAN netdevs)")
    p.add_argument("--errmon", type=float, default=8.0, help="seconds to watch for error frames")
    p.add_argument("--inventory", type=float, default=5.0, help="seconds to inventory devices")
    args = p.parse_args(argv)

    channels = (args.channels.split(",") if args.channels else can_interfaces())
    if not channels:
        print("no CAN interfaces found")
        return 2

    print(f"=== CAN diagnostics -- channels: {', '.join(channels)} ===")
    for ch in channels:
        check_link(ch)
        check_qdisc(ch)
    check_stack()
    check_kernel_log(channels[0])
    check_error_frames(channels, args.errmon)
    for ch in channels:
        inventory(ch, args.inventory)

    print("\n=== summary ===")
    for level in ("FAIL", "WARN", "PASS"):
        n = VERDICTS.count(level)
        if n:
            print(f"  {level}: {n}")
    return 1 if "FAIL" in VERDICTS else 0


if __name__ == "__main__":
    sys.exit(main())
