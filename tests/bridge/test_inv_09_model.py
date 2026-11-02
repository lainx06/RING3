"""INV-09: reservation before every request, stop at the first 401/403/transport error, no secret anywhere.

Fake transport only: no network, no paid call. The secrets test drives the real ``HttpsTransport`` with
``urllib.request.urlopen`` replaced by a fake (nothing leaves the process) and a random sentinel key.
"""

import email.message
import io
import json
import logging
import re
import secrets
import traceback
import urllib.error
from pathlib import Path

import pytest

from ring3bridge import abi
from ring3bridge.config import ModelConfig, Price
from ring3bridge.eventlog import EventLog, verify
from ring3bridge.ledger import Ledger
from ring3bridge.models import transport as transport_mod
from ring3bridge.models.muse_spark import ModelStop, MuseSparkClient, build_request, parse_response
from ring3bridge.models.transport import HttpsTransport, TransportError, TransportResponse

REPO = Path(__file__).resolve().parents[2]
FAKE_PRICE = Price(2_000_000, 8_000_000, "https://example.invalid/synthetic-test-price", "2026-11-02")
CFG = ModelConfig("muse-spark-1.3", "https://api.meta.ai/v1/responses", 512, 60, FAKE_PRICE)


def ok_body(text='{"action":"call","op":"halt","args":{}}', tin=100, tout=12, cached=0,
            status="completed") -> bytes:  # fmt: skip
    """A Responses API reply in the shape of Meta's docs (D-55)."""
    return json.dumps(
        {"id": "resp_test", "object": "response", "status": status, "model": CFG.id,
         "output": [{"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": text}]}],
         "usage": {"input_tokens": tin, "output_tokens": tout,
                   "input_tokens_details": {"cached_tokens": cached}}}
    ).encode()  # fmt: skip


class FakeTransport:
    """Scripted responses; at each call, records what the ledger and event log held at that moment."""

    def __init__(self, script, ledger_path: Path, log_path: Path):
        self.script = list(script)
        self.ledger_path = ledger_path
        self.log_path = log_path
        self.calls = []

    def post(self, url, body, timeout_s):
        ledger_lines = self.ledger_path.read_text().splitlines() if self.ledger_path.exists() else []
        events = [json.loads(x) for x in self.log_path.read_text().splitlines()]
        self.calls.append({"url": url, "body": body, "ledger": ledger_lines, "events": events})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make(tmp_path, script, budget=1_000_000, cap=30_000_000):
    log = EventLog(tmp_path / "runs" / "r1")
    log.append("run_start", run_id="r1", model_id=CFG.id)
    ledger = Ledger(cap, tmp_path / "ledger.jsonl")
    t = FakeTransport(script, ledger.path, log.path)
    return MuseSparkClient(CFG, t, ledger, log, "r1", budget), t, ledger, log


def types(log):
    return [json.loads(x)["type"] for x in log.path.read_text().splitlines()]


def test_inv_09_reservation_before_request(tmp_path):
    client, t, ledger, log = make(
        tmp_path, [TransportResponse(200, ok_body()), TransportResponse(200, ok_body())]
    )
    r1 = client.ask("context pack", "self-test the cell")
    client.ask("context pack", "observation: ok")
    assert r1.text == '{"action":"call","op":"halt","args":{}}'
    assert len(t.calls) == 2
    for i, call in enumerate(t.calls, 1):
        # the reservation for this request was durable in the ledger before the transport was called ...
        reserves = [json.loads(x) for x in call["ledger"] if json.loads(x)["kind"] == "reserve"]
        assert [r["id"] for r in reserves] == list(range(1, i + 1))
        # ... sized on exactly the bytes sent (1 token per byte) plus the max output tokens ...
        assert (
            reserves[-1]["in_tok"] == len(call["body"]) and reserves[-1]["out_tok"] == CFG.max_output_tokens
        )
        # ... and logged (spend_reserved, then model_request) before the call
        assert [e["type"] for e in call["events"]][-2:] == ["spend_reserved", "model_request"]
        assert call["events"][-1]["reservation"] == call["events"][-2]["reservation"] == i
    assert (
        types(log) == ["run_start"] + ["spend_reserved", "model_request", "model_reply", "spend_settled"] * 2
    )
    assert ledger.unsettled_uusd() == 0 and ledger.settled_uusd() == 2 * (200 + 96)
    assert verify(log.dir).ok


