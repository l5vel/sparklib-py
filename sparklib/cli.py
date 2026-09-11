"""`spark` -- SPARK bus inventory, config audit and remote repair.

    uv run spark status                     what is on the bus, with serials
    uv run spark clear                      clear latched faults (wakes a silent bus)
    uv run spark throttle                   re-send the pre-25 status periods a power cycle drops
    uv run spark audit                      config drift + duplicate ids, exit 1 on fault
    uv run spark duplicates                 ids answered by more than one controller
    uv run spark identify --serial 498B2579 blink one controller's LED
    uv run spark set-id --serial X --to 17  reassign a CAN id, addressed by serial
    uv run spark learn-serials --write      record serials into the base config, once
    uv run spark repair --id 12 [--persist] restore Status 1 Period from the declared config
    uv run spark provision --id 12 --write  restore every declared setting that drifted
    uv run spark params --id 12 --all       read the whole parameter table off one controller
    uv run spark canfix                     clear a wedged USB CAN adapter without replugging it

Read-only unless the subcommand says otherwise. Nothing here sends a setpoint or
starts the enable heartbeat, so controllers stay disabled and cannot actuate.
"""

import argparse
import datetime
import os
import re
import sys
import textwrap
import time

from. import admin as sa
from. import config as cfg
from.admin import (FIX, PARAM_STATUS_1_PERIOD,
                    REV_DEFAULT_STATUS_1_PERIOD_MS,
                    SparkAdmin,
                    audit_problems, collect_status,
                    coverage_note,
                    deviating_settings,
                    dominant_generation,
                    fault_frame,
                    load_motor_defaults,
                    declared_status_1_period_ms,
                    motor_settings)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def _base():
    return cfg.get()


def _use_declared_product():
    """Point admin at this robot's product before reading declared values.

    The declared-configuration files are per product and the product is per
    robot. admin reads the files and never reads spark.yaml, so the
    selection has to be handed down from here. Every command that resolves a
    declared value calls this first; a command that only reads the wire does not
    need it.

    Called through _base(), so a test that pins _base to a robot pins the file
    that robot's values come from with it.
    """
    ct = getattr(_base(), "controller_type", None)
    sa.set_controller_type(ct)
    return ct


SPARK_GROUPS = ("drive", "steer")


# A pack monitor is a different instrument from the motor rail and sparklib
# ships none. A host application registers one and `spark voltage` reports it.
POWER_PROVIDER = None


def register_power_provider(fn):
    """Install a callable returning an object with `.percent`, or None."""
    global POWER_PROVIDER
    POWER_PROVIDER = fn
    return fn


def _pack_status():
    if POWER_PROVIDER is None:
        return None
    try:
        return POWER_PROVIDER()
    except Exception:                   # noqa: BLE001 - an optional instrument
        return None


# Status 4 is written by the boot table but never appears on the wire on
# pre-25, so a cadence check on it would always fail. See docs/SPARKMAX-BRINGUP.md.
ABSENT_FRAME_INDEX = 4


def _role_map(groups=None):
    """{device_id: 'group/CORNER'} from devices, restricted to `groups`.

    ASSUMES TWO BUSES: SPARKs on can.interface, CANcoders on
    base.cancoder_bus. Device ids are unique per bus rather than per robot, so
    the groups must be partitioned before keying on id -- flattening them lets
    one group silently overwrite another wherever ids overlap. A rig with
    CANcoders on the SPARK bus would need ids unique across every group instead.
    """
    roles = {}
    for group, corners in vars(_base().devices).items():
        if groups is not None and group not in groups:
            continue
        for corner, dev_id in vars(corners).items():
            roles[int(dev_id)] = f"{group}/{corner}"
    return roles


def _spark_roles():
    """Only the SPARK groups; cancoders live on the other bus and reuse these ids."""
    return _role_map(SPARK_GROUPS)


def _known_serials():
    """{device_id: serial} from serials, if learn-serials has been run."""
    blk = getattr(_base(), "serials", None)
    if blk is None:
        return {}
    out = {}
    for group, corners in vars(blk).items():
        for corner, serial in vars(corners).items():
            dev = getattr(getattr(_base().devices, group), corner, None)
            if dev is not None:
                out[int(dev)] = str(serial).upper()
    return out


