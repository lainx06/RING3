"""The bridge run loop (ARCHITECTURE §6, T-03b step 5, D-53).

One ``Runner`` serves one QEMU over one COM2 link for one run directory:

  run_start -> (frames in: HELLO, HEARTBEAT, AUDIT, EVENT are logged as they come)
  REQUEST from the kernel -> user_request
    loop: build the prompt (context pack + request + prior actions and observations, observations labelled
          untrusted) -> reserve, call the model (every call, every retry) -> strict validation
          -> Call: TEXT (if any) + ACTION, wait for the OBSERVATION (or the kernel's EXEC_END)
          -> Done / Refuse: FINAL         -> two invalid replies in a row: STOP(invalid_replies)
          -> budget / transport / auth failure: STOP(reason)
          -> ACTIONS_PER_REQUEST actions sent: no further model call; the executor ends the request
    then read until the kernel's EXEC_END event (or 5 s) -> request_end
  run_end

Every frame in either direction is a ``frame`` record; every decoded AUDIT / EVENT is an ``audit`` /
``event`` record (the complete inbound tape, INV-10). The timeline callback gets one plain line per step.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ring3bridge import abi
from ring3bridge.actions import ActionSerializer
from ring3bridge.config import REPO
from ring3bridge.context_pack import ContextPack
from ring3bridge.eventlog import EventLog
from ring3bridge.framing import Frame, FrameError
from ring3bridge.link import Link, LinkError
from ring3bridge.models.muse_spark import STATUS_COMPLETED, ModelReply, ModelStop, MuseSparkClient
from ring3bridge.strictjson import Call, Done, Rejected, validate

POLICY_SOURCES = ("policy.h", "policy.c")
EXEC_END_WAIT_S = 5.0
OBSERVATION_WAIT_S = 150.0  # the kernel's request quota (120 s, D-58) ends the executor before this
MAX_INVALID_IN_A_ROW = 2
WITNESS_UNAVAILABLE = b"CAN'T VERIFY: no camera witness in this build (T-07)"
OUTCOMES = ("DONE", "REFUSED", "STOPPED", "ERROR")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def policy_sha256(variant: str = "default") -> str:
    """The Makefile's hash: ``cat policy.h policy.c variant_<v>.h | sha256sum``."""
    d = REPO / "kernel" / "policy"
    h = hashlib.sha256()
    for name in (*POLICY_SOURCES, f"variant_{variant}.h"):
        h.update((d / name).read_bytes())
    return h.hexdigest()


def render_output(data: bytes) -> str:
    """Observation output as ASCII for the prompt: printable bytes as is, anything else escaped."""
    out = []
    for b in data:
        if b == 0x0A:
            out.append("\n")
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


def observation_text(op_name: str, result: int, output: bytes, truncated: bool) -> str:
    err = ""
    if result < 0:
        name = next((k for k, v in abi.ERRNO.items() if v == -result), "E?")
        err = f" error={name}"
    if op_name == "cell_status" and result == abi.LINK_CELLST_LEN and len(output) == abi.LINK_CELLST_LEN:
        body = abi.cell_status_text(output)  # T-04: the fixed 16-byte record, decoded for the model
    else:
        body = render_output(output)
    if truncated:
        body += "\n[output truncated]"
    return f"OBSERVATION (untrusted data) op={op_name} result={result}{err}\n{body}\nEND OBSERVATION"


# Why a reply was rejected, in the model's terms. Keys are strictjson's stable codes (the part before any
# ":"), plus the run loop's own codes for replies that never reach strictjson. Nothing here changes what is
# accepted: the validator is unchanged, the model only learns which rule it broke.
REJECTION_WHY: dict[str, str] = {
    "empty": "it contained no text",
    "leading_text": "there was text before the JSON object",
    "trailing_text": "there was text after the JSON object",
    "code_fence": "it used a code fence",
    "json_syntax": "it was not valid JSON",
    "duplicate_key": "a key appeared twice",
    "float": "it contained a non-integer number (write 60, not 60.0)",
    "nan_or_infinity": "it contained NaN or Infinity",
    "int_too_large": "an integer had too many digits",
    "negative_zero": "it contained -0",
    "non_ascii": "it contained a non-ASCII character",
    "control_char": "it contained a control character",
    "bom": "it started with a byte-order mark",
    "too_large": "it was longer than 4096 bytes",
    "not_text": "it was not text",
    "not_object": "it was not a JSON object",
    "bad_action": '"action" was not call, done or refuse',
    "unknown_op": '"op" is not one of the listed operations',
    "args_not_object": '"args" was not a JSON object',
    "unknown_object": "the object name is not one of the listed objects",
    "missing_key": "the key {where} was missing",
    "extra_key": "the key {where} is not allowed",
    "not_int": "{where} must be an integer",
    "out_of_schema": "{where} was outside its allowed range",
    "not_string": "{where} must be a string",
    "not_printable": "{where} must be printable ASCII",
    "too_long": "{where} was over its byte limit (200 for summary and reason, 16 for LCD text, "
    "64 for object text)",
    "incomplete": "it was cut off at the output-token limit before any text was produced; think briefly",
    "status": "its status was {where}, not completed",
    "no_output_text": "it contained no output text",
    "several_output_texts": "it contained more than one output text",
}
REJECTION_LINE = "Reply with exactly one JSON object and nothing else."


