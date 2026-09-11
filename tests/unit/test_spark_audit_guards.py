"""Live guards on the SPARK audit: what it reads, and what its callers must not do.

Every test here PASSES. The work list of defects this suite once documented as
xfail now lives in tests/adversarial/, which owns it -- keeping both meant two
copies of one list, and the copy nobody ran drifted. What stayed here is the
part that guards behaviour the tooling already has:

  * `spark audit` opens STATUS_1 and scores faults, not cadence alone (D1)
  * a latched fault at a perfect broadcast period still exits non-zero
  * decoded flags reach a finding: follower, heartbeat lock, hard limit,
    sticky hasReset, clamped output, an unexpected SPARK model
  * the call-site rules: no caller reads a write response for truthiness, and
    a rejected parameter value is reported rather than retried
  * the hard-limit parameters stay refused by write_param

These were deleted as "documented gaps guarding almost nothing".
Thirteen of them had become live guards weeks earlier when the D1 fix landed,
which a `pytest -rX` would have shown and a marker count did not. Restored, and
the reasoning is in tests/README.md.
"""

import ast
import pathlib
import struct
from types import SimpleNamespace

import pytest

from sparklib import admin as sa

S0, S1, UID = sa.STATUS_0_API, sa.STATUS_1_API, sa.UNIQUE_ID_API

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "sparklib"
ADMIN = PKG / "admin.py"

# Bit order per REV-Specs 2.1.0, restated so these frames are built independently
# of the decoder they are used to exercise.
FAULT_BITS = ("other", "motorType", "sensor", "can",
              "temperature", "gateDriver", "escEeprom", "firmware")
WARNING_BITS = ("brownout", "overcurrent", "escEeprom", "extEeprom",
                "sensor", "stall", "hasReset", "other")
FAULT = {n: 1 << i for i, n in enumerate(FAULT_BITS)}
WARN = {n: 1 << i for i, n in enumerate(WARNING_BITS)}

# STATUS_0 byte 6 flags.
LIMIT_FWD = 0x01
LIMIT_REV = 0x02
HEARTBEAT_LOCK = 0x20

# FRC extended id device-type field. CAN ids are unique per (device type,
# manufacturer), not per bus, so a REV PDH may legitimately share id 10.
SPARK_DEVICE_TYPE = 0x02
PDH_DEVICE_TYPE = 0x08


# -- wire helpers -------------------------------------------------------------

def arb(api, dev, device_type=SPARK_DEVICE_TYPE):
    """FRC extended id: device type, manufacturer, api class, device id."""
    return (device_type << 24) | (sa.REV_MFR << 16) | (api << 6) | dev


def status_0(applied=0.0, volts=12.6, amps=0.0, temp=30, model=1, flags=0):
    """A STATUS_0 payload, encoded with the spec's scale factors."""
    d = bytearray(8)
    v = int(round(volts / 0.0073260073260073)) & 0x0FFF
    c = int(round(amps / 0.0366300366300366)) & 0x0FFF
    d[0:2] = struct.pack("<h", int(round(applied / 3.082369457075716e-05)))
    d[2] = v & 0xFF
    d[3] = ((v >> 8) & 0x0F) | ((c & 0x0F) << 4)
    d[4] = (c >> 4) & 0xFF
    d[5] = temp & 0xFF
    d[6] = flags | ((model & 0x03) << 6)
    d[7] = (model >> 2) & 0x03
    return bytes(d)


def status_1(faults=0, warnings=0, sticky_faults=0, sticky_warnings=0,
             follower=False):
    d = bytearray(8)
    d[0], d[2], d[3], d[5] = faults, warnings, sticky_faults, sticky_warnings
    d[6] = 0x01 if follower else 0x00
    return bytes(d)


def decoded(status0=None, status1=None):
    """One device's collect_status() entry, built by the real decoders."""
    return {"status0": sa.decode_status_0(status0),
            "status1": sa.decode_status_1(status1)}


class FakeMsg:
    def __init__(self, arbitration_id, data=b"", is_remote_frame=False):
        self.arbitration_id = arbitration_id
        self.data = data
        self.is_remote_frame = is_remote_frame


