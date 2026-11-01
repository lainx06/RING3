"""QEMU harness for RING3 tests: boot a kernel headless, capture COM1, return output and exit code.

Exit codes (kernel/include/qemu.h): 33 = PASS, 35 = FAIL, 37 = PANIC, 124 = harness timeout.
Every run fails on an unexpected `PANIC` line (INV-12) unless the caller asks for one.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "build" / "kernel.elf"
KERNEL_KTEST = REPO / "build" / "kernel-ktest.elf"
EVIDENCE = REPO / "docs" / "evidence" / "T-01"

EXIT_PASS = 33
EXIT_FAIL = 35
EXIT_PANIC = 37
EXIT_TIMEOUT = 124

QEMU = os.environ.get("RING3_QEMU", "qemu-system-i386")


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
    import time

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
    if run.has_panic() and not expect_panic:
        raise AssertionError(f"unexpected PANIC (INV-12):\n{run.output}")
    return run


def record_evidence(name: str, run: Run) -> None:
    """Write a COM1 transcript (and the exact QEMU command) under docs/evidence/T-01/."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cmd = " ".join(shlex.quote(c) for c in run.cmd)
    (EVIDENCE / name).write_text(
        f"$ {cmd}\n# exit code {run.exit_code}, {run.seconds:.1f} s\n\n{run.output}", encoding="utf-8"
    )
    (EVIDENCE / "qemu-command.txt").write_text(f"{cmd}\n", encoding="utf-8")
