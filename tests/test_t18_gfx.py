"""T-18: the pixel graphics screen (boot option `gfx=1`, D-101).

Host golden-image tests of the pure renderer (kernel/ui/gfx.c built by `make host-gfx`, the 8x16 glyphs from
tests/fixtures/vga-font-8x16.hex: the bytes the kernel read from VGA plane 2, dumped once with the test
image's `gfxfont` command); the rotor motion rules; the damage rectangles. QEMU tests: the font the kernel
reads equals the fixture; a `gfx=1` boot with the simulated cell gives a 1024x768 screendump whose pictures
match the host render pixel for pixel; a ring-3 write to either window is PROC_FAULT (vector 14) with the
screen still drawn afterwards; the hazard -> FAN 0 latency under continuous redraw is no worse than text
mode's (D-105); without a
VGA device the kernel stays in text mode. PNGs of every state from the simulated cell go to
build/test-logs/T-18/ (docs/evidence/T-18/ with RING3_EVIDENCE=1). Text mode is untouched: every other test
boots without `gfx=1`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.agentbridge import AGENT_QUOTAS, BridgeThread, call, done, runs_dir
from tests.cellharness import CONFIRM, END, Cell, cellcmd, wait_fresh
from tests.qemu import EXIT_PASS, REPO, Session, evidence_dir
from tests.sim_cell import NO_ECHO, SimCell
from tests.test_t08_screen import Monitor, screen_stats, screendump, settle

GFX_TEST = REPO / "build" / "host" / "gfx_test"
FONT = REPO / "tests" / "fixtures" / "vga-font-8x16.hex"
W, H = 1024, 768

# sha256 of the raw frame (W x H x 4 bytes, B G R 0) per state; regenerate with `gfx_test <font> <state>`
# after a deliberate design change and list the change in the evidence README.
GOLDEN = {
    "idle": "3363ac2837f903b97d8cdc9a2877e54a5fb09d0a23630d710a62fb283892f72b",
    "ask": "de81a27732e2ed717cd6ba62517844ca59df2118832fcd942f81d8eb8bdf7422",
    "ask-wipe": "2dfa4afd918fda30d89cd2b050cb9874dd93e3e048165f19924710fe966c94fd",
    "estop": "2a95905700a612ce1e9b7ab54ff676ca3e488a0ffd8ec7f3499827d62b65d337",
    "nocell": "65970b3e43e9ff9017e9295107851ead496a6a95243f9e9dd6718b814ad6dc19",
    "fan60": "6f429e5edc4055e6e0628131f4a51bf956438add2c0832ef10681af0eab40edf",
    "presence": "26f42fd68f99482886345115cfd84866a7555695d8974cba7bd8b273ca538b5a",
    "noecho": "b1aed81ccc926f558ff9227f6c94ea8f6092c0d35228f855a2020e41191ca58e",
    "stale": "8ca1d3f89f55a4613cd478c220a657f17244dcf3ab911a7e7f591480e383a90b",
    "simboot": "658719a624fb0aa4c57c6094bd03c5fdf1048a1aaf530d239f031d96a0da1fa3",
}

# The renderer's regions (x, y, w, h), as kernel/ui/gfx.c lays them out.
R_TITLE = (16, 12, 384, 32)
R_FAN = (152 - 68, 440 - 68, 136, 136)
R_FAN_TEXT = (4, 556, 300, 72)
R_GAUGE = (496 - 170, 532 - 172, 380, 180)  # the face (chrome) and the needle
R_GAUGE_TEXT = (306, 556, 380, 32)
R_SENSOR = (690, 310, 330, 120)
R_LAMP = (690, 460, 330, 46)  # the lamp, the E-STOP and LINK words; the age line below changes every tick
R_LAMP_DISC = (700, 460, 50, 48)
R_TICKER = (4, 676, 1016, 80)
R_CONSOLE = (4, 210, 1016, 80)
FB_PHYS = 0xFD000000  # QEMU standard VGA BAR0 as the kernel reports it (user/test/fbpoke.c writes here)
BACK_PHYS = 0x01000000  # the back buffer window (user/test/bbpoke.c)


@pytest.fixture(scope="module", autouse=True)
def host_gfx():
    subprocess.run(["make", "-s", "host-gfx"], cwd=REPO, check=True)
    assert GFX_TEST.exists()


def crop(img: np.ndarray, r: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = r
    return img[y : y + h, x : x + w]


def render(state: str, *opts: str) -> tuple[bytes, np.ndarray]:
    """The raw frame and its BGR image (H x W x 3)."""
    out = subprocess.run([str(GFX_TEST), str(FONT), state, *opts], capture_output=True, check=True).stdout
    if any(o == "incremental" or o.startswith("budget=") for o in opts):
        out = out[out.index(b"end\n") + 4 :]
    assert len(out) == W * H * 4, len(out)
    return out, np.frombuffer(out, dtype=np.uint8).reshape(H, W, 4)[:, :, :3].copy()


def rects(state: str) -> list[tuple[int, int, int, int]]:
    out = subprocess.run([str(GFX_TEST), str(FONT), state, "incremental"], capture_output=True, check=True)
    head = out.stdout[: out.stdout.index(b"end\n")].decode()
    return [tuple(int(v) for v in ln.split()[1:]) for ln in head.splitlines() if ln.startswith("rect ")]


def write_png(folder: Path, name: str, img: np.ndarray) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(folder / name), img)


# ---- host: golden images, motion, damage ----------------------------------------------------------------
@pytest.mark.parametrize("state", sorted(GOLDEN))
def test_golden_image(state):
    raw, img = render(state)
    write_png(evidence_dir("T-18"), f"host-{state}.png", img)
    assert hashlib.sha256(raw).hexdigest() == GOLDEN[state], (
        f"{state}: the frame changed; see host-{state}.png"
    )


def test_golden_images_differ():
    assert len(set(GOLDEN.values())) == len(GOLDEN)


def fan_count(ticks: int) -> dict[str, tuple[int, int]]:
    out = subprocess.run([str(GFX_TEST), str(FONT), "fan-count", str(ticks)], capture_output=True, check=True)
    return {
        ln.split()[0]: (int(ln.split()[1]), int(ln.split()[2])) for ln in out.stdout.decode().splitlines()
    }


def test_fan_motion_rules():
    """No rotation at duty 0, latched, stale or lost; every tick turns it while the board reports a duty;
    80 % turns further than 40 % over the same ticks (degrees summed over 200 ticks)."""
    n = fan_count(200)
    for frozen in ("duty0", "duty80-latched", "duty80-stale", "duty80-lost"):
        assert n[frozen] == (0, 0), (frozen, n[frozen])
    assert n["duty40"][0] == 200 and n["duty60"][0] == 200 and n["duty80"][0] == 200, n
    assert n["duty80"][1] > n["duty60"][1] > n["duty40"][1] > 0, n


def test_fan_turns_only_inside_its_box():
    """Two consecutive ticks at 60 %: the rotor picture differs, nothing outside the fan box changes."""
    _, a = render("fan60", "tick=15016")
    _, b = render("fan60", "tick=15017")
    assert not np.array_equal(crop(a, R_FAN), crop(b, R_FAN))
    a2, b2 = a.copy(), b.copy()
    x, y, w, h = R_FAN
    a2[y : y + h, x : x + w] = 0
    b2[y : y + h, x : x + w] = 0
    assert np.array_equal(a2, b2)


def test_frozen_rotor_is_the_rest_picture():
    """E-stop latched: the rotor stands at the rest angle in the halted colours; no tick turns it."""
    _, estop = render("estop")
    _, estop2 = render("estop", "tick=19250")
    assert np.array_equal(crop(estop, R_FAN), crop(estop2, R_FAN))
    _, idle = render("idle")
    assert np.array_equal(crop(estop, R_FAN), crop(idle, R_FAN))  # the same grey rest picture as duty 0


@pytest.mark.parametrize(
    "state", ["ask", "estop", "fan60", "presence", "noecho", "stale", "nocell", "simboot"]
)
def test_incremental_draw_equals_full_draw(state):
    """Drawing `state` over `idle` through the damage path gives the same pixels as a full draw, and every
    pixel that differs from `idle` lies inside a reported rectangle (the driver copies only those)."""
    _, full = render(state)
    _, inc = render(state, "incremental")
    assert np.array_equal(full, inc), state
    _, idle = render("idle")
    diff = np.any(full != idle, axis=2)
    covered = np.zeros_like(diff)
    for x, y, w, h in rects(state):
        covered[y : y + h, x : x + w] = True
    missed = np.argwhere(diff & ~covered)
    assert len(missed) == 0, (
        f"{state}: {len(missed)} changed pixels outside the damage rectangles, first {missed[:5]}"
    )


def calls(state: str, budget: int) -> tuple[int, list[tuple[int, int, int, int]]]:
    out = subprocess.run(
        [str(GFX_TEST), str(FONT), state, f"budget={budget}"], capture_output=True, check=True
    ).stdout
    head = out[: out.index(b"end\n")].decode()
    rs = [tuple(int(v) for v in ln.split()[1:]) for ln in head.splitlines() if ln.startswith("rect ")]
    return head.count("call\n") + 1, rs


@pytest.mark.parametrize("state", ["ask", "estop", "fan60", "stale"])
def test_budgeted_draw_converges(state):
    """With the kernel's per-tick budget a big change (a decision: banner, console, ticker, status) takes
    several calls; the frame after the last call equals a full draw and the rectangles cover every change."""
    n, rs = calls(state, 66000)
    assert n >= 2, f"{state}: a decision's redraw must not fit one budget ({n} calls)"
    assert calls(state, 0)[0] == 1
    _, full = render(state)
    _, budgeted = render(state, "budget=66000")
    assert np.array_equal(full, budgeted), state
    _, idle = render("idle")
    diff = np.any(full != idle, axis=2)
    covered = np.zeros_like(diff)
    for x, y, w, h in rs:
        covered[y : y + h, x : x + w] = True
    assert not np.any(diff & ~covered), state


def test_every_state_has_its_word_in_the_source():
    """Pixels can't be read back as text; the words are the text screen's (words.c, one vocabulary) and the
    state words are literal in the renderer. The banned-words test (INV-11) scans the same file."""
    src = (REPO / "kernel" / "ui" / "gfx.c").read_text(encoding="utf-8")
    for w in (
        "NO DATA",
        "HALTED (E-STOP)",
        "STALE",
        "HALTED (LINK)",
        "STOPPED",
        "TURNING",
        "PRESENCE",
        "CLEAR",
        "NO ECHO",
        "E-STOP PRESSED, LATCHED",
        "E-STOP RELEASED",
        "LINK ok",
        "LINK STALE",
        "TELEMETRY AGE",
        "TAINTED: YES",
        "EXEC KILLED REQ",
    ):
        assert f'"{w}' in src, w
    assert "ui_verdict_word" in src and "ui_reason_words" in src


# ---- QEMU -----------------------------------------------------------------------------------------------
def start_gfx(tmp_path: Path, quotas: str, com2: bool = False) -> tuple[Cell, Monitor, str]:
    """The test image booted with gfx=1, the simulated cell on COM3, a monitor socket; returns the kernel's
    `gfx:` boot line too. `com2`: a bridge link too (the scripted model of tests/agentbridge.py)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sock = tmp_path / "mon.sock"
    s = Session(
        append=f"{quotas} gfx=1",
        com2=com2,
        com3=True,
        timeout=90.0,
        monitor=f"unix:{sock},server=on,wait=off",
    )
    m = s.expect(r"^gfx: .*$")
    s.expect(r"^cell: COM3 present.*$")
    sim = SimCell(s.com3_port).start()
    s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=\d+ decision=BOARD_BOOT reason=board_boot .*$")
    s.prompt()
    c = Cell(s, sim)
    wait_fresh(c)
    return c, Monitor(sock), m.group(0)


