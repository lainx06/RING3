#!/usr/bin/env python3
"""RING3 work-cell test tool (T-06). Talks to the ring3cell firmware over the Uno's USB serial port.

Test tool only: never run it while QEMU has the port open (the kernel owns COM3 then, INV-13).
Standard library only. Frame format: hardware/protocol.md (constants mirror firmware/ring3cell/protocol.h).

  uv run python tools/cellctl.py selftest                 # codec vs firmware/test/link_vectors.txt, no board
  uv run python tools/cellctl.py watch /dev/ttyACM0       # print decoded STATE frames (keeps PINGs going)
  uv run python tools/cellctl.py a5 /dev/ttyACM0          # the T-06 A5 checks, with prompts for manual steps
  uv run python tools/cellctl.py a5 /dev/ttyACM0 --only rate,clamp
  uv run python tools/cellctl.py noise /dev/ttyACM0       # does the fan set the E-stop latch? (moves fan)
"""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path

# ---- protocol constants (firmware/ring3cell/protocol.h) ----
T_PING, T_FAN, T_SERVO, T_LCD, T_ALARM, T_ESTOP_RESET, T_STATE = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x81
STATE_LEN = 12
NO_ECHO = 0xFFFF
F_WDT_RESET, F_CLAMP, F_BAD_FRAME = 1 << 0, 1 << 1, 1 << 2
F_ESTOP_LATCHED, F_PRESENCE_FAULT, F_LINK_LOST = 1 << 3, 1 << 4, 1 << 5
FLAG_NAMES = [
    (F_WDT_RESET, "WDT"),
    (F_CLAMP, "CLAMP"),
    (F_BAD_FRAME, "BAD"),
    (F_ESTOP_LATCHED, "ESTOP_L"),
    (F_PRESENCE_FAULT, "PRESENCE"),
    (F_LINK_LOST, "LINK_LOST"),
]
LINK_LOSS_FAN = 60  # above the 50 % minimum spinning duty (D-47), so the stop is visible
BOOT_WINDOW_MS = 3000  # a STATE with t_ms below this comes from the boot that our open caused
MAX_WIRE = 64  # generous host-side cap; the device's own frames are at most 26 bytes on the wire


# ---- codec ----
def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    """Canonical COBS, as the firmware: no empty block after a full 0xFF block at the end."""
    out = bytearray([0])
    ci = 0
    code = 1
    for i, b in enumerate(data):
        if b == 0:
            out[ci] = code
            ci = len(out)
            out.append(0)
            code = 1
            continue
        out.append(b)
        code += 1
        if code == 0xFF:
            out[ci] = code
            if i + 1 == len(data):
                return bytes(out)
            ci = len(out)
            out.append(0)
            code = 1
    out[ci] = code
    return bytes(out)


def cobs_decode(enc: bytes) -> bytes | None:
    out = bytearray()
    i = 0
    n = len(enc)
    while i < n:
        code = enc[i]
        i += 1
        if code == 0 or i + code - 1 > n:
            return None
        chunk = enc[i : i + code - 1]
        if 0 in chunk:
            return None
        out += chunk
        i += code - 1
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def frame_decoded(ftype: int, seq: int, body: bytes = b"") -> bytes:
    payload = struct.pack("<BHH", ftype, seq & 0xFFFF, len(body)) + body
    return payload + struct.pack("<H", crc16_ccitt_false(payload))


def frame_wire(ftype: int, seq: int, body: bytes = b"") -> bytes:
    return cobs_encode(frame_decoded(ftype, seq, body)) + b"\x00"


@dataclass
class State:
    seq: int
    t_ms: int
    fan: int
    servo: int
    dist: int
    estop: int
    flags: int
    ack_seq: int
    rx_time: float

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)

    def __str__(self) -> str:
        dist = "NO_ECHO" if self.dist == NO_ECHO else f"{self.dist} mm"
        names = ",".join(n for f, n in FLAG_NAMES if self.flags & f) or "-"
        return (
            f"STATE seq={self.seq} t={self.t_ms} ms fan={self.fan}% servo={self.servo} dist={dist} "
            f"estop={self.estop} flags={names} ack={self.ack_seq}"
        )


