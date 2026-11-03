"""INV-13 E-stop latch (simulated device): the button latches and halts; motion is DENY_ESTOP; cell_halt
still works; neither ring 3 nor the bridge can clear it (no syscall exists); the console reset is confirmed
and goes through only with the button released and fresh telemetry; ESTOP_RESET reaches the board only
then."""

import time

from tests.cellharness import cellcmd, decisions, estop_reset, results, start
from tests.qemu import EXIT_PASS, audit_lines, record_evidence
from tests.sim_cell import T_ESTOP_RESET


def test_inv_13_estop_latch():
    with start() as c:
        t = cellcmd(c, "fan 60; wait 20", answers=["y"])
        assert results(t)[0] == ("fan 60", 0)
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=60 decision=AUTO_HALT reason=estop .*$")
        c.sim.wait_fan(0, start=n)
        assert c.sim.alarm == 2 and c.sim.lcd == {0: "AUTO HALT fan 60", 1: "E-STOP latched"}
        c.s.prompt()
        t = cellcmd(c, "fan 60; servo 100; halt; fan 0; sys 13; sys 7; status")
        assert results(t)[:6] == [
            ("fan 60", -200), ("servo 100", -200), ("halt", 0), ("fan 0", 0), ("sys 13", -38),
            ("sys 7", -200),
        ]  # fmt: skip
        assert decisions(t) == [
            ("cell_fan", "DENY_ESTOP", "estop"),
            ("cell_servo", "DENY_ESTOP", "estop"),
            ("cell_halt", "ALLOW", "halt"),
            ("cell_fan", "ALLOW", "none"),
            ("unknown", "DENY_RULE", "nosys"),  # no E-stop reset syscall exists (13 = SYS_COUNT)
            ("cell_servo", "DENY_ESTOP", "estop"),  # raw servo(0): the latch outranks every limit
            ("cell_status", "ALLOW", "none"),
        ], decisions(t)
        assert "latched=1" in t and "estop=1" in t
        assert c.sim.fan_commands(n) == [0, 0, 0]  # AUTO_HALT, halt, fan 0: reducing energy always goes
        # released: the latch stays until a human resets it at the console; the bridge has no path to COM1
        c.sim.estop = False
        time.sleep(0.2)
        t = cellcmd(c, "fan 60")
        assert results(t) == [("fan 60", -200)]
        assert decisions(t, "cell_fan") == [("cell_fan", "DENY_ESTOP", "estop")]
        # reset attempts: pressed -> denied even after y; n -> no; timeout -> no; stale -> denied; then yes
        c.sim.estop = True
        time.sleep(0.2)
        assert estop_reset(c, "y") == "DENY_ESTOP estop_button"
        c.sim.estop = False
        time.sleep(0.2)
        assert estop_reset(c, "n") == "CONFIRM_NO answer"
        assert estop_reset(c, None) == "CONFIRM_NO timeout"
        c.sim.streaming = False
        time.sleep(0.7)
        assert estop_reset(c, "y") == "DENY_ESTOP stale"
        c.sim.streaming = True
        time.sleep(0.3)
        assert not any(f.type == T_ESTOP_RESET for f in c.sim.frames)
        assert c.sim.latched
        assert estop_reset(c, "y") == "CONFIRM_YES estop_reset (ESTOP_RESET sent)"
        c.sim.wait_frame(lambda f: f.type == T_ESTOP_RESET, timeout=2.0)
        time.sleep(0.1)
        assert not c.sim.latched and c.sim.lcd == {0: "CONFIRMED reset", 1: "operator: yes"}
        t = cellcmd(c, "wait 110; fan 60", answers=["y"])
        assert results(t)[1] == ("fan 60", 0)
        c.sim.wait_fan(60, start=n)
        a = audit_lines(c.s.output)
        assert [x["decision"] for x in a if x["sys"] == "console"] == ["CONFIRM_PENDING"] * 5
        assert [x["reason"] for x in a if x["sys"] == "confirm" and x["target"] == "0"] == [
            "estop_button", "answer", "stale", "estop_reset"]  # fmt: skip
        assert any(x["sys"] == "tick" and x["decision"] == "CONFIRM_NO" for x in a)
        run = c.close()
        record_evidence("sim-estop-latch.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS
