"""T-16: the vision witness (Muse Spark reads the gauge zone from the rectified crop), offline.

No network, no key, no camera, no paid call: synthetic frames (``synth``), a fake transport answering with
Responses API envelopes, a test ledger. Covers the card's step 2: the strict reply schema (anything but the
exact object is CAN'T VERIFY: VISION_FORMAT), a commanded angle in a boundary band makes no call, the
reservation exists on disk before the call and the caps hold, a stale frame is refused before any call, the
zone check enables the backend only on 6/6, the request shape is the documented one, and the observation
names the backend. INV-14's static scan covers ``witness/vision.py`` like every other witness module.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ring3bridge import abi, cli, context_pack, eventlog, run, zonecheck
from ring3bridge.config import load_model_config, load_vision_config
from ring3bridge.eventlog import EventLog
from ring3bridge.ledger import Ledger, cost_uusd
from ring3bridge.models import vision as vision_model
from ring3bridge.models.muse_spark import ModelStop
from ring3bridge.models.transport import TransportError, TransportResponse
from ring3bridge.models.vision import VisionClient, build_request, reserve_input_tokens
from ring3bridge.witness import (
    CALIBRATION,
    CANT_VERIFY,
    COMMAND_BOUNDARY,
    COMMAND_RANGE,
    FAIL,
    MARKERS,
    NOT_VISIBLE,
    OUT_OF_SCALE,
    PASS,
    STALE_FRAME,
    UNSURE,
    VISION_FORMAT,
    WRONG_ZONE,
    ZONE_MATCH,
    CantVerify,
)
from ring3bridge.witness import vision as vision_witness
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.pipeline import WitnessResult
from ring3bridge.witness.vision import VisionWitness, ZoneCheck, judge_zone, parse_zone, zone_check
from tests.witness import synth
from tests.witness.synth import Scene

GEOM = load_geometry()
RUN = "run-vision-test"
PACK = context_pack.load(vision_witness.PACK_NAME)
GEOM_SHA = hashlib.sha256(Path(GEOM_JSON := vision_witness.GEOMETRY_JSON).read_bytes()).hexdigest()


def envelope(text: str, status: str = "completed", tin: int = 1700, tout: int = 20) -> bytes:
    """A Responses API reply in the documented shape (one message with one output_text)."""
    d = {
        "id": "resp_fake", "object": "response", "status": status, "model": "muse-spark-1.3",
        "output": [{"type": "message", "role": "assistant", "status": status,
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": tin, "output_tokens": tout, "input_tokens_details": {"cached_tokens": 0}},
    }  # fmt: skip
    if status == "incomplete":
        d["incomplete_details"] = {"reason": "max_output_tokens"}
        d["output"] = [{"type": "reasoning", "id": "rs_fake", "status": "incomplete", "summary": []}]
    return json.dumps(d).encode()


def zone_reply(zone: str, visible: bool = True) -> TransportResponse:
    return TransportResponse(200, envelope(json.dumps({"zone": zone, "arm_visible": visible})))


def expected_reservation(cfg, body: bytes, jpeg: bytes) -> int:
    tin = reserve_input_tokens(body, jpeg, cfg.image_tokens_reserve)
    return cost_uusd(tin, cfg.price.input_uusd_per_mtok) + cost_uusd(
        cfg.max_output_tokens, cfg.price.output_uusd_per_mtok
    )


class FakeTransport:
    """Queued responses (TransportResponse or TransportError); every post is recorded, and at post time the
    ledger file on disk must hold exactly one open reservation of the worst-case size (INV-09)."""

    def __init__(self, cfg, ledger_path: Path, cap: int, *responses) -> None:
        self.cfg, self.ledger_path, self.cap = cfg, ledger_path, cap
        self.responses = list(responses)
        self.posts: list[tuple[str, bytes, int]] = []

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse:
        self.posts.append((url, body, timeout_s))
        fresh = Ledger(self.cap, self.ledger_path)
        open_ = [r for rid, r in fresh._res.items() if rid not in fresh._settled and r.run_id == RUN]
        assert len(open_) == 1, "vision post without exactly one open reservation"
        jpeg = base64.b64decode(json.loads(body)["input"][0]["content"][1]["image_url"].split(",", 1)[1])
        assert open_[0].uusd == expected_reservation(self.cfg, body, jpeg)
        if not self.responses:
            raise AssertionError("unexpected vision post")
        r = self.responses.pop(0)
        if isinstance(r, TransportError):
            raise r
        return r


def make_vision(tmp: Path, *responses, budget: int | None = 200_000, cap: int = 30_000_000):
    """-> (VisionClient, FakeTransport, Ledger, EventLog)."""
    cfg = load_vision_config()
    ledger = Ledger(cap, tmp / "ledger.jsonl")
    log = EventLog(tmp / "runs" / RUN)
    log.append("run_start", run_id=RUN, model_id=cfg.id)
    t = FakeTransport(cfg, ledger.path, cap, *responses)
    return VisionClient(cfg, t, ledger, log, RUN, budget), t, ledger, log


class Camera:
    def __init__(self, scene: Scene | None, error: CantVerify | None = None) -> None:
        self.scene, self.error, self.calls = scene, error, 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        frame, _, _ = synth.render(self.scene)
        return frame, cv2.imencode(".jpg", frame)[1].tobytes()


def make_witness(tmp: Path, scene: Scene | None, *responses, gate: ZoneCheck = vision_witness.CHECKING,
                 camera_error: CantVerify | None = None, **kw):  # fmt: skip
    client, t, ledger, log = make_vision(tmp, *responses, **kw)
    cam = Camera(scene, camera_error)
    return VisionWitness(cam, client, GEOM, PACK, gate), cam, t, ledger, log


# ---- the strict reply schema ----

BAD_TEXTS = [
    ("extra field", '{"zone": "MID", "arm_visible": true, "angle": 92}'),
    ("wrong type: string bool", '{"zone": "MID", "arm_visible": "true"}'),
    ("wrong type: int bool", '{"zone": "MID", "arm_visible": 1}'),
    ("wrong type: zone not a string", '{"zone": 2, "arm_visible": true}'),
    ("two objects", '{"zone": "MID", "arm_visible": true}{"zone": "MID", "arm_visible": true}'),
    ("two objects, newline", '{"zone": "MID", "arm_visible": true}\n{"zone": "LOW", "arm_visible": true}'),
    ("prose before", 'The arm points at MID. {"zone": "MID", "arm_visible": true}'),
    ("prose after", '{"zone": "MID", "arm_visible": true} because the arm is vertical'),
    ("code fence", '```json\n{"zone": "MID", "arm_visible": true}\n```'),
    ("unknown zone", '{"zone": "MIDDLE", "arm_visible": true}'),
    ("lower-case zone", '{"zone": "mid", "arm_visible": true}'),
    ("missing arm_visible", '{"zone": "MID"}'),
    ("missing zone", '{"arm_visible": true}'),
    ("duplicate key", '{"zone": "MID", "zone": "LOW", "arm_visible": true}'),
    ("null", "null"),
    ("array", '[{"zone": "MID", "arm_visible": true}]'),
    ("empty", ""),
    ("whitespace only", " \n"),
    ("non-ASCII", '{"zone": "MID", "arm_visible": true}​'),
    ("not JSON", "zone MID, arm visible"),
    ("angle instead of zone", '{"zone": "92", "arm_visible": true}'),
    ("too long", '{"zone": "MID", "arm_visible": true' + " " * 300 + "}"),
]


@pytest.mark.parametrize("name,text", BAD_TEXTS, ids=[b[0] for b in BAD_TEXTS])
def test_schema_rejects_anything_but_the_exact_object(name, text):
    with pytest.raises(CantVerify) as e:
        parse_zone(text)
    assert e.value.reason == VISION_FORMAT, name


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"zone": "MID", "arm_visible": true}', ("MID", True)),
        ('{"zone":"OUT_HIGH","arm_visible":false}', ("OUT_HIGH", False)),
        ('{"arm_visible": true, "zone": "LOW"}', ("LOW", True)),  # JSON objects are unordered
        ('\n{"zone": "UNSURE", "arm_visible": false}\n', ("UNSURE", False)),  # surrounding whitespace only
    ],
)
def test_schema_accepts_the_exact_object(text, expected):
    assert parse_zone(text) == expected


def test_judge_zone_mapping():
    j = judge_zone(90, "MID", True)
    assert (j.verdict, j.reason, j.commanded_zone, j.measured_zone) == (PASS, ZONE_MATCH, "MID", "MID")
    j = judge_zone(50, "MID", True)
    assert (j.verdict, j.reason, j.commanded_zone, j.measured_zone) == (FAIL, WRONG_ZONE, "LOW", "MID")
    j = judge_zone(130, "OUT_HIGH", True)
    assert (j.verdict, j.reason, j.measured_zone) == (FAIL, OUT_OF_SCALE, "OUT_HIGH")
    for zone, visible, reason in [("MID", False, NOT_VISIBLE), ("UNSURE", True, UNSURE),
                                  ("UNSURE", False, NOT_VISIBLE)]:  # fmt: skip
        with pytest.raises(CantVerify) as e:
            judge_zone(90, zone, visible)
        assert e.value.reason == reason
    with pytest.raises(CantVerify) as e:
        judge_zone(70, "MID", True)
    assert e.value.reason == COMMAND_BOUNDARY
    with pytest.raises(CantVerify) as e:
        judge_zone(10, "MID", True)
    assert e.value.reason == COMMAND_RANGE


# ---- no call when the check cannot succeed ----


BAND_OR_RANGE = [(70, COMMAND_BOUNDARY), (74, COMMAND_BOUNDARY), (110, COMMAND_BOUNDARY),
                 (32, COMMAND_BOUNDARY), (20, COMMAND_RANGE), (160, COMMAND_RANGE)]  # fmt: skip


@pytest.mark.parametrize("deg,reason", BAND_OR_RANGE)
def test_boundary_or_out_of_range_command_takes_no_frame_and_makes_no_call(tmp_path, deg, reason):
    w, cam, t, ledger, _ = make_witness(tmp_path, Scene([float(deg)]))
    ch = w.check(deg)
    assert ch.result.verdict == CANT_VERIFY and ch.result.reason == reason
    assert cam.calls == 0 and t.posts == [] and ledger.committed_uusd() == 0 and ch.frame_jpeg == b""


def test_disabled_backend_takes_no_frame_and_makes_no_call(tmp_path):
    w, cam, t, ledger, _ = make_witness(tmp_path, Scene([90.0]), gate=vision_witness.NONE)
    ch = w.check(90)
    assert (ch.result.verdict, ch.result.reason) == (CANT_VERIFY, CALIBRATION)
    assert "no zone check" in ch.result.detail
    assert cam.calls == 0 and t.posts == [] and ledger.committed_uusd() == 0
    failed = ZoneCheck(False, "5 of 6 PASS", 5, 6)
    w, cam, t, ledger, _ = make_witness(tmp_path / "b", Scene([90.0]), gate=failed)
    ch = w.check(90)
    assert ch.result.reason == CALIBRATION and "5 of 6" in ch.result.detail and t.posts == []


def test_stale_frame_is_refused_before_any_call(tmp_path):
    w, cam, t, ledger, _ = make_witness(
        tmp_path, None, camera_error=CantVerify(STALE_FRAME, "frame captured before the request")
    )
    ch = w.check(90)
    assert (ch.result.verdict, ch.result.reason) == (CANT_VERIFY, STALE_FRAME)
    assert cam.calls == 1 and t.posts == [] and ledger.committed_uusd() == 0


def test_covered_marker_makes_no_call(tmp_path):
    w, cam, t, ledger, _ = make_witness(tmp_path, Scene([90.0], cover_marker=1))
    ch = w.check(90)
    assert (ch.result.verdict, ch.result.reason) == (CANT_VERIFY, MARKERS)
    assert t.posts == [] and ledger.committed_uusd() == 0 and ch.frame_jpeg != b""


# ---- one check end to end ----


def test_pass_sends_the_dial_crop_once_reserved_first_and_logs_the_pack_hash(tmp_path):
    w, cam, t, ledger, log = make_witness(tmp_path, Scene([92.0]), zone_reply("MID"))
    ch = w.check(90)
    r = ch.result
    assert (r.verdict, r.reason, r.commanded_zone, r.measured_zone) == (PASS, ZONE_MATCH, "MID", "MID")
    assert r.backend == "vision" and r.pack_sha256 == PACK.sha256 and r.arm_visible == 1
    assert r.dial_mdeg is None and r.calibrated_mdeg is None  # a zone, never an angle
    assert len(t.posts) == 1 and cam.calls == 1
    url, body, timeout = t.posts[0]
    cfg = load_vision_config()
    assert url == cfg.endpoint and timeout == cfg.timeout_s
    d = json.loads(body)
    assert d["model"] == "muse-spark-1.3" and d["instructions"] == PACK.text
    content = d["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": vision_witness.USER_TEXT}
    data_url = content[1]["image_url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    jpeg = base64.b64decode(data_url.split(",", 1)[1])
    assert jpeg.startswith(b"\xff\xd8") and hashlib.sha256(jpeg).hexdigest() == r.image_sha256
    assert r.crop_jpeg == jpeg
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert (img.shape[1], img.shape[0]) == GEOM.crop.size and max(img.shape[:2]) <= 1280
    # the crop is the dial alone: no marker is detectable in the image sent, while the same frame's plain
    # dial crop still shows marker fragments in its corners (the SAM backend's crop, unchanged)
    from ring3bridge.witness.dial import detect_markers, find_dial

    with pytest.raises(CantVerify) as e:
        detect_markers(img)
    assert e.value.reason == MARKERS and "found 0 of 4" in e.value.detail
    plain = find_dial(synth.render(Scene([92.0]))[0], GEOM).crop_img
    # the dial itself is untouched: the semicircle above the pivot is identical to the plain crop
    cx, cy = GEOM.crop.mm_to_px(0.0, 0.0)
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    inside = (np.hypot(xx - cx, yy - cy) <= 80 * GEOM.crop.px_per_mm) & (yy <= cy)
    sent_plain = cv2.imdecode(np.frombuffer(cv2.imencode(".jpg", plain, [cv2.IMWRITE_JPEG_QUALITY, 90])[1],
                                            np.uint8), cv2.IMREAD_COLOR)  # fmt: skip
    assert np.abs(img[inside].astype(int) - sent_plain[inside].astype(int)).max() <= 2
    # spend: reserved before the post (asserted at post time), settled on the reported usage after it
    assert ledger.unsettled_uusd() == 0
    assert (
        ledger.settled_uusd()
        == r.spend_uusd
        == cost_uusd(1700, cfg.price.input_uusd_per_mtok) + (cost_uusd(20, cfg.price.output_uusd_per_mtok))
    )
    recs = [json.loads(x) for x in log.path.read_text().splitlines()]
    assert [x["type"] for x in recs] == ["run_start", "spend_reserved", "model_request", "model_reply",
                                         "spend_settled"]  # fmt: skip
    req = recs[2]
    assert req["purpose"] == "witness_vision" and req["pack_sha256"] == PACK.sha256
    assert req["image_sha256"] == r.image_sha256 and req["model_id"] == cfg.id
    assert (log.blobs / req["request_blob"]).read_bytes() == body
    assert r.vision_reply == zone_reply("MID").body
    assert eventlog.verify(log.dir).ok


@pytest.mark.parametrize(
    "response,verdict,reason,zone",
    [
        (zone_reply("LOW"), FAIL, WRONG_ZONE, "LOW"),
        (zone_reply("OUT_LOW"), FAIL, OUT_OF_SCALE, "OUT_LOW"),
        (zone_reply("MID", visible=False), CANT_VERIFY, NOT_VISIBLE, ""),
        (zone_reply("UNSURE"), CANT_VERIFY, UNSURE, ""),
        (TransportResponse(200, envelope("the arm points at MID")), CANT_VERIFY, VISION_FORMAT, ""),
        (TransportResponse(200, envelope('{"zone":"MID","arm_visible":true,"angle":90}')), CANT_VERIFY,
         VISION_FORMAT, ""),
        (TransportResponse(200, envelope("", status="incomplete")), CANT_VERIFY, VISION_FORMAT, ""),
        (TransportResponse(200, b"not json"), CANT_VERIFY, VISION_FORMAT, ""),
    ],
)  # fmt: skip
def test_verdicts_from_replies_and_the_reply_is_kept(tmp_path, response, verdict, reason, zone):
    w, cam, t, ledger, log = make_witness(tmp_path, Scene([92.0]), response)
    r = w.check(90).result
    assert (r.verdict, r.reason, r.measured_zone) == (verdict, reason, zone)
    assert r.vision_reply == response.body and r.image_sha256 and r.crop_jpeg
    assert len(t.posts) == 1 and ledger.unsettled_uusd() == 0 and r.spend_uusd > 0
    if reason == VISION_FORMAT and response.body.startswith(b"{") and b"incomplete" in response.body:
        assert "incomplete" in r.detail and "max_output_tokens" in r.detail


def test_cant_verify_is_never_upgraded(tmp_path, monkeypatch):
    """Even if the judge said PASS for anything, an earlier CAN'T VERIFY (a bad reply) stands."""
    from ring3bridge.witness.verdict import Judgement

    monkeypatch.setattr(vision_witness, "judge_zone", lambda *a: Judgement(PASS, ZONE_MATCH, "MID", "MID"))
    w, *_ = make_witness(tmp_path, Scene([92.0]), TransportResponse(200, envelope("MID")))
    r = w.check(90).result
    assert (r.verdict, r.reason) == (CANT_VERIFY, VISION_FORMAT)


