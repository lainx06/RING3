"""Synthetic end-to-end fixtures (T-07 step 7): rendered gauge + drawn arm under perspective, blur and light,
through the real marker detection, crop, SAM parser (fake transport), angle, calibration and verdict."""

import pytest

from ring3bridge.witness import BOUNDARY, CALIBRATION, CANT_VERIFY, FAIL, OUT_OF_SCALE, PASS, WRONG_ZONE
from ring3bridge.witness.angle import arm_angle
from ring3bridge.witness.calibrate import RESIDUAL_LIMIT_MDEG
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.sam import parse_line
from tests.witness import synth
from tests.witness.synth import OFFSET_DEG, Scene

GEOM = load_geometry()


@pytest.fixture(scope="module")
def cal(tmp_path_factory):
    c, _ = synth.calibrate(GEOM, tmp_path_factory.mktemp("cal"))
    assert c.ok, c.message()
    return c


@pytest.mark.parametrize("quad", sorted(synth.QUADS))
@pytest.mark.parametrize("light,blur", [("even", 0.8), ("gradient", 1.5), ("dim", 1.2)])
def test_raw_angle_within_tolerance(quad, light, blur):
    """Camera over the pivot: the box-centre angle is within 4.5 deg of the drawn arm (the rest is the
    hub-vs-tip box bias, which the linear calibration absorbs)."""
    for i, a in enumerate((40, 55, 90, 125, 140)):
        frame, _, labels = synth.render(Scene([a], quad=quad, light=light, blur=blur, seed=i))
        recs = parse_line(synth.sam_line(synth.label_boxes(frame, labels, GEOM), GEOM), *GEOM.crop.size)
        got = arm_angle(recs, GEOM.crop).dial_mdeg / 1000
        assert abs(got - a) <= 4.5, (quad, light, a, got)


def test_calibration_from_synthetic_session(cal):
    assert cal.residual_mdeg <= RESIDUAL_LIMIT_MDEG
    assert abs(cal.predict_dial_mdeg(90) - 90_000 - OFFSET_DEG * 1000) <= 3000  # recovers the mounting offset


@pytest.mark.parametrize("offset", [-7.0, -5.0, 4.0, 5.0, 7.0])
def test_dead_zone_offsets_disable_the_witness(tmp_path, offset):
    """D-58 box-centre method, SG90 horn: within ~5 deg of 90 the hub sets both x edges of the box, so the
    reading sticks at 90. With the mounting offset putting that dead zone on a calibration point, the linear
    fit's residual passes the limit: the witness disables itself with a message, and every check is
    CAN'T VERIFY: CALIBRATION (fails closed, never guessed)."""
    c, _ = synth.calibrate(GEOM, tmp_path, offset_deg=offset)
    assert not c.ok and "residual" in c.reason
    assert c.message().startswith("witness disabled for this session")
    res, t, _, _ = synth.run_check(Scene([90 + offset]), 90, c, GEOM, tmp_path)
    assert res.verdict == CANT_VERIFY and res.reason == CALIBRATION and t.posts == []


CHECKS = [
    # (name, arm truth deg, commanded deg, verdict, reason)
    ("LOW reached", 50 + OFFSET_DEG, 50, PASS, None),
    ("MID reached", 90 + OFFSET_DEG, 90, PASS, None),
    ("HIGH reached", 130 + OFFSET_DEG, 130, PASS, None),
    ("held in MID, HIGH commanded", 90 + OFFSET_DEG, 130, FAIL, WRONG_ZONE),
    ("held in LOW, HIGH commanded", 50 + OFFSET_DEG, 130, FAIL, WRONG_ZONE),
    ("held in HIGH, LOW commanded", 130 + OFFSET_DEG, 50, FAIL, WRONG_ZONE),
    ("drooped below scale", 8.0, 50, FAIL, OUT_OF_SCALE),
    ("pushed past scale", 172.0, 130, FAIL, OUT_OF_SCALE),
    ("on the LOW/MID edge", 70 + OFFSET_DEG, 50, CANT_VERIFY, BOUNDARY),
    ("on the MID/HIGH edge", 110 + OFFSET_DEG, 130, CANT_VERIFY, BOUNDARY),
]


@pytest.mark.parametrize("name,truth,cmd,verdict,reason", CHECKS, ids=[c[0] for c in CHECKS])
@pytest.mark.parametrize("quad,light", [("mild", "even"), ("steep", "gradient"), ("rotated", "dim")])
def test_verdicts_after_calibration(cal, tmp_path, name, truth, cmd, verdict, reason, quad, light):
    res, t, ledger, line = synth.run_check(Scene([truth], quad=quad, light=light), cmd, cal, GEOM, tmp_path)
    assert res.verdict == verdict, (name, res)
    if reason:
        assert res.reason == reason
    assert len(t.posts) == 1 and ledger.settled_uusd() == 2500  # one image, reserved then settled
    assert line and res.sam_line == line and len(res.image_sha256) == 64
    if verdict != CANT_VERIFY:
        assert type(res.calibrated_mdeg) is int and type(res.dial_mdeg) is int


@pytest.mark.parametrize("axis", [(0.0, 35.0), (20.0, 10.0), (-12.0, 25.0), (8.0, 0.0)])
def test_off_axis_camera_never_passes_a_wrong_zone(tmp_path, axis):
    """Parallax: the horn is ~27 mm above the paper, so a camera not centred over the pivot shifts it. The
    calibration either disables the witness or the verdicts stay zone-correct; a wrong zone never PASSes."""
    c, _ = synth.calibrate(GEOM, tmp_path, camera_axis_mm=axis)
    for i, (truth_cmd, cmd) in enumerate([(50, 50), (90, 90), (130, 130), (90, 130), (50, 90), (130, 50)]):
        d = tmp_path / f"c{i}"
        d.mkdir()
        res, *_ = synth.run_check(Scene([truth_cmd + OFFSET_DEG], camera_axis_mm=axis), cmd, c, GEOM, d)
        if not c.ok:
            assert res.verdict == CANT_VERIFY and res.reason == CALIBRATION
        elif truth_cmd != cmd:
            assert res.verdict != PASS, (axis, truth_cmd, cmd, res)
        else:
            assert res.verdict in (PASS, CANT_VERIFY), (axis, truth_cmd, cmd, res)
