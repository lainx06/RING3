"""Step 2: COBS + CRC framing. Shared vectors byte for byte, round trips, every malformed class."""

import random
import re
import struct
from pathlib import Path

import pytest

from ring3bridge import abi
from ring3bridge.framing import (
    B2K_BOUNDS,
    K2B_BOUNDS,
    Frame,
    FrameDecoder,
    FrameError,
    build_decoded,
    cobs_decode,
    cobs_encode,
    crc16,
    encode_frame,
    max_cobs_len,
)

REPO = Path(__file__).resolve().parents[2]
VECTORS = REPO / "firmware" / "test" / "link_vectors.txt"
PROTOCOL = REPO / "firmware" / "ring3cell" / "protocol.h"


def _hex(s: str) -> bytes:
    return b"" if s == "-" else bytes.fromhex(s)


def load_vectors():
    crcs, cobs, frames = [], [], []
    for line in VECTORS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        if f[0] == "crc":
            crcs.append((_hex(f[1]), int(f[2], 16)))
        elif f[0] == "cobs":
            cobs.append((f[1], _hex(f[2]), _hex(f[3])))
        elif f[0] == "frame":
            frames.append((f[1], f[2], _hex(f[3]), _hex(f[4])))
        else:
            raise AssertionError(f"unknown vector line: {line[:40]}")
    return crcs, cobs, frames


CRCS, COBS, FRAMES = load_vectors()


def cell_bounds() -> tuple[dict[int, tuple[int, int]], int]:
    """The COM3 cell link's type table, read from firmware/ring3cell/protocol.h (read-only)."""
    d = {
        k: int(v, 0)
        for k, v in re.findall(r"#define (CELL_\w+) (0x[0-9A-Fa-f]+|\d+)\b", PROTOCOL.read_text())
    }
    b = {
        d["CELL_T_PING"]: (0, 0),
        d["CELL_T_FAN"]: (d["CELL_FAN_LEN"], d["CELL_FAN_LEN"]),
        d["CELL_T_SERVO"]: (d["CELL_SERVO_LEN"], d["CELL_SERVO_LEN"]),
        d["CELL_T_LCD"]: (d["CELL_LCD_MIN_LEN"], d["CELL_LCD_MAX_LEN"]),
        d["CELL_T_ALARM"]: (d["CELL_ALARM_LEN"], d["CELL_ALARM_LEN"]),
        d["CELL_T_ESTOP_RESET"]: (0, 0),
        d["CELL_T_STATE"]: (d["CELL_STATE_LEN"], d["CELL_STATE_LEN"]),
    }
    return b, abi.LINK_MIN_DECODED + d["CELL_MAX_BODY"]


def test_crc_check_value():
    assert crc16(b"123456789") == abi.LINK_CRC_CHECK == 0x29B1
    assert CRCS
    for data, want in CRCS:
        assert crc16(data) == want


@pytest.mark.parametrize("name,raw,enc", COBS, ids=[c[0] for c in COBS])
def test_cobs_vectors(name, raw, enc):
    assert cobs_encode(raw) == enc
    assert cobs_decode(enc) == raw
    assert len(enc) <= max_cobs_len(len(raw))


# Field-value checks (alarm pattern 3) belong to the receiving device, not to framing: framing accepts it.
FRAMING_EXPECT = {"ok": None, "crc": "crc", "len": "len", "type": "type", "body_len": "body_len",
                  "oversize": "oversize", "field": None}  # fmt: skip


@pytest.mark.parametrize("name,expect,decoded,wire", FRAMES, ids=[f[0] for f in FRAMES])
def test_shared_frame_vectors(name, expect, decoded, wire):
    bounds, max_dec = cell_bounds()
    assert wire[-1] == 0 and 0 not in wire[:-1]
    assert cobs_encode(decoded) + b"\x00" == wire  # byte for byte, both directions
    dec = FrameDecoder(bounds, max_dec, body_decoder=None)
    out = dec.feed(wire)
    assert len(out) == 1
    want = FRAMING_EXPECT[expect]
    if want is None:
        assert isinstance(out[0], Frame), out[0]
        f = out[0]
        assert build_decoded(f.type, f.seq, f.body) == decoded
        if expect == "ok":
            assert encode_frame(f.type, f.seq, f.body, bounds) == wire
    else:
        assert isinstance(out[0], FrameError) and out[0].kind == want, out[0]


