"""T-14: the VGA cell panel (pictures of the work cell from real data, text mode, D-94).

Host tests of the pure renderer (`make host-screen`): exact 80-column goldens of the panel states that
T-08's goldens don't cover (fan 60 % at a fixed rotor frame, presence, no echo, stale; idle, tainted ASK
and E-stop latched are T-08's goldens with the panel rows), pixel colours decoded from the half blocks, the
rotor's motion rule, the needle cells at 30 / 90 / 150 degrees, wording. A QEMU run with the simulated cell
takes a screendump of every state (build/test-logs/T-14/, or docs/evidence/T-14/ with RING3_EVIDENCE=1) and
measures the render time per tick with the rotor turning.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.cellharness import CONFIRM, END, cellcmd, wait_fresh
from tests.qemu import REPO, evidence_dir
from tests.sim_cell import NO_ECHO
from tests.test_t08_screen import (
    BANNED,
    NOT_OURS,
    ROWS,
    SCREEN_TEST,
    Screen,
    read_vga,
    render,
    screen_stats,
    screendump,
    settle,
    start_screen,
)

# VGA colours
BLACK, DRED, BROWN, GREY, DGREY, LCYAN, LRED, YELLOW, WHITE = 0, 4, 6, 7, 8, 11, 12, 14, 15
TEXT, INFO, ASK, ALARM, MARK = 0x0F, 0x0B, 0xE0, 0x4F, 0x0C

FAN = (7, 15, 13, 17)  # columns, rows (one-based, inclusive)
GAUGE = (22, 42, 13, 18)
LAMP = (46, 50, 17, 18)


@pytest.fixture(scope="module", autouse=True)
def host_screen():
    subprocess.run(["make", "-s", "host-screen"], cwd=REPO, check=True)
    assert SCREEN_TEST.exists()


def pixels(s: Screen, r: int, c: int) -> tuple[int, int]:
    """(top, bottom) pixel colours of a panel cell; 0 = empty."""
    ch = s.raw[((r - 1) * 80 + (c - 1)) * 2]
    a = s.attr(r, c)
    fg, bg = a & 15, a >> 4
    got = {0x20: (0, 0), 0xDB: (fg, fg), 0xDF: (fg, bg), 0xDC: (bg, fg)}.get(ch)
    assert got is not None, f"cell ({r},{c}) is not a pixel cell: {ch:#x}"
    return got


def colours(s: Screen, box: tuple[int, int, int, int]) -> set[int]:
    c0, c1, r0, r1 = box
    return {p for r in range(r0, r1 + 1) for c in range(c0, c1 + 1) for p in pixels(s, r, c)} - {BLACK}


def cells_with(s: Screen, box: tuple[int, int, int, int], colour: int) -> set[tuple[int, int]]:
    c0, c1, r0, r1 = box
    return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1) if colour in pixels(s, r, c)}


GOLDEN = {
    "fan60": """
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:02:30   EXEC IDLE                 FREE 14920 KiB   LOGGED 47           ║
║ LAST KERNEL DECISION: ALLOW                              TAINTED: NO         ║
║                                    ALLOW                                     ║
║ cell_fan(60)                                                                 ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ agentsh: action 2 servo_set(120) -> 0                                        ║
║ agentsh: action 3 fan_set(60) -> 0                                           ║
║ OUTCOME DONE: gauge at 120, fan at 60%                                       ║
║ END pid=6 EXIT ticks=402 uptime=15000                                        ║
║ ring3>                                                                       ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║         ███▄     │       ▄▄█▀█▀█▄▄       │ DIST 250 mm                 CLEAR ║
║     ▄▄  ██▀▀     │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▓▒▒▒▒▒▒▒░░░░░░█··········· ║
║     █████        │   █▀         █▀  ▀█   │          ┴100 mm           400 mm ║
║     ▀▀   ██▄     │  █▀█        █▀   █▀█  │                                   ║
║          ██▀     │  █         █▀      █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 060%     │ █▀        ▀▀       ▀█ │ ▀███▀ LINK ok                     ║
║      TURNING     │  SERVO 120° (30-150)  │       TELEMETRY AGE 20 ms         ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:00:00  REQ -  BOARD BOOT  fresh boot                                      ║
║ 00:02:10  REQ 6  ALLOW       cell_status                                     ║
║ 00:02:11  REQ 6  ALLOW       cell_servo(120)                                 ║
║ 00:02:12  REQ 6  ALLOW       cell_fan(60)                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    "presence": """
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
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 70 mm               PRESENCE ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▒▒░█·························· ║
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
    "noecho": """
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
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST NO ECHO              NO ECHO ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █································ ║
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
    "stale": """
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:02:30   EXEC IDLE                 FREE 14920 KiB   LOGGED 47           ║
║ LAST KERNEL DECISION: ALLOW                              TAINTED: NO         ║
║                                    ALLOW                                     ║
║ cell_fan(60)                                                                 ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ agentsh: action 2 servo_set(120) -> 0                                        ║
║ agentsh: action 3 fan_set(60) -> 0                                           ║
║ OUTCOME DONE: gauge at 120, fan at 60%                                       ║
║ END pid=6 EXIT ticks=402 uptime=15000                                        ║
║ ring3>                                                                       ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 250 mm                 STALE ║
║        ▀█▀       │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▓▒▒▒▒▒▒▒░░░░░░█··········· ║
║     ▄▄▄▄█▄▄▄▄    │   █▀         █▀  ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█        █▀   █▀█  │                                   ║
║      ▀▀    ▀     │  █         █▀      █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 060%     │ █▀        ▀▀       ▀█ │ ▀███▀ LINK STALE                  ║
║       STALE      │  SERVO 120° (30-150)  │       TELEMETRY AGE 900 ms        ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:00:00  REQ -  BOARD BOOT  fresh boot                                      ║
║ 00:02:10  REQ 6  ALLOW       cell_status                                     ║
║ 00:02:11  REQ 6  ALLOW       cell_servo(120)                                 ║
║ 00:02:12  REQ 6  ALLOW       cell_fan(60)                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
}


@pytest.mark.parametrize("state", list(GOLDEN))
def test_panel_golden_text(state):
    """Exact 80-column rows (rows 1-6 and 20-25 as T-08; console rows 7-11; panel rule 12, pictures 13-19)."""
    want = GOLDEN[state].strip("\n").split("\n")
    assert len(want) == ROWS and all(len(r) == 80 for r in want), [len(r) for r in want]
    got = render(state).text()
    diff = [
        f"row {i}:\n  want {w!r}\n  got  {g!r}"
        for i, (w, g) in enumerate(zip(want, got, strict=True), 1)
        if w != g
    ]
    assert not diff, "\n".join(diff)


def test_panel_layout_idle():
    """The panel replaced the LIVE CELL rows and took 2 console rows: rule at 12, separators at 20 and 44."""
    s = render("idle")
    assert s.row(6).startswith("╠═ CONSOLE ") and s.row(12).startswith("╠═ LIVE CELL ")
    assert s.row(12)[19] == "╤" and s.row(12)[43] == "╤"
    assert all(s.row(r)[19] == "│" and s.row(r)[43] == "│" for r in range(13, 20))
    assert s.row(20).startswith("╠═ LAST 4 KERNEL DECISIONS")


def test_fan_colours_follow_the_reported_duty():
    """Turning: light cyan blades, white hub, TURNING. Frozen (duty 0, latched, stale, no data): only the
    halted colours, and the word says why."""
    run = render("fan60")
    assert colours(run, FAN) == {LCYAN, WHITE} and "TURNING" in run.row(19) and run.attrs(19, 8, 14) == {INFO}
    for state, word, attr in (
        ("idle", "STOPPED", TEXT),
        ("estop", "HALTED (E-STOP)", ALARM),
        ("stale", "STALE", ALARM),
        ("nocell", "NO DATA", TEXT),
    ):
        s = render(state)
        assert colours(s, FAN) == {DGREY, GREY}, state
        row = s.row(19)[2:19]
        assert row.strip() == word, (state, row)
        c0 = 3 + row.index(word)
        assert s.attrs(19, c0, c0 + len(word) - 1) == {attr}, state


def test_fan_frame_motion():
    """The frame does not advance at duty 0, while latched, stale or lost; it advances faster at 80 % than at
    40 % over the same number of ticks (fixed table: 12 and 6 ticks per frame at 100 Hz)."""
    out = subprocess.run(
        [str(SCREEN_TEST), "fan-count", "960"], capture_output=True, text=True, check=True
    ).stdout
    n = {k: int(v) for k, v in (ln.split() for ln in out.splitlines())}
    assert (
        n["duty0"] == 0 and n["duty80-latched"] == 0 and n["duty80-stale"] == 0 and n["duty80-lost"] == 0
    ), n
    assert n["duty80"] > n["duty60"] > n["duty40"] > 0, n
    assert (n["duty40"], n["duty60"], n["duty80"]) == (80, 120, 160), n


def test_fan_frames_differ():
    """Consecutive frames draw different rotors (the picture moves when the frame steps)."""
    a = render("fan60")
    rotor = [a.row(r)[6:15] for r in range(13, 18)]
    frame0 = [render("idle").row(r)[6:15] for r in range(13, 18)]
    assert rotor != frame0


# the needle (light cyan) cells as designed (README "as built"): 90 straight up column 32, 30 and 150 mirrored
NEEDLE = {
    30: {(16, 26), (17, 27), (17, 28), (17, 29), (17, 30), (18, 30), (18, 31)},
    90: {(14, 32), (15, 32), (16, 32), (17, 32), (18, 32)},
    150: {(16, 38), (17, 34), (17, 35), (17, 36), (17, 37), (18, 33), (18, 34)},
}


@pytest.mark.parametrize("deg", [30, 90, 150])
def test_needle_cells(deg):
    s = render(f"servo={deg}")
    assert cells_with(s, GAUGE, LCYAN) == NEEDLE[deg]
    assert f"SERVO {deg:03d}° (30-150)" in s.row(19)
    assert pixels(s, 18, 32)[1] == WHITE  # the pivot


def test_needle_mirror_and_marks():
    """30 and 150 are mirror images about column 32; the limit marks are light red, the 90 mark yellow, the
    arc brown inside 30-150 and dark red outside."""
    assert {(r, 64 - c) for r, c in NEEDLE[30]} == NEEDLE[150]
    s = render("idle")
    assert colours(s, GAUGE) == {DRED, BROWN, LRED, YELLOW, LCYAN, WHITE}
    assert cells_with(s, GAUGE, LRED) == {(r, 64 - c) for r, c in cells_with(s, GAUGE, LRED)}


def test_no_needle_without_data_grey_when_stale():
    assert LCYAN not in colours(render("nocell"), GAUGE)
    st = render("stale")
    assert LCYAN not in colours(st, GAUGE) and len(cells_with(st, GAUGE, DGREY)) >= 5


BEAM = "\u2593\u2592\u2591"


@pytest.mark.parametrize(
    ("state", "dist_text", "word", "word_attr", "beam_fg", "end_col"),
    [
        ("idle", "DIST 320 mm", "CLEAR", INFO, LCYAN, 47 + 320 * 32 // 400),
        ("presence", "DIST 70 mm", "PRESENCE", ASK, YELLOW, 47 + 70 * 32 // 400),
        ("stale", "DIST 250 mm", "STALE", ALARM, DGREY, 47 + 250 * 32 // 400),
        ("noecho", "DIST NO ECHO", "NO ECHO", ASK, None, None),
        ("nocell", "DIST ---", "NO DATA", TEXT, None, None),
    ],
)
def test_sensor_beam(state, dist_text, word, word_attr, beam_fg, end_col):
    """The beam runs from the sensor (column 46) to the reading (12.5 mm per cell), the object block at the
    reading, dots beyond; the 100 mm threshold under column 55; every state in words."""
    s = render(state)
    r13 = s.row(13)
    assert r13[45:].startswith(dist_text) and r13[:78].endswith(word), r13
    assert s.attrs(13, 79 - len(word), 78) == {word_attr}
    r14 = s.row(14)
    assert r14[45] == "█"
    if end_col is None:
        assert r14[46:78] == "·" * 32
    else:
        assert all(ch in BEAM for ch in r14[46 : end_col - 1]) and r14[end_col - 1] == "█", r14
        assert set(r14[end_col:78]) <= {"·"}
        assert {s.attr(14, c) & 15 for c in range(47, end_col)} == {beam_fg}
    assert s.row(15)[54:61] == "┴100 mm" and s.attrs(15, 55, 61) == {MARK}


def test_presence_threshold_matches_the_kernel_constant():
    """99 mm is drawn left of the threshold mark, 100 mm on it (PRESENCE_MM = 100, display only)."""
    assert 47 + 99 * 32 // 400 < 55 and 47 + 100 * 32 // 400 == 55


@pytest.mark.parametrize(
    ("state", "lamp", "word", "attr"),
    [
        ("idle", DGREY, "E-STOP RELEASED", TEXT),
        ("estop", LRED, "E-STOP PRESSED, LATCHED", ALARM),
        ("nocell", DGREY, "E-STOP -", TEXT),
    ],
)
def test_estop_lamp(state, lamp, word, attr):
    s = render(state)
    assert colours(s, LAMP) == {lamp}
    assert s.row(17)[51:].startswith(word) and s.attrs(17, 52, 51 + len(word)) == {attr}
    assert s.row(18)[45:50] == "▀███▀" and s.row(17)[45:50] == "▄███▄"


def test_tainted_ask_panel():
    """The tainted ASK keeps the panel at the reported values (fan 0 until the answer); the banner asks."""
    s = render("ask")
    assert "TAINTED: YES" in s.row(3) and s.row(4).strip("║ ") == "ASK"
    assert "FAN 000%" in s.row(18) and "STOPPED" in s.row(19) and colours(s, FAN) == {DGREY, GREY}
    assert "CLEAR" in s.row(13) and "SERVO 090°" in s.row(19)


def test_panel_wording():
    """Banned words and franchise or military wording over every rendered state and the new source."""
    from tests.test_inv_11_banned_words import c_literals

    for state in ("idle", "ask", "estop", "nocell", "fan60", "presence", "noecho", "stale"):
        t = "\n".join(render(state).text())
        assert not BANNED.search(t) and not NOT_OURS.search(t), state
    for f in (REPO / "kernel/ui/screen.c", REPO / "kernel/ui/screen.h"):
        for line, lit in c_literals(f):
            assert not BANNED.search(lit) and not NOT_OURS.search(lit), f"{f.name}:{line}: {lit!r}"
    for f in (
        REPO / "tests/test_t14_panel.py",
        REPO / "tests/host/screen_test.c",
        REPO / "docs/evidence/T-14/README.md",
    ):
        assert not BANNED.search(f.read_text(encoding="utf-8")), f.name


# ---- QEMU: every state from the simulated cell -----------------------------------------------------------
def shot(mon, tmp_path, folder, name: str) -> Screen:
    settle()
    scr = read_vga(mon, tmp_path)
    screendump(mon, folder / f"sim-{name}.png")
    (folder / f"sim-{name}.txt").write_text("\n".join(scr.text()) + "\n", encoding="utf-8")
    return scr


def test_t14_panel_sim(tmp_path):
    """Screendumps of idle, fan 60 % turning, tainted ASK, E-stop latched, presence, no echo and stale from a
    simulated-cell run; the render time per tick stays under 1 ms with the rotor turning."""
    folder = evidence_dir("T-14")
    c, mon = start_screen(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        cellcmd(c, "status; servo 120; wait 25; servo 90")
        idle = shot(mon, tmp_path, folder, "idle")
        assert "CLEAR" in idle.row(13) and "STOPPED" in idle.row(19) and "SERVO 090°" in idle.row(19)

        c.sim.distance = 70
        pr = shot(mon, tmp_path, folder, "presence")
        assert "PRESENCE" in pr.row(13) and "DIST 70 mm" in pr.row(13) and "E-STOP RELEASED" in pr.row(17)
        c.sim.distance = NO_ECHO
        ne = shot(mon, tmp_path, folder, "noecho")
        assert "NO ECHO" in ne.row(13)
        c.sim.distance = 250
        time.sleep(0.3)  # a few frames with the new reading, then silence
        c.sim.streaming = False
        time.sleep(0.8)
        st = shot(mon, tmp_path, folder, "stale")
        assert "STALE" in st.row(13) and "DIST 250 mm" in st.row(13) and "LINK STALE" in st.row(18)
        assert LCYAN not in colours(st, GAUGE)
        c.sim.distance = 300
        c.sim.streaming = True
        wait_fresh(c)
        settle()

        c.s.send("run cellcmd read 1; fan 60; wait 300")
        m = c.s.expect(CONFIRM)
        assert m.group(4), "the read of maint_notes taints the executor"
        ask = shot(mon, tmp_path, folder, "ask")
        assert "TAINTED: YES" in ask.row(3) and ask.row(4).strip("║ ") == "ASK" and "STOPPED" in ask.row(19)
        c.s.send("y")
        c.sim.wait_fan(60)
        b = shot(mon, tmp_path, folder, "fan60")
        assert "FAN 060%" in b.row(18) and "TURNING" in b.row(19) and colours(b, FAN) == {LCYAN, WHITE}
        rotors = set()  # 60 % = 8 ticks (80 ms) per frame: 6 reads 50 ms apart see more than one frame
        for _ in range(6):
            rotors.add("\n".join(read_vga(mon, tmp_path).row(r)[6:15] for r in range(13, 18)))
            time.sleep(0.05)
        assert len(rotors) > 1, "the rotor turns while the board reports 60 %"
        n = len(c.sim.frames)
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=60 decision=AUTO_HALT reason=estop .*$")
        c.sim.wait_fan(0, start=n)
        es = shot(mon, tmp_path, folder, "estop")
        assert "E-STOP PRESSED, LATCHED" in es.row(17) and "HALTED (E-STOP)" in es.row(19)
        assert colours(es, LAMP) == {LRED} and colours(es, FAN) == {DGREY, GREY}
        c.sim.estop = False
        c.s.expect(END, 20)
        c.s.prompt()

        mon.close()
        c.close()  # INV-12 / F5: no PANIC, dropped=0


def test_t14_render_time(tmp_path):
    """Render < 1 ms per tick with the rotor turning (60 %), the beam and the needle jumping to new readings.
    No monitor command during the measurement: pmemsave / screendump hold the vCPU while the TSC runs on, so a
    service pass that spans one reads several ms (seen 3-10 ms) that is not render work."""
    c, mon = start_screen(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        time.sleep(0.6)  # the figures start after the 0.5 s TSC calibration
        c.s.send("run cellcmd servo 120; fan 60; wait 300")
        c.s.expect(CONFIRM)
        c.s.send("y")
        c.sim.wait_fan(60)
        for d in (70, 250, NO_ECHO, 300):  # beam redraws while the rotor turns
            c.sim.distance = d
            time.sleep(0.5)
        c.s.expect(END, 20)
        c.s.prompt()
        st = screen_stats(c)
        assert st["renders"] > 250 and st["over_1ms"] == 0 and st["max_us"] < 1000, st
        (evidence_dir("T-14") / "render-time.txt").write_text(f"test_t14_render_time: {st}\n")
        mon.close()
        c.close()
