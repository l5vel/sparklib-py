"""What this host is allowed to put on a live SPARK bus, and how it listens.

Two objects. `WireInjector` transmits failure signatures onto the real wire;
`Sniffer` records everything that crossed it, host frames and device frames kept
apart.

The injector's safety rule is one sentence: **it may only emit frames a SPARK
itself emits.** Periodic status frames and response frames are things a
controller broadcasts, so no controller consumes them and none can act on one.
Every command frame -- a setpoint, a heartbeat, a reset, the bootloader entry --
is refused by `emit()` and is listed in FORBIDDEN_BASES, which
test_wire_discipline.py also asserts against the frames the `spark` subcommands
actually send. That list is not theoretical: a blind RTR sweep across all 1024
API values halted device 17 on this fleet by reaching
ENTER_SWDL_CAN_BOOTLOADER, and only a motor-rail power cycle brought it back
(docs/spark/runs/-rig-flex-probe-log.md).

The congestion filler is the one exception, and it is safe for the opposite
reason: its arbitration id carries a manufacturer no device on this bus claims,
so nothing decodes it at all.

Why an injected frame reaches the driver: socketcan enables CAN_RAW_LOOPBACK by
default, so a frame sent on one socket is delivered to every *other* socket on
that interface, and CAN_RAW_RECV_OWN_MSGS only suppresses it on the socket that
sent it. `SparkAdmin` opens its own socket, so it receives what the injector
writes. The kernel marks locally created frames MSG_DONTROUTE, which python-can
surfaces as `Message.is_rx is False` -- that is how `Sniffer` tells a frame this
host wrote from a frame a controller sent, and it is the only difference between
the two. Nothing in `spark_admin` reads `is_rx`, which is what makes the
injection work and is worth knowing on its own.

tests/hardware/test_wire_injection.py asserts the loopback premise before it
asserts anything else; if that control fails, every injection in the module is
measuring nothing.
"""
from __future__ import annotations

import collections
import contextlib
import threading
import time

import can

from sparksim import frames as F

# -- frames that must never leave this host ------------------------------------
# The 26.1.6 setpoints and the SparkMax-era ones controller.py still
# builds, both heartbeats, the two resets, the software-download frames and the
# bootloader entry. Arbitration ids from REV-Specs spark-frames-2.1.0; the
# roboRIO heartbeat is device type 1 / manufacturer 1 and so is not in REV's
# file -- it is the catalogue's 0x01011840.
FORBIDDEN_BASES = {
    0x02050000: "VELOCITY_SETPOINT",
    0x02050080: "DUTY_CYCLE_SETPOINT",
    0x02050100: "POSITION_SETPOINT",
    0x02050140: "VOLTAGE_SETPOINT",
    0x02050180: "CURRENT_SETPOINT",
    0x02050200: "MAXMOTION_POSITION_SETPOINT",
    0x02050240: "MAXMOTION_VELOCITY_SETPOINT",
    0x02050480: "SparkMax velocity setpoint (controller.py)",
    0x02050C80: "SparkMax position setpoint (controller.py)",
    0x02050540: "RESET_SAFE_PARAMETERS",
    0x020505C0: "COMPLETE_FACTORY_RESET",
    0x02052700: "SWDL_DATA",
    0x02052740: "SWDL_CHECKSUM",
    0x02052800: "SET_PRIMARY_ENCODER_POSITION",
    0x02052880: "SET_I_ACCUMULATION",
    0x020528C0: "SET_ANALOG_POSITION",
    0x02052900: "SET_EXT_OR_ALT_ENCODER_POSITION",
    0x02052940: "SET_DUTY_CYCLE_POSITION",
    0x02052C80: "SECONDARY_HEARTBEAT",
    0x02057FC0: "ENTER_SWDL_CAN_BOOTLOADER",
    0x01011840: "roboRIO universal heartbeat",
    # Pre-25 commands. Every entry above is a 25+ arbitration id, so without
    # these the denylist covers only one generation. Derived from IDENTIFY_UNIQUE
    # (0x02051D80, api 0x076), which anchors the pre-25 command numbering:
    # arb = 0x02050000 | (api << 6).
    # The burn flash is denied because it WORKS, which is a stronger reason than
    # the unverified one this entry used to carry. rig-max, firmware
    # 24.0.1: carrying a two-byte magic 15011 little-endian, this frame is
    # answered 0x00 and commits the parameter table, ids 0-133, to flash
    # (pre25.burn_flash_api, how=HARDWARE). It is a command a controller acts on,
    # which is this list's whole rule, and what it does is spend one of a finite
    # number of flash cycles and make whatever happens to be in RAM permanent on
    # a live fleet. A zero-length frame is ignored by the device, but the guard
    # refuses the base rather than a payload, so every form of it stays out.
    0x02051C80: "pre-25 CMD_API_BURN_FLASH (api 0x072)",
    0x02051D00: "pre-25 CMD_API_FACTORY_DEFAULT (api 0x074)",
    0x02051D40: "pre-25 CMD_API_FACTORY_RESET (api 0x075)",
    # Parameter access puts the parameter id IN the arbitration id, so one base
    # entry covers one parameter. This names parameter 0, the CAN id. The rest of
    # api class 48 is refused by the allowlist below anyway: a parameter frame is
    # a command, not something a device broadcasts.
    0x0205C000: "pre-25 parameter 0 (kCanID) write",
}

