# rig-max: drive overcurrent under load, and an adapter with an intermittent connection, rig-max, eight SPARK MAX on firmware 24.0.1, bus `can0`.
Two findings got tangled together for about an hour because they happened at the
same moment and the tooling blamed the wrong one. They are separate.

Evidence: `records/rig-max-post-rail-cycle-20260910.log`.

## Finding 1: every drive controller latched overcurrent, and no steer one did

A high-speed drive test at scale 0.99. Afterwards:

    id  role       sticky faults            reset_kind
     1  drive/LB   overcurrent              none
     2  steer/LB   -                        none
     3  steer/RB   -                        none
     4  drive/RB   overcurrent, gateDriver  none
     5  drive/RF   overcurrent              none
     6  steer/RF   -                        none
     7  steer/LF   -                        none
     8  drive/LF   overcurrent              none

The split is exact: the faulted set IS the drive set. Nothing rebooted, so this
was not a brownout reset. id 4 additionally latched `gateDriver`, and a second
reading half an hour later found it on three controllers instead, which is the
open item at the end of this file.

This is a real finding about the drive test and it stands on its own. The
per-controller current spans measured during those runs reached 54 A.

## Finding 2: the adapter would not stay enumerated

The failure began mid-run as `OSError errno 100, Network is down`.

`can_diag` afterwards said the CAN side was healthy: link UP, ERROR-ACTIVE,
every controller error counter zero, and no error frames in an 8 s watch. What
had failed was the USB link, and the kernel log says so plainly:

    usb xmit fail 0.. 9          ten in 20 ms
    usb 3-1.3: USB disconnect     28 ms later

The adapter did not wedge. It fell off the bus. Over the session it burned
through seven device numbers, 5, 8, 9, 10, 11, 12 and 15, with three
`device descriptor read/64, error -32` stalls during enumeration. One instance
survived 0.54 s before disconnecting again, another 2.34 s.

The CANivore on the same hub enumerated cleanly every time, so this is the
CANable adapter, its cable, or its hub port, and not the machine.

## The cause, reproduced on demand

With `tools/can_usb_watch.py` running and the drive stack up at 495 tx/s and
1330 rx/s, the operator flicked the adapter with a finger. It dropped at that
instant:

    t+56.5s  usb xmit fail 0.. 9        ten, inside 200 ms
    t+56.7s  usb 3-1.3: USB disconnect, device number 15
    t+57.0s  usb 3-1.3: new full-speed USB device number 16

The CANivore control saw nothing through the same window.

That is the same signature as the mid-run failure, produced by a mechanical
stimulus. So the adapter has a physically intermittent connection, and the rest
of the candidate list is out: EMI, ground shift, transmit saturation and
firmware all fail to explain a finger flick.

It also explains the two things that looked strangest. A high-speed run shakes
the robot, which is the same stimulus applied for longer, so an identical
earlier run surviving is exactly what an intermittent joint does. And every
recovery that appeared to work was the connection happening to make contact
again.

**There is now an acceptance test.** With the watcher running, flick the
adapter. A healthy one does not notice. Use it on the replacement before
trusting it.

## Why it looked random, and why the command order was a red herring

The operator's reading was that `spark status` first left the bus dead while
`spark clear` first recovered it. The enumeration timeline explains that without
any protocol mechanism: with instances lasting 0.54 s and 2.34 s, whether a
command worked depended on whether it happened to run inside a good window.

The sequence bears that out. Two `spark clear` runs did nothing. The third was
refused outright with `can0 does not exist`, which is the adapter gone
from the system entirely. The fourth, immediately after it came back, recovered
all eight. `spark canfix` and a physical replug had both failed in between.

## The tooling's own explanation does not fit

`spark status` tells the operator that a latched fault "stops a controller
broadcasting AND ACKing, which can silence the whole bus." Four of eight were
latched here and all eight went dark.

`tests/adversarial/test_rig_max_drive_overcurrent_silence.py` rules that out off
the robot. Silence exactly the four that faulted and the other four keep
broadcasting. So the latched faults are not what took the bus down, and the
kernel log names what did.

