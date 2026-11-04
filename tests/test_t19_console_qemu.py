"""T-19: `ring3 console` against the real kernel. COM1 over TCP as `make run-cell-console` starts it, the
proxy's real loop, the real bridge run loop with a scripted model, a simulated cell, a fake transcription
transport. No network, no key, no microphone.

Typed use: the same keystrokes through `-serial stdio` and through the proxy give the same bytes.
Voice (on the production image: the test image prints TAPE and AUDIT lines over the prompt): the transcript
becomes a REQUEST only after Enter; the kernel decides as for a typed line; at a CONFIRM the voice path is
refused and the keyboard answers."""

from __future__ import annotations

import collections
import io
import json
import os
import re
import subprocess
import threading
import time
import wave

import pytest

from ring3bridge import console, replay, voice
from ring3bridge.eventlog import EventLog, verify
from ring3bridge.ledger import Ledger
from ring3bridge.models.transcribe import TranscribeClient, VoiceConfig
from ring3bridge.models.transport import TransportResponse
from tests.agentbridge import AGENT_QUOTAS, BridgeThread, call, done, runs_dir
from tests.qemu import (
    EXIT_PASS,
    KERNEL,
    KERNEL_TEST,
    REPO,
    Session,
    free_port,
    qemu_command,
)
from tests.sim_cell import SimCell

F2 = b"\x1bOQ"
PROMPT_RE = r"ring3> [\r\n]*\Z"  # the audit drain may break the idle line with a bare CR LF