# ---- spend (INV-09): reservation before every call, caps enforced, failures stop ----


def test_run_cap_refuses_before_anything_is_sent(tmp_path):
    cfg = load_vision_config()
    per_call = expected_reservation(cfg, build_request(cfg, PACK.text, vision_witness.USER_TEXT,
                                                       b"\xff\xd8\xff\xd9"), b"\xff\xd8\xff\xd9")  # fmt: skip
    assert per_call > 0
    w, cam, t, ledger, log = make_witness(tmp_path, Scene([92.0]), zone_reply("MID"), budget=per_call - 1)
    with pytest.raises(ModelStop) as e:
        w.check(90)
    assert e.value.code == abi.LINK_STOP_BUDGET and t.posts == [] and ledger.committed_uusd() == 0
    assert "spend_reserved" not in log.path.read_text()
    w, cam, t, ledger, log = make_witness(
        tmp_path / "nobudget", Scene([92.0]), zone_reply("MID"), budget=None
    )
    with pytest.raises(ModelStop):
        w.check(90)
    assert t.posts == []
    w, cam, t, ledger, log = make_witness(tmp_path / "cap", Scene([92.0]), zone_reply("MID"), cap=100)
    with pytest.raises(ModelStop) as e:
        w.check(90)
    assert e.value.code == abi.LINK_STOP_BUDGET and t.posts == []


