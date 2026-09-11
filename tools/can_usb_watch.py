#!/usr/bin/env python3
"""Watch a USB CAN adapter survive, or not, while the robot drives.

WHY THIS EXISTS

  On rig-max, the gs_usb adapter dropped off the USB bus in the
  middle of a high-speed run and re-enumerated seven times. `can_diag` can only
  say what the wreckage looks like afterwards. This says WHEN it happened and
  what the bus was doing at that moment, which is what separates the candidate
  causes.

  See docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md.

WHAT IT DOES

  Polls, and survives the adapter disappearing. Every sample records whether the
  netdev exists at all, its link and controller state, the transmit backlog, the
  frame counters and the error counters. It prints a line the moment any of that
  changes, and nothing while the picture is steady, so the output IS the event
  log.

  It watches a CONTROL interface too, the CANivore by default. That one sits on
  the same hub and the same machine, so if both drop together the cause is
  upstream of either, and if only one drops it is that adapter, its cable or its
  port.

  It reads `journalctl -k` for USB and gs_usb events and interleaves them with
  the samples on one clock.

  It sends nothing, opens no CAN socket and needs no sudo, so it cannot disturb
  what it is measuring and it is safe to leave running through a drive test.

HOW TO USE IT

  IT IS INTERMITTENT. A high-speed run survived, and a later one
  in the same session did not, so a single timed test is the wrong instrument.
  Leave it running for the whole session and drive normally:

    uv run python tools/can_usb_watch.py --seconds 0 --log can-watch.log

  --seconds 0 means until Ctrl-C. Every line carries a wall clock, so the log
  lines up with whatever the robot was doing. A heartbeat line every 30 s says
  it is still alive, because an adapter that never drops otherwise produces a
  silent terminal that reads exactly like a broken script.

  To tell the candidates apart, run the SAME drive with one thing changed at a
  time and compare the summaries:

    1. cable swapped for a known-good one
    2. adapter moved off the hub, straight into the machine
    3. USB cable rerouted away from the motor leads and tied down
    4. robot pushed by hand at speed with the motors disabled, which is
       vibration with no motor current and no switching noise

  A failure that survives 1 and 2 and disappears under 3 is coupling from the
  motor leads. One that reproduces under 4 is mechanical. One that needs motor
  current is electrical.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

SAMPLE = "%-8s %-14s %-7s %-13s %7s %9s %9s"


def sh(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def sample(iface):
    """Everything worth knowing about one interface, right now."""
    link = sh(["ip", "-details", "-statistics", "link", "show", iface])
    if not link.strip():
        return {"present": False}
    state = re.search(r"state (\w+)", link)
    can = re.search(r"can state (\S+)", link)
    qd = sh(["tc", "-s", "qdisc", "show", "dev", iface])
    backlog = re.search(r"backlog\s+\S+\s+(\d+)p", qd)
    sent = re.search(r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt", qd)
    rx = re.search(r"RX:.*?\n\s*(\d+)\s+(\d+)", link, re.S)
    errs = re.search(
        r"re-started bus-errors.*?\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        link, re.S)
    return {
        "present": True,
        "link": state.group(1) if state else "?",
        "can": can.group(1) if can else "?",
        "backlog": int(backlog.group(1)) if backlog else 0,
        "tx_pkt": int(sent.group(2)) if sent else 0,
        "rx_pkt": int(rx.group(2)) if rx else 0,
        "errors": tuple(int(g) for g in errs.groups()) if errs else (),
    }


def interesting(prev, cur):
    """Has anything changed that a person would want a line about?"""
    if prev is None:
        return True
    if prev.get("present") != cur.get("present"):
        return True
    if not cur.get("present"):
        return False
    for k in ("link", "can", "errors"):
        if prev.get(k) != cur.get(k):
            return True
    # A backlog that starts growing is the first sign of a transmit problem.
    if cur["backlog"] > 0 and cur["backlog"] != prev.get("backlog", 0):
        return True
    return False


def kernel_events(since_boot):
    """gs_usb and USB lines the kernel logged after we started."""
    out = sh(["journalctl", "-k", "--no-pager", "-o", "short-monotonic",
              "--since", "-1h"], timeout=10)
    rows = []
    for line in out.splitlines():
        m = re.match(r"\[\s*([\d.]+)\]", line)
        if not m or float(m.group(1)) < since_boot:
            continue
        if re.search(r"gs_usb|usb \d+-[\d.]+:|USB disconnect|descriptor read", line):
            rows.append((float(m.group(1)), line.split("] ", 1)[-1].strip()))
    return rows


def monotonic_now():
    """Kernel monotonic seconds, so samples and dmesg share one clock."""
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except OSError:
        return 0.0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-i", "--interface", default=None)
    p.add_argument("-c", "--control", default="canivore",
                   help="a second interface as the control (default: canivore)")
    p.add_argument("--seconds", type=float, default=0.0,
                   help="0 means run until interrupted (default)")
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--heartbeat", type=float, default=30.0,
                   help="seconds between 'still alive' lines (0 to silence)")
    p.add_argument("--log", default=None,
                   help="append every line to this file as well as the terminal")
    a = p.parse_args(argv)

    iface = a.interface
    if iface is None:
        from sparklib.config import get as _spark_config
        iface = _spark_config().can.interface

    started = monotonic_now()
    sink = open(a.log, "a", buffering=1) if a.log else None

    def say(line=""):
        stamped = f"{time.strftime('%H:%M:%S')}  {line}" if line else ""
        print(stamped, flush=True)
        if sink:
            sink.write(stamped + "\n")

    forever = a.seconds <= 0
    say(f"watching {iface} "
        f"{'until interrupted' if forever else f'for {a.seconds:.0f}s'}, "
        f"control {a.control}, sampling every {a.interval * 1000:.0f} ms. "
        "Sends nothing.")
    say("Drive normally. A line appears when something changes, and a heartbeat "
        "otherwise so silence is")
    say("distinguishable from a dead script.")
    say(SAMPLE % ("t+", "iface", "link", "can state", "backlog", "tx pkt",
                  "rx pkt"))

    prev = prev_ctl = None
    drops, first_drop, max_backlog = 0, None, 0
    ctl_drops, ctl_seen, samples = 0, False, 0
    peak_tx_rate = peak_rx_rate = 0.0
    last_beat = 0.0
    t0 = time.monotonic()
    try:
        while forever or time.monotonic() - t0 < a.seconds:
            t = time.monotonic() - t0
            cur, ctl = sample(iface), sample(a.control)
            samples += 1
            ctl_seen = ctl_seen or ctl.get("present", False)
            if cur.get("present"):
                max_backlog = max(max_backlog, cur["backlog"])
                if prev and prev.get("present") and a.interval > 0:
                    peak_tx_rate = max(peak_tx_rate,
                                       (cur["tx_pkt"] - prev["tx_pkt"]) / a.interval)
                    peak_rx_rate = max(peak_rx_rate,
                                       (cur["rx_pkt"] - prev["rx_pkt"]) / a.interval)
            if prev is not None and prev.get("present") and not cur.get("present"):
                drops += 1
                first_drop = t if first_drop is None else first_drop
            if (prev_ctl is not None and prev_ctl.get("present")
                    and not ctl.get("present")):
                ctl_drops += 1
            if interesting(prev, cur):
                last_beat = t
                if cur.get("present"):
                    say(SAMPLE % (f"{t:6.0f}s", iface, cur["link"], cur["can"],
                                  cur["backlog"], cur["tx_pkt"], cur["rx_pkt"]))
                else:
                    say(SAMPLE % (f"{t:6.0f}s", iface, "GONE", "-", "-", "-", "-"))
            if prev_ctl is not None and interesting(prev_ctl, ctl):
                last_beat = t
                say(SAMPLE % (f"{t:6.0f}s", a.control + " (ctl)",
                              "GONE" if not ctl.get("present") else ctl["link"],
                              ctl.get("can", "-"), ctl.get("backlog", "-"),
                              ctl.get("tx_pkt", "-"), ctl.get("rx_pkt", "-")))
            if a.heartbeat > 0 and t - last_beat >= a.heartbeat:
                last_beat = t
                say(SAMPLE % (f"{t:6.0f}s", iface + " (ok)",
                              cur.get("link", "GONE"), cur.get("can", "-"),
                              cur.get("backlog", "-"), cur.get("tx_pkt", "-"),
                              cur.get("rx_pkt", "-")))
            prev, prev_ctl = cur, ctl
            time.sleep(a.interval)
    except KeyboardInterrupt:
        say("interrupted")

    say("")
    say("--- kernel events during the window ---")
    ev = kernel_events(started)
    if not ev:
        say("  none")
    for mono, line in ev:
        say(f"  t+{mono - started:6.1f}s  {line}")

    watched = time.monotonic() - t0
    say("")
    say("=== summary ===")
    say(f"  watched {watched / 60:.1f} min over {samples} samples")
    say(f"  peak frame rates seen: {peak_tx_rate:.0f} tx/s, {peak_rx_rate:.0f} rx/s")
    say(f"  {iface}: {drops} disconnect(s), peak TX backlog {max_backlog} frames")
    if first_drop is not None:
        say(f"  first disconnect at t+{first_drop:.0f}s "
            f"({first_drop / 60:.1f} min in)")
    if ctl_seen:
        say(f"  {a.control} (control): {ctl_drops} disconnect(s)")
    else:
        say(f"  {a.control} (control): NEVER PRESENT, so this run has no "
            "control. Pass --control an")
        say("   interface this machine has, or the result can only speak for "
            "the watched adapter.")

    if drops and ctl_seen and not ctl_drops:
        say("")
        say("  ONLY THE WATCHED ADAPTER DROPPED. The machine, the hub and the "
            "other adapter came")
        say("  through the same window, so this is that device, its cable or "
            "its port.")
    elif drops and ctl_seen and ctl_drops:
        say("")
        say("  BOTH DROPPED, so the cause is upstream of either adapter: the "
            "hub, its supply or the")
        say("  machine's USB. Test the hub before blaming the adapter.")
    elif drops and not ctl_seen:
        say("")
        say("  The adapter dropped with no control running, so this cannot yet "
            "separate the adapter")
        say("  from the machine or the hub.")
    else:
        say("")
        say("  NOTHING DROPPED. THIS FAULT IS INTERMITTENT: an "
            "earlier high-speed run")
        say("  survived and a later one in the same session did not, so a quiet "
            "window is not a clean")
        say("  bill of health. Compare the peak frame rates above against the "
            "failing run before")
        say("  reading anything into it, and prefer one long session to several "
            "short tests.")
    if sink:
        sink.close()
    return 1 if drops else 0


if __name__ == "__main__":
    sys.exit(main())
