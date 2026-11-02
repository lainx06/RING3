"""RING3 gauge generator (T-07 Part A, D-22, D-24, D-56).

Writes, next to this file:
  ring3_gauge.pdf     US Letter, print at 100 % ("actual size")
  ring3_gauge_a4.pdf  A4, same drawing (every position is relative to the pivot, so both share one geometry)
  ring3_gauge.png     US Letter at 300 dpi (preview)
  geometry.json       the gauge frame the witness uses (pivot, zones, bands, marker corners, crop) in mm

Gauge frame: millimetres, origin at the servo pivot, +x right, +y up the page; dial angles are
counter-clockwise from +x, so 90 deg points straight up. The servo lies flat on the paper with its shaft over
the printed pivot (no cutting or holes, D-24); its single long horn is the arm.

Run: ``uv run python hardware/gauge/make_gauge.py`` (deterministic: same bytes every run).
"""

from __future__ import annotations

import json
import math
import zlib
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent

# ---- geometry (mm, gauge frame) ----
R_INTERIOR = 50.0  # dark disc sector behind the arm (contrast for a white horn)
R_RING_IN = 56.0
R_RING_OUT = 80.0  # dial radius (card: >= 70 mm)
R_TICK_LABEL = 86.0
ZONES = (("LOW", 30, 70), ("MID", 70, 110), ("HIGH", 110, 150))  # kernel servo range 30-150 deg, 40 deg each
EDGES = (30, 70, 110, 150)
BAND_DEG = 5  # boundary band half-width (prov.); verdict.py holds the same value and a test compares them
SCALE_DEG = (25, 155)  # below / above: out of scale
MARKER_DICT = "DICT_4X4_50"
MARKER_MM = 30.0  # card: >= 30 mm
MARKER_BITS = 4
# bottom-left corner of each marker (id -> (x, y)); markers sit upright on the page
MARKER_ORIGIN = {0: (-93.0, 70.0), 1: (63.0, 70.0), 2: (63.0, -42.0), 3: (-93.0, -42.0)}
CROP = {"x_min": -92.0, "x_max": 92.0, "y_min": -22.0, "y_max": 92.0, "px_per_mm": 5}
SERVO_BODY = (-6.1, -17.0, 6.1, 5.5)  # SG90 footprint (approx.), shaft over the pivot, body pointing down
CONTENT_Y = (-78.0, 124.0)  # vertical extent of everything drawn (for centring on the page)

PAPER_MM = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}
PDF_DPI = 600
PNG_DPI = 300

INK = 0
PAPER = 255
ZONE_FILL = {"LOW": 150, "MID": 120, "HIGH": 150}
INTERIOR = 135
BAND = 25


def marker_corners(mid: int) -> list[list[float]]:
    """ArUco corner order (TL, TR, BR, BL of the upright marker) in gauge mm."""
    x, y = MARKER_ORIGIN[mid]
    s = MARKER_MM
    return [[x, y + s], [x + s, y + s], [x + s, y], [x, y]]


def pivot_on_page(paper: str) -> tuple[float, float]:
    """Pivot position from the page's top-left corner (x right, y down), mm."""
    w, h = PAPER_MM[paper]
    mid = (CONTENT_Y[0] + CONTENT_Y[1]) / 2
    return (w / 2, h / 2 + mid)


def geometry() -> dict:
    return {
        "version": 1,
        "units": "mm",
        "frame": "origin at the servo pivot; +x right, +y up the page; angles counter-clockwise from +x",
        "pivot": [0.0, 0.0],
        "dial": {"r_interior": R_INTERIOR, "r_ring_inner": R_RING_IN, "r_ring_outer": R_RING_OUT},
        "zones": [{"name": n, "lo_deg": lo, "hi_deg": hi} for n, lo, hi in ZONES],
        "band_half_width_deg": BAND_DEG,
        "scale_deg": list(SCALE_DEG),
        "markers": {
            "dictionary": MARKER_DICT,
            "size_mm": MARKER_MM,
            "corner_order": "TL,TR,BR,BL of the upright marker",
            "corners": {str(i): marker_corners(i) for i in sorted(MARKER_ORIGIN)},
        },
        "crop": dict(CROP),
        "servo_body_mm": list(SERVO_BODY),
        "pivot_on_page_mm": {p: [round(v, 2) for v in pivot_on_page(p)] for p in sorted(PAPER_MM)},
    }


# ---- raster renderer (also used by the synthetic fixtures in tests/witness) ----
SHIFT = 4
ONE = 1 << SHIFT


