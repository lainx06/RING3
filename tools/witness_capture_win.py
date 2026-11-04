"""RING3 witness capture helper for **Windows** Python (T-07, D-39c, D-58).

WSL2's stock kernel probably has no webcam driver, so this small helper runs on the Windows side, owns the
C922 and answers capture requests through files in one shared folder (the bridge sees it as /mnt/c/...).
It opens no network listener and runs nothing it reads: a request is only a file name.

Protocol (bridge side: bridge/ring3bridge/witness/capture.py):
  request   <nonce>.req          nonce = 32 lowercase hex digits; the file holds the same nonce
  response  <nonce>.jpg          written as <nonce>.tmp.jpg, then renamed (atomic on one volume)
Fresh frames only (D-58, D-80): a backend may hand out a queued frame, so after it sees a request the helper
grabs and discards until it can prove a frame is newer than the request, on its own clock: queue backends
(e.g. MSMF: a grab waits for the sensor once the queue is drained) -> >= LIVE_GRABS_MIN grabs that waited >=
LIVE_GRAB_S; latest-frame backends (e.g. DSHOW: a grab never waits) -> >= LATEST_WAIT_S after seen and the
frame differs from one retrieved after seen (a frozen stream repeats it exactly). No proof within MAX_FLUSH_S:
no file. The JPEG comment ``ring3-frame:v3;id=..;seen_ns=..;cap_ns=..;live=..;changed=..`` lets the bridge
check the proof. One log line per request. Requests already present at start are deleted.

Setup (Windows, once):  py -m pip install opencv-python
Run (PowerShell):       $env:RING3_WITNESS_DIR = "C:\\ring3-witness"; py <repo>\\tools\\witness_capture_win.py
Options:                --camera N (default 0)  --backend dshow|msmf|any  --width/--height (1920x1080)
                        --once  --check  --selftest (3 captures without a request; no folder needed)
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
LIVE_GRAB_S = 0.012  # a grab that waited this long got a new sensor frame (queued frames return at once)
LIVE_GRABS_MIN = 2  # queue backends: this many waiting grabs after the request was seen
LATEST_WAIT_S = 0.25  # latest-frame backends: final frame >= this after seen (~7 intervals at 30 fps) ...
MIN_AFTER_SEEN_S = 0.1
MAX_FLUSH_S = 3.0  # give up this long after the flush started, whatever ``seen`` says
MAX_FLUSH_GRABS = 300  # and never more grabs than this (a camera that never blocks can't spin forever)
PACE_S = 0.005  # after a grab that did not wait (300 such grabs span >= 1.5 s)
FRAME_TAG = b"ring3-frame:v3;"
LATEST_WAIT_NS = int(LATEST_WAIT_S * 1e9)  # = the bridge's LATEST_WAIT_NS
MAX_PROOF_NS = 10 * 10**9  # = the bridge's MAX_FLUSH_NS
PROOF_MARGIN_NS = 10_000_000  # the helper writes only when the bridge's proof holds with 10 ms to spare
POLL_S = 0.05
JPEG_QUALITY = 92


def add_frame_comment(jpeg: bytes, nonce: str, seen_ns: int, cap_ns: int, live: int, changed: int) -> bytes:
    """COM segment right after SOI (the bridge's ``capture.add_frame_comment`` writes the same bytes)."""
    if not jpeg.startswith(b"\xff\xd8") or not NONCE_RE.fullmatch(nonce):
        raise ValueError("bad JPEG or nonce")
    body = f"id={nonce};seen_ns={seen_ns};cap_ns={cap_ns};live={live};changed={changed}"
    payload = FRAME_TAG + body.encode("ascii")
    return jpeg[:2] + b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


def proof_ok(live: int, changed: int, seen_ns: int, cap_ns: int, margin_ns: int = 0) -> bool:
    """The bridge's freshness check (``capture.proof_ok``), same arithmetic on the same recorded fields; the
    helper calls it with ``PROOF_MARGIN_NS`` before writing, so what it writes always passes the bridge."""
    if cap_ns <= seen_ns or cap_ns - seen_ns > MAX_PROOF_NS:
        return False
    return live >= LIVE_GRABS_MIN or (changed == 1 and cap_ns - seen_ns >= LATEST_WAIT_NS + margin_ns)


def frames_differ(a, b) -> bool:  # noqa: ANN001 - numpy arrays
    """A live stream never repeats a frame exactly (sensor noise); a frozen one does."""
    return a is not None and b is not None and a.shape == b.shape and bool((a[::4, ::4] != b[::4, ::4]).any())


class Grab:
    """One capture attempt: ``jpeg`` None means nothing is written (``reason`` says why)."""

    def __init__(self) -> None:
        self.jpeg: bytes | None = None
        self.reason = ""
        self.cap_ns = 0
        self.grabs = 0
        self.live = 0  # grabs that waited >= LIVE_GRAB_S after the request was seen
        self.changed = 0  # latest-frame proof: final frame differs from one retrieved after seen
        self.ms: list[float] = []

    def summary(self) -> str:
        rng = f"{min(self.ms):.1f}-{max(self.ms):.1f} ms" if self.ms else "-"
        return f"grabs {self.grabs}, new {self.live}, grab time {rng}, changed {self.changed}"


def grab_fresh(  # noqa: ANN001
    cam, encode, seen: float, clock=time.monotonic, wall_ns=time.time_ns, sleep=time.sleep, seen_ns=None
) -> Grab:
    """A frame captured after ``seen`` (a ``clock()`` time), never a queued one. Two proofs, one per backend
    kind: queue backends (grab waits for the sensor once the queue is drained) -> >= LIVE_GRABS_MIN grabs that
    waited; latest-frame backends (grab never waits, returns the newest frame) -> >= LATEST_WAIT_S after seen
    and the frame differs from one retrieved after seen (a frozen stream repeats it). The decision to write
    is ``proof_ok`` on the recorded ``seen_ns`` / ``cap_ns`` with ``PROOF_MARGIN_NS``: exactly what the bridge
    checks (the monotonic clock only paces the loop). Else nothing."""
    g = Grab()
    seen_ns = wall_ns() if seen_ns is None else seen_ns
    start = clock()
    ref = None
    while True:
        t0 = clock()
        if not cam.grab():
            g.reason = "camera grab failed"
            return g
        t1 = clock()
        g.grabs += 1
        g.ms.append((t1 - t0) * 1000)
        waited = t1 - t0 >= LIVE_GRAB_S
        if waited and t0 >= seen:
            g.live += 1
        if ref is None and t0 >= seen:
            ok, frame = cam.retrieve()
            ref = frame if ok else None
        after = t1 - seen
        if g.grabs >= FLUSH_GRABS and after >= MIN_AFTER_SEEN_S:
            if g.live >= LIVE_GRABS_MIN:
                ok, frame = cam.retrieve()
                cap_ns = wall_ns()
                if ok and frame is not None and proof_ok(g.live, 0, seen_ns, cap_ns, PROOF_MARGIN_NS):
                    g.cap_ns, g.jpeg = cap_ns, encode(frame)
                    return g
            elif after >= LATEST_WAIT_S:
                ok, frame = cam.retrieve()
                cap_ns = wall_ns()
                changed = int(ok and frames_differ(ref, frame))
                if changed and proof_ok(g.live, changed, seen_ns, cap_ns, PROOF_MARGIN_NS):
                    g.changed, g.cap_ns, g.jpeg = 1, cap_ns, encode(frame)
                    return g
        if not waited:
            sleep(PACE_S)  # a grab that did not wait: pace the loop so the grab limit spans real time
        if t1 - start > MAX_FLUSH_S or g.grabs >= MAX_FLUSH_GRABS:
            g.reason = (
                f"no fresh frame in {t1 - start:.1f} s ({g.live} waiting grab(s), frames "
                f"{'unchanged: frozen stream?' if ref is not None else 'not retrievable'})"
            )
            return g


def pending_requests(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.req") if NONCE_RE.fullmatch(p.stem))


def serve_once(folder: Path, cam, encode, info: str = "", log=print,  # noqa: ANN001
               clock=time.monotonic, wall_ns=time.time_ns) -> int:  # fmt: skip
    """Answer every pending request once, one log line each. Returns the number of frames written. No clock
    comparison with the request file (its time comes from the WSL side): freshness is proven on this clock."""
    written = 0
    for req in pending_requests(folder):
        nonce = req.stem
        try:
            body = req.read_bytes()[:64]
        except FileNotFoundError:
            continue
        if body.strip() != nonce.encode("ascii"):
            req.unlink(missing_ok=True)
            log(f"request {nonce[:8]}: {info}wrote nothing: request body is not its id")
            continue
        seen, seen_ns = clock(), wall_ns()
        g = grab_fresh(cam, encode, seen, clock, wall_ns, seen_ns=seen_ns)
        if g.jpeg is not None:
            tmp, out = folder / f"{nonce}.tmp.jpg", folder / f"{nonce}.jpg"
            tmp.write_bytes(add_frame_comment(g.jpeg, nonce, seen_ns, g.cap_ns, g.live, g.changed))
            os.replace(tmp, out)
            written += 1
            result = f"wrote frame ({(clock() - seen) * 1000:.0f} ms)"
        else:
            result = f"wrote nothing: {g.reason}"
        req.unlink(missing_ok=True)
        log(f"request {nonce[:8]}: {info}{g.summary()} -> {result}")
    return written


def drop_leftovers(folder: Path, log=print) -> int:  # noqa: ANN001
    """Requests already there when the helper starts belong to no waiting bridge: delete them."""
    n = 0
    for req in pending_requests(folder):
        req.unlink(missing_ok=True)
        n += 1
    if n:
        log(f"deleted {n} request(s) left from before the helper started")
    return n


def open_camera(cv2, index: int, backend: str, width: int, height: int, log=print):  # noqa: ANN001
    """-> (camera or None, info prefix for every log line)."""
    api = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}[backend]
    t0 = time.monotonic()
    cam = cv2.VideoCapture(index, api)
    if not cam.isOpened():
        log(f"camera {index} did not open with backend {backend}")
        return None, ""
    buf_ok = bool(cam.set(cv2.CAP_PROP_BUFFERSIZE, 1))
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    try:
        name = cam.getBackendName()
    except Exception:  # noqa: BLE001
        name = backend
    opened = time.strftime("%H:%M:%S")
    w, h = int(cam.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log(f"camera {index} opened at {opened} in {(time.monotonic() - t0) * 1000:.0f} ms: backend {name}, "
        f"{w}x{h}, buffersize=1 {'accepted' if buf_ok else 'not supported'}")  # fmt: skip
    return cam, f"camera opened {opened}, {name}, buffersize {'1' if buf_ok else 'default'}, "


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RING3 witness capture helper (Windows)")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--backend", choices=("dshow", "msmf", "any"), default="dshow")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--once", action="store_true", help="answer pending requests once and exit")
    ap.add_argument("--check", action="store_true", help="open the camera, grab one frame, print its size")
    ap.add_argument("--selftest", action="store_true", help="open the camera, 3 captures, no folder needed")
    a = ap.parse_args(argv)
    import cv2  # Windows-side OpenCV; imported here so the protocol functions load without it

    folder = Path(os.environ.get(ENV_DIR, ""))
    if not (a.check or a.selftest) and (not os.environ.get(ENV_DIR) or not folder.is_dir()):
        print(f"set {ENV_DIR} to an existing folder", file=sys.stderr)
        return 2
    cam, info = open_camera(cv2, a.camera, a.backend, a.width, a.height)
    if cam is None:
        return 1

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
        if a.selftest:
            return selftest(cam, encode, info)
        drop_leftovers(folder)
        print(f"serving capture requests in {ENV_DIR} (Ctrl+C stops); one line per request")
        while True:
            serve_once(folder, cam, encode, info)
            if a.once:
                return 0
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 0
    finally:
        cam.release()


def selftest(
    cam, encode, info: str, n: int = 3, log=print, clock=time.monotonic, wall_ns=time.time_ns
) -> int:  # noqa: ANN001
    """``--selftest``: n captures as if a request had just arrived; the same line per capture as serving."""
    good = 0
    for i in range(n):
        seen = clock()
        g = grab_fresh(cam, encode, seen, clock, wall_ns)
        res = f"captured {len(g.jpeg)} B JPEG" if g.jpeg is not None else f"nothing: {g.reason}"
        proof = "waiting grabs" if g.live >= LIVE_GRABS_MIN else ("frame changed" if g.changed else "none")
        log(f"selftest {i + 1}/{n}: {info}{g.summary()} -> {res} (proof: {proof})")
        good += g.jpeg is not None
        time.sleep(0.5)
    log(f"selftest: {good}/{n} fresh captures")
    return 0 if good == n else 1


if __name__ == "__main__":
    sys.exit(main())
