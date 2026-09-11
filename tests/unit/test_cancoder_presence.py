"""A freshly opened bus answers nothing for a moment, and that is not absence.

Hardware found this. The first encoder opened returned RX_TIMEOUT while the
three opened after it answered immediately, because the bus had only just come
up. A presence check that asks once reported a healthy corner as missing, and
which corner it picked depended on the order the config listed them.
"""

import pytest

from sparklib import cancoder


class Signal:
    """A status signal that starts timing out and then starts answering."""

    def __init__(self, timeouts, ok=True):
        self.remaining = timeouts
        self.waits = 0
        self._ok = ok
        self.value = 0.0

    @property
    def status(self):
        good = self._ok and self.remaining <= 0
        return type("Code", (), {"is_ok": lambda _self: good})()

    def wait_for_update(self, timeout_s):
        self.waits += 1
        self.remaining -= 1
        return self.status


class Encoder:
    def __init__(self, signal):
        self._signal = signal

    def get_absolute_position(self):
        return self._signal


def test_an_encoder_answering_at_once_is_present():
    signal = Signal(timeouts=0)
    assert cancoder.present(Encoder(signal)) is True
    assert signal.waits == 1


def test_an_encoder_that_answers_late_is_still_present():
    """The bus-just-came-up case, which is the one hardware hit."""
    signal = Signal(timeouts=3)
    assert cancoder.present(Encoder(signal), timeout_s=2.0, poll_s=0.01) is True
    assert signal.waits == 3


def test_an_encoder_that_never_answers_is_absent():
    signal = Signal(timeouts=0, ok=False)
    assert cancoder.present(Encoder(signal), timeout_s=0.05, poll_s=0.01) is False
    assert signal.waits > 1, "a single ask cannot tell a slow bus from a dead id"


def test_the_wait_is_bounded_by_the_timeout():
    import time
    signal = Signal(timeouts=0, ok=False)
    start = time.time()
    cancoder.present(Encoder(signal), timeout_s=0.1, poll_s=0.01)
    assert time.time() - start < 1.0


def test_a_signal_with_no_status_field_is_treated_as_answering():
    """Older phoenix6 signal objects carry no status; do not call those absent."""
    class Bare:
        value = 0.0

        def wait_for_update(self, timeout_s):
            return None

    assert cancoder.present(Encoder(Bare())) is True
