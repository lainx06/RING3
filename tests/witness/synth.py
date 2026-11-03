"""Synthetic camera frames of the printed gauge with a drawn arm, and recorded-style SAM lines (T-07 Part A).

A scene is rendered in the gauge frame (``make_gauge.render`` + an SG90-like white horn), then projected into
a 1280x960 "camera" frame through a known perspective, with blur, uneven light, noise and a JPEG round trip. A
parallel label image (the arm(s) only) goes through the same projection; the fake SAM box is the bounding box
of that label after the *detected* dial warp, so marker detection errors move the box like they would in a
real frame. Everything is deterministic (seeded).
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field

import cv2
import make_gauge
import numpy as np

from ring3bridge.witness.dial import Geometry, find_dial

S = 6.0  # px/mm of the scene canvas
REGION = (-100.0, 100.0, -52.0, 108.0)
CAM = (1280, 960)
ARM_LEN = 13.0  # tip-circle centre; + tip r 2 mm = 15 mm to the outer end (the real horn, T-07 Part B)
DESK = 90

QUADS = {  # scene canvas corners TL, TR, BR, BL -> camera pixels
    "flat": [(140, 80), (1140, 80), (1140, 880), (140, 880)],
    "mild": [(150, 90), (1130, 115), (1150, 880), (125, 860)],
    "steep": [(270, 170), (1010, 150), (1210, 905), (70, 880)],
    "rotated": [(260, 30), (1220, 250), (1050, 940), (60, 720)],
}


@dataclass
class Scene:
    arms_deg: list[float] = field(default_factory=lambda: [90.0])
    quad: str = "mild"
    blur: float = 1.0
    light: str = "even"  # even | gradient | dim
    noise: float = 3.0
    cover_marker: int | None = None
    camera_axis_mm: tuple[float, float] = (0.0, 0.0)  # gauge point under the camera
    parallax_k: float = 1.07  # horn ~27 mm above the paper, camera ~400 mm up: apparent scale about the axis
    seed: int = 1


def _draw_arm(c: make_gauge.Canvas, deg: float, color: int, scene: Scene, shadow: bool = False) -> None:
    """SG90 kit horn (hub r 3.5 mm, tip r 2 mm, 15 mm shaft-to-tip) as the camera sees it: scaled by
    ``parallax_k`` about the camera axis (the horn is above the paper). A shadow falls on the paper, unscaled
    and offset."""
    a = math.radians(deg)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    cx, cy = scene.camera_axis_mm
    k = 1.0 if shadow else scene.parallax_k
    off = (0.8, -0.8) if shadow else (0.0, 0.0)

    def t(x: float, y: float) -> tuple[float, float]:
        return (cx + (x - cx) * k + off[0], cy + (y - cy) * k + off[1])

    tx, ty = ARM_LEN * ux, ARM_LEN * uy
    c.poly([t(2.5 * nx, 2.5 * ny), t(tx + 2.0 * nx, ty + 2.0 * ny), t(tx - 2.0 * nx, ty - 2.0 * ny),
            t(-2.5 * nx, -2.5 * ny)], color)  # fmt: skip
    c.circle(t(0.0, 0.0), 3.5 * k, color)
    c.circle(t(tx, ty), 2.0 * k, color)


def _canvas(fill: int) -> make_gauge.Canvas:
    return make_gauge.Canvas(S, REGION, fill)


def render(scene: Scene) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """-> (camera BGR frame, scene-to-camera homography, per-arm label images in camera pixels)."""
    rng = np.random.default_rng(scene.seed)
    g = _canvas(255)
    make_gauge.draw_gauge(g)
    for d in scene.arms_deg:
        _draw_arm(g, d, 70, scene, shadow=True)
    for d in scene.arms_deg:
        _draw_arm(g, d, 245, scene)
    if scene.cover_marker is not None:
        x, y = make_gauge.MARKER_ORIGIN[scene.cover_marker]
        g.poly([(x + 6, y - 2), (x + 34, y - 2), (x + 34, y + 26), (x + 6, y + 26)], 205)  # a card over it
    h, w = g.img.shape
    src = np.array([(0, 0), (w, 0), (w, h), (0, h)], np.float32)
    hm = cv2.getPerspectiveTransform(src, np.array(QUADS[scene.quad], np.float32))
    cam = cv2.warpPerspective(g.img, hm, CAM, flags=cv2.INTER_AREA, borderValue=DESK).astype(np.float32)
    labels = []
    for d in scene.arms_deg:
        lab = _canvas(0)
        _draw_arm(lab, d, 255, scene)
        labels.append(cv2.warpPerspective(lab.img, hm, CAM, flags=cv2.INTER_LINEAR, borderValue=0))
    yy, xx = np.mgrid[0 : CAM[1], 0 : CAM[0]].astype(np.float32)
    if scene.light == "gradient":
        cam *= 0.55 + 0.55 * xx / CAM[0] + 0.1 * np.sin(yy / 140.0)
    elif scene.light == "dim":
        cam = cam * 0.45 + 10
    if scene.blur > 0:
        cam = cv2.GaussianBlur(cam, (0, 0), scene.blur)
    cam += rng.normal(0, scene.noise, cam.shape).astype(np.float32)
    bgr = cv2.cvtColor(np.clip(cam, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    bgr[..., 0] = np.clip(bgr[..., 0].astype(np.int16) + 6, 0, 255).astype(np.uint8)  # slight colour cast
    ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    return cv2.imdecode(jpg, cv2.IMREAD_COLOR), hm, labels


def label_boxes(
    frame: np.ndarray, labels: list[np.ndarray], geom: Geometry
) -> list[tuple[int, int, int, int]]:
    """Each arm's label, warped by the dial frame *detected* in ``frame`` -> crop box (x1, y1, x2, y2)."""
    dial = find_dial(frame, geom)
    m = geom.crop.matrix() @ dial.img_to_mm
    w, h = geom.crop.size
    out = []
    for lab in labels:
        crop = cv2.warpPerspective(lab, m, (w, h), flags=cv2.INTER_LINEAR)
        ys, xs = np.nonzero(crop > 127)
        if len(xs):
            out.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    return out


