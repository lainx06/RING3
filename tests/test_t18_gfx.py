"""T-18: the pixel graphics screen (boot option `gfx=1`, D-101).

Host golden-image tests of the pure renderer (kernel/ui/gfx.c built by `make host-gfx`, the 8x16 glyphs from
tests/fixtures/vga-font-8x16.hex: the bytes the kernel read from VGA plane 2, dumped once with the test
image's `gfxfont` command); the rotor motion rules; the damage rectangles. QEMU tests: the font the kernel
reads equals the fixture; a `gfx=1` boot with the simulated cell gives a 1024x768 screendump whose pictures
match the host render pixel for pixel; a ring-3 write to either window is PROC_FAULT (vector 14) with the
screen still drawn afterwards; the render time stays under 2 ms per tick with the rotor turning; without a
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
def start_gfx(tmp_path: Path, quotas: str) -> tuple[Cell, Monitor, str]:
    """The test image booted with gfx=1, the simulated cell on COM3, a monitor socket; returns the kernel's
    `gfx:` boot line too."""
    sock = tmp_path / "mon.sock"
    s = Session(append=f"{quotas} gfx=1", com3=True, timeout=60.0, monitor=f"unix:{sock},server=on,wait=off")
    m = s.expect(r"^gfx: .*$")
    s.expect(r"^cell: COM3 present.*$")
    sim = SimCell(s.com3_port).start()
    s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=\d+ decision=BOARD_BOOT reason=board_boot .*$")
    s.prompt()
    c = Cell(s, sim)
    wait_fresh(c)
    return c, Monitor(sock), m.group(0)


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
    in every picture (fan at rest, dial at 90, beam at 300 mm, lamp off) and the title; the measured render
    time stays under 2 ms; a ring-3 write to the framebuffer window and to the back buffer window each give
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
        assert st["gfx"] == 1 and st["renders"] > 50 and st["over_2ms"] == 0 and st["max_us"] < 2000, st
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


def test_t18_render_time(tmp_path):
    """A2: render <= 2 ms per tick worst case with the rotor turning at 60 %, the beam and the needle jumping.
    No monitor command during the measurement (T-14: a screendump holds the vCPU while the TSC runs on)."""
    c, mon, _ = start_gfx(tmp_path, "taction=500 trequest=3000 tconfirm=1500")
    with c:
        time.sleep(0.6)
        c.s.send("run cellcmd servo 120; fan 60; wait 300")
        c.s.expect(CONFIRM)
        c.s.send("y")
        c.sim.wait_fan(60)
        for d in (70, 250, NO_ECHO, 300):
            c.sim.distance = d
            time.sleep(0.5)
        c.s.expect(END, 20)
        c.s.prompt()
        st = screen_stats(c)
        assert st["gfx"] == 1 and st["renders"] > 250 and st["over_2ms"] == 0 and st["max_us"] < 2000, st
        (evidence_dir("T-18") / "render-time.txt").write_text(f"test_t18_render_time: {st}\n")
        mon.close()
        c.close()


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
