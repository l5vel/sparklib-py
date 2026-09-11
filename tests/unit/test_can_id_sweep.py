"""What a passive CAN sweep is allowed to call a device.

These exist because the sweep decoded `arb & 0x3F` on every frame it received,
and three classes of frame that carry no device id became controllers that were
never on the bus. One rig produced a phantom at id 1 (a socketcan
CAN_ERR_TX_TIMEOUT), a phantom at id 0 in `spark status` (the SPARK heartbeat,
delivered by socketcan local loopback), and a block of four that moved between
runs (Phoenix diagnostic-server ISO-TP sessions). All three were chased as a
duplicate CAN id that never existed. docs/TROUBLESHOOTING.md carries the entry,
"A CAN ID sweep shows devices that are not there".

The behavioural half checks the guard. The AST half checks the call site,
because a guard the loop reaches past is worth nothing and a behavioural test
cannot see that.
"""

import ast
import importlib.util
import pathlib

import pytest
from can import Message

REPO = pathlib.Path(__file__).resolve().parents[2]
SWEEP_SRC = REPO / "tools" / "can_id_sweep.py"
CENSUS_SRC = REPO / "tools" / "can_frame_census.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _load("can_id_sweep", SWEEP_SRC)
Reassembler = _load("can_frame_census", CENSUS_SRC).Reassembler

# Real device-addressed status frames, built from the fields the decoder reads:
# arb = devType<<24 | mfr<<16 | api<<6 | id. Observed on a live two-adapter rig.
CANCODER_21 = (5 << 24) | (4 << 16) | (0x0AF << 6) | 21
SPARK_9 = (2 << 24) | (5 << 16) | (0x2E0 << 6) | 9
# SparkBus._heartbeat_runnable sends this every 20 ms; it decodes to device 0.
SPARK_HEARTBEAT = 0x02052C80


def frame(**kw):
    kw.setdefault("is_extended_id", True)
    kw.setdefault("is_rx", True)
    return Message(**kw)


# -- the guard ---------------------------------------------------------------

def test_real_cancoder_status_frame_is_a_device():
    assert sweep._not_a_device(frame(arbitration_id=CANCODER_21)) is None


def test_real_spark_status_frame_is_a_device():
    assert sweep._not_a_device(frame(arbitration_id=SPARK_9)) is None


def test_error_frame_is_rejected_and_names_its_class():
    """CAN_ERR_TX_TIMEOUT is 0x001, so the old decoder called it device 1."""
    m = frame(arbitration_id=0x001, is_error_frame=True, is_extended_id=False)
    assert sweep._not_a_device(m) == "error frame (TX_TIMEOUT)"


def test_standard_id_frame_is_rejected():
    """FRC device addressing exists only in 29-bit frames."""
    assert sweep._not_a_device(frame(arbitration_id=0x123,
                                     is_extended_id=False)) == "standard 11-bit id"


def test_host_transmitted_frame_is_rejected():
    """Local loopback delivers another process's TX; is_rx is False for it."""
    m = frame(arbitration_id=SPARK_HEARTBEAT, is_rx=False)
    assert sweep._not_a_device(m) == "sent by this host"


def test_the_spark_heartbeat_would_otherwise_report_as_device_zero():
    """Guards the premise of the test above: 0 is also REV's factory-default id,
    so this frame reads as a controller that lost its flashed identity."""
    assert SPARK_HEARTBEAT & 0x3F == 0
    assert (SPARK_HEARTBEAT >> 24) & 0x1F == 2       # motor controller
    assert (SPARK_HEARTBEAT >> 16) & 0xFF == 5       # REV


@pytest.mark.parametrize("bits,name,device_id", [
    (0x001, "TX_TIMEOUT", 1),
    (0x002, "LOSTARB", 2),
    (0x004, "CRTL", 4),
    (0x008, "PROT", 8),
    (0x010, "TRX", 16),
    (0x020, "ACK", 32),
])
def test_error_classes_collide_with_the_device_id_field(bits, name, device_id):
    """From linux/can/error.h. Each of these was a reachable phantom id."""
    assert sweep._err_classes(bits) == name
    assert bits & 0x3F == device_id


def test_combined_error_classes_are_all_named():
    assert sweep._err_classes(0x0A0) == "ACK+BUSERROR"


# -- the call site -----------------------------------------------------------

def loop_body_of(func_name):
    """The while-loop body inside `func_name` in can_id_sweep.py."""
    tree = ast.parse(SWEEP_SRC.read_text(), filename=str(SWEEP_SRC))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func_name)
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.While))
    return loop


def test_sweep_loop_calls_the_guard_before_it_decodes_an_id():
    """A guard the loop reaches past is worth nothing.

    Fails if anyone reorders the body so `arb & 0x3F` runs before the reject.
    """
    loop = loop_body_of("_sweep_channel")
    guard_line = min(n.lineno for n in ast.walk(loop)
                     if isinstance(n, ast.Call) and getattr(n.func, "id", None)
                     == "_not_a_device")
    decode_lines = [n.lineno for n in ast.walk(loop)
                    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitAnd)
                    and isinstance(n.right, ast.Constant) and n.right.value == 0x3F]
    assert decode_lines, "no `& 0x3F` device decode found in the sweep loop"
    assert guard_line < min(decode_lines), (
        "_not_a_device must be called before the device id is decoded")


