"""``ring3 fuzz-link`` (INV-02): seeded malformed COM2 frames at the kernel; the kernel must drop, count and
audit every one, keep its heartbeat and never panic. The generator is deterministic for a seed, and every
frame it yields is checked against the bridge's own strict decoder (bridge->kernel bounds) so that nothing
accidentally valid is sent: the kernel's drop count must then equal the number of frames sent.
"""

from __future__ import annotations

import random
import struct
import time
from collections.abc import Iterator
from dataclasses import dataclass

from ring3bridge import abi
from ring3bridge.framing import B2K_BOUNDS, Frame, FrameDecoder, build_decoded, cobs_encode
from ring3bridge.link import Link

KINDS = (
    "random_bytes",
    "bad_crc",
    "bad_len_field",
    "unknown_type",
    "k2b_type",
    "body_too_short",
    "body_too_long",
    "oversize_raw",
    "oversize_decoded",
    "cobs_zero_inside",
    "cobs_overrun",
    "cobs_noncanonical",
    "short_decoded",
    "truncated_valid",
)


def _valid_decoded(rng: random.Random) -> bytes:
    """A well-formed bridge->kernel frame to mutate."""
    t = rng.choice(sorted(abi.B2K_TYPES))
    lo, hi = abi.BODY_BOUNDS[t]
    if t == abi.LINK_T_ACTION:
        body = abi.encode_action(abi.Action(op=abi.LINK_OP_HALT, nonce=rng.getrandbits(32)))
    elif t == abi.LINK_T_STOP:
        body = bytes([rng.randint(abi.LINK_STOP_MIN, abi.LINK_STOP_MAX)])
    else:
        n = rng.randint(lo, hi)
        body = bytes([rng.randint(1, 255)]) + bytes(rng.randint(0x20, 0x7E) for _ in range(n - 1))
    return build_decoded(t, rng.getrandbits(16), body)


def _mutate(kind: str, rng: random.Random) -> bytes:
    """Raw wire bytes (without the trailing delimiter) for one malformed frame."""
    dec = bytearray(_valid_decoded(rng))
    if kind == "random_bytes":
        n = rng.randint(1, 80)
        return bytes(rng.randint(1, 255) for _ in range(n))
    if kind == "bad_crc":
        dec[-1] ^= rng.randint(1, 255)
        return cobs_encode(bytes(dec))
    if kind == "bad_len_field":
        (blen,) = struct.unpack_from("<H", dec, abi.LINK_OFF_LEN)
        struct.pack_into("<H", dec, abi.LINK_OFF_LEN, (blen + rng.randint(1, 600)) & 0xFFFF)
        return cobs_encode(bytes(dec))
    if kind == "unknown_type":
        t = rng.choice([x for x in range(256) if x not in abi.B2K_TYPES and x not in abi.K2B_TYPES])
        body = dec[abi.LINK_OFF_BODY : -2]
        return cobs_encode(build_decoded(t, rng.getrandbits(16), bytes(body)))
    if kind == "k2b_type":
        t = rng.choice(sorted(abi.K2B_TYPES))
        lo, hi = abi.BODY_BOUNDS[t]
        body = bytes(rng.randint(0, 255) for _ in range(rng.randint(lo, min(hi, 64))))
        return cobs_encode(build_decoded(t, rng.getrandbits(16), body))
    if kind in ("body_too_short", "body_too_long"):
        t = rng.choice(sorted(abi.B2K_TYPES))
        lo, hi = abi.BODY_BOUNDS[t]
        n = rng.randint(0, lo - 1) if kind == "body_too_short" and lo > 0 else rng.randint(hi + 1, hi + 40)
        if kind == "body_too_short" and lo == 0:
            n = hi + 1
        body = bytes(rng.randint(0, 255) for _ in range(n))
        return cobs_encode(build_decoded(t, rng.getrandbits(16), body))
    if kind == "oversize_raw":
        n = rng.randint(abi.LINK_MAX_ENCODED + 1, abi.LINK_MAX_ENCODED + 300)
        return bytes(rng.randint(1, 255) for _ in range(n))
    if kind == "oversize_decoded":
        body = bytes(rng.randint(0, 255) for _ in range(rng.randint(abi.LINK_MAX_BODY + 1, 526)))
        enc = cobs_encode(build_decoded(abi.LINK_T_FINAL, 1, body))
        return enc[: abi.LINK_MAX_ENCODED]  # raw within the cap, decoded (or COBS) over it
    if kind == "cobs_zero_inside":
        enc = bytearray(cobs_encode(bytes(dec)))
        # a stray delimiter splits the frame into two malformed pieces; keep one piece by truncating
        cut = rng.randint(1, len(enc) - 1)
        return bytes(enc[:cut])
    if kind == "cobs_overrun":
        enc = bytearray(cobs_encode(bytes(dec)))
        enc[0] = 0xFF  # the first code claims 254 bytes the frame does not have
        return bytes(enc[: rng.randint(2, min(len(enc), 60))])
    if kind == "cobs_noncanonical":
        # 254 non-zero bytes then an empty final group: decodes to the same bytes on a lenient decoder
        data = bytes(rng.randint(1, 255) for _ in range(254))
        return cobs_encode(data) + b"\x01"
    if kind == "short_decoded":
        n = rng.randint(1, abi.LINK_MIN_DECODED - 1)
        return cobs_encode(bytes(rng.randint(0, 255) for _ in range(n)))
    if kind == "truncated_valid":
        enc = cobs_encode(bytes(dec))
        return enc[: rng.randint(1, len(enc) - 1)]
    raise ValueError(kind)


