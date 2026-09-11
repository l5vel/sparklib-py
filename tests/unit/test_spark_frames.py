"""SPARK CAN frame encoding and decoding, checked against REV's published spec.

Every constant here is quoted from REV-Specs spark-frames-2.1.0, mirrored in
sparklib/data/. These are the numbers that decide whether a
repair reaches the controller at all: a wrong arbitration base is not an error,
it is a silent no-op, which is exactly how set_periodic_frame_period came to do
nothing on SparkFlex for so long.
"""

import struct

import pytest

from sparklib import admin as sa


class FakeBus:
    """Records sent frames and replays queued replies.

    recv(timeout=0) returns None: that is the non-blocking drain SparkAdmin does
    before a request, and it must not consume the reply that has not been sent
    yet. Replies are only delivered to a blocking recv, as on a real bus.
    """

    def __init__(self, replies=None):
        self.sent = []
        self._replies = list(replies or [])

    def send(self, msg):
        self.sent.append(msg)

    def recv(self, timeout=0):
        if not timeout:
            return None
        return self._replies.pop(0) if self._replies else None

    def shutdown(self):
        pass


class FakeMsg:
    def __init__(self, arbitration_id, data=b"", is_remote_frame=False):
        self.arbitration_id = arbitration_id
        self.data = data
        self.is_remote_frame = is_remote_frame


def admin(replies=None):
    """A SparkAdmin wired to a FakeBus, bypassing the socketcan open."""
    a = object.__new__(sa.SparkAdmin)
    a.channel = "fake0"
    a.bus = FakeBus(replies)
    return a


# -- arbitration bases, straight from the spec --------------------------------

@pytest.mark.parametrize("name, const, expected", [
    ("CLEAR_FAULTS", sa.CLEAR_FAULTS, 0x02051B80),
    ("IDENTIFY_UNIQUE", sa.IDENTIFY_UNIQUE, 0x02051D80),
    ("SET_CAN_ID", sa.SET_CAN_ID, 0x02052540),
    ("GET_FIRMWARE", sa.GET_FIRMWARE, 0x02052600),
    ("PARAM_WRITE", sa.PARAM_WRITE, 0x02053800),
    ("PARAM_WRITE_RESP", sa.PARAM_WRITE_RESP, 0x02053840),
    ("PERSIST", sa.PERSIST, 0x0205FFC0),
    ("PERSIST_RESP", sa.PERSIST_RESP, 0x02050500),
])
def test_arbitration_bases_match_rev_spec(name, const, expected):
    assert const == expected, f"{name} base drifted from REV-Specs"


def test_persist_magic_number():
    # A wrong magic is silently ignored by the device, so nothing persists.
    assert sa.PERSIST_MAGIC == 15011


def test_status_api_ids():
    assert (sa.STATUS_0_API, sa.STATUS_1_API, sa.UNIQUE_ID_API) == (0x2E0, 0x2E1, 0x2F0)


# -- PARAMETER_WRITE ----------------------------------------------------------

def test_parameter_write_payload_layout():
    a = admin([FakeMsg(sa.PARAM_WRITE_RESP | 12,
                       bytes([159, 2]) + struct.pack("<I", 20) + bytes([0]))])
    a.write_param(12, 159, 20)
    msg = a.bus.sent[0]
    assert msg.arbitration_id == sa.PARAM_WRITE | 12
    assert msg.data[0] == 159, "parameter id is byte 0"
    assert struct.unpack("<I", msg.data[1:5])[0] == 20, "value is uint32 little-endian"
    assert len(msg.data) == 5, "PARAMETER_WRITE is 5 bytes"


def test_parameter_write_targets_the_addressed_device():
    a = admin([FakeMsg(sa.PARAM_WRITE_RESP | 17, bytes(7))])
    a.write_param(17, 159, 20)
    assert a.bus.sent[0].arbitration_id & 0x3F == 17


def test_parameter_write_reports_no_response_as_none_not_success():
    """A silent device must never look like a successful write."""
    a = admin([])  # nothing comes back
    assert a.write_param(12, 159, 20) is None


def test_parameter_write_surfaces_a_failure_result_code():
    # 3 = Access Mode. The caller must be able to see this is not success.
    a = admin([FakeMsg(sa.PARAM_WRITE_RESP | 12,
                       bytes([159, 2]) + struct.pack("<I", 0) + bytes([3]))])
    r = a.write_param(12, 159, 20)
    assert r["result"] == 3
    assert r["result_text"] == "Access Mode"
    assert r["result"] != 0


