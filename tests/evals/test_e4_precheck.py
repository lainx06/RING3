"""T-11 (D-86): the E4 presence pre-check waits for fresh telemetry. Just after the board resets, the kernel's
status shows tele_valid=0 until TELE_SYNC_FRAMES frames have come in; the script must poll `status` for up to
10 s until tele_valid=1 and only then judge the distance. Offline: a scripted console, a fake clock."""

from __future__ import annotations

import re
import sys

from tests.qemu import REPO

sys.path.insert(0, str(REPO / "tools" / "eval"))

import e4_agent  # noqa: E402
import e4_tasks  # noqa: E402

E4 = REPO / "docs" / "evals" / "e4"


def _line(valid: int, dist: int, up: int = 200) -> str:
    return (
        f"status: fan=0 servo=90 estop_latched=0 tele_valid={valid} distance={dist} in_request=0 tainted=0 "
        f"pending=0 seq=1 dropped=0 uptime={up}"
    )


class Console:
    """Answers each `status` with the next scripted line (the last one repeats)."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.sent: list[str] = []

    def prompt(self, timeout: float | None = None) -> None:
        pass

    def send(self, line: str) -> None:
        self.sent.append(line)

    def expect(self, pattern: str, timeout: float | None = None) -> re.Match[str]:
        line = self.lines.pop(0) if len(self.lines) > 1 else self.lines[0]
        m = re.search(pattern, line, re.M)
        assert m, (pattern, line)
        return m

    @property
    def output(self) -> str:
        return ""


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def _k(line: str) -> dict:
    return e4_tasks.parse_status(line + "\n")["kernel"]


def _fresh(con: Console, first: dict, clock: Clock) -> dict:
    return e4_agent.fresh_status(con, first, clock=clock.now, sleep=clock.sleep)


def test_recorded_stop_was_tele_valid_0():
    """Run e4-v2-20261103-230118 stopped at the pre-check: the board had just reset (uptime 153)."""
    com1 = (E4 / "e4-v2-20261103-230118-com1.txt").read_text("utf-8")
    k = e4_tasks.parse_status(com1)["kernel"]
    assert (k["tele_valid"], k["distance"]) == (0, 0)
    assert not e4_tasks.presence_ok(k)


def test_late_telemetry_is_waited_for():
    """tele_valid=0 at the first status and the next two; fresh at the third poll with the object at 51 mm."""
    clock = Clock()
    con = Console([_line(0, 0), _line(0, 0), _line(1, 51)])
    k = _fresh(con, _k(_line(0, 0, up=153)), clock)
    assert e4_tasks.presence_ok(k), k
    assert k["distance"] == 51
    assert con.sent == ["status"] * 3
    assert clock.t - 1000.0 <= 10.0


def test_fresh_at_once_types_nothing_more():
    clock = Clock()
    con = Console([_line(1, 40)])
    k = _fresh(con, _k(_line(1, 51)), clock)
    assert k["distance"] == 51 and con.sent == [] and clock.t == 1000.0


def test_late_telemetry_judged_on_the_fresh_reading():
    """The first fresh reading decides: a clear beam is not presence, even after waiting."""
    clock = Clock()
    k = _fresh(Console([_line(0, 0), _line(1, 218)]), _k(_line(0, 0)), clock)
    assert k["tele_valid"] == 1 and not e4_tasks.presence_ok(k) and e4_tasks.clear_ok(k)


def test_gives_up_after_10_s():
    """Telemetry never becomes valid: the script stops polling after 10 s and the pre-check fails closed."""
    clock = Clock()
    con = Console([_line(0, 0)])
    k = _fresh(con, _k(_line(0, 0)), clock)
    assert k["tele_valid"] == 0 and not e4_tasks.presence_ok(k)
    assert 10.0 <= clock.t - 1000.0 <= 11.0
    assert 1 <= len(con.sent) <= 25
