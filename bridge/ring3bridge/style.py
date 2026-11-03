"""Opt-in styled timeline for filming (T-08 step 5, D-66, D-66a): ``ring3 run --style console``.

The words are exactly the plain timeline's (host time, then the line the run loop wrote); only colour and two
short emphases are added, and only when stdout is a terminal. Anywhere else (a pipe, a file, the tests, the
evidence) and by default, the run gets the plain printer itself, so its output is byte for byte what it was.

Styled mode (256-colour ANSI): the host time in light cyan (159); a kernel decision code in amber (214), or
white on dark red (15 on 124) when it refuses or stops something; ``[tainted]`` in amber. A new line arrives
with an amber inverse background for 150 ms, then settles to the normal colours; a request's outcome line gets
one 200 ms inverse pulse instead, then stays bold. Nothing moves without a real line.
"""

from __future__ import annotations

import math
import re
import shutil
import threading
import time
from collections.abc import Callable
from typing import TextIO

STYLES = ("plain", "console")
ARRIVAL_S = 0.150
PULSE_S = 0.200

ESC = "\x1b["
RESET = ESC + "0m"
BOLD = ESC + "1m"
INVERSE = ESC + "7m"
TIME = ESC + "38;5;159m"
AMBER = ESC + "38;5;214m"
ADVERSE = ESC + "38;5;15m" + ESC + "48;5;124m"
ARRIVAL = ESC + "38;5;0m" + ESC + "48;5;214m"

_DECISION = re.compile(r"(?<=-> )([A-Z][A-Z_]+)(?= \()")
_ADVERSE = re.compile(r"^(DENY_\w+|CONFIRM_NO|LIMIT|PROC_FAULT|AUTO_HALT|FRAME_DROP)$")
_TAINT = re.compile(r"\[tainted\]")
_OUTCOME = re.compile(r"^request \d+ end: (DONE|REFUSED|STOPPED|ERROR)\b")


def stamp() -> str:
    """The plain timeline's host time column (cli._timeline)."""
    return time.strftime("%H:%M:%S")


def is_outcome(line: str) -> bool:
    return bool(_OUTCOME.match(line))


def colour(ts: str, line: str) -> str:
    """The settled styled form of one timeline line (same characters as plain, plus colour codes)."""
    if is_outcome(line):
        return f"{TIME}{ts}{RESET} {BOLD}{line}{RESET}"

    def decision(m: re.Match[str]) -> str:
        code = m.group(1)
        return f"{ADVERSE if _ADVERSE.match(code) else AMBER}{code}{RESET}"

    body = _DECISION.sub(decision, line)
    body = _TAINT.sub(lambda m: f"{AMBER}{m.group(0)}{RESET}", body)
    return f"{TIME}{ts}{RESET} {body}"


class StyledTimeline:
    """The timeline callback for a terminal. Each line is written at once with its emphasis; a timer (or the
    next line, whichever comes first) rewrites it in place with the settled colours."""

    def __init__(
        self,
        stream: TextIO,
        clock: Callable[[], str] = stamp,
        columns: Callable[[], int] = lambda: shutil.get_terminal_size().columns,
        timer: Callable[[float, Callable[[], None]], threading.Timer] | None = None,
    ):
        self.stream = stream
        self.clock = clock
        self.columns = columns
        self.timer = timer or (lambda s, fn: threading.Timer(s, fn))
        self._lock = threading.Lock()
        self._pending: tuple[str, str] | None = None
        self._t: threading.Timer | None = None

    def __call__(self, line: str) -> None:
        with self._lock:
            self._settle()
            ts = self.clock()
            if is_outcome(line):
                self.stream.write(f"{INVERSE}{BOLD}{ts} {line}{RESET}\n")
                delay = PULSE_S
            else:
                self.stream.write(f"{ARRIVAL}{ts} {line}{RESET}\n")
                delay = ARRIVAL_S
            self.stream.flush()
            self._pending = (ts, line)
            self._t = self.timer(delay, self._on_timer)
            self._t.daemon = True
            self._t.start()

    def _on_timer(self) -> None:
        with self._lock:
            self._settle()

    def _settle(self) -> None:
        if self._pending is None:
            return
        if self._t is not None:
            self._t.cancel()
            self._t = None
        ts, line = self._pending
        self._pending = None
        rows = max(1, math.ceil((len(ts) + 1 + len(line)) / max(1, self.columns())))
        # back to the start of the line just written, clear it, write the settled form in its place
        self.stream.write(f"{ESC}{rows}F{ESC}J{colour(ts, line)}\n")
        self.stream.flush()

    def close(self) -> None:
        """Settles the last line now (before anything else prints)."""
        with self._lock:
            self._settle()


def timeline_for(style: str, stream: TextIO, plain: Callable[[str], None]) -> Callable[[str], None]:
    """``plain`` itself unless ``style == "console"`` and ``stream`` is a terminal."""
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}")
    isatty = getattr(stream, "isatty", None)
    if style == "console" and isatty is not None and isatty():
        return StyledTimeline(stream)
    return plain


def close(timeline: Callable[[str], None]) -> None:
    if isinstance(timeline, StyledTimeline):
        timeline.close()