def test_the_guard_result_short_circuits_the_loop():
    """The reject branch must `continue`, not fall through into the tally."""
    loop = loop_body_of("_sweep_channel")
    reject = [n for n in ast.walk(loop)
              if isinstance(n, ast.If)
              and isinstance(n.test, ast.Name) and n.test.id == "reason"]
    assert reject, "expected `if reason:` guarding the decode"
    assert any(isinstance(s, ast.Continue) for s in ast.walk(reject[0]))


def test_every_caller_unpacks_the_three_tuple(monkeypatch, capsys):
    """_sweep_channel returns (devices, rejected, finished). Prove main reads all three.

    Breaking the call site rather than the function: if a caller goes back to
    `per_channel[ch] = result`, the rejected tally and the interrupted flag both
    disappear, and a run cut short reports as a complete one.
    """
    calls = []

    def fake_sweep(channel, duration):
        calls.append(channel)
        return ({21: {"count": 300, "mfrs": {4}}},
                {"error frame (TX_TIMEOUT)": 8, "sent by this host": 148},
                True)

    monkeypatch.setattr(sweep, "_sweep_channel", fake_sweep)
    monkeypatch.setattr(sweep, "_expectations",
                        lambda conf: ([(21, "cancoder/LF", "can0")], []))
    sweep.main(["--channels", "can0", "--range", "1-24"])

    out = capsys.readouterr().out
    assert calls == ["can0"]
    assert "cancoder/LF" in out
    assert "8 error frame (TX_TIMEOUT)" in out
    assert "148 sent by this host" in out


def test_an_interrupted_sweep_is_not_reported_as_a_complete_one(monkeypatch, capsys):
    """The third element exists so a half-finished listen cannot read as clean."""
    monkeypatch.setattr(sweep, "_sweep_channel",
                        lambda ch, d: ({}, {}, False))
    monkeypatch.setattr(sweep, "_expectations", lambda conf: ([], []))
    code = sweep.main(["--channels", "can0", "--range", "1-4"])
    assert code != 0
    assert "interrupt" in capsys.readouterr().out.lower()


def test_a_channel_that_could_not_be_opened_is_skipped(monkeypatch, capsys):
    """The None return still has to survive the tuple unpack."""
    monkeypatch.setattr(sweep, "_sweep_channel", lambda ch, d: None)
    monkeypatch.setattr(sweep, "_expectations", lambda conf: ([], []))
    code = sweep.main(["--channels", "nosuchdev0", "--range", "1-4"])
    out = capsys.readouterr().out
    assert code == 2
    assert "none of those channels could be listened on" in out.lower()


# -- ISO-TP reassembly -------------------------------------------------------

def test_single_frame_is_the_ghost_reply():
    """The reply that made a diagnostic channel look like a device announcing
    its own id: `05 04` is device type 5 / manufacturer CTRE, then the channel."""
    r = Reassembler()
    r.feed((0x3E5, 7), bytes.fromhex("067e0504fc5607aa"))
    assert r.done[(0x3E5, 7)] == [bytes.fromhex("7e0504fc5607")]


def test_multi_frame_is_reassembled_and_trimmed_to_its_declared_length():
    r = Reassembler()
    r.feed((0x3E5, 8), bytes([0x10, 0x14]) + bytes(range(6)))
    r.feed((0x3E5, 8), bytes([0x21]) + bytes(range(6, 13)))
    r.feed((0x3E5, 8), bytes([0x22]) + bytes(range(13, 20)))
    assert r.done[(0x3E5, 8)] == [bytes(range(20))]
    assert (0x3E5, 8) not in r.partial


def test_a_consecutive_frame_without_a_first_frame_is_dropped():
    """Otherwise a mid-capture start corrupts the next device's message."""
    r = Reassembler()
    r.feed((0x3E5, 9), bytes([0x22, 0xde, 0xad]))
    assert r.done == {} and r.partial == {}


def test_interleaved_devices_stay_separate():
    """Four channels reply concurrently, so their frames interleave."""
    r = Reassembler()
    r.feed((0x3E5, 7), bytes([0x10, 0x08]) + b"AAAAAA")
    r.feed((0x3E5, 8), bytes([0x10, 0x08]) + b"BBBBBB")
    r.feed((0x3E5, 7), bytes([0x21]) + b"AA" + bytes(5))
    r.feed((0x3E5, 8), bytes([0x21]) + b"BB" + bytes(5))
    assert r.done[(0x3E5, 7)] == [b"AAAAAAAA"]
    assert r.done[(0x3E5, 8)] == [b"BBBBBBBB"]
