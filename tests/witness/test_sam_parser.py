"""SAM record-line and envelope parser (T-07 step 3): strict, bounded, never guesses."""

import json

import pytest

from ring3bridge.witness import SAM_FORMAT, CantVerify
from ring3bridge.witness.sam import MAX_RECORDS, build_request, parse_envelope, parse_line

W, H = 920, 570
MASK = f"<|mask;x=0;y=0;c=0.9;data={H},{W},a85:9jqo^BlbD-BleB1DJ+*+F(f,q|>"
BOX = "<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=0.93|>"
REC = f"1 {BOX}{MASK}"


def rec(i=1, box=BOX, mask=MASK):
    return f"{i} {box}{mask}"


ACCEPT = [
    ("one record", REC, 1),
    ("trailing newline", REC + "\n", 1),
    ("trailing crlf", REC + "\r\n", 1),
    ("two records, comma", rec(1) + "," + rec(2), 2),
    ("two records, comma space", rec(1) + ", " + rec(2), 2),
    ("no confidence", "3 <|box;x1=1;y1=2;x2=3;y2=4;w=2;h=2|><|mask;x=0;y=0;data=4,4,zz|>", 1),
    ("c = 1", rec(box="<|box;x1=0;y1=0;x2=5;y2=5;w=5;h=5;c=1|>"), 1),
    ("c = 1.000000", rec(box="<|box;x1=0;y1=0;x2=5;y2=5;w=5;h=5;c=1.000000|>"), 1),
    ("c = 0", rec(box="<|box;x1=0;y1=0;x2=5;y2=5;w=5;h=5;c=0|>"), 1),
    ("w off by one (inclusive convention)", rec(box="<|box;x1=10;y1=10;x2=20;y2=20;w=11;h=11|>"), 1),
    ("box touches image edge", rec(box=f"<|box;x1=0;y1=0;x2={W};y2={H};w={W};h={H}|>"), 1),
    ("payload holds commas", rec(mask="<|mask;x=0;y=0;data=4,4,a,b,c,,d|>"), 1),
    ("payload holds '|>' and ',2<|'", rec(mask="<|mask;x=0;y=0;data=4,4,ab|>cd,2<|ef|>"), 1),
    ("payload ending in '|' then the record end", rec(mask="<|mask;x=0;y=0;data=4,4,ab||>"), 1),
    ("payload with '<|box' inside", rec(1, mask="<|mask;x=0;y=0;data=4,4,q<|box;x1=1|>") + "," + rec(2), 2),
    (f"{MAX_RECORDS} records", ",".join(rec(i) for i in range(1, MAX_RECORDS + 1)), MAX_RECORDS),
]