def start_cell_screen(tmp_path: Path, append: str, com2: bool = False) -> Cell:
    """The test image with the given options (text-mode `screen` for the latency comparison), sim on COM3."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = Session(append=append, com2=com2, com3=True, timeout=90.0)
    s.expect(r"^cell: COM3 present.*$")
    sim = SimCell(s.com3_port).start()
    s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=\d+ decision=BOARD_BOOT reason=board_boot .*$")
    s.prompt()
    c = Cell(s, sim)
    wait_fresh(c)
    return c


def shot(mon: Monitor, folder: Path, name: str) -> np.ndarray:
    settle()
    png = folder / f"sim-{name}.png"
    screendump(mon, png)
    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
    assert img is not None and img.shape == (H, W, 3), png
    return img


def same(a: np.ndarray, b: np.ndarray, r: tuple[int, int, int, int]) -> bool:
    return np.array_equal(crop(a, r), crop(b, r))


def test_t18_font_fixture_is_what_the_kernel_reads():
    with Session(append="gfx=1 testexit") as s:
        s.expect(r"^gfx: 1024x768x32 linear framebuffer phys=0x[0-9a-f]{8} .*font from VGA plane 2.*$")
        s.prompt()
        s.send("gfxfont")
        s.expect(r"^F ff [0-9a-f]{32}$")
        lines = [ln for ln in s.output.splitlines() if re.match(r"^F [0-9a-f]{2} [0-9a-f]{32}$", ln)]
        run = s.close()
    assert run.exit_code == EXIT_PASS
    assert lines == FONT.read_text(encoding="utf-8").splitlines(), "the fixture is not the plane-2 font"


def test_t18_gfx_screendump_and_ring3_fault(tmp_path):
    """A2/A3: the screendump of a gfx=1 boot with the simulated cell matches the host render of `simboot`
    in every picture (fan at rest, dial at 90, beam at 300 mm, lamp off) and the title; a ring-3 write to the
    framebuffer window and to the back buffer window each give
    PROC_FAULT vector 14 at that address with 0 PANIC, and the screen is still drawn afterwards."""
    folder = evidence_dir("T-18")
    c, mon, gfx_line = start_gfx(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        m = re.match(
            r"gfx: 1024x768x32 linear framebuffer phys=0x([0-9a-f]{8}) .*back buffer phys=0x([0-9a-f]{8})",
            gfx_line,
        )
        assert m, gfx_line
        assert int(m.group(1), 16) == FB_PHYS and int(m.group(2), 16) == BACK_PHYS, gfx_line
        time.sleep(0.6)  # figures start after the 0.5 s TSC calibration
        settle()
        st = screen_stats(c)  # before any monitor command (a screendump holds the vCPU mid-tick)
        assert st["gfx"] == 1 and st["renders"] > 50 and st["avg_us"] < 1000, st
        (folder / "render-time-boot.txt").write_text(f"test_t18_gfx_screendump_and_ring3_fault: {st}\n")
        _, host = render("simboot")
        img = shot(mon, folder, "boot")
        for name, r in (
            ("title", R_TITLE),
            ("fan", R_FAN),
            ("fan text", R_FAN_TEXT),
            ("gauge", R_GAUGE),
            ("gauge text", R_GAUGE_TEXT),
            ("sensor", R_SENSOR),
            ("lamp", R_LAMP),
        ):
            assert same(img, host, r), f"{name} region differs from the host render (sim-boot.png)"
        equal = float(np.mean(np.all(img == host, axis=2)))
        (folder / "pixel-match-boot.txt").write_text(
            f"pixels equal to the host simboot render: {equal:.4f}\n"
        )
        assert equal > 0.9, equal

        for prog, addr in (("fbpoke", FB_PHYS), ("bbpoke", BACK_PHYS)):
            t = c.s.run_prog(prog)
            f = re.search(
                r"^PROC_FAULT pid=(\d+) vector=(\d+) eip=0x4[0-9a-f]{7} err=0x([0-9a-f]+) cs=0x\w+ "
                r"cr2=0x([0-9a-f]{8})",
                t,
                re.M,
            )
            assert f and int(f.group(2)) == 14, f"{prog}: expected #PF:\n{t}"
            assert int(f.group(4), 16) == addr, f"{prog}: cr2 {f.group(4)} is not the window:\n{t}"
            assert int(f.group(3), 16) & 0x4, f"{prog}: the error code must say user mode:\n{t}"
            assert "STILL ALIVE" not in t and re.search(r"^END pid=\d+ FAULT ", t, re.M), t
            t = c.s.run_prog("ticks")
            assert "code=0" in t
        settle()
        after = shot(mon, folder, "after-fault")
        for name, r in (("fan", R_FAN), ("gauge", R_GAUGE), ("sensor", R_SENSOR), ("lamp", R_LAMP)):
            assert same(after, host, r), f"{name} region differs after the faults (sim-after-fault.png)"
        assert not same(after, img, R_CONSOLE), "the console window shows the fault lines"
        mon.close()
        run = c.close()  # INV-12 / F5: no PANIC, dropped=0
    assert run.exit_code == EXIT_PASS and not run.has_panic()


def percentile(hist: list[int], step: int, q: float) -> int:
    """The upper edge (µs) of the bucket holding the q-quantile of the pass durations."""
    total = sum(hist)
    seen = 0
    for i, n in enumerate(hist):
        seen += n
        if seen >= q * total:
            return (i + 1) * step
    return len(hist) * step


def screen_hist(c: Cell) -> tuple[dict[str, int], list[int], int]:
    """`screen`: the figures, the histogram and its bucket width."""
    c.s.send("screen")
    m = c.s.expect(r"^screen: (renders=.*)$")
    st = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", m.group(1))}
    h = c.s.expect(r"^screen-hist: step_us=(\d+) ([0-9,]+)$")
    c.s.prompt()
    return st, [int(v) for v in h.group(2).split(",")], int(h.group(1))


def halt_latency_trials(c: Cell, n: int) -> list[tuple[float, int]]:
    """n times: fan 60 % confirmed and running (the rotor turning: a redraw every tick with gfx=1), then the
    hand at 50 mm in one STATE frame sent at once; returns (host ms from that frame to the board's FAN 0,
    kernel ticks from the frame's acceptance to the AUTO_HALT record) per trial."""
    out = []
    for _ in range(n):
        c.s.send("run cellcmd fan 60; wait 300")
        c.s.expect(CONFIRM)
        c.s.send("y")
        c.sim.wait_fan(60)
        time.sleep(0.3)
        k = len(c.sim.frames)
        pos = len(c.s.output)
        c.sim.distance = 50
        t0 = time.monotonic()
        c.sim.emit_state()
        halt = c.sim.wait_fan(0, timeout=2.0, start=k)
        ms = (halt.t - t0) * 1000
        m = c.s.expect(
            r"^AUDIT seq=\d+ tick=(\d+) sys=telemetry target=\d+ decision=AUTO_HALT reason=presence .*$"
        )
        t_halt = int(m.group(1))
        rx = re.findall(r"^TAPE k=7 s=\d+ f=\d+ t=(\d+) nr=\d+ a0=50 ", c.s.output[pos:], re.M)
        assert rx, "the hazard frame's tape record"
        out.append((ms, t_halt - int(rx[0])))
        c.s.expect(END, 20)
        c.s.prompt()
        c.sim.distance = 300
        wait_fresh(c)
        time.sleep(1.1)  # FAN_MIN_GAP: 1 s between fan increases
    return out


def halt_while_waiting_for_the_model(c: Cell, tmp_path: Path, tag: str) -> tuple[float, int]:
    """test_inv_16_halt's scenario: agentsh blocked in agent_recv for a slow model (the kernel's wait loop
    there runs with interrupts off between its sti/hlt, so a render inside it is not preempted), the fan at
    60 % (rotor turning), then the hand in one STATE frame. Returns (host ms to FAN 0, kernel ticks from the
    frame's acceptance to AUTO_HALT)."""

    def slow_halt() -> str:
        time.sleep(2.0)
        return call("halt")

    replies = [call("fan_set", duty=60), slow_halt, done("fan ran at 60 for a moment, then halted")]
    b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, tag), replies).start()
    c.s.send("spin the fan at 60 % for a moment, then halt")
    c.s.expect(r"CONFIRM: agentsh wants cell_fan\(60\)\. Allow\? \[y/N\] ")
    c.s.send("y")
    c.sim.wait_fan(60)
    k = len(c.sim.frames)
    time.sleep(0.5)  # the executor is now waiting in agent_recv; with gfx=1 the rotor redraws every tick
    pos = len(c.s.output)
    c.sim.distance = 50
    t0 = time.monotonic()
    c.sim.emit_state()
    halt = c.sim.wait_fan(0, timeout=2.0, start=k)
    ms = (halt.t - t0) * 1000
    m = c.s.expect(
        r"^AUDIT seq=\d+ tick=(\d+) sys=telemetry target=\d+ decision=AUTO_HALT reason=presence .*$"
    )
    rx = re.findall(r"^TAPE k=7 s=\d+ f=\d+ t=(\d+) nr=\d+ a0=50 ", c.s.output[pos:], re.M)
    assert rx, "the hazard frame's tape record"
    c.s.expect(END, 40)
    b.join()
    c.s.prompt()
    c.sim.distance = 300
    wait_fresh(c)
    return ms, int(m.group(1)) - int(rx[0])