def mask_payload(w: int, h: int, box: tuple[int, int, int, int]) -> str:
    """A stand-in mask payload: Ascii85 of the packed box raster (the real encoding is undocumented)."""
    m = np.zeros((h, w), np.uint8)
    x1, y1, x2, y2 = box
    m[y1:y2, x1:x2] = 1
    return "a85:" + base64.a85encode(np.packbits(m).tobytes()).decode("ascii")


def sam_line(boxes: list[tuple[int, int, int, int]], geom: Geometry, conf: str = "0.91") -> str:
    w, h = geom.crop.size
    recs = []
    for i, (x1, y1, x2, y2) in enumerate(boxes, 1):
        c = f";c={conf}" if conf else ""
        recs.append(
            f"{i} <|box;x1={x1};y1={y1};x2={x2};y2={y2};w={x2 - x1};h={y2 - y1}{c}|>"
            f"<|mask;x=0;y=0{c};data={h},{w},{mask_payload(w, h, (x1, y1, x2, y2))}|>"
        )
    return ",".join(recs)


def envelope(text: str, status: str = "completed") -> bytes:
    """A Responses API reply holding one output_text (shape per D-55/D-56)."""
    return json.dumps(
        {
            "id": "resp_fake",
            "object": "response",
            "status": status,
            "model": "sam-3.1",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        }
    ).encode()


# ---- running a scene through the real pipeline with a fake SAM ----

OFFSET_DEG = 2.0  # residual mounting offset after aligning the paper by hand: dial angle = command + 2 deg
CAL_CMDS = (60, 70, 90, 110, 120)  # D-72 (= ring3bridge.calibration.CAL_POINTS); was 60/75/90/105/120


def line_for(frame: np.ndarray, labels: list[np.ndarray], geom: Geometry, conf: str = "0.91") -> str:
    """The recorded-style SAM line for this frame ("" when the frame has no arm). Markers missing -> "" too:
    the pipeline must stop before SAM then, and the fake transport fails the test on any post."""
    try:
        return sam_line(label_boxes(frame, labels, geom), geom, conf)
    except Exception:  # noqa: BLE001 - CantVerify(MARKERS): no SAM call expected
        return ""


def run_check(
    scene: Scene, commanded: int, cal, geom: Geometry, tmp, conf: str = "0.91", line: str | None = None
):
    """-> (WitnessResult, FakeTransport, Ledger, SAM line)."""
    from ring3bridge.witness.pipeline import check
    from tests.witness.fakes import make_client, ok

    frame, _, labels = render(scene)
    text = line_for(frame, labels, geom, conf) if line is None else line
    client, t, ledger = make_client(tmp, ok(envelope(text)))
    return check(frame, commanded, cal, geom, client), t, ledger, text


def calibrate(geom: Geometry, tmp, offset_deg: float | None = None, **scene_kw):
    """Simulated calibration: 5 commands, arm at command + offset, each measured through the pipeline."""
    from ring3bridge.witness import CantVerify
    from ring3bridge.witness.angle import ArmModel
    from ring3bridge.witness.calibrate import fit
    from ring3bridge.witness.pipeline import Evidence, measure
    from ring3bridge.witness.sam import DEFAULT_PROMPT
    from tests.witness.fakes import make_client, ok

    off = OFFSET_DEG if offset_deg is None else offset_deg
    pairs = []
    for i, c in enumerate(CAL_CMDS):
        frame, _, labels = render(Scene([c + off], seed=100 + i, **scene_kw))
        d = tmp / f"cal{i}"
        d.mkdir()
        client, _, _ = make_client(d, ok(envelope(line_for(frame, labels, geom))))
        try:  # like ring3 calibrate (pipeline.calibration_point): a CAN'T VERIFY point is a missing point
            pairs.append((c, measure(frame, geom, client, ArmModel(), DEFAULT_PROMPT, Evidence()).dial_mdeg))
        except CantVerify:
            pairs.append((c, None))
    return fit(pairs), pairs
