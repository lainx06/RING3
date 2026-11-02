"""Spend ledger (INV-09). Integer µUSD, append-only JSON lines, reserve before every model request.

Reservation size (D-52): input tokens = 1 per UTF-8 byte of the whole request (no tokenizer guesses) plus the
request's max output tokens, priced from config/models.toml and rounded up. Unsettled reservations count
against both the run cap (``--budget-usd``) and the project cap (config/budget.toml) until settled. No price,
no run budget, a cap that would be passed, or an unreadable ledger -> ``BudgetRefused``, nothing sent.

Every figure is an estimate from configured prices, not billing.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ring3bridge.config import REPO, Price

DEFAULT_LEDGER = REPO / "runs" / "ledger.jsonl"
LABEL = "estimate, not billing"


class BudgetRefused(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason  # no_price | no_run_budget | run_cap | project_cap | ledger_unreadable


def usd_to_uusd(text: str) -> int:
    """'0.50' -> 500000. Exact; at most 6 decimals; must be > 0."""
    try:
        d = Decimal(text)
    except (InvalidOperation, TypeError):
        raise ValueError(f"not a dollar amount: {text!r}") from None
    if not d.is_finite() or d <= 0:
        raise ValueError("budget must be a positive amount")
    u = d * 1_000_000
    if u != u.to_integral_value():
        raise ValueError("at most 6 decimals")
    return int(u)


def fmt_usd(uusd: int) -> str:
    return f"${uusd // 1_000_000}.{uusd % 1_000_000:06d}"


def cost_uusd(tokens: int, uusd_per_mtok: int) -> int:
    """Rounded up: never under-reserve."""
    return -(-tokens * uusd_per_mtok // 1_000_000)


def request_tokens(request: bytes | str) -> int:
    return len(request.encode("utf-8") if isinstance(request, str) else request)


@dataclass(frozen=True)
class Reservation:
    id: int
    run_id: str
    uusd: int
    input_tokens: int
    max_output_tokens: int
    price_in: int
    price_out: int
    price_cached: int = 0  # 0 = no cached-input price: cached tokens settle at the input price


def _canon(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Ledger:
    def __init__(self, project_cap_uusd: int, path: Path = DEFAULT_LEDGER) -> None:
        self.path = Path(path)
        self.project_cap = project_cap_uusd
        self._res: dict[int, Reservation] = {}
        self._settled: dict[int, int] = {}
        self._error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for n, line in enumerate(self.path.read_text("ascii").splitlines(), 1):
                rec = json.loads(line)
                if rec["kind"] == "reserve":
                    vals = [rec[k] for k in ("id", "uusd", "in_tok", "out_tok", "p_in", "p_out")]
                    vals.append(rec.get("p_cached", 0))
                    if not all(type(v) is int and v >= 0 for v in vals) or vals[0] in self._res:
                        raise ValueError(f"line {n}")
                    r = Reservation(vals[0], str(rec["run"]), *vals[1:])
                    self._res[r.id] = r
                elif rec["kind"] == "settle":
                    rid, u = rec["id"], rec["uusd"]
                    if rid not in self._res or rid in self._settled or type(u) is not int or u < 0:
                        raise ValueError(f"line {n}")
                    self._settled[rid] = u
                else:
                    raise ValueError(f"line {n}")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
            self._error = f"{self.path.name}: {e}"

    def _append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="ascii") as f:
            f.write(_canon(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ---- totals ----
    def settled_uusd(self, run_id: str | None = None) -> int:
        return sum(u for rid, u in self._settled.items() if run_id is None or self._res[rid].run_id == run_id)

    def unsettled_uusd(self, run_id: str | None = None) -> int:
        return sum(
            r.uusd
            for rid, r in self._res.items()
            if rid not in self._settled and (run_id is None or r.run_id == run_id)
        )

    def committed_uusd(self, run_id: str | None = None) -> int:
        return self.settled_uusd(run_id) + self.unsettled_uusd(run_id)

    # ---- reserve / settle ----
    def reserve(
        self,
        run_id: str,
        request: bytes | str,
        max_output_tokens: int,
        price: Price | None,
        run_budget_uusd: int | None,
    ) -> Reservation:
        if self._error:
            raise BudgetRefused("ledger_unreadable", self._error)
        if price is None:
            raise BudgetRefused("no_price", "model price unset in config/models.toml")
        if not run_budget_uusd or run_budget_uusd <= 0:
            raise BudgetRefused("no_run_budget", "--budget-usd is required")
        tin = request_tokens(request)
        amount = cost_uusd(tin, price.input_uusd_per_mtok) + cost_uusd(
            max_output_tokens, price.output_uusd_per_mtok
        )
        if self.committed_uusd(run_id) + amount > run_budget_uusd:
            raise BudgetRefused(
                "run_cap", f"{fmt_usd(amount)} would pass the run budget {fmt_usd(run_budget_uusd)}"
            )
        if self.committed_uusd() + amount > self.project_cap:
            raise BudgetRefused("project_cap", f"{fmt_usd(amount)} would pass the project cap")
        rid = max(self._res, default=0) + 1
        r = Reservation(rid, run_id, amount, tin, max_output_tokens, price.input_uusd_per_mtok,
                        price.output_uusd_per_mtok, price.cached_input_uusd_per_mtok or 0)  # fmt: skip
        self._append({"kind": "reserve", "id": rid, "run": run_id, "uusd": amount, "in_tok": tin,
                      "out_tok": max_output_tokens, "p_in": r.price_in, "p_out": r.price_out,
                      "p_cached": r.price_cached, "t_ns": time.time_ns()})  # fmt: skip
        self._res[rid] = r
        return r

    def settle(
        self,
        r: Reservation,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
    ) -> int:
        """Settles with reported usage; unknown usage settles at the full reservation (conservative).

        ``cached_tokens`` (a subset of ``input_tokens``, Responses API ``usage.input_tokens_details``) are
        priced at the cached-input price when the reservation has one; unknown or out of range -> none."""
        if r.id in self._settled:
            raise ValueError("already settled")
        if input_tokens is None or output_tokens is None or input_tokens < 0 or output_tokens < 0:
            actual = r.uusd
        else:
            cached = cached_tokens if cached_tokens is not None and 0 <= cached_tokens <= input_tokens else 0
            if not r.price_cached:
                cached = 0
            actual = (
                cost_uusd(input_tokens - cached, r.price_in)
                + cost_uusd(cached, r.price_cached)
                + cost_uusd(output_tokens, r.price_out)
            )
        self._append({"kind": "settle", "id": r.id, "uusd": actual, "t_ns": time.time_ns()})
        self._settled[r.id] = actual
        return actual

    def report(self) -> str:
        runs = sorted({r.run_id for r in self._res.values()})
        lines = [f"RING3 spend ({LABEL})"]
        if self._error:
            lines.append(f"ledger unreadable: {self._error} (reservations refused)")
        lines.append(f"  settled:              {fmt_usd(self.settled_uusd())}")
        lines.append(f"  reserved, unsettled:  {fmt_usd(self.unsettled_uusd())}")
        total, cap = fmt_usd(self.committed_uusd()), fmt_usd(self.project_cap)
        lines.append(f"  total:                {total} of project cap {cap}")
        lines.append(
            f"  requests: {len(self._res)} reserved, {len(self._settled)} settled, runs: {len(runs)}"
        )
        for run in runs:
            lines.append(f"    {run}: {fmt_usd(self.committed_uusd(run))}")
        return "\n".join(lines)
