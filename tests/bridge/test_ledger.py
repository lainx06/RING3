"""Step 5: spend ledger (INV-09). Synthetic price fixture only; the shipped config must fail closed."""

import pytest

from ring3bridge.config import ConfigError, Price, load_model_config, load_project_cap
from ring3bridge.ledger import (
    LABEL,
    BudgetRefused,
    Ledger,
    cost_uusd,
    fmt_usd,
    request_tokens,
    usd_to_uusd,
)

# Synthetic, deliberately not Meta's price: $2 per 1M input tokens, $8 per 1M output tokens.
FAKE = Price(2_000_000, 8_000_000, "https://example.invalid/synthetic-test-price", "2026-11-02")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(project_cap_uusd=30_000_000, path=tmp_path / "ledger.jsonl")


def test_shipped_config_has_no_price_and_fails_closed(tmp_path):
    cfg = load_model_config()
    assert cfg.id == "muse-spark-1.3"
    assert cfg.endpoint == "https://api.meta.ai/v1/responses"
    assert cfg.price is None  # [UNVERIFIED]: not from Meta's official pricing page
    assert load_project_cap() == 30_000_000  # $30, D-12
    led = Ledger(load_project_cap(), tmp_path / "l.jsonl")
    with pytest.raises(BudgetRefused) as e:
        led.reserve("run1", "hello", cfg.max_output_tokens, cfg.price, usd_to_uusd("1"))
    assert e.value.reason == "no_price"
    assert not (tmp_path / "l.jsonl").exists()  # nothing reserved, nothing logged as spent


def test_reservation_size_is_bytes_plus_max_output(ledger):
    req = "fan to 60 % é"  # é is 2 UTF-8 bytes
    assert request_tokens(req) == len(req.encode()) == 14
    r = ledger.reserve("run1", req, 512, FAKE, usd_to_uusd("1"))
    assert r.input_tokens == 14 and r.max_output_tokens == 512
    assert r.uusd == cost_uusd(14, 2_000_000) + cost_uusd(512, 8_000_000) == 28 + 4096


def test_cost_rounds_up():
    assert cost_uusd(1, 1) == 1  # 1e-6 µUSD -> 1 µUSD, never under-reserve
    assert cost_uusd(0, 5_000_000) == 0
    assert cost_uusd(3, 333_334) == 2


def test_no_run_budget_refused(ledger):
    for b in (None, 0, -5):
        with pytest.raises(BudgetRefused) as e:
            ledger.reserve("run1", "x", 10, FAKE, b)
        assert e.value.reason == "no_run_budget"


def test_run_cap_counts_unsettled_reservations(ledger):
    budget = 10_000  # $0.01
    r1 = ledger.reserve("run1", "a" * 1000, 512, FAKE, budget)  # 2000 + 4096 = 6096
    assert r1.uusd == 6096
    with pytest.raises(BudgetRefused) as e:  # 6096 unsettled + 6096 > 10000
        ledger.reserve("run1", "a" * 1000, 512, FAKE, budget)
    assert e.value.reason == "run_cap"
    ledger.settle(r1, input_tokens=300, output_tokens=100)  # 600 + 800 = 1400
    assert ledger.committed_uusd("run1") == 1400
    ledger.reserve("run1", "a" * 1000, 512, FAKE, budget)  # 1400 + 6096 <= 10000
    # another run has its own run budget but shares the project cap
    ledger.reserve("run2", "a", 1, FAKE, budget)


def test_project_cap_counts_unsettled_across_runs(tmp_path):
    led = Ledger(project_cap_uusd=10_000, path=tmp_path / "l.jsonl")
    led.reserve("run1", "a" * 1000, 512, FAKE, 1_000_000)
    with pytest.raises(BudgetRefused) as e:
        led.reserve("run2", "a" * 1000, 512, FAKE, 1_000_000)
    assert e.value.reason == "project_cap"


def test_settle_unknown_usage_is_full_reservation(ledger):
    r = ledger.reserve("run1", "abc", 100, FAKE, 1_000_000)
    assert ledger.settle(r) == r.uusd
    with pytest.raises(ValueError):
        ledger.settle(r)


def test_persists_and_reloads(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(30_000_000, p)
    r = led.reserve("run1", "abc", 100, FAKE, 1_000_000)
    led.reserve("run1", "abcd", 100, FAKE, 1_000_000)
    led.settle(r, 3, 10)
    again = Ledger(30_000_000, p)
    assert again.settled_uusd() == led.settled_uusd() == 6 + 80
    assert again.unsettled_uusd() == led.unsettled_uusd() == 8 + 800
    for line in p.read_text().splitlines():  # integers only, canonical
        assert "." not in line.split('"run"')[0] and ", " not in line


def test_unreadable_ledger_fails_closed(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"kind":"reserve","id":1}\n')
    led = Ledger(30_000_000, p)
    with pytest.raises(BudgetRefused) as e:
        led.reserve("run1", "x", 1, FAKE, 1_000_000)
    assert e.value.reason == "ledger_unreadable"
    assert "unreadable" in led.report()


def test_report_is_labelled_estimate(ledger):
    ledger.reserve("run1", "abc", 100, FAKE, 1_000_000)
    rep = ledger.report()
    assert LABEL == "estimate, not billing"
    assert LABEL in rep and "$0.000806" in rep


def test_usd_parsing():
    assert usd_to_uusd("0.50") == 500_000
    assert usd_to_uusd("30") == 30_000_000
    assert fmt_usd(1_234_567) == "$1.234567"
    for bad in ("0", "-1", "nan", "inf", "0.0000001", "abc", ""):
        with pytest.raises(ValueError):
            usd_to_uusd(bad)


def test_config_price_needs_source_and_date(tmp_path):
    base = '[model]\nid = "muse-spark-1.3"\nendpoint = "https://api.meta.ai/v1/responses"\n'
    base += "max_output_tokens = 512\ntimeout_s = 60\n"
    p = tmp_path / "m.toml"
    p.write_text(base + "price_input_uusd_per_mtok = 1\nprice_output_uusd_per_mtok = 2\n")
    with pytest.raises(ConfigError):
        load_model_config(p)
    p.write_text(base + 'price_input_uusd_per_mtok = 1.5\nprice_output_uusd_per_mtok = 2\nprice_source = '
                 '"https://x.example/p"\nprice_checked = "2026-11-02"\n')  # fmt: skip
    with pytest.raises(ConfigError):
        load_model_config(p)
    p.write_text(base + 'price_input_uusd_per_mtok = 1\nprice_output_uusd_per_mtok = 2\nprice_source = '
                 '"https://x.example/p"\nprice_checked = "2026-11-02"\n')  # fmt: skip
    assert load_model_config(p).price == Price(1, 2, "https://x.example/p", "2026-11-02")
    p.write_text(base.replace("muse-spark-1.3", "muse-spark-1.3-contributor"))
    with pytest.raises(ConfigError):
        load_model_config(p)  # D-06
