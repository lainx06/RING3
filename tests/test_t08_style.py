"""T-08 step 5: ``ring3 run --style console``. To anything but a terminal (and by default) the run gets
the plain timeline printer itself, so evidence files and tests are byte-identical; on a terminal the same
words get 256-colour ANSI, a 150 ms arrival highlight and a 200 ms outcome pulse. The sample lines are T-04
live run 4's timeline (docs/evidence/T-04/live-run/bridge-timeline-4.txt), host time column removed."""

from __future__ import annotations

import io
import os
import re
import sys

import pytest

from ring3bridge import cli, style
from tests.qemu import REPO

SAMPLE = REPO / "docs" / "evidence" / "T-04" / "live-run" / "bridge-timeline-4.txt"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sample_lines() -> list[str]:
    out = []
    for line in SAMPLE.read_text("utf-8").splitlines():
        m = re.match(r"^\d\d:\d\d:\d\d (.*)$", line)
        if m:
            out.append(m.group(1))
    assert len(out) > 15 and any(style.is_outcome(x) for x in out), out
    return out


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(style.time, "strftime", lambda fmt: "16:03:55")
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "16:03:55")


def test_default_style_is_plain():
    assert style.timeline_for("plain", io.StringIO(), cli._timeline) is cli._timeline
    with pytest.raises(ValueError):
        style.timeline_for("fancy", io.StringIO(), cli._timeline)


def test_console_style_to_a_non_tty_is_the_plain_printer(capsys, fixed_clock):
    """A5: ``--style console`` with stdout captured (not a terminal) prints exactly what plain prints."""
    assert not sys.stdout.isatty()
    lines = sample_lines()
    for line in lines:
        cli._timeline(line)
    plain = capsys.readouterr().out.encode()
    t = style.timeline_for("console", sys.stdout, cli._timeline)
    assert t is cli._timeline
    for line in lines:
        t(line)
    styled = capsys.readouterr().out.encode()
    assert styled == plain and b"\x1b" not in styled
    style.close(t)  # a no-op for the plain printer
    assert capsys.readouterr().out == ""


def test_console_style_to_a_pipe_and_a_file_is_plain(tmp_path):
    r, w = os.pipe()
    with os.fdopen(w, "w") as pipe, open(tmp_path / "out.txt", "w") as f:
        assert style.timeline_for("console", pipe, cli._timeline) is cli._timeline
        assert style.timeline_for("console", f, cli._timeline) is cli._timeline
    os.close(r)


def test_run_flag(capsys):
    """The flag parses on `ring3 run` (choices plain/console, default plain) and changes nothing else: the
    dry run prints the same bytes with and without it (no network, no key)."""
    assert cli.main(["run", "--budget-usd", "0.10", "--dry-run"]) == 0
    base = capsys.readouterr().out
    assert cli.main(["run", "--budget-usd", "0.10", "--dry-run", "--style", "console"]) == 0
    assert capsys.readouterr().out == base
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--budget-usd", "0.10", "--dry-run", "--style", "glow"])
    assert e.value.code == 2


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class ManualTimer:
    """Stands in for threading.Timer: fires only when the test says so."""

    def __init__(self, seconds: float, fn):
        self.seconds, self.fn, self.cancelled, self.daemon = seconds, fn, False, False

    def start(self) -> None:
        timers.append(self)

    def cancel(self) -> None:
        self.cancelled = True


timers: list[ManualTimer] = []


def styled(columns: int = 200) -> tuple[FakeTTY, style.StyledTimeline]:
    timers.clear()
    tty = FakeTTY()
    t = style.StyledTimeline(tty, clock=lambda: "16:03:55", columns=lambda: columns, timer=ManualTimer)
    return tty, t


def test_styled_arrival_then_settle():
    tty, t = styled()
    line = "kernel decision #12: obj_write target=2 -> CONFIRM_PENDING (taint) result=0 [tainted]"
    t(line)
    first = tty.getvalue()
    assert first == f"{style.ARRIVAL}16:03:55 {line}{style.RESET}\n"
    assert timers[-1].seconds == pytest.approx(0.150)
    timers[-1].fn()  # 150 ms later
    settled = tty.getvalue()[len(first) :]
    assert settled.startswith("\x1b[1F\x1b[J")  # back over the line just written, cleared, rewritten
    assert f"{style.AMBER}CONFIRM_PENDING{style.RESET}" in settled
    assert f"{style.AMBER}[tainted]{style.RESET}" in settled
    assert f"{style.TIME}16:03:55{style.RESET}" in settled
    assert ANSI.sub("", settled) == f"16:03:55 {line}\n"  # the same words as plain


def test_styled_adverse_and_outcome_pulse():
    tty, t = styled()
    t("kernel decision #13: confirm target=2 -> CONFIRM_NO (answer) result=-125 [tainted]")
    t("request 1 end: DONE after 2 action(s); executor exit code 0")  # settles the previous line first
    out = tty.getvalue()
    assert f"{style.ADVERSE}CONFIRM_NO{style.RESET}" in out
    assert timers[0].cancelled and timers[-1].seconds == pytest.approx(0.200)
    assert out.endswith(f"{style.INVERSE}{style.BOLD}16:03:55 request 1 end: DONE after 2 action(s); "
                        f"executor exit code 0{style.RESET}\n")  # fmt: skip
    t.close()  # the run ends: the pulse settles to a steady bold line
    tail = tty.getvalue()[len(out) :]
    assert tail.startswith("\x1b[1F\x1b[J") and f"{style.BOLD}request 1 end: DONE" in tail
    t.close()
    assert tty.getvalue()[len(out) + len(tail) :] == ""  # nothing pending, nothing written


def test_styled_long_line_rewrites_every_wrapped_row():
    tty, t = styled(columns=40)
    line = "x" * 75  # 9 + 75 = 84 characters on a 40-column terminal: three rows
    t(line)
    t.close()
    assert "\x1b[3F\x1b[J" in tty.getvalue()


def test_styled_words_equal_plain_for_the_sample():
    tty, t = styled()
    lines = sample_lines()
    for line in lines:
        t(line)
    t.close()
    plain = "".join(f"16:03:55 {x}\n" for x in lines)
    final_rows = [ANSI.sub("", r) for r in re.findall(r"\x1b\[\d+F\x1b\[J(.*?\n)", tty.getvalue())]
    assert "".join(final_rows) == plain
