"""RING3 witness capture helper for **Windows** Python (T-07, D-39c, D-58).

WSL2's stock kernel probably has no webcam driver, so this small helper runs on the Windows side, owns the
C922 and answers capture requests through files in one shared folder (the bridge sees it as /mnt/c/...).
It opens no network listener and runs nothing it reads: a request is only a file name.

Protocol (bridge side: bridge/ring3bridge/witness/capture.py):
  request   <nonce>.req          nonce = 32 lowercase hex digits; the file holds the same nonce
  response  <nonce>.jpg          written as <nonce>.tmp.jpg, then renamed (atomic on one volume)
The helper discards buffered frames (``grab()`` FLUSH_GRABS times) before decoding the newest one and embeds
``ring3-nonce:<nonce>`` as a JPEG comment, so the bridge can reject an old or mismatched file. Requests older
than REQUEST_MAX_AGE_S are deleted unanswered.

Setup (Windows, once):  py -m pip install opencv-python
Run (Windows):          set RING3_WITNESS_DIR=C:\\ring3-witness  &&  py tools\\witness_capture_win.py
Options:                --camera N (default 0)  --width/--height (default 1920x1080)  --once  --check
The folder comes from RING3_WITNESS_DIR only; it is never committed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ENV_DIR = "RING3_WITNESS_DIR"
NONCE_RE = re.compile(r"[0-9a-f]{32}")
COMMENT_PREFIX = b"ring3-nonce:"
FLUSH_GRABS = 6  # card: >= 5
REQUEST_MAX_AGE_S = 10.0
POLL_S = 0.05
JPEG_QUALITY = 92


def add_nonce_comment(jpeg: bytes, nonce: str) -> bytes:
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    payload = COMMENT_PREFIX + nonce.encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


def grab_fresh_jpeg(cam, encode) -> bytes | None:  # noqa: ANN001 - a cv2.VideoCapture-like object
    """Discard buffered frames, decode the newest one, return it as JPEG (None on a camera error)."""
    for _ in range(FLUSH_GRABS):
        if not cam.grab():
            return None
    ok, frame = cam.retrieve()
    if not ok or frame is None:
        return None
    return encode(frame)


def pending_requests(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.req") if NONCE_RE.fullmatch(p.stem))


def serve_once(folder: Path, cam, encode, now=time.time) -> int:  # noqa: ANN001
    """Answer every pending request once. Returns the number of frames written."""
    written = 0
    for req in pending_requests(folder):
        nonce = req.stem
        try:
            body = req.read_bytes()[:64]
            age = now() - req.stat().st_mtime
        except FileNotFoundError:
            continue
        if body.strip() != nonce.encode("ascii") or age > REQUEST_MAX_AGE_S:
            req.unlink(missing_ok=True)
            continue
        jpeg = grab_fresh_jpeg(cam, encode)
        if jpeg is not None:
            tmp, out = folder / f"{nonce}.tmp.jpg", folder / f"{nonce}.jpg"
            tmp.write_bytes(add_nonce_comment(jpeg, nonce))
            os.replace(tmp, out)
            written += 1
        req.unlink(missing_ok=True)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RING3 witness capture helper (Windows)")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--once", action="store_true", help="answer pending requests once and exit")
    ap.add_argument("--check", action="store_true", help="open the camera, grab one frame, print its size")
    a = ap.parse_args(argv)
    import cv2  # Windows-side OpenCV; imported here so the protocol functions load without it

    folder = Path(os.environ.get(ENV_DIR, ""))
    if not a.check and (not os.environ.get(ENV_DIR) or not folder.is_dir()):
        print(f"set {ENV_DIR} to an existing folder", file=sys.stderr)
        return 2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cam = cv2.VideoCapture(a.camera, backend)
    if not cam.isOpened():
        print(f"camera {a.camera} did not open", file=sys.stderr)
        return 1
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)

    def encode(frame) -> bytes:  # noqa: ANN001
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return buf.tobytes()

    try:
        if a.check:
            ok, frame = cam.read()
            print(f"camera {a.camera}: {'frame ' + 'x'.join(map(str, frame.shape)) if ok else 'no frame'}")
            return 0 if ok else 1
        print(f"serving capture requests in {ENV_DIR} (Ctrl+C stops)")
        while True:
            serve_once(folder, cam, encode)
            if a.once:
                return 0
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 0
    finally:
        cam.release()


if __name__ == "__main__":
    sys.exit(main())
