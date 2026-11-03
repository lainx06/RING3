"""E4 agent tasks (T-05a step 8; paid, the user runs it, D-57).

    uv run python tools/eval/e4_agent.py --set v2 --budget-usd 0.60              # print the plan, no network
    uv run python tools/eval/e4_agent.py --set v2 --budget-usd 0.60 --go         # the user: paid run
    uv run python tools/eval/e4_agent.py --score <run-id> --set v2               # re-score a recorded run

With --go: refuses if kernel/, user/, bridge/, abi/, config/ or docs/context-packs/ have uncommitted changes
(the eval measures committed code); builds the production image; starts `ring3 run` FIRST (D-70; it retries
COM2 for 30 s), then boots QEMU with the cell on COM3 and types the fixed requests from e4_tasks.py one at a
time. Every CONFIRM prompt is shown to the operator, who types the answer (the script never answers for the
human). Physical steps (hand, E-stop, ECHO wire) are asked for before the request and wait for Enter.
Afterwards: `ring3 verify` + `ring3 replay` of the run, the checkers, docs/evals/raw/e4-<set>.json, and
results.md is re-rendered. This script never reads a key; `ring3 run` does, in its own process.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO), str(REPO / "bridge")]

import e4_tasks  # noqa: E402

RAW = REPO / "docs" / "evals" / "raw"
LOGS = REPO / "docs" / "evals" / "e4"
CLEAN_PATHS = ["kernel", "user", "bridge", "abi", "config", "docs/context-packs"]
REQUEST_TIMEOUT_S = 400.0
CONFIRM_ANSWER_S = 25.0  # the kernel's prompt timeout is 30 s (kernel/include/exec.h)
END_RX = r"^END pid=\d+ (\S+) ticks=(\d+) uptime=(\d+)$"
END_LINE = r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+\n"
CONFIRM_RX = r"CONFIRM: (.*?)Allow\? \[y/N\] "


def say(msg: str) -> None:
    print(f"\n>>> {msg}", flush=True)


def plan(a: argparse.Namespace) -> int:
    from ring3bridge import abi, context_pack
    from ring3bridge.cli import _plan_reservation
    from ring3bridge.config import load_model_config
    from ring3bridge.ledger import fmt_usd

    pack_name, tasks = e4_tasks.SETS[a.set]
    cfg = load_model_config()
    pack = context_pack.load(pack_name)
    per_call = max(_plan_reservation(cfg, pack, t.text)[1] for t in tasks)
    worst = per_call * (abi.ACTIONS_PER_REQUEST + 1 + abi.ACTIONS_PER_REQUEST)
    print(
        f"E4 set {a.set}: pack {pack_name} (sha256 {pack.sha256[:16]}...), {len(tasks)} requests, n = 1 each"
    )
    for t in tasks:
        extra = f"  [before: {t.setup}]" if t.setup else ""
        print(f"  {t.id} {t.kind:6} {t.text!r}{extra}")
    print(
        f"reservation per model call (first call, largest request): {fmt_usd(per_call)} "
        f"(input + {cfg.max_output_tokens} max output tokens; estimate, not billing)"
    )
    print(
        f"worst case one request ({abi.ACTIONS_PER_REQUEST} actions + done + one retry each): "
        f"{fmt_usd(worst)}; "
        f"the run stops at --budget-usd {a.budget_usd}"
    )
    print("command (the user runs it; board on /dev/ttyACM0, user at the console):")
    w = f" --witness-dir {a.witness_dir}" if a.witness_dir else ""
    c = f" --calibration {a.calibration}" if a.calibration else ""
    print(f"  uv run python tools/eval/e4_agent.py --set {a.set} --budget-usd {a.budget_usd}{w}{c} --go")
    return 0


def frames_from_events(run_dir: Path) -> list[dict]:
    """Board telemetry on the run's tape (EV_TELEMETRY, kind 7): tick, fan, servo, dist, flags."""
    out = []
    p = run_dir / "events.jsonl"
    if not p.exists():
        return out
    for line in p.read_text("ascii").splitlines():
        r = json.loads(line)
        if r.get("type") == "event" and r.get("kind") == 7 and "a" in r:
            a1 = r["a"][1] & 0xFFFFFFFF
            out.append(
                {
                    "tick": r["tick"],
                    "dist": r["a"][0],
                    "fan": (a1 >> 8) & 0xFF,
                    "servo": (a1 >> 16) & 0xFF,
                    "flags": (a1 >> 24) & 0xFF,
                }
            )
    return out


def per_request(run_dir: Path) -> list[list[dict]]:
    """The run's records split at each `user_request` (index order = the order the requests were typed)."""
    out: list[list[dict]] = []
    p = run_dir / "events.jsonl"
    if p.exists():
        for line in p.read_text("ascii").splitlines():
            r = json.loads(line)
            if r.get("type") == "user_request":
                out.append([])
            if out:
                out[-1].append(r)
    return out


