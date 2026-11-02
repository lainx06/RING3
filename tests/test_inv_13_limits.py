"""INV-13 (kernel layer, simulated device on a socket): servo range/step/rate, fan duty/ramp, every allowed
command and only those reach COM3; boot resync and a previous boot's frames are counted, not dropped;
re-delivered frames change nothing; a corrupt frame is a FRAME_DROP; the sim run replays on the host."""

import subprocess
import time

from tests.cellharness import cellcmd, decisions, results, start
from tests.qemu import EXIT_PASS, HOST_POLICY_TEST, REPO, audit_lines, evidence_dir, record_evidence
from tests.sim_cell import T_FAN, T_SERVO, state_frame

LIMITS = (
    "fan 90; fan 30; fan 70; servo 10; servo 170; servo 150; fan 60; wait 20; servo 120; fan 80; fan 50; "
    "wait 110; fan 70; servo 150; wait 25; servo 140; halt"
)


def test_inv_13_limits():
    with start() as c:
        t = cellcmd(c, LIMITS, answers=["y"])
        r = results(t)
        assert r == [
            ("fan 90", -200),  # over 80 % (DENY_LIMIT fan_range)
            ("fan 30", -22),  # 1-49 % would stall (DENY_ARG, D-47)
            ("fan 70", -200),  # first step must be 50-60 (fan_step)
            ("servo 10", -200),  # servo_range
            ("servo 170", -200),  # servo_range
            ("servo 150", -200),  # 60 deg step (servo_step)
            ("fan 60", 0),  # first fan: prompt, y, FAN 60 sent
            ("wait 20", 0),
            ("servo 120", 0),  # 30 deg step: sent
            ("fan 80", -200),  # +20 but < 1 s after the last change (fan_rate)
            ("fan 50", 0),  # a decrease: sent
            ("wait 110", 0),
            ("fan 70", 0),  # +20, > 1 s: sent
            ("servo 150", 0),  # +30, > 200 ms: sent
            ("wait 25", 0),
            ("servo 140", 0),
            ("halt", 0),  # FAN 0
        ], t
        d = decisions(t)
        assert [x for x in d if x[0] in ("cell_fan", "cell_servo", "cell_halt", "confirm")] == [
            ("cell_fan", "DENY_LIMIT", "fan_range"),
            ("cell_fan", "DENY_ARG", "arg_range"),
            ("cell_fan", "DENY_LIMIT", "fan_step"),
            ("cell_servo", "DENY_LIMIT", "servo_range"),
            ("cell_servo", "DENY_LIMIT", "servo_range"),
            ("cell_servo", "DENY_LIMIT", "servo_step"),
            ("cell_fan", "CONFIRM_PENDING", "first_fan"),
            ("confirm", "CONFIRM_YES", "first_fan"),
            ("cell_servo", "ALLOW", "none"),
            ("cell_fan", "DENY_LIMIT", "fan_rate"),
            ("cell_fan", "ALLOW", "none"),
            ("cell_fan", "ALLOW", "none"),
            ("cell_servo", "ALLOW", "none"),
            ("cell_servo", "ALLOW", "none"),
            ("cell_halt", "ALLOW", "halt"),
        ], d
        # the board saw exactly the allowed motion commands, in order, and nothing else that moves
        assert c.sim.motion_commands() == [
            "FAN 0",  # the BOARD_BOOT halt at sync
            "FAN 60", "SERVO 120", "FAN 50", "FAN 70", "SERVO 150", "SERVO 140", "FAN 0",
        ], c.sim.motion_commands()  # fmt: skip
        time.sleep(0.15)  # the board applies a setpoint on its next 50 ms pass
        assert c.sim.fan == 0 and c.sim.servo == 140 and not c.sim.errors
        # the panel shows the kernel's last decision in plain words (INV-11: never "safe"/"success")
        assert c.sim.lcd == {0: "ALLOW halt", 1: "halt"}, c.sim.lcd
        c.s.send("status")
        m = c.s.expect(r"^cell: com3=1 synced=1 .*frame_drops=(\d+) .*$")
        assert m.group(1) == "0"
        run = c.close()
        record_evidence("sim-limits.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS
        # A5 seed: the same tape through the host build of the policy reproduces every decision
        tape = [line for line in run.output.splitlines() if line.startswith("TAPE ")]
        kernel = audit_lines(run.output)
        assert any(k["decision"] == "BOARD_BOOT" for k in kernel)
        p = subprocess.run([str(HOST_POLICY_TEST), "--replay"], input="\n".join(tape) + "\n", cwd=REPO,
                           capture_output=True, text=True, timeout=60, check=True)  # fmt: skip
        host = audit_lines(p.stdout)
        (evidence_dir("T-04") / "sim-limits-replay-host.txt").write_text(p.stdout)
        assert len(host) == len(kernel), f"kernel {len(kernel)} audits, host {len(host)}"
        for i, (k, h) in enumerate(zip(kernel, host, strict=True)):
            for field in ("seq", "tick", "sys", "target", "decision", "reason", "tainted"):
                assert k[field] == h[field], f"MISMATCH at #{i} field {field}: kernel {k} host {h}"
        print(f"REPLAY MATCH: {len(kernel)} decisions, {len(tape)} tape events (sim device)")


def test_boot_resync_and_previous_boot_frames_are_not_drops():
    """D-53/D-54: bytes before the first delimiter are a resync; whole frames of the board's previous boot
    (here a block of 6 delivered three times) are counted and not fed; the new boot is BOARD_BOOT once;
    frames delivered again later change nothing; a corrupt frame is a FRAME_DROP and nothing else."""
    cut = state_frame(1846, 99_950, dist=287, ack=8)[7:]  # the tail of a frame cut by the reset (+ its 0x00)
    with start(prev_boot_frames=6, prev_boot_repeat=3, cut_prefix=cut) as c:
        c.s.send("status")
        m = c.s.expect(
            r"^cell: com3=1 synced=1 frames=(\d+) prev_boot=(\d+) old=(\d+) frame_drops=(\d+) "
            r"resync_bytes=(\d+) .*$"
        )
        frames, prev_boot, old, drops, resync = (int(x) for x in m.groups())
        # the tail's bytes up to its delimiter are the resync (no FRAME_DROP); the 18 whole frames: prev_boot
        assert prev_boot == 18 and resync == len(cut) - 1 and drops == 0 and old == 0, m.group(0)
        assert frames >= 3
        boots = [a for a in audit_lines(c.s.output) if a["decision"] == "BOARD_BOOT"]
        assert len(boots) == 1 and not any(a["decision"] == "FRAME_DROP" for a in audit_lines(c.s.output))
        c.s.prompt()
        # re-delivery after sync: ignored, counted, no audit, policy state untouched
        c.s.send("status")
        seq_before = c.s.expect(r"^status: .*seq=(\d+) .*$").group(1)
        c.s.prompt()
        c.sim.resend_last(6)
        time.sleep(0.2)
        c.s.send("status")
        assert c.s.expect(r"^status: .*seq=(\d+) .*$").group(1) == seq_before  # no audit record from them
        m = c.s.expect(r"^cell: com3=1 synced=1 frames=\d+ prev_boot=18 old=(\d+) frame_drops=(\d+) .*$")
        assert m.group(1) == "6" and m.group(2) == "0", m.group(0)
        c.s.prompt()
        # a corrupt frame (bad CRC): FRAME_DROP, audited with the decoder's code, the stream continues
        bad = bytearray(state_frame(5000, 123_456))
        bad[-3] ^= 0x55
        c.sim.write_raw(bytes(bad))
        c.s.expect(r"^AUDIT seq=\d+ tick=\d+ sys=telemetry target=5 decision=FRAME_DROP reason=bad_frame .*$")
        c.s.prompt()
        c.s.send("status")
        m = c.s.expect(r"^cell: com3=1 synced=1 .*frame_drops=(\d+) .*$")
        assert m.group(1) == "1"
        c.s.prompt()
        boots = [a for a in audit_lines(c.s.output) if a["decision"] == "BOARD_BOOT"]
        assert len(boots) == 1 and boots[0]["target"] == "0"
        assert c.s.output.count("decision=BOARD_BOOT") == 1
        moving = [f for f in c.sim.frames if f.type == T_SERVO or (f.type == T_FAN and f.body[0] != 0)]
        assert not moving
        run = c.close()
        record_evidence("sim-boot-resync.txt", run, "T-04")
        assert run.exit_code == EXIT_PASS