def malformed_frames(seed: int, count: int) -> Iterator[tuple[str, bytes]]:
    """Yields (kind, wire bytes incl. the delimiter). Every frame is rejected by the strict decoder; a
    candidate the decoder would accept is skipped (never happens for the kinds above, but it is checked)."""
    rng = random.Random(seed)
    check = FrameDecoder(B2K_BOUNDS, body_decoder=None)
    n = 0
    while n < count:
        kind = KINDS[rng.randrange(len(KINDS))]
        raw = _mutate(kind, rng)
        if not raw or 0 in raw:
            continue
        out = check.feed(raw + b"\x00")
        if len(out) != 1 or isinstance(out[0], Frame):
            continue  # accidentally valid or not exactly one frame: skip
        n += 1
        yield kind, raw + b"\x00"


@dataclass
class FuzzResult:
    sent: int
    kinds: dict[str, int]
    heartbeats: int
    first_tick: int | None
    last_tick: int | None
    dropped_reported: int | None
    drop_audits: int
    audits: int
    frame_errors: int
    seconds: float

    def ok(self) -> bool:
        return (
            self.heartbeats >= 2
            and self.first_tick is not None
            and self.last_tick is not None
            and self.last_tick > self.first_tick
            and self.dropped_reported == self.sent
            and self.drop_audits == self.sent
        )

    def report(self) -> str:
        lines = [
            f"fuzz-link: sent {self.sent} malformed frames in {self.seconds:.1f} s",
            "  kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(self.kinds.items())),
            f"  heartbeats {self.heartbeats} (tick {self.first_tick} -> {self.last_tick}), "
            f"kernel frames_dropped={self.dropped_reported}, FRAME_DROP audits={self.drop_audits} "
            f"of {self.audits} audits, bridge-side frame errors={self.frame_errors}",
            "  FUZZ OK: every frame counted and audited, heartbeat continued"
            if self.ok()
            else "  FUZZ FAILED",
        ]
        return "\n".join(lines)


def run_fuzz(
    link: Link,
    seed: int,
    count: int,
    settle_s: float = 3.0,
    burst: int = 50,
    on_frame=None,
) -> FuzzResult:
    """Sends `count` malformed frames in bursts while reading the kernel's frames; then waits `settle_s` for
    the audits and a heartbeat that reports the final drop count."""
    t0 = time.monotonic()
    kinds: dict[str, int] = {}
    heartbeats = 0
    first_tick = last_tick = None
    dropped: int | None = None
    drop_audits = audits = 0

    def absorb(timeout: float) -> None:
        nonlocal heartbeats, first_tick, last_tick, dropped, drop_audits, audits
        for item in link.recv(timeout):
            if on_frame is not None:
                on_frame(item)
            if not isinstance(item, Frame):
                continue
            if isinstance(item.msg, abi.Heartbeat):
                heartbeats += 1
                if first_tick is None:
                    first_tick = item.msg.tick
                last_tick = item.msg.tick
                dropped = item.msg.dropped
            elif isinstance(item.msg, abi.Audit):
                audits += 1
                if abi.POLICY_CODE_NAMES[item.msg.code] == "FRAME_DROP":
                    drop_audits += 1

    absorb(0.5)
    sent = 0
    pending = bytearray()
    for kind, wire in malformed_frames(seed, count):
        kinds[kind] = kinds.get(kind, 0) + 1
        pending += wire
        sent += 1
        if sent % burst == 0:
            link.send_raw(bytes(pending))
            pending.clear()
            absorb(0.0)
    if pending:
        link.send_raw(bytes(pending))
    # wait for the kernel to report the final count (heartbeat) and every audit
    deadline = time.monotonic() + max(settle_s, 1.0)
    while time.monotonic() < deadline:
        absorb(0.2)
        if dropped == sent and drop_audits >= sent and heartbeats >= 2:
            absorb(1.2)  # one more heartbeat after the last drop shows the kernel is still alive
            break
    return FuzzResult(sent, kinds, heartbeats, first_tick, last_tick, dropped, drop_audits, audits,
                      link.decoder.errors, time.monotonic() - t0)  # fmt: skip
