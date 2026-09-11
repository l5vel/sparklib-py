"""Spin every drive and steer motor at fixed percent output for a few seconds.

    uv run python tools/spin_all_motors.py
    uv run python tools/spin_all_motors.py --power 0.1 --duration 2.0
    uv run python tools/spin_all_motors.py --sequential
    uv run python tools/spin_all_motors.py --drive-only
    uv run python tools/spin_all_motors.py --steer-only

Answers one question: does every controller actually drive its motor? Run it
before any swerve, kinematics or closed-loop layer is in the picture, so a motor
that never moves is not mistaken for a gain that needs work.

MOVES EVERY MOTOR ON THE BUS. Put the robot on jack stands, or block the wheels.
The tool names every motor it will command, at what duty, and the total seconds
of motion, then waits for a typed y. Output is zeroed and the bus shut down on
every exit path, Ctrl-C included.

Every controller is initialised even when only one group is commanded, and the
idle ones are held at an explicit 0 instead of being left unsent. The heartbeat
enables only the ids it carries, and a SPARK that is not enabled freewheels
where an enabled one brakes. On a coaxial module the drive motor's reaction
torque then turns the steer ring through that freewheeling steer motor, so the
module spins in place while the wheel barely rolls.

Each controller is commanded to 0 the moment it is created, because the enable
heartbeat starts with it and a setpoint left in the controller would otherwise
drive the motor as soon as it is enabled.

hb_lock reads False on rig-max (eight SPARK MAX on firmware 24.0.1). The bit
decoded there, Status 0 byte 6 bit 5, is never set, and the firmware still
honours the enable heartbeat. Proof is applied_output tracking the commanded
duty in the status lines during the hold, so read those rather than hb_lock.

Ids, product and bus name come from the config, so one command covers a sparkmax
and a sparkflex base with no edit.

Exit codes: 0 once every commanded motor has finished its hold, 1 when Ctrl-C
cuts the run short, and 2 when the tool refuses to command anything. An
interrupted run names the motors it never reached.
"""

import argparse
import math
import sys
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import config as cfg
from sparklib import netdev
from sparklib.can_bus import SPARK_ID_MAX, SPARK_ID_MIN

TICK_S = 0.02
STATUS_PERIOD_S = 0.5
SETTLE_S = 2.0
GAP_S = 0.3
REFUSED = 2
INCOMPLETE = 1
PRODUCTS = {"sparkflex": SPARK_FLEX, "sparkmax": SPARK_MAX}


def _fmt_float(value, suffix=""):
    if value is None:
        return "None"
    return f"{value:.3f}{suffix}"


def _fmt_hex(value):
    if value is None:
        return "None"
    return f"0x{value:04X}"


def _group(conf, name):
    """[(label, configured value)] for one devices group, in config order."""
    group = getattr(getattr(conf, "devices", None), name, None)
    if group is None or not hasattr(group, "__dict__"):
        return []
    return [(f"{name} {label}", value) for label, value in vars(group).items()]


def _can_id(value):
    """The configured value as a SPARK can id, or None when it is not one."""
    if isinstance(value, (bool, float)):
        return None
    try:
        can_id = int(value)
    except (TypeError, ValueError):
        return None
    if not SPARK_ID_MIN <= can_id <= SPARK_ID_MAX:
        return None
    return can_id


def _names(specs):
    return ", ".join(f"{label}={can_id}" for label, can_id in specs)


def _print_status(title, motors):
    print(title)
    for label, m in motors:
        print(f"  {label:<12} id={m.id:2d} "
              f"applied={_fmt_float(m.applied_output)} "
              f"voltage={_fmt_float(m.bus_voltage, 'V')} "
              f"current={_fmt_float(m.output_current, 'A')} "
              f"active={_fmt_hex(m.active_faults)} "
              f"sticky={_fmt_hex(m.sticky_faults)} "
              f"hb_lock={m.primary_heartbeat_lock} "
              f"follower={m.is_follower}")


def _print_tx_queue(interface):
    """Print the interface transmit queue, which is where a wedged adapter shows."""
    state = netdev.CanNetdev(interface)._qdisc_state()
    if state is None:
        return
    print(f"  [tx queue] {state['line']}")
    if state["backlog_packets"]:
        print(f"  [tx queue] {state['backlog_packets']} packet(s) backlogged, so "
              "the adapter is not draining TX. `uv run spark canfix` rebinds it.")


