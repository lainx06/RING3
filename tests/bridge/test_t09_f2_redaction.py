"""T-09 F2 (Codex Gate A, D-74): a key written with JSON escapes in the API envelope, or inside the action
JSON the model embeds in its text, is redacted at the decoded level. The real ``HttpsTransport`` runs
against a fake ``_urlopen`` that echoes a synthetic key (never ``MODEL_API_KEY``'s real value) as plain
text, as ``\\u00XX`` escapes with lower- and upper-case hex, with only some characters escaped, with the
letter case swapped, in the envelope's own fields, in the reply text and double-escaped inside the embedded
action JSON. The key then appears nowhere: not in any blob (raw or decoded), not in the events, not in the
returned reply, not in the validated action (the FINAL text and the timeline line come from it), not in the
exception text of a 401 whose body carries it. On the code before T-09 the escaped forms survive and the
validated summary is the key."""

import email.message
import json
import secrets
import urllib.error
from pathlib import Path

import pytest

from ring3bridge.eventlog import EventLog, verify
from ring3bridge.ledger import Ledger
from ring3bridge.models import transport as transport_mod
from ring3bridge.models.muse_spark import ModelStop, MuseSparkClient
from ring3bridge.models.transport import HttpsTransport
from ring3bridge.strictjson import Done, Refuse, validate
from tests.bridge.test_inv_09_model import CFG
from tests.bridge.test_inv_09_model import _Resp as _BaseResp


class _Resp(_BaseResp):
    def close(self) -> None:  # HTTPError's cleanup closes its file object
        pass


def esc(s: str, every: int = 1, upper: bool = False) -> str:
    """``s`` as JSON ``\\u00XX`` escapes for every ``every``-th character (all of them by default)."""
    out = []
    for i, ch in enumerate(s):
        if i % every == 0:
            h = f"{ord(ch):04x}"
            out.append("\\u" + (h.upper() if upper else h))
        else:
            out.append(ch)
    return "".join(out)


def envelope(text_literal: str, extra: str = "") -> bytes:
    """A Responses API reply built by hand so the escapes inside are exactly the ones written."""
    return (
        '{"id":"resp_' + extra + '","object":"response","status":"completed","model":"' + CFG.id + '",'
        '"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"'
        + text_literal
        + '"}]}],"usage":{"input_tokens":100,"output_tokens":12,'
        '"input_tokens_details":{"cached_tokens":0}}}'
    ).encode()


def strings_in(obj: object) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings_in(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings_in(v)]
    return []


def nowhere(key: str, text: str) -> bool:
    low = text.lower()
    return key.lower() not in low and key[-16:].lower() not in low


@pytest.fixture
def key(monkeypatch):
    k = "sk-ring3-sentinel-" + secrets.token_hex(12)
    monkeypatch.setenv("MODEL_API_KEY", k)
    return k


def test_redactor_through_escapes(key):
    r = transport_mod.Redactor(key)  # absent before T-09: this test errors, the next one fails
    # the literal key, any letter case
    assert r.text(f"a {key} b") == "a [REDACTED] b"
    assert r.text(f"a {key.swapcase()} b") == "a [REDACTED] b"
    # escaped inside a JSON string token: lower/upper hex, every character or every second one
    for variant in (esc(key), esc(key, upper=True), esc(key, every=2), esc(key.swapcase(), every=3)):
        out = r.text('{"x":"' + variant + '"}')
        assert json.loads(out) == {"x": "[REDACTED]"}, variant
    # double-escaped: the action JSON embedded in the envelope's text string
    action = '{"action":"done","summary":"' + esc(key) + '"}'
    env = '{"text":' + json.dumps(action) + "}"
    out = r.text(env)
    assert validate(json.loads(out)["text"]) == Done("[REDACTED]")
    # bytes: untouched bytes stay byte for byte (invalid UTF-8 included); no backslash, no scan
    raw = b'{"a":"\xff\xfe plain"}'
    assert r.body(raw) == raw
    assert r.body(b'{"k":"' + esc(key).encode() + b'","b":"\xff"}') == b'{"k":"[REDACTED]","b":"\xff"}'
    assert "sk-" not in repr(r) and key not in repr(r)


def test_t09_f2_escaped_key_nowhere(tmp_path, key, monkeypatch):
    bodies = [
        envelope(esc(key)),  # reply text = the key, every char escaped
        envelope(esc(key, upper=True, every=2), extra=esc(key, upper=True)),  # partial, upper hex; id too
        envelope(json.dumps('{"action":"done","summary":"' + esc(key) + '"}')[1:-1]),  # embedded action
        envelope(json.dumps('{"action":"refuse","reason":"' + esc(key.swapcase(), every=2) + '"}')[1:-1]),
        envelope(json.dumps('{"action":"done","summary":"' + key + '"}')[1:-1]),  # literal inside the action
    ]
    calls = []

    def hostile_urlopen(req, timeout):
        calls.append(1)
        if len(calls) <= len(bodies):
            return _Resp(200, bodies[len(calls) - 1])
        hdrs = email.message.Message()
        raise urllib.error.HTTPError(req.full_url, 401, "bad key", hdrs, _Resp(401, envelope(esc(key))))

    monkeypatch.setattr(transport_mod, "_urlopen", hostile_urlopen)
    transport = HttpsTransport()
    log = EventLog(tmp_path / "runs" / "r1")
    log.append("run_start", run_id="r1", model_id=CFG.id)
    ledger = Ledger(30_000_000, tmp_path / "ledger.jsonl")
    client = MuseSparkClient(CFG, transport, ledger, log, "r1", 5_000_000)
    texts: list[str] = []
    verdicts = []
    for _ in bodies:
        reply = client.ask("ctx", "req")
        assert reply.status == "completed" and reply.texts == 1
        texts.append(reply.text)
        verdicts.append(validate(reply.text))
    with pytest.raises(ModelStop) as e:
        client.ask("ctx", "req")
    texts += [str(e.value), repr(e.value)]
    # the validated actions (what becomes the FINAL frame, the final_sent event and the timeline line)
    assert verdicts[2] == Done("[REDACTED]") and verdicts[4] == Done("[REDACTED]")
    assert verdicts[3] == Refuse("[REDACTED]")
    for v in verdicts:
        assert nowhere(key, repr(v)), v
    # nowhere: returned text, exception text, events, every blob raw and JSON-decoded
    for t in texts:
        assert nowhere(key, t), t
    events = (log.dir / "events.jsonl").read_text()
    assert nowhere(key, events)
    blobs = sorted(p for p in Path(log.blobs).iterdir() if p.is_file())
    replies = [json.loads(ln) for ln in events.splitlines() if '"type":"model_reply"' in ln]
    assert len(replies) == len(bodies) + 1  # the five 200s and the 401
    for p in blobs:
        raw = p.read_bytes()
        assert nowhere(key, raw.decode("utf-8", "replace")), p.name
        try:
            decoded = json.loads(raw)
        except ValueError:
            continue
        for s in strings_in(decoded):
            assert nowhere(key, s), p.name
            if s.startswith("{"):  # the embedded action JSON, one level down
                try:
                    for s2 in strings_in(json.loads(s)):
                        assert nowhere(key, s2), p.name
                except ValueError:
                    pass
    # positive control: every reply blob was scrubbed, not merely absent (equal scrubbed bodies share a blob)
    for rec in replies:
        assert b"[REDACTED]" in (Path(log.blobs) / rec["blob"]).read_bytes()
    assert verify(log.dir).ok
