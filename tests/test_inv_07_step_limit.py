"""INV-07 / A7 (D-53): the kernel itself ends the request when a 9th ACTION arrives, whatever the executor
does. The test image's `agent-nocap` runs agentsh without its own 8-action cap."""

import re

from ring3bridge import abi
from tests.agentbridge import AGENT_QUOTAS, ManualBridge
from tests.qemu import EXIT_PASS, Session, audit_lines, record_evidence


def test_ninth_action_is_limit_in_ring0():
    with Session(append=AGENT_QUOTAS, com2=True, timeout=60) as s:
        s.expect(r"^link: COM2 present.*$")
        m = ManualBridge(s.com2_port)
        s.prompt()
        s.send("agent-nocap keep polling")
        start = s.pos
        m.wait_type(abi.LINK_T_REQUEST, 10)
        for i in range(1, 9):
            obs = m.observation(m.action(abi.LINK_OP_CELL_STATUS, nonce=100 + i))
            assert obs.result == -abi.ERRNO["ENOSYS"]
        m.action(abi.LINK_OP_CELL_STATUS, nonce=109)  # the 9th
        s.expect(r"^END pid=\d+ LIMIT ticks=\d+ uptime=\d+$", 20)
        t = s.output[start : s.pos]
        m.pump(1.0)
        assert "agentsh: action 8 cell_status" in t and "agentsh: action 9" not in t, t
        assert "OUTCOME" not in t  # ended by the kernel, not by agentsh
        a = audit_lines(t)
        lim = [x for x in a if x["decision"] == "LIMIT" and x["reason"] == "action_cap"]
        assert len(lim) == 1 and lim[0]["sys"] == "action" and lim[0]["target"] == "9", t
        # the 9th frame sat in the queue and was flushed at EXEC_END
        assert any(x["decision"] == "FRAME_DROP" and x["reason"] == "flushed" for x in a), t
        la = m.audits_named()
        assert any(x["decision"] == "LIMIT" and x["reason"] == "action_cap" for x in la)
        ends = [e for e in m.events() if e.kind == abi.EV_EXEC_END]
        assert ends and ends[-1].sub == 2  # END_LIMIT
        actions = [e for e in m.events() if e.kind == abi.EV_ACTION]
        assert len(actions) == 9
        # the next request works
        s.prompt()
        s.send("list the objects")
        m.wait_type(abi.LINK_T_REQUEST, 10)
        obs = m.observation(m.action(abi.LINK_OP_LIST_OBJECTS, nonce=7))
        assert obs.result > 0
        m.send(abi.LINK_T_FINAL, abi.encode_final(abi.LINK_FINAL_DONE, "ok"))
        s.expect(r"^OUTCOME DONE: ok$", 20)
        s.expect(r"^END pid=\d+ EXIT ticks=\d+ uptime=\d+$", 20)
        m.close()
        run = s.close()
    record_evidence("step-limit-ring0.txt", run, "T-03b")
    assert run.exit_code == EXIT_PASS


def test_text_id_mismatch_is_einval_in_ring3():
    with Session(append=AGENT_QUOTAS, com2=True, timeout=60) as s:
        s.expect(r"^link: COM2 present.*$")
        m = ManualBridge(s.com2_port)
        s.prompt()
        s.send("write a note")
        start = s.pos
        m.wait_type(abi.LINK_T_REQUEST, 10)
        # TEXT id 5, ACTION says 6 -> -EINVAL, no syscall
        m.send(abi.LINK_T_TEXT, abi.encode_text(5, "hello"))
        m.send(
            abi.LINK_T_ACTION,
            abi.encode_action(abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=2, nonce=11, text_id=6)),
        )
        assert m.observation(11).result == -abi.ERRNO["EINVAL"]
        # ACTION with text but no TEXT before it
        m.send(
            abi.LINK_T_ACTION,
            abi.encode_action(abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=2, nonce=12, text_id=1)),
        )
        assert m.observation(12).result == -abi.ERRNO["EINVAL"]
        # TEXT, then an unrelated frame, then the ACTION: the binding is only to the frame right before
        m.send(abi.LINK_T_TEXT, abi.encode_text(3, "hello"))
        m.send(abi.LINK_T_ACTION, abi.encode_action(abi.Action(op=abi.LINK_OP_CELL_STATUS, nonce=13)))
        assert m.observation(13).result == -abi.ERRNO["ENOSYS"]
        m.send(
            abi.LINK_T_ACTION,
            abi.encode_action(abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=2, nonce=14, text_id=3)),
        )
        assert m.observation(14).result == -abi.ERRNO["EINVAL"]
        # the matching pair works
        m.send(abi.LINK_T_TEXT, abi.encode_text(9, "hello"))
        m.send(
            abi.LINK_T_ACTION,
            abi.encode_action(abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=2, nonce=15, text_id=9)),
        )
        assert m.observation(15).result == 5
        m.send(abi.LINK_T_FINAL, abi.encode_final(abi.LINK_FINAL_DONE, "note written"))
        s.expect(r"^END pid=\d+ EXIT ticks=\d+ uptime=\d+$", 20)
        t = s.output[start : s.pos]
        m.close()
        a = audit_lines(t)
        writes = [x for x in a if x["sys"] == "obj_write"]
        assert len(writes) == 1 and writes[0]["decision"] == "ALLOW", (
            t
        )  # only the matching pair reached the kernel
        assert len(re.findall(r"rejected in ring 3 \(field rule\)", t)) == 3, t
        run = s.close()
    record_evidence("text-id-mismatch.txt", run, "T-03b")
    assert run.exit_code == EXIT_PASS
