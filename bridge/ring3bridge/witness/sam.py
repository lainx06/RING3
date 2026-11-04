"""SAM 3.1 adapter (T-07 step 3, D-56, D-58, INV-09, INV-14).

Request (official docs, checked by the lead 2026-11-02, D-56): ``POST https://api.meta.ai/v1/responses`` with
``"model": "sam-3.1"``, ``input`` = one user message holding an ``input_text`` (a short noun phrase) and an
``input_image`` whose ``image_url`` is a base64 data URL, ``stream: false`` and
``metadata.include_confidence: "true"``. The endpoint, model ID and per-image price come from the ``[sam]``
table of ``config/models.toml``.

Reply: one text line per image (we send one), records separated by commas, each
``<id> <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|><|mask;x=..;y=..[;c=..];data=H,W,<enc>payload|>``.
Parsed with a bounded regex, not a comma split (mask data contains commas). Coordinates are strict integers in
source pixels, ``c`` a decimal in [0, 1] kept as integer parts-per-million. The mask payload is opaque (its
encoding is not documented beyond "row-major 0/1 raster"): bounded and checked for shape, never decoded.
Anything the parser doesn't accept -> ``CAN'T VERIFY: SAM_FORMAT``. An empty line means "concept not present":
no records.

Spend and authority: this module never touches the model key and never builds headers. It takes an injected
``Transport`` (the T-03a one in Part B; a fake in tests) and an injected ``ImageSpend``; one image is reserved
before every post, and a refused reservation (``BudgetRefused``) propagates with nothing sent. Any non-200
status (401/403 included) or transport error settles the reservation in full and raises ``SamStop``: the run
stops, it is never turned into CAN'T VERIFY. After a stop every later call raises without sending.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ring3bridge.config import MODELS_TOML, ConfigError, Price
from ring3bridge.ledger import BudgetRefused, Ledger, Reservation
from ring3bridge.models.transport import Transport, TransportError
from ring3bridge.witness import SAM_FORMAT, CantVerify

DEFAULT_PROMPT = "white plastic arm"
MAX_PROMPT = 64
MAX_IMAGE_BYTES = 4 << 20
MAX_LINE = 1 << 20
MAX_RECORDS = 16
MAX_PAYLOAD = 1 << 20
MAX_MASK_DIM = 16384
STATUS_COMPLETED = "completed"

# ---- config ----


@dataclass(frozen=True)
class SamConfig:
    model_id: str
    endpoint: str
    timeout_s: int
    price_uusd_per_image: int | None  # None = unset: reservations are refused (fails closed)
    price_source: str = ""
    price_checked: str = ""


def load_sam_config(path: Path = MODELS_TOML) -> SamConfig:
    with open(path, "rb") as f:
        s = tomllib.load(f).get("sam")
    if type(s) is not dict:
        raise ConfigError("[sam] table missing")
    mid, ep, to = s.get("id"), s.get("endpoint"), s.get("timeout_s")
    if type(mid) is not str or not mid:
        raise ConfigError("sam id missing")
    if type(ep) is not str or not ep.startswith("https://"):
        raise ConfigError("sam endpoint must be https")
    if type(to) is not int or not 1 <= to <= 600:
        raise ConfigError("sam timeout_s must be an integer in 1..600")
    keys = ("price_uusd_per_image", "price_source", "price_checked")
    present = [k for k in keys if k in s]
    if not present:
        return SamConfig(mid, ep, to, None)
    if len(present) != len(keys):
        raise ConfigError("a sam price needs price_uusd_per_image, price_source and price_checked")
    p, src, chk = s["price_uusd_per_image"], s["price_source"], s["price_checked"]
    if type(p) is not int or not 1 <= p <= 10**7:
        raise ConfigError("price_uusd_per_image must be an integer in 1..10000000")
    if type(src) is not str or not src.startswith("https://"):
        raise ConfigError("price_source must be the https URL of the official pricing page")
    if type(chk) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", chk):
        raise ConfigError("price_checked must be YYYY-MM-DD")
    return SamConfig(mid, ep, to, p, src, chk)


# ---- spend ----


class ImageSpend(Protocol):
    def reserve_image(self) -> object: ...
    def settle_image(self, token: object) -> int: ...


class LedgerImageSpend:
    """One SAM image on the T-03a ledger (same file, same run and project caps).

    The ledger prices tokens, so an image is recorded as 0 input tokens + 1 output token at
    ``price_uusd_per_image`` µUSD per token (= x 1,000,000 per 1M tokens): the reservation is exactly the
    image price. SAM bills per image, so it always settles at the full reservation."""

    def __init__(self, ledger: Ledger, run_id: str, cfg: SamConfig, run_budget_uusd: int | None) -> None:
        self._ledger = ledger
        self._run = run_id
        self._budget = run_budget_uusd
        self._price = (
            None
            if cfg.price_uusd_per_image is None
            else Price(0, cfg.price_uusd_per_image * 1_000_000, cfg.price_source, cfg.price_checked)
        )

    def reserve_image(self) -> Reservation:
        if self._price is None:
            raise BudgetRefused("no_price", "sam price unset in config/models.toml")
        return self._ledger.reserve(self._run, b"", 1, self._price, self._budget)

    def settle_image(self, token: object) -> int:
        assert isinstance(token, Reservation)
        return self._ledger.settle(token)


# ---- request / envelope ----


def build_request(model_id: str, prompt: str, jpeg: bytes) -> bytes:
    if not 1 <= len(prompt) <= MAX_PROMPT or not all(0x20 <= ord(ch) < 0x7F for ch in prompt):
        raise ValueError("prompt must be 1-64 printable ASCII characters")
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_IMAGE_BYTES:
        raise ValueError("image must be a JPEG of at most 4 MiB")
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": url},
                ],
            }
        ],
        "metadata": {"include_confidence": "true"},
        "stream": False,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def parse_envelope(body: bytes) -> str:
    """Responses API reply -> the single ``output_text`` string. Anything else -> SAM_FORMAT."""
    try:
        d = json.loads(body)
    except (ValueError, RecursionError):
        raise CantVerify(SAM_FORMAT, "reply is not JSON") from None
    if type(d) is not dict or d.get("status") != STATUS_COMPLETED:
        raise CantVerify(SAM_FORMAT, "reply status is not completed")
    out = d.get("output")
    if type(out) is not list:
        raise CantVerify(SAM_FORMAT, "no output list")
    texts = []
    for item in out:
        if type(item) is not dict or item.get("type") != "message":
            continue
        content = item.get("content")
        if type(content) is not list:
            raise CantVerify(SAM_FORMAT, "message without content list")
        for part in content:
            if type(part) is dict and part.get("type") == "output_text":
                t = part.get("text")
                if type(t) is not str:
                    raise CantVerify(SAM_FORMAT, "output_text without text")
                texts.append(t)
    if len(texts) != 1:
        raise CantVerify(SAM_FORMAT, f"{len(texts)} output_text items, expected 1")
    return texts[0]


# ---- record line ----

_INT = r"(0|[1-9][0-9]{0,5})"
_DEC = r"(0(?:\.[0-9]{1,6})?|1(?:\.0{1,6})?)"
_BOX = rf"<\|box;x1={_INT};y1={_INT};x2={_INT};y2={_INT};w={_INT};h={_INT}(?:;c={_DEC})?\|>"
_MASK = rf"<\|mask;x={_INT};y={_INT}(?:;c={_DEC})?;data={_INT},{_INT},([\x21-\x7e]{{1,{MAX_PAYLOAD}}}?)\|>"
_NEXT = r"(?=\Z|, ?(?:0|[1-9][0-9]{0,5}) <\|box;)"  # the payload ends at the "|>" that ends the record
_RECORD = re.compile(rf"(0|[1-9][0-9]{{0,5}}) {_BOX}{_MASK}{_NEXT}")
_SEP = re.compile(r", ?")


@dataclass(frozen=True)
class SamRecord:
    obj_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    c_ppm: int | None  # box confidence, parts per million
    mask_c_ppm: int | None
    mask_h: int
    mask_w: int
    mask_payload_len: int


def _ppm(dec: str | None) -> int | None:
    if dec is None:
        return None
    whole, _, frac = dec.partition(".")
    return int(whole) * 1_000_000 + int((frac + "000000")[:6])


def parse_line(text: str, img_w: int, img_h: int) -> list[SamRecord]:
    """One SAM line for an ``img_w`` x ``img_h`` image -> records ([] when empty); else SAM_FORMAT."""
    if len(text) > MAX_LINE:
        raise CantVerify(SAM_FORMAT, "line too long")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if text == "":
        return []
    if not all(0x20 <= ord(ch) < 0x7F for ch in text):
        raise CantVerify(SAM_FORMAT, "not one line of printable ASCII")
    recs: list[SamRecord] = []
    pos = 0
    while True:
        m = _RECORD.match(text, pos)
        if m is None:
            raise CantVerify(SAM_FORMAT, f"record {len(recs) + 1} not understood at byte {pos}")
        g = m.groups()
        oid, x1, y1, x2, y2, w, h = (int(v) for v in g[0:7])
        bc, mx, my, mc, mh, mw, payload = g[7], int(g[8]), int(g[9]), g[10], int(g[11]), int(g[12]), g[13]
        if not (0 <= x1 < x2 <= img_w and 0 <= y1 < y2 <= img_h):
            raise CantVerify(SAM_FORMAT, f"record {oid}: box outside the image or empty")
        if abs(w - (x2 - x1)) > 1 or abs(h - (y2 - y1)) > 1:
            raise CantVerify(SAM_FORMAT, f"record {oid}: w/h disagree with the corners")
        if not (
            1 <= mh <= MAX_MASK_DIM and 1 <= mw <= MAX_MASK_DIM and mx <= MAX_MASK_DIM and my <= MAX_MASK_DIM
        ):
            raise CantVerify(SAM_FORMAT, f"record {oid}: mask shape out of range")
        if any(r.obj_id == oid for r in recs):
            raise CantVerify(SAM_FORMAT, f"duplicate object id {oid}")
        recs.append(SamRecord(oid, x1, y1, x2, y2, _ppm(bc), _ppm(mc), mh, mw, len(payload)))
        if len(recs) > MAX_RECORDS:
            raise CantVerify(SAM_FORMAT, f"more than {MAX_RECORDS} records")
        pos = m.end()
        if pos == len(text):
            return recs
        s = _SEP.match(text, pos)
        if s is None:
            raise CantVerify(SAM_FORMAT, f"bad separator at byte {pos}")
        pos = s.end()


# ---- client ----


class SamStop(Exception):
    """The run must stop (INV-09): HTTP 401/403, any other non-200 status, or a transport error."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"SAM stop: {detail}")
        self.detail = detail


