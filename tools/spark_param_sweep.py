"""Read every parameter off a live SPARK bus. Writes nothing, ever.

WHY THIS EXISTS

  The fleet's parameter table, configs/baseline/rev_parameter_index.tsv, has no
  product qualifier on it. It carries Status 0 Period through Status 7 Period at
  ids 158-165, and gives 166 and 167 to MAXMotion Max Velocity 0 and MAXMotion
  Max Accel 0. Appendix A of the assembly manual configures ten status frames on
  a SPARK Flex, Status 0 through Status 9. Ten frames cannot be addressed by
  eight parameters, so the table and the manual describe different things and
  nobody has written down which.

  That gap decides whether `spark repair` may write a status period on rig-max-2
  and rig-max, which are SPARK MAX. Guessing it is how a Flex id gets written to
  a MAX, and the repo has already spent a day on one frame layout applied to the
  wrong product.

WHAT SETTLES IT

  The parameter response carries the DEVICE'S OWN type code for the id, in byte
  1. So the controller says what each id is, and its answer is compared against
  what the table claims:

    id 166 answers Uint    -> the table is wrong here; 166 is a period
    id 166 answers Float   -> the table is right; Status 8 lives elsewhere
    id 166 answers Unused  -> the id does not exist on this product

  Parameter 198 is Param Table Version, read-only. It names the table directly.

WHY THIS CANNOT WRITE

  Every frame here is a READ_PARAMETER or GET_PARAMETER_TYPES request, sent as a
  remote frame with no payload. There is no value to append and no write api is
  addressed, so the read/write ambiguity this tool was built around is gone. It
  used to read through SparkAdmin.read_param, which asked on PARAMETER_WRITE
  with a one-byte payload, and that method was deleted.

  The canary stays anyway. It reads a known non-zero parameter twice before the
  sweep and stops if the value moves, which is what would catch a future edit
  that routed this back onto an api that can write. See `_canary`.

USAGE

    uv run python tools/spark_param_sweep.py --dry-run
    uv run python tools/spark_param_sweep.py --id 12
    uv run python tools/spark_param_sweep.py            # every configured id
"""

from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sparklib.config import get as _spark_config                       # noqa: E402
from sparklib import admin as sa        # noqa: E402

INDEX = os.path.join(os.path.dirname(__file__), os.pardir, "sparklib",
                     "data", "rev_parameter_index.tsv")

# Read-only ids, read first: a write to one of these is refused by the device,
# so they probe the read path without putting a value at risk.
PROBE_READ_ONLY = (198, 155, 156, 157)

# Read twice and compared. Provisioned to 20 on this fleet and 250 from the
# factory, so any answer of 0 means the read frame wrote.
CANARY_PARAM = 159

TYPE_NAMES = {0: "Unused", 1: "Int", 2: "Uint", 3: "Float", 4: "Boolean"}
TYPE_CODE_FOR_NAME = {v: k for k, v in TYPE_NAMES.items()}
INDEX_TYPE_TO_CODE = {"UINT32": 2, "INT32": 1, "FLOAT": 3, "BOOL": 4,
                      "BOOLEAN": 4}


# devices also carries the CANcoders, which are not REV controllers and
# answer no SPARK parameter api.
SPARK_GROUPS = ("drive", "steer")


def spark_ids(can_ids):
    """Configured SPARK ids only, with the CANcoders left out."""
    out = set()
    for group, members in vars(can_ids).items():
        if group not in SPARK_GROUPS:
            continue
        out.update(vars(members).values())
    return sorted(out)


def load_index(path=INDEX):
    """{id: {name, type, access, default}} from the fleet's parameter table."""
    rows = {}
    try:
        with open(path) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5 or not parts[0].strip().isdigit():
                    continue
                rows[int(parts[0])] = {"name": parts[1], "type": parts[2],
                                       "access": parts[3], "default": parts[4]}
    except OSError:
        return {}
    return rows


def decode(raw, type_code):
    """The 4 payload bytes read as the type the DEVICE reported."""
    if type_code == 3:
        return round(struct.unpack("<f", struct.pack("<I", raw))[0], 6)
    if type_code == 1:
        return struct.unpack("<i", struct.pack("<I", raw))[0]
    if type_code == 4:
        return bool(raw)
    return raw


