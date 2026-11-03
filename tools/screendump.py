"""PNG of the running QEMU display through the monitor socket of `make run-cell-screen` (T-08).

    uv run python -I tools/screendump.py build/screen-mon.sock docs/evidence/T-08/idle.png

Also writes the VGA text buffer next to it (<name>.txt, CP437 decoded, 25 rows of 80 columns) so the
evidence carries the exact characters. Reads only; sends nothing to the guest."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path


def monitor(path: str, line: str) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(path)
        s.settimeout(10)
        buf = b""
        for cmd in (None, line):
            if cmd is not None:
                s.sendall(cmd.encode() + b"\n")
                buf = b""
            while not buf.endswith(b"(qemu) "):
                chunk = s.recv(4096)
                if not chunk:
                    raise SystemExit("monitor closed")
                buf += chunk
        return buf.decode("utf-8", "replace")


def wait_file(p: Path, size: int | None = None) -> None:
    deadline = time.monotonic() + 5
    while not (p.exists() and (p.stat().st_size == size if size else p.stat().st_size > 0)):
        if time.monotonic() > deadline:
            raise SystemExit(f"QEMU wrote nothing to {p}")
        time.sleep(0.05)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: screendump.py <monitor socket> <out.png>")
    sock, png = sys.argv[1], Path(sys.argv[2]).resolve()
    png.parent.mkdir(parents=True, exist_ok=True)
    raw = png.with_suffix(".bin")
    for p in (png, raw):
        p.unlink(missing_ok=True)
    monitor(sock, f'screendump "{png}" -f png')
    monitor(sock, f'pmemsave 0xb8000 4000 "{raw}"')
    wait_file(png)
    wait_file(raw, 4000)
    data = raw.read_bytes()
    raw.unlink()
    rows = [bytes(data[(r * 80 + c) * 2] for c in range(80)).decode("cp437") for r in range(25)]
    png.with_suffix(".txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {png.name} and {png.with_suffix('.txt').name} in {png.parent}")


if __name__ == "__main__":
    main()