def adapter_instability(iface, minutes=30):
    """Has this USB CAN adapter been falling off the bus lately?

    A bus that answers nothing looks identical whether the controllers are dark
    or the adapter is. The kernel knows which, and nothing here used to ask it.
    rig-max spent an hour checking motor power and the CAN chain over
    an adapter with an intermittent connector.

    Read-only, no sudo, and it never raises: a diagnostic that breaks the
    command it is helping is worse than no diagnostic.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["journalctl", "-k", "--no-pager", "--since", f"-{int(minutes)}min"],
            capture_output=True, text=True, timeout=6).stdout
    except Exception:
        return None
    if not out.strip():
        return None
    lines = [l for l in out.splitlines() if iface in l or "usb" in l.lower()]
    enum = len([l for l in lines if "Configuring for" in l])
    xmit = len([l for l in lines if "xmit fail" in l])
    stall = len([l for l in lines if "descriptor read" in l])
    gone = len([l for l in lines if "USB disconnect" in l])
    if not (enum or xmit or stall or gone):
        return None
    return {"minutes": int(minutes), "reenumerations": enum,
            "xmit_failures": xmit, "descriptor_stalls": stall,
            "disconnects": gone}


def adapter_instability_note(iface, minutes=30):
    """The sentences to print when a silent bus might be a silent adapter."""
    d = adapter_instability(iface, minutes)
    if not d:
        return []
    if not (d["disconnects"] or d["xmit_failures"] or d["descriptor_stalls"]):
        return []
    return [
        f"CHECK THE ADAPTER BEFORE THE ROBOT. In the last {d['minutes']} min the "
        f"kernel logged {d['disconnects']} USB disconnect(s), "
        f"{d['xmit_failures']} transmit failure(s), {d['descriptor_stalls']} "
        f"enumeration stall(s) and {d['reenumerations']} re-enumeration(s) on "
        "this bus.",
        "  An adapter that keeps leaving the USB bus silences everything and "
        "looks exactly like a dead fleet.",
        "  The TX LED is the fastest check: if the host is sending and that LED "
        "is dark, frames are not",
        "  reaching the adapter and the fault is the USB link, not the CAN bus. "
        "Flick the adapter with",
        "  `uv run python tools/can_usb_watch.py --seconds 0` running; a healthy "
        "one does not notice.",
        "  rig-max: an intermittent connector, reproduced by a finger "
        "flick. See",
        "  docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md",
    ]


def _adapter_state(iface):
    """What the netdev looks like right now, without transmitting anything.

    Read-only on purpose. Every `spark` subcommand calls this before opening the
    bus, and a probe that sent a frame to find out whether it could send a frame
    would put traffic on the bus for `spark status`.
    """
    import subprocess
    st = {"iface": iface, "exists": os.path.exists(f"/sys/class/net/{iface}"),
          "up": False, "bitrate": None, "can_state": None, "restart_ms": None,
          "parentdev": None, "bus_off": None}
    if not st["exists"]:
        return st
    out = subprocess.run(["ip", "-details", "-statistics", "link", "show", iface],
                         capture_output=True, text=True).stdout
    st["up"] = " state UP " in out
    for key, pat in (("bitrate", r"bitrate (\d+)"),
                     ("restart_ms", r"restart-ms (\d+)"),
                     ("can_state", r"can state (\S+)"),
                     ("parentdev", r"parentdev (\S+)")):
        m = re.search(pat, out)
        if m:
            st[key] = m.group(1)
    # The bus-off column of the error counter block, when the kernel printed one.
    m = re.search(r"re-started bus-errors.*?\n\s*(\d+)\s+(\d+)\s+(\d+)\s+"
                  r"(\d+)\s+(\d+)\s+(\d+)", out, re.S)
    if m:
        st["restarts"], st["bus_off"] = int(m.group(1)), int(m.group(6))
    return st


def _require_working_adapter(iface):
    """Refuse to open a netdev that cannot carry frames, and name the fix.

    A gs_usb adapter that has lost its netdev, or come back DOWN, produces the
    same symptom as eight dead controllers: silence. Saying which one it is costs
    one sysfs read, and it saves an operator from running `spark clear` against a
    bus the host cannot reach at all.

    Raises SystemExit rather than returning a message, so the diagnosis and the
    remedy stay in one place -- the operator reads one block, not a refusal here
    and advice somewhere else.
    """
    st = _adapter_state(iface)
    if st["exists"] and st["up"]:
        return
    if not st["exists"]:
        raise SystemExit(
            f"REFUSED: {iface} does not exist, so the USB CAN adapter is not "
            f"enumerated and no controller can be reached.\n"
            f"{FIX}check it is plugged in -- `lsusb -d 1d50:606f` for a "
            "candleLight/CANable. If it is plugged in and this persists, "
            "`uv run spark canfix` rebinds the driver, which clears an adapter "
            "that enumerated and then wedged. If the kernel logged `unable to "
            "enumerate USB device`, replug the cable: that is the one case a "
            "rebind cannot reach.")
    raise SystemExit(
        f"REFUSED: {iface} exists but the link is DOWN, so nothing can be sent "
        f"or received.\n"
        f"{FIX}`uv run spark canfix` brings it up at 1 Mbit and rebinds the "
        "driver if that is not enough. `systemctl status sparkarm-can.service` "
        "shows whether the udev bring-up fired.")


def _can_interface():
    return _base().can.interface


def _open():
    _require_working_adapter(_can_interface())
    return SparkAdmin(_can_interface())


# -- subcommands ------------------------------------------------------------

def _config_name():
    """Filename of the config actually in use."""
    return os.path.basename(cfg.config_path())


def _base_index():
    """The rig label a baseline and the experiment guard are keyed on."""
    return str(getattr(_base(), "rig_name", None) or "rig")


def _baseline_path():
    """spark-baseline.yaml, written beside the config it describes.

    Kept out of spark.yaml because `spark snapshot --write` generates it, and a
    tool that rewrites a hand-edited config file will eventually lose a comment
    or a key it did not understand.
    """
    declared = getattr(_base(), "baseline_path", None)
    if declared:
        return os.path.abspath(str(declared))
    return os.path.join(os.path.dirname(cfg.config_path()),
                        "spark-baseline.yaml")


def _load_baseline():
    path = os.path.abspath(_baseline_path())
    if not os.path.exists(path):
        return None, path
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh), path


def cmd_snapshot(args):
    """Capture this bus as the baseline for this base index."""
    import subprocess
    roles = _spark_roles()
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, min(args.window, 1.0)))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        # Pre-25 broadcasts no serial, so an inventory that does not ask for the
        # fingerprint writes the literal string None into every identity field
        # and the baseline cannot tell a swapped controller from a moved id --
        # which is the one thing a baseline exists to do.
        inv = adm.inventory(args.window, with_fingerprint=pre25)
        fw = {d: adm.firmware(d)[0] for d in sorted(inv)}
        # The refusal comes first and the sweep reads its result, so the two
        # cannot drift apart. Duplicating the predicate here let a 46-frame
        # sweep run against a bus the next line was about to refuse.
        missing = sorted(set(roles) - set(inv))
        table = None
        if not missing and getattr(args, "parameters", True):
            table = sa.fleet_param_table(adm, sorted(roles), pre25=pre25,
                                         wait=getattr(args, "wait", 0.4),
                                         role_of=roles.get)
    if missing:
        print("refusing to snapshot an incomplete bus; these ids are silent:")
        for d in missing:
            print(f"  {d} ({roles[d]})")
        print("\nrun `spark clear` first.")
        return 1

    rev = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)),
                          "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True)
    lines = [
        f"# SPARK baseline for {_base_index()} -- the reference a later `spark audit`",
        "# compares against. Regenerate with `spark snapshot --write` only when the",
        "# bus is known good.",
        "meta:",
        f"  rig_name: {_base_index()}",
        f"  config: {_config_name()}",
        f"  captured_utc: '{_utcnow()}'",
        f"  controller_type: {_base().controller_type}",
        f"  generation: {sa.normalise_generation(gen)}",
        f"  can_interface: {_can_interface()}",
        f"  git_rev: {rev.stdout.strip() or 'unknown'}",
        "controllers:",
    ]
    # STATUS_1 is 0x2E1 on firmware 25+ and 0x061 before it. Reading the 25+ api
    # on a pre-25 bus finds nothing broadcasting and writes null for every
    # controller, so the period half of the baseline was blank on this fleet.
    status_1_api = sa.API_SETS[sa.normalise_generation(gen)]["status_1"]
    for dev in sorted(inv):
        p1 = inv[dev]["periods_ms"].get(status_1_api)
        lines += [
            f"  {dev}:",
            f"    role: {roles.get(dev, 'UNCONFIGURED')}",
            f"    serial: '{inv[dev]['serial']}'",  # pre-25: the api 0x094 fingerprint
            f"    firmware: '{fw.get(dev) or 'unknown'}'",
            f"    status_1_period_ms: {p1 if p1 is not None else 'null'}",
        ]
    if table is not None:
        # Undeclared ids only, so this and the declared file partition the id
        # space and one deliberate config edit is never reported twice.
        if table.get("short"):
            print("refusing to record a parameter table: some controllers did "
                  "not answer every id, so a shorter table would look like a "
                  "smaller fleet configuration rather than a lost read.")
            for dev, ids in sorted(table["short"].items()):
                print(f"  id {dev} missed {len(ids)}: {ids[:12]}"
                      f"{'...' if len(ids) > 12 else ''}")
            print(f"{FIX}re-run with a longer --wait. A whole 16-id block goes "
                  "missing when one apiClass 13 types frame times out, because "
                  "the values are never requested for ids reported Unused.")
            return 1
        if table["divergent"]:
            print("refusing to record a parameter table: "
                  f"{len(table['divergent'])} undeclared parameter(s) differ "
                  "across the fleet, so there is no fleet value to record.")
            for pid, answers in sorted(table["divergent"].items()):
                shown = ", ".join(f"{d}:{v}" for d, v in sorted(answers.items()))
                print(f"  {pid:>4}  {shown}")
            print(f"{FIX}the majority is not an authority here. Nothing declares "
                  "these ids, so decide which value is right, put the odd "
                  "controller back with `uv run spark provision` or `uv run "
                  "spark params --set`, and re-run this. Pass --no-parameters "
                  "to capture identity and cadence without them.")
            return 1
        lines += ["parameters:",
                  "  # Undeclared ids only: the declared file answers for the "
                  "rest, and a value",
                  "  # here is what a bus known to be good was holding, not what "
                  "anything requires.",
                  "  # Raw uint32 words, which is what the wire carries and what "
                  "compares exactly.",
                  f"  captured_ids: {len(table['common'])}",
                  "  common:"]
        index = _parameter_index()
        for pid, raw in sorted(table["common"].items()):
            name = index.get(pid, {}).get("name", "")
            kind = table["types"].get(pid, "")
            shown = sa.declared_param_value(raw, {"float": "FLOAT", "float32": "FLOAT",
                                                  "boolean": "BOOL", "bool": "BOOL",
                                                  "int": "INT32", "int32": "INT32"}
 .get(str(kind).lower(), "UINT32"))
            # One comment marker, emitted once, whichever halves are present.
            # Building the marker with the NAME left every unnamed id past 198
            # rendering as `202: 0 = 0`, which YAML reads as the string "0 = 0".
            bits = [b for b in (name, f"= {shown:g}" if isinstance(shown, float)
                                else None) if b]
            note = ("  # " + " ".join(bits)) if bits else ""
            lines.append(f"    {pid}: {raw}{note}")

    text = "\n".join(lines) + "\n"
    path = os.path.abspath(_baseline_path())
    if not args.write:
        print(text)
        print(f"dry run -- pass --write to save to {path}")
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path + ".bak", "w") as fh:
            fh.write(open(path).read())
    with open(path, "w") as fh:
        fh.write(text)
    print(f"wrote {path}")

    # Serials are the bus's only stable identity, so a baseline captured without
    # them cannot later distinguish a swapped controller from a moved CAN id.
    # Record them now if setup skipped that step, rather than leaving a baseline
    # that quietly cannot be audited properly.
    if not _known_serials():
        print("\nserials is not set -- recording it now so this baseline "
              "can be audited against hardware identity.")
        rc = cmd_learn_serials(argparse.Namespace(
            window=args.window, write=True, config=None))
        if rc != 0:
            print("could not record serials; run `spark learn-serials --write` "
                  "once the whole bus is broadcasting.")
    return 0


# A base index here is carrying an experiment whose value is destroyed by writing
# to it. Remove an entry when the experiment is finished and written up, never to
# get a command to run. tests/support/sparkhw/guards.py enforces the same table
# for the hardware suite.
LIVE_EXPERIMENTS = {
    "rig-flex-2": (
        "rig-flex-2 is the brownout / config-loss experiment. Controllers 9, 10, 11, "
        "12 and 14 are drifted and are the control; 13, 15 and 16 hold the "
        "provisioned 20 ms across a power cycle at 10.24 V and are the subjects. "
        "Any write, and every persist, destroys both halves. Protocol: "
        "docs/runs/rig-flex-2-brownout-protocol.md"
 ),
}


def _refuse_on_the_example_config(command):
    """Refuse a writing command aimed by the config that ships with the package.

    sparklib/data/spark.yaml is a worked example off a real eight-motor bus, so
    it carries real device ids and real declared settings. That makes it useful
    to read and dangerous to write from: `provision` would push one rig's values
    onto whatever answers those ids on yours.

    It also catches the case where two packages both install a `spark` command
    and the wrong one wins, because the give-away is exactly this: a config
    nobody pointed at.
    """
    if os.path.abspath(cfg.config_path()) != os.path.abspath(cfg.SHIPPED_CONFIG):
        return False
    print(f"REFUSED: `spark {command}` writes to controllers, and the config in "
          f"use is the example that ships with the package:\n"
          f"    {cfg.SHIPPED_CONFIG}\n\n"
          "  Its device ids and declared settings come from one particular bus, "
          "so writing from it would push that rig's values onto yours.\n\n"
          f"  FIX: copy it, edit the ids to match your bus, and point "
          f"${cfg.ENV_VAR} at your copy:\n"
          "      cp \"$(uv run python -c 'import sparklib.config as c; print(c.SHIPPED_CONFIG)')\" ./spark.yaml\n"
          f"      export {cfg.ENV_VAR}=$PWD/spark.yaml\n")
    return True


def _refuse_on_a_live_experiment(command):
    """Refuse a writing command on a robot carrying an experiment.

    The hardware test suite has refused this since the table was written. The
    CLI did not, so `spark repair` on rig-flex-2 would have ended the experiment
    from a shell with no warning.
    """
    reason = LIVE_EXPERIMENTS.get(_base_index())
    if not reason:
        return False
    print(f"REFUSED: `spark {command}` writes to {_base_index()}, which must not "
          f"be written to.\n\n  {reason}\n\n"
          "  FIX: read the bus instead -- `uv run spark status`, `uv run spark "
          "faults`, `uv run spark audit`. To end the experiment deliberately, "
          "write up its findings first, then remove the entry from "
          "LIVE_EXPERIMENTS in cli.py and tests/support/sparkhw/guards.py.\n")
    return True


def cmd_defaults(args):
    """Show the declared configuration every motor is provisioned to."""
    mine = _use_declared_product()
    fname = sa.motor_defaults_file(mine)
    d = load_motor_defaults(required=False)
    if not d:
        print(f"could not read {fname}")
        print("  FIX: a default config ships in sparklib/data/spark.yaml. "
              "Copy it beside your work and point $SPARKLIB_CONFIG at it.")
        return 1
    m = d.get("meta", {})
    declared = sa.motor_defaults_product(d)
    if declared and mine and declared != mine:
        print(f"WARNING: {fname} applies to {declared} and this robot is {mine}. "
              f"Every value below is a {declared} value.\n")
    print(f"declared motor configuration -- {m.get('settings')} settings, "
          f"{m.get('deviating_from_factory_default')} deviate from factory default")
    print(f"source: {m.get('source')}\n")
    roles = [args.role] if args.role else ["steer", "drive"]
    for role in roles:
        settings = motor_settings(role)
        shown = deviating_settings(role) if not args.all else settings
        label = "all" if args.all else "deviating from factory default"
        print(f"  {role} ({len(shown)} of {len(settings)} shown -- {label})")
        print(f"    {'id':>4}  {'RHC tab':<28} {'setting':<30} {'value':<15} "
              f"{'REV default'}")
        src = d.get("common", []) + d.get("by_role", [])
        for pid, st in sorted(shown.items(), key=lambda kv: (kv[1].get("tab") or "",
                                                             kv[0])):
            ref = next((e for e in src if e["param_id"] == pid), {})
            print(f"    {pid:>4}  {(st.get('tab') or '?'):<28} {st['name']:<30} "
                  f"{str(st['value']):<15} {ref.get('rev_default', '-')}")
        print()
    print("  RHC tab = where to set it in REV Hardware Client 2: select the device,")
    print("            Configuration, then that tab. Remember the orange Save button.")
    print(f"  edit: {cfg.config_path()}, under {sa.motor_defaults_key(mine)}:")
    print("  per-controller identity (CAN id, serial) lives in the same "
          "file, under devices: and serials:")
    # What can be checked without USB-C is the opposite on the two products, so
    # saying "the rest need RHC2" on a MAX would send an operator to a cable they
    # do not need for anything in this table.
    if mine == "sparkmax":
        print("  every parameter 0-133 is readable over CAN on pre-25 firmware; "
              "the status periods are not")
        print("  parameters and are read with `spark throttle` instead")
    else:
        print("  every parameter 0-255 is readable over CAN on firmware 25+")
        print("  Status 1 Period is the only one also verifiable as a cadence")
    return 0


def cmd_status(args):
    roles, known = _spark_roles(), _known_serials()
    with _open() as adm:
        status = collect_status(adm.bus, min(args.window, 1.0))
        gen = dominant_generation(status)
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        # Pre-25 broadcasts no identity, so ask for it. Costs one round trip per
        # controller and turns the serial column from permanently blank into the
        # thing that makes a swapped controller visible.
        inv = adm.inventory(args.window, with_fingerprint=pre25)
        print(f"bus {adm.channel} -- {len(inv)} REV controller(s)\n")
        ff = fault_frame(gen)
        ident = "fingerprint" if pre25 else "serial"
        print(f"  {'id':>3}  {'role':<10} {ident:<12} {'fw':<9} "
              f"{ff['label'].lower():>9}  notes")
        for dev, info in inv.items():
            fw, _ = adm.firmware(dev)
            p1 = info["periods_ms"].get(ff["api"])
            notes = []
            if dev not in roles:
                notes.append("NOT IN CONFIG")
            if known and dev in known and info["serial"] != known[dev]:
                notes.append(f"serial != config ({known[dev]})")
            if p1 is None:
                notes.append(f"no {ff['label']}")
            elif (ff["rev_default_ms"] != ff["expected_ms"]
                    and abs(p1 - ff["rev_default_ms"]) < 30):
                notes.append(f"{ff['label']} at factory default (CAN ID intact)")
            print(f"  {dev:>3}  {roles.get(dev, '-'):<10} "
                  f"{info['serial'] or '-':<12} {fw or '-':<9} "
                  f"{(str(p1) + ' ms') if p1 else '-':>9}  {', '.join(notes)}")
        missing = sorted(set(roles) - set(inv))
        if missing:
            print(f"\n  MISSING: {', '.join(f'{d} ({roles[d]})' for d in missing)}")
            note = adapter_instability_note(_can_interface())
            if note:
                print("  " + "\n  ".join(note))
            elif not inv:
                print("  FIX: NOTHING answered, which a silent adapter and a "
                      "silent fleet both produce. Check the adapter's TX LED "
                      "while something sends: dark means the frames never "
                      "reach it. Then `uv run spark clear`, then motor power "
                      "and the LED on each missing controller.")
            else:
                print("  FIX: a latched fault stops a controller broadcasting "
                      "AND ACKing, which can silence the whole bus. Run `uv run "
                      "spark clear` first, then check motor power and the LED "
                      "on each missing controller.")
        _warn_if_unthrottled(inv, ff, gen)
        # This tree assumes MAX means pre-25 and Flex means firmware 25+. That is
        # true of rig-max and rig-flex and is not a protocol guarantee, so say so
        # loudly if the wire ever disagrees rather than letting notes and
        # defaults written against it quietly go stale.
        broken = sa.unexpected_generation(
            getattr(_base(), "controller_type", None),
            sa.observed_generation(status))
        if broken:
            print(f"\n  FLEET ASSUMPTION BROKEN: {broken}")
    return 0


def _warn_if_unthrottled(inv, ff, generation=None):
    """Say so when a pre-25 bus is running at REV's cold defaults.

    This is the state a rail cycle leaves behind, and nothing puts the throttle
    back until the driver starts or `spark throttle` runs. It is not a fault and
    every controller is healthy, which is exactly why it goes unnoticed.

    It matters because of the adapter, not the controllers. gs_usb turns every
    CAN frame into a transfer on a 12 Mbit full-speed USB link, and
    docs/SPARKMAX-BRINGUP.md calls leaving a cold bus there the wedge risk. This
    line makes the state visible before it becomes one.

    The figure this prints is the CONTROLLER contribution, because `inv` carries
    REV controllers only. Measured on rig-max, decomposed by api over a
    10 s window:

        eight controllers, REV cold defaults    1872 frames/s
        eight controllers, boot throttle         384 frames/s
        other traffic on this bus                404 frames/s, constant
        TOTAL on the wire                       2276 cold, 788 throttled

    Both totals close exactly on the sum, so the controller figure and the wire
    figure are both right and are not interchangeable. Earlier notes quoted them
    against each other. The 404 does not move when the throttle is applied.
    """
    # Scored against the table this package actually writes, NOT against
    # ff["expected_ms"]. On pre-25 fault_frame returns REV's 10 ms for both
    # expected_ms and rev_default_ms, so comparing those two can never separate a
    # throttled bus from a cold one -- that is the open defect SPARKMAX.md
    # section 6 names, and a check built on it is silent exactly when it matters.
    deliberate = sa._deliberate_periods(generation)
    want = deliberate.get(ff["api"])
    if want is None or abs(want - ff["rev_default_ms"]) < 5:
        return                      # no separate throttle on this generation
    cold = [d for d, i in inv.items()
            if (i["periods_ms"].get(ff["api"]) is not None
                and abs(i["periods_ms"][ff["api"]] - ff["rev_default_ms"]) < 5)]
    if not cold:
        return
    rate = sum(1000.0 / max(p, 1) for i in inv.values()
               for p in i["periods_ms"].values() if p)
    print(f"\n  UNTHROTTLED: {len(cold)} of {len(inv)} controller(s) are at "
          f"REV's cold default on {ff['label']}, not this package's boot "
          f"throttle. That is the state a power cycle leaves behind.")
    print(f"  {FIX}`uv run spark throttle` re-sends the table. The controllers "
          f"are contributing roughly {rate:.0f} frames/s in this state against "
          "about 384 at the boot throttle, and leaving a gs_usb adapter at the "
          "higher rate is what docs/SPARKMAX-BRINGUP.md calls the wedge risk. "
          "This counts the REV controllers only; anything else on the bus is on "
          "top and is not reduced by throttling.")


def _report_cleared(record, roles):
    """Print what the clear erased and what refused to release.

    The sticky bytes are the only record of a brownout or a reboot and this
    command destroys them, so they are printed here or they are lost. A fault
    still present afterwards regenerated through the clear, which is a
    different repair from one that released.
    """
    erased, held = [], []
    for dev in sorted(record):
        r = record[dev]
        gone = r["sticky_faults"] + r["sticky_warnings"]
        if gone:
            erased.append(f"    {dev:>3} {roles.get(dev, '-'):<10} {', '.join(gone)}")
        if not r["cleared"] and r["answered"]:
            held.append(f"    {dev:>3} {roles.get(dev, '-'):<10} "
                        f"{', '.join(r['faults']) or 'sticky only'}")
    if erased:
        print("  erased, and recorded here because nothing else holds it:")
        print("\n".join(erased))
    if held:
        print("  STILL FAULTED after the clear -- these regenerated through it:")
        print("\n".join(held))
        print("    FIX: a fault that comes straight back is not latched state. "
              "A gate driver fault that survives a clear, a reflash and a "
              "factory reset is an RMA. `uv run spark faults` names the bit.")


def cmd_throttle(args):
    """Re-send the volatile per-boot status periods a pre-25 fleet loses."""
    with _open() as adm:
        status = collect_status(adm.bus, 2.0)
        gen = dominant_generation(status)
        if gen != sa.GEN_PRE25:
            print(f"this bus reads {gen}, which is left at REV's defaults -- "
                  "there is no boot throttle to re-apply here.")
            return 0

        from.can_bus import _SPARKMAX_STATUS_PERIODS_MS
        roles = _spark_roles()
        before = adm.inventory(args.window)
        if not before:
            print("no controller is broadcasting -- run `spark clear` first")
            return 1

        for dev in sorted(before):
            for idx, ms in _SPARKMAX_STATUS_PERIODS_MS.items():
                adm.set_legacy_status_period(dev, idx, ms)
        time.sleep(1.5)
        after = adm.inventory(args.window)

    # Every frame that was written is checked, not just the first. Verifying one
    # and reporting on all of them would let a controller that took frame 0 and
    # dropped the rest exit 0.
    base_api = sa.LEGACY_STATUS_0_API
    checked = {i: float(ms) for i, ms in _SPARKMAX_STATUS_PERIODS_MS.items()
               if i != ABSENT_FRAME_INDEX}
    print(f"  {'id':>3}  {'role':<10} {'0x060 before':>13} {'after':>8}  frames ok")
    missed = {}
    for dev in sorted(before):
        was = (before[dev]["periods_ms"] or {}).get(base_api)
        now_all = after.get(dev, {}).get("periods_ms") or {}
        bad = {f"0x{base_api + i:03X}": now_all.get(base_api + i)
               for i, want in checked.items()
               if now_all.get(base_api + i) is None
               or abs(now_all[base_api + i] - want) > max(8.0, want * 0.3)}
        if bad:
            missed[dev] = bad
        now = now_all.get(base_api)
        print(f"  {dev:>3}  {roles.get(dev, '-'):<10} "
              f"{(f'{was:.0f} ms' if was else '-'):>13} "
              f"{(f'{now:.0f} ms' if now else '-'):>8}"
              f"  {len(checked) - len(bad)}/{len(checked)}")

    if missed:
        print("\n  these frames did not take the throttle:")
        for dev, bad in sorted(missed.items()):
            print(f"    id {dev} {roles.get(dev, '-')}: {bad}")
        print(FIX + "the write is unacknowledged by design on this firmware, so "
              "the cadence is the only read-back. Re-run; if it stays, check "
              "bus utilisation and termination.")
        return 1
    print(f"\n  all {len(before)} controller(s) at the boot throttle across "
          f"{len(checked)} frames "
          f"({', '.join(f'{k}:{v}' for k, v in _SPARKMAX_STATUS_PERIODS_MS.items())} ms)")
    print(f"  frame {ABSENT_FRAME_INDEX} is written but never broadcasts on this "
          "firmware, so it is not checked.")
    print("  RAM only -- these are lost whenever a controller restarts.")
    return 0


def cmd_clear(args):
    roles = _spark_roles()
    with _open() as adm:
        # Read and PERSIST before the clear. The sticky bits are the only record
        # a brownout or a reboot leaves.
        before = collect_status(adm.bus, min(getattr(args, "window", 4.0), 1.0))
        print(f"clearing latched faults on {sorted(roles)}...")
        record = adm.clear_faults(sorted(roles))
        try:
            path = sa.write_fault_record(record, status=before)
            print(f"  pre-clear record saved: {path}")
        except OSError as e:
            print(f"  WARNING: could not save the pre-clear record ({e}). "
                  "The bits below are now the only copy -- keep this output.")
        kinds = {d: sa.classify_reset(r.get("sticky_warnings"),
                                      r.get("sticky_faults"))
                 for d, r in (record or {}).items()}
        brownouts = sorted(d for d, k in kinds.items() if k == "brownout")
        if brownouts:
            print(f"  BROWNOUT on {brownouts}: the rail collapsed and took the "
                  "controller with it. This is a POWER PATH fault -- battery "
                  "state of charge, main breaker, lug torque, wire gauge -- and "
                  "re-provisioning fixes none of it. `uv run spark voltage`.")
        elif any(k == "power-cycle" for k in kinds.values()):
            print("  hasReset without brownout: an ordinary power cycle, no "
                  "power-path fault to chase. Re-apply the configuration.")
        _report_cleared(record, roles)
        inv = adm.inventory(args.window)
        back = sorted(inv)
        print(f"broadcasting now: {back} ({len(back)}/{len(roles)})")
        missing = sorted(set(roles) - set(inv))
        if missing:
            print(f"still silent: {', '.join(f'{d} ({roles[d]})' for d in missing)}")
            note = adapter_instability_note(_can_interface())
            if note:
                print("  " + "\n  ".join(note))
            elif len(missing) == len(roles):
                print("  FIX: clearing did not wake ANY of them, which is as "
                      "consistent with a silent adapter as with a silent "
                      "fleet. Check the adapter's TX LED while something "
                      "sends: dark means the frames are not reaching it.")
            print("  FIX: clearing did not wake these, so the fault is not "
                  "latched state. Check 12V at the controller and the CAN "
                  "chain past the last one that did answer -- a break silences "
                  "everything downstream of it, so the lowest missing id is "
                  "where to look.")
            return 1
    return 0


def cmd_duplicates(args):
    known = _known_serials()
    roles = _spark_roles()
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, 2.0))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        if pre25:
            # Requested, not listened for. Pre-25 broadcasts no identity, so a
            # duplicate is found by asking each id for its fingerprint and
            # counting the distinct answers -- two controllers on one id both
            # reply. Sweeps the whole legal range, because a duplicate sitting at
            # an id no config knows about is exactly the case that hides.
            dups = adm.duplicates(args.window, generation=gen)
        else:
            dups = adm.duplicates(args.window)
    if not sa.duplicate_detection_available(gen):
        print(f"CANNOT TELL -- this bus reads {gen}, and no identity is readable "
              "there, so two controllers sharing an id are indistinguishable "
              "from one.")
        return 0
    if not dups:
        how = ("every id in 1..62 was asked for its fingerprint and answered "
               "at most once" if pre25
               else "every UNIQUE_ID seen was unique to its id")
        print(f"no duplicate CAN ids -- {how}")
        return 0
    label = "fingerprint" if pre25 else "serial"
    inv_serial = {s: d for d, s in known.items()}
    for dev, serials in dups.items():
        print(f"\nCAN id {dev} is answered by {len(serials)} controllers:")
        for s in serials:
            owner = inv_serial.get(s)
            if owner:
                owner_label = f"belongs at id {owner} ({roles.get(owner, '?')})"
            elif pre25:
                owner_label = "not recorded anywhere; identity here is this "\
                              "fingerprint and the CAN id"
            else:
                owner_label = "not in serials -- run `spark learn-serials`"
            print(f"    {label} {s}   {owner_label}")
        if pre25:
            # Neither remedy that works on 25+ works here, and naming one would
            # send an operator to a command that refuses. `learn-serials` needs
            # UNIQUE_ID, which this generation does not broadcast, and set-id is
            # addressed by serial, which is untested on pre-25 -- identify turned
            # out to be addressed by CAN id instead, so set-id may be too.
            print("\n  fix: the two cannot be addressed apart over CAN, because "
                  "an addressed frame reaches both and only the first reply is "
                  "read.")
            print("    * unplug one controller at a time and re-run this "
                  "command to learn which fingerprint is which")
            print("    * then set the id over USB-C in REV Hardware Client")
            print("    * `spark set-id` is NOT a route here: it addresses by "
                  "serial, and whether pre-25 accepts that is untested")
        else:
            print("\n  fix, one controller at a time:")
            for s in serials:
                owner = inv_serial.get(s)
                if owner is not None and owner != dev:
                    print(f"    uv run spark set-id --serial {s} --to {owner}")
    return 1


def cmd_identify(args):
    """Blink one controller's LED, in whichever form this generation wants.

    Read off the wire rather than from controller_type, because a SPARK MAX
    updated to 25.0.0 takes the Flex form.
    """
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, min(args.window, 2.0)))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25

        if pre25:
            if args.id is None:
                print(f"REFUSED: this bus reads {gen}, where identify is "
                      "addressed by CAN ID and takes no serial at all. Pre-25 "
                      "broadcasts no serial for --serial to name.")
                print(f"{FIX}`uv run spark identify --id N`. "
                      "`uv run spark status` lists the ids.")
                return 2
            adm.identify_by_id(args.id)
            print(f"sent IDENTIFY to id {args.id} -- its LED should blink now.")
            print("  Nothing acknowledges this frame, so an operator watching the "
                  "controller is the only confirmation. Repeat it with "
                  "`uv run python tools/spark_blink.py --id N --repeat 5` if the "
                  "pulse is too short to catch.")
            return 0

        if args.serial is None:
            print(f"REFUSED: this bus reads {gen}, where identify is addressed by "
                  "SERIAL and broadcast, so --id cannot name a target.")
            print(f"{FIX}`uv run spark identify --serial X`. "
                  "`uv run spark status` lists the serials.")
            return 2
        adm.identify(args.serial)
        print(f"sent IDENTIFY to serial {args.serial.upper()} -- its LED should blink")
        return 0


def cmd_set_id(args):
    if _refuse_on_the_example_config("set-id"):
        return 2
    if _refuse_on_a_live_experiment("set-id"):
        return 2
    serial = args.serial.upper()
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, min(args.window, 1.0)))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        # Ask for the identity on pre-25, or the lookup below can never match:
        # that generation broadcasts none, so `serial` stays None for every
        # controller and the command reports the target as not present.
        inv = adm.inventory(args.window, with_fingerprint=pre25)
        at = [d for d, i in inv.items() if i["serial"] == serial]
        if not at:
            print(f"serial {serial} is not broadcasting -- nothing to reassign")
            print("  FIX: `uv run spark status` lists the serials that are "
                  "answering. If this one is absent, run `uv run spark clear` "
                  "and re-check before assuming the controller is dead.")
            return 1
        current = at[0]
        if current == args.to:
            print(f"serial {serial} is already at id {args.to}")
            return 0
        print(f"serial {serial} is at id {current}; moving to {args.to}")
        outcome = adm.set_can_id(current, serial, args.to)
        if outcome is False:
            print(f"REFUSED -- serial {serial} is still at id {current}")
            print("  FIX: firmware sends no NACK for a refused id change, so "
                  "this was found by looking at who is broadcasting. A "
                  "controller in recovery mode ignores the frame. Power cycle "
                  "it and retry; if it refuses again, set the id over USB-C in "
                  "the REV Hardware Client.")
            return 1

        inv = adm.inventory(args.window)
        now = [d for d, i in inv.items() if i["serial"] == serial]
        if now != [args.to]:
            print(f"FAILED -- serial now at {now or 'nowhere'}")
            print(f"  FIX: the move did not hold. Power-cycling returns this "
                  f"controller to id {current}. Re-run; if it fails again, "
                  f"another controller already holds id {args.to} -- check "
                  "with `uv run spark duplicates`.")
            return 1

        if outcome is None and pre25:
            # Expected on this generation, not a failure. set_can_id finishes by
            # calling persist(), which sends the 25+ PERSIST_PARAMETERS that
            # 24.0.1 does not carry, so it is dropped and returns None. The
            # firmware's own burn is api 0x072 with the magic 15011, measured to
            # work, and this package deliberately does not send it.
            print(f"MOVED, RAM ONLY -- {serial} answers on id {args.to} now, and "
                  "nothing burned it to flash.")
            print(f"{FIX}this WILL revert at the next power cycle. The frame "
                  "that would persist it (PERSIST_PARAMETERS) does not exist on "
                  "this firmware, and the one that does (api 0x072) is not "
                  "exposed here. Set the id over USB-C in REV Hardware Client if "
                  "it has to survive a rail cycle. See pre25.burn_flash_api.")
            return 1
        if outcome is None:
            print(f"MOVED but NOT CONFIRMED -- serial {serial} answers on id "
                  f"{args.to} now, and the flash burn was not acknowledged")
            print(f"  FIX: the id is live but may be gone at the next power "
                  f"cycle (CD 391976). Run `uv run spark persist --id "
                  f"{args.to}`, then power cycle and re-check `uv run spark "
                  "status` before trusting it.")
            return 1

        print(f"OK -- serial {serial} now answers on id {args.to}, burned to flash")
        return 0


def _parameter_index():
    """{id: {name, type, access, default}} from data/rev_parameter_index.tsv.

    Names and TYPES only. Its status-period defaults do not match pre-25 firmware
    -- it gives parameter 159 a default of 250 ms and 165 a default of 20 ms,
    while the measured pre-25 values for those frames are 20 ms and 250 ms -- so
    the `default` column is shown as REV's documented value and never used to
    decide anything.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "data", "rev_parameter_index.tsv")
    rows = {}
    try:
        with open(path) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 5 and parts[0].strip().isdigit():
                    rows[int(parts[0])] = {"name": parts[1], "type": parts[2],
                                           "access": parts[3], "default": parts[4]}
    except OSError:
        pass
    # The file stops at 199, and the read frames reach 255. Anything declared in
    # the product's own defaults file is named from there instead of showing "?".
    for pid, st in (sa.motor_settings("steer") or {}).items():
        rows.setdefault(pid, {"name": st.get("name", ""),
                              "type": str(st.get("type", "")).lower(),
                              "access": "RW", "default": st.get("rev_default", "")})
    return rows


