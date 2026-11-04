"""T-07 Part B, offline: the calibration plan and script, the calibration file, the witness service and the
witness observation text, the CLI dry run. No QEMU, no network, no key, no camera (synthetic frames)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from ring3bridge import abi, cli, eventlog
from ring3bridge.calibration import (
    CAL_POINTS,
    CalibrationError,
    CalibrationScript,
    calibration_doc,
    load_calibration,
    plan,
    write_calibration,
)
from ring3bridge.run import witness_output
from ring3bridge.strictjson import Call, Done, validate
from ring3bridge.witness import CALIBRATION, CANT_VERIFY, NO_MOVE, PASS, STALE_FRAME, CantVerify, calibrate
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.service import Witness, no_move
from tests.witness import synth
from tests.witness.fakes import make_client, ok
from tests.witness.synth import OFFSET_DEG, Scene

GEOM = load_geometry()
RECORDED_FRAME = Path(__file__).parent / "recorded" / "frame-7855cbe2624a-side-mount-90.jpg"


# ---- plan ----


@pytest.mark.parametrize("start", range(30, 151))
def test_plan_steps_range_cap_and_one_approach_side(start):
    """D-58: every move <= 30 deg from the previous one (the first from the kernel's current angle), inside
    30-150, never a zero-length move; every point measured once, all approached from the same side; fewer
    moves than the 8 ACTIONs after which agentsh ends a request (so the last point's witness still runs)."""
    moves = plan(start)
    assert len(moves) < abi.ACTIONS_PER_REQUEST
    prev, sides = start, set()
    for deg, measured in moves:
        assert 30 <= deg <= 150 and 0 < abs(deg - prev) <= 30, (start, moves)
        if measured:
            sides.add(deg > prev)
        prev = deg
    assert len(sides) == 1, moves
    pts = [d for d, m in moves if m]
    assert pts in (sorted(CAL_POINTS), sorted(CAL_POINTS, reverse=True))


def test_default_points_are_d72_and_span_enough():
    """D-72 (replaces D-59's "avoid 80-100"): 60/70/90/110/120, two-sided with 90 as the anchor; the span
    satisfies the fit's minimum (steps and the cap: test_plan_steps_range_cap_and_one_approach_side)."""
    assert CAL_POINTS == (60, 70, 90, 110, 120)
    assert (
        len(CAL_POINTS) >= calibrate.MIN_POINTS
        and max(CAL_POINTS) - min(CAL_POINTS) >= calibrate.MIN_SPAN_DEG
    )


def test_synthetic_calibration_on_the_part_b_points_is_under_the_limit(tmp_path):
    """The D-72 points calibrate on synthetic frames of the real 15 mm horn with the hand-aligned paper (2 deg
    residual offset), under the unchanged 3.0 deg limit."""
    pairs = []
    for i, c in enumerate(CAL_POINTS):
        frame, _, labels = synth.render(Scene([c + OFFSET_DEG], seed=200 + i))
        d = tmp_path / f"p{i}"
        d.mkdir()
        client, t, _ = make_client(d, ok(synth.envelope(synth.line_for(frame, labels, GEOM))))
        w = Witness(lambda f=frame: (f, b"\xff\xd8"), client, calibrate.NONE, GEOM)
        r = w.measure(c).result
        assert (r.verdict, r.reason, r.detail) == (CANT_VERIFY, CALIBRATION, "calibration point")
        assert r.dial_mdeg is not None and len(t.posts) == 1
        pairs.append((c, r.dial_mdeg))
    cal = calibrate.fit(pairs)
    assert cal.ok, cal.message()
    assert cal.residual_mdeg <= calibrate.RESIDUAL_LIMIT_MDEG


# ---- the scripted model ----


class _Req:
    def __init__(self) -> None:
        self.results: list[tuple[str, int]] = []


class _Runner:
    def __init__(self, tele_servo: int | None) -> None:
        self.request = _Req()
        self.tele_servo = tele_servo

    def fresh_servo(self) -> int | None:
        return self.tele_servo


class _FakeWitness:
    """Measures dial = command + 2 deg; records what it was asked."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def measure(self, deg: int):
        from ring3bridge.witness.pipeline import WitnessResult
        from ring3bridge.witness.service import Checked

        self.asked.append(deg)
        r = WitnessResult(CANT_VERIFY, CALIBRATION, "calibration point", deg, "", "", deg * 1000 + 2000, None,
                          "", "", 2500)  # fmt: skip
        return Checked(r, b"")


def _drive(script: CalibrationScript, start: int, deny_at: int | None = None, no_move_at: int | None = None):
    """Plays the run loop's part: answers each scripted call like the kernel and the bridge would."""
    script.runner = _Runner(start)
    req = script.runner.request
    calls = []
    for _ in range(40):
        v = validate(script.ask("", "").text)
        if isinstance(v, Done):
            return calls, v.summary
        assert isinstance(v, Call)
        calls.append((v.op, dict(v.args)))
        if v.op == "servo_set":
            denied = deny_at is not None and v.args["deg"] == deny_at
            req.results.append(("servo_set", -abi.ERRNO["ESAFETY"] if denied else 0))
        elif v.op == "witness":
            last = next(a["deg"] for op, a in reversed(calls) if op == "servo_set")
            if last != no_move_at:
                script.measure(last)
            req.results.append(("witness", 0))
    raise AssertionError("the script did not end")


def test_script_reads_status_moves_measures_and_finishes():
    fw = _FakeWitness()
    s = CalibrationScript(fw, move_gap_s=0)  # type: ignore[arg-type]
    calls, summary = _drive(s, 90)
    assert calls[0] == ("servo_set", {"deg": plan(90)[0][0]})
    servo = [a["deg"] for op, a in calls if op == "servo_set"]
    assert servo == [d for d, _ in plan(90)]
    # a witness op right after every measured move, and only there
    measured = iter([m for _, m in plan(90)])
    for i, (op, _a) in enumerate(calls):
        if op == "servo_set":
            assert (calls[i + 1][0] == "witness") == next(measured)
    assert fw.asked == sorted(CAL_POINTS)
    assert s.pairs == [(c, c * 1000 + 2000) for c in sorted(CAL_POINTS)]
    assert calibrate.fit(s.pairs).ok
    assert summary == "calibration moves done: 5 of 5 points measured" and s.note == ""
    assert s.start_deg == 90
    assert len(calls) <= s.max_actions()


def test_script_stops_on_a_kernel_refusal_and_never_retries():
    fw = _FakeWitness()
    s = CalibrationScript(fw, move_gap_s=0)  # type: ignore[arg-type]
    calls, summary = _drive(s, 90, deny_at=70)
    servo = [a["deg"] for op, a in calls if op == "servo_set"]
    assert servo[-1] == 70 and servo.count(70) == 1
    assert "servo_set 70 refused by the kernel" in summary and s.note
    assert not calibrate.fit(s.pairs).ok  # 2 points: disabled, never guessed


def test_script_records_a_missing_point_when_the_bridge_answered_no_move():
    s = CalibrationScript(_FakeWitness(), move_gap_s=0)  # type: ignore[arg-type]
    _, summary = _drive(s, 30, no_move_at=60)
    assert (60, None) in s.pairs and summary == "calibration moves done: 4 of 5 points measured"
    cal = calibrate.fit(s.pairs)
    assert not cal.ok and "no measurement at commanded [60]" in cal.reason


def test_script_does_not_start_without_telemetry():
    s = CalibrationScript(_FakeWitness(), move_gap_s=0)  # type: ignore[arg-type]
    s.runner = _Runner(None)
    v = validate(s.ask("", "").text)
    assert isinstance(v, Done) and v.summary == "calibration not started: no telemetry from the cell yet"


# ---- calibration file ----


def _cal_run(tmp_path, pairs, name="cal-x"):
    run = tmp_path / name
    log = eventlog.EventLog(run)
    log.append("run_start", run_id=name)
    cal = calibrate.fit(pairs)
    geom_sha = "a" * 64
    sha = write_calibration(run, calibration_doc(name, pairs, cal, geom_sha))
    log.append("calibration", source_run=name, file_sha256=sha, ok=int(cal.ok), reason=cal.reason,
               slope_ppm=cal.slope_ppm, offset_mdeg=cal.offset_mdeg, residual_mdeg=cal.residual_mdeg, n=cal.n,
               geometry_sha256=geom_sha, points=[[c, m] for c, m in pairs])  # fmt: skip
    return run, cal, geom_sha


GOOD_PAIRS = [(60, 62_000), (70, 72_100), (90, 91_900), (110, 112_200), (120, 121_900)]


def test_calibration_file_round_trip_refits_the_points(tmp_path):
    run, cal, g = _cal_run(tmp_path, GOOD_PAIRS)
    assert cal.ok
    got, doc, _ = load_calibration(run, g)
    assert got == cal and doc["points"] == [list(p) for p in GOOD_PAIRS]
    assert eventlog.check_values(doc) is None  # integers only (D-15)


def test_calibration_file_tampering_and_other_geometry_are_refused(tmp_path):
    run, _, g = _cal_run(tmp_path, GOOD_PAIRS)
    with pytest.raises(CalibrationError, match="another gauge geometry"):
        load_calibration(run, "b" * 64)
    p = run / "calibration.json"
    doc = json.loads(p.read_text())
    doc["points"][0][1] = 45_000  # a point edited after the fact
    p.write_text(eventlog.canonical(doc) + "\n")
    with pytest.raises(CalibrationError, match="not the file the calibration run logged"):
        load_calibration(run, g)


def test_a_disabled_calibration_stays_disabled_when_loaded(tmp_path):
    bad = [(60, 62_000), (70, None), (90, 91_900), (110, 112_200), (120, 121_900)]
    run, cal, g = _cal_run(tmp_path, bad)
    got, _, _ = load_calibration(run, g)
    assert not cal.ok and not got.ok
    with pytest.raises(CantVerify):
        got.to_command_mdeg(90_000)


# ---- witness service + observation ----


def test_disabled_calibration_takes_no_frame_and_spends_nothing(tmp_path):
    shots = []
    client, t, ledger = make_client(tmp_path)
    w = Witness(lambda: shots.append(1) or (None, b""), client, calibrate.NONE, GEOM)
    r = w.check(50).result
    assert (r.verdict, r.reason, r.commanded_zone) == (CANT_VERIFY, CALIBRATION, "LOW")
    assert shots == [] and t.posts == [] and ledger.committed_uusd() == 0


def test_stale_frame_spends_nothing(tmp_path):
    cal, _ = synth.calibrate(GEOM, tmp_path)
    client, t, ledger = make_client(tmp_path)

    def camera():
        raise CantVerify(STALE_FRAME, "no frame within 5.0 s")

    r = Witness(camera, client, cal, GEOM).check(90).result
    assert (
        (r.verdict, r.reason) == (CANT_VERIFY, STALE_FRAME) and t.posts == [] and ledger.committed_uusd() == 0
    )


def test_witness_check_pass_and_the_observation_text(tmp_path):
    cal, _ = synth.calibrate(GEOM, tmp_path)
    frame, _, labels = synth.render(Scene([50 + OFFSET_DEG], quad="steep", light="gradient"))
    line = synth.line_for(frame, labels, GEOM)
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    client, t, _ = make_client(tmp_path, ok(synth.envelope(line)))
    checked = Witness(lambda: (frame, jpeg), client, cal, GEOM).check(50)
    r = checked.result
    assert (r.verdict, r.commanded_zone, r.measured_zone) == (PASS, "LOW", "LOW")
    assert checked.frame_jpeg == jpeg and r.crop_jpeg.startswith(b"\xff\xd8") and len(t.posts) == 1
    out = witness_output(r).decode("ascii")
    first, second = out.split("\n")[:2]
    assert first == "PASS"
    assert second.startswith(
        "reason=ZONE_MATCH commanded_deg=50 commanded_zone=LOW measured_zone=LOW measured_deg="
    )
    assert "box" not in out and "mask" not in out  # the SAM line never goes to the model


def test_no_move_observation_text():
    out = witness_output(no_move(-1, "no kernel-allowed servo_set earlier in this request")).decode()
    assert out == (
        "CAN'T VERIFY\nreason=NO_MOVE commanded_deg=none commanded_zone=none measured_zone=none "
        "measured_deg=none\ndetail: no kernel-allowed servo_set earlier in this request"
    )
    assert NO_MOVE in out


# ---- CLI (dry runs only: no network, no key) ----


def test_calibrate_dry_run_prints_moves_and_reservation(capsys, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["calibrate", "--budget-usd", "0.05", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "no network, no key read" in out and "sam-3.1" in out
    assert "reservation: 5 images x $0.002500 = $0.012500" in out
    assert "moves: 60, 50, 60*, 70*, 90*, 110*, 120*" in out and "points [60, 70, 90, 110, 120]" in out
    assert cli.main(["calibrate", "--budget-usd", "0.05", "--dry-run", "--dry-run-start", "150"]) == 0
    assert "moves: 120*, 110*, 90*, 70*, 60*" in capsys.readouterr().out
    args = ["calibrate", "--budget-usd", "0.05", "--dry-run", "--points", "45,60,75,105,120"]
    assert cli.main(args) == 0 and "moves: 60, 35, 45*, 60*, 75*, 105*, 120*" in capsys.readouterr().out
    assert cli.main(["calibrate", "--budget-usd", "0.05", "--dry-run", "--points", "60,x"]) == 2


def test_calibrate_needs_a_budget_and_a_witness_dir(capsys, monkeypatch):
    with pytest.raises(SystemExit):
        cli.main(["calibrate", "--dry-run"])
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["calibrate", "--budget-usd", "0.05"]) == 2
    assert "--witness-dir is required" in capsys.readouterr().err


def test_run_dry_run_names_the_witness(capsys, tmp_path):
    assert cli.main(["run", "--budget-usd", "0.50", "--dry-run", "--pack", "agent-v3",
                     "--witness-dir", str(tmp_path), "--calibration", "cal-x"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "context pack agent-v3" in out
    assert "witness: sam-3.1, one image reserved per witness op ($0.002500), calibration cal-x" in out


# ---- setup check (ring3 witness-frame): no SAM ----


def test_frame_report_on_axis_and_off_axis():
    from ring3bridge.witness.service import frame_report

    frame, _, _ = synth.render(Scene([90.0], quad="flat"))
    rep = frame_report(frame, GEOM)
    assert rep.crop_img is not None and rep.lines[1].startswith("markers 0-3 found")
    assert "from the pivot" in rep.lines[2]
    covered, _, _ = synth.render(Scene([90.0], cover_marker=1))
    rep = frame_report(covered, GEOM)
    assert not rep.ok and rep.crop_img is None and "MARKERS" in rep.lines[-1]


def test_witness_frame_through_the_capture_folder(tmp_path, capsys, monkeypatch):
    """The CLI asks the helper for one frame (a fake helper thread answers), reports, saves under runs/."""
    import threading
    import time as _time

    from ring3bridge.witness.capture import add_frame_comment

    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    frame, _, _ = synth.render(Scene([90.0], quad="flat"))
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    stop = threading.Event()

    def helper():
        while not stop.is_set():
            for req in tmp_path.glob("*.req"):
                n = req.stem
                now = _time.time_ns()
                (tmp_path / f"{n}.tmp.jpg").write_bytes(
                    add_frame_comment(jpeg, n, now, now + 100_000_000, 3, 0)
                )
                (tmp_path / f"{n}.tmp.jpg").replace(tmp_path / f"{n}.jpg")
            _time.sleep(0.02)

    th = threading.Thread(target=helper, daemon=True)
    th.start()
    try:
        rc = cli.main(["witness-frame", "--witness-dir", str(tmp_path)])
    finally:
        stop.set()
        th.join(2)
    out = capsys.readouterr().out
    assert "markers 0-3 found" in out and "from the pivot" in out
    saved = list((tmp_path / "runs").glob("witness-frame-*"))
    assert len(saved) == 1 and (saved[0] / "frame.jpg").exists() and (saved[0] / "crop.jpg").exists()
    assert rc in (0, 1)


def test_calibrate_checks_dry_run(capsys, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["calibrate", "--budget-usd", "0.05", "--dry-run", "--checks"]) == 0
    out = capsys.readouterr().out
    assert "targets [50, 90, 130]" in out and "moves: 60, 50*, 80, 90*, 120, 130*   (* = checked)" in out
    assert "reservation: 12 images x $0.002500 = $0.030000 over 4 request(s)" in out
    assert cli.main(["calibrate", "--budget-usd", "0.05", "--checks", "--witness-dir", "."]) == 2
    assert "--calibration <run> is required" in capsys.readouterr().err


# ---- after the first real calibration (cal-20261103-194227: NOT_FOUND at all 5 points) ----


def test_raw_sam_reply_is_kept_even_when_it_does_not_parse(tmp_path):
    """The run log keeps SAM's reply body (as the transport returned it) for every check, also for
    SAM_FORMAT, so a real reply can be compared with the documented grammar without a second paid call."""
    cal, _ = synth.calibrate(GEOM, tmp_path)
    frame, _, _ = synth.render(Scene([90.0 + OFFSET_DEG]))
    bad = synth.envelope("the arm points up")
    client, _, _ = make_client(tmp_path, ok(bad))
    r = Witness(lambda: (frame, b"\xff\xd8"), client, cal, GEOM).check(90).result
    assert (r.verdict, r.reason) == (CANT_VERIFY, "SAM_FORMAT")
    assert r.sam_reply == bad and r.spend_uusd == 2500
    empty = synth.envelope("")
    (tmp_path / "e").mkdir()
    client, _, _ = make_client(tmp_path / "e", ok(empty))
    r = Witness(lambda: (frame, b"\xff\xd8"), client, cal, GEOM).check(90).result
    assert (r.reason, r.detail, r.sam_reply) == ("NOT_FOUND", "SAM found no arm", empty)


def _probe_frame(tmp_path, deg=90.0):
    frame, _, labels = synth.render(Scene([deg + OFFSET_DEG], quad="flat"))
    p = tmp_path / "frame.jpg"
    p.write_bytes(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())
    return p, frame, labels


def test_probe_plan_caps_at_five_images(tmp_path):
    from ring3bridge.calibration import PROBE_MAX, probe_plan

    f = tmp_path / "f.jpg"
    assert len(probe_plan([f], ["a", "b", "c"], ["full"])) == 3
    assert len(probe_plan([f], ["a", "b"], ["full", "tight"])) == 4
    with pytest.raises(ValueError, match="at most 5"):
        probe_plan([f], ["a", "b", "c"], ["full", "tight"])
    with pytest.raises(ValueError):
        probe_plan([f], ["a"], ["wide"])
    assert PROBE_MAX == 5


PROBE_WORD = "PROBE (saved frame, not evidence)"


def test_probe_sends_each_image_once_reserved_and_logs_the_raw_reply(tmp_path):
    from ring3bridge.calibration import PROBE_CROPS, probe, probe_plan
    from ring3bridge.witness.dial import Crop, Geometry

    path, frame, labels = _probe_frame(tmp_path)
    tight = Geometry(GEOM.marker_corners, Crop(*PROBE_CROPS["tight"]))
    replies = [
        ok(synth.envelope("")),  # full, "white plastic arm": concept not present
        ok(synth.envelope(synth.line_for(frame, labels, GEOM))),  # full, "servo horn"
        ok(
            synth.envelope(synth.sam_line(synth.label_boxes(frame, labels, tight), tight))
        ),  # tight, servo horn
    ]
    client, t, ledger = make_client(tmp_path, *replies)  # asserts one reserved image at every post
    log = eventlog.EventLog(tmp_path / "probe")
    log.append("run_start", run_id="probe")
    items = probe_plan([path], ["white plastic arm", "servo horn"], ["full", "tight"])[:3]
    printed: list[str] = []
    rows = probe(items, GEOM, client, log, out=printed.append)
    assert [(r["crop"], r["phrase"], r["reason"]) for r in rows] == [
        ("full", "white plastic arm", "NOT_FOUND"),
        ("full", "servo horn", PROBE_WORD),
        ("tight", "white plastic arm", PROBE_WORD),
    ]
    # S5 (T-12, D-88): a probe row is a saved frame of any age, never a witness measurement (INV-14 words)
    assert "MEASURED" not in "\n".join(printed) and sum(PROBE_WORD in line for line in printed) == 2
    assert abs(rows[1]["dial_mdeg"] - rows[2]["dial_mdeg"]) < 3000  # both crops measure the same arm
    assert len(t.posts) == 3 and ledger.settled_uusd() == 3 * 2500
    assert [json.loads(b)["input"][0]["content"][0]["text"] for _, b, _ in t.posts] == [
        "white plastic arm", "servo horn", "white plastic arm"]  # fmt: skip
    recs = [json.loads(x) for x in (tmp_path / "probe" / "events.jsonl").read_text().splitlines()]
    probes = [r for r in recs if r["type"] == "sam_probe"]
    assert len(probes) == 3 and all("sam_reply_blob" in r and "crop_blob" in r for r in probes)
    assert [r["reason"] for r in probes] == ["NOT_FOUND", PROBE_WORD, PROBE_WORD]
    assert "MEASURED" not in (tmp_path / "probe" / "events.jsonl").read_text()
    assert eventlog.verify(tmp_path / "probe").ok


def test_frame_report_gives_resolution_and_a_pivot_zoom():
    from ring3bridge.witness.service import frame_report

    frame, _, _ = synth.render(Scene([90.0], quad="flat"))
    rep = frame_report(frame, GEOM)
    assert any(line.startswith("camera resolves ") and "15 mm horn spans" in line for line in rep.lines)
    assert rep.pivot_img is not None and rep.pivot_img.shape[:2] == (840, 1200)  # 60 x 42 mm at 20 px/mm


def test_calibrate_probe_dry_run_and_cap(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    path, _, _ = _probe_frame(tmp_path)
    args = ["calibrate", "--budget-usd", "0.02", "--dry-run", "--probe", str(path),
            "--phrases", "white plastic arm;servo horn;small white lever", "--crops", "full"]  # fmt: skip
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "3 image(s) (max 5) x $0.002500 = $0.007500" in out and "phrase='servo horn'" in out
    args[-1] = "full,tight"
    assert cli.main(args) == 2 and "6 images planned, at most 5" in capsys.readouterr().err


# ---- D-79: hub-offset readout in ring3 witness-frame (local image analysis, no SAM) ----


def _hub_scene(
    shaft=(0.0, 0.0), cap=True, body=True, extra_caps=(), quad="flat", light="even", cap_r=3.5, hole=True
):
    """A rendered camera frame with the D-78 mount drawn in gauge mm through the detected homography: blue
    servo body on its back around the shaft, the blackened horn pointing at 90, a white cap at the shaft."""
    frame, _, _ = synth.render(Scene([], quad=quad, light=light))
    from ring3bridge.witness.dial import find_dial

    to_img = np.linalg.inv(find_dial(frame, GEOM).img_to_mm)

    def px(pts):
        p = np.c_[np.asarray(pts, float), np.ones(len(pts))] @ to_img.T
        return np.round(p[:, :2] / p[:, 2:]).astype(np.int32)

    def circle(cx, cy, r):
        t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        return px(np.c_[cx + r * np.cos(t), cy + r * np.sin(t)])

    sx, sy = shaft
    if body:
        outline = [(sx - 6.1, sy - 17), (sx + 6.1, sy - 17), (sx + 6.1, sy + 5.5), (sx - 6.1, sy + 5.5)]
        cv2.fillPoly(frame, [px(outline)], (190, 90, 30))
    cv2.fillPoly(
        frame, [px([(sx - 2.5, sy), (sx + 2.5, sy), (sx + 2, sy + 15), (sx - 2, sy + 15)])], (35, 35, 35)
    )
    for c in ([(sx, sy)] if cap else []) + list(extra_caps):
        cv2.fillPoly(frame, [circle(c[0], c[1], cap_r)], (238, 238, 238))
        if hole:  # the screw hole in the middle of the hub = the shaft centre (what D-79's readout finds)
            cv2.fillPoly(frame, [circle(c[0], c[1], 1.2)], (45, 45, 45))
    frame = cv2.GaussianBlur(frame, (0, 0), 1.0)
    return cv2.imdecode(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])[1], cv2.IMREAD_COLOR)


@pytest.mark.parametrize("variant", [("flat", "even"), ("mild", "gradient"), ("steep", "dim")])
@pytest.mark.parametrize(
    "shaft,word",
    [
        ((1.2, 0.0), "LEFT"),
        ((-1.2, 0.0), "RIGHT"),
        ((0.0, 1.2), "DOWN"),
        ((0.0, -1.2), "UP"),
        ((0.2, -0.2), None),
    ],  # fmt: skip
)
def test_hub_offset_per_direction(shaft, word, variant):
    from ring3bridge.witness.service import frame_report

    rep = frame_report(_hub_scene(shaft, quad=variant[0], light=variant[1]), GEOM)
    assert rep.hub is not None, rep.lines[-1]
    assert abs(rep.hub.dx_mm - shaft[0]) <= 0.2 and abs(rep.hub.dy_mm - shaft[1]) <= 0.2, rep.lines[-1]
    line = rep.lines[-1]
    if word is None:
        assert line.endswith(" OK") and rep.hub.dist_mm <= 0.5
    else:
        m = re.search(r" MOVE: move servo (LEFT|RIGHT|UP|DOWN) (\d+\.\d) mm \(as seen in pivot\.jpg", line)
        assert m and m.group(1) == word and abs(float(m.group(2)) - 1.2) <= 0.2, line
        assert "OK" not in line
        assert not rep.ok  # witness-frame exits 1 until the hub is placed


@pytest.mark.parametrize(
    "kw,why",
    [
        ({"cap": False}, "no dark screw hole"),  # horn and body only
        ({"hole": False}, "no dark screw hole"),  # a white hub without a visible screw hole
        ({"extra_caps": [(3.0, 3.0)]}, "2 candidates"),  # two hubs near the pivot
        ({"shaft": (9.0, 0.0)}, "no dark screw hole"),  # hub far from the pivot: not searched for
    ],
)
def test_hub_offset_never_ok_when_unsure(kw, why):
    from ring3bridge.witness.service import frame_report

    rep = frame_report(_hub_scene(**kw), GEOM)
    assert rep.hub is None and rep.lines[-1].startswith("hub offset: not found"), rep.lines[-1]
    assert why is None or why in rep.lines[-1]
    assert not rep.ok and "OK" not in rep.lines[-1]


def test_recorded_frames_say_not_found():
    """Every recorded real frame predates the D-78 remount (servo on its side, no cap facing the camera):
    the readout must say not found there, never OK. Uses the probe frame committed as a fixture."""
    from ring3bridge.witness.service import frame_report

    rep = frame_report(cv2.imread(str(RECORDED_FRAME)), GEOM)
    assert rep.lines[-1] == "hub offset: not found (no dark screw hole in a white hub near the PIVOT mark)"
    assert rep.pivot_img is not None and rep.pivot_img.shape[:2] == (840, 1200)


def test_hub_offset_on_the_real_upright_frame():
    """witness-frame-20261103-213006 (D-78 remount, servo on its back): the screw hole is the shaft centre;
    the lead measured it at about dx -2.4 mm, dy 0.0 mm by hand."""
    from ring3bridge.witness.service import frame_report

    rep = frame_report(cv2.imread(str(Path(__file__).parent / "recorded" / "frame-upright-213006.jpg")), GEOM)
    assert rep.hub is not None, rep.lines[-1]
    assert abs(rep.hub.dx_mm - -2.4) <= 0.3 and abs(rep.hub.dy_mm - 0.0) <= 0.3, rep.lines[-1]
    assert "MOVE: move servo RIGHT 2.3 mm" in rep.lines[-1] and not rep.ok