def test_inv_09_no_request_without_reservation(tmp_path):
    # no price -> nothing is sent; over the run budget -> nothing is sent
    client, t, ledger, log = make(tmp_path, [TransportResponse(200, ok_body())])
    client.config = ModelConfig(CFG.id, CFG.endpoint, 512, 60, None)
    with pytest.raises(ModelStop) as e:
        client.ask("c", "x")
    assert e.value.code == abi.LINK_STOP_BUDGET and t.calls == []
    client, t, ledger, log = make(tmp_path / "b", [TransportResponse(200, ok_body())], budget=100)
    with pytest.raises(ModelStop) as e:
        client.ask("c", "x")
    assert e.value.code == abi.LINK_STOP_BUDGET and t.calls == [] and "spend_reserved" not in types(log)
    client, t, ledger, log = make(tmp_path / "c", [TransportResponse(200, ok_body())], budget=None)
    with pytest.raises(ModelStop):
        client.ask("c", "x")
    assert t.calls == []


@pytest.mark.parametrize(
    "first,code",
    [
        (TransportResponse(401, b'{"error":"unauthorized"}'), abi.LINK_STOP_MODEL_AUTH),
        (TransportResponse(403, b"forbidden"), abi.LINK_STOP_MODEL_AUTH),
        (TransportResponse(500, b"oops"), abi.LINK_STOP_TRANSPORT),
        (TransportResponse(429, b"slow down"), abi.LINK_STOP_TRANSPORT),
        (TransportError("network"), abi.LINK_STOP_TRANSPORT),
        (TransportError("timeout"), abi.LINK_STOP_TIMEOUT),
    ],
    ids=["401", "403", "500", "429", "network", "timeout"],
)
def test_inv_09_stop_on_401(tmp_path, first, code):
    """The first 401/403 (or other transport failure) stops the run: no later request is sent."""
    client, t, ledger, log = make(tmp_path, [first, TransportResponse(200, ok_body())])
    with pytest.raises(ModelStop) as e:
        client.ask("c", "x")
    assert e.value.code == code
    for _ in range(3):
        with pytest.raises(ModelStop) as e2:
            client.ask("c", "again")
        assert e2.value.code == code
    assert len(t.calls) == 1  # never retried
    assert ledger.unsettled_uusd() == 0  # the failed request is settled at its full reservation
    assert ledger.settled_uusd() == ledger.committed_uusd() > 0
    assert types(log)[-2:] == ["model_reply", "spend_settled"]
    assert verify(log.dir).ok


def test_parse_response_never_raises():
    r = parse_response(ok_body("hi", 5, 6, cached=2))
    assert (r.text, r.input_tokens, r.output_tokens, r.cached_tokens) == ("hi", 5, 6, 2)
    assert r.status == "completed"
    for junk in (b"", b"[]", b"{", b'{"output":"x"}', b'{"output":[{"content":[{"type":"output_text"}]}]}',
                 b'{"usage":{"input_tokens":-1,"output_tokens":true}}', b"[" * 100000):  # fmt: skip
        r = parse_response(junk)
        assert r.text == "" and r.input_tokens is None and r.output_tokens is None and r.cached_tokens is None
    two = {
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": "a"}, {"type": "output_text", "text": "b"}]}],
    }
    assert parse_response(json.dumps(two).encode()).text == ""  # more than one text: not one reply


def test_parse_response_status_must_be_completed():
    """D-55: any status but "completed" (incomplete, failed, in_progress, missing) is an invalid reply; the
    usage is still read so the reservation settles on real numbers."""
    for status in ("incomplete", "failed", "in_progress", "queued", "cancelled", "", None, 1, "Completed"):
        body = json.loads(ok_body("hi", 50, 60))
        if status is None:
            del body["status"]
        else:
            body["status"] = status
        r = parse_response(json.dumps(body).encode())
        assert r.text == "", status
        assert (r.input_tokens, r.output_tokens) == (50, 60), status
    assert parse_response(ok_body("hi", 50, 60)).text == "hi"
    # usage details that are not a dict or hold junk: cached unknown, the rest still parsed
    body = json.loads(ok_body("hi", 50, 60))
    body["usage"]["input_tokens_details"] = "x"
    r = parse_response(json.dumps(body).encode())
    assert (r.text, r.input_tokens, r.cached_tokens) == ("hi", 50, None)
    body["usage"]["input_tokens_details"] = {"cached_tokens": -4}
    assert parse_response(json.dumps(body).encode()).cached_tokens is None


