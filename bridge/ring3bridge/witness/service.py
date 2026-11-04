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

import cv2
import numpy as np

from ring3bridge.witness import CANT_VERIFY, NO_MOVE, CantVerify
from ring3bridge.witness.angle import DEFAULT_ARM, ArmModel
from ring3bridge.witness.calibrate import Calibration
from ring3bridge.witness.capture import REQUEST_TIMEOUT_S, request_frame
from ring3bridge.witness.dial import Crop, Geometry, find_dial
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
HUB_OK_MM = 0.5  # D-79: shaft within this of the PIVOT mark (1 mm costs ~2.6 deg after the calibration fit)
PIVOT_ZOOM = Crop(-30.0, 30.0, -12.0, 30.0, 20)  # pivot.jpg: gauge mm, 20 px/mm, dial on top
HOLE_R_MM = (0.5, 2.0)  # thresholded core of the hub's dark screw hole = the shaft centre (D-79)
HUB_SEARCH_MM = 6.0  # hole centre must be this close to the printed pivot
HOLE_MIN_CIRCULARITY = 0.6
HOLE_RING_MM = (0.5, 2.0)  # the ring just outside the hole ...
HOLE_RING_WHITE = 0.70  # ... must be mostly white plastic (rules out dark gaps at the edge of the horn)


@dataclass(frozen=True)
class HubReading:
    dx_mm: float  # cap centre - printed pivot, gauge x (right in pivot.jpg)
    dy_mm: float  # gauge y (up in pivot.jpg, toward the dial)
    r_mm: float
    centre_px: tuple[float, float]  # in the pivot zoom

    @property
    def dist_mm(self) -> float:
        return float(np.hypot(self.dx_mm, self.dy_mm))

    def line(self) -> str:
        head = f"hub offset: dx {self.dx_mm:+.1f} mm, dy {self.dy_mm:+.1f} mm, {self.dist_mm:.2f} mm"
        if self.dist_mm <= HUB_OK_MM:
            return head + " OK"
        moves = []
        if abs(self.dx_mm) >= 0.1:
            moves.append(f"{'LEFT' if self.dx_mm > 0 else 'RIGHT'} {abs(self.dx_mm):.1f} mm")
        if abs(self.dy_mm) >= 0.1:
            moves.append(f"{'DOWN' if self.dy_mm > 0 else 'UP'} {abs(self.dy_mm):.1f} mm")
        return head + " MOVE: move servo " + ", ".join(moves) + " (as seen in pivot.jpg, dial on top)"


