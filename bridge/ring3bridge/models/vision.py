"""Muse Spark image call for the vision witness (T-16, D-98, INV-09, INV-14): one rectified gauge crop in,
one zone answer out. Same transport, redaction and reservation path as ``muse_spark.py``.

Request (official docs, checked by the builder on 2026-11-04; quoted in docs/evidence/T-16/README.md):
``POST https://api.meta.ai/v1/responses`` (https://dev.meta.ai/docs/image-understanding) with ``model``,
``instructions`` (the witness context pack), ``input`` = one ``{"type": "message", "role": "user"}`` item
whose ``content`` holds an ``input_text`` (``text``) and an ``input_image`` (``image_url``: "Fully qualified
URL or base64-encoded data URL"; JPEG is among the accepted formats; inline images up to 50 MB; ``detail``
is optional and left at its default), ``max_output_tokens``, ``store: false``, ``reasoning.effort`` (D-61)
and structured output: ``text.format`` = ``{"type": "json_schema", "name", "schema", "strict": true}``
(https://dev.meta.ai/docs/structured-output; the Responses schemas page names the fields: ``type`` enum
json_schema, ``name`` a-z A-Z 0-9 _ - up to 64 characters, ``schema`` a JSON Schema object, ``strict``;
strict mode needs a plain root object, ``additionalProperties: false`` and every property required). The
schema below is exactly the card's object. The docs say decoding is constrained to the schema; the witness
still validates the text strictly (``witness.vision.parse_zone``) and treats the reply as data either way.

Reservation (INV-09, card rule): before every call, 1 token per request byte outside the image's base64
plus ``image_tokens_reserve`` from ``[vision]`` (a ceiling over the docs' "~1,300-1,500 tokens for a ~1280 px
image"; the count endpoint ``POST /v1/responses/input_tokens`` exists and would be exact, but needs its own
authenticated call per check and is not used) plus ``max_output_tokens`` in full, at the ``[vision]`` prices.
Settled on the reported usage. A refused reservation, a 401/403, any other non-200 status or a transport
error raises ``ModelStop`` (the run stops; INV-09) and stops this client for good. A reply that does not
parse is returned as text "" with its status: the witness turns it into CAN'T VERIFY, never a retry.

Nothing here reads the key or builds a header: the injected ``Transport`` does (``transport.py``).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from ring3bridge import abi
from ring3bridge.config import REASONING_EFFORTS, VisionConfig
from ring3bridge.eventlog import EventLog
from ring3bridge.ledger import BudgetRefused, Ledger
from ring3bridge.models.muse_spark import ModelReply, ModelStop, parse_response, redact_reply
from ring3bridge.models.transport import Transport, TransportError

ZONE_VALUES = ("OUT_LOW", "LOW", "MID", "HIGH", "OUT_HIGH", "UNSURE")
ZONE_FORMAT_NAME = "ring3_gauge_zone"
ZONE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "zone": {"type": "string", "enum": list(ZONE_VALUES)},
        "arm_visible": {"type": "boolean"},
    },
    "required": ["zone", "arm_visible"],
    "additionalProperties": False,
}
MAX_IMAGE_BYTES = 4 << 20  # the docs allow 50 MB inline; a 920 x 570 crop is ~100 KB
MAX_REPLY_KEEP = 1 << 20  # reply bytes kept for the witness record


def _data_url(jpeg: bytes) -> str:
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_IMAGE_BYTES:
        raise ValueError("image must be a JPEG of at most 4 MiB")
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def build_request(cfg: VisionConfig, instructions: str, user_text: str, jpeg: bytes) -> bytes:
    """Canonical JSON bytes of the documented request (module docstring)."""
    if cfg.reasoning_effort is not None and cfg.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError("reasoning_effort must be one of the documented values (D-61)")
    body: dict[str, object] = {
        "model": cfg.id,
        "instructions": instructions,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_text},
                    {"type": "input_image", "image_url": _data_url(jpeg)},
                ],
            }
        ],
        "max_output_tokens": cfg.max_output_tokens,
        "store": False,
        "text": {
            "format": {"type": "json_schema", "name": ZONE_FORMAT_NAME, "schema": ZONE_SCHEMA, "strict": True}
        },
    }
    if cfg.reasoning_effort is not None:
        body["reasoning"] = {"effort": cfg.reasoning_effort}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def reserve_input_tokens(body: bytes, jpeg: bytes, image_tokens: int) -> int:
    """The worst case reserved for the input: every request byte outside the image's base64 (1 token per
    byte, D-52) plus the configured per-image ceiling."""
    return len(body) - len(base64.b64encode(jpeg)) + image_tokens


@dataclass(frozen=True)
class VisionReply:
    text: str  # the model's text, untrusted: goes to ``witness.vision.parse_zone`` and nowhere else
    status: str
    incomplete_reason: str
    texts: int
    input_tokens: int | None
    output_tokens: int | None
    uusd: int  # settled spend estimate for this call
    request_sha256: str
    image_sha256: str
    body: bytes  # the reply as the transport returned it (scrubbed), bounded


class VisionClient:
    def __init__(
        self,
        cfg: VisionConfig,
        transport: Transport,
        ledger: Ledger,
        log: EventLog,
        run_id: str,
        run_budget_uusd: int | None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.ledger = ledger
        self.log = log
        self.run_id = run_id
        self.run_budget_uusd = run_budget_uusd
        self.stopped: ModelStop | None = None
        self.requests = 0

    def __repr__(self) -> str:
        return f"VisionClient(model={self.cfg.id!r}, requests={self.requests})"

    def ask(self, instructions: str, user_text: str, jpeg: bytes) -> VisionReply:
        if self.stopped is not None:
            raise ModelStop(self.stopped.code, "vision client stopped earlier: " + self.stopped.detail)
        cfg = self.cfg
        body = build_request(cfg, instructions, user_text, jpeg)
        tin_reserve = reserve_input_tokens(body, jpeg, cfg.image_tokens_reserve)
        pack_sha = hashlib.sha256(instructions.encode("ascii", "replace")).hexdigest()
        image_sha = hashlib.sha256(jpeg).hexdigest()
        try:
            res = self.ledger.reserve(
                self.run_id, body, cfg.max_output_tokens, cfg.price, self.run_budget_uusd, tin_reserve
            )
        except BudgetRefused as e:
            self.stopped = ModelStop(abi.LINK_STOP_BUDGET, e.reason)
            raise self.stopped from None
        self.log.append("spend_reserved", reservation=res.id, uusd=res.uusd, in_tokens=res.input_tokens,
                        max_out_tokens=res.max_output_tokens,
                        image_tokens=cfg.image_tokens_reserve)  # fmt: skip
        self.log.append("model_request", model_id=cfg.id, endpoint=cfg.endpoint, bytes=len(body),
                        request_blob=self.log.blob(body), reservation=res.id, purpose="witness_vision",
                        pack_sha256=pack_sha, image_sha256=image_sha, image_bytes=len(jpeg))  # fmt: skip
        self.requests += 1
        failure: ModelStop | None = None
        resp = None
        try:
            resp = self.transport.post(cfg.endpoint, body, cfg.timeout_s)
        except TransportError as e:
            code = abi.LINK_STOP_TIMEOUT if e.kind == "timeout" else abi.LINK_STOP_TRANSPORT
            failure = ModelStop(code, f"transport {e.kind}")
        if failure is not None or resp is None:
            assert failure is not None
            self.log.append("model_reply", status=0, bytes=0, error=failure.detail, reservation=res.id)
            self._settle(res, None, None, None)
            self.stopped = failure
            raise failure
        self.log.append("model_reply", status=resp.status, bytes=len(resp.body),
                        blob=self.log.blob(resp.body), reservation=res.id)  # fmt: skip
        if resp.status != 200:
            self._settle(res, None, None, None)
            code = abi.LINK_STOP_MODEL_AUTH if resp.status in (401, 403) else abi.LINK_STOP_TRANSPORT
            self.stopped = ModelStop(code, f"HTTP {resp.status}")
            raise self.stopped
        reply: ModelReply = redact_reply(self.transport, parse_response(resp.body))
        uusd = self._settle(res, reply.input_tokens, reply.output_tokens, reply.cached_tokens)
        return VisionReply(reply.text, reply.status, reply.incomplete_reason, reply.texts, reply.input_tokens,
                           reply.output_tokens, uusd, hashlib.sha256(body).hexdigest(), image_sha,
                           resp.body[:MAX_REPLY_KEEP])  # fmt: skip

    def _settle(self, res, tin: int | None, tout: int | None, cached: int | None) -> int:
        actual = self.ledger.settle(res, tin, tout, cached)
        self.log.append("spend_settled", reservation=res.id, uusd=actual, usage_known=int(tin is not None))
        return actual