def test_t18_halt_latency_under_redraw(tmp_path):
    """D-105 (b): the hazard -> AUTO_HALT -> FAN 0 latency with the pixel screen redrawing every tick (rotor
    at 60 %) is no worse than text mode's: the same trials in a text-mode session first, then in a gfx=1
    session, with the executor running in ring 3 (the render then runs at the tail of the tick handler) and
    once with it blocked in agent_recv (the render runs in that wait loop with interrupts off); every trial
    stays inside the bound text mode is tested against (test_inv_16_halt: FAN 0 within 250 ms = 25 ticks of
    the hand) and the kernel-internal gap (hazard frame accepted -> AUTO_HALT) is 0 ticks in both modes; the
    host-side medians are reported side by side (both carry the host's socket and scheduling jitter, 1-50 ms
    in either mode). D-105 (c): the render average, p99 and max of the gfx session as measured, the per-tick
    budget kept."""
    folder = evidence_dir("T-18")
    text = start_cell_screen(tmp_path / "text", f"{AGENT_QUOTAS} screen", com2=True)
    with text:
        t_trials = halt_latency_trials(text, 6)
        t_wait = halt_while_waiting_for_the_model(text, tmp_path, "text")
        t_st, _, _ = screen_hist(text)
        text.close()
    c, mon, _ = start_gfx(tmp_path / "gfx", AGENT_QUOTAS, com2=True)
    with c:
        time.sleep(0.6)
        g_trials = halt_latency_trials(c, 6)
        g_wait = halt_while_waiting_for_the_model(c, tmp_path, "gfx")
        st, hist, step = screen_hist(c)
        mon.close()
        c.close()
    t_ms = sorted(ms for ms, _ in t_trials)
    g_ms = sorted(ms for ms, _ in g_trials)
    ticks = [int(-(-ms // 10)) for ms in g_ms]
    med = lambda v: v[len(v) // 2]  # noqa: E731
    p99 = percentile(hist, step, 0.99)
    lines = [
        "hazard (hand at 50 mm, one STATE frame) -> AUTO_HALT -> FAN 0 on COM3, host ms per trial:",
        f"  text mode (screen): {[round(v, 1) for v in t_ms]}  median {med(t_ms):.1f} ms,"
        f" max {t_ms[-1]:.1f} ms",
        f"  gfx=1 rotor at 60 %: {[round(v, 1) for v in g_ms]}  median {med(g_ms):.1f} ms,"
        f" max {g_ms[-1]:.1f} ms = {ticks[-1]} ticks (bound 25)",
        f"  kernel ticks from the frame's acceptance to AUTO_HALT: text {[t for _, t in t_trials]},"
        f" gfx {[t for _, t in g_trials]}",
        "the same hazard while agentsh waits in agent_recv for a slow model (render with interrupts off):"
        f" text {t_wait[0]:.1f} ms / {t_wait[1]} ticks internal, gfx {g_wait[0]:.1f} ms"
        f" = {int(-(-g_wait[0] // 10))} ticks / {g_wait[1]} ticks internal (bound 25)",
        f"render (gfx session, whole service pass): renders={st['renders']} avg_us={st['avg_us']}"
        f" p99_us<={p99} max_us={st['max_us']} max_gross_us={st['max_gross_us']} over_1ms={st['over_1ms']}"
        f" over_2ms={st['over_2ms']} max_px_copied={st['max_cells_changed']}",
        f"  histogram ({step} us buckets, last = 4 ms and over): {hist}",
        f"  text session for comparison: avg_us={t_st['avg_us']} max_us={t_st['max_us']}",
    ]
    (folder / "latency.txt").write_text("\n".join(lines) + "\n")
    (folder / "render-time.txt").write_text(lines[5] + "\n" + lines[6] + "\n")
    assert all(t == 0 for _, t in t_trials) and all(t == 0 for _, t in g_trials), lines[3]
    assert ticks[-1] <= 25, lines[2]
    assert g_wait[0] < 250 and g_wait[1] == 0 and t_wait[1] == 0, lines[4]
    assert st["gfx"] == 1 and st["renders"] > 300 and st["avg_us"] < 1000, st


def test_t18_fallback_without_vga_device():
    """A3: gfx=1 with no VGA device at all: the kernel says so, stays in text mode, boots to the prompt."""
    with Session(append="gfx=1 testexit", extra=["-vga", "none"]) as s:
        s.expect(r"^gfx: no QEMU standard VGA \(PCI 1234:1111\) on bus 0: text mode$")
        s.prompt()
        s.send("screen")
        m = s.expect(r"^screen: renders=\d+ .* gfx=(\d) over_2ms=\d+ max_gross_us=\d+$")
        assert m.group(1) == "0"
        run = s.close()
    assert run.exit_code == EXIT_PASS and not run.has_panic()


def test_t18_states_sim(tmp_path):
    """A4: PNGs of every state from the simulated cell: idle, presence, no echo, stale, tainted ASK, fan 60 %
    turning, E-stop latched. Each picture that the state fixes is checked against the host render."""
    folder = evidence_dir("T-18")
    c, mon, _ = start_gfx(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        cellcmd(c, "status; servo 120; wait 25; servo 90")
        idle = shot(mon, folder, "idle")
        _, h_boot = render("simboot")
        assert same(idle, h_boot, R_GAUGE) and same(idle, h_boot, R_FAN) and same(idle, h_boot, R_SENSOR)

        c.sim.distance = 70
        pr = shot(mon, folder, "presence")
        _, h_pr = render("presence")
        assert same(pr, h_pr, R_SENSOR), "beam + PRESENCE at 70 mm"
        c.sim.distance = NO_ECHO
        ne = shot(mon, folder, "noecho")
        _, h_ne = render("noecho")
        assert same(ne, h_ne, R_SENSOR), "empty beam + NO ECHO"
        c.sim.distance = 250
        time.sleep(0.3)
        c.sim.streaming = False
        time.sleep(0.8)
        st = shot(mon, folder, "stale")
        _, h_st = render("stale")
        assert same(st, h_st, R_SENSOR), "grey beam at 250 mm + STALE"
        c.sim.distance = 300
        c.sim.streaming = True
        wait_fresh(c)
        settle()

        c.s.send("run cellcmd read 1; fan 60; wait 300")
        m = c.s.expect(CONFIRM)
        assert m.group(4), "the read of maint_notes taints the executor"
        ask = shot(mon, folder, "ask")
        _, h_ask = render("ask")
        assert same(ask, h_ask, R_FAN) and same(ask, h_ask, R_FAN_TEXT), "fan at rest, STOPPED"
        c.s.send("y")
        c.sim.wait_fan(60)
        settle()
        turning = [render("fan60", f"tick={t}")[1] for t in range(24)]  # 60 % = 5 deg/tick: 24 pictures
        frames = []
        for i in range(4):
            png = folder / ("sim-fan60.png" if i == 0 else f"sim-fan60-{i}.png")
            screendump(mon, png)
            frames.append(cv2.imread(str(png), cv2.IMREAD_COLOR))
            time.sleep(0.05)
        for i, f in enumerate(frames):
            assert any(same(f, t, R_FAN) for t in turning), (
                f"rotor picture {i} is not one of the 24 host pictures"
            )
            assert same(f, turning[0], R_FAN_TEXT), "FAN 060% TURNING"
        assert len({crop(f, R_FAN).tobytes() for f in frames}) > 1, (
            "the rotor turns while the board reports 60 %"
        )
        n = len(c.sim.frames)
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=60 decision=AUTO_HALT reason=estop .*$")
        c.sim.wait_fan(0, start=n)
        es = shot(mon, folder, "estop")
        _, h_es = render("estop")
        assert same(es, h_es, R_LAMP_DISC), "lit lamp"
        assert same(es, h_es, R_FAN) and same(es, h_es, R_FAN_TEXT), "rotor at rest, HALTED (E-STOP)"
        c.sim.estop = False
        c.s.expect(END, 20)
        c.s.prompt()
        mon.close()
        c.close()