def find_hub(zoom: np.ndarray) -> tuple[HubReading | None, str]:
    """The shaft centre in the pivot zoom (``PIVOT_ZOOM``): the dark screw hole of the horn hub. Dark spots
    come from a black-hat of the brightness (darker than their surroundings within ~1.5 mm); a candidate must
    be hole-sized, roughly round, within ``HUB_SEARCH_MM`` of the printed pivot, and enclosed by white plastic
    (bright, unsaturated) in the ring just outside it. Exactly one candidate -> its centroid; none or
    several -> None with the reason (never a guess)."""
    s = PIVOT_ZOOM.px_per_mm
    hsv = cv2.cvtColor(zoom, cv2.COLOR_BGR2HSV)
    v, sat = hsv[..., 2], hsv[..., 1].astype(np.int32)
    paper = float(np.percentile(v, 95))
    white = (v.astype(np.int32) >= 0.75 * paper) & (sat <= 60)
    k = int(round(3 * s)) | 1
    hat = cv2.morphologyEx(v, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    dark = (hat >= 40).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(dark)
    px0, py0 = PIVOT_ZOOM.mm_to_px(0.0, 0.0)
    a_lo, a_hi = (np.pi * (r * s) ** 2 for r in HOLE_R_MM)
    yy, xx = np.mgrid[0 : zoom.shape[0], 0 : zoom.shape[1]]
    found = []
    for i in range(1, n):
        area = st[i, cv2.CC_STAT_AREA]
        if not a_lo <= area <= a_hi:
            continue
        cx, cy = cen[i]
        if np.hypot(cx - px0, cy - py0) > HUB_SEARCH_MM * s:
            continue
        cnts, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = cv2.arcLength(cnts[0], True)
        if not per or 4 * np.pi * area / (per * per) < HOLE_MIN_CIRCULARITY:
            continue
        r = np.sqrt(area / np.pi)
        d = np.hypot(xx - cx, yy - cy)
        ring = (d >= r + HOLE_RING_MM[0] * s) & (d <= r + HOLE_RING_MM[1] * s)
        if ring.any() and white[ring].mean() >= HOLE_RING_WHITE:
            found.append((cx, cy, r / s))
    if len(found) != 1:
        return None, "no dark screw hole in a white hub near the PIVOT mark" if not found else (
            f"{len(found)} candidates")  # fmt: skip
    cx, cy, r = found[0]
    x_mm, y_mm = PIVOT_ZOOM.px_to_mm(cx, cy)
    return HubReading(x_mm, y_mm, r, (cx, cy)), ""


def mark_hub(zoom: np.ndarray, hub: HubReading | None) -> np.ndarray:
    """pivot.jpg: a green ring on the printed pivot, the detected cap centre in red."""
    out = zoom.copy()
    s = PIVOT_ZOOM.px_per_mm
    px0, py0 = (round(v) for v in PIVOT_ZOOM.mm_to_px(0.0, 0.0))
    cv2.circle(out, (px0, py0), round(HUB_OK_MM * s), (0, 200, 0), 1)
    if hub is not None:
        cx, cy = (round(v) for v in hub.centre_px)
        cv2.circle(out, (cx, cy), round(hub.r_mm * s), (0, 0, 255), 1)
        cv2.drawMarker(out, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 12, 2)
    return out


@dataclass(frozen=True)
class FrameReport:
    """Setup check for one frame (``ring3 witness-frame``): no SAM, no spend."""

    ok: bool
    lines: list[str]
    crop_img: np.ndarray | None = None
    pivot_img: np.ndarray | None = None  # 20 px/mm zoom around the pivot, hub marked (D-79)
    hub: HubReading | None = None


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
    mm_to_img = np.linalg.inv(dial.img_to_mm)
    p0, p1 = (mm_to_img @ np.array([0.0, 0.0, 1.0])), (mm_to_img @ np.array([10.0, 0.0, 1.0]))
    px_mm = float(np.hypot(p1[0] / p1[2] - p0[0] / p0[2], p1[1] / p1[2] - p0[1] / p0[2])) / 10
    zoom = find_dial(img, Geometry(geom.marker_corners, PIVOT_ZOOM)).crop_img
    hub, why = find_hub(zoom)
    lines += [
        f"markers 0-3 found, worst marker corner fit {dial.reproj_mm_max:.2f} mm",
        f"image centre is over gauge point x={x:+.1f} y={y:+.1f} mm, {off:.1f} mm from the pivot "
        + (
            "(ok)"
            if axis_ok
            else f"(move the camera so the pivot is under the image centre, <= {AXIS_OK_MM:g} mm)"
        ),
        f"camera resolves {px_mm:.2f} px/mm at the gauge: the 15 mm horn spans ~{15 * px_mm:.0f} px "
        "(look at pivot.jpg: the horn must stand out from the grey dial)",
        hub.line() if hub is not None else f"hub offset: not found ({why})",
    ]
    hub_ok = hub is not None and hub.dist_mm <= HUB_OK_MM
    return FrameReport(axis_ok and hub_ok, lines, dial.crop_img, mark_hub(zoom, hub), hub)


class FolderCamera:
    """One fresh still per call through the shared folder (``capture.request_frame``)."""

    def __init__(self, folder: Path, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
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
