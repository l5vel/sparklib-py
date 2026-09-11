"""SocketCAN netdev bring-up and USB-adapter recovery, independent of any SPARK.

A CAN adapter on Linux is a network device. It has to exist, be named, be UP at
the right bitrate, and be draining its transmit queue before a single SPARK
frame goes anywhere, and each of those fails in its own way. This module does
that bring-up and recovers the failures that are recoverable in software.

Most of it is specific to the candleLight / Geschwister Schneider gs_usb family
(1d50:606f), which is what most people plug into a Linux box. A gs_usb dongle
can carry a wedged transmit FIFO across `ip link down/up`, so only a USB-level
unbind and bind clears it, and a firmware-stalled endpoint needs a genuine
re-enumeration that a rebind does not perform. Both recoveries are here.

Nothing here knows what a SPARK is. It works for any SocketCAN interface.
"""

import glob
import os
import re
import subprocess
import time

# /sys/class/net/<dev>/type for a CAN interface, from linux/if_arp.h.
ARPHRD_CAN = "280"


def present_can_netdevs():
    """Every CAN netdev on this machine, by name."""
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with open(f"/sys/class/net/{name}/type") as f:
                if f.read().strip() == ARPHRD_CAN:
                    out.append(name)
        except OSError:
            continue
    return sorted(out)


def check_netdev(can_interface, role="can.interface"):
    """Raise unless the named CAN netdev exists and is UP."""
    try:
        with open(f"/sys/class/net/{can_interface}/operstate") as f:
            state = f.read().strip()
    except FileNotFoundError as e:
        present = present_can_netdevs()
        raise RuntimeError(
            f"CAN netdev {can_interface!r} ({role}) does not exist. "
            f"CAN netdevs actually present: {', '.join(present) or 'none'}. "
            f"Either {role} names the wrong bus, or the adapter is unplugged and "
            f"no udev rule named it "
            f"(sudo udevadm control --reload-rules && sudo udevadm trigger)."
        ) from e
    if state not in ("up", "unknown"):
        raise RuntimeError(
            f"CAN netdev {can_interface!r} ({role}) is not UP (state={state!r}). "
            f"Run: sudo ip link set {can_interface} up"
        )