def test_shared_vectors_as_one_stream_any_chunking():
    bounds, max_dec = cell_bounds()
    stream = b"".join(w for *_, w in FRAMES)
    want = [FRAMING_EXPECT[e] for _, e, _, _ in FRAMES]
    rng = random.Random(3)
    for chunk in (1, 2, 7, 64, len(stream)):
        dec = FrameDecoder(bounds, max_dec, body_decoder=None)
        out = []
        i = 0
        while i < len(stream):
            n = chunk if chunk != 7 else rng.randint(1, 9)
            out += dec.feed(stream[i : i + n])
            i += n
        got = [None if isinstance(o, Frame) else o.kind.value for o in out]
        assert got == want


def _body_for(t: int, rng: random.Random, n: int) -> bytes:
    """A valid body of length n for COM2 type t (rules of docs/LINK_ABI.md §3)."""
    p = lambda k: bytes(rng.randint(0x20, 0x7E) for _ in range(k))  # noqa: E731
    if t == abi.LINK_T_HELLO:
        return abi.encode_hello(abi.Hello(1, rng.randint(0, 1), rng.randbytes(12), rng.randbytes(16)))
    if t == abi.LINK_T_REQUEST:
        return p(n)
    if t == abi.LINK_T_OBSERVATION:
        o = abi.Observation(rng.getrandbits(32), rng.randint(-200, 2048), rng.randint(1, 9), bool(n & 1),
                            rng.randbytes(n - abi.LINK_OBS_HDR_LEN))  # fmt: skip
        return abi.encode_observation(o)
    if t == abi.LINK_T_AUDIT:
        return abi.encode_audit(abi.Audit(1, 2, 3, 6, 1, 0, 60, 0, 1, False))
    if t == abi.LINK_T_EVENT:
        return abi.encode_event(abi.Event(7, 0, 1, rng.getrandbits(32), 0, (250, 0, -1, 2**31 - 1)))
    if t == abi.LINK_T_HEARTBEAT:
        return abi.encode_heartbeat(abi.Heartbeat(rng.getrandbits(32), 0, 1))
    if t == abi.LINK_T_ACTION:
        return abi.encode_action(abi.Action(op=abi.LINK_OP_SERVO_SET, p1=rng.randint(0, 180), nonce=7))
    if t == abi.LINK_T_TEXT:
        return bytes([rng.randint(1, 255)]) + p(n - 1)
    if t == abi.LINK_T_FINAL:
        return bytes([rng.randint(1, 2)]) + p(n - 1)
    return bytes([rng.randint(1, 6)])


