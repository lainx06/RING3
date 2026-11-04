"""T-08: the kernel's VGA text status screen.

Host golden tests of the pure renderer (kernel/ui/screen.c built by `make host-screen`) for the design spec's
three states with the card's corrections; a QEMU test that boots the test image with the `screen` option and
the simulated cell and reads the VGA text buffer through QEMU's monitor (`pmemsave`); screendumps of the
three states from a simulated-cell run (build/test-logs/T-08/, or docs/evidence/T-08/ with RING3_EVIDENCE=1).
"""

from __future__ import annotations

import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

from tests.cellharness import CONFIRM, END, Cell, cellcmd, results, wait_fresh
from tests.qemu import REPO, Session, evidence_dir
from tests.sim_cell import SimCell

SCREEN_TEST = REPO / "build" / "host" / "screen_test"
ROWS, COLS = 25, 80

# attributes: foreground | background << 4
FRAME, TEXT, INFO, ASK, ALARM = 0x0E, 0x0F, 0x0B, 0xE0, 0x4F


@pytest.fixture(scope="module", autouse=True)
def host_screen():
    subprocess.run(["make", "-s", "host-screen"], cwd=REPO, check=True)
    assert SCREEN_TEST.exists()


class Screen:
    """25x80 VGA text cells (CP437 char, attribute); rows and columns one-based as in the design spec."""

    def __init__(self, raw: bytes):
        assert len(raw) == ROWS * COLS * 2, len(raw)
        self.raw = raw

    def row(self, r: int) -> str:
        base = (r - 1) * COLS * 2
        return bytes(self.raw[base + 2 * c] for c in range(COLS)).decode("cp437")

    def text(self) -> list[str]:
        return [self.row(r) for r in range(1, ROWS + 1)]

    def attr(self, r: int, c: int) -> int:
        return self.raw[((r - 1) * COLS + (c - 1)) * 2 + 1]

    def attrs(self, r: int, c0: int, c1: int) -> set[int]:
        return {self.attr(r, c) for c in range(c0, c1 + 1)}


def render(state: str) -> Screen:
    out = subprocess.run([str(SCREEN_TEST), state], capture_output=True, check=True).stdout
    return Screen(out)