class Clock:
    """Virtual wall clock. Every wait in the driver advances it, none sleeps."""

    def __init__(self, t=0.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))


@pytest.fixture
def clock(monkeypatch):
    """Substitute spark_admin's time module so waits and blackouts are free."""
    c = Clock()
    monkeypatch.setattr(sa, "time", SimpleNamespace(time=c.time, sleep=c.sleep))
    return c


class WireBus:
    """The one bus fake: replays a timed frame schedule and answers requests.

    `schedule` is [(absolute_time, arbitration_id, data)]; data may be a
    zero-argument callable when a device's broadcast changes during the test.
    `on_send(msg, now) -> [(delay, arbitration_id, data),...]` is the device
    model, delivering replies `delay` later.

    recv(timeout=0) returns None -- the driver's pre-request drain must not
    swallow a reply that has not been sent yet. Frames are consumed: a second
    listening window over the same traffic sees what is left, as on real
    hardware, which is what makes a two-window scan detectable.
    """

    def __init__(self, clock, schedule=(), on_send=None):
        self.clock = clock
        self._sched = sorted(schedule, key=lambda f: f[0])
        self._i = 0
        self._pending = []
        self.on_send = on_send
        self.sent = []          # FakeMsg, in send order
        self.sent_at = []       # (virtual time, FakeMsg)
        self.received_at = []   # (virtual time, FakeMsg)
        self.log = []           # ('send'|'recv', arbitration_id, virtual time)

    def send(self, msg):
        self.sent.append(msg)
        self.sent_at.append((self.clock.t, msg))
        self.log.append(("send", msg.arbitration_id, self.clock.t))
        for delay, a, data in (self.on_send(msg, self.clock.t) or ())\
                if self.on_send else ():
            self._pending.append((self.clock.t + delay, FakeMsg(a, data)))
        self._pending.sort(key=lambda p: p[0])

    def recv(self, timeout=0):
        if not timeout:
            return None
        deadline = self.clock.t + timeout
        options = []
        if self._pending:
            options.append(("reply", self._pending[0][0]))
        if self._i < len(self._sched):
            options.append(("stream", self._sched[self._i][0]))
        if options:
            kind, when = min(options, key=lambda o: o[1])
            if when <= deadline:
                if kind == "reply":
                    _, m = self._pending.pop(0)
                else:
                    _, a, data = self._sched[self._i]
                    self._i += 1
                    m = FakeMsg(a, data() if callable(data) else data)
                self.clock.t = max(self.clock.t, when)
                self.log.append(("recv", m.arbitration_id, self.clock.t))
                self.received_at.append((self.clock.t, m))
                return m
        self.clock.t = deadline
        return None

    def shutdown(self):
        pass

    def sent_ids(self):
        return [m.arbitration_id for m in self.sent]

    def sends_matching(self, base):
        return [(t, m) for t, m in self.sent_at
                if m.arbitration_id & ~0x3F == base]


def repeat(arbitration_id, data, period_ms, span_s, start_s=0.0):
    """A frame broadcast at a fixed period for `span_s` of the window."""
    p = period_ms / 1000.0
    return [(start_s + (i + 1) * p, arbitration_id, data)
            for i in range(int(round(span_s / p)))]


def admin(bus):
    """A SparkAdmin wired to a fake bus, bypassing the socketcan open."""
    a = object.__new__(sa.SparkAdmin)
    a.channel = "fake0"
    a.bus = bus
    return a




