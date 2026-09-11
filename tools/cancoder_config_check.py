"""Per-corner CANcoder flash state, sticky faults, firmware and reading stability.

    uv run python tools/cancoder_config_check.py --corner all
    uv run python tools/cancoder_config_check.py --corner RF
    uv run python tools/cancoder_config_check.py --expected-offset 0.0

This is the first diagnostic of a swerve bring-up. Before a single wheel is
aligned, every encoder has to answer on the bus, hold a steady reading, and carry
the flash state you expect. It reads and never applies a device configuration, so
running it leaves every calibration exactly as it found it. It exits with a
status code, so a longer procedure can gate on it.

Per corner it reports the absolute reading and its jitter, the wheel angle that
reading gives through the recorded offset, the magnet offset persisted in flash
against the one you expect, sensor direction, magnet health, the sticky fault
field decoded bit by bit, and the firmware version. tools/cancoder_audit.py walks
the same bus comparing the corners against each other, so a direction
disagreement between modules is its verdict.

A CANcoder that never answers is the reading this tool exists to catch. phoenix6
answers a silent id with a default value and a bad status code instead of an
exception, so 0.000 deg with no jitter looks like a healthy encoder parked at
zero. Every number below is taken only when the status code of its own refresh
says a frame arrived, and anything else prints as unavailable with the code named.

Calibration belongs in the config file, as cancoder.offsets_deg, while the device
side stays at zero, which is why the expected flash offset defaults to 0.0. A
non-zero value means something applied a CANcoderConfiguration to the encoder. A
default-constructed one carries magnet_offset 0.0, so by the time you read a
surprise here the previous device-side number is already gone.
docs/TROUBLESHOOTING.md carries the recovery.

Two sticky bits are reported and forgiven. 0x00000008, UnlicensedFeatureInUse,
latches when Phoenix probes a licensed feature. 0x00080000, a BootDuringEnable
variant on rig-flex CANcoder firmware 436273408, latches whenever an encoder
boots while the host heartbeat is already alive, and clearing it would need a
boot order no robot actually uses. Both leave the absolute reading alone.
Everything else, BadMagnet and Undervoltage included, fails the corner loudly.

Jitter above 0.3 deg across 20 samples at 50 Hz means the wheel is turning under
you, or the magnet or the bus is suspect. An offset measured through that noise
is the one that comes back different on the next run.

Exit codes: 0 when every corner reads cleanly, 1 when a corner is flagged, 2 when
the run is refused before any device is opened, and 130 when Ctrl-C leaves
corners unread.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import sys
import time

from sparklib import cancoder, steer
from sparklib import config as cfg

WARMUP_SAMPLES = 10
STATIONARY_SAMPLES = 20
SAMPLE_DT = 0.02
JITTER_BUDGET_DEG = 0.3
OFFSET_TOL_DEG = 0.01

REFUSED = 2
INTERRUPTED = 130

CANCODER_ID_MIN, CANCODER_ID_MAX = 0, 62

DEVICES_EXAMPLE = "cancoder: {LF: 1, RF: 3, LB: 2, RB: 4}"
OFFSETS_EXAMPLE = "offsets_deg: {LF: 78.07, RF: -94.40, LB: 109.72, RB: -98.26}"

INFORMATIONAL_STICKY_MASK = 0x00080008

STICKY_BITS = {
    1 << 0: "Hardware",
    1 << 1: "Undervoltage",
    1 << 2: "BootDuringEnable",
    1 << 3: "UnlicensedFeatureInUse",
    1 << 16: "BadMagnet",
    1 << 17: "BadMagnetField",
    1 << 18: "RemoteSensorReset",
    1 << 19: "BootDuringEnable_v2",
}


class SilentDevice(Exception):
    """The absolute-position signal carried no frame from this id."""


def decode_sticky(field):
    """Names of the bits set in a sticky fault field."""
    if field == 0:
        return "(none)"
    names = [name for mask, name in STICKY_BITS.items() if field & mask]
    known = 0
    for mask in STICKY_BITS:
        known |= mask
    unknown = field & ~known
    if unknown:
        names.append(f"unknown=0x{unknown:08X}")
    return ", ".join(names)


def status_name(sig):
    """The status code from a signal's last refresh, named."""
    status = getattr(sig, "status", None)
    return getattr(status, "name", str(status))


