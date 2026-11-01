"""T-01 boot tests: banner, timer, deliberate CPL-0 panic dump (A4), INV-12 panic check."""

import re

from tests.qemu import EXIT_PANIC, EXIT_PASS, record_evidence, run_qemu


def test_boot_banner_and_ticks():
    run = run_qemu(timeout=15)
    record_evidence("boot.txt", run)
    assert run.exit_code == EXIT_PASS, run.output
    assert run.seconds < 5.0 or "RING3 " in run.output
    assert re.search(r"^RING3 \d+\.\d+\.\d+ build \S+$", run.output, re.M), run.output
    assert re.search(r"^memory: \d+ KiB usable$", run.output, re.M)
    t0 = int(re.search(r"^TIMER t0=(\d+)$", run.output, re.M).group(1))
    t1 = int(re.search(r"^TIMER t1=(\d+)$", run.output, re.M).group(1))
    assert t1 > t0, "ticks did not advance"
    assert re.search(r"^KERNEL ALIVE uptime=(\d+)$", run.output, re.M)


def test_banner_within_5s():
    run = run_qemu(timeout=15)
    assert "RING3 " in run.output
    assert run.seconds < 5.0, f"boot took {run.seconds:.1f} s"


def test_deliberate_cpl0_panic_dumps_and_exits():
    run = run_qemu(append="testexit panic", timeout=15, expect_panic=True)
    record_evidence("panic.txt", run)
    assert run.exit_code == EXIT_PANIC, run.output
    assert run.has_panic()
    dump = run.after("PANIC:")
    assert re.search(r"vector=\d+ err=0x[0-9a-f]{8}", dump)
    for reg in ("eip=0x", "cs=0x", "eflags=0x", "cr2=0x", "eax=0x", "esp=0x"):
        assert reg in dump, f"{reg} missing from dump:\n{dump}"
    assert "KERNEL ALIVE" not in run.output