class Canvas:
    """Gray raster of a rectangle of the gauge frame: region (x0, x1, y0, y1) mm at ``s`` px/mm."""

    def __init__(self, s: float, region: tuple[float, float, float, float], fill: int = PAPER) -> None:
        self.s = s
        self.x0, self.x1, self.y0, self.y1 = region
        w = int(round((self.x1 - self.x0) * s))
        h = int(round((self.y1 - self.y0) * s))
        self.img = np.full((h, w), fill, np.uint8)

    def px(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) * self.s, (self.y1 - y) * self.s)

    def _fx(self, x: float, y: float) -> tuple[int, int]:
        u, v = self.px(x, y)
        return (int(round(u * ONE)), int(round(v * ONE)))

    def poly(self, pts, color: int) -> None:
        a = np.array([self._fx(x, y) for x, y in pts], np.int32)
        cv2.fillPoly(self.img, [a], color, cv2.LINE_AA, SHIFT)

    def line(self, p, q, color: int, width_mm: float) -> None:
        t = max(1, int(round(width_mm * self.s)))
        cv2.line(self.img, self._fx(*p), self._fx(*q), color, t, cv2.LINE_AA, SHIFT)

    def circle(self, c, r_mm: float, color: int, width_mm: float | None = None) -> None:
        t = -1 if width_mm is None else max(1, int(round(width_mm * self.s)))
        cv2.circle(self.img, self._fx(*c), int(round(r_mm * self.s * ONE)), color, t, cv2.LINE_AA, SHIFT)

    def text(self, s: str, center, height_mm: float, color: int, bold: bool = False) -> None:
        font = cv2.FONT_HERSHEY_DUPLEX if bold else cv2.FONT_HERSHEY_SIMPLEX
        scale = height_mm * self.s / 22.0
        thick = max(1, int(round(scale * (2.2 if bold else 1.4))))
        (tw, th), _ = cv2.getTextSize(s, font, scale, thick)
        u, v = self.px(*center)
        org = (int(round(u - tw / 2)), int(round(v + th / 2)))
        cv2.putText(self.img, s, org, font, scale, color, thick, cv2.LINE_AA)

    def blit(self, tile: np.ndarray, x: float, y_top: float, size_mm: float) -> None:
        """Paste a square tile with its top-left corner at (x, y_top), resized with nearest neighbour.
        Pixel centres sit at integer coordinates, so an edge at coordinate e lies before index e + 0.5."""
        u0, v0 = self.px(x, y_top)
        u1, v1 = self.px(x + size_mm, y_top - size_mm)
        u0, v0, u1, v1 = (int(round(t + 0.5)) for t in (u0, v0, u1, v1))
        self.img[v0:v1, u0:u1] = cv2.resize(tile, (u1 - u0, v1 - v0), interpolation=cv2.INTER_NEAREST)


def sector(r_in: float, r_out: float, a0: float, a1: float, step: float = 0.25) -> list[tuple[float, float]]:
    n = max(2, int(math.ceil((a1 - a0) / step)) + 1)
    angs = [math.radians(a0 + (a1 - a0) * i / (n - 1)) for i in range(n)]
    outer = [(r_out * math.cos(a), r_out * math.sin(a)) for a in angs]
    inner = [(r_in * math.cos(a), r_in * math.sin(a)) for a in reversed(angs)] if r_in > 0 else [(0.0, 0.0)]
    return outer + inner


