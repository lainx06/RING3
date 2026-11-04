"""T-19: the Muse Voice Transcribe adapter over a fake transport. Reserve, then post; the key goes nowhere."""

import io
import json
import wave

import pytest

from ring3bridge.config import ConfigError
from ring3bridge.eventlog import EventLog, verify
from ring3bridge.ledger import Ledger
from ring3bridge.models import transcribe as tr
from ring3bridge.models.transport import HttpsTransport, Redactor, TransportError, TransportResponse

KEY = "sk-fake-key-for-the-test-1234567890"
CFG = tr.VoiceConfig(
    "muse-voice-transcribe-1.0", "https://api.meta.ai/v1/asr/transcribe", 60, 180000,
    "https://example.invalid/synthetic-test-price", "2026-11-04",
)  # fmt: skip


def make_wav(seconds=2.0, rate=24000, channels=1, width=2) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(seconds * rate) * channels * width))
    return b.getvalue()


def reply(text="set the fan to 100 %", ms=2000) -> bytes:
    return json.dumps({"sessionId": "s1", "transcript": text, "audioDurationMs": ms, "turns": []}).encode()


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.redactor = Redactor(KEY)
        self.committed_at_post = []
        self.ledger = None

    def post(self, url, body, timeout_s, content_type="application/json"):
        self.calls.append((url, body, timeout_s, content_type))
        if self.ledger is not None:
            self.committed_at_post.append(self.ledger.unsettled_uusd())
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def setup(tmp_path, *responses, budget=1000, cfg=CFG):
    t = FakeTransport(*responses)
    led = Ledger(30_000_000, tmp_path / "ledger.jsonl")
    t.ledger = led
    log = EventLog(tmp_path / "run")
    log.append("run_start", run_id="r", kind="voice_console")
    return t, led, log, tr.TranscribeClient(cfg, t, led, log, "r", budget)


def records(tmp_path):
    return [json.loads(x) for x in (tmp_path / "run" / "events.jsonl").read_text().splitlines()]


def parts(call):
    """The multipart body as the server would split it, from its own Content-Type header."""
    _url, body, _t, ctype = call
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    assert body.endswith(b"--" + boundary + b"--\r\n")
    for chunk in body.split(b"--" + boundary)[1:-1]:
        head, _, data = chunk.partition(b"\r\n\r\n")
        out[head.split(b'name="')[1].split(b'"')[0].decode()] = (head, data[:-2])
    return out


def test_request_shape_is_the_documented_one(tmp_path):
    wav = make_wav()
    t, _led, _log, c = setup(tmp_path, TransportResponse(200, reply()))
    heard = c.transcribe(wav)
    assert heard.text == "set the fan to 100 %" and heard.audio_ms == 2000
    assert len(t.calls) == 1
    url, _body, timeout, _ctype = t.calls[0]
    assert url == "https://api.meta.ai/v1/asr/transcribe" and timeout == 60
    p = parts(t.calls[0])
    assert set(p) == {"request", "audio"}
    assert b"Content-Type: application/json" in p["request"][0]
    assert json.loads(p["request"][1]) == {
        "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
    }  # fmt: skip
    assert b"Content-Type: audio/wav" in p["audio"][0] and p["audio"][1] == wav


def test_reservation_comes_before_the_call_and_settles_on_whole_seconds(tmp_path):
    t, led, _log, c = setup(tmp_path, TransportResponse(200, reply(ms=2900)))
    c.transcribe(make_wav(seconds=2.9))
    assert t.committed_at_post == [150]  # 3 s rounded up x 50 µUSD/s, committed when the call left
    assert led.unsettled_uusd() == 0 and led.settled_uusd() == 100  # docs: rounded down to whole seconds


def test_the_order_in_the_log(tmp_path):
    _t, _led, _log, c = setup(tmp_path, TransportResponse(200, reply()))
    c.transcribe(make_wav())
    kinds = [r["type"] for r in records(tmp_path)]
    assert kinds == ["run_start", "spend_reserved", "voice_request", "voice_reply", "spend_settled"]
    assert verify(tmp_path / "run").ok


def test_the_cap_is_enforced_and_15_s_reserves_the_published_price(tmp_path):
    t, led, _log, c = setup(tmp_path, TransportResponse(200, reply(ms=15000)))
    c.transcribe(make_wav(seconds=tr.MAX_SECONDS))
    assert t.committed_at_post == [750]  # 15 s at $0.18 / h
    assert led.settled_uusd() == 750
    with pytest.raises(tr.AudioRefused, match="15 s"):
        c.transcribe(make_wav(seconds=tr.MAX_SECONDS + 0.01))
    assert len(t.calls) == 1 and led.committed_uusd() == 750  # no reservation, no call


@pytest.mark.parametrize(
    "wav",
    [
        b"",
        b"not a wav at all",
        make_wav(seconds=0),
        make_wav(rate=44100),
        make_wav(rate=8000),
        make_wav(channels=2),
        make_wav(width=1),
        make_wav(width=3),
        make_wav()[:100],  # the header promises more frames than there are
        make_wav() + b"\x00" * (2 << 20),
    ],
    ids=["empty", "junk", "no-frames", "44k", "8k", "stereo", "8-bit", "24-bit", "cut", "huge"],
)
def test_a_wav_the_docs_do_not_accept_never_costs_anything(tmp_path, wav):
    t, led, _log, c = setup(tmp_path)
    with pytest.raises(tr.AudioRefused):
        c.transcribe(wav)
    assert t.calls == [] and led.committed_uusd() == 0


