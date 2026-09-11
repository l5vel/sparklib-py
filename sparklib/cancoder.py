"""The absolute-encoder half of a swerve module, for a CTRE CANcoder.

A SPARK counts motor revolutions from wherever it powered on, so on a steer axis
it knows how far the axis turned and not which way the wheel points. Commanding a
real wheel angle needs that reference supplied once, from an absolute sensor on
the steer shaft. On most modules that is a CTRE CANcoder, usually on a second bus
in FD mode.

The reference arrives through a SPARK operation, `set_encoder_position`, which is
why this lives here rather than in whatever library owns the sensor. Everything
else sparklib does works without any of it.

    seed_from_absolute(motor, encoder, offset_deg=78.07, gear_ratio=26.0)

is the whole integration: read the absolute angle once, subtract the recorded
wheel-zero offset, convert to motor rotations, and write it into the SPARK. From
then on the steer loop runs on the controller's own encoder at the controller's
own rate. docs/STEER-CONTROL.md covers why it works that way.

This module needs phoenix6, which the core library does not:

    uv add "sparklib-py[swerve]"

Import it and you get a clear message if the extra is missing, rather than a
ModuleNotFoundError from four frames down.
"""

import time
from collections import deque

EXTRA_HINT = (
    "sparklib.cancoder needs phoenix6, which is not installed. It ships in the "
    "swerve extra because a bench rig driving one SPARK has no use for it.\n"
    "    inside this checkout:   uv sync --extra swerve\n"
    "    from your own project:  uv add \"sparklib-py[swerve]\"\n"
    "If you just ran that and are still reading this, a bare `uv run` re-synced "
    "the environment to the default extras and took phoenix6 back out. Export "
    "UV_NO_SYNC=1 and sync by hand, or pass the extra through every time: "
    "`uv run --extra swerve <command>`.\n"
    "Everything else in sparklib works without it."
)


def _phoenix():
    """Import phoenix6 against real hardware, or say which extra supplies it.

    CTR_TARGET is set first and deliberately. phoenix6 26.x tries its simulation
    libraries before its hardware ones, and the sim .so ships in the wheel, so it
    always wins. The bus then stays in sim mode forever and every read returns a
    default with no error anywhere. Set before the first phoenix6 import or it
    has no effect.
    """
    import os
    os.environ.setdefault("CTR_TARGET", "Hardware")
    try:
        import phoenix6
        return phoenix6
    except ImportError as err:
        raise ImportError(EXTRA_HINT) from err


def available():
    """True when the swerve extra is installed."""
    try:
        _phoenix()
        return True
    except ImportError:
        return False


def bus_arg(bus_name):
    """A bus argument phoenix6 accepts across its 24.x to 26.x versions."""
    _phoenix()
    from phoenix6 import CANBus
    try:
        return CANBus(bus_name)
    except TypeError:
        return bus_name


def open_encoder(device_id, bus_name):
    """One CANcoder on the named bus."""
    _phoenix()
    from phoenix6.hardware import CANcoder
    return CANcoder(int(device_id), bus_arg(bus_name))


def open_module_encoders(devices, bus_name):
    """{label: CANcoder} for a `devices` group, so a whole set opens at once."""
    return {label: open_encoder(dev, bus_name) for label, dev in devices.items()}


