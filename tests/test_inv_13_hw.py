"""T-04 step 7 / A3: the real work cell (hardware, user present, opt-in with RING3_HW=1; run with -s).

    RING3_HW=1 uv run pytest -s tests/test_inv_13_hw.py

The test image boots with the Uno as COM3 (RING3_CELL_PORT, default /dev/ttyACM0; opening it resets the
board, D-54). No model, no network. Each test prints what the user should do and waits; the measured stop
times come from the board's own telemetry timestamps (t_ms) on the kernel's tape. Transcripts go to
docs/evidence/T-04/hw-*.txt (RING3_EVIDENCE=1) or build/test-logs/T-04/.
"""

from __future__ import annotations

import os
import re
import time

import pytest

from tests.cellharness import CONSOLE_CONFIRM, cellcmd, decisions, results
from tests.qemu import EXIT_PASS, Run, Session, audit_lines, record_evidence

PORT = os.environ.get("RING3_CELL_PORT", "/dev/ttyACM0")
HW_QUOTAS = "taction=100 trequest=6000 tconfirm=3000"  # 30 s for the human at the console
F_ESTOP_LATCHED, F_PRESENCE_FAULT, F_LINK_LOST = 1 << 3, 1 << 4, 1 << 5
TAPE_TELE = r"^TAPE k=7 s=0 f=3 t=(\d+) nr=0 a0=(\d+) a1=(-?\d+) a2=(\d+) a3=(\d+)$"
AUTO_HALT = r"^AUDIT seq=\d+ tick=(\d+) sys=telemetry target=60 decision=AUTO_HALT reason={reason} .*$"

pytestmark = [
    pytest.mark.hw,
    pytest.mark.skipif(
        os.environ.get("RING3_HW") != "1", reason="hardware test: set RING3_HW=1 (board attached)"
    ),
    pytest.mark.skipif(not os.access(PORT, os.R_OK | os.W_OK), reason=f"{PORT} missing or not read/writable"),
]


def say(msg: str) -> None:
    print(f"\n>>> {msg}", flush=True)


def frames(text: str) -> list[dict[str, int]]:
    """Telemetry samples on the tape: tick, dist, estop, fan, servo, flags, seq, t_ms."""
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


class HwCell:
    """The test image with the board as COM3, synced on the board's boot."""

    def __init__(self) -> None:
        self.s = Session(append=HW_QUOTAS, com3=PORT, timeout=60)
        self.s.expect(r"^cell: COM3 present.*$")
        say("waiting for the board's boot (the port open resets it) ...")
        m = self.s.expect(r"^AUDIT seq=\d+ tick=(\d+) sys=telemetry target=0 decision=BOARD_BOOT .*$", 15)
        say(f"BOARD_BOOT at tick {m.group(1)}")
        self.s.prompt()
        time.sleep(0.5)
        self.status()

    def status(self) -> str:
        self.s.send("status")
        self.s.expect(r"^status: .*$")
        m = self.s.expect(r"^cell: last .*$")
        self.s.prompt()
        return m.group(0)

    def start_fan(self, script: str = "fan 60; wait 1500") -> None:
        say("keep the sensor clear; the fan starts at 60 % after you answer y on this console")
        self.s.send(f"run cellcmd {script}")
        self.s.expect(r"CONFIRM: cellcmd wants cell_fan\(60\)\. Allow\? \[y/N\] ", 30)
        self.s.send("y")
        self.s.expect(r"^cellcmd: fan 60 -> 0$", 10)

    def __enter__(self) -> HwCell:
        return self

    def __exit__(self, *exc: object) -> None:
        self.s.__exit__(*exc)


def test_inv_13_hw_interlock_stop():
    """Fan at 60 % -> a hand in front of the sensor -> AUTO_HALT; the fan stops; new motion is denied."""
    with HwCell() as hw:
        hw.start_fan()
        say("the fan runs: put your hand in front of the sensor (closer than 10 cm) within 10 s")
        m = hw.s.expect(AUTO_HALT.format(reason="presence"), 15)
        halt_tick = int(m.group(1))
        say("AUTO_HALT: remove your hand; the program ends by itself")
        hw.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 30)
        hw.s.prompt()
        t = hw.s.output
        fr = frames(t)
        hand = next(f for f in fr if f["dist"] < 100 and f["tick"] <= halt_tick)
        cut = next(f for f in fr if f["tick"] >= hand["tick"] and f["fan"] == 0)
        say(
            f"hand seen at t_ms {hand['t_ms']} (tick {hand['tick']}); AUTO_HALT at tick {halt_tick} "
            f"(+{(halt_tick - hand['tick']) * 10} ms); the board reported fan 0 at t_ms {cut['t_ms']} "
            f"(+{cut['t_ms'] - hand['t_ms']} ms, flags 0x{cut['flags']:02x})"
        )
        assert ("telemetry", "AUTO_HALT", "presence") in decisions(t)
        assert halt_tick - hand["tick"] <= 2  # within one tick of the frame that showed it
        assert cut["t_ms"] - hand["t_ms"] <= 150
        t2 = cellcmd(hw, "fan 60")  # the hand is gone: too soon after the halt (rate rule); nothing moves
        assert results(t2) == [("fan 60", -200)]
        run = hw.s.close()
    record_evidence("hw-interlock.txt", run, "T-04")
    assert run.exit_code == EXIT_PASS


