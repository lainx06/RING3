"""INV-13 interlock + stale (simulated device): presence (< 100 mm), no echo (0xFFFF), stale (> 500 ms) deny
motion and halt a running fan from the tick lease; firmware restart handling (D-53); the A7 cases: a hazard
during a pending prompt denies on y with nothing sent; AUTO_HALT then cell_fan(50) while tainted prompts."""

import time

from tests.cellharness import cellcmd, decisions, estop_reset, last_request, results, start
from tests.qemu import EXIT_PASS, audit_lines, record_evidence
from tests.sim_cell import NO_ECHO, T_FAN, T_LCD


def test_inv_13_interlock_and_stale():
    with start() as c:
        c.sim.distance = 50
        time.sleep(0.2)
        t = cellcmd(c, "fan 60; servo 120")
        assert results(t) == [("fan 60", -200), ("servo 120", -200)]
        assert decisions(t, "cell_fan") == [("cell_fan", "DENY_INTERLOCK", "presence")]
        assert decisions(t, "cell_servo") == [("cell_servo", "DENY_INTERLOCK", "presence")]
        c.sim.distance = NO_ECHO
        time.sleep(0.2)
        t = cellcmd(c, "fan 60")
        assert results(t) == [("fan 60", -200)]
        assert decisions(t, "cell_fan") == [("cell_fan", "DENY_INTERLOCK", "no_echo")]
        assert c.sim.lcd == {0: "DENIED fan 60", 1: "sensor: no echo"} and c.sim.alarm == 1
        assert c.sim.fan_commands() == [0]  # only the BOARD_BOOT halt so far
        # clear: the fan runs; then the board goes silent -> stale within 500 ms -> AUTO_HALT from the lease
        c.sim.distance = 300
        time.sleep(0.2)
        t = cellcmd(c, "fan 60; wait 20", answers=["y"])
        assert results(t)[0] == ("fan 60", 0)
        c.sim.wait_fan(60)
        assert c.sim.fan == 60
        n = len(c.sim.frames)
        c.sim.streaming = False
        t_silent = time.monotonic()
        halt = c.sim.wait_fan(0, timeout=2.0, start=n)
        stop_ms = (halt.t - t_silent) * 1000
        assert stop_ms < 800, stop_ms  # 500 ms stale limit + a tick + socket latency
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=tick target=60 decision=AUTO_HALT reason=stale .*$")
        c.s.prompt()
        assert c.sim.lcd == {0: "AUTO HALT fan 60", 1: "stale telemetry"}
        t = cellcmd(c, "fan 60; wait 110; fan 60")  # the halt was a change: rate rule first (R5), then stale
        assert results(t) == [("fan 60", -200), ("wait 110", 0), ("fan 60", -200)]
        assert decisions(t, "cell_fan") == [
            ("cell_fan", "DENY_LIMIT", "fan_rate"), ("cell_fan", "DENY_STALE", "stale"),
        ]  # fmt: skip
        # frames resume (same boot): fresh again; the hazard stop withdrew the first-fan confirmation
        c.sim.streaming = True
        time.sleep(1.2)
        t = cellcmd(c, "fan 60", answers=["y"])
        assert results(t) == [("fan 60", 0)]
        assert decisions(t)[-2:] == [
            ("cell_fan", "CONFIRM_PENDING", "first_fan"), ("confirm", "CONFIRM_YES", "first_fan"),
        ]  # fmt: skip
        c.sim.wait_fan(60, start=n)
        # a hand while the fan runs: AUTO_HALT within one frame + one tick, FAN 0 on the wire
        n = len(c.sim.frames)
        t_hand = time.monotonic()
        c.sim.distance = 50
        halt = c.sim.wait_fan(0, timeout=1.0, start=n)
        assert (halt.t - t_hand) * 1000 < 250, (halt.t - t_hand) * 1000
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=60 decision=AUTO_HALT reason=presence .*$")
        assert c.sim.lcd == {0: "AUTO HALT fan 60", 1: "hand too close"} and c.sim.alarm == 1
        c.s.prompt()
        print(f"stop after silence: {stop_ms:.0f} ms; stop after presence: {(halt.t - t_hand) * 1000:.0f} ms")
        run = c.close()
        record_evidence("sim-interlock-stale.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS


def test_firmware_restart_stale_until_3_frames():
    """D-53 / A7: seq and t_ms going backwards -> BOARD_BOOT: duty 0, FAN 0 sent and nothing else, the
    previous motion never re-commanded, telemetry stale until 3 monotonic frames, first fan asked again."""
    with start() as c:
        c.sim.age(120_000)  # a board that has been up for two minutes
        time.sleep(0.2)
        t = cellcmd(c, "fan 60; servo 120; wait 20", answers=["y"])
        assert results(t)[:2] == [("fan 60", 0), ("servo 120", 0)]
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        c.sim.restart(hold_after=1)  # one frame of the new boot, then silence
        c.s.expect(r"^AUDIT .*sys=telemetry target=60 decision=BOARD_BOOT reason=board_boot .*$")
        c.s.prompt()
        halt = c.sim.wait_fan(0, timeout=1.0, start=n)
        assert halt is not None
        # D-89: the panel frames land a few ms after FAN 0; wait for line 1 (sent after line 0), then check
        c.sim.wait_frame(lambda f: f.type == T_LCD and f.body[0] == 1, timeout=1.0, start=n)
        assert c.sim.lcd == {0: "BOARD BOOT", 1: "fresh boot"}
        t = cellcmd(c, "wait 110; fan 60; servo 120")  # > 1 s after the boot (past the rate rule)
        assert results(t)[1:] == [("fan 60", -200), ("servo 120", -200)]
        assert decisions(t, "cell_fan") == [("cell_fan", "DENY_STALE", "no_telemetry")]
        c.sim.streaming = True  # frames 2 and 3 of the new boot
        time.sleep(0.3)
        t = cellcmd(c, "fan 60", answers=["y"])
        assert results(t) == [("fan 60", 0)]
        assert decisions(t)[-2:] == [
            ("cell_fan", "CONFIRM_PENDING", "first_fan"), ("confirm", "CONFIRM_YES", "first_fan"),
        ]  # fmt: skip
        # after the restart the board got FAN 0 first and no FAN > 0 or SERVO until the new confirmed command
        after = [str(f) for f in c.sim.frames_after(n) if f.type in (T_FAN, 3)]
        assert after == ["FAN 0", "FAN 60"], after
        run = c.close()
        record_evidence("sim-restart.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS


def test_hazard_during_pending_prompt_denies_on_y():
    """A7 / R1: a hand, the E-stop or silence while the first-fan prompt waits; the human says y; the call
    is denied at the answer's tick and no FAN frame reaches COM3."""
    with start() as c:
        n = len(c.sim.frames)
        c.s.send("run cellcmd fan 60")
        c.s.expect(r"CONFIRM: cellcmd wants cell_fan\(60\)\. Allow\? \[y/N\] ")
        c.sim.distance = 50
        time.sleep(0.2)
        c.s.send("y")
        c.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        t = c.s.output
        assert "cellcmd: fan 60 -> -200" in t
        assert decisions(t, "confirm")[-1] == ("confirm", "DENY_INTERLOCK", "presence")
        c.s.prompt()
        c.sim.distance = 300
        time.sleep(0.2)
        c.s.send("run cellcmd fan 60")
        c.s.expect(r"CONFIRM: cellcmd wants cell_fan\(60\)\. Allow\? \[y/N\] ")
        c.sim.estop = True
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=0 decision=AUTO_HALT reason=estop .*$")
        c.s.send("y")
        c.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        assert decisions(c.s.output, "confirm")[-1] == ("confirm", "DENY_ESTOP", "estop")
        c.s.prompt()
        c.sim.estop = False
        time.sleep(0.2)
        # the latch outlives the button: a console reset first
        assert estop_reset(c, "y") == "CONFIRM_YES estop_reset (ESTOP_RESET sent)"
        c.s.send("run cellcmd fan 60")
        c.s.expect(r"CONFIRM: cellcmd wants cell_fan\(60\)\. Allow\? \[y/N\] ")
        c.sim.streaming = False
        time.sleep(0.7)
        c.s.send("y")
        c.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        assert decisions(c.s.output, "confirm")[-1] == ("confirm", "DENY_STALE", "stale")
        c.s.prompt()
        assert c.sim.fan_commands(n) == [0], c.sim.motion_commands(n)  # only the E-stop latch's AUTO_HALT
        assert not any(f.type == T_FAN and f.body[0] for f in c.sim.frames)
        assert c.sim.fan == 0
        run = c.close()
        record_evidence("sim-prompt-hazard.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS


def test_autohalt_then_fan_while_tainted_prompts():
    """A7 (D-53): after an AUTO_HALT the commanded duty is 0, so cell_fan(50) while tainted is an
    energy-adding step from 0 that prompts [tainted]; it is not treated as a decrease from 60."""
    with start() as c:
        c.s.send("run cellcmd read 1; fan 60; wait 150; fan 50")
        c.s.expect(r"CONFIRM: cellcmd wants cell_fan\(60\) \[tainted\]\. Allow\? \[y/N\] ")
        c.s.send("y")
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        c.sim.distance = 50
        c.sim.wait_fan(0, timeout=1.0, start=n)
        c.sim.distance = 300
        c.s.expect(r"CONFIRM: cellcmd wants cell_fan\(50\) \[tainted\]\. Allow\? \[y/N\] ")
        c.s.send("y")
        c.s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        t = last_request(c)
        assert "cellcmd: fan 50 -> 0" in t
        a = [x for x in audit_lines(t) if x["sys"] in ("cell_fan", "confirm", "telemetry")]
        assert [(x["decision"], x["reason"], x["target"]) for x in a] == [
            ("CONFIRM_PENDING", "taint", "60"),
            ("CONFIRM_YES", "taint", "60"),
            ("AUTO_HALT", "presence", "60"),
            ("CONFIRM_PENDING", "taint", "50"),
            ("CONFIRM_YES", "taint", "50"),
        ], a
        c.sim.wait_fan(50, start=n)  # the frame is on its way when END prints
        assert c.sim.fan_commands(n) == [0, 50]
        c.s.prompt()
        run = c.close()
        record_evidence("sim-autohalt-taint.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS
