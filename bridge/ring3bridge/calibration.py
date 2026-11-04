"""``ring3 calibrate`` (T-07 Part B step 2, D-56, D-58, D-72): calibration moves through the kernel.

A scripted model stands in for Muse Spark (no model call): it answers the kernel's REQUEST with ordinary
actions through the normal bridge -> kernel -> agentsh path, so the kernel enforces and audits every move:

  1. the start is the board's current ``STATE`` servo angle (``servo_cmd``), from a telemetry frame read
     after the request opened (``Runner.fresh_servo``; no ACTION needed: agentsh ends a request after its 8th
     ACTION, and the worst plan already takes 7 moves);
  2. ``servo_set`` moves of at most ``STEP_MAX`` deg, at least ``MOVE_GAP_S`` apart, to each point of
     ``CAL_POINTS`` (D-72: 60/70/90/110/120, see below); every point is approached from the same side (a
     short pre-move past the first point when needed), so servo backlash is the same for all of them;
  3. after each point, a ``witness`` op: the bridge waits for telemetry to show the angle + 500 ms, takes a
     fresh frame, reserves one image, asks SAM, and records (commanded deg, dial millidegrees);
  4. ``done``. A move the kernel refuses ends the script with ``done`` naming it (nothing is retried).

The CLI then fits the points (``witness.calibrate.fit``), logs a ``calibration`` record and writes
``calibration.json`` (integers only) next to the run's ``events.jsonl``. ``ring3 run --calibration <run>``
re-reads the points and re-fits them with the current limits; a mismatch with the stored numbers is refused.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ring3bridge import abi, eventlog
from ring3bridge.models.muse_spark import STATUS_COMPLETED, ModelReply
from ring3bridge.witness.calibrate import Calibration, fit
from ring3bridge.witness.service import Checked, Witness

# D-72 (lead, T-07 Part B), replacing D-59's "calibration points avoid 80-100 deg": on synthetic frames
# (12 camera/light variants, 18 mm arm) the box-centre error steps by ~7 deg at 90 (+3 just below, -4..-5
# just above); two-sided sets without 90 failed the 3.0 deg limit in most variants (45/60/75/105/120: 2 of
# 12), every two-sided set with 90 passed 12 of 12; 60/70/90/110/120 had the widest margin (worst 1.83 deg).
# The limit is unchanged. ``ring3 calibrate --points`` overrides the set.
CAL_POINTS = (60, 70, 90, 110, 120)
STEP_MAX = 30  # policy.h SERVO_STEP_MAX_DEG
SERVO_RANGE = (30, 150)  # policy.h SERVO_MIN_DEG / SERVO_MAX_DEG
PRE_MOVE_DEG = 10  # approach the first point from this far beyond it
MOVE_GAP_S = 0.3  # policy: at least 200 ms between servo commands
CAL_FILE = "calibration.json"


def _steps(a: int, b: int) -> list[int]:
    """Intermediate + final targets from ``a`` to ``b`` in steps of at most STEP_MAX (``[b]`` when a == b)."""
    out, cur = [], a
    while abs(b - cur) > STEP_MAX:
        cur += STEP_MAX if b > cur else -STEP_MAX
        out.append(cur)
    out.append(b)
    return out


def plan(current: int, points: tuple[int, ...] = CAL_POINTS) -> list[tuple[int, bool]]:
    """``(deg, measure)`` servo commands from the ``current`` angle. Every point is approached from
    the same side (a pre-move PRE_MOVE_DEG past the first point when ``current`` is not already beyond it);
    ascending or descending, whichever needs fewer moves."""
    lo, hi = SERVO_RANGE
    if not lo <= current <= hi:
        raise ValueError(f"current servo {current} outside {lo}-{hi}")
    pts = sorted(points)
    if len(set(pts)) != len(pts) or pts[0] < lo or pts[-1] > hi:
        raise ValueError("bad calibration points")

    def route(order: list[int]) -> list[tuple[int, bool]]:
        first, up = order[0], order[-1] > order[0]
        moves: list[tuple[int, bool]] = []
        cur = current
        if (up and current >= first) or (not up and current <= first):  # equal: unknown approach side
            pre = max(lo, first - PRE_MOVE_DEG) if up else min(hi, first + PRE_MOVE_DEG)
            moves += [(d, False) for d in _steps(cur, pre)]
            cur = pre
        for p in order:
            path = _steps(cur, p)
            moves += [(d, False) for d in path[:-1]] + [(p, True)]
            cur = p
        return moves

    up, down = route(pts), route(pts[::-1])
    return up if len(up) <= len(down) else down


CHECK_TARGETS = (50, 90, 130)  # zone centres (D-59), for ``ring3 calibrate --checks``


def route(current: int, targets: tuple[int, ...]) -> list[tuple[int, bool]]:
    """``(deg, check)`` commands visiting ``targets`` in order, in steps of at most STEP_MAX; a target equal
    to the current angle is still commanded (the witness needs a kernel-allowed move in the request)."""
    lo, hi = SERVO_RANGE
    if not lo <= current <= hi or not targets or any(not lo <= t <= hi for t in targets):
        raise ValueError("bad current angle or targets")
    moves: list[tuple[int, bool]] = []
    cur = current
    for t in targets:
        path = _steps(cur, t)
        moves += [(d, False) for d in path[:-1]] + [(t, True)]
        cur = t
    return moves


def _reply(obj: dict) -> ModelReply:
    text = json.dumps(obj, separators=(",", ":"))
    return ModelReply(text, None, None, status=STATUS_COMPLETED, texts=1)


def _call(op: str, **args: int) -> ModelReply:
    return _reply({"action": "call", "op": op, "args": args})


@dataclass
class CalibrationScript:
    """The scripted model (``ask``) and the witness callable (``measure``) for calibration requests, or with
    ``checks=True`` for scripted zone checks (``ring3 calibrate --checks``): ``route`` to each of ``points``
    and a real ``Witness.check`` after each; every request starts again from its own fresh telemetry."""

    witness: Witness
    points: tuple[int, ...] = CAL_POINTS
    checks: bool = False
    move_gap_s: float = MOVE_GAP_S
    requests: int = 0  # replies given (the run loop's timeline reads this like a model client's)
    runner: object = None  # bound after the Runner exists (reads telemetry and the open request's results)
    pairs: list[tuple[int, int | None]] = field(default_factory=list)
    moves: list[tuple[int, bool]] = field(default_factory=list)
    start_deg: int | None = None
    note: str = ""  # why the script ended early ("" when every point was visited)
    _step: int = 0  # index of the next move
    _expect_point: int | None = None
    _measured: dict[int, int | None] = field(default_factory=dict)
    results: list[tuple[int, str, Checked]] = field(
        default_factory=list
    )  # checks: (request, its text, result)
    _req: object = None

    def max_actions(self) -> int:
        """The bridge's turn bound for this request: the kernel's ACTION cap (the moves) plus one witness per
        point (witness ops send no frame, so the kernel's cap does not count them)."""
        return abi.ACTIONS_PER_REQUEST + len(self.points)

    def measure(self, commanded_deg: int) -> Checked:
        if self.checks:
            checked = self.witness.check(commanded_deg)
            req = getattr(self.runner, "request", None)
            self.results.append((getattr(req, "index", 0), getattr(req, "text", ""), checked))
        else:
            checked = self.witness.measure(commanded_deg)
        self._measured[commanded_deg] = checked.result.dial_mdeg
        return checked

    def _done(self, summary: str) -> ModelReply:
        return _reply({"action": "done", "summary": summary[:200]})

    def ask(self, instructions: str, user_input: str) -> ModelReply:
        self.requests += 1
        req = getattr(self.runner, "request", None)
        if req is not self._req:  # a new request: plan again from its own telemetry
            self._req, self.start_deg, self.moves, self._step, self._expect_point = req, None, [], 0, None
        last = req.results[-1] if req is not None and req.results else None
        what = "checks" if self.checks else "calibration"
        if self.start_deg is None:
            start = self.runner.fresh_servo() if self.runner is not None else None
            if start is None:
                self.note = "no telemetry from the cell yet"
                return self._done(f"{what} not started: " + self.note)
            self.start_deg = start
            try:
                self.moves = (route if self.checks else plan)(self.start_deg, self.points)
            except ValueError as e:
                self.note = str(e)
                return self._done(f"{what} not started: " + self.note)
            if len(self.moves) >= abi.ACTIONS_PER_REQUEST:  # agentsh ends the request after its 8th ACTION
                self.note = f"{len(self.moves)} moves needed; at most {abi.ACTIONS_PER_REQUEST - 1} fit"
                return self._done(f"{what} not started: " + self.note)
        elif last is not None and last[0] == "servo_set" and last[1] < 0:
            deg = self.moves[self._step - 1][0]
            self.note = f"servo_set {deg} refused by the kernel (result {last[1]})"
            return self._done(f"{what} stopped: " + self.note)
        if self._expect_point is not None:  # the witness op for this point has just been answered
            p, self._expect_point = self._expect_point, None
            if not self.checks:
                self.pairs.append((p, self._measured.pop(p, None)))
        elif self._step > 0 and self.moves[self._step - 1][1]:
            self._expect_point = self.moves[self._step - 1][0]
            return _call("witness")
        if self._step >= len(self.moves):
            if self.checks:
                n = sum(1 for _, m in self.moves if m)
                return self._done(f"checks done: {n} witness checks after kernel-allowed moves")
            got = sum(1 for _, m in self.pairs if m is not None)
            return self._done(f"calibration moves done: {got} of {len(self.pairs)} points measured")
        deg = self.moves[self._step][0]
        self._step += 1
        time.sleep(self.move_gap_s)
        return _call("servo_set", deg=deg)


# ---- the calibration file ----


def calibration_doc(
    run_id: str, pairs: list[tuple[int, int | None]], cal: Calibration, geom_sha: str
) -> dict:
    return {
        "run_id": run_id,
        "points": [[c, m] for c, m in pairs],
        "ok": int(cal.ok),
        "reason": cal.reason,
        "slope_ppm": cal.slope_ppm,
        "offset_mdeg": cal.offset_mdeg,
        "residual_mdeg": cal.residual_mdeg,
        "n": cal.n,
        "geometry_sha256": geom_sha,
    }


def write_calibration(run_dir: Path, doc: dict) -> str:
    data = (eventlog.canonical(doc) + "\n").encode("ascii")
    (Path(run_dir) / CAL_FILE).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class CalibrationError(Exception):
    pass


def load_calibration(run_dir: Path, geom_sha: str) -> tuple[Calibration, dict, str]:
    """-> (calibration re-fitted from the stored points, the stored document, its SHA-256). Refuses a run
    whose chain does not verify, a file that is not the one the run logged, another gauge geometry, or stored
    numbers that the current fit does not reproduce."""
    run_dir = Path(run_dir)
    v = eventlog.verify(run_dir)
    if not v.ok:
        raise CalibrationError(f"calibration run does not verify: {v.message}")
    data = (run_dir / CAL_FILE).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    recs = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]
    logged = [r for r in recs if r.get("type") == "calibration"]
    if not logged or logged[-1].get("file_sha256") != sha:
        raise CalibrationError(f"{CAL_FILE} is not the file the calibration run logged")
    doc = json.loads(data)
    if doc.get("geometry_sha256") != geom_sha:
        raise CalibrationError("calibrated on another gauge geometry")
    pts = doc.get("points")
    if type(pts) is not list or not all(
        type(p) is list and len(p) == 2 and type(p[0]) is int and (p[1] is None or type(p[1]) is int)
        for p in pts
    ):
        raise CalibrationError("bad points")
    cal = fit([(c, m) for c, m in pts])
    stored = (bool(doc.get("ok")), doc.get("slope_ppm"), doc.get("offset_mdeg"), doc.get("residual_mdeg"))
    if cal.ok and stored != (cal.ok, cal.slope_ppm, cal.offset_mdeg, cal.residual_mdeg):
        raise CalibrationError("stored fit differs from the re-fit of its points")
    return cal, doc, sha


