"""``ring3 frames-selftest``: CRC check value, the shared link vectors byte for byte, COM2 round trips."""

from __future__ import annotations

import re
from pathlib import Path

from ring3bridge import abi
from ring3bridge.config import REPO
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
)

VECTORS = REPO / "firmware" / "test" / "link_vectors.txt"
PROTOCOL = REPO / "firmware" / "ring3cell" / "protocol.h"
# field-value rules belong to the receiving device; framing accepts those frames
FRAMING_EXPECT = {"ok": None, "field": None, "crc": "crc", "len": "len", "type": "type",
                  "body_len": "body_len", "oversize": "oversize"}  # fmt: skip


def _hex(s: str) -> bytes:
    return b"" if s == "-" else bytes.fromhex(s)


def cell_bounds(protocol: Path = PROTOCOL) -> tuple[dict[int, tuple[int, int]], int]:
    d = {
        k: int(v, 0)
        for k, v in re.findall(r"#define (CELL_\w+) (0x[0-9A-Fa-f]+|\d+)\b", protocol.read_text())
    }
    b = {
        d["CELL_T_PING"]: (0, 0),
        d["CELL_T_FAN"]: (d["CELL_FAN_LEN"],) * 2,
        d["CELL_T_SERVO"]: (d["CELL_SERVO_LEN"],) * 2,
        d["CELL_T_LCD"]: (d["CELL_LCD_MIN_LEN"], d["CELL_LCD_MAX_LEN"]),
        d["CELL_T_ALARM"]: (d["CELL_ALARM_LEN"],) * 2,
        d["CELL_T_ESTOP_RESET"]: (0, 0),
        d["CELL_T_STATE"]: (d["CELL_STATE_LEN"],) * 2,
    }
    return b, abi.LINK_MIN_DECODED + d["CELL_MAX_BODY"]


def run() -> tuple[bool, list[str]]:
    lines: list[str] = []
    fails = 0
    checks = 0

    def check(ok: bool, what: str) -> None:
        nonlocal fails, checks
        checks += 1
        if not ok:
            fails += 1
            lines.append(f"FAIL {what}")

    check(crc16(b"123456789") == abi.LINK_CRC_CHECK, "crc check value 0x29B1")
    bounds, max_dec = cell_bounds()
    n_vec = 0
    for line in VECTORS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        n_vec += 1
        if f[0] == "crc":
            check(crc16(_hex(f[1])) == int(f[2], 16), f"crc {f[1][:16]}")
        elif f[0] == "cobs":
            raw, enc = _hex(f[2]), _hex(f[3])
            check(cobs_encode(raw) == enc and cobs_decode(enc) == raw, f"cobs {f[1]}")
        elif f[0] == "frame":
            name, expect, dec, wire = f[1], f[2], _hex(f[3]), _hex(f[4])
            check(cobs_encode(dec) + b"\x00" == wire, f"frame {name} encoding")
            out = FrameDecoder(bounds, max_dec, body_decoder=None).feed(wire)
            want = FRAMING_EXPECT.get(expect, "?")
            if want is None:
                ok = len(out) == 1 and isinstance(out[0], Frame)
                ok = ok and build_decoded(out[0].type, out[0].seq, out[0].body) == dec
            else:
                ok = len(out) == 1 and isinstance(out[0], FrameError) and out[0].kind == want
            check(ok, f"frame {name} expect {expect}")
        else:
            check(False, f"unknown vector line {f[0]}")
    lines.append(f"shared vectors: {n_vec} lines from firmware/test/link_vectors.txt")
    for t, (lo, hi) in sorted(abi.BODY_BOUNDS.items()):
        dirb = K2B_BOUNDS if t in abi.K2B_TYPES else B2K_BOUNDS
        for n in {lo, hi}:
            body = _valid_body(t, n)
            out = FrameDecoder(dirb).feed(encode_frame(t, 7, body))
            ok = len(out) == 1 and isinstance(out[0], Frame) and out[0].body == body
            check(ok, f"COM2 {abi.TYPE_NAMES[t]} round trip, body {n} B")
    lines.append(f"COM2 types: {len(abi.BODY_BOUNDS)}, max decoded {abi.LINK_MAX_DECODED} B, "
                 f"max encoded {abi.LINK_MAX_ENCODED} B")  # fmt: skip
    lines.append(f"FRAMES {'OK' if fails == 0 else 'FAILED'}: {checks - fails}/{checks} checks")
    return fails == 0, lines


def _valid_body(t: int, n: int) -> bytes:
    if t == abi.LINK_T_HELLO:
        return abi.encode_hello(abi.Hello(abi.LINK_ABI_VERSION, 0, b"\x01" * 12, b"\x02" * 16))
    if t == abi.LINK_T_OBSERVATION:
        return abi.encode_observation(abi.Observation(1, 0, 1, False, b"o" * (n - abi.LINK_OBS_HDR_LEN)))
    if t == abi.LINK_T_AUDIT:
        return abi.encode_audit(abi.Audit(1, 1, 3, 6, 1, 0, 60, 0, 0, False))
    if t == abi.LINK_T_EVENT:
        return abi.encode_event(abi.Event(6, 0, 0, 1, 0, (0, 0, 0, 0)))
    if t == abi.LINK_T_HEARTBEAT:
        return abi.encode_heartbeat(abi.Heartbeat(1, 0, 0))
    if t == abi.LINK_T_ACTION:
        return abi.encode_action(abi.Action(op=abi.LINK_OP_HALT, nonce=1))
    if t == abi.LINK_T_TEXT:
        return bytes([1]) + b"t" * (n - 1)
    if t == abi.LINK_T_FINAL:
        return bytes([abi.LINK_FINAL_DONE]) + b"f" * (n - 1)
    if t == abi.LINK_T_STOP:
        return bytes([abi.LINK_STOP_OPERATOR])
    return b"r" * n  # REQUEST
