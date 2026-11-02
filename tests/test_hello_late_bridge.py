"""A7 (D-53): with `wait=off` the bridge may connect long after boot; the kernel re-sends HELLO every 5 s
until the bridge speaks, so a late bridge still gets one within 5 s."""

import time

from ring3bridge import abi
from tests.agentbridge import ManualBridge
from tests.qemu import EXIT_PASS, KERNEL, Session


def test_hello_arrives_for_a_bridge_connecting_10s_after_boot():
    with Session(KERNEL, append="", com2=True, timeout=60) as s:
        s.expect(r"^link: COM2 present.*$")
        time.sleep(10.0)
        m = ManualBridge(s.com2_port)
        t0 = time.monotonic()
        h = m.wait_type(abi.LINK_T_HELLO, 5.5)
        waited = time.monotonic() - t0
        assert h.msg.abi_version == abi.LINK_ABI_VERSION and h.msg.variant == abi.LINK_VARIANT_DEFAULT
        assert waited < 5.5
        # once the bridge has spoken (any valid frame), HELLO stops; heartbeats continue
        m.send(abi.LINK_T_STOP, bytes([abi.LINK_STOP_OPERATOR]))  # outside a request: queued then flushed
        m.pump(6.0)
        later = [f for f in m.frames if f.type == abi.LINK_T_HELLO]
        assert len(later) == 1, [abi.TYPE_NAMES[f.type] for f in m.frames]
        assert sum(1 for f in m.frames if f.type == abi.LINK_T_HEARTBEAT) >= 5
        m.close()
        run = s.close()
    assert run.exit_code == EXIT_PASS
