"""T-07 Part B on the simulated cell (QEMU + COM2 + COM3 sim): the witness op in the real run loop, and
`ring3 calibrate`'s scripted model through the real kernel. No network, no key, no camera: the camera renders
the synthetic gauge with the arm where the simulated board says the servo is (or where the test holds it);
SAM is a fake transport answering for that frame; the ledger is a test ledger (one image reserved per post).

INV-14: a witness op sends no COM2 frame and changes no kernel decision; CAN'T VERIFY is never upgraded.
INV-09: a SAM 401 stops the request and the run."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

from ring3bridge import abi, eventlog
from ring3bridge.calibration import CalibrationScript, plan
from ring3bridge.models.transport import TransportResponse
from ring3bridge.witness import calibrate
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.service import Witness
from tests.agentbridge import AGENT_QUOTAS, BridgeThread, call, done, runs_dir
from tests.cellharness import END, start
from tests.qemu import EXIT_PASS, audit_lines, record_evidence

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "hardware" / "gauge")]  # synth draws the gauge

from tests.witness import synth  # noqa: E402
from tests.witness.fakes import make_client, ok  # noqa: E402
from tests.witness.synth import OFFSET_DEG, Scene  # noqa: E402

GEOM = load_geometry()


class SimCamera:
    """A fresh synthetic frame of the arm at the simulated board's servo angle (+ the mounting offset), or at
    ``held`` (a hand holding the arm). Queues the matching SAM reply (or ``sam_reply``) on the fake
    transport."""

    def __init__(self, sim, transport, held: float | None = None, sam_reply: TransportResponse | None = None):
        self.sim, self.t, self.held, self.sam_reply = sim, transport, held, sam_reply
        self.shots: list[float] = []

    def __call__(self):
        deg = self.held if self.held is not None else self.sim.servo + OFFSET_DEG
        self.shots.append(deg)
        frame, _, labels = synth.render(Scene([deg], quad="mild", light="gradient", seed=len(self.shots)))
        self.t.responses.append(self.sam_reply or ok(synth.envelope(synth.line_for(frame, labels, GEOM))))
        return frame, cv2.imencode(".jpg", frame)[1].tobytes()


def later(text: str, s: float = 0.3):
    """A reply given after ``s`` seconds (the kernel wants >= 200 ms between servo commands)."""

    def f() -> str:
        time.sleep(s)
        return text

    return f


def _request(c, text: str, timeout: float = 40.0) -> str:
    c.s.prompt()
    c.s.send(text)
    start_pos = c.s.pos
    c.s.expect(END, timeout)
    return c.s.output[start_pos : c.s.pos]


def _b2k(b: BridgeThread) -> list[str]:
    return [r["ftype"] for r in b.records() if r["type"] == "frame" and r["dir"] == "b2k"]


def _witness_records(b: BridgeThread) -> list[dict]:
    return [r for r in b.records() if r["type"] == "witness"]


def _setup(tmp_path, sim, **cam_kw):
    d = tmp_path / "cal-fixture"
    d.mkdir()
    cal, _ = synth.calibrate(GEOM, d)
    client, t, ledger = make_client(tmp_path)
    cam = SimCamera(sim, t, **cam_kw)
    return Witness(cam, client, cal, GEOM), cam, t, ledger


def test_witness_op_pass_no_move_and_no_frame(tmp_path):
    """Three requests: witness before any move -> NO_MOVE (no frame taken, no spend); LOW in two moves then
    witness -> PASS; a refused move then witness -> NO_MOVE. Each witness sends zero COM2 frames."""
    with start(append=AGENT_QUOTAS, com2=True, timeout=120) as c:
        w, cam, t, ledger = _setup(tmp_path, c.sim)
        replies = [
            call("witness"), done("nothing moved; witness could not confirm"),
            call("servo_set", deg=60), later(call("servo_set", deg=50)), call("witness"),
            done("gauge at 50, witness PASS"),
            later(call("servo_set", deg=130)), call("witness"), done("servo 130 refused; not confirmed"),
        ]  # fmt: skip
        b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, "witness"), replies, requests=3, pack="agent-v3",
                         witness=w.check).start()  # fmt: skip
        t1 = _request(c, "check the gauge with the camera")
        assert cam.shots == [] and t.posts == [] and ledger.committed_uusd() == 0
        t2 = _request(c, "move the gauge to LOW and confirm it really moved")
        assert c.sim.servo == 50 and len(cam.shots) == 1 and len(t.posts) == 1
        t3 = _request(c, "move the gauge to HIGH and confirm")
        b.join()
        run = c.close()
    record_evidence("sim-witness-loop.txt", run, "T-07")
    assert run.exit_code == EXIT_PASS
    for t_ in (t1, t2, t3):
        assert "OUTCOME DONE" in t_, t_
    # the kernel saw no witness: agentsh executed only the servo moves (0 + 2 + 1 ACTIONs)
    assert "EXECUTED 0 operation(s)" in t1
    assert "1 servo_set(60) -> 0" in t2 and "2 servo_set(50) -> 0" in t2
    a3 = [x for x in audit_lines(t3) if x["sys"] == "cell_servo"]
    assert [(x["decision"], x["reason"]) for x in a3] == [("DENY_LIMIT", "servo_step")]
    # the bridge: exactly one ACTION per servo_set, nothing for the three witness ops
    assert _b2k(b) == ["FINAL", "ACTION", "ACTION", "FINAL", "ACTION", "FINAL"]
    ws = _witness_records(b)
    assert [(r["verdict"], r["reason"]) for r in ws] == [
        ("CAN'T VERIFY", "NO_MOVE"),
        ("PASS", "ZONE_MATCH"),
        ("CAN'T VERIFY", "NO_MOVE"),
    ]
    assert ws[0]["commanded_deg"] == -1 and "no kernel-allowed servo_set" in ws[0]["detail"]
    assert ws[2]["commanded_deg"] == -1  # 130 was refused, so this request has no allowed move
    p = ws[1]
    assert (p["commanded_deg"], p["commanded_zone"], p["measured_zone"], p["tele_servo"]) == (
        50,
        "LOW",
        "LOW",
        50,
    )
    assert type(p["dial_mdeg"]) is int and type(p["calibrated_mdeg"]) is int and p["spend_uusd"] == 2500
    assert {"frame_blob", "crop_blob", "sam_line_blob"} <= p.keys() and p["ms"] >= 500  # settled + 500 ms
    assert ledger.settled_uusd() == 2500 and ledger.unsettled_uusd() == 0
    # what the model saw for the PASS
    prompts = b.model.prompts()
    assert "op=witness result=0\nPASS\nreason=ZONE_MATCH commanded_deg=50 commanded_zone=LOW" in prompts[5]
    assert "op=witness result=0\nCAN'T VERIFY\nreason=NO_MOVE" in prompts[1]
    assert eventlog.verify(b.run_dir).ok


def test_witness_held_arm_fails_and_the_model_halts(tmp_path):
    """A hand holds the arm at the park angle while the model moves to LOW: the board reports servo 50 (the
    command), the camera sees 90 -> FAIL (never PASS); the scripted model halts."""
    with start(append=AGENT_QUOTAS, com2=True, timeout=120) as c:
        w, cam, t, _ = _setup(tmp_path, c.sim, held=90.0 + OFFSET_DEG)
        replies = [call("servo_set", deg=60), later(call("servo_set", deg=50)), call("witness"), call("halt"),
                   done("witness FAIL: gauge did not reach LOW; halted")]  # fmt: skip
        b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, "held"), replies, pack="agent-v3",
                         witness=w.check).start()  # fmt: skip
        tr = _request(c, "move the gauge to LOW and confirm it really moved")
        b.join()
        run = c.close()
    record_evidence("sim-witness-held.txt", run, "T-07")
    assert run.exit_code == EXIT_PASS
    assert "OUTCOME DONE" in tr and "3 halt(0) -> 0" in tr, tr
    (r,) = _witness_records(b)
    assert (r["verdict"], r["reason"], r["commanded_zone"], r["measured_zone"]) == (
        "FAIL",
        "WRONG_ZONE",
        "LOW",
        "MID",
    )
    assert r["tele_servo"] == 50  # the board says it moved; the camera says it did not
    assert "\nFAIL\nreason=WRONG_ZONE" in b.model.prompts()[3]
    assert _b2k(b).count("ACTION") == 3


def test_sam_401_stops_the_request_and_the_run(tmp_path):
    """INV-09: SAM answers 401 -> STOP (model_auth) to the kernel, the request is STOPPED, the bridge serves
    no further request; nothing is turned into CAN'T VERIFY."""
    with start(append=AGENT_QUOTAS, com2=True, timeout=120) as c:
        w, _, t, ledger = _setup(
            tmp_path, c.sim, sam_reply=TransportResponse(401, b'{"error":"unauthorized"}')
        )
        replies = [call("servo_set", deg=60), call("witness")]
        b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, "sam401"), replies, requests=2,
                         witness=w.check).start()  # fmt: skip
        tr = _request(c, "move the gauge to 60 and confirm")
        b.join()
        run = c.close()
    assert run.exit_code == EXIT_PASS
    assert "STOPPED by the bridge" in tr, tr
    stops = [r for r in b.records() if r["type"] == "stop_sent"]
    assert [(s["reason"], s["detail"]) for s in stops] == [(abi.LINK_STOP_MODEL_AUTH, "SAM: HTTP 401")]
    assert _witness_records(b) == [] and b.request_ends()[0]["outcome"] == "STOPPED"
    assert len(t.posts) == 1 and ledger.unsettled_uusd() == 0  # reserved, posted once, settled
    assert b.runner.requests_done == 1  # requests=2 was asked for: the run stopped after the first


