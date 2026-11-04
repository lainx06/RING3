"""Zone and verdict (T-07 step 5, D-58). Integer millidegrees in, integer comparisons only.

Zones (kernel servo range 30-150 deg): LOW 30-70, MID 70-110, HIGH 110-150. A boundary band of +-5 deg
*(prov.)* sits at all four edges (30, 70, 110, 150), bounds included.

  * measured below 25 deg or above 155 deg      -> FAIL (OUT_OF_SCALE), never PASS
  * measured inside a band                      -> CAN'T VERIFY (BOUNDARY)
  * measured zone == commanded zone             -> PASS
  * measured zone != commanded zone             -> FAIL (WRONG_ZONE)

The commanded zone must itself be unambiguous: a commanded angle inside a band -> CAN'T VERIFY
(COMMAND_BOUNDARY); outside 30-150 -> CAN'T VERIFY (COMMAND_RANGE). Those are checked before any spend.
These constants must equal the printed gauge (``hardware/gauge/geometry.json``); a test compares them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ring3bridge.witness import (
    BOUNDARY,
    COMMAND_BOUNDARY,
    COMMAND_RANGE,
    FAIL,
    OUT_OF_SCALE,
    PASS,
    WRONG_ZONE,
    ZONE_MATCH,
    CantVerify,
)

ZONES = (("LOW", 30_000, 70_000), ("MID", 70_000, 110_000), ("HIGH", 110_000, 150_000))
EDGES = (30_000, 70_000, 110_000, 150_000)
BAND_MDEG = 5_000  # (prov.)
SCALE_MDEG = (25_000, 155_000)


def _in_band(mdeg: int) -> int | None:
    for e in EDGES:
        if abs(mdeg - e) <= BAND_MDEG:
            return e
    return None


def _zone(mdeg: int) -> str:
    for name, lo, hi in ZONES:
        if lo < mdeg < hi:
            return name
    raise AssertionError("caller checks bands and scale first")


def commanded_zone(deg: int) -> str:
    if type(deg) is not int or not 30 <= deg <= 150:
        raise CantVerify(COMMAND_RANGE, f"commanded {deg} deg is outside 30-150")
    e = _in_band(deg * 1000)
    if e is not None:
        raise CantVerify(COMMAND_BOUNDARY, f"commanded {deg} deg is within 5 deg of the {e // 1000} deg edge")
    return _zone(deg * 1000)


def zone_of_mdeg(mdeg: int, what: str = "commanded") -> str:
    """Like ``commanded_zone`` for an integer millidegree angle (T-16: the servo-mapped command)."""
    if type(mdeg) is not int or not 30_000 <= mdeg <= 150_000:
        raise CantVerify(COMMAND_RANGE, f"{what} {mdeg / 1000:.1f} deg is outside 30-150")
    e = _in_band(mdeg)
    if e is not None:
        raise CantVerify(
            COMMAND_BOUNDARY, f"{what} {mdeg / 1000:.1f} deg is within 5 deg of the {e // 1000} deg edge"
        )
    return _zone(mdeg)


@dataclass(frozen=True)
class Judgement:
    verdict: str  # PASS | FAIL (CAN'T VERIFY is raised)
    reason: str
    commanded_zone: str
    measured_zone: str  # "" when out of scale


def judge(commanded_deg: int, measured_mdeg: int) -> Judgement:
    cz = commanded_zone(commanded_deg)
    if type(measured_mdeg) is not int:
        raise TypeError("measured angle must be integer millidegrees")
    if measured_mdeg < SCALE_MDEG[0] or measured_mdeg > SCALE_MDEG[1]:
        return Judgement(FAIL, OUT_OF_SCALE, cz, "")
    e = _in_band(measured_mdeg)
    if e is not None:
        raise CantVerify(BOUNDARY, f"measured {measured_mdeg / 1000:.1f} deg, within 5 deg of {e // 1000}")
    mz = _zone(measured_mdeg)
    return Judgement(PASS, ZONE_MATCH, cz, mz) if mz == cz else Judgement(FAIL, WRONG_ZONE, cz, mz)
