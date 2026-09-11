"""Census every distinct frame class on a CAN bus. The tool that finds a ghost.

    uv run python tools/can_frame_census.py can0
    uv run python tools/can_frame_census.py can0 --payloads
    uv run python tools/can_frame_census.py can0 --isotp --api 0x3E4,0x3E5

can_id_sweep answers "which device ids are live". When it reports an id that
nothing is configured at, this answers "what actually produced that frame". It
groups frames by (direction, error class, extended, device type, manufacturer,
api, device id) instead of collapsing everything onto a device number, so host
traffic, error frames and diagnostic transport are visible as themselves.

`dir` is the column that settles most questions. RX is a device on the wire, TX
is this host, delivered back by socketcan local loopback. A passive listener
sees both, so a frame count alone never proves a device exists.

--isotp reassembles ISO-TP (ISO 15765-2) transport and dumps the result as hex
plus ASCII. Phoenix's diagnostic server talks to CANcoders that way, and its
per-session channel numbers are what a plain id scan misreads as devices.
docs/TROUBLESHOOTING.md, "A CAN ID sweep shows devices that are not there",
carries the session traffic this tool was written to identify.

FD mode is detected from the netdev, because reading an FD bus in classic mode
drops every CANcoder frame in silence.

The role column is a convenience read out of spark.yaml, and the census itself
needs no config at all. A config that will not load costs the labels and
nothing else, so a bus named on the command line is still censused.

It only listens, and transmits nothing.
"""

import argparse
import collections
import math
import os
import sys
import time
from types import SimpleNamespace

import can

from sparklib import config as cfg
from sparklib import netdev

_OK = 0
_NOTHING_HEARD = 1
_REFUSED = 2
_ENDED_EARLY = 3
_INTERRUPTED = 130

# Helpers read from can_id_sweep, public spelling first.
_BORROWED = (("iface_state", ("iface_state", "_iface_state")),
             ("err_classes", ("err_classes", "_err_classes")),
             ("mfr_names", ("MFR_NAMES", "_MFR_NAMES")))


def _sweep_helpers():
    """Borrow iface_state, err_classes and MFR_NAMES from can_id_sweep."""
    try:
        import can_id_sweep
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import can_id_sweep
        except ImportError as err:
            raise ImportError(
                f"tools/can_id_sweep.py did not import ({err}), and this tool "
                "reads its FRC decode from there.\n"
                "  FIX: run from the repository root, as "
                "`uv run python tools/can_frame_census.py`, so that both the "
                "tool and its dependencies resolve.") from err
    out = {}
    for attr, names in _BORROWED:
        for name in names:
            value = getattr(can_id_sweep, name, None)
            if value is not None:
                out[attr] = value
                break
        else:
            raise AttributeError(
                f"tools/can_id_sweep.py exports none of {' or '.join(names)}, "
                "and this tool reads that decode from there.\n"
                f"  FIX: export {names[0]} from tools/can_id_sweep.py.")
    return SimpleNamespace(**out)


class Reassembler:
    """ISO-TP (ISO 15765-2) reassembly, keyed by whatever the caller passes."""

    def __init__(self):
        self.partial = {}
        self.done = {}

    def feed(self, key, b):
        if not b:
            return
        kind = b[0] & 0xF0
        if kind == 0x00:                                  # single frame
            self._finish(key, b[1:1 + (b[0] & 0x0F)])
        elif kind == 0x10:                                # first frame
            self.partial[key] = {"want": ((b[0] & 0x0F) << 8) | b[1],
                                 "buf": bytearray(b[2:])}
        elif kind == 0x20 and key in self.partial:        # consecutive frame
            st = self.partial[key]
            st["buf"] += b[1:]
            if len(st["buf"]) >= st["want"]:
                self._finish(key, bytes(st["buf"][:st["want"]]))
                del self.partial[key]

    def _finish(self, key, blob):
        self.done.setdefault(key, []).append(bytes(blob))


