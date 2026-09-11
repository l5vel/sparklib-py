"""Write a SPARK parameter over CAN and verify the device accepted it.

Frames per REV-Specs spark-frames-2.1.0:
  PARAMETER_WRITE           api 0x0E0  0x02053800|id  5B  [param_id, value_u32_LE]
  PARAMETER_WRITE_RESPONSE  api 0x0E1  0x02053840|id  7B  id, type, value, result
  PERSIST_PARAMETERS        api 0x3FF  0x0205FFC0|id  2B  magic 15011
  PERSIST_PARAMETERS_RESPONSE api 0x014 0x02050500|id 1B  result

Writes to RAM only unless --persist is given. Sends no setpoints and starts no
heartbeat, so the controller stays disabled and cannot actuate.

    uv run python tools/spark_param_write.py --id 12 --param 159 --value 20
    uv run python tools/spark_param_write.py --id 12 --param 159 --value 20 --persist
"""
import argparse
import struct
import sys
import time

import can

CH = "can0"
PARAM_WRITE, PARAM_WRITE_RESP = 0x02053800, 0x02053840
PERSIST, PERSIST_RESP = 0x0205FFC0, 0x02050500
PERSIST_MAGIC = 15011

# The data-port safety interlock. Refused here for the same reason
# spark_admin.write_param refuses it: a hard limit is a safety interlock,
# and disabling or repolarising one is never a repair a general-purpose writer
# should be able to make by typing a number.
#
# This guard is dated. On the limit-switch polarities on ids 13, 15
# and 16 arrived at True, which made both hard limits read as REACHED and
# silently blocked three of the four swerve modules until. What wrote
# them was never established, and this script -- unguarded, general-purpose, and
# last edited inside the six-hour window in which it happened -- was the only
# tool on the machine that could have. That is suggestive and not a conclusion,
# and it is reason enough that it should not be able to.
#
# The sanctioned exception is tools/spark_limit_polarity_repair.py, which writes
# only 50 and 51, only False or True, never 52 or 53, and refuses while the
# drive stack is running.
PROTECTED_PARAMS = {
    0: "CAN ID",
    50: "Limit Switch Fwd Polarity",
    51: "Limit Switch Rev Polarity",
    52: "Hard Limit Fwd En",
    53: "Hard Limit Rev En",
}

RESULT = {0: "Success", 1: "Invalid ID", 2: "Mismatched Type",
          3: "Access Mode", 4: "Invalid", 5: "Not Implemented"}
PTYPE = {0: "Unused", 1: "Int", 2: "Uint", 3: "Float", 4: "Boolean"}


def _await(bus, arb, wait):
    end = time.time() + wait
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is not None and m.arbitration_id == arb and not m.is_remote_frame:
            return bytes(m.data)
    return None


def write_param(bus, dev, param_id, raw_value, wait=1.0):
    payload = bytes([param_id]) + struct.pack("<I", raw_value)
    bus.send(can.Message(arbitration_id=PARAM_WRITE | dev, data=payload,
                         is_extended_id=True))
    d = _await(bus, PARAM_WRITE_RESP | dev, wait)
    if d is None or len(d) < 7:
        return None
    return {"param_id": d[0], "type": PTYPE.get(d[1], d[1]),
            "value": int.from_bytes(d[2:6], "little"), "result": d[6],
            "result_text": RESULT.get(d[6], f"unknown({d[6]})")}


def persist(bus, dev, wait=3.0):
    bus.send(can.Message(arbitration_id=PERSIST | dev,
                         data=struct.pack("<H", PERSIST_MAGIC),
                         is_extended_id=True))
    d = _await(bus, PERSIST_RESP | dev, wait)
    return None if d is None else d[0]


def measure_period(bus, dev, api, seconds):
    """Observed broadcast period in ms for one status api, or None if silent."""
    arb = 0x02050000 | (api << 6) | dev
    n, end = 0, time.time() + seconds
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is not None and m.arbitration_id == arb:
            n += 1
    return round(seconds * 1000.0 / n, 1) if n else None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--param", type=int, required=True)
    p.add_argument("--value", type=int, required=True, help="raw uint32 value")
    p.add_argument("--float", dest="as_float", action="store_true",
                   help="reinterpret --value as a float before encoding")
    p.add_argument("--persist", action="store_true", help="also commit to flash")
    p.add_argument("--check-api", type=lambda s: int(s, 0), default=None,
                   help="status api to measure before/after, e.g. 0x2E1")
    p.add_argument("--window", type=float, default=4.0)
    args = p.parse_args(argv)

    if args.param in PROTECTED_PARAMS:
        sys.exit(
            f"refusing to write parameter {args.param} "
            f"({PROTECTED_PARAMS[args.param]}): it is part of the data-port "
            "safety interlock. One bit withholds output in its own direction "
            "only; both together withhold all of it. A triggered "
            "limit is a state to respect, not a value to change from here. If a "
            "polarity genuinely needs correcting, use "
            "tools/spark_limit_polarity_repair.py, which writes only 50 and 51 "
            "and refuses while the drive stack is running.")

    raw = (struct.unpack("<I", struct.pack("<f", float(args.value)))[0]
           if args.as_float else args.value)

    bus = can.Bus(interface="socketcan", channel=CH)
    try:
        if args.check_api is not None:
            before = measure_period(bus, args.id, args.check_api, args.window)
            print(f"before: api 0x{args.check_api:03X} period = {before} ms")

        r = write_param(bus, args.id, args.param, raw)
        if r is None:
            print(f"PARAMETER_WRITE param {args.param} -> NO RESPONSE "
                  f"(not implemented on this firmware?)")
            return 1
        print(f"PARAMETER_WRITE param {r['param_id']} type={r['type']} "
              f"readback={r['value']} -> {r['result_text']}")
        if r["result"] != 0:
            return 1

        if args.check_api is not None:
            after = measure_period(bus, args.id, args.check_api, args.window)
            print(f"after:  api 0x{args.check_api:03X} period = {after} ms")

        if args.persist:
            code = persist(bus, args.id)
            print(f"PERSIST_PARAMETERS -> "
                  + ("NO RESPONSE" if code is None
                     else f"{RESULT.get(code, code)} (code {code})"))
            return 0 if code == 0 else 1
        else:
            print("RAM only -- not persisted. A power cycle reverts this.")
        return 0
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
