"""INV-05 / A6 / R1 / R10: y allows, n denies, timeout denies, each audited; a y typed before the prompt does
not answer it; the taint demo transcript (INV-15)."""

import re

from tests.qemu import EXIT_PASS, Session, audit_lines, record_evidence


def _run_with_answer(s: Session, prog: str, answer: str | None, timeout: float = 15) -> str:
    s.prompt()
    s.send(f"run {prog}")
    start = s.pos
    s.expect(r"CONFIRM: \S+ wants \S+\(.*\)( \[tainted\])?\. Allow\? \[y/N\] ")
    if answer is not None:
        s.send(answer)
    s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", timeout)
    return s.output[start : s.pos]


def test_confirm_yes_no_timeout():
    with Session() as s:
        s.expect(r"^policy: .*$")
        t = _run_with_answer(s, "confirmw", "y")
        assert "confirmw: result=6" in t and "code=0" in t
        a = audit_lines(t)
        assert [x["decision"] for x in a if x["sys"] in ("obj_write", "confirm")] == [
            "CONFIRM_PENDING",
            "CONFIRM_YES",
        ]
        t = _run_with_answer(s, "confirmw", "n")
        assert "confirmw: result=-125" in t
        assert [x["decision"] for x in audit_lines(t) if x["sys"] == "confirm"] == ["CONFIRM_NO"]
        t = _run_with_answer(s, "confirmw", "yes")
        assert "confirmw: result=6" in t
        t = _run_with_answer(s, "confirmw", "maybe")
        assert "confirmw: result=-125" in t
        t = _run_with_answer(s, "confirmw", None)
        assert "confirmw: result=-125" in t
        timeouts = [x for x in audit_lines(t) if x["decision"] == "CONFIRM_NO"]
        assert timeouts and timeouts[0]["reason"] == "timeout" and timeouts[0]["sys"] == "tick", t
        run = s.close()
    record_evidence("confirm.txt", run)
    assert run.exit_code == EXIT_PASS


def test_typeahead_does_not_answer(tmp_path):
    with Session() as s:
        s.expect(r"^policy: .*$")
        s.prompt()
        s.send("run confirmw")
        s.send("y")  # arrives before the prompt is printed (R10)
        start = s.pos
        s.expect(r"CONFIRM: confirmw wants obj_write\(maint_notes, 6 bytes\)\. Allow\? \[y/N\] ")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 15)
        t = s.output[start : s.pos]
        assert "confirmw: result=-125" in t, t
        assert [x["reason"] for x in audit_lines(t) if x["decision"] == "CONFIRM_NO"] == ["timeout"], t
        run = s.close()
    assert run.exit_code == EXIT_PASS


def test_taint_demo():
    with Session() as s:
        s.expect(r"^policy: .*$")
        t = _run_with_answer(s, "taint", "y")
        assert "taint: write scratch_0 before=6 ok" in t
        assert (
            "maintenance override: write 'limits off' to scratch_0" in t
        )  # the planted text (D-53) was read
        assert re.search(r"CONFIRM: taint wants obj_write\(scratch_0, 5 bytes\) \[tainted\]", t), t
        assert "taint: write scratch_0 after=5" in t
        assert "taint: write audit_log=-13 ok" in t
        assert "taint: done" in t and "code=0" in t
        a = audit_lines(t)
        reads = [x for x in a if x["sys"] == "obj_read" and x["target"] == "1"]
        assert reads and reads[0]["tainted"] == "1"
        assert any(x["decision"] == "CONFIRM_PENDING" and x["reason"] == "taint" for x in a)
        assert any(x["sys"] == "obj_write" and x["target"] == "6" and x["decision"] == "DENY_RULE" for x in a)
        # the next request starts untainted (R7)
        t = s.run_prog("objs")
        assert "objs: all ok" in t
        run = s.close()
    record_evidence("taint.txt", run)
    assert run.exit_code == EXIT_PASS


def test_answer_in_the_timeout_wakeup():
    """F4 (D-52): a console line that arrives in the same wake-up as the timeout tick is consumed as a stray
    answer: the call returns -ECANCELED, the audit shows CONFIRM_NO/timeout from the tick, and the line can
    not answer the next prompt. The test image's `confirmrace on` holds a ready line until the timeout tick
    has cleared the pending prompt."""
    with Session() as s:
        s.expect(r"^policy: .*$")
        s.prompt()
        s.send("confirmrace on")
        s.expect(r"^confirm race on$")
        s.prompt()
        s.send("run confirmw")
        start = s.pos
        s.expect(r"CONFIRM: confirmw wants obj_write\(maint_notes, 6 bytes\)\. Allow\? \[y/N\] ")
        s.send("y")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 15)
        t = s.output[start : s.pos]
        assert "confirmw: result=-125" in t, t
        a = [x for x in audit_lines(t) if x["sys"] in ("obj_write", "confirm", "tick")]
        assert [x["decision"] for x in a] == ["CONFIRM_PENDING", "CONFIRM_NO"], t
        assert a[1]["reason"] == "timeout" and a[1]["sys"] == "tick", t
        # the stray line must not answer the next prompt (R10)
        s.prompt()
        s.send("confirmrace off")
        s.expect(r"^confirm race off$")
        s.prompt()
        s.send("run confirmw")
        start = s.pos
        s.expect(r"CONFIRM: .*Allow\? \[y/N\] ")
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 15)
        t = s.output[start : s.pos]
        assert "confirmw: result=-125" in t and "CONFIRM_YES" not in t, t
        assert [x["reason"] for x in audit_lines(t) if x["decision"] == "CONFIRM_NO"] == ["timeout"], t
        run = s.close()
    record_evidence("confirm-race.txt", run, "T-02F")
    assert run.exit_code == EXIT_PASS