def answered(sig):
    """Whether a signal's last refresh carried a frame, None when unknowable.

    phoenix6 answers a device that is absent or silent with a default value and a
    bad status code, so the value on its own says nothing about whether anything
    replied. A version that reports no status at all leaves this undecidable, and
    the caller then falls back on the value.
    """
    is_ok = getattr(getattr(sig, "status", None), "is_ok", None)
    if not callable(is_ok):
        return None
    return bool(is_ok())


def signal_value(encoder, *names):
    """Value of the first of these status signals that answers, and why none did.

    Returns (value, None) when a reading arrived, or (None, reason) when this
    phoenix6 version exposes none of the names, a call raised, or every signal
    came back with a status code saying no frame.
    """
    reason = "not reported by this firmware"
    for name in names:
        getter = getattr(encoder, name, None)
        if getter is None:
            continue
        try:
            sig = getter()
            value = sig.value
        except Exception as err:        # noqa: BLE001 - optional signal
            reason = f"read failed: {err}"
            continue
        if answered(sig) is False:
            reason = f"no frame from the device, status {status_name(sig)}"
            continue
        return value, None
    return None, reason


def sample_angle(encoder, samples, sample_dt):
    """Mean absolute angle and its peak-to-peak spread, in degrees.

    Every sample is measured as a wrapped difference from the first one, so a
    corner parked on the encoder's own discontinuity reports the jitter it has
    instead of a full turn of it. Raises SilentDevice when the position signal
    carries no frame, either before the samples start or by the time they end.
    """
    if samples < 2:
        raise ValueError("a peak-to-peak spread needs at least two samples")

    sig = encoder.get_absolute_position()
    for _ in range(WARMUP_SAMPLES):
        cancoder.read_angle_deg(encoder, settle=False)
        time.sleep(sample_dt)
    if answered(sig) is False:
        raise SilentDevice(f"nothing answered at this id, status "
                           f"{status_name(sig)}")

    anchor = cancoder.read_angle_deg(encoder, settle=False)
    deltas = [0.0]
    for _ in range(samples - 1):
        time.sleep(sample_dt)
        deltas.append(steer.normalize_deg(
            cancoder.read_angle_deg(encoder, settle=False) - anchor))
    if answered(sig) is False:
        raise SilentDevice(f"the encoder stopped answering partway through, "
                           f"status {status_name(sig)}")

    mean = steer.normalize_deg(anchor + sum(deltas) / len(deltas))
    return mean, max(deltas) - min(deltas)


