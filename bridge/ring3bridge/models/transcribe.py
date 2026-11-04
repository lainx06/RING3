"""Muse Voice Transcribe adapter over a ``Transport`` (T-19, D-104). The transcript it returns is untrusted.

Request (official docs, https://dev.meta.ai/docs/api-reference/voice/transcribe and .../voice/schemas, quoted
in docs/evidence/T-19/README.md): ``POST https://api.meta.ai/v1/asr/transcribe``, ``multipart/form-data`` with
a ``request`` part (JSON: ``model``, ``audioEncoding: "WAV"``, ``mode: "PUSH_TO_TALK"``) and an ``audio`` part
(RIFF/WAVE, mono, 16-bit PCM, 16 or 24 kHz). No ``Accept`` header: the documented default is one JSON object
with ``sessionId``, ``transcript``, ``audioDurationMs``, ``turns``.

Order for every recording, enforced here:
  1. check the WAV (format, 15 s cap): refused -> ``AudioRefused``, nothing reserved, nothing sent;
  2. ``ledger.reserve`` the duration, rounded up to whole seconds: refused -> ``VoiceStop``, nothing sent;
  3. log ``spend_reserved``, ``voice_request`` (size and SHA-256 only: the audio itself is not kept);
  4. ``transport.post``;
  5. log ``voice_reply`` (status + scrubbed body blob), settle (documented metering: whole seconds rounded
     down; a failure or an implausible duration settles at the full reservation, as for every other model);
  6. redact the decoded transcript with the transport's ``Redactor`` before returning it.
Any non-200 status or transport error stops the client: every later call raises ``VoiceStop`` without sending.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import tomllib
import wave
from dataclasses import dataclass
from pathlib import Path

from ring3bridge.config import MODELS_TOML, ConfigError, Price
from ring3bridge.ledger import BudgetRefused, Ledger, Reservation
from ring3bridge.models.transport import Transport, TransportError

MAX_SECONDS = 15  # card: every recording is capped at 15 s
ACCEPTED_RATES = (16000, 24000)  # docs: mono integer PCM at 16 kHz or 24 kHz
MAX_WAV_BYTES = 1 << 20  # 15 s at 24 kHz is 720,044 B
SECONDS_PER_HOUR = 3600


class AudioRefused(ValueError):
    """Not a recording this adapter sends; nothing was reserved or sent."""


class VoiceStop(Exception):
    """Voice is over for this console session; the keyboard is unaffected."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class VoiceConfig:
    model_id: str
    endpoint: str
    timeout_s: int
    price_uusd_per_hour: int | None  # None = unset: reservations are refused (fails closed)
    price_source: str = ""
    price_checked: str = ""


def load_voice_config(path: Path = MODELS_TOML) -> VoiceConfig:
    with open(path, "rb") as f:
        v = tomllib.load(f).get("voice")
    if type(v) is not dict:
        raise ConfigError("[voice] table missing")
    mid, ep, to = v.get("id"), v.get("endpoint"), v.get("timeout_s")
    if type(mid) is not str or not mid:
        raise ConfigError("voice id missing")
    if type(ep) is not str or not ep.startswith("https://"):
        raise ConfigError("voice endpoint must be https")
    if type(to) is not int or not 1 <= to <= 600:
        raise ConfigError("voice timeout_s must be an integer in 1..600")
    keys = ("price_uusd_per_hour", "price_source", "price_checked")
    present = [k for k in keys if k in v]
    if not present:
        return VoiceConfig(mid, ep, to, None)
    if len(present) != len(keys):
        raise ConfigError("a voice price needs price_uusd_per_hour, price_source and price_checked")
    price, src, checked = v["price_uusd_per_hour"], v["price_source"], v["price_checked"]
    if type(price) is not int or not 1 <= price <= 10**9:
        raise ConfigError("price_uusd_per_hour must be a positive integer")
    if type(src) is not str or not src.startswith("https://"):
        raise ConfigError("price_source must be the https URL of the official pricing page")
    if type(checked) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
        raise ConfigError("price_checked must be YYYY-MM-DD")
    return VoiceConfig(mid, ep, to, price, src, checked)


@dataclass(frozen=True)
class WavInfo:
    rate: int
    frames: int

    @property
    def ms(self) -> int:
        return self.frames * 1000 // self.rate

    @property
    def seconds_up(self) -> int:
        return -(-self.frames // self.rate)


def check_wav(wav: bytes) -> WavInfo:
    """RIFF/WAVE, mono, 16-bit PCM, a documented rate, 1 frame .. 15 s, as many samples as the header says."""
    if len(wav) > MAX_WAV_BYTES:
        raise AudioRefused(f"recording over the {MAX_SECONDS} s cap")
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            channels, width, rate, frames = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
                w.getnframes(),
            )
            data = w.readframes(frames)
    except (wave.Error, EOFError, ValueError):
        raise AudioRefused("not a PCM WAV file") from None
    if channels != 1 or width != 2 or rate not in ACCEPTED_RATES:
        raise AudioRefused(
            f"WAV must be mono, 16-bit, 16 or 24 kHz (got {channels} ch, {width * 8} bit, {rate} Hz)"
        )
    if frames <= 0 or len(data) != frames * 2:
        raise AudioRefused("WAV has no audio or fewer samples than its header says")
    if frames > MAX_SECONDS * rate:
        raise AudioRefused(f"recording over the {MAX_SECONDS} s cap")
    return WavInfo(rate, frames)