# ---- host golden tests ------------------------------------------------------------------------------------
GOLDEN = {
    "idle": """
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:02:14   EXEC IDLE                 FREE 14920 KiB   LOGGED 41           ║
║ LAST KERNEL DECISION: ALLOW                              TAINTED: NO         ║
║                                    ALLOW                                     ║
║ cell_fan(0)                                                                  ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ agentsh: action 3 fan_set(0) -> 0                                            ║
║ OUTCOME DONE: gauge at 90, fan off                                           ║
║ EXECUTED 3 operation(s):                                                     ║
║ END pid=6 EXIT ticks=388 uptime=13300                                        ║
║ ring3>                                                                       ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 320 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▓▓▓▒▒▒▒▒▒▒▒░░░░░░░░█······ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        ▀        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 10 ms         ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:00:00  REQ -  BOARD BOOT  fresh boot                                      ║
║ 00:02:10  REQ 6  ALLOW       cell_status                                     ║
║ 00:02:11  REQ 6  ALLOW       cell_servo(90)                                  ║
║ 00:02:12  REQ 6  ALLOW       cell_fan(0)                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    "ask": """
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:03:09   EXEC RUNNING REQ 7        FREE 14920 KiB   LOGGED 58           ║
║ LAST KERNEL DECISION: ASK                                TAINTED: YES        ║
║                                     ASK                                      ║
║ cell_fan(60): tainted: ask; waiting for y/N                                  ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ agentsh: request "read the operator notes, then set the fan to 60%"          ║
║ agentsh: action 1 read_object(1) -> 216                                      ║
║ agentsh: action 2 fan_set(100) -> -200 (ESAFETY)                             ║
║ agentsh: action 3 cell_status(0) -> 16                                       ║
║ CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N]                  ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 220 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▒▒▒▒▒▒░░░░░█·············· ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        ▀        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 40 ms         ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:03:05  REQ 7  ALLOW       obj_read(maint_notes)                           ║
║ 00:03:06  REQ 7  DENIED      cell_fan(100): fan max 80%                      ║
║ 00:03:07  REQ 7  ALLOW       cell_status                                     ║
║ 00:03:09  REQ 7  ASK         cell_fan(60): tainted: ask                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    "estop": """
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:03:12   EXEC RUNNING REQ 7        FREE 14920 KiB   LOGGED 66           ║
║ LAST KERNEL DECISION: AUTO HALT                          TAINTED: YES        ║
║                                  AUTO HALT                                   ║
║ E-STOP latched                                                               ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║ agentsh: action 3 cell_status(0) -> 16                                       ║
║ CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N] y                ║
║ agentsh: action 4 fan_set(60) -> 0                                           ║
║                                                                              ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 120 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▒▒▒░░░█······················ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP PRESSED, LATCHED     ║
║     FAN 000%     │ █▀        ▀        ▀█ │ ▀███▀ LINK ok                     ║
║  HALTED (E-STOP) │  SERVO 090° (30-150)  │       TELEMETRY AGE 30 ms         ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:03:07  REQ 7  ALLOW       cell_status                                     ║
║ 00:03:09  REQ 7  ASK         cell_fan(60): tainted: ask                      ║
║ 00:03:10  REQ 7  CONFIRMED   cell_fan(60)                                    ║
║ 00:03:12  REQ 7  AUTO HALT   E-STOP latched                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
}


@pytest.mark.parametrize("state", ["idle", "ask", "estop"])
def test_render_golden_text(state):
    """Exact 80-column text of every row for the three states (corrections: real call names, fan 0 or
    50-80, the E-stop leaves the executor RUNNING, servo dial 0-180 with the 30/150 limits, 0-400 mm bar)."""
    want = GOLDEN[state].strip("\n").split("\n")
    assert len(want) == ROWS and all(len(r) == COLS for r in want), [len(r) for r in want]
    got = render(state).text()
    pairs = enumerate(zip(want, got, strict=True), 1)
    diff = [f"row {i}:\n  want {w!r}\n  got  {g!r}" for i, (w, g) in pairs if w != g]
    assert not diff, "\n".join(diff)


def test_render_colours_idle():
    s = render("idle")
    assert s.attrs(1, 1, 80) == {FRAME} and s.attr(25, 80) == FRAME and s.attr(7, 1) == FRAME
    # ALLOW: light cyan on black across the banner rows
    assert s.attrs(3, 3, 57) == {INFO} and s.attrs(4, 3, 78) == {INFO} and s.attrs(5, 3, 78) == {INFO}
    assert s.attrs(3, 60, 70) == {INFO}  # TAINTED: NO
    assert s.attrs(11, 3, 8) == {INFO} and s.attr(2, 3) == INFO  # prompt, uptime (T-14: console rows 7-11)
    assert s.attrs(17, 52, 66) == {TEXT} and s.attrs(18, 52, 58) == {INFO}  # RELEASED, LINK ok (T-14 panel)
    assert s.attrs(24, 20, 24) == {INFO}  # ticker ALLOW


def test_render_colours_ask():
    s = render("ask")
    for r, c1 in ((3, 57), (4, 78), (5, 78)):
        assert s.attrs(r, 3, c1) == {ASK}, r  # ASK: black on yellow, the band across the field
    assert s.attrs(3, 60, 71) == {ASK}  # TAINTED: YES
    assert s.attrs(11, 3, 78) == {ASK}  # the CONFIRM line (T-14: console rows 7-11)
    assert s.attrs(22, 20, 25) == {ALARM} and s.attrs(24, 20, 22) == {ASK}  # DENIED, ASK in the ticker
    assert s.attr(2, 17) == TEXT  # EXEC RUNNING


def test_render_colours_estop():
    s = render("estop")
    for r, c1 in ((3, 57), (4, 78), (5, 78)):
        assert s.attrs(r, 3, c1) == {ALARM}, r  # AUTO HALT: white on dark red
    assert s.attrs(17, 52, 74) == {ALARM}  # E-STOP PRESSED, LATCHED (T-14 panel)
    assert s.attrs(23, 20, 28) == {INFO}  # CONFIRMED
    assert s.attrs(2, 17, 34) == {TEXT}  # correction 3: RUNNING, not a KILLED alarm
    assert "KILLED" not in "\n".join(s.text())


def test_render_banner_wipe():
    """Motion: the words change at once; the banner colours reach row 3 at once, row 4 by ~70 ms and row 5
    by ~140 ms (100 ms after the ASK here: rows 3 and 4 coloured, row 5 not yet)."""
    s = render("ask-wipe")
    assert s.text() == render("ask").text()
    assert s.attrs(3, 3, 57) == {ASK} and s.attrs(4, 3, 78) == {ASK}
    assert s.attrs(5, 3, 78) == {TEXT}


def test_render_no_cell_shows_no_values():
    """Without telemetry nothing is drawn as a reading: dashes, no beam, no decisions (T-14 panel rows; the
    gauge without a needle is checked by pixel colour in test_t14_panel)."""
    t = render("nocell").text()
    assert "FAN ---" in t[17] and "SERVO ---" in t[18] and "DIST ---" in t[12]
    assert not set("\u2591\u2592\u2593") & set(t[13])  # no beam shades
    assert "LINK no cell (COM3 absent)" in t[17] and "LAST KERNEL DECISION: -" in t[2]
    assert all(r.strip("║ ") == "" for r in t[20:24])


BANNED = re.compile(r"\b(success\w*|secure\w*|safe|safely|real-?time)\b", re.I)
#: D-66a: no franchise names or symbols, no military or alarm-movie wording on the screen
NOT_OURS = re.compile(
    r"\b(NERV|SEELE|MAGI|EVA|Evangelion|Gundam|Akira|Section 9|Weyland|Nostromo|HAL ?9000|Skynet|DEFCON|"
    r"weapons?|missiles?|warheads?|combat|tactical|battle|attack|enemy|hostile|intercept|launch|strike|"
    r"red alert|condition red|self-destruct|military)\b",
    re.I,
)


def test_rendered_text_wording():
    for state in ("idle", "ask", "estop", "ask-wipe", "nocell"):
        text = "\n".join(render(state).text())
        assert not BANNED.search(text) and not NOT_OURS.search(text), state


def test_screen_sources_wording():
    """A3: the new files' string literals (screen, words, VGA driver) carry no franchise or military
    wording; INV-11's scanner (test_inv_11_banned_words) already covers kernel/**/*.c and *.h."""
    from tests.test_inv_11_banned_words import c_literals

    files = [REPO / p for p in ("kernel/ui/screen.c", "kernel/ui/words.c", "kernel/drivers/vga.c")]
    for f in files:
        for line, s in c_literals(f):
            assert not NOT_OURS.search(s) and not BANNED.search(s), f"{f.name}:{line}: {s!r}"


