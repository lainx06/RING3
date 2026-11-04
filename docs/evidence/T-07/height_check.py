"""T-07 Part B, D-78 remount: angle error from the horn's height above the gauge plane (no SAM, no camera).

A point at height h, seen by a pinhole camera at height H whose axis meets the paper at c, maps back through
the paper homography to c + k (p - c), k = H / (H - h). The whole horn (hub included) is at h, so its own
direction is kept; the witness measures from the printed pivot, so the box centre m becomes k m - (k - 1) c:
a fixed offset of (k - 1)|c| seen from k|m|. Run: uv run python docs/evidence/T-07/height_check.py
"""

import math

import numpy as np

# Camera height from the measured scale and the C922's specified 78 deg diagonal FOV (1920x1080).
PX_MM = 4.84
f_px = math.hypot(1920, 1080) / 2 / math.tan(math.radians(78 / 2))
H_EST = f_px / PX_MM
HUB_R, TIP_C, TIP_R = 3.5, 13.0, 2.0  # horn outline in its own plane (15 mm shaft centre to tip end)
D72 = (60, 70, 90, 110, 120)
CHECK = range(50, 131, 1)  # every command the zone checks can use


def outline(theta_deg):
    a = math.radians(theta_deg)
    u = np.array([math.cos(a), math.sin(a)])
    n = np.array([-u[1], u[0]])
    t = np.linspace(0, 2 * math.pi, 180)
    hub = np.c_[HUB_R * np.cos(t), HUB_R * np.sin(t)]
    tip = TIP_C * u + np.c_[TIP_R * np.cos(t), TIP_R * np.sin(t)]
    bar = np.array([2.5 * n, TIP_C * u + 2.0 * n, TIP_C * u - 2.0 * n, -2.5 * n])
    return np.vstack([hub, tip, bar])


def measured(theta, k, c):
    """Angle the witness computes: printed pivot (0,0) -> centre of the box of the apparent horn."""
    p = k * outline(theta) + (1 - k) * c  # points at height h seen on the paper plane
    x0, y0 = p.min(0)
    x1, y1 = p.max(0)
    return math.degrees(math.atan2((y0 + y1) / 2, (x0 + x1) / 2))


def errors(h, H, d, extra=None):
    """Max |error| caused by height h (and a shaft placement offset ``extra``) over all camera-offset
    directions, raw and after the D-72 linear fit, relative to the same horn at h = 0, c = 0."""
    extra = np.zeros(2) if extra is None else extra
    k = H / (H - h)
    angles = sorted(set(CHECK) | set(D72))
    base = {th: measured(th, 1.0, np.zeros(2)) for th in angles}
    raw_w, fit_w = 0.0, 0.0
    for phi in np.linspace(0, 2 * math.pi, 72, endpoint=False):
        c = d * np.array([math.cos(phi), math.sin(phi)])
        dev = {th: measured_off(th, k, c, extra) - base[th] for th in angles}
        raw_w = max(raw_w, max(abs(v) for v in dev.values()))
        # calibration: least-squares line through the 5 D-72 points; what is left at every check angle
        a, b = np.polyfit(np.array(D72, float), np.array([dev[t] for t in D72]), 1)
        fit_w = max(fit_w, max(abs(dev[t] - (a * t + b)) for t in CHECK))
    return raw_w, fit_w


def measured_off(theta, k, c, s):
    """Shaft at s (mm) instead of the printed pivot: the horn's points shift by s, then are lifted."""
    p = k * (outline(theta) + s) + (1 - k) * c
    x0, y0 = p.min(0)
    x1, y1 = p.max(0)
    return math.degrees(math.atan2((y0 + y1) / 2, (x0 + x1) / 2))


print(f"camera height from 4.84 px/mm and 78 deg DFOV: f = {f_px:.0f} px, H = {H_EST:.0f} mm")
print(
    "box centre distance from the pivot, 15 mm horn:",
    f"{min(math.hypot(*((outline(t).min(0) + outline(t).max(0)) / 2)) for t in CHECK):.2f} mm",
    "(min over 50-130)",
)
print("\nheight 25 mm, camera axis 1.3-1.7 mm off the pivot (worst direction):")
print(" H (mm)   k      raw max err   after D-72 fit")
for H in (200, 250, H_EST, 350, 450):
    for d in (1.3, 1.7):
        r, f = errors(25.0, H, d)
        print(f" {H:5.0f}  {H / (H - 25):.3f}   d={d}: {r:5.2f} deg     {f:5.2f} deg")
print(f"\nfor comparison, shaft placed off the PIVOT mark by s (camera offset 1.7 mm, H = {H_EST:.0f}):")
for s in (0.5, 1.0, 2.0):
    worst_r = worst_f = 0
    for psi in np.linspace(0, 2 * math.pi, 24, endpoint=False):
        r, f = errors(25.0, H_EST, 1.7, extra=s * np.array([math.cos(psi), math.sin(psi)]))
        worst_r, worst_f = max(worst_r, r), max(worst_f, f)
    print(f" s = {s} mm: raw max {worst_r:5.2f} deg, after D-72 fit {worst_f:5.2f} deg")
