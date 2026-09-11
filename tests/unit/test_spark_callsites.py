"""Call-site guards for the SPARK write path.

A behavioural test exercises a function; it cannot see a caller that reaches
past it. Every check here is about a bypass rather than a bug: writing a
parameter without going through the protected-parameter guard, or building a
frame from a literal that silently addresses the wrong thing.

The literal check is not hypothetical. `set_periodic_frame_period` hardcoded the
SparkMax STATUS base and was therefore a no-op on every SparkFlex -- no error,
no log line, just a frame nothing acted on.
"""

import ast
import re
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "sparklib"
ADMIN = PKG / "admin.py"

# Frame bases that must only ever be constructed inside spark_admin: each is a
# write, and two of them are irreversible in practice (a persisted parameter, a
# CAN id that can create an unrecoverable duplicate).
GUARDED_FRAME_CONSTANTS = {
    "PARAM_WRITE": 0x02053800,
    "SET_CAN_ID": 0x02052540,
    "PERSIST": 0x0205FFC0,
}


def package_sources():
    for path in PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def parse(path):
    return ast.parse(path.read_text(), filename=str(path))


def int_literals(tree):
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, int)}


def test_guarded_frame_bases_are_defined_only_in_spark_admin():
    """No other module may hardcode a write-frame arbitration base."""
    offenders = []
    for path in package_sources():
        if path == ADMIN:
            continue
        found = int_literals(parse(path)) & set(GUARDED_FRAME_CONSTANTS.values())
        if found:
            offenders.append((path.relative_to(REPO), [hex(v) for v in found]))
    assert offenders == [], (
        "write-frame arbitration bases hardcoded outside admin.py; import "
        f"the constants instead: {offenders}")


def test_write_param_raises_on_protected_parameters_inside_the_helper():
    """The guard must live in write_param, not be remembered by each caller."""
    tree = parse(ADMIN)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "write_param")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "PROTECTED_PARAMS" in names, "write_param does not consult PROTECTED_PARAMS"
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert raises, "write_param has no raise; a refused write would fall through"


def test_write_param_checks_before_it_sends():
    """A guard after the send would already have hit the controller."""
    tree = parse(ADMIN)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "write_param")
    raise_line = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Raise))
    send_lines = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Attribute) and n.attr == "send"]
    assert send_lines, "write_param never sends?"
    assert raise_line < min(send_lines), "the protected check must precede the send"


def test_set_can_id_validates_the_range_before_sending():
    tree = parse(ADMIN)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "set_can_id")
    raise_line = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Raise))
    send_lines = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Attribute) and n.attr == "send"]
    assert raise_line < min(send_lines)


def test_the_cli_never_calls_write_param_with_a_protected_id():
    """Static backstop: no literal 50-53 reaches a write_param call in the CLI."""
    cli = PKG / "cli.py"
    protected = {50, 51, 52, 53}
    for node in ast.walk(parse(cli)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_param"):
            literals = {a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, int)}
            assert not (literals & protected), (
                f"spark_cli calls write_param with a safety-interlock id: {literals}")


@pytest.mark.parametrize("name", sorted(GUARDED_FRAME_CONSTANTS))
def test_guarded_constants_still_exist_with_the_spec_value(name):
    from sparklib import admin as sa
    assert getattr(sa, name) == GUARDED_FRAME_CONSTANTS[name]


def test_spark_admin_never_sends_a_setpoint_frame():
    """These tools must not be able to move a motor. Setpoint frames live at
    0x02050080/0480/0C80; none may appear here."""
    setpoints = {0x02050080, 0x02050480, 0x02050C80}
    assert not (int_literals(parse(ADMIN)) & setpoints)


def test_spark_admin_does_not_enter_the_bootloader():
    """ENTER_SWDL_CAN_BOOTLOADER (api 0x1FF, arb 0x02057FC0) halts a controller
    until physical power is cycled. It knocked one offline during development
    and there is no CAN command to undo it."""
    assert 0x02057FC0 not in int_literals(parse(ADMIN))
    for path in package_sources():
        assert 0x02057FC0 not in int_literals(parse(path)), (
            f"{path.name} can enter the SWDL bootloader")


