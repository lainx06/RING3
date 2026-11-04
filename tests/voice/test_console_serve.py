"""T-19: the proxy's loop on a real pty and a socket pair: bytes through unchanged, terminal restored."""

import os
import pty
import socket
import termios
import threading
import time

from ring3bridge import console


def _read_exact(fd, n, timeout=5.0):
    os.set_blocking(fd, False)
    got = bytearray()
    end = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < end:
        try:
            got += os.read(fd, n - len(got))
        except BlockingIOError:
            time.sleep(0.005)
    return bytes(got)


def _recv_exact(s, n, timeout=5.0):
    s.settimeout(timeout)
    got = bytearray()
    while len(got) < n:
        got += s.recv(n - len(got))
    return bytes(got)


class Loop:
    def __enter__(self):
        self.master, self.slave = pty.openpty()
        self.before = termios.tcgetattr(self.slave)
        self.com1, self.kernel = socket.socketpair()
        self.rc = []
        self.t = threading.Thread(
            target=lambda: self.rc.append(console.serve(self.com1, self.slave, self.slave, None)), daemon=True
        )
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.kernel.close()
        self.t.join(5)
        assert not self.t.is_alive()
        self.com1.close()
        os.close(self.master)
        os.close(self.slave)


def test_keyboard_to_kernel_byte_for_byte():
    keys = bytes(b for b in range(256) if b not in (0x1B, 0x03, 0x1A, 0x1C)) + b"\x1b[A\r"
    with Loop() as x:
        time.sleep(0.1)
        os.write(x.master, keys)
        assert _recv_exact(x.kernel, len(keys)) == keys


def test_kernel_to_screen_unchanged_and_com1_eof_ends_the_loop():
    # OPOST stays on, as under QEMU's -serial stdio: the tty driver turns \n into \r\n in both cases
    out = b"ring3> status\nstatus: fan=0\nring3> "
    want = out.replace(b"\n", b"\r\n")
    with Loop() as x:
        time.sleep(0.1)
        x.kernel.sendall(out)
        assert _read_exact(x.master, len(want)) == want
        x.kernel.close()
        x.t.join(5)
        assert not x.t.is_alive() and x.rc == [0]


def test_terminal_mode_matches_qemu_stdio_and_is_restored():
    with Loop() as x:
        time.sleep(0.1)
        iflag, oflag, _cflag, lflag, _i, _o, cc = termios.tcgetattr(x.slave)
        assert not lflag & (termios.ECHO | termios.ICANON | termios.IEXTEN)
        assert lflag & termios.ISIG  # Ctrl-C keeps working, as with `make run-cell`
        assert not iflag & (termios.ICRNL | termios.IXON)
        assert oflag & termios.OPOST
        before = x.before
        x.kernel.close()
        x.t.join(5)
        assert termios.tcgetattr(x.slave) == before


def test_not_a_terminal_works_too():
    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    com1, kernel = socket.socketpair()
    rc = []
    t = threading.Thread(target=lambda: rc.append(console.serve(com1, r_in, w_out, None)), daemon=True)
    t.start()
    os.write(w_in, b"help\r")
    assert _recv_exact(kernel, 5) == b"help\r"
    os.close(w_in)  # end of keyboard: the console stays up, the output keeps flowing
    kernel.sendall(b"ok\n")
    assert _read_exact(r_out, 3) == b"ok\n"
    kernel.close()
    t.join(5)
    assert not t.is_alive() and rc == [0]
    for fd in (r_in, r_out, w_out):
        os.close(fd)
    com1.close()