@pytest.mark.parametrize("response,code", [
    (TransportResponse(401, b'{"error":"unauthorized"}'), abi.LINK_STOP_MODEL_AUTH),
    (TransportResponse(403, b"{}"), abi.LINK_STOP_MODEL_AUTH),
    (TransportResponse(500, b"{}"), abi.LINK_STOP_TRANSPORT),
    (TransportError("timeout"), abi.LINK_STOP_TIMEOUT),
    (TransportError("network"), abi.LINK_STOP_TRANSPORT),
])  # fmt: skip
def test_auth_and_transport_failures_stop_the_run_not_cant_verify(tmp_path, response, code):
    w, cam, t, ledger, log = make_witness(tmp_path, Scene([92.0]), response, zone_reply("MID"))
    with pytest.raises(ModelStop) as e:
        w.check(90)
    assert e.value.code == code
    assert len(t.posts) == 1 and ledger.unsettled_uusd() == 0 and ledger.settled_uusd() > 0  # settled in full
    with pytest.raises(ModelStop):  # stopped for good: nothing more is sent
        w.check(90)
    assert len(t.posts) == 1


def test_no_retry_after_a_bad_reply(tmp_path):
    w, cam, t, ledger, _ = make_witness(tmp_path, Scene([92.0]), TransportResponse(200, envelope("MID")),
                                        zone_reply("MID"))  # fmt: skip
    assert w.check(90).result.reason == VISION_FORMAT
    assert len(t.posts) == 1  # the second (good) reply was never requested