That message should not have been the first thing an operator read, and the FIX
beside it sent us hunting motor power and the CAN chain for an hour
when a meter had already shown 12.20 V at the panel and the controller LEDs were
lit the whole time.

## What reproduces off the robot, and what cannot

Reproducible, and now pinned in
`tests/adversarial/test_rig_max_drive_overcurrent_silence.py`:

- the exact fault pattern, drive latched and steer clean
- a latched controller going dark and `clear_faults` bringing it back
- sticky bits surviving indefinitely until something clears them, which is why
  the record was still readable ten minutes and three clear attempts later
- the key negative above

Not reproducible, and it is the half that caused the outage. `SimBus` is a
Python object with no socket, no USB device and no queueing discipline, so
`usb xmit fail`, a self-disconnect, an enumeration stall and a transmit backlog
that will not drain have nowhere to happen. A test asserts that floor directly
rather than leaving someone to hunt for a repro that cannot exist.

## Two defects this incident found in the tooling

**`spark status` reported a generation it never read.** With zero controllers on
the bus it printed `FLEET ASSUMPTION BROKEN:... the bus reads fw25+`.
`dominant_generation` falls back to its `default` on an empty reading, and three
call sites treated that fallback as an observation. Fixed with
`admin.observed_generation`, which returns None when nothing reported, and
guarded by an AST check over the call sites rather than a test of the helper,
since the helper was already correct.

**`can_diag` offered a command this adapter rejects.** It advised
`ip link set... type can restart-ms 100` on a gs_usb, which rejects restart-ms
outright, measured on rig-max. It now names `spark canfix` for
gs_usb and keeps the generic advice everywhere else.

## How to recognise this again

Three signs, cheapest first.

**The TX LED on the adapter is dark while the host is sending.** No software
needed. If something is transmitting and that LED does not blink, the frames are
not reaching the adapter, so the fault is the USB link and not the CAN bus. This
was the first indicator the operator noticed.

**The kernel log names it.** `journalctl -k` carries the whole signature:

    gs_usb... can0: usb xmit fail 0.. 9     ten, inside 200 ms
    usb 3-1.3: USB disconnect, device number N
    usb 3-1.3: new full-speed USB device number N+1
    usb 3-1.3: device descriptor read/64, error -32   on a marginal contact

Ten numbered transmit failures followed within tens of milliseconds by a
disconnect is the whole tell. The CAN controller state and its error counters
stay clean throughout, which is what makes this look like a dead fleet.

**`spark status` and `spark clear` now say so themselves.** When nothing answers,
both read the kernel log first through `cli.adapter_instability_note` and
report any disconnects, transmit failures, enumeration stalls and
re-enumerations in the last half hour, naming the TX LED check. They stay quiet
on a healthy machine, and a plain `canfix` rebind does not trip them, because a
message that fires after every routine recovery gets ignored.

To watch it happen, `tools/can_usb_watch.py` runs through a whole session and
keeps a second interface as a control.

## Still open

**The gate-driver latches are spreading, and this is the real open item.** At
18:48:36 one controller held gateDriver, id 4. After further driving, at
19:16:02, three held it, ids 1, 5 and 8, and id 4 did not. Both readings are
exactly the drive set with nothing reset. So it is not one unit misbehaving; it
recurs across the drive half under load and moves between units.

The adapter does not explain it. That fault is a USB connector carrying no motor
current, and it cannot latch a gate driver.

What would separate tuning from hardware: read the faults after a run at reduced
drive scale. Latches that track scale are load. Latches that persist at low
output are hardware. The Smart Current Stall Limit on this fleet is 40 A against
REV's 80, the one setting that deviates from factory, and nothing has yet
related the two.

**The adapter needs replacing, not moving.** The flick test settles what it is:
an intermittent connection at the adapter. Replace the unit, or its cable, or
resolder its connector. No software change reaches an intermittent joint.
