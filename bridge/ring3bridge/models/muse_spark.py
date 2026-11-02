"""Muse Spark adapter over a ``Transport`` (D-06, D-55, ARCHITECTURE §6, INV-09).

Order for every request, enforced here:
  1. build the request body (no key in it);
  2. ``ledger.reserve`` (worst case: 1 token per request byte + max output tokens) - refused -> ``ModelStop``;
  3. log ``spend_reserved``, then a header-free ``model_request`` (body as a blob);
  4. ``transport.post``;
  5. log ``model_reply`` (status + scrubbed body blob), settle the reservation, log ``spend_settled``.
The first 401/403, any other non-200 status or any transport error stops the client: every later ``ask()``
raises ``ModelStop`` without sending.

Request and response shape: Meta's Responses API (https://dev.meta.ai/docs/protocols/responses, checked by
the lead on 2026-11-01, D-55): ``POST <endpoint>`` with ``model``, ``instructions`` (re-sent every turn),
``input``, ``max_output_tokens`` (>= 16; it bounds reasoning and visible output together, so the reservation
takes it in full) and ``store: false``. The reply text is ``output[].content[].text`` where
``type == "output_text"``; usage is ``usage.input_tokens``, ``usage.output_tokens`` and
``usage.input_tokens_details.cached_tokens``; ``status`` must be ``"completed"``, anything else (incomplete,
failed, ...) is an invalid reply. ``reasoning: {"effort": <value>}`` is sent when the config sets
``reasoning_effort`` (D-61: ``"low"``; documented values minimal/low/medium/high/xhigh; ``"none"`` is HTTP 400
for Muse Spark and is refused before any body is built). Structured output (``text.format``) is not used: the
bridge's strict validation is authoritative either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ring3bridge import abi
from ring3bridge.config import REASONING_EFFORTS, ModelConfig
from ring3bridge.eventlog import EventLog
from ring3bridge.ledger import BudgetRefused, Ledger
from ring3bridge.models.transport import Transport, TransportError

STATUS_COMPLETED = "completed"


class ModelStop(Exception):
    """The run must stop; ``code`` is the LINK_STOP_* reason sent to the kernel."""

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"{abi.STOP_NAMES.get(code, '?')}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ModelReply:
    text: str  # the model's text, still untrusted: goes to strictjson.validate and nowhere else
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None = None
    status: str = ""  # the reply's ``status`` field ("" when absent or not a string)
    incomplete_reason: str = ""  # ``incomplete_details.reason`` (e.g. "max_output_tokens"), bounded, else ""
    texts: int = 0  # how many ``output_text`` items the reply held (exactly one is valid)


MAX_REASON_LEN = 32


def _reason_token(v: object) -> str:
    """A short lower-case identifier from the API, or "" (it is quoted to the model, so it is bounded)."""
    if type(v) is str and 0 < len(v) <= MAX_REASON_LEN and all(c.isalnum() or c == "_" for c in v):
        return v.lower()
    return ""


def build_request(
    model_id: str,
    instructions: str,
    user_input: str,
    max_output_tokens: int,
    reasoning_effort: str | None = None,
) -> bytes:
    """Responses API body (D-55, D-61). Canonical JSON bytes: the reservation is sized on what is sent.

    ``reasoning_effort`` must be one of the documented values or ``None`` (field absent); anything else, in
    particular ``"none"``, raises before a body exists."""
    body: dict[str, object] = {
        "model": model_id,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    if reasoning_effort is not None:
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("reasoning_effort must be one of the documented values (D-61)")
        body["reasoning"] = {"effort": reasoning_effort}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _nonneg_int(v: object) -> int | None:
    return v if type(v) is int and v >= 0 else None


def parse_response(body: bytes) -> ModelReply:
    """Responses API reply (D-55). Never raises: anything unexpected yields text "" (strictjson rejects it).

    Text is taken only from a reply whose ``status`` is ``"completed"`` and that holds exactly one
    ``output_text`` item. Usage is parsed whenever present so an incomplete reply still settles on real
    numbers. ``incomplete_reason`` carries ``incomplete_details.reason`` (Meta docs: "max_output_tokens" when
    reasoning plus output hit the cap) so the run loop can tell the model why nothing arrived."""
    try:
        d = json.loads(body)
    except (ValueError, RecursionError):
        return ModelReply("", None, None)
    if type(d) is not dict:
        return ModelReply("", None, None)
    status = d.get("status")
    status = status if type(status) is str else ""
    inc = d.get("incomplete_details")
    incomplete_reason = _reason_token(inc.get("reason")) if type(inc) is dict else ""
    texts: list[str] = []
    out = d.get("output")
    if type(out) is list:
        for item in out:
            content = item.get("content") if type(item) is dict else None
            if type(content) is list:
                for c in content:
                    if type(c) is dict and c.get("type") == "output_text" and type(c.get("text")) is str:
                        texts.append(c["text"])
    usage = d.get("usage")
    usage = usage if type(usage) is dict else {}
    tin = _nonneg_int(usage.get("input_tokens"))
    tout = _nonneg_int(usage.get("output_tokens"))
    details = usage.get("input_tokens_details")
    cached = _nonneg_int(details.get("cached_tokens")) if type(details) is dict else None
    text = texts[0] if len(texts) == 1 and status == STATUS_COMPLETED else ""
    return ModelReply(text, tin, tout, cached, status, incomplete_reason, len(texts))


class MuseSparkClient:
    def __init__(
        self,
        config: ModelConfig,
        transport: Transport,
        ledger: Ledger,
        log: EventLog,
        run_id: str,
        run_budget_uusd: int | None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.ledger = ledger
        self.log = log
        self.run_id = run_id
        self.run_budget_uusd = run_budget_uusd
        self.stopped: ModelStop | None = None
        self.requests = 0

    def ask(self, instructions: str, user_input: str) -> ModelReply:
        if self.stopped is not None:
            raise ModelStop(self.stopped.code, "client stopped earlier: " + self.stopped.detail)
        cfg = self.config
        body = build_request(cfg.id, instructions, user_input, cfg.max_output_tokens, cfg.reasoning_effort)
        try:
            res = self.ledger.reserve(
                self.run_id, body, cfg.max_output_tokens, cfg.price, self.run_budget_uusd
            )
        except BudgetRefused as e:
            self.stopped = ModelStop(abi.LINK_STOP_BUDGET, e.reason)
            raise self.stopped from None
        self.log.append("spend_reserved", reservation=res.id, uusd=res.uusd, in_tokens=res.input_tokens,
                        max_out_tokens=res.max_output_tokens)  # fmt: skip
        self.log.append("model_request", model_id=cfg.id, endpoint=cfg.endpoint, bytes=len(body),
                        request_blob=self.log.blob(body), reservation=res.id)  # fmt: skip
        self.requests += 1
        failure: ModelStop | None = None
        try:
            resp = self.transport.post(cfg.endpoint, body, cfg.timeout_s)
        except TransportError as e:
            code = abi.LINK_STOP_TIMEOUT if e.kind == "timeout" else abi.LINK_STOP_TRANSPORT
            failure = ModelStop(code, f"transport {e.kind}")
        if failure is not None:
            self.log.append("model_reply", status=0, bytes=0, error=failure.detail, reservation=res.id)
            self._settle(res, None, None, None)
            self.stopped = failure
            raise failure
        blob = self.log.blob(resp.body)
        self.log.append(
            "model_reply", status=resp.status, bytes=len(resp.body), blob=blob, reservation=res.id
        )
        if resp.status in (401, 403):
            self._settle(res, None, None, None)
            self.stopped = ModelStop(abi.LINK_STOP_MODEL_AUTH, f"HTTP {resp.status}")
            raise self.stopped
        if resp.status != 200:
            self._settle(res, None, None, None)
            self.stopped = ModelStop(abi.LINK_STOP_TRANSPORT, f"HTTP {resp.status}")
            raise self.stopped
        reply = parse_response(resp.body)
        self._settle(res, reply.input_tokens, reply.output_tokens, reply.cached_tokens)
        return reply

    def _settle(self, res, tin: int | None, tout: int | None, cached: int | None) -> None:
        actual = self.ledger.settle(res, tin, tout, cached)
        self.log.append("spend_settled", reservation=res.id, uusd=actual, usage_known=int(tin is not None))
