"""INV-07 / A3 / R2: spin -> LIMIT on the action quota; crossing action boundaries but running too long ->
LIMIT on the request quota; a prompt pauses the action quota but not the request quota."""

import re

from tests.qemu import EXIT_PASS, Session, record_evidence


def test_action_and_request_quotas():
    with Session(append="taction=100 trequest=300 tconfirm=1000", timeout=40) as s:
        s.expect(r"^policy: .*$")
        t = s.run_prog("spin")
        assert re.search(r"decision=LIMIT reason=action_quota", t), t
        m = re.search(r"^LIMIT pid=\d+ ticks=(\d+)$", t, re.M)
        assert m and 100 <= int(m.group(1)) < 150, t
        t = s.run_prog("multi")
        assert re.search(r"decision=LIMIT reason=request_quota", t), t
        assert "action_quota" not in t
        m = re.search(r"^LIMIT pid=\d+ ticks=(\d+)$", t, re.M)
        assert m and 300 <= int(m.group(1)) < 350, t
        t = s.run_prog("ticks")
        assert "code=0" in t
        # Waiting on a prompt: the action quota (1 s) must not kill it; the request quota (3 s) does.
        s.prompt()
        s.send("run confirmw")
        s.expect(r"CONFIRM: confirmw wants obj_write\(maint_notes, 6 bytes\)\. Allow\? \[y/N\] ")
        start = s.pos
        s.expect(r"^END pid=\d+ LIMIT ticks=(\d+) uptime=\d+$", timeout=10)
        t = s.output[start : s.pos]
        assert "action_quota" not in t and "request_quota" in t, t
        assert "confirmw: result=" not in t
        run = s.close()
    record_evidence("quotas.txt", run)
    assert run.exit_code == EXIT_PASS
