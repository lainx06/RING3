"""T-16: the vision adapter uses the real ``HttpsTransport`` against a fake ``_urlopen`` that echoes a
synthetic key (plain and as JSON escapes). The key then appears nowhere: not in the returned reply, not in
the witness record's reply bytes, not in any event or blob, not in the exception of a 401. The request
headers are never logged (INV-09, T-09 F2; the redaction path is the one ``muse_spark.py`` uses)."""

import email.message
import json
import urllib.error
from pathlib import Path

import pytest

from ring3bridge.config import load_vision_config
from ring3bridge.eventlog import EventLog, verify
from ring3bridge.ledger import Ledger
from ring3bridge.models import transport as transport_mod
from ring3bridge.models.muse_spark import ModelStop
from ring3bridge.models.transport import HttpsTransport
from ring3bridge.models.vision import VisionClient
from tests.bridge.test_t09_f2_redaction import _Resp, esc, key, nowhere, strings_in  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + bytes(64) + b"\xff\xd9"


def envelope(text_literal: str, extra: str = "") -> bytes:
    return (
        '{"id":"resp_' + extra + '","object":"response","status":"completed","model":"muse-spark-1.3",'
        '"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"'
        + text_literal
        + '"}]}],"usage":{"input_tokens":1800,"output_tokens":12,'
        '"input_tokens_details":{"cached_tokens":0}}}'
    ).encode()


def test_key_nowhere_in_vision_events_blobs_replies_or_exceptions(tmp_path, key, monkeypatch):  # noqa: F811
    bodies = [
        envelope(esc(key)),
        envelope(esc(key, upper=True, every=2), extra=esc(key, upper=True)),
        envelope(json.dumps('{"zone":"' + esc(key) + '","arm_visible":true}')[1:-1]),
        envelope(json.dumps('{"zone":"MID","arm_visible":true,"k":"' + key + '"}')[1:-1]),
    ]
    calls = []
    seen_headers = []

    def hostile_urlopen(req, timeout):
        calls.append(1)
        seen_headers.append(dict(req.header_items()))
        if len(calls) <= len(bodies):
            return _Resp(200, bodies[len(calls) - 1])
        hdrs = email.message.Message()
        raise urllib.error.HTTPError(req.full_url, 401, "bad key", hdrs, _Resp(401, envelope(esc(key))))

    monkeypatch.setattr(transport_mod, "_urlopen", hostile_urlopen)
    cfg = load_vision_config()
    transport = HttpsTransport()
    log = EventLog(tmp_path / "runs" / "r1")
    log.append("run_start", run_id="r1", model_id=cfg.id)
    ledger = Ledger(30_000_000, tmp_path / "ledger.jsonl")
    client = VisionClient(cfg, transport, ledger, log, "r1", 5_000_000)
    texts: list[str] = []
    for _ in bodies:
        reply = client.ask("ctx", "question", JPEG)
        assert reply.status == "completed" and reply.texts == 1
        texts += [reply.text, reply.body.decode("utf-8", "replace"), repr(reply)]
    with pytest.raises(ModelStop) as e:
        client.ask("ctx", "question", JPEG)
    texts += [str(e.value), repr(e.value), repr(client), repr(transport)]
    assert any("Authorization" in h for h in seen_headers)  # the header went to the fake server only
    for t in texts:
        assert nowhere(key, t), t
    events = (log.dir / "events.jsonl").read_text()
    assert nowhere(key, events) and "Authorization" not in events and "Bearer" not in events
    replies = [json.loads(ln) for ln in events.splitlines() if '"type":"model_reply"' in ln]
    assert len(replies) == len(bodies) + 1
    for p in sorted(q for q in Path(log.blobs).iterdir() if q.is_file()):
        raw = p.read_bytes()
        assert nowhere(key, raw.decode("utf-8", "replace")), p.name
        try:
            decoded = json.loads(raw)
        except ValueError:
            continue
        for s in strings_in(decoded):
            assert nowhere(key, s), p.name
    assert verify(log.dir).ok and ledger.unsettled_uusd() == 0