def _read_table(adm, dev, pids, wait, modern):
    """{pid: {'raw','type','ok'}} for one controller, in its own read dialect."""
    if not modern:
        out = {}
        for pid in pids:
            if 158 <= pid <= 165 or pid > sa.LEGACY_PARAM_MAX:
                continue
            r = adm.read_legacy_param(dev, pid, wait=wait)
            if r is not None and r["ok"]:
                out[pid] = r
        return out
    types = {}
    for start in range(0, sa.PARAM_ID_MAX + 1, 16):
        types.update(adm.param_types(dev, start_id=start, wait=wait) or {})
    out = {}
    for pid in pids:
        raw = adm.read_param_value(dev, pid, wait=wait)
        if raw is None:
            continue
        kind = str(types.get(pid, "")).lower()
        if kind == "unused":
            continue
        out[pid] = {"raw": raw, "ok": True,
                    "type": {"float": "float32", "boolean": "bool",
                             "uint": "uint32", "int": "int32"}.get(kind, kind)}
    return out


def _params_refuse(pid):
    """Why this parameter must not be addressed, or None."""
    if pid in sa.PROTECTED_PARAMS:
        return (f"parameter {pid} is protected: {sa.PROTECTED_PARAMS[pid]}. "
                "A bad write here is not recoverable on a generation that "
                "broadcasts no serial to find the controller with afterwards.")
    if 158 <= pid <= 165:
        return (f"parameter {pid} is a Status Frame Period, and periods are NOT "
                "parameters on this generation -- they move on api class 6 and "
                "these ids answer with a non-zero status. Use `uv run spark "
                "throttle`, which re-sends the whole table and measures the "
                "cadence back.")
    if pid > sa.LEGACY_PARAM_MAX:
        return (f"parameter {pid} is above LEGACY_PARAM_MAX ({sa.LEGACY_PARAM_MAX}). "
                "Above the table the SAME api range carries COMMANDS: 0x300 | 255 "
                "is 0x0205FFC0, which is Persist Parameters. A sweep that runs "
                "past the table finishes by firing a flash command.")
    return None


