"""SPARK CAN protocol: arbitration maths, payload codecs, constants.

Restated from REV-Specs spark-frames-2.1.0 (vendored at
reference/REV-spark-frames-2.1.0.json) and NOT imported
from the driver. A simulator that borrowed the driver's constants could not catch
the driver's constants drifting; tests/adversarial/test_simulator.py compares the
two sets in one place and nowhere else.

Pure functions and constants only: no clock, no mutable state, no I/O.
"""
from __future__ import annotations

import struct
from typing import NamedTuple

# -- arbitration id ----------------------------------------------------------

DEVICE_TYPE_MOTOR_CONTROLLER = 0x02
DEVICE_TYPE_PDH = 0x08
REV_MFR = 0x05


def arb(api: int, dev: int, device_type: int = DEVICE_TYPE_MOTOR_CONTROLLER,
        mfr: int = REV_MFR) -> int:
    """FRC extended id: device type, manufacturer, api class+index, device id."""
    return (((device_type & 0x1F) << 24) | ((mfr & 0xFF) << 16)
            | ((api & 0x3FF) << 6) | (dev & 0x3F))


class ArbFields(NamedTuple):
    device_type: int
    mfr: int
    api: int
    dev: int


def split_arb(a: int) -> ArbFields:
    return ArbFields((a >> 24) & 0x1F, (a >> 16) & 0xFF, (a >> 6) & 0x3FF, a & 0x3F)


def is_spark(a: int) -> bool:
    f = split_arb(a)
    return f.device_type == DEVICE_TYPE_MOTOR_CONTROLLER and f.mfr == REV_MFR


def api_of(a: int) -> int:
    return (a >> 6) & 0x3FF


def dev_of(a: int) -> int:
    return a & 0x3F


def base_of(a: int) -> int:
    """The frame base, i.e. the arbitration id with the device field cleared."""
    return a & ~0x3F


# -- periodic frames ---------------------------------------------------------

# Pre-25 firmware status apis, api class 6. REV's SPARK MAX control-interfaces
# page describes Periodic Status 0/1/2; frames 2.1.0 keeps only 0x060, which it
# deprecates at 25.0.0. These are a firmware generation, not a product.
API_LEGACY_STATUS_0 = 0x060
API_LEGACY_STATUS_1 = 0x061
API_LEGACY_STATUS_2 = 0x062

GEN_PRE25 = "pre25"
GEN_FW25 = "fw25+"

# frames 2.1.0 LEGACY_STATUS_0 on 25+: every signal has decodedMin == decodedMax.
# Applied output 0, all 32 fault bits set, other signals 0.
LEGACY_BEACON_PAYLOAD = b"\x00\x00\xff\xff\xff\xff\x00\x00"


def generation_for_firmware(version) -> str:
    """Which frame generation a firmware version broadcasts."""
    try:
        major = int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return GEN_FW25
    return GEN_FW25 if major >= 25 else GEN_PRE25
API_STATUS_0 = 0x2E0
API_STATUS_1 = 0x2E1
API_STATUS_2 = 0x2E2
API_STATUS_3 = 0x2E3
API_STATUS_4 = 0x2E4
API_STATUS_5 = 0x2E5
API_STATUS_6 = 0x2E6
API_STATUS_7 = 0x2E7
API_STATUS_8 = 0x2E8
API_STATUS_9 = 0x2E9
API_UNIQUE_ID = 0x2F0

DEFAULT_PERIOD_MS = {0x060: 10, 0x061: 20, 0x062: 20, 0x2E0: 10, 0x2E1: 250, 0x2E2: 20, 0x2E3: 20,
                     0x2E4: 20, 0x2E5: 20, 0x2E6: 20, 0x2E7: 20, 0x2E8: 20,
                     0x2E9: 100, 0x2F0: 2000}

ENABLED_BY_DEFAULT = {0x060: True, 0x061: True, 0x062: False,
                      0x2E0: True, 0x2E1: True, 0x2E2: False,
                      0x2E3: False, 0x2E4: False, 0x2E5: False, 0x2E6: False,
                      0x2E7: False, 0x2E8: False, 0x2E9: False, 0x2F0: True}

