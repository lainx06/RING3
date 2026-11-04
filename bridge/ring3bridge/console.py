"""``ring3 console`` (T-19, D-104): the operator's COM1 console over TCP, plus a push-to-talk key.

Keys go to the kernel and the kernel's output to the screen byte for byte; the only bytes taken out of the
stream are F2's. ``Core`` is the state machine (no I/O of its own, so every guard is testable); ``serve`` is
the loop.

Voice follows one path: F2 (microphone opens, REC on screen) -> F2 or the 15 s cap -> reservation and
transcription -> ``heard: "..."`` -> Enter sends the line, Esc discards it. The guards:
  - voice starts only at an empty ``ring3> `` prompt, and ``send_line`` checks that again at the last step;
  - any CONFIRM prompt in any voice phase ends the voice attempt and hands the keyboard back;
  - a line that is only y / n / yes / no never reaches ``send_line``'s write;
  - keys pressed during a voice attempt go nowhere near the kernel, so they cannot answer anything.
The kernel's own R10 rule (type-ahead is drained before a prompt prints) stays underneath all of this.

QEMU is started by ``make run-cell-console`` and not from here: the bridge starts no processes (INV-01).
"""

from __future__ import annotations

import argparse
import collections
import os
import selectors
import socket
import sys
import termios
import time
from pathlib import Path

from ring3bridge.voice import (
    MAX_LINE,
    MAX_RECORD_S,
    REFUSED_TEXT,
    Cleaned,
    is_bare_answer,
    is_console_command,
)

ESC = 0x1B
F2_SEQS = (b"\x1bOQ", b"\x1b[12~", b"\x1b[[B")  # xterm + Windows Terminal, vt220, Linux console
ESC_WAIT_S = 0.05  # a held ESC with nothing after it for this long is the Esc key
PROMPT = b"ring3> "
CONFIRM_MARK = b"CONFIRM:"
MAX_TAIL = 512
DEFAULT_COM1_PORT = 4556
CONFIRM_DISCARD = "confirm_prompt"


class OutputWatch:
    """What the kernel's output says about its state. ``at_prompt`` is positive evidence only: the last
    non-blank thing the kernel printed is exactly an empty prompt. Blank lines don't count because the audit
    drain breaks an idle line with a bare CR LF once records queue up (kernel/core/audit.c may_print).
    ``confirm_open``: a CONFIRM was printed and no prompt has followed; anything else that interleaves (an
    AUDIT line) leaves it set."""

    def __init__(self) -> None:
        self.line = bytearray()
        self.last = b""  # the last non-blank finished line, CR gone
        self.confirm_open = False

    def feed(self, data: bytes) -> None:
        for seg in _split_keep(data):
            if seg == b"\n":
                done = bytes(self.line).rstrip(b"\r")
                if done:
                    self.last = done
                self.line.clear()
                continue
            self.line += seg[: max(0, MAX_TAIL - len(self.line))]
            if CONFIRM_MARK in self.line or CONFIRM_MARK in seg:
                self.confirm_open = True
        if self.at_prompt:
            self.confirm_open = False

    @property
    def at_prompt(self) -> bool:
        cur = bytes(self.line).rstrip(b"\r")
        return cur == PROMPT or (not cur and self.last == PROMPT)


def _split_keep(data: bytes) -> list[bytes]:
    """Segments of ``data``, each ``b"\\n"`` on its own."""
    out = []
    for i, part in enumerate(data.split(b"\n")):
        if i:
            out.append(b"\n")
        if part:
            out.append(part)
    return out