class FakeSpark:
    """One controller behaving the way the field reports say they behave.

    _ram and flashed are the two copies of a parameter. A PARAMETER_WRITE is
    acknowledged immediately but reaches RAM only after `apply_delay`; PERSIST
    flashes whatever RAM holds at the instant it arrives and then stops
    answering CAN for `blackout` seconds. `drop_writes` swallows the first N
    writes outright -- no PARAM_WRITE_RESP at all, which is the reported
    back-to-back write failure.
    """

    def __init__(self, dev, clock, ram=250, apply_delay=0.0, blackout=0.0,
                 drop_writes=0, latency=0.001, firmware=None,
                 write_result=0, drop_first_write_of=None):
        self.dev = dev
        self.clock = clock
        self._ram = ram
        self.flashed = ram
        self.apply_delay = apply_delay
        self.blackout = blackout
        self.drop_writes = drop_writes
        self.latency = latency
        self.firmware = firmware
        self.write_result = write_result
        self.drop_first_write_of = drop_first_write_of
        self._dropped = set()
        self.blocked_until = -1.0
        self._pending_apply = None

    @property
    def ram(self):
        self._settle(self.clock.t)
        return self._ram

    def _settle(self, now):
        if self._pending_apply and now >= self._pending_apply[0]:
            self._ram = self._pending_apply[1]
            self._pending_apply = None

    def on_send(self, msg, now):
        self._settle(now)
        if now < self.blocked_until:
            return []                       # frame dropped during the flash burn
        a = msg.arbitration_id
        if a == sa.PARAM_WRITE | self.dev:
            param_id = msg.data[0]
            if self.drop_writes > 0:
                self.drop_writes -= 1
                return []
            if param_id == self.drop_first_write_of and param_id not in self._dropped:
                self._dropped.add(param_id)
                return []
            value = int.from_bytes(msg.data[1:5], "little")
            self._pending_apply = (now + self.apply_delay, value)
            return [(self.latency, sa.PARAM_WRITE_RESP | self.dev,
                     bytes([param_id, 2]) + struct.pack("<I", value)
                     + bytes([self.write_result]))]
        if a == sa.PERSIST | self.dev:
            self.flashed = self._ram
            self.blocked_until = now + self.blackout
            return [(self.latency, sa.PERSIST_RESP | self.dev, bytes([0]))]
        if a == sa.GET_FIRMWARE | self.dev and self.firmware:
            major, minor, fix = (int(x) for x in self.firmware.split("."))
            return [(self.latency, a,
                     bytes([major, minor]) + fix.to_bytes(2, "big") + bytes([0, 3]))]
        return []


# -- bus identity: rig-flex -----------------------------------------------------

ROLES = {10: "drive/RB", 11: "steer/RB", 12: "drive/RF", 13: "steer/RF",
         14: "drive/LF", 15: "steer/LF", 16: "drive/LB", 17: "steer/LB"}
SERIALS = {10: "C7B24C10", 11: "AB15F6C7", 12: "6B029ADD", 13: "4D9AB6E7",
           14: "08FE696E", 15: "5400CEBD", 16: "F3003513", 17: "498B2579"}

_NO_KEY = object()


def ctrl(serial, s1_ms=20.0, s0_ms=10.0, frames=600, firmware=_NO_KEY):
    """One device's entry with exactly the keys inventory() produces.

    inventory() sets no 'firmware' key. Pass firmware= only to model the
    enriched dict cmd_audit builds when a baseline is loaded (cli.py:440);
    a fixture that invents it everywhere hides the dead baseline check below.
    """
    periods = {}
    if s0_ms is not None:
        periods[S0] = s0_ms
    if s1_ms is not None:
        periods[S1] = s1_ms
    entry = {"serial": serial, "frames": frames, "periods_ms": periods}
    if firmware is not _NO_KEY:
        entry["firmware"] = firmware
    return entry


def healthy_inventory():
    return {d: ctrl(s) for d, s in SERIALS.items()}




def clean_status():
    return {d: decoded(status_0(), status_1()) for d in ROLES}


def audit(inventory, dups=None, roles=None, serials=None, base=None, **extra):
    """audit_problems, optionally handed the decoded status it must consume."""
    try:
        return sa.audit_problems(inventory, dups or {}, roles or {},
                                 serials or {}, base, **extra)
    except TypeError as exc:
        pytest.fail(
            "audit_problems must accept the readings the driver already takes "
            f"({', '.join(sorted(extra))}): {exc}")




# =============================================================================
# decoding and sampling: evidence lost before anything can read it
# =============================================================================









