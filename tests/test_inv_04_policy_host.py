"""INV-04 / INV-15 / INV-16 / A5: the policy source compiled for the host passes its unit tests
(one per object-table row, per syscall-capability row, per device rule and per design rule R1-R8)."""

import re
import subprocess

from tests.qemu import EVIDENCE, HOST_POLICY_TEST, HOST_POLICY_TEST_PERMISSIVE, REPO


def test_host_policy_tests():
    p = subprocess.run([str(HOST_POLICY_TEST)], cwd=REPO, capture_output=True, text=True, timeout=30)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "policy-host.txt").write_text(
        f"$ build/host/policy_test\n# exit code {p.returncode}\n\n{p.stdout}"
    )
    assert p.returncode == 0, p.stdout + p.stderr
    m = re.search(r"^policy_test: (\d+) checks, (\d+) failed$", p.stdout, re.M)
    assert m and m.group(2) == "0" and int(m.group(1)) >= 150, p.stdout
    for name in (
        "object_table",
        "syscall_caps",
        "default_deny",
        "no_escalation",
        "taint_forces_confirm",
        "taint_not_clearable",
        "halt_always",
        "fan_rules",
        "servo_rules",
        "interlock",
        "stale",
        "estop",
        "tick_autohalt",
        "R1_",
        "R2_",
        "R3_",
        "R4_",
        "R5_",
        "R6_",
        "R7_",
        "R8_",
        "F3_",
        "F7_",
        "F8_",
    ):
        assert re.search(rf"^ok .*{name}", p.stdout, re.M), f"{name} missing:\n{p.stdout}"


def test_host_policy_permissive_variant():
    p = subprocess.run(
        [str(HOST_POLICY_TEST_PERMISSIVE)], cwd=REPO, capture_output=True, text=True, timeout=30
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "PERMISSIVE (EVAL ONLY)" in p.stdout
