"""T-09 F3 (Codex Gate A, D-74): two real processes against one ledger whose remaining allowance fits one
reservation: exactly one reserves. Each process loads the ledger, both wait at a barrier (so both hold the
same pre-reservation view, the race the finding describes), then both reserve. The reservation re-reads the
ledger under an exclusive ``flock`` on ``<ledger>.lock``, so the second sees the first's record and is
refused ``project_cap``. On the code before T-09 both reserve and the ledger passes the cap."""

import multiprocessing as mp
from pathlib import Path

import pytest

from ring3bridge.config import Price
from ring3bridge.ledger import BudgetRefused, Ledger, cost_uusd

FAKE = Price(2_000_000, 8_000_000, "https://example.invalid/synthetic-test-price", "2026-11-02")
REQUEST = b"x" * 100
MAX_OUT = 1000
AMOUNT = cost_uusd(100, 2_000_000) + cost_uusd(MAX_OUT, 8_000_000)  # 200 + 8,000 µUSD
CAP = AMOUNT + AMOUNT // 2  # room for one reservation, not two


def _reserve_after_barrier(path: Path, barrier, q, name: str) -> None:
    led = Ledger(CAP, path)  # both processes load the same (empty) ledger ...
    barrier.wait()  # ... and only then race to reserve
    try:
        r = led.reserve(name, REQUEST, MAX_OUT, FAKE, 10_000_000)
        q.put((name, "reserved", r.uusd))
    except BudgetRefused as e:
        q.put((name, e.reason, 0))


@pytest.mark.parametrize("attempt", range(3))
def test_t09_f3_two_processes_one_allowance(tmp_path, attempt):
    path = tmp_path / "ledger.jsonl"
    ctx = mp.get_context("spawn")
    barrier, q = ctx.Barrier(2), ctx.Queue()
    procs = [ctx.Process(target=_reserve_after_barrier, args=(path, barrier, q, n)) for n in ("a", "b")]
    for p in procs:
        p.start()
    results = sorted(q.get(timeout=60) for _ in procs)
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert sorted(kind for _, kind, _ in results) == ["project_cap", "reserved"], results
    led = Ledger(CAP, path)
    assert led.committed_uusd() == AMOUNT <= CAP  # one reservation on disk, the cap holds
    assert (tmp_path / "ledger.jsonl.lock").exists()


def test_t09_f3_stale_view_is_refreshed_under_the_lock(tmp_path):
    """Sequential form of the same race: a ledger loaded before another process reserved sees that
    reservation when it reserves (reload under the lock), and a settlement goes through the lock too."""
    path = tmp_path / "ledger.jsonl"
    stale = Ledger(CAP, path)  # loaded empty
    fresh = Ledger(CAP, path)
    r = fresh.reserve("other", REQUEST, MAX_OUT, FAKE, 10_000_000)
    with pytest.raises(BudgetRefused) as e:
        stale.reserve("mine", REQUEST, MAX_OUT, FAKE, 10_000_000)
    assert e.value.reason == "project_cap"
    assert stale.committed_uusd() == AMOUNT  # the stale instance now holds the other process's record
    assert fresh.settle(r, 10, 10) == cost_uusd(10, 2_000_000) + cost_uusd(10, 8_000_000)
    with pytest.raises(ValueError):
        stale.settle(r, 10, 10)  # already settled, seen through the reload
    assert Ledger(CAP, path).settled_uusd() == cost_uusd(10, 2_000_000) + cost_uusd(10, 8_000_000)
