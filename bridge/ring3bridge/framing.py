"""COBS + CRC-16/CCITT-FALSE framing for the RING3 links (docs/LINK_ABI.md §1).

decoded = type u8 | seq u16 LE | len u16 LE | body[len] | crc16 LE;  wire = COBS(decoded) 0x00

The incremental ``FrameDecoder`` never raises on input bytes: every complete frame becomes a ``Frame`` or a
``FrameError``. Raw input between delimiters is capped at the maximum encoded length; longer input is
discarded up to the next ``0x00`` and reported once as ``oversize``. Only canonical COBS is accepted.
"""

from __future__ import annotations

import enum
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ring3bridge import abi


def crc16(data: bytes, crc: int = abi.LINK_CRC_INIT) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ abi.LINK_CRC_POLY) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    """Canonical COBS (as in firmware/test/link_vectors.txt), without the delimiter."""
    out = bytearray([0])
    code_pos = 0
    code = 1
    for i, byte in enumerate(data):
        if byte == 0:
            out[code_pos] = code
            code_pos = len(out)
            out.append(0)
            code = 1
            continue
        out.append(byte)
        code += 1
        if code == 0xFF and i + 1 < len(data):
            out[code_pos] = code
            code_pos = len(out)
            out.append(0)
            code = 1
    out[code_pos] = code
    return bytes(out)


def cobs_decode(enc: bytes) -> bytes | None:
    """Strict COBS decode: None for a zero byte, a code overrunning the input, or a non-canonical encoding."""
    out = bytearray()
    i = 0
    n = len(enc)
    if n == 0:
        return None
    while i < n:
        code = enc[i]
        if code == 0:
            return None
        end = i + code
        if end > n:
            return None
        block = enc[i + 1 : end]
        if 0 in block:
            return None
        out += block
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    result = bytes(out)
    if cobs_encode(result) != enc:  # one byte sequence, one encoding (D-52)
        return None
    return result