def _canary(adm, dev, index, log):
    """Prove the read path is a read before sweeping anything writable.

    Returns (ok, message). A read that is secretly a write of zero shows up as a
    parameter that was non-zero and is now zero, so the value is read twice and
    compared. The check is only conclusive when the first read is non-zero;
    a controller legitimately holding 0 there cannot distinguish the two, and
    the run says so instead of claiming a clean bill.

    READ_PARAMETER carries no payload at all, so this is belt and braces now.
    It stays because it costs two frames and it is what would catch a future
    edit that routed the sweep back onto an api that can write.
    """
    first = _one(adm, dev, CANARY_PARAM)
    if first is None:
        return None, (f"parameter {CANARY_PARAM} did not answer, so the read "
                      "path could not be proven either way")
    log.append(("canary-1", CANARY_PARAM, first))
    if first["raw"] == 0:
        return None, (f"parameter {CANARY_PARAM} read back 0 on the FIRST read. "
                      "Provisioned is 20 and the factory default is 250, so "
                      "either this controller was already zeroed or the read "
                      "itself wrote. STOP -- the two are indistinguishable from "
                      "here, so treat RAM as suspect: power cycle this "
                      "controller, then read the period off the wire with "
                      "`uv run spark status` before doing anything else")
    second = _one(adm, dev, CANARY_PARAM)
    log.append(("canary-2", CANARY_PARAM, second))
    if second is None:
        return None, "the canary answered once and not twice; refusing to sweep"
    if second["raw"] != first["raw"]:
        return False, (f"parameter {CANARY_PARAM} read {first['raw']} then "
                       f"{second['raw']}. Reading it CHANGED it, so these "
                       "frames are writes. STOP -- power cycle this controller "
                       "to restore RAM, and do not run the sweep")
    return True, (f"parameter {CANARY_PARAM} read {first['raw']} twice; the "
                  "read path does not mutate")