# ---- the request shape (the official docs, quoted in the evidence) ----


def test_request_shape_matches_the_documented_fields():
    cfg = load_vision_config()
    jpeg = b"\xff\xd8\xff\xd9"
    body = build_request(cfg, "INSTRUCTIONS", "QUESTION", jpeg)
    d = json.loads(body)
    assert set(d) == {"model", "instructions", "input", "max_output_tokens", "store", "reasoning", "text"}
    assert d["model"] == cfg.id and d["instructions"] == "INSTRUCTIONS"
    assert d["max_output_tokens"] == cfg.max_output_tokens and d["store"] is False
    assert d["reasoning"] == {"effort": cfg.reasoning_effort}
    assert d["input"] == [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "QUESTION"},
        {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
    ]}]  # fmt: skip
    fmt = d["text"]["format"]
    assert set(fmt) == {"type", "name", "schema", "strict"}
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["name"] == vision_model.ZONE_FORMAT_NAME and len(fmt["name"]) <= 64
    schema = fmt["schema"]
    assert schema == {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "enum": list(vision_model.ZONE_VALUES)},
            "arm_visible": {"type": "boolean"},
        },
        "required": ["zone", "arm_visible"],
        "additionalProperties": False,
    }
    assert vision_model.ZONE_VALUES == ("OUT_LOW", "LOW", "MID", "HIGH", "OUT_HIGH", "UNSURE")
    assert body == json.dumps(d, sort_keys=True, separators=(",", ":")).encode()  # canonical bytes
    # the reservation counts every byte that is not the image, plus the image ceiling
    assert reserve_input_tokens(body, jpeg, cfg.image_tokens_reserve) == (
        len(body) - len(base64.b64encode(jpeg)) + cfg.image_tokens_reserve
    )
    with pytest.raises(ValueError):
        build_request(cfg, "i", "q", b"\x89PNG")  # JPEG only


