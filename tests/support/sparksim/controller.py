"""One SPARK's state machine, including the state no CAN frame reveals.

The point of the whole simulator is here: RAM vs flash, the value a device
committed vs the value it echoed, latched vs clearable. Every confirmed driver
defect is a disagreement between what the driver concluded and what the device
actually did, and only a model with hidden ground truth can express that.

Config state and wire behaviour are one variable: `period_ms()` resolves out of
`ram`, so a parameter write that really commits changes the cadence the driver
measures, and one that only echoes does not.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from. import frames as F
from.faults import (ClearRecord, PersistRecord, SetCanIdRecord, Silence,
                     SparkBehaviour, WriteRecord)

Reply = Tuple[float, int, bytes]

# Apis with a modelled payload; LEGACY_STATUS_0 is opt-in, give it a period.
MODELLED_APIS = (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID) + F.STATUS_APIS[2:]


_TYPE_CODE = {"INT32": 1, "UINT32": 2, "FLOAT": 3, "BOOL": 4}


def _declared_type_codes():
    """{param_id: type code} from both declared files, so the sim reports the
    type production expects rather than Uint for everything."""
    from sparklib import admin as sa
    out = {}
    for product in ("sparkflex", "sparkmax"):
        defaults = sa.load_motor_defaults(controller_type=product)
        for role in ("steer", "drive"):
            for pid, st in sa.motor_settings(role, defaults).items():
                code = _TYPE_CODE.get(str(st.get("type") or "").upper())
                if code:
                    out[pid] = code
    return out


_DECLARED_TYPE_CODES = _declared_type_codes()


@dataclass
class SimSpark:
    dev: int
    serial: str
    firmware: str = "26.1.6"
    hw_rev: int = 3
    debug: int = 0
    model: int = 1
    device_type: int = F.DEVICE_TYPE_MOTOR_CONTROLLER
    mfr: int = F.REV_MFR

    volts: float = 12.6
    amps: float = 0.0
    temp_c: int = 30
    applied: float = 0.0
    inverted: bool = False
    heartbeat_lock: bool = False
    # Byte 6 of pre-25 STATUS_0, whole. None emits the value a driving
    # controller reports; set it to model one that will not drive.
    legacy_other_signals: Optional[int] = None
    hard_fwd: bool = False
    hard_rev: bool = False
    soft_fwd: bool = False
    soft_rev: bool = False
    follower: bool = False

    faults: int = 0
    warnings: int = 0
    sticky_faults: int = 0
    sticky_warnings: int = 0

    ram: dict = field(default_factory=dict)
    flash: dict = field(default_factory=dict)
    # {param_id: type code} overriding what type_of would otherwise report.
    param_types: dict = field(default_factory=dict)
    periods_ms: dict = field(default_factory=dict)

    behaviour: SparkBehaviour = field(default_factory=SparkBehaviour)

    deaf_until: float = -1.0
    offline: bool = False
    awaiting_clear: bool = False
    amps_pinned: bool = False
    disabled_at: Optional[float] = None
    disable_broadcasts: int = 0
    invalid_sensor_config: bool = False
    chopping: bool = False
    regulating: bool = False
    heartbeat_starved_until: float = -1.0
    controller_type: str = "sparkflex"
    # A 25+ controller emitting the deprecated 0x060 beacon. Documented in
    # frames 2.1.0; rig-flex was never observed sending one.
    legacy_beacon: bool = False
    velocity_rpm: float = 0.0
    woken_by_traffic_at: float = -1.0
    bus_off_at: Optional[float] = None
    silences: List[Silence] = field(default_factory=list)

    write_log: List[WriteRecord] = field(default_factory=list)
    persist_log: List[PersistRecord] = field(default_factory=list)
    clear_log: List[ClearRecord] = field(default_factory=list)
    can_id_log: List[SetCanIdRecord] = field(default_factory=list)
    identify_count: int = 0
    # Every pre-25 burn-flash frame this device received, whether or not the
    # model acted on it. A test asserts on the count to prove the driver did not
    # send one, which matters more now that the frame is known to commit.
    burn_flash_attempts: List[float] = field(default_factory=list)

    def __post_init__(self):
        self.serial = self.serial.upper()
        if self.ram and not self.flash:
            self.flash = dict(self.ram)
        self.ram.setdefault(F.PARAM_CAN_ID, self.dev)
        self.flash.setdefault(F.PARAM_CAN_ID, self.dev)
        self._pending: List[Tuple[float, int, int]] = []
        self._ignored_out: List[str] = []

    # -- pending RAM commits -------------------------------------------------

    def _settle(self, now: float) -> None:
        """Apply RAM commits whose in-flight window has elapsed."""
        if not self._pending:
            return
        due = [p for p in self._pending if p[0] <= now]
        if due:
            self._pending = [p for p in self._pending if p[0] > now]
            for _, pid, value in due:
                self.ram[pid] = value
                if pid == F.PARAM_CAN_ID and 0 <= value <= 62:
                    self.dev = value

    # -- cadence -------------------------------------------------------------

    def period_ms(self, api: int) -> Optional[float]:
        """Resolved broadcast period, or None when the frame is silent."""
        pid = F.PERIOD_PARAM_FOR_API.get(api)
        if pid is not None and pid in self.ram:
            ms = self.ram[pid]
        elif api in self.periods_ms:
            ms = self.periods_ms[api]
        elif F.ENABLED_BY_DEFAULT.get(api):
            ms = F.DEFAULT_PERIOD_MS[api]
        else:
            return None
        return float(ms) if ms else None

    def set_period_ms(self, api: int, ms) -> None:
        pid = F.PERIOD_PARAM_FOR_API.get(api)
        if pid is None:
            self.periods_ms[api] = int(ms)
        else:
            self.ram[pid] = int(ms)

    def enabled_apis(self) -> List[int]:
        # A product broadcasts on its own api class and no other. Emitting both
        # would let a test pass against the wrong decoder.
        # Pre-25 has api class 6 and no UNIQUE_ID: 0x2F0 is implemented at
        # 25.0.0. A 25+ controller broadcasts 0x2E0.. and may also emit 0x060
        # as the compatibility beacon, which `legacy_beacon` opts into.
        if self.is_pre25():
            base = {F.API_LEGACY_STATUS_0, F.API_LEGACY_STATUS_1}
            other = {F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID}
        else:
            base = set(MODELLED_APIS)
            if self.legacy_beacon:
                base |= {F.API_LEGACY_STATUS_0}
            other = ({F.API_LEGACY_STATUS_1, F.API_LEGACY_STATUS_2}
                     | (set() if self.legacy_beacon else {F.API_LEGACY_STATUS_0}))
        apis = base | set(self.periods_ms)
        apis |= {F.API_FOR_PERIOD_PARAM[p] for p in self.ram
                 if p in F.API_FOR_PERIOD_PARAM}
        # periods_ms is seeded with the 25+ apis by the fleet helpers, so the
        # generation filter runs last or a pre-25 device broadcasts on both.
        return sorted(a for a in apis - other if self.period_ms(a))

    # -- payloads ------------------------------------------------------------

    def status_0_payload(self) -> bytes:
        if self.heartbeat_starved_until > 0:
            self.applied = 0.0        # the spec: act as if the robot is disabled
        hard_fwd, hard_rev = self.hard_fwd, self.hard_rev
        if self.behaviour.unwired_limit_inputs:
            hard_fwd = hard_fwd or bool(self.value_of(F.PARAM_LIMIT_FWD_POLARITY))
            hard_rev = hard_rev or bool(self.value_of(F.PARAM_LIMIT_REV_POLARITY))
        return F.encode_status_0(
            applied=self.applied, volts=self.volts, amps=self.amps,
            temp_c=self.temp_c, model=self.model, hard_fwd=hard_fwd,
            hard_rev=hard_rev, soft_fwd=self.soft_fwd, soft_rev=self.soft_rev,
            inverted=self.inverted, heartbeat_lock=self.heartbeat_lock)

    def heartbeat_starved(self, now: float) -> bool:
        """True while the enable heartbeat has been absent past the spec window."""
        return now < self.heartbeat_starved_until

    def _settle_sensor_config(self):
        if self.invalid_sensor_config:
            self.faults |= F.FAULT["sensor"]
            self.sticky_faults |= F.FAULT["sensor"]

    def status_0_payload_sparkmax(self) -> bytes:
        self._settle_sensor_config()
        return F.encode_status_0_sparkmax(
            applied=self.applied, faults=self.faults,
            sticky_faults=self.sticky_faults, is_follower=self.follower,
            other_signals=self.legacy_other_signals)

    def status_1_payload_sparkmax(self) -> bytes:
        return F.encode_status_1_sparkmax(
            velocity_rpm=self.velocity_rpm, temp_c=self.temp_c,
            volts=self.volts, amps=self.amps)

    def status_1_payload(self) -> bytes:
        self._settle_sensor_config()
        return F.encode_status_1(faults=self.faults, warnings=self.warnings,
                                 sticky_faults=self.sticky_faults,
                                 sticky_warnings=self.sticky_warnings,
                                 follower=self.follower)

    def unique_id_payload(self) -> bytes:
        return F.encode_unique_id(self.serial)

    def is_sparkmax(self) -> bool:
        return self.controller_type == "sparkmax"

    @property
    def generation(self) -> str:
        """Frame generation, from firmware version. Not from the product."""
        return F.generation_for_firmware(self.firmware)

    def is_pre25(self) -> bool:
        return self.generation == F.GEN_PRE25

    def payload_for(self, api: int, now: float = 0.0) -> Optional[bytes]:
        self._settle(now)
        # Pre-25 broadcasts on 0x060/0x061 with a different layout, 25+ on
        # 0x2E0/0x2E1. Emitting one generation's frames on the other's api is
        # the mistake that made a 13.7 V rail read as a fault word.
        if self.is_pre25():
            if api == F.API_LEGACY_STATUS_0:
                return self.status_0_payload_sparkmax()
            if api == F.API_LEGACY_STATUS_1:
                return self.status_1_payload_sparkmax()
            if api in (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID):
                return None      # pre-25 does not answer on the 25+ apis
        elif api == F.API_LEGACY_STATUS_0:
            # Pinned by firmware 25+, so it carries no reading to encode.
            return F.LEGACY_BEACON_PAYLOAD
        if api == F.API_STATUS_0:
            return self.status_0_payload()
        if api == F.API_STATUS_1:
            return self.status_1_payload()
        if api == F.API_UNIQUE_ID:
            return self.unique_id_payload()
        if api in F.STATUS_APIS or api == F.API_LEGACY_STATUS_0:
            return bytes(8)
        return None

    # -- presence ------------------------------------------------------------

    def silence_reason(self, t: float, api: Optional[int] = None) -> Optional[str]:
        if self.bus_off_at is not None and t >= self.bus_off_at:
            return "bus_off"
        if self.offline:
            return "offline"
        if t < self.deaf_until:
            return "blackout"
        for s in self.silences:
            if s.covers(t, api):
                return "silenced"
        if self.awaiting_clear:
            return "awaiting_clear"
        return None

    def _deaf_to_requests(self, now: float) -> Optional[str]:
        """awaiting_clear still answers requests; everything else here does not."""
        r = self.silence_reason(now)
        return None if r == "awaiting_clear" else r

    # -- request handling ----------------------------------------------------

    def _on_read_pair(self, base, msg, now, latency_s):
        """READ_PARAMETER, apiClasses 15-22. Answers on the id it was asked on."""
        if not self._is_read_request(msg, "parameter read"):
            return []
        if not self.behaviour.answer_param_reads:
            self._ignored_out.append("parameter reads not answered")
            return []
        index = (base - F.READ_PARAM_BASE) >> 6
        first = index * 2
        pre = (self.value_of(first), self.value_of(first + 1))
        # Firmware that mistakes a read for a write of zero. Modelled on this
        # path too, so the sweep's canary is exercised against the api the sweep
        # actually uses. Nothing measured says 26.1.6 does this.
        b = self.behaviour
        if b.param_read_writes_zero:
            for pid in (first, first + 1):
                if pid not in F.PROTECTED_PARAMS:
                    self._log_write(now, pid, 0, 0, 0, self.ram.get(pid))
                    self.ram[pid] = 0
            if b.param_read_reports_pre_value:
                d = (pre[0].to_bytes(4, "little") + pre[1].to_bytes(4, "little"))
                return [(now + latency_s, base | self.dev, d)]
        d = (self.value_of(first).to_bytes(4, "little")
             + self.value_of(first + 1).to_bytes(4, "little"))
        return [(now + latency_s, base | self.dev, d)]

    def _is_read_request(self, msg, what):
        """Hardware answers a remote frame with dlc 8 and nothing else.

        Measured on rig-flex: a zero-length data frame and a remote
        frame with dlc 0 both draw silence on firmware 26.1.6.
        """
        if msg.is_remote_frame and msg.dlc == F.READ_FRAME_DLC:
            return True
        self._ignored_out.append(
            f"{what} was not a remote frame with dlc {F.READ_FRAME_DLC}")
        return False

    def _on_param_types(self, base, msg, now, latency_s):
        """GET_PARAMETER_n_TO_n+15_TYPES, apiClass 13: sixteen 4-bit codes."""
        if not self._is_read_request(msg, "parameter types request"):
            return []
        if not self.behaviour.answer_param_reads:
            self._ignored_out.append("parameter reads not answered")
            return []
        start = ((base - F.GET_PARAM_TYPES) >> 6) * 16
        d = bytearray(8)
        for i in range(16):
            code = self.type_of(start + i)
            d[i // 2] |= (code & 0x0F) << (4 * (i % 2))
        return [(now + latency_s, base | self.dev, bytes(d))]

    def value_of(self, param_id) -> int:
        """Raw uint32 a read returns: RAM first, then flash, then zero."""
        if param_id in self.ram:
            return int(self.ram[param_id]) & 0xFFFFFFFF
        if param_id in self.flash:
            return int(self.flash[param_id]) & 0xFFFFFFFF
        return 0

    def type_of(self, param_id) -> int:
        """Parameter type code. 0 is Unused, which is how a device says an id
        does not exist on it.

        A real controller reports a type per id and callers branch on it: a
        provision run skips a row whose declared type the firmware disagrees
        with, and the baseline writer decodes a float before commenting it.
        Reporting Uint for everything made both branches unreachable in this
        suite, which is how a baseline row rendered `202: 0 = 0` and reached
        hardware. Declared ids report their declared type; `param_types`
        overrides anything else.
        """
        if param_id in self.param_types:
            return self.param_types[param_id]
        if param_id in _DECLARED_TYPE_CODES:
            return _DECLARED_TYPE_CODES[param_id]
        if param_id in self.ram or param_id in self.flash:
            return 2          # Uint, for an id nothing declares a type for
        return 0

    def on_request(self, msg, now: float, latency_s: float) -> List[Reply]:
        """Replies as (absolute delivery time, arb, data). The bus owns the timeline."""
        self._ignored_out = []
        self._settle(now)
        arb = msg.arbitration_id
        base = F.base_of(arb)
        data = bytes(msg.data or b"")

        if arb == F.DISABLE_BROADCAST:
            # The spec's word is "immediately", and it is unconditional: a
            # controller that is deaf to requests, gated awaiting a clear, or
            # mid-blackout still has to stop driving the motor.
            self.disable_broadcasts += 1
            self.disabled_at = now
            self.applied = 0.0
            return []

        if base == F.IDENTIFY_UNIQUE:
            return self._on_identify(arb, data)

        deaf = self._deaf_to_requests(now)
        if deaf:
            self._ignored_out.append(deaf)
            return []

        if base == F.GET_FIRMWARE:
            if not (msg.is_remote_frame or len(data) == 0):
                self._ignored_out.append("firmware request carried data")
                return []
            if not self.behaviour.answer_firmware:
                self._ignored_out.append("firmware not answered")
                return []
            return [(now + latency_s, arb,
                     F.encode_firmware(self.firmware, self.debug, self.hw_rev))]

        # Everything from here to the generation split is firmware 25+.
        # PARAMETER_WRITE, PERSIST_PARAMETERS, READ_PARAMETER_n and
        # GET_PARAMETER_TYPES are all versionImplemented 25.0.0 in REV-Specs
        # spark-frames-2.1.0, so a 24.0.1 device does not carry one of them and
        # drops the unmatched extended id in silence. Answering them here made
        # every pre-25 write path in this suite pass against behaviour the
        # hardware does not have.
        if self.is_pre25():
            return self._on_legacy_request(arb, data, now, latency_s)

        if base == F.PARAM_WRITE:
            return self._on_param_write(data, now, latency_s)

        if F.READ_PARAM_BASE <= base <= F.READ_PARAM_LAST:
            return self._on_read_pair(base, msg, now, latency_s)

        if F.GET_PARAM_TYPES <= base <= F.GET_PARAM_TYPES_LAST:
            return self._on_param_types(base, msg, now, latency_s)

        if base == F.PERSIST:
            return self._on_persist(data, now, latency_s)

        if base == F.SET_CAN_ID:
            return self._on_set_can_id(arb, data, now)

        if base == F.CLEAR_FAULTS:
            return self._on_clear_faults(now, latency_s)

        self._ignored_out.append("unhandled request")
        return []

    # -- the pre-25 dialect --------------------------------------------------

    def _on_legacy_request(self, arb, data, now, latency_s) -> List[Reply]:
        """Everything a 24.0.1 controller answers, and nothing else.

        SET_CAN_ID (apiClass 9 idx 5) and CLEAR_FAULTS (apiClass 6 idx 14) are
        versionImplemented 1.5.0 and 1.0.0, so they are here. GET_FIRMWARE and
        IDENTIFY_UNIQUE are handled before the split, being older still.
        """
        base = F.base_of(arb)

        if base == F.SET_CAN_ID:
            return self._on_set_can_id(arb, data, now)

        if base == F.CLEAR_FAULTS:
            return self._on_clear_faults(now, latency_s)

        # The status-period write shares its arbitration id with the
        # LEGACY_STATUS_N broadcast and is told apart by DLC alone: 2 bytes sets
        # the period, 8 bytes is a device broadcasting status.
        frame = (base - F.LEGACY_SET_PERIOD) >> 6
        if (F.LEGACY_SET_PERIOD <= base
                and F.LEGACY_FRAME_MIN <= frame <= F.LEGACY_FRAME_MAX
                and len(data) == 2):
            return self._on_legacy_period_write(frame, data, now)

        # The fingerprint, api 0x094: addressed, zero-length, four bytes back.
        # Modelled from the serial so a fleet built with distinct serials gets
        # distinct fingerprints for free, and two controllers put on ONE id
        # answer the same request differently -- which is the whole tell.
        if base == (0x02050000 | (F.LEGACY_FINGERPRINT_API << 6)) and not data:
            return [(now + latency_s, arb, bytes.fromhex(self.serial)[:4])]

        pid = F.legacy_param_id_of(arb)
        if pid is not None:
            return self._on_legacy_param(pid, arb, data, now, latency_s)

        if base == F.LEGACY_BURN_FLASH:
            return self._on_legacy_burn_flash(data, now, latency_s)

        self._ignored_out.append(
            "unhandled request (pre-25 carries no frame at this api)")
        return []

    def _on_legacy_period_write(self, frame: int, data: bytes, now: float) -> List[Reply]:
        """Set one status frame's cadence. Nothing is ever sent back."""
        if self.behaviour.ignore_legacy_period_writes:
            self._ignored_out.append("legacy period write ignored")
            return []
        ms = int.from_bytes(data, "little")
        api = 0x060 + frame
        self.periods_ms[api] = ms
        # RAM only, and unlike a parameter write there is no way to make it
        # otherwise. The period parameters are not parameters on this generation,
        # so there is no ram[] entry to make, and the burn flash does not reach
        # them: rig-max set 0x060 to a distinctive 77 ms on id 3,
        # burned it with the magic and was answered 0x00, and the frame came back
        # at REV's 10 ms after the rail cycle.
        return []

    def _on_legacy_param(self, pid: int, arb: int, data: bytes, now: float,
                         latency_s: float) -> List[Reply]:
        """Read or write one parameter. The reply returns on the request's own id."""
        b = self.behaviour
        if not b.answer_legacy_param_reads or pid in b.unanswerable_legacy_params:
            self._ignored_out.append("legacy parameter not answered")
            return []

        if len(data) == 0:                       # read
            value = self.value_of(pid)
        elif len(data) == 5:                     # write
            value = int.from_bytes(data[:4], "little")
            if b.legacy_param_refuse_status == 0 and pid not in b.ignore_writes_for:
                # RAM ONLY, on every parameter, always. Nothing here reaches
                # self.flash, so a reboot() discards it. Measured on rig-max
                # and re-confirmed -- eight controllers set
                # to BRAKE, rail cycled, all eight back at COAST. Scoped to this
                # frame: a burn flash sent afterwards DOES commit the table on
                # 24.0.1 (_on_legacy_burn_flash), so "RAM only" describes the
                # write, not the whole generation.
                self.ram[pid] = value
                if pid == F.PARAM_CAN_ID:
                    self.dev = value
            else:
                value = self.value_of(pid)       # the device echoes what it kept
                self._ignored_out.append("legacy write not applied")
        else:
            self._ignored_out.append("bad legacy parameter frame length")
            return []

        type_tag = self.legacy_type_of(pid)
        return [(now + latency_s, arb,
                 F.encode_legacy_param_resp(value, type_tag,
                                            b.legacy_param_refuse_status))]

    def legacy_type_of(self, pid: int) -> int:
        """The pre-25 type tag for one parameter.

        These are NOT the 25+ codes. Tag 2 is float32 here and Uint there, so a
        reply decoded through the wrong table turns every float into an integer
        and reports success.
        """
        return F.LEGACY_TYPE_TAG_FOR_PARAM.get(pid, 1)

    def _on_legacy_burn_flash(self, data: bytes, now: float,
                              latency_s: float) -> List[Reply]:
        """api 0x072, the pre-25 burn flash, as measured on rig-max.

        Firmware 24.0.1 distinguishes THREE cases and the model has to as well,
        because a driver that reads the accept byte cannot otherwise be tested:

          zero-length            no reply at all, nothing committed
          first two bytes != magic   0xFF on the request's own arb id, nothing committed
          first two bytes == magic   0x00 on that id, and the PARAMETER TABLE commits

        Only the first two bytes are read, little-endian: magic plus trailing
        bytes is accepted, a big-endian magic and a single byte are refused.

        The status periods are NOT committed. They live in `periods_ms` rather
        than in `ram`, and copying `ram` into `flash` leaves them untouched, which
        is the modelled form of the 77 ms marker that read 10.0 ms after a rail
        cycle.

        `legacy_burn_flash_works` now defaults to the measured behaviour. Setting
        it False models a controller that refuses the burn, which is a defect
        rather than the norm.
        """
        self.burn_flash_attempts.append(now)
        # ZERO length only. A one-byte frame IS answered, with 0xFF -- measured
        # on rig-max. Gating on len < 2 modelled a silent refusal the
        # hardware does not have, which would hide a driver that sent a truncated
        # payload and then waited forever for a reply that was actually sent.
        if not data:
            self._ignored_out.append(
                "pre-25 burn flash: zero-length frame draws no reply on 24.0.1")
            return []
        magic = int.from_bytes(data[:2], "little")
        if magic != F.PERSIST_MAGIC or not self.behaviour.legacy_burn_flash_works:
            why = ("magic mismatch" if magic != F.PERSIST_MAGIC
                   else "controller refuses the burn")
            self._ignored_out.append(f"pre-25 burn flash refused ({why})")
            return [(now + latency_s, F.LEGACY_BURN_FLASH | self.dev, b"\xff")]
        # The parameter table only. periods_ms is deliberately not touched.
        self.flash = dict(self.ram)
        self.persist_log.append(PersistRecord(at=now, committed=dict(self.flash),
                                              stale=(), result=0))
        return [(now + latency_s, F.LEGACY_BURN_FLASH | self.dev, b"\x00")]

    def _on_identify(self, arb: int, data: bytes) -> List[Reply]:
        """Two addressing models on one api, split by firmware generation.

            firmware 25+   broadcast on device 0, 4-byte serial payload
            pre-25         addressed as IDENTIFY_UNIQUE | dev, payload EMPTY

        Measured on rig-max, by capturing REV Hardware Client's own
        LED button: three DLC-0 frames at 02051D81/82/83 for the three
        controllers blinked, and nothing else crossed the bus. Modelling only the
        25+ form meant a pre-25 identify test could pass against behaviour the
        firmware does not have -- which is exactly how the shipped command came
        to report success while never blinking anything.
        """
        addressed = arb & 0x3F
        if self.is_pre25():
            if addressed == self.dev and not data:
                self.identify_count += 1
            elif addressed == self.dev:
                self._ignored_out.append(
                    "pre-25 identify carries no payload; this one had data")
            else:
                self._ignored_out.append("addressed to another id")
            return []
        if addressed:
            self._ignored_out.append(
                "25+ identify is broadcast on device 0, not addressed")
        elif data[:4] == bytes.fromhex(self.serial):
            self.identify_count += 1
        else:
            self._ignored_out.append("serial mismatch")
        return []

    def _on_param_read(self, data: bytes, now: float, latency_s: float) -> List[Reply]:
        """One byte on the parameter api is a read: report, change nothing."""
        b = self.behaviour
        param_id = data[0]
        if not b.answer_param_reads or param_id in b.unreadable_params:
            self._ignored_out.append("param read unanswered")
            return []
        pre = self.ram.get(param_id)
        if b.param_read_writes_zero and param_id not in F.PROTECTED_PARAMS:
            self.ram[param_id] = 0
            self._log_write(now, param_id, 0, 0, 0, pre)
            if b.param_read_reports_pre_value:
                return [(now + latency_s, F.PARAM_WRITE_RESP | self.dev,
                         F.encode_param_resp(param_id, int(pre or 0), b.read_result))]
        value = int(self.ram.get(param_id, 0) or 0)
        return [(now + latency_s, F.PARAM_WRITE_RESP | self.dev,
                 F.encode_param_resp(param_id, value, b.read_result))]

    def _on_param_write(self, data: bytes, now: float, latency_s: float) -> List[Reply]:
        if len(data) == 1:
            return self._on_param_read(data, now, latency_s)
        if len(data) != 5:
            self._ignored_out.append("bad param write length")
            return []
        b = self.behaviour
        param_id = data[0]
        requested = int.from_bytes(data[1:5], "little")
        if param_id in F.PROTECTED_PARAMS:
            self._ignored_out.append("protected")

        if b.drop_write_responses > 0:
            b.drop_write_responses -= 1
            self._log_write(now, param_id, requested, None, None, None)
            return []
        if param_id in b.drop_write_responses_for:
            b.drop_write_responses_for = b.drop_write_responses_for - {param_id}
            self._log_write(now, param_id, requested, None, None, None)
            return []

        ignored = param_id in b.ignore_writes_for
        result = 0 if ignored else b.write_result
        if b.echo_value is not None:
            echoed = b.echo_value
        elif b.echo_transform is not None:
            echoed = b.echo_transform(param_id, requested)
        else:
            echoed = requested

        pre = self.ram.get(param_id)
        response_at = now + latency_s
        commit_at = None
        if result == 0 and not ignored:
            commit_at = response_at + b.apply_delay_s
            self._pending.append((commit_at, param_id, requested))
        else:
            self._ignored_out.append("write not applied")
        self._log_write(now, param_id, requested, echoed, result, pre,
                        response_at, commit_at)
        return [(response_at, F.PARAM_WRITE_RESP | self.dev,
                 F.encode_param_resp(param_id, echoed, result))]

    def _log_write(self, at, param_id, requested, echoed, result, pre,
                   response_at=None, commit_at=None):
        self.write_log.append(WriteRecord(
            at=at, param_id=param_id, requested=requested,
            echoed=requested if echoed is None else echoed,
            result=0 if result is None else result,
            response_at=response_at, commit_at=commit_at, pre_value=pre))

    def _on_persist(self, data: bytes, now: float, latency_s: float) -> List[Reply]:
        if len(data) < 2 or struct.unpack("<H", data[:2])[0] != F.PERSIST_MAGIC:
            self._ignored_out.append("bad magic")
            return []
        b = self.behaviour
        if b.drop_persist_response:
            self.persist_log.append(PersistRecord(at=now, committed=dict(self.flash),
                                                  stale=(), result=b.persist_result))
            return []
        committed, stale = {}, []
        for pid in self.ram:
            w = self._last_answered_write(pid)
            if w is not None and (now - w.response_at) < b.persist_settle_s:
                committed[pid] = w.pre_value if w.pre_value is not None else self.ram[pid]
                stale.append(pid)
            else:
                committed[pid] = self.ram[pid]
        self.flash = dict(committed)
        self.deaf_until = now + b.persist_blackout_s
        self.persist_log.append(PersistRecord(at=now, committed=dict(committed),
                                              stale=tuple(stale), result=b.persist_result))
        return [(now + latency_s, F.PERSIST_RESP | self.dev,
                 F.encode_persist_resp(b.persist_result))]

    def _last_answered_write(self, param_id) -> Optional[WriteRecord]:
        for w in reversed(self.write_log):
            if w.param_id == param_id and w.response_at is not None:
                return w
        return None

    def _on_set_can_id(self, arb: int, data: bytes, now: float) -> List[Reply]:
        want = F.SET_CAN_ID | self.dev
        reason = None
        if arb != want:
            reason = "broadcast id" if F.dev_of(arb) == 0 else "wrong current id"
        elif len(data) != 5:
            reason = "bad length"
        elif data[:4] != bytes.fromhex(self.serial):
            reason = "serial mismatch"
        elif not 1 <= data[4] <= 62:
            reason = "id out of range"
        elif not self.behaviour.accept_set_can_id:
            reason = "refused"
        if reason:
            self.can_id_log.append(SetCanIdRecord(
                at=now, from_id=self.dev, to_id=data[4] if len(data) == 5 else -1,
                serial=self.serial, accepted=False, reason=reason))
            self._ignored_out.append(reason)
            return []
        new_id = data[4]
        self.can_id_log.append(SetCanIdRecord(at=now, from_id=self.dev, to_id=new_id,
                                              serial=self.serial, accepted=True,
                                              reason="accepted"))
        self.dev = new_id
        self.ram[F.PARAM_CAN_ID] = new_id
        if not self.behaviour.set_can_id_ram_only:
            self.flash[F.PARAM_CAN_ID] = new_id
        return []

    def _on_clear_faults(self, now: float, latency_s: float) -> List[Reply]:
        b = self.behaviour
        before = (self.faults, self.warnings, self.sticky_faults, self.sticky_warnings)
        if b.ignore_clear_faults:
            self.clear_log.append(ClearRecord(at=now, before=before, after=before))
            self._ignored_out.append("clear ignored")
            return []
        self.faults &= b.latched_faults
        self.warnings &= b.latched_warnings
        self.sticky_faults &= b.latched_sticky_faults
        self.sticky_warnings &= b.latched_sticky_warnings
        self.awaiting_clear = False
        after = (self.faults, self.warnings, self.sticky_faults, self.sticky_warnings)
        self.clear_log.append(ClearRecord(at=now, before=before, after=after))
        if b.clear_faults_response:
            return [(now + latency_s, F.ACK | self.dev, b"")]
        return []

    # -- power ---------------------------------------------------------------

    def reboot(self, now: float, has_reset: bool = True) -> None:
        """Volatile config is lost; flash is what comes back.

        The two generations record the reset in different places, and getting it
        wrong is what made `spark faults` report a rail-cycled rig-max as clean.
        On 25+ hasReset is a sticky WARNING. Pre-25 has no warning field at all:
        it is bit 9 of the sixteen-bit sticky FAULT half of the 0x060 word,
        confirmed on rig-max by two rail cycles that came back with
        sticky 0x0200 and nothing else set.

        The status periods go with it on pre-25, back to REV's cold defaults,
        because they are not parameters on that generation and so were never in
        flash to survive. Nothing puts them back until apply_boot_config runs,
        which is the next driver start rather than the rail cycle itself.
        """
        self._pending = []
        self.ram = dict(self.flash)
        self.dev = self.flash.get(F.PARAM_CAN_ID, self.dev)
        self.faults = 0
        self.warnings = 0
        if self.is_pre25():
            self.periods_ms = {0x060 + f: ms
                               for f, ms in F.LEGACY_REV_DEFAULT_PERIOD_MS.items()}
            if has_reset:
                self.sticky_faults |= F.LEGACY_FAULT["hasReset"]
        elif has_reset:
            self.sticky_warnings |= F.WARN["hasReset"]
        self.applied = 0.0

    def __repr__(self) -> str:
        return (f"SimSpark(dev={self.dev}, serial={self.serial}, "
                f"s1={self.period_ms(F.API_STATUS_1)}ms, "
                f"faults={F.fault_names(self.faults)})")
