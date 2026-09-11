"""Reset a CANcoder's stored magnet_offset and clear its sticky faults.

    uv run python tools/cancoder_reset_flash.py --corner RF
    uv run python tools/cancoder_reset_flash.py --corner all    # confirms each one
    uv run python tools/cancoder_reset_flash.py --corner RF --yes

WRITES DEVICE FLASH. For each corner it reads the live configuration and the
sticky faults, prints exactly what would change, asks for a typed y, writes
magnet_offset, calls clear_sticky_faults(), then re-reads the device and checks
that the write took.

This is the cleanup tool for what tools/cancoder_config_check.py reports: a
magnet_offset nobody meant to set, or sticky faults that need clearing. Every
run costs a flash write, so reach for it when a device needs resetting and leave
it out of any routine.

The write sends back the whole configuration the device just reported, with
magnet_offset replaced, which is what keeps sensor_direction and the
discontinuity point intact. A corner whose configuration never arrived is left
alone: phoenix6 answers a missing device with a default-constructed
configuration and a bad StatusCode, so applying that default would wipe those
fields on a device that is only unreachable. Every read here is believed once
the status that arrived with it reads OK.

Leave the device flash at magnet_offset 0. The software offset in
cancoder.offsets_deg carries the wheel-frame conversion, and an offset stored in
the device shifts every reading underneath it. The recorded software offset then
describes a device that has moved out from under it. After resetting a corner,
re-measure it with tools/cancoder_calibrate.py and paste the new
cancoder.offsets_deg block into your config.

Corners are visited in the order devices.cancoder lists them, so --corner all
walks the rig the way the config is written.

Commands no motor and opens no SPARK bus; the CANcoder bus is the only one it
touches.

Exit codes: 0 when every selected corner ends at the target, 1 when a corner is
written without verifying, fails, is declined or is never reached, and 2 when
the tool refuses before it touches the bus.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import math
import sys
import time

from sparklib import cancoder
from sparklib import config as cfg

WARMUP_SAMPLES = 10
SAMPLE_DT = 0.02
CHANGE_TOL_DEG = 0.001
VERIFY_TOL_DEG = 0.01
WRITE_SETTLE_S = 0.2
MAX_DEVICE_ID = 62
MAX_OFFSET_DEG = 360.0

# One line per corner in the summary, keyed by what reset_one returned.
VERDICTS = {
    "written": "OK (written and verified)",
    "at-target": "already at target",
    "unread": "NOT WRITTEN (this id never reported its configuration)",
    "sticky-unread": "NOT WRITTEN (at target, sticky faults never reported)",
    "declined": "NOT WRITTEN (declined at the prompt)",
    "unverified": "WRITTEN, NOT VERIFIED (see above)",
    "failed": "FAILED (see above)",
    "interrupted": "INTERRUPTED part way (device state unknown)",
    "skipped": "NOT REACHED (run stopped first)",
}
DONE_VERDICTS = ("written", "at-target")
WROTE_VERDICTS = ("written", "unverified", "interrupted")


def _status_ok(status):
    """True when phoenix6 calls this StatusCode good, or cannot say."""
    is_ok = getattr(status, "is_ok", None)
    return bool(is_ok()) if callable(is_ok) else True


def _status_text(status):
    """A StatusCode as its name, falling back to whatever it prints as."""
    return str(getattr(status, "name", None) or status)


def _new_device_config():
    """An empty CANcoderConfiguration, phoenix6 reached as sparklib reaches it."""
    # sparklib exposes no flash-write helper, so this holds the only direct import.
    if not cancoder.available():
        raise ImportError(cancoder.EXTRA_HINT)
    from phoenix6.configs.cancoder_configs import CANcoderConfiguration
    return CANcoderConfiguration()


def _read_angle(encoder):
    """Absolute angle in degrees, whether it settled, and whether it reported."""
    if not cancoder.present(encoder):
        return float("nan"), False, False, "<no reply from this id>"
    try:
        return cancoder.read_angle_deg(encoder), True, True, ""
    except TimeoutError:
        pass
    except Exception as err:            # noqa: BLE001 - a failed read is a result
        return float("nan"), False, False, f"<unavailable: {err}>"
    abs_deg = float("nan")
    try:
        for _ in range(WARMUP_SAMPLES):
            abs_deg = cancoder.read_angle_deg(encoder, settle=False)
            time.sleep(SAMPLE_DT)
    except Exception as err:            # noqa: BLE001 - the fallback read too
        return float("nan"), False, False, f"<unavailable: {err}>"
    return abs_deg, False, True, ""


def _read_device_config(encoder):
    """The device's live CANcoderConfiguration, and whether the device sent it."""
    device_cfg = _new_device_config()
    status = encoder.configurator.refresh(device_cfg)
    if not _status_ok(status):
        return device_cfg, False, f"<no reply: {_status_text(status)}>"
    return device_cfg, True, ""


