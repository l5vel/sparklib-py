"""Does a parameter written over CAN actually survive a power cycle?

This is the one claim code-only repair cannot make on its own. On SparkFlex
firmware 26.1.6 there is no parameter read, so after `PERSIST_PARAMETERS`
returns 0 the only evidence the value reached flash is that it is still there
after the rail has been off. `PERSIST` returning success is necessary, not
sufficient -- the 24.0.1 firmware bug was precisely a burn that reported success
without writing.

Two stages, because a power cycle cannot happen inside a test:

    # 1. write + persist, then physically cut and restore motor power
    SPARK_PERSIST_STAGE=arm uv run --with pytest pytest tests/hardware -q --hardware

    # 2. after the power cycle
    SPARK_PERSIST_STAGE=verify uv run --with pytest pytest tests/hardware -q --hardware

Stage `arm` deliberately writes the REV factory-default value (250 ms) so that a
value which merely survived in RAM is distinguishable from one that persisted --
if the write did not reach flash, the controller comes back at its provisioned
20 ms and the verify stage fails. Stage `verify` restores the provisioned value
and persists it again, so the bus is left as it was found.

Sends no setpoints and starts no heartbeat: controllers stay disabled throughout.
"""

import json
import os
import pathlib
import time

import pytest

from sparklib.config import get as _spark_config
from sparklib import admin as sa

pytestmark = pytest.mark.hardware

STAGE = os.environ.get("SPARK_PERSIST_STAGE", "")
STATE = pathlib.Path(os.environ.get(
    "SPARK_PERSIST_STATE", "/tmp/spark_persist_stage.json"))

PROVISIONED_MS = sa.APPENDIX_A_STATUS_1_PERIOD_MS      # 20
FACTORY_MS = sa.REV_DEFAULT_STATUS_1_PERIOD_MS         # 250


def _channel():
    """The same bus the session `channel` fixture picks.

    This module builds its own SparkAdmin instead of taking the fixture, so it
    resolves the name the same way or SPARK_HW_CHANNEL silently misses it and
    these tests run against a different interface than the rest of the tier.
    """
    return os.environ.get("SPARK_HW_CHANNEL", "").strip() or _spark_config().can.interface


def _first_configured_id():
    ids = []
    for group, corners in vars(_spark_config().devices).items():
        if group in ("drive", "steer"):
            ids += [int(v) for v in vars(corners).values()]
    return sorted(ids)[0]


@pytest.fixture
def adm():
    with sa.SparkAdmin(_channel()) as a:
        yield a


def test_bus_is_healthy_before_touching_anything(adm):
    inv = adm.inventory(4.0)
    assert inv, "no controllers broadcasting; run `spark clear` first"
    dups = adm.duplicates(4.0)
    assert not dups, f"duplicate CAN ids present, refusing to write: {dups}"


def test_persist_reports_success_and_the_change_is_observable(adm, gate):
    """The necessary half: the write lands and the device's behaviour changes.

    Writes a parameter and spends a flash cycle, so it needs both gates. It
    carried neither until, and a shadowed collection hook meant the
    module's `inject` marker never landed either, so this ran three times inside
    a selection advertised as read-only.
    """
    gate("inject")
    gate("flash")
    dev = _first_configured_id()
    before = adm.status_period_ms(dev, seconds=4.0)
    assert before is not None, f"id {dev} sends no STATUS_1"

    result = adm.write_param(dev, sa.PARAM_STATUS_1_PERIOD, PROVISIONED_MS)
    assert result is not None, "no PARAMETER_WRITE response: nothing was written"
    assert result["result"] == 0, f"write refused: {result['result_text']}"

    time.sleep(0.5)
    after = adm.status_period_ms(dev, seconds=4.0)
    assert after == pytest.approx(PROVISIONED_MS, abs=5), (
        "the device acknowledged the write but its broadcast period did not "
        f"change ({before} -> {after}); the parameter did not take effect")

    assert adm.persist(dev) == 0, "PERSIST_PARAMETERS did not report success"


@pytest.mark.skipif(STAGE != "arm", reason="set SPARK_PERSIST_STAGE=arm")
def test_stage_arm_write_factory_value_and_persist(adm):
    dev = _first_configured_id()
    r = adm.write_param(dev, sa.PARAM_STATUS_1_PERIOD, FACTORY_MS)
    assert r is not None and r["result"] == 0
    time.sleep(0.5)
    assert adm.status_period_ms(dev, seconds=4.0) == pytest.approx(FACTORY_MS, abs=30)
    assert adm.persist(dev) == 0
    STATE.write_text(json.dumps({"device": dev, "written_ms": FACTORY_MS,
                                 "at": time.time()}))
    print(f"\nARMED: id {dev} written to {FACTORY_MS} ms and persisted.\n"
          "Now cut and restore motor power, then run stage `verify`.")


@pytest.mark.skipif(STAGE != "verify", reason="set SPARK_PERSIST_STAGE=verify")
def test_stage_verify_value_survived_the_power_cycle(adm):
    assert STATE.exists(), "run stage `arm` first"
    state = json.loads(STATE.read_text())
    dev = state["device"]

    observed = adm.status_period_ms(dev, seconds=6.0)
    assert observed is not None, (
        f"id {dev} is silent after the power cycle; run `spark clear`")
    try:
        assert observed == pytest.approx(state["written_ms"], abs=30), (
            f"id {dev} came back at {observed} ms, not the persisted "
            f"{state['written_ms']} ms -- PERSIST_PARAMETERS reported success but "
            "the value did not reach flash. Do not trust code-only repair on "
            "this firmware; re-provision through RHC2.")
    finally:
        # Leave the bus as we found it whether or not the assertion held.
        adm.write_param(dev, sa.PARAM_STATUS_1_PERIOD, PROVISIONED_MS)
        adm.persist(dev)
        STATE.unlink(missing_ok=True)


def test_protected_parameters_are_refused_on_real_hardware(adm):
    """The interlock guard must hold against a live controller, not just a mock."""
    dev = _first_configured_id()
    for pid in sorted(sa.PROTECTED_PARAMS):
        with pytest.raises(sa.ProtectedParameterError):
            adm.write_param(dev, pid, 0)


def test_parameter_reads_are_still_unavailable_on_this_firmware(adm):
    """Guards the assumption the whole audit design rests on. If a future
    firmware starts answering reads, this fails and the audit can be widened
    from behaviour-inference to real read-back."""
    dev = _first_configured_id()
    fw, _ = adm.firmware(dev)
    assert fw, "GET_FIRMWARE_VERSION should answer on every supported firmware"

    import can
    arb = 0x02053C00 | dev              # READ_PARAMETER_0_AND_1, api 0x0F0
    adm._drain()
    adm.bus.send(can.Message(arbitration_id=arb, is_extended_id=True,
                             is_remote_frame=True, dlc=0))
    assert adm._await(arb, 0.5) is None, (
        f"firmware {fw} now answers parameter reads -- spark audit can stop "
        "inferring config from broadcast behaviour and read it directly")