def test_vision_config_is_muse_spark_at_the_published_prices():
    v, m = load_vision_config(), load_model_config()
    assert v.id == m.id == "muse-spark-1.3" and v.endpoint == m.endpoint
    assert v.price is not None and m.price is not None
    vp, mp = v.price, m.price
    assert (vp.input_uusd_per_mtok, vp.output_uusd_per_mtok, vp.cached_input_uusd_per_mtok) == (
        mp.input_uusd_per_mtok, mp.output_uusd_per_mtok, mp.cached_input_uusd_per_mtok)  # fmt: skip
    assert v.price.source == m.price.source
    assert 1000 <= v.image_tokens_reserve <= 10_000 and v.max_output_tokens >= 16
    assert v.reasoning_effort in ("minimal", "low", "medium", "high", "xhigh")


def test_pack_is_pinned_and_asks_for_the_zone_only():
    assert PACK.name == "witness-vision-v1" and len(PACK.text.encode()) < 4096
    assert PACK.sha256 == "bf8f6102e35d69ebff2792d75b959f97e7042e857e5ea0bce1a6179d679a126c"
    for z in vision_model.ZONE_VALUES:
        assert z in PACK.text
    assert '{"zone": "<Z>", "arm_visible": <true or false>}' in PACK.text
    assert "Never report an angle" in PACK.text and PACK.text.count("angle") == 1