REJECT = [
    ("garbage", "the arm is at 90 degrees"),
    ("json instead of records", json.dumps({"box": [1, 2, 3, 4]})),
    ("missing id", f"{BOX}{MASK}"),
    ("negative id", f"-1 {BOX}{MASK}"),
    ("id with leading zero", f"01 {BOX}{MASK}"),
    ("two spaces after id", f"1  {BOX}{MASK}"),
    ("missing mask", f"1 {BOX}"),
    ("missing box", f"1 {MASK}"),
    ("mask before box", f"1 {MASK}{BOX}"),
    ("float coordinate", rec(box="<|box;x1=440.5;y1=320;x2=480;y2=430;w=40;h=110|>")),
    ("negative coordinate", rec(box="<|box;x1=-4;y1=320;x2=480;y2=430;w=484;h=110|>")),
    ("leading zero coordinate", rec(box="<|box;x1=040;y1=320;x2=480;y2=430;w=440;h=110|>")),
    ("exponent coordinate", rec(box="<|box;x1=4e2;y1=320;x2=480;y2=430;w=80;h=110|>")),
    ("seven-digit coordinate", rec(box="<|box;x1=1;y1=1;x2=1000000;y2=4;w=999999;h=3|>")),
    ("box keys reordered", rec(box="<|box;y1=320;x1=440;x2=480;y2=430;w=40;h=110|>")),
    ("box key missing", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40|>")),
    ("box extra key", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;z=1|>")),
    ("c above 1", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=1.2|>")),
    ("c = 1.5", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=1.5|>")),
    ("c negative", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=-0.1|>")),
    ("c without leading digit", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=.9|>")),
    ("c exponent", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=9e-1|>")),
    ("c seven decimals", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=0.1234567|>")),
    ("c NaN", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=110;c=nan|>")),
    ("x1 >= x2", rec(box="<|box;x1=480;y1=320;x2=480;y2=430;w=0;h=110|>")),
    ("y1 > y2", rec(box="<|box;x1=440;y1=431;x2=480;y2=430;w=40;h=1|>")),
    ("box beyond image width", rec(box=f"<|box;x1=10;y1=10;x2={W + 1};y2=20;w={W - 9};h=10|>")),
    ("box beyond image height", rec(box=f"<|box;x1=10;y1=10;x2=20;y2={H + 5};w=10;h={H - 5}|>")),
    ("w disagrees", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=90;h=110|>")),
    ("h disagrees", rec(box="<|box;x1=440;y1=320;x2=480;y2=430;w=40;h=10|>")),
    ("mask without data", rec(mask="<|mask;x=0;y=0;c=0.9|>")),
    ("mask data without W", rec(mask="<|mask;x=0;y=0;data=570,payload|>")),
    ("mask data empty payload", rec(mask="<|mask;x=0;y=0;data=570,920,|>")),
    ("mask zero height", rec(mask="<|mask;x=0;y=0;data=0,920,abc|>")),
    ("mask huge width", rec(mask="<|mask;x=0;y=0;data=570,99999,abc|>")),
    ("mask payload with space", rec(mask="<|mask;x=0;y=0;data=4,4,ab cd|>")),
    ("mask unterminated", rec(mask="<|mask;x=0;y=0;data=4,4,abcd")),
    ("duplicate object id", rec(1) + "," + rec(1)),
    ("semicolon separator", rec(1) + ";" + rec(2)),
    ("trailing comma", rec(1) + ","),
    ("trailing text", rec(1) + " done"),
    ("two lines", rec(1) + "\n" + rec(2)),
    ("tab", rec(1).replace(" ", "\t", 1)),
    ("non-ASCII", rec(1).replace("box", "böx")),
    ("NUL byte", rec(1) + "\x00"),
    ("too many records", ",".join(rec(i) for i in range(1, MAX_RECORDS + 2))),
    ("only whitespace", "   "),
    ("only a newline pair", "\n\n"),
]


@pytest.mark.parametrize("name,line,n", ACCEPT, ids=[a[0] for a in ACCEPT])
def test_parser_accepts(name, line, n):
    assert len(parse_line(line, W, H)) == n


@pytest.mark.parametrize("name,line", REJECT, ids=[r[0] for r in REJECT])
def test_parser_rejects(name, line):
    with pytest.raises(CantVerify) as e:
        parse_line(line, W, H)
    assert e.value.reason == SAM_FORMAT


def test_parser_reads_fields_as_integers():
    (r,) = parse_line(REC, W, H)
    assert (r.obj_id, r.x1, r.y1, r.x2, r.y2) == (1, 440, 320, 480, 430)
    assert r.c_ppm == 930_000 and r.mask_c_ppm == 900_000
    assert (r.mask_h, r.mask_w) == (H, W)
    assert all(type(v) is int for v in (r.x1, r.y1, r.x2, r.y2, r.c_ppm, r.mask_c_ppm))


def test_empty_line_is_no_records():
    assert parse_line("", W, H) == []
    assert parse_line("\n", W, H) == []


def test_parser_is_bounded():
    with pytest.raises(CantVerify):
        parse_line("1 " + "x" * (1 << 21), W, H)
    big = rec(mask="<|mask;x=0;y=0;data=4,4," + "a" * 500_000 + "|>")  # long payload: linear, not stuck
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

    assert parse_envelope(envelope(REC)) == REC
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
