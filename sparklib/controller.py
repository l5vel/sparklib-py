from can import Message
from . import statuses
from struct import pack
import time

"""
Description: Objects for storing data from motor controllers and sending data to motor controllers
Author: Jacob Peskuski, Gabriel Carlson

Supports both REV SparkMax and REV SparkFlex via the controller_type parameter.
The two share the same command IDs but use different status frame APIs and
formats.
"""

# Controller type constants
SPARK_MAX  = "sparkmax"
SPARK_FLEX = "sparkflex"

# Per-type CAN configuration
_CONFIGS = {
    SPARK_MAX: {
        "s0_api":    0x60,
        "s1_api":    0x61,
        "s2_api":    0x62,
        # Full arbitration ID bases for status frames we decode.
        # Formula: 0x02050000 + (api << 6).
        "s1_id_base": 0x02051840,   # velocity
        "s2_id_base": 0x02051880,   # position
        # Bit-field formats for statuses.Status
        "s1_fmt": ((32, 8, 12, 12), ('float', 'uint', 'uint', 'uint')),
        "s2_fmt": ((32,),           ('float',)),
        # (api_key, data_index) for velocity and position properties
        "velocity_from": (0x61, 0),
        "position_from": (0x62, 0),
    },
    SPARK_FLEX: {
        "s0_api":    0x2E0,
        "s1_api":    0x2E1,
        "s2_api":    0x2E2,
        "s1_id_base": 0x0205B840,
        "s2_id_base": 0x0205B880,
        "s1_fmt": None,
        "s2_fmt": ((32, 32), ('float', 'float')),
        "velocity_from": (0x2E2, 0),
        "position_from": (0x2E2, 1),
    },
}


def packer_float(value):
    hexStr = pack('fi', value, 0)
    return hexStr

def packer_float_four(value):
    hexStr =  pack('f', value)
    val = hexStr + b'\x02\x00\x00\x00'
    return val