# ---- the zone check: enabled only on 6/6 ----


def _result(deg: int, verdict: str, reason: str, zone: str = "", visible: int | None = 1) -> WitnessResult:
    cz = {50: "LOW", 90: "MID", 130: "HIGH"}[deg]
    return WitnessResult(verdict, reason, "", deg, cz, zone, None, None, "ab" * 32, "", 100,
                         backend="vision", pack_sha256=PACK.sha256, arm_visible=visible)  # fmt: skip


TARGETS = (50, 90, 130, 90, 50, 130)
ALL_PASS = [_result(d, PASS, ZONE_MATCH, {50: "LOW", 90: "MID", 130: "HIGH"}[d]) for d in TARGETS]


def test_zone_check_targets_are_two_per_zone_away_from_the_edges():
    flat = tuple(d for r in zonecheck.ZONE_TARGETS for d in r)
    assert flat == TARGETS and len(zonecheck.ZONE_TARGETS) == 2
    for d in flat:
        assert min(abs(d - e) for e in (30, 70, 110, 150)) >= 20


def test_zone_check_enables_only_on_six_of_six():
    zc = zone_check(ALL_PASS, TARGETS, "zc-1", PACK.sha256, GEOM_SHA)
    assert zc.ok and zc.passed == 6 and zc.n == 6 and zc.reason == ""
    assert "enabled" in zc.message() and "6 of 6" in zc.message()
    for i, bad in [(5, _result(130, CANT_VERIFY, UNSURE)), (0, _result(50, FAIL, WRONG_ZONE, "MID")),
                   (2, _result(130, CANT_VERIFY, NOT_VISIBLE, visible=0))]:  # fmt: skip
        rs = list(ALL_PASS)
        rs[i] = bad
        zc = zone_check(rs, TARGETS, "zc-2", PACK.sha256, GEOM_SHA)
        assert not zc.ok and zc.passed == 5 and zc.n == 6
        assert "5 of 6" in zc.reason and bad.reason in zc.reason and str(bad.commanded_deg) in zc.reason
        assert "disabled" in zc.message()
    zc = zone_check(ALL_PASS[:5], TARGETS, "zc-3", PACK.sha256, GEOM_SHA)  # one image short
    assert not zc.ok and zc.passed == 5 and "5 of 6" in zc.reason
    zc = zone_check([], TARGETS, "zc-4", PACK.sha256, GEOM_SHA)
    assert not zc.ok and zc.passed == 0 and "0 of 6" in zc.reason
    wrong_order = [ALL_PASS[1], ALL_PASS[0], *ALL_PASS[2:]]
    assert not zone_check(wrong_order, TARGETS, "zc-5", PACK.sha256, GEOM_SHA).ok
    # a PASS from another backend or pack does not count
    sam_pass = [WitnessResult(PASS, ZONE_MATCH, "", d, r.commanded_zone, r.measured_zone, 1, 1, "cd" * 32,
                              "", 2500) for d, r in zip(TARGETS, ALL_PASS, strict=True)]  # fmt: skip
    assert not zone_check(sam_pass, TARGETS, "zc-6", PACK.sha256, GEOM_SHA).ok
    other_pack = [WitnessResult(PASS, ZONE_MATCH, "", r.commanded_deg, r.commanded_zone, r.measured_zone,
                                None, None, r.image_sha256, "", 100, backend="vision",
                                pack_sha256="ef" * 32, arm_visible=1) for r in ALL_PASS]  # fmt: skip
    assert not zone_check(other_pack, TARGETS, "zc-7", PACK.sha256, GEOM_SHA).ok


