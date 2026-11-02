"""INV-11: user-facing text never says "success", "secure", "safe" or "real-time". Scanned: every string
literal in the kernel, the user programs and the firmware sketch (what reaches COM1, the LCD or the bridge),
every string constant in the bridge package (timeline, outcomes, prompts), the context packs and the README.
Comments are not user-facing and are skipped. Outcomes are DONE / REFUSED / STOPPED / ERROR."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.qemu import REPO

BANNED = re.compile(r"\b(success\w*|secure\w*|safe|safely|real-?time)\b", re.IGNORECASE)
C_GLOBS = ("kernel/**/*.c", "kernel/**/*.h", "user/**/*.c", "user/**/*.h", "firmware/ring3cell/*.ino",
           "firmware/ring3cell/*.h", "abi/*.h")  # fmt: skip
PY_GLOBS = ("bridge/ring3bridge/**/*.py",)
TEXT_FILES = ("README.md",)
TEXT_GLOBS = ("docs/context-packs/*.md",)
# a string literal that is not user-facing text (names of files/flags are not screen text)
ALLOW = {
    "ESAFETY",  # the errno name of the safety policy (ARCHITECTURE §3), not a claim
}


def _strip_c_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def c_literals(path: Path) -> list[tuple[int, str]]:
    src = _strip_c_comments(path.read_text("utf-8", "replace"))
    out = []
    for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', src):
        line = src.count("\n", 0, m.start()) + 1
        out.append((line, m.group(1)))
    return out


def py_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


def _hits(items: list[tuple[int, str]], where: Path) -> list[str]:
    hits = []
    for line, text in items:
        if text in ALLOW:
            continue
        m = BANNED.search(text)
        if m and m.group(0) not in ALLOW:
            hits.append(f"{where.relative_to(REPO)}:{line}: {m.group(0)!r} in {text[:80]!r}")
    return hits


def test_inv_11_banned_words():
    hits: list[str] = []
    for g in C_GLOBS:
        for p in sorted(REPO.glob(g)):
            hits += _hits(c_literals(p), p)
    for g in PY_GLOBS:
        for p in sorted(REPO.glob(g)):
            hits += _hits(py_literals(p), p)
    for name in TEXT_FILES:
        p = REPO / name
        if p.exists():
            hits += _hits(list(enumerate(p.read_text("utf-8").splitlines(), 1)), p)
    for g in TEXT_GLOBS:
        for p in sorted(REPO.glob(g)):
            hits += _hits(list(enumerate(p.read_text("utf-8").splitlines(), 1)), p)
    assert not hits, "banned words in user-facing text (INV-11):\n" + "\n".join(hits)


def test_scanner_sees_strings():
    """The scanner would catch a hit: the kernel's LCD and audit vocabulary is in its reach."""
    device = REPO / "kernel" / "drivers" / "device.c"
    texts = [t for _, t in c_literals(device)]
    assert "hand too close" in texts and "within limits" in texts
    assert BANNED.search("the cell is safe now") and BANNED.search("Success!") and BANNED.search("real-time")
    assert not BANNED.search("ESAFETY") and not BANNED.search("safety policy")