def polar(r: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return (r * math.cos(a), r * math.sin(a))


def draw_gauge(c: Canvas) -> None:
    # dark interior sector behind the arm, thin rays at the zone edges (outside the arm's reach)
    c.poly(sector(0.0, R_INTERIOR, 0, 180), INTERIOR)
    for e in EDGES:
        c.line(polar(24.0, e), polar(R_INTERIOR, e), 70, 0.5)
    # zone ring, out-of-scale ends, boundary bands
    for name, lo, hi in ZONES:
        c.poly(sector(R_RING_IN, R_RING_OUT, lo, hi), ZONE_FILL[name])
    for lo, hi in ((0, SCALE_DEG[0]), (SCALE_DEG[1], 180)):
        c.poly(sector(R_RING_IN, R_RING_OUT, lo, hi), 225)
        c.text("OUT", polar((R_RING_IN + R_RING_OUT) / 2, (lo + hi) / 2), 3.2, INK)
    for e in EDGES:
        c.poly(sector(R_RING_IN, R_RING_OUT, e - BAND_DEG, e + BAND_DEG), BAND)
        c.line(polar(R_RING_IN, e), polar(R_RING_OUT, e), PAPER, 0.35)
    for name, lo, hi in ZONES:
        c.text(name, polar((R_RING_IN + R_RING_OUT) / 2, (lo + hi) / 2), 6.0, PAPER, bold=True)
    arc = [polar(R_RING_OUT, a / 4) for a in range(0, 721)]
    for p, q in zip(arc, arc[1:], strict=False):
        c.line(p, q, INK, 0.4)
    # ticks every 10 deg, labels every 20 deg from 30 to 150 plus 0 and 180
    for d in range(0, 181, 10):
        c.line(polar(R_RING_OUT, d), polar(R_RING_OUT + (3.5 if d % 30 == 0 else 2.0), d), INK, 0.35)
    for d in (0, 30, 50, 70, 90, 110, 130, 150, 180):
        c.text(str(d), polar(R_TICK_LABEL + 1.5, d), 3.0, INK)
    # servo body outline (dashed) and the pivot
    x0, y0, x1, y1 = SERVO_BODY
    for p, q in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 2.0))
        for i in range(0, n, 2):
            a, b = i / n, min(1.0, (i + 1) / n)
            pa = (p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a)
            pb = (p[0] + (q[0] - p[0]) * b, p[1] + (q[1] - p[1]) * b)
            c.line(pa, pb, INK, 0.3)
    c.text("SG90 body (approx.)", (0.0, -21.0), 2.6, INK)
    c.line((-9.0, 0.0), (9.0, 0.0), PAPER, 0.5)
    c.line((0.0, -9.0), (0.0, 9.0), PAPER, 0.5)
    c.circle((0, 0), 2.0, PAPER, 0.5)
    c.line((-9.0, 0.0), (9.0, 0.0), INK, 0.2)
    c.line((0.0, -9.0), (0.0, 9.0), INK, 0.2)
    c.text("PIVOT: servo shaft here", (-36.0, -6.0), 2.6, INK)
    # ArUco markers, each on a white quiet zone
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for mid, (x, y) in MARKER_ORIGIN.items():
        tile = cv2.aruco.generateImageMarker(d, mid, (MARKER_BITS + 2) * 10, borderBits=1)
        c.poly([(x - 4, y - 4), (x + MARKER_MM + 4, y - 4), (x + MARKER_MM + 4, y + MARKER_MM + 4),
                (x - 4, y + MARKER_MM + 4)], PAPER)  # fmt: skip
        c.blit(tile, x, y + MARKER_MM, MARKER_MM)
        c.text(f"ID {mid}", (x + MARKER_MM / 2, y - 6.5 if y < 0 else y + MARKER_MM + 5.5), 2.4, INK)
    # title, scale bar, instructions
    c.text("RING3", (0.0, 117.0), 11.0, INK, bold=True)
    c.text("camera witness gauge  -  print at 100 % (actual size)", (0.0, 106.0), 3.0, INK)
    yb = -52.0
    c.line((-50.0, yb), (50.0, yb), INK, 0.4)
    for i in range(11):
        c.line((-50.0 + 10 * i, yb), (-50.0 + 10 * i, yb + (3.0 if i % 5 == 0 else 1.8)), INK, 0.3)
    c.text("100 mm: check with a ruler before use", (0.0, yb - 4.5), 2.6, INK)
    notes = (
        "Servo flat on the paper, shaft over PIVOT, body over the dashed outline. Long single horn = arm.",
        "Keep all four markers visible and flat. No cutting, no holes.",
        "Zones follow the kernel servo range 30-150 deg. Black bands = boundary: CAN'T VERIFY there.",
    )
    for i, line in enumerate(notes):
        c.text(line, (0.0, -63.0 - 5.0 * i), 2.5, INK)


def render(s: float, region: tuple[float, float, float, float]) -> np.ndarray:
    c = Canvas(s, region)
    draw_gauge(c)
    return c.img


def page_region(paper: str) -> tuple[float, float, float, float]:
    w, h = PAPER_MM[paper]
    px, py = pivot_on_page(paper)
    return (-px, w - px, py - h, py)


def render_page(paper: str, dpi: int) -> np.ndarray:
    return render(dpi / 25.4, page_region(paper))


def pdf_bytes(gray: np.ndarray, paper: str) -> bytes:
    """One-page PDF: the gray raster drawn over the whole page (so 100 % print = true scale)."""
    w_mm, h_mm = PAPER_MM[paper]
    wpt, hpt = (f"{v * 72 / 25.4:.3f}" for v in (w_mm, h_mm))
    h, w = gray.shape
    img = zlib.compress(np.ascontiguousarray(gray).tobytes(), 9)
    content = f"q {wpt} 0 0 {hpt} 0 0 cm /Im0 Do Q\n".encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R /ViewerPreferences << /PrintScaling /None >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {wpt} {hpt}] /Resources << /XObject << /Im0 4 0 R >> >>"
        f" /Contents 5 0 R >>".encode("ascii"),
        f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray"
        f" /BitsPerComponent 8 /Filter /FlateDecode /Length {len(img)} >>\nstream\n".encode("ascii")
        + img
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream",
        b"<< /Title (RING3 gauge) /Producer (hardware/gauge/make_gauge.py) >>",
    ]
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii")
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
        "ascii"
    )
    return bytes(out)


def write_all(out_dir: Path = HERE) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "geometry": out_dir / "geometry.json",
        "pdf": out_dir / "ring3_gauge.pdf",
        "pdf_a4": out_dir / "ring3_gauge_a4.pdf",
        "png": out_dir / "ring3_gauge.png",
    }
    paths["geometry"].write_text(json.dumps(geometry(), indent=2, sort_keys=True) + "\n", "ascii")
    paths["pdf"].write_bytes(pdf_bytes(render_page("letter", PDF_DPI), "letter"))
    paths["pdf_a4"].write_bytes(pdf_bytes(render_page("a4", PDF_DPI), "a4"))
    ok, png = cv2.imencode(".png", render_page("letter", PNG_DPI), [cv2.IMWRITE_PNG_COMPRESSION, 9])
    assert ok
    paths["png"].write_bytes(png.tobytes())
    return paths


if __name__ == "__main__":
    for k, p in write_all().items():
        print(f"{k:9s} {p.relative_to(HERE.parents[1])}  {p.stat().st_size} B")
