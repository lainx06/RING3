"""T-11 (D-75): the hardware test harness itself, checked without hardware (default suite).

1. `HwCell` stops its QEMU when its setup fails (it leaked one per failure on the real board; two QEMUs on the
   Uno's port gave bad frames).
2. The hardware tests' prompts never tell the user to answer a prompt that the script answers itself.
"""

from __future__ import annotations

import ast
import re

import pytest

import tests.test_inv_13_hw as hw
from tests.qemu import REPO, Session


def test_hwcell_stops_its_qemu_when_setup_fails(monkeypatch):
    """No board attached: `cell: COM3 present` never comes, setup raises, and the QEMU it started is gone."""
    made: list[Session] = []

    def no_board(**kw: object) -> Session:
        s = Session(append=str(kw.get("append", "")), timeout=3)  # no COM3 at all: the kernel says absent
        made.append(s)
        return s

    monkeypatch.setattr(hw, "Session", no_board)
    try:
        with pytest.raises(AssertionError, match="COM3 present"):
            hw.HwCell()
        assert len(made) == 1
        assert made[0].proc.poll() is not None, "HwCell left its QEMU running after a failed setup"
    finally:
        for s in made:  # never leave a QEMU behind, whatever the outcome
            if s.proc.poll() is None:
                s.proc.kill()
                s.proc.wait()


class _ScriptedSession:
    """Stands in for Session in `start_fan`: records what the script types; every expect matches."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, line: str) -> None:
        self.sent.append(line)

    def expect(self, pattern: str, timeout: float | None = None) -> re.Match[str]:
        m = re.match(".*", "")
        assert m is not None
        return m


def test_start_fan_says_the_script_answers(capsys):
    """`start_fan` types the `y` itself, so its message must not ask the user to answer."""
    cell = object.__new__(hw.HwCell)
    cell.s = _ScriptedSession()  # type: ignore[assignment]
    cell.start_fan()
    said = capsys.readouterr().out
    assert "y" in cell.s.sent  # the script answers the console itself
    assert "you answer" not in said and "answer y" not in said.replace("answers y", ""), said
    assert "the script answers y" in said and "nothing to type" in said, said


def _say_texts(path: str) -> list[str]:
    """Every say(...) message in a hardware test file (f-strings with their literal parts)."""
    tree = ast.parse((REPO / path).read_text("utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "say" and node.args:
            arg = node.args[0]
            parts = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
            out.append(
                "".join(p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str))
            )
    return out


@pytest.mark.parametrize("path", ["tests/test_inv_13_hw.py"])
def test_hw_prompts_never_ask_for_an_answer_the_script_types(path):
    """D-75: every message that mentions an answer says the script types it and the user types nothing.
    (Questions the user really answers use input(), not say().)"""
    texts = _say_texts(path)
    assert texts, "no say() messages found"
    bad = [
        t for t in texts if re.search(r"\banswer", t) and not ("the script" in t and "nothing to type" in t)
    ]
    assert bad == [], bad