def _hold(active, idle, power, duration, all_motors):
    """Command `active` at `power` and `idle` at 0 every tick for `duration`.

    Idle motors get an explicit 0 each tick so the heartbeat keeps them enabled
    and braked, which is what stops a coaxial module rotating on drive torque.
    """
    end = time.time() + duration
    next_status = time.time()
    while time.time() < end:
        for _, m in active:
            m.percent_output(power)
        for _, m in idle:
            m.percent_output(0.0)
        if time.time() >= next_status:
            _print_status("  [status while commanding]", all_motors)
            next_status += STATUS_PERIOD_S
        time.sleep(TICK_S)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="spin_all_motors", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]).strip())
    p.add_argument("--power", type=float, default=0.25,
                   help="percent output in [-1, 1] (default 0.25)")
    p.add_argument("--duration", type=float, default=3.0,
                   help="seconds to hold the command, per motor (default 3.0)")
    p.add_argument("--sequential", action="store_true",
                   help="one motor at a time, %.1f s apart, the rest held at 0"
                        % GAP_S)
    p.add_argument("--interface", help="SPARK bus; defaults to can.interface")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--drive-only", action="store_true",
                     help="command the drive group only")
    grp.add_argument("--steer-only", action="store_true",
                     help="command the steer group only")
    args = p.parse_args(argv)

    if not -1.0 <= args.power <= 1.0:
        p.error(f"--power must be in [-1, 1]; got {args.power}")
    if not (math.isfinite(args.duration) and args.duration > 0.0):
        p.error(f"--duration must be a finite number of seconds above 0; "
                f"got {args.duration}")

    cfg.load_host()
    conf = cfg.get()
    drives = _group(conf, "drive")
    steers = _group(conf, "steer")
    iface = args.interface or getattr(getattr(conf, "can", None), "interface", None)
    ctype = getattr(conf, "controller_type", None)
    product = PRODUCTS.get(ctype)

    if not drives and not steers:
        print("REFUSED: this config names no motors.\n"
              "  FIX: add a devices.drive or devices.steer group to spark.yaml, "
              "each one a label to can id map such as {LF: 14, RF: 12}.")
        return REFUSED
    if args.drive_only and not drives:
        print("REFUSED: --drive-only, and this config has no devices.drive group.\n"
              "  FIX: add devices.drive to spark.yaml, or drop the flag.")
        return REFUSED
    if args.steer_only and not steers:
        print("REFUSED: --steer-only, and this config has no devices.steer group.\n"
              "  FIX: add devices.steer to spark.yaml, or drop the flag.")
        return REFUSED

    bad = [f"{label}={value!r}" for label, value in drives + steers
           if _can_id(value) is None]
    if bad:
        print(f"REFUSED: these devices entries are not SPARK can ids: "
              f"{', '.join(bad)}.\n"
              f"  FIX: give every label in spark.yaml a whole number from "
              f"{SPARK_ID_MIN} to {SPARK_ID_MAX}, such as {{LF: 14, RF: 12}}.")
        return REFUSED
    drives = [(label, _can_id(value)) for label, value in drives]
    steers = [(label, _can_id(value)) for label, value in steers]

    owner = {}
    clash = []
    for label, can_id in drives + steers:
        if can_id in owner:
            clash.append(f"{owner[can_id]} and {label} both use id {can_id}")
        owner[can_id] = label
    if clash:
        print(f"REFUSED: {'; '.join(clash)}, and one id cannot be two motors.\n"
              "  FIX: correct spark.yaml, and reassign the duplicate with "
              "`uv run spark set-id`, which addresses a controller by serial.")
        return REFUSED

    if not iface:
        print("REFUSED: no CAN interface to open.\n"
              "  FIX: set can.interface in spark.yaml to the netdev name from "
              "`ip -d link show type can`, or pass --interface.")
        return REFUSED
    if product is None:
        print(f"REFUSED: controller_type {ctype!r} names no SPARK product.\n"
              "  FIX: set controller_type in spark.yaml to sparkflex or sparkmax.")
        return REFUSED
    try:
        netdev.check_netdev(iface)
    except RuntimeError as err:
        print(f"REFUSED: {err}\n"
              "  FIX: bring the bus up, then `uv run spark canfix` if it stays down.")
        return REFUSED

    all_specs = drives + steers
    if args.drive_only:
        active_specs, idle_specs = drives, steers
    elif args.steer_only:
        active_specs, idle_specs = steers, drives
    else:
        active_specs, idle_specs = all_specs, []

    order = "one at a time" if args.sequential else "all together"
    total_s = args.duration
    if args.sequential:
        total_s = (args.duration * len(active_specs)
                   + GAP_S * max(0, len(active_specs) - 1))
    print(f"bus {iface}, {ctype}: {len(all_specs)} controller(s) enabled, "
          f"{len(active_specs)} commanded")
    print(f"about to spin {_names(active_specs)}")
    print(f"  at {args.power:+.2f} duty, {args.duration:.1f} s each, {order}, "
          f"so {total_s:.1f} s of motion in total")
    if idle_specs:
        print(f"  enabled and held at 0 in brake: {_names(idle_specs)}")
    try:
        answer = input("Wheels off the ground, robot on stands? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nREFUSED: no answer at the prompt, so nothing was commanded.\n"
              "  FIX: run this from a terminal, with the robot on stands, and "
              "answer y.")
        return REFUSED
    if answer.strip().lower() != "y":
        print("REFUSED: not confirmed, so nothing was commanded.\n"
              "  FIX: put the robot on stands with the wheels free, then answer y.")
        return REFUSED

    bus = SparkBus(channel=iface)
    motors = []
    reached = []
    interrupted = False
    try:
        for label, can_id in all_specs:
            motor = bus.init_controller(can_id, product)
            motor.percent_output(0.0)
            motors.append((label, motor))
        by_id = {m.id: (label, m) for label, m in motors}
        active = [by_id[can_id] for _, can_id in active_specs]
        idle = [by_id[can_id] for _, can_id in idle_specs]
        time.sleep(SETTLE_S)

        live = set(bus.live_ids())
        missing = [(label, m.id) for label, m in motors if m.id not in live]
        if missing:
            print(f"REFUSED: no STATUS_0 within {SETTLE_S:.0f} s from:")
            for label, can_id in missing:
                print(f"  - {label} (can id {can_id})")
            print(f"  FIX: check that the motors have power and the CAN cable is "
                  f"on {iface}, then `uv run spark status` to see who answers.")
            return REFUSED

        heartbeat = bus.heartbeat_payload_hex()
        print(f"heartbeat {heartbeat} enables {len(motors)} id(s)")
        _print_status("  [status before command]", motors)
        if not any(m.primary_heartbeat_lock for _, m in motors):
            print("  no controller reports hb_lock, which is what rig-max does. "
                  "Read applied_output below instead.")
            _print_tx_queue(iface)
            print(f"  SparkBus tx_error_count={bus.tx_error_count}")
            print(f"  heartbeat frame in flight: {iface} 02052C80#{heartbeat}\n")

        if args.sequential:
            for label, m in active:
                others = [(lbl, mm) for lbl, mm in active if mm.id != m.id] + idle
                print(f"  -> {label} (can id {m.id})")
                _hold([(label, m)], others, args.power, args.duration, motors)
                m.percent_output(0.0)
                reached.append(label)
                time.sleep(GAP_S)
        else:
            names = ", ".join(f"{lbl}={m.id}" for lbl, m in active)
            print(f"  -> {len(active)} motor(s) together ({names})")
            _hold(active, idle, args.power, args.duration, motors)
            reached.extend(label for label, _ in active)
    except KeyboardInterrupt:
        interrupted = True
        print("\ninterrupted, stopping motors.")
    finally:
        for _, m in motors:
            m.percent_output(0.0)
        time.sleep(0.1)
        if motors:
            _print_status("  [status after stop]", motors)
        bus.shutdown(zero_outputs=True)
        print("all motors zeroed.")

    if interrupted:
        never = [label for label, _ in active_specs if label not in reached]
        print(f"INCOMPLETE: spun {', '.join(reached) or 'nothing'}")
        print(f"  never reached: {', '.join(never) or 'nothing'}")
        return INCOMPLETE
    return 0


if __name__ == "__main__":
    sys.exit(main())