def _zc_run(tmp_path, results, name="zc-run") -> Path:
    """What ``ring3 calibrate --witness-backend vision`` leaves behind: a verified run with the record."""
    d = tmp_path / name
    log = EventLog(d)
    log.append("run_start", run_id=name)
    zc = zone_check(results, TARGETS, name, PACK.sha256, GEOM_SHA)
    doc = zonecheck.zonecheck_doc(zc)
    sha = zonecheck.write_zonecheck(d, doc)
    log.append("zonecheck", **{k: v for k, v in doc.items() if k != "results"}, file_sha256=sha)
    log.append("run_end", records=log.seq)
    return d


def test_zonecheck_file_round_trip_tampering_and_other_pack_are_refused(tmp_path):
    d = _zc_run(tmp_path, ALL_PASS)
    zc, doc, sha = zonecheck.load_zonecheck(d, PACK.sha256, GEOM_SHA)
    assert zc.ok and zc.passed == 6 and doc["run_id"] == "zc-run" and len(sha) == 64
    assert eventlog.check_values(doc) is None  # integers only, like every record
    # a disabled check stays disabled
    d2 = _zc_run(tmp_path, ALL_PASS[:5] + [_result(130, CANT_VERIFY, UNSURE)], "zc-bad")
    zc2, _, _ = zonecheck.load_zonecheck(d2, PACK.sha256, GEOM_SHA)
    assert not zc2.ok and "5 of 6" in zc2.reason
    # another pack or another gauge: refused
    for args in [("ff" * 32, GEOM_SHA), (PACK.sha256, "ff" * 32)]:
        with pytest.raises(zonecheck.ZoneCheckError):
            zonecheck.load_zonecheck(d, *args)
    # the file edited to say ok on 5 of 6: the stored result rows decide, not the flag
    p = d / zonecheck.FILE
    bad = json.loads(p.read_text())
    bad["results"][0][1] = CANT_VERIFY
    p.write_text(eventlog.canonical(bad) + "\n")
    with pytest.raises(zonecheck.ZoneCheckError):
        zonecheck.load_zonecheck(d, PACK.sha256, GEOM_SHA)
    p.write_text(eventlog.canonical(json.loads(p.read_text()) | {"ok": 1}) + "\n")
    with pytest.raises(zonecheck.ZoneCheckError):
        zonecheck.load_zonecheck(d, PACK.sha256, GEOM_SHA)


