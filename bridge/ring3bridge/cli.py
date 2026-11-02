"""``uv run ring3 <command>``.

Only ``run`` (without ``--dry-run``) makes network calls, and only to the model endpoint through the
transport in ``models/``; it needs ``--budget-usd``. Nothing here reads a key: ``HttpsTransport`` does, from
the environment, when ``run`` constructs it.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

from ring3bridge import abi, context_pack, eventlog, replay, selftest
from ring3bridge.config import REPO, load_model_config, load_project_cap
from ring3bridge.ledger import DEFAULT_LEDGER, Ledger, cost_uusd, fmt_usd, request_tokens, usd_to_uusd

RUNS = REPO / "runs"
DEFAULT_PORT = 4555


def _run_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    return RUNS / arg


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


def cmd_replay(a: argparse.Namespace) -> int:
    r = replay.replay_run(_run_dir(a.run))
    print(r.message)
    kinds = replay.event_kinds(replay.read_records(_run_dir(a.run)))
    print("tape events by kind: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return 0 if r.ok else 1


def cmd_fuzz_link(a: argparse.Namespace) -> int:
    from ring3bridge.fuzz import run_fuzz
    from ring3bridge.link import Link

    link = Link(a.host, a.port)
    link.connect(a.connect_timeout)
    try:
        res = run_fuzz(link, a.seed, a.frames, settle_s=a.settle)
    finally:
        link.close()
    print(res.report())
    return 0 if res.ok() else 1


def _plan_reservation(cfg, pack, request_text: str) -> tuple[int, int]:
    from ring3bridge.models.muse_spark import build_request
    from ring3bridge.run import build_input

    body = build_request(
        cfg.id, pack.text, build_input(request_text, [], None), cfg.max_output_tokens, cfg.reasoning_effort
    )
    tin = request_tokens(body)
    assert cfg.price is not None
    uusd = cost_uusd(tin, cfg.price.input_uusd_per_mtok) + cost_uusd(
        cfg.max_output_tokens, cfg.price.output_uusd_per_mtok
    )
    return tin, uusd


def cmd_run(a: argparse.Namespace) -> int:
    from ring3bridge.run import Runner, start_run

    cfg = load_model_config()
    pack = context_pack.load(a.pack)
    budget = usd_to_uusd(a.budget_usd)
    kernel = Path(a.kernel)
    if not kernel.is_absolute():
        kernel = REPO / kernel
    if cfg.price is None:
        print(
            "run: no price in config/models.toml; the ledger would refuse every reservation", file=sys.stderr
        )
        return 2
    if a.dry_run:
        sample = a.dry_run_request or "list the objects, read cell_spec, and tell me the fan limits"
        tin, uusd = _plan_reservation(cfg, pack, sample)
        print("ring3 run --dry-run (no network, no key read)")
        price = cfg.price
        p_in, p_out = price.input_uusd_per_mtok / 1e6, price.output_uusd_per_mtok / 1e6
        per_req = abi.ACTIONS_PER_REQUEST + 1 + abi.ACTIONS_PER_REQUEST
        lines = [
            f"  model {cfg.id} at {cfg.endpoint}",
            f"  context pack {pack.name}: {len(pack.text)} B, sha256 {pack.sha256[:16]}...",
            f"  price: ${p_in:.2f} / 1M in, ${p_out:.2f} / 1M out ({price.source}, checked {price.checked})",
            f"  first call for request {sample!r}: request body {tin} B -> {tin} input tokens reserved",
            f"    + {cfg.max_output_tokens} max output tokens = {fmt_usd(uusd)} per call",
            "    (estimate, not billing)",
            f"  worst case per request: {abi.ACTIONS_PER_REQUEST} actions + done + one retry each",
            f"    = {per_req} calls; later calls grow with the observations (<= 512 B each)",
            f"  --budget-usd {a.budget_usd} = {fmt_usd(budget)} per run",
            f"  project cap {fmt_usd(load_project_cap())}",
        ]
        print("\n".join(lines))
        return 0
    from ring3bridge.link import Link
    from ring3bridge.models.muse_spark import MuseSparkClient
    from ring3bridge.models.transport import HttpsTransport, TransportError

    run_id = a.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    try:
        transport = HttpsTransport()
    except TransportError as e:
        print(f"run: {e} (the model API key comes from the environment, never from a file)", file=sys.stderr)
        return 2
    log = eventlog.EventLog(run_dir)
    ledger = Ledger(load_project_cap(), DEFAULT_LEDGER)
    client = MuseSparkClient(cfg, transport, ledger, log, run_id, budget)
    seed = secrets.token_bytes(16)
    start_run(log, run_id, kernel, pack, cfg.id, seed, budget)
    link = Link(a.host, a.port)
    print(f"run {run_id}: connecting to COM2 at {a.host}:{a.port} (budget {fmt_usd(budget)}, estimate)")
    link.connect(a.connect_timeout)
    runner = Runner(link, client, log, pack, seed, timeline=_timeline)
    try:
        runner.serve(requests=a.requests, idle_s=a.idle)
    except KeyboardInterrupt:
        print("interrupted by the operator")
        if runner.request is not None:
            try:
                link.send(abi.LINK_T_STOP, bytes([abi.LINK_STOP_OPERATOR]))
            except Exception:  # noqa: BLE001
                pass
    finally:
        runner.finish()
        link.close()
    print(f"run {run_id}: {runner.requests_done} request(s); log {run_dir.relative_to(REPO)}/events.jsonl")
    print(ledger.report())
    v = eventlog.verify(run_dir)
    print(v.message)
    return 0 if v.ok else 1


def _timeline(line: str) -> None:
    print(time.strftime("%H:%M:%S"), line, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
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
    r = sub.add_parser("replay", help="host replay: feed a run's event tape through the host policy build")
    r.add_argument("run", help="run directory, or a run id under runs/")
    r.set_defaults(fn=cmd_replay)
    z = sub.add_parser("fuzz-link", help="send seeded malformed frames to a running kernel's COM2 (INV-02)")
    z.add_argument("--host", default="127.0.0.1")
    z.add_argument("--port", type=int, default=DEFAULT_PORT)
    z.add_argument("--seed", type=int, default=1)
    z.add_argument("--frames", type=int, default=10000)
    z.add_argument("--settle", type=float, default=10.0, help="seconds to wait for the final counts")
    z.add_argument("--connect-timeout", type=float, default=10.0)
    z.set_defaults(fn=cmd_fuzz_link)
    u = sub.add_parser("run", help="serve requests from a running kernel with Muse Spark (paid)")
    u.add_argument("--budget-usd", required=True, help="run cap in USD, e.g. 0.50 (required)")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=DEFAULT_PORT)
    u.add_argument("--kernel", default="build/kernel.elf", help="image whose SHA-256 goes in run_start")
    u.add_argument("--pack", default=context_pack.CURRENT)
    u.add_argument("--run-id")
    u.add_argument("--requests", type=int, help="stop after this many requests")
    u.add_argument("--idle", type=float, help="stop after this many seconds without a kernel frame")
    u.add_argument("--connect-timeout", type=float, default=30.0)
    u.add_argument("--dry-run", action="store_true", help="print the planned reservation; no network, no key")
    u.add_argument("--dry-run-request", help="request text to size the dry-run reservation on")
    u.set_defaults(fn=cmd_run)
    a = ap.parse_args(args)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