class Screen:
    """The proxy's stdout, read through a pipe."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.pos = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                d = os.read(self.fd, 4096)
            except OSError:
                return
            if not d:
                return
            with self.lock:
                self.buf += d

    def text(self) -> str:
        with self.lock:
            return self.buf.decode("ascii", "replace")

    def expect(self, pattern: str, timeout: float = 30.0) -> re.Match[str]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            t = self.text()
            m = re.compile(pattern, re.M).search(t, self.pos)
            if m:
                self.pos = m.end()
                return m
            time.sleep(0.01)
        raise AssertionError(f"no {pattern!r} after {self.pos} in:\n{self.text()[-3000:]}")


class Proxied:
    """QEMU with COM1 on TCP, `console.serve` on pipes in a thread."""

    def __init__(
        self,
        voice_service=None,
        events=None,
        com2=False,
        com3=False,
        append="",
        kernel=KERNEL,
        com2_port=None,
    ) -> None:
        self.com1_port = free_port()
        self.com2_port = com2_port or (free_port() if com2 else None)
        self.com3_port = free_port() if com3 else None
        cmd = qemu_command(kernel, append, com2_port=self.com2_port, com3_port=self.com3_port)
        cmd[cmd.index("stdio")] = f"tcp:127.0.0.1:{self.com1_port},server=on,wait=on"
        self.proc = subprocess.Popen(cmd, cwd=REPO)
        self.com1 = console._connect("127.0.0.1", self.com1_port, 10.0)
        self.kbd_r, self.kbd_w = os.pipe()
        self.scr_r, self.scr_w = os.pipe()
        self.screen = Screen(self.scr_r)
        self.rc: list[int] = []
        self.thread = threading.Thread(
            target=lambda: self.rc.append(
                console.serve(self.com1, self.kbd_r, self.scr_w, voice_service, events)
            ),
            daemon=True,
        )
        self.thread.start()

    def key(self, b: bytes) -> None:
        os.write(self.kbd_w, b)

    def line(self, text: str) -> None:
        self.key(text.encode() + b"\r")

    def exit(self) -> int:
        self.line("exit")
        code = self.proc.wait(20)
        self.thread.join(5)
        assert not self.thread.is_alive() and self.rc == [0]  # COM1 closing ends the proxy
        return code

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        self.com1.close()
        for fd in (self.kbd_r, self.kbd_w, self.scr_w):
            os.close(fd)


def _normal(t: str) -> str:
    return re.sub(r"\d+", "#", t)


SCRIPT = ["help", "status", "objects", "audit", "list", "exit"]


@pytest.mark.parametrize("kernel", [KERNEL, KERNEL_TEST], ids=["production", "test-image"])
def test_typed_use_through_the_proxy_is_byte_for_byte(kernel):
    """Same image, same lines: `-serial stdio` vs the proxy. Counters may differ; nothing else may."""
    with Session(kernel=kernel, append="", timeout=30) as s:
        for line in SCRIPT[:-1]:
            s.prompt()
            s.send(line)
        s.prompt()
        s.send("exit")
        direct = s.close()
    assert direct.exit_code == EXIT_PASS
    p = Proxied(kernel=kernel)
    try:
        for line in SCRIPT[:-1]:
            p.screen.expect(PROMPT_RE)
            p.line(line)
        p.screen.expect(PROMPT_RE)
        assert p.exit() == EXIT_PASS
        through = p.screen.text()
    finally:
        p.close()
    assert "PANIC" not in through
    d = direct.output.replace("\r\n", "\n")
    t = through.replace("\r\n", "\n")
    assert _normal(d) == _normal(t)


def _wav() -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 48000)
    return b.getvalue()


class FakeRecorder:
    def __init__(self) -> None:
        self.starts = 0

    def begin(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def wait_wav(self) -> bytes:
        return _wav()


class FakeAsr:
    """Plays transcripts back in the documented reply shape."""

    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = list(transcripts)
        self.calls = 0

    def post(self, url, body, timeout_s, content_type="application/json"):
        self.calls += 1
        text = self.transcripts.pop(0)
        return TransportResponse(
            200,
            json.dumps({"sessionId": "s", "transcript": text, "audioDurationMs": 2000, "turns": []}).encode(),
        )


def _voice_rig(tmp_path, transcripts):
    log = voice.LockedLog(EventLog(runs_dir(tmp_path, "voice")))
    log.append("run_start", run_id="voice", kind="voice_console")
    led = Ledger(30_000_000, tmp_path / "voice-ledger.jsonl")
    cfg = VoiceConfig("muse-voice-transcribe-1.0", "https://api.meta.ai/v1/asr/transcribe", 60, 180000,
                      "https://example.invalid/synthetic-test-price", "2026-11-04")  # fmt: skip
    asr = FakeAsr(transcripts)
    events: collections.deque = collections.deque()
    rec = FakeRecorder()
    svc = voice.VoiceService(rec, TranscribeClient(cfg, asr, led, log, "voice", 10_000), log, events.append)
    return svc, events, asr, rec, led


def _say(p: Proxied) -> None:
    p.key(F2)
    p.screen.expect(r"\[REC")
    p.key(F2)


def test_a_spoken_request_end_to_end_with_the_kernels_decisions(tmp_path):
    """RING3, set the fan to 100 %: the kernel denies. A second spoken request reaches a CONFIRM: voice is
    refused there, the keyboard answers. A spoken "yes." at the prompt is shown and never sent."""
    replies = [
        call("fan_set", duty=100),
        done("fan_set 100 refused by the kernel"),
        call("fan_set", duty=60),
        done("fan_set 60 answered no"),
    ]
    svc, events, asr, rec, led = _voice_rig(
        tmp_path, ["RING3, set the fan to 100 %", "ring three, set the fan to 60 %", "Yes."]
    )
    p = Proxied(svc, events, com2=True, com3=True, append=AGENT_QUOTAS)
    sim = SimCell(p.com3_port).start()
    b = None
    try:
        p.screen.expect(PROMPT_RE)
        for _ in range(50):
            p.line("status")
            m = p.screen.expect(r"status: .*tele_valid=(\d)")
            p.screen.expect(PROMPT_RE)
            if m.group(1) == "1":
                break
            time.sleep(0.1)
        assert m.group(1) == "1"
        b = BridgeThread(p.com2_port, runs_dir(tmp_path, "bridge"), replies, requests=2).start()

        # 1: the transcript waits for Enter; nothing reaches the kernel before it
        _say(p)
        p.screen.expect(r'heard: "set the fan to 100 %"  Enter = send, Esc = discard')
        time.sleep(0.5)
        assert "REQUEST" not in p.screen.text()
        p.key(b"\r")
        p.screen.expect(r"^REQUEST set the fan to 100 %\r?$")
        p.screen.expect(r"^OUTCOME DONE: fan_set 100 refused by the kernel\r?$", 40)
        p.screen.expect(PROMPT_RE)
        assert "1 fan_set(100) -> -200 (ESAFETY)" in p.screen.text()
        assert (
            asr.calls == 1 and led.settled_uusd() == 100
        )  # 2 s reserved and settled, at the configured price

        # 2: a CONFIRM: voice refused while it is open, y/n only from the keyboard
        _say(p)
        p.screen.expect(r'heard: "set the fan to 60 %"')
        p.key(b"\r")
        p.screen.expect(r"CONFIRM: agentsh wants cell_fan\(.*\)\. Allow\? \[y/N\] ", 20)
        starts = rec.starts
        p.key(F2)
        p.screen.expect(r"\[voice\] a CONFIRM is open: keyboard only")
        assert rec.starts == starts and asr.calls == 2  # no recording, no call
        p.key(b"n\r")
        p.screen.expect(r"fan_set\(60\) -> -125 \(ECANCELED\)", 10)
        p.screen.expect(r"^OUTCOME DONE: fan_set 60 answered no\r?$", 40)
        p.screen.expect(PROMPT_RE)
        b.join()

        # 3: a bare answer at the prompt: paid for, shown, never a line
        before = p.screen.text().count("REQUEST")
        _say(p)
        p.screen.expect(r'heard: "Yes\."  not sent: only a y/n word')
        p.key(b"\r")  # the keyboard's own empty line
        time.sleep(0.3)
        assert p.screen.text().count("REQUEST") == before
        p.screen.expect(PROMPT_RE)
        p.line("audit")  # the production image keeps its decisions here, not on the console
        p.screen.expect(r"audit_log")
        p.screen.expect(PROMPT_RE)
        assert p.exit() == EXIT_PASS
    finally:
        p.close()
        sim.stop()
    t = p.screen.text()
    assert "PANIC" not in t and not any(int(x) for x in re.findall(r"\bdropped=(\d+)", t))
    assert verify(tmp_path / "runs" / "voice").ok
    recs = [json.loads(x) for x in (tmp_path / "runs" / "voice" / "events.jsonl").read_text().splitlines()]
    sent = [r["text"] for r in recs if r["type"] == "voice_sent"]
    assert sent == ["set the fan to 100 %", "set the fan to 60 %"]
    assert [r["refused"] for r in recs if r["type"] == "voice_heard"] == ["", "", voice.REFUSED_ANSWER]
    # the kernel decided both as for a typed line (its AUDIT frames, as the bridge logged them)
    fan = [(r["code_name"], r["reason_name"]) for r in b.records() if r["type"] == "audit"
           and r["subject"] in ("cell_fan", "confirm")]  # fmt: skip
    assert fan == [("DENY_LIMIT", "fan_range"), ("CONFIRM_PENDING", "first_fan"), ("CONFIRM_NO", "answer")]
    # the bridge received exactly what was shown on the screen, like a typed line
    blobs = [r["text_blob"] for r in b.records() if r["type"] == "user_request"]
    got = [(b.run_dir / "blobs" / h).read_text() for h in blobs]
    assert got == ["set the fan to 100 %", "set the fan to 60 %"]


def test_the_printed_start_order_gives_a_replayable_run(tmp_path):
    """Bridge first (retrying), then QEMU, then the console, as the evidence README tells the operator. QEMU
    opens COM2 only once the console has attached, so the bridge must still see the tape from the boot on: a late
    bridge is what made T-04's runs 1-2 unreplayable (D-70)."""
    com2 = free_port()
    b = BridgeThread(
        com2, runs_dir(tmp_path, "bridge"), [call("list_objects"), done("listed the objects")], requests=1
    ).start()
    svc, events, _asr, _rec, _led = _voice_rig(tmp_path, ["RING3, list the objects"])
    p = Proxied(svc, events, com3=True, append=AGENT_QUOTAS, com2_port=com2)
    sim = SimCell(p.com3_port).start()
    try:
        p.screen.expect(PROMPT_RE)
        _say(p)
        p.screen.expect(r'heard: "list the objects"  Enter = send')
        p.key(b"\r")
        p.screen.expect(r"^OUTCOME DONE: listed the objects\r?$", 40)
        p.screen.expect(PROMPT_RE)
        b.join()
        assert p.exit() == EXIT_PASS
    finally:
        p.close()
        sim.stop()
    assert verify(b.run_dir).ok
    r = replay.replay_run(b.run_dir)
    assert r.ok, r


def test_make_run_cell_console_gives_com1_to_the_tool():
    """The Makefile target this file assumes: COM1 first, on TCP, QEMU waiting; COM2 and COM3 as run-cell."""
    out = subprocess.run(
        ["make", "-n", "run-cell-console", "COM1_PORT=4556", "COM2_PORT=4555", "CELL_PORT=/dev/ttyACM0"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    serials = re.findall(r"-serial (\S+)", out)
    assert serials == [
        "tcp:127.0.0.1:4556,server=on,wait=on",
        "tcp:127.0.0.1:4555,server=on,wait=off",
        "/dev/ttyACM0",
    ]
    assert "build/kernel.elf" in out and "kernel-test" not in out