def score(set_name: str, run_id: str, com1: str, meta: dict) -> dict:
    """Split the COM1 transcript per request and run the checkers."""
    pack_name, tasks = e4_tasks.SETS[set_name]
    run_dir = REPO / "runs" / run_id
    tele = frames_from_events(run_dir)
    reqs = per_request(run_dir)
    blocks = re.split(r"(?m)^(?=E4-TASK )", com1)
    recs = []
    for i, t in enumerate(tasks):
        mine = reqs[i] if i < len(reqs) else []
        blk = next((b for b in blocks if b.startswith(f"E4-TASK {t.id} ")), "")
        pre, _, rest = blk.partition("\nREQUEST ")
        m_run = re.search(r"^RUN pid=\d+ name=agentsh .* uptime=(\d+)$", rest, re.M)
        m_end = re.search(END_RX, rest, re.M)
        lo = int(m_run.group(1)) if m_run else None
        hi = int(m_end.group(3)) if m_end else None
        window = [f for f in tele if lo is not None and hi is not None and lo <= f["tick"] <= hi + 20]
        prompts = [
            [m.group(1).strip(), m.group(2).strip()]
            for m in re.finditer(r"CONFIRM: (.*?)Allow\? \[y/N\] \n?(.*)$", rest, re.M)
        ]
        outcome, why = e4_tasks.parse_outcome(rest)
        rec = {
            "id": t.id,
            "text": t.text,
            "outcome": outcome,
            "outcome_text": why,
            "ops": e4_tasks.parse_ops(rest),
            "prompts": prompts,
            "pre": e4_tasks.parse_status(pre),
            "post": e4_tasks.parse_status(rest),
            "board": {**e4_tasks.board_window(window), "no_echo": any(f["dist"] == 0xFFFF for f in window)},
            "witness_verdicts": [str(r.get("verdict", "")) for r in mine if r.get("type") == "witness"],
            "witness_ops": sum(
                1 for r in mine if r.get("type") == "observation" and r.get("op") == "witness"
            ),
            "ran": bool(m_end),
        }
        rec["check"] = e4_tasks.check(t.id, rec)
        recs.append(rec)
    return {"suite": "E4", "set": set_name, "pack": pack_name, "run_id": run_id, **meta, "tasks": recs}


def _sh(cmd: list[str]) -> str:
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)
    return (p.stdout + p.stderr).strip()


def run_meta(run_id: str, budget_uusd: int | None) -> dict:
    v = _sh(["uv", "run", "ring3", "verify", run_id]).splitlines()
    r = _sh(["uv", "run", "ring3", "replay", run_id]).splitlines()
    spend = _sh(["uv", "run", "ring3", "spend"])
    m = re.search(rf"^\s+{re.escape(run_id)}: \$(\d+)\.(\d{{6}})$", spend, re.M)
    return {
        "verify": v[0] if v else "",
        "replay": r[0] if r else "",
        "settled_uusd": int(m.group(1)) * 1_000_000 + int(m.group(2)) if m else None,
        "budget_uusd": budget_uusd,
    }


def answer(s, prompt: str) -> None:
    """The operator answers a kernel prompt. The kernel counts no answer within 30 s as no
    (CONFIRM_TIMEOUT_TICKS); a later keystroke would land at the console as a new request line, so a late
    answer is not typed."""
    t0 = time.monotonic()
    ans = input(f"\n>>> {prompt} ")
    if time.monotonic() - t0 > CONFIRM_ANSWER_S:
        say("too late: the kernel has timed the prompt out (counted as no); nothing typed")
        return
    s.send(ans.strip()[:1] or "n")


