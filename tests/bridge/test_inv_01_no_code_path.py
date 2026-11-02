"""INV-01: model output is data, never code. AST scan of every Python file under bridge/.

Stronger than "no model text reaches eval": the code-execution and dynamic-loading primitives are not used in
the bridge at all. A later card that needs one (e.g. running the host replay binary) adds an entry to
``ALLOWED`` with its file, the primitive and why its arguments can't come from model text, and the scan
still holds for every other file.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIDGE = REPO / "bridge"

BANNED_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "input"}
BANNED_DYNAMIC = {"globals", "locals", "vars", "getattr", "setattr", "delattr"}  # with a non-literal name
BANNED_MODULES = {"subprocess", "pickle", "cPickle", "marshal", "shelve", "importlib", "imp", "runpy", "code",
                  "codeop", "ctypes", "multiprocessing", "pty", "dill", "yaml", "jsonpickle"}  # fmt: skip
BANNED_ATTRS = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execve"), ("os", "execvp"),
                ("os", "spawnv"), ("os", "spawnl"), ("os", "posix_spawn"), ("os", "fork"),
                ("builtins", "eval"), ("builtins", "exec")}  # fmt: skip

#: (file relative to bridge/, primitive) -> justification
ALLOWED: dict[tuple[str, str], str] = {
    (
        "ring3bridge/abi.py",
        "globals",
    ): "c_constants() lists this module's own LINK_* integers; no input involved",
    (
        "ring3bridge/replay.py",
        "subprocess",
    ): "replay_run() executes the repo's own host policy build (build/host/policy_test --replay, a fixed "
    "argv) and feeds it TAPE lines rendered from integer `event` records; no model text is involved",
}


def scan_source(src: str, rel: str) -> list[str]:
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        where = f"{rel}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                root = n.split(".")[0]
                if root in BANNED_MODULES and (rel, root) not in ALLOWED:
                    out.append(f"{where} imports {n}")
            if isinstance(node, ast.ImportFrom) and node.module in ("os", "builtins"):
                for a in node.names:
                    if (node.module, a.name) in BANNED_ATTRS:
                        out.append(f"{where} imports {node.module}.{a.name}")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id in BANNED_CALLS and (rel, f.id) not in ALLOWED:
                    out.append(f"{where} calls {f.id}()")
                if f.id in BANNED_DYNAMIC and (rel, f.id) not in ALLOWED:
                    literal = len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    if f.id in ("globals", "locals", "vars") or not literal:
                        out.append(f"{where} calls {f.id}() dynamically")
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if (f.value.id, f.attr) in BANNED_ATTRS:
                    out.append(f"{where} calls {f.value.id}.{f.attr}()")
                if f.value.id in BANNED_MODULES and (rel, f.value.id) not in ALLOWED:
                    out.append(f"{where} calls {f.value.id}.{f.attr}()")
        elif isinstance(node, ast.Name) and node.id in ("__builtins__",):
            out.append(f"{where} touches __builtins__")
    return out


def bridge_files() -> list[Path]:
    files = sorted(BRIDGE.rglob("*.py"))
    assert files, "no bridge sources found"
    return files


def test_inv_01_no_code_path():
    problems = []
    for p in bridge_files():
        problems += scan_source(p.read_text(), p.relative_to(BRIDGE).as_posix())
    assert not problems, "\n".join(problems)


def test_allowlist_entries_are_still_needed():
    for (rel, prim), why in ALLOWED.items():
        assert why
        src = (BRIDGE / rel).read_text()
        assert f"{prim}(" in src or f"import {prim}" in src, f"stale ALLOWED entry {rel} {prim}"


@pytest.mark.parametrize(
    "snippet",
    [
        "eval(reply)",
        "exec(reply)",
        "compile(reply, 'x', 'exec')",
        "__import__(name)",
        "import subprocess",
        "import subprocess as sp",
        "from subprocess import run",
        "import pickle",
        "from importlib import import_module",
        "import importlib.util",
        "import marshal",
        "import ctypes",
        "os.system(cmd)",
        "os.popen(cmd)",
        "from os import system",
        "getattr(handlers, op)",
        "setattr(obj, key, 1)",
        "globals()[name]",
        "vars(obj)[k]",
        "__builtins__['eval']",
        "builtins.eval(x)",
        "input()",
    ],
)
def test_scanner_catches(snippet):
    assert scan_source(snippet, "ring3bridge/x.py"), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        "import re\nre.compile('x')",
        "json.loads(s)",
        "getattr(obj, 'name')",
        "import hashlib",
        "struct.pack('<B', 1)",
    ],
)
def test_scanner_allows_plain_data_code(snippet):
    assert scan_source(snippet, "ring3bridge/x.py") == []
