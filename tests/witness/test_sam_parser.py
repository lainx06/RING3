"""SAM record-line and envelope parser (T-07 step 3): strict, bounded, never guesses."""

import json
from pathlib import Path

import pytest

from ring3bridge.witness import SAM_FORMAT, CantVerify
from ring3bridge.witness.sam import MAX_RECORDS, build_request, parse_envelope, parse_line
from ring3bridge.witness.sam import PAYLOAD_ALPHABET as ALPHABET

W, H = 920, 570
# Official grammar (dev.meta.ai/docs/sam/reading-segmentation): FRAME record ("," record)*; the id directly
# before <|box; w/h = image size; x2/y2 inclusive; mask data=H,W with H = y2-y1+1, W = x2-x1+1; enc ~ or !.
BOX = f"<|box;x1=440;y1=320;x2=480;y2=430;w={W};h={H};c=0.93|>"
MASK = "<|mask;x=0;y=0;c=0.9;data=111,41,~9jqo^BlbD-BleB1DJ+*+F(fq|>"
REC = f"0{BOX}{MASK}"
LINE = "<0f>" + REC


def box(x1=440, y1=320, x2=480, y2=430, w=W, h=H, c=";c=0.93"):
    return f"<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}{c}|>"


def mask(mh=111, mw=41, enc="~", payload="9jqo^Bl", c=";c=0.9", xy="x=0;y=0"):
    return f"<|mask;{xy}{c};data={mh},{mw},{enc}{payload}|>"


def rec(i=0, b=None, m=None):
    return f"{i}{b or BOX}{m or MASK}"


def line(*recs, frame="<0f>"):
    return frame + ",".join(recs)


ACCEPT = [
    ("one record", LINE, 1),
    ("trailing newline", LINE + "\n", 1),
    ("trailing crlf", LINE + "\r\n", 1),
    ("two records", line(rec(0), rec(1)), 2),
    ("no confidence", line(rec(3, box(1, 2, 3, 4, c=""), mask(3, 3, c=""))), 1),
    ("c = 1", line(rec(b=box(c=";c=1"))), 1),
    ("c = 1.000000", line(rec(b=box(c=";c=1.000000"))), 1),
    ("c = 0", line(rec(b=box(c=";c=0"))), 1),
    ("c with 16 digits (as recorded)", line(rec(b=box(c=";c=0.8672199249267578"))), 1),
    ("one-pixel box", line(rec(b=box(5, 5, 5, 5), m=mask(1, 1))), 1),
    ("box on the last pixel", line(rec(b=box(0, 0, W - 1, H - 1), m=mask(H, W))), 1),
    ("one-bit encoding", line(rec(m=mask(enc="!"))), 1),
    ("payload uses the whole 85-character alphabet", line(rec(m=mask(payload="".join(sorted(ALPHABET))))), 1),
    (f"{MAX_RECORDS} records", line(*(rec(i) for i in range(MAX_RECORDS))), MAX_RECORDS),
]

