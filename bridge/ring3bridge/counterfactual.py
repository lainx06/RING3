"""``ring3 counterfactual <bundle>`` (T-17, D-98, D-99): the same recorded inputs through both policy builds.

The bundle's inbound tape (every event the kernel saw: the executor's system calls made from the model's
proposals, telemetry, console answers) goes through the host build of RING3's policy and through the host
build of the permissive variant (ARCHITECTURE §7). Each row is one tape event that produced a decision in
either build. The RING3 column must equal the bundle's own recorded decisions (the ``ring3 replay`` check),
or nothing is printed but an error.

Honesty rule (printed with every table): rows up to and including the first divergence are exact, because
up to that point both builds saw the same inputs and gave the same answers. After it, the recorded proposals
were made after RING3's answer, so those rows are hypothetical: a model talking to the permissive kernel
would have seen a different answer and might have proposed something else.

Host only: no QEMU, no key, no network, no hardware. Nothing here is a live run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from ring3bridge import abi, replay

BANNER = "PERMISSIVE BUILD: no capability, taint or confirmation checks (device limits kept)"
HONESTY = (
    "Rows up to and including the first DIFFERENT row are exact: both builds got the same recorded inputs. "
    "After it, the recorded proposals were made after RING3's answer, so later rows are marked "
    '"hypothetical (the model saw RING3\'s answer)".'
)
EXACT = "exact"
HYPOTHETICAL = "hypothetical (the model saw RING3's answer)"
SAME_DEVICE = "device limits are in both builds"
#: Decisions made by the device rules (limits, interlock, stale telemetry, the E-stop latch): both builds.
DEVICE_CODES = frozenset({"DENY_LIMIT", "DENY_INTERLOCK", "DENY_STALE", "DENY_ESTOP", "AUTO_HALT"})
#: Executor plumbing shown only with ``--all`` (or when the builds differ on it).
PLUMBING = frozenset({"cons_write", "agent_send", "agent_recv"})
CELL_FIELDS = ("decision", "reason", "target", "result")


@dataclass(frozen=True)
class Row:
    ev: int  # index of the tape event
    req: int  # request number (0 = before the first request)
    op: str  # operation and arguments
    ring3: dict[str, str] | None
    permissive: dict[str, str] | None
    different: bool
    basis: str  # EXACT or HYPOTHETICAL
    note: str

    @property
    def num(self) -> str:
        """The decision number: RING3's audit seq (the bundle's own), else the permissive one as pN."""
        if self.ring3 is not None:
            return self.ring3["seq"]
        return "p" + self.permissive["seq"] if self.permissive is not None else "-"

    @property
    def plumbing(self) -> bool:
        return self.op.split("(")[0] in PLUMBING or self.op == "telemetry"


@dataclass
class Result:
    ok: bool
    message: str
    rows: list[Row]
    first_divergence: Row | None = None
    bundle_policy: str = ""


def _tape_events(records: list[dict]) -> list[dict]:
    """The event records in tape order (the same filter as ``replay.tape_lines``)."""
    return [r for r in records if r.get("type") == "event" and "a" in r]


def _host_decisions(exe: Path, tape: list[str]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for a in replay.parse_audit_lines(replay.run_host_policy(exe, tape, with_ev=True)):
        if "ev" not in a:
            raise RuntimeError(f"{exe.name} printed no ev= field (rebuild: make host-policy)")
        out[int(a["ev"])] = a
    return out


def _op(ev: dict, sysname: str) -> str:
    """Operation and arguments of one tape event, from the tape itself."""
    a0 = ev["a"][0]
    if ev["kind"] != abi.EV_SYSCALL:
        name = abi.POLICY_EVENT_NAMES[ev["kind"]] if ev["kind"] < len(abi.POLICY_EVENT_NAMES) else "?"
        if name == "confirm":
            return "confirm (the human's answer)"
        if name == "console":
            return "console (operator command)"
        return name
    if sysname in ("obj_read", "obj_write"):
        obj = abi.OBJECT_NAMES[a0] if 0 <= a0 < len(abi.OBJECT_NAMES) else str(a0)
        return f"{sysname}({obj})"
    if sysname in ("cell_fan", "cell_servo"):
        return f"{sysname}({a0})"
    return sysname


def cell_text(d: dict[str, str] | None) -> str:
    if d is None:
        return "- (no decision)"
    s = f"{d['decision']} {d['reason']}"
    return s + f" ({d['result']})" if d["result"] != "0" else s


def _note(r3: dict[str, str] | None, pm: dict[str, str] | None, different: bool) -> str:
    if not different:
        return SAME_DEVICE if r3 is not None and r3["decision"] in DEVICE_CODES else ""
    asked = r3 is not None and r3["decision"] == "CONFIRM_PENDING"
    if asked and pm is not None and pm["decision"] == "ALLOW":
        return "RING3 asks the human; the permissive build allows it without asking"
    if r3 is not None and r3["decision"] in ("CONFIRM_YES", "CONFIRM_NO") and pm is None:
        return "the permissive build asked nobody, so there was no answer to decide"
    if r3 is not None and r3["decision"].startswith("DENY") and pm is not None and pm["decision"] == "ALLOW":
        return "RING3 refuses; the permissive build allows it"
    return ""


def counterfactual(
    run_dir: Path, ring3_exe: Path | None = None, permissive_exe: Path | None = None
) -> Result:
    ring3_exe = Path(ring3_exe or replay.HOST_POLICY_TEST)
    permissive_exe = Path(permissive_exe or replay.HOST_POLICY_TEST_PERMISSIVE)
    for exe in (ring3_exe, permissive_exe):
        if not exe.exists():
            return Result(False, f"COUNTERFACTUAL ERROR: {exe} missing (make host-policy)", [])
    records = replay.read_records(run_dir)
    start = next((r for r in records if r.get("type") == "run_start"), {})
    if any(r.get("type") == "audit" and r.get("permissive") for r in records):
        return Result(False, "COUNTERFACTUAL ERROR: the bundle was recorded on the permissive build", [])
    events = _tape_events(records)
    tape = replay.tape_lines(records)
    try:
        r3 = _host_decisions(ring3_exe, tape)
        pm = _host_decisions(permissive_exe, tape)
    except RuntimeError as e:
        return Result(False, f"COUNTERFACTUAL ERROR: {e}", [])
    # The RING3 column must be the bundle's own decisions (INV-10), field for field.
    at, why = replay.compare(replay.recorded_audits(records), [r3[k] for k in sorted(r3)])
    if at is not None:
        return Result(False, f"COUNTERFACTUAL ERROR: the RING3 column does not match the recording at #{at}: "
                      f"{why}", [])  # fmt: skip
    rows: list[Row] = []
    first: Row | None = None
    req = 0
    for i, ev in enumerate(events):
        if ev["kind"] == abi.EV_EXEC_START:
            req += 1
        a, b = r3.get(i), pm.get(i)
        if a is None and b is None:
            continue
        sysname = (a or b)["sys"]
        different = (a is None) != (b is None) or (
            a is not None and b is not None and any(a[f] != b[f] for f in CELL_FIELDS)
        )
        row = Row(i, req, _op(ev, sysname), a, b, different, EXACT if first is None else HYPOTHETICAL,
                  _note(a, b, different))  # fmt: skip
        rows.append(row)
        if different and first is None:
            first = row
    n_diff = sum(r.different for r in rows)
    if first is None:
        msg = f"NO DIVERGENCE: all {len(rows)} decisions are the same in both builds; every row is exact"
    else:
        msg = (
            f"FIRST DIVERGENCE at #{first.num} {first.op}: RING3 {cell_text(first.ring3)}, permissive "
            f"{cell_text(first.permissive)}; {n_diff} DIFFERENT row(s) of {len(rows)}"
        )
    return Result(True, msg, rows, first, start.get("policy_sha256", ""))


def visible(rows: list[Row], show_all: bool) -> list[Row]:
    return rows if show_all else [r for r in rows if r.different or not r.plumbing]


HEAD = ("#", "req", "operation and arguments", "RING3", "permissive", "diff", "rows", "note")


def _cols(r: Row) -> tuple[str, ...]:
    return (r.num, str(r.req), r.op, cell_text(r.ring3), cell_text(r.permissive),
            "DIFFERENT" if r.different else "same", r.basis, r.note)  # fmt: skip


def format_text(rows: list[Row]) -> list[str]:
    table = [HEAD, *(_cols(r) for r in rows)]
    w = [max(len(t[c]) for t in table) for c in range(len(HEAD))]
    return ["  ".join(t[c].ljust(w[c]) for c in range(len(HEAD))).rstrip() for t in table]


def format_markdown(rows: list[Row]) -> list[str]:
    def line(t: tuple[str, ...]) -> str:
        return "| " + " | ".join(x.replace("|", "\\|") for x in t) + " |"

    return [line(HEAD), "|" + "---|" * len(HEAD), *(line(_cols(r)) for r in rows)]


def report(run_dir: Path, show_all: bool = False, markdown: bool = False) -> tuple[int, list[str]]:
    from ring3bridge.run import policy_sha256

    res = counterfactual(run_dir)
    if not res.ok:
        return 1, [res.message]
    shown = visible(res.rows, show_all)
    ring3_hash = policy_sha256("default")
    n_ring3 = sum(r.ring3 is not None for r in res.rows)
    more = "" if show_all else " (--all adds telemetry and executor plumbing that is the same in both builds)"
    out = [
        f"COUNTERFACTUAL {Path(run_dir).name}: host replay of a recorded run, not a live run "
        "(no model, no key, no network, no hardware)",
        f"RING3 policy sha256 {ring3_hash[:16]}... (bundle recorded on {res.bundle_policy[:16]}...)",
        f"permissive policy sha256 {policy_sha256('permissive')[:16]}...: {BANNER}",
        f"RING3 column = the bundle's own decisions (REPLAY MATCH: {n_ring3})",
        f"Honesty rule: {HONESTY}",
        "",
        *(format_markdown(shown) if markdown else format_text(shown)),
        "",
        f"{len(shown)} of {len(res.rows)} rows shown{more}",
        res.message,
    ]  # fmt: skip
    return 0, out


def add_parser(sub: argparse._SubParsersAction) -> None:
    """The `ring3 counterfactual` subcommand (kept here so cli.py changes by two lines)."""
    p = sub.add_parser("counterfactual", help="the recorded tape through RING3's and the permissive policy")
    p.add_argument("run", help="run directory or replay bundle, or a run id under runs/")
    p.add_argument("--all", action="store_true", help="every decision, telemetry and plumbing included")
    p.add_argument("--markdown", action="store_true", help="the table as Markdown")
    p.set_defaults(fn=_cmd)


def _cmd(a: argparse.Namespace) -> int:
    from ring3bridge.cli import _run_dir

    rc, lines = report(_run_dir(a.run), a.all, a.markdown)
    print("\n".join(lines))
    return rc
