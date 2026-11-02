"""F5 (D-50, D-52) on the audit/tape path of INV-10: records are drained at line start and per tick, the ring
never drops (`dropped > 0` fails every QEMU run through the harness), and the fuzzer / the F1 program keep
`dropped == 0`."""

import re

from tests.qemu import EXIT_PASS, Session, record_evidence


def test_audit_drains_at_line_start():
    """A program's audited call is on COM1 before the program ends, not held in the ring until EXEC_END."""
    with Session() as s:
        s.expect(r"^policy: .*$")
        t = s.run_prog("spin")
        audit = re.search(r"^AUDIT seq=\d+ tick=\d+ sys=cons_write .*$", t, re.M)
        limit = re.search(r"^LIMIT pid=\d+ ticks=\d+$", t, re.M)
        assert audit and limit, t
        assert audit.start() < limit.start(), f"the cons_write audit was held until the LIMIT:\n{t}"
        run = s.close()
    record_evidence("drain-line-start.txt", run, "T-02F")
    assert run.exit_code == EXIT_PASS


def test_irq_side_records_drained_per_tick():
    """Telemetry records fed from the tick IRQ while ring 3 spins (no syscalls): more of them than the ring
    holds, drained per tick, none dropped, and they appear on the tape during the run."""
    with Session(append="taction=300 trequest=400 tconfirm=150", timeout=40) as s:
        s.expect(r"^policy: .*$")
        s.prompt()
        s.send("teletick 300 0 1")
        s.expect(r"^teletick every 1$")
        t = s.run_prog("spin", timeout=20)
        assert re.search(r"decision=LIMIT reason=action_quota", t), t
        tele = re.findall(r"^TAPE k=7 .*$", t, re.M)
        assert len(tele) >= 280, f"only {len(tele)} telemetry tape lines during a 300-tick spin:\n{t}"
        s.prompt()
        s.send("teletick off")
        s.expect(r"^teletick off$")
        s.prompt()
        s.send("status")
        st = s.expect(r"^status: .*$").group(0)
        assert "dropped=0" in st, st
        run = s.close()
    record_evidence("drain-per-tick.txt", run, "T-02F")
    assert run.exit_code == EXIT_PASS