# ---- SAM probe (``ring3 calibrate --probe``): saved frames x phrases x crops, at most PROBE_MAX images ----

PROBE_MAX = 5
#: S5 (D-88): a probe re-reads a saved frame of any age, so its verdict word is outside the INV-14 vocabulary
#: on purpose: a probe row is never a witness measurement and never evidence
PROBE_WORD = "PROBE (saved frame, not evidence)"
#: the pipeline's crop (geometry.json) and a tighter one around the pivot at a higher scale: the 15 mm horn is
#: ~75 px in the full 920 x 570 crop and ~180 px in the 720 x 504 tight crop (no new detail, a larger object)
PROBE_CROPS = {"full": None, "tight": (-30.0, 30.0, -12.0, 30.0, 12)}


def probe_plan(frames: list[Path], phrases: list[str], crops: list[str]) -> list[tuple[Path, str, str]]:
    if not frames or not phrases or not crops or any(c not in PROBE_CROPS for c in crops):
        raise ValueError(f"need frames, phrases and crops from {sorted(PROBE_CROPS)}")
    plan_ = [(f, c, p) for f in frames for c in crops for p in phrases]
    if len(plan_) > PROBE_MAX:
        raise ValueError(f"{len(plan_)} images planned, at most {PROBE_MAX}")
    return plan_


