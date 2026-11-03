"""QEMU harness for RING3 tests: boot a kernel headless, capture COM1, return output and exit code.

Exit codes (kernel/include/qemu.h): 33 = PASS, 35 = FAIL, 37 = PANIC, 124 = harness timeout.
Every run fails on an unexpected `PANIC` line (INV-12) unless the caller asks for one.

Two styles: `run_qemu()` boots with no console input and waits for exit; `Session` drives the console over
stdin with expect/send, for confirmations, `run <prog>` and `tele` injections.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "build" / "kernel.elf"
KERNEL_TEST = REPO / "build" / "kernel-test.elf"
KERNEL_TEST_PERMISSIVE = REPO / "build" / "kernel-test-permissive.elf"
KERNEL_KTEST = REPO / "build" / "kernel-ktest.elf"
HOST_POLICY_TEST = REPO / "build" / "host" / "policy_test"
HOST_POLICY_TEST_PERMISSIVE = REPO / "build" / "host" / "policy_test_permissive"
EVIDENCE = REPO / "docs" / "evidence" / "T-02"
TEST_LOGS = REPO / "build" / "test-logs"

EXIT_PASS = 33
EXIT_FAIL = 35
EXIT_PANIC = 37
EXIT_TIMEOUT = 124

QEMU = os.environ.get("RING3_QEMU", "qemu-system-i386")

# Short quotas for the suite (ticks at 100 Hz): action 1 s, request 4 s, confirmation 1.5 s.
FAST_QUOTAS = "taction=100 trequest=400 tconfirm=150"


@dataclass
class Run:
    cmd: list[str]
    output: str
    exit_code: int
    seconds: float

    def lines(self) -> list[str]:
        return self.output.splitlines()

    def has_panic(self) -> bool:
        return any(line.startswith("PANIC:") for line in self.lines())

    def dropped(self) -> list[int]:
        """Every `dropped=N` the kernel printed (`status` lines and the `audit:` line before KERNEL ALIVE)."""
        return [int(m) for m in re.findall(r"\bdropped=(\d+)", self.output)]

    def check(self, expect_panic: bool = False) -> None:
        """INV-12: an unexpected PANIC fails the run; F5 (D-52): any dropped audit record fails the run."""
        if self.has_panic() and not expect_panic:
            raise AssertionError(f"unexpected PANIC (INV-12):\n{self.output}")
        if any(self.dropped()):
            raise AssertionError(f"audit records dropped (F5): {self.dropped()}\n{self.output}")

    def after(self, marker: str) -> str:
        """Output after the first line containing `marker` (empty if absent)."""
        i = self.output.find(marker)
        return "" if i < 0 else self.output[i:]


def free_port() -> int:
    """A free TCP port on the loopback for QEMU's COM2 listener."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def qemu_command(
    kernel: Path,
    append: str = "",
    memory_mb: int = 64,
    com2_port: int | None = None,
    com3_port: int | str | None = None,
    monitor: str = "none",
) -> list[str]:
    """`com2_port`: expose COM2 as `tcp:127.0.0.1:<port>,server=on,wait=off` (the bridge link, T-03b);
    None leaves QEMU without a second serial port (the kernel reports `link: COM2 absent`). `com3_port`:
    the same for COM3, the work cell (T-04, `tests/sim_cell.py` connects there), or a device path such as
    /dev/ttyACM0 (the real board, hardware tests); it needs a COM2 (null if `com2_port` is None). `monitor`:
    QEMU's `-monitor` argument (T-08 reads the VGA text buffer through a unix-socket monitor)."""
    cmd = [
        QEMU,
        "-kernel",
        str(kernel.relative_to(REPO) if kernel.is_absolute() else kernel),
        "-m",
        str(memory_mb),
        "-display",
        "none",
        "-serial",
        "stdio",
    ]
    if com2_port is not None:
        cmd += ["-serial", f"tcp:127.0.0.1:{com2_port},server=on,wait=off"]
    elif com3_port is not None:
        cmd += ["-serial", "null"]
    if isinstance(com3_port, str):
        cmd += ["-serial", com3_port]
    elif com3_port is not None:
        cmd += ["-serial", f"tcp:127.0.0.1:{com3_port},server=on,wait=off"]
    cmd += [
        "-monitor",
        monitor,
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-no-reboot",
    ]
    if append:
        cmd += ["-append", append]
    return cmd


def run_qemu(
    kernel: Path = KERNEL,
    append: str = "testexit",
    timeout: float = 20.0,
    expect_panic: bool = False,
) -> Run:
    cmd = qemu_command(kernel, append)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        out = proc.stdout.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        code = EXIT_TIMEOUT
    run = Run(cmd, out.replace("\r\n", "\n"), code, time.monotonic() - t0)
    run.check(expect_panic)
    return run


