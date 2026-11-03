"""Simulated work cell for the QEMU tests (T-04 step 6): speaks the COM3 protocol on the TCP socket QEMU
uses as its third serial port. Plays the firmware's behaviour as visible on the link (hardware/protocol.md):
STATE at 20 Hz, FAN/SERVO clamps, the E-stop latch, the presence cutoff, the link watchdog, ESTOP_RESET;
scripted telemetry (presence, no echo, stale, E-stop press/release, a firmware restart, a previous boot's
frames delivered first, re-delivered blocks). Every kernel frame is recorded with a host timestamp. No
hardware, no model, no network beyond the loopback socket.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field

from ring3bridge.framing import Frame, FrameDecoder, FrameError, encode_frame

# firmware/ring3cell/protocol.h
T_PING, T_FAN, T_SERVO, T_LCD, T_ALARM, T_ESTOP_RESET, T_STATE = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x81
CELL_BOUNDS = {T_PING: (0, 0), T_FAN: (1, 1), T_SERVO: (1, 1), T_LCD: (1, 17), T_ALARM: (1, 1),
               T_ESTOP_RESET: (0, 0)}  # fmt: skip
STATE_BOUNDS = {T_STATE: (12, 12)}
MAX_DECODED = 24
NO_ECHO = 0xFFFF
F_CLAMP, F_BAD_FRAME, F_ESTOP_LATCHED, F_PRESENCE_FAULT, F_LINK_LOST = 1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 5
FAN_MAX, SERVO_MIN, SERVO_MAX, SERVO_PARK, PRESENCE_MM, NO_ECHO_LIMIT = 80, 15, 165, 90, 100, 3
LINK_TIMEOUT_S = 1.0
TYPE_NAMES = {T_PING: "PING", T_FAN: "FAN", T_SERVO: "SERVO", T_LCD: "LCD", T_ALARM: "ALARM",
              T_ESTOP_RESET: "ESTOP_RESET"}  # fmt: skip


@dataclass(frozen=True)
class KernelFrame:
    t: float  # host monotonic seconds
    type: int
    seq: int
    body: bytes
    dev_t_ms: int  # the device clock when it arrived (for stop-time measurements)

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"0x{self.type:02x}")

    def __str__(self) -> str:
        if self.type in (T_FAN, T_SERVO, T_ALARM):
            return f"{self.name} {self.body[0]}"
        if self.type == T_LCD:
            return f"LCD {self.body[0]} {self.body[1:].decode('ascii', 'replace')!r}"
        return self.name


def state_body(t_ms: int, fan: int, servo: int, dist: int, estop: int, flags: int, ack: int) -> bytes:
    return struct.pack("<IBBHBBH", t_ms & 0xFFFFFFFF, fan, servo, dist, estop, flags, ack)


def state_frame(seq: int, t_ms: int, fan: int = 0, servo: int = 90, dist: int = 300, estop: int = 0,
                flags: int = 0, ack: int = 0) -> bytes:  # fmt: skip
    return encode_frame(T_STATE, seq, state_body(t_ms, fan, servo, dist, estop, flags, ack), STATE_BOUNDS)


@dataclass
class SimCell:
    """Connects to QEMU's COM3 server socket and runs the board model in a thread.

    Scripted inputs (set at any time): ``distance`` (mm, ``NO_ECHO`` for no echo), ``estop`` (button held),
    ``streaming`` (False = silence: stale telemetry). ``restart()`` reboots the model (seq/t_ms from 0, fan
    off, servo parked). ``resend_last(n)`` writes the last n STATE frames again (D-54 re-delivery).
    """

    port: int
    period_s: float = 0.05
    boot_t_ms: int = 16
    prev_boot_frames: int = 0  # whole frames of a "previous boot" sent before the new boot (D-54)
    prev_boot_repeat: int = 1
    cut_prefix: bytes = b""  # bytes of a frame cut by the reset, sent first (resync, not a drop)
    distance: int = 300
    estop: bool = False
    streaming: bool = True
    # board model
    seq: int = 0
    t_ms: int = 0
    fan_cmd: int = 0
    fan: int = 0
    servo: int = SERVO_PARK
    latched: bool = False
    presence_fault: bool = False
    link_lost: bool = False
    clamp: bool = False
    bad_frame: bool = False
    ack_seq: int = 0
    no_echo_run: int = 0
    lcd: dict[int, str] = field(default_factory=dict)
    alarm: int = 0
    frames: list[KernelFrame] = field(default_factory=list)
    errors: list[FrameError] = field(default_factory=list)
    states_sent: int = 0
    fan_history: list[tuple[float, int, int]] = field(default_factory=list)  # (host t, t_ms, fan)

    def __post_init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._decoder = FrameDecoder(CELL_BOUNDS, MAX_DECODED, body_decoder=None)
        self._last_rx = time.monotonic()
        self._sent: list[bytes] = []
        self._hold_after = 0
        self._flags = 0
        self.t0 = time.monotonic()

    # ---- lifecycle ----
    def start(self, connect_timeout: float = 10.0) -> SimCell:
        deadline = time.monotonic() + connect_timeout
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", self.port), timeout=1.0)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        self._sock.setblocking(False)
        self._last_rx = time.monotonic()
        self.t_ms = self.boot_t_ms
        self._preamble()
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def __enter__(self) -> SimCell:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---- scripted events ----
    def age(self, ms: int) -> None:
        """Advance the device clock (a board that has been up for a while, so a reboot is unambiguous)."""
        with self._lock:
            self.t_ms += ms

    def write_raw(self, data: bytes) -> None:
        """Bytes straight onto the line (a cut or corrupt frame)."""
        self._write(data)

    def restart(self, hold_after: int = 0) -> None:
        """The board reboots: counters from 0, fan off, servo parked, latches cleared (a real power cycle).
        ``hold_after`` > 0: stream that many frames of the new boot, then pause (``streaming`` = False) so a
        test can act while the kernel still waits for its 3 monotonic frames."""
        with self._lock:
            self._hold_after = hold_after
            self.seq = 0
            self.t_ms = self.boot_t_ms
            self.fan_cmd = self.fan = 0
            self.servo = SERVO_PARK
            self.latched = self.presence_fault = False
            self.clamp = self.bad_frame = False
            self.ack_seq = 0
            self.no_echo_run = 0
            self._last_rx = time.monotonic()

    def resend_last(self, n: int) -> None:
        """D-54: the USB path delivers an earlier block of STATE frames again."""
        with self._lock:
            block = b"".join(self._sent[-n:])
        self._write(block)

    def emit_state(self) -> None:
        """One STATE frame of the current boot now, from the model as it stands (seq/t_ms advance as in a
        20 Hz pass), whether or not the stream is paused: a single frame at a chosen moment (T-09 F1)."""
        with self._lock:
            self._emit()

    def write_fast(self, data: bytes, deadline_s: float = 10.0) -> int:
        """Writes everything as fast as the socket takes it (a flood, T-09 F4); returns the bytes written."""
        assert self._sock is not None
        sent = 0
        deadline = time.monotonic() + deadline_s
        while sent < len(data) and time.monotonic() < deadline:
            try:
                sent += self._sock.send(data[sent:])
            except BlockingIOError:
                time.sleep(0.001)
            except OSError:
                break
        return sent

    def frames_after(self, index: int) -> list[KernelFrame]:
        with self._lock:
            return list(self.frames[index:])

    def wait_frame(self, pred, timeout: float = 5.0, start: int = 0) -> KernelFrame:
        """The first kernel frame at or after ``start`` matching ``pred``, waiting up to ``timeout`` s."""
        deadline = time.monotonic() + timeout
        while True:
            for f in self.frames_after(start):
                if pred(f):
                    return f
            if time.monotonic() > deadline:
                seen = [str(f) for f in self.frames_after(start)]
                raise AssertionError(f"no matching frame within {timeout} s; frames: {seen}")
            time.sleep(0.01)

    def wait_fan(self, duty: int, timeout: float = 5.0, start: int = 0) -> KernelFrame:
        return self.wait_frame(lambda f: f.type == T_FAN and f.body[0] == duty, timeout, start)

    def fan_commands(self, start: int = 0) -> list[int]:
        return [f.body[0] for f in self.frames_after(start) if f.type == T_FAN]

    def motion_commands(self, start: int = 0) -> list[str]:
        return [str(f) for f in self.frames_after(start) if f.type in (T_FAN, T_SERVO)]

    # ---- the model ----
    def _preamble(self) -> None:
        if self.cut_prefix:
            self._write(self.cut_prefix)
        if self.prev_boot_frames:
            block = b"".join(
                state_frame(1847 + i, 100_000 + 50 * i, servo=SERVO_PARK, dist=287, ack=8)
                for i in range(self.prev_boot_frames)
            )
            for _ in range(self.prev_boot_repeat):
                self._write(block)

    def _write(self, data: bytes) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(data)
        except OSError:
            pass

    def _clear_reading(self) -> bool:
        return self.distance != NO_ECHO and self.distance >= PRESENCE_MM

    def _on_kernel_frame(self, fr: Frame) -> None:
        now = time.monotonic()
        self.frames.append(KernelFrame(now, fr.type, fr.seq, fr.body, self.t_ms))
        self.ack_seq = fr.seq
        self._last_rx = now
        self.link_lost = False
        any_latch = self.latched or self.estop or self.presence_fault
        if fr.type == T_FAN:
            duty = fr.body[0]
            self.clamp = duty > FAN_MAX
            if any_latch:
                if self.presence_fault and self._clear_reading():
                    self.presence_fault = False  # the FAN clears the fault; its setpoint is still dropped
                return  # D-50 F2: dropped while any latch is set
            if duty > 0 and not self._clear_reading():
                self.presence_fault = True  # refused and latched
                self.fan_cmd = 0
                return
            self.fan_cmd = min(duty, FAN_MAX)
        elif fr.type == T_SERVO:
            deg = fr.body[0]
            self.clamp = deg < SERVO_MIN or deg > SERVO_MAX
            if self.latched or self.estop or not self._clear_reading():
                return  # held
            self.servo = max(SERVO_MIN, min(SERVO_MAX, deg))
        elif fr.type == T_LCD:
            self.lcd[fr.body[0]] = fr.body[1:].decode("ascii", "replace")
        elif fr.type == T_ALARM:
            self.alarm = fr.body[0]
        elif fr.type == T_ESTOP_RESET:
            if not self.estop:
                self.latched = False
                self.alarm = 0

    def _tick(self) -> None:
        """One 50 ms pass of the firmware loop, then a STATE frame."""
        now = time.monotonic()
        if self.estop and not self.latched:
            self.latched = True
            self.fan_cmd = 0
        if self.distance == NO_ECHO:
            self.no_echo_run += 1
        else:
            self.no_echo_run = 0
        running = self.fan_cmd > 0
        if running and (
            (self.distance != NO_ECHO and self.distance < PRESENCE_MM) or self.no_echo_run >= NO_ECHO_LIMIT
        ):
            self.presence_fault = True
            self.fan_cmd = 0
        if now - self._last_rx > LINK_TIMEOUT_S:
            if not self.link_lost:
                self.fan_cmd = 0
                self.servo = SERVO_PARK
            self.link_lost = True
        cut = self.latched or self.estop or self.presence_fault or self.link_lost
        self.fan = 0 if cut else self.fan_cmd
        flags = (
            (F_CLAMP if self.clamp else 0)
            | (F_BAD_FRAME if self.bad_frame else 0)
            | (F_ESTOP_LATCHED if self.latched else 0)
            | (F_PRESENCE_FAULT if self.presence_fault else 0)
            | (F_LINK_LOST if self.link_lost else 0)
        )
        self.bad_frame = False
        if not self.fan_history or self.fan_history[-1][2] != self.fan:
            self.fan_history.append((now, self.t_ms, self.fan))
        self._flags = flags
        if self.streaming:
            self._emit()
            if self._hold_after:
                self._hold_after -= 1
                if self._hold_after == 0:
                    self.streaming = False
        self.t_ms += int(self.period_s * 1000)

    def _emit(self) -> None:
        """Writes one STATE frame from the model's current fields (lock held)."""
        wire = state_frame(
            self.seq, self.t_ms, self.fan, self.servo, self.distance, int(self.estop), self._flags,
            self.ack_seq,
        )  # fmt: skip
        self._write(wire)
        self._sent.append(wire)
        del self._sent[:-64]
        self.states_sent += 1
        self.seq = (self.seq + 1) & 0xFFFF

    def _run(self) -> None:
        assert self._sock is not None
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                data = self._sock.recv(4096)
                if data:
                    with self._lock:
                        for item in self._decoder.feed(data):
                            if isinstance(item, Frame):
                                self._on_kernel_frame(item)
                            else:
                                self.errors.append(item)
                                self.bad_frame = True
            except BlockingIOError:
                pass
            except OSError:
                break
            now = time.monotonic()
            if now >= next_t:
                with self._lock:
                    self._tick()
                next_t += self.period_s
                if now - next_t > 1.0:
                    next_t = now  # fell behind (a paused host): do not burst
            time.sleep(0.002)
