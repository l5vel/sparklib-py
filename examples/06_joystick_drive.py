"""Drive a whole swerve chassis from a USB joystick.

    uv run python examples/06_joystick_drive.py --dry-run
    uv run python examples/06_joystick_drive.py --device /dev/input/js0

The full path in one file: read a stick, turn it into a chassis velocity, solve
per-module speed and angle, seed each steer axis from its CANcoder, and hold the
angles while the drive wheels run. docs/INTEGRATION.md walks through it.

--dry-run needs no hardware at all. It prints the module states for whatever the
stick is doing and touches no bus, so the math is readable before a wheel turns.

Reads the joystick through the Linux joydev interface, which is in the kernel, so
no extra package is needed. Axis numbers vary by pad: run with --dry-run and
watch which axis moves.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.

MOVES EVERY WHEEL. Put the base on blocks first.
"""

import argparse
import math
import os
import signal
import struct
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, kinematics, steer
from sparklib import config as cfg

JS_EVENT = struct.Struct("IhBB")
JS_AXIS = 0x02
JS_INIT = 0x80


class Joystick:
    """Axis values in -1 to 1, read without blocking the control loop."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.axes = {}

    def poll(self, max_events=256):
        """Drain pending events. Call once per loop tick.

        Bounded, so a device that streams without pause cannot hold the control
        loop open past its period.
        """
        for _ in range(max_events):
            try:
                data = os.read(self.fd, JS_EVENT.size)
            except BlockingIOError:
                return
            if len(data) < JS_EVENT.size:
                return
            _, value, kind, number = JS_EVENT.unpack(data)
            if kind & ~JS_INIT == JS_AXIS:
                self.axes[number] = value / 32767.0

    def axis(self, number):
        return self.axes.get(number, 0.0)

    def close(self):
        os.close(self.fd)


class Chassis:
    """One steer motor, one drive motor and one CANcoder per corner."""

    def __init__(self, bus, conf, product):
        self.ratio = conf.cancoder.steer_gear_ratio
        self.offsets = cancoder.offsets(conf)
        self.corners = {}
        for label in vars(conf.devices.steer):
            steer_id = getattr(conf.devices.steer, label)
            drive_id = getattr(conf.devices.drive, label)
            cc_id = getattr(conf.devices.cancoder, label)
            self.corners[label] = {
                "steer": bus.init_controller(steer_id, product),
                "drive": bus.init_controller(drive_id, product),
                "encoder": cancoder.open_encoder(cc_id, conf.cancoder.bus),
            }

    def seed(self):
        """Teach each SPARK where its wheel points, once, before the loop."""
        for label, c in self.corners.items():
            angle = cancoder.seed_from_absolute(
                c["steer"], c["encoder"], self.offsets[label], self.ratio)
            print(f"  {label}: seeded at {angle:+7.2f} deg")

    def wheel_rad(self, label):
        c = self.corners[label]
        raw = cancoder.read_angle_deg(c["encoder"], settle=False)
        return math.radians(cancoder.wheel_angle_deg(raw, self.offsets[label]))

    def apply(self, states):
        """Send one solved state set to the wheels."""
        for label, (speed, angle) in states.items():
            c = self.corners[label]
            speed, target, _ = kinematics.optimize(
                angle, self.wheel_rad(label), speed)
            c["steer"].position_output(
                cancoder.motor_rotations(math.degrees(target), self.ratio))
            c["drive"].percent_output(speed)

    def stop(self):
        for c in self.corners.values():
            c["drive"].percent_output(0.0)
            c["steer"].percent_output(0.0)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--device", default="/dev/input/js0")
    p.add_argument("--interface", help="SPARK bus netdev; defaults to can.interface")
    p.add_argument("--dry-run", action="store_true",
                   help="print module states, open no bus, move nothing")
    p.add_argument("--axis-strafe", type=int, default=0)
    p.add_argument("--axis-forward", type=int, default=1)
    p.add_argument("--axis-turn", type=int, default=3)
    p.add_argument("--max-speed", type=float,
                   help="m/s; defaults to chassis.max_speed")
    p.add_argument("--max-angular", type=float, default=2.0, help="rad/s")
    p.add_argument("--deadband", type=float, default=0.08)
    p.add_argument("--expo", type=float, default=0.4)
    p.add_argument("--rate-hz", type=float, default=50.0)
    args = p.parse_args(argv)

    if args.rate_hz <= 0:
        print(f"REFUSED: --rate-hz {args.rate_hz:g} sets no loop period.\n"
              "  FIX: pass a positive rate, for example --rate-hz 50.")
        return 2
    if not 0.0 <= args.deadband < 1.0:
        print(f"REFUSED: --deadband {args.deadband:g} lies outside 0 to 1.\n"
              "  FIX: pass something like --deadband 0.08.")
        return 2

    cfg.load_host()
    conf = cfg.get()
    chassis_conf = getattr(conf, "chassis", None)
    if chassis_conf is None:
        print("REFUSED: this config has no `chassis` block, so the wheel "
              "positions are unknown.\n"
              "  FIX: add track_width, wheel_base and max_speed. "
              "sparklib/data/spark.yaml carries a worked example.")
        return 2
    geometry = kinematics.module_geometry(chassis_conf.track_width,
                                          chassis_conf.wheel_base)

    try:
        stick = Joystick(args.device)
    except OSError as err:
        print(f"REFUSED: cannot open {args.device}: {err}\n"
              "  FIX: plug a pad in, or pass --device with the right node. "
              "`ls /dev/input/js*` lists what the kernel found.")
        return 2

    max_speed = args.max_speed or chassis_conf.max_speed
    held = {label: 0.0 for label in geometry}
    period = 1.0 / args.rate_hz

    def solve():
        stick.poll()
        forward, strafe, rotate = kinematics.chassis_from_stick(
            stick.axis(args.axis_strafe), -stick.axis(args.axis_forward),
            stick.axis(args.axis_turn), max_speed, args.max_angular,
            deadband=args.deadband, expo=args.expo)
        states = kinematics.desaturate(kinematics.module_states(
            forward, strafe, rotate, geometry, max_speed,
            hold_angles=held))
        held.update({m: a for m, (_, a) in states.items()})
        return (forward, strafe, rotate), states

    if args.dry_run:
        print("dry run: nothing is opened and nothing moves. Ctrl-C to stop.")
        try:
            while True:
                (f, s, r), states = solve()
                row = "  ".join(
                    f"{m} {sp:+.2f}@{math.degrees(a):+7.1f}"
                    for m, (sp, a) in states.items())
                print(f"\rfwd{f:+.2f} str{s:+.2f} rot{r:+.2f} | {row}",
                      end="", flush=True)
                time.sleep(period)
        except KeyboardInterrupt:
            print()
            return 130
        finally:
            stick.close()

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    missing = [g for g in ("drive", "steer", "cancoder")
               if getattr(conf.devices, g, None) is None]
    if missing:
        print(f"REFUSED: devices is missing {', '.join(missing)}, and a swerve "
              "corner needs all three.\n"
              "  FIX: add each group with one id per corner label. "
              "sparklib/data/spark.yaml carries a worked example.")
        stick.close()
        return 2

    labels = set(vars(conf.devices.steer))
    for group in ("drive", "cancoder"):
        gap = labels - set(vars(getattr(conf.devices, group)))
        if gap:
            print(f"REFUSED: devices.{group} has no id for "
                  f"{', '.join(sorted(gap))}.\n"
                  "  FIX: use the same corner labels in all three groups.")
            stick.close()
            return 2

    offsets = cancoder.offsets(conf)
    if labels - set(offsets):
        print(f"REFUSED: no recorded offset for "
              f"{', '.join(sorted(labels - set(offsets)))}.\n"
              "  FIX: run tools/cancoder_calibrate.py and paste the block it "
              "prints into cancoder.offsets_deg.")
        stick.close()
        return 2

    iface = args.interface or conf.can.interface
    product = SPARK_FLEX if conf.controller_type == "sparkflex" else SPARK_MAX
    print(f"{len(geometry)} corners on {iface}, "
          f"CANcoders on {conf.cancoder.bus}, up to {max_speed} m/s")
    if input("Base on blocks, wheels free? [y/N] ").strip().lower() != "y":
        stick.close()
        return 1

    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(SystemExit(130)))

    bus = None
    chassis = None
    try:
        bus = SparkBus(channel=iface)
        chassis = Chassis(bus, conf, product)
        chassis.seed()
        print("driving. Ctrl-C to stop.")
        while True:
            _, states = solve()
            chassis.apply(states)
            time.sleep(period)
    except SystemExit:
        print("\nstopped.")
        return 130
    finally:
        if chassis is not None:
            chassis.stop()
        if bus is not None:
            bus.shutdown()
        stick.close()


if __name__ == "__main__":
    raise SystemExit(main())
