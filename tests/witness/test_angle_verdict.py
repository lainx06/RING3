"""Arm angle from boxes (D-58) and the zone verdict, as pure functions."""

import pytest

from ring3bridge.witness import (
    AMBIGUOUS,
    BOUNDARY,
    COMMAND_BOUNDARY,
    COMMAND_RANGE,
    FAIL,
    LOW_CONFIDENCE,
    NOT_FOUND,
    OUT_OF_SCALE,
    PASS,
    SHAPE,
    WRONG_ZONE,
    CantVerify,
)
from ring3bridge.witness.angle import ArmModel, arm_angle
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.sam import SamRecord
from ring3bridge.witness.verdict import judge

CROP = load_geometry().crop


def box(x0, y0, x1, y1, c=900_000, oid=1):
    """A record whose box covers gauge mm [x0, x1] x [y0, y1] (inverse of angle.box_mm)."""
    u0, v1 = CROP.mm_to_px(x0, y0)
    u1, v0 = CROP.mm_to_px(x1, y1)
    return SamRecord(
        oid, round(u0 + 0.5), round(v0 + 0.5), round(u1 + 0.5), round(v1 + 0.5), c, None, 1, 1, 1
    )


ARM_90 = (-3.5, -3.5, 3.5, 20.0)
ARM_45 = (-3.5, -3.5, 14.7, 14.7)
ARM_135 = (-14.7, -3.5, 3.5, 14.7)


@pytest.mark.parametrize("b,deg", [(ARM_90, 90.0), (ARM_45, 45.0), (ARM_135, 135.0)])
def test_angle_through_box_centre(b, deg):
    r = arm_angle([box(*b)], CROP)
    assert abs(r.dial_mdeg - deg * 1000) <= 500
    assert type(r.dial_mdeg) is int


def test_centre_not_farthest_corner_near_90():
    # an arm at 90 deg: the farthest corner is off by atan(3.5/20) ~ 10 deg; the centre is exact
    r = arm_angle([box(*ARM_90)], CROP)
    assert abs(r.dial_mdeg - 90_000) <= 300


def test_no_records_is_not_found():
    with pytest.raises(CantVerify) as e:
        arm_angle([], CROP)
    assert e.value.reason == NOT_FOUND


@pytest.mark.parametrize(
    "b,why",
    [
        ((20.0, 20.0, 30.0, 40.0), "away from the pivot"),
        ((-3.5, -3.5, 3.5, 60.0), "far too long"),
        ((-3.5, -3.5, 3.5, 6.0), "too short"),
        ((-6.0, -6.0, 6.0, 6.0), "centred on the pivot"),
        ((-60.0, -10.0, 60.0, 70.0), "the whole dial"),
    ],
)
def test_wrong_shape(b, why):
    with pytest.raises(CantVerify) as e:
        arm_angle([box(*b)], CROP)
    assert e.value.reason == SHAPE, why


def test_two_plausible_is_ambiguous_overlapping_or_not():
    for second in (ARM_135, ARM_45, (-3.5, -3.5, 4.0, 19.0)):
        with pytest.raises(CantVerify) as e:
            arm_angle([box(*ARM_90), box(*second, oid=2)], CROP)
        assert e.value.reason == AMBIGUOUS


def test_low_confidence_candidate_still_counts_for_ambiguity():
    with pytest.raises(CantVerify) as e:
        arm_angle([box(*ARM_90), box(*ARM_45, c=50_000, oid=2)], CROP)
    assert e.value.reason == AMBIGUOUS


def test_one_plausible_plus_junk_is_measured():
    r = arm_angle([box(30.0, 30.0, 40.0, 40.0), box(*ARM_90, oid=7)], CROP)
    assert r.obj_id == 7


@pytest.mark.parametrize("c", [None, 0, 499_999])
def test_low_or_missing_confidence(c):
    with pytest.raises(CantVerify) as e:
        arm_angle([box(*ARM_90, c=c)], CROP)
    assert e.value.reason == LOW_CONFIDENCE


def test_mask_confidence_used_when_box_has_none():
    r0 = box(*ARM_90, c=None)
    r = SamRecord(r0.obj_id, r0.x1, r0.y1, r0.x2, r0.y2, None, 800_000, 1, 1, 1)
    assert arm_angle([r], CROP).conf_ppm == 800_000
    assert arm_angle([r0], CROP, ArmModel(require_conf=False)).dial_mdeg > 0


# ---- verdict ----

CASES = [
    (50, 50_000, PASS, "LOW"),
    (90, 90_000, PASS, "MID"),
    (130, 130_000, PASS, "HIGH"),
    (50, 90_000, FAIL, WRONG_ZONE),
    (130, 90_000, FAIL, WRONG_ZONE),
    (90, 35_001, FAIL, WRONG_ZONE),
    (50, 24_999, FAIL, OUT_OF_SCALE),
    (50, 155_001, FAIL, OUT_OF_SCALE),
    (130, 170_000, FAIL, OUT_OF_SCALE),
    (50, -10_000, FAIL, OUT_OF_SCALE),
    (50, 25_000, None, BOUNDARY),
    (50, 35_000, None, BOUNDARY),
    (50, 65_000, None, BOUNDARY),
    (90, 75_000, None, BOUNDARY),
    (90, 105_000, None, BOUNDARY),
    (130, 115_000, None, BOUNDARY),
    (130, 145_000, None, BOUNDARY),
    (130, 155_000, None, BOUNDARY),
    (70, 90_000, None, COMMAND_BOUNDARY),
    (35, 50_000, None, COMMAND_BOUNDARY),
    (145, 130_000, None, COMMAND_BOUNDARY),
    (110, 130_000, None, COMMAND_BOUNDARY),
    (20, 50_000, None, COMMAND_RANGE),
    (160, 130_000, None, COMMAND_RANGE),
]


@pytest.mark.parametrize("cmd,meas,verdict,what", CASES)
def test_judge(cmd, meas, verdict, what):
    if verdict is None:
        with pytest.raises(CantVerify) as e:
            judge(cmd, meas)
        assert e.value.reason == what
    else:
        j = judge(cmd, meas)
        assert j.verdict == verdict
        assert (j.measured_zone if verdict == PASS else j.reason) == what


def test_judge_sweep_pass_only_when_clear_and_same_zone():
    zones = {"LOW": (35_000, 65_000), "MID": (75_000, 105_000), "HIGH": (115_000, 145_000)}
    for cmd in range(30, 151):
        for meas in range(-20_000, 200_001, 250):
            try:
                j = judge(cmd, meas)
            except CantVerify:
                continue
            if j.verdict == PASS:
                lo, hi = zones[j.commanded_zone]
                assert lo < meas < hi, (cmd, meas)
            if meas < 25_000 or meas > 155_000:
                assert j.verdict == FAIL and j.reason == OUT_OF_SCALE
