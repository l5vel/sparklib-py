# Getting a CAN bus up on Linux

You have SPARK controllers, a USB CAN adapter and a Linux machine, and nothing
talks yet. This is the path from there to `spark status` printing your motors.

None of it is specific to sparklib. Any SocketCAN program needs the same things,
so if `candump` already scrolls against your controllers, jump to
[the config](#8-the-config).

## 1. What the pieces are

On a roboRIO, CAN is wired into the board and WPILib hands you a bus. On a Linux
box you build one, out of four layers that each fail in their own way:

| layer | what it is | how it fails |
| --- | --- | --- |
| the adapter | a USB dongle with a CAN transceiver | unplugged, or its endpoint stalls |
| the kernel driver | `gs_usb`, `slcan`, or a vendor module | not loaded, or never bound to the device |
| the netdev | `can0`, or whatever udev names it | exists but is DOWN, or sits at the wrong bitrate |
| the wire | two conductors and two terminators | one terminator, a broken splice, or no power at the controllers |

`spark status` finding nothing looks identical at all four. The doctor in
section 7 tells them apart.

## 2. Packages

Debian and Ubuntu:

    sudo apt install can-utils iproute2 usbutils

`can-utils` gives you `candump`, `cansend` and `cangen`, which check the bus with
none of this library involved. `iproute2` gives `ip link`, which configures a CAN
interface. `usbutils` gives `lsusb`.

Python side:

    uv add sparklib-py

That pulls `python-can`, `bitstring` and `pyyaml`, and nothing else.

**One version pin matters.** `python-can` 4.6.0 and 4.6.1 drop transmitted frames
on gs_usb adapters. On a candleLight dongle install `sparklib-py[gsusb]`, which
pins 4.5.0. On any other adapter take the current release.

## 3. Kernel modules

Most adapters use a module that ships with the kernel:

| adapter | vid:pid | module |
| --- | --- | --- |
| candleLight, CANable in candleLight mode, CANtact | `1d50:606f` | `gs_usb` |
| CANable in slcan mode | `16d0:117e` | `slcan`, plus `slcand` |
| PEAK PCAN-USB | `0c72:*` | `peak_usb` |
| CTRE CANivore | `29ca:4481` | `canivore-usb`, out of tree, from CTRE |

Check it loaded:

    lsusb | grep -i -e 1d50 -e 16d0
    lsmod | grep -e gs_usb -e slcan
    ip -d link show type can

`lsusb` showing the device while `ip link` shows no CAN netdev means the driver
did not bind. Load it by hand:

    sudo modprobe gs_usb

To load it at every boot, name it under `/etc/modules-load.d/`:

    echo gs_usb | sudo tee /etc/modules-load.d/can.conf

An out-of-tree module has to be there before anything else works. CTRE's CANivore
module in particular loads on first hardware open otherwise, which races with
whatever configures the interface at boot.

## 4. Bringing the interface up

A CAN netdev arrives DOWN and with no bitrate. Set both:

    sudo ip link set can0 up type can bitrate 1000000
    sudo ip link set can0 txqueuelen 1000

**1 Mbit, classic CAN, never FD.** Every SPARK speaks 1 Mbit classic CAN. A bus
configured for FD, or for 500k, enumerates cleanly and receives nothing.

Check it took:

    ip -d link show can0

You want `state UP` and `bitrate 1000000`. Then watch the wire:

    candump can0

A healthy SPARK broadcasts continuously, so this scrolls immediately. Silence
means the controllers have no power, the wire is broken, or the bitrate is wrong.
Once it scrolls, the hard part is behind you.

## 5. Making it survive a reboot, if you want that

Everything from here to section 7 is convenience. The library works on a bus you
bring up by hand, so skip this part and come back when typing `ip link` a dozen
times a day starts to grate.

What it buys you is a bus that returns on its own after a reboot, a replug, or a
USB hub renegotiating mid-session. [../system/README.md](../system/README.md)
describes each file and what it is worth on its own.

### A stable name per adapter

Kernel `canN` numbering follows enumeration order, which moves between boots.
With one adapter that rarely bites. With two it does, and the buses swap under
you.

The rig these measurements came from runs two adapters, which is common once a
robot carries more than motors:

| bus | adapter | speed | what is on it |
| --- | --- | --- | --- |
| the SPARK bus | candleLight gs_usb | 1 Mbit classic CAN | eight SPARK controllers |
| the sensor bus | CTRE CANivore | 1 Mbit with a 2 Mbit data phase, CAN FD | four CANcoders |

Those are separate buses at different speeds, so **device ids are unique per bus
rather than per robot**. A CANcoder at id 1 and a SPARK at id 1 are both correct
and both reachable, each on its own wire. That is why the config groups ids by
role instead of flattening them, because a rule written as "id 1 is the CANcoder"
breaks as soon as a second bus exists.

### Name the adapters, because `can0` moves

The kernel hands out `can0`, `can1` and so on in the order adapters enumerate,
and that order is not stable. Two adapters can swap names across a reboot, or
after a replug, or when one takes slightly longer to come up. Nothing warns you.
The bus is up, the name resolves, and the frames you send reach the wrong wire.

With one adapter it costs you nothing. With two it is the difference between
addressing eight motor controllers and addressing four encoders, and both buses
answer, so the failure looks like the devices rather than the wiring.

A udev rule keyed on vendor and product id gives each adapter a name it keeps:

    sudo cp system/99-spark-can.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules && sudo udevadm trigger

Pick names that say what the adapter IS, not what you plug it into today. The
examples throughout these docs are `gsusb` for the candleLight adapter carrying
the SPARKs and `canivore` for the CTRE adapter carrying the CANcoders, because
those are the two products. A name like `steer_bus` ages badly the moment you
move a device.

The shipped rule keys on the candleLight `1d50:606f`. Edit the vid:pid to match
yours, add a line per adapter, then put each name into `spark.yaml` under
`can.interface` and `cancoder.bus`. `lsusb` gives you the ids, and
`udevadm info -a -p /sys/class/net/can0` gives you every attribute you could
match on if vid:pid is not enough, which happens when two identical adapters
are in the same machine. Those are told apart by the USB port path, `KERNELS==`,
so the name follows the socket rather than the device.

Names are yours throughout. sparklib only ever touches the interface you name in
the config, so the other bus stays with whatever owns it, and nothing in the
library defaults to a name from the rig it was written on.

### Bring-up on every plug

The same rules file starts `spark-can.service` on each udev `add`, which sets the
bitrate and queue length so a plug is all it takes:

    sudo cp system/spark-can-bringup /usr/local/sbin/
    sudo cp system/spark-can.service /etc/systemd/system/
    sudo systemctl daemon-reload

Firing on the plug rather than at boot is the useful part, since a replug or a
USB re-enumeration then recovers on its own.

## 6. Letting the library recover the adapter

A gs_usb dongle can wedge its transmit FIFO in a way that `ip link down/up`
leaves wedged. A USB-level unbind and bind clears it, and a firmware-stalled
endpoint wants a genuine re-enumeration on top of that. `spark canfix` does both
once it can reach the two sysfs nodes without a password:

    sudo install -m 0440 system/canbus.sudoers /etc/sudoers.d/canbus
    sudo visudo -c

Edit `YOURUSER` first. The grant names two exact sysfs paths rather than a shell,
so it stays a narrow permission.

Prefer to keep root out of it? Leave the grant uninstalled and set
`can.skip_gs_usb_rebind: true` in `spark.yaml`. The library then leaves the USB
driver alone, and a replug by hand does the same job.

## 7. The doctor

    bash system/check-can-setup

Read-only, sends nothing, needs no sudo. It reports which of the four layers is
the one that is wrong, which is the question you actually have.

## 8. The config

Copy the shipped example and point at it:

    cp "$(uv run python -c 'import sparklib.config as c; print(c.SHIPPED_CONFIG)')" ./spark.yaml
    export SPARKLIB_CONFIG=$PWD/spark.yaml

Two fields before anything works:

    controller_type: sparkflex     # or sparkmax
    can:
      interface: gsusb             # whatever you named it above

Then the device ids. `spark status` lists what is on the bus, so fill them in
from what answers:

    devices:
      drive: {LF: 14, RF: 12, LB: 16, RB: 10}
      steer: {LF: 15, RF: 13, LB: 17, RB: 11}

Role and position names are yours. One motor on a test bench is a valid config:

    devices:
      main: {motor: 1}

Finally, record the serials once, so a controller that loses its CAN id stays
addressable:

    uv run spark learn-serials --write

### A config per rig

sparklib reads one file describing one bus, and `$SPARKLIB_CONFIG` selects it.
Nothing keys on hostname. Running several robots off one checkout means a file
each, selected per machine from your shell profile:

    # ~/.profile on the robot
    export SPARKLIB_CONFIG=/etc/spark/$(hostname).yaml

or from a systemd unit's `Environment=`. Keeping the binding in your deployment
is what lets one library serve rigs it has never heard of.

## 9. When it still does not work

Work down the layers, not across them:

    lsusb                                   # is the adapter on the USB bus?
    ip -d link show type can                # did a netdev appear, and is it UP?
    candump can0                            # is anything on the wire?
    uv run spark status                     # does sparklib see controllers?

A silent bus behind a live adapter is almost always one of three things: the
controllers have no motor-rail power, the bus is missing a 120 ohm terminator at
one end, or every controller holds a latched fault.

### Is anything powered?

SPARKs run off the motor rail. Sensors on a second bus usually run off the logic
rail, so a sweep that finds your CANcoders and no SPARKs means motor power, a
breaker or an e-stop is off. The controllers cannot talk without it.

Three host-side readings settle it without touching the robot:

    ip -s link show can0            # RX 0 packets means nothing is broadcasting
    cansend can0 123#DEADBEEF       # standard-id, inert: REV uses 29-bit ids
    tc -s qdisc show dev can0       # backlog above 0 while TX packets stays 0

A CAN frame needs one other node to acknowledge it. With none, it retries
forever, so the queue shows the packet requeued while the interface transmit
counter never moves. That is a bus with nothing powered on it rather than a dead
adapter, which section 4 settles separately.

### A whole bus that went quiet after a power cycle

Every controller can be powered and acknowledging while none of them broadcasts,
because a latched fault silences a SPARK's transmitter completely. In order:

1. Motor power on, status LEDs lit or blinking?
2. `uv run spark clear`. Read `spark faults` first: clear erases the record.
3. Replug the adapter physically. A driver rebind is sometimes not enough.
4. Check termination: about 60 ohms across CANH and CANL, power off.
5. Connect REV Hardware Client over USB-C, which also clears latched faults and
   brings silent controllers back. This is the manual recovery when nothing
   automatic reaches them.

### A degraded adapter looks exactly like unpowered motors

A failing gs_usb dongle delivers zero received frames while logging nothing at
the socket level, which is indistinguishable from a bus with no power on it. The
kernel knows the difference:

    dmesg | grep gs_usb             # usb xmit fail, or Unexpected unused echo id

Check that before chasing breakers and LEDs. It has cost a debugging session
already.

### Recovery is asymmetric

A bus lost mid-run and a bus missing at startup are not the same problem. Once
something is watching, a controller that comes back is noticed and can be
reconfigured. A controller missing when your program starts is different: the
check that fails usually runs before the watchdog that would notice it returning,
so power coming good thirty seconds later changes nothing and every retry repeats
the identical failure until a person intervenes.

If you are building startup logic, that asymmetry is worth designing for. Start
the watcher before the check, or make the check retry rather than exit.

[FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md) covers the failure modes themselves.
