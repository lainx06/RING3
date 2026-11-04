"""Servo map (D-103, T-16): commanded servo angle -> the arm's angle on the printed gauge.

A least-squares line ``gauge = gain * command + offset`` fitted to arm angles measured in the rectified crops
of a zone check (first: the six crops of ``zc-20261104-153656``, 2026-11-04, where an 80 deg command span
moved the arm ~60 deg). Stored in ``config/servo_map.toml`` as integers (gain in parts per million, offset
and points in millidegrees) with its provenance (run id, evidence path, date, method); the loader re-fits the
stored points and refuses a file whose numbers it does not reproduce (fails closed, like a calibration).

Used by the **vision backend only**: the expected zone and the boundary check take the mapped angle, every
witness record logs the commanded and the mapped angle, and the zone-check targets are the commands whose
mapped angles are the zone centres (clamped to the kernel's 30-150). The SAM backend keeps its own
calibration (D-72) and never reads this file. ``IDENTITY`` (gain 1, offset 0) is the map for a cell whose
arm follows the command exactly (the simulated cell, tests) and for runs without a map.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ring3bridge.config import REPO, ConfigError

SERVO_MAP_TOML = REPO / "config" / "servo_map.toml"
GAIN_PPM_RANGE = (300_000, 2_000_000)  # a gain outside this is not a servo, refuse the file
OFFSET_MDEG_LIMIT = 90_000
MIN_POINTS = 4
MIN_SPAN_DEG = 40
SERVO_RANGE = (30, 150)  # policy.h SERVO_MIN_DEG / SERVO_MAX_DEG, the kernel's range for a command


@dataclass(frozen=True)
class ServoMap:
    gain_ppm: int
    offset_mdeg: int
    source_run: str = ""
    evidence: str = ""
    measured: str = ""
    method: str = ""
    points: tuple[tuple[int, int], ...] = ()  # (commanded deg, measured gauge millidegrees)
    residual_mdeg: int = 0  # worst |fit - measured| over the points

    @property
    def identity(self) -> bool:
        return self.gain_ppm == 1_000_000 and self.offset_mdeg == 0

    def mapped_mdeg(self, command_deg: int) -> int:
        """Commanded degrees -> gauge millidegrees, rounded half up (integers only)."""
        if type(command_deg) is not int:
            raise TypeError("command must be an integer")
        num = self.gain_ppm * command_deg * 1000 + self.offset_mdeg * 1_000_000
        return (2 * num + 1_000_000) // (2 * 1_000_000)

    def command_for(self, target_mdeg: int) -> int:
        """The integer command whose mapped angle is nearest ``target_mdeg``, clamped to the kernel's
        range: ``(target - offset) / gain`` rounded half up."""
        num = (target_mdeg - self.offset_mdeg) * 1_000_000
        den = self.gain_ppm * 1000
        cmd = (2 * num + den) // (2 * den)
        lo, hi = SERVO_RANGE
        return max(lo, min(hi, int(cmd)))

    def describe(self) -> str:
        if self.identity:
            return "servo map: identity (the arm follows the command)"
        return (
            f"servo map: gauge = {self.gain_ppm / 1e6:.4f} x command + {self.offset_mdeg / 1000:.2f} deg "
            f"({len(self.points)} points, worst residual {self.residual_mdeg / 1000:.2f} deg, from "
            f"{self.source_run or '?'}, {self.measured or '?'})"
        )


IDENTITY = ServoMap(1_000_000, 0)


def fit_servo_map(points: Sequence[tuple[int, int]], **provenance: str) -> ServoMap:
    """Least squares over (commanded deg, measured gauge mdeg); integers out; residual from the integers."""
    pts = tuple((int(c), int(m)) for c, m in points)
    if len(pts) < MIN_POINTS:
        raise ValueError(f"{len(pts)} points, need {MIN_POINTS}")
    cmds = [c for c, _ in pts]
    if max(cmds) - min(cmds) < MIN_SPAN_DEG:
        raise ValueError(f"commands span {max(cmds) - min(cmds)} deg, need {MIN_SPAN_DEG}")
    n = len(pts)
    mx = sum(cmds) / n
    my = sum(m for _, m in pts) / n
    sxx = sum((c - mx) ** 2 for c in cmds)
    sxy = sum((c - mx) * (m - my) for c, m in pts)
    gain = sxy / sxx / 1000  # mdeg per deg -> deg per deg
    gain_ppm = int(round(gain * 1_000_000))
    offset = int(round(my - gain * 1000 * mx))
    m0 = ServoMap(gain_ppm, offset)
    resid = max(abs(m0.mapped_mdeg(c) - m) for c, m in pts)
    return ServoMap(gain_ppm, offset, points=pts, residual_mdeg=resid, **provenance)


def load_servo_map(path: Path = SERVO_MAP_TOML) -> ServoMap:
    """The stored map, re-fitted from its points; a mismatch, a missing field or an unreasonable gain is a
    ``ConfigError``. A missing file is ``IDENTITY`` with no provenance (the setup has not been measured)."""
    path = Path(path)
    if not path.exists():
        return IDENTITY
    with open(path, "rb") as f:
        d = tomllib.load(f).get("servo_map")
    if type(d) is not dict:
        raise ConfigError("[servo_map] table missing")
    for key in ("gain_ppm", "offset_mdeg"):
        if type(d.get(key)) is not int:
            raise ConfigError(f"servo_map {key} must be an integer")
    prov = {}
    for key in ("source_run", "evidence", "measured", "method"):
        v = d.get(key)
        if type(v) is not str or not v:
            raise ConfigError(f"servo_map {key} (provenance) is required")
        prov[key] = v
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", prov["measured"]):
        raise ConfigError("servo_map measured must be YYYY-MM-DD")
    pts = d.get("points")
    if type(pts) is not list or not all(
        type(p) is list and len(p) == 2 and type(p[0]) is int and type(p[1]) is int for p in pts
    ):
        raise ConfigError("servo_map points must be [[command deg, measured mdeg], ...]")
    try:
        fitted = fit_servo_map([(p[0], p[1]) for p in pts], **prov)
    except ValueError as e:
        raise ConfigError(f"servo_map points: {e}") from None
    if (fitted.gain_ppm, fitted.offset_mdeg) != (d["gain_ppm"], d["offset_mdeg"]):
        raise ConfigError("servo_map gain/offset differ from the fit of the stored points")
    if not GAIN_PPM_RANGE[0] <= fitted.gain_ppm <= GAIN_PPM_RANGE[1]:
        raise ConfigError(f"servo_map gain {fitted.gain_ppm / 1e6:.3f} outside the plausible range")
    if abs(fitted.offset_mdeg) > OFFSET_MDEG_LIMIT:
        raise ConfigError("servo_map offset beyond 90 deg")
    return fitted