# Response frames a controller sends. Injectable for the same reason a status
# frame is: they travel device to host, so no device is listening for one.
RESPONSE_BASES = {
    F.PARAM_WRITE_RESP: "PARAMETER_WRITE_RESPONSE",
    F.PERSIST_RESP: "PERSIST_PARAMETERS_RESPONSE",
    F.SET_STATUSES_ENABLED_RESP: "SET_STATUSES_ENABLED_RESPONSE",
}

# Legacy status frames are injectable for the same reason the modern ones are: a
# device broadcasts them, so nothing on the bus acts on one. Faults live in 0x060
# on pre-25, so the injector needs these to reach that generation at all. Pre-25
# COMMAND apis are denied above; add them there, never here.
LEGACY_STATUS_APIS = frozenset({0x060, 0x061, 0x062, 0x063,
                               0x064, 0x065, 0x066, 0x067})
INJECTABLE_APIS = (frozenset(F.STATUS_APIS) | LEGACY_STATUS_APIS
                   | {F.API_UNIQUE_ID})
# A REV PDH broadcasts the same api space on device type 8. It is here
# because inventory() filters on manufacturer alone, so a PDH frame on a
# SPARK's id is the phantom-controller failure -- and it is still a frame
# a device emits, so nothing on the bus acts on it.
INJECTABLE_DEVICE_TYPES = frozenset({F.DEVICE_TYPE_MOTOR_CONTROLLER,
                                     F.DEVICE_TYPE_PDH})

# Congestion filler. Manufacturer 0xFF is unassigned, so neither the SPARKs
# (manufacturer 5) nor a CTRE device (4) decodes it, and device type is never 0,
# which is the FRC broadcast space that carries Disable and Halt. The low
# variant sorts above every SPARK frame and so only fills the gaps; the high
# variant sorts below them and wins arbitration, which is what actually delays a
# status frame.
FILLER_LOW_PRIORITY = 0x1FFFFFFF
FILLER_HIGH_PRIORITY = 0x01FF0000

_FILLER_PAYLOAD = bytes(8)

# Frames a controller broadcasts on its own. `last_status_at` tracks only these,
# so a caller can watch for the bus going quiet without its own GET_FIRMWARE
# probe registering as traffic resuming -- the request and the reply share an
# arbitration id, and both are REV motor-controller frames.
_BROADCAST_APIS = frozenset(F.STATUS_APIS) | {F.API_UNIQUE_ID}


def forbidden_frames(messages):
    """Every frame in `messages` whose base is one this host may never send."""
    return [m for m in messages
            if (m.arbitration_id & ~0x3F) in FORBIDDEN_BASES
            or m.arbitration_id in FORBIDDEN_BASES]


def _base(arb):
    return arb & ~0x3F


class InjectionRefused(RuntimeError):
    """Raised when an injection would emit a frame a controller acts on."""


# -- listening -----------------------------------------------------------------

