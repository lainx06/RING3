"""Render docs/evals/results.md from docs/evals/raw/*.json (T-05a). Pure function of the raw files: the same
raw files give the same bytes. Every number printed here is read from a raw file; counts come with their
sample size; no percentages.
"""

from __future__ import annotations

import json
from pathlib import Path

E1_EXPECT = {"PROC_FAULT": "PROC_FAULT", "LIMIT": "LIMIT"}


def _load(raw: Path, name: str) -> dict | None:
    p = raw / f"{name}.json"
    return json.loads(p.read_text("ascii")) if p.exists() else None


def _yn(b: bool) -> str:
    return "yes" if b else "**no**"


def _rng(xs: list[int]) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    lo, hi = min(xs), max(xs)
    return str(lo) if lo == hi else f"{lo}-{hi}"


def _table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _build_line(d: dict) -> str:
    b = d.get("build") or {}
    if not b:
        return ""
    ph = b.get("policy_sha256", {})
    return (
        f"Measured build: commit `{b.get('commit')}` (exported with `git archive`, built in build/eval/src); "
        f"policy SHA-256 RING3 `{ph.get('RING3', '')[:16]}...`, permissive "
        f"`{ph.get('permissive', '')[:16]}...`."
    )


# ------------------------------------------------------------------------------------------------ checks
def _e1_ok(row: dict, expect: list) -> bool:
    kind, vec = expect
    if row["end"] != ("FAULT" if kind == "PROC_FAULT" else "LIMIT"):
        return False
    if kind == "PROC_FAULT" and row["vector"] != vec:
        return False
    if kind == "LIMIT" and row["limit_reason"] != "action_quota":
        return False
    return row["next_request_exit0"] and row["uptime_advanced"]


def must_pass_failures(raw: Path) -> list[str]:
    """The EVAL_PLAN must-pass rows, checked on the RING3 build only."""
    f: list[str] = []
    e1 = _load(raw, "e1")
    if e1:
        b = e1["builds"]["RING3"]
        if any(x["panic"] or x["exit_code"] != 33 for x in b["boots"]):
            f.append("E1: a RING3 boot panicked or did not exit 33")
        for p in e1["programs"]:
            bad = [r for r in b["programs"][p["name"]] if not _e1_ok(r, p["expect"])]
            if bad:
                f.append(f"E1: {p['name']} did not end as {p['expect']} in {len(bad)} run(s)")
    e2 = _load(raw, "e2")
    if e2:
        b = e2["builds"]["RING3"]
        h, z = b["hand"], b["fuzz"]
        if b["panic"] or b["exit_code"] != 33:
            f.append("E2: the RING3 boot panicked or did not exit 33")
        if h["unexpected"] or not h["all_ok_line"] or h["proc_fault"]:
            f.append("E2: a hand-written case was unexpected")
        if not z or z.get("bad") != 0 or not z.get("exit0") or z.get("proc_fault"):
            f.append("E2: the fuzzer saw a result that is not an error code, or did not finish")
    e3 = _load(raw, "e3")
    if e3:
        for r in e3["builds"]["RING3"]:
            if r["panic"] or not r["fuzz_ok"] or r["exit_code"] != 33:
                f.append(f"E3: seed {r['seed']} on RING3: panic={r['panic']} fuzz_ok={r['fuzz_ok']}")
    e5 = _load(raw, "e5")
    if e5:
        for t in e5["tapes"]:
            if t.get("missing") or t.get("late_link"):
                continue
            if t.get("recorded") != "MATCH":
                f.append(f"E5: {t['label']}: {t.get('recorded')} on the recorded policy")
    e6 = _load(raw, "e6")
    if e6:
        k = e6["kernel_hw"]
        if not k["interlock"].get("auto_halt") or k["interlock"].get("frames_after_cut_fan_gt0"):
            f.append("E6: interlock")
        if not k["estop"].get("auto_halt") or not k["estop"].get("board_latched_flag"):
            f.append("E6: E-stop")
        w = k["watchdog"]
        if (
            not w.get("link_lost")
            or w.get("frames_after_fan_gt0")
            or w.get("ms_mute_to_fan0_at_most", 9999) > 1500
        ):
            f.append("E6: link watchdog")
    return f


