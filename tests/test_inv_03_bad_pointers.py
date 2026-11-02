"""INV-03 / A4: every bad user pointer gets -EFAULT (no kernel page fault); the valid edges succeed; bounded
string copies; and the seeded syscall fuzzer runs 10,000 calls with 0 panics."""

import re

from tests.qemu import EXIT_PASS, Session, audit_lines, record_evidence


def test_bad_pointers_and_strings():
    with Session() as s:
        s.expect(r"^policy: .*$")
        t = s.run_prog("badptr")
        cases = re.findall(r"^badptr case (\d+) (EFAULT ok|valid ok|UNEXPECTED)", t, re.M)
        assert len(cases) == 18 and all(k != "UNEXPECTED" for _, k in cases), t
        assert "badptr: all cases ok" in t and "code=0" in t
        assert "PROC_FAULT" not in t
        # every denied pointer is a DENY_FAULT with -EFAULT and no state change (R3)
        denies = [a for a in audit_lines(t) if a["decision"] == "DENY_FAULT"]
        assert len(denies) >= 12 and all(a["result"] == "-14" and a["reason"] == "uaccess" for a in denies)
        t2 = s.run_prog("lcd")
        assert "lcd: done" in t2 and "UNEXPECTED" not in t2, t2
        run = s.close()
    record_evidence("badptr.txt", run)
    assert run.exit_code == EXIT_PASS


def test_syscall_fuzz():
    with Session(timeout=60) as s:
        s.expect(r"^policy: .*$")
        s.prompt()
        s.send("tape off")  # 10,000 audit lines would be paced by the UART baud rate
        s.expect(r"^tape off$")
        t = s.run_prog("fuzz", timeout=120)
        m = re.search(r"^fuzz: calls=10000 .* bad=(\d+) ticks=(\d+)$", t, re.M)
        assert m and m.group(1) == "0", t
        assert int(m.group(2)) > 0, "uptime did not advance during the fuzz run"
        assert "EXIT pid=" in t and "code=0" in t, t
        s.prompt()
        s.send("status")
        st = s.expect(r"^status: .*$").group(0)
        assert "dropped=0" in st and "in_request=0" in st and "pending=0" in st, st
        run = s.close()
    record_evidence("fuzz.txt", run)
    assert run.exit_code == EXIT_PASS
    assert not run.has_panic()
