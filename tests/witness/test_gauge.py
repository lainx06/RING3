"""Gauge generator outputs (T-07 step 1): geometry in sync, markers where geometry.json says, true scale."""

import json
import re
import zlib

import cv2
import make_gauge
import numpy as np
import pytest

from ring3bridge.witness import verdict
from ring3bridge.witness.dial import GEOMETRY_JSON, detect_markers, load_geometry

GAUGE = GEOMETRY_JSON.parent


def test_geometry_json_is_generated_and_in_sync():
    committed = GEOMETRY_JSON.read_text("ascii")
    assert committed == json.dumps(make_gauge.geometry(), indent=2, sort_keys=True) + "\n"
    g = json.loads(committed)
    assert g["dial"]["r_ring_outer"] >= 70
    assert g["markers"]["size_mm"] >= 30 and g["markers"]["dictionary"] == "DICT_4X4_50"
    assert sorted(g["markers"]["corners"]) == ["0", "1", "2", "3"]
    assert [(z["name"], z["lo_deg"], z["hi_deg"]) for z in g["zones"]] == [
        ("LOW", 30, 70), ("MID", 70, 110), ("HIGH", 110, 150)]  # fmt: skip
    load_geometry()  # the witness accepts it


def test_verdict_constants_match_the_printed_gauge():
    g = json.loads(GEOMETRY_JSON.read_text("ascii"))
    assert verdict.ZONES == tuple((z["name"], z["lo_deg"] * 1000, z["hi_deg"] * 1000) for z in g["zones"])
    assert verdict.BAND_MDEG == g["band_half_width_deg"] * 1000
    assert verdict.SCALE_MDEG == tuple(v * 1000 for v in g["scale_deg"])
    assert verdict.EDGES == (30_000, 70_000, 110_000, 150_000)


def test_markers_do_not_overlap_the_dial_or_each_other():
    r_clear = make_gauge.R_TICK_LABEL + 4
    for mid in range(4):
        c = np.array(make_gauge.marker_corners(mid))
        x0, y0 = c.min(axis=0)
        x1, y1 = c.max(axis=0)
        # nearest point of the marker square to the pivot is outside the dial and its labels (upper half)
        nx, ny = np.clip(0, x0, x1), np.clip(0, y0, y1)
        assert ny < 0 or np.hypot(nx, ny) > r_clear, mid


def _page_px_to_mm(paper: str, dpi: int, uv: np.ndarray) -> np.ndarray:
    s = dpi / 25.4
    px, py = make_gauge.pivot_on_page(paper)
    return np.column_stack([uv[:, 0] / s - px, py - uv[:, 1] / s])


def test_png_markers_found_at_their_geometry_positions():
    img = cv2.imread(str(GAUGE / "ring3_gauge.png"), cv2.IMREAD_GRAYSCALE)
    assert img.shape == (3300, 2550)  # Letter at 300 dpi
    found = detect_markers(img)
    geom = load_geometry()
    for mid, corners in found.items():
        mm = _page_px_to_mm("letter", 300, corners + 0.5)  # detector corners are pixel-centre based
        assert np.max(np.abs(mm - geom.marker_corners[mid])) < 0.3, mid


@pytest.mark.parametrize("name,paper", [("ring3_gauge.pdf", "letter"), ("ring3_gauge_a4.pdf", "a4")])
def test_pdf_is_true_scale_and_holds_the_gauge(name, paper):
    data = (GAUGE / name).read_bytes()
    assert data.startswith(b"%PDF-1.6") and data.rstrip().endswith(b"%%EOF")
    assert b"/PrintScaling /None" in data
    w_mm, h_mm = make_gauge.PAPER_MM[paper]
    m = re.search(rb"/MediaBox \[0 0 ([0-9.]+) ([0-9.]+)\]", data)
    assert (
        abs(float(m.group(1)) - w_mm * 72 / 25.4) < 0.01 and abs(float(m.group(2)) - h_mm * 72 / 25.4) < 0.01
    )
    m = re.search(rb"/Width (\d+) /Height (\d+) .*?/Length (\d+) >>\nstream\n", data, re.S)
    w, h, n = (int(v) for v in m.groups())
    assert abs(w - w_mm * 600 / 25.4) <= 1 and abs(h - h_mm * 600 / 25.4) <= 1
    raster = np.frombuffer(zlib.decompress(data[m.end() : m.end() + n]), np.uint8).reshape(h, w)
    small = cv2.resize(raster, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    found = detect_markers(small)  # the PDF's picture carries all four markers
    geom = load_geometry()
    for mid, corners in found.items():
        mm = _page_px_to_mm(paper, 300, corners + 0.5)
        assert np.max(np.abs(mm - geom.marker_corners[mid])) < 0.3, mid


def test_generator_is_deterministic(tmp_path):
    a = make_gauge.pdf_bytes(make_gauge.render_page("letter", 150), "letter")
    b = make_gauge.pdf_bytes(make_gauge.render_page("letter", 150), "letter")
    assert a == b