# -- tools/ has no tests of its own, so its call sites are guarded here -------
#
# On the firmware-axis change re-keyed collect_status from
# controller_type to generation. Every caller in sparklib/ was swept and
# the suite went green, but tools/spark_steer_stress.py:166 still passed
# controller_type=. That call sits INSIDE the drive loop, after steer output has
# been commanded, so it would have raised TypeError with the motors turning.
# all_stop() in the finally would have zeroed the steers, but the run would end
# in a traceback with no CSV.
#
# It survived a grep because the call spans two lines. An AST walk does not care.

TOOLS = sorted((REPO / "tools").glob("*.py"))

# Keyword arguments that were REMOVED from a spark_admin signature. Passing one
# is a TypeError at the call site, and these tools drive real motors.
REMOVED_KWARGS = {
    "collect_status": {"controller_type"},
    "fault_frame": {"controller_type"},
    "api_set": {"controller_type"},
    "normalised_reading": {"controller_type"},
    "status_problems": {"controller_type"},
}


def _calls(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name:
                yield name, node


def test_the_tools_exist_to_be_swept():
    """Guards the glob: an empty TOOLS list makes every check below vacuous."""
    assert len(TOOLS) >= 5, f"only found {[t.name for t in TOOLS]}"


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_no_tool_passes_a_removed_keyword(tool):
    """A signature change that misses tools/ fails on the robot, not in CI."""
    for name, node in _calls(tool):
        for kw in node.keywords:
            assert kw.arg not in REMOVED_KWARGS.get(name, ()), (
                f"{tool.name}:{node.lineno} calls {name}({kw.arg}=...), which "
                f"that function no longer accepts. This is a TypeError on the "
                "robot; several of these tools raise it mid-motion")


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_every_spark_admin_keyword_a_tool_passes_actually_exists(tool):
    """Wider than the list above: check every keyword against the real
    signature, so the NEXT rename is caught without anyone updating a table."""
    import inspect
    from sparklib import admin as sa

    for name, node in _calls(tool):
        fn = getattr(sa, name, None)
        if fn is None or not callable(fn) or inspect.isclass(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        if any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values()):
            continue
        for kw in node.keywords:
            if kw.arg is None:
                continue
            assert kw.arg in sig.parameters, (
                f"{tool.name}:{node.lineno} calls sa.{name}({kw.arg}=...) but "
                f"the signature is {sig}. TypeError on the robot")


# -- names, not just keywords -------------------------------------------------
#
# The sweep above checks that debug tools pass keywords spark_admin still
# accepts. Nothing checked that a caller names a function spark_admin still
# DEFINES. `decode_status_0_sparkmax` was renamed to `decode_legacy_status_0`
# and two call sites in controller.py kept the old name for a day, so the
# pre-25 firmware path raised AttributeError instead of returning a fault
# reading. No test reached it, because no SPARK MAX is on this bench.


def _bound_names(target):
    """Names a single assignment target binds, unpacking tuples and lists.

    `STATUS_0_API, STATUS_1_API, UNIQUE_ID_API = 0x2E0, 0x2E1, 0x2F0` binds three
    names through an ast.Tuple. A first version of this helper read ast.Name
    targets only and reported all three as undefined.
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {n for e in target.elts for n in _bound_names(e)}
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return set()


def _public_names(path):
    """Top-level functions, classes and assigned constants in a module."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                out |= _bound_names(t)
        elif isinstance(node, ast.AnnAssign):
            out |= _bound_names(node.target)
    return out


def _modules_under(root):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))


@pytest.mark.parametrize("path", _modules_under(PKG) + _modules_under(REPO / "tools"),
                         ids=lambda p: str(p.relative_to(REPO)))
