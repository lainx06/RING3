"""In-kernel unit tests (KTEST=1 build) all pass and cover the required areas."""

import re

from tests.qemu import EXIT_PASS, KERNEL_KTEST, record_evidence, run_qemu

REQUIRED = {
    "crc16_check_value",
    "cobs_zero_bytes",
    "cobs_runs_253_254_255",
    "cobs_max_frame",
    "cobs_rejects_malformed",
    "pmm_alloc_free",
    "pmm_double_free_detected",
    "mmap_parsing",
    "kmalloc_reuse_after_free",
    "kmalloc_double_free_detected",
    "uaccess_bounds",
    "uaccess_pde_bits",
    "uaccess_copy_string",
    "objstore_audit_log_keeps_newest",
    "string_helpers",
    "printk_formats",
}


def test_ktests_pass():
    run = run_qemu(KERNEL_KTEST, append="ktest testexit", timeout=20, expect_panic=True)
    record_evidence("ktest.txt", run)
    # The only acceptable PANIC lines are the ones the double-free tests deliberately provoke and trap.
    panics = [line for line in run.lines() if line.startswith("PANIC:")]
    assert all("double free" in line for line in panics), run.output
    assert len(panics) == 2, run.output
    results = dict(re.findall(r"^KTEST (\S+) (PASS|FAIL)$", run.output, re.M))
    assert REQUIRED <= set(results), f"missing: {REQUIRED - set(results)}"
    assert all(v == "PASS" for v in results.values()), run.output
    m = re.search(r"^KTEST SUMMARY pass=(\d+) fail=(\d+)$", run.output, re.M)
    assert m and int(m.group(2)) == 0 and int(m.group(1)) == len(results)
    assert run.exit_code == EXIT_PASS, run.output