class Controller:
    def __init__(self, bus, id, controller_type=SPARK_MAX):
        self.bus = bus
        self.id = id
        self.controller_type = controller_type

        # Generation observed on the wire, set by the router. None until seen.
        self._observed_generation = None
        self._apply_config(_CONFIGS[controller_type])

        # Raw bytes for STATUS_0 (applied output) and STATUS_1 (faults).
        # Set by SparkBus bus_monitor.
        self._status0_raw = None
        self._status1_raw = None

        # monotonic timestamp of the last frame received from this controller.
        # The raw STATUS buffers above are never cleared -- once a SPARK has been
        # seen they keep their last value forever, so "has _status0_raw" answers
        # "was it EVER alive", not "is it alive NOW". A base power cut needs the
        # second question, hence the timestamp. None = never seen.
        self._last_seen = None

        # Control properties
        self.percentProps  = {"dir": 1, "scale": 1}
        self.velocityProps = {"dir": 1, "countConversion": 1}
        self.positionProps = {"dir": 1, "countConversion": 1}

    def _apply_config(self, cfg):
        """Point the api keys, decoders and setpoint frames at one layout.

        Split out of __init__ so the layout can be re-pointed when the wire
        contradicts the configured product. The two blocks in _CONFIGS are
        GENERATIONS wearing product names: api class 6 before firmware 25, and
        0x2E0.. from 25 on.
        """
        self._active_config = cfg
        # API keys for raw capture in bus_monitor
        self._s0_api = cfg["s0_api"]
        self._s1_api = cfg["s1_api"]
        self._s2_api = cfg["s2_api"]

        # Where to read velocity and position from
        self._velocity_api, self._velocity_idx = cfg["velocity_from"]
        self._position_api, self._position_idx = cfg["position_from"]

        # Only entries with a fmt are decoded. Pre-25: s1 = velocity, s2 =
        # position. Firmware 25+: s2 carries both and s1 is unused.
        s1_status = (statuses.Status(cfg["s1_id_base"] + self.id, *cfg["s1_fmt"])
                     if cfg["s1_fmt"] else None)
        s2_status = statuses.Status(cfg["s2_id_base"] + self.id, *cfg["s2_fmt"])
        self.statuses = {
            cfg["s0_api"]: None,
            cfg["s1_api"]: s1_status,
            cfg["s2_api"]: s2_status,
        }

    def _config_for_generation(self):
        """The layout block the OBSERVED generation calls for."""
        return _CONFIGS[SPARK_FLEX if self._modern() else SPARK_MAX]

    def seconds_since_seen(self):
        """Age of the last frame from this controller, or None if never seen."""
        if self._last_seen is None:
            return None
        return time.monotonic() - self._last_seen

    def is_live(self, window_s):
        """True if a frame arrived within `window_s`. A controller that has gone
        dark (motor power cut, brownout, unplugged CAN) fails this while still
        reporting its pre-cut applied_output/faults from the raw buffers."""
        age = self.seconds_since_seen()
        return age is not None and age <= window_s

    # ------------------------------------------------------------------
    # Fault / utility commands
    # ------------------------------------------------------------------

    def enable(self):
        pass

    def disable(self):
        pass

    def clear_faults(self):
        msg = Message(arbitration_id=0x02051B80 + self.id, data=[], is_extended_id=True)
        self.bus.send_msg(msg)

    def set_periodic_frame_period(self, frame_index, period_ms):
        # Volatile per-power-cycle. Arb shares STATUS_N broadcast id; SPARK
        # disambiguates by DLC (2 bytes = set period; 8 bytes = STATUS data).
        if not 0 <= frame_index <= 6:
            raise ValueError(f"frame_index must be 0..6, got {frame_index}")
        if not 0 <= period_ms <= 0xFFFF:
            raise ValueError(f"period_ms must be 0..65535, got {period_ms}")
        arb = 0x02051800 + (frame_index << 6) + self.id
        data = bytes([period_ms & 0xFF, (period_ms >> 8) & 0xFF])
        msg = Message(arbitration_id=arb, data=data, is_extended_id=True)
        self.bus.send_msg(msg)

    def decode_message(self, message):
        """One-liner string describing a received CAN frame.

        Used by SparkBus.bus_monitor's per-frame debug print to make raw
        traffic readable. Identifies which periodic status frame it is and
        pulls out the fields we know how to decode for that frame; unknown
        APIs fall back to a raw hex dump.
        """
        api = (message.arbitration_id & 0x0000FFC0) >> 6
        data = bytes(message.data)
        hex_ = data.hex()

        if api == self._s0_api:
            ao = (int.from_bytes(data[0:2], 'little', signed=True)
                  * 3.082369457075716e-05) if len(data) >= 2 else None
            return f"S0 applied={ao:+.3f} hex={hex_}" if ao is not None else f"S0 hex={hex_}"
        if api == self._s1_api:
            active = f"0x{data[0]:02X}" if len(data) >= 1 else "n/a"
            sticky = f"0x{data[3]:02X}" if len(data) >= 4 else "n/a"
            return f"S1 active={active} sticky={sticky} hex={hex_}"
        if api == self._s2_api:
            return f"S2 hex={hex_}"
        return f"api=0x{api:03X} hex={hex_}"

    def stop_follower_mode(self):
        msg = Message(arbitration_id=0x02057C80 + self.id, data=[], is_extended_id=True)
        self.bus.send_msg(msg)

    def print_diagnostics(self):
        print(f"  controller_type: {self.controller_type}")
        s0 = self._decoded(0) if self._modern() else None
        if s0 is not None:
            print(
                f"  [STATUS_0] applied_output={s0['applied_output']:.4f} "
                f"volts={s0['voltage_v']:.2f} amps={s0['current_a']:.2f} "
                f"temp={s0['motor_temp_c']}C "
                f"hard_fwd={s0['hard_forward_limit']} "
                f"hard_rev={s0['hard_reverse_limit']} "
                f"primary_hb_lock={s0['primary_heartbeat_lock']} "
                f"spark_model={s0['spark_model']}"
            )
            if s0["implausible"]:
                print(f"  [STATUS_0] NOT A READING: "
                      f"{', '.join(s0['implausible'])} pegged at full scale")
        elif self._status0_raw is not None:
            from . import admin
            legacy = admin.decode_legacy_status_0(self._status0_raw) or {}
            d = self._status0_raw
            applied_output = legacy.get("applied_output", 0.0)
            active_faults = legacy.get("active_faults", 0)
            sticky_faults = legacy.get("sticky_faults", 0)
            # Byte 6 is printed raw. The bit names this used to print are
            # refuted, so naming them again would restate a known-wrong reading
            # in a place operators trust. See docs/SPARKMAX-BRINGUP.md.
            print(
                f"  [STATUS_0] applied_output={applied_output:.4f} "
                f"active_faults=0x{active_faults:04X} "
                f"sticky_faults=0x{sticky_faults:04X} "
                f"other_signals=0x{d[6]:02X}{d[7]:02X}"
            )
            problem = admin.legacy_other_signals_problem(self._status0_raw)
            if problem:
                print(f"  [STATUS_0] {problem}")
        else:
            print("  [STATUS_0] not yet received")

        s1 = self._decoded(1) if self._modern() else None
        if s1 is not None:
            print(f"  [STATUS_1] faults={s1['faults'] or '-'} "
                  f"warnings={s1['warnings'] or '-'} "
                  f"sticky_faults={s1['sticky_faults'] or '-'} "
                  f"sticky_warnings={s1['sticky_warnings'] or '-'} "
                  f"is_follower={s1['is_follower']}")
        elif self._status1_raw is not None:
            print("  [STATUS_1] telemetry frame received")
        else:
            print("  [STATUS_1] not yet received")

    # ------------------------------------------------------------------
    # Control outputs
    # ------------------------------------------------------------------

    def percent_output(self, value):
        mod_value = value * self.percentProps["dir"] * self.percentProps["scale"]
        if not self._modern():
            # SparkMax: legacy duty-cycle frame (float + 4-byte zero pad)
            msg = Message(arbitration_id=0x02050080 + self.id, data=packer_float(mod_value), is_extended_id=True)
        else:
            # SparkFlex 26.x: unified setpoint frame, mode byte = 0x00 (duty cycle).
            # Same arbitration ID; mode byte + 0x00 0x80 0xFD trailer match the
            # format used by velocity_output / position_output below -- without
            # it the firmware silently ignores the frame and holds the last
            # valid setpoint ("stuck on last command").
            data = pack('f', mod_value) + b'\x00\x00\x80\xFD'
            msg = Message(arbitration_id=0x02050080 + self.id, data=data, is_extended_id=True)
        self.bus.send_msg(msg)

    def velocity_output(self, value):
        mod_value = value * self.velocityProps["dir"] * self.velocityProps["countConversion"]
        if not self._modern():
            # SparkMax: dedicated velocity setpoint frame
            msg = Message(arbitration_id=0x02050480 + self.id, data=packer_float(mod_value), is_extended_id=True)
        else:
            # SparkFlex 26.x: unified setpoint frame, byte 4 = 0x01 (velocity)
            data = pack('f', mod_value) + b'\x01\x00\x80\xFD'
            msg = Message(arbitration_id=0x02050080 + self.id, data=data, is_extended_id=True)
        self.bus.send_msg(msg)

    def position_output(self, value):
        mod_value = value * self.positionProps["dir"] * self.positionProps["countConversion"]
        if not self._modern():
            # SparkMax: dedicated position setpoint frame
            msg = Message(arbitration_id=0x02050C80 + self.id, data=packer_float(mod_value), is_extended_id=True)
        else:
            # SparkFlex 26.x: unified setpoint frame, byte 4 = 0x02 (position)
            data = pack('f', mod_value) + b'\x02\x00\x80\xFD'
            msg = Message(arbitration_id=0x02050080 + self.id, data=data, is_extended_id=True)
        self.bus.send_msg(msg)

    def set_encoder_position(self, value):
        mod_value = packer_float(value)
        msg = Message(arbitration_id=0x02052800 + self.id, data=mod_value, is_extended_id=True)
        self.bus.send_msg(msg)
        time.sleep(0.1)
        actual_position = self.position
        print(f"Position set to {mod_value} but read back {actual_position}")

    # ------------------------------------------------------------------
    # Sensor properties
    # ------------------------------------------------------------------

    @property
    def velocity(self):
        return self.statuses[self._velocity_api].data[self._velocity_idx]

    @property
    def position(self):
        return self.statuses[self._position_api].data[self._position_idx]

    @property
    def applied_output(self):
        if self._status0_raw is None:
            return None
        return int.from_bytes(self._status0_raw[0:2], 'little', signed=True) * 3.082369457075716e-05

    @property
    def primary_heartbeat_lock(self):
        if self._status0_raw is None:
            return None
        return bool(self._status0_raw[6] & 0x20)

    # -- telemetry ----------------------------------------------------------
    #
    # SparkFlex 26.1.6 moved every one of these. Faults left STATUS_0 entirely
    # and live in STATUS_1; STATUS_0 bytes 2-6 are now bus voltage, output
    # current and motor temperature; and STATUS_0 byte 6 bit 0 is
    # hard_forward_limit, not is_follower. Reading the 24.x offsets on this
    # firmware returns a healthy 12.4 V rail as an active fault bitfield, and
    # reports a closed limit switch as follower mode. The 26.1.6 layout is
    # decoded in one place, admin, and both readers go through it.

    # Pre-25 is api class 6; 25+ is 0x2E0 and up.
    _MODERN_APIS = (0x2E0, 0x2E1, 0x2E2, 0x2F0)
    _LEGACY_ONLY_APIS = (0x061, 0x062)

    def note_frame_api(self, api):
        """Record the generation from the api a frame actually arrived on.

        0x061/0x062 exist only pre-25. 0x2E0.. and 0x2F0 exist only on 25+.
        0x060 alone is ambiguous -- on 25+ it is a pinned compatibility beacon --
        so it is deliberately not used to decide.
        """
        before = self._observed_generation
        if api in self._MODERN_APIS:
            self._observed_generation = "fw25+"
        elif api in self._LEGACY_ONLY_APIS:
            self._observed_generation = "pre25"
        # Re-point the layout when the wire contradicts the configured product.
        # Without this the api keys, the velocity and position sources and the
        # setpoint frames stayed on the declared product for the life of the
        # object, so a reflashed controller decoded and was commanded through
        # the wrong generation's frames while _modern() reported the right one.
        if (self._observed_generation != before
                and self._observed_generation is not None):
            wanted = self._config_for_generation()
            if wanted is not self._active_config:
                self._apply_config(wanted)

    def _modern(self):
        """True when this controller's frames are the 25+ layout.

        Answered by what arrived, not by the configured product. Falls back to
        the declared type only before any status frame has been seen, and at
        that point every raw buffer is None so nothing is decoded anyway.
        """
        if self._observed_generation is not None:
            return self._observed_generation == "fw25+"
        return self.controller_type == SPARK_FLEX

    def _flex(self):
        """Deprecated: kept so no caller silently changes meaning. Frame layout
        keys on firmware generation, so use _modern()."""
        return self._modern()

    def _decoded(self, which):
        from . import admin
        raw = self._status0_raw if which == 0 else self._status1_raw
        if raw is None:
            return None
        return (admin.decode_status_0(raw) if which == 0
                else admin.decode_status_1(raw))

    @staticmethod
    def _mask(names, table):
        m = 0
        for n in names or ():
            if n in table:
                m |= 1 << table.index(n)
        return m

    @property
    def active_faults(self):
        """Fault mask, or None when this reader cannot honestly produce one.

        TROUBLESHOOTING.md records that on firmware 25.x+ STATUS_0 bytes 2-6 are
        bus voltage, output current and motor temperature, and faults moved to
        STATUS_1. The old slice returned a 12.4 V rail as a fault bitfield. No
        verified SparkMax layout exists in this repo, so that path returns None
        rather than a number that means something else -- every caller already
        treats None as "no reading", which is true.
        """
        from . import admin
        if self._modern():
            s1 = self._decoded(1)
            return None if s1 is None else self._mask(
                s1["faults"], list(admin._FAULT_BITS))
        s0 = admin.decode_legacy_status_0(self._status0_raw)
        if s0 is None or s0["is_beacon"]:
            return None
        return s0["active_faults"]

    @property
    def sticky_faults(self):
        from . import admin
        if self._modern():
            s1 = self._decoded(1)
            return None if s1 is None else self._mask(
                s1["sticky_faults"], list(admin._FAULT_BITS))
        s0 = admin.decode_legacy_status_0(self._status0_raw)
        if s0 is None or s0["is_beacon"]:
            return None
        return s0["sticky_faults"]

    def _legacy_telemetry(self):
        """Pre-25 telemetry, decoded by admin rather than here.

        One decoder, one set of scales. A second copy of the volt and amp counts
        in this class would drift from the sourced one silently, since nothing
        compares them and a wrong scale still yields a plausible reading.
        """
        from . import admin
        return admin.decode_legacy_status_1(self._status1_raw)

    @property
    def bus_voltage(self):
        if self._modern():
            s0 = self._decoded(0)
            return None if s0 is None else s0["voltage_v"]
        s1 = self._legacy_telemetry()
        return None if s1 is None else s1["voltage_v"]

    @property
    def output_current(self):
        if self._modern():
            s0 = self._decoded(0)
            return None if s0 is None else s0["current_a"]
        s1 = self._legacy_telemetry()
        return None if s1 is None else s1["current_a"]

    @property
    def is_follower(self):
        if self._modern():
            s1 = self._decoded(1)
            return None if s1 is None else s1["is_follower"]
        if self._status0_raw is None:
            return None
        return bool(self._status0_raw[6] & 0x01)

    @property
    def hard_forward_limit(self):
        """STATUS_0 byte 6 bit 0 on 26.1.6. The bit the 24.x reader called
        is_follower, and the reason a closed limit switch read as follower mode.
        """
        s0 = self._decoded(0) if self._modern() else None
        return None if s0 is None else s0["hard_forward_limit"]

    @property
    def hard_reverse_limit(self):
        s0 = self._decoded(0) if self._modern() else None
        return None if s0 is None else s0["hard_reverse_limit"]


