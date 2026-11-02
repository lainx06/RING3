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


def qemu_command(kernel: Path, append: str = "", memory_mb: int = 64) -> list[str]:
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
        "-monitor",
        "none",
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

    def __init__(self, kernel: Path = KERNEL_TEST, append: str = FAST_QUOTAS, timeout: float = 30.0):
        self.cmd = qemu_command(kernel, append)
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
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()

    def prompt(self, timeout: float | None = None) -> None:
        self.expect(r"ring3> ", timeout)

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


def record_evidence(name: str, run: Run, card: str = "T-02") -> None:
    """Write a COM1 transcript (and the exact QEMU command) under docs/evidence/<card>/."""
    folder = EVIDENCE.parent / card
    folder.mkdir(parents=True, exist_ok=True)
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
