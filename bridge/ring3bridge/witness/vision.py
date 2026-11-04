"""Vision witness backend (T-16, D-98, INV-14): Muse Spark names the printed zone the arm points at.

One check (``VisionWitness.check``), cheap and offline first so a check that cannot succeed takes no frame
and spends nothing:

  commanded zone (``verdict.commanded_zone``: a command inside a boundary band or outside 30-150 is CAN'T
  VERIFY before anything else) -> the session gate (``ZoneCheck``: enabled only by a 6-of-6 zone check, else
  CAN'T VERIFY: CALIBRATION) -> a fresh frame (the camera raises STALE_FRAME, D-80) -> the 4 markers and the
  rectified dial crop (``dial``, then ``mask_markers``: the dial alone, 920 x 570 px, the marker squares
  painted over, no desk) -> one Muse Spark call
  through the injected ``ZoneModel`` (the adapter in ``models/vision.py``: it reserves spend first and raises
  ``ModelStop`` on a refusal, 401/403 or a transport error, which stops the run, INV-09) -> the reply text
  must be exactly ``{"zone": "<Z>", "arm_visible": true|false}`` (``parse_zone``: no extra field, no prose,
  no fence, no second object, no unknown zone; anything else CAN'T VERIFY: VISION_FORMAT, never a retry) ->
  verdict (``judge_zone``): PASS when the zone is the commanded one and the arm is visible, FAIL for a
  different definite zone (WRONG_ZONE, or OUT_OF_SCALE for OUT_LOW / OUT_HIGH), else CAN'T VERIFY
  (NOT_VISIBLE, UNSURE).

The model is asked for the zone only, never an angle; ``dial_mdeg`` and ``calibrated_mdeg`` stay None.
The first ``CantVerify`` ends the check, so a CAN'T VERIFY is never upgraded. The context pack's SHA-256
and the crop's SHA-256 go into every result. Nothing here reads the model key, touches the kernel link or
writes a file (the INV-14 static scan covers this module).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ring3bridge.witness import (
    CALIBRATION,
    CANT_VERIFY,
    FAIL,
    NOT_VISIBLE,
    OUT_OF_SCALE,
    PASS,
    UNSURE,
    VISION_FORMAT,
    WRONG_ZONE,
    ZONE_MATCH,
    CantVerify,
)
from ring3bridge.witness.dial import GEOMETRY_JSON, Geometry, encode_jpeg, find_dial
from ring3bridge.witness.pipeline import WitnessResult
from ring3bridge.witness.service import Camera, Checked
from ring3bridge.witness.verdict import Judgement, commanded_zone

__all__ = ["GEOMETRY_JSON"]  # re-exported for the CLI and tests (the geometry the zone check is tied to)

BACKEND = "vision"
PACK_NAME = "witness-vision-v1"  # docs/context-packs/witness-vision-v1.md, SHA-256 logged per call
USER_TEXT = "Which zone does the black arm point at? Reply with the JSON object only."
ZONE_VALUES = ("OUT_LOW", "LOW", "MID", "HIGH", "OUT_HIGH", "UNSURE")  # = models.vision.ZONE_VALUES
OUT_ZONES = ("OUT_LOW", "OUT_HIGH")
MAX_LONG_SIDE = 1280  # card: the image sent is at most 1280 px on its long side
MARKER_MASK_MM = 1.0  # the four marker squares (plus this margin) are painted paper-tone in the image sent
MAX_TEXT_BYTES = 256  # the valid object is 40 bytes; anything longer is not it
JSON_WS = " \t\n\r"
STATUS_COMPLETED = "completed"


class ZoneReply(Protocol):
    """What the adapter returns (``models.vision.VisionReply``); only these attributes are read here."""

    text: str
    status: str
    incomplete_reason: str
    texts: int
    uusd: int
    body: bytes


class ZoneModel(Protocol):
    def ask(self, instructions: str, user_text: str, jpeg: bytes) -> ZoneReply: ...


class Pack(Protocol):
    name: str
    text: str
    sha256: str


# ---- the image sent: the dial alone ----


def mask_markers(crop_img: np.ndarray, geom: Geometry) -> np.ndarray:
    """The dial crop with the four ArUco squares painted over (card: the dial alone, no markers). The
    geometry's crop already excludes the desk; the marker squares at its corners are filled with the
    crop's paper tone (its 95th-percentile brightness). The markers were used for the rectification; the
    model never sees them."""
    out = crop_img.copy()
    gray = out if out.ndim == 2 else out.mean(axis=2)
    fill = int(np.percentile(gray, 95))
    h, w = out.shape[:2]
    for corners in geom.marker_corners.values():
        x0, x1 = float(corners[:, 0].min()) - MARKER_MASK_MM, float(corners[:, 0].max()) + MARKER_MASK_MM
        y0, y1 = float(corners[:, 1].min()) - MARKER_MASK_MM, float(corners[:, 1].max()) + MARKER_MASK_MM
        u0, v1 = geom.crop.mm_to_px(x0, y0)  # y up on the paper, v down in the crop
        u1, v0 = geom.crop.mm_to_px(x1, y1)
        ua, ub = max(0, int(np.floor(u0))), min(w, int(np.ceil(u1)))
        va, vb = max(0, int(np.floor(v0))), min(h, int(np.ceil(v1)))
        if ua < ub and va < vb:
            out[va:vb, ua:ub] = fill
    return out


# ---- the reply as data ----


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    d: dict[str, object] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key")
        d[k] = v
    return d


def _no_numbers(_s: str) -> object:
    raise ValueError("number")


def parse_zone(text: str) -> tuple[str, bool]:
    """The model's text -> (zone, arm_visible). Exactly one JSON object with exactly the two keys, ``zone``
    one of ``ZONE_VALUES`` and ``arm_visible`` a JSON boolean; surrounding ASCII whitespace only. Anything
    else raises ``CantVerify(VISION_FORMAT)``."""
    if type(text) is not str:
        raise CantVerify(VISION_FORMAT, "reply is not text")
    s = text.strip(JSON_WS)
    if not s:
        raise CantVerify(VISION_FORMAT, "empty reply")
    if len(s) > MAX_TEXT_BYTES:
        raise CantVerify(VISION_FORMAT, f"reply longer than {MAX_TEXT_BYTES} bytes")
    if not all(0x20 <= ord(ch) < 0x7F for ch in s):
        raise CantVerify(VISION_FORMAT, "non-ASCII or control character in the reply")
    if not s.startswith("{"):
        raise CantVerify(VISION_FORMAT, "text before the JSON object")
    try:
        d = json.loads(s, object_pairs_hook=_no_duplicates, parse_int=_no_numbers, parse_float=_no_numbers,
                       parse_constant=_no_numbers)  # fmt: skip
    except json.JSONDecodeError as e:
        raise CantVerify(VISION_FORMAT, "text after the JSON object" if e.msg == "Extra data" else
                         "not valid JSON") from None  # fmt: skip
    except (ValueError, RecursionError) as e:
        raise CantVerify(VISION_FORMAT, str(e) or "not valid JSON") from None
    if type(d) is not dict:
        raise CantVerify(VISION_FORMAT, "not a JSON object")
    if set(d) != {"zone", "arm_visible"}:
        raise CantVerify(VISION_FORMAT, "keys are not exactly zone and arm_visible")
    zone, visible = d["zone"], d["arm_visible"]
    if type(zone) is not str or zone not in ZONE_VALUES:
        raise CantVerify(VISION_FORMAT, "zone is not one of " + "/".join(ZONE_VALUES))
    if type(visible) is not bool:
        raise CantVerify(VISION_FORMAT, "arm_visible is not a JSON boolean")
    return zone, visible


def judge_zone(commanded_deg: int, zone: str, arm_visible: bool) -> Judgement:
    """The card's verdict table. Raises ``CantVerify`` (COMMAND_RANGE, COMMAND_BOUNDARY, NOT_VISIBLE,
    UNSURE); returns PASS (ZONE_MATCH), FAIL (WRONG_ZONE) or FAIL (OUT_OF_SCALE)."""
    cz = commanded_zone(commanded_deg)
    if zone not in ZONE_VALUES:
        raise ValueError("zone must be validated first")
    if not arm_visible:
        raise CantVerify(NOT_VISIBLE, "the model reports no visible arm on the pivot")
    if zone == UNSURE:
        raise CantVerify(UNSURE, "the model could not name a definite zone")
    if zone == cz:
        return Judgement(PASS, ZONE_MATCH, cz, zone)
    if zone in OUT_ZONES:
        return Judgement(FAIL, OUT_OF_SCALE, cz, zone)
    return Judgement(FAIL, WRONG_ZONE, cz, zone)


# ---- the session gate: the zone check ----

Row = tuple[int, str, str, str, int | None]  # (commanded deg, verdict, reason, zone, arm_visible)


@dataclass(frozen=True)
class ZoneCheck:
    """The result of ``ring3 calibrate --witness-backend vision``: the backend is enabled for a session only
    when every one of the ``targets`` (6 images, 2 per zone) was PASS, by this backend with this pack."""

    ok: bool
    reason: str  # "" when ok, else the message shown to the user
    passed: int = 0
    n: int = 0
    run_id: str = ""
    pack_sha256: str = ""
    geometry_sha256: str = ""
    targets: tuple[int, ...] = ()
    results: tuple[Row, ...] = ()

    def message(self) -> str:
        if self.ok:
            return f"vision witness enabled: zone check {self.passed} of {self.n} PASS" + (
                f" (run {self.run_id})" if self.run_id else ""
            )
        return f"vision witness disabled for this session: {self.reason}"


NONE = ZoneCheck(False, "no zone check (run ring3 calibrate --witness-backend vision first)")
CHECKING = ZoneCheck(True, "")  # the zone check itself: calls allowed, nothing is enabled by it yet


def zone_check(
    results: Sequence[WitnessResult],
    targets: Sequence[int],
    run_id: str,
    pack_sha256: str,
    geometry_sha256: str,
) -> ZoneCheck:
    """Rows in the order commanded; ok only when each target has a PASS from this backend and pack, in
    order, with nothing missing or extra. Every non-PASS row is named in the reason (reported as measured)."""
    rows: tuple[Row, ...] = tuple(
        (r.commanded_deg, r.verdict, r.reason, r.measured_zone, r.arm_visible) for r in results
    )
    n = len(targets)
    problems: list[str] = []
    passed = 0
    for i, t in enumerate(targets):
        if i >= len(results):
            problems.append(f"image {i + 1} ({t} deg): missing")
            continue
        r = results[i]
        if r.commanded_deg != t:
            problems.append(f"image {i + 1}: commanded {r.commanded_deg} deg, expected {t}")
        elif r.backend != BACKEND or r.pack_sha256 != pack_sha256:
            problems.append(f"image {i + 1} ({t} deg): not from the vision backend with this pack")
        elif r.verdict != PASS:
            problems.append(f"image {i + 1} ({t} deg): {r.verdict} {r.reason}")
        else:
            passed += 1
    if len(results) > n:
        problems.append(f"{len(results) - n} result(s) beyond the {n} planned")
    ok = not problems and passed == n and n > 0
    reason = "" if ok else f"{passed} of {n} PASS: " + "; ".join(problems)
    return ZoneCheck(ok, reason, passed, n, run_id, pack_sha256, geometry_sha256, tuple(targets), rows)


# ---- one check ----


class VisionWitness:
    backend = BACKEND

    def __init__(self, camera: Camera, model: ZoneModel, geom: Geometry, pack: Pack, gate: ZoneCheck = NONE):
        self.camera = camera
        self.model = model
        self.geom = geom
        self.pack = pack
        self.gate = gate

    def _result(self, verdict: str, reason: str, detail: str, deg: int, cz: str, zone: str, image_sha: str,
                spend: int, crop: bytes, visible: int | None, body: bytes) -> WitnessResult:  # fmt: skip
        return WitnessResult(verdict, reason, detail, deg, cz, zone, None, None, image_sha, "", spend, crop,
                             b"", BACKEND, self.pack.sha256, visible, body)  # fmt: skip

    def check(self, commanded_deg: int) -> Checked:
        cz = ""
        try:
            cz = commanded_zone(commanded_deg)
            if not self.gate.ok:
                raise CantVerify(CALIBRATION, "vision witness disabled: " + self.gate.reason)
            img, jpeg = self.camera()
        except CantVerify as e:
            return Checked(self._result(CANT_VERIFY, e.reason, e.detail, commanded_deg, cz, "", "", 0, b"",
                                        None, b""), b"")  # fmt: skip
        image_sha, crop, body, spend, visible = "", b"", b"", 0, None
        try:
            dial = find_dial(img, self.geom)
            h, w = dial.crop_img.shape[:2]
            if max(h, w) > MAX_LONG_SIDE:
                raise ValueError(f"dial crop {w}x{h} exceeds {MAX_LONG_SIDE} px (geometry.json)")
            crop = encode_jpeg(mask_markers(dial.crop_img, self.geom))
            image_sha = hashlib.sha256(crop).hexdigest()
            reply = self.model.ask(self.pack.text, USER_TEXT, crop)  # ModelStop propagates: the run stops
            body, spend = reply.body, reply.uusd
            if reply.status != STATUS_COMPLETED:
                why = f"reply status {reply.status or 'missing'}"
                if reply.incomplete_reason:
                    why += f" (incomplete:{reply.incomplete_reason})"
                raise CantVerify(VISION_FORMAT, why)
            if reply.texts != 1:
                raise CantVerify(VISION_FORMAT, f"{reply.texts} output texts, expected 1")
            zone, arm_visible = parse_zone(reply.text)
            visible = int(arm_visible)
            j = judge_zone(commanded_deg, zone, arm_visible)
        except CantVerify as e:
            return Checked(self._result(CANT_VERIFY, e.reason, e.detail, commanded_deg, cz, "", image_sha,
                                        spend, crop, visible, body), jpeg)  # fmt: skip
        return Checked(self._result(j.verdict, j.reason, "", commanded_deg, cz, j.measured_zone, image_sha,
                                    spend, crop, visible, body), jpeg)  # fmt: skip