def test_inv_13_estop_cuts_fan():
    """Fan at 60 % -> E-stop pressed -> the board cuts the fan in its interrupt, the kernel latches
    (DENY_ESTOP), cell_halt still allowed; release + `estop reset` + y clears it; motion is possible again."""
    with HwCell() as hw:
        hw.start_fan()
        say("the fan runs: press the E-stop within 10 s and keep it pressed")
        m = hw.s.expect(AUTO_HALT.format(reason="estop"), 15)
        halt_tick = int(m.group(1))
        hw.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 30)
        hw.s.prompt()
        fr = frames(hw.s.output)
        press = next(f for f in fr if f["estop"] == 1)
        cut = next(f for f in fr if f["tick"] >= press["tick"] and f["fan"] == 0)
        say(
            f"button seen at t_ms {press['t_ms']}; AUTO_HALT at tick {halt_tick} "
            f"(+{(halt_tick - press['tick']) * 10} ms); board fan 0 at t_ms {cut['t_ms']} "
            f"(+{cut['t_ms'] - press['t_ms']} ms, flags 0x{cut['flags']:02x})"
        )
        assert cut["flags"] & F_ESTOP_LATCHED
        t = cellcmd(hw, "fan 60; servo 100; halt")
        assert results(t) == [("fan 60", -200), ("servo 100", -200), ("halt", 0)]
        assert decisions(t, "cell_fan") == [("cell_fan", "DENY_ESTOP", "estop")]
        say("button still pressed: the reset must be refused; answer y to the next prompt")
        hw.s.send("estop reset")
        hw.s.expect(CONSOLE_CONFIRM, 30)
        hw.s.send("y")
        m = hw.s.expect(r"^estop reset: (.*)$", 10)
        say(f"estop reset while pressed -> {m.group(1)}")
        hw.s.prompt()
        assert m.group(1) == "DENY_ESTOP estop_button"
        say("release the E-stop now; the reset is asked again in 3 s: answer y")
        time.sleep(3.0)
        hw.s.send("estop reset")
        hw.s.expect(CONSOLE_CONFIRM, 30)
        hw.s.send("y")
        m = hw.s.expect(r"^estop reset: (.*)$", 10)
        say(f"estop reset released -> {m.group(1)}")
        hw.s.prompt()
        assert m.group(1) == "CONFIRM_YES estop_reset (ESTOP_RESET sent)"
        time.sleep(0.5)
        st = hw.status()
        assert re.search(r"flags=0x[0-9a-f]*[0-7]\b", st) or "flags=0x0 " in st, st  # board latch clear
        t = cellcmd(hw, "wait 110; fan 60; wait 100; halt", answers=["y"])
        assert results(t)[1] == ("fan 60", 0) and results(t)[3] == ("halt", 0)
        run = hw.s.close()
    record_evidence("hw-estop.txt", run, "T-04")
    assert run.exit_code == EXIT_PASS
    a = audit_lines(run.output)
    reasons = [x["reason"] for x in a if x["sys"] == "confirm" and x["target"] == "0"]
    assert reasons == ["estop_button", "estop_reset"]


def test_inv_13_watchdog_safe_state():
    """The kernel goes silent (test knob `cellmute`) with the fan running: the board's link watchdog stops
    the fan on its own within ~1.1 s, visible in its telemetry (LINK_LOST, fan 0); the fan does not restart
    when the link comes back (D-50 F2). Then QEMU is killed with the fan running: the user confirms by eye."""
    with HwCell() as hw:
        t = cellcmd(hw, "fan 60", answers=["y"])
        assert results(t) == [("fan 60", 0)]
        time.sleep(1.0)
        hw.s.send("cellmute 250")  # 2.5 s without any kernel frame, PINGs included
        hw.s.expect(r"^cellmute 250 ticks .*$")
        hw.s.prompt()
        time.sleep(3.5)
        st = hw.status()
        fr = frames(hw.s.output)
        lost = next((f for f in fr if f["flags"] & F_LINK_LOST and f["fan"] == 0), None)
        assert lost is not None, "the board never reported LINK_LOST with the fan off"
        last_running = [f for f in fr if f["tick"] < lost["tick"] and f["fan"] == 60][-1]
        gap = lost["t_ms"] - last_running["t_ms"]
        say(
            f"last frame with the fan running: t_ms {last_running['t_ms']}; LINK_LOST + fan 0 at t_ms "
            f"{lost['t_ms']}: the fan was off at most {gap} ms after that frame; status now: {st}"
        )
        assert gap <= 1500  # A3: within 1.5 s
        after = [f for f in fr if f["tick"] > lost["tick"]]
        assert all(f["fan"] == 0 for f in after), "the fan restarted after the link came back"
        assert "flags=0x0 " in st, st  # LINK_LOST cleared by the PINGs again, fan still 0
        t = cellcmd(hw, "halt")
        assert results(t) == [("halt", 0)]
        # QEMU killed while the fan runs: the board's own watchdog, observed by the user
        t = cellcmd(hw, "wait 110; fan 60", answers=["y"])
        assert results(t)[1] == ("fan 60", 0)
        say("QEMU is killed NOW with the fan running: watch the fan stop (about 1 s) and the 3 beeps")
        hw.s.proc.kill()
        hw.s.proc.wait()
        hw.s.exit_code = -9
        answer = input(">>> did the fan stop within about a second, with 3 beeps? [y/N] ").strip()
        text = hw.s.output + f"\n[user-reported after the QEMU kill: {answer}]\n"
    record_evidence("hw-watchdog.txt", Run(hw.s.cmd, text, -9, 0), "T-04")
    assert answer.lower() in ("y", "yes"), "the user did not see the fan stop after the QEMU kill"