def probe(items: list[tuple[Path, str, str]], geom, sam, log: eventlog.EventLog, out=print) -> list[dict]:
    """One SAM image per item (reserved first, by ``sam``): markers -> crop -> SAM -> arm filter. Logs a
    ``sam_probe`` record per item with the raw reply as a blob; never touches the kernel link. A frame the
    pipeline reads gives ``PROBE_WORD`` (not MEASURED: a saved frame is not evidence, S5), else the
    CantVerify reason."""
    import cv2
    import numpy as np

    from ring3bridge.witness import CantVerify
    from ring3bridge.witness.angle import DEFAULT_ARM
    from ring3bridge.witness.dial import Crop, Geometry
    from ring3bridge.witness.pipeline import Evidence, measure

    rows = []
    for frame, crop_name, phrase in items:
        data = Path(frame).read_bytes()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        g = (
            geom
            if PROBE_CROPS[crop_name] is None
            else Geometry(geom.marker_corners, Crop(*PROBE_CROPS[crop_name]))
        )
        ev = Evidence()
        reason, detail, dial = PROBE_WORD, "", None
        try:
            if img is None:
                raise ValueError("frame does not decode")
            dial = measure(img, g, sam, DEFAULT_ARM, phrase, ev).dial_mdeg
        except CantVerify as e:
            reason, detail = e.reason, e.detail
        n_rec = ev.sam_line.count("<|box;")
        blobs = {"frame_blob": log.blob(data)}
        if ev.jpeg:
            blobs["crop_blob"] = log.blob(ev.jpeg)
        if ev.sam_reply:
            blobs["sam_reply_blob"] = log.blob(ev.sam_reply)
        row = {"frame_sha256": hashlib.sha256(data).hexdigest(), "crop": crop_name, "phrase": phrase,
               "records": n_rec, "line_bytes": len(ev.sam_line), "reason": reason, "detail": detail,
               "dial_mdeg": dial, "spend_uusd": ev.spend_uusd}  # fmt: skip
        log.append("sam_probe", **row, **blobs)
        rows.append(row)
        deg = "" if dial is None else f" dial {dial / 1000:.1f} deg"
        out(f"  {crop_name:5s} {phrase!r:24s} -> {n_rec} record(s), {reason}{deg} {detail}")
    return rows