def test_parse_response_reports_the_incomplete_reason_and_the_text_count():
    """Live run 20261102-122431: a reply cut off at max_output_tokens has status incomplete,
    incomplete_details.reason and only a reasoning item. The reason is bounded (it is quoted to the model)."""
    body = json.loads(ok_body("hi", 50, 2048, status="incomplete"))
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    body["output"] = [{"type": "reasoning", "summary": [], "encrypted_content": "AAAA"}]
    r = parse_response(json.dumps(body).encode())
    assert (r.text, r.status, r.incomplete_reason, r.texts) == ("", "incomplete", "max_output_tokens", 0)
    assert (r.input_tokens, r.output_tokens) == (50, 2048)
    for junk in ("", "x" * 33, "has space", "semi;colon", 5, None, ["max_output_tokens"], {"a": 1}):
        body["incomplete_details"] = {"reason": junk}
        assert parse_response(json.dumps(body).encode()).incomplete_reason == "", junk
    body["incomplete_details"] = "max_output_tokens"
    assert parse_response(json.dumps(body).encode()).incomplete_reason == ""
    body["incomplete_details"] = {"reason": "Content_Filter9"}
    assert parse_response(json.dumps(body).encode()).incomplete_reason == "content_filter9"  # lower-cased
    # the text count: completed with 0 or 2 texts is still text "" (not one reply), and says how many
    assert parse_response(ok_body("hi")).texts == 1
    two = json.loads(ok_body("hi"))
    two["output"][0]["content"].append({"type": "output_text", "text": "b"})
    r = parse_response(json.dumps(two).encode())
    assert (r.text, r.texts) == ("", 2)
    two["output"] = []
    r = parse_response(json.dumps(two).encode())
    assert (r.text, r.texts, r.status) == ("", 0, "completed")


def test_incomplete_reply_settles_on_usage_and_counts_as_invalid(tmp_path):
    body = ok_body("hi", 300, 100, status="incomplete")
    client, t, ledger, log = make(tmp_path, [TransportResponse(200, body)])
    r = client.ask("ctx", "req")
    assert r.text == "" and r.status == "incomplete"
    assert client.stopped is None  # an invalid reply is the run loop's business (two in a row -> STOP)
    assert ledger.settled_uusd() == 300 * 2 + 100 * 8  # settled on the reported usage, not the reservation
    assert types(log)[-2:] == ["model_reply", "spend_settled"]


def test_cached_tokens_reach_the_ledger(tmp_path):
    cfg_cached = ModelConfig(
        CFG.id,
        CFG.endpoint,
        512,
        60,
        Price(2_000_000, 8_000_000, FAKE_PRICE.source, FAKE_PRICE.checked, 500_000),
    )
    client, t, ledger, log = make(tmp_path, [TransportResponse(200, ok_body("x", 1000, 10, cached=400))])
    client.config = cfg_cached
    client.ask("ctx", "req")
    assert ledger.settled_uusd() == 600 * 2 + 400 * 0.5 + 10 * 8  # 1480


def test_request_body_matches_the_responses_api():
    """D-55 / D-61: exactly the documented fields, store false, reasoning.effort from the config, no key."""
    b = json.loads(build_request("muse-spark-1.3", "ctx", "req", 512))
    assert set(b) == {"model", "instructions", "input", "max_output_tokens", "store"}
    assert b["store"] is False and b["max_output_tokens"] == 512 and b["model"] == "muse-spark-1.3"
    assert b["instructions"] == "ctx" and b["input"] == "req"
    assert "reasoning" not in b and "Authorization" not in build_request("m", "c", "r", 16).decode()
    # D-61: with an effort the field set is exactly the documented one plus reasoning
    b = json.loads(build_request("muse-spark-1.3", "ctx", "req", 4096, "low"))
    assert set(b) == {"model", "instructions", "input", "max_output_tokens", "store", "reasoning"}
    assert b["reasoning"] == {"effort": "low"} and b["max_output_tokens"] == 4096
    for effort in ("minimal", "low", "medium", "high", "xhigh"):
        assert json.loads(build_request("m", "c", "r", 16, effort))["reasoning"] == {"effort": effort}
    for bad in ("none", "None", "", "LOW", "auto", "lowest", 1, True):
        with pytest.raises((ValueError, TypeError)):
            build_request("m", "c", "r", 16, bad)  # type: ignore[arg-type]
    # the shipped config's effort reaches the wire through the client (the live-run body shape)
    raw = build_request("muse-spark-1.3", "ctx", "req", 4096, "low").decode()
    assert '"reasoning":{"effort":"low"}' in raw and '"none"' not in raw


def test_client_sends_the_configured_reasoning_effort(tmp_path):
    client, t, ledger, log = make(
        tmp_path, [TransportResponse(200, ok_body()), TransportResponse(200, ok_body())]
    )
    client.ask("ctx", "req")
    assert "reasoning" not in json.loads(t.calls[0]["body"])  # the synthetic CFG sets none
    client.config = ModelConfig(CFG.id, CFG.endpoint, 512, 60, FAKE_PRICE, "low")
    client.ask("ctx", "req")
    assert json.loads(t.calls[1]["body"])["reasoning"] == {"effort": "low"}


