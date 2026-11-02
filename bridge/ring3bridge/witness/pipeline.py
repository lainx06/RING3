"""One witness check, end to end (T-07 Part A).

Part B adds capture, the ``witness`` log record and the run loop.

Order (cheap and offline first, so a check that can't succeed spends nothing):
  commanded zone -> calibration enabled -> markers + crop -> SAM (one image reserved) -> arm angle
  -> calibrated angle -> verdict.
The first ``CantVerify`` ends the check: nothing after it runs, so it is never upgraded. ``SamStop`` and
``BudgetRefused`` are not caught: they stop the run (INV-09).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from ring3bridge.witness import CANT_VERIFY, CantVerify
from ring3bridge.witness.angle import DEFAULT_ARM, ArmModel, ArmReading, arm_angle
from ring3bridge.witness.calibrate import Calibration
from ring3bridge.witness.dial import Geometry, encode_jpeg, find_dial
from ring3bridge.witness.sam import DEFAULT_PROMPT, SamClient
from ring3bridge.witness.verdict import commanded_zone, judge


@dataclass(frozen=True)
class WitnessResult:
    verdict: str  # PASS | FAIL | CAN'T VERIFY
    reason: str
    detail: str
    commanded_deg: int
    commanded_zone: str
    measured_zone: str
    dial_mdeg: int | None
    calibrated_mdeg: int | None
    image_sha256: str  # cropped JPEG sent to SAM ("" when none was sent)
    sam_line: str  # raw SAM line ("" when none)
    spend_uusd: int


@dataclass
class Evidence:
    """What a check gathered so far (kept for the log even when it ends in CAN'T VERIFY)."""

    image_sha256: str = ""
    sam_line: str = ""
    spend_uusd: int = 0
    reading: ArmReading | None = field(default=None)


def measure(
    img: np.ndarray, geom: Geometry, sam: SamClient, arm: ArmModel, prompt: str, ev: Evidence
) -> ArmReading:
    """Markers -> crop -> SAM -> dial angle (no calibration; ``ring3 calibrate`` uses this in Part B)."""
    dial = find_dial(img, geom)
    jpeg = encode_jpeg(dial.crop_img)
    ev.image_sha256 = hashlib.sha256(jpeg).hexdigest()
    w, h = geom.crop.size
    res = sam.segment(jpeg, w, h, prompt)
    ev.sam_line, ev.spend_uusd = res.line, res.uusd
    ev.reading = arm_angle(res.records, geom.crop, arm)
    return ev.reading


def check(
    img: np.ndarray,
    commanded_deg: int,
    calibration: Calibration,
    geom: Geometry,
    sam: SamClient,
    arm: ArmModel = DEFAULT_ARM,
    prompt: str = DEFAULT_PROMPT,
) -> WitnessResult:
    ev = Evidence()
    cz = ""
    try:
        cz = commanded_zone(commanded_deg)
        calibration.to_command_mdeg(0)  # disabled or missing calibration -> CAN'T VERIFY before any spend
        reading = measure(img, geom, sam, arm, prompt, ev)
        cal = calibration.to_command_mdeg(reading.dial_mdeg)
        j = judge(commanded_deg, cal)
    except CantVerify as e:
        dial = ev.reading.dial_mdeg if ev.reading else None
        return WitnessResult(CANT_VERIFY, e.reason, e.detail, commanded_deg, cz, "", dial, None,
                             ev.image_sha256, ev.sam_line, ev.spend_uusd)  # fmt: skip
    return WitnessResult(j.verdict, j.reason, "", commanded_deg, j.commanded_zone, j.measured_zone,
                         reading.dial_mdeg, cal, ev.image_sha256, ev.sam_line, ev.spend_uusd)  # fmt: skip
