"""Muse Spark adapter over a ``Transport`` (D-06, ARCHITECTURE §6, INV-09).

Order for every request, enforced here:
  1. build the request body (no key in it);
  2. ``ledger.reserve`` (worst case: 1 token per request byte + max output tokens) - refused -> ``ModelStop``;
  3. log ``spend_reserved``, then a header-free ``model_request`` (body as a blob);
  4. ``transport.post``;
  5. log ``model_reply`` (status + scrubbed body blob), settle the reservation, log ``spend_settled``.
The first 401/403, any other non-200 status or any transport error stops the client: every later ``ask()``
raises ``ModelStop`` without sending.

[UNVERIFIED] Request and response field names below (``model``, ``instructions``, ``input``,
``max_output_tokens``; ``output[].content[].text`` with ``type == "output_text"``; ``usage.input_tokens`` /
``usage.output_tokens``) follow the common Responses-API shape. They were not checked against Meta's public
API docs in T-03a (no network access to Meta). The endpoint and model ID come from config/models.toml
(ARCHITECTURE §6, seen in the authenticated catalog 2026-10-31).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ring3bridge import abi
from ring3bridge.config import ModelConfig
from ring3bridge.eventlog import EventLog
from ring3bridge.ledger import BudgetRefused, Ledger
from ring3bridge.models.transport import Transport, TransportError


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


def build_request(model_id: str, instructions: str, user_input: str, max_output_tokens: int) -> bytes:
    """[UNVERIFIED] field names. Canonical JSON bytes, so the reservation is sized on exactly what is sent."""
    body = {
        "model": model_id,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": max_output_tokens,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def parse_response(body: bytes) -> ModelReply:
    """[UNVERIFIED] field names. Never raises: anything unexpected yields text "" (strictjson rejects it)."""
    try:
        d = json.loads(body)
    except (ValueError, RecursionError):
        return ModelReply("", None, None)
    texts: list[str] = []
    out = d.get("output") if type(d) is dict else None
    if type(out) is list:
        for item in out:
            content = item.get("content") if type(item) is dict else None
            if type(content) is list:
                for c in content:
                    if type(c) is dict and c.get("type") == "output_text" and type(c.get("text")) is str:
                        texts.append(c["text"])
    usage = d.get("usage") if type(d) is dict else None
    tin = usage.get("input_tokens") if type(usage) is dict else None
    tout = usage.get("output_tokens") if type(usage) is dict else None
    tin = tin if type(tin) is int and tin >= 0 else None
    tout = tout if type(tout) is int and tout >= 0 else None
    return ModelReply(texts[0] if len(texts) == 1 else "", tin, tout)


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
        body = build_request(cfg.id, instructions, user_input, cfg.max_output_tokens)
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
            self._settle(res, None, None)
            self.stopped = failure
            raise failure
        blob = self.log.blob(resp.body)
        self.log.append(
            "model_reply", status=resp.status, bytes=len(resp.body), blob=blob, reservation=res.id
        )
        if resp.status in (401, 403):
            self._settle(res, None, None)
            self.stopped = ModelStop(abi.LINK_STOP_MODEL_AUTH, f"HTTP {resp.status}")
            raise self.stopped
        if resp.status != 200:
            self._settle(res, None, None)
            self.stopped = ModelStop(abi.LINK_STOP_TRANSPORT, f"HTTP {resp.status}")
            raise self.stopped
        reply = parse_response(resp.body)
        self._settle(res, reply.input_tokens, reply.output_tokens)
        return reply

    def _settle(self, res, tin: int | None, tout: int | None) -> None:
        actual = self.ledger.settle(res, tin, tout)
        self.log.append("spend_settled", reservation=res.id, uusd=actual, usage_known=int(tin is not None))