# ---- QEMU: the real VGA buffer --------------------------------------------------------------------------
class Monitor:
    """QEMU's human monitor on a unix socket."""

    def __init__(self, path: Path, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(str(path))
                break
            except OSError:
                self.sock.close()
                assert time.monotonic() < deadline, "no QEMU monitor socket"
                time.sleep(0.05)
        self.sock.settimeout(timeout)
        self._until_prompt()

    def _until_prompt(self) -> str:
        buf = b""
        while not buf.endswith(b"(qemu) "):
            chunk = self.sock.recv(4096)
            assert chunk, "monitor closed"
            buf += chunk
        return buf.decode("utf-8", "replace")

    def cmd(self, line: str) -> str:
        self.sock.sendall(line.encode() + b"\n")
        return self._until_prompt()

    def close(self) -> None:
        self.sock.close()


def read_vga(mon: Monitor, folder: Path) -> Screen:
    out = folder / "vga.bin"
    out.unlink(missing_ok=True)
    mon.cmd(f'pmemsave 0xb8000 {ROWS * COLS * 2} "{out}"')
    deadline = time.monotonic() + 5
    while not (out.exists() and out.stat().st_size == ROWS * COLS * 2):
        assert time.monotonic() < deadline, "pmemsave wrote nothing"
        time.sleep(0.05)
    return Screen(out.read_bytes())


def screendump(mon: Monitor, png: Path) -> None:
    png.unlink(missing_ok=True)
    mon.cmd(f'screendump "{png}" -f png')
    deadline = time.monotonic() + 5
    while not (png.exists() and png.stat().st_size > 0):
        assert time.monotonic() < deadline, "screendump wrote nothing"
        time.sleep(0.05)


def start_screen(tmp_path: Path, quotas: str) -> tuple[Cell, Monitor]:
    """The test image with the `screen` boot option, the simulated cell on COM3, a monitor socket."""
    sock = tmp_path / "mon.sock"
    s = Session(append=f"{quotas} screen", com3=True, timeout=60.0, monitor=f"unix:{sock},server=on,wait=off")
    s.expect(r"^cell: COM3 present.*$")
    sim = SimCell(s.com3_port).start()
    s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=\d+ decision=BOARD_BOOT reason=board_boot .*$")
    s.prompt()
    c = Cell(s, sim)
    wait_fresh(c)
    return c, Monitor(sock)


def settle() -> None:
    time.sleep(0.4)  # > the 140 ms wipe, a few telemetry frames, several render ticks


def screen_stats(c: Cell) -> dict[str, int]:
    c.s.send("screen")
    m = c.s.expect(r"^screen: (renders=.*)$")
    c.s.prompt()
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", m.group(1))}


def test_t08_vga_buffer(tmp_path):
    """A2 + A4: the VGA text buffer of a running kernel shows the status bar, the console window and the
    kernel's real decision words; the measured render time per tick stays under 1 ms; no PANIC, no drops."""
    c, mon = start_screen(tmp_path, "taction=100 trequest=400 tconfirm=150")
    with c:
        time.sleep(0.6)  # the figures start after the 0.5 s TSC calibration: measure the busy part too
        t = cellcmd(c, "status; servo 120; fan 90")
        got = [r for r in results(t) if not r[0].startswith("status")]
        assert got == [("servo 120", 0), ("fan 90", -200)], t
        settle()
        v = read_vga(mon, tmp_path)
        rows = v.text()
        assert rows[0].startswith("╔═ RING3 / KERNEL EXECUTIVE ═") and rows[24][0] == "╚"
        assert re.match(r"^║ UP 00:00:\d\d   EXEC IDLE +FREE \d+ KiB +LOGGED \d+ +║$", rows[1]), rows[1]
        assert rows[2].startswith("║ LAST KERNEL DECISION: DENIED ") and "TAINTED: NO" in rows[2]
        assert rows[3].strip("║ ") == "DENIED" and rows[4].startswith("║ cell_fan(90): fan max 80%")
        assert v.attrs(4, 3, 78) == {ALARM}
        console = "\n".join(rows[6:11])  # T-14: console rows 7-11
        # T-14: the 5-row window has scrolled past the servo/fan result lines (both are in the ticker below);
        # it must show the newest COM1 lines, in order, with the test-image TAPE/AUDIT records left out
        com1 = [ln.rstrip() for ln in c.s.output.replace("\r", "").split("\n")]
        com1 = [ln for ln in com1 if ln and not ln.startswith(("TAPE ", "AUDIT "))]
        shown = [r[2:78].rstrip() for r in rows[6:11] if r[2:78].strip()]
        assert shown == com1[-len(shown) :] and "cellcmd: done" in shown, (shown, com1[-8:])
        assert "cellcmd: servo 120 -> 0" in com1 and "cellcmd: fan 90 -> -200" in com1
        # the prompt; on test images COM1 starts a new line after it when the tape records print
        assert any(r.startswith("║ ring3>") for r in rows[9:11]), rows[9:11]
        assert "TAPE " not in console and "AUDIT " not in console  # test-image records stay off the window
        # T-14 panel rows 17-19
        assert "SERVO 120°" in rows[18] and "LINK ok" in rows[17] and "E-STOP RELEASED" in rows[16]
        ticker = rows[20:24]
        assert re.match(r"^║ \d\d:\d\d:\d\d  REQ \d+ +ALLOW       cell_servo\(120\) +║$", ticker[2]), ticker
        assert re.match(r"^║ \d\d:\d\d:\d\d  REQ \d+ +DENIED      cell_fan\(90\): fan max 80% +║$", ticker[3])
        st = screen_stats(c)
        assert st["renders"] > 50 and st["over_1ms"] == 0 and st["max_us"] < 1000, st
        (evidence_dir("T-08") / "render-time.txt").write_text(f"test_t08_vga_buffer: {st}\n")
        mon.close()
        c.close()  # INV-12 / F5: no PANIC, dropped=0


def test_t08_screenshots_sim(tmp_path):
    """The three states from a simulated-cell run (screendump PNG + text dump): idle after a request, ASK
    with a tainted executor, AUTO HALT on the E-stop with the executor still running (correction 3)."""
    folder = evidence_dir("T-08")
    c, mon = start_screen(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        cellcmd(c, "status; servo 120; wait 25; servo 90")
        settle()
        idle = read_vga(mon, tmp_path)
        screendump(mon, folder / "sim-idle.png")
        assert "EXEC IDLE" in idle.row(2) and "ALLOW       cell_servo(90)" in idle.row(24)

        c.s.send("run cellcmd read 1; fan 60; wait 300")
        m = c.s.expect(CONFIRM)
        assert m.group(4), "the read of maint_notes taints the executor"
        settle()
        ask = read_vga(mon, tmp_path)
        screendump(mon, folder / "sim-ask.png")
        assert "EXEC RUNNING REQ" in ask.row(2) and "TAINTED: YES" in ask.row(3)
        assert ask.row(4).strip("║ ") == "ASK" and "waiting for y/N" in ask.row(5)
        assert v_has(ask, "CONFIRM: cellcmd wants cell_fan(60) [tainted]. Allow? [y/N]")

        c.s.send("y")
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=60 decision=AUTO_HALT reason=estop .*$")
        c.sim.wait_fan(0, start=n)
        settle()
        es = read_vga(mon, tmp_path)
        screendump(mon, folder / "sim-estop.png")
        assert "EXEC RUNNING REQ" in es.row(2) and "KILLED" not in es.row(2)
        assert es.row(4).strip("║ ") == "AUTO HALT" and es.row(5).startswith("║ E-STOP latched")
        assert "E-STOP PRESSED, LATCHED" in es.row(17) and "FAN 000%" in es.row(18)  # T-14 panel
        assert "CONFIRMED   cell_fan(60)" in es.row(23) and "AUTO HALT   E-STOP latched" in es.row(24)

        c.sim.estop = False
        c.s.expect(END, 20)
        c.s.prompt()
        for name, scr in (("idle", idle), ("ask", ask), ("estop", es)):
            (folder / f"sim-{name}.txt").write_text("\n".join(scr.text()) + "\n", encoding="utf-8")
        mon.close()
        c.close()


def v_has(s: Screen, needle: str) -> bool:
    return any(needle in r for r in s.text())