def classify(msg, err_classes):
    """(direction, error-class-or-blank) for one frame."""
    return ("RX" if msg.is_rx else "TX",
            err_classes(msg.arbitration_id) if msg.is_error_frame else "")


def key_of(msg, err_classes):
    """The census grouping key for one frame."""
    a = msg.arbitration_id
    d, err = classify(msg, err_classes)
    return (d, err, msg.is_extended_id,
            (a >> 24) & 0x1F, (a >> 16) & 0xFF, (a >> 6) & 0x3FF, a & 0x3F)


def hexdump(blob, indent="        "):
    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in blob)
    out = []
    for i in range(0, len(blob), 16):
        row = blob[i:i + 16]
        hx = " ".join(f"{c:02x}" for c in row)
        out.append(f"{indent}{i:04x}  {hx:<47}  {txt[i:i + 16]}")
    return out


def collect(channel, duration, helpers, apis=None):
    """Listen on `channel`; return the census, or None when it was refused.

    A Ctrl-C or a bus that dies mid-window keeps whatever was already heard and
    says so in the result, because a partial census still names the ghost.
    """
    exists, is_up, is_fd = helpers.iface_state(channel)
    if not exists:
        present = ", ".join(netdev.present_can_netdevs()) or "none"
        print(f"REFUSED: CAN netdev {channel!r} does not exist. "
              f"CAN netdevs present: {present}.\n"
              "  FIX: name one of those, or find it with "
              "`ip -d link show type can`.")
        return None
    if not is_up:
        print(f"REFUSED: [{channel}] is DOWN, so it carries nothing to census.")
        if is_fd:
            print(f"  FIX: sudo ip link set {channel} up")
        else:
            print(f"  FIX: sudo ip link set {channel} up type can "
                  "bitrate 1000000 txqueuelen 1000")
        return None

    try:
        bus = can.Bus(interface="socketcan", channel=channel, fd=is_fd)
    except Exception as err:            # noqa: BLE001 - surface any backend error
        print(f"REFUSED: [{channel}] could not open "
              f"({'FD' if is_fd else 'classic'}): {err}\n"
              f"  FIX: read the netdev back with `ip -d link show {channel}`. "
              "An FD bus wants a dbitrate, a classic one only a bitrate.")
        return None

    counts = collections.Counter()
    payloads = collections.defaultdict(list)
    tp = Reassembler()
    filtered = 0
    interrupted = False
    error = None
    t0 = time.time()
    try:
        print(f"  [{channel}] listening {duration:.1f}s "
              f"({'CAN-FD' if is_fd else 'classic CAN'})...")
        deadline = t0 + duration
        while time.time() < deadline:
            msg = bus.recv(timeout=max(0.0, deadline - time.time()))
            if msg is None:
                continue
            a = msg.arbitration_id
            api = (a >> 6) & 0x3FF
            if apis is not None and api not in apis:
                filtered += 1
                continue
            k = key_of(msg, helpers.err_classes)
            counts[k] += 1
            body = bytes(msg.data)
            if body not in payloads[k] and len(payloads[k]) < 6:
                payloads[k].append(body)
            if msg.is_rx and msg.is_extended_id and not msg.is_error_frame:
                tp.feed((api, a & 0x3F), body)
    except KeyboardInterrupt:
        interrupted = True
    except Exception as err:            # noqa: BLE001 - keep the partial census
        error = str(err) or err.__class__.__name__
    finally:
        bus.shutdown()
    return SimpleNamespace(counts=counts, payloads=payloads, tp=tp,
                           filtered=filtered, interrupted=interrupted,
                           error=error, elapsed=time.time() - t0)


def _load_config():
    """(config, why-it-did-not-load). It feeds the role column and nothing else."""
    try:
        cfg.load_host()
        return cfg.get(), None
    except Exception as err:            # noqa: BLE001 - the census runs without it
        return None, str(err) or err.__class__.__name__