# =============================================================================
# the audit never sees a status payload
# =============================================================================

@pytest.mark.parametrize("frame, tag, url", [
    (status_1(faults=FAULT["sensor"], sticky_faults=FAULT["sensor"]), "sensor",
     "https://www.chiefdelphi.com/t/440522"),
    (status_1(faults=FAULT["gateDriver"], sticky_faults=FAULT["gateDriver"]),
     "gateDriver", "https://www.chiefdelphi.com/t/444231"),
    (status_1(sticky_faults=FAULT["can"]), "can",
     "https://www.chiefdelphi.com/t/426287"),
], ids=["encoder-cable", "gate-driver", "duplicate-id"])
def test_a_latched_fault_at_a_perfect_period_is_not_audited_as_healthy(
        frame, tag, url):
    """A broken encoder cable latches a sensor fault and locks the module up
    (t/440522, and REV states an unplugged encoder turns the output off
    entirely, t/392888); a gate driver fault latches active and sticky and
    survives CLEAR_FAULTS, factory reset and RMA-grade abuse (t/444231,
    t/363736); two controllers on one id show up as sticky CAN TX/RX faults on
    the node still transmitting rather than as a missing device (t/426287).

    In all three the frames keep flowing at a perfect 20 ms, serial and period
    are right, and a cadence-only audit returns clean while the controller is
    broadcasting exactly what is wrong.
    """
    status = clean_status()
    status[14] = decoded(status_0(), frame)

    problems = audit(healthy_inventory(), roles=ROLES, serials=SERIALS,
                     status=status)

    assert problems, f"fault broadcast at a clean period, audit said nothing ({url})"
    assert any("14" in p and tag.lower() in p.lower() for p in problems), (
        f"a controller refusing to drive audited as {problems}")


def test_sticky_has_reset_is_reported_as_config_loss_risk():
    """A SPARK MAX reset mid-match and came back without its inverted setting;
    REV's answer was to monitor the sticky hasReset flag.
    https://www.chiefdelphi.com/t/377014

    hasReset means the controller dropped exactly what write_param puts in RAM.
    decode_status_1 already extracts it into sticky_warnings.
    """
    status = clean_status()
    status[11] = decoded(status_0(),
                         status_1(sticky_warnings=WARN["hasReset"] | WARN["brownout"]))

    problems = audit(healthy_inventory(), roles=ROLES, serials=SERIALS,
                     status=status)

    assert any("11" in p and "reset" in p.lower() for p in problems), (
        f"a controller that rebooted and may have lost its RAM config audited "
        f"clean: {problems}")


def test_a_status_period_after_a_restart_is_not_flash_config_loss():
    """Status frame periods are volatile and drop to defaults on any restart, so
    teams write code that detects controller restarts and restores them.
    https://www.chiefdelphi.com/t/405541

    The sticky hasReset warning is the only thing separating a restart from a
    lost flash config, and acting on the wrong one spends a flash cycle on a
    controller whose flash was never wrong.
    """
    inv = {10: ctrl(SERIALS[10], s1_ms=250.0)}
    status = {10: decoded(status_0(), status_1(sticky_warnings=WARN["hasReset"]))}

    problems = audit(inv, roles={10: "drive/RB"}, serials={10: SERIALS[10]},
                     status=status)

    assert problems
    assert any("restart" in p.lower() or "reset" in p.lower() for p in problems), (
        f"hasReset is set: the periods were lost to a restart, not to flash: {problems}")
    assert not any("re-provision" in p for p in problems), (
        "re-burning flash on a controller whose flash was never wrong")


