"""Fresh-frame protocol (D-58): bridge side and the Windows helper's logic, with a fake camera."""

import os
import threading
import time

import cv2
import numpy as np
import pytest
import witness_capture_win as helper

from ring3bridge.witness import STALE_FRAME, CantVerify
from ring3bridge.witness.capture import add_nonce_comment, read_nonce_comment, request_frame

NONCE = "0123456789abcdef0123456789abcdef"


def jpeg(value=128):
    ok, b = cv2.imencode(".jpg", np.full((48, 64, 3), value, np.uint8))
    assert ok
    return b.tobytes()


class FakeCam:
    def __init__(self, fail=False):
        self.grabs = 0
        self.retrieves = 0
        self.fail = fail

    def grab(self):
        self.grabs += 1
        return not self.fail

    def retrieve(self):
        self.retrieves += 1
        return True, np.full((48, 64, 3), 200, np.uint8)


def encode(frame):
    return cv2.imencode(".jpg", frame)[1].tobytes()


def test_nonce_comment_round_trip_and_helper_copy_agrees():
    j = jpeg()
    assert add_nonce_comment(j, NONCE) == helper.add_nonce_comment(j, NONCE)
    assert read_nonce_comment(add_nonce_comment(j, NONCE)) == NONCE
    assert read_nonce_comment(j) is None
    assert cv2.imdecode(np.frombuffer(add_nonce_comment(j, NONCE), np.uint8), cv2.IMREAD_COLOR) is not None


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
    assert img.shape == (48, 64, 3) and read_nonce_comment(data) == nonce
    assert cam.grabs >= 5 and cam.retrieves == 1  # buffered frames discarded before decoding
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


@pytest.mark.parametrize(
    "case",
    ["other nonce", "no nonce", "old file", "undecodable"],
)
def test_bad_frames_are_stale(tmp_path, case):
    other = "f" * 32
    data, mtime = {
        "other nonce": (lambda n: add_nonce_comment(jpeg(), other), None),
        "no nonce": (lambda n: jpeg(), None),
        "old file": (lambda n: add_nonce_comment(jpeg(), n), time.time() - 600),
        "undecodable": (lambda n: add_nonce_comment(b"\xff\xd8garbage", n), None),
    }[case]
    th = _answer(tmp_path, data, mtime)
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert e.value.reason == STALE_FRAME, case


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
    assert read_nonce_comment((tmp_path / f"{NONCE}.jpg").read_bytes()) == NONCE
