"""The check to run before the first setpoint, and what each answer means.

    uv run python examples/03_startup_gate.py --interface can0 --ids 1,2,3,4

Sends no setpoint and starts no heartbeat, so nothing can move while this runs.
Exit 0 means every controller will apply output. Exit 1 means at least one will
not, and it says which and why.

Why this exists. "Is it talking" and "will it move" are different questions, and
the second is the one that goes unasked. On one rig, three of eight controllers
accepted 1579 setpoint frames each, applied exactly 0.000 and drew 0.00 A,
because both data-port hard limits read as REACHED. The base drove on five motors
for an unknown number of sessions and nothing noticed, because every controller
was broadcasting the whole time.

Lift this into your own startup path. Run it after the bus is up and before you
enable anything.
"""

import argparse

from sparklib import admin as sa


def blocked_reasons(reading):
    """Why this controller will not apply output, or an empty list."""
    reasons = []

    # An all-ones frame saturates every field at once, so no bit in it is a reading.
    if reading["implausible"]:
        return ["STATUS_0 is not a measurement: "
                + sa.describe_implausible(reading["implausible"])]

    if reading["hard_forward_limit"]:
        reasons.append("hard forward limit reached")
    if reading["hard_reverse_limit"]:
        reasons.append("hard reverse limit reached")
    if reading["primary_heartbeat_lock"]:
        reasons.append("locked to another heartbeat source")
    for bit in reading["faults"]:
        reasons.append(f"active fault: {bit}")
    return reasons


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", default="can0")
    p.add_argument("--ids", required=True, help="comma-separated controller ids")
    p.add_argument("--window", type=float, default=3.0, help="seconds to listen")
    args = p.parse_args()

    want = [int(x) for x in args.ids.split(",")]

    with sa.SparkAdmin(args.interface) as adm:
        status = sa.collect_status(adm.bus, args.window)

    # Decode for the generation the frames are, never for the product.
    generation = sa.dominant_generation(status)
    print(f"bus {args.interface}: {len(status)} controller(s), generation {generation}")

    blocked, silent = {}, []
    for dev in want:
        if dev not in status:
            silent.append(dev)
            continue
        reasons = blocked_reasons(sa.normalised_reading(status[dev], generation))
        if reasons:
            blocked[dev] = reasons

    for dev in silent:
        print(f"  id {dev}: SILENT -- nothing arrived. Try `spark clear`, which "
              "wakes a bus that went quiet after a power cycle.")
    for dev, reasons in sorted(blocked.items()):
        print(f"  id {dev}: BLOCKED -- {'; '.join(reasons)}")
        for r in reasons:
            bit = r.split("active fault: ")[-1] if r.startswith("active fault") else None
            if bit:
                # remedy_for needs the generation or it gives 25+ advice.
                print(f"      {sa.remedy_for([bit], generation)}")

    if silent or blocked:
        print(f"\nREFUSING to start: {len(silent)} silent, {len(blocked)} blocked.")
        return 1
    print(f"\nall {len(want)} controller(s) will apply output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
