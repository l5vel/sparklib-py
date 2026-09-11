"""Parameter reads over the frames REV documents, instead of the write api.

There used to be a `read_param` here that sent a one-byte payload on
PARAMETER_WRITE (apiClass 14) and let payload length carry the read/write
distinction. That path is not in the spec, one appended byte turned a read into
a write, and it was deleted.

frames 2.1.0 defines dedicated frames, all implemented at 25.0.0 and all
rtr:true with lengthBytes 8:

    READ_PARAMETER_n_AND_n+1        apiClasses 15-22   128 frames, params 0-255
    WRITE_PARAMETER_n_AND_n+1       apiClasses 23-30   128 frames, params 0-255
    GET_PARAMETER_n_TO_n+15_TYPES   apiClass 13         16 frames, params 0-255

TWO things decide whether a read is answered, and this tree got both wrong until
rig-flex was measured. The request has to be a REMOTE frame, and its
dlc has to be 8. A zero-length data frame and a remote frame with dlc 0 are both
ignored by firmware 26.1.6. Separately, apiClass 19 is the fifth of eight read
classes and covers 128-159; reading it as the whole read api is what limited
this package to that band.
"""

from __future__ import annotations

from sparklib import admin as sa
from sparksim import attach, spark
from sparksim import frames as F
from sparksim.faults import SparkBehaviour


def test_a_read_goes_out_on_a_read_class_and_never_on_the_write_api(sim):
    """The hazard the whole change removes: a read that cannot be a write."""
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    adm = attach(bus)

    got = adm.read_param_pair(12, 159)
    assert got is not None, "the documented read frame went unanswered"

    sent = [m for m in bus.sent
            if F.base_of(m.arbitration_id) == F.PARAM_WRITE]
    assert not sent, (
        "a parameter read put a frame on PARAMETER_WRITE (apiClass 14); on that "
        "api one appended byte is a write to the id being read")


def test_the_read_pair_frame_returns_both_parameters(sim):
    """One frame carries two values, so reading 158 also yields 159."""
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    got = attach(bus).read_param_pair(12, 158)

    assert got == {"first_id": 158, "first": 10, "second_id": 159, "second": 20}


def test_every_parameter_0_to_255_has_a_documented_read_frame(sim):
    """The coverage that apiClass 19 alone does not give.

    Parameter 0 (the CAN id), the current limits and Status 8/9 Period (199,
    224) all sit outside 128-159 and all answered on rig-flex.
    """
    bus = sim([spark(12, ram={0: 12, 59: 80, 61: 10000, 199: 20, 224: 100})])
    adm = attach(bus)

    for pid, want in ((0, 12), (59, 80), (61, 10000), (199, 20), (224, 100)):
        assert adm.read_param_value(12, pid) == want, (
            f"parameter {pid} is reachable on apiClass {15 + pid // 32} and was "
            "not read; the arbitration id is one base plus (param_id // 2) << 6")


def test_a_parameter_id_past_the_table_sends_nothing(sim):
    """The guard that keeps a read a read.

    Index 128 past READ_PARAM_BASE is WRITE_PARAMETER_0_AND_1 at 0x02055C00,
    which writes the device's own CAN id. The bound has to be checked before the
    index is computed, not after.
    """
    bus = sim([spark(12, ram={0: 12})])
    adm = attach(bus)

    for pid in (-1, 256, 512):
        before = len(bus.sent)
        assert adm.read_param_pair(12, pid) is None
        assert not bus.sent[before:], (
            f"parameter {pid} is past the table and put "
            f"{[hex(m.arbitration_id) for m in bus.sent[before:]]} on the "
            "bus; one index past the last read frame is a write of the CAN id")