def parse_state(enc: bytes, rx_time: float) -> State | None:
    dec = cobs_decode(enc)
    if dec is None or len(dec) < 7:
        return None
    ftype, seq, length = struct.unpack_from("<BHH", dec, 0)
    if ftype != T_STATE or length != STATE_LEN or len(dec) != 7 + length:
        return None
    if struct.unpack_from("<H", dec, 5 + length)[0] != crc16_ccitt_false(dec[: 5 + length]):
        return None
    t_ms, fan, servo, dist, estop, flags, ack = struct.unpack_from("<IBBHBBH", dec, 5)
    if estop > 1 or flags & 0xC0:
        return None
    return State(seq, t_ms, fan, servo, dist, estop, flags, ack, rx_time)


# ---- serial link ----
class Cell:
    def __init__(self, port: str, log) -> None:
        self.log = log
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self.fd)
        a = termios.tcgetattr(self.fd)
        a[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
        a[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE | termios.CRTSCTS)
        a[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 1
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)  # reads now wait <= 100 ms (VTIME)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        self.rx = bytearray()
        self.seq = 0
        self.last_ping = 0.0
        self.pinging = True
        self.last_tx = 0.0  # time.monotonic() of the last frame actually written
        self.states: list[State] = []
        self.bad_rx = 0

    def close(self) -> None:
        os.close(self.fd)

    def send(self, ftype: int, body: bytes = b"", corrupt_crc: bool = False) -> int:
        self.seq = (self.seq + 1) & 0xFFFF
        dec = bytearray(frame_decoded(ftype, self.seq, body))
        if corrupt_crc:
            dec[-1] ^= 0x5A
        os.write(self.fd, cobs_encode(bytes(dec)) + b"\x00")
        self.last_tx = time.monotonic()
        return self.seq

    def poll(self) -> list[State]:
        """Read what's there (<= 100 ms), keep the link fed with PING every 250 ms, return new STATEs."""
        now = time.monotonic()
        if self.pinging and now - self.last_ping >= 0.25:
            self.send(T_PING)
            self.last_ping = now
        data = os.read(self.fd, 256)
        new: list[State] = []
        t = time.monotonic()
        for b in data:
            if b != 0:
                if len(self.rx) < MAX_WIRE:
                    self.rx.append(b)
                continue
            if self.rx:
                st = parse_state(bytes(self.rx), t)
                if st is None:
                    self.bad_rx += 1
                else:
                    new.append(st)
            self.rx.clear()
        self.states += new
        return new

    def run(self, seconds: float) -> list[State]:
        out: list[State] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            out += self.poll()
        return out

    def wait_for(self, pred, timeout: float) -> State | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for st in self.poll():
                if pred(st):
                    return st
        return None

    def latest(self, timeout: float = 0.3) -> State | None:
        got = self.run(timeout)
        return got[-1] if got else None


# ---- A5 checks ----
class Checks:
    def __init__(self, cell: Cell, log) -> None:
        self.c = cell
        self.log = log
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        self.log(f"  -> {'OK' if ok else 'FAIL'}: {detail}")

    def prompt(self, text: str) -> None:
        self.log(f"  >>> {text}")

    def clear_reading(self, timeout: float = 15.0) -> bool:
        st = self.c.wait_for(lambda s: s.dist != NO_ECHO and s.dist >= 100, timeout)
        return st is not None

    def fan_off_reset(self) -> None:
        self.c.send(T_FAN, bytes([0]))
        self.c.run(0.2)

    def rate(self) -> None:
        self.log("[rate] STATE frames over 5 s")
        got = self.c.run(5.0)
        hz = (len(got) - 1) / (got[-1].rx_time - got[0].rx_time) if len(got) > 1 else 0.0
        dev_hz = ((len(got) - 1) * 1000.0 / (got[-1].t_ms - got[0].t_ms)) if len(got) > 1 else 0.0
        seqs = [s.seq for s in got]
        gaps = sum(1 for a, b in zip(seqs, seqs[1:], strict=False) if (b - a) & 0xFFFF != 1)
        self.record(
            "rate",
            18.0 <= hz <= 22.0,
            f"{len(got)} STATE, {hz:.1f} Hz host clock, {dev_hz:.1f} Hz device clock, seq gaps {gaps}, "
            f"undecodable {self.c.bad_rx}",
        )

    def clamp(self) -> None:
        self.log("[clamp] FAN 100 -> 80 + CLAMP; SERVO 170 -> 165 + CLAMP")
        if not self.clear_reading():
            self.record("clamp", False, "no clear presence reading (check the backstop)")
            return
        seq = self.c.send(T_FAN, bytes([100]))
        st = self.c.wait_for(lambda s: s.ack_seq == seq, 1.0)
        st = self.c.latest(0.2) or st
        fan_ok = st is not None and st.fan == 80 and st.has(F_CLAMP)
        self.log(f"  FAN 100: {st}")
        self.fan_off_reset()
        seq = self.c.send(T_SERVO, bytes([170]))
        st2 = self.c.wait_for(lambda s: s.ack_seq == seq, 1.0)
        self.log(f"  SERVO 170: {st2}")
        servo_ok = st2 is not None and st2.servo == 165 and st2.has(F_CLAMP)
        self.c.run(0.5)
        self.c.send(T_SERVO, bytes([90]))
        self.c.run(0.5)
        self.record(
            "clamp",
            fan_ok and servo_ok,
            f"fan reported {st.fan if st else '?'} clamp={st.has(F_CLAMP) if st else '?'}; "
            f"servo reported {st2.servo if st2 else '?'} clamp={st2.has(F_CLAMP) if st2 else '?'}",
        )

    def bad_crc(self) -> None:
        self.log("[bad_crc] FAN 40 with a corrupted CRC: rejected, BAD_FRAME, fan unchanged")
        self.fan_off_reset()
        before = self.c.latest()
        seq = self.c.send(T_FAN, bytes([40]), corrupt_crc=True)
        st = self.c.wait_for(lambda s: s.has(F_BAD_FRAME), 1.0)
        after = self.c.latest(0.3)
        self.log(f"  flagged: {st}")
        ok = (
            st is not None
            and st.fan == 0
            and st.ack_seq != seq
            and after is not None
            and after.fan == 0
            and before is not None
        )
        self.record(
            "bad_crc",
            ok,
            f"BAD_FRAME seen={st is not None}, fan stayed {after.fan if after else '?'}, "
            f"ack_seq not advanced to {seq}={st is not None and st.ack_seq != seq}",
        )

    def estop(self) -> None:
        self.log(
            "[estop] fan 40, press E-stop -> fan 0 in the next STATE; latched until ESTOP_RESET + released"
        )
        if not self.clear_reading():
            self.record("estop", False, "no clear presence reading")
            return
        self.c.send(T_FAN, bytes([40]))
        run = self.c.wait_for(lambda s: s.fan == 40, 1.0)
        self.prompt("Press and HOLD the E-stop now (30 s).")
        first = self.c.wait_for(lambda s: s.estop == 1 or s.has(F_ESTOP_LATCHED), 30.0)
        self.log(f"  first STATE after press: {first}")
        press_ok = run is not None and first is not None and first.fan == 0 and first.has(F_ESTOP_LATCHED)
        self.c.send(T_ESTOP_RESET)  # while held: must not clear
        held = self.c.latest(0.3)
        held_ok = held is not None and held.has(F_ESTOP_LATCHED)
        self.log(f"  ESTOP_RESET while held: {held}")
        self.prompt("Release the E-stop now.")
        rel = self.c.wait_for(lambda s: s.estop == 0, 30.0)
        self.c.run(0.3)
        self.c.send(T_FAN, bytes([40]))  # latched: fan must stay 0
        latched = self.c.latest(0.4)
        latched_ok = (
            rel is not None and latched is not None and latched.fan == 0 and latched.has(F_ESTOP_LATCHED)
        )
        self.log(f"  released, FAN 40 sent: {latched}")
        self.c.send(T_ESTOP_RESET)
        cleared = self.c.wait_for(lambda s: not s.has(F_ESTOP_LATCHED), 1.0)
        self.log(f"  ESTOP_RESET released: {cleared}")
        self.c.run(0.2)
        after = self.c.latest(0.3)
        no_restart = after is not None and after.fan == 0
        self.record(
            "estop",
            press_ok and held_ok and latched_ok and cleared is not None and no_restart,
            f"cut on press={press_ok}, reset refused while held={held_ok}, latch holds after release="
            f"{latched_ok}, reset clears={cleared is not None}, fan not auto-restarted={no_restart}",
        )

    def presence(self) -> None:
        self.log("[presence] fan 40, hand < 100 mm -> fan 0 + PRESENCE latched")
        if not self.clear_reading():
            self.record("presence", False, "no clear presence reading")
            return
        self.c.send(T_FAN, bytes([40]))
        run = self.c.wait_for(lambda s: s.fan == 40, 1.0)
        self.prompt("Put your hand about 5 cm in front of the sensor now (30 s). Keep fingers off the fan.")
        st = self.c.wait_for(lambda s: s.has(F_PRESENCE_FAULT) or (s.dist != NO_ECHO and s.dist < 100), 30.0)
        self.c.run(0.1)
        cut = self.c.latest(0.2)
        self.log(f"  first STATE with hand: {st}")
        self.log(f"  then: {cut}")
        ok = run is not None and cut is not None and cut.fan == 0 and cut.has(F_PRESENCE_FAULT)
        self.prompt("Take your hand away.")
        clear = self.clear_reading(30.0)
        still = self.c.latest(0.3)
        latched_ok = still is not None and still.has(F_PRESENCE_FAULT) and still.fan == 0
        self.c.send(T_FAN, bytes([0]))
        reset = self.c.wait_for(lambda s: not s.has(F_PRESENCE_FAULT), 1.0)
        self.record(
            "presence",
            ok and clear and latched_ok and reset is not None,
            f"cut={ok}, still latched after hand removed={latched_ok}, FAN 0 with clear reading clears="
            f"{reset is not None}",
        )

    def echo_unplug(self) -> None:
        self.log("[echo_unplug] fan 40, ECHO wire pulled from D3 -> fan 0 within ~150 ms + PRESENCE latched")
        if not self.clear_reading():
            self.record("echo_unplug", False, "no clear presence reading")
            return
        self.c.send(T_FAN, bytes([40]))
        run = self.c.wait_for(lambda s: s.fan == 40, 1.0)
        self.prompt("Pull the ECHO wire out of Uno D3 now (30 s). Keep fingers off the fan.")
        first = self.c.wait_for(lambda s: s.dist == NO_ECHO, 30.0)
        cut = self.c.wait_for(lambda s: s.fan == 0, 1.0) if first and first.fan != 0 else first
        dt_ms = (cut.t_ms - first.t_ms) if (first and cut) else -1
        self.log(f"  first NO_ECHO: {first}")
        self.log(f"  fan 0:         {cut}")
        ok = run is not None and cut is not None and cut.has(F_PRESENCE_FAULT) and 0 <= dt_ms <= 200
        self.prompt("Put the ECHO wire back into D3.")
        back = self.clear_reading(60.0)
        self.c.send(T_FAN, bytes([0]))
        self.c.wait_for(lambda s: not s.has(F_PRESENCE_FAULT), 1.0)
        self.record(
            "echo_unplug",
            ok and back,
            f"fan 0 {dt_ms} ms (device clock) after the first NO_ECHO STATE; the 3-miss window starts up to "
            f"~100 ms earlier; PRESENCE latched={cut.has(F_PRESENCE_FAULT) if cut else False}; "
            f"wire back={back}",
        )

    def estop_flood(self) -> None:
        self.log("[estop_flood] FAN 40 sent every ~5 ms; press E-stop during it -> fan stays 0")
        if not self.clear_reading():
            self.record("estop_flood", False, "no clear presence reading")
            return
        self.prompt("In the next 10 s, while FAN commands stream, press and release the E-stop once.")
        end = time.monotonic() + 10.0
        pressed_at = None
        bad_after: list[State] = []
        sent = 0
        while time.monotonic() < end:
            self.c.send(T_FAN, bytes([40]))
            sent += 1
            for st in self.c.poll():
                if pressed_at is None and (st.estop or st.has(F_ESTOP_LATCHED)):
                    pressed_at = st
                    self.log(f"  first STATE after press: {st}")
                elif pressed_at is not None and st.fan != 0:
                    bad_after.append(st)
            time.sleep(0.005)
        self.log(f"  FAN frames sent: {sent}; STATE with fan != 0 after the press: {len(bad_after)}")
        ok = pressed_at is not None and pressed_at.fan == 0 and not bad_after
        self.prompt("Make sure the E-stop is released.")
        self.c.wait_for(lambda s: s.estop == 0, 30.0)
        self.c.run(0.2)
        self.c.send(T_ESTOP_RESET)
        self.c.wait_for(lambda s: not s.has(F_ESTOP_LATCHED), 1.0)
        self.fan_off_reset()
        self.record("estop_flood", ok, f"press seen={pressed_at is not None}, fan stayed 0={not bad_after}")

    def link_loss(self) -> None:
        self.log("[link_loss] fan 60 + servo 120, then silence -> fan 0, servo 90, LINK_LOST after 1 s")
        if not self.clear_reading():
            self.record("link_loss", False, "no clear presence reading")
            return
        self.c.send(T_SERVO, bytes([120]))
        self.c.run(0.4)
        self.c.send(T_FAN, bytes([LINK_LOSS_FAN]))
        run = self.c.wait_for(lambda s: s.fan == LINK_LOSS_FAN, 1.0)
        self.c.pinging = False
        last_sent = (
            self.c.last_tx
        )  # the last frame really sent (a PING may have gone out during the wait above)
        st = self.c.wait_for(lambda s: s.has(F_LINK_LOST), 3.0)
        silence_ms = int((st.rx_time - last_sent) * 1000) if st else -1
        self.log(f"  LINK_LOST: {st}  ({silence_ms} ms after the last frame, host clock)")
        ok = (
            run is not None
            and st is not None
            and st.fan == 0
            and st.servo == 90
            and 900 <= silence_ms <= 1300
        )
        self.c.pinging = True
        back = self.c.wait_for(lambda s: not s.has(F_LINK_LOST), 1.0)
        restart = self.c.latest(0.3)
        no_restart = restart is not None and restart.fan == 0
        self.record(
            "link_loss",
            ok and back is not None and no_restart,
            f"parked after {silence_ms} ms, fan={st.fan if st else '?'} servo={st.servo if st else '?'}"
            f", PING clears LINK_LOST={back is not None}, fan not auto-restarted={no_restart}",
        )


ALL_CHECKS = ["rate", "clamp", "bad_crc", "estop", "presence", "echo_unplug", "estop_flood", "link_loss"]


def open_cell(port: str, log) -> tuple[Cell, State]:
    """Open, then wait for the first STATE of the fresh boot (the open resets the Uno). Frames left over from
    before the reset are discarded, and nothing is sent until the new boot's first STATE arrives, so the
    bootloader never sees our bytes."""
    cell = Cell(port, log)
    cell.pinging = False
    log(f"opened {port} raw 115200 8N1; the Uno resets on open, waiting for the first STATE of the new boot")
    t0 = time.monotonic()
    first = None
    stale = 0
    while first is None and time.monotonic() - t0 < 6.0:
        for st in cell.poll():
            if st.t_ms < BOOT_WINDOW_MS:
                first = st
                break
            stale += 1
    if first is None:
        cell.close()
        raise SystemExit(
            "ERROR: no STATE from a fresh boot within 6 s (is ring3cell flashed? did the open reset "
            "the Uno? is QEMU holding the port?)"
        )
    log(
        f"first STATE of the new boot after {int((time.monotonic() - t0) * 1000)} ms "
        f"({stale} pre-reset frames "
        f"discarded): {first}"
    )
    log(f"  boot flags: WDT_RESET={first.has(F_WDT_RESET)} LINK_LOST={first.has(F_LINK_LOST)}")
    log(f"  undecodable bytes runs before sync (frames cut by the reset): {cell.bad_rx}")
    cell.bad_rx = 0
    cell.states.clear()
    cell.pinging = True
    return cell, first


def _hex(x: str) -> bytes:
    return b"" if x == "-" else bytes.fromhex(x)


def cmd_selftest(_args, log) -> int:
    path = Path(__file__).resolve().parent.parent / "firmware" / "test" / "link_vectors.txt"
    n = fails = 0
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        n += 1
        if f[0] == "crc":
            ok = crc16_ccitt_false(_hex(f[1])) == int(f[2], 16)
        elif f[0] == "cobs":
            ok = cobs_encode(_hex(f[2])) == _hex(f[3]) and cobs_decode(_hex(f[3])) == _hex(f[2])
        elif f[0] == "frame":
            dec, wire = _hex(f[3]), _hex(f[4])
            ok = cobs_encode(dec) + b"\x00" == wire and cobs_decode(wire[:-1]) == dec
            if ok and f[2] == "ok":
                ftype, seq, length = struct.unpack_from("<BHH", dec, 0)
                ok = frame_wire(ftype, seq, dec[5 : 5 + length]) == wire
                if ftype == T_STATE:
                    ok = ok and parse_state(wire[:-1], 0.0) is not None
            elif ok and f[2] == "crc":
                ok = struct.unpack_from("<H", dec, len(dec) - 2)[0] != crc16_ccitt_false(dec[:-2])
        else:
            continue
        if not ok:
            fails += 1
            log(f"FAIL {line}")
    log(f"selftest: {n - fails}/{n} vectors match")
    return 1 if fails else 0


def cmd_noise(args, log) -> int:
    """Diagnostic: does anything other than a press set the E-stop latch? Idle, servo moves, then FAN 60 for
    2 s, twice; prints every change of flags/fan/servo/button. Moves the fan and servo."""
    cell, _ = open_cell(args.port, log)
    last = [None]
    trips = []

    def watch(label: str, secs: float) -> None:
        end = time.monotonic() + secs
        while time.monotonic() < end:
            for st in cell.poll():
                key = (st.flags & ~F_BAD_FRAME, st.fan, st.servo, st.estop)
                if key != last[0]:
                    log(f"  [{label}] {st}")
                    if (
                        st.has(F_ESTOP_LATCHED)
                        and not st.estop
                        and (last[0] is None or not last[0][0] & F_ESTOP_LATCHED)
                    ):
                        trips.append(label)
                    last[0] = key

    try:
        watch("boot idle", 1.5)
        cell.send(T_ESTOP_RESET)
        watch("after ESTOP_RESET, idle", 2.0)
        cell.send(T_SERVO, bytes([120]))
        watch("servo 120", 1.0)
        cell.send(T_SERVO, bytes([90]))
        watch("servo 90", 1.0)
        for trial in (1, 2):
            cell.send(T_ESTOP_RESET)
            watch(f"reset before fan trial {trial}", 0.6)
            cell.send(T_FAN, bytes([LINK_LOSS_FAN]))
            watch(f"FAN {LINK_LOSS_FAN} trial {trial}", 2.0)
            cell.send(T_FAN, bytes([0]))
            watch(f"FAN 0 trial {trial}", 1.0)
        cell.send(T_ESTOP_RESET)
        watch("final reset", 0.5)
    finally:
        cell.send(T_FAN, bytes([0]))
        cell.run(0.3)
        cell.close()
    log(f"NOISE SUMMARY: latch set without a press in: {', '.join(trips) if trips else 'none'}")
    return 1 if trips else 0


def cmd_watch(args, log) -> int:
    cell, _ = open_cell(args.port, log)
    try:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            for st in cell.poll():
                log(str(st))
    finally:
        cell.close()
    return 0


def cmd_a5(args, log) -> int:
    names = args.only.split(",") if args.only else ALL_CHECKS
    for n in names:
        if n not in ALL_CHECKS:
            raise SystemExit(f"unknown check {n}; choose from {','.join(ALL_CHECKS)}")
    cell, _ = open_cell(args.port, log)
    checks = Checks(cell, log)
    try:
        cell.run(1.5)  # let the link settle (LINK_LOST from boot clears on the first PING)
        for n in names:
            getattr(checks, n)()
            cell.send(T_FAN, bytes([0]))
            cell.run(0.3)
    finally:
        try:
            cell.send(T_FAN, bytes([0]))
            cell.run(0.2)
        finally:
            cell.close()
    log("A5 SUMMARY")
    for name, ok, detail in checks.results:
        log(f"  {name:12s} {'OK  ' if ok else 'FAIL'} {detail}")
    return 0 if all(ok for _, ok, _ in checks.results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    nz = sub.add_parser("noise")
    nz.add_argument("port")
    w = sub.add_parser("watch")
    w.add_argument("port")
    w.add_argument("--seconds", type=float, default=10.0)
    a = sub.add_parser("a5")
    a.add_argument("port")
    a.add_argument("--only", default="")
    args = ap.parse_args()

    def log(msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    return {"selftest": cmd_selftest, "watch": cmd_watch, "a5": cmd_a5, "noise": cmd_noise}[args.cmd](
        args, log
    )


if __name__ == "__main__":
    sys.exit(main())
