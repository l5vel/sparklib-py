"""A python-can drop-in that broadcasts a modelled fleet on a virtual clock.

The scheduler is the part that has to be exactly right: it materialises the next
frame that is due, advances the clock to it and hands it over, so a driver loop
written as `while time.time() < end: bus.recv(timeout=end - time.time())`
terminates, measures its window exactly, and costs no real time.

recv(timeout=0) -- the non-blocking drain SparkAdmin does before every request --
returns None by default, so a drain cannot eat a reply that is not due yet.
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import frames as F
from .clock import VirtualClock
from .controller import SimSpark
from .faults import (AmbiguousDeviceError, BootloaderFrameForbidden, BusShutdown,
                     Congestion, ProtectedWriteReachedBus, SetpointFrameForbidden,
                     Silence, SimFrameBudgetExceeded)

EPS = 1e-9


def _q(t: float) -> float:
    """Quantise to nanoseconds so a cadence lands on an exact grid."""
    return round(t, 9)


@dataclass
class SimMessage:
    arbitration_id: int
    data: bytes = b""
    timestamp: float = 0.0
    is_extended_id: bool = True
    is_remote_frame: bool = False
    is_error_frame: bool = False
    dlc: Optional[int] = None
    channel: Optional[str] = None
    # python-can sets this on every frame it hands back, and the driver reads it.
    # The pre-25 parameter reply arrives on the SAME arbitration id as the
    # request, so separating a reply from an echo of the request is not optional
    # there: _await_legacy_param in admin.py checks exactly this field.
    # Nothing this bus delivers is an echo, so True is the honest default.
    is_rx: bool = True

    def __post_init__(self):
        self.data = bytes(self.data or b"")
        if self.dlc is None:
            self.dlc = len(self.data)

    @classmethod
    def coerce(cls, msg, timestamp=0.0, channel=None) -> "SimMessage":
        return cls(arbitration_id=msg.arbitration_id,
                   data=bytes(getattr(msg, "data", b"") or b""),
                   timestamp=timestamp,
                   is_extended_id=bool(getattr(msg, "is_extended_id", True)),
                   is_remote_frame=bool(getattr(msg, "is_remote_frame", False)),
                   is_error_frame=bool(getattr(msg, "is_error_frame", False)),
                   dlc=getattr(msg, "dlc", None), channel=channel,
                   is_rx=bool(getattr(msg, "is_rx", True)))

    def __str__(self) -> str:
        f = F.split_arb(self.arbitration_id)
        return (f"{self.timestamp:8.4f} 0x{self.arbitration_id:08X} "
                f"api=0x{f.api:03X} dev={f.dev} {self.data.hex()}")


@dataclass(order=True)
class _Cand:
    sort_key: tuple = field(compare=True)
    t: float = field(compare=False, default=0.0)
    kind: str = field(compare=False, default="periodic")
    arb: int = field(compare=False, default=0)
    data: bytes = field(compare=False, default=b"")
    ci: Optional[int] = field(compare=False, default=None)
    api: Optional[int] = field(compare=False, default=None)
    seq: int = field(compare=False, default=0)


class SparkBusSim:
    """The bus a SparkAdmin talks to. Nothing here costs real time."""

    def __init__(self, controllers: Optional[Sequence[SimSpark]] = None,
                 clock: Optional[VirtualClock] = None, *,
                 channel: str = "sim0",
                 nonblocking_drain: str = "none",
                 rx_buffer: int = 1000,
                 congestion: float = 0.0,
                 congestion_seed: int = 0,
                 congestion_affects_tx: bool = False,
                 latency_s: float = 0.001,
                 phase_skew_s: float = 0.001,
                 forbid_bootloader: bool = True,
                 forbid_setpoints: bool = True,
                 forbid_protected_writes: bool = False,
                 max_frames: int = 2_000_000) -> None:
        if controllers is None:
            from .fleet import build_fleet
            controllers = build_fleet()
        self.controllers: List[SimSpark] = list(controllers)
        self.clock = clock or VirtualClock()
        self.channel = channel
        self.mode = nonblocking_drain
        self.rx_buffer = rx_buffer
        self.congestion_affects_tx = congestion_affects_tx
        self.latency_s = latency_s
        self.phase_skew_s = phase_skew_s
        self.forbid_bootloader = forbid_bootloader
        self.forbid_setpoints = forbid_setpoints
        self.forbid_protected_writes = forbid_protected_writes
        self.max_frames = max_frames

        self.start = self.clock.now
        self.rng = random.Random(congestion_seed)
        self.congestions: List[Congestion] = []
        if congestion:
            self.congestions.append(Congestion(congestion, self.start, None))

        self.sent: List[SimMessage] = []
        self.sent_at: List[Tuple[float, SimMessage]] = []
        self.delivered: List[Tuple[float, SimMessage]] = []
        self.dropped: List[Tuple[float, int, str]] = []
        self.ignored: List[Tuple[float, int, str]] = []
        self.log: List[Tuple[str, float, int]] = []

        self._last: dict = {}
        self._replies: List[dict] = []
        self._callbacks: List[Tuple[float, int, object]] = []
        self._backlog: deque = deque()
        self._seq = 0
        self._materialised = 0
        self._shut = False
        self.state = "active"
        self.clock.on_advance.append(self._settle_all)

    # -- python-can surface --------------------------------------------------

    @property
    def channel_info(self) -> str:
        return f"sparksim:{self.channel}"

    def shutdown(self) -> None:
        self._shut = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.shutdown()

    def __iter__(self):
        return self

    def __next__(self):
        m = self.recv(timeout=None)
        if m is None:
            raise StopIteration
        return m

    def send(self, msg, timeout: Optional[float] = None) -> None:
        if self._shut:
            raise BusShutdown("send() after shutdown()")
        now = self.clock.now
        m = SimMessage.coerce(msg, timestamp=now, channel=self.channel)
        self.sent.append(m)
        self.sent_at.append((now, m))
        self.log.append(("send", now, m.arbitration_id))
        base = F.base_of(m.arbitration_id)

        if self.forbid_bootloader and base == F.ENTER_SWDL_CAN_BOOTLOADER:
            raise BootloaderFrameForbidden(
                f"0x{m.arbitration_id:08X} is ENTER_SWDL_CAN_BOOTLOADER; it halts the "
                "controller and nothing in this repo may send it")
        if self.forbid_setpoints and base in F.SETPOINT_BASES:
            raise SetpointFrameForbidden(
                f"0x{m.arbitration_id:08X} is a setpoint frame; spark_admin promises "
                "it never commands motion")
        if base == F.PARAM_WRITE and len(m.data) >= 1 and m.data[0] in F.PROTECTED_PARAMS:
            self.ignored.append((now, m.arbitration_id, "protected"))
            if self.forbid_protected_writes:
                raise ProtectedWriteReachedBus(
                    f"PARAM_WRITE for protected parameter {m.data[0]} reached the bus; "
                    "the hard-limit interlock must be refused in the driver")
        if not m.is_extended_id:
            self.ignored.append((now, m.arbitration_id, "standard id"))
            return

        # PROBE-LOG section 11: one GET_FIRMWARE addressed to id 17 brought all
        # eight back with sticky hasReset intact. The wake is bus-wide and
        # erases nothing, so any host frame releases every gated transmitter.
        for c in self.controllers:
            if c.awaiting_clear:
                c.awaiting_clear = False
                c.woken_by_traffic_at = now

        if self.congestion_affects_tx and self._in_congestion(now):
            if self.rng.random() < self._congestion_fraction(now):
                self.dropped.append((now, m.arbitration_id, "congestion"))
                self.log.append(("drop", now, m.arbitration_id))
                return

        targets = self._targets(m)
        if not targets:
            self.ignored.append((now, m.arbitration_id, "no such device"))
            return
        for ci, c in targets:
            lat = (self.latency_s if c.behaviour.reply_latency_s is None
                   else c.behaviour.reply_latency_s)
            replies = c.on_request(m, now, lat)
            for reason in c._ignored_out:
                self.ignored.append((now, m.arbitration_id, reason))
            for t, arb, data in replies:
                self._seq += 1
                self._replies.append({"t": _q(t), "arb": arb, "data": bytes(data),
                                      "seq": self._seq, "ci": ci})

    def _targets(self, m: SimMessage):
        base = F.base_of(m.arbitration_id)
        if m.arbitration_id == F.DISABLE_BROADCAST:
            # arbID 0 carries no device type, manufacturer or device id -- it is
            # addressed to the whole bus by construction, which is what makes it
            # reach a controller whose id is wrong, duplicated or unknown.
            return list(enumerate(self.controllers))
        if base in (F.IDENTIFY_UNIQUE, F.IDENTIFY):
            return list(enumerate(self.controllers))
        f = F.split_arb(m.arbitration_id)
        if base == F.SET_CAN_ID and f.dev == 0:
            # id 0 is the broadcast address: every device hears it, all refuse
            return list(enumerate(self.controllers))
        return [(i, c) for i, c in enumerate(self.controllers)
                if c.dev == f.dev and c.device_type == f.device_type and c.mfr == f.mfr]

    def recv(self, timeout: Optional[float] = None) -> Optional[SimMessage]:
        if self._shut:
            return None
        if timeout is not None and timeout == 0:
            if self.mode == "none":
                return None
            self._fill_backlog(self.clock.now)
            return self._backlog.popleft() if self._backlog else None

        deadline = math.inf if timeout is None else self.clock.now + timeout
        while True:
            if self.mode == "backlog":
                self._fill_backlog(self.clock.now)
                if self._backlog:
                    return self._backlog.popleft()
            cb_t = self._next_callback_time()
            cand = self._earliest_candidate()
            if (cb_t is not None and cb_t <= deadline + EPS
                    and (cand is None or cb_t <= cand.t)):
                self._run_due_callbacks(cb_t)
                continue
            if cand is None or cand.t > deadline + EPS:
                if deadline == math.inf:
                    return None
                self._expire(deadline, timeout)
                return None
            self.clock.advance_to(cand.t)
            self._consume(cand)
            reason = self._suppressed(cand)
            if reason:
                self.dropped.append((cand.t, cand.arb, reason))
                self.log.append(("drop", cand.t, cand.arb))
                continue
            msg = SimMessage(cand.arb, cand.data, timestamp=cand.t,
                             channel=self.channel, is_remote_frame=False)
            self.delivered.append((cand.t, msg))
            self.log.append(("recv", cand.t, cand.arb))
            return msg

    def _expire(self, deadline: float, timeout: float) -> None:
        """Consume the rest of the window, guaranteeing the caller's loop ends."""
        before = self.clock.now
        self.clock.advance_to(deadline)
        if self.clock.now <= before and timeout and timeout > 0:
            self.clock.advance(max(float(timeout), EPS))

    # -- scheduler -----------------------------------------------------------

    def _settle_all(self, now: float) -> None:
        for c in self.controllers:
            c._settle(now)

    def _phase(self, i: int, period_s: float) -> float:
        """Per-source cadence offset. Index 0 is skewed too, so no source lands
        on the 0.000 grid a measurement window closes on and every healthy device
        measures to its nominal period exactly."""
        return ((i + 1) * self.phase_skew_s) % period_s

    def _next_due(self, t0: float, phase: float, period: float) -> float:
        n = math.floor((t0 - phase) / period) + 1
        t = _q(phase + n * period)
        if t <= t0 + 1e-12:
            t = _q(t + period)
        return t

    def _next_due_at_or_after(self, t: float, phase: float, period: float) -> float:
        k = math.ceil((t - phase) / period - 1e-9)
        due = _q(phase + k * period)
        return due if due >= t - 1e-12 else _q(due + period)

    def _earliest_candidate(self) -> Optional[_Cand]:
        best = None
        for r in self._replies:
            key = (r["t"], 0, r["arb"], r["ci"] if r["ci"] is not None else -1, r["seq"])
            if best is None or key < best.sort_key:
                best = _Cand(key, r["t"], "reply", r["arb"], r["data"], r["ci"],
                             None, r["seq"])
        now = self.clock.now
        for i, c in enumerate(self.controllers):
            for api in c.enabled_apis():
                period = c.period_ms(api) / 1000.0
                phase = self._phase(i, period)
                t0 = self._last.get((i, api), self.start)
                t = self._next_due(t0, phase, period)
                if self.mode == "none" and t < now:
                    # a frame due while nobody was listening is never delivered
                    t = self._next_due_at_or_after(now, phase, period)
                arb = F.arb(api, c.dev, c.device_type, c.mfr)
                key = (t, 1, arb, i, 0)
                if best is None or key < best.sort_key:
                    best = _Cand(key, t, "periodic", arb, b"", i, api, 0)
        return best

    def _consume(self, cand: _Cand) -> None:
        self._materialised += 1
        if self._materialised > self.max_frames:
            raise SimFrameBudgetExceeded(
                f"{self._materialised} frames materialised over "
                f"{self.clock.now - self.start:.1f} virtual seconds with "
                f"{len(self.controllers)} controllers; a window this long is a bug, "
                "not a slow test")
        if cand.kind == "reply":
            self._replies = [r for r in self._replies if r["seq"] != cand.seq]
        else:
            self._last[(cand.ci, cand.api)] = cand.t
            c = self.controllers[cand.ci]
            payload = c.payload_for(cand.api, cand.t)
            if payload is None:
                # Nothing to say on this api: a SPARK MAX has no Flex STATUS_0.
                # Emitting an empty frame instead would put the api on the wire
                # with no content, which reads as a present-but-broken device.
                self._replies = [r for r in self._replies if r["seq"] != cand.seq]
                return
            cand.data = payload

    def _suppressed(self, cand: _Cand) -> Optional[str]:
        if cand.ci is not None:
            c = self.controllers[cand.ci]
            reason = c.silence_reason(cand.t, cand.api)
            # PROBE-LOG section 9: with the bus in its post-cycle silence,
            # GET_FIRMWARE to all eight was answered by all eight. The gated
            # state stops periodic frames; it does not stop replies.
            if reason == "awaiting_clear" and cand.kind == "reply":
                reason = None
            if reason:
                return reason
            if cand.kind == "periodic" and c.period_ms(cand.api) is None:
                return "disabled"
        if self._in_congestion(cand.t):
            if self.rng.random() < self._congestion_fraction(cand.t):
                return "congestion"
        return None

    def _in_congestion(self, t: float) -> bool:
        return any(w.covers(t) for w in self.congestions)

    def _congestion_fraction(self, t: float) -> float:
        for w in self.congestions:
            if w.covers(t):
                return w.fraction
        return 0.0

    def _fill_backlog(self, upto: float) -> None:
        while True:
            cb_t = self._next_callback_time()
            cand = self._earliest_candidate()
            if cb_t is not None and cb_t <= upto and (cand is None or cb_t <= cand.t):
                self._run_due_callbacks(cb_t)
                continue
            if cand is None or cand.t > upto + EPS:
                return
            self._consume(cand)
            reason = self._suppressed(cand)
            if reason:
                self.dropped.append((cand.t, cand.arb, reason))
                self.log.append(("drop", cand.t, cand.arb))
                continue
            if len(self._backlog) >= self.rx_buffer:
                old = self._backlog.popleft()
                self.dropped.append((old.timestamp, old.arbitration_id, "overrun"))
                self.log.append(("drop", old.timestamp, old.arbitration_id))
            msg = SimMessage(cand.arb, cand.data, timestamp=cand.t,
                             channel=self.channel, is_remote_frame=False)
            self._backlog.append(msg)
            self.delivered.append((cand.t, msg))
            self.log.append(("recv", cand.t, cand.arb))

    # -- scheduled callbacks -------------------------------------------------

    def _next_callback_time(self) -> Optional[float]:
        return self._callbacks[0][0] if self._callbacks else None

    def _run_due_callbacks(self, upper_bound: float) -> None:
        while self._callbacks and self._callbacks[0][0] <= upper_bound + EPS:
            at, _, fn = heapq.heappop(self._callbacks)
            self.clock.advance_to(at)
            fn(self)

    def schedule(self, at: float, fn) -> None:
        """Run fn(sim) when virtual time reaches `at`. The escape hatch for
        anything the injection API above does not cover."""
        self._seq += 1
        heapq.heappush(self._callbacks, (_q(at), self._seq, fn))

    def _at(self, at, fn) -> None:
        if at is None:
            fn(self)
        else:
            self.schedule(at, fn)

    # -- device lookup -------------------------------------------------------

    def controllers_at(self, dev) -> List[SimSpark]:
        if isinstance(dev, SimSpark):
            return [dev]
        return [c for c in self.controllers if c.dev == dev]

    def controller(self, dev) -> SimSpark:
        found = self.controllers_at(dev)
        if len(found) > 1:
            raise AmbiguousDeviceError(
                f"CAN id {dev} is answered by {len(found)} controllers "
                f"({', '.join(c.serial for c in found)}); pass the SimSpark to "
                "disambiguate")
        if not found:
            raise LookupError(f"no controller with CAN id {dev}")
        return found[0]

    def add(self, spark: SimSpark) -> SimSpark:
        self.controllers.append(spark)
        return spark

    def _each(self, dev, broadcast=False) -> List[SimSpark]:
        if broadcast:
            found = self.controllers_at(dev)
            if not found:
                raise LookupError(f"no controller with CAN id {dev}")
            return found
        return [self.controller(dev)]

    # -- timeline injection: device presence ---------------------------------

    def silence(self, dev, start, end, apis=None, reason="dropout") -> None:
        apis = None if apis is None else frozenset(
            apis if isinstance(apis, (list, tuple, set, frozenset)) else [apis])
        for c in self._each(dev, broadcast=True):
            c.silences.append(Silence(float(start), float(end), apis, reason))

    def bus_off(self, dev, at=None) -> None:
        for c in self._each(dev, broadcast=True):
            c.bus_off_at = self.clock.now if at is None else float(at)

    def power_cycle(self, dev, at=None, offline_s=0.0, has_reset=True) -> None:
        targets = self._each(dev, broadcast=True)

        def _do(s):
            t = s.clock.now
            for c in targets:
                c.reboot(t, has_reset)
                if offline_s:
                    c.silences.append(Silence(t, t + float(offline_s), None, "reboot"))
        self._at(at, _do)

    def silent_until_cleared(self, dev, at=None) -> None:
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.awaiting_clear = True
        self._at(at, _do)

    # -- timeline injection: cadence and config ------------------------------

    def set_period(self, dev, api, ms, at=None) -> None:
        targets = self._each(dev)

        def _do(s):
            for c in targets:
                c.set_period_ms(api, ms)
        self._at(at, _do)

    def disable_frame(self, dev, api, at=None) -> None:
        self.set_period(dev, api, 0, at=at)

    def revert_to_defaults(self, dev, at=None) -> None:
        targets = self._each(dev)

        def _do(s):
            for c in targets:
                for pid in list(F.API_FOR_PERIOD_PARAM):
                    c.ram.pop(pid, None)
                    c.flash.pop(pid, None)
                c.periods_ms.clear()
        self._at(at, _do)

    def set_can_id(self, dev, new_id, at=None, ram_only=False) -> None:
        targets = self._each(dev)

        def _do(s):
            for c in targets:
                c.dev = new_id
                c.ram[F.PARAM_CAN_ID] = new_id
                if not ram_only:
                    c.flash[F.PARAM_CAN_ID] = new_id
        self._at(at, _do)

    def revert_to_id_zero(self, dev, at=None) -> None:
        self.set_can_id(dev, 0, at=at)

    # -- timeline injection: bus conditions ----------------------------------

    def congestion(self, fraction, start=0.0, end=None, seed=None) -> None:
        if seed is not None:
            self.rng = random.Random(seed)
        self.congestions.append(Congestion(float(fraction), float(start),
                                           None if end is None else float(end)))

    # -- timeline injection: device state ------------------------------------

    def set_fault(self, dev, faults=(), warnings=(), sticky=True, at=None) -> None:
        fm = F.as_fault_mask(faults)
        wm = F.as_warn_mask(warnings)
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.faults |= fm
                c.warnings |= wm
                if sticky:
                    c.sticky_faults |= fm
                    c.sticky_warnings |= wm
        self._at(at, _do)

    def clear(self, dev, at=None) -> None:
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.faults = c.warnings = c.sticky_faults = c.sticky_warnings = 0
        self._at(at, _do)

    def brownout(self, dev, at, volts=6.0, duration=0.25, reboot=False) -> None:
        targets = self._each(dev, broadcast=True)
        saved = {id(c): c.volts for c in targets}

        def _start(s):
            for c in targets:
                c.volts = volts
                c.warnings |= F.WARN["brownout"]
                c.sticky_warnings |= F.WARN["brownout"]

        def _end(s):
            for c in targets:
                c.volts = saved[id(c)]
                c.warnings &= ~F.WARN["brownout"]
                if reboot:
                    c.reboot(s.clock.now, has_reset=True)
        self._at(at, _start)
        self.schedule((self.clock.now if at is None else at) + duration, _end)

    def brownout_stages(self, at=None, floor_v=6.0) -> None:
        """A rail sag through the roboRIO's three brownout stages.

        WPILib documents it as staged, and 6.8 V is NOT the disable threshold:
        "When the voltage drops below 6.8V, the 6V output on the PWM pins will
        start to drop." Output disable comes lower, and at stage 2 the roboRIO
        "sends CAN motor controllers an explicit disable command" -- which is
        the arbID 0 broadcast, so a brownout ends in the same frame a stop does.

        The sequence matters because a tool that only watches the rail sees a
        low number, while what actually stopped the motors was a frame.
        """
        t0 = self.clock.now if at is None else float(at)
        targets = list(self.controllers)
        for k, volts in enumerate((10.5, 8.2, 6.9, floor_v)):
            def _sag(sim, v=volts):
                for c in targets:
                    c.volts = v
                    if v <= 6.8:
                        c.sticky_warnings |= F.WARN["brownout"]
            self._at(t0 + k * 0.08, _sag)

        def _disable(sim):
            # stage 2: the explicit CAN disable, not merely ceasing to command
            for c in targets:
                c.disable_broadcasts += 1
                c.applied = 0.0
        self._at(t0 + 4 * 0.08, _disable)

    def bus_wedged_flat(self, at=None) -> None:
        """The CAN bus inaccessible with utilisation flat at zero.

        Reported on WPILib 2026.2.1 with REVLib 2026.0.1 and one SPARK Flex:
        "doing a Restart Robot Code from the DriverStation leads to a state
        where the CANbus seems to be locked up/inaccessible. A second Restart
        Robot Code clears the problem."

        Distinct from every controller being dead: nothing is transmitting at
        all, host frames included, so the tell is the ABSENCE of the host's own
        traffic rather than the absence of replies.
        """
        def _do(sim):
            for c in sim.controllers:
                c.offline = True
        self._at(at, _do)

    def accel_transient(self, dev, peak_a=None, at=None) -> None:
        """The current fault a hard acceleration constraint provokes.

        Reported on a swerve steering axis: "pushing it to the max causes the
        SPARK MAX to flag a current fault at times", and backing the
        acceleration off removes it. Intermittent by nature -- it is the
        transient at the start of a move, not a steady state -- which is why a
        window that samples the average never sees it.
        """
        t0 = self.clock.now if at is None else float(at)
        peak = 132.0 if peak_a is None else float(peak_a)
        targets = self._each(dev, broadcast=True)
        for k, (amps, applied) in enumerate(
                ((peak, 0.55), (peak * 0.55, 0.4), (28.0, 0.25), (12.0, 0.15))):
            def _step(sim, a=amps, ap=applied):
                for c in targets:
                    c.amps, c.applied = a, ap
                    c.amps_pinned = True
            self._at(t0 + k * 0.06, _step)

    def heartbeat_gap(self, seconds=0.15, at=None) -> None:
        """A gap in the enable heartbeat longer than the spec's window.

        FIRST CAN Device Specification, on the universal heartbeat at
        0x01011840 sent every 20 ms: "If 100 ms has passed since this packet was
        received, the robot program can be considered hung, and devices should
        act as if the robot has been disabled."

        The spec puts that duty on the DEVICE, not the host: a node driving an
        actuator "must implement a way to verify that the robot is enabled and
        that commands originate with the main robot controller". So a host that
        stalls -- a long GC pause, a blocked thread, a wedged USB adapter -- has
        to end with the motors stopping, and the controller is what enforces it.
        """
        targets = list(self.controllers)   # the heartbeat is bus-wide, not per-id

        def _do(sim):
            for c in targets:
                # Set ONLY the starvation window. Zeroing applied here would
                # make the injector do the controller's job, and a test built on
                # it would pass with the enforcement deleted.
                c.heartbeat_starved_until = sim.clock.now + float(seconds)
        self._at(at, _do)

    def flex_status_timeout(self, dev, at=None) -> None:
        """The Flex-only status-frame timeout defect of 2024.

        REV: "SPARK Flex firmware v25.0.2 has been released which includes a fix
        for the status timeouts ... The 500ms timeout was put in as a stopgap
        during 2024 for when users were experiencing this same exact issue."
        A Flex on pre-25.0.2 firmware stops broadcasting status for longer than
        the library's validity window, so the host reads stale telemetry and
        believes it current. https://www.chiefdelphi.com/t/480555
        """
        for c in self._each(dev, broadcast=True):
            self.silence(c.dev, 0.0 if at is None else float(at),
                         (0.0 if at is None else float(at)) + 0.6,
                         apis=[F.API_STATUS_0, F.API_STATUS_1])

    def drop_config_write(self, dev, param_id, at=None) -> None:
        """One missed configuration message, and the sensor fault it causes.

        REV's own root cause for the 2024 SPARK Flex Sensor Fault epidemic:
        "due to a missed configuration message (from high bus utilization, poor
        CAN wiring, etc.) the Flex gets into an invalid sensor configuration
        resulting in the Sensor Fault." One dropped write, not a broken device.
        https://www.chiefdelphi.com/t/456113
        """
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.behaviour.drop_write_responses_for = frozenset(
                    set(c.behaviour.drop_write_responses_for) | {param_id})
                c.invalid_sensor_config = True
        self._at(at, _do)

    def current_chop(self, dev, at=None, cycles=6, period_s=0.05,
                     commanded=0.45) -> None:
        """The SECOND factory threshold, distinct from the Smart Current Limit.

        kCurrentChop, parameter 11, default 115 A: "If the half bridge detects
        this current limit, it will disable the motor driver for a fixed amount
        of time set by kCurrentChopCycles. This is a low sophistication current
        control." So applied output collapses while current is high -- which
        reads exactly like the dropout signature unless you know the threshold.
        https://docs.revrobotics.com/brushless/spark-max/parameters
        """
        targets = self._each(dev, broadcast=True)

        def _do(sim):
            for c in targets:
                c.amps = 118.0
                c.amps_pinned = True
                c.chopping = True
                c.applied = 0.0
        self._at(at, _do)

        # The h-bridge goes off for kCurrentChopCycles and then back on, so the
        # signature is an OSCILLATION, not a sustained dead output. REV publish
        # no fault condition for overcurrent on either product, so nothing on
        # the bus announces it: the applied-output pattern is the only evidence.
        t0 = (self.clock.now if at is None else float(at))
        for k in range(1, cycles * 2 + 1):
            on = bool(k % 2)

            def _flip(sim, on=on):
                for c in targets:
                    c.applied = (commanded if on else 0.0)
            self._at(t0 + k * period_s, _flip)

    def limit_holds_at_value(self, dev, amps=80.0, at=None) -> None:
        """What the Smart Current Limit looks like when it is working.

        REV's software lead: the limit holds the motor AT the limit value and
        scales duty cycle to do it. So a controller pinned at its limit shows a
        steady current at the limit and a REDUCED applied output -- it is being
        regulated, not merely reporting a number, and that is not a fault.
        https://www.chiefdelphi.com/t/350542 (post 27)
        """
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.amps = float(amps)
                c.amps_pinned = True
                c.applied = 0.35         # scaled down by the regulator
                c.regulating = True
        self._at(at, _do)

    def stuck_current(self, dev, amps, at=None) -> None:
        """Pin a controller's reported output current, load or no load.

        CD 373283: three teams saw SPARK MAX output current stuck at a constant
        implausible value -- 72 A, 85 A and 125 A -- with the motor unloaded and
        the applied output varying. The number is not a measurement, and a tool
        that treats it as one reports a load that is not there.
        """
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.amps = float(amps)
                c.amps_pinned = True
        self._at(at, _do)

    def loose_dock(self, dev, at=None) -> None:
        """The Vortex-to-Flex docking joint not fully seated.

        REV, CD 453509: "The EEPROM Fault and Other Error can result from a loose
        connection between the Vortex and the Flex. Please reseat them and make
        sure that the docking screws are fully installed." The bus signature is
        those two bits plus a sensor fault, appearing after the motor runs.
        """
        targets = self._each(dev, broadcast=True)

        def _do(s):
            for c in targets:
                c.faults |= F.FAULT["escEeprom"] | F.FAULT["other"]
                c.sticky_faults |= F.FAULT["escEeprom"] | F.FAULT["other"]
                c.faults |= F.FAULT["sensor"]
                c.sticky_faults |= F.FAULT["sensor"]
        self._at(at, _do)

    def thermal_foldback(self, dev, at, temp_c=95, duration=2.0) -> None:
        targets = self._each(dev, broadcast=True)
        saved = {id(c): c.temp_c for c in targets}

        def _start(s):
            for c in targets:
                c.temp_c = temp_c
                c.faults |= F.FAULT["temperature"]

        def _end(s):
            for c in targets:
                c.temp_c = saved[id(c)]
                c.faults &= ~F.FAULT["temperature"]
        self._at(at, _start)
        self.schedule((self.clock.now if at is None else at) + duration, _end)

    # -- observability -------------------------------------------------------

    def sent_ids(self) -> List[int]:
        return [m.arbitration_id for m in self.sent]

    def sends_matching(self, base) -> List[Tuple[float, SimMessage]]:
        return [(t, m) for t, m in self.sent_at if F.base_of(m.arbitration_id) == base]

    def frames(self, dev=None, api=None, since=0.0, until=None):
        if isinstance(dev, SimSpark):
            dev = dev.dev
        out = []
        for t, m in self.delivered:
            if t < since or (until is not None and t > until):
                continue
            f = F.split_arb(m.arbitration_id)
            if dev is not None and f.dev != dev:
                continue
            if api is not None and f.api != api:
                continue
            out.append((t, m))
        return out

    def measured_period_ms(self, dev, api, seconds, start=None):
        """The same measurement status_period_ms makes, on the same virtual clock."""
        if start is not None:
            self.clock.advance_to(start)
        c = dev if isinstance(dev, SimSpark) else None
        target = F.arb(api, c.dev if c else dev, c.device_type if c else 0x02,
                       c.mfr if c else 0x05)
        n = 0
        end = self.clock.now + seconds
        while self.clock.now < end:
            m = self.recv(timeout=max(0.0, end - self.clock.now))
            if m is not None and m.arbitration_id == target:
                n += 1
        return round(seconds * 1000.0 / n, 1) if n else None

    def elapsed(self) -> float:
        return self.clock.now - self.start

    def explain(self, dev=None) -> str:
        """A readable timeline: what was sent, dropped and delivered."""
        if isinstance(dev, SimSpark):
            dev = dev.dev
        lines = [f"--- sparksim timeline, {self.elapsed():.3f} virtual s, "
                 f"{len(self.controllers)} controller(s) ---"]
        for c in self.controllers:
            if dev is None or c.dev == dev:
                lines.append(
                    f"  dev {c.dev} {c.serial}: s0={c.period_ms(F.API_STATUS_0)}ms "
                    f"s1={c.period_ms(F.API_STATUS_1)}ms ram={c.ram} flash={c.flash} "
                    f"faults={F.fault_names(c.faults)} "
                    f"sticky={F.fault_names(c.sticky_faults)}")
        counts = {}
        for t, m in self.delivered:
            f = F.split_arb(m.arbitration_id)
            if dev is None or f.dev == dev:
                counts[(f.dev, f.api)] = counts.get((f.dev, f.api), 0) + 1
        for (d, api), n in sorted(counts.items()):
            lines.append(f"  delivered dev {d} api 0x{api:03X}: {n} frames")
        drops = {}
        for t, arb, why in self.dropped:
            f = F.split_arb(arb)
            if dev is None or f.dev == dev:
                drops[(f.dev, why)] = drops.get((f.dev, why), 0) + 1
        for (d, why), n in sorted(drops.items()):
            lines.append(f"  dropped dev {d} ({why}): {n} frames")
        for t, m in self.sent_at:
            f = F.split_arb(m.arbitration_id)
            if dev is None or f.dev == dev:
                lines.append(f"  sent {t:8.4f} 0x{m.arbitration_id:08X} "
                             f"api=0x{f.api:03X} dev={f.dev} {m.data.hex()}")
        for t, arb, why in self.ignored:
            f = F.split_arb(arb)
            if dev is None or f.dev == dev:
                lines.append(f"  ignored {t:8.4f} 0x{arb:08X}: {why}")
        return "\n".join(lines)