def warmup(named_signals, timeout_s=2.0, settle_window_s=0.5,
           stable_tol_deg=0.3, sample_dt=0.05, verbose=True, strict=True):
    """Block until every signal reads a value that has held still.

    The first `get_absolute_position().value` after a process starts can return a
    stale or zero-initialised cache entry, before the bus has delivered a fresh
    frame. Seeding a steer loop from that value gives the robot a different
    starting offset on every restart, and the symptom looks like a mechanical
    fault rather than a timing one. This waits for fresh, stationary data.

    named_signals: [(label, StatusSignal)], pre-cached.
    Returns {label: mean_degrees}, and reports per-label jitter so a noisy
    encoder is visible rather than averaged away.

    strict decides what an axis that never settles costs you. True raises, which
    is right when the number becomes a calibration offset. False returns the
    mean it measured and marks the label in the printed jitter, which suits a
    diagnostic run that should report the unsteady axis and carry on.
    """
    _phoenix()
    from phoenix6 import BaseStatusSignal

    n = max(1, int(settle_window_s / sample_dt) + 1)
    history = {label: deque(maxlen=n) for label, _ in named_signals}
    deadline = time.time() + timeout_s
    settled = {}

    while time.time() < deadline and len(settled) < len(named_signals):
        BaseStatusSignal.refresh_all(*[sig for _, sig in named_signals])
        for label, sig in named_signals:
            if label in settled:
                continue
            history[label].append(sig.value * 360.0)
            h = history[label]
            if len(h) == h.maxlen and (max(h) - min(h)) < stable_tol_deg:
                settled[label] = sum(h) / len(h)
        time.sleep(sample_dt)

    for label, sig in named_signals:
        h = history[label]
        if label not in settled:
            spread = (max(h) - min(h)) if h else float("nan")
            message = (
                f"CANcoder {label} never settled within {timeout_s:.1f} s "
                f"(spread {spread:.2f} deg, tolerance {stable_tol_deg:.2f}). "
                "Either the axis is moving, or the encoder is not reporting. "
                "Check the magnet and the bus before trusting any offset.")
            if strict:
                raise TimeoutError(message)
            if not h:
                if verbose:
                    print(f"  {label}: no samples")
                continue
            settled[label] = sum(h) / len(h)
            if verbose:
                print(f"  {label}: {settled[label]:+8.2f} deg  "
                      f"(jitter {spread:.2f}, NEVER SETTLED)")
            continue
        if verbose:
            print(f"  {label}: {settled[label]:+8.2f} deg  "
                  f"(jitter {max(h) - min(h):.2f})")
    return settled


def read_angle_deg(encoder, settle=True, **kw):
    """Absolute angle in degrees, waiting for a fresh stationary reading."""
    sig = encoder.get_absolute_position()
    if not settle:
        from phoenix6 import BaseStatusSignal
        BaseStatusSignal.refresh_all(sig)
        return sig.value * 360.0
    return warmup([("encoder", sig)], verbose=False, **kw)["encoder"]


def wheel_angle_deg(raw_deg, offset_deg):
    """Wheel angle from a raw reading and this corner's recorded zero offset."""
    a = (raw_deg - offset_deg) % 360.0
    return a - 360.0 if a > 180.0 else a


def motor_rotations(angle_deg, gear_ratio):
    """Steer motor rotations for a wheel angle. This is where a wrong ratio bites."""
    return (angle_deg / 360.0) * float(gear_ratio)


def seed_from_absolute(motor, encoder, offset_deg, gear_ratio, settle=True):
    """Teach a SPARK where its steer axis actually is, once, at startup.

    Reads the absolute angle, converts through the recorded offset and the steer
    reduction, and writes it into the controller's encoder. After this the SPARK
    can run its own position loop, or you can close one on the host, and either
    is finally talking about the wheel rather than about an arbitrary zero.

    Returns the wheel angle it seeded, in degrees.
    """
    raw = read_angle_deg(encoder, settle=settle)
    angle = wheel_angle_deg(raw, offset_deg)
    motor.set_encoder_position(motor_rotations(angle, gear_ratio))
    return angle


def measure_offset_deg(encoder, settle=True, **kw):
    """The offset to record for a corner you have physically aligned to zero.

    Point the wheel at your chosen zero, call this, and write the number into
    the config. Measuring against a wheel you have not aligned bakes the
    misalignment in, so do step 2 of docs/SWERVE-CALIBRATION.md first.
    """
    return read_angle_deg(encoder, settle=settle, **kw)


