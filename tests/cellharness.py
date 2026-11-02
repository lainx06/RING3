"""Shared pieces of the T-04 simulated-cell tests: a QEMU session with COM2 + COM3, the simulated board
synced (one BOARD_BOOT audited), and helpers to run `cellcmd` scripts and read their result lines."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from tests.qemu import FAST_QUOTAS, Session, audit_lines
from tests.sim_cell import SimCell

END = r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$"
CONFIRM = r"CONFIRM: (\S+) wants (\S+)\((.*)\)( \[tainted\])?\. Allow\? \[y/N\] "
CONSOLE_CONFIRM = r"CONFIRM: console wants estop_reset .*Allow\? \[y/N\] "


@dataclass
class Cell:
    s: Session
    sim: SimCell

    def __enter__(self) -> Cell:
        return self

    def __exit__(self, *exc: object) -> None:
        """A failed test must not leave QEMU or the sim thread behind."""
        self.sim.stop()
        self.s.__exit__(*exc)

    def close(self):
        """`exit` on the console, the harness checks (PANIC, dropped), the sim stopped; returns the Run."""
        run = self.s.close()
        self.sim.stop()
        return run


def start(append: str = FAST_QUOTAS, timeout: float = 60.0, **sim_kw) -> Cell:
    """Boots the test image with COM2 (null unless a bridge is wanted) and COM3, connects the simulated board
    and waits for the kernel to sync on its boot (the BOARD_BOOT audit)."""
    s = Session(append=append, com2=sim_kw.pop("com2", False), com3=True, timeout=timeout)
    s.expect(r"^cell: COM3 present.*$")
    sim = SimCell(s.com3_port, **sim_kw).start()
    s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=\d+ decision=BOARD_BOOT reason=board_boot .*$")
    s.prompt()
    c = Cell(s, sim)
    wait_fresh(c)
    return c


def cellcmd(c, script: str, answers: list[str] | None = None, timeout: float = 30.0) -> str:
    """`run cellcmd <script>`; each CONFIRM prompt is answered from `answers` in order. Returns the
    transcript from RUN to END."""
    c.s.send(f"run cellcmd {script}")
    start_pos = c.s.pos
    pending = list(answers or [])
    while True:
        m = c.s.expect(rf"({CONFIRM})|({END})", timeout)
        if m.group(0).startswith("CONFIRM"):
            assert pending, f"unexpected prompt {m.group(0)!r}"
            c.s.send(pending.pop(0))
            continue
        break
    assert not pending, f"answers left over: {pending}"
    t = c.s.output[start_pos : c.s.pos]
    c.s.prompt()
    time.sleep(0.12)  # the sim thread reads the socket every 2 ms; let the last frames land before asserting
    return t


def results(t: str) -> list[tuple[str, int]]:
    """[('fan 60', 0), ('servo 120', -200), ...] from a cellcmd transcript."""
    return [(m.group(1), int(m.group(2))) for m in re.finditer(r"^cellcmd: (.+?) -> (-?\d+)$", t, re.M)]


#: audit subjects that are plumbing, not the device path (left out of `decisions()` unless asked for)
PLUMBING = {"exec_start", "exec_end", "cons_write", "obj_list", "obj_read", "obj_write", "uptime",
            "agent_send", "agent_recv", "action", "link_drop"}  # fmt: skip


def decisions(t: str, sys: str | None = None) -> list[tuple[str, str, str]]:
    """(sys, decision, reason) of the AUDIT lines in `t`: one subject, or every device-path subject (the
    cell_* calls, confirm, telemetry, tick, console, unknown numbers)."""
    return [
        (a["sys"], a["decision"], a["reason"])
        for a in audit_lines(t)
        if (a["sys"] == sys if sys is not None else a["sys"] not in PLUMBING)
    ]


def last_request(c) -> str:
    """The console output from the last `RUN pid=` line on."""
    out = c.s.output
    return out[out.rfind("RUN pid=") :]


def wait_fresh(c, timeout: float = 3.0) -> None:
    """Waits until the kernel holds fresh telemetry (3 monotonic frames after a boot)."""
    deadline = time.monotonic() + timeout
    while True:
        c.s.send("status")
        m = c.s.expect(r"^status: .*tele_valid=(\d) .*$")
        c.s.prompt()
        if m.group(1) == "1":
            return
        assert time.monotonic() < deadline, "telemetry never became fresh"
        time.sleep(0.1)


def estop_reset(c, answer: str | None) -> str:
    """`estop reset` at the prompt, the prompt answered (None = let it time out); returns the result line."""
    c.s.send("estop reset")
    c.s.expect(CONSOLE_CONFIRM)
    if answer is not None:
        c.s.send(answer)
    m = c.s.expect(r"^estop reset: (.*)$", 15)
    c.s.prompt()
    return m.group(1)