def _config_channel(conf):
    """can.interface from the config, empty when the bus is hosted on a roboRIO."""
    ch = getattr(getattr(conf, "can", None), "interface", None)
    ch = "" if ch is None else str(ch).strip()
    return "" if ch.lower() in ("", "none", "rio") else ch


def _roles(conf, channel=None):
    """({device_id: 'group/LABEL'}, complaints) for the groups on one bus.

    Ids are unique per BUS. Labelling every group at once names a frame after
    whichever group shares its id on the other adapter.
    """
    roles = {}
    complaints = []
    devices = getattr(conf, "devices", None)
    if devices is None:
        return roles, complaints
    from sparklib import config as _cfg

    for group, members in vars(devices).items():
        if not hasattr(members, "__dict__"):
            complaints.append(f"devices.{group} is {members!r}, not a group of "
                              "labelled ids, so nothing under it labels a row")
            continue
        if channel is not None and _cfg.group_bus(group, conf) != channel:
            continue
        entries = vars(members)
        if not entries:
            complaints.append(f"devices.{group} holds no labels")
            continue
        for label, dev_id in entries.items():
            try:
                key = int(dev_id)
            except (TypeError, ValueError):
                complaints.append(f"devices.{group}.{label} = {dev_id!r} is "
                                  "not a device id")
                continue
            if not 0 <= key <= 63:
                complaints.append(f"devices.{group}.{label} = {key} is outside "
                                  "the 6-bit id space 0-63, so no frame can "
                                  "carry it")
                continue
            name = f"{group}/{label}"
            roles[key] = f"{roles[key]}+{name}" if key in roles else name
    return roles, complaints


def _frames(n):
    """'1 frame was' or 'N frames were', for a count in running prose."""
    return "1 frame was" if n == 1 else f"{n} frames were"