# ------------------------------------------------------------------------------------------------ sections
def _e1(d: dict | None) -> list[str]:
    out = ["## E1 Executor faults", ""]
    if d is None:
        return out + ["Not run yet.", ""]
    n = d["runs_per_program"]
    out += [
        _build_line(d),
        "",
        f"Each of the 8 programs ran in {n} fresh boots per build (`run <prog>` on the test image, quotas "
        f"`{d['quotas']}` = 1 s per action), each followed by `run ticks` to show the next request runs and "
        "uptime advances. Ticks to end: the kernel's `END ... ticks=` (100 Hz), min-max over the runs.",
        "",
    ]
    rows = []
    for p in d["programs"]:
        kind, vec = p["expect"]
        exp = f"PROC_FAULT vector {vec}" if kind == "PROC_FAULT" else "LIMIT (action quota)"
        row = [f"`{p['name']}`", p["what"], exp]
        for build in ("RING3", "permissive"):
            rs = d["builds"][build]["programs"][p["name"]]
            ok = sum(1 for r in rs if _e1_ok(r, p["expect"]))
            row += [f"{ok}/{len(rs)}", _rng([r["ticks_to_end"] for r in rs])]
        rows.append(row)
    out += _table(
        [
            "Program",
            "What",
            "Must end with",
            "RING3 as expected",
            "RING3 ticks to end",
            "permissive as expected",
            "permissive ticks to end",
        ],
        rows,
    )
    out.append("")
    brows = []
    for build in ("RING3", "permissive"):
        boots = d["builds"][build]["boots"]
        progs = d["builds"][build]["programs"]
        nxt = sum(1 for rs in progs.values() for r in rs if r["next_request_exit0"] and r["uptime_advanced"])
        tot = sum(len(rs) for rs in progs.values())
        brows.append(
            [
                build,
                f"`{boots[0].get('policy_name', '?')}`",
                len(boots),
                sum(1 for b in boots if b["panic"]),
                sum(1 for b in boots if b["exit_code"] == 33),
                f"{nxt}/{tot}",
            ]
        )
    out += _table(
        [
            "Build",
            "Banner policy",
            "Boots",
            "Kernel panics",
            "Boots ending KERNEL ALIVE (exit 33)",
            "Next request ran + uptime advanced",
        ],
        brows,
    )
    return out + [""]


def _e2(d: dict | None) -> list[str]:
    out = ["## E2 Syscall boundary", ""]
    if d is None:
        return out + ["Not run yet.", ""]
    out += [
        _build_line(d),
        "",
        "Hand-written cases: `user/test/badptr.c` (null, kernel, unmapped, wrap-around, page-spanning into a "
        "guard page, read-only target, above `USER_TOP`, and the valid edges). The plan's 40 hand cases are "
        "not all written; this table reports the cases that exist. Fuzzer: `user/test/fuzz.c`, one seed "
        f"({d['fuzz_seed']}), random syscall numbers and arguments, audit tape off during the run.",
        "",
    ]
    rows = []
    for build in ("RING3", "permissive"):
        b = d["builds"][build]
        h = b["hand"]
        rows.append(
            [
                build,
                h["cases"],
                h["efault_ok"],
                h["valid_ok"],
                h["unexpected"],
                h["deny_fault_audits"],
                _yn(h["deny_fault_all_efault_uaccess"]),
                _yn(not h["proc_fault"]),
                "1" if b["panic"] else "0",
            ]
        )
    out += _table(
        [
            "Build",
            "Hand cases",
            "-EFAULT as expected",
            "valid edge as expected",
            "Unexpected",
            "DENY_FAULT audits",
            "all -14 uaccess",
            "no PROC_FAULT",
            "Kernel panics",
        ],
        rows,
    )
    out.append("")
    rows = []
    for build in ("RING3", "permissive"):
        z = d["builds"][build]["fuzz"]
        rows.append(
            [
                build,
                z.get("calls"),
                z.get("ok"),
                z.get("eperm"),
                z.get("efault"),
                z.get("einval"),
                z.get("enosys"),
                z.get("esafety"),
                z.get("other"),
                z.get("bad"),
                _yn(z.get("ticks", 0) > 0),
                "1" if d["builds"][build]["panic"] else "0",
            ]
        )
    out += _table(
        [
            "Build",
            "Calls",
            ">= 0",
            "-EPERM",
            "-EFAULT",
            "-EINVAL",
            "-ENOSYS",
            "-ESAFETY",
            "other known errno",
            "not an error code",
            "uptime advanced",
            "Kernel panics",
        ],
        rows,
    )
    out += [
        "",
        "`other known errno` = -EACCES, -ECANCELED, -ENOENT or -ETIMEDOUT. `not an error code` must be 0.",
    ]
    return out + [""]


