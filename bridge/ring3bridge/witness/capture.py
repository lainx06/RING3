"""Bridge side of the fresh-frame protocol with ``tools/witness_capture_win.py`` (D-39c, D-58).

The two sides share one folder (Windows path seen from WSL as ``/mnt/c/...``; the caller passes it in, this
module takes no settings of its own). Per capture:

  1. the bridge picks a random 128-bit nonce and writes ``<nonce>.req`` (via a temp file + rename);
  2. the helper discards buffered frames, grabs a new one, embeds ``ring3-nonce:<nonce>`` as a JPEG comment,
     writes ``<nonce>.tmp.jpg`` and renames it to ``<nonce>.jpg``;
  3. the bridge accepts the file only if it appears within the timeout, carries the same nonce inside, is not
     older than the request and decodes. Otherwise ``CAN'T VERIFY: STALE_FRAME``.
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
COMMENT_PREFIX = b"ring3-nonce:"
MAX_FRAME_BYTES = 16 << 20
CLOCK_SKEW_S = 2.0  # the helper's file times come from the Windows clock


def add_nonce_comment(jpeg: bytes, nonce: str) -> bytes:
    """Insert a COM segment holding the nonce right after SOI (the helper has its own copy of this)."""
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    payload = COMMENT_PREFIX + nonce.encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


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


def request_frame(folder: Path, timeout_s: float = 5.0, poll_s: float = 0.05, nonce: str | None = None):
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
    if read_nonce_comment(data) != nonce:
        raise CantVerify(STALE_FRAME, "frame nonce does not match the request")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise CantVerify(STALE_FRAME, "frame does not decode")
    return img, data