def test_a_read_goes_out_as_a_remote_frame_carrying_dlc_8(sim):
    """The half of the frame form that took eight days to find.

    Measured on rig-flex: a zero-length data frame draws silence, a
    remote frame with dlc 0 draws silence, a remote frame with dlc 8 answers.
    A regression to either silent form returns None on hardware and nothing
    else in this suite would notice.
    """
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    adm = attach(bus)
    adm.read_param_pair(12, 159)
    adm.param_types(12, start_id=0)

    sent = [m for m in bus.sent
            if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST
            or F.GET_PARAM_TYPES <= F.base_of(m.arbitration_id) <= F.GET_PARAM_TYPES_LAST]
    assert sent, "no read frame reached the bus at all"
    for m in sent:
        assert m.is_remote_frame, (
            f"0x{m.arbitration_id:08X} went out as a data frame; 26.1.6 ignores "
            "those on the read classes")
        assert m.dlc == F.READ_FRAME_DLC, (
            f"0x{m.arbitration_id:08X} went out with dlc {m.dlc}; dlc 0 is "
            "silent on hardware and only dlc 8 is answered")


def test_the_types_frame_says_which_ids_the_device_implements(sim):
    """apiClass 13 answers 'which ids exist' directly: sixteen type codes per
    frame, type 0 meaning Unused. That is what the sweep was built to find."""
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    types = attach(bus).param_types(12, start_id=148)

    assert types is not None, "GET_PARAMETER_TYPES went unanswered"
    assert set(types) == set(range(144, 160)), (
        f"one frame covers a 16-id block aligned to 16: {sorted(types)}")
    assert types[158] == "Uint" and types[159] == "Uint"
    assert types[145] == "Unused", "an id the device does not carry reads Unused"


def test_all_256_parameter_types_arrive_in_sixteen_frames(sim):
    """The claim that makes apiClass 13 worth using at all."""
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    adm = attach(bus)

    seen = {}
    for start in range(0, 256, 16):
        block = adm.param_types(12, start_id=start)
        assert block is not None, f"no answer for the block at {start}"
        seen.update(block)

    assert sorted(seen) == list(range(256)), "sixteen frames must cover 0-255"
    assert seen[158] == "Uint"
    assert seen[200] == "Unused"


# -- retrying a read, which a write may not do --------------------------------

def test_a_silent_read_is_retried(sim):
    """The post-burn window is the case: a controller stops answering for about
    two seconds after PERSIST (CD 432129), and read_param_pair sent once and
    gave up. write_param already rides that out with three attempts."""
    bus = sim([spark(12, ram={158: 10, 159: 20})])
    adm = attach(bus)
    seen = {"n": 0}
    real = adm._await

    def deaf_once(arb, wait):
        seen["n"] += 1
        return None if seen["n"] == 1 else real(arb, wait)

    adm._await = deaf_once
    assert adm.read_param_value(12, 159) == 20, (
        "a read that went unanswered once returned None instead of retrying")
    assert seen["n"] >= 2


def test_a_read_that_is_never_answered_still_returns_none(sim):
    """The retry must not turn a genuinely silent controller into a hang or a
    guess. Bounded attempts, then None."""
    bus = sim([spark(12, behaviour=SparkBehaviour(answer_param_reads=False))])
    adm = attach(bus)
    assert adm.read_param_value(12, 159, wait=0.05) is None
    sent = [m for m in bus.sent
            if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST]
    assert len(sent) == sa.READ_ATTEMPTS, (
        f"{len(sent)} attempt(s) for one read; the retry has to be bounded")


def test_a_retried_read_sends_the_same_idempotent_frame(sim):
    """A retry is only safe because the request carries no payload. If a read
    ever grew one, re-sending it would repeat whatever that payload did."""
    bus = sim([spark(12, behaviour=SparkBehaviour(answer_param_reads=False))])
    attach(bus).read_param_value(12, 159, wait=0.05)
    sent = [m for m in bus.sent
            if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST]
    assert sent and all(m.is_remote_frame and not len(m.data) for m in sent)
    assert len({m.arbitration_id for m in sent}) == 1, (
        "the retries went to different arbitration ids")