REJECT = [
    ("garbage", "the arm is at 90 degrees"),
    ("json instead of records", json.dumps({"box": [1, 2, 3, 4]})),
    ("no frame marker (the card's old grammar)", REC),
    (
        "old grammar: space after the id, box-size w/h",
        "<0f>0 <|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110|>" + MASK,
    ),  # fmt: skip
    ("frame 1 (we sent one image)", line(REC, frame="<1f>")),
    ("frame with leading zero", line(REC, frame="<00f>")),
    ("frame marker only", "<0f>"),
    ("two frames", LINE + LINE),
    ("missing id", line(BOX + MASK)),
    ("negative id", line(rec(-1))),
    ("id with leading zero", line("01" + BOX + MASK)),
    ("space after id", line("0 " + BOX + MASK)),
    ("missing mask", line("0" + BOX)),
    ("missing box", line("0" + MASK)),
    ("mask before box", line("0" + MASK + BOX)),
    ("float coordinate", line(rec(b="<|box;x1=440.5;y1=320;x2=480;y2=430;w=920;h=570|>"))),
    ("negative coordinate", line(rec(b=box(x1=-4)))),
    ("leading zero coordinate", line(rec(b="<|box;x1=040;y1=320;x2=480;y2=430;w=920;h=570|>"))),
    ("exponent coordinate", line(rec(b="<|box;x1=4e2;y1=320;x2=480;y2=430;w=920;h=570|>"))),
    ("seven-digit coordinate", line(rec(b=box(x2=1000000)))),
    ("box keys reordered", line(rec(b="<|box;y1=320;x1=440;x2=480;y2=430;w=920;h=570|>"))),
    ("box key missing", line(rec(b="<|box;x1=440;y1=320;x2=480;y2=430;w=920|>"))),
    ("box extra key", line(rec(b="<|box;x1=440;y1=320;x2=480;y2=430;w=920;h=570;z=1|>"))),
    ("w is not the image width", line(rec(b=box(w=40)))),
    ("h is not the image height", line(rec(b=box(h=110)))),
    ("c above 1", line(rec(b=box(c=";c=1.2")))),
    ("c = 1.5", line(rec(b=box(c=";c=1.5")))),
    ("c negative", line(rec(b=box(c=";c=-0.1")))),
    ("c without leading digit", line(rec(b=box(c=";c=.9")))),
    ("c exponent", line(rec(b=box(c=";c=9e-1")))),
    ("c 21 decimals", line(rec(b=box(c=";c=0." + "1" * 21)))),
    ("c NaN", line(rec(b=box(c=";c=nan")))),
    ("x1 > x2", line(rec(b=box(x1=481), m=mask(111, 1)))),
    ("y1 > y2", line(rec(b=box(y1=431), m=mask(1, 41)))),
    ("x2 = image width (inclusive corner off the image)", line(rec(b=box(x2=W), m=mask(111, W - 440 + 1)))),
    ("y2 = image height", line(rec(b=box(y2=H), m=mask(H - 320 + 1, 41)))),
    ("mask height disagrees with the box", line(rec(m=mask(110, 41)))),
    ("mask width disagrees with the box (exclusive reading)", line(rec(m=mask(111, 40)))),
    ("mask origin not 0", line(rec(m=mask(xy="x=1;y=0")))),
    ("mask without data", line(rec(m="<|mask;x=0;y=0;c=0.9|>"))),
    ("mask data without W", line(rec(m="<|mask;x=0;y=0;data=111,~payload|>"))),
    ("mask unknown encoding marker", line(rec(m=mask(enc="a85:")))),
    ("mask empty payload", line(rec(m="<|mask;x=0;y=0;data=111,41,~|>"))),
    ("mask payload with space", line(rec(m=mask(payload="ab cd")))),
    ("payload holds a comma", line(rec(m=mask(payload="a,b")))),
    ("payload holds '|>' (would swallow the next record)", line(rec(m=mask(payload="ab|>cd")))),
    ("payload ending in '|'", line(rec(m=mask(payload="ab|")))),
    ("payload holds '<|box'", line(rec(0, m=mask(payload="q<|box;x1=1")), rec(1))),
    ("payload holds '}'", line(rec(m=mask(payload="ab}cd")))),
    ("mask unterminated", line(rec(m="<|mask;x=0;y=0;data=111,41,~abcd"))),
    ("duplicate object id", line(rec(1), rec(1))),
    ("comma space separator", "<0f>" + rec(0) + ", " + rec(1)),
    ("semicolon separator", "<0f>" + rec(0) + ";" + rec(1)),
    ("trailing comma", line(rec(0)) + ","),
    ("trailing text", line(rec(0)) + " done"),
    ("two lines", line(rec(0)) + "\n" + line(rec(1))),
    ("tab", line(rec(0)).replace(";", "\t", 1)),
    ("non-ASCII", line(rec(0)).replace("box", "b\u00f6x")),
    ("NUL byte", line(rec(0)) + "\x00"),
    ("too many records", line(*(rec(i) for i in range(MAX_RECORDS + 1)))),
    ("only whitespace", "   "),
    ("only a newline pair", "\n\n"),
]