def build_request(model_id: str, wav: bytes) -> tuple[bytes, str]:
    """The multipart body and its Content-Type."""
    boundary = "ring3" + secrets.token_hex(16)
    settings = json.dumps(
        {"model": model_id, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    b = boundary.encode("ascii")
    body = b"".join(
        (
            b"--", b, b'\r\nContent-Disposition: form-data; name="request"\r\n',
            b"Content-Type: application/json\r\n\r\n", settings, b"\r\n",
            b"--", b, b'\r\nContent-Disposition: form-data; name="audio"; filename="recording.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n", wav, b"\r\n",
            b"--", b, b"--\r\n",
        )
    )  # fmt: skip
    return body, "multipart/form-data; boundary=" + boundary


def parse_reply(body: bytes) -> tuple[str | None, int | None]:
    """(transcript, audioDurationMs). Never raises; a missing or wrongly typed field is None."""
    try:
        d = json.loads(body)
    except (ValueError, RecursionError):
        return None, None
    if type(d) is not dict:
        return None, None
    text, ms = d.get("transcript"), d.get("audioDurationMs")
    return (text if type(text) is str else None), (ms if type(ms) is int else None)


@dataclass(frozen=True)
class Heard:
    text: str  # still untrusted: goes to voice.clean_transcript and nowhere else
    audio_ms: int


class TranscribeClient:
    def __init__(
        self,
        config: VoiceConfig,
        transport: Transport,
        ledger: Ledger,
        log,  # noqa: ANN001 - EventLog or voice.LockedLog
        run_id: str,
        run_budget_uusd: int | None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.ledger = ledger
        self.log = log
        self.run_id = run_id
        self.run_budget_uusd = run_budget_uusd
        self.stopped: VoiceStop | None = None
        cfg = config
        self._price = (
            None
            if cfg.price_uusd_per_hour is None
            # The ledger prices tokens: one second is recorded as one output token at the per-second price.
            else Price(
                0,
                -(-cfg.price_uusd_per_hour * 1_000_000 // SECONDS_PER_HOUR),
                cfg.price_source,
                cfg.price_checked,
            )
        )

    def _stop(self, res: Reservation, detail: str) -> VoiceStop:
        self.ledger_settle(res, None)
        self.stopped = VoiceStop(detail)
        return self.stopped

    def ledger_settle(self, res: Reservation, seconds: int | None) -> None:
        actual = self.ledger.settle(res, None if seconds is None else 0, seconds)
        self.log.append(
            "spend_settled", reservation=res.id, uusd=actual, usage_known=int(seconds is not None)
        )

    def transcribe(self, wav: bytes) -> Heard:
        if self.stopped is not None:
            raise VoiceStop("voice stopped earlier: " + self.stopped.detail)
        cfg = self.config
        info = check_wav(wav)
        try:
            res = self.ledger.reserve(self.run_id, b"", info.seconds_up, self._price, self.run_budget_uusd)
        except BudgetRefused as e:
            self.stopped = VoiceStop(e.reason)
            raise self.stopped from None
        self.log.append("spend_reserved", reservation=res.id, uusd=res.uusd, in_tokens=0,
                        max_out_tokens=res.max_output_tokens)  # fmt: skip
        body, content_type = build_request(cfg.model_id, wav)
        audio_sha = hashlib.sha256(wav).hexdigest()
        self.log.append("voice_request", model_id=cfg.model_id, endpoint=cfg.endpoint, reservation=res.id,
                        bytes=len(body), audio_ms=info.ms, audio_sha256=audio_sha)  # fmt: skip
        failure = ""
        try:
            resp = self.transport.post(cfg.endpoint, body, cfg.timeout_s, content_type=content_type)
        except TransportError as e:
            failure = f"transport {e.kind}"
        if failure:
            self.log.append("voice_reply", status=0, bytes=0, error=failure, reservation=res.id)
            raise self._stop(res, failure)
        blob = self.log.blob(resp.body)
        self.log.append(
            "voice_reply", status=resp.status, bytes=len(resp.body), blob=blob, reservation=res.id
        )
        if resp.status != 200:
            raise self._stop(res, f"HTTP {resp.status}")
        text, ms = parse_reply(resp.body)
        if text is None:
            raise self._stop(res, "reply without a transcript")
        plausible = ms is not None and 0 <= ms <= (MAX_SECONDS + 1) * 1000
        self.ledger_settle(res, ms // 1000 if plausible else None)
        red = getattr(self.transport, "redactor", None)
        return Heard(red.text(text) if red is not None else text, ms if plausible else info.ms)