class CanNetdev:
    """Bring one SocketCAN interface up, and recover it when it wedges.

    The interface name is yours to choose and is not guessed: kernel `canN`
    ordering is not deterministic across reboots, so a rig with two adapters
    wants udev names. docs/CAN-SETUP.md carries the rule.
    """

    def __init__(self, interface, wait_timeout_s=20, skip_gs_usb_rebind=False,
                 bitrate=1000000, txqueuelen=1000):
        self.target_interface = interface
        # Bound the netdev wait so a missing/unbound adapter fails fast (0 = wait forever).
        self.WAIT_TIMEOUT_S = int(wait_timeout_s)
        self.skip_gs_usb_rebind = bool(skip_gs_usb_rebind)
        self.bitrate = int(bitrate)
        self.txqueuelen = int(txqueuelen)
        # True once a bring-up SKIPPED the reset because the bus was already healthy.
        self.adopted = False

    def is_available(self):
        """True once the netdev exists under the configured name."""
        result = subprocess.run(["ip", "link", "show", self.target_interface],
                                capture_output=True, text=True)
        return result.returncode == 0

    @staticmethod
    def netdev_exists(name):
        return os.path.exists(f"/sys/class/net/{name}")

    def _check_netdev(self, iface):
        """Raise unless `iface` exists and is UP. A hook, so a host application
        that resolves the netdev differently has one place to say so."""
        check_netdev(iface)

    def _skip_rebind(self):
        """Read at the call site, not at construction: a host application whose
        config can change under it overrides this and reads its config here."""
        return self.skip_gs_usb_rebind

    def _check_netdev(self, iface):
        """Raise unless `iface` exists and is UP. A hook, so a host application
        that resolves the netdev differently has one place to say so."""
        check_netdev(iface)

    def _skip_rebind(self):
        """Read at the call site, not at construction: a host application whose
        config can change under it overrides this and reads its config here."""
        return self.skip_gs_usb_rebind

    def _bus_ready(self):
        """True when the bus is already up and draining TX -- nothing to reset.

        Stricter than _tx_probe_ok, which gives the benefit of the doubt when it cannot
        measure. On the cold path that is harmless (the reset already ran); on adopt it
        has to mean fall through to the full reset, because that is always correct and
        adopting a bus we cannot vouch for is not.
        """
        try:
            self._check_netdev(self.target_interface)
        except RuntimeError:
            return False
        state = self._qdisc_state()
        if state is None or state["sent_packets"] is None:
            return False
        return self._tx_probe_ok()

    def _after_bring_up(self):
        """Hook for a second bus a subclass also needs up. Does nothing here."""

    def check_can_interface(self, adopt=False):
        # adopt: claim a healthy bus instead of running the rebind + reset below.
        if adopt and self._bus_ready():
            print(f"[CAN] Adopting a live {self.target_interface} -- no rebind, no bus reset")
            self.adopted = True
            self._after_bring_up()
            return self.target_interface
        # The candleLight gs_usb dongle can carry forward a wedged TX FIFO across
        # `ip link down/up` (only a USB-level unbind/bind clears it). A one-frame
        # `_tx_probe_ok()` passes even when the dongle is partially degraded, so
        # historically we only rebound on probe failure and the dongle stayed bad.
        # Always rebind first when this is a gs_usb interface; CTRE CANivore has
        # no USB parentdev so `_is_gs_usb()` excludes it automatically.
        #
        # skip_gs_usb_rebind skips the pre-emptive rebind, for a dongle the
        # rebind itself leaves unable to transmit.
        skip_rebind = self._skip_rebind()
        if self._is_gs_usb() and not skip_rebind:
            print(f"[CAN] Pre-emptively resetting gs_usb driver for {self.target_interface}...")
            self._reset_gs_usb_driver()
        elif skip_rebind:
            print("[CAN] Skipping pre-emptive gs_usb rebind (can.skip_gs_usb_rebind).")
        self.setup_can_interface()
        if not self._tx_probe_ok():
            print(f"[CAN] {self.target_interface} TX probe failed; resetting gs_usb driver once...")
            if self._reset_gs_usb_driver():
                self.setup_can_interface()
                if not self._tx_probe_ok():
                    print(
                        f"[CAN] WARNING: {self.target_interface} still cannot drain TX "
                        "after driver reset. Replug the USB CAN adapter or check bus wiring."
                    )
            else:
                print(
                    f"[CAN] WARNING: could not reset the USB driver for {self.target_interface}. "
                    "Replug the USB CAN adapter if TX remains wedged."
                )
        self._after_bring_up()
        return self.target_interface

    def _is_gs_usb(self):
        return self._parent_usb_interface() is not None

    def setup_can_interface(self, bitrate=None, txqueuelen=None):
        """Performs a deep reset to clear 'Transmit buffer full' errors."""
        bitrate = self.bitrate if bitrate is None else bitrate
        txqueuelen = self.txqueuelen if txqueuelen is None else txqueuelen
        print(f"Resetting {self.target_interface} to clear zombie buffers...")

        # Force DOWN, delete the qdisc to clear any wedged backlog, then bring
        # the bus back up. Do not replace the qdisc with plain pfifo here:
        # leaving the kernel to recreate its default pfifo_fast path has proven
        # more reliable on gs_usb adapters.
        commands = [
            f"sudo ip link set {self.target_interface} down",
            f"sudo tc qdisc del dev {self.target_interface} root",
            f"sudo ip link set {self.target_interface} up type can bitrate {bitrate}",
            f"sudo ip link set {self.target_interface} txqueuelen {txqueuelen}",
        ]

        failures = []
        for cmd in commands:
            try:
                subprocess.run(cmd, shell=True, check=True, capture_output=True)
                print(f"Executed: {cmd}")
            except subprocess.CalledProcessError as e:
                detail = e.stderr.decode().strip()
                if "sudo tc qdisc del" in cmd and (
                    "No such file or directory" in detail
                    or "Cannot delete qdisc" in detail
                    or "Invalid argument" in detail
                ):
                    print(f"Ignored: {cmd}: {detail}")
                    continue
                print(f"Error executing {cmd}: {detail}")
                failures.append((cmd, detail))
        state = self._print_tx_queue_state()
        if failures:
            failed_cmds = "\n".join(f"  {cmd}: {detail}" for cmd, detail in failures)
            raise RuntimeError(
                f"Could not reset {self.target_interface}; sudo failed for:\n"
                f"{failed_cmds}\n"
                "Run the CAN reset command manually from a terminal with sudo, "
                "then launch the motor program again."
            )
        if state is not None and state["backlog_packets"]:
            raise RuntimeError(
                f"{self.target_interface} TX queue is still wedged "
                f"({state['backlog_packets']} pending packets) after reset. "
                "Replug the gs_usb adapter or reset the interface with sudo "
                "before commanding motors."
            )

    def _qdisc_state(self):
        result = subprocess.run(
            ["tc", "-s", "qdisc", "show", "dev", self.target_interface],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        line = " ".join(result.stdout.splitlines())
        sent = re.search(r"Sent\s+\d+\s+bytes\s+(\d+)\s+pkt", line)
        backlog = re.search(r"backlog\s+\S+\s+(\d+)p", line)
        return {
            "line": line,
            "sent_packets": int(sent.group(1)) if sent else None,
            "backlog_packets": int(backlog.group(1)) if backlog else 0,
        }

    def _print_tx_queue_state(self):
        state = self._qdisc_state()
        if state is None:
            return None
        backlog = state["backlog_packets"]
        print(f"[CAN] {self.target_interface} tx queue: {state['line']}")
        if backlog:
            print(
                f"[CAN] WARNING: {self.target_interface} still has {backlog} "
                "pending TX packets. Commands may fail with 'Transmit buffer full'. "
                "Power-cycle or USB replug the gs_usb adapter if this does not clear."
            )
        return state

    def _tx_probe_ok(self):
        """Send one harmless test frame and verify SocketCAN drains TX."""
        before = self._qdisc_state()
        if before is None or before["sent_packets"] is None:
            return True

        subprocess.run(
            ["cansend", self.target_interface, "1ABCDE00#00"],
            capture_output=True,
            text=True,
        )
        time.sleep(0.2)

        after = self._qdisc_state()
        if after is None or after["sent_packets"] is None:
            return True

        sent_delta = after["sent_packets"] - before["sent_packets"]
        backlog_delta = after["backlog_packets"] - before["backlog_packets"]
        ok = sent_delta >= 1 and after["backlog_packets"] == 0
        print(
            f"[CAN] TX probe {self.target_interface}: "
            f"sent_delta={sent_delta} backlog_delta={backlog_delta} "
            f"backlog={after['backlog_packets']}"
        )
        return ok

    def _parent_usb_interface(self):
        result = subprocess.run(
            ["ip", "-d", "-o", "link", "show", self.target_interface],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        match = re.search(r"parentdev\s+(\S+)", result.stdout)
        return match.group(1) if match else None

    def _unbound_gs_usb_interfaces(self):
        """Bindable gs_usb (1d50:606f) USB interfaces with no driver bound.

        Found via sysfs, NOT via the netdev -- this is the one case
        _parent_usb_interface() cannot see, because the driver never bound so no
        netdev was ever created.

        Only interface number 00 is returned: gs_usb matches `in00` (see
        `modinfo gs_usb`), and iface 01 is the DFU/firmware interface (class
        fe/01/01, 0 endpoints) which always rejects a bind with ENODEV.
        """
        found = []
        try:
            for path in glob.glob("/sys/bus/usb/devices/*-*:*.*"):
                dev = path.rsplit(":", 1)[0]           # iface -> parent device
                try:
                    with open(os.path.join(dev, "idVendor")) as fh:
                        vid = fh.read().strip()
                    with open(os.path.join(dev, "idProduct")) as fh:
                        pid = fh.read().strip()
                    with open(os.path.join(path, "bInterfaceNumber")) as fh:
                        ifnum = fh.read().strip()
                except OSError:
                    continue
                if ((vid, pid) == ("1d50", "606f") and ifnum == "00"
                        and not os.path.exists(os.path.join(path, "driver"))):
                    found.append(os.path.basename(path))
        except Exception as e:
            print(f"[CAN] sysfs scan for unbound gs_usb failed: {e}")
        return sorted(found)

    def _gs_usb_port_disable_node(self):
        """sysfs `disable` node of the hub port the gs_usb adapter sits on.

        Writing 1 then 0 there power-cycles the port -- a true re-enumeration,
        which (unlike a driver rebind) DOES reset the device and clears a
        stalled endpoint. Returns None if the adapter or node isn't found.
        """
        try:
            for path in glob.glob("/sys/bus/usb/devices/*-*:*.*"):
                dev = path.rsplit(":", 1)[0]
                try:
                    with open(os.path.join(dev, "idVendor")) as fh:
                        vid = fh.read().strip()
                    with open(os.path.join(dev, "idProduct")) as fh:
                        pid = fh.read().strip()
                except OSError:
                    continue
                if (vid, pid) != ("1d50", "606f"):
                    continue
                # dev is e.g. /sys/bus/usb/devices/3-1.2 -> hub 3-1, port 2
                name = os.path.basename(dev)                 # "3-1.2"
                if "." not in name:
                    continue
                hub, port = name.rsplit(".", 1)              # "3-1", "2"
                for node in glob.glob(
                        f"/sys/bus/usb/devices/{hub}/{hub}:*/{hub}-port{port}/disable"):
                    return node
        except Exception as e:
            print(f"[CAN] hub-port lookup failed: {e}")
        return None

    def _power_cycle_gs_usb_port(self):
        """Power-cycle the adapter's hub port (software equivalent of a replug).

        Clears a firmware-stalled endpoint that a driver rebind cannot fix.
        Requires the port `disable` node in the sudoers grant; returns False (with
        guidance) when that privilege is missing.
        """
        node = self._gs_usb_port_disable_node()
        if not node:
            return False
        print(f"[CAN] power-cycling adapter hub port via {node} ...")
        for val, label in (("1", "off"), ("0", "on")):
            result = subprocess.run(
                ["sudo", "-n", "tee", node], input=val,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout).strip()
                print(f"[CAN] port power-{label} failed: {err}")
                if "password" in err.lower() or "sudo:" in err.lower():
                    print("[CAN] to allow automatic recovery, add to /etc/sudoers.d/canbus:")
                    print(f"        /usr/bin/tee {node}")
                return False
            time.sleep(2.0 if val == "1" else 0.5)
        # udev re-creates and renames the netdev after re-enumeration.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if self.is_available():
                print(f"[CAN] {self.target_interface} recovered by port power-cycle.")
                return True
            time.sleep(0.3)
        return False

    def _gs_usb_probe_stalled(self):
        """True if the kernel is rejecting gs_usb probe with EPIPE (-32).

        A stalled USB endpoint lives in the ADAPTER's firmware -- typically left
        wedged when a process is killed mid-transmit. A driver rebind only
        re-attaches the driver, it does not reset the device, so the probe keeps
        failing. Only a power-cycle (physical replug) clears it.
        """
        for log in ("/var/log/kern.log", "/var/log/syslog"):
            try:
                with open(log, "r", errors="ignore") as fh:
                    tail = fh.readlines()[-400:]
            except OSError:
                continue
            for line in reversed(tail):
                if "probe with driver gs_usb failed" in line:
                    return "error -32" in line or "err=-32" in line
        return False

    def _bind_unbound_gs_usb(self):
        """Bind a present-but-unbound gs_usb adapter so its netdev appears.

        Recovers the chicken-and-egg case: no netdev -> _is_gs_usb() is False ->
        the normal rebind path is unreachable. Returns True if a netdev appeared.
        """
        ifaces = self._unbound_gs_usb_interfaces()
        if not ifaces:
            return False
        if self._gs_usb_probe_stalled():
            print("[CAN] gs_usb probe is failing with EPIPE (-32): the adapter's USB "
                  "endpoint is stalled in firmware.")
            # A rebind can't clear this, but a hub-port power-cycle re-enumerates
            # the device and does -- try that before asking for a physical replug.
            if self._power_cycle_gs_usb_port():
                return True
            print("[CAN] Could not power-cycle in software -- PHYSICALLY REPLUG the CAN "
                  "adapter.")
            return False
        print(f"[CAN] gs_usb adapter present but unbound ({', '.join(ifaces)}); binding...")
        bound_any = False
        for iface in ifaces:
            result = subprocess.run(
                ["sudo", "-n", "tee", "/sys/bus/usb/drivers/gs_usb/bind"],
                input=iface, capture_output=True, text=True,
            )
            if result.returncode == 0:
                bound_any = True
            else:
                err = (result.stderr or result.stdout).strip()
                print(f"[CAN] bind {iface} failed: {err}")
                if "password" in err.lower() or "sudo:" in err.lower():
                    print("[CAN] passwordless sudo for the gs_usb bind node is missing -- "
                          "install /etc/sudoers.d/canbus (see docs/CAN-SETUP.md).")
                    return False
        if not bound_any:
            return False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self.is_available():
                print(f"[CAN] {self.target_interface} recovered by USB bind.")
                return True
            time.sleep(0.2)
        return False

    def _reset_gs_usb_driver(self):
        parent = self._parent_usb_interface()
        if not parent:
            return False
        print(f"[CAN] resetting gs_usb interface {parent}")
        # Write the parent path to the driver's unbind/bind sysfs nodes. Uses
        # `sudo tee <node>` (not `sudo sh -c 'echo > node'`) so the passwordless
        # sudoers grant can whitelist the two exact node paths literally instead
        # of a shell -- keeping /etc/sudoers.d/canbus a tight, non-root scope.
        commands = [
            ["sudo", "tee", "/sys/bus/usb/drivers/gs_usb/unbind"],
            ["sudo", "tee", "/sys/bus/usb/drivers/gs_usb/bind"],
        ]
        for cmd in commands:
            result = subprocess.run(
                cmd, input=parent, capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"Error executing {' '.join(cmd)}: {(result.stderr or result.stdout).strip()}")
                return False
            time.sleep(0.5)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self.is_available():
                return True
            time.sleep(0.2)
        return False

    def start_can(self):
        """The main entry point used in your script."""
        return self.bring_up_can_buses()

    def bring_up_can_buses(self, adopt=False):
        """Wait for the netdev, then bring the bus up and return its name.

        Idempotent -- safe to call on every init, so a bus left DOWN after a
        replug or reboot is recovered automatically.

        adopt=True skips the reset when the bus is already up and healthy, and falls
        through to the full bring-up when it is not. Read `self.adopted` afterwards for
        which one happened. The netdev wait below always runs first, so adopt never
        skips the no-netdev recovery.
        """
        # Wait for the device to be plugged in and recognized by udev. Bounded:
        # if the adapter is absent or its USB driver never bound, no amount of
        # waiting helps -- fail with an actionable message instead of hanging.
        # Self-heal the no-netdev case up front: if the adapter is on USB but its
        # driver never bound, bind it here. The normal rebind in
        # check_can_interface() can't reach this -- it needs the netdev to find
        # the USB parent, which is exactly what's missing.
        if not self.is_available():
            self._bind_unbound_gs_usb()

        attempts = 0
        while not self.is_available():
            if self.WAIT_TIMEOUT_S > 0 and attempts >= self.WAIT_TIMEOUT_S:
                raise RuntimeError(self._missing_iface_help())
            if attempts % 5 == 0:
                print(f"Searching for {self.target_interface} (check USB connection)... "
                      f"[{attempts}/{self.WAIT_TIMEOUT_S}s]")
                if attempts:            # retry the bind as udev may still be settling
                    self._bind_unbound_gs_usb()
            time.sleep(1)
            attempts += 1

        return self.check_can_interface(adopt=adopt)

    def _missing_iface_help(self):
        """Diagnose why the netdev is missing and return an actionable message."""
        iface = self.target_interface
        lines = [f"CAN interface '{iface}' did not appear after "
                 f"{self.WAIT_TIMEOUT_S}s."]
        # Is the gs_usb adapter even on the USB bus?
        try:
            lsusb = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
        except Exception:
            lsusb = ""
        if "1d50:606f" not in lsusb:
            lines.append("  - The gs_usb adapter (1d50:606f) is NOT on the USB bus. "
                         "Plug in the CAN adapter.")
        else:
            lines.append("  - The gs_usb adapter (1d50:606f) IS present on USB, but no "
                         "netdev was created -- the driver did not bind.")
            unbound = self._unbound_gs_usb_interfaces()
            if unbound:
                lines.append(f"    Unbound CAN interface(s): {', '.join(unbound)}")
            if self._gs_usb_probe_stalled():
                lines.append("    CAUSE: kernel probe fails with EPIPE (-32) -- the adapter's")
                lines.append("      USB endpoint is STALLED in firmware (typically after a")
                lines.append("      process was killed mid-transmit).")
                lines.append("    FIX: physically UNPLUG and REPLUG the CAN adapter. A driver")
                lines.append("      rebind/modprobe cannot reset the device and will not help.")
                lines.append("    Diagnose: sparklib/system/check-can-setup")
                return "\n".join(lines)
            # Auto-recovery needs the passwordless grant; say so when it's absent.
            if subprocess.run(["sudo", "-n", "-l"], capture_output=True,
                              text=True).returncode != 0:
                lines.append("    NOTE: passwordless sudo is unavailable, so auto-recovery "
                             "could not run.")
                lines.append("      Install /etc/sudoers.d/canbus (docs/CAN-SETUP.md) "
                             "to let init self-heal this.")
            lines.append("    Recover now (needs root):")
            lines.append("      sudo modprobe -r gs_usb && sudo modprobe gs_usb")
            lines.append("      # or physically replug the adapter (udev re-creates it)")
        lines.append("  - Diagnose: sparklib/system/check-can-setup")
        return "\n".join(lines)