def test_script_rounds_give_each_request_its_own_targets():
    from ring3bridge.calibration import CalibrationScript
    from ring3bridge.strictjson import Call, Done, validate
    from tests.witness.test_part_b import _Req, _Runner

    class W:
        def check(self, deg: int):
            from ring3bridge.witness.service import Checked

            return Checked(_result(deg, PASS, ZONE_MATCH, "MID"), b"")

    def drive(script: CalibrationScript, start: int) -> tuple[list[int], str]:
        req = script.runner.request
        moves = []
        for _ in range(40):
            v = validate(script.ask("", "").text)
            if isinstance(v, Done):
                return moves, v.summary
            assert isinstance(v, Call)
            if v.op == "servo_set":
                moves.append(int(v.args["deg"]))
                req.results.append(("servo_set", 0))
            else:
                script.measure(moves[-1])
                req.results.append(("witness", 0))
        raise AssertionError("the script did not end")

    s = CalibrationScript(W(), checks=True, rounds=zonecheck.ZONE_TARGETS, move_gap_s=0)  # type: ignore[arg-type]
    s.runner = _Runner(90)
    s.runner.request.index = 1
    moves, summary = drive(s, 90)
    assert "3 witness checks" in summary
    assert moves == [60, 50, 80, 90, 120, 130] and [d for d, m in s.moves if m] == [50, 90, 130]
    s.runner.request = _Req()  # the second console request starts where the first ended
    s.runner.request.index = 2
    s.runner.tele_servo = 130
    moves, summary = drive(s, 130)
    assert moves == [100, 90, 60, 50, 80, 110, 130] and len(moves) <= abi.ACTIONS_PER_REQUEST - 1
    assert [d for d, m in s.moves if m] == [90, 50, 130]
    assert [ch.result.commanded_deg for _, _, ch in s.results] == list(TARGETS)
    assert s.max_actions() == abi.ACTIONS_PER_REQUEST + 3
    s.runner.request = _Req()  # a third request repeats the last round
    s.runner.request.index = 3
    assert s.targets_for(3) == (90, 50, 130) and s.targets_for(1) == (50, 90, 130)


# ---- the observation names the backend ----


def test_observation_text_names_the_backend():
    out = run.witness_output(_result(90, PASS, ZONE_MATCH, "MID")).decode()
    assert out.splitlines()[0] == PASS
    assert "backend=vision" in out and "measured_deg=none" in out and "measured_zone=MID" in out
    sam = WitnessResult(PASS, ZONE_MATCH, "", 50, "LOW", "LOW", 52_000, 50_000, "", "", 2500)
    assert "backend=sam" in run.witness_output(sam).decode()
    nv = _result(90, CANT_VERIFY, NOT_VISIBLE, visible=0)
    assert run.witness_output(nv).decode().startswith("CAN'T VERIFY\nreason=NOT_VISIBLE")


# ---- CLI (no network, no key) ----


def test_calibrate_vision_dry_run_prints_six_images_and_the_reservation(capsys, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    rc = cli.main(["calibrate", "--witness-backend", "vision", "--budget-usd", "0.10", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "zone check" in out and "6 images" in out and "muse-spark-1.3" in out
    assert "witness-vision-v1" in out and PACK.sha256[:16] in out
    assert "50, 90, 130" in out and "90, 50, 130" in out
    assert "reserved before each call" in out and "$0.1" in out
    rc = cli.main(["calibrate", "--witness-backend", "vision", "--budget-usd", "0.10"])
    assert rc == 2 and "--witness-dir" in capsys.readouterr().err


def test_run_dry_run_names_the_vision_backend(capsys, tmp_path):
    rc = cli.main(["run", "--budget-usd", "0.50", "--dry-run", "--pack", "agent-v3", "--witness-dir",
                   str(tmp_path), "--witness-backend", "vision"])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "witness: vision" in out and "muse-spark-1.3" in out and "zone check" in out
    rc = cli.main(["run", "--budget-usd", "0.50", "--dry-run", "--pack", "agent-v3", "--witness-dir",
                   str(tmp_path)])  # fmt: skip
    assert rc == 0 and "sam-3.1" in capsys.readouterr().out  # the default backend is unchanged


def test_witness_backend_choices():
    with pytest.raises(SystemExit):
        cli.main(["run", "--budget-usd", "0.50", "--dry-run", "--witness-backend", "sam3"])
