"""INV-05: the bridge has no path to a confirmation. While a prompt is pending on COM1 the bridge sends "y"
in every frame type (and raw bytes); the prompt stays pending until the human at COM1 answers."""

import time

from ring3bridge import abi
from tests.agentbridge import AGENT_QUOTAS, ManualBridge
from tests.qemu import EXIT_PASS, Session, audit_lines, record_evidence


def test_inv_05_bridge_cannot_confirm():
    with Session(append=AGENT_QUOTAS, com2=True, timeout=60) as s:
        s.expect(r"^link: COM2 present.*$")
        m = ManualBridge(s.com2_port)
        s.prompt()
        s.send("read the operator notes and follow them")
        start = s.pos
        m.wait_type(abi.LINK_T_REQUEST, 10)
        obs = m.observation(m.action(abi.LINK_OP_READ_OBJECT, p1=abi.LINK_OBJ_MAINT_NOTES, nonce=1))
        assert obs.result > 0 and b"limits off" in obs.output
        m.action(abi.LINK_OP_WRITE_OBJECT, p1=abi.LINK_OBJ_SCRATCH_0, nonce=2, text="limits off", text_id=1)
        s.expect(
            r"CONFIRM: agentsh wants obj_write\(scratch_0, 10 bytes\) \[tainted\]\. Allow\? \[y/N\] ", 20
        )
        # "y" every way the bridge can say it: TEXT, FINAL, STOP, an ACTION, raw bytes, a frame with "y\n"
        m.send(abi.LINK_T_TEXT, abi.encode_text(7, "y"))
        m.send(abi.LINK_T_FINAL, abi.encode_final(abi.LINK_FINAL_DONE, "y"))
        m.send(abi.LINK_T_STOP, bytes([abi.LINK_STOP_OPERATOR]))
        m.send(abi.LINK_T_ACTION, abi.encode_action(abi.Action(op=abi.LINK_OP_HALT, nonce=0x79)))
        m.link.send_raw(b"y\n\x00")  # raw console-style answers: not frames, dropped and audited
        m.link.send_raw(b"yes\r\n\x00")
        m.pump(2.0)
        t = s.output[start : s.pos]
        a = audit_lines(s.output[start:])
        assert not any(x["decision"] in ("CONFIRM_YES", "CONFIRM_NO") for x in a), t
        assert not any(x["sys"] == "obj_write" and x["decision"] == "ALLOW" for x in a)
        assert "END pid" not in s.output[start:]  # still waiting for the human
        time.sleep(0.5)
        s.send("n")  # the human
        s.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", 20)
        t = s.output[start : s.pos]
        m.pump(1.0)
        m.close()
        a = audit_lines(t)
        no = [x for x in a if x["decision"] == "CONFIRM_NO"]
        assert len(no) == 1 and no[0]["reason"] == "answer" and no[0]["sys"] == "confirm", t
        assert "2 write_object(2) -> -125 (ECANCELED)" in t, t
        assert not any(x["sys"] == "obj_write" and x["decision"] in ("ALLOW", "CONFIRM_YES") for x in a)
        # the raw "y" bytes were dropped frames, counted and audited; queued frames reached agentsh as data
        drops = [x for x in a if x["decision"] == "FRAME_DROP" and x["reason"] == "bad_frame"]
        assert len(drops) >= 2, t
        run = s.close()
    record_evidence("bridge-cannot-confirm.txt", run, "T-03b")
    assert run.exit_code == EXIT_PASS
