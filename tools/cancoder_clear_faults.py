"""Clear the CANcoder sticky faults that one clear_sticky_faults() call leaves.

    uv run python tools/cancoder_clear_faults.py                  # every corner
    uv run python tools/cancoder_clear_faults.py --corner RF
    uv run python tools/cancoder_clear_faults.py --corner RF --max-attempts 5
    uv run python tools/cancoder_clear_faults.py --dry-run        # read only

A single clear handles most faults. BootDuringEnable and a few firmware-specific
bits re-assert immediately when their trigger condition was live during the
previous boot, which is why this tool exists. It reads the sticky field and
decodes every bit that is set, retries the generic clear a few times, then falls
back to the per-fault clear methods phoenix6 exposes on some versions. Whatever
survives is reported bit by bit.

The read comes first because the clear destroys it. Sticky bits are the only
record a brownout or a reboot leaves behind, so keep this output. --dry-run
reads and decodes without clearing anything.

A silent encoder is not a clean one. phoenix6 answers a read from a missing
device with a zero and a bad StatusCode rather than raising, so every read here
is believed only when the status that arrived with it is OK. A corner that never
answered is reported as unread and fails the run.

Bit positions in the sticky fault field move between firmware versions, so the
decoder carries the common positions and the observed ones together. Bit 19
reads as BootDuringEnable on firmware 436273408.

`spark clear` is the SPARK equivalent. This is the CTRE one, and neither tool
reaches the other's devices. Resetting a CANcoder magnet_offset is a flash write
and a separate job; nothing here writes configuration or commands motion.

Exit codes: 0 when every selected corner ends clean, 1 when a fault survives, a
corner fails or the run is interrupted, and 2 when the tool refuses before it
reaches the bus.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import sys
import time

from sparklib import cancoder
from sparklib import config as cfg

BUS_WARMUP_S = 1.0
MAX_DEVICE_ID = 62

# Sticky fault bit names: the common positions plus the observed ones.
FAULT_BITS = {
    1 << 0: "Hardware",
    1 << 1: "Undervoltage",
    1 << 2: "BootDuringEnable",
    1 << 3: "UnlicensedFeatureInUse",
    1 << 16: "BadMagnet",
    1 << 17: "BadMagnetField",
    1 << 18: "RemoteSensorReset",
    1 << 19: "BootDuringEnable_v2",
}

# Per-fault clear methods some phoenix6 versions expose.
PER_FAULT_CLEARS = [
    "clear_sticky_fault_hardware",
    "clear_sticky_fault_undervoltage",
    "clear_sticky_fault_boot_during_enable",
    "clear_sticky_fault_unlicensed_feature_in_use",
    "clear_sticky_fault_bad_magnet",
    "clear_sticky_fault_bad_magnet_field",
    "clear_sticky_fault_remote_sensor_reset",
]

# One line per corner in the summary, keyed by what clear_one returned.
VERDICTS = {
    "clean": "already clean",
    "cleared": "CLEARED",
    "persists": "STILL HAS FAULTS (power-cycle may be needed)",
    "unread": "NOT READ (this id never answered)",
    "read-only": "FAULTS PRESENT, nothing cleared (--dry-run)",
    "failed": "FAILED (see above)",
    "interrupted": "INTERRUPTED part way (device state unknown)",
    "skipped": "NOT REACHED (run stopped first)",
}
CLEAN_VERDICTS = ("clean", "cleared")
UNFINISHED_VERDICTS = ("interrupted", "skipped")


def _hex(field):
    """A sticky field as hex, or a marker when the read itself failed."""
    return f"0x{field:08X}" if field >= 0 else "<unavailable>"


def _decode(field):
    """Every set bit, named where a name is known, unknown bits kept visible."""
    if field == 0:
        return ["(none)"]
    out = []
    matched = 0
    for mask, name in FAULT_BITS.items():
        if field & mask:
            out.append(f"0x{mask:08X} ({name})")
            matched |= mask
    if field & ~matched:
        out.append(f"0x{(field & ~matched):08X} (unknown bits)")
    return out


def _status_ok(status):
    """True when phoenix6 calls this StatusCode good, or cannot say."""
    is_ok = getattr(status, "is_ok", None)
    return bool(is_ok()) if callable(is_ok) else True


def _read_sticky(encoder):
    """The sticky fault field as an int, or -1 when nothing trustworthy arrived."""
    try:
        signal = encoder.get_sticky_fault_field()
        field = int(signal.value)
    except Exception as err:            # noqa: BLE001 - a failed read is a result
        print(f"    WARNING: get_sticky_fault_field() failed: {err}")
        return -1
    status = getattr(signal, "status", None)
    if status is not None and not _status_ok(status):
        print(f"    WARNING: the read came back with StatusCode {status}.")
        print("    phoenix6 hands back a zero with a bad status when a device is "
              "silent, so this reads as a missing encoder, not a clean one.")
        return -1
    return field


def _try_clear(encoder, method_name=None):
    """Call a clear method, or report that this phoenix6 version lacks it."""
    method = getattr(encoder, method_name or "clear_sticky_faults", None)
    if method is None:
        return False, "<method missing>"
    try:
        status = method()
    except Exception as err:            # noqa: BLE001 - best effort, keep going
        return False, f"<exception: {err}>"
    suffix = "" if _status_ok(status) else "  (not OK)"
    return True, f"{status}{suffix}"


def clear_one(label, device_id, bus, max_attempts=3, settle_s=0.5, dry_run=False):
    """Read, clear and re-read one CANcoder. Returns a key of VERDICTS."""
    print(f"\n=== {label} (CANcoder id {device_id}, bus '{bus}') ===")
    encoder = cancoder.open_encoder(device_id, bus)
    time.sleep(BUS_WARMUP_S)

    before = _read_sticky(encoder)
    if before < 0:
        print(f"  [{label}] sticky faults unreadable. Check the CAN id, the "
              "wiring and the adapter before looking at software.")
        return "unread"
    print(f"  initial: sticky_faults = {_hex(before)}")
    for desc in _decode(before):
        print(f"    bit set: {desc}")
    if before == 0:
        print(f"  [{label}] already clean. Nothing to do.")
        return "clean"
    if dry_run:
        print(f"  [{label}] dry run: bits read, nothing cleared.")
        return "read-only"

    current = before
    for attempt in range(1, max_attempts + 1):
        print(f"  attempt {attempt}: clear_sticky_faults() ...")
        _, status = _try_clear(encoder)
        print(f"    StatusCode: {status}")
        time.sleep(settle_s)
        current = _read_sticky(encoder)
        print(f"    after: sticky_faults = {_hex(current)}")
        if current == 0:
            print(f"  [{label}] CLEARED after {attempt} generic attempt(s).")
            return "cleared"

    print(f"  generic clear left {_hex(current)}; trying per-fault methods")
    any_method_present = False
    for method_name in PER_FAULT_CLEARS:
        called, status = _try_clear(encoder, method_name)
        if not called and status == "<method missing>":
            continue
        any_method_present = True
        print(f"    {method_name}() -> {status}")
    if not any_method_present:
        print("    (this phoenix6 version exposes no per-fault clear methods)")

    time.sleep(settle_s)
    final = _read_sticky(encoder)
    print(f"  final: sticky_faults = {_hex(final)}")
    if final < 0:
        print(f"  [{label}] the read-back never answered, so whether the clear "
              "took is unknown.")
        return "unread"
    if final == 0:
        print(f"  [{label}] CLEARED via per-fault methods.")
        return "cleared"

    print(f"  [{label}] NOT CLEARED. Persistent bits:")
    for desc in _decode(final):
        print(f"    {desc}")
    print()
    print("  The trigger condition is most likely re-firing on every boot.")
    print("  BootDuringEnable means the CANcoder saw the host heartbeat, which")
    print("  reads as enabled, at the moment it powered up. That is the literal")
    print("  definition of the fault. Two ways out:")
    print("    - Power-cycle the CANcoder with no script running, so the")
    print("      heartbeat stays silent while it boots.")
    print("    - Or leave it. It is informational and changes neither the")
    print("      encoder readings nor motor control.")
    return "persists"


def _device_ids(devices):
    """{label: int id} for a devices group, and the entries that cannot be used."""
    ids = {}
    bad = []
    for label, raw in devices.items():
        try:
            device_id = int(str(raw).strip(), 10)
        except (TypeError, ValueError):
            bad.append(f"{label}: {raw!r} is not a device id")
            continue
        if not 0 <= device_id <= MAX_DEVICE_ID:
            bad.append(f"{label}: id {device_id} is outside the CTRE range "
                       f"0-{MAX_DEVICE_ID}")
            continue
        ids[label] = device_id
    return ids, bad


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="cancoder_clear_faults", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]).strip())
    p.add_argument("--corner", default="all",
                   help="one label from devices.cancoder, or 'all' (default)")
    p.add_argument("--bus", help="override cancoder.bus")
    p.add_argument("--max-attempts", type=int, default=3,
                   help="generic clear_sticky_faults() retries (default 3)")
    p.add_argument("--settle-ms", type=int, default=500,
                   help="ms between a clear and the read-back (default 500)")
    p.add_argument("--dry-run", action="store_true",
                   help="read and decode the sticky bits, clear nothing")
    args = p.parse_args(argv)

    if args.max_attempts < 1:
        print(f"REFUSED: --max-attempts {args.max_attempts} clears nothing and "
              "still reports every fault as surviving.\n"
              "  FIX: pass 1 or more, or pass --dry-run to read the bits without "
              "clearing them.")
        return 2
    if args.settle_ms < 0:
        print(f"REFUSED: --settle-ms {args.settle_ms} is negative, and the wait "
              "between a clear and its read-back cannot run backwards.\n"
              "  FIX: pass 0 or more.")
        return 2

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    devices = vars(group) if group is not None else {}
    if not devices:
        print("REFUSED: this config names no CANcoders, so there are no sticky "
              "faults to clear.\n"
              "  FIX: add a `devices.cancoder` group and a `cancoder:` block to "
              "your config. sparklib/data/spark.yaml carries a worked example "
              "of both.")
        return 2

    bus = args.bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not bus:
        print("REFUSED: no CANcoder bus named.\n"
              "  FIX: set `cancoder.bus` in the config, or pass --bus. It is the "
              "netdev the encoders are on, which is usually a different adapter "
              "from the SPARK bus.")
        return 2

    if args.corner != "all":
        if args.corner not in devices:
            print(f"REFUSED: {args.corner!r} is not in devices.cancoder "
                  f"({', '.join(devices)}).\n"
                  "  FIX: name one of those labels, or pass --corner all.")
            return 2
        devices = {args.corner: devices[args.corner]}

    ids, bad = _device_ids(devices)
    if bad:
        print("REFUSED: devices.cancoder carries entries this tool cannot "
              "address:")
        for line in bad:
            print(f"    {line}")
        print(f"  FIX: give every label in devices.cancoder a whole number "
              f"between 0 and {MAX_DEVICE_ID}, which is the CTRE device id "
              "range. sparklib/data/spark.yaml carries a worked example.")
        return 2

    order = list(ids)
    print(f"\nbus {bus} -- {len(order)} CANcoder(s): {', '.join(order)}")
    if args.dry_run:
        print("dry run: reading and decoding only, nothing is cleared")
    else:
        print(f"Each corner is read first, then clear_sticky_faults() runs up to "
              f"{args.max_attempts} time(s) with {args.settle_ms} ms between a "
              "clear and its read-back.")
        print("Clearing destroys the only record a brownout or a reboot leaves "
              "behind, so keep the output below.")

    results = {}
    interrupted = False
    for label in order:
        try:
            results[label] = clear_one(label, ids[label], bus,
                                       args.max_attempts,
                                       args.settle_ms / 1000.0, args.dry_run)
        except KeyboardInterrupt:
            print(f"\n  [{label}] interrupted before it finished.")
            results[label] = "interrupted"
            interrupted = True
            break
        except Exception as err:        # noqa: BLE001 - one bad corner, not the run
            print(f"\n  [{label}] FAILED: {err}")
            results[label] = "failed"

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for label in order:
        verdict = results.get(label, "skipped")
        print(f"  {label}: {VERDICTS[verdict]}")

    if interrupted:
        unfinished = [label for label in order
                      if results.get(label, "skipped") in UNFINISHED_VERDICTS]
        print(f"\nInterrupted. These corners were left unfinished or never "
              f"reached: {', '.join(unfinished)}.")

    print("\nRe-read without clearing anything:")
    print("  uv run python tools/cancoder_clear_faults.py --dry-run")
    print("Verify the corners afterwards:")
    print("  uv run python tools/cancoder_config_check.py --corner all")
    print("Magnet health and sensor direction: tools/cancoder_audit.py")

    clean = [label for label in order
             if results.get(label) in CLEAN_VERDICTS]
    return 0 if len(clean) == len(order) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted before the run finished")
        sys.exit(1)