def _e3(d: dict | None) -> list[str]:
    out = ["## E3 Link fuzz (COM2)", ""]
    if d is None:
        return out + ["Not run yet.", ""]
    out += [
        _build_line(d),
        "",
        "`ring3bridge.fuzz.run_fuzz` (the code behind `ring3 fuzz-link`) against the production image of "
        "each "
        f"build, {d['frames_per_seed']} malformed frames per seed, seeds {', '.join(map(str, d['seeds']))}. "
        "Every frame is checked against the bridge's own strict decoder first, so the kernel must drop "
        "all of "
        "them. The kernel does not report a ring-buffer high-water mark, so max fill is not measured.",
        "",
    ]
    rows = []
    for build in ("RING3", "permissive"):
        for r in d["builds"][build]:
            rows.append(
                [
                    build,
                    r["seed"],
                    r["sent"],
                    r["kernel_frames_dropped"],
                    r["frame_drop_audits"],
                    r["status_frames_dropped"],
                    r["tx_dropped"],
                    r["stalled"],
                    _yn(r["heartbeat_continued"]),
                    "1" if r["panic"] else "0",
                ]
            )
    out += _table(
        [
            "Build",
            "Seed",
            "Frames sent",
            "Kernel frames_dropped (heartbeat)",
            "FRAME_DROP audits",
            "frames_dropped (status)",
            "tx_dropped",
            "stalled",
            "Heartbeat continued",
            "Kernel panics",
        ],
        rows,
    )
    kinds: dict[str, int] = {}
    for r in d["builds"]["RING3"]:
        for k, v in r["kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
    out += [
        "",
        "Malformed-frame kinds over the RING3 seeds (same generator and seeds for both builds): "
        + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        + ".",
    ]
    return out + [""]


def _e5(d: dict | None) -> list[str]:
    out = ["## E5 Replay", ""]
    if d is None:
        return out + ["Not run yet.", ""]
    out += [
        "Each tape is fed through the host build of the policy (`policy_test --replay`) and every host "
        "decision is compared with the recorded kernel decision (seq, tick, call, target, decision, reason, "
        "taint; result for denials). **Recorded policy** = the source at the commit whose policy files hash "
        "to the SHA-256 the run logged; **HEAD policy** = today's source (a cross-check, not the criterion). "
        "Live-run tapes come from `runs/` (git-ignored; three are committed as bundles in `replay/`); "
        "hardware tapes are the TAPE/AUDIT lines of the COM1 transcripts.",
        "",
    ]
    rows = []
    match = total = 0
    for t in d["tapes"]:
        if t.get("missing"):
            rows.append([t["label"], f"`{t['source']}`", "-", "-", "-", "tape not in this checkout", "-"])
            continue
        if t.get("late_link"):
            res = (
                f"not replayable (late link, D-70): first recorded decision is seq {t['first_seq']}; "
                f"replay gives {t.get('recorded')}"
            )
        else:
            total += 1
            match += t.get("recorded") == "MATCH"
            res = f"REPLAY {t.get('recorded')}"
            if t.get("recorded") != "MATCH":
                res += f" at #{t.get('recorded_mismatch_at')}: {t.get('recorded_why', '')[:120]}"
        pol = f"`{t['policy_sha256'][:12]}` @ `{t.get('recorded_policy_commit', '?')}`"
        rows.append(
            [
                t["label"],
                f"`{t['source']}`",
                t["tape_events"],
                t["decisions"],
                pol,
                res,
                "MATCH" if t["head"] == "MATCH" else f"{t['head']} at #{t.get('head_mismatch_at')}",
            ]
        )
    out += _table(
        [
            "Tape",
            "Source",
            "Tape events",
            "Kernel decisions",
            "Recorded policy",
            "Replay (recorded policy)",
            "Replay (HEAD policy)",
        ],
        rows,
    )
    late = sum(1 for t in d["tapes"] if t.get("late_link"))
    out += [
        "",
        f"Replayable tapes matching on the recorded policy: **{match} of {total}**. Late-link tapes "
        f"(listed above, not counted as replayed): {late}.",
    ]
    return out + [""]


def _e6(d: dict | None) -> list[str]:
    out = ["## E6 Cell safety (recorded hardware evidence)", ""]
    if d is None:
        return out + ["Not run yet.", ""]
    k = d["kernel_hw"]
    i, e, w = k["interlock"], k["estop"], k["watchdog"]
    out += [
        "No new hardware runs for this card: every row is computed from transcripts already recorded on the "
        "real cell. Times are the board's own `t_ms` (50 ms telemetry frames) or kernel ticks (10 ms). "
        "These rows ran on the RING3 build only (the permissive build keeps the same device limits, "
        "interlock "
        "and E-stop latch, ARCHITECTURE §7).",
        "",
        "**Kernel + board (T-04 step 7, `tests/test_inv_13_hw.py`, 1 run each, user present)**",
        "",
    ]
    rows = [
        [
            "Hand < 100 mm while the fan runs",
            f"`{i['source']}`",
            1,
            f"AUTO_HALT presence {i['kernel_ticks_after_frame']} tick(s) after the frame that showed the "
            "hand; "
            f"board fan 0 {i['board_fan0_ms_after_hand']} ms after that frame (t_ms)",
        ],
        [
            "Fan after the interlock stop",
            f"`{i['source']}`",
            1,
            f"{'; '.join(i['next_fan_call'])} (result {', '.join(map(str, i['next_fan_results']))}); "
            "frames with "
            f"fan > 0 after the cut: {i['frames_after_cut_fan_gt0']} of {i['frames_after_cut']}",
        ],
        [
            "E-stop pressed while the fan runs",
            f"`{e['source']}`",
            1,
            f"AUTO_HALT estop {e['kernel_ticks_after_frame']} tick(s) after the frame; board fan 0 "
            f"{e['board_fan0_ms_after_press']} ms after it; board latch flag {_yn(e['board_latched_flag'])}",
        ],
        [
            "Motion while latched",
            f"`{e['source']}`",
            1,
            f"{e['motion_denied_while_latched']} cell_fan/cell_servo calls DENY_ESTOP (result "
            f"{', '.join(e['motion_denied_results'])}); cell_halt allowed {e['halt_calls_allowed']} time(s)",
        ],
        [
            "Reset at the console (human only)",
            f"`{e['source']}`",
            1,
            "while pressed then after release: " + " -> ".join(e["console_resets"]),
        ],
        [
            "Kernel silent 2.5 s (`cellmute`)",
            f"`{w['source']}`",
            1,
            f"fan {w['fan_before_mute']} before; the mute began after board t_ms "
            f"{w['t_ms_last_frame_before_mute']}; "
            f"LINK_LOST + fan 0 at t_ms {w['t_ms_link_lost_fan0']}, i.e. at most "
            f"{w['ms_mute_to_fan0_at_most']} ms into "
            f"the mute; servo {w['parked_servo']}; "
            f"fan > 0 afterwards in {w['frames_after_fan_gt0']} of {w['frames_after_until_status']} frames",
        ],
        [
            "QEMU killed with the fan at 60 %",
            f"`{w['source']}`",
            1,
            f"user-reported by eye: `{w['qemu_kill_user_report']}` (the kernel cannot timestamp its own "
            "death)",
        ],
    ]
    out += _table(["Case", "Source", "Runs", "Result"], rows)
    out += ["", "**Out-of-limit calls by the model on the real cell (T-04 live runs)**", ""]
    rows = [
        [r["label"], f"`{r['source']}`", "; ".join(r["cell_denials"]) or "none"]
        for r in d["live_limits"]["runs"]
    ]
    out += _table(["Run", "Source", "Kernel denials of cell calls"], rows)
    fw = d["firmware"]
    out += [
        "",
        f"**Board alone (T-06 `tools/cellctl.py a5`, no kernel; `{fw['source']}`)**: every SUMMARY row, "
        "counted per check. Rows before the D-49 E-stop ground fix are listed separately; the one later FAIL "
        "was a cellctl defect (T-06 README, test fix #8), re-run after the fix.",
        "",
    ]
    rows = []
    for part, label in (
        ("after_d49", "222d26d, D-49 wiring (incl. v2 build)"),
        ("before_d49", "before D-49"),
    ):
        for check, r in sorted(fw[part].items()):
            rows.append(
                [
                    label,
                    f"`{check}`",
                    r["ok"] + r["fail"],
                    r["ok"],
                    r["fail"],
                    ", ".join(map(str, r["ms"])) if r["ms"] else "-",
                ]
            )
    out += _table(["Hardware", "Check", "Runs", "OK", "FAIL", "Measured ms (as printed)"], rows)
    return out + [""]


def _e4(d: dict | None) -> list[str]:
    out = ["## E4 Agent tasks (real Muse Spark, cell attached; paid, run by the user)", ""]
    if d is None:
        return out + ["Not run yet (paid suite; the user runs `tools/eval/e4_agent.py`, D-57).", ""]
    from e4_render import render_e4  # noqa: PLC0415

    return out + render_e4(d) + [""]


def _e7(d: dict | None) -> list[str]:
    out = ["## E7 Camera witness", ""]
    if d is None:
        return out + ["Not run yet (T-07 Part B writes `docs/evals/raw/e7.json`).", ""]
    rows = d.get("rows")
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        head = list(rows[0].keys())
        out += _table(head, [[r.get(h, "") for h in head] for r in rows])
    else:
        out += ["```", json.dumps(d, indent=1, sort_keys=True), "```"]
    return out + [""]


def render(raw: Path) -> str:
    data = {s: _load(raw, s) for s in ("e1", "e2", "e3", "e4", "e5", "e6", "e7")}
    out = [
        "# RING3 evaluation results",
        "",
        "Generated by `make evals` (`tools/eval/run_all.py`) from `docs/evals/raw/*.json`. Do not edit by "
        "hand. "
        "Spec: `docs/evals/EVAL_PLAN.md`. Every number is read from a raw file; counts are given with their "
        "sample size. Count columns are deterministic for the fixed seeds; tick and ms columns are "
        "measurements and can differ by a tick between reruns.",
        "",
    ]
    fails = must_pass_failures(raw)
    out += [
        "**Must-pass rows on the RING3 build:** "
        + ("all hold." if not fails else "FAILED: " + "; ".join(fails)),
        "",
    ]
    out += _e1(data["e1"]) + _e2(data["e2"]) + _e3(data["e3"]) + _e4(data["e4"]) + _e5(data["e5"])
    out += _e6(data["e6"]) + _e7(data["e7"])
    return "\n".join(line for line in out).rstrip() + "\n"
