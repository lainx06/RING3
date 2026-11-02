"""Boot tests: banner with the policy hash, timer, the `ring3> ` prompt, CPL-0 panic dump, INV-12."""

import re

from tests.qemu import EXIT_PANIC, EXIT_PASS, KERNEL, KERNEL_TEST, Session, record_evidence, run_qemu


def test_boot_banner_prompt_and_exit():
    with Session(KERNEL, append="") as s:  # the production image: no test programs, no options
        s.expect(r"^RING3 \d+\.\d+\.\d+ build \S+$")
        m = s.expect(r"^policy: (\S+) sha256=([0-9a-f]{64})$")
        assert m.group(1) == "default"
        s.expect(r"^memory: \d+ KiB usable$")
        t0 = int(s.expect(r"^TIMER t0=(\d+)$").group(1))
        t1 = int(s.expect(r"^TIMER t1=(\d+)$").group(1))
        assert t1 > t0, "ticks did not advance"
        assert s.proc.poll() is None
        s.prompt()
        s.send("run hello")  # not a production command
        s.expect(r"^unknown command: run hello$")
        s.prompt()
        s.send("help")
        s.expect(r"^commands: .*$")
        run = s.close()
    record_evidence("boot.txt", run)
    assert run.exit_code == EXIT_PASS, run.output
    assert re.search(r"^KERNEL ALIVE uptime=\d+$", run.output, re.M)
    assert "PERMISSIVE" not in run.output


def test_banner_within_5s():
    with Session(KERNEL_TEST) as s:
        s.expect(r"^RING3 ", timeout=5)
        s.prompt(timeout=5)
        run = s.close()
    assert run.exit_code == EXIT_PASS


def test_deliberate_cpl0_panic_dumps_and_exits():
    run = run_qemu(KERNEL_TEST, append="testexit panic", timeout=15, expect_panic=True)
    record_evidence("panic.txt", run)
    assert run.exit_code == EXIT_PANIC, run.output
    assert run.has_panic()
    dump = run.after("PANIC:")
    assert re.search(r"vector=\d+ err=0x[0-9a-f]{8}", dump)
    for reg in ("eip=0x", "cs=0x", "eflags=0x", "cr2=0x", "eax=0x", "esp=0x"):
        assert reg in dump, f"{reg} missing from dump:\n{dump}"
    assert "KERNEL ALIVE" not in run.output
