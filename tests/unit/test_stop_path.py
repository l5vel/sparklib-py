"""The software stop has to actually stop.

`SparkBus.broadcast_disable` sends arbitration id 0, the FIRST CAN disable that
every actuator must act on. Its docstring called that "the right thing to send on
a stop edge". It was not, on its own: the same SparkBus runs a heartbeat thread
asserting "enabled" every 20 ms, so the disable was overridden before the next
status frame arrived.

Measured on rig-max, id 3, a steer motor turning at 828 rpm:

    broadcast_disable() alone      applied stayed 0.151, wheel still 823 rpm
                                   after 2 s -- it did not stop
    heartbeat stopped as well      applied 0 at 31 ms, wheel stopped by ~500 ms
    heartbeat stopped alone        applied 0 at 220 ms, wheel stopped by ~1 s

Nothing in the suite called broadcast_disable, which is why a stop that did not
stop went unnoticed. These tests are cheap; the failure they cover is not.
"""
from __future__ import annotations

import threading


class _Sent:
    def __init__(self):
        self.frames = []
        self.heartbeat_at_send = []


def _bus(sent):
    """A SparkBus with only what broadcast_disable touches."""
    from sparklib.can_bus import SparkBus

    b = SparkBus.__new__(SparkBus)
    b.heartbeat_enabled = True
    b._bus_lock = threading.Lock()

    class _Chan:
        def send(self, msg):
            sent.frames.append(msg)
            # Recorded per frame: the heartbeat must already be off when the
            # FIRST disable goes out, not merely by the time the call returns.
            sent.heartbeat_at_send.append(b.heartbeat_enabled)

    b.bus = _Chan()
    return b


def test_a_disable_broadcast_stops_the_heartbeat_that_would_undo_it():
    sent = _Sent()
    b = _bus(sent)

    b.broadcast_disable()

    assert b.heartbeat_enabled is False, (
        "broadcast_disable left the heartbeat running. The controller is "
        "re-enabled 50 times a second, so the disable is overridden and a "
        "commanded motor keeps turning -- measured on rig-max at 823 rpm, two "
        "full seconds after the stop")


def test_the_heartbeat_is_already_off_when_the_first_frame_goes_out():
    """Order matters. Clearing the flag afterwards leaves a window in which the
    last heartbeat is NEWER than the disable, and the controller re-arms."""
    sent = _Sent()
    b = _bus(sent)

    b.broadcast_disable()

    assert sent.heartbeat_at_send, "no disable frame was sent at all"
    assert sent.heartbeat_at_send[0] is False, (
        "the first disable frame went out while the heartbeat was still "
        "enabled, so a heartbeat sent after it re-arms the controller")


def test_every_disable_frame_is_the_unaddressed_broadcast():
    sent = _Sent()
    b = _bus(sent)

    b.broadcast_disable(repeats=3)

    assert len(sent.frames) == 3, "a stop must not be lost to one dropped frame"
    for m in sent.frames:
        assert m.arbitration_id == 0, (
            "the disable must carry no device type, manufacturer or id, or it "
            "cannot reach a controller whose id is wrong or duplicated")
        assert not m.data, "the FIRST disable broadcast is zero length"


def test_the_frame_can_still_be_sent_without_stopping_the_heartbeat():
    """The escape hatch stays available and stays explicit."""
    sent = _Sent()
    b = _bus(sent)

    b.broadcast_disable(stop_heartbeat=False)

    assert b.heartbeat_enabled is True
    assert sent.frames, "the frame should still go out"


def test_disable_heartbeat_still_leaves_the_heartbeat_off():
    sent = _Sent()
    b = _bus(sent)

    b.disable_heartbeat()

    assert b.heartbeat_enabled is False
    assert sent.frames, "disable_heartbeat must still announce the stop"
