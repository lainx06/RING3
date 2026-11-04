"""One witness check, end to end (T-07 Part A; ``calibration_point`` and the crop JPEG for the log: Part B).

Capture is the caller's (``service.Witness``); the ``witness`` log record and the wait for a settled move are
the run loop's (``ring3bridge.run``), which is outside this package because it holds the link.

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

from ring3bridge.witness import CALIBRATION, CANT_VERIFY, CantVerify
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
    crop_jpeg: bytes = b""  # the cropped JPEG sent to SAM (b"" when none was sent), for the run's blobs
    sam_reply: bytes = b""  # SAM's reply body as received (scrubbed by the transport), for the run's blobs
    backend: str = "sam"  # T-16: "sam" or "vision"
    pack_sha256: str = ""  # vision: SHA-256 of the witness context pack sent as instructions
    arm_visible: int | None = None  # vision: the reply's arm_visible (1/0), None when no reply parsed
    vision_reply: bytes = b""  # vision: the model's reply body as received (scrubbed), for the run's blobs


@dataclass
class Evidence:
    """What a check gathered so far (kept for the log even when it ends in CAN'T VERIFY)."""

    image_sha256: str = ""
    jpeg: bytes = b""
    sam_reply: bytes = b""
    sam_line: str = ""
    spend_uusd: int = 0
    reading: ArmReading | None = field(default=None)


def measure(
    img: np.ndarray, geom: Geometry, sam: SamClient, arm: ArmModel, prompt: str, ev: Evidence
) -> ArmReading:
    """Markers -> crop -> SAM -> dial angle (no calibration; ``ring3 calibrate`` uses this in Part B)."""
    dial = find_dial(img, geom)
    jpeg = encode_jpeg(dial.crop_img)
    ev.image_sha256, ev.jpeg = hashlib.sha256(jpeg).hexdigest(), jpeg
    w, h = geom.crop.size
    try:
        res = sam.segment(jpeg, w, h, prompt)
    finally:  # kept also when the reply does not parse (SAM_FORMAT): the raw reply is the evidence
        ev.sam_reply, ev.spend_uusd = sam.last_reply, sam.last_uusd
    ev.sam_line = res.line
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
                             ev.image_sha256, ev.sam_line, ev.spend_uusd, ev.jpeg, ev.sam_reply)  # fmt: skip
    return WitnessResult(j.verdict, j.reason, "", commanded_deg, j.commanded_zone, j.measured_zone,
                         reading.dial_mdeg, cal, ev.image_sha256, ev.sam_line, ev.spend_uusd,
                         ev.jpeg, ev.sam_reply)  # fmt: skip


def calibration_point(
    img: np.ndarray,
    commanded_deg: int,
    geom: Geometry,
    sam: SamClient,
    arm: ArmModel = DEFAULT_ARM,
    prompt: str = DEFAULT_PROMPT,
) -> WitnessResult:
    """One ``ring3 calibrate`` measurement. There is no calibration yet, so no zone can be judged: the result
    is always ``CAN'T VERIFY: CALIBRATION`` ("calibration point"), with ``dial_mdeg`` set when the markers,
    SAM and the arm shape gave a reading; any other CAN'T VERIFY reason leaves ``dial_mdeg`` None (the fit
    then disables the witness for a missing point)."""
    ev = Evidence()
    try:
        reading = measure(img, geom, sam, arm, prompt, ev)
    except CantVerify as e:
        return WitnessResult(CANT_VERIFY, e.reason, e.detail, commanded_deg, "", "", None, None,
                             ev.image_sha256, ev.sam_line, ev.spend_uusd, ev.jpeg, ev.sam_reply)  # fmt: skip
    return WitnessResult(CANT_VERIFY, CALIBRATION, "calibration point", commanded_deg, "", "",
                         reading.dial_mdeg, None, ev.image_sha256, ev.sam_line, ev.spend_uusd,
                         ev.jpeg, ev.sam_reply)  # fmt: skip
