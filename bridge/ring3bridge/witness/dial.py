"""Dial frame (T-07 step 2): 4 ArUco markers in *this* frame -> homography to gauge mm -> dial warp-crop.

No stored reference frame: every check finds all four markers (DICT_4X4_50, IDs 0-3) again, so a nudged camera
or paper still works and a covered marker is ``CAN'T VERIFY: MARKERS``. The 16 marker corners give a
least-squares homography; a fit whose worst corner is off by more than ``MAX_REPROJ_MM`` is rejected.

Crop coordinates: pixel (u, v) of the crop is gauge point x = x_min + u / s, y = y_max - v / s (y up on the
paper, v down in the image), with s = ``crop.px_per_mm`` from ``hardware/gauge/geometry.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ring3bridge.config import REPO
from ring3bridge.witness import MARKERS, CantVerify

GEOMETRY_JSON = REPO / "hardware" / "gauge" / "geometry.json"
MARKER_IDS = (0, 1, 2, 3)
MAX_REPROJ_MM = 2.0  # (prov.) worst marker-corner error of the homography fit
JPEG_QUALITY = 90


@dataclass(frozen=True)
class Crop:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    px_per_mm: int

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) in pixels."""
        s = self.px_per_mm
        return (int(round((self.x_max - self.x_min) * s)), int(round((self.y_max - self.y_min) * s)))

    def px_to_mm(self, u: float, v: float) -> tuple[float, float]:
        s = self.px_per_mm
        return (self.x_min + u / s, self.y_max - v / s)

    def mm_to_px(self, x: float, y: float) -> tuple[float, float]:
        s = self.px_per_mm
        return ((x - self.x_min) * s, (self.y_max - y) * s)

    def matrix(self) -> np.ndarray:
        """3x3: gauge mm -> crop px."""
        s = float(self.px_per_mm)
        return np.array([[s, 0.0, -self.x_min * s], [0.0, -s, self.y_max * s], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class Geometry:
    marker_corners: dict[int, np.ndarray]  # id -> (4, 2) gauge mm, ArUco corner order
    crop: Crop


def load_geometry(path: Path = GEOMETRY_JSON) -> Geometry:
    d = json.loads(Path(path).read_text("ascii"))
    if d.get("version") != 1 or d.get("units") != "mm" or d["markers"]["dictionary"] != "DICT_4X4_50":
        raise ValueError("unsupported gauge geometry")
    corners = {}
    for mid in MARKER_IDS:
        a = np.array(d["markers"]["corners"][str(mid)], np.float64)
        if a.shape != (4, 2) or not np.all(np.isfinite(a)):
            raise ValueError(f"marker {mid}: bad corners")
        corners[mid] = a
    c = d["crop"]
    crop = Crop(
        float(c["x_min"]), float(c["x_max"]), float(c["y_min"]), float(c["y_max"]), int(c["px_per_mm"])
    )
    if not (crop.x_min < 0 < crop.x_max and crop.y_min < 0 < crop.y_max and 1 <= crop.px_per_mm <= 20):
        raise ValueError("crop must contain the pivot")
    return Geometry(corners, crop)


@dataclass(frozen=True)
class DialFrame:
    crop_img: np.ndarray  # warp-cropped dial (BGR or gray, like the input)
    crop: Crop
    img_to_mm: np.ndarray  # 3x3 homography, camera px -> gauge mm
    reproj_mm_max: float


def _detector() -> cv2.aruco.ArucoDetector:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params)


def detect_markers(img: np.ndarray) -> dict[int, np.ndarray]:
    """IDs 0-3 found exactly once -> (4, 2) image corners. Duplicates of 0-3 or a missing ID -> MARKERS."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector().detectMarkers(gray)
    found: dict[int, np.ndarray] = {}
    dup = set()
    for c, i in zip(corners, [] if ids is None else ids.flatten().tolist(), strict=True):
        if i not in MARKER_IDS:
            continue
        if i in found:
            dup.add(i)
        found[i] = c.reshape(4, 2).astype(np.float64)
    if dup:
        raise CantVerify(MARKERS, f"duplicate marker id {sorted(dup)}")
    if len(found) != len(MARKER_IDS):
        missing = [i for i in MARKER_IDS if i not in found]
        raise CantVerify(MARKERS, f"found {len(found)} of 4, missing id {missing}")
    return found


def _apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(pts.reshape(-1, 1, 2), h).reshape(-1, 2)


def find_dial(img: np.ndarray, geom: Geometry) -> DialFrame:
    found = detect_markers(img)
    src = np.vstack([found[i] for i in MARKER_IDS])
    dst = np.vstack([geom.marker_corners[i] for i in MARKER_IDS])
    h, _ = cv2.findHomography(src, dst, 0)
    if h is None or not np.all(np.isfinite(h)):
        raise CantVerify(MARKERS, "no homography")
    err = float(np.max(np.linalg.norm(_apply(h, src) - dst, axis=1)))
    if err > MAX_REPROJ_MM:
        raise CantVerify(MARKERS, f"marker fit off by {err:.1f} mm")
    m = geom.crop.matrix() @ h  # camera px -> crop px
    w, hh = geom.crop.size
    back = _apply(np.linalg.inv(m), np.array([[0, 0], [w, 0], [w, hh], [0, hh]], np.float64))
    ih, iw = img.shape[:2]
    if np.any(back < -1) or np.any(back[:, 0] > iw + 1) or np.any(back[:, 1] > ih + 1):
        raise CantVerify(MARKERS, "dial region not fully in the frame")
    crop_img = cv2.warpPerspective(img, m, (w, hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return DialFrame(crop_img, geom.crop, h, err)


def encode_jpeg(img: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()