def test_calibration_script_through_the_kernel(tmp_path):
    """`ring3 calibrate`'s scripted model on the simulated cell: from the board's STATE servo (90), the D-72
    plan in <= 30 deg steps; every move ALLOWed by the kernel (no rate or step denial), one SAM image per
    point, the fit under the unchanged limit."""
    with start(append=AGENT_QUOTAS, com2=True, timeout=120) as c:
        client, t, ledger = make_client(tmp_path)
        cam = SimCamera(c.sim, t)
        script = CalibrationScript(Witness(cam, client, calibrate.NONE, GEOM))
        b = BridgeThread(c.s.com2_port, runs_dir(tmp_path, "cal"), [], client=script, witness=script.measure,
                         max_actions=script.max_actions())  # fmt: skip
        script.runner = b.runner
        b.start()
        tr = _request(c, "calibrate the witness", timeout=90)
        b.join(90)
        run = c.close()
    record_evidence("sim-calibrate.txt", run, "T-07")
    assert run.exit_code == EXIT_PASS
    assert "OUTCOME DONE: calibration moves done: 5 of 5 points measured" in tr, tr
    moves = [d for d, _ in plan(90)]
    servo = [x for x in audit_lines(tr) if x["sys"] == "cell_servo"]
    assert [(x["decision"], int(x["target"])) for x in servo] == [("ALLOW", d) for d in moves]
    assert script.start_deg == 90 and [p for p, _ in script.pairs] == [60, 70, 90, 110, 120]
    cal = calibrate.fit(script.pairs)
    assert cal.ok, cal.message()
    assert len(t.posts) == 5 and ledger.settled_uusd() == 5 * 2500
    assert _b2k(b).count("ACTION") == len(moves)  # the moves only; the 5 witness ops send nothing
    assert len(_witness_records(b)) == 5