def test_a_heartbeat_locked_fleet_is_reported():
    """CAN lockout: once a SPARK has seen a roboRIO -- or the REV Hardware
    Client -- it ignores every other source until it is disconnected and
    power-cycled. https://www.chiefdelphi.com/t/403283
    https://www.chiefdelphi.com/t/452062

    Frames, periods and fault bits are all normal and applied output stays 0, so
    a whole locked-out bus audits as healthy and every conclusion drawn from "it
    did not move" is then wrong. It is also the cheapest answer to "is another
    master already holding this fleet", which decides whether a repair run means
    anything at all.
    """
    devs = (10, 11)
    inv = {d: ctrl(SERIALS[d]) for d in devs}
    status = {d: decoded(status_0(flags=HEARTBEAT_LOCK), status_1()) for d in devs}

    problems = audit(inv, roles={d: ROLES[d] for d in devs}, status=status)

    assert problems, "a heartbeat-locked fleet cannot be driven and audits clean"
    assert any("lock" in p.lower() or "heartbeat" in p.lower() for p in problems), (
        f"every controller is latched to an absent master: {problems}")


def test_a_latched_hard_limit_is_named_so_the_refused_repair_is_actionable():
    """A misaligned data-port breakout board held a hard limit active. The first
    advice the team got was to disable the hard limits; REV found the breakout
    board sitting misaligned. https://www.chiefdelphi.com/t/455481

    The driver is right to refuse the write -- but refusing is only defensible
    if the audit says what is actually wrong.
    """
    inv = {14: ctrl(SERIALS[14])}
    status = {14: decoded(status_0(flags=LIMIT_REV), status_1())}

    problems = audit(inv, roles={14: "arm/pivot"}, serials={14: SERIALS[14]},
                     status=status)

    assert any("limit" in p.lower() for p in problems), (
        f"mechanism is pinned at a hard limit and audits clean: {problems}")


def test_disabling_a_hard_limit_stays_refused(clock):
    """The other half of the same thread: the tempting fix must stay impossible.
    Community advice was to turn the forward and reverse hard limits off.
    https://www.chiefdelphi.com/t/455481
    """
    bus = WireBus(clock, on_send=FakeSpark(14, clock).on_send)
    with pytest.raises(sa.ProtectedParameterError):
        admin(bus).write_param(14, 53, 0)
    assert bus.sent == [], "a frame was aimed at a safety interlock"




def test_output_clamped_at_the_current_limit_is_reported():
    """The smart current limit holds applied output near zero at stall: 0.079 of
    full while pulling the configured 40 A at a nominal rail, with no fault and
    no warning raised. https://www.chiefdelphi.com/t/491331

    The mirror image of the dead-output-stage case, and the same three decoded
    fields decide it.
    """
    inv = {d: ctrl(SERIALS[d]) for d in (12, 13, 14)}
    status = {
        12: decoded(status_0(applied=0.079, volts=12.4, amps=40.0), status_1()),
        13: decoded(status_0(applied=0.0, volts=12.4, amps=0.2), status_1()),
        14: decoded(status_0(applied=0.85, volts=12.4, amps=40.0), status_1()),
    }

    problems = audit(inv, roles={d: ROLES[d] for d in (12, 13, 14)}, status=status)

    assert any("12" in p for p in problems), (
        "near-zero output at the current limit on a nominal rail is a stalled or "
        "mis-limited mechanism, not a healthy controller")
    assert not any("13" in p for p in problems), "an idle controller is not stalled"
    assert not any("14" in p for p in problems), (
        "a controller doing real work at its limit is not clamped out")


def test_a_controller_broadcasting_the_follower_bit_is_reported():
    """A leader latched in follower mode after restoreFactoryDefaults + follow():
    the controller fights its own commands about four times a second and the
    state survives a power cycle. The same bit is the whole story where one side
    of a drivetrain never moves because the commanded controller is a follower.
    https://www.chiefdelphi.com/t/378716  https://www.chiefdelphi.com/t/373252

    The flag is free to check against the role map. Which leader it is following
    is parameter 194 and genuinely unreadable on 26.1.6 -- the flag is not.
    """
    inv = {d: ctrl(SERIALS[d]) for d in (10, 11)}
    status = {10: decoded(status_0(), status_1(follower=True)),
              11: decoded(status_0(), status_1())}

    problems = audit(inv, roles={10: ROLES[10], 11: ROLES[11]}, status=status)

    about_10 = [p for p in problems if "10" in p]
    assert about_10, ("a controller broadcasting is_follower cannot accept a "
                      "setpoint; the audit must not call it healthy")
    assert any("follow" in p.lower() for p in about_10)
    assert not [p for p in problems if "11" in p and "10" not in p], (
        "the controller that is not a follower must not be flagged")


