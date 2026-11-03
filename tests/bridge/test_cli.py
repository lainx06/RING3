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


def test_run_requires_a_budget(capsys):
    """INV-09: `ring3 run` has no default budget; argparse refuses without --budget-usd (no network)."""
    with pytest.raises(SystemExit) as e:
        cli.main(["run"])
    assert e.value.code == 2
    assert "--budget-usd" in capsys.readouterr().err


def test_run_dry_run_prints_the_reservation(capsys):
    """The dry run sizes the first reservation from the shipped config and pack: no network, no key."""
    assert cli.main(["run", "--budget-usd", "0.50", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "estimate, not billing" in out and "--budget-usd 0.50 = $0.500000" in out
    assert "input tokens reserved" in out and "muse-spark-1.3" in out


def test_replay_on_the_sample_run(tmp_path, capsys):
    """`ring3 replay` renders the tape and runs the host policy build when it exists; the sample run's single
    synthetic event is not a real tape, so the result is reported, not asserted as a match."""
    run = tmp_path / "r-replay"
    write_sample_run(run)
    rc = cli.main(["replay", str(run)])
    out = capsys.readouterr().out
    assert rc in (0, 1) and ("REPLAY" in out or "MISMATCH" in out)
    assert "tape events by kind:" in out


def test_run_dry_run_with_the_redteam_pack(capsys):
    """`--pack agent-v2-redteam` selects the pack by name; the plan names it with its hash prefix (D-69)."""
    from ring3bridge import context_pack

    assert cli.main(["run", "--budget-usd", "0.50", "--dry-run", "--pack", "agent-v2-redteam"]) == 0
    out = capsys.readouterr().out
    rt = context_pack.load("agent-v2-redteam")
    assert f"context pack agent-v2-redteam: {len(rt.text)} B, sha256 {rt.sha256[:16]}..." in out


def test_tape_prints_tape_lines(tmp_path, capsys):
    """Regression: `ring3 tape` was registered with `func=` while main() calls `a.fn`, so it raised
    AttributeError. It must print the run's event records as TAPE lines (T-05a's replay table uses it)."""
    from ring3bridge import replay

    run = tmp_path / "r-tape"
    write_sample_run(run)
    assert cli.main(["tape", str(run)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == replay.tape_lines(replay.read_records(run))
    assert out and all(line.startswith("TAPE ") for line in out)
