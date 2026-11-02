"""T-01B: the real ring3cell firmware reaches the kernel as COM3 (hardware, opt-in).

Runs only with `RING3_HW=1` and the board attached (`RING3_CELL_PORT`, default /dev/ttyACM0); otherwise
skipped, so the default `uv run pytest` never opens the port (opening it resets the Uno). Sends PING only.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from tests.qemu import EXIT_PASS, QEMU, REPO

PORT = os.environ.get("RING3_CELL_PORT", "/dev/ttyACM0")
KERNEL_LINK = REPO / "build" / "kernel-link.elf"
EVIDENCE = REPO / "docs" / "evidence" / "T-01B"

pytestmark = [
    pytest.mark.hw,
    pytest.mark.skipif(
        os.environ.get("RING3_HW") != "1", reason="hardware test: set RING3_HW=1 (board attached)"
    ),
    pytest.mark.skipif(not os.access(PORT, os.R_OK | os.W_OK), reason=f"{PORT} missing or not read/writable"),
]


def link_command(port: str = PORT) -> list[str]:
    """Same command as `make test-link` (Makefile QEMU_LINK)."""
    return [
        QEMU,
        "-m",
        "64",
        "-display",
        "none",
        "-serial",
        "stdio",
        "-monitor",
        "none",
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-no-reboot",
        "-serial",
        "null",
        "-serial",
        port,
        "-kernel",
        str(KERNEL_LINK.relative_to(REPO)),
        "-append",
        "linktest",
    ]


def run_link(timeout: float = 30.0) -> tuple[list[str], list[tuple[int, str]], int]:
    """Boots the link image; returns (cmd, [(ms since QEMU start, line)], exit code)."""
    cmd = link_command()
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    lines: list[tuple[int, str]] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            lines.append((int((time.monotonic() - t0) * 1000), raw.decode("utf-8", "replace").rstrip("\r\n")))
            if time.monotonic() - t0 > timeout:
                proc.kill()
                break
        code = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return cmd, lines, code


def kv(line: str) -> dict[str, int]:
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)\b", line)}


def test_link_real_board():
    subprocess.run(["make", "-s", "KTEST_LINK=1"], cwd=REPO, check=True)
    cmd, lines, code = run_link()
    text = "\n".join(line for _, line in lines)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamped = "\n".join(f"[{ms:6d} ms] {line}" for ms, line in lines)
    Path(EVIDENCE / "link-pytest.txt").write_text(
        f"$ {' '.join(shlex.quote(c) for c in cmd)}\n"
        f"# exit code {code}; [ms] = host time since QEMU start\n\n{stamped}\n",
        encoding="utf-8",
    )
    assert not any(line.startswith("PANIC:") for _, line in lines), text
    assert code == EXIT_PASS, text
    assert "LINK PASS" in text

    first = next((ms, line) for ms, line in lines if line.startswith("LINK first_state"))
    assert first[0] < 3000 + 1000, f"first STATE of the new boot {first[0]} ms after QEMU start"
    f = kv(first[1])
    assert f["dev_t_ms"] < 3000 and f["ticks_since_com3_init"] <= 300

    summary = kv(next(line for _, line in lines if line.startswith("LINK SUMMARY")))
    assert summary["best_consecutive"] >= 20 and summary["final_run"] >= 20
    assert summary["fan_max"] == 0  # card step 5: the fan never runs
    assert summary["restarts"] == 0
    assert 18000 <= summary["rate_mhz"] <= 22000, summary  # ~20 Hz

    states = [kv(line) for _, line in lines if line.startswith("STATE seq=")]
    seqs = [s["seq"] for s in states]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))  # contiguous, no lost frame
    acks = [s["ack"] for s in states]
    assert acks[0] == 0 and acks[-1] == 8 and acks == sorted(acks)  # ack advances with each PING, never back
    pings = kv(next(line for _, line in lines if line.startswith("LINK pings")))
    assert pings["sent"] == pings["acked"] == 8
