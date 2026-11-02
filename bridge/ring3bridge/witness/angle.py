"""Arm angle from SAM boxes (T-07 step 4, D-58).

Boxes arrive in crop pixels and are mapped to gauge mm (axis-aligned stays axis-aligned: the crop is a pure
scale + flip of the gauge frame). A box is a plausible arm when

  * the pivot lies inside it (within ``pivot_tol_mm``) and one of its edges is near the pivot
    (``near_pivot_mm``: the horn's hub surrounds the shaft), and
  * its far side is near the arm's radius: the farthest corner from the pivot is between
    ``len_lo`` x length and ``len_hi`` x length + ``len_extra_mm``, and
  * its centre is clearly away from the pivot (else the direction is undefined).

Exactly one plausible record is required: none and no records at all -> NOT_FOUND; records but none plausible
-> SHAPE; two or more plausible (overlapping or not) -> AMBIGUOUS. Confidence never removes a candidate (so a
low-confidence ghost can't be dropped to make the answer unique); it only blocks the one candidate:
missing or below ``min_conf_ppm`` -> LOW_CONFIDENCE.

Angle = direction from the pivot **through the box centre** (not the farthest corner, which jumps by the arm's
half-width around 90 deg). Returned as integer millidegrees, counter-clockwise from +x, in (-180000, 180000].
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ring3bridge.witness import AMBIGUOUS, LOW_CONFIDENCE, NOT_FOUND, SHAPE, CantVerify
from ring3bridge.witness.dial import Crop
from ring3bridge.witness.sam import SamRecord


@dataclass(frozen=True)
class ArmModel:
    """SG90 single long horn. Every value (prov.): measured on the real horn in Part B."""

    length_mm: float = 18.0  # pivot to tip
    pivot_tol_mm: float = 3.0
    near_pivot_mm: float = 8.0
    len_lo: float = 0.7
    len_hi: float = 1.45
    len_extra_mm: float = 4.0
    min_centre_mm: float = 4.0
    min_conf_ppm: int = 500_000
    require_conf: bool = True


@dataclass(frozen=True)
class ArmReading:
    dial_mdeg: int
    box_mm: tuple[float, float, float, float]  # x0, y0, x1, y1 (y up)
    conf_ppm: int
    obj_id: int


def box_mm(r: SamRecord, crop: Crop) -> tuple[float, float, float, float]:
    """Box edges are taken as pixel-edge coordinates (pixel u spans [u, u+1), x2/y2 exclusive, w = x2 - x1);
    the crop maps pixel *centres*, hence the half-pixel. SAM's exact convention is [UNVERIFIED] (0.1 mm)."""
    xa, ya = crop.px_to_mm(r.x1 - 0.5, r.y1 - 0.5)
    xb, yb = crop.px_to_mm(r.x2 - 0.5, r.y2 - 0.5)
    return (min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb))


def shape_problem(b: tuple[float, float, float, float], arm: ArmModel) -> str | None:
    """None when the box is shaped like the arm on this pivot, else why not."""
    x0, y0, x1, y1 = b
    t = arm.pivot_tol_mm
    if not (x0 - t <= 0.0 <= x1 + t and y0 - t <= 0.0 <= y1 + t):
        return "pivot not inside the box"
    if min(abs(x0), abs(x1), abs(y0), abs(y1)) > arm.near_pivot_mm:
        return "no edge near the pivot"
    far = max(math.hypot(x, y) for x in (x0, x1) for y in (y0, y1))
    lo, hi = arm.len_lo * arm.length_mm, arm.len_hi * arm.length_mm + arm.len_extra_mm
    if not lo <= far <= hi:
        return f"far side {far:.1f} mm, arm radius {lo:.1f}-{hi:.1f} mm"
    if math.hypot((x0 + x1) / 2, (y0 + y1) / 2) < arm.min_centre_mm:
        return "box centred on the pivot"
    return None


DEFAULT_ARM = ArmModel()


def arm_angle(records: list[SamRecord], crop: Crop, arm: ArmModel = DEFAULT_ARM) -> ArmReading:
    if not records:
        raise CantVerify(NOT_FOUND, "SAM found no arm")
    plausible = []
    why = []
    for r in records:
        b = box_mm(r, crop)
        p = shape_problem(b, arm)
        if p is None:
            plausible.append((r, b))
        else:
            why.append(f"{r.obj_id}: {p}")
    if not plausible:
        raise CantVerify(SHAPE, "; ".join(why))
    if len(plausible) > 1:
        raise CantVerify(AMBIGUOUS, f"{len(plausible)} arm-shaped records")
    r, b = plausible[0]
    conf = r.c_ppm if r.c_ppm is not None else r.mask_c_ppm
    if conf is None:
        if arm.require_conf:
            raise CantVerify(LOW_CONFIDENCE, "no confidence in the record")
        conf = 0
    elif conf < arm.min_conf_ppm:
        raise CantVerify(LOW_CONFIDENCE, f"confidence {conf / 1e6:.2f} below {arm.min_conf_ppm / 1e6:.2f}")
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    mdeg = int(round(math.degrees(math.atan2(cy, cx)) * 1000))
    if mdeg <= -180000:
        mdeg += 360000
    return ArmReading(mdeg, b, conf, r.obj_id)
