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
    the next sensor frame (``live_s=0``: grabs never wait, like a latest-frame backend). Frames carry sensor
    noise; ``frozen=True`` returns the same frame forever (a stalled stream)."""

    def __init__(self, fail=False, buffered=4, live_s=0.015, frozen=False):
        self.grabs = 0
        self.retrieves = 0
        self.fail = fail
        self.buffered = buffered
        self.live_s = live_s
        self.frozen = frozen
        self.rng = np.random.default_rng(1)

    def grab(self):
        self.grabs += 1
        if self.grabs > self.buffered and self.live_s:
            time.sleep(self.live_s)
        return not self.fail

    def retrieve(self):
        self.retrieves += 1
        if self.frozen:
            return True, np.full((48, 64, 3), 200, np.uint8)
        return True, (200 + self.rng.integers(-3, 4, (48, 64, 3))).astype(np.uint8)


def encode(frame):
    return cv2.imencode(".jpg", frame)[1].tobytes()


def test_frame_comment_round_trip_and_helper_copy_agrees():
    j = jpeg()
    args = (NONCE, 1_793_000_000_000_000_000, 1_793_000_000_150_000_000, 3, 0)
    assert add_frame_comment(j, *args) == helper.add_frame_comment(j, *args)
    assert read_frame_comment(add_frame_comment(j, *args)) == args
    assert read_frame_comment(j) is None and read_frame_comment(add_nonce_comment(j, NONCE)) is None
    assert cv2.imdecode(np.frombuffer(add_frame_comment(j, *args), np.uint8), cv2.IMREAD_COLOR) is not None


def _serve_in_background(folder, cam, stop):
    def loop():
        while not stop.is_set():
            helper.serve_once(folder, cam, encode, log=lambda _l: None)
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
    fid, seen_ns, cap_ns, live, changed = read_frame_comment(data)
    assert img.shape == (48, 64, 3) and fid == nonce and cap_ns > seen_ns and live >= 2
    assert cam.grabs >= 6 and cam.grabs > cam.buffered + 1  # the queued frames were discarded
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


def _v3(nonce, seen_s=0.0, cap_s=0.15, live=3, fid=None, changed=0):
    """A v3 frame for ``nonce``: helper saw the request ``seen_s`` and captured ``cap_s`` seconds from now."""
    now = time.time_ns()
    seen, cap = now + int(seen_s * 1e9), now + int(cap_s * 1e9)
    return add_frame_comment(jpeg(), fid or nonce, seen, cap, live, changed)


STALE_CASES = {
    "mismatched request id": (lambda n: _v3(n, fid="f" * 32), "does not match"),
    "captured long before the request (old frame)": (lambda n: _v3(n, -601.0, -600.0), "before the request"),
    "captured before the helper saw the request": (lambda n: _v3(n, 0.0, -0.05), "before the helper saw"),
    "queued frame: no waiting grab, frame unchanged": (
        lambda n: _v3(n, live=1, cap_s=0.3),
        "no freshness proof",
    ),
    "latest-frame proof too early (< 250 ms)": (
        lambda n: _v3(n, live=0, cap_s=0.1, changed=1),
        "no freshness",
    ),
    "old helper, nonce only (restart it)": (lambda n: add_nonce_comment(jpeg(), n), "restart it"),
    "no comment": (lambda n: jpeg(), "no frame proof"),
    "undecodable": (lambda n: add_frame_comment(b"\xff\xd8garbage", n, 1, 2, 3, 0), None),
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


@pytest.mark.parametrize("proof", ["waiting grabs", "latest frame changed"])
def test_fresh_v3_frame_is_accepted(tmp_path, proof):
    make = (
        (lambda n: _v3(n)) if proof == "waiting grabs" else (lambda n: _v3(n, live=0, cap_s=0.3, changed=1))
    )
    th = _answer(tmp_path, make)
    nonce, img, data = request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert read_frame_comment(data)[0] == nonce and img.shape == (48, 64, 3)


def test_old_file_is_stale(tmp_path):
    th = _answer(tmp_path, lambda n: _v3(n), mtime=time.time() - 600)
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=3.0, poll_s=0.01)
    th.join()
    assert e.value.reason == STALE_FRAME and "older than the request" in e.value.detail


def test_helper_never_waiting_camera_frozen_stream_writes_nothing(tmp_path, monkeypatch):
    """Grabs never wait and the frame never changes (a stalled stream): no proof, no file, one log line."""
    monkeypatch.setattr(helper, "MAX_FLUSH_S", 0.4)
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    lines = []
    cam = FakeCam(buffered=10**6, live_s=0, frozen=True)
    assert helper.serve_once(tmp_path, cam, encode, log=lines.append) == 0
    assert not (tmp_path / f"{NONCE}.jpg").exists()
    assert len(lines) == 1 and "wrote nothing: no fresh frame" in lines[0] and "frozen stream" in lines[0]


def test_helper_never_waiting_camera_live_stream_uses_the_latest_frame_proof(tmp_path):
    """Grabs never wait (latest-frame backend) but the stream is live: a frame >= 250 ms after seen that
    differs from one retrieved after seen is written, with changed=1; the bridge accepts it."""
    cam, stop = FakeCam(buffered=10**6, live_s=0), threading.Event()
    th = _serve_in_background(tmp_path, cam, stop)
    try:
        nonce, _, data = request_frame(tmp_path, poll_s=0.01)
    finally:
        stop.set()
        th.join()
    fid, seen_ns, cap_ns, live, changed = read_frame_comment(data)
    assert fid == nonce and live == 0 and changed == 1 and cap_ns - seen_ns >= 250_000_000


def _fake_clock(step):
    t = [100.0]

    def clock():
        return t[0]

    class Cam:
        grabs = 0

        def grab(self):
            Cam.grabs += 1
            t[0] += step
            return True

        def retrieve(self):
            return True, np.full((8, 8, 3), Cam.grabs % 251, np.uint8)

    return t, clock, Cam


def test_helper_counts_live_grabs_only_after_the_request_was_seen():
    t, clock, Cam = _fake_clock(0.04)  # every grab waits 40 ms (a queue backend at 25 fps)
    g = helper.grab_fresh(Cam(), encode, seen=100.0, clock=clock, wall_ns=lambda: 5)
    assert g.jpeg is not None and g.live >= 2 and Cam.grabs >= 6 and t[0] - 100.0 >= 0.1
    t[0], Cam.grabs = 100.0, 0  # request "seen" in the future: no grab counts, and the loop still ends
    g = helper.grab_fresh(Cam(), encode, seen=1e9, clock=clock, wall_ns=lambda: 5, sleep=lambda _s: None)
    assert g.jpeg is None and "no fresh frame" in g.reason and Cam.grabs <= helper.MAX_FLUSH_GRABS
    assert t[0] - 100.0 <= helper.MAX_FLUSH_S + 0.05


def test_helper_gives_up_within_its_limits():
    t, clock, Cam = _fake_clock(0.0001)  # never waits, never seen: bounded by MAX_FLUSH_GRABS
    slept = []

    def sleep(dt):  # paced: each non-waiting grab sleeps PACE_S on the fake clock
        slept.append(dt)
        t[0] += dt

    g = helper.grab_fresh(Cam(), encode, seen=1e9, clock=clock, sleep=sleep)
    assert g.jpeg is None and g.grabs == helper.MAX_FLUSH_GRABS
    assert len(slept) == g.grabs and helper.MAX_FLUSH_GRABS * helper.PACE_S >= 1.5  # the limit spans >= 1.5 s


def test_bridge_waits_longer_than_the_helper_can_take():
    """The bridge's request timeout covers the helper's worst case (flush limit + poll + write) + margin."""
    from ring3bridge.witness import capture

    assert capture.REQUEST_TIMEOUT_S >= helper.MAX_FLUSH_S + helper.POLL_S + 1.0 + 1.0
    assert capture.LIVE_GRABS_MIN == helper.LIVE_GRABS_MIN
    assert capture.LATEST_WAIT_NS == int(helper.LATEST_WAIT_S * 1e9)
    assert capture.FRAME_TAG == helper.FRAME_TAG


def test_helper_logs_one_line_per_request(tmp_path):
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    lines = []
    assert (
        helper.serve_once(
            tmp_path, FakeCam(), encode, info="camera opened 21:40:00, DSHOW, ", log=lines.append
        )
        == 1
    )
    (line,) = lines
    assert line.startswith(f"request {NONCE[:8]}: camera opened 21:40:00, DSHOW, grabs ")
    assert ", new " in line and "grab time " in line and " ms" in line and "-> wrote frame (" in line


def test_helper_serves_a_request_whatever_its_file_time_says(tmp_path):
    """The request file's time comes from the WSL clock, which can drift from Windows': not compared."""
    req = tmp_path / f"{NONCE}.req"
    req.write_text(NONCE)
    os.utime(req, (time.time() - 3600, time.time() - 3600))
    assert helper.serve_once(tmp_path, FakeCam(), encode, log=lambda _l: None) == 1


def test_helper_deletes_leftover_requests_at_start(tmp_path):
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    lines = []
    assert helper.drop_leftovers(tmp_path, log=lines.append) == 1
    assert not (tmp_path / f"{NONCE}.req").exists() and "deleted 1 request" in lines[0]


def test_selftest_prints_three_lines_and_a_total():
    lines = []
    rc = helper.selftest(FakeCam(), encode, "camera opened 21:40:00, MSMF, ", log=lines.append)
    assert rc == 0 and len(lines) == 4 and lines[-1] == "selftest: 3/3 fresh captures"
    assert all(
        x.startswith(f"selftest {i}/3: camera opened") and "-> captured" in x
        for i, x in enumerate(lines[:3], 1)
    )
    lines.clear()
    rc = helper.selftest(FakeCam(fail=True), encode, "", log=lines.append)
    assert (
        rc == 1 and "nothing: camera grab failed" in lines[0] and lines[-1] == "selftest: 0/3 fresh captures"
    )


def test_helper_ignores_malformed_requests(tmp_path):
    cam = FakeCam()
    bad_body = tmp_path / f"{'a' * 32}.req"
    bad_body.write_text("b" * 32)
    (tmp_path / "hello.req").write_text("hello")
    lines = []
    assert helper.serve_once(tmp_path, cam, encode, log=lines.append) == 0
    assert cam.grabs == 0 and not bad_body.exists()
    assert (tmp_path / "hello.req").exists()  # not a nonce name: never touched
    assert lines == [f"request {'a' * 8}: wrote nothing: request body is not its id"]


def test_helper_camera_failure_writes_nothing(tmp_path):
    (tmp_path / f"{NONCE}.req").write_text(NONCE)
    assert helper.serve_once(tmp_path, FakeCam(fail=True), encode, log=lambda _l: None) == 0
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
    assert helper.serve_once(tmp_path, FakeCam(), encode, log=lambda _l: None) == 1
    assert seen == [(f"{NONCE}.tmp.jpg", f"{NONCE}.jpg", False)]
    assert read_frame_comment((tmp_path / f"{NONCE}.jpg").read_bytes())[0] == NONCE
