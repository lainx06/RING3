"""Calibration maths (T-07 step 6): commanded servo angle -> measured dial angle, a linear fit.

From at least 5 (commanded deg, measured dial millidegrees) pairs with distinct commands spanning >= 40 deg,
fit ``dial = slope * command + offset`` by least squares. The fit is stored as integers (slope in parts per
million, offset in millidegrees) and the residual is recomputed from those integers, so the logged numbers are
the numbers used. The witness is **disabled for the session** (reported, never guessed) when

  * a point is missing (its measurement was CAN'T VERIFY),
  * the worst residual exceeds ``RESIDUAL_LIMIT_MDEG`` *(prov.)*,
  * the slope is outside ``SLOPE_PPM`` (a negative slope = the servo turns against the printed scale), or
  * the fitted dial angle at a 90 deg command is more than ``OFFSET_LIMIT_MDEG`` from 90 deg (horn mounted
    more than about one spline tooth off). Checked at 90, not at the 0 deg intercept, which the slope moves.

A disabled calibration makes every check ``CAN'T VERIFY: CALIBRATION`` before any spend.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ring3bridge.witness import CALIBRATION, CantVerify

MIN_POINTS = 5
MIN_SPAN_DEG = 40
RESIDUAL_LIMIT_MDEG = 3_000  # (prov.)
SLOPE_PPM = (850_000, 1_150_000)  # (prov.)
OFFSET_LIMIT_MDEG = 15_000  # (prov.) SG90 spline: 25 teeth, 14.4 deg each


@dataclass(frozen=True)
class Calibration:
    ok: bool
    reason: str  # "" when ok, else the message shown to the user
    slope_ppm: int = 0
    offset_mdeg: int = 0
    residual_mdeg: int = 0  # worst |fit - measured| over the points
    n: int = 0

    def to_command_mdeg(self, dial_mdeg: int) -> int:
        """Measured dial angle -> calibrated (servo command) angle, integer millidegrees, rounded half up."""
        if not self.ok:
            raise CantVerify(CALIBRATION, self.reason or "witness disabled")
        num = (dial_mdeg - self.offset_mdeg) * 1_000_000
        return (2 * num + self.slope_ppm) // (2 * self.slope_ppm)

    def predict_dial_mdeg(self, command_deg: int) -> int:
        return (self.slope_ppm * command_deg + 500) // 1000 + self.offset_mdeg

    def message(self) -> str:
        if self.ok:
            return (
                f"calibration ok: slope {self.slope_ppm / 1e6:.4f}, "
                f"dial at a 90 deg command {self.predict_dial_mdeg(90) / 1000:.2f} deg, "
                f"residual {self.residual_mdeg / 1000:.2f} deg (limit {RESIDUAL_LIMIT_MDEG / 1000:.1f}), "
                f"{self.n} points"
            )
        return f"witness disabled for this session: {self.reason}"


NONE = Calibration(False, "not calibrated")


def _disabled(reason: str, **kw: int) -> Calibration:
    return Calibration(False, reason, **kw)


def fit(pairs: Sequence[tuple[int, int | None]]) -> Calibration:
    if len(pairs) < MIN_POINTS:
        return _disabled(f"{len(pairs)} points, need {MIN_POINTS}")
    missing = [c for c, m in pairs if m is None]
    if missing:
        return _disabled(f"no measurement at commanded {missing} deg")
    pts = [(c, m) for c, m in pairs if m is not None]
    if any(type(c) is not int or type(m) is not int for c, m in pts):
        raise TypeError("calibration pairs are (int deg, int millidegrees)")
    cmds = [c for c, _ in pts]
    if len(set(cmds)) != len(cmds):
        return _disabled("repeated commanded angle")
    if max(cmds) - min(cmds) < MIN_SPAN_DEG:
        return _disabled(f"commanded angles span {max(cmds) - min(cmds)} deg, need {MIN_SPAN_DEG}")
    n = len(pts)
    mx = sum(cmds) / n
    my = sum(m for _, m in pts) / n
    sxx = sum((c - mx) ** 2 for c in cmds)
    sxy = sum((c - mx) * (m - my) for c, m in pts)
    slope_mdeg_per_deg = sxy / sxx
    slope_ppm = int(round(slope_mdeg_per_deg * 1000))
    offset = int(round(my - slope_mdeg_per_deg * mx))
    cal = Calibration(True, "", slope_ppm, offset, 0, n)
    resid = max(abs(cal.predict_dial_mdeg(c) - m) for c, m in pts)
    kw = {"slope_ppm": slope_ppm, "offset_mdeg": offset, "residual_mdeg": resid, "n": n}
    if not SLOPE_PPM[0] <= slope_ppm <= SLOPE_PPM[1]:
        hint = " (negative: the servo turns against the printed scale)" if slope_ppm < 0 else ""
        return _disabled(
            f"slope {slope_ppm / 1e6:.3f} outside {SLOPE_PPM[0] / 1e6}-{SLOPE_PPM[1] / 1e6}{hint}", **kw
        )
    off90 = cal.predict_dial_mdeg(90) - 90_000
    if abs(off90) > OFFSET_LIMIT_MDEG:
        return _disabled(
            f"offset at 90 deg {off90 / 1000:.1f} beyond {OFFSET_LIMIT_MDEG / 1000:.0f}: re-seat the horn",
            **kw,
        )
    if resid > RESIDUAL_LIMIT_MDEG:
        return _disabled(
            f"residual {resid / 1000:.2f} deg above the {RESIDUAL_LIMIT_MDEG / 1000:.1f} deg limit", **kw
        )
    return Calibration(True, "", **kw)
