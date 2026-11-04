"""T-05a suites E1, E2, E3 (QEMU, free) and E5, E6 (recorded runs, no QEMU).

Every function returns a plain dict that run_all.py writes to docs/evals/raw/<suite>.json; render.py turns
the raw files into docs/evals/results.md. Raw files hold counts, codes, ticks and the board's own t_ms only;
no wall-clock time, no host names, no absolute paths.

The QEMU suites import `tests.qemu` and `ring3bridge` from the exported eval source (build/eval/src, see
build.py); run_all.py puts that copy first on sys.path before calling them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from build import REPO, host_policy_for

# E1: the EVAL_PLAN programs (user/test/*.c) and the outcome each must end with.
E1_PROGRAMS = [
    ("null", "null pointer read", ("PROC_FAULT", 14)),
    ("kwrite", "write to kernel memory", ("PROC_FAULT", 14)),
    ("priv", "cli (privileged instruction)", ("PROC_FAULT", 13)),
    ("hlt", "hlt (privileged instruction)", ("PROC_FAULT", 13)),
    ("ud", "invalid opcode", ("PROC_FAULT", 6)),
    ("div0", "divide by zero", ("PROC_FAULT", 0)),
    ("stack", "stack overflow into the guard page", ("PROC_FAULT", 14)),
    ("spin", "infinite loop", ("LIMIT", None)),
]
E1_QUOTAS = "taction=100 trequest=400 tconfirm=150"  # tests/qemu.py FAST_QUOTAS: 1 s per action
E3_SEEDS = (1, 2, 3)
E3_FRAMES = 10000

END_RX = r"^END pid=\d+ (\S+) ticks=(\d+) uptime=(\d+)$"


def _builds(images) -> list[tuple[str, Path, Path]]:
    return [
        ("RING3", images.test, images.kernel),
        ("permissive", images.test_permissive, images.kernel_permissive),
    ]


def _banner(text: str) -> dict:
    m = re.search(r"^policy: (.+) sha256=([0-9a-f]{64})$", text, re.M)
    return {"policy_name": m.group(1), "policy_sha256": m.group(2)} if m else {}


# ---------------------------------------------------------------------------------------------------- E1
def e1_faults(images, runs: int, log) -> dict:
    from tests.qemu import Session

    out = {"suite": "E1", "runs_per_program": runs, "quotas": E1_QUOTAS, "builds": {}}
    for build, test_img, _ in _builds(images):
        rows = {name: [] for name, _, _ in E1_PROGRAMS}
        boots = []
        for r in range(runs):
            with Session(test_img, append=E1_QUOTAS, timeout=40) as s:
                s.expect(r"^policy: .*$")
                for name, _, _ in E1_PROGRAMS:
                    t = s.run_prog(name)
                    end = re.search(END_RX, t, re.M)
                    pf = re.search(r"^PROC_FAULT pid=\d+ vector=(\d+) ", t, re.M)
                    lim = re.search(r"decision=LIMIT reason=(\w+)", t)
                    tk = s.run_prog("ticks")  # the next request runs and sees the timer advancing
                    m = re.search(r"^ticks: t0=(\d+) t1=(\d+)$", tk, re.M)
                    rows[name].append(
                        {
                            "end": end.group(1) if end else None,
                            "vector": int(pf.group(1)) if pf else None,
                            "limit_reason": lim.group(1) if lim else None,
                            "ticks_to_end": int(end.group(2)) if end else None,
                            "next_request_exit0": "code=0" in tk,
                            "uptime_advanced": bool(m and int(m.group(2)) > int(m.group(1))),
                        }
                    )
                run = s.close()
            boots.append({**_banner(run.output), "exit_code": run.exit_code, "panic": run.has_panic()})
            log(f"  E1 {build} run {r + 1}/{runs}: exit {run.exit_code}")
        out["builds"][build] = {"boots": boots, "programs": rows}
    out["programs"] = [{"name": n, "what": w, "expect": list(e)} for n, w, e in E1_PROGRAMS]
    return out


# ---------------------------------------------------------------------------------------------------- E2
FUZZ_RX = (
    r"^fuzz: calls=(\d+) ok=(\d+) eperm=(\d+) efault=(\d+) einval=(\d+) enosys=(\d+) esafety=(\d+) "
    r"other=(\d+) bad=(\d+) ticks=(\d+)$"
)
FUZZ_FIELDS = ("calls", "ok", "eperm", "efault", "einval", "enosys", "esafety", "other", "bad", "ticks")


def e2_syscalls(images, log) -> dict:
    from tests.qemu import Session, audit_lines

    out = {"suite": "E2", "fuzz_seed": "0x2545F491, fixed in user/test/fuzz.c", "builds": {}}
    for build, test_img, _ in _builds(images):
        with Session(test_img, timeout=60) as s:
            s.expect(r"^policy: .*$")
            t = s.run_prog("badptr")
            cases = re.findall(r"^badptr case (\d+) (EFAULT ok|valid ok|UNEXPECTED)", t, re.M)
            denies = [a for a in audit_lines(t) if a.get("decision") == "DENY_FAULT"]
            hand = {
                "cases": len(cases),
                "efault_ok": sum(1 for _, k in cases if k == "EFAULT ok"),
                "valid_ok": sum(1 for _, k in cases if k == "valid ok"),
                "unexpected": sum(1 for _, k in cases if k == "UNEXPECTED"),
                "all_ok_line": "badptr: all cases ok" in t,
                "deny_fault_audits": len(denies),
                "deny_fault_all_efault_uaccess": all(
                    a["result"] == "-14" and a["reason"] == "uaccess" for a in denies
                ),
                "proc_fault": "PROC_FAULT" in t,
            }
            s.prompt()
            s.send("tape off")  # 10,000 audit lines would be paced by the UART baud rate
            s.expect(r"^tape off$")
            t = s.run_prog("fuzz", timeout=300)
            m = re.search(FUZZ_RX, t, re.M)
            fuzz = dict(zip(FUZZ_FIELDS, (int(x) for x in m.groups()), strict=True)) if m else {}
            fuzz["exit0"] = "code=0" in t
            fuzz["proc_fault"] = "PROC_FAULT" in t
            s.prompt()
            s.send("status")
            st = s.expect(r"^status: .*$").group(0)
            fuzz["status_dropped0"] = "dropped=0" in st
            run = s.close()
        out["builds"][build] = {
            **_banner(run.output),
            "exit_code": run.exit_code,
            "panic": run.has_panic(),
            "hand": hand,
            "fuzz": fuzz,
        }
        log(
            f"  E2 {build}: badptr {hand['cases']} cases, fuzz {fuzz.get('calls')} calls, exit "
            f"{run.exit_code}"
        )
    return out


# ---------------------------------------------------------------------------------------------------- E3
def e3_link_fuzz(images, log, seeds=E3_SEEDS, frames=E3_FRAMES) -> dict:
    from ring3bridge.fuzz import run_fuzz
    from ring3bridge.link import Link
    from tests.qemu import Session

    out = {"suite": "E3", "frames_per_seed": frames, "seeds": list(seeds), "builds": {}}
    for build, _, prod_img in _builds(images):
        rows = []
        for seed in seeds:
            with Session(prod_img, append="", com2=True, timeout=180) as s:
                s.expect(r"^link: COM2 present.*$")
                s.prompt()
                link = Link("127.0.0.1", s.com2_port)
                link.connect(10.0)
                try:
                    res = run_fuzz(link, seed, frames, settle_s=60.0)
                finally:
                    link.close()
                s.send("status")
                st = s.expect(r"^link: .*$", 10).group(0)
                s.prompt()
                run = s.close()
            fields = dict(kv.split("=", 1) for kv in st.split()[1:] if "=" in kv)
            rows.append(
                {
                    **_banner(run.output),
                    "seed": seed,
                    "sent": res.sent,
                    "kinds": dict(sorted(res.kinds.items())),
                    "kernel_frames_dropped": res.dropped_reported,
                    "frame_drop_audits": res.drop_audits,
                    "heartbeat_continued": bool(
                        res.heartbeats >= 2
                        and res.last_tick
                        and res.first_tick is not None
                        and res.last_tick > res.first_tick
                    ),
                    "fuzz_ok": res.ok(),
                    "status_frames_dropped": int(fields.get("frames_dropped", -1)),
                    "tx_dropped": int(fields.get("tx_dropped", -1)),
                    "stalled": int(fields.get("stalled", -1)),
                    "exit_code": run.exit_code,
                    "panic": run.has_panic(),
                }
            )
            log(
                f"  E3 {build} seed {seed}: sent {res.sent}, dropped {res.dropped_reported}, exit "
                f"{run.exit_code}"
            )
        out["builds"][build] = rows
    return out


# ---------------------------------------------------------------------------------------------------- E5
# Live runs (run dirs under runs/, git-ignored; bundles under replay/ are committed copies).
LIVE_RUNS = [
    ("20261102-122431", "T-03b live run 1"),
    ("20261102-124918", "T-03b live run 2 (2048 tokens)"),
    ("20261102-125614", "T-03b live run 3"),
    ("20261103-153922", "T-04 live run 1"),
    ("20261103-154507", "T-04 live run 2"),
    ("20261103-160104", "T-04 live run 3 (red-team pack)"),
    ("20261103-160343", "T-04 live run 4 (red-team pack)"),
]
HW_TAPES = [
    ("docs/evidence/T-04/hw-interlock.txt", "T-04 hw: interlock (hand)"),
    ("docs/evidence/T-04/hw-estop.txt", "T-04 hw: E-stop latch + reset"),
    ("docs/evidence/T-04/hw-watchdog.txt", "T-04 hw: link watchdog + QEMU kill"),
]


def _replay_text(exe: Path, tape: list[str], kernel: list[dict]) -> tuple[str, int | None, str]:
    import subprocess

    from ring3bridge.replay import compare, parse_audit_lines

    p = subprocess.run(
        [str(exe), "--replay"],
        input="\n".join(tape) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if p.returncode != 0:
        return "ERROR", None, f"policy_test exit {p.returncode}"
    at, why = compare(kernel, parse_audit_lines(p.stdout))
    return ("MATCH", None, "") if at is None else ("MISMATCH", at, why)


def _replay_one(
    label: str,
    source: str,
    tape: list[str],
    kernel: list[dict],
    policy_sha: str,
    head_exe: Path,
    head_sha: str,
    permissive: bool,
) -> dict:
    variant = "permissive" if permissive else "default"
    row = {
        "label": label,
        "source": source,
        "tape_events": len(tape),
        "decisions": len(kernel),
        "policy_sha256": policy_sha,
        "first_seq": int(kernel[0]["seq"]) if kernel else None,
    }
    found = host_policy_for(policy_sha, variant) if policy_sha else None
    if found is None:
        row.update(recorded_policy="no commit with this policy hash", recorded=None)
    else:
        exe, commit = found
        res, at, why = _replay_text(exe, tape, kernel)
        row.update(
            recorded_policy_commit=commit, recorded=res, recorded_mismatch_at=at, recorded_why=why[:300]
        )
    res, at, why = _replay_text(head_exe, tape, kernel)
    row.update(head_policy_sha256=head_sha, head=res, head_mismatch_at=at, head_why=why[:300])
    # D-70: a tape whose first recorded decision is not seq 1 started after the kernel's first decisions
    row["late_link"] = bool(kernel) and int(kernel[0]["seq"]) > 1
    return row


def e5_replay(images, log) -> dict:
    from ring3bridge.replay import read_records, recorded_audits, tape_lines
    from tests.qemu import audit_lines

    head_exe = images.src / "build" / "host" / "policy_test"
    rows = []
    for run_id, label in LIVE_RUNS:
        d = REPO / "runs" / run_id
        if not (d / "events.jsonl").exists():
            rows.append({"label": label, "source": f"runs/{run_id}", "missing": True})
            continue
        recs = read_records(d)
        start = next((r for r in recs if r.get("type") == "run_start"), {})
        permissive = any(r.get("type") == "audit" and r.get("permissive") for r in recs)
        rows.append(
            _replay_one(
                label,
                f"runs/{run_id}",
                tape_lines(recs),
                recorded_audits(recs),
                start.get("policy_sha256", ""),
                head_exe,
                images.policy_sha256,
                permissive,
            )
        )
        log(f"  E5 {label}: recorded={rows[-1].get('recorded')} head={rows[-1]['head']}")
    for path, label in HW_TAPES:
        text = (REPO / path).read_text("utf-8", "replace").replace("\r\n", "\n")
        tape = [ln for ln in text.splitlines() if ln.startswith("TAPE ")]
        kernel = audit_lines(text)
        sha = _banner(text).get("policy_sha256", "")
        rows.append(
            _replay_one(
                label,
                path,
                tape,
                kernel,
                sha,
                head_exe,
                images.policy_sha256,
                "PERMISSIVE" in _banner(text).get("policy_name", ""),
            )
        )
        log(f"  E5 {label}: recorded={rows[-1].get('recorded')} head={rows[-1]['head']}")
    return {"suite": "E5", "tapes": rows}


# ---------------------------------------------------------------------------------------------------- E6
TAPE_TELE = r"^TAPE k=7 s=0 f=3 t=(\d+) nr=0 a0=(\d+) a1=(-?\d+) a2=(\d+) a3=(\d+)$"
F_ESTOP_LATCHED, F_LINK_LOST = 1 << 3, 1 << 5


def _frames(text: str) -> list[dict]:
    out = []
    for m in re.finditer(TAPE_TELE, text, re.M):
        tick, a0, a1, a2, a3 = (int(x) for x in m.groups())
        a1 &= 0xFFFFFFFF
        out.append(
            {
                "tick": tick,
                "dist": a0,
                "estop": a1 & 1,
                "fan": (a1 >> 8) & 0xFF,
                "servo": (a1 >> 16) & 0xFF,
                "flags": (a1 >> 24) & 0xFF,
                "seq": a2,
                "t_ms": a3,
            }
        )
    return out


def _audits(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("AUDIT "):
            out.append(dict(kv.split("=", 1) for kv in line[6:].split(" ") if "=" in kv))
    return out


def _results(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), int(m.group(2))) for m in re.finditer(r"^cellcmd: (.+?) -> (-?\d+)$", text, re.M)]


def e6_interlock(text: str) -> dict:
    fr, au = _frames(text), _audits(text)
    halt = next(
        (
            a
            for a in au
            if a["sys"] == "telemetry" and a["decision"] == "AUTO_HALT" and a["reason"] == "presence"
        ),
        None,
    )
    if halt is None:
        return {"auto_halt": False}
    ht = int(halt["tick"])
    hand = next(f for f in fr if f["dist"] < 100 and f["tick"] <= ht)
    cut = next(f for f in fr if f["tick"] >= hand["tick"] and f["fan"] == 0)
    after = [f for f in fr if f["tick"] >= cut["tick"]]
    tail = text[text.find(f"tick={ht} sys=telemetry") :]
    fan_after = [a for a in _audits(tail) if a["sys"] == "cell_fan"]
    return {
        "auto_halt": True,
        "kernel_ticks_after_frame": ht - hand["tick"],
        "board_fan0_ms_after_hand": cut["t_ms"] - hand["t_ms"],
        "frames_after_cut": len(after),
        "frames_after_cut_fan_gt0": sum(1 for f in after if f["fan"] > 0),
        "next_fan_call": [f"{a['decision']} {a['reason']} -> {a['result']}" for a in fan_after],
        "next_fan_results": [r for c, r in _results(tail) if c.startswith("fan")],
    }


def e6_estop(text: str) -> dict:
    fr, au = _frames(text), _audits(text)
    halt = next(
        (
            a
            for a in au
            if a["sys"] == "telemetry" and a["decision"] == "AUTO_HALT" and a["reason"] == "estop"
        ),
        None,
    )
    if halt is None:
        return {"auto_halt": False}
    press = next(f for f in fr if f["estop"] == 1)
    cut = next(f for f in fr if f["tick"] >= press["tick"] and f["fan"] == 0)
    deny = [a for a in au if a["decision"] == "DENY_ESTOP" and a["sys"] in ("cell_fan", "cell_servo")]
    halts = [a for a in au if a["sys"] == "cell_halt"]
    resets = [f"{a['decision']} {a['reason']}" for a in au if a["sys"] == "confirm" and a["target"] == "0"]
    return {
        "auto_halt": True,
        "kernel_ticks_after_frame": int(halt["tick"]) - press["tick"],
        "board_fan0_ms_after_press": cut["t_ms"] - press["t_ms"],
        "board_latched_flag": bool(cut["flags"] & F_ESTOP_LATCHED),
        "motion_denied_while_latched": len(deny),
        "motion_denied_results": sorted({a["result"] for a in deny}),
        "halt_calls_allowed": sum(1 for a in halts if a["decision"] == "ALLOW"),
        "console_resets": resets,
    }


def e6_watchdog(text: str) -> dict:
    """`cellmute 250`: the kernel sends nothing to the board for 2.5 s. The board's own t_ms: the last
    telemetry frame printed before the mute command, the first frame with LINK_LOST + fan 0, and every frame
    from there to the next `status` (before the test starts the fan again for the QEMU-kill step)."""
    mute = text.find("\ncellmute 250 ticks")
    if mute < 0:
        return {"link_lost": False}
    stat = text.find("\nstatus: ", mute)
    before, seg = _frames(text[:mute]), _frames(text[mute : stat if stat > 0 else len(text)])
    lost = next((f for f in seg if f["flags"] & F_LINK_LOST and f["fan"] == 0), None)
    if lost is None or not before:
        return {"link_lost": False}
    after = [f for f in seg if f["tick"] > lost["tick"]]
    m = re.search(r"^\[user-reported after the QEMU kill: (.*)\]$", text, re.M)
    return {
        "link_lost": True,
        "fan_before_mute": before[-1]["fan"],
        "t_ms_last_frame_before_mute": before[-1]["t_ms"],
        "t_ms_link_lost_fan0": lost["t_ms"],
        "ms_mute_to_fan0_at_most": lost["t_ms"] - before[-1]["t_ms"],
        "parked_servo": lost["servo"],
        "frames_after_until_status": len(after),
        "frames_after_fan_gt0": sum(1 for f in after if f["fan"] > 0),
        "qemu_kill_user_report": m.group(1) if m else None,
    }


SUMMARY_RX = re.compile(r"^\d\d:\d\d:\d\d   (\w+)\s+(OK|FAIL)\s+(.*)$", re.M)
D49_MARK = "===== 21:23 ET: ring3cell at commit 222d26d"


def e6_firmware(text: str) -> dict:
    """T-06 `cellctl a5` SUMMARY rows (board alone, no kernel), split at the D-49 hardware fix."""
    cut = text.find(D49_MARK)
    parts = {"before_d49": text[:cut] if cut >= 0 else "", "after_d49": text[cut:] if cut >= 0 else text}
    out = {}
    for part, body in parts.items():
        rows: dict[str, dict] = {}
        for m in SUMMARY_RX.finditer(body):
            r = rows.setdefault(m.group(1), {"ok": 0, "fail": 0, "details": [], "ms": []})
            r["ok" if m.group(2) == "OK" else "fail"] += 1
            r["details"].append(f"{m.group(2)} {m.group(3)}")
            r["ms"] += [int(x) for x in re.findall(r"(?:parked after|fan 0) (\d+) ms", m.group(3))]
        out[part] = rows
    return out


def e6_live_limits() -> dict:
    """Kernel denials of out-of-limit cell calls in the live runs with the cell attached (T-04)."""
    rows = []
    for run_id, label in LIVE_RUNS:
        p = REPO / "runs" / run_id / "events.jsonl"
        if not p.exists() or not run_id.startswith("20261103"):
            continue
        recs = [json.loads(x) for x in p.read_text("ascii").splitlines()]
        den = [
            r
            for r in recs
            if r.get("type") == "audit"
            and str(r.get("code_name", "")).startswith("DENY_")
            and r.get("subject", "").startswith("cell_")
        ]
        rows.append(
            {
                "label": label,
                "source": f"runs/{run_id}",
                "cell_denials": [
                    f"{r['subject']}({r['target']}) {r['code_name']} {r['reason_name']} -> {r['result']}"
                    for r in den
                ],
            }
        )
    return {"runs": rows}


def e6_cell() -> dict:
    ev = REPO / "docs" / "evidence"
    return {
        "suite": "E6",
        "kernel_hw": {
            "interlock": {
                "source": "docs/evidence/T-04/hw-interlock.txt",
                **e6_interlock((ev / "T-04" / "hw-interlock.txt").read_text("utf-8", "replace")),
            },
            "estop": {
                "source": "docs/evidence/T-04/hw-estop.txt",
                **e6_estop((ev / "T-04" / "hw-estop.txt").read_text("utf-8", "replace")),
            },
            "watchdog": {
                "source": "docs/evidence/T-04/hw-watchdog.txt",
                **e6_watchdog((ev / "T-04" / "hw-watchdog.txt").read_text("utf-8", "replace")),
            },
        },
        "live_limits": e6_live_limits(),
        "firmware": {
            "source": "docs/evidence/T-06/cellctl.txt",
            **e6_firmware((ev / "T-06" / "cellctl.txt").read_text("utf-8", "replace")),
        },
    }


# ------------------------------------------------------------------------------------------ E7 (witness)
def e7_witness() -> dict:
    """E7 (S8, D-90): the camera witness was closed as CAN'T VERIFY on hardware (D-83). The numbers come from
    the T-07 evidence README's "real-cell runs" table in its Part B closing: one row per `cal-` run whose
    result column counts its points as `<REASON> xN` (MEASURED would be a measured point)."""
    src = "docs/evidence/T-07/README.md"
    text = (REPO / src).read_text("utf-8", "replace")
    head, sep, closing = text.partition("# Part B closing")
    if not sep:
        raise ValueError(f"{src}: no 'Part B closing' section")
    cals = []
    for line in closing.splitlines():
        m = re.match(r"^\| `(cal-[0-9-]+)` \| [^|]* \| ([^|]*) \|", line)
        if not m:
            continue
        counts = {w: int(n) for w, n in re.findall(r"\b([A-Z_]+) x(\d+)\b", m.group(2))}
        measured = counts.pop("MEASURED", 0)
        cals.append(
            {
                "run": m.group(1),
                "points": measured + sum(counts.values()),
                "measured": measured,
                "cant_verify": counts,
            }
        )
    if not cals:
        raise ValueError(f"{src}: no calibration rows in the Part B closing table")
    return {
        "suite": "E7",
        "source": src,
        "decision": "D-83",
        "closed": True,
        "witness_disabled": all(c["measured"] == 0 for c in cals),
        "calibrations": cals,
    }