def test_a_different_spark_model_answering_a_configured_id_is_flagged():
    """'CANSparkMax object created for CAN ID 10, which is not a SPARK MAX' --
    the host detected over the bus that the device was a different model.
    https://www.chiefdelphi.com/t/475584

    decode_status_0 already extracts spark_model, and the expectation exists
    too: base.controller_type and the baseline meta both record it. Nothing
    compares them, and the serial guard is skipped for an id with no recorded
    serial.
    """
    status = clean_status()
    status[10] = decoded(status_0(model=2), status_1())

    problems = audit(healthy_inventory(), roles=ROLES, serials=SERIALS,
                     status=status, controller_type="sparkflex")

    assert any("10" in p and "model" in p.lower() for p in problems), (
        f"a foreign controller model answered id 10 and audited clean: {problems}")


def test_audit_problems_consumes_the_status_reading(clock):
    """Call-site guard: a status helper exists to be called by the audit.

    A helper that decodes faults nobody feeds into the audit is the same blind
    spot with extra code -- collect_status and decode_status_1 are already in
    exactly that position.
    """
    st = sa.collect_status(WireBus(clock, [
        (0.01, arb(S0, 10), status_0()),
        (0.02, arb(S1, 10), status_1(faults=FAULT["gateDriver"])),
    ]), 1.0)

    direct = sa.status_problems(st, {10: "drive/RB"})
    audited = audit({10: ctrl(SERIALS[10])}, roles={10: "drive/RB"},
                    serials={10: SERIALS[10]}, status=st)

    assert direct, "status_problems itself found nothing; the fixture is wrong"
    missing = [p for p in direct if p not in audited]
    assert not missing, f"audit_problems drops status findings: {missing}"