def _read_sticky(encoder):
    """The sticky fault field as an int, or None when no good read arrived."""
    getter = getattr(encoder, "get_sticky_fault_field", None)
    if getter is None:
        return None, "<not reported by this firmware>"
    try:
        signal = getter()
        field = int(signal.value)
    except Exception as err:            # noqa: BLE001 - a failed read is a result
        return None, f"<unavailable: {err}>"
    status = getattr(signal, "status", None)
    if status is not None and not _status_ok(status):
        return None, f"<no reply: {_status_text(status)}>"
    return field, ""


def read_state(encoder):
    """Absolute angle, stored magnet_offset and sticky faults, as one snapshot."""
    abs_deg, settled, angle_ok, angle_note = _read_angle(encoder)
    device_cfg, cfg_ok, cfg_note = _read_device_config(encoder)
    sticky, sticky_note = _read_sticky(encoder)
    return {
        "abs_deg": abs_deg,
        "settled": settled,
        "angle_ok": angle_ok,
        "angle_note": angle_note,
        "cfg": device_cfg,
        "cfg_ok": cfg_ok,
        "cfg_note": cfg_note,
        "magnet_offset_deg": (device_cfg.magnet_sensor.magnet_offset * 360.0
                              if cfg_ok else None),
        "sticky": sticky,
        "sticky_note": sticky_note,
    }


def print_state(name, state, label):
    """Print one snapshot under a BEFORE or AFTER heading."""
    print(f"  [{name}] {label}:")
    if state["angle_ok"]:
        note = ("" if state["settled"]
                else "   (never settled; reading may be stale)")
        print(f"    absolute_position = {state['abs_deg']:+8.3f} deg{note}")
    else:
        print(f"    absolute_position = {state['angle_note']}")
    if state["magnet_offset_deg"] is None:
        print(f"    magnet_offset     = {state['cfg_note']}")
    else:
        print(f"    magnet_offset     = {state['magnet_offset_deg']:+8.3f} deg")
    if state["sticky"] is None:
        print(f"    sticky_faults     = {state['sticky_note']}")
    else:
        named = cancoder.decode_sticky(state["sticky"])
        suffix = f"  ({', '.join(named)})" if named else ""
        print(f"    sticky_faults     = 0x{state['sticky']:08X}{suffix}")


def needs_change(state, target_offset_deg=0.0):
    """True when this device holds an offset or a sticky fault worth writing."""
    offset_deg = state["magnet_offset_deg"]
    sticky = state["sticky"]
    return ((offset_deg is not None
             and abs(offset_deg - target_offset_deg) > CHANGE_TOL_DEG)
            or (sticky is not None and sticky != 0))


