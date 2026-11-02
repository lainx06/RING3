"""A7 (seed of INV-10 replay): a scripted QEMU run prints its event tape and decisions; the same TAPE lines
fed to build/host/policy_test --replay (the same policy source compiled for the host) must reproduce every
audited decision in order."""

import subprocess

from tests.qemu import EXIT_PASS, HOST_POLICY_TEST, REPO, Session, audit_lines, evidence_dir, record_evidence


def _scripted_run() -> str:
    with Session(timeout=40) as s:
        s.expect(r"^policy: .*$")
        s.run_prog("objs")
        s.run_prog("null")  # PROC_FAULT
        s.run_prog("lcd")
        s.run_prog("nocap")
        s.prompt()
        s.send("run taint")
        s.expect(r"CONFIRM: .*Allow\? \[y/N\] ")
        s.send("y")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        s.prompt()
        s.send("run confirmw")
        s.expect(r"CONFIRM: .*Allow\? \[y/N\] ")
        s.send("n")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        s.prompt()
        s.send("run fan")
        s.expect(r"CONFIRM: fan wants cell_fan\(60\)\. Allow\? \[y/N\] ")
        s.send("y")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$")
        for line in (
            "tele 300 0",
            "estop reset",
            "tele 300 1 bad",
            "tele 300 0",
            "estop reset",
            "estop reset",
        ):
            s.prompt()
            s.send(line)
        s.run_prog("spin")  # LIMIT on a tick
        s.prompt()
        s.send("run confirmw")  # timeout on a tick
        s.expect(r"CONFIRM: .*Allow\? \[y/N\] ")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 15)
        run = s.close()
    record_evidence("replay-match.txt", run)
    assert run.exit_code == EXIT_PASS
    return run.output


def test_kernel_and_host_decisions_match():
    out = _scripted_run()
    tape = [line for line in out.splitlines() if line.startswith("TAPE ")]
    kernel = audit_lines(out)
    assert len(tape) > 100 and len(kernel) > 60, out
    p = subprocess.run(
        [str(HOST_POLICY_TEST), "--replay"],
        input="\n".join(tape) + "\n",
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    host = audit_lines(p.stdout)
    (evidence_dir("T-02") / "replay-host.txt").write_text(p.stdout)
    assert len(host) == len(kernel), f"kernel {len(kernel)} audits, host {len(host)}"
    for i, (k, h) in enumerate(zip(kernel, host, strict=True)):
        for field in ("seq", "tick", "sys", "target", "decision", "reason", "tainted"):
            assert k[field] == h[field], f"MISMATCH at #{i} field {field}: kernel {k} host {h}"
        if k["decision"] not in ("ALLOW", "CONFIRM_YES"):
            assert k["result"] == h["result"], f"MISMATCH at #{i} result: kernel {k} host {h}"
    decisions = {k["decision"] for k in kernel}
    for want in (
        "ALLOW",
        "DENY_CAP",
        "DENY_RULE",
        "DENY_ARG",
        "DENY_FAULT",
        "DENY_LIMIT",
        "DENY_INTERLOCK",
        "DENY_STALE",
        "DENY_ESTOP",
        "CONFIRM_PENDING",
        "CONFIRM_YES",
        "CONFIRM_NO",
        "FRAME_DROP",
        "PROC_FAULT",
        "LIMIT",
        "AUTO_HALT",
    ):
        assert want in decisions, f"{want} never exercised in the scripted run: {sorted(decisions)}"
    print(f"REPLAY MATCH: {len(kernel)} decisions, {len(tape)} tape events")
