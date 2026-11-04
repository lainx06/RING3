"""Host replay of a run's event tape (INV-10, D-28; T-05a adds the QEMU re-boot mode).

The run's ``event`` records are the complete inbound tape the kernel saw (one per EVENT frame); its ``audit``
records are the decisions the kernel made. ``tape_lines`` renders the tape in the ``TAPE k=... a3=...``
format that ``build/host/policy_test --replay`` (the same policy source compiled for the host) consumes, and
``compare`` matches the host's AUDIT lines against the recorded audits in order.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ring3bridge import abi
from ring3bridge.config import REPO

HOST_POLICY_TEST = REPO / "build" / "host" / "policy_test"
HOST_POLICY_TEST_PERMISSIVE = REPO / "build" / "host" / "policy_test_permissive"
COMPARE_FIELDS = ("seq", "tick", "sys", "target", "decision", "reason", "tainted")


def read_records(run_dir: Path) -> list[dict]:
    out = []
    for line in (Path(run_dir) / "events.jsonl").read_text("ascii").splitlines():
        out.append(json.loads(line))
    return out


def tape_lines(records: list[dict]) -> list[str]:
    lines = []
    for r in records:
        if r.get("type") != "event" or "a" not in r:
            continue
        a = r["a"]
        lines.append(
            f"TAPE k={r['kind']} s={r['sub']} f={r['flags']} t={r['tick']} nr={r['nr']} "
            f"a0={a[0]} a1={a[1]} a2={a[2]} a3={a[3]}"
        )
    return lines


def recorded_audits(records: list[dict]) -> list[dict[str, str]]:
    """The run's audit records in the kernel's text form (field -> string), for comparison."""
    out = []
    for r in records:
        if r.get("type") != "audit":
            continue
        target = r["target"] - (1 << 32) if r["target"] >= 1 << 31 else r["target"]  # printed as %d
        subject = r.get("subject")
        if subject is None:  # records written before the subject field existed
            if r["kind"] == abi.EV_SYSCALL:
                subject = (
                    abi.POLICY_SYSCALL_NAMES[r["nr"]] if r["nr"] < len(abi.POLICY_SYSCALL_NAMES) else "?"
                )
            else:
                subject = (
                    abi.POLICY_EVENT_NAMES[r["kind"]] if r["kind"] < len(abi.POLICY_EVENT_NAMES) else "?"
                )
        out.append(
            {
                "seq": str(r["audit_seq"]),
                "tick": str(r["tick"]),
                "sys": subject,
                "target": str(target),
                "decision": r["code_name"],
                "reason": r["reason_name"],
                "result": str(r["result"]),
                "tainted": str(r["tainted"]),
            }
        )
    return out


def parse_audit_lines(text: str) -> list[dict[str, str]]:
    out = []
    for line in text.splitlines():
        if line.startswith("AUDIT "):
            out.append(dict(kv.split("=", 1) for kv in line[6:].split(" ") if "=" in kv))
    return out


def run_host_policy(exe: Path, tape: list[str], with_ev: bool = False) -> str:
    """The host policy build over TAPE lines (fixed argv: ``--replay``, or ``--replay-ev`` for T-17's
    counterfactual, which appends the tape event index to each AUDIT line); returns its stdout."""
    p = subprocess.run(
        [str(exe), "--replay-ev" if with_ev else "--replay"], input="\n".join(tape) + "\n", cwd=REPO,
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    if p.returncode != 0:
        raise RuntimeError(f"{Path(exe).name} exit {p.returncode}")
    return p.stdout


@dataclass
class ReplayResult:
    ok: bool
    message: str
    events: int
    audits: int
    mismatch_at: int | None = None


def compare(kernel: list[dict[str, str]], host: list[dict[str, str]]) -> tuple[int | None, str]:
    if len(kernel) != len(host):
        n = min(len(kernel), len(host))
        for i in range(n):
            for f in COMPARE_FIELDS:
                if kernel[i][f] != host[i][f]:
                    return i, f"field {f}: kernel {kernel[i]} host {host[i]}"
        return n, f"kernel {len(kernel)} audits, host {len(host)}"
    for i, (k, h) in enumerate(zip(kernel, host, strict=True)):
        for f in COMPARE_FIELDS:
            if k[f] != h[f]:
                return i, f"field {f}: kernel {k} host {h}"
        if k["decision"] not in ("ALLOW", "CONFIRM_YES") and k["result"] != h["result"]:
            return i, f"result: kernel {k} host {h}"
    return None, ""


def replay_run(run_dir: Path, policy_test: Path | None = None) -> ReplayResult:
    records = read_records(run_dir)
    permissive = any(r.get("type") == "audit" and r.get("permissive") for r in records)
    exe = policy_test or (HOST_POLICY_TEST_PERMISSIVE if permissive else HOST_POLICY_TEST)
    if not Path(exe).exists():
        return ReplayResult(False, f"REPLAY ERROR: {exe} missing (make host-policy)", 0, 0)
    tape = tape_lines(records)
    kernel = recorded_audits(records)
    p = subprocess.run(
        [str(exe), "--replay"], input="\n".join(tape) + "\n", cwd=REPO, capture_output=True, text=True,
        timeout=120, check=False,
    )  # fmt: skip
    if p.returncode != 0:
        return ReplayResult(False, f"REPLAY ERROR: policy_test exit {p.returncode}", len(tape), len(kernel))
    host = parse_audit_lines(p.stdout)
    at, why = compare(kernel, host)
    if at is None:
        return ReplayResult(True, f"REPLAY MATCH: {len(kernel)} decisions from {len(tape)} tape events",
                            len(tape), len(kernel))  # fmt: skip
    note = ""
    if kernel and int(kernel[0]["seq"]) > 1:
        lost = int(kernel[0]["seq"]) - 1
        note = (
            f"; the log's first audit is seq {kernel[0]['seq']}: {lost} decision(s) were made before the "
            "bridge connected, so the tape is incomplete (start `ring3 run` before the kernel boots)"
        )
    return ReplayResult(False, f"MISMATCH at #{at}: {why}{note}", len(tape), len(kernel), at)


def event_kinds(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        if r.get("type") == "event" and "kind" in r and "a" in r:
            k = r["kind"]
            name = abi.POLICY_EVENT_NAMES[k] if k < len(abi.POLICY_EVENT_NAMES) else str(k)
            out[name] = out.get(name, 0) + 1
    return out
