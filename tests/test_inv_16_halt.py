"""INV-16 (simulated device): cell_halt always goes through (tainted, under E-stop, without capabilities:
host test) and sends FAN 0; the tick lease halts a hazard while agentsh waits in agent_recv for a slow model,
with no syscall involved, within one tick of the frame that showed it."""

import time

from tests.agentbridge import AGENT_QUOTAS, BridgeThread, call, done, runs_dir
from tests.cellharness import cellcmd, decisions, results, start
from tests.qemu import EXIT_PASS, audit_lines, record_evidence


def test_inv_16_halt_always():
    with start() as c:
        t = cellcmd(c, "fan 60; wait 20", answers=["y"])
        assert results(t)[0] == ("fan 60", 0)
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        # tainted + E-stop latched: halt and fan(0) need no telemetry, no capability, no prompt
        c.sim.estop = True
        c.s.expect(r"^AUDIT .*decision=AUTO_HALT reason=estop .*$")
        c.s.prompt()
        c.sim.streaming = False  # and stale on top
        time.sleep(0.7)
        t = cellcmd(c, "read 1; halt; fan 0; fan 60")
        assert results(t) == [("read 1", 216), ("halt", 0), ("fan 0", 0), ("fan 60", -200)]
        assert decisions(t) == [
            ("cell_halt", "ALLOW", "halt"),
            ("cell_fan", "ALLOW", "none"),
            ("cell_fan", "DENY_ESTOP", "estop"),
        ]
        assert all(a["tainted"] == "1" for a in audit_lines(t) if a["sys"] in ("cell_halt", "cell_fan"))
        assert c.sim.fan_commands(n) == [0, 0, 0]  # AUTO_HALT, halt, fan 0; the fan 60 never left the kernel
        run = c.close()
        record_evidence("sim-halt-always.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS


def test_inv_16_tick_lease_autohalt(tmp_path):
    """The model asks for fan 60 (the human says y), then takes 2 s to answer; a hand appears meanwhile,
    while agentsh is blocked in agent_recv: the kernel's COM3 path halts the fan on its own and the board
    sees FAN 0 within one frame + one tick. The model's later halt finds the fan already stopped."""
    with start(append=AGENT_QUOTAS, com2=True, timeout=90) as c:
        sim = c.sim

        def slow_halt() -> str:
            time.sleep(2.0)
            return call("halt")

        replies = [call("fan_set", duty=60), slow_halt, done("fan ran at 60 for a moment, then halted")]
        b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, "lease"), replies).start()
        c.s.send("spin the fan at 60 % for a moment, then halt")
        c.s.expect(r"CONFIRM: agentsh wants cell_fan\(60\)\. Allow\? \[y/N\] ")
        c.s.send("y")
        sim.wait_fan(60)
        n = len(sim.frames)
        time.sleep(0.3)  # the executor is now waiting in agent_recv for the slow reply
        t_hand = time.monotonic()
        sim.distance = 50
        halt = sim.wait_fan(0, timeout=1.0, start=n)
        stop_ms = (halt.t - t_hand) * 1000
        c.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 40)
        b.join()
        t = c.s.output
        assert "OUTCOME DONE" in t and "2 halt(0) -> 0" in t, t
        subjects = ("cell_fan", "confirm", "telemetry", "agent_recv", "cell_halt")
        a = [x for x in audit_lines(t) if x["sys"] in subjects]
        kinds = [(x["sys"], x["decision"], x["reason"]) for x in a]
        i_yes = kinds.index(("confirm", "CONFIRM_YES", "first_fan"))
        i_halt = kinds.index(("telemetry", "AUTO_HALT", "presence"))
        i_model_halt = kinds.index(("cell_halt", "ALLOW", "halt"))
        assert i_yes < i_halt < i_model_halt, kinds
        # between the y and the AUTO_HALT the executor made one syscall: the agent_recv it was blocked in
        between = [k for k in kinds[i_yes + 1 : i_halt] if k[0] != "telemetry"]
        assert between == [("agent_recv", "ALLOW", "none")], between
        assert stop_ms < 250, stop_ms
        assert sim.fan_commands(n) == [0, 0], sim.motion_commands(n)  # the AUTO_HALT, then the model's halt
        print(f"AUTO_HALT while waiting for the model: FAN 0 on COM3 {stop_ms:.0f} ms after the hand")
        run = c.close()
        record_evidence("sim-tick-lease.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS
