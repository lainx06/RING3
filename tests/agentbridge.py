"""Fake-model bridge for the QEMU agent tests (T-03b step 6): the real bridge run loop over the real COM2
link, with the model replaced by a scripted transport. No network, no key, no paid call.

``BridgeThread`` runs ``Runner.serve`` next to a ``Session`` (COM1). ``ManualBridge`` is the same link
without the run loop, for tests that inject exact frames (mismatched text ids, late frames, a 9th ACTION,
"y" in every frame type during a prompt).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ring3bridge import abi, context_pack
from ring3bridge.config import ModelConfig, Price
from ring3bridge.eventlog import EventLog
from ring3bridge.framing import Frame, FrameError
from ring3bridge.ledger import Ledger
from ring3bridge.link import Link
from ring3bridge.models.muse_spark import MuseSparkClient
from ring3bridge.models.transport import TransportResponse
from ring3bridge.run import Runner, start_run
from tests.qemu import KERNEL_TEST, REPO

# Synthetic price (not Meta's): the ledger is exercised, nothing is billed.
FAKE_PRICE = Price(2_000_000, 8_000_000, "https://example.invalid/synthetic-test-price", "2026-11-02")
CFG = ModelConfig("muse-spark-1.3", "https://api.meta.ai/v1/responses", 512, 60, FAKE_PRICE)
SEED = bytes(range(16))
# Agent tests: 2 s per action, 30 s per request, 5 s to answer a prompt (ticks at 100 Hz).
AGENT_QUOTAS = "taction=200 trequest=3000 tconfirm=500"


def call(op: str, **args) -> str:
    return json.dumps({"action": "call", "op": op, "args": args}, separators=(",", ":"))


def done(summary: str) -> str:
    return json.dumps({"action": "done", "summary": summary}, separators=(",", ":"))


def refuse(reason: str) -> str:
    return json.dumps({"action": "refuse", "reason": reason}, separators=(",", ":"))


def responses_body(text: str, tin: int = 1000, tout: int = 20) -> bytes:
    """A reply in the shape of Meta's Responses API (D-55)."""
    return json.dumps(
        {"id": "resp_fake", "object": "response", "status": "completed", "model": CFG.id,
         "output": [{"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": text}]}],
         "usage": {"input_tokens": tin, "output_tokens": tout, "input_tokens_details": {"cached_tokens": 0}}}
    ).encode()  # fmt: skip


def incomplete_body(tin: int = 1700, tout: int = 2048) -> bytes:
    """A reply cut off at ``max_output_tokens`` while reasoning, in the shape the live run 20261102-122431
    recorded (D-55 docs): ``status: incomplete``, ``incomplete_details.reason``, a ``reasoning`` output item
    with no summary and no ``message`` item at all."""
    return json.dumps(
        {"id": "resp_fake", "object": "response", "status": "incomplete", "model": CFG.id,
         "incomplete_details": {"reason": "max_output_tokens"},
         "output": [{"type": "reasoning", "id": "rs_fake", "status": "incomplete", "summary": [],
                     "encrypted_content": "AAAA"}],
         "usage": {"input_tokens": tin, "output_tokens": tout, "input_tokens_details": {"cached_tokens": 0},
                   "output_tokens_details": {"reasoning_tokens": tout - 2}}}
    ).encode()  # fmt: skip


class ScriptedModel:
    """A transport whose replies are the given model texts (or raw Responses API bodies as ``bytes``), in
    order; records every request body. A callable entry is called for its text (it may sleep first: a slow
    model while the executor waits in agent_recv, T-04)."""

    def __init__(self, replies: list[str | bytes]):
        self.replies = list(replies)
        self.bodies: list[bytes] = []

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse:
        self.bodies.append(body)
        if not self.replies:
            raise AssertionError("the scripted model has no reply left: the bridge asked once too often")
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply()
        return TransportResponse(200, reply if isinstance(reply, bytes) else responses_body(reply))

    @property
    def calls(self) -> int:
        return len(self.bodies)

    def prompts(self) -> list[str]:
        return [json.loads(b)["input"] for b in self.bodies]


class BridgeThread:
    """The real run loop in a thread, serving `requests` requests from the kernel at `port`."""

    def __init__(
        self,
        port: int,
        run_dir: Path,
        replies: list[str],
        requests: int = 1,
        budget: int = 1_000_000,
        pack: str = context_pack.CURRENT,
        witness=None,  # T-07 Part B: the run loop's witness callable (deg -> Checked)
        client=None,  # T-07 Part B: a model client in place of the scripted Muse Spark (ring3 calibrate's)
        max_actions: int = abi.ACTIONS_PER_REQUEST,
    ):
        self.model = ScriptedModel(replies)
        self.log = EventLog(run_dir)
        self.run_dir = run_dir
        self.ledger = Ledger(30_000_000, run_dir.parent / "ledger.jsonl")
        self.client = client or MuseSparkClient(CFG, self.model, self.ledger, self.log, run_dir.name, budget)
        self.pack = context_pack.load(pack)
        start_run(self.log, run_dir.name, KERNEL_TEST, self.pack, CFG.id, SEED, budget)
        self.link = Link("127.0.0.1", port)
        self.timeline: list[str] = []
        self.runner = Runner(self.link, self.client, self.log, self.pack, SEED, timeline=self.timeline.append,
                             exec_end_wait_s=5.0, observation_wait_s=30.0, witness=witness,
                             max_actions=max_actions)  # fmt: skip
        self.requests = requests
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.link.connect(10.0)
            self.runner.serve(requests=self.requests, idle_s=40.0)
        except BaseException as e:  # noqa: BLE001 - surfaced to the test by join()
            self.error = e
        finally:
            try:
                self.runner.finish()
            finally:
                self.link.close()

    def start(self) -> BridgeThread:
        self.thread.start()
        return self

    def join(self, timeout: float = 60.0) -> None:
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "bridge thread still running:\n" + "\n".join(self.timeline)
        if self.error is not None:
            raise AssertionError(f"bridge thread failed: {self.error!r}\n" + "\n".join(self.timeline))

    def records(self) -> list[dict]:
        return [json.loads(x) for x in (self.run_dir / "events.jsonl").read_text().splitlines()]

    def audits(self) -> list[dict]:
        return [r for r in self.records() if r["type"] == "audit"]

    def request_ends(self) -> list[dict]:
        return [r for r in self.records() if r["type"] == "request_end"]


class ManualBridge:
    """The link without the run loop: send exact frames, collect what the kernel sends."""

    def __init__(self, port: int):
        self.link = Link("127.0.0.1", port)
        self.link.connect(10.0)
        self.frames: list[Frame] = []
        self.errors: list[FrameError] = []

    def pump(self, seconds: float) -> list[Frame]:
        new: list[Frame] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for item in self.link.recv(min(0.2, max(0.01, deadline - time.monotonic()))):
                if isinstance(item, Frame):
                    self.frames.append(item)
                    new.append(item)
                else:
                    self.errors.append(item)
        return new

    def wait_for(self, pred, timeout: float = 15.0) -> Frame:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for f in self.pump(0.2):
                if pred(f):
                    return f
        raise AssertionError(
            f"no matching frame within {timeout} s; got {[abi.TYPE_NAMES.get(f.type) for f in self.frames]}"
        )

    def wait_type(self, ftype: int, timeout: float = 15.0) -> Frame:
        return self.wait_for(lambda f: f.type == ftype, timeout)

    def send(self, ftype: int, body: bytes) -> None:
        self.link.send(ftype, body)

    def action(
        self, op: int, p1: int = 0, nonce: int = 1, text: str | None = None, text_id: int | None = None
    ) -> int:
        """TEXT (when given) then ACTION; returns the nonce."""
        tid = 0
        if text is not None:
            tid = text_id if text_id is not None else 1
            self.send(abi.LINK_T_TEXT, abi.encode_text(tid if text_id is None else max(1, tid), text))
        act = abi.Action(op=op, p1=p1, nonce=nonce, text_id=tid)
        self.send(abi.LINK_T_ACTION, abi.encode_action(act))
        return nonce

    def observation(self, nonce: int, timeout: float = 15.0) -> abi.Observation:
        f = self.wait_for(lambda f: isinstance(f.msg, abi.Observation) and f.msg.nonce == nonce, timeout)
        return f.msg

    def audit_list(self) -> list[abi.Audit]:
        return [f.msg for f in self.frames if isinstance(f.msg, abi.Audit)]

    def audits_named(self) -> list[dict]:
        out = []
        for a in self.audit_list():
            subject = (
                abi.POLICY_SYSCALL_NAMES[a.nr] if a.kind == abi.EV_SYSCALL else abi.POLICY_EVENT_NAMES[a.kind]
            )
            out.append({"seq": a.seq, "sys": subject, "decision": abi.POLICY_CODE_NAMES[a.code],
                        "reason": abi.POLICY_REASON_NAMES[a.reason], "target": a.target, "result": a.result,
                        "tainted": a.tainted})  # fmt: skip
        return out

    def events(self) -> list[abi.Event]:
        return [f.msg for f in self.frames if isinstance(f.msg, abi.Event)]

    def close(self) -> None:
        self.link.close()


def runs_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "runs" / name
    d.parent.mkdir(parents=True, exist_ok=True)
    return d


__all__ = ["REPO", "AGENT_QUOTAS", "BridgeThread", "ManualBridge", "call", "done", "refuse", "runs_dir"]