class Session:
    """An interactive QEMU run: `expect(regex)` waits for output, `send(line)` types on COM1.

    Use as a context manager; `close()` sends `exit` (unless already finished), waits, and fails on PANIC.
    """

    def __init__(
        self,
        kernel: Path = KERNEL_TEST,
        append: str = FAST_QUOTAS,
        timeout: float = 30.0,
        com2: bool = False,
        com3: bool | str = False,
        monitor: str = "none",
    ):
        self.com2_port: int | None = free_port() if com2 else None
        self.com3_port: int | str | None = com3 if isinstance(com3, str) else free_port() if com3 else None
        self.cmd = qemu_command(
            kernel, append, com2_port=self.com2_port, com3_port=self.com3_port, monitor=monitor
        )
        self.timeout = timeout
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(
            self.cmd, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._eof = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.pos = 0  # consumed up to here
        self.sent_at = 0  # output length when the last line was sent (prompt() looks from here)
        self.exit_code: int | None = None

    def _read(self) -> None:
        assert self.proc.stdout is not None
        while True:
            chunk = self.proc.stdout.read1(4096)
            if not chunk:
                break
            with self._lock:
                self._buf += chunk
        self._eof.set()

    @property
    def output(self) -> str:
        with self._lock:
            return bytes(self._buf).decode("utf-8", "replace").replace("\r\n", "\n")

    def expect(self, pattern: str, timeout: float | None = None) -> re.Match[str]:
        """Waits until `pattern` (regex, multiline) matches output not yet consumed; consumes through it."""
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        rx = re.compile(pattern, re.M)
        while True:
            text = self.output
            m = rx.search(text, self.pos)
            if m and pattern.endswith("$") and m.end() == len(text) and not text.endswith("\n"):
                m = None  # a line-anchored pattern matched a partially arrived line: wait for the rest
            if m:
                self.pos = m.end()
                return m
            if "PANIC:" in text[self.pos :]:
                raise AssertionError(f"unexpected PANIC (INV-12):\n{text}")
            if self._eof.is_set() or time.monotonic() > deadline:
                raise AssertionError(f"timeout waiting for {pattern!r}; output so far:\n{text}")
            time.sleep(0.01)

    def send(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.sent_at = len(self.output)
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()

    def prompt(self, timeout: float | None = None) -> None:
        """Waits until the console is at `ring3> ` again: a prompt printed any time after the last line was
        sent counts, even one that `expect()` already read past (asynchronous AUDIT/TAPE lines from COM3
        telemetry can land after the prompt, T-04)."""
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            text = self.output
            i = text.find("ring3> ", self.sent_at)
            if i >= 0:
                self.pos = max(self.pos, i + len("ring3> "))
                return
            if "PANIC:" in text[self.pos :]:
                raise AssertionError(f"unexpected PANIC (INV-12):\n{text}")
            if self._eof.is_set() or time.monotonic() > deadline:
                raise AssertionError(f"timeout waiting for the prompt; output so far:\n{text}")
            time.sleep(0.01)

    def run_prog(self, name: str, timeout: float | None = None) -> str:
        """`run <name>` at the prompt; returns the transcript from RUN to END (consumes it)."""
        self.prompt()
        self.send(f"run {name}")
        start = self.pos
        self.expect(r"^END pid=\d+ \S+ ticks=\d+ uptime=\d+$", timeout)
        return self.output[start : self.pos]

    def close(self, expect_panic: bool = False) -> Run:
        if self.exit_code is None:
            try:
                if not self._eof.is_set():
                    if not self.output.endswith("ring3> "):
                        self.prompt(5)
                    self.send("exit")
            except AssertionError:
                pass
            try:
                self.exit_code = self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
                self.exit_code = EXIT_TIMEOUT
            self._reader.join(timeout=5)
        run = Run(self.cmd, self.output, self.exit_code, time.monotonic() - self.t0)
        run.check(expect_panic)
        return run

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.exit_code is None:
            self.proc.kill()
            self.proc.wait()


def evidence_dir(card: str = "T-02") -> Path:
    """D-54: test transcripts go to build/test-logs/<card>/ unless RING3_EVIDENCE=1 asks for the committed
    docs/evidence/<card>/ files, so a normal test run leaves the working tree clean."""
    base = EVIDENCE.parent if os.environ.get("RING3_EVIDENCE") == "1" else TEST_LOGS
    folder = base / card
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def record_evidence(name: str, run: Run, card: str = "T-02") -> None:
    """Write a COM1 transcript (and the exact QEMU command) under the card's evidence folder."""
    folder = evidence_dir(card)
    cmd = " ".join(shlex.quote(c) for c in run.cmd)
    (folder / name).write_text(
        f"$ {cmd}\n# exit code {run.exit_code}, {run.seconds:.1f} s\n\n{run.output}", encoding="utf-8"
    )
    (folder / "qemu-command.txt").write_text(f"{cmd}\n", encoding="utf-8")


def audit_lines(text: str) -> list[dict[str, str]]:
    """Parses `AUDIT k=v ...` lines into dicts (decision fields as strings)."""
    out = []
    for line in text.splitlines():
        if line.startswith("AUDIT "):
            out.append(dict(kv.split("=", 1) for kv in line[6:].split(" ") if "=" in kv))
    return out
