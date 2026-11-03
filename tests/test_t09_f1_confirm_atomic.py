"""T-09 F1 (Codex Gate A, D-74): the confirmation's final decision and the COM3 command it allows are one
step. An E-stop STATE that reaches the kernel between the operator's `y` and the FAN frame is decided after
the command (FAN 60 on the wire, then the latch's FAN 0), never in between (the latch's FAN 0 and then a
stale FAN 60 on a decision made before the latch). One that arrives before the answer makes the answer's
decision DENY_ESTOP and nothing moves. Deterministic through the test image's `confirmgap` seam: after the
final decision the kernel prints a marker and holds, interrupts exactly as the code left them, until a COM3
byte is waiting (or the latch is set), and only then sends. On the code before T-09 the frame is taken in
that hold and the board sees FAN 0 then FAN 60."""

import time

from tests.cellharness import CONFIRM, END, cellcmd, decisions, estop_reset, results, start, wait_fresh
from tests.qemu import EXIT_PASS, audit_lines, record_evidence
from tests.sim_cell import T_FAN, T_SERVO


def test_t09_f1_estop_between_answer_and_command():
    with start() as c:
        c.s.send("confirmgap on")
        c.s.expect(r"^confirm gap on$")
        c.s.prompt()
        # ---- the frame lands in the gap: the command decided before it goes out first, the latch halts after
        c.s.send("run cellcmd fan 60; wait 30")
        start_pos = c.s.pos
        m = c.s.expect(CONFIRM)
        assert m.group(2) == "cell_fan"
        n = len(c.sim.frames)
        c.sim.streaming = False  # from here on the only COM3 frame is the E-stop one
        c.s.send("y")
        c.s.expect(r"^confirmgap: waiting for COM3$")  # the answer is decided; the command is not out yet
        c.sim.estop = True
        c.sim.emit_state()  # one STATE with estop=1, written while the kernel holds
        c.s.expect(END)
        t = c.s.output[start_pos : c.s.pos]
        c.s.prompt()
        time.sleep(0.2)
        assert results(t) == [("fan 60", 0), ("wait 30", 0)], results(t)
        d = decisions(t)
        assert ("confirm", "CONFIRM_YES", "first_fan") in d and ("telemetry", "AUTO_HALT", "estop") in d, d
        assert d.index(("confirm", "CONFIRM_YES", "first_fan")) < d.index(("telemetry", "AUTO_HALT", "estop"))
        fans = c.sim.fan_commands(n)
        assert fans == [60, 0], f"board saw FAN {fans}: a FAN > 0 after the latch's FAN 0 is the F1 race"
        assert not any(f.type == T_SERVO for f in c.sim.frames_after(n))
        c.s.send("status")
        c.s.expect(r"^status: fan=0 servo=90 estop_latched=1 .*$")
        c.s.prompt()
        # ---- the frame lands before the answer: the answer's decision is the denial, nothing is sent
        c.s.send("confirmgap off")
        c.s.expect(r"^confirm gap off$")
        c.s.prompt()
        c.sim.estop = False
        c.sim.streaming = True
        time.sleep(0.3)
        wait_fresh(c)
        assert estop_reset(c, "y") == "CONFIRM_YES estop_reset (ESTOP_RESET sent)"
        t = cellcmd(c, "wait 110")  # the fan rate gap since the halt
        c.s.send("run cellcmd fan 60; wait 30")
        start_pos = c.s.pos
        c.s.expect(CONFIRM)
        n = len(c.sim.frames)
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=0 decision=AUTO_HALT reason=estop .*$")
        c.s.send("y")
        c.s.expect(END)
        t = c.s.output[start_pos : c.s.pos]
        c.s.prompt()
        time.sleep(0.2)
        assert results(t) == [("fan 60", -200), ("wait 30", 0)], results(t)
        assert decisions(t, "confirm") == [("confirm", "DENY_ESTOP", "estop")], decisions(t)
        assert c.sim.fan_commands(n) == [0], c.sim.fan_commands(n)  # the latch's FAN 0 only
        assert not any(f.type in (T_FAN, T_SERVO) and f.body[0] for f in c.sim.frames_after(n))
        yes = [x for x in audit_lines(c.s.output) if x["sys"] == "confirm" and x["decision"] == "CONFIRM_YES"]
        assert [x["target"] for x in yes] == ["60", "0"]  # the fan once (phase 1), the reset once
        run = c.close()
        record_evidence("f1-confirm-atomic.txt", run, "T-09")
        assert run.exit_code == EXIT_PASS
