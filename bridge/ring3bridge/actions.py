"""Validated reply -> link frames (docs/LINK_ABI.md §3, §4).

A ``Call`` becomes a ``TEXT`` frame first when its op carries text, then exactly one 16-byte ``ACTION`` with
every unused byte zero. ``Done``/``Refuse`` become one ``FINAL``. Nonce and text id are deterministic (D-52):

  nonce   = LE u32 of SHA-256("ring3-nonce-v1" || seed[16] || action_index u32 LE)[0:4]
  text_id = action_index % 255 + 1

``seed`` is the run's 16-byte seed recorded in ``run_start``; ``action_index`` counts ACTIONs from 0 in the
run. Recorded replies therefore re-serialize byte for byte. No device limits here: the kernel decides.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from ring3bridge import abi
from ring3bridge.framing import encode_frame
from ring3bridge.strictjson import Call, Done, Refuse

NONCE_TAG = b"ring3-nonce-v1"
SEED_LEN = 16


def new_seed() -> bytes:
    return secrets.token_bytes(SEED_LEN)


def nonce_for(seed: bytes, index: int) -> int:
    if len(seed) != SEED_LEN or not 0 <= index <= 0xFFFFFFFF:
        raise ValueError("bad seed or index")
    d = hashlib.sha256(NONCE_TAG + seed + index.to_bytes(4, "little")).digest()
    return int.from_bytes(d[:4], "little")


def text_id_for(index: int) -> int:
    return index % 255 + 1


@dataclass(frozen=True)
class Outgoing:
    """Frames for one reply, in send order: [(type, body), ...]."""

    frames: tuple[tuple[int, bytes], ...]
    action: abi.Action | None = None
    index: int | None = None  # action index, None for FINAL


class ActionSerializer:
    def __init__(self, seed: bytes) -> None:
        if len(seed) != SEED_LEN:
            raise ValueError("seed must be 16 bytes")
        self.seed = bytes(seed)
        self.next_index = 0

    def serialize(self, reply: Call | Done | Refuse) -> Outgoing:
        if isinstance(reply, Done):
            return Outgoing(((abi.LINK_T_FINAL, abi.encode_final(abi.LINK_FINAL_DONE, reply.summary)),))
        if isinstance(reply, Refuse):
            return Outgoing(((abi.LINK_T_FINAL, abi.encode_final(abi.LINK_FINAL_REFUSE, reply.reason)),))
        if not isinstance(reply, Call):
            raise TypeError("not a validated reply")
        op = abi.OPS[reply.op]
        a = reply.args
        p1 = 0
        text: str | None = None
        if op == abi.LINK_OP_FAN_SET:
            p1 = int(a["duty"])
        elif op == abi.LINK_OP_SERVO_SET:
            p1 = int(a["deg"])
        elif op == abi.LINK_OP_LCD_SHOW:
            p1 = int(a["line"])
            text = str(a["text"])
        elif op in (abi.LINK_OP_READ_OBJECT, abi.LINK_OP_WRITE_OBJECT):
            p1 = abi.OBJECT_IDS[str(a["name"])]
            if op == abi.LINK_OP_WRITE_OBJECT:
                text = str(a["text"])
        index = self.next_index
        tid = text_id_for(index) if text is not None else 0
        action = abi.Action(op=op, p1=p1, nonce=nonce_for(self.seed, index), text_id=tid)
        body = abi.encode_action(action)  # raises on any field rule: never sends a bad frame
        frames: list[tuple[int, bytes]] = []
        if text is not None:
            frames.append((abi.LINK_T_TEXT, abi.encode_text(tid, text)))  # TEXT before ACTION
        frames.append((abi.LINK_T_ACTION, body))
        self.next_index += 1
        return Outgoing(tuple(frames), action, index)


class SeqCounter:
    """Per-direction frame sequence: from 0, +1 per frame, wrapping at 65536."""

    def __init__(self, start: int = 0) -> None:
        self.next = start & 0xFFFF

    def wire(self, ftype: int, body: bytes) -> bytes:
        w = encode_frame(ftype, self.next, body)
        self.next = (self.next + 1) & 0xFFFF
        return w
