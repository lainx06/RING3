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


def ok_body(text='{"action":"call","op":"halt","args":{}}', tin=100, tout=12) -> bytes:
    return json.dumps(
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
         "usage": {"input_tokens": tin, "output_tokens": tout}}
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
    assert parse_response(ok_body("hi", 5, 6)).text == "hi"
    assert parse_response(ok_body("hi", 5, 6)).input_tokens == 5
    for junk in (b"", b"[]", b"{", b'{"output":"x"}', b'{"output":[{"content":[{"type":"output_text"}]}]}',
                 b'{"usage":{"input_tokens":-1,"output_tokens":true}}', b"[" * 100000):  # fmt: skip
        r = parse_response(junk)
        assert r.text == "" and r.input_tokens is None and r.output_tokens is None
    two = {
        "output": [{"content": [{"type": "output_text", "text": "a"}, {"type": "output_text", "text": "b"}]}]
    }
    assert parse_response(json.dumps(two).encode()).text == ""  # more than one text: not one reply


def test_request_body_has_no_secret_fields():
    b = json.loads(build_request("muse-spark-1.3", "ctx", "req", 512))
    assert set(b) == {"model", "instructions", "input", "max_output_tokens"}


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
