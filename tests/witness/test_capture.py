"""Fresh-frame protocol (D-58): bridge side and the Windows helper's logic, with a fake camera."""

import os
import threading
import time

import cv2
import numpy as np
import pytest
import witness_capture_win as helper

from ring3bridge.witness import STALE_FRAME, CantVerify
from ring3bridge.witness.capture import (
    add_frame_comment,
    add_nonce_comment,
    read_frame_comment,
    request_frame,
)

NONCE = "0123456789abcdef0123456789abcdef"


def jpeg(value=128):
    ok, b = cv2.imencode(".jpg", np.full((48, 64, 3), value, np.uint8))
    assert ok
    return b.tobytes()


class FakeCam:
    """Like an OpenCV backend: ``buffered`` queued frames return at once, then every grab waits ``live_s`` for
    the next sensor frame (``live_s=0``: a camera that only ever hands out buffered frames)."""

    def __init__(self, fail=False, buffered=4, live_s=0.015):
        self.grabs = 0
        self.retrieves = 0
        self.fail = fail
        self.buffered = buffered
        self.live_s = live_s

    def grab(self):
        self.grabs += 1
        if self.grabs > self.buffered and self.live_s:
            time.sleep(self.live_s)
        return not self.fail

    def retrieve(self):
        self.retrieves += 1
        return True, np.full((48, 64, 3), 200, np.uint8)


def encode(frame):
    return cv2.imencode(".jpg", frame)[1].tobytes()


def test_frame_comment_round_trip_and_helper_copy_agrees():
    j = jpeg()
    args = (NONCE, 1_793_000_000_000_000_000, 1_793_000_000_150_000_000, 3)
    assert add_frame_comment(j, *args) == helper.add_frame_comment(j, *args)
    assert read_frame_comment(add_frame_comment(j, *args)) == args
    assert read_frame_comment(j) is None and read_frame_comment(add_nonce_comment(j, NONCE)) is None
    assert cv2.imdecode(np.frombuffer(add_frame_comment(j, *args), np.uint8), cv2.IMREAD_COLOR) is not None


def _serve_in_background(folder, cam, stop):
    def loop():
        while not stop.is_set():
            helper.serve_once(folder, cam, encode)
            time.sleep(0.01)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th


def test_fresh_frame_round_trip(tmp_path):
    cam, stop = FakeCam(), threading.Event()
    th = _serve_in_background(tmp_path, cam, stop)
    try:
        nonce, img, data = request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    finally:
        stop.set()
        th.join()
    fid, seen_ns, cap_ns, live = read_frame_comment(data)
    assert img.shape == (48, 64, 3) and fid == nonce and cap_ns > seen_ns and live >= 2
    assert cam.grabs >= 6 and cam.grabs > cam.buffered + 1 and cam.retrieves == 1  # buffered ones discarded
    assert list(tmp_path.iterdir()) == []  # request and frame cleaned up


def test_no_helper_is_stale(tmp_path):
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=0.2, poll_s=0.01)
    assert e.value.reason == STALE_FRAME


def test_missing_folder_is_stale(tmp_path):
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path / "nope", timeout_s=0.1)
    assert e.value.reason == STALE_FRAME


def _answer(tmp_path, data, mtime=None):
    """A misbehaving 'helper' that answers the next request with ``data``."""

    def run():
        for _ in range(300):
            reqs = list(tmp_path.glob("*.req"))
            if reqs:
                out = tmp_path / (reqs[0].stem + ".jpg")
                tmp = tmp_path / "x.tmp"
                tmp.write_bytes(data(reqs[0].stem))
                if mtime is not None:
                    os.utime(tmp, (mtime, mtime))
                os.replace(tmp, out)
                return
            time.sleep(0.01)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def _v2(nonce, seen_s=0.0, cap_s=0.15, live=3, fid=None):
    """A v2 frame for ``nonce``: helper saw the request ``seen_s`` and captured ``cap_s`` seconds from now."""
    now = time.time_ns()
    return add_frame_comment(jpeg(), fid or nonce, now + int(seen_s * 1e9), now + int(cap_s * 1e9), live)