def describe(encoder):
    """Configuration and health of one CANcoder, read-only.

    Reports the fields that silently break a swerve module: which direction the
    sensor counts, where its own zero sits, and whether the magnet is close
    enough to be trusted.
    """
    _phoenix()
    from phoenix6.configs.cancoder_configs import CANcoderConfiguration
    cfg = CANcoderConfiguration()
    encoder.configurator.refresh(cfg)
    ms = cfg.magnet_sensor
    out = {
        "sensor_direction": str(getattr(ms, "sensor_direction", "unknown")),
        "magnet_offset": getattr(ms, "magnet_offset", None),
        "absolute_range": str(getattr(ms, "absolute_sensor_discontinuity_point",
                                      getattr(ms, "absolute_sensor_range", "unknown"))),
    }
    for name in ("get_magnet_health", "get_version_major"):
        getter = getattr(encoder, name, None)
        if getter is not None:
            try:
                out[name.replace("get_", "")] = str(getter().value)
            except Exception:           # noqa: BLE001 - optional signal
                pass
    return out


class Refused(Exception):
    """A tool cannot run because the config does not describe any encoders."""


def modules(corner=None, bus=None, conf=None):
    """({label: device_id}, bus_name) for the corners a tool should act on.

    Every CANcoder tool needs the same three answers: which corners exist, what
    their ids are, and which bus carries them. Resolving that in one place keeps
    the refusal wording identical across tools, and keeps corner labels coming
    from the config, so a three-module rig or one that calls its corners
    something else works without editing any tool.

    Raises Refused with a message that names the fix.
    """
    from . import config as cfg

    if conf is None:
        cfg.load_host()
        conf = cfg.get()

    where = cfg.config_path()

    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    if group is None:
        raise Refused(
            f"{where}\n"
            "  has no `devices.cancoder` group, so there are no absolute "
            "encoders to read.\n"
            "  FIX: add the group and a `cancoder:` block to THAT file. "
            "sparklib/data/spark.yaml carries a worked example of both.")

    devices = vars(group)
    resolved = bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not resolved:
        raise Refused(
            f"{where}\n"
            "  names no CANcoder bus. Set `cancoder.bus` in THAT file, or pass "
            "--bus.\n"
            "  The block sits at the top level, beside `devices:`, and YAML "
            "nesting is easy to get wrong here: if `cancoder:` ends up indented "
            "under `devices:` it reads as a device group and not as this block.\n"
            "  Reading the wrong file entirely is the other cause. The order is "
            "$SPARKLIB_CONFIG, then ./spark.yaml relative to the directory you "
            "are in, then the shipped example.")

    if corner:
        if corner not in devices:
            raise Refused(
                f"{corner!r} is not in devices.cancoder "
                f"({', '.join(sorted(devices))}).")
        devices = {corner: devices[corner]}

    return devices, resolved


def offsets(conf=None):
    """{label: offset_deg} recorded for each corner, empty when none are set."""
    from . import config as cfg

    if conf is None:
        cfg.load_host()
        conf = cfg.get()
    group = getattr(getattr(conf, "cancoder", None), "offsets_deg", None)
    if group is None:
        return {}
    return {k: float(v) for k, v in vars(group).items()}


def present(encoder, timeout_s=2.0, poll_s=0.1):
    """True when this id is actually answering on the bus.

    Read the status code, never the value. An id with nothing behind it returns
    absolute_position 0.000 with no exception and no error in the value itself,
    so a tool that judges presence by the number reports a wheel parked at zero
    with beautifully low jitter. Every downstream check then either passes on a
    default or fails on the one field that has no default, which is how an
    absent encoder gets reported as a magnet mounted wrong.

    Keep asking until the timeout. A bus that has just come up delivers nothing
    for a moment, so the first encoder opened answers RX_TIMEOUT while the ones
    opened after it are fine. A single read there reports a healthy encoder as
    absent, and which corner it picks depends on the order the config lists them.
    """
    sig = encoder.get_absolute_position()
    deadline = time.time() + timeout_s
    while True:
        try:
            sig.wait_for_update(poll_s)
        except Exception:           # noqa: BLE001 - older signal API
            from phoenix6 import BaseStatusSignal
            BaseStatusSignal.refresh_all(sig)
        status = getattr(sig, "status", None)
        ok = getattr(status, "is_ok", None)
        if not callable(ok):
            return status is None
        if ok():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_s)


# CANcoder sticky fault bits, from the Phoenix 6 CANcoder API docs.
STICKY_FAULTS = {
    0x00000001: "Hardware",
    0x00000002: "Undervoltage",
    0x00000004: "BootDuringEnable",
    0x00000008: "UnlicensedFeatureInUse",
    0x00000010: "BadMagnet",
}