def max_cobs_len(n: int) -> int:
    """Worst-case canonical COBS length of n input bytes."""
    return 1 if n == 0 else n + -(-n // 254)


class FrameErrorKind(enum.StrEnum):
    OVERSIZE = "oversize"  # raw input above the encoded cap, or decoded length above the decoded cap
    COBS = "cobs"  # malformed or non-canonical COBS
    SHORT = "short"  # decoded length below 7
    LEN = "len"  # decoded length != 7 + len field
    CRC = "crc"
    TYPE = "type"  # type not known for this direction
    BODY_LEN = "body_len"  # body length outside the type's bounds
    FIELD = "field"  # body rule (reserved bytes, ranges, text) violated


@dataclass(frozen=True)
class Frame:
    type: int
    seq: int
    body: bytes
    msg: object = None  # typed body (abi.Body) when the decoder has a body decoder


@dataclass(frozen=True)
class FrameError:
    kind: FrameErrorKind
    raw_len: int  # bytes between delimiters (capped count for oversize)
    detail: str = ""


Bounds = dict[int, tuple[int, int]]
BodyDecoder = Callable[[int, bytes], object]


def build_decoded(ftype: int, seq: int, body: bytes) -> bytes:
    head = struct.pack("<BHH", ftype, seq & 0xFFFF, len(body)) + body
    return head + struct.pack("<H", crc16(head))


def encode_frame(ftype: int, seq: int, body: bytes, bounds: Bounds | None = None) -> bytes:
    """Wire bytes for one frame (COBS + delimiter). Raises ValueError if the body breaks its type's bounds."""
    bounds = abi.BODY_BOUNDS if bounds is None else bounds
    lim = bounds.get(ftype)
    if lim is None or not lim[0] <= len(body) <= lim[1]:
        raise ValueError(f"frame type 0x{ftype:02x} body length {len(body)} outside bounds")
    return cobs_encode(build_decoded(ftype, seq, body)) + b"\x00"


def parse_decoded(
    dec: bytes, bounds: Bounds, max_decoded: int, body_decoder: BodyDecoder | None = None, raw_len: int = 0
) -> Frame | FrameError:
    """Checks one COBS-decoded payload, in the order of the shared vectors: size, length, CRC, type, body."""
    if len(dec) > max_decoded:
        return FrameError(FrameErrorKind.OVERSIZE, raw_len, f"decoded {len(dec)}")
    if len(dec) < abi.LINK_MIN_DECODED:
        return FrameError(FrameErrorKind.SHORT, raw_len, f"decoded {len(dec)}")
    ftype, seq, blen = struct.unpack_from("<BHH", dec)
    if len(dec) != abi.LINK_MIN_DECODED + blen:
        return FrameError(FrameErrorKind.LEN, raw_len, f"len {blen}, decoded {len(dec)}")
    (crc,) = struct.unpack_from("<H", dec, len(dec) - 2)
    if crc16(dec[:-2]) != crc:
        return FrameError(FrameErrorKind.CRC, raw_len)
    lim = bounds.get(ftype)
    if lim is None:
        return FrameError(FrameErrorKind.TYPE, raw_len, f"type 0x{ftype:02x}")
    if not lim[0] <= blen <= lim[1]:
        return FrameError(FrameErrorKind.BODY_LEN, raw_len, f"type 0x{ftype:02x} len {blen}")
    body = bytes(dec[abi.LINK_OFF_BODY : abi.LINK_OFF_BODY + blen])
    msg = None
    if body_decoder is not None:
        msg = body_decoder(ftype, body)
        if isinstance(msg, abi.BodyError):
            return FrameError(FrameErrorKind.FIELD, raw_len, msg.reason)
    return Frame(ftype, seq, body, msg)


def _restrict(types: Iterable[int]) -> Bounds:
    return {t: abi.BODY_BOUNDS[t] for t in types}


#: what the bridge accepts from the kernel, and what the kernel accepts from the bridge
K2B_BOUNDS: Bounds = _restrict(abi.K2B_TYPES)
B2K_BOUNDS: Bounds = _restrict(abi.B2K_TYPES)


class FrameDecoder:
    """Incremental decoder: ``feed()`` bytes in any chunking, get frames and errors out. Bounded memory."""

    def __init__(
        self,
        bounds: Bounds | None = None,
        max_decoded: int = abi.LINK_MAX_DECODED,
        body_decoder: BodyDecoder | None = abi.decode_body,
    ) -> None:
        self.bounds = K2B_BOUNDS if bounds is None else bounds
        self.max_decoded = max_decoded
        self.max_encoded = max_cobs_len(max_decoded)
        self.body_decoder = body_decoder
        self._buf = bytearray()
        self._overflow = 0  # bytes discarded since the cap was hit (0 = not overflowing)
        self.frames = 0
        self.errors = 0

    @property
    def pending(self) -> int:
        """Bytes held waiting for a delimiter (never above ``max_encoded``)."""
        return len(self._buf)

    def feed(self, data: bytes) -> list[Frame | FrameError]:
        out: list[Frame | FrameError] = []
        for byte in data:
            if byte == 0:
                item = self._end_of_frame()
                if item is not None:
                    out.append(item)
                    if isinstance(item, Frame):
                        self.frames += 1
                    else:
                        self.errors += 1
            elif self._overflow:
                self._overflow += 1
            elif len(self._buf) >= self.max_encoded:
                self._overflow = len(self._buf) + 1
                self._buf.clear()
            else:
                self._buf.append(byte)
        return out

    def _end_of_frame(self) -> Frame | FrameError | None:
        if self._overflow:
            n = self._overflow
            self._overflow = 0
            return FrameError(FrameErrorKind.OVERSIZE, n, f"raw {n} > {self.max_encoded}")
        if not self._buf:
            return None  # empty frame (back-to-back delimiters): a resync, not an error
        raw = bytes(self._buf)
        self._buf.clear()
        dec = cobs_decode(raw)
        if dec is None:
            return FrameError(FrameErrorKind.COBS, len(raw))
        return parse_decoded(dec, self.bounds, self.max_decoded, self.body_decoder, len(raw))