def check_corner(label, device_id, bus, recorded_offset_deg, expected_offset_deg,
                 jitter_budget_deg, samples, sample_dt=SAMPLE_DT):
    """Read one corner and say whether it is fit to calibrate against."""
    print(f"\n=== {label} (CANcoder id {device_id}, bus '{bus}') ===")
    try:
        encoder = cancoder.open_encoder(device_id, bus)
    except Exception as err:            # noqa: BLE001 - report, keep going
        print(f"  unreachable:        {err}")
        return False

    try:
        mean_deg, jitter_deg = sample_angle(encoder, samples, sample_dt)
    except SilentDevice as err:
        print(f"  absolute_position:  <{err}>")
        print("    --> no frame from this id. Check the CAN id, the bus name and "
              "the wiring before looking at software.")
        return False
    except Exception as err:            # noqa: BLE001 - report, keep going
        print(f"  absolute_position:  <unavailable: {err}>")
        print("    --> the position read itself failed, so this corner is "
              "unmeasured. Nothing below it would be worth reading.")
        return False

    ok = True
    print(f"  absolute_position:  {mean_deg:+8.3f} deg  "
          f"(jitter {jitter_deg:.3f} over {samples} samples)")
    if jitter_deg > jitter_budget_deg:
        ok = False
        print(f"    --> jitter is over the {jitter_budget_deg:.3f} deg budget. "
              "The wheel is turning, or the magnet or the bus is suspect.")

    if recorded_offset_deg is None:
        print("  recorded offset:    <none>    [config cancoder.offsets_deg]")
        print("  wheel_deg:          <unknown until this corner has an offset>  "
              "[tools/cancoder_calibrate.py]")
    else:
        print(f"  recorded offset:    {recorded_offset_deg:+8.3f} deg  "
              "[config cancoder.offsets_deg]")
        wheel_deg = cancoder.wheel_angle_deg(mean_deg, recorded_offset_deg)
        print(f"  wheel_deg:          {wheel_deg:+8.3f} deg  "
              "(reads near 0 with this wheel at its zero)")

    try:
        described = cancoder.describe(encoder)
    except Exception as err:            # noqa: BLE001 - report, keep going
        print(f"  device config:      <unavailable: {err}>")
        described = None
        ok = False

    if described is not None:
        flash = described.get("magnet_offset")
        if flash is None:
            print("  magnet_offset:      <not reported by this firmware>")
            ok = False
        else:
            flash_deg = flash * 360.0
            matched = abs(flash_deg - expected_offset_deg) < OFFSET_TOL_DEG
            print(f"  magnet_offset:      {flash_deg:+8.3f} deg  "
                  f"[persisted in flash, expected {expected_offset_deg:+.3f}]  "
                  f"[{'OK' if matched else 'MISMATCH'}]")
            if not matched:
                ok = False
                print("    --> something applied a device configuration and wrote "
                      "this offset to flash. Reset it with "
                      "tools/cancoder_reset_flash.py, then re-measure with "
                      "tools/cancoder_calibrate.py.")

        direction = described.get("sensor_direction", "unknown")
        print(f"  sensor_direction:   {direction}")

    health, why = signal_value(encoder, "get_magnet_health")
    if health is None:
        print(f"  magnet_health:      <unavailable: {why}>")
        ok = False
        print("    --> the magnet reading is the one line that says whether this "
              "corner can be trusted, so a corner without it stays unverified.")
    else:
        green = "GREEN" in str(health).upper()
        print(f"  magnet_health:      {health}  [{'OK' if green else 'CHECK'}]")
        if not green:
            ok = False
            print("    --> the magnet sits too far, too close or off centre. "
                  "Fix the mount before trusting a reading from this corner.")

    sticky, why = signal_value(encoder, "get_sticky_fault_field")
    if sticky is None:
        print(f"  sticky_faults:      <unavailable: {why}>")
    else:
        field = int(sticky)
        actionable = field & ~INFORMATIONAL_STICKY_MASK
        if field == 0:
            tag = "OK"
        elif actionable == 0:
            tag = "OK (sticky history only)"
        else:
            tag = "CHECK"
        print(f"  sticky_faults:      0x{field:08X}  [{tag}]  "
              f"{decode_sticky(field)}")
        if field and actionable == 0:
            print(f"    note: 0x{field:08X} sits inside the informational mask "
                  f"0x{INFORMATIONAL_STICKY_MASK:08X}, so it is reported and "
                  "forgiven.")
        if actionable:
            ok = False
            print(f"    --> 0x{actionable:08X} is a real fault. Power-cycle the "
                  "encoder, or clear it with tools/cancoder_clear_faults.py; one "
                  "that comes straight back is live.")

    firmware, why = signal_value(encoder, "get_version", "get_version_major")
    if firmware is None:
        print(f"  firmware:           <unavailable: {why}>")
    else:
        print(f"  firmware:           {firmware}")

    return ok


def can_id(value):
    """A config entry as a CANcoder id, or None when it is not one."""
    if isinstance(value, bool):
        return None
    try:
        ident = int(value)
    except (TypeError, ValueError):
        return None
    if CANCODER_ID_MIN <= ident <= CANCODER_ID_MAX:
        return ident
    return None