# Bits that latch on a healthy rig and say nothing about the encoder's output.
# UnlicensedFeatureInUse latches whenever a Phoenix Pro signal is touched
# without a licence. BootDuringEnable latches on any power cycle taken while
# the host heartbeat was alive, which is every normal restart of a running rig.
INFORMATIONAL_STICKY = 0x00000008 | 0x00000004


def sticky_faults(encoder):
    """The raw sticky fault field, or None when this firmware omits it."""
    getter = getattr(encoder, "get_sticky_fault_field", None)
    if getter is None:
        return None
    try:
        return int(getter().value)
    except Exception:               # noqa: BLE001 - optional signal
        return None


def decode_sticky(field, table=None):
    """[names] for the bits set in a sticky fault field."""
    table = table or STICKY_FAULTS
    named = [name for bit, name in sorted(table.items()) if field & bit]
    unknown = field & ~sum(table)
    if unknown:
        named.append(f"unknown(0x{unknown:08X})")
    return named


def actionable_sticky(field):
    """The part of a sticky field that a person should act on."""
    return field & ~INFORMATIONAL_STICKY


def firmware(encoder):
    """Firmware version string, or None when this firmware omits the signal."""
    for name in ("get_version", "get_version_major"):
        getter = getattr(encoder, name, None)
        if getter is None:
            continue
        try:
            return str(getter().value)
        except Exception:           # noqa: BLE001 - optional signal
            continue
    return None


def health_report(label, encoder, samples=20, sample_dt=0.02, out=print):
    """Everything worth knowing about one CANcoder, printed at startup.

    Best-effort by design: each field is read on its own and an unavailable one
    prints as unavailable, so a firmware that omits a signal still gives you the
    rest. Run it before a session and the numbers that drifted are in the log
    next to the run that went wrong.

    The stationary sample assumes the wheel is not moving. Jitter above a degree
    means the magnet or the bus is worth a look before you trust any angle.
    """
    out(f"[{label} CANcoder health]")
    if not present(encoder):
        out("  no reply on this id. Check the CAN id and the wiring.")
        return

    for name, getter in (("magnet_health", "get_magnet_health"),
                         ("fault_field", "get_fault_field"),
                         ("sticky_faults", "get_sticky_fault_field")):
        fn = getattr(encoder, getter, None)
        if fn is None:
            out(f"  {name}: unavailable on this firmware")
            continue
        try:
            value = fn().value
        except Exception as err:          # noqa: BLE001 - optional signal
            out(f"  {name}: unavailable ({err})")
            continue
        if name.endswith("faults") or name.endswith("field"):
            bits = decode_sticky(int(value))
            out(f"  {name}: 0x{int(value):08X}"
                + (f"  {', '.join(bits)}" if bits else ""))
        else:
            out(f"  {name}: {value}")

    version = firmware(encoder)
    out(f"  firmware: {version}" if version else "  firmware: unavailable")

    try:
        described = describe(encoder)
        offset = described.get("magnet_offset")
        if offset is not None:
            out(f"  magnet_offset: {offset * 360:+8.3f} deg "
                f"({offset:+.6f} rev) [held in flash]")
        out(f"  sensor_direction: {described.get('sensor_direction', 'unknown')}")
    except Exception as err:              # noqa: BLE001 - optional config read
        out(f"  flash config: unavailable ({err})")

    try:
        readings = []
        for _ in range(max(2, samples)):
            readings.append(read_angle_deg(encoder, settle=False))
            time.sleep(sample_dt)
        jitter = max(readings) - min(readings)
        out(f"  stationary: mean {sum(readings) / len(readings):+.3f} deg  "
            f"jitter {jitter:.3f} deg  (n={len(readings)})")
        if jitter > 1.0:
            out("  jitter above 1.0 deg: check the magnet gap and the bus "
                "before trusting this angle")
    except Exception as err:              # noqa: BLE001 - bus can go quiet
        out(f"  stationary sample: unavailable ({err})")