@pytest.mark.parametrize("rate", [16000, 24000])
def test_both_documented_rates_pass(tmp_path, rate):
    t, _led, _log, c = setup(tmp_path, TransportResponse(200, reply()))
    c.transcribe(make_wav(rate=rate))
    assert len(t.calls) == 1


def test_over_the_run_budget_stops_before_the_call(tmp_path):
    t, _led, _log, c = setup(tmp_path, budget=99)  # 2 s reserve 100 µUSD
    with pytest.raises(tr.VoiceStop, match="run_cap"):
        c.transcribe(make_wav())
    assert t.calls == []
    with pytest.raises(tr.VoiceStop):  # and stays stopped
        c.transcribe(make_wav())
    assert t.calls == []


def test_no_budget_or_no_price_refuses(tmp_path):
    t, _led, _log, c = setup(tmp_path, budget=None)
    with pytest.raises(tr.VoiceStop, match="no_run_budget"):
        c.transcribe(make_wav())
    assert t.calls == []
    (tmp_path / "b").mkdir()
    nop = tr.VoiceConfig(CFG.model_id, CFG.endpoint, 60, None)
    t, _led, _log, c = setup(tmp_path / "b", cfg=nop)
    with pytest.raises(tr.VoiceStop, match="no_price"):
        c.transcribe(make_wav())
    assert t.calls == []


@pytest.mark.parametrize(
    "resp,settled",
    [
        (TransportResponse(401, b"{}"), 100),
        (TransportResponse(429, b"{}"), 100),
        (TransportResponse(500, b"{}"), 100),
        (TransportError("timeout"), 100),
        (TransportError("network"), 100),
        (TransportResponse(200, b"not json"), 100),
        (TransportResponse(200, b'{"transcript": 5}'), 100),
        (TransportResponse(200, b'{"sessionId": "s"}'), 100),
    ],
    ids=["401", "429", "500", "timeout", "network", "junk", "transcript-int", "no-transcript"],
)
def test_a_failure_settles_in_full_and_stops_the_client(tmp_path, resp, settled):
    t, led, _log, c = setup(tmp_path, resp)
    with pytest.raises(tr.VoiceStop):
        c.transcribe(make_wav())
    assert led.settled_uusd() == settled and led.unsettled_uusd() == 0
    with pytest.raises(tr.VoiceStop):
        c.transcribe(make_wav())
    assert len(t.calls) == 1  # never sent again


@pytest.mark.parametrize("ms", [-1, 10**12, "2000", None, True])
def test_an_implausible_duration_settles_at_the_reservation(tmp_path, ms):
    _t, led, _log, c = setup(tmp_path, TransportResponse(200, reply(ms=ms)))
    c.transcribe(make_wav())
    assert led.settled_uusd() == 100


def test_the_key_goes_nowhere(tmp_path):
    """A reply echoing the key, literally and through JSON escapes: not in events, blobs, exceptions, text."""
    escaped = "".join(f"\\u{ord(ch):04x}" for ch in KEY)
    body = (
        '{"sessionId":"s","audioDurationMs":2000,"turns":[],"transcript":"fan ' + KEY + " " + escaped + '"}'
    )
    scrubbed = Redactor(KEY).body(body.encode())
    t, _led, _log, c = setup(tmp_path, TransportResponse(200, scrubbed), TransportResponse(401, b"{}"))
    heard = c.transcribe(make_wav())
    assert KEY not in heard.text and "[REDACTED]" in heard.text
    with pytest.raises(tr.VoiceStop) as e:
        c.transcribe(make_wav())
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    for p in (tmp_path / "run").rglob("*"):
        if p.is_file():
            assert KEY.encode() not in p.read_bytes(), p.name
    assert all(KEY.encode() not in call[1] for call in t.calls)


def test_https_transport_sends_the_content_type_and_only_bearer(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n):
            return b"{}"

    def fake_open(req, timeout):
        seen.update(req.header_items())
        seen["data"], seen["timeout"] = req.data, timeout
        return Resp()

    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.setattr("ring3bridge.models.transport._urlopen", fake_open)
    t = HttpsTransport()
    t.post("https://api.meta.ai/v1/asr/transcribe", b"x", 5, content_type="multipart/form-data; boundary=b")
    assert seen["Content-type"] == "multipart/form-data; boundary=b"
    assert seen["Authorization"] == "Bearer " + KEY
    t.post("https://api.meta.ai/v1/responses", b"x", 5)
    assert seen["Content-type"] == "application/json"  # the existing callers are unchanged


def test_shipped_config_is_the_published_price():
    cfg = tr.load_voice_config()
    assert cfg.model_id == "muse-voice-transcribe-1.0"
    assert cfg.endpoint == "https://api.meta.ai/v1/asr/transcribe"
    assert cfg.price_uusd_per_hour == 180000
    assert cfg.price_source == "https://dev.meta.ai/docs/pricing-rate-limits"


def test_config_refuses_a_price_without_its_source(tmp_path):
    p = tmp_path / "m.toml"
    p.write_text('[voice]\nid="m"\nendpoint="https://x.invalid/"\ntimeout_s=5\nprice_uusd_per_hour=1\n')
    with pytest.raises(ConfigError):
        tr.load_voice_config(p)
    p.write_text('[voice]\nid="m"\nendpoint="http://x.invalid/"\ntimeout_s=5\n')
    with pytest.raises(ConfigError):
        tr.load_voice_config(p)
