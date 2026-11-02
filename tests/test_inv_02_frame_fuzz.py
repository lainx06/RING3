"""INV-02 / A2: >= 10,000 seeded malformed COM2 frames at the production kernel: 0 panics, the heartbeat
continues, every drop counted (HEARTBEAT.frames_dropped) and audited (one FRAME_DROP per frame)."""

import os

from ring3bridge.fuzz import run_fuzz
from ring3bridge.link import Link
from tests.qemu import EXIT_PASS, KERNEL, Session, evidence_dir, record_evidence

FRAMES = int(os.environ.get("RING3_FUZZ_FRAMES", "10000"))
SEED = int(os.environ.get("RING3_FUZZ_SEED", "20261102"))


def test_inv_02_frame_fuzz():
    with Session(KERNEL, append="", com2=True, timeout=120) as s:
        s.expect(r"^link: COM2 present.*$")
        s.prompt()
        link = Link("127.0.0.1", s.com2_port)
        link.connect(10.0)
        try:
            res = run_fuzz(link, SEED, FRAMES, settle_s=60.0)
        finally:
            link.close()
        (evidence_dir("T-03b") / "fuzz.txt").write_text(res.report() + "\n")
        assert res.sent == FRAMES
        assert res.ok(), res.report()
        s.send("status")
        st = s.expect(r"^link: .*$", 10).group(0)
        assert f"frames_dropped={FRAMES}" in st and "tx_dropped=0" in st and "stalled=0" in st, st
        s.prompt()
        run = s.close()
    record_evidence("fuzz-com1.txt", run, "T-03b")
    assert run.exit_code == EXIT_PASS
    assert not run.has_panic()