def test_cmd_audit_reads_status_payloads_not_just_cadence():
    """`spark audit --help` promises 'exit 1 on any fault'. The static half of
    that promise: the audit must actually obtain a status reading and hand it on.
    """
    cli = ast.parse((PKG / "cli.py").read_text())
    fn = next(n for n in ast.walk(cli)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_audit")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "collect_status" in called, "cmd_audit never reads a STATUS payload"
    audit_call = next(n for n in ast.walk(fn)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id == "audit_problems")
    assert "status" in {k.arg for k in audit_call.keywords}, (
        "cmd_audit reads the status frames and then does not pass them to the audit")


def test_cmd_audit_tells_the_audit_which_firmware_generation_the_bus_is():
    """Frame selection keys on firmware generation, and the audit has to be
    handed the one the bus actually reported.

    Without it `fault_frame` falls back to 25+ and a pre-25 base is audited
    against api 0x2E1 while its controllers broadcast on 0x060. The behavioural
    tests all pass their own generation, so nothing else here can see the
    keyword go missing at the one call site that matters.
    """
    cli = ast.parse((PKG / "cli.py").read_text())
    fn = next(n for n in ast.walk(cli)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_audit")

    call = next(n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "audit_problems")
    assert "generation" in {k.arg for k in call.keywords}, (
        "cmd_audit calls audit_problems() without generation, so it scores the "
        "25+ fault frame on every base regardless of what is on the bus")

    # collect_status listens on both generations and labels each device, so
    # passing it a product would narrow a reading it can make for itself.
    listen = next(n for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "collect_status")
    assert "controller_type" not in {k.arg for k in listen.keywords}, (
        "cmd_audit still hands collect_status a product; frame layout keys on "
        "firmware and the wire already carries it")


def test_cmd_audit_exits_nonzero_for_a_broadcast_fault(clock, monkeypatch, capsys):
    """The end of the same promise, at the call site: a controller broadcasting a
    latched sensor fault at a perfect period must not exit 0.
    https://www.chiefdelphi.com/t/440522
    """
    from sparklib import cli as spark_cli

    bus = WireBus(clock, [(0.01, arb(S0, 10), status_0()),
                          (0.02, arb(S1, 10), status_1(faults=FAULT["sensor"])),
                          (0.03, arb(UID, 10), bytes.fromhex(SERIALS[10]))])

    class FakeAdmin:
        channel = "fake0"

        def __init__(self):
            self.bus = bus

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        # **kw so this double does not break every time the real signature
        # grows an opt-in argument. It gained with_fingerprint.
        def inventory(self, seconds=5.0, **kw):
            return {10: ctrl(SERIALS[10])}

        def duplicates(self, seconds=6.0, **kw):
            return {}

        def scan(self, seconds=6.0):
            return self.inventory(seconds), self.duplicates(seconds)

        # Silent to every parameter read. This double exists to pin the exit
        # code for a BROADCAST fault, so the declared-config comparison must
        # neither crash it nor add findings of its own.
        def read_param_value(self, dev, param_id, wait=0.5):
            return None

        def read_param_pair(self, dev, param_id, wait=0.5):
            return None

        def probe_missing(self, devs, wait=0.5):
            return {}

        def firmware(self, dev, wait=0.5):
            return "26.1.6", 1

    monkeypatch.setattr(spark_cli, "_open", FakeAdmin)
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: {10: "drive/RB"})
    monkeypatch.setattr(spark_cli, "_known_serials", lambda: {10: SERIALS[10]})
    monkeypatch.setattr(spark_cli, "_load_baseline", lambda: (None, "none"))

    rc = spark_cli.cmd_audit(SimpleNamespace(window=1.0))

    assert rc == 1, ("`spark audit` exited 0 over a latched sensor fault:\n"
                     + capsys.readouterr().out)


# =============================================================================
# cadence arithmetic: readings that mean something else entirely
# =============================================================================































# =============================================================================
# firmware skew the audit cannot see
# =============================================================================





# =============================================================================
# the write path: answered, and not applied
# =============================================================================





def test_a_rejected_value_is_reported_and_not_retried(clock):
    """'received parameter invalid error parameter id 113' -- an out-of-range
    computed value, a zero conversion factor from an unset gear ratio, is refused
    with result 4. https://www.chiefdelphi.com/t/427628

    Timeout and rejection are opposite repairs: one is retried, one loops forever
    if retried. Any retry added for t/456184 must branch on which it is.
    """
    def on_send(msg, now):
        if msg.arbitration_id != sa.PARAM_WRITE | 9:
            return []
        return [(0.001, sa.PARAM_WRITE_RESP | 9,
                 bytes([113, 3]) + struct.pack("<I", 0) + bytes([4]))]

    bus = WireBus(clock, on_send=on_send)
    r = admin(bus).write_param(9, 113, sa.float_bits(0.0))

    assert r["result"] == 4 and r["result_text"] == "Invalid"
    assert bus.sent_ids().count(sa.PARAM_WRITE | 9) == 1, (
        "a refused value was re-sent; the device will refuse it forever")


def test_no_caller_reads_a_write_response_for_truthiness():
    """Call-site guard for the same pair: a rejection is a dict with result=4, and
    a dict is truthy. `if adm.write_param(...)` reads a refused write as a
    success. https://www.chiefdelphi.com/t/427628
    """
    offenders = []
    for path in PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                     if isinstance(n.value, ast.Call)
                     and isinstance(n.value.func, ast.Attribute)
                     and n.value.func.attr == "write_param"
                     for t in n.targets if isinstance(t, ast.Name)}
            for name in names:
                reads = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript)
                         and isinstance(n.value, ast.Name) and n.value.id == name
                         and isinstance(n.slice, ast.Constant)
                         and n.slice.value == "result"]
                if not reads:
                    offenders.append(f"{path.name}:{fn.name} ignores {name}['result']")
    assert offenders == [], offenders
















# =============================================================================
# clearing faults: a release the driver never checks
# =============================================================================





# =============================================================================
# what the audit claims it did not check
# =============================================================================

