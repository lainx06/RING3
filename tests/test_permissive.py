"""A8 / ARCHITECTURE §7: the permissive build is labelled on the banner and on every audit line, confirmations
auto-answer yes, and its policy hash differs from the default build's."""

import re

from tests.qemu import EXIT_PASS, KERNEL_TEST, KERNEL_TEST_PERMISSIVE, Session, audit_lines, record_evidence


def _policy_hash(kernel) -> str:
    with Session(kernel) as s:
        m = s.expect(r"^policy: (.+) sha256=([0-9a-f]{64})$")
        s.prompt()
        s.close()
    return m.group(1), m.group(2)


def test_permissive_labelled_and_different_hash():
    default_name, default_hash = _policy_hash(KERNEL_TEST)
    assert default_name == "default"
    with Session(KERNEL_TEST_PERMISSIVE) as s:
        m = s.expect(r"^policy: (.+) sha256=([0-9a-f]{64})$")
        assert m.group(1) == "PERMISSIVE (EVAL ONLY)"
        assert m.group(2) != default_hash
        t = s.run_prog("confirmw")  # no prompt in the permissive build
        assert "CONFIRM:" not in t and "confirmw: result=6" in t, t
        t2 = s.run_prog("taint")
        assert "CONFIRM:" not in t2 and "taint: write scratch_0 after=5" in t2, t2
        run = s.close()
    record_evidence("permissive.txt", run)
    assert run.exit_code == EXIT_PASS
    audits = audit_lines(run.output)
    assert len(audits) > 10
    for line in run.output.splitlines():
        if line.startswith("AUDIT "):
            assert line.endswith(" PERMISSIVE (EVAL ONLY)"), line
    assert re.search(r"reason=permissive", run.output)