def cmd_params(args):
    """Read and write the parameter table, in whichever dialect the bus answers.

    Pre-25 answers the legacy api for parameters 0-133, and rig-max answers all
    134 on all eight controllers. Firmware 25+ answers READ_PARAMETER for all of
    0-255, measured on rig-flex with zero silent frames across a full
    sweep of eight SPARK Flex.
    """
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, min(args.window, 2.0)))
        modern = sa.normalise_generation(gen) != sa.GEN_PRE25

        roles = _spark_roles()
        devs = [args.id] if args.id is not None else sorted(
            set(roles) & set(adm.inventory(args.window)))
        if not devs:
            print("no controllers to read")
            return 1

        if args.set is not None:
            return _params_write(adm, args, roles, modern=modern)

        index = _parameter_index()
        top = sa.PARAM_ID_MAX if modern else sa.LEGACY_PARAM_MAX
        pids = list([args.param] if args.param is not None else range(0, top + 1))
        if args.param is not None and args.param > top:
            print(f"REFUSED: {_params_refuse(args.param)}" if not modern else
                  f"REFUSED: parameter {args.param} is above {top}. One index "
                  "past the last read frame is WRITE_PARAMETER_0_AND_1, a write "
                  "of the device's own CAN id.")
            return 2

        table = {dev: _read_table(adm, dev, pids, args.wait, modern)
                 for dev in devs}

        answered = {d: len(v) for d, v in table.items()}
        print(f"read {min(answered.values())}-{max(answered.values())} of "
              f"{len(list(pids))} parameter(s) on {len(devs)} controller(s)\n")

        every = sorted({pid for v in table.values() for pid in v})
        # Parameter 0 is each device's own CAN id, so it differs by definition on
        # any fleet. Counting it as drift buries the one row that is drift.
        differs = [pid for pid in every
                   if pid != sa.PARAM_CAN_ID
                   and len({table[d][pid]["raw"] for d in devs if pid in table[d]}) > 1]

        hdr = f"  {'id':>4}  {'name':<32} {'type':<8} " + "".join(
            f"{('dev ' + str(d)):>12}" for d in devs)
        print(hdr)
        shown = every if (args.all or args.param is not None) else differs
        if not shown and not args.all:
            print("  one controller, so there is nothing to compare against. "
                  "--all shows the table anyway." if len(devs) == 1 else
                  "  every parameter reads identically on all controllers. "
                  "--all shows the table anyway.")
        for pid in shown:
            meta = index.get(pid, {})
            row = f"  {pid:>4}  {meta.get('name', '?'):<32} " \
                  f"{(table[devs[0]].get(pid) or {}).get('type', '-'):<8} "
            for d in devs:
                r = table[d].get(pid)
                row += f"{(_params_show(r) if r else '-'):>12}"
            print(row)

        if differs and not args.all:
            print(f"\n  {len(differs)} of {len(every)} parameter(s) differ across "
                  "the fleet; the rest are identical.")
        print("\n  a float32 parameter is shown decoded, because 1073741824 and "
              "2.0 are the same number and only one of them is readable. The "
              "type column says which decoding was applied.")
        print("  parameter 0 is each device's own CAN id and is excluded from the "
              "drift count; it is the self-check that the reads are live and "
              "per-device rather than one cached answer.")
        print("  reads are non-destructive. Writes land in RAM only and are lost "
              "at the next power cycle unless burned, and nothing in this "
              "package burns.")
        return 0


def _params_show(r):
    """One parameter value, readable.

    The legacy reply carries a per-parameter type tag, so a float can be decoded
    rather than printed as its bit pattern. Tag 2 is float32 in THIS dialect and
    Uint in the 25+ one -- reading a legacy reply through the modern table turns
    every float into an integer and reports it as successful.
    """
    raw = r["raw"]
    if r.get("type") == "float32":
        import struct
        val = struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]
        return f"{val:g}"
    if r.get("type") == "bool":
        return "true" if raw else "false"
    if r.get("type") == "int32" and raw >= 0x80000000:
        return str(raw - 0x100000000)
    return str(raw)


def _params_write(adm, args, roles, modern=False):
    """One parameter, one controller, RAM only, refused where it must be."""
    if args.id is None or args.param is None:
        print("REFUSED: --set needs both --id and --param. A write is aimed at "
              "one controller and one parameter, never at the fleet.")
        return 2
    if modern and args.param in sa.PROTECTED_PARAMS:
        print(f"REFUSED: parameter {args.param} is protected: "
              f"{sa.PROTECTED_PARAMS[args.param]}")
        return 2
    if not modern:
        why = _params_refuse(args.param)
        if why:
            print(f"REFUSED: {why}")
            return 2
    if _refuse_on_the_example_config("params --set"):
        return 2
    if _refuse_on_a_live_experiment("params --set"):
        return 2

    if modern:
        raw = adm.read_param_value(args.id, args.param, wait=args.wait)
        if raw is None:
            print(f"REFUSED: id {args.id} did not answer a read of parameter "
                  f"{args.param}. Not writing to a controller that is not "
                  "answering normally.")
            return 1
        print(f"id {args.id} parameter {args.param}: {raw} -> {args.set}")
        w = adm.write_param(args.id, args.param, args.set)
        if w is None:
            print("no answer to the write")
            return 1
        if w["result"]:
            print(f"REFUSED by the controller: {w['result_text']}")
            return 1
        after = adm.read_param_value(args.id, args.param, wait=args.wait)
        print(f"  read back: {after if after is not None else 'no answer'}")
        if after != args.set:
            print(f"  REFUSING to report success: asked for {args.set}, the "
                  f"controller holds {after}. That is CD 456184's shape.")
            return 1
        print("\n  RAM ONLY until `uv run spark persist` commits it.")
        return 0

    before = adm.read_legacy_param(args.id, args.param, wait=args.wait)
    if before is None or not before["ok"]:
        print(f"REFUSED: id {args.id} did not answer a read of parameter "
              f"{args.param} ({before}). Not writing to a controller that is not "
              "answering normally.")
        return 1
    print(f"id {args.id} parameter {args.param}: {before['raw']} "
          f"({before['type']}) -> {args.set}")

    w = adm.write_legacy_param(args.id, args.param, args.set)
    if w is None:
        print("no answer to the write")
        return 1
    if not w["ok"]:
        print(f"REFUSED by the controller, status {w['status']}")
        return 1
    if not w["verified"]:
        print(f"  REFUSING to report success: asked for {args.set}, the device "
              f"echoed {w['raw']}. That is CD 456184's shape -- a setting that "
              "reports written and was not.")
        return 1
    after = adm.read_legacy_param(args.id, args.param, wait=args.wait)
    print(f"  read back: {after['raw'] if after else 'no answer'}")
    print(f"\n  RAM ONLY. This is lost at the next power cycle. Nothing in this "
          "package burns it to flash; the firmware's own burn is recorded in "
          "provenance as pre25.burn_flash_api and is deliberately not "
          "exposed here.")
    return 0