def explain_rejection(reason: str) -> str:
    """One sentence for a rejection reason such as ``float``, ``missing_key:args.duty`` or
    ``incomplete:max_output_tokens``; unknown reasons are quoted as they are."""
    head, _, where = reason.partition(":")
    why = REJECTION_WHY.get(head)
    if why is None:
        return f"it broke the rule {reason!r}"
    if head == "incomplete" and where and where != "max_output_tokens":
        return f"it was cut off ({where}) before any text was produced"
    return why.format(where=f'"{where}"' if where else "?")


def reply_rejection(reply: ModelReply) -> Rejected | None:
    """Rejections the adapter sees before strictjson can: the reply did not complete, or it did not hold
    exactly one output text. ``None`` means the text can go to ``validate``."""
    if reply.status != STATUS_COMPLETED:
        if reply.status == "incomplete":
            return Rejected(f"incomplete:{reply.incomplete_reason or 'unknown'}")
        return Rejected(f"status:{reply.status or 'missing'}")
    if reply.texts == 0:
        return Rejected("no_output_text")
    if reply.texts > 1:
        return Rejected("several_output_texts")
    return None


def build_input(request: str, history: list[tuple[str, str]], rejection: str | None) -> str:
    """The ``input`` field: the request, every prior (reply, observation) pair, the one-line reason for a
    rejected previous reply (if any), and the next-step ask."""
    parts = [f"REQUEST: {request}", ""]
    for i, (reply, obs) in enumerate(history, 1):
        parts.append(f"ACTION {i}: {reply}")
        parts.append(obs)
        parts.append("")
    if rejection is not None:
        parts.append(f"Your previous reply was rejected: {explain_rejection(rejection)}. {REJECTION_LINE}")
    parts.append("Reply with the next action, or done / refuse.")
    return "\n".join(parts)


@dataclass
class RequestState:
    text: str
    index: int
    history: list[tuple[str, str]] = field(default_factory=list)
    actions: int = 0
    invalid_streak: int = 0
    last_rejection: str | None = None
    pending_nonce: int | None = None
    pending_op: str = ""
    observation: abi.Observation | None = None
    exec_end: abi.Event | None = None
    outcome: str = ""
    detail: str = ""