def health_report(label, motor, clear=True, out=print):
    """Everything worth knowing about one SPARK, printed at startup.

    Reports the three states that stop a controller driving while it keeps
    broadcasting happily: a heartbeat lock, follower mode, and a latched fault.
    Each one produces a controller that answers every question correctly and
    applies zero output.

    clear decides whether the two recoverable ones are acted on. True leaves
    follower mode and clears the faults, which is what a test harness wants
    before it commands anything. False reports and changes nothing.

    A heartbeat lock needs a power cycle, so it is always reported and never
    acted on.
    """
    out(f"[{label} SPARK health]")
    motor.print_diagnostics()

    if motor.primary_heartbeat_lock:
        out("  heartbeat lock is set. This one needs a power cycle to drive.")

    if motor.is_follower:
        out("  in follower mode, so it mirrors another controller"
            + (", leaving it" if clear else ""))
        if clear:
            motor.stop_follower_mode()

    faults = motor.active_faults
    if faults:
        out(f"  active faults 0x{faults:02X}"
            + (", clearing" if clear else ""))
        if clear:
            motor.clear_faults()

    for name in ("position", "velocity"):
        try:
            out(f"  encoder {name}: {getattr(motor, name):+.4f}")
        except Exception as err:          # noqa: BLE001 - frame may not have landed
            out(f"  encoder {name}: no frame decoded yet ({err})")
