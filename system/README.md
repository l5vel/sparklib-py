# system/

Five optional files that make a CAN adapter behave itself across reboots and
replugs. Everything in this directory is convenience. Install none of it and the
library works, as long as you bring the interface up yourself.

Install them when you want the bus to come back on its own, which is what you
want on a robot that gets power-cycled and on a bench where the adapter gets
unplugged a dozen times a day.

| File | What it buys you | Skip it if |
| --- | --- | --- |
| `99-spark-can.rules` | a stable name for each adapter, and bring-up on every plug | you have one adapter and are happy with `can0` |
| `spark-can.service` | the udev hook that runs the bring-up | you run `ip link set ... up` yourself |
| `spark-can-bringup` | sets 1 Mbit, the queue length and bus-off restart | same |
| `canbus.sudoers` | `spark canfix` recovers a wedged adapter without a password | you would rather type a password, or replug by hand |
| `check-can-setup` | a read-only doctor that says which layer is broken | nothing; it changes no state and is worth keeping |

## What each one is for

**`99-spark-can.rules`** names an adapter by its USB vendor and product id, so
the same dongle is the same netdev on every boot. Kernel `canN` numbering follows
enumeration order, which moves. One adapter usually survives that; two adapters
swap under you, and a rig with a SPARK bus and a sensor bus wants each to stay
itself.

The rule also tags the netdev for systemd, so every plug event starts the
bring-up below. Firing on the plug rather than at boot is the point: a replug or a
USB re-enumeration then brings the link back on its own. The commonest cause of a
bus disappearing mid-session is a shared hub renegotiating, and this recovers
from that without anyone noticing.

**`spark-can.service` and `spark-can-bringup`** set the bitrate, the transmit
queue length and the bus-off restart interval. A CAN netdev arrives DOWN with no
bitrate, so something has to configure it, and doing it here means it happens the
same way every time. The script is idempotent and safe to run by hand.

**`canbus.sudoers`** grants two exact sysfs paths, the gs_usb driver's unbind and
bind nodes. That is what lets `spark canfix` clear a wedged transmit FIFO, which
survives `ip link down/up` and needs a USB-level rebind. The grant names the two
node paths rather than a shell, so it stays a narrow permission.

**`check-can-setup`** reads state and reports. It sends nothing, needs no sudo,
and tells you which of the adapter, the driver, the netdev or the wire is the one
that is wrong.

## Running without any of it

Bring the interface up by hand and everything else works:

```console
sudo modprobe gs_usb
sudo ip link set can0 up type can bitrate 1000000
uv run spark status
```

Set `can.interface` in `spark.yaml` to whatever name the kernel gave you, and
`can.skip_gs_usb_rebind: true` if you would rather the library never touch the
USB driver. `spark canfix` then reports what it would do and leaves the rebind to
you.

## Installing them

```console
sudo cp 99-spark-can.rules /etc/udev/rules.d/
sudo cp spark-can-bringup  /usr/local/sbin/
sudo cp spark-can.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo udevadm control --reload-rules && sudo udevadm trigger

# optional, and edit YOURUSER first
sudo install -m 0440 canbus.sudoers /etc/sudoers.d/canbus
sudo visudo -c
```

The vendor and product ids in the rules file are for candleLight and gs_usb
adapters (`1d50:606f`). Run `lsusb` and edit them for anything else, and add a
second line per adapter if you have more than one.

[../docs/CAN-SETUP.md](../docs/CAN-SETUP.md) is the full path from a bare machine
to a scrolling `candump`.
