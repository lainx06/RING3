"""RING3 witness capture helper for **Windows** Python (T-07, D-39c, D-58).

WSL2's stock kernel probably has no webcam driver, so this small helper runs on the Windows side, owns the
C922 and answers capture requests through files in one shared folder (the bridge sees it as /mnt/c/...).
It opens no network listener and runs nothing it reads: a request is only a file name.

Protocol (bridge side: bridge/ring3bridge/witness/capture.py):
  request   <nonce>.req          nonce = 32 lowercase hex digits; the file holds the same nonce
  response  <nonce>.jpg          written as <nonce>.tmp.jpg, then renamed (atomic on one volume)
Fresh frames only (D-58, D-80): OpenCV backends queue frames, so a plain grab() can return one captured before
the request. The helper sets CAP_PROP_BUFFERSIZE=1 where the backend supports it, notes when it saw the
request, then grabs and discards: a buffered frame returns at once, a grab that blocks >= LIVE_GRAB_S waited
for a new frame from the sensor. It captures only after >= FLUSH_GRABS grabs, >= LIVE_GRABS_MIN live ones and
MIN_AFTER_SEEN_S since the request was seen (else, after MAX_FLUSH_S, it writes nothing). The JPEG carries a
comment ``ring3-frame:v2;id=<request id>;seen_ns=<int>;cap_ns=<int>;live=<n>`` (Windows wall clock, ns); the
bridge rejects any frame without it, with another id, or captured before the request. Requests older than
REQUEST_MAX_AGE_S are deleted unanswered.

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
FLUSH_GRABS = 6  # card: >= 5
LIVE_GRAB_S = (
    0.012  # a grab that blocked this long waited for a new sensor frame (buffered ones return at once)
)
LIVE_GRABS_MIN = 2
MIN_AFTER_SEEN_S = 0.1
MAX_FLUSH_S = 3.0  # from the start of the flush, whatever ``seen`` says
MAX_FLUSH_GRABS = 300  # and never more grabs than this (a camera that never blocks can't spin forever)
FRAME_TAG = b"ring3-frame:v2;"
REQUEST_MAX_AGE_S = 10.0
POLL_S = 0.05
JPEG_QUALITY = 92


def add_frame_comment(jpeg: bytes, nonce: str, seen_ns: int, cap_ns: int, live: int) -> bytes:
    """COM segment right after SOI (the bridge's ``capture.add_frame_comment`` is the same bytes)."""
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    payload = FRAME_TAG + f"id={nonce};seen_ns={seen_ns};cap_ns={cap_ns};live={live}".encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


def grab_fresh_jpeg(cam, encode, seen: float, clock=time.monotonic, wall_ns=time.time_ns):  # noqa: ANN001
    """Discard buffered frames until live frames arrive after ``seen`` (a ``clock()`` time), then decode the
    newest one. Returns (JPEG, capture wall time ns, live grabs) or None (camera error or no live frame)."""
    grabs = live = 0
    start = clock()
    while True:
        t0 = clock()
        if not cam.grab():
            return None
        t1 = clock()
        grabs += 1
        if t1 - t0 >= LIVE_GRAB_S and t0 >= seen:
            live += 1
        if grabs >= FLUSH_GRABS and live >= LIVE_GRABS_MIN and t1 - seen >= MIN_AFTER_SEEN_S:
            cap_ns = wall_ns()
            break
        if t1 - start > MAX_FLUSH_S or grabs >= MAX_FLUSH_GRABS:
            return None
    ok, frame = cam.retrieve()
    if not ok or frame is None:
        return None
    return encode(frame), cap_ns, live


def pending_requests(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.req") if NONCE_RE.fullmatch(p.stem))


def serve_once(folder: Path, cam, encode, now=time.time, clock=time.monotonic, wall_ns=time.time_ns) -> int:  # noqa: ANN001
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
        seen, seen_ns = clock(), wall_ns()
        got = grab_fresh_jpeg(cam, encode, seen, clock, wall_ns)
        if got is not None:
            jpeg, cap_ns, live = got
            tmp, out = folder / f"{nonce}.tmp.jpg", folder / f"{nonce}.jpg"
            tmp.write_bytes(add_frame_comment(jpeg, nonce, seen_ns, cap_ns, live))
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
    cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # honoured by some backends only; the live-grab wait covers the rest
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