class Core:
    PASS, STARTING, REC, WORKING, HEARD = "pass", "starting", "rec", "working", "heard"

    def __init__(self, write_kernel, write_screen, voice, tidy: bool = False) -> None:  # noqa: ANN001
        self.write_kernel = write_kernel
        self.write_screen = write_screen
        self.voice = voice
        self.watch = OutputWatch()
        self.state = self.PASS
        self.hold = bytearray()
        self.hold_t = 0.0
        self.pending: Cleaned | None = None
        self.rec_t0 = 0.0
        self.rec_shown = -1
        self.tidy = tidy  # --tidy-echo: see _tidied
        self.input_open = False
        self.typed = bytearray()
        self.echoes: collections.deque[int] = collections.deque()
        self.recent = b"\n"  # the kernel's last bytes, as sent
        self.held_cr = False
        self.dropped_break = False

    # ---- --tidy-echo ----
    def _tidied(self, data: bytes) -> bytes:
        """kernel/core/audit.c may_print() breaks the input line for every queued telemetry record, on the
        production image too, where the record prints nothing: typed characters land one per line. With the
        flag on, a CR LF at an open input line (from the kernel's ``ring3> `` to the Enter key) is not drawn.
        The first byte that isn't the echo of a typed key closes the input line and starts on a line of its own,
        so only CR LF ever goes missing. The guards read the raw output, never this."""
        out = bytearray()
        for b in data:
            if self.input_open:
                if b == 0x0D and not self.held_cr:
                    self.held_cr = True
                    continue
                if self.held_cr:
                    self.held_cr = False
                    if b == 0x0A:
                        self.dropped_break = True
                        continue
                    out.append(0x0D)
                if self.echoes and b == self.echoes[0]:
                    self.echoes.popleft()
                    self.typed.append(b)
                    out.append(b)
                    continue
                self.input_open = False
                if self.dropped_break:
                    out += b"\r\n"
            out.append(b)
            self.recent = (self.recent + bytes([b]))[-(len(PROMPT) + 1) :]
            if self.recent == b"\n" + PROMPT:
                self.input_open, self.dropped_break = True, False
                self.typed.clear()
                self.echoes.clear()
        return bytes(out)

    def _send_keys(self, data: bytes) -> None:
        """Every byte for the kernel. With the flag on: which echoes to expect; Enter ends the line."""
        if self.tidy and self.input_open:
            for b in data:
                if b in (0x0D, 0x0A):
                    self.input_open = False
                    if self.held_cr:
                        self.held_cr = False
                        self.write_screen(b"\r")
                    break
                if 0x20 <= b < 0x7F:
                    self.echoes.append(b)
        self.write_kernel(data)

    # ---- screen ----
    def _say(self, msg: str) -> None:
        """A tool line of its own; the kernel's current line is drawn again under it."""
        if self.tidy and self.input_open:
            tail = PROMPT + bytes(self.typed)
        else:
            tail = PROMPT if self.watch.at_prompt else bytes(self.watch.line)
        self.write_screen(b"\r\n[voice] " + msg.encode("ascii", "replace") + b"\r\n" + tail)

    def _show_rec(self, seconds: int) -> None:
        self.rec_shown = seconds
        self.write_screen(b"\r[REC %2d s / %d s]  F2 = stop, Esc = cancel " % (seconds, MAX_RECORD_S))

    # ---- from the kernel ----
    def from_kernel(self, data: bytes) -> None:
        self.write_screen(self._tidied(data) if self.tidy else data)
        self.watch.feed(data)
        if self.state != self.PASS and self.watch.confirm_open:
            self._drop(CONFIRM_DISCARD, "a CONFIRM prompt is open: voice stopped, answer on the keyboard")

    def _drop(self, reason: str, msg: str) -> None:
        if self.state != self.HEARD:
            self.voice.cancel()
        self.state = self.PASS
        self.pending = None
        self.voice.note("voice_discarded", reason=reason)
        self._say(msg)

    # ---- from the keyboard ----
    def from_keyboard(self, data: bytes, now: float) -> None:
        self.hold += data
        self.hold_t = now
        self._drain_keys()

    def tick(self, now: float) -> None:
        if self.hold and now - self.hold_t >= ESC_WAIT_S:
            held = bytes(self.hold)
            self.hold.clear()
            self._plain(held, lone_esc=held == b"\x1b")
            self._drain_keys()
        if self.state == self.REC:
            elapsed = now - self.rec_t0
            if elapsed >= MAX_RECORD_S:
                self.voice.stop()
                self.state = self.WORKING
                self._say(f"{MAX_RECORD_S} s cap reached; transcribing (Esc = discard)")
            elif int(elapsed) != self.rec_shown:
                self._show_rec(int(elapsed))

    def _drain_keys(self) -> None:
        while self.hold:
            if self.hold[0] != ESC:
                i = self.hold.find(ESC)
                n = len(self.hold) if i < 0 else i
                plain = bytes(self.hold[:n])
                del self.hold[:n]
                self._plain(plain)
                continue
            head = bytes(self.hold)
            seq = next((s for s in F2_SEQS if head.startswith(s)), None)
            if seq is not None:
                del self.hold[: len(seq)]
                self._f2()
                continue
            if any(s.startswith(head) for s in F2_SEQS):
                return  # could still become F2: wait for more, tick() ends the wait
            del self.hold[:1]
            self._plain(b"\x1b")

    def _plain(self, data: bytes, lone_esc: bool = False) -> None:
        for i, b in enumerate(data):
            if self.state == self.PASS:
                self._send_keys(data[i:])
                return
            if b == ESC:
                if lone_esc:
                    self._escape()
            elif b in (0x0D, 0x0A) and self.state == self.HEARD:
                self._enter()
            # every other key during a voice attempt goes nowhere

    def _escape(self) -> None:
        if self.state == self.HEARD:
            self._drop("operator", "discarded")
        elif self.state in (self.STARTING, self.REC, self.WORKING):
            self._drop("operator", "cancelled")

    def _enter(self) -> None:
        c, self.pending = self.pending, None
        self.state = self.PASS
        assert c is not None and c.line is not None
        if self.send_line(c.line):
            self.voice.note("voice_sent", text=c.line)
        else:
            self.voice.note("voice_discarded", reason="not_at_prompt")
            self._say("not sent: the console is no longer at an empty ring3> prompt")

    def send_line(self, text: str) -> bool:
        """The only write of voice text to COM1. Every check again, whatever the state machine believes."""
        if not self.watch.at_prompt or self.watch.confirm_open:
            return False
        if not text or len(text) > MAX_LINE or not all(0x20 <= ord(c) < 0x7F for c in text):
            return False
        if is_bare_answer(text) or is_console_command(text):
            return False
        self._send_keys(text.encode("ascii") + b"\r")
        return True

    def _f2(self) -> None:
        if self.state == self.REC:
            self.voice.stop()
            self.state = self.WORKING
            self._say("transcribing (Esc = discard)")
        elif self.state != self.PASS:
            pass  # already busy with this recording
        elif self.voice is None:
            self._say("voice is off: start with --budget-usd and the helper (the keyboard is unchanged)")
        elif self.watch.confirm_open:
            self._say("a CONFIRM is open: keyboard only")
        elif not self.watch.at_prompt:
            self._say(
                "not at an empty ring3> prompt (a request is running or a line is part typed): keyboard only"
            )
        else:
            self.state = self.STARTING
            self.voice.begin()

    # ---- from the voice worker ----
    def voice_event(self, event: tuple, now: float) -> None:
        kind = event[0]
        if kind == "rec" and self.state == self.STARTING:
            self.state = self.REC
            self.rec_t0 = now
            self.write_screen(b"\r\n")
            self._show_rec(0)
        elif kind == "heard" and self.state == self.WORKING:
            self._heard(event[1])
        elif kind == "error" and self.state != self.PASS and self.state != self.HEARD:
            self.state = self.PASS
            self._say(f"ERROR {event[1]} (the keyboard is unchanged)")

    def _heard(self, c: Cleaned) -> None:
        if c.line is None:
            self.state = self.PASS
            self.voice.note("voice_discarded", reason=c.refused)
            self._say(f'heard: "{c.text}"  not sent: {REFUSED_TEXT[c.refused]}')
            return
        self.state = self.HEARD
        self.pending = c
        self._say(f'heard: "{c.text}"  Enter = send, Esc = discard')