FRAME_LENGTH = {0x060: 8, 0x061: 8, 0x062: 8, 0x2E0: 8, 0x2E1: 8, 0x2E2: 8, 0x2E3: 8, 0x2E4: 8,
                0x2E5: 8, 0x2E6: 8, 0x2E7: 8, 0x2E8: 8, 0x2E9: 8, 0x2F0: 4}

STATUS_APIS = (0x2E0, 0x2E1, 0x2E2, 0x2E3, 0x2E4,
               0x2E5, 0x2E6, 0x2E7, 0x2E8, 0x2E9)

# -- non-periodic frames -----------------------------------------------------

SET_STATUSES_ENABLED = 0x02050400
SET_STATUSES_ENABLED_RESP = 0x02050440
PERSIST_RESP = 0x02050500
# FIRST CAN Device Specification. "Devices should disable immediately when
# receiving the Disable message (arbID 0)." It is the only broadcast the robot
# controller sends that every actuator node must act on, it carries no payload,
# and it is always a classic CAN 2.0 frame.
#   https://docs.wpilib.org/en/stable/docs/software/can-devices/can-addressing.html
DISABLE_BROADCAST = 0x00000000

# The universal heartbeat: 20 ms period, eight-byte bitfield. If 100 ms passes
# without one, a device must behave as though the robot were disabled.
UNIVERSAL_HEARTBEAT = 0x01011840
HEARTBEAT_PERIOD_S = 0.020
HEARTBEAT_TIMEOUT_S = 0.100

CLEAR_FAULTS = 0x02051B80
IDENTIFY_UNIQUE = 0x02051D80
IDENTIFY = 0x02051DC0
NACK = 0x02052000
ACK = 0x02052040
SET_CAN_ID = 0x02052540
GET_FIRMWARE = 0x02052600
PARAM_WRITE = 0x02053800
PARAM_WRITE_RESP = 0x02053840

# Documented parameter frames, frames 2.1.0. READ_PARAMETER is apiClasses 15-22,
# 128 frames in pairs over 0-255. GET_PARAMETER types (apiClass 13) covers 0-255.
# Every one is rtr:true with lengthBytes 8, and the hardware answers only a
# remote frame carrying dlc 8.
READ_PARAM_BASE = 0x02053C00
READ_PARAM_LAST = 0x02055BC0
GET_PARAM_TYPES = 0x02053400
GET_PARAM_TYPES_LAST = 0x020537C0
PARAM_ID_MAX = 255
READ_FRAME_DLC = 8