STALE_CASES = {
    "mismatched request id": (lambda n: _v2(n, fid="f" * 32), "does not match"),
    "captured before the request (buffered frame)": (lambda n: _v2(n, -601.0, -600.0), "before the request"),
    "captured before the helper saw the request": (lambda n: _v2(n, 0.0, -0.05), "before the helper saw"),
    "no live grab": (lambda n: _v2(n, live=0), "live grab"),
    "old helper, nonce only (restart it)": (lambda n: add_nonce_comment(jpeg(), n), "restart it"),
    "no comment": (lambda n: jpeg(), "no frame proof"),
    "undecodable": (lambda n: add_frame_comment(b"\xff\xd8garbage", n, 1, 2, 3), None),
}


@pytest.mark.parametrize("case", list(STALE_CASES))
def test_stale_or_unproven_frames_are_cant_verify(tmp_path, case):
    """D-80: a frame is evidence only if it proves it was captured for this request, after it."""
    make, why = STALE_CASES[case]
    th = _answer(tmp_path, make)
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert e.value.reason == STALE_FRAME, case
    assert why is None or why in e.value.detail, e.value.detail


def test_fresh_v2_frame_is_accepted(tmp_path):
    th = _answer(tmp_path, lambda n: _v2(n))
    nonce, img, data = request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert read_frame_comment(data)[0] == nonce and img.shape == (48, 64, 3)


def test_old_file_is_stale(tmp_path):
    th = _answer(tmp_path, lambda n: _v2(n), mtime=time.time() - 600)
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert e.value.reason == STALE_FRAME and "older than the request" in e.value.detail


def test_helper_writes_nothing_when_only_buffered_frames_come(tmp_path, monkeypatch):
    """A camera whose grabs never wait for the sensor (all frames queued): no live frame, no file."""
    monkeypatch.setattr(helper, "MAX_FLUSH_S", 0.3)
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    cam = FakeCam(buffered=10**6, live_s=0)
    assert helper.serve_once(tmp_path, cam, encode) == 0
    assert not (tmp_path / f"{NONCE}.jpg").exists() and cam.retrieves == 0


def test_helper_counts_live_grabs_only_after_the_request_was_seen():
    t = [100.0]

    def clock():
        return t[0]

    class Cam:
        grabs = 0

        def grab(self):
            Cam.grabs += 1
            t[0] += 0.04  # every grab blocks 40 ms (live)
            return True

        def retrieve(self):
            return True, np.full((8, 8, 3), 9, np.uint8)

    got = helper.grab_fresh_jpeg(Cam(), encode, seen=100.0, clock=clock, wall_ns=lambda: 5)
    assert got is not None and got[2] >= 2 and Cam.grabs >= 6 and t[0] - 100.0 >= 0.1
    t[0], Cam.grabs = 100.0, 0  # request "seen" in the future: no grab counts as live, and the loop ends
    assert helper.grab_fresh_jpeg(Cam(), encode, seen=1e9, clock=clock, wall_ns=lambda: 5) is None
    assert Cam.grabs <= helper.MAX_FLUSH_GRABS


def test_helper_ignores_old_or_malformed_requests(tmp_path):
    cam = FakeCam()
    old = tmp_path / f"{NONCE}.req"
    old.write_text(NONCE)
    os.utime(old, (time.time() - 60, time.time() - 60))
    bad_body = tmp_path / f"{'a' * 32}.req"
    bad_body.write_text("b" * 32)
    (tmp_path / "hello.req").write_text("hello")
    assert helper.serve_once(tmp_path, cam, encode) == 0
    assert cam.grabs == 0
    assert not old.exists() and not bad_body.exists()
    assert (tmp_path / "hello.req").exists()  # not a nonce name: never touched


def test_helper_camera_failure_writes_nothing(tmp_path):
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    assert helper.serve_once(tmp_path, FakeCam(fail=True), encode) == 0
    assert not (tmp_path / f"{NONCE}.jpg").exists()


def test_helper_writes_atomically(tmp_path, monkeypatch):
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(
            (os.path.basename(src), os.path.basename(dst), (tmp_path / os.path.basename(dst)).exists())
        )
        real_replace(src, dst)

    monkeypatch.setattr(helper.os, "replace", spy)
    assert helper.serve_once(tmp_path, FakeCam(), encode) == 1
    assert seen == [(f"{NONCE}.tmp.jpg", f"{NONCE}.jpg", False)]
    assert read_frame_comment((tmp_path / f"{NONCE}.jpg").read_bytes())[0] == NONCE