def _parse_apis(text):
    """The --api filter as a set of api ids, or None when it was not given."""
    if not text:
        return None
    apis = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            api = int(token, 0)
        except ValueError as err:
            raise ValueError(f"--api {token!r} is not an api id") from err
        if not 0 <= api <= 0x3FF:
            raise ValueError(f"--api {token} is outside the 10-bit api space "
                             "0x000-0x3FF, so no frame can carry it")
        apis.add(api)
    if not apis:
        raise ValueError("--api names no api id")
    return apis


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Passive: this transmits nothing. Exit 0 when frames were "
               "censused, 1 when the window ran and nothing was heard, 2 when "
               "the census was refused, 3 when the listen ended early, and "
               "130 on Ctrl-C.")
    p.add_argument("channel", nargs="?", default=None,
                   help="CAN netdev, e.g. can0 or slcan0 "
                        "(default: can.interface from the config)")
    p.add_argument("--duration", type=float, default=3.0,
                   help="seconds to listen (default 3.0)")
    p.add_argument("--api", default=None,
                   help="restrict to these api ids, e.g. 0x3E4,0x3E5")
    p.add_argument("--payloads", action="store_true",
                   help="show up to 6 distinct payloads per frame class")
    p.add_argument("--isotp", action="store_true",
                   help="reassemble ISO-TP replies and dump hex plus ASCII")
    args = p.parse_args(argv)

    try:
        helpers = _sweep_helpers()
    except (ImportError, AttributeError) as err:
        print(f"REFUSED: {err}")
        return _REFUSED

    if not args.duration > 0 or not math.isfinite(args.duration):
        print(f"REFUSED: --duration {args.duration} is not a listening window, "
              "and a census of no time at all reads like a silent bus.\n"
              "  FIX: pass a positive number of seconds, e.g. --duration 3.")
        return _REFUSED
    try:
        apis = _parse_apis(args.api)
    except ValueError as err:
        print(f"REFUSED: {err}.\n"
              "  FIX: pass comma-separated api ids, e.g. --api 0x3E4,0x3E5.")
        return _REFUSED

    conf, conf_err = _load_config()
    channel = args.channel or _config_channel(conf)
    if not channel:
        print("REFUSED: no CAN netdev to census.")
        if conf_err:
            print(f"  The config did not load: {conf_err}")
        print("  FIX: name the netdev on the command line, as "
              "`tools/can_frame_census.py can0`, or set can.interface in your "
              "spark.yaml. A CANcoder bus is cancoder.bus.")
        return _REFUSED
    if conf_err:
        print(f"  WARN: the config did not load ({conf_err}), so every row "
              "reads with an empty role. The census itself does not need it.")

    roles, complaints = _roles(conf, channel) if conf is not None else ({}, [])

    result = collect(channel, args.duration, helpers, apis)
    if result is None:
        return _REFUSED
    counts, payloads, tp = result.counts, result.payloads, result.tp

    print(f"\n{channel}  {result.elapsed:.1f}s of {args.duration:.1f}s   "
          f"(TX = this host transmitted it, not a device on the wire)\n")
    print(f"  {'dir':<4}{'error':<12}{'ext':<6}{'devType':>7}{'mfr':>6}"
          f"{'api':>8}{'devID':>7}{'frames':>8}  role")
    for k, n in counts.most_common():
        d, err, ext, dt, mfr, api, dev = k
        mfr_name = helpers.mfr_names.get(mfr, f"0x{mfr:02X}")
        role = roles.get(dev, "") if d == "RX" and ext and not err else ""
        print(f"  {d:<4}{(err or '-'):<12}{ext!s:<6}{dt:>7}{mfr_name:>6}"
              f"  0x{api:04X}{dev:>7}{n:>8}  {role}".rstrip())
        if args.payloads:
            for body in payloads[k]:
                print(f"        {body.hex(' ') or '(no data)'}")
    if result.filtered and counts:
        print(f"\n  {_frames(result.filtered)} dropped by --api {args.api}.")
    if roles and counts:
        print("\n  role is every devices entry in the config carrying that id, "
              "from either bus, because ids repeat per bus. Only an RX "
              "extended frame carries a device id at all.")
    for line in complaints:
        print(f"  WARN: config: {line}.")
    if complaints:
        print("  FIX: give every label under devices a number in 0-63, or "
              "drop the label. An unlabelled row is still censused.")

    if args.isotp:
        print("\n  reassembled ISO-TP\n")
        if not tp.done:
            print("  none: no RX extended frame carried ISO-TP transport.\n")
        for (api, dev), blobs in sorted(tp.done.items()):
            uniq = []
            for b in blobs:
                if b not in uniq:
                    uniq.append(b)
            print(f"  api 0x{api:03X}  dev {dev:>2}   "
                  f"{len(blobs)} replies, {len(uniq)} distinct")
            for i, b in enumerate(uniq[:3]):
                print(f"    #{i} ({len(b)} bytes)")
                for line in hexdump(b):
                    print(line)
            print()

    if result.interrupted:
        print(f"  INTERRUPTED after {result.elapsed:.1f}s of the "
              f"{args.duration:.1f}s window. The table covers only what was "
              "heard before Ctrl-C, and the rest of the window was never "
              "listened to.")
        return _INTERRUPTED
    if result.error:
        print(f"  ENDED EARLY after {result.elapsed:.1f}s of the "
              f"{args.duration:.1f}s window: {result.error}. The table covers "
              "only what was heard before that.")
        return _ENDED_EARLY
    if not sum(counts.values()):
        if result.filtered:
            print(f"  no frame matched --api {args.api}, so the census is "
                  f"empty. {_frames(result.filtered)} heard and dropped by "
                  "that filter, so the bus itself is not silent.")
        else:
            print(f"  nothing at all was heard in {result.elapsed:.1f}s. The "
                  "bus is silent, or the devices are on another netdev.")
        return _NOTHING_HEARD
    return _OK


if __name__ == "__main__":
    sys.exit(main())