def reset_one(name, device_id, bus, skip_prompt=False, target_offset_deg=0.0):
    """Read, plan, confirm, write and verify one corner. Returns a VERDICTS key."""
    print(f"\n=== {name} (CANcoder id {device_id}, bus '{bus}') ===")
    encoder = cancoder.open_encoder(device_id, bus)

    before = read_state(encoder)
    print_state(name, before, "BEFORE")

    if not before["cfg_ok"]:
        if before["angle_ok"]:
            print(f"  [{name}] NOT WRITTEN: this device answers on the bus and "
                  "never reported its configuration, so there is nothing to "
                  "send back to it.")
        else:
            print(f"  [{name}] NOT WRITTEN: CANcoder id {device_id} is silent on "
                  f"bus '{bus}'.")
        print("    phoenix6 hands back a default CANcoderConfiguration with a "
              "bad StatusCode when nothing answers, and writing that default "
              "would take sensor_direction and the discontinuity point with it.")
        print(f"    FIX: check power, the CAN id and the adapter for id "
              f"{device_id} on bus '{bus}', then re-read with "
              "tools/cancoder_config_check.py.")
        return "unread"

    if not before["angle_ok"]:
        print(f"  [{name}] NOTE: the flash state above came off the device "
              "while absolute_position stayed silent, so the reset below runs "
              "on a live configuration and a stale angle. "
              "tools/cancoder_config_check.py covers the magnet and the bus.")

    if not needs_change(before, target_offset_deg):
        if before["sticky"] is None:
            print(f"  [{name}] magnet_offset is already "
                  f"{target_offset_deg:+.3f} deg. The sticky fault field never "
                  "reported, so nothing here was cleared and nothing was "
                  "written.")
            return "sticky-unread"
        print(f"  [{name}] already at magnet_offset={target_offset_deg:.3f} "
              f"and sticky=0x0. Nothing to do.")
        return "at-target"

    clear_sticky = before["sticky"] is not None and before["sticky"] != 0
    print(f"\n  [{name}] PLAN:")
    if abs(before["magnet_offset_deg"] - target_offset_deg) > CHANGE_TOL_DEG:
        print(f"    magnet_offset {before['magnet_offset_deg']:+.3f} -> "
              f"{target_offset_deg:+.3f} deg  (flash write; the current value is "
              "gone once it lands)")
    if clear_sticky:
        print(f"    clear_sticky_faults() (current: 0x{before['sticky']:08X})")

    if not skip_prompt:
        print()
        try:
            answer = input(f"  Write flash on {name}, CANcoder id {device_id}, "
                           f"bus '{bus}'? [y/N] ").strip().lower()
        except EOFError:
            print(f"  [{name}] nothing on stdin to answer with, so nothing was "
                  "written. Pass --yes to run unattended.")
            return "declined"
        if answer != "y":
            print(f"  [{name}] aborted (no changes written).")
            return "declined"

    device_cfg = before["cfg"]
    device_cfg.magnet_sensor.magnet_offset = target_offset_deg / 360.0
    print(f"  [{name}] writing new magnet_offset to flash ...")
    status = encoder.configurator.apply(device_cfg)
    if not _status_ok(status):
        print(f"  [{name}] ERROR: configurator.apply returned "
              f"{_status_text(status)}")
        return "failed"

    if clear_sticky:
        print(f"  [{name}] calling clear_sticky_faults() ...")
        try:
            status = encoder.clear_sticky_faults()
            if not _status_ok(status):
                print(f"  [{name}] WARNING: clear_sticky_faults returned "
                      f"{_status_text(status)}")
        except Exception as err:        # noqa: BLE001 - optional on old firmware
            print(f"  [{name}] WARNING: clear_sticky_faults raised: {err}")

    time.sleep(WRITE_SETTLE_S)
    after = read_state(encoder)
    print_state(name, after, "AFTER")

    if not after["cfg_ok"]:
        print(f"  [{name}] WARNING: the read-back never reported, so where the "
              "device landed is unknown. Run this tool again to find out.")
        return "unverified"
    if abs(after["magnet_offset_deg"] - target_offset_deg) > VERIFY_TOL_DEG:
        print(f"  [{name}] WARNING: magnet_offset reads "
              f"{after['magnet_offset_deg']:+.3f} deg after a write of "
              f"{target_offset_deg:+.3f} deg.")
        return "unverified"
    if after["sticky"] is None:
        print(f"  [{name}] magnet_offset verified. The sticky fault field never "
              "reported, so its state is unknown.")
        return "unverified"
    if after["sticky"] != 0:
        print(f"  [{name}] magnet_offset verified. Sticky faults survive as "
              f"0x{after['sticky']:08X}; tools/cancoder_clear_faults.py retries "
              "the clear and decodes what is left.")
        return "unverified"
    print(f"  [{name}] verified.")
    return "written"