class Runner:
    def __init__(
        self,
        link: Link,
        client: MuseSparkClient,
        log: EventLog,
        pack: ContextPack,
        seed: bytes,
        timeline: Callable[[str], None] = print,
        max_actions: int = abi.ACTIONS_PER_REQUEST,
        exec_end_wait_s: float = EXEC_END_WAIT_S,
        observation_wait_s: float = OBSERVATION_WAIT_S,
    ) -> None:
        self.link = link
        self.client = client
        self.log = log
        self.pack = pack
        self.serializer = ActionSerializer(seed)
        self.timeline = timeline
        self.max_actions = max_actions
        self.exec_end_wait_s = exec_end_wait_s
        self.observation_wait_s = observation_wait_s
        self.hello: abi.Hello | None = None
        self.heartbeats = 0
        self.last_heartbeat: abi.Heartbeat | None = None
        self.audits = 0
        self.events = 0
        self.frame_errors = 0
        self.requests_done = 0
        self.request: RequestState | None = None
        self._stop = False
        self._pending_requests: list[str] = []

    # ---- lifecycle ------------------------------------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def serve(self, requests: int | None = None, idle_s: float | None = None) -> int:
        """Reads the link until ``requests`` requests ended, ``idle_s`` passed with no frame, the peer closed
        the socket or ``stop()`` was called. Returns the number of requests served."""
        last_frame = time.monotonic()
        while not self._stop:
            if requests is not None and self.requests_done >= requests:
                break
            if self._pending_requests:
                self._run_request(self._pending_requests.pop(0))
                continue
            items = self.link.recv(0.2)
            if items:
                last_frame = time.monotonic()
            for item in items:
                self._on_frame(item)
            if self.link.closed_by_peer:
                self.timeline("link closed by the kernel side")
                break
            if idle_s is not None and time.monotonic() - last_frame > idle_s:
                self.timeline(f"no frame for {idle_s:.0f} s: leaving")
                break
        return self.requests_done

    def finish(self) -> None:
        self.log.append("run_end", records=self.log.seq, requests=self.requests_done, audits=self.audits,
                        events=self.events, heartbeats=self.heartbeats,
                        frame_errors=self.frame_errors)  # fmt: skip

    # ---- frames ---------------------------------------------------------------------------------------
    def _log_frame(self, direction: str, frame: Frame) -> None:
        self.log.append("frame", dir=direction, ftype=abi.TYPE_NAMES.get(frame.type, "?"), code=frame.type,
                        fseq=frame.seq, bytes=len(frame.body), body=frame.body.hex())  # fmt: skip

    def _on_frame(self, item: Frame | FrameError) -> None:
        if isinstance(item, FrameError):
            self.frame_errors += 1
            self.log.append("frame_error", kind=str(item.kind), raw_len=item.raw_len, detail=item.detail)
            self.timeline(f"dropped a kernel frame: {item.kind} ({item.detail})")
            return
        self._log_frame("k2b", item)
        msg = item.msg
        if isinstance(msg, abi.Hello):
            self.hello = msg
            variant = "PERMISSIVE (EVAL ONLY)" if msg.variant == abi.LINK_VARIANT_PERMISSIVE else "default"
            expected = policy_sha256(
                "permissive" if msg.variant == abi.LINK_VARIANT_PERMISSIVE else "default"
            )
            match = int(bytes.fromhex(expected[:32]) == msg.policy_prefix)
            self.log.append("event", kind=0, hello=1, abi_version=msg.abi_version, variant=msg.variant,
                            build_id=msg.build_id.hex(), policy_prefix=msg.policy_prefix.hex(),
                            policy_match=match)  # fmt: skip
            build = msg.build_id.rstrip(b"\x00").decode("ascii", "replace")
            self.timeline(
                f"HELLO from the kernel: ABI {msg.abi_version}, policy {variant}, build {build}, "
                f"policy hash prefix {msg.policy_prefix.hex()[:16]} "
                f"({'matches' if match else 'DOES NOT MATCH'} the policy sources in this checkout)"
            )
        elif isinstance(msg, abi.Heartbeat):
            self.heartbeats += 1
            self.last_heartbeat = msg
        elif isinstance(msg, abi.Audit):
            self.audits += 1
            code = abi.POLICY_CODE_NAMES[msg.code] if msg.code < len(abi.POLICY_CODE_NAMES) else "?"
            reason = abi.POLICY_REASON_NAMES[msg.reason] if msg.reason < len(abi.POLICY_REASON_NAMES) else "?"
            if msg.kind == abi.EV_SYSCALL:
                subject = abi.POLICY_SYSCALL_NAMES[msg.nr] if msg.nr < len(abi.POLICY_SYSCALL_NAMES) else "?"
            else:
                subject = abi.POLICY_EVENT_NAMES[msg.kind] if msg.kind < len(abi.POLICY_EVENT_NAMES) else "?"
            self.log.append("audit", audit_seq=msg.seq, tick=msg.tick, kind=msg.kind, nr=msg.nr,
                            code=msg.code, reason=msg.reason, target=msg.target, result=msg.result,
                            tainted=msg.tainted, permissive=int(msg.permissive), code_name=code,
                            reason_name=reason, subject=subject)  # fmt: skip
            if subject not in ("cons_write", "agent_send", "agent_recv"):
                tainted = " [tainted]" if msg.tainted else ""
                self.timeline(
                    f"kernel decision #{msg.seq}: {subject} target={msg.target} -> {code} ({reason}) "
                    f"result={msg.result}{tainted}"
                )
        elif isinstance(msg, abi.Event):
            self.events += 1
            self.log.append("event", kind=msg.kind, sub=msg.sub, flags=msg.flags, tick=msg.tick, nr=msg.nr,
                            a=list(msg.args))  # fmt: skip
            if msg.kind == abi.EV_EXEC_END and self.request is not None:
                self.request.exec_end = msg
        elif isinstance(msg, abi.Request):
            if self.request is not None:
                self.timeline("REQUEST while a request is open: queued")
                self._pending_requests.append(msg.text)
            else:
                self._pending_requests.append(msg.text)
        elif isinstance(msg, abi.Observation):
            if self.request is not None and self.request.pending_nonce == msg.nonce:
                self.request.observation = msg
            else:
                self.timeline(f"observation with an unexpected nonce {msg.nonce}: ignored")

    def _pump(self, seconds: float) -> None:
        for item in self.link.recv(seconds):
            self._on_frame(item)

    def _wait(self, done: Callable[[], bool], timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while not done():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.link.closed_by_peer:
                return done()
            self._pump(min(0.2, remaining))
        return True

    # ---- one request ----------------------------------------------------------------------------------
    def _send(self, ftype: int, body: bytes) -> None:
        wire = self.link.send(ftype, body)
        self.log.append("frame", dir="b2k", ftype=abi.TYPE_NAMES[ftype], code=ftype,
                        fseq=(self.link.seq.next - 1) & 0xFFFF, bytes=len(body), body=body.hex(),
                        wire_bytes=len(wire))  # fmt: skip

    def _stop_request(self, req: RequestState, code: int, detail: str) -> None:
        name = abi.STOP_NAMES.get(code, "?")
        try:
            self._send(abi.LINK_T_STOP, bytes([code]))
        except LinkError as e:
            detail += f"; STOP not sent ({e})"
        self.log.append("stop_sent", reason=code, reason_name=name, detail=detail)
        self.timeline(f"sent STOP ({name}): {detail}")
        req.outcome, req.detail = "STOPPED", f"{name}: {detail}"

    def _run_request(self, text: str) -> None:
        req = RequestState(text, self.requests_done + 1)
        self.request = req
        blob = self.log.blob(text.encode("ascii"))
        self.log.append("user_request", index=req.index, text_blob=blob, bytes=len(text))
        self.timeline(f'REQUEST {req.index}: "{text}"')
        try:
            self._model_loop(req)
        except LinkError as e:
            req.outcome, req.detail = "ERROR", f"link: {e}"
            self.timeline(f"link error: {e}")
        # D-53: keep reading until the kernel's EXEC_END (the executor's own end), then close the request.
        if req.exec_end is None and not self.link.closed_by_peer:
            self._wait(lambda: req.exec_end is not None, self.exec_end_wait_s)
        end_kind = -1
        end_text = "no EXEC_END seen"
        if req.exec_end is not None:
            end_kind = req.exec_end.sub
            end_text = {0: f"executor exit code {req.exec_end.args[0]}",
                        1: f"executor fault vector {req.exec_end.args[0]}",
                        2: f"executor LIMIT after {req.exec_end.args[0]} ticks"}  # fmt: skip
            end_text = end_text.get(end_kind, "?")
            if req.outcome in ("", "DONE", "REFUSED") and end_kind != 0:
                req.outcome, req.detail = "STOPPED", end_text
        if not req.outcome:
            req.outcome, req.detail = "ERROR", "request ended without an outcome"
        assert req.outcome in OUTCOMES
        self.log.append("request_end", index=req.index, outcome=req.outcome, detail=req.detail,
                        actions=req.actions, exec_end=end_kind)  # fmt: skip
        self.timeline(f"request {req.index} end: {req.outcome} after {req.actions} action(s); {end_text}")
        self.requests_done += 1
        self.request = None

    def _model_loop(self, req: RequestState) -> None:
        while True:
            if req.exec_end is not None:
                req.outcome, req.detail = "STOPPED", "the kernel ended the executor"
                return
            if req.actions >= self.max_actions:
                self.timeline(
                    f"{req.actions} actions sent: no further model call (cap), waiting for the kernel"
                )
                req.outcome, req.detail = "STOPPED", f"action cap ({self.max_actions})"
                return
            user_input = build_input(req.text, req.history, req.last_rejection)
            self.timeline(f"model call {self.client.requests + 1} (reserving spend first)")
            try:
                reply = self.client.ask(self.pack.text, user_input)
            except ModelStop as e:
                self._stop_request(req, e.code, e.detail)
                return
            verdict = reply_rejection(reply) or validate(reply.text)
            if isinstance(verdict, Rejected):
                req.invalid_streak += 1
                req.last_rejection = verdict.reason
                self.log.append("reply_rejected", reason=verdict.reason, streak=req.invalid_streak,
                                blob=self.log.blob(reply.text.encode("utf-8", "replace")),
                                status=reply.status)  # fmt: skip
                self.timeline(
                    f"model reply rejected: {verdict.reason} ({req.invalid_streak} of {MAX_INVALID_IN_A_ROW})"
                )
                if req.invalid_streak >= MAX_INVALID_IN_A_ROW:
                    self._stop_request(
                        req, abi.LINK_STOP_INVALID_REPLIES, "two invalid model replies in a row"
                    )
                    return
                continue
            req.invalid_streak = 0
            req.last_rejection = None
            if isinstance(verdict, Call) and verdict.op == "witness":
                # D-58: the camera witness is the bridge's (T-07); it never becomes an ACTION frame. Until
                # T-07 the answer is CAN'T VERIFY; it still counts as one of the request's actions.
                req.actions += 1
                text = observation_text("witness", -abi.ERRNO["ENOSYS"], WITNESS_UNAVAILABLE, False)
                self.log.append("observation", index=-1, nonce=0, op="witness", result=-abi.ERRNO["ENOSYS"],
                                truncated=0, bytes=len(WITNESS_UNAVAILABLE), local=1,
                                output_blob=self.log.blob(WITNESS_UNAVAILABLE))  # fmt: skip
                req.history.append((reply.text.strip(), text))
                self.timeline(f"model action {req.actions}: witness -> answered by the bridge: CAN'T VERIFY")
                continue
            out = self.serializer.serialize(verdict)
            if isinstance(verdict, Call):
                assert out.action is not None and out.index is not None
                for ftype, body in out.frames:
                    self._send(ftype, body)
                self.log.append("action_sent", index=out.index, op=verdict.op, p1=out.action.p1,
                                nonce=out.action.nonce, text_id=out.action.text_id,
                                frames=len(out.frames))  # fmt: skip
                req.actions += 1
                args = ", ".join(f"{k}={v!r}" for k, v in verdict.args.items())
                self.timeline(f"model action {req.actions}: {verdict.op}({args}) -> sent as ACTION")
                req.pending_nonce = out.action.nonce
                req.pending_op = verdict.op
                req.observation = None
                self._wait(
                    lambda: req.observation is not None or req.exec_end is not None, self.observation_wait_s
                )
                if req.observation is None:
                    if req.exec_end is None:
                        req.outcome, req.detail = "ERROR", "no observation for the last action"
                    return
                obs = req.observation
                self.log.append("observation", index=out.index, nonce=obs.nonce, op=verdict.op,
                                result=obs.result, truncated=int(obs.truncated), bytes=len(obs.output),
                                output_blob=self.log.blob(obs.output))  # fmt: skip
                text = observation_text(verdict.op, obs.result, obs.output, obs.truncated)
                req.history.append((reply.text.strip(), text))
                self.timeline(
                    f"observation {req.actions}: result {obs.result}, {len(obs.output)} output byte(s)"
                )
                continue
            kind = abi.LINK_FINAL_DONE if isinstance(verdict, Done) else abi.LINK_FINAL_REFUSE
            summary = verdict.summary if isinstance(verdict, Done) else verdict.reason
            for ftype, body in out.frames:
                self._send(ftype, body)
            kind_name = "done" if kind == abi.LINK_FINAL_DONE else "refuse"
            self.log.append("final_sent", kind=kind, kind_name=kind_name, text=summary)
            req.outcome = "DONE" if kind == abi.LINK_FINAL_DONE else "REFUSED"
            req.detail = summary
            self.timeline(
                f'model {"done" if kind == abi.LINK_FINAL_DONE else "refuse"}: "{summary}" -> sent FINAL'
            )
            return


def start_run(
    log: EventLog,
    run_id: str,
    kernel: Path,
    pack: ContextPack,
    model_id: str,
    seed: bytes,
    budget_uusd: int | None,
) -> None:
    log.append("run_start", run_id=run_id, kernel_sha256=sha256_file(kernel) if kernel.exists() else "",
               kernel_path=str(kernel.relative_to(REPO)) if kernel.is_relative_to(REPO) else kernel.name,
               policy_sha256=policy_sha256(), context_pack=pack.name, context_pack_sha256=pack.sha256,
               model_id=model_id, seed=seed.hex(), abi_version=abi.LINK_ABI_VERSION,
               budget_uusd=budget_uusd or 0, t_ns=time.time_ns())  # fmt: skip
