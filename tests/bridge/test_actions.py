"""Step 4: ACTION serializer. TEXT before ACTION, zeroed unused bytes, deterministic nonce, round trips."""

import hashlib

import pytest

from ring3bridge import abi
from ring3bridge.actions import ActionSerializer, SeqCounter, nonce_for, text_id_for
from ring3bridge.framing import B2K_BOUNDS, Frame, FrameDecoder
from ring3bridge.strictjson import Call, Done, Refuse, validate

SEED = bytes(range(16))

REPLIES = [
    '{"action":"call","op":"cell_status","args":{}}',
    '{"action":"call","op":"fan_set","args":{"duty":60}}',
    '{"action":"call","op":"servo_set","args":{"deg":120}}',
    '{"action":"call","op":"halt","args":{}}',
    '{"action":"call","op":"lcd_show","args":{"line":1,"text":"SELF TEST"}}',
    '{"action":"call","op":"list_objects","args":{}}',
    '{"action":"call","op":"read_object","args":{"name":"maint_notes"}}',
    '{"action":"call","op":"write_object","args":{"name":"scratch_2","text":"fan ok at 60"}}',
    '{"action":"call","op":"witness","args":{}}',
    '{"action":"done","summary":"Self-test: sensor 290 mm, gauge and fan responded."}',
]


def serialize_all(seed: bytes) -> bytes:
    s = ActionSerializer(seed)
    seq = SeqCounter()
    out = b""
    for r in REPLIES:
        v = validate(r)
        assert isinstance(v, Call | Done | Refuse), r
        for t, body in s.serialize(v).frames:
            out += seq.wire(t, body)
    return out


def test_every_op_roundtrips_through_the_kernel_side_decoder():
    wire = serialize_all(SEED)
    frames = FrameDecoder(B2K_BOUNDS).feed(wire)
    assert all(isinstance(f, Frame) for f in frames), frames
    assert [f.seq for f in frames] == list(range(len(frames)))
    last_text = None
    actions = []
    for f in frames:
        if f.type == abi.LINK_T_TEXT:
            last_text = f.msg
        elif f.type == abi.LINK_T_ACTION:
            a = f.msg
            assert len(f.body) == 16 and f.body[11:] == b"\x00" * 5
            assert a.flags == 0 and a.p2 == 0
            if a.op in abi.TEXT_OPS:
                assert last_text is not None and last_text.text_id == a.text_id  # TEXT immediately before
            else:
                assert a.text_id == 0
            last_text = None
            actions.append(a)
    ops = [abi.OP_NAMES[a.op] for a in actions]
    assert ops == ["cell_status", "fan_set", "servo_set", "halt", "lcd_show", "list_objects", "read_object",
                   "write_object", "witness"]  # fmt: skip
    p1 = {abi.OP_NAMES[a.op]: a.p1 for a in actions}
    assert p1["fan_set"] == 60 and p1["servo_set"] == 120 and p1["lcd_show"] == 1
    assert p1["read_object"] == abi.LINK_OBJ_MAINT_NOTES and p1["write_object"] == abi.LINK_OBJ_SCRATCH_2
    assert all(p1[o] == 0 for o in ("cell_status", "halt", "list_objects", "witness"))
    assert [a.nonce for a in actions] == [nonce_for(SEED, i) for i in range(9)]
    assert frames[-1].type == abi.LINK_T_FINAL and frames[-1].msg == abi.Final(
        abi.LINK_FINAL_DONE, "Self-test: sensor 290 mm, gauge and fan responded."
    )


def test_text_frame_precedes_its_action():
    s = ActionSerializer(SEED)
    out = s.serialize(Call("lcd_show", {"line": 0, "text": "HI"}))
    assert [t for t, _ in out.frames] == [abi.LINK_T_TEXT, abi.LINK_T_ACTION]
    assert out.frames[0][1] == bytes([text_id_for(0)]) + b"HI"
    out = s.serialize(Call("halt", {}))
    assert [t for t, _ in out.frames] == [abi.LINK_T_ACTION]


def test_nonce_is_deterministic_and_seeded():
    assert serialize_all(SEED) == serialize_all(SEED)  # recorded replies re-serialize byte for byte
    assert serialize_all(SEED) != serialize_all(bytes(16))
    d = hashlib.sha256(b"ring3-nonce-v1" + SEED + (5).to_bytes(4, "little")).digest()
    assert nonce_for(SEED, 5) == int.from_bytes(d[:4], "little")
    assert len({nonce_for(SEED, i) for i in range(1000)}) > 990


def test_exact_bytes_for_a_known_action():
    a = ActionSerializer(SEED).serialize(Call("fan_set", {"duty": 60})).frames[0][1]
    n = nonce_for(SEED, 0)
    assert a == bytes([2, 0, 60, 0, 0, 0]) + n.to_bytes(4, "little") + bytes(6)


def test_text_ids_cycle_1_to_255():
    assert [text_id_for(i) for i in (0, 1, 253, 254, 255, 509, 510)] == [1, 2, 254, 255, 1, 255, 1]


def test_refuse_and_bad_inputs():
    out = ActionSerializer(SEED).serialize(Refuse("no"))
    assert out.frames == ((abi.LINK_T_FINAL, b"\x02no"),)
    with pytest.raises(TypeError):
        ActionSerializer(SEED).serialize("halt")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ActionSerializer(b"short")
    # an unvalidated Call that breaks the schema can't reach the wire either
    with pytest.raises(ValueError):
        ActionSerializer(SEED).serialize(Call("fan_set", {"duty": 101}))


def test_seq_wraps():
    c = SeqCounter(0xFFFF)
    w1 = c.wire(abi.LINK_T_STOP, b"\x01")
    w2 = c.wire(abi.LINK_T_STOP, b"\x01")
    f = FrameDecoder(B2K_BOUNDS).feed(w1 + w2)
    assert [x.seq for x in f] == [0xFFFF, 0]