@pytest.mark.parametrize("name,text,n", ACCEPT, ids=[a[0] for a in ACCEPT])
def test_parser_accepts(name, text, n):
    assert len(parse_line(text, W, H)) == n


@pytest.mark.parametrize("name,text", REJECT, ids=[r[0] for r in REJECT])
def test_parser_rejects(name, text):
    with pytest.raises(CantVerify) as e:
        parse_line(text, W, H)
    assert e.value.reason == SAM_FORMAT


def test_parser_reads_fields_as_integers():
    (r,) = parse_line(LINE, W, H)
    # SAM's inclusive bottom-right corner (480, 430) is stored as the exclusive edge (481, 431)
    assert (r.obj_id, r.x1, r.y1, r.x2, r.y2) == (0, 440, 320, 481, 431)
    assert r.c_ppm == 930_000 and r.mask_c_ppm == 900_000
    assert (r.mask_h, r.mask_w) == (111, 41)
    assert all(type(v) is int for v in (r.x1, r.y1, r.x2, r.y2, r.c_ppm, r.mask_c_ppm))


def test_empty_line_is_no_records():
    assert parse_line("", W, H) == []
    assert parse_line("\n", W, H) == []


def test_parser_is_bounded():
    with pytest.raises(CantVerify):
        parse_line("<0f>0" + "x" * (1 << 21), W, H)
    big = line(rec(m=mask(payload="a" * 500_000)))  # long payload: linear, not stuck
    assert len(parse_line(big, W, H)) == 1


ENV_REJECT = [
    ("not json", b"<html>"),
    ("json list", b"[]"),
    ("status incomplete", json.dumps({"status": "incomplete", "output": []}).encode()),
    ("status missing", json.dumps({"output": []}).encode()),
    ("output missing", json.dumps({"status": "completed"}).encode()),
    ("no output_text", json.dumps({"status": "completed", "output": []}).encode()),
    (
        "two output_text",
        json.dumps(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": ""}, {"type": "output_text", "text": ""}],
                    }
                ],
            }
        ).encode(),  # fmt: skip
    ),
    (
        "text not a string",
        json.dumps(
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": 5}]}],
            }
        ).encode(),  # fmt: skip
    ),
]


@pytest.mark.parametrize("name,body", ENV_REJECT, ids=[e[0] for e in ENV_REJECT])
def test_envelope_rejects(name, body):
    with pytest.raises(CantVerify) as e:
        parse_envelope(body)
    assert e.value.reason == SAM_FORMAT


def test_envelope_reads_the_line():
    from tests.witness.synth import envelope

    assert parse_envelope(envelope(LINE)) == LINE
    assert parse_envelope(envelope("")) == ""


def test_request_shape():
    jpeg = b"\xff\xd8\xff\xe0fakejpeg"
    body = json.loads(build_request("sam-3.1", "white plastic arm", jpeg))
    assert body["model"] == "sam-3.1" and body["stream"] is False
    assert body["metadata"] == {"include_confidence": "true"}
    (msg,) = body["input"]
    assert msg["role"] == "user"
    text, image = msg["content"]
    assert text == {"type": "input_text", "text": "white plastic arm"}
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/jpeg;base64,")
    import base64

    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == jpeg


@pytest.mark.parametrize("prompt", ["", "x" * 65, "arm\n", "bras é"])
def test_request_rejects_bad_prompts(prompt):
    with pytest.raises(ValueError):
        build_request("sam-3.1", prompt, b"\xff\xd8x")


def test_request_rejects_non_jpeg():
    with pytest.raises(ValueError):
        build_request("sam-3.1", "arm", b"\x89PNG")


# ---- recorded real replies (tests/witness/recorded/, user's probes 2026-11-03, full 920 x 570 crop) ----

RECORDED = Path(__file__).parent / "recorded"


