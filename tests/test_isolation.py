"""T-01 isolation slice (A5) and kill check K1-K4: one transcript, checked in order."""

import re

from tests.qemu import EXIT_PASS, record_evidence, run_qemu


def _after(text: str, marker: str) -> str:
    i = text.find(marker)
    assert i >= 0, f"{marker!r} missing:\n{text}"
    return text[i:]


def test_isolation_transcript():
    run = run_qemu(timeout=30)
    record_evidence("isolation.txt", run)
    out = run.output
    assert run.exit_code == EXIT_PASS, out
    assert not run.has_panic()

    # K1: hello makes a syscall from CPL 3 and exits 0; the writable data/bss pages work.
    t = _after(out, "RUN pid=1 name=hello")
    assert re.search(r"^SYSCALL pid=1 nr=\d+ cpl=3$", t, re.M), t
    assert "hello from ring 3" in t
    assert "g_counter=42 g_bss[2]=43 g_bss[0]=0" in t
    assert "EXIT pid=1 code=0" in t

    # K2: kwrite -> #PF (vector 14), killed, kernel continues.
    t = _after(t, "RUN pid=2 name=kwrite")
    assert re.search(r"^PROC_FAULT pid=2 vector=14 eip=0x4[0-9a-f]{7}", t, re.M), t
    assert "kwrite: STILL ALIVE" not in t
    assert "END pid=2 FAULT" in t

    # the program after a fault still gets timer ticks
    t = _after(t, "RUN pid=3 name=ticks")
    m = re.search(r"^ticks: t0=(\d+) t1=(\d+)$", t, re.M)
    assert m and int(m.group(2)) > int(m.group(1)), t
    assert "EXIT pid=3 code=0" in t

    # priv -> #GP (vector 13)
    t = _after(t, "RUN pid=4 name=priv")
    assert re.search(r"^PROC_FAULT pid=4 vector=13 ", t, re.M), t
    assert "priv: STILL ALIVE" not in t

    # badptr: every case -EFAULT, no kernel page fault, exit 0
    t = _after(t, "RUN pid=5 name=badptr")
    body = t.split("RUN pid=6", 1)[0]
    cases = re.findall(r"^badptr case (\d+) (EFAULT ok|UNEXPECTED)", body, re.M)
    assert len(cases) >= 6 and all(k == "EFAULT ok" for _, k in cases), body
    assert "badptr: all cases EFAULT" in body
    assert "EXIT pid=5 code=0" in body

    # K3: spin -> LIMIT with ticks advancing during it
    t = _after(t, "RUN pid=6 name=spin")
    before = int(re.search(r"^RUN pid=6 name=spin uptime=(\d+)$", t, re.M).group(1))
    m = re.search(r"^LIMIT pid=6 ticks=(\d+)$", t, re.M)
    assert m and int(m.group(1)) >= 100, t
    after_spin = int(re.search(r"^END pid=6 LIMIT ticks=\d+ uptime=(\d+)$", t, re.M).group(1))
    assert after_spin > before

    # the program after LIMIT still gets timer ticks
    t = _after(t, "RUN pid=7 name=ticks")
    m = re.search(r"^ticks: t0=(\d+) t1=(\d+)$", t, re.M)
    assert m and int(m.group(2)) > int(m.group(1)), t

    # K4: KERNEL ALIVE with uptime greater than before spin
    m = re.search(r"^KERNEL ALIVE uptime=(\d+)$", t, re.M)
    assert m and int(m.group(1)) > before, t
