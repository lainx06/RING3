"""Bridge side of the fresh-frame protocol with ``tools/witness_capture_win.py`` (D-39c, D-58).

The two sides share one folder (Windows path seen from WSL as ``/mnt/c/...``; the caller passes it in, this
module takes no settings of its own). Per capture:

  1. the bridge picks a random 128-bit nonce and writes ``<nonce>.req`` (via a temp file + rename);
  2. the helper notes when it saw the request, discards frames until it can prove one is newer (D-80),
     embeds ``ring3-frame:v3;id=<nonce>;seen_ns=..;cap_ns=..;live=..;changed=..`` as a JPEG comment, writes
     ``<nonce>.tmp.jpg`` and renames it to ``<nonce>.jpg``;
  3. the bridge accepts the file only if it appears within ``REQUEST_TIMEOUT_S``, decodes, and its comment
     proves a fresh capture for this request, on the helper's own clock: same id, captured after the helper
     saw the request (which exists only after the bridge wrote it), and either >= 2 grabs that waited for the
     sensor or >= 250 ms after seen with a frame that changed. Cross-clock checks (file time, capture time vs
     the bridge's clock) only catch grossly old files: WSL2's clock can drift from Windows', so their
     tolerance is ``CLOCK_SKEW_S``. Anything else, including an older helper's comment, is ``STALE_FRAME``.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

import cv2
import numpy as np

from ring3bridge.witness import STALE_FRAME, CantVerify

NONCE_RE = re.compile(r"[0-9a-f]{32}")
COMMENT_PREFIX = b"ring3-nonce:"  # v1 (Part A helper): no capture time, no longer accepted (D-80)
FRAME_TAG = b"ring3-frame:v3;"
FRAME_RE = re.compile(
    rb"ring3-frame:v3;id=([0-9a-f]{32});seen_ns=([1-9][0-9]{0,19});cap_ns=([1-9][0-9]{0,19});"
    rb"live=([0-9]{1,6});changed=([01])"
)
LIVE_GRABS_MIN = 2  # = helper LIVE_GRABS_MIN
LATEST_WAIT_NS = 250_000_000  # = helper LATEST_WAIT_S
#: The helper's worst case per request: up to MAX_FLUSH_S (3.0) of flushing after it sees the request, its
#: poll interval (0.05), retrieve + JPEG encode + write + rename over /mnt/c (allow 1.0); plus 2.0 s margin.
#: The camera is opened once at helper start, not per request.
REQUEST_TIMEOUT_S = 3.0 + 0.05 + 1.0 + 2.0
MAX_FLUSH_NS = 10 * 10**9  # capture more than this after the request was seen: not this request's frame
MAX_FRAME_BYTES = 16 << 20
CLOCK_SKEW_S = 120.0  # cross-clock sanity only (WSL2 vs Windows clocks can drift); the proof is same-clock


def add_nonce_comment(jpeg: bytes, nonce: str) -> bytes:
    """Insert a COM segment holding the nonce right after SOI (the helper has its own copy of this)."""
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    payload = COMMENT_PREFIX + nonce.encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


def add_frame_comment(jpeg: bytes, nonce: str, seen_ns: int, cap_ns: int, live: int, changed: int) -> bytes:
    """The v3 comment the helper writes (same bytes as ``tools/witness_capture_win.add_frame_comment``)."""
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    body = f"id={nonce};seen_ns={seen_ns};cap_ns={cap_ns};live={live};changed={changed}"
    payload = FRAME_TAG + body.encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


def read_frame_comment(jpeg: bytes) -> tuple[str, int, int, int, int] | None:
    """(id, seen_ns, cap_ns, live, changed) from a v3 COM segment directly after SOI, or None."""
    if len(jpeg) < 6 or jpeg[:4] != b"\xff\xd8\xff\xfe":
        return None
    n = int.from_bytes(jpeg[4:6], "big")
    m = FRAME_RE.fullmatch(jpeg[6 : 4 + n]) if n >= 2 else None
    if m is None:
        return None
    return m.group(1).decode("ascii"), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))


def read_nonce_comment(jpeg: bytes) -> str | None:
    """The nonce from a COM segment directly after SOI, or None."""
    if len(jpeg) < 6 or jpeg[:4] != b"\xff\xd8\xff\xfe":
        return None
    n = int.from_bytes(jpeg[4:6], "big")
    body = jpeg[6 : 4 + n]
    if len(body) != n - 2 or not body.startswith(COMMENT_PREFIX):
        return None
    s = body[len(COMMENT_PREFIX) :].decode("ascii", "replace")
    return s if NONCE_RE.fullmatch(s) else None


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def request_frame(
    folder: Path, timeout_s: float = REQUEST_TIMEOUT_S, poll_s: float = 0.05, nonce: str | None = None
):
    """Ask the helper for a fresh still. Returns (nonce, decoded BGR image, JPEG bytes)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise CantVerify(STALE_FRAME, "capture folder missing")
    nonce = nonce or secrets.token_hex(16)
    if not NONCE_RE.fullmatch(nonce):
        raise ValueError("nonce must be 32 lowercase hex digits")
    req, jpg = folder / f"{nonce}.req", folder / f"{nonce}.jpg"
    t_req = time.time()
    _write_atomic(req, nonce.encode("ascii"))
    deadline = time.monotonic() + timeout_s
    try:
        while not jpg.exists():
            if time.monotonic() >= deadline:
                raise CantVerify(STALE_FRAME, f"no frame within {timeout_s:.1f} s")
            time.sleep(poll_s)
        return (nonce, *check_frame(jpg, nonce, t_req))
    finally:
        for p in (req, jpg):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def check_frame(jpg: Path, nonce: str, t_req: float) -> tuple[np.ndarray, bytes]:
    st = jpg.stat()
    if st.st_size > MAX_FRAME_BYTES:
        raise CantVerify(STALE_FRAME, "frame file too large")
    if st.st_mtime < t_req - CLOCK_SKEW_S:
        raise CantVerify(STALE_FRAME, "frame file older than the request")
    data = jpg.read_bytes()
    meta = read_frame_comment(data)
    if meta is None:
        old = read_nonce_comment(data) is not None or b"ring3-frame:v2;" in data[:64]
        raise CantVerify(STALE_FRAME, "frame from an older helper: restart it" if old else "no frame proof")
    fid, seen_ns, cap_ns, live, changed = meta
    if fid != nonce:
        raise CantVerify(STALE_FRAME, "frame request id does not match the request")
    if cap_ns <= seen_ns or cap_ns - seen_ns > MAX_FLUSH_NS:
        raise CantVerify(STALE_FRAME, "frame captured before the helper saw the request")
    if cap_ns < int((t_req - CLOCK_SKEW_S) * 1e9):
        raise CantVerify(STALE_FRAME, "frame captured before the request")
    if live < LIVE_GRABS_MIN and not (changed and cap_ns - seen_ns >= LATEST_WAIT_NS):
        raise CantVerify(
            STALE_FRAME,
            f"no freshness proof ({live} waiting grab(s), changed={changed}, "
            f"{(cap_ns - seen_ns) // 1_000_000} ms after seen)",
        )
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise CantVerify(STALE_FRAME, "frame does not decode")
    return img, data