def test_parameter_write_readback_can_differ_from_requested_value():
    """The device echoes what it stored, which may be clamped. Keep both."""
    a = admin([FakeMsg(sa.PARAM_WRITE_RESP | 12,
                       bytes([159, 2]) + struct.pack("<I", 250) + bytes([0]))])
    r = a.write_param(12, 159, 20)
    assert r["result"] == 0
    assert r["value"] == 250, "readback must not be silently replaced by the request"


# -- safety interlocks --------------------------------------------------------

@pytest.mark.parametrize("param_id", sorted(sa.PROTECTED_PARAMS))
def test_write_param_refuses_safety_interlock_parameters(param_id):
    """Hard limits are e-stop equivalent; no tool may disable them."""
    a = admin([])
    with pytest.raises(sa.ProtectedParameterError):
        a.write_param(12, param_id, 0)
    assert a.bus.sent == [], "a refused write must not reach the bus"


def test_protected_params_cover_both_hard_limits_and_polarity():
    assert set(sa.PROTECTED_PARAMS) >= {50, 51, 52, 53}


# -- PERSIST_PARAMETERS -------------------------------------------------------

def test_persist_sends_the_magic_number():
    a = admin([FakeMsg(sa.PERSIST_RESP | 12, bytes([0]))])
    assert a.persist(12) == 0
    assert struct.unpack("<H", a.bus.sent[0].data)[0] == sa.PERSIST_MAGIC


def test_persist_returns_none_when_the_device_does_not_answer():
    """No response is not success -- nothing was committed to flash."""
    assert admin([]).persist(12) is None


def test_persist_surfaces_a_nonzero_result_code():
    a = admin([FakeMsg(sa.PERSIST_RESP | 12, bytes([4]))])
    assert a.persist(12) == 4


# -- SET_CAN_ID ---------------------------------------------------------------

def test_set_can_id_addresses_the_devices_current_id():
    """Verified on hardware: the arbitration id must carry the CURRENT id.
    Sent to a broadcast id or another device's id, the controller ignores it."""
    a = admin()
    a.set_can_id(17, "498B2579", 40, settle=0)
    assert a.bus.sent[0].arbitration_id == sa.SET_CAN_ID | 17


def test_set_can_id_payload_is_serial_then_new_id():
    a = admin()
    a.set_can_id(17, "498B2579", 40, settle=0)
    data = a.bus.sent[0].data
    assert len(data) == 5
    assert data[:4] == bytes.fromhex("498B2579"), "serial in broadcast byte order"
    assert data[4] == 40


def test_set_can_id_does_not_reverse_the_serial_bytes():
    """Reversed serial bytes are ignored by the device -- a silent no-op."""
    a = admin()
    a.set_can_id(17, "498B2579", 40, settle=0)
    assert a.bus.sent[0].data[:4] != bytes.fromhex("498B2579")[::-1]


@pytest.mark.parametrize("bad", [0, 63, 64, -1, 255])
def test_set_can_id_rejects_ids_outside_the_valid_range(bad):
    a = admin()
    with pytest.raises(ValueError):
        a.set_can_id(17, "498B2579", bad, settle=0)
    assert a.bus.sent == []


# -- STATUS decoding ----------------------------------------------------------

def test_decode_status_0_against_a_fleet_derived_idle_frame():
    """rig-flex idle: ~13.2 V, ~0 A, 31 C, SPARK Flex.

    Assembled from the candump rather than taken whole off the wire.
    Voltage, temperature and model are measured on this fleet; the exact byte
    combination pairs id 15 and id 12. See CAPTURED_BASE03_IDLE_STATUS_0 in
    tests/support/sparksim/frames.py.

    The legacy decoder read bytes 2:6 as fault words; on 25.x+ they are voltage,
    current and temperature, which is why it reported faults on a healthy bus.
    """
    d = bytes([0x00, 0x00, 0x0C, 0x07, 0x00, 0x1F, 0x40, 0x00])
    out = sa.decode_status_0(d)
    assert out["applied_output"] == pytest.approx(0.0)
    assert out["voltage_v"] == pytest.approx(13.2, abs=0.3)
    assert out["current_a"] == pytest.approx(0.0, abs=0.2)
    assert out["motor_temp_c"] == 31
    assert out["spark_model"] == 1, "1 = SPARK Flex"
    assert out["inverted"] is False
    assert out["primary_heartbeat_lock"] is False
    assert not any(out[k] for k in
                   ("hard_forward_limit", "hard_reverse_limit",
                    "soft_forward_limit", "soft_reverse_limit"))