@pytest.mark.parametrize("t", sorted(abi.BODY_BOUNDS), ids=lambda t: abi.TYPE_NAMES[t])
def test_roundtrip_every_type_at_its_bounds(t):
    rng = random.Random(t)
    lo, hi = abi.BODY_BOUNDS[t]
    bounds = K2B_BOUNDS if t in abi.K2B_TYPES else B2K_BOUNDS
    for n in sorted({lo, hi, (lo + hi) // 2}):
        body = _body_for(t, rng, n)
        assert len(body) == n
        seq = rng.randint(0, 0xFFFF)
        wire = encode_frame(t, seq, body)
        assert len(wire) <= abi.LINK_MAX_WIRE
        out = FrameDecoder(bounds).feed(wire)
        assert len(out) == 1 and isinstance(out[0], Frame), out
        assert (out[0].type, out[0].seq, out[0].body) == (t, seq, body)
        assert out[0].msg == abi.decode_body(t, body)
        with pytest.raises(ValueError):
            encode_frame(t, seq, body + b"\x20" * (hi - n + 1))


def test_max_frame_is_exactly_at_the_encoded_cap():
    """The largest OBSERVATION with no zero bytes hits the worst-case COBS length exactly."""
    body = abi.encode_observation(abi.Observation(0x01010101, 0x01010101, 1, True, b"\x01" * 512))
    body = body[:10] + b"\x00\x00" + body[12:]
    wire = encode_frame(abi.LINK_T_OBSERVATION, 0x0101, body)
    assert len(wire) <= abi.LINK_MAX_WIRE
    assert max_cobs_len(abi.LINK_MAX_DECODED) == abi.LINK_MAX_ENCODED
    assert len(cobs_encode(b"\x01" * abi.LINK_MAX_DECODED)) == abi.LINK_MAX_ENCODED
    out = FrameDecoder().feed(wire)
    assert isinstance(out[0], Frame)


def ok_wire(seq=1) -> bytes:
    return encode_frame(abi.LINK_T_HEARTBEAT, seq, abi.encode_heartbeat(abi.Heartbeat(100, 0, 1)))


def kinds(out):
    return [o.kind.value if isinstance(o, FrameError) else abi.TYPE_NAMES[o.type] for o in out]


def test_inject_garbage_then_resync():
    rng = random.Random(1)
    dec = FrameDecoder()
    garbage = bytes(rng.randint(1, 255) for _ in range(300))
    out = dec.feed(garbage + b"\x00" + ok_wire())
    assert kinds(out)[-1] == "HEARTBEAT"
    assert all(isinstance(o, FrameError) for o in out[:-1])


def test_inject_truncate():
    w = ok_wire()
    for cut in range(1, len(w) - 1):
        out = FrameDecoder().feed(w[: len(w) - 1 - cut] + b"\x00" + w)
        assert len(out) in (1, 2)
        assert kinds(out)[-1] == "HEARTBEAT"
        if len(out) == 2:
            assert isinstance(out[0], FrameError)


def test_inject_bad_crc_every_bit():
    body = abi.encode_heartbeat(abi.Heartbeat(100, 0, 1))
    good = build_decoded(abi.LINK_T_HEARTBEAT, 1, body)
    for i in range(len(good)):
        for bit in range(8):
            bad = bytearray(good)
            bad[i] ^= 1 << bit
            out = FrameDecoder().feed(cobs_encode(bytes(bad)) + b"\x00")
            assert len(out) == 1 and isinstance(out[0], FrameError), (i, bit)
            # a single-bit error is never accepted; it is reported as crc unless it hit type/len first
            assert out[0].kind in {"crc", "len", "oversize"}, (i, bit, out[0])


def test_inject_oversize_raw_then_resync():
    dec = FrameDecoder()
    out = dec.feed(b"\x01" * 5000)
    assert out == [] and dec.pending <= abi.LINK_MAX_ENCODED
    out = dec.feed(b"\x00" + ok_wire())
    assert kinds(out) == ["oversize", "HEARTBEAT"]
    assert out[0].raw_len == 5000


def test_oversize_decoded_length():
    # A consistent frame whose body is one byte over the largest body: decoded 532 > 531.
    dec = build_decoded(abi.LINK_T_OBSERVATION, 1, b"\x00" * (abi.LINK_MAX_BODY + 1))
    out = FrameDecoder().feed(cobs_encode(dec) + b"\x00")
    assert kinds(out) == ["oversize"]


def test_inject_flood():
    dec = FrameDecoder()
    stream = b"".join(ok_wire(i) for i in range(10_000))
    out = dec.feed(stream)
    assert len(out) == 10_000 and all(isinstance(o, Frame) for o in out)
    assert [o.seq for o in out[:3]] == [0, 1, 2] and out[-1].seq == 9_999
    assert dec.pending == 0


def test_inject_stall_partial_frame():
    dec = FrameDecoder()
    w = ok_wire()
    assert dec.feed(w[:-3]) == []
    assert dec.pending == len(w) - 3 <= abi.LINK_MAX_ENCODED
    out = dec.feed(b"\x00" + w)  # the sender gives up and resynchronises
    assert kinds(out)[-1] == "HEARTBEAT" and len(out) == 2


def test_inject_forbidden_types():
    # B->K types arriving at the bridge, unknown types, and COM3 cell frames on COM2 are all "type".
    act = abi.encode_action(abi.Action(op=abi.LINK_OP_HALT))
    for t, body in [(abi.LINK_T_ACTION, act), (abi.LINK_T_STOP, b"\x01"), (0x42, b""), (0x02, b"\x28"),
                    (0x81, b"\x00" * 12), (0x00, b""), (0xFF, b"")]:  # fmt: skip
        out = FrameDecoder(K2B_BOUNDS).feed(cobs_encode(build_decoded(t, 1, body)) + b"\x00")
        assert kinds(out) == ["type"], hex(t)
    # and the other way: K->B types are not accepted where B->K frames are expected
    out = FrameDecoder(B2K_BOUNDS).feed(ok_wire())
    assert kinds(out) == ["type"]


def test_len_field_mismatch_and_short():
    body = abi.encode_heartbeat(abi.Heartbeat(1, 0, 0))
    head = struct.pack("<BHH", abi.LINK_T_HEARTBEAT, 1, len(body) + 1) + body
    dec = head + struct.pack("<H", crc16(head))
    assert kinds(FrameDecoder().feed(cobs_encode(dec) + b"\x00")) == ["len"]
    for n in range(1, 7):
        assert kinds(FrameDecoder().feed(cobs_encode(b"\x15" * n) + b"\x00")) == ["short"]


def test_body_bounds_per_type():
    for t, (lo, hi) in abi.BODY_BOUNDS.items():
        bounds = K2B_BOUNDS if t in abi.K2B_TYPES else B2K_BOUNDS
        for n in {lo - 1, hi + 1} - {-1}:
            if n > abi.LINK_MAX_BODY:
                continue  # covered by the oversize tests
            out = FrameDecoder(bounds).feed(cobs_encode(build_decoded(t, 1, b"\x01" * n)) + b"\x00")
            assert kinds(out) == ["body_len"], (abi.TYPE_NAMES[t], n)


def test_field_rules_through_the_decoder():
    body = bytearray(abi.encode_heartbeat(abi.Heartbeat(1, 0, 0)))
    body[10] = 1  # rsv
    out = FrameDecoder().feed(encode_frame(abi.LINK_T_HEARTBEAT, 1, bytes(body)))
    assert kinds(out) == ["field"] and out[0].detail == "rsv_nonzero"
    out = FrameDecoder().feed(encode_frame(abi.LINK_T_REQUEST, 1, b"hi\x01"))
    assert kinds(out) == ["field"]


def test_non_canonical_cobs_rejected():
    raw = bytes(range(1, 255))  # 254 non-zero bytes: canonical is FF + data, no trailing group
    canon = cobs_encode(raw)
    assert cobs_decode(canon) == raw
    assert cobs_decode(canon + b"\x01") is None  # same bytes, non-canonical trailing empty group
    assert cobs_decode(b"") is None
    assert cobs_decode(b"\x05\x01\x02") is None  # code overruns the input
    assert cobs_decode(b"\x03\x01\x00") is None  # zero inside
    # A valid frame whose last 254 decoded bytes are non-zero (len high byte is the last zero): its canonical
    # encoding ends with a full 0xFF block. Appending an empty group decodes to the same bytes with a matching
    # CRC, and must still be rejected.
    for fill in range(0x20, 0x7F):
        good = build_decoded(abi.LINK_T_REQUEST, 0x0101, bytes([fill]) * 252)
        if 0 not in good[-2:]:
            break
    enc = cobs_encode(good)
    assert enc[-255] == 0xFF and cobs_decode(enc + b"\x01") is None
    assert isinstance(FrameDecoder().feed(enc + b"\x00")[0], Frame)
    assert kinds(FrameDecoder().feed(enc + b"\x01\x00")) == ["cobs"]


def test_decoder_never_raises_on_random_input():
    rng = random.Random(20261102)
    dec = FrameDecoder()
    for _ in range(3000):
        n = rng.randint(0, 700)
        data = bytes(rng.choice((0, 1, rng.randint(0, 255))) if rng.random() < 0.1 else rng.randint(0, 255)
                     for _ in range(n))  # fmt: skip
        dec.feed(data)
        assert dec.pending <= abi.LINK_MAX_ENCODED
    assert kinds(dec.feed(b"\x00" + ok_wire()))[-1] == "HEARTBEAT"
