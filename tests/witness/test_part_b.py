"""T-07 Part B, offline: the calibration plan and script, the calibration file, the witness service and the
witness observation text, the CLI dry run. No QEMU, no network, no key, no camera (synthetic frames)."""

from __future__ import annotations

import json

import cv2
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
