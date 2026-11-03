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
from ring3bridge.witness.dial import Geometry, find_dial
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


AXIS_OK_MM = 5.0  # (prov.) camera axis within this of the pivot; Part A: 22 mm off moved readings ~10 deg


@dataclass(frozen=True)
class FrameReport:
    """Setup check for one frame (``ring3 witness-frame``): no SAM, no spend."""

    ok: bool
    lines: list[str]
    crop_img: np.ndarray | None = None


def frame_report(img: np.ndarray, geom: Geometry) -> FrameReport:
    """Markers and dial crop in this frame, the marker fit, and the gauge point under the image centre (for a
    camera looking straight down, its axis): the pivot should be under it (parallax, Part A finding 2)."""
    h, w = img.shape[:2]
    lines = [f"frame {w}x{h}"]
    try:
        dial = find_dial(img, geom)
    except CantVerify as e:
        return FrameReport(False, [*lines, f"{e} -> every witness check would be CAN'T VERIFY"])
    c = dial.img_to_mm @ np.array([w / 2, h / 2, 1.0])
    x, y = c[0] / c[2], c[1] / c[2]
    off = float(np.hypot(x, y))
    axis_ok = off <= AXIS_OK_MM
    lines += [
        f"markers 0-3 found, worst marker corner fit {dial.reproj_mm_max:.2f} mm",
        f"image centre is over gauge point x={x:+.1f} y={y:+.1f} mm, {off:.1f} mm from the pivot "
        + (
            "(ok)"
            if axis_ok
            else f"(move the camera so the pivot is under the image centre, <= {AXIS_OK_MM:g} mm)"
        ),
    ]
    return FrameReport(axis_ok, lines, dial.crop_img)


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