def test_every_spark_admin_attribute_a_caller_names_actually_exists(path):
    """`spark_admin.X` and `sa.X` across the package and the debug tools.

    Only attribute access through a module alias, so an instance attribute on a
    SparkAdmin object is out of scope and cannot produce a false positive.
    """
    if path == ADMIN:
        pytest.skip("admin does not reach itself through an alias")
    tree = ast.parse(path.read_text())

    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith("admin"):
                    aliases.add(a.asname or a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "admin":
                    aliases.add(a.asname or "admin")
    if not aliases:
        pytest.skip("does not import admin")

    defined = _public_names(ADMIN)
    missing = sorted({
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        and n.value.id in aliases and n.attr not in defined
        and not n.attr.startswith("__")})
    assert not missing, (
        f"{path.relative_to(REPO)} calls spark_admin.{missing} and spark_admin "
        "defines no such name. A rename left the call site behind, and the "
        "branch that reaches it raises AttributeError at runtime.")


def test_a_missing_declared_motor_configuration_is_an_error_not_an_empty_dict(tmp_path):
    """`spark audit` compared against {} when the file was absent.

    load_motor_defaults returned None on any exception and motor_settings turned
    that into {}, so a clone without the baseline file audited every controller
    clean. The file is the only statement of what a motor should be set to.
    """
    import sparklib.admin as sa

    with pytest.raises(sa.MotorDefaultsMissing) as err:
        sa.load_motor_defaults(path=str(tmp_path / "not_here.yaml"))
    assert "audit" in str(err.value), (
        "the message must say what breaks, or the operator sees a path and no "
        "reason to care")

    assert sa.load_motor_defaults(path=str(tmp_path / "not_here.yaml"),
                                 required=False) is None


def test_the_shipped_declared_motor_configuration_is_present_and_readable():
    """The call site, not the loader. This is what `spark audit` resolves."""
    import sparklib.admin as sa

    d = sa.load_motor_defaults()
    assert d, "the shipped declared motor configuration did not load"
    for role in ("steer", "drive"):
        assert sa.motor_settings(role), (
            f"motor_settings({role!r}) is empty, so an audit of a {role} "
            "controller would compare it against nothing and pass")


# -- one decoder per frame ----------------------------------------------------


def test_no_module_reimplements_the_legacy_telemetry_scales():
    """The legacy volt and amp scales must exist in one place only.

    A second copy drifts from the sourced one without failing: a wrong scale
    still returns a plausible voltage, so nothing downstream can tell. The
    scales themselves are sourced in spark_admin and recorded in provenance.
    """
    admin_src = ADMIN.read_text()
    for scale in ("0.0073260073260073", "0.0366300366300366"):
        assert scale in admin_src, f"spark_admin no longer defines the {scale} scale"

    for path in _modules_under(PKG):
        if path == ADMIN:
            continue
        src = path.read_text()
        for bad in ("/ 128.0", "/ 32.0"):
            if bad not in src:
                continue
            # Only flag it where it sits beside a status-frame read.
            for lineno, line in enumerate(src.splitlines(), 1):
                if bad in line:
                    window = "\n".join(src.splitlines()[max(0, lineno - 8):lineno])
                    assert "_status1_raw" not in window and "_status0_raw" not in window, (
                        f"{path.relative_to(REPO)}:{lineno} decodes a status frame "
                        f"with {bad}, which is a second copy of the pre-25 scales. "
                        "Call spark_admin.decode_legacy_status_1 instead.")


def test_the_pre25_telemetry_properties_agree_with_spark_admin():
    """Behaviour, not just source shape. Build a pre-25 frame and read it both ways."""
    import struct

    from sparklib import admin as sa
    from sparklib import controller as sc

    frame = (struct.pack("<f", 1234.5)          # velocity
             + bytes([27])                      # temperature
             + bytes([0xD6, 0x06, 0x00]))       # voltage / current fixed point
    assert len(frame) == 8

    expected = sa.decode_legacy_status_1(frame)

    c = object.__new__(sc.Controller)
    c._status1_raw = frame
    c._status0_raw = None
    c._observed_generation = sa.GEN_PRE25
    c.controller_type = sc.SPARK_MAX

    assert c.bus_voltage == pytest.approx(expected["voltage_v"]), (
        f"Controller.bus_voltage reads {c.bus_voltage} where spark_admin reads "
        f"{expected['voltage_v']}; two decoders for one frame")
    assert c.output_current == pytest.approx(expected["current_a"]), (
        f"Controller.output_current reads {c.output_current} where spark_admin "
        f"reads {expected['current_a']}")


def test_the_pre25_fault_properties_agree_with_spark_admin():
    """The properties that raised AttributeError until."""
    from sparklib import admin as sa
    from sparklib import controller as sc

    frame = bytes([0x00, 0x00, 0x21, 0x00, 0x04, 0x00, 0x00, 0x00])
    expected = sa.decode_legacy_status_0(frame)
    assert not expected["is_beacon"], "premise: this must not be the 25+ beacon"

    c = object.__new__(sc.Controller)
    c._status0_raw = frame
    c._status1_raw = None
    c._observed_generation = sa.GEN_PRE25
    c.controller_type = sc.SPARK_MAX

    assert c.active_faults == expected["active_faults"]
    assert c.sticky_faults == expected["sticky_faults"]


def test_a_25plus_beacon_is_not_read_as_a_fault_word():
    """On firmware 25+ api 0x060 is a presence beacon with every fault bit set.

    Scoring those bits reports sixteen faults on a healthy controller, which is
    the reading that wedged a base.
    """
    from sparklib import admin as sa
    from sparklib import controller as sc

    c = object.__new__(sc.Controller)
    c._status0_raw = sa.LEGACY_BEACON_PAYLOAD
    c._status1_raw = None
    c._observed_generation = sa.GEN_PRE25
    c.controller_type = sc.SPARK_MAX

    assert c.active_faults is None, (
        "the 25+ beacon was scored as a fault word; every bit in it is set by "
        "design so old software knows something is wrong")
    assert c.sticky_faults is None


# -- the index has to keep listing every tool -------------------------------

def test_the_tools_readme_lists_every_tool_and_no_ghosts():
    """tools/README.md is how a reader finds out to find out which of these
    move motors. A tool missing from it reads as safe by omission."""
    readme = REPO / "tools" / "README.md"
    assert readme.exists(), "the tools directory must document itself"
    listed = set(re.findall(r"`([a-z_0-9]+\.py)`", readme.read_text()))
    actual = {t.name for t in TOOLS}
    assert not actual - listed, f"not in tools/README.md: {sorted(actual - listed)}"
    assert not listed - actual, f"listed but gone: {sorted(listed - actual)}"


def test_the_telemetry_scales_are_named_once_and_not_re_inlined():
    """Both decoders and tools/ read the same volts- and amps-per-count. Four
    inline copies drifted apart once already."""
    admin = (REPO / "sparklib" / "admin.py").read_text()
    assert admin.count("0.0073260073260073") == 1, "volts-per-count re-inlined"
    assert admin.count("0.0366300366300366") == 1, "amps-per-count re-inlined"
    from sparklib import admin as sa
    assert sa.VOLT_PER_COUNT == 0.0073260073260073
    assert sa.AMP_PER_COUNT == 0.0366300366300366


def _can_diag(monkeypatch, ip_output):
    """Run can_diag's link check over a canned `ip -details link` output and
    return what it emitted. Checking the source text instead misses strings
    split across lines, which is how the first version of this test passed
    while the message was wrong."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "can_diag", REPO / "tools" / "can_diag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    said = []
    monkeypatch.setattr(mod, "emit", lambda lvl, msg: said.append((lvl, msg)))
    monkeypatch.setattr(mod, "run", lambda *a, **k: ip_output)
    mod.check_link("can0")
    return said


_GS_USB_LINK = """13: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 state UP
    can state ERROR-ACTIVE restart-ms 0
    bitrate 1000000 sample-point 0.750
    gs_usb: tseg1 1..16 tseg2 1..8 sjw 1..4 brp 1..1024 brp_inc 1
    re-started bus-errors arbit-lost error-warn error-pass bus-off
    0          0          0          0          0          0
    RX:  bytes packets errors dropped  missed   mcast
         0       0      0       0       0       0
    TX:  bytes packets errors dropped carrier collsns
         0       0      0       0       0       0
"""

_OTHER_LINK = _GS_USB_LINK.replace("gs_usb:", "mcp251xfd:")


def test_can_diag_does_not_send_gs_usb_users_to_a_rejected_command(monkeypatch):
    """rig-max's gs_usb rejects restart-ms outright, measured, so the
    generic advice is a dead end there. README.md records the rebind instead."""
    said = " ".join(m for _, m in _can_diag(monkeypatch, _GS_USB_LINK))
    assert "restart-ms" in said, "the warning stopped firing altogether"
    assert "canfix" in said, "gs_usb users need the rebind, not restart-ms"
    assert "ip link set" not in said, "still offering the command this adapter rejects"


def test_can_diag_still_offers_restart_ms_where_it_works(monkeypatch):
    """The advice is only wrong for gs_usb. Suppressing it everywhere would
    trade one bad message for another."""
    said = " ".join(m for _, m in _can_diag(monkeypatch, _OTHER_LINK))
    assert "ip link set" in said and "restart-ms 100" in said
    assert "canfix" not in said


# -- a silent bus is not necessarily a silent fleet ---------------------------

_FLAPPING_LOG = """\
Sep 10 19:13:43 host kernel: gs_usb 3-1.3:1.0 can0: usb xmit fail 0
Sep 10 19:13:43 host kernel: gs_usb 3-1.3:1.0 can0: usb xmit fail 9
Sep 10 19:13:43 host kernel: usb 3-1.3: USB disconnect, device number 15
Sep 10 19:13:44 host kernel: usb 3-1.3: device descriptor read/64, error -32
Sep 10 19:13:44 host kernel: gs_usb 3-1.3:1.0: Configuring for 1 interfaces
"""

_QUIET_LOG = "Sep 10 19:13:43 host kernel: nvme nvme0: using unchecked data buffer\n"


def _instability(monkeypatch, log):
    from sparklib import cli as c
    import subprocess

    class R:
        stdout = log
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    return c, c.adapter_instability("can0", 30)


def test_a_flapping_adapter_is_counted_from_the_kernel_log(monkeypatch):
    """rig-max spent an hour on motor power and the CAN chain while
    the adapter was falling off the USB bus. Nothing asked the kernel."""
    c, d = _instability(monkeypatch, _FLAPPING_LOG)
    assert d["disconnects"] == 1
    assert d["xmit_failures"] == 2
    assert d["descriptor_stalls"] == 1
    assert d["reenumerations"] == 1


def test_a_quiet_kernel_log_produces_no_note(monkeypatch):
    """The note must stay silent on a healthy machine, or it is noise that
    trains people to skip it."""
    c, d = _instability(monkeypatch, _QUIET_LOG)
    assert d is None
    assert c.adapter_instability_note("can0", 30) == []


_REBIND_ONLY_LOG = """\
Sep 10 19:13:43 host kernel: gs_usb 3-1.3:1.0: Configuring for 1 interfaces
Sep 10 19:13:43 host kernel: gs_usb 3-1.3:1.0 can0: renamed from can0
"""


def test_a_plain_rebind_does_not_raise_the_alarm(monkeypatch):
    """`spark canfix` reconfigures the driver every time it runs, and that alone
    is not instability. Warning on it would fire after every routine recovery
    and train people to ignore the message."""
    c, d = _instability(monkeypatch, _REBIND_ONLY_LOG)
    assert d is not None and d["reenumerations"] == 1
    assert d["disconnects"] == d["xmit_failures"] == d["descriptor_stalls"] == 0
    assert c.adapter_instability_note("can0", 30) == [], (
        "a reconfigure with no disconnect, no transmit failure and no stall is "
        "a rebind, not a failing adapter")


def test_the_note_names_the_tx_led_and_the_watcher(monkeypatch):
    """The LED is the fastest check and needs no software at all: if the host is
    sending and it is dark, the frames never reach the adapter."""
    c, _ = _instability(monkeypatch, _FLAPPING_LOG)
    note = " ".join(c.adapter_instability_note("can0", 30))
    assert "TX LED" in note
    assert "can_usb_watch.py" in note
    assert "-rig-max-drive-overcurrent-and-adapter-flap" in note


def test_the_check_never_raises_and_never_hangs(monkeypatch):
    """A diagnostic that breaks the command it is helping is worse than none."""
    from sparklib import cli as c
    import subprocess

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("journalctl", 6)
    monkeypatch.setattr(subprocess, "run", boom)
    assert c.adapter_instability("can0", 30) is None
    assert c.adapter_instability_note("can0", 30) == []


def test_both_silent_bus_messages_consult_the_adapter_first():
    """Read the call sites. The defect was advice, not a missing helper: the
    FIX text sent the operator to motor power with no adapter check above it."""
    src = (REPO / "sparklib" / "cli.py").read_text()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "adapter_instability_note"]
    assert len(calls) >= 2, (
        "both the empty-inventory path and the clear-did-not-wake path must ask "
        "the kernel before blaming the robot")
