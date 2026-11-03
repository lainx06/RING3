"""The witness as the run loop uses it (T-07 Part B): take a fresh frame, then check it or measure it.

``Witness.check(commanded_deg)`` is the ``witness`` op; ``Witness.measure(commanded_deg)`` is one
``ring3 calibrate`` point. Both run the cheap offline checks first (commanded zone, calibration enabled),
then capture, then the pipeline, so a check that cannot succeed takes no frame and spends nothing.

The camera is an injected callable returning ``(BGR image, JPEG bytes)`` or raising
``CantVerify(STALE_FRAME)``: ``FolderCamera`` (the Windows helper's shared folder, D-39c, D-58) or a fake in
tests. Whether a kernel-allowed move happened and has settled is the run loop's question (it holds the
link); this module never sees the link and never sends anything to the kernel (INV-14).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ring3bridge.witness import CANT_VERIFY, NO_MOVE, CantVerify
from ring3bridge.witness.angle import DEFAULT_ARM, ArmModel
from ring3bridge.witness.calibrate import Calibration
from ring3bridge.witness.capture import request_frame
from ring3bridge.witness.dial import Geometry
from ring3bridge.witness.pipeline import WitnessResult, calibration_point, check
from ring3bridge.witness.sam import DEFAULT_PROMPT, SamClient
from ring3bridge.witness.verdict import commanded_zone

Camera = Callable[[], tuple[np.ndarray, bytes]]


@dataclass(frozen=True)
class Checked:
    result: WitnessResult
    frame_jpeg: bytes  # the full camera frame as the helper wrote it (b"" when no frame was taken)


def cant_verify(commanded_deg: int, reason: str, detail: str) -> WitnessResult:
    """A CAN'T VERIFY decided before any frame or spend (``NO_MOVE``, ``COMMAND_*``, ``CALIBRATION``, ...)."""
    try:
        cz = commanded_zone(commanded_deg)
    except CantVerify:
        cz = ""
    return WitnessResult(CANT_VERIFY, reason, detail, commanded_deg, cz, "", None, None, "", "", 0)


def no_move(commanded_deg: int, detail: str) -> WitnessResult:
    return cant_verify(commanded_deg, NO_MOVE, detail)


class FolderCamera:
    """One fresh still per call through the shared folder (``capture.request_frame``)."""

    def __init__(self, folder: Path, timeout_s: float = 5.0) -> None:
        self.folder = Path(folder)
        self.timeout_s = timeout_s

    def __call__(self) -> tuple[np.ndarray, bytes]:
        _, img, data = request_frame(self.folder, self.timeout_s)
        return img, data


class Witness:
    def __init__(
        self,
        camera: Camera,
        sam: SamClient,
        calibration: Calibration,
        geom: Geometry,
        arm: ArmModel = DEFAULT_ARM,
        prompt: str = DEFAULT_PROMPT,
    ) -> None:
        self.camera = camera
        self.sam = sam
        self.calibration = calibration
        self.geom = geom
        self.arm = arm
        self.prompt = prompt

    def check(self, commanded_deg: int) -> Checked:
        try:
            commanded_zone(commanded_deg)
            self.calibration.to_command_mdeg(0)  # disabled -> CAN'T VERIFY: CALIBRATION, no frame, no spend
            img, jpeg = self.camera()
        except CantVerify as e:
            return Checked(cant_verify(commanded_deg, e.reason, e.detail), b"")
        return Checked(
            check(img, commanded_deg, self.calibration, self.geom, self.sam, self.arm, self.prompt), jpeg
        )

    def measure(self, commanded_deg: int) -> Checked:
        try:
            img, jpeg = self.camera()
        except CantVerify as e:
            return Checked(cant_verify(commanded_deg, e.reason, e.detail), b"")
        r = calibration_point(img, commanded_deg, self.geom, self.sam, self.arm, self.prompt)
        return Checked(r, jpeg)
