"""INV-06 / A3: each of the seven fault programs ends with PROC_FAULT (correct vector), the next request
still runs, uptime advances, zero panics. Plus the DF-flag regression (skeptic #3)."""

import re

from tests.qemu import EXIT_PASS, Session, record_evidence

FAULTS = [
    ("null", 14),
    ("kwrite", 14),
    ("priv", 13),  # cli
    ("hlt", 13),
    ("ud", 6),
    ("div0", 0),
    ("stack", 14),
]


def test_fault_containment():
    with Session() as s:
        s.expect(r"^policy: .*$")
        for name, vector in FAULTS:
            t = s.run_prog(name)
            m = re.search(r"^PROC_FAULT pid=(\d+) vector=(\d+) eip=0x4[0-9a-f]{7}", t, re.M)
            assert m and int(m.group(2)) == vector, f"{name}: expected vector {vector}:\n{t}"
            assert "STILL ALIVE" not in t
            assert re.search(r"decision=PROC_FAULT reason=exec_end", t), t
            assert re.search(r"^END pid=\d+ FAULT ", t, re.M)
            if name == "stack":
                assert re.search(r"cr2=0x7fff[ab][0-9a-f]{3}", t), t  # the guard page below the stack
            # the next request runs and sees the timer advancing
            t = s.run_prog("ticks")
            m = re.search(r"^ticks: t0=(\d+) t1=(\d+)$", t, re.M)
            assert m and int(m.group(2)) > int(m.group(1)), t
            assert "EXIT pid=" in t and "code=0" in t
        t = s.run_prog("dflag")
        assert "dflag: text copied with DF set" in t and "code=0" in t
        run = s.close()
    record_evidence("faults.txt", run)
    assert run.exit_code == EXIT_PASS
    assert not run.has_panic()
