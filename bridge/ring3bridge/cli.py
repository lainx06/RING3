"""``uv run ring3 <command>``. No command here makes a network call or reads a key."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ring3bridge import eventlog, selftest
from ring3bridge.config import REPO, load_project_cap
from ring3bridge.ledger import DEFAULT_LEDGER, Ledger

RUNS = REPO / "runs"
STUBS = {
    "run": "T-03b",
    "replay": "T-05a",
    "fuzz-link": "T-05a",
}


def _run_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    q = RUNS / arg
    return q


def cmd_verify(a: argparse.Namespace) -> int:
    r = eventlog.verify(_run_dir(a.run))
    print(r.message)
    return 0 if r.ok else 1


def cmd_spend(a: argparse.Namespace) -> int:
    led = Ledger(load_project_cap(), Path(a.ledger) if a.ledger else DEFAULT_LEDGER)
    print(led.report())
    return 0


def cmd_frames_selftest(_a: argparse.Namespace) -> int:
    ok, lines = selftest.run()
    print("\n".join(lines))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in STUBS:
        return _stub(args[0], STUBS[args[0]])
    ap = argparse.ArgumentParser(prog="ring3", description="RING3 host bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a run's hash chain and every blob")
    v.add_argument("run", help="run directory, or a run id under runs/")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("spend", help="ledger totals (estimate, not billing)")
    s.add_argument("--ledger", help="ledger file (default runs/ledger.jsonl)")
    s.set_defaults(fn=cmd_spend)
    f = sub.add_parser("frames-selftest", help="CRC, shared link vectors, COM2 round trips")
    f.set_defaults(fn=cmd_frames_selftest)
    for name, card in STUBS.items():
        sub.add_parser(name, help=f"not implemented yet ({card})")
    a = ap.parse_args(args)
    return a.fn(a)


def _stub(name: str, card: str) -> int:
    print(f"ring3 {name}: not implemented yet, arrives in {card}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