def _device_ids(devices):
    """{label: int id} for a devices group, and the entries that cannot be used."""
    ids = {}
    bad = []
    for label, raw in devices.items():
        if isinstance(raw, bool) or (isinstance(raw, float)
                                     and not raw.is_integer()):
            bad.append(f"{label}: {raw!r} is not a device id")
            continue
        try:
            device_id = int(raw)
        except (TypeError, ValueError):
            shown = "no value at all" if raw is None else repr(raw)
            bad.append(f"{label}: carries {shown}, which is not a device id")
            continue
        if not 0 <= device_id <= MAX_DEVICE_ID:
            bad.append(f"{label}: id {device_id} is outside the CTRE range "
                       f"0-{MAX_DEVICE_ID}")
            continue
        ids[label] = device_id
    return ids, bad


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="cancoder_reset_flash", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]).strip("\n"))
    p.add_argument("--corner", required=True,
                   help="one label from devices.cancoder, or 'all'")
    p.add_argument("--yes", action="store_true",
                   help="skip the per-corner confirmation prompt")
    p.add_argument("--target-offset", type=float, default=0.0,
                   help=f"magnet_offset to write, in degrees (default 0.0, range "
                        f"-{MAX_OFFSET_DEG:.0f} to {MAX_OFFSET_DEG:.0f}). Almost "
                        "always leave at 0; the software offset in "
                        "cancoder.offsets_deg carries the wheel-frame conversion.")
    p.add_argument("--bus", help="override cancoder.bus")
    args = p.parse_args(argv)

    if not math.isfinite(args.target_offset):
        print(f"REFUSED: --target-offset {args.target_offset} is not a number a "
              "device can store.\n"
              f"  FIX: pass a value between -{MAX_OFFSET_DEG:.0f} and "
              f"{MAX_OFFSET_DEG:.0f} degrees, or leave it at 0.0.")
        return 2
    if abs(args.target_offset) > MAX_OFFSET_DEG:
        print(f"REFUSED: --target-offset {args.target_offset:+.3f} deg is more "
              "than a CANcoder holds. magnet_offset stores -1 to 1 rotations, "
              f"so -{MAX_OFFSET_DEG:.0f} to {MAX_OFFSET_DEG:.0f} degrees.\n"
              "  FIX: pass a value inside that range, or leave --target-offset "
              "at 0.0.")
        return 2

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    devices = vars(group) if group is not None else {}
    if not devices:
        print("REFUSED: this config names no CANcoders, so there are no absolute "
              "encoders to reset.\n"
              "  FIX: add a `devices.cancoder` group and a `cancoder:` block "
              "with `bus`. sparklib/data/spark.yaml carries a worked example of "
              "both.")
        return 2

    bus = args.bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not bus:
        print("REFUSED: no CANcoder bus named, so there is nowhere to send the "
              "write.\n"
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
              "address, and one flash write to the wrong id is not undone by "
              "reading it back:")
        for line in bad:
            print(f"    {line}")
        print("  FIX: give every label in devices.cancoder a whole number "
              f"between 0 and {MAX_DEVICE_ID}, which is the CTRE device id "
              "range. sparklib/data/spark.yaml carries a worked example.")
        return 2

    order = list(ids)
    print()
    print("=" * 60)
    print("CANcoder flash reset -- WRITES TO FLASH")
    print("=" * 60)
    print(f"Bus:                  {bus}")
    print("Corners:              "
          + ", ".join(f"{name} (id {ids[name]})" for name in order))
    print(f"Target magnet_offset: {args.target_offset:+.3f} deg")
    print(f"Flash writes:         up to {len(order)}, one per corner listed "
          "above, each permanent")
    print("Each corner is read first, and only a device that differs from that "
          "target or holds a sticky fault is written.")
    if args.target_offset != 0.0:
        print("--target-offset is not 0, so every corner written below shifts "
              "its own readings by that much, underneath the software offset in "
              "cancoder.offsets_deg.")
    if args.yes:
        print("--yes given: every write below runs without a confirmation prompt.")
    print()

    results = {}
    for name in order:
        try:
            results[name] = reset_one(name, ids[name], bus,
                                      skip_prompt=args.yes,
                                      target_offset_deg=args.target_offset)
        except KeyboardInterrupt:
            print(f"\n  [{name}] interrupted before it finished.")
            results[name] = "interrupted"
            break
        except Exception as err:        # noqa: BLE001 - one bad device, keep going
            print(f"\n  [{name}] FAILED: {err}")
            results[name] = "failed"

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for name in order:
        verdict = results.get(name, "skipped")
        print(f"  {name}: {VERDICTS[verdict]}")

    written = [n for n in order if results.get(n) in WROTE_VERDICTS]
    if written:
        print()
        print("Next: re-measure every corner that was written --")
        for name in written:
            print(f"  uv run python tools/cancoder_calibrate.py --corner {name}")
        print("Then paste the printed offsets_deg block into your config, under "
              "cancoder.")

    undone = [n for n in order if results.get(n, "skipped") not in DONE_VERDICTS]
    if not undone:
        return 0
    print()
    print("Left undone: "
          + ", ".join(f"{n} ({results.get(n, 'skipped')})" for n in undone))
    return 1


if __name__ == "__main__":
    sys.exit(main())