def go(a: argparse.Namespace) -> int:
    dirty = _sh(["git", "status", "--porcelain", "--", *CLEAN_PATHS])
    if dirty:
        print("refusing: uncommitted changes in the measured code (commit or wait for the other builders):")
        print(dirty)
        return 2
    subprocess.run(["make", "-s"], cwd=REPO, check=True)
    import tests.qemu as q

    pack_name, tasks = e4_tasks.SETS[a.set]
    port = q.free_port()
    run_id = f"e4-{a.set}-{time.strftime('%Y%m%d-%H%M%S')}"
    LOGS.mkdir(parents=True, exist_ok=True)
    bridge_log = LOGS / f"{run_id}-bridge.txt"
    tasks_log = LOGS / f"{run_id}-tasks.txt"
    cmd = [
        "uv",
        "run",
        "ring3",
        "run",
        "--pack",
        pack_name,
        "--budget-usd",
        a.budget_usd,
        "--requests",
        str(len(tasks)),
        "--port",
        str(port),
        "--run-id",
        run_id,
        "--kernel",
        "build/kernel.elf",
    ]
    if a.witness_dir:
        cmd += ["--witness-dir", a.witness_dir]
    if a.calibration:
        cmd += ["--calibration", a.calibration]
    say(f"starting the bridge first (D-70): {' '.join(cmd)}")
    with bridge_log.open("w") as bl:
        bridge = subprocess.Popen(cmd, cwd=REPO, stdout=bl, stderr=subprocess.STDOUT)
        time.sleep(2.0)
        q.free_port = lambda: port  # Session takes COM2 on the port the bridge is already dialling
        com1 = ""
        try:
            with q.Session(q.KERNEL, append="", com2=True, com3=a.cell, timeout=60) as s:
                s.expect(r"^cell: COM3 present.*$")
                s.prompt()
                for t in tasks:
                    com1_mark = len(s.output)
                    if t.setup:
                        input(f"\n>>> {t.id}: {t.setup}; then press Enter ")
                    s.prompt()
                    s.send("status")
                    s.expect(r"^status: .*$")
                    s.prompt()
                    pre = s.output[com1_mark:]
                    say(f"{t.id}: typing {t.text!r}" + (f" (operator: {t.answer})" if t.answer else ""))
                    s.send(t.text)
                    start = s.pos
                    while True:
                        m = s.expect(rf"({CONFIRM_RX})|({END_LINE})", REQUEST_TIMEOUT_S)
                        if m.group(0).startswith("CONFIRM"):
                            answer(s, f"{m.group(0).strip()}  (task {t.id}; suggested: {t.answer or 'n'})")
                            continue
                        break
                    s.prompt()
                    s.send("status")
                    s.expect(r"^status: .*$")
                    s.prompt()
                    body = s.output[start:]
                    com1 += f"E4-TASK {t.id} \n{pre}\nREQUEST {body}\n"
                    tasks_log.write_text(com1, encoding="utf-8")  # kept even if a later step fails
                    if t.teardown == "estop_reset":
                        input("\n>>> release the E-stop if it is still pressed, then press Enter")
                        s.send("estop reset")
                        m = s.expect(rf"({CONFIRM_RX})|(^estop reset: .*\n)", 30)
                        if m.group(0).startswith("CONFIRM"):
                            answer(s, f"{m.group(0).strip()}  (the human reset; answer y)")
                            s.expect(r"^estop reset: .*\n", 40)
                        s.prompt()
                    elif t.teardown == "replug":
                        input("\n>>> put the ECHO wire back into D3, then press Enter")
                try:
                    bridge.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    say("the bridge did not exit after the last request; stopping it")
                    bridge.terminate()
                    bridge.wait(timeout=10)
                run = s.close()
                (LOGS / f"{run_id}-com1.txt").write_text(run.output, encoding="utf-8")
        finally:
            if bridge.poll() is None:
                bridge.terminate()
    data = score(a.set, run_id, com1, run_meta(run_id, _uusd(a.budget_usd)))
    (RAW / f"e4-{a.set}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="ascii")
    subprocess.run(["uv", "run", "python", "tools/eval/run_all.py", "--render-only"], cwd=REPO, check=False)
    print(json.dumps([{x["id"]: x["check"]} for x in data["tasks"]], indent=1))
    return 0


def _uusd(usd: str) -> int:
    whole, _, frac = usd.partition(".")
    return int(whole or 0) * 1_000_000 + int((frac + "000000")[:6])


def rescore(a: argparse.Namespace) -> int:
    com1 = (LOGS / f"{a.score}-tasks.txt").read_text("utf-8")
    old = (
        json.loads((RAW / f"e4-{a.set}.json").read_text("ascii"))
        if (RAW / f"e4-{a.set}.json").exists()
        else {}
    )
    meta = {k: old.get(k) for k in ("verify", "replay", "settled_uusd", "budget_uusd")} if old else {}
    data = score(a.set, a.score, com1, meta or run_meta(a.score, None))
    (RAW / f"e4-{a.set}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="ascii")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="e4_agent.py")
    ap.add_argument("--set", choices=sorted(e4_tasks.SETS), required=True)
    ap.add_argument("--budget-usd", help="run cap in USD (required with --go and for the plan)")
    ap.add_argument("--cell", default="/dev/ttyACM0")
    ap.add_argument("--witness-dir")
    ap.add_argument("--calibration")
    ap.add_argument("--go", action="store_true", help="the paid run (the user)")
    ap.add_argument("--score", metavar="RUN_ID", help="re-score a recorded run from docs/evals/e4/")
    a = ap.parse_args(argv)
    if a.score:
        return rescore(a)
    if not a.budget_usd:
        ap.error("--budget-usd is required")
    return go(a) if a.go else plan(a)


if __name__ == "__main__":
    sys.exit(main())
