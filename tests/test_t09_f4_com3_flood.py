"""T-09 F4 (Codex Gate A, D-74): a COM3 stream faster than the kernel cannot hold the CPU in interrupt
context. The receive pump has a per-tick byte/frame budget; what is left waits in the UART for the next
IRQ or tick. Under a flood the policy tick keeps running (uptime advances with the wall clock), the tick
lease still halts a hazard, the console still answers `status`, no audit record is dropped, no PANIC.
On the code before T-09 the pump loops in IRQ context for as long as bytes keep coming: the tick, the
lease and the console stall, and the audit ring overflows (dropped > 0)."""

import os
import re
import time

from tests.cellharness import cellcmd, results, start
from tests.qemu import EXIT_PASS, audit_lines, record_evidence
from tests.sim_cell import T_FAN, T_SERVO, state_frame

STATUS = r"^status: .*dropped=(\d+) uptime=(\d+)$"
CELL = r"^cell: com3=1 .*halts=(\d+)(?: rx_deferred=(\d+))?$"  # no rx_deferred before T-09: counts as 0
RX_TICK_FRAMES = 8  # kernel/drivers/device.c


def status(c) -> tuple[int, int, int, int, float]:
    """(dropped, uptime ticks, halts, rx_deferred, host time) from `status`, which must answer within 5 s."""
    c.s.send("status")
    m = c.s.expect(STATUS, timeout=5.0)
    k = c.s.expect(CELL, timeout=5.0)
    c.s.prompt(timeout=5.0)
    return int(m.group(1)), int(m.group(2)), int(k.group(1)), int(k.group(2) or 0), time.monotonic()


def test_t09_f4_alternating_seq_flood_keeps_the_tick_running():
    """The finding's stream: valid STATE frames alternating seq 1 / 10000, every one a board boot with its
    synchronous FAN 0 + LCD + alarm, written as fast as the socket takes them."""
    with start() as c:
        t = cellcmd(c, "fan 60; wait 20", answers=["y"])
        assert results(t)[0] == ("fan 60", 0)
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        c.sim.streaming = False
        flood = b"".join(state_frame((1, 10000)[i % 2], 100 + 50 * i) for i in range(3000))
        sent = c.sim.write_fast(flood)
        assert sent == len(flood), (sent, len(flood))
        c.s.expect(r"^AUDIT .*sys=telemetry target=60 decision=BOARD_BOOT reason=board_boot .*$", timeout=5.0)
        d1, u1, h1, r1, t1 = status(c)  # answered while the flood is being received
        time.sleep(1.5)
        d2, u2, h2, r2, t2 = status(c)
        wall = t2 - t1
        advance = (u2 - u1) / 100.0
        assert d1 == 0 and d2 == 0
        assert advance >= 0.7 * wall, f"uptime advanced {advance:.2f} s in {wall:.2f} s: the tick stalled"
        assert h2 > h1, "the flood's boots are being processed"
        assert h2 - h1 <= RX_TICK_FRAMES * (u2 - u1) + RX_TICK_FRAMES, "more frames per tick than the budget"
        assert r2 > r1, "the budget never bound: the flood was not faster than the kernel"
        # no motion after the first boot of the flood: FAN 0 only, no SERVO
        assert set(c.sim.fan_commands(n)) <= {0}, set(c.sim.fan_commands(n))
        assert not any(f.type == T_SERVO for f in c.sim.frames_after(n))
        # the backlog drains at the budget's pace and the board's own stream resumes
        deadline = time.monotonic() + 20
        while True:
            _, _, h, _, _ = status(c)
            time.sleep(0.5)
            _, _, h_next, _, _ = status(c)
            if h_next == h:
                break
            assert time.monotonic() < deadline, "the backlog never drained"
        c.sim.streaming = True
        time.sleep(0.5)
        run = c.close()
        record_evidence("f4-flood-alternating.txt", run, "T-09")
        assert run.exit_code == EXIT_PASS


def test_t09_f4_junk_flood_lease_still_halts():
    """Bytes that never make a frame (every chunk a rejected, audited FRAME_DROP) while the fan runs: the
    telemetry goes stale behind the flood and the tick lease halts the fan on time (stale at 500 ms), FAN 0
    reaching the board; `status` answers; nothing dropped."""
    with start() as c:
        t = cellcmd(c, "fan 60; wait 20", answers=["y"])
        assert results(t)[0] == ("fan 60", 0)
        c.sim.wait_fan(60)
        n = len(c.sim.frames)
        rnd = os.urandom(20 * 3000)
        junk = b"".join(bytes(b or 1 for b in rnd[20 * i : 20 * i + 20]) + b"\x00" for i in range(3000))
        c.sim.streaming = False
        t_last = time.monotonic()
        sent = c.sim.write_fast(junk)
        assert sent == len(junk), (sent, len(junk))
        halt = c.sim.wait_fan(0, timeout=5.0, start=n)
        stop_s = halt.t - t_last
        assert stop_s <= 1.5, f"FAN 0 reached the board {stop_s:.2f} s after the last frame: lease late"
        c.s.expect(r"^AUDIT .*sys=tick target=60 decision=AUTO_HALT reason=stale .*$", timeout=5.0)
        d1, u1, _, r1, t1 = status(c)
        time.sleep(1.0)
        d2, u2, _, r2, t2 = status(c)
        assert d1 == 0 and d2 == 0
        assert (u2 - u1) / 100.0 >= 0.7 * (t2 - t1), "the tick stalled under the junk flood"
        assert r2 > r1, "the budget never bound"
        assert set(c.sim.fan_commands(n)) <= {0}
        assert not any(f.type in (T_FAN, T_SERVO) and f.body[0] for f in c.sim.frames_after(n))
        deadline = time.monotonic() + 20
        while True:
            m = re.search(r"frame_drops=(\d+)", c.s.output[-4000:])
            drops = int(m.group(1)) if m else -1
            time.sleep(0.5)
            status(c)
            m = re.search(r"frame_drops=(\d+)", c.s.output[-4000:])
            if m and int(m.group(1)) == drops:
                break
            assert time.monotonic() < deadline, "the junk backlog never drained"
        rejected = sum(1 for a in audit_lines(c.s.output) if a["decision"] == "FRAME_DROP")
        assert rejected >= 100, rejected  # the flood was audited, chunk by chunk, none lost
        c.sim.streaming = True
        time.sleep(0.5)
        run = c.close()
        record_evidence("f4-flood-junk.txt", run, "T-09")
        assert run.exit_code == EXIT_PASS