def documented_types(adm, dev, lo, hi, wait, log):
    """Which ids the device implements, from GET_PARAMETER_n_TO_n+15_TYPES.

    apiClass 13, sixteen frames for the whole 0-255 range, and it answers the
    question this sweep was built for directly. It carries no read/write
    ambiguity: there is no payload to append a value to.
    """
    out = {}
    for start in range(lo // 16 * 16, hi + 1, 16):
        block = adm.param_types(dev, start_id=start, wait=wait)
        log.append(("types", start, block))
        if block:
            out.update(block)
    return out


def _one(adm, dev, pid, wait=0.5):
    """One parameter as a {'raw'} row, or None. The sweep's only read."""
    raw = adm.read_param_value(dev, pid, wait=wait)
    return None if raw is None else {"raw": raw}


def documented_values(adm, dev, lo, hi, wait, log):
    """Values from READ_PARAMETER, apiClasses 15-22.

    128 frames covering the whole 0-255 table in pairs. A read here cannot be
    mistaken for a write: the request is a remote frame and carries no payload
    to append a value to.
    """
    out = {}
    first = max(lo, 0)
    last = min(hi, sa.PARAM_ID_MAX)
    for pid in range(first - (first % 2), last + 1, 2):
        pair = adm.read_param_pair(dev, pid, wait=wait)
        log.append(("read-pair", pid, pair))
        if pair:
            out[pair["first_id"]] = pair["first"]
            out[pair["second_id"]] = pair["second"]
    return out


def sweep(adm, dev, index, lo, hi, wait, log):
    """Read every id in range. Returns a list of row dicts."""
    rows = []
    types = {}
    for start in range(lo // 16 * 16, hi + 1, 16):
        types.update(adm.param_types(dev, start_id=start, wait=wait) or {})
    for pid in range(lo, hi + 1):
        r = _one(adm, dev, pid, wait=wait)
        if r is not None:
            r["type_code"] = TYPE_CODE_FOR_NAME.get(types.get(pid), 0)
            r["result"], r["result_text"] = 0, "Success"
        log.append(("read", pid, r))
        ref = index.get(pid, {})
        if r is None:
            rows.append({"id": pid, "name": ref.get("name", ""),
                         "answered": False, "device_type": "", "value": "",
                         "raw": "", "result": "", "index_type": ref.get("type", ""),
                         "index_default": ref.get("default", ""), "note": "silent"})
            continue
        code = r["type_code"]
        want = INDEX_TYPE_TO_CODE.get(ref.get("type", "").upper())
        note = []
        if not ref:
            note.append("not in index")
        elif want is not None and code != want and code != 0:
            note.append(f"TYPE MISMATCH: index says {ref['type']}, "
                        f"device says {TYPE_NAMES.get(code, code)}")
        if code == 0:
            note.append("device reports Unused")
        if r["result"] != 0:
            note.append(f"result {r['result_text']}")
        rows.append({"id": pid, "name": ref.get("name", ""), "answered": True,
                     "device_type": TYPE_NAMES.get(code, code),
                     "value": decode(r["raw"], code), "raw": r["raw"],
                     "result": r["result_text"],
                     "index_type": ref.get("type", ""),
                     "index_default": ref.get("default", ""),
                     "note": "; ".join(note)})
    return rows


def report(rows, dev, product, out_dir):
    """Write the TSV and print what it settles."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"param-sweep-id{dev}.tsv")
    cols = ["id", "name", "answered", "device_type", "value", "raw", "result",
            "index_type", "index_default", "note"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    answered = [r for r in rows if r["answered"]]
    mismatch = [r for r in answered if "TYPE MISMATCH" in r["note"]]
    unused = [r for r in answered if "Unused" in r["note"]]
    print(f"\n  id {dev} ({product}): {len(answered)} of {len(rows)} ids answered "
          f"-> {path}")
    if not answered:
        print("    nothing answered. This firmware does not serve parameter "
              "reads over CAN, which is what sparkflex_motor_defaults.yaml "
              "already records for 26.1.6. Use RHC2 over USB-C.")
        return path
    version = next((r for r in answered if r["id"] == 198), None)
    if version:
        print(f"    Param Table Version = {version['value']}")
    for pid in (158, 159, 165, 166, 167):
        r = next((x for x in rows if x["id"] == pid), None)
        if r and r["answered"]:
            print(f"    {pid:>3} {r['name'] or '(not in index)':<28} "
                  f"device={r['device_type']:<8} value={r['value']}")
    if mismatch:
        print(f"    {len(mismatch)} id(s) whose type disagrees with the index:")
        for r in mismatch[:10]:
            print(f"      {r['id']:>3} {r['name']:<28} {r['note']}")
    if unused:
        print(f"    {len(unused)} id(s) the device reports as Unused "
              f"(first: {[r['id'] for r in unused][:8]})")
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id", type=int, action="append",
                   help="device id; repeatable. Default: every configured id")
    p.add_argument("--lo", type=int, default=0, help="first parameter id")
    p.add_argument("--hi", type=int, default=198, help="last parameter id")
    p.add_argument("--wait", type=float, default=0.15, help="reply timeout")
    p.add_argument("--out", default=None, help="output directory")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent and exit without opening the bus")
    args = p.parse_args(argv)

    product = sa.normalise_product(_spark_config().controller_type)
    chan = _spark_config().can.interface
    index = load_index()
    devs = args.id or spark_ids(_spark_config().devices)

    print(f"channel {chan}   product {product}   ids {devs}")
    print(f"parameter index: {len(index)} entries")
    print(f"reading ids {args.lo}-{args.hi}; NO write frame is sent and "
          "PERSIST is never sent")

    if args.dry_run:
        print("\ndry run. Per device this would send:")
        print(f"  {len(PROBE_READ_ONLY)} read-only probes {list(PROBE_READ_ONLY)}")
        print(f"  2 canary reads of parameter {CANARY_PARAM}")
        print(f"  {args.hi - args.lo + 1} sweep reads")
        print("  every frame a remote request with no payload")
        return 0

    stamp = time.strftime("%Y-%m-%d")
    out_dir = args.out or os.path.join(
        os.path.dirname(__file__), os.pardir, "records",
        f"{stamp}-{product}-paramsweep")

    rc = 0
    with sa.SparkAdmin(chan) as adm:
        for dev in devs:
            log = []

            print(f"\nid {dev}: documented read frames (apiClass 13 and 15-22)")
            types = documented_types(adm, dev, args.lo, args.hi, args.wait, log)
            values = documented_values(adm, dev, args.lo, args.hi, args.wait, log)
            if types:
                live = sorted(k for k, v in types.items() if v != "Unused")
                print(f"  apiClass 13 answered: {len(live)} of "
                      f"{len(types)} ids implemented")
                print(f"  implemented ids: {live[:24]}"
                      f"{'...' if len(live) > 24 else ''}")
            else:
                print("  apiClass 13 answered nothing")
            if values:
                print(f"  apiClasses 15-22 answered {len(values)} values")
            else:
                print("  apiClasses 15-22 answered nothing")

            print(f"\nid {dev}: read-only probes")
            for pid in PROBE_READ_ONLY:
                r = _one(adm, dev, pid, wait=args.wait)
                log.append(("probe", pid, r))
                name = index.get(pid, {}).get("name", "?")
                code = TYPE_CODE_FOR_NAME.get(types.get(pid), 0)
                print(f"  {pid:>3} {name:<22} "
                      + ("no answer" if r is None
                         else f"{TYPE_NAMES.get(code, code)} = {decode(r['raw'], code)}"))
            if not types and not values:
                print("  nothing answered; this controller serves no parameter "
                      "reads. Skipping the sweep.")
                continue

            ok, why = _canary(adm, dev, index, log)
            print(f"  canary: {why}")
            if ok is False:
                rc = 1
                continue
            if ok is None:
                print("  refusing to sweep this controller")
                rc = 1
                continue
            rows = sweep(adm, dev, index, args.lo, args.hi, args.wait, log)
            report(rows, dev, product, out_dir)

    print("\n  Nothing was written and nothing was persisted. Compare the "
          "device_type column against index_type: that is what says whether "
          "this product uses the table in rev_parameter_index.tsv.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