def _recorded(name: str) -> list:
    return parse_line(parse_envelope((RECORDED / name).read_bytes()), W, H)


def test_recorded_empty_reply_is_concept_not_present():
    assert _recorded("sam-empty-black-plastic-arm.json") == []


def test_recorded_servo_reply_parses():
    (r,) = _recorded("sam-blue-servo-motor.json")
    assert (r.obj_id, r.x1, r.y1, r.x2, r.y2) == (0, 393, 425, 564, 570)
    assert (r.mask_h, r.mask_w) == (145, 171) and r.c_ppm == 867_219 and r.mask_c_ppm == 867_219


def test_recorded_square_reply_lands_on_the_printed_markers():
    """The real boxes, through the same box -> gauge mm mapping the arm angle uses, cover the ArUco markers
    printed at the positions in geometry.json (clipped by the crop): the inclusive-corner reading is right."""
    from ring3bridge.witness.angle import box_mm
    from ring3bridge.witness.dial import load_geometry

    geom = load_geometry()
    recs = _recorded("sam-black-square.json")
    assert [r.obj_id for r in recs] == [0, 1, 2, 3]
    c = geom.crop

    def expected(mid):  # the marker's extent in gauge mm, clipped to the crop
        a = geom.marker_corners[mid]
        return (max(a[:, 0].min(), c.x_min), max(a[:, 1].min(), c.y_min),
                min(a[:, 0].max(), c.x_max), min(a[:, 1].max(), c.y_max))  # fmt: skip

    for rec_id, mid in ((1, 2), (2, 1)):  # whole markers 2 (bottom right) and 1 (top right)
        got = box_mm(recs[rec_id], c)
        assert all(abs(g - e) <= 1.0 for g, e in zip(got, expected(mid), strict=True)), (rec_id, got)
    for rec_id in (0, 3):  # single 5 mm cells of marker 0 (30 mm = 6 cells)
        x0, y0, x1, y1 = box_mm(recs[rec_id], c)
        m0 = expected(0)
        assert m0[0] <= x0 and x1 <= m0[2] and m0[1] <= y0 and y1 <= m0[3] + 0.2
        assert abs((x1 - x0) - 5.0) <= 0.4


def test_recorded_black_bar_reply_holds_the_arm_and_the_filter_stays_strict():
    """probe-20261103-220356 (upright mount, arm taped black at 90 deg): with "black bar", exactly one of the
    11 records lies near the pivot and points at ~90 deg: the arm. Its near end is 5.5 mm out (the tape
    stopped at the white hub), so the pivot-in-box filter still refuses it: SHAPE, not relaxed."""
    import math

    from ring3bridge.witness import SHAPE
    from ring3bridge.witness.angle import arm_angle, box_mm
    from ring3bridge.witness.dial import load_geometry
    from ring3bridge.witness.sam import DEFAULT_PROMPT

    assert DEFAULT_PROMPT == "black bar"
    crop = load_geometry().crop
    recs = _recorded("sam-black-bar-upright.json")
    assert len(recs) == 11

    def gap(b):  # distance from the pivot to the box (0 when inside)
        dx = b[0] if b[0] > 0 else (-b[2] if b[2] < 0 else 0.0)
        dy = b[1] if b[1] > 0 else (-b[3] if b[3] < 0 else 0.0)
        return math.hypot(dx, dy)

    near = [(r, box_mm(r, crop)) for r in recs if gap(box_mm(r, crop)) < 10.0]
    assert len(near) == 1  # the bands and markers are all >= 54 mm away
    r, b = near[0]
    assert r.obj_id == 4 and abs(gap(b) - 5.5) < 0.3
    assert abs(math.degrees(math.atan2((b[1] + b[3]) / 2, (b[0] + b[2]) / 2)) - 88.3) < 1.0
    with pytest.raises(CantVerify) as e:
        arm_angle(recs, crop)
    assert e.value.reason == SHAPE and "4: pivot not inside the box" in e.value.detail