def read_pair_arb(param_id):
    """Arbitration base for the READ_PARAMETER frame carrying this parameter."""
    if not 0 <= param_id <= PARAM_ID_MAX:
        return None
    return READ_PARAM_BASE + ((param_id // 2) << 6)


def type_frame_arb(param_id):
    """Arbitration base for the GET_PARAMETER_n_TO_n+15_TYPES frame."""
    if not 0 <= param_id <= PARAM_ID_MAX:
        return None
    return GET_PARAM_TYPES + ((param_id // 16) << 6)
ENTER_SWDL_CAN_BOOTLOADER = 0x02057FC0
PERSIST = 0x0205FFC0
PERSIST_MAGIC = 15011

SETPOINT_BASES = (0x02050000, 0x02050080, 0x02050100, 0x02050140,
                  0x02050180, 0x02050200, 0x02050240)

# -- the pre-25 dialect ------------------------------------------------------
#
# Everything above this line is firmware 25+. A pre-25 controller carries none of
# PARAM_WRITE, PERSIST, READ_PARAM_PAIR or GET_PARAM_TYPES -- all four are
# versionImplemented 25.0.0 in REV-Specs spark-frames-2.1.0 -- and drops an
# unmatched extended id without a NACK. What it does carry is these.

# Parameter access. The parameter id rides in the ARBITRATION ID and the reply
# comes back on that same id; there is no separate response frame.
#
#   arb   = 0x02050000 | ((0x300 | param_id) << 6) | dev
#   read  = DLC 0                     -> [uint32 value LE][type tag][status]
#   write = [int32 value LE][type tag] -> the value the device actually took
# Pre-25 device fingerprint, api 0x094. A zero-length ADDRESSED request is
# answered with four read-only bytes unique to the controller. This is what makes
# duplicate detection possible on a generation that broadcasts no identity: two
# controllers sharing an id both answer the one request.
LEGACY_FINGERPRINT_API = 0x094

LEGACY_PARAM_ACCESS = 0x300
LEGACY_PARAM_MAX = 133
LEGACY_PARAM_OK = 0

# NOT the 25+ type codes. Tag 2 is float32 here and Uint there, so a legacy reply
# read through PARAM_TYPE turns every float parameter into an integer and reports
# it as successful.
LEGACY_PARAM_TYPE = {0: "int32", 1: "uint32", 2: "float32", 3: "bool"}

# Status-period write. Shares its arbitration id with the LEGACY_STATUS_N
# broadcast; the SPARK separates them by DLC -- 2 bytes sets the period, 8 bytes
# is status data. Nothing is sent back, ever, so the only read-back is measuring
# the cadence.
LEGACY_SET_PERIOD = 0x02051800
LEGACY_FRAME_MIN, LEGACY_FRAME_MAX = 0, 6

# Pre-25 burn flash, api 0x072. MEASURED on rig-max, firmware 24.0.1:
# this is a WORKING burn-flash command. It takes a two-byte payload carrying
# PERSIST_MAGIC little-endian -- the same 15011 the 25+ PERSIST_PARAMETERS uses,
# which REV's own spec names "Magic Number" with decodedMin == decodedMax -- and
# replies on the REQUEST'S OWN arbitration id: 0x00 accepted, 0xFF refused. What
# it commits is the PARAMETER TABLE, ids 0-133; a wrong value, a big-endian magic
# or a single byte is refused, and a zero-length frame draws no reply at all and
# commits nothing. It does NOT reach the status periods -- those move on api
# class 6, and a burned-in 77 ms came back at REV's 10 ms across a rail cycle
# (pre25.burn_flash_api, how=HARDWARE). Nothing in this package sends this frame,
# and it is denied to the wire injector.
LEGACY_BURN_FLASH = 0x02051C80

# REV's own pre-25 defaults, which is what a rail-cycled MAX comes back at.
# Measured on rig-max, both sides of two rail cycles. Frame 4 never
# broadcasts on this generation at all, so it has no entry here.
LEGACY_REV_DEFAULT_PERIOD_MS = {0: 10, 1: 20, 2: 20, 3: 50, 5: 200, 6: 200, 7: 250}

# Status 4 is written by this package's boot table and never appears on the wire.
LEGACY_ABSENT_FRAME_INDEX = 4


# Type tag per parameter, for the ones this suite exercises. The default is
# uint32, which is what most of the table is. Sourced from
# configs/baseline/rev_parameter_index.tsv, which gives names and types for the
# SPARK generally; its status-period DEFAULTS do not match pre-25 and are not
# used here.
LEGACY_TYPE_TAG_FOR_PARAM = {
    13: 2,      # P 0, FLOAT
    14: 2,      # I 0, FLOAT
    15: 2,      # D 0, FLOAT
    16: 2,      # F 0, FLOAT
    45: 3,      # Inverted, BOOL
}


def legacy_param_arb(dev: int, param_id: int) -> int:
    """Arbitration id for one parameter on one device, pre-25 dialect."""
    if not 0 <= param_id <= LEGACY_PARAM_MAX:
        raise ValueError(
            f"parameter {param_id} is outside the pre-25 table (0..{LEGACY_PARAM_MAX}). "
            "Above it the same api range carries COMMANDS: 0x300 | 255 is "
            "0x0205FFC0, which is Persist Parameters.")
    return 0x02050000 | ((LEGACY_PARAM_ACCESS | param_id) << 6) | dev


def legacy_param_id_of(a: int):
    """The parameter id an arbitration id addresses, or None if it is not one."""
    api = (a >> 6) & 0x3FF
    if api & LEGACY_PARAM_ACCESS != LEGACY_PARAM_ACCESS:
        return None
    # The id occupies the low EIGHT bits, because the table runs to 133. Masking
    # six would fold parameter 64 onto 0 and hand a caller the CAN id.
    pid = api & 0xFF
    return pid if pid <= LEGACY_PARAM_MAX else None


def encode_legacy_param_resp(value: int, type_tag: int = 1, status: int = 0) -> bytes:
    return struct.pack("<IBB", int(value) & 0xFFFFFFFF, type_tag & 0xFF,
                       status & 0xFF)

# -- parameters --------------------------------------------------------------

PARAM_CAN_ID = 0
PARAM_STATUS_0_PERIOD = 158
PARAM_STATUS_1_PERIOD = 159
PARAM_STATUS_2_PERIOD = 160
PARAM_STATUS_3_PERIOD = 161
PARAM_STATUS_4_PERIOD = 162
PARAM_STATUS_5_PERIOD = 163
PARAM_STATUS_6_PERIOD = 164
PARAM_STATUS_7_PERIOD = 165
PARAM_STATUS_8_PERIOD = 166
PARAM_STATUS_9_PERIOD = 167

# Status 0-7 are contiguous at 158-165; Status 8 and 9 are NOT 166 and 167.
# REVLib SparkParameters.h puts them at 199 and 224, and the fleet's parameter
# table stops at 198 so it could never have shown this. 166 and 167 are
# MAXMotion Max Velocity 0 and Max Accel 0, both FLOAT: the old mapping made a
# period write land on a motion limit.
# reference/revlib-2026.0.2/SparkParameters.h:149-215
PERIOD_PARAM_FOR_API = {0x2E0: 158, 0x2E1: 159, 0x2E2: 160, 0x2E3: 161, 0x2E4: 162,
                        0x2E5: 163, 0x2E6: 164, 0x2E7: 165, 0x2E8: 199, 0x2E9: 224}
API_FOR_PERIOD_PARAM = {v: k for k, v in PERIOD_PARAM_FOR_API.items()}

# Parameter 0 is the device's own CAN id. PARAM_WRITE must never carry it: the
# supported way to move an id is SET_CAN_ID, which is addressed by serial rather
# than by id, so it reaches the controller you chose even among duplicates.
PROTECTED_PARAMS = {0, 2, 50, 51, 52, 53}
PARAM_LIMIT_FWD_POLARITY = 50
PARAM_LIMIT_REV_POLARITY = 51

WRITE_RESULT = {0: "Success", 1: "InvalidID", 2: "MismatchedType",
                3: "AccessMode", 4: "Invalid", 5: "NotImplemented"}
PARAM_TYPE_UINT = 2

# -- fault and warning bits, STATUS_1, 2025+ layout --------------------------

FAULT_BITS = ("other", "motorType", "sensor", "can",
              "temperature", "gateDriver", "escEeprom", "firmware")
WARNING_BITS = ("brownout", "overcurrent", "escEeprom", "extEeprom",
                "sensor", "stall", "hasReset", "other")
FAULT = {n: 1 << i for i, n in enumerate(FAULT_BITS)}
WARN = {n: 1 << i for i, n in enumerate(WARNING_BITS)}

# -- fault bits, LEGACY_STATUS_0, pre-25 layout -------------------------------
# A different ordering, and twice as wide. Kept separate from FAULT_BITS above
# so the simulator cannot encode a legacy word with 2025+ names and agree with a
# driver making the same mistake.
LEGACY_FAULT_BITS = ("brownout", "overcurrent", "iwdtReset", "motorType",
                     "sensor", "stall", "eepromCrc", "canTx",
                     "canRx", "hasReset", "gateDriver", "other",
                     "softLimitFwd", "softLimitRev",
                     "hardLimitFwd", "hardLimitRev")
LEGACY_FAULT = {n: 1 << i for i, n in enumerate(LEGACY_FAULT_BITS)}


def legacy_fault_mask(*names: str) -> int:
    """Mask for these pre-25 fault names. An unknown name raises."""
    m = 0
    for n in names:
        m |= LEGACY_FAULT[n]
    return m


def legacy_fault_names(mask: int) -> list[str]:
    return [n for i, n in enumerate(LEGACY_FAULT_BITS) if mask & (1 << i)]


def fault_mask(*names: str) -> int:
    """Mask for these fault names. An unknown name raises, never returns 0."""
    m = 0
    for n in names:
        m |= FAULT[n]
    return m


def warn_mask(*names: str) -> int:
    m = 0
    for n in names:
        m |= WARN[n]
    return m


def fault_names(mask: int) -> list[str]:
    return [n for i, n in enumerate(FAULT_BITS) if mask & (1 << i)]


def warn_names(mask: int) -> list[str]:
    return [n for i, n in enumerate(WARNING_BITS) if mask & (1 << i)]


def as_fault_mask(spec) -> int:
    """An int mask, a fault name, or an iterable of names."""
    return _as_mask(spec, fault_mask)


def as_warn_mask(spec) -> int:
    return _as_mask(spec, warn_mask)


def _as_mask(spec, namer):
    if spec is None:
        return 0
    if isinstance(spec, int):
        return spec
    if isinstance(spec, str):
        return namer(spec)
    return namer(*spec)


# -- scale factors and codecs ------------------------------------------------

APPLIED_SCALE = 3.082369457075716e-05
VOLTS_SCALE = 0.0073260073260073
AMPS_SCALE = 0.0366300366300366


# Byte 6 of a pre-25 STATUS_0 as a real controller that drives reports it.
# Hardcoded rather than imported from spark_admin so a wrong production constant
# cannot agree with itself here. docs/SPARKMAX-BRINGUP.md has the
# measurement.
LEGACY_OTHER_SIGNALS_DRIVING = 0x10


def encode_status_0_sparkmax(applied=0.0, faults=0, sticky_faults=0,
                             is_follower=False, other_signals=None) -> bytes:
    """SPARK MAX Periodic Status 0, api 0x060. REV's own description:

      "Applied Output... stores this value as a 16-bit signed integer"
      "Faults  Each bit represents a different fault on the controller."
      "Sticky Faults  The same as the Faults field, however the bits do not
       reset until a power cycle or a 'Clear Faults' command is sent."
      "Is Follower  A single bit that is true if the controller is configured
       to follow another controller."
      https://docs.revrobotics.com/brushless/spark-max/control-interfaces

    Faults are in Status 0 on PRE-25 firmware. A controller on 25+ moves them
    to STATUS_1 and emits 0x060 as a fixed beacon instead.
    """
    d = bytearray(8)
    d[0:2] = struct.pack("<h", int(round(applied / APPLIED_SCALE)))
    d[2:4] = struct.pack("<H", faults & 0xFFFF)
    d[4:6] = struct.pack("<H", sticky_faults & 0xFFFF)
    # Bit 0 is REV's Is Follower. The rest of the byte is set wholesale: the
    # bit names this used to encode -- inverted at 0x10, heartbeat lock at 0x20
    # -- are refuted on this generation, and encoding them would let a test pass
    # against a decode that hardware contradicts.
    base = (LEGACY_OTHER_SIGNALS_DRIVING if other_signals is None
            else int(other_signals) & 0xFF)
    d[6] = base | (0x01 if is_follower else 0)
    return bytes(d)


def encode_status_1_sparkmax(velocity_rpm=0.0, temp_c=0, volts=12.6,
                             amps=0.0) -> bytes:
    """SPARK MAX Periodic Status 1, api 0x061: velocity, temperature, voltage,
    current -- telemetry, with nowhere to put a fault word.

    Field widths (32, 8, 12, 12) leave nowhere for a fault word, which is why
    pre-25 keeps faults in Status 0.
    """
    d = bytearray(8)
    d[0:4] = struct.pack("<f", float(velocity_rpm))
    d[4] = int(temp_c) & 0xFF
    v = int(round(volts / VOLTS_SCALE)) & 0x0FFF
    a = int(round(amps / AMPS_SCALE)) & 0x0FFF
    d[5] = v & 0xFF
    d[6] = ((v >> 8) & 0x0F) | ((a & 0x0F) << 4)
    d[7] = (a >> 4) & 0xFF
    return bytes(d)



def encode_status_0(applied=0.0, volts=12.6, amps=0.0, temp_c=30, model=1,
                    hard_fwd=False, hard_rev=False, soft_fwd=False, soft_rev=False,
                    inverted=False, heartbeat_lock=False) -> bytes:
    v = int(round(volts / VOLTS_SCALE)) & 0x0FFF
    c = int(round(amps / AMPS_SCALE)) & 0x0FFF
    d = bytearray(8)
    d[0:2] = struct.pack("<h", int(round(applied / APPLIED_SCALE)))
    d[2] = v & 0xFF
    d[3] = ((v >> 8) & 0x0F) | ((c & 0x0F) << 4)
    d[4] = (c >> 4) & 0xFF
    d[5] = int(temp_c) & 0xFF
    d[6] = ((0x01 if hard_fwd else 0) | (0x02 if hard_rev else 0)
            | (0x04 if soft_fwd else 0) | (0x08 if soft_rev else 0)
            | (0x10 if inverted else 0) | (0x20 if heartbeat_lock else 0)
            | ((model & 0x03) << 6))
    d[7] = (model >> 2) & 0x03
    return bytes(d)


def decode_status_0(data: bytes) -> dict:
    """Independent reference decoder. Tests assert through spark_admin's, not this."""
    d = bytes(data)
    return {
        "applied_output": int.from_bytes(d[0:2], "little", signed=True) * APPLIED_SCALE,
        "voltage_v": (d[2] | ((d[3] & 0x0F) << 8)) * VOLTS_SCALE,
        "current_a": ((d[3] >> 4) | (d[4] << 4)) * AMPS_SCALE,
        "motor_temp_c": d[5],
        "hard_forward_limit": bool(d[6] & 0x01),
        "hard_reverse_limit": bool(d[6] & 0x02),
        "soft_forward_limit": bool(d[6] & 0x04),
        "soft_reverse_limit": bool(d[6] & 0x08),
        "inverted": bool(d[6] & 0x10),
        "primary_heartbeat_lock": bool(d[6] & 0x20),
        "spark_model": ((d[6] >> 6) & 0x03) | ((d[7] & 0x03) << 2),
    }


def encode_status_1(faults=0, warnings=0, sticky_faults=0, sticky_warnings=0,
                    follower=False) -> bytes:
    d = bytearray(8)
    d[0] = faults & 0xFF
    d[2] = warnings & 0xFF
    d[3] = sticky_faults & 0xFF
    d[5] = sticky_warnings & 0xFF
    d[6] = 0x01 if follower else 0x00
    return bytes(d)


def decode_status_1(data: bytes) -> dict:
    d = bytes(data)
    return {
        "faults": fault_names(d[0]),
        "warnings": warn_names(d[2]),
        "sticky_faults": fault_names(d[3]),
        "sticky_warnings": warn_names(d[5]),
        "is_follower": bool(d[6] & 0x01),
    }


def encode_unique_id(serial_hex: str) -> bytes:
    """The 4 serial bytes in the order the device broadcasts them on api 0x2F0."""
    b = bytes.fromhex(serial_hex)
    if len(b) != 4:
        raise ValueError(f"serial must be 8 hex chars, got {serial_hex!r}")
    return b


def encode_firmware(version="26.1.6", debug=0, hw_rev=3) -> bytes:
    major, minor, build = (int(p) for p in str(version).split("."))
    d = bytearray(8)
    d[0] = major & 0xFF
    d[1] = minor & 0xFF
    d[2:4] = struct.pack(">H", build & 0xFFFF)
    d[4] = debug & 0xFF
    d[5] = hw_rev & 0xFF
    return bytes(d)


def decode_firmware(data: bytes) -> tuple[str, int]:
    d = bytes(data)
    return f"{d[0]}.{d[1]}.{int.from_bytes(d[2:4], 'big')}", d[5]


def encode_param_resp(param_id, value_u32, result=0, ptype=PARAM_TYPE_UINT) -> bytes:
    return (bytes([param_id & 0xFF, ptype & 0xFF])
            + struct.pack("<I", value_u32 & 0xFFFFFFFF) + bytes([result & 0xFF]))


def encode_param_write(param_id, value_u32) -> bytes:
    return bytes([param_id & 0xFF]) + struct.pack("<I", value_u32 & 0xFFFFFFFF)


def encode_persist_resp(result=0) -> bytes:
    return bytes([result & 0xFF])


def encode_persist_request() -> bytes:
    return struct.pack("<H", PERSIST_MAGIC)


def encode_set_can_id(serial_hex: str, new_id: int) -> bytes:
    return encode_unique_id(serial_hex) + bytes([new_id & 0xFF])


# Assembled from the rig-flex candump, not a single frame off the
# wire. Id 15 sent 0205B80F#00000C07001B4000 for 958 frames at zero current
# and 27 C; id 12 sent 0205B80C#00000C07011F4000 once at 31 C. Voltage,
# temperature and model are measured on this fleet; the byte combination is
# not. Raw voltage field 1804, i.e. 13.216 V not 13.2.
CAPTURED_BASE03_IDLE_STATUS_0 = bytes([0x00, 0x00, 0x0C, 0x07, 0x00, 0x1F, 0x40, 0x00])
CAPTURED_BASE03_IDLE_VOLTS = 1804 * VOLTS_SCALE