# ---- secrets --------------------------------------------------------------------------------------------
class _Resp:
    def __init__(self, status, body):
        self.status, self._b = status, io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_inv_09_no_secrets_anywhere(tmp_path, monkeypatch, caplog, capsys):
    key = "sk-ring3-sentinel-" + secrets.token_hex(16)
    monkeypatch.setenv("MODEL_API_KEY", key)
    caplog.set_level(logging.DEBUG)
    seen_auth = []

    def hostile_urlopen(req, timeout):
        """A server that echoes the key everywhere it can: body, error message, headers, exception reason."""
        seen_auth.append(req.get_header("Authorization"))
        n = len(seen_auth)
        if n == 1:
            return _Resp(200, ok_body(f"echo {key}"))
        if n == 2:
            hdrs = email.message.Message()
            hdrs["WWW-Authenticate"] = f"Bearer {key}"
            raise urllib.error.HTTPError(req.full_url, 401, f"bad key {key}", hdrs, io.BytesIO(key.encode()))
        raise urllib.error.URLError(f"connection reset while sending {key}")

    monkeypatch.setattr(transport_mod, "_urlopen", hostile_urlopen)
    transport = HttpsTransport()
    log = EventLog(tmp_path / "runs" / "r1")
    log.append("run_start", run_id="r1", model_id=CFG.id)
    ledger = Ledger(30_000_000, tmp_path / "ledger.jsonl")
    client = MuseSparkClient(CFG, transport, ledger, log, "r1", 1_000_000)
    texts = []

    r = client.ask("ctx", "req")  # 200 whose body echoes the key
    texts.append(r.text)
    with pytest.raises(ModelStop) as e:  # 401 with the key in message, headers and body
        client.ask("ctx", "req")
    texts += [str(e.value), repr(e.value), "".join(traceback.format_exception(e.value))]
    # a transport error whose underlying exception names the key
    t2 = HttpsTransport()
    with pytest.raises(TransportError) as e3:
        t2.post(CFG.endpoint, b"{}", 5)
    assert e3.value.__context__ is None and e3.value.__cause__ is None
    texts += [str(e3.value), repr(e3.value), "".join(traceback.format_exception(e3.value)), repr(transport)]
    with pytest.raises(TransportError) as e4:
        monkeypatch.delenv("MODEL_API_KEY")
        HttpsTransport()
    texts.append(str(e4.value))

    # positive control: the key did go out, and only as the Authorization header
    assert seen_auth == [f"Bearer {key}"] * 3
    out = capsys.readouterr()
    blobs = [p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()]
    haystacks = (
        [t.encode() for t in texts] + blobs + [caplog.text.encode(), out.out.encode(), out.err.encode()]
    )
    for h in haystacks:
        assert key.encode() not in h
        assert key[-16:].encode() not in h  # not even a fragment
    assert b"[REDACTED]" in b"".join(blobs)  # the echoed key was scrubbed, not just missing
    assert verify(log.dir).ok

    # fixtures and committed bridge files hold no key-shaped value
    for p in list((REPO / "tests" / "bridge").rglob("*")) + list((REPO / "config").rglob("*")):
        if p.is_file() and p.suffix in (".py", ".toml", ".json", ".jsonl", ".txt", ".md"):
            assert key not in p.read_text(errors="replace")


def test_key_is_read_only_in_models_and_never_from_env_files():
    bridge = REPO / "bridge" / "ring3bridge"
    for p in bridge.rglob("*.py"):
        src = p.read_text()
        rel = p.relative_to(bridge).as_posix()
        if "MODEL_API_KEY" in src or "os.environ" in src or "getenv" in src:
            assert rel.startswith("models/"), f"{rel} touches the environment"
        assert not re.search(r"""['"]\.env""", src), f"{rel} names a .env file"
        assert "dotenv" not in src


def test_redirects_are_refused_and_timeouts_classified(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-ring3-sentinel-" + secrets.token_hex(8))
    h = transport_mod._NoRedirect()
    assert h.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid/") is None

    def timeout_urlopen(req, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(transport_mod, "_urlopen", timeout_urlopen)
    with pytest.raises(TransportError) as e:
        HttpsTransport().post(CFG.endpoint, b"{}", 5)
    assert e.value.kind == "timeout"
    with pytest.raises(TransportError) as e:
        HttpsTransport().post("http://api.meta.ai/v1/responses", b"{}", 5)  # https only
    assert e.value.kind == "network"


def test_bridge_tests_cannot_reach_a_network():
    import socket

    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