def cmd_canfix(args):
    """Clear a wedged USB CAN adapter without physically replugging it.

    Measured on rig-max: a driver-level unbind/bind of gs_usb destroys
    the netdev and recreates it, `99-sbot-can.rules` fires `sparkarm-can.service`
    on the re-add, and the bus comes back at 1 Mbit with both directions working.
    It is a complete substitute for pulling the cable, for every failure that is
    driver or adapter state rather than a dead USB device.

    It is NOT a substitute for one thing. When the kernel logs `unable to
    enumerate USB device`, the adapter's own USB stack has stopped answering and
    nothing the host can write to sysfs revives it -- the kernel has already
    tried a port power cycle by then. That case needs VBUS removed, which means
    the cable. This command says which of the two it is looking at rather than
    leaving an operator to guess.

    Why this exists at all: the driver bring-up path (`CheckCanID.check_can_interface`)
    already probes TX and rebinds a wedged dongle, and no `spark` subcommand ever
    called it -- `_open()` went straight to SparkAdmin. So every recovery this
    repo had was unreachable from the CLI.
    """
    iface = _can_interface()
    st = _adapter_state(iface)

    print(f"adapter: {iface}")
    print(f"  netdev present: {'yes' if st['exists'] else 'NO'}")
    if st["exists"]:
        print(f"  link: {'UP' if st['up'] else 'DOWN'}")
        print(f"  bitrate: {st['bitrate'] or '-'} bps")
        print(f"  can state: {st['can_state'] or '-'}")
        print(f"  usb parent: {st['parentdev'] or '- (not a USB adapter)'}")
        if st.get("bus_off") is not None:
            print(f"  bus-off count: {st['bus_off']}   restarts: {st.get('restarts')}")
        if st["restart_ms"] == "0":
            print("  restart-ms: 0 -- SocketCAN will NOT auto-recover from "
                  "bus-off on its own.")
            print("                   candleLight/CANable gs_usb rejects "
                  "restart-ms outright (`Device doesn't support restart from "
                  "Bus Off`, measured on rig-max), so on this adapter "
                  "a rebind is the only recovery and this command is it.")

    if not st["exists"]:
        print("\nthe adapter is not enumerated, so there is no driver to rebind.")
        print(f"{FIX}check the cable: `lsusb -d 1d50:606f`. If the kernel logged "
              "`unable to enumerate USB device`, the adapter's USB stack is "
              "wedged and only unplugging it removes VBUS and resets it.")
        return 1

    if st["parentdev"] is None:
        print("\nthis netdev has no USB parent, so it is not a gs_usb adapter "
              "and there is nothing to rebind.")
        return 1

    from.netdev import CanNetdev
    checker = CanNetdev(iface)

    healthy = st["up"] and checker._tx_probe_ok()
    if healthy and not args.force:
        print("\nthe adapter is UP and draining TX. Nothing to do.")
        print("  pass --force to rebind anyway.")
        return 0

    if healthy:
        print("\nthe adapter looks healthy; rebinding anyway because --force was given.")
    else:
        print("\nTX is not draining, or the link is down: rebinding the driver.")

    if not checker._reset_gs_usb_driver():
        print("\nFAILED to rebind the gs_usb driver.")
        print(f"{FIX}check the passwordless sudo grant with `sudo -n -l | grep "
              "gs_usb`. If the grant is present and the rebind still fails, the "
              "adapter has stopped answering USB and the cable is the only fix.")
        return 1

    # The udev add fires sparkarm-can.service, which sets the bitrate and brings
    # the link up. Wait for that rather than racing it.
    deadline = time.time() + 10.0
    while time.time() < deadline:
        after = _adapter_state(iface)
        if after["exists"] and after["up"]:
            break
        time.sleep(0.25)
    after = _adapter_state(iface)

    print(f"\nafter rebind: netdev {'present' if after['exists'] else 'ABSENT'}, "
          f"link {'UP' if after['up'] else 'DOWN'}, "
          f"bitrate {after['bitrate'] or '-'} bps")

    if not (after["exists"] and after["up"]):
        print(f"{FIX}the driver rebound but the link did not come up. Check "
              "`systemctl status sparkarm-can.service` and "
              "`journalctl -u sparkarm-can.service -n 20`.")
        return 1
    if not checker._tx_probe_ok():
        print(f"{FIX}the link is up and TX still will not drain. This is the "
              "case a rebind cannot fix: unplug the adapter and plug it back "
              "in, which is the only way to remove VBUS and reset it.")
        return 1

    print("\nOK -- the adapter is back and draining TX. `uv run spark status` "
          "to confirm the controllers answer.")
    return 0


def cmd_verify(args):
    """What this driver believes about the hardware, and what it has checked.

    Prints every claim the package rests on, how it was established, and for
    the unsettled ones the command that would settle it. Exits 1 when anything
    the selected product depends on is inferred or unverified, so a check that
    has never been run cannot pass silently.
    """
    from. import provenance as pv

    product = getattr(args, "product", None) or _base().controller_type
    print(f"provenance for controller_type={product}\n")
    for line in pv.report(product):
        print(line)

    gaps = pv.unsettled(product)
    print()
    if not gaps:
        print("every claim for this product is measured or vendor-stated.")
        return 0
    print(f"{len(gaps)} claim(s) this driver relies on WITHOUT having checked "
          "them on this product:")
    for c in gaps:
        print(f"  {c.key}")
    print("\n  These are not failures. They are the things that would be "
          "found out the hard way,")
    print("  and each one above carries the command that settles it. Run "
          "those on the robot")
    print("  before trusting anything downstream of them.")
    return 1


def cmd_persist(args):
    if _refuse_on_the_example_config("persist"):
        return 2
    if _refuse_on_a_live_experiment("persist"):
        return 2
    with _open() as adm:
        # Refuse BEFORE sending anything on a generation that does not carry the
        # frame. PERSIST_PARAMETERS is apiClass 63 index 15, versionImplemented
        # 25.0.0, so a 24.0.1 device drops it and adm.persist returns None. The
        # None branch below then measures the cadence against fault_frame's
        # expected_ms and, on a fleet already sitting at that value, reports "the
        # burn landed" and exits 0 -- a false success on a controller where
        # nothing was written and nothing could have been. Found by audit
        #; the same route is reachable through `spark repair
        # --persist` if the provisioned gate in cmd_repair is ever relaxed.
        gen = dominant_generation(collect_status(adm.bus, 1.0))
        if sa.normalise_generation(gen) == sa.GEN_PRE25:
            print(f"REFUSED: this bus reads {gen}, which does not carry "
                  "PERSIST_PARAMETERS (apiClass 63 index 15, versionImplemented "
                  "25.0.0). Sending it would be dropped in silence and this "
                  "command cannot tell that apart from a burn whose reply was "
                  "lost.")
            print(f"{FIX}pre-25 firmware has its own burn-flash at api 0x072, "
                  "arbitration id 0x02051C80 | id, carrying the magic 15011 "
                  "little-endian. It is measured to work and to commit the "
                  "parameter table (pre25.burn_flash_api), but this package "
                  "deliberately neither sends nor exposes it, and it does not "
                  "reach the status periods in any case. Use `uv run spark "
                  "throttle` for the periods, and REV Hardware Client over "
                  "USB-C for anything that must survive a power cycle.")
            return 2
        code = adm.persist(args.id)
        print(f"PERSIST_PARAMETERS id {args.id} -> "
              + ("no response" if code is None
                 else f"{'Success' if code == 0 else 'FAILED'} (code {code})"))
        if code == 0:
            return 0
        if code is None:
            # The controller stops answering for about two seconds after a burn
            # (CD 432129), so a lost PERSIST_RESP is not evidence of failure.
            # Reporting one sends the operator to burn again, spending a flash
            # cycle on a controller that was already correct. Ask the wire.
            ff = fault_frame(dominant_generation(
                collect_status(adm.bus, 1.0)))
            target = int(ff["expected_ms"])
            after = adm.status_period_ms(args.id, ff["api"],
                                         seconds=getattr(args, "window", 4.0))
            if after is not None and abs(after - target) <= max(2.0, target * 0.25):
                # Evidence, not proof. A controller broadcasting the declared
                # period may have been doing so before this command ran: the
                # cadence says the CONFIGURATION is right, not that it reached
                # flash. It is still the best available answer, because a repeat
                # burn spends a flash cycle to learn nothing.
                print(f"  the response was lost in the post-flash blackout, and "
                      f"the controller is broadcasting at {after} ms against a "
                      f"declared {target} ms, which is consistent with the burn "
                      "having landed. Not retrying, because a repeat burn spends "
                      "a flash cycle either way. Power cycle and re-check "
                      "`uv run spark status` if this has to be certain.")
                return 0
            print(f"  FIX: no response AND the wire reads {after} ms against a "
                  f"declared {target} ms, so the burn cannot be confirmed. "
                  "Power cycle the controller and check `uv run spark status` "
                  "before burning again -- a repeat burn costs a flash cycle.")
            return 1
        print(f"  FIX: nothing was burned to flash, so id {args.id} reverts on "
              "the next power cycle. Confirm it is on the bus with `uv run "
              "spark status` and that no second controller shares the id "
              "(`uv run spark duplicates`), then retry.")
        return 1


def cmd_repair(args):
    if _refuse_on_the_example_config("repair"):
        return 2
    if _refuse_on_a_live_experiment("repair"):
        return 2
    try:
        sa.require_motor_defaults_for(_use_declared_product())
    except (sa.MotorDefaultsWrongProduct, sa.MotorDefaultsMissing) as err:
        print(f"REFUSED: `spark repair` writes the declared configuration.\n\n  {err}\n")
        return 2
    with _open() as adm:
        window = min(getattr(args, "window", 4.0), 1.0)
        status = collect_status(adm.bus, window)
        gen = dominant_generation(status)
        # A parameter written into a moving motor is CD 346537, where a
        # firmware/API mismatch left controllers running while the robot was
        # disabled and e-stopped, nearly destroying an elevator. The reading is
        # already in hand here, so the check costs nothing and consumes nothing.
        driving = sa.driving_ids(status)
        if args.id in driving:
            print(f"refusing to repair id {args.id}: it is DRIVING, applied "
                  f"output {driving[args.id]:+.3f}.")
            print("  FIX: stop the mechanism and retry. Reconfiguring a moving "
                  "motor is CD 346537.")
            return 1
    ff = fault_frame(gen)
    if not ff["provisioned"]:
        print(f"refusing to repair id {args.id}: this bus reads as {gen}, and "
              f"no parameter id for the {ff['label']} Period is verified there.")
        print("  FIX: parameter 158 is the pre-25 Status 0 Period and no write "
              "to it has been checked here. Set the period in REV Hardware "
              "Client over USB-C, and settle both.status_period_unit -- "
              "`uv run spark verify` prints what would close it.")
        return 1
    with _open() as adm:
        # PARAMETER_WRITE is addressed by id, so on a duplicated id it reaches
        # both controllers and only the first response is read. The operator
        # would be reconfiguring a device they cannot see and did not choose.
        # The generation is passed because duplicates() otherwise resolves to
        # fw25+ and finds nothing on a pre-25 bus, where the tell is two
        # differing STATUS_0 payloads rather than a UNIQUE_ID broadcast.
        dups = adm.duplicates(seconds=args.window, generation=gen)
        if args.id in dups:
            print(f"refusing to repair id {args.id}: it is answered by "
                  f"{len(dups[args.id])} controllers ({', '.join(dups[args.id])})")
            print("  FIX: a write addressed by id reaches both of them and one "
                  "reply is discarded. Separate them first -- `uv run spark "
                  "identify --serial X` blinks one, `uv run spark set-id "
                  "--serial X --to N` moves it -- then repair.")
            return 1
        before = adm.status_period_ms(args.id, ff["api"], seconds=args.window)
        print(f"id {args.id}: {ff['label']} period before = {before} ms")
        target = int(declared_status_1_period_ms())
        print(f"  declared value from {sa.motor_defaults_file()}: {target} ms")
        # The at-rest reading above is two full listening windows old by now:
        # duplicates() and status_period_ms() each block for --window seconds,
        # about 10 s at the default. A motor that started moving in that gap is
        # what this re-check costs 50 ms to catch.
        try:
            adm.require_at_rest(args.id)
        except sa.MotorNotAtRestError as err:
            print(f"refusing to repair id {args.id}: {err}")
            return 1
        r = adm.write_param(args.id, PARAM_STATUS_1_PERIOD, target)
        if r is None:
            print("PARAMETER_WRITE got no response")
            print(f"  FIX: id {args.id} is not answering parameter requests. "
                  "Confirm it is on the bus with `uv run spark status`, and "
                  "that no second controller shares the id (`uv run spark "
                  "duplicates`) -- an addressed write reaches both and neither "
                  "reply is trustworthy.")
            return 1
        print(f"PARAMETER_WRITE param {r['param_id']} -> {r['result_text']} "
              f"(readback {r['value']}, {r['attempts']} attempt(s))")
        if r["result"] != 0:
            return 1
        if not r["verified"]:
            print(f"  REFUSING to go on: asked for {target}, the device echoed "
                  f"{r['value']} and still answered Success. CD 456184 is this "
                  "exact shape -- a setting that reports written and was not.")
            print("  FIX: re-run the repair. If the echo stays wrong, another "
                  "controller shares this id and both are answering: check "
                  "`uv run spark duplicates`.")
            return 1
        # Wait for the new cadence to reach the wire before measuring it. A
        # window that straddles the change averages the old period with the new
        # one and reports a number the controller never ran at.
        def took():
            now = adm.status_period_ms(args.id, ff["api"], seconds=0.3)
            return now is not None and abs(now - target) <= max(2.0, target * 0.25)

        deadline = time.time() + max(2.0, args.window)
        while time.time() < deadline and not took():
            pass

        after = adm.status_period_ms(args.id, ff["api"], seconds=args.window)
        print(f"id {args.id}: {ff['label']} period after  = {after} ms")
        if after is None or abs(after - target) > max(2.0, target * 0.25):
            print(f"  the write was accepted and the wire did not change: still "
                  f"{after} ms against a target of {target} ms. Nothing is "
                  "persisted, because flashing a value that did not take would "
                  "spend a flash cycle to record the failure.")
            print("  FIX: `uv run spark faults` first -- a controller in a "
                  "latched fault takes writes and applies nothing. Otherwise "
                  "check `uv run spark duplicates`.")
            return 1
        if args.persist:
            # Burn on the same observable, not on a clock.
            code = adm.persist(args.id, confirm=took)
            print("PERSIST_PARAMETERS -> "
                  + ("no response" if code is None
                     else f"{'Success' if code == 0 else 'FAILED'} (code {code})"))
            if code != 0:
                print("  FIX: the repair is in RAM only and reverts on the "
                      "next power cycle. Retry `uv run spark repair --id "
                      f"{args.id} --persist`; if it fails again, check `uv "
                      "run spark duplicates`.")
            return 0 if code == 0 else 1
        print("RAM only -- pass --persist to survive a power cycle")
    return 0


