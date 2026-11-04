"""SAM adapter spend and stop rules (INV-09 for the witness): one image reserved first; 401/403 stop."""

import json

import pytest

from ring3bridge.config import ConfigError
from ring3bridge.ledger import BudgetRefused, Ledger
from ring3bridge.models.transport import TransportError, TransportResponse
from ring3bridge.witness import SAM_FORMAT, CantVerify
from ring3bridge.witness.sam import (
    LedgerImageSpend,
    SamClient,
    SamConfig,
    SamStop,
    load_sam_config,
)
from tests.witness.fakes import RUN, FakeTransport, make_client, ok
from tests.witness.synth import envelope

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
W, H = 920, 570
LINE = "<0f>0<|box;x1=440;y1=320;x2=480;y2=430;w=920;h=570;c=0.93|><|mask;x=0;y=0;data=111,41,~abc|>"


def _ledger_lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]


def test_shipped_sam_config_has_the_official_price():
    cfg = load_sam_config()
    assert cfg.model_id == "sam-3.1" and cfg.endpoint == "https://api.meta.ai/v1/responses"
    assert cfg.price_uusd_per_image == 2500  # $2.50 per 1,000 images (D-56)
    assert cfg.price_source.startswith("https://dev.meta.ai/") and cfg.price_checked == "2026-11-02"


def test_one_image_reserved_before_every_call_then_settled(tmp_path):
    client, t, ledger = make_client(tmp_path, ok(envelope(LINE)), ok(envelope("")))
    r1 = client.segment(JPEG, W, H)
    r2 = client.segment(JPEG, W, H)
    assert len(r1.records) == 1 and r2.records == [] and len(t.posts) == 2
    kinds = [x["kind"] for x in _ledger_lines(tmp_path)]
    assert kinds == ["reserve", "settle", "reserve", "settle"]
    assert all(x["uusd"] == 2500 for x in _ledger_lines(tmp_path))
    assert ledger.unsettled_uusd() == 0 and ledger.settled_uusd(RUN) == 5000
    assert r1.uusd == 2500


def test_no_price_refuses_and_sends_nothing(tmp_path):
    cfg = SamConfig("sam-3.1", "https://api.meta.ai/v1/responses", 60, None)
    t = FakeTransport()
    client = SamClient(cfg, t, LedgerImageSpend(Ledger(30_000_000, tmp_path / "l.jsonl"), RUN, cfg, 100_000))
    with pytest.raises(BudgetRefused) as e:
        client.segment(JPEG, W, H)
    assert e.value.reason == "no_price" and t.posts == []
    assert not (tmp_path / "l.jsonl").exists()


@pytest.mark.parametrize("budget,reason", [(None, "no_run_budget"), (2499, "run_cap")])
def test_budget_refusal_sends_nothing(tmp_path, budget, reason):
    client, t, _ = make_client(tmp_path, budget_uusd=budget)
    with pytest.raises(BudgetRefused) as e:
        client.segment(JPEG, W, H)
    assert e.value.reason == reason and t.posts == []


def test_project_cap_refuses(tmp_path):
    client, t, _ = make_client(tmp_path, cap=2499)
    with pytest.raises(BudgetRefused) as e:
        client.segment(JPEG, W, H)
    assert e.value.reason == "project_cap" and t.posts == []


@pytest.mark.parametrize(
    "resp",
    [
        TransportResponse(401, b'{"error":"unauthorized"}'),
        TransportResponse(403, b'{"error":"forbidden"}'),
        TransportResponse(429, b"{}"),
        TransportResponse(500, b"{}"),
        TransportResponse(302, b""),
        TransportError("timeout"),
        TransportError("network"),
        TransportError("too_large", 200),
    ],
    ids=["401", "403", "429", "500", "302", "timeout", "network", "too_large"],
)
def test_auth_and_transport_failures_stop_the_run(tmp_path, resp):
    client, t, ledger = make_client(tmp_path, resp)
    with pytest.raises(SamStop):  # never CAN'T VERIFY
        client.segment(JPEG, W, H)
    assert ledger.unsettled_uusd() == 0 and ledger.settled_uusd() == 2500  # settled at the full reservation
    with pytest.raises(SamStop):  # stopped for good: nothing more is sent or reserved
        client.segment(JPEG, W, H)
    assert len(t.posts) == 1 and ledger.settled_uusd() == 2500
    assert client.stopped


def test_unreadable_reply_is_sam_format_after_settling(tmp_path):
    client, _, ledger = make_client(tmp_path, ok(b"not json"))
    with pytest.raises(CantVerify) as e:
        client.segment(JPEG, W, H)
    assert e.value.reason == SAM_FORMAT and ledger.unsettled_uusd() == 0


def test_sam_config_fails_closed(tmp_path):
    p = tmp_path / "m.toml"
    p.write_text(
        '[sam]\nid = "sam-3.1"\nendpoint = "https://x"\ntimeout_s = 5\nprice_uusd_per_image = 2500\n'
    )
    with pytest.raises(ConfigError):
        load_sam_config(p)
    p.write_text('[model]\nid = "x"\n')
    with pytest.raises(ConfigError):
        load_sam_config(p)
    p.write_text('[sam]\nid = "sam-3.1"\nendpoint = "http://x"\ntimeout_s = 5\n')
    with pytest.raises(ConfigError):
        load_sam_config(p)
    p.write_text('[sam]\nid = "sam-3.1"\nendpoint = "https://x"\ntimeout_s = 5\n')
    assert load_sam_config(p).price_uusd_per_image is None
