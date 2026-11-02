"""COM2 as seen from the host: a TCP client to QEMU's ``-serial tcp:127.0.0.1:<port>,server=on,wait=off``.

Receives through ``FrameDecoder`` (kernel->bridge bounds, body decoding), sends through ``SeqCounter``.
The link never interprets anything: the run loop (run.py) decides what a frame means and logs it.
"""

from __future__ import annotations

import select
import socket
import time

from ring3bridge.actions import SeqCounter
from ring3bridge.framing import Frame, FrameDecoder, FrameError


class LinkError(Exception):
    pass


class Link:
    def __init__(self, host: str = "127.0.0.1", port: int = 4555) -> None:
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self.decoder = FrameDecoder()
        self.seq = SeqCounter()
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.closed_by_peer = False

    def connect(self, timeout_s: float = 10.0) -> None:
        """Retries until QEMU's listener is up (``wait=off``: the kernel may already be running)."""
        deadline = time.monotonic() + timeout_s
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=1.0)
                self.sock.setblocking(False)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return
            except OSError as e:
                last = e
                time.sleep(0.1)
        raise LinkError(f"cannot connect to COM2 at {self.host}:{self.port}: {last}")

    def send(self, ftype: int, body: bytes) -> bytes:
        """Frames and sends one body; returns the wire bytes (for the ``frame`` record)."""
        if self.sock is None:
            raise LinkError("link not connected")
        wire = self.seq.wire(ftype, body)
        self.sock.setblocking(True)
        try:
            self.sock.sendall(wire)
        except OSError as e:
            raise LinkError(f"send failed: {e.__class__.__name__}") from None
        finally:
            self.sock.setblocking(False)
        self.tx_bytes += len(wire)
        return wire

    def send_raw(self, data: bytes) -> None:
        """Raw bytes (the fuzzer): no framing, no sequence number."""
        if self.sock is None:
            raise LinkError("link not connected")
        self.sock.setblocking(True)
        try:
            self.sock.sendall(data)
        finally:
            self.sock.setblocking(False)
        self.tx_bytes += len(data)

    def recv(self, timeout_s: float) -> list[Frame | FrameError]:
        """Waits up to ``timeout_s`` for bytes and returns every complete frame or error they produced."""
        if self.sock is None:
            raise LinkError("link not connected")
        out: list[Frame | FrameError] = []
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return out
            r, _, _ = select.select([self.sock], [], [], remaining)
            if not r:
                return out
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                self.closed_by_peer = True
                return out
            self.rx_bytes += len(data)
            out.extend(self.decoder.feed(data))
            if out:
                return out

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
