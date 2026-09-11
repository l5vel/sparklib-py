#!/usr/bin/env python3
"""Check whether a SocketCAN interface can drain transmitted frames."""

import argparse
import re
import subprocess
import time


TEST_FRAME = "1ABCDE00#00"


def _qdisc(interface):
    result = subprocess.run(
        ["tc", "-s", "qdisc", "show", "dev", interface],
        capture_output=True,
        text=True,
        check=True,
    )
    text = " ".join(result.stdout.splitlines())
    sent = re.search(r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt", text)
    backlog = re.search(r"backlog\s+\S+\s+(\d+)p", text)
    return {
        "text": text,
        "sent_packets": int(sent.group(2)) if sent else None,
        "backlog_packets": int(backlog.group(1)) if backlog else 0,
    }


def _run(cmd, check=False):
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        text = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{' '.join(cmd)} failed: {text}")
    return result


def _interface_exists(interface):
    return _run(["ip", "link", "show", interface]).returncode == 0


def _parent_usb_interface(interface):
    result = _run(["ip", "-d", "-o", "link", "show", interface])
    if result.returncode != 0:
        return None
    match = re.search(r"parentdev\s+(\S+)", result.stdout)
    return match.group(1) if match else None


def _setup_can(interface, bitrate, txqueuelen):
    commands = [
        ["sudo", "ip", "link", "set", interface, "down"],
        ["sudo", "ip", "link", "set", interface, "txqueuelen", "0"],
        ["sudo", "tc", "qdisc", "replace", "dev", interface, "root", "pfifo", "limit", str(txqueuelen)],
        ["sudo", "ip", "link", "set", interface, "type", "can", "bitrate", str(bitrate)],
        ["sudo", "ip", "link", "set", interface, "txqueuelen", str(txqueuelen)],
        ["sudo", "ip", "link", "set", interface, "up"],
    ]
    for cmd in commands:
        result = _run(cmd)
        if result.returncode != 0:
            print(f"[reset] {' '.join(cmd)} failed: {(result.stderr or result.stdout).strip()}")
            return False
    return True


def _reset_gs_usb(interface, bitrate, txqueuelen):
    parent = _parent_usb_interface(interface)
    if not parent:
        print(f"[reset] could not find parentdev for {interface}; is it a gs_usb interface?")
        return False

    print(f"[reset] rebinding gs_usb interface {parent}")
    for action in ("unbind", "bind"):
        cmd = [
            "sudo",
            "sh",
            "-c",
            f"echo {parent} > /sys/bus/usb/drivers/gs_usb/{action}",
        ]
        result = _run(cmd)
        if result.returncode != 0:
            print(f"[reset] {' '.join(cmd)} failed: {(result.stderr or result.stdout).strip()}")
            return False
        time.sleep(0.5)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _interface_exists(interface):
            return _setup_can(interface, bitrate, txqueuelen)
        time.sleep(0.2)

    print(f"[reset] {interface} did not reappear after gs_usb rebind")
    return False


def _probe(interface, frames, delay):
    before = _qdisc(interface)
    print(f"[before] {before['text']}")

    for _ in range(frames):
        subprocess.run(["cansend", interface, TEST_FRAME], check=False)
        time.sleep(delay)

    time.sleep(0.2)
    after = _qdisc(interface)
    print(f"[after]  {after['text']}")

    before_sent = before["sent_packets"]
    after_sent = after["sent_packets"]
    if before_sent is None or after_sent is None:
        print("[result] could not parse TX packet counters")
        return False

    sent_delta = after_sent - before_sent
    backlog_delta = after["backlog_packets"] - before["backlog_packets"]
    print(f"[delta] sent_packets={sent_delta} backlog_packets={backlog_delta}")
    ok = sent_delta >= frames and after["backlog_packets"] == 0
    if ok:
        print("[result] TX queue drains normally.")
    else:
        print(
            "[result] TX is not draining cleanly. This is below Python/Spark code; "
            "the SocketCAN adapter/driver is wedged."
        )
    return ok


def main():
    parser = argparse.ArgumentParser(description="Probe CAN TX queue drain health.")
    parser.add_argument("interface", nargs="?", default="can0")
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.02)
    parser.add_argument("--reset", action="store_true",
                        help="If TX is wedged, rebind the gs_usb driver and probe again.")
    parser.add_argument("--bitrate", type=int, default=1000000)
    parser.add_argument("--txqueuelen", type=int, default=1000)
    args = parser.parse_args()

    ok = _probe(args.interface, args.frames, args.delay)
    if ok or not args.reset:
        return

    if _reset_gs_usb(args.interface, args.bitrate, args.txqueuelen):
        print("[reset] re-probing after driver reset")
        _probe(args.interface, args.frames, args.delay)
    else:
        print("[reset] failed; physical USB replug or adapter replacement is the next layer.")


if __name__ == "__main__":
    main()