class Sniffer:
    """Everything on the wire while a block runs, with the host's own frames
    kept apart from the controllers'.

    Opens a second socket on the same interface, so it sees both what the
    controllers broadcast and what `SparkAdmin` transmits. Used as a context
    manager; the reader runs in a thread because the code under observation is
    the thing blocking the main one.

    `maxlen` bounds the recording, for the staged waits that listen for minutes
    on a bus carrying five hundred frames a second. `last_spark_at` is kept on
    every frame so a caller can watch for the bus going quiet without rescanning
    what it has already stored.
    """

    def __init__(self, channel, maxlen=None):
        self.channel = channel
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.frames = collections.deque(maxlen=maxlen)
        self.last_at = None
        self.last_spark_at = None
        self.last_status_at = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        while self.bus.recv(timeout=0) is not None:
            pass
        self.frames.clear()
        self.last_at = self.last_spark_at = self.last_status_at = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return False

    def _read(self):
        while not self._stop.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is None:
                continue
            self.frames.append(m)
            self.last_at = m.timestamp
            if F.is_spark(m.arbitration_id):
                self.last_spark_at = m.timestamp
                if F.api_of(m.arbitration_id) in _BROADCAST_APIS:
                    self.last_status_at = m.timestamp

    def close(self):
        with contextlib.suppress(Exception):
            self.bus.shutdown()

    # -- views -------------------------------------------------------------

    @property
    def from_this_host(self):
        """Frames the kernel marked as locally created."""
        return [m for m in self.frames if m.is_rx is False]

    @property
    def from_the_bus(self):
        return [m for m in self.frames if m.is_rx is not False]

    def bases_from_this_host(self):
        return {_base(m.arbitration_id) for m in self.from_this_host}

    def apis_from(self, dev):
        return {F.api_of(m.arbitration_id) for m in self.from_the_bus
                if F.dev_of(m.arbitration_id) == dev and F.is_spark(m.arbitration_id)}

    def explain(self, limit=20):
        """The frames, newest last, for a failure message."""
        rows = []
        for m in list(self.frames)[-limit:]:
            who = "host" if m.is_rx is False else "bus "
            rows.append(f"  {who} 0x{m.arbitration_id:08X} "
                        f"api 0x{F.api_of(m.arbitration_id):03X} "
                        f"dev {F.dev_of(m.arbitration_id):>2} "
                        f"{bytes(m.data).hex()}")
        return (f"{len(self.frames)} frame(s), last {len(rows)}:\n"
                + "\n".join(rows))


# -- transmitting --------------------------------------------------------------

