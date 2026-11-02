"""Calibration maths (T-07 step 6): linear fit, residual limit, disabled-for-session, never guessed."""

import pytest

from ring3bridge.witness import CALIBRATION, CantVerify
from ring3bridge.witness.calibrate import NONE, RESIDUAL_LIMIT_MDEG, fit

CMDS = (60, 75, 90, 105, 120)


def pairs(f):
    return [(c, f(c)) for c in CMDS]


def test_good_fit():
    cal = fit(pairs(lambda c: c * 1000 + 4000 + (300 if c == 90 else -100)))
    assert cal.ok, cal.message()
    assert abs(cal.slope_ppm - 1_000_000) < 20_000 and abs(cal.offset_mdeg - 4000) < 1500
    assert cal.residual_mdeg <= RESIDUAL_LIMIT_MDEG
    assert abs(cal.to_command_mdeg(94_000) - 90_000) < 500
    assert all(type(v) is int for v in (cal.slope_ppm, cal.offset_mdeg, cal.residual_mdeg))
    assert "residual" in cal.message()


def test_round_trip_exact_for_ideal_data():
    cal = fit(pairs(lambda c: c * 1000))
    assert (cal.slope_ppm, cal.offset_mdeg, cal.residual_mdeg) == (1_000_000, 0, 0)
    for d in (25_000, 50_000, 90_000, 155_000, -3_000):
        assert cal.to_command_mdeg(d) == d


@pytest.mark.parametrize(
    "data,word",
    [
        (pairs(lambda c: c * 1000 + (8000 if c == 90 else 0)), "residual"),
        (pairs(lambda c: c * 1000 + (c - 90) ** 2 * 15), "residual"),
        (pairs(lambda c: 180_000 - c * 1000), "negative"),
        (pairs(lambda c: c * 1300), "slope"),
        (pairs(lambda c: c * 1000 + 20_000), "offset"),
        ([(60, 60_000), (75, 75_000), (90, None), (105, 105_000), (120, 120_000)], "no measurement"),
        (pairs(lambda c: c * 1000)[:4], "need 5"),
        ([(80, 80_000), (85, 85_000), (90, 90_000), (95, 95_000), (100, 100_000)], "span"),
        ([(60, 60_000), (60, 60_000), (90, 90_000), (105, 105_000), (120, 120_000)], "repeated"),
    ],
)
def test_bad_calibration_disables_the_witness(data, word):
    cal = fit(data)
    assert not cal.ok and word in cal.reason
    assert cal.message().startswith("witness disabled for this session")
    with pytest.raises(CantVerify) as e:
        cal.to_command_mdeg(90_000)
    assert e.value.reason == CALIBRATION


def test_uncalibrated_is_cant_verify():
    with pytest.raises(CantVerify) as e:
        NONE.to_command_mdeg(90_000)
    assert e.value.reason == CALIBRATION


def test_pairs_must_be_integers():
    with pytest.raises(TypeError):
        fit([(c, float(c * 1000)) for c in CMDS])
