"""Step 10 / A4: ``ring3 verify`` detects a one-byte edit; ``ring3 spend`` works on a sample ledger."""

import pytest

from ring3bridge import cli
from ring3bridge.config import Price
from ring3bridge.ledger import Ledger

from .sample_run import write_sample_run

FAKE = Price(2_000_000, 8_000_000, "https://example.invalid/synthetic-test-price", "2026-11-02")


def test_verify_ok_then_one_byte_edit(tmp_path, capsys):
    run = tmp_path / "r-cli"
    write_sample_run(run)
    assert cli.main(["verify", str(run)]) == 0
    assert capsys.readouterr().out.startswith("CHAIN OK")
    p = run / "events.jsonl"
    b = bytearray(p.read_bytes())
    b[len(b) // 2] ^= 0x01
    p.write_bytes(bytes(b))
    assert cli.main(["verify", str(run)]) == 1
    assert capsys.readouterr().out.startswith("BROKEN at record #")


def test_verify_missing_run(tmp_path, capsys):
    assert cli.main(["verify", str(tmp_path / "nope")]) == 1
    assert "BROKEN" in capsys.readouterr().out


def test_spend_on_sample_ledger(tmp_path, capsys):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(30_000_000, path)
    r = led.reserve("run-a", "x" * 1000, 512, FAKE, 1_000_000)
    led.settle(r, 900, 40)
    led.reserve("run-b", "y" * 10, 512, FAKE, 1_000_000)
    assert cli.main(["spend", "--ledger", str(path)]) == 0
    out = capsys.readouterr().out
    assert "estimate, not billing" in out
    assert "settled:              $0.002120" in out
    assert "reserved, unsettled:  $0.004116" in out
    assert "total:                $0.006236 of project cap $30.000000" in out


def test_spend_with_no_ledger(tmp_path, capsys):
    assert cli.main(["spend", "--ledger", str(tmp_path / "none.jsonl")]) == 0
    assert "$0.000000" in capsys.readouterr().out


def test_frames_selftest(capsys):
    assert cli.main(["frames-selftest"]) == 0
    assert "FRAMES OK" in capsys.readouterr().out


@pytest.mark.parametrize("cmd,card", [("run", "T-03b"), ("replay", "T-05a"), ("fuzz-link", "T-05a")])
def test_stubs_name_their_card(cmd, card, capsys):
    assert cli.main([cmd, "--anything"]) == 2
    assert card in capsys.readouterr().err