class WireInjector:
    """Failure signatures transmitted onto the live bus from this host.

    Every emitter goes through `emit()`, which refuses any frame base outside
    the periodic status frames, the unique-id broadcast and the response frames
    -- i.e. outside what a controller itself transmits. `congestion()` is the
    single exception and carries an unassigned manufacturer instead.
    """

    def __init__(self, channel):
        self.channel = channel
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.sent = []

    def close(self):
        with contextlib.suppress(Exception):
            self.bus.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- the guard ---------------------------------------------------------

    @staticmethod
    def check(arb):
        """Refuse anything a controller would act on. Raises, never returns False."""
        base = _base(arb)
        if base in FORBIDDEN_BASES or arb in FORBIDDEN_BASES:
            raise InjectionRefused(
                f"0x{arb:08X} is {FORBIDDEN_BASES.get(base) or FORBIDDEN_BASES[arb]}"
                ", a frame controllers act on")
        if base in RESPONSE_BASES:
            return True
        fields = F.split_arb(arb)
        if fields.mfr != F.REV_MFR or fields.device_type not in INJECTABLE_DEVICE_TYPES:
            raise InjectionRefused(
                f"0x{arb:08X} is device type {fields.device_type} manufacturer "
                f"{fields.mfr}, which is not a REV device this bus carries; only "
                "congestion() may leave that space")
        if fields.api not in INJECTABLE_APIS:
            raise InjectionRefused(
                f"api 0x{fields.api:03X} is not a frame a REV device broadcasts; "
                "the injector may only emit frames a device emits")
        return True

    def emit(self, arb, data):
        self.check(arb)
        msg = can.Message(arbitration_id=arb, data=bytes(data), is_extended_id=True)
        self.bus.send(msg, timeout=0.5)
        self.sent.append(msg)
        return msg

    # -- one frame at a time -----------------------------------------------

    def status_0(self, dev, **fields):
        """A STATUS_0 payload of this host's choosing on `dev`."""
        return self.emit(F.arb(F.API_STATUS_0, dev), F.encode_status_0(**fields))

    def status_1(self, dev, **fields):
        return self.emit(F.arb(F.API_STATUS_1, dev), F.encode_status_1(**fields))

    def legacy_status_0(self, dev, **fields):
        """A pre-25 LEGACY_STATUS_0 payload on `dev`, faults and all.

        The only way to put a chosen fault on a pre-25 bus: faults live in this
        frame rather than in STATUS_1. `faults` and `sticky_faults` are 16-bit
        legacy masks -- build them with F.legacy_fault_mask, never F.fault_mask.
        """
        return self.emit(F.arb(F.API_LEGACY_STATUS_0, dev),
                         F.encode_status_0_sparkmax(**fields))

    def unique_id(self, dev, serial):
        return self.emit(F.arb(F.API_UNIQUE_ID, dev), F.encode_unique_id(serial))

    def pdh_status(self, dev, data=b"\x00" * 8):
        """A REV Power Distribution Hub frame on a SPARK's id.

        Device type 8, manufacturer 5. `inventory()` and `collect_status()`
        filter on manufacturer alone, so this is the frame that becomes a
        phantom SPARK.
        """
        return self.emit(F.arb(F.API_STATUS_0, dev, device_type=F.DEVICE_TYPE_PDH),
                         data)

    def param_write_response(self, dev, param_id, value, result=0):
        """The frame another host's provisioning loop leaves on the bus."""
        return self.emit(F.PARAM_WRITE_RESP | dev,
                         F.encode_param_resp(param_id, value, result))

    # -- streams -----------------------------------------------------------

    @contextlib.contextmanager
    def stream(self, frames, period_s, seconds=None):
        """Repeat `frames` -- (arb, data) pairs -- every `period_s` in a thread.

        Yields a counter object; leaving the block stops the stream. `seconds`
        caps the run even if the block is left open, so a hung test cannot
        transmit indefinitely onto a robot's bus.
        """
        for arb, _ in frames:
            self.check(arb)
        stop = threading.Event()
        state = _StreamState()
        deadline = time.monotonic() + seconds if seconds else None

        def run():
            while not stop.is_set():
                if deadline is not None and time.monotonic() > deadline:
                    state.capped = True
                    return
                for arb, data in frames:
                    try:
                        self.bus.send(can.Message(arbitration_id=arb,
                                                  data=bytes(data),
                                                  is_extended_id=True), timeout=0.5)
                        state.count += 1
                    except can.CanError as exc:
                        state.errors.append(repr(exc))
                        return
                stop.wait(period_s)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield state
        finally:
            stop.set()
            thread.join(timeout=2.0)

    def phantom(self, dev, serial, *, period_s=0.02, seconds=30.0, status_1=None,
                status_0=None):
        """A second controller answering on `dev`: STATUS_0, STATUS_1 and a serial.

        This is the duplicate-id signature of catalogue B1 as the driver meets
        it -- two distinct UNIQUE_ID payloads on one address -- without a
        controller being written to. On an address a real controller already
        owns, the two transmitters will occasionally start the same arbitration
        id in the same bit time and both lose it to an error frame; at 1 Mbit an
        8-byte extended frame is about 150 us, so a 20 ms stream overlaps a real
        10 ms STATUS_0 on the order of 1% of frames. Error counters recover far
        faster than that, which is why the collide gate exists but is not a
        refusal.
        """
        s0 = F.encode_status_0(**(status_0 or {}))
        s1 = F.encode_status_1(**(status_1 or {}))
        return self.stream([(F.arb(F.API_STATUS_0, dev), s0),
                            (F.arb(F.API_STATUS_1, dev), s1),
                            (F.arb(F.API_UNIQUE_ID, dev), F.encode_unique_id(serial))],
                           period_s, seconds)

    @contextlib.contextmanager
    def congestion(self, seconds, priority="low", max_frames=200000):
        """Fill the bus with frames no device on it decodes.

        `priority="low"` sorts above every SPARK frame and so only takes the
        gaps: measured on rig-flex at 1 Mbit, 24751 filler frames in five seconds
        left STATUS_1 at 20.0 ms and all eight controllers inventoried.
        `priority="high"` sorts below them and wins every arbitration, which is
        what CD 455329's logging library was doing: 29702 frames over the same
        five seconds took the fleet from eight controllers to none, with no
        dropped frame and no error counter moving anywhere.
        Both are capped by `seconds` and `max_frames`; the interface is left
        with `restart-ms 0` on this fleet, so a bus-off does not clear itself
        and the caller has to check the link afterwards.
        """
        arb = FILLER_HIGH_PRIORITY if priority == "high" else FILLER_LOW_PRIORITY
        fields = F.split_arb(arb)
        if fields.mfr in (0x00, F.REV_MFR, 0x04) or fields.device_type == 0:
            raise InjectionRefused(
                f"congestion filler 0x{arb:08X} is inside a space a device on "
                "this bus decodes; manufacturer 0 and device type 0 are the FRC "
                "broadcast space that carries Disable and Halt")
        stop = threading.Event()
        state = _StreamState()
        deadline = time.monotonic() + seconds
        msg = can.Message(arbitration_id=arb, data=_FILLER_PAYLOAD,
                          is_extended_id=True)

        def run():
            while not stop.is_set() and time.monotonic() < deadline:
                if state.count >= max_frames:
                    state.capped = True
                    return
                try:
                    self.bus.send(msg, timeout=0.5)
                    state.count += 1
                except can.CanError as exc:
                    state.errors.append(repr(exc))
                    return

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield state
        finally:
            stop.set()
            thread.join(timeout=seconds + 2.0)


class _StreamState:
    """How much a stream sent, and whether it stopped on its own."""

    def __init__(self):
        self.count = 0
        self.capped = False
        self.errors = []

    def __repr__(self):
        return (f"<stream frames={self.count} capped={self.capped} "
                f"errors={self.errors}>")