def _console_termios(fd: int) -> list | None:
    """The same terminal mode QEMU's ``-serial stdio`` sets, so typed use looks the same: no echo, no line
    editing, Enter arrives as CR, Ctrl-C still signals. Returns the mode to restore (None: not a terminal)."""
    try:
        before = termios.tcgetattr(fd)
    except termios.error:
        return None
    t = termios.tcgetattr(fd)
    t[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR
              | termios.IGNCR | termios.ICRNL | termios.IXON)  # fmt: skip
    t[1] |= termios.OPOST
    t[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.IEXTEN)
    t[2] = (t[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8
    t[6][termios.VMIN] = 1
    t[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, t)
    return before


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def serve(  # noqa: ANN201
    com1: socket.socket,
    in_fd: int,
    out_fd: int,
    voice=None,
    events=None,
    tidy: bool = False,  # noqa: ANN001
) -> int:
    """Runs until COM1 closes (QEMU exited). ``voice``/``events``: a VoiceService and its event queue."""
    events = events if events is not None else collections.deque()
    core = Core(com1.sendall, lambda b: _write_all(out_fd, b), voice, tidy)
    before = _console_termios(in_fd)
    sel = selectors.DefaultSelector()
    try:
        sel.register(com1, selectors.EVENT_READ, "com1")
        sel.register(in_fd, selectors.EVENT_READ, "keys")
        while True:
            for key, _ in sel.select(0.02):
                if key.data == "com1":
                    data = com1.recv(4096)
                    if not data:
                        return 0
                    core.from_kernel(data)
                else:
                    data = os.read(in_fd, 4096)
                    if data:
                        core.from_keyboard(data, time.monotonic())
                    else:
                        sel.unregister(in_fd)  # end of keyboard input: the output keeps flowing
            now = time.monotonic()
            while events:
                core.voice_event(events.popleft(), now)
            core.tick(now)
    except OSError:
        return 0  # COM1 or the screen went away
    finally:
        sel.close()
        if before is not None:
            termios.tcsetattr(in_fd, termios.TCSANOW, before)


def _connect(host: str, port: int, timeout_s: float) -> socket.socket:
    end = time.monotonic() + timeout_s
    while True:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError:
            if time.monotonic() >= end:
                raise
            time.sleep(0.1)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("console", help="COM1 console with push-to-talk on F2 (voice is paid: --budget-usd)")
    p.add_argument(
        "--budget-usd", help="turns voice on: the cap for this session; absent: no key, no network"
    )
    p.add_argument("--voice-dir", help="the folder tools/voice_capture_win.py serves (voice only)")
    p.add_argument(
        "--tidy-echo",
        action="store_true",
        help="keep typed characters on one line (drops the kernel's empty breaks)",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_COM1_PORT, help="QEMU's COM1 (make run-cell-console)")
    p.add_argument("--connect-timeout", type=float, default=60.0)
    p.add_argument("--run-id")
    p.set_defaults(fn=_cmd)


def _voice_setup(a: argparse.Namespace, events: collections.deque):  # noqa: ANN202
    """-> (VoiceService, log, ledger, run id, run dir). Only here, with --budget-usd given, is a key read."""
    from ring3bridge import eventlog
    from ring3bridge.cli import RUNS
    from ring3bridge.config import load_project_cap
    from ring3bridge.ledger import DEFAULT_LEDGER, Ledger, usd_to_uusd
    from ring3bridge.models.transcribe import TranscribeClient, load_voice_config
    from ring3bridge.models.transport import HttpsTransport
    from ring3bridge.voice import FolderRecorder, LockedLog, VoiceService

    budget = usd_to_uusd(a.budget_usd)
    folder = a.voice_dir or ""  # an argument: only models/ touches the environment (INV-09)
    if not folder or not Path(folder).is_dir():
        raise SystemExit(
            "console: --budget-usd needs --voice-dir, the folder the helper serves (start the helper first)"
        )
    cfg = load_voice_config()
    if cfg.price_uusd_per_hour is None:
        raise SystemExit(
            "console: no voice price in config/models.toml; the ledger would refuse every reservation"
        )
    transport = HttpsTransport()
    run_id = a.run_id or "voice-" + time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    log = LockedLog(eventlog.EventLog(run_dir))
    ledger = Ledger(load_project_cap(), DEFAULT_LEDGER)
    log.append("run_start", run_id=run_id, kind="voice_console", model_id=cfg.model_id, budget_uusd=budget)
    client = TranscribeClient(cfg, transport, ledger, log, run_id, budget)
    return (
        VoiceService(FolderRecorder(Path(folder)), client, log, events.append),
        log,
        ledger,
        run_id,
        run_dir,
    )


def _cmd(a: argparse.Namespace) -> int:
    from ring3bridge import eventlog
    from ring3bridge.ledger import fmt_usd
    from ring3bridge.models.transport import TransportError

    events: collections.deque = collections.deque()
    voice = log = ledger = run_id = run_dir = None
    if a.budget_usd:
        try:
            voice, log, ledger, run_id, run_dir = _voice_setup(a, events)
        except TransportError as e:
            print(
                f"console: {e} (the model API key comes from the environment, never from a file)",
                file=sys.stderr,
            )
            return 2
    print(f"console: waiting for COM1 at {a.host}:{a.port} (make run-cell-console)", flush=True)
    try:
        com1 = _connect(a.host, a.port, a.connect_timeout)
    except OSError as e:
        print(f"console: cannot connect to COM1: {type(e).__name__}", file=sys.stderr)
        return 1
    print(
        "console: connected. "
        + ("F2 = talk. " if voice else "Voice off (no --budget-usd). ")
        + "Ctrl-C leaves.",
        flush=True,
    )
    rc = 0
    try:
        rc = serve(com1, sys.stdin.fileno(), sys.stdout.fileno(), voice, events, a.tidy_echo)
    except KeyboardInterrupt:
        print("\r\nconsole: left; QEMU keeps running (Ctrl-C in its terminal)", flush=True)
    finally:
        com1.close()
    if voice is not None:
        voice.cancel()
        voice.join(70)
        log.append("run_end", run_id=run_id)
        print(f"\r\nvoice session {run_id}: {fmt_usd(ledger.committed_uusd(run_id))} (estimate, not billing)")
        print(eventlog.verify(run_dir).message)
    return rc