# The firmware's own name for a parameter type, per dialect, against the name the
# declared file uses.
_DECLARED_TYPE_NAMES = {"FLOAT": ("float", "float32"),
                        "BOOL": ("boolean", "bool"),
                        "UINT32": ("uint", "uint32"),
                        "INT32": ("int", "int32")}


def _type_disagrees(declared_type, wire_type):
    """The firmware's type for a parameter when it is not the file's, else None."""
    if wire_type is None:
        return None
    names = _DECLARED_TYPE_NAMES.get(str(declared_type or "").upper(), ())
    return None if str(wire_type).lower() in names else str(wire_type)


def _declared_show(value, param_id=None):
    """One declared or read value for a ledger column, enums by name."""
    members = sa.PARAM_ENUM_MEMBERS.get(sa.PARAM_ENUMS.get(param_id), ())
    if members and isinstance(value, int) and not isinstance(value, bool) \
            and 0 <= value < len(members):
        return f"{members[value]} ({value})"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _write_declared(adm, dev, pid, raw, pre25):
    """One declared setting in this generation's write dialect, flattened.

    Neither dialect's answer is evidence the value took: 26.1.6 answers Success
    and echoes 2 for a BOOL written 2, and stores the 2. The read back decides.
    """
    if pre25:
        w = adm.write_legacy_param(dev, pid, raw)
        return None if w is None else {
            "echo": w["raw"], "ok": bool(w["ok"]),
            "text": "Success" if w["ok"] else f"status {w['status']}"}
    w = adm.write_param(dev, pid, raw)
    return None if w is None else {
        "echo": w["value"], "ok": w["result"] == 0, "text": w["result_text"]}