def degrees(value):
    """A config entry as a number of degrees, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Reads only. It applies no device configuration and commands no "
               "motor. tools/cancoder_audit.py walks the same bus comparing the "
               "corners against each other.")
    p.add_argument("--corner", default="all",
                   help="one label from devices.cancoder, or all (default: all)")
    p.add_argument("--bus", help="override cancoder.bus")
    p.add_argument("--expected-offset", type=float, default=0.0,
                   help="device-side magnet_offset every corner should carry, in "
                        "degrees (default 0.0, flagged when it differs)")
    p.add_argument("--jitter-budget", type=float, default=JITTER_BUDGET_DEG,
                   help=f"degrees a stationary reading may span, peak to peak "
                        f"(default {JITTER_BUDGET_DEG})")
    p.add_argument("--samples", type=int, default=STATIONARY_SAMPLES,
                   help=f"stationary samples per corner (default "
                        f"{STATIONARY_SAMPLES}, one every {SAMPLE_DT} s)")
    args = p.parse_args(argv)

    if args.samples < 2:
        print(f"REFUSED: --samples {args.samples} spans no interval, so the "
              "jitter check could never fail and the corner would pass "
              "unmeasured.\n"
              f"  FIX: pass 2 or more. The default is {STATIONARY_SAMPLES}, one "
              f"every {SAMPLE_DT} s.")
        return REFUSED

    if args.jitter_budget <= 0:
        print(f"REFUSED: --jitter-budget {args.jitter_budget} flags every "
              "corner, a perfectly steady one included.\n"
              f"  FIX: pass a positive number of degrees. The default is "
              f"{JITTER_BUDGET_DEG}.")
        return REFUSED

    if abs(args.expected_offset) > 360.0:
        print(f"REFUSED: --expected-offset {args.expected_offset} lies outside "
              "one turn, so no flash value can ever match it.\n"
              "  FIX: pass degrees between -360 and 360. A rig that keeps its "
              "calibration in the config expects 0.0 on the device.")
        return REFUSED

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return REFUSED

    cfg.load_host()
    conf = cfg.get()

    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    devices = vars(group) if group is not None else {}
    if not devices:
        print("REFUSED: this config names no CANcoders, so there is nothing to "
              "check.\n"
              "  FIX: add a `devices.cancoder` group and a `cancoder:` block to "
              "your config, as in `" + DEVICES_EXAMPLE + "`. "
              "sparklib/data/spark.yaml carries a worked example of both.")
        return REFUSED

    bad_ids = [f"{label}={value!r}" for label, value in devices.items()
               if can_id(value) is None]
    if bad_ids:
        print("REFUSED: devices.cancoder carries an entry that is not a CAN id "
              f"({', '.join(bad_ids)}).\n"
              f"  FIX: give every label a whole number from {CANCODER_ID_MIN} to "
              f"{CANCODER_ID_MAX}, as in `" + DEVICES_EXAMPLE + "`. A label left "
              "blank reads as no id at all.")
        return REFUSED
    devices = {label: can_id(value) for label, value in devices.items()}

    bus = args.bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not bus:
        print("REFUSED: no CANcoder bus named.\n"
              "  FIX: set `cancoder.bus` in your config, or pass --bus. It is the "
              "netdev the encoders are on, which is usually a different adapter "
              "from the SPARK bus.")
        return REFUSED

    offsets = {}
    offset_group = getattr(getattr(conf, "cancoder", None), "offsets_deg", None)
    if offset_group is not None:
        offsets = vars(offset_group)

    bad_offsets = [f"{label}={value!r}" for label, value in offsets.items()
                   if label in devices and degrees(value) is None]
    if bad_offsets:
        print("REFUSED: cancoder.offsets_deg carries an entry that is not a "
              f"number of degrees ({', '.join(bad_offsets)}).\n"
              "  FIX: give every listed label a measured value, as in `"
              + OFFSETS_EXAMPLE + "`, or drop the label and re-measure it with "
              "tools/cancoder_calibrate.py.")
        return REFUSED
    offsets = {label: degrees(value) for label, value in offsets.items()
               if degrees(value) is not None}

    if args.corner != "all":
        if args.corner not in devices:
            print(f"REFUSED: {args.corner!r} is not in devices.cancoder "
                  f"({', '.join(devices)}).\n"
                  "  FIX: name one of those labels, or pass --corner all.")
            return REFUSED
        devices = {args.corner: devices[args.corner]}

    results = {label: None for label in devices}
    interrupted = False
    for label in devices:
        try:
            results[label] = check_corner(label, devices[label], bus,
                                          offsets.get(label), args.expected_offset,
                                          args.jitter_budget, args.samples)
        except KeyboardInterrupt:
            interrupted = True
            print(f"\n  {label}: interrupted before this corner finished")
            break
        except Exception as err:        # noqa: BLE001 - one bad corner, not the run
            print(f"\n  {label}: FAILED: {err}")
            results[label] = False

    print()
    print("=" * 62)
    print(f"Summary: {len(results)} corner(s) on bus {bus}")
    print("=" * 62)
    for label, verdict in results.items():
        if verdict is None:
            state = "NOT READ (the run stopped first)"
        else:
            state = "OK" if verdict else "ISSUES (see above)"
        print(f"  {label}: {state}")

    if interrupted:
        unread = [label for label, verdict in results.items() if verdict is None]
        print(f"\nInterrupted, so this run says nothing about {', '.join(unread)}. "
              "Run it again before treating the bus as checked.")
        return INTERRUPTED

    if all(results.values()):
        print("\nEvery corner reads cleanly. Alignment and calibration can go "
              "ahead.")
        return 0
    print("\nAt least one corner needs attention. docs/TROUBLESHOOTING.md carries "
          "the recovery for each line flagged above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