@dataclass(frozen=True)
class SamResult:
    records: list[SamRecord]
    line: str  # the raw SAM text line (untrusted data; for the log)
    request_sha256: str
    image_sha256: str
    uusd: int  # settled spend estimate


class SamClient:
    def __init__(self, cfg: SamConfig, transport: Transport, spend: ImageSpend) -> None:
        self._cfg = cfg
        self._transport = transport
        self._spend = spend
        self._stopped: str | None = None
        self.last_reply: bytes = (
            b""  # the last reply body as the transport returned it (scrubbed), for the log
        )
        self.last_uusd: int = 0  # the last settled image (also when the reply did not parse)

    @property
    def stopped(self) -> str | None:
        return self._stopped

    def segment(self, jpeg: bytes, img_w: int, img_h: int, prompt: str = DEFAULT_PROMPT) -> SamResult:
        if self._stopped is not None:
            raise SamStop(f"stopped earlier: {self._stopped}")
        self.last_reply, self.last_uusd = b"", 0
        body = build_request(self._cfg.model_id, prompt, jpeg)
        token = self._spend.reserve_image()  # BudgetRefused propagates: nothing is sent
        failure: str | None = None
        resp = None
        try:
            resp = self._transport.post(self._cfg.endpoint, body, self._cfg.timeout_s)
        except TransportError as e:
            failure = f"transport {e.kind}"
        uusd = self._spend.settle_image(token)
        self.last_uusd = uusd
        if resp is not None:
            self.last_reply = resp.body[:MAX_LINE]
        if failure is None and resp is not None and resp.status != 200:
            failure = f"HTTP {resp.status}"
        if failure is not None:
            self._stopped = failure
            raise SamStop(failure)
        assert resp is not None
        line = parse_envelope(resp.body)
        records = parse_line(line, img_w, img_h)
        return SamResult(
            records, line, hashlib.sha256(body).hexdigest(), hashlib.sha256(jpeg).hexdigest(), uusd
        )