def cmd_provision(args):
    """Restore every declared setting one controller has drifted on.

    `spark repair` puts back the one parameter whose value can be checked as a
    cadence. This is the command the audit's own remedy names: it reads the whole
    declared file off one controller, writes back what disagrees, and reads each
    write again afterwards, because the echo is not evidence.
    """
    if _refuse_on_the_example_config("provision"):
        return 2
    if _refuse_on_a_live_experiment("provision"):
        return 2
    product = _use_declared_product()
    try:
        sa.require_motor_defaults_for(product)
    except (sa.MotorDefaultsWrongProduct, sa.MotorDefaultsMissing) as err:
        print("REFUSED: `spark provision` writes the declared configuration."
              f"\n\n  {err}\n")
        return 2
    roles = _spark_roles()
    role = str(roles.get(args.id) or "").split("/")[0]
    if not role:
        print(f"REFUSED: id {args.id} is not in devices, so this robot "
              "declares no configuration for it.")
        print(f"{FIX}P 0 is declared per role, so provisioning an id with no "
              "role would write every common setting and silently skip the one "
              "gain that differs between a steer motor and a drive motor.")
        return 2
    settings = motor_settings(role)
    if not settings:
        print(f"REFUSED: {sa.motor_defaults_file(product)} declares nothing for "
              f"role {role}, so there is nothing to restore.")
        return 2

    with _open() as adm:
        window = min(getattr(args, "window", 4.0), 1.0)
        status = collect_status(adm.bus, window)
        gen = dominant_generation(status)
        # A parameter written into a moving motor is CD 346537.
        driving = sa.driving_ids(status)
        if args.id in driving:
            print(f"refusing to provision id {args.id}: it is DRIVING, applied "
                  f"output {driving[args.id]:+.3f}.")
            print("  FIX: stop the mechanism and retry. Reconfiguring a moving "
                  "motor is CD 346537.")
            return 1
        # The dialect is chosen from the wire and a silent controller offers no
        # wire. normalise_generation(None) resolves to fw25+, so going on here
        # would pick a write dialect by default. PARAMETER_WRITE is
        # versionImplemented 25.0.0 and a 24.0.1 device drops it in silence,
        # which reads exactly like a dead controller.
        if args.id not in status:
            print(f"refusing to provision id {args.id}: it broadcast nothing in "
                  f"{window:.1f} s, so nothing here knows its generation.")
            print(f"{FIX}`uv run spark clear` wakes a bus a latched fault has "
                  "silenced, and `uv run spark status` shows what came back.")
            return 1
        # This command writes to ONE controller, so the dialect comes from that
        # controller's own reading and not from the bus-wide majority. A single
        # odd controller on a fleet of eight is exactly the case a majority hides.
        target_gen = status[args.id].get("generation") or gen
        if sa.normalise_generation(target_gen) != sa.normalise_generation(gen):
            print(f"note: id {args.id} reads {target_gen} while the bus reads "
                  f"{gen}. Writing in this controller's own dialect.\n")
        gen = target_gen
        # Frames come from the wire and VALUES come from the product's file. When
        # those disagree this would push one product's numbers through the
        # other's frames.
        # A warning, not a refusal, the same as in cmd_status and cmd_audit. The
        # VALUES come from the product's file, which require_motor_defaults_for
        # already checked, and the DIALECT comes from the wire. A MAX updated to
        # 25.0.0 trips this and is still provisioned correctly.
        broken = sa.unexpected_generation(product, sa.observed_generation(status))
        if broken:
            print(f"FLEET ASSUMPTION BROKEN: {broken}\n")
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        if pre25 and args.persist:
            print(f"REFUSED: --persist on a bus that reads {gen}. "
                  "PERSIST_PARAMETERS is apiClass 63 index 15, versionImplemented "
                  "25.0.0, so the frame is dropped in silence and a burn that "
                  "never happened reads exactly like one whose reply was lost.")
            print(f"{FIX}drop --persist. Every write below stays in RAM until "
                  "the next power cycle takes it back.")
            return 2
        # PARAMETER_WRITE is addressed by id, so on a duplicated id it reaches
        # both controllers and only the first response is read.
        # With no generation this resolves to fw25+ and finds no duplicate on a
        # pre-25 bus, where the tell is a pair of differing STATUS_0 payloads
        # rather than a UNIQUE_ID broadcast. The generation is in hand here.
        dups = adm.duplicates(seconds=args.window, generation=gen)
        if args.id in dups:
            print(f"refusing to provision id {args.id}: it is answered by "
                  f"{len(dups[args.id])} controllers ({', '.join(dups[args.id])})")
            print("  FIX: separate them first -- `uv run spark identify "
                  "--serial X` blinks one, `uv run spark set-id --serial X --to "
                  "N` moves it -- then provision.")
            return 1

        rows = sa.declared_rows(adm, args.id, role, wait=args.wait,
                                pre25=pre25, settings=settings)
        dialect = "api class 48" if pre25 else "READ_PARAMETER"
        print(f"id {args.id} ({roles[args.id]}) -- {len(rows)} declared "
              f"setting(s) from {sa.motor_defaults_file(product)}, read over "
              f"{dialect}\n")
        print(f"  {'id':>4}  {'setting':<30} {'declared':<18} {'holds':<18} state")
        for r in rows:
            held = (_declared_show(r["actual"], r["param_id"])
                    if r["state"] in ("ok", "drift") else "-")
            print(f"  {r['param_id']:>4}  {r['name']:<30} "
                  f"{_declared_show(r['declared'], r['param_id']):<18} "
                  f"{held:<18} {r['state']}")

        stranded = [r for r in rows if r["state"] == "unaddressable"]
        if stranded:
            print(f"\n  {len(stranded)} declared setting(s) are out of reach on "
                  "this generation and were neither read nor written:")
            for r in stranded:
                print(f"    {r['param_id']:>4}  {r['name']}")
            print("  the status periods are not parameters here and move on "
                  "LEGACY_SET_PERIOD; `uv run spark throttle` re-sends every one "
                  f"and measures the cadence back. Ids above {sa.LEGACY_PARAM_MAX} "
                  "are not addressable at all.")

        unread = [r for r in rows if r["state"] in ("silent", "refused")]
        if unread and not pre25:
            print(f"\nrefusing to provision id {args.id}: {len(unread)} declared "
                  "setting(s) did not read back, and every parameter 0-255 "
                  "answers on this generation.")
            print(f"{FIX}a silent parameter is the controller and not the range. "
                  "Nothing was written: a run that cannot read cannot verify "
                  "what it wrote.")
            return 1

        blocked = [r for r in rows if r["state"] == "drift"
                   and r["param_id"] in sa.PROTECTED_PARAMS]
        if blocked:
            print(f"\nrefusing to provision id {args.id}: {len(blocked)} "
                  "protected parameter(s) have drifted.")
            for r in blocked:
                print(f"  - {r['name']} (parameter {r['param_id']}) reads "
                      f"{_declared_show(r['actual'], r['param_id'])}, declared "
                      f"{_declared_show(r['declared'], r['param_id'])} -- "
                      f"{sa.PROTECTED_PARAMS[r['param_id']]}")
            print(f"{FIX}write_param and write_legacy_param both refuse these "
                  "and no flag here overrides that: 50-53 are the data-port "
                  "hard-limit interlock and 2 is Motor Type, which is CD 424550 "
                  "-- a controller that lights up, answers, and will not turn. "
                  "Put them back in REV Hardware Client 2 over USB-C, then "
                  "re-run this. The rest of the run stops with them, because a "
                  "controller this tooling cannot fully restore must not be "
                  "burned holding a configuration nobody declared.")
            return 1

        drifted = [r for r in rows if r["state"] == "drift"]
        types = {}
        if not pre25:
            for start in sorted({r["param_id"] // 16 * 16 for r in drifted}):
                types.update(adm.param_types(args.id, start_id=start,
                                             wait=args.wait) or {})
        plan, skipped = [], []
        for r in drifted:
            pid = r["param_id"]
            wire = r["wire_type"] if pre25 else types.get(pid)
            if str(wire).lower() == "unused":
                skipped.append((r, "the firmware reports this id Unused, so this "
                                   "build does not implement it"))
                continue
            disagrees = _type_disagrees(r["type"], wire)
            if disagrees:
                skipped.append((r, f"the file types it {r['type']} and the "
                                   f"firmware reports {disagrees}"))
                continue
            raw = sa.declared_raw_value(r["declared"], r["type"], pid)
            if raw is None:
                skipped.append((r, f"the declared value {r['declared']!r} does "
                                   f"not encode as {r['type']}"))
                continue
            plan.append(dict(r, raw_write=raw))

        if skipped:
            print(f"\n  {len(skipped)} drifted setting(s) will NOT be written:")
            for r, why in skipped:
                print(f"    {r['param_id']:>4}  {r['name']}: {why}")
            print(f"{FIX}each of these is a disagreement between "
                  f"{sa.motor_defaults_file(product)} and the firmware, not a "
                  "controller fault. Writing a value whose type the device does "
                  "not share puts the wrong bits in and decodes the read back "
                  "wrong, so both halves look clean.")

        if not plan:
            print(f"\nnothing to write: id {args.id} holds the declared value "
                  "for every setting this generation can reach.")
            return 1 if skipped else 0
        if not args.write:
            print(f"\ndry run -- {len(plan)} setting(s) would be written to id "
                  f"{args.id}. Pass --write to send them.")
            return 1

        # The survey above is seconds old by now. A motor that started moving
        # since then is the one case it cannot cover.
        try:
            adm.require_at_rest(args.id)
        except sa.MotorNotAtRestError as err:
            print(f"\nrefusing to provision id {args.id}: {err}")
            return 1

        print(f"\nwriting {len(plan)} setting(s) to id {args.id}\n")
        print(f"  {'id':>4}  {'setting':<30} {'asked':<14} {'echo':<12} "
              f"{'reads':<18} verdict")
        landed, stopped = [], None
        for r in plan:
            pid = r["param_id"]
            w = _write_declared(adm, args.id, pid, r["raw_write"], pre25)
            # Through the same reader the audit uses, so a write is judged by the
            # comparison that will judge it again tomorrow.
            back = sa.declared_rows(adm, args.id, role, wait=args.wait,
                                    pre25=pre25, settings={pid: settings[pid]})[0]
            if w is None:
                verdict = "NO ANSWER"
            elif not w["ok"]:
                verdict = f"REFUSED ({w['text']})"
            elif back["state"] != "ok":
                verdict = "NOT HELD"
            else:
                verdict = "landed"
            reads = (_declared_show(back["actual"], pid)
                     if back["state"] in ("ok", "drift") else back["state"])
            print(f"  {pid:>4}  {r['name']:<30} "
                  f"{_declared_show(r['declared'], pid):<14} "
                  f"{('-' if w is None else str(w['echo'])):<12} "
                  f"{reads:<18} {verdict}")
            if verdict != "landed":
                stopped = (r, verdict, w)
                break
            landed.append(r)

        if stopped:
            r, verdict, w = stopped
            print(f"\nSTOPPED at parameter {r['param_id']} ({r['name']}): "
                  f"{verdict}.")
            print(f"  {len(landed)} setting(s) landed and "
                  f"{len(plan) - len(landed) - 1} were never sent, so id "
                  f"{args.id} holds a configuration that is neither what it had "
                  "nor what the file declares.")
            if w is not None and w["ok"]:
                print(f"  the controller answered {w['text']} and echoed "
                      f"{w['echo']}, and the read back disagrees. That is "
                      "CD 456184's shape -- a setting that reports written and "
                      "was not -- and it is why the echo is not the check here.")
            print(f"{FIX}nothing was burned, so a power cycle puts this "
                  "controller back where it started. Check `uv run spark faults` "
                  "first: a controller in a latched fault takes writes and "
                  "applies nothing. Then re-run this command.")
            return 1

        if skipped:
            print(f"\n{len(landed)} setting(s) landed, and {len(skipped)} "
                  "drifted setting(s) could not be written. NOT burning: a "
                  "controller this tooling cannot fully restore must not be "
                  "made permanent holding a configuration nobody declared.")
            return 1
        if not args.persist:
            print(f"\n{len(landed)} setting(s) landed. RAM only -- pass "
                  "--persist to survive a power cycle.")
            return 0

        # One burn for the whole run. Flash endurance is finite (CD 455171).
        # Burned on the observable: the last write is the one closest to the
        # flash and the one CD 432129 puts at risk.
        last = landed[-1]["param_id"]

        def held():
            r = sa.declared_rows(adm, args.id, role, wait=args.wait,
                                 pre25=pre25, settings={last: settings[last]})[0]
            return r["state"] == "ok"

        code = adm.persist(args.id, confirm=held)
        print("\nPERSIST_PARAMETERS -> "
              + ("no response" if code is None
                 else f"{'Success' if code == 0 else 'FAILED'} (code {code})"))
        if code == 0:
            return 0
        if code is None:
            print("  the controller stops answering for about two seconds after "
                  "a burn (CD 432129), so a lost reply is not evidence of "
                  "failure. Power cycle it and re-run this command WITHOUT "
                  "--write: a clean read is the proof.")
            return 1
        print(f"{FIX}the {len(landed)} setting(s) above are in RAM only and "
              "revert on the next power cycle. Re-run `uv run spark provision "
              f"--id {args.id} --write --persist`.")
        return 1


def cmd_faults(args):
    roles = _spark_roles()
    with _open() as adm:
        st = collect_status(adm.bus, args.window)
    # The remedy for a bit is not the same on both generations, so the advice
    # under the table has to know which bus this is. Read off the wire, not from
    # controller_type: a MAX updated to 25.0.0 is written like a Flex.
    gen = dominant_generation(st)
    if not st:
        print("no controllers broadcasting -- run `spark clear`")
        return 1
    bad = 0
    seen_bits, any_limit, any_unreadable = set(), False, False
    print(f"  {'id':>3}  {'role':<10} {'volts':>6} {'amps':>6} {'temp':>5}  errors")
    for dev, v in st.items():
        n = sa.normalised_reading(v)
        s0 = n if n.get("has_telemetry") else None
        s1 = n if v.get("status1") or n.get("faults") or n.get("sticky_faults") else None
        unreadable = bool(s0 and s0.get("implausible"))
        if unreadable:
            cells = (f"  {dev:>3}  {roles.get(dev, '-'):<10} "
                     f"{'--':>6} {'--':>6} {'--':>5}")
        else:
            cells = (f"  {dev:>3}  {roles.get(dev, '-'):<10} "
                     f"{s0['voltage_v']:>6.2f} {s0['current_a']:>6.2f} "
                     f"{s0['motor_temp_c']:>4}C" if s0 else f"  {dev:>3}  {roles.get(dev,'-'):<10}")
        limits = []
        if s0 and not unreadable:
            for key, tag in (("hard_forward_limit", "HARD-FWD"),
                             ("hard_reverse_limit", "HARD-REV"),
                             ("soft_forward_limit", "soft-fwd"),
                             ("soft_reverse_limit", "soft-rev")):
                if s0.get(key):
                    limits.append(tag)
        limit_txt = ("  LIMIT " + ",".join(limits)) if limits else ""
        any_limit = any_limit or bool(limits)
        if unreadable:
            print(cells + "  UNREADABLE: " + sa.describe_implausible(s0["implausible"]))
            any_unreadable = True
            bad += 1
            continue
        if s1 is None:
            print(cells + limit_txt
                  + "  no STATUS_1 -- this controller reports no errors at all")
            bad += 1
            continue
        parts = []
        for key, tag in (("faults", "FAULT"), ("warnings", "warn"),
                         ("sticky_faults", "sticky-FAULT"),
                         ("sticky_warnings", "sticky-warn")):
            if s1.get(key):
                parts.append(f"{tag}: {','.join(s1[key])}")
                seen_bits.update(s1[key])
        if s1.get("faults") or s1.get("sticky_faults"):
            bad += 1
        print(cells + limit_txt + "  "
              + ("; ".join(parts) if parts else ("interlock" if limits else "clean")))
    if not seen_bits and not any_limit and not any_unreadable:
        return 0

    print()
    print("  what to do")
    if any_unreadable:
        print("    UNREADABLE -- the controller is powered and still on the bus, "
              "and what it broadcasts is")
        print("    no longer a reading. Nothing in that frame can be trusted, the "
              "limit bits included. Cut")
        print("    and restore motor power, then `uv run spark faults` again: the "
              "sticky bits survive and")
        print("    say what preceded it.")
    if any_limit:
        declared = bool(getattr(_base(), "limit_switch_polarity", False))
        print("    LIMIT HARD-FWD / HARD-REV -- a data-port limit switch reads as "
              "asserted, which is a safety")
        print("    interlock. It is DIRECTIONAL: one bit withholds output that way "
              "only and the motor still")
        print("    runs the other way at full commanded duty. Both bits together "
              "withhold everything. No command")
        print("    here can disable it, because params 50-53 are write-protected. "
              "This robot declares")
        print(f"    limit_switch_polarity = {declared}. If no switch is actually "
              "wired to that port, the")
        print("    parameter has drifted and the wheel is held by a switch that does "
              "not exist. Confirm and repair:")
        print("        uv run python tools/spark_limit_polarity_repair.py --repair "
              "--persist")
    remedies = dict(sa.BIT_REMEDIES)
    if sa.normalise_generation(gen) == sa.GEN_PRE25:
        remedies.update(sa.LEGACY_BIT_REMEDIES)
    for bit in sorted(seen_bits):
        print(f"    {_wrap_remedy(bit, remedies.get(bit))}")
    print("    sticky bits are history, not the present. Read them before")
    print("      `uv run spark clear` erases them.")
    return 1 if bad else 0


def _wrap_remedy(bit, text, width=74, indent=" " * 6):
    """One remedy, wrapped under its bit name so long advice stays readable."""
    if not text:
        return f"{bit} -- no remedy recorded for this bit yet"
    body = textwrap.wrap(f"{bit} -- {text}.", width=width)
    return ("\n" + indent).join(body)


def cmd_voltage(args):
    """Motor-rail voltage measured at each controller, plus the pack SoC.

    This is a different measurement from battery.py: the Anker reports the power
    station's state of charge, while STATUS_0 reports the rail voltage the SPARKs
    actually see, downstream of breakers and wiring. A controller can brown out
    while the pack still reads healthy, so the two belong side by side.
    """
    roles = _spark_roles()
    with _open() as adm:
        st = collect_status(adm.bus, args.window)
    readings = dict((d, sa.normalised_reading(v)) for d, v in st.items())
    volts = {d: n["voltage_v"] for d, n in readings.items()
             if n.get("voltage_v") is not None and not n.get("implausible")}
    if not volts:
        print("no controllers broadcasting -- run `spark clear`")
        return 1

    print(f"  {'id':>3}  {'role':<10} {'volts':>7} {'amps':>6} {'temp':>5}  brownout")
    flagged = 0
    for dev in sorted(st):
        n = readings[dev]
        s0 = n if n.get("voltage_v") is not None else None
        s1 = n
        if not s0:
            continue
        if s0.get("implausible"):
            flagged += 1
            print(f"  {dev:>3}  {roles.get(dev, '-'):<10} {'--':>7} {'--':>6} "
                  f"{'--':>5}  UNREADABLE: "
                  f"{sa.describe_implausible(s0['implausible'])}")
            continue
        bo = []
        if s1:
            if "brownout" in (s1.get("warnings") or []):
                bo.append("ACTIVE")
            if "brownout" in (s1.get("sticky_warnings") or []):
                bo.append("sticky")
            if "hasReset" in (s1.get("sticky_warnings") or []):
                bo.append("has-reset")
        if bo:
            flagged += 1
        print(f"  {dev:>3}  {roles.get(dev, '-'):<10} {s0['voltage_v']:>6.2f}V "
              f"{s0['current_a']:>5.2f}A {s0['motor_temp_c']:>4}C  "
              f"{','.join(bo) if bo else '-'}")

    # The rail is measured at the controllers themselves, so this needs nothing
    # outside sparklib. A pack monitor is a separate instrument and is optional;
    # register_power_provider installs one.
    from.rail import (classify_power, get_rail_status, rail_endpoints,
                       rail_implausible, rail_usable_pct)
    rail = get_rail_status(channel=_can_interface(), window=args.window)
    rail_cfg = getattr(_base(), "rail", None)
    level = pct = None
    if rail is not None:
        full_v, empty_v = rail_endpoints(rail_cfg)
        bad = rail_implausible(rail.mean_v, full_v, empty_v)
        if bad:
            print(f"  WARNING: {bad}")
        else:
            pct = rail_usable_pct(rail.mean_v, full_v, empty_v)
            level = classify_power(pct)
        print(f"\n  rail: mean {rail.mean_v:.2f} V   "
              f"(min {rail.min_v:.2f}  max {rail.max_v:.2f}  "
              f"spread {rail.spread_v:.2f})")
        if rail.spread_v > 0.5:
            print("  spread over 0.5 V suggests wiring or connector resistance "
                  "to the low controller")
    chem = getattr(rail_cfg, "chemistry", None)
    print(f"  chemistry: {chem or 'not set under rail: -- voltage reported, not graded'}")
    if level is not None:
        print(f"  rail level: {level.value.upper()}  ({pct:.0f}% of usable range)")
    pack = _pack_status()
    if pack is not None:
        print(f"  pack SoC: {pack.percent:.0f}%   "
              "(separate source from the motor rail)")

    if flagged:
        print(f"\n{flagged} controller(s) report brownout or reset history -- this "
              "is the mechanism behind config loss.")
        return 1
    return 0


def cmd_audit(args):
    _use_declared_product()
    # See EXPECTED_GENERATION in admin.py. Checked here as well as in
    # `status` because audit is what an operator runs when something is already
    # wrong, and a reflashed controller would make several of its rules read
    # against the wrong generation's expectations.
    roles, known = _spark_roles(), _known_serials()
    baseline, bpath = _load_baseline()
    if baseline:
        print(f"baseline: {os.path.basename(bpath)} "
              f"(captured {baseline.get('meta', {}).get('captured_utc', '?')})\n")
    else:
        print(f"note: no baseline for {_base_index()} -- run `spark snapshot --write` "
              "on a known-good bus\n")
    with _open() as adm:
        gen_now = dominant_generation(collect_status(adm.bus, min(args.window, 1.0)))
        pre25 = sa.normalise_generation(gen_now) == sa.GEN_PRE25
        # Ask for the identity on a generation that broadcasts none, or the
        # swapped-controller check has nothing to compare and the audit reports
        # that it cannot check identity while the answer is one request away.
        inv = adm.inventory(args.window, with_fingerprint=pre25)
        dups = adm.duplicates(args.window, generation=gen_now) if pre25 \
            else adm.duplicates(args.window)
        status = collect_status(adm.bus, args.window)
        broken = sa.unexpected_generation(
            getattr(_base(), "controller_type", None),
            sa.observed_generation(status))
        if broken:
            print(f"FLEET ASSUMPTION BROKEN: {broken}\n")
        if baseline:
            for dev in inv:
                inv[dev]["firmware"] = adm.firmware(dev)[0]
        # Both generations answer a parameter read, in different dialects: pre-25
        # over the legacy api for parameters 0-133, and 25+ over READ_PARAMETER
        # for all of 0-255. So every declared deviation is compared here.
        param_problems, checked_params = ([], set())
        _devs = {d: roles[d] for d in inv if d in roles}
        param_notes = []
        if _devs:
            reader = (sa.legacy_deviation_problems if pre25
                      else sa.modern_deviation_problems)
            param_problems, checked_params = reader(adm, _devs)
            # NOTES, never findings. These ids are undeclared, so the baseline
            # records what a good bus held and nothing says what it must hold.
            param_notes = sa.baseline_param_problems(adm, _devs, baseline,
                                                     pre25=pre25)

    problems = audit_problems(inv, dups, roles, known,
                              (baseline or {}).get("controllers"),
                              status=status,
                              controller_type=_base().controller_type,
                              generation=dominant_generation(status))
    problems += param_problems
    if param_notes:
        print(f"note: {len(param_notes)} undeclared parameter(s) differ from the "
              "baseline. Nothing declares them, so these do not fail the audit:")
        for n in param_notes:
            print(f"  - {n}")
        print()
    if not known:
        if not sa.duplicate_detection_available(dominant_generation(status)):
            # learn-serials cannot help here: Unique ID Broadcast is apiClass 47,
            # versionImplemented 25.0.0, so this firmware emits no serial at all.
            print("note: no serials on this generation -- a swapped controller is "
                  "invisible over CAN here, and `spark learn-serials` cannot "
                  "change that. Identity has to come from the parameter table "
                  "or from RHC2 over USB-C.\n")
        else:
            print("note: serials is not set -- run `spark learn-serials "
                  "--write` so swapped controllers can be detected\n")
    if problems:
        print(f"{len(problems)} problem(s):")
        fixes = []
        for p in problems:
            finding, _, fix = p.partition(FIX)
            print(f"  - {finding}")
            if fix and fix not in fixes:
                fixes.append(fix)
        if fixes:
            print("\n  what to do")
            for fix in fixes:
                print("    " + "\n      ".join(textwrap.wrap(fix, width=74)))
    else:
        print(f"no problems found across {len(inv)} controller(s).")
    print("\ncoverage: " + coverage_note(len(inv), dominant_generation(status),
                                          checked_params))
    return 1 if problems else 0


# -- learn-serials ----------------------------------------------------------

def _render_serials_block(roles_by_group, indent="  ", pre25=False):
    """The YAML block to paste under `base:`.

    The comment names where the value came from, because the two generations
    read identity from different frames and a reader six months from now should
    not have to work out which fleet this was written on.
    """
    source = ("fingerprints, api 0x094 (pre-25 broadcasts no serial)" if pre25
              else "hardware serials, api 0x2F0")
    lines = [f"{indent}{_serials_key()}:                               "
             f"# {source}. Written by `spark learn-serials`."]
    declared = vars(_base().devices)
    for group in declared:
        if group not in roles_by_group:
            continue
        order = {label: i for i, label in enumerate(vars(declared[group]))}
        corners = sorted(roles_by_group[group], key=lambda cs: order.get(cs[0], 99))
        pairs = ", ".join(f"{c}: '{s}'" for c, s in corners)
        lines.append(f"{indent}  {group}:    {{{pairs}}}")
    return "\n".join(lines) + "\n"


def _serials_key():
    return str(getattr(_base(), "serials_key", None) or "serials")


def _devices_key():
    return str(getattr(_base(), "devices_key", None) or "devices")


def _insert_block(text, block):
    """Replace an existing serials block, or insert one after the devices block.

    Both key names come from the config, so a host application whose file calls
    them something else is still written in the right place.
    """
    lines = text.splitlines(keepends=True)
    out, i, done = [], 0, False
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith(_serials_key() + ":"):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                i += 1
            out.append(block)
            done = True
            continue
        out.append(line)
        if not done and stripped.startswith(_devices_key() + ":"):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                out.append(nxt)
                i += 1
            out.append(block)
            done = True
            continue
        i += 1
    return "".join(out), done


def cmd_learn_serials(args):
    """Record each controller's identity into serials.

    Works on both generations. On 25+ the serial arrives on its
    own in UNIQUE_ID and is read passively. Pre-25 broadcasts none, so the
    identity is REQUESTED at LEGACY_FINGERPRINT_API instead -- see
    pre25.serial_is_readable_at_api_0x094. This command used to exit 1 on that
    generation with advice to listen longer, which was advice for a frame that
    was never going to arrive.

    The recorded value is what makes a SWAPPED controller visible: an id that
    answers with a different identity than the config expects is a controller
    that was replaced or moved, which no id-based check can see.
    """
    roles = _spark_roles()
    with _open() as adm:
        gen = dominant_generation(collect_status(adm.bus, min(args.window, 1.0)))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        inv = adm.inventory(args.window, with_fingerprint=pre25)
    missing = sorted(set(roles) - set(inv))
    if missing:
        print("cannot record serials -- these configured ids are not broadcasting:")
        for d in missing:
            print(f"  {d} ({roles[d]})")
        print("\nrun `uv run spark clear` first, then retry.")
        return 1

    by_group = {}
    print("serials read from the bus:\n")
    for dev in sorted(roles):
        group, corner = roles[dev].split("/")
        serial = inv[dev]["serial"]
        if not serial:
            if pre25:
                print(f"  id {dev} ({roles[dev]}): no answer to the fingerprint "
                      f"request at api 0x{sa.LEGACY_FINGERPRINT_API:03X}.")
                print("    This generation broadcasts no identity, so listening "
                      "longer will not help. The controller answered the "
                      "inventory and did not answer this, which is worth "
                      "investigating on its own.")
            else:
                print(f"  id {dev} ({roles[dev]}): no serial seen -- listen "
                      "longer with --window 10")
            return 1
        by_group.setdefault(group, []).append((corner, serial))
        print(f"  id {dev:>3}  {roles[dev]:<10} {serial}")

    block = _render_serials_block(by_group, pre25=pre25)
    if pre25:
        print("\n  these are FINGERPRINTS read at api 0x094, not REV serials. "
              "They are unique, stable and read-only, which is what identity "
              "needs; REV Hardware Client does not show them and pre-25 identify "
              "does not take one.")
    print("\nblock to record in the config:\n")
    print(block)

    if not args.write:
        print("dry run -- pass --write to update the config file")
        return 0

    path = args.config or cfg.config_path()
    if not path or not os.path.exists(path):
        print(f"config file not found ({path}); pass --config PATH")
        return 1
    original = open(path).read()
    updated, ok = _insert_block(original, block)
    if not ok:
        print(f"could not find a {_devices_key()}: block to anchor to; "
              "nothing written")
        print(f"  FIX: {os.path.basename(path)} has no `{_devices_key()}:` key. "
              "Add one, or paste the block above in by hand -- this command "
              "only ever appends next to an existing anchor.")
        return 1
    backup = path + ".bak"
    with open(backup, "w") as fh:
        fh.write(original)
    with open(path, "w") as fh:
        fh.write(updated)
    print(f"wrote {path}  (previous copy kept at {backup})")
    return 0


def main(argv=None):
    try:
        cfg.load_host()
    except RuntimeError as err:
        print(f"REFUSED: {err}")
        return 2
    # Before any handler runs, so a command that reads a declared value reads
    # this robot's product and not the module default.
    _use_declared_product()
    p = argparse.ArgumentParser(prog="spark", description=__doc__.splitlines()[0])
    p.add_argument("--window", type=float, default=5.0,
                   help="seconds to listen when sampling the bus (default 5)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="what is on the bus, with serials and firmware")
    sub.add_parser("clear", help="clear latched faults; wakes a silent bus")
    sub.add_parser("throttle",
                   help="re-send the pre-25 status periods lost on every power cycle")
    sub.add_parser("audit", help="config drift + duplicate ids; exit 1 on any fault")
    sub.add_parser("duplicates", help="ids answered by more than one controller")
    p_pa = sub.add_parser("params",
                          help="read or write the parameter table, either dialect")
    p_pa.add_argument("--id", type=int, default=None, help="one controller")
    p_pa.add_argument("--param", type=int, default=None, help="one parameter id")
    p_pa.add_argument("--set", type=int, default=None,
                      help="write this raw value (needs --id and --param)")
    p_pa.add_argument("--all", action="store_true",
                      help="show every parameter, not only those that differ")
    p_pa.add_argument("--wait", type=float, default=0.4,
                      help="seconds to wait for each reply (default 0.4)")

    p_cf = sub.add_parser("canfix",
                          help="clear a wedged USB CAN adapter without replugging it")
    p_cf.add_argument("--force", action="store_true",
                      help="rebind even when the adapter looks healthy")
    sub.add_parser("faults", help="decoded faults, warnings and telemetry per controller")
    sub.add_parser("voltage", help="motor-rail voltage per controller + pack SoC")
    p_d = sub.add_parser("defaults",
                         help="the declared configuration every motor is provisioned to")
    p_d.add_argument("--role", choices=["steer", "drive"], default=None)
    p_d.add_argument("--all", action="store_true",
                     help="show all settings, not just those that deviate")

    p_i = sub.add_parser("identify", help="blink one controller's LED")
    # Neither is required: which one applies depends on the generation on the
    # wire, and the command says so rather than failing in argparse with advice
    # that is wrong for half the fleet.
    p_i.add_argument("--serial", default=None,
                     help="firmware 25+: the controller's serial")
    p_i.add_argument("--id", type=int, default=None,
                     help="pre-25: the controller's CAN id")

    p_s = sub.add_parser("set-id", help="reassign a CAN id, addressed by serial")
    p_s.add_argument("--serial", required=True)
    p_s.add_argument("--to", type=int, required=True)

    p_v = sub.add_parser(
        "verify", help="what this driver believes about the hardware, and "
                       "which beliefs have never been checked")
    p_v.add_argument("--product", choices=("sparkmax", "sparkflex"),
                     help="default: the active config's controller_type")

    p_p = sub.add_parser("persist", help="commit a controller's RAM config to flash")
    p_p.add_argument("--id", type=int, required=True)

    p_r = sub.add_parser("repair",
                         help="restore Status 1 Period from the declared config")
    p_r.add_argument("--id", type=int, required=True)
    p_r.add_argument("--persist", action="store_true")

    p_pr = sub.add_parser("provision",
                          help="restore every declared setting one controller "
                               "has drifted on")
    p_pr.add_argument("--id", type=int, required=True)
    p_pr.add_argument("--write", action="store_true",
                      help="send the writes; without it this only reports drift")
    p_pr.add_argument("--persist", action="store_true",
                      help="burn once, after every write has read back correct")
    p_pr.add_argument("--wait", type=float, default=0.4,
                      help="seconds to wait for each parameter reply (default 0.4)")

    p_sn = sub.add_parser("snapshot",
                          help="capture this bus as the baseline for this base index")
    p_sn.add_argument("--write", action="store_true")
    p_sn.add_argument("--no-parameters", dest="parameters", action="store_false",
                      help="capture identity and cadence only, no parameter table")
    p_sn.add_argument("--wait", type=float, default=0.4,
                      help="seconds to wait for each parameter reply (default 0.4)")
    p_l = sub.add_parser("learn-serials",
                         help="record hardware serials into the base config (one-time)")
    p_l.add_argument("--write", action="store_true")
    p_l.add_argument("--config", default=None, help="config file to update")

    args = p.parse_args(argv)
    handlers = {"status": cmd_status, "clear": cmd_clear, "audit": cmd_audit,
                "throttle": cmd_throttle,
                "duplicates": cmd_duplicates, "identify": cmd_identify,
                "faults": cmd_faults, "voltage": cmd_voltage,
                "defaults": cmd_defaults,
                "set-id": cmd_set_id, "verify": cmd_verify,
        "persist": cmd_persist, "repair": cmd_repair,
                "provision": cmd_provision,
                "learn-serials": cmd_learn_serials,
                "snapshot": cmd_snapshot, "canfix": cmd_canfix,
                "params": cmd_params}
    if args.cmd not in handlers:
        p.print_help()
        return 2
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
