"""Fake transport and spend helpers for witness tests (no network, no key)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ring3bridge.ledger import Ledger
from ring3bridge.models.transport import TransportError, TransportResponse
from ring3bridge.witness.sam import LedgerImageSpend, SamClient, load_sam_config

RUN = "run-witness-test"


class FakeTransport:
    """Returns queued responses (TransportResponse or TransportError) and records every post."""

    def __init__(self, *responses, on_post: Callable[[], None] | None = None) -> None:
        self.responses = list(responses)
        self.posts: list[tuple[str, bytes, int]] = []
        self.on_post = on_post

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse:
        if self.on_post is not None:
            self.on_post()
        self.posts.append((url, body, timeout_s))
        if not self.responses:
            raise AssertionError("unexpected SAM post")
        r = self.responses.pop(0)
        if isinstance(r, TransportError):
            raise r
        return r


def ok(body: bytes) -> TransportResponse:
    return TransportResponse(200, body)


def make_client(tmp: Path, *responses, budget_uusd: int | None = 100_000, cap: int = 30_000_000,
                check_reserved: bool = True):  # fmt: skip
    """-> (SamClient, FakeTransport, Ledger). With ``check_reserved`` every post asserts that one unsettled
    image reservation exists at that moment (INV-09: reserve before the request)."""
    cfg = load_sam_config()
    ledger = Ledger(cap, tmp / "ledger.jsonl")

    def reserved_now() -> None:
        fresh = Ledger(cap, tmp / "ledger.jsonl")  # what is on disk, not in memory
        assert fresh.unsettled_uusd(RUN) == cfg.price_uusd_per_image, "SAM post without a reserved image"

    t = FakeTransport(*responses, on_post=reserved_now if check_reserved else None)
    return SamClient(cfg, t, LedgerImageSpend(ledger, RUN, cfg, budget_uusd)), t, ledger