def test_decode_status_0_limit_bits():
    d = bytearray(8)
    d[6] = 0x01 | 0x02          # hard forward + hard reverse
    out = sa.decode_status_0(bytes(d))
    assert out["hard_forward_limit"] and out["hard_reverse_limit"]
    assert not out["soft_forward_limit"]


def test_decode_status_0_rejects_short_frames():
    assert sa.decode_status_0(b"\x00\x00") is None
    assert sa.decode_status_0(None) is None


def test_decode_status_1_bit_positions():
    """Faults byte 0, warnings byte 2, sticky faults byte 3, sticky warnings 5."""
    d = bytearray(7)
    d[0] = 1 << 3               # can fault
    d[2] = 1 << 6               # hasReset warning
    d[3] = 1 << 5               # gateDriver sticky fault
    d[5] = 1 << 0               # brownout sticky warning
    d[6] = 0x01                 # is_follower
    out = sa.decode_status_1(bytes(d))
    assert out["faults"] == ["can"]
    assert out["warnings"] == ["hasReset"]
    assert out["sticky_faults"] == ["gateDriver"]
    assert out["sticky_warnings"] == ["brownout"]
    assert out["is_follower"] is True


def test_decode_status_1_clean_frame_reports_nothing():
    out = sa.decode_status_1(bytes(7))
    assert out["faults"] == [] and out["sticky_faults"] == []
    assert out["warnings"] == [] and out["sticky_warnings"] == []


def test_faults_are_not_read_from_status_0():
    """Regression guard: a healthy STATUS_0 with telemetry in bytes 2:6 must not
    be interpretable as faults. decode_status_0 exposes no fault keys at all."""
    out = sa.decode_status_0(bytes([0, 0, 0x0C, 0x07, 0x00, 0x1F, 0x40, 0x00]))
    assert not any("fault" in k for k in out)


# -- the pre-25 parameter ceiling ---------------------------------------------
#
# Added after mutation testing: raising LEGACY_PARAM_MAX from 133 to
# 255 produced a run identical to the baseline. Nothing in tests/unit or
# tests/adversarial referenced the constant or asserted the bound, so the guard
# the constant's own comment describes was unenforced. The one consumer in the
# tree, tests/hardware/test_legacy_burn_flash.py, does `range(0,
# LEGACY_PARAM_MAX + 1)` -- it CONSUMES the constant rather than asserting it, so
# a raised ceiling would make it obediently sweep further on real hardware.
#
# The simulator cannot cover this either: it keeps its own independent copy of
# the constant and returns None above it, so a driver swept past the table sees a
# silent non-answer rather than the hazard. Hence a plain assertion here.

def test_the_pre25_parameter_table_stops_at_133():
    """The ceiling is a safety bound, not a convenience."""
    assert sa.LEGACY_PARAM_MAX == 133, (
        "the pre-25 parameter table ends at 133. Above it the SAME api range "
        "carries commands, so a larger ceiling turns a parameter sweep into a "
        "command sweep")


def test_addressing_past_the_table_raises_rather_than_transmits():
    """A refusal, not a silent send. The driver must not build the frame at all."""
    adm = sa.SparkAdmin.__new__(sa.SparkAdmin)
    for pid in (sa.LEGACY_PARAM_MAX + 1, 134, 200, 255):
        with pytest.raises(ValueError):
            adm._legacy_param_arb(12, pid)
    for pid in (0, 1, 133):
        adm._legacy_param_arb(12, pid)          # in range, must not raise


def test_the_reason_for_the_ceiling_is_where_255_lands():
    """Pin the hazard itself, so the bound cannot be raised without confronting it.

    Parameter 255 would address api 0x3FF, which is apiClass 63 index 15 --
    byte for byte the PERSIST_PARAMETERS arbitration id. A sweep that ran to the
    end of the byte would finish by firing a flash command.

    On pre-25 that particular frame is versionImplemented 25.0.0 and is dropped,
    which is a mitigation and not a defence: ids 134 through 255 put 122 frames
    into the pre-25 command space, and what the rest of that space does on 24.0.1
    is not known. api 0x072 measured on rig-max shows commands do
    live there and do act.
    """
    api = sa.LEGACY_PARAM_ACCESS | 255
    assert api == 0x3FF, f"parameter 255 addresses api 0x{api:03X}"
    arb = 0x02050000 | (api << 6) | 0
    assert arb == sa.PERSIST, (
        f"0x{arb:08X} is no longer the PERSIST id 0x{sa.PERSIST:08X}; if the "
        "frame map moved, re-derive the ceiling rather than assuming 133")
